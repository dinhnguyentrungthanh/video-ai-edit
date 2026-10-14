import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import psutil

from biliflow import render_watchdog
from biliflow.render_watchdog import (
    STALL_SECONDS,
    RenderStalled,
    _kill,
    _Watch,
    encoded_seconds,
    read_progress_tail,
    run_watched,
    stall_limit,
    stall_message,
)

ROOT = Path(__file__).resolve().parents[1]
# The interpreter itself: a venv's python.exe only starts it as a child, so its own
# reads (what the watch counts) would never show.
PYTHON = getattr(sys, "_base_executable", None) or sys.executable
FFMPEG = Path(os.environ.get("BILIFLOW_FFMPEG") or ROOT / "tools/ffmpeg/bin/ffmpeg.exe")

# A stand-in for FFmpeg: argv[1] is what it does, argv[2] its -progress file.
FAKE_FFMPEG = r'''
import sys, time
mode, progress = sys.argv[1], sys.argv[2]

def block(frame, end=False):
    with open(progress, "a", encoding="utf-8") as handle:
        handle.write(f"frame={frame}\nfps=0.00\nout_time_us={frame * 40000}\n"
                     f"total_size={frame * 100}\nspeed=1x\nprogress={'end' if end else 'continue'}\n")

if mode == "hang":  # job 134: no frame ever
    while True:
        time.sleep(1)
if mode == "same":  # FFmpeg's own way to stall after some frames: the same block every second
    while True:
        block(25)
        time.sleep(0.05)
if mode == "moving":  # argv[3] seconds of frames, then exit code argv[4] with stderr argv[5]
    started, frame = time.monotonic(), 0
    while time.monotonic() - started < float(sys.argv[3]):
        frame += 1
        block(frame)
        time.sleep(0.05)
    block(frame, end=True)
    sys.stderr.buffer.write(sys.argv[5].encode("utf-8"))  # as FFmpeg writes file names
    sys.exit(int(sys.argv[4]))
if mode == "reads":  # decoding through a CUT: argv[3] seconds of reads of argv[4], no frame
    started = time.monotonic()
    while time.monotonic() - started < float(sys.argv[3]):
        with open(sys.argv[4], "rb", buffering=0) as handle:
            handle.read()
        time.sleep(0.05)
    block(1, end=True)
'''


def write_block(path, **values):
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


class ProgressTailTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "render.progress"

    def test_no_file_and_no_complete_block_read_as_none(self):
        self.assertIsNone(read_progress_tail(self.path))
        self.path.write_text("frame=1\nout_time_us=40000\n", encoding="utf-8")  # FFmpeg still writing
        self.assertIsNone(read_progress_tail(self.path))

    def test_the_last_complete_block_wins_and_a_block_being_written_is_ignored(self):
        write_block(self.path, frame=1, out_time_us=40000, total_size=48, progress="continue")
        write_block(self.path, frame=2, out_time_us=80000, total_size=96, progress="continue")
        write_block(self.path, frame=3, out_time_us=1)  # half a block
        self.assertEqual(read_progress_tail(self.path),
                         {"frame": "2", "out_time_us": "80000", "total_size": "96", "progress": "continue"})

    def test_a_long_file_is_read_from_its_end_only(self):
        for frame in range(2000):
            write_block(self.path, frame=frame, out_time_us=frame * 40000, progress="continue")
        self.assertGreater(self.path.stat().st_size, render_watchdog.PROGRESS_TAIL_BYTES)
        values = read_progress_tail(self.path)
        self.assertEqual((values["frame"], values["out_time_us"]), ("1999", str(1999 * 40000)))
        self.assertEqual(encoded_seconds(values), 79.96)

    def test_out_time_before_the_first_packet_is_not_a_time(self):
        self.assertIsNone(encoded_seconds({"frame": "0", "out_time_us": "N/A", "progress": "continue"}))
        self.assertIsNone(encoded_seconds(None))


class StallMessageTests(unittest.TestCase):
    def test_the_message_says_how_long_and_where_in_vietnamese(self):
        self.assertIn("không tiến triển trong 10 phút (chưa xuất được khung hình nào)",
                      stall_message(idle_seconds=STALL_SECONDS, encoded=None, expected_seconds=90))
        self.assertIn("trong 20 phút (đứng ở 1:02:05 / 1:30:00)",
                      stall_message(idle_seconds=stall_limit(longest_cut_seconds=0.0, parallel=True),
                                    encoded=3725.4, expected_seconds=5400))
        message = stall_message(idle_seconds=630, encoded=65, expected_seconds=90)
        self.assertIn("đứng ở 1:05 / 1:30", message)
        self.assertIn("Thử lại bước lỗi", message)
        self.assertIn("chưa có bản xuất nào được ghi", message)


class FakeCounters:
    def __init__(self):
        self.read_bytes = 0


class FakeProcess:
    def __init__(self, counters):
        self.counters = counters

    def io_counters(self):
        return self.counters


