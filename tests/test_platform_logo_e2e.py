"""Safety of scripts/platform_logo_e2e.py (batch 4a): child commands and junction-first cleanup."""
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("platform_logo_e2e", ROOT / "scripts" / "platform_logo_e2e.py")
e2e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(e2e)


class ChildCommandTests(unittest.TestCase):
    def test_prewarm_is_dropped_and_paths_point_into_the_temp_root(self):
        root = Path("T:/e2e/root")
        text = ("powershell.exe", "-File", "T:/e2e/root/scripts/run.ps1", "scan-text", "--input", "a.mp4",
                "--recognition-batch-size", "8", "--prewarm-logo-routing", "--logo-sample-every", "2.0",
                "--logo-decode", "nvdec", "--detect-precision", "fp16")
        name, argv = e2e._child_command(root, text)
        self.assertEqual(name, "scan-text")
        self.assertEqual(argv[:6], [sys.executable, "-m", "biliflow", "--project-root", str(root), "scan-text"])
        self.assertNotIn("--prewarm-logo-routing", argv)
        self.assertFalse([value for value in argv if value.startswith("--logo-")])
        self.assertNotIn("nvdec", argv)
        self.assertIn("--detect-precision", argv)
        self.assertEqual(argv[argv.index("--model-dir") + 1], str(root / "models" / "easyocr"))
        self.assertEqual(argv[argv.index("--semantic-seed") + 1],
                         str(root / "annotations" / "text_semantics_seed_v1.json"))
        name, argv = e2e._child_command(root, ("pwsh", "-File", "x/run.ps1", "localize-visual-logo",
                                                "--report", "r.json", "--output", "o.json"))
        self.assertEqual(argv[1], str(e2e.CODE_ROOT / "scripts" / "localize_visual_logo_report.py"))
        self.assertEqual(argv[2:4], ["--project-root", str(root)])


def _junction(link, target):
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


def _e2e_root(install, stamp, *, under=None, meta_install=None):
    """``<under or install/temp>/platform-logo-e2e-<stamp>/root`` with the e2e-meta.json prepare writes."""
    base = Path(under or Path(install) / "temp") / f"platform-logo-e2e-{stamp}"
    (base / "root").mkdir(parents=True)
    (base / e2e.META).write_text(json.dumps({"production_root": str(meta_install or install)}), encoding="utf-8")
    return base / "root"


def _cleanup(root, install):
    return e2e.main(["cleanup", "--root", str(root), "--production-root", str(install)])


@unittest.skipUnless(os.name == "nt", "junctions are Windows-only")
class CleanupTests(unittest.TestCase):
    def test_cleanup_removes_junctions_first_and_never_touches_their_targets(self):
        with TemporaryDirectory() as folder:
            outside = Path(folder) / "production-models"
            outside.mkdir()
            (outside / "weights.bin").write_bytes(b"keep me")
            root = _e2e_root(folder, "20261003-000000")
            (root / "reports").mkdir()
            (root / "reports" / "queue.json").write_text("{}", encoding="utf-8")
            for name in ("models", "tools"):
                _junction(root / name, outside)
            with self.assertRaises(SystemExit):
                _cleanup(Path(folder) / "temp" / "other" / "root", folder)
            with mock.patch("builtins.print"):
                self.assertEqual(_cleanup(root, folder), 0)
            self.assertFalse(root.parent.exists())
            self.assertEqual((outside / "weights.bin").read_bytes(), b"keep me")

    def test_cleanup_refuses_a_real_folder_named_like_a_junction(self):
        with TemporaryDirectory() as folder:
            root = _e2e_root(folder, "20261003-000001")
            (root / "models").mkdir()
            (root / "models" / "weights.bin").write_bytes(b"x")
            with self.assertRaisesRegex(SystemExit, "real folder"):
                _cleanup(root, folder)
            self.assertTrue((root / "models" / "weights.bin").is_file())

    def test_cleanup_needs_the_prepare_marker_of_this_install(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / "temp" / "platform-logo-e2e-20261003-000002" / "root"
            (root / "reports").mkdir(parents=True)
            with self.assertRaisesRegex(SystemExit, "e2e-meta.json"):
                _cleanup(root, folder)
            other = _e2e_root(folder, "20261003-000004", meta_install=Path(folder) / "another-install")
            with self.assertRaisesRegex(SystemExit, "another install"):
                _cleanup(other, folder)
            self.assertTrue((root / "reports").is_dir())
            self.assertTrue(other.is_dir())

    def test_cleanup_stays_inside_the_install_temp_folder(self):
        # Security review 2026-10-03 (L6): the guard was names only.
        with TemporaryDirectory() as folder:
            install = Path(folder) / "install"
            nested = _e2e_root(install, "20261003-000005", under=Path(folder) / "elsewhere" / "temp")
            with self.assertRaisesRegex(SystemExit, "inside"):
                _cleanup(nested, install)
            self.assertTrue(nested.is_dir())
            # install/temp is a junction to another folder: lexically inside, really elsewhere.
            target = Path(folder) / "target-temp"
            moved = _e2e_root(install, "20261003-000006", under=target)
            (moved / "kept.txt").write_text("x", encoding="utf-8")
            install.mkdir()
            _junction(install / "temp", target)
            with self.assertRaisesRegex(SystemExit, "reparse point"):
                _cleanup(install / "temp" / moved.parent.name / "root", install)
            os.rmdir(install / "temp")
            self.assertTrue((moved / "kept.txt").is_file())
            # The e2e folder itself is a junction.
            (install / "temp").mkdir()
            _junction(install / "temp" / moved.parent.name, moved.parent)
            with self.assertRaisesRegex(SystemExit, "reparse point"):
                _cleanup(install / "temp" / moved.parent.name / "root", install)
            os.rmdir(install / "temp" / moved.parent.name)
            self.assertTrue((moved / "kept.txt").is_file())

    def test_default_install_root_is_the_main_tree_of_a_worktree(self):
        self.assertEqual(e2e._install_root(Path("E:/x/BiliFlow/temp/wt-batch4")), Path("E:/x/BiliFlow"))
        self.assertEqual(e2e._install_root(Path("E:/x/BiliFlow")), Path("E:/x/BiliFlow"))

    def test_links_are_junctions_even_with_an_ampersand_in_the_path(self):
        with TemporaryDirectory() as folder:
            production = Path(folder) / "prod&co"
            for name in e2e.JUNCTIONS:
                (production / name).mkdir(parents=True)
                (production / name / "kept.txt").write_text("x", encoding="utf-8")
            root = _e2e_root(folder, "20261003-000003")
            e2e._ensure_links(root, production)
            for name in e2e.JUNCTIONS:
                self.assertTrue(e2e._reparse_point(root / name), name)
                self.assertEqual((root / name / "kept.txt").read_text(encoding="utf-8"), "x")
            with mock.patch("builtins.print"):
                self.assertEqual(_cleanup(root, folder), 0)
            self.assertTrue(all((production / name / "kept.txt").is_file() for name in e2e.JUNCTIONS))


if __name__ == "__main__":
    unittest.main()
