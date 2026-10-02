import json
import unittest
from pathlib import Path

from biliflow.golden_scoring import (
    compare, covered_fraction, labels_fingerprint, region_quality, score, scorecard_markdown, union_length,
)

SHA = "a" * 64


def manifest():
    return {
        "schema_version": 1, "golden_set": "v1",
        "sources": {"src": {"sha256": SHA, "duration_seconds": 1000.0, "width": 1920, "height": 1080}},
        "segments": [
            {"id": "S1", "source": "src", "start_seconds": 0.0, "end_seconds": 100.0, "split": "dev"},
            {"id": "S2", "source": "src", "start_seconds": 200.0, "end_seconds": 300.0, "split": "holdout"},
        ],
    }


def label(id, segment, category, start, end, action="BLUR", region=None, severity="must_catch", ambiguous=False):
    return {"id": id, "segment_id": segment, "category": category, "start_seconds": start, "end_seconds": end,
            "region_source_pixels": region, "expected_action": action, "severity": severity,
            "ambiguous": ambiguous, "notes": ""}


def labels_doc(events, complete=("S1", "S2")):
    return {"revision": 3, "segments": {s: {"status": "complete" if s in complete else "in_progress"}
                                        for s in ("S1", "S2")}, "events": events}


def item(id, category, start, end, region=None, suggested=None, priority="high", candidate_type=None):
    return {"id": id, "category": category, "start_seconds": start, "end_seconds": end,
            "suggested_region_source_pixels": region, "suggested_decision": suggested, "priority": priority,
            "candidate_type": candidate_type, "labels": ["x"]}


def queue(items, advisory=(), scope=None):
    payload = {"source": {"sha256": SHA}, "items": list(items), "advisory_items": list(advisory)}
    if scope is not None:
        payload["detection_scope"] = {"selected": scope}
    return payload


WATERMARK = {"x": 100, "y": 100, "width": 200, "height": 50}


class IntervalTest(unittest.TestCase):
    def test_union_and_coverage(self):
        self.assertEqual(union_length([(0, 10), (5, 15), (20, 25)]), 20)
        self.assertAlmostEqual(covered_fraction((0, 10), [(-5, 4), (8, 30)]), 0.6)

    def test_region_quality_uses_coverage_not_iou(self):
        tight = {"x": 110, "y": 105, "width": 180, "height": 40}
        loose = {"x": 90, "y": 90, "width": 220, "height": 70}
        self.assertFalse(region_quality(tight, WATERMARK)["ok"])  # hides only 72% of the label box
        self.assertTrue(region_quality(loose, WATERMARK)["ok"])   # covers it, 1.54x larger
        huge = {"x": 0, "y": 0, "width": 1000, "height": 500}
        self.assertFalse(region_quality(huge, WATERMARK)["ok"])
        self.assertEqual(region_quality(None, WATERMARK)["reason"], "no_region")


