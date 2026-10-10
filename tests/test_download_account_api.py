"""The account runtime of the downloader (download_account_api, M4): the root's manager and sign-in coordinator,
their lifecycle with DownloadService, the read-only status and the PC actions (sign in, cancel, disconnect).

Fake windows only (tests/test_download_account_login.FakeWindow: no Edge, no visible window) and made-up
sessions in temporary roots under the install's temp/. The routes through the Control Center handler (PC only,
the phone refused) are in test_download_account_queue.RouteTest.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from biliflow import download_account_tasks
from biliflow.download_account_api import AccountRuntime, owns
from biliflow.download_account_vault import SessionVault
from biliflow.download_accounts import AccountManager
from biliflow.download_api import DownloadService
from biliflow.download_sources import DirectMediaProvider, SourceRegistry
from biliflow.download_store import DownloadStore
from biliflow.download_worker import DownloadWorker
from tests.test_download_account_browser import CANARY_SID, CONFIG, FIRST, TEMP_PARENT
from tests.test_download_account_login import FIXTURE_ADAPTER, Answers, FakeWindow
from tests.test_download_accounts import Clock, FakeProtector, GoodAcl

WAIT_SECONDS = 30


def wait_until(predicate, timeout=WAIT_SECONDS):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class RuntimeCase(unittest.TestCase):
    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        self._temp = TemporaryDirectory(dir=TEMP_PARENT, prefix="account-api-")
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.clock = Clock()
        self.windows: list[FakeWindow] = []
        self.launches = 0
        self.wakes = 0

    def manager(self) -> AccountManager:
        vault = SessionVault(self.root, protector=FakeProtector(), acl=GoodAcl(FIRST))
        return AccountManager(self.root, CONFIG, vault=vault, clock=self.clock)

    def launcher(self, **window):
        def launch(source, http, vault, control, permit, stop_at):
            self.launches += 1
            self.windows.append(FakeWindow(control, **window))
            return self.windows[-1]
        return launch

    def runtime(self, verifier=None, *, verifiers=None, grace_seconds=None, **window) -> AccountRuntime:
        options = {"launcher": self.launcher(**window), "poll_seconds": 0.01,
                   "verifiers": verifiers if verifiers is not None else {FIXTURE_ADAPTER: verifier or Answers(True)}}
        if grace_seconds is not None:
            options["grace_seconds"] = grace_seconds
        runtime = AccountRuntime(self.root, manager=self.manager(), coordinator_options=options)
        runtime.attach(wake=self.wake, registry=None, waiting=lambda: {"alpha": 2})
        self.addCleanup(runtime.close)
        self.addCleanup(runtime.stop, 10)
        return runtime

    def service(self, runtime):
        store = DownloadStore(self.root / "state" / "downloads.sqlite3")
        worker = DownloadWorker(self.root, store, sources=SourceRegistry([DirectMediaProvider()]))
        return DownloadService(self.root, cleanable=lambda: (0, 0), bin_reader=None, store=store, worker=worker,
                               accounts=runtime)

    def wake(self):
        self.wakes += 1

    @staticmethod
    def source(runtime, source_id="alpha"):
        return next(item for item in runtime.status()["sources"] if item["id"] == source_id)


class RuntimeTest(RuntimeCase):
    def test_without_a_configured_source_there_is_no_manager_and_no_vault(self):
        runtime = AccountRuntime(self.root)
        self.assertIsNone(runtime.manager)
        self.assertEqual(runtime.start(), {})
        self.assertEqual(runtime.status()["sources"], [])
        status, reply = runtime.handle_post("alpha", "login")
        self.assertEqual((status, reply["code"]), (503, "ACCOUNT_NOT_READY"))
        runtime.stop(1)
        runtime.close()
        self.assertEqual(list(self.root.iterdir()), [])  # nothing was created: no database, no private folder
        self.assertTrue(owns("/api/download-accounts") and owns("/api/download-accounts/alpha/login"))
        self.assertFalse(owns("/api/downloads"))

    def test_a_pc_sign_in_runs_the_window_and_its_end_wakes_the_queue(self):
        runtime = self.runtime()
        before = self.source(runtime)
        self.assertEqual((before["login_supported"], before["login_running"], before["last_login"],
                          before["waiting_tasks"], before["state"]), (True, False, None, 2, "NOT_CONNECTED"))
        self.assertEqual(self.launches, 0)  # reading the status never opens a window
        status, reply = runtime.handle_post("alpha", "login")
        self.assertEqual((status, reply["login"]), (202, "STARTED"))
        self.assertTrue(wait_until(lambda: runtime.last_login.get("alpha")))
        last = self.source(runtime)["last_login"]
        self.assertEqual((last["code"], last["connected"], last["hang"]), ("CONNECTED", True, None))
        self.assertGreaterEqual(self.wakes, 1)
        self.assertTrue(runtime.manager.session_gate("alpha")[0])
        self.assertNotIn(CANARY_SID, json.dumps(runtime.status()))
        self.assertEqual(self.launches, 1)

    def test_a_second_sign_in_is_busy_and_cancel_ends_the_open_one(self):
        runtime = self.runtime(Answers())  # the user never finishes signing in
        self.assertEqual(runtime.handle_post("alpha", "login")[0], 202)
        self.assertTrue(wait_until(lambda: self.windows and self.windows[0].paused.is_set()))
        status, busy = runtime.handle_post("alpha", "login")
        self.assertEqual((status, busy["code"]), (409, "LOGIN_BUSY"))
        self.assertTrue(self.source(runtime)["login_running"])
        self.assertTrue(runtime.busy())
        status, cancelled = runtime.handle_post("alpha", "cancel-login")
        self.assertEqual((status, cancelled["cancelled"]), (200, True))
        self.assertTrue(wait_until(lambda: runtime.last_login.get("alpha")))
        last = runtime.last_login["alpha"]
        self.assertEqual((last["code"], last["connected"]), ("LOGIN_CANCELLED", False))
        self.assertTrue(wait_until(lambda: not runtime.busy()))
        self.assertTrue(self.windows[0].closed)
        after = self.source(runtime)
        self.assertEqual((after["state"], after["error_code"]), ("NOT_CONNECTED", "LOGIN_CANCELLED"))
        self.assertFalse(runtime.manager.session_gate("alpha")[0])
        self.assertEqual(runtime.handle_post("beta", "cancel-login")[1]["cancelled"], False)
        self.assertEqual(self.launches, 1)  # the busy second sign-in never opened a window

    def test_a_session_change_tells_the_downloader_to_drop_the_sources_kept_links(self):
        runtime = self.runtime()
        forgotten = []
        runtime.attach(wake=self.wake, registry=None, waiting=lambda: {},
                       forget=lambda owner, source_id: forgotten.append((owner, source_id)))
        owner = runtime.manager.owner
        runtime.handle_post("alpha", "login")
        self.assertTrue(wait_until(lambda: forgotten))
        self.assertEqual(forgotten, [(owner, "alpha")])  # a sign-in that connected: a new session
        self.assertEqual(runtime.handle_post("alpha", "disconnect")[0], 200)
        self.assertEqual(forgotten, [(owner, "alpha")] * 2)
        runtime.last_login.clear()
        runtime.coordinator.verifiers[FIXTURE_ADAPTER] = Answers()  # the user never finishes signing in
        runtime.handle_post("alpha", "login")
        self.assertTrue(wait_until(lambda: self.windows and self.windows[-1].paused.is_set()))
        runtime.handle_post("alpha", "cancel-login")
        self.assertTrue(wait_until(lambda: runtime.last_login.get("alpha")))
        self.assertEqual(runtime.last_login["alpha"]["code"], "LOGIN_CANCELLED")
        self.assertEqual(len(forgotten), 2)  # no new session: the kept links stay

    def test_a_disconnect_that_fails_halfway_still_drops_the_kept_links(self):
        runtime = self.runtime()
        forgotten = []
        runtime.attach(wake=self.wake, registry=None, waiting=lambda: {},
                       forget=lambda owner, source_id: forgotten.append(source_id))
        runtime.handle_post("alpha", "login")
        self.assertTrue(wait_until(lambda: forgotten == ["alpha"]))
        with mock.patch.object(runtime.manager, "disconnect", side_effect=sqlite3.OperationalError("locked")):
            status, reply = runtime.handle_post("alpha", "disconnect")
        self.assertEqual((status, reply["code"]), (503, "ACCOUNT_STATE_ERROR"))
        self.assertEqual(forgotten, ["alpha", "alpha"])

    def test_a_failing_forget_never_fails_a_disconnect(self):
        runtime = self.runtime()

        def forget(owner, source_id):
            raise RuntimeError("the downloader is shutting down")
        runtime.attach(wake=self.wake, registry=None, waiting=lambda: {}, forget=forget)
        runtime.handle_post("alpha", "login")
        self.assertTrue(wait_until(lambda: runtime.last_login.get("alpha")))
        self.assertEqual(runtime.handle_post("alpha", "disconnect")[0], 200)
        self.assertFalse(runtime.manager.session_gate("alpha")[0])

    def test_disconnect_closes_an_open_window_and_drops_the_session(self):
        runtime = self.runtime(Answers(True, False))
        runtime.handle_post("alpha", "login")
        self.assertTrue(wait_until(lambda: runtime.last_login.get("alpha")))
        self.assertTrue(runtime.manager.session_gate("alpha")[0])
        runtime.last_login.clear()
        runtime.coordinator.verifiers[FIXTURE_ADAPTER] = Answers()
        self.assertEqual(runtime.handle_post("alpha", "login")[0], 202)
        self.assertTrue(wait_until(lambda: len(self.windows) == 2 and self.windows[1].paused.is_set()))
        status, reply = runtime.handle_post("alpha", "disconnect")
        self.assertEqual((status, reply["source"]["state"]), (200, "NOT_CONNECTED"))
        self.assertTrue(wait_until(lambda: not runtime.busy()))
        self.assertTrue(self.windows[1].closed)  # the open window ended (its run closed it)
        self.assertTrue(wait_until(lambda: runtime.last_login.get("alpha")))
        last = runtime.last_login["alpha"]
        self.assertEqual((last["code"], last["connected"]), ("LOGIN_CANCELLED", False))
        self.assertEqual(runtime.manager.session_gate("alpha"), (False, "NOT_CONNECTED", None))
        self.assertEqual(self.source(runtime)["state"], "NOT_CONNECTED")
        self.assertEqual(self.launches, 2)

    def test_only_configured_sources_with_a_sign_in_check_open_a_window(self):
        runtime = self.runtime(verifiers={})
        status, unknown = runtime.handle_post("zzz", "login")
        self.assertEqual((status, unknown["code"]), (404, "ACCOUNT_UNKNOWN"))
        status, unsupported = runtime.handle_post("alpha", "login")
        self.assertEqual((status, unsupported["code"]), (409, "LOGIN_UNSUPPORTED"))
        self.assertFalse(self.source(runtime)["login_supported"])
        self.assertEqual(self.launches, 0)

    def test_start_settles_an_interrupted_sign_in_without_any_window(self):
        first = self.manager()
        first.begin_login("alpha")  # the Control Center was killed while the window was open
        first.close()
        runtime = self.runtime()
        recovered = runtime.start()
        self.assertEqual((recovered["unsettled"], recovered["profiles_cleared"]), ([], True))
        self.assertEqual(self.source(runtime)["state"], "NOT_CONNECTED")
        self.assertEqual(self.launches, 0)

    def test_shutdown_refuses_new_sign_ins_and_closes_the_manager_only_when_idle(self):
        # The window hangs until its Edge is killed (60 s after the cancel, or below by the test): it outlives the
        # bounded stop, so only the service's closer may close the manager, once the window has ended.
        runtime = self.runtime(Answers(), hang=True, grace_seconds=60)
        service = self.service(runtime)
        self.assertEqual(runtime.handle_post("alpha", "login")[0], 202)
        self.assertTrue(wait_until(lambda: self.windows and self.windows[0].paused.is_set()))
        window = self.windows[0]
        self.addCleanup(window.force_close)  # never left hanging if an assertion fails first
        stop, close = runtime.stop, runtime.close
        closed_when = []  # (a window still running, that window closed) at the moment the manager is closed

        def closing():
            closed_when.append((runtime.busy(), window.closed))
            close()
        with mock.patch.object(runtime, "stop", side_effect=lambda *args: stop(0.2)), \
                mock.patch.object(runtime, "close", side_effect=closing), \
                mock.patch.object(runtime, "busy", wraps=runtime.busy) as busy:
            service.stop()  # the worker, then no new sign-in and the window cancelled, waited for 0.2 s only
            self.assertEqual(closed_when, [])
            self.assertFalse(service.wait_closed(0))
            self.assertTrue(runtime.busy())
            status, refused = runtime.handle_post("alpha", "login")
            self.assertEqual((status, refused["code"]), (503, "SHUTTING_DOWN"))
            checks = busy.call_count
            self.assertTrue(wait_until(lambda: busy.call_count > checks))  # the closer checked: still busy
            self.assertEqual(closed_when, [])
            self.assertFalse(service.wait_closed(0))
            window.force_close()  # its Edge is killed: the window's thread ends
            self.assertTrue(service.wait_closed(WAIT_SECONDS))
        self.assertEqual(closed_when, [(False, True)])  # closed once, by the closer, after the window ended
        self.assertTrue(wait_until(lambda: runtime.last_login.get("alpha")))
        last = runtime.last_login["alpha"]
        self.assertEqual((last["code"], last["connected"]), ("LOGIN_CANCELLED", False))
        after = self.manager()  # the account's row as the next start reads it: nothing was saved
        self.addCleanup(after.close)
        status = after.status("alpha")
        self.assertEqual((status["state"], status["error_code"]), ("NOT_CONNECTED", "LOGIN_CANCELLED"))
        self.assertEqual(after.session_gate("alpha"), (False, "NOT_CONNECTED", None))
        self.assertEqual(self.launches, 1)


class ServiceLifecycleTest(RuntimeCase):
    def test_stop_ends_the_worker_then_the_windows_then_closes_the_manager(self):
        """DownloadService.stop (plan 9.14) with the real worker, sign-in coordinator and manager: the dispatch
        loop runs and a sign-in window is open (the user never finishes). Each step is wrapped, never replaced,
        and notes what was true when it began and when it ended: the worker's loop has ended while the window
        is still open; only then does the accounts' stop close that window (and refuse new sign-ins); only once
        it has ended is the manager closed, and the store after it."""
        runtime = self.runtime(Answers())  # the user never finishes signing in: the window stays open
        service = self.service(runtime)
        service.start()
        self.assertEqual(runtime.handle_post("alpha", "login")[0], 202)
        self.assertTrue(wait_until(lambda: self.windows and self.windows[0].paused.is_set()))
        window, worker, manager = self.windows[0], service.worker, runtime.manager
        self.addCleanup(window.force_close)  # never left open if an assertion fails first
        steps = []

        def manager_open() -> bool:
            try:
                manager.status("alpha")
            except sqlite3.ProgrammingError:  # "Cannot operate on a closed database."
                return False
            return True

        refused = []  # a sign-in asked for right after the accounts' stop, while the manager is still open

        def wrapped(name, real):
            def step(*args):
                steps.append((f"{name} begins", worker.finished(), window.closed, runtime.busy(), manager_open()))
                real(*args)
                steps.append((f"{name} ended", worker.finished(), window.closed, runtime.busy(), manager_open()))
                if name == "accounts.stop":
                    refused.append(runtime.handle_post("alpha", "login"))
            return step
        with mock.patch.object(worker, "shutdown", side_effect=wrapped("worker.shutdown", worker.shutdown)), \
                mock.patch.object(runtime, "stop", side_effect=wrapped("accounts.stop", runtime.stop)), \
                mock.patch.object(runtime, "close", side_effect=wrapped("accounts.close", runtime.close)), \
                mock.patch.object(service.store, "close", side_effect=wrapped("store.close", service.store.close)):
            service.stop()
            self.assertTrue(service.wait_closed(WAIT_SECONDS))
        # (step, the worker's loop has ended, the window is closed, a window still runs, the manager is open)
        self.assertEqual(steps, [("worker.shutdown begins", False, False, True, True),
                                 ("worker.shutdown ended", True, False, True, True),
                                 ("accounts.stop begins", True, False, True, True),
                                 ("accounts.stop ended", True, True, False, True),
                                 ("accounts.close begins", True, True, False, True),
                                 ("accounts.close ended", True, True, False, False),
                                 ("store.close begins", True, True, False, False),
                                 ("store.close ended", True, True, False, False)])
        self.assertEqual([(status, reply["code"]) for status, reply in refused], [(503, "SHUTTING_DOWN")])
        self.assertEqual(self.launches, 1)  # the refused sign-in never opened a window

    def test_a_failed_worker_stop_still_ends_the_windows_and_closes_the_store(self):
        runtime = self.runtime()
        service = self.service(runtime)
        with mock.patch.object(service.worker, "shutdown", side_effect=RuntimeError("stuck")), \
                mock.patch.object(runtime, "stop") as stop, mock.patch.object(service.store, "close") as close:
            with self.assertRaises(RuntimeError):
                service.stop()
        stop.assert_called_once()
        close.assert_called_once()
        service.store.close()

    def test_the_manager_stays_open_while_a_window_could_still_use_it(self):
        runtime = self.runtime()
        service = self.service(runtime)
        window_open = threading.Event()
        window_open.set()
        seen = []  # what each idle check of the service found: a window still running

        def busy():
            seen.append(window_open.is_set())
            return window_open.is_set()
        with mock.patch.object(runtime, "busy", side_effect=busy), \
                mock.patch.object(runtime, "close", wraps=runtime.close) as close:
            service.stop()
            self.assertEqual(seen[:1], [True])  # the stop's own check: busy, so the closer took over
            self.assertTrue(wait_until(lambda: len(seen) >= 3))  # then the closer checked twice, still busy
            close.assert_not_called()
            self.assertFalse(service.wait_closed(0))
            window_open.clear()  # the window ended
            self.assertTrue(service.wait_closed(10))
        close.assert_called_once()  # closed once, by the closer
        self.assertEqual(seen[-1], False)

    def test_a_database_or_disk_error_gives_a_fixed_message_never_its_text(self):
        service = self.service(self.runtime())
        secret = "UNIQUE constraint failed: download_group_members.item_key at E:/private/x"
        with mock.patch.object(service.worker, "episodes", side_effect=sqlite3.IntegrityError(secret)):
            status, reply = service.handle_get("/api/downloads/1/episodes", "")
        self.assertEqual((status, reply["code"], reply["kind"]), (503, "DOWNLOAD_STATE_ERROR", "IntegrityError"))
        with mock.patch.object(service.worker, "group_action", side_effect=OSError(5, "denied", "E:/private")):
            status, reply = service.handle_post("/api/downloads/groups/1/stop", {})
        self.assertEqual((status, reply["code"]), (500, "DOWNLOAD_DISK_ERROR"))
        self.assertNotIn("private", json.dumps(reply))
        service.stop()

    def test_the_snapshot_shows_the_accounts_without_any_secret(self):
        runtime = self.runtime()
        service = self.service(runtime)
        service.start()
        try:
            runtime.handle_post("alpha", "login")
            self.assertTrue(wait_until(lambda: runtime.last_login.get("alpha")))
            snapshot = service.snapshot()
            self.assertEqual([item["id"] for item in snapshot["accounts"]["sources"]], ["alpha", "beta"])
            self.assertEqual(snapshot["groups"], [])
            self.assertNotIn(CANARY_SID, json.dumps(snapshot))
            self.assertEqual(snapshot["accounts"]["sources"][0]["state"], "CONNECTED")
        finally:
            service.stop()


class RequirementValuesTest(unittest.TestCase):
    def test_tasks_waiting_for_a_sign_in_are_checked_again_every_30_seconds(self):
        """R57 (plan 9.14: the dispatcher sends WAITING_LOGIN tasks back to the queue "ngay sau callback đăng
        nhập, và mỗi 30 giây để phủ restart"; no busy loop in between): LOGIN_RECHECK_SECONDS = 30."""
        self.assertEqual(download_account_tasks.LOGIN_RECHECK_SECONDS, 30.0)


if __name__ == "__main__":
    unittest.main()
