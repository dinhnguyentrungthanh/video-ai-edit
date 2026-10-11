"""GET /api/hardware-profile and its three POST routes (docs/HARDWARE_PROFILE.md 6.1; section 9 "E2, route").

On the frame of tests/test_phone_tailscale.py: a stub Control Center on 127.0.0.1 port 0 and the
phone listener on 127.0.0.1, never the real Control Center. A fake center.hardware answers where only
the route is under test; the real HardwareManager, built by control_center._hardware with a fake popen
and observe, answers where the 409s, the switch file and the events are. No PowerShell, measurement,
GPU or real mutex: every mutex name is Local\\BiliFlowTest-<uuid>, passed as a parameter.
"""
from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import sys
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from biliflow import control_center, hardware_detect, phone_access
from biliflow import hardware_manager as hm
from biliflow import hardware_schema as hs
from biliflow.control_center import ControlCenter, _handler_class, _hardware, _phone_access, _phone_handler_class
from biliflow.export_guards import ActionConflict
from biliflow.job_store import JobStore
from biliflow.review_evidence import ReviewFrameCache
from biliflow.scheduler import JobScheduler
from tests.hardware_fixtures import AMPERE, TempRootCase, observation, profile_document
from tests.test_dashboard_v2_phone_hardening import raw
from tests.test_hardware_manager import FakePopen, fresh_mutex_name, wait_for
from tests.test_phone_tailscale import http, no_lookup

SRC = Path(__file__).resolve().parents[1] / "src"
POSTS = tuple(f"/api/hardware-profile/{action}" for action in phone_access.HARDWARE_ACTIONS)
TOKEN_REFUSAL = "Phiên Control Center không hợp lệ"


