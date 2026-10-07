"""Anime gore tag evidence, card hint and triage (docs/ANIME_GORE_PLAN.md steps 1-3)."""
import io
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from biliflow import animation_policy as policy
from biliflow.animation_policy import (
    GORE_BLOOD_FAMILY_LABELS,
    GORE_LABELS,
    attach_gore_tag_evidence,
    gore_frame_tag_evidence,
    gore_interval_tag_evidence,
    gore_policy_decision,
    gore_tag_evidence_policy,
    probabilistic_union,
)
from biliflow.gore_triage import (
    GORE_TRIAGE_LEVEL,
    GORE_TRIAGE_LEVELS,
    GORE_TRIAGE_MODEL_REPO,
    GORE_TRIAGE_MODEL_REVISION,
    gore_evidence_hint,
    gore_scan_is_calibrated,
    normalize_gore_triage_level,
)
from biliflow.intervals import group_hits
from biliflow.review_workflow import (
    _interactive_html,
    annotate_gore_tag_evidence,
    build_review_queue,
    record_review_decision,
    triage_anime_gore_items,
)
from biliflow.scanner import temporal_confirm_hits


def _evidence(blood, injury=0.1, corpse=0.001, frames=4, label="blood"):
    return {"blood_family_max": blood, "injury_max": injury, "corpse_max": corpse,
            "confirmed_frames": frames, "strongest_blood_label": label,
            "strongest_blood_label_score": blood}


