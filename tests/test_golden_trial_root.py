"""The throw-away root of Golden clip trials: copies, junctions, read-only snapshot, safe removal."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from biliflow import recycle_bin
from biliflow.golden_set import file_sha256

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import golden_trial_root as gtr  # noqa: E402

TEMP_PARENT = recycle_bin.INSTALL_ROOT / "temp"


@unittest.skipUnless(os.name == "nt", "junctions are Windows only")
class TrialRootTest(unittest.TestCase):
    def setUp(self):
        TEMP_PARENT.mkdir(exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=TEMP_PARENT, prefix="golden-trial-root-test-")
        self.install = Path(self.folder.name) / "install"
        for name in ("src", "models", "reports", "scripts", "state", "temp", "golden/v2/media"):
            (self.install / name).mkdir(parents=True)
        (self.install / "models/m.bin").write_bytes(b"model")
        (self.install / "scripts/run.ps1").write_text("# run", encoding="utf-8")
        (self.install / "state/brand-memory.json").write_text("{}", encoding="utf-8")
        (self.install / "state/secret.bin").write_bytes(b"never copied")
        database = sqlite3.connect(self.install / "state/control-center.sqlite3")
        database.execute("create table jobs (id integer)")
        database.execute("insert into jobs values (37)")
        database.commit()
        database.close()
        self.golden = self.install / "golden"
        (self.golden / "v2/segments.json").write_text(
            json.dumps({"sources": {"c": {"path": "media/c.mp4"}}}), encoding="utf-8")
        (self.golden / "v2/media/c.mp4").write_bytes(b"clip")

    def tearDown(self):
        self.folder.cleanup()

    def test_the_root_reads_the_install_and_writes_nothing_back(self):
        root = gtr.create(self.install, self.golden, "v2")
        try:
            self.assertEqual(root.parent, (self.install / "temp").resolve())
            self.assertEqual((root / "models/m.bin").read_bytes(), b"model")  # junction
            self.assertTrue(gtr._is_link(root / "models"))
            self.assertFalse(gtr._is_link(root / "scripts"))  # a copy: the stage finds this root
            self.assertFalse((root / "state/secret.bin").exists())
            with closing(sqlite3.connect(root / "state/control-center.sqlite3")) as copy:
                self.assertEqual(copy.execute("select id from jobs").fetchall(), [(37,)])
            clip = root / "input/golden/v2/media/c.mp4"
            self.assertEqual(file_sha256(clip), file_sha256(self.golden / "v2/media/c.mp4"))
            self.assertFalse(gtr._is_link(clip))
        finally:
            gtr.remove(root, self.install)
        self.assertFalse(root.exists())
        self.assertEqual((self.install / "models/m.bin").read_bytes(), b"model")
        self.assertTrue((self.golden / "v2/media/c.mp4").exists())

    def test_trial_folders_move_to_the_install_reports(self):
        root = gtr.create(self.install, self.golden, "v2")
        try:
            self.assertFalse(gtr._is_link(root / "reports"))  # the scheduler keeps artifacts inside its root
            (root / "reports/jobs/trial-a").mkdir()
            (root / "reports/jobs/trial-a/review-queue.json").write_text("{}", encoding="utf-8")
            (root / "reports/benchmarks/trial-a").mkdir()
            moved = gtr.harvest(root, self.install)
            self.assertEqual(sorted(p.relative_to(self.install.resolve()).as_posix() for p in moved),
                             ["reports/benchmarks/trial-a", "reports/jobs/trial-a"])
            self.assertTrue((self.install / "reports/jobs/trial-a/review-queue.json").exists())
            (root / "reports/jobs/trial-a").mkdir()
            with self.assertRaises(RuntimeError):  # never over an existing folder
                gtr.harvest(root, self.install)
        finally:
            gtr.remove(root, self.install)

    def test_removal_refuses_another_folder_and_a_left_link(self):
        with self.assertRaises(ValueError):
            gtr.remove(self.install / "reports", self.install)
        root = gtr.create(self.install, self.golden, "v2")
        stray = root / "work" / "models-again"
        gtr._junction(self.install / "models", stray)
        try:
            with self.assertRaises(RuntimeError):
                gtr.remove(root, self.install)
            self.assertTrue((root / "scripts/run.ps1").exists())  # nothing deleted
        finally:
            os.rmdir(stray)
            gtr.remove(root, self.install)
        self.assertEqual((self.install / "models/m.bin").read_bytes(), b"model")

    def test_a_failed_copy_leaves_no_root(self):
        (self.golden / "v2/media/c.mp4").unlink()
        with self.assertRaises(FileNotFoundError):
            gtr.create(self.install, self.golden, "v2")
        self.assertEqual([p for p in (self.install / "temp").iterdir()], [])
        self.assertEqual((self.install / "models/m.bin").read_bytes(), b"model")


if __name__ == "__main__":
    unittest.main()
