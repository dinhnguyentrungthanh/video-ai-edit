"""What a session browser's Edge starts with, and the guards outside its route handler (plan 9.9, M2a).

Split out of ``download_account_browser`` (M3) so the browser class stays readable; nothing changed:

- ``HARDENING_ARGS``: no QUIC, no DNS of Edge's own (every name maps to nothing), no background
  networking, pings or DNS prefetch, and the black hole as the only proxy, loopback included.
- ``PROFILE_PREFERENCES``, written into the fresh profile before Edge starts (``write_preferences``):
  WebRTC may not use UDP outside the proxy (Edge does not read the command-line switch), and the password
  manager and the autofill of addresses and cards are off.
- ``edge_environment``: Edge gets only the Windows basics of BiliFlow's environment, TEMP and TMP in the
  profile. ``edge_processes`` finds the processes of one run's own profile (to end a hung run, never a
  personal browser).
- ``BlackHoleProxy``: the browser's only proxy, which closes every connection and forwards nothing.
- ``hop_page`` stands for a redirect of a navigation; ``_is_upload`` spots a file upload the browser refuses.
- ``BrowserUnavailable``, ``BrowserFailed`` (host only, never a path or a query) and ``Refusal``.
"""
from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

import psutil

MAX_REFUSALS = 500
HARDENING_ARGS = (
    "--disable-quic",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",  # not read by Edge: see PROFILE_PREFERENCES
    "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1",
    "--dns-prefetch-disable",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-domain-reliability",
    "--disable-sync",
    "--no-pings",
    "--proxy-bypass-list=<-loopback>",
)
# Written into the fresh profile before Edge starts: WebRTC may not use UDP outside the proxy (so it has no
# UDP at all: the only proxy is the black hole) and only one network route; the password manager (its offer
# to save a password, its sign-in) and the autofill of addresses and cards are switched off (the sign-in
# window is the user's typing). Edge 154 keeps every one of these keys when it closes (M2b test; it drops the
# old "autofill.enabled", so that one is not set); that a visible window shows no such offer is not checked yet.
PROFILE_PREFERENCES = {
    "webrtc": {"ip_handling_policy": "disable_non_proxied_udp", "multiple_routes_enabled": False,
               "nonproxied_udp_enabled": False},
    "credentials_enable_service": False,
    "credentials_enable_autosignin": False,
    "profile": {"password_manager_enabled": False},
    "autofill": {"profile_enabled": False, "credit_card_enabled": False},
}
# What Edge gets of BiliFlow's environment: what Windows programs need, nothing of BiliFlow's own.
EDGE_ENVIRONMENT = frozenset({
    "ALLUSERSPROFILE", "APPDATA", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "COMMONPROGRAMW6432",
    "COMPUTERNAME", "COMSPEC", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "NUMBER_OF_PROCESSORS", "OS", "PATH",
    "PATHEXT", "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER", "PROGRAMDATA", "PROGRAMFILES",
    "PROGRAMFILES(X86)", "PROGRAMW6432", "PUBLIC", "SYSTEMDRIVE", "SYSTEMROOT", "USERDOMAIN", "USERNAME",
    "USERPROFILE", "WINDIR"})
# Removes the WebRTC page API in every frame and popup before any page script runs (defence in depth: the
# UDP policy and the black-hole proxy keep WebRTC off the network).
NO_WEBRTC_SCRIPT = """(() => {
  for (const name of ['RTCPeerConnection', 'webkitRTCPeerConnection', 'RTCDataChannel', 'RTCRtpSender',
                      'RTCRtpReceiver', 'RTCRtpTransceiver', 'RTCIceCandidate', 'RTCSessionDescription',
                      'RTCDtlsTransport', 'RTCIceTransport', 'RTCSctpTransport', 'RTCCertificate']) {
    try { delete window[name]; } catch (e) {}
    try { Object.defineProperty(window, name, {value: undefined, configurable: false, writable: false}); }
    catch (e) {}
  }
})();"""
# A file part of a multipart body. Playwright leaves a file's bytes out of ``post_data_buffer`` (M2a test),
# so such a request would go out cut short; the session browser never uploads files.
_FILE_PART = re.compile(rb'(?im)^content-disposition:[^\r\n]*;\s*filename\*?=(?!"")')  # "": no file chosen


