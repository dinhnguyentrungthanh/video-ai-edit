"""The sign-in coordinator (download_account_login), with fixture sources only.

No visible window ever opens here:
- the coordinator's own logic runs against a fake window (no Edge);
- the flow (sign-in, evidence, save, reuse) runs on real headless Edge through a launcher that builds the same
  SessionBrowser without the permit, the stand-in for the window, against the local HTTPS fixture server of
  the browser tests (tests/test_download_account_browser.py), so through the M2a route handler only;
- what a headed window changes is checked on Playwright's launch call with a fake runtime (only ``headless``
  differs: the same sandbox, flags, black hole, profile and preferences), never by starting Edge headed.
A mock is no proof of the real window: a headed sign-in has not been checked by the user yet (plan 9.11).
The fixture "user" signs in by itself (a form that submits itself) with a made-up password. Made-up hosts
(.example), cookies and passwords only, in temporary roots under the install's temp/.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from biliflow import download_account_browser as browser_module
from biliflow import download_account_login as login_module
from biliflow.download_account_browser import (
    HARDENING_ARGS,
    NO_WEBRTC_SCRIPT,
    PROFILE_PREFERENCES,
    BrowserFailed,
    BrowserUnavailable,
    HeadedPermit,
    SessionBrowser,
)
from biliflow.download_account_driver import DriverHang
from biliflow.download_account_runs import run_with_session
from biliflow.download_account_http import SessionHttp
from biliflow.download_account_login import (
    LOGIN_VERIFIERS,
    LoginCoordinator,
    LoginUnsupported,
    LoginView,
    visible_window,
)
from biliflow.download_account_vault import SessionVault, VaultRefused
from biliflow.download_accounts import AccountBusy, AccountManager, AccountUnknown, LoginRequired, StaleLogin
from biliflow.download_http import Cancelled
from biliflow.download_runner import ProcessControl
from tests import test_download_account_browser as browser_tests
from tests.source_fixtures import PUBLIC_ADDRESS, Reply
from tests.test_download_account_browser import (
    ALPHA,
    BETA,
    BETA_PORTAL,
    CANARY_PW,
    CANARY_SID,
    CONFIG,
    FIRST,
    HAVE_BROWSER,
    HAVE_PLAYWRIGHT,
    NEED_BROWSER,
    PORTAL,
    TEMP_PARENT,
    BrowserCase,
    cookie,
    cookies_of,
    page_reply,
    redirect,
)
from tests.test_download_accounts import T0, Clock, FakeProtector, GoodAcl
from tests.tls_fixtures import HAVE_TLS

FIXTURE_ADAPTER = "ticket-files"
RUN_SECONDS = 90  # the most a test waits for one sign-in to end
# The fixture's own sign-in: the form submits itself (the "user"), the source sets its cookie and redirects,
# then its home page writes localStorage and IndexedDB and tells the source it is ready.
LOGIN_FORM = (f'<form method="post" action="/session"><input type="password" name="pw" value="{CANARY_PW}">'
              "</form><script>setTimeout(() => document.forms[0].submit(), 300)</script>")
HOME_PAGE = """<p>home</p><script>
localStorage.setItem('tok', 't-ls');
const opening = indexedDB.open('bf', 1);
opening.onupgradeneeded = () => opening.result.createObjectStore('kv');
opening.onsuccess = () => {
  const tx = opening.result.transaction('kv', 'readwrite');
  tx.objectStore('kv').put('t-idb', 'tok');
  tx.oncomplete = () => { opening.result.close(); fetch('/api/ready'); };
};
</script>"""
READ_STORAGE = """async () => {
  const db = await new Promise((ok, no) => {
    const r = indexedDB.open('bf', 1);
    r.onupgradeneeded = () => r.result.createObjectStore('kv');
    r.onsuccess = () => ok(r.result); r.onerror = () => no(r.error);
  });
  const value = await new Promise(ok => {
    const g = db.transaction('kv').objectStore('kv').get('tok');
    g.onsuccess = () => ok(g.result === undefined ? null : g.result); g.onerror = () => ok(null);
  });
  db.close();
  return {ls: localStorage.getItem('tok'), idb: value};
}"""


def setUpModule():
    browser_tests.setUpModule()


def tearDownModule():
    browser_tests.tearDownModule()


class FixtureVerifier:
    """The fixture source's evidence: its own /api/me, asked from the shown page, says signedIn. ``outside``
    also asks the other source, a foreign host and plain http first (never reached)."""

    def __init__(self, outside: bool = False):
        self.calls = 0
        self.outside = outside
        self.foreign: list = []
        self.seen: list[tuple[str, str | None]] = []

    def signed_in(self, view: LoginView) -> bool:
        self.calls += 1
        if self.outside:
            self.foreign += [view.fetch(f"https://{BETA_PORTAL}/api/me"), view.fetch("https://evil.example/api/me"),
                             view.fetch(f"http://{PORTAL}/api/me")]
        self.seen.append((view.url, view.text("p")))
        answer = view.fetch(f"https://{PORTAL}/api/me")
        if answer is None or answer.status != 200:
            return False
        try:
            return json.loads(answer.body).get("signedIn") is True
        except ValueError:
            return False


class Answers:
    """A verifier that gives its answers in turn (then False); ``before`` runs ahead of each check."""

    def __init__(self, *answers: bool, error: Exception | None = None, before=None):
        self.answers = list(answers)
        self.error = error
        self.before = before
        self.calls = 0

    def signed_in(self, view: LoginView) -> bool:
        self.calls += 1
        if self.before is not None:
            self.before(self.calls)
        if self.error is not None:
            raise self.error
        return self.answers.pop(0) if self.answers else False


class StuckVerifier:
    """Lets the page start its endless script, then asks the page for a text: the call hangs until Edge is
    killed. ``stuck``: how long that call took."""

    def __init__(self):
        self.entered = threading.Event()
        self.stuck = 0.0

    def signed_in(self, view: LoginView) -> bool:
        time.sleep(0.5)
        self.entered.set()
        asked = time.monotonic()
        view.text("p")
        self.stuck = time.monotonic() - asked
        return False


class GatedVerifier:
    """Blocks in its first check until released, then claims success: a result that comes too late."""

    def __init__(self):
        self.entered, self.release = threading.Event(), threading.Event()

    def signed_in(self, view: LoginView) -> bool:
        self.entered.set()
        self.release.wait(30)
        return True


class FakeWindow:
    """The window for the coordinator's own logic: no Edge. ``closes_after``: the user closes it at that pause;
    ``hold``: pauses wait for it to be set; ``slow_page``: the login page loads until the run is stopped (a
    request in flight, cut by the run's control); ``hang``: its first pause never returns by itself (a hung
    Playwright call), only when the window is killed (``force_close``)."""

    def __init__(self, control, *, enter_error=None, navigate_error=None, state=None, state_error=None,
                 closes_after: int | None = None, hold: threading.Event | None = None, slow_page: bool = False,
                 hang: bool = False, profile_left: bool = False, on_enter=None, on_read=None):
        self.control = control
        self.enter_error, self.navigate_error, self.state_error = enter_error, navigate_error, state_error
        self.state = state if state is not None else {"cookies": [cookie("sid", CANARY_SID, PORTAL)], "origins": []}
        self.closes_after, self.hold, self.slow_page = closes_after, hold, slow_page
        self.hang, self.profile_left, self.on_enter, self.on_read = hang, profile_left, on_enter, on_read
        self.killed = threading.Event()
        self.paused = threading.Event()  # set at the first pause: the window is open and the page was asked
        self.context = None
        self.urls: list[str] = []
        self.pauses = 0
        self.closed = self.user_closed = False

    def force_close(self):
        self.killed.set()
        self.user_closed = True
        return 1

    def __enter__(self):
        if self.on_enter is not None:
            self.on_enter()
        if self.enter_error is not None:
            raise self.enter_error
        return self

    def __exit__(self, *exc):
        self.closed = True

    def navigate(self, url):
        self.urls.append(url)
        if self.navigate_error is not None:
            raise self.navigate_error
        while self.slow_page and not self.control.wait(0.01):
            pass
        if self.slow_page:
            raise Cancelled()

    @property
    def window_closed(self):
        return self.user_closed

    def pause(self, seconds):
        self.pauses += 1
        self.paused.set()
        if self.hang:
            self.killed.wait(RUN_SECONDS)
            return not self.user_closed
        if self.hold is not None:
            self.hold.wait(seconds)
        else:
            time.sleep(min(seconds, 0.01))
        if self.closes_after is not None and self.pauses >= self.closes_after:
            self.user_closed = True
            return False
        return True

    def storage_state(self):
        if self.on_read is not None:  # something happens while the state is read (it can take a while)
            self.on_read(self)
        if self.state_error is not None:
            raise self.state_error
        return self.state


class StuckDriverWindow(FakeWindow):
    """A hung window whose call still waits after its Edge is killed (its driver never saw Edge go): only the
    end of its own driver frees it, unless ``freed_by_edge``. The freed call then raises what Playwright raises
    once its driver is gone: a plain Exception, not a Playwright error."""

    def __init__(self, control, freed_by_edge: bool = False):
        super().__init__(control)
        self.freed_by_edge = freed_by_edge
        self.edge_kills = self.driver_ends = 0
        self.hang = None  # the browser's report (a DriverHang), not FakeWindow's flag: ``pause`` always hangs here

    def pause(self, seconds):
        self.pauses += 1
        self.paused.set()
        self.killed.wait(RUN_SECONDS)
        if self.driver_ends:
            raise Exception("Connection closed while reading from the driver")
        return not self.user_closed

    def force_close(self):
        self.edge_kills += 1
        return super().force_close() if self.freed_by_edge else 1

    def end_driver(self):
        self.driver_ends += 1
        self.hang = DriverHang("page", ("playwright._impl._sync_base:_sync:113",))
        self.killed.set()
        return True


def stored_sessions(manager: AccountManager, source_id: str = "alpha") -> list[str]:
    folder = manager.vault.source_folder(source_id)
    return sorted(path.name for path in folder.glob("session-*")) if folder.exists() else []


class CoordinatorCase(unittest.TestCase):
    """A coordinator with fake windows: no browser, no network."""

    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        self._root = TemporaryDirectory(dir=TEMP_PARENT, prefix="session-login-")
        self.addCleanup(self._root.cleanup)
        self.root = Path(self._root.name)
        self.clock = Clock()
        self.manager = self.new_manager(self.root)
        self.windows: list[FakeWindow] = []
        self.launches: list[tuple] = []

    def new_manager(self, root: Path) -> AccountManager:
        manager = AccountManager(root, CONFIG, vault=SessionVault(root, protector=FakeProtector(),
                                                                  acl=GoodAcl(FIRST)), clock=self.clock)
        self.addCleanup(manager.close)
        return manager

    def launcher(self, **window):
        def launch(source, http, vault, control, permit, stop_at):
            self.launches.append((source, http, vault, control, permit, stop_at))
            self.windows.append(FakeWindow(control, **window))
            return self.windows[-1]
        return launch

    def coordinator(self, verifier=None, manager=None, launcher=None, **options) -> LoginCoordinator:
        options.setdefault("poll_seconds", 0.01)
        coordinator = LoginCoordinator(manager or self.manager, verifiers={FIXTURE_ADAPTER: verifier or Answers(True)},
                                       launcher=launcher or self.launcher(), **options)
        self.addCleanup(coordinator.shutdown, 10)
        return coordinator

    def sign_in(self, coordinator: LoginCoordinator):
        outcome = coordinator.start("alpha").wait(RUN_SECONDS)
        self.assertIsNotNone(outcome)
        return outcome

    def paused_window(self, count: int) -> FakeWindow:
        """The ``count``-th window, once it waits in its first pause."""
        deadline = time.monotonic() + RUN_SECONDS
        while len(self.windows) < count and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.windows[count - 1].paused.wait(RUN_SECONDS))
        return self.windows[count - 1]


class CoordinatorTest(CoordinatorCase):
    """The coordinator's logic with a fake window: no browser, no network."""

    def test_an_adapter_without_a_verifier_opens_no_window(self):
        # Production has a verifier for "release-forms" only (M7); alpha's adapter "ticket-files" has none.
        self.assertEqual(set(LOGIN_VERIFIERS), {"release-forms"})
        self.assertEqual(self.manager.config.sources["alpha"].adapter.id, "ticket-files")
        launcher = mock.Mock()
        coordinator = LoginCoordinator(self.manager, launcher=launcher)
        with self.assertRaises(LoginUnsupported) as caught:
            coordinator.start("alpha")
        self.assertEqual(caught.exception.code, "LOGIN_UNSUPPORTED")
        self.assertIn("chưa mở cửa sổ", caught.exception.message)
        self.assertEqual(launcher.mock_calls, [])
        self.assertEqual(self.manager.status("alpha")["state"], "NOT_CONNECTED")  # never LOGGING_IN
        with self.assertRaises(AccountUnknown):
            self.coordinator().start("gamma")
        self.assertEqual(self.launches, [])

    def test_the_limits_of_the_wait(self):
        for options in ({"max_seconds": 0}, {"max_seconds": 601}, {"max_seconds": float("nan")},
                        {"poll_seconds": 0}, {"poll_seconds": 11}, {"grace_seconds": 0}, {"grace_seconds": 61}):
            with self.assertRaises(ValueError):
                LoginCoordinator(self.manager, **options)
        LoginCoordinator(self.manager, max_seconds=600, poll_seconds=10, grace_seconds=60)

    def test_evidence_saves_the_state_through_the_manager(self):
        verifier = Answers(False, False, True)
        outcome = self.sign_in(self.coordinator(verifier))
        self.assertEqual((outcome.code, outcome.connected), ("CONNECTED", True))
        self.assertEqual(verifier.calls, 3)
        status = self.manager.status("alpha")
        self.assertEqual(status["state"], "CONNECTED")
        self.assertEqual(status["authenticated_at"], T0.isoformat())
        self.assertEqual(status["recheck_at"], (T0 + timedelta(seconds=3600)).isoformat())
        self.assertEqual(outcome.status, status)
        lease = self.manager.session_for("alpha")
        self.assertEqual((lease.generation, lease.state["cookies"][0]["value"]), (1, CANARY_SID))
        source, http, vault, control, permit, stop_at = self.launches[0]
        self.assertIs(source, ALPHA)
        self.assertEqual(http.hosts, ALPHA.all_hosts)  # the M2a checked client of exactly this source
        self.assertIs(vault, self.manager.vault)
        self.assertIsInstance(permit, HeadedPermit)
        self.assertEqual(permit.attempt.source_id, "alpha")
        self.assertTrue(self.manager.owns(permit.attempt))
        self.assertGreater(stop_at, time.monotonic())
        self.assertEqual(self.windows[0].urls, [ALPHA.login_url])
        self.assertTrue(self.windows[0].closed)
        for text in (repr(outcome), outcome.message, repr(permit), json.dumps(self.manager.statuses())):
            self.assertNotIn(CANARY_SID, text)
            self.assertNotIn(permit.attempt.attempt_id, text)

    def test_every_failure_ends_the_sign_in_without_a_session(self):
        cases = {
            "BROWSER_UNAVAILABLE": ({"enter_error": BrowserUnavailable("x")}, None),
            "PROFILE_FAILED": ({"enter_error": VaultRefused("x")}, None),
            "LOGIN_PAGE_FAILED": ({"navigate_error": BrowserFailed("TLS_ERROR", PORTAL)}, None),
            "VERIFIER_ERROR": ({}, Answers(error=RuntimeError(CANARY_SID))),
            "BROWSER_FAILED": ({"state_error": BrowserFailed("SESSION_READ_FAILED")}, None),
            "LOGIN_FAILED": ({"state": {"cookies": "not a list"}}, None),
        }
        for code, (window, verifier) in cases.items():
            with self.subTest(code):
                outcome = self.sign_in(self.coordinator(verifier, launcher=self.launcher(**window)))
                self.assertEqual(outcome.code, code)
                self.assertFalse(outcome.connected)
                status = self.manager.status("alpha")
                self.assertEqual((status["state"], status["error_code"]), ("NOT_CONNECTED", "LOGIN_FAILED"))
                self.assertEqual(stored_sessions(self.manager), [])
                self.assertNotIn(CANARY_SID, f"{outcome!r} {outcome.message}")
        with self.subTest("a launcher bug"):
            def broken(*args):
                raise RuntimeError(CANARY_SID)
            outcome = self.sign_in(self.coordinator(launcher=broken))
            self.assertEqual(outcome.code, "LOGIN_FAILED")
            self.assertEqual(self.manager.status("alpha")["state"], "NOT_CONNECTED")

    def test_a_database_error_while_saving_is_a_save_failure(self):
        coordinator = self.coordinator()
        with mock.patch.object(self.manager, "complete_login", side_effect=sqlite3.OperationalError("locked")):
            outcome = self.sign_in(coordinator)
        self.assertEqual(outcome.code, "SESSION_SAVE_FAILED")
        self.assertEqual(self.manager.status("alpha")["error_code"], "LOGIN_FAILED")
        with mock.patch.object(self.manager, "attempt_is_current", side_effect=sqlite3.OperationalError("busy")):
            outcome = self.sign_in(self.coordinator(Answers(False, True)))
        self.assertEqual(outcome.code, "CONNECTED")  # a busy read ends nothing; completing checks again

    def test_the_window_closed_by_the_user(self):
        outcome = self.sign_in(self.coordinator(Answers(), launcher=self.launcher(closes_after=2)))
        self.assertEqual(outcome.code, "LOGIN_WINDOW_CLOSED")
        status = self.manager.status("alpha")
        self.assertEqual((status["state"], status["error_code"]), ("NOT_CONNECTED", "LOGIN_WINDOW_CLOSED"))

    def test_one_deadline_for_the_whole_run(self):
        # Between checks either the loop or the timer sees the deadline first; a page still loading is stopped
        # only by the timer (the run's control).
        for window, reasons in (({}, (None, "timeout")), ({"slow_page": True}, ("timeout",))):
            with self.subTest(window=window):
                started = time.monotonic()
                coordinator = self.coordinator(Answers(), launcher=self.launcher(**window), max_seconds=0.5)
                run = coordinator.start("alpha")
                outcome = run.wait(RUN_SECONDS)
                self.assertEqual(outcome.code, "LOGIN_TIMEOUT")
                self.assertLess(time.monotonic() - started, 5)
                self.assertIn(run.control.reason, reasons)
                self.assertEqual(self.manager.status("alpha")["error_code"], "LOGIN_TIMEOUT")
                self.assertLessEqual(self.launches[-1][5], started + 0.5 + 0.1)  # the browser's stop_at

    def test_a_second_start_is_refused_before_a_second_window(self):
        hold = threading.Event()
        verifier = Answers()
        coordinator = self.coordinator(verifier, launcher=self.launcher(hold=hold))
        run = coordinator.start("alpha")
        with self.assertRaises(AccountBusy) as caught:
            coordinator.start("alpha")
        self.assertEqual(caught.exception.code, "LOGIN_BUSY")
        again = self.coordinator(Answers(), manager=self.new_manager(self.root))  # a second caller, same root
        with self.assertRaises(AccountBusy) as other:
            again.start("alpha")
        self.assertEqual(other.exception.code, "LOGIN_BUSY")  # the open sign-in, not a busy database
        self.assertTrue(coordinator.cancel("alpha"))
        hold.set()
        self.assertEqual(run.wait(RUN_SECONDS).code, "LOGIN_CANCELLED")
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.manager.status("alpha")["error_code"], "LOGIN_CANCELLED")
        verifier.answers = [True]
        self.assertEqual(self.sign_in(coordinator).code, "CONNECTED")  # the next sign-in may start
        self.assertEqual(len(self.launches), 2)

    def test_a_sign_in_ended_elsewhere_stops_the_window(self):
        verifier = Answers(False, True, before=lambda call: call == 2 and self.manager.cancel_login("alpha"))
        outcome = self.sign_in(self.coordinator(verifier))
        self.assertEqual(outcome.code, "LOGIN_STALE")  # its success after the end is never saved
        self.assertEqual(stored_sessions(self.manager), [])
        status = self.manager.status("alpha")
        self.assertEqual((status["state"], status["error_code"]), ("NOT_CONNECTED", "LOGIN_CANCELLED"))

    def test_a_newer_sign_in_is_never_ended_by_the_old_window(self):
        newer = {}

        def replace(call):
            if call == 1:
                self.manager.cancel_login("alpha")
                newer["attempt"] = self.manager.begin_login("alpha")
        outcome = self.sign_in(self.coordinator(Answers(True, before=replace)))
        self.assertEqual(outcome.code, "LOGIN_STALE")
        self.assertTrue(self.manager.attempt_is_current(newer["attempt"]))
        self.assertEqual(self.manager.status("alpha")["state"], "LOGGING_IN")

    def test_a_cancel_before_any_window_and_shutdown(self):
        hold = threading.Event()
        coordinator = self.coordinator(Answers(), launcher=self.launcher(hold=hold))
        self.assertFalse(coordinator.cancel("alpha"))  # nothing open
        run = coordinator.start("alpha")
        coordinator.shutdown(timeout=0.01)
        hold.set()
        self.assertEqual(run.wait(RUN_SECONDS).code, "LOGIN_CANCELLED")
        launched = len(self.launches)  # 0 or 1: the stop may come before the window starts
        with self.assertRaises(RuntimeError):  # no new window after the stop
            coordinator.start("alpha")
        self.assertEqual(len(self.launches), launched)
        self.assertEqual(self.manager.status("alpha")["state"], "NOT_CONNECTED")

    def test_only_true_is_evidence(self):
        verifier = Answers(1, "yes", [True], True)
        self.assertEqual(self.sign_in(self.coordinator(verifier)).code, "CONNECTED")
        self.assertEqual(verifier.calls, 4)

    def test_no_thread_for_the_window(self):
        coordinator = self.coordinator()
        with mock.patch.object(login_module.threading.Thread, "start", side_effect=RuntimeError("no thread")):
            run = coordinator.start("alpha")
        self.assertTrue(run.done)
        self.assertEqual(run.outcome.code, "BROWSER_UNAVAILABLE")
        self.assertEqual(self.launches, [])
        self.assertEqual(self.manager.status("alpha")["state"], "NOT_CONNECTED")  # the attempt was ended

    def test_the_users_cancel_ends_only_its_own_attempt(self):
        coordinator = self.coordinator(Answers(), launcher=self.launcher(hang=True), grace_seconds=0.1)
        run = coordinator.start("alpha")
        self.paused_window(1)  # the window waits (its next check would see the change)
        self.manager.cancel_login("alpha")  # meanwhile another caller replaced the sign-in
        newer = self.manager.begin_login("alpha")
        self.assertTrue(coordinator.cancel("alpha"))
        self.assertEqual(run.wait(RUN_SECONDS).code, "LOGIN_CANCELLED")
        self.assertTrue(self.manager.attempt_is_current(newer))  # the stale Hủy never ended the newer one
        self.assertTrue(coordinator.cancel("alpha"))  # no window here: the source's open sign-in ends
        self.assertFalse(self.manager.attempt_is_current(newer))
        with mock.patch.object(self.manager, "cancel_login", side_effect=sqlite3.OperationalError("busy")):
            self.assertFalse(coordinator.cancel("alpha"))

    def test_a_cancel_while_the_window_starts_never_opens_the_login_page(self):
        holder = {}
        launcher = self.launcher(on_enter=lambda: holder["coordinator"].cancel("alpha"))
        coordinator = holder["coordinator"] = self.coordinator(Answers(), launcher=launcher)
        self.assertEqual(self.sign_in(coordinator).code, "LOGIN_CANCELLED")
        self.assertEqual(self.windows[0].urls, [])

    def test_a_kept_profile_and_an_unexpected_error_are_reported(self):
        outcome = self.sign_in(self.coordinator(launcher=self.launcher(profile_left=True)))
        self.assertEqual((outcome.code, outcome.profile_left, outcome.failure), ("CONNECTED", True, None))

        def broken(*args):
            raise KeyError(CANARY_SID)
        outcome = self.sign_in(self.coordinator(launcher=broken))
        self.assertEqual((outcome.code, outcome.failure), ("LOGIN_FAILED", "KeyError"))
        self.assertNotIn(CANARY_SID, f"{outcome!r} {outcome.message}")

    def test_a_failing_end_still_finishes_the_run(self):
        with mock.patch.object(self.manager, "end_login", side_effect=RuntimeError("broken")):
            outcome = self.sign_in(self.coordinator(Answers(), launcher=self.launcher(closes_after=1)))
        self.assertEqual(outcome.code, "LOGIN_WINDOW_CLOSED")

    def test_a_hung_window_is_killed_after_the_deadline_or_a_cancel(self):
        started = time.monotonic()
        run = self.coordinator(Answers(), launcher=self.launcher(hang=True), max_seconds=0.3,
                               grace_seconds=0.3).start("alpha")
        self.assertEqual(run.wait(RUN_SECONDS).code, "LOGIN_TIMEOUT")
        self.assertTrue(self.windows[-1].killed.is_set())
        self.assertLess(time.monotonic() - started, 5)
        coordinator = self.coordinator(Answers(), launcher=self.launcher(hang=True), grace_seconds=0.3)
        run = coordinator.start("alpha")
        window = self.paused_window(2)
        coordinator.cancel("alpha")
        self.assertEqual(run.wait(RUN_SECONDS).code, "LOGIN_CANCELLED")
        self.assertTrue(window.killed.is_set())
        quick = self.coordinator(Answers(True), grace_seconds=0.1)
        self.assertEqual(self.sign_in(quick).code, "CONNECTED")
        time.sleep(0.3)
        self.assertFalse(self.windows[-1].killed.is_set())  # a window that closed in time is never killed

    def stuck_window(self, name: str, freed_by_edge: bool = False):
        """A sign-in whose window stays hung after its Edge is killed, stopped by ``name`` (the deadline or the
        user's cancel): its outcome, how long it took and its window."""
        def launch(source, http, vault, control, permit, stop_at):
            self.windows.append(StuckDriverWindow(control, freed_by_edge))
            return self.windows[-1]
        options = {"max_seconds": 0.3} if name == "deadline" else {}
        coordinator = self.coordinator(Answers(), launcher=launch, grace_seconds=0.3, **options)
        count = len(self.windows) + 1
        started = time.monotonic()
        run = coordinator.start("alpha")
        window = self.paused_window(count)
        if name == "cancel":
            coordinator.cancel("alpha")
        outcome = run.wait(RUN_SECONDS)
        self.assertIsNotNone(outcome)
        return outcome, time.monotonic() - started, window

    def test_a_window_still_hung_after_its_edge_is_killed_has_its_own_driver_ended(self):
        for name, code in (("deadline", "LOGIN_TIMEOUT"), ("cancel", "LOGIN_CANCELLED")):
            with self.subTest(name):
                outcome, seconds, window = self.stuck_window(name)
                self.assertEqual(outcome.code, code)
                self.assertLess(seconds, 5)
                self.assertEqual((window.edge_kills, window.driver_ends), (1, 1))  # its Edge first, then its driver
                self.assertEqual(outcome.hang, window.hang)
                self.assertEqual(outcome.hang.phase, "page")
                time.sleep(0.7)  # the run has finished: nothing fires again
                self.assertEqual((window.edge_kills, window.driver_ends), (1, 1))

    def test_a_window_freed_by_the_kill_of_its_edge_never_has_its_driver_ended(self):
        outcome, _seconds, window = self.stuck_window("deadline", freed_by_edge=True)
        self.assertEqual(outcome.code, "LOGIN_TIMEOUT")
        time.sleep(0.7)  # the driver's step was cancelled when the run finished
        self.assertEqual((window.edge_kills, window.driver_ends), (1, 0))
        self.assertIsNone(outcome.hang)


