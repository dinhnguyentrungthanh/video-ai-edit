"""Queue and state machine of the video downloader.

Runs inside the Control Center process, apart from the GPU scheduler. A task
is probed, waits for disk space, downloads into ``temp\\downloads\\<id>``, is
checked with FFmpeg and is renamed into ``input\\`` under a free name (never
overwriting). The watcher then sees it like a file the user copied in; nothing
is scanned, exported or published automatically. Deletes stay inside
``temp\\downloads``.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from biliflow.cleanup import prune_download_caches
from biliflow.download_files import (
    OUTPUT_SUFFIX,
    VIDEO_EXTENSIONS,
    UnsafePathError,
    safe_remove_tree,
    sanitize_name,
    sha256_file,
    unique_target,
    verify_video,
)
from biliflow.download_probe import ProbeEntry, choose
from biliflow.download_runner import ProcessControl, SizeGuard, YtDlpRunner
from biliflow.download_links import DownloadBatchError, Resolver, default_resolver, validate_batch
from biliflow.download_store import FINAL_STATES, SLOT_STATES, STATES, DownloadStore
from biliflow.download_upkeep import DownloadUpkeep
from biliflow.storage import GIB, storage_status

DEFAULT_SLOTS = 2
MAX_SLOTS = 3
SPACE_FACTOR = 2.2  # video + audio + the merged file
MIN_UNKNOWN_BUDGET_BYTES = 512 * 1024**2
SPACE_RETRY_SECONDS = 60.0
SWEEP_SECONDS = 3600.0
ACTION_WAIT_SECONDS = 15.0
PUBLISH_ATTEMPTS = 20
PUBLISH_LOCK_RETRIES = 5  # a scanner or the indexer holding the new file
PUBLISH_LOCK_WAIT_SECONDS = 2.0
CANCEL_RETRY_SECONDS = 30.0  # a temp file still held after a cancel

RUNNING = frozenset({"PROBING", "WAITING_SPACE", "DOWNLOADING", "VERIFYING"})
STOPPABLE = frozenset({"QUEUED", "PROBING", "WAITING_SPACE", "DOWNLOADING"})
RESUMABLE = frozenset({"STOPPED", "INTERRUPTED"})
CANCELLABLE = frozenset(set(STATES) - {"PUBLISHING", "COMPLETED", "EXPIRED", "CANCELLED"})
RETRYABLE = frozenset({"FAILED", "INTERRUPTED", "STOPPED", "CANCELLED", "EXPIRED"})
RENAMABLE = frozenset(set(STATES) - {"PUBLISHING", "COMPLETED"})
_RESET_FIELDS = {
    "entries": None, "probe": None, "chosen_entry": None, "video_id": None, "duration_seconds": None,
    "estimated_bytes": None, "downloaded_bytes": 0, "total_bytes": None, "speed": None, "eta": None,
    "error_code": None, "error_message": None, "temp_dir": None, "temp_file": None, "output_path": None,
    "output_sha256": None, "output_size": None, "verify": None, "name_locked": 0, "pid": None,
    "pid_created": None,
}

SpaceProbe = Callable[[Path], tuple[int, int]]


class DownloadActionError(RuntimeError):
    """A user action that cannot run in the task's current state."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def default_space(root: Path) -> tuple[int, int]:
    """Free bytes and the reserve the project keeps (20 % of the drive, at least 100 GB)."""
    status = storage_status(root)
    return int(status.free_gb * GIB), int(status.required_free_gb * GIB)


def _gb(value: int | float) -> str:
    return f"{value / GIB:.1f}"


