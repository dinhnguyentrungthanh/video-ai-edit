"""Fixed on-screen text becomes one track card (job 40 "Nhất Âu Xuân - Tập 10", 2026-10-02)."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.review_workflow import (
    _merge_items,
    build_edit_plan,
    build_review_queue,
    corroborate_fixed_text_overlays,
    full_frame_logo_ad_evidence,
    promote_fixed_text_overlays,
    record_review_decision,
)

DURATION = 2607.0
LINE = "PHIM ĐƯỢC CẬP NHẬT NHANH NHẤT TẠI MOTCHILLV PH"
FRAGMENTS = [
    # (start, text, x1, x2): pieces of the faint bottom line OCR read now and then
    (198, "ILLV Ph", 613, 667), (300, "NHANHNHAT", 445, 537), (339, "MoTCHILLVPH", 563, 667),
    (375, "PHIM DUOC CAP NHaT NHANH", 293, 499), (540, "PhIM ĐUOC CAP NHAT NHAN", 292, 491),
    (669, "Tai MotchILLV PH", 539, 669), (894, "DUOC CAP NHAT NHANH NHAT", 331, 539),
    (1224, "PHIM ĐUOC CaP NHaT NHANH NHA", 293, 531), (1383, LINE, 292, 670),
    (1503, "OTCHILLV", 577, 645), (2268, "PHIM ĐƯQC CẬP NHẬT NHANH NHAT TAI MOTCHILLV PH.", 292, 674),
]


def _track(track_id, start, end, text, box, **extra):
    track = {
        "track_id": track_id, "start_seconds": start, "end_seconds": end,
        "recommended_blur_end_seconds": end + 0.5, "observations": 1,
        "zone": "subtitle", "persistent": False, "review_priority": "low",
        "max_confidence": 0.5, "union_box": box, "sample_text": [text],
        "ad_probability": 0.01, "review_candidate": False,
        "routing": "LIKELY_SCENE_TEXT", "reason": "Chữ ngắn/nhỏ trong cảnh và điểm quảng cáo thấp",
    }
    track.update(extra)
    return track


def _job40_tracks(*, watermark_text="Motchillv.ph", watermark_observations=865, ad_line=True):
    tracks = [_track(
        1, 0.0, DURATION, watermark_text, [34, 25, 210, 78], zone="top-left",
        persistent=True, observations=watermark_observations, ad_probability=0.158,
    )]
    for index, (start, text, x1, x2) in enumerate(FRAGMENTS, start=2):
        tracks.append(_track(index, start, start + 3, text, [x1, 371, x2, 391]))
    if ad_line:
        tracks[-1].update({
            "routing": "REVIEW_AD_LIKELY", "review_candidate": True,
            "ad_probability": 0.666, "review_priority": "high",
        })
    # Ordinary scene text elsewhere stays untouched.
    tracks.append(_track(90, 1000, 1003, "SPRING BLADE", [471, 217, 601, 261], zone="middle-center"))
    return tracks


def _payload(tracks, **extra):
    payload = {
        "status": "REVIEW_REQUIRED", "duration_seconds": DURATION,
        "scan_start_seconds": 0.0, "scan_duration_seconds": DURATION,
        "sample_every_seconds": 3.0, "source_size": [1280, 534], "analysis_size": [960, 400],
        "tracks": tracks,
    }
    payload.update(extra)
    return payload


class PromoteFixedTextOverlayTests(unittest.TestCase):
    def test_whole_film_watermark_and_faint_line_become_two_track_cards(self):
        payload = _payload(_job40_tracks())
        output = promote_fixed_text_overlays(payload)
        overlays = [track for track in output["tracks"] if track.get("fixed_text_overlay")]
        self.assertEqual(len(overlays), 2)
        watermark = next(t for t in overlays if t["fixed_text_overlay"]["rule"] == "whole_film_text")
        line = next(t for t in overlays if t["fixed_text_overlay"]["rule"] == "recurring_fixed_text")
        for overlay in overlays:
            self.assertEqual(overlay["routing"], "REVIEW_PERSISTENT_OVERLAY")
            self.assertEqual(overlay["candidate_type"], "persistent_overlay")
            self.assertEqual((overlay["start_seconds"], overlay["end_seconds"]), (0.0, DURATION))
            self.assertEqual(overlay["suggested_decision"], "BLUR")
            self.assertTrue(overlay["fixed_text_overlay"]["whole_film"])
            self.assertFalse(overlay["automatic_edit"])
        self.assertEqual(watermark["union_box"], [34, 25, 210, 78])
        self.assertEqual(line["union_box"], [292, 371, 674, 391])
        self.assertEqual(line["fixed_text_overlay"]["track_count"], len(FRAGMENTS))
        self.assertEqual(line["fixed_text_overlay"]["observed_start_seconds"], 198.0)
        # Absorbed fragments are gone; unrelated scene text is kept.
        ids = [track["track_id"] for track in output["tracks"]]
        self.assertIn(90, ids)
        self.assertEqual(len(output["tracks"]), 3)
        self.assertEqual(len(payload["tracks"]), len(FRAGMENTS) + 2, "input payload is not modified")
        self.assertEqual(len(output["fixed_text_overlays"]), 2)

    def test_faint_line_inherits_the_site_name_of_the_watermark(self):
        output = promote_fixed_text_overlays(_payload(_job40_tracks(ad_line=False)))
        line = next(
            t for t in output["tracks"]
            if (t.get("fixed_text_overlay") or {}).get("rule") == "recurring_fixed_text"
        )
        self.assertEqual(line["suggested_decision"], "BLUR")
        self.assertIn("motchillv", line["fixed_text_overlay"]["ad_evidence"][0])

    def test_without_ad_evidence_the_card_has_no_suggestion(self):
        # No web address on the corner text, no ad routing on the line ("MOTCHILLV PH"
        # without a dot is not an address), and so no site name to share.
        tracks = _job40_tracks(watermark_text="BIG TITLE", ad_line=False)
        output = promote_fixed_text_overlays(_payload(tracks))
        overlays = [track for track in output["tracks"] if track.get("fixed_text_overlay")]
        self.assertEqual(len(overlays), 2)
        for overlay in overlays:
            self.assertIsNone(overlay["suggested_decision"])
            self.assertEqual(overlay["fixed_text_overlay"]["ad_evidence"], [])
            self.assertIn("bạn tự quyết", overlay["reason"])

    def test_platform_name_is_overlay_ad_evidence(self):
        # A whole-film WeTV corner mark is a third-party watermark: BLUR it.
        output = promote_fixed_text_overlays(_payload(_job40_tracks(watermark_text="WeTV")[:1]))
        [overlay] = [track for track in output["tracks"] if track.get("fixed_text_overlay")]
        self.assertEqual(overlay["suggested_decision"], "BLUR")
        self.assertEqual(overlay["fixed_text_overlay"]["ad_evidence"],
                         ["tên nền tảng video: Tencent Video (WeTV)"])
        # The opening-ident CUT check never counts a platform name (that would re-arm CUT).
        ident = {"start_seconds": 8.0, "end_seconds": 13.0, "source_candidate_refs": []}
        payloads = {"text.json": _payload([_track(1, 9.0, 12.0, "iOlYI", [378, 156, 574, 230],
                                                  zone="middle-center")])}
        self.assertEqual(full_frame_logo_ad_evidence(ident, [], payloads), [])

    def test_text_read_in_a_minority_of_the_film_is_not_promoted(self):
        tracks = _job40_tracks(watermark_observations=200)[:1]  # 600 s of 2607 s
        payload = _payload(tracks)
        self.assertIs(promote_fixed_text_overlays(payload), payload)

    def test_changing_subtitle_lines_do_not_form_a_group(self):
        lines = [
            "Anh đi đâu vậy", "Ta không biết nữa", "Mau chạy đi thôi", "Hắn đã tới rồi",
            "Ngươi là ai vậy", "Đừng nói nữa", "Chúng ta về nhà", "Trời sắp mưa rồi",
            "Ngươi nhớ kỹ lời ta", "Ta sẽ quay lại",
        ]
        tracks = [
            _track(index, 100 + index * 200, 103 + index * 200, text, [300, 371, 640, 391])
            for index, text in enumerate(lines, start=1)
        ]
        payload = _payload(tracks)
        self.assertIs(promote_fixed_text_overlays(payload), payload)

    def test_partial_scan_card_covers_only_the_scanned_range(self):
        tracks = _job40_tracks()[:1]
        tracks[0].update({"start_seconds": 600.0, "end_seconds": 1200.0, "observations": 200})
        output = promote_fixed_text_overlays(_payload(
            tracks, scan_start_seconds=600.0, scan_duration_seconds=600.0,
        ))
        overlay = output["tracks"][0]
        self.assertEqual((overlay["start_seconds"], overlay["end_seconds"]), (600.0, 1200.0))


class FixedTextQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for name in ("reports", "work", "input"):
            (self.root / name).mkdir()
        self.source = self.root / "input" / "source.mp4"
        self.source.write_bytes(b"source")

    def tearDown(self):
        self.temporary.cleanup()

    def _text_report(self, tracks):
        directory = self.root / "reports" / "job" / "text"
        directory.mkdir(parents=True)
        path = directory / "text-scan.json"
        payload = _payload(tracks, input=str(self.source))
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_job40_queue_blurs_both_marks_for_the_whole_video_after_approval(self):
        report = self._text_report(_job40_tracks())
        queue_path = self.root / "reports" / "job" / "review-queue.json"
        queue = build_review_queue(project_root=self.root, report_paths=[report], queue_path=queue_path)
        cards = [item for item in queue["items"] if item.get("fixed_text_overlay")]
        self.assertEqual(len(cards), 2)
        self.assertEqual(len(queue["items"]), 2, "the three short REVIEW lines joined the line card")
        regions = sorted(
            (card["suggested_region_source_pixels"]["y"], card["suggested_region_source_pixels"]["height"])
            for card in cards
        )
        # Scaled from the 960x400 analysis frame to 1280x534; each card keeps its own box.
        self.assertLess(regions[0][0] + regions[0][1], 120)
        self.assertGreater(regions[1][0], 480)
        self.assertTrue(queue["candidate_coverage"]["reference_complete"])
        for card in cards:
            self.assertEqual(card["review_kind"], "logo_overlay")
            record_review_decision(
                project_root=self.root, queue_path=queue_path, item_id=card["id"], decision="BLUR",
            )
        plan = build_edit_plan(
            project_root=self.root, queue_path=queue_path, plan_path=self.root / "work" / "plan.json",
        )
        operations = plan["approved_operations"]
        self.assertEqual(len(operations), 2)
        for operation in operations:
            self.assertEqual((operation["start_seconds"], operation["end_seconds"]), (0.0, DURATION))
            self.assertLess(operation["region_source_pixels"]["height"], 100)

    def test_corner_mark_never_merges_with_a_bottom_line_into_one_box(self):
        # Tập 18: an uncertain whole-film corner track swallowed bottom lines into
        # one 839x483 box. Too few readings for a line card here: the bottom text
        # stays its own card, and the corner mark its own track.
        tracks = _job40_tracks()[:1] + [
            _track(2, 2067, 2076, "PHIM ĐUOC CẬP NHA", [297, 371, 437, 391],
                   routing="REVIEW_UNCERTAIN", review_candidate=True, ad_probability=0.202),
        ]
        tracks[0].update({"routing": "REVIEW_UNCERTAIN_OVERLAY", "review_candidate": True})
        report = self._text_report(tracks)
        queue = build_review_queue(
            project_root=self.root, report_paths=[report],
            queue_path=self.root / "reports" / "job" / "review-queue.json",
        )
        heights = sorted(item["suggested_region_source_pixels"]["height"] for item in queue["items"])
        self.assertEqual(len(queue["items"]), 2)
        self.assertLess(heights[-1], 100)


class TextMergeTests(unittest.TestCase):
    @staticmethod
    def _item(start, end, region):
        return {
            "id": f"t-{start}-{region['y']}", "category": "text", "start_seconds": start,
            "end_seconds": end, "max_score": 0.3, "priority": "low", "labels": ["x"],
            "reasons": [], "evidence": ["r"], "preview_images": [],
            "suggested_region_source_pixels": region, "candidate_type": None,
            "suggested_decision": None, "source_candidate_refs": [f"r#{start}-{region['y']}"],
            "detected_intervals": [{"start_seconds": start, "end_seconds": end}],
        }

    def test_lines_of_one_paragraph_still_merge(self):
        # Troy's opening narration: lines about one to two line heights apart.
        merged = _merge_items([
            self._item(51.0, 60.5, {"x": 480, "y": 682, "width": 960, "height": 52}),
            self._item(51.0, 63.5, {"x": 412, "y": 778, "width": 1096, "height": 58}),
        ], 1.0)
        self.assertEqual(len(merged), 1)

    def test_texts_far_apart_stay_separate(self):
        merged = _merge_items([
            self._item(0.0, 2600.0, {"x": 48, "y": 32, "width": 274, "height": 75}),
            self._item(2067.0, 2076.5, {"x": 396, "y": 494, "width": 187, "height": 27}),
        ], 1.0)
        self.assertEqual(len(merged), 2)


class CorroborationTests(unittest.TestCase):
    def test_visual_confirmation_of_the_same_box_suggests_blur(self):
        card = {
            "id": "c", "category": "text", "start_seconds": 0.0, "end_seconds": 100.0,
            "suggested_region_source_pixels": {"x": 45, "y": 33, "width": 235, "height": 71},
            "suggested_decision": None, "decision": None, "reasons": [],
            "fixed_text_overlay": {"rule": "whole_film_text", "ad_evidence": []},
        }
        visual = {
            "id": "v", "category": "visual_logo", "start_seconds": 20.0, "end_seconds": 25.0,
            "suggested_region_source_pixels": {"x": 46, "y": 41, "width": 188, "height": 45},
            "model_evidence": {"vlm_confirmation": "CONFIRMED", "vlm_source": "qwen_local"},
        }
        far = dict(visual, id="far", suggested_region_source_pixels={
            "x": 900, "y": 300, "width": 100, "height": 50,
        })
        corroborate_fixed_text_overlays([card, visual, far])
        self.assertEqual(card["suggested_decision"], "BLUR")
        self.assertIn("1 đoạn", card["fixed_text_overlay"]["ad_evidence"][0])

    def test_rejected_visual_window_is_not_evidence(self):
        card = {
            "id": "c", "category": "text", "start_seconds": 0.0, "end_seconds": 100.0,
            "suggested_region_source_pixels": {"x": 45, "y": 33, "width": 235, "height": 71},
            "suggested_decision": None, "decision": None, "reasons": [],
            "fixed_text_overlay": {"rule": "whole_film_text", "ad_evidence": []},
        }
        visual = {
            "id": "v", "category": "visual_logo", "start_seconds": 20.0, "end_seconds": 25.0,
            "suggested_region_source_pixels": {"x": 46, "y": 41, "width": 188, "height": 45},
            "model_evidence": {"vlm_confirmation": "REJECTED"},
        }
        corroborate_fixed_text_overlays([card, visual])
        self.assertIsNone(card["suggested_decision"])


if __name__ == "__main__":
    unittest.main()