class CommitPointTest(CoordinatorCase):
    """Evidence was seen; a stop (the deadline, a shutdown, the user's Hủy) comes while the state is read or
    right before its commit (``complete_login``): nothing is saved, the session saved before keeps its
    generation, ``authenticated_at`` and TTL, and a newer attempt is never touched. A stop after the commit
    point leaves the new session in place. Fake windows and made-up cookies only."""

    def fresh(self) -> None:
        """A new root and manager for each case, at T0."""
        root = TemporaryDirectory(dir=TEMP_PARENT, prefix="session-login-commit-")
        self.addCleanup(root.cleanup)
        self.clock.at(0)
        self.manager = self.new_manager(Path(root.name))

    def with_old_session(self) -> None:
        self.manager.complete_login(self.manager.begin_login("alpha"),
                                    {"cookies": [cookie("sid", "old", PORTAL)], "origins": []})
        self.clock.at(100)  # the new sign-in comes later: a new session would carry a new authenticated_at

    def assert_old_session_kept(self, code: str) -> None:
        status = self.manager.status("alpha")
        self.assertEqual((status["state"], status["error_code"]), ("CONNECTED", code))
        self.assertEqual(status["authenticated_at"], T0.isoformat())  # neither renewed nor extended
        self.assertEqual(status["recheck_at"], (T0 + timedelta(seconds=3600)).isoformat())
        lease = self.manager.session_for("alpha")
        self.assertEqual((lease.generation, [item["value"] for item in lease.state["cookies"]]), (1, ["old"]))
        self.assertEqual(stored_sessions(self.manager), ["session-1.bin"])

    def assert_nothing_saved(self, code: str) -> None:
        status = self.manager.status("alpha")
        self.assertEqual((status["state"], status["error_code"]), ("NOT_CONNECTED", code))
        self.assertEqual(stored_sessions(self.manager), [])

    @staticmethod
    def stops(holder: dict) -> dict:
        """What stops the run while its state is read: name -> (the action, its code, coordinator options)."""
        return {
            "deadline": (lambda window: window.control.wait(5), "LOGIN_TIMEOUT",
                         {"max_seconds": 0.3, "grace_seconds": 5}),
            "shutdown": (lambda window: holder["coordinator"].shutdown(timeout=0), "LOGIN_CANCELLED", {}),
            "cancel": (lambda window: holder["coordinator"].cancel("alpha"), "LOGIN_CANCELLED", {}),
        }

    def test_a_stop_while_the_state_is_read_saves_nothing(self):
        for old in (False, True):
            for name in ("deadline", "shutdown", "cancel"):
                with self.subTest(stop=name, old_session=old):
                    self.fresh()
                    if old:
                        self.with_old_session()
                    holder: dict = {}
                    during, code, options = self.stops(holder)[name]
                    coordinator = holder["coordinator"] = self.coordinator(
                        Answers(True), launcher=self.launcher(on_read=during), **options)
                    self.assertEqual(self.sign_in(coordinator).code, code)
                    if old:
                        self.assert_old_session_kept(code)
                    else:
                        self.assert_nothing_saved(code)

    def test_a_stop_that_reaches_the_manager_right_before_the_commit_saves_nothing(self):
        # After the last check and before the commit: only the manager can refuse it (the source's lock).
        for name, code in (("cancel", "LOGIN_CANCELLED"), ("shutdown", "LOGIN_CANCELLED"), ("deadline", "LOGIN_TIMEOUT")):
            with self.subTest(name):
                self.fresh()
                self.with_old_session()
                coordinator = self.coordinator(Answers(True))
                commit = self.manager.complete_login

                def stop_then_commit(attempt, state, name=name, coordinator=coordinator, commit=commit):
                    if name == "cancel":
                        coordinator.cancel("alpha")
                    elif name == "shutdown":
                        coordinator.shutdown(timeout=0)
                    else:  # what the deadline's timer calls
                        coordinator._stop_run(coordinator._runs["alpha"], "timeout")
                    return commit(attempt, state)
                with mock.patch.object(self.manager, "complete_login", side_effect=stop_then_commit):
                    self.assertEqual(self.sign_in(coordinator).code, code)
                self.assert_old_session_kept(code)

    def test_a_stop_after_the_commit_point_leaves_the_new_session(self):
        for name in ("cancel", "shutdown"):
            with self.subTest(name):
                self.fresh()
                self.with_old_session()
                coordinator = self.coordinator(Answers(True))
                commit, seen = self.manager.complete_login, {}

                def commit_then_stop(attempt, state, name=name, coordinator=coordinator, commit=commit):
                    status = commit(attempt, state)
                    seen["cancel"] = coordinator.cancel("alpha") if name == "cancel" else coordinator.shutdown(timeout=0)
                    return status
                with mock.patch.object(self.manager, "complete_login", side_effect=commit_then_stop):
                    outcome = self.sign_in(coordinator)
                self.assertEqual(outcome.code, "CONNECTED")  # never saved and then taken back
                status = self.manager.status("alpha")
                self.assertEqual((status["state"], status["error_code"]), ("CONNECTED", None))
                self.assertEqual(status["authenticated_at"], (T0 + timedelta(seconds=100)).isoformat())
                lease = self.manager.session_for("alpha")
                self.assertEqual((lease.generation, lease.state["cookies"][0]["value"]), (2, CANARY_SID))
                self.assertEqual(stored_sessions(self.manager), ["session-2.bin"])
                if name == "cancel":
                    self.assertIs(seen["cancel"], True)  # the window was still open: it is closed, nothing undone

    def test_an_old_runs_stop_never_ends_a_newer_attempt(self):
        for name in ("deadline", "shutdown", "cancel"):
            with self.subTest(name):
                self.fresh()
                self.with_old_session()
                holder: dict = {}
                newer: dict = {}
                during, code, options = self.stops(holder)[name]

                def replaced_then_stopped(window, during=during, newer=newer):
                    self.manager.cancel_login("alpha")  # another caller ends this attempt and begins its own
                    newer["attempt"] = self.manager.begin_login("alpha")
                    during(window)
                coordinator = holder["coordinator"] = self.coordinator(
                    Answers(True), launcher=self.launcher(on_read=replaced_then_stopped), **options)
                self.assertEqual(self.sign_in(coordinator).code, code)
                self.assertTrue(self.manager.attempt_is_current(newer["attempt"]))
                self.assertEqual(self.manager.status("alpha")["state"], "LOGGING_IN")
                lease = self.manager.session_for("alpha")
                self.assertEqual((lease.generation, lease.state["cookies"][0]["value"]), (1, "old"))
                status = self.manager.complete_login(newer["attempt"], {"cookies": [cookie("sid", "newer", PORTAL)],
                                                                        "origins": []})
                self.assertEqual(status["state"], "CONNECTED")  # the newer sign-in still commits
                self.assertEqual(self.manager.session_for("alpha").generation, 2)


