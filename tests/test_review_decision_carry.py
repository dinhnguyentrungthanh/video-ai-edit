"""Decisions carried onto a rebuilt review queue (session D; review of 220d25f)."""
import copy
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.review_decision_carry import carry_review_decisions
from biliflow.review_workflow import build_review_queue

BAND = {"x": 0, "y": 35, "width": 1280, "height": 68}
PIECE = {"x": 300, "y": 40, "width": 100, "height": 40}
NO_REASONS = {"conflict": 0, "not_wider": 0, "moments_outside": 0, "no_match": 0}


def _card(card_id, start, end, region, decision=None, **extra):
    card = {
        "id": card_id, "category": "text", "start_seconds": start, "end_seconds": end,
        "suggested_region_source_pixels": region, "candidate_type": None, "decision": decision,
        "decision_region_source_pixels": region if decision == "BLUR" else None,
        "decided_at": "2026-10-12T10:00:00+00:00" if decision else None,
        "source_frame_size": [1280, 640],
    }
    card.update(extra)
    return card


def _moments(*spans):
    return {"temporal_policy": "discrete_detected_intervals",
            "detected_intervals": [{"start_seconds": a, "end_seconds": b} for a, b in spans]}


class CarryTests(unittest.TestCase):
    def test_a_keep_of_one_piece_does_not_resolve_a_card_for_the_whole_band(self):
        old = _card("old", 10.0, 20.0, PIECE, "KEEP")
        items, _, summary = carry_review_decisions([old], [_card("new", 10.0, 20.0, BAND)], [])
        new = next(item for item in items if item["id"] == "new")
        self.assertIsNone(new["decision"])
        self.assertIn(old["id"], [item["id"] for item in items])  # the old KEEP comes back unchanged
        self.assertEqual(summary["restored_unchanged"], 1)

    def test_a_cut_never_reaches_time_nobody_reviewed(self):
        reviewed = _card("old", 11.0, 20.0, PIECE, "CUT", detected_interval={"start_seconds": 10.0,
                                                                              "end_seconds": 20.0})
        for new in (_card("new-a", 10.0, 20.2, PIECE), _card("new-b", 10.0, 22.4, PIECE)):
            with self.subTest(new=new["id"]):
                items, _, _ = carry_review_decisions([reviewed], [new], [])
                carried = next(item for item in items if item["id"] == new["id"])
                self.assertIsNone(carried["decision"])
                restored = next(item for item in items if item["id"] == "old")
                self.assertEqual((restored["start_seconds"], restored["end_seconds"], restored["decision"]),
                                 (11.0, 20.0, "CUT"))

    def test_a_cut_carries_to_a_card_inside_the_reviewed_time_and_box(self):
        old = _card("old", 10.0, 20.0, BAND, "CUT")
        items, _, summary = carry_review_decisions([old], [_card("new", 10.5, 19.0, PIECE)], [])
        self.assertEqual(next(item for item in items if item["id"] == "new")["decision"], "CUT")
        self.assertEqual(summary, {"previous_decisions": 1, "carried": 1, "restored_unchanged": 0,
                                   "conflicts": 0, "restored_reasons": NO_REASONS})

    def test_the_same_id_with_other_moments_is_not_the_same_card(self):
        old = _card("same", 100.0, 160.0, None, "CUT", category="violence", **_moments((100, 110), (150, 160)))
        new = _card("same", 100.0, 160.0, None, category="violence", **_moments((100, 110), (130, 140),
                                                                                (150, 160)))
        items, _, _ = carry_review_decisions([old], [new], [])
        decisions = sorted((item["decision"] or "-") for item in items)
        self.assertEqual(decisions, ["-", "CUT"])  # the new card waits; the old CUT stays as it was
        restored = next(item for item in items if item.get("migration_status"))
        self.assertEqual(restored["detected_intervals"], old["detected_intervals"])

    def test_the_same_id_and_moments_carry_exactly(self):
        moments = _moments((100, 110), (150, 160))
        old = _card("same", 100.0, 160.0, None, "CUT", category="violence", **moments)
        items, _, summary = carry_review_decisions([old], [_card("same", 100.0, 160.0, None,
                                                                 category="violence", **moments)], [])
        self.assertEqual([item["decision"] for item in items], ["CUT"])
        self.assertEqual(summary["carried"], 1)

    def test_a_blur_onto_separate_moments_keeps_their_region_and_the_old_blur_beside_them(self):
        watermark = {"x": 552, "y": 470, "width": 200, "height": 32}
        covered = _card("covered", 27.0, 40.0, watermark, "BLUR")
        wide = _card("wide", 300.0, 318.0, {"x": 297, "y": 409, "width": 715, "height": 87}, "BLUR")
        group = _card("group", 24.0, 400.0, watermark, **_moments((24.0, 75.5), (297.0, 320.0)))
        items, _, summary = carry_review_decisions([covered, wide], [group], [])
        new = next(item for item in items if item["id"] == "group")
        self.assertEqual(new["decision"], "BLUR")
        self.assertEqual(new["decision_region_source_pixels"], watermark)
        self.assertEqual((new["start_seconds"], new["end_seconds"]), (24.0, 400.0))
        # The wide old blur reaches outside the watermark: it still applies as it was.
        restored = [item["id"] for item in items if item.get("migration_status")]
        self.assertEqual(restored, ["wide"])
        self.assertEqual(summary["carried"], 1)

    def test_a_blur_widens_a_card_of_one_span(self):
        old = _card("old", 2928.0, 2943.5, {"x": 0, "y": 35, "width": 1280, "height": 68}, "BLUR")
        new = _card("new", 2922.0, 2943.5, {"x": 0, "y": 30, "width": 1280, "height": 80})
        items, _, _ = carry_review_decisions([old], [new], [])
        [carried] = items
        self.assertEqual(carried["decision_region_source_pixels"], {"x": 0, "y": 30, "width": 1280, "height": 80})
        self.assertEqual(carried["decision_carried_from"], ["old"])

    def test_an_old_card_carried_to_its_own_card_does_not_vote_on_another(self):
        # Job 134 rev 2 (2026-10-16): the watermark card grew to the film's end (a new id). A KEEP on a
        # subtitle line inside its box and time, carried to the line's own card, left it open as a conflict.
        watermark = {"x": 552, "y": 443, "width": 176, "height": 31}
        line = {"x": 333, "y": 408, "width": 618, "height": 65}
        blur = _card("mark", 0.0, 2643.5, watermark, "BLUR", **_moments((0.0, 2643.5)))
        keep = _card("line", 2253.0, 2259.5, line, "KEEP")
        longer = _card("mark-longer", 0.0, 2657.2, watermark, **_moments((0.0, 2657.2)))
        for home in ("main", "optional"):
            with self.subTest(line_card=home):
                own = _card("line", 2253.0, 2259.5, line, **({"advisory": True} if home == "optional" else {}))
                items, optional, summary = carry_review_decisions(
                    [blur, keep], [longer] + ([own] if home == "main" else []), [own] if home == "optional" else [])
                by_id = {item["id"]: item for item in items}
                self.assertEqual(by_id["mark-longer"]["decision"], "BLUR")
                self.assertEqual(by_id["mark-longer"]["decision_carried_from"], ["mark"])
                self.assertNotIn("decision_conflict", by_id["mark-longer"])
                self.assertEqual(by_id["line"]["decision"], "KEEP")
                self.assertEqual(optional, [])
                self.assertEqual(summary, {"previous_decisions": 2, "carried": 2, "restored_unchanged": 0,
                                           "conflicts": 0, "restored_reasons": NO_REASONS})

    def test_a_blur_of_separate_moments_carries_to_the_same_moments_grown(self):
        # Two moments, the last grown to the film's end: each old moment lies inside a new one, so the
        # old card does not come back beside the new one.
        watermark = {"x": 552, "y": 443, "width": 176, "height": 31}
        old = _card("mark", 0.0, 2559.5, watermark, "BLUR", **_moments((0.0, 1000.5), (1297.0, 2559.5)))
        grown = _card("mark-longer", 0.0, 2700.0, watermark, **_moments((0.0, 1000.5), (1297.0, 2700.0)))
        items, _, summary = carry_review_decisions([old], [grown], [])
        [carried] = items
        self.assertEqual((carried["id"], carried["decision"], carried["decision_carried_from"]),
                         ("mark-longer", "BLUR", ["mark"]))
        self.assertEqual((summary["carried"], summary["restored_unchanged"]), (1, 0))
        # An old moment outside every new moment still brings the old card back unchanged.
        shorter = _card("mark-other", 0.0, 2700.0, watermark, **_moments((0.0, 900.0), (1297.0, 2700.0)))
        items, _, summary = carry_review_decisions([old], [shorter], [])
        self.assertEqual(sorted(item["id"] for item in items), ["mark", "mark-other"])
        self.assertEqual(summary["restored_unchanged"], 1)

    def test_old_cards_without_their_own_card_still_disagree(self):
        watermark = {"x": 552, "y": 443, "width": 176, "height": 31}
        blur = _card("mark", 0.0, 2643.5, watermark, "BLUR", **_moments((0.0, 2643.5)))
        keep = _card("line", 2253.0, 2259.5, {"x": 333, "y": 408, "width": 618, "height": 65}, "KEEP")
        longer = _card("mark-longer", 0.0, 2657.2, watermark, **_moments((0.0, 2657.2)))
        items, _, summary = carry_review_decisions([blur, keep], [longer], [])
        new = next(item for item in items if item["id"] == "mark-longer")
        self.assertIsNone(new["decision"])
        self.assertEqual(new["decision_conflict"], ["line", "mark"])
        self.assertEqual((summary["conflicts"], summary["restored_unchanged"]), (1, 2))

    def test_an_optional_card_with_the_old_id_moves_to_the_main_list(self):
        old = _card("adv", 50.0, 53.5, PIECE, "BLUR")
        items, optional, summary = carry_review_decisions(
            [old], [], [_card("adv", 50.0, 53.5, PIECE, advisory=True)])
        self.assertEqual(optional, [])
        self.assertEqual(items[0]["decision"], "BLUR")
        self.assertNotIn("advisory", items[0])
        self.assertEqual(summary["carried"], 1)

    def test_the_summary_says_why_each_decision_came_back_unchanged(self):
        watermark = {"x": 552, "y": 443, "width": 176, "height": 31}
        cases = [
            # A KEEP of one piece meets only the card of the whole band.
            ([_card("old", 10.0, 20.0, PIECE, "KEEP")], [_card("new", 10.0, 20.0, BAND)], "not_wider"),
            # Two old cards disagree on the one new card.
            ([_card("mark", 0.0, 2643.5, watermark, "BLUR", **_moments((0.0, 2643.5))),
              _card("line", 2253.0, 2259.5, {"x": 333, "y": 408, "width": 618, "height": 65}, "KEEP")],
             [_card("mark-longer", 0.0, 2657.2, watermark, **_moments((0.0, 2657.2)))], "conflict"),
            # A blur with a moment outside the separate moments of the new card.
            ([_card("mark", 0.0, 2559.5, watermark, "BLUR", **_moments((0.0, 1000.5), (1297.0, 2559.5)))],
             [_card("mark-other", 0.0, 2700.0, watermark, **_moments((0.0, 900.0), (1297.0, 2700.0)))],
             "moments_outside"),
            # Nothing in the new queue shows it.
            ([_card("old", 10.0, 20.0, PIECE, "CUT")], [], "no_match"),
        ]
        for previous, items, reason in cases:
            with self.subTest(reason=reason):
                _, _, summary = carry_review_decisions(previous, items, [])
                self.assertEqual(summary["restored_reasons"],
                                 {**NO_REASONS, reason: summary["restored_unchanged"]})
                self.assertGreater(summary["restored_unchanged"], 0)

    def test_inputs_are_not_modified(self):
        previous = [_card("old", 10.0, 20.0, BAND, "BLUR")]
        items = [_card("new", 8.0, 20.0, BAND)]
        advisory = [_card("adv", 50.0, 53.5, PIECE, advisory=True)]
        before = copy.deepcopy((previous, items, advisory))
        carry_review_decisions(previous, items, advisory)
        self.assertEqual((previous, items, advisory), before)


class KeepDecisionsRefusalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for name in ("reports", "work", "input"):
            (self.root / name).mkdir()
        self.source = self.root / "input" / "source.mp4"
        self.source.write_bytes(b"source")
        directory = self.root / "reports" / "job" / "text"
        directory.mkdir(parents=True)
        self.report = directory / "text-scan.json"
        self.report.write_text(json.dumps({
            "status": "REVIEW_REQUIRED", "input": str(self.source), "duration_seconds": 600.0,
            "sample_every_seconds": 3.0, "source_size": [1280, 640], "analysis_size": [960, 480],
            "tracks": [],
        }), encoding="utf-8")
        self.queue = self.root / "reports" / "job" / "review-queue.json"

    def tearDown(self):
        self.temporary.cleanup()

    def _rebuild(self):
        return build_review_queue(project_root=self.root, report_paths=[self.report], queue_path=self.queue,
                                  keep_decisions=True)

    def test_an_unreadable_old_queue_is_never_overwritten(self):
        self.queue.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            self._rebuild()
        self.assertEqual(self.queue.read_text(encoding="utf-8"), "{not json")

    def test_decisions_on_another_source_are_never_overwritten(self):
        other = self.root / "input" / "other.mp4"
        other.write_bytes(b"other")
        text = json.dumps({"source": {"path": str(other.resolve()), "sha256": None},
                           "items": [_card("old", 10.0, 20.0, BAND, "BLUR")]})
        self.queue.write_text(text, encoding="utf-8")
        with self.assertRaises(ValueError):
            self._rebuild()
        self.assertEqual(self.queue.read_text(encoding="utf-8"), text)


if __name__ == "__main__":
    unittest.main()
