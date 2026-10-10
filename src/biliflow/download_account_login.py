"""Sign-in of a source account (docs/SOURCE_ACCOUNTS_PLAN.md, M2b): the one visible browser window.

``LoginCoordinator.start(source_id)`` is the only way to a headed window. M4's PC-only route calls it when the
user presses Đăng nhập; nothing else does: status, start-up, a missing session and hidden runs never open a
window. It begins the manager's sign-in (LOGGING_IN; LOGIN_BUSY when one is open) and runs the window in a
thread of its own, never under a manager or store lock:

- the window is a ``SessionBrowser`` with a ``HeadedPermit``: BiliFlow's own fresh profile in the private
  folder, the M2a network layer (every request through the route handler), Edge's sandbox, the pinned
  profile, password manager and autofill off (PROFILE_PREFERENCES), deleted at the end. No personal profile,
  cookie or password is ever read, and BiliFlow has no password form;
- it opens the source's login URL; the user signs in on the source's own pages;
- every ``poll_seconds`` the adapter's verifier looks for evidence from the source through a ``LoginView``:
  the shown page's URL and text, and answers the page itself fetches from the source's own hosts (so through
  the route handler, with the browser's own cookies). A cookie, a new URL, a 200 or a closed window alone is
  never evidence;
- on evidence the context's state (cookies, localStorage, IndexedDB) is collected into memory, every stop
  condition is checked again, and only then is it committed by ``manager.complete_login``: encrypted under
  the next generation, CONNECTED, ``authenticated_at`` now (the 3,600-second fallback TTL counts from it).
  That commit is the commit point (``_complete``);
- otherwise the sign-in ends (``end_login``) as cancelled, window closed, timed out or failed, and a session
  saved before stays as it was. The whole run has one deadline (``max_seconds``, at most
  LOGIN_ATTEMPT_MAX_SECONDS, counted from just before the attempt began): it is the browser's ``stop_at`` (the
  launch, the login page and every wait end by then), and when it comes the run is stopped as a cancel would
  be (a request in flight is cut). A Playwright call that hangs anyway (a page stuck in a script, a hung
  browser) ends when the window's own Edge is killed, ``grace_seconds`` after the deadline or a cancel, and
  if the window has still not closed ``grace_seconds`` after that (its driver did not see Edge go), its own
  Playwright driver is ended (``SessionBrowser.end_driver``); the outcome's ``hang`` then says where it waited.
  It stops on a cancel, a shutdown, the deadline, a disconnect or a newer sign-in. A cancel, a shutdown and
  the deadline end the run's own attempt in the manager (``_stop_run``), never a newer one, under the lock
  the commit holds: one that comes before the commit point makes it fail (nothing saved), one that comes
  after it leaves the new session in place.

Verifiers are code, by adapter id (LOGIN_VERIFIERS, read-only); the config never names code. "release-forms"
has one, written in M7 from a real source's evidence checked with the user (download_account_release_forms);
``ticket-files`` has none, so its sign-in is refused (LOGIN_UNSUPPORTED) before any window opens. Tests pass
fixture verifiers. Nothing here logs; outcomes carry codes and fixed texts only.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit

from biliflow.download_account_browser import (
    BrowserFailed,
    BrowserUnavailable,
    HeadedPermit,
    SessionBrowser,
    _playwright_error,
)
from biliflow.download_account_config import SourceAccount
from biliflow.download_account_driver import DriverHang
from biliflow.download_account_http import SessionHttp, SessionNetwork
from biliflow.download_account_pages import is_sign_in_page
from biliflow.download_account_release_forms import RELEASE_FORMS_VERIFIER
from biliflow.download_account_vault import SessionVault, VaultError
from biliflow.download_accounts import (
    LOGIN_ATTEMPT_MAX_SECONDS,
    LOGIN_END_CODES,
    MESSAGES,
    AccountBusy,
    AccountError,
    AccountManager,
    AccountUnknown,
    LoginAttempt,
    SessionSaveFailed,
    StaleLogin,
)
from biliflow.download_http import Cancelled, HttpError, Interruptible
from biliflow.download_runner import ProcessControl

POLL_SECONDS = 1.0
MAX_POLL_SECONDS = 10.0
GRACE_SECONDS = 10.0  # after the deadline or a cancel, how long a window may take to close before it is killed
MAX_GRACE_SECONDS = 60.0
FETCH_LIMIT = 64 * 1024  # characters of a fetched answer a verifier sees
TEXT_LIMIT = 4096
# Ways a sign-in ends that the manager keeps as they are; any other code ends it as LOGIN_FAILED.
_END_CODES = LOGIN_END_CODES - {"LOGIN_FAILED"}
OUTCOME_MESSAGES = {
    "CONNECTED": "Đã đăng nhập; BiliFlow đã lưu phiên của nguồn này.",
    "LOGIN_PAGE_FAILED": "Không mở được trang đăng nhập của nguồn (mạng, chứng chỉ hoặc trang bị chặn).",
    "BROWSER_UNAVAILABLE": "Không mở được cửa sổ Edge của BiliFlow trên máy này.",
    "BROWSER_FAILED": "Cửa sổ đăng nhập gặp lỗi và đã đóng; chưa lưu phiên.",
    "PROFILE_FAILED": "Không tạo được thư mục tạm an toàn cho cửa sổ đăng nhập.",
    "VERIFIER_ERROR": "BiliFlow không kiểm tra được kết quả đăng nhập; chưa lưu phiên.",
}
_FETCH_SCRIPT = """async ([url, limit]) => {
  try {
    const answer = await fetch(url, {credentials: 'include', cache: 'no-store', redirect: 'manual'});
    const body = await answer.text();
    return {status: answer.status, body: body.slice(0, limit)};
  } catch (error) { return null; }
}"""
_TEXT_SCRIPT = """([selector, limit]) => {
  const node = document.querySelector(selector);
  return node ? (node.textContent || '').slice(0, limit) : null;
}"""


class LoginUnsupported(AccountError):
    """No verifier in the code for the source's adapter: its sign-in cannot be confirmed, so none starts."""


