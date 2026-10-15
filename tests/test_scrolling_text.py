"""Scrolling site text and web addresses read without their dot (session D, 2026-10-15).

Shapes measured on jobs 107, 108 and 140: the source site's line scrolls through
the top band in about 15 s and the text scan reads it every 3 s, as an entry piece
at the right edge, a wide piece and exit pieces at the left edge, often without the
domain's dot. The real site name never appears here: ``EXAMPLE.COM`` stands for it.
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.review_workflow import build_review_queue
from biliflow.scrolling_text import (
    promote_scrolling_text,
    promote_web_address_tracks,
    web_address_tokens,
)

DURATION = 3974.1
SAMPLE = 3.0


def _track(track_id, start, end, texts, box, **extra):
    track = {
        "track_id": track_id, "start_seconds": start, "end_seconds": end,
        "recommended_blur_end_seconds": end + 0.5, "observations": len(texts),
        "zone": "top-center", "persistent": False, "review_priority": "low",
        "max_confidence": 0.6, "union_box": box, "sample_text": list(texts),
        "ad_probability": 0.03, "semantic_top_label": "scene_text", "review_candidate": False,
        "routing": "LIKELY_SCENE_TEXT", "reason": "Chữ chỉ xuất hiện ngắn; cần người dùng kiểm tra",
        "preview": f"text-previews/track-{track_id:05d}.jpg",
    }
    track.update(extra)
    return track


def _review(**extra):
    values = {"review_candidate": True, "review_priority": "high"}
    values.update(extra)
    return values


def _job107_ticker(offset=0.0):
    """Job 107 at 2925 s: two halves of the line, never read together."""
    t = 2925.0 + offset
    return [
        _track(158, t, t + 3, ["'EXAMPLECOM chia sẻ €"], [637, 31, 960, 75], zone="top-right"),
        _track(159, t + 3, t + 9, ["EXAMPLE COM-chia-sẻ dữlỉệu phim; hoạt hình miễ",
                                   ";COM chia sẻ dữ liệu phim; hoạt hình miễn phí_"],
               [0, 26, 960, 82], routing="REVIEW_AD_LIKELY", ad_probability=0.649,
               semantic_top_label="advertisement", **_review()),
        _track(160, t + 6, t + 9, ["Cập nhật nhanh; chấ"], [668, 30, 960, 80], zone="top-right",
               routing="REVIEW_UNCERTAIN", ad_probability=0.27, **_review()),
        _track(162, t + 9, t + 15, ["Cập nhật nhanh, chất lượng cao; ổn định và lâu",
                                    "hật nhanh; chất luong cao; ổn định và lâu dài."],
               [0, 30, 960, 83], routing="REVIEW_UNCERTAIN", ad_probability=0.35,
               semantic_top_label="credits", **_review()),
        _track(161, t + 9, t + 12, ["hoạt hình miễn phí"], [8, 28, 288, 80], zone="top-left"),
        _track(163, t + 15, t + 18, ["ổn định và lâu dài"], [0, 30, 268, 80], zone="top-left",
               routing="REVIEW_AD_LIKELY", ad_probability=0.455, **_review()),
        # Unrelated text at the same time, outside the band.
        _track(164, t + 15, t + 18, ["dnaRS €"], [602, 186, 673, 235], zone="middle-center"),
    ]


def _job108_ticker():
    """Job 108 at 405 s: one wide track reads the whole line as it slides."""
    return [
        _track(51, 405.0, 408.0, ["227"], [648, 92, 726, 134], zone="top-right"),
        _track(52, 405.0, 408.0, ["EXAMPLECOM chia s8 dữ [ệ"], [568, 30, 960, 80],
               zone="top-right", ad_probability=0.066, semantic_top_label="credits"),
        _track(53, 408.0, 420.0, [
            "EXAMPLECOM chia sẻ dữ liệu phim; hoạt hình miễn phí",
            "Ichia sẻ dữ [iệu phim; hoạt hình miễn pphí - Cập uhật uhanh; chất",
            "t hình miễn phí - Cập nhật nhanh chất luong cao; ổn định và lâu dài",
            "ihanh chất Iượng cao; ổn định và Iâu dài.",
        ], [0, 28, 960, 83], routing="REVIEW_UNCERTAIN_OVERLAY", ad_probability=0.0526,
            semantic_top_label="credits", **_review(review_priority="medium")),
        _track(54, 411.0, 414.0, ["Iượ"], [901, 32, 965, 79], zone="top-right"),
        _track(55, 420.0, 423.0, ["ịnh và lâu dàis"], [0, 33, 205, 77], zone="top-left",
               ad_probability=0.06, semantic_top_label="credits"),
    ]


def _job140_ticker():
    """Job 140 at 324 s: entry and tail share no words; a wide reading joins them."""
    return [
        _track(11, 324.0, 327.0, ["EXAMPLECOM] chia s8 dữ liệu"], [603, 27, 951, 65],
               zone="top-right", routing="LOW_AD_UNCERTAIN", ad_probability=0.07),
        _track(12, 327.0, 330.0, ["EXAMPLECOM] chia s& dữ [iệu phim; hoat bhal miễD phí - Cập nl"],
               [227, 25, 960, 69], ad_probability=0.196, semantic_top_label="credits"),
        _track(13, 333.0, 336.0, ["h miễn phí - Cập ulhật nlhanh chẩt lrgngcao, ổn định và Iâu dài."],
               [0, 22, 738, 73], routing="REVIEW_AD_LIKELY", ad_probability=0.646, **_review()),
    ]


def _payload(tracks, **extra):
    payload = {
        "status": "REVIEW_REQUIRED", "duration_seconds": DURATION,
        "scan_start_seconds": 0.0, "scan_duration_seconds": DURATION,
        "sample_every_seconds": SAMPLE, "source_size": [1280, 640], "analysis_size": [960, 480],
        "tracks": tracks,
    }
    payload.update(extra)
    return payload


def _scrolling(output):
    return [track for track in output["tracks"] if track.get("candidate_type") == "scrolling_text"]


class ScrollingTextGroupTests(unittest.TestCase):
    def test_job107_halves_become_one_band_track_from_one_read_before(self):
        payload = _payload(_job107_ticker())
        output = promote_scrolling_text(payload)
        [ticker] = _scrolling(output)
        self.assertEqual(sorted(ticker["grouped_track_ids"]), [158, 159, 160, 161, 162, 163])
        # The line is visible between the read before (2922 s) and the first one (2925 s).
        self.assertEqual(ticker["start_seconds"], 2922.0)
        self.assertEqual(ticker["recommended_blur_end_seconds"], 2943.5)
        self.assertEqual(ticker["union_box"], [0, 26, 960, 83], "the whole band it scrolls through")
        self.assertEqual(ticker["routing"], "REVIEW_SCROLLING_TEXT")
        self.assertEqual(ticker["suggested_decision"], "BLUR")
        self.assertTrue(ticker["review_candidate"])
        self.assertEqual(ticker["review_priority"], "high")
        self.assertFalse(ticker["automatic_edit"])
        self.assertEqual(ticker["scrolling_text"]["first_read_seconds"], 2925.0)
        self.assertEqual(ticker["scrolling_text"]["last_read_seconds"], 2940.0)
        self.assertIn(164, [track["track_id"] for track in output["tracks"]])
        self.assertEqual(len(output["tracks"]), 2)
        self.assertEqual(len(payload["tracks"]), 7, "the input payload is not modified")

    def test_job108_entry_and_exit_pieces_join_the_sliding_track(self):
        output = promote_scrolling_text(_payload(_job108_ticker()))
        [ticker] = _scrolling(output)
        self.assertEqual(sorted(ticker["grouped_track_ids"]), [52, 53, 54, 55])
        self.assertEqual(ticker["start_seconds"], 402.0)
        self.assertEqual(ticker["recommended_blur_end_seconds"], 423.5)
        self.assertEqual(ticker["union_box"][0], 0)
        self.assertEqual(ticker["union_box"][2], 960)
        self.assertIn(51, [track["track_id"] for track in output["tracks"]], "a number below the band")

    def test_job140_wide_reading_joins_entry_and_tail(self):
        output = promote_scrolling_text(_payload(_job140_ticker()))
        [ticker] = _scrolling(output)
        self.assertEqual(sorted(ticker["grouped_track_ids"]), [11, 12, 13])
        self.assertEqual(ticker["start_seconds"], 321.0)
        # The last reading (333 s) still covered 77 % of the width and no exit piece
        # was read at 336 s: one more interval for the line's unread tail.
        self.assertEqual(ticker["recommended_blur_end_seconds"], 339.5)
        self.assertEqual(ticker["scrolling_text"]["direction"], "right_to_left")
        self.assertEqual(ticker["scrolling_text"]["tail_seconds"], 6.0)

    def test_two_intervals_before_a_first_reading_that_is_not_at_the_edge(self):
        tracks = [track for track in _job107_ticker() if track["track_id"] != 158]
        [ticker] = _scrolling(promote_scrolling_text(_payload(tracks)))
        self.assertEqual(ticker["scrolling_text"]["first_read_seconds"], 2928.0)
        self.assertEqual(ticker["start_seconds"], 2922.0)
        self.assertEqual(ticker["scrolling_text"]["lead_seconds"], 6.0)

    def test_appearances_far_apart_stay_separate(self):
        tracks = _job107_ticker() + [
            dict(track, track_id=track["track_id"] + 1000) for track in _job107_ticker(offset=600.0)
        ]
        tickers = _scrolling(promote_scrolling_text(_payload(tracks)))
        self.assertEqual(sorted(ticker["start_seconds"] for ticker in tickers), [2922.0, 3522.0])

    def test_lead_never_starts_before_the_scan(self):
        tracks = [dict(track, start_seconds=track["start_seconds"] - 2925.0 + 1.0,
                       end_seconds=track["end_seconds"] - 2925.0 + 1.0,
                       recommended_blur_end_seconds=track["recommended_blur_end_seconds"] - 2925.0 + 1.0)
                  for track in _job107_ticker()]
        [ticker] = _scrolling(promote_scrolling_text(_payload(tracks)))
        self.assertEqual(ticker["start_seconds"], 0.0)

    def test_changing_subtitles_are_not_scrolling_text(self):
        lines = ["Tôi không biết nữa", "Anh không biết gì cả", "Không biết thật sao",
                 "Chúng ta không biết đường", "Em không biết anh ở đâu"]
        tracks = []
        for index, line in enumerate(lines):
            start = 600.0 + index * 3.0
            width = 18 * len(line)
            left = (960 - width) // 2
            tracks.append(_track(index + 1, start, start + 3.0, [line], [left, 400, left + width, 440],
                                 zone="subtitle", routing="LIKELY_SUBTITLE"))
        payload = _payload(tracks)
        self.assertIs(promote_scrolling_text(payload), payload)

    def test_a_static_title_read_whole_and_in_part_is_not_scrolling_text(self):
        tracks = [
            _track(1, 60.0, 72.0, ["HỒ SƠ BÁC SĨ X - TẬP MỘT", "HỒ SƠ BÁC SĨ X - TẬP MỘT"] * 2,
                   [180, 30, 780, 80]),
            _track(2, 63.0, 66.0, ["BÁC SĨ X - TẬP MỘT"], [400, 30, 780, 80]),
            _track(3, 66.0, 69.0, ["HỒ SƠ BÁC SĨ"], [180, 30, 470, 80]),
        ]
        payload = _payload(tracks)
        self.assertIs(promote_scrolling_text(payload), payload)

    def test_a_fixed_corner_watermark_is_not_scrolling_text(self):
        tracks = [
            _track(index, 100.0 + index * 30.0, 103.0 + index * 30.0, ["EXAMPLE.COM"],
                   [20, 20, 200, 60], zone="top-left")
            for index in range(1, 6)
        ]
        payload = _payload(tracks)
        self.assertIs(promote_scrolling_text(payload), payload)

    def test_unrelated_text_in_the_band_is_not_absorbed(self):
        tracks = _job107_ticker() + [
            _track(300, 2880.0, 2990.0, ["KÊNH PHIM HAY"] * 6, [10, 28, 210, 80], zone="top-left",
                   persistent=True, observations=37),
        ]
        output = promote_scrolling_text(_payload(tracks))
        [ticker] = _scrolling(output)
        self.assertNotIn(300, ticker["grouped_track_ids"])
        self.assertIn(300, [track["track_id"] for track in output["tracks"]])

    def test_persistent_overlays_are_left_to_their_own_rule(self):
        tracks = _job107_ticker()
        tracks[1] = dict(tracks[1], candidate_type="persistent_overlay",
                         routing="REVIEW_PERSISTENT_OVERLAY")
        output = promote_scrolling_text(_payload(tracks))
        for ticker in _scrolling(output):
            self.assertNotIn(159, ticker["grouped_track_ids"])

    def test_subtitle_lines_and_other_text_in_the_band_keep_their_own_tracks(self):
        # A crossing of the bottom band with two subtitle lines and a centred sign inside it.
        tracks = [dict(track, union_box=[track["union_box"][0], 400 + track["union_box"][1] - 26,
                                         track["union_box"][2], 400 + track["union_box"][3] - 26],
                       zone="subtitle")
                  for track in _job107_ticker() if track["track_id"] != 164]
        tracks += [
            _track(500, 2930.0, 2933.0, ["Anh không biết gì cả"], [300, 402, 660, 450],
                   zone="subtitle", routing="LIKELY_SUBTITLE"),
            _track(501, 2936.0, 2939.0, ["Em đi đâu vậy"], [330, 402, 630, 450],
                   zone="subtitle", routing="LIKELY_SUBTITLE"),
            _track(502, 2933.0, 2936.0, ["QUÁN CƠM NGON"], [380, 404, 600, 452], zone="subtitle"),
        ]
        output = promote_scrolling_text(_payload(tracks))
        [ticker] = _scrolling(output)
        self.assertEqual(ticker["scrolling_text"]["band"], "bottom")
        self.assertEqual(sorted(ticker["grouped_track_ids"]), [158, 159, 160, 161, 162, 163])
        self.assertEqual(sorted(track["track_id"] for track in output["tracks"]
                                if track.get("candidate_type") != "scrolling_text"), [500, 501, 502])

    def test_end_credits_whose_reading_only_grows_are_not_scrolling_text(self):
        # Job 128 at 3813 s and job 108 at 3657 s: a credit read short, then whole.
        tracks = [
            _track(1, 3813.0, 3840.0, ["Studio", "Estudio de Doblaje", "Estudio de doblaje"],
                   [312, 60, 474, 86], routing="LIKELY_CREDITS"),
            _track(2, 3813.0, 3834.0, ["Example Digital", "Example Digital Germany"],
                   [488, 60, 672, 86], routing="LIKELY_CREDITS"),
            _track(3, 3657.0, 3663.0, ["successful", "successful, anc tne"],
                   [300, 365, 520, 397], zone="subtitle", routing="LIKELY_CREDITS"),
        ]
        payload = _payload(tracks)
        self.assertIs(promote_scrolling_text(payload), payload)

    def test_subtitles_sharing_a_few_words_are_not_scrolling_text(self):
        # The shape of job 136 at 129 s: "…với anh", then "Em nói anh…" line up on 8 letters only.
        tracks = [
            _track(1, 129.0, 138.0, ["Không ai giúp được anh đâu.", "Chẳng ai chia sẻ với anh,"],
                   [100, 400, 860, 440], zone="subtitle", routing="REVIEW_UNCERTAIN", **_review()),
            _track(2, 138.0, 141.0, ["mà anh cũng không nói."], [300, 400, 660, 440],
                   zone="subtitle", routing="LIKELY_SUBTITLE"),
            _track(3, 141.0, 144.0, ["Em nói anh biết nhé"], [100, 400, 860, 440],
                   zone="subtitle", routing="LIKELY_SUBTITLE"),
        ]
        payload = _payload(tracks)
        self.assertIs(promote_scrolling_text(payload), payload)

    def test_a_line_read_only_full_width_is_found_by_its_slide(self):
        line = ("EXAMPLECOM chia sẻ dữ liệu phim, hoạt hình miễn phí - Cập nhật nhanh, "
                "chất lượng cao, ổn định và lâu dài")
        tracks = [
            _track(70 + step, 500.0 + 3 * step, 503.0 + 3 * step, [line[12 * step:12 * step + 48]],
                   [0, 28, 960, 83], routing="LOW_AD_UNCERTAIN")
            for step in range(4)
        ]
        [ticker] = _scrolling(promote_scrolling_text(_payload(tracks)))
        self.assertEqual(sorted(ticker["grouped_track_ids"]), [70, 71, 72, 73])
        self.assertIsNone(ticker["scrolling_text"]["direction"])
        # No reading at the edges: two intervals before, one more after.
        self.assertEqual(ticker["start_seconds"], 494.0)
        self.assertEqual(ticker["end_seconds"], 515.0)


class WebAddressTests(unittest.TestCase):
    def test_web_address_with_a_lost_dot_or_spaces_is_found(self):
        for text in (
            "EXAMPLE.COM chia sẻ dữ liệu", "'EXAMPLECOM chia sẻ €", "EXAMPLE COM-chia-sẻ",
            "EXAMPLE. COM", "EXAMPLE ,COM", "xem tại example.com nhé", "EXAMPLECOM]",
            "EXAMPLENET", "EXAMPLE NET", "EXAMPLEVN", "EXAMPLE.VN", "EXAMPLETV", "EXAMPLEORG",
            "www example", "https://example.org/a", "Example.Net", "Example.Com",
            # Subdomains, endings OCR read in mixed case, a short name read with a space.
            "xem.example.com", "m.example.com", "EXAMPLE.cOM", "Example.aPP", "XYZ COM",
        ):
            with self.subTest(text=text):
                self.assertTrue(web_address_tokens(text), text)

    def test_ordinary_film_text_is_not_a_web_address(self):
        for text in (
            "Anh ăn cơm chưa?", "NẤU CƠM ĐI", "Xem TV đi em", "Ok, tv hỏng rồi", "Hà Nội, VN",
            "sitcom", "VIETTEL TELECOM", "ANIMAL PLANET", "INTERNET", "CABINET", "COMPANY",
            "COMBO 3 MÓN", "NETWORK", "VTV3", "HTV7", "SCTV", "Tập 12", "3.14", "A NETFLIX SERIES",
            "Ừ. Com", "CCOM]", "NCCOM chia sẻ",
            # ASCII words before an ending (review of 220d25f).
            "Anh, TV dang bat", "Saigon, VN", "Ha Noi, VN", "SMART TV", "THE NET", "LIVE TV",
            "OLED TV", "MUSIC TV", "OK. TV hỏng", "Ok. Tv", "Wait.Me", "Stop.To", "Good.Pro",
            "abc@example.com",
        ):
            with self.subTest(text=text):
                self.assertEqual(web_address_tokens(text), [], text)

    def test_tracks_with_a_web_address_join_the_main_list_with_blur(self):
        tracks = [
            _track(1, 900.0, 903.0, ["EXAMPLE.COM"], [700, 30, 950, 70], zone="top-right",
                   routing="LOW_AD_UNCERTAIN", ad_probability=0.07),
            _track(2, 1500.0, 1503.0, ["xem phim tại EXAMPLECOM"], [300, 400, 660, 440],
                   zone="subtitle", routing="LIKELY_SUBTITLE"),
            _track(3, 1800.0, 1803.0, ["Anh ăn cơm chưa?"], [300, 400, 660, 440],
                   zone="subtitle", routing="LIKELY_SUBTITLE"),
            _track(4, 2000.0, 2003.0, ["EXAMPLE.COM"], [700, 30, 950, 70], zone="top-right",
                   routing="REVIEW_AD_LIKELY", ad_probability=0.8, **_review()),
        ]
        payload = _payload(tracks)
        output = promote_web_address_tracks(payload)
        by_id = {track["track_id"]: track for track in output["tracks"]}
        for track_id in (1, 2, 4):
            with self.subTest(track=track_id):
                track = by_id[track_id]
                self.assertTrue(track["review_candidate"])
                self.assertEqual(track["suggested_decision"], "BLUR")
                self.assertEqual(track["review_priority"], "high")
                self.assertTrue(track["web_address"]["tokens"])
        self.assertEqual(by_id[1]["routing"], "REVIEW_WEB_ADDRESS")
        self.assertEqual(by_id[2]["routing"], "REVIEW_WEB_ADDRESS")
        self.assertEqual(by_id[4]["routing"], "REVIEW_AD_LIKELY", "an ad routing stays")
        self.assertIs(by_id[3], tracks[2], "ordinary dialogue is untouched")
        self.assertEqual(tracks[0]["routing"], "LOW_AD_UNCERTAIN", "the input payload is not modified")


class ScrollingTextQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for name in ("reports", "work", "input"):
            (self.root / name).mkdir()
        self.source = self.root / "input" / "source.mp4"
        self.source.write_bytes(b"source")
        self.queue_path = self.root / "reports" / "job" / "review-queue.json"

    def tearDown(self):
        self.temporary.cleanup()

    def _report(self, tracks):
        directory = self.root / "reports" / "job" / "text"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "text-scan.json"
        path.write_text(json.dumps(_payload(tracks, input=str(self.source)), ensure_ascii=False),
                        encoding="utf-8")
        return path

    def _build(self, tracks, **options):
        return build_review_queue(
            project_root=self.root, report_paths=[self._report(tracks)], queue_path=self.queue_path,
            **options,
        )

    @staticmethod
    def _text_cards(items, start, end):
        return [
            item for item in items
            if item["category"] == "text"
            and float(item["start_seconds"]) < end and float(item["end_seconds"]) > start
        ]

    def test_job107_ticker_is_one_main_card_suggesting_blur(self):
        queue = self._build(_job107_ticker())
        [card] = self._text_cards(queue["items"], 2915.0, 2950.0)
        self.assertEqual(card["candidate_type"], "scrolling_text")
        self.assertEqual(card["suggested_decision"], "BLUR")
        self.assertEqual(card["priority"], "high")
        self.assertEqual((card["start_seconds"], card["end_seconds"]), (2922.0, 2943.5))
        region = card["suggested_region_source_pixels"]
        self.assertEqual((region["x"], region["width"]), (0, 1280))
        self.assertLessEqual(region["y"], 35)
        self.assertGreaterEqual(region["y"] + region["height"], 100)
        self.assertEqual(card["suggested_blur_edge_mode"], "vertical_only")
        refs = card["source_candidate_refs"]
        for track_id in (158, 159, 160, 161, 162, 163):
            self.assertTrue(any(ref.endswith(f"#track:{track_id}") for ref in refs), track_id)
        self.assertEqual(len(card["preview_images"]), 6, "every piece's preview is on the card")
        self.assertEqual(self._text_cards(queue["advisory_items"], 2915.0, 2950.0), [])
        self.assertTrue(queue["candidate_coverage"]["reference_complete"])

    def test_job108_ticker_no_longer_waits_among_optional_candidates(self):
        queue = self._build(_job108_ticker())
        [card] = self._text_cards(queue["items"], 398.0, 428.0)
        self.assertEqual(card["candidate_type"], "scrolling_text")
        self.assertEqual(card["suggested_decision"], "BLUR")
        self.assertEqual((card["start_seconds"], card["end_seconds"]), (402.0, 423.5))
        self.assertEqual(self._text_cards(queue["advisory_items"], 398.0, 428.0), [])

    def test_a_web_address_is_never_only_an_optional_candidate(self):
        queue = self._build([
            _track(1, 900.0, 903.0, ["EXAMPLE.COM"], [700, 30, 950, 70], zone="top-right",
                   routing="LOW_AD_UNCERTAIN", ad_probability=0.07),
        ])
        [card] = self._text_cards(queue["items"], 899.0, 904.0)
        self.assertEqual(card["suggested_decision"], "BLUR")
        self.assertEqual(self._text_cards(queue["advisory_items"], 899.0, 904.0), [])

    def _decided(self, item, decision, region=None):
        decided = dict(item)
        decided.update({
            "decision": decision, "decision_note": "đã duyệt",
            "decision_region_source_pixels": region if decision == "BLUR" else None,
            "decision_blur_edge_mode": "vertical_only" if decision == "BLUR" else None,
            "decision_actor": "control_center_user", "decision_transport": "control_center",
            "decided_at": "2026-10-12T10:00:00+00:00",
        })
        return decided

    def _old_queue(self, items):
        """A queue as the old code wrote it, with the user's decisions."""
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1, "status": "READY_FOR_EDIT_PLAN",
            "source": {"path": str(self.source.resolve()), "sha256": None, "duration_seconds": DURATION},
            "items": items, "advisory_items": [],
            "audit_log": [{"at": "2026-10-12T10:00:00+00:00", "action": "DECIDE", "item_id": items[0]["id"],
                           "decision": items[0]["decision"]}],
        }
        self.queue_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _old_card(self, item_id, start, end, region, decision, **extra):
        card = {
            "id": item_id, "category": "text", "start_seconds": start, "end_seconds": end,
            "priority": "normal", "labels": ["EXAMPLE COM-chia-sẻ"], "reasons": [],
            "evidence": ["reports/job/text/text-scan.json"], "preview_images": [],
            "suggested_region_source_pixels": region, "candidate_type": None,
            "suggested_decision": None, "source_candidate_refs": [],
        }
        card.update(extra)
        return self._decided(card, decision, region)

    def test_rebuild_keeps_the_old_blur_on_the_longer_card(self):
        band = {"x": 0, "y": 35, "width": 1280, "height": 68}
        elsewhere = {"x": 100, "y": 300, "width": 200, "height": 40}
        self._old_queue([
            self._old_card("text-old-ticker", 2928.0, 2943.5, band, "BLUR"),
            # A visual card of a report not given to this rebuild: kept as it was.
            self._decided({
                "id": "visual-old", "category": "visual_logo", "start_seconds": 10.0,
                "end_seconds": 20.0, "priority": "normal", "labels": [], "reasons": [],
                "evidence": [], "preview_images": [], "suggested_region_source_pixels": elsewhere,
                "candidate_type": None, "suggested_decision": None, "source_candidate_refs": [],
            }, "BLUR", elsewhere),
        ])
        queue = self._build(_job107_ticker(), keep_decisions=True)
        [card] = self._text_cards(queue["items"], 2915.0, 2950.0)
        self.assertEqual(card["decision"], "BLUR")
        self.assertEqual((card["start_seconds"], card["end_seconds"]), (2922.0, 2943.5))
        region = card["decision_region_source_pixels"]
        self.assertEqual((region["x"], region["width"]), (0, 1280))
        self.assertLessEqual(region["y"], 35)
        self.assertGreaterEqual(region["y"] + region["height"], 103, "never smaller than the user's box")
        self.assertEqual(card["decision_actor"], "control_center_user")
        self.assertEqual(card["decided_at"], "2026-10-12T10:00:00+00:00")
        self.assertEqual(card["decision_carried_from"], ["text-old-ticker"])
        [visual] = [item for item in queue["items"] if item["id"] == "visual-old"]
        self.assertEqual(visual["decision"], "BLUR")
        self.assertEqual(visual["decision_region_source_pixels"], elsewhere)
        self.assertEqual(queue["counts"]["pending"], 0)
        self.assertEqual(queue["status"], "READY_FOR_EDIT_PLAN")
        actions = [entry["action"] for entry in queue["audit_log"]]
        self.assertEqual(actions[0], "DECIDE", "the old audit log is kept")
        self.assertEqual(actions[-1], "REBUILD_KEEP_DECISIONS")

    def test_rebuild_without_keep_decisions_is_unchanged(self):
        band = {"x": 0, "y": 35, "width": 1280, "height": 68}
        self._old_queue([self._old_card("text-old-ticker", 2928.0, 2943.5, band, "BLUR")])
        queue = self._build(_job107_ticker())
        [card] = self._text_cards(queue["items"], 2915.0, 2950.0)
        self.assertIsNone(card["decision"])

    def test_old_keep_does_not_hide_a_new_scrolling_card(self):
        self._old_queue([self._old_card("text-old-exit", 2940.0, 2943.5,
                                        {"x": 0, "y": 40, "width": 357, "height": 67}, "KEEP")])
        queue = self._build(_job107_ticker(), keep_decisions=True)
        cards = self._text_cards(queue["items"], 2915.0, 2950.0)
        ticker = next(card for card in cards if card.get("candidate_type") == "scrolling_text")
        self.assertIsNone(ticker["decision"], "a KEEP of one piece is not a KEEP of the whole line")
        [old] = [card for card in cards if card["id"] == "text-old-exit"]
        self.assertEqual(old["decision"], "KEEP", "the old decision itself is kept")
        self.assertEqual(old["migration_status"], "preserved_decided_from_previous_queue")
        self.assertEqual(queue["status"], "REVIEW_REQUIRED")

    def test_conflicting_old_decisions_leave_the_card_open(self):
        band = {"x": 0, "y": 35, "width": 1280, "height": 68}
        self._old_queue([
            self._old_card("text-old-a", 2928.0, 2943.5, band, "BLUR"),
            self._old_card("text-old-b", 2925.0, 2940.0, band, "KEEP"),
        ])
        queue = self._build(_job107_ticker(), keep_decisions=True)
        cards = self._text_cards(queue["items"], 2915.0, 2950.0)
        ticker = next(card for card in cards if card.get("candidate_type") == "scrolling_text")
        self.assertIsNone(ticker["decision"])
        self.assertIn("text-old-a", ticker["decision_conflict"])
        self.assertIn("text-old-b", ticker["decision_conflict"])
        kept = {card["id"]: card["decision"] for card in cards if card["id"].startswith("text-old")}
        self.assertEqual(kept, {"text-old-a": "BLUR", "text-old-b": "KEEP"})


if __name__ == "__main__":
    unittest.main()
