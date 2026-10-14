"""Perf plan A1b gates 1, 2, 3 and 6 on a test Control Center (docs/handoff/A1b-lanes.md).

Never the real Control Center or the install folder: a throw-away root <install>/temp/a1b-gates-<stamp>,
made as scripts/a1b_vram_measure.py makes one, its own port, stream-copied clips (the user's videos are
only read). The real Control Center must be idle (both share Local\\BiliFlowGpuInference and
Local\\BiliFlowFinalRender); its /api/status is only read. Each step starts the test Control Center and
shuts it down again (only a server whose state file names this child, port and token is driven); the
root's a1b-gates.json carries what the steps found.

  setup    the root; clips X1 (the 1080p video) and Y1 (the small one), and copies X2, Y2, Y3 that differ
           only in a metadata tag (the same packets, so the same frames; another SHA-256, so a job and a scan
           of their own); the baseline code (git archive <baseline> src) for gate 1
  copies   --of X1 --tags X3 X4 ...: more such copies (the test Control Center imports them on its next start)
  prep     scan X1 and X2 (live action, every detector group) and decide their reviews (the detectors'
           suggestions accepted, everything else KEEP), so that each can be exported
  ready    --tags Y1 Y2 Y3 --style mixed: scan the clips not scanned yet, then decide their reviews
  profile  "Đo lại" of the test Control Center: the export lane needs a profile that confirms NVENC. The
           measurement guards the install too, so the real queue must be paused or the real Control Center
           off; the test Control Center's own queue is paused around it
  lanes    --mode off|auto: start the scan of Yn, then (once its first stage process runs) export Xn;
           n = 1 for off, 2 for auto (gate 6, and gate 2's first pair). Times, both lanes every 0.5 s, NVML
           every 0.1 s, the export's events and manifest; a copy of the approved edit plan is kept for gate 1
  alone    --name N --scan TAG --style S: scan one clip with nothing beside it (gate 2's reference)
  cover    --name N --scan TAG --export TAG --beside STAGE --against N --style S: start the scan and, once
           STAGE's process runs, export the other job (auto), so that the export runs beside that stage and
           the ones after it (gate 2 for the GPU stages the first pair did not reach)
           alone and cover first move the root's content-addressed grounding cache aside, so that every scan
           computes its own grounding boxes
  gate1    four x264 renders of copies of X1's approved edit plan, each to its own output:
             sequential_a3  NVENC fails (CUDA_VISIBLE_DEVICES=-1) and A3 encodes again with x264 at once
             a1b_handback   the render the export lane runs (BILIFLOW_EXPORT_LANE=parallel), NVENC failing the
                            same way, writes the deferral record and stops; the render the main lane runs
                            then encodes it with x264. Both are run here as the scheduler would run them; the
                            scheduler's own hand-back is covered by tests/test_scheduler.py (fake processes)
             forced_x264    the plan asks for libx264
             baseline_x264  the same with the baseline code
           passes when the four mp4 SHA-256 and the four framemd5 are identical
  report   gates 2, 3 and 6 from what the steps recorded; writes <evidence>/gates.json
  remove   take the root down
No video name or path is written to the evidence.

Usage (from the worktree, with the install's .venv python):
  python scripts/a1b_gates.py setup --install E:\\DungChung\\BiliFlow --code . --source-1080 <video> --source-small <video>
  python scripts/a1b_gates.py prep|profile|gate1 --install E:\\DungChung\\BiliFlow --root <root>
  python scripts/a1b_gates.py lanes --install E:\\DungChung\\BiliFlow --root <root> --mode off|auto
  python scripts/a1b_gates.py report --install E:\\DungChung\\BiliFlow --root <root> --evidence docs/evidence/a1b/<dir>
  python scripts/a1b_gates.py remove --install E:\\DungChung\\BiliFlow --root <root>
"""
from __future__ import annotations

import argparse
import http.client
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))
from a1b_vram_measure import (  # noqa: E402
    REAL_PORT, SETTLED, Http, Sampler, check_place, cut_clip, healthy, job_for, job_state, log, make_root, probe,
    remove_root, sha256, start_scan, wait_for,
)

from biliflow.final_renderer import (  # noqa: E402
    DEFAULT_MAX_OUTPUT_BYTES, DEFAULT_TARGET_OUTPUT_BYTES, EXPORT_LANE_ENVIRONMENT, PARALLEL_EXPORT_LANE,
    export_deferral_path,
)
from biliflow.hardware_locks import RENDER_MUTEX, NamedMutex  # noqa: E402
from biliflow.scheduler import STARTABLE_STATES  # noqa: E402

PREFIX = "a1b-gates-"
STATE_FILE = "a1b-gates.json"
DEFAULT_PORT = 8792
DEFAULT_BASELINE = "23c9e00"
CLIP_TAGS = ("X1", "X2", "Y1", "Y2", "Y3")
COPIES = {"X2": "X1", "Y2": "Y1", "Y3": "Y1"}
RENDERS = ("sequential_a3", "a1b_handback", "forced_x264", "baseline_x264")
NO_GPU = {"CUDA_VISIBLE_DEVICES": "-1"}
# Variables a render must not inherit from this process (each render sets its own).
RENDER_ENVIRONMENT = (EXPORT_LANE_ENVIRONMENT, "CUDA_VISIBLE_DEVICES")
LANE_EVENTS = ("EXPORT_LANE_PARALLEL", "EXPORT_LANE_SEQUENTIAL", "EXPORT_DEFERRED")
RUN_TIMEOUT_SECONDS = 7200
# The scan stages that use the GPU: VRAM above their baseline in docs/evidence/a1b/vram-2026-10-14
# (preflight and build_review use none). Gate 2 needs each one that ran beside an export at least once.
GPU_STAGES = ("animation_safety", "adult", "verify_adult", "live_safety", "confirm_violence", "text",
              "visual_logo", "localize_logo")
# The grounding DINO cache is keyed on frame content, not on the source: copies of a clip would share it.
GROUNDING_CACHE = ("cache", "ad_candidate_pipeline")
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
# Gate 2: keys whose values are wall-clock facts of a run (created_at, elapsed_seconds, total_wall_seconds,
# clock...), never what the detectors found. Times in the video (timestamp_seconds, *_time_groups) are
# compared like every other result.
WALL_CLOCK_WORDS = frozenset({"elapsed", "wall", "clock"})
# Inside a stage's "metrics" block, its times and memory too (read_seconds, peak_process_ram_bytes,
# peak_cuda_memory_bytes, video_seconds_per_processing_second); its counts (crops, recognition_calls,
# frames_prepared, calls, failed_calls) and decode or precision facts are compared.
METRICS_PERF_WORDS = frozenset({"seconds", "second", "ram", "memory"})
# How a run got its results, never the results: "at" (an audit-log time), "source_report_sha256" (the hash
# of a report that holds its created_at) and "peak_cuda_bytes" (the CUDA memory grounding took).
RUN_KEYS = frozenset({"job_id", "pid", "at", "source_report_sha256", "peak_cuda_bytes"})
# Masked as well for the prep reference pair X1/X2 only: X2 took its grounding boxes from the cache X1
# filled (computed 0, cache_hits 14). Every gate pair compares them.
CACHE_KEYS = frozenset({"cache_hits", "computed"})
REVIEW_ID = re.compile(r"review-[0-9a-f]{12}")