class WatchClockTests(unittest.TestCase):
    """_Watch.poll with a clock and an FFmpeg the test moves by hand."""

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.progress = Path(temp.name) / "render.progress"
        self.now = 0.0
        self.counters = FakeCounters()
        process = patch("biliflow.render_watchdog.psutil.Process", return_value=FakeProcess(self.counters))
        process.start()
        self.addCleanup(process.stop)
        self.watch = _Watch(1234, self.progress, 5.0, lambda: self.now)

    def poll_at(self, now):
        self.now = now
        return self.watch.poll()

    def test_quiet_polls_add_up_and_any_sign_of_progress_starts_again(self):
        self.assertTrue(self.watch.reads_counted)
        self.assertEqual(self.poll_at(5), 5)
        self.assertEqual(self.poll_at(10), 10)
        write_block(self.progress, frame=0, out_time_us="N/A", progress="continue")  # the first block
        self.assertEqual(self.poll_at(15), 0)
        write_block(self.progress, frame=0, out_time_us="N/A", progress="continue")  # the same again
        self.assertEqual(self.poll_at(20), 5)
        self.counters.read_bytes += 32768  # decoding through a CUT
        self.assertEqual(self.poll_at(25), 0)
        self.assertEqual(self.poll_at(30), 5)
        write_block(self.progress, frame=1, out_time_us=40000, progress="continue")
        self.assertEqual(self.poll_at(35), 0)

    def test_a_poll_long_after_the_last_one_counts_once(self):
        # The PC slept (or the process was frozen): FFmpeg did not run either.
        self.assertEqual(self.poll_at(5), 5)
        self.assertEqual(self.poll_at(5 + 3600), 10)
        self.assertEqual(self.poll_at(5 + 3600 + 5), 15)

    def test_without_the_read_counter_only_the_progress_counts(self):
        with patch("biliflow.render_watchdog.psutil.Process", side_effect=psutil.AccessDenied()):
            watch = _Watch(1234, self.progress, 5.0, lambda: self.now)
        self.assertFalse(watch.reads_counted)
        self.counters.read_bytes += 32768
        self.now = 5
        self.assertEqual(watch.poll(), 5)

    def test_a_progress_file_that_cannot_be_read_for_a_moment_is_no_progress(self):
        write_block(self.progress, frame=25, out_time_us=1000000, progress="continue")
        self.assertEqual(self.poll_at(5), 0)
        with patch("biliflow.render_watchdog.read_progress_tail", return_value=None):
            self.assertEqual(self.poll_at(10), 5)
        self.assertEqual(self.poll_at(15), 10)  # the same block as before the gap
        self.assertEqual(self.watch.values["frame"], "25")


class StallLimitTests(unittest.TestCase):
    def test_the_limit_grows_with_the_longest_cut_and_doubles_beside_a_scan(self):
        self.assertEqual(STALL_SECONDS, 600.0)
        self.assertEqual(stall_limit(longest_cut_seconds=0.0, parallel=False), 600.0)
        self.assertEqual(stall_limit(longest_cut_seconds=0.0, parallel=True), 1200.0)
        # A 30 min CUT: FFmpeg decodes it without a frame, 4 s allowed per second of it.
        self.assertEqual(stall_limit(longest_cut_seconds=1800.0, parallel=False), 7800.0)
        self.assertEqual(stall_limit(longest_cut_seconds=1800.0, parallel=True), 15600.0)
        self.assertEqual(stall_limit(longest_cut_seconds=-1.0, parallel=False), 600.0)


class KillTests(unittest.TestCase):
    def test_a_killed_ffmpeg_that_never_ends_is_not_waited_for(self):
        class Stuck:
            killed = False
            timeout = None

            def kill(self):
                self.killed = True

            def communicate(self, timeout=None):
                self.timeout = timeout
                raise subprocess.TimeoutExpired("ffmpeg", timeout)

        stuck = Stuck()
        self.assertEqual(_kill(stuck), "")
        self.assertTrue(stuck.killed)
        self.assertEqual(stuck.timeout, render_watchdog.KILL_WAIT_SECONDS)


