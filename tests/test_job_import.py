import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.job_import import import_existing_project
from biliflow.job_store import JobStore


class JobImportTests(unittest.TestCase):
    def test_import_preserves_queue_and_activates_latest(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("input", "reports/r1", "output", "state"):
                (root / name).mkdir(parents=True, exist_ok=True)
            source = root / "input" / "movie.mp4"
            source.write_bytes(b"source")
            digest = hashlib.sha256(b"source").hexdigest()
            report = root / "reports" / "r1" / "scan.json"
            report.write_text(json.dumps({"content_style": "animation"}), encoding="utf-8")
            queue = {
                "status": "REVIEW_REQUIRED", "updated_at": "2026-01-01T00:00:00+00:00",
                "source": {"path": str(source), "sha256": digest, "duration_seconds": 12.0},
                "reports": ["reports/r1/scan.json"], "items": [],
            }
            queue_path = root / "reports" / "r1" / "review-queue.json"
            original = json.dumps(queue)
            queue_path.write_text(original, encoding="utf-8")
            store = JobStore(root / "state" / "jobs.sqlite3")
            try:
                result = import_existing_project(root, store)
                self.assertEqual(result, {"sources": 1, "revisions": 1})
                job = store.list_jobs()[0]
                self.assertEqual(job["content_style"], "animation")
                self.assertEqual(job["state"], "WAITING_REVIEW")
                self.assertEqual(queue_path.read_text(encoding="utf-8"), original)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
