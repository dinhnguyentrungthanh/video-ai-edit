import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.job_store import JobStore


class JobStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.source = self.root / "video.mp4"
        self.source.write_bytes(b"video")
        self.store = JobStore(self.root / "state.sqlite3")
        self.job = self.store.upsert_job(
            job_key="video-12345678", source_path=self.source,
            source_sha256="1" * 64, source_size_bytes=5,
            source_mtime_ns=self.source.stat().st_mtime_ns,
        )

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_job_round_trip_and_update(self):
        value = self.store.update_job(self.job["id"], content_style="animation", profile="fast")
        self.assertEqual(value["content_style"], "animation")
        self.assertEqual(value["profile"], "fast")

    def test_stages_keep_order_and_find_pending(self):
        self.store.replace_stages(self.job["id"], ["one", "two"])
        self.store.update_stage(self.job["id"], "one", state="COMPLETED")
        self.assertEqual(self.store.next_pending_stage(self.job["id"])["name"], "two")

    def test_ensure_stage_is_idempotent(self):
        self.store.ensure_stage(self.job["id"], "render")
        self.store.ensure_stage(self.job["id"], "render")
        self.assertEqual(len(self.store.stages(self.job["id"])), 1)

    def test_revision_activation_updates_job(self):
        revision = self.store.add_revision(self.job["id"], "reports/q.json", "REVIEW_REQUIRED", None)
        self.store.activate_revision(self.job["id"], revision)
        value = self.store.get_job(self.job["id"])
        self.assertEqual(value["active_queue_path"], "reports/q.json")
        self.assertEqual(value["active_revision"], 1)

    def test_recovery_makes_running_stage_resumable(self):
        self.store.replace_stages(self.job["id"], ["text"])
        self.store.update_stage(self.job["id"], "text", state="RUNNING", pid=123)
        self.store.update_job(self.job["id"], state="SCANNING_TEXT", current_stage="text")
        self.assertEqual(self.store.recover_interrupted(), 1)
        self.assertEqual(self.store.get_job(self.job["id"])["state"], "INTERRUPTED_RECOVERABLE")
        self.assertEqual(self.store.stage(self.job["id"], "text")["state"], "PENDING")

    def test_watcher_stability_resets_when_size_changes(self):
        first = self.store.observe_file(self.source, 5, 1)
        second = self.store.observe_file(self.source, 6, 2)
        self.assertNotEqual(first["stable_since"], "")
        self.assertEqual(second["size_bytes"], 6)
        self.assertIsNone(second["imported_job_id"])

    def test_events_decode_payload(self):
        self.store.add_event(self.job["id"], "TEST", "ok", payload={"a": 1})
        self.assertEqual(self.store.events(self.job["id"])[0]["payload"], {"a": 1})


if __name__ == "__main__":
    unittest.main()