class ScoreTest(unittest.TestCase):
    def test_persistent_item_catches_clipped_watermark_label(self):
        events = [label("L1", "S1", "visual_logo", 0, 1000, region=WATERMARK)]
        card = score(manifest(), labels_doc(events, complete=("S1",)),
                     {"src": queue([item("W", "text", 0, 1000, WATERMARK, "BLUR")])})
        row = card["labels"][0]
        self.assertEqual(row["status"], "caught")  # OCR item catches an advertising-group logo label
        self.assertEqual(row["clip"], [0.0, 100.0])
        self.assertTrue(row["region"]["ok"])
        self.assertTrue(row["suggestion_agrees"])
        metrics = card["metrics"]["advertising"]["dev"]
        self.assertEqual((metrics["caught"], metrics["useful_main"], metrics["main_items"]), (1, 1, 1))
        self.assertEqual(card["metrics"]["advertising"]["holdout"]["scored_seconds"], 0)

    def test_union_of_short_windows_and_partial_and_missed(self):
        events = [label("L1", "S1", "adult", 10, 20, action="CUT"),
                  label("L2", "S1", "adult", 40, 60, action="CUT"),
                  label("L3", "S1", "adult", 80, 90, action="CUT", severity="should_catch")]
        items = [item("A", "adult", 9, 15), item("B", "adult", 14, 21), item("C", "adult", 40, 45)]
        card = score(manifest(), labels_doc(events, complete=("S1",)), {"src": queue(items)})
        status = {l["id"]: l["status"] for l in card["labels"]}
        self.assertEqual(status, {"L1": "caught", "L2": "partial", "L3": "missed"})
        metrics = card["metrics"]["adult"]["all"]
        self.assertEqual((metrics["must_catch"], metrics["must_caught"], metrics["recall_all"]), (2, 1, 0.3333))

    def test_advisory_only_region_incompatibility_and_false_positive(self):
        events = [label("L1", "S1", "visual_logo", 0, 50, region=WATERMARK)]
        elsewhere = {"x": 1500, "y": 800, "width": 100, "height": 100}
        card = score(manifest(), labels_doc(events, complete=("S1",)), {"src": queue(
            [item("FAR", "visual_logo", 0, 50, elsewhere, "BLUR")],
            advisory=[item("ADV", "visual_logo", 0, 50, WATERMARK, "KEEP", "context")])})
        self.assertEqual(card["labels"][0]["status"], "advisory_only")
        verdicts = {i["item_id"]: i["verdict"] for i in card["items"]}
        self.assertEqual(verdicts, {"FAR": "false_positive", "ADV": "useful"})
        self.assertEqual(card["metrics"]["advertising"]["all"]["precision_main"], 0.0)
        self.assertFalse(card["labels"][0]["suggestion_agrees"])

    def test_trap_hit_ambiguous_and_scope(self):
        head = {"x": 1200, "y": 190, "width": 260, "height": 240}
        events = [label("TRAP", "S2", "visual_logo", 210, 215, action="KEEP", region=head, severity=None),
                  label("AMB", "S2", "gore", 250, 260, action="CUT", ambiguous=True),
                  label("NUDE", "S2", "adult", 220, 230, action="BLUR")]
        items = [item("HEAD", "visual_logo", 210, 1000, head, "BLUR"), item("G", "gore", 251, 255)]
        card = score(manifest(), labels_doc(events, complete=("S2",)),
                     {"src": queue(items, scope=["advertising", "gore"])})
        status = {l["id"]: l["status"] for l in card["labels"]}
        self.assertEqual(status, {"TRAP": "trap_hit", "AMB": "ambiguous"})
        self.assertEqual(card["unscored_labels"]["adult"], 1)
        verdicts = {i["item_id"]: i["verdict"] for i in card["items"]}
        self.assertEqual(verdicts, {"HEAD": "false_positive", "G": "ambiguous_only"})
        self.assertEqual(card["metrics"]["gore"]["holdout"]["main_items"], 0)
        self.assertEqual(card["metrics"]["advertising"]["holdout"]["trap_hits"], 1)
        self.assertIn("trúng bẫy", scorecard_markdown(card, "Test"))

    def test_incomplete_segments_are_not_scored_and_wrong_source_is_refused(self):
        events = [label("L1", "S1", "adult", 10, 20)]
        card = score(manifest(), labels_doc(events, complete=()), {"src": queue([])})
        self.assertEqual(card["labels"], [])
        self.assertEqual(card["segments"][0]["reason"], "chưa xem hết đoạn")
        bad = queue([])
        bad["source"]["sha256"] = "b" * 64
        with self.assertRaises(ValueError):
            score(manifest(), labels_doc(events), {"src": bad})


