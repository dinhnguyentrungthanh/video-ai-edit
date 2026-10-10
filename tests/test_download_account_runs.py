"""When a hidden run (download_account_runs.run_with_session) may give its value, with a fake session browser:
no Edge, no network.

A run stopped by the caller's cancel or past its deadline gives no value (Cancelled or RunTimedOut), whether
the stop came during its action, while its state was read or while its browser closed, and whether or not the
run's timer had fired yet. The cookies it rotated are still saved under its lease, without making the sign-in
newer. The real browser's runs (rotation, deadline, a hung Edge) are in tests/test_download_account_browser.py
and tests/test_download_account_sources.py. Made-up cookies and .example hosts only, in temporary roots under
the install's temp/.
"""
from __future__ import annotations

import inspect
import threading
import time
import unittest
from typing import Any, Callable
from unittest import mock

from biliflow import download_account_runs as runs
from biliflow.download_account_driver import DriverHang
from biliflow.download_account_edge import BrowserFailed, failure_kind
from biliflow.download_account_runs import RunTimedOut, SessionRun, run_with_session
from biliflow.download_account_sources import _failure_words, _log_stats
from biliflow.download_http import Cancelled
from biliflow.download_runner import ProcessControl
from tests.test_download_account_browser import PORTAL, cookie
from tests.test_download_account_login import CoordinatorCase

SIGNED_IN = {"cookies": [cookie("sid", "first", PORTAL)], "origins": []}
ROTATED = {"cookies": [cookie("sid", "rotated", PORTAL)], "origins": []}
STOP_WAIT = 5.0  # the most a fake step waits for the run's stop (it comes after 0.2 s)
LOCK_WAIT = 30.0  # a bounded wait for the source's lock: a lock kept by mistake fails the test, never hangs it

Step = Callable[[Any], None]


