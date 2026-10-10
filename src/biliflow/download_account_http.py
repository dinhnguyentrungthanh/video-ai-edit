"""HTTP of the session browser (docs/SOURCE_ACCOUNTS_PLAN.md, M2a, design B): one checked exchange per call.

The browser of a source account never reaches the network by itself: the route handler of
``download_account_browser`` hands every request to ``SessionHttp.send``, which answers it from Python.
One call is one HTTP exchange. No redirect is followed here: a 3xx comes back as it is and the browser
layer decides what it becomes (each next hop is a new request, checked again). Per request:

- https only, on the default port, to a host on that source's own list (exact names). Any other host is
  refused before a DNS lookup;
- the policy of ``SafeHttp.check`` (``download_http``): the shape of the link, every resolved address
  public. The socket connects only to those checked addresses, with no second lookup, so DNS
  rebinding cannot change it. TLS verifies the certificate for the host name (SNI carries the name);
- a deadline for the whole exchange: DNS (a lookup that does not answer in time fails), connect, TLS,
  request, headers and body. A watchdog closes the socket when it passes. Connect and read timeouts as
  well; a TLS stream cut without close_notify is an error, never the end of an answer;
- a byte limit for the body, also after decompression, and a bounded request body;
- the request headers the browser built, cookies included: the browser's own jar applied domain, path,
  secure, expiry and SameSite for that URL. Hop-by-hop, framing, conditional and range headers are
  dropped;
- a stop or a cancel (``Interruptible``) closes the socket at once.

``SafeHttp`` itself is unchanged: it still sends no cookie, and the anonymous providers keep using it.
Nothing here logs. Errors (``HttpError``) name the host only, never the path, the query, a header or
a body: a signed link carries its token in the URL, and a request body may carry a password.
"""
from __future__ import annotations

import http.client
import socket
import ssl
import threading
import time
import zlib
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlsplit

from biliflow.download_http import (
    CONNECT_TIMEOUT_SECONDS,
    MAX_DERIVED_URL_LENGTH,
    READ_TIMEOUT_SECONDS,
    Cancelled,
    Connector,
    HttpError,
    Interruptible,
    SafeHttp,
    Target,
    default_connector,
)
from biliflow.download_links import LinkRejected, Resolver, check_link, default_resolver, is_public_address

REQUEST_SECONDS = 30.0  # one whole exchange, from the DNS lookup to the last byte of the body
MAX_REQUEST_SECONDS = 120.0  # the most a caller may allow one exchange (and each of its timeouts)
MAX_RESPONSE_BYTES = 16 * 1024 * 1024  # one answer (a sign-in page's script bundle fits)
MAX_RESPONSE_CAP = 64 * 1024 * 1024  # the most a caller may allow one answer
MAX_REQUEST_BODY = 1024 * 1024
CHUNK_BYTES = 64 * 1024
DNS_POLL_SECONDS = 0.05  # how often a running DNS lookup looks for a stop or a cancel
METHODS = frozenset({"GET", "HEAD", "POST", "OPTIONS"})
# Never forwarded: framing and connection control, proxy credentials, the encoding (always identity), and
# conditional or range headers (the browser always gets the whole, bounded answer, never a cached 304).
DROPPED_REQUEST_HEADERS = frozenset({
    "host", "connection", "keep-alive", "proxy-authorization", "proxy-connection", "te", "trailer",
    "transfer-encoding", "upgrade", "content-length", "accept-encoding", "expect", "if-none-match",
    "if-modified-since", "if-match", "if-unmodified-since", "if-range", "range"})
# What the exchange itself used up: the body handed on is whole and decoded.
FRAMING_RESPONSE_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-connection", "transfer-encoding", "content-length", "content-encoding",
    "te", "trailer", "upgrade"})
_DECODERS = {"gzip": 16 + zlib.MAX_WBITS, "x-gzip": 16 + zlib.MAX_WBITS, "deflate": zlib.MAX_WBITS}


