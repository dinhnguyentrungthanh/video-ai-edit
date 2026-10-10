"""Group actions that must hold through interruptions, and the downloader's bounded stop (M4 review fix,
docs/SOURCE_ACCOUNTS_PLAN.md 9.15).

Hủy nhóm cancels every unfinished episode the per-task Hủy can reach and keeps finished files; the group's
CANCELLED state and the members' stored Dừng / Tiếp tục are applied again after an interruption or a restart,
before anything dispatches or wakes. ``DownloadService.stop`` closes the account manager and the store only once no
thread of the service can use them.

Made-up lists and temporary roots under the install's temp/ only; partial downloads are a few self-made bytes; no
browser, no sign-in, no download (the task body is replaced wherever a task could start).
"""
from __future__ import annotations

import sqlite3
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from biliflow.download_api import DownloadService
from biliflow.download_groups import GroupError
from biliflow.download_runner import ProcessControl
from biliflow.download_store import DownloadStore
from biliflow.download_worker import STOPPABLE, DownloadActionError, DownloadWorker
from tests import test_download_groups as group_tests
from tests.account_queue_fixtures import series

WAIT = 10.0


class SimulatedInterruption(BaseException):
    """A crash between two steps (it passes every ``except Exception``)."""


class IntentCase(group_tests.GroupCase):
    def worker(self) -> DownloadWorker:
        worker = DownloadWorker(self.root, self.store, space_probe=lambda root: (10**12, 0),
                                cache_pruner=lambda root: {})
        worker.cancel_retry_seconds = 0.0
        return worker

    def group_with_tasks(self, count: int, *, film: str = "f1", key: str = "key-00000001",
                         tasks: int | None = None):
        """A confirmed group of ``count`` episodes; ``tasks`` of them become tasks now (all by default)."""
        created = self.create(self.waiting_page(series(count, film=film)), key=key)
        limit = None if tasks is None else self.store.unfinished_count() + tasks
        return created.group, (self.groups.fill() if limit is None else self.groups.fill(limit))

    def restart(self) -> DownloadWorker:
        self.reopen()
        worker = self.worker()
        worker.recover()
        return worker

    def state(self, task) -> str:
        return self.store.get(task["id"])["state"]

    def states(self, tasks) -> list[str]:
        return [self.state(task) for task in tasks]

    def partial(self, task):
        folder = self.root / "temp" / "downloads" / str(task["id"])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "clip.mp4.part").write_bytes(b"partial-bytes" * 64)
        return folder

    def held(self, task):
        folder = self.partial(task)
        handle = (folder / "held.mp4.part").open("wb")  # Windows refuses to delete an open file
        self.addCleanup(handle.close)
        return folder, handle

    def interrupted(self, worker, group_id, action, after=0):
        """``action`` on the group, interrupted before its (after+1)-th episode."""
        real, seen = worker._group_task_action, []

        def flaky(task, name):
            seen.append(task["id"])
            if len(seen) > after:
                raise SimulatedInterruption()
            return real(task, name)

        with mock.patch.object(worker, "_group_task_action", side_effect=flaky):
            with self.assertRaises(SimulatedInterruption):
                worker.group_action(group_id, action)

    def no_start(self, worker):
        """Record instead of running a task body: nothing is probed, fetched or downloaded."""
        started: list[int] = []
        patcher = mock.patch.object(worker, "_run_task", side_effect=lambda task_id, control: started.append(task_id))
        patcher.start()
        self.addCleanup(patcher.stop)
        return started