class BrowserUnavailable(Exception):
    """Playwright or Edge cannot be started here (nothing was installed or downloaded to fix it)."""


class BrowserFailed(Exception):
    """A navigation or a browser action failed; ``message`` names the host only, never a path or a query
    (a Playwright error repeats the whole URL, which may carry a ticket)."""

    def __init__(self, code: str, host: str = "", detail: str = ""):
        self.code = code
        self.host = host
        # Where it failed and the error's kind (``failure_kind``): fixed words only, never the error's text.
        self.detail = detail
        shown = f"{code}, {detail}" if detail else code
        self.message = (f"Trang của {host} không mở được ({shown})." if host
                        else f"Trình duyệt của phiên nguồn báo lỗi ({shown}).")
        super().__init__(self.message)


@dataclass(frozen=True)
class Refusal:
    """One request the handler refused: why, to which host and what kind (never the path, query or body)."""
    code: str
    host: str
    kind: str


def _host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "")[:253]
    except ValueError:
        return ""


def _playwright_error() -> type[BaseException]:
    try:
        from playwright.sync_api import Error
    except ImportError:  # no Playwright: nothing can raise its errors
        return BrowserUnavailable
    return Error


def _playwright_timeout() -> type[BaseException]:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
    except ImportError:
        return BrowserUnavailable
    return PlaywrightTimeout


# Words of a browser error's text and the kind each one names, first match wins.
_FAILURE_KINDS = (("indexeddb", "INDEXEDDB"), ("crash", "CRASHED"), ("connection", "CONNECTION"),
                  ("closed", "CLOSED"))


def failure_kind(error: BaseException) -> str:
    """A browser error as one fixed word for the task's message: TIMEOUT, INDEXEDDB, CRASHED, CONNECTION,
    CLOSED or OTHER. Its text names URLs (which may carry a ticket), so it is only searched, never kept."""
    if isinstance(error, _playwright_timeout()):
        return "TIMEOUT"
    text = str(error).lower()
    return next((kind for word, kind in _FAILURE_KINDS if word in text), "OTHER")


# What failed inside Playwright's IndexedDB read or restore, searched in its (or the browser's) message in order.
_INDEXED_DB_CAUSES = (("name is empty", "NAME_EMPTY"), ("version is unset", "VERSION_UNSET"), ("denied", "DENIED"),
                      ("quota", "QUOTA"), ("abort", "ABORTED"), ("already exists", "KEY_EXISTS"),
                      ("version", "VERSION"), ("clone", "CLONE"), ("key", "KEY"), ("internal", "INTERNAL"),
                      ("not iterable", "SHAPE"), ("cannot read prop", "SHAPE"))


def indexed_db_cause(error: BaseException) -> str:
    """An INDEXEDDB failure (``failure_kind``) as one fixed word for the run's log line, e.g. DENIED or
    VERSION_UNSET; OTHER when none fits. Only the browser's own words after "IndexedDB:" on that line are searched
    (Playwright's call log after it names hosts and URLs); the text is never kept."""
    text = str(error).lower()
    text = text.split("indexeddb:", 1)[1].split("\n", 1)[0] if "indexeddb:" in text else ""
    return next((cause for word, cause in _INDEXED_DB_CAUSES if word in text), "OTHER")


def hop_page(target: str) -> bytes:
    """The page that stands for a redirect: it replaces itself with ``target`` (already checked) and sends
    no Referer. When ``target`` is this page's own URL with a fragment, ``location.replace`` would only
    scroll: the page then takes that URL and loads it again (a GET), as a browser does for the redirect. The
    browser compares its own parsed URLs for that. ``target`` is a JSON string literal, with "<" escaped so it
    cannot close the script."""
    literal = json.dumps(target).replace("<", "\\u003c")
    script = ("(() => { const t = new URL(" + literal + ", location.href).href;"
              " if (t.includes('#') && t.split('#')[0] === location.href.split('#')[0]) {"
              " history.replaceState(null, '', t); location.reload(); } else { location.replace(t); } })()")
    return ('<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer"><title></title>'
            f"<script>{script}</script>").encode("utf-8")


