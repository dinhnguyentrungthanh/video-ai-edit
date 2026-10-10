"""Hidden runs of a source's session browser: bound to the manager (M2a), one at a time per source (M3).

``run_with_session(manager, source_id, action)`` is the only way the downloader uses a saved session:

- One run at a time per project root, Windows account and source (``source_run_lock``), so two runs never
  lease the same session and overwrite each other's rotated cookies (plan 9.9, L3). It is a lock of its own:
  no worker, store or manager lock is held while the browser runs. Waiting for it ends at once on a cancel
  of the caller (``Cancelled``) and after ``lock_seconds`` (``SourceBusy``, retryable).
- The session comes from ``manager.session_for`` (LoginRequired or SessionUnavailable as they are), and the
  browser is headless: a hidden run has no way to ask for a window, and never opens the sign-in.
- One deadline for the whole run (``run_seconds``): it is the browser's ``stop_at`` (launch, navigations and
  waits end by then, the route handler refuses later requests) and, when it passes, the run's own control is
  stopped like a cancel, which cuts the request in flight. A cancel of the caller does the same. If a
  Playwright call still hangs (a page stuck in a script), the run's own Edge is ended ``grace_seconds``
  later (``SessionBrowser.force_close``: only the processes of this run's profile, never another browser),
  and if the run has still not closed ``grace_seconds`` after that (the driver did not see Edge go: a call,
  the context's close or the runtime's stop waits for it), the run's own Playwright driver is ended
  (``SessionBrowser.end_driver``: only the driver this run's runtime started, checked before it is ended).
  Those steps stay armed until the browser has closed, its cleanup included. A hidden run so ends at most
  about ``run_seconds`` + 2 × ``grace_seconds`` after its browser started, plus the kill of its Edge (under
  a second as a rule, at most download_runner.KILL_WAIT_SECONDS for each Edge process that does not end),
  download_account_driver.DRIVER_WAIT_SECONDS for its driver and the rest of the cleanup (a few short tries
  to delete the profile, else ``profile_left``); the wait for the source's lock comes before that.
  The caller gets ``RunTimedOut`` (retryable, like a slow network) or ``Cancelled``. This is checked again
  after the browser closed, also when the action returned: a run stopped or past its deadline while its
  state was read or its browser closed never gives its value (a list or a ticket) to the caller. When the
  driver had to be ended, the error's ``hang`` (``DriverHang``) says in which phase and where the run waited.
- Rotated cookies go back only through ``manager.save_rotated`` with the same lease, inside the lock: saved
  only by the manager (project root, Windows account), source and generation that gave the session out,
  never after a disconnect or a new sign-in, and never extending ``authenticated_at`` or the TTL. A state
  without any cookie left (a sign-out) is not saved. When the action raises (a source error, a cancel, the
  deadline), the browser's state is still read once if the browser answers, and saved the same way before
  the error goes on: a source that rotates its cookie on every use keeps working after an interrupted run.
  A Playwright error of the action becomes BrowserFailed (its text would repeat URLs).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, Iterable, Mapping, TypeVar

from biliflow.download_account_browser import SessionBrowser
from biliflow.download_account_driver import DriverHang
from biliflow.download_account_edge import BrowserFailed, _playwright_error, failure_kind
from biliflow.download_account_http import SessionHttp, SessionNetwork
from biliflow.download_account_page_script import PageScript
from biliflow.download_accounts import AccountManager, SessionLease
from biliflow.download_http import Cancelled, HttpError, Interruptible
from biliflow.download_runner import ProcessControl

T = TypeVar("T")

HIDDEN_RUN_SECONDS = 180.0  # one discovery or one ticket, launch included
MAX_HIDDEN_RUN_SECONDS = 600.0
GRACE_SECONDS = 10.0  # after the deadline or a cancel, before a hung run's own Edge is ended
MAX_GRACE_SECONDS = 60.0
LOCK_WAIT_SECONDS = 15 * 60.0  # the most a run waits while other runs of the same source go first
LOCK_POLL_SECONDS = 0.2  # how soon a cancel ends that wait

_LOCKS_GUARD = threading.Lock()
_RUN_LOCKS: dict[tuple[str, str, str], threading.Lock] = {}


class RunTimedOut(HttpError):
    """The hidden run did not finish before its deadline: like a slow network, worth another try later.
    ``hang``: set when its driver had to be ended (see the module docstring)."""

    hang: DriverHang | None = None

    def __init__(self, seconds: float):
        super().__init__("NETWORK", f"Trang nguồn chưa xong trong {seconds:g} giây; thử lại sau.", retryable=True)


class SourceBusy(HttpError):
    """Other runs of the same source kept the browser longer than a run may wait."""

    def __init__(self) -> None:
        super().__init__("SERVER_BUSY", "Trình duyệt của nguồn này đang bận với lượt khác; thử lại sau.",
                         retryable=True)


@dataclass(frozen=True)
class SessionRun(Generic[T]):
    """What ``run_with_session`` returns: the action's value and what happened to the cookies.
    ``saved``: None when they did not change; True when the rotated ones were saved under the lease;
    False when the manager refused them (stale lease, another manager), could not save them, or every
    cookie of the session was gone (a sign-out is never saved as a session).
    ``profile_left``: the temporary profile could not be deleted (``clear_browser_profiles`` deletes it).
    ``lease``: the session the run used (its generation decides what the run's evidence may change).
    ``state_unread``: why the browser's state could not be read after the action (fixed words, e.g.
    "SESSION_READ_FAILED" with its kind; empty when it was read): the value still stands, nothing was saved.
    ``stats``: how the run went (``SessionBrowser.run_stats`` and ``seconds``; numbers and fixed words only)."""
    value: T
    saved: bool | None
    profile_left: bool = False
    lease: SessionLease | None = field(default=None, repr=False)
    state_unread: str = ""
    stats: Mapping[str, float | int | str] = field(default_factory=dict)


def _comparable(state: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """A state in one canonical form, so that only a real change counts: every key of every cookie as it is
    (value, expiry unrounded, httpOnly, secure, sameSite, partitionKey and whatever else Playwright gives),
    keys sorted, cookies and origins sorted, and an origin's storage items sorted."""
    cookies = sorted(json.dumps(cookie, sort_keys=True) for cookie in state.get("cookies", []))
    origins = sorted(json.dumps({**origin, "localStorage": sorted(
        (json.dumps(item, sort_keys=True) for item in origin.get("localStorage", [])))}, sort_keys=True)
                     for origin in state.get("origins", []))
    return tuple(cookies), tuple(origins)


