"""The install folder (install_paths): found by its marker file a few levels above the package,
never by an environment variable or a parameter; the delete guard keeps its meaning and no scan
stage's cache key reads the files this changed. Fake trees live in a temporary folder under the
install's temp/."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow import install_paths, job_purge, recycle_bin
from biliflow.cache_dependencies import stage_source_paths
from biliflow.install_paths import MARKER_NAME, InstallRootNotFound, find_install_root, is_marker
from biliflow.stage_cache import CACHEABLE_STAGES


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMP_PARENT = REPO_ROOT / "temp"
VALID = json.dumps({"biliflow_install": 1, "layout": "build"})


class RepoMarkerTest(unittest.TestCase):
    def test_the_checkout_root_is_the_install_root_as_before(self):
        self.assertTrue(is_marker(REPO_ROOT / MARKER_NAME))
        root = install_paths.install_root()
        self.assertEqual(root, REPO_ROOT.resolve())
        self.assertEqual(root, Path(recycle_bin.__file__).resolve().parents[2])
        self.assertEqual(recycle_bin.INSTALL_ROOT, root)
        self.assertEqual(job_purge.INSTALL_ROOT, root)
        self.assertEqual(recycle_bin.TEST_RECYCLE_ROOT, root / "temp" / "recycle-bin-test")

    def test_no_environment_variable_moves_the_root(self):
        install_paths.install_root.cache_clear()
        try:
            with patch.dict(os.environ, {"BILIFLOW_ROOT": "C:\\elsewhere", "BILIFLOW_INSTALL_ROOT": "C:\\x"}):
                self.assertEqual(install_paths.install_root(), REPO_ROOT.resolve())
        finally:
            install_paths.install_root.cache_clear()

    def test_no_marker_raises_instead_of_guessing(self):
        install_paths.install_root.cache_clear()
        try:
            with patch.object(install_paths, "find_install_root", return_value=None):
                with self.assertRaisesRegex(InstallRootNotFound, MARKER_NAME):
                    install_paths.install_root()
        finally:
            install_paths.install_root.cache_clear()
        self.assertEqual(install_paths.install_root(), REPO_ROOT.resolve())

    def test_the_delete_guard_keeps_its_meaning(self):
        root = recycle_bin.INSTALL_ROOT
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=TEMP_PARENT) as temp:
            self.assertTrue(job_purge.allowed_project_root(root))
            self.assertTrue(job_purge.allowed_project_root(temp))
            self.assertFalse(job_purge.allowed_project_root(root / "temp"))
            self.assertFalse(job_purge.allowed_project_root(root / "docs"))
            self.assertFalse(job_purge.allowed_project_root(root.parent))


class FindInstallRootTest(unittest.TestCase):
    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        self._temp = TemporaryDirectory(dir=TEMP_PARENT)
        self.base = Path(self._temp.name).resolve()

    def tearDown(self):
        self._temp.cleanup()

    def tree(self, root: Path, *package: str, marker: str | None = VALID) -> Path:
        folder = root.joinpath(*package, "biliflow")
        folder.mkdir(parents=True, exist_ok=True)
        module = folder / "module.py"
        module.write_text("", encoding="utf-8")
        if marker is not None:
            (root / MARKER_NAME).write_text(marker, encoding="utf-8")
        return module

    def test_source_layout_finds_the_checkout_root(self):
        root = self.base / "BiliFlow"
        self.assertEqual(find_install_root(self.tree(root, "src")), root)

    def test_the_nearest_marker_wins_so_a_worktree_is_its_own_root(self):
        install = self.base / "BiliFlow"
        self.tree(install, "src")
        worktree = install / "temp" / "wt-installer"
        self.assertEqual(find_install_root(self.tree(worktree, "src")), worktree)

    def test_a_worktree_without_a_marker_never_borrows_the_install_root(self):
        install = self.base / "BiliFlow"
        self.tree(install, "src")
        module = self.tree(install / "temp" / "wt-old", "src", marker=None)
        self.assertIsNone(find_install_root(module))

    def test_the_search_stops_three_levels_above_the_package(self):
        root = self.base / "build"
        self.assertEqual(find_install_root(self.tree(root, "app", "lib")), root)
        far = self.base / "far"
        self.assertIsNone(find_install_root(self.tree(far, "a", "b", "c")))

    def test_an_invalid_marker_is_not_a_marker(self):
        cases = {
            "wrong-version": json.dumps({"biliflow_install": 2}),
            "true-is-not-one": json.dumps({"biliflow_install": True}),
            "string-version": json.dumps({"biliflow_install": "1"}),
            "list": json.dumps([{"biliflow_install": 1}]),
            "not-json": "biliflow_install=1",
            "empty": "",
            "too-big": json.dumps({"biliflow_install": 1, "pad": "x" * 5000}),
        }
        for name, text in cases.items():
            with self.subTest(name):
                self.assertIsNone(find_install_root(self.tree(self.base / name, "src", marker=text)))
        latin = self.base / "latin1"
        module = self.tree(latin, "src", marker=None)
        (latin / MARKER_NAME).write_bytes(b'{"biliflow_install": 1, "x": "\xe9"}')
        self.assertIsNone(find_install_root(module))

    def test_a_folder_named_like_the_marker_is_not_a_marker(self):
        root = self.base / "folder"
        module = self.tree(root, "src", marker=None)
        (root / MARKER_NAME).mkdir()
        self.assertIsNone(find_install_root(module))

    def test_a_linked_marker_is_not_a_marker(self):
        real = self.base / "real.json"
        real.write_text(VALID, encoding="utf-8")
        root = self.base / "linked"
        module = self.tree(root, "src", marker=None)
        try:
            os.symlink(real, root / MARKER_NAME)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlinks unavailable: {error}")
        self.assertIsNone(find_install_root(module))

    def test_a_missing_module_path_still_answers(self):
        root = self.base / "BiliFlow"
        self.tree(root, "src")
        self.assertEqual(find_install_root(root / "src" / "biliflow" / "gone.py"), root)


class CacheKeyTest(unittest.TestCase):
    def test_no_scan_stage_reads_the_files_of_this_change(self):
        source = REPO_ROOT / "src" / "biliflow"
        changed = {source / name for name in (
            "install_paths.py", "recycle_bin.py", "job_purge.py", "download_tools.py", "performance_report.py",
        )}
        for stage in sorted(CACHEABLE_STAGES):
            with self.subTest(stage):
                paths = set(stage_source_paths(REPO_ROOT, stage))
                self.assertIn(source / "cli.py", paths)
                self.assertEqual(paths & changed, set())


if __name__ == "__main__":
    unittest.main()
