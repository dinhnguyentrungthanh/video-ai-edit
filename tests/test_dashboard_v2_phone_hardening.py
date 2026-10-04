"""Phone mode hardening (Dashboard V2 batch 3, plan §13): H1–H6.

Every listener here runs on 127.0.0.1 with a temporary root, and every PhoneAccess gets a
fake Wi-Fi lookup (`lan`): no test binds or probes the real Wi-Fi address.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import socket
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from biliflow import control_center, logo_memory_admin, phone_access
from biliflow.control_center import ControlCenter, _handler_class, _phone_access, _phone_handler_class
from biliflow.job_store import JobStore
from biliflow.review_evidence import ReviewFrameCache
from biliflow.scheduler import JobScheduler

ROOT = Path(__file__).resolve().parents[1]
FAKE_LAN = "127.0.0.1"


def raw(port, data: bytes, *, host=None, wait=5.0) -> bytes:
    """Send raw bytes and read until the server closes (or `wait` seconds pass)."""
    with socket.create_connection(("127.0.0.1", port), timeout=wait) as sock:
        sock.sendall(data)
        chunks = []
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            try:
                chunk = sock.recv(65536)
            except (TimeoutError, socket.timeout):
                break
            except ConnectionResetError:
                break
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)


def http(port, method, path, *, host, headers=None, body=b"") -> tuple[int, dict, bytes]:
    lines = [f"{method} {path} HTTP/1.1", f"Host: {host}", "Connection: close"]
    for key, value in (headers or {}).items():
        lines.append(f"{key}: {value}")
    if body or method == "POST":
        lines.append(f"Content-Length: {len(body)}")
    reply = raw(port, ("\r\n".join(lines) + "\r\n\r\n").encode() + body)
    head, _, payload = reply.partition(b"\r\n\r\n")
    status_line, *header_lines = head.decode("iso-8859-1").split("\r\n")
    status = int(status_line.split()[1]) if status_line else 0
    parsed = {}
    for line in header_lines:
        key, _, value = line.partition(":")
        parsed.setdefault(key.strip().lower(), []).append(value.strip())
    return status, parsed, payload


class HardeningBase(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.store = JobStore(self.root / "state" / "control-center.sqlite3")
        source = self.root / "input" / "Tập 1.mp4"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video" * 100)
        stat = source.stat()
        self.store.upsert_job(job_key="job-1", source_path=source, source_sha256=hashlib.sha256(b"1").hexdigest(),
                              source_size_bytes=stat.st_size, source_mtime_ns=stat.st_mtime_ns, state="WAITING_REVIEW")
        center = ControlCenter.__new__(ControlCenter)
        center.root, center.host, center.token, center.store = self.root, "127.0.0.1", "test-token", self.store
        center.frame_cache = ReviewFrameCache(self.root, self.root / "missing-ffmpeg.exe")
        center._stopping = threading.Event()
        center.scheduler = JobScheduler(self.root, self.store)  # never started
        center.recovered = 0
        center.start_ai_audit = mock.Mock(return_value=None)
        self.center = center
        self.pc = ThreadingHTTPServer(("127.0.0.1", 0), _handler_class(center))
        self.pc.daemon_threads = True
        self.pc_port = self.pc.server_address[1]
        threading.Thread(target=self.pc.serve_forever, daemon=True).start()
        self.phone = _phone_access(center)
        self.phone._lan = lambda: FAKE_LAN  # never the real Wi-Fi

    def tearDown(self):
        self.phone.disable()
        self.pc.shutdown()
        self.pc.server_close()
        self.store.close()
        self.temp.cleanup()

    def enable(self, **kw):
        options = {"address": FAKE_LAN, "check_address": lambda _a: None, "check_port": lambda _p: None, "port": 0}
        options.update(kw)
        status = self.phone.enable(lambda access: _phone_handler_class(self.center, access), **options)
        self.port, self.code = status["port"], status["code"]
        self.host = f"127.0.0.1:{self.port}"
        return status

    def cookie(self) -> str:
        status, headers, _ = http(self.port, "POST", "/phone-login", host=self.host,
                                  headers={"Content-Type": "application/x-www-form-urlencoded"},
                                  body=f"code={self.code}".encode())
        self.assertEqual(status, 200)
        return headers["set-cookie"][0].split(";", 1)[0]

    def phone_post(self, path, payload, cookie, *, token="test-token"):
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json", "Cookie": cookie, "Origin": f"http://{self.host}"}
        if token:
            headers["X-BiliFlow-Token"] = token
        return http(self.port, "POST", path, host=self.host, headers=headers, body=body)


class H1RequestHardening(HardeningBase):
    def test_malformed_paths_are_400_without_a_traceback_on_both_listeners(self):
        self.enable()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for port, host in ((self.port, self.host), (self.pc_port, f"127.0.0.1:{self.pc_port}")):
                for method in ("GET", "POST"):
                    with self.subTest(port=port, method=method):
                        status, _, body = http(port, method, "http://[x/", host=host)
                        self.assertEqual(status, 400, body[:200])
                        self.assertIn("Đường dẫn không hợp lệ", body.decode("utf-8"))
        self.assertNotIn("Traceback", err.getvalue())

    def test_handle_error_writes_one_short_line_per_interval(self):
        self.enable()
        server = self.phone._server
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for _ in range(5):
                try:
                    raise ValueError("secret request data")
                except ValueError:
                    server.handle_error(None, ("192.168.1.50", 1234))
            server._error_last = 0.0  # the interval has passed
            try:
                raise ValueError("again")
            except ValueError:
                server.handle_error(None, ("192.168.1.50", 1234))
        lines = err.getvalue().strip().splitlines()
        self.assertEqual(len(lines), 2, lines)
        self.assertIn("ValueError from 192.168.1.50", lines[0])
        self.assertIn("(4 more skipped)", lines[1])
        self.assertNotIn("secret request data", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())

    def test_connections_over_the_limit_are_closed_and_cookieless_ones_time_out(self):
        with mock.patch.object(phone_access, "GATE_TIMEOUT_SECONDS", 1.0):
            self.enable(max_connections=2)
            held = [socket.create_connection(("127.0.0.1", self.port), timeout=5) for _ in range(2)]
            time.sleep(0.2)
            extra = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            started = time.monotonic()
            self.assertEqual(extra.recv(10), b"", "the third connection is closed at once")
            self.assertLess(time.monotonic() - started, 1.0)
            extra.close()
            started = time.monotonic()
            for sock in held:
                data = sock.recv(4096)  # the handler gives up on the silent connection
                self.assertTrue(data == b"" or data.startswith(b"HTTP/1.0 408"), data[:40])
                sock.close()
            self.assertLess(time.monotonic() - started, 3.0, "about GATE_TIMEOUT_SECONDS, not 20 s")
            time.sleep(0.3)
            # The slots are free again.
            self.assertEqual(http(self.port, "GET", "/", host=self.host)[0], 401)

    def test_after_the_cookie_the_usual_timeout_applies(self):
        with mock.patch.object(phone_access, "GATE_TIMEOUT_SECONDS", 1.0):
            self.enable()
            cookie = self.cookie()
            body = json.dumps({"paused": True}).encode()
            head = (f"POST /api/scheduler HTTP/1.1\r\nHost: {self.host}\r\nCookie: {cookie}\r\n"
                    f"X-BiliFlow-Token: test-token\r\nContent-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n\r\n").encode()
            with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
                sock.sendall(head)
                time.sleep(1.6)  # longer than the gate timeout: the body still arrives in time
                sock.sendall(body)
                reply = sock.recv(4096)
            self.assertTrue(reply.startswith(b"HTTP/1.0 200"), reply[:80])
            self.assertTrue(self.store.setting("scheduler_paused"))
        self.assertEqual(control_center._phone_handler_class(self.center, self.phone).timeout,
                         phone_access.GATE_TIMEOUT_SECONDS)


def _do_post_routes() -> list[str]:
    """Every POST route of the Control Center handler, as written in control_center.py."""
    source = Path(control_center.__file__).read_text(encoding="utf-8")
    start = source.index("        def do_POST(self) -> None:")
    end = source.index("    return Handler", start)
    block = source[start:end]
    routes = re.findall(r'path == "([^"]+)"', block)
    routes += re.findall(r're\.fullmatch\(r"([^"]+)", path\)', block)
    routes += [logo_memory_admin.API_CLASS, logo_memory_admin.API_DELETE]
    return routes


def _examples(route: str) -> list[str]:
    """Concrete paths for one route: (\\d+) → 7, (a|b) → each alternative."""
    route = route.replace(r"(\d+)", "7")
    match = re.search(r"\(([^()]+)\)", route)
    if not match:
        return [route.replace("\\", "")]
    return [example for option in match.group(1).split("|")
            for example in _examples(route[:match.start()] + option + route[match.end():])]


class H2AllowList(HardeningBase):
    def test_every_do_post_route_is_classified(self):
        routes = _do_post_routes()
        self.assertGreater(len(routes), 10)
        examples = [example for route in routes for example in _examples(route)]
        self.assertIn("/api/jobs/7/review/finalize", examples)
        self.assertIn("/api/phone-mode", examples)
        unclassified = [path for path in examples if not phone_access.post_policy(path)[2]]
        self.assertEqual(unclassified, [], "add the route to PHONE_ALLOWED_POSTS or PC_ONLY_POSTS")
        allowed = sorted(path for path in examples if phone_access.post_policy(path)[0])
        self.assertIn("/api/scheduler", allowed)
        self.assertIn("/api/jobs/7/ai-audit", allowed)
        for pc_only in ("/api/shutdown", "/api/source-cleanup", "/api/logo-memory/delete", "/api/phone-mode"):
            self.assertNotIn(pc_only, allowed)

    def test_an_unknown_post_is_pc_only_by_default_and_answered_before_the_body(self):
        self.assertEqual(phone_access.post_policy("/api/brand-new-route"), (False, phone_access.PC_ONLY_DEFAULT, False))
        self.assertEqual(phone_access.pc_only_reason("/api/jobs/7/review/new-action"), phone_access.PC_ONLY_DEFAULT)
        self.enable()
        cookie = self.cookie()
        head = (f"POST /api/brand-new-route HTTP/1.1\r\nHost: {self.host}\r\nCookie: {cookie}\r\n"
                "X-BiliFlow-Token: test-token\r\nContent-Type: application/json\r\nContent-Length: 50\r\n\r\n").encode()
        started = time.monotonic()
        reply = raw(self.port, head, wait=8)  # the body never comes
        self.assertTrue(reply.startswith(b"HTTP/1.0 403"), reply[:80])
        self.assertIn('"pc_only"'.encode(), reply)
        self.assertLess(time.monotonic() - started, 3.0, "no wait for the missing body")

    def test_visual_ai_audit_is_pc_only_and_the_json_audit_still_runs_on_the_phone(self):
        self.enable()
        cookie = self.cookie()
        status, _, body = self.phone_post("/api/jobs/1/ai-audit", {"visual": True}, cookie)
        self.assertEqual(status, 403)
        answer = json.loads(body)
        self.assertEqual((answer["code"], answer["error"]), ("pc_only", phone_access.PC_ONLY_VISUAL_AUDIT))
        self.center.start_ai_audit.assert_not_called()
        status, _, _ = self.phone_post("/api/jobs/1/ai-audit", {"visual": True}, cookie, token=None)
        self.assertEqual(status, 403, "no token: refused before the body is used")
        self.center.start_ai_audit.assert_not_called()
        status, _, body = self.phone_post("/api/jobs/1/ai-audit", {"visual": False}, cookie)
        self.assertEqual((status, json.loads(body)), (200, {"status": "QUEUED", "visual_opt_in": False}))
        self.center.start_ai_audit.assert_called_once_with(1, visual_opt_in=False)
        # On the PC the visual audit still runs.
        payload = json.dumps({"visual": True}).encode()
        status, _, body = http(self.pc_port, "POST", "/api/jobs/1/ai-audit", host=f"127.0.0.1:{self.pc_port}",
                               headers={"Content-Type": "application/json", "X-BiliFlow-Token": "test-token"},
                               body=payload)
        self.assertEqual((status, json.loads(body)["visual_opt_in"]), (200, True))
        self.center.start_ai_audit.assert_called_with(1, visual_opt_in=True)

    def test_the_phone_guide_says_reviews_can_change_remembered_logos(self):
        guide = (ROOT / "docs" / "DASHBOARD_V2_PHONE.md").read_text(encoding="utf-8")
        self.assertIn("thêm hoặc bỏ", guide)
        self.assertIn("Visual AI Audit", guide)


class H3Lifecycle(HardeningBase):
    def wait_off(self, seconds=5.0):
        """Until the listener is closed for good (port refused) and its event is stored."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            done = not self.phone.enabled and any(
                e["event_type"] == "PHONE_MODE_DISABLED" for e in self.store.events(None))
            if done:
                try:
                    socket.create_connection(("127.0.0.1", self.port), timeout=0.5).close()
                except OSError:
                    return
            time.sleep(0.05)

    def test_it_turns_itself_off_after_its_lifetime(self):
        status = self.enable(lifetime_seconds=0.6, check_seconds=5)
        self.assertAlmostEqual(status["expires_at"] - status["enabled_at"], 0.6, places=2)
        self.self_check_banner(True)
        self.wait_off()
        self.assertFalse(self.phone.enabled)
        pc = json.loads(http(self.pc_port, "GET", "/api/phone-mode", host=f"127.0.0.1:{self.pc_port}")[2])
        self.assertEqual((pc["enabled"], pc["last_disabled_reason"]), (False, "expired"))
        self.assertEqual(pc["last_disabled_reason_text"], "hết 8 giờ")
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", self.port), timeout=2).close()
        self.self_check_banner(False)

    def test_it_turns_itself_off_when_the_wifi_address_changes(self):
        self.enable(check_seconds=0.2)
        self.phone._lan = lambda: "127.0.0.2"
        self.wait_off()
        self.assertEqual(self.phone.status()["last_disabled_reason"], "address_changed")
        events = [e for e in self.store.events(None) if e["event_type"] == "PHONE_MODE_DISABLED"]
        self.assertEqual(events[0]["payload"]["current_address"], "127.0.0.2")

    def test_a_missing_route_counts_as_an_address_change(self):
        self.enable(check_seconds=0.2)

        def gone():
            raise ValueError("no Wi-Fi")
        self.phone._lan = gone
        self.wait_off()
        self.assertEqual(self.phone.status()["last_disabled_reason"], "address_changed")

    def test_user_and_stop_reasons(self):
        self.enable()
        body = json.dumps({"enabled": False}).encode()
        status, _, reply = http(self.pc_port, "POST", "/api/phone-mode", host=f"127.0.0.1:{self.pc_port}",
                                headers={"Content-Type": "application/json", "X-BiliFlow-Token": "test-token"},
                                body=body)
        self.assertEqual((status, json.loads(reply)["last_disabled_reason"]), (200, "user"))
        self.enable()
        self.center.stop_ai_audits = self.center.stop_ai_login = lambda: None
        self.center.server = None
        self.center.watcher = mock.Mock()
        self.center.scheduler = mock.Mock()
        self.center._stopped = threading.Event()
        self.center.lock = mock.Mock()
        with contextlib.suppress(Exception):
            self.center.stop()
        self.assertEqual(self.phone.status()["last_disabled_reason"], "stopped")

    def test_an_old_watchdog_never_closes_a_newer_listener(self):
        self.enable(lifetime_seconds=0.4, check_seconds=5)
        first = self.phone._server
        self.phone.disable()
        second = self.enable(lifetime_seconds=60, check_seconds=60)
        time.sleep(0.7)
        self.assertTrue(self.phone.enabled)
        self.assertIsNot(self.phone._server, first)
        self.assertEqual(self.phone.status(include_secret=True)["code"], second["code"])

    def self_check_banner(self, on: bool):
        _, _, page = http(self.pc_port, "GET", "/", host=f"127.0.0.1:{self.pc_port}")
        self.assertEqual(b'id="phone-mode-notice"' in page, on)
        if on:
            self.assertIn(f"Đang mở cho điện thoại: http://127.0.0.1:{self.port}/".encode(), page)
            self.assertNotIn(self.code.encode(), page, "the classic page never shows the code")

    def test_the_constants_match_the_plan(self):
        self.assertEqual(phone_access.AUTO_OFF_SECONDS, 8 * 3600)
        self.assertEqual(phone_access.ADDRESS_CHECK_SECONDS, 60)
        self.assertEqual(phone_access.MAX_CONNECTIONS, 32)
        self.assertEqual(phone_access.GATE_TIMEOUT_SECONDS, 5.0)
        launcher = (ROOT / "scripts" / "Start-BiliFlow.ps1").read_text(encoding="utf-8")
        self.assertIn("Show-PhoneNotice", launcher)


