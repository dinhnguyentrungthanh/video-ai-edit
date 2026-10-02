"""Opt-in NSFW shot completion (C1 rule R3) and its window cut detector."""
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import torch

from biliflow.intervals import group_hits, merge_intervals
from biliflow.scanner import complete_nsfw_sequence_context, complete_nsfw_shot_context, scan_nsfw
from biliflow.shot_cuts import (
    FrameChange, detect_cuts, find_window_cuts, frame_changes, merge_windows, window_cuts,
    window_frame_changes,
)

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = ROOT / "tools/ffmpeg/bin/ffmpeg.exe"


def samples_from(score_at, end=60.0, fps=2.0):
    count = int(round(end * fps))
    return [{"timestamp_seconds": round(index / fps, 3), "score": score_at(index / fps)}
            for index in range(count)]


def interval(start, end):
    return {"start_seconds": start, "end_seconds": end, "max_score": 0.99,
            "strongest_frame": "thumbnails/x.jpg", "sample_count": 4}


class RecordingCuts:
    def __init__(self, cuts):
        self.cuts = cuts
        self.calls = []

    def __call__(self, windows):
        self.calls.append(list(windows))
        return list(self.cuts)


def complete(intervals, samples, find_cuts, duration=60.0):
    return complete_nsfw_shot_context(
        intervals, samples, find_cuts, seed_threshold=0.95, context_threshold=0.70,
        padding_seconds=1.0, duration_seconds=duration,
    )


def bed_scene(t):
    """Lead-in below the context threshold (19-26 s), then seeds until 40 s."""
    if 26.0 <= t <= 40.0:
        return 0.99
    return 0.30 if 19.0 <= t < 26.0 else 0.05