class GoreEvidencePolicyTests(unittest.TestCase):
    def test_blood_family_is_the_blood_tags_and_pool_of_blood(self):
        derived = tuple(label for label in GORE_LABELS
                        if label.startswith("blood") or label == "pool_of_blood")
        self.assertEqual(GORE_BLOOD_FAMILY_LABELS, derived)
        self.assertEqual(len(GORE_BLOOD_FAMILY_LABELS), 16)
        self.assertNotIn("pink_blood", GORE_BLOOD_FAMILY_LABELS)
        self.assertIn(policy.GORE_INJURY_LABEL, GORE_LABELS)
        self.assertIn(policy.GORE_CORPSE_LABEL, GORE_LABELS)
        block = gore_tag_evidence_policy()
        self.assertEqual(block["blood_family_labels"], list(GORE_BLOOD_FAMILY_LABELS))
        self.assertEqual(block["version"], policy.GORE_TAG_EVIDENCE_VERSION)

    def test_frame_evidence_is_the_union_of_the_blood_family(self):
        probabilities = {"blood": 0.2, "blood_on_face": 0.5, "pink_blood": 0.9, "injury": 0.3,
                         "corpse": 0.04, "guro": 0.7}
        frame = gore_frame_tag_evidence(probabilities)
        self.assertAlmostEqual(frame["blood_family"], 1 - 0.8 * 0.5)
        self.assertEqual((frame["injury"], frame["corpse"]), (0.3, 0.04))
        self.assertEqual((frame["blood_label"], frame["blood_label_score"]), ("blood_on_face", 0.5))

    def test_interval_evidence_is_the_maximum_over_its_hits(self):
        hits = [{"tag_evidence": gore_frame_tag_evidence(values)} for values in (
            {"blood": 0.1, "injury": 0.6, "corpse": 0.0},
            {"blood_stain": 0.3, "injury": 0.2, "corpse": 0.05},
        )]
        evidence = gore_interval_tag_evidence(hits)
        self.assertEqual(evidence, {
            "blood_family_max": 0.3, "injury_max": 0.6, "corpse_max": 0.05, "confirmed_frames": 2,
            "strongest_blood_label": "blood_stain", "strongest_blood_label_score": 0.3,
        })
        self.assertIsNone(gore_interval_tag_evidence([*hits, {}]))
        self.assertIsNone(gore_interval_tag_evidence([]))

    def test_attached_evidence_follows_group_hits_and_changes_nothing_else(self):
        def hit(second, blood):
            return {"frame_index": second * 2, "timestamp_seconds": float(second), "score": 0.3 + blood,
                    "reason": "high_score", "predicted_label": "blood",
                    "tag_evidence": gore_frame_tag_evidence({"blood": blood, "injury": 0.1})}

        hits = [hit(1, 0.1), hit(2, 0.4), hit(5, 0.2), hit(20, 0.05), hit(24, 0.6), hit(40, 0.9)]
        plain = group_hits(hits, 6.0, 1.0, 100.0)
        intervals = group_hits(hits, 6.0, 1.0, 100.0)
        attach_gore_tag_evidence(intervals, hits, 6.0)
        self.assertEqual([{k: v for k, v in i.items() if k != "gore_tag_evidence"} for i in intervals], plain)
        self.assertEqual([i["gore_tag_evidence"]["blood_family_max"] for i in intervals], [0.4, 0.6, 0.9])
        self.assertEqual([i["gore_tag_evidence"]["confirmed_frames"] for i in intervals], [3, 2, 1])
        self.assertEqual([len(group) for group in policy._gore_hit_groups(hits, 6.0)],
                         [i["sample_count"] for i in plain])
        # Groups that no longer match the intervals leave every interval without evidence.
        stale = group_hits(hits[:-1], 6.0, 1.0, 100.0)
        attach_gore_tag_evidence(stale, hits, 6.0)
        self.assertFalse(any("gore_tag_evidence" in interval for interval in stale))
        # Same sizes but another strongest score (other frames) is a mismatch too.
        shifted = group_hits(hits, 6.0, 1.0, 100.0)
        shifted[1]["max_score"] = round(shifted[1]["max_score"] - 0.01, 6)
        attach_gore_tag_evidence(shifted, hits, 6.0)
        self.assertFalse(any("gore_tag_evidence" in interval for interval in shifted))

    def test_regrouping_matches_group_hits_on_random_hits(self):
        # The evidence regroups the hits itself (intervals.py stays out of the scan cache keys);
        # it must give group_hits' groups, gaps of exactly the merge gap and equal times included.
        generator = np.random.default_rng(20261007)
        for _case in range(300):
            count = int(generator.integers(0, 40))
            times = np.round(np.sort(generator.uniform(0.0, 120.0, size=count)) * 2) / 2
            hits = [{"timestamp_seconds": float(t), "score": float(generator.uniform(0.05, 1.0)),
                     "tag_evidence": gore_frame_tag_evidence({"blood": float(generator.uniform())})}
                    for t in generator.permutation(times)]
            gap = float(generator.choice([0.5, 1.0, 6.0]))
            intervals = group_hits(hits, gap, 1.0, 130.0)
            groups = policy._gore_hit_groups(hits, gap)
            self.assertEqual([len(group) for group in groups], [i["sample_count"] for i in intervals])
            self.assertEqual([round(max(h["score"] for h in group), 6) for group in groups],
                             [i["max_score"] for i in intervals])
            attach_gore_tag_evidence(intervals, hits, gap)
            self.assertTrue(all("gore_tag_evidence" in interval for interval in intervals))


class _RawFfmpeg:
    def __init__(self, frames):
        self.stdout = io.BytesIO(b"".join(frame.tobytes() for frame in frames))
        self.stderr = io.BytesIO(b"")

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0

    def terminate(self):
        pass

    def kill(self):
        pass


