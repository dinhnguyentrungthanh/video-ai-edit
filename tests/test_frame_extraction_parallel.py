"""Localization frame extraction runs FFmpeg in parallel but keeps order and errors."""
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


class ConcurrencyProbe:
    def __init__(self):
        self.lock, self.active, self.peak, self.calls = threading.Lock(), 0, 0, []

    def __call__(self, *args, **kwargs):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.calls.append(args)
        time.sleep(0.02)
        with self.lock:
            self.active -= 1
        return args


class PrefetchTests(unittest.TestCase):
    def test_florence_frames_in_order_with_bounded_prefetch(self):
        from localize_visual_logo_report import FRAME_PREFETCH, _prefetched_frames
        probe = ConcurrencyProbe()
        stamps = [float(i) for i in range(11)]
        got = [f.result()[2] for f in _prefetched_frames(Path("ff"), Path("v"), stamps, extract=probe)]
        self.assertEqual(got, stamps)
        self.assertLessEqual(probe.peak, FRAME_PREFETCH)
        self.assertEqual(len(probe.calls), len(stamps))

    def test_florence_extraction_error_surfaces_and_early_stop_is_clean(self):
        from localize_visual_logo_report import _prefetched_frames

        def failing(ffmpeg, video, stamp):
            if stamp == 2.0:
                raise RuntimeError("ffmpeg failed")
            return stamp
        frames = _prefetched_frames(Path("ff"), Path("v"), [0.0, 1.0, 2.0, 3.0], extract=failing)
        self.assertEqual([next(frames).result(), next(frames).result()], [0.0, 1.0])
        with self.assertRaisesRegex(RuntimeError, "ffmpeg failed"):
            next(frames).result()
        frames.close()


class DinoExtractionTests(unittest.TestCase):
    def test_runs_every_command_with_at_most_four_at_once(self):
        from biliflow.ad_candidate_pipeline import _run_extractions
        probe = ConcurrencyProbe()
        commands = [["ffmpeg", str(i)] for i in range(9)]
        _run_extractions(commands, runner=probe)
        self.assertEqual(sorted(call[0][1] for call in probe.calls), sorted(c[1] for c in commands))
        self.assertLessEqual(probe.peak, 4)
        _run_extractions([], runner=probe)

    def test_failure_propagates(self):
        from biliflow.ad_candidate_pipeline import _run_extractions

        def runner(command, **kwargs):
            if command[1] == "3":
                raise RuntimeError("extract failed")
        with self.assertRaisesRegex(RuntimeError, "extract failed"):
            _run_extractions([["ffmpeg", str(i)] for i in range(6)], runner=runner)


if __name__ == "__main__":
    unittest.main()


class GroundingBoxesTests(unittest.TestCase):
    """transformers 5.x: batch_decode([]) == [''] for a frame with no detection."""

    def processed(self, boxes, scores, labels):
        import torch
        return {"boxes": torch.tensor(boxes, dtype=torch.float32).reshape(-1, 4),
                "scores": torch.tensor(scores, dtype=torch.float16), "text_labels": labels}

    def test_frame_without_detections_has_no_regions(self):
        from biliflow.ad_candidate_pipeline import _grounding_boxes
        self.assertEqual(_grounding_boxes(self.processed([], [], [""])), [])
        self.assertEqual(_grounding_boxes(self.processed([], [], [])), [])

    def test_detections_are_converted_unchanged(self):
        from biliflow.ad_candidate_pipeline import _grounding_boxes
        boxes = _grounding_boxes(self.processed([[10.4, 20.6, 110.2, 60.5]], [0.5], ["logo"]))
        self.assertEqual(boxes, [{"x": 10, "y": 21, "width": 100, "height": 40, "score": 0.5, "label": "logo"}])

    def test_other_length_mismatches_still_fail(self):
        from biliflow.ad_candidate_pipeline import _grounding_boxes
        with self.assertRaises(ValueError):
            _grounding_boxes(self.processed([[0, 0, 5, 5]], [0.4], ["a", "b"]))