# --- the root and its record ------------------------------------------------------------------------

def paths(args: argparse.Namespace) -> tuple[Path, Path]:
    install = Path(args.install).resolve(strict=True)
    root = Path(args.root).resolve(strict=True)
    check_place(install, root)
    if not root.name.startswith(PREFIX):
        raise SystemExit(f"{root} is not an A1b gates root")
    return install, root


def load(root: Path) -> dict:
    return json.loads((root / STATE_FILE).read_text(encoding="utf-8"))


def save(root: Path, state: dict) -> None:
    (root / STATE_FILE).write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def ffmpeg_tools(root: Path) -> Path:
    return root / "tools" / "ffmpeg" / "bin"


def real_status() -> dict | None:
    """The real Control Center's /api/status (GET only), or None when it does not run."""
    try:
        return Http(REAL_PORT).call("GET", "/api/status", timeout=5)
    except OSError:
        return None


def real_busy(status: dict | None) -> bool:
    """A stage runs, or jobs wait in a queue that is not paused (a paused queue starts nothing)."""
    if not status:
        return False
    if status.get("active") or status.get("active_lanes"):
        return True
    queue = status.get("queue") or {}
    return bool(queue.get("length")) and not queue.get("paused")


def require_real_idle() -> dict | None:
    status = real_status()
    if real_busy(status):
        raise SystemExit(f"the real Control Center is busy: {status.get('active')}, queue {status.get('queue')}")
    return status


class RealWatch(threading.Thread):
    """Every 2 s: whether the real Control Center started work while this step ran (GET only)."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.stop, self.busy, self.start_at = threading.Event(), [], time.monotonic()

    def run(self) -> None:
        while not self.stop.is_set():
            at = round(time.monotonic() - self.start_at, 1)
            try:
                status = real_status()
            except (ValueError, http.client.HTTPException) as error:
                # Unreadable is not idle: recorded, and the watch goes on.
                self.busy.append({"at_s": at, "watch_error": type(error).__name__})
            else:
                if real_busy(status):
                    self.busy.append({"at_s": at, "active": status.get("active"), "queue": status.get("queue")})
            self.stop.wait(2.0)

    def finish(self) -> list[dict]:
        self.stop.set()
        self.join(10)
        return self.busy


class Center:
    """The test Control Center on its own port, started for one step and shut down after it.

    Only a server whose state/control-center.json names this child's PID, the port and the token it
    hands out is driven: never another Control Center that happens to answer on the port.
    """

    def __init__(self, root: Path, port: int) -> None:
        if port == REAL_PORT:
            raise SystemExit("the real Control Center's port is never used")
        self.root, self.port = root, port
        self.process: subprocess.Popen | None = None
        self.http = Http(port)

    def __enter__(self) -> Http:
        if healthy(self.http):
            raise SystemExit(f"port {self.port} already answers: stop that server first")
        environment = {**os.environ, "PYTHONPATH": str(self.root / "src")}
        for name in RENDER_ENVIRONMENT:
            environment.pop(name, None)
        self.log = (self.root / "logs" / "test-control-center.out.log").open("ab")
        self.process = subprocess.Popen(
            [str(self.root / ".venv" / "Scripts" / "python.exe"), "-m", "biliflow.control_entry",
             "--project-root", str(self.root), "--host", "127.0.0.1", "--port", str(self.port),
             "--stable-seconds", "5", "--no-import-existing"],
            cwd=self.root, env=environment, stdout=self.log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        log(f"test Control Center pid {self.process.pid} on port {self.port}")
        try:
            wait_for(self._up, 120, "the test Control Center")
            token = self.http.call("GET", "/api/session")["token"]
            record = json.loads((self.root / "state" / "control-center.json").read_text(encoding="utf-8"))
            if (record.get("port"), record.get("token")) != (self.port, token) or not self._ours(record.get("pid")):
                raise RuntimeError(f"the server on port {self.port} is not this root's test Control Center")
            self.http.token = token
        except BaseException:
            self._stop()
            raise
        return self.http

    def _ours(self, pid: Any) -> bool:
        """The PID the server recorded is this child, or the interpreter the venv's python.exe launcher started."""
        import psutil

        assert self.process is not None
        if pid == self.process.pid:
            return True
        try:
            return type(pid) is int and psutil.Process(pid).ppid() == self.process.pid
        except psutil.Error:
            return False

    def _up(self) -> bool:
        assert self.process is not None
        if self.process.poll() is not None:
            raise RuntimeError(f"the test Control Center exited with {self.process.returncode}")
        return healthy(self.http)

    def __exit__(self, *exc: object) -> None:
        self._stop()

    def _stop(self) -> None:
        assert self.process is not None
        try:
            if self.http.token:  # set only once the server proved to be this child
                self.http.call("POST", "/api/shutdown", {"mode": "immediate"})
            self.process.wait(90)
        except Exception:  # noqa: BLE001 - our own child: stop its tree
            subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True)
        self.log.close()
        log("test Control Center stopped")


def register_jobs(test: Http, root: Path, state: dict) -> None:
    """The job of every clip of the record (the Control Center imports new clips at its start)."""
    for tag, clip in state["clips"].items():
        if tag not in state["jobs"]:
            path = root / clip["path"]
            state["jobs"][tag] = wait_for(lambda path=path: job_for(test, path), 600, f"the job of {tag}")["id"]
    save(root, state)


# --- setup ------------------------------------------------------------------------------------------

def remux(ffmpeg: Path, source: Path, target: Path, tag: str) -> None:
    """The same packets with another metadata tag: identical frames, another SHA-256."""
    subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-i", str(source), "-map", "0", "-c", "copy",
                    "-metadata", f"comment={tag}", "-movflags", "+faststart", str(target)], check=True, timeout=600)


def extract_baseline(code: Path, commit: str, target: Path) -> str:
    archive = subprocess.run(["git", "-C", str(code), "archive", "--format=tar", commit, "src"],
                             capture_output=True, check=True, timeout=120).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(target, filter="data")
    full = subprocess.run(["git", "-C", str(code), "rev-parse", commit], capture_output=True, text=True, check=True)
    return full.stdout.strip()


