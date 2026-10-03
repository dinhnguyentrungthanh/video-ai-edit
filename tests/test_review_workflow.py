import hashlib
import http.client
import importlib
import importlib.util
import json
import re
import shutil
import subprocess
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow.export_dialog import EXPORT_DIALOG_JS
from biliflow.export_guards import (
    CONTROL_CENTER_JOB_MESSAGE,
    CONTROL_CENTER_STATE_UNREADABLE,
    REVIEW_EDIT_IN_FLIGHT_MESSAGE,
    SOURCE_CLEANED_REVIEW_REFUSAL,
    SOURCE_MISSING_MESSAGE,
    STANDALONE_SKIPPED_EDIT_REFUSAL,
)
from biliflow.job_store import JobStore
from biliflow.review_workflow import (
    _guard_in_film_text,
    _guard_title_overlays,
    _in_film_text_references,
    _text_items,
    _interactive_html,
    apply_visual_ai_assessments,
    build_edit_plan,
    build_review_queue,
    bulk_accept_suggested_decisions,
    preserve_unresolved_review_items,
    revalidate_preserved_review_items,
    bulk_keep_review_items,
    clear_review_decision,
    ensure_unique_review_item_ids,
    record_review_decision,
    review_export_paths,
    review_resource_status,
    serve_review_ui,
    pixel_region_iou,
    promote_strong_adult_priorities,
    reconcile_persistent_overlay_items,
    refine_persistent_logo_regions,
    tighten_text_region,
    triage_adult_items,
    union_pixel_regions,
)


_SHELL_DELETE_PATCH = None


def setUpModule():
    """Contract R13: serve_review_ui imports control_center, so no test here may reach the real Recycle Bin."""
    global _SHELL_DELETE_PATCH
    if importlib.util.find_spec("biliflow.recycle_bin") is None:  # batch 3 step A5 not landed yet
        return
    recycle_bin = importlib.import_module("biliflow.recycle_bin")

    def refuse(*_args, **_kwargs):
        raise AssertionError("real Recycle Bin call in a test")

    _SHELL_DELETE_PATCH = patch.object(recycle_bin, "_shell_delete", refuse)
    _SHELL_DELETE_PATCH.start()


def tearDownModule():
    global _SHELL_DELETE_PATCH
    if _SHELL_DELETE_PATCH is not None:
        _SHELL_DELETE_PATCH.stop()
        _SHELL_DELETE_PATCH = None


def _box(x, y, width, height):
    return {"x": x, "y": y, "width": width, "height": height}


def _persistent_track(category, region, sources=None, end=6738, **extra):
    item = {
        "category": category, "candidate_type": "persistent_overlay",
        "review_kind": "logo_overlay", "start_seconds": 0, "end_seconds": end,
        "suggested_region_source_pixels": region,
        "labels": [], "reasons": [], "evidence": [f"{category}-track"],
        "preview_images": [], "source_candidate_refs": [f"{category}-track-ref"],
    }
    if sources is not None:
        item["model_evidence"] = {"region_sources": list(sources)}
    item.update(extra)
    return item


def _logo_read(start, region, sources=("grounding", "ocr"), **extra):
    item = {
        "category": "visual_logo", "candidate_type": None,
        "review_kind": "logo_overlay", "start_seconds": start,
        "end_seconds": start + 5, "suggested_region_source_pixels": region,
        "model_evidence": {"region_sources": list(sources)},
        "labels": [], "reasons": [], "evidence": [f"read-{start}-{region['x']}"],
        "preview_images": [], "source_candidate_refs": [f"read-ref-{start}-{region['x']}"],
        "decision": None,
    }
    item.update(extra)
    return item