@dataclass(frozen=True)
class Fetched:
    """An answer the page fetched from the source: its status and the start of its body."""
    status: int
    body: str = field(repr=False)


class LoginView:
    """What a verifier may read, never Playwright objects (so never ``context.request``, a route or a new
    page): the shown page's URL and text, and answers that page fetches from the source's own hosts. Those
    go through the route handler and carry the browser's cookies for their URL. The fetch runs in the page,
    so a script of the source's own pages could answer for it; in the sign-in window scripts of other hosts
    never load (M2a), not even the source's ``page_script``: M7 exception A is for hidden ticket runs only
    (download_account_page_script), and the sign-in browser never gets one."""

    def __init__(self, browser: SessionBrowser):
        self._browser = browser

    def _page(self) -> Any:
        try:
            pages = self._browser.context.pages
        except (AttributeError, _playwright_error()):
            return None
        return pages[0] if pages else None  # never a new page: in the window that would open a tab

    @property
    def url(self) -> str:
        page = self._page()
        try:
            return page.url if page is not None else ""
        except _playwright_error():
            return ""

    def left_sign_in(self) -> bool:
        """The shown page is an https page of one of the source's portal hosts and not its sign-in page (the
        source's own ``login_url``): the user got past the sign-in form. Never evidence by itself."""
        source = self._browser.source
        url = self.url
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            return False
        return (parts.scheme == "https" and port in (None, 443) and (parts.hostname or "") in source.hosts["portal"]
                and not is_sign_in_page(source, url))

    def text(self, selector: str) -> str | None:
        """The text of the first element ``selector`` matches in the shown page (at most TEXT_LIMIT), or None."""
        page = self._page()
        if page is None:
            return None
        try:
            value = page.evaluate(_TEXT_SCRIPT, [selector, TEXT_LIMIT])
        except _playwright_error():  # navigating, closed, or a bad selector
            return None
        return value if isinstance(value, str) else None

    def fetch(self, url: str) -> Fetched | None:
        """GET ``url`` (a source host only) from the shown page, with its cookies; None when refused or failed."""
        try:
            self._browser.http.in_scope(url)
        except HttpError:
            return None
        page = self._page()
        if page is None:
            return None
        try:
            answer = page.evaluate(_FETCH_SCRIPT, [url, FETCH_LIMIT])
        except _playwright_error():
            return None
        if not isinstance(answer, dict) or not isinstance(answer.get("status"), int) \
                or not isinstance(answer.get("body"), str):
            return None
        return Fetched(answer["status"], answer["body"][:FETCH_LIMIT])


class LoginVerifier(Protocol):
    def signed_in(self, view: LoginView) -> bool:
        """True only on the source's own evidence that the user is signed in."""