def describe_clip(tools: Path, root: Path, clip: Path, origin: str | None) -> dict:
    record = {"path": clip.relative_to(root).as_posix(), **probe(tools / "ffprobe.exe", clip),
              "sha256": sha256(clip), "bytes": clip.stat().st_size}
    if origin:
        record["copy_of"] = origin
    log(f"clip {clip.stem}: {record['width']}x{record['height']}, {record['duration_seconds']:.1f} s")
    return record


def setup(args: argparse.Namespace) -> int:
    install, code = Path(args.install).resolve(strict=True), Path(args.code).resolve(strict=True)
    root = make_root(install, code, prefix=PREFIX)
    log(f"test root {root}")
    try:
        tools = ffmpeg_tools(install)
        clips: dict[str, dict] = {}
        for tag, source in (("X1", Path(args.source_1080)), ("Y1", Path(args.source_small))):
            clip = root / "input" / f"a1b-{tag}.mp4"
            cut_clip(tools / "ffmpeg.exe", source, clip, args.start_at, args.minutes * 60)
            clips[tag] = describe_clip(tools, root, clip, None)
        for tag, origin in COPIES.items():
            clip = root / "input" / f"a1b-{tag}.mp4"
            remux(tools / "ffmpeg.exe", root / clips[origin]["path"], clip, f"a1b-gates-{tag}")
            clips[tag] = describe_clip(tools, root, clip, origin)
        baseline = extract_baseline(code, args.baseline, root / "baseline-code")
        head = subprocess.run(["git", "-C", str(code), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        save(root, {"created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "code_commit": head.stdout.strip(), "baseline_commit": baseline, "port": args.port,
                    "clips": clips, "jobs": {}, "runs": {}})
    except BaseException:
        remove_root(install, root)
        raise
    log(f"setup done: {root}")
    return 0


def copies(args: argparse.Namespace) -> int:
    _, root = paths(args)
    state = load(root)
    if healthy(Http(state["port"])):
        raise SystemExit("stop the test Control Center first")
    tools = ffmpeg_tools(root)
    for tag in args.tags:
        if tag in state["clips"]:
            raise SystemExit(f"{tag} exists already")
        clip = root / "input" / f"a1b-{tag}.mp4"
        remux(tools / "ffmpeg.exe", root / state["clips"][args.of]["path"], clip, f"a1b-gates-{tag}")
        state["clips"][tag] = describe_clip(tools, root, clip, args.of)
        save(root, state)
    return 0


# --- prep and ready: exportable jobs ------------------------------------------------------------------

def decide(test: Http, job_id: int) -> dict:
    """The detectors' suggestions accepted, everything else KEEP (a test root's own review)."""
    test.call("POST", f"/api/jobs/{job_id}/review/bulk-accept", {"filter": "all"})
    test.call("POST", f"/api/jobs/{job_id}/review/bulk-keep", {"filter": "all"})
    queue = test.call("GET", f"/api/jobs/{job_id}/review/queue")
    for item in queue.get("items", []):
        if item.get("decision") not in ("KEEP", "BLUR", "CUT"):
            test.call("POST", f"/api/jobs/{job_id}/review/decision", {"id": item["id"], "decision": "KEEP"})
    queue = test.call("GET", f"/api/jobs/{job_id}/review/queue")
    return {"queue_status": queue.get("status"),
            "decisions": dict(Counter(str(item.get("decision")) for item in queue.get("items", []))),
            "state": job_state(test, job_id)}


def wait_settled(test: Http, job_ids: list[int], what: str) -> dict[int, str]:
    def settled() -> dict[int, str] | None:
        states = {job_id: job_state(test, job_id) for job_id in job_ids}
        return states if all(state in SETTLED for state in states.values()) else None
    return wait_for(settled, RUN_TIMEOUT_SECONDS, what, step=2.0)


def prep(args: argparse.Namespace) -> int:
    _, root = paths(args)
    state = load(root)
    require_real_idle()
    real = RealWatch()
    real.start()
    with Center(root, state["port"]) as test:
        register_jobs(test, root, state)
        log(f"jobs {state['jobs']}")
        exports = [state["jobs"]["X1"], state["jobs"]["X2"]]
        for job_id in exports:
            start_scan(test, job_id, "live_action")
        states = wait_settled(test, exports, "the scans of X1 and X2")
        log(f"scans ended {states}")
        state["prep"] = {tag: decide(test, state["jobs"][tag]) for tag in ("X1", "X2")}
    state["prep"]["real_control_center_busy"] = real.finish()
    save(root, state)
    log(f"prep: {state['prep']}")
    ready_now = all(state["prep"][tag]["state"] == "READY_TO_EXPORT" for tag in ("X1", "X2"))
    return 0 if ready_now else 1


def ready(args: argparse.Namespace) -> int:
    _, root = paths(args)
    state = load(root)
    require_real_idle()
    real = RealWatch()
    real.start()
    with Center(root, state["port"]) as test:
        register_jobs(test, root, state)
        ids = [state["jobs"][tag] for tag in args.tags]
        scanned = [job_id for job_id in ids if job_state(test, job_id) in STARTABLE_STATES]
        for job_id in scanned:
            start_scan(test, job_id, args.style)
        if scanned:
            log(f"scans ended {wait_settled(test, scanned, 'the scans')}")
        result = {tag: {**decide(test, state["jobs"][tag]), "scanned_now": state["jobs"][tag] in scanned,
                        "style": args.style if state["jobs"][tag] in scanned else None} for tag in args.tags}
    state.setdefault("ready", {}).update(result)
    state["ready"]["real_control_center_busy"] = real.finish()
    save(root, state)
    log(f"ready: {result}")
    return 0 if all(item["state"] == "READY_TO_EXPORT" for item in result.values()) else 1


# --- profile ----------------------------------------------------------------------------------------

def profile(args: argparse.Namespace) -> int:
    _, root = paths(args)
    state = load(root)
    status = real_status()
    if status is not None and not status.get("scheduler_paused"):
        raise SystemExit("the real queue is not paused (or the real Control Center turned off)")
    if real_busy(status):
        raise SystemExit("the real Control Center is still running a stage")
    with Center(root, state["port"]) as test:
        test.call("POST", "/api/scheduler", {"paused": True})
        try:
            test.call("POST", "/api/hardware-profile/remeasure", {})
            wait_for(lambda: not ((test.call("GET", "/api/hardware-profile").get("task") or {}).get("running")),
                     1200, "Đo lại", step=5.0)
            task = test.call("GET", "/api/hardware-profile").get("task") or {}
        finally:
            test.call("POST", "/api/scheduler", {"paused": False})
    from biliflow.export_encoder import profile_nvenc_trial

    trial = profile_nvenc_trial(root, ffmpeg_tools(root) / "ffmpeg.exe")
    state["profile"] = {"exit": task.get("exit"), "outcome": task.get("outcome"), "nvenc_trial": trial}
    save(root, state)
    log(f"profile: {state['profile']}")
    return 0 if trial == "ok" else 1


# --- runs: a scan, alone or with an export beside it --------------------------------------------------

class LaneWatch(threading.Thread):
    """Every 0.5 s the test Control Center's active_lanes: one segment per (lane, job, stage, pid)."""

    def __init__(self, test: Http, start: float) -> None:
        super().__init__(daemon=True)
        self.test, self.start_at = test, start
        self.stop, self.lock = threading.Event(), threading.Lock()
        self.open: dict[tuple, dict] = {}
        self.segments: list[dict] = []

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                lanes = self.test.call("GET", "/api/status").get("active_lanes") or []
            except (OSError, ValueError, http.client.HTTPException):
                lanes = None
            if lanes is not None:
                self._observe(lanes)
            self.stop.wait(0.5)
        self._observe([])

    def _observe(self, lanes: list[dict]) -> None:
        now = round(time.monotonic() - self.start_at, 1)
        seen = {(item.get("lane"), item.get("job_id"), item.get("stage"), item.get("pid")) for item in lanes}
        with self.lock:
            for key in [key for key in self.open if key not in seen]:
                segment = self.open.pop(key)
                segment["end_s"] = now
                self.segments.append(segment)
            for key in seen - set(self.open):
                self.open[key] = {"lane": key[0], "job_id": key[1], "stage": key[2], "pid": key[3], "start_s": now}

    def process_of(self, job_id: int) -> dict | None:
        with self.lock:
            return next((dict(item) for item in self.open.values() if item["job_id"] == job_id and item["pid"]), None)

    def finish(self) -> list[dict]:
        self.stop.set()
        self.join(10)
        return sorted(self.segments, key=lambda item: (item["start_s"], str(item["lane"])))


def _overlap(first: list[tuple[float, float]], second: list[tuple[float, float]]) -> float:
    return round(sum(max(0.0, min(a_end, b_end) - max(a_start, b_start))
                     for a_start, a_end in first for b_start, b_end in second), 1)


def overlap_seconds(segments: list[dict], export_job: int, scan_job: int) -> float:
    """How long the export of ``export_job`` ran while a stage process of ``scan_job`` ran."""
    exports = [(item["start_s"], item["end_s"]) for item in segments if item["job_id"] == export_job]
    scans = [(item["start_s"], item["end_s"]) for item in segments if item["job_id"] == scan_job and item["pid"]]
    return _overlap(exports, scans)


def stage_overlaps(segments: list[dict], export_job: int | None, scan_job: int) -> dict[str, float]:
    """For each stage process of ``scan_job``: the seconds the export ran beside it (0 for none)."""
    exports = [(item["start_s"], item["end_s"]) for item in segments if item["job_id"] == export_job]
    result: dict[str, float] = {}
    for item in segments:
        if item["job_id"] == scan_job and item["pid"]:
            seconds = _overlap(exports, [(item["start_s"], item["end_s"])])
            result[item["stage"]] = round(result.get(item["stage"], 0.0) + seconds, 1)
    return result


def iso_seconds(start: Any, end: Any) -> float | None:
    try:
        return round((datetime.fromisoformat(str(end)) - datetime.fromisoformat(str(start))).total_seconds(), 1)
    except ValueError:
        return None


def job_record(test: Http, job_id: int) -> dict:
    detail = test.call("GET", f"/api/jobs/{job_id}")
    stages = [{"name": item["name"], "state": item["state"], "attempt": item.get("attempt"),
               "seconds": iso_seconds(item.get("started_at"), item.get("completed_at"))}
              for item in detail["stages"] if item.get("started_at")]
    events = []
    for item in reversed(detail["events"]):
        if item.get("event_type") in LANE_EVENTS:
            payload = item.get("payload")
            if payload is None and item.get("payload_json"):
                payload = json.loads(item["payload_json"])
            events.append({"event": item["event_type"], "level": item.get("level"), "payload": payload})
    outputs = [item["path"] for item in detail["artifacts"] if item.get("kind") == "final_output"]
    return {"state": detail["job"]["state"], "stages": stages, "lane_events": events,
            "output": outputs[-1] if outputs else None}


def export_facts(root: Path, output: str | None) -> dict | None:
    if not output:
        return None
    path = root / output
    manifest = json.loads(path.with_name(path.name + ".manifest.json").read_text(encoding="utf-8"))
    return {"status": manifest.get("status"), "encoding": manifest.get("encoding"),
            "bytes": path.stat().st_size, "sha256": sha256(path)}


def move_grounding_cache(root: Path, name: str) -> bool:
    """Move the root's grounding cache aside (inside the root), so that the next scan computes its own boxes."""
    source = root.joinpath(*GROUNDING_CACHE)
    if not source.exists():
        return False
    if os.lstat(source).st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        raise SystemExit(f"{source} is a link: not moved")
    target = source.with_name(f"{source.name}-before-{name}")
    if target.exists():
        raise SystemExit(f"{target} exists already")
    source.rename(target)
    return True


def scan_run(root: Path, state: dict, *, name: str, mode: str, scan_tag: str, style: str,
             export_tag: str | None = None, beside: str | None = None, keep_plan: bool = False,
             fresh_grounding: bool = False) -> dict:
    """Scan ``scan_tag``; with ``export_tag``, export it once the scan's ``beside`` stage process runs
    (its first stage process when None). Recorded as state["runs"][name]."""
    if name in state["runs"]:
        raise SystemExit(f"the run {name} is recorded already")
    scan_job = state["jobs"][scan_tag]
    export_job = state["jobs"][export_tag] if export_tag else None
    require_real_idle()
    run: dict[str, Any] = {"mode": mode, "scan": scan_tag, "scan_job": scan_job, "style": style,
                           "export": export_tag, "export_job": export_job, "beside_requested": beside,
                           "grounding_cache_moved": move_grounding_cache(root, name) if fresh_grounding else False}
    sampler = Sampler()
    sampler.start()
    sampler.ready.wait(10)
    real = RealWatch()
    real.start()
    try:
        with Center(root, state["port"]) as test:
            run["parallel_export"] = test.call("POST", "/api/scheduler/parallel-export", {"mode": mode})["mode"]
            time.sleep(3)
            start = time.monotonic()
            before = sampler.window(start - 3.0, start)
            run["vram_before_mib"] = max(before) if before else None
            watch = LaneWatch(test, start)
            watch.start()
            try:
                start_scan(test, scan_job, style)
                if export_job is not None:
                    def at_stage() -> dict | None:
                        process = watch.process_of(scan_job)
                        if process and (beside is None or process["stage"] == beside):
                            return process
                        if job_state(test, scan_job) in SETTLED:
                            raise RuntimeError(f"the scan ended before its {beside} stage ran")
                        return None

                    first = wait_for(at_stage, RUN_TIMEOUT_SECONDS, f"the scan's {beside or 'first'} stage", step=0.25)
                    run["export_requested_s"] = round(time.monotonic() - start, 1)
                    run["export_requested_beside"] = first["stage"]
                    answer = test.call("POST", f"/api/jobs/{export_job}/review/finalize", {"size_mode": "default"})
                    if keep_plan:
                        plan_copy = root / "work" / f"a1b-gate1-{name}-approved-plan.json"
                        shutil.copyfile(root / answer["plan"], plan_copy)
                        run["plan_copy"] = plan_copy.relative_to(root).as_posix()
                jobs = [scan_job] + ([export_job] if export_job is not None else [])
                run["states"] = {str(key): value for key, value in wait_settled(test, jobs, f"the run {name}").items()}
                run["total_seconds"] = round(time.monotonic() - start, 1)
            finally:
                run["lane_segments"] = watch.finish()
            run["vram_peak_mib"] = max(sampler.window(start, time.monotonic()) or [0])
            run["export_record"] = job_record(test, export_job) if export_job is not None else None
            run["scan_record"] = job_record(test, scan_job)
    finally:
        run["real_control_center_busy"] = real.finish()
        sampler.stop.set()
        sampler.join(10)
    if export_job is not None:
        run["overlap_seconds"] = overlap_seconds(run["lane_segments"], export_job, scan_job)
        run["export_facts"] = export_facts(root, run["export_record"]["output"])
    run["stage_overlap_seconds"] = stage_overlaps(run["lane_segments"], export_job, scan_job)
    state["runs"][name] = run
    save(root, state)
    events = (run.get("export_record") or {}).get("lane_events")
    log(f"{name}: total {run.get('total_seconds')} s, states {run.get('states')}, "
        f"beside {run['stage_overlap_seconds']}, events {events}")
    return run


def lanes(args: argparse.Namespace) -> int:
    _, root = paths(args)
    state = load(root)
    number = "1" if args.mode == "off" else "2"
    scan_run(root, state, name=args.mode, mode=args.mode, scan_tag=f"Y{number}", style="mixed",
             export_tag=f"X{number}", keep_plan=True)
    return 0


def alone(args: argparse.Namespace) -> int:
    _, root = paths(args)
    state = load(root)
    scan_run(root, state, name=args.name, mode="auto", scan_tag=args.scan, style=args.style, fresh_grounding=True)
    return 0


def cover(args: argparse.Namespace) -> int:
    _, root = paths(args)
    state = load(root)
    if args.against not in state["runs"] or state["runs"][args.against].get("export_job") is not None:
        raise SystemExit(f"{args.against} is not a recorded scan alone")
    run = scan_run(root, state, name=args.name, mode="auto", scan_tag=args.scan, style=args.style,
                   export_tag=args.export, beside=args.beside, fresh_grounding=True)
    run["against"] = args.against
    save(root, state)
    return 0


# --- gate 1: x264 of a handed-back export against sequential x264 ------------------------------------

def render_environment(extra: dict[str, str]) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if key not in RENDER_ENVIRONMENT}
    environment.update(extra)
    return environment


def size_arguments() -> list[str]:
    # The default size policy, as the Control Center's export of these plans used it.
    return ["--max-output-bytes", str(DEFAULT_MAX_OUTPUT_BYTES), "--target-output-bytes", str(DEFAULT_TARGET_OUTPUT_BYTES)]


def run_render(root: Path, plan: Path, output: Path, extra: dict[str, str]) -> dict:
    """render-final through the root's run.ps1 (Local\\BiliFlowFinalRender), as the scheduler runs it."""
    command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root / "scripts" / "run.ps1"),
               "render-final", "--plan", str(plan), "--output", str(output), *size_arguments()]
    started = time.monotonic()
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=7200, env=render_environment(extra))
    return {"returncode": result.returncode, "seconds": round(time.monotonic() - started, 1),
            "error_tail": (result.stderr or result.stdout)[-300:] if result.returncode else None}