@dataclass(frozen=True)
class SessionNetwork:
    """How SessionHttp reaches the network. Production keeps the defaults; only a test replaces them (a
    fixture server's resolver, connector and TLS context): there is no other way to reach a local address.
    The defaults are the system resolver, ``is_public_address``, a plain TCP connection and the system CAs
    with host-name checking (``ssl.create_default_context``); a test pins them."""
    resolver: Resolver = default_resolver
    is_public: Callable[[str], bool] = is_public_address
    connector: Connector = default_connector
    ssl_context: ssl.SSLContext | None = None


@dataclass(frozen=True)
class SessionReply:
    """One answer: status and headers as received (repeated names kept, framing removed), the whole body."""
    url: str = field(repr=False)
    status: int
    headers: tuple[tuple[str, str], ...] = field(repr=False)
    body: bytes = field(repr=False)

    def values(self, name: str) -> list[str]:
        wanted = name.lower()
        return [value for key, value in self.headers if key.lower() == wanted]

    def header(self, name: str) -> str:
        values = self.values(name)
        return values[0].strip() if values else ""


def _abort(holder: dict[str, socket.socket]) -> None:
    """Close the socket of a running exchange from another thread (see ``download_http._abort_socket``)."""
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


def _bounded_resolver(resolver: Resolver, seconds: float, current: threading.local) -> Resolver:
    """``resolver`` with a time limit: the lookup runs in a helper thread and a lookup that has not answered
    in ``seconds``, or whose task was stopped or cancelled meanwhile (``current.control``), counts as failed.
    The system resolver cannot be interrupted; its thread is left to end by itself."""
    def lookup(host: str, port: int) -> list[str]:
        result: list = []

        def run() -> None:
            try:
                result.append(("ok", list(resolver(host, port))))
            except BaseException as error:  # noqa: BLE001 - handed back to the caller below
                result.append(("error", error))
        worker = threading.Thread(target=run, name="session-dns", daemon=True)
        worker.start()
        deadline = time.monotonic() + seconds
        control = getattr(current, "control", None)
        while worker.is_alive() and time.monotonic() < deadline:
            if control is not None and control.requested:
                raise OSError("DNS lookup cancelled")  # ``send`` turns it into Cancelled
            worker.join(min(DNS_POLL_SECONDS, max(0.0, deadline - time.monotonic())))
        if not result:
            raise OSError("DNS lookup timed out")
        kind, value = result[0]
        if kind == "error":
            raise value
        return value
    return lookup


class _Exchange:
    """What one exchange holds and what may end it early. The deadline: a watchdog closes the socket. A stop
    or a cancel of the task: a closer closes it. The socket is kept from its first moment (TCP, then the TLS
    socket before its handshake), and ``keep`` refuses to go on once either has happened, so a cancel that
    came while connecting is never missed. Leaving the ``with`` closes the response, the connection and the
    socket."""

    def __init__(self, control: Interruptible, seconds: float):
        self.control = control
        self.deadline = time.monotonic() + seconds
        self.expired = threading.Event()
        self.holder: dict[str, socket.socket] = {}
        self.connection: http.client.HTTPConnection | None = None
        self.raw: http.client.HTTPResponse | None = None
        self._watchdog = threading.Timer(seconds, self._expire)
        self._watchdog.daemon = True
        self._remove: Callable[[], None] = lambda: None

    def _expire(self) -> None:
        self.expired.set()
        _abort(self.holder)

    @property
    def ended(self) -> bool:
        return self.expired.is_set() or self.control.requested

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def keep(self, sock: socket.socket) -> None:
        self.holder["sock"] = sock
        if self.ended:
            raise TimeoutError("the exchange ended")  # mapped to TIMEOUT or Cancelled by ``SessionHttp``

    def __enter__(self) -> _Exchange:
        self._remove = self.control.add_closer(lambda: _abort(self.holder))
        self._watchdog.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._watchdog.cancel()
        self._remove()
        for closer in ((self.raw.close if self.raw is not None else None),
                       (self.connection.close if self.connection is not None else None),
                       (self.holder["sock"].close if "sock" in self.holder else None)):
            if closer is None:
                continue
            try:
                closer()
            except OSError:
                pass


