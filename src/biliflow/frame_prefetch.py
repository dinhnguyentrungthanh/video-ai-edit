"""Bounded frame prefetch; never resamples or drops frames.

FramePrefetch moves only raw-byte reads to a thread. BatchPrefetch also runs the
caller's batch preparation there, with exactly the serial batch boundaries.
"""
from __future__ import annotations

import queue
import subprocess
import threading
from time import perf_counter


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
        _stop_process_and_thread(self.process, self._thread, self._queue, "FFmpeg frame reader")
        for name in ("stdout", "stderr"):
            stream = getattr(self.process, name, None)
            if stream is not None:
                stream.close()
        return False


THREAD_STOP_SECONDS = 5.0


def _stop_process_and_thread(process, thread, work_queue, name: str) -> None:
    # Killing the producer process unblocks a thread inside pipe.read;
    # closing its BufferedReader first could wait on that reader's lock.
    # IteratorPrefetch accepts process=None for producers without a subprocess.
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if thread is not None:
        # Without a process nothing can unblock the producer, and it may still be
        # using what the caller releases next (a VideoCapture, a temporary
        # directory). It stops after its current item, so wait for it, as the
        # serial loop would have waited for that item.
        thread.join(timeout=THREAD_STOP_SECONDS if process is not None else None)
        if thread.is_alive():
            raise RuntimeError(f"{name} did not stop")
        while True:
            try:
                work_queue.get_nowait()
            except queue.Empty:
                break


class BatchPrefetch:
    """Read frames and prepare model batches on one CPU thread for a single consumer.

    While the consumer runs the model on batch n, batch n+1 is read with
    `read_frame` (None at end of stream) and converted by `prepare`. Batches
    hold exactly `batch_size` consecutive frames (the last may be shorter), in
    source order, and `prepare` receives the same frames the serial loop would
    give it; only the thread differs. At most `depth` batches wait in the queue,
    plus one prepared batch waiting to be queued and the consumer's batch.
    Producer errors are re-raised by the consumer after the batches before them.
    `finished` is true when the producer stopped on its own (end of stream or a
    read/prepare error) rather than because the consumer left early.
    Leaving the context terminates a still-running FFmpeg process and joins the thread.
    """

    def __init__(self, process, read_frame, prepare, batch_size, *, depth=2):
        integers = all(isinstance(v, int) and not isinstance(v, bool) for v in (batch_size, depth))
        if not integers or batch_size <= 0 or not 1 <= depth <= 4:
            raise ValueError("Positive batch size and prefetch depth 1..4 required")
        self.process = process
        self.read_frame = read_frame
        self.prepare = prepare
        self.batch_size = batch_size
        self.depth = depth
        self.frames_read = 0
        self.finished = False
        self.read_seconds = 0.0
        self.prepare_seconds = 0.0
        self._queue = queue.Queue(maxsize=depth)
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        self._thread = threading.Thread(target=self._produce, name="biliflow-batch-prefetch", daemon=True)
        self._thread.start()
        return self

    def _put(self, value):
        while not self._stop.is_set():
            try:
                self._queue.put(value, timeout=0.05)
                return
            except queue.Full:
                continue

    def _emit(self, frames):
        first = self.frames_read - len(frames)
        started = perf_counter()
        prepared = self.prepare(frames)
        self.prepare_seconds += perf_counter() - started
        self._put(((frames, list(range(first, first + len(frames))), prepared), None))

    def _produce(self):
        try:
            frames = []
            while not self._stop.is_set():
                started = perf_counter()
                frame = self.read_frame()
                self.read_seconds += perf_counter() - started
                if frame is None:
                    break
                frames.append(frame)
                self.frames_read += 1
                if len(frames) == self.batch_size:
                    self._emit(frames)
                    frames = []
            if frames and not self._stop.is_set():
                self._emit(frames)
            self.finished = not self._stop.is_set()
            self._put((None, None))
        except BaseException as error:
            self.finished = True
            self._put((None, error))

    def __iter__(self):
        while True:
            value, error = self._queue.get()
            if error is not None:
                raise error
            if value is None:
                return
            yield value

    def __exit__(self, *exc):
        self._stop.set()
        _stop_process_and_thread(self.process, self._thread, self._queue, "Batch prefetch thread")
        return False


_END = object()


class IteratorPrefetch:
    """Advance a producer iterator on one CPU thread for a single consumer, in order.

    The iterator (typically a generator that reads an FFmpeg pipe and prepares
    model inputs) is only ever advanced on the producer thread, so the items and
    their order are exactly those of iterating it in the consumer. At most `depth`
    items wait in the queue, plus one produced item waiting to be queued. Errors
    are re-raised to the consumer after the items before them. `finished` is true
    when the iterator ended on its own (exhausted or raised) rather than because
    the consumer left early. Leaving the context terminates a still-running FFmpeg
    process and joins the thread. ``process`` may be None when the producer owns
    no subprocess (for example OpenCV seeks or file reads); the thread is still
    joined before the context exits.
    """

    def __init__(self, process, items, *, depth=16):
        if not isinstance(depth, int) or isinstance(depth, bool) or not 1 <= depth <= 64:
            raise ValueError("Prefetch depth 1..64 required")
        self.process = process
        self.items = items
        self.depth = depth
        self.finished = False
        self._queue = queue.Queue(maxsize=depth)
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        self._thread = threading.Thread(target=self._produce, name="biliflow-iterator-prefetch", daemon=True)
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
            for item in self.items:
                if self._stop.is_set():
                    return
                self._put((item, None))
            self.finished = not self._stop.is_set()
            self._put((_END, None))
        except BaseException as error:
            self.finished = True
            self._put((None, error))

    def __iter__(self):
        while True:
            value, error = self._queue.get()
            if error is not None:
                raise error
            if value is _END:
                return
            yield value

    def __exit__(self, *exc):
        self._stop.set()
        _stop_process_and_thread(self.process, self._thread, self._queue, "Iterator prefetch thread")
        return False