def run_baseline_render(root: Path, plan: Path, output: Path) -> dict:
    """render-final with the baseline code (root/baseline-code/src), holding the render mutex as run.ps1 does."""
    environment = render_environment({"PYTHONPATH": str(root / "baseline-code" / "src"),
                                      "TEMP": str(root / "temp"), "TMP": str(root / "temp")})
    # As run.ps1 runs every command: the package's __main__.
    command = [str(root / ".venv" / "Scripts" / "python.exe"), "-m", "biliflow", "render-final",
               "--plan", str(plan), "--output", str(output), *size_arguments()]
    mutex = NamedMutex(RENDER_MUTEX)
    if not mutex.try_acquire(600):
        raise RuntimeError("Local\\BiliFlowFinalRender stayed busy")
    try:
        started = time.monotonic()
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=7200, env=environment)
    finally:
        mutex.release()
    return {"returncode": result.returncode, "seconds": round(time.monotonic() - started, 1),
            "error_tail": (result.stderr or result.stdout)[-300:] if result.returncode else None}


def framemd5(root: Path, video: Path, target: Path) -> str:
    subprocess.run([str(ffmpeg_tools(root) / "ffmpeg.exe"), "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
                    "-map", "0:v:0", "-map", "0:a:0?", "-f", "framemd5", str(target)], check=True, timeout=3600)
    return sha256(target)


