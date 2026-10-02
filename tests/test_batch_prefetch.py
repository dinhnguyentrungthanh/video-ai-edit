import io
import subprocess
import sys
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow.cli import main
from biliflow.frame_prefetch import BatchPrefetch


class FinishedProcess:
    stdout = None

    def poll(self):
        return 0


def reader_of(values):
    iterator = iter(values)
    return lambda: next(iterator, None)


def serial_batches(frames, batch_size, prepare):
    output = []
    for start in range(0, len(frames), batch_size):
        chunk = frames[start:start + batch_size]
        output.append((chunk, list(range(start, start + len(chunk))), prepare(chunk)))
    return output


class BatchPrefetchTests(unittest.TestCase):
    def test_batches_indices_and_prepared_values_match_serial_loop(self):
        frames = [f"frame-{index}" for index in range(10)]
        prepare = lambda chunk: "|".join(chunk)
        for batch_size in (1, 3, 4, 10, 16):
            for depth in (1, 2, 4):
                with self.subTest(batch_size=batch_size, depth=depth):
                    prefetch = BatchPrefetch(FinishedProcess(), reader_of(frames), prepare, batch_size, depth=depth)
                    with prefetch:
                        batches = list(prefetch)
                    self.assertEqual(batches, serial_batches(frames, batch_size, prepare))
                    self.assertEqual(prefetch.frames_read, 10)
                    self.assertTrue(prefetch.finished)

    def test_empty_stream_yields_nothing(self):
        prefetch = BatchPrefetch(FinishedProcess(), reader_of([]), list, 8)
        with prefetch:
            self.assertEqual(list(prefetch), [])
        self.assertEqual(prefetch.frames_read, 0)

    def test_read_error_arrives_after_the_batches_before_it(self):
        error = RuntimeError("Incomplete raw frame")
        values = iter(["a", "b", "c"])

        def read():
            value = next(values, None)
            if value is None:
                raise error
            return value

        prefetch = BatchPrefetch(FinishedProcess(), read, list, 2)
        received = []
        with self.assertRaises(RuntimeError) as caught:
            with prefetch:
                for batch, indices, _ in prefetch:
                    received.append((batch, indices))
        self.assertIs(caught.exception, error)
        self.assertEqual(received, [(["a", "b"], [0, 1])])  # "c" never forms a batch
        self.assertTrue(prefetch.finished)
        self.assertEqual(prefetch.frames_read, 3)  # like the serial loop, "c" was read

    def test_prepare_error_is_propagated(self):
        def prepare(chunk):
            raise ValueError("transform failed")

        with self.assertRaisesRegex(ValueError, "transform failed"):
            with BatchPrefetch(FinishedProcess(), reader_of(["a"]), prepare, 1) as prefetch:
                list(prefetch)

    def test_backpressure_and_early_exit_release_the_thread(self):
        reached = threading.Event()
        count = []

        def read():
            count.append(1)
            if len(count) == 3:
                reached.set()
            return "frame"  # endless stream

        prefetch = BatchPrefetch(FinishedProcess(), read, list, 1, depth=2)
        with prefetch:
            self.assertTrue(reached.wait(2))
            # Two queued batches plus one being put: a full queue blocks the reader.
            self.assertEqual(len(count), 3)
        self.assertFalse(prefetch._thread.is_alive())
        self.assertFalse(prefetch.finished)  # stopped by the consumer, not by the stream

    def test_consumer_error_terminates_real_pipe(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], stdout=subprocess.PIPE)
        try:
            prefetch = BatchPrefetch(process, lambda: process.stdout.read(4) or None, list, 2)
            with self.assertRaisesRegex(ValueError, "consumer error"):
                with prefetch:
                    raise ValueError("consumer error")
            self.assertIsNotNone(process.poll())
            self.assertFalse(prefetch._thread.is_alive())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()

    def test_invalid_settings_are_rejected(self):
        for batch_size, depth in ((0, 2), (8, 0), (8, 5), (True, 2)):
            with self.subTest(batch_size=batch_size, depth=depth), self.assertRaises(ValueError):
                BatchPrefetch(FinishedProcess(), reader_of([]), list, batch_size, depth=depth)


class AnimationPrecisionCliTests(unittest.TestCase):
    def run_cli(self, *extra):
        with TemporaryDirectory() as directory:
            argv = ["biliflow", "--project-root", directory, "scan-animation-safety",
                    "--input", "input.mp4", "--report-dir", "reports/result", *extra]
            with patch.object(sys, "argv", argv), patch(
                "biliflow.cli.scan_animation_safety", return_value={"status": "COMPLETED"}
            ) as scanner, patch("biliflow.cli.ensure_model_allowed"), redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
            return scanner.call_args.kwargs

    def test_precision_defaults_to_fp32_and_passes_fp16(self):
        self.assertEqual(self.run_cli()["precision"], "fp32")
        self.assertEqual(self.run_cli("--precision", "fp16")["precision"], "fp16")

    def test_scanner_rejects_unknown_precision_before_any_work(self):
        from biliflow.animation_safety_scanner import scan_animation_safety
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reports").mkdir()
            video = root / "video.mp4"
            video.write_bytes(b"x")
            for precision, device in (("bf16", "cuda"), ("fp16", "cpu")):
                with self.subTest(precision=precision, device=device), self.assertRaises(ValueError):
                    scan_animation_safety(project_root=root, input_path=video, report_dir=root / "reports/out",
                                          model_path=root, ffmpeg_path=video, ffprobe_path=video,
                                          device_name=device, precision=precision)


if __name__ == "__main__":
    unittest.main()


