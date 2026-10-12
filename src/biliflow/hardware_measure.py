"""The locked hardware measurement of "Đo lại" (docs/HARDWARE_PROFILE.md section 6.3).

``python -m biliflow.hardware_profile measure ...`` (through scripts/hardware-measure.ps1) ends in
``main()``. In order, on the main thread:

1. argument checks: exit 64 before any lock, child process or write (I11: a ProjectRoot that
   ``job_purge.allowed_project_root`` refuses is refused here);
2. the measurement's own mutex, tried for 0 s (held: exit 75);
3. the idle check of every guard root (hardware_idle); ``--wait-minutes N`` rechecks every 30 s
   while holding the own mutex only (it blocks no queue work: only another measurement);
4. the GPU mutex, the FinalRender mutex and each guard root's existing ``state\\final-render.lock``
   (hardware_locks), then the idle check again;
5. detection (hardware_detect.detect), the synthetic clip and the FFmpeg trials (hardware_trials),
   every command checking the cancel file and the 10-minute budget each second (hardware_tools);
6. the GPU and export locks are released, the profile is written (main, then last-good only for
   an "ok" attempt), the own mutex is released and the run folder removed.

Exit codes: 0 ok (main and last-good), 10 failed attempt (main only), 64 bad arguments or root,
70 internal error, 75 busy / not idle / cancelled, 124 time budget spent; 64, 70, 75 and 124
write no profile. Lock names are constants; only the ``names`` parameter replaces them (I10).
Also here: the measurement history (section 3.2) and ``hysteresis_choice`` (section 5.5).
Imported only by hardware_profile's ``__main__`` block and tests, never by a scan stage.
"""
from __future__ import annotations

import argparse
import contextlib
import math
import os
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from biliflow import __version__
from biliflow.hardware_detect import DETECT_STEPS, Detection, detect, detect_replacements
from biliflow.hardware_idle import REASON_TEXTS, IdleReport, check_idle, pid_alive
from biliflow.hardware_locks import (
    LOCK_TRY_SECONDS, LockBusy, LockNames, NamedMutex, acquire_export_locks, unique_roots,
)
from biliflow.hardware_schema import (
    FAILURE_MESSAGES, MAX_MEASUREMENTS, MAX_MESSAGE, ProfileRead, build_profile, canonical_sha256, classify_attempt,
    previous_had_cuda, profile_path, read_profile, sanitize_message, write_json_atomic, write_profile,
)
from biliflow.hardware_system import HardwareSystem, WindowsSystem
from biliflow.hardware_tools import (
    CANCEL_FILE, POLL_SECONDS, BudgetExceeded, Cancelled, GuardedSystem, RunGuard, ToolRunner, kill_process_tree,
)
from biliflow.hardware_trials import CLIP_LABEL, Timing, TrialContext, TrialReport, run_trials

EXIT_OK = 0
EXIT_FAILED = 10
EXIT_USAGE = 64
EXIT_INTERNAL = 70
EXIT_BUSY = 75
EXIT_TIMEOUT = 124

CODE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_FFMPEG_PARTS = ("tools", "ffmpeg", "bin")
RUN_DIR_PREFIX = "hardware-measure-"
PROGRESS_FILE = "progress.json"
RUN_DIR_LEFTOVERS = frozenset({CANCEL_FILE, PROGRESS_FILE})  # the manager may write "cancel" first
STEPS = ("idle", "locks", *DETECT_STEPS, "clip", "encoders", "nvdec_codecs", "logo_routing", "write")
MEASURE_BUDGET_SECONDS = 600
WAIT_RECHECK_SECONDS = 30.0
MAX_WAIT_MINUTES = 60
LAUNCHED_BY = ("control-center", "script")
STAMP = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{4}")
_WAIT_MINUTES = re.compile(r"[0-9]{1,2}")
FAILURE_STEPS = {
    "cuda_unavailable": "torch_probe", "torch_probe_timeout": "torch_probe", "torch_probe_crashed": "torch_probe",
    "nvml_failed": "nvml", "nvml_torch_mismatch": "torch_probe", "adapters_unreadable": "adapters",
}

