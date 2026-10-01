import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from biliflow.golden_set import (
    FilmLogoConflict, LabelStore, StaleRevision, easy_cards, merge_suggestions, queue_suggestions, validate_event, validate_manifest,
    watermark_candidates,
)

SHA = "a" * 64
BOX = {"x": 100, "y": 100, "width": 200, "height": 50}


def manifest():
    return {
        "schema_version": 1, "golden_set": "v1",
        "sources": {"src": {"sha256": SHA, "duration_seconds": 1000.0, "width": 1920, "height": 1080}},
        "segments": [
            {"id": "S1", "source": "src", "start_seconds": 0.0, "end_seconds": 100.0, "split": "dev"},
            {"id": "S2", "source": "src", "start_seconds": 200.0, "end_seconds": 300.0, "split": "holdout"},
        ],
    }


def event(**changes):
    payload = {"segment_id": "S1", "category": "visual_logo", "start_seconds": 0, "end_seconds": 1000,
               "region_source_pixels": BOX, "expected_action": "BLUR", "severity": "must_catch"}
    payload.update(changes)
    return payload


SUGGESTION = {"id": "sug-1", "segment_id": "S1", "category": "adult", "group": "adult",
              "start_seconds": 10.0, "end_seconds": 20.0, "region_source_pixels": None}


