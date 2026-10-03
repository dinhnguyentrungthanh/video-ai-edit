import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.textscan import (
    Track, _accept_detection, _annotate_platform_names, _iou,
    _limit_report_tracks, _promote_repeated_corner_overlays,
    _sha256_file, _summarize_track,
    _text_continuity, _zone,
)


class TextTrackingTests(unittest.TestCase):
    def test_repeated_corner_ocr_fragments_become_one_blur_suggestion(self):
        tracks = []
        for index, (start, value) in enumerate((
            (6.0, "SHIN CẬU BÉ BÚT CHÌ"),
            (54.0, "SHIN CẬU CÊ BÚT CHÌ"),
            (117.0, "SHIN CẬU BÉ BÚT CHÌ"),
            (267.0, "SHIN C)U BÉ BÚT CHÌ"),
            (414.0, "SHIN CẬU BÉ BÚT CHÌ"),
        ), 1):
            tracks.append({
                "track_id": index, "start_seconds": start,
                "end_seconds": start + 3.0,
                "recommended_blur_end_seconds": start + 3.5,
                "zone": "top-right", "union_box": [684, 30, 906, 78],
                "sample_text": [value], "max_confidence": 0.2,
                "ad_probability": 0.8, "review_candidate": True,
                "routing": "REVIEW_AD_LIKELY",
                "review_priority": "low", "persistent": False,
            })
        result = _promote_repeated_corner_overlays(tracks, 445.0)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["suggested_decision"], "BLUR")
        self.assertEqual(result[0]["candidate_type"], "persistent_overlay")
        self.assertEqual(result[0]["start_seconds"], 6.0)
        self.assertEqual(result[0]["end_seconds"], 417.0)
        self.assertFalse(result[0]["automatic_edit"])

    def test_repeated_low_ad_title_is_not_suggested_for_blur(self):
        tracks = []
        for index, start in enumerate((6.0, 54.0, 117.0, 267.0, 414.0), 1):
            tracks.append({
                "track_id": index, "start_seconds": start,
                "end_seconds": start + 3.0,
                "recommended_blur_end_seconds": start + 3.5,
                "zone": "top-right", "union_box": [684, 30, 906, 78],
                "sample_text": ["SHIN CẬU BÉ BÚT CHÌ"],
                "max_confidence": 0.2, "ad_probability": 0.02,
                "review_candidate": False, "routing": "LIKELY_SCENE_TEXT",
                "review_priority": "low", "persistent": False,
            })
        result = _promote_repeated_corner_overlays(tracks, 445.0)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["routing"], "LIKELY_TITLE_OVERLAY")
        self.assertEqual(result[0]["candidate_type"], "title_overlay")
        self.assertFalse(result[0]["review_candidate"])
        self.assertIsNone(result[0]["suggested_decision"])

    def test_repeated_overlay_ignores_one_ocr_box_joined_to_scene_text(self):
        tracks = []
        boxes = (
            [54, 79, 140, 107], [53, 80, 140, 107],
            [54, 80, 141, 108], [53, 79, 140, 108],
            [55, 73, 368, 119],
        )
        for index, (start, box) in enumerate(zip(
            (6.0, 60.0, 120.0, 240.0, 414.0), boxes, strict=True,
        ), 1):
            tracks.append({
                "track_id": index, "start_seconds": start,
                "end_seconds": start + 3.0,
                "recommended_blur_end_seconds": start + 3.5,
                "zone": "top-left", "union_box": box,
                "sample_text": ["XEMBZ.NET"], "max_confidence": 0.9,
                "ad_probability": 0.8, "review_candidate": True,
                "routing": "REVIEW_AD_LIKELY",
                "review_priority": "high", "persistent": False,
            })
        result = _promote_repeated_corner_overlays(tracks, 445.0)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["union_box"], [53, 79, 141, 108])

    def test_platform_tracks_survive_report_truncation(self):
        candidates = [
            {"track_id": index, "review_candidate": True,
             "sample_text": ["SHOP NOW"], "start_seconds": float(index)}
            for index in range(1, 4)
        ]
        references = [
            {"track_id": 10 + index, "review_candidate": False,
             "sample_text": ["Một dòng chữ trong cảnh"], "start_seconds": 20.0 + index}
            for index in range(10)
        ]
        ending = {"track_id": 99, "review_candidate": False,
                  "sample_text": ["iOIYI"], "start_seconds": 2700.0}
        summaries = _annotate_platform_names(candidates + references + [ending])
        self.assertNotIn("platform_name", ending)
        kept = _limit_report_tracks(summaries, 6)
        self.assertEqual([track["track_id"] for track in kept], [1, 2, 3, 99, 10, 11])
        self.assertEqual(kept[3]["platform_name"]["key"], "iqiyi")
        self.assertEqual(kept[3]["platform_name"]["text"], "iOIYI")
        self.assertNotIn("platform_name", kept[0])
        crowded = _limit_report_tracks(summaries, 2)
        self.assertEqual([track["track_id"] for track in crowded], [1, 2, 3, 99])

    def test_report_source_hash_uses_file_content(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "video.mp4"
            path.write_bytes(b"biliflow-text-source")
            self.assertEqual(
                _sha256_file(path),
                "1decc664c1419d5d542ad757d33c7cd1d19cbe235aaa941504938583f8ba17d6",
            )

    def test_corner_persistence_is_high_priority(self):
        track = Track(1, 0.0, 0.0, (800, 20, 930, 70), "top-right")
        for timestamp in (0.0, 3.0, 6.0):
            track.observations.append(
                {
                    "timestamp_seconds": timestamp,
                    "box": [800, 20, 930, 70],
                    "text": "sample",
                    "confidence": 0.9,
                }
            )
            track.last_seen = timestamp
            track.best_confidence = 0.9
        result = _summarize_track(track, 3.0, 15.0)
        self.assertTrue(result["persistent"])
        self.assertEqual(result["review_priority"], "high")
        self.assertTrue(result["endpoint_confirmed"])
        self.assertEqual(result["recommended_blur_end_seconds"], 9.5)

    def test_bottom_center_is_subtitle_zone(self):
        self.assertEqual(_zone((250, 420, 710, 500), 960, 520), "subtitle")

    def test_iou_rejects_separate_regions(self):
        self.assertEqual(_iou((0, 0, 20, 20), (100, 100, 120, 120)), 0.0)

    def test_low_confidence_long_top_banner_is_retained(self):
        self.assertTrue(
            _accept_detection(
                confidence=0.12,
                text="C0M chia se du lieu phim hoat hinh mien phi",
                box=(20, 20, 930, 70),
                width=960,
                height=540,
                minimum_confidence=0.35,
            )
        )
        self.assertFalse(
            _accept_detection(
                confidence=0.12,
                text="mot dong chu trong canh phim",
                box=(220, 220, 740, 270),
                width=960,
                height=540,
                minimum_confidence=0.35,
            )
        )
        self.assertFalse(
            _accept_detection(
                confidence=0.99,
                text=" ",
                box=(20, 20, 200, 70),
                width=960,
                height=540,
                minimum_confidence=0.35,
            )
        )

    def test_misread_banner_is_retained_without_keyword_logic(self):
        track = Track(3, 225.0, 225.0, (10, 25, 950, 80), "top-center")
        track.observations.append(
            {
                "timestamp_seconds": 225.0,
                "box": [10, 25, 950, 80],
                "text": "C0M chla se du lieu phim hoat hinh mien phi",
                "confidence": 0.12,
            }
        )
        track.best_confidence = 0.12
        result = _summarize_track(track, 3.0)
        self.assertEqual(result["review_priority"], "low")

    def test_text_continuity_separates_changing_subtitles(self):
        self.assertFalse(
            _text_continuity("Em đang đi đâu vậy?", "Ngày mai chúng ta sẽ gặp lại.")
        )

    def test_text_continuity_keeps_banner_fragments_and_ocr_variants(self):
        self.assertTrue(
            _text_continuity(
                "NGUONC.COM chia sẻ dữ liệu phim",
                "chia sẻ dữ liệu phim hoạt hình miễn phí",
            )
        )
        self.assertTrue(_text_continuity("NETFLỈX SCRỈES", "NETFLIX SERIES"))


if __name__ == "__main__":
    unittest.main()