class ShotCompletionRuleTests(unittest.TestCase):
    def test_fills_pre_seed_gap_back_to_the_bounding_cut(self):
        cuts = RecordingCuts([12.0, 19.0, 58.0])
        completed, stats = complete([interval(25.0, 41.0)], samples_from(bed_scene), cuts)
        self.assertEqual(completed[0]["start_seconds"], 19.0)
        self.assertEqual(completed[0]["end_seconds"], 41.0)
        context = completed[0]["shot_context"]
        self.assertTrue(context["applied"])
        self.assertEqual(context["start"]["status"], "extended")
        self.assertEqual(context["start"]["extension_seconds"], 6.0)
        self.assertEqual(context["start"]["shot_start_seconds"], 19.0)
        self.assertEqual(context["start"]["shot_end_seconds"], 46.0)  # no cut within 20 s
        self.assertEqual(context["start"]["seed_sample_count"], 29)
        self.assertEqual(context["end"]["status"], "no_cut_within_reach")
        self.assertEqual((context["input_start_seconds"], context["input_end_seconds"]), (25.0, 41.0))
        # One call; windows only around the two edges (both hold >= 5 seeds).
        self.assertEqual(cuts.calls, [[(16.5, 46.5), (19.5, 49.5)]])
        self.assertEqual(stats["extended_start_edge_count"], 1)
        self.assertEqual(stats["extended_end_edge_count"], 0)
        self.assertEqual(stats["added_interval_seconds"], 6.0)

    def test_end_edge_moves_forward_to_the_next_cut(self):
        def scene(t):
            return 0.99 if 16.0 <= t <= 30.0 else (0.60 if 30.0 < t < 34.0 else 0.05)

        completed, _ = complete([interval(15.0, 31.0)], samples_from(scene), RecordingCuts([5.0, 34.0]))
        context = completed[0]["shot_context"]
        self.assertEqual(completed[0]["start_seconds"], 15.0)
        self.assertEqual(context["start"]["status"], "no_cut_within_reach")  # 5.0 is 10 s out
        self.assertEqual(completed[0]["end_seconds"], 34.0)
        self.assertEqual(context["end"]["status"], "extended")
        self.assertEqual(context["end"]["extension_seconds"], 3.0)
        # No cut within 20 s before the last supporting sample: the shot starts 20 s back.
        self.assertEqual((context["end"]["shot_start_seconds"], context["end"]["shot_end_seconds"]), (10.0, 34.0))

    def test_rejects_shot_below_the_context_share_gate(self):
        def sparse(t):
            return 0.99 if 26.0 <= t < 30.0 else 0.05  # 8 seeds in a 27 s shot

        completed, stats = complete([interval(25.0, 31.0)], samples_from(sparse), RecordingCuts([19.0]))
        edge = completed[0]["shot_context"]["start"]
        self.assertEqual(edge["status"], "evidence_gate_failed")
        self.assertEqual(edge["seed_sample_count"], 8)
        self.assertLess(edge["context_share"], 0.40)
        self.assertEqual(completed[0]["start_seconds"], 25.0)
        self.assertFalse(completed[0]["shot_context"]["applied"])
        self.assertEqual(stats["extended_interval_count"], 0)

    def test_rejects_shot_with_fewer_than_five_seeds(self):
        def scene(t):
            if 19.0 <= t < 26.0:
                return 0.75  # all context-level, share 100 %
            return 0.99 if 26.0 <= t <= 40.0 else 0.05

        # The shot 19-28 holds only four seeds; the seeds after the 28 s cut do not count.
        completed, _ = complete([interval(25.0, 41.0)], samples_from(scene), RecordingCuts([19.0, 28.0]))
        edge = completed[0]["shot_context"]["start"]
        self.assertEqual(edge["status"], "evidence_gate_failed")
        self.assertEqual((edge["shot_start_seconds"], edge["shot_end_seconds"]), (19.0, 28.0))
        self.assertEqual(edge["seed_sample_count"], 4)
        self.assertEqual(edge["context_share"], 1.0)
        self.assertEqual(completed[0]["start_seconds"], 25.0)

    def test_respects_the_eight_second_limit_on_both_edges(self):
        samples = samples_from(bed_scene)
        for cut, expected in ((16.9, 25.0), (17.0, 17.0)):
            completed, _ = complete([interval(25.0, 41.0)], samples, RecordingCuts([cut]))
            self.assertEqual(completed[0]["start_seconds"], expected, cut)

        def long_tail(t):
            return 0.99 if 26.0 <= t <= 40.0 else (0.50 if 40.0 < t < 52.0 else 0.05)

        samples = samples_from(long_tail)
        for cut, expected in ((49.1, 41.0), (49.0, 49.0)):
            completed, _ = complete([interval(25.0, 41.0)], samples, RecordingCuts([cut]))
            self.assertEqual(completed[0]["end_seconds"], expected, cut)
        self.assertEqual(
            complete([interval(25.0, 41.0)], samples, RecordingCuts([49.1]))[0][0]["shot_context"]["end"]["status"],
            "no_cut_within_reach",
        )

    def test_never_creates_intervals_and_skips_decoding_without_seeds(self):
        def elsewhere(t):
            return 0.99 if 40.0 <= t < 55.0 else 0.05  # a strong shot with no interval

        cuts = RecordingCuts([39.0, 56.0])
        completed, stats = complete([], samples_from(elsewhere), cuts)
        self.assertEqual(completed, [])
        self.assertEqual(cuts.calls, [])

        weak = interval(4.0, 7.0)
        completed, stats = complete([weak], samples_from(elsewhere), cuts)
        self.assertEqual(len(completed), 1)
        self.assertEqual((completed[0]["start_seconds"], completed[0]["end_seconds"]), (4.0, 7.0))
        self.assertEqual(completed[0]["shot_context"]["start"]["status"], "too_few_seeds_nearby")
        self.assertEqual(completed[0]["shot_context"]["end"]["status"], "too_few_seeds_nearby")
        self.assertEqual(cuts.calls, [])
        self.assertEqual(stats["decoded_edge_count"], 0)

    def test_cut_inside_interval_and_video_edges_leave_interval_unchanged(self):
        completed, _ = complete([interval(25.0, 41.0)], samples_from(bed_scene), RecordingCuts([25.5, 40.5]))
        self.assertEqual(completed[0]["shot_context"]["start"]["status"], "cut_inside_interval")
        self.assertEqual(completed[0]["shot_context"]["end"]["status"], "cut_inside_interval")
        self.assertFalse(completed[0]["shot_context"]["applied"])

        def everywhere(t):
            return 0.99

        completed, _ = complete([interval(0.0, 60.0)], samples_from(everywhere), RecordingCuts([30.0]))
        self.assertEqual(completed[0]["shot_context"]["start"]["status"], "at_video_start")
        self.assertEqual(completed[0]["shot_context"]["end"]["status"], "at_video_end")

    def test_input_intervals_are_not_mutated(self):
        original = interval(25.0, 41.0)
        before = json.dumps(original, sort_keys=True)
        complete([original], samples_from(bed_scene), RecordingCuts([19.0]))
        self.assertEqual(json.dumps(original, sort_keys=True), before)


