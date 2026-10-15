"""Perf plan A1c check on a test Control Center (docs/handoff/A1c-preflight-lane.md).

An export queued ahead of a film that waits at preflight: the main lane runs the film's preflight and
its first GPU stage, the export runs beside that stage in the export lane (NVENC), the film's scan
equals the scan of a copy scanned alone, and the export passes the post-export check of "Xóa video gốc".

Never the real Control Center or the install folder: a throw-away root <install>/temp/a1c-check-<stamp>,
made as scripts/a1b_vram_measure.py makes one, its own port, stream-copied clips (the source video is only
read). No route of the real Control Center is called: before and during every GPU step its database is
read with mode=ro, and the step refuses to run unless its queue is empty and paused (the user pauses it;
the test Control Center shares Local\\BiliFlowGpuInference and Local\\BiliFlowFinalRender with it, and
"Đo lại" needs the real queue paused). This process runs at below-normal priority, so the test Control
Center, its stages and every FFmpeg under them do too (Windows children inherit it); the run records
the priority of each FFmpeg it sees.

  setup    --source <video>: the root; clips E1 (the export) and S1 (the film), stream-copied from the
           source at --export-at and --scan-at, and S2: S1 with another metadata tag (the same frames,
           another SHA-256, so a job and a scan of its own)
  profile  "Đo lại" of the test Control Center (the export lane needs a profile that confirms NVENC)
  ready    scan E1 and decide its review (the detectors' suggestions accepted, everything else KEEP)
  run      the check: with the test queue paused, export E1 (auto) and start S1's scan, then resume, so
           that the first waiting stage is the export and the film waits at preflight. Both lanes every
           0.5 s, NVML every 0.1 s, the stage rows, the lane events and the export's manifest
  alone    scan S2 with nothing beside it (the reference)
  report   --evidence <dir>: the checks; check.json without names, paths or detector text
  remove   take the root down
Scans use the detector groups in DETECTORS (advertising: preflight, then text on the GPU).

Usage (from the worktree, with the install's .venv python):
  python scripts/a1c_check.py setup --install E:\\DungChung\\BiliFlow --code . --source <video>
  python scripts/a1c_check.py profile|ready|run|alone|remove --install E:\\DungChung\\BiliFlow --root <root>
  python scripts/a1c_check.py report --install E:\\DungChung\\BiliFlow --root <root> --evidence docs/evidence/a1c/<dir>
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import threading
import time
from collections import Counter
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))
from a1b_gates import (  # noqa: E402
    GPU_STAGES, Center, LaneWatch, _Idle, compare_scans, decide, describe_clip, export_facts, ffmpeg_tools,
    job_record, move_grounding_cache, overlap_seconds, remux, stage_overlaps, wait_settled,
)
from a1b_vram_measure import (  # noqa: E402
    Sampler, check_place, cut_clip, job_for, job_state, log, make_root, remove_root, wait_for,
)

from biliflow.job_store import IN_PROCESS_STATES  # noqa: E402
from biliflow.scheduler import STARTABLE_STATES  # noqa: E402

PREFIX = "a1c-check-"
STATE_FILE = "a1c-check.json"
DEFAULT_PORT = 8793
DETECTORS = ["advertising"]
STYLE = "live_action"
BELOW_NORMAL = "below_normal"


# --- the root, its record and the real queue -------------------------------------------------------

def paths(args: argparse.Namespace) -> tuple[Path, Path]:
    install = Path(args.install).resolve(strict=True)
    root = Path(args.root).resolve(strict=True)
    check_place(install, root)
    if not root.name.startswith(PREFIX):
        raise SystemExit(f"{root} is not an A1c check root")
    return install, root


def load(root: Path) -> dict:
    return json.loads((root / STATE_FILE).read_text(encoding="utf-8"))


def save(root: Path, state: dict) -> None:
    (root / STATE_FILE).write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def real_queue(install: Path) -> dict:
    """The real Control Center's queue from its database, opened read-only (never a route)."""
    database = install / "state" / "control-center.sqlite3"
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=10)) as connection:
        states = dict(connection.execute("SELECT state, COUNT(*) FROM jobs GROUP BY state").fetchall())
        paused = connection.execute("SELECT value_json FROM settings WHERE key='scheduler_paused'").fetchone()
    return {"queued": int(states.get("QUEUED", 0)),
            "running": sum(int(count) for state, count in states.items() if state in IN_PROCESS_STATES),
            "paused": bool(paused and json.loads(paused[0]) is True)}


