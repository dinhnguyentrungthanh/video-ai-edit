import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow.cleanup import FILE_CACHE_POLICIES, cleanup_candidates, prune_file_caches


class CleanupTests(unittest.TestCase):
    def test_compressed_logo_cache_becomes_dry_run_candidate_after_30_days(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache" / "visual-logo" / "sha" / "routing.json.gz"
            cache.parent.mkdir(parents=True)
            cache.write_bytes(b"compressed")
            old = datetime.now(timezone.utc) - timedelta(days=31)
            os.utime(cache, (old.timestamp(), old.timestamp()))
            candidates = cleanup_candidates(root, now=datetime.now(timezone.utc))
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0]["path"].endswith("routing.json.gz"))
        self.assertIn("30 days", candidates[0]["reason"])

    def test_old_file_caches_are_pruned_automatically(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache" / "ad_candidate_pipeline" / "siglip" / "old.json"
            cache.parent.mkdir(parents=True)
            cache.write_bytes(b"cached")
            now = datetime.now(timezone.utc)
            old = now - timedelta(days=31)
            os.utime(cache, (old.timestamp(), old.timestamp()))
            result = prune_file_caches(root, now=now)
        self.assertEqual(result["removed_files"], 1)
        self.assertEqual(result["removed_bytes"], len(b"cached"))

    def test_review_frame_cache_policy_is_bounded(self):
        policy = FILE_CACHE_POLICIES["cache/review-frames"]
        self.assertEqual(policy["max_age"], timedelta(days=14))
        self.assertEqual(policy["max_bytes"], 1024**3)

    def test_review_frames_are_pruned_by_age(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            frames = root / "cache" / "review-frames" / "f43cf94aadffb8c1"
            frames.mkdir(parents=True)
            old_frame = frames / "0000939500-w640.jpg"
            new_frame = frames / "0000944500-w640.jpg"
            old_frame.write_bytes(b"old")
            new_frame.write_bytes(b"new!")
            now = datetime.now(timezone.utc)
            old = now - timedelta(days=15)
            recent = now - timedelta(days=13)
            os.utime(old_frame, (old.timestamp(), old.timestamp()))
            os.utime(new_frame, (recent.timestamp(), recent.timestamp()))
            candidates = cleanup_candidates(root, now=now)
            self.assertEqual(
                [Path(value["path"]).name for value in candidates], ["0000939500-w640.jpg"],
            )
            result = prune_file_caches(root, now=now)
            self.assertEqual(result["removed_files"], 1)
            self.assertEqual(result["removed_bytes"], 3)
            self.assertFalse(old_frame.exists())
            self.assertTrue(new_frame.exists())

    def test_review_frames_are_pruned_by_size_oldest_first(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            frames = root / "cache" / "review-frames" / "f43cf94aadffb8c1"
            frames.mkdir(parents=True)
            now = datetime.now(timezone.utc)
            paths = []
            for index in range(4):
                path = frames / f"{index:010d}-w640.jpg"
                path.write_bytes(b"x" * 10)
                stamp = (now - timedelta(hours=4 - index)).timestamp()
                os.utime(path, (stamp, stamp))
                paths.append(path)
            bounded = {
                "cache/review-frames": {"max_age": timedelta(days=14), "max_bytes": 25},
            }
            with patch.dict(FILE_CACHE_POLICIES, bounded):
                result = prune_file_caches(root, now=now)
            self.assertEqual(result["removed_files"], 2)
            self.assertEqual(result["kept_bytes"], 20)
            self.assertEqual([path.exists() for path in paths], [False, False, True, True])


if __name__ == "__main__":
    unittest.main()
