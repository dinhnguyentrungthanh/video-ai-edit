"""A watch on the export's FFmpeg render: it stops a render that no longer moves.

job 134 (2026-10-14): FFmpeg 9.0.1 read 2.2 MB of the source, then spun one
core and never wrote a frame. The render stage's heartbeat only says that its
process lives, so the export stayed RUNNING and held the queue for good.

FFmpeg counts as moving while either of these changes:
- the latest block of its ``-progress`` file (frame, out_time, size). FFmpeg
  writes a block every second, the same values again when nothing moves, and
  none before its first encoded frame;
- the bytes it has read (psutil's I/O counters). The start before the first
  frame, decoding through a long CUT and the +faststart rewrite after the
  last frame all read without writing a new frame.
When neither changes for the stall limit, FFmpeg is killed and RenderStalled
raised. It is a RuntimeError of its own: never an NVENC failure, so never an
x264 retry nor an export handed back to the main lane, where the same graph
would stall again.

The limit (stall_limit), from the quiet stretches measured in healthy exports
(docs/evidence/export-regions/README.md): a 1080p export of 60 regions with
CUTs of 30 and 20 s went at most 1.0 s without a new frame or a read on an
idle PC, and 98.3 s with the CPU at 100 % (four more x264 encodes beside it,
20 times slower). STALL_SECONDS is six times that, plus CUT_DECODE_FACTOR
seconds per second of the plan's longest CUT, which FFmpeg decodes without
a frame and, once it has read ahead, without a read. A1b's second lane
shares the CPU with a scan and waits PARALLEL_FACTOR times as long.
"""
from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import psutil

STALL_SECONDS = 600.0
CUT_DECODE_FACTOR = 4.0
PARALLEL_FACTOR = 2.0
PARALLEL_STALL_SECONDS = STALL_SECONDS * PARALLEL_FACTOR
POLL_SECONDS = 5.0
# A poll that comes this much later than asked means that this process did not
# run (the PC slept, or the process was frozen): FFmpeg did not run either, so
# the gap counts as one poll only.
SUSPENDED_GAP_SECONDS = 30.0
PROGRESS_TAIL_BYTES = 8192
_MOVING_KEYS = ("frame", "out_time_us", "out_time_ms", "total_size")


class RenderStalled(RuntimeError):
    """The watch stopped FFmpeg: no frame and no read for the stall limit."""

    def __init__(self, message: str, *, idle_seconds: float, encoded_seconds: float | None, stderr: str):
        super().__init__(message)
        self.idle_seconds = idle_seconds
        self.encoded_seconds = encoded_seconds
        self.stderr = stderr


def stall_limit(*, longest_cut_seconds: float, parallel: bool) -> float:
    """Seconds without a frame or a read after which a render counts as stalled."""
    limit = STALL_SECONDS + CUT_DECODE_FACTOR * max(0.0, longest_cut_seconds)
    return limit * PARALLEL_FACTOR if parallel else limit


def read_progress_tail(path: Path) -> dict[str, str] | None:
    """The values of the last complete block of an FFmpeg ``-progress`` file; None before the first one."""
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            start = max(0, size - PROGRESS_TAIL_BYTES)
            handle.seek(start)
            data = handle.read()
    except OSError:
        return None
    lines = data.decode("utf-8", "replace").splitlines()
    if start:
        lines = lines[1:]  # it may begin inside a line
    ends = [index for index, line in enumerate(lines) if line.startswith("progress=")]
    if not ends:
        return None
    begin = ends[-2] + 1 if len(ends) > 1 else 0
    values: dict[str, str] = {}
    for line in lines[begin:ends[-1] + 1]:
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    return values


def encoded_seconds(values: dict[str, str] | None) -> float | None:
    """The output time of a progress block, None when FFmpeg has not written one yet."""
    if not values:
        return None
    for key in ("out_time_us", "out_time_ms"):
        try:
            return max(0.0, int(values[key]) / 1_000_000)
        except (KeyError, ValueError):
            continue
    return None


def _clock_text(seconds: float) -> str:
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, seconds_left = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{seconds_left:02d}" if hours else f"{minutes}:{seconds_left:02d}"


def stall_message(*, idle_seconds: float, encoded: float | None, expected_seconds: float) -> str:
    minutes = max(1, round(idle_seconds / 60))
    if encoded is None or encoded <= 0:
        where = "chưa xuất được khung hình nào"
    else:
        where = f"đứng ở {_clock_text(encoded)} / {_clock_text(expected_seconds)}"
    return (
        f"Xuất video bị dừng: FFmpeg không tiến triển trong {minutes} phút ({where}). "
        "BiliFlow đã dừng FFmpeg để hàng đợi chạy tiếp; chưa có bản xuất nào được ghi. "
        "Bấm “Thử lại bước lỗi” để xuất lại; nếu lại dừng như vậy, hãy gửi nhật ký bước xuất để kiểm tra."
    )


class _Watch:
    """What FFmpeg has done since the last poll, and for how long it has done nothing."""

    def __init__(self, pid: int, progress_path: Path, poll_seconds: float, clock: Callable[[], float]):
        self._progress_path = progress_path
        self._poll_seconds = poll_seconds
        self._clock = clock
        try:
            self._process: psutil.Process | None = psutil.Process(pid)
        except psutil.Error:
            self._process = None
        self._read_bytes = self._reads()
        self._moving: tuple | None = None
        self._last = clock()
        self.idle_seconds = 0.0
        self.values: dict[str, str] | None = None

    @property
    def reads_counted(self) -> bool:
        return self._read_bytes is not None

    def _reads(self) -> int | None:
        if self._process is None:
            return None
        try:
            return int(self._process.io_counters().read_bytes)
        except (psutil.Error, OSError, AttributeError):
            return None

    def poll(self) -> float:
        """Seconds without a sign of progress, after one more look."""
        now = self._clock()
        gap = now - self._last
        self._last = now
        if gap > SUSPENDED_GAP_SECONDS:
            gap = self._poll_seconds
        values = read_progress_tail(self._progress_path)
        moving = None if values is None else tuple(values.get(key) for key in _MOVING_KEYS)
        reads = self._reads()
        progressed = moving != self._moving or (
            reads is not None and self._read_bytes is not None and reads > self._read_bytes
        )
        self._moving = moving
        self._read_bytes = reads
        if values is not None:
            self.values = values
        self.idle_seconds = 0.0 if progressed else self.idle_seconds + gap
        return self.idle_seconds


def run_watched(
    command: list[str], *, progress_path: Path, expected_seconds: float, stall_seconds: float,
    poll_seconds: float = POLL_SECONDS, clock: Callable[[], float] = time.monotonic,
) -> subprocess.CompletedProcess:
    """subprocess.run(command, capture_output=True, text=True) for an FFmpeg render, stopped when it stalls.

    Raises RenderStalled once FFmpeg, ``stall_seconds`` without a sign of
    progress (stall_limit), is killed.
    """
    with subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) as process:
        try:
            watch = _Watch(process.pid, progress_path, poll_seconds, clock)
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=poll_seconds)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if watch.poll() >= stall_seconds:
                    process.kill()
                    _, stderr = process.communicate()
                    encoded = encoded_seconds(watch.values)
                    raise RenderStalled(
                        stall_message(idle_seconds=watch.idle_seconds, encoded=encoded,
                                      expected_seconds=expected_seconds),
                        idle_seconds=watch.idle_seconds, encoded_seconds=encoded, stderr=stderr or "",
                    )
        except RenderStalled:
            raise
        except BaseException:
            process.kill()
            raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
