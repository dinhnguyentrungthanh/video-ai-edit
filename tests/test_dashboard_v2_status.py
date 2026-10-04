"""G3: /api/status reports render_request from the backend (store.render_request), additive only."""
from __future__ import annotations

import hashlib
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.control_center import ControlCenter
from biliflow.job_store import JobStore
from biliflow.review_evidence import ReviewFrameCache
from biliflow.scheduler import JobScheduler


class StatusRenderRequestTests(unittest.TestCase):
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
        center.recovered = 0
        self.center = center

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def add_job(self, name: str, state: str) -> int:
        source = self.root / "input" / f"{name}.mp4"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(name.encode() * 64)
        stat = source.stat()
        job = self.store.upsert_job(job_key=name, source_path=source,
                                    source_sha256=hashlib.sha256(name.encode()).hexdigest(),
                                    source_size_bytes=stat.st_size, source_mtime_ns=stat.st_mtime_ns, state=state)
        return int(job["id"])

    def by_id(self):
        return {job["id"]: job for job in self.center.status()["jobs"]}

    def test_render_request_follows_the_render_stage(self):
        none = self.add_job("none", "READY_TO_EXPORT")
        paused = self.add_job("paused", "PAUSED")
        failed = self.add_job("failed", "FAILED")
        done = self.add_job("done", "COMPLETED")
        for job_id, stage_state in ((paused, "PENDING"), (failed, "FAILED_RETRYABLE"), (done, "COMPLETED")):
            self.store.ensure_stage(job_id, "render")
            self.store.update_stage(job_id, "render", state=stage_state)
        jobs = self.by_id()
        self.assertIs(jobs[none]["render_request"], False)
        self.assertIs(jobs[paused]["render_request"], True)
        self.assertIs(jobs[failed]["render_request"], True)
        self.assertIs(jobs[done]["render_request"], False, "a finished render is history, not a request")
        for job_id in (none, paused, failed, done):
            self.assertEqual(jobs[job_id]["render_request"], self.store.render_request(job_id) is not None)

    def test_the_field_is_only_added(self):
        job_id = self.add_job("plain", "DISCOVERED")
        job = self.by_id()[job_id]
        for key in ("queue_position", "queue_kind", "render_progress", "review_summary", "source_present",
                    "source_cleanup", "source_archive", "cleanup", "archive"):
            self.assertIn(key, job)
        self.assertIs(job["render_request"], False)


if __name__ == "__main__":
    unittest.main()
