"""Automatic retries of passing network errors and per-host pacing of source lookups (docs/VIDEO_DOWNLOAD_PLAN.md
4.15; the user's choices of 2026-10-13).

A probe or a download that ends on a passing network error (a dropped or reset connection, a timeout, a failed
name lookup, HTTP 429/502/503/504) waits in WAITING_RETRY instead of ending: no slot, no thread, no browser, its
part kept. It goes back to the end of the queue after 20, 60 and 180 s (each times 0.8–1.2), or after the
Retry-After of a 429/503 answer when that is longer. A fourth such error in the same step, or a server asking for
more than 15 minutes, ends the task as before (a probe FAILED, a download INTERRUPTED) with a note. A probe is
tried again from the start; a download continues its part like Tiếp tục. A restart of the Control Center keeps
the wait (``retry_at`` is stored). Lasting errors (404/410/451, 401/403/407, a sign-in, TLS, the network policy,
DRM, a full disk, a changed source, HTTP 500) are never retried.

Source lookups (a probe, and a provider's fresh link before a transfer) run one at a time per host of the pasted
link, the next one at least ``host_gap`` seconds after the previous one ended. A queued task whose host is busy
does not take a slot meanwhile; file transfers keep the slots as before.

``retry_network`` is "Thử lại các lượt lỗi mạng" (the user's button): a task that ended FAILED on a network error
starts again (Thử lại), an INTERRUPTED one continues its part (Tiếp tục).
"""
from __future__ import annotations

import math
import random
import threading
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit

from biliflow.download_http import Cancelled, Interruptible

RETRY_SCHEDULE = (20.0, 60.0, 180.0)
RETRY_JITTER = 0.2
MAX_SERVER_WAIT_SECONDS = 900.0
HOST_GAP_SECONDS = 5.0
PACE_POLL_SECONDS = 0.25
TRANSIENT_CODES = frozenset({"NETWORK", "DNS_FAILED"})
BUSY_STATUSES = frozenset({429, 502, 503, 504})
# What "Thử lại các lượt lỗi mạng" picks up: the stored code only (a stored task keeps no HTTP status).
NETWORK_CODES = frozenset({"NETWORK", "DNS_FAILED", "SERVER_BUSY"})
RETRY_FIELDS = {"retry_count": None, "retry_at": None, "retry_phase": None}


@dataclass(frozen=True)
class NetworkPolicy:
    schedule: tuple[float, ...] = RETRY_SCHEDULE
    jitter: float = RETRY_JITTER
    max_server_wait: float = MAX_SERVER_WAIT_SECONDS
    host_gap: float = HOST_GAP_SECONDS


def is_transient(code: str | None, status: int | None = None) -> bool:
    """A network error that may pass by itself: no answer at all, or a busy server (429, 502, 503, 504; a busy
    error without a status is a source's own wait, e.g. another run of an account source)."""
    if code in TRANSIENT_CODES:
        return True
    return code == "SERVER_BUSY" and (status is None or status in BUSY_STATUSES)


def retry_delay(policy: NetworkPolicy, done: int, *, retry_after: float | None = None,
                rand: float = 0.5) -> float | None:
    """Seconds before automatic retry number ``done + 1`` (``rand`` in [0, 1] picks the jitter), never sooner than
    the server's Retry-After; None when none follows (all used, or the server asks for more than the limit)."""
    if done >= len(policy.schedule) or (retry_after is not None and retry_after > policy.max_server_wait):
        return None
    delay = policy.schedule[done] * (1.0 - policy.jitter + 2.0 * policy.jitter * rand)
    return delay if retry_after is None else max(delay, retry_after)


def lookup_host(url: str) -> str:
    """The host whose lookups are paced: the pasted link's (already checked and normalized when it was added)."""
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


