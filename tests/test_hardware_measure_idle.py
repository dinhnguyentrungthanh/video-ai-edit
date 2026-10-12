"""The idle check of the hardware measurement (docs/HARDWARE_PROFILE.md 6.3 step 3, section 9 "E2, đo").

Temporary roots under the worktree's temp folder, with databases made by the real JobStore and
DownloadStore (then closed) so the read-only queries meet the real schema. No HTTP, no real
Control Center: "running" is a PID the test declares alive.
"""
from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow import hardware_idle as hi
from biliflow import recycle_bin
from biliflow.download_store import DownloadStore
from biliflow.job_store import IN_PROCESS_STATES, JobStore

TEMP_PARENT = recycle_bin.INSTALL_ROOT / "temp"
CANARY = "canary-session-token-7f3a9c"
NOW = "2026-10-11T10:00:00+07:00"


def alive(_pid: int) -> bool:
    return True


def dead(_pid: int) -> bool:
    return False


def no_database(*_args, **_kwargs):
    raise AssertionError("no database may be opened while no Control Center runs")


class IdleRootCase(unittest.TestCase):
    def setUp(self) -> None:
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        temp = TemporaryDirectory(dir=TEMP_PARENT)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "root"
        (self.root / "state").mkdir(parents=True)

    def control_center_json(self, pid: object = 4321, *, raw: bytes | None = None) -> None:
        path = self.root / "state" / "control-center.json"
        if raw is not None:
            path.write_bytes(raw)
            return
        path.write_text(json.dumps({"schema_version": 1, "pid": pid, "host": "127.0.0.1", "port": 8798,
                                    "url": "http://127.0.0.1:8798/", "token": CANARY, "started_at": NOW}),
                        encoding="utf-8")

    def queue(self, *, paused: object = True, job_state: str | None = None, stage_state: str | None = None) -> None:
        store = JobStore(self.root / "state" / "control-center.sqlite3")
        try:
            if paused is not None:
                store.set_setting("scheduler_paused", paused)
        finally:
            store.close()
        connection = sqlite3.connect(self.root / "state" / "control-center.sqlite3")
        try:
            if job_state is not None or stage_state is not None:
                connection.execute(
                    "INSERT INTO jobs (job_key, source_path, source_sha256, source_size_bytes, source_mtime_ns, "
                    "state, created_at, updated_at) VALUES ('job-a', 'input/clip-a.mp4', 'aa', 1, 1, ?, ?, ?)",
                    (job_state or "WAITING_REVIEW", NOW, NOW))
            if stage_state is not None:
                connection.execute("INSERT INTO stages (job_id, name, ordinal, state) VALUES (1, 'text', 1, ?)",
                                   (stage_state,))
            connection.commit()
        finally:
            connection.close()

    def downloads(self, *states: str) -> None:
        store = DownloadStore(self.root / "state" / "downloads.sqlite3")
        store.close()
        connection = sqlite3.connect(self.root / "state" / "downloads.sqlite3")
        try:
            for state in states:
                connection.execute(
                    "INSERT INTO download_tasks (url, state, created_at, updated_at, queued_at, state_since) "
                    "VALUES ('https://media.example/clip', ?, ?, ?, ?, ?)", (state, NOW, NOW, NOW, NOW))
            connection.commit()
        finally:
            connection.close()

    def state_files(self) -> list[str]:
        return sorted(path.name for path in (self.root / "state").iterdir())


class PidReaderTests(IdleRootCase):
    def test_the_pid_is_read_and_nothing_else_is_returned(self):
        self.control_center_json(4321)
        self.assertEqual(hi.control_center_pid(self.root), 4321)

    def test_a_missing_file_means_no_control_center(self):
        self.assertIsNone(hi.control_center_pid(self.root))

    def test_odd_pids_and_broken_files_are_unreadable_without_leaking_the_token(self):
        cases = {
            "bool": json.dumps({"pid": True, "token": CANARY}).encode(),
            "zero": json.dumps({"pid": 0, "token": CANARY}).encode(),
            "negative": json.dumps({"pid": -5, "token": CANARY}).encode(),
            "text": json.dumps({"pid": "4321", "token": CANARY}).encode(),
            "missing": json.dumps({"schema_version": 1, "port": 0, "token": CANARY}).encode(),
            "broken json": b'{"pid": 4321, "token": "' + CANARY.encode() + b'"',
            "not utf-8": b'{"pid": 4321, "token": "' + CANARY.encode() + b'\xff"}',
            "too large": json.dumps({"pid": 4321, "token": CANARY, "pad": "x" * 70000}).encode(),
        }
        for name, raw in cases.items():
            with self.subTest(case=name):
                self.control_center_json(raw=raw)
                with self.assertRaises(hi.StateUnreadable) as caught:
                    hi.control_center_pid(self.root)
                error = caught.exception
                for text in (str(error), repr(error), repr(error.args)):
                    self.assertNotIn(CANARY, text)
                self.assertIsNone(error.__cause__)
                self.assertIsNone(error.__context__)

    def test_an_unreadable_file_refuses_the_root_without_opening_a_database(self):
        self.control_center_json(raw=b"{not json " + CANARY.encode())
        result = hi.check_root(self.root, connect=no_database, alive=alive)
        self.assertFalse(result.idle)
        self.assertEqual(result.reason, hi.STATE_UNREADABLE)
        self.assertNotIn(CANARY, repr(result))


