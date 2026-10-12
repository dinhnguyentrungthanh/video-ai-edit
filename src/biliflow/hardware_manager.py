"""Control Center side of the hardware profile (docs/HARDWARE_PROFILE.md sections 6.1 and 6.3).

Cài đặt → Phần cứng on the PC: ``status()`` answers GET /api/hardware-profile, ``start("remeasure")``
runs ``scripts/hardware-measure.ps1`` of this checkout, ``set_cpu_only()`` and ``set_untested_fast()``
write the switch file, ``close()`` asks a running measurement to stop. The measurement takes every
lock and checks the queue again itself; the manager only refuses early, watches the child process
and maps its exit code.

Imported only by ``control_center._hardware(center)``, inside that function (I2), never by a scan
stage, and nothing here runs from /api/status. Module level: standard library, hardware_profile
(with its hardware_schema) and hardware_status. hardware_detect (observe), job_store and
export_guards are imported inside functions. Every string that leaves goes through
hardware_schema.sanitize_message.
"""
from __future__ import annotations

import contextlib
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from biliflow import hardware_profile, hardware_status
from biliflow.hardware_profile import (
    READY_UNTESTED, Observation, UntestedFast, loads_strict, read_override, read_profile, write_override,
)
from biliflow.hardware_schema import FAILURE_MESSAGES, path_replacements, sanitize_message

CODE_DIR = Path(__file__).resolve().parents[2]
MEASURE_SCRIPT = CODE_DIR / "scripts" / "hardware-measure.ps1"  # this checkout's, never <root>\scripts
# The measurement's own mutex (section 6.3 step 2). The manager only opens it, never creates it.
MEASURE_MUTEX = r"Local\BiliFlowHardwareProfile"
ACTIONS = ("remeasure",)

OBSERVE_CACHE_SECONDS = 30.0  # the light observation of the Settings page (section 3.3)
MEASURE_LIMIT_SECONDS = 15 * 60  # the measurement's 10-minute budget plus grace (section 6.3)
CANCEL_GRACE_SECONDS = 60.0  # after the cancel file and before the whole tree is killed
POLL_SECONDS = 1.0
PREVIOUS_PROBE_SECONDS = 5.0  # how often status() asks again whether an earlier run still holds the mutex
KILL_TIMEOUT_SECONDS = 30.0
EXIT_WAIT_SECONDS = 30.0
MAX_PROGRESS_BYTES = 4096
MESSAGE_LIMIT = 600

RUN_DIR_PREFIX = "hardware-measure-"
CANCEL_FILE = "cancel"
PROGRESS_FILE = "progress.json"
LOG_DIR = ("logs", "control-center")

BUSY_MESSAGE = "Đang đo phần cứng; chờ lần đo xong rồi bấm lại."
PREVIOUS_RUN_MESSAGE = "Đang đo (từ lần chạy trước)"
PREVIOUS_DONE_MESSAGE = "Lần đo từ lần chạy trước đã xong."
PROBE_UNKNOWN_MESSAGE = "Không kiểm tra được có lần đo nào đang chạy hay không; thử lại sau."
CLOSING_MESSAGE = "Control Center đang tắt; không bắt đầu lần đo mới."
NOT_PAUSED_MESSAGE = "Hãy tạm dừng hàng đợi trước"
NOT_IDLE_MESSAGE = "Hàng đợi đã tạm dừng nhưng còn việc đang chạy; chờ stage đang chạy xong."
QUEUE_UNKNOWN_MESSAGE = "Không đọc được trạng thái hàng đợi; thử lại."
NOT_UNTESTED_MESSAGE = ("Chỉ bật được Chế độ nhanh khi máy ở trạng thái chưa qua cổng (ready_untested); "
                        "trạng thái hiện tại: {status}.")
UNKNOWN_ACTION_MESSAGE = "Việc phần cứng không hợp lệ."
ENABLED_MESSAGE = "enabled phải là true hoặc false"
START_FAILED_MESSAGE = "Không chạy được lệnh đo ({error})."
KILLED_MESSAGE = "Lệnh đo chạy quá 15 phút: đã yêu cầu dừng, rồi buộc dừng cả cây tiến trình."
UNKNOWN_EXIT_MESSAGE = "Lỗi không rõ (mã {code})."
# Exit codes of the measurement (section 6.3): (outcome, message).
EXIT_OUTCOMES = {
    0: ("done", "Đo xong; đã ghi hồ sơ phần cứng."),
    10: ("failed", "Đo xong nhưng lần đo thất bại; bản tốt gần nhất giữ nguyên."),
    64: ("refused", "Lệnh đo từ chối tham số hoặc thư mục gốc; không ghi gì."),
    70: ("error", "Lệnh đo gặp lỗi nội bộ; không ghi gì. Xem log."),
    75: ("busy", "Lệnh đo không chạy: đang có lần đo khác, hàng đợi chưa rảnh hoặc đã hủy; không ghi gì."),
    124: ("timeout", "Lệnh đo quá giờ; không ghi gì."),
}

