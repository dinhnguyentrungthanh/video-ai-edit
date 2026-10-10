"""A session browser's own Playwright driver (download_account_driver): recorded when its runtime starts it,
checked before it is ended, ended through its own handle only.

Every process here is the test's own: Python children that sleep (the real interpreter, isolated, in a
temporary folder under the install's temp/, no window) and the driver of a Playwright runtime this test
starts (no browser). Nothing looks for a process by name, and nothing outside this test is touched.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import os
import subprocess
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import greenlet
import psutil

from biliflow.download_account_driver import DriverHang, DriverProcess, blocked_at, driver_popen
from tests.test_download_account_browser import TEMP_PARENT

HAVE_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
PYTHON = psutil.Process().exe()  # the interpreter itself, not a venv launcher that starts another one
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SLEEPER = "import time; time.sleep(120)"
# A child that starts a grandchild, prints its pid and sleeps: the grandchild is not this process's child.
PARENT_OF_ONE = ("import subprocess, sys, time; child = subprocess.Popen([sys.executable, '-I', '-c', "
                 "'import time; time.sleep(120)']); print(child.pid, flush=True); time.sleep(120)")
WAIT = 30.0  # the most a test waits for anything (so a regression fails, never hangs)


class FakeHandle:
    """A handle-like object for a pid this test did not start through it: ``kill`` must never be called."""

    def __init__(self, pid: int):
        self.pid = pid
        self.returncode = None
        self.kills = 0

    def kill(self) -> None:
        self.kills += 1


class ChildCase(unittest.TestCase):
    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        self._folder = TemporaryDirectory(dir=TEMP_PARENT, prefix="driver-test-")
        self.addCleanup(self._folder.cleanup)
        self.folder = Path(self._folder.name)

    def spawn(self, code: str = SLEEPER, **options) -> subprocess.Popen:
        child = subprocess.Popen([PYTHON, "-I", "-c", code], cwd=self.folder, creationflags=NO_WINDOW,
                                 stdin=subprocess.DEVNULL, **options)
        self.addCleanup(self.end_tree, child)
        return child

    @staticmethod
    def end_tree(child: subprocess.Popen) -> None:
        """The test's own child and whatever it started."""
        try:
            for process in psutil.Process(child.pid).children(recursive=True):
                process.kill()
        except psutil.Error:
            pass
        child.kill()
        child.wait(WAIT)
        if child.stdout is not None:
            child.stdout.close()


class DriverProcessTest(ChildCase):
    def test_a_recorded_child_is_ended_through_its_own_handle(self):
        child = self.spawn()
        driver = DriverProcess.record(child)
        self.assertIsNotNone(driver)
        process = psutil.Process(child.pid)
        self.assertEqual((driver.pid, driver.created, os.path.normcase(driver.exe)),
                         (child.pid, process.create_time(), os.path.normcase(PYTHON)))
        self.assertNotIn("popen", repr(driver))
        self.assertIsNotNone(driver.owned())
        self.assertTrue(driver.end())
        self.assertIsNotNone(child.wait(WAIT))
        self.assertIsNone(driver.owned())  # gone, and its handle says so
        self.assertFalse(driver.end())  # never twice

    def test_a_recycled_pid_is_refused(self):
        """Same pid, another creation time: another process now has the number. It is never ended."""
        child = self.spawn()
        driver = dataclasses.replace(DriverProcess.record(child), created=psutil.Process(child.pid).create_time() + 1)
        self.assertIsNone(driver.owned())
        self.assertFalse(driver.end())
        self.assertIsNone(child.poll())  # still running

    def test_another_executable_is_refused(self):
        child = self.spawn()
        driver = dataclasses.replace(DriverProcess.record(child), exe=str(self.folder / "node.exe"))
        self.assertFalse(driver.end())
        self.assertIsNone(child.poll())

    def test_the_handle_of_another_process_is_refused(self):
        first, second = self.spawn(), self.spawn()
        driver = dataclasses.replace(DriverProcess.record(first), popen=second)
        self.assertFalse(driver.end())
        self.assertEqual((first.poll(), second.poll()), (None, None))

    def test_a_process_that_is_not_a_child_of_this_one_is_refused(self):
        parent = self.spawn(PARENT_OF_ONE, stdout=subprocess.PIPE, text=True)
        guard = threading.Timer(WAIT, parent.kill)  # a child that never prints ends: the read then fails
        guard.start()
        try:
            grandchild = psutil.Process(int(parent.stdout.readline()))
        finally:
            guard.cancel()
        handle = FakeHandle(grandchild.pid)
        self.assertIsNone(DriverProcess.record(handle))
        driver = DriverProcess(grandchild.pid, grandchild.create_time(), grandchild.exe(), handle)
        self.assertIsNone(driver.owned())
        self.assertFalse(driver.end())
        self.assertEqual(handle.kills, 0)
        self.assertTrue(grandchild.is_running())

    def test_an_exited_process_is_never_recorded_or_ended(self):
        child = self.spawn("pass")
        child.wait(WAIT)
        self.assertIsNone(DriverProcess.record(child))
        self.assertIsNone(DriverProcess.record(None))
        self.assertIsNone(driver_popen(object()))
        self.assertIsNone(driver_popen(None))

    def test_a_driver_that_exited_after_it_was_recorded_is_not_ended(self):
        """Its handle says it exited (not yet read by its owner): nothing is ended, nothing is reported."""
        child = self.spawn("import time; time.sleep(0.5)")
        driver = DriverProcess.record(child)
        self.assertIsNotNone(driver)
        psutil.Process(child.pid).wait(WAIT)  # gone; ``child.returncode`` is still unset
        reported: list[str] = []
        self.assertFalse(driver.end(before=lambda: reported.append("hang")))
        self.assertEqual(reported, [])

    def test_what_is_reported_is_there_before_the_driver_is_ended(self):
        child = self.spawn()
        driver = DriverProcess.record(child)
        seen: list = []
        self.assertTrue(driver.end(before=lambda: seen.append(child.poll())))
        self.assertEqual(seen, [None])  # reported while the driver still ran
        refused = dataclasses.replace(DriverProcess.record(self.spawn()), created=0.0)
        self.assertFalse(refused.end(before=lambda: seen.append("refused")))
        self.assertEqual(seen, [None])  # a refused driver reports nothing