class _PinnedConnection(http.client.HTTPSConnection):
    """HTTPS to the checked addresses only (never a new lookup of the host name), every socket kept by the
    exchange as soon as it exists. Each address gets at most what is left of the deadline."""

    def __init__(self, target: Target, connector: Connector, context: ssl.SSLContext, exchange: _Exchange,
                 connect_timeout: float):
        super().__init__(target.host, target.port, timeout=connect_timeout, context=context)
        self._target = target
        self._connector = connector
        self._exchange = exchange

    def connect(self) -> None:
        failure: OSError | None = None
        raw = None
        for address in self._target.addresses:
            left = self._exchange.remaining()
            if self._exchange.ended or left <= 0:
                raise TimeoutError("the exchange ended")
            try:
                raw = self._connector(address, self._target.port, min(self.timeout, left))
                break
            except OSError as error:
                failure = error
        if raw is None:
            raise failure or OSError("no address")
        self._exchange.keep(raw)
        try:  # headers and a POST body go in two writes: no Nagle delay between them (as http.client does)
            raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        # A TLS stream that ends without close_notify is an error, not the end of an answer without a length.
        tls = self._context.wrap_socket(raw, server_hostname=self.host, do_handshake_on_connect=False,
                                        suppress_ragged_eofs=False)
        self._exchange.keep(tls)
        tls.do_handshake()
        self.sock = tls


def _utf8_header(value: str) -> str:
    """http.client reads header bytes as Latin-1. A value sent as raw UTF-8 (a Location path like "tập-1", a
    cookie value) is read back as UTF-8, so the browser gets what the server meant; other bytes stay."""
    try:
        return value.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return value


def _network_error(host: str, error: BaseException) -> HttpError:
    if isinstance(error, ssl.SSLCertVerificationError):
        return HttpError("TLS_ERROR", f"Chứng chỉ HTTPS của {host} không hợp lệ; không gửi yêu cầu.")
    if isinstance(error, ssl.SSLEOFError):
        return HttpError("NETWORK", f"{host} ngắt kết nối TLS giữa chừng.", retryable=True)
    if isinstance(error, ssl.SSLError):
        return HttpError("TLS_ERROR", f"Không bắt tay TLS được với {host}.")
    if isinstance(error, (socket.timeout, TimeoutError)):
        return HttpError("NETWORK", f"{host} không trả lời kịp (quá thời gian chờ).", retryable=True)
    return HttpError("NETWORK", f"Mất kết nối với {host} ({type(error).__name__}).", retryable=True)


def _timeout(host: str, seconds: float) -> HttpError:
    return HttpError("TIMEOUT", f"{host} không trả lời xong trong {seconds:g} giây; dừng yêu cầu này.",
                     retryable=True)


