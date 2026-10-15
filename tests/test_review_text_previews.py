"""Text previews of a review card (session R, job 134 on the phone): display only.

A text card shows first the preview of its track with the longest OCR line (a scrolling line's other previews
caught only its tail), and each text preview carries the name of the zone the scanner printed on it
("REVIEW 58 | subtitle") so the review page can say it is a zone name. Decisions, suggestions, regions, ids and the
number of cards stay exactly what they are without this pass.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow import review_workflow
from biliflow.review_workflow import build_review_queue, order_text_previews


def _track(track_id, start, end, box, texts, zone):
    return {
        "track_id": track_id, "start_seconds": start, "end_seconds": end,
        "recommended_blur_end_seconds": end, "review_candidate": True, "union_box": box,
        "sample_text": texts, "zone": zone, "review_priority": "medium",
        "reason": "Chữ lặp lại nhưng có thể thuộc nội dung phim",
        "preview": f"text-previews/track-{track_id:05d}.jpg",
    }


class TextPreviewOrderTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        for name in ("reports", "input"):
            (self.root / name).mkdir()
        self.source = self.root / "input" / "source.mp4"
        self.source.write_bytes(b"source")
        self.report_dir = self.root / "reports" / "jobs" / "demo" / "text"
        (self.report_dir / "text-previews").mkdir(parents=True)

    def _report(self, tracks):
        for track in tracks:
            (self.report_dir / track["preview"]).write_bytes(b"jpeg")
        report = self.report_dir / "text-scan.json"
        report.write_text(json.dumps({
            "status": "REVIEW_REQUIRED", "input": str(self.source), "duration_seconds": 100,
            "source_size": [1280, 536], "analysis_size": [960, 402], "sample_every_seconds": 3.0,
            "tracks": tracks,
        }), encoding="utf-8")
        return report

    def _build(self, report, name="review-queue.json"):
        return build_review_queue(
            project_root=self.root, report_paths=[report], queue_path=self.root / "reports" / "jobs" / "demo" / name,
        )

    def _preview(self, track_id):
        return f"reports/jobs/demo/text/text-previews/track-{track_id:05d}.jpg"

    def test_merged_card_shows_the_longest_line_first_and_names_each_zone(self):
        # A scrolling line read three times in a row at the top: its tail ("Vie"), the full line, its end.
        report = self._report([
            _track(1, 10.0, 13.0, [400, 16, 960, 43], ["Vie"], "top-right"),
            _track(2, 13.0, 16.0, [0, 18, 960, 42], ["Vietsub và Thuyết Minh được cập nhật nhanh nhất tại TRANG.EXAMPLE"], "top-center"),
            _track(3, 16.0, 19.0, [0, 21, 57, 41], ["ủng hộ-"], "top-left"),
        ])
        queue = self._build(report)
        self.assertEqual(len(queue["items"]), 1)
        item = queue["items"][0]
        self.assertEqual(item["preview_images"], [self._preview(2), self._preview(3), self._preview(1)])
        self.assertEqual(item["text_preview_zones"], {
            self._preview(1): "top-right", self._preview(2): "top-center", self._preview(3): "top-left",
        })

    def test_the_pass_changes_no_decision_suggestion_region_id_or_card_count(self):
        report = self._report([
            _track(1, 10.0, 13.0, [400, 16, 960, 43], ["Vie"], "top-right"),
            _track(2, 13.0, 16.0, [0, 18, 960, 42], ["Vietsub và Thuyết Minh được cập nhật nhanh nhất"], "top-center"),
            _track(3, 40.0, 49.0, [417, 336, 545, 355], ["TRANG.EXAMPLE", "TRANGEXAMPLE"], "subtitle"),
            _track(4, 70.0, 73.0, [300, 200, 450, 240], ["chữ trong cảnh"], "middle-center"),
        ])
        with_pass = self._build(report, "with.json")
        with patch.object(review_workflow, "order_text_previews", lambda items, tracks: items):
            without_pass = self._build(report, "without.json")

        def stable(queue):
            return [
                {key: value for key, value in item.items() if key not in {"preview_images", "text_preview_zones"}}
                for item in [*queue["items"], *queue["advisory_items"]]
            ]

        self.assertEqual(stable(with_pass), stable(without_pass))
        self.assertEqual(with_pass["counts"], without_pass["counts"])
        self.assertEqual(with_pass["status"], without_pass["status"])
        for before, after in zip(without_pass["items"], with_pass["items"]):
            self.assertCountEqual(before["preview_images"], after["preview_images"])

    def test_single_track_card_keeps_its_preview_and_gets_its_zone(self):
        report = self._report([_track(58, 141.0, 150.0, [417, 336, 545, 355], ["TRANGEXAMPLECOM", "TRANG.EXAMPLE"], "subtitle")])
        item = self._build(report)["items"][0]
        self.assertEqual(item["preview_images"], [self._preview(58)])
        self.assertEqual(item["text_preview_zones"], {self._preview(58): "subtitle"})

    def test_only_text_cards_whose_previews_are_all_text_previews_are_reordered(self):
        tracks = {
            "a.jpg": {"zone": "top-right", "longest": 3},
            "b.jpg": {"zone": "top-center", "longest": 40},
        }
        text_card = {"id": "t", "category": "text", "preview_images": ["a.jpg", "b.jpg"]}
        mixed_card = {"id": "m", "category": "text", "preview_images": ["a.jpg", "logo.jpg", "b.jpg"]}
        visual_card = {"id": "v", "category": "visual_logo", "preview_images": ["logo.jpg", "a.jpg", "b.jpg"]}
        plain_card = {"id": "p", "category": "violence", "preview_images": ["x.jpg", "y.jpg"]}
        cards = [text_card, mixed_card, visual_card, plain_card]
        ordered = order_text_previews(cards, tracks)
        self.assertEqual(ordered[0]["preview_images"], ["b.jpg", "a.jpg"])
        self.assertEqual(ordered[1]["preview_images"], ["a.jpg", "logo.jpg", "b.jpg"])
        self.assertEqual(ordered[2]["preview_images"], ["logo.jpg", "a.jpg", "b.jpg"])
        self.assertEqual(ordered[2]["text_preview_zones"], {"a.jpg": "top-right", "b.jpg": "top-center"})
        self.assertNotIn("text_preview_zones", ordered[3])
        # A new list of new cards: the cards passed in are not changed.
        self.assertEqual(text_card["preview_images"], ["a.jpg", "b.jpg"])
        self.assertNotIn("text_preview_zones", text_card)

    def test_equal_lines_keep_their_order(self):
        tracks = {"a.jpg": {"zone": "", "longest": 9}, "b.jpg": {"zone": "", "longest": 9}, "c.jpg": {"zone": "", "longest": 12}}
        ordered = order_text_previews([{"id": "t", "category": "text", "preview_images": ["a.jpg", "b.jpg", "c.jpg"]}], tracks)
        self.assertEqual(ordered[0]["preview_images"], ["c.jpg", "a.jpg", "b.jpg"])
        self.assertNotIn("text_preview_zones", ordered[0])  # no zone name known: no map


if __name__ == "__main__":
    unittest.main()