# Section 5.5: a knob that gives identical results changes only after 2 consecutive clean,
# plugged-in, same-fingerprint measurements show the new value at least 10% faster and at least
# 60 s faster on a reference film (a 2.5-hour film by convention, not a measured length).
REFERENCE_FILM_SECONDS = 9000
HYSTERESIS_RUNS = 2
HYSTERESIS_MIN_GAIN = 0.10
HYSTERESIS_MIN_SECONDS = 60.0
_TOLERANCE = 1e-9


class UsageError(ValueError):
    """Bad arguments: exit 64 before anything is locked, run or written."""


# --- step 1: arguments --------------------------------------------------------------------------

class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:  # argparse would exit 2; the contract says 64
        raise UsageError(message)

    def exit(self, status: int = 0, message: str | None = None) -> Any:
        raise UsageError(message or f"thoát {status}")


def build_parser() -> argparse.ArgumentParser:
    """No option names a mutex or a lock file (I10)."""
    parser = _Parser(prog="python -m biliflow.hardware_profile measure", add_help=False, allow_abbrev=False)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--guard-root", action="append", default=[])
    parser.add_argument("--ffmpeg-dir")
    parser.add_argument("--stamp")
    parser.add_argument("--wait-minutes", default="0")
    parser.add_argument("--launched-by", default="script")
    return parser


@dataclass(frozen=True)
class Settings:
    project_root: Path  # resolved
    guard_roots: tuple[Path, ...]  # resolved, unique, the project root first
    ffmpeg_dir: Path
    stamp: str
    wait_minutes: int
    launched_by: str

    @property
    def run_dir(self) -> Path:
        return self.project_root / "temp" / f"{RUN_DIR_PREFIX}{self.stamp}"


def new_stamp() -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"


def _allowed_project_root(root: Path) -> bool:
    from biliflow.job_purge import allowed_project_root
    return allowed_project_root(root)


def _absolute_dir(value: str, option: str) -> Path:
    path = Path(value)
    if not value or not path.is_absolute():
        raise UsageError(f"{option} phải là đường dẫn tuyệt đối")
    if not path.is_dir():
        raise UsageError(f"{option} không phải thư mục có sẵn")
    return path


def _ffmpeg_dir(value: str | None, code_dir: Path) -> Path:
    """Only the option itself is checked. A folder without ffmpeg.exe is measured all the same: detection
    records detection.ffmpeg.ok false and the warning ffmpeg_unavailable (section 6.3, step 8)."""
    folder = _absolute_dir(value, "--ffmpeg-dir") if value is not None else code_dir.joinpath(*DEFAULT_FFMPEG_PARTS)
    return Path(os.path.abspath(folder))


def _is_link(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except OSError:
        return False
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & reparse)


def _check_run_dir(run_dir: Path) -> None:
    """A run folder that already exists may hold only the manager's cancel file and a progress file."""
    if not os.path.lexists(run_dir):
        return
    if _is_link(run_dir) or not run_dir.is_dir():
        raise UsageError("thư mục chạy của Stamp này không phải thư mục thường")
    if {entry.name for entry in run_dir.iterdir()} - RUN_DIR_LEFTOVERS:
        raise UsageError("thư mục chạy của Stamp này đã có dữ liệu; dùng Stamp khác")


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    arguments = list(argv)
    if arguments[:1] != ["measure"]:
        raise UsageError("lệnh phải là: measure --project-root <thư mục> ...")
    return build_parser().parse_args(arguments[1:])