# By adapter id, read-only (a verifier is code, never added at run time): "release-forms" from the evidence of a
# real source checked with the user in M7 (download_account_release_forms). ``ticket-files`` has none, so its
# sign-in is refused (LOGIN_UNSUPPORTED) before any window.
LOGIN_VERIFIERS: Mapping[str, LoginVerifier] = MappingProxyType({"release-forms": RELEASE_FORMS_VERIFIER})


@dataclass(frozen=True)
class LoginOutcome:
    """How a sign-in ended: "CONNECTED" or a code. ``status``: the source's public status after a success.
    ``profile_left``: the window's temporary profile could not be deleted (``clear_browser_profiles`` deletes
    it at the next start). ``failure``: the class name of an unexpected error, for M4's log (never its text,
    which may repeat a URL). ``hang``: set when the window's driver had to be ended (its phase and where it
    waited, as module:function:line)."""
    source_id: str
    code: str
    status: Mapping[str, Any] | None = field(default=None, repr=False)
    profile_left: bool = False
    failure: str | None = None
    hang: DriverHang | None = None

    @property
    def connected(self) -> bool:
        return self.code == "CONNECTED"

    @property
    def message(self) -> str:
        return OUTCOME_MESSAGES.get(self.code) or MESSAGES.get(self.code, self.code)


class LoginRun:
    """One running sign-in, bound to its attempt; ``wait`` returns its outcome. Cancelled through
    ``LoginCoordinator.cancel``. ``deadline`` (``time.monotonic``) is taken just before the attempt began, so
    it never outlasts the manager's own limit; ``browser`` is the window, for ``force_close`` only. ``control``
    is a downloader ``ProcessControl``: its reason is "cancel" (the user, or shutdown) or "timeout" (the
    deadline, a reason only this module uses)."""

    def __init__(self, attempt: LoginAttempt, deadline: float):
        self.source_id = attempt.source_id
        self.attempt = attempt
        self.deadline = deadline
        self.control = ProcessControl()
        self.browser: Any = None
        self._finished = threading.Event()
        self.outcome: LoginOutcome | None = None
        self._timers: list[threading.Timer] = []
        self._timer_guard = threading.Lock()

    @property
    def done(self) -> bool:
        return self._finished.is_set()

    def wait(self, timeout: float | None = None) -> LoginOutcome | None:
        self._finished.wait(timeout)
        return self.outcome

    def add_timer(self, timer: threading.Timer) -> None:
        """Keep a watchdog timer of this run; it is cancelled when the run finishes (at once if it has)."""
        with self._timer_guard:
            if not self.done:
                self._timers.append(timer)
                return
        timer.cancel()

    def _finish(self, outcome: LoginOutcome) -> None:
        self.outcome = outcome
        with self._timer_guard:
            self._finished.set()
            timers, self._timers = self._timers, []
        for timer in timers:
            timer.cancel()


# (source, http, vault, control, permit, stop_at) -> a browser used as a context manager.
Launcher = Callable[[SourceAccount, SessionHttp, SessionVault, Interruptible, HeadedPermit, float], Any]


def visible_window(source: SourceAccount, http: SessionHttp, vault: SessionVault, control: Interruptible,
                   permit: HeadedPermit, stop_at: float) -> SessionBrowser:
    """The production launcher: the M2a session browser, headed for this sign-in only."""
    return SessionBrowser(source, http, vault, control, headed=permit, stop_at=stop_at)