class FrameRuleTest(unittest.TestCase):
    """Review 2026-09-29: a film-long watermark box must not stand in for whole-frame events."""

    def card(self, items, advisory=(), events=None):
        events = events if events is not None else [
            label("WM", "S1", "visual_logo", 0, 100, region=WATERMARK),
            label("OPEN", "S1", "visual_logo", 0, 15, action="CUT"),
            label("TRAP", "S1", "visual_logo", 20, 25, action="KEEP", severity=None),
        ]
        return score(manifest(), labels_doc(events, complete=("S1",)), {"src": queue(items, advisory)})

    def test_watermark_box_does_not_catch_whole_frame_cut_or_hit_whole_frame_trap(self):
        card = self.card([item("W", "visual_logo", 0, 1000, WATERMARK, "BLUR")])
        status = {l["id"]: l["status"] for l in card["labels"]}
        self.assertEqual(status, {"WM": "caught", "OPEN": "missed", "TRAP": "trap_ok"})

    def test_whole_frame_cut_item_catches_the_cut_label_and_hits_the_trap(self):
        card = self.card([item("W", "visual_logo", 0, 1000, WATERMARK, "BLUR"),
                          item("P1", "visual_logo", 0, 15, None, "CUT"),
                          item("P2", "visual_logo", 20, 25, None, "CUT", candidate_type="opening_promotion")])
        status = {l["id"]: l["status"] for l in card["labels"]}
        self.assertEqual(status, {"WM": "caught", "OPEN": "caught", "TRAP": "trap_hit"})
        verdicts = {i["item_id"]: i["verdict"] for i in card["items"]}
        # P2 lies inside the watermark label in time but is a whole-frame event: a false positive.
        self.assertEqual(verdicts, {"W": "useful", "P1": "useful", "P2": "false_positive"})

    def test_unknown_location_advisories_never_match(self):
        rejected = item("R", "visual_logo", 40, 45, None, "KEEP", "context")
        card = self.card([item("W", "visual_logo", 0, 1000, WATERMARK, "BLUR")], advisory=[rejected])
        by_id = {i["item_id"]: i for i in card["items"]}
        self.assertEqual((by_id["R"]["kind"], by_id["R"]["verdict"]), ("unknown", "false_positive"))
        self.assertEqual(card["metrics"]["advertising"]["all"]["advisory_noise"], 1)

    def test_large_box_counts_for_a_whole_frame_label(self):
        banner = {"x": 0, "y": 0, "width": 1920, "height": 600}  # 55% of the frame
        card = self.card([item("B", "visual_logo", 0, 15, banner, "BLUR")],
                         events=[label("OPEN", "S1", "visual_logo", 0, 15, action="CUT")])
        self.assertEqual(card["labels"][0]["status"], "caught")

    def test_region_quality_comes_from_the_item_covering_the_label_best(self):
        wide = {"x": 0, "y": 0, "width": 700, "height": 400}   # contains the label box, 28x its area
        card = self.card([item("A", "visual_logo", 0, 100, wide, "BLUR"),
                          item("B", "visual_logo", 50, 52, WATERMARK, "BLUR")],
                         events=[label("WM", "S1", "visual_logo", 0, 100, region=WATERMARK)])
        region = card["labels"][0]["region"]
        self.assertEqual((region["item_id"], region["ok"]), ("A", False))

    def test_region_quality_is_not_graded_for_coarse_dense_boxes(self):
        quadrant = {"x": 0, "y": 0, "width": 960, "height": 540}
        events = [dict(label("Q", "S1", "visual_logo", 0, 20, region=quadrant), from_suggestion="dense-1")]
        card = score(manifest(), labels_doc(events, complete=("S1",)),
                     {"src": queue([item("W", "visual_logo", 0, 20, WATERMARK, "BLUR")])},
                     approximate_suggestions={"dense-1"})
        row = card["labels"][0]
        self.assertEqual((row["status"], row["region"], row["region_approximate"]), ("caught", None, True))

    def test_item_touching_positive_and_ambiguous_labels_stays_a_false_positive(self):
        events = [label("POS", "S1", "gore", 10, 20, action="CUT"),
                  label("AMB", "S1", "gore", 49, 60, action="CUT", ambiguous=True)]
        card = score(manifest(), labels_doc(events, complete=("S1",)),
                     {"src": queue([item("G", "gore", 10, 50)])})
        self.assertEqual(card["items"][0]["verdict"], "false_positive")
        self.assertEqual(card["metrics"]["gore"]["all"]["main_items"], 1)