class HiddenRunStopTest(CoordinatorCase):
    def setUp(self):
        super().setUp()
        self.manager.complete_login(self.manager.begin_login("alpha"), SIGNED_IN)
        self.caller = ProcessControl()
        self.before = self.manager.status("alpha")
        self.browsers = 0

    def run_fake(self, action: Callable[[Any], Any] = lambda browser: "value", *, read: Step | None = None,
                 close: Step | None = None, run_seconds: float = 0.2,
                 lock_seconds: float = runs.LOCK_WAIT_SECONDS):
        """``run_with_session`` with a fake browser: ``action``, then ``read`` while its state is read (it
        gives ROTATED), then ``close`` while it closes. ``self.browsers`` counts the browsers it made."""
        class Browser:
            def __init__(browser, source, http, vault, control, **options):
                self.browsers += 1
                browser.control, browser.stop_at, browser.profile_left = control, options["stop_at"], False

            def __enter__(browser):
                return browser

            def __exit__(browser, *exc):
                if close is not None:
                    close(browser)

            def storage_state(browser):
                if read is not None:
                    read(browser)
                return ROTATED

            def force_close(browser):
                return 0

        with mock.patch.object(runs, "SessionBrowser", Browser):
            return run_with_session(self.manager, "alpha", action, control=self.caller, run_seconds=run_seconds,
                                    grace_seconds=1, lock_seconds=lock_seconds)

    def wait_for_stop(self, browser) -> None:
        self.assertTrue(browser.control.wait(STOP_WAIT), "the run's own control was never stopped")

    def cancel(self, browser) -> None:
        self.caller.request("cancel")

    def assert_rotation_kept(self) -> None:
        """The rotated cookie is the session's now, and the sign-in is not any newer."""
        self.assertEqual([item["value"] for item in self.manager.session_for("alpha").state["cookies"]], ["rotated"])
        after = self.manager.status("alpha")
        self.assertEqual((after["authenticated_at"], after["recheck_at"], after["state"]),
                         (self.before["authenticated_at"], self.before["recheck_at"], "CONNECTED"))

    def test_a_run_that_ends_in_time_gives_its_value_and_saves_its_rotated_cookies(self):
        run = self.run_fake(run_seconds=5)
        self.assertEqual((run.value, run.saved), ("value", True))
        self.assert_rotation_kept()

    def test_a_deadline_during_the_action_gives_no_value(self):
        def returns_late(browser):
            self.wait_for_stop(browser)
            return "late"

        def raises_late(browser):
            self.wait_for_stop(browser)
            raise Cancelled()  # what check_running raises once the run's own control was stopped
        for action in (returns_late, raises_late):
            with self.subTest(action=action.__name__), self.assertRaises(RunTimedOut):
                self.run_fake(action)
        self.assert_rotation_kept()

    def test_a_caller_cancel_during_the_action_gives_no_value(self):
        ran: list[str] = []

        def returns(browser):
            ran.append("returns")
            self.cancel(browser)
            return "late"

        def raises(browser):
            ran.append("raises")
            self.cancel(browser)
            raise Cancelled()
        for action in (returns, raises):
            self.caller = ProcessControl()  # a fresh caller: a cancelled one is refused before the run starts
            with self.subTest(action=action.__name__), self.assertRaises(Cancelled):
                self.run_fake(action, run_seconds=5)
        self.assertEqual(ran, ["returns", "raises"])  # both actions ran: the cancel came during each of them
        self.assert_rotation_kept()

    def test_a_deadline_while_the_state_is_read_gives_no_value_but_keeps_the_rotated_cookies(self):
        with self.assertRaises(RunTimedOut) as caught:
            self.run_fake(read=self.wait_for_stop)
        self.assertEqual((caught.exception.code, caught.exception.retryable), ("NETWORK", True))
        self.assert_rotation_kept()

    def test_a_caller_cancel_while_the_state_is_read_gives_no_value_but_keeps_the_rotated_cookies(self):
        with self.assertRaises(Cancelled):
            self.run_fake(read=self.cancel, run_seconds=5)
        self.assert_rotation_kept()

    def test_a_stop_while_the_browser_closes_gives_no_value(self):
        closed: list[str] = []

        def late(browser):
            closed.append("late")
            self.wait_for_stop(browser)

        def cancelled(browser):
            closed.append("cancelled")
            self.cancel(browser)
        with self.assertRaises(RunTimedOut):
            self.run_fake(close=late)
        with self.assertRaises(Cancelled):
            self.run_fake(close=cancelled, run_seconds=5)
        self.assertEqual(closed, ["late", "cancelled"])
        self.assert_rotation_kept()

    def test_a_state_that_cannot_be_read_after_the_action_keeps_the_value_and_the_saved_session(self):
        # A ticket the action got is never thrown away because Playwright could not read the browser's state
        # afterwards (it reopens closed origins to read their IndexedDB); the saved session stays as it was.
        def unreadable(browser):
            raise BrowserFailed("SESSION_READ_FAILED", detail="INDEXEDDB")
        run = self.run_fake(read=unreadable, run_seconds=5)
        self.assertEqual((run.value, run.saved, run.state_unread), ("value", None, "SESSION_READ_FAILED, INDEXEDDB"))
        self.assertEqual([item["value"] for item in self.manager.session_for("alpha").state["cookies"]], ["first"])
        self.assertGreaterEqual(run.stats["seconds"], 0)

    def test_a_state_unread_because_the_run_was_stopped_still_gives_no_value(self):
        def stopped_then_unreadable(browser):
            self.wait_for_stop(browser)
            raise BrowserFailed("SESSION_READ_FAILED", detail="CLOSED")
        with self.assertRaises(RunTimedOut):
            self.run_fake(read=stopped_then_unreadable)
        self.caller = ProcessControl()

        def cancelled_then_unreadable(browser):
            self.cancel(browser)
            raise BrowserFailed("SESSION_READ_FAILED", detail="CLOSED")
        with self.assertRaises(Cancelled):
            self.run_fake(read=cancelled_then_unreadable, run_seconds=5)

    def test_a_browser_error_in_the_action_names_its_step_and_kind_never_its_text(self):
        from playwright.sync_api import Error as PlaywrightError

        def closes(browser):
            browser.step = "TICKET_WAIT"
            raise PlaywrightError("Target page, context or browser has been closed at https://tickets.example/t?k=BF")
        with self.assertRaises(BrowserFailed) as caught:
            self.run_fake(closes, run_seconds=5)
        self.assertEqual((caught.exception.code, caught.exception.detail), ("BROWSER_FAILED", "TICKET_WAIT, CLOSED"))
        self.assertEqual(caught.exception.message, "Trình duyệt của phiên nguồn báo lỗi (BROWSER_FAILED, TICKET_WAIT, "
                                                   "CLOSED).")
        self.assertIsNone(caught.exception.__context__)  # raised outside the handler: the URL is not kept

    def test_a_run_waiting_for_the_sources_browser_gives_up_busy_or_on_a_cancel(self):
        """Another run of the same source holds its browser (its ``source_run_lock``). A run waiting for it ends
        SourceBusy (SERVER_BUSY, retryable) after ``lock_seconds``, or Cancelled once its caller cancels; a
        cancelled caller is refused even when the lock is free. None of them makes a browser, runs its action or
        keeps (or frees) the lock, so the next run goes on."""
        ran: list[str] = []

        def action(browser):
            ran.append("action")
            return "late"
        lock = runs.source_run_lock(self.manager, "alpha")
        self.assertTrue(lock.acquire(timeout=STOP_WAIT))  # another run of the same source holds the browser
        try:
            started = time.monotonic()
            with self.assertRaises(runs.SourceBusy) as caught:
                self.run_fake(action, run_seconds=5, lock_seconds=0.3)
            self.assertGreaterEqual(time.monotonic() - started, 0.3)  # it waited its lock_seconds first
            self.assertEqual((caught.exception.code, caught.exception.retryable), ("SERVER_BUSY", True))
            self.assertTrue(lock.locked())  # the other run's lock is still the other run's
            timer = threading.Timer(0.3, self.caller.request, ("cancel",))
            timer.daemon = True
            timer.start()
            with self.assertRaises(Cancelled):  # without the cancel this wait would end SourceBusy instead
                self.run_fake(action, run_seconds=5, lock_seconds=LOCK_WAIT)
            timer.join(STOP_WAIT)
            self.assertTrue(lock.locked())
        finally:
            lock.release()
        with self.assertRaises(Cancelled):  # the caller is still cancelled: refused although the lock is free
            self.run_fake(action, run_seconds=5, lock_seconds=LOCK_WAIT)
        self.assertFalse(lock.locked())  # given back at once
        self.assertEqual((ran, self.browsers), ([], 0))
        self.assertEqual(self.manager.status("alpha"), self.before)  # the session is untouched
        self.caller = ProcessControl()
        run = self.run_fake(run_seconds=5, lock_seconds=LOCK_WAIT)  # no refused run kept the lock
        self.assertEqual((run.value, self.browsers), ("value", 1))
        self.assertFalse(lock.locked())

    def test_the_deadline_counts_even_when_its_timer_has_not_fired(self):
        """No timer stops the run here: only its deadline, measured once the browser has closed."""
        def slow(browser) -> None:
            time.sleep(0.3)

        def slow_action(browser):
            slow(browser)
            return "late"
        with mock.patch.object(runs._RunStop, "_later", lambda stop, seconds, action, *args: None):
            for options in ({"action": slow_action}, {"read": slow}, {"close": slow}):
                with self.subTest(step=next(iter(options))), self.assertRaises(RunTimedOut):
                    self.run_fake(**options)
            self.assertEqual(self.run_fake(run_seconds=5).value, "value")  # a run in time is untouched


