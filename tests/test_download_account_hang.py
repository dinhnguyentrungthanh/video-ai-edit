"""A hidden run or a sign-in whose Playwright driver stops answering after its Edge is gone (plan 9.13): real
headless Edge on the fixture server of the browser tests, no network, temporary roots under the install's temp/.

How the hang is made: the run's own driver (the test's own child process, recorded by the browser) is
suspended at a chosen point, so every call waits for it as a driver that never saw its Edge go would: while
the browser opens (before and after Edge starts), during the action, while the state is read, while the
context closes and while the runtime stops. The run must still end, as RunTimedOut or Cancelled (LOGIN_TIMEOUT
or LOGIN_CANCELLED for a sign-in), within about the deadline + 2 × ``grace_seconds`` plus the kills and the
cleanup: its Edge is killed one grace after the stop, its driver one grace after that. Every run here goes
through ``bounded``, an outside watchdog: a run still going after LIMIT seconds is freed by the test (its own
driver, by the recorded handle) and the test fails, so a regression never hangs the suite.

Two time budgets. Edge's real start and close (a few seconds, tens of seconds on a busy machine) never count
against the short deadline that ends a hang: a run made to hang gets its short deadline RUN counted from the
moment its driver stopped answering. That stop is the run's own: the same ``_stop("timeout")`` its deadline
timer calls, armed among its own timers so its close disarms it (``_stop_run`` and ``_kill_if_hung`` of the
coordinator for a sign-in, as ``_sign_in`` arms them). The run's own deadline from its start, NORMAL_RUN, only
backs it up. One case keeps the production timing, a deadline from the run's start: a driver that stops before
Edge starts. A run that must end by itself gets NORMAL_RUN, and its deadline timer must be disarmed once it
ended, so nothing fires later.
"""
from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest import mock

import psutil

from biliflow import download_account_runs as runs
from biliflow.download_account_browser import SessionBrowser
from biliflow.download_account_edge import edge_processes
from biliflow.download_account_login import LoginCoordinator
from biliflow.download_account_runs import RunTimedOut, run_with_session
from biliflow.download_http import Cancelled
from biliflow.download_runner import ProcessControl
from tests import test_download_account_browser as browser_tests
from tests.test_download_account_browser import BETA_PORTAL, PORTAL, BrowserCase, cookie, page_reply
from tests.test_download_account_login import FIXTURE_ADAPTER, Answers

RUN, GRACE = 8.0, 1.0  # the short deadline that ends a hang, counted from the stop of the driver
NORMAL_RUN = 45.0  # a run's own deadline from its start: Edge's real start and close, below the watchdog LIMIT
BOUND = RUN + 2 * GRACE + 8  # from that stop: the deadline and both graces, then the kills and the cleanup
LIMIT = 60.0  # the outside watchdog: a run still going then is a failure, never a hung suite


def setUpModule():
    browser_tests.setUpModule()


def tearDownModule():
    browser_tests.tearDownModule()


class _ThenStick:
    """The context, whose close sticks the driver once it returned: the runtime's stop then waits for it."""

    def __init__(self, context: Any, stick: Callable[[], None]):
        self._context, self._stick = context, stick

    def close(self) -> None:
        self._context.close()
        self._stick()


def stuck_browsers(at: str | None, made: list, stuck: threading.Event,
                   on_stick: Callable[[Any], None] | None = None) -> type[SessionBrowser]:
    """A session browser whose own driver stops answering at ``at``: "open" (before Edge starts),
    "after-launch" (Edge started, its guards are being set), "storage_state", "close" (after the state was
    read: the context's close waits), "close-runtime" (the context closed: the runtime's stop waits), "pause"
    (a sign-in window waiting for the user) or None (only when the run's action calls ``stick``). Every
    browser made is kept in ``made``; ``on_stick(browser)`` is called once its driver no longer answers."""

    class StuckDriverBrowser(SessionBrowser):
        def __init__(self, *args: Any, **options: Any):
            super().__init__(*args, **options)
            self.profile_path: str | None = None
            self.stuck = False
            self.stuck_at: float | None = None
            made.append(self)

        def stick(self) -> None:
            if not self.stuck:  # this run's own recorded driver only
                self.stuck = True
                psutil.Process(self.driver.pid).suspend()
                self.stuck_at = time.monotonic()
                stuck.set()
                if on_stick is not None:
                    on_stick(self)

        def _launch(self, profile: str) -> Any:
            self.profile_path = profile
            if at == "open":
                self.stick()
            context = super()._launch(profile)
            if at == "after-launch":
                self.stick()
            return context

        def storage_state(self) -> dict[str, Any]:
            if at == "storage_state":
                self.stick()
            state = super().storage_state()
            if at == "close":
                self.stick()
            return state

        def pause(self, seconds: float) -> bool:
            if at == "pause":
                self.stick()
            return super().pause(seconds)

        def close(self) -> None:
            if at == "close-runtime" and self.context is not None:
                self.context = _ThenStick(self.context, self.stick)
            super().close()

    return StuckDriverBrowser