class SceneCardScoringTest(unittest.TestCase):
    """R1 scene cards (2026-10-01): a decision edits only the detected moments, so they are scored that way."""

    @staticmethod
    def scene(moments, scene_card=True):
        value = item("F", "violence", moments[0][0], moments[-1][1], suggested="BLUR")
        value.update(temporal_policy="discrete_detected_intervals",
                     detected_intervals=[{"start_seconds": a, "end_seconds": b} for a, b in moments])
        if scene_card:
            value["scene_card"] = {"kind": "fight", "applies_to": "detected_intervals_only"}
        return value

    def test_a_label_in_a_gap_is_not_caught_by_the_scene_card(self):
        events = [label("IN", "S1", "violence", 10, 20), label("GAP", "S1", "violence", 25, 35)]
        card = score(manifest(), labels_doc(events, complete=("S1",)),
                     {"src": queue([self.scene([(10, 20), (40, 50)])])})
        self.assertEqual({row["id"]: row["status"] for row in card["labels"]}, {"IN": "caught", "GAP": "missed"})
        self.assertEqual(card["items"][0]["moments"], [[10, 20], [40, 50]])
        self.assertIn("scene_card_extent", card["rules"])
        span = score(manifest(), labels_doc(events, complete=("S1",)),
                     {"src": queue([self.scene([(10, 20), (40, 50)], scene_card=False)])})
        self.assertEqual(span["labels"][1]["status"], "caught", "other items keep the start-end span rule")
        self.assertNotIn("moments", span["items"][0])
        self.assertNotIn("scene_card_extent", span["rules"])

    def test_scene_card_precision_counts_its_moments_not_its_gaps(self):
        # Conan 21 2161.5-2214.5 (reviewer finding): useful by its moments, a false positive by its span.
        events = [label("IN", "S1", "gore", 10, 20, action="CUT")]
        moments = [(10, 20), (40, 50)]
        card = score(manifest(), labels_doc(events, complete=("S1",)),
                     {"src": queue([dict(self.scene(moments), category="gore")])})
        self.assertEqual(card["items"][0]["verdict"], "useful")
        self.assertEqual(card["metrics"]["gore"]["all"]["precision_main"], 1.0)
        self.assertEqual(card["metrics"]["gore"]["all"]["main_items"], 1, "one card is one review item")
        span = score(manifest(), labels_doc(events, complete=("S1",)),
                     {"src": queue([dict(self.scene(moments, scene_card=False), category="gore")])})
        self.assertEqual(span["items"][0]["verdict"], "false_positive")

    def test_moments_are_clipped_to_the_segment(self):
        events = [label("L", "S1", "violence", 90, 100)]
        card = score(manifest(), labels_doc(events, complete=("S1",)),
                     {"src": queue([self.scene([(80, 85), (95, 110)])])})
        self.assertEqual(card["items"][0]["moments"], [[80, 85], [95, 100]])
        self.assertEqual(card["labels"][0]["status"], "partial")


class GateTest(unittest.TestCase):
    def cards(self, candidate_items, candidate_time=100.0):
        events = [label("L1", "S2", "adult", 210, 220, action="CUT"),
                  label("L2", "S2", "adult", 240, 250, action="CUT")]
        doc = labels_doc(events)
        base = score(manifest(), doc, {"src": queue([item("A", "adult", 210, 220), item("B", "adult", 240, 250)])},
                     {"total_wall_seconds": 100.0})
        new = score(manifest(), doc, {"src": queue(candidate_items)}, {"total_wall_seconds": candidate_time})
        return base, new

    def test_detector_gate_flags_new_holdout_miss_and_slowdown(self):
        base, new = self.cards([item("A", "adult", 210, 220)], candidate_time=106.0)
        result = compare(base, new, gate="detector")
        self.assertEqual([c["id"] for c in result["newly_missed"]], ["L2"])
        self.assertTrue(any("must_catch mới" in v for v in result["violations"]))
        self.assertTrue(any("5%" in v for v in result["violations"]))

    def test_speed_gate_allows_one_item_and_detects_changed_miss_list(self):
        base, same = self.cards([item("A", "adult", 210, 220), item("B", "adult", 240, 250),
                                 item("X", "adult", 270, 280)])
        self.assertEqual(compare(base, same, gate="speed")["violations"], [])
        base, worse = self.cards([item("A", "adult", 210, 220)])
        self.assertIn("Danh sách bỏ sót must_catch thay đổi", compare(base, worse, gate="speed")["violations"])

    def test_detector_gate_fails_closed_without_timing(self):
        base, new = self.cards([item("A", "adult", 210, 220), item("B", "adult", 240, 250)])
        new["timing"] = {}
        self.assertTrue(any("Thiếu thời gian" in v for v in compare(base, new, gate="detector")["violations"]))

    def test_new_false_positives_in_a_group_without_baseline_items_fail_the_detector_gate(self):
        events = [label("L1", "S2", "adult", 210, 220, action="CUT")]
        doc = labels_doc(events)
        base = score(manifest(), doc, {"src": queue([item("A", "adult", 210, 220)])}, {"total_wall_seconds": 100.0})
        new = score(manifest(), doc, {"src": queue([item("A", "adult", 210, 220), item("V", "violence", 260, 270)])},
                    {"total_wall_seconds": 100.0})
        self.assertIsNone(base["metrics"]["violence"]["all"]["precision_main"])
        self.assertTrue(any(v.startswith("violence: mốc không có mục chính") for v in
                            compare(base, new, gate="detector")["violations"]))

    def test_different_label_sets_are_not_comparable(self):
        base, new = self.cards([item("A", "adult", 210, 220)])
        new["labels_fingerprint"] = "other"
        self.assertFalse(compare(base, new, gate="speed")["comparable"])