class IteratorPrefetchTests(unittest.TestCase):
    def test_items_and_order_match_serial_iteration(self):
        from biliflow.frame_prefetch import IteratorPrefetch
        source = [("violence", i) if i % 3 else ("gore", i) for i in range(50)] + [None, 0, ""]
        for depth in (1, 4, 16):
            with self.subTest(depth=depth):
                prefetch = IteratorPrefetch(FinishedProcess(), iter(source), depth=depth)
                with prefetch:
                    self.assertEqual(list(prefetch), source)  # falsy items are delivered too
                self.assertTrue(prefetch.finished)

    def test_generator_error_arrives_after_prior_items(self):
        from biliflow.frame_prefetch import IteratorPrefetch

        def events():
            yield "a"
            yield "b"
            raise RuntimeError("Incomplete packed frame")

        received = []
        prefetch = IteratorPrefetch(FinishedProcess(), events(), depth=4)
        with self.assertRaisesRegex(RuntimeError, "Incomplete packed frame"):
            with prefetch:
                for item in prefetch:
                    received.append(item)
        self.assertEqual(received, ["a", "b"])
        self.assertTrue(prefetch.finished)

    def test_early_exit_stops_an_endless_producer(self):
        from biliflow.frame_prefetch import IteratorPrefetch
        count = []

        def endless():
            while True:
                count.append(1)
                yield len(count)

        prefetch = IteratorPrefetch(FinishedProcess(), endless(), depth=2)
        with prefetch:
            self.assertEqual(next(iter(prefetch)), 1)
        self.assertFalse(prefetch._thread.is_alive())
        self.assertFalse(prefetch.finished)
        self.assertLessEqual(len(count), 5)  # bounded read-ahead: queue depth plus one pending item

    def test_producer_without_a_process_is_joined_on_exit(self):
        from biliflow.frame_prefetch import IteratorPrefetch
        produced = []

        def items():
            for index in range(100):
                produced.append(threading.current_thread().name)
                yield index

        prefetch = IteratorPrefetch(None, items(), depth=2)
        with prefetch:
            self.assertEqual(list(prefetch), list(range(100)))
        self.assertTrue(prefetch.finished)
        self.assertEqual(set(produced), {"biliflow-iterator-prefetch"})
        self.assertFalse(prefetch._thread.is_alive())

        early = IteratorPrefetch(None, items(), depth=2)
        with early:
            self.assertEqual(next(iter(early)), 0)
        self.assertFalse(early._thread.is_alive())
        self.assertFalse(early.finished)

    def test_slow_item_without_a_process_is_awaited_beyond_the_stop_timeout(self):
        # A producer without a subprocess (OpenCV seeks, JPEG writes, the VLM processor)
        # cannot be unblocked; leaving must wait for its current item, never return
        # while it may still use the capture or the temporary directory.
        from biliflow import frame_prefetch
        from biliflow.frame_prefetch import IteratorPrefetch
        events = []

        def items():
            yield 0
            events.append("item-1-started")
            threading.Event().wait(0.3)  # longer than the patched stop timeout
            events.append("item-1-done")
            yield 1

        with patch.object(frame_prefetch, "THREAD_STOP_SECONDS", 0.02):
            prefetch = IteratorPrefetch(None, items(), depth=1)
            with prefetch:
                self.assertEqual(next(iter(prefetch)), 0)
                while not events:
                    threading.Event().wait(0.005)
            self.assertFalse(prefetch._thread.is_alive())
            self.assertEqual(events, ["item-1-started", "item-1-done"])

            class RunningProcess:
                def poll(self):
                    return 0  # already exited; nothing to terminate

            bounded = IteratorPrefetch(RunningProcess(), items(), depth=1)
            with self.assertRaisesRegex(RuntimeError, "did not stop"):
                with bounded:
                    self.assertEqual(next(iter(bounded)), 0)
                    while len(events) < 3:
                        threading.Event().wait(0.005)
            bounded._thread.join()

    def test_invalid_depth_is_rejected(self):
        from biliflow.frame_prefetch import IteratorPrefetch
        for depth in (0, 65, True, 2.0):
            with self.subTest(depth=depth), self.assertRaises(ValueError):
                IteratorPrefetch(FinishedProcess(), iter(()), depth=depth)


class LiveSafetyPrecisionTests(unittest.TestCase):
    def run_cli(self, *extra):
        with TemporaryDirectory() as directory:
            argv = ["biliflow", "--project-root", directory, "scan-live-safety",
                    "--input", "input.mp4", "--report-dir", "reports/result", *extra]
            payload = {"gore": {"status": "COMPLETED", "frames_scanned": 1},
                       "violence": {"status": "COMPLETED", "frames_scanned": 4}}
            with patch.object(sys, "argv", argv), patch(
                "biliflow.cli.scan_live_safety", return_value=payload
            ) as scanner, patch("biliflow.cli.ensure_model_allowed"), redirect_stdout(io.StringIO()):
                main()
            return scanner.call_args.kwargs

    def test_violence_precision_defaults_to_fp32_and_passes_fp16(self):
        self.assertEqual(self.run_cli()["violence_precision"], "fp32")
        self.assertEqual(self.run_cli("--violence-precision", "fp16")["violence_precision"], "fp16")

    def test_scanner_rejects_unknown_precision_before_any_work(self):
        from biliflow.live_safety_scanner import scan_live_safety
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reports").mkdir()
            video = root / "video.mp4"
            video.write_bytes(b"x")
            for precision, device in (("bf16", "cuda"), ("fp16", "cpu")):
                with self.subTest(precision=precision, device=device), self.assertRaises(ValueError):
                    scan_live_safety(project_root=root, input_path=video, report_dir=root / "reports/out",
                                     gore_model_path=root, violence_model_path=root, ffmpeg_path=video,
                                     ffprobe_path=video, device_name=device, violence_precision=precision)
