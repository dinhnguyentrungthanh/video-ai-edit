import os
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.job_store import IN_PROCESS_STATES, SCHEMA_VERSION, JobStore


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.environ.get("BILIFLOW_TEST_DATA_ROOT") or ROOT)

# Schema written by every JobStore before the click-order queue (commit f997dde).
# Older code still runs exactly this script against a migrated database.
PRE_QUEUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_info (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_key TEXT NOT NULL UNIQUE,
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL UNIQUE,
    source_size_bytes INTEGER NOT NULL,
    source_mtime_ns INTEGER NOT NULL,
    duration_seconds REAL,
    content_style TEXT NOT NULL DEFAULT 'unknown',
    profile TEXT NOT NULL DEFAULT 'careful',
    state TEXT NOT NULL DEFAULT 'DISCOVERED',
    current_stage TEXT,
    progress REAL NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 100,
    active_queue_path TEXT,
    active_revision INTEGER,
    stop_mode TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
    level TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_state_priority ON jobs(state, priority, created_at);
"""


def table_counts(path):
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )]
        return {name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]  # noqa: S608
                for name in tables}
    finally:
        connection.close()


class JobStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.source = self.root / "video.mp4"
        self.source.write_bytes(b"video")
        self.store = JobStore(self.root / "state.sqlite3")
        self.job = self.store.upsert_job(
            job_key="video-12345678", source_path=self.source,
            source_sha256="1" * 64, source_size_bytes=5,
            source_mtime_ns=self.source.stat().st_mtime_ns,
        )

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_job_round_trip_and_update(self):
        value = self.store.update_job(self.job["id"], content_style="animation", profile="fast")
        self.assertEqual(value["content_style"], "animation")
        self.assertEqual(value["profile"], "fast")

    def test_stages_keep_order_and_find_pending(self):
        self.store.replace_stages(self.job["id"], ["one", "two"])
        self.store.update_stage(self.job["id"], "one", state="COMPLETED")
        self.assertEqual(self.store.next_pending_stage(self.job["id"])["name"], "two")

    def test_ensure_stage_is_idempotent(self):
        self.store.ensure_stage(self.job["id"], "render")
        self.store.ensure_stage(self.job["id"], "render")
        self.assertEqual(len(self.store.stages(self.job["id"])), 1)

    def test_revision_activation_updates_job(self):
        revision = self.store.add_revision(self.job["id"], "reports/q.json", "REVIEW_REQUIRED", None)
        self.store.activate_revision(self.job["id"], revision)
        value = self.store.get_job(self.job["id"])
        self.assertEqual(value["active_queue_path"], "reports/q.json")
        self.assertEqual(value["active_revision"], 1)

    def test_recovery_makes_running_stage_resumable(self):
        self.store.replace_stages(self.job["id"], ["text"])
        self.store.update_stage(self.job["id"], "text", state="RUNNING", pid=123)
        self.store.update_job(self.job["id"], state="SCANNING_TEXT", current_stage="text")
        self.assertEqual(self.store.recover_interrupted(), 1)
        self.assertEqual(self.store.get_job(self.job["id"])["state"], "INTERRUPTED_RECOVERABLE")
        self.assertEqual(self.store.stage(self.job["id"], "text")["state"], "PENDING")

    def test_recovery_includes_every_in_process_state(self):
        self.assertIn("BUILDING_REVIEW", IN_PROCESS_STATES)
        self.store.replace_stages(self.job["id"], ["build_review"])
        self.store.update_stage(self.job["id"], "build_review", state="RUNNING", pid=7)
        self.store.update_job(self.job["id"], state="BUILDING_REVIEW", current_stage="build_review")
        self.assertEqual(self.store.recover_interrupted(), 1)
        job = self.store.get_job(self.job["id"])
        self.assertEqual((job["state"], job["stop_mode"]), ("INTERRUPTED_RECOVERABLE", "PAUSED"))
        self.assertEqual(self.store.stage(self.job["id"], "build_review")["state"], "PENDING")
        for state in ("QUEUED", "PAUSED", "WAITING_REVIEW", "COMPLETED"):
            self.store.update_job(self.job["id"], state=state)
            self.assertEqual(self.store.recover_interrupted(), 0)
            self.assertEqual(self.store.get_job(self.job["id"])["state"], state)

    def add_job(self, name, sha):
        return self.store.upsert_job(
            job_key=name, source_path=self.source, source_sha256=sha * 64,
            source_size_bytes=5, source_mtime_ns=1,
        )

    def test_mark_queued_numbers_clicks_and_keeps_place_without_reseq(self):
        second = self.add_job("second", "2")
        first = self.store.mark_queued(self.job["id"], reseq=True)
        other = self.store.mark_queued(second["id"], reseq=True)
        self.assertEqual((first["state"], first["queue_seq"]), ("QUEUED", 1))
        self.assertEqual(other["queue_seq"], 2)
        self.assertIsNotNone(first["queued_at"])
        # A resume keeps the place; a new click moves the job to the back.
        self.store.update_job(self.job["id"], state="PAUSED", stop_mode="PAUSED")
        kept = self.store.mark_queued(self.job["id"], reseq=False)
        self.assertEqual((kept["queue_seq"], kept["stop_mode"]), (1, None))
        self.assertEqual([job["id"] for job in self.store.queued_jobs()], [self.job["id"], second["id"]])
        moved = self.store.mark_queued(self.job["id"], reseq=True)
        self.assertEqual(moved["queue_seq"], 3)
        self.assertEqual([job["id"] for job in self.store.queued_jobs()], [second["id"], self.job["id"]])
        # A job without a place gets one even when the click keeps places.
        third = self.add_job("third", "3")
        self.assertEqual(self.store.mark_queued(third["id"], reseq=False)["queue_seq"], 4)
        # Reset fields change in the same update; unknown fields are refused.
        reset = self.store.mark_queued(second["id"], reseq=True, active_queue_path=None, progress=0.0)
        self.assertEqual(reset["queue_seq"], 5)
        with self.assertRaises(ValueError):
            self.store.mark_queued(second["id"], reseq=True, queue_seq=1)
        with self.assertRaises(ValueError):
            self.store.update_job(second["id"], queue_seq=1)
        with self.assertRaises(KeyError):
            self.store.mark_queued(999, reseq=True)

    def test_settled_jobs_give_their_place_back_and_paused_ones_keep_it(self):
        for state in ("WAITING_REVIEW", "READY_TO_EXPORT", "COMPLETED", "CANCELLED"):
            with self.subTest(state=state):
                self.store.mark_queued(self.job["id"], reseq=True)
                job = self.store.update_job(self.job["id"], state=state)
                self.assertEqual((job["queue_seq"], job["queued_at"]), (None, None))
        for state in ("PAUSED", "FAILED", "INTERRUPTED_RECOVERABLE", "PREFLIGHT"):
            with self.subTest(state=state):
                seq = self.store.mark_queued(self.job["id"], reseq=True)["queue_seq"]
                self.assertEqual(self.store.update_job(self.job["id"], state=state)["queue_seq"], seq)

    def test_a_job_requeued_by_older_code_goes_to_the_back_after_reopening(self):
        y = self.add_job("y", "2")
        z = self.add_job("z", "3")
        x = self.job["id"]
        self.store.mark_queued(x, reseq=True)
        self.store.update_job(x, state="WAITING_REVIEW")
        self.store.mark_queued(y["id"], reseq=True)
        self.store.close()
        # Older code queues with a plain state update and never writes queue_seq.
        connection = sqlite3.connect(self.root / "state.sqlite3")
        for job_id, stamp in ((x, "2099-01-01T00:00:01"), (z["id"], "2099-01-01T00:00:02")):
            connection.execute("UPDATE jobs SET state='QUEUED',updated_at=? WHERE id=?", (stamp, job_id))
        connection.commit()
        connection.close()
        self.store = JobStore(self.root / "state.sqlite3")
        self.assertEqual([job["id"] for job in self.store.queued_jobs()], [y["id"], x, z["id"]])

    def test_heartbeats_progress_and_stage_boundaries_do_not_reorder(self):
        later = self.add_job("later", "2")
        self.store.replace_stages(self.job["id"], ["one", "two"])
        self.store.mark_queued(self.job["id"], reseq=True)
        self.store.mark_queued(later["id"], reseq=True)
        self.store.update_job(self.job["id"], progress=0.5)
        self.store.update_stage(self.job["id"], "one", heartbeat_at="now", progress=0.3)
        self.assertTrue(self.store.claim_queued(self.job["id"], "SCANNING_TEXT", "one"))
        self.store.update_job(self.job["id"], state="QUEUED", current_stage=None)
        self.assertEqual(self.store.get_job(self.job["id"])["queue_seq"], 1)
        self.assertEqual([job["id"] for job in self.store.queued_jobs()], [self.job["id"], later["id"]])
        # Priority still comes first; a job without a place sorts last.
        self.store.update_job(later["id"], priority=50)
        unplaced = self.add_job("unplaced", "3")
        self.store.update_job(unplaced["id"], state="QUEUED")
        self.assertEqual(
            [job["id"] for job in self.store.queued_jobs()],
            [later["id"], self.job["id"], unplaced["id"]],
        )

    def test_claim_fails_after_a_pause_cancel_or_stop(self):
        self.store.mark_queued(self.job["id"], reseq=True)
        self.store.update_job(self.job["id"], state="PAUSED", stop_mode="PAUSED")
        self.assertFalse(self.store.claim_queued(self.job["id"], "PREFLIGHT", "preflight"))
        self.store.mark_queued(self.job["id"], reseq=False)
        self.store.update_job(self.job["id"], stop_mode="AFTER_STAGE")
        self.assertFalse(self.store.claim_queued(self.job["id"], "PREFLIGHT", "preflight"))
        self.store.mark_queued(self.job["id"], reseq=False)
        self.assertTrue(self.store.claim_queued(self.job["id"], "PREFLIGHT", "preflight"))
        job = self.store.get_job(self.job["id"])
        self.assertEqual((job["state"], job["current_stage"]), ("PREFLIGHT", "preflight"))
        self.assertFalse(self.store.claim_queued(self.job["id"], "PREFLIGHT", "preflight"))
        self.assertFalse(self.store.pause_if_queued(self.job["id"]))

    def test_watcher_stability_resets_when_size_changes(self):
        first = self.store.observe_file(self.source, 5, 1)
        second = self.store.observe_file(self.source, 6, 2)
        self.assertNotEqual(first["stable_since"], "")
        self.assertEqual(second["size_bytes"], 6)
        self.assertIsNone(second["imported_job_id"])

    def test_events_decode_payload(self):
        self.store.add_event(self.job["id"], "TEST", "ok", payload={"a": 1})
        self.assertEqual(self.store.events(self.job["id"])[0]["payload"], {"a": 1})


class QueueMigrationTests(unittest.TestCase):
    def make_old_database(self, path):
        connection = sqlite3.connect(path)
        connection.executescript(PRE_QUEUE_SCHEMA)
        connection.execute("INSERT INTO schema_info(version) VALUES (1)")
        rows = [
            (1, "COMPLETED", "2026-10-01T10:00:00"),
            (2, "QUEUED", "2026-10-02T12:00:00"),
            (3, "QUEUED", "2026-10-02T09:00:00"),
            (4, "QUEUED", "2026-10-02T09:00:00"),
        ]
        for job_id, state, updated in rows:
            connection.execute(
                """INSERT INTO jobs(id,job_key,source_path,source_sha256,source_size_bytes,
                source_mtime_ns,state,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                (job_id, f"job-{job_id}", f"input/{job_id}.mp4", str(job_id) * 64, 1, 1,
                 state, updated, updated),
            )
            connection.execute(
                "INSERT INTO events(job_id,level,event_type,message,created_at) VALUES (?,?,?,?,?)",
                (job_id, "INFO", "TEST", "old", updated),
            )
        connection.commit()
        connection.close()

    def test_old_database_gains_queue_columns_once_with_one_backup(self):
        with TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir()
            database = state / "control-center.sqlite3"
            self.make_old_database(database)
            before = table_counts(database)
            store = JobStore(database)
            try:
                columns = {row["name"] for row in store._connection.execute("PRAGMA table_info(jobs)")}
                self.assertTrue({"queue_seq", "queued_at"} <= columns)
                # Old waiting jobs keep their old order: updated_at, then id.
                self.assertEqual([job["id"] for job in store.queued_jobs()], [3, 4, 2])
                self.assertEqual(
                    {job["id"]: job["queue_seq"] for job in store.list_jobs()},
                    {1: None, 2: 3, 3: 1, 4: 2},
                )
                self.assertEqual(store.get_job(2)["queued_at"], "2026-10-02T12:00:00")
                self.assertEqual(
                    store._connection.execute("SELECT version FROM schema_info").fetchone()[0],
                    SCHEMA_VERSION,
                )
                self.assertIn("idx_jobs_queue", {
                    row[0] for row in store._connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='index'")
                })
                snapshot = store.list_jobs()
            finally:
                store.close()
            after = table_counts(database)
            for table, count in before.items():
                self.assertEqual(after[table], count, table)
            backups = sorted((state / "backups").iterdir())
            self.assertEqual(len(backups), 1)
            self.assertTrue(backups[0].name.startswith("control-center-before-queue-order-"))
            # The backup holds every old row, before any column was added.
            saved = table_counts(backups[0])
            self.assertEqual({table: saved[table] for table in before}, before)
            backup = sqlite3.connect(backups[0])
            try:
                self.assertNotIn("queue_seq", {row[1] for row in backup.execute("PRAGMA table_info(jobs)")})
            finally:
                backup.close()
            # A second open is a no-op: same rows, no further backup.
            store = JobStore(database)
            try:
                self.assertEqual(store.list_jobs(), snapshot)
            finally:
                store.close()
            self.assertEqual(len(list((state / "backups").iterdir())), 1)
            # Older code runs its own migration script and still reads every job.
            old = sqlite3.connect(database)
            try:
                old.row_factory = sqlite3.Row
                old.executescript(PRE_QUEUE_SCHEMA)
                self.assertEqual(old.execute("SELECT version FROM schema_info").fetchone()[0], 1)
                self.assertEqual(len(old.execute("SELECT * FROM jobs").fetchall()), 4)
                old.execute("UPDATE jobs SET state='QUEUED',updated_at='x' WHERE id=1")
                old.commit()
            finally:
                old.close()
            # A job queued by older code without a place is put at the back on the next open.
            store = JobStore(database)
            try:
                self.assertEqual(store.get_job(1)["queue_seq"], 4)
            finally:
                store.close()

    def test_new_database_needs_no_backup(self):
        with TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            JobStore(state / "control-center.sqlite3").close()
            JobStore(state / "control-center.sqlite3").close()
            self.assertFalse((state / "backups").exists())

    @unittest.skipUnless(
        (DATA_ROOT / "state" / "control-center.sqlite3").is_file(),
        "no Control Center database under BILIFLOW_TEST_DATA_ROOT",
    )
    def test_migration_on_a_read_only_copy_of_the_live_database(self):
        live = DATA_ROOT / "state" / "control-center.sqlite3"
        scratch = ROOT / "temp"
        scratch.mkdir(exist_ok=True)
        with TemporaryDirectory(dir=scratch) as directory:
            state = Path(directory) / "state"
            state.mkdir()
            copy = state / "control-center.sqlite3"
            source = sqlite3.connect(f"file:{live.as_posix()}?mode=ro", uri=True)
            target = sqlite3.connect(copy)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
            before = table_counts(copy)
            store = JobStore(copy)
            try:
                first = store.list_jobs()
                version = store._connection.execute("SELECT version FROM schema_info").fetchone()[0]
            finally:
                store.close()
            self.assertEqual(version, 1)
            after = table_counts(copy)
            self.assertEqual({table: after[table] for table in before}, before)
            backups = list((state / "backups").glob("*.sqlite3")) if (state / "backups").exists() else []
            self.assertLessEqual(len(backups), 1)
            store = JobStore(copy)
            try:
                self.assertEqual(store.list_jobs(), first)
            finally:
                store.close()
            self.assertEqual(
                len(list((state / "backups").glob("*.sqlite3")) if (state / "backups").exists() else []),
                len(backups),
            )


if __name__ == "__main__":
    unittest.main()
