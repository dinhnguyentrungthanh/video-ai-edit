"""SQLite state of the video downloader (``state/downloads.sqlite3``).

Kept apart from ``control-center.sqlite3``: the downloader never writes job,
review or cleanup rows. Every state change is a compare-and-set so an API call
and a worker thread can never both move the same task.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from biliflow.download_sources import DownloadBatchError

STATES = (
    "QUEUED", "PROBING", "NEEDS_CHOICE", "WAITING_SPACE", "DOWNLOADING", "VERIFYING",
    "PUBLISHING", "COMPLETED", "STOPPED", "FAILED", "CANCELLING", "CANCELLED",
    "INTERRUPTED", "EXPIRED",
)
# A task in one of these states holds a download slot.
SLOT_STATES = frozenset({"PROBING", "WAITING_SPACE", "DOWNLOADING", "VERIFYING", "PUBLISHING",
                         "CANCELLING"})
# No process runs for these; they may be removed from the list.
FINAL_STATES = frozenset({"COMPLETED", "CANCELLED", "FAILED", "STOPPED", "INTERRUPTED", "EXPIRED"})
# Rows that no longer count toward the limit or block the same link.
CLOSED_STATES = frozenset({"COMPLETED", "CANCELLED", "EXPIRED"})
RELEASED_LINK_STATES = frozenset({"CANCELLED", "EXPIRED"})
MAX_UNFINISHED_TASKS = 100
LOG_LINES_PER_TASK = 200
_JSON_FIELDS = {"entries": "entries_json", "probe": "probe_json", "verify": "verify_json"}
_COLUMNS = frozenset({
    "attempt", "queued_at", "desired_name", "original_title", "video_id", "duration_seconds",
    "estimated_bytes", "entries_json", "chosen_entry", "probe_json", "downloaded_bytes",
    "total_bytes", "speed", "eta", "error_code", "error_message", "temp_dir", "temp_file",
    "output_path", "output_sha256", "output_size", "verify_json", "name_locked", "pid",
    "pid_created",
})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DownloadStore:
    """Thread-safe store of download tasks, their events and a bounded log."""

    def __init__(self, database_path: Path, *, clock: Callable[[], datetime] = utc_now):
        self.path = database_path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self._connection = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
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
            CREATE TABLE IF NOT EXISTS download_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                url TEXT NOT NULL,
                state TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                queued_at TEXT NOT NULL,
                state_since TEXT NOT NULL,
                finished_at TEXT,
                desired_name TEXT,
                original_title TEXT,
                video_id TEXT,
                duration_seconds REAL,
                estimated_bytes INTEGER,
                entries_json TEXT,
                chosen_entry INTEGER,
                probe_json TEXT,
                downloaded_bytes INTEGER NOT NULL DEFAULT 0,
                total_bytes INTEGER,
                speed REAL,
                eta REAL,
                error_code TEXT,
                error_message TEXT,
                temp_dir TEXT,
                temp_file TEXT,
                output_path TEXT,
                output_sha256 TEXT,
                output_size INTEGER,
                verify_json TEXT,
                name_locked INTEGER NOT NULL DEFAULT 0,
                pid INTEGER,
                pid_created REAL
            );
            CREATE INDEX IF NOT EXISTS download_tasks_state ON download_tasks(state);
            CREATE TABLE IF NOT EXISTS download_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL REFERENCES download_tasks(id) ON DELETE CASCADE,
                attempt INTEGER NOT NULL,
                kind TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                payload_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS download_events_task ON download_events(task_id);
            CREATE TABLE IF NOT EXISTS download_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL REFERENCES download_tasks(id) ON DELETE CASCADE,
                attempt INTEGER NOT NULL,
                line TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS download_log_task ON download_log(task_id);
            CREATE TABLE IF NOT EXISTS download_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self._connection.commit()

    def _now(self) -> str:
        return self.clock().isoformat()

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        task = dict(row)
        for name, column in _JSON_FIELDS.items():
            raw = task.pop(column)
            task[name] = json.loads(raw) if raw else None
        task["name_locked"] = bool(task["name_locked"])
        return task

    @staticmethod
    def _columns(fields: dict[str, Any]) -> dict[str, Any]:
        columns: dict[str, Any] = {}
        for key, value in fields.items():
            if key in _JSON_FIELDS:
                columns[_JSON_FIELDS[key]] = None if value is None else json.dumps(value, ensure_ascii=False)
            elif key in _COLUMNS:
                columns[key] = value
            else:
                raise ValueError(f"Unknown download task field: {key!r}")
        return columns

    def add_tasks(self, source_id: str, urls: list[str]) -> list[dict[str, Any]]:
        """Insert a validated batch; a listed link or the 100-task cap rejects it all."""
        with self._lock:
            open_rows = {
                row["url"]: row["id"] for row in self._connection.execute(
                    "SELECT id, url FROM download_tasks WHERE state NOT IN (?, ?)",
                    tuple(sorted(RELEASED_LINK_STATES)),
                )
            }
            errors = [
                {"line": index, "url": url[:200], "code": "DUPLICATE_EXISTING",
                 "message": f"Link đã có trong danh sách (lượt #{open_rows[url]})."}
                for index, url in enumerate(urls, start=1) if url in open_rows
            ]
            if errors:
                raise DownloadBatchError("Lô bị từ chối: có link đã nằm trong danh sách.", errors)
            unfinished = self._connection.execute(
                "SELECT COUNT(*) FROM download_tasks WHERE state NOT IN (?, ?, ?)",
                tuple(sorted(CLOSED_STATES)),
            ).fetchone()[0]
            if unfinished + len(urls) > MAX_UNFINISHED_TASKS:
                message = (f"Tối đa {MAX_UNFINISHED_TASKS} lượt chưa xong; đang có {unfinished}, "
                           f"lô này thêm {len(urls)}.")
                raise DownloadBatchError(message, [{"line": 0, "code": "TOO_MANY_TASKS", "message": message}])
            now = self._now()
            ids = []
            with self._connection:
                for url in urls:
                    cursor = self._connection.execute(
                        "INSERT INTO download_tasks (source_id, url, state, created_at, updated_at, "
                        "queued_at, state_since) VALUES (?, ?, 'QUEUED', ?, ?, ?, ?)",
                        (source_id, url, now, now, now, now),
                    )
                    ids.append(int(cursor.lastrowid))
            return [self.get(task_id) for task_id in ids]

    def get(self, task_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM download_tasks WHERE id = ?", (task_id,),
            ).fetchone()
            return self._decode(row)

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM download_tasks ORDER BY id").fetchall()
            return [self._decode(row) for row in rows]

    def tasks_in(self, states: Iterable[str]) -> list[dict[str, Any]]:
        wanted = tuple(states)
        if not wanted:
            return []
        marks = ", ".join("?" for _ in wanted)
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM download_tasks WHERE state IN ({marks}) ORDER BY id", wanted,
            ).fetchall()
            return [self._decode(row) for row in rows]

    def next_queued(self, exclude: Iterable[int] = ()) -> dict[str, Any] | None:
        """The oldest QUEUED task, skipping ``exclude`` (tasks whose last thread is still ending)."""
        skip = sorted({int(item) for item in exclude})
        where = f" AND id NOT IN ({','.join('?' * len(skip))})" if skip else ""
        with self._lock:
            row = self._connection.execute(
                f"SELECT * FROM download_tasks WHERE state = 'QUEUED'{where} ORDER BY queued_at, id LIMIT 1",
                skip,
            ).fetchone()
            return self._decode(row)

    def transition(self, task_id: int, allowed_from: Iterable[str], to_state: str,
                   **fields: Any) -> dict[str, Any] | None:
        """Move a task only when its current state is allowed; None otherwise."""
        if to_state not in STATES:
            raise ValueError(f"Unknown download state: {to_state}")
        allowed = tuple(allowed_from)
        columns = self._columns(fields)
        now = self._now()
        columns.update({"state": to_state, "updated_at": now, "state_since": now,
                        "finished_at": now if to_state in FINAL_STATES else None})
        assignments = ", ".join(f"{name} = ?" for name in columns)
        marks = ", ".join("?" for _ in allowed)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                f"UPDATE download_tasks SET {assignments} WHERE id = ? AND state IN ({marks})",
                (*columns.values(), task_id, *allowed),
            )
            if cursor.rowcount != 1:
                return None
        return self.get(task_id)

    def update_fields(self, task_id: int, **fields: Any) -> dict[str, Any] | None:
        columns = self._columns(fields)
        columns["updated_at"] = self._now()
        assignments = ", ".join(f"{name} = ?" for name in columns)
        with self._lock, self._connection:
            self._connection.execute(
                f"UPDATE download_tasks SET {assignments} WHERE id = ?", (*columns.values(), task_id),
            )
        return self.get(task_id)

    def update_fields_if(self, task_id: int, allowed_states: Iterable[str],
                         **fields: Any) -> dict[str, Any] | None:
        """Change fields only while the task is in one of ``allowed_states``."""
        allowed = tuple(allowed_states)
        columns = self._columns(fields)
        columns["updated_at"] = self._now()
        assignments = ", ".join(f"{name} = ?" for name in columns)
        marks = ", ".join("?" for _ in allowed)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                f"UPDATE download_tasks SET {assignments} WHERE id = ? AND state IN ({marks})",
                (*columns.values(), task_id, *allowed),
            )
            if cursor.rowcount != 1:
                return None
        return self.get(task_id)

    def update_progress(self, task_id: int, attempt: int, *, downloaded_bytes: int,
                        total_bytes: int | None, speed: float | None, eta: float | None) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE download_tasks SET downloaded_bytes = ?, total_bytes = ?, speed = ?, eta = ?, "
                "updated_at = ? WHERE id = ? AND attempt = ? AND state = 'DOWNLOADING'",
                (downloaded_bytes, total_bytes, speed, eta, self._now(), task_id, attempt),
            )
            return cursor.rowcount == 1

    def add_event(self, task_id: int, attempt: int, kind: str, message: str, *,
                  level: str = "INFO", payload: dict[str, Any] | None = None) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO download_events (task_id, attempt, kind, level, message, payload_json, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, attempt, kind, level, message,
                 json.dumps(payload, ensure_ascii=False) if payload else None, self._now()),
            )

    def events(self, task_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM download_events WHERE task_id = ? ORDER BY id", (task_id,),
            ).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            raw = event.pop("payload_json")
            event["payload"] = json.loads(raw) if raw else None
            events.append(event)
        return events

    def append_log(self, task_id: int, attempt: int, lines: Iterable[str]) -> None:
        batch = [(task_id, attempt, line) for line in lines]
        if not batch:
            return
        with self._lock, self._connection:
            self._connection.executemany(
                "INSERT INTO download_log (task_id, attempt, line) VALUES (?, ?, ?)", batch,
            )
            self._connection.execute(
                "DELETE FROM download_log WHERE task_id = ? AND id NOT IN ("
                "SELECT id FROM download_log WHERE task_id = ? ORDER BY id DESC LIMIT ?)",
                (task_id, task_id, LOG_LINES_PER_TASK),
            )

    def log_lines(self, task_id: int) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT line FROM download_log WHERE task_id = ? ORDER BY id", (task_id,),
            ).fetchall()
            return [row["line"] for row in rows]

    def drop_attempts_before(self, task_id: int, attempt: int) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM download_events WHERE task_id = ? AND attempt < ?", (task_id, attempt),
            )
            self._connection.execute(
                "DELETE FROM download_log WHERE task_id = ? AND attempt < ?", (task_id, attempt),
            )

    def delete_task(self, task_id: int) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM download_tasks WHERE id = ?", (task_id,))

    def setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM download_settings WHERE key = ?", (key,),
            ).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO download_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value),
            )
