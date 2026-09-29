"""Frame-parallel logo routing must reproduce the serial routing exactly."""
import unittest

import numpy as np

from biliflow.brand_memory import _relative_crop, perceptual_hash
from biliflow.performance import ScanPerformance
from biliflow.visual_logo_scanner import RoutingPool, _iter_routed_frames, scan_visual_logos


def synthetic_frames(count=14, seed=7):
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 255, (180, 320, 3), dtype=np.uint8)
    frames = []
    for index in range(count):
        frame = base.copy()
        # A persistent corner mark plus a moving block exercises the
        # previous-frame correlation and scene-change paths.
        frame[8:40, 250:310] = (240, 30, 30)
        x = 20 + index * 9
        frame[100:150, x:x + 40] = rng.integers(0, 255, (50, 40, 3), dtype=np.uint8)
        if index == 6:
            frame = rng.integers(0, 255, (180, 320, 3), dtype=np.uint8)
        frames.append((index * 2.0, frame))
    return frames


def memory_for(frame):
    box = [0.75, 0.0, 0.25, 0.25]
    return [{"decision": "BLUR", "relative_box": box, "memory_class": "brand", "key": "mark",
             "phash": perceptual_hash(_relative_crop(frame, box))}]


class RoutingPoolTests(unittest.TestCase):
    def test_parallel_routing_matches_serial_exactly_and_in_order(self):
        frames = synthetic_frames()
        memory = memory_for(frames[0][1])
        serial = list(_iter_routed_frames(iter(frames), memory, ScanPerformance()))
        self.assertTrue(any("brand_memory" in f for _, _, f in serial))
        for workers, threads in ((2, 1), (3, None)):
            pool = RoutingPool(workers, memory, opencv_threads=threads, in_flight_per_worker=2)
            try:
                performance = ScanPerformance()
                parallel = list(_iter_routed_frames(iter(frames), memory, performance, pool))
            finally:
                pool.close()
            self.assertEqual([t for t, _, _ in parallel], [t for t, _, _ in serial])
            self.assertTrue(all(a is b for (_, a, _), (_, b) in zip(parallel, frames)))
            self.assertEqual([f for _, _, f in parallel], [f for _, _, f in serial])
            self.assertEqual(pool.metrics()["frames"], len(frames))
            phases = performance.snapshot()["phases"]
            self.assertIn("logo_routing_wait", phases)
            self.assertNotIn("logo_feature_extraction", phases)

    def test_worker_errors_propagate(self):
        pool = RoutingPool(2, [])
        try:
            with self.assertRaises(Exception):
                list(pool.iterate(iter([(0.0, np.zeros((4, 4), dtype=np.float64))]), ScanPerformance()))
        finally:
            pool.close()

    def test_worker_count_is_validated(self):
        for value in (1, 9, True, 2.0):
            with self.assertRaises(ValueError):
                RoutingPool(value, [])
        from pathlib import Path
        for value in (0, 9, True, 2.0):
            with self.assertRaisesRegex(ValueError, "routing_workers"):
                scan_visual_logos(project_root=Path("."), input_path=Path("missing.mp4"),
                                  report_dir=Path("reports/x"), model_path=Path("models"),
                                  ffmpeg_path=Path("."), ffprobe_path=Path("."), routing_workers=value)

    def test_cli_routing_workers_default_serial(self):
        from biliflow.cli import build_parser
        parser = build_parser()
        args = ["scan-visual-logo", "--input", "movie.mp4", "--report-dir", "reports/x"]
        self.assertEqual(parser.parse_args(args).routing_workers, 1)
        self.assertEqual(parser.parse_args(args + ["--routing-workers", "4"]).routing_workers, 4)
        with self.assertRaises(SystemExit):
            parser.parse_args(args + ["--routing-workers", "9"])


if __name__ == "__main__":
    unittest.main()
