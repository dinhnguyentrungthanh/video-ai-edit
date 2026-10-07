"""Shared parts of the source transfers (``download_media_file``, ``download_hls``).

- ``ProgressMeter``: real bytes on disk (a failed attempt's bytes are dropped), fragments done, a speed
  over the last seconds, at most one report per ``PROGRESS_EMIT_SECONDS``;
- ``LogBuffer``: masked log lines, flushed about once a second;
- ``probe_streams``: ffprobe on a local file only (``-protocol_whitelist file``);
- ``remux_to_mp4``: the project's FFmpeg copies the streams (``-c copy``, never a re-encode) from the
  parts written to its stdin in the given order, so no joined copy is ever written: the peak on disk
  is the parts plus the MP4;
- small JSON files written atomically inside the task folder; ``media-done.json`` names the finished
  file of a source, so a task stopped while it was checked or moved into input is not fetched again.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import psutil

from biliflow.download_http import Cancelled, Interruptible
from biliflow.download_runner import LOG_FLUSH_SECONDS, PROGRESS_EMIT_SECONDS, Progress, kill_process_tree, mask_line
from biliflow.download_source_types import SourceError

SPEED_WINDOW_SECONDS = 5.0
REMUX_TIMEOUT_SECONDS = 1800.0
PROBE_TIMEOUT_SECONDS = 60
COPY_CHUNK_BYTES = 1024 * 1024
FINISHED_NAME = "media-done.json"
RENAME_ATTEMPTS = 5  # a scanner or the indexer holding a file just written
RENAME_WAIT_SECONDS = 1.0
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ProgressMeter:
    """Thread-safe progress of one transfer. ``downloaded_bytes`` = finished pieces + pieces in flight."""

    def __init__(self, on_progress: Callable[[Progress], None], *, basis: str = "bytes",
                 total_bytes: int | None = None, fragments_total: int | None = None,
                 clock: Callable[[], float] = time.monotonic, emit_seconds: float = PROGRESS_EMIT_SECONDS):
        self._on_progress = on_progress
        self._lock = threading.Lock()
        self._emit_lock = threading.Lock()
        self._clock = clock
        self._emit_seconds = emit_seconds
        self.basis = basis
        self.total_bytes = total_bytes
        self.fragments_total = fragments_total
        self.fragments_done = 0
        self.committed = 0
        self._inflight: dict[Any, int] = {}
        self._window: deque[tuple[float, int]] = deque()
        self._window_bytes = 0  # the sum of the window, kept as it changes (a snapshot follows every piece)
        self._stage = "downloading"
        self._last_emit = 0.0

    def reuse(self, size: int, fragments: int = 0) -> None:
        """Parts already on disk from an earlier run: counted, but not in the speed."""
        with self._lock:
            self.committed += size
            self.fragments_done += fragments
        self.emit()

    def reset(self, committed: int = 0) -> None:
        with self._lock:
            self.committed = committed
            self._inflight.clear()
        self.emit(force=True)

    def advance(self, key: Any, size: int) -> None:
        with self._lock:
            self._inflight[key] = self._inflight.get(key, 0) + size
            self._window.append((self._clock(), size))
            self._window_bytes += size
        self.emit()

    def commit(self, key: Any, size: int, fragments: int = 1) -> None:
        with self._lock:
            self._inflight.pop(key, None)
            self.committed += size
            self.fragments_done += fragments
        self.emit()

    def drop(self, key: Any) -> None:
        """A failed attempt: its bytes are no longer counted as downloaded."""
        with self._lock:
            self._inflight.pop(key, None)

    def stage(self, name: str) -> None:
        with self._lock:
            self._stage = name
            self._window.clear()
            self._window_bytes = 0
        self.emit(force=True)

    def _speed(self, now: float) -> float | None:
        while self._window and now - self._window[0][0] > SPEED_WINDOW_SECONDS:
            self._window_bytes -= self._window.popleft()[1]
        if len(self._window) < 2:
            return None
        span = now - self._window[0][0]
        return self._window_bytes / span if span > 0 else None

    def snapshot(self) -> Progress:
        with self._lock:
            now = self._clock()
            done = self.committed + sum(self._inflight.values())
            speed = self._speed(now) if self._stage == "downloading" else None
            eta = None
            if speed:
                if self.basis == "fragments" and self.fragments_total and self.fragments_done:
                    remaining = self.fragments_total - self.fragments_done
                    average = self.committed / self.fragments_done
                    eta = max(0.0, remaining * average - sum(self._inflight.values())) / speed
                elif self.total_bytes:
                    eta = max(0, self.total_bytes - done) / speed
            fragments = self.basis == "fragments"
            return Progress(done, self.total_bytes, speed, eta, self.basis,
                            self.fragments_done if fragments else None,
                            self.fragments_total if fragments else None, self._stage)

    def emit(self, *, force: bool = False) -> None:
        with self._lock:
            now = self._clock()
            if not force and now - self._last_emit < self._emit_seconds:
                return
            self._last_emit = now
        with self._emit_lock:  # one report at a time: an older snapshot never lands after a newer one
            self._on_progress(self.snapshot())


class LogBuffer:
    """Masked log lines for one transfer, flushed at most about once a second (and on ``flush``)."""

    def __init__(self, on_log: Callable[[list[str]], None], *, flush_seconds: float = LOG_FLUSH_SECONDS):
        self._on_log = on_log
        self._flush_seconds = flush_seconds
        self._lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._pending: list[str] = []
        self._last = 0.0

    def __call__(self, line: str) -> None:
        with self._lock:
            self._pending.append(mask_line(line))
            due = time.monotonic() - self._last >= self._flush_seconds
        if due:
            self.flush()

    def flush(self) -> None:
        with self._flush_lock:  # lines reach the log in the order they were written
            with self._lock:
                lines, self._pending = self._pending, []
                self._last = time.monotonic()
            if lines:
                self._on_log(lines)


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))  # a config saved by PowerShell 5.1 starts with a BOM
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, data: Mapping[str, Any]) -> None:
    """Atomic within the task folder: a crash leaves the old file or the new one, never half of one."""
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)


def finished_media(task_dir: Path, identity: str) -> Path | None:
    """The file an earlier run of this source finished (it stopped at VERIFYING or PUBLISHING), if unchanged."""
    data = read_json(task_dir / FINISHED_NAME)
    name = data.get("name")
    if data.get("identity") != identity or not isinstance(name, str) or not name.startswith("media."):
        return None
    path = task_dir / name
    if path.parent != task_dir or not path.is_file() or path.stat().st_size != data.get("size"):
        return None
    return path


def mark_finished(task_dir: Path, identity: str, path: Path) -> None:
    write_json(task_dir / FINISHED_NAME, {"identity": identity, "name": path.name, "size": path.stat().st_size})


def probe_streams(ffprobe: Path | None, path: Path, *, input_format: str | None = None) -> dict[str, Any] | None:
    """ffprobe's streams and format of a local file; None when ffprobe is missing or cannot read it."""
    if ffprobe is None or not Path(ffprobe).is_file():
        return None
    command = [str(ffprobe), "-v", "error", "-protocol_whitelist", "file"]
    if input_format:
        command += ["-f", input_format]
    command += ["-show_streams", "-show_format", "-of", "json", str(path)]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=PROBE_TIMEOUT_SECONDS, check=False, creationflags=_CREATE_NO_WINDOW)
        if completed.returncode != 0:
            return None
        data = json.loads(completed.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return data if isinstance(data, dict) else None


def stream_summary(probe: Mapping[str, Any]) -> dict[str, Any]:
    """Video and audio of an ffprobe answer: codecs, size, duration, and whether each kind is there."""
    streams = probe.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"
                  and not (item.get("disposition") or {}).get("attached_pic")), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    try:
        duration = float((probe.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        duration = None
    return {"has_video": video is not None, "has_audio": audio is not None,
            "video_codec": video.get("codec_name") if video else None,
            "audio_codec": audio.get("codec_name") if audio else None,
            "width": int(video["width"]) if video and video.get("width") else None,
            "height": int(video["height"]) if video and video.get("height") else None,
            "duration_seconds": duration if duration and duration > 0 else None}


def require_audio_video(summary: Mapping[str, Any], what: str) -> None:
    if not summary["has_video"]:
        raise SourceError("NO_VIDEO_STREAM", f"{what} không có luồng hình; không tải.")
    if not summary["has_audio"]:
        raise SourceError("NO_AUDIO_STREAM", f"{what} không có luồng tiếng; không tải.")


def _feed(process: subprocess.Popen, parts: Sequence[Path], control: Interruptible) -> None:
    """Write the parts to FFmpeg's stdin in order. A part that cannot be read raises (DISK_ERROR); FFmpeg
    that ended early (killed or failed) only stops the feeding: its exit code and stderr tell why."""
    try:
        for part in parts:
            with part.open("rb") as handle:
                while not control.requested and (chunk := handle.read(COPY_CHUNK_BYTES)):
                    try:
                        process.stdin.write(chunk)
                    except OSError:
                        return
            if control.requested:
                return
    finally:
        try:
            process.stdin.close()  # always: FFmpeg never waits for an end of input that does not come
        except OSError:
            pass


def _end_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        kill_process_tree(process.pid)
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            pass


def remux_to_mp4(ffmpeg: Path, parts: Sequence[Path], output: Path, control: Interruptible, *,
                 on_start: Callable[[int, float], None] | None = None,
                 log: Callable[[str], None] = lambda line: None,
                 timeout: float = REMUX_TIMEOUT_SECONDS) -> None:
    """Copy the first video and audio streams of the MPEG-TS ``parts`` (in this order) into ``output``.

    The parts go through FFmpeg's stdin, so no joined copy is written. A stop or a cancel kills FFmpeg;
    so does ``timeout`` (a hung FFmpeg). Raises Cancelled or SourceError (REMUX_FAILED).
    """
    if not Path(ffmpeg).is_file():
        raise SourceError("NO_FFMPEG", "Không thấy FFmpeg của BiliFlow (tools\\ffmpeg\\bin); chưa ghép được video.")
    output.unlink(missing_ok=True)
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "pipe",
               "-f", "mpegts", "-i", "pipe:0", "-map", "0:v:0", "-map", "0:a:0", "-c", "copy",
               "-f", "mp4", "-y", str(output)]
    process = subprocess.Popen(command, cwd=output.parent, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, creationflags=_CREATE_NO_WINDOW)
    tail: deque[str] = deque(maxlen=20)

    def drain() -> None:
        for raw in iter(process.stderr.readline, b""):
            text = raw.decode("utf-8", "replace").strip()
            if text:
                tail.append(text)
    reader = threading.Thread(target=drain, name="download-remux-stderr", daemon=True)
    reader.start()
    remove = control.add_closer(lambda: kill_process_tree(process.pid))
    watchdog = threading.Timer(timeout, lambda: kill_process_tree(process.pid))
    watchdog.daemon = True
    watchdog.start()
    try:
        if on_start is not None:
            try:
                created = psutil.Process(process.pid).create_time()
            except psutil.Error:
                created = 0.0
            on_start(process.pid, created)
        _feed(process, parts, control)
        try:
            process.wait(timeout)
        except subprocess.TimeoutExpired:
            pass  # killed just below
    finally:
        watchdog.cancel()
        remove()
        _end_process(process)
        reader.join(5)
        if process.stderr is not None:
            process.stderr.close()
    for line in tail:
        log(f"[ffmpeg] {line}")
    if control.requested:
        raise Cancelled()
    if process.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        detail = mask_line(tail[-1])[:200] if tail else f"mã thoát {process.returncode}"
        raise SourceError("REMUX_FAILED", f"FFmpeg không ghép được các đoạn thành MP4: {detail}")


def replace_held(source: Path, target: Path, control: Interruptible, *, attempts: int = RENAME_ATTEMPTS,
                 wait: float = RENAME_WAIT_SECONDS) -> None:
    """os.replace inside the task folder, tried again while another program (antivirus, indexer) still
    holds the new file; a stop or a cancel during the wait raises Cancelled."""
    for attempt in range(1, attempts + 1):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == attempts:
                raise
            if control.wait(wait):
                raise Cancelled() from None