_STEP = re.compile(r"[a-z0-9_]{1,40}")
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
_SYSTEM32_FALLBACK = r"C:\Windows\System32"
_SYNCHRONIZE = 0x00100000
_MUTEX_MODIFY_STATE = 0x0001
_ERROR_FILE_NOT_FOUND = 2
_WAIT_OBJECT_0, _WAIT_ABANDONED, _WAIT_TIMEOUT = 0x0, 0x80, 0x102


# --- the queue (section 6.1, can_remeasure) -----------------------------------------------------

@dataclass(frozen=True)
class QueueView:
    """The four conditions of can_remeasure, read by queue_view()."""
    paused: bool  # scheduler_paused is true
    active: bool  # scheduler.active is not None
    busy: bool  # scheduler.is_busy(id) for some job
    in_process: bool  # some job is in IN_PROCESS_STATES (RENDERING included)


def queue_view(store: Any, scheduler: Any) -> QueueView:
    """Read through public methods only: never the scheduler's private fields (A1b changes them)."""
    from biliflow.job_store import IN_PROCESS_STATES
    jobs = store.list_jobs()
    return QueueView(
        paused=store.setting("scheduler_paused", False) is True,
        active=scheduler.active is not None,
        busy=any(scheduler.is_busy(job["id"]) for job in jobs),
        in_process=any(job["state"] in IN_PROCESS_STATES for job in jobs),
    )


def _conflict(code: str, message: str) -> Exception:
    from biliflow.export_guards import ActionConflict  # control_center's do_POST answers it with 409
    return ActionConflict(code, message)


# --- Windows helpers ------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _kernel32() -> Any:
    """A private kernel32 with 64-bit handle types (the shared ctypes.windll one is left alone)."""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, restype, argtypes in (
        ("OpenMutexW", wintypes.HANDLE, (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)),
        ("WaitForSingleObject", wintypes.DWORD, (wintypes.HANDLE, wintypes.DWORD)),
        ("ReleaseMutex", wintypes.BOOL, (wintypes.HANDLE,)),
        ("CloseHandle", wintypes.BOOL, (wintypes.HANDLE,)),
    ):
        function = getattr(kernel32, name)
        function.restype, function.argtypes = restype, argtypes
    return kernel32


def measurement_running(name: str) -> bool | None:
    """True while some process owns the measurement mutex ``name``; None when it cannot be told.

    OpenMutexW never creates the object. When nobody owns it, it is taken and released at once,
    in this call and on this thread (a Windows mutex belongs to the thread that took it).
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        kernel32 = _kernel32()
        handle = kernel32.OpenMutexW(_SYNCHRONIZE | _MUTEX_MODIFY_STATE, False, name)
        if not handle:
            return False if ctypes.get_last_error() == _ERROR_FILE_NOT_FOUND else None
        try:
            waited = kernel32.WaitForSingleObject(handle, 0)
            if waited in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
                kernel32.ReleaseMutex(handle)
                return False
            return True if waited == _WAIT_TIMEOUT else None
        finally:
            kernel32.CloseHandle(handle)
    except (OSError, AttributeError, ValueError):  # unknown: the caller treats it as busy
        return None


def _system32(*parts: str) -> str:
    """A program of the real System32 (GetSystemDirectoryW), never looked up through PATH."""
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(260)
        length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
        folder = buffer.value if length else _SYSTEM32_FALLBACK
    except (ImportError, AttributeError, OSError):
        folder = _SYSTEM32_FALLBACK
    return str(Path(folder, *parts))


def kill_process_tree(pid: int) -> None:
    """taskkill /T /F on the measurement's tree: PowerShell, the venv launcher, Python and its children."""
    subprocess.run([_system32("taskkill.exe"), "/PID", str(int(pid)), "/T", "/F"], stdin=subprocess.DEVNULL,
                   capture_output=True, timeout=KILL_TIMEOUT_SECONDS, shell=False, check=False,
                   creationflags=_CREATE_NO_WINDOW)


