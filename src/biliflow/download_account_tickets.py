"""A task's signed file link of a source account, kept in memory only (Codex's TICKET-REUSE prompt after the
user's real test, 2026-10-10): the probe's ticket serves the first transfer, and a short Dừng/Tiếp tục uses the
same link again (checked first by the bounded HTTP probe, download_source_steps) instead of a hidden browser run
for a new ticket.

The link never reaches the disk, the database, a log, an event, the API or a repr; a restart starts empty. One
entry per task, bound to the task's attempt, the probe's identity (source, film, episode, variant, version, size),
the manager that issued the ticket (project root, Windows account), the source and the generation of the session
lease the hidden run used (``ResolvedSource.issuer``). ``take`` compares all of them with the caller's current
values; the caller reads the generation from the account's row only (no vault), so a disconnect, a new sign-in,
a rejected or an expired session never matches.

The limits are the cache's own, not a guess at the ticket's life: at most ``MAX_ENTRIES`` entries, and
``IDLE_SECONDS`` without use on the monotonic clock. An entry is idle from the end of the probe that stored it (the
task waits for space or a slot) and from the end of a run that stopped or was interrupted; while a transfer uses
it, it never expires, so the cache never ends a transfer. The probe's own result serves the first transfer without a
check only within ``FRESH_SECONDS`` of the probe; a task that waited longer (for space) checks it first. A miss (expired, evicted, revoked, after a restart) only
means a new ticket through the existing bounded resolve; the part on disk is never touched here.

Races: a resolve begins with ``begin`` (a token of one sequence); ``revoke`` (cancel, remove, retry, a choice, a
sign-in wait, a task that ended) and ``clear`` (shutdown) move the sequence on, so a result whose resolve began
before them is never stored, and a result never replaces one whose resolve began later. A session change of a
source (``revoke_source``: Ngắt kết nối, a new sign-in) drops its links and refuses every result of that source
whose resolve began before it, for every task, holding a link or not; a resolve of the same run that begins later
(a refresh with the new session) may still store its link (``put``'s ``began``).
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable

from biliflow.download_source_types import ResolvedSource

MAX_ENTRIES = 100
IDLE_SECONDS = 600.0
FRESH_SECONDS = 120.0  # the probe's link serves the first transfer unchecked only this soon after the probe
MAX_REVOKED = 1000  # task ids (and sources) whose revocation is remembered; an older token is refused for all


@dataclass(frozen=True)
class Reuse:
    """A hit: the kept source and whether it is the probe's own result, not yet used by any run (``fresh``: the
    first transfer right after the probe may use it without a check)."""
    source: ResolvedSource = field(repr=False)
    fresh: bool


@dataclass
class _Entry:
    attempt: int
    identity: str
    owner: tuple[str, str] = field(repr=False)
    source_id: str
    generation: int
    source: ResolvedSource = field(repr=False)
    token: int
    fresh: bool
    idle_since: float | None
    touched: float


class TicketCache:
    """See the module docstring. Thread-safe; ``clock`` is the tests' injection (monotonic seconds)."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic, max_entries: int = MAX_ENTRIES,
                 idle_seconds: float = IDLE_SECONDS, fresh_seconds: float = FRESH_SECONDS,
                 max_revoked: int = MAX_REVOKED):
        if max_entries < 1 or idle_seconds <= 0 or fresh_seconds < 0 or max_revoked < 1:
            raise ValueError("cache limits must be positive")
        self.clock = clock
        self.max_entries = max_entries
        self.idle_seconds = idle_seconds
        self.fresh_seconds = fresh_seconds
        self.max_revoked = max_revoked
        self._lock = threading.Lock()
        self._entries: dict[int, _Entry] = {}
        self._revoked: OrderedDict[int, int] = OrderedDict()
        self._sessions: OrderedDict[tuple[tuple[str, str], str], int] = OrderedDict()  # (owner, source) -> mark
        self._sequence = 0
        self._floor = 0

    def __repr__(self) -> str:
        return f"TicketCache(entries={len(self)})"

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def begin(self) -> int:
        """The token of a resolve that begins now (``put`` refuses it once the task was revoked after this)."""
        with self._lock:
            return self._sequence

    def put(self, task_id: int, attempt: int, source: ResolvedSource, token: int, *, fresh: bool,
            began: int | None = None) -> bool:
        """Keep ``source`` for the task. ``token``: the run's (``begin`` at its start); ``began``: when this
        resolve began, if later (a refresh in the middle of the run). ``fresh``: the probe's result (idle until a
        transfer takes it); otherwise the link a running transfer uses now (in use, no expiry until the run ends).
        Only a source account's file with an issuer is kept. False when refused: the task was revoked since
        ``token``, the source's session changed since the resolve began, or a later run's link is kept."""
        issuer = source.issuer
        if issuer is None or source.transport != "http_file":
            return False
        began = token if began is None else max(token, began)
        with self._lock:
            if token < self._floor or self._revoked.get(task_id, -1) > token:
                return False
            if self._sessions.get((issuer.owner, issuer.source_id), -1) > began:
                return False
            current = self._entries.get(task_id)
            if current is not None and current.token > token:
                return False
            now = self.clock()
            self._purge(now)
            if task_id not in self._entries and len(self._entries) >= self.max_entries:
                self._evict()
            self._entries[task_id] = _Entry(
                attempt=attempt, identity=source.identity_key, owner=issuer.owner, source_id=issuer.source_id,
                generation=issuer.generation, source=source, token=token, fresh=fresh,
                idle_since=now if fresh else None, touched=now)
            return True

    def take(self, task_id: int, *, attempt: int, identity: str, owner: tuple[str, str], source_id: str,
             generation: int | None) -> Reuse | None:
        """The task's kept link when every binding matches (``generation``: the source's usable session now, None
        without one) and it has not been idle too long; it is then in use. Any mismatch drops the entry."""
        with self._lock:
            now = self.clock()
            self._purge(now)
            entry = self._entries.get(task_id)
            if entry is None:
                return None
            if (entry.attempt, entry.identity, entry.owner, entry.source_id, entry.generation) != (
                    attempt, identity, owner, source_id, generation):
                del self._entries[task_id]
                return None
            reuse = Reuse(entry.source, entry.fresh and now - entry.touched < self.fresh_seconds)
            entry.fresh, entry.idle_since, entry.touched = False, None, now
            return reuse

    def idle(self, task_id: int) -> None:
        """The task's run ended stopped or interrupted (or went back to the queue): its link starts to count idle
        time, and the next run checks it before use."""
        with self._lock:
            now = self.clock()
            entry = self._entries.get(task_id)
            if entry is not None:
                entry.fresh, entry.idle_since, entry.touched = False, now, now
            self._purge(now)

    def discard(self, task_id: int) -> None:
        """Forget the task's link (the file server refused it, or the check failed); a later resolve of the same
        run may store its new link."""
        with self._lock:
            self._entries.pop(task_id, None)

    def revoke(self, task_id: int) -> None:
        """Forget the task's link, and refuse every result of a resolve that began before now."""
        with self._lock:
            self._revoke(task_id)

    def revoke_source(self, owner: tuple[str, str], source_id: str) -> int:
        """A session change of the source (disconnect, a new sign-in): drop every link of the source and refuse
        every result of it whose resolve began before now (any task). Returns the number of links dropped."""
        with self._lock:
            self._sequence += 1
            key = (owner, source_id)
            self._sessions[key] = self._sequence
            self._sessions.move_to_end(key)
            while len(self._sessions) > self.max_revoked:
                _old, value = self._sessions.popitem(last=False)
                self._floor = max(self._floor, value)
            tasks = [task_id for task_id, entry in self._entries.items()
                     if entry.owner == owner and entry.source_id == source_id]
            for task_id in tasks:
                del self._entries[task_id]
            return len(tasks)

    def held(self) -> set[tuple[tuple[str, str], str]]:
        """The (owner, source) pairs that hold a link, after links idle past the limit have left memory."""
        with self._lock:
            self._purge(self.clock())
            return {(entry.owner, entry.source_id) for entry in self._entries.values()}

    def drop_stale(self, owner: tuple[str, str], source_id: str, generation: int | None) -> int:
        """Drop the source's links of any session other than ``generation`` (its usable one now; None without one:
        expired, rejected). They could never be taken; this only removes them from memory sooner."""
        with self._lock:
            tasks = [task_id for task_id, entry in self._entries.items() if entry.owner == owner
                     and entry.source_id == source_id and entry.generation != generation]
            for task_id in tasks:
                del self._entries[task_id]
            return len(tasks)

    def clear(self) -> None:
        """Shutdown: forget everything and refuse every result of a resolve that began before now."""
        with self._lock:
            self._sequence += 1
            self._floor = self._sequence
            self._entries.clear()
            self._revoked.clear()
            self._sessions.clear()

    # Inside (the lock is held) ---------------------------------------------------------------------------

    def _revoke(self, task_id: int) -> None:
        self._sequence += 1
        self._revoked[task_id] = self._sequence
        self._revoked.move_to_end(task_id)
        while len(self._revoked) > self.max_revoked:
            _old, value = self._revoked.popitem(last=False)
            self._floor = max(self._floor, value)  # its record is gone: refuse every token from before it
        self._entries.pop(task_id, None)

    def _purge(self, now: float) -> None:
        expired = [task_id for task_id, entry in self._entries.items()
                   if entry.idle_since is not None and now - entry.idle_since >= self.idle_seconds]
        for task_id in expired:
            del self._entries[task_id]

    def _evict(self) -> None:
        idle = [(entry.touched, task_id) for task_id, entry in self._entries.items() if entry.idle_since is not None]
        pool = idle or [(entry.touched, task_id) for task_id, entry in self._entries.items()]
        del self._entries[min(pool)[1]]