def plan_for(root: Path, approved: Path, name: str) -> Path:
    plan = root / "work" / f"a1b-gate1-{name}-plan.json"
    if name in ("forced_x264", "baseline_x264"):
        document = json.loads(approved.read_text(encoding="utf-8"))
        document["render_settings"] = {**(document.get("render_settings") or {}), "video_encoder": "libx264"}
        plan.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        shutil.copyfile(approved, plan)  # byte for byte: the approved plan itself
    return plan


def read_deferral(root: Path, output: Path) -> dict | None:
    path = export_deferral_path(root, output)
    if not path.is_file():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    return {"reason": record.get("reason"), "fallback_reason": (record.get("fallback") or {}).get("reason"),
            "encoder_choice": record.get("encoder_choice")}


def clear_render(root: Path, output: Path) -> None:
    """A gate 1 render's own files from an earlier attempt: its output, partial output and manifest, its
    deferral record (temp/render-progress, named after the output) and its framemd5, all in this root."""
    for path in (output, output.with_name(output.stem + ".partial" + output.suffix),
                 output.with_name(output.name + ".manifest.json"), export_deferral_path(root, output),
                 root / "work" / (output.stem + ".framemd5")):
        if path.resolve().is_relative_to(root):
            path.unlink(missing_ok=True)


