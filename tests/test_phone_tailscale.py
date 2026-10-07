"""Phone mode over Tailscale (2026-10-06): the phone listener on the PC's Tailscale address.

The user chose Tailscale to reach BiliFlow from outside the home Wi-Fi, with the same actions as
on the home Wi-Fi. Every listener here runs on 127.0.0.1 with a temporary root; every PhoneAccess
gets fake lookups, so no test runs the real `tailscale` CLI or binds a real address.
"""
from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from biliflow import control_center, phone_access, tailscale_manager
from biliflow.control_center import ControlCenter, _handler_class, _phone_access, _phone_handler_class, _tailscale
from biliflow.job_store import JobStore
from biliflow.review_evidence import ReviewFrameCache
from biliflow.scheduler import JobScheduler

ROOT = Path(__file__).resolve().parents[1]
TAILNET_ADDRESS = "100.101.102.103"


def http(port, method, path, *, host, headers=None, body=b"") -> tuple[int, dict, bytes]:
    lines = [f"{method} {path} HTTP/1.1", f"Host: {host}", "Connection: close"]
    for key, value in (headers or {}).items():
        lines.append(f"{key}: {value}")
    if body or method == "POST":
        lines.append(f"Content-Length: {len(body)}")
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode() + body)
        chunks = []
        while chunk := sock.recv(65536):
            chunks.append(chunk)
    head, _, payload = b"".join(chunks).partition(b"\r\n\r\n")
    status_line, *header_lines = head.decode("iso-8859-1").split("\r\n")
    parsed: dict[str, list[str]] = {}
    for line in header_lines:
        key, _, value = line.partition(":")
        parsed.setdefault(key.strip().lower(), []).append(value.strip())
    return int(status_line.split()[1]), parsed, payload


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def no_lookup():
    raise AssertionError("this lookup must not run")