class HostPacer:
    """One source lookup at a time per host, the next ``gap`` seconds after the previous one ended. A run waiting
    in its thread (``wait``: a fresh link before a transfer, which already holds a slot) goes before a new probe
    the dispatcher would start (``try_acquire``)."""

    def __init__(self, gap: float, *, clock: Callable[[], float] = time.monotonic):
        self.gap = gap
        self.clock = clock
        self._lock = threading.Lock()
        self._holders: dict[str, int] = {}
        self._ended: dict[str, float] = {}
        self._waiting: Counter[str] = Counter()

    def try_acquire(self, host: str, owner: int, *, waiting: bool = False) -> bool:
        with self._lock:
            holder = self._holders.get(host)
            if holder is not None:
                return holder == owner
            if not waiting and self._waiting[host]:
                return False
            ended = self._ended.get(host)
            if ended is not None and self.clock() - ended < self.gap:
                return False
            self._holders[host] = owner
            return True

    def release(self, host: str, owner: int) -> None:
        with self._lock:
            if self._holders.get(host) == owner:
                del self._holders[host]
                self._ended[host] = self.clock()

    def waiting(self, host: str) -> int:
        with self._lock:
            return self._waiting[host]

    def wait(self, host: str, owner: int, control: Interruptible, *, poll: float = PACE_POLL_SECONDS) -> bool:
        """Block until ``owner`` holds the host; False when a stop, a cancel or a shutdown came first."""
        with self._lock:
            self._waiting[host] += 1
        try:
            while not self.try_acquire(host, owner, waiting=True):
                if control.wait(poll):
                    return False
            return True
        finally:
            with self._lock:
                self._waiting[host] -= 1
                if self._waiting[host] <= 0:
                    del self._waiting[host]


def _due(value: Any) -> datetime | None:
    """A stored ``retry_at``; one without a zone is read as UTC (the store writes UTC), an unreadable one is None."""
    try:
        when = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)


