"""Allowlisted download sources and the backend check of a batch of links.

The repository file lists YouTube and Bilibili only. Sites the user adds live in
``config/download_sources.local.json``, which git ignores so real domains never
reach the public repository. The allowlist is the main guard: yt-dlp follows
redirects on its own, so the DNS check below only covers the first host.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

REPO_SOURCES = Path("config") / "download_sources.json"
LOCAL_SOURCES = Path("config") / "download_sources.local.json"
MAX_BATCH_LINKS = 20
MAX_URL_LENGTH = 2048
MAX_MIN_DURATION_SECONDS = 6 * 3600
_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
_BAD_URL_CHARACTERS = re.compile(r"[\x00-\x20\x7f]")
_DEFAULT_PORTS = {"http": 80, "https": 443}
# One DNS label after IDNA. Nothing such as a backslash or "%" that another URL
# parser (urllib3, requests) would read as the end of the host.
_HOST_LABEL = re.compile(r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?")

Resolver = Callable[[str, int], Iterable[str]]


class DownloadSourceError(ValueError):
    """The source configuration is invalid."""


class _LinkError(Exception):
    """One rejected link: a stable ``code`` and a Vietnamese ``message``."""

    def __init__(self, code: str, message: str):
        super().__init__(code, message)
        self.code = code
        self.message = message


class DownloadBatchError(ValueError):
    """A batch of links was rejected; ``errors`` lists every bad line."""

    def __init__(self, message: str, errors: list[dict[str, Any]]):
        super().__init__(message)
        self.errors = errors


@dataclass(frozen=True)
class DownloadSource:
    id: str
    label: str
    domains: tuple[str, ...]
    min_duration_seconds: float
    allow_multi_entry: bool
    notes: str
    local: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "label": self.label, "domains": list(self.domains),
            "min_duration_seconds": self.min_duration_seconds,
            "allow_multi_entry": self.allow_multi_entry, "notes": self.notes, "local": self.local,
        }


@dataclass(frozen=True)
class SourceCatalog:
    sources: tuple[DownloadSource, ...]
    warnings: tuple[str, ...]

    def get(self, source_id: str) -> DownloadSource | None:
        return next((item for item in self.sources if item.id == source_id), None)


def normalize_host(host: str) -> str:
    """Lower-case IDNA form without a trailing dot; raises ValueError when invalid."""
    cleaned = host.strip().rstrip(".").lower()
    if not cleaned:
        raise ValueError("empty host")
    try:
        return cleaned.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValueError(f"invalid host {host!r}") from error


def host_matches(host: str, domains: Iterable[str]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    return True


def _normalize_domain(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DownloadSourceError("Tên miền phải là chuỗi không rỗng.")
    if any(mark in value for mark in ("/", ":", "@", "*", " ")):
        raise DownloadSourceError(f"Tên miền không hợp lệ: {value!r} (chỉ ghi tên miền, không ghi link).")
    try:
        domain = normalize_host(value)
    except ValueError as error:
        raise DownloadSourceError(f"Tên miền không hợp lệ: {value!r}.") from error
    if "." not in domain or _is_ip_literal(domain):
        raise DownloadSourceError(f"Tên miền không hợp lệ: {value!r}.")
    return domain


def _parse_source(raw: Any, *, local: bool) -> DownloadSource:
    if not isinstance(raw, dict):
        raise DownloadSourceError("Mỗi nguồn phải là một object.")
    source_id = raw.get("id")
    if not isinstance(source_id, str) or not _ID_PATTERN.fullmatch(source_id):
        raise DownloadSourceError(f"id nguồn không hợp lệ: {source_id!r}.")
    label = raw.get("label")
    if not isinstance(label, str) or not label.strip() or len(label) > 60:
        raise DownloadSourceError(f"Nguồn {source_id}: label phải có 1–60 ký tự.")
    domains = raw.get("domains")
    if not isinstance(domains, list) or not domains:
        raise DownloadSourceError(f"Nguồn {source_id}: cần ít nhất một tên miền.")
    minimum = raw.get("min_duration_seconds", 0)
    if (isinstance(minimum, bool) or not isinstance(minimum, (int, float))
            or not 0 <= minimum <= MAX_MIN_DURATION_SECONDS):
        raise DownloadSourceError(f"Nguồn {source_id}: min_duration_seconds không hợp lệ.")
    multi = raw.get("allow_multi_entry", False)
    notes = raw.get("notes", "")
    if not isinstance(multi, bool) or not isinstance(notes, str) or len(notes) > 500:
        raise DownloadSourceError(f"Nguồn {source_id}: allow_multi_entry hoặc notes không hợp lệ.")
    normalized = tuple(dict.fromkeys(_normalize_domain(item) for item in domains))
    return DownloadSource(source_id, label.strip(), normalized, float(minimum), multi, notes, local)


def _parse_file(path: Path, *, local: bool) -> list[DownloadSource]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DownloadSourceError(f"Không đọc được {path.name}: {error}") from error
    items = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise DownloadSourceError(f"{path.name} thiếu danh sách sources.")
    sources = [_parse_source(item, local=local) for item in items]
    ids = [item.id for item in sources]
    if len(set(ids)) != len(ids):
        raise DownloadSourceError(f"{path.name} có id nguồn bị trùng.")
    return sources


def load_sources(project_root: Path) -> SourceCatalog:
    """Read the repository sources (must be valid) and the optional local ones."""
    sources = _parse_file(project_root / REPO_SOURCES, local=False)
    warnings: list[str] = []
    local_path = project_root / LOCAL_SOURCES
    if local_path.is_file():
        try:
            local = _parse_file(local_path, local=True)
            known = {item.id for item in sources}
            clash = sorted(known & {item.id for item in local})
            if clash:
                raise DownloadSourceError(f"{local_path.name} trùng id với nguồn có sẵn: {', '.join(clash)}.")
            sources.extend(local)
        except DownloadSourceError as error:
            warnings.append(f"Bỏ qua nguồn tự thêm: {error}")
    return SourceCatalog(tuple(sources), tuple(warnings))


def default_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _is_internal(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not ip.is_global or ip.is_multicast


def _check_url(raw: str, source: DownloadSource) -> tuple[str, str, int]:
    """Return (normalized url, host, port) or raise _LinkError."""
    if len(raw) > MAX_URL_LENGTH:
        raise _LinkError("URL_TOO_LONG", f"Link dài quá {MAX_URL_LENGTH} ký tự.")
    if _BAD_URL_CHARACTERS.search(raw):
        raise _LinkError("BAD_CHARACTERS", "Link có khoảng trắng hoặc ký tự điều khiển.")
    try:
        parts = urlsplit(raw)
        hostname = parts.hostname
        has_userinfo = parts.username is not None or parts.password is not None
    except ValueError:  # "https://[::1/", a netloc that changes under NFKC
        raise _LinkError("BAD_URL", "Link không đúng dạng.") from None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not raw.lower().startswith(scheme + "://"):
        raise _LinkError("BAD_SCHEME", "Chỉ nhận link http:// hoặc https://.")
    if has_userinfo:
        raise _LinkError("USERINFO", "Link không được chứa tên đăng nhập hay mật khẩu.")
    try:
        port = parts.port
    except ValueError:
        raise _LinkError("BAD_PORT", "Cổng trong link không hợp lệ.") from None
    if port not in (None, _DEFAULT_PORTS[scheme]):
        raise _LinkError("BAD_PORT", "Link không được dùng cổng riêng.")
    if not hostname:
        raise _LinkError("NO_HOST", "Link thiếu tên miền.")
    if _is_ip_literal(hostname):
        raise _LinkError("IP_LITERAL", "Link phải dùng tên miền, không dùng địa chỉ IP.")
    try:
        host = normalize_host(hostname)
    except ValueError:
        raise _LinkError("NO_HOST", "Tên miền trong link không hợp lệ.") from None
    if not all(_HOST_LABEL.fullmatch(label) for label in host.split(".")):
        raise _LinkError("NO_HOST", "Tên miền trong link có ký tự không hợp lệ.")
    if not host_matches(host, source.domains):
        raise _LinkError("HOST_NOT_ALLOWED", f"Tên miền {host} không thuộc nguồn {source.label}.")
    path = parts.path or "/"
    url = f"{scheme}://{host}{path}" + (f"?{parts.query}" if parts.query else "")
    return url, host, _DEFAULT_PORTS[scheme]


def _check_dns(host: str, port: int, resolver: Resolver) -> tuple[str, str] | None:
    try:
        addresses = list(resolver(host, port))
    except (OSError, UnicodeError):
        addresses = []
    if not addresses:
        return "DNS_FAILED", f"Không phân giải được tên miền {host}."
    try:
        internal = any(_is_internal(address) for address in addresses)
    except ValueError:
        internal = True
    if internal:
        return "PRIVATE_ADDRESS", f"Tên miền {host} trỏ tới địa chỉ nội bộ."
    return None


def validate_batch(source: DownloadSource, lines: Iterable[str], *,
                   resolver: Resolver = default_resolver) -> list[str]:
    """Normalized links of a batch; any bad or duplicate link rejects the batch."""
    entries = [(number, str(line).strip()) for number, line in enumerate(lines, start=1)]
    entries = [(number, line) for number, line in entries if line]
    if not entries:
        raise DownloadBatchError("Chưa có link nào.", [{"line": 0, "code": "EMPTY_BATCH",
                                                        "message": "Chưa có link nào."}])
    if len(entries) > MAX_BATCH_LINKS:
        message = f"Mỗi lần tối đa {MAX_BATCH_LINKS} link (đang có {len(entries)})."
        raise DownloadBatchError(message, [{"line": 0, "code": "TOO_MANY_LINKS", "message": message}])
    errors: list[dict[str, Any]] = []
    urls: list[str] = []
    seen: dict[str, int] = {}
    hosts: dict[str, tuple[str, str] | None] = {}
    for number, line in entries:
        try:
            url, host, port = _check_url(line, source)
        except _LinkError as error:
            errors.append({"line": number, "url": line[:200], "code": error.code, "message": error.message})
            continue
        if url in seen:
            errors.append({"line": number, "url": line[:200], "code": "DUPLICATE_IN_BATCH",
                           "message": f"Trùng với link ở dòng {seen[url]}."})
            continue
        seen[url] = number
        if host not in hosts:
            hosts[host] = _check_dns(host, port, resolver)
        if hosts[host] is not None:
            code, message = hosts[host]
            errors.append({"line": number, "url": line[:200], "code": code, "message": message})
            continue
        urls.append(url)
    if errors:
        raise DownloadBatchError(f"Lô bị từ chối: {len(errors)} link không hợp lệ.", errors)
    return urls