class H4Events(HardeningBase):
    def test_events_have_the_device_ip_and_never_the_code_or_cookie(self):
        self.enable()
        form = lambda code: http(self.port, "POST", "/phone-login", host=self.host,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"},
                                 body=f"code={code}".encode())
        cookie = self.cookie()
        for _ in range(phone_access.MAX_FAILED_ATTEMPTS):
            form("wrongcod")
        form(phone_access.UNLOCK_KEY)  # unlocked
        for _ in range(phone_access.MAX_FAILED_ATTEMPTS):
            form("wrongcod")
        for _ in range(phone_access.MAX_UNLOCK_ATTEMPTS):
            form("1111")
        self.phone.disable()
        events = [e for e in self.store.events(None, limit=200) if e["event_type"].startswith("PHONE_")]
        kinds = {e["event_type"] for e in events}
        for kind in ("PHONE_MODE_ENABLED", "PHONE_LOGIN", "PHONE_CODE_WRONG", "PHONE_CODE_LOCKED",
                     "PHONE_UNLOCKED", "PHONE_UNLOCK_WRONG", "PHONE_UNLOCK_LOCKED", "PHONE_MODE_DISABLED"):
            self.assertIn(kind, kinds)
        for event in events:
            if event["event_type"] in ("PHONE_MODE_ENABLED", "PHONE_MODE_DISABLED"):
                continue
            self.assertEqual(event["payload"]["ip"], "127.0.0.1", event)
        dump = json.dumps(events, ensure_ascii=False)
        cookie_value = cookie.split("=", 1)[1]
        self.assertNotIn(self.code, dump)
        self.assertNotIn(cookie_value, dump)
        self.assertNotIn("wrongcod", dump, "typed text is never recorded")
        self.assertNotIn(phone_access.UNLOCK_KEY, dump.replace("2007-", ""))
        locked = [e for e in events if e["event_type"] in ("PHONE_CODE_LOCKED", "PHONE_UNLOCK_LOCKED")]
        self.assertTrue(all(e["level"] == "WARN" for e in locked))
        disabled = [e for e in events if e["event_type"] == "PHONE_MODE_DISABLED"][0]
        self.assertEqual(disabled["payload"]["reason"], "user")

    def test_the_pc_status_lists_the_ten_latest_events_and_the_phone_gets_none(self):
        self.enable()
        cookie = self.cookie()
        for _ in range(4):
            http(self.port, "POST", "/phone-login", host=self.host,
                 headers={"Content-Type": "application/x-www-form-urlencoded"}, body=b"code=nope")
        pc = json.loads(http(self.pc_port, "GET", "/api/phone-mode", host=f"127.0.0.1:{self.pc_port}")[2])
        self.assertLessEqual(len(pc["events"]), 10)
        self.assertEqual(pc["events"][0]["type"], "PHONE_CODE_WRONG", "newest first")
        self.assertNotIn(self.code, json.dumps(pc["events"]))
        phone = json.loads(http(self.port, "GET", "/api/phone-mode", host=self.host, headers={"Cookie": cookie})[2])
        self.assertNotIn("events", phone)
        self.assertNotIn(self.code, json.dumps(phone))