class ScannerEvidenceTests(unittest.TestCase):
    """The real scanner on a fake tagger whose per-frame tag probabilities are designed."""

    FRAMES = 26

    @staticmethod
    def designed(index):
        """Tag probabilities of frame ``index`` (1 fps, so frame index = second)."""
        if 2 <= index <= 6:  # injury only: a scratch without blood
            return {"injury": 0.30 + 0.01 * index, "blood": 0.01 + 0.001 * index, "corpse": 0.002}
        if 15 <= index <= 19:  # blood and a little corpse
            return {"blood": 0.40 + 0.05 * (index - 15), "blood_on_face": 0.1, "corpse": 0.02 + 0.01 * (index == 17),
                    "injury": 0.05}
        if index == 23:  # one isolated, unconfirmed hit: its evidence must stay out
            return {"blood": 0.99, "corpse": 0.9, "injury": 0.9}
        return {}

    def run_scan(self, directory):
        from biliflow.animation_safety_scanner import scan_animation_safety

        labels = sorted(set(policy.GORE_LABELS + policy.GORE_CONTEXT_LABELS + policy.DIRECT_VIOLENCE_LABELS
                            + policy.DANGER_LABELS + policy.ADULT_EXPLICIT_LABELS)) + ["general"]
        designed = self.designed

        class Tagger:
            def to(self, device):
                return self

            def eval(self):
                return self

            def load_state_dict(self, state):
                return None

            def __call__(self, pixels):
                import torch

                rows = []
                for frame in pixels:
                    value = (float(frame.mean()) * 0.5 + 0.5) * 255.0
                    index = int(round((value - 5.0) / 9.0))
                    probabilities = designed(index)
                    row = [probabilities.get(label, 1e-5) for label in labels]
                    rows.append([math.log(p / (1 - p)) for p in row])
                return torch.tensor(rows, dtype=torch.float32)

        frames = [np.full((256, 256, 3), 5 + 9 * index, dtype=np.uint8) for index in range(self.FRAMES)]
        root = Path(directory)
        (root / "reports").mkdir()
        model_dir = root / "tagger"
        model_dir.mkdir()
        (model_dir / "config.json").write_text(json.dumps({
            "architecture": "fake", "num_classes": len(labels),
            "pretrained_cfg": {"input_size": [3, 32, 32], "interpolation": "bicubic", "crop_pct": 1.0,
                               "mean": [0.5, 0.5, 0.5], "std": [0.5, 0.5, 0.5]},
        }), encoding="utf-8")
        (model_dir / "manifest.json").write_text(json.dumps({"model": "tagger"}), encoding="utf-8")
        (model_dir / "selected_tags.csv").write_text(
            "tag_id,name,category,count\n" + "".join(f"{i},{name},0,1\n" for i, name in enumerate(labels)),
            encoding="utf-8")
        video = root / "anime.mkv"
        video.write_bytes(b"animation source bytes")
        for name in ("ffmpeg.exe", "ffprobe.exe"):
            (root / name).write_bytes(b"x")
        with mock.patch("biliflow.animation_safety_scanner.timm.create_model", return_value=Tagger()), \
                mock.patch("biliflow.animation_safety_scanner.load_file", return_value={}), \
                mock.patch("biliflow.animation_safety_scanner.require_capacity"), \
                mock.patch("biliflow.animation_safety_scanner.probe_video", return_value={}), \
                mock.patch("biliflow.animation_safety_scanner.duration_seconds", return_value=float(self.FRAMES)), \
                mock.patch("biliflow.animation_safety_scanner.subprocess.Popen", return_value=_RawFfmpeg(frames)):
            summary = scan_animation_safety(
                project_root=root, input_path=video, report_dir=root / "reports" / "job",
                model_path=model_dir, ffmpeg_path=root / "ffmpeg.exe", ffprobe_path=root / "ffprobe.exe",
                sample_fps=1.0, batch_size=4, device_name="cpu",
            )
        return {category: json.loads(Path(item["report"]).read_text(encoding="utf-8"))
                for category, item in summary["reports"].items()}

    def expected_gore(self):
        """Production policy, confirmation and grouping recomputed without the scanner."""
        hits = []
        for index in range(self.FRAMES):
            values = {label: 1e-5 for label in GORE_LABELS}
            values.update(self.designed(index))
            accepted, score, _context, reason = gore_policy_decision(values)
            if accepted:
                hits.append({"frame_index": index, "timestamp_seconds": float(index), "score": score,
                             "reason": reason, "predicted_label": max(GORE_LABELS, key=values.get),
                             "thumbnail": f"thumbnails/frame-{index:08d}-{float(index):.3f}s.jpg",
                             "tag_evidence": gore_frame_tag_evidence(values)})
        confirmed = sorted(
            [*temporal_confirm_hits([h for h in hits if h["reason"] == "high_score"], 5, 3),
             *temporal_confirm_hits([h for h in hits if h["reason"] == "context_confirmed"], 5, 4)],
            key=lambda hit: hit["frame_index"])
        return hits, confirmed, group_hits(confirmed, 6.0, 1.0, float(self.FRAMES))

    def test_scanner_records_evidence_without_changing_any_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            reports = self.run_scan(directory)
        raw, confirmed, expected = self.expected_gore()
        self.assertIn(23, [hit["frame_index"] for hit in raw])
        self.assertNotIn(23, [hit["frame_index"] for hit in confirmed])
        gore = reports["gore"]
        self.assertEqual(len(gore["intervals"]), 2)
        for interval, reference in zip(gore["intervals"], expected):
            for key in ("start_seconds", "end_seconds", "sample_count", "predicted_label", "reason",
                        "strongest_frame"):
                self.assertEqual(interval[key], reference[key], key)
            self.assertAlmostEqual(interval["max_score"], reference["max_score"], places=5)
            self.assertEqual(set(interval) - set(reference), {"gore_tag_evidence"})
        first, second = (interval["gore_tag_evidence"] for interval in gore["intervals"])
        for recorded, group in zip((first, second), policy._gore_hit_groups(confirmed, 6.0)):
            reference = gore_interval_tag_evidence(group)
            self.assertEqual(recorded["confirmed_frames"], reference["confirmed_frames"])
            self.assertEqual(recorded["strongest_blood_label"], reference["strongest_blood_label"])
            for key in ("blood_family_max", "injury_max", "corpse_max", "strongest_blood_label_score"):
                self.assertAlmostEqual(recorded[key], reference[key], places=5, msg=key)
        self.assertAlmostEqual(first["injury_max"], 0.36, places=5)
        self.assertAlmostEqual(first["corpse_max"], 0.002, places=5)
        self.assertEqual(first["confirmed_frames"], 5)
        self.assertAlmostEqual(second["blood_family_max"],
                               probabilistic_union([0.60, 0.1] + [1e-5] * 14), places=5)
        self.assertAlmostEqual(second["corpse_max"], 0.03, places=5)  # frame 23 (0.9) is not confirmed
        self.assertEqual(second["strongest_blood_label"], "blood")
        self.assertEqual(gore["gore_tag_evidence_policy"], gore_tag_evidence_policy())
        self.assertEqual(gore["temporal_confirmation"]["confirmed_hit_count"], len(confirmed))
        for category in ("adult", "violence"):
            self.assertNotIn("gore_tag_evidence_policy", reports[category])
            self.assertFalse(any("gore_tag_evidence" in i for i in reports[category]["intervals"]))


