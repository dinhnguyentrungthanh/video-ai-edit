"""POST /api/scheduler/parallel-export and the lane fields of /api/status (perf plan A1b).

On the frame of tests/test_hardware_routes.py: a stub Control Center on 127.0.0.1 port 0 and the phone
listener on 127.0.0.1, never the real Control Center. The scheduler is never started.
"""
from __future__ import annotations

from biliflow import phone_access
from biliflow.scheduler import EXPORT_LANE, MAIN_LANE
from tests.test_hardware_routes import RouteBase

PATH = "/api/scheduler/parallel-export"


class ParallelExportRouteTests(RouteBase):
    def test_the_pc_switches_the_mode_and_status_shows_it(self):
        status, answer = self.pc_call("GET", "/api/status")
        self.assertEqual(status, 200, answer)
        self.assertEqual((answer["parallel_export"], answer["active"], answer["active_lanes"]), ("auto", None, []))
        for mode in ("off", "auto", "off"):
            with self.subTest(mode=mode):
                self.assertEqual(self.pc_call("POST", PATH, {"mode": mode}), (200, {"mode": mode}))
                self.assertEqual(self.store.setting("parallel_export"), mode)
                status, answer = self.pc_call("GET", "/api/status")
                self.assertEqual((status, answer["parallel_export"]), (200, mode))

    def test_status_lists_every_lane_and_keeps_the_old_active_field(self):
        scheduler = self.center.scheduler
        scan = type("Process", (), {"pid": 4321})()
        render = type("Process", (), {"pid": 8765})()
        scheduler._active[MAIN_LANE], scheduler._process[MAIN_LANE] = (7, "scan_text"), scan
        scheduler._active[EXPORT_LANE], scheduler._process[EXPORT_LANE] = (8, "render"), render
        try:
            status, answer = self.pc_call("GET", "/api/status")
        finally:
            scheduler._active.clear()
            scheduler._process.clear()
        self.assertEqual(status, 200, answer)
        self.assertEqual(answer["active"], {"job_id": 7, "stage": "scan_text", "pid": 4321})
        self.assertEqual(answer["active_lanes"], [
            {"lane": "main", "job_id": 7, "stage": "scan_text", "pid": 4321},
            {"lane": "export", "job_id": 8, "stage": "render", "pid": 8765},
        ])

    def test_a_wrong_body_changes_nothing(self):
        for body in ({}, {"mode": "on"}, {"mode": None}, {"mode": "off", "extra": 1}, {"paused": True}):
            with self.subTest(body=body):
                status, answer = self.pc_call("POST", PATH, body)
                self.assertEqual(status, 400, answer)
                self.assertIn("error", answer)
                self.assertIsNone(self.store.setting("parallel_export"))

    def test_a_post_needs_the_session_token(self):
        for token in (None, "wrong"):
            with self.subTest(token=token):
                status, answer = self.pc_call("POST", PATH, {"mode": "off"}, token=token)
                self.assertEqual((status, answer), (403, {"error": "Phiên Control Center không hợp lệ"}))
        self.assertIsNone(self.store.setting("parallel_export"))

    def test_the_phone_cannot_switch_it(self):
        refusal = phone_access.PC_ONLY_POSTS[PATH]
        self.assertTrue(refusal.startswith("Chỉ làm trên PC"))
        session = self.phone_session()
        status, answer = self.phone_call(session, "POST", PATH, {"mode": "off"})
        self.assertEqual((status, answer), (403, {"error": refusal, "code": "pc_only"}))
        self.assertIsNone(self.store.setting("parallel_export"))
        # The phone still sees the mode on its status.
        status, answer = self.phone_call(session, "GET", "/api/status")
        self.assertEqual((status, answer["parallel_export"]), (200, "auto"))
