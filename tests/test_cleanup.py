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


class DownloadCacheTests(unittest.TestCase):
    def make_cache(self, root, relative, *, age_days):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"cached")
        stamp = (datetime.now(timezone.utc) - timedelta(days=age_days)).timestamp()
        os.utime(path, (stamp, stamp))
        return path

    def test_policies_are_one_gigabyte_and_thirty_days(self):
        from biliflow.cleanup import DOWNLOAD_CACHE_POLICIES
        self.assertEqual(set(DOWNLOAD_CACHE_POLICIES), {"cache/yt-dlp", "cache/deno"})
        for policy in DOWNLOAD_CACHE_POLICIES.values():
            self.assertEqual((policy["max_age"], policy["max_bytes"]), (timedelta(days=30), 1024**3))
        # The scan worker's pruning never touches the download caches.
        self.assertFalse(set(DOWNLOAD_CACHE_POLICIES) & set(FILE_CACHE_POLICIES))

    def test_old_download_caches_are_pruned_and_new_ones_kept(self):
        from biliflow.cleanup import prune_download_caches
        with TemporaryDirectory() as directory:
            root = Path(directory)
            old = self.make_cache(root, "cache/deno/gen/old.js", age_days=31)
            new = self.make_cache(root, "cache/yt-dlp/youtube-sigfuncs/new.json", age_days=1)
            other = self.make_cache(root, "cache/visual-logo/old.json.gz", age_days=31)
            result = prune_download_caches(root)
            self.assertEqual(result["removed_files"], 1)
            self.assertFalse(old.exists())
            self.assertTrue(new.exists())
            self.assertTrue(other.exists())

    def test_a_locked_download_cache_file_is_skipped(self):
        from biliflow.cleanup import prune_download_caches
        with TemporaryDirectory() as directory:
            root = Path(directory)
            locked = self.make_cache(root, "cache/deno/dep_analysis_cache_v2", age_days=40)
            free = self.make_cache(root, "cache/deno/gen/free.js", age_days=40)
            real_unlink = Path.unlink

            def unlink(path, missing_ok=False):
                if path.name == locked.name:
                    raise PermissionError("in use")
                return real_unlink(path, missing_ok=missing_ok)
            with patch.object(Path, "unlink", unlink):
                result = prune_download_caches(root)
                with self.assertRaises(PermissionError):
                    # The scan caches keep their old behaviour.
                    with patch.dict(FILE_CACHE_POLICIES, {"cache/deno": {"max_age": timedelta(days=30),
                                                                         "max_bytes": 1024**3}}):
                        prune_file_caches(root)
            self.assertEqual(result["removed_files"], 1)
            self.assertTrue(locked.exists())
            self.assertFalse(free.exists())


if __name__ == "__main__":
    unittest.main()