def source_run_lock(manager: AccountManager, source_id: str) -> threading.Lock:
    """The lock of one source's hidden runs, shared by every manager of the same root and Windows account."""
    with _LOCKS_GUARD:
        return _RUN_LOCKS.setdefault((*manager.owner, source_id), threading.Lock())


def _acquire(lock: threading.Lock, control: Interruptible, wait_seconds: float) -> None:
    end = time.monotonic() + wait_seconds
    while not lock.acquire(timeout=LOCK_POLL_SECONDS):
        if control.requested:
            raise Cancelled()
        if time.monotonic() >= end:
            raise SourceBusy()
    if control.requested:
        lock.release()
        raise Cancelled()


class _RunStop:
    """The run's own control: stopped by the caller's cancel or by the deadline (never the other way round,
    so a timeout of the browser run is not taken for the user's stop). The first of them also arms the end of
    a run that hangs: ``grace`` seconds later its own Edge is killed, and ``grace`` seconds after that its own
    driver is ended if the browser has still not closed (see the module docstring). ``close``, once the
    browser has closed, cancels what has not fired."""

    def __init__(self, caller: Interruptible, run_seconds: float, grace: float):
        self.control = ProcessControl()
        self.browser: SessionBrowser | None = None
        self._grace = grace
        self._timers: list[threading.Timer] = []
        self._lock = threading.Lock()
        self._done = False
        self._armed = False
        self._remove = caller.add_closer(lambda: self._stop("cancel"))
        self._later(run_seconds, self._stop, "timeout")

    def _later(self, seconds: float, action: Callable[..., Any], *args: Any) -> None:
        with self._lock:
            if self._done:
                return
            timer = threading.Timer(seconds, action, args)
            timer.daemon = True
            self._timers.append(timer)
        timer.start()

    def _stop(self, reason: str) -> None:
        self.control.request(reason)
        with self._lock:
            armed, self._armed = self._armed, True
        if not armed:
            self._later(self._grace, self._kill)

    def _kill(self) -> None:
        browser = self.browser
        if browser is None:
            return
        try:
            browser.force_close()
        except Exception:  # noqa: BLE001 - the driver's turn still comes
            pass
        self._later(self._grace, self._end_driver)

    def _end_driver(self) -> None:
        browser = self.browser
        if browser is not None:
            try:
                browser.end_driver()
            except Exception:  # noqa: BLE001 - nothing more can be done from this thread
                pass

    def close(self) -> None:
        with self._lock:
            self._done = True
            timers = list(self._timers)
        for timer in timers:
            timer.cancel()
        self._remove()