class ScoreSetsTest(unittest.TestCase):
    """v1 and v1.1 scored together: per-set sub-cards stay equal to a card of that set alone."""

    def setUp(self):
        from biliflow.golden_set import canonical_sha256
        self.v1 = manifest()
        self.v11 = {"schema_version": 1, "golden_set": "v1.1", "extends": "v1",
                    "extends_manifest_sha256": canonical_sha256(self.v1),
                    "sources": dict(self.v1["sources"], other={"sha256": "c" * 64, "duration_seconds": 500.0,
                                                               "width": 1280, "height": 720}),
                    "segments": [{"id": "N1", "source": "src", "start_seconds": 400.0, "end_seconds": 500.0,
                                  "split": "dev"},
                                 {"id": "N2", "source": "other", "start_seconds": 0.0, "end_seconds": 100.0,
                                  "split": "holdout"}]}
        self.v1_labels = labels_doc([label("L1", "S1", "adult", 10, 20), label("L2", "S2", "gore", 210, 220)])
        self.v11_labels = {"revision": 4, "segments": {"N1": {"status": "complete"}, "N2": {"status": "complete"}},
                           "events": [label("gs-N1-0001", "N1", "adult", 410, 430),
                                      label("gs-N2-0001", "N2", "violence", 10, 20, severity="should_catch")]}
        self.queues = {"src": queue([item("A", "adult", 9, 21), item("G", "gore", 250, 260),
                                     item("B", "adult", 415, 420)]),
                       "other": {"source": {"sha256": "c" * 64}, "items": [item("V", "violence", 10, 20)]}}

    def combined(self):
        from biliflow.golden_set import canonical_sha256
        manifest_doc = {"schema_version": 1, "golden_set": "v1+v1.1",
                        "sets": {"v1": canonical_sha256(self.v1), "v1.1": canonical_sha256(self.v11)},
                        "sources": self.v11["sources"],
                        "segments": [dict(s, set="v1") for s in self.v1["segments"]] +
                                    [dict(s, set="v1.1") for s in self.v11["segments"]]}
        labels = {"revision": {"v1": 3, "v1.1": 4},
                  "segments": {**self.v1_labels["segments"], **self.v11_labels["segments"]},
                  "events": self.v1_labels["events"] + self.v11_labels["events"]}
        parts = {"v1": {"manifest": self.v1, "labels": self.v1_labels},
                 "v1.1": {"manifest": self.v11, "labels": self.v11_labels}}
        return manifest_doc, labels, parts

    @staticmethod
    def without_time(card):
        return {k: v for k, v in card.items() if k not in ("created_at", "set", "sets")}

    def test_v1_sub_card_equals_a_v1_only_score(self):
        from biliflow.golden_scoring import score_sets
        timing = {"total_wall_seconds": 100.0}
        combined, cards = score_sets(*self.combined(), self.queues, timing)
        alone = score(self.v1, self.v1_labels, {"src": self.queues["src"]}, timing)
        self.assertEqual(self.without_time(cards["v1"]), self.without_time(alone))
        self.assertEqual(cards["v1"]["set"], "v1")
        result = compare(alone, cards["v1"], gate="detector")
        self.assertEqual((result["comparable"], result["label_changes"], result["violations"]), (True, [], []))
        self.assertFalse(compare(alone, combined)["comparable"])  # the combined card has another pin

    def test_combined_card_rows_are_the_union_of_the_sub_cards(self):
        from biliflow.golden_scoring import score_sets
        combined, cards = score_sets(*self.combined(), self.queues)
        self.assertEqual([(s["id"], s["set"]) for s in combined["segments"]],
                         [("S1", "v1"), ("S2", "v1"), ("N1", "v1.1"), ("N2", "v1.1")])
        for key in ("labels", "items"):
            self.assertEqual(combined[key], cards["v1"][key] + cards["v1.1"][key])
        for group in ("adult", "violence"):
            for field in ("labels", "caught", "main_items", "useful_main"):
                self.assertEqual(combined["metrics"][group]["all"][field],
                                 cards["v1"]["metrics"][group]["all"][field] +
                                 cards["v1.1"]["metrics"][group]["all"][field])
        self.assertEqual(combined["labels_revision"], {"v1": 3, "v1.1": 4})
        self.assertEqual(set(combined["sets"]), {"v1", "v1.1"})
        self.assertEqual(combined["sets"]["v1"]["labels_fingerprint"], cards["v1"]["labels_fingerprint"])
        self.assertEqual((combined["sets"]["v1.1"]["complete_segments"], combined["sets"]["v1.1"]["segments"]), (2, 2))
        self.assertEqual({l["id"]: l["status"] for l in cards["v1.1"]["labels"]},
                         {"gs-N1-0001": "partial", "gs-N2-0001": "caught"})

    def test_a_single_set_card_is_that_sets_card(self):
        from biliflow.golden_scoring import score_sets
        combined, cards = score_sets(self.v1, self.v1_labels, {"v1": {"manifest": self.v1, "labels": self.v1_labels}},
                                     {"src": self.queues["src"]})
        self.assertEqual(self.without_time(combined), self.without_time(cards["v1"]))
        self.assertEqual(list(combined["sets"]), ["v1"])
        self.assertNotIn("set", combined)

    def test_markdown_limits_follow_the_scored_sets(self):
        from biliflow.golden_scoring import score_sets
        v1_only = scorecard_markdown(score(self.v1, self.v1_labels, {"src": self.queues["src"]}), "T")
        self.assertIn("- Golden Set v1: 3 nguồn (Troy, Conan 20, Conan 21)", v1_only)
        self.assertNotIn("v1.1", v1_only)
        self.assertNotIn("Theo bộ nhãn", v1_only)
        combined, cards = score_sets(*self.combined(), self.queues)
        text = scorecard_markdown(combined, "T")
        self.assertIn("revision v1 3, v1.1 4", text)
        self.assertIn("## Theo bộ nhãn", text)
        self.assertIn("- Golden Set v1.1: vẫn 3 nguồn đó", text)
        self.assertIn("- Golden Set v1: 3 nguồn", text)
        self.assertNotIn("- Golden Set v1: 3", scorecard_markdown(cards["v1.1"], "T"))


