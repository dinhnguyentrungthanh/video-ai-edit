import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import golden_prefill  # noqa: E402


def track(routing, text, **extra):
    value = {"start_seconds": 10.0, "end_seconds": 12.0, "union_box": [10, 10, 60, 30], "routing": routing,
             "sample_text": [text], "ad_probability": 0.0, "persistent": False, "review_candidate": False,
             "semantic_top_label": "scene_text", "review_priority": "low", "suggested_decision": None}
    value.update(extra)
    return value


class DenseSuggestionTest(unittest.TestCase):
    def test_rolling_credits_are_dropped_unless_ad_like(self):
        manifest = {"sources": {"src": {"width": 1920, "height": 1080}},
                    "segments": [{"id": "S1", "source": "src", "start_seconds": 0.0, "end_seconds": 100.0}]}
        tracks = [
            track("LIKELY_CREDITS", "Costume Makers", ad_probability=0.2, persistent=True),
            track("LIKELY_TITLE_OVERLAY", "JEEDA BARFORD", persistent=True),
            track("LIKELY_CREDITS", "xem phim tai example.net", semantic_top_label="advertisement"),
            track("LIKELY_SCENE_TEXT", "A TimeWarner Company", ad_probability=0.15),
            track("LIKELY_SCENE_TEXT", "street sign", ad_probability=0.02),
            track("REVIEW_UNCERTAIN", "WARNER"),
        ]
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "dense/S1/text"
            base.mkdir(parents=True)
            (base / "text-scan.json").write_text(json.dumps(
                {"source_size": [1920, 1080], "analysis_size": [960, 540], "tracks": tracks}), encoding="utf-8")
            with patch.object(golden_prefill, "PREFILL", Path(temp)):
                rows = golden_prefill._dense_suggestions(manifest)
        self.assertEqual([r["labels"][0] for r in rows],
                         ["xem phim tai example.net", "A TimeWarner Company", "WARNER"])
        self.assertEqual(rows[0]["region_source_pixels"], {"x": 20, "y": 20, "width": 100, "height": 40})
        self.assertEqual({r["tier"] for r in rows}, {"dense"})


class SetOptionTest(unittest.TestCase):
    def tearDown(self):
        golden_prefill.use_set("v1")

    def test_set_is_accepted_before_or_after_the_command(self):
        self.assertEqual(golden_prefill.parse_args(["collect"]).set, "v1")
        self.assertEqual(golden_prefill.parse_args(["--set", "v1.1", "collect"]).set, "v1.1")
        args = golden_prefill.parse_args(["manifest", "--set", "v1.1", "--output", "x.json"])
        self.assertEqual((args.set, args.output), ("v1.1", "x.json"))
        with patch("sys.stderr"), self.assertRaises(SystemExit):
            golden_prefill.parse_args(["--set", "v2", "collect"])

    def test_use_set_switches_every_path(self):
        golden_prefill.use_set("v1.1")
        self.assertEqual(golden_prefill.MANIFEST, golden_prefill.GOLDEN_ROOT / "v1.1" / "segments.json")
        self.assertEqual(golden_prefill.SUGGESTIONS,
                         golden_prefill.ROOT / "reports/benchmarks/golden-v1.1/prefill/suggestions.json")
        golden_prefill.use_set("v1")
        self.assertEqual(golden_prefill.PREFILL, golden_prefill.ROOT / "reports/benchmarks/golden-v1/prefill")


def v1_manifest(sources):
    return {"schema_version": 1, "golden_set": "v1", "created_at": "2026-09-29T00:00:00+00:00", "sources": sources,
            "segments": [{"id": "S1", "source": "src", "start_seconds": 0.0, "end_seconds": 100.0, "split": "dev",
                          "purpose": "p"},
                         {"id": "S2", "source": "src", "start_seconds": 300.0, "end_seconds": 400.0, "split": "holdout",
                          "purpose": "p"}]}


SPECS = (("N1", "src", 100.0, 300.0, "dev", "chạm S1 và S2 nhưng không chồng"),
         ("N2", "src", 900.0, 1200.0, "holdout", "kéo quá cuối nguồn"))


