"""The Playwright driver of one session browser run, and the last way out of a run that still hangs (M3).

A session browser (download_account_browser) starts its own Playwright runtime, and with it its own driver: a
``node.exe`` child of BiliFlow that speaks to Edge. A run that hangs (a page stuck in a script, a hung
browser) is first ended by killing its Edge (``SessionBrowser.force_close``). The blocked call then fails,
unless the driver itself does not see Edge go: every sync call, ``context.close()`` and the runtime's stop
(which waits for the driver to exit) then wait for it for ever. So the run's own driver is ended too, one
grace later (``SessionBrowser.end_driver``), by these rules:

- Only the driver that run's own runtime started: the ``subprocess.Popen`` its Playwright connection holds
  (``driver_popen``, read from the runtime object, never a search by name or path), recorded at the start as
  ``DriverProcess``: pid, creation time and executable, while it is a child of this process.
- Before it is ended, the process must still be that one: the same pid behind the same handle, the same
  creation time and executable, still a child of this process, not exited. It is ended through that handle,
  never through a pid, so a pid that Windows gave to another process meanwhile is never touched (the handle
  also keeps the pid from being reused while it is open). Another run's driver, Claude, Codex, a dev server
  or a personal browser are never touched.
- Nothing here calls Playwright: the watchdog's thread only reads attributes and ends the process; the run's
  own thread sees its call fail and closes the rest as usual.

``blocked_at`` tells where a run waited (module, function and line of each frame; never a local, an argument,
a value or a message) for the run's error (``DriverHang``). ``driver_popen`` reads private attributes of
Playwright 1.63 and asyncio's subprocess transport; tests/test_download_account_driver.py checks them on the
installed version.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

import psutil

DRIVER_WAIT_SECONDS = 5.0  # how long ending a driver waits for it to be gone
MAX_STACK = 8  # frames of BiliFlow's own code in a hang report


@dataclass(frozen=True)
class DriverHang:
    """Where a run whose driver had to be ended was blocked, for M4's task log: ``phase`` (open, page,
    storage_state, close-context, close-runtime) and ``stack`` ("module:function:line", innermost first: the
    call that waited, then BiliFlow's own frames). No URL, value or text."""
    phase: str
    stack: tuple[str, ...] = ()


def driver_popen(runtime_manager: Any) -> Any | None:
    """The ``subprocess.Popen`` of the driver that ``runtime_manager`` (the object ``sync_playwright()``
    returned, while or after it starts) launched; None before it did or when the attributes are not there.
    Read only, from any thread."""
    connection = getattr(runtime_manager, "_connection", None)
    process = getattr(getattr(connection, "_transport", None), "_proc", None)  # asyncio.subprocess.Process
    popen = getattr(getattr(process, "_transport", None), "_proc", None)
    if (isinstance(getattr(popen, "pid", None), int) and callable(getattr(popen, "kill", None))
            and callable(getattr(popen, "poll", None))):
        return popen
    return None


@dataclass(frozen=True)
class DriverProcess:
    """One run's own driver, recorded when its runtime started it (see the module docstring)."""
    pid: int
    created: float
    exe: str
    popen: Any = field(repr=False, compare=False)

    @classmethod
    def record(cls, popen: Any) -> DriverProcess | None:
        """The process behind ``popen`` while it runs as a child of this process; None otherwise."""
        if popen is None or getattr(popen, "returncode", None) is not None:
            return None
        try:
            process = psutil.Process(popen.pid)
            if process.ppid() != os.getpid():
                return None
            return cls(process.pid, process.create_time(), process.exe(), popen)
        except (psutil.Error, OSError, ValueError):
            return None

    def owned(self) -> psutil.Process | None:
        """The process, only while it is still the one recorded: the same pid behind the same handle, not
        exited, the same creation time and executable, still a child of this process."""
        if getattr(self.popen, "pid", None) != self.pid or getattr(self.popen, "returncode", 0) is not None:
            return None
        try:
            process = psutil.Process(self.pid)
            if (process.create_time() != self.created or process.ppid() != os.getpid()
                    or os.path.normcase(process.exe()) != os.path.normcase(self.exe)):
                return None
        except (psutil.Error, OSError, ValueError):
            return None
        return process

    def end(self, wait: float = DRIVER_WAIT_SECONDS, before: Callable[[], None] | None = None) -> bool:
        """End the driver through its own handle (``owned`` first, and not exited by its handle's own account);
        True when it was ended. ``before`` runs once the driver is known to be this one and still running, just
        before it is ended: what it records is there before the run's blocked call can fail."""
        process = self.owned()
        if process is None:
            return False
        try:
            if self.popen.poll() is not None:  # exited already: there is nothing to end
                return False
            if before is not None:
                before()
            self.popen.kill()
        except OSError:
            return False
        try:
            process.wait(wait)
        except psutil.Error:  # gone already, or still going after ``wait``: nothing more to do from here
            pass
        return True


def blocked_at(owner: Any, limit: int = MAX_STACK, thread_id: int | None = None) -> tuple[str, ...]:
    """Where ``owner`` (the run thread's greenlet, suspended while the sync API waits for the driver) waits:
    the innermost frame, then up to ``limit`` frames of BiliFlow's own modules, as "module:function:line".
    When that greenlet is running instead (the runtime's stop waits in it for the driver to exit), the
    current frame of its thread ``thread_id``. () when neither can be read."""
    try:
        frame = getattr(owner, "gr_frame", None)
        if frame is None and thread_id is not None:
            frame = sys._current_frames().get(thread_id)
        found: list[str] = []
        innermost = True
        while frame is not None and len(found) <= limit:
            module = frame.f_globals.get("__name__", "?")
            if innermost or str(module).startswith("biliflow."):
                found.append(f"{module}:{frame.f_code.co_name}:{frame.f_lineno}")
            innermost = False
            frame = frame.f_back
        return tuple(found)
    except Exception:  # noqa: BLE001 - a report only: never a reason for the watchdog to fail
        return ()