class FakeHardware:
    """Stands in for the HardwareManager: records every call, never measures or writes."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.conflict: tuple[str, str] | None = None

    def status(self):
        self.calls.append(("status",))
        return {"status": "ready", "task": None}

    def start(self, action):
        self.calls.append(("start", action))
        if self.conflict is not None:
            raise ActionConflict(*self.conflict)
        return {"status": "ready", "task": {"running": True}}

    def set_cpu_only(self, enabled):
        self.calls.append(("cpu-only", enabled))
        return {"cpu_only": {"value": enabled, "valid": True}}

    def set_untested_fast(self, enabled):
        self.calls.append(("untested-fast", enabled))
        return {"untested_fast": {"value": enabled, "gpu": None, "applies": False}}

    def close(self):
        self.calls.append(("close",))


class Exploding:
    """Any attribute use fails the test: /api/status must never reach the hardware manager."""

    def __getattr__(self, name):
        raise AssertionError(f"/api/status used hardware.{name}")


class RouteBase(TempRootCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = JobStore(self.root / "state" / "control-center.sqlite3")
        self.addCleanup(self.store.close)
        self.job = self.add_job("1", "WAITING_REVIEW")
        center = ControlCenter.__new__(ControlCenter)
        center.root, center.host, center.token, center.store = self.root, "127.0.0.1", "test-token", self.store
        center.frame_cache = ReviewFrameCache(self.root, self.root / "missing-ffmpeg.exe")
        center._stopping = threading.Event()
        center.scheduler = JobScheduler(self.root, self.store)  # never started
        center.recovered = 0
        center.start_ai_audit = mock.Mock(return_value=None)
        self.fake = FakeHardware()
        center.hardware = self.fake
        self.center = center
        self.pc = ThreadingHTTPServer(("127.0.0.1", 0), _handler_class(center))
        self.pc.daemon_threads = True
        self.pc_port = self.pc.server_address[1]
        threading.Thread(target=self.pc.serve_forever, daemon=True).start()
        self.addCleanup(self.pc.server_close)
        self.addCleanup(self.pc.shutdown)
        self.phone = _phone_access(center)
        self.phone._lan = no_lookup  # never the real Wi-Fi
        self.phone._tailscale = no_lookup  # never the real CLI
        self.addCleanup(self.phone.disable)

    def add_job(self, key: str, state: str) -> dict:
        source = self.root / "input" / f"clip-{key}.mp4"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"video" * 100)
        return self.store.upsert_job(job_key=f"job-{key}", source_path=source, source_sha256=key * 64,
                                     source_size_bytes=500, source_mtime_ns=source.stat().st_mtime_ns, state=state)

    def pc_call(self, method, path, payload=None, *, token="test-token", body=None):
        headers = {"X-BiliFlow-Token": token} if token else {}
        if method == "POST":
            headers["Content-Type"] = "application/json"
            body = body if body is not None else json.dumps({} if payload is None else payload).encode()
        status, _, data = http(self.pc_port, method, path, host=f"127.0.0.1:{self.pc_port}", headers=headers,
                               body=body or b"")
        return status, json.loads(data)

    def phone_session(self):
        answer = self.phone.enable(lambda access: _phone_handler_class(self.center, access), address="127.0.0.1",
                                   check_address=lambda _a: None, check_port=lambda _p: None, port=0)
        port, host = answer["port"], f"127.0.0.1:{answer['port']}"
        status, headers, _ = http(port, "POST", "/phone-login", host=host,
                                  headers={"Content-Type": "application/x-www-form-urlencoded"},
                                  body=f"code={answer['code']}".encode())
        self.assertEqual(status, 200)
        return port, host, headers["set-cookie"][0].split(";", 1)[0]

    def phone_call(self, session, method, path, payload=None):
        port, host, cookie = session
        headers, body = {"Cookie": cookie, "X-BiliFlow-Token": "test-token", "Origin": f"http://{host}"}, b""
        if method == "POST":
            headers["Content-Type"] = "application/json"
            body = json.dumps({} if payload is None else payload).encode()
        status, _, data = http(port, method, path, host=host, headers=headers, body=body)
        return status, json.loads(data)


class RouteTextTests(unittest.TestCase):
    def test_the_routes_are_written_as_the_route_tests_read_them(self):
        source = Path(control_center.__file__).read_text(encoding="utf-8")
        get_block = source[source.index("def do_GET"):source.index("def do_POST")]
        post_block = source[source.index("def do_POST"):source.index("return Handler")]
        self.assertIn('elif path == "/api/hardware-profile":', get_block)
        self.assertIn(r're.fullmatch(r"/api/hardware-profile/(' + "|".join(phone_access.HARDWARE_ACTIONS)
                      + ')", path)', post_block)
        self.assertNotIn('path.startswith("/api/hardware-profile', source, "GET and POST paths never overlap")
        self.assertIsNone(re.search(r"^from biliflow import .*hardware", source, re.MULTILINE))

    def test_the_three_posts_are_pc_only_with_their_own_reason(self):
        self.assertEqual(phone_access.HARDWARE_ACTIONS, ("remeasure", "cpu-only", "untested-fast"))
        for path in POSTS:
            with self.subTest(path=path):
                self.assertEqual(phone_access.post_policy(path), (False, phone_access.PC_ONLY_HARDWARE, True))
        self.assertTrue(phone_access.PC_ONLY_HARDWARE.startswith("Chỉ làm trên PC"))
        self.assertNotIn(phone_access.PC_ONLY_HARDWARE, (phone_access.PC_ONLY_SOURCE, phone_access.PC_ONLY_TAILSCALE))
        self.assertNotIn("/api/hardware-profile", phone_access.PC_ONLY_POSTS, "no POST has the GET path")

    def test_importing_the_control_center_loads_no_hardware_module(self):
        code = ("import sys\nimport biliflow.control_center\n"
                "print(sorted(name for name in sys.modules if name.startswith('biliflow.hardware_')))\n")
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, (str(SRC), os.environ.get("PYTHONPATH"))))}
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                                check=True, env=env, stdin=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.stdout.split(), ["[]"])

    def test_the_live_page_may_load_its_hardware_module(self):
        self.assertIn("hardware-view.js", control_center.DASHBOARD_V2_FILES)

    def test_the_manager_is_closed_next_to_tailscale_in_serve_and_stop(self):
        source = Path(control_center.__file__).read_text(encoding="utf-8")
        for name in ("    def serve(self) -> None:", "    def stop(self, *, immediate: bool = False) -> None:"):
            with self.subTest(method=name.strip()):
                block = source[source.index(name):]
                block = block[:block.index("\n    def ", 10)]
                tailscale = block.index("self.tailscale.close()")
                hardware = block.index('if getattr(self, "hardware", None) is not None:')
                self.assertLess(tailscale, hardware)
                self.assertLess(hardware, block.index("self.stop_ai_audits()"))


class FakeManagerRouteTests(RouteBase):
    def test_the_pc_reads_the_profile(self):
        status, answer = self.pc_call("GET", "/api/hardware-profile")
        self.assertEqual((status, answer), (200, {"status": "ready", "task": None}))
        self.assertEqual(self.fake.calls, [("status",)])

    def test_the_phone_can_neither_read_nor_change_the_hardware_profile(self):
        session = self.phone_session()
        status, answer = self.phone_call(session, "GET", "/api/hardware-profile")
        self.assertEqual((status, answer), (403, {"error": phone_access.PC_ONLY_HARDWARE, "code": "pc_only"}))
        for path in POSTS:
            with self.subTest(path=path):
                status, answer = self.phone_call(session, "POST", path, {"enabled": True})
                self.assertEqual((status, answer), (403, {"error": phone_access.PC_ONLY_HARDWARE, "code": "pc_only"}))
        self.assertEqual(self.fake.calls, [])
        status, info = self.phone_call(session, "GET", "/api/phone-mode")
        self.assertIn(phone_access.PC_ONLY_HARDWARE, info["pc_only"])

    def test_a_phone_post_is_refused_before_its_body_arrives(self):
        port, host, cookie = self.phone_session()
        for path in POSTS:
            with self.subTest(path=path):
                head = (f"POST {path} HTTP/1.1\r\nHost: {host}\r\nCookie: {cookie}\r\nX-BiliFlow-Token: test-token\r\n"
                        "Content-Type: application/json\r\nContent-Length: 50\r\n\r\n").encode()
                started = time.monotonic()
                reply = raw(port, head, wait=8)  # the body never comes
                self.assertTrue(reply.startswith(b"HTTP/1.0 403"), reply[:80])
                self.assertIn(b'"pc_only"', reply)
                self.assertLess(time.monotonic() - started, 3.0, "no wait for the missing body")
        self.assertEqual(self.fake.calls, [])

    def test_a_post_needs_the_session_token(self):
        for path in POSTS:
            for token in (None, "wrong"):
                with self.subTest(path=path, token=token):
                    status, answer = self.pc_call("POST", path, {"enabled": True}, token=token)
                    self.assertEqual((status, answer), (403, {"error": TOKEN_REFUSAL}))
        self.assertEqual(self.fake.calls, [])

    def test_a_switch_body_must_be_exactly_one_enabled_bool(self):
        bad = ({}, {"enabled": 1}, {"enabled": 0}, {"enabled": "true"}, {"enabled": None}, {"enabled": [True]},
               {"enabled": True, "x": 1}, {"Enabled": True})
        for path in POSTS[1:]:
            for payload in bad:
                with self.subTest(path=path, payload=payload):
                    status, answer = self.pc_call("POST", path, payload)
                    self.assertEqual(status, 400, answer)
            for body in (b"[]", b"true", b"{bad"):
                with self.subTest(path=path, body=body):
                    self.assertEqual(self.pc_call("POST", path, body=body)[0], 400)
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.pc_call("POST", POSTS[1], {"enabled": True})[0], 200)
        self.assertEqual(self.pc_call("POST", POSTS[2], {"enabled": False})[0], 200)
        self.assertEqual(self.fake.calls, [("cpu-only", True), ("untested-fast", False)])

    def test_the_body_of_remeasure_is_ignored(self):
        status, answer = self.pc_call("POST", POSTS[0], {"enabled": "anything", "x": [1]})
        self.assertEqual((status, answer["task"]), (200, {"running": True}))
        self.assertEqual(self.fake.calls, [("start", "remeasure")])

    def test_a_conflict_is_a_409_with_its_code(self):
        for code in ("busy", "not_paused", "not_idle"):
            with self.subTest(code=code):
                self.fake.conflict = (code, f"Văn bản {code}")
                status, answer = self.pc_call("POST", POSTS[0])
                self.assertEqual((status, answer), (409, {"error": f"Văn bản {code}", "code": code}))

    def test_unknown_hardware_paths_are_not_found_on_the_pc(self):
        for method, path in (("POST", "/api/hardware-profile/measure"), ("POST", "/api/hardware-profile/remeasure/x"),
                             ("POST", "/api/hardware-profile"), ("GET", "/api/hardware-profile/remeasure")):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.pc_call(method, path)[0], 404)
        self.assertEqual(self.fake.calls, [])

    def test_api_status_never_touches_the_hardware_manager_or_observe(self):
        self.center.hardware = Exploding()
        refuse = mock.Mock(side_effect=AssertionError("/api/status must not reach hardware code"))
        with mock.patch.object(control_center, "_hardware", refuse), \
                mock.patch.object(hardware_detect, "observe", refuse), mock.patch.object(hm, "_observe", refuse):
            status, answer = self.pc_call("GET", "/api/status")
        self.assertEqual(status, 200, answer)
        refuse.assert_not_called()
        self.assertNotIn("hardware", answer)


class ManagerRouteTests(RouteBase):
    """The real manager behind the routes, built by control_center._hardware with fakes injected."""

    def setUp(self) -> None:
        super().setUp()
        self.popen = FakePopen()
        self.observation = observation()
        self.center.hardware = None
        build = functools.partial(
            hm.HardwareManager, popen=self.popen, observe=lambda _root, *, ffmpeg_exe: self.observation,
            kill_tree=mock.Mock(side_effect=AssertionError("no tree is killed here")), mutex_name=fresh_mutex_name(),
            python=str(self.base / "py" / "python.exe"), script=self.base / "code" / "scripts" / "hardware-measure.ps1",
            poll_seconds=0.01, observe_seconds=0)
        with mock.patch.object(hm, "HardwareManager", build):
            self.manager = _hardware(self.center)
        self.assertIs(self.center.hardware, self.manager)
        self.addCleanup(self.end_measurements)
        self.addCleanup(self.manager.close)

    def end_measurements(self) -> None:
        for process in self.popen.processes:
            if process.code is None:
                process.code = 1
        wait_for(lambda: self.manager._run is None)

    def events(self, kind: str) -> list[dict]:
        return [event for event in self.store.events(None) if event["event_type"] == kind]

    def test_remeasure_is_refused_until_the_queue_is_paused_and_idle_then_runs_once(self):
        status, answer = self.pc_call("POST", POSTS[0])
        self.assertEqual((status, answer), (409, {"error": hm.NOT_PAUSED_MESSAGE, "code": "not_paused"}))
        self.store.set_setting("scheduler_paused", True)
        rendering = self.add_job("2", "RENDERING")
        status, answer = self.pc_call("POST", POSTS[0])
        self.assertEqual((status, answer["code"]), (409, "not_idle"))
        self.assertEqual(self.popen.calls, [])
        self.store.update_job(rendering["id"], state="COMPLETED")
        status, answer = self.pc_call("POST", POSTS[0])
        self.assertEqual((status, answer["task"]["running"], answer["can_remeasure"]["reason"]), (200, True, "busy"))
        status, answer = self.pc_call("POST", POSTS[0])
        self.assertEqual((status, answer["code"]), (409, "busy"))
        self.assertEqual(len(self.popen.calls), 1)
        self.assertEqual([event["level"] for event in self.events("HARDWARE_MEASURE_STARTED")], ["INFO"])
        self.popen.processes[0].code = 75
        self.assertTrue(wait_for(lambda: self.events("HARDWARE_MEASURE_FINISHED")))
        self.assertEqual(self.events("HARDWARE_MEASURE_FINISHED")[0]["level"], "WARN")

    def test_cpu_only_writes_the_switch_and_an_event(self):
        self.write("main", profile_document())
        status, answer = self.pc_call("POST", POSTS[1], {"enabled": True})
        self.assertEqual((status, answer["cpu_only"]), (200, {"value": True, "valid": True}))
        self.assertIs(hs.read_override(self.root).cpu_only, True)
        events = self.events("HARDWARE_CPU_ONLY_CHANGED")
        self.assertEqual((events[0]["level"], events[0]["payload"]["cpu_only"]), ("INFO", True))
        status, answer = self.pc_call("POST", POSTS[1], {"enabled": False})
        self.assertEqual((status, answer["cpu_only"]["value"], answer["status"]), (200, False, "ready"))

    def test_the_untested_switch_is_a_409_on_a_gated_card(self):
        self.write("main", profile_document())
        status, answer = self.pc_call("POST", POSTS[2], {"enabled": True})
        self.assertEqual((status, answer["code"]), (409, "not_untested"))
        self.assertFalse(hs.override_path(self.root).exists())
        self.assertEqual(self.events("HARDWARE_UNTESTED_FAST_CHANGED"), [])
        status, answer = self.pc_call("POST", POSTS[2], {"enabled": False})
        self.assertEqual((status, answer["untested_fast"]["value"]), (200, False), "turning it off always works")

    def test_the_untested_switch_binds_the_card_and_the_get_shows_it(self):
        self.write("main", profile_document("cuda", gpus=(AMPERE,)))
        self.observation = observation((AMPERE,))
        status, before = self.pc_call("GET", "/api/hardware-profile")
        self.assertEqual((status, before["status"], before["row"]["status"]), (200, "ready_untested", "untested"))
        self.assertEqual({name: option["tf32"] for name, option in before["families"].items()},
                         {"fast": "off", "standard_ocr8": "off", "standard": "off"})
        self.assertEqual(before["untested_fast"], {"value": False, "gpu": None, "applies": False})
        status, answer = self.pc_call("POST", POSTS[2], {"enabled": True})
        self.assertEqual((status, answer["untested_fast"]["value"], answer["untested_fast"]["applies"]),
                         (200, True, True))
        self.assertEqual((answer["untested_fast"]["gpu"]["name"], answer["untested_fast"]["gpu"]["compute_capability"]),
                         (AMPERE.name, "8.6"))
        self.assertEqual(answer["families"]["fast"]["family"], "fast")
        events = self.events("HARDWARE_UNTESTED_FAST_CHANGED")
        self.assertEqual((events[0]["level"], events[0]["payload"]["gpu_name"]), ("INFO", AMPERE.name))
        self.assertEqual(set(answer) >= {"status", "applies_to_jobs", "row", "families", "reasons", "warnings",
                                         "cpu_only", "untested_fast", "profile", "detection", "task",
                                         "can_remeasure"}, True)


class HardwareWiringTests(TempRootCase):
    """_hardware(center): one manager per Control Center, its events in the store, closed on stop."""

    def test_one_manager_per_center_with_its_queue_and_events(self):
        store = JobStore(self.root / "state" / "control-center.sqlite3")
        self.addCleanup(store.close)
        center = ControlCenter.__new__(ControlCenter)
        center.root, center.store = self.root, store
        center.scheduler = JobScheduler(self.root, store)  # never started
        with mock.patch.object(hm, "MEASURE_MUTEX", fresh_mutex_name()):
            manager = _hardware(center)
        self.addCleanup(manager.close)
        self.assertIsInstance(manager, hm.HardwareManager)
        self.assertIs(_hardware(center), manager)
        self.assertEqual(manager.root, self.root)
        self.assertEqual(manager.can_remeasure()["reason"], "not_paused")
        store.set_setting("scheduler_paused", True)
        self.assertEqual(manager.can_remeasure(), {"ok": True, "reason": None, "text": None})
        manager._event("HARDWARE_MEASURE_FINISHED", "Lỗi", ok=False)
        manager._event("HARDWARE_CPU_ONLY_CHANGED", "Chỉ dùng CPU: bật", cpu_only=True)
        levels = {event["event_type"]: event["level"] for event in store.events(None)}
        self.assertEqual((levels["HARDWARE_MEASURE_FINISHED"], levels["HARDWARE_CPU_ONLY_CHANGED"]), ("WARN", "INFO"))

    def test_stopping_the_control_center_closes_the_manager(self):
        center = ControlCenter.__new__(ControlCenter)
        center.root = self.root
        center._stopping = threading.Event()
        center.hardware = mock.Mock()
        center.phone = mock.Mock()
        for name in ("stop_ai_audits", "stop_ai_login", "stop_downloads"):
            setattr(center, name, mock.Mock())
        center.watcher, center.scheduler, center.store, center.lock = (mock.Mock() for _ in range(4))
        center.server = None
        with mock.patch.object(control_center.source_cleanup, "wait_idle"):
            center.stop()
        center.hardware.close.assert_called_once_with()
        center.scheduler.shutdown.assert_called_once()

    def test_a_center_without_a_manager_stops_without_creating_one(self):
        center = ControlCenter.__new__(ControlCenter)
        center.root = self.root
        center._stopping = threading.Event()
        for name in ("stop_ai_audits", "stop_ai_login", "stop_downloads"):
            setattr(center, name, mock.Mock())
        center.watcher, center.scheduler, center.store, center.lock = (mock.Mock() for _ in range(4))
        center.server = None
        with mock.patch.object(control_center.source_cleanup, "wait_idle"):
            center.stop()
        self.assertIsNone(getattr(center, "hardware", None))

    def test_a_manager_made_after_stop_is_born_closed(self):
        center = ControlCenter.__new__(ControlCenter)
        center.root, center.store, center.scheduler = self.root, None, None
        center._stopping = threading.Event()
        center._stopping.set()  # stop() already closed the managers it saw
        with mock.patch.object(hm, "MEASURE_MUTEX", fresh_mutex_name()):
            manager = _hardware(center)
        self.addCleanup(manager.close)
        self.assertTrue(manager._closed.is_set())
        with self.assertRaises(ActionConflict) as caught:
            manager.start("remeasure")
        self.assertEqual(caught.exception.code, "busy")


if __name__ == "__main__":
    unittest.main()