class CutDetectorTests(unittest.TestCase):
    def test_histogram_and_adaptive_content_rules_skip_repeated_frames(self):
        changes = []
        for index in range(50):
            luma, content, histogram = 1.0, 1.0, 0.01
            if index == 10:
                luma, content = 0.1, 0.1  # repeated frame (24 fps carried at 25 fps)
            elif index == 11:
                content = 2.5  # frame after the repeat: two frames of motion
            elif index == 20:
                content, histogram = 9.0, 0.05  # adaptive content cut
            elif index in (35, 36):
                histogram = 0.2  # histogram cut; the second is within 0.2 s
            elif 39 <= index <= 45:
                content = 9.0 if index == 42 else 4.0  # busy motion: 9 < 3 x mean
            changes.append(FrameChange(round(index * 0.04, 2), luma, content, histogram))
        self.assertEqual(detect_cuts(changes), [0.8, 1.4])

    def test_frame_changes_find_a_cut_between_two_synthetic_shots(self):
        import numpy as np

        frames = []
        for index in range(30):
            frame = np.zeros((66, 160, 3), np.uint8)
            if index < 15:
                frame[:, :, 0] = 40
                frame[:, index * 4:index * 4 + 20, 0] = 90  # moving bar, dark red shot
            else:
                frame[:, :, 2] = 60
                frame[:, index * 4:index * 4 + 20, 1] = 120  # different shot
            frames.append((round(index * 0.04, 2), frame))
        changes = frame_changes(frames)
        self.assertEqual(len(changes), 29)
        self.assertEqual(detect_cuts(changes), [0.6])

    def test_merge_windows_joins_overlapping_and_near_windows(self):
        self.assertEqual(
            merge_windows([(30.0, 40.0), (0.0, 5.0), (5.5, 8.0), (35.0, 45.0), (9.5, 9.0)]),
            [(0.0, 8.0), (30.0, 45.0)],
        )


@unittest.skipUnless(FFMPEG.exists(), "project FFmpeg is required")
class WindowDecodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.video = Path(cls.temp.name) / "two-shots.mkv"
        subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=s=192x80:r=25:d=2",
            "-f", "lavfi", "-i", "color=c=navy:s=192x80:r=25:d=2", "-filter_complex",
            "[0]format=yuv420p[a];[1]format=yuv420p[b];[a][b]concat=n=2:v=1:a=0",
            "-c:v", "ffv1", str(cls.video)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_cut_time_is_on_the_scan_timeline_for_any_window_start(self):
        self.assertEqual(window_cuts(FFMPEG, self.video, 0.5, 3.5, threads=1), [2.0])
        self.assertEqual(window_cuts(FFMPEG, self.video, 1.3, 2.6, threads=1), [2.0])
        self.assertEqual(window_cuts(FFMPEG, self.video, 0.0, 1.9, threads=1), [])