def venv_install(python: str) -> Path | None:
    """<install> when ``python`` is <install>/.venv/Scripts/python.exe, else None."""
    path = Path(python)
    if (path.name.lower(), path.parent.name.lower(), path.parent.parent.name.lower()) != (
            "python.exe", "scripts", ".venv"):
        return None
    return path.parents[2]


def guard_install(python: str, root: Path) -> Path | None:
    """The real install a test Control Center also guards (section 6.3): the install whose .venv
    runs us, when it is not the root itself and holds state/control-center.sqlite3."""
    install = venv_install(python)
    if install is None:
        return None
    try:
        if os.path.normcase(str(install.resolve())) == os.path.normcase(str(Path(root).resolve())):
            return None
        return install if (install / "state" / "control-center.sqlite3").is_file() else None
    except OSError:
        return None


# --- small helpers ------------------------------------------------------------------------------

def _observe(root: Path, *, ffmpeg_exe: str | None) -> Observation:
    from biliflow import hardware_detect  # NVML and the registry are read only inside the call
    return hardware_detect.observe(root, ffmpeg_exe=ffmpeg_exe)


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _describe(error: BaseException) -> str:
    text = str(error).strip()
    return f"{type(error).__name__}: {text}"[:200] if text else type(error).__name__


def _require_bool(value: Any) -> None:
    if type(value) is not bool:
        raise ValueError(ENABLED_MESSAGE)


def _ffmpeg_path(root: Path) -> str | None:
    """detection.ffmpeg.path of the profile resolve() compares with: main when readable, else last-good."""
    for which in ("main", "last_good"):
        read = read_profile(root, which)
        if read.state == "ok":
            return read.document["detection"]["ffmpeg"]["path"]
    return None


def _profile_mark(root: Path) -> tuple[str, str | None]:
    read = read_profile(root, "main")
    return read.state, read.document["content_sha256"] if read.state == "ok" else None


def _profile_note(root: Path, before: tuple[str, str | None] | None) -> tuple[str, str | None]:
    """A sentence on what the main profile holds now, and the failure code of a new failed one."""
    read = read_profile(root, "main")
    if read.state == "missing":
        return "Chưa có hồ sơ phần cứng.", None
    if read.state != "ok":
        return "File hồ sơ chính hỏng; Đo lại.", None
    document = read.document
    if before == ("ok", document["content_sha256"]):
        return "Hồ sơ không đổi.", None
    attempt = document["attempt"]
    failure = None if attempt["failure"] is None else attempt["failure"]["code"]
    return f"Hồ sơ ghi lúc {document['written_at']}, kết quả {attempt['result']}.", failure


def _outcome(code: int | None, killed: bool, problem: str | None, failure: str | None) -> tuple[str, str]:
    if problem is not None:
        return "error", f"Lỗi khi theo dõi lần đo: {problem}."
    if killed:
        return "killed", KILLED_MESSAGE if code is not None else f"{KILLED_MESSAGE} Tiến trình chưa dừng hẳn."
    if code in EXIT_OUTCOMES:
        outcome, text = EXIT_OUTCOMES[code]
        if code == 10 and failure in FAILURE_MESSAGES:
            text = f"{text} {FAILURE_MESSAGES[failure]}"
        return outcome, text
    return "unknown", UNKNOWN_EXIT_MESSAGE.format(code=code)


def _progress_fields(data: bytes) -> dict[str, Any] | None:
    """{step, index, total[, started_at]} of progress.json, or None for a partial or odd file."""
    if len(data) > MAX_PROGRESS_BYTES:
        return None
    try:
        value = loads_strict(data)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if type(value) is not dict:
        return None
    step, index, total, started = (value.get(key) for key in ("step", "index", "total", "started_at"))
    if type(step) is not str or not _STEP.fullmatch(step) or type(index) is not int or not 0 <= index <= 1000:
        return None
    if total is not None and (type(total) is not int or not index <= total <= 1000):
        return None
    fields: dict[str, Any] = {"step": step, "index": index, "total": total}
    if type(started) is str and 0 < len(started) <= 40:
        fields["started_at"] = started
    return fields