def present(id, segment, category, start, end, severity="should_catch", ambiguous=False):
    """A "có thật · giữ" label: real safety content the user keeps on export."""
    return dict(label(id, segment, category, start, end, action="KEEP", severity=severity, ambiguous=ambiguous),
                content_present=True)


class ContentPresentScoringTest(unittest.TestCase):
    """2026-10-01: real violence/blood the user keeps is a positive the detector must flag, not a trap."""

    def card(self):
        events = [present("BLOOD", "S1", "gore", 10, 20),
                  present("FIGHT", "S1", "violence", 30, 40, severity="must_catch"),
                  present("STAB", "S1", "violence", 60, 70),
                  label("TRAP", "S1", "gore", 80, 90, action="KEEP", severity=None),
                  present("MAYBE", "S2", "gore", 210, 220, ambiguous=True)]
        items = [item("G", "gore", 10, 20, None, "CUT"),      # flags the kept blood: caught, action disagrees
                 item("F", "violence", 30, 40, None, "KEEP"),  # flags the kept fight, proposing KEEP
                 item("T", "gore", 80, 90, None, "CUT"),       # hits the plain KEEP trap
                 item("M", "gore", 211, 219, None, "CUT")]     # touches only the ambiguous label
        return score(manifest(), labels_doc(events), {"src": queue(items)})

    def test_present_labels_are_positives_and_plain_keep_stays_a_trap(self):
        card = self.card()
        rows = {l["id"]: l for l in card["labels"]}
        self.assertEqual({k: v["status"] for k, v in rows.items()},
                         {"BLOOD": "caught", "FIGHT": "caught", "STAB": "missed", "TRAP": "trap_hit",
                          "MAYBE": "ambiguous"})
        self.assertEqual((rows["BLOOD"]["suggestion_agrees"], rows["FIGHT"]["suggestion_agrees"]), (False, True))
        self.assertTrue(rows["BLOOD"]["content_present"])
        self.assertNotIn("content_present", rows["TRAP"])
        self.assertIsNone(rows["BLOOD"]["region"])  # no blur box to grade on a kept scene
        items = {i["item_id"]: i for i in card["items"]}
        self.assertEqual({k: v["verdict"] for k, v in items.items()},
                         {"G": "useful", "F": "useful", "T": "false_positive", "M": "ambiguous_only"})
        self.assertEqual((items["G"]["trap_hits"], items["T"]["trap_hits"]), ([], ["TRAP"]))

    def test_metrics_count_present_labels_by_their_severity(self):
        metrics = self.card()["metrics"]
        gore, violence = metrics["gore"]["all"], metrics["violence"]["all"]
        self.assertEqual((gore["labels"], gore["caught"], gore["must_catch"], gore["recall_all"]), (1, 1, 0, 1.0))
        self.assertEqual((gore["main_items"], gore["useful_main"], gore["trap_hits"], gore["ambiguous"]), (2, 1, 1, 1))
        self.assertEqual((gore["suggestion_evaluated"], gore["suggestion_agree"]), (1, 0))
        self.assertEqual((violence["labels"], violence["caught"], violence["missed"]), (2, 1, 1))
        self.assertEqual((violence["must_catch"], violence["must_caught"], violence["recall_all"]), (1, 1, 0.5))
        self.assertEqual((violence["precision_main"], violence["trap_hits"], violence["suggestion_agree"]), (1.0, 0, 1))

    def test_markdown_names_them_and_the_fingerprint_sees_them(self):
        card = self.card()
        text = scorecard_markdown(card, "T")
        self.assertIn("`STAB` S1 60.0–70.0s · violence có thật · giữ should_catch · **bỏ sót**", text)
        self.assertIn("`TRAP` S1 80.0–90.0s · gore KEEP  · **trúng bẫy**", text)
        self.assertIn("Nhãn có thật · giữ", text)
        self.assertIn(": 4, bắt được 2/3.", text)  # the ambiguous one is listed but not scored
        plain = labels_doc([dict(present("BLOOD", "S1", "gore", 10, 20), content_present=False)])
        self.assertNotEqual(labels_fingerprint(labels_doc([present("BLOOD", "S1", "gore", 10, 20)])),
                            labels_fingerprint(plain))


