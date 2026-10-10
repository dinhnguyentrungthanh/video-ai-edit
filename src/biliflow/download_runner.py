"""Run yt-dlp for one download task: probe, download, progress, stop.

Links are passed as a list argument after ``--``, never through a shell. No
cookies, accounts, plugins or user config are read, and every cache and temp
file stays under the project root. A stop kills the whole process tree
(yt-dlp, FFmpeg, Deno) with psutil.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import psutil

from biliflow.download_files import VIDEO_EXTENSIONS, tree_size
from biliflow.download_probe import classify_error
from biliflow.download_tools import DownloadToolsError, binary_path

PROGRESS_PREFIX = "BFPROG"
FORMAT = "bv*+ba/b"
FORMAT_SORT = "res:1080,vcodec:h264,acodec:aac"
FINAL_PATH_FILE = "final-path.txt"
PROGRESS_TEMPLATE = (
    f"download:{PROGRESS_PREFIX} %(progress.status)s %(progress.downloaded_bytes)s "
    "%(progress.total_bytes)s %(progress.total_bytes_estimate)s %(progress.speed)s "
    "%(progress.eta)s %(progress.filename)s"
)
DEFAULT_PROBE_TIMEOUT_SECONDS = 300
# A probe reads at most this many entries: a page with ads and a film has a few; a playlist or a
# channel pasted by mistake would otherwise be read video by video until the timeout.
MAX_PROBE_ENTRIES = 10
KILL_WAIT_SECONDS = 10
LOG_FLUSH_SECONDS = 1.0
PROGRESS_EMIT_SECONDS = 0.5
ERROR_TAIL_LINES = 50
SIZE_CHECK_SECONDS = 2.0
MAX_LOG_LINE = 500
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_MASKS = (
    (re.compile(r"(?i)\b((?:set-)?cookie|authorization|proxy-authorization|x-[\w-]*token)\s*[:=]\s*.+"),
     lambda match: f"{match.group(1)}: ***"),
    (re.compile(r"(?i)([?&;](?:[^=&\s\"']*(?:token|sig|signature|key|auth|session|policy|expire|pot|"
                r"cookie|pass|secret|credential)[^=&\s\"']*)=)[^&\s\"']+"),
     lambda match: f"{match.group(1)}***"),
    (re.compile(r"[A-Za-z0-9_\-+/=.]{40,}"), lambda match: "***"),
)


def mask_line(line: str) -> str:
    """Hide token-, cookie- and header-like values before a line reaches the log."""
    text = line.rstrip("\r\n")[:MAX_LOG_LINE * 4]  # bounded work: the masks never scan a huge line
    for pattern, replace in _MASKS:
        text = pattern.sub(replace, text)
    return text[:MAX_LOG_LINE]


def kill_process_tree(pid: int, *, timeout: float = KILL_WAIT_SECONDS) -> list[int]:
    """Kill ``pid`` and every descendant; return the pids still alive afterwards."""
    try:
        parent = psutil.Process(pid)
    except psutil.Error:
        return []
    try:
        parent.suspend()  # no new children between listing and killing
    except psutil.Error:
        pass
    try:
        processes = [*parent.children(recursive=True), parent]
    except psutil.Error:
        processes = [parent]
    for process in processes:
        try:
            process.kill()
        except psutil.Error:
            pass
    _gone, alive = psutil.wait_procs(processes, timeout=timeout)
    return [process.pid for process in alive]


def _run_closer(closer: Callable[[], None]) -> None:
    try:
        closer()
    except Exception:  # noqa: BLE001 - a socket already closed or a thread already gone
        pass


class ProcessControl:
    """Shared between a task thread and the API: who asked the process to end, and why.

    Besides a child process (``attach``), a source transfer registers closers (``add_closer``): open
    sockets and its segment pool, so a stop or a cancel never waits for a read to time out.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._pid: int | None = None
        self._closers: dict[int, Callable[[], None]] = {}
        self._next_closer = 0
        self.reason: str | None = None

    @property
    def requested(self) -> bool:
        return self.reason is not None

    def request(self, reason: str) -> None:
        """``stop``, ``cancel`` or ``shutdown``; a cancel overrides an earlier stop."""
        with self._lock:
            if self.reason is None or reason == "cancel":
                self.reason = reason
            pid = self._pid
            closers = list(self._closers.values())
        self._event.set()
        for closer in closers:
            _run_closer(closer)
        if pid is not None:
            kill_process_tree(pid)

    def add_closer(self, closer: Callable[[], None]) -> Callable[[], None]:
        """Run ``closer`` on the next request (at once when one was already made); returns its remover."""
        with self._lock:
            key = self._next_closer
            self._next_closer += 1
            self._closers[key] = closer
            requested = self.reason is not None
        if requested:
            _run_closer(closer)

        def remove() -> None:
            with self._lock:
                self._closers.pop(key, None)
        return remove

    def attach(self, pid: int) -> None:
        with self._lock:
            self._pid = pid
            requested = self.reason is not None
        if requested:
            kill_process_tree(pid)

    def detach(self) -> None:
        with self._lock:
            self._pid = None

    def wait(self, seconds: float) -> bool:
        """Sleep up to ``seconds``; True when an end was requested meanwhile."""
        return self._event.wait(seconds)