def _gore_payload(source, intervals, **extra):
    payload = {
        "status": "COMPLETED", "scan_type": "gore", "input": str(source), "input_sha256": "abc",
        "duration_seconds": 2000, "sample_fps": 2.0, "threshold": 0.2, "content_style": "animation",
        "merge_gap_seconds": 6.0,
        "temporal_confirmation": {"enabled": True, "window_frames": 5, "minimum_positive_frames": 3},
        "two_tier_policy": {"high_threshold": 0.2, "low_threshold": 0.05, "context_threshold": 0.02,
                            "cooccurrence_threshold": 0.03, "high_score_temporal_minimum_hits": 3,
                            "context_temporal_minimum_hits": 4},
        "model": {"repo_id": GORE_TRIAGE_MODEL_REPO, "revision": GORE_TRIAGE_MODEL_REVISION},
        "runtime": {"torch": "2.x", "cuda": "12.x", "gpu": "test", "precision": "fp16"},
        "gore_tag_evidence_policy": gore_tag_evidence_policy(),
        "intervals": intervals,
    }
    payload.update(extra)
    return payload


def _interval(start, evidence=None, *, length=4.0, label="injury"):
    interval = {"start_seconds": start, "end_seconds": start + length, "max_score": 0.3,
                "sample_count": 4, "predicted_label": label, "reason": "high_score"}
    if evidence is not None:
        interval["gore_tag_evidence"] = evidence
    return interval