class LoginViewTest(unittest.TestCase):
    """What a verifier can read, on a fake page: no browser."""

    class Page:
        def __init__(self, answer=None, error=None):
            self.url, self.answer, self.error, self.calls = f"https://{PORTAL}/home", answer, error, []

        def evaluate(self, script, args):
            self.calls.append(args)
            if self.error is not None:
                raise self.error
            return self.answer

    def view(self, *pages, context=True):
        browser = mock.Mock(spec=["http", "context"])
        browser.http = SessionHttp(ALPHA.all_hosts)
        browser.context = mock.Mock(pages=list(pages)) if context else None
        return LoginView(browser)

    def test_no_page_reads_nothing(self):
        for view in (self.view(), self.view(context=False)):
            self.assertEqual((view.url, view.text("p"), view.fetch(f"https://{PORTAL}/api/me")), ("", None, None))

    def test_the_shown_page_and_its_text(self):
        error = browser_module._playwright_error()("navigating")
        self.assertEqual(self.view(self.Page("home")).url, f"https://{PORTAL}/home")
        self.assertEqual(self.view(self.Page("home")).text("p"), "home")
        for page in (self.Page(5), self.Page(None), self.Page(error=error)):
            self.assertIsNone(self.view(page).text("p"))
        page = self.Page("x")
        self.view(page, self.Page("other")).text("h1")
        self.assertEqual(page.calls, [["h1", login_module.TEXT_LIMIT]])  # the first page only, bounded

    def test_fetch_reaches_only_the_source_and_keeps_its_bounds(self):
        page = self.Page({"status": 200, "body": "x" * (login_module.FETCH_LIMIT + 10)})
        view = self.view(page)
        for url in (f"https://{BETA_PORTAL}/api/me", "https://evil.example/api/me", f"http://{PORTAL}/api/me",
                    f"https://{PORTAL}:8443/api/me", "https://127.0.0.1/api/me"):
            self.assertIsNone(view.fetch(url), url)
        self.assertEqual(page.calls, [])  # refused before the page was asked
        answer = view.fetch(f"https://{PORTAL}/api/me")
        self.assertEqual((answer.status, len(answer.body)), (200, login_module.FETCH_LIMIT))
        self.assertEqual(page.calls, [[f"https://{PORTAL}/api/me", login_module.FETCH_LIMIT]])
        self.assertNotIn("xxx", repr(answer))
        for bad in (None, "200", {"status": "200", "body": ""}, {"status": 200, "body": 5}, {"status": 200}):
            self.assertIsNone(self.view(self.Page(bad)).fetch(f"https://{PORTAL}/api/me"), bad)
        self.assertIsNone(self.view(self.Page(error=browser_module._playwright_error()("x"))).fetch(
            f"https://{PORTAL}/api/me"))