@dataclass(frozen=True)
class Progress:
    """``downloaded_bytes`` is always real bytes. A segmented transfer counts its own unit in
    ``fragments_done``/``fragments_total`` (``basis`` "fragments"); ``stage`` is "downloading",
    "comparing" (a strict part compared with a fresh link, no new bytes) or "remuxing" for a source transfer
    (None for yt-dlp)."""
    downloaded_bytes: int
    total_bytes: int | None
    speed: float | None
    eta: float | None
    basis: str = "bytes"
    fragments_done: int | None = None
    fragments_total: int | None = None
    stage: str | None = None


class ProgressTracker:
    """Aggregate yt-dlp's per-file progress (video, then audio) into one bar."""

    def __init__(self, expected_files: int, estimated_bytes: int | None):
        self.expected_files = max(1, expected_files)
        self.estimated_bytes = estimated_bytes
        self._files: dict[str, tuple[int, int | None]] = {}

    def update(self, name: str, downloaded: int, total: int | None, estimate: int | None,
               speed: float | None) -> Progress:
        self._files[name] = (downloaded, total or estimate)
        done = sum(value for value, _ in self._files.values())
        sizes = [size for _, size in self._files.values()]
        total_bytes: int | None = None
        if len(self._files) >= self.expected_files and all(sizes):
            total_bytes = sum(sizes)
        elif self.estimated_bytes:
            total_bytes = max(self.estimated_bytes, done)
        eta = (total_bytes - done) / speed if total_bytes and speed else None
        return Progress(done, total_bytes, speed, eta)


class SizeGuard:
    """Ends a download that writes more than the free space allows, whatever size the page claimed.

    ``max_bytes`` bounds the bytes yt-dlp reports; ``max_disk_bytes`` bounds the task folder (parts,
    fragments and the merged file), read every ``SIZE_CHECK_SECONDS`` for downloaders that report nothing.
    """

    def __init__(self, task_dir: Path, max_bytes: int | None, max_disk_bytes: int | None, *,
                 interval: float = SIZE_CHECK_SECONDS):
        self.task_dir = task_dir
        self.max_bytes = max_bytes
        self.max_disk_bytes = max_disk_bytes
        self.interval = interval
        self.tripped = False
        self._done = threading.Event()

    def over(self, latest: Progress | None) -> bool:
        if self.max_bytes and latest is not None and latest.downloaded_bytes > self.max_bytes:
            self.tripped = True
        return self.tripped

    def watch(self, pid: int | None = None, *, on_trip: Callable[[], None] | None = None) -> None:
        """Read the task folder every ``interval`` seconds until ``stop``; when it is too big, kill the
        tree of ``pid`` and/or call ``on_trip`` (a source transfer closes its sockets and its pool)."""
        def run() -> None:
            while not self._done.wait(self.interval):
                if self.max_disk_bytes and tree_size(self.task_dir) > self.max_disk_bytes:
                    self.tripped = True
                    if pid is not None:
                        kill_process_tree(pid)
                    if on_trip is not None:
                        _run_closer(on_trip)
                    return
        threading.Thread(target=run, name="download-size-guard", daemon=True).start()

    def stop(self) -> None:
        self._done.set()

    def message(self) -> str:
        limit = self.max_bytes or self.max_disk_bytes or 0
        return f"File lớn hơn chỗ trống cho phép (quá {limit / 1024**3:.1f} GB); đã dừng tải."


