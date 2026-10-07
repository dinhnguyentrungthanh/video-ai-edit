"""The backend check of a batch of links.

Any public http(s) page is accepted; the yt-dlp probe then decides whether the
page holds a video it can read (``download_probe``). A link is refused before
that when it is malformed, carries an account, a port or an IP address, or its
host resolves to an internal address. yt-dlp follows redirects on its own, so
the DNS check only covers the first host there; BiliFlow's own source providers
(``download_http``) apply ``check_link`` and ``public_addresses`` to every
redirect and derived link.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import unicodedata
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

MAX_BATCH_LINKS = 20
MAX_URL_LENGTH = 2048
_BAD_URL_CHARACTERS = re.compile(r"[\x00-\x20\x7f]")
_DEFAULT_PORTS = {"http": 80, "https": 443}
# IPv6 ranges that carry an IPv4 address in their last 32 bits (IPv4-compatible, SIIT, NAT64), and IPv6
# ranges that only exist inside a network (deprecated site-local, local-use NAT64).
_EMBEDDED_V4 = tuple(ipaddress.ip_network(net) for net in ("::/96", "::ffff:0:0:0/96", "64:ff9b::/96"))
_LOCAL_V6 = tuple(ipaddress.ip_network(net) for net in ("fec0::/10", "64:ff9b:1::/48"))
# One DNS label after IDNA. Nothing such as a backslash or "%" that another URL
# parser (urllib3, requests) would read as the end of the host.
_HOST_LABEL = re.compile(r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?")
# Names that only exist inside a network; refused before DNS (a proxy would resolve them itself).
LOCAL_SUFFIXES = ("localhost", "local", "internal", "lan", "home.arpa", "localdomain")
# What Python's IDNA 2003 codec reads otherwise than a browser (UTS 46), so the host BiliFlow reads would not
# be the one a browser opens: sharp s and final sigma become "ss" and "σ"; the zero-width joiners and the
# Mongolian todo soft hyphen vanish.
_IDNA_DEVIATIONS = frozenset(map(chr, (0x00DF, 0x03C2, 0x200C, 0x200D, 0x1806)))
# The codec knows Unicode 3.2 only: a character added since (Cherokee small letters, newer variation
# selectors…) is neither folded nor dropped as a browser does.
_IDNA_2003_UNICODE = unicodedata.ucd_3_2_0

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


class LinkRejected(ValueError):
    """One link refused by the same rules as a pasted link (``download_http`` checks every derived link)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


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
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif any(ip in network for network in _LOCAL_V6):
            return True
        elif any(ip in network for network in _EMBEDDED_V4):
            ip = ipaddress.ip_address(int(ip) & 0xFFFFFFFF)  # judged by the IPv4 address it carries
    return not ip.is_global or ip.is_multicast


def _is_numeric_shorthand(host: str) -> bool:
    """An address the resolver still reads although ``ipaddress`` does not ("127.1", "0x7f.1", "2130706433"):
    no top-level domain is all digits or starts with "0x"."""
    last = host.rsplit(".", 1)[-1]
    return last.isdigit() or last.startswith("0x")


def _read_otherwise_by_a_browser(host: str) -> bool:
    return any(character in _IDNA_DEVIATIONS
               or (not character.isascii() and _IDNA_2003_UNICODE.category(character) == "Cn")
               for character in host)


def _checked_host(hostname: str | None) -> str:
    """The host of a link, normalized (``normalize_host``): a full public domain name, never an address."""
    if not hostname:
        raise _LinkError("NO_HOST", "Link thiếu tên miền.")
    if _is_ip_literal(hostname):
        raise _LinkError("IP_LITERAL", "Link phải dùng tên miền, không dùng địa chỉ IP.")
    if _read_otherwise_by_a_browser(hostname.lower()):
        raise _LinkError("NO_HOST", "Tên miền có ký tự mà trình duyệt đọc khác BiliFlow: ß, ς, ký tự nối vô hình "
                         "hay ký tự Unicode thêm sau bản 3.2. Hãy dùng dạng xn--… của tên miền này.")
    try:
        host = normalize_host(hostname)
    except ValueError:
        raise _LinkError("NO_HOST", "Tên miền trong link không hợp lệ.") from None
    if not all(_HOST_LABEL.fullmatch(label) for label in host.split(".")):
        raise _LinkError("NO_HOST", "Tên miền trong link có ký tự không hợp lệ.")
    if _is_ip_literal(host) or _is_numeric_shorthand(host):  # "１.１.１.１" or "1.2.3.4." only after IDNA
        raise _LinkError("IP_LITERAL", "Link phải dùng tên miền, không dùng địa chỉ IP.")
    if "." not in host:
        raise _LinkError("NO_HOST", "Link phải có tên miền đầy đủ, ví dụ video.example.")
    if any(host == suffix or host.endswith("." + suffix) for suffix in LOCAL_SUFFIXES):
        raise _LinkError("LOCAL_HOST", f"Tên miền {host} chỉ có trong mạng nội bộ.")
    return host


def _check_url(raw: str, max_length: int = MAX_URL_LENGTH) -> tuple[str, str, int]:
    """Return (normalized url, host, port) or raise _LinkError."""
    if len(raw) > max_length:
        raise _LinkError("URL_TOO_LONG", f"Link dài quá {max_length} ký tự.")
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
    host = _checked_host(hostname)
    path = parts.path or "/"
    url = f"{scheme}://{host}{path}" + (f"?{parts.query}" if parts.query else "")
    return url, host, _DEFAULT_PORTS[scheme]


def is_public_address(address: str) -> bool:
    """False for loopback, private, link-local, multicast, reserved and unreadable addresses."""
    try:
        return not _is_internal(address)
    except ValueError:
        return False


def check_link(raw: str, *, max_length: int = MAX_URL_LENGTH) -> tuple[str, str, int]:
    """(normalized url, host, port) of one link, or LinkRejected; no DNS lookup."""
    try:
        return _check_url(raw, max_length)
    except _LinkError as error:
        raise LinkRejected(error.code, error.message) from None


def check_host(name: str) -> str:
    """A bare host name (a provider's host list) by the rules of a link's host, normalized; LinkRejected
    for anything else, such as a wildcard, a port, a path, an account or an IP address."""
    try:
        return _checked_host(name)
    except _LinkError as error:
        raise LinkRejected(error.code, error.message) from None


def public_addresses(host: str, port: int, resolver: Resolver,
                     is_public: Callable[[str], bool] = is_public_address) -> list[str]:
    """Every address ``host`` resolves to; LinkRejected when there is none or one is internal."""
    try:
        addresses = list(resolver(host, port))
    except (OSError, UnicodeError):
        addresses = []
    if not addresses:
        raise LinkRejected("DNS_FAILED", f"Không phân giải được tên miền {host}.")
    if not all(is_public(address) for address in addresses):
        raise LinkRejected("PRIVATE_ADDRESS", f"Tên miền {host} trỏ tới địa chỉ nội bộ.")
    return addresses


def _check_dns(host: str, port: int, resolver: Resolver) -> tuple[str, str] | None:
    try:
        public_addresses(host, port, resolver)
    except LinkRejected as error:
        return error.code, error.message
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
