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


def _corner_track(track_id, start, box, text, *, end=None, observations=1, footprint=None):
    end = start + 3.0 if end is None else end
    return {
        "track_id": track_id, "start_seconds": start, "end_seconds": end,
        "recommended_blur_end_seconds": end + 0.5,
        "zone": "top-right", "union_box": box, "footprint_box": footprint or box,
        "observations": observations, "sample_text": [text], "max_confidence": 0.9,
        "ad_probability": 0.8, "review_candidate": True, "routing": "REVIEW_AD_LIKELY",
        "review_priority": "high", "persistent": observations >= 3,
    }


# A site's scrolling line read in eleven short pieces (y 18-43 of a 960 x 402 frame) and
# its fixed logo just below it, read the whole film (job 145's shape, example text).
LINE_PIECES = [
    _corner_track(index, 100.0 + index * 210.0, [432 + (index % 4) * 12, 18 + index % 2, 960, 43 - index % 2],
                  "EXAMPLE.COM chia se phim")
    for index in range(10, 21)
]
LOGO_FOOTPRINT = [644, 38, 862, 68]


def _logo_anchor(*, footprint=LOGO_FOOTPRINT, observations=607, end=2562.0):
    # Its min/max box also holds the few reads joined to the line above.
    return _corner_track(2, 0.0, [640, 18, 945, 71], "Example.com", end=end,
                         observations=observations, footprint=footprint)


class RepeatedOverlayAnchorTests(unittest.TestCase):
    def test_fixed_logo_read_all_film_stays_in_the_box_of_its_group(self):
        [result] = _promote_repeated_corner_overlays([_logo_anchor(), *LINE_PIECES], 2562.0)
        self.assertEqual(sorted(result["grouped_track_ids"]), [2, *range(10, 21)])
        self.assertEqual(result["union_box"], [432, 18, 960, 68])
        self.assertEqual(result["footprint_box"], result["union_box"])
        self.assertEqual(result["overlay_box_sources"], {
            "consensus_box": [432, 18, 960, 43],
            "anchors": [{"track_id": 2, "footprint_box": LOGO_FOOTPRINT, "observations": 607, "joined": True}],
        })
        self.assertEqual(result["suggested_decision"], "BLUR")

    def test_group_without_a_long_member_keeps_the_consensus_box(self):
        [result] = _promote_repeated_corner_overlays(list(LINE_PIECES), 2562.0)
        self.assertEqual(result["union_box"], [432, 18, 960, 43])
        self.assertNotIn("overlay_box_sources", result)

    def test_a_member_needs_ten_reads_and_the_group_span_to_be_an_anchor(self):
        for anchor in (_logo_anchor(observations=9), _logo_anchor(end=59.0)):
            with self.subTest(observations=anchor["observations"], end=anchor["end_seconds"]):
                [result] = _promote_repeated_corner_overlays([anchor, *LINE_PIECES], 2562.0)
                self.assertEqual(result["union_box"], [432, 18, 960, 43])
                self.assertNotIn("overlay_box_sources", result)

    def test_anchor_already_inside_the_consensus_changes_nothing(self):
        boxes = ([54, 79, 140, 107], [53, 80, 140, 107], [54, 80, 141, 108], [53, 79, 140, 108])
        tracks = [
            _corner_track(index, start, box, "EXAMPLE.COM")
            for index, (start, box) in enumerate(zip((6.0, 60.0, 120.0, 240.0), boxes, strict=True), 1)
        ]
        # A long track whose min/max box took in scene text; its own reads sit on the mark.
        tracks.append(_corner_track(5, 300.0, [55, 73, 368, 119], "EXAMPLE.COM", end=445.0,
                                    observations=48, footprint=[54, 80, 140, 107]))
        [result] = _promote_repeated_corner_overlays(tracks, 445.0)
        self.assertEqual(result["union_box"], [53, 79, 141, 108])
        self.assertNotIn("overlay_box_sources", result)

    def test_anchor_apart_or_much_taller_is_recorded_but_not_joined(self):
        for footprint in ([700, 80, 900, 105], [644, 38, 862, 110]):
            with self.subTest(footprint=footprint):
                [result] = _promote_repeated_corner_overlays([_logo_anchor(footprint=footprint), *LINE_PIECES], 2562.0)
                self.assertEqual(result["union_box"], [432, 18, 960, 43])
                self.assertEqual(result["overlay_box_sources"]["anchors"], [
                    {"track_id": 2, "footprint_box": footprint, "observations": 607, "joined": False},
                ])

    def test_member_without_a_footprint_is_never_an_anchor(self):
        anchor = _logo_anchor()
        del anchor["footprint_box"]  # a report written before footprints
        [result] = _promote_repeated_corner_overlays([anchor, *LINE_PIECES], 2562.0)
        self.assertEqual(result["union_box"], [432, 18, 960, 43])
        self.assertNotIn("overlay_box_sources", result)

    def test_anchor_beside_the_box_joins_unless_the_gap_is_wider_than_half_its_height(self):
        for footprint, joined in (([390, 20, 428, 40], True), ([380, 20, 410, 40], False)):
            with self.subTest(footprint=footprint):
                [result] = _promote_repeated_corner_overlays([_logo_anchor(footprint=footprint), *LINE_PIECES], 2562.0)
                self.assertEqual(result["overlay_box_sources"]["anchors"][0]["joined"], joined)
                self.assertEqual(result["union_box"], [390, 18, 960, 43] if joined else [432, 18, 960, 43])

    def test_two_anchors_join_whatever_the_order(self):
        second = _corner_track(3, 0.0, [640, 18, 945, 71], "Example.com", end=2562.0, observations=40,
                               footprint=[860, 40, 958, 80])
        boxes = {
            str(order): _promote_repeated_corner_overlays(tracks, 2562.0)[0]["union_box"]
            for order, tracks in enumerate(([_logo_anchor(), second, *LINE_PIECES],
                                            [*reversed(LINE_PIECES), second, _logo_anchor()]))
        }
        self.assertEqual(boxes, {"0": [432, 18, 960, 80], "1": [432, 18, 960, 80]})

    def test_track_footprint_drops_a_read_joined_to_nearby_text(self):
        track = Track(2, 0.0, 0.0, (644, 38, 862, 68), "top-right")
        boxes = [[644, 38, 862, 68], [646, 38, 862, 68], [644, 36, 862, 68], [560, 10, 960, 90],
                 [644, 38, 862, 68], [646, 36, 862, 68], [644, 38, 862, 68]]
        for index, box in enumerate(boxes):
            track.observations.append({"timestamp_seconds": index * 3.0, "box": box,
                                       "text": "Example.com", "confidence": 0.9})
            track.last_seen = index * 3.0
        result = _summarize_track(track, 3.0, 30.0)
        self.assertEqual(result["union_box"], [560, 10, 960, 90])
        self.assertEqual(result["footprint_box"], [644, 36, 862, 68])

    def test_short_track_footprint_is_its_box(self):
        track = Track(3, 0.0, 3.0, (10, 20, 90, 40), "top-left")
        for index, box in enumerate(([10, 20, 90, 40], [12, 22, 300, 60])):
            track.observations.append({"timestamp_seconds": index * 3.0, "box": box,
                                       "text": "EXAMPLE", "confidence": 0.9})
        result = _summarize_track(track, 3.0)
        self.assertEqual(result["footprint_box"], result["union_box"])


if __name__ == "__main__":
    unittest.main()