@dataclass(frozen=True)
class ProbeOutcome:
    ok: bool
    info: dict[str, Any] | None = None
    code: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class DownloadOutcome:
    """``resumable``: the part on disk is good and "Tiếp tục" may continue it (a network error after the
    retries of a source transfer); the worker then ends the task INTERRUPTED instead of FAILED."""
    ok: bool
    code: str | None = None
    message: str | None = None
    final_path: Path | None = None
    returncode: int | None = None
    resumable: bool = False


_STOP_CODES = {"stop": "STOPPED", "cancel": "CANCELLED", "shutdown": "SHUTDOWN"}
_STOP_MESSAGES = {"stop": "Đã dừng.", "cancel": "Đã hủy.", "shutdown": "Control Center đang tắt."}


def _stopped(control: ProcessControl) -> tuple[str, str]:
    reason = control.reason or "stop"
    return _STOP_CODES[reason], _STOP_MESSAGES[reason]


def stop_reason(control: ProcessControl) -> tuple[str, str]:
    """(code, message) of a requested end (STOPPED, CANCELLED, SHUTDOWN), as the yt-dlp runner reports it."""
    return _stopped(control)


def _value(text: str) -> float | None:
    try:
        number = float(text)
    except ValueError:
        return None
    return number if number >= 0 else None