def check_arguments(options: argparse.Namespace, *, allowed: Callable[[Path], bool] = _allowed_project_root,
                    code_dir: Path = CODE_DIR, stamp: Callable[[], str] = new_stamp) -> Settings:
    """Read-only checks of step 1; raises UsageError (exit 64)."""
    root = _absolute_dir(options.project_root, "--project-root")
    if not allowed(root):
        raise UsageError("--project-root không được phép: chỉ thư mục cài của bản code này hoặc một thư mục "
                         "trong temp\\ của nó")
    guards = [_absolute_dir(value, "--guard-root") for value in options.guard_root]
    ffmpeg_dir = _ffmpeg_dir(options.ffmpeg_dir, code_dir)
    chosen = stamp() if options.stamp is None else options.stamp
    if type(chosen) is not str or not STAMP.fullmatch(chosen):
        raise UsageError("--stamp phải có dạng yyyyMMdd-HHmmss-xxxx (xxxx: 4 chữ hex thường)")
    wait = options.wait_minutes
    if not _WAIT_MINUTES.fullmatch(wait) or int(wait) > MAX_WAIT_MINUTES:
        raise UsageError(f"--wait-minutes phải là số nguyên 0..{MAX_WAIT_MINUTES}")
    if options.launched_by not in LAUNCHED_BY:
        raise UsageError(f"--launched-by phải thuộc {list(LAUNCHED_BY)}")
    resolved = root.resolve()
    settings = Settings(resolved, unique_roots([resolved, *guards]), ffmpeg_dir, chosen, int(wait),
                        options.launched_by)
    _check_run_dir(settings.run_dir)
    return settings


# --- what the run uses (tests replace parts of it; the names only through ``names``) ------------

def _local_now() -> datetime:
    return datetime.now().astimezone()


def _stderr_line(text: str) -> None:
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


@dataclass(frozen=True)
class Dependencies:
    names: LockNames = LockNames()
    system: HardwareSystem | None = None  # None: WindowsSystem through the guarded runner
    popen: Callable[..., Any] = subprocess.Popen
    kill: Callable[[Any], None] = kill_process_tree
    connect: Callable[..., Any] = sqlite3.connect
    alive: Callable[[int], bool] = pid_alive
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], datetime] = _local_now
    python_exe: Path = Path(sys.executable)
    budget_seconds: float = MEASURE_BUDGET_SECONDS
    lock_seconds: float = LOCK_TRY_SECONDS
    poll_seconds: float = POLL_SECONDS
    log: Callable[[str], None] = _stderr_line


class Progress:
    """progress.json ({step, index, total, started_at}), replaced whole at each step.

    Each step first checks the cancel file and the budget; it raises Cancelled or BudgetExceeded.
    """

    def __init__(self, run_dir: Path, started_at: str, guard: RunGuard, log: Callable[[str], None]):
        self.path = run_dir / PROGRESS_FILE
        self.started_at = started_at
        self.guard = guard
        self.log = log

    def __call__(self, step: str) -> None:
        self.guard.check()
        index = STEPS.index(step) + 1
        try:
            write_json_atomic(self.path, {"step": step, "index": index, "total": len(STEPS),
                                          "started_at": self.started_at})
        except OSError as error:  # the file is only shown on the dashboard; the run goes on
            self.log(f"Không ghi được progress.json ({type(error).__name__}).")
        self.log(f"Bước {index}/{len(STEPS)}: {step}")


# --- steps 2-9 ---------------------------------------------------------------------------------

def run_measurement(settings: Settings, deps: Dependencies | None = None) -> int:
    """Steps 2-9 on the calling (main) thread; returns the exit code."""
    deps = deps if deps is not None else Dependencies()
    replacements = detect_replacements(settings.project_root, ffmpeg_dir=settings.ffmpeg_dir,
                                       python_exe=deps.python_exe)

    def say(text: object) -> None:
        deps.log(sanitize_message(text, replacements, 1000))

    try:
        return _run(settings, deps, say)
    except Cancelled:
        say("Đã hủy (có file cancel); không ghi hồ sơ.")
        return EXIT_BUSY
    except BudgetExceeded:
        say(f"Quá giờ ({int(deps.budget_seconds)} s); không ghi hồ sơ.")
        return EXIT_TIMEOUT
    except LockBusy as busy:
        say(f"Bận: {busy}")
        return EXIT_BUSY
    except Exception as error:  # noqa: BLE001 - an internal error still ends with its own code
        say(f"Lỗi nội bộ: {type(error).__name__}: {str(error)[:MAX_MESSAGE]}")
        return EXIT_INTERNAL


