import io
import subprocess
import sys
import threading
import unittest

from biliflow.frame_prefetch import FramePrefetch
from biliflow.textscan import _read_exact


class FinishedProcess:
    def __init__(self, stream):
        self.stdout = stream

    def poll(self):
        return 0


class FramePrefetchTests(unittest.TestCase):
    def test_bytes_order_eof_and_partial_frame_match_serial(self):
        data = bytes(range(255)) * 3
        results = []
        for depth in (0, 1, 2, 4):
            process = FinishedProcess(io.BytesIO(data))
            with FramePrefetch(process, 64, _read_exact, depth=depth) as reader:
                frames = []
                while value := reader.read():
                    frames.append(value)
                self.assertEqual(reader.read(), b"")
            results.append(frames)
        self.assertTrue(all(value == results[0] for value in results))
        self.assertEqual(b"".join(results[0]), data)
        self.assertEqual(len(results[0][-1]), len(data) % 64)

    def test_producer_error_is_propagated_after_prior_frames(self):
        error = OSError("broken pipe")
        calls = []
        def read(*args):
            if calls:
                raise error
            calls.append(True)
            return b"frame"
        with FramePrefetch(FinishedProcess(None), 5, read, depth=2) as reader:
            self.assertEqual(reader.read(), b"frame")
            with self.assertRaises(OSError) as caught:
                reader.read()
            self.assertIs(caught.exception, error)

    def test_backpressure_and_early_exit_release_reader(self):
        reached = threading.Event()
        calls = []
        def read(*args):
            calls.append(1)
            if len(calls) == 3:
                reached.set()
            return b"frame"
        reader = FramePrefetch(FinishedProcess(None), 5, read, depth=2)
        with reader:
            self.assertTrue(reached.wait(2))
            # Two queued frames plus one pending producer frame. A full queue
            # must block further reads rather than dropping/overwriting frames.
            self.assertEqual(len(calls), 3)
        self.assertFalse(reader._thread.is_alive())
        self.assertTrue(reader._queue.empty())

    def test_memory_budget_falls_back_to_original_reader(self):
        process = FinishedProcess(io.BytesIO(b"abcd"))
        with FramePrefetch(process, 4, _read_exact, depth=4, max_buffer_bytes=12) as reader:
            self.assertEqual(reader.depth, 0)
            self.assertIsNone(reader._thread)
            self.assertEqual(reader.read(), b"abcd")

    def test_cancellation_unblocks_real_pipe_and_preserves_consumer_error(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], stdout=subprocess.PIPE)
        try:
            reader = FramePrefetch(process, 4, _read_exact, depth=2)
            with self.assertRaisesRegex(ValueError, "consumer error"):
                with reader:
                    raise ValueError("consumer error")
            self.assertIsNotNone(process.poll())
            self.assertFalse(reader._thread.is_alive())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()


if __name__ == "__main__":
    unittest.main()