class RunWatchedTests(unittest.TestCase):
    """run_watched on real child processes (a Python stand-in for FFmpeg)."""

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.fake = self.folder / "fake_ffmpeg.py"
        self.fake.write_text(FAKE_FFMPEG, encoding="utf-8")
        self.progress = self.folder / "render.progress"

    def command(self, *args):
        return [PYTHON, str(self.fake), args[0], str(self.progress), *map(str, args[1:])]

    def watched(self, *args, **options):
        # 3 s: room for the stand-in to start on a loaded machine before its first block.
        options = {"stall_seconds": 3.0, "poll_seconds": 0.1, "expected_seconds": 90.0, **options}
        started = time.monotonic()
        try:
            return run_watched(self.command(*args), progress_path=self.progress, **options)
        finally:
            self.elapsed = time.monotonic() - started

    def test_a_render_that_never_writes_a_frame_is_stopped(self):
        with self.assertRaises(RenderStalled) as raised:
            self.watched("hang")
        self.assertIn("chưa xuất được khung hình nào", str(raised.exception))
        self.assertIsNone(raised.exception.encoded_seconds)
        self.assertGreaterEqual(raised.exception.idle_seconds, 3.0)
        self.assertLess(self.elapsed, 30)
        self.assertIsInstance(raised.exception, RuntimeError)

    def test_a_render_that_repeats_the_same_block_is_stopped_where_it_stood(self):
        with self.assertRaises(RenderStalled) as raised:
            self.watched("same")
        self.assertEqual(raised.exception.encoded_seconds, 1.0)
        self.assertIn("đứng ở 0:01 / 1:30", str(raised.exception))

    def test_a_render_that_keeps_moving_runs_to_its_end_as_subprocess_run_would(self):
        result = self.watched("moving", 5.0, 0, "")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertGreater(self.elapsed, 5.0)  # past the 3 s limit, never stopped
        self.assertEqual(read_progress_tail(self.progress)["progress"], "end")

    def test_its_exit_code_and_error_text_come_back(self):
        result = self.watched("moving", 0.3, 3, "[libx264 @ 0] broken\n")
        self.assertEqual((result.returncode, result.stderr), (3, "[libx264 @ 0] broken\n"))
        self.assertIsInstance(result, subprocess.CompletedProcess)

    def test_its_error_text_is_read_as_utf8(self):
        # A Vietnamese file name in FFmpeg's error: bytes the Windows locale cannot decode.
        result = self.watched("moving", 0.3, 1, "Lỗi: không mở được tập 6.mp4\n")
        self.assertEqual(result.stderr, "Lỗi: không mở được tập 6.mp4\n")

    def test_reading_the_source_counts_as_moving(self):
        source = self.folder / "source.bin"
        source.write_bytes(os.urandom(256 * 1024))
        result = self.watched("reads", 7.0, source)
        self.assertEqual(result.returncode, 0)
        self.assertGreater(self.elapsed, 7.0)  # no frame for longer than the 3 s limit

    def test_without_the_read_counter_only_the_progress_counts(self):
        source = self.folder / "source.bin"
        source.write_bytes(os.urandom(64 * 1024))
        with patch("biliflow.render_watchdog.psutil.Process", side_effect=psutil.AccessDenied()):
            with self.assertRaises(RenderStalled):
                self.watched("reads", 12.0, source)
        self.assertLess(self.elapsed, 11.0)

    @unittest.skipUnless(FFMPEG.exists(), "project FFmpeg is required")
    def test_real_ffmpeg_in_a_graph_of_32_nested_regions_is_stopped(self):
        # The shape that hung job 134's export: each region splits the picture the one before
        # made, 2**32 paths: no PC writes a frame of it.
        parts = ["[0:v]null[c0]"]
        for k in range(32):
            x = 8 + 24 * (k % 12)
            parts.append(f"[c{k}]split[b{k}][s{k}];[s{k}]crop=16:16:{x}:40,null[p{k}];"
                         f"[b{k}][p{k}]overlay={x}:40[c{k + 1}]")
        parts.append("[c32]null[vout]")
        graph = self.folder / "nested.fg"
        graph.write_text(";".join(parts), encoding="utf-8")
        output = self.folder / "out.mp4"
        command = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-stats_period", "1",
                   "-progress", str(self.progress), "-nostats", "-f", "lavfi", "-i",
                   "testsrc2=size=320x180:rate=25:duration=5", "-/filter_complex", str(graph),
                   "-map", "[vout]", "-c:v", "libx264", "-preset", "veryfast", str(output)]
        started = time.monotonic()
        with self.assertRaises(RenderStalled) as raised:
            run_watched(command, progress_path=self.progress, expected_seconds=5.0,
                        stall_seconds=3.0, poll_seconds=0.25)
        self.assertLess(time.monotonic() - started, 60)
        self.assertIn("chưa xuất được khung hình nào", str(raised.exception))
        output.unlink(missing_ok=True)  # FFmpeg is gone: this raises while it holds the file

    @unittest.skipUnless(FFMPEG.exists(), "project FFmpeg is required")
    def test_real_ffmpeg_that_renders_is_left_alone(self):
        output = self.folder / "out.mp4"
        command = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-stats_period", "1",
                   "-progress", str(self.progress), "-nostats", "-re", "-f", "lavfi", "-i",
                   "testsrc2=size=320x180:rate=25:duration=6", "-c:v", "libx264", "-preset", "veryfast",
                   str(output)]
        # Read in real time (-re): 6 s of polls against a 4 s limit. A lavfi source reads
        # no file, so only the progress (a block a second) keeps it going.
        started = time.monotonic()
        result = run_watched(command, progress_path=self.progress, expected_seconds=6.0,
                             stall_seconds=4.0, poll_seconds=0.25)
        self.assertGreater(time.monotonic() - started, 4.0)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertGreater(output.stat().st_size, 0)
        self.assertEqual(read_progress_tail(self.progress)["progress"], "end")


if __name__ == "__main__":
    unittest.main()
