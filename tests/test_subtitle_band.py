"""The site watermark in the subtitle band and subtitle lines read as ads (session D, part 2).

Shapes measured on jobs 134 and 143 (analysis 960 x 402, source 1280 x 536, a read
every 3 s): the watermark sits under the subtitles at about [414, 352, 546, 379] and
is read in short runs with gaps, its name misread a little each time; subtitle lines
are read once or twice, centred, about [230..730, 307..351]. ``EXAMPLE.COM`` stands
for the site.
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.review_workflow import approved_operations, build_review_queue, record_review_decision
from biliflow.subtitle_band import group_repeated_watermarks, move_subtitle_lines

DURATION = 2700.0
WATERMARK = [414, 352, 546, 379]
SPANS = [(0.0, 21.5), (24.0, 72.5), (75.0, 96.5), (99.0, 120.5), (123.0, 156.5)]


def _track(track_id, start, end, texts, box, **extra):
    track = {
        "track_id": track_id, "start_seconds": start, "end_seconds": end,
        "recommended_blur_end_seconds": end + 0.5, "observations": max(1, round((end - start) / 3.0)),
        "zone": "subtitle", "persistent": False, "review_priority": "high", "max_confidence": 0.7,
        "union_box": box, "sample_text": list(texts), "ad_probability": 0.4,
        "semantic_top_label": "credits", "review_candidate": True, "routing": "REVIEW_UNCERTAIN",
        "reason": "Cần kiểm tra", "preview": f"text-previews/track-{track_id:05d}.jpg", "policy_hits": [],
    }
    track.update(extra)
    return track


def _watermark_sightings():
    """Five runs of one watermark: misread names, boxes a few pixels apart, mixed routings."""
    spellings = ["EXAMPLECCOM", "EXAMPIECCOM", "XAMPLECCOM", "EXAMPLECCOM", "EXAMPLEC COM"]
    runs = [(0.0, 21.0), (27.0, 72.0), (78.0, 96.0), (102.0, 120.0), (126.0, 156.0)]
    routings = ["REVIEW_UNCERTAIN", "REVIEW_AD_LIKELY", "LIKELY_SCENE_TEXT", "LOW_AD_UNCERTAIN",
                "REVIEW_UNCERTAIN"]
    return [
        _track(100 + index, start, end, [spelling],
               [WATERMARK[0], WATERMARK[1] + index % 2, WATERMARK[2] + 10 * (index % 3), WATERMARK[3]],
               routing=routing, review_candidate=routing != "LIKELY_SCENE_TEXT", persistent=True,
               ad_probability=0.1 if routing == "LOW_AD_UNCERTAIN" else 0.4)
        for index, ((start, end), spelling, routing) in enumerate(zip(runs, spellings, routings))
    ]


def _subtitle_stream(count=24, start=300.0):
    """Centred dialogue lines read once each, routed uncertain; a third already routed as subtitles."""
    lines = []
    for index in range(count):
        text = f"Câu thoại số {index} của nhân vật"
        width = 16 * len(text)
        left = (960 - width) // 2
        routing = ("REVIEW_UNCERTAIN", "REVIEW_UNCERTAIN", "LIKELY_SUBTITLE")[index % 3]
        lines.append(_track(400 + index, start + 3.5 * index, start + 3.5 * index + 3.0, [text],
                            [left, 307, left + width, 351], routing=routing, ad_probability=0.3,
                            review_candidate=routing != "LIKELY_SUBTITLE"))
    return lines


def _payload(tracks):
    return {
        "status": "REVIEW_REQUIRED", "duration_seconds": DURATION, "scan_start_seconds": 0.0,
        "scan_duration_seconds": DURATION, "sample_every_seconds": 3.0, "source_size": [1280, 536],
        "analysis_size": [960, 402], "tracks": tracks,
    }


def _groups(payload):
    return [track for track in payload["tracks"] if track.get("candidate_type") == "repeated_watermark"]


class WatermarkGroupTests(unittest.TestCase):
    def test_one_watermark_read_in_runs_is_one_group_of_its_moments(self):
        sightings = _watermark_sightings()
        output = group_repeated_watermarks(_payload(sightings))
        [group] = _groups(output)
        self.assertEqual(output["tracks"], [group], "the sightings are replaced by their group")
        self.assertEqual(group["grouped_track_ids"], [100, 101, 102, 103, 104])
        self.assertEqual(group["suggested_decision"], "BLUR")
        self.assertTrue(group["review_candidate"])
        self.assertEqual(group["temporal_policy"], "discrete_detected_intervals")
        # From one read interval before each run's first reading to its blur end.
        self.assertEqual([(i["start_seconds"], i["end_seconds"]) for i in group["detected_intervals"]], SPANS)
        self.assertEqual((group["start_seconds"], group["recommended_blur_end_seconds"]), (0.0, 156.5))
        self.assertEqual(group["union_box"], [414, 352, 566, 379])
        self.assertIn("exampleccom", group["web_address"]["tokens"][0].lower().replace(" ", ""))
        self.assertEqual(sightings[0]["routing"], "REVIEW_UNCERTAIN", "the input is not modified")

    def test_a_box_holding_the_watermark_and_a_subtitle_does_not_join_it(self):
        mixed = _track(200, 160.0, 163.0, ["Anh nói gì vậy EXAMPLECCOM"], [230, 307, 730, 379])
        output = group_repeated_watermarks(_payload(_watermark_sightings() + [mixed]))
        [group] = _groups(output)
        self.assertNotIn(200, group["grouped_track_ids"])
        self.assertIn(mixed, output["tracks"])

    def test_a_wider_line_around_the_address_does_not_join_it(self):
        # Same row, 400 px wide around the 142 px mark: IoU 0.36 with the group's median box.
        wide = _track(201, 160.0, 163.0, ["Xem phim hay tại EXAMPLECCOM"], [280, 352, 680, 379])
        output = group_repeated_watermarks(_payload(_watermark_sightings() + [wide]))
        [group] = _groups(output)
        self.assertEqual(group["grouped_track_ids"], [100, 101, 102, 103, 104])
        self.assertIn(wide, output["tracks"])

    def test_one_box_grown_over_the_subtitle_line_does_not_widen_every_moment(self):
        # Ten runs; one OCR box reaches up 22 px into the line above (IoU 0.55, so it joins).
        runs = [_track(100 + n, 30.0 * n, 30.0 * n + 9.0, ["EXAMPLECCOM"],
                       [414, 330 if n == 4 else 352, 546, 379]) for n in range(10)]
        [group] = _groups(group_repeated_watermarks(_payload(runs)))
        self.assertEqual(len(group["grouped_track_ids"]), 10)
        self.assertEqual(group["union_box"], [414, 352, 546, 379])
        # Ten runs 17.5 s apart: the mark stays on screen between them, one moment.
        self.assertEqual(group["detected_intervals"], [{"start_seconds": 0.0, "end_seconds": 279.5}])
        # Fewer runs keep the union of their boxes.
        [few] = _groups(group_repeated_watermarks(_payload(runs[3:6])))
        self.assertEqual(few["union_box"], [414, 330, 546, 379])

    def test_a_frequent_mark_joins_runs_up_to_150_s_apart_and_no_further(self):
        starts = [0.0, 30.0, 60.0, 90.0, 120.0, 400.0, 430.0, 460.0, 490.0, 520.0]  # one 271 s gap
        runs = [_track(100 + n, start, start + 9.0, ["EXAMPLECCOM"], WATERMARK) for n, start in enumerate(starts)]
        [group] = _groups(group_repeated_watermarks(_payload(runs)))
        self.assertEqual([(m["start_seconds"], m["end_seconds"]) for m in group["detected_intervals"]],
                         [(0.0, 129.5), (397.0, 529.5)])
        self.assertEqual(group["repeated_watermark"]["bridge_seconds"], 150.0)
        # Fewer than ten runs: only runs that touch join (a line with an address said twice).
        [pair] = _groups(group_repeated_watermarks(_payload(runs[:2])))
        self.assertEqual(len(pair["detected_intervals"]), 2)

    def test_other_addresses_or_places_make_their_own_groups_or_stay(self):
        other_site = _track(300, 400.0, 403.0, ["ANOTHER-EXAMPLE.ORG"], WATERMARK)
        other_place = _track(301, 500.0, 503.0, ["EXAMPLECCOM"], [700, 20, 832, 47], zone="top-right")
        output = group_repeated_watermarks(_payload(_watermark_sightings() + [other_site, other_place]))
        [group] = _groups(output)
        self.assertEqual(group["grouped_track_ids"], [100, 101, 102, 103, 104])
        self.assertIn(other_site, output["tracks"])
        self.assertIn(other_place, output["tracks"])

    def test_a_single_sighting_or_text_without_an_address_is_left_alone(self):
        payload = _payload([_track(1, 10.0, 13.0, ["EXAMPLE.COM"], WATERMARK),
                            _track(2, 30.0, 33.0, ["Xin chào"], WATERMARK),
                            _track(3, 40.0, 43.0, ["Xin chào"], WATERMARK)])
        self.assertIs(group_repeated_watermarks(payload), payload)

    def test_a_remembered_address_groups_without_a_domain_ending(self):
        payload = _payload([_track(1, 10.0, 13.0, ["examplephim"], WATERMARK),
                            _track(2, 30.0, 33.0, ["EXAMPLEPHIM HD"], WATERMARK)])
        self.assertIs(group_repeated_watermarks(payload), payload)
        [group] = _groups(group_repeated_watermarks(payload, remembered=("examplephim",)))
        self.assertEqual(group["grouped_track_ids"], [1, 2])
        self.assertEqual(group["web_address"]["remembered"], ["examplephim"])

    def test_fixed_overlays_and_scrolling_lines_keep_their_own_cards(self):
        payload = _payload([
            _track(1, 0.0, 2600.0, ["EXAMPLE.COM"], WATERMARK, candidate_type="persistent_overlay",
                   routing="REVIEW_PERSISTENT_OVERLAY"),
            _track(2, 30.0, 33.0, ["EXAMPLE.COM"], WATERMARK, candidate_type="scrolling_text"),
            _track(3, 60.0, 63.0, ["EXAMPLE.COM"], WATERMARK),
        ])
        self.assertIs(group_repeated_watermarks(payload), payload)


class SubtitleLineTests(unittest.TestCase):
    def test_subtitle_lines_read_as_ads_go_to_the_optional_list(self):
        stream = _subtitle_stream()
        output = move_subtitle_lines(_payload(stream))
        moved = [track for track in output["tracks"] if track.get("subtitle_line")]
        self.assertEqual(len(moved), 16, "the uncertain lines; LIKELY_SUBTITLE lines were no cards")
        for track in moved:
            self.assertFalse(track["review_candidate"])
            self.assertEqual(track["subtitle_line"], {"previous_routing": "REVIEW_UNCERTAIN"})
        self.assertTrue(stream[0]["review_candidate"], "the input is not modified")

    def test_ads_in_the_subtitle_band_always_stay_cards(self):
        centred = [300, 307, 660, 351]
        ads = [
            _track(1, 900.0, 903.0, ["Xem phim tại EXAMPLE.COM"], centred),
            _track(2, 910.0, 913.0, ["Hotline 0912 345 678"], centred),
            _track(3, 920.0, 923.0, ["Theo dõi @examplephim"], centred),
            _track(4, 930.0, 933.0, ["Đăng ký kênh ngay"], centred, policy_hits=["đăng ký"]),
            _track(5, 940.0, 943.0, ["examplephim phim hay"], centred),
            _track(6, 950.0, 953.0, ["EXAMPLEFILM NET"], centred),
            _track(7, 960.0, 963.0, ["Mua tại example . vn"], centred),
            # The scan's ad routing without any address: the reviewer still sees it as a card.
            _track(8, 970.0, 973.0, ["Nạp thẻ nhận quà ngay"], centred, routing="REVIEW_AD_LIKELY",
                   ad_probability=0.7),
            # Routed uncertain without an address, but ranked or scored as an ad (session D review).
            _track(9, 980.0, 983.0, ["Nạp thẻ nhận quà ngay"], centred, ad_probability=0.3,
                   semantic_top_label="advertisement"),
            _track(10, 990.0, 993.0, ["Nạp thẻ nhận quà ngay"], centred, ad_probability=0.44),
        ]
        output = move_subtitle_lines(_payload(_subtitle_stream() + ads), remembered=("examplephim",))
        unchanged = [track for track in output["tracks"] if track["track_id"] <= 10]
        self.assertEqual(unchanged, ads)

    def test_off_centre_repeated_or_lonely_text_stays_as_it_was(self):
        others = [
            _track(1, 900.0, 903.0, ["Giảm giá hôm nay"], [20, 307, 300, 351]),
            _track(2, 910.0, 919.0, ["Giảm giá hôm nay"] * 3, [330, 307, 630, 351], observations=3),
            _track(3, 920.0, 923.0, ["Giảm giá hôm nay"], [330, 307, 630, 351], persistent=True),
            _track(4, 930.0, 933.0, ["Giảm giá hôm nay"], [330, 40, 630, 80], zone="top-center"),
            _track(5, 940.0, 943.0, ["Giảm giá hôm nay"], [330, 307, 630, 351], observations=None),
        ]
        output = move_subtitle_lines(_payload(_subtitle_stream() + others))
        self.assertEqual([track for track in output["tracks"] if track["track_id"] < 10], others)
        lonely = _payload(_subtitle_stream(count=10))
        self.assertIs(move_subtitle_lines(lonely), lonely, "a film without a stream of subtitle lines")


class SubtitleBandQueueTests(unittest.TestCase):
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

    def _build(self, tracks):
        directory = self.root / "reports" / "job" / "text"
        directory.mkdir(parents=True, exist_ok=True)
        report = directory / "text-scan.json"
        report.write_text(json.dumps(dict(_payload(tracks), input=str(self.source)), ensure_ascii=False),
                          encoding="utf-8")
        return build_review_queue(project_root=self.root, report_paths=[report], queue_path=self.queue)

    @staticmethod
    def _text_cards(items, start, end):
        return [item for item in items if item["category"] == "text"
                and float(item["start_seconds"]) < end and float(item["end_seconds"]) > start]

    def test_the_watermark_is_one_card_and_subtitle_lines_are_optional(self):
        payload = self._build(_watermark_sightings() + _subtitle_stream(start=10.0))
        [card] = self._text_cards(payload["items"], 0.0, 200.0)
        self.assertEqual(card["candidate_type"], "repeated_watermark")
        self.assertEqual(card["temporal_policy"], "discrete_detected_intervals")
        self.assertEqual([(i["start_seconds"], i["end_seconds"]) for i in card["detected_intervals"]], SPANS)
        self.assertEqual(card["suggested_decision"], "BLUR")
        # Only the watermark's own box (scaled 4/3, its padded bottom tightened), not the lines above.
        self.assertEqual(card["suggested_region_source_pixels"], {"x": 552, "y": 469, "width": 203, "height": 32})
        self.assertEqual(len(card["source_candidate_refs"]), 5)
        self.assertEqual(len(card["preview_images"]), 5)
        optional = self._text_cards(payload["advisory_items"], 0.0, 200.0)
        self.assertEqual(len(optional), 16)
        for item in optional:
            self.assertIn("Giống một dòng phụ đề", " ".join(item["reasons"]))
            self.assertEqual(item["suggested_decision"], "KEEP")
        self.assertTrue(payload["candidate_coverage"]["reference_complete"])

    def test_a_fixed_overlay_over_the_same_place_does_not_absorb_the_watermark_card(self):
        overlay = _track(1, 0.0, 2600.0, ["EXAMPLE.COM"], [400, 345, 570, 385], persistent=True,
                         candidate_type="persistent_overlay", routing="REVIEW_PERSISTENT_OVERLAY",
                         observations=800)
        payload = self._build(_watermark_sightings() + [overlay])
        kinds = sorted(str(item.get("candidate_type")) for item in payload["items"] if item["category"] == "text")
        self.assertEqual(kinds, ["persistent_overlay", "repeated_watermark"])

    def test_one_blur_edits_each_moment_of_the_watermark_only(self):
        payload = self._build(_watermark_sightings())
        [card] = self._text_cards(payload["items"], 0.0, 200.0)
        payload = record_review_decision(project_root=self.root, queue_path=self.queue, item_id=card["id"],
                                         decision="BLUR")
        operations = approved_operations(payload)
        self.assertEqual([(op["start_seconds"], op["end_seconds"]) for op in operations], SPANS)
        for operation in operations:
            self.assertEqual(operation["region_source_pixels"], card["suggested_region_source_pixels"])


if __name__ == "__main__":
    unittest.main()
