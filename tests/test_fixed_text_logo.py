"""A fixed logo beside a fixed text line that reads the same name (job 145, session D).

Shapes measured on job 145 (source 1280 x 536, analysis 960 x 402): the site's
scrolling line runs along the top at about y 21-57 and the site's name is a fixed logo
just under it at the right, OCR box [863, 53, 1149, 86]. The text scan's film-long
fixed-overlay track read the logo but kept the line's box [432, 18, 960, 43]; the
visual-logo scan read the name in the logo's own box at 175-180 s only.
``EXAMPLE.COM`` stands for the site.
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.fixed_text_logo import join_fixed_text_logos
from biliflow.review_workflow import (
    _generic_label, approved_operations, build_review_queue, reconcile_persistent_overlay_items,
    record_review_decision,
)

DURATION = 2561.172
LOGO = {"x": 857, "y": 47, "width": 298, "height": 45}
LINE = {"x": 0, "y": 21, "width": 1280, "height": 32}


def _text_card(**extra):
    card = {
        "id": "review-text", "category": "text", "candidate_type": "persistent_overlay",
        "start_seconds": 0.0, "end_seconds": DURATION,
        "detected_intervals": [{"start_seconds": 0.0, "end_seconds": DURATION}],
        "labels": ["EXAMPLE.COM", "EXAMPIE.COM", "EXAMPLE com",
                   "Phim cập nhật nhanh nhất tại EXAMPLE.COM. Phim chỉ đăng duy nhất trên web"],
        "reasons": ["Chữ/logo cố định"], "suggested_region_source_pixels": dict(LINE),
        "suggested_decision": "BLUR", "decision": None,
    }
    card.update(extra)
    return card


def _logo_card(**extra):
    card = {
        "id": "review-logo", "category": "visual_logo", "candidate_type": None,
        "start_seconds": 175.0, "end_seconds": 180.0,
        "detected_intervals": [{"start_seconds": 175.0, "end_seconds": 180.0}],
        "labels": ["Visual brand/logo candidate", "promotional banner", "</s>EXAMPLE.COM"],
        "reasons": ["Visual-language answer was uncertain; human review required"],
        "suggested_region_source_pixels": dict(LOGO),
        "model_evidence": {"region_sources": ["grounding", "ocr"]},
        "region_classification": "external_brand_candidate", "suggested_decision": "BLUR",
        "decision": None,
    }
    card.update(extra)
    return card


def _join(items):
    return join_fixed_text_logos(items, generic_label=_generic_label)


class JoinFixedTextLogoTests(unittest.TestCase):
    def test_the_line_card_covers_the_logo_and_the_logo_card_takes_its_time(self):
        text, logo = _text_card(), _logo_card()
        joined_text, joined_logo = _join([text, logo])
        self.assertEqual(joined_text["suggested_region_source_pixels"],
                         {"x": 0, "y": 21, "width": 1280, "height": 71})
        self.assertEqual(joined_text["fixed_text_logo"]["previous_region"], LINE)
        self.assertEqual(joined_text["fixed_text_logo"]["logo_regions"], [LOGO])
        self.assertIn("logo", joined_text["reasons"][-1])
        self.assertEqual((joined_logo["start_seconds"], joined_logo["end_seconds"]), (0.0, DURATION))
        self.assertEqual(joined_logo["detected_intervals"], [{"start_seconds": 0.0, "end_seconds": DURATION}])
        self.assertEqual(joined_logo["suggested_region_source_pixels"], LOGO, "the logo keeps its own box")
        self.assertEqual(joined_logo["fixed_text_logo"]["previous_interval"],
                         {"start_seconds": 175.0, "end_seconds": 180.0})
        self.assertEqual(text, _text_card(), "the inputs are not modified")
        self.assertEqual(logo, _logo_card())

    def test_scanner_labels_and_grounding_prompts_are_no_reading(self):
        items = [_text_card(labels=["EXAMPLE.COM", "Visual brand/logo candidate", "a company logo"]),
                 _logo_card(labels=["Visual brand/logo candidate", "a company logo", "watermark"])]
        self.assertIs(_join(items), items)

    def test_a_name_inside_a_longer_line_is_not_the_same_text(self):
        items = [_text_card(labels=["Phim cập nhật nhanh nhất tại EXAMPLE.COM. Phim chỉ đăng duy nhất"]),
                 _logo_card()]
        self.assertIs(_join(items), items)

    def test_another_name_a_far_box_or_no_ocr_reading_changes_nothing(self):
        for logo in (
            _logo_card(labels=["</s>ANOTHER-EXAMPLE.ORG"]),
            _logo_card(suggested_region_source_pixels={"x": 857, "y": 140, "width": 298, "height": 45}),
            _logo_card(suggested_region_source_pixels={"x": 857, "y": 47, "width": 298, "height": 120}),
            _logo_card(model_evidence={"region_sources": ["grounding"]}),
        ):
            items = [_text_card(), logo]
            self.assertIs(_join(items), items)

    def test_a_logo_already_inside_the_line_cards_box_or_an_end_card_is_left_alone(self):
        for logo in (
            _logo_card(suggested_region_source_pixels={"x": 857, "y": 23, "width": 298, "height": 28}),
            _logo_card(candidate_type="branded_end_card"),
            _logo_card(review_kind="title_overlay"),
        ):
            items = [_text_card(), logo]
            self.assertIs(_join(items), items)

    def test_only_a_blur_line_card_and_a_plain_logo_card_not_kept_take_part(self):
        for items in (
            [_text_card(suggested_decision="KEEP"), _logo_card()],
            [_text_card(suggested_decision=None), _logo_card()],
            [_text_card(), _logo_card(suggested_decision="KEEP")],
            [_text_card(), _logo_card(candidate_type="persistent_overlay")],
        ):
            self.assertIs(_join(items), items)

    def test_the_reconciliation_keeps_the_line_blur_with_a_persistent_logo_card(self):
        # A persistent visual logo owns its watermark in the reconciliation; joined to the
        # line's box it would absorb the line card and blur only the logo (session D review).
        items = _join([_text_card(), _logo_card(candidate_type="persistent_overlay")])
        cards = reconcile_persistent_overlay_items(items, source_duration=DURATION)
        self.assertTrue(any(card["suggested_region_source_pixels"] == LINE for card in cards))

    def test_a_logo_outside_the_line_cards_time_is_left_alone(self):
        items = [_text_card(end_seconds=100.0, detected_intervals=[{"start_seconds": 0.0, "end_seconds": 100.0}]),
                 _logo_card()]
        self.assertIs(_join(items), items)

    def test_decided_cards_are_never_changed(self):
        for items in ([_text_card(decision="BLUR"), _logo_card()], [_text_card(), _logo_card(decision="KEEP")]):
            self.assertIs(_join(items), items)

    def test_a_watermark_with_separate_moments_gives_the_logo_its_moments(self):
        moments = [{"start_seconds": 0.0, "end_seconds": 21.5}, {"start_seconds": 170.0, "end_seconds": 200.0},
                   {"start_seconds": 400.0, "end_seconds": 450.0}]
        text = _text_card(candidate_type="repeated_watermark", temporal_policy="discrete_detected_intervals",
                          detected_intervals=moments, end_seconds=450.0)
        joined_text, joined_logo = _join([text, _logo_card()])
        self.assertEqual(joined_logo["temporal_policy"], "discrete_detected_intervals")
        self.assertEqual(joined_logo["detected_intervals"], moments)
        self.assertEqual(joined_text["suggested_region_source_pixels"]["height"], 71)


class FixedTextLogoQueueTests(unittest.TestCase):
    """build_review_queue on a text scan and a visual-logo scan shaped like job 145."""

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for name in ("reports", "work", "input"):
            (self.root / name).mkdir()
        self.source = self.root / "input" / "source.mp4"
        self.source.write_bytes(b"source")
        self.queue = self.root / "reports" / "job" / "review-queue.json"

    def tearDown(self):
        self.temporary.cleanup()

    def _write(self, folder, name, payload):
        directory = self.root / "reports" / "job" / folder
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_text(json.dumps({
            "status": "REVIEW_REQUIRED", "input": str(self.source), "input_sha256": "abc",
            "duration_seconds": DURATION, "scan_start_seconds": 0.0, "scan_duration_seconds": DURATION,
            **payload,
        }, ensure_ascii=False), encoding="utf-8")
        return path

    def _build(self):
        line = {
            "track_id": 2, "start_seconds": 0.0, "end_seconds": 2562.0, "recommended_blur_end_seconds": 2565.0,
            "observations": 607, "zone": "top-right", "persistent": True, "review_priority": "high",
            "max_confidence": 0.9999, "union_box": [432, 18, 960, 43],
            "sample_text": ["EXAMPLE.COM", "EXAMPIE.COM", "EXAMPLE com", "EXAMPLE-com"],
            "ad_probability": 0.21, "semantic_top_label": "credits", "review_candidate": True,
            "routing": "REVIEW_PERSISTENT_OVERLAY", "candidate_type": "persistent_overlay",
            "suggested_decision": "BLUR", "policy_hits": [], "grouped_track_ids": [2, 98, 123, 138],
            "reason": "Chữ/logo cố định có thêm bằng chứng quảng cáo/thương hiệu",
            "preview": "text-previews/track-00002.jpg",
        }
        text = self._write("text", "text-scan.json", {
            "sample_every_seconds": 3.0, "source_size": [1280, 536], "analysis_size": [960, 402],
            "tracks": [line],
        })
        thumbnails = self.root / "reports" / "job" / "visual-logo" / "thumbnails"
        thumbnails.mkdir(parents=True)
        (thumbnails / "logo-0002-178.000s.jpg").write_bytes(b"image")
        logo = self._write("visual-logo", "scan-localized.json", {"scan_type": "visual_logo", "intervals": [{
            "start_seconds": 175.0, "end_seconds": 180.0, "max_score": 1.0, "sample_count": 2,
            "predicted_label": "Visual brand/logo candidate", "priority": "context",
            "reason": "Visual-language answer was uncertain; human review required",
            "strongest_timestamp_seconds": 178.0, "strongest_frame": "thumbnails/logo-0002-178.000s.jpg",
            "visual_logo_confirmation": {"state": "UNCERTAIN", "answer": "UNCERTAIN",
                                         "confirmation_source": "qwen_local"},
            "region_localization": {"frame_size": [1280, 536], "proposals": [{
                "box_xyxy": [863.36, 53.33, 1148.8, 86.03], "blur_region_px": [857, 47, 298, 45],
                "sources": ["grounding", "ocr"], "labels": ["promotional banner", "</s>EXAMPLE.COM"],
                "region_classification": "external_brand_candidate", "suggested_decision": "BLUR",
            }]},
            "suggested_decision": "BLUR",
        }]})
        return build_review_queue(project_root=self.root, report_paths=[text, logo], queue_path=self.queue)

    @staticmethod
    def _covers(region, box):
        return (region["x"] <= box["x"] and region["y"] <= box["y"]
                and region["x"] + region["width"] >= box["x"] + box["width"]
                and region["y"] + region["height"] >= box["y"] + box["height"])

    def test_the_film_long_card_blurs_the_logo_under_the_line_all_film(self):
        payload = self._build()
        [card] = [item for item in payload["items"] if item["category"] == "text"]
        self.assertEqual((card["start_seconds"], card["end_seconds"]), (0.0, DURATION))
        self.assertTrue(self._covers(card["suggested_region_source_pixels"], LOGO),
                        card["suggested_region_source_pixels"])
        # The logo's own window is not a separate short card any more (the forced ending
        # card has no region).
        short = [item for item in payload["items"]
                 if item["category"] == "visual_logo" and item.get("suggested_region_source_pixels")
                 and float(item["end_seconds"]) - float(item["start_seconds"]) < 60]
        self.assertEqual(short, [])
        [support] = [record for record in card.get("supporting_detections") or []
                     if record["category"] == "visual_logo"]
        self.assertEqual((support["start_seconds"], support["end_seconds"]), (0.0, DURATION))
        payload = record_review_decision(project_root=self.root, queue_path=self.queue, item_id=card["id"],
                                         decision="BLUR")
        for item in payload["items"]:
            if item["decision"] is None:  # the forced ending card
                payload = record_review_decision(project_root=self.root, queue_path=self.queue,
                                                 item_id=item["id"], decision="KEEP")
        [operation] = [op for op in approved_operations(payload) if op["start_seconds"] == 0.0]
        self.assertEqual(operation["end_seconds"], DURATION)
        self.assertTrue(self._covers(operation["region_source_pixels"], LOGO), operation["region_source_pixels"])


if __name__ == "__main__":
    unittest.main()
