import importlib.machinery
import importlib.util
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from biliflow import stage_cache
from biliflow.hardware_schema import GpuInfo
from biliflow.hardware_system import NvmlRead
from biliflow.stage_cache import (
    BUILD_MANIFEST_NAME,
    CACHEABLE_STAGES,
    StageArtifactCache,
    StageCachePathTooLong,
    build_code_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class StageArtifactCacheTests(unittest.TestCase):
    def _root(self, directory: str) -> Path:
        root = Path(directory)
        (root / "src" / "biliflow").mkdir(parents=True)
        (root / "src" / "biliflow" / "worker.py").write_text(
            "VERSION = 1\n", encoding="utf-8"
        )
        # The stages these tests cache must exist as source, or cacheable() refuses them.
        for name in ("scanner", "live_safety_scanner"):
            (root / "src" / "biliflow" / f"{name}.py").write_text("# scanner\n", encoding="utf-8")
        (root / "config").mkdir()
        (root / "config" / "processing_profiles.json").write_text(
            '{"careful": {"sample": 2}}', encoding="utf-8"
        )
        (root / "scripts").mkdir()
        (root / "scripts" / "run.ps1").write_text("python -m biliflow", encoding="utf-8")
        (root / "reports" / "jobs").mkdir(parents=True)
        (root / "input").mkdir()
        return root

    def test_exact_stage_snapshot_restores_all_evidence_into_new_revision(self):
        with TemporaryDirectory() as directory:
            root = self._root(directory)
            source = root / "input" / "movie.mp4"
            source.write_bytes(b"video")
            sha = "1" * 64
            old_report = root / "reports" / "jobs" / "run-1"
            old_stage = old_report / "adult"
            (old_stage / "thumbnails").mkdir(parents=True)
            (old_stage / "thumbnails" / "frame.jpg").write_bytes(b"jpeg")
            artifact = old_stage / "scan.json"
            artifact.write_text(
                json.dumps({
                    "status": "COMPLETED",
                    "input_sha256": sha,
                    "intervals": [{"thumbnail": "thumbnails/frame.jpg"}],
                }),
                encoding="utf-8",
            )
            cache = StageArtifactCache(root)
            stored = cache.store(
                stage_name="adult", source_sha256=sha, source_path=source,
                report_root=old_report,
                commands=(("run", "--input", str(source), "--report-dir", str(old_stage)),),
                artifact_paths=(artifact,),
            )
            self.assertIsNotNone(stored)

            new_report = root / "reports" / "jobs" / "run-2"
            new_artifact = new_report / "adult" / "scan.json"
            hit = cache.restore(
                stage_name="adult", source_sha256=sha, source_path=source,
                report_root=new_report,
                commands=(("run", "--input", str(source), "--report-dir", str(new_artifact.parent)),),
                artifact_paths=(new_artifact,),
            )
            self.assertIsNotNone(hit)
            self.assertTrue(new_artifact.is_file())
            self.assertEqual(
                (new_artifact.parent / "thumbnails" / "frame.jpg").read_bytes(),
                b"jpeg",
            )

    def test_changed_command_does_not_reuse_old_stage(self):
        with TemporaryDirectory() as directory:
            root = self._root(directory)
            source = root / "input" / "movie.mp4"
            source.write_bytes(b"video")
            sha = "2" * 64
            old_report = root / "reports" / "jobs" / "run-1"
            artifact = old_report / "adult" / "scan.json"
            artifact.parent.mkdir(parents=True)
            artifact.write_text(
                json.dumps({"status": "COMPLETED", "input_sha256": sha}),
                encoding="utf-8",
            )
            cache = StageArtifactCache(root)
            cache.store(
                stage_name="adult", source_sha256=sha, source_path=source,
                report_root=old_report,
                commands=(("run", "--sample-fps", "2", "--report-dir", str(artifact.parent)),),
                artifact_paths=(artifact,),
            )
            new_report = root / "reports" / "jobs" / "run-2"
            new_artifact = new_report / "adult" / "scan.json"
            hit = cache.restore(
                stage_name="adult", source_sha256=sha, source_path=source,
                report_root=new_report,
                commands=(("run", "--sample-fps", "1", "--report-dir", str(new_artifact.parent)),),
                artifact_paths=(new_artifact,),
            )
            self.assertIsNone(hit)
            self.assertFalse(new_artifact.exists())

    def test_sibling_stage_roots_do_not_replace_unrelated_completed_stage(self):
        with TemporaryDirectory() as directory:
            root = self._root(directory)
            source = root / "input" / "movie.mp4"
            source.write_bytes(b"video")
            sha = "4" * 64
            old_report = root / "reports" / "jobs" / "run-1"
            gore = old_report / "gore" / "scan.json"
            violence = old_report / "violence" / "scan.json"
            for artifact in (gore, violence):
                artifact.parent.mkdir(parents=True)
                artifact.write_text(
                    json.dumps({"status": "COMPLETED", "input_sha256": sha}),
                    encoding="utf-8",
                )
            cache = StageArtifactCache(root)
            cache.store(
                stage_name="live_safety", source_sha256=sha, source_path=source,
                report_root=old_report,
                commands=(("shared", "--report-dir", str(old_report)),),
                artifact_paths=(gore, violence),
            )

            new_report = root / "reports" / "jobs" / "run-2"
            adult = new_report / "adult" / "scan.json"
            adult.parent.mkdir(parents=True)
            adult.write_text('{"keep": true}', encoding="utf-8")
            new_gore = new_report / "gore" / "scan.json"
            new_violence = new_report / "violence" / "scan.json"
            hit = cache.restore(
                stage_name="live_safety", source_sha256=sha, source_path=source,
                report_root=new_report,
                commands=(("shared", "--report-dir", str(new_report)),),
                artifact_paths=(new_gore, new_violence),
            )
            self.assertIsNotNone(hit)
            self.assertTrue(adult.is_file())
            self.assertTrue(new_gore.is_file())
            self.assertTrue(new_violence.is_file())

    def test_prune_removes_expired_entries(self):
        with TemporaryDirectory() as directory:
            root = self._root(directory)
            cache = StageArtifactCache(root)
            manifest = (
                root / "cache" / "stage-results" / ("3" * 64)
                / "adult" / "key" / "manifest.json"
            )
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{}", encoding="utf-8")
            old = datetime.now(timezone.utc) - timedelta(days=3)
            os.utime(manifest, (old.timestamp(), old.timestamp()))
            result = cache.prune(max_age=timedelta(days=1))
            self.assertEqual(result["removed_entries"], 1)
            self.assertFalse(manifest.parent.exists())

    def _adult_store_args(self, root: Path, sha: str) -> dict:
        source = root / "input" / "movie.mp4"
        source.write_bytes(b"video")
        report = root / "reports" / "jobs" / "run-1"
        artifact = report / "adult" / "scan.json"
        (artifact.parent / "thumbnails").mkdir(parents=True)
        (artifact.parent / "thumbnails" / "frame.jpg").write_bytes(b"jpeg")
        artifact.write_text(json.dumps({"status": "COMPLETED", "input_sha256": sha}), encoding="utf-8")
        return {"stage_name": "adult", "source_sha256": sha, "source_path": source, "report_root": report,
                "commands": (("run", "--report-dir", str(artifact.parent)),), "artifact_paths": (artifact,)}

    @staticmethod
    def _cache_paths(root: Path) -> list[str]:
        base = root / "cache" / "stage-results"
        return sorted(path.relative_to(base).as_posix() for path in base.rglob("*")) if base.exists() else []

    def test_store_refuses_an_entry_past_the_path_limit_before_writing_anything(self):
        with TemporaryDirectory() as directory:
            root = self._root(directory)
            sha = "5" * 64
            args = self._adult_store_args(root, sha)
            cache = StageArtifactCache(root)
            entry = cache.entry(sha, "adult", cache.key(**args))
            # The longest paths store() writes are in the entry's .tmp folder.
            folder = Path(os.path.abspath(entry.with_name(entry.name + ".tmp"))) / "snapshot" / "adult" / "thumbnails"
            longest = {"MAX_DIRECTORY_PATH_CHARS": str(folder), "MAX_FILE_PATH_CHARS": str(folder / "frame.jpg")}
            other = {"MAX_DIRECTORY_PATH_CHARS": "MAX_FILE_PATH_CHARS",
                     "MAX_FILE_PATH_CHARS": "MAX_DIRECTORY_PATH_CHARS"}
            for name, path in longest.items():
                with (self.subTest(limit=name),
                      mock.patch.object(stage_cache, "_long_paths_enabled", return_value=False),
                      mock.patch.object(stage_cache, other[name], 10_000),
                      mock.patch.object(stage_cache, name, len(path) - 1)):
                    with self.assertRaises(StageCachePathTooLong) as raised:
                        cache.store(**args)
                    self.assertEqual(raised.exception.path, path)
                    self.assertEqual(raised.exception.limit, len(path) - 1)
                    self.assertEqual(self._cache_paths(root), [])
            with (mock.patch.object(stage_cache, "_long_paths_enabled", return_value=False),
                  mock.patch.object(stage_cache, "MAX_DIRECTORY_PATH_CHARS", len(longest["MAX_DIRECTORY_PATH_CHARS"])),
                  mock.patch.object(stage_cache, "MAX_FILE_PATH_CHARS", len(longest["MAX_FILE_PATH_CHARS"]))):
                self.assertIsNotNone(cache.store(**args))  # exactly at the limit
            self.assertFalse(any(path.endswith(".tmp") for path in self._cache_paths(root)))

    def test_windows_long_paths_lift_the_path_limit(self):
        with TemporaryDirectory() as directory:
            root = self._root(directory)
            args = self._adult_store_args(root, "6" * 64)
            with (mock.patch.object(stage_cache, "_long_paths_enabled", return_value=True),
                  mock.patch.object(stage_cache, "MAX_DIRECTORY_PATH_CHARS", 10),
                  mock.patch.object(stage_cache, "MAX_FILE_PATH_CHARS", 10)):
                self.assertIsNotNone(StageArtifactCache(root).store(**args))


class CacheKeyScopeTests(unittest.TestCase):
    """B1: models a stage loads, torch/GPU runtime, builds without sources, prune sizes."""

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = StageArtifactCacheTests._root(self, temp.name)
        self.source = self.root / "input" / "movie.mp4"
        self.source.write_bytes(b"video")
        stage_cache._gpu_identity.cache_clear()
        self.addCleanup(stage_cache._gpu_identity.cache_clear)

    def key(self, stage="text", root=None):
        root = root or self.root
        artifact = root / "reports" / "jobs" / "run-1" / stage / "scan.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text('{"status": "COMPLETED"}', encoding="utf-8")
        with mock.patch.object(stage_cache, "_gpu_identity", return_value=(("GPU A", "8.6"),)):
            return StageArtifactCache(root).key(
                stage_name=stage, source_sha256="5" * 64, source_path=self.source,
                report_root=artifact.parent.parent, commands=(("run", "--input", str(self.source)),),
                artifact_paths=(artifact,),
            )

    def write(self, relative, data=b"x"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def test_key_covers_only_the_stage_models_and_skips_files_that_are_not_models(self):
        self.write("models/easyocr/craft.pth", b"weights")
        self.write("models/easyocr/manifest.json", b'{"revision": "1"}')
        self.write("models/qwen2_vl_2b_instruct/config.json", b"{}")
        text, logo = self.key("text"), self.key("visual_logo")
        for relative in ("models/easyocr/.gitkeep", "models/easyocr/__pycache__/m.cpython-311.pyc",
                         "models/easyocr/.cache/huggingface/download/a.metadata",
                         "models/easyocr/stray.pyo", "models/florence_2_base/modeling.py"):
            self.write(relative)
        self.assertEqual(self.key("text"), text)
        self.write("models/qwen2_vl_2b_instruct/config.json", b'{"changed": true}')
        self.assertEqual(self.key("text"), text)
        self.assertNotEqual(self.key("visual_logo"), logo)
        self.write("models/easyocr/manifest.json", b'{"revision": "2"}')
        self.assertNotEqual(self.key("text"), text)

    def test_model_filter_matches_the_model_lock_script(self):
        spec = importlib.util.spec_from_file_location(
            "models_lock_for_test", REPO_ROOT / "scripts" / "models_lock.py")
        models_lock = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(models_lock)
        for relative in ("craft.pth", "a/b.safetensors", ".gitkeep", "x/__pycache__/m.pyc", "m.pyc",
                         "m.pyo", ".cache/huggingface/trees/t.json", "sub/.cache/x", "config.json"):
            with self.subTest(relative):
                self.assertEqual(stage_cache._not_a_model_file(Path(relative)),
                                 models_lock._not_a_model_file(Path(relative)))
        # The manifest is not a weight for the lock, but it stays in the cache key.
        self.assertFalse(stage_cache._not_a_model_file(Path("manifest.json")))

    def test_torch_and_gpu_are_in_the_key_but_not_the_driver(self):
        base = self.key()
        with mock.patch.object(stage_cache, "_torch_versions",
                               return_value={"torch": "9.9.9+cu999", "cuda": "99.9"}):
            self.assertNotEqual(self.key(), base)
        stage_cache._gpu_identity.cache_clear()
        gpu = GpuInfo("GPU A", "8.6", 6144)
        with mock.patch("biliflow.hardware_system.WindowsSystem.nvml",
                        return_value=NvmlRead("ok", driver="555.1", gpus=(gpu,))):
            first = stage_cache._runtime_identity()["gpus"]
        stage_cache._gpu_identity.cache_clear()
        with mock.patch("biliflow.hardware_system.WindowsSystem.nvml",
                        return_value=NvmlRead("ok", driver="560.9", gpus=(gpu,))):
            self.assertEqual(stage_cache._runtime_identity()["gpus"], first)
        self.assertEqual(first, [{"name": "GPU A", "compute_capability": "8.6"}])
        stage_cache._gpu_identity.cache_clear()
        with mock.patch("biliflow.hardware_system.WindowsSystem.nvml",
                        return_value=NvmlRead("no_library", error="none")):
            self.assertEqual(stage_cache._runtime_identity()["gpus"], {"nvml": "no_library"})
        for other in ((("GPU B", "8.6"),), (("GPU A", "8.9"),)):
            with mock.patch.object(stage_cache, "_gpu_identity", return_value=other):
                identity = stage_cache._runtime_identity()
            self.assertNotEqual(identity["gpus"], first)

    def test_torch_versions_come_from_version_py_without_importing_torch(self):
        package = self.root / "fake-site" / "torch"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("raise RuntimeError('imported')\n", encoding="utf-8")
        (package / "version.py").write_text(
            "from typing import Optional\n__version__ = '2.0.0+cu118'\ncuda: Optional[str] = '11.8'\n",
            encoding="utf-8")
        spec = importlib.machinery.ModuleSpec("torch", None, is_package=True)
        spec.submodule_search_locations = [str(package)]
        with mock.patch.object(stage_cache.importlib.util, "find_spec", return_value=spec):
            self.assertEqual(stage_cache._torch_versions(), {"torch": "2.0.0+cu118", "cuda": "11.8"})
        with mock.patch.object(stage_cache.importlib.util, "find_spec", return_value=None):
            self.assertEqual(stage_cache._torch_versions(), {"torch": None, "cuda": None})

    def test_a_build_without_sources_is_keyed_by_its_manifest_or_not_cached(self):
        build = self.root / "build"
        (build / "src" / "biliflow").mkdir(parents=True)
        (build / "src" / "biliflow" / "__main__.py").write_text("# kept as source\n", encoding="utf-8")
        (build / "reports" / "jobs").mkdir(parents=True)
        artifact = build / "reports" / "jobs" / "run-1" / "adult" / "scan.json"
        artifact.parent.mkdir(parents=True)
        artifact.write_text('{"status": "COMPLETED"}', encoding="utf-8")
        cache = StageArtifactCache(build)
        self.assertFalse(cache.cacheable("adult", (artifact,)))
        manifest = build_code_manifest(REPO_ROOT, "build-1")
        self.assertEqual(set(manifest["stages"]), set(CACHEABLE_STAGES))
        for stage, modules in manifest["stages"].items():
            self.assertIn("src/biliflow/cli.py", modules, stage)
        path = build / BUILD_MANIFEST_NAME
        path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertTrue(cache.cacheable("adult", (artifact,)))
        first = self.key("adult", root=build)
        path.write_text(json.dumps({**manifest, "build_id": "build-2"}), encoding="utf-8")
        self.assertNotEqual(self.key("adult", root=build), first)
        changed = json.loads(json.dumps(manifest))
        changed["stages"]["adult"]["src/biliflow/scanner.py"] = "0" * 64
        path.write_text(json.dumps(changed), encoding="utf-8")
        self.assertNotEqual(self.key("adult", root=build), first)
        for broken in ({**manifest, "biliflow_build": 2}, {**manifest, "build_id": " "},
                       {**manifest, "stages": {"text": manifest["stages"]["text"]}},
                       {**manifest, "stages": {"adult": {"src/biliflow/scanner.py": "XYZ"}}}):
            path.write_text(json.dumps(broken), encoding="utf-8")
            self.assertFalse(cache.cacheable("adult", (artifact,)))
        path.write_text("not json", encoding="utf-8")
        self.assertFalse(cache.cacheable("adult", (artifact,)))

    def test_source_files_win_over_a_build_manifest(self):
        first = self.key("adult")
        (self.root / BUILD_MANIFEST_NAME).write_text(
            json.dumps(build_code_manifest(REPO_ROOT, "build-1")), encoding="utf-8")
        self.assertEqual(self.key("adult"), first)
        self.assertTrue(StageArtifactCache(self.root).cacheable(
            "adult", (self.root / "reports" / "jobs" / "run-1" / "adult" / "scan.json",)))

    def test_prune_uses_recorded_sizes_and_walks_only_entries_without_one(self):
        cache = StageArtifactCache(self.root)
        report = self.root / "reports" / "jobs" / "run-1"
        entries = []
        for index, size in enumerate((300, 500, 700)):
            artifact = report / f"stage-{index}" / "scan.json"
            artifact.parent.mkdir(parents=True)
            artifact.write_text(json.dumps({"status": "COMPLETED", "pad": "p" * size}), encoding="utf-8")
            (artifact.parent / "frame.jpg").write_bytes(b"j" * size)
            stored = cache.store(stage_name="adult", source_sha256=str(index) * 64, source_path=self.source,
                                 report_root=report, commands=(("run", str(index)),),
                                 artifact_paths=(artifact,))
            entries.append(stored["entry"])
        manual = cache.cache_root / ("9" * 64) / "adult" / "manual"
        (manual / "snapshot").mkdir(parents=True)
        (manual / "snapshot" / "scan.json").write_bytes(b"s" * 900)
        (manual / "manifest.json").write_text('{"schema_version": 2}', encoding="utf-8")
        entries.append(manual)
        now = datetime.now(timezone.utc)
        for age, entry in enumerate(entries):
            stamp = (now - timedelta(hours=10 - age)).timestamp()
            os.utime(entry / "manifest.json", (stamp, stamp))
        walked = {entry: sum(path.stat().st_size for path in entry.rglob("*") if path.is_file())
                  for entry in entries}
        for entry in entries:
            self.assertEqual(stage_cache._entry_bytes(entry / "manifest.json"), walked[entry])
        # The same entries go as when every file was walked: oldest first, until under the cap.
        limit = sum(walked.values()) - walked[entries[0]] - 1
        walks = []
        original = Path.rglob
        with mock.patch.object(Path, "rglob", autospec=True,
                               side_effect=lambda path, pattern: walks.append(path) or original(path, pattern)):
            result = cache.prune(max_bytes=limit, now=now)
        self.assertEqual(walks, [manual])
        self.assertEqual(result["removed_entries"], 2)
        self.assertEqual([entry.exists() for entry in entries], [False, False, True, True])
        self.assertEqual(result["kept_bytes"], walked[entries[2]] + walked[manual])


if __name__ == "__main__":
    unittest.main()
