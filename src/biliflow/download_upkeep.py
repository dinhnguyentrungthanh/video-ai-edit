"""Recovery at start, temp cleaning and the hourly sweep of the video downloader.

Split out of ``download_worker`` for size: ``DownloadWorker`` inherits these
methods, which use its store, lock, running-task table and temp helpers. Deletes
stay inside ``temp\\downloads``; files in ``input`` are never touched.
"""
from __future__ import annotations

import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import psutil

from biliflow.download_files import UnsafePathError, safe_remove_tree, sha256_file, tree_size
from biliflow.download_runner import kill_process_tree
from biliflow.download_store import SLOT_STATES

KEEP_TEMP = timedelta(days=7)
KEEP_ROWS = timedelta(days=30)
ORPHAN_AGE = timedelta(hours=24)
TEMP_CLEANABLE = frozenset({"STOPPED", "FAILED", "INTERRUPTED"})
CLOSED = frozenset({"COMPLETED", "CANCELLED", "EXPIRED"})


class DownloadUpkeep:
    """Mixin of DownloadWorker (store, _lock, _controls, downloads_dir, cache_pruner, _event,
    _fail, _note_error, _remove_temp, _temp_gone and _finish_cancel come from the worker)."""

    def temp_summary(self) -> dict[str, Any]:
        """Temp bytes of the tasks "Dọn file tạm" would clean, for the confirmation (no lock: the
        snapshot never waits for a slow step that holds it)."""
        running = self._controls.copy()
        tasks = [task for task in self.store.tasks_in(TEMP_CLEANABLE) if task["id"] not in running]
        sizes = {task["id"]: tree_size(self.downloads_dir / str(task["id"])) for task in tasks}
        return {"tasks": len(tasks), "bytes": sum(sizes.values()), "ids": sorted(sizes),
                "total_temp_bytes": tree_size(self.downloads_dir)}

    def cleanup_temp(self, ids: Iterable[int] | None = None) -> dict[str, int]:
        """With ``ids`` (the tasks the confirmation listed), only those: a task stopped or failed
        while the dialog was open keeps its part."""
        wanted = None if ids is None else set(ids)
        freed = count = 0
        with self._lock:
            for task in self.store.tasks_in(TEMP_CLEANABLE):
                if task["id"] in self._controls or (wanted is not None and task["id"] not in wanted):
                    continue
                freed += self._remove_temp(task)
                if not self._temp_gone(task["id"]):
                    continue  # still held: stays as it is, the next clean tries again
                moved = self.store.transition(task["id"], TEMP_CLEANABLE, "EXPIRED", temp_file=None)
                if moved:
                    self.tickets.revoke(task["id"])  # the part is gone: Thử lại starts with a new ticket
                    count += 1
                    self._event(moved, "EXPIRED", "Đã dọn file tạm theo yêu cầu; thử lại sẽ tải từ đầu.")
        return {"tasks": count, "freed_bytes": freed}

    # ------------------------------------------------------- recovery, sweeping
    def _kill_leftover(self, task: dict[str, Any]) -> None:
        pid, created = task["pid"], task["pid_created"]
        if not pid:
            return
        try:
            process = psutil.Process(int(pid))
            same = bool(created) and abs(process.create_time() - float(created)) < 1.0
        except psutil.Error:
            return
        if same:
            kill_process_tree(int(pid))
            self._event(task, "LEFTOVER_KILLED", "Đã dừng tiến trình tải còn sót từ lần chạy trước.",
                        level="WARNING")

    def recover(self) -> dict[str, int]:
        """Settle tasks a previous Control Center left running; never restart a download. Then the group actions
        the user gave that a previous run did not finish (``_settle_groups``: a cancelled group's episodes are
        cancelled, a stored Dừng / Tiếp tục nhóm reaches its stopped or queued episodes) before anything
        dispatches; it never queues a task that was running (only WAITING_SPACE, which has no part file yet, goes
        back to QUEUED)."""
        counts: Counter[str] = Counter()
        for task in self.store.tasks_in(SLOT_STATES):  # every running state, PUBLISHING, CANCELLING
            try:
                moved = self._recover_one(task)
            except Exception as error:  # noqa: BLE001 - the loop's _reconcile tries this task again
                self._note_error(f"Lượt {task['id']}: {type(error).__name__}: {error}")
                continue
            if moved:
                counts[f"{task['state']}->{moved['state']}"] += 1
        try:
            self._settle_groups()
        except Exception as error:  # noqa: BLE001 - every dispatch pass settles them first anyway
            self._note_error(f"Nhóm tập: {type(error).__name__}: {error}")
        return dict(counts)

    def _recover_one(self, task: dict[str, Any]) -> dict[str, Any] | None:
        self._kill_leftover(task)
        state = task["state"]
        if state == "WAITING_SPACE":
            return self.store.transition(task["id"], {state}, "QUEUED", error_message=None)
        if state == "CANCELLING":
            self._finish_cancel(task)
            return self.store.get(task["id"])
        if state == "PUBLISHING":
            return self._recover_publish(task)
        moved = self.store.transition(task["id"], {state}, "INTERRUPTED", speed=None, eta=None,
                                      pid=None, pid_created=None)
        if moved:
            self._event(moved, "INTERRUPTED", "Control Center khởi động lại giữa chừng; "
                        "bấm Tiếp tục hoặc Thử lại.", level="WARNING")
        return moved

    def _recover_publish(self, task: dict[str, Any]) -> dict[str, Any] | None:
        output = Path(task["output_path"]) if task["output_path"] else None
        if (output is not None and output.is_file() and output.stat().st_size == task["output_size"]
                and sha256_file(output) == task["output_sha256"]):
            moved = self.store.transition(task["id"], {"PUBLISHING"}, "COMPLETED", temp_file=None)
            if moved:
                self._event(moved, "COMPLETED", f"Đã chuyển vào input: {output.name} (xác nhận khi khởi động).")
                self._remove_temp(moved)
            return moved
        if task["temp_file"] and Path(task["temp_file"]).is_file():
            moved = self.store.transition(task["id"], {"PUBLISHING"}, "INTERRUPTED", output_path=None,
                                          name_locked=0)
            if moved:
                self._event(moved, "INTERRUPTED", "Ngắt lúc chuyển vào input; file tạm còn nguyên.",
                            level="WARNING")
            return moved
        message = "Ngắt lúc chuyển vào input; không thấy file tạm và file đích không khớp. Không đoán."
        self._fail(task["id"], {"PUBLISHING"}, "PUBLISH_UNKNOWN", message)
        return self.store.get(task["id"])

    @staticmethod
    def _since(task: dict[str, Any]) -> datetime:
        return datetime.fromisoformat(task["state_since"])

    def sweep(self, now: datetime | None = None) -> dict[str, Any]:
        """7 days → EXPIRED, 30 days → row dropped, orphan folders > 24 h, caches when idle."""
        now = now or self.store.clock()
        summary: dict[str, Any] = {"expired": 0, "deleted": 0, "orphans": 0, "freed_bytes": 0, "cache": None}
        try:
            self._sweep(now, summary)
        finally:
            self._last_sweep = time.monotonic()  # a failing sweep waits for the next hour too
        return summary

    def _sweep(self, now: datetime, summary: dict[str, Any]) -> None:
        with self._lock:
            running = set(self._controls)
            for task in self.store.tasks_in(TEMP_CLEANABLE):
                if task["id"] not in running and self._since(task) <= now - KEEP_TEMP:
                    summary["freed_bytes"] += self._remove_temp(task)
                    if not self._temp_gone(task["id"]):
                        continue
                    moved = self.store.transition(task["id"], TEMP_CLEANABLE, "EXPIRED", temp_file=None)
                    if moved:
                        self.tickets.revoke(task["id"])
                        summary["expired"] += 1
                        self._event(moved, "EXPIRED", "Quá 7 ngày: đã dọn file tạm; thử lại sẽ tải từ đầu.")
            for task in self.store.tasks_in(CLOSED):
                summary["freed_bytes"] += self._remove_temp(task)
                if self._since(task) <= now - KEEP_ROWS:
                    self.tickets.revoke(task["id"])
                    self.store.delete_task(task["id"])
                    summary["deleted"] += 1
            if self.downloads_dir.is_dir():
                known = {str(task["id"]) for task in self.store.list_tasks()}
                for entry in self.downloads_dir.iterdir():
                    try:
                        modified = datetime.fromtimestamp(entry.lstat().st_mtime, now.tzinfo)
                        if entry.name in known or now - modified < ORPHAN_AGE:
                            continue
                        summary["freed_bytes"] += safe_remove_tree(self.downloads_dir, entry)
                        summary["orphans"] += 1
                    except (OSError, UnsafePathError):
                        continue
            if not running:
                # Under the lock so no task starts (and opens the Deno cache) meanwhile.
                summary["cache"] = self.cache_pruner(self.root)
