import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.review_regression import build_review_regression_manifest


class ReviewRegressionTests(unittest.TestCase):
    def test_completed_queue_becomes_regression_examples(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reports" / "job").mkdir(parents=True)
            (root / "input").mkdir()
            source = root / "input" / "video.mp4"
            source.write_bytes(b"video")
            queue = root / "reports" / "job" / "review-queue.json"
            queue.write_text(json.dumps({
                "source": {"path": str(source), "sha256": None, "duration_seconds": 10},
                "items": [
                    {"id": "a", "category": "visual_logo", "start_seconds": 0,
                     "end_seconds": 2, "decision": "CUT", "labels": ["brand"]},
                    {"id": "b", "category": "violence", "start_seconds": 5,
                     "end_seconds": 6, "decision": "KEEP", "labels": []},
                ],
            }), encoding="utf-8")
            result = build_review_regression_manifest(
                project_root=root, queue_paths=[queue], verify_sources=True,
            )
        self.assertEqual(result["source_count"], 1)
        self.assertEqual(result["example_count"], 2)
        self.assertEqual(result["decision_counts"]["CUT"], 1)
        self.assertEqual(result["category_counts"]["visual_logo"], 1)


if __name__ == "__main__":
    unittest.main()