def one_render(root: Path, name: str, approved: Path) -> dict:
    plan = plan_for(root, approved, name)
    output = root / "output" / f"a1b-gate1-{name}.mp4"
    clear_render(root, output)
    item: dict[str, Any] = {}
    if name == "a1b_handback":
        lane = {EXPORT_LANE_ENVIRONMENT: PARALLEL_EXPORT_LANE, **NO_GPU}
        item["export_lane"] = run_render(root, plan, output, lane)
        item["deferral"] = read_deferral(root, output)
        item["export_lane"]["wrote_output"] = output.exists()
        item["main_lane"] = run_render(root, plan, output, {})
        item["deferral_left"] = export_deferral_path(root, output).exists()
        final = item["main_lane"]
    elif name == "baseline_x264":
        final = item["render"] = run_baseline_render(root, plan, output)
    else:
        final = item["render"] = run_render(root, plan, output, NO_GPU if name == "sequential_a3" else {})
    if final["returncode"] == 0:
        manifest = json.loads(output.with_name(output.name + ".manifest.json").read_text(encoding="utf-8"))
        item["encoding"] = manifest.get("encoding")
        item["mp4_sha256"] = sha256(output)
        item["bytes"] = output.stat().st_size
        item["framemd5_sha256"] = framemd5(root, output, root / "work" / f"a1b-gate1-{name}.framemd5")
    log(f"gate 1 {name}: {json.dumps(item, ensure_ascii=False)[:600]}")
    return item


def gate1_checks(items: dict[str, dict]) -> dict[str, bool]:
    def encoding(name: str) -> dict:
        return items[name].get("encoding") or {}

    def fallback(name: str) -> dict:
        return encoding(name).get("fallback") or {}

    handback = items["a1b_handback"]
    return {
        "every_render_finished": all(item.get("mp4_sha256") for item in items.values()),
        "mp4_identical": len({item.get("mp4_sha256") for item in items.values()}) == 1,
        "framemd5_identical": len({item.get("framemd5_sha256") for item in items.values()}) == 1,
        "every_render_x264": all(encoding(name).get("encoder") == "libx264" for name in items),
        "sequential_fell_back_at_once": fallback("sequential_a3").get("reason") == "encoder_error"
        and not fallback("sequential_a3").get("deferred"),
        "export_lane_stopped_without_output": handback["export_lane"]["returncode"] != 0
        and not handback["export_lane"]["wrote_output"]
        and (handback.get("deferral") or {}).get("fallback_reason") == "encoder_error",
        "main_lane_encoded_the_deferral": fallback("a1b_handback").get("deferred") is True
        and encoding("a1b_handback").get("lane") == "main" and not handback["deferral_left"],
        # The baseline (23c9e00) records no "lane": a manifest without it is the old renderer's.
        "baseline_is_the_old_code": bool(encoding("baseline_x264")) and "lane" not in encoding("baseline_x264"),
    }


def gate1(args: argparse.Namespace) -> int:
    _, root = paths(args)
    state = load(root)
    approved = root / state["runs"]["off"]["plan_copy"]
    require_real_idle()
    real = RealWatch()
    real.start()
    items = {name: one_render(root, name, approved) for name in RENDERS}
    checks = gate1_checks(items)
    state["gate1"] = {"renders": items, "checks": checks, "passed": all(checks.values()),
                      "real_control_center_busy": real.finish()}
    save(root, state)
    log(f"gate 1: {checks}")
    return 0 if state["gate1"]["passed"] else 1


# --- report: gates 2, 3 and 6 -------------------------------------------------------------------------

def key_words(key: str) -> set[str]:
    return set(re.split(r"[^a-z0-9]+", key.lower()))


def run_fact(key: str, extra: frozenset[str] = frozenset()) -> bool:
    return key.lower().endswith("_at") or bool(key_words(key) & WALL_CLOCK_WORDS) or key in RUN_KEYS or key in extra


class Masker:
    """One job's own names as placeholders that are the same for every job.

    What differs between two copies of a clip by design (the file name, SHA-256 and size, and every name
    made from them: the job key, report paths, the routing cache keyed on the SHA-256) never reads as a
    difference of what the detectors found.
    """

    def __init__(self, job: dict, extra_keys: frozenset[str] = frozenset()) -> None:
        self.extra_keys = extra_keys  # masked like RUN_KEYS (CACHE_KEYS for the prep reference pair)
        source = Path(str(job["source_path"]))
        sha = str(job["source_sha256"])
        names = {
            str(job["job_key"]): "<job-key>", sha: "<source-sha256>", sha[:8]: "<source-sha256-8>",
            str(source): "<source-path>", str(source).replace("\\", "/"): "<source-path>",
            source.name: "<source-name>", source.stem: "<source-stem>", source.stem.lower(): "<source-stem>",
        }
        self.tokens = sorted(((token, value) for token, value in names.items() if token),
                             key=lambda pair: len(pair[0]), reverse=True)
        self.size = job.get("source_size_bytes")

    def text(self, value: str) -> str:
        for token, placeholder in self.tokens:
            value = value.replace(token, placeholder)
        return value


def renumber_review_ids(text: str) -> str:
    """Review item ids hash their evidence paths: number them by first appearance instead."""
    order: dict[str, str] = {}
    return REVIEW_ID.sub(lambda match: order.setdefault(match.group(0), f"<review-{len(order)}>"), text)


def normalize(value: Any, masker: Masker, ignored: set[str], in_metrics: bool = False) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            name = masker.text(key)
            perf = in_metrics and not isinstance(item, (dict, list)) and bool(key_words(key) & METRICS_PERF_WORDS)
            if run_fact(key, masker.extra_keys) or perf:
                ignored.add(f"metrics:{key}" if in_metrics else key)
                result[name] = "<run>"
            elif key == "routing_cache" and isinstance(item, dict):
                # Keyed on the source SHA-256; its contents are compared on their own (compare_routing).
                ignored.add("routing_cache.key/path/bytes")
                result[name] = {inner: "<cache>" if inner in ("key", "path", "bytes") else normalize(v, masker, ignored)
                                for inner, v in item.items()}
            else:
                result[name] = normalize(item, masker, ignored, in_metrics or key == "metrics")
        return result
    if isinstance(value, list):
        return [normalize(item, masker, ignored, in_metrics) for item in value]
    if isinstance(value, str):
        return masker.text(value)
    if type(value) is int and value == masker.size:
        return "<source-bytes>"
    return value


def normalized_document(value: Any, masker: Masker, ignored: set[str]) -> Any:
    return json.loads(renumber_review_ids(json.dumps(normalize(value, masker, ignored), ensure_ascii=False)))


def differences(left: Any, right: Any, limit: int = 20) -> list[str]:
    found: list[str] = []

    def walk(a: Any, b: Any, at: str) -> None:
        if len(found) >= limit:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                if key not in a or key not in b:
                    found.append(f"{at}/{key}: only on one side")
                else:
                    walk(a[key], b[key], f"{at}/{key}")
        elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            for index, (x, y) in enumerate(zip(a, b)):
                walk(x, y, f"{at}[{index}]")
        elif a != b:
            found.append(f"{at}: {json.dumps(a, ensure_ascii=False)[:120]} != {json.dumps(b, ensure_ascii=False)[:120]}")

    walk(left, right, "")
    return found