def _run(settings: Settings, deps: Dependencies, say: Callable[[object], None]) -> int:
    acquired = False
    try:
        with contextlib.ExitStack() as own_stack:
            own = NamedMutex(deps.names.own)
            if not own.try_acquire(0):
                say("Bận: đang có một lần đo khác.")
                return EXIT_BUSY
            own_stack.callback(own.release)
            acquired = True
            if own.abandoned:
                say("Mutex riêng bị bỏ rơi (lần đo trước bị dừng cứng); tính là đã lấy.")
            settings.run_dir.mkdir(parents=True, exist_ok=True)
            return _measure(settings, deps, say)
    finally:
        if acquired:
            _remove_run_dir(settings, say)


def _remove_run_dir(settings: Settings, say: Callable[[object], None]) -> None:
    run_dir = settings.run_dir
    if not os.path.lexists(run_dir) or _is_link(run_dir):
        return
    try:
        shutil.rmtree(run_dir)
    except OSError as error:
        say(f"Không xóa được thư mục chạy ({type(error).__name__}).")


def _idle_check(settings: Settings, deps: Dependencies) -> IdleReport:
    return check_idle(settings.guard_roots, connect=deps.connect, alive=deps.alive)


def _not_idle(report: IdleReport, say: Callable[[object], None]) -> int:
    busy = report.busy_root
    reason = REASON_TEXTS.get(busy.reason, busy.reason) if busy is not None else "?"
    say(f"Không rảnh ({busy.root if busy is not None else '?'}): {reason}")
    return EXIT_BUSY


def wait_until_idle(check: Callable[[], IdleReport], *, wait_seconds: float, guard: RunGuard,
                    sleep: Callable[[float], None], recheck_seconds: float = WAIT_RECHECK_SECONDS) -> IdleReport:
    """Recheck every ``recheck_seconds`` until idle or ``wait_seconds`` pass; the cancel file stops it."""
    clock = guard.clock
    report = check()
    end = clock() + wait_seconds
    while not report.idle and clock() < end:
        next_check = min(end, clock() + recheck_seconds)
        while clock() < next_check:
            if guard.cancelled():
                raise Cancelled
            sleep(min(1.0, max(next_check - clock(), 0.0)))
        report = check()
    return report


def _measure(settings: Settings, deps: Dependencies, say: Callable[[object], None]) -> int:
    guard = RunGuard(settings.run_dir, clock=deps.clock)
    progress = Progress(settings.run_dir, deps.now().isoformat(timespec="seconds"), guard, say)
    progress("idle")
    first = wait_until_idle(lambda: _idle_check(settings, deps), wait_seconds=settings.wait_minutes * 60,
                            guard=guard, sleep=deps.sleep)
    if not first.idle:
        return _not_idle(first, say)
    guard.start_budget(deps.budget_seconds)
    started_at = deps.now().isoformat(timespec="seconds")
    with contextlib.ExitStack() as work:
        progress("locks")
        held = acquire_export_locks(work, deps.names, settings.guard_roots, timeout_seconds=deps.lock_seconds)
        for name in held.abandoned:
            say(f"Mutex {name} bị bỏ rơi trước đó; tính là đã lấy.")
        again = _idle_check(settings, deps)
        if not again.idle:
            return _not_idle(again, say)
        detection, trials, system = _detect_and_try(settings, deps, guard, progress, say)
        last = _idle_check(settings, deps)
        power_now = system.power().ac_online
    progress("write")
    quality = run_quality((first, again, last), (detection.detection["power"]["ac_online"], power_now))
    document = build_document(settings, detection, trials, started_at=started_at,
                              finished_at=deps.now().isoformat(timespec="seconds"), quality=quality)
    guard.check()
    ok = document["attempt"]["result"] == "ok"
    write_profile(settings.project_root, document)
    if ok:
        _write_last_good(settings.project_root, document, say)
    failure = document["attempt"]["failure"]
    say("Xong: lần đo ok." if ok else f"Xong: lần đo thất bại ({failure['code']}).")
    return EXIT_OK if ok else EXIT_FAILED