RUN_STOP = runs._RunStop  # the real class: a run started while another one's recorder is patched in subclasses it


def recorded_stops(made: list) -> type:
    """The run's own ``_RunStop``, unchanged, kept in ``made`` so a test can read its control and its timers."""

    class RecordedStop(RUN_STOP):
        def __init__(self, *args: Any, **options: Any):
            super().__init__(*args, **options)
            made.append(self)

    return RecordedStop


class HangCase(BrowserCase):
    def setUp(self):
        super().setUp()
        self.made: list[SessionBrowser] = []
        self.stops: list[Any] = []
        self.stuck = threading.Event()
        self.addCleanup(self.release)

    def release(self) -> None:
        """Whatever a failed test left: the test's own drivers (resumed, then ended by their handle) and Edge."""
        for browser in self.made:
            driver = browser.driver
            if driver is not None and driver.owned() is not None:
                try:
                    psutil.Process(driver.pid).resume()
                except psutil.Error:
                    pass
                driver.end()
            browser.force_close()

    def bounded(self, work: Callable[[], Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}

        def target() -> None:
            try:
                result["value"] = work()
            except BaseException as error:  # noqa: BLE001 - the test reads it
                result["error"] = error
        thread = threading.Thread(target=target, daemon=True)
        started = time.monotonic()
        thread.start()
        thread.join(LIMIT)
        if thread.is_alive():
            self.release()
            thread.join(LIMIT)
            self.fail(f"the run was still blocked after {LIMIT:g} s")
        result["started"], result["ended"] = started, time.monotonic()
        result["seconds"] = result["ended"] - started
        return result

    def cancel_once_stuck(self, cancel: Callable[[], Any]) -> None:
        """The user's stop, once the driver has stopped answering."""
        threading.Thread(target=lambda: self.stuck.wait(LIMIT) and (time.sleep(0.3), cancel()),
                         daemon=True).start()

    def assert_hang(self, hang: Any, phase: str) -> None:
        """The report: the phase, the call that waited (Playwright's own frame) and BiliFlow's frames only."""
        self.assertIsNotNone(hang)
        self.assertEqual(hang.phase, phase)
        self.assertTrue(hang.stack and hang.stack[0].startswith("playwright."), hang.stack)
        self.assertTrue(any(frame.startswith("biliflow.download_account_") for frame in hang.stack), hang.stack)
        for frame in hang.stack:
            self.assertRegex(frame, r"^[\w.]+:[\w<>]+:\d+$")  # module:function:line, nothing else

    def since_stick(self, result: dict[str, Any]) -> float:
        """How long the run went on after its driver stopped answering (the last browser made is its own)."""
        return result["ended"] - self.made[-1].stuck_at

    def assert_cleaned(self) -> None:
        """This run's own driver and Edge are gone, its profile too."""
        browser = self.made[-1]
        driver = browser.driver
        self.assertIsNone(driver.owned())
        self.assertFalse(psutil.pid_exists(driver.pid) and psutil.Process(driver.pid).create_time() == driver.created)
        self.assertEqual(edge_processes(Path(browser.profile_path)), [])
        self.assert_no_profile_left()


class HungDriverTest(HangCase):
    def setUp(self):
        super().setUp()
        self.page = f"https://{PORTAL}/page"
        self.server.route("/page", page_reply("<p>page</p>"))
        self.connect(self.manager, {"cookies": [cookie("sid", "s1", PORTAL)], "origins": []})

    def run_stuck(self, at: str | None, action: Callable[[Any], Any] | None = None, *, cancel: bool = False,
                  source_id: str = "alpha", run_seconds: float = NORMAL_RUN,
                  stick_deadline: float | None = None) -> dict[str, Any]:
        """One run of ``source_id`` whose driver stops answering at ``at``. ``run_seconds``: the run's own
        deadline from its start; ``stick_deadline``: seconds after the driver stopped answering at which the
        run's own stop fires as its deadline would (None: only the deadline from its start)."""
        caller = ProcessControl()
        if cancel:
            self.cancel_once_stuck(lambda: caller.request("cancel"))
        action = action or (lambda browser: browser.navigate(self.page).status)
        on_stick = None if stick_deadline is None else (lambda browser: self.deadline_from(browser, stick_deadline))
        with mock.patch.object(runs, "SessionBrowser", stuck_browsers(at, self.made, self.stuck, on_stick)), \
                mock.patch.object(runs, "_RunStop", recorded_stops(self.stops)):
            return self.bounded(lambda: run_with_session(
                self.manager, source_id, action, control=caller, network=self.network,
                browser_options={"page_seconds": 10}, run_seconds=run_seconds, grace_seconds=GRACE))

    def deadline_from(self, browser: Any, seconds: float) -> None:
        """Arm the stuck run's own deadline ``seconds`` from now: the stop its deadline timer calls, among its
        own timers (its close disarms it), on the run of this browser only."""
        stop, = [item for item in self.stops if item.browser is browser]
        stop._later(seconds, stop._stop, "timeout")

    def stuck_action(self, browser: Any) -> int:
        browser.stick()
        return browser.navigate(self.page).status  # waits for the driver that no longer answers

    def assert_ended(self, result: dict[str, Any], kind: type, phase: str, *, from_start: bool = False) -> None:
        """``from_start``: the run's deadline counted from its start (else from the stop of its driver)."""
        error = result.get("error")
        self.assertIsInstance(error, kind, result)
        self.assertTrue(self.stuck.is_set())  # the driver was stuck before the stop came
        took = result["seconds"] if from_start else self.since_stick(result)
        self.assertLess(took, BOUND, result)
        if kind is RunTimedOut:  # neither the deadline nor the kill of Edge alone freed it: the driver's end did
            self.assertGreater(took, RUN + 2 * GRACE - 0.5, result)
        self.assert_hang(error.hang, phase)
        self.assert_cleaned()
        self.assertEqual(self.manager.status("alpha")["state"], "CONNECTED")  # the session stays as it was

    def test_a_driver_that_stops_answering_while_the_browser_opens(self):
        with self.subTest(at="open"):  # before Edge starts: the production timing, a deadline from the start
            self.assert_ended(self.run_stuck("open", run_seconds=RUN), RunTimedOut, "open", from_start=True)
        with self.subTest(at="after-launch"):
            self.stuck.clear()
            self.assert_ended(self.run_stuck("after-launch", stick_deadline=RUN), RunTimedOut, "open")

    def test_a_driver_that_stops_answering_during_the_action(self):
        self.assert_ended(self.run_stuck(None, self.stuck_action, stick_deadline=RUN), RunTimedOut, "page")

    def test_a_driver_that_stops_answering_while_the_state_is_read(self):
        self.assert_ended(self.run_stuck("storage_state", stick_deadline=RUN), RunTimedOut, "storage_state")

    def test_a_driver_that_stops_answering_while_the_browser_closes(self):
        for at, phase in (("close", "close-context"), ("close-runtime", "close-runtime")):
            with self.subTest(phase=phase):
                self.stuck.clear()
                self.assert_ended(self.run_stuck(at, stick_deadline=RUN), RunTimedOut, phase)

    def test_a_cancel_ends_a_hung_driver_too(self):
        for at, action, phase in ((None, self.stuck_action, "page"), ("close", None, "close-context")):
            with self.subTest(phase=phase):
                self.stuck.clear()
                self.assert_ended(self.run_stuck(at, action, cancel=True, run_seconds=60), Cancelled, phase)

    def test_a_run_that_ends_in_time_never_has_its_driver_ended(self):
        result = self.run_stuck(None)
        self.assertIn("value", result, result)  # it ended by itself, not by its deadline or the watchdog
        self.assertEqual(result["value"].value, 200)
        stop, = self.stops
        self.assertFalse(stop.control.requested)  # neither its deadline nor a cancel stopped it
        deadline, = stop._timers  # only the deadline was ever armed: never the end of Edge or of the driver
        self.assertTrue(deadline.finished.is_set())  # disarmed once the run ended
        deadline.join(LIMIT)
        self.assertFalse(deadline.is_alive())
        time.sleep(2 * GRACE + 0.5)  # nothing armed fires later
        self.assertFalse(stop.control.requested)
        self.assertEqual(len(stop._timers), 1)
        self.assertIsNone(self.made[-1].hang)
        self.assertIsNone(self.made[-1].driver.owned())  # its driver ended by itself, with the runtime's stop

    def test_two_sources_at_once_only_the_hung_runs_driver_is_ended_and_both_go_on(self):
        self.manager.complete_login(self.manager.begin_login("beta"),
                                    {"cookies": [cookie("bsid", "b1", BETA_PORTAL)], "origins": []})
        beta_page = f"https://{BETA_PORTAL}/page"
        beta_ready, alpha_done, seen = threading.Event(), threading.Event(), {}

        def beta_action(browser: Any) -> int:
            browser.navigate(beta_page)
            seen["driver"] = browser.driver
            beta_ready.set()
            if not alpha_done.wait(LIMIT):
                raise AssertionError("the alpha run never ended")
            seen["alive"] = browser.driver.owned() is not None
            return browser.navigate(beta_page).status  # its own driver still answers

        beta: dict[str, Any] = {}
        beta_thread = threading.Thread(target=lambda: beta.update(self.run_stuck(None, beta_action, source_id="beta",
                                                                                  run_seconds=60)), daemon=True)
        beta_thread.start()
        self.assertTrue(beta_ready.wait(LIMIT))
        alpha = self.run_stuck(None, self.stuck_action, stick_deadline=RUN)
        alpha_done.set()
        beta_thread.join(LIMIT)
        self.assertFalse(beta_thread.is_alive())
        self.assertIsInstance(alpha.get("error"), RunTimedOut, alpha)
        hung = [item for item in self.made if item.hang is not None]
        self.assertEqual(len(hung), 1)  # the hung run's driver, and no other
        self.assertNotEqual(hung[0].driver.pid, seen["driver"].pid)
        self.assertEqual(hung[0].hang.phase, "page")  # ended by its own deadline while its action waited
        self.assertLess(alpha["ended"] - hung[0].stuck_at, BOUND, alpha)
        self.assertTrue(seen["alive"])  # the other source's driver was never touched
        self.assertIn("value", beta, beta)
        self.assertEqual(beta["value"].value, 200)
        again = self.run_stuck(None)  # the source's lock was let go: its next run goes on
        self.assertIn("value", again, again)
        self.assertEqual(again["value"].value, 200)


class HungSignInTest(HangCase):
    """The sign-in coordinator shares the browser's lifecycle: a window (the headless stand-in) whose driver
    stops answering while it waits for the user ends as timed out or cancelled, never as failed. No real
    sign-in: the fixture source's login page and a verifier that never sees evidence."""

    def setUp(self):
        super().setUp()
        self.server.route("/login", page_reply("<p>login</p>"))

    def sign_in(self, cancel: bool) -> dict[str, Any]:
        def deadline_from_stick(_browser: Any) -> None:  # what _sign_in arms for its deadline, RUN from now
            run = coordinator._runs["alpha"]
            coordinator._later(run, RUN, lambda item: coordinator._stop_run(item, "timeout"))
            coordinator._kill_if_hung(run, RUN + GRACE)
        browsers = stuck_browsers("pause", self.made, self.stuck, None if cancel else deadline_from_stick)

        def launch(source, http, vault, control, permit, stop_at):  # the permit is not passed on: headless
            return browsers(source, http, vault, control, page_seconds=10, stop_at=stop_at)
        options = {} if cancel else {"max_seconds": NORMAL_RUN}
        coordinator = LoginCoordinator(self.manager, verifiers={FIXTURE_ADAPTER: Answers()}, launcher=launch,
                                       network=self.network, poll_seconds=0.2, grace_seconds=GRACE, **options)
        self.addCleanup(coordinator.shutdown, 30)
        if cancel:
            self.cancel_once_stuck(lambda: coordinator.cancel("alpha"))
        return self.bounded(lambda: coordinator.start("alpha").wait(LIMIT))

    def test_a_window_whose_driver_stops_answering_ends_as_timed_out_or_cancelled(self):
        for cancel, code in ((False, "LOGIN_TIMEOUT"), (True, "LOGIN_CANCELLED")):
            with self.subTest(code=code):
                self.stuck.clear()
                result = self.sign_in(cancel)
                outcome = result.get("value")
                self.assertIsNotNone(outcome, result)
                self.assertEqual((outcome.code, outcome.failure), (code, None))
                self.assertTrue(self.stuck.is_set())
                self.assertLess(self.since_stick(result), BOUND, result)
                self.assert_hang(outcome.hang, "page")
                self.assert_cleaned()
                self.assertEqual(self.manager.status("alpha")["state"], "NOT_CONNECTED")  # nothing was saved


if __name__ == "__main__":
    unittest.main()