class ValidationTest(unittest.TestCase):
    def test_valid_event_is_normalized(self):
        value = validate_event(event(notes="  xembz  "), manifest())
        self.assertEqual((value["notes"], value["ambiguous"], value["region_source_pixels"]), ("xembz", False, BOX))

    def test_invalid_events_are_rejected(self):
        for changes in ({"region_source_pixels": None}, {"expected_action": "DELETE"},
                        {"start_seconds": 500, "end_seconds": 600}, {"start_seconds": 20, "end_seconds": 10},
                        {"severity": None}, {"region_source_pixels": {"x": 1900, "y": 0, "width": 50, "height": 10}},
                        {"segment_id": "S9"}, {"category": "music"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_event(event(**changes), manifest())
        keep = validate_event(event(expected_action="KEEP", severity=None), manifest())
        self.assertIsNone(keep["severity"])
        full_frame_adult = validate_event(event(category="adult", region_source_pixels=None), manifest())
        self.assertIsNone(full_frame_adult["region_source_pixels"])

    def test_manifest_segment_outside_source_is_rejected(self):
        bad = manifest()
        bad["segments"][1]["end_seconds"] = 2000.0
        with self.assertRaises(ValueError):
            validate_manifest(bad)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name) / "golden"
        self.opened = []

    def tearDown(self):
        for store in self.opened:
            store.close()
        self.temp.cleanup()

    def store(self, suggestions=(SUGGESTION,)):
        store = LabelStore(self.dir, manifest(), list(suggestions))
        self.opened.append(store)
        return store

    def test_upsert_delete_history_and_backup(self):
        store = self.store()
        created = store.upsert_event(event(), 0)
        self.assertEqual(created["id"], "gs-S1-0001")
        updated = store.upsert_event(dict(created, notes="sửa"), 1)
        self.assertEqual((updated["labeled_at"], updated["notes"]), (created["labeled_at"], "sửa"))
        store.delete_event(created["id"], 2)
        history = [json.loads(line) for line in (self.dir / "label-history.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([h["action"] for h in history], ["upsert_event", "upsert_event", "delete_event"])
        self.assertEqual(history[-1]["before"]["notes"], "sửa")
        store.close()
        reopened = self.store()
        self.assertEqual(reopened.state()["revision"], 3)
        self.assertEqual(len(list((self.dir / "backups").glob("events-*.json"))), 1)

    def test_second_writer_is_refused_while_the_store_is_open(self):
        first = self.store()
        with self.assertRaisesRegex(ValueError, "đang được mở"):
            LabelStore(self.dir, manifest(), [SUGGESTION])
        first.close()
        self.store()  # the lock is released on close

    def test_failed_write_leaves_the_saved_state_unchanged(self):
        store = self.store()
        with patch("biliflow.golden_set.write_json_atomic", side_effect=PermissionError("locked by reader")):
            with self.assertRaises(PermissionError):
                store.upsert_event(event(), 0)
        self.assertEqual((store.state()["revision"], store.state()["events"]), (0, []))
        self.assertFalse((self.dir / "label-history.jsonl").exists())
        self.assertEqual(store.upsert_event(event(), 0)["id"], "gs-S1-0001")

    def test_stale_revision_is_refused(self):
        store = self.store()
        store.upsert_event(event(), 0)
        with self.assertRaises(StaleRevision):
            store.upsert_event(event(), 0)

    def test_segment_completion_requires_resolved_suggestions(self):
        store = self.store()
        with self.assertRaises(ValueError):
            store.set_segment_status("S1", "complete", 0)
        store.resolve_suggestion("sug-1", "rejected", 0)
        store.set_segment_status("S1", "complete", 1)
        with self.assertRaises(ValueError):  # complete segments are read-only until reopened
            store.upsert_event(event(), 2)
        store.set_segment_status("S1", "in_progress", 2)
        store.resolve_suggestion("sug-1", None, 3)
        self.assertEqual(store.unresolved("S1"), ["sug-1"])

    def test_accepting_and_deleting_a_suggestion_label(self):
        store = self.store()
        accepted = store.upsert_event(event(category="adult", region_source_pixels=None, start_seconds=10,
                                            end_seconds=20, ambiguous=True, from_suggestion="sug-1"), 0)
        self.assertEqual(store.state()["suggestion_resolutions"]["sug-1"]["resolution"], "ambiguous")
        with self.assertRaises(ValueError):
            store.resolve_suggestion("sug-1", "rejected", 1)
        store.delete_event(accepted["id"], 1)
        self.assertEqual(store.unresolved("S1"), ["sug-1"])

    def test_a_linked_suggestion_cannot_back_a_second_label(self):
        store = self.store()
        payload = event(category="adult", region_source_pixels=None, start_seconds=10, end_seconds=20,
                        from_suggestion="sug-1")
        first = store.upsert_event(dict(payload, ambiguous=True), 0)
        with self.assertRaisesRegex(ValueError, first["id"]):
            store.upsert_event(payload, 1)
        edited = store.upsert_event(dict(first, ambiguous=False), 1)  # editing the owner is fine
        self.assertEqual(store.state()["suggestion_resolutions"]["sug-1"]["resolution"], "accepted")
        self.assertEqual(edited["id"], first["id"])

    def test_label_whose_suggestion_vanished_stays_editable(self):
        store = self.store()
        created = store.upsert_event(event(category="adult", region_source_pixels=None, start_seconds=10,
                                           end_seconds=20, from_suggestion="sug-1"), 0)
        store.close()
        reopened = self.store(suggestions=())  # e.g. suggestions.json rebuilt without sug-1
        edited = reopened.upsert_event(dict(created, notes="vẫn sửa được"), 1)
        self.assertEqual((edited["from_suggestion"], edited["notes"]), ("sug-1", "vẫn sửa được"))
        with self.assertRaisesRegex(ValueError, "không tồn tại"):
            reopened.upsert_event(event(from_suggestion="sug-2"), 2)

    def test_ambiguous_logo_label_may_have_no_region(self):
        store = self.store()
        saved = store.upsert_event(event(region_source_pixels=None, ambiguous=True), 0)
        self.assertIsNone(saved["region_source_pixels"])

    def test_labels_for_a_different_manifest_are_not_opened(self):
        self.store().close()
        other = manifest()
        other["segments"][0]["end_seconds"] = 90.0
        with self.assertRaisesRegex(ValueError, "manifest khác"):
            LabelStore(self.dir, other)
        self.store()  # a refused open released its lock


class SuggestionTest(unittest.TestCase):
    def test_queue_items_are_clipped_merged_and_keep_prior_decisions(self):
        queue = {"source": {"sha256": SHA}, "items": [
            {"id": "p", "category": "visual_logo", "start_seconds": 0, "end_seconds": 1000,
             "suggested_region_source_pixels": BOX, "suggested_decision": "BLUR", "decision": "BLUR",
             "decided_at": "2026-09-27T00:00:00"},
            {"id": "w", "category": "visual_logo", "start_seconds": 40, "end_seconds": 45,
             "suggested_region_source_pixels": {"x": 105, "y": 102, "width": 190, "height": 48}},
            {"id": "n", "category": "adult", "start_seconds": 250, "end_seconds": 260,
             "suggested_region_source_pixels": "FULL_FRAME", "decision": "KEEP", "decided_at": "2026-09-28"},
        ], "advisory_items": [{"id": "a", "category": "text", "start_seconds": 60, "end_seconds": 70,
                               "suggested_region_source_pixels": {"x": 900, "y": 900, "width": 50, "height": 50}}]}
        rows = queue_suggestions(queue, manifest(), "job1-r1")
        self.assertEqual(len(rows), 5)  # persistent item appears in both segments
        merged = merge_suggestions(rows)
        by_segment = {}
        for row in merged:
            by_segment.setdefault(row["segment_id"], []).append(row)
        watermark = next(r for r in by_segment["S1"] if r["merged_count"] == 2)
        self.assertEqual((watermark["start_seconds"], watermark["end_seconds"], watermark["prior_decision"]),
                         (0.0, 100.0, "BLUR"))
        self.assertEqual(len(by_segment["S1"]), 2)
        self.assertEqual(sorted(r["prior_decision"] or "" for r in by_segment["S2"]), ["BLUR", "KEEP"])
        self.assertEqual([r["id"] for r in merge_suggestions(list(reversed(rows)))], [r["id"] for r in merged])

    def test_other_source_is_ignored(self):
        self.assertEqual(queue_suggestions({"source": {"sha256": "b" * 64}, "items": []}, manifest(), "x"), [])


if __name__ == "__main__":
    unittest.main()


class EasyModeTest(unittest.TestCase):
    WM = {"x": 1500, "y": 40, "width": 300, "height": 60}

    def manifest(self):
        value = manifest()
        value["segments"].append({"id": "S3", "source": "src", "start_seconds": 400.0, "end_seconds": 500.0,
                                  "split": "dev"})
        return value

    def suggestions(self):
        def row(id, segment, start, end, region, advisory=False, prior=None, kind=None, labels=("x",)):
            return {"id": id, "segment_id": segment, "category": "visual_logo", "group": "advertising",
                    "start_seconds": start, "end_seconds": end, "item_start_seconds": start, "item_end_seconds": end,
                    "region_source_pixels": region, "advisory": advisory, "candidate_type": kind,
                    "labels": list(labels), "prior_decisions": prior or [], "prior_decision": None, "tier": "review"}
        tight = {"x": 1520, "y": 45, "width": 250, "height": 45}
        return [
            row("w1", "S1", 0.0, 1000.0, self.WM, kind="persistent_overlay"),
            row("w2", "S2", 0.0, 1000.0, tight, labels=("Persistent external logo / watermark",),
                prior=[{"decision": "BLUR", "region_source_pixels": tight, "decided_at": "2026-09-29"}]),
            row("short", "S1", 10.0, 20.0, {"x": 10, "y": 10, "width": 50, "height": 50}, kind="persistent_overlay"),
            row("main", "S1", 30.0, 35.0, {"x": 800, "y": 500, "width": 100, "height": 40}),
            row("junk", "S1", 40.0, 45.0, None, advisory=True),
            row("kept", "S2", 250.0, 255.0, None, advisory=True),
        ]

    def test_watermark_candidates_group_boxes_and_prefer_the_users_latest_box(self):
        candidates = watermark_candidates(self.manifest(), self.suggestions())
        self.assertEqual(len(candidates), 1)  # the 10 s overlay is too short for a film-long watermark
        candidate = candidates[0]
        self.assertEqual(candidate["suggestion_ids"], ["w1", "w2"])
        self.assertEqual(candidate["region_source_pixels"], {"x": 1520, "y": 45, "width": 250, "height": 45})
        self.assertTrue(candidate["prior_blur"])

    def test_easy_cards_hide_watermark_members_and_low_value_advisories(self):
        suggestions = self.suggestions()
        suggestions[-1]["prior_decision"] = "BLUR"  # an advisory the user once blurred stays visible
        candidates = watermark_candidates(self.manifest(), suggestions)
        cards = easy_cards(suggestions, {"watermark_decisions": {}}, candidates)
        self.assertEqual(cards, {"S1": ["short", "main"], "S2": ["kept"]})
        declined = easy_cards(suggestions, {"watermark_decisions": {candidates[0]["id"]: {"decision": "declined"}}},
                              candidates)
        self.assertIn("w1", declined["S1"])

    def test_confirm_watermark_labels_every_open_segment_once(self):
        with tempfile.TemporaryDirectory() as temp:
            suggestions = self.suggestions()
            store = LabelStore(Path(temp), self.manifest(), suggestions)
            try:
                store.resolve_suggestion("main", "rejected", 0)
                store.resolve_suggestion("short", "rejected", 1)
                store.resolve_suggestion("junk", "rejected", 2)
                store.resolve_suggestion("w1", "rejected", 3)
                store.set_segment_status("S1", "complete", 4)  # completed segments are not touched
                candidate = watermark_candidates(self.manifest(), suggestions)[0]
                created = store.confirm_watermark(candidate, 5)
                self.assertEqual([(e["segment_id"], e["start_seconds"], e["end_seconds"]) for e in created],
                                 [("S2", 200.0, 300.0), ("S3", 400.0, 500.0)])
                state = store.state()
                self.assertEqual(state["suggestion_resolutions"]["w2"]["resolution"], "covered")
                self.assertEqual(state["watermark_decisions"][candidate["id"]]["decision"], "confirmed")
                with self.assertRaises(ValueError):
                    store.confirm_watermark(candidate, 6)
                store.delete_event(created[0]["id"], 6)  # undoing the label reopens its covered suggestions
                self.assertNotIn("w2", store.state()["suggestion_resolutions"])
            finally:
                store.close()


class WatermarkDedupeTest(unittest.TestCase):
    def test_confirm_skips_segments_that_already_have_the_watermark_label(self):
        wm = {"x": 1500, "y": 40, "width": 300, "height": 60}
        suggestion = {"id": "w1", "segment_id": "S1", "category": "visual_logo", "group": "advertising",
                      "start_seconds": 0.0, "end_seconds": 100.0, "item_start_seconds": 0.0, "item_end_seconds": 1000.0,
                      "region_source_pixels": wm, "advisory": False, "candidate_type": "persistent_overlay",
                      "labels": [], "prior_decisions": [], "tier": "review"}
        with tempfile.TemporaryDirectory() as temp:
            store = LabelStore(Path(temp), manifest(), [suggestion])
            try:
                store.upsert_event(event(category="text", start_seconds=0, end_seconds=100,
                                         region_source_pixels={"x": 1490, "y": 35, "width": 320, "height": 70}), 0)
                candidate = watermark_candidates(manifest(), [suggestion])[0]
                created = store.confirm_watermark(candidate, 1)
                self.assertEqual([e["segment_id"] for e in created], ["S2"])  # S1 already labelled by the user
            finally:
                store.close()


class FilmLogoTest(unittest.TestCase):
    LOGO = {"x": 20, "y": 900, "width": 200, "height": 80}

    def test_user_added_film_logo_labels_open_segments_once(self):
        with tempfile.TemporaryDirectory() as temp:
            store = LabelStore(Path(temp), manifest(), [])
            try:
                created = store.add_film_logo("src", self.LOGO, 0, 1000, 0)
                self.assertEqual([(e["segment_id"], e["start_seconds"], e["end_seconds"]) for e in created],
                                 [("S1", 0.0, 100.0), ("S2", 200.0, 300.0)])
                self.assertTrue(all("người dùng thêm" in e["notes"] for e in created))
                with self.assertRaisesRegex(ValueError, "đã được đánh dấu"):  # same box: nothing to add
                    store.add_film_logo("src", self.LOGO, 0, 1000, 1)
                self.assertEqual(store.state()["revision"], 1)  # a refused add writes nothing
                text = store.add_film_logo("src", {"x": 1500, "y": 900, "width": 200, "height": 80}, 250, 1000, 1,
                                           category="text")
                self.assertEqual([(e["segment_id"], e["start_seconds"], e["category"]) for e in text],
                                 [("S2", 250.0, "text")])
                for bad in ({"source": "nope"}, {"region": None}, {"start": 10, "end": 10.5}):
                    args = {"source": "src", "region": self.LOGO, "start": 0, "end": 1000} | bad
                    with self.subTest(bad=bad), self.assertRaises(ValueError):
                        store.add_film_logo(args["source"], args["region"], args["start"], args["end"], 2)
            finally:
                store.close()

    def test_larger_box_asks_before_enlarging_existing_labels(self):
        with tempfile.TemporaryDirectory() as temp:
            store = LabelStore(Path(temp), manifest(), [])
            try:
                store.add_film_logo("src", self.LOGO, 0, 1000, 0)
                bigger = {"x": 0, "y": 860, "width": 300, "height": 160}
                with self.assertRaises(FilmLogoConflict) as caught:
                    store.add_film_logo("src", bigger, 0, 1000, 1)
                self.assertEqual(caught.exception.code, "enlarge")
                self.assertEqual(store.state()["revision"], 1)
                changed = store.add_film_logo("src", bigger, 0, 1000, 1, replace=True)
                self.assertEqual({e["id"] for e in changed}, {"gs-S1-0001", "gs-S2-0001"})
                self.assertEqual({str(e["region_source_pixels"]) for e in store.state()["events"]}, {str(bigger)})
                history = (Path(temp) / "label-history.jsonl").read_text(encoding="utf-8")
                self.assertIn('"enlarged"', history)  # the old boxes stay recoverable
            finally:
                store.close()


class ResetSegmentTest(unittest.TestCase):
    def test_redo_keeps_film_long_labels_and_history(self):
        with tempfile.TemporaryDirectory() as temp:
            store = LabelStore(Path(temp), manifest(), [SUGGESTION])
            try:
                store.add_film_logo("src", {"x": 20, "y": 900, "width": 200, "height": 80}, 0, 1000, 0)
                store.upsert_event(event(category="adult", region_source_pixels=None, start_seconds=10,
                                         end_seconds=20, from_suggestion="sug-1"), 1)
                store.set_segment_status("S1", "complete", 2)
                self.assertEqual(store.reset_segment("S1", 3), 1)
                state = store.state()
                self.assertEqual([e["notes"] for e in state["events"] if e["segment_id"] == "S1"],
                                 ["Logo suốt phim do người dùng thêm"])
                self.assertEqual((state["segments"]["S1"]["status"], state["suggestion_resolutions"]), ("in_progress", {}))
                history = (Path(temp) / "label-history.jsonl").read_text(encoding="utf-8")
                self.assertIn('"reset_segment"', history)
                self.assertIn('"from_suggestion": "sug-1"', history)  # the removed answer is recoverable
            finally:
                store.close()


class ResetKeepsWholeSegmentLogoTest(unittest.TestCase):
    def test_redo_keeps_a_logo_label_that_spans_the_segment_whatever_its_origin(self):
        with tempfile.TemporaryDirectory() as temp:
            store = LabelStore(Path(temp), manifest(), [])
            try:
                kept = store.upsert_event(event(category="text", start_seconds=0, end_seconds=100), 0)
                store.upsert_event(event(start_seconds=10, end_seconds=15), 1)  # a short logo answer
                self.assertEqual(store.reset_segment("S1", 2), 1)
                self.assertEqual([e["id"] for e in store.state()["events"]], [kept["id"]])
            finally:
                store.close()


class CoverSuggestionTest(unittest.TestCase):
    LOGO = {"id": "logo-q", "segment_id": "S1", "category": "visual_logo", "group": "advertising",
            "start_seconds": 30.0, "end_seconds": 35.0, "region_source_pixels": None}

    def test_a_question_answered_by_the_watermark_label_is_covered_without_a_new_label(self):
        with tempfile.TemporaryDirectory() as temp:
            store = LabelStore(Path(temp), manifest(), [self.LOGO, SUGGESTION])
            try:
                watermark = store.upsert_event(event(start_seconds=0, end_seconds=100), 0)
                store.cover_suggestion("logo-q", watermark["id"], 1)
                state = store.state()
                self.assertEqual(len(state["events"]), 1)
                self.assertEqual(state["suggestion_resolutions"]["logo-q"]["resolution"], "covered")
                self.assertEqual(state["suggestion_resolutions"]["logo-q"]["event_id"], watermark["id"])
                with self.assertRaises(ValueError):  # a logo label cannot cover an 18+ question
                    store.cover_suggestion("sug-1", watermark["id"], 2)
                store.delete_event(watermark["id"], 2)  # deleting the cover reopens the question
                self.assertNotIn("logo-q", store.state()["suggestion_resolutions"])
            finally:
                store.close()

    def test_cover_needs_an_overlapping_label_and_an_editable_segment(self):
        with tempfile.TemporaryDirectory() as temp:
            store = LabelStore(Path(temp), manifest(), [self.LOGO])
            try:
                late = store.upsert_event(event(start_seconds=50, end_seconds=60), 0)
                with self.assertRaises(ValueError):
                    store.cover_suggestion("logo-q", late["id"], 1)
                unsure = store.upsert_event(event(start_seconds=0, end_seconds=100, ambiguous=True), 1)
                with self.assertRaises(ValueError):
                    store.cover_suggestion("logo-q", unsure["id"], 2)
                store.cover_suggestion("logo-q", store.upsert_event(event(start_seconds=0, end_seconds=100,
                                                                           region_source_pixels={"x": 5, "y": 5, "width": 40, "height": 40}), 2)["id"], 3)
                store.set_segment_status("S1", "complete", 4)
                with self.assertRaises(ValueError):  # complete segments stay locked
                    store.cover_suggestion("logo-q", late["id"], 5)
            finally:
                store.close()


class RetiredIdTest(unittest.TestCase):
    def test_a_deleted_label_id_is_never_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            store = LabelStore(Path(temp), manifest(), [])
            try:
                first = store.upsert_event(event(start_seconds=0, end_seconds=10), 0)
                store.delete_event(first["id"], 1)
                second = store.upsert_event(event(start_seconds=0, end_seconds=10), 2)
                self.assertEqual((first["id"], second["id"]), ("gs-S1-0001", "gs-S1-0002"))
                store.reset_segment("S1", 3)
                self.assertEqual(store.upsert_event(event(start_seconds=0, end_seconds=10), 4)["id"], "gs-S1-0003")
            finally:
                store.close()


# --- Golden Set v1.1: a separate label directory scored together with v1 -------------------------

from biliflow.golden_set import (  # noqa: E402
    GOLDEN_V1_1_SEGMENTS, GOLDEN_V1_SEGMENTS, SPLITS, canonical_sha256, check_disjoint_segments, empty_labels,
    load_golden_sets, read_json, write_json_atomic,
)


def extension(parent, segments=None, **changes):
    value = {"schema_version": 1, "golden_set": "v1.1", "extends": "v1",
             "extends_manifest_sha256": canonical_sha256(parent), "sources": json.loads(json.dumps(parent["sources"])),
             "segments": segments if segments is not None else [
                 {"id": "N1", "source": "src", "start_seconds": 100.0, "end_seconds": 200.0, "split": "dev"},
                 {"id": "N2", "source": "src", "start_seconds": 500.0, "end_seconds": 600.0, "split": "holdout"}]}
    value.update(changes)
    return value


class SetManifestTest(unittest.TestCase):
    def test_v1_1_manifest_is_accepted_and_unknown_sets_are_rejected(self):
        self.assertEqual(validate_manifest(extension(manifest()))["golden_set"], "v1.1")
        for bad in (dict(manifest(), golden_set="v2"), dict(manifest(), golden_set=None),
                    dict(manifest(), golden_set="v1+v1.1")):
            with self.subTest(golden_set=bad["golden_set"]), self.assertRaisesRegex(ValueError, "Golden Set đã biết"):
                validate_manifest(bad)

    def test_extension_must_name_and_pin_its_parent(self):
        for changes in ({"extends": None}, {"extends": "v1.1"}, {"extends_manifest_sha256": None},
                        {"extends_manifest_sha256": "abc"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_manifest(extension(manifest(), **changes))
        with self.assertRaisesRegex(ValueError, "không mở rộng"):
            validate_manifest(dict(manifest(), extends="v1"))  # v1 extends nothing

    def test_new_label_files_carry_their_own_set_name(self):
        v11 = extension(manifest())
        self.assertEqual(empty_labels(v11)["golden_set"], "v1.1")
        self.assertEqual(empty_labels(manifest())["golden_set"], "v1")
        with tempfile.TemporaryDirectory() as temp:
            with LabelStore(Path(temp), v11) as store:
                self.assertEqual(store.state()["golden_set"], "v1.1")
            self.assertEqual(read_json(Path(temp) / "events.json")["manifest_sha256"], canonical_sha256(v11))

    def test_proposed_v1_1_segments_never_overlap_v1(self):
        check_disjoint_segments([("v1", {"id": sid, "source": key, "start_seconds": start, "end_seconds": end})
                                 for sid, key, start, end, *_ in GOLDEN_V1_SEGMENTS] +
                                [("v1.1", {"id": sid, "source": key, "start_seconds": start, "end_seconds": end})
                                 for sid, key, start, end, *_ in GOLDEN_V1_1_SEGMENTS])
        self.assertEqual([row[0] for row in GOLDEN_V1_1_SEGMENTS], ["T5", "T6", "T7", "T8", "C21E", "C20F"])
        self.assertTrue(all(row[4] in SPLITS and row[2] < row[3] for row in GOLDEN_V1_1_SEGMENTS))

    def test_disjoint_check_allows_touching_segments_only(self):
        def row(id, start, end, source="src"):
            return {"id": id, "source": source, "start_seconds": start, "end_seconds": end}
        check_disjoint_segments([("v1", row("A", 0, 100)), ("v1.1", row("B", 100, 200)),
                                 ("v1.1", row("C", 50, 150, source="other"))])
        with self.assertRaisesRegex(ValueError, "chồng thời gian"):
            check_disjoint_segments([("v1", row("A", 0, 100)), ("v1.1", row("B", 99.5, 200))])
        with self.assertRaisesRegex(ValueError, "Trùng ID đoạn"):
            check_disjoint_segments([("v1", row("A", 0, 100)), ("v1.1", row("A", 300, 400))])


class LoadGoldenSetsTest(unittest.TestCase):
    """load_golden_sets combines annotations/golden/<set> directories read-only for scoring."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.v1 = manifest()
        self.v11 = extension(self.v1)
        self.write("v1", self.v1, [dict(event(), id="gs-S1-0001", segment_id="S1")], complete=("S1", "S2"), revision=7)
        self.write("v1.1", self.v11, [dict(event(start_seconds=120, end_seconds=130), id="gs-N1-0001",
                                           segment_id="N1")], complete=("N1",), revision=2)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value, events=None, complete=(), revision=1, pin=None):
        write_json_atomic(self.root / name / "segments.json", value)
        if events is not None:
            labels = empty_labels(value)
            labels.update(revision=revision, events=events, manifest_sha256=pin or canonical_sha256(value))
            for segment_id in complete:
                labels["segments"][segment_id]["status"] = "complete"
            write_json_atomic(self.root / name / "events.json", labels)

    def snapshot(self):
        return sorted((str(p.relative_to(self.root)), p.stat().st_mtime_ns) for p in self.root.rglob("*"))

    def test_combined_documents_keep_every_set_apart(self):
        before = self.snapshot()
        combined, labels, parts = load_golden_sets(self.root, ["v1.1", "v1"])
        self.assertEqual(self.snapshot(), before)  # read-only: no lock, backup or label file is written
        self.assertEqual(combined["golden_set"], "v1+v1.1")
        self.assertEqual(combined["sets"], {"v1": canonical_sha256(self.v1), "v1.1": canonical_sha256(self.v11)})
        self.assertEqual([(s["id"], s["set"]) for s in combined["segments"]],
                         [("S1", "v1"), ("S2", "v1"), ("N1", "v1.1"), ("N2", "v1.1")])
        self.assertEqual(combined["sources"], self.v1["sources"])
        self.assertEqual(labels["revision"], {"v1": 7, "v1.1": 2})
        self.assertEqual([e["id"] for e in labels["events"]], ["gs-S1-0001", "gs-N1-0001"])
        self.assertEqual({k: v["status"] for k, v in labels["segments"].items()},
                         {"S1": "complete", "S2": "complete", "N1": "complete", "N2": "unlabeled"})
        self.assertEqual(labels["manifest_sha256"], canonical_sha256(combined))
        self.assertEqual(parts["v1"]["manifest"], self.v1)
        self.assertEqual(parts["v1.1"]["labels_path"], self.root / "v1.1" / "events.json")
        with self.assertRaisesRegex(ValueError, "Golden Set đã biết"):  # a LabelStore never opens it
            LabelStore(self.root / "combined", combined)

    def test_a_single_set_keeps_its_own_documents_and_pin(self):
        combined, labels, parts = load_golden_sets(self.root, ["v1"])
        self.assertEqual(canonical_sha256(combined), canonical_sha256(self.v1))
        self.assertIs(labels, parts["v1"]["labels"])
        self.assertEqual(labels["revision"], 7)
        alone, _, _ = load_golden_sets(self.root, ["v1.1"])  # the parent pin is checked even when v1 is not scored
        self.assertEqual(alone["golden_set"], "v1.1")

    def test_a_set_without_labels_yet_reads_as_unlabelled(self):
        (self.root / "v1.1" / "events.json").unlink()
        combined, labels, parts = load_golden_sets(self.root, ["v1", "v1.1"])
        self.assertTrue(parts["v1.1"]["labels_missing"])
        self.assertIsNone(parts["v1.1"]["labels_path"])
        self.assertEqual((labels["segments"]["N1"]["status"], labels["revision"]["v1.1"]), ("unlabeled", 0))
        self.assertFalse((self.root / "v1.1" / "events.json").exists())
        with self.assertRaisesRegex(ValueError, "Không có tệp nhãn"):  # an explicit labels file must exist
            load_golden_sets(self.root, ["v1.1"], {"v1.1": self.root / "missing.json"})

    def test_labels_override_replaces_one_sets_file(self):
        write_json_atomic(self.root / "throwaway.json", empty_labels(self.v1))
        _, labels, parts = load_golden_sets(self.root, ["v1", "v1.1"], {"v1": self.root / "throwaway.json"})
        self.assertEqual(parts["v1"]["labels_path"], self.root / "throwaway.json")
        self.assertEqual([e["id"] for e in labels["events"]], ["gs-N1-0001"])
        with self.assertRaisesRegex(ValueError, "không được chọn"):
            load_golden_sets(self.root, ["v1"], {"v1.1": self.root / "throwaway.json"})

    def test_unknown_or_repeated_sets_are_refused(self):
        for names in (["v2"], [], ["v1", "v1"]):
            with self.subTest(names=names), self.assertRaises(ValueError):
                load_golden_sets(self.root, names)
        write_json_atomic(self.root / "v1.1" / "segments.json", self.v1)  # a v1 manifest in the v1.1 directory
        with self.assertRaisesRegex(ValueError, "không phải v1.1"):
            load_golden_sets(self.root, ["v1", "v1.1"])

    def test_labels_pinned_to_another_manifest_are_refused(self):
        self.write("v1.1", self.v11, [], pin="b" * 64)
        with self.assertRaisesRegex(ValueError, "different manifest"):
            load_golden_sets(self.root, ["v1", "v1.1"])

    def test_extension_of_a_changed_parent_is_refused(self):
        changed = manifest()
        changed["segments"][0]["purpose"] = "đã sửa"
        self.write("v1", changed, [], revision=1)
        for names in (["v1", "v1.1"], ["v1.1"]):
            with self.subTest(names=names), self.assertRaisesRegex(ValueError, "manifest v1 khác"):
                load_golden_sets(self.root, names)

    def test_duplicate_segment_ids_and_overlaps_across_sets_are_refused(self):
        cases = {
            "Trùng ID đoạn": [{"id": "S1", "source": "src", "start_seconds": 400.0, "end_seconds": 450.0,
                               "split": "dev"}],
            "chồng thời gian": [{"id": "N1", "source": "src", "start_seconds": 250.0, "end_seconds": 350.0,
                                 "split": "dev"}],
        }
        for message, segments in cases.items():
            with self.subTest(message=message):
                self.write("v1.1", extension(self.v1, segments), [], revision=0)
                with self.assertRaisesRegex(ValueError, message):
                    load_golden_sets(self.root, ["v1", "v1.1"])
                with self.assertRaisesRegex(ValueError, message):  # also when v1 itself is not scored
                    load_golden_sets(self.root, ["v1.1"])

    def test_overlap_inside_one_set_is_refused(self):
        segments = [{"id": "N1", "source": "src", "start_seconds": 400.0, "end_seconds": 500.0, "split": "dev"},
                    {"id": "N2", "source": "src", "start_seconds": 450.0, "end_seconds": 550.0, "split": "dev"}]
        self.write("v1.1", extension(self.v1, segments), [], revision=0)
        with self.assertRaisesRegex(ValueError, r"N2 \(v1.1\) chồng thời gian với đoạn N1 \(v1.1\)"):
            load_golden_sets(self.root, ["v1", "v1.1"])

    def test_differing_sources_are_refused(self):
        for field, value in (("sha256", "c" * 64), ("fps", 24.0), ("width", 1280), ("duration_seconds", 999.0)):
            with self.subTest(field=field):
                changed = extension(self.v1)
                changed["sources"]["src"][field] = value
                self.write("v1.1", changed, [], revision=0)
                with self.assertRaisesRegex(ValueError, f"Nguồn src khác nhau.*{field}"):
                    load_golden_sets(self.root, ["v1", "v1.1"])
        twin = extension(self.v1)
        twin["sources"]["copy"] = dict(self.v1["sources"]["src"])
        self.write("v1.1", twin, [], revision=0)
        with self.assertRaisesRegex(ValueError, "cùng một tệp dưới hai tên"):
            load_golden_sets(self.root, ["v1", "v1.1"])

    def test_colliding_event_ids_and_stray_labels_are_refused(self):
        self.write("v1.1", self.v11, [dict(event(start_seconds=120, end_seconds=130), id="gs-S1-0001",
                                           segment_id="N1")], revision=1)
        with self.assertRaisesRegex(ValueError, "Trùng ID nhãn gs-S1-0001"):
            load_golden_sets(self.root, ["v1", "v1.1"])
        self.write("v1.1", self.v11, [dict(event(), id="gs-S1-0009", segment_id="S1")], revision=1)
        with self.assertRaisesRegex(ValueError, "không thuộc manifest v1.1: S1"):
            load_golden_sets(self.root, ["v1", "v1.1"])


# --- "có thật · giữ": real safety content the user keeps (2026-10-01) ------------------------------

def safety_event(category="gore", **changes):
    payload = event(category=category, region_source_pixels=None, start_seconds=10, end_seconds=20,
                    expected_action="KEEP", severity=None, content_present=True)
    payload.update(changes)
    return payload


class ContentPresentValidationTest(unittest.TestCase):
    def test_a_kept_safety_label_can_say_the_content_is_really_there(self):
        for category in ("adult", "gore", "violence"):
            with self.subTest(category=category):
                value = validate_event(safety_event(category), manifest())
                self.assertEqual((value["expected_action"], value["content_present"], value["severity"]),
                                 ("KEEP", True, "should_catch"))  # default severity
        chosen = validate_event(safety_event("violence", severity="must_catch"), manifest())
        self.assertEqual(chosen["severity"], "must_catch")

    def test_content_present_must_be_a_boolean_and_only_on_safety_labels(self):
        for changes in ({"content_present": "yes"}, {"content_present": 1}, {"content_present": "true"},
                        {"category": "visual_logo"}, {"category": "text"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_event(safety_event(**changes), manifest())

    def test_earlier_labels_keep_their_exact_shape(self):
        keep = validate_event(event(expected_action="KEEP", severity=None), manifest())
        self.assertNotIn("content_present", keep)
        self.assertIsNone(keep["severity"])
        not_given = validate_event(safety_event(content_present=False), manifest())
        self.assertNotIn("content_present", not_given)
        self.assertIsNone(not_given["severity"])  # a plain KEEP stays a trap without severity
        self.assertNotIn("content_present", validate_event(safety_event(content_present=None), manifest()))
        implied = validate_event(safety_event(expected_action="CUT", severity="must_catch"), manifest())
        self.assertNotIn("content_present", implied)  # BLUR/CUT already mean the content is there
        self.assertNotIn("content_present", validate_event(event(content_present=False), manifest()))
        self.assertEqual(set(keep), {"id", "segment_id", "category", "start_seconds", "end_seconds",
                                     "region_source_pixels", "expected_action", "severity", "ambiguous", "notes",
                                     "from_suggestion"})

    def test_the_store_saves_it_as_the_answer_to_a_question(self):
        with tempfile.TemporaryDirectory() as temp, \
                LabelStore(Path(temp), manifest(), [dict(SUGGESTION, category="gore", group="gore")]) as store:
            saved = store.upsert_event(safety_event(from_suggestion="sug-1", notes="chế độ dễ"), 0)
            self.assertEqual((saved["content_present"], saved["severity"], saved["from_suggestion"]),
                             (True, "should_catch", "sug-1"))
            self.assertEqual(store.state()["suggestion_resolutions"]["sug-1"]["resolution"], "accepted")
            stored = read_json(Path(temp) / "events.json")["events"][0]
            self.assertTrue(stored["content_present"])


def question(id, segment, category, start, end, advisory=False):
    group = {"visual_logo": "advertising", "text": "advertising"}.get(category, category)
    return {"id": id, "segment_id": segment, "category": category, "group": group, "start_seconds": start,
            "end_seconds": end, "region_source_pixels": None, "advisory": advisory}


class ReopenSuggestionsTest(unittest.TestCase):
    QUESTIONS = [question("v-label", "S1", "violence", 10.0, 20.0), question("v-reject", "S1", "violence", 30.0, 40.0),
                 question("g-adv", "S1", "gore", 50.0, 55.0, advisory=True),
                 question("logo-q", "S1", "visual_logo", 60.0, 65.0), question("s2-q", "S2", "adult", 210.0, 220.0)]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.store = store = LabelStore(self.dir, manifest(), self.QUESTIONS)
        store.upsert_event(event(category="violence", region_source_pixels=None, start_seconds=10, end_seconds=20,
                                 expected_action="CUT", from_suggestion="v-label"), 0)
        self.watermark = store.upsert_event(event(start_seconds=0, end_seconds=100), 1)
        store.cover_suggestion("logo-q", self.watermark["id"], 2)
        store.resolve_suggestion("v-reject", "rejected", 3)
        store.reject_remaining("S1", False, 4)  # the advisory is bulk-rejected
        store.resolve_suggestion("s2-q", "rejected", 5)
        store.set_segment_status("S1", "complete", 6)
        store.set_segment_status("S2", "complete", 7)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def history(self):
        return [json.loads(line) for line in
                (self.dir / "label-history.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_answers_are_removed_and_the_questions_asked_again(self):
        before = len(self.history())
        result = self.store.reopen_suggestions(["v-label", "v-reject", "g-adv", "v-label"], 8)
        self.assertEqual(result, {"suggestions": ["v-label", "v-reject", "g-adv"], "deleted_events": ["gs-S1-0001"],
                                  "cleared": ["g-adv", "v-label", "v-reject"], "segments": ["S1"]})
        state = self.store.state()
        self.assertEqual(state["revision"], 9)
        self.assertEqual([e["id"] for e in state["events"]], [self.watermark["id"]])
        self.assertEqual(state["retired_event_ids"], ["gs-S1-0001"])
        self.assertEqual(set(state["suggestion_resolutions"]), {"logo-q", "s2-q"})
        self.assertEqual(state["segments"]["S1"], {"status": "in_progress", "completed_at": None})
        self.assertEqual(state["segments"]["S2"]["status"], "complete")  # untouched
        self.assertEqual(self.store.unresolved("S1"), ["v-label", "v-reject", "g-adv"])
        history = self.history()
        self.assertEqual(len(history), before + 1)  # one history entry
        entry = history[-1]
        self.assertEqual((entry["action"], entry["revision"]), ("reopen_suggestions", 9))
        self.assertEqual([e["id"] for e in entry["before"]["events"]], ["gs-S1-0001"])
        self.assertEqual(entry["before"]["segments"]["S1"]["status"], "complete")
        self.assertEqual(entry["before"]["resolutions"]["v-reject"]["resolution"], "rejected")
        # the easy page asks every reopened question again, the low-value advisory included
        self.assertEqual(easy_cards(self.QUESTIONS, state, [])["S1"], ["v-label", "v-reject", "g-adv", "logo-q"])
        self.assertNotIn("g-adv", easy_cards(self.QUESTIONS, dict(state, reopened_suggestions=[]), [])["S1"])
        answer = self.store.upsert_event(safety_event("violence", from_suggestion="v-label"), 9)
        self.assertEqual(answer["id"], "gs-S1-0003")  # the deleted id is never handed out again

    def test_refusals_write_nothing(self):
        for ids, error in ((["logo-q"], ValueError), (["s2-q", "logo-q"], ValueError), (["nope"], KeyError),
                           ([], ValueError)):
            with self.subTest(ids=ids), self.assertRaises(error):
                self.store.reopen_suggestions(ids, 8)
        self.assertEqual(self.store.state()["revision"], 8)
        self.store.reopen_suggestions(["v-reject"], 8)
        with self.assertRaisesRegex(ValueError, "đang chờ trả lời"):  # already open
            self.store.reopen_suggestions(["v-reject"], 9)
        with self.assertRaises(StaleRevision):
            self.store.reopen_suggestions(["s2-q"], 8)
        self.assertEqual(self.store.state()["revision"], 9)
        self.assertEqual(self.store.state()["segments"]["S2"]["status"], "complete")

    def test_questions_covered_by_a_deleted_label_reopen_too(self):
        with tempfile.TemporaryDirectory() as temp:
            logos = [question("logo-a", "S1", "visual_logo", 10.0, 30.0), question("logo-b", "S1", "text", 20.0, 25.0)]
            with LabelStore(Path(temp), manifest(), logos) as store:
                label = store.upsert_event(event(start_seconds=10, end_seconds=30, from_suggestion="logo-a"), 0)
                store.cover_suggestion("logo-b", label["id"], 1)
                result = store.reopen_suggestions(["logo-a"], 2)
                self.assertEqual(result["cleared"], ["logo-a", "logo-b"])  # as delete_event would
                self.assertEqual(store.unresolved("S1"), ["logo-a", "logo-b"])

    def test_a_covered_mark_can_be_cleared_like_a_rejection(self):
        self.store.set_segment_status("S1", "in_progress", 8)
        self.store.resolve_suggestion("logo-q", None, 9)  # easy-mode undo of "đã có khung xanh"
        self.assertNotIn("logo-q", self.store.state()["suggestion_resolutions"])
        with self.assertRaises(ValueError):  # a question that became a label still changes via the label
            self.store.resolve_suggestion("v-label", None, 10)


class AuditReopenScriptTest(unittest.TestCase):
    """scripts/golden_audit_corrections.py: a "reopen" plan dry-runs on a throw-away copy."""

    def setUp(self):
        import importlib
        import sys
        scripts = str(Path(__file__).resolve().parents[1] / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        self.script = importlib.import_module("golden_audit_corrections")
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "temp").mkdir()
        self.v1 = manifest()
        write_json_atomic(self.root / "annotations/golden/v1/segments.json", self.v1)
        self.v11 = extension(self.v1)
        self.labels = self.root / "annotations/golden/v1.1"
        write_json_atomic(self.labels / "segments.json", self.v11)
        self.questions = [question("t7-a", "N1", "violence", 110.0, 120.0), question("t7-b", "N1", "gore", 130.0, 140.0),
                          question("t7-c", "N1", "adult", 150.0, 160.0), question("t8-a", "N2", "violence", 510.0, 520.0)]
        write_json_atomic(self.root / "reports/benchmarks/golden-v1.1/prefill/suggestions.json",
                          {"manifest_sha256": canonical_sha256(self.v11), "suggestions": self.questions})
        with LabelStore(self.labels, self.v11, self.questions) as store:
            for revision, key in enumerate(("t7-a", "t7-b", "t7-c", "t8-a")):
                store.resolve_suggestion(key, "rejected", revision)  # "Sai" given only to keep the scenes
            store.set_segment_status("N1", "complete", 4)
            store.set_segment_status("N2", "complete", 5)
        self.patches = [patch.object(self.script, "ROOT", self.root),
                        patch.object(self.script, "GOLDEN_ROOT", self.root / "annotations/golden"),
                        patch.object(self.script, "BENCHMARKS", self.root / "reports/benchmarks")]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def run_script(self, plan, *argv):
        import contextlib
        import io
        path = self.root / "plan.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.script.main([str(path), *argv])
        return json.loads(output.getvalue())

    def snapshot(self):
        return {name: (self.labels / name).read_bytes() for name in ("events.json", "label-history.jsonl")}

    def plan(self, revision=6, **changes):
        value = {"golden_set": "v1.1", "labels_revision": revision,
                 "changes": [{"segment_id": "N1", "action": "reopen", "suggestion_ids": ["t7-a", "t7-b"]},
                             {"segment_id": "N2", "action": "reopen", "suggestion_ids": ["t8-a"]}]}
        value.update(changes)
        return value

    def test_reopen_plan_dry_run_changes_only_the_copy(self):
        before = self.snapshot()
        copy = self.root / "corrected"
        result = self.run_script(self.plan(), "--set", "v1.1", "--copy-to", str(copy))
        self.assertEqual(self.snapshot(), before)  # the real labels are untouched
        self.assertEqual((result["dry_run"], result["golden_set"], result["applied"], result["reopened_questions"]),
                         (True, "v1.1", {"reopen": 2}, 3))
        self.assertEqual(result["segments"], {"N1": "in_progress", "N2": "in_progress"})
        self.assertEqual(result["unanswered"], {"N1": 2, "N2": 1})
        corrected = read_json(copy / "events.json")
        self.assertEqual(set(corrected["suggestion_resolutions"]), {"t7-c"})  # the 18+ "Sai" stays
        self.assertEqual(corrected["reopened_suggestions"], ["t7-a", "t7-b", "t8-a"])
        lines = (copy / "label-history.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(line)["action"] for line in lines[-4:]],
                         ["set_segment_status", "reopen_suggestions", "set_segment_status", "reopen_suggestions"])
        self.assertEqual({json.loads(line)["actor"] for line in lines[-4:]}, {"claude-audit"})
        self.assertEqual(list((self.root / "temp").iterdir()), [])  # the throw-away copy is gone

    def test_stale_or_mismatched_plans_are_refused(self):
        before = self.snapshot()
        wrong_segment = [{"segment_id": "N2", "action": "reopen", "suggestion_ids": ["t7-a"]}]
        unknown = [{"segment_id": "N1", "action": "reopen", "suggestion_ids": ["nope"]}]
        for plan, argv in ((self.plan(revision=5), ["--set", "v1.1"]), (self.plan(), []),
                           (self.plan(changes=wrong_segment), ["--set", "v1.1"]),
                           (self.plan(changes=unknown), ["--set", "v1.1"])):
            with self.subTest(changes=plan["changes"][0], argv=argv), self.assertRaises(SystemExit):
                self.run_script(plan, *argv)
        self.assertEqual(self.snapshot(), before)

    def test_repeated_questions_are_refused_before_any_write(self):
        before = self.snapshot()
        twice = [{"segment_id": "N2", "action": "reopen", "suggestion_ids": ["t8-a"]},
                 {"segment_id": "N1", "action": "reopen", "suggestion_ids": ["t7-a"]},
                 {"segment_id": "N1", "action": "reopen", "suggestion_ids": ["t7-a"]}]
        with self.assertRaises(SystemExit):
            self.run_script(self.plan(changes=twice), "--set", "v1.1", "--apply")
        self.assertEqual(self.snapshot(), before)

    def test_reopening_an_unanswered_question_is_refused_before_any_write(self):
        copy = self.root / "first"
        self.run_script(self.plan(), "--set", "v1.1", "--copy-to", str(copy))
        again = self.plan(revision=read_json(copy / "events.json")["revision"])
        self.labels = copy  # run the same plan on the corrected copy: every question is open already
        with patch.object(self.script, "set_paths", lambda name: (copy, self.root / "reports/benchmarks/"
                                                                         "golden-v1.1/prefill/suggestions.json")):
            before = self.snapshot()
            with self.assertRaises(SystemExit):
                self.run_script(again, "--set", "v1.1")
            self.assertEqual(self.snapshot(), before)

    def test_answer_question_and_update_label(self):
        copy = self.root / "answered"
        plan = {"golden_set": "v1.1", "labels_revision": 6, "changes": [
            {"segment_id": "N1", "action": "answer_question", "suggestion_id": "t7-c",
             "answer": {"expected_action": "BLUR", "severity": "should_catch", "notes": "ngụ ý"}}]}
        result = self.run_script(plan, "--set", "v1.1", "--copy-to", str(copy))
        self.assertEqual(result["applied"], {"answer_question": 1})
        labels = read_json(copy / "events.json")
        label = next(e for e in labels["events"] if e["from_suggestion"] == "t7-c")
        self.assertEqual((label["category"], label["expected_action"], label["severity"], label["start_seconds"]),
                         ("adult", "BLUR", "should_catch", 150.0))
        self.assertEqual(labels["suggestion_resolutions"]["t7-c"]["resolution"], "accepted")
        self.assertEqual(labels["segments"]["N1"]["status"], "complete")
        twice = dict(plan, changes=plan["changes"] * 2)  # the second one no longer meets a "Sai" answer
        before = self.snapshot()
        with self.assertRaises(SystemExit):
            self.run_script(twice, "--set", "v1.1", "--copy-to", str(self.root / "twice"))
        self.assertEqual(self.snapshot(), before)

    def test_default_set_is_v1(self):
        labels, suggestions = self.script.set_paths("v1")
        self.assertEqual((labels, suggestions), (self.root / "annotations/golden/v1",
                                                 self.root / "reports/benchmarks/golden-v1/prefill/suggestions.json"))
        with self.assertRaises(SystemExit):
            self.script.set_paths("v2")