class LegacyLabelsUnchangedTest(unittest.TestCase):
    """Labels without content_present score exactly as before the "có thật · giữ" change."""

    @staticmethod
    def fixture():
        head = {"x": 1200, "y": 190, "width": 260, "height": 240}
        events = [label("WM", "S1", "visual_logo", 0, 100, region=WATERMARK),
                  label("OPEN", "S1", "visual_logo", 0, 15, action="CUT"),
                  label("TRAP", "S1", "visual_logo", 20, 25, action="KEEP", region=head, severity=None),
                  label("GORE", "S1", "gore", 40, 50, action="CUT"),
                  label("AMB", "S2", "gore", 250, 260, action="CUT", ambiguous=True),
                  label("NUDE", "S2", "adult", 220, 230, action="BLUR", severity="should_catch"),
                  label("SAFE", "S2", "violence", 270, 280, action="KEEP", severity=None)]
        items = [item("W", "visual_logo", 0, 1000, WATERMARK, "BLUR"), item("P1", "visual_logo", 0, 15, None, "CUT"),
                 item("HEAD", "visual_logo", 20, 30, head, "BLUR"), item("G1", "gore", 38, 47, None, "CUT"),
                 item("G2", "gore", 251, 255), item("A1", "adult", 221, 226, None, "BLUR"),
                 item("V1", "violence", 268, 282, None, "CUT"), item("V2", "violence", 60, 70, None, "KEEP")]
        advisory = [item("ADV", "adult", 226, 231, None, "KEEP", "context")]
        return manifest(), labels_doc(events), {"src": queue(items, advisory)}

    def test_card_equals_the_one_frozen_before_the_change(self):
        from biliflow.golden_set import canonical_sha256
        card = score(*self.fixture(), {"total_wall_seconds": 10.0})
        # Computed with the scorer as it was before content_present existed (2026-10-01).
        self.assertEqual(canonical_sha256({k: v for k, v in card.items() if k != "created_at"}),
                         "096ce28b3d02ebcd6a30624d254ae4d1665778ba37cd47c4eee885db877ddfad")
        self.assertEqual(card["labels_fingerprint"], "0703e3f840d87782ea305dc85e60e4a59e461a8e594c14f8707b7cda295653b9")
        self.assertEqual({l["id"]: l["status"] for l in card["labels"]}["SAFE"], "trap_hit")

    def test_fingerprint_formula_is_the_old_one_without_the_field(self):
        from biliflow.golden_set import canonical_sha256
        doc = self.fixture()[1]
        keys = ("id", "segment_id", "category", "start_seconds", "end_seconds", "region_source_pixels",
                "expected_action", "severity", "ambiguous")
        old = canonical_sha256({"complete_segments": ["S1", "S2"],
                                "events": sorted(({k: e.get(k) for k in keys} for e in doc["events"]),
                                                 key=lambda e: e["id"])})
        self.assertEqual(labels_fingerprint(doc), old)