class HeadedOnlyFromTheSignInTest(unittest.TestCase):
    """A hidden path has no way to a visible window; the window differs from a hidden run only by ``headless``."""

    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        self._root = TemporaryDirectory(dir=TEMP_PARENT, prefix="session-login-headed-")
        self.addCleanup(self._root.cleanup)
        self.root = Path(self._root.name)
        self.manager = AccountManager(self.root, CONFIG, vault=SessionVault(
            self.root, protector=FakeProtector(), acl=GoodAcl(FIRST)), clock=Clock())
        self.addCleanup(self.manager.close)

    def test_only_a_permit_of_the_same_source_opens_a_window(self):
        vault, control = self.manager.vault, ProcessControl()
        attempt = self.manager.begin_login("alpha")
        for flag in (True, 1, "headed", {"attempt": attempt}):
            with self.assertRaises(TypeError):
                SessionBrowser(ALPHA, SessionHttp(ALPHA.all_hosts), vault, control, headed=flag)
        with self.assertRaises(TypeError):
            HeadedPermit(object())
        with self.assertRaises(ValueError):
            SessionBrowser(BETA, SessionHttp(BETA.all_hosts), vault, control, headed=HeadedPermit(attempt))
        browser = visible_window(ALPHA, SessionHttp(ALPHA.all_hosts), vault, control, HeadedPermit(attempt), 1.0)
        self.assertEqual((browser.headed.attempt, browser.stop_at), (attempt, 1.0))
        self.assertIsNone(SessionBrowser(ALPHA, SessionHttp(ALPHA.all_hosts), vault, control).headed)
        self.assertNotIn(attempt.attempt_id, repr(HeadedPermit(attempt)))

    def test_a_hidden_run_cannot_ask_for_a_window(self):
        manager = mock.Mock()
        attempt = self.manager.begin_login("alpha")
        for value in (True, HeadedPermit(attempt)):
            with self.assertRaises(ValueError):
                run_with_session(manager, "alpha", lambda browser: None, control=ProcessControl(),
                                 browser_options={"headed": value})
        self.assertEqual(manager.mock_calls, [])  # refused before the session is read

    def test_only_the_coordinator_makes_a_permit(self):
        source = Path(browser_module.__file__).parent
        makers = sorted(path.relative_to(source).as_posix() for path in source.rglob("*.py")
                        if "HeadedPermit(" in path.read_text(encoding="utf-8"))
        self.assertEqual(makers, ["download_account_login.py"])

    def launched(self, **browser_options) -> dict:
        """Open and close a SessionBrowser on a fake Playwright runtime: what Edge would have been given."""
        seen: dict = {}

        class Chromium:
            @staticmethod
            def launch_persistent_context(profile, **kwargs):
                seen.update(profile=profile, kwargs=kwargs, prefs=json.loads(
                    (Path(profile) / "Default" / "Preferences").read_text(encoding="utf-8")))
                context = seen["context"] = mock.MagicMock()
                context.pages = []
                return context
        runtime = mock.Mock()
        runtime.chromium = Chromium()
        starter = mock.Mock()
        starter.return_value.start.return_value = runtime
        with mock.patch("playwright.sync_api.sync_playwright", starter), browser_tests.FAKE_DRIVER():
            with SessionBrowser(ALPHA, SessionHttp(ALPHA.all_hosts), self.manager.vault, ProcessControl(),
                                **browser_options) as browser:
                seen["proxy"] = browser.proxy.server
        seen["left"] = list(self.manager.vault.browser_folder().iterdir())
        return seen

    @unittest.skipUnless(HAVE_PLAYWRIGHT, "the installed Playwright is required (nothing is installed)")
    def test_the_window_keeps_every_protection_of_a_hidden_run(self):
        permit = HeadedPermit(self.manager.begin_login("alpha"))
        headed = self.launched(headed=permit, stop_at=time.monotonic() + 5)
        hidden = self.launched()
        for seen, headless in ((headed, False), (hidden, True)):
            kwargs, profile = seen["kwargs"], seen["profile"]
            self.assertIs(kwargs["headless"], headless)
            self.assertEqual(kwargs["channel"], "msedge")  # the installed Edge, never a personal profile
            self.assertTrue(profile.startswith(str(self.manager.vault.browser_folder())))
            self.assertIs(kwargs["chromium_sandbox"], True)
            self.assertEqual(kwargs["args"], list(HARDENING_ARGS))
            self.assertEqual(kwargs["proxy"], {"server": seen["proxy"], "bypass": "<-loopback>"})
            self.assertEqual((kwargs["service_workers"], kwargs["accept_downloads"]), ("block", False))
            self.assertEqual((kwargs["downloads_path"], kwargs["artifacts_dir"], kwargs["env"]["TEMP"]),
                             (profile, profile, profile))
            self.assertEqual(seen["prefs"], PROFILE_PREFERENCES)
            self.assertIs(seen["prefs"]["credentials_enable_service"], False)  # no offer to save a password
            self.assertIs(seen["prefs"]["profile"]["password_manager_enabled"], False)
            self.assertEqual(seen["prefs"]["autofill"], {"profile_enabled": False, "credit_card_enabled": False})
            context = seen["context"]
            context.route.assert_called_once()
            self.assertEqual(context.route.call_args.args[0], "**/*")  # every request through the handler
            context.route_web_socket.assert_called_once()
            context.add_init_script.assert_called_once_with(NO_WEBRTC_SCRIPT)
            self.assertEqual(seen["left"], [])  # the profile is deleted at the end
        self.assertEqual(sorted(headed["kwargs"]), sorted(hidden["kwargs"]))
        self.assertLessEqual(headed["kwargs"]["timeout"], 5000)  # the launch ends by the sign-in's deadline
        self.assertEqual(hidden["kwargs"]["timeout"], 30000)


