import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.cleanup import cleanup_candidates


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


if __name__ == "__main__":
    unittest.main()