class GroupCancelTest(IntentCase):
    def test_cancel_reaches_every_unfinished_episode_and_keeps_finished_files(self):
        # Not part of the cancelled group: a stopped episode of another, active group and a link of its own.
        other_group, other = self.group_with_tasks(2, film="f2", key="key-00000002")
        for task in other:
            self.store.transition(task["id"], {"QUEUED"}, "STOPPED")
        alone, = self.store.add_tasks(["https://clips.example/v/alone"])
        self.store.transition(alone["id"], {"QUEUED"}, "STOPPED")
        group, tasks = self.group_with_tasks(9, tasks=7)  # seven episode tasks, two members still PENDING
        queued, stopped, interrupted, failed, waiting, choosing, completed = tasks
        for task, state in ((stopped, "STOPPED"), (interrupted, "INTERRUPTED"), (failed, "FAILED"),
                            (waiting, "WAITING_LOGIN"), (choosing, "NEEDS_CHOICE"), (completed, "COMPLETED")):
            self.store.transition(task["id"], {"QUEUED"}, state)
        folders = [self.partial(task) for task in (stopped, interrupted, failed, waiting)]
        kept = self.root / "input" / "001 - Phim - S01E01.mp4"
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_bytes(b"finished episode")
        self.store.update_fields(completed["id"], output_path=str(kept), output_size=len(b"finished episode"))
        worker = self.worker()
        before = self.store.unfinished_count()

        summary = worker.group_action(group["id"], "cancel")["group"]

        self.assertEqual(self.states(tasks), ["CANCELLED"] * 6 + ["COMPLETED"])
        self.assertTrue(all(not folder.exists() for folder in folders))
        self.assertEqual(kept.read_bytes(), b"finished episode")
        self.assertEqual((summary["state"], summary["counts"]["cancelled"], summary["done"]), ("CANCELLED", 8, 1))
        self.assertEqual(self.groups.fill(), [])  # the two PENDING members never become tasks
        self.assertEqual(self.store.unfinished_count(), before - 6)  # their places in the 100 are free
        self.assertFalse(any(self.groups.item_taken(task["item_key"], -1) for task in tasks[:6]))
        started = self.no_start(worker)
        for _ in range(3):  # settling again changes nothing; nothing outside the group is touched
            worker.dispatch()
        self.assertEqual((self.state(alone), self.states(other)), ("STOPPED", ["STOPPED", "STOPPED"]))
        self.assertEqual((started, self.groups.group(other_group["id"])["state"]), ([], "ACTIVE"))

    def test_running_and_publishing_episodes_follow_the_per_task_rules(self):
        group, (first, second, publishing) = self.group_with_tasks(3)
        self.store.transition(publishing["id"], {"QUEUED"}, "PUBLISHING")
        worker = self.worker()
        worker.set_slots(3)
        worker._controls[publishing["id"]] = ProcessControl()  # its thread is moving the file into input
        probing = []

        def blocked_probe(task, control):
            probing.append(task["id"])
            deadline = time.monotonic() + WAIT
            while not control.requested and time.monotonic() < deadline:
                time.sleep(0.01)
            return None

        with mock.patch.object(worker, "_probe", side_effect=blocked_probe):
            self.assertEqual(sorted(worker.dispatch()), sorted([first["id"], second["id"]]))
            deadline = time.monotonic() + WAIT
            while len(probing) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            worker.group_action(group["id"], "cancel")  # the running ones are asked and waited for
        self.assertEqual(self.states([first, second, publishing]), ["CANCELLED", "CANCELLED", "PUBLISHING"])
        # A failed publish leaves an unfinished episode: the next pass cancels it.
        del worker._controls[publishing["id"]]
        self.store.transition(publishing["id"], {"PUBLISHING"}, "INTERRUPTED")
        worker.dispatch()
        self.assertEqual(self.state(publishing), "CANCELLED")

    def test_a_published_episode_stays_completed(self):
        group, (publishing, other) = self.group_with_tasks(2)
        self.store.transition(publishing["id"], {"QUEUED"}, "PUBLISHING")
        worker = self.worker()
        worker._controls[publishing["id"]] = ProcessControl()
        worker.group_action(group["id"], "cancel")
        self.assertEqual(self.states([publishing, other]), ["PUBLISHING", "CANCELLED"])
        del worker._controls[publishing["id"]]
        self.store.transition(publishing["id"], {"PUBLISHING"}, "COMPLETED")
        worker.dispatch()
        self.assertEqual(self.state(publishing), "COMPLETED")

    def test_a_held_part_file_keeps_cancelling_until_it_can_go_even_across_a_restart(self):
        group, (held, free) = self.group_with_tasks(2)
        self.store.transition(held["id"], {"QUEUED"}, "STOPPED")
        folder, handle = self.held(held)
        worker = self.worker()
        worker.group_action(group["id"], "cancel")
        self.assertEqual(self.states([held, free]), ["CANCELLING", "CANCELLED"])
        self.assertIn("chưa xóa được file tạm", self.store.get(held["id"])["error_message"])
        with self.assertRaises(DownloadActionError):
            worker.group_action(group["id"], "remove")  # never removed while a part file is left
        worker = self.restart()  # still held: never reported CANCELLED on a guess
        self.assertEqual(self.state(held), "CANCELLING")
        worker.dispatch()
        self.assertEqual(self.state(held), "CANCELLING")
        handle.close()
        worker.dispatch()  # the clean-up retry of the existing path
        self.assertEqual(self.state(held), "CANCELLED")
        self.assertFalse(folder.exists())
        self.assertTrue(worker.group_action(group["id"], "remove")["removed"])
        self.assertEqual((self.store.get(held["id"]), self.store.get(free["id"])), (None, None))

    def test_resume_and_retry_of_a_cancelled_groups_episode_are_refused_clearly(self):
        group, (stopped, queued) = self.group_with_tasks(2)
        self.store.transition(stopped["id"], {"QUEUED"}, "STOPPED")
        worker = self.worker()
        self.interrupted(worker, group["id"], "cancel")  # stored: the group is CANCELLED, no episode touched
        self.assertEqual(self.states([stopped, queued]), ["STOPPED", "QUEUED"])
        for action in (worker.resume, worker.retry):
            with self.assertRaises(DownloadActionError) as caught:
                action(stopped["id"])
            self.assertEqual(caught.exception.status, 409)
            self.assertIn("đã hủy", str(caught.exception))
        self.assertEqual(self.state(stopped), "STOPPED")  # never reopened, so never a QUEUED → CANCELLED loop
        started = self.no_start(worker)
        self.assertEqual(worker.dispatch(), [])
        self.assertEqual((self.states([stopped, queued]), started), (["CANCELLED", "CANCELLED"], []))
        with self.assertRaises(DownloadActionError):
            worker.retry(stopped["id"])
        for action in ("stop", "resume", "retry"):
            with self.assertRaises(GroupError) as refused:
                worker.group_action(group["id"], action)
            self.assertEqual((refused.exception.code, refused.exception.status), ("GROUP_CANCELLED", 409))
        self.assertEqual(worker.group_action(group["id"], "cancel")["group"]["state"], "CANCELLED")  # a repeat
        self.assertTrue(worker.group_action(group["id"], "remove")["removed"])