class Q14Q15ExtendAndHistory(HardeningBase):
    def pc_post(self, payload):
        body = json.dumps(payload).encode()
        return http(self.pc_port, "POST", "/api/phone-mode", host=f"127.0.0.1:{self.pc_port}",
                    headers={"Content-Type": "application/json", "X-BiliFlow-Token": "test-token"}, body=body)

    def test_extend_pushes_the_auto_off_back_and_keeps_the_code(self):
        """Question 15: 'Gia hạn thêm 8 giờ' (here the test lifetime) from now, same code."""
        status = self.enable(lifetime_seconds=0.8, check_seconds=5)
        time.sleep(0.5)
        code, _, body = self.pc_post({"extend": True})
        extended = json.loads(body)
        self.assertEqual(code, 200)
        self.assertEqual(extended["code"], status["code"])
        self.assertGreater(extended["expires_at"], status["expires_at"] + 0.3)
        time.sleep(0.5)  # past the first deadline
        self.assertTrue(self.phone.enabled, "the extension moved the deadline")
        deadline = time.monotonic() + 5
        while self.phone.enabled and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(self.phone.status()["last_disabled_reason"], "expired")
        kinds = [e["event_type"] for e in self.store.events(None)]
        self.assertIn("PHONE_MODE_EXTENDED", kinds)
        self.assertEqual(self.pc_post({"extend": True})[0], 400, "nothing to extend once off")
        self.assertEqual(self.pc_post({"extend": "yes"})[0], 400)

    def test_extend_is_pc_only(self):
        self.enable()
        cookie = self.cookie()
        status, _, body = self.phone_post("/api/phone-mode", {"extend": True}, cookie)
        self.assertEqual((status, json.loads(body)["code"]), (403, "pc_only"))
        self.assertEqual(self.phone_post("/api/phone-mode", {"extend": True}, cookie, token=None)[0], 403)

    def restarted(self):
        """A new Control Center process: a fresh PhoneAccess built from the same store."""
        self.phone.disable()
        fresh = ControlCenter.__new__(ControlCenter)
        fresh.store = self.store
        access = _phone_access(fresh)
        access._lan = lambda: FAKE_LAN
        return access

    def test_after_a_restart_the_latest_events_and_the_last_state_come_back(self):
        """Question 14: the PC panel shows the latest phone events and why it is off."""
        self.enable()
        self.cookie()
        http(self.port, "POST", "/phone-login", host=self.host,
             headers={"Content-Type": "application/x-www-form-urlencoded"}, body=b"code=nope")
        access = self.restarted()  # the previous run turned it off by hand ("user")
        status = access.status(include_secret=True)
        self.assertFalse(status["enabled"])
        self.assertEqual(status["last_disabled_reason"], "user")
        self.assertIsNotNone(status["last_disabled_at"])
        kinds = [e["type"] for e in status["events"]]
        self.assertEqual(kinds[0], "PHONE_MODE_DISABLED", "newest first")
        self.assertIn("PHONE_CODE_WRONG", kinds)
        self.assertIn("PHONE_LOGIN", kinds)
        self.assertTrue(all(e.get("restored") for e in status["events"]))
        self.assertTrue(any(e.get("ip") == "127.0.0.1" for e in status["events"]))
        self.assertNotIn(self.code, json.dumps(status))
        self.assertLessEqual(len(status["events"]), 10)

    def test_a_run_that_stopped_while_on_reads_as_stopped(self):
        self.store.add_event(None, "PHONE_MODE_ENABLED", "Bật chế độ điện thoại tại 192.168.1.5:8767",
                             payload={"address": "192.168.1.5", "port": 8767})
        fresh = ControlCenter.__new__(ControlCenter)
        fresh.store = self.store
        status = _phone_access(fresh).status(include_secret=True)
        self.assertEqual((status["enabled"], status["last_disabled_reason"]), (False, "stopped"))
        self.assertEqual(status["last_disabled_reason_text"], "Control Center dừng")
        self.assertEqual(status["events"][0]["type"], "PHONE_MODE_ENABLED")

    def test_no_history_is_fine(self):
        fresh = ControlCenter.__new__(ControlCenter)
        fresh.store = JobStore(self.root / "state" / "other.sqlite3")
        try:
            status = _phone_access(fresh).status(include_secret=True)
            self.assertEqual((status["last_disabled_reason"], status["events"]), (None, []))
        finally:
            fresh.store.close()


