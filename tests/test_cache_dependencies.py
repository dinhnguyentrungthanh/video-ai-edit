import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.cache_dependencies import stage_source_paths
from biliflow.stage_cache import CACHEABLE_STAGES, StageArtifactCache


class CacheDependencyTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.code = self.root / "src/biliflow"
        self.code.mkdir(parents=True)
        for name, body in {
            "__init__": "VERSION = 1",
            "cli": "# dispatcher",
            "license_policy": "# license policy",
            "scanner": "from biliflow.shared import value",
            "textscan": "def scan():\n    from biliflow.text_semantics import classify",
            "text_semantics": "classify = 1",
            "shared": "value = 1",
            "visual_logo_scanner": "from biliflow.brand_memory import memory",
            "brand_memory": "memory = 1",
            "control_center": "# dashboard",
            "vlm_confirmation": "# confirmer",
            "ad_candidate_pipeline": "# grounding",
            "florence_regions": "# boxes",
        }.items():
            (self.code / (name + ".py")).write_text(body, encoding="utf-8")
        (self.root / "scripts").mkdir()
        (self.root / "scripts/localize_visual_logo_report.py").write_text("# localizer")
        self.source = self.root / "input.mp4"
        self.source.write_bytes(b"immutable input")
        self.report = self.root / "reports/jobs/run-1"
        self.artifact = self.report / "adult/scan.json"
        self.artifact.parent.mkdir(parents=True)
        self.artifact.write_text('{"status":"COMPLETED"}')
        self.cache = StageArtifactCache(self.root)

    def arguments(self, stage="adult", commands=None):
        return dict(stage_name=stage, source_sha256="a" * 64,
                    source_path=self.source, report_root=self.report,
                    commands=commands or (("run", "--input", str(self.source)),),
                    artifact_paths=(self.artifact,))

    def test_dashboard_change_reuses_cache_but_transitive_scanner_edit_invalidates(self):
        args = self.arguments()
        old = self.cache.key(**args)
        (self.code / "control_center.py").write_text("# new UI")
        self.assertEqual(old, self.cache.key(**args))
        (self.code / "shared.py").write_text("value = 2")
        self.assertNotEqual(old, self.cache.key(**args))

    def test_lazy_import_invalidation_is_stage_scoped(self):
        adult = self.cache.key(**self.arguments())
        text = self.cache.key(**self.arguments("text"))
        (self.code / "text_semantics.py").write_text("classify = 2")
        self.assertEqual(adult, self.cache.key(**self.arguments()))
        self.assertNotEqual(text, self.cache.key(**self.arguments("text")))

    def test_dispatcher_change_invalidates_stages(self):
        old = self.cache.key(**self.arguments())
        (self.code / "cli.py").write_text("# different dispatcher")
        self.assertNotEqual(old, self.cache.key(**self.arguments()))

    def test_config_seed_and_model_changes_invalidate_same_instance(self):
        args = self.arguments()
        for relative in ("config/text_review_policy.json", "annotations/text_semantics_seed_v1.json",
                         "models/test/manifest.json", "models/test/config.json", "models/test/weights.bin",
                         "scripts/env.ps1"):
            with self.subTest(relative=relative):
                path = self.root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"one")
                old = self.cache.key(**args)
                path.write_bytes(b"two")
                self.assertNotEqual(old, self.cache.key(**args))

    def test_brand_memory_change_invalidates_logo_only(self):
        adult = self.cache.key(**self.arguments())
        logo = self.cache.key(**self.arguments("visual_logo"))
        path = self.root / "state/brand-memory.json"
        path.parent.mkdir()
        path.write_text('{"records": [1]}')
        self.assertEqual(adult, self.cache.key(**self.arguments()))
        self.assertNotEqual(logo, self.cache.key(**self.arguments("visual_logo")))

    def test_upstream_report_content_invalidation_and_internal_output_exclusion(self):
        scan = self.report / "scan.json"
        middle = self.report / "scan-florence.json"
        final = self.report / "scan-localized.json"
        scan.write_text('{"intervals": [1]}')
        args = self.arguments("localize_logo", (
            ("localize", "--report", str(scan), "--output", str(middle)),
            ("ground", "--report", str(middle), "--output", str(final)),
        ))
        old = self.cache.key(**args)
        middle.write_text('{"generated": true}')
        self.assertEqual(old, self.cache.key(**args))
        scan.write_text('{"intervals": [2]}')
        self.assertNotEqual(old, self.cache.key(**args))
        scan.unlink()
        with self.assertRaises(FileNotFoundError):
            self.cache.key(**args)

    def test_source_and_command_changes_cannot_reuse(self):
        args = self.arguments()
        old = self.cache.key(**args)
        self.assertNotEqual(old, self.cache.key(**{**args, "source_sha256": "b" * 64}))
        self.assertNotEqual(old, self.cache.key(**{**args, "commands": (("run", "--fps", "8"),)}))

    def test_dependencies_changed_during_execution_not_stored(self):
        args = self.arguments()
        old = self.cache.key(**args)
        (self.code / "shared.py").write_text("value = 3")
        self.assertIsNone(self.cache.store(**args, expected_key=old))
        self.assertFalse(self.cache.cache_root.exists())

    def test_ui_only_update_restores_exact_report_and_image(self):
        args = self.arguments()
        image = self.artifact.parent / "frame.jpg"
        image.write_bytes(b"original image")
        self.cache.store(**args)
        (self.code / "control_center.py").write_text("# new layout")
        new_root = self.root / "reports/jobs/run-2"
        new_artifact = new_root / "adult/scan.json"
        hit = self.cache.restore(**{**args, "report_root": new_root, "artifact_paths": (new_artifact,)})
        self.assertIsNotNone(hit)
        self.assertEqual(new_artifact.read_bytes(), self.artifact.read_bytes())
        self.assertEqual((new_artifact.parent / "frame.jpg").read_bytes(), image.read_bytes())

    def test_unresolved_dynamic_or_invalid_imports_fall_back_to_all_code(self):
        for body in ("import biliflow.missing", "exec('pass')", "this is invalid python!",
                     "from importlib import import_module as load\nload('biliflow.shared')"):
            with self.subTest(body=body):
                (self.code / "scanner.py").write_text(body)
                self.assertIn(self.code / "control_center.py", stage_source_paths(self.root, "adult"))

    def test_relative_and_package_imports_are_traversed(self):
        (self.code / "scanner.py").write_text("from . import shared\nfrom biliflow.helpers import work")
        package = self.code / "helpers"
        package.mkdir()
        (package / "__init__.py").write_text("from .work import value")
        (package / "work.py").write_text("value = 1")
        paths = stage_source_paths(self.root, "adult")
        self.assertIn(package / "work.py", paths)
        self.assertIn(self.code / "shared.py", paths)
        self.assertNotIn(self.code / "control_center.py", paths)


