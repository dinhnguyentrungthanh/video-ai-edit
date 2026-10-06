"""GET /dashboard-v2/ on the real Control Center handler (port 0, temporary root, stub center).

D1 whitelist, D2 "/" opens V2 and the classic pages are unchanged behind CLASSIC_DASHBOARD,
D3 CSP, D4 POST still needs the token.
No scheduler thread, watcher, video or real project data.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from biliflow import control_center
from biliflow.control_center import (
    DASHBOARD_V2_CSP,
    DASHBOARD_V2_DIR,
    DASHBOARD_V2_FILES,
    ControlCenter,
    _dashboard_html,
    _handler_class,
)
from biliflow.job_store import JobStore
from biliflow.review_evidence import ReviewFrameCache
from biliflow.scheduler import JobScheduler

ROOT = Path(__file__).resolve().parents[1]
CLASSIC = json.loads((ROOT / "tests" / "fixtures" / "dashboard_v2_classic_pages.json").read_text(encoding="utf-8"))


class DashboardV2RouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.store = JobStore(self.root / "state" / "control-center.sqlite3")
        center = ControlCenter.__new__(ControlCenter)
        center.root = self.root
        center.host = "127.0.0.1"
        center.token = "test-token"
        center.store = self.store
        center.frame_cache = ReviewFrameCache(self.root, self.root / "missing-ffmpeg.exe")
        center._stopping = threading.Event()
        center.scheduler = JobScheduler(self.root, self.store)  # never started
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_class(center))
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.store.close()
        self.temp.cleanup()

    def request(self, path, *, method="GET", headers=None, body=None, host=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request(method, path, body=body, headers={"Host": host or f"127.0.0.1:{self.port}", **(headers or {})})
            response = connection.getresponse()
            return response.status, response.getheaders(), response.read()
        finally:
            connection.close()

    @staticmethod
    def header(headers, name):
        return [value for key, value in headers if key.lower() == name.lower()]

    # D1 ---------------------------------------------------------------
    def test_the_live_page_and_every_whitelisted_asset_are_served(self):
        status, headers, body = self.request("/dashboard-v2/")
        self.assertEqual(status, 200)
        self.assertEqual(body, (DASHBOARD_V2_DIR / "live.html").read_bytes())
        self.assertIn('data-mode="live"', body.decode("utf-8"))
        self.assertEqual(self.header(headers, "Content-Type"), ["text/html; charset=utf-8"])
        for name in sorted(DASHBOARD_V2_FILES):
            with self.subTest(asset=name):
                status, headers, body = self.request("/dashboard-v2/" + name)
                self.assertEqual(status, 200)
                self.assertEqual(body, (DASHBOARD_V2_DIR / name).read_bytes())
                self.assertEqual(self.header(headers, "X-Content-Type-Options"), ["nosniff"])

    def test_every_asset_the_live_page_references_is_whitelisted(self):
        html = (DASHBOARD_V2_DIR / "live.html").read_text(encoding="utf-8")
        import re
        referenced = set(re.findall(r'(?:src|href)="([^"#:]+)"', html))
        self.assertTrue(referenced)
        self.assertEqual(referenced - DASHBOARD_V2_FILES, set())
        posters = {f"assets/poster-{c}.svg" for c in ("amber", "blue", "rose", "sage", "violet")}
        self.assertLessEqual(posters, DASHBOARD_V2_FILES)

    def test_without_the_slash_redirects_so_relative_assets_resolve(self):
        status, headers, body = self.request("/dashboard-v2")
        self.assertEqual(status, 301)
        self.assertEqual(self.header(headers, "Location"), ["/dashboard-v2/"])
        self.assertEqual(body, b"")

    def test_anything_outside_the_whitelist_is_404_and_never_listed(self):
        for path in (
            "/dashboard-v2/index.html", "/dashboard-v2/mock-data.js", "/dashboard-v2/demo-store.js",
            "/dashboard-v2/serve.py", "/dashboard-v2/verify.cjs", "/dashboard-v2/README.md",
            "/dashboard-v2/assets/", "/dashboard-v2/assets", "/dashboard-v2/live.html/",
            "/dashboard-v2/../src/biliflow/control_center.py", "/dashboard-v2/%2e%2e/pyproject.toml",
            "/dashboard-v2/assets/../app.js", "/dashboard-v2//etc/passwd", "/dashboard-v2/APP.JS",
            "/dashboard-v2/app.js%00", "/dashboard-v2/state/control-center.sqlite3",
            "/dashboard-v2-x/app.js", "/dashboard-v2x",
        ):
            with self.subTest(path=path):
                status, headers, body = self.request(path)
                self.assertEqual(status, 404, body[:120])
                self.assertNotIn(b"<html", body.lower())

    def test_post_and_foreign_host_are_refused(self):
        status, _, _ = self.request("/dashboard-v2/", host="evil.example")
        self.assertEqual(status, 403)
        status, _, body = self.request("/dashboard-v2/", method="POST", body=b"{}",
                                       headers={"Content-Type": "application/json", "Content-Length": "2"})
        self.assertEqual(status, 403, body)

    # D2 ---------------------------------------------------------------
    def test_the_classic_dashboard_is_off_by_default(self):
        # The user's choice (2026-10-06): V2 is the dashboard. The only test that pins it; a rollback changes it too.
        self.assertFalse(control_center.CLASSIC_DASHBOARD)

    def test_root_opens_dashboard_v2_while_the_classic_dashboard_is_off(self):
        with mock.patch.object(control_center, "CLASSIC_DASHBOARD", False):
            status, headers, body = self.request("/")
            refused = self.request("/", host="evil.example")[0]
        self.assertEqual((status, body), (303, b""))
        self.assertEqual(self.header(headers, "Location"), ["/dashboard-v2/"])
        self.assertEqual(self.header(headers, "Cache-Control"), ["no-store"], "not cached: the switch can be undone")
        self.assertEqual(self.header(headers, "Content-Security-Policy"), ["frame-ancestors 'self'"])
        self.assertEqual(refused, 403)

    def test_classic_dashboard_and_review_page_are_byte_identical_to_before_v2(self):
        # The classic page stays in the code for a rollback: CLASSIC_DASHBOARD = True serves it at "/" again.
        with mock.patch.object(control_center, "CLASSIC_DASHBOARD", True):
            status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertEqual(body, _dashboard_html().encode())
        self.assertEqual((len(body), hashlib.sha256(body).hexdigest()),
                         (CLASSIC["dashboard_bytes"], CLASSIC["dashboard_sha256"]))
        self.assertEqual(self.header(headers, "Content-Security-Policy"), ["frame-ancestors 'self'"])
        status, headers, body = self.request("/review/1")
        self.assertEqual(status, 200)
        self.assertEqual((len(body), hashlib.sha256(body).hexdigest()),
                         (CLASSIC["review_bytes"], CLASSIC["review_job_1_token_test_sha256"]))
        self.assertEqual(self.header(headers, "Content-Security-Policy"), ["frame-ancestors 'self'"])

    # D3 ---------------------------------------------------------------
    def test_live_page_csp_allows_same_origin_connect_and_keeps_anti_framing(self):
        _, headers, _ = self.request("/dashboard-v2/")
        policies = self.header(headers, "Content-Security-Policy")
        # The shared frame-ancestors header of every response stays; the page adds its own policy.
        self.assertIn("frame-ancestors 'self'", policies)
        self.assertIn(DASHBOARD_V2_CSP, policies)
        self.assertIn("connect-src 'self'", DASHBOARD_V2_CSP)
        self.assertNotIn("connect-src 'none'", DASHBOARD_V2_CSP)
        self.assertNotIn("*", DASHBOARD_V2_CSP)
        self.assertIn("frame-ancestors 'self'", DASHBOARD_V2_CSP)
        self.assertEqual(self.header(headers, "X-Frame-Options"), ["SAMEORIGIN"])
        self.assertEqual(self.header(headers, "Cache-Control"), ["no-store"])
        meta = (DASHBOARD_V2_DIR / "live.html").read_text(encoding="utf-8")
        self.assertIn("connect-src 'self'", meta)
        _, asset_headers, _ = self.request("/dashboard-v2/app.js")
        self.assertEqual(self.header(asset_headers, "X-Frame-Options"), ["SAMEORIGIN"])

    # D4 ---------------------------------------------------------------
    def test_writes_still_need_the_session_token(self):
        body = json.dumps({"paused": True}).encode()
        status, _, payload = self.request("/api/scheduler", method="POST", body=body,
                                          headers={"Content-Type": "application/json", "Content-Length": str(len(body))})
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(payload)["error"], "Phiên Control Center không hợp lệ")
        self.assertIsNone(self.store.setting("scheduler_paused"))
        status, _, payload = self.request("/api/session")
        self.assertEqual((status, json.loads(payload)), (200, {"token": "test-token"}))
        status, _, payload = self.request("/api/scheduler", method="POST", body=body, headers={
            "Content-Type": "application/json", "Content-Length": str(len(body)), "X-BiliFlow-Token": "test-token"})
        self.assertEqual((status, json.loads(payload)), (200, {"paused": True}))

    def test_the_route_reads_nothing_from_the_project_root(self):
        before = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*"))
        for path in ("/dashboard-v2/", "/dashboard-v2/app.js", "/dashboard-v2/nope.js"):
            self.request(path)
        after = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*"))
        self.assertEqual(before, after)
        self.assertNotEqual(DASHBOARD_V2_DIR.parent, self.root)


if __name__ == "__main__":
    unittest.main()
