import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.storage_summary import FOLDERS, StorageSummaryCache, compute_summary


@dataclass
class FakeBin:
    volume: str = "E:"
    max_bytes: int = 1000
    used_bytes: int = 250
    items: int = 3


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        for index, name in enumerate(FOLDERS, start=1):
            folder = self.root / name / "sub"
            folder.mkdir(parents=True)
            (folder / "f.bin").write_bytes(b"x" * index * 10)

    def tearDown(self):
        self.directory.cleanup()

    def test_folder_sizes_cleanable_part_and_recycle_bin(self):
        before = {path: path.stat().st_mtime_ns for path in self.root.rglob("*") if path.is_file()}
        summary = compute_summary(self.root, cleanable=lambda: (2, 4096), bin_reader=lambda root: FakeBin())
        self.assertEqual(summary["folders"], {"input": 10, "output": 20, "reports": 30, "cache": 40, "temp": 50})
        self.assertEqual(summary["cleanable"], {"jobs": 2, "bytes": 4096})
        self.assertEqual(summary["recycle_bin"]["used_bytes"], 250)
        self.assertGreater(summary["drive"]["total_bytes"], 0)
        self.assertGreaterEqual(summary["drive"]["reserve_bytes"], 100 * 1024**3)
        after = {path: path.stat().st_mtime_ns for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_errors_are_reported_in_their_part_only(self):
        def broken_bin(root):
            raise OSError("no bin")

        def broken_cleanable():
            raise RuntimeError("bad job")
        summary = compute_summary(self.root, cleanable=broken_cleanable, bin_reader=broken_bin)
        self.assertIn("no bin", summary["recycle_bin"]["error"])
        self.assertIn("bad job", summary["cleanable"]["error"])
        self.assertEqual(summary["folders"]["input"], 10)
        self.assertIn("error", compute_summary(self.root, cleanable=lambda: (0, 0), bin_reader=None)["recycle_bin"])

    def test_the_cache_serves_the_last_value_and_refreshes_after_five_minutes(self):
        now = [0.0]
        calls = []

        def cleanable():
            calls.append(now[0])
            return (0, 0)
        cache = StorageSummaryCache(self.root, cleanable=cleanable, bin_reader=lambda root: FakeBin(),
                                    clock=lambda: now[0])
        first = cache.get()
        self.assertIsNone(first["summary"])
        self.assertTrue(first["computing"])
        cache.wait()
        self.assertEqual(cache.get()["summary"]["folders"]["temp"], 50)
        self.assertEqual(len(calls), 1)
        now[0] = 299.0
        cache.get()
        cache.wait()
        self.assertEqual(len(calls), 1)
        now[0] = 301.0
        cache.get()
        cache.wait()
        self.assertEqual(len(calls), 2)
        cache.get(refresh=True)
        cache.wait()
        self.assertEqual(len(calls), 3)

    def test_a_refresh_during_a_round_starts_one_more_round(self):
        import threading
        release, calls = threading.Event(), []

        def cleanable():
            calls.append(1)
            release.wait(5)
            return (0, 0)
        cache = StorageSummaryCache(self.root, cleanable=cleanable, bin_reader=lambda root: FakeBin())
        cache.get()
        self.assertTrue(cache.get(refresh=True)["computing"])  # asked while the first round runs
        release.set()
        cache.wait()
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