class RepositoryCacheScopeTests(unittest.TestCase):
    """Dashboard, queue and review-page edits must never invalidate scan caches."""

    # Control-plane modules edited by the dashboard/queue work (2026-10-02, batch 1;
    # 2026-10-03, batch 2 adds the shared export dialog; 2026-10-03, batch 3 adds
    # source cleanup; 2026-10-03, batch 4 adds source archive).
    CONTROL_PLANE = (
        "control_center.py", "scheduler.py", "job_store.py", "job_import.py",
        "review_workflow.py", "export_dialog.py",
        "export_guards.py", "recycle_bin.py", "source_cleanup.py",
        "source_archive.py", "source_archive_files.py", "source_archive_restore.py",
    )

    def test_control_plane_modules_are_in_no_scan_stage_key(self):
        root = Path(__file__).resolve().parents[1]
        source = root / "src" / "biliflow"
        everything = set(source.rglob("*.py"))
        for stage in sorted(CACHEABLE_STAGES):
            with self.subTest(stage=stage):
                paths = set(stage_source_paths(root, stage))
                # A fallback to every source file would make any edit invalidate the stage.
                self.assertFalse(everything <= paths)
                self.assertEqual({path.name for path in paths} & set(self.CONTROL_PLANE), set())

    def test_platform_modules_stay_out_of_logo_and_safety_keys(self):
        # Batch 4a (2026-10-03): only the text stage (textscan -> platform_names) changes
        # key; the build-time platform probe, cards, memory and admin page stay out of all.
        root = Path(__file__).resolve().parents[1]
        build_time = {
            "platform_logos.py", "platform_cards.py", "platform_memory.py", "logo_memory_admin.py",
        }
        for stage in sorted(CACHEABLE_STAGES):
            with self.subTest(stage=stage):
                names = {path.name for path in stage_source_paths(root, stage)}
                self.assertEqual(names & build_time, set())
                self.assertEqual("platform_names.py" in names, stage == "text")


if __name__ == "__main__":
    unittest.main()
