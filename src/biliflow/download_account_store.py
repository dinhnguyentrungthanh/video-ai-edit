"""SQLite rows of the source accounts: table ``source_account_state`` in ``state/downloads.sqlite3``.

One row per Windows account and source, like the vault's folders (``<user SID>/<source_id>``): an
``AccountStore`` reads and changes only the rows of the account it was opened for, so a second Windows
account running BiliFlow on the same install never changes the first account's state, generation or
sign-in, before or after it, and the first account's start-up never acts on the second one's rows.

No secret is ever stored here. A row holds the state of a source's saved session (NONE, ACTIVE, INVALID),
its generation, when the user signed in, the id of an open sign-in, the result of the last session check
and an error code (the page's text comes from the code, never from a site). The session itself is in the
vault (``download_account_vault``). Every change is a compare-and-set on the columns the caller names, so
an old callback can never overwrite a newer session.

Schema: created on open with ``CREATE TABLE IF NOT EXISTS``; primary key ``(account_sid, source_id)``.
Nothing is migrated: the unscoped ``source_accounts`` table of the unreleased M1 draft (keyed by
``source_id`` alone) only ever existed in test roots; if a database still has it, it is never read,
changed or dropped. The download tables of the same file are not touched either.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from biliflow.download_account_winsec import SID_PATTERN

TABLE = "source_account_state"
SESSION_STATES = frozenset({"NONE", "ACTIVE", "INVALID"})
_COLUMNS = frozenset({"session_state", "generation", "authenticated_at", "login_attempt", "login_started_at",
                      "check_state", "checked_at", "error_code"})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AccountStore:
    """Thread-safe rows of one Windows account (``account_sid``) in ``source_account_state``, in the same
    database file as the download tasks. The account comes from the caller's trusted side (the vault's
    process token), never from a config or a request."""

    def __init__(self, database_path: Path, account_sid: str, *, clock: Callable[[], datetime] = utc_now):
        if not isinstance(account_sid, str) or not SID_PATTERN.fullmatch(account_sid):
            raise ValueError("An account store needs the SID of the Windows account it belongs to")
        self.account_sid = account_sid
        self.path = Path(database_path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self._connection = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {TABLE} (
                    account_sid TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    session_state TEXT NOT NULL DEFAULT 'NONE',
                    generation INTEGER NOT NULL DEFAULT 0,
                    authenticated_at TEXT,
                    login_attempt TEXT,
                    login_started_at TEXT,
                    check_state TEXT,
                    checked_at TEXT,
                    error_code TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (account_sid, source_id)
                )
                """
            )
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def get(self, source_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(f"SELECT * FROM {TABLE} WHERE account_sid = ? AND source_id = ?",
                                           (self.account_sid, source_id)).fetchone()
        return dict(row) if row is not None else None

    def _write(self, sql: str, values: Sequence[Any]) -> int:
        """Run one change and commit it; on any error roll it back, so a change whose commit failed can
        never be committed later by another call on this shared connection. The number of rows changed."""
        with self._lock:
            try:
                cursor = self._connection.execute(sql, values)
                self._connection.commit()
            except sqlite3.Error:
                try:
                    self._connection.rollback()
                except sqlite3.Error:
                    pass  # the first error is the one reported; a connection that cannot roll back fails again
                raise
            return cursor.rowcount

    def ensure(self, source_id: str) -> dict[str, Any]:
        """The row of ``source_id`` for this account, created (no session) when there is none."""
        with self._lock:
            self._write(f"INSERT OR IGNORE INTO {TABLE} (account_sid, source_id, updated_at) VALUES (?, ?, ?)",
                        (self.account_sid, source_id, self.clock().isoformat()))
            row = self.get(source_id)
        if row is None:
            raise sqlite3.OperationalError(f"{TABLE} row missing right after it was created")
        return row

    def update_if(self, source_id: str, expected: Mapping[str, Any], **changes: Any) -> bool:
        """Apply ``changes`` to this account's row only while every column of ``expected`` still has that
        value (None: NULL)."""
        names = set(expected) | set(changes)
        if not names <= _COLUMNS:
            raise ValueError(f"Unknown {TABLE} columns: {sorted(names - _COLUMNS)}")
        if "session_state" in changes and changes["session_state"] not in SESSION_STATES:
            raise ValueError(f"Unknown session state {changes['session_state']!r}")
        assignments = "".join(f"{name} = ?, " for name in changes)
        conditions = "".join(f" AND {name} IS ?" for name in expected)
        values = [*changes.values(), self.clock().isoformat(), self.account_sid, source_id, *expected.values()]
        return self._write(f"UPDATE {TABLE} SET {assignments}updated_at = ? "
                           f"WHERE account_sid = ? AND source_id = ?{conditions}", values) == 1