class TailscaleAddressTests(unittest.TestCase):
    def test_only_the_tailscale_range_counts(self):
        for good in ("100.64.0.1", TAILNET_ADDRESS, "100.127.255.254"):
            self.assertTrue(phone_access.is_tailscale_ipv4(good), good)
        for bad in ("100.63.255.255", "100.128.0.1", "192.168.1.5", "10.0.0.1", "127.0.0.1", "0.0.0.0",
                    "8.8.8.8", "100.64.0.01", " 100.64.0.1", "fd7a:115c:a1e0::1", "", None):
            with self.subTest(address=bad):
                self.assertFalse(phone_access.is_tailscale_ipv4(bad))
                with self.assertRaises(ValueError):
                    phone_access.require_tailscale_ipv4(bad)

    def test_the_address_comes_from_tailscale_ip_4(self):
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, stdout=TAILNET_ADDRESS + "\r\n", stderr="")

        address = phone_access.tailscale_address(run=run, cli=lambda: r"C:\Tailscale\tailscale.exe")
        self.assertEqual(address, TAILNET_ADDRESS)
        command, kwargs = calls[0]
        self.assertEqual(command, [r"C:\Tailscale\tailscale.exe", "ip", "-4"])
        self.assertTrue(kwargs["capture_output"])
        self.assertEqual(kwargs["timeout"], phone_access.TAILSCALE_TIMEOUT_SECONDS)
        self.assertFalse(kwargs.get("shell", False))

    def test_a_missing_stopped_or_odd_tailscale_is_refused_with_a_reason(self):
        def answer(code, out="", err=""):
            return lambda command, **_kw: subprocess.CompletedProcess(command, code, stdout=out, stderr=err)

        def raises(error):
            def run(command, **_kw):
                raise error
            return run

        cases = {
            "stopped": (answer(1, err="Tailscale is stopped."), phone_access.TAILSCALE_DOWN_MESSAGE[:20]),
            "empty": (answer(0, out="\n"), phone_access.TAILSCALE_DOWN_MESSAGE[:20]),
            "not tailnet": (answer(0, out="192.168.1.5\n"), "Tailscale"),
            "no exe": (raises(FileNotFoundError("x")), phone_access.TAILSCALE_DOWN_MESSAGE[:20]),
            "hangs": (raises(subprocess.TimeoutExpired("tailscale", 10)), phone_access.TAILSCALE_DOWN_MESSAGE[:20]),
        }
        for name, (run, expected) in cases.items():
            with self.subTest(case=name), self.assertRaises(ValueError) as caught:
                phone_access.tailscale_address(run=run, cli=lambda: "tailscale.exe")
            self.assertIn(expected, str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            phone_access.tailscale_address(run=lambda *_a, **_k: no_lookup(), cli=lambda: None)
        self.assertEqual(str(caught.exception), phone_access.NO_TAILSCALE_MESSAGE)

    def test_the_cli_is_only_looked_up_in_program_files(self):
        with TemporaryDirectory() as folder:
            self.assertIsNone(phone_access.tailscale_cli(program_files=folder))
            exe = Path(folder) / "Tailscale" / "tailscale.exe"
            exe.parent.mkdir()
            exe.write_bytes(b"")
            self.assertEqual(phone_access.tailscale_cli(program_files=folder), str(exe))
        source = Path(phone_access.__file__).read_text(encoding="utf-8")
        self.assertNotIn("shutil.which", source, "PATH and the current folder are never searched")


class TailscaleEnableTests(unittest.TestCase):
    def test_the_network_must_be_wifi_or_tailscale(self):
        for network in ("public", "", None, 1, "Tailscale", "lan"):
            with self.subTest(network=network):
                access = phone_access.PhoneAccess(lan=no_lookup, tailscale=no_lookup)
                factory = mock.Mock(side_effect=AssertionError("nothing may be bound"))
                with self.assertRaises(ValueError) as caught:
                    access.enable(factory, network=network)
                self.assertEqual(str(caught.exception), phone_access.NETWORK_MESSAGE)
                factory.assert_not_called()
                self.assertFalse(access.enabled)

    def test_tailscale_uses_its_own_lookup_and_check(self):
        seen = []

        def check(address):
            seen.append(address)
            raise ValueError("stop before binding")

        access = phone_access.PhoneAccess(lan=no_lookup, tailscale=lambda: TAILNET_ADDRESS)
        with self.assertRaises(ValueError):
            access.enable(mock.Mock(), network="tailscale", check_address=check)
        self.assertEqual(seen, [TAILNET_ADDRESS])

    def test_tailscale_refuses_a_home_wifi_address_and_wifi_refuses_a_tailscale_one(self):
        factory = mock.Mock(side_effect=AssertionError("nothing may be bound"))
        with self.assertRaises(ValueError) as caught:
            phone_access.PhoneAccess(tailscale=lambda: "192.168.1.5").enable(factory, network="tailscale")
        self.assertIn("Tailscale", str(caught.exception))
        with self.assertRaises(ValueError):
            phone_access.PhoneAccess(lan=lambda: TAILNET_ADDRESS).enable(factory)
        factory.assert_not_called()

    def test_turning_it_off_during_the_lookup_cancels_the_enable(self):
        started, release = threading.Event(), threading.Event()

        def slow():
            started.set()
            release.wait(5)
            return "127.0.0.1"

        access = phone_access.PhoneAccess(lan=no_lookup, tailscale=slow)
        factory = mock.Mock(side_effect=AssertionError("nothing may be bound"))
        outcome = {}

        def run():
            try:
                access.enable(factory, network="tailscale", check_address=lambda _a: None,
                              check_port=lambda _p: None, port=0)
            except ValueError as error:
                outcome["error"] = str(error)

        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(started.wait(5))
        access.disable("user")
        release.set()
        worker.join(5)
        self.assertEqual(outcome.get("error"), phone_access.ENABLE_CANCELLED_MESSAGE)
        self.assertFalse(access.enabled)
        factory.assert_not_called()

    def test_a_tailscale_lookup_error_reaches_the_user(self):
        def down():
            raise ValueError(phone_access.NO_TAILSCALE_MESSAGE)

        with self.assertRaises(ValueError) as caught:
            phone_access.PhoneAccess(tailscale=down).enable(mock.Mock(), network="tailscale")
        self.assertEqual(str(caught.exception), phone_access.NO_TAILSCALE_MESSAGE)


class ListenerBase(unittest.TestCase):
    """The Control Center handlers with the phone listener on 127.0.0.1 standing in for 100.x."""

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
        self.phone._lan = no_lookup          # never the real Wi-Fi
        self.phone._tailscale = lambda: "127.0.0.1"  # never the real CLI

    def tearDown(self):
        self.phone.disable()
        self.pc.shutdown()
        self.pc.server_close()
        self.store.close()
        self.temp.cleanup()

    def pc_post(self, payload):
        status, _, body = http(self.pc_port, "POST", "/api/phone-mode", host=f"127.0.0.1:{self.pc_port}",
                               headers={"Content-Type": "application/json", "X-BiliFlow-Token": "test-token"},
                               body=json.dumps(payload).encode())
        return status, json.loads(body)

    def pc_status(self):
        return json.loads(http(self.pc_port, "GET", "/api/phone-mode", host=f"127.0.0.1:{self.pc_port}")[2])

    def enable_over_http(self):
        port = free_port()
        with mock.patch.object(phone_access, "require_tailscale_ipv4", lambda _a: None):
            status, answer = self.pc_post({"enabled": True, "network": "tailscale", "port": port})
        self.assertEqual(status, 200, answer)
        return answer


class TailscaleListenerTests(ListenerBase):
    def test_the_pc_turns_it_on_for_tailscale(self):
        answer = self.enable_over_http()
        self.assertEqual((answer["enabled"], answer["network"]), (True, "tailscale"))
        self.assertEqual(answer["network_text"], "Tailscale")
        self.assertEqual(answer["url"], f"http://127.0.0.1:{answer['port']}/")
        self.assertEqual(self.pc_status()["network"], "tailscale")
        enabled = [e for e in self.store.events(None) if e["event_type"] == "PHONE_MODE_ENABLED"]
        self.assertEqual(enabled[0]["payload"]["network"], "tailscale")
        self.assertIn("Tailscale", enabled[0]["message"])
        self.assertNotIn(answer["code"], json.dumps(enabled, ensure_ascii=False))

    def test_over_http_the_real_checks_refuse_a_non_tailscale_address_and_an_unknown_network(self):
        status, answer = self.pc_post({"enabled": True, "network": "tailscale"})
        self.assertEqual(status, 400)
        self.assertIn("Tailscale", answer["error"])
        status, answer = self.pc_post({"enabled": True, "network": "internet"})
        self.assertEqual((status, answer["error"]), (400, phone_access.NETWORK_MESSAGE))

        def down():
            raise ValueError(phone_access.NO_TAILSCALE_MESSAGE)
        self.phone._tailscale = down
        status, answer = self.pc_post({"enabled": True, "network": "tailscale"})
        self.assertEqual((status, answer["error"]), (400, phone_access.NO_TAILSCALE_MESSAGE))
        self.assertFalse(self.phone.enabled)

    def test_the_wifi_default_is_unchanged(self):
        self.phone._lan = lambda: "127.0.0.1"
        status, answer = self.pc_post({"enabled": True})
        self.assertEqual(status, 400, "127.0.0.1 is not a home Wi-Fi address")
        self.assertIn("IPv4 riêng", answer["error"])
        self.assertIsNone(self.pc_status()["network"])

    def test_a_device_of_the_pcs_tailscale_account_opens_without_the_code(self):
        """The user's choice (2026-10-07): over Tailscale the PC's own account needs no code."""
        asked = []
        self.phone._tailscale_device = lambda ip: asked.append(ip) or "zphone"
        answer = self.enable_over_http()
        port, host = answer["port"], f"127.0.0.1:{answer['port']}"
        self.assertEqual(http(port, "GET", "/api/jobs", host=host)[0], 401)
        self.assertEqual(asked, [], "an API call without the cookie never asks Tailscale")
        status, headers, body = http(port, "GET", "/", host=host)
        self.assertEqual(status, 200)
        self.assertIn("Đang mở BiliFlow", body.decode())
        self.assertNotIn(answer["code"].encode(), body)
        self.assertEqual(asked, ["127.0.0.1"])
        cookie = headers["set-cookie"][0].split(";", 1)[0]
        self.assertEqual(http(port, "GET", "/api/jobs", host=host, headers={"Cookie": cookie})[0], 200)
        http(port, "GET", "/dashboard-v2/", host=host)  # a second entry: one PHONE_LOGIN per device
        logins = [e for e in self.store.events(None) if e["event_type"] == "PHONE_LOGIN"]
        self.assertEqual(len(logins), 1)
        self.assertEqual((logins[0]["payload"]["ip"], logins[0]["payload"]["device"]), ("127.0.0.1", "zphone"))
        self.assertIn("không cần mã", logins[0]["message"])

    def test_any_other_device_still_types_the_code(self):
        def broken(_ip):
            raise OSError("the CLI is gone")
        for lookup in (lambda _ip: None, broken):
            with self.subTest(lookup=lookup):
                self.phone._tailscale_device = lookup
                answer = self.enable_over_http()
                status, headers, body = http(answer["port"], "GET", "/", host=f"127.0.0.1:{answer['port']}")
                self.assertEqual(status, 401)
                self.assertNotIn("set-cookie", headers)
                self.assertIn("Nhập mã truy cập", body.decode())
                self.phone.disable()

    def test_on_the_home_wifi_tailscale_is_never_asked(self):
        asked = []
        self.phone._tailscale_device = lambda ip: asked.append(ip) or "zphone"
        self.phone.enable(lambda access: _phone_handler_class(self.center, access), address="127.0.0.1",
                          check_address=lambda _a: None, check_port=lambda _p: None, port=0)
        status, _, _ = http(self.phone.port, "GET", "/", host=f"127.0.0.1:{self.phone.port}")
        self.assertEqual((status, asked), (401, []))

    def test_the_phone_logs_in_and_learns_the_network_but_not_the_code(self):
        answer = self.enable_over_http()
        port, host = answer["port"], f"127.0.0.1:{answer['port']}"
        status, _, page = http(port, "GET", "/", host=host)
        self.assertEqual(status, 401)
        text = page.decode("utf-8")
        self.assertIn("Tailscale", text)
        self.assertNotIn("Chỉ dùng trong Wi-Fi nhà", text)
        status, headers, _ = http(port, "POST", "/phone-login", host=host,
                                  headers={"Content-Type": "application/x-www-form-urlencoded"},
                                  body=f"code={answer['code']}".encode())
        self.assertEqual(status, 200)
        cookie = headers["set-cookie"][0].split(";", 1)[0]
        status, _, body = http(port, "GET", "/api/phone-mode", host=host, headers={"Cookie": cookie})
        remote = json.loads(body)
        self.assertEqual((status, remote["remote"], remote["network"]), (200, True, "tailscale"))
        self.assertNotIn(answer["code"].encode(), body)
        self.assertEqual(http(port, "GET", "/", host=f"{TAILNET_ADDRESS}:{port}")[0], 403, "Host must be the listener")

    def test_the_wifi_login_page_keeps_its_warning(self):
        status = self.phone.enable(lambda access: _phone_handler_class(self.center, access), address="127.0.0.1",
                                   check_address=lambda _a: None, check_port=lambda _p: None, port=0)
        page = http(status["port"], "GET", "/", host=f"127.0.0.1:{status['port']}")[2].decode("utf-8")
        self.assertIn("Chỉ dùng trong Wi-Fi nhà", page)
        self.assertEqual(status["network"], "wifi")

    def wait_off(self, timeout=5.0):
        """Until the listener is closed and its PHONE_MODE_DISABLED event (written after the close) is stored."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.phone.enabled and any(
                    e["event_type"] == "PHONE_MODE_DISABLED" for e in self.store.events(None)):
                return
            time.sleep(0.05)
        self.fail("the phone mode did not turn itself off")

    def test_it_turns_itself_off_when_tailscale_stops_and_ignores_the_wifi(self):
        self.phone.enable(lambda access: _phone_handler_class(self.center, access), network="tailscale",
                          address="127.0.0.1", check_address=lambda _a: None, check_port=lambda _p: None,
                          port=0, check_seconds=0.1)
        time.sleep(0.5)
        self.assertTrue(self.phone.enabled, "the Wi-Fi lookup (which fails here) is not consulted")

        def stopped():
            raise ValueError(phone_access.TAILSCALE_DOWN_MESSAGE)
        self.phone._tailscale = stopped
        self.wait_off()
        status = self.pc_status()
        self.assertEqual(status["last_disabled_reason"], "tailscale_changed")
        self.assertEqual(status["last_disabled_reason_text"], phone_access.DISABLE_REASONS["tailscale_changed"])
        disabled = [e for e in self.store.events(None) if e["event_type"] == "PHONE_MODE_DISABLED"]
        self.assertEqual(disabled[0]["payload"]["reason"], "tailscale_changed")
        self.assertEqual(disabled[0]["payload"]["network"], "tailscale")

    def test_one_failed_tailscale_check_does_not_turn_it_off(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise ValueError(phone_access.TAILSCALE_DOWN_MESSAGE)  # e.g. the CLI timed out once
            return "127.0.0.1"

        self.phone._tailscale = flaky
        self.phone.enable(lambda access: _phone_handler_class(self.center, access), network="tailscale",
                          address="127.0.0.1", check_address=lambda _a: None, check_port=lambda _p: None,
                          port=0, check_seconds=0.1)
        deadline = time.monotonic() + 5
        while len(calls) < 4 and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertGreaterEqual(len(calls), 4)
        self.assertTrue(self.phone.enabled, "a single miss is forgiven")

    def test_the_wifi_watchdog_still_turns_off_at_the_first_change(self):
        calls = []

        def changed():
            calls.append(1)
            return "127.0.0.2"
        self.phone._lan = changed  # `address=` below skips the lookup at enable; the watchdog calls it
        self.phone.enable(lambda access: _phone_handler_class(self.center, access), address="127.0.0.1",
                          check_address=lambda _a: None, check_port=lambda _p: None, port=0, check_seconds=0.1)
        self.wait_off()
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.pc_status()["last_disabled_reason"], "address_changed")

    def test_the_last_reason_survives_a_restart(self):
        self.phone.enable(lambda access: _phone_handler_class(self.center, access), network="tailscale",
                          address="127.0.0.1", check_address=lambda _a: None, check_port=lambda _p: None,
                          port=0, check_seconds=0.1)
        self.phone._tailscale = lambda: "127.0.0.2"
        self.wait_off()
        restored = phone_access.PhoneAccess(lan=no_lookup, tailscale=no_lookup)
        restored.restore_history(self.store.events(None, limit=500))
        self.assertEqual(restored.status()["last_disabled_reason"], "tailscale_changed")


class FakeTailscale:
    """Stands in for the TailscaleManager: nothing is installed, started or signed in."""

    def __init__(self):
        self.started, self.status_calls, self.closed = [], 0, 0
        self.busy = False

    def status(self):
        self.status_calls += 1
        return {"installed": True, "backend": "Running", "ip": TAILNET_ADDRESS, "task": None}

    def start(self, action):
        if self.busy:
            raise ValueError(tailscale_manager.BUSY_MESSAGE)
        self.started.append(action)
        return {"installed": True, "task": {"action": action, "running": True}}

    def close(self):
        self.closed += 1

    def address(self):
        return "127.0.0.1"

    def same_account_device(self, _ip):
        return None


class TailscaleRouteTests(ListenerBase):
    """Cài đặt → Tailscale is PC only; "Gia hạn thêm 8 giờ" works from the phone over Tailscale only."""

    def setUp(self):
        super().setUp()
        self.fake = FakeTailscale()
        self.center.tailscale = self.fake

    def pc_call(self, method, path, payload=None, *, token="test-token"):
        headers, body = {}, b""
        if token:
            headers["X-BiliFlow-Token"] = token
        if method == "POST":
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload or {}).encode()
        status, _, data = http(self.pc_port, method, path, host=f"127.0.0.1:{self.pc_port}", headers=headers,
                               body=body)
        return status, json.loads(data)

    def phone_login(self, answer):
        """The phone types the code; returns its session (port, host, cookie)."""
        port, host = answer["port"], f"127.0.0.1:{answer['port']}"
        status, headers, _ = http(port, "POST", "/phone-login", host=host,
                                  headers={"Content-Type": "application/x-www-form-urlencoded"},
                                  body=f"code={answer['code']}".encode())
        self.assertEqual(status, 200)
        return port, host, headers["set-cookie"][0].split(";", 1)[0]

    def phone_call(self, session, method, path):
        port, host, cookie = session
        headers, body = {"Cookie": cookie, "X-BiliFlow-Token": "test-token", "Origin": f"http://{host}"}, b""
        if method == "POST":
            headers["Content-Type"] = "application/json"
            body = b"{}"
        status, _, data = http(port, method, path, host=host, headers=headers, body=body)
        return status, json.loads(data), data

    def enable_on_wifi(self):
        return self.phone.enable(lambda access: _phone_handler_class(self.center, access), address="127.0.0.1",
                                 check_address=lambda _a: None, check_port=lambda _p: None, port=0)

    def test_the_actions_match_everywhere(self):
        self.assertEqual(set(phone_access.TAILSCALE_ACTIONS), set(tailscale_manager.ACTIONS))
        source = Path(control_center.__file__).read_text(encoding="utf-8")
        self.assertIn(r'/api/tailscale/(' + "|".join(phone_access.TAILSCALE_ACTIONS) + ')", path)', source)

    def test_the_pc_reads_the_panel_and_starts_each_action(self):
        status, answer = self.pc_call("GET", "/api/tailscale")
        self.assertEqual((status, answer["backend"]), (200, "Running"))
        for action in phone_access.TAILSCALE_ACTIONS:
            status, answer = self.pc_call("POST", f"/api/tailscale/{action}")
            self.assertEqual((status, answer["task"]["action"]), (200, action))
        self.assertEqual(self.fake.started, list(phone_access.TAILSCALE_ACTIONS))

    def test_the_pc_needs_its_token_and_a_known_action(self):
        self.assertEqual(self.pc_call("POST", "/api/tailscale/install", token=None)[0], 403)
        self.assertEqual(self.pc_call("POST", "/api/tailscale/install", token="wrong")[0], 403)
        self.assertEqual(self.pc_call("POST", "/api/tailscale/uninstall")[0], 404)
        self.assertEqual(self.pc_call("POST", "/api/tailscale/install/x")[0], 404)
        self.assertEqual(self.fake.started, [])
        self.fake.busy = True
        status, answer = self.pc_call("POST", "/api/tailscale/up")
        self.assertEqual((status, answer["error"]), (400, tailscale_manager.BUSY_MESSAGE))

    def test_the_phone_can_neither_read_nor_drive_tailscale(self):
        session = self.phone_login(self.enable_over_http())
        status, answer, _ = self.phone_call(session, "GET", "/api/tailscale")
        self.assertEqual((status, answer["error"]), (403, phone_access.PC_ONLY_TAILSCALE))
        for action in phone_access.TAILSCALE_ACTIONS:
            with self.subTest(action=action):
                status, answer, _ = self.phone_call(session, "POST", f"/api/tailscale/{action}")
                self.assertEqual((status, answer["error"], answer["code"]),
                                 (403, phone_access.PC_ONLY_TAILSCALE, "pc_only"))
        self.assertEqual((self.fake.started, self.fake.status_calls), ([], 0))
        status, info, _ = self.phone_call(session, "GET", "/api/phone-mode")
        self.assertIn(phone_access.PC_ONLY_TAILSCALE, info["pc_only"])

    def test_the_phone_extends_the_limit_over_tailscale(self):
        answer = self.enable_over_http()
        session = self.phone_login(answer)
        before = self.pc_status()["expires_at"]
        time.sleep(0.05)
        status, body, raw = self.phone_call(session, "POST", "/api/phone-mode/extend")
        self.assertEqual(status, 200, body)
        self.assertEqual(set(body), {"remote", "enabled", "network", "expires_at", "added_seconds"},
                         "never the code or the link")
        self.assertGreater(body["added_seconds"], 0)
        self.assertEqual((body["remote"], body["enabled"], body["network"]), (True, True, "tailscale"))
        self.assertGreater(body["expires_at"], before)
        self.assertNotIn(answer["code"].encode(), raw)
        extended = [e for e in self.store.events(None) if e["event_type"] == "PHONE_MODE_EXTENDED"]
        self.assertEqual(extended[0]["payload"]["ip"], "127.0.0.1")
        self.assertIn("từ thiết bị 127.0.0.1", extended[0]["message"])
        status, info, _ = self.phone_call(session, "GET", "/api/phone-mode")
        self.assertEqual((status, info["expires_at"]), (200, body["expires_at"]))
        self.assertEqual(self.phone_call(session, "POST", "/api/phone-mode")[0], 403, "on/off stays on the PC")

    def test_on_the_home_wifi_only_the_pc_extends(self):
        answer = self.enable_on_wifi()
        session = self.phone_login(answer)
        before = self.phone.status()["expires_at"]
        status, body, _ = self.phone_call(session, "POST", "/api/phone-mode/extend")
        self.assertEqual((status, body["error"], body["code"]), (403, phone_access.EXTEND_TAILSCALE_ONLY, "pc_only"))
        self.assertEqual(self.phone.status()["expires_at"], before)
        self.assertFalse([e for e in self.store.events(None) if e["event_type"] == "PHONE_MODE_EXTENDED"])
        time.sleep(0.05)
        status, body = self.pc_call("POST", "/api/phone-mode/extend")
        self.assertEqual((status, body["remote"]), (200, False))
        self.assertGreater(body["expires_at"], before)

    def test_extending_needs_the_mode_on(self):
        status, body = self.pc_call("POST", "/api/phone-mode/extend")
        self.assertEqual(status, 400)
        self.assertIn("đang tắt", body["error"])
        self.assertEqual(self.pc_call("POST", "/api/phone-mode/extend", token=None)[0], 403)


class TailscaleWiringTests(unittest.TestCase):
    """_tailscale(center): one manager per Control Center, its events in the store, closed on stop."""

    def center(self, root):
        center = ControlCenter.__new__(ControlCenter)
        center.root = root
        return center

    def test_one_manager_per_center_with_its_events_in_the_store(self):
        with TemporaryDirectory(dir=ROOT / "temp") as folder:
            store = JobStore(Path(folder) / "state" / "control-center.sqlite3")
            try:
                center = self.center(Path(folder))
                center.store = store
                manager = _tailscale(center)
                self.assertIsInstance(manager, tailscale_manager.TailscaleManager)
                self.assertIs(_tailscale(center), manager)
                self.assertEqual(manager.root, Path(folder))
                manager.on_event("TAILSCALE_TASK", "Đã cài Tailscale", {"ok": True, "action": "install"})
                manager.on_event("TAILSCALE_TASK", "Không cài được", {"ok": False, "action": "install"})
                events = [e for e in store.events(None) if e["event_type"] == "TAILSCALE_TASK"]
                self.assertEqual([e["level"] for e in events], ["WARN", "INFO"])
            finally:
                store.close()

    def test_the_phone_mode_asks_the_manager_for_the_address(self):
        center = self.center(ROOT / "temp")
        center.tailscale = mock.Mock(address=mock.Mock(return_value=TAILNET_ADDRESS))
        self.assertEqual(_phone_access(center)._lookup("tailscale"), TAILNET_ADDRESS)
        center.tailscale.address.assert_called_once_with()

    def test_the_phone_mode_asks_the_manager_who_a_device_belongs_to(self):
        center = self.center(ROOT / "temp")
        center.tailscale = mock.Mock(same_account_device=mock.Mock(return_value="zphone"))
        self.assertEqual(_phone_access(center)._tailscale_device("100.64.0.9"), "zphone")
        center.tailscale.same_account_device.assert_called_once_with("100.64.0.9")

    def test_open_for_the_phone_outside_turns_the_phone_mode_on_over_tailscale(self):
        center = self.center(ROOT / "temp")
        center.phone = mock.Mock()
        center.phone.enable.return_value = {"enabled": True, "network": "tailscale"}
        self.assertEqual(_tailscale(center).phone_enable()["network"], "tailscale")
        (factory,), kwargs = center.phone.enable.call_args
        self.assertEqual(kwargs, {"network": "tailscale"})
        self.assertTrue(callable(factory))

    def test_stopping_the_control_center_closes_the_manager(self):
        with TemporaryDirectory(dir=ROOT / "temp") as folder:
            center = self.center(Path(folder))
            center._stopping = threading.Event()
            center.tailscale = mock.Mock()
            center.phone = mock.Mock()
            for name in ("stop_ai_audits", "stop_ai_login", "stop_downloads"):
                setattr(center, name, mock.Mock())
            center.watcher, center.scheduler, center.store, center.lock = (mock.Mock() for _ in range(4))
            center.server = None
            with mock.patch.object(control_center.source_cleanup, "wait_idle"):
                center.stop()
            center.tailscale.close.assert_called_once_with()
            center.phone.disable.assert_called_once_with("stopped")


if __name__ == "__main__":
    unittest.main()
