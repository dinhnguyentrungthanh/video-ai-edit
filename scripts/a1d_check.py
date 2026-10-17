"""Perf plan A1d check on a test Control Center (docs/handoff/A1d-lane-budget.md).

An export queued ahead of a film that waits at preflight: the export runs in the export lane beside the
film's light first GPU stage (text), budgeted at that stage's own measured peak, and the film's heavier
stage after it (visual_logo) waits for the export when the VRAM left cannot hold both. nvidia-smi and NVML
sample the GPU's memory all along; nothing runs out of memory and the export passes the post-export check
of "Xóa video gốc".

Never the real Control Center or the install folder: a throw-away root <install>/temp/a1d-check-<stamp>,
made as scripts/a1b_vram_measure.py makes one, its own port, stream-copied clips (the source video is only
read). No route of the real Control Center is called: before and during every GPU step its database is
read with mode=ro, and the step refuses to run unless its queue is empty and paused (the user pauses it).
This process runs at below-normal priority, so the test Control Center, its stages and every FFmpeg under
them do too; the run records the priority of each FFmpeg it sees.

  setup    --source <video>: the root; clips E1 (the export) and S1 (the film), stream-copied from the
           source at --export-at and --scan-at; with --film, F1: the whole source remuxed with another
           metadata tag (the same frames, another SHA-256)
  profile  "Đo lại" of the test Control Center (the export lane needs a profile that confirms NVENC)
  ready    scan E1 and decide its review (the detectors' suggestions accepted, everything else KEEP)
  run      with the test queue paused, export E1 (auto) and start S1's scan, then resume: the first waiting
           stage is the export and the film waits at preflight. Both lanes every 0.5 s, nvidia-smi every
           0.2 s, NVML every 0.1 s, the stage rows, the lane events and the export's manifest
  measure  scan F1 alone (the grounding cache moved aside first): each stage process's VRAM peak above
           the VRAM in use in the second before it started, as A1b measured it on 10-minute clips, now
           on a whole film, with the stages' own peak CUDA memory from their reports
  report   --evidence <dir>: the checks; check.json and vram-1s.csv, without names, paths or detector text
  remove   take the root down
Scans use the detector groups of scripts/a1c_check.py (advertising: preflight, text, visual_logo,
localize_logo, build_review), live action, profile careful, "Quét nhanh" on.

Usage (from the worktree, with the install's .venv python):
  python scripts/a1d_check.py setup --install E:\\DungChung\\BiliFlow --code . --source <video>
  python scripts/a1d_check.py profile|ready|run|remove --install E:\\DungChung\\BiliFlow --root <root>
  python scripts/a1d_check.py report --install E:\\DungChung\\BiliFlow --root <root> --evidence docs/evidence/a1d/<dir>
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))
from a1b_gates import (  # noqa: E402
    Center, LaneWatch, _Idle, decide, describe_clip, export_facts, ffmpeg_tools, job_record, move_grounding_cache,
    overlap_seconds, remux, stage_overlaps, wait_settled,
)
from a1b_vram_measure import Sampler, check_place, cut_clip, job_for, job_state, log, make_root, remove_root, wait_for  # noqa: E402
from a1c_check import (  # noqa: E402
    BELOW_NORMAL, DETECTORS, STYLE, PriorityWatch, RealQueueWatch, below_normal, require_real_idle, start_scan,
    timeline,
)

from biliflow import export_lanes  # noqa: E402
from biliflow.scheduler import STARTABLE_STATES  # noqa: E402

PREFIX = "a1d-check-"
STATE_FILE = "a1d-check.json"
DEFAULT_PORT = 8794
LIGHT_STAGE = "text"
HEAVY_STAGE = "visual_logo"
# Words a CUDA or PyTorch out-of-memory failure writes into a stage's log.
OUT_OF_MEMORY_WORDS = ("out of memory", "outofmemoryerror", "cuda_error_out_of_memory", "cudaerrormemoryallocation")
SMI_INTERVAL_MS = 200
# What a1c_check.start_scan sends with every scan of this check (recorded in check.json).
SCAN_SETTINGS = {"profile": "careful", "fast_scan": True, "ocr_recognition_batch_size": 1}


# --- the root and its record -------------------------------------------------------------------------

def paths(args: argparse.Namespace) -> tuple[Path, Path]:
    install = Path(args.install).resolve(strict=True)
    root = Path(args.root).resolve(strict=True)
    check_place(install, root)
    if not root.name.startswith(PREFIX):
        raise SystemExit(f"{root} is not an A1d check root")
    return install, root


def load(root: Path) -> dict:
    return json.loads((root / STATE_FILE).read_text(encoding="utf-8"))


def save(root: Path, state: dict) -> None:
    (root / STATE_FILE).write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def register(test: Any, root: Path, state: dict) -> None:
    """The job of every clip of the record (the test Control Center imports the clips at its start)."""
    for tag, clip in state["clips"].items():
        if tag not in state["jobs"]:
            path = root / clip["path"]
            state["jobs"][tag] = wait_for(lambda path=path: job_for(test, path), 600, f"the job of {tag}")["id"]
    save(root, state)


class SmiSampler(threading.Thread):
    """nvidia-smi (System32) every SMI_INTERVAL_MS: GPU 0's memory.used and memory.total in MiB."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.samples: list[tuple[float, int, int]] = []
        self.lock, self.ready = threading.Lock(), threading.Event()
        self.process: subprocess.Popen | None = None
        self.error: str | None = None

    def run(self) -> None:
        exe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
        command = [str(exe), "-i", "0", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits",
                   "-lms", str(SMI_INTERVAL_MS)]
        try:
            self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as error:
            self.error = type(error).__name__
            self.ready.set()
            return
        self.ready.set()
        assert self.process.stdout is not None
        for line in self.process.stdout:
            parts = [part.strip() for part in line.split(",")]
            try:
                used, total = int(parts[0]), int(parts[1])
            except (IndexError, ValueError):
                continue
            with self.lock:
                self.samples.append((time.monotonic(), used, total))

    def finish(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.join(10)

    def window(self, start: float, end: float) -> list[int]:
        with self.lock:
            return [used for moment, used, _total in self.samples if start <= moment <= end]

    def total_mib(self) -> int | None:
        with self.lock:
            return self.samples[-1][2] if self.samples else None


class ProcessWatch(threading.Thread):
    """Every 0.25 s the test Control Center's main-lane stage process. For each process: its VRAM peak
    above the least VRAM in use in the second before it was first seen (as the scheduler reads its
    baseline: the process imports Python and torch first, its models load later), from NVML and nvidia-smi."""

    def __init__(self, test: Any, start: float, nvml: Sampler, smi: SmiSampler) -> None:
        super().__init__(daemon=True)
        self.test, self.start_at, self.nvml, self.smi = test, start, nvml, smi
        self.stop, self.current, self.processes = threading.Event(), None, []

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                lanes = self.test.call("GET", "/api/status").get("active_lanes") or []
            except (OSError, ValueError):
                lanes = None
            if lanes is not None:
                main = next((item for item in lanes if item.get("lane") == "main" and item.get("pid")), None)
                self._observe(main, time.monotonic())
            self.stop.wait(0.25)
        self._observe(None, time.monotonic())

    def _observe(self, active: dict | None, now: float) -> None:
        key = (active["job_id"], active["stage"], active["pid"]) if active else None
        current = self.current
        if current and key != current["key"]:
            nvml, smi = self.nvml.window(current["start"], now), self.smi.window(current["start"], now)
            self.processes.append({
                "job_id": current["key"][0], "stage": current["key"][1],
                "start_s": round(current["start"] - self.start_at, 1), "end_s": round(now - self.start_at, 1),
                "baseline_mib": current["baseline"],
                "peak_mib": {"nvml": max(nvml) if nvml else None, "nvidia_smi": max(smi) if smi else None},
                "peak_above_baseline_mib": {
                    name: (max(values) - current["baseline"][name]
                           if values and current["baseline"][name] is not None else None)
                    for name, values in (("nvml", nvml), ("nvidia_smi", smi))},
            })
            self.current = current = None
        if key and current is None:
            nvml, smi = self.nvml.window(now - 1.0, now), self.smi.window(now - 1.0, now)
            self.current = {"key": key, "start": now,
                            "baseline": {"nvml": min(nvml) if nvml else None, "nvidia_smi": min(smi) if smi else None}}

    def finish(self) -> list[dict]:
        self.stop.set()
        self.join(10)
        return self.processes


def stage_peaks(processes: list[dict]) -> dict[str, int]:
    """Each stage's largest process peak above its baseline (NVML, as A1b recorded it)."""
    peaks: dict[str, int] = {}
    for item in processes:
        value = item["peak_above_baseline_mib"]["nvml"]
        if value is not None:
            peaks[item["stage"]] = max(peaks.get(item["stage"], 0), value)
    return peaks


def report_cuda_peaks(root: Path, store: Any, job_id: int) -> dict[str, int | None]:
    """The peak CUDA memory (MiB, PyTorch) the film's logo stages wrote into their reports."""
    job = store.get_job(job_id)
    base = root / "reports" / "jobs" / str(store.setting(f"pipeline_key:{job_id}") or job["job_key"]) / "visual-logo"
    found: dict[str, int | None] = {"visual_logo": None, "grounding": None}
    try:
        scan = json.loads((base / "scan.json").read_text(encoding="utf-8"))
        found["visual_logo"] = int((scan.get("metrics") or {})["peak_cuda_memory_bytes"]) // (1024 * 1024)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        localized = json.loads((base / "scan-localized.json").read_text(encoding="utf-8"))
        inference = (localized.get("grounding_region_summary") or {}).get("inference") or {}
        found["grounding"] = int(inference["peak_cuda_bytes"]) // (1024 * 1024)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return found


def per_second(start: float, end: float, smi: SmiSampler, nvml: Sampler) -> list[dict]:
    """The largest reading of each second, from both samplers."""
    rows = []
    for second in range(int(end - start) + 1):
        low, high = start + second, start + second + 1
        smi_values, nvml_values = smi.window(low, high), nvml.window(low, high)
        rows.append({"at_s": second, "nvidia_smi_used_mib": max(smi_values) if smi_values else None,
                     "nvml_used_mib": max(nvml_values) if nvml_values else None})
    return rows


# --- setup, profile, ready ---------------------------------------------------------------------------

def setup(args: argparse.Namespace) -> int:
    install, code = Path(args.install).resolve(strict=True), Path(args.code).resolve(strict=True)
    source = Path(args.source).resolve(strict=True)
    root = make_root(install, code, prefix=PREFIX)
    log(f"test root {root}")
    try:
        tools = ffmpeg_tools(install)
        clips: dict[str, dict] = {}
        for tag, start in (("E1", args.export_at), ("S1", args.scan_at)):
            clip = root / "input" / f"a1d-{tag}.mp4"
            cut_clip(tools / "ffmpeg.exe", source, clip, start, args.minutes * 60)
            clips[tag] = describe_clip(tools, root, clip, None)
        if args.film:
            clip = root / "input" / "a1d-F1.mp4"
            remux(tools / "ffmpeg.exe", source, clip, "a1d-check-F1")
            clips["F1"] = describe_clip(tools, root, clip, None)
        head = subprocess.run(["git", "-C", str(code), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        save(root, {"created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "code_commit": head.stdout.strip(), "port": args.port, "clips": clips, "jobs": {}, "runs": {},
                    "real_queue_changes": {}})
    except BaseException:
        remove_root(install, root)
        raise
    log(f"setup done: {root}")
    return 0


def profile(args: argparse.Namespace) -> int:
    install, root = paths(args)
    state = load(root)
    require_real_idle(install)
    real = RealQueueWatch(install)
    real.start()
    try:
        with Center(root, state["port"]) as test:
            test.call("POST", "/api/scheduler", {"paused": True})
            try:
                test.call("POST", "/api/hardware-profile/remeasure", {})
                wait_for(lambda: not ((test.call("GET", "/api/hardware-profile").get("task") or {}).get("running")),
                         1200, "Đo lại", step=5.0)
                task = test.call("GET", "/api/hardware-profile").get("task") or {}
            finally:
                test.call("POST", "/api/scheduler", {"paused": False})
    finally:
        state["real_queue_changes"]["profile"] = real.finish()
    from biliflow.export_encoder import profile_nvenc_trial

    trial = profile_nvenc_trial(root, ffmpeg_tools(root) / "ffmpeg.exe")
    state["profile"] = {"exit": task.get("exit"), "outcome": task.get("outcome"), "nvenc_trial": trial}
    save(root, state)
    log(f"profile: {state['profile']}")
    return 0 if trial == "ok" else 1


def ready(args: argparse.Namespace) -> int:
    install, root = paths(args)
    state = load(root)
    require_real_idle(install)
    real = RealQueueWatch(install)
    real.start()
    try:
        with Center(root, state["port"]) as test:
            register(test, root, state)
            job_id = state["jobs"]["E1"]
            if job_state(test, job_id) in STARTABLE_STATES:
                start_scan(test, job_id)
                log(f"scan of E1 ended {wait_settled(test, [job_id], 'the scan of E1')}")
            state["ready"] = decide(test, job_id)
    finally:
        state["real_queue_changes"]["ready"] = real.finish()
        save(root, state)
    log(f"ready: {state['ready']}")
    return 0 if state["ready"]["state"] == "READY_TO_EXPORT" else 1


# --- run ---------------------------------------------------------------------------------------------

def stage_events(test: Any, job_id: int) -> list[dict]:
    """The film's STAGE_WAITS_FOR_EXPORT events (A1d), oldest first."""
    detail = test.call("GET", f"/api/jobs/{job_id}")
    events = []
    for item in reversed(detail["events"]):
        if item.get("event_type") == "STAGE_WAITS_FOR_EXPORT":
            payload = item.get("payload")
            if payload is None and item.get("payload_json"):
                payload = json.loads(item["payload_json"])
            events.append({"event": item["event_type"], "level": item.get("level"), "payload": payload})
    return events


def run_check(args: argparse.Namespace) -> int:
    install, root = paths(args)
    state = load(root)
    if "run" in state["runs"]:
        raise SystemExit("the run is recorded already")
    if (state.get("profile") or {}).get("nvenc_trial") != "ok":
        raise SystemExit("run profile first: the export lane needs a profile that confirms NVENC")
    require_real_idle(install)
    run: dict[str, Any] = {"scan": "S1", "export": "E1", "detectors": DETECTORS, "style": STYLE}
    nvml, smi = Sampler(), SmiSampler()
    nvml.start()
    smi.start()
    nvml.ready.wait(10)
    smi.ready.wait(10)
    if smi.error:
        raise SystemExit(f"nvidia-smi did not start ({smi.error}): nothing was run")
    real = RealQueueWatch(install)
    real.start()
    center = Center(root, state["port"])
    try:
        with center as test:
            assert center.process is not None
            priority = PriorityWatch(center.process.pid)
            priority.start()
            run["parallel_export"] = test.call("POST", "/api/scheduler/parallel-export", {"mode": "auto"})["mode"]
            register(test, root, state)
            scan_job = run["scan_job"] = state["jobs"]["S1"]
            export_job = run["export_job"] = state["jobs"]["E1"]
            time.sleep(3)
            start = time.monotonic()
            before = smi.window(start - 3.0, start)
            run["vram_before_mib"] = {"nvidia_smi": max(before) if before else None,
                                      "nvml": max(nvml.window(start - 3.0, start) or [0]) or None}
            watch = LaneWatch(test, start)
            watch.start()
            try:
                test.call("POST", "/api/scheduler", {"paused": True})
                try:
                    test.call("POST", f"/api/jobs/{export_job}/review/finalize", {"size_mode": "default"})
                    start_scan(test, scan_job)
                finally:
                    test.call("POST", "/api/scheduler", {"paused": False})
                run["states"] = {str(key): value for key, value in
                                 wait_settled(test, [scan_job, export_job], "the run").items()}
                end = time.monotonic()
                run["total_seconds"] = round(end - start, 1)
            finally:
                run["lane_segments"] = watch.finish()
                run["process_priority"] = priority.finish()
            run["vram_peak_mib"] = {"nvidia_smi": max(smi.window(start, end) or [0]),
                                    "nvml": max(nvml.window(start, end) or [0])}
            run["vram_total_mib"] = {"nvidia_smi": smi.total_mib(), "nvml": nvml.total_mib}
            run["vram_per_second"] = per_second(start, end, smi, nvml)
            run["gpu"] = {"name": nvml.gpu_name, "driver": nvml.driver}
            run["export_record"] = job_record(test, export_job)
            run["scan_record"] = job_record(test, scan_job)
            run["scan_wait_events"] = stage_events(test, scan_job)
    finally:
        run["real_queue_changes"] = real.finish()
        smi.finish()
        nvml.stop.set()
        nvml.join(10)
    run["overlap_seconds"] = overlap_seconds(run["lane_segments"], run["export_job"], run["scan_job"])
    run["stage_overlap_seconds"] = stage_overlaps(run["lane_segments"], run["export_job"], run["scan_job"])
    run["export_facts"] = export_facts(root, run["export_record"]["output"])
    state["runs"]["run"] = run
    save(root, state)
    log(f"run: {run.get('total_seconds')} s, states {run.get('states')}, beside {run['stage_overlap_seconds']}, "
        f"peak {run['vram_peak_mib']}, events {run['export_record']['lane_events']} {run['scan_wait_events']}")
    return 0


def measure(args: argparse.Namespace) -> int:
    install, root = paths(args)
    state = load(root)
    if "F1" not in state["clips"]:
        raise SystemExit("no whole film in this root: run setup with --film")
    if "measure" in state:
        raise SystemExit("the measurement is recorded already")
    require_real_idle(install)
    result: dict[str, Any] = {"film": "F1", "detectors": DETECTORS, "style": STYLE,
                              "grounding_cache_moved": move_grounding_cache(root, "measure")}
    nvml, smi = Sampler(), SmiSampler()
    nvml.start()
    smi.start()
    nvml.ready.wait(10)
    smi.ready.wait(10)
    if smi.error:
        raise SystemExit(f"nvidia-smi did not start ({smi.error}): nothing was run")
    real = RealQueueWatch(install)
    real.start()
    center = Center(root, state["port"])
    try:
        with center as test:
            assert center.process is not None
            priority = PriorityWatch(center.process.pid)
            priority.start()
            register(test, root, state)
            film_job = result["film_job"] = state["jobs"]["F1"]
            time.sleep(3)
            start = time.monotonic()
            watch = ProcessWatch(test, start, nvml, smi)
            watch.start()
            try:
                start_scan(test, film_job)
                result["states"] = {str(key): value for key, value in
                                    wait_settled(test, [film_job], "the scan of the whole film").items()}
                result["total_seconds"] = round(time.monotonic() - start, 1)
            finally:
                result["processes"] = watch.finish()
                result["process_priority"] = priority.finish()
            result["film_record"] = job_record(test, film_job)
    finally:
        result["real_queue_changes"] = real.finish()
        smi.finish()
        nvml.stop.set()
        nvml.join(10)
    result["stage_peak_above_baseline_mib"] = stage_peaks(result["processes"])
    state["measure"] = result
    save(root, state)
    log(f"measure: {result.get('total_seconds')} s, states {result.get('states')}, "
        f"peaks {result['stage_peak_above_baseline_mib']}")
    return 0


# --- report ------------------------------------------------------------------------------------------

def out_of_memory_logs(root: Path, job_ids: list[int]) -> list[str]:
    """Names of the jobs' stage logs that mention running out of memory."""
    found = []
    for job_id in job_ids:
        for path in sorted((root / "logs" / "control-center").glob(f"job-{job_id}-*.log")):
            text = path.read_text(encoding="utf-8", errors="replace").lower()
            if any(word in text for word in OUT_OF_MEMORY_WORDS):
                found.append(path.name)
    return found


def a1b_left_mib(payload: dict) -> int | None:
    """What A1b's single peak would have left beside the light stage: total - max(used, baseline + 4943) - need."""
    try:
        return (int(payload["total_mib"]) - max(int(payload["used_mib"]), int(payload["baseline_mib"]) + 4943)
                - int(payload["need_mib"]))
    except (KeyError, TypeError, ValueError):
        return None


def checks(root: Path, store: Any, state: dict) -> dict:
    from biliflow.source_cleanup import assess_job

    run = state["runs"]["run"]
    scan_job, export_job = run["scan_job"], run["export_job"]
    events = run["export_record"]["lane_events"]
    parallel = [item for item in events if item["event"] == "EXPORT_LANE_PARALLEL"]
    turned_down = [item for item in events if item["event"] in ("EXPORT_LANE_SEQUENTIAL", "EXPORT_DEFERRED")]
    payload = (parallel[0]["payload"] or {}) if parallel else {}
    order = timeline(store, scan_job, export_job)
    start_of = {(item["job"], item["stage"]): item for item in order}
    render, light, heavy = (start_of.get(("E1", "render")), start_of.get(("S1", LIGHT_STAGE)),
                            start_of.get(("S1", HEAVY_STAGE)))
    waits = [item for item in run["scan_wait_events"] if (item["payload"] or {}).get("stage") == HEAVY_STAGE]
    export_segments = [item for item in run["lane_segments"] if item["job_id"] == export_job]
    assessment = assess_job(root, store, _Idle(), store.get_job(export_job), fresh=True)
    facts = run.get("export_facts") or {}
    encoding = facts.get("encoding") or {}
    ffmpeg_seen = Counter((run.get("process_priority") or {}).get("ffmpeg") or {})
    real_changes = {**state.get("real_queue_changes", {}), "run": run.get("real_queue_changes")}
    failed = [event["event_type"] for job_id in (scan_job, export_job)
              for event in store.events(job_id, limit=1000) if event["event_type"] == "STAGE_FAILED"]
    memory_logs = out_of_memory_logs(root, [scan_job, export_job])
    total = run["vram_total_mib"]["nvidia_smi"] or run["vram_total_mib"]["nvml"]
    peak = max(value for value in run["vram_peak_mib"].values() if value is not None)
    # The export lane was still running when the light stage ended: the heavy stage after it had to fit
    # beside the export (stage_beside_export) or wait for it.
    export_outlasted_light = bool(render and light and render["end_s"] is not None and light["end_s"] is not None
                                  and render["end_s"] > light["end_s"])
    heavy_overlap = run["stage_overlap_seconds"].get(HEAVY_STAGE, 0.0)
    result = {
        "export_beside_the_light_stage_at_its_own_peak": len(parallel) == 1 and not turned_down
        and payload.get("scan_job_id") == scan_job and payload.get("scan_stage") == LIGHT_STAGE
        and payload.get("scan_peak_basis") == "stage"
        and payload.get("scan_peak_mib") == export_lanes.STAGE_PEAK_MIB[LIGHT_STAGE][0],
        "export_only_in_the_export_lane": bool(export_segments)
        and all(item["lane"] == "export" for item in export_segments),
        "both_lanes_at_once": bool(run.get("overlap_seconds")),
        # Either the heavy stage waited (one event, started after the export ended), or its own peak fit
        # beside the export and no wait was recorded.
        "heavy_stage_waited_or_fit": export_outlasted_light and (
            (len(waits) == 1 and heavy_overlap == 0.0 and bool(heavy and render and heavy["start_s"] >= render["end_s"]))
            or (not waits and heavy_overlap > 0.0)),
        "vram_peak_below_the_card": bool(total) and peak < total,
        "no_out_of_memory": not failed and not memory_logs,
        "scan_and_export_finished": run["states"].get(str(scan_job)) == "WAITING_REVIEW"
        and assessment.eligible,
        "export_manifest_valid": facts.get("status") == "COMPLETED" and encoding.get("encoder") == "h264_nvenc"
        and encoding.get("lane") == "parallel" and not encoding.get("fallback"),
        "every_ffmpeg_below_normal": bool(ffmpeg_seen) and set(ffmpeg_seen) == {BELOW_NORMAL},
        "real_queue_stayed_empty_and_paused": not any(real_changes.values()),
    }
    film = state.get("measure")
    film_details = None
    if film is not None:
        measured = film["stage_peak_above_baseline_mib"]
        recorded = {stage: export_lanes.STAGE_PEAK_MIB[stage][0] for stage in measured
                    if stage in (export_lanes.STAGE_PEAK_MIB or {})}
        film_job = film["film_job"]
        film_failed = [event["event_type"] for event in store.events(film_job, limit=1000)
                       if event["event_type"] == "STAGE_FAILED"]
        # The per-stage numbers hold on a whole film too: no stage went above its recorded peak.
        result["whole_film_within_the_recorded_peaks"] = (
            film["states"].get(str(film_job)) == "WAITING_REVIEW" and not film_failed
            and not out_of_memory_logs(root, [film_job]) and bool(recorded)
            and all(measured[stage] <= peak for stage, peak in recorded.items()))
        result["real_queue_stayed_empty_and_paused"] = (
            result["real_queue_stayed_empty_and_paused"] and not film.get("real_queue_changes"))
        film_details = {
            "stage_peak_above_baseline_mib": measured, "recorded_stage_peak_mib": recorded,
            "over_the_recorded_peak": {stage: measured[stage] - peak for stage, peak in recorded.items()
                                       if measured[stage] > peak},
            "report_peak_cuda_mib": report_cuda_peaks(root, store, film_job), "processes": film["processes"],
            "seconds": film.get("total_seconds"), "stage_failed_events": film_failed,
            "process_priority": film.get("process_priority"), "real_queue_changes": film.get("real_queue_changes"),
            "grounding_cache_moved": film.get("grounding_cache_moved"),
        }
    result["passed"] = all(result.values())
    details = {
        "whole_film": film_details,
        "timeline": order, "lane_events": events, "scan_wait_events": run["scan_wait_events"],
        "lane_segments": run["lane_segments"], "overlap_seconds": run.get("overlap_seconds"),
        "stage_overlap_seconds": run["stage_overlap_seconds"], "export_outlasted_the_light_stage": export_outlasted_light,
        "a1b_rule_left_mib_beside_the_light_stage": a1b_left_mib(payload),
        "margin_mib": export_lanes.SAFETY_MARGIN_MIB,
        "post_export_check": {"eligible_for_delete_source": assessment.eligible, "refusal": assessment.reason,
                              "status": facts.get("status"), "encoder": encoding.get("encoder"),
                              "lane": encoding.get("lane"), "validation_method": encoding.get("validation_method"),
                              "fallback": encoding.get("fallback")},
        "stage_failed_events": failed, "out_of_memory_logs": memory_logs,
        "process_priority": run.get("process_priority"), "real_queue_changes": real_changes,
        "seconds": run.get("total_seconds"), "gpu": run.get("gpu"),
        "vram_mib": {"before": run.get("vram_before_mib"), "peak": run.get("vram_peak_mib"),
                     "total": run.get("vram_total_mib")},
    }
    return {"checks": result, "details": details}


def report(args: argparse.Namespace) -> int:
    _, root = paths(args)
    state = load(root)
    from biliflow.job_store import JobStore

    store = JobStore(root / "state" / "control-center.sqlite3")
    try:
        found = checks(root, store, state)
    finally:
        store.close()
    evidence = {
        "written_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "code_commit": state["code_commit"], "test_root": root.name, "detectors": DETECTORS, "style": STYLE,
        "settings": SCAN_SETTINGS, "clips": {tag: {key: value for key, value in clip.items() if key != "path"}
                  for tag, clip in state["clips"].items()},
        "jobs": state["jobs"], "profile": state.get("profile"), "ready": state.get("ready"), **found,
    }
    target = Path(args.evidence).resolve()
    target.mkdir(parents=True, exist_ok=True)
    (target / "check.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
    with (target / "vram-1s.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["at_s", "nvidia_smi_used_mib", "nvml_used_mib"])
        writer.writeheader()
        writer.writerows(state["runs"]["run"]["vram_per_second"])
    log(f"checks: {json.dumps(found['checks'], ensure_ascii=False)}")
    return 0 if found["checks"]["passed"] else 1


def remove(args: argparse.Namespace) -> int:
    remove_root(Path(args.install).resolve(strict=True), Path(args.root))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    first = commands.add_parser("setup")
    first.add_argument("--install", required=True)
    first.add_argument("--code", required=True, help="the checkout whose src, scripts and config the root runs")
    first.add_argument("--source", required=True, help="the video the clips are cut from (only read)")
    first.add_argument("--minutes", type=float, default=6.0)
    first.add_argument("--export-at", type=float, default=600.0, help="where E1 starts in the video (seconds)")
    first.add_argument("--scan-at", type=float, default=1800.0, help="where S1 starts in the video (seconds)")
    first.add_argument("--port", type=int, default=DEFAULT_PORT)
    first.add_argument("--film", action="store_true", help="also remux the whole source as F1 for measure")
    steps: dict[str, Callable[[argparse.Namespace], int]] = {
        "profile": profile, "ready": ready, "run": run_check, "measure": measure, "report": report,
        "remove": remove,
    }
    for name in steps:
        step = commands.add_parser(name)
        step.add_argument("--install", required=True)
        step.add_argument("--root", required=True)
        if name == "report":
            step.add_argument("--evidence", required=True)
    args = parser.parse_args()
    below_normal()
    if args.command == "setup":
        return setup(args)
    return steps[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