@unittest.skipUnless(HAVE_BROWSER and HAVE_TLS, NEED_BROWSER)
class SignInFlowTest(BrowserCase):
    """The coordinator on real headless Edge (the stand-in for the window), with the fixture source."""

    def setUp(self):
        super().setUp()
        self.windows: list[SessionBrowser] = []
        self.permits: list[HeadedPermit] = []

    def stand_in(self, browser_class=SessionBrowser):
        def launch(source, http, vault, control, permit, stop_at):
            self.permits.append(permit)  # the permit is not passed on: headless, never a visible window
            self.windows.append(browser_class(source, http, vault, control, page_seconds=10, stop_at=stop_at))
            return self.windows[-1]
        return launch

    def coordinator(self, verifier, manager=None, browser_class=SessionBrowser, **options) -> LoginCoordinator:
        options.setdefault("poll_seconds", 0.2)
        coordinator = LoginCoordinator(manager or self.manager, verifiers={FIXTURE_ADAPTER: verifier},
                                       launcher=self.stand_in(browser_class), network=self.network, **options)
        self.addCleanup(coordinator.shutdown, 30)
        return coordinator

    def sign_in(self, coordinator: LoginCoordinator):
        outcome = coordinator.start("alpha").wait(RUN_SECONDS)
        self.assertIsNotNone(outcome)
        return outcome

    def serve_source(self, session_cookie: str = CANARY_SID):
        self.server.route("/login", page_reply(LOGIN_FORM))
        self.server.route("/session", redirect(302, "/home", **{
            "Set-Cookie": f"sid={session_cookie}; Path=/; Secure; HttpOnly; SameSite=Lax"}))
        self.server.route("/home", page_reply(HOME_PAGE))
        self.server.route("/api/ready", Reply(b"ok", content_type="text/plain"))
        self.server.route("/check", page_reply("<p>check</p>"))

        def me(seen, number):
            signed = cookies_of(seen).get("sid") == CANARY_SID and self.server.count("/api/ready") > 0
            return Reply(json.dumps({"signedIn": signed}).encode(), content_type="application/json")
        self.server.route("/api/me", me)

    def serve_plain_login(self):
        self.server.route("/login", page_reply("<p>login</p>", **{
            "Set-Cookie": f"sid={CANARY_SID}; Path=/; Secure; HttpOnly"}))

    def test_a_verified_sign_in_is_saved_and_reused_by_a_hidden_run(self):
        self.serve_source()
        verifier = FixtureVerifier(outside=True)
        coordinator = self.coordinator(verifier)
        run = coordinator.start("alpha")
        self.assertEqual(self.manager.status("alpha")["state"], "LOGGING_IN")
        outcome = run.wait(RUN_SECONDS)
        self.assertEqual(outcome.code, "CONNECTED")
        self.assert_no_profile_left()
        self.assertEqual(self.windows[0].refusals, [])
        self.assertGreaterEqual(verifier.calls, 2)  # asked on the login page first: no evidence there
        self.assertEqual(verifier.seen[0], (ALPHA.login_url, None))
        self.assertEqual(verifier.seen[-1], (f"https://{PORTAL}/home", "home"))
        self.assertEqual(set(verifier.foreign), {None})  # another source, a foreign host, http: never asked
        posted = self.server.seen("/session")
        self.assertEqual([item.body for item in posted], [f"pw={CANARY_PW}".encode()])  # typed to the source only
        status = self.manager.status("alpha")
        self.assertEqual((status["state"], status["authenticated_at"]), ("CONNECTED", T0.isoformat()))
        self.assertEqual(status["recheck_at"], (T0 + timedelta(seconds=3600)).isoformat())

        lease = self.manager.session_for("alpha")
        self.assertEqual(lease.generation, 1)
        self.assertEqual([(item["name"], item["value"]) for item in lease.state["cookies"]], [("sid", CANARY_SID)])
        origin = next(item for item in lease.state["origins"] if item["origin"] == f"https://{PORTAL}")
        self.assertEqual(origin["localStorage"], [{"name": "tok", "value": "t-ls"}])
        self.assertTrue(origin.get("indexedDB"))  # IndexedDB is kept, not dropped
        self.assertNotIn(CANARY_PW, json.dumps(lease.state))

        def reuse(browser):
            browser.navigate(f"https://{PORTAL}/check")
            return browser.page().evaluate(READ_STORAGE)
        self.clock.at(3599)  # within the TTL that counts from the sign-in
        self.assertEqual(self.manager.session_gate("alpha"), (True, None, 1))
        reused = run_with_session(self.manager, "alpha", reuse, control=self.control, network=self.network,
                                  browser_options={"page_seconds": 10})
        self.assertEqual(reused.value, {"ls": "t-ls", "idb": "t-idb"})
        self.assertIsNone(reused.saved)  # nothing changed: nothing saved, the TTL is not extended
        self.assertEqual(cookies_of(self.server.seen("/check")[0]), {"sid": CANARY_SID})
        self.assertEqual(self.manager.status("alpha")["authenticated_at"], T0.isoformat())
        late_runs: list = []
        for seconds in (3600, 3601):  # from the mark on, a sign-in is needed and no hidden run gets the session
            with self.subTest(seconds=seconds):
                self.clock.at(seconds)
                status = self.manager.status("alpha")
                self.assertEqual((status["state"], status["error_code"]), ("NEEDS_LOGIN", "SESSION_EXPIRED"))
                self.assertEqual(self.manager.session_gate("alpha"), (False, "SESSION_EXPIRED", 1))
                with self.assertRaises(LoginRequired) as caught:
                    run_with_session(self.manager, "alpha", late_runs.append, control=self.control,
                                     network=self.network, browser_options={"page_seconds": 10})
                self.assertEqual(caught.exception.code, "SESSION_EXPIRED")
        self.assertEqual(late_runs, [])  # refused before any browser started
        self.assertEqual(self.server.count("/check"), 1)
        self.assert_no_profile_left()

        self.assertEqual(self.hosts_seen(), {PORTAL})  # the M2a network guard: one checked path only
        self.assertEqual(set(self.resolver.calls), {PORTAL})
        self.assertEqual({address for address, _ in self.server.connections}, {PUBLIC_ADDRESS})
        for text in (repr(outcome), outcome.message, repr(run.outcome), repr(self.permits),
                     json.dumps(self.manager.statuses()), repr(self.windows[0].refusals)):
            self.assertNotIn(CANARY_PW, text)
            self.assertNotIn(CANARY_SID, text)
        self.assert_no_secret_on_disk(CANARY_PW, CANARY_SID)

    def test_a_cookie_a_new_url_and_a_200_are_no_evidence_and_the_old_session_stays(self):
        self.connect(self.manager, {"cookies": [cookie("sid", "old", PORTAL)], "origins": []})
        self.serve_source(session_cookie="not-signed-in")
        verifier = FixtureVerifier()
        started = time.monotonic()
        outcome = self.sign_in(self.coordinator(verifier, max_seconds=6))
        elapsed = time.monotonic() - started
        self.assertEqual(outcome.code, "LOGIN_TIMEOUT")
        self.assertLess(elapsed, 6 + 5)
        self.assertGreaterEqual(self.server.count("/home"), 1)  # it got a cookie, a new URL and a 200
        self.assertGreaterEqual(verifier.calls, 3)
        status = self.manager.status("alpha")
        self.assertEqual((status["state"], status["error_code"]), ("CONNECTED", "LOGIN_TIMEOUT"))
        lease = self.manager.session_for("alpha")
        self.assertEqual((lease.generation, lease.state["cookies"][0]["value"]), (1, "old"))
        self.assert_no_profile_left()

    def test_the_deadline_cuts_a_request_in_flight(self):
        self.server.route("/login", Reply(b"x" * 600, content_type="text/html", chunk=10, delay=0.5))  # 30 s
        started = time.monotonic()
        outcome = self.sign_in(self.coordinator(FixtureVerifier(), max_seconds=5))
        self.assertEqual(outcome.code, "LOGIN_TIMEOUT")
        self.assertLess(time.monotonic() - started, 5 + 5)
        self.assertEqual(self.server.count("/login"), 1)
        self.assertEqual(self.manager.status("alpha")["error_code"], "LOGIN_TIMEOUT")
        self.assert_no_profile_left()

    def test_a_window_stuck_in_a_page_script_is_killed(self):
        self.server.route("/login", page_reply("<p>login</p><script>setTimeout(() => { for (;;) {} }, 200)</script>"))
        for name, options, code in (("deadline", {"max_seconds": 4}, "LOGIN_TIMEOUT"), ("cancel", {}, "LOGIN_CANCELLED")):
            with self.subTest(name):
                verifier = StuckVerifier()
                coordinator = self.coordinator(verifier, grace_seconds=1, **options)
                started = time.monotonic()
                run = coordinator.start("alpha")
                self.assertTrue(verifier.entered.wait(RUN_SECONDS))
                if name == "cancel":
                    coordinator.cancel("alpha")
                self.assertEqual(run.wait(RUN_SECONDS).code, code)
                self.assertLess(time.monotonic() - started, 4 + 1 + 20)
                self.assertGreater(verifier.stuck, 0.8)  # the page call came back only when Edge was killed
                self.assertEqual(self.manager.status("alpha")["error_code"], code)
                self.assert_no_profile_left()
        self.assertEqual(browser_tests.edge_command_lines(str(self.root)), [])  # no Edge of these runs is left

    def test_closing_the_window_ends_the_sign_in_unsaved(self):
        self.serve_plain_login()

        class ClosesPages(SessionBrowser):
            def pause(self, seconds):
                for page in list(self.context.pages):
                    page.close()
                return super().pause(seconds)

        class ClosesBrowser(SessionBrowser):
            def pause(self, seconds):
                self.context.close()
                return super().pause(seconds)
        for browser_class in (ClosesPages, ClosesBrowser):
            with self.subTest(browser_class.__name__):
                outcome = self.sign_in(self.coordinator(Answers(), browser_class=browser_class))
                self.assertEqual(outcome.code, "LOGIN_WINDOW_CLOSED")
                status = self.manager.status("alpha")
                self.assertEqual((status["state"], status["error_code"]), ("NOT_CONNECTED", "LOGIN_WINDOW_CLOSED"))
                self.assertEqual(stored_sessions(self.manager), [])
                self.assert_no_profile_left()

    def test_a_cancel_stops_the_window_and_its_late_success_is_never_saved(self):
        self.serve_plain_login()
        verifier = GatedVerifier()
        coordinator = self.coordinator(verifier)
        run = coordinator.start("alpha")
        self.assertTrue(verifier.entered.wait(RUN_SECONDS))
        asked = time.monotonic()  # the window is open: the manager answers at once (no lock is held)
        self.assertEqual(self.manager.status("alpha")["state"], "LOGGING_IN")
        self.assertEqual([item["state"] for item in self.manager.statuses()], ["LOGGING_IN", "NOT_CONNECTED"])
        self.assertLess(time.monotonic() - asked, 2)
        self.assertTrue(coordinator.cancel("alpha"))
        verifier.release.set()
        self.assertEqual(run.wait(RUN_SECONDS).code, "LOGIN_CANCELLED")
        status = self.manager.status("alpha")
        self.assertEqual((status["state"], status["error_code"]), ("NOT_CONNECTED", "LOGIN_CANCELLED"))
        self.assertEqual(stored_sessions(self.manager), [])
        with self.assertRaises(StaleLogin):  # the manager refuses the late result too
            self.manager.complete_login(self.permits[0].attempt, {"cookies": [], "origins": []})
        self.assert_no_profile_left()

    def saving_with(self, hook) -> type[SessionBrowser]:
        class Interleaved(SessionBrowser):
            def storage_state(self):  # after the evidence, just before the manager is asked to save
                state = super().storage_state()
                hook()
                return state
        return Interleaved

    def test_a_disconnect_or_a_newer_sign_in_during_the_save_wins(self):
        self.serve_plain_login()
        newer = {}

        def replace():
            self.manager.cancel_login("alpha")
            newer["attempt"] = self.manager.begin_login("alpha")
        for name, hook, state in (("disconnect", lambda: self.manager.disconnect("alpha"), "NOT_CONNECTED"),
                                  ("newer sign-in", replace, "LOGGING_IN")):
            with self.subTest(name):
                outcome = self.sign_in(self.coordinator(Answers(True), browser_class=self.saving_with(hook)))
                self.assertEqual(outcome.code, "LOGIN_STALE")
                self.assertEqual(self.manager.status("alpha")["state"], state)
                self.assertEqual(stored_sessions(self.manager), [])
                self.assert_no_profile_left()
        self.assertTrue(self.manager.attempt_is_current(newer["attempt"]))  # the old window never ended it

    def test_a_stop_while_the_state_is_read_keeps_the_old_session(self):
        self.serve_plain_login()
        for name, code, options in (("deadline", "LOGIN_TIMEOUT", {"max_seconds": 8}),
                                    ("shutdown", "LOGIN_CANCELLED", {})):
            with self.subTest(name):
                root = TemporaryDirectory(dir=TEMP_PARENT, prefix="session-login-stop-")
                self.addCleanup(root.cleanup)
                manager = self.new_manager(Path(root.name))
                self.connect(manager, {"cookies": [cookie("sid", "old", PORTAL)], "origins": []})
                holder: dict = {}

                def during(name=name, holder=holder):  # the real state was just read from Edge
                    coordinator = holder["coordinator"]
                    if name == "deadline":
                        coordinator._runs["alpha"].control.wait(15)
                    else:
                        coordinator.shutdown(timeout=0)
                coordinator = holder["coordinator"] = self.coordinator(
                    Answers(True), manager=manager, browser_class=self.saving_with(during), **options)
                outcome = coordinator.start("alpha").wait(RUN_SECONDS)
                self.assertEqual(outcome.code, code)
                status = manager.status("alpha")
                self.assertEqual((status["state"], status["error_code"], status["authenticated_at"]),
                                 ("CONNECTED", code, T0.isoformat()))
                lease = manager.session_for("alpha")
                self.assertEqual((lease.generation, lease.state["cookies"][0]["value"]), (1, "old"))
                self.assertEqual(stored_sessions(manager), ["session-1.bin"])
                self.assert_no_profile_left(manager)

    def test_a_result_of_another_manager_is_refused(self):
        self.serve_plain_login()
        other_root = TemporaryDirectory(dir=TEMP_PARENT, prefix="session-login-other-")
        self.addCleanup(other_root.cleanup)
        other = self.new_manager(Path(other_root.name))
        theirs = {}

        def interleave():
            theirs.setdefault("attempt", other.begin_login("alpha"))
        outcome = self.sign_in(self.coordinator(Answers(True), browser_class=self.saving_with(interleave)))
        self.assertEqual(outcome.code, "CONNECTED")  # saved by its own manager only
        mine = self.permits[0].attempt
        state = {"cookies": [cookie("sid", "x", PORTAL)], "origins": []}
        with self.assertRaises(StaleLogin):
            other.complete_login(mine, state)
        with self.assertRaises(StaleLogin):
            self.manager.complete_login(theirs["attempt"], state)
        self.assertEqual(other.status("alpha")["state"], "LOGGING_IN")
        self.assertEqual(stored_sessions(other), [])
        self.assertEqual(self.manager.session_for("alpha").state["cookies"][0]["value"], CANARY_SID)

    def test_a_save_error_keeps_the_old_session(self):
        self.connect(self.manager, {"cookies": [cookie("sid", "old", PORTAL)], "origins": []})
        self.serve_plain_login()
        self.manager.vault.protector.fail_protect = True
        outcome = self.sign_in(self.coordinator(Answers(True)))
        self.assertEqual(outcome.code, "SESSION_SAVE_FAILED")
        status = self.manager.status("alpha")
        self.assertEqual((status["state"], status["error_code"]), ("CONNECTED", "SESSION_SAVE_FAILED"))
        lease = self.manager.session_for("alpha")
        self.assertEqual((lease.generation, lease.state["cookies"][0]["value"]), (1, "old"))
        self.assertEqual(stored_sessions(self.manager), ["session-1.bin"])
        self.assert_no_profile_left()

    def test_edge_keeps_the_password_and_autofill_preferences(self):
        self.server.route("/page", page_reply('<form><input type="password" name="pw"></form>'))

        class KeepsPreferences(SessionBrowser):
            kept: dict = {}

            def close(self):
                profile = self._profile
                if self.context is not None:
                    self.context.close()  # Edge writes its preferences back as it closes
                if profile is not None:
                    type(self).kept = json.loads((profile / "Default" / "Preferences").read_text(encoding="utf-8"))
                super().close()
        with self.browser(browser_class=KeepsPreferences) as browser:
            browser.navigate(f"https://{PORTAL}/page")
        kept = KeepsPreferences.kept
        self.assertIs(kept["credentials_enable_service"], False)  # no offer to save a password
        self.assertIs(kept["credentials_enable_autosignin"], False)
        self.assertIs(kept["profile"]["password_manager_enabled"], False)
        self.assertIs(kept["autofill"]["profile_enabled"], False)  # no addresses filled in
        self.assertIs(kept["autofill"]["credit_card_enabled"], False)
        self.assertEqual(kept["webrtc"], PROFILE_PREFERENCES["webrtc"])
        self.assert_no_profile_left()


if __name__ == "__main__":
    unittest.main()