class GroupIntentRecoveryTest(IntentCase):
    def test_a_cancel_interrupted_after_some_episodes_finishes_after_a_restart(self):
        group, tasks = self.group_with_tasks(4)
        self.store.transition(tasks[3]["id"], {"QUEUED"}, "STOPPED")
        self.interrupted(self.worker(), group["id"], "cancel", after=2)
        self.assertEqual(self.states(tasks), ["CANCELLED", "CANCELLED", "QUEUED", "STOPPED"])
        worker = self.restart()  # recovery settles the stored cancel before anything dispatches
        self.assertEqual(self.states(tasks), ["CANCELLED"] * 4)
        started = self.no_start(worker)
        self.assertEqual((worker.dispatch(), started), ([], []))

    def test_a_dispatch_pass_settles_a_cancel_before_it_starts_or_wakes_anything(self):
        group, tasks = self.group_with_tasks(3)
        self.store.transition(tasks[2]["id"], {"QUEUED"}, "WAITING_LOGIN", login_source="alpha")
        worker = self.worker()
        self.interrupted(worker, group["id"], "cancel")
        started = self.no_start(worker)
        with mock.patch.object(worker, "_resume_waiting_logins", wraps=worker._resume_waiting_logins) as wake:
            worker.wake_logins()  # a sign-in callback that comes late
            self.assertEqual(worker.dispatch(), [])
        wake.assert_called_once()
        self.assertEqual((self.states(tasks), started), (["CANCELLED"] * 3, []))

    def test_a_cancel_that_keeps_failing_still_never_starts_or_wakes_an_episode(self):
        group, tasks = self.group_with_tasks(3)
        self.store.transition(tasks[2]["id"], {"QUEUED"}, "WAITING_LOGIN", login_source="alpha")
        worker = self.worker()
        self.interrupted(worker, group["id"], "cancel")
        started = self.no_start(worker)
        signed_in = SimpleNamespace(usable_generation=lambda: 7, account_sid=None)  # a sign-in that just landed
        with mock.patch.object(worker, "_cancel_in_group", side_effect=OSError("file held")), \
                mock.patch.object(worker.sources, "get", return_value=signed_in):
            worker.wake_logins()
            self.assertEqual(worker.dispatch(), [])
        self.assertEqual(self.states(tasks), ["QUEUED", "QUEUED", "WAITING_LOGIN"])
        self.assertEqual(started, [])
        self.assertIn("file held", worker.last_error)
        worker.dispatch()  # the cancel works again: settled
        self.assertEqual(self.states(tasks), ["CANCELLED"] * 3)

    def test_a_running_episode_with_a_stored_stop_ends_interrupted_after_a_crash(self):
        group, tasks = self.group_with_tasks(2)
        self.store.transition(tasks[0]["id"], {"QUEUED"}, "DOWNLOADING")  # its thread died with the process
        self.groups.hold(group["id"], STOPPABLE)  # Dừng nhóm stored, never applied
        self.assertEqual(len(self.groups.intents()), 2)
        worker = self.restart()
        self.assertEqual(self.states(tasks), ["INTERRUPTED", "STOPPED"])  # never queued again
        self.assertEqual(self.groups.intents(), [])
        started = self.no_start(worker)
        self.assertEqual((worker.dispatch(), started), ([], []))

    def test_a_group_retry_keeps_a_stored_stop(self):
        group, tasks = self.group_with_tasks(3)
        worker = self.worker()
        self.interrupted(worker, group["id"], "stop")
        worker.group_action(group["id"], "retry")  # nothing failed: it touches no episode and no intent
        self.assertEqual(len(self.groups.intents()), 3)
        self.restart()
        self.assertEqual(self.states(tasks), ["STOPPED"] * 3)

    def test_an_older_database_gets_the_intent_column_when_opened(self):
        group, tasks = self.group_with_tasks(2)
        self.store._connection.execute("DROP INDEX download_group_members_intent")
        self.store._connection.execute("ALTER TABLE download_group_members DROP COLUMN intent")
        self.store._connection.commit()
        self.reopen()
        columns = {row[1] for row in self.store._connection.execute("PRAGMA table_info(download_group_members)")}
        self.assertIn("intent", columns)
        self.assertEqual(len(self.groups.members(group["id"])), 2)
        self.worker().group_action(group["id"], "stop")
        self.assertEqual(self.states(tasks), ["STOPPED", "STOPPED"])

    def test_a_stop_interrupted_midway_is_finished_after_a_restart(self):
        group, tasks = self.group_with_tasks(6, tasks=4)
        self.interrupted(self.worker(), group["id"], "stop", after=1)
        self.assertEqual(self.states(tasks), ["STOPPED", "QUEUED", "QUEUED", "QUEUED"])
        worker = self.restart()
        self.assertEqual(self.states(tasks), ["STOPPED"] * 4)
        self.assertEqual((self.groups.intents(), self.groups.fill()), ([], []))  # the held members stay held
        self.assertEqual(self.groups.summary(group["id"])["counts"]["held"], 2)
        started = self.no_start(worker)
        self.assertEqual((worker.dispatch(), started), ([], []))

    def test_a_resume_interrupted_midway_is_finished_after_a_restart(self):
        group, tasks = self.group_with_tasks(4)
        worker = self.worker()
        worker.group_action(group["id"], "stop")
        self.interrupted(worker, group["id"], "resume", after=1)
        self.assertEqual(self.states(tasks), ["QUEUED", "STOPPED", "STOPPED", "STOPPED"])
        self.restart()
        self.assertEqual(self.states(tasks), ["QUEUED"] * 4)
        self.assertEqual(self.groups.intents(), [])

    def test_an_episodes_own_action_replaces_a_stored_group_intent(self):
        group, tasks = self.group_with_tasks(3)
        worker = self.worker()
        self.interrupted(worker, group["id"], "stop")
        worker.stop(tasks[1]["id"])
        worker.resume(tasks[1]["id"])  # the user's own Tiếp tục comes after the group's Dừng
        started = self.no_start(worker)
        with mock.patch.object(worker, "slots", return_value=0):  # settle only: no slot for anything
            worker.dispatch()
        self.assertEqual(self.states(tasks), ["STOPPED", "QUEUED", "STOPPED"])
        self.assertEqual(started, [])

    def test_a_crash_between_an_episodes_change_and_its_intent_clear_settles_once(self):
        group, tasks = self.group_with_tasks(3)
        worker = self.worker()
        with mock.patch.object(worker, "_clear_member_intent", side_effect=SimulatedInterruption):
            with self.assertRaises(SimulatedInterruption):
                worker.group_action(group["id"], "stop")
        self.assertEqual(self.states(tasks), ["STOPPED", "QUEUED", "QUEUED"])  # changed, its intent still stored
        self.assertEqual(len(self.groups.intents()), 3)
        self.restart()
        self.assertEqual(self.states(tasks), ["STOPPED"] * 3)
        self.assertEqual(self.groups.intents(), [])
        stops = [event for event in self.store.events(tasks[0]["id"]) if event["kind"] == "STOPPED"]
        self.assertEqual(len(stops), 1)  # applied once: the settle saw it stopped already

    def test_a_later_cancel_replaces_a_stored_stop(self):
        group, tasks = self.group_with_tasks(3)
        worker = self.worker()
        self.interrupted(worker, group["id"], "stop")
        worker.group_action(group["id"], "cancel")
        self.assertEqual(self.states(tasks), ["CANCELLED"] * 3)
        self.assertEqual(self.groups.intents(), [])


