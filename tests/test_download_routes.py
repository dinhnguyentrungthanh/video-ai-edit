"""The "Tải video" routes through the Control Center handler (video download plan, D2).

Real HTTP on port 0 against a ``ControlCenter.__new__`` stub on a temporary root:
the download service uses the fake yt-dlp of tests/fake_yt_dlp.py and its worker
loop is never started, so nothing downloads unless a test runs the queue itself.
Nothing here reaches the project's Control Center, state, input or the network.
"""
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from biliflow import control_center, phone_access
from biliflow.control_center import ControlCenter, _handler_class, _phone_access, _phone_handler_class
from biliflow.download_api import DownloadService
from biliflow.job_store import JobStore
from biliflow.review_evidence import ReviewFrameCache
from biliflow.scheduler import JobScheduler
from tests.test_dashboard_v2_phone_hardening import FAKE_LAN, http, raw
from tests.test_download_worker import CLIP, WorkerCase, video

TOKEN = "test-token"


class RouteCase(WorkerCase):
    def setUp(self):
        super().setUp()
        self.jobs = JobStore(self.root / "state" / "control-center.sqlite3")
        center = ControlCenter.__new__(ControlCenter)
        center.root, center.host, center.token, center.store = self.root, "127.0.0.1", TOKEN, self.jobs
        center.frame_cache = ReviewFrameCache(self.root, self.root / "missing-ffmpeg.exe")
        center._stopping = threading.Event()
        center.scheduler = JobScheduler(self.root, self.jobs)  # never started
        center.recovered = 0
        center.downloads = DownloadService(self.root, cleanable=lambda: (0, 0), bin_reader=None,
                                           store=self.store, worker=self.worker)
        self.center = center
        self.pc = ThreadingHTTPServer(("127.0.0.1", 0), _handler_class(center))
        self.pc.daemon_threads = True
        self.pc_port = self.pc.server_address[1]
        self.pc_host = f"127.0.0.1:{self.pc_port}"
        threading.Thread(target=self.pc.serve_forever, daemon=True).start()
        self.phone = _phone_access(center)
        self.phone._lan = lambda: FAKE_LAN  # never the real Wi-Fi

    def tearDown(self):
        self.phone.disable()
        self.pc.shutdown()
        self.pc.server_close()
        self.jobs.close()
        super().tearDown()

    def get(self, path, *, host=None):
        status, _, payload = http(self.pc_port, "GET", path, host=host or self.pc_host)
        return status, json.loads(payload or b"null")

    def post(self, path, payload=None, *, token=TOKEN, host=None, raw=None):
        body = raw if raw is not None else json.dumps(payload or {}).encode()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-BiliFlow-Token"] = token
        status, _, reply = http(self.pc_port, "POST", path, host=host or self.pc_host, headers=headers, body=body)
        return status, json.loads(reply or b"null")

    def batch(self, *urls, rights=True):
        return self.post("/api/downloads", {"urls": list(urls), "rights_confirmed": rights})


