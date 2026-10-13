"""The throw-away root of Golden clip trials: copies, hard-linked trees, read-only snapshot, safe removal."""
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
        for name in ("src/biliflow", "models", "reports", "scripts", "state", "temp", ".venv", "golden/v2/media",
                     "runtime/python/cpython-3.11.16"):
            (self.install / name).mkdir(parents=True)
        (self.install / gtr.MARKER).write_text('{"biliflow_install": 1}', encoding="utf-8")
        (self.install / "src/biliflow/x.py").write_text("X = 1\n", encoding="utf-8")
        (self.install / "models/m.bin").write_bytes(b"model")
        (self.install / "runtime/python/cpython-3.11.16/python.txt").write_bytes(b"py")
        self.inner = self.install / "runtime/python/cpython-3.11"
        gtr._junction(self.install / "runtime/python/cpython-3.11.16", self.inner)
        (self.install / "scripts/run.ps1").write_text("# run", encoding="utf-8")
        (self.install / "state/brand-memory.json").write_text("{}", encoding="utf-8")
        (self.install / "state/secret.bin").write_bytes(b"never copied")
        with closing(sqlite3.connect(self.install / "state/control-center.sqlite3")) as database:
            database.execute("create table jobs (id integer)")
            database.execute("insert into jobs values (37)")
            database.commit()
        self.golden = self.install / "golden"
        (self.golden / "v2/segments.json").write_text(
            json.dumps({"sources": {"c": {"path": "media/c.mp4"}}}), encoding="utf-8")
        (self.golden / "v2/media/c.mp4").write_bytes(b"clip")

    def tearDown(self):
        if gtr._is_link(self.inner):
            os.rmdir(self.inner)
        self.folder.cleanup()

    def test_the_root_reads_the_install_and_writes_nothing_back(self):
        root = gtr.create(self.install, self.golden, "v2")
        try:
            self.assertEqual(root.parent, (self.install / "temp").resolve())
            model = root / "models/m.bin"
            self.assertFalse(gtr._is_link(model) or gtr._is_link(root / "models"))  # stays inside after resolve
            self.assertEqual((model.read_bytes(), os.stat(model).st_nlink), (b"model", 2))  # a hard link
            alias = root / "runtime/python/cpython-3.11"
            self.assertTrue(gtr._is_link(alias))
            self.assertEqual(alias.resolve(), (root / "runtime/python/cpython-3.11.16").resolve())
            self.assertEqual((alias / "python.txt").read_bytes(), b"py")
            self.assertTrue(gtr._is_link(root / ".venv"))
            self.assertTrue((root / gtr.MARKER).is_file())  # its own root, like a checkout
            self.assertFalse(gtr._is_link(root / "src") or gtr._is_link(root / "scripts"))
            self.assertFalse((root / "state/secret.bin").exists())
            with closing(sqlite3.connect(root / "state/control-center.sqlite3")) as copy:
                self.assertEqual(copy.execute("select id from jobs").fetchall(), [(37,)])
            clip = root / "input/golden/v2/media/c.mp4"
            self.assertEqual(file_sha256(clip), file_sha256(self.golden / "v2/media/c.mp4"))
            self.assertEqual(os.stat(clip).st_nlink, 1)  # a copy, not a link to the Golden clip
        finally:
            gtr.remove(root, self.install)
        self.assertFalse(root.exists())
        self.assertEqual(((self.install / "models/m.bin").read_bytes(), os.stat(self.install / "models/m.bin").st_nlink),
                         (b"model", 1))
        self.assertEqual((self.inner / "python.txt").read_bytes(), b"py")
        self.assertTrue((self.install / ".venv").is_dir())
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

    def test_removal_takes_links_down_without_entering_them(self):
        with self.assertRaises(ValueError):
            gtr.remove(self.install / "reports", self.install)
        root = gtr.create(self.install, self.golden, "v2")
        gtr._junction(self.install / "models", root / "work" / "models-again")
        gtr.remove(root, self.install)
        self.assertFalse(root.exists())
        self.assertEqual((self.install / "models/m.bin").read_bytes(), b"model")

    def test_a_link_leaving_its_tree_is_refused_and_leaves_no_root(self):
        gtr._junction(self.install / "state", self.install / "models/elsewhere")
        try:
            with self.assertRaises(RuntimeError):
                gtr.create(self.install, self.golden, "v2")
        finally:
            os.rmdir(self.install / "models/elsewhere")
        self.assertEqual(list((self.install / "temp").iterdir()), [])
        self.assertTrue((self.install / "state/secret.bin").exists())

    def test_a_failed_copy_leaves_no_root(self):
        (self.golden / "v2/media/c.mp4").unlink()
        with self.assertRaises(FileNotFoundError):
            gtr.create(self.install, self.golden, "v2")
        self.assertEqual(list((self.install / "temp").iterdir()), [])
        self.assertEqual((self.install / "models/m.bin").read_bytes(), b"model")


if __name__ == "__main__":
    unittest.main()