class GoreHintTests(unittest.TestCase):
    def test_hint_reads_the_evidence(self):
        cases = {
            "Tagger: thấy máu": _evidence(0.9, corpse=0.01),
            "Tagger: thấy máu · có thể là xác": _evidence(0.9, corpse=0.2),
            "Tagger: chỉ vết thương, không thấy máu": _evidence(0.03, injury=0.4),
            "Tagger: không thấy máu · có thể là xác": _evidence(0.02, injury=0.0, corpse=0.12),
            "Tagger: máu yếu, nên xem kỹ": _evidence(0.2, injury=0.4),
            # protected by C1's corpse level (0.0104) but below the strong 0.05 hint
            "Tagger: không thấy máu · có dấu hiệu xác (yếu), nên xem kỹ": _evidence(0.02, injury=0.0, corpse=0.03),
        }
        for text, evidence in cases.items():
            with self.subTest(text=text):
                self.assertEqual(gore_evidence_hint(evidence), text)
        self.assertIsNone(gore_evidence_hint(None))
        self.assertIsNone(gore_evidence_hint({"blood_family_max": "x"}))
        self.assertIsNone(gore_evidence_hint(_evidence(float("nan"))))

    def test_the_hint_is_shown_by_dashboard_v2_and_the_classic_page_is_unchanged(self):
        # Dashboard V2 is the review dialog (S10 in dashboard_v2/review-core.js); the classic page stays
        # byte-identical as its fallback (D2, tests/fixtures/dashboard_v2_classic_pages.json).
        # The V2 behaviour itself is checked by dashboard_v2/verify-review.cjs (S10).
        self.assertNotIn("gore_hint", _interactive_html("token"))
        cards = (Path(__file__).resolve().parents[1] / "dashboard_v2" / "review-cards.js").read_text(encoding="utf-8")
        self.assertIn("x.gore_hint", cards)


