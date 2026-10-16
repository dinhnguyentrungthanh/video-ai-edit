"""A film-long fixed-text card made of subtitle dialogue suggests no BLUR (task 2, 2026-10-16).

In jobs 130-145 the dialogue lines under the film read one after another at one place chained
into one "recurring fixed text" group (each piece shares a word with the next), which became a
whole-film card suggesting BLUR over the subtitles. The site's mark below the subtitles has its
own repeated-watermark card. Such a card stays visible but suggests nothing, and it must not hide
an ad card inside its box or take a BLUR from another card.
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.review_workflow import (
    _merge_items,
    build_review_queue,
    corroborate_fixed_text_overlays,
    promote_fixed_text_overlays,
    reconcile_persistent_overlay_items,
)

DURATION = 2607.0
BAND_BOX = [300, 371, 640, 391]  # analysis frame 960 x 400: centre at 49 % of the width, 95 % of the height
TOP_BOX = [300, 16, 640, 36]
# Each long reading holds two neighbouring words and each short one the word they share: the tracks
# chain into one group while no single line holds most of them.
WORDS = ["khoang", "buoitr", "sangma", "chieut", "toinay", "demqua"]


def _track(track_id, start, text, box, **extra):
    track = {
        "track_id": track_id, "start_seconds": start, "end_seconds": start + 3.0,
        "recommended_blur_end_seconds": start + 3.5, "observations": 1,
        "zone": "subtitle", "persistent": False, "review_priority": "low",
        "max_confidence": 0.5, "union_box": list(box), "sample_text": [text],
        "ad_probability": 0.01, "review_candidate": False,
        "routing": "LIKELY_SCENE_TEXT", "reason": "Chữ ngắn/nhỏ trong cảnh và điểm quảng cáo thấp",
    }
    track.update(extra)
    return track


def _chain(box, *, step=230.0):
    tracks, start = [], 100.0
    for index in range(len(WORDS) - 1):
        tracks.append(_track(len(tracks) + 1, start, f"{WORDS[index]} {WORDS[index + 1]}", box))
        start += step
        if index < len(WORDS) - 2:
            tracks.append(_track(len(tracks) + 1, start, WORDS[index + 1], box))
            start += step
    # One dialogue line the semantic model scored as an ad: the card's old BLUR evidence.
    tracks[2].update({"ad_probability": 0.84, "routing": "REVIEW_AD_LIKELY"})
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


def _line_card(output):
    [card] = [track for track in output["tracks"]
              if (track.get("fixed_text_overlay") or {}).get("rule") == "recurring_fixed_text"]
    return card


class SubtitleDialogueCardTests(unittest.TestCase):
    def test_a_film_long_chain_of_dialogue_in_the_subtitle_band_suggests_no_blur(self):
        card = _line_card(promote_fixed_text_overlays(_payload(_chain(BAND_BOX))))
        details = card["fixed_text_overlay"]
        self.assertTrue(details["whole_film"])
        self.assertEqual(details["track_count"], 9)
        self.assertIsNone(card["suggested_decision"])
        self.assertLess(details["subtitle_dialogue"]["line_consensus"], 0.60)
        self.assertTrue(details["ad_evidence"], "the weak evidence stays on the card for the reviewer")
        self.assertIn("chuỗi phụ đề thoại", card["reason"])
        self.assertIn("thẻ watermark riêng", card["reason"])

    def test_a_contact_reading_in_the_chain_keeps_the_blur(self):
        for reading in ("xem tai example.com", "xem tai EXAMPLE COM", "goi 0901234567 ngay", "theo doi @examplefilm",
                        "xem tai Example.ph", "doc quyen tren WeTV"):
            with self.subTest(reading=reading):
                tracks = _chain(BAND_BOX)
                tracks[5]["sample_text"].append(reading)
                card = _line_card(promote_fixed_text_overlays(_payload(tracks)))
                self.assertEqual(card["suggested_decision"], "BLUR")
                self.assertNotIn("subtitle_dialogue", card["fixed_text_overlay"])

    def test_the_same_chain_outside_the_subtitle_band_keeps_the_blur(self):
        card = _line_card(promote_fixed_text_overlays(_payload(_chain(TOP_BOX))))
        self.assertEqual(card["suggested_decision"], "BLUR")
        self.assertNotIn("subtitle_dialogue", card["fixed_text_overlay"])

    def test_a_chain_over_part_of_the_film_keeps_the_blur(self):
        card = _line_card(promote_fixed_text_overlays(_payload(_chain(BAND_BOX, step=110.0))))
        self.assertFalse(card["fixed_text_overlay"]["whole_film"])
        self.assertEqual(card["suggested_decision"], "BLUR")

    def test_dialogue_naming_a_watermarks_site_keeps_the_blur(self):
        watermark = _track(90, 0.0, "examplesite.com", [34, 25, 210, 78], zone="top-left", persistent=True,
                           observations=865)
        watermark["end_seconds"] = DURATION
        tracks = _chain(BAND_BOX)
        tracks[4]["sample_text"].append("xem tiep o examplesite nhe")
        card = _line_card(promote_fixed_text_overlays(_payload(tracks + [watermark])))
        self.assertEqual(card["suggested_decision"], "BLUR")
        self.assertNotIn("subtitle_dialogue", card["fixed_text_overlay"])

    def test_one_line_read_in_pieces_in_the_band_keeps_the_blur(self):
        line = "PHIM DUOC CAP NHAT NHANH NHAT TAI EXAMPLE"
        pieces = [line, line[:24], line[5:30], line[10:], line[:18], line[12:36], line, line[3:27], line[20:]]
        tracks = [_track(index, 100.0 + index * 230.0, text, BAND_BOX) for index, text in enumerate(pieces, start=1)]
        tracks[2].update({"ad_probability": 0.84, "routing": "REVIEW_AD_LIKELY"})
        card = _line_card(promote_fixed_text_overlays(_payload(tracks)))
        self.assertEqual(card["suggested_decision"], "BLUR")
        self.assertNotIn("subtitle_dialogue", card["fixed_text_overlay"])


def _item(item_id, *, region, suggested=None, details=None, **extra):
    item = {
        "id": item_id, "category": "text", "start_seconds": 0.0, "end_seconds": DURATION,
        "max_score": 0.3, "priority": "high", "labels": ["x"], "reasons": [], "evidence": [item_id],
        "preview_images": [], "suggested_region_source_pixels": region, "candidate_type": "persistent_overlay",
        "suggested_decision": suggested, "decision": None, "source_candidate_refs": [f"r#track:{item_id}"],
        "detected_intervals": [{"start_seconds": 0.0, "end_seconds": DURATION}],
    }
    if details is not None:
        item["fixed_text_overlay"] = details
    item.update(extra)
    return item


DIALOGUE_REGION = {"x": 400, "y": 495, "width": 453, "height": 27}


def _dialogue_card():
    return _item("dialogue", region=dict(DIALOGUE_REGION), details={
        "rule": "recurring_fixed_text", "whole_film": True, "ad_evidence": ["điểm quảng cáo 0.84"],
        "subtitle_dialogue": {"line_consensus": 0.333},
    })


class SubtitleDialogueItemTests(unittest.TestCase):
    def test_a_visual_logo_inside_it_does_not_bring_the_blur_back(self):
        card = _dialogue_card()
        visual = {
            "id": "v", "category": "visual_logo", "start_seconds": 20.0, "end_seconds": 25.0,
            "suggested_region_source_pixels": {"x": 560, "y": 497, "width": 150, "height": 22},
            "model_evidence": {"vlm_confirmation": "CONFIRMED", "vlm_source": "qwen_local"},
        }
        corroborate_fixed_text_overlays([card, visual])
        self.assertIsNone(card["suggested_decision"])

    def test_merged_cards_keep_their_ad_evidence(self):
        region = {"x": 520, "y": 497, "width": 200, "height": 22}
        plain = _item("plain", region=dict(region), candidate_type=None, start_seconds=1000.0, end_seconds=1003.0)
        policy = _item("policy", region=dict(region), candidate_type=None, start_seconds=1003.5, end_seconds=1006.0,
                       ad_text_evidence=True)
        [merged] = _merge_items([plain, policy], 1.0)
        self.assertTrue(merged["ad_text_evidence"])

    def test_it_never_merges_with_a_card_suggesting_blur(self):
        line = _item("line", region={"x": 420, "y": 470, "width": 400, "height": 24}, suggested="BLUR",
                     details={"rule": "recurring_fixed_text", "whole_film": True, "ad_evidence": ["x"]})
        merged = _merge_items([_dialogue_card(), line], 1.0)
        self.assertEqual(sorted((item["id"], item["suggested_decision"]) for item in merged),
                         [("dialogue", None), ("line", "BLUR")])

    def test_it_absorbs_only_plain_text_cards_without_ad_evidence(self):
        inside = {"x": 520, "y": 497, "width": 200, "height": 22}

        def line(item_id, start, **extra):
            options = {"candidate_type": None, "start_seconds": start, "end_seconds": start + 3.0,
                       "max_score": 0.12, "labels": ["nhung ma anh oi"], **extra}
            return _item(item_id, region=dict(inside), **options)

        cards = [
            _dialogue_card(),
            line("suggested", 1000.0, suggested="BLUR"),
            line("phone", 1010.0, labels=["goi 0901234567 dat ve"]),
            line("scored", 1020.0, max_score=0.52),
            line("evidence", 1030.0, ad_text_evidence=True),
            line("at", 1040.0, labels=["den n@i roi"]),
            line("address", 1050.0, web_address={"tokens": ["example.com"]}),
            {**line("logo", 1060.0), "category": "visual_logo"},
            line("plain", 1200.0),
        ]
        kept = reconcile_persistent_overlay_items(cards, source_duration=DURATION)
        self.assertEqual(sorted(item["id"] for item in kept),
                         ["address", "at", "dialogue", "evidence", "logo", "phone", "scored", "suggested"])

    def test_a_site_line_card_still_absorbs_an_ad_card_inside_it(self):
        site_line = _item("site", region=dict(DIALOGUE_REGION), suggested="BLUR",
                          details={"rule": "recurring_fixed_text", "whole_film": True, "ad_evidence": ["x"]})
        ad_line = _item("ad", region={"x": 520, "y": 497, "width": 200, "height": 22}, suggested="BLUR",
                        candidate_type=None, start_seconds=1000.0, end_seconds=1003.0)
        kept = reconcile_persistent_overlay_items([site_line, ad_line], source_duration=DURATION)
        self.assertEqual([item["id"] for item in kept], ["site"])


class SubtitleDialogueQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for name in ("reports", "work", "input"):
            (self.root / name).mkdir()
        self.source = self.root / "input" / "source.mp4"
        self.source.write_bytes(b"source")

    def tearDown(self):
        self.temporary.cleanup()

    def _queue(self, extra_tracks):
        directory = self.root / "reports" / "job" / "text"
        directory.mkdir(parents=True)
        report = directory / "text-scan.json"
        report.write_text(json.dumps(_payload(_chain(BAND_BOX) + extra_tracks, input=str(self.source)),
                                     ensure_ascii=False), encoding="utf-8")
        return build_review_queue(project_root=self.root, report_paths=[report],
                                  queue_path=self.root / "reports" / "job" / "review-queue.json")

    def _cards_citing(self, queue, track_id):
        return [item for item in queue["items"]
                if any(ref.endswith(f"#track:{track_id}") for ref in item.get("source_candidate_refs") or [])]

    def test_an_ad_line_inside_the_dialogue_card_keeps_its_own_blur_card(self):
        phone = _track(50, 1003.0, "goi 0901234567 dat ve ngay", [400, 372, 560, 390], review_candidate=True,
                       routing="REVIEW_AD_LIKELY", ad_probability=0.71, suggested_decision="BLUR",
                       review_priority="high")
        queue = self._queue([phone])
        dialogue = [item for item in queue["items"] if (item.get("fixed_text_overlay") or {}).get("subtitle_dialogue")]
        self.assertEqual([item["suggested_decision"] for item in dialogue], [None])
        self.assertEqual([item["suggested_decision"] for item in self._cards_citing(queue, 50)], ["BLUR"])
        self.assertNotIn(self._cards_citing(queue, 50)[0]["id"], [item["id"] for item in dialogue])

    def test_ad_lines_without_a_suggestion_inside_it_stay_cards_of_their_own(self):
        # A phone number, an @handle or a policy hit read under the subtitles carries no suggestion of
        # its own: inside a card that suggests nothing it would no longer be seen.
        phone = _track(50, 1003.0, "goi 0901234567 dat ve ngay", [400, 372, 560, 390], review_candidate=True,
                       routing="REVIEW_AD_LIKELY", ad_probability=0.71, review_priority="high")
        policy = _track(51, 1203.0, "ma giam gia hom nay", [400, 372, 560, 390], review_candidate=True,
                        routing="REVIEW_POLICY_OVERRIDE", ad_probability=0.05, policy_hits=["ma giam gia"],
                        review_priority="high")
        queue = self._queue([phone, policy])
        dialogue_ids = [item["id"] for item in queue["items"]
                        if (item.get("fixed_text_overlay") or {}).get("subtitle_dialogue")]
        for track_id in (50, 51):
            with self.subTest(track=track_id):
                [card] = self._cards_citing(queue, track_id)
                self.assertNotIn(card["id"], dialogue_ids)
                self.assertTrue(card["ad_text_evidence"])


if __name__ == "__main__":
    unittest.main()