class ParallelWindowCutsTests(unittest.TestCase):
    """find_window_cuts decodes merged windows two at a time with the serial result."""

    WINDOWS = [(50.0, 60.0), (0.0, 10.0), (12.0, 20.0), (25.0, 30.0), (40.0, 41.0), (19.5, 22.0)]

    def test_parallel_windows_give_the_serial_cuts(self):
        calls = []
        active = {"now": 0, "peak": 0}
        guard = threading.Lock()

        def fake_window_cuts(ffmpeg, video, start, end, *, settings, threads):
            with guard:
                active["now"] += 1
                active["peak"] = max(active["peak"], active["now"])
            try:
                time.sleep(0.02 + 0.01 * ((int(start) * 7) % 5))  # windows finish out of order
                calls.append((start, end, threading.current_thread().name, threads))
                return [start + 1.0, round((start + end) / 2, 3), 5.0]  # 5.0 repeats across windows
            finally:
                with guard:
                    active["now"] -= 1

        with mock.patch("biliflow.shot_cuts.window_cuts", side_effect=fake_window_cuts):
            serial = find_window_cuts(FFMPEG, Path("video.mp4"), self.WINDOWS, workers=1)
            serial_calls, calls[:] = list(calls), []
            serial_peak, active["peak"] = active["peak"], 0
            parallel = find_window_cuts(FFMPEG, Path("video.mp4"), self.WINDOWS)
        merged = merge_windows(self.WINDOWS)
        self.assertEqual(parallel, serial)
        self.assertEqual(serial, sorted({cut for a, b in merged for cut in (a + 1.0, round((a + b) / 2, 3), 5.0)}))
        self.assertEqual(sorted(call[:2] for call in calls), sorted(call[:2] for call in serial_calls))
        self.assertEqual([call[:2] for call in serial_calls], merged)  # each window decoded once
        self.assertEqual({call[3] for call in calls}, {2})  # FFmpeg keeps threads=2 per window
        self.assertEqual({call[2] for call in serial_calls}, {"MainThread"})
        self.assertEqual(serial_peak, 1)
        self.assertEqual(active["peak"], 2)

    def test_first_failing_window_in_window_order_is_raised(self):
        def fake_window_cuts(ffmpeg, video, start, end, *, settings, threads):
            if start == 12.0:
                time.sleep(0.15)
                raise RuntimeError("FFmpeg cut decode failed: window 12")
            if start == 25.0:
                raise RuntimeError("FFmpeg cut decode failed: window 25")
            time.sleep(0.05)
            return [start + 1.0]

        windows = [(0.0, 10.0), (12.0, 20.0), (25.0, 30.0), (40.0, 41.0)]
        for workers in (1, 2):
            with self.subTest(workers=workers), \
                    mock.patch("biliflow.shot_cuts.window_cuts", side_effect=fake_window_cuts), \
                    self.assertRaisesRegex(RuntimeError, "window 12"):
                find_window_cuts(FFMPEG, Path("video.mp4"), windows, workers=workers)

    def test_invalid_worker_count_is_rejected(self):
        for workers in (0, -1, True, 2.0):
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                find_window_cuts(FFMPEG, Path("video.mp4"), [(0.0, 1.0)], workers=workers)

    @unittest.skipUnless(FFMPEG.exists(), "project FFmpeg is required")
    def test_real_decode_in_parallel_matches_serial_decode(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "three-shots.mkv"
            subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc2=s=192x80:r=25:d=3",
                "-f", "lavfi", "-i", "color=c=navy:s=192x80:r=25:d=3",
                "-f", "lavfi", "-i", "testsrc=s=192x80:r=25:d=3", "-filter_complex",
                "[0]format=yuv420p[a];[1]format=yuv420p[b];[2]format=yuv420p[c];[a][b][c]concat=n=3:v=1:a=0",
                "-c:v", "ffv1", str(video)], check=True)
            windows = [(0.5, 1.2), (2.5, 3.5), (5.5, 6.5), (7.8, 8.5)]
            self.assertEqual(merge_windows(windows), windows)
            serial = find_window_cuts(FFMPEG, video, windows, workers=1)
            parallel = find_window_cuts(FFMPEG, video, windows, workers=2)
            serial_changes = [window_frame_changes(FFMPEG, video, a, b) for a, b in windows]
            results = {}

            def decode(index):
                results[index] = window_frame_changes(FFMPEG, video, *windows[index])

            threads = [threading.Thread(target=decode, args=(index,)) for index in range(len(windows))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(serial, [3.0, 6.0])
        self.assertEqual(parallel, serial)
        self.assertEqual([results[index] for index in range(len(windows))], serial_changes)
        self.assertTrue(all(serial_changes))


class _FakeStdout:
    """Raw 448x448 RGB frames whose first two bytes carry the frame index."""

    def __init__(self, count):
        self.count = count
        self.index = 0
        self.buffer = b""

    def read(self, size):
        if not self.buffer:
            if self.index >= self.count:
                return b""
            frame = bytearray(448 * 448 * 3)
            frame[0:2] = self.index.to_bytes(2, "little")
            self.buffer = bytes(frame)
            self.index += 1
        chunk, self.buffer = self.buffer[:size], self.buffer[size:]
        return chunk

    def close(self):
        pass


class _FakeProcess:
    def __init__(self, count):
        self.stdout = _FakeStdout(count)
        self.stderr = io.BytesIO(b"")

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0

    def terminate(self):
        pass

    def kill(self):
        pass


class ScanNsfwShotCompletionTests(unittest.TestCase):
    """scan_nsfw with a fake decoder and model: default off leaves intervals untouched."""

    SAMPLE_FPS = 2.0
    DURATION = 40.0

    @staticmethod
    def score(t):
        if 14.0 <= t < 30.0:
            return 0.99
        return 0.30 if 10.0 <= t < 14.0 else 0.05

    def run_scan(self, **kwargs):
        frames = int(self.DURATION * self.SAMPLE_FPS)
        scores = [self.score(index / self.SAMPLE_FPS) for index in range(frames)]

        def processor(images, return_tensors):
            indices = [int.from_bytes(image.tobytes()[0:2], "little") for image in images]
            return {"pixel_values": torch.tensor([[scores[i]] for i in indices], dtype=torch.float64)}

        class Model:
            config = SimpleNamespace(id2label={0: "safe", 1: "porn"})

            def to(self, device):
                return self

            def eval(self):
                return self

            def __call__(self, pixel_values):
                p = pixel_values.clamp(1e-9, 1 - 1e-9)
                return SimpleNamespace(logits=torch.log(torch.cat([1 - p, p], dim=1)))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reports").mkdir()
            (root / "model").mkdir()
            for name in ("input.mp4", "ffmpeg.exe", "ffprobe.exe"):
                (root / name).write_bytes(b"x")
            with mock.patch("biliflow.scanner.require_capacity"), \
                    mock.patch("biliflow.scanner.probe_video", return_value={}), \
                    mock.patch("biliflow.scanner.duration_seconds", return_value=self.DURATION), \
                    mock.patch("biliflow.scanner.AutoImageProcessor.from_pretrained",
                               return_value=processor), \
                    mock.patch("biliflow.scanner.AutoModelForImageClassification.from_pretrained",
                               return_value=Model()), \
                    mock.patch("biliflow.scanner.subprocess.Popen",
                               side_effect=lambda *a, **k: _FakeProcess(frames)):
                payload = scan_nsfw(
                    project_root=root, input_path=root / "input.mp4",
                    report_dir=root / "reports" / "adult", model_path=root / "model",
                    ffmpeg_path=root / "ffmpeg.exe", ffprobe_path=root / "ffprobe.exe",
                    sample_fps=self.SAMPLE_FPS, batch_size=8, top_k_candidates=3,
                    threshold=0.95, merge_gap_seconds=2.0, padding_seconds=1.0,
                    device_name="cpu", content_style="live_action", **kwargs,
                )
        return payload, scores

    def expected_default_intervals(self, scores):
        hits = [{"frame_index": i, "timestamp_seconds": round(i / self.SAMPLE_FPS, 3), "score": s,
                 "predicted_label": "porn",
                 "thumbnail": f"thumbnails/frame-{i:08d}-{i / self.SAMPLE_FPS:.3f}s.jpg"}
                for i, s in enumerate(scores) if s >= 0.95]
        samples = [{"frame_index": i, "timestamp_seconds": round(i / self.SAMPLE_FPS, 3), "score": s}
                   for i, s in enumerate(scores)]
        intervals = merge_intervals(group_hits(hits, 2.0, 1.0, self.DURATION), 3.0)
        return complete_nsfw_sequence_context(
            intervals, samples, context_threshold=0.70, context_seconds=8.0,
            padding_seconds=1.0, duration_seconds=self.DURATION,
        )

    def test_default_off_leaves_payload_intervals_byte_identical(self):
        with mock.patch("biliflow.shot_cuts.find_window_cuts",
                        side_effect=AssertionError("must not decode")) as finder:
            payload, scores = self.run_scan()
            explicit, _ = self.run_scan(shot_completion=False)
        finder.assert_not_called()
        expected = json.dumps(self.expected_default_intervals(scores))
        self.assertEqual(json.dumps(payload["intervals"]), expected)
        self.assertEqual(json.dumps(explicit["intervals"]), expected)
        self.assertNotIn("shot_context", expected)
        block = payload["shot_completion"]
        self.assertFalse(block["enabled"])
        self.assertEqual(block["status"], "DISABLED")
        self.assertNotIn("shot_completion", payload["metrics"]["performance"]["phases"])

    def test_opt_in_moves_start_to_the_cut_and_reports_it(self):
        with mock.patch("biliflow.shot_cuts.find_window_cuts", return_value=[10.0, 30.0]) as finder:
            payload, scores = self.run_scan(shot_completion=True)
        finder.assert_called_once()
        default = self.expected_default_intervals(scores)
        self.assertEqual([(i["start_seconds"], i["end_seconds"]) for i in default], [(13.0, 30.5)])
        self.assertEqual([(i["start_seconds"], i["end_seconds"]) for i in payload["intervals"]],
                         [(10.0, 30.5)])
        interval_context = payload["intervals"][0]["shot_context"]
        self.assertEqual(interval_context["start"]["status"], "extended")
        self.assertEqual(interval_context["end"]["status"], "cut_inside_interval")
        block = payload["shot_completion"]
        self.assertTrue(block["enabled"])
        self.assertEqual(block["status"], "APPLIED")
        self.assertEqual(block["extended_interval_count"], 1)
        self.assertEqual((block["seed_threshold"], block["context_threshold"]), (0.95, 0.70))
        self.assertEqual(block["maximum_extension_seconds"], 8.0)
        self.assertEqual((block["minimum_seed_samples"], block["minimum_context_share"]), (5, 0.40))
        self.assertEqual(payload["sequence_completion"]["completed_interval_count"], 1)

    def test_source_hash_runs_beside_the_scan_and_equals_the_file_digest(self):
        with mock.patch("biliflow.shot_cuts.find_window_cuts", return_value=[10.0, 30.0]), \
                mock.patch("biliflow.scanner.sha256_file",
                           side_effect=AssertionError("no serial re-hash after the scan")):
            payload, _ = self.run_scan(shot_completion=True)
        self.assertEqual(payload["input_sha256"], hashlib.sha256(b"x").hexdigest())
        self.assertEqual(payload["shot_completion"]["status"], "APPLIED")
        self.assertIn("source_hash", payload["metrics"]["performance"]["phases"])

    def test_opt_in_decode_failure_keeps_sequence_intervals(self):
        with mock.patch("biliflow.shot_cuts.find_window_cuts", side_effect=RuntimeError("decode broke")):
            payload, scores = self.run_scan(shot_completion=True)
        self.assertEqual(payload["status"], "COMPLETED")
        self.assertEqual(json.dumps(payload["intervals"]), json.dumps(self.expected_default_intervals(scores)))
        self.assertEqual(payload["shot_completion"]["status"], "FAILED")
        self.assertIn("decode broke", payload["shot_completion"]["error"])


class CliShotCompletionTests(unittest.TestCase):
    def run_cli(self, *extra):
        from biliflow.cli import main

        with tempfile.TemporaryDirectory() as directory:
            argv = ["biliflow", "--project-root", directory, "scan",
                    "--input", "input.mp4", "--report-dir", "reports/result", *extra]
            with mock.patch.object(sys, "argv", argv), mock.patch(
                "biliflow.cli.scan_nsfw", return_value={"status": "COMPLETED"}
            ) as scanner, mock.patch("biliflow.cli.ensure_model_allowed"), redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
        return scanner.call_args.kwargs

    def test_flag_is_off_by_default_and_opt_in(self):
        self.assertIs(self.run_cli()["shot_completion"], False)
        self.assertIs(self.run_cli("--shot-completion")["shot_completion"], True)


if __name__ == "__main__":
    unittest.main()