def _write_last_good(root: Path, document: dict[str, Any], say: Callable[[object], None]) -> None:
    """The last-good copy, after the main file. The main file already holds this profile, so a failure
    here keeps the earlier copy and the exit stays 0: exit 70 always means nothing was written."""
    try:
        write_json_atomic(profile_path(root, "last_good"), document)
    except OSError as error:
        say(f"Không cập nhật được bản last-good ({type(error).__name__}); hồ sơ chính đã ghi, bản cũ giữ nguyên.")


def _detect_and_try(settings: Settings, deps: Dependencies, guard: RunGuard, progress: Progress,
                    say: Callable[[object], None]) -> tuple[Detection, TrialReport, Any]:
    runner = ToolRunner(guard, popen=deps.popen, kill=deps.kill, poll_seconds=deps.poll_seconds)
    system = deps.system if deps.system is not None else GuardedSystem(WindowsSystem(), runner)
    detection = detect(settings.project_root, ffmpeg_dir=settings.ffmpeg_dir, python_exe=deps.python_exe,
                       system=system, progress=progress)
    replacements = detect_replacements(settings.project_root, ffmpeg_dir=settings.ffmpeg_dir,
                                       python_exe=deps.python_exe)
    context = TrialContext(runner, settings.ffmpeg_dir / "ffmpeg.exe", settings.ffmpeg_dir / "ffprobe.exe",
                           settings.run_dir, replacements, progress, say)
    return detection, run_trials(context, detection.detection["ffmpeg"]), system


def run_quality(reports: Sequence[IdleReport], ac_online: Sequence[bool | None]) -> str:
    """battery when on battery at either end; contended when a download ran or the queue woke up."""
    if any(value is False for value in ac_online):
        return "battery"
    if any(report.contended or not report.idle for report in reports):
        return "contended"
    return "clean"


# --- step 9: the profile ------------------------------------------------------------------------

def build_document(settings: Settings, detection: Detection, trials: TrialReport, *, started_at: str,
                   finished_at: str, quality: str) -> dict[str, Any]:
    """The profile of section 3.2 (hardware_schema.build_profile validates it)."""
    found = detection.detection
    merged = {**found, "ffmpeg": {**found["ffmpeg"], "encoders": trials.encoders, "nvdec": trials.nvdec,
                                  "nvdec_codecs": trials.nvdec_codecs}}
    main = read_profile(settings.project_root, "main")
    last_good = read_profile(settings.project_root, "last_good")
    outcome = classify_attempt(merged, detection.gate_key, previous=previous_had_cuda(main, last_good))
    fingerprint = canonical_sha256(detection.gate_key)
    clip = None if trials.clip_sha256 is None else f"{CLIP_LABEL}:{trials.clip_sha256}"
    records = measurement_records(trials.timings, stamp=settings.stamp, fingerprint=fingerprint, at=finished_at,
                                  quality=quality, clip=clip)
    previous_records = main.document["measurements"] if main.state == "ok" and main.document else []
    failure = None if outcome.failure_code is None else {
        "code": outcome.failure_code, "step": FAILURE_STEPS[outcome.failure_code],
        "message": failure_message(outcome.failure_code, merged, detection.gate_key),
    }
    return build_profile(
        written_at=finished_at,
        writer={"biliflow_version": __version__, "mode": "measure", "launched_by": settings.launched_by},
        attempt={"result": outcome.result, "failure": failure, "started_at": started_at,
                 "finished_at": finished_at, "budget_seconds": MEASURE_BUDGET_SECONDS},
        gate_key=detection.gate_key, info=detection.info, detection=merged,
        measurements=merge_measurements(previous_records, records, fingerprint),
        history={"had_cuda": outcome.had_cuda,
                 "first_cuda_at": first_cuda_at((main, last_good), had_cuda=outcome.had_cuda,
                                                torch_ok=merged["torch_cuda"]["ok"], now=finished_at)},
    )