@dataclass(frozen=True)
class _Run:
    """One measurement started by this manager."""
    process: Any
    stamp: str
    run_dir: Path
    log: str  # relative to the root, for the task and the events
    handle: Any
    started: float  # clock()
    before: tuple[str, str | None]  # the main profile (state, content_sha256) before the run
    cancelled: threading.Event = field(default_factory=threading.Event)  # the manager wrote the cancel file


class HardwareManager:
    """One per Control Center (control_center._hardware). Thread-safe; status() and close() never raise."""

    def __init__(self, root: Path, *, store_event: Callable[[str, str, dict[str, Any]], None] | None,
                 scheduler_view: Callable[[], QueueView], popen: Callable[..., Any] | None = None,
                 clock: Callable[[], float] | None = None, now: Callable[[], datetime] | None = None,
                 observe: Callable[..., Any] | None = None, kill_tree: Callable[[int], None] | None = None,
                 mutex_name: str | None = None, python: str | None = None, script: Path | None = None,
                 limit_seconds: float = MEASURE_LIMIT_SECONDS, grace_seconds: float = CANCEL_GRACE_SECONDS,
                 poll_seconds: float = POLL_SECONDS, observe_seconds: float = OBSERVE_CACHE_SECONDS):
        self.root = Path(root)
        self._store_event, self._scheduler_view = store_event, scheduler_view
        self._popen = popen if popen is not None else subprocess.Popen
        self._clock = clock if clock is not None else time.monotonic
        self._now = now if now is not None else _local_now
        self._observe = observe if observe is not None else _observe
        self._kill_tree = kill_tree if kill_tree is not None else kill_process_tree
        # I10: the name changes only through this parameter (tests), never argv or the environment.
        self._mutex_name = mutex_name if mutex_name is not None else MEASURE_MUTEX
        self._python = python if python is not None else sys.executable
        self._script = Path(script) if script is not None else MEASURE_SCRIPT
        self._limit, self._grace, self._poll = limit_seconds, grace_seconds, poll_seconds
        self._observe_seconds = observe_seconds
        self._guard = guard_install(self._python, self.root)
        install = venv_install(self._python)
        self._replacements = path_replacements(self.root, *(((install, "<install>"),) if install else ()))
        self._lock = threading.Lock()  # _run and _task
        self._start_lock = threading.Lock()  # one start at a time
        self._override_lock = threading.Lock()  # the switch file (section 3.6)
        self._observe_lock = threading.Lock()
        self._observed: tuple[float, str | None, Observation | None] | None = None
        self._closed = threading.Event()
        self._run: _Run | None = None
        self._task: dict[str, Any] | None = None
        self._previous_checked = 0.0
        running = measurement_running(self._mutex_name)  # a measurement left by an earlier Control Center
        if running is not False:
            self._task, self._previous_checked = self._previous_task(running), self._clock()

    # --- GET ------------------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """GET /api/hardware-profile (section 6.1). Never raises: a 404 would read as an old Control
        Center, and a 500 would hide the page."""
        try:
            observed = self._observation()
            resolution = hardware_profile.resolve(self.root, observed=observed)
            body = hardware_status.payload(self.root, resolution, observed, self._clean)
        except Exception as error:  # noqa: BLE001 - the Settings page must always get an answer
            body = hardware_status.fallback(error)
        return {**body, "task": self.task(), "can_remeasure": self.can_remeasure()}

    def _observation(self) -> Observation | None:
        """The light observation, at most once per OBSERVE_CACHE_SECONDS; None when it failed.

        Always for the ffmpeg.exe the profile measured, or ffmpeg_changed would be reported wrongly.
        """
        path = _ffmpeg_path(self.root)
        with self._observe_lock:
            cached = self._observed
            if cached is not None and cached[1] == path and self._clock() - cached[0] < self._observe_seconds:
                return cached[2]
            try:
                value = self._observe(self.root, ffmpeg_exe=path)
            except Exception:  # noqa: BLE001 - shown as observed: null with the not_observed warning
                value = None
            if type(value) is not Observation:
                value = None
            self._observed = (self._clock(), path, value)
            return value

    def _drop_observation(self) -> None:
        with self._observe_lock:
            self._observed = None

    def task(self) -> dict[str, Any] | None:
        """The measurement: running (step, index, total) or finished (exit, outcome, message)."""
        try:
            self._recheck_previous()
        except Exception:  # noqa: BLE001 - the task stays as it was; the next call asks again
            pass
        with self._lock:
            return None if self._task is None else dict(self._task)

    def can_remeasure(self) -> dict[str, Any]:
        blocker = self._blocker()
        if blocker is None:
            return {"ok": True, "reason": None, "text": None}
        return {"ok": False, "reason": blocker[0], "text": blocker[1]}

    def _blocker(self) -> tuple[str, str] | None:
        """busy, not_paused or not_idle (section 6.1); a queue that cannot be read is not idle."""
        with self._lock:
            task = self._task
        if task is not None and task.get("running"):
            return "busy", task.get("message") or BUSY_MESSAGE
        try:
            view = self._scheduler_view()
        except Exception:  # noqa: BLE001 - never guess that the queue is idle
            return "not_idle", QUEUE_UNKNOWN_MESSAGE
        if type(view) is not QueueView:
            return "not_idle", QUEUE_UNKNOWN_MESSAGE
        if view.paused is not True:
            return "not_paused", NOT_PAUSED_MESSAGE
        if view.active or view.busy or view.in_process:
            return "not_idle", NOT_IDLE_MESSAGE
        return None

    # --- a measurement left by an earlier Control Center --------------------------------------

    def _previous_task(self, running: bool | None) -> dict[str, Any]:
        return {"running": True, "previous_run": True, "step": None, "index": None, "total": None,
                "started_at": None, "message": PREVIOUS_RUN_MESSAGE if running else PROBE_UNKNOWN_MESSAGE}

    def _recheck_previous(self) -> None:
        with self._lock:
            task = self._task
            if task is None or not task.get("previous_run") or not task.get("running"):
                return
            if self._clock() - self._previous_checked < PREVIOUS_PROBE_SECONDS:
                return
            self._previous_checked = self._clock()
        if measurement_running(self._mutex_name) is not False:
            return
        note, _failure = _profile_note(self.root, None)
        finished = {"running": False, "previous_run": True, "exit": None, "outcome": "previous_done",
                    "message": self._clean(f"{PREVIOUS_DONE_MESSAGE} {note}"), "finished_at": self._iso_now()}
        with self._lock:
            if self._task is task:
                self._task = finished
        self._drop_observation()

    # --- "Đo lại" (section 6.3) -----------------------------------------------------------------

    def start(self, action: Any) -> dict[str, Any]:
        """POST /api/hardware-profile/remeasure: ActionConflict (409) or the measurement in the background."""
        if type(action) is not str or action not in ACTIONS:
            raise ValueError(UNKNOWN_ACTION_MESSAGE)
        with self._start_lock:
            if self._closed.is_set():
                raise _conflict("busy", CLOSING_MESSAGE)
            self._refuse_unless_free()
            run = self._spawn()
            if self._closed.is_set():  # close() ran meanwhile and saw no run: it stops the same way
                with contextlib.suppress(OSError):
                    self._write_cancel(run)
        threading.Thread(target=self._watch, args=(run,), daemon=True, name="biliflow-hardware-measure").start()
        self._event("HARDWARE_MEASURE_STARTED", "Bắt đầu đo phần cứng (Đo lại).", ok=True, log=run.log)
        return self.status()

    def _refuse_unless_free(self) -> None:
        """Own task first, then the private mutex (an earlier or hand-started run), then the queue."""
        blocker = self._blocker()
        if blocker is not None and blocker[0] == "busy":
            raise _conflict(*blocker)
        running = measurement_running(self._mutex_name)
        if running is not False:
            with self._lock:
                if self._task is None or not self._task.get("running"):
                    self._task, self._previous_checked = self._previous_task(running), self._clock()
            raise _conflict("busy", PREVIOUS_RUN_MESSAGE if running else PROBE_UNKNOWN_MESSAGE)
        if blocker is not None:
            raise _conflict(*blocker)

    def _command(self, stamp: str) -> list[str]:
        command = [_system32("WindowsPowerShell", "v1.0", "powershell.exe"), "-NoProfile", "-NonInteractive",
                   "-ExecutionPolicy", "Bypass", "-File", str(self._script),
                   "-ProjectRoot", str(self.root), "-GuardRoot", str(self.root)]
        if self._guard is not None:
            command += ["-GuardRoot", str(self._guard)]
        # -LaunchedBy fills writer.launched_by of the profile; a person running the script leaves it out.
        command += ["-Python", str(self._python), "-Stamp", stamp, "-LaunchedBy", "control-center"]
        # PowerShell 5.1 drops an empty argument and merges the next one after a trailing backslash.
        bad = next((value for value in command[1:] if not value or '"' in value or value.endswith("\\")), None)
        if bad is not None:
            raise ValueError(self._clean(f"Đường dẫn không truyền được qua PowerShell: {bad}"))
        return command

    def _spawn(self) -> _Run:
        now = self._now()
        stamp = f"{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
        command = self._command(stamp)
        log_path = self.root.joinpath(*LOG_DIR, f"hardware-measure-{stamp}.log")
        log = log_path.relative_to(self.root).as_posix()
        before = _profile_mark(self.root)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handle = log_path.open("ab")
        except OSError as error:
            raise RuntimeError(self._clean(f"Không mở được file log của lần đo ({_describe(error)}).")) from None
        try:
            process = self._popen(command, cwd=str(self.root), stdin=subprocess.DEVNULL, stdout=handle,
                                  stderr=subprocess.STDOUT, shell=False,
                                  creationflags=_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW)
        except Exception as error:  # noqa: BLE001 - reported to the user; the log handle is closed
            handle.close()
            message = self._clean(START_FAILED_MESSAGE.format(error=_describe(error)))
            with self._lock:
                self._task = {"running": False, "previous_run": False, "exit": None, "outcome": "start_failed",
                              "message": message, "finished_at": self._iso_now(), "log": log}
            raise RuntimeError(message) from None
        run = _Run(process, stamp, self.root / "temp" / f"{RUN_DIR_PREFIX}{stamp}", log, handle, self._clock(),
                   before)
        with self._lock:
            self._run = run
            self._task = {"running": True, "previous_run": False, "step": "starting", "index": None,
                          "total": None, "started_at": now.isoformat(timespec="seconds"), "log": log}
        return run

    def _watch(self, run: _Run) -> None:
        """The measurement's thread: progress, the 15-minute limit, then the result. Always ends the task."""
        killed, problem = False, None
        try:
            killed = self._wait(run)
        except Exception as error:  # noqa: BLE001 - the task must end with a reason
            problem = _describe(error)
        finally:
            with contextlib.suppress(OSError):
                run.handle.close()
        self._finish(run, killed, problem)

    def _wait(self, run: _Run) -> bool:
        """Poll until the child exits. Past the limit: the cancel file first, a grace period, and only
        then the whole tree (section 6.3). After close() the tree is never killed. True when killed."""
        cancel_at = None  # the grace counts from the cancel file, so a clock jump never skips it
        while run.process.poll() is None:
            self._read_progress(run)
            now = self._clock()
            if cancel_at is None and now - run.started >= self._limit:
                cancel_at = now
                with contextlib.suppress(OSError):
                    self._write_cancel(run)
            elif cancel_at is not None and now - cancel_at >= self._grace and not self._closed.is_set():
                self._kill_tree(run.process.pid)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    run.process.wait(timeout=EXIT_WAIT_SECONDS)
                return True
            time.sleep(self._poll)
        return False

    def _read_progress(self, run: _Run) -> None:
        """progress.json of the run folder, replaced whole per step; a missing or partial one keeps the last."""
        try:
            with (run.run_dir / PROGRESS_FILE).open("rb") as handle:
                data = handle.read(MAX_PROGRESS_BYTES + 1)
        except OSError:
            return
        fields = _progress_fields(data)
        if fields is None:
            return
        with self._lock:
            if self._run is run and self._task is not None and self._task.get("running"):
                self._task = {**self._task, **fields}

    def _write_cancel(self, run: _Run) -> None:
        run.run_dir.mkdir(parents=True, exist_ok=True)
        (run.run_dir / CANCEL_FILE).write_bytes(b"")
        run.cancelled.set()

    def _finish(self, run: _Run, killed: bool, problem: str | None) -> None:
        try:
            code = run.process.poll()
        except Exception as error:  # noqa: BLE001 - the task still ends, with the reason
            code, problem = None, problem or _describe(error)
        note, failure = _profile_note(self.root, run.before)  # read again: what was written, if anything
        outcome, text = _outcome(code, killed, problem, failure)
        message = self._clean(f"{text} {note}")
        task = {"running": False, "previous_run": False, "exit": code, "outcome": outcome, "message": message,
                "finished_at": self._iso_now(), "log": run.log}
        with self._lock:
            if self._run is run:
                self._run = None
            self._task = task
        self._drop_observation()
        self._remove_own_cancel(run)
        self._event("HARDWARE_MEASURE_FINISHED", message, ok=code == 0 and not killed, exit=code,
                    outcome=outcome, log=run.log)

    def _remove_own_cancel(self, run: _Run) -> None:
        """The empty cancel file the manager wrote, and the run folder when nothing else is left in it."""
        if not run.cancelled.is_set():
            return
        with contextlib.suppress(OSError):
            cancel = run.run_dir / CANCEL_FILE
            if [path.name for path in run.run_dir.iterdir()] == [CANCEL_FILE] and cancel.stat().st_size == 0:
                cancel.unlink()
                run.run_dir.rmdir()

    def close(self) -> None:
        """Control Center stop: write the cancel file of a running measurement. Never waits, kills or
        raises; the measurement releases its locks and exits on its own (section 6.3)."""
        try:
            self._closed.set()
            with self._lock:
                run = self._run
            if run is not None and run.process.poll() is None:
                self._write_cancel(run)
        except Exception:  # noqa: BLE001 - stopping the Control Center must go on
            pass

    # --- the two switches (sections 3.6, 5.6) ---------------------------------------------------

    def set_cpu_only(self, enabled: Any) -> dict[str, Any]:
        """POST /api/hardware-profile/cpu-only: both switches written, the untested one kept.

        A broken switch file is replaced: the switch the user set gets its value and the other one
        is off, as when the file is missing (it is never guessed from the broken file).
        """
        _require_bool(enabled)
        with self._override_lock:
            current = read_override(self.root)
            kept = current.untested_fast if current.state == "ok" else None
            self._write_switches(cpu_only=enabled, untested_fast=kept)
        self._event("HARDWARE_CPU_ONLY_CHANGED",
                    f"Chỉ dùng CPU: {'bật' if enabled else 'tắt'} (có hiệu lực cho job mới từ Đợt 2).",
                    cpu_only=enabled, untested_fast=kept is not None, replaced_invalid=current.state == "invalid")
        return self.status()

    def set_untested_fast(self, enabled: Any) -> dict[str, Any]:
        """POST /api/hardware-profile/untested-fast: on only while resolve() says ready_untested, bound
        to the profile's card (name, compute capability); off always. The CPU-only switch is kept."""
        _require_bool(enabled)
        with self._override_lock:
            current = read_override(self.root)
            switch = self._card_switch() if enabled else None
            kept = current.cpu_only if current.state == "ok" else False
            self._write_switches(cpu_only=kept, untested_fast=switch)
        card = "" if switch is None else f" cho {switch.gpu_name} ({switch.compute_capability})"
        self._event("HARDWARE_UNTESTED_FAST_CHANGED",
                    f"Chế độ nhanh trên card chưa qua cổng: {'bật' if enabled else 'tắt'}{card} "
                    "(có hiệu lực cho job mới từ Đợt 2).",
                    enabled=enabled, gpu_name=None if switch is None else switch.gpu_name,
                    compute_capability=None if switch is None else switch.compute_capability,
                    replaced_invalid=current.state == "invalid")
        return self.status()

    def _card_switch(self) -> UntestedFast:
        resolution = hardware_profile.resolve(self.root, observed=self._observation())
        gpus = () if resolution.profile is None else resolution.profile.gpus
        if resolution.status != READY_UNTESTED or len(gpus) != 1:
            raise _conflict("not_untested", NOT_UNTESTED_MESSAGE.format(status=resolution.status))
        return UntestedFast(gpus[0].name, gpus[0].compute_capability, self._iso_now())

    def _write_switches(self, *, cpu_only: bool, untested_fast: UntestedFast | None) -> None:
        try:
            write_override(self.root, cpu_only=cpu_only, untested_fast=untested_fast, now=self._now())
        except OSError as error:
            raise RuntimeError(self._clean(f"Không ghi được file công tắc phần cứng ({_describe(error)}).")) from None

    # --- shared ---------------------------------------------------------------------------------

    def _clean(self, text: object) -> str:
        return sanitize_message(text, self._replacements, MESSAGE_LIMIT)

    def _iso_now(self) -> str:
        return self._now().isoformat(timespec="seconds")

    def _event(self, event_type: str, message: str, **payload: Any) -> None:
        callback = self._store_event
        if callback is None:
            return
        try:
            callback(event_type, self._clean(message), payload)
        except Exception:  # noqa: BLE001 - an event store problem must not break a task or a switch
            pass
