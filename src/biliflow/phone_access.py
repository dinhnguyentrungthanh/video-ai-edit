"""Open the Control Center to a phone or laptop on the home Wi-Fi (Dashboard V2 batch 2, plan §12.3).

Modelled on Golden Label's --phone mode (scripts/golden_label_server.py, golden_label_app.py):
an extra listener, off by default, bound only to the PC's private IPv4 address (10/8,
172.16/12, 192.168/16) on its own port, never 0.0.0.0 or a public address. Every request
needs a cookie proving the random 8-character access code (HttpOnly, SameSite=Strict,
constant-time check); after MAX_FAILED_ATTEMPTS wrong codes, code entry stays locked until
the next time the mode is turned on. The code is always typed into the code page, never put
in a link (question 12), so it stays out of browser history. The Host header must be exactly <ip>:<port> (DNS
rebinding). Writes still need the session token, and only PHONE_ALLOWED_POSTS are accepted there.
Batch 3 (plan §13): bounded connections, short timeout before the cookie, one-line error log,
auto-off after 8 hours or on a Wi-Fi address change, and events without the code or cookie.

Tailscale (the user's choice, 2026-10-06): to reach BiliFlow from outside the home Wi-Fi, the same
listener can instead bind the PC's Tailscale address (100.64/10), read from `tailscale ip -4` of the
installed client. Only devices in the user's tailnet reach it; everything above stays the same, and
the mode turns itself off when Tailscale on the PC stops or its address changes.

Imported only by control_center.py (outside the stage-cache fingerprint).
"""
from __future__ import annotations