class FakeAccounts:
    """The account runtime's lifecycle surface; ``window`` set means a sign-in window still runs."""

    def __init__(self):
        self.window = threading.Event()
        self.calls: list[str] = []

    def attach(self, **kwargs):
        pass

    def stop(self, timeout=30.0):
        self.calls.append("stop")

    def busy(self):
        return self.window.is_set()

    def close(self):
        self.calls.append("close")


class ServiceStopTest(IntentCase):
    def service(self, worker, accounts=None):
        self.accounts = accounts or FakeAccounts()
        return DownloadService(self.root, store=self.store, worker=worker, cleanable=lambda: (0, 0),
                               bin_reader=None, accounts=self.accounts)

    def lingering(self, worker, task, *, delay=0.0):
        """A task thread that outlives the bounded stop and then still reads the store."""
        release, reads = threading.Event(), []

        def finishing():
            release.wait(WAIT)
            time.sleep(delay)
            reads.append(self.store.get(task["id"])["id"])

        thread = threading.Thread(target=finishing, daemon=True)
        worker._threads[task["id"]] = thread
        thread.start()
        return release, reads, thread

    @staticmethod
    def quick(worker):
        return mock.patch.object(worker, "shutdown", side_effect=lambda: DownloadWorker.shutdown(worker, 0.1))

    def test_a_task_that_outlives_the_bounded_stop_keeps_the_store_open_until_it_ends(self):
        task, = self.store.add_tasks(["https://clips.example/v/1"])
        worker = self.worker()
        service = self.service(worker)
        release, reads, thread = self.lingering(worker, task, delay=0.6)
        with self.quick(worker):
            service.stop()  # bounded: returns while the task still runs
        self.assertEqual(self.accounts.calls, ["stop"])
        self.assertFalse(service.wait_closed(0.3))
        release.set()
        thread.join(WAIT)
        self.assertTrue(service.wait_closed(WAIT))
        self.assertEqual((reads, self.accounts.calls), ([task["id"]], ["stop", "close"]))
        with self.assertRaises(sqlite3.ProgrammingError):  # closed for good, by the closer
            self.store.get(task["id"])

    def test_a_dispatch_loop_still_in_a_step_keeps_the_store_open(self):
        worker = self.worker()
        service = self.service(worker)
        entered, release = threading.Event(), threading.Event()

        def slow_pass():
            entered.set()
            release.wait(WAIT)
            return []

        with mock.patch.object(worker, "dispatch", side_effect=slow_pass):
            worker.start()
            self.assertTrue(entered.wait(WAIT))
            with self.quick(worker):
                service.stop()
            self.assertFalse(service.wait_closed(0.3))
            release.set()  # the loop's sweep after this pass still uses the store
            self.assertTrue(service.wait_closed(WAIT))
        self.assertFalse(worker._loop.is_alive())
        self.assertIsNone(worker.last_error)

    def test_a_sign_in_window_keeps_the_manager_and_store_open_until_it_ends(self):
        worker = self.worker()
        service = self.service(worker)
        self.accounts.window.set()
        service.stop()
        self.assertFalse(service.wait_closed(0.6))
        self.assertEqual(self.accounts.calls, ["stop"])
        self.accounts.window.clear()
        self.assertTrue(service.wait_closed(WAIT))
        self.assertEqual(self.accounts.calls, ["stop", "close"])

    def test_a_failed_worker_stop_still_stops_the_windows_and_closes_only_when_idle(self):
        task, = self.store.add_tasks(["https://clips.example/v/1"])
        worker = self.worker()
        service = self.service(worker)
        release, reads, thread = self.lingering(worker, task)

        def broken():
            worker._stopping.set()
            raise RuntimeError("stuck")

        with mock.patch.object(worker, "shutdown", side_effect=broken):
            with self.assertRaises(RuntimeError):
                service.stop()
        self.assertEqual(self.accounts.calls, ["stop"])
        self.assertEqual(worker.dispatch(), [])  # nothing new starts
        self.assertFalse(service.wait_closed(0.3))
        release.set()
        thread.join(WAIT)
        self.assertTrue(service.wait_closed(WAIT))
        self.assertEqual(reads, [task["id"]])

    def test_a_repeated_or_concurrent_stop_closes_everything_once(self):
        worker = self.worker()
        service = self.service(worker)
        shutdowns = []
        with mock.patch.object(worker, "shutdown", side_effect=lambda: (shutdowns.append(1), time.sleep(0.2))), \
                mock.patch.object(self.store, "close", wraps=self.store.close) as close:
            threads = [threading.Thread(target=service.stop) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(WAIT)
            service.stop()
            self.assertTrue(service.close_when_idle())
        self.assertEqual(len(shutdowns), 1)
        self.assertEqual(self.accounts.calls, ["stop", "close"])
        close.assert_called_once()

    def test_the_normal_stop_closes_at_once_and_nothing_uses_the_store_after(self):
        worker = self.worker()
        service = self.service(worker)
        worker.start()
        service.stop()
        self.assertTrue(service.wait_closed(0))
        self.assertFalse(worker._loop.is_alive())
        worker.wake_logins()  # a late sign-in callback only sets events
        self.assertEqual(worker.dispatch(), [])
        status, reply = service.handle_get("/api/downloads", "")
        self.assertEqual((status, reply["code"]), (503, "DOWNLOAD_STATE_ERROR"))

    def test_a_late_close_never_touches_a_newer_service_on_the_same_root(self):
        task, = self.store.add_tasks(["https://clips.example/v/1"])
        worker = self.worker()
        old = self.service(worker)
        release, reads, thread = self.lingering(worker, task)
        with self.quick(worker):
            old.stop()
        newer = DownloadStore(self.path)  # a restart in the same process opens its own store
        try:
            release.set()
            thread.join(WAIT)
            self.assertTrue(old.wait_closed(WAIT))
            self.assertEqual(reads, [task["id"]])  # the old task still had its own store until it ended
            self.assertEqual(newer.get(task["id"])["id"], task["id"])
        finally:
            newer.close()


if __name__ == "__main__":
    unittest.main()