class LoginCoordinator:
    """The sign-ins of one AccountManager (its project root and Windows account); see the module docstring.
    ``start`` is for the user's Đăng nhập only. ``launcher``, ``network`` and ``http_options`` are the tests'
    dependency injection; production passes none of them."""

    def __init__(self, manager: AccountManager, *, verifiers: Mapping[str, LoginVerifier] = LOGIN_VERIFIERS,
                 launcher: Launcher = visible_window, network: SessionNetwork | None = None,
                 http_options: Mapping[str, Any] | None = None,
                 max_seconds: float = LOGIN_ATTEMPT_MAX_SECONDS, poll_seconds: float = POLL_SECONDS,
                 grace_seconds: float = GRACE_SECONDS,
                 on_finish: Callable[[LoginOutcome], None] | None = None):
        for name, value, cap in (("max_seconds", max_seconds, LOGIN_ATTEMPT_MAX_SECONDS),
                                 ("poll_seconds", poll_seconds, MAX_POLL_SECONDS),
                                 ("grace_seconds", grace_seconds, MAX_GRACE_SECONDS)):
            if not 0 < value <= cap:
                raise ValueError(f"{name} must be above 0 and at most {cap:g}")
        self.manager = manager
        self.verifiers = dict(verifiers)
        self.launcher = launcher
        self.network = network
        self.http_options = dict(http_options or {})
        self.max_seconds = max_seconds
        self.poll_seconds = poll_seconds
        self.grace_seconds = grace_seconds
        # Called once per sign-in, after its run finished (M4: the downloader keeps the outcome and wakes its
        # dispatcher); in the window's thread, never under a lock of this coordinator. Its errors are ignored.
        self.on_finish = on_finish
        self._runs: dict[str, LoginRun] = {}
        self._closed = False
        self._guard = threading.Lock()  # this coordinator's run table only; never held while a window runs

    def start(self, source_id: str) -> LoginRun:
        """The user pressed Đăng nhập: begin the sign-in and open its window in a thread. Before any window:
        AccountUnknown; LoginUnsupported (no verifier); AccountBusy with LOGIN_BUSY (one is open) or
        ACCOUNT_BUSY (the database could not be written); RuntimeError after ``shutdown``."""
        source = self.manager.config.sources.get(source_id)
        if source is None:
            raise AccountUnknown("ACCOUNT_UNKNOWN", source_id)
        verifier = self.verifiers.get(source.adapter.id)
        if verifier is None:
            raise LoginUnsupported("LOGIN_UNSUPPORTED", source_id)
        with self._guard:
            if self._closed:
                raise RuntimeError("The sign-in coordinator was shut down")
            running = self._runs.get(source_id)
            if running is not None and not running.done:
                raise AccountBusy("LOGIN_BUSY", source_id)
            started = time.monotonic()  # before the attempt: the window's deadline never outlasts it
            try:
                attempt = self.manager.begin_login(source_id)
            except sqlite3.Error:
                raise AccountBusy("ACCOUNT_BUSY", source_id) from None
            run = self._runs[source_id] = LoginRun(attempt, started + self.max_seconds)
        worker = threading.Thread(target=self._run, args=(run, source, verifier), name=f"source-login-{source_id}",
                                  daemon=True)
        try:
            worker.start()
        except RuntimeError:  # no thread to be had: the sign-in ends at once
            self._finish(run, LoginOutcome(source_id, "BROWSER_UNAVAILABLE"))
        return run

    def active(self) -> list[str]:
        """The sources whose window of this coordinator has not finished (read only)."""
        with self._guard:
            return [source_id for source_id, run in self._runs.items() if not run.done]

    def cancel(self, source_id: str) -> bool:
        """The user's Hủy. A window of this coordinator: stop it and end its own attempt (a late result is then
        refused, and a newer attempt is never touched); a window still open ``grace_seconds`` later is killed.
        No window here: end the source's open sign-in in the manager (one another caller left). True when
        something was cancelled."""
        with self._guard:
            run = self._runs.get(source_id)
        if run is not None and not run.done:
            self._stop_run(run, "cancel")
            self._kill_if_hung(run, self.grace_seconds)
            return True
        try:
            return self.manager.cancel_login(source_id)
        except sqlite3.Error:
            return False

    def shutdown(self, timeout: float = 30.0) -> None:
        """Control Center stop (M4): refuse new sign-ins, cancel every window as ``cancel`` does (its own
        attempt ends in the manager, so a sign-in not committed yet is never saved) and wait for them to close,
        at most ``timeout`` seconds in all (a window still open ``grace_seconds`` later is killed)."""
        with self._guard:
            self._closed = True
            runs = [run for run in self._runs.values() if not run.done]
        for run in runs:
            self._stop_run(run, "cancel")
            self._kill_if_hung(run, self.grace_seconds)
        end = time.monotonic() + timeout
        for run in runs:
            run.wait(max(end - time.monotonic(), 0.0))

    def _stop_run(self, run: LoginRun, reason: str) -> None:
        """Stop a run (``reason``: "cancel" or "timeout"): first its control (the window stops, a request in
        flight is cut), then its own attempt in the manager, never another one. The manager ends the attempt
        under the source's lock, the same lock its commit of a sign-in holds (``complete_login``): a stop that
        gets there first makes that commit fail, so nothing is saved; one that comes after the commit changes
        nothing. The control goes first, so a refused commit is read as this stop (``_complete``)."""
        run.control.request(reason)
        try:
            self.manager.end_login(run.attempt, self._requested(run))
        except sqlite3.Error:  # the window stops anyway; the attempt ends at its time limit
            pass

    def _kill_if_hung(self, run: LoginRun, seconds: float) -> threading.Timer:
        """In ``seconds``, kill the run's Edge if the run has not finished by then: a Playwright call that hangs
        (a page stuck in a script, a hung browser) then fails, and the run's thread closes the rest as usual.
        ``grace_seconds`` after that, a run still not finished has its own driver ended (``_end_driver``)."""
        return self._later(run, seconds, self._force_close)

    def _later(self, run: LoginRun, seconds: float, action: Callable[[LoginRun], None]) -> threading.Timer:
        timer = threading.Timer(max(seconds, 0.0), action, (run,))
        timer.daemon = True
        run.add_timer(timer)
        timer.start()
        return timer

    def _force_close(self, run: LoginRun) -> None:
        force = getattr(run.browser, "force_close", None)
        if run.done or force is None:
            return
        try:
            force()
        except Exception:  # noqa: BLE001 - the driver's turn still comes
            pass
        self._later(run, self.grace_seconds, self._end_driver)

    @staticmethod
    def _end_driver(run: LoginRun) -> None:
        end = getattr(run.browser, "end_driver", None)
        if run.done or end is None:
            return
        try:
            end()
        except Exception:  # noqa: BLE001 - nothing more can be done from this thread
            pass

    # The window's thread -------------------------------------------------------------------------------

    def _run(self, run: LoginRun, source: SourceAccount, verifier: LoginVerifier) -> None:
        outcome = LoginOutcome(source.id, "LOGIN_FAILED")
        try:
            outcome = self._sign_in(run, source, verifier)
        except Exception as error:  # noqa: BLE001 - a bug ends the sign-in as failed; nothing was saved
            outcome = LoginOutcome(source.id, "LOGIN_FAILED", failure=type(error).__name__)
        finally:
            self._finish(run, outcome)

    def _finish(self, run: LoginRun, outcome: LoginOutcome) -> None:
        try:
            if not outcome.connected:  # a no-op when the manager ended or replaced the attempt already
                self.manager.end_login(run.attempt, outcome.code if outcome.code in _END_CODES else "LOGIN_FAILED")
        except Exception:  # noqa: BLE001 - the attempt still ends at its time limit; the run must finish
            pass
        finally:
            run._finish(outcome)
            self._notify(outcome)

    def _notify(self, outcome: LoginOutcome) -> None:
        if self.on_finish is None:
            return
        try:
            self.on_finish(outcome)
        except Exception:  # noqa: BLE001 - a listener's error never changes the sign-in
            pass

    def _sign_in(self, run: LoginRun, source: SourceAccount, verifier: LoginVerifier) -> LoginOutcome:
        # At the deadline the run is stopped as a cancel would be (``_stop_run``): a request in flight is cut
        # (its socket closed), the page is refused everything, its attempt ends in the manager, and the run
        # ends as timed out. A window still open ``grace_seconds`` later is hung: it is killed.
        left = max(run.deadline - time.monotonic(), 0.0)
        timer = threading.Timer(left, self._stop_run, (run, "timeout"))
        timer.daemon = True
        timer.start()
        killer = self._kill_if_hung(run, left + self.grace_seconds)
        try:
            return self._open_window(run, source, verifier)
        finally:
            timer.cancel()
            killer.cancel()

    def _open_window(self, run: LoginRun, source: SourceAccount, verifier: LoginVerifier) -> LoginOutcome:
        stop = self._stop(run, None)
        if stop:
            return LoginOutcome(source.id, stop)
        http = SessionHttp(source.all_hosts, self.network, **self.http_options)
        window: Any = None
        outcome: LoginOutcome | None = None
        code = "LOGIN_FAILED"
        try:
            window = run.browser = self.launcher(source, http, self.manager.vault, run.control,
                                                 HeadedPermit(run.attempt), run.deadline)
            with window as browser:
                code = self._watch(run, source, verifier, browser)
                if code == "EVIDENCE":
                    outcome = self._complete(run, source, browser)  # the window closes after the save
        except Cancelled:
            code = self._requested(run)
        except BrowserUnavailable:
            code = self._stop(run, None) or "BROWSER_UNAVAILABLE"
        except BrowserFailed:
            code = self._stop(run, None) or "BROWSER_FAILED"
        except VaultError:  # the private profile folder could not be made or pinned
            code = "PROFILE_FAILED"
        except Exception:  # a call freed when the window's own driver was ended (a plain Exception), or a bug
            stopped = self._stop(run, None)
            if stopped is None:
                raise
            code = stopped  # stopped: the deadline or a cancel, whatever the freed call raised
        outcome = outcome or LoginOutcome(source.id, code)
        if getattr(window, "profile_left", False):
            outcome = replace(outcome, profile_left=True)
        hang = getattr(window, "hang", None)
        return replace(outcome, hang=hang) if isinstance(hang, DriverHang) else outcome

    @staticmethod
    def _requested(run: LoginRun) -> str:
        return "LOGIN_TIMEOUT" if run.control.reason == "timeout" else "LOGIN_CANCELLED"

    def _watch(self, run: LoginRun, source: SourceAccount, verifier: LoginVerifier, browser: Any) -> str:
        """Open the login page, then ask the verifier until it sees evidence ("EVIDENCE") or a stop code."""
        stop = self._stop(run, browser)
        if stop:  # cancelled or timed out while the window was starting: the login page is not opened
            return stop
        try:
            browser.navigate(source.login_url)
        except Cancelled:
            return self._requested(run)
        except BrowserFailed:
            return self._stop(run, browser) or "LOGIN_PAGE_FAILED"
        except _playwright_error():  # the window went away before the page could be opened
            return self._stop(run, browser) or "BROWSER_FAILED"
        view = LoginView(browser)
        while True:
            stop = self._stop(run, browser)
            if stop:
                return stop
            try:
                signed_in = verifier.signed_in(view) is True
            except Exception:  # noqa: BLE001 - a broken verifier is never evidence
                return self._stop(run, None) or "VERIFIER_ERROR"  # a page freed by a stop is not the verifier's
            if signed_in:  # unless it was cancelled, replaced or timed out meanwhile
                return self._stop(run, None) or "EVIDENCE"
            if not browser.pause(min(self.poll_seconds, max(run.deadline - time.monotonic(), 0.0))):
                return self._stop(run, browser) or "LOGIN_WINDOW_CLOSED"

    def _stop(self, run: LoginRun, browser: Any) -> str | None:
        if run.control.requested:
            return self._requested(run)
        if browser is not None and browser.window_closed:
            return "LOGIN_WINDOW_CLOSED"
        if time.monotonic() >= run.deadline:
            return "LOGIN_TIMEOUT"
        try:
            current = self.manager.attempt_is_current(run.attempt)
        except sqlite3.Error:  # a busy database ends nothing; completing checks the attempt again
            current = True
        return None if current else "LOGIN_STALE"

    def _complete(self, run: LoginRun, source: SourceAccount, browser: Any) -> LoginOutcome:
        """Evidence seen: collect the state, check again, then commit.

        1. Collect: the context's state, in memory (this can take a while).
        2. Check again: stopped (cancel, shutdown, deadline), the window closed, the deadline passed, the
           attempt no longer the open one. Any of them: nothing is saved, the run ends with that code.
        3. Commit: ``complete_login`` (the vault file, then the row, under the source's lock). This is the
           commit point. A stop that ended this run's attempt in the manager before it (``_stop_run``) makes it
           fail with StaleLogin, read here as that stop: nothing saved, and the session saved before keeps its
           generation, ``authenticated_at`` and TTL. A stop after it changes nothing: the new session stands
           and the run ends CONNECTED (it is never saved and then taken back)."""
        try:
            state = browser.storage_state()
        except BrowserFailed:
            return LoginOutcome(source.id, self._stop(run, browser) or "BROWSER_FAILED")
        stop = self._stop(run, browser)
        if stop:
            return LoginOutcome(source.id, stop)
        try:
            status = self.manager.complete_login(run.attempt, state)
        except StaleLogin as error:
            return LoginOutcome(source.id, self._requested(run) if run.control.requested else error.code)
        except SessionSaveFailed as error:
            return LoginOutcome(source.id, error.code)
        except sqlite3.Error:
            return LoginOutcome(source.id, "SESSION_SAVE_FAILED")
        return LoginOutcome(source.id, "CONNECTED", status)
