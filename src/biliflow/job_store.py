from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
# States a worker sets while a stage subprocess (or the review build) runs.
# A restart finds them stale and turns them into resumable states.
IN_PROCESS_STATES = frozenset({
    "PREFLIGHT", "SCANNING_SAFETY", "SCANNING_TEXT", "SCANNING_LOGO",
    "LOCALIZING_REGIONS", "BUILDING_REVIEW", "AI_AUDITING", "RENDERING", "VERIFYING",
})
# Additive jobs columns for the click-order queue. SCHEMA_VERSION stays 1 so an
# older checkout can still open the database (SELECT * just returns extra keys).
QUEUE_COLUMNS = (("queue_seq", "INTEGER"), ("queued_at", "TEXT"))
# Settled states a later click always re-queues with a new place, so the job
# gives its place back. PAUSED, FAILED and INTERRUPTED_RECOVERABLE keep theirs
# for Tiếp tục / Thử lại. A job an older checkout queues again then has no
# stale place and is backfilled at the back of the queue.
QUEUE_RELEASE_STATES = frozenset({
    "WAITING_REVIEW", "READY_TO_EXPORT", "COMPLETED", "SKIPPED", "CANCELLED",
})
# A render stage in one of these states is an export request that has not
# finished: waiting, running, or failed and retryable.
RENDER_REQUEST_STATES = ("PENDING", "RUNNING", "FAILED_RETRYABLE", "FAILED")
# The only stage states retire_stage may change. RUNNING belongs to the worker
# (pause and cancel handle it) and COMPLETED is history.
RETIRABLE_STAGE_STATES = ("PENDING", "FAILED_RETRYABLE", "FAILED")
# "Dọn video gốc" history: one row per attempt to move a job's source video to
# the Windows Recycle Bin. Additive like the queue columns: the table is not in
# the _migrate script, so older code runs its own script unchanged and never
# sees it, and SCHEMA_VERSION stays 1.
SOURCE_CLEANUP_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS source_cleanups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL REFERENCES jobs(id),
        kind TEXT NOT NULL CHECK(kind IN ('EXPORTED','SKIPPED')),
        source_path TEXT NOT NULL,
        source_sha256 TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        mtime_ns INTEGER NOT NULL,
        output_path TEXT,
        output_sha256 TEXT,
        output_bytes INTEGER,
        exported_at TEXT,
        skipped_at TEXT,
        state TEXT NOT NULL CHECK(state IN ('PENDING','RECYCLED','FAILED','RESTORED')),
        verified INTEGER NOT NULL DEFAULT 0,
        recycle_record TEXT,
        error TEXT,
        created_at TEXT NOT NULL,
        finished_at TEXT,
        restored_at TEXT,
        restored_mtime_ns INTEGER
    )""",
    "CREATE INDEX IF NOT EXISTS idx_source_cleanups_job ON source_cleanups(job_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_source_cleanups_state ON source_cleanups(state)",
)
SOURCE_CLEANUP_KINDS = frozenset({"EXPORTED", "SKIPPED"})
# A finished attempt: the file left input/ (RECYCLED) or it is still there (FAILED).
SOURCE_CLEANUP_FINISH_STATES = frozenset({"RECYCLED", "FAILED"})
# The latest row of a job in one of these states locks every action on the job:
# the source is in the Recycle Bin, or the shell call has not answered yet.
SOURCE_CLEANED_STATES = ("PENDING", "RECYCLED")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class JobStore:
    """Thread-safe SQLite state for the on-demand Control Center."""

    def __init__(self, database_path: Path):
        self.path = database_path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self.path, check_same_thread=False, timeout=30,
        )
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._migrate()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _migrate(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_info (
                version INTEGER NOT NULL
            );
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
                updated_at TEXT NOT NULL,
                queue_seq INTEGER,
                queued_at TEXT
            );
            CREATE TABLE IF NOT EXISTS job_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                revision INTEGER NOT NULL,
                queue_path TEXT NOT NULL,
                queue_status TEXT,
                queue_updated_at TEXT,
                is_active INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(job_id, revision),
                UNIQUE(job_id, queue_path)
            );
            CREATE TABLE IF NOT EXISTS stages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                state TEXT NOT NULL DEFAULT 'PENDING',
                attempt INTEGER NOT NULL DEFAULT 0,
                progress REAL NOT NULL DEFAULT 0,
                pid INTEGER,
                heartbeat_at TEXT,
                started_at TEXT,
                completed_at TEXT,
                checkpoint_path TEXT,
                artifact_path TEXT,
                error TEXT,
                UNIQUE(job_id, name)
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                stage_name TEXT,
                kind TEXT NOT NULL,
                path TEXT NOT NULL,
                sha256 TEXT,
                bytes INTEGER,
                status TEXT NOT NULL DEFAULT 'VALID',
                created_at TEXT NOT NULL,
                UNIQUE(job_id, kind, path)
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
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS watcher_files (
                path TEXT PRIMARY KEY,
                size_bytes INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                stable_since TEXT NOT NULL,
                imported_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_state_priority
                ON jobs(state, priority, created_at);
            CREATE INDEX IF NOT EXISTS idx_stages_state
                ON stages(state, ordinal);
            CREATE INDEX IF NOT EXISTS idx_events_job_created
                ON events(job_id, created_at DESC);
            """
        )
        row = self._connection.execute("SELECT version FROM schema_info").fetchone()
        if row is None:
            self._connection.execute(
                "INSERT INTO schema_info(version) VALUES (?)", (SCHEMA_VERSION,)
            )
        elif int(row["version"]) != SCHEMA_VERSION:
            raise RuntimeError(
                f"Unsupported Control Center database schema: {row['version']}"
            )
        self._connection.commit()
        self._ensure_additive_schema()

    def _backup(self, label: str) -> Path:
        """Copy the live database before a structural change (never overwrites)."""
        directory = self.path.parent / "backups"
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = directory / f"{self.path.stem}-before-{label}-{stamp}{self.path.suffix}"
        counter = 1
        while target.exists():
            counter += 1
            target = directory / f"{self.path.stem}-before-{label}-{stamp}-{counter}{self.path.suffix}"
        copy = sqlite3.connect(target)
        try:
            self._connection.backup(copy)
            # A self-contained file: no -wal/-shm companions when it is opened later.
            copy.execute("PRAGMA journal_mode=DELETE")
        finally:
            copy.close()
        return target

    def _ensure_additive_schema(self) -> None:
        """Add the queue columns and the source_cleanups table to older databases.

        At most one backup per open, taken before the first change: a
        database without the queue columns gets 'queue-order' (it also lacks
        the table); one that has them but no source_cleanups table and at
        least one job gets 'source-cleanup'. A new database has no job to
        protect and gets none.

        The queue index is created only after the columns exist: an old
        database would otherwise fail on the CREATE INDEX before the ALTER
        TABLE ran.
        """
        columns = {
            row["name"] for row in self._connection.execute("PRAGMA table_info(jobs)")
        }
        tables = {
            row["name"] for row in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        has_rows = bool(self._connection.execute(
            "SELECT EXISTS(SELECT 1 FROM jobs) AS value"
        ).fetchone()["value"])
        missing = [(name, kind) for name, kind in QUEUE_COLUMNS if name not in columns]
        if missing:
            self._backup("queue-order")
        elif "source_cleanups" not in tables and has_rows:
            self._backup("source-cleanup")
        if missing:
            for name, kind in missing:
                try:
                    self._connection.execute(
                        f"ALTER TABLE jobs ADD COLUMN {name} {kind}"  # noqa: S608
                    )
                except sqlite3.OperationalError as error:
                    if "duplicate column" not in str(error).casefold():
                        raise
        # Jobs queued by older code have no place yet: keep their old order.
        waiting = self._connection.execute(
            """SELECT id FROM jobs WHERE state='QUEUED' AND queue_seq IS NULL
            ORDER BY updated_at ASC, id ASC"""
        ).fetchall()
        if waiting:
            start = int(self._connection.execute(
                "SELECT COALESCE(MAX(queue_seq),0) AS value FROM jobs"
            ).fetchone()["value"])
            for offset, row in enumerate(waiting, start=1):
                self._connection.execute(
                    """UPDATE jobs SET queue_seq=?,queued_at=COALESCE(queued_at,updated_at)
                    WHERE id=?""",
                    (start + offset, row["id"]),
                )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(state, priority, queue_seq, id)"
        )
        for statement in SOURCE_CLEANUP_SCHEMA:
            self._connection.execute(statement)
        self._connection.commit()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def setting(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._connection.execute(
                "SELECT value_json FROM settings WHERE key=?", (key,)
            ).fetchone()
        return default if row is None else json.loads(row["value_json"])

    def set_setting(self, key: str, value: Any) -> None:
        stamp = now_iso()
        with self._lock:
            self._connection.execute(
                """INSERT INTO settings(key,value_json,updated_at) VALUES (?,?,?)
                ON CONFLICT(key) DO UPDATE SET
                value_json=excluded.value_json,updated_at=excluded.updated_at""",
                (key, json.dumps(value, ensure_ascii=False), stamp),
            )
            self._connection.commit()

    def add_event(
        self, job_id: int | None, event_type: str, message: str,
        *, level: str = "INFO", payload: dict | None = None,
    ) -> None:
        with self._lock:
            self._connection.execute(
                """INSERT INTO events(job_id,level,event_type,message,payload_json,created_at)
                VALUES (?,?,?,?,?,?)""",
                (
                    job_id, level, event_type, message,
                    json.dumps(payload, ensure_ascii=False) if payload else None,
                    now_iso(),
                ),
            )
            # Keep the state DB bounded without deleting review/report evidence.
            self._connection.execute(
                """DELETE FROM events WHERE id IN (
                    SELECT id FROM events ORDER BY id DESC LIMIT -1 OFFSET 10000
                )"""
            )
            self._connection.commit()

    def upsert_job(
        self, *, job_key: str, source_path: Path, source_sha256: str,
        source_size_bytes: int, source_mtime_ns: int,
        duration_seconds: float | None = None, content_style: str = "unknown",
        profile: str = "careful", state: str = "DISCOVERED",
    ) -> dict[str, Any]:
        if content_style not in {"unknown", "animation", "live_action", "mixed"}:
            raise ValueError("Unsupported content style")
        if profile not in {"careful", "fast"}:
            raise ValueError("Unsupported processing profile")
        stamp = now_iso()
        with self._lock:
            self._connection.execute(
                """INSERT INTO jobs(
                    job_key,source_path,source_sha256,source_size_bytes,
                    source_mtime_ns,duration_seconds,content_style,profile,state,
                    created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source_sha256) DO UPDATE SET
                    source_path=excluded.source_path,
                    source_size_bytes=excluded.source_size_bytes,
                    source_mtime_ns=excluded.source_mtime_ns,
                    duration_seconds=COALESCE(excluded.duration_seconds,jobs.duration_seconds),
                    updated_at=excluded.updated_at""",
                (
                    job_key, str(source_path.resolve()), source_sha256,
                    source_size_bytes, source_mtime_ns, duration_seconds,
                    content_style, profile, state, stamp, stamp,
                ),
            )
            self._connection.commit()
            row = self._connection.execute(
                "SELECT * FROM jobs WHERE source_sha256=?", (source_sha256,)
            ).fetchone()
        assert row is not None
        return dict(row)

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM jobs ORDER BY priority, updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def queued_jobs(self) -> list[dict[str, Any]]:
        """Waiting jobs in click order: priority first, then queue_seq."""
        with self._lock:
            rows = self._connection.execute(
                """SELECT * FROM jobs WHERE state='QUEUED' AND stop_mode IS NULL
                ORDER BY priority ASC, queue_seq IS NULL, queue_seq ASC, id ASC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_queued(self, job_id: int, *, reseq: bool, **reset_fields: Any) -> dict[str, Any]:
        """Put a job in the queue; the only writer that assigns queue_seq/queued_at.

        update_job() only clears them when a job settles (QUEUE_RELEASE_STATES).

        reseq=True (Bắt đầu, Chạy lại kiểm tra, Xuất video) moves the job to the
        back of the queue. reseq=False (Tiếp tục, Thử lại bước lỗi) keeps its
        place and assigns one only when it has none. reset_fields change in the
        same UPDATE, so the worker never sees a half-reset job.
        """
        allowed = {
            "content_style", "profile", "current_stage", "progress",
            "active_queue_path", "active_revision",
        }
        unknown = set(reset_fields) - allowed
        if unknown:
            raise ValueError(f"Unsupported job fields: {sorted(unknown)}")
        stamp = now_iso()
        values: dict[str, Any] = {
            "state": "QUEUED", "stop_mode": None, "error": None,
            **reset_fields, "updated_at": stamp,
        }
        with self._lock:
            row = self._connection.execute(
                "SELECT queue_seq FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown job: {job_id}")
            if reseq or row["queue_seq"] is None:
                values["queue_seq"] = int(self._connection.execute(
                    "SELECT COALESCE(MAX(queue_seq),0)+1 AS value FROM jobs"
                ).fetchone()["value"])
                values["queued_at"] = stamp
            assignments = ",".join(f"{key}=?" for key in values)
            self._connection.execute(
                f"UPDATE jobs SET {assignments} WHERE id=?",  # noqa: S608
                (*values.values(), job_id),
            )
            self._connection.commit()
        return self.get_job(job_id)

    def claim_queued(self, job_id: int, state: str, stage: str) -> bool:
        """Atomically move a waiting job into its stage state.

        Fails when a pause, cancel or stop request landed after the scheduler
        selected the job, so that request wins over the worker.
        """
        with self._lock:
            cursor = self._connection.execute(
                """UPDATE jobs SET state=?,current_stage=?,error=NULL,updated_at=?
                WHERE id=? AND state='QUEUED' AND stop_mode IS NULL""",
                (state, stage, now_iso(), job_id),
            )
            self._connection.commit()
        return cursor.rowcount == 1

    def pause_if_queued(self, job_id: int) -> bool:
        """Take a waiting job out of the queue; False when it already started."""
        with self._lock:
            cursor = self._connection.execute(
                """UPDATE jobs SET state='PAUSED',stop_mode='PAUSED',updated_at=?
                WHERE id=? AND state='QUEUED'""",
                (now_iso(), job_id),
            )
            self._connection.commit()
        return cursor.rowcount == 1

    def get_job(self, job_id: int) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown job: {job_id}")
        return dict(row)

    def find_by_sha(self, source_sha256: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM jobs WHERE source_sha256=?", (source_sha256,)
            ).fetchone()
        return self._row(row)

    def update_job(self, job_id: int, **values: Any) -> dict[str, Any]:
        value = self._update_job(job_id, "", (), values)
        if value is None:
            raise KeyError(f"Unknown job: {job_id}")
        return value

    def update_job_if(
        self, job_id: int, *, states: Iterable[str] | None = None,
        exclude: Iterable[str] | None = None, **values: Any,
    ) -> dict[str, Any] | None:
        """update_job in one UPDATE, only while the job is in ``states`` and not in ``exclude``.

        Returns None when the job was in another state, so a concurrent
        writer (the worker claiming a job, a pause or a cancel) always wins.
        """
        condition, params = "", []
        for operator, group in (("IN", states), ("NOT IN", exclude)):
            if group is None:
                continue
            group = sorted(group)
            if not group:
                raise ValueError("State filter must not be empty")
            condition += f" AND state {operator} ({','.join('?' * len(group))})"
            params.extend(group)
        self.get_job(job_id)
        return self._update_job(job_id, condition, tuple(params), values)

    def _update_job(
        self, job_id: int, condition: str, params: tuple, values: dict[str, Any],
    ) -> dict[str, Any] | None:
        allowed = {
            "duration_seconds", "content_style", "profile", "state",
            "current_stage", "progress", "priority", "active_queue_path",
            "active_revision", "stop_mode", "error",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unsupported job fields: {sorted(unknown)}")
        if not values:
            return self.get_job(job_id)
        if values.get("state") in QUEUE_RELEASE_STATES:
            values["queue_seq"] = None
            values["queued_at"] = None
        values["updated_at"] = now_iso()
        assignments = ",".join(f"{key}=?" for key in values)
        with self._lock:
            cursor = self._connection.execute(
                f"UPDATE jobs SET {assignments} WHERE id=?{condition}",  # noqa: S608
                (*values.values(), job_id, *params),
            )
            self._connection.commit()
        if cursor.rowcount != 1:
            return None
        return self.get_job(job_id)

    def replace_stages(self, job_id: int, names: Iterable[str]) -> None:
        names = list(names)
        with self._lock:
            self._connection.execute("DELETE FROM stages WHERE job_id=?", (job_id,))
            self._connection.executemany(
                """INSERT INTO stages(job_id,name,ordinal,state)
                VALUES (?,?,?,'PENDING')""",
                ((job_id, name, ordinal) for ordinal, name in enumerate(names)),
            )
            self._connection.commit()

    def ensure_stage(self, job_id: int, name: str, ordinal: int | None = None) -> dict[str, Any]:
        """Add a durable stage without disturbing completed pipeline history."""
        with self._lock:
            if ordinal is None:
                row = self._connection.execute(
                    "SELECT COALESCE(MAX(ordinal),-1)+1 AS value FROM stages WHERE job_id=?",
                    (job_id,),
                ).fetchone()
                ordinal = int(row["value"])
            self._connection.execute(
                """INSERT INTO stages(job_id,name,ordinal,state) VALUES (?,?,?,'PENDING')
                ON CONFLICT(job_id,name) DO NOTHING""",
                (job_id, name, ordinal),
            )
            self._connection.commit()
        return self.stage(job_id, name)

    def stages(self, job_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM stages WHERE job_id=? ORDER BY ordinal", (job_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def stage(self, job_id: int, name: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM stages WHERE job_id=? AND name=?", (job_id, name)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown stage: {name}")
        return dict(row)

    def update_stage(self, job_id: int, name: str, **values: Any) -> dict[str, Any]:
        allowed = {
            "state", "attempt", "progress", "pid", "heartbeat_at",
            "started_at", "completed_at", "checkpoint_path",
            "artifact_path", "error",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unsupported stage fields: {sorted(unknown)}")
        if not values:
            return self.stage(job_id, name)
        assignments = ",".join(f"{key}=?" for key in values)
        with self._lock:
            cursor = self._connection.execute(
                f"UPDATE stages SET {assignments} WHERE job_id=? AND name=?",  # noqa: S608
                (*values.values(), job_id, name),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown stage: {name}")
            self._connection.commit()
        return self.stage(job_id, name)

    def next_pending_stage(self, job_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT * FROM stages WHERE job_id=? AND state IN ('PENDING','FAILED_RETRYABLE')
                ORDER BY ordinal LIMIT 1""",
                (job_id,),
            ).fetchone()
        return self._row(row)

    def render_request(self, job_id: int) -> dict[str, Any] | None:
        """The job's render stage while it is an unfinished export request, else None."""
        marks = ",".join("?" * len(RENDER_REQUEST_STATES))
        with self._lock:
            row = self._connection.execute(
                f"SELECT * FROM stages WHERE job_id=? AND name='render' AND state IN ({marks})",  # noqa: S608
                (job_id, *RENDER_REQUEST_STATES),
            ).fetchone()
        return self._row(row)

    def retire_stage(
        self, job_id: int, name: str, *, error: str,
        states: tuple[str, ...] = RETIRABLE_STAGE_STATES,
    ) -> str | None:
        """Cancel a stage that is still only a request; return its old state, else None.

        One conditional UPDATE: a stage the worker is running (RUNNING) or
        has finished (COMPLETED) is never changed, whatever ``states`` says.
        """
        states = tuple(states)
        if not states or not set(states) <= set(RETIRABLE_STAGE_STATES):
            raise ValueError(
                f"retire_stage only changes {', '.join(RETIRABLE_STAGE_STATES)} stages"
            )
        marks = ",".join("?" * len(states))
        with self._lock:
            row = self._connection.execute(
                "SELECT state FROM stages WHERE job_id=? AND name=?", (job_id, name)
            ).fetchone()
            if row is None or row["state"] not in states:
                return None
            cursor = self._connection.execute(
                f"""UPDATE stages SET state='CANCELLED',pid=NULL,heartbeat_at=NULL,error=?
                WHERE job_id=? AND name=? AND state IN ({marks})""",  # noqa: S608
                (error, job_id, name, *states),
            )
            self._connection.commit()
        return str(row["state"]) if cursor.rowcount == 1 else None

    def add_artifact(
        self, job_id: int, *, stage_name: str | None, kind: str, path: str,
        sha256: str | None = None, bytes_count: int | None = None,
        status: str = "VALID",
    ) -> None:
        with self._lock:
            self._connection.execute(
                """INSERT INTO artifacts(
                    job_id,stage_name,kind,path,sha256,bytes,status,created_at
                ) VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(job_id,kind,path) DO UPDATE SET
                    stage_name=excluded.stage_name,sha256=excluded.sha256,
                    bytes=excluded.bytes,status=excluded.status""",
                (
                    job_id, stage_name, kind, path, sha256, bytes_count,
                    status, now_iso(),
                ),
            )
            self._connection.commit()

    def artifacts(self, job_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM artifacts WHERE job_id=? ORDER BY id", (job_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def add_revision(
        self, job_id: int, queue_path: str, queue_status: str | None,
        queue_updated_at: str | None,
    ) -> int:
        with self._lock:
            existing = self._connection.execute(
                "SELECT revision FROM job_revisions WHERE job_id=? AND queue_path=?",
                (job_id, queue_path),
            ).fetchone()
            if existing:
                return int(existing["revision"])
            row = self._connection.execute(
                "SELECT COALESCE(MAX(revision),0)+1 AS next FROM job_revisions WHERE job_id=?",
                (job_id,),
            ).fetchone()
            revision = int(row["next"])
            self._connection.execute(
                """INSERT INTO job_revisions(
                    job_id,revision,queue_path,queue_status,queue_updated_at,is_active,created_at
                ) VALUES (?,?,?,?,?,0,?)""",
                (
                    job_id, revision, queue_path, queue_status,
                    queue_updated_at, now_iso(),
                ),
            )
            self._connection.commit()
        return revision

    def activate_revision(self, job_id: int, revision: int) -> None:
        with self._lock:
            row = self._connection.execute(
                "SELECT queue_path FROM job_revisions WHERE job_id=? AND revision=?",
                (job_id, revision),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown revision: {revision}")
            self._connection.execute(
                "UPDATE job_revisions SET is_active=0 WHERE job_id=?", (job_id,)
            )
            self._connection.execute(
                "UPDATE job_revisions SET is_active=1 WHERE job_id=? AND revision=?",
                (job_id, revision),
            )
            self._connection.execute(
                """UPDATE jobs SET active_queue_path=?,active_revision=?,updated_at=?
                WHERE id=?""",
                (row["queue_path"], revision, now_iso(), job_id),
            )
            self._connection.commit()

    def revisions(self, job_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM job_revisions WHERE job_id=? ORDER BY revision",
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def events(self, job_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("Event limit must be 1..1000")
        with self._lock:
            if job_id is None:
                rows = self._connection.execute(
                    "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT * FROM events WHERE job_id=? ORDER BY id DESC LIMIT ?",
                    (job_id, limit),
                ).fetchall()
        result = []
        for row in rows:
            value = dict(row)
            value["payload"] = (
                json.loads(value.pop("payload_json"))
                if value.get("payload_json") else None
            )
            result.append(value)
        return result

    def observe_file(self, path: Path, size_bytes: int, mtime_ns: int) -> dict[str, Any]:
        """Record a watcher observation and reset stability after any file change."""
        value = str(path.resolve())
        stamp = now_iso()
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM watcher_files WHERE path=?", (value,)
            ).fetchone()
            if row is None:
                self._connection.execute(
                    """INSERT INTO watcher_files(
                        path,size_bytes,mtime_ns,stable_since,updated_at
                    ) VALUES (?,?,?,?,?)""",
                    (value, size_bytes, mtime_ns, stamp, stamp),
                )
            elif int(row["size_bytes"]) != size_bytes or int(row["mtime_ns"]) != mtime_ns:
                self._connection.execute(
                    """UPDATE watcher_files SET size_bytes=?,mtime_ns=?,stable_since=?,
                    imported_job_id=NULL,updated_at=? WHERE path=?""",
                    (size_bytes, mtime_ns, stamp, stamp, value),
                )
            else:
                self._connection.execute(
                    "UPDATE watcher_files SET updated_at=? WHERE path=?", (stamp, value)
                )
            self._connection.commit()
            current = self._connection.execute(
                "SELECT * FROM watcher_files WHERE path=?", (value,)
            ).fetchone()
        assert current is not None
        return dict(current)

    def mark_file_imported(self, path: Path, job_id: int) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE watcher_files SET imported_job_id=?,updated_at=? WHERE path=?",
                (job_id, now_iso(), str(path.resolve())),
            )
            self._connection.commit()

    def reset_watched_file(self, path: Path) -> None:
        """Forget what the watcher knew about a path whose file left input/.

        A file copied back later then counts as new: it waits to be stable and
        is hashed again. Only an UPDATE; the watcher row is never deleted.
        """
        stamp = now_iso()
        with self._lock:
            self._connection.execute(
                """UPDATE watcher_files SET size_bytes=-1,mtime_ns=-1,imported_job_id=NULL,
                stable_since=?,updated_at=? WHERE path=?""",
                (stamp, stamp, str(Path(path).resolve(strict=False))),
            )
            self._connection.commit()

    @staticmethod
    def _cleanup_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        value = dict(row)
        value["verified"] = bool(value["verified"])
        return value

    def add_source_cleanup(
        self, *, job_id: int, kind: str, source_path: str, source_sha256: str,
        size_bytes: int, mtime_ns: int, output_path: str | None = None,
        output_sha256: str | None = None, output_bytes: int | None = None,
        exported_at: str | None = None, skipped_at: str | None = None,
    ) -> int:
        """Record a cleanup about to call the Recycle Bin (state PENDING); return its id."""
        if kind not in SOURCE_CLEANUP_KINDS:
            raise ValueError(f"Unsupported source cleanup kind: {kind}")
        with self._lock:
            self.get_job(job_id)
            try:
                cursor = self._connection.execute(
                    """INSERT INTO source_cleanups(
                        job_id,kind,source_path,source_sha256,size_bytes,mtime_ns,
                        output_path,output_sha256,output_bytes,exported_at,skipped_at,
                        state,verified,created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,'PENDING',0,?)""",
                    (
                        job_id, kind, str(source_path), str(source_sha256), int(size_bytes),
                        int(mtime_ns), output_path, output_sha256, output_bytes,
                        exported_at, skipped_at, now_iso(),
                    ),
                )
                self._connection.commit()
            except sqlite3.Error:
                self._connection.rollback()
                raise
        return int(cursor.lastrowid)

    def finish_source_cleanup(
        self, row_id: int, *, state: str, verified: bool = False,
        recycle_record: str | None = None, error: str | None = None,
    ) -> dict[str, Any] | None:
        """Settle a PENDING row as RECYCLED or FAILED; None when it is not PENDING (or unknown)."""
        if state not in SOURCE_CLEANUP_FINISH_STATES:
            raise ValueError(f"A cleanup can only finish as RECYCLED or FAILED, not {state}")
        with self._lock:
            cursor = self._connection.execute(
                """UPDATE source_cleanups SET state=?,verified=?,recycle_record=?,error=?,
                finished_at=? WHERE id=? AND state='PENDING'""",
                (state, int(bool(verified)), recycle_record, error, now_iso(), row_id),
            )
            self._connection.commit()
            if cursor.rowcount != 1:
                return None
            row = self._connection.execute(
                "SELECT * FROM source_cleanups WHERE id=?", (row_id,)
            ).fetchone()
        return self._cleanup_row(row)

    def latest_source_cleanup(self, job_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM source_cleanups WHERE job_id=? ORDER BY id DESC LIMIT 1",
                (job_id,),
            ).fetchone()
        return self._cleanup_row(row)

    def _latest_cleanup_rows(self, state: str | None = None) -> list[dict[str, Any]]:
        query = """SELECT * FROM source_cleanups
            WHERE id IN (SELECT MAX(id) FROM source_cleanups GROUP BY job_id)"""
        params: tuple = ()
        if state is not None:
            query += " AND state=?"
            params = (state,)
        with self._lock:
            rows = self._connection.execute(query + " ORDER BY id", params).fetchall()
        return [self._cleanup_row(row) for row in rows]

    def latest_source_cleanups(self) -> dict[int, dict[str, Any]]:
        """The latest cleanup row of every job that has one, keyed by job id (one query)."""
        return {int(row["job_id"]): row for row in self._latest_cleanup_rows()}

    def source_cleaned(self, job_id: int) -> bool:
        """True while the job's source is in the Recycle Bin or on its way there."""
        row = self.latest_source_cleanup(job_id)
        return row is not None and row["state"] in SOURCE_CLEANED_STATES

    def recycled_cleanups(self) -> list[dict[str, Any]]:
        """Jobs whose latest row is RECYCLED (the watcher may see the source come back)."""
        return self._latest_cleanup_rows("RECYCLED")

    def pending_source_cleanups(self) -> list[dict[str, Any]]:
        """Jobs whose latest row is still PENDING (reconciled at startup)."""
        return self._latest_cleanup_rows("PENDING")

    def mark_source_restored(self, row_id: int, *, mtime_ns: int) -> dict[str, Any] | None:
        """A RECYCLED source is back in input/ with its SHA-256: unlock the job.

        One transaction: the row becomes RESTORED and the job takes the new
        mtime, so the review page accepts the file again. None when the row
        is not RECYCLED (or unknown).
        """
        stamp = now_iso()
        with self._lock:
            try:
                cursor = self._connection.execute(
                    """UPDATE source_cleanups SET state='RESTORED',restored_at=?,restored_mtime_ns=?
                    WHERE id=? AND state='RECYCLED'""",
                    (stamp, int(mtime_ns), row_id),
                )
                if cursor.rowcount != 1:
                    self._connection.rollback()
                    return None
                row = self._connection.execute(
                    "SELECT * FROM source_cleanups WHERE id=?", (row_id,)
                ).fetchone()
                self._connection.execute(
                    "UPDATE jobs SET source_mtime_ns=?,updated_at=? WHERE id=?",
                    (int(mtime_ns), stamp, row["job_id"]),
                )
                self._connection.commit()
            except sqlite3.Error:
                self._connection.rollback()
                raise
        return self._cleanup_row(row)

    def recover_interrupted(self) -> int:
        """Convert stale in-process states into resumable stage-level states."""
        stamp = now_iso()
        states = sorted(IN_PROCESS_STATES)
        with self._lock:
            rows = self._connection.execute(
                f"SELECT id,current_stage FROM jobs WHERE state IN ({','.join('?' * len(states))})",  # noqa: S608
                states,
            ).fetchall()
            for row in rows:
                if row["current_stage"]:
                    self._connection.execute(
                        """UPDATE stages SET state='PENDING',pid=NULL,error=?,heartbeat_at=NULL
                        WHERE job_id=? AND name=? AND state='RUNNING'""",
                        ("Interrupted before Control Center restart", row["id"], row["current_stage"]),
                    )
                self._connection.execute(
                    """UPDATE jobs SET state='INTERRUPTED_RECOVERABLE',stop_mode='PAUSED',
                    error=?,updated_at=? WHERE id=?""",
                    ("Recovered after an unclean Control Center shutdown", stamp, row["id"]),
                )
            self._connection.commit()
        return len(rows)