class H6SmallFixes(HardeningBase):
    def test_try_code_gives_the_cookie_in_the_same_call(self):
        self.enable()
        outcome, cookie = self.phone.try_code(self.code, ip="192.168.1.9")
        self.assertEqual(outcome, "ok")
        self.assertTrue(cookie.startswith(phone_access.COOKIE_NAME + "="))
        self.assertTrue(self.phone.cookie_ok(cookie.split(";", 1)[0]))
        self.assertEqual(self.phone.try_code("nope")[1], None)
        self.assertFalse(hasattr(self.phone, "set_cookie_header"), "no second lock round-trip left")
        self.phone.disable()
        self.assertEqual(self.phone.try_code(self.code), ("wrong", None))

    def test_the_guide_has_the_firewall_rule_for_port_8767(self):
        guide = (ROOT / "docs" / "DASHBOARD_V2_PHONE.md").read_text(encoding="utf-8")
        for needle in ("New-NetFirewallRule", "-LocalPort 8767", "-Profile Private", "-RemoteAddress LocalSubnet",
                       "Remove-NetFirewallRule"):
            self.assertIn(needle, guide)


class Batch4LowFindings(HardeningBase):
    """Plan §14: L1 per-device limit and a real gate deadline, L2 login events, L3 cut on off."""

    def fake_client(self, ip):
        """One accepted connection as if it came from `ip` (socketpair; no Wi-Fi address involved)."""
        server_end, client_end = socket.socketpair()
        client_end.settimeout(5)
        self.phone._server.process_request(server_end, (ip, 40000))
        return client_end

    def test_l1_one_device_cannot_take_every_connection(self):
        with mock.patch.object(phone_access, "GATE_TIMEOUT_SECONDS", 3.0):
            self.enable(max_per_ip=2)
            held = [self.fake_client("10.0.0.5") for _ in range(2)]
            time.sleep(0.2)
            self.assertEqual(self.phone._server.connections_of("10.0.0.5"), 2)
            third = self.fake_client("10.0.0.5")
            started = time.monotonic()
            self.assertEqual(third.recv(10), b"", "the same device's third connection is closed at once")
            self.assertLess(time.monotonic() - started, 1.0)
            other = self.fake_client("10.0.0.6")
            other.sendall(f"GET / HTTP/1.1\r\nHost: {self.host}\r\n\r\n".encode())
            reply = other.recv(65536)
            self.assertTrue(reply.startswith(b"HTTP/1.0 401"), "another device is still served")
            for sock in (third, other, *held):
                sock.close()

    def test_l1_refusals_write_one_rate_limited_event(self):
        with mock.patch.object(phone_access, "GATE_TIMEOUT_SECONDS", 3.0):
            self.enable(max_per_ip=1)
            first = self.fake_client("10.0.0.7")
            time.sleep(0.1)
            refused = [self.fake_client("10.0.0.7") for _ in range(5)]
            events = [e for e in self.store.events(None) if e["event_type"] == "PHONE_CONNECTIONS_LIMITED"]
            self.assertEqual(len(events), 1, "at most one event per interval")
            self.assertEqual((events[0]["payload"]["ip"], events[0]["payload"]["limit"]), ("10.0.0.7", "per_ip"))
            pc = json.loads(http(self.pc_port, "GET", "/api/phone-mode", host=f"127.0.0.1:{self.pc_port}")[2])
            self.assertEqual(pc["events"][0]["type"], "PHONE_CONNECTIONS_LIMITED", "the PC panel sees it")
            self.phone._limited_last -= phone_access.LIMITED_EVENT_INTERVAL_SECONDS + 1  # the interval passed
            refused.append(self.fake_client("10.0.0.7"))
            events = [e for e in self.store.events(None) if e["event_type"] == "PHONE_CONNECTIONS_LIMITED"]
            self.assertEqual(len(events), 2)
            for sock in (first, *refused):
                sock.close()
        self.assertEqual(phone_access.MAX_CONNECTIONS_PER_IP, 8)  # M1: 6–8 cookieless per device
        self.assertEqual(phone_access.LIMITED_EVENT_INTERVAL_SECONDS, 60.0)

    def test_l1_a_trickling_connection_is_cut_at_the_deadline(self):
        with mock.patch.object(phone_access, "GATE_TIMEOUT_SECONDS", 1.0):
            self.enable()
            sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            started = time.monotonic()
            closed_at = None
            request = f"GET / HTTP/1.1\r\nHost: {self.host}\r\nX-Slow: ".encode() + b"a" * 40
            for byte in request:
                try:
                    sock.sendall(bytes([byte]))
                except OSError:
                    closed_at = time.monotonic()
                    break
                time.sleep(0.3)  # one byte every 0.3 s: each read is well under the per-read timeout
                if time.monotonic() - started > 4:
                    break
            if closed_at is None:
                try:
                    data = sock.recv(10)
                except OSError:
                    data = b""
                self.assertEqual(data, b"")
                closed_at = time.monotonic()
            sock.close()
            self.assertLess(closed_at - started, 2.5, "cut about GATE_TIMEOUT_SECONDS after the accept")
            time.sleep(0.2)
            self.assertEqual(self.phone._server.connections_of("127.0.0.1"), 0, "the slot is given back")

    def test_l2_one_login_event_per_device_per_enable(self):
        self.enable()
        for _ in range(4):
            self.cookie()  # 127.0.0.1
        self.assertEqual(self.phone.try_code(self.code, ip="10.0.0.9")[0], "ok")
        self.assertEqual(self.phone.try_code(self.code, ip="10.0.0.9")[0], "ok")
        logins = [e["payload"]["ip"] for e in self.store.events(None) if e["event_type"] == "PHONE_LOGIN"]
        self.assertEqual(sorted(logins), ["10.0.0.9", "127.0.0.1"])
        self.phone.disable()
        self.enable()
        self.cookie()
        logins = [e for e in self.store.events(None) if e["event_type"] == "PHONE_LOGIN"]
        self.assertEqual(len(logins), 3, "a new enable records the device again")

    def test_l3_turning_off_cuts_a_video_being_streamed_but_not_the_pc(self):
        big = self.root / "big.mp4"
        with big.open("wb") as handle:
            handle.truncate(256 * 1024 * 1024)  # sparse: far more than the socket buffers hold
        self.center.media_key_valid = lambda job_id, key: True
        self.center.review_video = lambda job_id: (big, "video/mp4")
        self.enable()
        cookie = self.cookie()

        def start(port, host, extra=""):
            sock = socket.create_connection(("127.0.0.1", port), timeout=5)
            sock.sendall(f"GET /api/jobs/1/review/video?k=x HTTP/1.1\r\nHost: {host}\r\n{extra}\r\n".encode())
            head = sock.recv(65536)
            self.assertIn(b"200", head.split(b"\r\n", 1)[0])
            return sock

        phone_stream = start(self.port, self.host, f"Cookie: {cookie}\r\n")
        pc_stream = start(self.pc_port, f"127.0.0.1:{self.pc_port}")
        time.sleep(0.5)  # both servers are now blocked writing
        self.assertEqual(self.phone._server.connections_of("127.0.0.1"), 0, "M1: a stream with the cookie is not counted per IP")
        self.assertEqual(self.phone._server.open_connections(), 1)
        server = self.phone._server
        started = time.monotonic()
        self.phone.disable()
        phone_stream.settimeout(5)
        ended = False
        while time.monotonic() - started < 5:
            try:
                chunk = phone_stream.recv(1 << 20)
            except OSError:
                ended = True
                break
            if not chunk:
                ended = True
                break
        self.assertTrue(ended)
        self.assertLess(time.monotonic() - started, 2.0, "the phone stream ends with the mode")
        time.sleep(0.2)
        self.assertEqual(server.open_connections(), 0)
        # The PC stream keeps flowing.
        pc_stream.settimeout(5)
        received = sum(len(pc_stream.recv(1 << 20)) for _ in range(5))
        self.assertGreater(received, 0)
        phone_stream.close()
        pc_stream.close()

    def test_l4_the_guide_and_the_launcher_never_suggest_cancel(self):
        guide = (ROOT / "docs" / "DASHBOARD_V2_PHONE.md").read_text(encoding="utf-8")
        firewall = guide[guide.index("## 2. Windows Firewall"):guide.index("## 3.")]
        self.assertIn("Không bấm Cancel", firewall)
        self.assertNotIn("có thể bấm **Cancel**", guide)
        for needle in ("-Program $Python", "-LocalPort 8767", "-Profile Private", "-RemoteAddress LocalSubnet",
                       "cpython-3.11.*-windows-x86_64-none", "Public"):
            self.assertIn(needle, firewall)
        self.assertNotRegex(firewall, r"cpython-3\.11\.\d+-windows", "no hard-coded patch number")
        self.assertNotIn("kết nối chưa có cookie bị đóng sau 5 s;", guide)
        self.assertIn("5 s sau lúc kết nối", guide)
        self.assertIn("mỗi thiết bị (mỗi IP) tối đa 8", guide)
        launcher = (ROOT / "scripts" / "Start-BiliFlow.ps1").read_bytes()
        self.assertTrue(all(byte < 128 for byte in launcher), "Windows PowerShell 5.1 reads the script as ANSI")
        self.assertIn(b"KHONG bam Cancel", launcher)