def failure_message(code: str, detection: Mapping[str, Any], gate_key: Mapping[str, Any]) -> str:
    """What failed, in the words of the step that found it; the fixed text of section 7.2 otherwise."""
    torch, gpus = detection["torch_cuda"], gate_key["nvidia_gpus"]
    if code == "nvml_torch_mismatch":
        nvml = ", ".join(gpu["compute_capability"] for gpu in gpus) or "-"
        found = torch["capability"] or "-"
        detail = f"NVML: {len(gpus)} GPU ({nvml}); torch: {torch['device_count']} GPU ({found})"
    elif code == "nvml_failed":
        detail = detection["nvml"]["error"]
    elif code == "adapters_unreadable":
        detail = detection["adapters"]["error"]
    else:
        detail = torch["error"]
    return sanitize_message(detail or FAILURE_MESSAGES[code], (), MAX_MESSAGE) or FAILURE_MESSAGES[code]


def first_cuda_at(reads: Sequence[ProfileRead], *, had_cuda: bool, torch_ok: bool, now: str) -> str | None:
    """Kept from an earlier file; this run's time when CUDA works now for the first time."""
    if not had_cuda:
        return None
    for read in reads:
        if read.state == "ok" and read.document is not None and read.document["history"]["first_cuda_at"]:
            return read.document["history"]["first_cuda_at"]
    return now if torch_ok else None


def measurement_records(timings: Sequence[Timing], *, stamp: str, fingerprint: str, at: str, quality: str,
                        clip: str | None) -> list[dict[str, Any]]:
    """Section 3.2 records; ids ``m-<stamp>-<n>`` group one run's records (hysteresis_choice)."""
    return [{"id": f"m-{stamp}-{index}", "kind": timing.kind, "subject": timing.subject, "at": at,
             "fingerprint_sha256": fingerprint, "clip": clip, "wall_seconds": timing.wall_seconds,
             "cpu_seconds": None, "value": timing.value, "unit": timing.unit, "ok": timing.ok,
             "error": timing.error, "quality": quality}
            for index, timing in enumerate(timings, start=1)]


def merge_measurements(previous: Sequence[Mapping[str, Any]], new: Sequence[Mapping[str, Any]],
                       fingerprint: str) -> list[dict[str, Any]]:
    """Earlier records of the same fingerprint, then the new ones; the newest MAX_MEASUREMENTS kept."""
    kept = [dict(record) for record in previous
            if isinstance(record, Mapping) and record.get("fingerprint_sha256") == fingerprint]
    return (kept + [dict(record) for record in new])[-MAX_MEASUREMENTS:]


# --- section 5.5: hysteresis for knobs chosen by timing ----------------------------------------