class IdleCheckTests(IdleRootCase):
    def test_no_control_center_means_idle_and_no_database_is_opened(self):
        self.queue(paused=False, job_state="SCANNING_TEXT", stage_state="RUNNING")  # stale rows of a crash
        before = self.state_files()
        for setup, liveness in (("no json", alive), ("dead pid", dead)):
            with self.subTest(case=setup):
                if setup == "dead pid":
                    self.control_center_json(4321)
                result = hi.check_root(self.root, connect=no_database, alive=liveness)
                self.assertTrue(result.idle)
                self.assertFalse(result.contended)
                self.assertFalse(result.control_center)
        self.assertEqual(sorted(set(self.state_files()) - {"control-center.json"}), before)

    def test_a_running_control_center_needs_a_paused_idle_queue(self):
        cases = (
            ({"paused": True}, None),
            ({"paused": False}, hi.NOT_PAUSED),
            ({"paused": None}, hi.NOT_PAUSED),  # never set: the scheduler runs
            ({"paused": 1}, hi.NOT_PAUSED),  # only the JSON true pauses
            ({"paused": True, "job_state": "WAITING_REVIEW"}, None),
            ({"paused": True, "stage_state": "COMPLETED"}, None),
            ({"paused": True, "stage_state": "RUNNING"}, hi.STAGE_RUNNING),
        ) + tuple(({"paused": True, "job_state": state}, hi.JOB_RUNNING) for state in sorted(IN_PROCESS_STATES))
        for setup, reason in cases:
            with self.subTest(setup=setup):
                for name in ("control-center.sqlite3", "control-center.sqlite3-wal", "control-center.sqlite3-shm"):
                    (self.root / "state" / name).unlink(missing_ok=True)
                self.queue(**setup)
                self.control_center_json(4321)
                result = hi.check_root(self.root, alive=alive)
                self.assertEqual(result.reason, reason)
                self.assertEqual(result.idle, reason is None)

    def test_databases_are_opened_read_only(self):
        self.queue(paused=True)
        self.downloads()
        self.control_center_json(4321)
        opened: list[tuple[str, dict]] = []

        def recording(database, **kwargs):
            opened.append((database, kwargs))
            return sqlite3.connect(database, **kwargs)

        hi.check_root(self.root, connect=recording, alive=alive)
        self.assertEqual(len(opened), 2)
        for uri, kwargs in opened:
            self.assertTrue(uri.startswith("file:") and uri.endswith("?mode=ro"))
            self.assertTrue(kwargs["uri"])
        with self.assertRaises(sqlite3.OperationalError):
            connection = sqlite3.connect(opened[0][0], uri=True)
            try:
                connection.execute("CREATE TABLE written (x)")
            finally:
                connection.close()

    def test_a_missing_queue_database_is_not_idle_and_is_not_created(self):
        self.control_center_json(4321)
        result = hi.check_root(self.root, alive=alive)
        self.assertEqual(result.reason, hi.STATE_UNREADABLE)
        self.assertEqual(self.state_files(), ["control-center.json"])

    def test_active_downloads_only_mark_the_measurement_contended(self):
        self.queue(paused=True)
        self.control_center_json(4321)
        for states, expected in (((), 0), (("COMPLETED", "QUEUED", "FAILED"), 0),
                                 (("DOWNLOADING", "PROBING", "COMPLETED"), 2)):
            with self.subTest(states=states):
                for name in ("downloads.sqlite3", "downloads.sqlite3-wal", "downloads.sqlite3-shm"):
                    (self.root / "state" / name).unlink(missing_ok=True)
                self.downloads(*states)
                result = hi.check_root(self.root, alive=alive)
                self.assertTrue(result.idle)
                self.assertEqual(result.downloads, expected)
                self.assertEqual(result.contended, expected > 0)

    def test_no_downloads_database_counts_zero_and_an_unreadable_one_is_contended(self):
        self.queue(paused=True)
        self.control_center_json(4321)
        self.assertEqual(hi.check_root(self.root, alive=alive).downloads, 0)
        self.assertFalse((self.root / "state" / "downloads.sqlite3").exists())
        (self.root / "state" / "downloads.sqlite3").write_bytes(b"not a database" * 100)
        result = hi.check_root(self.root, alive=alive)
        self.assertTrue(result.idle)
        self.assertIsNone(result.downloads)
        self.assertTrue(result.contended)

    def test_the_report_is_idle_only_when_every_root_is(self):
        idle = hi.RootIdle(self.root, False, None, 0)
        busy = hi.RootIdle(self.root, True, hi.NOT_PAUSED, 0)
        busy_downloads = hi.RootIdle(self.root, True, None, 3)
        self.assertTrue(hi.IdleReport((idle, busy_downloads)).idle)
        self.assertTrue(hi.IdleReport((idle, busy_downloads)).contended)
        self.assertFalse(hi.IdleReport((idle, busy)).idle)
        self.assertIs(hi.IdleReport((idle, busy)).busy_root, busy)


if __name__ == "__main__":
    unittest.main()