class HangEscalationTest(CoordinatorCase):
    """The two steps that end a run still hung after its stop (plan 9.13): its own Edge one grace later, its own
    driver one grace after that, armed until the browser has closed (its cleanup included). Fake browsers whose
    blocked call only one of the two steps frees; every wait is bounded, so a regression fails, never hangs."""

    GRACE = 0.3
    RUN = 0.3

    def setUp(self):
        super().setUp()
        self.manager.complete_login(self.manager.begin_login("alpha"), SIGNED_IN)
        self.caller = ProcessControl()
        self.steps: list[tuple[str, float]] = []

    def run_blocked(self, block: str | None, freed_by: str = "driver", *, cancel_at: float | None = None):
        """A run blocked in ``block`` ("action", "read", "close", or None: not blocked) until the kill of its
        Edge (``freed_by="edge"``) or the end of its driver (``"driver"``) frees it."""
        released, steps, started = threading.Event(), self.steps, time.monotonic()
        hang = DriverHang("page", ("playwright._impl._sync_base:_sync:113", "biliflow.download_account_pages:x:1"))

        def wait() -> None:
            if not released.wait(STOP_WAIT):
                raise AssertionError("never freed")

        class Browser:
            def __init__(browser, source, http, vault, control, **options):
                browser.control, browser.profile_left, browser.hang = control, False, None

            def __enter__(browser):
                return browser

            def __exit__(browser, *exc):
                if block == "close":
                    wait()  # the context's close returns once its driver is gone

            def storage_state(browser):
                if block == "read":
                    wait()
                    raise RuntimeError("Connection closed")  # what a freed call raises
                return ROTATED

            def force_close(browser):
                steps.append(("edge", time.monotonic() - started))
                if freed_by == "edge":
                    released.set()
                return 1

            def end_driver(browser):
                steps.append(("driver", time.monotonic() - started))
                browser.hang = hang
                released.set()
                return True

        def action(browser):
            if cancel_at is not None:
                threading.Timer(cancel_at, self.caller.request, ("cancel",)).start()
            if block == "action":
                wait()
                raise RuntimeError("Connection closed")
            return "value"

        with mock.patch.object(runs, "SessionBrowser", Browser):
            return run_with_session(self.manager, "alpha", action, control=self.caller, run_seconds=self.RUN,
                                    grace_seconds=self.GRACE)

    def step_names(self) -> list[str]:
        return [name for name, _at in self.steps]

    def test_a_run_still_blocked_after_its_edge_is_killed_has_its_driver_ended_one_grace_later(self):
        for block in ("action", "read", "close"):
            with self.subTest(block=block):
                self.steps.clear()
                with self.assertRaises(RunTimedOut) as caught:
                    self.run_blocked(block)
                self.assertEqual(self.step_names(), ["edge", "driver"])
                self.assertGreater(self.steps[0][1], self.RUN + self.GRACE - 0.1)  # one grace after the deadline
                self.assertGreater(self.steps[1][1] - self.steps[0][1], self.GRACE - 0.1)  # one more grace
                self.assertEqual(caught.exception.hang.phase, "page")  # what the browser reported
        self.assertEqual(self.manager.status("alpha")["state"], "CONNECTED")

    def test_a_run_freed_by_the_kill_of_its_edge_never_has_its_driver_ended(self):
        with self.assertRaises(RunTimedOut) as caught:
            self.run_blocked("action", freed_by="edge")
        time.sleep(2 * self.GRACE)  # the driver's step was cancelled when the browser closed
        self.assertEqual(self.step_names(), ["edge"])
        self.assertIsNone(caught.exception.hang)

    def test_a_cancel_ends_a_hung_run_the_same_way(self):
        self.RUN = 5.0  # only the cancel stops it
        with self.assertRaises(Cancelled) as caught:
            self.run_blocked("close", cancel_at=0.1)
        self.assertEqual(self.step_names(), ["edge", "driver"])
        self.assertGreater(self.steps[0][1], 0.1 + self.GRACE - 0.1)
        self.assertEqual(caught.exception.hang.phase, "page")

    def test_a_cancel_and_then_the_deadline_arm_the_two_steps_once(self):
        with self.assertRaises(Cancelled):
            self.run_blocked("action", cancel_at=0.1)  # the deadline comes 0.2 s later, while it still hangs
        time.sleep(2 * self.GRACE + 0.2)
        self.assertEqual(self.step_names(), ["edge", "driver"])

    def test_a_run_that_ends_in_time_arms_nothing(self):
        self.RUN = 5.0
        self.assertEqual(self.run_blocked(None).value, "value")
        time.sleep(2 * self.GRACE + 0.2)
        self.assertEqual(self.steps, [])