class PcRouteTests(RouteCase):
    def test_a_batch_is_queued_and_shown_in_the_snapshot(self):
        status, created = self.batch(CLIP)
        self.assertEqual(status, 200, created)
        status, snapshot = self.get("/api/downloads")
        self.assertEqual(status, 200)
        self.assertEqual([task["id"] for task in snapshot["tasks"]], [created["tasks"][0]["id"]])
        self.assertEqual(snapshot["counts"], {"QUEUED": 1})
        status, detail = self.get(f"/api/downloads/{created['tasks'][0]['id']}")
        self.assertEqual((status, detail["events"][0]["kind"]), (200, "QUEUED"))

    def test_writes_need_the_token_and_every_route_needs_a_local_host(self):
        self.assertEqual(self.batch(CLIP)[0], 200)
        task_id = self.store.list_tasks()[0]["id"]
        for path in ("/api/downloads", f"/api/downloads/{task_id}/cancel", "/api/downloads/settings",
                     "/api/downloads/cleanup-temp"):
            with self.subTest(path=path):
                body = {"confirm": True, "slots": 1}
                self.assertEqual(self.post(path, body, token=None)[0], 403)
                self.assertEqual(self.post(path, body, token="wrong")[0], 403)
                self.assertEqual(self.post(path, body, host="evil.example")[0], 403)
        self.assertEqual(self.get("/api/downloads", host="evil.example")[0], 403)
        self.assertEqual(self.get("/api/storage-summary", host="evil.example:80")[0], 403)
        self.assertEqual(self.state(task_id), "QUEUED")
        self.assertEqual(self.worker.slots(), 2)

    def test_a_bad_batch_is_refused_whole_with_one_error_per_line(self):
        status, body = self.batch(CLIP, "https://user@x.example/a", "ftp://clips.example/b", CLIP)
        self.assertEqual((status, body["code"]), (400, "BATCH_REJECTED"))
        self.assertEqual([(error["line"], error["code"]) for error in body["errors"]],
                         [(2, "USERINFO"), (3, "BAD_SCHEME"), (4, "DUPLICATE_IN_BATCH")])
        self.assertEqual(self.store.list_tasks(), [])

    def test_a_link_already_in_the_list_is_refused(self):
        self.assertEqual(self.batch(CLIP)[0], 200)
        status, body = self.batch(CLIP)
        self.assertEqual((status, body["errors"][0]["code"]), (400, "DUPLICATE_EXISTING"))
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_limits_rights_and_bodies(self):
        status, body = self.batch(*[f"https://clips.example/v/{n}" for n in range(21)])
        self.assertEqual((status, body["errors"][0]["code"]), (400, "TOO_MANY_LINKS"))
        status, body = self.batch(CLIP, rights=False)
        self.assertEqual(status, 400)
        self.assertIn("quyền", body["error"])
        # Only the declared length: the handler refuses before reading, and unread bytes would make
        # Windows reset the connection before the client reads the 400.
        reply = raw(self.pc_port, (f"POST /api/downloads HTTP/1.1\r\nHost: {self.pc_host}\r\n"
                                   f"X-BiliFlow-Token: {TOKEN}\r\nContent-Type: application/json\r\n"
                                   "Content-Length: 70000\r\nConnection: close\r\n\r\n").encode())
        self.assertTrue(reply.startswith(b"HTTP/1.0 400"), reply[:60])
        self.assertIn("Request is too large", reply.decode("utf-8", "replace"))
        self.assertEqual(self.post("/api/downloads", raw=b"[1, 2]")[0], 400)
        self.assertEqual(self.post("/api/downloads", {"urls": CLIP})[0], 400)
        self.assertEqual(self.post("/api/downloads/settings", {"slots": 0})[0], 400)
        self.assertEqual(self.post("/api/downloads/cleanup-temp", {})[0], 400)
        self.assertEqual(self.store.list_tasks(), [])

    def test_task_actions_and_unknown_paths(self):
        _, created = self.batch(CLIP)
        task_id = created["tasks"][0]["id"]
        status, body = self.post(f"/api/downloads/{task_id}/rename", {"name": "Tên mới"})
        self.assertEqual((status, body["task"]["title"]), (200, "Tên mới"))
        self.assertEqual(self.post(f"/api/downloads/{task_id}/choose", {"entry_index": 0})[0], 409)
        self.assertEqual(self.post(f"/api/downloads/{task_id}/cancel")[1]["task"]["state"], "CANCELLED")
        self.assertEqual(self.post(f"/api/downloads/{task_id}/remove"),
                         (200, {"id": task_id, "removed": True, "freed_bytes": 0}))
        self.assertEqual(self.post("/api/downloads/999/stop")[0], 404)
        self.assertEqual(self.post("/api/downloads/abc/stop")[0], 404)
        self.assertEqual(self.post(f"/api/downloads/{task_id}/explode")[0], 404)
        self.assertEqual(self.get("/api/downloads/abc")[0], 404)
        self.assertEqual(self.post("/api/downloads/settings", {"slots": 3}), (200, {"slots": 3}))

    def test_a_finished_download_reports_only_its_file_name(self):
        self.scenario(probe={"json": video("Phim hay")}, download={"id": "abc"})
        self.batch(CLIP)
        self.run_all()
        _, snapshot = self.get("/api/downloads")
        task = snapshot["tasks"][0]
        self.assertEqual((task["state"], task["output_name"]), ("COMPLETED", "Phim hay.mp4"))
        self.assertNotIn(str(self.root), json.dumps(snapshot, ensure_ascii=False))
        self.assertTrue((self.root / "input" / "Phim hay.mp4").is_file())

    def test_storage_summary_is_served(self):
        status, body = self.get("/api/storage-summary")
        self.assertEqual(status, 200)
        self.assertIn("computing", body)
        self.center.downloads.storage.wait()
        _, body = self.get("/api/storage-summary")
        self.assertEqual(body["summary"]["cleanable"], {"jobs": 0, "bytes": 0})
        self.assertIn("error", body["summary"]["recycle_bin"])

    def test_without_the_service_the_routes_answer_503_and_the_rest_still_works(self):
        self.center.downloads = None
        self.center.downloads_error = "OperationalError: disk I/O error"
        for status, body in (self.get("/api/downloads"), self.batch(CLIP), self.get("/api/storage-summary")):
            self.assertEqual(status, 503)
            self.assertIn("disk I/O error", body["error"])
        self.assertEqual(self.get("/healthz")[0], 200)
        self.assertEqual(self.post("/api/scheduler", {"paused": True}), (200, {"paused": True}))


