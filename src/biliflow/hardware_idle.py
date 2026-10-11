"""Is the queue idle? The idle check of the hardware measurement (docs/HARDWARE_PROFILE.md 6.3 step 3).

No HTTP, and never ``state\\control-center.lock`` (opening it writes). For each guard root:

- the Control Center runs when ``state\\control-center.json`` names a live PID. That file also holds
  the session token, so the reader returns the PID only: its content, its parse errors and the
  token never reach a return value, an exception or a log line;
- when it runs, ``state\\control-center.sqlite3`` is read through a ``mode=ro`` URI (never JobStore,
  whose constructor migrates and writes): the queue must be paused, no job in IN_PROCESS_STATES and
  no stage RUNNING. ``state\\downloads.sqlite3`` only gives the number of tasks holding a download
  slot (COUNT only: the table also holds links and titles); active downloads never refuse, they
  only mark the measurements ``contended``;
- when it does not run, no database is opened at all: both use WAL, and even a read-only connection
  may create ``-wal``/``-shm`` files in a real state folder.

Imported only by hardware_measure, never by a scan stage. Standard library only; job_store,
download_store and psutil are imported inside functions.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import quote

CONTROL_CENTER_JSON = ("state", "control-center.json")
CONTROL_CENTER_DATABASE = ("state", "control-center.sqlite3")
DOWNLOADS_DATABASE = ("state", "downloads.sqlite3")
MAX_STATE_JSON_BYTES = 64 * 1024
SQLITE_TIMEOUT_SECONDS = 5.0

NOT_PAUSED = "not_paused"
JOB_RUNNING = "job_running"
STAGE_RUNNING = "stage_running"
STATE_UNREADABLE = "state_unreadable"
REASON_TEXTS = {
    NOT_PAUSED: "Hàng đợi chưa tạm dừng.",
    JOB_RUNNING: "Có job đang chạy.",
    STAGE_RUNNING: "Có stage đang chạy.",
    STATE_UNREADABLE: "Không đọc được trạng thái Control Center.",
}
_PID_UNREADABLE = "Không đọc được số PID trong state/control-center.json."


class StateUnreadable(Exception):
    """A fixed message only: never the file's content, its parse error or the token."""


@dataclass(frozen=True)
class RootIdle:
    """One guard root. ``downloads`` is None when downloads.sqlite3 could not be read (contended)."""
    root: Path
    control_center: bool | None  # None: control-center.json could not be read
    reason: str | None  # None: idle
    downloads: int | None

    @property
    def idle(self) -> bool:
        return self.reason is None

    @property
    def contended(self) -> bool:
        return self.downloads is None or self.downloads > 0


@dataclass(frozen=True)
class IdleReport:
    roots: tuple[RootIdle, ...]

    @property
    def idle(self) -> bool:
        return all(root.idle for root in self.roots)

    @property
    def contended(self) -> bool:
        return any(root.contended for root in self.roots)

    @property
    def busy_root(self) -> RootIdle | None:
        return next((root for root in self.roots if not root.idle), None)


# --- the PID of a running Control Center ----------------------------------------------------------

def control_center_pid(root: Path) -> int | None:
    """The ``pid`` of ``state\\control-center.json``; None when the file is missing.

    Raises StateUnreadable (fixed text, no context) when the file cannot be read, is too large, is
    not JSON or holds no positive integer PID (a bool is refused).
    """
    path = Path(root).joinpath(*CONTROL_CENTER_JSON)
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_STATE_JSON_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError:
        data = None
    pid = _pid_from(data)
    if pid is None:  # raised outside every except block, so no parse error rides along as context
        raise StateUnreadable(_PID_UNREADABLE)
    return pid


def _pid_from(data: bytes | None) -> int | None:
    if data is None or len(data) > MAX_STATE_JSON_BYTES:
        return None
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        value = None
    pid = value.get("pid") if type(value) is dict else None
    return pid if type(pid) is int and pid > 0 else None


def pid_alive(pid: int) -> bool:
    import psutil
    return bool(psutil.pid_exists(pid))


# --- read-only databases ------------------------------------------------------------------------

Connect = Callable[..., Any]


def _read_only(connect: Connect, database: Path) -> Any:
    return connect(f"file:{quote(database.as_posix())}?mode=ro", uri=True, timeout=SQLITE_TIMEOUT_SECONDS)


def queue_reason(root: Path, *, connect: Connect = sqlite3.connect) -> str | None:
    """None when the queue of a running Control Center is idle, else the reason code."""
    from biliflow.job_store import IN_PROCESS_STATES
    database = Path(root).joinpath(*CONTROL_CENTER_DATABASE)
    if not database.is_file():
        return STATE_UNREADABLE
    try:
        connection = _read_only(connect, database)
        try:
            row = connection.execute("SELECT value_json FROM settings WHERE key='scheduler_paused'").fetchone()
            states = sorted(IN_PROCESS_STATES)
            marks = ",".join("?" * len(states))
            jobs = connection.execute(
                f"SELECT COUNT(*) FROM jobs WHERE state IN ({marks})", states).fetchone()[0]  # noqa: S608
            stages = connection.execute("SELECT COUNT(*) FROM stages WHERE state='RUNNING'").fetchone()[0]
        finally:
            connection.close()
    except sqlite3.Error:
        return STATE_UNREADABLE
    try:
        paused = row is not None and json.loads(row[0]) is True
    except (TypeError, ValueError):
        return STATE_UNREADABLE
    if not paused:
        return NOT_PAUSED
    if jobs:
        return JOB_RUNNING
    if stages:
        return STAGE_RUNNING
    return None


def active_downloads(root: Path, *, connect: Connect = sqlite3.connect) -> int | None:
    """Tasks holding a download slot; 0 without the database, None when it cannot be read."""
    from biliflow.download_store import SLOT_STATES
    database = Path(root).joinpath(*DOWNLOADS_DATABASE)
    if not database.is_file():
        return 0
    states = sorted(SLOT_STATES)
    marks = ",".join("?" * len(states))
    try:
        connection = _read_only(connect, database)
        try:
            count = connection.execute(
                f"SELECT COUNT(*) FROM download_tasks WHERE state IN ({marks})", states).fetchone()[0]  # noqa: S608
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    return count if type(count) is int and count >= 0 else None


# --- the check ----------------------------------------------------------------------------------

def check_root(root: Path, *, connect: Connect = sqlite3.connect,
               alive: Callable[[int], bool] = pid_alive) -> RootIdle:
    """One guard root; opens a database only while its Control Center runs."""
    try:
        pid = control_center_pid(root)
    except StateUnreadable:
        return RootIdle(Path(root), None, STATE_UNREADABLE, None)
    if pid is None or not alive(pid):
        return RootIdle(Path(root), False, None, 0)
    reason = queue_reason(root, connect=connect)
    return RootIdle(Path(root), True, reason, active_downloads(root, connect=connect))


def check_idle(roots: Sequence[Path], *, connect: Connect = sqlite3.connect,
               alive: Callable[[int], bool] = pid_alive) -> IdleReport:
    return IdleReport(tuple(check_root(root, connect=connect, alive=alive) for root in roots))