def _checked_limits(run_seconds: float, grace_seconds: float) -> None:
    if not 0 < run_seconds <= MAX_HIDDEN_RUN_SECONDS:
        raise ValueError(f"run_seconds must be above 0 and at most {MAX_HIDDEN_RUN_SECONDS:g}")
    if not 0 < grace_seconds <= MAX_GRACE_SECONDS:
        raise ValueError(f"grace_seconds must be above 0 and at most {MAX_GRACE_SECONDS:g}")


def run_with_session(manager: AccountManager, source_id: str, action: Callable[[SessionBrowser], T], *,
                     control: Interruptible, network: SessionNetwork | None = None,
                     http_options: Mapping[str, Any] | None = None,
                     browser_options: Mapping[str, Any] | None = None, hosts: Iterable[str] | None = None,
                     run_seconds: float = HIDDEN_RUN_SECONDS, grace_seconds: float = GRACE_SECONDS,
                     lock_seconds: float = LOCK_WAIT_SECONDS, page_script: bool = False) -> SessionRun[T]:
    """Run ``action`` in a hidden browser with the source's saved session (see the module docstring).
    ``hosts``: the source's hosts the browser may reach (all of them by default; the portal is required).
    ``page_script``: let the source's configured page script load in this run (M7 exception A; a ticket run
    only, download_account_page_script); nothing when the source configures none.
    ``network`` and ``http_options`` are the tests' injection only; production code never passes them."""
    options = dict(browser_options or {})
    if "headed" in options:
        raise ValueError("A hidden run is always headless; only the user's sign-in opens a window")
    if "stop_at" in options:
        raise ValueError("A hidden run's deadline is run_seconds")
    if "page_script" in options:
        raise ValueError("A run's page script comes from the source's config only")
    _checked_limits(run_seconds, grace_seconds)
    lock = source_run_lock(manager, source_id)
    _acquire(lock, control, lock_seconds)
    try:
        lease = manager.session_for(source_id)
        source = manager.config.sources[source_id]
        http = SessionHttp(frozenset(hosts) if hosts is not None else source.all_hosts, network,
                           **(http_options or {}))
        if page_script and source.page_script:
            options["page_script"] = PageScript(source.page_script, network)
        stop = _RunStop(control, run_seconds, grace_seconds)
        started = time.monotonic()
        deadline = started + run_seconds
        kept: dict[str, Any] = {}
        try:
            value, final, profile_left, unread, stats = _run_browser(source, http, manager, lease, stop, action,
                                                                     deadline, options, kept)
        except Exception:
            if "state" in kept:
                _save(manager, lease, kept["state"])
            stopped = _stopped(control, stop, deadline, run_seconds)
            if stopped is not None:
                raise _with_hang(stopped, stop.browser) from None
            raise
        finally:
            stop.close()
        saved = None if final is None else _save(manager, lease, final)
        stopped = _stopped(control, stop, deadline, run_seconds)
        if stopped is not None:  # stopped while the state was read or the browser closed: its value is not used
            raise _with_hang(stopped, stop.browser)
        stats = {**stats, "seconds": round(time.monotonic() - started, 1)}
        return SessionRun(value, saved, profile_left, lease, unread, stats)
    finally:
        lock.release()