class DownloadRetrySteps:
    """Mixin of DownloadWorker (store, groups, _event, _note_error, retry and resume come from the worker)."""

    network_policy: NetworkPolicy | None
    pacer: HostPacer | None
    retry_random: Callable[[], float]
    _leases: dict[int, str]

    def _init_retries(self, policy: NetworkPolicy | None) -> None:
        self.network_policy = policy
        self.pacer = HostPacer(policy.host_gap) if policy is not None else None
        self.retry_random = random.random
        self._leases = {}  # task id -> host whose lookup lease the dispatcher took for its probe

    def auto_retries(self) -> int:
        return len(self.network_policy.schedule) if self.network_policy is not None else 0

    # ------------------------------------------------------------------ waiting
    def _retry_later(self, task_id: int, phase: str, code: str | None, message: str | None, *,
                     status: int | None = None, retry_after: float | None = None) -> str | None:
        """A failure of ``phase`` (PROBING or DOWNLOADING). None: the task now waits in WAITING_RETRY (or was moved
        meanwhile by the user). Otherwise the message to end it with: ``message``, with a note when its automatic
        retries are used up or the server asked for a longer wait than the policy allows."""
        policy = self.network_policy
        if policy is None or not is_transient(code, status):
            return message
        task = self.store.get(task_id)
        if task is None:
            return message
        done = int(task.get("retry_count") or 0) if task.get("retry_phase") == phase else 0
        delay = retry_delay(policy, done, retry_after=retry_after, rand=self.retry_random())
        if delay is None:
            if done < len(policy.schedule) and retry_after is not None:
                note = (f"Máy chủ hẹn thử lại sau khoảng {math.ceil(retry_after / 60)} phút nên BiliFlow không tự "
                        "thử lại.")
            elif done:
                note = f"Đã tự thử lại {done} lần."
            else:
                return message
            return f"{message or 'Lỗi mạng.'} {note}"
        due = self.store.clock() + timedelta(seconds=delay)
        moved = self.store.transition(task_id, {phase}, "WAITING_RETRY", error_code=code, error_message=message,
                                      retry_count=done + 1, retry_at=due.isoformat(), retry_phase=phase,
                                      speed=None, eta=None, pid=None, pid_created=None)
        if moved:
            self._event(moved, "AUTO_RETRY_SCHEDULED",
                        f"{message or 'Lỗi mạng.'} Tự thử lại lần {done + 1}/{len(policy.schedule)} sau "
                        f"{round(delay)} giây.", level="WARNING",
                        payload={"code": code, "retry": done + 1, "delay_seconds": round(delay)})
        return None

    def _wake_retries(self) -> list[int]:
        """Each dispatch pass (worker lock held): tasks whose wait is over go to the end of the queue. An error here
        never stops the dispatch (the tasks keep waiting; the next pass tries again)."""
        woken: list[int] = []
        try:
            now = self.store.clock()
            cancelled = self.groups.cancelled_group_ids()
            for task in self.store.tasks_in({"WAITING_RETRY"}):
                due = _due(task.get("retry_at"))
                if task.get("group_id") in cancelled or (due is not None and due > now):
                    continue
                moved = self.store.transition(task["id"], {"WAITING_RETRY"}, "QUEUED", retry_at=None,
                                              error_code=None, error_message=None, queued_at=now.isoformat())
                if moved:
                    woken.append(moved["id"])
                    count = int(moved.get("retry_count") or 0)
                    self._event(moved, "AUTO_RETRY", f"Tự thử lại lần {count}/{self.auto_retries() or count}.")
        except Exception as error:  # noqa: BLE001 - the waiting tasks stay; the snapshot shows the error
            self._note_error(f"Chờ thử lại: {type(error).__name__}: {error}")
        return woken

    def _stop_waiting_retry(self, task: dict[str, Any]) -> None:
        """A stop asked while the run was moving its task to WAITING_RETRY: the stop wins."""
        moved = self.store.transition(task["id"], {"WAITING_RETRY"}, "STOPPED", retry_at=None)
        if moved:
            self._event(moved, "STOPPED", "Đã dừng.")

    # ------------------------------------------------------------------ pacing
    def _lookup_lease(self, task: dict[str, Any]) -> bool:
        """Dispatch (worker lock held): True when the task may take a slot now. A task that still needs its probe
        takes its host's lookup lease (released when the probe ends); its host busy: False, it stays queued."""
        if self.pacer is None or (task.get("probe") or {}).get("ready"):
            return True
        host = lookup_host(task["url"])
        if not host:
            return True
        if not self.pacer.try_acquire(host, task["id"]):
            return False
        self._leases[task["id"]] = host
        return True

    def _release_lookup(self, task_id: int) -> None:
        host = self._leases.pop(task_id, None)
        if host is not None and self.pacer is not None:
            self.pacer.release(host, task_id)

    @contextmanager
    def _paced_lookup(self, task: dict[str, Any], control: Interruptible) -> Iterator[None]:
        """A lookup inside a running task (a provider's fresh link): waits for its host's turn; a stop ends the
        wait (Cancelled)."""
        pacer = self.pacer
        host = lookup_host(task["url"]) if pacer is not None else ""
        if pacer is None or not host or self._leases.get(task["id"]) == host:
            yield
            return
        if not pacer.wait(host, task["id"], control):
            raise Cancelled()
        try:
            yield
        finally:
            pacer.release(host, task["id"])

    # ------------------------------------------------------------------ "Thử lại các lượt lỗi mạng"
    def _network_failed(self) -> list[dict[str, Any]]:
        return [task for task in self.store.tasks_in({"FAILED", "INTERRUPTED"})
                if task.get("error_code") in NETWORK_CODES]

    def network_failures(self) -> int:
        return len(self._network_failed())

    def retry_network(self) -> dict[str, list[Any]]:
        """Every task that ended on a network error goes back to the queue in its order: FAILED starts again
        (Thử lại), INTERRUPTED continues its part (Tiếp tục). One that cannot follow is named with the reason."""
        from biliflow.download_worker import DownloadActionError

        retried: list[int] = []
        resumed: list[int] = []
        skipped: list[dict[str, Any]] = []
        for task in self._network_failed():
            try:
                if task["state"] == "INTERRUPTED":
                    self.resume(task["id"])
                    resumed.append(task["id"])
                else:
                    self.retry(task["id"])
                    retried.append(task["id"])
            except DownloadActionError as error:
                skipped.append({"id": task["id"], "reason": str(error)})
        return {"retried": retried, "resumed": resumed, "skipped": skipped}
