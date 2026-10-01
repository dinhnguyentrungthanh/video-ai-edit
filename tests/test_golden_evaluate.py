import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import evaluate_golden  # noqa: E402
from biliflow.golden_scoring import compare, score  # noqa: E402
from biliflow.golden_set import canonical_sha256, empty_labels, write_json_atomic  # noqa: E402

SHA = "a" * 64


def v1_manifest():
    return {"schema_version": 1, "golden_set": "v1",
            "sources": {"troy": {"path": "input/t.mp4", "size_bytes": 10, "sha256": SHA, "duration_seconds": 1000.0,
                                 "width": 1920, "height": 1080, "fps": 25.0, "content_style": "live_action"}},
            "segments": [{"id": "T1", "source": "troy", "start_seconds": 0.0, "end_seconds": 100.0, "split": "dev"},
                         {"id": "T2", "source": "troy", "start_seconds": 200.0, "end_seconds": 300.0,
                          "split": "holdout"}]}


def v11_manifest(parent):
    return {"schema_version": 1, "golden_set": "v1.1", "extends": "v1",
            "extends_manifest_sha256": canonical_sha256(parent), "sources": parent["sources"],
            "segments": [{"id": "T5", "source": "troy", "start_seconds": 400.0, "end_seconds": 500.0, "split": "dev"}]}


def event(id, segment, category, start, end):
    return {"id": id, "segment_id": segment, "category": category, "start_seconds": start, "end_seconds": end,
            "region_source_pixels": None, "expected_action": "CUT", "severity": "must_catch", "ambiguous": False,
            "notes": ""}


class EvaluateGoldenTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.golden = self.root / "annotations/golden"
        self.v1 = v1_manifest()
        self.v11 = v11_manifest(self.v1)
        self.write_labels("v1", self.v1, [event("gs-T1-0001", "T1", "adult", 10, 20),
                                          event("gs-T2-0001", "T2", "visual_logo", 210, 220)], ("T1", "T2"), 457)
        self.write_labels("v1.1", self.v11, [event("gs-T5-0001", "T5", "gore", 410, 420)], ("T5",), 3)
        self.queue = self.root / "reports/jobs/trial/review-queue.json"
        write_json_atomic(self.queue, {"source": {"sha256": SHA}, "detection_scope": {"selected": [
            "advertising", "adult", "gore", "violence"]}, "items": [
            {"id": "A", "category": "adult", "start_seconds": 9, "end_seconds": 21, "suggested_decision": "CUT"},
            {"id": "L", "category": "visual_logo", "start_seconds": 210, "end_seconds": 220,
             "suggested_decision": "CUT"},
            {"id": "G", "category": "gore", "start_seconds": 450, "end_seconds": 460}]})
        self.patches = [patch.object(evaluate_golden, "ROOT", self.root),
                        patch.object(evaluate_golden, "GOLDEN_ROOT", self.golden),
                        patch.object(evaluate_golden, "BENCHMARKS", self.root / "reports/benchmarks")]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def write_labels(self, name, manifest, events, complete, revision):
        write_json_atomic(self.golden / name / "segments.json", manifest)
        labels = empty_labels(manifest)
        labels.update(events=events, revision=revision)
        for segment_id in complete:
            labels["segments"][segment_id]["status"] = "complete"
        write_json_atomic(self.golden / name / "events.json", labels)
        return labels

    def score(self, **changes):
        args = argparse.Namespace(queue=[str(self.queue)], trial=None, no_frames=True, set=None, labels=None,
                                  tag="t")
        vars(args).update(changes)
        with contextlib.redirect_stdout(io.StringIO()):
            out = evaluate_golden.score_command(args)
        return {path.name: json.loads(path.read_text(encoding="utf-8")) for path in out.glob("*.json")}, out

    def test_default_scores_every_set_with_a_sub_card_per_set(self):
        cards, out = self.score()
        self.assertEqual(sorted(cards), ["scorecard-v1.1.json", "scorecard-v1.json", "scorecard.json"])
        self.assertEqual(sorted(p.name for p in out.glob("*.md")), ["scorecard-v1.1.md", "scorecard-v1.md",
                                                                     "scorecard.md"])
        combined, v1 = cards["scorecard.json"], cards["scorecard-v1.json"]
        self.assertEqual(combined["labels_revision"], {"v1": 457, "v1.1": 3})
        self.assertEqual(set(combined["sets"]), {"v1", "v1.1"})
        self.assertTrue(combined["sets"]["v1.1"]["labels_path"].endswith("events.json"))
        labels = json.loads((self.golden / "v1/events.json").read_text(encoding="utf-8"))
        alone = score(self.v1, labels, {"troy": json.loads(self.queue.read_text(encoding="utf-8"))})
        self.assertEqual((v1["manifest_sha256"], v1["labels_fingerprint"], v1["metrics"]),
                         (canonical_sha256(self.v1), alone["labels_fingerprint"], alone["metrics"]))
        result = compare(alone, v1)
        self.assertEqual((result["comparable"], result["label_changes"]), (True, []))
        self.assertEqual({l["id"]: l["status"] for l in cards["scorecard-v1.1.json"]["labels"]},
                         {"gs-T5-0001": "missed"})
        self.assertEqual(v1["queues"]["troy"]["path"], "reports/jobs/trial/review-queue.json")

    def test_set_v1_alone_reproduces_a_v1_card(self):
        cards, _ = self.score(set=["v1"])
        self.assertEqual(sorted(cards), ["scorecard-v1.json", "scorecard.json"])
        combined, v1 = cards["scorecard.json"], cards["scorecard-v1.json"]
        self.assertEqual(combined["manifest_sha256"], canonical_sha256(self.v1))
        self.assertEqual((combined["metrics"], combined["labels_fingerprint"], combined["labels_revision"]),
                         (v1["metrics"], v1["labels_fingerprint"], 457))
        self.assertEqual(combined["labels_path"], v1["labels_path"])

    def test_labels_option_accepts_bare_v1_path_and_set_prefix(self):
        throwaway = self.root / "temp/events.json"
        write_json_atomic(throwaway, empty_labels(self.v11))
        cards, _ = self.score(labels=[f"v1.1={throwaway}"])
        self.assertEqual(cards["scorecard-v1.1.json"]["labels"], [])
        self.assertEqual(cards["scorecard.json"]["sets"]["v1.1"]["labels_path"], str(throwaway.resolve()))
        self.assertEqual(evaluate_golden._label_overrides([str(throwaway)]), {"v1": throwaway})
        with self.assertRaises(ValueError):
            evaluate_golden._label_overrides(["a.json", "v1=b.json"])

    def test_troy_trial_runs_every_group_unless_narrowed(self):
        manifest, labels, _ = evaluate_golden._load(argparse.Namespace(set=None, labels=None))
        self.assertEqual(evaluate_golden.troy_detector_scope(None, manifest, labels),
                         (["advertising", "adult", "gore", "violence"], {}))
        self.assertEqual(evaluate_golden.troy_detector_scope(["advertising"], manifest, labels),
                         (["advertising"], {"adult": 1, "gore": 1}))


if __name__ == "__main__":
    unittest.main()