class SessionHttp:
    """See the module docstring. One instance per source; ``hosts`` is that source's whole host list."""

    def __init__(self, hosts: Iterable[str], network: SessionNetwork | None = None, *,
                 request_seconds: float = REQUEST_SECONDS, connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
                 read_timeout: float = READ_TIMEOUT_SECONDS, max_body: int = MAX_RESPONSE_BYTES):
        self.hosts = frozenset(hosts)
        if not self.hosts:
            raise ValueError("A session needs the source's host list")
        for name, value, cap in (("request_seconds", request_seconds, MAX_REQUEST_SECONDS),
                                 ("connect_timeout", connect_timeout, MAX_REQUEST_SECONDS),
                                 ("read_timeout", read_timeout, MAX_REQUEST_SECONDS),
                                 ("max_body", max_body, MAX_RESPONSE_CAP)):
            if not 0 < value <= cap:
                raise ValueError(f"{name} must be above 0 and at most {cap}")
        network = network or SessionNetwork()
        self._current = threading.local()  # the task of the running ``send`` (its lookup polls it for a cancel)
        # Used only for ``check`` (link shape and public addresses); it never sends a request of the session.
        self._policy = SafeHttp(resolver=_bounded_resolver(network.resolver, request_seconds, self._current),
                                is_public=network.is_public, connector=network.connector,
                                ssl_context=network.ssl_context, connect_timeout=connect_timeout,
                                read_timeout=read_timeout)
        self.connector = network.connector
        self.context = self._policy.context
        self.request_seconds = request_seconds
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_body = max_body

    def in_scope(self, url: str) -> str:
        """The host of ``url`` when it may be asked at all (https, default port, a host of the source); no
        DNS lookup. HttpError otherwise."""
        try:
            normalized, host, _port = check_link(url, max_length=MAX_DERIVED_URL_LENGTH)
        except LinkRejected as error:
            raise HttpError(error.code, error.message) from None
        if urlsplit(normalized).scheme != "https":
            raise HttpError("NOT_HTTPS", f"Phiên nguồn chỉ đi qua https; yêu cầu tới {host} bị chặn.")
        if host not in self.hosts:
            raise HttpError("HOST_NOT_ALLOWED", f"{host} không thuộc nguồn này; không gửi yêu cầu.")
        return host

    def check(self, url: str) -> Target:
        """``in_scope``, then the DNS lookup (bounded in time): every address public (``SafeHttp.check``)."""
        self.in_scope(url)
        return self._policy.check(url)

    @staticmethod
    def _headers(headers: Mapping[str, str] | None, host: str) -> dict[str, str]:
        result: dict[str, str] = {"Connection": "close"}
        for name, value in (headers or {}).items():
            key = name.strip().lower()
            if not key or key.startswith(":") or key in DROPPED_REQUEST_HEADERS:
                continue
            text = str(value)
            if any(character in text for character in "\r\n\0"):
                raise HttpError("BAD_REQUEST", f"Yêu cầu tới {host} có header không hợp lệ.")
            try:
                key.encode("ascii")
                text.encode("latin-1")
            except UnicodeEncodeError:
                raise HttpError("BAD_REQUEST", f"Yêu cầu tới {host} có header không mã hóa được.") from None
            result[key] = text
        return result

    @staticmethod
    def _shape(method: str, body: bytes | None) -> tuple[str, bytes | None]:
        method = method.upper()
        if method not in METHODS:
            raise HttpError("METHOD_NOT_ALLOWED", f"Phương thức {method[:10]} không được dùng trong phiên nguồn.")
        if not body:
            return method, None
        if method != "POST":
            raise HttpError("BAD_REQUEST", f"Chỉ POST mới có nội dung gửi đi ({method}).")
        if len(body) > MAX_REQUEST_BODY:
            raise HttpError("REQUEST_TOO_LARGE", "Nội dung gửi đi lớn quá 1 MiB; không gửi.")
        return method, body

    def send(self, url: str, control: Interruptible, *, method: str = "GET",
             headers: Mapping[str, str] | None = None, body: bytes | None = None) -> SessionReply:
        """One exchange with ``url`` (checked; see the module docstring). Any final status comes back, a 3xx
        too. The DNS lookup counts toward the deadline, and a stop or a cancel ends it at once."""
        method, body = self._shape(method, body)
        if control.requested:
            raise Cancelled()
        started = time.monotonic()
        self._current.control = control
        try:
            target = self.check(url)
        except HttpError:
            if control.requested:
                raise Cancelled() from None
            raise
        finally:
            self._current.control = None
        if control.requested:
            raise Cancelled()
        outgoing = self._headers(headers, target.host)
        remaining = self.request_seconds - (time.monotonic() - started)
        if remaining <= 0:
            raise _timeout(target.host, self.request_seconds)
        with _Exchange(control, remaining) as exchange:
            try:
                return self._exchange(exchange, target, method, outgoing, body)
            except HttpError:
                raise
            except Exception as error:  # noqa: BLE001 - mapped here; the sockets close on leaving the with
                if control.requested:
                    raise Cancelled() from None
                if exchange.expired.is_set() or exchange.remaining() <= 0:
                    raise _timeout(target.host, self.request_seconds) from None
                if isinstance(error, (OSError, http.client.HTTPException, ValueError)):
                    raise _network_error(target.host, error) from None
                raise

    def _exchange(self, exchange: _Exchange, target: Target, method: str, outgoing: dict[str, str],
                  body: bytes | None) -> SessionReply:
        connection = _PinnedConnection(target, self.connector, self.context, exchange, self.connect_timeout)
        exchange.connection = connection
        connection.connect()
        connection.sock.settimeout(self.read_timeout)
        connection.request(method, target.path, body=body, headers=outgoing)
        raw = exchange.raw = connection.getresponse()
        if raw.status < 200:  # http.client skips only 100: an interim 103 would be taken as the answer
            raise HttpError("BAD_RESPONSE", f"{target.host} trả về HTTP {raw.status} tạm thời; chưa hỗ trợ.")
        received = tuple((key, _utf8_header(value)) for key, value in raw.getheaders()
                         if key.lower() not in FRAMING_RESPONSE_HEADERS)
        data = b"" if method == "HEAD" else self._body(raw, target.host, exchange)
        return SessionReply(target.url, raw.status, received, data)

    def _body(self, raw: http.client.HTTPResponse, host: str, exchange: _Exchange) -> bytes:
        control = exchange.control
        chunked = "chunked" in (raw.getheader("Transfer-Encoding") or "").lower()
        length = (raw.getheader("Content-Length") or "").strip()
        expected = int(length) if length.isascii() and length.isdigit() and not chunked else None
        if expected is not None and expected > self.max_body:
            raise HttpError("TOO_LARGE_RESPONSE", f"Phản hồi của {host} lớn quá {self.max_body} byte; không đọc.")
        body = bytearray()
        while True:
            if control.requested:
                raise Cancelled()
            data = raw.read1(CHUNK_BYTES)
            if not data:
                # A closed socket may read as the end: never take a body cut by the deadline or a cancel
                # as whole (an answer without a length ends only there).
                if control.requested:
                    raise Cancelled()
                if exchange.expired.is_set():
                    raise _timeout(host, self.request_seconds)
                break
            body += data
            if len(body) > self.max_body:
                raise HttpError("TOO_LARGE_RESPONSE", f"Phản hồi của {host} lớn quá {self.max_body} byte; dừng.")
        if expected is not None and len(body) < expected:
            raise HttpError("NETWORK", f"{host} ngắt kết nối giữa chừng.", retryable=True)
        return self._decoded(body, raw.getheader("Content-Encoding") or "", host)

    def _decoded(self, body: bytearray, encoding: str, host: str) -> bytes:
        encoding = encoding.strip().lower()
        if encoding in ("", "identity"):
            return bytes(body)
        if encoding not in _DECODERS:
            raise HttpError("BAD_RESPONSE", f"{host} nén phản hồi kiểu {encoding[:20]!r}, chưa hỗ trợ.")
        decoder = zlib.decompressobj(_DECODERS[encoding])
        try:
            decoded = decoder.decompress(bytes(body), self.max_body + 1)
        except zlib.error:
            raise HttpError("BAD_RESPONSE", f"Phản hồi nén của {host} bị lỗi.") from None
        if len(decoded) > self.max_body or decoder.unconsumed_tail:
            raise HttpError("TOO_LARGE_RESPONSE", f"Phản hồi của {host} lớn quá {self.max_body} byte khi giải nén.")
        if not decoder.eof:
            raise HttpError("BAD_RESPONSE", f"Phản hồi nén của {host} bị cắt giữa chừng.")
        return decoded