def hysteresis_choice(history: Sequence[Mapping[str, Any]], field: str, current: object,
                      candidates: Sequence[object]) -> object:
    """The value of ``field`` new jobs should use; ``current`` unless the timings clearly favour another.

    ``candidates`` are the row's ``free_fields[field]`` (empty: the value never changes). A record
    counts when its ``subject`` is ``"<field>=<value>"``, its unit ``x_realtime`` (clip seconds per
    wall second), and it is ``ok`` and ``clean`` (idle and plugged in); contended and battery records
    are ignored. Records of one run share the id prefix ``m-<stamp>``. Only the latest runs with the
    latest fingerprint count. The value changes when each of the last two such runs shows the same
    other value at least 10% faster than ``current`` and at least 60 s faster per reference film
    (``(t_current - t_new) / clip_seconds * REFERENCE_FILM_SECONDS``); changing back needs the same.
    """
    options = tuple(candidates)
    if current not in options or len(options) < 2:
        return current
    recent = _latest_runs(_field_runs(history, field, options))
    if len(recent) < HYSTERESIS_RUNS:
        return current
    old = options.index(current)
    winner = _fastest_better(recent[-1], old)
    if winner is None or not all(_better(speeds, winner, old) for speeds in recent):
        return current
    return options[winner]


def _field_runs(history: Sequence[Mapping[str, Any]], field: str,
                options: tuple[object, ...]) -> list[tuple[str, dict[int, float]]]:
    """(fingerprint, {option index: speed}) per run, oldest first; only records that count."""
    subjects = {f"{field}={option}": index for index, option in enumerate(options)}
    runs: dict[str, tuple[str, dict[int, float]]] = {}
    for record in history:
        index = subjects.get(record.get("subject")) if isinstance(record, Mapping) else None
        if index is None or not _counts(record):
            continue
        run = str(record["id"]).rsplit("-", 1)[0]
        run_print, speeds = runs.setdefault(run, (record["fingerprint_sha256"], {}))
        if record["fingerprint_sha256"] == run_print:
            speeds[index] = float(record["value"])
    return list(runs.values())


def _counts(record: Mapping[str, Any]) -> bool:
    value = record.get("value")
    return (record.get("ok") is True and record.get("quality") == "clean" and record.get("unit") == "x_realtime"
            and type(value) in (int, float) and math.isfinite(value) and value > 0
            and type(record.get("id")) is str and "-" in record["id"]
            and type(record.get("fingerprint_sha256")) is str)


def _latest_runs(runs: list[tuple[str, dict[int, float]]]) -> list[dict[int, float]]:
    """The last HYSTERESIS_RUNS runs of the latest fingerprint, counted back while it stays the same."""
    if not runs:
        return []
    latest = runs[-1][0]
    recent: list[dict[int, float]] = []
    for fingerprint, speeds in reversed(runs):
        if fingerprint != latest:
            break
        recent.append(speeds)
    return list(reversed(recent[:HYSTERESIS_RUNS]))


def _better(speeds: Mapping[int, float], new: int, old: int) -> bool:
    if new not in speeds or old not in speeds:
        return False
    gain = 1.0 - speeds[old] / speeds[new]
    seconds = (1.0 / speeds[old] - 1.0 / speeds[new]) * REFERENCE_FILM_SECONDS
    return gain >= HYSTERESIS_MIN_GAIN - _TOLERANCE and seconds >= HYSTERESIS_MIN_SECONDS - _TOLERANCE


def _fastest_better(speeds: Mapping[int, float], old: int) -> int | None:
    """The index of the fastest value clearly better than ``old`` in one run, if any."""
    better = [index for index in speeds if index != old and _better(speeds, index, old)]
    return max(better, key=lambda index: speeds[index]) if better else None


# --- entry point --------------------------------------------------------------------------------

def _utf8_stderr() -> None:
    """The Control Center sends this output to a log file; Vietnamese text is written as UTF-8."""
    reconfigure = getattr(sys.stderr, "reconfigure", None)
    if reconfigure is not None:
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    """``measure --project-root <dir> [--guard-root <dir>]... [--ffmpeg-dir <dir>] [--stamp <stamp>]
    [--wait-minutes N] [--launched-by control-center|script]``; returns the exit code."""
    _utf8_stderr()
    try:
        settings = check_arguments(parse_arguments(sys.argv[1:] if argv is None else argv))
    except UsageError as error:
        _stderr_line(f"Tham số sai: {error}")
        return EXIT_USAGE
    return run_measurement(settings)