class FailureWordsTest(unittest.TestCase):
    """What a failed or slow hidden run tells the task: fixed words and numbers, never an error's text."""

    def test_a_browser_error_becomes_one_fixed_word(self):
        from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout
        cases = {
            PlaywrightTimeout("Timeout 30000ms exceeded at https://portal.example/a"): "TIMEOUT",
            PlaywrightError("Unable to serialize IndexedDB: Database version is unset"): "INDEXEDDB",
            PlaywrightError("Page crashed"): "CRASHED",
            PlaywrightError("Connection closed while reading from the driver"): "CONNECTION",
            PlaywrightError("Target page, context or browser has been closed"): "CLOSED",
            PlaywrightError("net::ERR_ABORTED at https://tickets.example/t?k=BF"): "OTHER",
        }
        for error, kind in cases.items():
            with self.subTest(kind=kind):
                self.assertEqual(failure_kind(error), kind)

    def test_the_tasks_message_names_the_inner_code_step_and_kind_but_no_host(self):
        self.assertEqual(_failure_words("BROWSER_FAILED", BrowserFailed("BROWSER_FAILED", detail="TICKET_WAIT, CLOSED")),
                         "BROWSER_FAILED, TICKET_WAIT, CLOSED")
        self.assertEqual(_failure_words("BROWSER_FAILED", BrowserFailed("SESSION_LOAD_FAILED", "portal.example",
                                                                        "OTHER")),
                         "BROWSER_FAILED, SESSION_LOAD_FAILED, OTHER")
        self.assertEqual(_failure_words("BROWSER_FAILED", BrowserFailed("BROWSER_FAILED")), "BROWSER_FAILED")
        self.assertEqual(_failure_words("BROWSER_UNAVAILABLE", RuntimeError("x")), "BROWSER_UNAVAILABLE")

    def test_a_run_leaves_one_log_line_of_numbers(self):
        class Got:
            ticket = object()
        lines: list[str] = []
        stats = {"seconds": 41.6, "launch_seconds": 3.2, "answered": 57, "network_seconds": 30.4, "skipped": 12}
        _log_stats(SessionRun(Got(), None, stats=stats), True, lines.append)
        _log_stats(SessionRun(None, None, stats=stats), False, lines.append)
        _log_stats(SessionRun(None, None), True, lines.append)  # no numbers: no line
        _log_stats(SessionRun(None, None, stats=stats), True, None)
        self.assertEqual(lines, [
            "Lượt ẩn (có vé): 42 s; mở Edge và nạp phiên 3 s; 57 yêu cầu qua mạng, chờ 30 s; bỏ qua 12 ảnh/font.",
            "Lượt ẩn: 42 s; mở Edge và nạp phiên 3 s; 57 yêu cầu qua mạng, chờ 30 s; bỏ qua 12 ảnh/font."])

    def test_the_log_line_says_how_the_session_loaded_and_was_read(self):
        lines: list[str] = []
        base = {"seconds": 6, "launch_seconds": 4, "answered": 2, "network_seconds": 1, "skipped": 0}
        _log_stats(SessionRun(None, None, stats={**base, "session_loaded": 1, "load_cause": "DENIED"}), False,
                   lines.append)
        _log_stats(SessionRun(None, None, stats={**base, "session_loaded": 2, "load_cause": "",
                                                 "indexed_db_kept": 1, "save_cause": "VERSION_UNSET"}), False,
                   lines.append)
        head = "Lượt ẩn: 6 s; mở Edge và nạp phiên 4 s; 2 yêu cầu qua mạng, chờ 1 s; bỏ qua 0 ảnh/font"
        self.assertEqual(lines, [
            head + "; phiên nạp không kèm IndexedDB (IndexedDB: DENIED).",
            head + "; phiên nạp chỉ cookie (IndexedDB: OTHER); không đọc được IndexedDB mới (VERSION_UNSET): "
                   "lưu cookie mới và dữ liệu trang đã nạp."])


class RequirementValuesTest(unittest.TestCase):
    """The plan's values of download_account_runs, pinned where the code keeps them (and as the defaults of
    ``run_with_session``, which every hidden run of a source goes through)."""

    def test_a_run_waits_at_most_15_minutes_for_its_sources_browser(self):
        """R55 (plan 9.12: one run of a source at a time; "chờ quá 15 phút: SourceBusy (SERVER_BUSY, thử lại
        được)"): LOCK_WAIT_SECONDS = 900."""
        self.assertEqual(runs.LOCK_WAIT_SECONDS, 900.0)
        self.assertEqual(inspect.signature(run_with_session).parameters["lock_seconds"].default, 900.0)

    def test_a_hidden_run_has_180_seconds_by_default_and_never_more_than_600(self):
        """Plan 9.12 ("Một hạn chung cho cả lượt (mặc định 180 giây, tối đa 600)"): HIDDEN_RUN_SECONDS = 180,
        MAX_HIDDEN_RUN_SECONDS = 600."""
        self.assertEqual((runs.HIDDEN_RUN_SECONDS, runs.MAX_HIDDEN_RUN_SECONDS), (180.0, 600.0))
        self.assertEqual(inspect.signature(run_with_session).parameters["run_seconds"].default, 180.0)
