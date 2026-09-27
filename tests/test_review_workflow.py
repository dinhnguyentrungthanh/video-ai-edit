import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

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
    pixel_region_iou,
    refine_persistent_logo_regions,
    tighten_text_region,
    union_pixel_regions,
)


class VisualAuditQueueTests(unittest.TestCase):
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
        self.assertIn("NHÓM SỰ KIỆN", page)
        self.assertIn("các khoảng trống giữa chúng không bị cắt hoặc làm mờ", page)
        self.assertIn("Ứng viên kiểm tra thêm — chưa thuộc quyết định chính", page)
        self.assertIn("Logo ở vị trí hoặc track khác vẫn cần quyết định riêng", page)
        self.assertIn('id="output-size-mode"', page)
        self.assertIn("Tối đa 3,5 GB (mặc định)", page)
        self.assertIn("Giới hạn tùy chỉnh", page)
        self.assertIn("Không giới hạn dung lượng", page)
        self.assertIn("function outputSizeSelection()", page)
        self.assertIn("body:JSON.stringify(selection)", page)

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
                {"start_seconds": 15, "end_seconds": 16, "max_score": 0.4, "priority": "context"},
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


if __name__ == "__main__":
    unittest.main()