class DownloadWorker(DownloadUpkeep):
    def __init__(self, root: Path, store: DownloadStore, *, runner: YtDlpRunner | None = None,
                 ffmpeg: Path | None = None, ffprobe: Path | None = None,
                 space_probe: SpaceProbe = default_space, verifier: Callable[..., Any] = verify_video,
                 resolver: Resolver = default_resolver, poll_seconds: float = 1.0,
                 space_retry_seconds: float = SPACE_RETRY_SECONDS, sweep_seconds: float = SWEEP_SECONDS,
                 cache_pruner: Callable[[Path], Any] = prune_download_caches):
        self.root = root.resolve(strict=True)
        self.store = store
        self.runner = runner or YtDlpRunner(self.root)
        tools = self.root / "tools" / "ffmpeg" / "bin"
        self.ffmpeg = ffmpeg or tools / "ffmpeg.exe"
        self.ffprobe = ffprobe or tools / "ffprobe.exe"
        self.input_dir = self.root / "input"
        self.downloads_dir = self.root / "temp" / "downloads"
        self.space_probe = space_probe
        self.verifier = verifier
        self.resolver = resolver
        self.poll_seconds = poll_seconds
        self.space_retry_seconds = space_retry_seconds
        self.sweep_seconds = sweep_seconds
        self.cache_pruner = cache_pruner
        self.publish_wait_seconds = PUBLISH_LOCK_WAIT_SECONDS
        self.cancel_retry_seconds = CANCEL_RETRY_SECONDS
        self.last_error: str | None = None
        self.last_error_at: str | None = None
        self._cancel_retry_at: dict[int, float] = {}
        self._lock = threading.RLock()
        self._publish_lock = threading.Lock()
        self._controls: dict[int, ProcessControl] = {}
        self._threads: dict[int, threading.Thread] = {}
        self._stopping = threading.Event()
        self._wake = threading.Event()
        self._loop: threading.Thread | None = None
        self._last_sweep: float | None = None

    # ----------------------------------------------------------------- lifecycle
    def start(self) -> dict[str, int]:
        """Settle what a previous run left, then start the loop (which sweeps first); the loop
        always starts, an error only shows in the snapshot."""
        recovered: dict[str, int] = {}
        try:
            self.downloads_dir.mkdir(parents=True, exist_ok=True)
            recovered = self.recover()
        except Exception as error:  # noqa: BLE001 - queued tasks must still run
            self._note_error(f"{type(error).__name__}: {error}")
        self._loop = threading.Thread(target=self._run, name="biliflow-download-worker", daemon=True)
        self._loop.start()
        return recovered

    def shutdown(self, timeout: float = 20.0) -> None:
        """Every running tree is killed at once (not one after another); one deadline for all."""
        deadline = time.monotonic() + timeout
        self._stopping.set()
        self._wake.set()
        with self._lock:
            controls = list(self._controls.values())
        killers = [threading.Thread(target=control.request, args=("shutdown",), daemon=True) for control in controls]
        for killer in killers:
            killer.start()
        self.wait_idle(max(0.0, deadline - time.monotonic()))
        if self._loop is not None and self._loop is not threading.current_thread():
            self._loop.join(max(0.0, deadline - time.monotonic()))

    def wait_idle(self, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                threads = [thread for thread in self._threads.values() if thread.is_alive()]
            if not threads:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            threads[0].join(min(remaining, 0.5))

    def _run(self) -> None:
        while not self._stopping.is_set():
            for step in (self.dispatch, self._sweep_when_due):
                try:
                    step()
                except Exception as error:  # keep the loop alive; the snapshot shows the error
                    self._note_error(f"{type(error).__name__}: {error}")
            self._wake.wait(self.poll_seconds)
            self._wake.clear()

    def _note_error(self, text: str) -> None:
        """The latest error stays in the snapshot, with its time, until a newer one replaces it."""
        self.last_error = text
        self.last_error_at = self.store.clock().isoformat()

    def _sweep_when_due(self) -> None:
        if self._last_sweep is None or time.monotonic() - self._last_sweep >= self.sweep_seconds:
            self.sweep()

    def slots(self) -> int:
        try:
            value = int(self.store.setting("slots", str(DEFAULT_SLOTS)) or DEFAULT_SLOTS)
        except ValueError:
            value = DEFAULT_SLOTS
        return min(MAX_SLOTS, max(1, value))

    def running_ids(self) -> set[int]:
        # No lock: the snapshot must not wait for a slow step under the lock; dict.copy() is atomic.
        return set(self._controls.copy())

    # ------------------------------------------------------------------ dispatch
    def dispatch(self) -> list[int]:
        """Start the oldest queued tasks while a slot is free; lowering slots never stops a task."""
        started: list[int] = []
        with self._lock:
            if self._stopping.is_set():  # checked under the lock: shutdown() sees every thread
                return started
            self._reconcile()
            busy = len(self.store.tasks_in(SLOT_STATES))
            while busy < self.slots():
                # A task whose previous thread is still ending waits for the next pass.
                task = self.store.next_queued(exclude=self._controls)
                if task is None:
                    break
                ready = bool(task["probe"] and task["probe"].get("ready"))
                claimed = self.store.transition(task["id"], {"QUEUED"}, "WAITING_SPACE" if ready else "PROBING")
                if claimed is None:
                    continue
                control = ProcessControl()
                thread = threading.Thread(target=self._run_task, args=(claimed["id"], control),
                                          name=f"biliflow-download-{claimed['id']}", daemon=True)
                self._controls[claimed["id"]] = control
                self._threads[claimed["id"]] = thread
                thread.start()
                started.append(claimed["id"])
                busy += 1
        return started

    def _run_task(self, task_id: int, control: ProcessControl) -> None:
        try:
            task = self.store.get(task_id)
            steps = (self._probe, self._wait_for_space, self._download, self._verify, self._publish)
            for step in steps[0 if task and task["state"] == "PROBING" else 1:]:
                if task is None:
                    break
                task = step(task, control)
        except Exception as error:
            try:
                self._fail(task_id, RUNNING | {"PUBLISHING"}, "INTERNAL_ERROR",
                           f"Lỗi nội bộ: {type(error).__name__}: {error}")
            except Exception as failure:  # noqa: BLE001 - the database failed; _reconcile settles it later
                self._note_error(f"{type(failure).__name__}: {failure}")
        finally:
            # One step under the lock: an action sees the thread (and asks it) or a settled task.
            with self._lock:
                try:
                    self._settle(task_id, control)
                except Exception as error:  # noqa: BLE001 - _reconcile settles it on a later pass
                    self._note_error(f"{type(error).__name__}: {error}")
                finally:
                    if self._controls.get(task_id) is control:
                        del self._controls[task_id]
                        self._threads.pop(task_id, None)
            self._wake.set()

    def _settle(self, task_id: int, control: ProcessControl) -> None:
        """A task thread never leaves its task in a running state."""
        task = self.store.get(task_id)
        if task is None:
            return
        if task["state"] == "CANCELLING":
            self._finish_cancel(task)
        elif task["state"] in RUNNING:
            if control.requested:
                self._end_requested(task_id, control)
            else:
                self._fail(task_id, RUNNING, "INTERNAL_ERROR", "Lượt tải dừng bất thường.")

    def _reconcile(self) -> None:
        """Settle tasks left in a slot state without a thread (caller holds the lock).

        A cancel whose temp files were still held is tried again every
        ``cancel_retry_seconds``; a running state whose thread could not record its end
        (a failed database write) becomes INTERRUPTED; PUBLISHING is checked like at start.
        """
        for task in self.store.tasks_in(SLOT_STATES):
            if task["id"] not in self._controls:
                try:
                    self._reconcile_one(task, time.monotonic())
                except Exception as error:  # noqa: BLE001 - one stuck task never blocks the queue
                    self._note_error(f"Lượt {task['id']}: {type(error).__name__}: {error}")

    def _reconcile_one(self, task: dict[str, Any], now: float) -> None:
        if task["state"] == "CANCELLING":
            if now >= self._cancel_retry_at.get(task["id"], 0.0):
                self._finish_cancel(task, report=False)
        elif task["state"] == "PUBLISHING":
            self._recover_publish(task)
        elif task["state"] == "WAITING_SPACE":
            self.store.transition(task["id"], {"WAITING_SPACE"}, "QUEUED", error_message=None)
        else:
            moved = self.store.transition(task["id"], {task["state"]}, "INTERRUPTED", speed=None, eta=None,
                                          pid=None, pid_created=None)
            if moved:
                self._event(moved, "INTERRUPTED", "Lượt tải dừng bất thường; bấm Tiếp tục hoặc Thử lại.",
                            level="WARNING")

    # --------------------------------------------------------------- task steps
    def _event(self, task: dict[str, Any], kind: str, message: str, *, level: str = "INFO",
               payload: dict[str, Any] | None = None) -> None:
        self.store.add_event(task["id"], task["attempt"], kind, message, level=level, payload=payload)

    def _task_dir(self, task_id: int) -> Path:
        path = self.downloads_dir / str(int(task_id))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _pid_recorder(self, task_id: int) -> Callable[[int, float], None]:
        return lambda pid, created: self.store.update_fields(task_id, pid=pid, pid_created=created)

    def _fail(self, task_id: int, allowed: set[str] | frozenset[str], code: str | None,
              message: str | None, **fields: Any) -> None:
        moved = self.store.transition(task_id, allowed, "FAILED", error_code=code, error_message=message,
                                      speed=None, eta=None, pid=None, pid_created=None, **fields)
        if moved:
            self._event(moved, "FAILED", message or code or "Lỗi", level="ERROR", payload={"code": code})
        return None

    def _end_requested(self, task_id: int, control: ProcessControl) -> None:
        task = self.store.get(task_id)
        if task is None:
            return None
        if control.reason == "cancel":
            self._finish_cancel(task)
        elif control.reason == "stop":
            moved = self.store.transition(task_id, RUNNING, "STOPPED", speed=None, eta=None, pid=None)
            if moved:
                self._event(moved, "STOPPED", "Đã dừng; giữ file tạm để tải tiếp.")
        elif self.store.transition(task_id, {"WAITING_SPACE"}, "QUEUED", error_message=None) is None:
            moved = self.store.transition(task_id, RUNNING, "INTERRUPTED", speed=None, eta=None, pid=None)
            if moved:
                self._event(moved, "INTERRUPTED", "Control Center tắt giữa chừng; bấm Tiếp tục để tải tiếp.")
        return None

    def _still_allowed(self, task: dict[str, Any], state: str) -> bool:
        """Check the link again right before yt-dlp opens it: its host may now resolve to an
        internal address."""
        try:
            validate_batch([task["url"]], resolver=self.resolver)
        except DownloadBatchError as error:
            item = error.errors[0] if error.errors else {}
            self._fail(task["id"], {state}, item.get("code") or "LINK_REJECTED", item.get("message") or str(error))
            return False
        return True

    def _probe(self, task: dict[str, Any], control: ProcessControl) -> dict[str, Any] | None:
        task_id = task["id"]
        if not self._still_allowed(task, "PROBING"):
            return None
        task_dir = self._task_dir(task_id)
        self.store.update_fields(task_id, temp_dir=str(task_dir))
        outcome = self.runner.probe(task["url"], task_dir, control, on_start=self._pid_recorder(task_id))
        self.store.update_fields(task_id, pid=None, pid_created=None)
        if control.requested:
            return self._end_requested(task_id, control)
        if not outcome.ok:
            return self._fail(task_id, {"PROBING"}, outcome.code, outcome.message)
        info = outcome.info or {}
        choice = choose(info)
        entries = [entry.as_dict() for entry in choice.entries]
        page = {"extractor": info.get("extractor_key") or info.get("extractor"),
                "page_title": str(info.get("title") or "")[:300], "entry_count": max(1, len(entries))}
        if choice.kind == "FAILED":
            return self._fail(task_id, {"PROBING"}, choice.code, choice.message, entries=entries, probe=page)
        if choice.kind == "NEEDS_CHOICE":
            moved = self.store.transition(task_id, {"PROBING"}, "NEEDS_CHOICE", entries=entries, probe=page,
                                          error_message=choice.message)
            if moved:
                self._event(moved, "NEEDS_CHOICE", choice.message or "Cần chọn video.")
            return None
        return self._ready(task_id, {"PROBING"}, choice.entry, entries, page, "WAITING_SPACE")

    def _ready(self, task_id: int, allowed: set[str], entry: ProbeEntry, entries: list[dict[str, Any]],
               page: dict[str, Any], to_state: str) -> dict[str, Any] | None:
        probe = {**page, "ready": True, "playlist_item": entry.index or None,
                 "expected_files": entry.expected_files, "video_codec": entry.video_codec,
                 "audio_codec": entry.audio_codec, "height": entry.height}
        return self.store.transition(
            task_id, allowed, to_state, original_title=entry.title[:300],
            duration_seconds=entry.duration_seconds, estimated_bytes=entry.estimated_bytes,
            video_id=entry.video_id, chosen_entry=entry.index or None, entries=entries, probe=probe,
            error_code=None, error_message=None,
        )

    def _others_need(self, task_id: int) -> int:
        """Bytes the other running downloads may still write (size × SPACE_FACTOR, less what they wrote)."""
        total = 0
        for other in self.store.tasks_in({"DOWNLOADING"}):
            if other["id"] != task_id:
                size = other["estimated_bytes"] or other["total_bytes"] or 0
                total += max(0, int(size * SPACE_FACTOR) - int(other["downloaded_bytes"] or 0))
        return total

    def _wait_for_space(self, task: dict[str, Any], control: ProcessControl) -> dict[str, Any] | None:
        announced = False
        while True:
            if control.requested:
                return self._end_requested(task["id"], control)
            # Under the lock: two tasks never both count the same free space.
            with self._lock:
                free, reserve = self.space_probe(self.root)
                others = self._others_need(task["id"])
                usable = free - others
                held = f" ({_gb(others)} GB dành cho lượt đang tải)" if others else ""
                estimate = task["estimated_bytes"]
                # What this task may write; the page's estimate is never trusted as a limit (SizeGuard).
                budget = int((usable - reserve) / SPACE_FACTOR)
                limit: int | None = None
                if estimate:
                    needed = int(estimate * SPACE_FACTOR) + reserve
                    ready = usable >= needed
                    message = (f"Chờ chỗ trống: cần {_gb(needed)} GB (gồm {_gb(reserve)} GB giữ lại), "
                               f"đang còn {_gb(free)} GB{held}.")
                else:
                    limit = budget
                    ready = limit >= MIN_UNKNOWN_BUDGET_BYTES
                    message = (f"Chờ chỗ trống: chưa biết dung lượng video, còn {_gb(free)} GB{held}, "
                               f"giữ lại {_gb(reserve)} GB.")
                if ready:
                    moved = self.store.transition(task["id"], {"WAITING_SPACE"}, "DOWNLOADING",
                                                  error_message=None, speed=None, eta=None)
                    if moved is not None:
                        moved["max_filesize"] = limit
                        moved["max_bytes"] = budget
                    return moved
            if not announced:
                self.store.update_fields(task["id"], error_message=message)
                self._event(task, "WAITING_SPACE", message)
                announced = True
            control.wait(self.space_retry_seconds)

    def _download(self, task: dict[str, Any], control: ProcessControl) -> dict[str, Any] | None:
        task_id, attempt = task["id"], task["attempt"]
        probe = task["probe"] or {}
        if not self._still_allowed(task, "DOWNLOADING"):
            return None
        self._event(task, "DOWNLOADING", "Bắt đầu tải.")
        outcome = self.runner.download(
            task["url"], self._task_dir(task_id), control,
            on_progress=lambda item: self.store.update_progress(
                task_id, attempt, downloaded_bytes=item.downloaded_bytes, total_bytes=item.total_bytes,
                speed=item.speed, eta=item.eta),
            on_log=lambda lines: self.store.append_log(task_id, attempt, lines),
            on_start=self._pid_recorder(task_id),
            playlist_item=probe.get("playlist_item"), max_filesize=task.get("max_filesize"),
            expected_files=int(probe.get("expected_files") or 1), estimated_bytes=task["estimated_bytes"],
            guard=self._size_guard(task),
        )
        self.store.update_fields(task_id, pid=None, pid_created=None)
        if control.requested:
            return self._end_requested(task_id, control)
        if not outcome.ok:
            return self._fail(task_id, {"DOWNLOADING"}, outcome.code, outcome.message)
        return self.store.transition(task_id, {"DOWNLOADING"}, "VERIFYING",
                                     temp_file=str(outcome.final_path), speed=None, eta=None)

    def _size_guard(self, task: dict[str, Any]) -> SizeGuard | None:
        budget = task.get("max_bytes")
        if not budget or budget <= 0:
            return None
        return SizeGuard(self._task_dir(task["id"]), budget, int(budget * SPACE_FACTOR))

    def _verify(self, task: dict[str, Any], control: ProcessControl) -> dict[str, Any] | None:
        path = Path(task["temp_file"])
        if path.resolve().parent != (self.downloads_dir / str(task["id"])).resolve():
            return self._fail(task["id"], {"VERIFYING"}, "BAD_OUTPUT", "File tải về nằm ngoài thư mục của lượt.")
        result = self.verifier(self.ffprobe, self.ffmpeg, path, expected_duration=task["duration_seconds"])
        if control.requested:
            return self._end_requested(task["id"], control)
        if not result.ok:
            return self._fail(task["id"], {"VERIFYING"}, result.code, result.message, verify=result.as_dict())
        digest, size = sha256_file(path), path.stat().st_size
        if control.requested:
            return self._end_requested(task["id"], control)
        return self.store.transition(task["id"], {"VERIFYING"}, "PUBLISHING", verify=result.as_dict(),
                                     output_sha256=digest, output_size=size, name_locked=1)

    def _publish(self, task: dict[str, Any], control: ProcessControl) -> None:
        task_id = task["id"]
        if not self.input_dir.is_dir():
            return self._fail(task_id, {"PUBLISHING"}, "NO_INPUT_DIR", "Không thấy thư mục input của BiliFlow.")
        source = Path(task["temp_file"])
        stem = sanitize_name(task["desired_name"] or task["original_title"] or "",
                             fallback=task["video_id"] or f"video-{task_id}")
        # yt-dlp merges and remuxes to mp4; another container keeps its own extension.
        suffix = source.suffix.lower() if source.suffix.lower() in VIDEO_EXTENSIONS else OUTPUT_SUFFIX
        with self._publish_lock:
            for _ in range(PUBLISH_ATTEMPTS):
                target = unique_target(self.input_dir, stem, suffix)
                # Recorded first so a crash during the rename can be settled at the next start.
                self.store.update_fields(task_id, output_path=str(target))
                try:
                    self._rename(source, target, control)
                except FileExistsError:
                    continue
                except PermissionError as error:
                    # Still held after the retries: keep the verified file; Tiếp tục tries again.
                    moved = self.store.transition(
                        task_id, {"PUBLISHING"}, "INTERRUPTED", output_path=None, name_locked=0,
                        error_code="PUBLISH_BLOCKED",
                        error_message=f"File đang bị chương trình khác giữ, chưa chuyển vào input: {error}",
                    )
                    if moved:
                        self._event(moved, "INTERRUPTED", "File bị giữ khi chuyển vào input; file tạm còn nguyên. "
                                    "Bấm Tiếp tục để thử lại.", level="WARNING")
                    return None
                except OSError as error:
                    return self._fail(task_id, {"PUBLISHING"}, "PUBLISH_FAILED",
                                      f"Không chuyển được file vào input: {error}", output_path=None)
                break
            else:
                return self._fail(task_id, {"PUBLISHING"}, "PUBLISH_FAILED",
                                  "Không tìm được tên trống trong input.", output_path=None)
        moved = self.store.transition(task_id, {"PUBLISHING"}, "COMPLETED", temp_file=None,
                                      speed=None, eta=None, error_code=None, error_message=None)
        if moved:
            self._event(moved, "COMPLETED", f"Đã chuyển vào input: {target.name}",
                        payload={"name": target.name, "size_bytes": moved["output_size"]})
            self._remove_temp(moved)
        return None

    def _rename(self, source: Path, target: Path, control: ProcessControl) -> None:
        """os.rename (never os.replace: an existing file is never overwritten), tried again while
        another program (antivirus, indexer) still holds the new file."""
        for attempt in range(1, PUBLISH_LOCK_RETRIES + 1):
            try:
                os.rename(source, target)
                return
            except FileExistsError:
                raise
            except PermissionError:
                if attempt == PUBLISH_LOCK_RETRIES or control.wait(self.publish_wait_seconds):
                    raise

    def _temp_path(self, task_id: int) -> Path:
        return self.downloads_dir / str(int(task_id))

    def _remove_temp(self, task: dict[str, Any], *, report: bool = True) -> int:
        """Delete the task's temp folder; afterwards ``_temp_gone`` says whether it is really gone."""
        path = self._temp_path(task["id"])
        try:
            return safe_remove_tree(self.downloads_dir, path) if self.downloads_dir.exists() else 0
        except (OSError, UnsafePathError) as error:
            if report:
                self._event(task, "TEMP_REMOVE_FAILED", f"Chưa xóa được file tạm: {error}", level="WARNING")
            return 0

    def _temp_gone(self, task_id: int) -> bool:
        return not os.path.lexists(self._temp_path(task_id))

    def _finish_cancel(self, task: dict[str, Any], *, report: bool = True) -> None:
        """CANCELLED only once the temp folder is gone. A file still held (a process that outlived
        the kill, a scanner) keeps CANCELLING; _reconcile tries again every cancel_retry_seconds."""
        freed = self._remove_temp(task, report=report)
        if not self._temp_gone(task["id"]):
            self._cancel_retry_at[task["id"]] = time.monotonic() + self.cancel_retry_seconds
            self.store.update_fields_if(task["id"], {"CANCELLING"}, speed=None, eta=None,
                                        error_message="Đang hủy: chưa xóa được file tạm, sẽ thử lại.")
            return
        self._cancel_retry_at.pop(task["id"], None)
        moved = self.store.transition(task["id"], CANCELLABLE | {"CANCELLING"}, "CANCELLED", temp_file=None,
                                      speed=None, eta=None, pid=None, pid_created=None, error_message=None)
        if moved:
            self._event(moved, "CANCELLED", "Đã hủy và xóa file tạm.", payload={"freed_bytes": freed})

    # ------------------------------------------------------------- user actions
    def _require(self, task_id: int) -> dict[str, Any]:
        task = self.store.get(task_id)
        if task is None:
            raise DownloadActionError("Không thấy lượt tải.", 404)
        return task

    def add(self, urls: list[str], *, rights_confirmed: bool) -> list[dict[str, Any]]:
        if rights_confirmed is not True:
            raise DownloadActionError("Cần tick xác nhận có quyền tải và chỉnh sửa video.", 400)
        tasks = self.store.add_tasks(validate_batch(urls, resolver=self.resolver))
        for task in tasks:
            self._event(task, "QUEUED", "Đã thêm vào hàng đợi.")
        self._wake.set()
        return tasks

    def _wait_for(self, task_id: int, thread: threading.Thread | None) -> dict[str, Any] | None:
        if thread is not None and thread is not threading.current_thread():
            thread.join(ACTION_WAIT_SECONDS)
        return self.store.get(task_id)

    def stop(self, task_id: int) -> dict[str, Any]:
        with self._lock:
            task = self._require(task_id)
            if task["state"] == "STOPPED":
                return task
            if task["state"] not in STOPPABLE:
                raise DownloadActionError("Lượt này không ở trạng thái dừng được.")
            control, thread = self._controls.get(task_id), self._threads.get(task_id)
            if control is None:
                moved = self.store.transition(task_id, STOPPABLE, "STOPPED", speed=None, eta=None)
                if moved is None:
                    raise DownloadActionError("Trạng thái vừa đổi; thử lại.")
                self._event(moved, "STOPPED", "Đã dừng.")
                return moved
        control.request("stop")
        return self._wait_for(task_id, thread)

    def cancel(self, task_id: int) -> dict[str, Any]:
        with self._lock:
            task = self._require(task_id)
            if task["state"] == "CANCELLED":
                return task
            moved = task if task["state"] == "CANCELLING" else self.store.transition(
                task_id, CANCELLABLE, "CANCELLING")
            if moved is None:
                raise DownloadActionError("Lượt này đã (hoặc đang) chuyển vào input; không hủy được.")
            control, thread = self._controls.get(task_id), self._threads.get(task_id)
            if control is None:
                self._finish_cancel(moved)
                return self.store.get(task_id)
        control.request("cancel")
        return self._wait_for(task_id, thread)

    def resume(self, task_id: int) -> dict[str, Any]:
        with self._lock:
            task = self._require(task_id)
            if task["state"] == "QUEUED" or task["state"] in SLOT_STATES:
                return task
            if task["state"] not in RESUMABLE:
                raise DownloadActionError("Chỉ tiếp tục được lượt đã dừng hoặc bị ngắt.")
            moved = self.store.transition(task_id, RESUMABLE, "QUEUED", error_code=None, error_message=None,
                                          queued_at=self.store.clock().isoformat())
            if moved:
                self._event(moved, "RESUMED", "Tiếp tục: tải nối phần đã có nếu trang hỗ trợ.")
        self._wake.set()
        return moved or self.store.get(task_id)

    def retry(self, task_id: int) -> dict[str, Any]:
        with self._lock:
            task = self._require(task_id)
            if task["state"] == "QUEUED" or task["state"] in SLOT_STATES:
                return task
            if task["state"] not in RETRYABLE:
                raise DownloadActionError("Lượt này không thử lại được.")
            self._remove_temp(task)
            if not self._temp_gone(task_id):  # never mix old part files into a fresh download
                raise DownloadActionError("Chưa xóa được file tạm của lần trước (file đang bị giữ); thử lại sau.")
            attempt = int(task["attempt"]) + 1
            moved = self.store.transition(task_id, RETRYABLE, "QUEUED", attempt=attempt,
                                          queued_at=self.store.clock().isoformat(), **_RESET_FIELDS)
            if moved:
                self.store.drop_attempts_before(task_id, attempt)
                self._event(moved, "RETRY", f"Thử lại lần {attempt}: tải lại từ đầu.")
        self._wake.set()
        return moved or self.store.get(task_id)

    def remove(self, task_id: int) -> dict[str, Any]:
        """Drop a finished row and its temp folder; a file already in input is never touched."""
        with self._lock:
            task = self.store.get(task_id)
            if task is None:
                return {"id": task_id, "removed": True, "freed_bytes": 0}
            if task["state"] not in FINAL_STATES or task_id in self._controls:
                raise DownloadActionError("Chỉ xóa được lượt đã kết thúc; dừng hoặc hủy trước.")
            freed = self._remove_temp(task)
            if not self._temp_gone(task_id):  # the row stays with its folder; nothing is left unaccounted
                raise DownloadActionError("Chưa xóa được file tạm của lượt này (file đang bị giữ); thử lại sau.")
            self.store.delete_task(task_id)
        return {"id": task_id, "removed": True, "freed_bytes": freed}

    def rename(self, task_id: int, name: Any) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip():
            raise DownloadActionError("Tên không được để trống.", 400)
        cleaned = sanitize_name(name, fallback=None)
        if not cleaned:
            raise DownloadActionError("Tên chỉ có ký tự không dùng được trong tên file.", 400)
        self._require(task_id)
        moved = self.store.update_fields_if(task_id, RENAMABLE, desired_name=cleaned)
        if moved is None:
            raise DownloadActionError("Tên đã khóa: file đã (hoặc đang) chuyển vào input.")
        self._event(moved, "RENAMED", f"Đổi tên thành: {cleaned}")
        return moved

    def choose(self, task_id: int, entry_index: Any) -> dict[str, Any]:
        if isinstance(entry_index, bool) or not isinstance(entry_index, int):
            raise DownloadActionError("Cần chọn một mục.", 400)
        with self._lock:
            task = self._require(task_id)
            if task["state"] != "NEEDS_CHOICE":
                if task["chosen_entry"] == entry_index and task["state"] != "FAILED":
                    return task
                raise DownloadActionError("Lượt này không chờ chọn video.")
            match = next((item for item in task["entries"] or [] if item.get("index") == entry_index), None)
            if match is None:
                raise DownloadActionError("Mục này không có trong danh sách.", 400)
            moved = self._ready(task_id, {"NEEDS_CHOICE"}, ProbeEntry(**match), task["entries"],
                                task["probe"] or {}, "QUEUED")
            if moved:
                self._event(moved, "CHOSEN", f"Đã chọn: {match.get('title')}")
        self._wake.set()
        return moved or self.store.get(task_id)

    def set_slots(self, slots: Any) -> int:
        if isinstance(slots, bool) or not isinstance(slots, int) or not 1 <= slots <= MAX_SLOTS:
            raise DownloadActionError(f"Số luồng tải phải từ 1 đến {MAX_SLOTS}.", 400)
        self.store.set_setting("slots", str(slots))
        self._wake.set()
        return slots