def load_json(path: Path, masker: Masker, ignored: set[str]) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        return [normalized_document(json.loads(line), masker, ignored) for line in text.splitlines() if line.strip()]
    return normalized_document(json.loads(text), masker, ignored)


def text_differences(left: str, right: str, limit: int = 10) -> list[str]:
    import difflib

    lines = [line for line in difflib.unified_diff(left.splitlines(), right.splitlines(), lineterm="", n=0)
             if line[:1] in "+-" and not line.startswith(("+++", "---"))]
    return [line[:200] for line in lines[:limit]]


def compare_file(left: Path, right: Path, maskers: tuple[Masker, Masker], ignored: set[str]) -> list[str]:
    suffix = left.suffix.lower()
    if suffix in (".json", ".jsonl"):
        return differences(load_json(left, maskers[0], ignored), load_json(right, maskers[1], ignored))
    if suffix in (".html", ".txt", ".csv", ".md"):
        texts = [renumber_review_ids(masker.text(path.read_text(encoding="utf-8")))
                 for path, masker in ((left, maskers[0]), (right, maskers[1]))]
        return [] if texts[0] == texts[1] else text_differences(*texts)
    return [] if sha256(left) == sha256(right) else ["bytes differ"]


def routing_caches(root: Path, job: dict) -> list[Path]:
    return sorted((root / "cache" / "visual-logo" / str(job["source_sha256"])).glob("routing-*.json.gz"))


def compare_routing(root: Path, jobs: dict[str, dict], maskers: tuple[Masker, Masker]) -> dict:
    """The visual-logo routing caches (one per source SHA-256): windows, boundaries and counts.

    ok only when both have exactly one and they match, or neither has one.
    """
    import gzip

    found = {side: routing_caches(root, job) for side, job in jobs.items()}
    counts = {side: len(paths) for side, paths in found.items()}
    if counts == {"left": 0, "right": 0}:
        return {"compared": False, "files": counts, "ok": True}
    if counts != {"left": 1, "right": 1}:
        return {"compared": False, "files": counts, "ok": False}
    documents = []
    for path, masker in ((found["left"][0], maskers[0]), (found["right"][0], maskers[1])):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            document = json.load(handle)
        document["cache_key"] = "<cache>"
        documents.append(normalized_document(document, masker, set()))
    listed = differences(*documents)
    return {"compared": True, "files": counts, "differences": listed, "ok": not listed}


def grounding_counters(root: Path, job: dict) -> dict | None:
    """computed and cache_hits of the job's grounding boxes (visual-logo/scan-localized.json), or None."""
    path = root / "reports" / "jobs" / str(job["job_key"]) / "visual-logo" / "scan-localized.json"
    if not path.is_file():
        return None
    inference = (json.loads(path.read_text(encoding="utf-8")).get("grounding_region_summary") or {}).get("inference")
    if not isinstance(inference, dict):
        return None
    return {"computed": inference.get("computed"), "cache_hits": inference.get("cache_hits")}


def compare_scans(root: Path, store: Any, left_id: int, right_id: int,
                  extra_keys: frozenset[str] = frozenset()) -> dict:
    """Every file of the two jobs' report folders and their routing caches.

    JSON: wall-clock keys, the times and memory of "metrics" blocks and RUN_KEYS read as "<run>" (and
    ``extra_keys``), the job's own names as placeholders, review ids numbered by first appearance; HTML and
    text the same way; run logs are not compared; anything else byte for byte. Without ``extra_keys`` both
    scans must also have computed their own grounding boxes (no cache hit).
    """
    jobs = {"left": store.get_job(left_id), "right": store.get_job(right_id)}
    maskers = (Masker(jobs["left"], extra_keys), Masker(jobs["right"], extra_keys))
    files: dict[str, dict[str, Path]] = {}
    for (side, job), masker in zip(jobs.items(), maskers):
        folder = root / "reports" / "jobs" / str(job["job_key"])
        files[side] = {masker.text(path.relative_to(folder).as_posix()): path
                       for path in folder.rglob("*") if path.is_file()}
    ignored: set[str] = set()
    result: dict[str, Any] = {"files": 0, "identical": 0, "only_left": [], "only_right": [], "different": [],
                              "not_compared_run_logs": []}
    for name in sorted(set(files["left"]) | set(files["right"])):
        if name not in files["right"]:
            result["only_left"].append(name)
            continue
        if name not in files["left"]:
            result["only_right"].append(name)
            continue
        if name.lower().endswith(".log"):
            result["not_compared_run_logs"].append(name)
            continue
        result["files"] += 1
        found = compare_file(files["left"][name], files["right"][name], maskers, ignored)
        if found:
            result["different"].append({"file": name, "differences": found})
        else:
            result["identical"] += 1
    result["routing_cache"] = compare_routing(root, jobs, maskers)
    grounding = {side: grounding_counters(root, job) for side, job in jobs.items()}
    result["grounding"] = {**grounding, "own_boxes": all(
        not (counters or {}).get("cache_hits") for counters in grounding.values())}
    result["ignored_keys"] = sorted(ignored)
    result["passed"] = (not (result["only_left"] or result["only_right"] or result["different"])
                        and result["routing_cache"]["ok"]
                        and (bool(extra_keys) or result["grounding"]["own_boxes"]))
    return result


def beside_comparison(root: Path, store: Any, alone_job: int, alone_tag: str, run: dict) -> dict:
    """A scan beside an export against a scan of the same frames alone, with what ran beside what."""
    coverage = stage_overlaps(run["lane_segments"], run["export_job"], run["scan_job"])
    return {"alone": alone_tag, "beside": run["scan"], "export": run["export"],
            "export_requested_beside": run.get("export_requested_beside"),
            "stage_overlap_seconds": coverage,
            "stages_beside_export": sorted(stage for stage, seconds in coverage.items() if seconds > 0),
            **compare_scans(root, store, alone_job, run["scan_job"])}


