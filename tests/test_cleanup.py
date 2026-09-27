import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.cleanup import cleanup_candidates, prune_file_caches


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


if __name__ == "__main__":
    unittest.main()
