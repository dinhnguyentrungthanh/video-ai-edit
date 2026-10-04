"""Phone mode (Dashboard V2 batch 2, plan §12.3): P1–P5 on the real handlers.

The phone listener runs on 127.0.0.1 with a temporary root here (tests may not rely on a
private address); the production path only accepts a private IPv4 address and is checked
separately. No scheduler thread, watcher, Recycle Bin or real data.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import re
import socket
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from biliflow import phone_access
from biliflow.control_center import (
    ControlCenter,
    _dashboard_html,
    _handler_class,
    _phone_access,
    _phone_handler_class,
)
from biliflow.job_store import JobStore
from biliflow.review_evidence import ReviewFrameCache
from biliflow.scheduler import JobScheduler

ROOT = Path(__file__).resolve().parents[1]
CLASSIC = json.loads((ROOT / "tests" / "fixtures" / "dashboard_v2_classic_pages.json").read_text(encoding="utf-8"))
NO_CHECK = {"address": "127.0.0.1", "check_address": lambda _a: None, "check_port": lambda _p: None, "port": 0}


def request(port, path, *, method="GET", host=None, headers=None, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        sent = {"Host": host if host is not None else f"127.0.0.1:{port}", **(headers or {})}
        if body is not None and "Content-Length" not in sent:
            sent["Content-Length"] = str(len(body))
        connection.request(method, path, body=body, headers=sent)
        response = connection.getresponse()
        return response.status, response.getheaders(), response.read()
    finally:
        connection.close()


def header(headers, name):
    return [value for key, value in headers if key.lower() == name.lower()]


class PhoneModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.store = JobStore(self.root / "state" / "control-center.sqlite3")
        source = self.root / "input" / "Tập 1.mp4"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video" * 100)
        stat = source.stat()
        self.store.upsert_job(job_key="job-1", source_path=source, source_sha256=hashlib.sha256(b"1").hexdigest(),
                              source_size_bytes=stat.st_size, source_mtime_ns=stat.st_mtime_ns, state="COMPLETED")
        center = ControlCenter.__new__(ControlCenter)
        center.root, center.host, center.token, center.store = self.root, "127.0.0.1", "test-token", self.store
        center.frame_cache = ReviewFrameCache(self.root, self.root / "missing-ffmpeg.exe")
        center._stopping = threading.Event()
        center.scheduler = JobScheduler(self.root, self.store)  # never started
        center.recovered = 0
        recycler = Mock(side_effect=AssertionError("the Recycle Bin must never be reached"))
        center.recycler = center.bin_info = center.record_finder = recycler
        self.center = center
        self.pc = ThreadingHTTPServer(("127.0.0.1", 0), _handler_class(center))
        self.pc.daemon_threads = True
        self.pc_port = self.pc.server_address[1]
        threading.Thread(target=self.pc.serve_forever, daemon=True).start()
        self.phone = _phone_access(center)

    def tearDown(self):
        self.phone.disable()
        self.pc.shutdown()
        self.pc.server_close()
        self.store.close()
        self.temp.cleanup()

    # helpers ---------------------------------------------------------------
    def enable(self):
        status = self.phone.enable(lambda access: _phone_handler_class(self.center, access), **NO_CHECK)
        self.port = status["port"]
        return status

    def get(self, path, **kw):
        return request(self.port, path, **kw)

    def form(self, code):
        return request(self.port, "/phone-login", method="POST", body=f"code={code}".encode(),
                       headers={"Content-Type": "application/x-www-form-urlencoded"})

    def login(self, code):
        status, headers, body = self.form(code)
        cookie = header(headers, "Set-Cookie")
        return status, (cookie[0].split(";", 1)[0] if cookie else None), body

    def post(self, port, path, payload, headers=None, host=None):
        body = json.dumps(payload).encode()
        return request(port, path, method="POST", body=body, host=host,
                       headers={"Content-Type": "application/json", **(headers or {})})

    # P1 --------------------------------------------------------------------
    def test_p1_nothing_without_the_cookie_and_the_code_gives_a_cookie(self):
        status = self.enable()
        self.assertRegex(status["code"], r"^[abcdefghjkmnpqrstuvwxyz23456789]{8}$")
        for path in ("/", "/dashboard-v2/", "/dashboard-v2/app.js", "/api/session", "/api/status", "/api/jobs",
                     "/api/jobs/1", "/review/1", "/media/x.jpg", "/api/logo-memory", "/healthz", "/logo-memory",
                     "/api/source-cleanup/preview?ids=1", "/nope"):
            with self.subTest(path=path):
                code, _, body = self.get(path)
                self.assertEqual(code, 401, path)
                self.assertNotIn(b"test-token", body)
                self.assertNotIn(status["code"].encode(), body)
        code, headers, body = self.get("/")
        self.assertIn('action="/phone-login"'.encode(), body)
        self.assertTrue(any("default-src 'none'" in v for v in header(headers, "Content-Security-Policy")))
        self.assertIn("frame-ancestors 'self'", header(headers, "Content-Security-Policy"))
        code, cookie, body = self.login(status["code"])
        self.assertEqual(code, 200)
        self.assertIn(b'http-equiv="refresh" content="0;url=/dashboard-v2/"', body)
        _, headers, _ = self.form(status["code"])
        set_cookie = header(headers, "Set-Cookie")[0]
        for flag in ("HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(flag, set_cookie)
        self.assertNotIn(status["code"], set_cookie, "the cookie proves the code without carrying it")
        code, _, body = self.get("/api/session", headers={"Cookie": cookie})
        self.assertEqual((code, json.loads(body)), (200, {"token": "test-token"}))
        code, _, body = self.get("/dashboard-v2/", headers={"Cookie": cookie})
        self.assertEqual(code, 200)
        self.assertIn(b'data-mode="live"', body)
        code, headers, _ = self.get("/", headers={"Cookie": cookie})
        self.assertEqual((code, header(headers, "Location")), (303, ["/dashboard-v2/"]))
        code, _, _ = self.get("/api/status", headers={"Cookie": cookie + "x"})
        self.assertEqual(code, 401)

    def test_p1_the_form_login_works_and_wrong_codes_lock_entry_until_the_next_enable(self):
        status = self.enable()
        form = lambda code: request(self.port, "/phone-login", method="POST", body=f"code={code}".encode(),
                                    headers={"Content-Type": "application/x-www-form-urlencoded"})
        for attempt in range(1, phone_access.MAX_FAILED_ATTEMPTS):
            code, _, body = form("wrongcod")
            self.assertEqual(code, 401, attempt)
            self.assertIn(f"Còn {phone_access.MAX_FAILED_ATTEMPTS - attempt} lần".encode(), body)
        code, _, body = form("nopenope")  # the 10th wrong code locks
        self.assertEqual(code, 403)
        self.assertTrue(self.phone.status()["locked"])
        code, headers, _ = form(status["code"])
        self.assertEqual(code, 403, "even the right code is refused once locked")
        self.assertEqual(header(headers, "Set-Cookie"), [])
        self.assertEqual(self.get("/")[0], 403)
        self.phone.disable()
        again = self.enable()
        self.assertFalse(again["locked"])
        self.assertNotEqual(again["code"], status["code"])
        code, headers, _ = form(again["code"])
        self.assertEqual(code, 200)
        self.assertEqual(len(header(headers, "Set-Cookie")), 1)

    def lock(self, form):
        for _ in range(phone_access.MAX_FAILED_ATTEMPTS):
            form("wrongcod")
        self.assertTrue(self.phone.status()["locked"])

    def test_p1_the_special_key_only_lifts_the_lock(self):
        """Question 9: 2007 lifts the lock and resets the count; the real code is still needed."""
        status = self.enable()
        form = lambda code: request(self.port, "/phone-login", method="POST", body=f"code={code}".encode(),
                                    headers={"Content-Type": "application/x-www-form-urlencoded"})
        code, _, _ = form(phone_access.UNLOCK_KEY)  # not locked: just a wrong code, nothing opens
        self.assertEqual(code, 401)
        self.assertEqual(self.phone.status()["failed_attempts"], 1)
        self.lock(form)
        code, _, body = self.get("/")
        self.assertEqual(code, 403)
        self.assertIn("Khóa mở đặc biệt".encode(), body)
        self.assertNotIn(phone_access.UNLOCK_KEY.encode(), body, "the page never shows the key")
        code, headers, body = form(phone_access.UNLOCK_KEY)
        self.assertEqual(code, 401)
        self.assertEqual(header(headers, "Set-Cookie"), [], "the key never gives a cookie by itself")
        self.assertIn("Đã gỡ khóa".encode(), body)
        state = self.phone.status()
        self.assertEqual((state["locked"], state["failed_attempts"]), (False, 0))
        self.assertEqual(self.get("/api/status")[0], 401)
        code, headers, _ = form(status["code"])
        self.assertEqual(code, 200)
        self.assertEqual(len(header(headers, "Set-Cookie")), 1)

    def test_p1_wrong_special_keys_lock_the_key_until_the_next_enable(self):
        status = self.enable()
        form = lambda code: request(self.port, "/phone-login", method="POST", body=f"code={code}".encode(),
                                    headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.lock(form)
        for attempt in range(1, phone_access.MAX_UNLOCK_ATTEMPTS):
            code, _, body = form("1234")
            self.assertEqual(code, 403, attempt)
            self.assertIn(f"Còn {phone_access.MAX_UNLOCK_ATTEMPTS - attempt} lần".encode(), body)
        code, _, _ = form(status["code"])  # the 5th wrong key: even the real code counts as one
        self.assertEqual(code, 403)
        self.assertTrue(self.phone.status()["unlock_locked"])
        code, headers, body = form(phone_access.UNLOCK_KEY)
        self.assertEqual(code, 403)
        self.assertEqual(header(headers, "Set-Cookie"), [])
        self.assertTrue(self.phone.status()["locked"])
        self.assertNotIn(b'action="/phone-login"', self.get("/")[2], "no form once the key is locked too")
        self.phone.disable()
        again = self.enable()
        self.assertEqual((again["locked"], again["unlock_locked"], again["unlock_failures"]), (False, False, 0))
        self.assertEqual(form(again["code"])[0], 200)

    def test_p1_the_special_key_lifts_the_lock_only_a_few_times_per_enable(self):
        self.enable()
        for round_ in range(phone_access.MAX_UNLOCKS):
            self.phone.locked = True
            self.assertEqual(self.phone.try_code(phone_access.UNLOCK_KEY), "unlocked", round_)
        self.phone.locked = True
        self.assertEqual(self.phone.try_code(phone_access.UNLOCK_KEY), "unlock_locked")
        self.assertTrue(self.phone.status()["locked"])
        self.phone.disable()
        self.enable()
        self.phone.locked = True
        self.assertEqual(self.phone.try_code(phone_access.UNLOCK_KEY), "unlocked")

    def test_p1_the_special_key_counter_is_safe_under_parallel_attempts(self):
        self.enable()
        self.phone.locked = True
        outcomes = []
        threads = [threading.Thread(target=lambda: outcomes.append(self.phone.try_code("9999"))) for _ in range(40)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(self.phone.status()["unlock_failures"], phone_access.MAX_UNLOCK_ATTEMPTS)
        self.assertEqual(outcomes.count("wrong_unlock"), phone_access.MAX_UNLOCK_ATTEMPTS - 1)
        self.assertEqual(self.phone.try_code(phone_access.UNLOCK_KEY), "unlock_locked")

    def test_p1_the_code_form_works_with_the_origin_a_browser_sends(self):
        status = self.enable()
        code, headers, _ = self.get("/")
        self.assertEqual(header(headers, "Referrer-Policy"), ["same-origin"],
                         "no-referrer would make browsers send Origin: null on the form")
        send = lambda origin: request(self.port, "/phone-login", method="POST", body=f"code={status['code']}".encode(),
                                      headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": origin})
        self.assertEqual(send("null")[0], 403)
        self.assertEqual(send("http://evil.example")[0], 403)
        self.assertEqual(self.phone.status()["failed_attempts"], 0, "a refused origin is not a code attempt")
        code, headers, _ = send(f"http://127.0.0.1:{self.port}")
        self.assertEqual(code, 200)
        self.assertEqual(len(header(headers, "Set-Cookie")), 1)

    def test_p1_a_code_in_a_link_never_logs_in_and_is_not_an_attempt(self):
        """Question 12: the code is typed on the phone; ?code= is ignored everywhere."""
        status = self.enable()
        self.assertNotIn("link", status)
        self.assertNotIn("?code=", json.dumps(status))
        for path in ("/?code=", "/dashboard-v2/?code=", "/phone-login?code="):
            with self.subTest(path=path):
                code, headers, body = self.get(path + status["code"])
                self.assertEqual(code, 401)
                self.assertEqual(header(headers, "Set-Cookie"), [])
                self.assertIn(b'action="/phone-login"', body)
        self.assertEqual(self.phone.status()["failed_attempts"], 0)
        _, pc_headers, pc_body = request(self.pc_port, "/api/phone-mode")
        self.assertNotIn(b"?code=", pc_body)

    def test_p1_a_cookie_from_the_previous_enable_no_longer_opens_anything(self):
        status = self.enable()
        _, cookie, _ = self.login(status["code"])
        self.assertEqual(self.get("/api/status", headers={"Cookie": cookie})[0], 200)
        self.phone.disable()
        self.enable()
        self.assertEqual(self.get("/api/status", headers={"Cookie": cookie})[0], 401)

    # P2 --------------------------------------------------------------------
    def test_p2_host_token_and_pc_only_actions(self):
        status = self.enable()
        _, cookie, _ = self.login(status["code"])
        for bad in ("localhost:%d" % self.port, "127.0.0.1", "evil.example:%d" % self.port, "evil.example", ""):
            with self.subTest(host=bad):
                self.assertEqual(self.get("/api/status", host=bad, headers={"Cookie": cookie})[0], 403)
        # POST without the session token: 403 and nothing written.
        code, _, _ = self.post(self.port, "/api/scheduler", {"paused": True}, {"Cookie": cookie})
        self.assertEqual(code, 403)
        self.assertIsNone(self.store.setting("scheduler_paused"))
        # A foreign Origin is refused even with cookie and token.
        code, _, _ = self.post(self.port, "/api/scheduler", {"paused": True},
                               {"Cookie": cookie, "X-BiliFlow-Token": "test-token", "Origin": "http://evil.example"})
        self.assertEqual(code, 403)
        # With cookie + token + same origin a normal action runs through the phone.
        code, _, body = self.post(self.port, "/api/scheduler", {"paused": True},
                                  {"Cookie": cookie, "X-BiliFlow-Token": "test-token",
                                   "Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual((code, json.loads(body)), (200, {"paused": True}))
        pc_only = {
            "/api/source-cleanup": {"job_ids": [1], "preview_id": "a" * 64},
            "/api/source-archive": {"job_ids": [1], "preview_id": "a" * 64},
            "/api/source-archive/restore": {"job_id": 1},
            "/api/source-recycle-check": {"kind": "source_cleanup", "id": 1},
            "/api/shutdown": {"mode": "after_stage"},
            "/api/ai/config": {"enabled": False},
            "/api/ai/login": {},
            "/api/logo-memory/class": {"key": "k", "memory_class": "studio_logo", "expected_sha256": "a" * 64},
            "/api/logo-memory/delete": {"key": "k", "expected_sha256": "a" * 64},
            "/api/phone-mode": {"enabled": False},
        }
        for path, payload in pc_only.items():
            with self.subTest(path=path):
                code, _, body = self.post(self.port, path, payload, {"Cookie": cookie, "X-BiliFlow-Token": "test-token"})
                self.assertEqual(code, 403, path)
                answer = json.loads(body)
                self.assertEqual(answer["code"], "pc_only")
                self.assertTrue(answer["error"].startswith("Chỉ làm trên PC"))
        self.assertFalse(self.center._stopping.is_set(), "shutdown never ran")
        self.assertTrue(self.phone.enabled, "the phone cannot turn the phone mode off")
        # The same PC-only actions still reach their handlers over 127.0.0.1 (no pc_only refusal).
        for path, payload in pc_only.items():
            if path in ("/api/shutdown", "/api/ai/login", "/api/phone-mode"):
                continue  # would stop the center, start a login process or close the listener under test
            with self.subTest(pc=path):
                code, _, body = self.post(self.pc_port, path, payload, {"X-BiliFlow-Token": "test-token"})
                self.assertNotEqual(json.loads(body).get("code"), "pc_only", path)
        # The phone learns it is remote, never the code, link or counters.
        code, _, body = self.get("/api/phone-mode", headers={"Cookie": cookie})
        answer = json.loads(body)
        self.assertEqual((code, answer["remote"]), (200, True))
        self.assertNotIn("code", answer)
        self.assertNotIn(status["code"].encode(), body)

    # P3 --------------------------------------------------------------------
    def test_p3_toggle_only_over_127_0_0_1_with_the_token(self):
        self.assertEqual(json.loads(request(self.pc_port, "/api/phone-mode")[2])["enabled"], False)
        code, _, _ = self.post(self.pc_port, "/api/phone-mode", {"enabled": True})
        self.assertEqual(code, 403, "token required")
        code, _, body = self.post(self.pc_port, "/api/phone-mode", {"enabled": "yes"}, {"X-BiliFlow-Token": "test-token"})
        self.assertEqual(code, 400)
        # Over HTTP the production checks apply: no private address here, so it is refused.
        self.phone._lan = lambda: "127.0.0.1"
        code, _, body = self.post(self.pc_port, "/api/phone-mode", {"enabled": True}, {"X-BiliFlow-Token": "test-token"})
        self.assertEqual(code, 400)
        self.assertIn("IPv4 riêng", json.loads(body)["error"])
        self.assertFalse(self.phone.enabled)
        first = self.enable()
        status = json.loads(request(self.pc_port, "/api/phone-mode")[2])
        self.assertEqual((status["enabled"], status["code"], status["port"]), (True, first["code"], self.port))
        self.assertEqual(self.enable()["code"], first["code"], "turning it on again keeps the running code")
        code, _, body = self.post(self.pc_port, "/api/phone-mode", {"enabled": False}, {"X-BiliFlow-Token": "test-token"})
        self.assertEqual((code, json.loads(body)["enabled"], json.loads(body)["code"]), (200, False, None))
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", self.port), timeout=2).close()
        second = self.enable()
        self.assertNotEqual(second["code"], first["code"])
        self.assertEqual(self.login(first["code"])[0], 401)
        self.assertEqual(self.login(second["code"])[0], 200)

    def test_p3_stopping_the_control_center_closes_the_phone_listener(self):
        self.enable()
        self.center.stop_ai_audits = self.center.stop_ai_login = lambda: None
        self.center.server = None
        self.center.watcher = Mock()
        self.center.scheduler = Mock()
        self.center._stopped = threading.Event()
        self.center.lock = Mock()
        try:
            self.center.stop()
        except Exception:  # noqa: BLE001 - the stub center lacks parts stop() touches after the phone
            pass
        self.assertFalse(self.phone.enabled)

    # P4 --------------------------------------------------------------------
    def test_p4_only_a_private_ipv4_address_is_ever_bound(self):
        for good in ("10.0.0.5", "172.16.0.1", "172.31.255.254", "192.168.1.23"):
            self.assertTrue(phone_access.is_private_ipv4(good), good)
        for bad in ("0.0.0.0", "127.0.0.1", "8.8.8.8", "172.32.0.1", "100.64.0.1", "169.254.1.1", "::",
                    "::1", "fd00::1", "localhost", "192.168.1.256", "192.168.01.5", " 192.168.1.5", "", None):
            with self.subTest(address=bad):
                self.assertFalse(phone_access.is_private_ipv4(bad))
                access = phone_access.PhoneAccess(lan=lambda: bad)
                factory = Mock(side_effect=AssertionError("nothing may be bound"))
                with self.assertRaises(ValueError):
                    access.enable(factory)
                with self.assertRaises(ValueError):
                    phone_access.PhoneAccess().enable(factory, address=bad)
                factory.assert_not_called()
                self.assertFalse(access.enabled)
        for port in (0, 80, 1023, 8765, 70000, "8767", True):
            with self.subTest(port=port), self.assertRaises(ValueError):
                phone_access.PhoneAccess(lan=lambda: "192.168.1.23").enable(Mock(), port=port)
        with self.assertRaises(ValueError):
            phone_access.lan_address(probe_target="127.0.0.1")  # routes over loopback: not a home Wi-Fi

    # P5 --------------------------------------------------------------------
    def test_p5_the_127_0_0_1_listener_is_unchanged(self):
        self.enable()
        code, headers, body = request(self.pc_port, "/")
        self.assertEqual(code, 200)
        self.assertEqual(body, _dashboard_html().encode())
        self.assertEqual((len(body), hashlib.sha256(body).hexdigest()),
                         (CLASSIC["dashboard_bytes"], CLASSIC["dashboard_sha256"]))
        code, _, body = request(self.pc_port, "/review/1")
        self.assertEqual(hashlib.sha256(body).hexdigest(), CLASSIC["review_job_1_token_test_sha256"])
        self.assertEqual(request(self.pc_port, "/api/session")[0], 200)
        self.assertEqual(request(self.pc_port, "/", host="192.168.1.23:8767")[0], 403,
                         "the PC listener still refuses the phone listener's Host")
        self.assertEqual(self.post(self.pc_port, "/api/scheduler", {"paused": True})[0], 403)


if __name__ == "__main__":
    unittest.main()