import hashlib
import hmac
import http.cookies
import ipaddress
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
CODE_LENGTH = 8
DEFAULT_PORT = 8767  # 8765 Control Center, 8766 Golden Label
MAX_FAILED_ATTEMPTS = 10
# User decision (plan §8, question 9): once code entry is locked, this special key only lifts the
# lock and resets the count; the 8-character code of this enable is still required afterwards.
# Wrong keys are counted too: MAX_UNLOCK_ATTEMPTS of them lock the key until the next enable.
UNLOCK_KEY = "2007"
MAX_UNLOCK_ATTEMPTS = 5
# The key is in the source, so it may lift the lock only a few times per enable; otherwise it
# would give unlimited code guesses (10 per unlock).
MAX_UNLOCKS = 3
COOKIE_NAME = "biliflow_phone"
# Batch 3 (plan §13): the extra door closes by itself and cannot be held open by strangers.
AUTO_OFF_SECONDS = 8 * 3600        # H3: turned off 8 hours after it was turned on
# The user's choice (2026-10-07): "Gia hạn" adds one lifetime to the time left, but never past
# this many lifetimes from now (24 hours for the 8-hour default).
EXTEND_MAX_LIFETIMES = 3
ADDRESS_CHECK_SECONDS = 60          # H3: turned off when the PC's Wi-Fi address changes
MAX_CONNECTIONS = 32                # H1: further connections are closed at once
# L1/M1: one device cannot take every connection. Only connections that have not shown the cookie
# count against this (a browser opens up to 6 at once; streams and loaded pages hold theirs), so a
# little headroom over 6. Devices behind one NAT share it.
MAX_CONNECTIONS_PER_IP = 8
STREAM_WRITE_TIMEOUT_SECONDS = 60.0  # M1: a phone video stream whose reader stops is closed; it resumes with Range
LIMITED_EVENT_INTERVAL_SECONDS = 60.0  # L1: at most one PHONE_CONNECTIONS_LIMITED event per interval
GATE_TIMEOUT_SECONDS = 5.0          # H1/L1: a connection without the cookie is closed this long after it was accepted
ERROR_LOG_INTERVAL_SECONDS = 10.0   # H1: at most one error line per interval, with a skipped count
RECENT_EVENTS = 20                  # H4: kept in memory for the PC panel (also written to the store)
DISABLE_REASONS = {
    "user": "người dùng tắt",
    "expired": "đến giờ tự tắt",
    "address_changed": "địa chỉ Wi-Fi của PC đổi",
    "tailscale_changed": "Tailscale trên PC tắt hoặc đổi địa chỉ",
    "stopped": "Control Center dừng",
}
PRIVATE_NETWORKS = tuple(ipaddress.IPv4Network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
# Where the listener may be opened: the home Wi-Fi (default) or the user's tailnet.
NETWORKS = {"wifi": "Wi-Fi nhà", "tailscale": "Tailscale"}
DEFAULT_NETWORK = "wifi"
TAILSCALE_NETWORK = ipaddress.IPv4Network("100.64.0.0/10")  # every Tailscale IPv4 address is in here
TAILSCALE_TIMEOUT_SECONDS = 10.0
TAILSCALE_MISSES_BEFORE_OFF = 2  # wrong address checks in a row before the watchdog turns the mode off
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

NOT_PRIVATE_MESSAGE = (
    "Chế độ điện thoại chỉ nghe địa chỉ IPv4 riêng trong Wi-Fi nhà (10.x, 172.16–31.x, 192.168.x), "
    "không dùng “{address}”."
)
NO_WIFI_MESSAGE = "Không tìm thấy Wi-Fi nhà (địa chỉ {address}); chế độ điện thoại chỉ dùng trong mạng nội bộ."
NETWORK_MESSAGE = "Mạng của chế độ điện thoại phải là “wifi” (Wi-Fi nhà) hoặc “tailscale”."
NOT_TAILSCALE_MESSAGE = (
    "Chế độ điện thoại qua Tailscale chỉ nghe địa chỉ Tailscale của PC (100.64.x–100.127.x), "
    "không dùng “{address}”."
)
NO_TAILSCALE_MESSAGE = (
    "Không thấy Tailscale trên PC. Vào Cài đặt → Tailscale, bấm “Cài và cấu hình Tailscale”, "
    "đăng nhập rồi bật lại."
)
TAILSCALE_DOWN_MESSAGE = (
    "Tailscale trên PC chưa chạy hoặc chưa đăng nhập ({detail}). Mở Tailscale, đăng nhập rồi bật lại."
)
PORT_MESSAGE = "Cổng chế độ điện thoại phải từ 1024 đến 65535 và khác cổng 8765 của Control Center."
PORT_BUSY_MESSAGE = "Không mở được {address}:{port}: {error}. Cổng có thể đang bận."
PORT_BUSY_TAILSCALE_MESSAGE = (
    "Không mở được {address}:{port}: {error}. Cổng có thể đang bận, hoặc Tailscale trên PC chưa sẵn sàng."
)
ENABLE_CANCELLED_MESSAGE = "Chế độ điện thoại vừa được tắt trong lúc đang bật; bấm bật lại nếu cần."
LOCKED_MESSAGE = (
    "Đã nhập sai mã quá nhiều lần nên nhập mã đang bị khóa. Nhập khóa mở đặc biệt để gỡ khóa, "
    "hoặc tắt rồi bật lại chế độ điện thoại trên PC để có mã mới."
)
UNLOCK_LOCKED_MESSAGE = (
    "Khóa mở đã bị khóa (nhập sai quá nhiều lần hoặc đã dùng hết lượt mở). Nhập mã bị khóa tới lần bật "
    "chế độ điện thoại sau "
    "(tắt rồi bật lại trên PC để có mã mới)."
)
UNLOCKED_MESSAGE = "Đã gỡ khóa. Nhập mã truy cập 8 ký tự hiện trên PC."
WRONG_UNLOCK_MESSAGE = "Khóa mở không đúng. Nhập mã vẫn đang bị khóa."

PC_ONLY_SOURCE = (
    "Chỉ làm trên PC: lưu trữ, khôi phục bản xuất và kiểm tra lại Thùng rác không làm qua điện thoại."
)
PC_ONLY_POSTS = {
    "/api/source-archive": PC_ONLY_SOURCE,
    "/api/source-archive/restore": PC_ONLY_SOURCE,
    "/api/source-recycle-check": PC_ONLY_SOURCE,
    "/api/shutdown": "Chỉ làm trên PC: không tắt Control Center qua điện thoại.",
    "/api/ai/config": "Chỉ làm trên PC: cấu hình AI Supervisor không làm qua điện thoại.",
    "/api/ai/login": "Chỉ làm trên PC: đăng nhập ChatGPT cho AI Supervisor không làm qua điện thoại.",
    "/api/logo-memory/class": "Chỉ làm trên PC: không sửa bộ nhớ logo qua điện thoại.",
    "/api/logo-memory/delete": "Chỉ làm trên PC: không xóa bộ nhớ logo qua điện thoại.",
    "/api/phone-mode": "Chỉ làm trên PC: bật/tắt chế độ điện thoại chỉ làm trên PC.",
}
# Dashboard V2 → Cài đặt → Tailscale (docs/TAILSCALE_PLAN.md): installing and driving Tailscale is PC only.
PC_ONLY_TAILSCALE = "Chỉ làm trên PC: cài, đăng nhập và điều khiển Tailscale chỉ làm trên PC."
TAILSCALE_ACTIONS = ("install", "start-service", "firewall", "login", "up", "down", "logout", "remote-on")
PC_ONLY_POSTS.update({f"/api/tailscale/{action}": PC_ONLY_TAILSCALE for action in TAILSCALE_ACTIONS})
# The user's choice (2026-10-06): "Gia hạn thêm 8 giờ" also from the phone, only while it is open over Tailscale.
EXTEND_TAILSCALE_ONLY = ("Gia hạn từ điện thoại chỉ có khi chế độ điện thoại mở qua Tailscale; "
                         "ở Wi-Fi nhà, gia hạn trên PC.")
BODY_CUT_MESSAGE = "Request body was cut"
PC_ONLY_DEFAULT = "Chỉ làm trên PC: thao tác này không làm qua điện thoại."
PC_ONLY_VISUAL_AUDIT = "Chỉ làm trên PC: Visual AI Audit gửi ảnh ra ngoài máy."
# H2: the phone listener accepts only these POST routes (full match); everything else is PC-only
# and refused before the body is read. ai-audit is allowed only with visual false (checked on
# the body by the phone handler). tests/test_dashboard_v2_phone.py lists every do_POST route
# and fails when one is in neither this list nor PC_ONLY_POSTS.
PHONE_ALLOWED_POSTS = tuple(re.compile(pattern) for pattern in (
    r"/api/scheduler",
    r"/api/ai/check",
    r"/api/jobs/\d+/(?:start|resume|pause|stop-after-stage|cancel|retry|rerun|skip|unskip|hide|unhide)",
    r"/api/jobs/\d+/ai-audit",
    r"/api/jobs/\d+/review/(?:decision|clear|bulk-keep|bulk-accept|finalize)",
    # "Tải video": the phone pastes links and drives tasks; files land on the PC.
    r"/api/downloads",
    r"/api/downloads/\d+/(?:rename|choose|stop|resume|cancel|retry|remove)",
    r"/api/downloads/settings",
    r"/api/downloads/cleanup-temp",
    # "Xóa video gốc", "Xóa video" and "Dọn video mất gốc" (the user's choice, 2026-10-06): the same
    # preview_id + confirm_permanent request as on the PC; archive, restore and the bin check stay PC only.
    r"/api/source-cleanup",
    r"/api/job-delete",
    # The handler refuses it unless the mode is open over Tailscale (EXTEND_TAILSCALE_ONLY).
    r"/api/phone-mode/extend",
))
AI_AUDIT_ROUTE = re.compile(r"/api/jobs/\d+/ai-audit")


def is_private_ipv4(address: Any) -> bool:
    """True only for a plain IPv4 address inside 10/8, 172.16/12 or 192.168/16."""
    try:
        value = ipaddress.IPv4Address(str(address))
    except (ipaddress.AddressValueError, ValueError):
        return False
    return str(value) == str(address) and any(value in net for net in PRIVATE_NETWORKS)


def require_private_ipv4(address: Any) -> None:
    if not is_private_ipv4(address):
        raise ValueError(NOT_PRIVATE_MESSAGE.format(address=address))


def require_phone_port(port: Any) -> None:
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535 or port == 8765:
        raise ValueError(PORT_MESSAGE)


def lan_address(probe_target: str = "192.168.1.1") -> str:
    """Address of the interface that routes to the home network (no packet is sent)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((probe_target, 80))
        address = probe.getsockname()[0]
    except OSError as error:
        raise ValueError(NO_WIFI_MESSAGE.format(address=error)) from error
    finally:
        probe.close()
    if not is_private_ipv4(address):
        raise ValueError(NO_WIFI_MESSAGE.format(address=address))
    return address


def is_tailscale_ipv4(address: Any) -> bool:
    """True only for a plain IPv4 address inside Tailscale's 100.64/10."""
    try:
        value = ipaddress.IPv4Address(str(address))
    except (ipaddress.AddressValueError, ValueError):
        return False
    return str(value) == str(address) and value in TAILSCALE_NETWORK


def require_tailscale_ipv4(address: Any) -> None:
    if not is_tailscale_ipv4(address):
        raise ValueError(NOT_TAILSCALE_MESSAGE.format(address=address))


def tailscale_cli(program_files: str | None = None) -> str | None:
    """tailscale.exe of the installed client. PATH and the current folder are never searched."""
    base = program_files if program_files is not None else os.environ.get("ProgramFiles", r"C:\Program Files")
    exe = Path(base) / "Tailscale" / "tailscale.exe"
    return str(exe) if exe.is_file() else None


def tailscale_address(*, run: Callable[..., Any] = subprocess.run,
                      cli: Callable[[], str | None] = tailscale_cli) -> str:
    """The PC's Tailscale IPv4 address from `tailscale ip -4`; ValueError with the reason otherwise."""
    exe = cli()
    if exe is None:
        raise ValueError(NO_TAILSCALE_MESSAGE)
    try:
        done = run([exe, "ip", "-4"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                   timeout=TAILSCALE_TIMEOUT_SECONDS, creationflags=_CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(TAILSCALE_DOWN_MESSAGE.format(detail=type(error).__name__)) from error
    lines = [line.strip() for line in (done.stdout or "").splitlines() if line.strip()]
    if done.returncode != 0 or not lines:
        detail = next((line.strip() for line in (done.stderr or "").splitlines() if line.strip()),
                      "không có địa chỉ IPv4")
        raise ValueError(TAILSCALE_DOWN_MESSAGE.format(detail=detail[:120]))
    require_tailscale_ipv4(lines[0])
    return lines[0]


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def _epoch(stamp: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(stamp)).timestamp()
    except (TypeError, ValueError):
        return None


def _span(seconds: float) -> str:
    """'8 giờ', '3 giờ 20 phút', '45 phút' or, for the short test lifetimes, '2 giây'."""
    total = int(round(seconds))
    if total < 60:
        return f"{total} giây"
    hours, minutes = divmod(total // 60, 60)
    return " ".join(([f"{hours} giờ"] if hours else []) + ([f"{minutes} phút"] if minutes else []))


def post_policy(path: str) -> tuple[bool, str | None, bool]:
    """(allowed on the phone, refusal reason, explicitly classified) for a POST path."""
    if any(pattern.fullmatch(path) for pattern in PHONE_ALLOWED_POSTS):
        return True, None, True
    if path in PC_ONLY_POSTS:
        return False, PC_ONLY_POSTS[path], True
    return False, PC_ONLY_DEFAULT, False


def pc_only_reason(path: str) -> str | None:
    """The 403 reason for a POST over the phone listener, or None when it is allowed."""
    allowed, reason, _ = post_policy(path)
    return None if allowed else reason


class _PhoneServer(ThreadingHTTPServer):
    # Without SO_REUSEADDR a second listener on Windows fails to bind instead of sharing the port.
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: type, *, max_connections: int = MAX_CONNECTIONS,
                 max_per_ip: int = MAX_CONNECTIONS_PER_IP,
                 on_limited: Callable[[str, str], None] | None = None):
        self._slots = threading.BoundedSemaphore(max_connections)
        self._max_per_ip = max_per_ip
        self._per_ip: dict[str, int] = {}
        self._counted: dict[Any, str] = {}  # M1: cookieless connections, counted against their IP
        self._open: set[Any] = set()  # L3: accepted sockets, closed when the mode is turned off
        self._conn_lock = threading.Lock()
        self._closed = False
        self.on_limited = on_limited
        self._error_lock = threading.Lock()
        self._error_last = 0.0
        self._error_skipped = 0
        super().__init__(address, handler)

    @staticmethod
    def _ip(client_address: Any) -> str:
        return str(client_address[0]) if client_address else "?"

    def process_request(self, request: Any, client_address: Any) -> None:
        # H1/L1: at most `max_connections` handler threads, and at most `max_per_ip` per device;
        # a connection over either limit is closed at once.
        ip = self._ip(client_address)
        with self._conn_lock:
            refused = self._closed or self._per_ip.get(ip, 0) >= self._max_per_ip
            if not refused and not self._slots.acquire(blocking=False):
                refused = "total"
            if not refused:
                self._per_ip[ip] = self._per_ip.get(ip, 0) + 1
                self._counted[request] = ip
                self._open.add(request)
        if refused:
            self.shutdown_request(request)
            if self.on_limited is not None and not self._closed:
                self.on_limited(ip, "total" if refused == "total" else "per_ip")
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._release(request, ip)
            raise

    def _uncount(self, request: Any) -> None:
        """Drop `request` from its IP's cookieless count (once). Caller holds _conn_lock."""
        ip = self._counted.pop(request, None)
        if ip is None:
            return
        left = self._per_ip.get(ip, 1) - 1
        if left > 0:
            self._per_ip[ip] = left
        else:
            self._per_ip.pop(ip, None)

    def promote(self, request: Any) -> None:
        """M1: the connection showed a valid cookie; it no longer counts against its IP (only the 32)."""
        with self._conn_lock:
            self._uncount(request)

    def _release(self, request: Any, ip: str) -> None:
        with self._conn_lock:
            self._open.discard(request)
            self._uncount(request)
        self._slots.release()

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._release(request, self._ip(client_address))

    def close_connections(self) -> int:
        """L3: shut down every accepted connection (a video being streamed included)."""
        with self._conn_lock:
            self._closed = True
            sockets = list(self._open)
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        return len(sockets)

    def open_connections(self) -> int:
        with self._conn_lock:
            return len(self._open)

    def connections_of(self, ip: str) -> int:
        with self._conn_lock:
            return self._per_ip.get(ip, 0)

    def handle_error(self, request: Any, client_address: Any) -> None:
        # H1: one short line, at most once per interval, never a traceback with request data.
        now = time.monotonic()
        with self._error_lock:
            if now - self._error_last < ERROR_LOG_INTERVAL_SECONDS:
                self._error_skipped += 1
                return
            skipped, self._error_skipped, self._error_last = self._error_skipped, 0, now
        kind = type(sys.exc_info()[1]).__name__
        print(f"BiliFlow phone listener: request error {kind} from {client_address[0] if client_address else '?'}"
              + (f" ({skipped} more skipped)" if skipped else ""), file=sys.stderr, flush=True)


class PhoneAccess:
    """State of the phone listener; every method is thread-safe."""

    def __init__(self, *, lan: Callable[[], str] = lan_address,
                 tailscale: Callable[[], str] = tailscale_address,
                 on_event: Callable[[str, str, dict[str, Any]], None] | None = None):
        self._lock = threading.Lock()
        self._lan = lan
        self._tailscale = tailscale
        self.on_event = on_event  # (event_type, message, payload) -> stored as a Control Center event
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._watch_stop: threading.Event | None = None
        self.address: str | None = None
        self.port: int | None = None
        self.network: str | None = None  # "wifi" or "tailscale" while on
        self._generation = 0  # bumped by every disable() request (not the watchdog's own close)
        self._code: str | None = None
        self._secret: bytes | None = None
        self.enabled_at: float | None = None
        self.expires_at: float | None = None
        self.last_disabled_reason: str | None = None
        self.last_disabled_at: float | None = None
        self.events: deque[dict[str, Any]] = deque(maxlen=RECENT_EVENTS)
        self._deadline: float | None = None   # monotonic deadline read by the watchdog (extendable)
        self._login_ips: set[str] = set()      # L2: one PHONE_LOGIN event per device per enable
        self._limited_last = 0.0               # L1: last PHONE_CONNECTIONS_LIMITED event
        self._lifetime = AUTO_OFF_SECONDS
        self._reset_counters()

    def _limited(self, ip: str, kind: str) -> None:
        """L1: a refused connection; at most one event per LIMITED_EVENT_INTERVAL_SECONDS."""
        now = time.monotonic()
        with self._lock:
            if self._limited_last and now - self._limited_last < LIMITED_EVENT_INTERVAL_SECONDS:
                return
            self._limited_last = now
        what = "quá số kết nối cho một thiết bị" if kind == "per_ip" else "hết chỗ kết nối"
        self._event("PHONE_CONNECTIONS_LIMITED", f"Từ chối kết nối của thiết bị {ip} ({what})",
                    ip=ip, limit=kind)

    def _reset_counters(self) -> None:
        self.failed_attempts = 0
        self.locked = False
        self.unlock_failures = 0
        self.unlock_locked = False
        self.unlocks = 0
        self._login_ips = set()

    # ------------------------------------------------------------------ events (H4)
    def _event(self, event_type: str, message: str, **payload: Any) -> None:
        """Record one event. Payloads hold IPs, counts and reasons only: never the code or a cookie."""
        entry = {"type": event_type, "message": message, "at": time.time(), **payload}
        with self._lock:
            self.events.append(entry)
        callback = self.on_event
        if callback is not None:
            try:
                callback(event_type, message, dict(payload))
            except Exception:  # noqa: BLE001 - an event store problem must not break the listener
                pass

    # ------------------------------------------------------------------ lifecycle
    @property
    def enabled(self) -> bool:
        return self._server is not None

    def _lookup(self, network: str) -> str:
        """The PC's current address on `network` (read at call time: tests swap the lookups)."""
        return self._tailscale() if network == "tailscale" else self._lan()

    def enable(self, handler_factory: Callable[["PhoneAccess"], type], *, port: int = DEFAULT_PORT,
               network: Any = DEFAULT_NETWORK,
               address: str | None = None,
               check_address: Callable[[str], None] | None = None,
               check_port: Callable[[Any], None] | None = None,
               lifetime_seconds: float = AUTO_OFF_SECONDS,
               check_seconds: float = ADDRESS_CHECK_SECONDS,
               max_connections: int = MAX_CONNECTIONS,
               max_per_ip: int = MAX_CONNECTIONS_PER_IP) -> dict[str, Any]:
        """Open the listener on `network` with a new code; already on: unchanged (same code)."""
        if not isinstance(network, str) or network not in NETWORKS:
            raise ValueError(NETWORK_MESSAGE)
        with self._lock:
            if self._server is not None:
                return self._status(include_secret=True)
            generation = self._generation
        (check_port or require_phone_port)(port)
        # Outside the lock: `tailscale ip -4` can take a few seconds, and status polls must not wait.
        host = address if address is not None else self._lookup(network)
        check = check_address or (require_tailscale_ipv4 if network == "tailscale" else require_private_ipv4)
        check(host)
        with self._lock:
            if self._server is not None:  # turned on meanwhile
                return self._status(include_secret=True)
            if self._generation != generation:
                # A "turn off" (or the Control Center stopping) came during the lookup: it wins.
                raise ValueError(ENABLE_CANCELLED_MESSAGE)
            code, secret = new_code(), secrets.token_bytes(32)
            self.address, self._code, self._secret = host, code, secret
            self._reset_counters()
            try:
                server = _PhoneServer((host, port), handler_factory(self), max_connections=max_connections,
                                      max_per_ip=max_per_ip, on_limited=self._limited)
            except OSError as error:
                self.address = self._code = self._secret = None
                busy = PORT_BUSY_TAILSCALE_MESSAGE if network == "tailscale" else PORT_BUSY_MESSAGE
                raise ValueError(busy.format(address=host, port=port, error=error)) from error
            self.port = int(server.server_address[1])
            self.network = network
            self._server = server
            self.enabled_at = time.time()
            self.expires_at = self.enabled_at + lifetime_seconds
            self._lifetime = lifetime_seconds
            self._deadline = time.monotonic() + lifetime_seconds
            self._thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5},
                                            name="biliflow-phone", daemon=True)
            self._thread.start()
            stop = self._watch_stop = threading.Event()
            threading.Thread(target=self._watch, args=(server, stop, check_seconds, host, network),
                             name="biliflow-phone-watch", daemon=True).start()
            status = self._status(include_secret=True)
        where = " qua Tailscale" if network == "tailscale" else ""
        self._event("PHONE_MODE_ENABLED", f"Bật chế độ điện thoại{where} tại {host}:{self.port}",
                    address=host, port=self.port, network=network, expires_in_seconds=int(lifetime_seconds))
        return status

    def _watch(self, server: Any, stop: threading.Event, every: float, address: str,
               network: str = DEFAULT_NETWORK) -> None:
        """H3: turn the mode off at the deadline (extendable), or when the PC's address on its
        network changes (Wi-Fi changed, or Tailscale stopped).

        Tailscale is read through its CLI, which can fail once (a busy PC, an update replacing the
        exe): it turns the mode off only after TAILSCALE_MISSES_BEFORE_OFF wrong checks in a row,
        because the user, away from home, could not turn it back on.
        """
        misses = 0
        while not stop.is_set():
            with self._lock:
                deadline = self._deadline if self._server is server else None
            if deadline is None:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.disable("expired", only=server)
                return
            if stop.wait(min(every, remaining)):
                return
            with self._lock:
                deadline = self._deadline if self._server is server else None
            if deadline is None or time.monotonic() >= deadline:
                continue
            try:
                current = self._lookup(network)
            except Exception:  # noqa: BLE001 - no route to the home network / Tailscale down counts as a change
                current = None
            if current == address:
                misses = 0
                continue
            misses += 1
            if network == "tailscale" and misses < TAILSCALE_MISSES_BEFORE_OFF:
                continue
            reason = "tailscale_changed" if network == "tailscale" else "address_changed"
            self.disable(reason, only=server, current_address=current)
            return

    def disable(self, reason: str = "user", *, only: Any = None, current_address: str | None = None) -> dict[str, Any]:
        """Close the listener; the code and every cookie stop working at once.

        `only`: the watchdog closes only the server it watches (never a newer one).
        """
        with self._lock:
            if only is None:
                self._generation += 1  # cancels an enable() still looking up its address
            server = self._server
            if server is None or (only is not None and server is not only):
                return self._status(include_secret=True)
            self._server = None
            address, port, network = self.address, self.port, self.network
            if self._watch_stop is not None:
                self._watch_stop.set()
            self._watch_stop = None
            self._code = self._secret = None
            self.address = self.port = self.network = None
            self.enabled_at = self.expires_at = self._deadline = None
            self.last_disabled_reason = reason if reason in DISABLE_REASONS else "user"
            self.last_disabled_at = time.time()
            self._reset_counters()
        server.shutdown()
        server.server_close()
        server.close_connections()  # L3: open requests and video streams end with the mode
        extra = {"current_address": current_address} if reason in ("address_changed", "tailscale_changed") else {}
        self._event("PHONE_MODE_DISABLED",
                    f"Tắt chế độ điện thoại ({DISABLE_REASONS.get(reason, reason)})",
                    reason=self.last_disabled_reason, address=address, port=port, network=network, **extra)
        return self.status(include_secret=True)

    def extend(self, seconds: float | None = None, *, by: str | None = None) -> dict[str, Any]:
        """Question 15: add `seconds` (default 8 hours) to the time left; same code.

        The user's choice (2026-10-07): presses add up, but the auto-off never moves past
        EXTEND_MAX_LIFETIMES lifetimes (24 hours) from now, and never earlier than it was.
        `by`: the IP of the phone that asked (over Tailscale, the user's choice 2026-10-06); None on the PC.
        """
        with self._lock:
            if self._server is None:
                raise ValueError("Chế độ điện thoại đang tắt; bật lại trên PC.")
            step = self._lifetime if seconds is None else seconds
            limit = EXTEND_MAX_LIFETIMES * self._lifetime
            now = time.monotonic()
            left = max(0.0, (self._deadline or now) - now)
            remaining = max(left, min(left + step, limit))
            self._deadline = now + remaining
            self.expires_at = time.time() + remaining
            expires_at, address, port = self.expires_at, self.address, self.port
        added = remaining - left
        off_at = time.strftime("%H:%M %d/%m", time.localtime(expires_at))
        message = (f"Gia hạn chế độ điện thoại thêm {_span(added)}, tự tắt lúc {off_at}" if added >= 1 else
                   f"Gia hạn chế độ điện thoại: đã ở mức tối đa {_span(limit)}, tự tắt lúc {off_at}")
        who = {"ip": by} if by else {}
        self._event("PHONE_MODE_EXTENDED", message + (f" (từ thiết bị {by})" if by else ""),
                    address=address, port=port, expires_in_seconds=int(remaining),
                    added_seconds=int(round(added)), **who)
        return {**self.status(include_secret=True), "added_seconds": int(round(added))}

    def restore_history(self, stored_events: list[dict[str, Any]]) -> None:
        """Question 14: after a restart, show the latest phone events and the last off reason.

        `stored_events` are JobStore.events() rows (newest first). A last ENABLED event without a
        later DISABLED one means the Control Center stopped while the mode was on.
        """
        phone = [row for row in stored_events if str(row.get("event_type", "")).startswith("PHONE_")]
        entries = []
        for row in reversed(phone[:RECENT_EVENTS]):
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            entries.append({"type": row.get("event_type"), "message": row.get("message"),
                            "at": _epoch(row.get("created_at")), "restored": True,
                            **{key: value for key, value in payload.items() if key not in ("type", "message", "at")}})
        state = next((row for row in phone if row.get("event_type") in ("PHONE_MODE_ENABLED", "PHONE_MODE_DISABLED")),
                     None)
        with self._lock:
            if self._server is not None:
                return
            self.events.extend(entries)
            if state is None:
                return
            if state.get("event_type") == "PHONE_MODE_DISABLED":
                reason = (state.get("payload") or {}).get("reason")
                self.last_disabled_reason = reason if reason in DISABLE_REASONS else "user"
            else:
                self.last_disabled_reason = "stopped"
            self.last_disabled_at = _epoch(state.get("created_at"))

    # ------------------------------------------------------------------ status
    def _status(self, *, include_secret: bool) -> dict[str, Any]:
        on = self._server is not None
        value: dict[str, Any] = {
            "enabled": on,
            "address": self.address if on else None,
            "port": self.port if on else None,
            "url": f"http://{self.address}:{self.port}/" if on else None,
            "network": self.network if on else None,
            "network_text": NETWORKS.get(self.network or "") if on else None,
            "enabled_at": self.enabled_at if on else None,
            "expires_at": self.expires_at if on else None,
            "last_disabled_reason": self.last_disabled_reason,
            "last_disabled_reason_text": DISABLE_REASONS.get(self.last_disabled_reason or ""),
            "last_disabled_at": self.last_disabled_at,
            "locked": self.locked,
            "failed_attempts": self.failed_attempts,
            "max_failed_attempts": MAX_FAILED_ATTEMPTS,
            "unlock_failures": self.unlock_failures,
            "unlock_locked": self.unlock_locked,
            "max_unlock_attempts": MAX_UNLOCK_ATTEMPTS,
            "unlocks": self.unlocks,
            "max_unlocks": MAX_UNLOCKS,
            "default_port": DEFAULT_PORT,
        }
        if include_secret:
            # PC listener only. No link with the code in it (question 12): the code is always typed.
            value["code"] = self._code if on else None
            value["events"] = [dict(entry) for entry in list(self.events)[-10:]][::-1]
        return value

    def status(self, *, include_secret: bool = False) -> dict[str, Any]:
        with self._lock:
            return self._status(include_secret=include_secret)

    # ------------------------------------------------------------------ checks
    @property
    def origin(self) -> str | None:
        return f"http://{self.address}:{self.port}" if self.enabled else None

    def host_ok(self, header: str | None) -> bool:
        """The Host header must be exactly <ip>:<port> of this listener (DNS rebinding)."""
        expected = f"{self.address}:{self.port}" if self.enabled else None
        return expected is not None and (header or "").strip().casefold() == expected

    def _cookie_value(self) -> str | None:
        if self._code is None or self._secret is None:
            return None
        return hmac.new(self._secret, self._code.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def _cookie_header(value: str) -> str:
        # No Max-Age: a session cookie. No Secure: the listener is plain HTTP (on the home Wi-Fi, or
        # inside Tailscale's encrypted tunnel).
        return f"{COOKIE_NAME}={value}; HttpOnly; SameSite=Strict; Path=/"

    def cookie_ok(self, header: str | None) -> bool:
        with self._lock:
            expected = self._cookie_value()
        if expected is None or not header:
            return False
        try:
            morsel = http.cookies.SimpleCookie(header).get(COOKIE_NAME)
        except http.cookies.CookieError:
            return False
        return morsel is not None and hmac.compare_digest(morsel.value.encode("utf-8"), expected.encode("utf-8"))

    def try_code(self, given: Any, *, ip: str | None = None) -> tuple[str, str | None]:
        """(outcome, Set-Cookie header or None), decided under one hold of the lock (H6).

        Outcomes: 'ok', 'wrong', 'locked', 'unlocked', 'wrong_unlock', 'unlock_locked'. Each wrong
        code counts; the 10th wrong one locks entry. While locked, only UNLOCK_KEY is checked: it
        lifts the lock ('unlocked') but never gives the cookie by itself.
        """
        text = str(given or "").strip().lower()[:64]
        cookie: str | None = None
        with self._lock:
            if self._code is None:
                outcome = "wrong"
            elif self.locked:
                if self.unlock_locked:
                    outcome = "unlock_locked"
                elif hmac.compare_digest(text.encode("utf-8"), UNLOCK_KEY.encode("utf-8")):
                    if self.unlocks >= MAX_UNLOCKS:
                        self.unlock_locked = True
                        outcome = "unlock_locked_now"
                    else:
                        self.unlocks += 1
                        self.locked, self.failed_attempts = False, 0
                        outcome = "unlocked"
                else:
                    self.unlock_failures += 1
                    if self.unlock_failures >= MAX_UNLOCK_ATTEMPTS:
                        self.unlock_locked = True
                        outcome = "unlock_locked_now"
                    else:
                        outcome = "wrong_unlock"
            elif hmac.compare_digest(text.encode("utf-8"), self._code.encode("utf-8")):
                value = self._cookie_value()
                cookie = self._cookie_header(value) if value else None
                outcome = "ok" if cookie else "wrong"
            else:
                self.failed_attempts += 1
                outcome = "locked_now" if self.failed_attempts >= MAX_FAILED_ATTEMPTS else "wrong"
                if outcome == "locked_now":
                    self.locked = True
            counts = {"failed_attempts": self.failed_attempts, "unlock_failures": self.unlock_failures,
                      "unlocks": self.unlocks}
        # Events after the lock (they reach the store); never the text that was typed.
        who = {"ip": ip or "?"}
        if outcome == "ok":
            # L2: one event per device per enable, so a holder of the code cannot flood the event log.
            with self._lock:
                first = who["ip"] not in self._login_ips
                self._login_ips.add(who["ip"])
            if first:
                self._event("PHONE_LOGIN", f"Thiết bị {who['ip']} nhập đúng mã", **who)
        elif outcome == "wrong":
            self._event("PHONE_CODE_WRONG", f"Thiết bị {who['ip']} nhập sai mã", **who, **counts)
        elif outcome == "locked_now":
            self._event("PHONE_CODE_LOCKED", f"Khóa nhập mã sau {MAX_FAILED_ATTEMPTS} lần sai (thiết bị {who['ip']})",
                        **who, **counts)
        elif outcome == "unlocked":
            self._event("PHONE_UNLOCKED", f"Thiết bị {who['ip']} gỡ khóa bằng khóa mở", **who, **counts)
        elif outcome == "wrong_unlock":
            self._event("PHONE_UNLOCK_WRONG", f"Thiết bị {who['ip']} nhập sai khóa mở", **who, **counts)
        elif outcome == "unlock_locked_now":
            self._event("PHONE_UNLOCK_LOCKED", f"Khóa mở bị khóa (thiết bị {who['ip']})", **who, **counts)
        public = {"locked_now": "locked", "unlock_locked_now": "unlock_locked"}.get(outcome, outcome)
        return public, cookie

    def unlock_attempts_left(self) -> int:
        with self._lock:
            return max(0, MAX_UNLOCK_ATTEMPTS - self.unlock_failures)

    def attempts_left(self) -> int:
        with self._lock:
            return max(0, MAX_FAILED_ATTEMPTS - self.failed_attempts)
