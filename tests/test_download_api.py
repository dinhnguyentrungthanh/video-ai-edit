import re
import unittest
from dataclasses import dataclass

from biliflow import download_api
from biliflow.download_api import DownloadService, public_task
from biliflow.storage_summary import StorageSummaryCache
from tests.test_download_worker import CLIP, WorkerCase, video


@dataclass
class FakeBin:
    volume: str = "E:"
    max_bytes: int = 10
    used_bytes: int = 1
    items: int = 1


class ServiceTests(WorkerCase):
    def setUp(self):
        super().setUp()
        self.service = DownloadService(
            self.root, cleanable=lambda: (1, 123), bin_reader=lambda root: FakeBin(),
            store=self.store, worker=self.worker,
            storage=StorageSummaryCache(self.root, cleanable=lambda: (1, 123), bin_reader=lambda root: FakeBin()),
        )

    def post(self, path, body=None):
        return self.service.handle_post(path, body or {})

    def test_snapshot_lists_tasks_sources_slots_and_space(self):
        status, created = self.post("/api/downloads", {"source_id": "clips", "urls": [CLIP],
                                                       "rights_confirmed": True})
        self.assertEqual(status, 200)
        status, snapshot = self.service.handle_get("/api/downloads", "")
        self.assertEqual(status, 200)
        self.assertEqual([task["id"] for task in snapshot["tasks"]], [created["tasks"][0]["id"]])
        self.assertEqual([source["id"] for source in snapshot["sources"]], ["clips", "movies"])
        self.assertEqual(snapshot["settings"], {"slots": 2, "max_slots": 3})
        self.assertEqual(snapshot["counts"], {"QUEUED": 1})
        self.assertEqual(snapshot["space"]["reserve_bytes"], self.space[1])
        self.assertIn("bytes", snapshot["temp"])

    def test_public_tasks_never_carry_local_paths(self):
        self.scenario(probe={"json": video("Phim")})
        self.post("/api/downloads", {"source_id": "clips", "urls": [CLIP], "rights_confirmed": True})
        self.run_all()
        task = self.store.list_tasks()[0]
        shown = public_task(task)
        self.assertEqual(shown["output_name"], "Phim.mp4")
        self.assertEqual(shown["title"], "Phim")
        text = repr(shown)
        self.assertNotIn(str(self.root), text)
        self.assertNotIn("temp_dir", shown)

    def test_batch_errors_and_bad_bodies_are_400(self):
        status, body = self.post("/api/downloads", {"source_id": "clips", "urls": ["https://x.example/a"],
                                                    "rights_confirmed": True})
        self.assertEqual((status, body["code"]), (400, "BATCH_REJECTED"))
        self.assertEqual(body["errors"][0]["code"], "HOST_NOT_ALLOWED")
        self.assertEqual(self.post("/api/downloads", {"source_id": "clips", "urls": "x"})[0], 400)
        status, body = self.post("/api/downloads", {"source_id": "clips", "urls": [CLIP]})
        self.assertEqual(status, 400)
        self.assertIn("quyền", body["error"])
        self.assertEqual(self.post("/api/downloads/settings", {"slots": 9})[0], 400)
        self.assertEqual(self.post("/api/downloads/cleanup-temp", {})[0], 400)
        for ids in ("1", [True], [0], ["1"], [10 ** 12], list(range(1, 1002))):
            with self.subTest(ids=str(ids)[:20]):
                self.assertEqual(self.post("/api/downloads/cleanup-temp", {"confirm": True, "ids": ids})[0], 400)
        self.assertEqual(self.post("/api/downloads/cleanup-temp", {"confirm": True, "ids": []}),
                         (200, {"tasks": 0, "freed_bytes": 0}))

    def test_actions_are_routed_and_unknown_paths_are_left_to_the_caller(self):
        _, created = self.post("/api/downloads", {"source_id": "clips", "urls": [CLIP],
                                                  "rights_confirmed": True})
        task_id = created["tasks"][0]["id"]
        status, body = self.post(f"/api/downloads/{task_id}/rename", {"name": "Tên mới"})
        self.assertEqual((status, body["task"]["title"]), (200, "Tên mới"))
        self.assertEqual(self.post(f"/api/downloads/{task_id}/stop")[1]["task"]["state"], "STOPPED")
        self.assertEqual(self.post(f"/api/downloads/{task_id}/choose", {"entry_index": 1})[0], 409)
        self.assertEqual(self.post(f"/api/downloads/{task_id}/remove")[1]["removed"], True)
        self.assertEqual(self.post("/api/downloads/999/stop")[0], 404)
        self.assertEqual(self.post("/api/downloads/settings", {"slots": 3}), (200, {"slots": 3}))
        self.assertEqual(self.post("/api/downloads/cleanup-temp", {"confirm": True})[0], 200)
        self.assertIsNone(self.post("/api/jobs/1/start"))
        self.assertIsNone(self.service.handle_get("/api/jobs", ""))

    def test_the_route_lists_name_every_path_the_service_answers(self):
        examples = {"GET": ["/api/downloads", "/api/downloads/7", "/api/storage-summary"],
                    "POST": ["/api/downloads", "/api/downloads/settings", "/api/downloads/cleanup-temp",
                             *[f"/api/downloads/7/{op}" for op in
                               ("rename", "choose", "stop", "resume", "cancel", "retry", "remove")]]}
        for method, routes in (("GET", download_api.GET_ROUTES), ("POST", download_api.POST_ROUTES)):
            for path in examples[method]:
                with self.subTest(method=method, path=path):
                    self.assertTrue(any(re.fullmatch(route, path) for route in routes))
                    self.assertTrue(download_api.owns(path))
                    answer = (self.service.handle_get(path, "") if method == "GET"
                              else self.service.handle_post(path, {}))
                    self.assertIsNotNone(answer)
        self.assertFalse(download_api.owns("/api/downloadsx"))

    def test_local_paths_never_reach_a_page(self):
        _, created = self.post("/api/downloads", {"source_id": "clips", "urls": [CLIP], "rights_confirmed": True})
        task_id = created["tasks"][0]["id"]
        where = str(self.root.resolve())
        self.store.update_fields(task_id, error_message=f"[WinError 32] {where}\\temp\\downloads\\1\\a.mp4")
        self.store.add_event(task_id, 1, "NOTE", "Moved " + where.replace("\\", "/").upper() + "/input/a.mp4")
        self.store.append_log(task_id, 1, [f'[download] Destination: {where}\\temp\\downloads\\1\\a.mp4'])
        self.worker.last_error = f"OSError: {where}\\state"
        _, snapshot = self.service.handle_get("/api/downloads", "")
        _, detail = self.service.handle_get(f"/api/downloads/{task_id}", "")
        text = repr((snapshot, detail)).lower()
        self.assertNotIn(where.lower(), text)
        self.assertNotIn(where.replace("\\", "/").lower(), text)
        self.assertIn("<biliflow>\\\\temp", text)
        self.assertIn("<biliflow>", snapshot["worker_error"].lower())

    def test_an_id_too_long_for_sqlite_is_not_found(self):
        huge = "9" * 30
        self.assertEqual(self.service.handle_get(f"/api/downloads/{huge}", "")[0], 404)
        self.assertEqual(self.post(f"/api/downloads/{huge}/stop")[0], 404)

    def test_task_detail_has_events_and_log(self):
        _, created = self.post("/api/downloads", {"source_id": "clips", "urls": [CLIP],
                                                  "rights_confirmed": True})
        task_id = created["tasks"][0]["id"]
        status, body = self.service.handle_get(f"/api/downloads/{task_id}", "")
        self.assertEqual(status, 200)
        self.assertEqual(body["events"][0]["kind"], "QUEUED")
        self.assertEqual(body["log"], [])
        self.assertEqual(self.service.handle_get("/api/downloads/999", "")[0], 404)

    def test_storage_summary_is_read_only_and_cached(self):
        status, first = self.service.handle_get("/api/storage-summary", "")
        self.assertEqual(status, 200)
        self.service.storage.wait()
        _, second = self.service.handle_get("/api/storage-summary", "")
        self.assertEqual(second["summary"]["cleanable"], {"jobs": 1, "bytes": 123})
        self.assertEqual(second["summary"]["recycle_bin"]["used_bytes"], 1)


if __name__ == "__main__":
    unittest.main()