def _is_upload(headers: Mapping[str, str], body: bytes | None) -> bool:
    """A multipart request with a file part, or one whose body Playwright gave as nothing at all."""
    kind = next((value for name, value in headers.items() if name.lower() == "content-type"), "")
    if not kind.strip().lower().startswith("multipart/"):
        return False
    return body is None or _FILE_PART.search(body) is not None


def _driver_debug_on() -> bool:
    """Playwright's driver writes its protocol (URLs, cookies and bodies included) to stderr when DEBUG names
    it, and PWDEBUG opens its inspector window: the session browser never starts then."""
    debug = os.environ.get("DEBUG", "").lower()
    return bool(os.environ.get("PWDEBUG")) or "pw" in debug or "*" in debug


def write_preferences(profile: Path) -> None:
    """Edge's preferences of a fresh profile, before its first start: PROFILE_PREFERENCES (WebRTC may not send
    UDP outside the proxy; the command-line switch for it is not read by Edge, M2a tests)."""
    folder = profile / "Default"
    folder.mkdir()
    (folder / "Preferences").write_text(json.dumps(PROFILE_PREFERENCES), encoding="utf-8")


def edge_processes(profile: Path) -> list[int]:
    """The msedge.exe processes started with ``profile`` (a run's own, unique folder) as their user data
    folder. Read only; a process that cannot be read is skipped."""
    wanted = os.path.normcase("--user-data-dir=" + str(profile))
    found: list[int] = []
    for process in psutil.process_iter(["name", "cmdline"]):
        name, args = process.info.get("name") or "", process.info.get("cmdline") or ()
        if name.lower() == "msedge.exe" and any(os.path.normcase(arg) == wanted for arg in args):
            found.append(process.pid)
    return found


def edge_environment(profile: str) -> dict[str, str]:
    """The environment Edge starts with: the Windows basics of EDGE_ENVIRONMENT, TEMP and TMP in the profile."""
    env = {key: value for key, value in os.environ.items() if key.upper() in EDGE_ENVIRONMENT}
    env.update(TEMP=profile, TMP=profile)
    return env


class BlackHoleProxy:
    """The browser's only proxy: 127.0.0.1 on a free port. Every connection is closed after reading at most
    its first line; nothing is forwarded. ``hosts`` keeps the target host of each attempt (CONNECT host or
    the host of an absolute URL, never a path), for the tests."""

    def __init__(self) -> None:
        self._socket = socket.socket()
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(64)
        self.port = self._socket.getsockname()[1]
        self.hosts: list[str] = []
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self._thread = threading.Thread(target=self._run, name="session-browser-black-hole", daemon=True)
        self._thread.start()

    @property
    def server(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _run(self) -> None:
        while not self._closing.is_set():
            try:
                connection, _ = self._socket.accept()
            except OSError:
                if self._closing.is_set():
                    return
                time.sleep(0.05)  # a passing accept error; the next connection is still closed
                continue
            try:
                connection.settimeout(1.0)
                line = connection.recv(4096).split(b"\r\n", 1)[0].decode("latin-1", "replace")
            except OSError:
                line = ""
            finally:
                connection.close()
            parts = line.split(" ")
            target = parts[1] if len(parts) > 2 else ""
            host = target.rsplit(":", 1)[0] if parts[0] == "CONNECT" else _host_of(target)
            with self._lock:
                if len(self.hosts) < MAX_REFUSALS:
                    self.hosts.append(host[:253])

    def close(self) -> None:
        self._closing.set()
        try:
            self._socket.close()
        except OSError:
            pass
        self._thread.join(timeout=2)


def _never_connect(socket_route: Any) -> None:
    """A WebSocket of the page stays a mock: no ``connect_to_server``, so nothing reaches a server."""