class YtDlpRunner:
    def __init__(self, project_root: Path, *, command_prefix: list[str] | None = None,
                 deno_path: Path | None | str = "auto", ffmpeg_dir: Path | None = None,
                 env_extra: dict[str, str] | None = None,
                 probe_timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS):
        self.root = project_root
        # -P: the task folder (the cwd, where yt-dlp writes) is never put on sys.path.
        self.command_prefix = command_prefix or [sys.executable, "-P", "-m", "yt_dlp"]
        if deno_path == "auto":
            try:
                deno_path = binary_path(project_root, "deno")
            except DownloadToolsError:
                deno_path = project_root / "tools" / "deno" / "deno.exe"
        self.deno = Path(deno_path) if deno_path and Path(deno_path).is_file() else None
        self.ffmpeg_dir = ffmpeg_dir or project_root / "tools" / "ffmpeg" / "bin"
        self.cache_dir = project_root / "cache" / "yt-dlp"
        self.deno_dir = project_root / "cache" / "deno"
        self.env_extra = dict(env_extra or {})
        self.probe_timeout = probe_timeout

    def environment(self, task_dir: Path) -> dict[str, str]:
        env = dict(os.environ)
        env.update({
            "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
            "DENO_DIR": str(self.deno_dir), "DENO_NO_UPDATE_CHECK": "1", "DENO_NO_PROMPT": "1",
            "TEMP": str(task_dir / "tmp"), "TMP": str(task_dir / "tmp"),
            "XDG_CACHE_HOME": str(self.root / "cache"),
        })
        env.update(self.env_extra)
        return env

    def _common(self) -> list[str]:
        runtimes = ["--no-js-runtimes"] + (["--js-runtimes", f"deno:{self.deno}"] if self.deno else [])
        return [
            "--ignore-config", "--no-plugin-dirs", "--no-cookies", "--no-cookies-from-browser",
            "--no-update", "--no-remote-components", *runtimes,
            "--cache-dir", str(self.cache_dir), "--ffmpeg-location", str(self.ffmpeg_dir),
            "--socket-timeout", "30", "--no-playlist", "-f", FORMAT, "-S", FORMAT_SORT,
        ]

    def probe_command(self, url: str) -> list[str]:
        return [*self.command_prefix, *self._common(), "--dump-single-json", "--skip-download",
                "--playlist-end", str(MAX_PROBE_ENTRIES), "--no-warnings", "--", url]

    def download_command(self, url: str, task_dir: Path, *, playlist_item: int | None = None,
                         max_filesize: int | None = None) -> list[str]:
        extra = []
        if playlist_item:
            extra += ["--playlist-items", str(int(playlist_item))]
        if max_filesize:
            extra += ["--max-filesize", str(int(max_filesize))]
        return [
            *self.command_prefix, *self._common(), *extra,
            "--merge-output-format", "mp4", "--remux-video", "mp4",
            "-P", f"home:{task_dir}", "-P", f"temp:{task_dir / 'frag'}",
            "-o", "%(id)s.%(ext)s", "--restrict-filenames",
            "--continue", "--newline", "--no-mtime", "--progress", "--progress-delta", "0.5",
            "--progress-template", PROGRESS_TEMPLATE,
            "--print-to-file", "after_move:%(filepath)s", str(task_dir / FINAL_PATH_FILE),
            "--no-write-subs", "--no-write-auto-subs", "--no-embed-subs", "--no-write-thumbnail",
            "--no-write-info-json", "--retries", "10", "--fragment-retries", "10",
            "--", url,
        ]

    def _spawn(self, command: list[str], task_dir: Path, *, merge_stderr: bool) -> subprocess.Popen:
        (task_dir / "tmp").mkdir(parents=True, exist_ok=True)
        return subprocess.Popen(
            command, cwd=task_dir, env=self.environment(task_dir), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
            creationflags=_CREATE_NO_WINDOW,
        )

    @staticmethod
    def _started(process: subprocess.Popen, control: ProcessControl,
                 on_start: Callable[[int, float], None] | None) -> None:
        if on_start is not None:
            try:
                created = psutil.Process(process.pid).create_time()
            except psutil.Error:
                created = 0.0
            on_start(process.pid, created)
        control.attach(process.pid)

    @staticmethod
    def _reap(process: subprocess.Popen) -> None:
        """After the run or an exception (a database write that failed): never leave yt-dlp
        running or its pipes open."""
        if process.poll() is None:
            kill_process_tree(process.pid)
            try:
                process.wait(KILL_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                pass  # it ignored the kill (access denied): nothing more can be done from here
        for stream in (process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()

    def probe(self, url: str, task_dir: Path, control: ProcessControl, *,
              on_start: Callable[[int, float], None] | None = None) -> ProbeOutcome:
        process = self._spawn(self.probe_command(url), task_dir, merge_stderr=False)
        deadline = time.monotonic() + self.probe_timeout
        timed_out = False
        try:
            self._started(process, control, on_start)
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    if not control.requested and time.monotonic() > deadline:
                        timed_out = True
                        kill_process_tree(process.pid)
        finally:
            control.detach()
            self._reap(process)
        if control.requested:
            return ProbeOutcome(False, None, *_stopped(control))
        if timed_out:
            return ProbeOutcome(False, None, "PROBE_TIMEOUT",
                                f"Thăm dò quá {int(self.probe_timeout)} giây; đã dừng.")
        error_text = stderr.decode("utf-8", "replace")
        if process.returncode != 0:
            return ProbeOutcome(False, None, *classify_error(mask_line_block(error_text), stage="probe"))
        try:
            info = json.loads(stdout.decode("utf-8", "replace"))
        except ValueError:
            return ProbeOutcome(False, None, "PROBE_FAILED", "Kết quả thăm dò không đọc được.")
        if not isinstance(info, dict):
            return ProbeOutcome(False, None, "PROBE_FAILED", "Kết quả thăm dò không đọc được.")
        return ProbeOutcome(True, info)

    def download(self, url: str, task_dir: Path, control: ProcessControl, *,
                 on_progress: Callable[[Progress], None], on_log: Callable[[list[str]], None],
                 on_start: Callable[[int, float], None] | None = None,
                 playlist_item: int | None = None, max_filesize: int | None = None,
                 expected_files: int = 1, estimated_bytes: int | None = None,
                 guard: SizeGuard | None = None) -> DownloadOutcome:
        command = self.download_command(url, task_dir, playlist_item=playlist_item,
                                        max_filesize=max_filesize)
        process = self._spawn(command, task_dir, merge_stderr=True)
        tracker = ProgressTracker(expected_files, estimated_bytes)
        tail: list[str] = []
        pending: list[str] = []
        last_flush = last_emit = 0.0
        latest: Progress | None = None
        try:
            self._started(process, control, on_start)
            if guard is not None:
                guard.watch(process.pid)
            for raw in iter(process.stdout.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                now = time.monotonic()
                if line.startswith(PROGRESS_PREFIX + " "):
                    latest = self._progress(line, tracker) or latest
                    if latest is not None and now - last_emit >= PROGRESS_EMIT_SECONDS:
                        on_progress(latest)
                        last_emit = now
                elif line.strip():
                    masked = mask_line(line)
                    pending.append(masked)
                    tail = (tail + [masked])[-ERROR_TAIL_LINES:]
                    if now - last_flush >= LOG_FLUSH_SECONDS:
                        on_log(pending)
                        pending, last_flush = [], now
                if guard is not None and guard.over(latest):
                    kill_process_tree(process.pid)
                    break
            process.wait()
        finally:
            if guard is not None:
                guard.stop()
            control.detach()
            self._reap(process)
        if pending:
            on_log(pending)
        if latest is not None:
            on_progress(latest)
        if control.requested:
            return DownloadOutcome(False, *_stopped(control), returncode=process.returncode)
        if guard is not None and guard.tripped:
            return DownloadOutcome(False, "TOO_LARGE", guard.message(), returncode=process.returncode)
        if process.returncode != 0:
            code, message = classify_error("\n".join(tail), stage="download")
            return DownloadOutcome(False, code, message, returncode=process.returncode)
        final = self._final_file(task_dir)
        if final is None:
            return DownloadOutcome(False, "NO_OUTPUT", "yt-dlp báo xong nhưng không thấy file video.",
                                   returncode=0)
        return DownloadOutcome(True, final_path=final, returncode=0)

    @staticmethod
    def _progress(line: str, tracker: ProgressTracker) -> Progress | None:
        parts = line.split(" ", 7)
        if len(parts) < 7:
            return None
        downloaded = _value(parts[2])
        if downloaded is None:
            return None
        total, estimate, speed = _value(parts[3]), _value(parts[4]), _value(parts[5])
        name = parts[7] if len(parts) > 7 else ""
        return tracker.update(name, int(downloaded), int(total) if total else None,
                              int(estimate) if estimate else None, speed)

    @staticmethod
    def _final_file(task_dir: Path) -> Path | None:
        home = task_dir.resolve()
        marker = task_dir / FINAL_PATH_FILE
        if marker.is_file():
            for line in reversed(marker.read_text(encoding="utf-8", errors="replace").splitlines()):
                candidate = Path(line.strip())
                if line.strip() and candidate.is_file() and candidate.resolve().parent == home:
                    return candidate
        videos = [path for path in task_dir.iterdir()
                  if path.is_file() and path.suffix.casefold() in VIDEO_EXTENSIONS]
        return max(videos, key=lambda path: path.stat().st_size) if videos else None


def mask_line_block(text: str) -> str:
    return "\n".join(mask_line(line) for line in text.splitlines())
