"""HTTP of BiliFlow's own source transfers: every request, redirect and derived link passes the network policy.

yt-dlp keeps its own networking for the links it handles (``download_runner``). For a source that a
provider resolved (``download_sources``), BiliFlow fetches the media itself, so every link it opens,
including each redirect, playlist, segment and media link, is checked first:

- the rules of a pasted link (``download_links.check_link``): http or https on the default port, no
  account in the link, no IP literal, a full public host name;
- the host is resolved here, every address must be public, and the socket connects to that checked
  address: no second lookup that a DNS rebinding could change. TLS still verifies the certificate
  for the host name (SNI and the Host header carry the name);
- redirects are followed by hand, at most ``MAX_REDIRECTS``, each target checked again;
- bounded reads, connect and read timeouts, no cookies, no proxy, no credentials, allow-listed
  request headers only;
- a stop or a cancel (``ProcessControl`` or a ``Scope``) shuts the open socket at once.

The headers are the ones yt-dlp already sends for every link (a common browser User-Agent); no TLS
or client fingerprint is imitated and no cookie is ever sent. Messages name the host only, never the
link: a signed link carries its token in the URL.
"""
from __future__ import annotations

import http.client
import socket
import ssl
import threading
import zlib
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Mapping, Protocol, TypeVar
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from biliflow.download_links import (
    LinkRejected,
    Resolver,
    check_link,
    default_resolver,
    is_public_address,
    public_addresses,
)

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
CONNECT_TIMEOUT_SECONDS = 10.0
READ_TIMEOUT_SECONDS = 30.0
MAX_REDIRECTS = 5
# A derived link (a playlist entry, a redirect) may carry a long signature; a pasted one stays at 2048.
MAX_DERIVED_URL_LENGTH = 8192
CHUNK_BYTES = 256 * 1024
ALLOWED_HEADERS = frozenset({"referer", "origin", "accept", "accept-language", "range", "if-range", "content-type"})
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_DECODERS = {"gzip": 16 + zlib.MAX_WBITS, "deflate": zlib.MAX_WBITS, "x-gzip": 16 + zlib.MAX_WBITS}
# Percent-encodes only what HTTP cannot carry (non-ASCII, spaces); "%" stays, so an encoded link is unchanged.
_PATH_SAFE = "!#$%&'()*+,/:;=?@[]~"

Connector = Callable[[str, int, float], socket.socket]
T = TypeVar("T")


class Interruptible(Protocol):
    """What a request needs from a task: ``ProcessControl`` or a ``Scope``."""

    @property
    def requested(self) -> bool: ...

    def wait(self, seconds: float) -> bool: ...

    def add_closer(self, closer: Callable[[], None]) -> Callable[[], None]: ...