def real_idle(queue: dict) -> bool:
    return queue["paused"] and not queue["queued"] and not queue["running"]


def require_real_idle(install: Path) -> None:
    queue = real_queue(install)
    if not real_idle(queue):
        raise SystemExit(f"the real queue must be empty and paused ({queue}): nothing was run")


class RealQueueWatch(threading.Thread):
    """Every 2 s, read-only: whether the real queue got work or was resumed while a step ran."""

    def __init__(self, install: Path) -> None:
        super().__init__(daemon=True)
        self.install, self.stop, self.start_at, self.changes = install, threading.Event(), time.monotonic(), []

    def run(self) -> None:
        while not self.stop.is_set():
            at = round(time.monotonic() - self.start_at, 1)
            try:
                queue = real_queue(self.install)
            except sqlite3.Error as error:
                # Unreadable is not idle: recorded, and the watch goes on.
                self.changes.append({"at_s": at, "read_error": type(error).__name__})
            else:
                if not real_idle(queue):
                    self.changes.append({"at_s": at, **queue})
            self.stop.wait(2.0)

    def finish(self) -> list[dict]:
        self.stop.set()
        self.join(10)
        return self.changes


class PriorityWatch(threading.Thread):
    """Every 2 s: the priority class of each process below the test Control Center, FFmpeg apart."""

    def __init__(self, pid: int) -> None:
        super().__init__(daemon=True)
        self.pid, self.stop = pid, threading.Event()
        self.seen: dict[str, Counter] = {"ffmpeg": Counter(), "other": Counter()}

    def run(self) -> None:
        import psutil

        names = {psutil.BELOW_NORMAL_PRIORITY_CLASS: BELOW_NORMAL, psutil.NORMAL_PRIORITY_CLASS: "normal",
                 psutil.IDLE_PRIORITY_CLASS: "idle", psutil.ABOVE_NORMAL_PRIORITY_CLASS: "above_normal",
                 psutil.HIGH_PRIORITY_CLASS: "high"}
        counted: set[tuple[int, str]] = set()
        while not self.stop.is_set():
            try:
                children = psutil.Process(self.pid).children(recursive=True)
            except psutil.Error:
                children = []
            for child in children:
                try:
                    kind = "ffmpeg" if child.name().lower() == "ffmpeg.exe" else "other"
                    priority = names.get(child.nice(), str(child.nice()))
                except psutil.Error:
                    continue
                if (child.pid, priority) not in counted:  # one count per process and class
                    counted.add((child.pid, priority))
                    self.seen[kind][priority] += 1
            self.stop.wait(2.0)

    def finish(self) -> dict[str, dict[str, int]]:
        self.stop.set()
        self.join(10)
        return {kind: dict(counter) for kind, counter in self.seen.items()}


def below_normal() -> None:
    """This process and, by Windows inheritance, the test Control Center and every process it starts."""
    import psutil

    psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)


def register(test: Any, root: Path, state: dict) -> None:
    """The job of every clip of the record (the test Control Center imports the clips at its start)."""
    for tag, clip in state["clips"].items():
        if tag not in state["jobs"]:
            path = root / clip["path"]
            state["jobs"][tag] = wait_for(lambda path=path: job_for(test, path), 600, f"the job of {tag}")["id"]
    save(root, state)


def start_scan(test: Any, job_id: int) -> None:
    test.call("POST", f"/api/jobs/{job_id}/start", {
        "content_style": STYLE, "profile": "careful", "detectors": DETECTORS,
        "ocr_recognition_batch_size": 1, "fast_scan": True,
    })


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
            clip = root / "input" / f"a1c-{tag}.mp4"
            cut_clip(tools / "ffmpeg.exe", source, clip, start, args.minutes * 60)
            clips[tag] = describe_clip(tools, root, clip, None)
        clip = root / "input" / "a1c-S2.mp4"
        remux(tools / "ffmpeg.exe", root / clips["S1"]["path"], clip, "a1c-check-S2")
        clips["S2"] = describe_clip(tools, root, clip, "S1")
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


# --- run and alone -------------------------------------------------------------------------------------