ROOT = Path(__file__).resolve().parents[1]
BASELINE_R469 = ROOT / "reports/benchmarks/golden-baseline-v1-r469-20260930-235857"
BASELINE_QUEUES = {"troy": "troy-allgroups-full-fast-20260930-162534",
                   "conan20": "conan20-allgroups-full-fast-20260930-073627",
                   "conan21": "conan21-allgroups-full-golden-20260930-222720"}


def _baseline_available() -> bool:
    try:
        labels = json.loads((ROOT / "annotations/golden/v1/events.json").read_text(encoding="utf-8"))
        return (BASELINE_R469 / "scorecard.json").exists() and labels.get("revision") == 469 and all(
            (ROOT / "reports/jobs" / job / "review-queue.json").exists() for job in BASELINE_QUEUES.values())
    except (OSError, ValueError):
        return False


@unittest.skipUnless(_baseline_available(), "local v1 labels are not at revision 469 or the baseline queues are gone")
class V1BaselineR469Test(unittest.TestCase):
    """The real v1 scorecard (labels r469, three baseline queues) is unchanged by the new rules."""

    def test_rescoring_reproduces_the_baseline_card(self):
        import shutil
        import tempfile
        from biliflow.golden_scoring import score_sets
        from biliflow.golden_set import load_golden_sets, read_json
        baseline = read_json(BASELINE_R469 / "scorecard.json")
        with tempfile.TemporaryDirectory() as temp:  # labels are read from a copy; annotations/ is never opened
            (Path(temp) / "v1").mkdir()
            for name in ("segments.json", "events.json"):
                shutil.copy2(ROOT / "annotations/golden/v1" / name, Path(temp) / "v1" / name)
            manifest_doc, labels, parts = load_golden_sets(Path(temp), ["v1"])
        queues = {key: read_json(ROOT / "reports/jobs" / job / "review-queue.json") for key, job in BASELINE_QUEUES.items()}
        prefill = ROOT / "reports/benchmarks/golden-v1/prefill/suggestions.json"
        approximate = {s["id"] for s in read_json(prefill)["suggestions"]
                       if s.get("tier") == "dense" and s.get("candidate_type") == "dense_logo_window"}
        combined, _ = score_sets(manifest_doc, labels, parts, queues, baseline["timing"], approximate)
        for field in ("manifest_sha256", "labels_fingerprint", "labels_revision", "rules", "segments", "labels",
                      "items", "metrics", "unscored_labels"):
            with self.subTest(field=field):
                self.assertEqual(combined[field], baseline[field])


if __name__ == "__main__":
    unittest.main()
