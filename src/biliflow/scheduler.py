from __future__ import annotations

import itertools
import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from biliflow import export_lanes
from biliflow.job_import import VIDEO_EXTENSIONS, source_path_key
from biliflow.codex_supervisor import run_local_queue_audit
# A skipped video ("Bỏ qua (không xuất)") must be reopened before it can run
# again; both refusals stay importable from biliflow.scheduler. A source moved
# to the Recycle Bin by "Dọn video gốc", or into archive/ by "Lưu trữ", locks
# every action on its job.
from biliflow.export_guards import (
    RESUMABLE_EXPORT_STATES,
    SKIPPED_REFUSAL,
    SKIPPED_STOP_REFUSAL,
    SOURCE_ARCHIVED_MESSAGE,
    SOURCE_ARCHIVED_STOP_REFUSAL,
    SOURCE_CLEANED_MESSAGE,
    SOURCE_CLEANED_STOP_REFUSAL,
    ActionConflict,
)
from biliflow.job_pipeline import (
    DEFAULT_DETECTOR_GROUPS,
    PipelineStage,
    StageCommand,
    normalize_detector_groups,
    DEFAULT_FAST_SCAN,
    normalize_fast_scan,
    normalize_ocr_batch_size,
    pipeline_stages,
    run_command,
    safe_job_key,
    validate_json_artifact,
)
from biliflow.job_store import IN_PROCESS_STATES, JobStore, now_iso, sha256_file
from biliflow.final_renderer import (
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_TARGET_OUTPUT_BYTES,
    export_deferral_path,
    read_export_deferral,
    read_render_stall,
    render_graph_path,
    render_progress_path,
    render_stall_path,
)
from biliflow.probe import duration_seconds, probe_video
from biliflow.license_policy import audit_project_models
from biliflow.stage_cache import StageArtifactCache, StageCachePathTooLong
from biliflow.cleanup import prune_file_caches


ACTIVE_STATES = IN_PROCESS_STATES
# The dashboard "Bắt đầu" button may only configure a video still waiting for setup.
STARTABLE_STATES = {"NEEDS_METADATA", "DISCOVERED"}
# Stages whose _after_success sets the job's final state. The worker marks the
# stage COMPLETED first, so a crash in between leaves no pending stage to run.
FINISHING_STAGES = ("build_review", "render")
# Why an export request (a render stage not yet RUNNING or COMPLETED) was
# retired; the message is the event text and the stage error.
EXPORT_RETIRE_MESSAGES = {
    "cancel": "Đã hủy lệnh xuất video.",
    "review_changed": (
        "Lệnh xuất cũ hết hiệu lực vì quyết định duyệt đã đổi; bấm “Xuất video” để xuất lại."
    ),
    "skipped": "Lệnh xuất cũ hết hiệu lực vì video đã được đánh dấu bỏ qua.",
    "output_exists": "Bản xuất của lần duyệt này đã có; không cần render lại.",
}
RESUME_STALE_EXPORT_REFUSAL = "Lệnh xuất cũ không còn hiệu lực; bấm “Xuất video” để xuất lại."
# Format with job_id=...
RESUME_SETTLED_REFUSAL = (
    "Video #{job_id} đã có kết quả duyệt; dùng “Xuất video” hoặc “Chạy lại kiểm tra” "
    "thay vì Tiếp tục."
)
# A job with a recorded review result has nothing for Tiếp tục to continue.
RESUME_SETTLED_STATES = frozenset({"WAITING_REVIEW", "READY_TO_EXPORT", "COMPLETED"})
# Hủy has nothing to stop (ActionConflict, HTTP 409; no write, no event).
# Format with job_id=... A cancelled job stays revivable (lead decision L1).
CANCEL_REPEAT_REFUSAL = "Video #{job_id} đã được hủy trước đó; không hủy thêm lần nữa."
CANCEL_COMPLETED_REFUSAL = "Video #{job_id} đã hoàn tất; không có gì để hủy."
CANCEL_SETTLED_STATES = frozenset({"COMPLETED", "SKIPPED", "CANCELLED"})
# Job states Dừng ngay and Hủy write while the worker may be inside _execute.
STOPPED_BY_USER_STATES = frozenset({"PAUSED", "CANCELLED"})
# RESUMABLE_EXPORT_STATES (imported above): the only states in which a pending
# render is an export the user paused, that failed, or that a restart
# interrupted, so Tiếp tục may run it. The standalone review UI refuses edits
# in exactly those states (export_guards.standalone_edit_refusal).
# InputWatcher events for a source that "Dọn video gốc" moved to the Recycle Bin.
SOURCE_RESTORED_MESSAGE = "Đã khôi phục video gốc (SHA-256 khớp)"
# Format with file_name=... (the name the source had in input/).
SOURCE_RESTORE_WRONG_PATH_MESSAGE = (
    "Đã thấy video gốc ở tên khác; hãy chép lại đúng tên “{file_name}” vào input."
)
SOURCE_RESTORE_REJECTED_MESSAGE = (
    "File mới ở đường dẫn cũ có SHA-256 khác video gốc; không dùng để chạy lại."
)
# Perf plan A1b. The main lane runs every scan stage and every export one at a
# time, exports first, each kind in click order (A1a). With "Xuất song song: Tự
# động" a second lane runs ONE NVENC export beside a GPU scan stage of the main
# lane when export_lanes allows it; with "Tắt" the second lane never starts.
MAIN_LANE = "main"
EXPORT_LANE = "export"
LANES = (MAIN_LANE, EXPORT_LANE)
# Perf plan A1c. CPU stages short enough that the main lane runs them while the
# first waiting export waits for the export lane beside the GPU stage after them.
# A film that has not started waits at preflight (source, tools, models and disk
# checked, no process); judged there for its first GPU stage, the export no longer
# runs ahead of the whole scan.
SHORT_CPU_STAGES = frozenset({"preflight"})
# EXPORT_DEFERRED event text by the renderer's reason (final_renderer.DEFER_REASONS).
EXPORT_DEFERRED_MESSAGES = {
    "encoder_error": "NVENC lỗi khi xuất song song; bản xuất sẽ được encode lại bằng x264 sau khi lượt quét đang chạy xong.",
    "size_limit": "Bản NVENC vượt giới hạn dung lượng; bản xuất sẽ được encode lại bằng x264 sau khi lượt quét đang chạy xong.",
    "not_nvenc": "Bản xuất không chạy được NVENC cạnh bước quét; sẽ xuất sau khi lượt quét đang chạy xong.",
}
EXPORT_HANDED_BACK_MESSAGE = "Bản xuất được trả về làn chính sau khi lượt quét đang chạy xong."


def runs_on_gpu(definition: PipelineStage) -> bool:
    """A scan stage whose processes hold the GPU, as run.ps1 decides (no ``--device cpu``)."""
    if not definition.uses_gpu or not definition.commands:
        return False
    for command in definition.commands:
        argv = [str(value) for value in command.argv]
        if any(value == "--device" and argv[index + 1:index + 2] == ["cpu"] for index, value in enumerate(argv)):
            return False
    return True


@dataclass(frozen=True)
class _GpuStage:
    """The main lane's GPU stage process the second lane may run beside."""
    token: int
    job_id: int
    stage: str
    # NVML "used" just before it started (other programs, an export already running).
    baseline_mib: int | None


class _ExportHandedBack(RuntimeError):
    """A render of the second lane stopped and left its deferral record."""


class _LaneWake:
    """set() wakes every lane; each lane waits on and clears only its own flag.

    With one shared Event a lane could clear a wake-up meant for the other one,
    which then slept a whole poll before it looked again.
    """

    def __init__(self, lanes: tuple[str, ...]) -> None:
        self._events = {lane: threading.Event() for lane in lanes}

    def set(self) -> None:
        for event in self._events.values():
            event.set()

    def wait(self, lane: str, timeout: float) -> None:
        event = self._events[lane]
        event.wait(timeout)
        event.clear()