class GoreTriageTests(unittest.TestCase):
    MOVE = _evidence(0.03, injury=0.2, corpse=0.001)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "input").mkdir()
        self.source = self.root / "input" / "source.mp4"
        self.source.write_bytes(b"source")
        self.job = self.root / "reports" / "jobs" / "job" / "animation-safety"
        self.gore = self.job / "gore" / "scan.json"
        self.violence = self.job / "violence" / "scan.json"
        for path in (self.gore, self.violence):
            path.parent.mkdir(parents=True)
        self.violence.write_text(json.dumps(dict(
            _gore_payload(self.source, [_interval(100, label="fighting"), _interval(1500, label="punching")]),
            scan_type="violence")), encoding="utf-8")
        self.queue = self.root / "reports" / "jobs" / "job" / "review-queue.json"

    def tearDown(self):
        self.temporary.cleanup()

    def intervals(self):
        return [
            _interval(100, self.MOVE),                                   # 0 scratch only: moves
            _interval(200, _evidence(0.9, corpse=0.01), label="blood"),  # 1 blood seen
            _interval(300, _evidence(0.02, injury=0.0, corpse=0.2)),     # 2 corpse seen
            _interval(400, self.MOVE),                                   # 3 card 400-414 with 4,
            _interval(410, _evidence(0.5, corpse=0.001)),                # 4 whose blood blocks it
            _interval(500, self.MOVE),                                   # 5 card 500-516 with 6:
            _interval(512, _evidence(0.06, injury=0.3, corpse=0.0)),     # 6 both qualify, card moves
            _interval(600),                                              # 7 no evidence
            _interval(700, _evidence(0.0716, corpse=0.0104)),            # 8 exactly on the thresholds
            _interval(800, _evidence(0.0717, corpse=0.0)),               # 9 just above (blood)
            _interval(900, _evidence(0.01, corpse=0.0105)),              # 10 just above (corpse)
        ]

    def write(self, intervals=None, **extra):
        self.gore.write_text(json.dumps(_gore_payload(self.source, intervals or self.intervals(), **extra)),
                             encoding="utf-8")

    def build(self, style="animation", level="no_blood_no_corpse"):
        return build_review_queue(project_root=self.root, report_paths=[self.gore, self.violence],
                                  queue_path=self.queue, content_style=style, gore_triage_level=level)

    @staticmethod
    def starts(items, category="gore"):
        return sorted(item["start_seconds"] for item in items if item["category"] == category)

    def test_default_level_is_c1(self):
        # The user turned C1 on 2026-10-07 after gates 4.1-4.7 (docs/ANIME_GORE_PLAN.md).
        self.assertEqual(GORE_TRIAGE_LEVEL, "no_blood_no_corpse")
        self.assertEqual(normalize_gore_triage_level(None), "no_blood_no_corpse")
        self.assertEqual(GORE_TRIAGE_LEVELS["no_blood_no_corpse"],
                         {"rule": "C1", "blood_family_max": 0.0716, "corpse_max": 0.0104})
        with self.assertRaises(ValueError):
            normalize_gore_triage_level("aggressive")
        self.write()
        default = build_review_queue(project_root=self.root, report_paths=[self.gore, self.violence],
                                     queue_path=self.queue, content_style="animation")
        explicit = self.build()
        self.assertTrue(self.starts(default["advisory_items"]))
        self.assertEqual(self.starts(default["advisory_items"]), self.starts(explicit["advisory_items"]))
        self.assertEqual(default["gore_triage"]["level"], "no_blood_no_corpse")

    def test_level_off_still_moves_nothing(self):
        self.write()
        queue = self.build(level="off")
        self.assertEqual(self.starts(queue["advisory_items"]), [])
        self.assertEqual((queue["gore_triage"]["level"], queue["gore_triage"]["reason"]), ("off", "level_off"))
        self.assertFalse(any("gore_triage" in item for item in queue["items"]))

    def test_animation_moves_only_cards_without_blood_or_corpse(self):
        self.write()
        queue = self.build()
        self.assertEqual(self.starts(queue["items"]), [200, 300, 400, 600, 800, 900])
        self.assertEqual(self.starts(queue["advisory_items"]), [100, 500, 700])
        moved = {item["start_seconds"]: item for item in queue["advisory_items"] if item["category"] == "gore"}
        for item in moved.values():
            self.assertTrue(item["advisory"])
            self.assertEqual(item["priority"], "context")
            self.assertIsNone(item["suggested_decision"])
            self.assertIsNone(item["decision"])
            self.assertEqual((item["gore_triage"]["outcome"], item["gore_triage"]["rule"]), ("advisory", "C1"))
            self.assertIn("Ứng viên phụ", item["reasons"][-1])
            self.assertIn("không xóa", item["reasons"][-1])
        card = moved[500]
        self.assertEqual(card["scene_card"]["moment_count"], 2)
        self.assertEqual(len(card["source_candidate_refs"]), 2)
        self.assertEqual(card["gore_triage"]["intervals"], 2)
        self.assertEqual(card["gore_triage"]["blood_family_max"], 0.06)
        kept = {item["start_seconds"]: item["gore_triage"]["reason"]
                for item in queue["items"] if item["category"] == "gore"}
        self.assertEqual(kept, {200: "blood_or_corpse_seen", 300: "blood_or_corpse_seen",
                                400: "blood_or_corpse_seen", 600: "evidence_missing",
                                800: "blood_or_corpse_seen", 900: "blood_or_corpse_seen"})
        audit = queue["gore_triage"]
        self.assertTrue(audit["applied"])
        self.assertEqual((audit["evaluated_items"], audit["moved_items"], audit["moved_intervals"]), (9, 3, 4))
        self.assertEqual(audit["moved_seconds"], 4.0 + 16.0 + 4.0)
        self.assertEqual(audit["moved_detected_seconds"], 4.0 + 8.0 + 4.0)  # the 8 s gap is not counted
        self.assertEqual(audit["kept_by_reason"], {"blood_or_corpse_seen": 5, "evidence_missing": 1})
        coverage = queue["candidate_coverage"]
        self.assertTrue(coverage["complete"])
        self.assertEqual(coverage["missing_refs"], [])
        ids = [item["id"] for item in [*queue["items"], *queue["advisory_items"]]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(queue["counts"]["total"], len(queue["items"]))

    def test_moved_cards_and_other_groups_keep_their_identity(self):
        self.write()
        triaged = self.build()
        plain = self.build(level="off")
        self.assertEqual([item for item in triaged["items"] if item["category"] != "gore"],
                         [item for item in plain["items"] if item["category"] != "gore"])
        before = {item["id"]: item for item in plain["items"]}
        for item in triaged["advisory_items"]:
            original = before[item["id"]]
            for key in ("start_seconds", "end_seconds", "source_candidate_refs", "detected_intervals",
                        "evidence", "preview_images", "labels", "max_score", "gore_tag_evidence", "gore_hint"):
                self.assertEqual(item[key], original[key], key)

    def test_cards_carry_the_evidence_and_a_hint_at_every_level(self):
        self.write()
        queue = self.build(level="off")
        cards = {item["start_seconds"]: item for item in queue["items"] if item["category"] == "gore"}
        self.assertEqual(cards[400]["gore_tag_evidence"], {
            "blood_family_max": 0.5, "injury_max": 0.2, "corpse_max": 0.001, "confirmed_frames": 8,
            "intervals": 2, "strongest_blood_label": "blood"})
        self.assertEqual(cards[400]["gore_hint"], "Tagger: thấy máu")
        self.assertEqual(cards[100]["gore_hint"], "Tagger: chỉ vết thương, không thấy máu")
        self.assertEqual(cards[300]["gore_hint"], "Tagger: không thấy máu · có thể là xác")
        self.assertNotIn("gore_tag_evidence", cards[600])
        self.assertNotIn("gore_hint", cards[600])
        self.assertFalse(any("gore_hint" in item for item in queue["items"] if item["category"] != "gore"))

    def test_only_animation_jobs_move_cards(self):
        self.write()
        reference = self.build(style=None)
        self.assertEqual(reference["gore_triage"]["reason"], "content_style_missing")
        for style in ("live_action", "mixed", "unknown"):
            with self.subTest(style=style):
                queue = self.build(style=style)
                self.assertEqual(self.starts(queue["advisory_items"]), [])
                self.assertFalse(queue["gore_triage"]["applied"])
                self.assertEqual(queue["gore_triage"]["reason"], "content_style_not_animation")
                self.assertEqual([i["id"] for i in queue["items"]], [i["id"] for i in reference["items"]])

    def test_uncalibrated_or_live_action_reports_move_nothing(self):
        cases = {
            "sample_fps": {"sample_fps": 1.0},
            "model": {"model": {"repo_id": GORE_TRIAGE_MODEL_REPO, "revision": "0" * 40}},
            "policy": {"two_tier_policy": {"high_threshold": 0.3}},
            "evidence_version": {"gore_tag_evidence_policy": {"version": 99}},
            "no_evidence_policy": {"gore_tag_evidence_policy": None},
            # The thresholds were measured on fp16 scores (the default fast scan); fp32 or an
            # unknown precision (reports made before runtime.precision existed) moves nothing.
            "fp32": {"runtime": {"precision": "fp32"}},
            "no_precision": {"runtime": {"torch": "2.x"}},
            "no_runtime": {"runtime": None},
        }
        for name, extra in cases.items():
            with self.subTest(name=name):
                self.write(**extra)
                queue = self.build()
                self.assertEqual(self.starts(queue["advisory_items"]), [])
                self.assertEqual(queue["gore_triage"]["kept_by_reason"], {"uncalibrated_scan": 9})
        self.write(content_style="live_action")
        queue = self.build()
        self.assertEqual(self.starts(queue["advisory_items"]), [])
        self.assertEqual(queue["gore_triage"]["kept_by_reason"], {"not_animation_gore_scan": 9})
        self.assertTrue(gore_scan_is_calibrated(_gore_payload(self.source, [])))

    def test_decided_items_never_move(self):
        payloads = {"reports/jobs/job/animation-safety/gore/scan.json": _gore_payload(self.source, self.intervals())}
        weak = {"category": "gore", "start_seconds": 100, "end_seconds": 104, "priority": "high", "reasons": [],
                "source_candidate_refs": ["reports/jobs/job/animation-safety/gore/scan.json#interval:0"]}
        items = [dict(weak, decision="KEEP"), dict(weak, decision="NEEDS_MORE_CONTEXT"),
                 dict(weak, decision="BLUR"), dict(weak, decision=None)]
        required, advisory, audit = triage_anime_gore_items(items, payloads, "animation", "no_blood_no_corpse")
        self.assertEqual(required, items[:3])  # untouched, no triage annotation
        self.assertEqual(len(advisory), 1)
        self.assertEqual(audit["kept_by_reason"], {"decided": 3})
        self.assertEqual(audit["evaluated_items"], 1)

    def test_references_that_do_not_resolve_keep_the_item(self):
        payloads = {
            "reports/jobs/job/animation-safety/gore/scan.json": _gore_payload(self.source, self.intervals()),
            "reports/jobs/job/animation-safety/violence/scan.json": dict(
                _gore_payload(self.source, [_interval(100, self.MOVE)]), scan_type="violence"),
        }
        base = {"category": "gore", "start_seconds": 100, "end_seconds": 104, "decision": None}
        cases = {
            "no_source_refs": [],
            "unresolved_source_refs": ["reports/jobs/old/animation-safety/gore/scan.json#interval:0"],
            "not_animation_gore_scan": ["reports/jobs/job/animation-safety/violence/scan.json#interval:0"],
            "stale_source_refs": ["reports/jobs/job/animation-safety/gore/scan.json#interval:5"],
        }
        for reason, refs in cases.items():
            with self.subTest(reason=reason):
                required, advisory, _audit = triage_anime_gore_items(
                    [dict(base, source_candidate_refs=refs)], payloads, "animation", "no_blood_no_corpse")
                self.assertEqual(advisory, [])
                self.assertEqual(required[0]["gore_triage"]["reason"], reason)
                self.assertEqual(annotate_gore_tag_evidence([dict(base, source_candidate_refs=refs,
                                                                  gore_hint="stale")], payloads)[0].get("gore_hint"),
                                 None)

    def test_preserved_unresolved_items_are_triaged_again(self):
        self.write()
        self.queue.write_text(json.dumps({
            "source": {"path": str(self.source.resolve()), "sha256": "abc"},
            "items": [
                {   # from an older revision whose report is gone: stays required
                    "id": "review-old-unresolvable", "category": "gore", "candidate_type": None,
                    "start_seconds": 1700, "end_seconds": 1704, "priority": "context", "decision": None,
                    "labels": ["injury"], "reasons": [], "evidence": ["reports/jobs/old/gore/scan.json"],
                    "preview_images": [], "detected_intervals": [{"start_seconds": 1700, "end_seconds": 1704}],
                    "source_candidate_refs": ["reports/jobs/old/gore/scan.json#interval:0"],
                    "gore_hint": "Tagger: chỉ vết thương, không thấy máu",
                },
            ],
        }), encoding="utf-8")
        queue = self.build()
        kept = next(item for item in queue["items"] if item["start_seconds"] == 1700)
        self.assertEqual(kept["migration_status"], "preserved_unresolved_from_previous_queue")
        self.assertEqual(kept["gore_triage"]["reason"], "unresolved_source_refs")
        self.assertNotIn("gore_hint", kept)
        self.assertEqual(self.starts(queue["advisory_items"]), [100, 500, 700])

    def test_a_decision_on_a_moved_card_returns_it_to_the_main_list(self):
        self.write()
        queue = self.build()
        moved = next(item for item in queue["advisory_items"] if item["start_seconds"] == 100)
        record_review_decision(project_root=self.root, queue_path=self.queue, item_id=moved["id"],
                               decision="BLUR", full_frame=True)
        saved = json.loads(self.queue.read_text(encoding="utf-8"))
        self.assertIn(moved["id"], [item["id"] for item in saved["items"]])
        self.assertNotIn(moved["id"], [item["id"] for item in saved["advisory_items"]])
        decided = next(item for item in saved["items"] if item["id"] == moved["id"])
        self.assertEqual(decided["decision"], "BLUR")
        # The decided card is never moved back by a later triage pass.
        payloads = {"reports/jobs/job/animation-safety/gore/scan.json":
                    json.loads(self.gore.read_text(encoding="utf-8"))}
        required, advisory, _audit = triage_anime_gore_items(
            saved["items"], payloads, "animation", "no_blood_no_corpse")
        self.assertIn(moved["id"], [item["id"] for item in required])
        self.assertEqual(advisory, [])

    def test_build_review_from_the_cli_uses_the_default_level(self):
        # No CLI flag: cli.py is in the cache key of every scan stage, so the level is the
        # code default (GORE_TRIAGE_LEVEL) and measurements call build_review_queue directly.
        from biliflow import cli

        argv = ["biliflow", "--project-root", str(self.root), "build-review", "--report", str(self.gore),
                "--queue", str(self.queue), "--content-style", "animation"]
        with mock.patch("biliflow.cli.build_review_queue", return_value={}) as build, \
                mock.patch("sys.stdout", new_callable=io.StringIO), mock.patch("sys.argv", argv):
            self.assertEqual(cli.main(), 0)
        self.assertNotIn("gore_triage_level", build.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