class Batch5(HardeningBase):
    """Plan §15: M1 cookieless-only per-IP limit and a phone stream write timeout, M2 firewall guide,
    M3 cut POST bodies, M4 the review page's back button returns to V2."""

    def fake_client(self, ip):
        server_end, client_end = socket.socketpair()
        client_end.settimeout(5)
        self.phone._server.process_request(server_end, (ip, 40000))
        return client_end

    def test_m1_connections_with_the_cookie_do_not_count_against_the_device(self):
        with mock.patch.object(phone_access, "GATE_TIMEOUT_SECONDS", 5.0):
            self.enable(max_per_ip=3)
            cookie = self.cookie()
            # Ten connections that showed the cookie, held open by an unfinished POST body.
            head = (f"POST /api/scheduler HTTP/1.1\r\nHost: {self.host}\r\nCookie: {cookie}\r\n"
                    "X-BiliFlow-Token: test-token\r\nContent-Type: application/json\r\n"
                    "Content-Length: 16\r\n\r\n").encode()
            held = []
            for _ in range(10):
                sock = self.fake_client("10.0.0.5")
                sock.sendall(head)
                held.append(sock)
                time.sleep(0.05)
            time.sleep(0.3)
            server = self.phone._server
            self.assertEqual(server.connections_of("10.0.0.5"), 0, "promoted connections are not counted per IP")
            self.assertEqual(server.open_connections(), 10, "all ten are being served")
            # Cookieless connections of the same device are still limited.
            silent = [self.fake_client("10.0.0.5") for _ in range(3)]
            time.sleep(0.2)
            over = self.fake_client("10.0.0.5")
            self.assertEqual(over.recv(10), b"", "the 4th cookieless connection is closed at once")
            other = self.fake_client("10.0.0.6")
            other.sendall(f"GET / HTTP/1.1\r\nHost: {self.host}\r\n\r\n".encode())
            self.assertTrue(other.recv(65536).startswith(b"HTTP/1.0 401"), "another device is unaffected")
            for sock in held:
                sock.sendall(b'{"paused": true}')
                self.assertTrue(sock.recv(4096).startswith(b"HTTP/1.0 200"))
            for sock in (*held, *silent, over, other):
                sock.close()

    def test_m1_a_stalled_phone_stream_is_closed_but_the_pc_stream_is_not(self):
        big = self.root / "big.mp4"
        with big.open("wb") as handle:
            handle.truncate(256 * 1024 * 1024)
        self.center.media_key_valid = lambda job_id, key: True
        self.center.review_video = lambda job_id: (big, "video/mp4")
        with mock.patch.object(phone_access, "STREAM_WRITE_TIMEOUT_SECONDS", 1.0):
            self.enable()
            cookie = self.cookie()

            def start(port, host, extra=""):
                sock = socket.create_connection(("127.0.0.1", port), timeout=10)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
                sock.sendall(f"GET /api/jobs/1/review/video?k=x HTTP/1.1\r\nHost: {host}\r\n{extra}\r\n".encode())
                sock.recv(4096)
                return sock

            phone_stream = start(self.port, self.host, f"Cookie: {cookie}\r\n")
            pc_stream = start(self.pc_port, f"127.0.0.1:{self.pc_port}")
            server = self.phone._server
            deadline = time.monotonic() + 8
            while server.open_connections() and time.monotonic() < deadline:
                time.sleep(0.1)  # neither client reads: only the phone side gives up
            self.assertEqual(server.open_connections(), 0, "the stalled phone stream was closed")
            pc_stream.settimeout(5)
            self.assertGreater(len(pc_stream.recv(1 << 16)), 0, "the PC stream still flows after the same wait")
            phone_stream.close()
            pc_stream.close()
        self.assertEqual(phone_access.STREAM_WRITE_TIMEOUT_SECONDS, 60.0)

    def test_m3_a_cut_post_body_is_400_and_runs_nothing(self):
        self.enable()
        cookie = self.cookie()
        self.center.scheduler = mock.Mock()
        for path, body in (("/api/scheduler", b'{"paused":'), ("/api/jobs/1/ai-audit", b'{"visual"')):
            with self.subTest(path=path):
                head = (f"POST {path} HTTP/1.1\r\nHost: {self.host}\r\nCookie: {cookie}\r\n"
                        "X-BiliFlow-Token: test-token\r\nContent-Type: application/json\r\n"
                        "Content-Length: 64\r\n\r\n").encode()
                with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
                    sock.sendall(head + body)
                    sock.shutdown(socket.SHUT_WR)  # 10 bytes of 64, then the client stops sending
                    reply = b""
                    while chunk := sock.recv(4096):
                        reply += chunk
                self.assertTrue(reply.startswith(b"HTTP/1.0 400"), reply[:80])
                self.assertIn(phone_access.BODY_CUT_MESSAGE.encode(), reply)
        self.assertIsNone(self.store.setting("scheduler_paused"))
        self.center.start_ai_audit.assert_not_called()
        status, _, body = self.phone_post("/api/scheduler", {"paused": True}, cookie)
        self.assertEqual((status, json.loads(body)), (200, {"paused": True}), "a whole body still runs")
        # The PC listener is unchanged (its Handler.body() is not touched).
        source = Path(control_center.__file__).read_text(encoding="utf-8")
        self.assertIn('value = json.loads(self.rfile.read(length) or b"{}")', source)

    def test_m2_the_firewall_guide_uses_the_safe_commands_in_order(self):
        guide = (ROOT / "docs" / "DASHBOARD_V2_PHONE.md").read_text(encoding="utf-8")
        firewall = guide[guide.index("## 2. Windows Firewall"):guide.index("## 3.")]
        for needle in ("Sort-Object { [version]", "if ($Python -and (Test-Path -LiteralPath $Python -PathType Leaf))",
                       "Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule",
                       "Where-Object { $_.Program -ieq $Python }", "Disable-NetFirewallRule", "Enable-NetFirewallRule",
                       "junction", "làm lại bước 2–3"):
            self.assertIn(needle, firewall, needle)
        self.assertNotIn("phải trùng `$Python`", firewall)
        self.assertNotIn("Sort-Object Name", firewall)
        order = [firewall.index(marker) for marker in ("Đặt Wi-Fi nhà là Private", "Tạo rule", "Thử:",
                                                      "Chỉ khi điện thoại đã vào được")]
        self.assertEqual(order, sorted(order), "Private → rule → try → clean up")
        self.assertIn("8 này", guide)
        self.assertIn("cùng một NAT", guide)


if __name__ == "__main__":
    unittest.main()