class VisualAuditQueueTests(unittest.TestCase):
    def test_sustained_high_confidence_adult_scene_is_prioritized(self):
        items = promote_strong_adult_priorities([
            {
                "id": "adult-strong", "category": "adult", "priority": "context",
                "start_seconds": 928.5, "end_seconds": 950.5,
                "max_score": 0.999486, "labels": ["porn"], "decision": None,
            },
            {
                "id": "adult-short", "category": "adult", "priority": "context",
                "start_seconds": 475.0, "end_seconds": 478.0,
                "max_score": 0.9999, "labels": ["porn"], "decision": None,
            },
            {
                "id": "violence", "category": "violence", "priority": "context",
                "start_seconds": 928.5, "end_seconds": 950.5,
                "max_score": 1.0, "labels": ["Violent"], "decision": None,
            },
        ])
        self.assertEqual(items[0]["priority"], "high")
        self.assertEqual(items[1]["priority"], "context")
        self.assertEqual(items[2]["priority"], "context")

    def test_high_confidence_visual_conflict_requires_human_choice(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            queue_path = root / "reports" / "job" / "review-queue.json"
            queue_path.parent.mkdir(parents=True)
            queue_path.write_text(json.dumps({
                "status": "REVIEW_REQUIRED",
                "counts": {"total": 1, "pending": 1, "decisions": {}},
                "items": [{
                    "id": "logo-1", "category": "visual_logo",
                    "priority": "high", "start_seconds": 0, "end_seconds": 5,
                    "preview_images": [], "labels": [], "reasons": [],
                    "suggested_decision": "BLUR",
                    "suggested_region_source_pixels": {
                        "x": 10, "y": 10, "width": 20, "height": 20,
                    },
                    "decision": None,
                }],
                "audit_log": [],
            }), encoding="utf-8")
            updated = apply_visual_ai_assessments(
                project_root=root, queue_path=queue_path,
                audit_payload={
                    "created_at": "now", "model": "gpt-5.6-luna",
                    "reasoning_effort": "medium", "visual_audit": {"image_count": 1},
                    "visual_assessments": [{
                        "item_id": "logo-1", "classification": "false_positive",
                        "suggested_decision": "KEEP", "confidence": 0.98,
                        "region_assessment": "TOO_WIDE", "reasoning": "film content",
                    }],
                },
            )
            item = updated["items"][0]
            self.assertNotIn("suggested_decision", item)
            self.assertEqual(item["suggestion_source"], "visual_ai_conflict")
            self.assertEqual(item["suggestion_conflict"]["local_suggestion"], "BLUR")
            self.assertIsNone(item["decision"])

    def test_region_identity_prevents_duplicate_review_ids(self):
        items = [
            {
                "category": "visual_logo", "start_seconds": 10, "end_seconds": 15,
                "evidence": ["scan.json"], "review_kind": "logo_overlay",
                "suggested_region_source_pixels": {"x": 10, "y": 20, "width": 30, "height": 40},
            },
            {
                "category": "visual_logo", "start_seconds": 10, "end_seconds": 15,
                "evidence": ["scan.json"], "review_kind": "logo_overlay",
                "suggested_region_source_pixels": {"x": 100, "y": 20, "width": 30, "height": 40},
            },
        ]
        ensure_unique_review_item_ids(items)
        self.assertEqual(len({item["id"] for item in items}), 2)

    def test_visual_audit_only_adds_suggestions_and_never_decides(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            queue_path = root / "reports" / "job" / "review-queue.json"
            queue_path.parent.mkdir(parents=True)
            queue = {
                "status": "REVIEW_REQUIRED",
                "counts": {"total": 1, "pending": 1, "decisions": {}},
                "items": [{
                    "id": "logo-1", "category": "visual_logo",
                    "priority": "high", "start_seconds": 0.0, "end_seconds": 5.0,
                    "preview_images": [], "labels": ["logo"], "reasons": [],
                    "decision": None,
                    "suggested_region_source_pixels": {
                        "x": 10, "y": 10, "width": 50, "height": 20,
                    },
                }],
                "audit_log": [],
            }
            queue_path.write_text(json.dumps(queue), encoding="utf-8")
            updated = apply_visual_ai_assessments(
                project_root=root, queue_path=queue_path,
                audit_payload={
                    "created_at": "now", "model": "gpt-5.6-luna",
                    "reasoning_effort": "medium",
                    "visual_audit": {"image_count": 1},
                    "visual_assessments": [{
                        "item_id": "logo-1",
                        "classification": "external_brand",
                        "suggested_decision": "BLUR",
                        "confidence": 0.99,
                        "region_assessment": "TIGHT",
                        "reasoning": "External logo.",
                    }],
                },
            )
            item = updated["items"][0]
            self.assertIsNone(item["decision"])
            self.assertEqual(item["suggested_decision"], "BLUR")
            self.assertEqual(item["suggestion_source"], "visual_ai")
            self.assertEqual(item["ai_visual_audit"]["authority"], "ADVISORY_ONLY")
            self.assertEqual(updated["status"], "REVIEW_REQUIRED")
            self.assertEqual(updated["counts"]["pending"], 1)



class ReviewWorkflowTests(unittest.TestCase):
    def test_text_items_include_frame_size_and_clip_region_to_source(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "reports" / "text" / "scan.json"
            payload = {
                "source_size": [1920, 1080], "analysis_size": [960, 540],
                "tracks": [{
                    "track_id": 1, "start_seconds": 10, "end_seconds": 12,
                    "union_box": [900, 500, 990, 570],
                    "sample_text": ["scene text"], "review_candidate": True,
                }],
            }
            item = _text_items(root, report, payload)[0]
        self.assertEqual(item["source_frame_size"], [1920, 1080])
        self.assertEqual(
            item["suggested_region_source_pixels"],
            {"x": 1800, "y": 1000, "width": 120, "height": 80},
        )

    def test_graphical_logo_keeps_full_grounding_box_when_ocr_is_only_text_fragment(self):
        items = [
            {
                "category": "visual_logo", "candidate_type": "persistent_overlay",
                "review_kind": "logo_overlay", "start_seconds": 5, "end_seconds": 100,
                "suggested_region_source_pixels": {"x": 90, "y": 10, "width": 250, "height": 210},
                "model_evidence": {"region_sources": ["grounding"]},
            },
            {
                "category": "visual_logo", "candidate_type": None,
                "review_kind": "logo_overlay", "start_seconds": 5, "end_seconds": 10,
                "suggested_region_source_pixels": {"x": 128, "y": 104, "width": 160, "height": 67},
                "model_evidence": {"region_sources": ["ocr"]},
            },
            {
                "category": "visual_logo", "candidate_type": None,
                "review_kind": "logo_overlay", "start_seconds": 80, "end_seconds": 85,
                "suggested_region_source_pixels": {"x": 130, "y": 104, "width": 159, "height": 67},
                "model_evidence": {"region_sources": ["ocr"]},
            },
        ]
        refined = refine_persistent_logo_regions(items)
        self.assertEqual(
            refined[0]["suggested_region_source_pixels"],
            {"x": 100, "y": 20, "width": 230, "height": 190},
        )
        self.assertEqual(
            refined[0]["region_refinement"]["method"],
            "grounding_box_padding_trim",
        )
        self.assertEqual(refined[0]["region_refinement"]["support_count"], 2)

    def test_text_logo_prefers_repeated_ocr_when_it_covers_most_of_grounding(self):
        items = [
            {
                "category": "visual_logo", "candidate_type": "persistent_overlay",
                "review_kind": "logo_overlay", "start_seconds": 5, "end_seconds": 100,
                "suggested_region_source_pixels": {"x": 100, "y": 90, "width": 200, "height": 90},
                "model_evidence": {"region_sources": ["grounding"]},
            },
            {
                "category": "visual_logo", "candidate_type": None,
                "review_kind": "logo_overlay", "start_seconds": 5, "end_seconds": 10,
                "suggested_region_source_pixels": {"x": 120, "y": 100, "width": 160, "height": 67},
                "model_evidence": {"region_sources": ["ocr"]},
            },
            {
                "category": "visual_logo", "candidate_type": None,
                "review_kind": "logo_overlay", "start_seconds": 80, "end_seconds": 85,
                "suggested_region_source_pixels": {"x": 119, "y": 100, "width": 162, "height": 67},
                "model_evidence": {"region_sources": ["ocr"]},
            },
        ]
        refined = refine_persistent_logo_regions(items)
        self.assertEqual(
            refined[0]["suggested_region_source_pixels"],
            {"x": 119, "y": 100, "width": 162, "height": 67},
        )
        self.assertEqual(
            refined[0]["region_refinement"]["method"],
            "repeated_tight_ocr_support",
        )

    def test_persistent_text_logo_uses_dominant_visual_ocr_region(self):
        items = [{
            "category": "text", "candidate_type": "persistent_overlay",
            "review_kind": "logo_overlay", "start_seconds": 0, "end_seconds": 100,
            "suggested_region_source_pixels": {
                "x": 110, "y": 146, "width": 626, "height": 84,
            },
        }]
        for start, region in (
            (5, {"x": 105, "y": 160, "width": 174, "height": 52}),
            (40, {"x": 107, "y": 160, "width": 172, "height": 53}),
            (80, {"x": 106, "y": 161, "width": 173, "height": 52}),
            # A nearby OCR read is inside the broad source region but describes
            # a different target and must not expand the persistent blur.
            (60, {"x": 410, "y": 160, "width": 180, "height": 52}),
        ):
            items.append({
                "category": "visual_logo", "candidate_type": None,
                "review_kind": "logo_overlay", "start_seconds": start,
                "end_seconds": start + 5,
                "suggested_region_source_pixels": region,
                "model_evidence": {"region_sources": ["ocr"]},
            })
        refined = refine_persistent_logo_regions(items)
        self.assertEqual(
            refined[0]["suggested_region_source_pixels"],
            {"x": 105, "y": 160, "width": 174, "height": 53},
        )
        self.assertEqual(
            refined[0]["region_refinement"]["method"],
            "repeated_visual_ocr_consensus",
        )
        self.assertEqual(refined[0]["region_refinement"]["support_count"], 3)

    def test_persistent_text_uses_full_mark_reads_when_ocr_consensus_is_a_word_fragment(self):
        # Modelled on the Conan 21 "PhimOnline.net" watermark: two repeated
        # reads of only "Online.net" must not shrink the box and cut off
        # "Phim" while repeated reads of the whole mark contain them. The box
        # becomes the extent of those whole-mark reads.
        original = {"x": 1544, "y": 38, "width": 376, "height": 67}
        items = [{
            "category": "text", "candidate_type": "persistent_overlay",
            "review_kind": "logo_overlay", "start_seconds": 0, "end_seconds": 6738,
            "suggested_region_source_pixels": dict(original),
        }]
        for start, region in (
            (870, {"x": 1653, "y": 46, "width": 208, "height": 66}),
            (4000, {"x": 1655, "y": 46, "width": 204, "height": 64}),
            (410, {"x": 1553, "y": 45, "width": 308, "height": 67}),
            (455, {"x": 1553, "y": 44, "width": 308, "height": 69}),
            (595, {"x": 1555, "y": 44, "width": 306, "height": 69}),
        ):
            items.append({
                "category": "visual_logo", "candidate_type": None,
                "review_kind": "logo_overlay", "start_seconds": start,
                "end_seconds": start + 5,
                "suggested_region_source_pixels": region,
                "model_evidence": {"region_sources": ["grounding", "ocr"]},
            })
        refined = refine_persistent_logo_regions(items)
        self.assertEqual(
            refined[0]["suggested_region_source_pixels"], _box(1553, 44, 308, 69),
        )
        refinement = refined[0]["region_refinement"]
        self.assertEqual(refinement["method"], "repeated_full_mark_ocr_support")
        self.assertEqual(refinement["support_count"], 3)
        self.assertEqual(refinement["original_region"], original)
        self.assertEqual(refinement["ocr_fragment_region"], _box(1653, 46, 208, 66))

    def test_word_fragment_guard_counts_full_reads_larger_than_the_track_box(self):
        # Troy geometry: the OCR track box (164x49) is slightly smaller than
        # every whole-mark read (172x52). Two first-word reads must not cut
        # the watermark down to 90 px because the fuller reads are larger.
        items = [_persistent_track("text", _box(110, 160, 164, 49))]
        items += [_logo_read(start, _box(107, 161, 90, 51)) for start in (1000, 5000)]
        items += [
            _logo_read(start, _box(107, 160, 172, 52))
            for start in (135, 525, 680, 2845, 3300, 5210)
        ]
        refined = refine_persistent_logo_regions(items)
        self.assertEqual(
            refined[0]["suggested_region_source_pixels"], _box(107, 160, 172, 52),
        )
        self.assertEqual(
            refined[0]["region_refinement"]["method"], "repeated_full_mark_ocr_support",
        )

    def test_full_mark_box_still_contains_the_fragment_it_replaces(self):
        # Fuller reads may each cover only part of the line ("PhimOnline"),
        # leaving ".net" of the fragment outside their extent; the refined
        # box must keep the fragment so no glyph that was blurred is exposed.
        items = [_persistent_track("text", _box(1560, 45, 296, 60))]
        items += [
            _logo_read(870, _box(1665, 48, 185, 55)),
            _logo_read(4000, _box(1667, 48, 183, 55)),
            _logo_read(410, _box(1565, 48, 235, 56)),
            _logo_read(455, _box(1566, 48, 234, 56)),
        ]
        refined = refine_persistent_logo_regions(items)
        self.assertEqual(
            refined[0]["suggested_region_source_pixels"], _box(1565, 48, 285, 56),
        )

    def test_padded_full_reads_do_not_block_grounding_tightening(self):
        # Two reads that only add OCR padding around the same mark (similar
        # width) are not evidence of a fragment; the oversized grounding box
        # is still tightened to the repeated tight reads.
        items = [_persistent_track(
            "visual_logo", _box(1530, 24, 350, 100), ["grounding"], end=100,
        )]
        items += [
            _logo_read(5, _box(1553, 44, 308, 69), ("ocr",)),
            _logo_read(60, _box(1555, 44, 306, 68), ("ocr",)),
            _logo_read(30, _box(1545, 36, 325, 84), ("ocr",)),
            _logo_read(80, _box(1546, 37, 324, 83), ("ocr",)),
        ]
        refined = refine_persistent_logo_regions(items)
        self.assertEqual(
            refined[0]["suggested_region_source_pixels"], _box(1553, 44, 308, 69),
        )
        self.assertEqual(
            refined[0]["region_refinement"]["method"], "repeated_tight_ocr_support",
        )

    def test_whole_emblem_reads_do_not_block_emblem_padding_trim(self):
        # The OCR word inside an emblem is not a fragment of a text line when
        # the fuller reads are the (much taller) emblem: keep the 5% trim.
        items = [_persistent_track(
            "visual_logo", _box(90, 10, 250, 210), ["grounding"], end=100,
        )]
        items += [
            _logo_read(5, _box(128, 104, 160, 67), ("ocr",)),
            _logo_read(80, _box(130, 104, 159, 67), ("ocr",)),
            _logo_read(20, _box(95, 15, 240, 200)),
            _logo_read(50, _box(96, 15, 239, 200)),
        ]
        refined = refine_persistent_logo_regions(items)
        self.assertEqual(
            refined[0]["suggested_region_source_pixels"], _box(100, 20, 230, 190),
        )
        self.assertEqual(
            refined[0]["region_refinement"]["method"], "grounding_box_padding_trim",
        )

    def test_region_refinement_does_not_depend_on_item_order(self):
        # A persistent visual card that is itself refined must not become a
        # fragment-sized read for the OCR track only when it comes first.
        def scenario():
            text = _persistent_track("text", _box(1544, 38, 376, 67), id="T")
            visual = _persistent_track(
                "visual_logo", _box(1553, 44, 316, 69), ["grounding", "ocr"], id="P",
            )
            reads = [
                _logo_read(870, _box(1653, 46, 208, 66)),
                _logo_read(4000, _box(1655, 46, 204, 64)),
                _logo_read(1200, _box(1553, 45, 306, 67)),
            ]
            return text, visual, reads

        results = []
        for text_first in (True, False):
            text, visual, reads = scenario()
            order = [text, visual] if text_first else [visual, text]
            refine_persistent_logo_regions(order + reads)
            results.append((
                text["suggested_region_source_pixels"],
                visual["suggested_region_source_pixels"],
            ))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0][0], _box(1553, 44, 316, 69))

    def test_region_refinement_tolerates_missing_model_evidence(self):
        items = [_persistent_track("text", _box(1544, 38, 376, 67))]
        items += [
            _logo_read(870, _box(1653, 46, 208, 66)),
            _logo_read(4000, _box(1655, 46, 204, 64)),
            _logo_read(1300, _box(1600, 50, 100, 40), model_evidence=None),
        ]
        refined = refine_persistent_logo_regions(items)
        self.assertEqual(
            refined[0]["suggested_region_source_pixels"], _box(1653, 46, 208, 66),
        )

    def test_approved_brand_memory_box_is_not_refined_by_ocr_reads(self):
        brand = _persistent_track(
            "visual_logo", _box(1565, 52, 282, 46), ["brand_memory"],
            region_classification="external_brand",
        )
        items = [brand] + [
            _logo_read(start, _box(1655, 55, 180, 40), ("ocr",))
            for start in (100, 900)
        ]
        refine_persistent_logo_regions(items)
        self.assertEqual(brand["suggested_region_source_pixels"], _box(1565, 52, 282, 46))
        self.assertNotIn("region_refinement", brand)

    def test_approved_brand_box_keeps_ownership_of_matching_ocr_fragment_track(self):
        # A persistent OCR track read only as "Online.net" overlaps the
        # approved "PhimOnline.net" box by 0.69: they are one mark, and the
        # user-approved box must stay the blur region, in either input order.
        for brand_first in (True, False):
            brand = _persistent_track(
                "visual_logo", _box(1565, 52, 282, 46), ["brand_memory"],
                region_classification="external_brand", suggested_decision="BLUR",
            )
            text = _persistent_track("text", _box(1653, 46, 208, 66))
            items = [brand, text] if brand_first else [text, brand]
            final = reconcile_persistent_overlay_items(items, source_duration=6738)
            self.assertEqual(len(final), 1)
            self.assertIs(final[0], brand)
            self.assertEqual(
                final[0]["suggested_region_source_pixels"], _box(1565, 52, 282, 46),
            )
            self.assertEqual(
                final[0]["linked_detector_categories"], ["text", "visual_logo"],
            )
            self.assertEqual(
                [record["category"] for record in final[0]["supporting_detections"]],
                ["text"],
            )

    def test_ocr_track_never_absorbs_an_approved_brand_card(self):
        # An OCR fragment track overlapping an approved brand box by 0.69 used
        # to absorb it (external_brand needs only 0.60) and the approved box
        # was lost. A brand-memory card is only ever joined by a brand owner.
        text = _persistent_track("text", _box(1653, 46, 208, 66))
        brand_read = _logo_read(
            400, _box(1565, 52, 282, 46), ("brand_memory",),
            region_classification="external_brand", suggested_decision="BLUR",
        )
        final = reconcile_persistent_overlay_items(
            [text, brand_read], source_duration=6738,
        )
        self.assertEqual(final, [text, brand_read])
        self.assertNotIn("supporting_detections", text)

        # The brand owner joins one OCR track (the first matching one, here the
        # full read). A second, fragment track must not then swallow the brand
        # owner together with everything it absorbed.
        brand = _persistent_track(
            "visual_logo", _box(1565, 52, 282, 46), ["brand_memory"],
            region_classification="external_brand", suggested_decision="BLUR",
        )
        full_track = _persistent_track("text", _box(1547, 43, 314, 70))
        fragment_track = _persistent_track("text", _box(1653, 46, 208, 66))
        brand_read = _logo_read(
            400, _box(1565, 52, 282, 46), ("brand_memory",),
            region_classification="external_brand", suggested_decision="BLUR",
        )
        final = reconcile_persistent_overlay_items(
            [full_track, fragment_track, brand, brand_read], source_duration=6738,
        )
        self.assertEqual(final, [fragment_track, brand])
        self.assertEqual(brand["suggested_region_source_pixels"], _box(1565, 52, 282, 46))
        self.assertEqual(
            [record["region_source_pixels"] for record in brand["supporting_detections"]],
            [_box(1547, 43, 314, 70), _box(1565, 52, 282, 46)],
        )
        self.assertNotIn("supporting_detections", fragment_track)

    def test_blur_card_inside_joined_ocr_track_folds_into_brand_owner(self):
        # Conan 21, 3745 s: a padded "Online.net" card overlaps the approved
        # box by only 0.72, but lies inside the joined OCR track of the same
        # watermark and proposes the same BLUR, so it is not a second card.
        brand = _persistent_track(
            "visual_logo", _box(1565, 52, 282, 46), ["brand_memory"],
            region_classification="external_brand", suggested_decision="BLUR",
        )
        text = _persistent_track("text", _box(1547, 43, 314, 70))
        same_mark = _logo_read(
            3745, _box(1644, 34, 223, 83), ("grounding_dino",),
            suggested_decision="BLUR", region_classification="unknown",
        )
        # A card inside the track without a proposed action keeps its advisory
        # path, and a BLUR card mostly outside the track box is unrelated.
        unexplained = _logo_read(
            2020, _box(1660, 45, 203, 69), ("ocr",), suggested_decision=None,
            region_classification="unknown",
        )
        unrelated = _logo_read(
            1170, _box(1760, 40, 150, 70), ("ocr",), suggested_decision="BLUR",
            region_classification="unknown",
        )
        final = reconcile_persistent_overlay_items(
            [brand, text, same_mark, unexplained, unrelated], source_duration=6738,
        )
        self.assertEqual([item is brand for item in final], [True, False, False])
        self.assertIn(unexplained, final)
        self.assertIn(unrelated, final)
        supports = [record["start_seconds"] for record in brand["supporting_detections"]]
        self.assertEqual(supports, [0, 3745])
        self.assertEqual(brand["suggested_region_source_pixels"], _box(1565, 52, 282, 46))

    def test_blur_card_inside_joined_track_but_off_the_owner_box_stays_a_decision(self):
        # Only the owner's box is blurred after one approval. A BLUR card that
        # lies inside a long joined OCR track but does not overlap that box
        # would be hidden as a support count and never blurred.
        brand = _persistent_track(
            "visual_logo", _box(1565, 52, 282, 46), ["brand_memory"],
            region_classification="external_brand", suggested_decision="BLUR",
        )
        line_track = _persistent_track(
            "text", _box(1180, 40, 700, 75), suggested_decision="BLUR",
        )
        other_mark = _logo_read(
            2000, _box(1200, 48, 330, 58), suggested_decision="BLUR",
            region_classification="unknown",
        )
        final = reconcile_persistent_overlay_items(
            [brand, line_track, other_mark], source_duration=6738,
        )
        self.assertEqual(final, [brand, other_mark])
        self.assertEqual(
            [record["category"] for record in brand["supporting_detections"]],
            ["text"],
        )

        # A moving watermark: the OCR track spans the travel path, the
        # persistent visual box sits at one position, and reads of the mark at
        # other positions must stay separate decisions.
        visual = _persistent_track(
            "visual_logo", _box(1600, 45, 280, 55), ["grounding", "ocr"],
            region_classification="external_brand_candidate", suggested_decision="BLUR",
        )
        path_track = _persistent_track(
            "text", _box(900, 40, 990, 65), suggested_decision="BLUR",
        )
        moved = [
            _logo_read(
                start, _box(x, 45, 280, 55), suggested_decision="BLUR",
                region_classification="unknown",
            )
            for start, x in ((300, 920), (1800, 1150), (4200, 1320))
        ]
        final = reconcile_persistent_overlay_items(
            [visual, path_track] + moved, source_duration=6738,
        )
        self.assertEqual(final, [visual] + moved)
        self.assertEqual(visual["supporting_candidate_count"], 1)

    def test_owner_covering_an_approved_brand_box_absorbs_its_intervals(self):
        # Without a persistent brand owner, a text track whose box covers the
        # approved brand box still collects the intermittent brand intervals
        # as one decision; its blur covers every approved box.
        text = _persistent_track(
            "text", _box(1547, 43, 314, 70), suggested_decision="BLUR",
        )
        brand_reads = [
            _logo_read(
                start, _box(1565, 52, 282, 46), ("brand_memory",),
                region_classification="external_brand", suggested_decision="BLUR",
            )
            for start in (100, 900, 2500, 4000, 6000)
        ]
        final = reconcile_persistent_overlay_items(
            [text] + brand_reads, source_duration=6738,
        )
        self.assertEqual(final, [text])
        self.assertEqual(text["supporting_candidate_count"], 5)
        self.assertEqual(text["suggested_region_source_pixels"], _box(1547, 43, 314, 70))

    def test_oversized_grounding_box_is_still_tightened_beside_single_wider_read(self):
        items = [{
            "category": "visual_logo", "candidate_type": "persistent_overlay",
            "review_kind": "logo_overlay", "start_seconds": 0, "end_seconds": 100,
            "suggested_region_source_pixels": {
                "x": 1530, "y": 24, "width": 350, "height": 100,
            },
            "model_evidence": {"region_sources": ["grounding"]},
        }]
        for start, region in (
            (5, {"x": 1553, "y": 44, "width": 308, "height": 69}),
            (60, {"x": 1555, "y": 44, "width": 306, "height": 68}),
            # One wider OCR read (mark plus neighbouring artwork) is not a
            # repeated observation and must not block the correction.
            (30, {"x": 1540, "y": 30, "width": 330, "height": 90}),
        ):
            items.append({
                "category": "visual_logo", "candidate_type": None,
                "review_kind": "logo_overlay", "start_seconds": start,
                "end_seconds": start + 5,
                "suggested_region_source_pixels": region,
                "model_evidence": {"region_sources": ["ocr"]},
            })
        refined = refine_persistent_logo_regions(items)
        self.assertEqual(
            refined[0]["suggested_region_source_pixels"],
            {"x": 1553, "y": 44, "width": 308, "height": 69},
        )
        self.assertEqual(
            refined[0]["region_refinement"]["method"],
            "repeated_tight_ocr_support",
        )
        self.assertEqual(refined[0]["region_refinement"]["support_count"], 2)

    def test_detector_upgrade_preserves_unresolved_previous_candidate(self):
        previous = [{
            "id": "pending-old", "category": "visual_logo", "decision": None,
            "reasons": ["old detector candidate"],
        }, {
            "id": "resolved-old", "category": "visual_logo", "decision": "KEEP",
            "reasons": ["already resolved"],
        }]
        current = [{
            "id": "current", "category": "visual_logo", "decision": None,
            "reasons": [],
        }]
        result = preserve_unresolved_review_items(previous, current)
        by_id = {item["id"]: item for item in result}
        self.assertIn("pending-old", by_id)
        self.assertNotIn("resolved-old", by_id)
        self.assertEqual(
            by_id["pending-old"]["migration_status"],
            "preserved_unresolved_from_previous_queue",
        )

    def test_preserved_uncorroborated_region_stops_blocking_after_upgrade(self):
        # The Conan Movie 20 zombie: a single-source grounding box on a
        # character's head kept a whole-film blur demand alive across rebuilds
        # because its persistent_overlay type exempted it from every gate.
        stale = {
            "id": "review-3c16902164fd", "category": "visual_logo",
            "start_seconds": 510.0, "end_seconds": 6689.578,
            "candidate_type": "persistent_overlay", "review_kind": "logo_overlay",
            "region_classification": "unknown", "suggested_decision": "BLUR",
            "priority": "high", "decision": None, "reasons": [],
            "suggested_region_source_pixels": {
                "x": 1203, "y": 193, "width": 261, "height": 239,
            },
            "model_evidence": {"region_sources": ["grounding"]},
            "migration_status": "preserved_unresolved_from_previous_queue",
        }
        required, advisory = revalidate_preserved_review_items([stale])
        self.assertEqual(required, [])
        self.assertEqual(len(advisory), 1)
        self.assertEqual(advisory[0]["priority"], "context")
        self.assertEqual(advisory[0]["suggested_decision"], "KEEP")
        self.assertTrue(advisory[0]["advisory"])
        self.assertEqual(
            advisory[0]["region_revalidation"]["previous_suggested_decision"], "BLUR",
        )

    def test_revalidation_keeps_corroborated_and_fresh_items_required(self):
        approved = {
            "id": "brand", "category": "visual_logo", "decision": None,
            "start_seconds": 0.0, "end_seconds": 100.0,
            "candidate_type": "persistent_overlay",
            "region_classification": "external_brand",
            "suggested_decision": "BLUR", "priority": "high", "reasons": [],
            "suggested_region_source_pixels": {
                "x": 1565, "y": 52, "width": 282, "height": 46,
            },
            "model_evidence": {"region_sources": ["brand_memory"]},
            "migration_status": "preserved_unresolved_from_previous_queue",
        }
        fresh = {
            "id": "fresh", "category": "visual_logo", "decision": None,
            "start_seconds": 10.0, "end_seconds": 15.0, "candidate_type": None,
            "region_classification": "unknown", "suggested_decision": None,
            "priority": "context", "reasons": [],
            "suggested_region_source_pixels": {
                "x": 100, "y": 100, "width": 50, "height": 50,
            },
            "model_evidence": {"region_sources": ["grounding"]},
        }
        required, advisory = revalidate_preserved_review_items([approved, fresh])
        self.assertEqual([item["id"] for item in required], ["brand", "fresh"])
        self.assertEqual(advisory, [])

    def test_detector_upgrade_does_not_duplicate_equivalent_pending_candidate(self):
        target = {
            "category": "visual_logo", "decision": None,
            "start_seconds": 0, "end_seconds": 100,
            "candidate_type": "persistent_overlay",
            "suggested_region_source_pixels": {
                "x": 100, "y": 20, "width": 200, "height": 50,
            },
            "reasons": [],
        }
        previous = [{**target, "id": "old"}]
        current = [{
            **target, "id": "new",
            "suggested_region_source_pixels": {
                "x": 102, "y": 21, "width": 198, "height": 49,
            },
        }]
        result = preserve_unresolved_review_items(previous, current)
        self.assertEqual([item["id"] for item in result], ["new"])

    def test_approved_brand_memory_region_cannot_be_relabelled_as_title(self):
        item = {
            "category": "visual_logo", "start_seconds": 10, "end_seconds": 15,
            "suggested_region_source_pixels": {"x": 800, "y": 20, "width": 150, "height": 50},
            "review_kind": "logo_overlay", "candidate_type": None,
            "suggested_decision": "BLUR", "priority": "high",
            "labels": ["Known watermark"], "reasons": [],
            "model_evidence": {
                "vlm_source": "approved_brand_memory", "region_sources": ["brand_memory"],
            },
        }
        reference = {
            "start_seconds": 10, "end_seconds": 15,
            "region": {"x": 800, "y": 20, "width": 150, "height": 50},
            "labels": ["MISREAD TITLE"],
        }
        guarded = _guard_title_overlays([item], [reference])[0]
        self.assertEqual(guarded["review_kind"], "logo_overlay")
        self.assertEqual(guarded["suggested_decision"], "BLUR")

    def test_low_ad_in_scene_text_overrides_unrelated_frame_branding(self):
        payload = {
            "source_size": [1920, 1080], "analysis_size": [960, 540],
            "duration_seconds": 6738.0,
            "tracks": [{
                "start_seconds": 4527, "end_seconds": 4533,
                "recommended_blur_end_seconds": 4533.5,
                "routing": "REVIEW_UNCERTAIN", "persistent": False,
                "zone": "subtitle", "ad_probability": 0.10,
                "union_box": [424, 471, 517, 553],
                "sample_text": ["SCRAP"],
                "visual_features": {"overlay_signal": False},
            }],
        }
        references = _in_film_text_references(payload)
        self.assertEqual(len(references), 1)
        item = {
            "category": "visual_logo", "start_seconds": 4530,
            "end_seconds": 4535, "candidate_type": None,
            "suggested_region_source_pixels": {
                "x": 800, "y": 926, "width": 240, "height": 154,
            },
            "review_kind": "logo_overlay", "suggested_decision": "BLUR",
            "priority": "high", "labels": [], "reasons": [],
            "model_evidence": {"region_sources": ["grounding_dino"]},
        }
        guarded = _guard_in_film_text([item], references)[0]
        self.assertEqual(guarded["review_kind"], "in_film_text")
        self.assertEqual(guarded["region_classification"], "scene_text")
        self.assertEqual(guarded["suggested_decision"], "KEEP")

    def test_in_film_guard_never_overrides_approved_brand_memory(self):
        item = {
            "category": "visual_logo", "start_seconds": 100, "end_seconds": 105,
            "candidate_type": None,
            "suggested_region_source_pixels": {
                "x": 800, "y": 50, "width": 200, "height": 70,
            },
            "review_kind": "logo_overlay", "suggested_decision": "BLUR",
            "priority": "high", "labels": [], "reasons": [],
            "model_evidence": {
                "vlm_source": "approved_brand_memory",
                "region_sources": ["brand_memory"],
            },
        }
        reference = {
            "start_seconds": 100, "end_seconds": 105,
            "region": {"x": 800, "y": 50, "width": 200, "height": 70},
            "classification": "scene_text", "labels": ["MISREAD"],
        }
        guarded = _guard_in_film_text([item], [reference])[0]
        self.assertEqual(guarded["review_kind"], "logo_overlay")
        self.assertEqual(guarded["suggested_decision"], "BLUR")

    def test_review_ui_explains_pending_scene_and_has_safe_dashboard_back(self):
        page = _interactive_html("token")
        self.assertIn("← Quay lại Dashboard", page)
        self.assertIn("location.href='/'", page)
        self.assertIn("function pendingDescription()", page)
        self.assertIn("Xử lý riêng vùng logo khoanh đỏ", page)
        self.assertIn("Đây là logo thương hiệu — làm mờ", page)
        self.assertIn("Đây là tiêu đề/nội dung phim — giữ lại", page)
        self.assertIn("Chỉ nội dung nằm trong khung đỏ này đang được phân loại", page)
        self.assertIn("Track <strong>${label}</strong> ở vùng khác", page)
        self.assertIn("confidence>=.9", page)
        self.assertIn("Quyết định cho toàn cảnh", page)
        self.assertIn("logo thương hiệu đã xác nhận", page)
        self.assertIn("Quyết định toàn cảnh bên dưới chỉ áp dụng", page)
        self.assertIn('id="candidate-filter"', page)
        self.assertIn("Ứng viên phụ (${advisory})", page)
        self.assertIn("mục chính bắt buộc duyệt", page)
        self.assertIn("function decisionScope(x)", page)
        self.assertIn("Phạm vi áp dụng:", page)
        self.assertIn("QUYẾT ĐỊNH TOÀN VIDEO", page)
        self.assertIn("QUYẾT ĐỊNH TOÀN KHOẢNG XUẤT HIỆN", page)
        self.assertIn("CHỈ ĐOẠN HIỆN TẠI", page)
        self.assertIn("'Nhóm sự kiện').toUpperCase()", page)
        self.assertIn("KHOẢNH KHẮC`", page)
        self.assertIn("các khoảng trống giữa chúng không bị cắt hoặc làm mờ", page)
        self.assertIn("Ứng viên kiểm tra thêm — chưa thuộc quyết định chính", page)
        self.assertIn("Logo ở vị trí hoặc track khác vẫn cần quyết định riêng", page)
        self.assertIn('id="output-size-mode"', page)
        self.assertIn("Tối đa 3,5 GB (mặc định)", page)
        self.assertIn("Giới hạn tùy chỉnh", page)
        self.assertIn("Không giới hạn dung lượng", page)
        self.assertIn("function outputSizeSelection()", page)
        self.assertIn("body:JSON.stringify(selection)", page)
        self.assertIn("function queueIdentity(value)", page)
        self.assertIn("async function refreshQueue()", page)
        self.assertIn("setInterval(refreshQueue,3000)", page)

    def test_disjoint_adjacent_logo_regions_are_not_merged(self):
        report = self._report(
            "two-logo-regions", "visual_logo",
            [
                {
                    "start_seconds": 0, "end_seconds": 5, "max_score": 0.9,
                    "region_localization": {
                        "frame_size": [1280, 720],
                        "proposals": [{"blur_region_px": [900, 50, 200, 100]}],
                    },
                },
                {
                    "start_seconds": 5, "end_seconds": 10, "max_score": 0.9,
                    "region_localization": {
                        "frame_size": [1280, 720],
                        "proposals": [{"blur_region_px": [20, 20, 160, 90]}],
                    },
                },
            ],
        )
        queue = build_review_queue(
            project_root=self.root, report_paths=[report],
            queue_path=self.root / "reports" / "two-logo-review" / "queue.json",
        )
        self.assertEqual(len(queue["items"]), 2)
        self.assertEqual(
            pixel_region_iou(
                queue["items"][0]["suggested_region_source_pixels"],
                queue["items"][1]["suggested_region_source_pixels"],
            ),
            0.0,
        )

    def test_two_regions_in_one_frame_become_independent_review_items(self):
        report = self._report(
            "two-regions-one-frame", "visual_logo",
            [{
                "start_seconds": 25, "end_seconds": 30, "max_score": 0.91,
                "suggested_decision": "BLUR",
                "region_localization": {
                    "frame_size": [1920, 1080],
                    "proposals": [
                        {
                            "blur_region_px": [120, 50, 180, 90],
                            "labels": ["NewGates Anime"],
                            "sources": ["grounding", "ocr"],
                            "region_classification": "external_brand",
                        },
                        {
                            "blur_region_px": [1360, 50, 460, 110],
                            "labels": ["Movie title"],
                            "sources": ["ocr"],
                            "region_classification": "movie_title",
                            "suggested_decision": "KEEP",
                        },
                    ],
                },
            }],
        )
        queue = build_review_queue(
            project_root=self.root, report_paths=[report],
            queue_path=self.root / "reports" / "two-regions-review" / "queue.json",
        )
        self.assertEqual(len(queue["items"]), 2)
        by_class = {item["region_classification"]: item for item in queue["items"]}
        self.assertEqual(by_class["external_brand"]["suggested_decision"], "BLUR")
        self.assertEqual(by_class["movie_title"]["suggested_decision"], "KEEP")
        self.assertNotEqual(
            by_class["external_brand"]["id"], by_class["movie_title"]["id"]
        )

    def test_partial_overlap_cannot_leak_blur_to_unknown_dino_box(self):
        report = self._report(
            "partial-overlap-region-proof", "visual_logo", [{
                "start_seconds": 25, "end_seconds": 30, "max_score": 1.0,
                "visual_logo_confirmation": {
                    "state": "CONFIRMED", "confirmation_source": "qwen_local",
                },
                "region_localization": {
                    "frame_size": [1920, 1080],
                    "proposals": [{
                        "blur_region_px": [1751, 48, 114, 65],
                        "sources": ["grounding_dino"],
                        "region_classification": "unknown",
                        "suggested_decision": None,
                    }, {
                        "blur_region_px": [1591, 55, 268, 55],
                        "sources": ["ocr"], "labels": ["sample.net"],
                        "region_classification": "external_brand_candidate",
                        "suggested_decision": "BLUR",
                    }],
                },
            }],
        )
        queue = build_review_queue(
            project_root=self.root, report_paths=[report],
            queue_path=self.root / "reports" / "partial-overlap-review" / "queue.json",
        )
        self.assertEqual(queue["counts"]["total"], 1)
        self.assertEqual(queue["items"][0]["region_classification"], "external_brand_candidate")
        self.assertEqual(queue["items"][0]["suggested_decision"], "BLUR")
        self.assertEqual(len(queue["advisory_items"]), 1)
        self.assertEqual(queue["advisory_items"][0]["region_classification"], "unknown")

    def test_rejected_logo_candidate_is_optional_until_user_selects_it(self):
        directory = self.root / "reports" / "logo-advisory"
        (directory / "audit-thumbnails").mkdir(parents=True)
        (directory / "audit-thumbnails" / "window.jpg").write_bytes(b"image")
        report = directory / "scan.json"
        report.write_text(json.dumps({
            "status": "COMPLETED", "scan_type": "visual_logo",
            "input": str(self.source), "duration_seconds": 100,
            "intervals": [],
            "rejected_windows": [{
                "start_seconds": 20, "end_seconds": 25, "max_score": 0.3,
                "audit_frame": "audit-thumbnails/window.jpg",
                "visual_logo_confirmation": {
                    "state": "REJECTED", "confirmation_source": "qwen_local",
                },
            }],
        }), encoding="utf-8")
        queue_path = self.root / "reports" / "logo-advisory-review" / "queue.json"
        queue = build_review_queue(
            project_root=self.root, report_paths=[report], queue_path=queue_path,
        )
        self.assertEqual(queue["counts"]["total"], 0)
        self.assertEqual(len(queue["advisory_items"]), 1)
        updated = record_review_decision(
            project_root=self.root, queue_path=queue_path,
            item_id=queue["advisory_items"][0]["id"], decision="KEEP",
        )
        self.assertEqual(updated["counts"]["total"], 1)
        self.assertEqual(updated["counts"]["pending"], 0)
        self.assertEqual(updated["advisory_items"], [])

    def test_export_paths_are_stable_and_change_with_review_decisions(self):
        queue = {
            "source": {"path": "E:/input/Tập phim mới.mp4", "sha256": "abcdef123456"},
            "items": [{
                "id": "review-1", "decision": "CUT", "start_seconds": 0,
                "end_seconds": 5, "decision_region_source_pixels": None,
            }],
        }
        first = review_export_paths(self.root, queue)
        self.assertTrue(str(first[0]).endswith("-edit-plan.json"))
        self.assertTrue(str(first[1]).endswith("-reviewed.mp4"))
        queue["items"][0]["decision"] = "KEEP"
        second = review_export_paths(self.root, queue)
        self.assertNotEqual(first[0], second[0])
        default = review_export_paths(self.root, {
            **queue,
            "export_size_policy": {
                "mode": "default", "maximum_output_bytes": 3_500_000_000,
                "target_output_bytes": 3_300_000_000,
            },
        })
        self.assertEqual(second[0], default[0])
        unlimited = review_export_paths(self.root, {
            **queue,
            "export_size_policy": {
                "mode": "unlimited", "maximum_output_bytes": None,
                "target_output_bytes": None,
            },
        })
        self.assertNotEqual(default[0], unlimited[0])

    def test_text_region_union_keeps_horizontal_travel_and_tightens_height(self):
        merged = union_pixel_regions(
            {"x": 777, "y": 51, "width": 503, "height": 64},
            {"x": 0, "y": 48, "width": 1280, "height": 80},
        )
        self.assertEqual(merged, {"x": 0, "y": 48, "width": 1280, "height": 80})
        self.assertEqual(
            tighten_text_region(merged),
            {"x": 0, "y": 48, "width": 1280, "height": 72},
        )

    def test_full_width_text_defaults_to_vertical_feather(self):
        directory = self.root / "reports" / "scrolling-banner"
        directory.mkdir()
        report = directory / "text-scan.json"
        report.write_text(json.dumps({
            "status": "REVIEW_REQUIRED", "input": str(self.source),
            "duration_seconds": 100, "source_size": [1280, 720],
            "analysis_size": [960, 540],
            "tracks": [{
                "start_seconds": 10, "end_seconds": 20,
                "review_candidate": True, "union_box": [0, 36, 960, 96],
                "sample_text": ["scrolling banner"],
            }],
        }), encoding="utf-8")
        queue_path = self.root / "reports" / "banner-review" / "queue.json"
        queue = build_review_queue(
            project_root=self.root, report_paths=[report], queue_path=queue_path,
        )
        item = queue["items"][0]
        self.assertEqual(item["suggested_blur_edge_mode"], "vertical_only")
        updated = record_review_decision(
            project_root=self.root, queue_path=queue_path,
            item_id=item["id"], decision="BLUR",
        )
        self.assertEqual(updated["items"][0]["decision_blur_edge_mode"], "vertical_only")
        plan = build_edit_plan(
            project_root=self.root, queue_path=queue_path,
            plan_path=self.root / "work" / "banner-plan.json",
        )
        self.assertEqual(plan["approved_operations"][0]["blur"]["edge_feather_mode"], "vertical_only")

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "reports").mkdir()
        (self.root / "work").mkdir()
        (self.root / "input").mkdir()
        self.source = self.root / "input" / "source.mp4"
        self.source.write_bytes(b"source")

    def tearDown(self):
        self.temporary.cleanup()

    def _report(self, name, category, intervals):
        directory = self.root / "reports" / name
        (directory / "thumbs").mkdir(parents=True)
        for index in range(len(intervals)):
            (directory / "thumbs" / f"{index}.jpg").write_bytes(b"image")
            intervals[index]["strongest_frame"] = f"thumbs/{index}.jpg"
        path = directory / "scan.json"
        path.write_text(
            json.dumps(
                {
                    "status": "COMPLETED",
                    "scan_type": category,
                    "input": str(self.source),
                    "input_sha256": "abc",
                    "duration_seconds": 100,
                    "intervals": intervals,
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_safety_event_group_expands_to_original_intervals_in_edit_plan(self):
        report = self._report(
            "violence-event-group", "violence", [
                {"start_seconds": 10, "end_seconds": 12, "max_score": 0.8},
                {"start_seconds": 16, "end_seconds": 18, "max_score": 0.9},
                {"start_seconds": 60, "end_seconds": 62, "max_score": 0.7},
            ],
        )
        queue_path = self.root / "reports" / "violence-event-review" / "queue.json"
        queue = build_review_queue(
            project_root=self.root, report_paths=[report], queue_path=queue_path,
        )
        self.assertEqual(len(queue["items"]), 2)
        grouped = next(
            item for item in queue["items"]
            if item.get("candidate_type") == "review_event_group"
        )
        self.assertEqual(grouped["event_detection_count"], 2)
        self.assertEqual(grouped["temporal_policy"], "discrete_detected_intervals")
        record_review_decision(
            project_root=self.root, queue_path=queue_path,
            item_id=grouped["id"], decision="BLUR", full_frame=True,
        )
        other = next(item for item in queue["items"] if item["id"] != grouped["id"])
        record_review_decision(
            project_root=self.root, queue_path=queue_path,
            item_id=other["id"], decision="KEEP",
        )
        plan = build_edit_plan(
            project_root=self.root, queue_path=queue_path,
            plan_path=self.root / "work" / "violence-event-plan.json",
        )
        self.assertEqual(
            [
                (operation["start_seconds"], operation["end_seconds"])
                for operation in plan["approved_operations"]
            ],
            [(10.0, 12.0), (16.0, 18.0)],
        )

    def test_visual_detector_omissions_make_queue_coverage_incomplete(self):
        report = self._report(
            "incomplete-logo-selection", "visual_logo", [{
                "start_seconds": 0, "end_seconds": 5, "max_score": 0.9,
            }],
        )
        payload = json.loads(report.read_text(encoding="utf-8"))
        payload["candidate_selection_coverage"] = {
            "complete": False,
            "novel_candidate_windows": 10,
            "novel_candidate_windows_selected": 8,
            "novel_candidate_windows_omitted": 2,
        }
        report.write_text(json.dumps(payload), encoding="utf-8")
        queue = build_review_queue(
            project_root=self.root, report_paths=[report],
            queue_path=self.root / "reports" / "incomplete-review" / "queue.json",
        )
        self.assertFalse(queue["candidate_coverage"]["complete"])
        self.assertFalse(queue["candidate_coverage"]["detectors_complete"])
        self.assertFalse(queue["candidate_coverage"]["reports"][0]["detector_complete"])

    def test_single_source_unknown_logo_region_is_optional_not_blocking(self):
        report = self._report(
            "frame-logo-leakage", "visual_logo", [{
                "start_seconds": 50, "end_seconds": 55, "max_score": 1.0,
                "visual_logo_confirmation": {
                    "state": "CONFIRMED", "confirmation_source": "qwen_local",
                },
                "region_localization": {
                    "frame_size": [1920, 1080],
                    "proposals": [{
                        "blur_region_px": [860, 120, 480, 480],
                        "sources": ["grounding"],
                        "labels": ["a company logo"],
                        "region_classification": "unknown",
                        "suggested_decision": None,
                    }, {
                        "blur_region_px": [1550, 44, 310, 68],
                        "sources": ["grounding", "ocr"],
                        "labels": ["External watermark"],
                        "region_classification": "external_brand",
                        "suggested_decision": "BLUR",
                    }],
                },
            }],
        )
        queue = build_review_queue(
            project_root=self.root, report_paths=[report],
            queue_path=self.root / "reports" / "leakage-review" / "queue.json",
        )
        self.assertEqual(queue["counts"]["total"], 1)
        self.assertEqual(queue["items"][0]["region_classification"], "external_brand")
        self.assertEqual(len(queue["advisory_items"]), 1)
        advisory = queue["advisory_items"][0]
        self.assertEqual(advisory["candidate_type"], "uncorroborated_logo_region")
        self.assertIsNone(advisory["suggested_decision"])

    def test_multisource_unknown_region_is_advisory_and_counts_as_covered(self):
        report = self._report(
            "multisource-frame-leakage", "visual_logo", [{
                "start_seconds": 50, "end_seconds": 55, "max_score": 1.0,
                "visual_logo_confirmation": {
                    "state": "CONFIRMED", "confirmation_source": "qwen_local",
                },
                "region_localization": {
                    "frame_size": [1920, 1080],
                    "proposals": [{
                        "blur_region_px": [860, 120, 480, 480],
                        "sources": ["grounding", "ocr"],
                        "labels": ["story sign"],
                        "region_classification": "unknown",
                        "suggested_decision": None,
                    }],
                },
            }],
        )
        queue = build_review_queue(
            project_root=self.root, report_paths=[report],
            queue_path=self.root / "reports" / "multisource-leakage-review" / "queue.json",
        )
        self.assertEqual(queue["counts"]["total"], 0)
        self.assertEqual(len(queue["advisory_items"]), 1)
        self.assertIsNone(queue["advisory_items"][0]["suggested_decision"])
        self.assertTrue(queue["candidate_coverage"]["reference_complete"])
        self.assertEqual(queue["candidate_coverage"]["missing_refs"], [])

    def test_low_ad_ocr_track_stays_visible_as_optional_candidate(self):
        directory = self.root / "reports" / "low-ad-text"
        directory.mkdir()
        report = directory / "text-scan.json"
        report.write_text(json.dumps({
            "status": "REVIEW_REQUIRED", "input": str(self.source),
            "input_sha256": "abc", "duration_seconds": 100,
            "source_size": [1920, 1080], "analysis_size": [960, 540],
            "tracks": [{
                "track_id": 1, "start_seconds": 40, "end_seconds": 43,
                "recommended_blur_end_seconds": 43.5,
                "review_candidate": False, "routing": "LOW_AD_UNCERTAIN",
                "review_priority": "low", "ad_probability": 0.04,
                "union_box": [300, 200, 450, 240], "sample_text": ["870"],
                "reason": "Điểm quảng cáo thấp",
            }],
        }), encoding="utf-8")
        queue = build_review_queue(
            project_root=self.root, report_paths=[report],
            queue_path=self.root / "reports" / "low-ad-review" / "queue.json",
        )
        self.assertEqual(queue["counts"]["total"], 0)
        self.assertEqual(len(queue["advisory_items"]), 1)
        self.assertEqual(
            queue["advisory_items"][0]["candidate_type"],
            "low_ad_text_candidate",
        )

    def test_legacy_low_ad_review_uncertain_is_migrated_to_optional_candidate(self):
        directory = self.root / "reports" / "legacy-low-ad-text"
        directory.mkdir()
        report = directory / "text-scan.json"
        report.write_text(json.dumps({
            "status": "REVIEW_REQUIRED", "input": str(self.source),
            "input_sha256": "abc", "duration_seconds": 100,
            "source_size": [1920, 1080], "analysis_size": [960, 540],
            "tracks": [{
                "track_id": 2, "start_seconds": 50, "end_seconds": 53,
                "recommended_blur_end_seconds": 53.5,
                "review_candidate": True, "routing": "REVIEW_UNCERTAIN",
                "review_priority": "low", "ad_probability": 0.01,
                "persistent": False,
                "visual_features": {"overlay_signal": False},
                "union_box": [100, 100, 200, 140], "sample_text": ["OUT"],
            }],
        }), encoding="utf-8")
        queue = build_review_queue(
            project_root=self.root, report_paths=[report],
            queue_path=self.root / "reports" / "legacy-low-ad-review" / "queue.json",
        )
        self.assertEqual(queue["counts"]["total"], 0)
        self.assertEqual(len(queue["advisory_items"]), 1)

    def test_low_ad_overlay_signal_without_policy_hit_is_optional(self):
        directory = self.root / "reports" / "low-ad-scene-emblem"
        directory.mkdir()
        report = directory / "text-scan.json"
        report.write_text(json.dumps({
            "status": "REVIEW_REQUIRED", "input": str(self.source),
            "input_sha256": "abc", "duration_seconds": 100,
            "source_size": [1920, 1080], "analysis_size": [960, 540],
            "tracks": [{
                "track_id": 78, "start_seconds": 40, "end_seconds": 50,
                "review_candidate": True, "routing": "REVIEW_UNCERTAIN",
                "review_priority": "medium", "ad_probability": 0.10,
                "visual_features": {"overlay_signal": True},
                "policy_hits": [], "union_box": [270, 28, 507, 216],
                "sample_text": ["DEPARTMENT OF JUSTICE"],
            }],
        }), encoding="utf-8")
        queue = build_review_queue(
            project_root=self.root, report_paths=[report],
            queue_path=self.root / "reports" / "low-ad-scene-emblem-review" / "queue.json",
        )
        self.assertEqual(queue["counts"]["total"], 0)
        self.assertEqual(len(queue["advisory_items"]), 1)
        self.assertEqual(
            queue["advisory_items"][0]["candidate_type"],
            "low_ad_text_candidate",
        )

    def test_build_queue_merges_same_category_and_blocks_unresolved_plan(self):
        first = self._report(
            "violence-a",
            "violence",
            [{"start_seconds": 10, "end_seconds": 15, "max_score": 0.8, "priority": "context"}],
        )
        second = self._report(
            "violence-b",
            "violence",
            [{"start_seconds": 14, "end_seconds": 18, "max_score": 0.95, "priority": "high"}],
        )
        gore = self._report(
            "gore",
            "gore",
            [{"start_seconds": 40, "end_seconds": 42, "max_score": 0.7, "reason": "high_score"}],
        )
        queue_path = self.root / "reports" / "review" / "queue.json"
        queue = build_review_queue(
            project_root=self.root,
            report_paths=[first, second, gore],
            queue_path=queue_path,
        )
        self.assertEqual(queue["counts"]["total"], 2)
        self.assertEqual(queue["items"][0]["priority"], "high")
        self.assertEqual(len(queue["items"][0]["evidence"]), 2)
        self.assertTrue(queue_path.with_suffix(".html").exists())
        with self.assertRaisesRegex(ValueError, "remain unresolved"):
            build_edit_plan(
                project_root=self.root,
                queue_path=queue_path,
                plan_path=self.root / "work" / "plan.json",
            )

        for item in queue["items"]:
            record_review_decision(
                project_root=self.root,
                queue_path=queue_path,
                item_id=item["id"],
                decision="CUT" if item["category"] == "violence" else "KEEP",
                note="test decision",
            )
        plan = build_edit_plan(
            project_root=self.root,
            queue_path=queue_path,
            plan_path=self.root / "work" / "plan.json",
        )
        self.assertEqual(plan["status"], "READY_FOR_PREVIEW")
        self.assertEqual(len(plan["approved_operations"]), 1)
        self.assertEqual(plan["approved_operations"][0]["type"], "cut")
        self.assertFalse(plan["final_export_allowed"])

    def test_blur_requires_region_or_explicit_full_frame(self):
        report = self._report(
            "adult", "adult",
            [{"start_seconds": 5, "end_seconds": 6, "max_score": 0.9}],
        )
        queue_path = self.root / "reports" / "review" / "queue.json"
        queue = build_review_queue(
            project_root=self.root, report_paths=[report], queue_path=queue_path,
        )
        item_id = queue["items"][0]["id"]
        with self.assertRaisesRegex(ValueError, "requires a region"):
            record_review_decision(
                project_root=self.root, queue_path=queue_path,
                item_id=item_id, decision="BLUR",
            )
        updated = record_review_decision(
            project_root=self.root, queue_path=queue_path,
            item_id=item_id, decision="BLUR", full_frame=True,
        )
        self.assertEqual(
            updated["items"][0]["decision_region_source_pixels"], "FULL_FRAME"
        )
        cleared = clear_review_decision(
            project_root=self.root, queue_path=queue_path, item_id=item_id,
        )
        self.assertIsNone(cleared["items"][0]["decision"])
        self.assertEqual(cleared["counts"]["pending"], 1)

    def test_full_frame_blur_can_override_a_suggested_logo_region(self):
        report = self._report(
            "logo-full-frame", "visual_logo",
            [{
                "start_seconds": 5, "end_seconds": 8, "max_score": 0.9,
                "region_localization": {
                    "proposals": [{"blur_region_px": [100, 40, 200, 80]}]
                },
            }],
        )
        queue_path = self.root / "reports" / "review-full-frame" / "queue.json"
        queue = build_review_queue(
            project_root=self.root, report_paths=[report], queue_path=queue_path,
        )
        updated = record_review_decision(
            project_root=self.root, queue_path=queue_path,
            item_id=queue["items"][0]["id"], decision="BLUR", full_frame=True,
        )
        self.assertEqual(
            updated["items"][0]["decision_region_source_pixels"], "FULL_FRAME"
        )

    def test_review_interval_adjustment_is_validated_and_audited(self):
        report = self._report(
            "logo", "visual_logo",
            [{"start_seconds": 6, "end_seconds": 9.5, "max_score": 0.9}],
        )
        queue_path = self.root / "reports" / "review-adjust" / "queue.json"
        queue = build_review_queue(
            project_root=self.root, report_paths=[report], queue_path=queue_path,
        )
        item_id = queue["items"][0]["id"]
        with self.assertRaisesRegex(ValueError, "requires both"):
            record_review_decision(
                project_root=self.root, queue_path=queue_path, item_id=item_id,
                decision="CUT", start_seconds=0,
            )
        updated = record_review_decision(
            project_root=self.root, queue_path=queue_path, item_id=item_id,
            decision="CUT", start_seconds=0, end_seconds=10.5,
            note="continuous approved intro",
        )
        item = updated["items"][0]
        self.assertEqual(item["detected_interval"], {
            "start_seconds": 6.0, "end_seconds": 9.5,
        })
        self.assertEqual((item["start_seconds"], item["end_seconds"]), (0.0, 10.5))
        self.assertEqual(updated["audit_log"][-1]["original_interval"], {
            "start_seconds": 6.0, "end_seconds": 9.5,
        })
        plan = build_edit_plan(
            project_root=self.root, queue_path=queue_path,
            plan_path=self.root / "work" / "adjusted-plan.json",
        )
        self.assertEqual(plan["approved_operations"][0]["detected_intervals"], [{
            "review_item_id": item_id,
            "start_seconds": 6.0, "end_seconds": 9.5,
        }])

    def test_edit_plan_merges_overlapping_cuts_with_provenance(self):
        logo = self._report(
            "logo", "visual_logo",
            [{"start_seconds": 0, "end_seconds": 10, "max_score": 0.9}],
        )
        text = self._report(
            "text", "text",
            [{"start_seconds": 6, "end_seconds": 9.5, "max_score": 0.8}],
        )
        queue_path = self.root / "reports" / "review-merge" / "queue.json"
        queue = build_review_queue(
            project_root=self.root, report_paths=[logo, text], queue_path=queue_path,
        )
        for item in queue["items"]:
            record_review_decision(
                project_root=self.root, queue_path=queue_path,
                item_id=item["id"], decision="CUT",
            )
        plan = build_edit_plan(
            project_root=self.root, queue_path=queue_path,
            plan_path=self.root / "work" / "merged-plan.json",
        )
        self.assertEqual(len(plan["approved_operations"]), 1)
        operation = plan["approved_operations"][0]
        self.assertEqual((operation["start_seconds"], operation["end_seconds"]), (0, 10))
        self.assertEqual(set(operation["review_item_ids"]), {item["id"] for item in queue["items"]})
        self.assertEqual(len(operation["detected_intervals"]), 2)

    def test_bulk_keep_only_changes_pending_items_in_filter(self):
        report = self._report(
            "mixed", "gore",
            [
                {"start_seconds": 5, "end_seconds": 6, "max_score": 0.9, "priority": "high"},
                # 39 s apart: two blood scenes, not one scene card (the gap is not under 20 s).
                {"start_seconds": 45, "end_seconds": 46, "max_score": 0.4, "priority": "context"},
            ],
        )
        queue_path = self.root / "reports" / "review" / "queue.json"
        queue = build_review_queue(
            project_root=self.root, report_paths=[report], queue_path=queue_path,
        )
        updated = bulk_keep_review_items(
            project_root=self.root, queue_path=queue_path, review_filter="high",
        )
        self.assertEqual(updated["counts"]["pending"], 1)
        self.assertEqual(updated["counts"]["decisions"]["KEEP"], 1)
        self.assertEqual(updated["audit_log"][-1]["action"], "BULK_KEEP")
        status = review_resource_status(project_root=self.root, queue_path=queue_path)
        self.assertEqual(status["source_bytes"], 6)
        self.assertFalse(status["review_mode"]["runs_ffmpeg"])

    def test_bulk_keep_supports_visual_logo_filter(self):
        report = self._report(
            "logos", "visual_logo",
            [{"start_seconds": 25, "end_seconds": 28, "max_score": 0.8}],
        )
        queue_path = self.root / "reports" / "review-logo" / "queue.json"
        build_review_queue(
            project_root=self.root, report_paths=[report], queue_path=queue_path,
        )
        updated = bulk_keep_review_items(
            project_root=self.root, queue_path=queue_path,
            review_filter="visual_logo",
        )
        self.assertEqual(updated["counts"]["pending"], 0)
        self.assertEqual(updated["counts"]["decisions"]["KEEP"], 1)

    def test_bulk_accept_applies_end_card_cut_suggestion_once(self):
        report = self._report(
            "end-card", "visual_logo",
            [{
                "start_seconds": 92, "end_seconds": 100, "max_score": 0.9,
                "candidate_type": "branded_end_card",
                "suggested_decision": "CUT",
                "supporting_frames": ["thumbs/0.jpg"],
                "region_localization": {
                    "frame_size": [1920, 1080],
                    "proposals": [{
                        "blur_region_px": [1553, 45, 308, 67],
                        "suggested_decision": "BLUR",
                    }],
                },
            }],
        )
        queue_path = self.root / "reports" / "review-end-card" / "queue.json"
        queue = build_review_queue(
            project_root=self.root, report_paths=[report], queue_path=queue_path,
        )
        self.assertEqual(len(queue["items"]), 1)
        self.assertEqual(queue["items"][0]["suggested_decision"], "CUT")
        self.assertEqual(queue["items"][0]["review_kind"], "branded_end_card")
        self.assertIsNone(queue["items"][0]["suggested_region_source_pixels"])
        updated = bulk_accept_suggested_decisions(
            project_root=self.root, queue_path=queue_path, review_filter="visual_logo",
        )
        self.assertEqual(updated["counts"]["pending"], 0)
        self.assertEqual(updated["items"][0]["decision"], "CUT")
        self.assertEqual(updated["audit_log"][-1]["action"], "BULK_ACCEPT_SUGGESTIONS")

    def test_visual_logo_florence_region_is_suggested_for_blur(self):
        report = self._report(
            "logo-region", "visual_logo",
            [{
                "start_seconds": 2, "end_seconds": 5, "max_score": 0.9,
                "region_localization": {
                    "proposals": [{"blur_region_px": [100, 50, 220, 90]}]
                },
            }],
        )
        queue = build_review_queue(
            project_root=self.root, report_paths=[report],
            queue_path=self.root / "reports" / "review-region" / "queue.json",
        )
        self.assertEqual(
            queue["items"][0]["suggested_region_source_pixels"],
            {"x": 100, "y": 50, "width": 220, "height": 90},
        )

    def test_persistent_logo_blur_suggestion_reaches_review(self):
        report = self._report(
            "persistent-logo", "visual_logo",
            [{
                "start_seconds": 6, "end_seconds": 90, "max_score": 0.9,
                "candidate_type": "persistent_overlay", "suggested_decision": "BLUR",
                "region_localization": {
                    "proposals": [{"blur_region_px": [10, 20, 80, 40]}]
                },
            }],
        )
        queue = build_review_queue(
            project_root=self.root, report_paths=[report],
            queue_path=self.root / "reports" / "review-persistent" / "queue.json",
        )
        self.assertEqual(queue["items"][0]["suggested_decision"], "BLUR")

    def test_opening_full_frame_windows_become_one_cut_suggestion(self):
        intervals = []
        for start in (0, 5, 10):
            intervals.append({
                "start_seconds": start, "end_seconds": start + 5,
                "max_score": 1.0,
                "visual_logo_confirmation": {
                    "boundary_window": True,
                    "boundary_scene_context": {"state": "UNCERTAIN"},
                    "features": {"full_frame_score": 0.9},
                },
                "region_localization": {
                    "proposals": [{"blur_region_px": [100, 100, 500, 300]}],
                },
            })
        intervals.append({
            "start_seconds": 15, "end_seconds": 20, "max_score": 1.0,
            "visual_logo_confirmation": {
                "boundary_window": True,
                "boundary_scene_context": {"state": "MOVIE_CONTENT"},
                "features": {"full_frame_score": 1.0},
            },
            "region_localization": {
                "proposals": [{"blur_region_px": [900, 20, 120, 50]}],
            },
        })
        report = self._report("opening-promotion", "visual_logo", intervals)
        queue = build_review_queue(
            project_root=self.root, report_paths=[report],
            queue_path=self.root / "reports" / "opening-review" / "queue.json",
        )
        opening = next(
            item for item in queue["items"]
            if item.get("candidate_type") == "opening_promotion"
        )
        self.assertEqual((opening["start_seconds"], opening["end_seconds"]), (0, 15))
        self.assertEqual(opening["suggested_decision"], "CUT")
        self.assertIsNone(opening["suggested_region_source_pixels"])
        self.assertEqual(len(opening["source_candidate_refs"]), 3)
        self.assertEqual(len(opening["detected_intervals"]), 3)
        self.assertTrue(queue["candidate_coverage"]["complete"])
        self.assertEqual(queue["candidate_coverage"]["source_candidate_count"], 4)
        self.assertEqual(queue["candidate_coverage"]["queue_item_count"], 2)

    def test_full_duration_persistent_overlay_cannot_become_opening_cut(self):
        report = self._report(
            "persistent-not-opening", "visual_logo", [{
                "start_seconds": 0.75, "end_seconds": 100,
                "max_score": 1.0, "candidate_type": "persistent_overlay",
                "visual_logo_confirmation": {
                    "state": "CONFIRMED", "boundary_window": True,
                    "boundary_scene_context": {"state": "PROMO_FULL_FRAME"},
                    "features": {"full_frame_score": 0.95},
                },
                "region_localization": {
                    "frame_size": [1920, 1080],
                    "proposals": [{
                        "blur_region_px": [1550, 44, 310, 68],
                        "sources": ["brand_memory"],
                        "region_classification": "external_brand",
                        "suggested_decision": "BLUR",
                    }],
                },
            }],
        )
        queue = build_review_queue(
            project_root=self.root, report_paths=[report],
            queue_path=self.root / "reports" / "persistent-not-opening-review" / "queue.json",
        )
        self.assertEqual(len(queue["items"]), 1)
        self.assertEqual(queue["items"][0]["candidate_type"], "persistent_overlay")
        self.assertEqual(queue["items"][0]["suggested_decision"], "BLUR")
        self.assertIsInstance(
            queue["items"][0]["suggested_region_source_pixels"], dict
        )

    def test_persistent_text_does_not_swallow_unrelated_candidates_and_is_clamped(self):
        directory = self.root / "reports" / "text-separation"
        directory.mkdir()
        report = directory / "text-scan.json"
        report.write_text(json.dumps({
            "status": "REVIEW_REQUIRED", "input": str(self.source),
            "duration_seconds": 100, "source_size": [1920, 1080],
            "analysis_size": [960, 540],
            "tracks": [
                {
                    "track_id": 1, "start_seconds": 0, "end_seconds": 100,
                    "recommended_blur_end_seconds": 103,
                    "review_candidate": True, "review_priority": "high",
                    "routing": "REVIEW_PERSISTENT_OVERLAY",
                    "candidate_type": "persistent_overlay",
                    "suggested_decision": "BLUR",
                    "union_box": [776, 22, 930, 56],
                    "sample_text": ["PHIMONLINE.NET"],
                },
                {
                    "track_id": 2, "start_seconds": 20, "end_seconds": 23,
                    "recommended_blur_end_seconds": 23.5,
                    "review_candidate": True, "review_priority": "low",
                    "routing": "REVIEW_UNCERTAIN", "candidate_type": None,
                    "union_box": [100, 200, 300, 240],
                    "sample_text": ["SCENE TEXT"],
                },
            ],
        }), encoding="utf-8")
        queue = build_review_queue(
            project_root=self.root, report_paths=[report],
            queue_path=self.root / "reports" / "text-separation-review" / "queue.json",
        )
        self.assertEqual(len(queue["items"]), 2)
        persistent = next(
            item for item in queue["items"]
            if item.get("candidate_type") == "persistent_overlay"
        )
        self.assertEqual(persistent["end_seconds"], 100)
        self.assertEqual(persistent["labels"], ["PHIMONLINE.NET"])

    def test_visual_and_ocr_persistent_logo_are_one_review_decision(self):
        visual = self._report(
            "persistent-visual-link", "visual_logo", [
                {
                    "start_seconds": 140, "end_seconds": 100,
                    "max_score": 1.0, "candidate_type": "persistent_overlay",
                    "suggested_decision": "BLUR",
                    "region_localization": {
                        "frame_size": [1920, 1080],
                        "proposals": [{"blur_region_px": [1553, 44, 308, 68]}],
                    },
                },
                {
                    "start_seconds": 20, "end_seconds": 25,
                    "max_score": 1.0,
                    "region_localization": {
                        "frame_size": [1920, 1080],
                        "proposals": [{"blur_region_px": [1554, 44, 307, 68]}],
                    },
                },
            ],
        )
        # The helper uses a 100-second source; keep the visual interval valid.
        payload = json.loads(visual.read_text(encoding="utf-8"))
        payload["intervals"][0]["start_seconds"] = 10
        visual.write_text(json.dumps(payload), encoding="utf-8")
        directory = self.root / "reports" / "persistent-text-link"
        directory.mkdir()
        text = directory / "text-scan.json"
        text.write_text(json.dumps({
            "status": "REVIEW_REQUIRED", "input": str(self.source),
            "input_sha256": "abc", "duration_seconds": 100,
            "source_size": [1920, 1080], "analysis_size": [960, 540],
            "tracks": [{
                "track_id": 7, "start_seconds": 0, "end_seconds": 100,
                "recommended_blur_end_seconds": 103,
                "review_candidate": True, "review_priority": "high",
                "routing": "REVIEW_PERSISTENT_OVERLAY",
                "candidate_type": "persistent_overlay",
                "suggested_decision": "BLUR",
                "union_box": [776, 22, 930, 56],
                "sample_text": ["PHIMONLINE.NET"],
            }, {
                "track_id": 8, "start_seconds": 40, "end_seconds": 45,
                "recommended_blur_end_seconds": 45.5,
                "review_candidate": True, "review_priority": "high",
                "routing": "REVIEW_UNCERTAIN",
                "suggested_decision": None,
                "union_box": [901, 37, 925, 48],
                "sample_text": ["NPt"],
            }],
        }), encoding="utf-8")
        queue = build_review_queue(
            project_root=self.root, report_paths=[visual, text],
            queue_path=self.root / "reports" / "persistent-link-review" / "queue.json",
        )
        persistent = [
            item for item in queue["items"]
            if item.get("candidate_type") == "persistent_overlay"
        ]
        self.assertEqual(len(persistent), 1)
        self.assertEqual((persistent[0]["start_seconds"], persistent[0]["end_seconds"]), (0, 100))
        self.assertEqual(persistent[0]["linked_detector_categories"], ["text", "visual_logo"])
        self.assertEqual(len(persistent[0]["evidence"]), 2)
        self.assertEqual(len(persistent[0]["source_candidate_refs"]), 4)
        self.assertIn("NPt", persistent[0]["labels"])
        self.assertEqual(queue["candidate_coverage"]["source_candidate_count"], 4)
        self.assertEqual(queue["candidate_coverage"]["queue_item_count"], 1)
        self.assertTrue(queue["candidate_coverage"]["complete"])

    def test_ocr_only_persistent_track_absorbs_matching_visual_cards(self):
        directory = self.root / "reports" / "ocr-only-persistent"
        directory.mkdir()
        text = directory / "text-scan.json"
        text.write_text(json.dumps({
            "status": "REVIEW_REQUIRED", "input": str(self.source),
            "input_sha256": "abc", "duration_seconds": 100,
            "source_size": [1920, 1080], "analysis_size": [960, 540],
            "tracks": [{
                "track_id": 1, "start_seconds": 0, "end_seconds": 100,
                "review_candidate": True, "review_priority": "high",
                "routing": "REVIEW_PERSISTENT_OVERLAY",
                "candidate_type": "persistent_overlay",
                "suggested_decision": "BLUR",
                "union_box": [50, 25, 150, 50],
                "sample_text": ["EXAMPLE.NET"],
            }],
        }), encoding="utf-8")
        visual = self._report(
            "matching-visual-cards", "visual_logo", [{
                "start_seconds": 20, "end_seconds": 25, "max_score": 1.0,
                "region_localization": {
                    "frame_size": [1920, 1080],
                    "proposals": [{
                        "blur_region_px": [100, 50, 200, 50],
                        "region_classification": "external_brand_candidate",
                        "suggested_decision": "BLUR",
                    }],
                },
            }],
        )
        queue = build_review_queue(
            project_root=self.root, report_paths=[text, visual],
            queue_path=self.root / "reports" / "ocr-only-review" / "queue.json",
        )
        self.assertEqual(len(queue["items"]), 1)
        track = queue["items"][0]
        self.assertEqual(track["candidate_type"], "persistent_overlay")
        self.assertEqual(track["category"], "text")
        self.assertEqual(track["supporting_candidate_count"], 1)
        self.assertEqual(len(track["supporting_detections"]), 1)
        self.assertEqual(queue["candidate_coverage"]["source_candidate_count"], 2)
        self.assertTrue(queue["candidate_coverage"]["complete"])

    def test_repeated_ocr_overlay_is_routed_to_logo_review(self):
        directory = self.root / "reports" / "ocr-overlay"
        directory.mkdir()
        report = directory / "text-scan.json"
        report.write_text(json.dumps({
            "status": "REVIEW_REQUIRED", "input": str(self.source),
            "duration_seconds": 100, "source_size": [1920, 1080],
            "analysis_size": [960, 540],
            "tracks": [{
                "track_id": 9, "start_seconds": 6, "end_seconds": 90,
                "recommended_blur_end_seconds": 90.5,
                "review_candidate": True, "review_priority": "high",
                "routing": "REVIEW_PERSISTENT_OVERLAY",
                "candidate_type": "persistent_overlay",
                "suggested_decision": "BLUR",
                "union_box": [682, 28, 908, 78],
                "sample_text": ["SHIN CẬU BÉ BÚT CHÌ"],
            }],
        }), encoding="utf-8")
        queue = build_review_queue(
            project_root=self.root, report_paths=[report],
            queue_path=self.root / "reports" / "ocr-overlay-review" / "queue.json",
        )
        self.assertEqual(queue["items"][0]["category"], "text")
        self.assertEqual(queue["items"][0]["review_kind"], "logo_overlay")
        self.assertEqual(queue["items"][0]["suggested_decision"], "BLUR")
        self.assertEqual(
            queue["items"][0]["suggested_region_source_pixels"],
            {"x": 1364, "y": 56, "width": 452, "height": 92},
        )

    def test_low_ad_title_prevents_visual_logo_blur_suggestion(self):
        text_dir = self.root / "reports" / "title-guard-text"
        text_dir.mkdir()
        text_report = text_dir / "text-scan.json"
        text_report.write_text(json.dumps({
            "status": "REVIEW_REQUIRED", "input": str(self.source),
            "duration_seconds": 100, "source_size": [1920, 1080],
            "analysis_size": [960, 540],
            "tracks": [{
                "track_id": 1, "start_seconds": 6, "end_seconds": 90,
                "recommended_blur_end_seconds": 90.5,
                "review_candidate": False, "routing": "LIKELY_TITLE_OVERLAY",
                "candidate_type": "title_overlay", "union_box": [682, 28, 908, 78],
                "sample_text": ["TÊN PHIM"],
            }],
        }), encoding="utf-8")
        visual_report = self._report(
            "title-guard-logo", "visual_logo",
            [{
                "start_seconds": 25, "end_seconds": 30, "max_score": 0.9,
                "suggested_decision": "BLUR",
                "region_localization": {
                    "proposals": [{"blur_region_px": [1363, 51, 454, 108]}]
                },
            }],
        )
        queue = build_review_queue(
            project_root=self.root, report_paths=[visual_report, text_report],
            queue_path=self.root / "reports" / "title-guard-review" / "queue.json",
        )
        item = queue["items"][0]
        self.assertEqual(item["review_kind"], "title_overlay")
        self.assertEqual(item["suggested_decision"], "KEEP")
        self.assertEqual(item["candidate_type"], "title_overlay")

    def test_adjacent_visual_candidate_types_keep_separate_actions(self):
        report = self._report(
            "logo-segments", "visual_logo",
            [
                {
                    "start_seconds": 0, "end_seconds": 5, "max_score": 0.8,
                    "candidate_type": "opening_boundary",
                },
                {
                    "start_seconds": 5, "end_seconds": 90, "max_score": 0.9,
                    "candidate_type": "persistent_overlay",
                    "suggested_decision": "BLUR",
                    "region_localization": {
                        "proposals": [{"blur_region_px": [10, 20, 80, 40]}]
                    },
                },
                {
                    "start_seconds": 90, "end_seconds": 100, "max_score": 0.95,
                    "candidate_type": "branded_end_card",
                    "suggested_decision": "CUT",
                },
            ],
        )
        queue = build_review_queue(
            project_root=self.root, report_paths=[report],
            queue_path=self.root / "reports" / "review-logo-segments" / "queue.json",
        )
        visual_items = [
            item for item in queue["items"] if item["category"] == "visual_logo"
        ]
        self.assertEqual(len(visual_items), 3)
        by_type = {item["candidate_type"]: item for item in visual_items}
        self.assertIsNone(by_type["opening_boundary"]["suggested_decision"])
        self.assertEqual(by_type["persistent_overlay"]["suggested_decision"], "BLUR")
        self.assertEqual(by_type["branded_end_card"]["suggested_decision"], "CUT")

    def test_visual_logo_prompt_echo_is_not_exposed_as_a_brand_label(self):
        report = self._report(
            "logo-echo", "visual_logo",
            [{
                "start_seconds": 2, "end_seconds": 5, "max_score": 0.9,
                "predicted_label": (
                    "brand name when known, NO | unknown brand when branding exists"
                ),
            }],
        )
        queue = build_review_queue(
            project_root=self.root, report_paths=[report],
            queue_path=self.root / "reports" / "review-echo" / "queue.json",
        )
        self.assertEqual(queue["items"][0]["labels"], [])


def _js_function(page, name):
    """Source of one named JS function in the review page (brace matched)."""
    match = re.search(rf"(?:async )?function {re.escape(name)}\(", page)
    if match is None:
        raise AssertionError(f"function {name} is missing from the review page")
    start = page.index("{", match.end())
    depth = 0
    for index in range(start, len(page)):
        if page[index] == "{":
            depth += 1
        elif page[index] == "}":
            depth -= 1
            if depth == 0:
                return page[match.start():index + 1]
    raise AssertionError(f"function {name} is not closed")


class FocusReviewPageTests(unittest.TestCase):
    def setUp(self):
        self.page = _interactive_html("token")

    def test_api_paths_come_from_one_rewritable_constant_and_nothing_external(self):
        page = self.page
        self.assertIn("const API='/api/';", page)
        self.assertEqual(page.count("'/api/"), 1)
        self.assertNotRegex(page, r"https?://")
        self.assertNotRegex(page, r'<link[^>]+href="(?!data:)')
        self.assertNotRegex(page, r"<script[^>]+src=")
        hostile = _interactive_html('a"</script><b>')
        self.assertIn('let token="a\\"\\u003c/script>\\u003cb>";', hostile)
        self.assertEqual(hostile.count("</script>"), 1)
        self.assertNotIn("__BILIFLOW_REVIEW_TOKEN__", page)

    def test_focus_layout_keeps_one_item_list_navigation_and_four_decisions(self):
        page = self.page
        for label in (
            'data-filter="pending"', 'data-filter="adult"', 'data-filter="gore"',
            'data-filter="violence"', 'data-filter="ads"', 'data-filter="all"',
            "Chưa duyệt (${c.pending})", "← Trước", "Sau →", "↶ Hoàn tác",
            "Tự sang mục chưa duyệt kế tiếp", "Danh sách để chọn lại ▾",
            "Giữ nguyên", "Làm mờ cả cảnh", "Cắt cảnh", "Cần xem thêm", "phím ${key}",
            "Rõ nhất lúc", "▶ Phát đoạn này", "Chi tiết kỹ thuật", "Xuất video",
            "máy nghi ngờ ở ${seeds.count} khung",
            "bản quét cũ: chưa lưu thời điểm từng khung nghi ngờ",
            "Duyệt tất cả đề xuất đang lọc", "Giữ nguyên tất cả đang lọc",
            "Hoàn tất duyệt và xuất video", "Bỏ chọn",
        ):
            with self.subTest(label=label):
                self.assertIn(label, page)
        self.assertEqual(page.count("<video"), 1)
        self.assertIn('<video id="video" preload="none"', page)
        self.assertIn("@media (max-width:820px)", page)
        self.assertIn("grid-template-columns:280px minmax(0,1fr)", page)
        self.assertIn("content-visibility:auto", page)

    def test_keyboard_shortcuts_ignore_typing_and_key_repeat(self):
        handler = _js_function(self.page, "onKeyDown")
        self.assertIn("document.addEventListener('keydown',onKeyDown)", self.page)
        for fragment in ("isTyping(e.target)", "e.repeat", "keyDecision(Number(k))",
                         "'ArrowLeft'", "'ArrowRight'", "k===' '", "togglePlay()", "undo()"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, handler)
        typing = _js_function(self.page, "isTyping")
        self.assertIn("TEXTAREA", typing)
        self.assertIn("SELECT", typing)
        self.assertIn("checkbox", typing)
        choice = _js_function(self.page, "keyDecision")
        self.assertIn("2:['BLUR',true]", choice)

    def test_polling_patches_in_place_and_never_rebuilds_the_focus_card(self):
        poll = _js_function(self.page, "refreshQueue")
        self.assertIn("if(queueVersion(latest)!==queueVersion(queue))", poll)
        self.assertIn("pendingWrites", poll)
        self.assertIn("epoch!==localEpoch", poll)
        for call in ("render()", "renderFocus(", "renderList(", "setupSafetyMedia("):
            with self.subTest(call=call):
                self.assertNotIn(call, poll)
        update = _js_function(self.page, "applyQueueUpdate")
        same_revision = update.split("render();return;}", 1)[1]
        self.assertIn("updateListStatuses()", same_revision)
        self.assertIn("refreshFocusIfChanged()", same_revision)
        self.assertNotIn("renderFocus(", same_revision)
        self.assertNotIn("render()", same_revision)
        refresh = _js_function(self.page, "refreshFocusIfChanged")
        self.assertIn("renderSide(x)", refresh)
        self.assertNotIn("setupSafetyMedia(", refresh)
        self.assertIn("side.__html===html", _js_function(self.page, "renderSide"))
        statuses = _js_function(self.page, "updateListStatuses")
        self.assertIn("pill.textContent=label", statuses)

    def test_evidence_is_lazy_one_item_ahead_and_media_is_released(self):
        page = self.page
        self.assertIn("const evidenceCache=new Map()", page)
        self.assertIn("evidence?item=", _js_function(page, "loadEvidence"))
        prefetch = _js_function(page, "prefetchNext")
        self.assertIn("nextUndecided(from)", prefetch)
        # The same picker as the visible strip, so a scene card prefetches frames of its moments.
        self.assertIn("pickFor(x,ev.frames)", prefetch)
        self.assertIn("pickSceneStrip(frames,momentsOf(x),8):pickStrip(frames,8)", _js_function(page, "pickFor"))
        self.assertIn("URL.revokeObjectURL", _js_function(page, "pruneFrames"))
        self.assertIn("frame?item=", _js_function(page, "frameUrl"))
        self.assertIn('loading="lazy"', _js_function(page, "renderStrip"))
        ensure = _js_function(page, "ensureVideo")
        self.assertIn("video.preload='metadata'", ensure)
        self.assertIn("video?k=", ensure)
        release = _js_function(page, "releaseVideo")
        self.assertIn("video.removeAttribute('src')", release)
        self.assertIn("video.load()", release)
        self.assertIn("window.addEventListener('pagehide',releaseVideo)", page)
        # Video is requested only from user actions (play, frame or timeline click).
        self.assertEqual(page.count("ensureVideo()"), 2)

    def test_writes_use_only_the_existing_review_endpoints(self):
        page = self.page
        kinds = set(re.findall(r"(?:postJson|enqueueWrite)\('([a-z-]+)'", page))
        self.assertEqual(kinds, {"decision", "clear", "bulk-keep", "bulk-accept"})
        self.assertIn("fetch(API+'finalize'", page)
        undo = _js_function(page, "undo")
        self.assertIn("enqueueWrite('clear'", undo)
        self.assertIn("enqueueWrite('decision'", undo)
        decide = _js_function(page, "decide")
        self.assertIn("confirm('Bạn có xác nhận làm mờ toàn bộ khung hình trong đoạn này?')", decide)
        self.assertIn("confidence>=.9", decide)
        self.assertIn("afterLocalChange(id,autoNext&&id===focusId)", decide)

    def test_failed_writes_are_retried_then_name_the_item_and_reopen_it(self):
        # Regression (review fix 1): a 500 while saving lost the decision behind
        # an alert with a raw OS path, after auto-advance had already moved on.
        page = self.page
        self.assertIn("error.status=response.status", _js_function(page, "readJson"))
        enqueue = _js_function(page, "enqueueWrite")
        self.assertIn("postWrite(kind,body)", enqueue)
        self.assertNotIn("postJson(", enqueue)
        self.assertIn("writeFailureMessage(kind,body,error)", enqueue)
        self.assertIn("reopenAfterResync=body.id", enqueue)
        self.assertIn("const WRITE_RETRY_MS=[300,900];", page)
        retry = _js_function(page, "postWrite")
        self.assertIn("!error.status||error.status>=500", retry)
        self.assertIn("attempt>WRITE_RETRY_MS.length", retry)
        message = _js_function(page, "writeFailureMessage")
        for fragment in ("catName(x)", "span(x)", "actionName(x,", "đã thử", "được mở lại",
                         "WinError", "máy chủ chưa ghi được file hàng đợi"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, message)
        resync = _js_function(page, "resync")
        self.assertIn("selectItem(id)", resync)
        self.assertIn("setFilter('all')", resync)

    def test_a_new_media_key_reloads_frames_and_video_instead_of_disabling_them(self):
        # Regression (review fix 2): after a Control Center restart the strip
        # stayed blank and the player was disabled for the whole session.
        page = self.page
        session = _js_function(page, "refreshSession")
        self.assertIn("mediaKey=s.media_key", session)
        self.assertIn("mediaKeyChanged()", session)
        self.assertIn("await refreshSession()", _js_function(page, "postJson"))
        self.assertIn("frameKey(id,t)", _js_function(page, "queueFrames"))
        frame = _js_function(page, "fetchFrame")
        self.assertIn("frameUrl(entry.item,entry.t,used)", frame)
        self.assertIn("r.status===403", frame)
        self.assertIn("await refreshSession()", frame)
        self.assertIn("'retry'", frame)
        self.assertIn("result==='retry'", _js_function(page, "pumpFrames"))
        changed = _js_function(page, "mediaKeyChanged")
        self.assertIn("video.removeAttribute('src')", changed)
        self.assertIn("renderStrip(x,", changed)
        self.assertIn("pstate.srcKey=mediaKey", _js_function(page, "ensureVideo"))
        listener = page.split("video.addEventListener('error',", 1)[1].split("});", 1)[0]
        self.assertIn("videoFailed(used,want,code)", listener)
        self.assertNotIn("available=false", listener)
        failed = _js_function(page, "videoFailed")
        self.assertIn("probeVideo(used)", failed)
        self.assertIn("seekTo(want.t,want.play)", failed)
        for status in ("404:'source_missing'", "409:'source_changed'", "415:'unsupported_container'",
                       "'decode_error'"):
            with self.subTest(status=status):
                self.assertIn(status, failed)
        self.assertEqual(failed.count("pstate.available=false"), 1)
        # A network error with a healthy stream is transient, not a decode failure.
        self.assertIn("(code===3||code===4)?'decode_error'", failed)
        self.assertIn("decode_error:", _js_function(page, "videoReason"))
        # The probe runs only after a user-started load failed.
        self.assertEqual(page.count("probeVideo("), 2)

    def test_undo_does_not_clear_a_promoted_advisory_candidate(self):
        # Regression (review fix 3): /clear left a decided candidate in the
        # required list, so undo made a non-blocking candidate block export.
        page = self.page
        self.assertIn("pushUndo(item,!item.decision&&isAdvisoryItem(item))", _js_function(page, "decide"))
        undo = _js_function(page, "undo")
        barrier = undo.split("if(entry.advisory){", 1)[1].split("const prev=entry.prev;", 1)[0]
        self.assertIn("alert(advisoryUndoMessage(item))", barrier)
        self.assertIn("return;", barrier)
        self.assertNotIn("enqueueWrite", barrier)
        self.assertIn("ứng viên phụ", _js_function(page, "advisoryUndoMessage"))
        self.assertIn("last.advisory", _js_function(page, "updateNavState"))

    def test_space_is_left_to_a_focused_control(self):
        # Regression (review fix 4): Space toggled the player even on a focused
        # summary or button.
        handler = _js_function(self.page, "onKeyDown")
        space = handler.split("k===' '", 1)[1]
        self.assertLess(space.index("spaceActivates(e.target)"), space.index("togglePlay()"))
        activates = _js_function(self.page, "spaceActivates")
        for selector in ("button", "summary", "a[href]", "input", "label"):
            with self.subTest(selector=selector):
                self.assertIn(selector, activates)
        # Mouse clicks (detail > 0) drop button focus so Space keeps playing the video.
        self.assertIn("document.addEventListener('click',e=>{if(!e.detail||!e.target.closest)return;", self.page)


class StandaloneReviewServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        job = self.root / "reports" / "job"
        (job / "thumbs").mkdir(parents=True)
        (job / "thumbs" / "a.jpg").write_bytes(b"\xff\xd8secret-thumb\xff\xd9")
        self.queue_path = job / "review-queue.json"
        self.queue_path.write_text(json.dumps({
            "status": "REVIEW_REQUIRED", "source": {"path": "missing.mp4"},
            "reports": [], "items": [{"id": "a", "category": "adult", "decision": None,
                                      "start_seconds": 1, "end_seconds": 2}],
            "advisory_items": [],
        }), encoding="utf-8")
        created = threading.Event()
        holder = {}

        class CapturingServer(ThreadingHTTPServer):
            daemon_threads = True

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                holder["server"] = self
                created.set()

        self.patches = [
            patch("biliflow.review_workflow.ThreadingHTTPServer", CapturingServer),
            patch("builtins.print"),
        ]
        for value in self.patches:
            value.start()
        self.thread = threading.Thread(target=serve_review_ui, kwargs={
            "project_root": self.root, "queue_path": self.queue_path, "port": 0,
        }, daemon=True)
        self.thread.start()
        self.assertTrue(created.wait(10))
        self.server = holder["server"]
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(10)
        for value in reversed(self.patches):
            value.stop()
        self.temporary.cleanup()

    def request(self, path, *, host, method="GET", headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request(method, path, body=body, headers={"Host": host, **(headers or {})})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_foreign_host_is_refused_like_the_control_center(self):
        local = f"127.0.0.1:{self.port}"
        status, page = self.request("/", host=local)
        self.assertEqual(status, 200)
        self.assertIn(b"const API='/api/';", page)
        token = re.search(rb'let token="([^"]+)";', page).group(1).decode()
        for path in ("/", "/api/session", "/api/queue", "/media/reports/job/thumbs/a.jpg"):
            for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1.evil.example", " "):
                with self.subTest(path=path, host=host):
                    status, body = self.request(path, host=host)
                    self.assertEqual(status, 403)
                    self.assertNotIn(token.encode(), body)
                    self.assertNotIn(b"secret-thumb", body)
        self.assertEqual(self.request("/media/reports/job/thumbs/a.jpg", host=f"localhost:{self.port}")[0], 200)
        before = self.queue_path.read_bytes()
        status, _ = self.request(
            "/api/decision", host="evil.example", method="POST",
            headers={"X-BiliFlow-Token": token, "Content-Type": "application/json"},
            body=json.dumps({"id": "a", "decision": "KEEP"}).encode(),
        )
        self.assertEqual(status, 403)
        self.assertEqual(self.queue_path.read_bytes(), before)

    def test_queue_reads_wait_for_an_in_flight_write(self):
        # Same Windows race as the Control Center: a read holding the queue
        # open made the decision's Path.replace fail with WinError 5.
        queue = json.loads(self.queue_path.read_text(encoding="utf-8"))
        queue["items"][0].update({"priority": "high", "labels": [], "preview_images": []})
        self.queue_path.write_text(json.dumps(queue), encoding="utf-8")
        local = f"127.0.0.1:{self.port}"
        token = re.search(rb'let token="([^"]+)";', self.request("/", host=local)[1]).group(1).decode()
        entered, release = threading.Event(), threading.Event()
        order = []

        def slow_record(**kwargs):
            entered.set()
            release.wait(5)
            order.append("write")
            return record_review_decision(**kwargs)

        def post():
            order.append(("decision", self.request(
                "/api/decision", host=local, method="POST",
                headers={"X-BiliFlow-Token": token, "Content-Type": "application/json"},
                body=json.dumps({"id": "a", "decision": "KEEP"}).encode(),
            )[0]))

        with patch("biliflow.review_workflow.record_review_decision", side_effect=slow_record):
            poster = threading.Thread(target=post)
            poster.start()
            self.assertTrue(entered.wait(5))
            readers = [
                threading.Thread(target=lambda path=path: order.append((path, self.request(path, host=local)[0])))
                for path in ("/api/queue", "/api/export")
            ]
            for thread in readers:
                thread.start()
            time.sleep(0.3)
            self.assertEqual(order, [], "queue reads must wait for the in-flight write")
            release.set()
            for thread in [poster, *readers]:
                thread.join(10)
        self.assertEqual(order[0], "write")
        self.assertEqual(dict(order[1:]), {"decision": 200, "/api/queue": 200, "/api/export": 200})
        self.assertEqual(json.loads(self.queue_path.read_text(encoding="utf-8"))["items"][0]["decision"], "KEEP")

    # ---------------- Control Center guards (batch 3, step B5: open item (a))
    SHA = "ab" * 32
    EDITS = (
        ("/api/decision", {"id": "a", "decision": "BLUR", "full_frame": True}),
        ("/api/clear", {"id": "a"}),
        ("/api/bulk-keep", {"filter": "pending"}),
        ("/api/bulk-accept", {"filter": "pending"}),
    )

    def post_json(self, path, body=None):
        local = f"127.0.0.1:{self.port}"
        token = re.search(rb'let token="([^"]+)";', self.request("/", host=local)[1]).group(1).decode()
        status, payload = self.request(
            path, host=local, method="POST",
            headers={"X-BiliFlow-Token": token, "Content-Type": "application/json"},
            body=json.dumps(body or {}).encode(),
        )
        return status, json.loads(payload or b"{}")

    def ready_queue(self, *, sha=SHA, source_exists=False):
        source = self.root / "input" / "Tập 7.mp4"
        if source_exists:
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"source video")
        queue = json.loads(self.queue_path.read_text(encoding="utf-8"))
        queue.update({"status": "READY_FOR_EDIT_PLAN",
                      "source": {"path": str(source), "sha256": sha, "duration_seconds": 60.0}})
        queue["items"][0].update({"decision": "KEEP", "priority": "high", "labels": [], "preview_images": []})
        self.queue_path.write_text(json.dumps(queue), encoding="utf-8")
        return source

    def control_center_job(self, state, *, sha=SHA, cleanup=None, **fields):
        """A job of the Control Center database in this root, written and closed (never the real DB)."""
        store = JobStore(self.root / "state" / "control-center.sqlite3")
        try:
            source = self.root / "input" / "Tập 7.mp4"
            job = store.upsert_job(
                job_key=f"tap7-{sha[:8]}", source_path=source, source_sha256=sha, source_size_bytes=12,
                source_mtime_ns=1, content_style="animation", state=state,
            )
            if fields:
                store.update_job(job["id"], **fields)
            if cleanup:
                row = store.add_source_cleanup(
                    job_id=job["id"], kind="EXPORTED", source_path=str(source), source_sha256=sha,
                    size_bytes=12, mtime_ns=1,
                )
                if cleanup != "PENDING":
                    store.finish_source_cleanup(row, state=cleanup, verified=True)
            return int(job["id"])
        finally:
            store.close()

    def assert_refused(self, path, body, message):
        before = self.queue_path.read_bytes()
        status, payload = self.post_json(path, body)
        self.assertEqual((status, payload.get("error")), (400, message))
        self.assertEqual(self.queue_path.read_bytes(), before)
        self.assertEqual(sorted(self.root.rglob("*-export-job.json")), [])
        self.assertFalse((self.root / "work").exists())

    def test_standalone_export_refuses_a_missing_source(self):
        self.ready_queue(source_exists=False)
        self.assert_refused("/api/finalize", {"size_mode": "unlimited"}, SOURCE_MISSING_MESSAGE)

    def test_standalone_export_refuses_a_video_the_control_center_owns(self):
        for index, state in enumerate(("READY_TO_EXPORT", "COMPLETED", "SKIPPED")):
            with self.subTest(state=state):
                sha = f"{index + 1:02x}" * 32
                # The queue hash matches whatever its case.
                self.ready_queue(sha=sha.upper(), source_exists=True)
                job_id = self.control_center_job(state, sha=sha)
                message = CONTROL_CENTER_JOB_MESSAGE.format(job_id=job_id, state=state)
                self.assertTrue(message.startswith(f"Video này thuộc job #{job_id} của Control Center"))
                self.assert_refused("/api/finalize", {"size_mode": "default"}, message)

    def test_an_unreadable_control_center_database_fails_closed(self):
        self.ready_queue(source_exists=True)
        database = self.root / "state" / "control-center.sqlite3"
        database.parent.mkdir(parents=True)
        database.write_bytes(b"not a sqlite database " * 64)
        self.assert_refused("/api/finalize", {}, CONTROL_CENTER_STATE_UNREADABLE)
        for path, body in self.EDITS:
            with self.subTest(path=path):
                self.assert_refused(path, body, CONTROL_CENTER_STATE_UNREADABLE)

    def test_standalone_edits_follow_the_control_center_job(self):
        cases = (
            ("waiting export", {"state": "QUEUED", "current_stage": "render"}, REVIEW_EDIT_IN_FLIGHT_MESSAGE),
            ("rendering", {"state": "RENDERING"}, REVIEW_EDIT_IN_FLIGHT_MESSAGE),
            ("skipped", {"state": "SKIPPED"}, STANDALONE_SKIPPED_EDIT_REFUSAL),
            ("recycled", {"state": "COMPLETED", "cleanup": "RECYCLED"}, SOURCE_CLEANED_REVIEW_REFUSAL),
            ("moving", {"state": "COMPLETED", "cleanup": "PENDING"}, SOURCE_CLEANED_REVIEW_REFUSAL),
        )
        for index, (name, fields, message) in enumerate(cases):
            sha = f"{index + 1:02x}" * 32
            self.ready_queue(sha=sha, source_exists=True)
            fields = dict(fields)
            state = fields.pop("state")
            self.control_center_job(state, sha=sha, **fields)
            for path, body in self.EDITS:
                with self.subTest(case=name, path=path):
                    self.assert_refused(path, body, message)
        # A settled job of the Control Center can still be reviewed here (only
        # its export goes through the Dashboard); a FAILED cleanup row does not lock.
        sha = "ee" * 32
        self.ready_queue(sha=sha, source_exists=True)
        self.control_center_job("READY_TO_EXPORT", sha=sha, cleanup="FAILED")
        status, payload = self.post_json("/api/clear", {"id": "a"})
        self.assertEqual(status, 200, payload)
        self.assertIsNone(payload["items"][0]["decision"])
        status, payload = self.post_json("/api/decision", {"id": "a", "decision": "KEEP"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["items"][0]["decision"], "KEEP")


REVISION = "a5ce9eec1ac11773ca9ff44f45b1bb6591631562"


def _verification(nsfw, state="SCORED", revision=REVISION):
    if state != "SCORED":
        return {"state": state, "error": "decode failed", "nsfw_max": None, "nsfw_frames": 0,
                "sample_fps": 2.0, "frame_size": 448, "model": "image_safety_classifier_m",
                "target_label": "NSFW", "revision": revision}
    return {"state": "SCORED", "nsfw_max": nsfw, "nsfw_frames": 4, "nsfw_scores": [nsfw] * 4,
            "sample_fps": 2.0, "frame_size": 448, "model": "image_safety_classifier_m",
            "target_label": "NSFW", "revision": revision}


class AdultTriageTests(unittest.TestCase):
    """docs/ADULT_FALSE_ALARM_PLAN.md steps 1-3: move weak 18+ candidates, never delete."""

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "input").mkdir()
        self.source = self.root / "input" / "source.mp4"
        self.source.write_bytes(b"source")
        self.job = self.root / "reports" / "jobs" / "job"
        self.adult = self.job / "adult"
        self.adult.mkdir(parents=True)
        self.scan = self.adult / "scan.json"
        self.verified = self.adult / "scan-verified.json"
        gore = self.job / "gore" / "scan.json"
        gore.parent.mkdir(parents=True)
        gore.write_text(json.dumps(self._payload("gore", [
            {"start_seconds": 100, "end_seconds": 102, "max_score": 0.8, "sample_count": 1,
             "predicted_label": "NSFL"},
        ])), encoding="utf-8")
        self.gore = gore
        self.queue = self.job / "review-queue.json"

    def tearDown(self):
        self.temporary.cleanup()

    def _payload(self, scan_type, intervals, **extra):
        payload = {
            "status": "COMPLETED", "scan_type": scan_type, "input": str(self.source),
            "input_sha256": "abc", "duration_seconds": 2000, "sample_fps": 2.0,
            "threshold": 0.95 if scan_type == "nsfw" else 0.5, "content_style": "live_action",
            "intervals": intervals,
        }
        payload.update(extra)
        return payload

    @staticmethod
    def _interval(start, seeds, label="porn", verification=None, *, length=2.0, score=0.97):
        interval = {"start_seconds": start, "end_seconds": start + length, "max_score": score,
                    "sample_count": seeds, "predicted_label": label}
        if verification is not None:
            interval["adult_verification"] = verification
        return interval

    def _intervals(self, revision=REVISION):
        def scored(value):
            return _verification(value, revision=revision)

        return [
            self._interval(100, 6, "hentai", scored(0.05)),          # 0 credits
            self._interval(200, 1, "porn", scored(0.30)),            # 1 weak twice
            self._interval(300, 1, "porn", scored(0.92)),            # 2 few seeds, verifier sure
            self._interval(400, 17, "sexy", scored(0.334)),          # 3 implied nudity (blanket)
            self._interval(500, 1, "porn", _verification(None, state="FAILED", revision=revision)),
            self._interval(600, 1, "porn"),                          # 5 never verified
            self._interval(700, 3, "porn", scored(0.40)),            # 6 balanced-only move
            self._interval(800, 1, "porn", scored(0.20), length=6.0, score=0.999),  # 7 would be promoted
            self._interval(900, 1, "porn", scored(0.20)),            # 8 grouped with 9
            self._interval(905, 9, "porn", scored(0.95)),            # 9
        ]

    def _write(self, intervals, **scan_extra):
        plain = [{key: value for key, value in interval.items() if key != "adult_verification"}
                 for interval in intervals]
        self.scan.write_text(json.dumps(self._payload("nsfw", plain, **scan_extra)), encoding="utf-8")
        verified = self._payload("nsfw", intervals, **scan_extra)
        verified["adult_verification"] = {
            "state": "COMPLETED",
            "source_report": "reports/jobs/job/adult/scan.json",
            "source_report_sha256": hashlib.sha256(self.scan.read_bytes()).hexdigest(),
        }
        self.verified.write_text(json.dumps(verified), encoding="utf-8")

    def _build(self, style="live_action", level=None, reports=None):
        return build_review_queue(
            project_root=self.root, report_paths=reports or [self.verified, self.gore],
            queue_path=self.queue, content_style=style, adult_triage_level=level,
        )

    @staticmethod
    def _starts(items, category="adult"):
        return sorted(item["start_seconds"] for item in items if item["category"] == category)

    def test_live_action_moves_only_weak_undecided_candidates_and_keeps_coverage(self):
        self._write(self._intervals())
        queue = self._build()
        self.assertEqual(self._starts(queue["items"]), [300, 400, 500, 600, 700, 900])
        self.assertEqual(self._starts(queue["advisory_items"]), [100, 200, 800])
        moved = {item["start_seconds"]: item for item in queue["advisory_items"] if item["category"] == "adult"}
        for item in moved.values():
            self.assertTrue(item["advisory"])
            self.assertEqual(item["priority"], "context")
            self.assertIsNone(item["suggested_decision"])
            self.assertIsNone(item["decision"])
            self.assertEqual(item["adult_triage"]["outcome"], "advisory")
            self.assertIn("Ứng viên phụ", item["reasons"][-1])
            self.assertIn("không xóa", item["reasons"][-1])
        self.assertEqual(moved[100]["adult_triage"]["rule"], "credits")
        self.assertEqual(moved[100]["adult_triage"]["labels"], ["hentai"])
        weak = moved[200]["adult_triage"]
        self.assertEqual((weak["rule"], weak["n_seeds"], weak["verifier_max"], weak["k"], weak["t"]),
                         ("two_signal", 1, 0.3, 2, 0.7))
        self.assertEqual((weak["model"], weak["revision"], weak["level"]),
                         ("image_safety_classifier_m", REVISION, "conservative"))
        self.assertEqual(moved[800]["priority"], "context")  # moved before strong-scene promotion
        kept = {item["start_seconds"]: item["adult_triage"] for item in queue["items"]
                if item["category"] == "adult"}
        self.assertEqual({start: info["reason"] for start, info in kept.items()}, {
            300: "strong_evidence", 400: "strong_evidence", 500: "verification_failed",
            600: "verification_missing", 700: "strong_evidence", 900: "strong_evidence",
        })
        self.assertTrue(all(info["outcome"] == "kept" for info in kept.values()))
        self.assertEqual(kept[400]["n_seeds"], 17)  # implied nudity under a blanket stays
        self.assertEqual((kept[900]["n_seeds"], kept[900]["verifier_max"]), (10, 0.95))
        audit = queue["adult_triage"]
        self.assertTrue(audit["applied"])
        self.assertEqual((audit["level"], audit["content_style"]), ("conservative", "live_action"))
        self.assertEqual(audit["two_signal"], {"k": 2, "t": 0.7})
        self.assertEqual((audit["evaluated_items"], audit["moved_items"]), (9, 3))
        self.assertEqual(audit["moved_by_rule"], {"credits": 1, "two_signal": 2})
        self.assertEqual(audit["moved_seconds"], 10.0)
        self.assertEqual(audit["kept_by_reason"], {
            "strong_evidence": 4, "verification_failed": 1, "verification_missing": 1,
        })
        self.assertEqual(audit["verification_reports"],
                         {"reports/jobs/job/adult/scan-verified.json": "COMPLETED"})
        coverage = queue["candidate_coverage"]
        self.assertTrue(coverage["complete"])
        self.assertEqual(coverage["missing_refs"], [])
        self.assertEqual(coverage["represented_source_candidate_count"], 11)
        ids = [item["id"] for item in [*queue["items"], *queue["advisory_items"]]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(queue["counts"]["total"], len(queue["items"]))
        self.assertEqual(queue["content_style"], "live_action")

    def test_other_groups_and_the_moved_items_keep_their_identity(self):
        self._write(self._intervals())
        triaged = self._build()
        plain = self._build(style=None)
        self.assertEqual(
            [item for item in triaged["items"] if item["category"] != "adult"],
            [item for item in plain["items"] if item["category"] != "adult"],
        )
        before = {item["id"]: item for item in plain["items"]}
        for item in triaged["advisory_items"]:
            original = before[item["id"]]  # same id in the main list without triage
            for key in ("start_seconds", "end_seconds", "source_candidate_refs", "detected_intervals",
                        "evidence", "preview_images", "labels", "max_score"):
                self.assertEqual(item[key], original[key])

    def test_mixed_animation_and_unknown_jobs_move_nothing(self):
        self._write(self._intervals())
        reference = self._build(style=None)
        self.assertEqual(reference["adult_triage"]["reason"], "content_style_missing")
        for style in ("mixed", "animation", "unknown"):
            with self.subTest(style=style):
                queue = self._build(style=style)
                self.assertEqual(self._starts(queue["advisory_items"]), [])
                self.assertFalse(any("adult_triage" in item for item in queue["items"]))
                self.assertFalse(queue["adult_triage"]["applied"])
                self.assertEqual(queue["adult_triage"]["reason"], "content_style_not_live_action")
                self.assertEqual([item["id"] for item in queue["items"]],
                                 [item["id"] for item in reference["items"]])
        with self.assertRaises(ValueError):
            self._build(style="cartoon")

    def test_levels(self):
        self._write(self._intervals())
        self.assertEqual(self._starts(self._build(level="off")["advisory_items"]), [])
        self.assertEqual(self._build(level="off")["adult_triage"]["reason"], "level_off")
        self.assertEqual(self._starts(self._build(level="credits")["advisory_items"]), [100])
        self.assertEqual(self._starts(self._build(level="balanced")["advisory_items"]), [100, 200, 700, 800])
        self.assertEqual(self._starts(self._build()["advisory_items"]), [100, 200, 800])

    def test_credits_rule_yields_to_a_confident_verifier(self):
        intervals = self._intervals()
        intervals[0] = self._interval(100, 6, "hentai", _verification(0.85))  # "hentai" label, real nudity
        self._write(intervals)
        queue = self._build()
        self.assertNotIn(100, self._starts(queue["advisory_items"]))
        kept = {item["start_seconds"]: item["adult_triage"] for item in queue["items"] if item["category"] == "adult"}
        self.assertEqual((kept[100]["outcome"], kept[100]["reason"]), ("kept", "strong_evidence"))

    def test_unverified_scan_report_moves_only_credits(self):
        self._write(self._intervals())
        queue = self._build(reports=[self.scan, self.gore])
        self.assertEqual(self._starts(queue["advisory_items"]), [100])
        self.assertEqual(queue["adult_triage"]["verification_reports"],
                         {"reports/jobs/job/adult/scan.json": "MISSING"})
        self.assertEqual(queue["adult_triage"]["kept_by_reason"], {"verification_missing": 8})

    def test_uncalibrated_scan_settings_move_nothing(self):
        self._write(self._intervals(), sample_fps=1.0)
        queue = self._build()
        self.assertEqual(self._starts(queue["advisory_items"]), [])
        self.assertEqual(queue["adult_triage"]["kept_by_reason"], {"uncalibrated_scan": 9})

    def test_uncalibrated_verifier_revision_moves_only_credits(self):
        self._write(self._intervals(revision="0" * 40))
        queue = self._build()
        self.assertEqual(self._starts(queue["advisory_items"]), [100])
        reasons = {item["start_seconds"]: item["adult_triage"]["reason"]
                   for item in queue["items"] if item["category"] == "adult"}
        self.assertEqual(reasons[200], "verification_uncalibrated")

    def test_verified_copy_of_a_changed_scan_is_refused(self):
        self._write(self._intervals())
        payload = json.loads(self.scan.read_text(encoding="utf-8"))
        payload["intervals"].pop()
        self.scan.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "stale"):
            self._build()
        self.assertFalse(self.queue.exists())

    def test_decided_items_never_move(self):
        self._write(self._intervals())
        payloads = {"reports/jobs/job/adult/scan-verified.json":
                    json.loads(self.verified.read_text(encoding="utf-8"))}
        weak = {"category": "adult", "start_seconds": 200, "end_seconds": 202,
                "source_candidate_refs": ["reports/jobs/job/adult/scan-verified.json#interval:1"],
                "priority": "context", "reasons": []}
        items = [dict(weak, decision="KEEP"), dict(weak, decision="NEEDS_MORE_CONTEXT"),
                 dict(weak, decision=None)]
        required, advisory, audit = triage_adult_items(items, payloads, "live_action")
        self.assertEqual(required[:2], items[:2])  # untouched, no triage annotation
        self.assertEqual(len(advisory), 1)
        self.assertEqual(audit["kept_by_reason"], {"decided": 2})
        self.assertEqual(audit["evaluated_items"], 1)

    def test_duplicate_references_to_one_interval_count_its_seeds_once(self):
        self._write(self._intervals())
        payload = json.loads(self.verified.read_text(encoding="utf-8"))
        payloads = {"reports/jobs/job/adult/scan-verified.json": payload,
                    "reports/jobs/job/adult/scan.json": payload}
        item = {"category": "adult", "start_seconds": 200, "end_seconds": 202, "decision": None,
                "source_candidate_refs": ["reports/jobs/job/adult/scan-verified.json#interval:1",
                                          "reports/jobs/job/adult/scan.json#interval:1"]}
        _required, advisory, audit = triage_adult_items([item], payloads, "live_action")
        self.assertEqual(advisory[0]["adult_triage"]["n_seeds"], 1)
        self.assertEqual(list(audit["verification_reports"]),
                         ["reports/jobs/job/adult/scan-verified.json"])

    def test_references_that_do_not_resolve_keep_the_item(self):
        self._write(self._intervals())
        payloads = {
            "reports/jobs/job/adult/scan-verified.json": json.loads(self.verified.read_text(encoding="utf-8")),
            "reports/jobs/job/gore/scan.json": json.loads(self.gore.read_text(encoding="utf-8")),
        }
        base = {"category": "adult", "start_seconds": 200, "end_seconds": 202, "decision": None}
        cases = {
            "no_source_refs": [],
            "unresolved_source_refs": ["reports/jobs/old/adult/scan.json#interval:1"],
            "not_live_action_nsfw_scan": ["reports/jobs/job/gore/scan.json#interval:0"],
            "stale_source_refs": ["reports/jobs/job/adult/scan-verified.json#interval:5"],
        }
        for reason, refs in cases.items():
            with self.subTest(reason=reason):
                required, advisory, _audit = triage_adult_items(
                    [dict(base, source_candidate_refs=refs)], payloads, "live_action")
                self.assertEqual(advisory, [])
                self.assertEqual(required[0]["adult_triage"]["reason"], reason)

    def test_preserved_unresolved_items_are_triaged_again(self):
        self._write(self._intervals())
        self.queue.write_text(json.dumps({
            "source": {"path": str(self.source.resolve()), "sha256": "abc"},
            "items": [
                {   # from an older revision whose report is gone: stays required
                    "id": "review-old-unresolvable", "category": "adult", "candidate_type": None,
                    "start_seconds": 1500, "end_seconds": 1502, "priority": "context", "decision": None,
                    "labels": ["porn"], "reasons": [], "evidence": ["reports/jobs/old/adult/scan.json"],
                    "preview_images": [], "detected_intervals": [{"start_seconds": 1500, "end_seconds": 1502}],
                    "source_candidate_refs": ["reports/jobs/old/adult/scan.json#interval:0"],
                },
                {   # cites the unverified scan.json of this revision: resolved through the verified copy
                    "id": "review-old-weak", "category": "adult", "candidate_type": None,
                    "start_seconds": 202.5, "end_seconds": 203.0, "priority": "context", "decision": None,
                    "labels": ["porn"], "reasons": [], "evidence": ["reports/jobs/job/adult/scan.json"],
                    "preview_images": [], "detected_intervals": [{"start_seconds": 202.5, "end_seconds": 203.0}],
                    "source_candidate_refs": ["reports/jobs/job/adult/scan.json#interval:1"],
                },
            ],
        }), encoding="utf-8")
        queue = self._build()
        kept = next(item for item in queue["items"] if item["start_seconds"] == 1500)
        self.assertEqual(kept["migration_status"], "preserved_unresolved_from_previous_queue")
        self.assertEqual(kept["adult_triage"]["reason"], "unresolved_source_refs")
        weak = next(item for item in queue["advisory_items"] if item["start_seconds"] == 200)
        self.assertEqual(sorted(weak["source_candidate_refs"]), [
            "reports/jobs/job/adult/scan-verified.json#interval:1",
            "reports/jobs/job/adult/scan.json#interval:1",
        ])
        self.assertEqual((weak["adult_triage"]["rule"], weak["adult_triage"]["n_seeds"]), ("two_signal", 1))
        self.assertTrue(queue["candidate_coverage"]["reference_complete"])


class DetectionScopeTests(unittest.TestCase):
    def test_queue_records_selected_and_skipped_detector_groups(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input" / "video.mp4"
            source.parent.mkdir()
            source.write_bytes(b"video")
            report = root / "reports" / "job" / "text-scan.json"
            report.parent.mkdir(parents=True)
            report.write_text(json.dumps({
                "status": "COMPLETED",
                "input": str(source),
                "input_sha256": "a" * 64,
                "duration_seconds": 10,
                "tracks": [],
            }), encoding="utf-8")
            queue = build_review_queue(
                project_root=root,
                report_paths=[report],
                queue_path=root / "reports" / "job" / "review-queue.json",
                selected_detectors=["advertising"],
            )
            self.assertEqual(
                queue["detection_scope"]["selected"], ["advertising"]
            )
            self.assertEqual(
                queue["detection_scope"]["skipped"],
                ["adult", "gore", "violence"],
            )
            self.assertFalse(queue["detection_scope"]["all_selected"])



# Fake DOM for finalizeExport: only the elements it and the notice touch.
EXPORT_PANEL_HARNESS = r"""
const els={
  '#export-section':{open:true,querySelector(){return {focus(){}}}},
  '#export-notice':{hidden:true,className:'export-notice',textContent:''},
  '#finalize':{disabled:false,focus(){}},
  '#export-status':{textContent:'',error:false,classList:{toggle(name,on){els['#export-status'].error=!!on}}},
};
const $=s=>els[s];
const API='/api/';let token='t';let writeChain=Promise.resolve();
let queue={status:'READY_FOR_EDIT_PLAN'};let exportJob={status:'READY_TO_EXPORT'};
let exportRequestInFlight=false;let exportNoticeActive=false;let exportError='';
let selectionError=null;let confirmAnswer=true;const log={fetches:[],confirms:[],alerts:[],openAtFetch:[]};
let reply=()=>({ok:true,status:200,json:async()=>({status:'QUEUED',export_size_policy:{mode:'default'}})});
function outputSizeSelection(){if(selectionError)throw new Error(selectionError);return{size_mode:'default',description:'tối đa 3,5 GB'};}
function renderExport(){$('#finalize').disabled=exportRequestInFlight;setText($('#export-status'),exportError||'ready');$('#export-status').classList.toggle('error',!!exportError);}
const confirm=m=>{log.confirms.push(m);return confirmAnswer;};const alert=m=>log.alerts.push(m);
async function fetch(url,opt){log.fetches.push([url,JSON.parse(opt.body)]);log.openAtFetch.push(els['#export-section'].open);await new Promise(r=>setTimeout(r,20));return reply();}
function reset(){els['#export-section'].open=true;Object.assign(els['#export-notice'],{hidden:true,className:'export-notice',textContent:''});els['#export-status'].textContent='';els['#export-status'].error=false;
  exportJob={status:'READY_TO_EXPORT'};exportNoticeActive=false;exportError='';selectionError=null;confirmAnswer=true;log.fetches=[];log.confirms=[];log.alerts=[];log.openAtFetch=[];
  reply=()=>({ok:true,status:200,json:async()=>({status:'QUEUED',export_size_policy:{mode:'default'}})});}
function snap(){return{open:els['#export-section'].open,notice:els['#export-notice'].hidden?null:els['#export-notice'].textContent,tone:els['#export-notice'].className,
  status:els['#export-status'].textContent,statusError:els['#export-status'].error,fetches:log.fetches.length,confirms:log.confirms.length,alerts:log.alerts.length,
  openAtFetch:log.openAtFetch,disabled:els['#finalize'].disabled,inFlight:exportRequestInFlight};}
(async()=>{const out={};
  reset();await finalizeExport();out.ok=snap();out.ok_body=log.fetches[0];out.ok_confirm=log.confirms[0];
  reset();confirmAnswer=false;await finalizeExport();out.cancel=snap();
  reset();reply=()=>({ok:false,status:400,json:async()=>({error:'X'})});await finalizeExport();out.refused=snap();
  reset();reply=()=>{throw new TypeError('Failed to fetch')};await finalizeExport();out.offline=snap();
  reset();const first=finalizeExport(),second=finalizeExport();out.busy=snap();await Promise.all([first,second]);out.double=snap();
  reset();selectionError='Giới hạn tùy chỉnh phải từ 0,05 đến 1.000 GB.';await finalizeExport();out.invalid=snap();
  reset();queue.status='REVIEW_REQUIRED';await finalizeExport();out.unresolved=snap();queue.status='READY_FOR_EDIT_PLAN';
  // A render that fails later only changes the header notice; the panel stays closed.
  reset();await finalizeExport();exportJob={status:'FAILED',error:'boom'};updateExportNotice();out.later_failure=snap();
  out.progress=[exportNoticeText({status:'RENDERING',render_progress:{percent:42.34,eta_seconds:150}}),
    exportNoticeText({status:'RENDERING',render_progress:{state:'VERIFYING',percent:100}}),
    exportNoticeText({status:'RENDERING'}),exportNoticeText({status:'COMPLETED',output:'output/a.mp4'}),exportNoticeText({status:'IDLE'})];
  // Opening a page while an export is queued shows its progress without a click.
  reset();exportJob={status:'QUEUED'};updateExportNotice();out.on_load=snap();
  console.log(JSON.stringify(out));
})().catch(e=>{console.error(e&&e.stack||e);process.exit(1);});
"""


class ExportPanelTests(unittest.TestCase):
    def setUp(self):
        self.page = _interactive_html("token")

    def test_export_notice_markup_and_finalize_order(self):
        page = self.page
        self.assertIn(
            '</details><span class="export-notice" id="export-notice" role="status" aria-live="polite" hidden></span>',
            page,
        )
        finalize = _js_function(page, "finalizeExport")
        self.assertTrue(finalize.startswith(
            "async function finalizeExport(){if(exportRequestInFlight)return;exportRequestInFlight=true;"
        ))
        # The panel closes right after OK and before the request is sent.
        self.assertLess(finalize.index("confirm("), finalize.index("panel.open=false"))
        self.assertLess(finalize.index("panel.open=false"), finalize.index("fetch(API+'finalize'"))
        catch = finalize[finalize.index("catch(e){exportNoticeActive=false;"):]
        self.assertIn("exportError=`Không gửi được lệnh xuất: ${e.message}`", catch)
        self.assertIn("panel.open=true", catch)
        self.assertNotIn("alert(e.message)", finalize)
        self.assertIn("finally{exportRequestInFlight=false;renderExport();}", finalize)
        render = _js_function(page, "renderExport")
        self.assertIn("$('#finalize').disabled=!ready||exportRequestInFlight;", render)
        self.assertIn(
            "setText($('#export-status'),exportError||(cleaned?cleanedText:exportText[exportJob.status])"
            "||pendingDescription())", render,
        )
        # Polling updates only the notice; nothing in it reopens the panel.
        self.assertNotIn("open", _js_function(page, "updateExportNotice"))
        self.assertIn("renderExport();updateExportNotice();}catch(_error){}}},3000);", page)
        # On desktop the notice stays on the header line (ellipsis, full text in title);
        # the mobile header is static and lets it wrap below.
        self.assertIn("max-width:34ch;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}", page)
        self.assertIn(".export-notice{order:4;flex-basis:100%;max-width:none;white-space:normal;", page)
        self.assertIn("if(el.title!==full)el.title=full;", _js_function(page, "showExportNotice"))

    def test_decisions_are_locked_while_the_export_waits_or_runs(self):
        from biliflow.control_center import REVIEW_EDIT_IN_FLIGHT_MESSAGE

        page = self.page
        self.assertIn(f"const EXPORT_LOCK_MESSAGE='{REVIEW_EDIT_IN_FLIGHT_MESSAGE}';", page)
        for name in ("decide", "clearDecision", "undo", "bulkKeep", "bulkAccept"):
            with self.subTest(function=name):
                body = _js_function(page, name)
                guard = body.index("refuseWhileExporting()")
                # Checked before anything changes locally or is sent.
                for write in ("enqueueWrite(", "applyLocal", "postJson(", "confirm("):
                    if write in body:
                        self.assertLess(guard, body.index(write), write)
        render = _js_function(page, "renderExport")
        # One lock for an export that waits or runs and for a source moved to the Recycle Bin.
        self.assertIn("cleaned=!!exportJob.source_cleaned,locked=active||cleaned,", render)
        self.assertIn("document.body.classList.toggle('export-locked',locked);", render)
        self.assertIn(
            "for(const b of document.querySelectorAll('.list-foot button')){b.disabled=locked;"
            "b.title=locked?(cleaned?SOURCE_CLEANED_LOCK_MESSAGE:EXPORT_LOCK_MESSAGE):'';}", render,
        )
        self.assertIn("body.export-locked .decide button", page)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_export_lock_follows_the_export_status_in_node(self):
        script = (
            "let exportJob={status:'IDLE'};const alerts=[];globalThis.alert=m=>alerts.push(m);"
            + re.search(r"const EXPORT_LOCK_MESSAGE='[^']*';", self.page).group(0)
            + re.search(r"const SOURCE_CLEANED_LOCK_MESSAGE='[^']*';", self.page).group(0)
            + _js_function(self.page, "decisionsLocked") + _js_function(self.page, "refuseWhileExporting")
            + "const out={};for(const s of ['IDLE','READY_TO_EXPORT','QUEUED','RENDERING','COMPLETED','FAILED','SKIPPED'])"
            "{exportJob={status:s};out[s]=refuseWhileExporting();}"
            # A source moved to the Recycle Bin locks every decision, whatever the export status.
            "for(const s of ['COMPLETED','SKIPPED']){exportJob={status:s,source_cleaned:true};out['cleaned_'+s]=refuseWhileExporting();}"
            "exportJob={status:'COMPLETED',source_cleaned:false};out.restored=refuseWhileExporting();"
            "out.alerts=alerts;console.log(JSON.stringify(out));"
        )
        result = subprocess.run([shutil.which("node"), "-e", script], capture_output=True, text=True,
                                encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual({key: value for key, value in out.items() if value is True},
                         {"QUEUED": True, "RENDERING": True, "cleaned_COMPLETED": True, "cleaned_SKIPPED": True})
        self.assertFalse(out["restored"])
        self.assertEqual(out["alerts"], [
            "Video đang chờ xuất hoặc đang xuất; hủy lệnh xuất trước khi đổi quyết định."] * 2 + [
            "Video gốc đã được dọn vào Thùng rác; trang duyệt chỉ để xem. Chép lại video gốc vào input để sửa "
            "quyết định hoặc xuất lại."] * 2)

    def test_skipped_video_cannot_be_exported_from_the_review_page(self):
        render = _js_function(self.page, "renderExport")
        self.assertIn(
            "skippedExport=exportJob.status==='SKIPPED',cleaned=!!exportJob.source_cleaned,locked=active||cleaned,"
            "ready=status==='READY_FOR_EDIT_PLAN'&&!active&&!skippedExport&&!cleaned;",
            render,
        )
        self.assertIn(
            "SKIPPED:'Video đã được đánh dấu bỏ qua (không xuất). Bấm “Mở lại để xuất” ở Dashboard nếu muốn xuất video.'",
            render,
        )
        self.assertIn("skippedExport?'đã bỏ qua'", render)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_cleaned_source_review_page_is_read_only_in_node(self):
        page = self.page
        script = (
            "let exportRequestInFlight=false,exportError='',resources=null,exportJob={status:'IDLE'};"
            "let queue={status:'READY_FOR_EDIT_PLAN',items:[{decision:'KEEP',category:'text'}],advisory_items:[],"
            "source:{duration_seconds:100}};const alerts=[];globalThis.alert=m=>alerts.push(m);"
            "const mk=()=>({textContent:'',disabled:false,title:'',classList:{set:new Set(),"
            "toggle(c,on){if(on)this.set.add(c);else this.set.delete(c)},contains(c){return this.set.has(c)}}});"
            "const els={'#summary':mk(),'#resources':mk(),'#finalize':mk(),'#export-status':mk(),"
            "'#export-summary':mk(),'#export-section':mk()};const $=s=>els[s];const foot=[mk(),mk()],body=mk();"
            "globalThis.document={body,querySelectorAll:s=>s==='.list-foot button'?foot:[]};"
            + re.search(r"const EXPORT_LOCK_MESSAGE='[^']*';", page).group(0)
            + re.search(r"const SOURCE_CLEANED_LOCK_MESSAGE='[^']*';", page).group(0)
            + "".join(_js_function(page, name) for name in (
                "formatStamp", "countsFrom", "trackCoversFullVideo", "pendingDescription", "setText", "setHtml",
                "renderExport", "decisionsLocked", "refuseWhileExporting"))
            + "const out={};const snap=()=>({finalize:els['#finalize'].disabled,locked:body.classList.contains('export-locked'),"
            "foot:foot.map(b=>[b.disabled,b.title]),status:els['#export-status'].textContent,"
            "summary:els['#export-summary'].textContent,ready:els['#export-section'].classList.contains('ready')});"
            "const jobs={ready:{status:'READY_TO_EXPORT'},completed:{status:'COMPLETED',output:'output/a.mp4'},"
            "cleaned:{status:'COMPLETED',output:'output/a.mp4',source_cleaned:true,source_name:'Tập 11.mp4',"
            "source_cleanup:{state:'RECYCLED',finished_at:'2026-10-03T08:00:04+07:00'}},"
            "cleaned_skipped:{status:'SKIPPED',source_cleaned:true,source_name:'Tập 30.mp4',"
            "source_cleanup:{state:'PENDING',finished_at:null}},queued:{status:'QUEUED'}};"
            "for(const [key,job] of Object.entries(jobs)){exportJob=job;renderExport();out[key]=snap();"
            "out[key].refused=refuseWhileExporting();}"
            "exportJob=jobs.cleaned;exportError='Không gửi được lệnh xuất: X';renderExport();"
            "out.error_first=els['#export-status'].textContent;out.alerts=alerts;console.log(JSON.stringify(out));"
        )
        result = subprocess.run([shutil.which("node"), "-e", script], capture_output=True, text=True,
                                encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout.strip().splitlines()[-1])
        export_lock = "Video đang chờ xuất hoặc đang xuất; hủy lệnh xuất trước khi đổi quyết định."
        cleaned_lock = ("Video gốc đã được dọn vào Thùng rác; trang duyệt chỉ để xem. Chép lại video gốc vào "
                        "input để sửa quyết định hoặc xuất lại.")
        self.assertEqual(out["ready"], {"finalize": False, "locked": False, "foot": [[False, ""]] * 2,
                                        "status": "Đã duyệt đủ. Bạn có thể xuất video.", "summary": "sẵn sàng",
                                        "ready": True, "refused": False})
        self.assertEqual((out["completed"]["finalize"], out["completed"]["locked"], out["completed"]["status"],
                          out["completed"]["summary"]), (False, False, "Hoàn tất: output/a.mp4", "đã xuất"))
        cleaned = out["cleaned"]
        self.assertEqual((cleaned["finalize"], cleaned["locked"], cleaned["ready"], cleaned["refused"]),
                         (True, True, False, True))
        self.assertEqual(cleaned["foot"], [[True, cleaned_lock]] * 2)
        self.assertEqual(cleaned["summary"], "đã dọn video gốc")
        self.assertTrue(cleaned["status"].startswith(
            "Hoàn tất: output/a.mp4. Video gốc đã được dọn vào Thùng rác lúc "), cleaned["status"])
        self.assertTrue(cleaned["status"].endswith(
            ". Chép lại đúng tên “Tập 11.mp4” vào input để xuất lại hoặc sửa quyết định."), cleaned["status"])
        skipped = out["cleaned_skipped"]
        self.assertEqual(skipped["status"], "Video gốc đã được dọn vào Thùng rác. Chép lại đúng tên “Tập 30.mp4” "
                                            "vào input để xuất lại hoặc sửa quyết định.")
        self.assertEqual((skipped["finalize"], skipped["locked"], skipped["summary"]), (True, True, "đã dọn video gốc"))
        queued = out["queued"]
        self.assertEqual((queued["locked"], queued["foot"], queued["summary"]),
                         (True, [[True, export_lock]] * 2, "đang xuất…"))
        # A request error is still shown first.
        self.assertEqual(out["error_first"], "Không gửi được lệnh xuất: X")
        self.assertEqual(out["alerts"], [cleaned_lock, cleaned_lock, export_lock])

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_cleaned_source_uses_stored_previews_in_node(self):
        page = self.page
        script = (
            "const SAFETY={};let mediaKey='k';"
            + "".join(_js_function(page, name) for name in (
                "videoReason", "stripFrames", "pickFor", "pickStrip", "pickSceneStrip", "momentsOf", "thumbTime",
                "isScene", "isSafety", "momentIndex"))
            + "const x={category:'text',preview_images:['reports/a/x-12.5s.jpg','reports/a/x-20s.jpg']};"
            "const frames=[{t:1,kind:'seed'},{t:2,kind:'strongest'}];"
            "const out={reason:videoReason({reason:'source_cleaned'}),"
            "cleaned:stripFrames(x,{frames,video:{available:false,mime:null,reason:'source_cleaned'}}),"
            "missing:stripFrames(x,{frames,video:{available:false,reason:'source_missing'}}),"
            "live:stripFrames(x,{frames,video:{available:true}}),"
            "changed:stripFrames(x,{frames,video:{available:false,reason:'source_changed'}})};"
            "console.log(JSON.stringify(out));"
        )
        result = subprocess.run([shutil.which("node"), "-e", script], capture_output=True, text=True,
                                encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(out["reason"], "Video gốc đã được dọn vào Thùng rác; chỉ xem được ảnh đã lưu trong report.")
        stored = [
            {"t": 12.5, "kind": "preview", "src": "/media/reports%2Fa%2Fx-12.5s.jpg"},
            {"t": 20, "kind": "preview", "src": "/media/reports%2Fa%2Fx-20s.jpg"},
        ]
        # The frame route answers 410 for a cleaned source (404 when it is missing): use the stored previews.
        self.assertEqual(out["cleaned"], stored)
        self.assertEqual(out["missing"], stored)
        remote = [{"t": 1, "kind": "seed", "remote": True}, {"t": 2, "kind": "strongest", "remote": True}]
        self.assertEqual(out["live"], remote)
        self.assertEqual(out["changed"], remote)

    def test_video_failure_410_means_the_source_was_cleaned(self):
        failed = _js_function(self.page, "videoFailed")
        self.assertIn("{404:'source_missing',409:'source_changed',410:'source_cleaned',415:'unsupported_container'}",
                      failed)
        self.assertIn(
            "const SOURCE_CLEANED_LOCK_MESSAGE='Video gốc đã được dọn vào Thùng rác; trang duyệt chỉ để xem. "
            "Chép lại video gốc vào input để sửa quyết định hoặc xuất lại.';", self.page,
        )
        self.assertIn("function decisionsLocked(){return ['QUEUED','RENDERING'].includes(exportJob.status)"
                      "||!!exportJob.source_cleaned;}", self.page)
        render = _js_function(self.page, "renderExport")
        self.assertIn("active?'đang xuất…':cleaned?'đã dọn video gốc':skippedExport?'đã bỏ qua'", render)
        self.assertIn("formatStamp(exportJob.source_cleanup?.finished_at)", render)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_the_done_note_does_not_offer_an_export_after_cleanup_in_node(self):
        source = "let exportJob={status:'IDLE'};" + "\n".join(
            _js_function(self.page, name) for name in ("reviewDoneHint", "nextNote"))
        source += (
            "\nconst ready=nextNote({pending:0});exportJob={status:'COMPLETED',source_cleaned:true};"
            "console.log(JSON.stringify([ready,nextNote({pending:0})]));"
        )
        result = subprocess.run([shutil.which("node"), "-e", source],
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1]), [
            "Đã duyệt đủ mọi mục chính. Bấm “Xuất video” ở trên để xuất.",
            "Đã duyệt đủ mọi mục chính. Video gốc đã được dọn vào Thùng rác; trang duyệt chỉ để xem.",
        ])
        focus = _js_function(self.page, "renderFocus")
        self.assertIn("`Đã duyệt đủ ${queue.items.length} mục chính. ${reviewDoneHint()}`", focus)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_finalize_export_closes_reports_and_reopens_in_node(self):
        # finalizeExport uses the shared confirm text and gate (export_dialog.py).
        source = EXPORT_DIALOG_JS + "\n".join(_js_function(self.page, name) for name in (
            "setText", "readJson", "offlineError", "showExportNotice", "exportNoticeText",
            "updateExportNotice", "finalizeExport"))
        result = subprocess.run(
            [shutil.which("node"), "-e", EXPORT_PANEL_HARNESS.replace("const $=s=>els[s];", "const $=s=>els[s];" + source)],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout.strip().splitlines()[-1])
        ok = out["ok"]
        self.assertEqual(out["ok_confirm"],
                         "Khóa các lựa chọn hiện tại và bắt đầu xuất video hoàn chỉnh (tối đa 3,5 GB)?")
        self.assertEqual(out["ok_body"], ["/api/finalize", {"size_mode": "default", "description": "tối đa 3,5 GB"}])
        self.assertEqual(ok["openAtFetch"], [False])
        self.assertFalse(ok["open"])
        self.assertEqual((ok["notice"], ok["tone"]), ("Đã xếp hàng xuất video.", "export-notice running"))
        self.assertEqual((ok["fetches"], ok["alerts"], ok["inFlight"], ok["disabled"]), (1, 0, False, False))
        cancel = out["cancel"]
        self.assertEqual((cancel["open"], cancel["fetches"], cancel["notice"]), (True, 0, None))
        for key, message in (("refused", "Không gửi được lệnh xuất: X"),
                             ("offline", "Không gửi được lệnh xuất: Mất kết nối với Review.")):
            value = out[key]
            self.assertTrue(value["open"], key)
            self.assertEqual(value["openAtFetch"], [False], key)
            self.assertTrue(value["status"].startswith(message), key)
            self.assertTrue(value["statusError"], key)
            self.assertEqual((value["notice"], value["tone"]), (value["status"], "export-notice error"))
            self.assertEqual(value["alerts"], 0, key)
        # A double click sends one request and asks once; the button is disabled meanwhile.
        self.assertTrue(out["busy"]["inFlight"])
        self.assertEqual((out["double"]["fetches"], out["double"]["confirms"]), (1, 1))
        self.assertFalse(out["double"]["inFlight"])
        invalid = out["invalid"]
        self.assertEqual((invalid["open"], invalid["confirms"], invalid["fetches"]), (True, 0, 0))
        self.assertEqual(invalid["status"], "Giới hạn tùy chỉnh phải từ 0,05 đến 1.000 GB.")
        self.assertTrue(invalid["statusError"])
        unresolved = out["unresolved"]
        self.assertEqual((unresolved["alerts"], unresolved["confirms"], unresolved["open"]), (1, 0, True))
        later = out["later_failure"]
        self.assertFalse(later["open"])
        self.assertEqual((later["notice"], later["tone"]), ("Xuất video thất bại: boom", "export-notice error"))
        self.assertEqual(out["progress"], [
            ["Đang xuất video: 42,3% · còn khoảng 3 phút", "running"],
            ["Đang kiểm tra video đã xuất…", "running"],
            ["Đang xuất video…", "running"],
            # The one-line header shows the file name; the full path is in the tooltip.
            ["Đã xuất xong: a.mp4", "ok", "Đã xuất xong: output/a.mp4"],
            None,
        ])
        self.assertEqual(out["on_load"]["notice"], "Đã xếp hàng xuất video.")


if __name__ == "__main__":
    unittest.main()