class HttpError(Exception):
    """A request that failed. ``retryable``: a network error, 429 or 5xx; ``status`` when the server answered."""

    def __init__(self, code: str, message: str, *, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable


class Cancelled(HttpError):
    """The task asked to end (stop, cancel, shutdown) or the transfer aborted itself."""

    def __init__(self) -> None:
        super().__init__("CANCELLED", "Đã dừng theo yêu cầu.")


def default_connector(address: str, port: int, timeout: float) -> socket.socket:
    return socket.create_connection((address, port), timeout=timeout)


def _abort_socket(holder: dict[str, socket.socket]) -> None:
    """End at once a request that another thread is reading (a stop, a cancel, an aborted wave).

    The socket is the one kept right after connecting: http.client hands it to the response of a
    ``Connection: close`` request and clears ``connection.sock``. socket.socket.shutdown on purpose
    (SSLSocket.shutdown would drop its SSL object under the reading thread). Windows does not wake a
    blocked recv on a shutdown, so the handle is closed too: the base class close, because
    socket.close() waits for the response's file object to be closed first. The reader then fails
    with an OSError that ``open`` and ``Response.chunks`` turn into Cancelled.
    """
    sock = holder.get("sock")
    if sock is None:
        return
    try:
        socket.socket.shutdown(sock, socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        super(socket.socket, sock).close()
    except OSError:
        pass


class Scope:
    """A task's control seen through a local abort. A transfer ends its own requests (a failed segment,
    the size guard) without asking the task to stop; a request of the task reaches every scope."""

    def __init__(self, parent: Interruptible):
        self.parent = parent
        self._aborted = threading.Event()
        self._lock = threading.Lock()
        self._closers: dict[int, Callable[[], None]] = {}
        self._next = 0
        self._detach = parent.add_closer(self.abort)

    @property
    def requested(self) -> bool:
        return self._aborted.is_set() or self.parent.requested

    @property
    def aborted(self) -> bool:
        return self._aborted.is_set()

    def abort(self) -> None:
        with self._lock:
            self._aborted.set()
            closers = list(self._closers.values())
        for closer in closers:
            try:
                closer()
            except Exception:  # noqa: BLE001 - a socket already closed
                pass

    def wait(self, seconds: float) -> bool:
        return self._aborted.wait(seconds) or self.parent.requested

    def add_closer(self, closer: Callable[[], None]) -> Callable[[], None]:
        with self._lock:
            key = self._next
            self._next += 1
            self._closers[key] = closer
            aborted = self._aborted.is_set()
        if aborted:
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass

        def remove() -> None:
            with self._lock:
                self._closers.pop(key, None)
        return remove

    def close(self) -> None:
        self._detach()


@dataclass(frozen=True)
class Target:
    url: str
    scheme: str
    host: str
    port: int
    path: str
    addresses: tuple[str, ...]  # every one checked public; tried in this order


def _status_error(status: int, host: str) -> HttpError:
    if status in (401, 407):
        return HttpError("LOGIN_REQUIRED", f"{host} đòi đăng nhập (HTTP {status}); BiliFlow không dùng tài khoản "
                         "hay cookie nên không tải.", status=status)
    if status == 403:
        return HttpError("FORBIDDEN", f"{host} từ chối (HTTP 403): link đã hết hạn hoặc không cho tải.", status=status)
    if status in (404, 410):
        return HttpError("UNAVAILABLE", f"{host} báo không còn file (HTTP {status}).", status=status)
    if status == 451:
        return HttpError("UNAVAILABLE", f"{host} chặn nội dung này (HTTP 451).", status=status)
    if status == 429 or status >= 500:
        return HttpError("SERVER_BUSY", f"{host} đang bận hoặc lỗi (HTTP {status}).", status=status, retryable=True)
    return HttpError("HTTP_ERROR", f"{host} trả về HTTP {status}.", status=status)


def _network_error(host: str, error: BaseException) -> HttpError:
    if isinstance(error, ssl.SSLCertVerificationError):
        return HttpError("TLS_ERROR", f"Chứng chỉ HTTPS của {host} không hợp lệ; không tải.")
    if isinstance(error, (socket.timeout, TimeoutError)):
        return HttpError("NETWORK", f"{host} không trả lời kịp (quá thời gian chờ).", retryable=True)
    return HttpError("NETWORK", f"Mất kết nối với {host} ({type(error).__name__}).", retryable=True)


class Response:
    """An open response after the redirects; read with ``chunks``, ``read_some`` or ``read_all``, then close."""

    def __init__(self, connection: http.client.HTTPConnection, raw: http.client.HTTPResponse, target: Target,
                 remove_closer: Callable[[], None], control: Interruptible):
        self._connection = connection
        self._raw = raw
        self._remove_closer = remove_closer
        self._control = control
        self.target = target
        self.status = raw.status
        self.headers = raw.headers
        self.url = target.url
        self.host = target.host
        chunked = "chunked" in (raw.getheader("Transfer-Encoding") or "").lower()
        length = (raw.getheader("Content-Length") or "").strip()
        self.content_length = int(length) if length.isascii() and length.isdigit() and not chunked else None
        self._received = 0

    def header(self, name: str) -> str:
        return (self._raw.getheader(name) or "").strip()

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._remove_closer()
        try:
            self._raw.close()
        finally:
            self._connection.close()

    def chunks(self, size: int = CHUNK_BYTES) -> Iterator[bytes]:
        """The body as it arrives (at most ``size`` bytes a piece, from one read of the socket, so progress
        follows a slow server); a body shorter than its Content-Length is a (retryable) network error."""
        while True:
            if self._control.requested:
                raise Cancelled()
            try:
                data = self._raw.read1(size)
            except Exception as error:  # noqa: BLE001 - every read failure is mapped, never raised raw
                if self._control.requested:
                    raise Cancelled() from None
                if isinstance(error, (OSError, http.client.HTTPException, ValueError)):
                    raise _network_error(self.host, error) from None
                raise
            if self._control.requested:
                raise Cancelled()
            if not data:
                if self.content_length is not None and self._received < self.content_length:
                    raise HttpError("NETWORK", f"{self.host} ngắt kết nối giữa chừng.", retryable=True)
                return
            self._received += len(data)
            yield data

    def read_some(self, limit: int) -> bytes:
        """At most ``limit`` bytes from the start of the body (the rest is never read)."""
        body = bytearray()
        for chunk in self.chunks(min(CHUNK_BYTES, limit)):
            body += chunk
            if len(body) >= limit:
                return bytes(body[:limit])
        return bytes(body)

    def read_all(self, limit: int) -> bytes:
        """The whole body (decompressed when the server compressed it anyway), at most ``limit`` bytes."""
        body = bytearray()
        for chunk in self.chunks():
            body += chunk
            if len(body) > limit:
                raise HttpError("TOO_LARGE_RESPONSE", f"Phản hồi của {self.host} lớn quá {limit} byte; dừng để "
                                "tránh đọc nhầm nội dung.")
        encoding = self.header("Content-Encoding").lower()
        if encoding in ("", "identity"):
            return bytes(body)
        if encoding not in _DECODERS:
            raise HttpError("BAD_RESPONSE", f"{self.host} nén phản hồi kiểu {encoding[:20]!r}, chưa hỗ trợ.")
        decoder = zlib.decompressobj(_DECODERS[encoding])
        try:
            data = decoder.decompress(bytes(body), limit + 1)
        except zlib.error:
            raise HttpError("BAD_RESPONSE", f"Phản hồi nén của {self.host} bị lỗi.") from None
        if len(data) > limit or decoder.unconsumed_tail:
            raise HttpError("TOO_LARGE_RESPONSE", f"Phản hồi của {self.host} lớn quá {limit} byte khi giải nén.")
        if not decoder.eof:  # a compressed body cut short is never taken as the whole playlist
            raise HttpError("BAD_RESPONSE", f"Phản hồi nén của {self.host} bị cắt giữa chừng.")
        return data


def allowed_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    """Only the allow-listed request headers, without line breaks; anything else is a programming error."""
    result: dict[str, str] = {}
    for name, value in (headers or {}).items():
        if name.lower() not in ALLOWED_HEADERS:
            raise ValueError(f"Header {name!r} is not allowed for source requests")
        text = str(value)
        if "\r" in text or "\n" in text:
            raise ValueError(f"Header {name!r} has a line break")
        result[name] = text
    return result


_SCHEME_PORTS = {"http": 80, "https": 443}


def _referer_for(target: Target, headers: dict[str, str]) -> dict[str, str]:
    """``headers`` with a provider's Referer as a browser sends it by default (strict-origin-when-cross-origin):
    the whole page link only to that page's own origin, its origin only to any other one (a CDN, a redirect),
    nothing from https to http, never an account or a fragment. So a page link's path and query (maybe a
    token) never reach another host."""
    result: dict[str, str] = {}
    for name, value in headers.items():
        if name.lower() == "referer":
            value = _referer_value(value, target)
            if value is None:
                continue
        result[name] = value
    return result


def _referer_value(page: str, target: Target) -> str | None:
    try:
        parts = urlsplit(page)
        port = parts.port
    except ValueError:  # unparsable, or a bad port
        return None
    scheme, host = parts.scheme.lower(), parts.hostname
    if scheme not in _SCHEME_PORTS or not host or (scheme == "https" and target.scheme != "https"):
        return None
    authority = parts.netloc.rpartition("@")[2]  # never an account
    if port == _SCHEME_PORTS[scheme]:
        authority = authority.rpartition(":")[0]  # the default port is not written
    if not authority.isascii():  # a host not in its IDNA form: no header can carry it
        return None
    if (scheme, host, port or _SCHEME_PORTS[scheme]) == (target.scheme, target.host, target.port):
        # Percent-encoded like the request line (check): a header carries no "tập-1".
        return urlunsplit((scheme, authority, quote(parts.path or "/", safe=_PATH_SAFE),
                           quote(parts.query, safe=_PATH_SAFE), ""))
    return f"{scheme}://{authority}/"


class SafeHttp:
    """The only HTTP client of the source transfers (see the module docstring)."""

    def __init__(self, *, resolver: Resolver = default_resolver, is_public: Callable[[str], bool] = is_public_address,
                 connector: Connector = default_connector, ssl_context: ssl.SSLContext | None = None,
                 connect_timeout: float = CONNECT_TIMEOUT_SECONDS, read_timeout: float = READ_TIMEOUT_SECONDS,
                 max_redirects: int = MAX_REDIRECTS, user_agent: str = USER_AGENT):
        self.resolver = resolver
        self.is_public = is_public
        self.connector = connector
        self.context = ssl_context or ssl.create_default_context()
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_redirects = max_redirects
        self.user_agent = user_agent

    def check(self, url: str) -> Target:
        """The policy of one link: shape, then DNS (every address public); HttpError when refused.
        A failed lookup is retryable (the network may be down for a moment); a refused link is not."""
        try:
            normalized, host, port = check_link(url, max_length=MAX_DERIVED_URL_LENGTH)
            addresses = public_addresses(host, port, self.resolver, self.is_public)
        except LinkRejected as error:
            raise HttpError(error.code, error.message, retryable=error.code == "DNS_FAILED") from None
        parts = urlsplit(normalized)
        path = quote(parts.path or "/", safe=_PATH_SAFE) + (f"?{quote(parts.query, safe=_PATH_SAFE)}"
                                                             if parts.query else "")
        return Target(normalized, parts.scheme, host, port, path, tuple(addresses))

    def _connection(self, target: Target) -> http.client.HTTPConnection:
        if target.scheme == "https":
            connection: http.client.HTTPConnection = http.client.HTTPSConnection(
                target.host, target.port, timeout=self.connect_timeout, context=self.context)
        else:
            connection = http.client.HTTPConnection(target.host, target.port, timeout=self.connect_timeout)
        connector = self.connector

        def create_connection(_address: Any, timeout: float | None = None, _source: Any = None) -> socket.socket:
            # The checked addresses in turn, never a new lookup of the host name.
            failure: OSError | None = None
            for address in target.addresses:
                try:
                    return connector(address, target.port, timeout or self.connect_timeout)
                except OSError as error:
                    failure = error
            raise failure or OSError("no address")
        connection._create_connection = create_connection  # type: ignore[attr-defined]
        return connection

    def _headers(self, extra: dict[str, str]) -> dict[str, str]:
        return {"User-Agent": self.user_agent, "Accept": "*/*", "Accept-Encoding": "identity",
                "Connection": "close", **extra}

    def open(self, url: str, control: Interruptible, *, headers: Mapping[str, str] | None = None,
             method: str = "GET", body: bytes | None = None) -> Response:
        """GET ``url`` (checked), following checked redirects; a non-2xx answer is an HttpError. A provider's
        Referer goes out as a browser sends it by default (``_referer_for``), on every hop."""
        if method not in ("GET", "POST") or (method == "GET" and body is not None):
            raise ValueError("Only GET and bounded POST source requests are supported")
        if body is not None and (not isinstance(body, bytes) or len(body) > 1024 * 1024):
            raise ValueError("Source POST body exceeds the one-MiB limit")
        extra = allowed_headers(headers)
        current = url
        for _ in range(self.max_redirects + 1):
            if control.requested:
                raise Cancelled()
            target = self.check(current)
            connection = self._connection(target)
            holder: dict[str, socket.socket] = {}
            remove = control.add_closer(lambda kept=holder: _abort_socket(kept))
            try:
                connection.connect()
                holder["sock"] = connection.sock  # the TLS socket for https; kept after getresponse() drops it
                if control.requested:  # asked while connecting: the closer had no socket to shut yet
                    raise Cancelled()
                if connection.sock is not None:
                    connection.sock.settimeout(self.read_timeout)
                connection.request(method, target.path, body=body, headers=self._headers(_referer_for(target, extra)))
                raw = connection.getresponse()
            except Exception as error:  # noqa: BLE001 - mapped below, the socket is always closed
                remove()
                connection.close()
                if control.requested:
                    raise Cancelled() from None
                if isinstance(error, (OSError, http.client.HTTPException, ValueError)):
                    raise _network_error(target.host, error) from None
                raise
            if raw.status in _REDIRECTS:
                redirect_status = raw.status
                location = (raw.getheader("Location") or "").strip()
                raw.close()
                connection.close()
                remove()
                if not location:
                    raise HttpError("BAD_REDIRECT", f"{target.host} chuyển hướng mà không có địa chỉ mới.")
                try:
                    current = urljoin(target.url, location)
                except ValueError:
                    raise HttpError("BAD_REDIRECT", f"{target.host} chuyển hướng tới địa chỉ hỏng.") from None
                if target.scheme == "https" and urlsplit(current).scheme.lower() == "http":
                    raise HttpError("DOWNGRADE", f"{target.host} chuyển từ https sang http; không tải qua kết nối "
                                    "không mã hóa.")
                if method == "POST":
                    old_origin = (target.scheme, target.host, target.port)
                    next_target = self.check(current)
                    if (next_target.scheme, next_target.host, next_target.port) != old_origin:
                        raise HttpError("CROSS_ORIGIN_POST", "API chuyển POST sang nguồn khác; dừng.")
                    if redirect_status in (301, 302, 303):
                        method, body = "GET", None
                        extra = {key: value for key, value in extra.items()
                                 if key.lower() not in ("content-type", "origin")}
                continue
            if not 200 <= raw.status < 300:
                raw.close()
                connection.close()
                remove()
                raise _status_error(raw.status, target.host)
            return Response(connection, raw, target, remove, control)
        raise HttpError("TOO_MANY_REDIRECTS", f"Quá {self.max_redirects} lần chuyển hướng; dừng.")

    def fetch(self, url: str, control: Interruptible, *, limit: int,
              headers: Mapping[str, str] | None = None, method: str = "GET",
              body: bytes | None = None) -> tuple[bytes, Response]:
        """The whole (small) body of ``url`` and its closed response (final URL, status, headers)."""
        with self.open(url, control, headers=headers, method=method, body=body) as response:
            return response.read_all(limit), response


def with_retries(action: Callable[[], T], control: Interruptible, *, attempts: int,
                 on_retry: Callable[[int, HttpError], None] | None = None,
                 base_delay: float = 1.0, max_delay: float = 8.0) -> T:
    """Run ``action``; a retryable HttpError is tried again up to ``attempts`` times after an
    interruptible pause (1, 2, 4, 8 s…); a stop, a cancel or a final error is raised."""
    for attempt in range(attempts + 1):
        try:
            return action()
        except Cancelled:
            raise
        except HttpError as error:
            if not error.retryable or attempt >= attempts:
                raise
            if on_retry is not None:
                on_retry(attempt + 1, error)
            if control.wait(min(max_delay, base_delay * 2 ** attempt)):
                raise Cancelled() from None
    raise AssertionError("unreachable")