def scan(args: argparse.Namespace, name: str, scan_tag: str, export_tag: str | None) -> int:
    """Scan ``scan_tag``; with ``export_tag``, its export queued ahead of the film while the test queue is
    paused, so that both wait when the lanes look again. Recorded as state["runs"][name]."""
    install, root = paths(args)
    state = load(root)
    if name in state["runs"]:
        raise SystemExit(f"the run {name} is recorded already")
    if export_tag and (state.get("profile") or {}).get("nvenc_trial") != "ok":
        raise SystemExit("run profile first: the export lane needs a profile that confirms NVENC")
    require_real_idle(install)
    run: dict[str, Any] = {"scan": scan_tag, "export": export_tag, "detectors": DETECTORS, "style": STYLE,
                           "grounding_cache_moved": move_grounding_cache(root, name)}
    sampler = Sampler()
    sampler.start()
    sampler.ready.wait(10)
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
            scan_job = run["scan_job"] = state["jobs"][scan_tag]
            export_job = run["export_job"] = state["jobs"][export_tag] if export_tag else None
            time.sleep(3)
            start = time.monotonic()
            before = sampler.window(start - 3.0, start)
            run["vram_before_mib"] = max(before) if before else None
            watch = LaneWatch(test, start)
            watch.start()
            try:
                test.call("POST", "/api/scheduler", {"paused": True})
                try:
                    if export_job is not None:
                        test.call("POST", f"/api/jobs/{export_job}/review/finalize", {"size_mode": "default"})
                    start_scan(test, scan_job)
                finally:
                    test.call("POST", "/api/scheduler", {"paused": False})
                jobs = [scan_job] + ([export_job] if export_job is not None else [])
                run["states"] = {str(key): value for key, value in wait_settled(test, jobs, f"the run {name}").items()}
                run["total_seconds"] = round(time.monotonic() - start, 1)
            finally:
                run["lane_segments"] = watch.finish()
                run["process_priority"] = priority.finish()
            run["vram_peak_mib"] = max(sampler.window(start, time.monotonic()) or [0])
            run["export_record"] = job_record(test, export_job) if export_job is not None else None
            run["scan_record"] = job_record(test, scan_job)
    finally:
        run["real_queue_changes"] = real.finish()
        sampler.stop.set()
        sampler.join(10)
    if run.get("export_job") is not None:
        run["overlap_seconds"] = overlap_seconds(run["lane_segments"], run["export_job"], run["scan_job"])
        run["export_facts"] = export_facts(root, run["export_record"]["output"])
    run["stage_overlap_seconds"] = stage_overlaps(run["lane_segments"], run.get("export_job"), run["scan_job"])
    state["runs"][name] = run
    save(root, state)
    log(f"{name}: {run.get('total_seconds')} s, states {run.get('states')}, beside {run['stage_overlap_seconds']}, "
        f"events {(run.get('export_record') or {}).get('lane_events')}")
    return 0


def run_check(args: argparse.Namespace) -> int:
    return scan(args, "run", "S1", "E1")


def alone(args: argparse.Namespace) -> int:
    return scan(args, "alone", "S2", None)


# --- report ------------------------------------------------------------------------------------------