def _stopped(control: Interruptible, stop: _RunStop, deadline: float, run_seconds: float) -> Exception | None:
    """Why a run may not give its result, checked after the browser closed, whether the action returned or
    raised: the caller's cancel (Cancelled), else the run's own stop or its deadline, even when the timer has
    not fired yet (RunTimedOut). None: the run ended in time."""
    if control.requested:
        return Cancelled()
    if stop.control.requested or time.monotonic() >= deadline:
        return RunTimedOut(run_seconds)
    return None


def _with_hang(error: Exception, browser: SessionBrowser | None) -> Exception:
    """``error`` with the browser's ``hang`` as its own ``hang`` when the run's driver had to be ended."""
    hang = getattr(browser, "hang", None)
    if hang is not None:
        error.hang = hang  # type: ignore[attr-defined]
    return error


def _run_browser(source: Any, http: SessionHttp, manager: AccountManager, lease: SessionLease, stop: _RunStop,
                 action: Callable[[SessionBrowser], T], deadline: float, options: Mapping[str, Any],
                 kept: dict[str, Any]) -> tuple[T, dict[str, Any] | None, bool, str, dict[str, float | int | str]]:
    """The action's value, the state read after it (None when it could not be read: why, in the fourth item),
    whether the profile was left and the run's numbers. ``kept["state"]``: the browser's state read after the
    action raised, when the browser still answered."""
    browser = SessionBrowser(source, http, manager.vault, stop.control, state=lease.state, stop_at=deadline,
                             **options)
    stop.browser = browser
    failed = ""
    final: dict[str, Any] | None = None
    unread = ""
    with browser:
        try:
            value = action(browser)
            try:
                final = browser.storage_state()
            except BrowserFailed as error:  # the action's value stands (a ticket is not thrown away); the
                unread = ", ".join(part for part in (error.code, error.detail) if part)  # saved session stays
        except _playwright_error() as error:
            # Where and what kind, in fixed words; raised below, outside the handler: the error (and its URLs)
            # is not kept as context.
            failed = f"{getattr(browser, 'step', '?')}, {failure_kind(error)}"
        except BaseException:
            _keep_state(browser, kept)
            raise
    if failed:
        raise BrowserFailed("BROWSER_FAILED", detail=failed)
    stats = browser.run_stats() if callable(getattr(browser, "run_stats", None)) else {}
    return value, final, browser.profile_left, unread, stats


def _keep_state(browser: SessionBrowser, kept: dict[str, Any]) -> None:
    """Read the state of a run whose action raised, if the browser still answers (a killed or hung browser
    gives nothing; its error is not kept)."""
    try:
        kept["state"] = browser.storage_state()
    except Exception:  # noqa: BLE001 - best effort only: BrowserFailed, a closed context, a malformed state
        pass


def _save(manager: AccountManager, lease: SessionLease, final: Mapping[str, Any]) -> bool | None:
    if _comparable(final) == _comparable(lease.state):
        return None
    if lease.state.get("cookies") and not final.get("cookies"):
        return False  # signed out, not rotated
    try:
        return manager.save_rotated(lease, final)
    except sqlite3.Error:  # the database is busy or broken: the action's result is still the caller's
        return False