class PhoneRouteTests(RouteCase):
    def setUp(self):
        super().setUp()
        status = self.phone.enable(lambda access: _phone_handler_class(self.center, access), address=FAKE_LAN,
                                   check_address=lambda _a: None, check_port=lambda _p: None, port=0)
        self.port, self.code = status["port"], status["code"]
        self.host = f"127.0.0.1:{self.port}"
        status, headers, _ = http(self.port, "POST", "/phone-login", host=self.host,
                                  headers={"Content-Type": "application/x-www-form-urlencoded"},
                                  body=f"code={self.code}".encode())
        self.assertEqual(status, 200)
        self.cookie = headers["set-cookie"][0].split(";", 1)[0]

    def phone_post(self, path, payload, *, token=TOKEN, origin=None, cookie=True):
        headers = {"Content-Type": "application/json", "Origin": origin or f"http://{self.host}"}
        if cookie:
            headers["Cookie"] = self.cookie
        if token:
            headers["X-BiliFlow-Token"] = token
        status, _, reply = http(self.port, "POST", path, host=self.host, headers=headers,
                                body=json.dumps(payload).encode())
        return status, json.loads(reply or b"null")

    def test_the_phone_pastes_links_and_drives_tasks(self):
        status, created = self.phone_post("/api/downloads", {"urls": [CLIP], "rights_confirmed": True})
        self.assertEqual(status, 200, created)
        task_id = created["tasks"][0]["id"]
        for action in ("stop", "resume", "cancel", "remove"):
            with self.subTest(action=action):
                self.assertEqual(self.phone_post(f"/api/downloads/{task_id}/{action}", {})[0], 200)
        self.assertEqual(self.phone_post("/api/downloads/settings", {"slots": 1}), (200, {"slots": 1}))
        self.assertEqual(self.phone_post("/api/downloads/cleanup-temp", {"confirm": True})[0], 200)
        status, _, payload = http(self.port, "GET", "/api/downloads", host=self.host,
                                  headers={"Cookie": self.cookie})
        self.assertEqual((status, json.loads(payload)["tasks"]), (200, []))

    def test_the_phone_still_needs_cookie_token_and_its_own_origin(self):
        payload = {"urls": [CLIP], "rights_confirmed": True}
        self.assertEqual(self.phone_post("/api/downloads", payload, cookie=False)[0], 401)
        self.assertEqual(self.phone_post("/api/downloads", payload, token=None)[0], 403)
        self.assertEqual(self.phone_post("/api/downloads", payload, origin="http://evil.example")[0], 403)
        self.assertEqual(http(self.port, "GET", "/api/downloads", host=self.host)[0], 401)
        self.assertEqual(self.store.list_tasks(), [])

    def test_source_cleanup_and_archive_stay_pc_only(self):
        for path in ("/api/source-cleanup", "/api/source-archive", "/api/source-archive/restore",
                     "/api/source-recycle-check"):
            with self.subTest(path=path):
                status, body = self.phone_post(path, {"job_ids": [1], "preview_id": "x"})
                self.assertEqual((status, body["code"]), (403, "pc_only"))

    def test_every_download_post_route_is_allowed_on_the_phone(self):
        for path in ("/api/downloads", "/api/downloads/7/rename", "/api/downloads/7/choose",
                     "/api/downloads/7/stop", "/api/downloads/7/resume", "/api/downloads/7/cancel",
                     "/api/downloads/7/retry", "/api/downloads/7/remove", "/api/downloads/settings",
                     "/api/downloads/cleanup-temp"):
            self.assertIsNone(phone_access.pc_only_reason(path), path)
        self.assertIsNotNone(phone_access.pc_only_reason("/api/downloads/7/explode"))


class RealCenterTests(unittest.TestCase):
    """The real constructor on a temporary root: the service is built, never started here."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def make_center(self):
        with mock.patch("biliflow.control_center.import_existing_project") as imported:
            center = ControlCenter(self.root, port=0, import_existing=False)
        imported.assert_not_called()
        self.addCleanup(center.lock.close)
        self.addCleanup(center.store.close)
        return center

    def test_the_constructor_builds_the_service_and_stop_closes_it(self):
        center = self.make_center()
        self.assertIsInstance(center.downloads, DownloadService)
        self.assertIsNone(center.downloads_error)
        self.assertTrue((self.root / "state" / "downloads.sqlite3").is_file())
        self.assertEqual(center.cleanable_sources(), (0, 0))
        center.stop_downloads()

    def test_a_broken_download_database_does_not_stop_the_control_center(self):
        (self.root / "state" / "downloads.sqlite3").mkdir(parents=True)  # sqlite cannot open a folder
        center = self.make_center()
        self.assertIsNone(center.downloads)
        self.assertIn("Error", center.downloads_error)
        status, body = control_center._download_answer(center, "GET", "/api/downloads", "")
        self.assertEqual(status, 503)
        self.assertIn("chưa sẵn sàng", body["error"])
        self.assertIsNone(control_center._download_answer(center, "GET", "/api/jobs", ""))
        center.stop_downloads()  # nothing to stop


if __name__ == "__main__":
    unittest.main()