def at(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def timeline(store: Any, scan_job: int, export_job: int) -> list[dict]:
    """The film's stages and the export's render, in start order, in seconds from the first start
    (the export job's own scan ran in the ready step and is left out)."""
    rows = [{"job": tag, "stage": row["name"], "start": at(row["started_at"]), "end": at(row["completed_at"])}
            for tag, job_id in (("S1", scan_job), ("E1", export_job)) for row in store.stages(job_id)
            if at(row["started_at"]) and (tag == "S1" or row["name"] == "render")]
    if not rows:
        return []
    origin = min(row["start"] for row in rows)
    return [{"job": row["job"], "stage": row["stage"],
             "start_s": round((row["start"] - origin).total_seconds(), 1),
             "end_s": round((row["end"] - origin).total_seconds(), 1) if row["end"] else None}
            for row in sorted(rows, key=lambda item: item["start"])]


def checks(root: Path, store: Any, state: dict) -> dict:
    from biliflow.source_cleanup import assess_job

    run, reference = state["runs"]["run"], state["runs"]["alone"]
    scan_job, export_job = run["scan_job"], run["export_job"]
    first_gpu = next((row["name"] for row in store.stages(scan_job) if row["name"] in GPU_STAGES), None)
    events = run["export_record"]["lane_events"]
    parallel = [item for item in events if item["event"] == "EXPORT_LANE_PARALLEL"]
    turned_down = [item for item in events if item["event"] in ("EXPORT_LANE_SEQUENTIAL", "EXPORT_DEFERRED")]
    order = timeline(store, scan_job, export_job)
    start_of = {(item["job"], item["stage"]): item for item in order}
    render, preflight = start_of.get(("E1", "render")), start_of.get(("S1", "preflight"))
    gpu = start_of.get(("S1", first_gpu)) if first_gpu else None
    export_segments = [item for item in run["lane_segments"] if item["job_id"] == export_job]
    comparison = compare_scans(root, store, reference["scan_job"], scan_job)
    assessment = assess_job(root, store, _Idle(), store.get_job(export_job), fresh=True)
    encoding = (run.get("export_facts") or {}).get("encoding") or {}
    ffmpeg_seen: Counter = Counter()
    for name in ("run", "alone"):
        ffmpeg_seen.update((state["runs"][name].get("process_priority") or {}).get("ffmpeg") or {})
    real_changes = {**state.get("real_queue_changes", {}),
                    **{name: state["runs"][name].get("real_queue_changes") for name in ("run", "alone")}}
    payload = (parallel[0]["payload"] or {}) if parallel else {}
    result = {
        "export_beside_first_gpu_stage": len(parallel) == 1 and not turned_down
        and payload.get("scan_job_id") == scan_job and payload.get("scan_stage") == first_gpu,
        "preflight_ran_before_the_export": bool(render and preflight and preflight["end_s"] is not None
                                                 and preflight["end_s"] <= render["start_s"]),
        "gpu_stage_started_before_the_export": bool(render and gpu and gpu["start_s"] <= render["start_s"]),
        "export_only_in_the_export_lane": bool(export_segments)
        and all(item["lane"] == "export" for item in export_segments),
        "both_lanes_at_once": bool(run.get("overlap_seconds")),
        "scan_identical_to_alone": comparison["passed"],
        "export_passes_post_export_check": assessment.eligible and encoding.get("encoder") == "h264_nvenc"
        and encoding.get("lane") == "parallel" and not encoding.get("fallback"),
        "every_ffmpeg_below_normal": bool(ffmpeg_seen) and set(ffmpeg_seen) == {BELOW_NORMAL},
        "real_queue_stayed_empty_and_paused": not any(real_changes.values()),
    }
    result["passed"] = all(result.values())
    details = {
        "first_gpu_stage_of_the_film": first_gpu, "timeline": order, "lane_events": events,
        "lane_segments": run["lane_segments"], "overlap_seconds": run.get("overlap_seconds"),
        "stage_overlap_seconds": run["stage_overlap_seconds"],
        "post_export_check": {"eligible_for_delete_source": assessment.eligible, "refusal": assessment.reason,
                              "encoder": encoding.get("encoder"), "lane": encoding.get("lane"),
                              "validation_method": encoding.get("validation_method"),
                              "fallback": encoding.get("fallback")},
        # Counts and file names only: a difference would quote what the detectors read in the film.
        "scan_comparison": {"files": comparison["files"], "identical": comparison["identical"],
                            "only_alone": comparison["only_left"], "only_beside": comparison["only_right"],
                            "different_files": [item["file"] for item in comparison["different"]],
                            "not_compared_run_logs": comparison["not_compared_run_logs"],
                            "routing_cache": {key: value for key, value in comparison["routing_cache"].items()
                                              if key != "differences"},
                            "grounding": comparison["grounding"], "ignored_keys": comparison["ignored_keys"],
                            "passed": comparison["passed"]},
        "process_priority": {name: state["runs"][name].get("process_priority") for name in ("run", "alone")},
        "real_queue_changes": real_changes,
        "seconds": {"run": run.get("total_seconds"), "alone": reference.get("total_seconds")},
        "vram_mib": {"run_before": run.get("vram_before_mib"), "run_peak": run.get("vram_peak_mib"),
                     "alone_peak": reference.get("vram_peak_mib")},
    }
    if comparison["different"]:
        log(f"scan differences (kept out of the evidence): {comparison['different']}")
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
        "clips": {tag: {key: value for key, value in clip.items() if key != "path"}
                  for tag, clip in state["clips"].items()},
        "jobs": state["jobs"], "profile": state.get("profile"), "ready": state.get("ready"), **found,
    }
    target = Path(args.evidence).resolve()
    target.mkdir(parents=True, exist_ok=True)
    (target / "check.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
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
    steps: dict[str, Callable[[argparse.Namespace], int]] = {
        "profile": profile, "ready": ready, "run": run_check, "alone": alone, "report": report, "remove": remove,
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