def terminate_process_tree(process: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            # CTRL_BREAK can stop the PowerShell/Python wrapper while an
            # FFmpeg child survives as an orphan. Kill the complete process
            # tree so an immediate pause really releases CPU, GPU and file
            # handles before the stage is restarted.
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
        else:
            process.terminate()
        process.wait(timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        process.kill()
        process.wait(timeout=5)


class JobScheduler:
    """Durable workers for GPU scans and final renders: a main lane and an export lane.

    Stages are individual subprocesses. A pause or restart repeats only the active
    stage; completed artifacts and review revisions remain intact. The export lane
    (perf plan A1b) only ever runs one NVENC export beside a GPU scan stage of the
    main lane; every other export runs in the main lane, as before.
    """

    def __init__(self, root: Path, store: JobStore, *, poll_seconds: float = 1.0):
        self.root = root.resolve(strict=True)
        self.store = store
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        # Set by shutdown: no lane starts a new stage; running stages finish or stop.
        self._draining = threading.Event()
        # Any change wakes each lane to look again (_wake.set()).
        self._wake = _LaneWake(LANES)
        self._lock = threading.RLock()
        # One lock for every user action that changes where a job is going:
        # start, rerun, resume, retry, export (queue_render) here, and skip,
        # unskip and finalize in the Control Center. Each re-reads the job
        # state inside it, so two clicks or two pages cannot interleave.
        self.job_action_lock = threading.RLock()
        self._start_lock = self.job_action_lock
        # Per lane, under _lock: its thread, its running stage process, (job_id,
        # stage) while that process runs, and the process's log file.
        self._threads: dict[str, threading.Thread] = {}
        self._process: dict[str, subprocess.Popen] = {}
        self._active: dict[str, tuple[int, str]] = {}
        self._log_handle: dict[str, Any] = {}
        # The job each lane is inside _execute for, including the stage
        # bookkeeping after its subprocess ended (no _active entry then).
        self._executing: dict[str, int] = {}
        self._stage_cache = StageArtifactCache(self.root)
        # Perf plan A1b, under _lock: the main lane's running GPU stage process, the
        # exports the export lane turned down (each then runs in the main lane), and
        # the scan job the main lane ran last (an export handed back waits for it).
        self._gpu_stage: _GpuStage | None = None
        self._gpu_stage_runs = itertools.count(1)
        self._sequential_exports: set[int] = set()
        self._parallel_decisions: dict[int, export_lanes.LaneDecision] = {}
        self._last_scan_job_id: int | None = None
        self._frame_probes: dict[tuple[str, int, int], dict] = {}
        # NVML read of GPU 0 (export_lanes.read_vram); tests give their own.
        self.read_vram = export_lanes.read_vram

    def _describe(self, lane: str) -> dict[str, Any]:
        job_id, stage = self._active[lane]
        process = self._process.get(lane)
        return {"job_id": job_id, "stage": stage, "pid": process.pid if process else None}

    @property
    def active(self) -> dict[str, Any] | None:
        """The running stage, or None when every lane is idle (the main lane's first).

        Kept as a single dict for the pages and checks that read one (the classic
        dashboard, "Đo lại"); active_lanes lists every lane.
        """
        with self._lock:
            lane = next((lane for lane in LANES if lane in self._active), None)
            return self._describe(lane) if lane is not None else None

    @property
    def active_lanes(self) -> list[dict[str, Any]]:
        """Every running stage process: [{"lane", "job_id", "stage", "pid"}], main lane first."""
        with self._lock:
            return [{"lane": lane, **self._describe(lane)} for lane in LANES if lane in self._active]

    def is_busy(self, job_id: int) -> bool:
        """A lane is running a stage of this job or still recording its result."""
        with self._lock:
            return (
                any(active[0] == job_id for active in self._active.values())
                or job_id in self._executing.values()
            )

    def _lane_busy(self, lane: str) -> bool:
        with self._lock:
            return lane in self._active or lane in self._executing

    def parallel_export_mode(self) -> str:
        """``auto`` or ``off`` ("Xuất song song" in Cài đặt); ``auto`` when never set."""
        return export_lanes.stored_mode(
            self.store.setting(export_lanes.MODE_SETTING, export_lanes.DEFAULT_MODE)
        )

    def _parallel_allowed(self) -> bool:
        """"Tự động" on a machine whose VRAM numbers were measured: the export lane may open."""
        return export_lanes.measured() and self.parallel_export_mode() == export_lanes.MODE_AUTO

    def set_parallel_export_mode(self, mode: Any) -> str:
        """Store the mode. ``off`` stops only new exports from going to the export
        lane; an export already running there finishes."""
        value = export_lanes.normalize_mode(mode)
        self.store.set_setting(export_lanes.MODE_SETTING, value)
        self._wake.set()
        return value

    def start(self) -> None:
        if any(thread.is_alive() for thread in self._threads.values()):
            return
        self._stop.clear()
        self._draining.clear()
        self._threads = {
            lane: threading.Thread(
                target=self._run, args=(lane,), daemon=False,
                name="biliflow-scheduler" if lane == MAIN_LANE else f"biliflow-scheduler-{lane}",
            )
            for lane in LANES
        }
        for thread in self._threads.values():
            thread.start()

    def shutdown(self, *, immediate: bool = False, timeout: float = 20.0) -> None:
        # From now on no lane starts a stage; each lane's running stage stops
        # (immediate) or reaches its durable boundary.
        self._draining.set()
        if immediate:
            for job_id in sorted({item["job_id"] for item in self.active_lanes}):
                self.pause_now(job_id)
        else:
            marked: set[int] = set()
            # Leave the workers alive until every lane's subprocess reaches its
            # durable stage boundary. The explicit Stop script may therefore
            # take as long as those stages, while the browser remains responsive.
            while lanes := self.active_lanes:
                for job_id in {item["job_id"] for item in lanes} - marked:
                    self.store.update_job(job_id, stop_mode="AFTER_STAGE")
                    marked.add(job_id)
                time.sleep(0.25)
        self._stop.set()
        self._wake.set()
        current = threading.current_thread()
        for thread in self._threads.values():
            if thread is not current:
                thread.join(timeout)
        if any(thread.is_alive() for thread in self._threads.values() if thread is not current):
            self._terminate_active("Control Center shutdown timeout")
            for thread in self._threads.values():
                if thread is not current:
                    thread.join(5)

    def _pipeline_key(self, job: dict[str, Any]) -> str:
        return str(self.store.setting(f"pipeline_key:{job['id']}") or job["job_key"])

    def detector_groups(self, job_id: int) -> tuple[str, ...]:
        stored = self.store.setting(f"detector_groups:{job_id}")
        if stored is None:
            return DEFAULT_DETECTOR_GROUPS
        return normalize_detector_groups(list(stored))

    def ocr_batch_size(self, job_id: int) -> int:
        return normalize_ocr_batch_size(self.store.setting(f"ocr_batch_size:{job_id}", 1))

    def fast_scan(self, job_id: int) -> bool:
        return normalize_fast_scan(self.store.setting(f"fast_scan:{job_id}", DEFAULT_FAST_SCAN))

    def configure_and_queue(
        self, job_id: int, *, content_style: str, profile: str,
        pipeline_key: str | None = None,
        detector_groups: list[str] | tuple[str, ...] | None = None,
        ocr_recognition_batch_size: int | None = None,
        fast_scan: bool | None = None,
        reseq: bool = True,
        reset_fields: dict[str, Any] | None = None,
    ) -> dict:
        """Store the scan settings, rebuild the stage list, then queue the job.

        The stage list is replaced before the job becomes QUEUED, so the worker
        can never pick it with the previous stage list.
        """
        batch_size = (self.ocr_batch_size(job_id) if ocr_recognition_batch_size is None
                      else normalize_ocr_batch_size(ocr_recognition_batch_size))
        fast = self.fast_scan(job_id) if fast_scan is None else normalize_fast_scan(fast_scan)
        if pipeline_key is not None:
            self.store.set_setting(f"pipeline_key:{job_id}", pipeline_key)
        selected_detectors = normalize_detector_groups(
            list(detector_groups) if detector_groups is not None
            else list(self.detector_groups(job_id))
        )
        self.store.set_setting(
            f"detector_groups:{job_id}", list(selected_detectors)
        )
        self.store.set_setting(f"ocr_batch_size:{job_id}", batch_size)
        self.store.set_setting(f"fast_scan:{job_id}", fast)
        job = self.store.get_job(job_id)
        definitions = pipeline_stages(
            root=self.root, job_key=self._pipeline_key(job), source=Path(job["source_path"]),
            content_style=content_style, profile=profile,
            source_sha256=job["source_sha256"],
            detector_groups=selected_detectors,
            ocr_recognition_batch_size=batch_size,
            fast_scan=fast,
        )
        self.store.replace_stages(job_id, [item.name for item in definitions])
        job = self.store.mark_queued(
            job_id, reseq=reseq, content_style=content_style, profile=profile,
            current_stage=None, progress=0.0, **(reset_fields or {}),
        )
        self.store.add_event(
            job_id, "JOB_QUEUED", f"Queued with {profile} profile",
            payload={"detector_groups": list(selected_detectors), "ocr_recognition_batch_size": batch_size,
                     "fast_scan": fast},
        )
        self._wake.set()
        return job

    def start_job(
        self, job_id: int, *, content_style: str, profile: str,
        detector_groups: list[str] | tuple[str, ...] | None = None,
        ocr_recognition_batch_size: int | None = None,
        fast_scan: bool | None = None,
    ) -> dict:
        """First start from the dashboard: only a video still waiting for setup.

        A stale browser card must not re-queue a job that is already queued,
        running or finished; reruns keep their own revisioned path.
        """
        with self._start_lock:
            job = self.store.get_job(job_id)
            self._refuse_locked(job_id)
            if job["state"] not in STARTABLE_STATES:
                raise ValueError(
                    f"Video #{job_id} đang ở trạng thái {job['state']}, không còn chờ thiết lập; "
                    "không xếp hàng lại. Dùng “Chạy lại kiểm tra” nếu muốn quét lại."
                )
            return self.configure_and_queue(
                job_id, content_style=content_style, profile=profile,
                detector_groups=detector_groups,
                ocr_recognition_batch_size=ocr_recognition_batch_size,
                fast_scan=fast_scan,
            )

    def rerun(
        self, job_id: int,
        *, detector_groups: list[str] | tuple[str, ...] | None = None,
        ocr_recognition_batch_size: int | None = None,
        fast_scan: bool | None = None,
        pipeline_key: str | None = None,
    ) -> dict:
        """Queue a clean scan in a new revision, at the back of the queue.

        Runs under the start lock and refuses a job that is already waiting or
        running, so a double click cannot queue two revisions. ``pipeline_key``:
        the revision's report folder a caller prepared ("Dựng lại danh sách
        duyệt (giữ quyết định)", review_rebuild); otherwise <job_key>-run-<time>.
        """
        with self._start_lock:
            job = self.store.get_job(job_id)
            # Before anything is written (render:{id} and skip:{id} below).
            self._refuse_locked(job_id)
            if self.is_busy(job_id) or job["state"] in IN_PROCESS_STATES:
                raise ValueError("Dừng job hiện tại trước khi chạy lại từ đầu")
            if job["state"] == "QUEUED":
                raise ValueError(
                    f"Video #{job_id} đang nằm trong hàng đợi; không xếp chạy lại thêm lần nữa."
                )
            if job["content_style"] == "unknown":
                raise ValueError("Hãy chọn loại nội dung trước khi chạy lại")
            # Validate first: a refused rerun must leave every setting untouched.
            if detector_groups is not None:
                normalize_detector_groups(list(detector_groups))
            if ocr_recognition_batch_size is not None:
                normalize_ocr_batch_size(ocr_recognition_batch_size)
            if fast_scan is not None:
                normalize_fast_scan(fast_scan)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            pipeline_key = pipeline_key or f"{job['job_key']}-run-{stamp}"
            self.store.set_setting(f"render:{job_id}", None)
            # A new scan replaces the review a skip was recorded for.
            self.store.set_setting(f"skip:{job_id}", None)
            # The old revision is cleared in the same UPDATE that queues the job.
            value = self.configure_and_queue(
                job_id, content_style=job["content_style"], profile=job["profile"],
                pipeline_key=pipeline_key,
                detector_groups=detector_groups,
                ocr_recognition_batch_size=ocr_recognition_batch_size,
                fast_scan=fast_scan,
                reset_fields={"active_queue_path": None, "active_revision": None},
            )
            self.store.add_event(
                job_id, "JOB_RERUN_QUEUED",
                "Queued a clean scan in a new report revision",
                payload={"pipeline_key": pipeline_key},
            )
            self._wake.set()
            return value

    def _resume_refusal(self, job: dict[str, Any], pending: dict[str, Any] | None) -> str | None:
        """Why Tiếp tục must not queue ``job`` again, given its next pending stage.

        A settled job (review recorded or exported) has nothing to continue,
        and a pending render is a live export only while the job was paused,
        failed, interrupted or is still queued; anywhere else (for example a
        cancelled job) it is an old request that Xuất video must replace.
        """
        if job["state"] in RESUME_SETTLED_STATES:
            return RESUME_SETTLED_REFUSAL.format(job_id=job["id"])
        if (
            pending is not None and pending["name"] == "render"
            and job["state"] not in RESUMABLE_EXPORT_STATES
        ):
            return RESUME_STALE_EXPORT_REFUSAL
        return None

    def resume(self, job_id: int) -> dict:
        with self.job_action_lock:
            job = self.store.get_job(job_id)
            if job["state"] == "SKIPPED":
                raise ValueError(SKIPPED_REFUSAL)
            self._refuse_locked(job_id)
            if job["content_style"] == "unknown":
                raise ValueError("Choose animation, live_action, or mixed before starting")
            if self.is_busy(job_id) or job["state"] in IN_PROCESS_STATES:
                raise ValueError(
                    f"Video #{job_id} đang được xử lý; chờ bước hiện tại xong rồi hãy bấm Tiếp tục."
                )
            refusal = self._resume_refusal(job, self.store.next_pending_stage(job_id))
            if refusal:
                raise ValueError(refusal)
            # Tiếp tục / Thử lại keep the job's place in the queue (user decision
            # 2026-10-02), including after a graceful shutdown or a restart.
            if not self.store.stages(job_id):
                return self.configure_and_queue(
                    job_id, content_style=job["content_style"], profile=job["profile"],
                    reseq=False,
                )
            if self.store.next_pending_stage(job_id) is None:
                # Interrupted after its last stage completed: finish that step
                # instead of queueing a job the worker can never pick.
                if job["state"] in {"INTERRUPTED_RECOVERABLE", "PAUSED", "FAILED"} and (
                    self._finish_interrupted(job)
                ):
                    return self.store.get_job(job_id)
                if self._runnable(self.store.get_job(job_id)) is None:
                    raise ValueError(
                        f"Video #{job_id} không còn bước nào để tiếp tục; hãy dùng Chạy lại kiểm tra."
                    )
            value = self.store.mark_queued(job_id, reseq=False)
            self.store.add_event(job_id, "JOB_RESUMED", "Job returned to the scheduler")
            self._wake.set()
            return value

    def _refuse_skipped(self, job_id: int) -> None:
        # A stale dashboard tab can still show Hủy / Dừng on a job another tab
        # skipped; a skip ends only through Mở lại để xuất or Chạy lại.
        if self.store.get_job(job_id)["state"] == "SKIPPED":
            raise ValueError(SKIPPED_STOP_REFUSAL)

    def _refuse_locked(self, job_id: int, *, stop: bool = False) -> None:
        # The source is in the Recycle Bin, or the shell call moving it there
        # has not answered (latest source_cleanups row RECYCLED or PENDING):
        # only the watcher unlocks the job, once the same file is back. Or it
        # is in archive/ or on its way there or back (latest source_archives
        # row PENDING, ARCHIVED or RESTORING): only "Khôi phục bản xuất"
        # unlocks it. Called inside job_action_lock, before any write.
        lock = self.store.source_lock(job_id)
        if lock == "cleaned":
            raise ValueError(SOURCE_CLEANED_STOP_REFUSAL if stop else SOURCE_CLEANED_MESSAGE)
        if lock == "archived":
            raise ValueError(SOURCE_ARCHIVED_STOP_REFUSAL if stop else SOURCE_ARCHIVED_MESSAGE)

    @staticmethod
    def _refuse_settled_cancel(job: dict[str, Any]) -> None:
        """Hủy on a job that has nothing left to stop: skipped, cancelled or completed."""
        if job["state"] == "SKIPPED":
            raise ValueError(SKIPPED_STOP_REFUSAL)
        if job["state"] == "CANCELLED":
            raise ActionConflict("already_cancelled", CANCEL_REPEAT_REFUSAL.format(job_id=job["id"]))
        if job["state"] == "COMPLETED":
            raise ActionConflict("not_cancellable", CANCEL_COMPLETED_REFUSAL.format(job_id=job["id"]))

    def stop_after_stage(self, job_id: int) -> dict:
        with self.job_action_lock:
            self._refuse_skipped(job_id)
            self._refuse_locked(job_id, stop=True)
            # A job still waiting in the queue has no stage to finish: take it out
            # (PAUSED keeps its place for Tiếp tục) instead of leaving it stuck.
            if self.store.pause_if_queued(job_id):
                self.store.add_event(job_id, "JOB_PAUSED", "Đã rút khỏi hàng đợi trước khi chạy")
                return self.store.get_job(job_id)
            value = self.store.update_job(job_id, stop_mode="AFTER_STAGE")
            self.store.add_event(job_id, "STOP_REQUESTED", "Will pause after the current stage")
            return value

    def pause_now(self, job_id: int) -> dict:
        with self.job_action_lock:
            self._refuse_skipped(job_id)
            self._refuse_locked(job_id, stop=True)
            stages = self.store.stages(job_id)
            for stage in stages:
                if stage["state"] == "RUNNING":
                    self.store.update_stage(job_id, stage["name"], state="PENDING", pid=None,
                                            heartbeat_at=None, error="Paused; this stage will restart")
            value = self.store.update_job(job_id, state="PAUSED", stop_mode="PAUSED", error=None)
            # Read after the write: a stage process a lane launches from now on
            # sees PAUSED and never starts (_execute checks under _lock).
            lanes = self._lanes_running(job_id)
        # The process tree is stopped outside the action lock so other clicks never
        # wait on it. Only this job's lane: a stage of another job in the other lane runs on.
        for lane in lanes:
            self._terminate_lane(lane)
        self.store.add_event(job_id, "JOB_PAUSED", "Paused safely at stage boundary")
        return value

    def cancel(self, job_id: int) -> dict:
        with self.job_action_lock:
            self._refuse_skipped(job_id)
            self._refuse_locked(job_id, stop=True)
            self._refuse_settled_cancel(self.store.get_job(job_id))
            # Conditional: a job the worker finished, or another tab cancelled,
            # since the check above is refused instead of cancelled twice.
            value = self.store.update_job_if(
                job_id, exclude=CANCEL_SETTLED_STATES, state="CANCELLED", stop_mode="CANCELLED",
            )
            if value is None:
                self._refuse_settled_cancel(self.store.get_job(job_id))
                raise ActionConflict("not_cancellable", CANCEL_COMPLETED_REFUSAL.format(job_id=job_id))
            for stage in self.store.stages(job_id):
                if stage["state"] == "RUNNING":
                    self.store.update_stage(job_id, stage["name"], state="CANCELLED", pid=None,
                                            heartbeat_at=None, error="Cancelled by user")
            # Hủy also ends an export that is only waiting (render PENDING), so
            # neither Tiếp tục nor the worker can start it later. current_stage
            # stays 'render' for the "Đã hủy xuất video" card.
            self.retire_render_request(job_id, "cancel")
            # Read after the write: a stage process a lane launches from now on
            # sees CANCELLED and never starts (_execute checks under _lock).
            lanes = self._lanes_running(job_id)
        for lane in lanes:
            self._terminate_lane(lane)
        self.store.add_event(job_id, "JOB_CANCELLED", "Job cancelled; source was not changed")
        return value

    def _lanes_running(self, job_id: int) -> list[str]:
        """The lanes whose stage process belongs to this job (read under _lock)."""
        with self._lock:
            return [lane for lane, active in self._active.items() if active[0] == job_id]

    def retry(self, job_id: int) -> dict:
        with self.job_action_lock:
            job = self.store.get_job(job_id)
            if job["state"] == "SKIPPED":
                raise ValueError(SKIPPED_REFUSAL)
            self._refuse_locked(job_id)
            if self.is_busy(job_id) or job["state"] in IN_PROCESS_STATES:
                raise ValueError(
                    f"Video #{job_id} đang được xử lý; chờ bước hiện tại xong rồi hãy bấm Thử lại."
                )
            stages = self.store.stages(job_id)
            failed = next((item for item in stages if item["state"] in {"FAILED", "FAILED_RETRYABLE"}), None)
            # resume() refuses after the reset below would already have been
            # written; check its refusals against the stage that would be next.
            pending = next((
                item for item in stages
                if item is failed or item["state"] in {"PENDING", "FAILED_RETRYABLE"}
            ), None)
            refusal = self._resume_refusal(job, pending)
            if refusal:
                raise ValueError(refusal)
            if failed:
                self.store.update_stage(job_id, failed["name"], state="PENDING", error=None, pid=None)
            return self.resume(job_id)

    def retire_render_request(
        self, job_id: int, reason: str, *, clear_current_stage: bool = False,
    ) -> bool:
        """Retire the job's unfinished export request; True when one was retired.

        Does nothing for a QUEUED or in-process job (finalize, the worker and
        pause/cancel own those). Otherwise one conditional UPDATE turns a
        PENDING, FAILED_RETRYABLE or FAILED render stage into CANCELLED; a
        RUNNING or COMPLETED render is never touched. ``render:{id}`` stays as
        history. clear_current_stage drops the 'render' marker even when there
        was no stage left to retire.
        """
        message = EXPORT_RETIRE_MESSAGES[reason]
        with self.job_action_lock:
            job = self.store.get_job(job_id)
            if job["state"] == "QUEUED" or job["state"] in IN_PROCESS_STATES:
                return False
            old = self.store.retire_stage(job_id, "render", error=message)
            if clear_current_stage and job.get("current_stage") == "render":
                self.store.update_job_if(job_id, states={job["state"]}, current_stage=None)
            if old is None:
                return False
            self.store.add_event(
                job_id, "EXPORT_REQUEST_RETIRED", message,
                payload={
                    "reason": reason, "stage_state": old,
                    "render": self.store.setting(f"render:{job_id}"),
                },
            )
            return True

    def queue_render(
        self, job_id: int, *, plan_path: Path, output_path: Path,
        max_output_bytes: int | None = DEFAULT_MAX_OUTPUT_BYTES,
        target_output_bytes: int | None = DEFAULT_TARGET_OUTPUT_BYTES,
    ) -> dict:
        """Queue the approved export: a PENDING render stage plus the ``render:{id}`` setting.

        ``render:{id}`` only tells the worker what to render and records the
        last export; it stays after the request is retired or finished. No
        check reads it to decide whether a job is exporting or whether its
        source may be cleaned: the render stage state (render_request) and the
        job state decide that.
        """
        if (max_output_bytes is None) != (target_output_bytes is None):
            raise ValueError("Output maximum and target must both be set or both be unlimited")
        with self.job_action_lock:
            # Defensive: finalize refuses a cleaned source before it gets here.
            self._refuse_locked(job_id)
            # Finalizing an export that is already waiting keeps its place.
            reseq = self.store.get_job(job_id)["state"] != "QUEUED"
            # A1b: a new request starts clean, NVENC beside a scan allowed again; a
            # record an earlier request's render left (handed back to the main lane) goes.
            export_deferral_path(self.root, output_path).unlink(missing_ok=True)
            render_stall_path(self.root, output_path).unlink(missing_ok=True)
            with self._lock:
                self._sequential_exports.discard(job_id)
                self._parallel_decisions.pop(job_id, None)
            self.store.ensure_stage(job_id, "render")
            self.store.update_stage(job_id, "render", state="PENDING", error=None, pid=None)
            self.store.set_setting(f"render:{job_id}", {
                "plan": plan_path.resolve().relative_to(self.root).as_posix(),
                "output": output_path.resolve().relative_to(self.root).as_posix(),
                "max_output_bytes": max_output_bytes,
                "target_output_bytes": target_output_bytes,
            })
            # current_stage marks the waiting job as an export for the dashboard.
            value = self.store.mark_queued(job_id, reseq=reseq, current_stage="render")
            self.store.add_event(job_id, "EXPORT_QUEUED", "Approved final export queued")
            self._wake.set()
            return value

    def _definitions(self, job: dict[str, Any]) -> dict[str, PipelineStage]:
        # A job queued before verify_adult existed keeps its stored stage list; its
        # build_review must then read adult/scan.json, not a verified copy never made.
        stored = [stage["name"] for stage in self.store.stages(int(job["id"]))]
        legacy_adult = bool(stored) and "adult" in stored and "verify_adult" not in stored
        result = {item.name: item for item in pipeline_stages(
            root=self.root, job_key=self._pipeline_key(job), source=Path(job["source_path"]),
            content_style=job["content_style"], profile=job["profile"],
            source_sha256=job["source_sha256"],
            detector_groups=self.detector_groups(int(job["id"])),
            ocr_recognition_batch_size=self.ocr_batch_size(int(job["id"])),
            fast_scan=self.fast_scan(int(job["id"])),
            adult_verification=False if legacy_adult else None,
        )}
        render = self.store.setting(f"render:{job['id']}")
        if render:
            output = self.root / render["output"]
            maximum_output_bytes = render.get(
                "max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES,
            )
            target_output_bytes = render.get(
                "target_output_bytes", DEFAULT_TARGET_OUTPUT_BYTES,
            )
            render_arguments: list[object] = [
                "--plan", self.root / render["plan"], "--output", output,
            ]
            if maximum_output_bytes is None:
                render_arguments.append("--no-output-size-limit")
            else:
                render_arguments += [
                    "--max-output-bytes", maximum_output_bytes,
                    "--target-output-bytes", target_output_bytes,
                ]
            result["render"] = PipelineStage(
                "render", "RENDERING",
                (StageCommand(run_command(
                    self.root, "render-final", *render_arguments,
                ), (output.with_suffix(output.suffix + ".manifest.json"),)),),
                uses_gpu=False,
            )
        return result

    def recover_finished_stages(self) -> list[int]:
        """Finish jobs a restart caught between their last stage and its result.

        Runs once at startup, after recover_interrupted() and the import: a
        build_review or render stage that already COMPLETED gets its revision,
        structure audit or final output recorded, as the worker would have done.
        """
        recovered = []
        for job in self.store.list_jobs():
            if job["state"] != "INTERRUPTED_RECOVERABLE":
                continue
            if self.store.next_pending_stage(int(job["id"])) is not None:
                continue
            with self._start_lock:
                if self._finish_interrupted(job):
                    recovered.append(int(job["id"]))
        return recovered

    def _finish_interrupted(self, job: dict[str, Any]) -> bool:
        """Redo _after_success for a completed final stage; False when not applicable.

        When the result cannot be recorded (missing review queue, output or
        manifest) the stage goes back to PENDING so Tiếp tục runs it again:
        build_review is deterministic and a render without output starts clean.
        """
        job_id = int(job["id"])
        name = job.get("current_stage")
        if name not in FINISHING_STAGES:
            return False
        stage = next((item for item in self.store.stages(job_id) if item["name"] == name), None)
        if stage is None or stage["state"] != "COMPLETED":
            return False
        try:
            definition = self._definitions(job).get(name)
            if definition is None:
                raise RuntimeError(f"No {name} settings are stored for this job")
            if name == "render":
                output = self.root / self.store.setting(f"render:{job_id}")["output"]
                manifest = json.loads(
                    output.with_suffix(output.suffix + ".manifest.json").read_text(encoding="utf-8")
                )
                if not output.is_file() or manifest.get("status") != "COMPLETED":
                    raise RuntimeError(f"Final output is not complete: {output}")
            self._after_success(job_id, name, definition)
        except Exception as error:  # noqa: BLE001 - any failure falls back to a clean rerun
            self.store.update_stage(job_id, name, state="PENDING", pid=None,
                                    error=f"Interrupted before its result was recorded: {error}")
            self.store.add_event(
                job_id, "STAGE_RECOVERY_RESET",
                f"Stage {name} will run again: its result was not recorded before the restart",
                level="WARNING", payload={"stage": name, "error": str(error)},
            )
            return False
        self.store.update_job(job_id, error=None)
        self.store.add_event(
            job_id, "JOB_RECOVERED",
            f"Recorded the {name} result that finished before the restart",
            payload={"stage": name},
        )
        return True

    def _runnable(self, job: dict[str, Any]) -> tuple[dict[str, Any], PipelineStage] | None:
        stage = self.store.next_pending_stage(job["id"])
        if stage is None:
            return None
        definition = self._definitions(job).get(stage["name"])
        return (stage, definition) if definition else None

    def _export_next(self, job: dict[str, Any]) -> bool:
        stage = self.store.next_pending_stage(int(job["id"]))
        return stage is not None and stage["name"] == "render"

    def _waiting(
        self, *, first_only: bool = False,
    ) -> list[tuple[dict[str, Any], dict[str, Any], PipelineStage]]:
        """Runnable waiting jobs, in the order the worker takes them.

        Scans and exports share the single worker. Within a priority a waiting
        export goes before every waiting scan (perf plan A1a), so Xuất video no
        longer waits behind scans: it runs at the next stage boundary. Each kind
        keeps its click order (queue_seq), and a scan between two of its stages
        keeps its place among the scans. first_only stops at the job the worker
        takes, so the stage lists of the jobs behind it are not built.
        """
        waiting = []
        for item in self._iter_waiting():
            waiting.append(item)
            if first_only:
                break
        return waiting

    def _iter_waiting(
        self, *, exports_only: bool = False,
    ) -> Iterator[tuple[dict[str, Any], dict[str, Any], PipelineStage]]:
        """_waiting() one job at a time: the stage lists behind are built only when asked for."""
        # sorted() is stable: equal keys keep queued_jobs()' order.
        keyed = [(job, self._export_next(job)) for job in self.store.queued_jobs()]
        ordered = sorted(keyed, key=lambda pair: (pair[0]["priority"], not pair[1]))
        for job, export_next in ordered:
            if exports_only and not export_next:
                continue
            runnable = self._runnable(job)
            if runnable is not None:
                yield (job, *runnable)

    def queue_order(self) -> list[dict[str, Any]]:
        """Runnable waiting jobs in the exact order the worker will take them."""
        return [
            {
                "job_id": int(job["id"]), "position": position,
                "kind": "export" if stage["name"] == "render" else "scan",
            }
            for position, (job, stage, _definition) in enumerate(self._waiting(), start=1)
        ]

    def _select(
        self, lane: str = MAIN_LANE,
    ) -> tuple[dict[str, Any], dict[str, Any], PipelineStage] | None:
        """The next stage this lane takes, or None.

        "Tạm dừng hàng đợi" (scheduler_paused) and shutdown stop both lanes from
        starting a stage; a stage already running in either lane goes on.
        """
        if self.store.setting("scheduler_paused", False) or self._draining.is_set():
            return None
        if lane == EXPORT_LANE:
            return self._select_parallel_export()
        if lane != MAIN_LANE:
            raise ValueError(f"Unknown scheduler lane: {lane}")
        return self._select_main()

    def _select_main(self) -> tuple[dict[str, Any], dict[str, Any], PipelineStage] | None:
        """The main lane: A1a's order, unless an export must wait for the export lane.

        With "Tắt" (or no VRAM measurement yet) and an idle export lane this is
        exactly the single worker of before. Otherwise, when the first waiting
        stage is an export, the main lane takes the first waiting scan stage
        instead while the export lane runs an
        export (one render at a time), while that export goes to the export lane
        beside the scan stage the main lane is about to start, or beside the first
        GPU stage after the short CPU stages it is about to start (A1c: a film at
        preflight), and while it was handed back from the export lane and the scan
        job that ran beside it still has stages to run (perf plan A1b: x264 after
        the scan, never cutting in).
        """
        parallel = self._parallel_allowed()
        export_lane_busy = self._lane_busy(EXPORT_LANE)
        if not parallel and not export_lane_busy:
            waiting = self._waiting(first_only=True)
            return waiting[0] if waiting else None
        waiting = self._iter_waiting()
        first = next(waiting, None)
        if first is None or first[2].name != "render":
            return first
        next_scan = next((item for item in waiting if item[2].name != "render"), None)
        if next_scan is not None and self._export_waits(
            first, next_scan, parallel=parallel, export_lane_busy=export_lane_busy,
        ):
            return next_scan
        if export_lane_busy:
            return None
        return first

    def _export_waits(
        self, export: tuple[dict[str, Any], dict[str, Any], PipelineStage],
        next_scan: tuple[dict[str, Any], dict[str, Any], PipelineStage],
        *, parallel: bool, export_lane_busy: bool,
    ) -> bool:
        """Whether the main lane runs ``next_scan`` before the first waiting ``export``."""
        if export_lane_busy:
            return True
        if not parallel:
            return False
        lane = self._threads.get(EXPORT_LANE)
        if lane is not None and not lane.is_alive():
            # An error ended the export lane's thread: nothing would take the export
            # beside the scan, so it never waits for that lane.
            return False
        job_id = int(export[0]["id"])
        if self._handed_back(job_id):
            with self._lock:
                last_scan = self._last_scan_job_id
            return int(next_scan[0]["id"]) == last_scan
        with self._lock:
            if job_id in self._sequential_exports:
                return False
        if self._first_gpu_stage(next_scan) is None:
            return False
        # Nothing runs on the GPU now: what is in use is the baseline of the stage to come.
        decision = self._lane_decision(export, baseline_mib=None)
        if not decision.parallel:
            self._turn_down(job_id, decision)
        return decision.parallel

    def _first_gpu_stage(
        self, scan: tuple[dict[str, Any], dict[str, Any], PipelineStage],
    ) -> PipelineStage | None:
        """The GPU stage the export would run beside if the main lane takes ``scan``, or None.

        The waiting stage itself when it runs on the GPU (A1b). When it is a short
        CPU stage (A1c, SHORT_CPU_STAGES: preflight), the first GPU stage among the
        job's stages still to run after it, reached through short CPU stages only;
        the export lane takes the export once that stage's process starts, and the
        main lane judges the export again (VRAM read anew) at that stage's boundary.
        None when another CPU stage or no GPU stage comes first: the export then goes
        first, as before.
        """
        job, stage, definition = scan
        if runs_on_gpu(definition):
            return definition
        if definition.name not in SHORT_CPU_STAGES:
            return None
        definitions = self._definitions(job)
        rows = self.store.stages(int(job["id"]))
        names = [row["name"] for row in rows]
        if stage["name"] not in names:
            return None
        for row in rows[names.index(stage["name"]) + 1:]:
            # The stages next_pending_stage would hand out next, in order.
            if row["state"] not in {"PENDING", "FAILED_RETRYABLE"}:
                continue
            following = definitions.get(row["name"])
            if following is None:
                return None
            if runs_on_gpu(following):
                return following
            if following.name not in SHORT_CPU_STAGES:
                return None
        return None

    def _handed_back(self, job_id: int) -> bool:
        render = self.store.setting(f"render:{job_id}")
        if not isinstance(render, dict) or not render.get("output"):
            return False
        return os.path.lexists(export_deferral_path(self.root, self.root / str(render["output"])))

    def _render_stall(self, job_id: int) -> str | None:
        """The Vietnamese message the watch on FFmpeg left for this job's render, if it stopped it."""
        render = self.store.setting(f"render:{job_id}")
        if not isinstance(render, dict) or not render.get("output"):
            return None
        return read_render_stall(self.root, self.root / str(render["output"]))

    def _probe_frame(self, source: Path) -> dict:
        """ffprobe's answer for a source, kept per (path, size, mtime) between checks."""
        stat = source.stat()
        key = (str(source), stat.st_size, stat.st_mtime_ns)
        with self._lock:
            known = self._frame_probes.get(key)
        if known is None:
            known = probe_video(self.root / "tools" / "ffmpeg" / "bin" / "ffprobe.exe", source)
            with self._lock:
                if len(self._frame_probes) >= 64:
                    self._frame_probes.clear()
                self._frame_probes[key] = known
        return known

    def _lane_decision(
        self, export: tuple[dict[str, Any], dict[str, Any], PipelineStage], *, baseline_mib: int | None,
    ) -> export_lanes.LaneDecision:
        """Whether this export may run in the export lane beside a scan (export_lanes)."""
        job_id = int(export[0]["id"])
        candidate = export_lanes.export_candidate(
            self.root, self.store.setting(f"render:{job_id}"), probe=self._probe_frame,
        )
        if not candidate.eligible:
            return export_lanes.LaneDecision(False, candidate.reason, {
                key: value for key, value in (("width", candidate.width), ("height", candidate.height))
                if value is not None
            })
        read = self.read_vram()
        if baseline_mib is None and read is not None:
            baseline_mib = read.used_mib
        decision = export_lanes.vram_decision(read, baseline_mib=baseline_mib, need_mib=int(candidate.need_mib))
        return export_lanes.LaneDecision(decision.parallel, decision.reason, {
            **decision.detail, "width": candidate.width, "height": candidate.height,
        })

    def _select_parallel_export(self) -> tuple[dict[str, Any], dict[str, Any], PipelineStage] | None:
        """The export lane: the first waiting export, beside the main lane's GPU stage, when allowed.

        Each export is judged once: turned down, it runs in the main lane at its
        next stage boundary (exports first), as with "Tắt".
        """
        if not self._parallel_allowed():
            return None
        with self._lock:
            gpu = self._gpu_stage
            if gpu is None or EXPORT_LANE in self._active or EXPORT_LANE in self._executing:
                return None
        export = next((item for item in self._iter_waiting(exports_only=True) if item[2].name == "render"), None)
        if export is None:
            return None
        job_id = int(export[0]["id"])
        with self._lock:
            if job_id in self._sequential_exports:
                return None
        decision = self._lane_decision(export, baseline_mib=gpu.baseline_mib)
        if not decision.parallel:
            self._turn_down(job_id, decision)
            return None
        with self._lock:
            if self._gpu_stage is not gpu:
                # The scan stage it was judged beside ended meanwhile: judge again.
                return None
            self._parallel_decisions[job_id] = export_lanes.LaneDecision(True, decision.reason, {
                **decision.detail, "scan_job_id": gpu.job_id, "scan_stage": gpu.stage,
            })
        return export

    def _turn_down(self, job_id: int, decision: export_lanes.LaneDecision) -> None:
        """The export runs in the main lane; its events say why, once per export request."""
        with self._lock:
            if job_id in self._sequential_exports:
                return
            self._sequential_exports.add(job_id)
        self.store.add_event(
            job_id, "EXPORT_LANE_SEQUENTIAL", decision.text,
            payload={"reason": decision.reason, **decision.detail},
        )

    def _run(self, lane: str = MAIN_LANE) -> None:
        while not self._stop.is_set():
            selection = self._select(lane)
            if selection is None:
                self._wake.wait(lane, self.poll_seconds)
                continue
            with self._lock:
                self._executing[lane] = int(selection[0]["id"])
            try:
                self._execute(*selection, lane=lane)
            finally:
                with self._lock:
                    self._executing.pop(lane, None)
                # The other lane looks again at once (a stage boundary, a free export lane).
                self._wake.set()

    def _execute(
        self, job: dict[str, Any], stage_row: dict[str, Any], definition: PipelineStage,
        lane: str = MAIN_LANE,
    ) -> None:
        job_id = int(job["id"])
        name = definition.name
        # A pause, cancel or stop that landed after _select wins: run nothing.
        if not self.store.claim_queued(job_id, definition.job_state, name):
            return
        attempt = int(stage_row["attempt"]) + 1
        log_dir = self.root / "logs" / "control-center"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"job-{job_id}-{name}-attempt-{attempt}.log"
        # Conditional too: a Hủy or Dừng ngay landing since the claim wins.
        if not self.store.start_stage(job_id, name, job_state=definition.job_state, attempt=attempt,
                                      progress=0.0, started_at=now_iso(), completed_at=None,
                                      heartbeat_at=now_iso(), error=None):
            return
        self.store.add_event(job_id, "STAGE_STARTED", f"Stage {name} started",
                             payload={"attempt": attempt, "log": log_path.relative_to(self.root).as_posix()})
        self._note_lane_start(job_id, name, lane)
        try:
            if name == "preflight":
                self._run_preflight(job)
            artifacts = tuple(
                artifact
                for command in definition.commands
                for artifact in command.expected_artifacts
            )
            report_root = self.root / "reports" / "jobs" / self._pipeline_key(job)
            cache_key = self._stage_cache.key(
                stage_name=name, source_sha256=str(job["source_sha256"]),
                source_path=Path(job["source_path"]), report_root=report_root,
                commands=(command.argv for command in definition.commands),
                artifact_paths=artifacts,
            ) if self._stage_cache.cacheable(name, artifacts) else None
            cache_hit = self._stage_cache.restore(
                stage_name=name,
                source_sha256=str(job["source_sha256"]),
                source_path=Path(job["source_path"]),
                report_root=report_root,
                commands=(command.argv for command in definition.commands),
                artifact_paths=artifacts,
            )
            if cache_hit is not None:
                self.store.add_event(
                    job_id, "STAGE_CACHE_HIT",
                    f"Stage {name} restored from an exact source/config cache",
                    payload={"stage": name, "cache_key": cache_hit["key"]},
                )
                for artifact in artifacts:
                    self._register_artifact(job_id, name, artifact)
            else:
                for command in definition.commands:
                    self._run_process(job_id, definition, command, log_path, lane)
                    for artifact in command.expected_artifacts:
                        self._register_artifact(job_id, name, artifact)
                try:
                    cached = self._stage_cache.store(
                        stage_name=name,
                        source_sha256=str(job["source_sha256"]),
                        source_path=Path(job["source_path"]),
                        report_root=report_root,
                        commands=(command.argv for command in definition.commands),
                        artifact_paths=artifacts,
                        expected_key=cache_key,
                    )
                except StageCachePathTooLong as skipped:
                    # The stage's own result is complete; only its exact-rerun copy is left out.
                    cached = None
                    warning = (f"Stage {name} finished but was not saved to the stage cache: {skipped}. "
                               "It will run again instead of being restored. A shorter install folder "
                               "or Windows long paths would allow the cache.")
                    with log_path.open("ab") as handle:
                        handle.write(f"\nWARNING: {warning}\n  path: {skipped.path}\n".encode("utf-8"))
                    self.store.add_event(job_id, "STAGE_CACHE_SKIPPED", warning, level="WARN",
                                         payload={"stage": name, "path_characters": len(skipped.path),
                                                  "limit": skipped.limit})
                if cached is not None:
                    pruning = self._stage_cache.prune()
                    file_pruning = prune_file_caches(self.root)
                    self.store.add_event(
                        job_id, "STAGE_CACHE_STORED",
                        f"Stage {name} saved for exact reruns",
                        payload={
                            "stage": name,
                            "cache_key": cached["key"],
                            **pruning,
                            "file_cache_pruning": file_pruning,
                        },
                    )
            self.store.update_stage(job_id, name, state="COMPLETED", progress=1.0,
                                    pid=None, heartbeat_at=now_iso(), completed_at=now_iso(),
                                    error=None)
            self._after_success(job_id, name, definition)
        except Exception as error:
            with self._lock:
                process = self._process.get(lane)
            if process and process.poll() is None:
                self._terminate_process(process)
            self._release_lane(lane)
            self._cleanup_interrupted_stage(job_id, name)
            current = self.store.get_job(job_id)
            if current["state"] in STOPPED_BY_USER_STATES:
                return
            if isinstance(error, _ExportHandedBack):
                self._hand_back_export(job_id)
                return
            self.store.update_stage(job_id, name, state="FAILED_RETRYABLE", pid=None,
                                    completed_at=now_iso(), error=str(error))
            self.store.update_job(job_id, state="FAILED", error=str(error), current_stage=name)
            self.store.add_event(job_id, "STAGE_FAILED", str(error), level="ERROR",
                                 payload={"stage": name})

    def _note_lane_start(self, job_id: int, name: str, lane: str) -> None:
        """Remember the main lane's scan job; record why an export runs beside a scan."""
        with self._lock:
            decision = self._parallel_decisions.pop(job_id, None)
            if lane == MAIN_LANE and name != "render":
                self._last_scan_job_id = job_id
        if lane == EXPORT_LANE and decision is not None:
            self.store.add_event(
                job_id, "EXPORT_LANE_PARALLEL", decision.text,
                payload={"reason": decision.reason, **decision.detail},
            )

    def _run_process(
        self, job_id: int, definition: PipelineStage, command: StageCommand, log_path: Path, lane: str,
    ) -> None:
        """Run one command of a stage in its lane; RuntimeError when it fails."""
        name = definition.name
        gpu = lane == MAIN_LANE and runs_on_gpu(definition) and self._parallel_allowed()
        # A1b: what the GPU holds before this scan process starts, for the export lane.
        baseline = self.read_vram() if gpu else None
        # Checked and published under _lock. cancel and pause_now write the job
        # state first and read _active under _lock after, so either this check
        # sees their write or they see the process and stop it.
        with self._lock:
            if self.store.get_job(job_id)["state"] in STOPPED_BY_USER_STATES:
                raise RuntimeError(f"Stage {name} was stopped before its process started")
            log_handle = log_path.open("ab")
            kwargs: dict[str, Any] = {"cwd": self.root, "stdout": log_handle, "stderr": subprocess.STDOUT}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            if lane == EXPORT_LANE:
                kwargs["env"] = {**os.environ, **export_lanes.PARALLEL_RENDER_ENVIRONMENT}
            try:
                process = subprocess.Popen(command.argv, **kwargs)
            except BaseException:
                log_handle.close()
                raise
            self._process[lane] = process
            self._active[lane] = (job_id, name)
            self._log_handle[lane] = log_handle
            if gpu:
                self._gpu_stage = _GpuStage(
                    next(self._gpu_stage_runs), job_id, name,
                    baseline.used_mib if baseline is not None else None,
                )
        if gpu:
            self._wake.set()  # the export lane may now judge a waiting export
        self.store.update_stage(job_id, name, pid=process.pid, heartbeat_at=now_iso())
        while process.poll() is None:
            if self._stop.wait(1.0):
                break
            self.store.update_stage(job_id, name, heartbeat_at=now_iso())
        if process.poll() is None:
            self._terminate_process(process)
        code = process.wait()
        self._release_lane(lane)
        if code != 0:
            # The watch on FFmpeg stopped the render (render_watchdog): the stage
            # fails with its message in either lane, never handed back to the main lane.
            stalled = self._render_stall(job_id) if name == "render" else None
            if stalled is not None:
                raise RuntimeError(stalled)
            if lane == EXPORT_LANE and name == "render" and self._handed_back(job_id):
                raise _ExportHandedBack(f"Stage process exited with code {code}; see {log_path}")
            raise RuntimeError(f"Stage process exited with code {code}; see {log_path}")

    def _release_lane(self, lane: str) -> None:
        """The lane's stage process ended: forget it and close its log."""
        with self._lock:
            self._process.pop(lane, None)
            self._active.pop(lane, None)
            log_handle = self._log_handle.pop(lane, None)
            if lane == MAIN_LANE:
                self._gpu_stage = None
        if log_handle is not None and not log_handle.closed:
            log_handle.close()

    def _hand_back_export(self, job_id: int) -> None:
        """The export lane's render stopped and left its record: the main lane finishes it.

        The render stage waits again (RUNNING to PENDING, never over a pause or a
        cancel) and the job keeps its place; the main lane runs it with x264 once
        the scan job that ran beside it has no stage left to run (_select_main).
        """
        render = self.store.setting(f"render:{job_id}") or {}
        record = None
        if render.get("output") and render.get("plan"):
            record = read_export_deferral(
                self.root, self.root / str(render["output"]), self.root / str(render["plan"]),
            )
        if not self.store.requeue_running_stage(job_id, "render"):
            return
        with self._lock:
            self._sequential_exports.add(job_id)
        job = self.store.get_job(job_id)
        if job.get("stop_mode") == "AFTER_STAGE" or self._stop.is_set():
            self.store.update_job_if(job_id, states=IN_PROCESS_STATES, state="PAUSED",
                                     stop_mode="PAUSED", current_stage="render")
        else:
            # Conditional: a pause or cancel landing right now still wins.
            self.store.update_job_if(job_id, states=IN_PROCESS_STATES, state="QUEUED",
                                     current_stage="render")
        reason = record["reason"] if record is not None else None
        self.store.add_event(
            job_id, "EXPORT_DEFERRED", EXPORT_DEFERRED_MESSAGES.get(reason, EXPORT_HANDED_BACK_MESSAGE),
            level="WARNING", payload={"reason": reason, "fallback": record.get("fallback") if record else None},
        )

    def _register_artifact(self, job_id: int, stage_name: str, artifact: Path) -> None:
        validate_json_artifact(artifact)
        relative = artifact.resolve().relative_to(self.root).as_posix()
        self.store.add_artifact(
            job_id, stage_name=stage_name, kind=stage_name,
            path=relative, bytes_count=artifact.stat().st_size,
        )

    def _run_preflight(self, job: dict[str, Any]) -> None:
        source = Path(job["source_path"])
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"Source is missing: {source}")
        stat = source.stat()
        if stat.st_size != int(job["source_size_bytes"]):
            raise ValueError("Source size changed after the job was imported")
        if sha256_file(source) != job["source_sha256"]:
            raise ValueError("Source checksum changed after the job was imported")
        for required in (
            self.root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
            self.root / "tools" / "ffmpeg" / "bin" / "ffprobe.exe",
            self.root / "scripts" / "run.ps1",
        ):
            if not required.exists():
                raise FileNotFoundError(f"Required tool is missing: {required}")
        probe = probe_video(self.root / "tools" / "ffmpeg" / "bin" / "ffprobe.exe", source)
        duration = duration_seconds(probe)
        if duration <= 0:
            raise ValueError("Source video has no valid duration")
        license_result = audit_project_models(self.root)
        if license_result.get("blocked_count", 0):
            raise RuntimeError("Model license audit contains blocked models")
        free_bytes = shutil.disk_usage(self.root).free
        if free_bytes < 5_000_000_000:
            raise RuntimeError("Less than 5 GB remains on the project drive")
        self.store.update_job(int(job["id"]), duration_seconds=duration)
        self.store.add_event(int(job["id"]), "PREFLIGHT_OK", "Source, tools, models and storage passed")

    def _after_success(self, job_id: int, name: str, definition: PipelineStage) -> None:
        stages = self.store.stages(job_id)
        completed = sum(1 for item in stages if item["state"] == "COMPLETED")
        progress = completed / max(1, len(stages))
        job = self.store.get_job(job_id)
        self.store.add_event(job_id, "STAGE_COMPLETED", f"Stage {name} completed")
        if name == "build_review":
            queue = self.root / "reports" / "jobs" / self._pipeline_key(job) / "review-queue.json"
            payload = json.loads(queue.read_text(encoding="utf-8"))
            relative = queue.relative_to(self.root).as_posix()
            revision = self.store.add_revision(job_id, relative, payload.get("status"), payload.get("updated_at"))
            self.store.activate_revision(job_id, revision)
            structure_audit = run_local_queue_audit(
                root=self.root, job=job, queue_path=queue,
            )
            structure_audit["created_at"] = now_iso()
            structure_path = queue.parent / "structure-audit.json"
            temporary = structure_path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(structure_audit, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(structure_path)
            self.store.add_artifact(
                job_id, stage_name="build_review", kind="structure_audit",
                path=structure_path.relative_to(self.root).as_posix(),
                bytes_count=structure_path.stat().st_size,
            )
            self.store.add_event(
                job_id, "STRUCTURE_AUDIT_COMPLETED",
                f"Local structure audit: {structure_audit['result']} — "
                f"{structure_audit['summary']}",
                payload={"queue_path": relative},
            )
            state = "READY_TO_EXPORT" if payload.get("status") == "READY_FOR_EDIT_PLAN" else "WAITING_REVIEW"
            self.store.update_job(job_id, state=state, current_stage=None, progress=progress, stop_mode=None)
            return
        if name == "render":
            render = self.store.setting(f"render:{job_id}")
            output = self.root / render["output"]
            self.store.add_artifact(job_id, stage_name="render", kind="final_output",
                                    path=output.relative_to(self.root).as_posix(),
                                    bytes_count=output.stat().st_size)
            self.store.update_job(job_id, state="COMPLETED", current_stage=None,
                                  progress=1.0, stop_mode=None, error=None)
            self._cleanup_interrupted_stage(job_id, name)
            return
        job = self.store.get_job(job_id)
        if job["state"] in STOPPED_BY_USER_STATES:
            # Dừng ngay or Hủy landed while a stage without a subprocess (cache
            # hit, preflight) finished: keep the user's choice, never requeue.
            self.store.update_job(job_id, current_stage=None, progress=progress)
        elif job.get("stop_mode") == "AFTER_STAGE" or self._stop.is_set():
            self.store.update_job_if(
                job_id, states=IN_PROCESS_STATES, state="PAUSED", current_stage=None,
                stop_mode="PAUSED", progress=progress,
            )
        else:
            # Conditional: a pause or cancel landing right now still wins.
            self.store.update_job_if(
                job_id, states=IN_PROCESS_STATES, state="QUEUED", current_stage=None,
                progress=progress,
            )

    def _terminate_process(self, process: subprocess.Popen) -> None:
        terminate_process_tree(process)

    def _cleanup_interrupted_stage(self, job_id: int, stage_name: str) -> None:
        if stage_name != "render":
            return
        render = self.store.setting(f"render:{job_id}")
        if not isinstance(render, dict) or not render.get("output"):
            return
        output_root = (self.root / "output").resolve()
        output = (self.root / str(render["output"])).resolve()
        if output == output_root or output_root not in output.parents:
            return
        partial = output.with_name(output.stem + ".partial" + output.suffix)
        partial.unlink(missing_ok=True)
        render_progress_path(self.root, output).unlink(missing_ok=True)
        # A stop kills the render-final process before it can remove its graph file.
        render_graph_path(self.root, output).unlink(missing_ok=True)
        # Read (or not needed) by now: _run_process takes its message before this runs.
        render_stall_path(self.root, output).unlink(missing_ok=True)

    def _terminate_active(self, reason: str) -> None:
        """Stop the stage process of every lane (shutdown timeout)."""
        for lane in LANES:
            self._terminate_lane(lane)

    def _terminate_lane(self, lane: str) -> None:
        """Stop this lane's stage process only; the other lane's stage runs on."""
        with self._lock:
            process = self._process.get(lane)
            active = self._active.get(lane)
        if process and process.poll() is None:
            self._terminate_process(process)
        if active:
            self._cleanup_interrupted_stage(*active)


class InputWatcher:
    def __init__(self, root: Path, store: JobStore, *, stable_seconds: float = 60.0,
                 poll_seconds: float = 5.0):
        self.root = root.resolve(strict=True)
        self.store = store
        self.stable_seconds = stable_seconds
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="biliflow-input-watcher", daemon=False)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(self.poll_seconds + 2)

    def scan_once(self) -> int:
        imported = 0
        ffprobe = self.root / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"
        # Sources that "Dọn video gốc" moved to the Recycle Bin, by path: one
        # query per scan, used to reject a different file put at an old path.
        recycled = {
            source_path_key(row["source_path"]): row for row in self.store.recycled_cleanups()
        }
        for path in sorted((self.root / "input").iterdir()):
            try:
                if self._scan_file(path, ffprobe, recycled):
                    imported += 1
            except FileNotFoundError:
                # The file left input/ between iterdir() and stat() or the hash;
                # the rest of the scan still runs (no WATCHER_ERROR).
                continue
        return imported

    def _scan_file(self, path: Path, ffprobe: Path, recycled: dict[str, dict[str, Any]]) -> bool:
        """Observe one input file and hash it once it is stable; True when a job was discovered.

        Each (path, size, mtime) is hashed once: mark_file_imported records the
        result. A file whose SHA-256 belongs to a cleaned job never moves the
        job's source_path and never creates a second job for the same video.
        """
        if not path.is_file() or path.suffix.casefold() not in VIDEO_EXTENSIONS:
            return False
        stat = path.stat()
        observed = self.store.observe_file(path, stat.st_size, stat.st_mtime_ns)
        imported_job_id = observed.get("imported_job_id")
        if imported_job_id:
            # A cleanup resets the watcher row (reset_watched_file), so a source
            # put back counts as new and waits to be stable. Should that reset
            # be missing, a source restored with its old size and mtime would
            # stay "imported" for ever: the path of a recycled source still
            # marked with that same job is hashed once more.
            row = recycled.get(source_path_key(observed["path"]))
            if row is None or int(row["job_id"]) != int(imported_job_id):
                return False
        stable_since = datetime.fromisoformat(observed["stable_since"])
        elapsed = (datetime.now(stable_since.tzinfo) - stable_since).total_seconds()
        if elapsed < self.stable_seconds:
            return False
        digest = sha256_file(path)
        job = self.store.find_by_sha(digest)
        if job is not None:
            cleanup = self.store.latest_source_cleanup(int(job["id"]))
            if cleanup is not None and cleanup["state"] == "RECYCLED":
                self._source_came_back(path, stat, digest, cleanup)
            # A PENDING row (the shell call has not answered yet) and every
            # other known video: only mark the file, never upsert.
            self.store.mark_file_imported(path, int(job["id"]))
            return False
        duration = duration_seconds(probe_video(ffprobe, path))
        job = self.store.upsert_job(
            job_key=safe_job_key(path, digest), source_path=path,
            source_sha256=digest, source_size_bytes=stat.st_size,
            source_mtime_ns=stat.st_mtime_ns, duration_seconds=duration,
            state="NEEDS_METADATA",
        )
        self.store.add_event(job["id"], "INPUT_DISCOVERED",
                             "Stable input discovered; choose content style")
        rejected = recycled.get(source_path_key(path.resolve()))
        if rejected is not None:
            # A different video at the path of a recycled source: the old job
            # stays locked and this file became a new job (still needs Bắt đầu).
            self.store.add_event(
                int(rejected["job_id"]), "SOURCE_RESTORE_REJECTED", SOURCE_RESTORE_REJECTED_MESSAGE,
                level="WARNING", payload={
                    "path": str(path.resolve()), "size_bytes": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns, "sha256": digest,
                    "expected_sha256": rejected["source_sha256"],
                },
            )
        self.store.mark_file_imported(path, int(job["id"]))
        return True

    def _source_came_back(self, path: Path, stat: os.stat_result, digest: str,
                          cleanup: dict[str, Any]) -> None:
        """The SHA-256 of a recycled source is in input/ again: restore or explain."""
        job_id = int(cleanup["job_id"])
        resolved = str(path.resolve())
        if (source_path_key(resolved) == source_path_key(cleanup["source_path"])
                and stat.st_size == int(cleanup["size_bytes"])):
            # Same path, same size, same SHA-256: the job is unlocked and takes
            # the new mtime. None means another writer settled the row first.
            if self.store.mark_source_restored(int(cleanup["id"]), mtime_ns=stat.st_mtime_ns) is not None:
                self.store.add_event(
                    job_id, "SOURCE_RESTORED", SOURCE_RESTORED_MESSAGE,
                    payload={"path": resolved, "size_bytes": stat.st_size, "sha256": digest},
                )
            return
        self.store.add_event(
            job_id, "SOURCE_RESTORE_WRONG_PATH",
            SOURCE_RESTORE_WRONG_PATH_MESSAGE.format(file_name=Path(cleanup["source_path"]).name),
            level="WARNING",
            payload={"path": resolved, "expected_path": cleanup["source_path"], "sha256": digest},
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception as error:
                self.store.add_event(None, "WATCHER_ERROR", str(error), level="ERROR")
            self._stop.wait(self.poll_seconds)
