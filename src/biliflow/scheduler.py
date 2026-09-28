from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from biliflow.job_import import VIDEO_EXTENSIONS
from biliflow.codex_supervisor import run_local_queue_audit
from biliflow.job_pipeline import (
    DEFAULT_DETECTOR_GROUPS,
    PipelineStage,
    StageCommand,
    normalize_detector_groups,
    normalize_ocr_batch_size,
    pipeline_stages,
    run_command,
    safe_job_key,
    validate_json_artifact,
)
from biliflow.job_store import JobStore, now_iso, sha256_file
from biliflow.final_renderer import (
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_TARGET_OUTPUT_BYTES,
    render_progress_path,
)
from biliflow.probe import duration_seconds, probe_video
from biliflow.license_policy import audit_project_models
from biliflow.stage_cache import StageArtifactCache
from biliflow.cleanup import prune_file_caches


ACTIVE_STATES = {
    "PREFLIGHT", "SCANNING_SAFETY", "SCANNING_TEXT", "SCANNING_LOGO",
    "LOCALIZING_REGIONS", "BUILDING_REVIEW", "RENDERING", "VERIFYING",
}


class JobScheduler:
    """One durable worker for GPU scans and final renders.

    Stages are individual subprocesses. A pause or restart repeats only the active
    stage; completed artifacts and review revisions remain intact.
    """

    def __init__(self, root: Path, store: JobStore, *, poll_seconds: float = 1.0):
        self.root = root.resolve(strict=True)
        self.store = store
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._active: tuple[int, str] | None = None
        self._log_handle = None
        self._stage_cache = StageArtifactCache(self.root)

    @property
    def active(self) -> dict[str, Any] | None:
        with self._lock:
            if self._active is None:
                return None
            return {"job_id": self._active[0], "stage": self._active[1],
                    "pid": self._process.pid if self._process else None}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="biliflow-scheduler", daemon=False)
        self._thread.start()

    def shutdown(self, *, immediate: bool = False, timeout: float = 20.0) -> None:
        active = self.active
        if immediate and active:
            self.pause_now(active["job_id"])
        elif active:
            self.store.update_job(active["job_id"], stop_mode="AFTER_STAGE")
            # Leave the worker alive until the current subprocess reaches its
            # durable stage boundary. The explicit Stop script may therefore
            # take as long as that stage, while the browser remains responsive.
            while self.active is not None:
                time.sleep(0.25)
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout)
        if self._thread and self._thread.is_alive():
            self._terminate_active("Control Center shutdown timeout")
            self._thread.join(5)

    def _pipeline_key(self, job: dict[str, Any]) -> str:
        return str(self.store.setting(f"pipeline_key:{job['id']}") or job["job_key"])

    def detector_groups(self, job_id: int) -> tuple[str, ...]:
        stored = self.store.setting(f"detector_groups:{job_id}")
        if stored is None:
            return DEFAULT_DETECTOR_GROUPS
        return normalize_detector_groups(list(stored))

    def ocr_batch_size(self, job_id: int) -> int:
        return normalize_ocr_batch_size(self.store.setting(f"ocr_batch_size:{job_id}", 1))

    def configure_and_queue(
        self, job_id: int, *, content_style: str, profile: str,
        pipeline_key: str | None = None,
        detector_groups: list[str] | tuple[str, ...] | None = None,
        ocr_recognition_batch_size: int | None = None,
    ) -> dict:
        batch_size = (self.ocr_batch_size(job_id) if ocr_recognition_batch_size is None
                      else normalize_ocr_batch_size(ocr_recognition_batch_size))
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
        job = self.store.update_job(
            job_id, content_style=content_style, profile=profile, state="QUEUED",
            current_stage=None, stop_mode=None, error=None, progress=0.0,
        )
        definitions = pipeline_stages(
            root=self.root, job_key=self._pipeline_key(job), source=Path(job["source_path"]),
            content_style=content_style, profile=profile,
            source_sha256=job["source_sha256"],
            detector_groups=selected_detectors,
            ocr_recognition_batch_size=batch_size,
        )
        self.store.replace_stages(job_id, [item.name for item in definitions])
        self.store.add_event(
            job_id, "JOB_QUEUED", f"Queued with {profile} profile",
            payload={"detector_groups": list(selected_detectors), "ocr_recognition_batch_size": batch_size},
        )
        self._wake.set()
        return self.store.get_job(job_id)

    def rerun(
        self, job_id: int,
        *, detector_groups: list[str] | tuple[str, ...] | None = None,
        ocr_recognition_batch_size: int | None = None,
    ) -> dict:
        job = self.store.get_job(job_id)
        if self.active and self.active["job_id"] == job_id:
            raise ValueError("Dừng job hiện tại trước khi chạy lại từ đầu")
        if job["content_style"] == "unknown":
            raise ValueError("Hãy chọn loại nội dung trước khi chạy lại")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        pipeline_key = f"{job['job_key']}-run-{stamp}"
        value = self.configure_and_queue(
            job_id, content_style=job["content_style"], profile=job["profile"],
            pipeline_key=pipeline_key,
            detector_groups=detector_groups,
            ocr_recognition_batch_size=ocr_recognition_batch_size,
        )
        value = self.store.update_job(
            job_id, active_queue_path=None, active_revision=None,
            state="QUEUED", current_stage=None, stop_mode=None, error=None,
            progress=0.0,
        )
        self.store.set_setting(f"render:{job_id}", None)
        self.store.add_event(
            job_id, "JOB_RERUN_QUEUED",
            "Queued a clean scan in a new report revision",
            payload={"pipeline_key": pipeline_key},
        )
        self._wake.set()
        return value

    def resume(self, job_id: int) -> dict:
        job = self.store.get_job(job_id)
        if job["content_style"] == "unknown":
            raise ValueError("Choose animation, live_action, or mixed before starting")
        if not self.store.stages(job_id):
            return self.configure_and_queue(
                job_id, content_style=job["content_style"], profile=job["profile"]
            )
        value = self.store.update_job(
            job_id, state="QUEUED", stop_mode=None, error=None,
        )
        self.store.add_event(job_id, "JOB_RESUMED", "Job returned to the scheduler")
        self._wake.set()
        return value

    def stop_after_stage(self, job_id: int) -> dict:
        value = self.store.update_job(job_id, stop_mode="AFTER_STAGE")
        self.store.add_event(job_id, "STOP_REQUESTED", "Will pause after the current stage")
        return value

    def pause_now(self, job_id: int) -> dict:
        with self._lock:
            active = self._active
        stages = self.store.stages(job_id)
        for stage in stages:
            if stage["state"] == "RUNNING":
                self.store.update_stage(job_id, stage["name"], state="PENDING", pid=None,
                                        heartbeat_at=None, error="Paused; this stage will restart")
        value = self.store.update_job(job_id, state="PAUSED", stop_mode="PAUSED", error=None)
        if active and active[0] == job_id:
            self._terminate_active("Paused by user")
        self.store.add_event(job_id, "JOB_PAUSED", "Paused safely at stage boundary")
        return value

    def cancel(self, job_id: int) -> dict:
        with self._lock:
            active = self._active
        value = self.store.update_job(job_id, state="CANCELLED", stop_mode="CANCELLED")
        for stage in self.store.stages(job_id):
            if stage["state"] == "RUNNING":
                self.store.update_stage(job_id, stage["name"], state="CANCELLED", pid=None,
                                        heartbeat_at=None, error="Cancelled by user")
        if active and active[0] == job_id:
            self._terminate_active("Cancelled by user")
        self.store.add_event(job_id, "JOB_CANCELLED", "Job cancelled; source was not changed")
        return value

    def retry(self, job_id: int) -> dict:
        stages = self.store.stages(job_id)
        failed = next((item for item in stages if item["state"] in {"FAILED", "FAILED_RETRYABLE"}), None)
        if failed:
            self.store.update_stage(job_id, failed["name"], state="PENDING", error=None, pid=None)
        return self.resume(job_id)

    def queue_render(
        self, job_id: int, *, plan_path: Path, output_path: Path,
        max_output_bytes: int | None = DEFAULT_MAX_OUTPUT_BYTES,
        target_output_bytes: int | None = DEFAULT_TARGET_OUTPUT_BYTES,
    ) -> dict:
        if (max_output_bytes is None) != (target_output_bytes is None):
            raise ValueError("Output maximum and target must both be set or both be unlimited")
        stage = self.store.ensure_stage(job_id, "render")
        self.store.update_stage(job_id, "render", state="PENDING", error=None, pid=None)
        self.store.set_setting(f"render:{job_id}", {
            "plan": plan_path.resolve().relative_to(self.root).as_posix(),
            "output": output_path.resolve().relative_to(self.root).as_posix(),
            "max_output_bytes": max_output_bytes,
            "target_output_bytes": target_output_bytes,
        })
        value = self.store.update_job(job_id, state="QUEUED", stop_mode=None, error=None)
        self.store.add_event(job_id, "EXPORT_QUEUED", "Approved final export queued")
        self._wake.set()
        return value

    def _definitions(self, job: dict[str, Any]) -> dict[str, PipelineStage]:
        result = {item.name: item for item in pipeline_stages(
            root=self.root, job_key=self._pipeline_key(job), source=Path(job["source_path"]),
            content_style=job["content_style"], profile=job["profile"],
            source_sha256=job["source_sha256"],
            detector_groups=self.detector_groups(int(job["id"])),
            ocr_recognition_batch_size=self.ocr_batch_size(int(job["id"])),
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

    def _select(self) -> tuple[dict[str, Any], dict[str, Any], PipelineStage] | None:
        if self.store.setting("scheduler_paused", False):
            return None
        for job in self.store.list_jobs():
            if job["state"] != "QUEUED" or job.get("stop_mode"):
                continue
            stage = self.store.next_pending_stage(job["id"])
            if stage is None:
                continue
            definition = self._definitions(job).get(stage["name"])
            if definition:
                return job, stage, definition
        return None

    def _run(self) -> None:
        while not self._stop.is_set():
            selection = self._select()
            if selection is None:
                self._wake.wait(self.poll_seconds)
                self._wake.clear()
                continue
            self._execute(*selection)

    def _execute(self, job: dict[str, Any], stage_row: dict[str, Any], definition: PipelineStage) -> None:
        job_id = int(job["id"])
        name = definition.name
        attempt = int(stage_row["attempt"]) + 1
        log_dir = self.root / "logs" / "control-center"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"job-{job_id}-{name}-attempt-{attempt}.log"
        self.store.update_job(job_id, state=definition.job_state, current_stage=name, error=None)
        self.store.update_stage(job_id, name, state="RUNNING", attempt=attempt,
                                progress=0.0, started_at=now_iso(), completed_at=None,
                                heartbeat_at=now_iso(), error=None)
        self.store.add_event(job_id, "STAGE_STARTED", f"Stage {name} started",
                             payload={"attempt": attempt, "log": log_path.relative_to(self.root).as_posix()})
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
                    log_handle = log_path.open("ab")
                    kwargs: dict[str, Any] = {"cwd": self.root, "stdout": log_handle,
                                              "stderr": subprocess.STDOUT}
                    if os.name == "nt":
                        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
                    process = subprocess.Popen(command.argv, **kwargs)
                    with self._lock:
                        self._process = process
                        self._active = (job_id, name)
                        self._log_handle = log_handle
                    self.store.update_stage(job_id, name, pid=process.pid, heartbeat_at=now_iso())
                    while process.poll() is None:
                        if self._stop.wait(1.0):
                            break
                        self.store.update_stage(job_id, name, heartbeat_at=now_iso())
                    if process.poll() is None:
                        self._terminate_process(process)
                    code = process.wait()
                    log_handle.close()
                    with self._lock:
                        self._process = None
                        self._active = None
                        self._log_handle = None
                    if code != 0:
                        raise RuntimeError(f"Stage process exited with code {code}; see {log_path}")
                    for artifact in command.expected_artifacts:
                        self._register_artifact(job_id, name, artifact)
                cached = self._stage_cache.store(
                    stage_name=name,
                    source_sha256=str(job["source_sha256"]),
                    source_path=Path(job["source_path"]),
                    report_root=report_root,
                    commands=(command.argv for command in definition.commands),
                    artifact_paths=artifacts,
                    expected_key=cache_key,
                )
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
                process = self._process
            if process and process.poll() is None:
                self._terminate_process(process)
            if self._log_handle and not self._log_handle.closed:
                self._log_handle.close()
            with self._lock:
                self._process = None
                self._active = None
                self._log_handle = None
            self._cleanup_interrupted_stage(job_id, name)
            current = self.store.get_job(job_id)
            if current["state"] not in {"PAUSED", "CANCELLED"}:
                self.store.update_stage(job_id, name, state="FAILED_RETRYABLE", pid=None,
                                        completed_at=now_iso(), error=str(error))
                self.store.update_job(job_id, state="FAILED", error=str(error), current_stage=name)
                self.store.add_event(job_id, "STAGE_FAILED", str(error), level="ERROR",
                                     payload={"stage": name})

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
        if job.get("stop_mode") == "AFTER_STAGE" or self._stop.is_set():
            self.store.update_job(job_id, state="PAUSED", current_stage=None,
                                  stop_mode="PAUSED", progress=progress)
        else:
            self.store.update_job(job_id, state="QUEUED", current_stage=None, progress=progress)

    def _terminate_process(self, process: subprocess.Popen) -> None:
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

    def _terminate_active(self, reason: str) -> None:
        with self._lock:
            process = self._process
            active = self._active
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
        for path in sorted((self.root / "input").iterdir()):
            if not path.is_file() or path.suffix.casefold() not in VIDEO_EXTENSIONS:
                continue
            stat = path.stat()
            observed = self.store.observe_file(path, stat.st_size, stat.st_mtime_ns)
            if observed.get("imported_job_id"):
                continue
            stable_since = datetime.fromisoformat(observed["stable_since"])
            elapsed = (datetime.now(stable_since.tzinfo) - stable_since).total_seconds()
            if elapsed < self.stable_seconds:
                continue
            digest = sha256_file(path)
            job = self.store.find_by_sha(digest)
            if job is None:
                duration = duration_seconds(probe_video(ffprobe, path))
                job = self.store.upsert_job(
                    job_key=safe_job_key(path, digest), source_path=path,
                    source_sha256=digest, source_size_bytes=stat.st_size,
                    source_mtime_ns=stat.st_mtime_ns, duration_seconds=duration,
                    state="NEEDS_METADATA",
                )
                self.store.add_event(job["id"], "INPUT_DISCOVERED",
                                     "Stable input discovered; choose content style")
                imported += 1
            self.store.mark_file_imported(path, int(job["id"]))
        return imported

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception as error:
                self.store.add_event(None, "WATCHER_ERROR", str(error), level="ERROR")
            self._stop.wait(self.poll_seconds)
