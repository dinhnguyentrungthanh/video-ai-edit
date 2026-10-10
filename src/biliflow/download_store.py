"""SQLite state of the video downloader (``state/downloads.sqlite3``).

Kept apart from ``control-center.sqlite3``: the downloader never writes job,
review or cleanup rows. Every state change is a compare-and-set so an API call
and a worker thread can never both move the same task.

Source accounts (docs/SOURCE_ACCOUNTS_PLAN.md 9.14) add WAITING_LOGIN (a task of
an account source waits for the user's sign-in: no slot, no thread, its part
kept) and EXPANDED (a pasted film page split into an episode group: final, no
file of its own, its link released), the episode preview of a page
(``download_previews``) and the groups (``download_groups``,
``download_group_members``; download_groups.py writes them).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from biliflow.download_links import DownloadBatchError

STATES = (
    "QUEUED", "PROBING", "NEEDS_CHOICE", "WAITING_SPACE", "DOWNLOADING", "VERIFYING",
    "PUBLISHING", "COMPLETED", "STOPPED", "FAILED", "CANCELLING", "CANCELLED",
    "INTERRUPTED", "EXPIRED", "WAITING_LOGIN", "EXPANDED",
)
# A task in one of these states holds a download slot.
SLOT_STATES = frozenset({"PROBING", "WAITING_SPACE", "DOWNLOADING", "VERIFYING", "PUBLISHING",
                         "CANCELLING"})
# No process runs for these; they may be removed from the list.
FINAL_STATES = frozenset({"COMPLETED", "CANCELLED", "FAILED", "STOPPED", "INTERRUPTED", "EXPIRED", "EXPANDED"})
# Rows that no longer count toward the limit or block the same link.
CLOSED_STATES = frozenset({"COMPLETED", "CANCELLED", "EXPIRED", "EXPANDED"})
RELEASED_LINK_STATES = frozenset({"CANCELLED", "EXPIRED", "EXPANDED"})
MAX_UNFINISHED_TASKS = 100
LOG_LINES_PER_TASK = 200
_JSON_FIELDS = {"entries": "entries_json", "probe": "probe_json", "verify": "verify_json"}
_COLUMNS = frozenset({
    "attempt", "queued_at", "desired_name", "original_title", "video_id", "duration_seconds",
    "estimated_bytes", "entries_json", "chosen_entry", "probe_json", "downloaded_bytes",
    "total_bytes", "speed", "eta", "error_code", "error_message", "temp_dir", "temp_file",
    "output_path", "output_sha256", "output_size", "verify_json", "name_locked", "pid",
    "pid_created", "progress_basis", "fragments_done", "fragments_total", "transfer_stage",
    "group_id", "member_id", "item_key", "account_owner", "login_source", "login_generation", "login_reason",
})
# Added after D5 (source providers) and M4 (source accounts); older databases get them on open (ALTER TABLE ADD
# COLUMN, no data change).
_ADDED_COLUMNS = {"progress_basis": "TEXT", "fragments_done": "INTEGER", "fragments_total": "INTEGER",
                  "transfer_stage": "TEXT", "group_id": "INTEGER", "member_id": "INTEGER", "item_key": "TEXT",
                  "account_owner": "TEXT", "login_source": "TEXT", "login_generation": "INTEGER",
                  "login_reason": "TEXT"}
# The episode preview of a pasted film page and the episode groups (download_groups.py).
_GROUP_SCHEMA = """
CREATE UNIQUE INDEX IF NOT EXISTS download_tasks_member ON download_tasks(member_id) WHERE member_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS download_tasks_item ON download_tasks(item_key) WHERE item_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS download_previews (
    task_id INTEGER PRIMARY KEY REFERENCES download_tasks(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    listing_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    draft_json TEXT,
    revision INTEGER NOT NULL DEFAULT 0,
    summary_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS download_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_task_id INTEGER UNIQUE,
    source_id TEXT NOT NULL,
    source_label TEXT NOT NULL,
    account_owner TEXT,
    film TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    mode TEXT NOT NULL,
    complete INTEGER NOT NULL,
    reasons_json TEXT,
    note TEXT,
    total INTEGER NOT NULL,
    width INTEGER NOT NULL,
    request_key TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL,
    state TEXT NOT NULL,
    existing_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS download_group_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES download_groups(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    item_key TEXT NOT NULL,
    selection_json TEXT NOT NULL,
    season_number INTEGER,
    season_label TEXT,
    episode_number INTEGER,
    episode_label TEXT NOT NULL,
    special INTEGER NOT NULL DEFAULT 0,
    variant_label TEXT NOT NULL,
    code TEXT NOT NULL,
    status TEXT NOT NULL,
    task_id INTEGER,
    last_state TEXT,
    intent TEXT,
    UNIQUE (group_id, ordinal),
    UNIQUE (group_id, item_key)
);
CREATE INDEX IF NOT EXISTS download_group_members_status ON download_group_members(status, group_id, ordinal);
CREATE INDEX IF NOT EXISTS download_group_members_item ON download_group_members(item_key);
CREATE INDEX IF NOT EXISTS download_group_members_task ON download_group_members(task_id);
"""


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
                pid_created REAL,
                progress_basis TEXT,
                fragments_done INTEGER,
                fragments_total INTEGER,
                transfer_stage TEXT
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
        columns = {row[1] for row in self._connection.execute("PRAGMA table_info(download_tasks)")}
        if "source_id" in columns:  # the list of sources, dropped after D4 (test roots only)
            self._connection.execute("ALTER TABLE download_tasks DROP COLUMN source_id")
        for name, kind in _ADDED_COLUMNS.items():
            if name not in columns:
                self._connection.execute(f"ALTER TABLE download_tasks ADD COLUMN {name} {kind}")
        self._connection.commit()
        self._connection.executescript(_GROUP_SCHEMA)
        previews = {row[1] for row in self._connection.execute("PRAGMA table_info(download_previews)")}
        if "summary_json" not in previews:  # a preview table of an earlier M4 build (test roots only)
            self._connection.execute("ALTER TABLE download_previews ADD COLUMN summary_json TEXT")
        members = {row[1] for row in self._connection.execute("PRAGMA table_info(download_group_members)")}
        if "intent" not in members:  # the stored Dừng/Tiếp tục of a group, added by the M4 review fix
            self._connection.execute("ALTER TABLE download_group_members ADD COLUMN intent TEXT")
        self._connection.execute("CREATE INDEX IF NOT EXISTS download_group_members_intent "
                                 "ON download_group_members(intent) WHERE intent IS NOT NULL")
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

    def add_tasks(self, urls: list[str], owners: list[str | None] | None = None) -> list[dict[str, Any]]:
        """Insert a validated batch; a listed link or the 100-task cap rejects it all. An episode task of a
        group (``item_key``) never blocks its film page's link: episodes share it. ``owners``: the Windows
        account (SID) of each link an account source claims, else None."""
        if owners is not None and len(owners) != len(urls):
            raise ValueError("One owner per link")
        with self._lock:
            marks = ", ".join("?" for _ in RELEASED_LINK_STATES)
            open_rows = {
                row["url"]: row["id"] for row in self._connection.execute(
                    f"SELECT id, url FROM download_tasks WHERE state NOT IN ({marks}) AND item_key IS NULL",
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
            unfinished = self.unfinished_count()
            if unfinished + len(urls) > MAX_UNFINISHED_TASKS:
                message = (f"Tối đa {MAX_UNFINISHED_TASKS} lượt chưa xong; đang có {unfinished}, "
                           f"lô này thêm {len(urls)}.")
                raise DownloadBatchError(message, [{"line": 0, "code": "TOO_MANY_TASKS", "message": message}])
            now = self._now()
            ids = []
            with self._connection:
                for url, owner in zip(urls, owners or [None] * len(urls)):
                    cursor = self._connection.execute(
                        "INSERT INTO download_tasks (url, state, created_at, updated_at, "
                        "queued_at, state_since, account_owner) VALUES (?, 'QUEUED', ?, ?, ?, ?, ?)",
                        (url, now, now, now, now, owner),
                    )
                    ids.append(int(cursor.lastrowid))
            return [self.get(task_id) for task_id in ids]

    def unfinished_count(self) -> int:
        """Rows that count toward MAX_UNFINISHED_TASKS (every state but the closed ones)."""
        marks = ", ".join("?" for _ in CLOSED_STATES)
        with self._lock:
            return int(self._connection.execute(
                f"SELECT COUNT(*) FROM download_tasks WHERE state NOT IN ({marks})", tuple(sorted(CLOSED_STATES)),
            ).fetchone()[0])

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
                        total_bytes: int | None, speed: float | None, eta: float | None,
                        progress_basis: str | None = None, fragments_done: int | None = None,
                        fragments_total: int | None = None, transfer_stage: str | None = None) -> bool:
        """Only the running attempt, only while DOWNLOADING; ``downloaded_bytes`` is always real bytes."""
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE download_tasks SET downloaded_bytes = ?, total_bytes = ?, speed = ?, eta = ?, "
                "progress_basis = ?, fragments_done = ?, fragments_total = ?, transfer_stage = ?, "
                "updated_at = ? WHERE id = ? AND attempt = ? AND state = 'DOWNLOADING'",
                (downloaded_bytes, total_bytes, speed, eta, progress_basis, fragments_done, fragments_total,
                 transfer_stage, self._now(), task_id, attempt),
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
        """Drop a row; an episode of a group keeps its last state in its member row (the group's count)."""
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE download_group_members SET last_state = (SELECT state FROM download_tasks WHERE id = ?) "
                "WHERE task_id = ?", (task_id, task_id))
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