def gate2(root: Path, store: Any, state: dict) -> dict:
    runs = state["runs"]
    pairs = {"auto": beside_comparison(root, store, runs["off"]["scan_job"], runs["off"]["scan"], runs["auto"])}
    for name, run in runs.items():
        if run.get("against"):
            reference = runs[run["against"]]
            pairs[name] = beside_comparison(root, store, reference["scan_job"], reference["scan"], run)
    covered = {stage for pair in pairs.values() for stage in pair["stages_beside_export"]}
    ran = {stage for name in pairs for stage in stage_overlaps(runs[name]["lane_segments"], None, runs[name]["scan_job"])}
    missing = sorted(stage for stage in GPU_STAGES if stage in ran and stage not in covered)
    references = {"x1_vs_x2_prep": {"note": "both alone, before the hardware profile; X2's grounding came from "
                                            "X1's cache, so computed/cache_hits are masked here",
                                    **compare_scans(root, store, state["jobs"]["X1"], state["jobs"]["X2"], CACHE_KEYS)}}
    if (state.get("ready") or {}).get("Y3", {}).get("scanned_now"):
        references["y1_vs_y3"] = {"note": "both alone, after the hardware profile",
                                  **compare_scans(root, store, state["jobs"]["Y1"], state["jobs"]["Y3"])}
    return {"pairs": pairs, "gpu_stages_beside_export": sorted(covered), "gpu_stages_never_beside_export": missing,
            "references": references,
            "passed": all(pair["passed"] for pair in pairs.values()) and not missing}


class _Idle:
    """assess_job asks the scheduler only whether the job runs; the test Control Center is off."""

    def is_busy(self, job_id: int) -> bool:
        return False


def gate3(root: Path, store: Any, state: dict) -> dict:
    from biliflow.source_cleanup import assess_job

    exports: dict[str, Any] = {}
    for name, run in state["runs"].items():
        if run.get("export_job") is None:
            continue
        assessment = assess_job(root, store, _Idle(), store.get_job(run["export_job"]), fresh=True)
        encoding = (run.get("export_facts") or {}).get("encoding") or {}
        exports[name] = {"export": run["export"], "mode": run["mode"],
                         "eligible_for_delete_source": assessment.eligible, "refusal": assessment.reason,
                         "encoder": encoding.get("encoder"), "lane": encoding.get("lane"),
                         "validation_method": encoding.get("validation_method"), "fallback": encoding.get("fallback")}
    parallel = [item for item in exports.values() if item["lane"] == "parallel"]
    return {"exports": exports, "parallel_exports": len(parallel),
            "passed": bool(parallel) and all(item["eligible_for_delete_source"] and item["encoder"] == "h264_nvenc"
                                             for item in parallel)}


def gate6(state: dict) -> dict:
    result: dict[str, Any] = {}
    for mode in ("off", "auto"):
        run = state["runs"].get(mode)
        if not run:
            continue
        render = next((item for item in run["export_record"]["stages"] if item["name"] == "render"), {})
        result[mode] = {
            "total_seconds": run.get("total_seconds"), "overlap_seconds": run["overlap_seconds"],
            "export_render_seconds": render.get("seconds"),
            "scan_stage_seconds": round(sum(item["seconds"] or 0 for item in run["scan_record"]["stages"]), 1),
            "lane_events": run["export_record"]["lane_events"], "vram_before_mib": run.get("vram_before_mib"),
            "vram_peak_mib": run.get("vram_peak_mib"),
            "real_control_center_busy": bool(run.get("real_control_center_busy")),
        }
    if "off" in result and "auto" in result:
        saved = result["off"]["total_seconds"] - result["auto"]["total_seconds"]
        result["saved_seconds"] = round(saved, 1)
        result["saved_share"] = round(saved / result["off"]["total_seconds"], 3)
    return result


def readable(value: Any) -> Any:
    """error_tail of a render recorded before its output was read as UTF-8 (read as cp1252): undone."""
    if isinstance(value, dict):
        return {key: readable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [readable(item) for item in value]
    if isinstance(value, str):
        try:
            return value.encode("cp1252").decode("utf-8")
        except UnicodeError:
            return value
    return value


def report(args: argparse.Namespace) -> int:
    _, root = paths(args)
    state = load(root)
    from biliflow.job_store import JobStore

    store = JobStore(root / "state" / "control-center.sqlite3")
    evidence = {
        "written_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "code_commit": state["code_commit"], "baseline_commit": state["baseline_commit"], "test_root": root.name,
        "clips": {tag: {key: value for key, value in clip.items() if key != "path"}
                  for tag, clip in state["clips"].items()},
        "jobs": state["jobs"], "prep": state.get("prep"), "ready": state.get("ready"), "profile": state.get("profile"),
        "gate1": readable(state.get("gate1")), "gate2": gate2(root, store, state), "gate3": gate3(root, store, state),
        "gate6": gate6(state),
        "runs": {name: {key: value for key, value in run.items() if key != "plan_copy"}
                 for name, run in state["runs"].items()},
    }
    target = Path(args.evidence).resolve()
    target.mkdir(parents=True, exist_ok=True)
    (target / "gates.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"gate 1 passed: {(evidence['gate1'] or {}).get('passed')}; gate 2 passed: {evidence['gate2']['passed']} "
        f"(beside: {evidence['gate2']['gpu_stages_beside_export']}, never: "
        f"{evidence['gate2']['gpu_stages_never_beside_export']}); gate 3 passed: {evidence['gate3']['passed']}; "
        f"gate 6: {evidence['gate6'].get('saved_seconds')} s saved")
    return 0


def remove(args: argparse.Namespace) -> int:
    remove_root(Path(args.install).resolve(strict=True), Path(args.root))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    first = commands.add_parser("setup")
    first.add_argument("--install", required=True)
    first.add_argument("--code", required=True, help="the checkout whose src, scripts and config the root runs")
    first.add_argument("--source-1080", required=True)
    first.add_argument("--source-small", required=True)
    first.add_argument("--minutes", type=float, default=10.0)
    first.add_argument("--start-at", type=float, default=600.0, help="where each clip starts in its video (seconds)")
    first.add_argument("--baseline", default=DEFAULT_BASELINE)
    first.add_argument("--port", type=int, default=DEFAULT_PORT)
    steps: dict[str, Callable[[argparse.Namespace], int]] = {
        "copies": copies, "prep": prep, "ready": ready, "profile": profile, "lanes": lanes, "alone": alone,
        "cover": cover, "gate1": gate1, "report": report, "remove": remove,
    }
    for name in steps:
        step = commands.add_parser(name)
        step.add_argument("--install", required=True)
        step.add_argument("--root", required=True)
        if name == "copies":
            step.add_argument("--of", required=True)
            step.add_argument("--tags", nargs="+", required=True)
        if name == "ready":
            step.add_argument("--tags", nargs="+", required=True)
            step.add_argument("--style", required=True)
        if name == "lanes":
            step.add_argument("--mode", choices=("off", "auto"), required=True)
        if name in ("alone", "cover"):
            step.add_argument("--name", required=True)
            step.add_argument("--scan", required=True)
            step.add_argument("--style", required=True)
        if name == "cover":
            step.add_argument("--export", required=True)
            step.add_argument("--beside", required=True)
            step.add_argument("--against", required=True)
        if name == "report":
            step.add_argument("--evidence", required=True)
    args = parser.parse_args()
    if args.command == "setup":
        return setup(args)
    return steps[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