class BlockedAtTest(unittest.TestCase):
    def test_where_a_suspended_greenlet_waits_module_function_and_line_only(self):
        secret = "BF-CANARY-local-value"
        main = greenlet.getcurrent()
        report: dict = {}

        def look() -> None:  # from another thread, while ``waits_here`` is suspended
            report["stack"] = blocked_at(suspended)

        def waits_here() -> None:
            local = secret  # noqa: F841 - a local value that must never be in the report
            main.switch()

        suspended = greenlet.greenlet(waits_here)
        suspended.switch()  # runs until ``main.switch()`` inside it, then comes back here
        reader = threading.Thread(target=look)
        reader.start()
        reader.join(WAIT)
        stack = report["stack"]
        self.assertEqual(len(stack), 1)  # the innermost frame; no frame of biliflow is in this greenlet
        module, function, line = stack[0].rsplit(":", 2)
        self.assertEqual((module, function), (__name__, "waits_here"))
        self.assertTrue(line.isdigit())
        self.assertNotIn(secret, repr(stack))
        self.assertEqual(blocked_at(greenlet.getcurrent()), ())  # a running greenlet has no frame to report
        self.assertEqual(blocked_at(object()), ())
        suspended.switch()  # let it finish
        self.assertEqual(DriverHang("page", stack).stack, stack)

    def test_a_running_greenlet_is_read_through_its_thread(self):
        """The runtime's stop may wait in the run thread's own greenlet: the thread's current frame is read."""
        secret = "BF-CANARY-thread-value"
        started, release = threading.Event(), threading.Event()

        def waits_in_thread() -> None:
            local = secret  # noqa: F841 - a local value that must never be in the report
            started.set()
            release.wait(WAIT)

        thread = threading.Thread(target=waits_in_thread)
        thread.start()
        try:
            self.assertTrue(started.wait(WAIT))
            time.sleep(0.1)
            stack = blocked_at(object(), thread_id=thread.ident)
        finally:
            release.set()
            thread.join(WAIT)
        self.assertEqual(len(stack), 1)  # the innermost frame: the wait in the standard library
        self.assertTrue(stack[0].startswith("threading:wait:"), stack)
        self.assertNotIn(secret, repr(stack))
        self.assertEqual(blocked_at(object(), thread_id=-1), ())  # no such thread


@unittest.skipUnless(HAVE_PLAYWRIGHT, "the installed Playwright is required (nothing is installed)")
class InstalledPlaywrightTest(unittest.TestCase):
    """The installed Playwright 1.63: the handle of its driver is where ``driver_popen`` reads it, and a call
    that waits on a driver that stopped answering ends when that driver is ended (no browser here)."""

    def start(self):
        from playwright.sync_api import sync_playwright
        manager = sync_playwright()
        runtime = manager.start()
        return manager, runtime

    def test_the_driver_handle_is_the_runtimes_own_child(self):
        manager, runtime = self.start()
        try:
            driver = DriverProcess.record(driver_popen(manager))
            self.assertIsNotNone(driver)
            self.assertEqual(Path(driver.exe).name.lower(), "node.exe")
            self.assertEqual(psutil.Process(driver.pid).ppid(), os.getpid())
        finally:
            runtime.stop()
        self.assertIsNone(driver.owned())  # the runtime's stop let it exit; nothing is left to end

    def test_a_call_waiting_on_a_driver_that_stopped_answering_ends_when_that_driver_is_ended(self):
        outcome: dict = {}

        def run() -> None:  # one thread owns the runtime (the sync API's rule); the test only watches it
            manager, runtime = self.start()
            driver = outcome["driver"] = DriverProcess.record(driver_popen(manager))
            outcome["greenlet"] = greenlet.getcurrent()
            psutil.Process(driver.pid).suspend()  # the driver stops answering, as one that never saw Edge go
            started = time.monotonic()
            try:
                runtime.request.new_context()
            except Exception as error:  # noqa: BLE001 - what the blocked call raised once its driver was ended
                outcome["error"] = type(error).__name__
            outcome["blocked"] = time.monotonic() - started
            started = time.monotonic()
            runtime.stop()
            outcome["stopped"] = time.monotonic() - started

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        deadline = time.monotonic() + WAIT
        while "greenlet" not in outcome and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(1.0)  # the call is waiting now
        driver = outcome["driver"]
        stack = blocked_at(outcome["greenlet"])
        self.assertTrue(driver.end())
        worker.join(WAIT)
        if worker.is_alive():  # never hang the suite: end the test's own driver by its handle, then fail
            driver.popen.kill()
            self.fail("the call did not end when its driver was ended")
        self.assertIn("error", outcome)
        self.assertLess(outcome["stopped"], 5)
        self.assertTrue(stack and stack[0].startswith("playwright."), stack)
        self.assertIsNone(driver.owned())


if __name__ == "__main__":
    unittest.main()
