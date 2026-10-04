"""Open the Control Center to a phone or laptop on the home Wi-Fi (Dashboard V2 batch 2, plan §12.3).

Modelled on Golden Label's --phone mode (scripts/golden_label_server.py, golden_label_app.py):
an extra listener, off by default, bound only to the PC's private IPv4 address (10/8,
172.16/12, 192.168/16) on its own port, never 0.0.0.0 or a public address. Every request
needs a cookie proving the random 8-character access code (HttpOnly, SameSite=Strict,
constant-time check); after MAX_FAILED_ATTEMPTS wrong codes, code entry stays locked until
the next time the mode is turned on. The code is always typed into the code page, never put
in a link (question 12), so it stays out of browser history. The Host header must be exactly <ip>:<port> (DNS
rebinding). Writes still need the session token, and PC_ONLY_POSTS are refused there.

Imported only by control_center.py (outside the stage-cache fingerprint).
"""
from __future__ import annotations

import hashlib
import hmac
import http.cookies
import ipaddress
import secrets
import socket
import threading
from http.server import ThreadingHTTPServer
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
PRIVATE_NETWORKS = tuple(ipaddress.IPv4Network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))

NOT_PRIVATE_MESSAGE = (
    "Chế độ điện thoại chỉ nghe địa chỉ IPv4 riêng trong Wi-Fi nhà (10.x, 172.16–31.x, 192.168.x), "
    "không dùng “{address}”."
)
NO_WIFI_MESSAGE = "Không tìm thấy Wi-Fi nhà (địa chỉ {address}); chế độ điện thoại chỉ dùng trong mạng nội bộ."
PORT_MESSAGE = "Cổng chế độ điện thoại phải từ 1024 đến 65535 và khác cổng 8765 của Control Center."
PORT_BUSY_MESSAGE = "Không mở được {address}:{port}: {error}. Cổng có thể đang bận."
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

PC_ONLY_SOURCE = "Chỉ làm trên PC: dọn, lưu trữ, khôi phục video gốc và kiểm tra lại Thùng rác không làm qua điện thoại."
PC_ONLY_POSTS = {
    "/api/source-cleanup": PC_ONLY_SOURCE,
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


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


class _PhoneServer(ThreadingHTTPServer):
    # Without SO_REUSEADDR a second listener on Windows fails to bind instead of sharing the port.
    allow_reuse_address = False
    daemon_threads = True


class PhoneAccess:
    """State of the phone listener; every method is thread-safe."""

    def __init__(self, *, lan: Callable[[], str] = lan_address):
        self._lock = threading.Lock()
        self._lan = lan
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.address: str | None = None
        self.port: int | None = None
        self._code: str | None = None
        self._secret: bytes | None = None
        self.failed_attempts = 0
        self.locked = False
        self.unlock_failures = 0
        self.unlock_locked = False
        self.unlocks = 0

    # ------------------------------------------------------------------ lifecycle
    @property
    def enabled(self) -> bool:
        return self._server is not None

    def enable(self, handler_factory: Callable[["PhoneAccess"], type], *, port: int = DEFAULT_PORT,
               address: str | None = None,
               check_address: Callable[[str], None] = require_private_ipv4,
               check_port: Callable[[Any], None] | None = None) -> dict[str, Any]:
        """Open the listener with a new code; already on: unchanged (same code)."""
        with self._lock:
            if self._server is not None:
                return self._status(include_secret=True)
            (check_port or require_phone_port)(port)
            host = address if address is not None else self._lan()
            check_address(host)
            code, secret = new_code(), secrets.token_bytes(32)
            self.address, self._code, self._secret = host, code, secret
            self.failed_attempts, self.locked = 0, False
            self.unlock_failures, self.unlock_locked, self.unlocks = 0, False, 0
            try:
                server = _PhoneServer((host, port), handler_factory(self))
            except OSError as error:
                self.address = self._code = self._secret = None
                raise ValueError(PORT_BUSY_MESSAGE.format(address=host, port=port, error=error)) from error
            self.port = int(server.server_address[1])
            self._server = server
            self._thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5},
                                            name="biliflow-phone", daemon=True)
            self._thread.start()
            return self._status(include_secret=True)

    def disable(self) -> dict[str, Any]:
        """Close the listener; the code and every cookie stop working at once."""
        with self._lock:
            server, self._server = self._server, None
            self._code = self._secret = None
            self.address = self.port = None
            self.failed_attempts, self.locked = 0, False
            self.unlock_failures, self.unlock_locked, self.unlocks = 0, False, 0
        if server is not None:
            server.shutdown()
            server.server_close()
        return self.status(include_secret=True)

    # ------------------------------------------------------------------ status
    def _status(self, *, include_secret: bool) -> dict[str, Any]:
        on = self._server is not None
        value: dict[str, Any] = {
            "enabled": on,
            "address": self.address if on else None,
            "port": self.port if on else None,
            "url": f"http://{self.address}:{self.port}/" if on else None,
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
            # No link with the code in it (question 12): the code is always typed on the phone.
            value["code"] = self._code if on else None
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

    def set_cookie_header(self) -> str:
        with self._lock:
            value = self._cookie_value()
        if value is None:
            raise ValueError("Chế độ điện thoại đang tắt")
        # No Max-Age: a session cookie. No Secure: the listener is plain HTTP on the home Wi-Fi.
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

    def try_code(self, given: Any) -> str:
        """'ok', 'wrong', 'locked', 'unlocked', 'wrong_unlock' or 'unlock_locked'.

        Each wrong code counts; the 10th wrong one locks entry. While locked, only UNLOCK_KEY
        is checked: it lifts the lock ('unlocked') but never opens anything by itself.
        """
        text = str(given or "").strip().lower()[:64]
        with self._lock:
            if self._code is None:
                return "wrong"
            if self.locked:
                if self.unlock_locked:
                    return "unlock_locked"
                if hmac.compare_digest(text.encode("utf-8"), UNLOCK_KEY.encode("utf-8")):
                    if self.unlocks >= MAX_UNLOCKS:
                        self.unlock_locked = True
                        return "unlock_locked"
                    self.unlocks += 1
                    self.locked, self.failed_attempts = False, 0
                    return "unlocked"
                self.unlock_failures += 1
                if self.unlock_failures >= MAX_UNLOCK_ATTEMPTS:
                    self.unlock_locked = True
                    return "unlock_locked"
                return "wrong_unlock"
            if hmac.compare_digest(text.encode("utf-8"), self._code.encode("utf-8")):
                return "ok"
            self.failed_attempts += 1
            if self.failed_attempts >= MAX_FAILED_ATTEMPTS:
                self.locked = True
                return "locked"
            return "wrong"

    def unlock_attempts_left(self) -> int:
        with self._lock:
            return max(0, MAX_UNLOCK_ATTEMPTS - self.unlock_failures)

    def attempts_left(self) -> int:
        with self._lock:
            return max(0, MAX_FAILED_ATTEMPTS - self.failed_attempts)


def pc_only_reason(path: str) -> str | None:
    return PC_ONLY_POSTS.get(path)
