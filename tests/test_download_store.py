import sqlite3
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.download_links import DownloadBatchError
from biliflow.download_store import (
    FINAL_STATES,
    LOG_LINES_PER_TASK,
    MAX_UNFINISHED_TASKS,
    DownloadStore,
)


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.clock = FakeClock()
        self.store = DownloadStore(Path(self.directory.name) / "state" / "downloads.sqlite3",
                                   clock=self.clock)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def add(self, *urls):
        return self.store.add_tasks(list(urls))

    def test_a_database_made_with_the_list_of_sources_still_takes_new_links(self):
        schema = self.store._connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'download_tasks'").fetchone()[0]
        self.store.close()
        path = Path(self.directory.name) / "state" / "old.sqlite3"
        old = sqlite3.connect(path)
        # The table as it was with the list of sources: source_id NOT NULL right after id.
        old.execute(schema.replace("AUTOINCREMENT,", "AUTOINCREMENT, source_id TEXT NOT NULL,", 1))
        old.execute("INSERT INTO download_tasks (source_id, url, state, created_at, updated_at, queued_at, "
                    "state_since) VALUES ('clips', 'https://clips.example/old', 'COMPLETED', 'a', 'a', 'a', 'a')")
        old.commit()
        old.close()
        self.store = DownloadStore(path, clock=self.clock)
        with self.assertRaises(sqlite3.OperationalError):
            self.store._connection.execute("SELECT source_id FROM download_tasks")
        self.assertEqual([task["url"] for task in self.store.list_tasks()], ["https://clips.example/old"])
        added, = self.add("https://clips.example/new")
        self.assertEqual(added["state"], "QUEUED")

    def test_new_tasks_are_queued_in_order(self):
        tasks = self.add("https://clips.example/a", "https://clips.example/b")
        self.assertEqual([task["state"] for task in tasks], ["QUEUED", "QUEUED"])
        self.assertEqual([task["attempt"] for task in tasks], [1, 1])
        self.assertEqual(self.store.next_queued()["url"], "https://clips.example/a")

    def test_links_already_listed_reject_the_batch(self):
        first, = self.add("https://clips.example/a")
        with self.assertRaises(DownloadBatchError) as caught:
            self.add("https://clips.example/new", "https://clips.example/a")
        self.assertEqual(caught.exception.errors[0]["code"], "DUPLICATE_EXISTING")
        self.assertEqual(caught.exception.errors[0]["line"], 2)
        self.assertEqual(len(self.store.list_tasks()), 1)
        # A cancelled or expired row no longer blocks the same link.
        self.store.transition(first["id"], {"QUEUED"}, "CANCELLED")
        self.assertEqual(len(self.add("https://clips.example/a")), 1)

    def test_completed_rows_still_block_the_same_link(self):
        task, = self.add("https://clips.example/a")
        self.store.transition(task["id"], {"QUEUED"}, "COMPLETED")
        with self.assertRaises(DownloadBatchError):
            self.add("https://clips.example/a")

    def test_unfinished_tasks_are_capped(self):
        urls = [f"https://clips.example/{index}" for index in range(MAX_UNFINISHED_TASKS)]
        for start in range(0, len(urls), 20):
            self.store.add_tasks(urls[start:start + 20])
        with self.assertRaises(DownloadBatchError) as caught:
            self.add("https://clips.example/one-more")
        self.assertEqual(caught.exception.errors[0]["code"], "TOO_MANY_TASKS")

    def test_transition_only_from_the_allowed_states(self):
        task, = self.add("https://clips.example/a")
        self.assertIsNone(self.store.transition(task["id"], {"DOWNLOADING"}, "STOPPED"))
        self.clock.advance(minutes=5)
        moved = self.store.transition(task["id"], {"QUEUED"}, "PROBING", error_code=None)
        self.assertEqual(moved["state"], "PROBING")
        self.assertEqual(moved["state_since"], self.clock.now.isoformat())
        self.assertIsNone(moved["finished_at"])
        failed = self.store.transition(task["id"], {"PROBING"}, "FAILED", error_code="LIVE",
                                       error_message="live")
        self.assertIn("FAILED", FINAL_STATES)
        self.assertEqual(failed["finished_at"], self.clock.now.isoformat())
        self.assertEqual(failed["error_code"], "LIVE")

    def test_unknown_columns_are_refused(self):
        task, = self.add("https://clips.example/a")
        with self.assertRaises(ValueError):
            self.store.update_fields(task["id"], **{"state = 'x'; --": 1})

    def test_progress_from_an_old_attempt_is_ignored(self):
        task, = self.add("https://clips.example/a")
        self.store.transition(task["id"], {"QUEUED"}, "DOWNLOADING")
        self.assertTrue(self.store.update_progress(task["id"], 1, downloaded_bytes=10, total_bytes=100,
                                                   speed=5.0, eta=18.0))
        self.store.transition(task["id"], {"DOWNLOADING"}, "QUEUED", attempt=2)
        self.store.transition(task["id"], {"QUEUED"}, "DOWNLOADING")
        self.assertFalse(self.store.update_progress(task["id"], 1, downloaded_bytes=99, total_bytes=100,
                                                    speed=1.0, eta=1.0))
        self.assertEqual(self.store.get(task["id"])["downloaded_bytes"], 10)

    def test_log_keeps_the_last_lines_only(self):
        task, = self.add("https://clips.example/a")
        self.store.append_log(task["id"], 1, [f"line {index}" for index in range(LOG_LINES_PER_TASK + 50)])
        lines = self.store.log_lines(task["id"])
        self.assertEqual(len(lines), LOG_LINES_PER_TASK)
        self.assertEqual(lines[-1], f"line {LOG_LINES_PER_TASK + 49}")

    def test_dropping_old_attempts_keeps_the_new_events(self):
        task, = self.add("https://clips.example/a")
        self.store.add_event(task["id"], 1, "PROBE_FAILED", "old", level="ERROR")
        self.store.append_log(task["id"], 1, ["old line"])
        self.store.add_event(task["id"], 2, "QUEUED", "new")
        self.store.drop_attempts_before(task["id"], 2)
        self.assertEqual([event["kind"] for event in self.store.events(task["id"])], ["QUEUED"])
        self.assertEqual(self.store.log_lines(task["id"]), [])

    def test_delete_removes_events_and_log(self):
        task, = self.add("https://clips.example/a")
        self.store.add_event(task["id"], 1, "QUEUED", "x", payload={"a": 1})
        self.store.append_log(task["id"], 1, ["x"])
        self.store.delete_task(task["id"])
        self.assertIsNone(self.store.get(task["id"]))
        self.assertEqual(self.store.events(task["id"]), [])

    def test_json_fields_round_trip(self):
        task, = self.add("https://clips.example/a")
        entries = [{"index": 1, "title": "Phim", "duration_seconds": 5400.0}]
        self.store.update_fields(task["id"], entries=entries, verify={"ok": True})
        row = self.store.get(task["id"])
        self.assertEqual(row["entries"], entries)
        self.assertEqual(row["verify"], {"ok": True})

    def test_settings(self):
        self.assertEqual(self.store.setting("slots", "2"), "2")
        self.store.set_setting("slots", "3")
        self.assertEqual(self.store.setting("slots"), "3")

    def test_concurrent_adds_never_exceed_the_cap_or_duplicate(self):
        errors = []

        def worker(offset):
            try:
                self.store.add_tasks([f"https://clips.example/{offset}-{n}" for n in range(20)])
            except DownloadBatchError as error:
                errors.append(error)
        threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(self.store.list_tasks()), MAX_UNFINISHED_TASKS)
        self.assertEqual(len(errors), 3)


if __name__ == "__main__":
    unittest.main()
