"""Bounded raw-byte prefetch; never resamples, drops or transforms frames."""
from __future__ import annotations

import queue
import subprocess
import threading


class FramePrefetch:
    """Single consumer, one optional CPU reader thread for an owned FFmpeg pipe.

    Depth zero delegates to the original reader. The queue has at most `depth`
    frames, plus one producer read and the consumer's frame. The budget reserves
    three additional frame buffers for consumer and read_exact transient copies;
    it is not a cap on FFmpeg, model, Python or image-processing memory.
    """

    def __init__(self, process, frame_bytes, read_exact, *, depth=0, max_buffer_bytes=32 * 1024**2):
        if frame_bytes <= 0 or not isinstance(depth, int) or not 0 <= depth <= 4:
            raise ValueError("Positive frame size and prefetch depth 0..4 required")
        if max_buffer_bytes < 0:
            raise ValueError("Buffer budget cannot be negative")
        self.process = process
        self.frame_bytes = frame_bytes
        self.read_exact = read_exact
        self.depth = min(depth, max(0, max_buffer_bytes // frame_bytes - 3))
        self._queue = queue.Queue(maxsize=max(1, self.depth))
        self._stop = threading.Event()
        self._thread = None
        self._eof = False

    def __enter__(self):
        if self.depth:
            self._thread = threading.Thread(target=self._produce, name="biliflow-frame-reader", daemon=True)
            self._thread.start()
        return self

    def _put(self, value):
        while not self._stop.is_set():
            try:
                self._queue.put(value, timeout=0.05)
                return
            except queue.Full:
                continue

    def _produce(self):
        try:
            while not self._stop.is_set():
                value = self.read_exact(self.process.stdout, self.frame_bytes)
                self._put((value, None))
                if not value:
                    return
        except BaseException as error:
            self._put((None, error))

    def read(self):
        if self._eof:
            return b""
        if not self.depth:
            value = self.read_exact(self.process.stdout, self.frame_bytes)
        else:
            value, error = self._queue.get()
            if error is not None:
                raise error
        if not value:
            self._eof = True
        return value

    def __exit__(self, *exc):
        self._stop.set()
        # Killing the producer process unblocks a thread inside pipe.read;
        # closing its BufferedReader first could wait on that reader's lock.
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise RuntimeError("FFmpeg frame reader did not stop")
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
        for name in ("stdout", "stderr"):
            stream = getattr(self.process, name, None)
            if stream is not None:
                stream.close()
        return False
