"""A site watermark read in many runs reaches the scan's edges (2026-10-16, job 134 rev 2).

Job 134's mark sat under the subtitles to the film's end, but the end credits covered it
from about 2640 s, so its card stopped at 2643.5 s of 2657.2 s and the last 14 s showed it.
A mark read in WATERMARK_ROBUST_RUNS runs or more does not leave the frame: as between its
runs, a first or last moment WATERMARK_BRIDGE_SECONDS or closer to the scan's start or end
reaches it. ``EXAMPLECCOM`` stands for the site, read without its dot.
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.review_workflow import approved_operations, build_review_queue, record_review_decision
from biliflow.subtitle_band import group_repeated_watermarks

DURATION = 2700.0
WATERMARK = [414, 352, 546, 379]


def _run(track_id, start, end):
    return {
        "track_id": track_id, "start_seconds": start, "end_seconds": end,
        "recommended_blur_end_seconds": end + 0.5, "observations": 3, "zone": "subtitle",
        "persistent": False, "review_priority": "high", "max_confidence": 0.7, "union_box": list(WATERMARK),
        "sample_text": ["EXAMPLECCOM"], "ad_probability": 0.4, "semantic_top_label": "credits",
        "review_candidate": True, "routing": "REVIEW_UNCERTAIN", "reason": "Cần kiểm tra",
        "preview": f"text-previews/track-{track_id:05d}.jpg", "policy_hits": [],
    }


def _runs(first_start, last_end, count):
    """``count`` runs of 9 s, evenly spread from ``first_start`` to a last run ending at ``last_end``."""
    step = (last_end - 9.0 - first_start) / (count - 1)
    return [_run(100 + n, round(first_start + n * step, 3), round(first_start + n * step + 9.0, 3))
            for n in range(count)]


def _payload(tracks, **scan):
    payload = {
        "status": "REVIEW_REQUIRED", "duration_seconds": DURATION, "scan_start_seconds": 0.0,
        "scan_duration_seconds": DURATION, "sample_every_seconds": 3.0, "source_size": [1280, 536],
        "analysis_size": [960, 402], "tracks": tracks,
    }
    payload.update(scan)
    return payload


def _moments(payload):
    [group] = [track for track in payload["tracks"] if track.get("candidate_type") == "repeated_watermark"]
    return group, [(m["start_seconds"], m["end_seconds"]) for m in group["detected_intervals"]]


class WatermarkEdgeTests(unittest.TestCase):
    def test_a_frequent_mark_reaches_the_film_start_and_end(self):
        # First reading 50 s in (moment from 47 s), last blur end 2559.5 s: 140.5 s before the end.
        group, moments = _moments(group_repeated_watermarks(_payload(_runs(50.0, 2559.0, 26))))
        self.assertEqual(moments, [(0.0, DURATION)])
        self.assertEqual((group["start_seconds"], group["recommended_blur_end_seconds"]), (0.0, DURATION))
        self.assertEqual(group["repeated_watermark"]["read_span"], {"start_seconds": 47.0, "end_seconds": 2559.5})
        self.assertIn("đầu và cuối vùng quét", group["reason"])
        self.assertIn("0:00–45:00", group["reason"])

    def test_an_edge_more_than_150_s_away_is_not_reached(self):
        group, moments = _moments(group_repeated_watermarks(_payload(_runs(200.0, 2409.0, 23))))
        self.assertEqual(moments, [(197.0, 2409.5)])
        self.assertNotIn("vùng quét", group["reason"])

    def test_150_s_reaches_the_edge_and_150_5_s_does_not(self):
        # Moment from 150.0 s; blur end 2550.0 s, exactly 150 s before the end.
        _, moments = _moments(group_repeated_watermarks(_payload(_runs(153.0, 2549.5, 25))))
        self.assertEqual(moments, [(0.0, DURATION)])
        _, moments = _moments(group_repeated_watermarks(_payload(_runs(153.5, 2549.0, 25))))
        self.assertEqual(moments, [(150.5, 2549.5)])

    def test_a_mark_read_in_fewer_runs_keeps_its_own_time(self):
        # Nine runs: only runs that touch join, and the edges are not reached either.
        _, moments = _moments(group_repeated_watermarks(_payload(_runs(20.0, 2690.0, 9))))
        self.assertEqual(moments[0][0], 17.0)
        self.assertEqual(moments[-1][1], 2690.5)
        self.assertEqual(len(moments), 9)

    def test_the_edges_are_the_scanned_part_of_the_film(self):
        part = {"scan_start_seconds": 600.0, "scan_duration_seconds": 1200.0}
        _, moments = _moments(group_repeated_watermarks(_payload(_runs(650.0, 1709.0, 11), **part)))
        self.assertEqual(moments, [(600.0, 1800.0)])
        # A scan reaching past the film ends at the film's end.
        _, moments = _moments(group_repeated_watermarks(
            _payload(_runs(650.0, 1709.0, 11), duration_seconds=1750.0, **part)))
        self.assertEqual(moments, [(600.0, 1750.0)])

    def test_only_the_near_edge_is_reached_and_named(self):
        # Read from the scan's start (first moment at 0 s): only the end is reached.
        group, moments = _moments(group_repeated_watermarks(_payload(_runs(0.0, 2559.0, 26))))
        self.assertEqual(moments, [(0.0, DURATION)])
        self.assertIn("kéo tới cuối vùng quét", group["reason"])
        self.assertNotIn("đầu", group["reason"])
        group, moments = _moments(group_repeated_watermarks(_payload(_runs(50.0, 2400.0, 24))))
        self.assertEqual(moments, [(0.0, 2400.5)])
        self.assertIn("kéo tới đầu vùng quét", group["reason"])

    def test_separate_moments_keep_their_gap_and_only_the_outer_ones_reach_the_edges(self):
        runs = _runs(50.0, 1000.0, 12) + [dict(run, track_id=200 + n) for n, run in enumerate(_runs(1300.0, 2559.0, 12))]
        _, moments = _moments(group_repeated_watermarks(_payload(runs)))
        self.assertEqual(moments, [(0.0, 1000.5), (1297.0, DURATION)])

    def test_the_film_length_stands_in_for_a_missing_scan_length(self):
        payload = _payload(_runs(50.0, 2559.0, 26))
        del payload["scan_duration_seconds"]
        _, moments = _moments(group_repeated_watermarks(payload))
        self.assertEqual(moments, [(0.0, DURATION)])

    def test_without_a_known_end_only_the_start_is_reached(self):
        payload = _payload(_runs(50.0, 2559.0, 26))
        del payload["duration_seconds"], payload["scan_duration_seconds"]
        _, moments = _moments(group_repeated_watermarks(payload))
        self.assertEqual(moments, [(0.0, 2559.5)])


class WatermarkEdgeQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for name in ("reports", "work", "input"):
            (self.root / name).mkdir()
        self.source = self.root / "input" / "source.mp4"
        self.source.write_bytes(b"source")
        self.queue = self.root / "reports" / "job" / "review-queue.json"
        self.report = self.root / "reports" / "job" / "text" / "text-scan.json"
        self.report.parent.mkdir(parents=True)

    def tearDown(self):
        self.temporary.cleanup()

    def _build(self, payload, **options):
        self.report.write_text(json.dumps(dict(payload, input=str(self.source))), encoding="utf-8")
        return build_review_queue(project_root=self.root, report_paths=[self.report], queue_path=self.queue,
                                  **options)

    @staticmethod
    def _text_cards(payload):
        return [item for item in payload["items"] if item["category"] == "text"]

    def test_one_blur_covers_the_film_to_its_end(self):
        [card] = self._text_cards(self._build(_payload(_runs(50.0, 2559.0, 26))))
        self.assertEqual((card["start_seconds"], card["end_seconds"]), (0.0, DURATION))
        payload = record_review_decision(project_root=self.root, queue_path=self.queue, item_id=card["id"],
                                         decision="BLUR")
        self.assertEqual([(op["start_seconds"], op["end_seconds"]) for op in approved_operations(payload)],
                         [(0.0, DURATION)])

    def test_a_rebuild_carries_the_old_blur_to_the_longer_card(self):
        # The same runs in a film 200 s longer: 340.5 s from the end, the card stops at its last read.
        runs = _runs(50.0, 2559.0, 26)
        [old] = self._text_cards(self._build(_payload(runs, duration_seconds=2900.0, scan_duration_seconds=2900.0)))
        self.assertEqual(old["end_seconds"], 2559.5)
        record_review_decision(project_root=self.root, queue_path=self.queue, item_id=old["id"], decision="BLUR")
        payload = self._build(_payload(runs), keep_decisions=True)
        [card] = self._text_cards(payload)
        self.assertEqual((card["start_seconds"], card["end_seconds"]), (0.0, DURATION))
        self.assertEqual(card["decision"], "BLUR")
        self.assertEqual(card["decision_carried_from"], [old["id"]])
        self.assertEqual([(op["start_seconds"], op["end_seconds"]) for op in approved_operations(payload)],
                         [(0.0, DURATION)])


if __name__ == "__main__":
    unittest.main()
