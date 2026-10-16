"""Web addresses the reviewer blurred, remembered for later review builds (session D, item 5).

``EXAMPLE.COM`` stands for the real source site everywhere here.
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.ad_text_memory import (
    AD_TEXT_MEMORY_PATH,
    load_ad_text_memory,
    memory_key,
    readmit_remembered_tracks,
)
from biliflow.review_workflow import (
    build_review_queue,
    bulk_accept_suggested_decisions,
    record_review_decision,
)

DURATION = 1800.0


def _track(track_id, start, end, texts, box, **extra):
    track = {
        "track_id": track_id, "start_seconds": start, "end_seconds": end,
        "recommended_blur_end_seconds": end + 0.5, "observations": len(texts),
        "zone": "subtitle", "persistent": False, "review_priority": "low", "max_confidence": 0.6,
        "union_box": box, "sample_text": list(texts), "ad_probability": 0.04,
        "semantic_top_label": "subtitle", "review_candidate": False, "routing": "LIKELY_SUBTITLE",
        "reason": "Phụ đề", "preview": f"text-previews/track-{track_id:05d}.jpg",
    }
    track.update(extra)
    return track


class MemoryKeyTests(unittest.TestCase):
    def test_one_key_for_every_way_ocr_reads_the_address(self):
        for token in ("EXAMPLE.COM", "Example com", "EXAMPLECOM", "www.example.com", "https://example.com",
                      "EXAMPLE. COM", "EXAMPLE,COM"):
            with self.subTest(token=token):
                self.assertEqual(memory_key(token), "examplecom")

    def test_too_short_or_without_a_name_is_no_key(self):
        for token in ("www", "https://", "ab.vn", ""):
            with self.subTest(token=token):
                self.assertIsNone(memory_key(token))


class AdTextMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for name in ("reports", "work", "input", "state"):
            (self.root / name).mkdir()
        self.source = self.root / "input" / "source.mp4"
        self.source.write_bytes(b"source")
        self.queue_path = self.root / "reports" / "job" / "review-queue.json"
        self.memory_path = self.root / AD_TEXT_MEMORY_PATH

    def tearDown(self):
        self.temporary.cleanup()

    def _build(self, tracks, queue_path=None, omitted=None, **options):
        directory = self.root / "reports" / "job" / "text"
        directory.mkdir(parents=True, exist_ok=True)
        report = directory / "text-scan.json"
        report.write_text(json.dumps({
            "status": "REVIEW_REQUIRED", "input": str(self.source), "duration_seconds": DURATION,
            "scan_start_seconds": 0.0, "scan_duration_seconds": DURATION, "sample_every_seconds": 3.0,
            "source_size": [1280, 640], "analysis_size": [960, 480], "tracks": tracks,
            **({"omitted_tracks": omitted} if omitted is not None else {}),
        }, ensure_ascii=False), encoding="utf-8")
        return build_review_queue(
            project_root=self.root, report_paths=[report], queue_path=queue_path or self.queue_path, **options,
        )

    def _web_card(self):
        payload = self._build([_track(1, 300.0, 303.0, ["EXAMPLE.COM"], [400, 420, 560, 445],
                                      routing="LIKELY_SCENE_TEXT")])
        [card] = [item for item in payload["items"] if item["category"] == "text"]
        return card

    def test_blur_on_a_web_address_card_remembers_the_folded_address(self):
        card = self._web_card()
        payload = record_review_decision(project_root=self.root, queue_path=self.queue_path,
                                         item_id=card["id"], decision="BLUR")
        [record] = load_ad_text_memory(self.root)["records"]
        self.assertEqual(record["key"], "examplecom")
        self.assertEqual(record["blur_decisions"], 1)
        [decided] = [item for item in payload["items"] if item["id"] == card["id"]]
        self.assertEqual(decided["ad_text_memory"]["remembered"], ["examplecom"])

    def test_accepting_the_blur_suggestions_at_once_remembers_the_address_too(self):
        self._web_card()
        payload = bulk_accept_suggested_decisions(project_root=self.root, queue_path=self.queue_path,
                                                  review_filter="text")
        self.assertEqual([r["key"] for r in load_ad_text_memory(self.root)["records"]], ["examplecom"])
        [decided] = [item for item in payload["items"] if item["category"] == "text"]
        self.assertEqual(decided["decision"], "BLUR")
        self.assertEqual(decided["ad_text_memory"]["remembered"], ["examplecom"])

    def test_keep_and_cut_remember_nothing(self):
        for decision in ("KEEP", "CUT"):
            with self.subTest(decision=decision):
                card = self._web_card()
                record_review_decision(project_root=self.root, queue_path=self.queue_path,
                                       item_id=card["id"], decision=decision)
                self.assertFalse(self.memory_path.exists())

    def test_a_blur_on_text_without_a_web_address_remembers_nothing(self):
        payload = self._build([_track(1, 300.0, 303.0, ["Giảm giá 50% hôm nay"], [400, 420, 760, 445],
                                      routing="REVIEW_AD_LIKELY", review_candidate=True, ad_probability=0.8)])
        [card] = [item for item in payload["items"] if item["category"] == "text"]
        record_review_decision(project_root=self.root, queue_path=self.queue_path, item_id=card["id"],
                               decision="BLUR")
        self.assertFalse(self.memory_path.exists())

    def test_nothing_removes_a_remembered_address(self):
        card = self._web_card()
        record_review_decision(project_root=self.root, queue_path=self.queue_path, item_id=card["id"],
                               decision="BLUR")
        payload = record_review_decision(project_root=self.root, queue_path=self.queue_path,
                                         item_id=card["id"], decision="KEEP")
        [record] = load_ad_text_memory(self.root)["records"]
        self.assertEqual(record["key"], "examplecom")
        [decided] = [item for item in payload["items"] if item["id"] == card["id"]]
        self.assertNotIn("ad_text_memory", decided)
        # A rebuild leaves the memory as it is.
        self._build([_track(1, 300.0, 303.0, ["EXAMPLE.COM"], [400, 420, 560, 445])],
                    use_studio_logo_memory=True)
        self.assertEqual([r["key"] for r in load_ad_text_memory(self.root)["records"]], ["examplecom"])

    def test_an_unreadable_memory_refuses_the_blur_before_the_decision_is_written(self):
        card = self._web_card()
        before = self.queue_path.read_bytes()
        self.memory_path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            record_review_decision(project_root=self.root, queue_path=self.queue_path, item_id=card["id"],
                                   decision="BLUR")
        self.assertEqual(self.queue_path.read_bytes(), before)
        self.assertEqual(self.memory_path.read_text(encoding="utf-8"), "{not json")

    def _remember_example(self):
        self.memory_path.write_text(json.dumps({"schema_version": 1, "records": [
            {"key": "examplecom", "first_remembered_at": "2026-10-15T10:00:00+07:00",
             "last_remembered_at": "2026-10-15T10:00:00+07:00", "blur_decisions": 1},
        ]}), encoding="utf-8")

    def test_a_later_build_suggests_blur_for_a_remembered_address_the_rule_does_not_read(self):
        self._remember_example()
        # Lower case and a dash: not a web address to web_address_tokens.
        tracks = [_track(7, 600.0, 603.0, ["xem tai examplecom nhe"], [300, 420, 700, 445]),
                  _track(8, 612.0, 615.0, ["EXAMPLE - COM"], [400, 420, 560, 445], routing="LIKELY_SCENE_TEXT")]
        payload = self._build(tracks, use_studio_logo_memory=True)
        cards = [item for item in payload["items"] if item["category"] == "text"]
        self.assertEqual(len(cards), 2)
        for card in cards:
            self.assertEqual(card["suggested_decision"], "BLUR")
            self.assertEqual(card["web_address"]["remembered"], ["examplecom"])
        self.assertEqual(payload["ad_text_memory"], {"used": True, "remembered_addresses": 1,
                                                     "cards_matched": 2})

    def test_without_the_users_memories_a_remembered_address_changes_nothing(self):
        self._remember_example()
        payload = self._build([_track(7, 600.0, 603.0, ["xem tai examplecom nhe"], [300, 420, 700, 445])])
        self.assertEqual([item for item in payload["items"] if item["category"] == "text"], [])
        self.assertEqual(payload["ad_text_memory"]["used"], False)

    def test_ordinary_text_sharing_the_name_without_its_ending_is_not_matched(self):
        self._remember_example()
        payload = self._build([_track(7, 600.0, 603.0, ["Ví dụ: example nhé"], [300, 420, 700, 445])],
                              use_studio_logo_memory=True)
        self.assertEqual([item for item in payload["items"] if item["category"] == "text"], [])

    def test_a_remembered_address_the_scan_left_past_its_limit_gets_its_card(self):
        self._remember_example()
        omitted = [_omitted(_track(7, 600.0, 603.0, ["xem tai examplecom nhe"], [300, 420, 700, 445])),
                   _omitted(_track(8, 612.0, 615.0, ["Một dòng chữ"], [300, 420, 700, 445]))]
        payload = self._build([], omitted=omitted, use_studio_logo_memory=True)
        [card] = [item for item in payload["items"] if item["category"] == "text"]
        self.assertEqual(card["suggested_decision"], "BLUR")
        self.assertEqual(card["web_address"]["remembered"], ["examplecom"])
        self.assertEqual((card["start_seconds"] <= 600.0, card["end_seconds"] >= 603.0), (True, True))

    def test_without_the_users_memories_tracks_past_the_scan_limit_stay_out(self):
        self._remember_example()
        omitted = [_omitted(_track(7, 600.0, 603.0, ["xem tai examplecom nhe"], [300, 420, 700, 445]))]
        payload = self._build([], omitted=omitted)
        self.assertEqual([item for item in payload["items"] if item["category"] == "text"], [])

    def test_an_unreadable_memory_does_not_stop_a_build(self):
        self.memory_path.write_text("{not json", encoding="utf-8")
        payload = self._build([_track(7, 600.0, 603.0, ["xem tai examplecom nhe"], [300, 420, 700, 445])],
                              use_studio_logo_memory=True)
        self.assertEqual([item for item in payload["items"] if item["category"] == "text"], [])
        self.assertIn("error", payload["ad_text_memory"])


def _omitted(track):
    """A track the text scan left past its track limit: kept in the report without a preview."""
    return {key: value for key, value in track.items() if key != "preview"}


class ReadmitRememberedTracksTests(unittest.TestCase):
    def _payload(self):
        return {"tracks": [_track(1, 10.0, 13.0, ["Phụ đề"], [0, 0, 10, 10])],
                "omitted_tracks": [_omitted(_track(2, 900.0, 903.0, ["xem tai examplecom nhe"], [300, 420, 700, 445])),
                                   _omitted(_track(3, 910.0, 913.0, ["Một dòng chữ"], [300, 420, 700, 445]))]}

    def test_a_track_past_the_scan_limit_holding_a_remembered_address_comes_back(self):
        payload = self._payload()
        output = readmit_remembered_tracks(payload, ("examplecom",))
        self.assertEqual([track["track_id"] for track in output["tracks"]], [1, 2])
        self.assertEqual([track["track_id"] for track in output["omitted_tracks"]], [3])
        self.assertEqual(output["readmitted_track_ids"], [2])
        self.assertEqual([track["track_id"] for track in payload["tracks"]], [1])
        self.assertEqual(len(payload["omitted_tracks"]), 2)

    def test_nothing_to_take_back_returns_the_same_payload(self):
        for name, keys, payload in (
            ("no memory", (), self._payload()),
            ("no match", ("othersitecom",), self._payload()),
            ("no omitted tracks", ("examplecom",), {"tracks": []}),
            ("name without its ending", ("examplecom",), {"tracks": [], "omitted_tracks": [
                _omitted(_track(4, 1.0, 2.0, ["example nhe"], [0, 0, 10, 10]))]}),
        ):
            with self.subTest(name):
                self.assertIs(readmit_remembered_tracks(payload, keys), payload)


if __name__ == "__main__":
    unittest.main()
