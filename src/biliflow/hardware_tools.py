"""Child processes of the hardware measurement (docs/HARDWARE_PROFILE.md section 6.3 steps 6-8).

Every command runs as an argv list with ``shell=False``, ``stdin=DEVNULL``, ``CREATE_NO_WINDOW`` and
its own timeout. The main thread waits in one-second slices (``communicate(timeout=1)``, which keeps
reading both pipes) and checks the cancel file and the run's time budget between them, so a
cancel stops the run within a second and no lock is ever released from another thread.

``GuardedSystem`` gives hardware_detect the same runner (the torch probe and the FFmpeg listings
then stop on a cancel too); every other method is the real WindowsSystem's.
Imported only by hardware_measure and hardware_trials, never by a scan stage. Standard library and
hardware_system; psutil is imported inside the kill function.
"""
from __future__ import annotations

import contextlib
import math
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from biliflow.hardware_system import NOT_FOUND, CommandResult, HardwareSystem, checked_argv, describe

POLL_SECONDS = 1.0
KILL_WAIT_SECONDS = 10.0
CANCEL_FILE = "cancel"
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Cancelled(Exception):
    """The cancel file appeared: the run releases its locks and exits 75."""


class BudgetExceeded(Exception):
    """The run's time budget is spent: it releases its locks, writes nothing and exits 124."""


class RunGuard:
    """The cancel file of the run folder and the time budget, checked between and during steps."""

    def __init__(self, run_dir: Path, *, clock: Callable[[], float] = time.monotonic):
        self.cancel_path = Path(run_dir) / CANCEL_FILE
        self.clock = clock
        self.deadline: float | None = None

    def start_budget(self, seconds: float) -> None:
        self.deadline = self.clock() + seconds

    def cancelled(self) -> bool:
        return self.cancel_path.exists()

    def expired(self) -> bool:
        return self.deadline is not None and self.clock() >= self.deadline

    def remaining(self) -> float:
        return math.inf if self.deadline is None else max(self.deadline - self.clock(), 0.0)

    def check(self) -> None:
        if self.cancelled():
            raise Cancelled
        if self.expired():
            raise BudgetExceeded


@dataclass(frozen=True)
class ToolResult:
    """``returncode`` is None when the command timed out or could not start (``error``)."""
    returncode: int | None
    stdout: bytes = b""
    stderr: bytes = b""
    seconds: float = 0.0
    timed_out: bool = False
    error: str | None = None  # NOT_FOUND, or why the program could not start

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def kill_process_tree(process: Any) -> None:
    """Kill the process and its descendants (the venv python.exe is a launcher with a child)."""
    import psutil
    try:
        children = psutil.Process(process.pid).children(recursive=True)
    except psutil.Error:  # the process already ended
        children = []
    for child in children:
        with contextlib.suppress(psutil.Error):  # a child that already ended
            child.kill()
    with contextlib.suppress(OSError):
        process.kill()


class ToolRunner:
    """Runs one command at a time on the calling thread; raises Cancelled or BudgetExceeded."""

    def __init__(self, guard: RunGuard, *, popen: Callable[..., Any] = subprocess.Popen,
                 kill: Callable[[Any], None] = kill_process_tree, poll_seconds: float = POLL_SECONDS):
        self.guard = guard
        self._popen = popen
        self._kill = kill
        self._poll = poll_seconds

    def run(self, argv: Sequence[str], *, timeout: float, env: Mapping[str, str] | None = None) -> ToolResult:
        command = checked_argv(argv, timeout)
        self.guard.check()
        clock = self.guard.clock
        started = clock()
        step_deadline = started + timeout
        try:
            process = self._popen(command, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, creationflags=_CREATE_NO_WINDOW,
                                  env=None if env is None else dict(env))
        except FileNotFoundError:
            return ToolResult(None, error=NOT_FOUND)
        except OSError as error:
            return ToolResult(None, error=describe(error))
        try:
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=self._poll)
                    return ToolResult(process.returncode, stdout or b"", stderr or b"",
                                      round(max(clock() - started, 0.0), 3))
                except subprocess.TimeoutExpired:
                    stop = self._stop_reason(step_deadline)
                    if stop is not None:
                        break
        except BaseException:
            # Ctrl+C or any other error: the child never outlives the locks released above this frame.
            with contextlib.suppress(Exception):
                self._end(process)
            raise
        self._end(process)
        if stop == "cancel":
            raise Cancelled
        if stop == "budget":
            raise BudgetExceeded
        return ToolResult(None, seconds=round(max(clock() - started, 0.0), 3), timed_out=True)

    def _stop_reason(self, step_deadline: float) -> str | None:
        if self.guard.cancelled():
            return "cancel"
        if self.guard.expired():
            return "budget"
        if self.guard.clock() >= step_deadline:
            return "timeout"
        return None

    def _end(self, process: Any) -> None:
        self._kill(process)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.communicate(timeout=KILL_WAIT_SECONDS)


def decoded(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


class GuardedSystem:
    """A HardwareSystem whose commands run through a ToolRunner; everything else is ``inner``'s."""

    def __init__(self, inner: HardwareSystem, runner: ToolRunner):
        self._inner = inner
        self._runner = runner

    def run(self, argv: Sequence[str], *, timeout: float,
            env: Mapping[str, str] | None = None) -> CommandResult:
        result = self._runner.run(argv, timeout=timeout, env=env)
        if result.timed_out:
            return CommandResult(None, timed_out=True)
        if result.error is not None:
            return CommandResult(None, error=result.error)
        return CommandResult(result.returncode, decoded(result.stdout), decoded(result.stderr))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
