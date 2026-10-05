"""The backend check of a batch of links.

Any public http(s) page is accepted; the yt-dlp probe then decides whether the
page holds a video it can read (``download_probe``). A link is refused before
that when it is malformed, carries an account, a port or an IP address, or its
host resolves to an internal address. yt-dlp follows redirects on its own, so
the DNS check only covers the first host.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

MAX_BATCH_LINKS = 20
MAX_URL_LENGTH = 2048
_BAD_URL_CHARACTERS = re.compile(r"[\x00-\x20\x7f]")
_DEFAULT_PORTS = {"http": 80, "https": 443}
# One DNS label after IDNA. Nothing such as a backslash or "%" that another URL
# parser (urllib3, requests) would read as the end of the host.
_HOST_LABEL = re.compile(r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?")
# Names that only exist inside a network; refused before DNS (a proxy would resolve them itself).
LOCAL_SUFFIXES = ("localhost", "local", "internal", "lan", "home.arpa", "localdomain")

Resolver = Callable[[str, int], Iterable[str]]


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


def normalize_host(host: str) -> str:
    """Lower-case IDNA form without a trailing dot; raises ValueError when invalid."""
    cleaned = host.strip().rstrip(".").lower()
    if not cleaned:
        raise ValueError("empty host")
    try:
        return cleaned.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValueError(f"invalid host {host!r}") from error


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    return True


def default_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _is_internal(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not ip.is_global or ip.is_multicast


def _check_url(raw: str) -> tuple[str, str, int]:
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
    if _is_ip_literal(host):  # "１.１.１.１" or "1.2.3.4." only become an address after IDNA
        raise _LinkError("IP_LITERAL", "Link phải dùng tên miền, không dùng địa chỉ IP.")
    if "." not in host:
        raise _LinkError("NO_HOST", "Link phải có tên miền đầy đủ, ví dụ video.example.")
    if any(host == suffix or host.endswith("." + suffix) for suffix in LOCAL_SUFFIXES):
        raise _LinkError("LOCAL_HOST", f"Tên miền {host} chỉ có trong mạng nội bộ.")
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


def validate_batch(lines: Iterable[str], *, resolver: Resolver = default_resolver) -> list[str]:
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
            url, host, port = _check_url(line)
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