class ExtensionManifestTest(unittest.TestCase):
    SOURCES = {"src": {"job_id": 1, "path": "input/a.mp4", "size_bytes": 10, "sha256": "a" * 64,
                       "duration_seconds": 1000.4567, "width": 1920, "height": 1080, "fps": 25.0,
                       "content_style": "live_action"}}

    def test_sources_are_copied_verbatim_and_the_parent_is_pinned(self):
        from biliflow.golden_set import canonical_sha256
        parent = v1_manifest(self.SOURCES)
        built = golden_prefill.extension_manifest("v1.1", parent, SPECS)
        self.assertEqual((built["golden_set"], built["extends"]), ("v1.1", "v1"))
        self.assertEqual(built["extends_manifest_sha256"], canonical_sha256(parent))
        self.assertEqual(json.dumps(built["sources"], sort_keys=True), json.dumps(parent["sources"], sort_keys=True))
        self.assertEqual([(s["id"], s["start_seconds"], s["end_seconds"]) for s in built["segments"]],
                         [("N1", 100.0, 300.0), ("N2", 900.0, 1000.456)])  # clamped to the source like v1

    def test_overlaps_duplicates_and_unknown_sources_are_refused(self):
        parent = v1_manifest(self.SOURCES)
        for specs, message in (((("N1", "src", 50.0, 150.0, "dev", ""),), "chồng thời gian"),
                               ((("S2", "src", 500.0, 600.0, "dev", ""),), "Trùng ID đoạn"),
                               ((("N1", "src", 500.0, 600.0, "dev", ""), ("N2", "src", 550.0, 650.0, "dev", "")),
                                "chồng thời gian"),
                               ((("N1", "nope", 500.0, 600.0, "dev", ""),), "not in the v1 manifest")):
            with self.subTest(message=message, specs=specs), self.assertRaisesRegex(Exception, message):
                golden_prefill.extension_manifest("v1.1", parent, specs)
        with self.assertRaises(RuntimeError):
            golden_prefill.extension_manifest("v1", parent, SPECS)  # v1 extends nothing


class BuildExtensionManifestTest(unittest.TestCase):
    """manifest --set v1.1: re-hash the v1 sources, refuse writes into annotations while pending."""

    def setUp(self):
        from biliflow.golden_set import write_json_atomic
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        video = self.root / "input" / "a.mp4"
        video.parent.mkdir()
        video.write_bytes(b"frames" * 100)
        sources = {"src": {"job_id": 1, "path": "input/a.mp4", "size_bytes": 600,
                           "sha256": hashlib.sha256(b"frames" * 100).hexdigest(), "duration_seconds": 1000.0,
                           "width": 1920, "height": 1080, "fps": 25.0, "content_style": "live_action"}}
        self.parent = v1_manifest(sources)
        write_json_atomic(self.root / "annotations/golden/v1/segments.json", self.parent)
        self.patches = [patch.object(golden_prefill, "ROOT", self.root),
                        patch.object(golden_prefill, "GOLDEN_ROOT", self.root / "annotations/golden"),
                        patch.dict("biliflow.golden_set.SET_SEGMENTS", {"v1.1": SPECS})]
        for item in self.patches:
            item.start()
        golden_prefill.use_set("v1.1")

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        golden_prefill.use_set("v1")
        self.temp.cleanup()

    def run_manifest(self, *extra):
        with patch("builtins.print"):
            golden_prefill.build_manifest(golden_prefill.parse_args(["--set", "v1.1", "manifest", *extra]))

    def test_dry_run_writes_only_the_output_file(self):
        from biliflow.golden_set import read_json, validate_manifest
        target = self.root / "scratch" / "segments.json"
        self.run_manifest("--output", str(target))
        built = validate_manifest(read_json(target))
        self.assertEqual((built["golden_set"], [s["id"] for s in built["segments"]]), ("v1.1", ["N1", "N2"]))
        self.assertEqual(built["sources"], self.parent["sources"])
        self.assertFalse((self.root / "annotations/golden/v1.1").exists())
        self.run_manifest("--output", str(target))  # same content again: unchanged, not an error

    def test_pending_decision_blocks_the_real_directory(self):
        with patch("biliflow.golden_set.GOLDEN_V1_1_PENDING_DECISION", True):
            with self.assertRaisesRegex(RuntimeError, "PENDING_DECISION"):
                self.run_manifest()
            with self.assertRaisesRegex(RuntimeError, "PENDING_DECISION"):
                self.run_manifest("--output", str(self.root / "annotations/golden/v1.1/segments.json"))
        self.assertFalse((self.root / "annotations/golden/v1.1").exists())
        with patch("biliflow.golden_set.GOLDEN_V1_1_PENDING_DECISION", False):
            self.run_manifest()
            self.assertTrue(golden_prefill.MANIFEST.exists())
            with patch.dict("biliflow.golden_set.SET_SEGMENTS", {"v1.1": SPECS[:1]}), \
                    self.assertRaisesRegex(RuntimeError, "different content"):
                self.run_manifest()  # an existing, different manifest is never replaced

    def test_a_changed_source_file_is_refused(self):
        (self.root / "input" / "a.mp4").write_bytes(b"FRAMES" * 100)  # same size, other content
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.run_manifest("--output", str(self.root / "scratch" / "segments.json"))
        self.assertFalse((self.root / "scratch").exists())


if __name__ == "__main__":
    unittest.main()
