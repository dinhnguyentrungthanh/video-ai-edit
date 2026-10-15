"""Perf plan A1b: VRAM of NVENC exports beside the scan stages, measured on a test Control Center.

Never the real Control Center or the install folder: a throw-away root under <install>/temp (made
as scripts/golden_trial_root.py makes one: hard links to models, tools and runtime, a junction to
.venv), its own port, stream-copied clips of two videos (the originals are only read). The real
Control Center must be idle, since both share Local\\BiliFlowGpuInference and
Local\\BiliFlowFinalRender; its /api/status is only read, to record whether it stayed idle.

Phases, with NVML "used" MiB of GPU 0 sampled every 0.1 s throughout:
  idle    the baseline;
  alone   render-final of each clip (NVENC, as an "auto" export chooses it) with nothing else running;
  scan    a scan of the 1080p clip, every detector group, live action: each GPU stage process's peak
          above the VRAM in use just before it started;
  beside  a scan of the small clip, every detector group, mixed style (adds animation_safety); once a
          Qwen2-VL stage (visual_logo, confirm_violence) has loaded its model, render-final of a clip
          runs beside it: NVENC's VRAM on top of Qwen2-VL.
Writes <evidence>/summary.json and <evidence>/samples-1s.csv. No video name or path is written.

Usage (from the worktree):
  python scripts/a1b_vram_measure.py run --install E:\\DungChung\\BiliFlow --code . \\
      --source-1080 <video> --source-small <video> --evidence docs/evidence/a1b [--minutes 10]
  python scripts/a1b_vram_measure.py remove --install E:\\DungChung\\BiliFlow --root <test root>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))
from golden_trial_root import _junction, _link_tree, _links  # noqa: E402

from biliflow.final_renderer import DEFAULT_MAX_OUTPUT_BYTES, DEFAULT_TARGET_OUTPUT_BYTES  # noqa: E402

PREFIX = "a1b-measure-"
# The throw-away roots of the A1b scripts (this one and scripts/a1b_gates.py) and of A1c's check
# (scripts/a1c_check.py).
PREFIXES = (PREFIX, "a1b-gates-", "a1c-check-")
MARKER = "biliflow-install.json"
REAL_PORT = 8765
MIB = 1024 * 1024
LINKED_TREES = ("models", "tools", "runtime", "cache/huggingface", "cache/torch")
FROM_CODE = ("src", "scripts", "config", "pyproject.toml", "requirements.lock.txt")
FROM_INSTALL = (MARKER, "annotations/text_semantics_seed_v1.json", "annotations/review-regression-v1.json",
                "state/brand-memory.json", "state/studio-logo-memory.json", "state/studio-logo-frames")
EMPTY = ("input", "work", "previews", "logs", "temp", "output", "reports/jobs", "reports/benchmarks", "state")
IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc", "*.local.json")
DETECTORS = ["advertising", "adult", "gore", "violence"]
QWEN_STAGES = ("visual_logo", "confirm_violence")
SETTLED = {"WAITING_REVIEW", "READY_TO_EXPORT", "COMPLETED", "FAILED", "PAUSED", "CANCELLED", "SKIPPED"}
# A Qwen2-VL stage counts as loaded once VRAM sits this far above its baseline, steady for 3 s.
LOADED_ABOVE_BASELINE_MIB = 500
STEADY_SPREAD_MIB = 60


def log(message: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {message}", flush=True)


# --- the test root ---------------------------------------------------------------------------------

def check_place(install: Path, root: Path) -> None:
    if root.parent != install / "temp" or not root.name.startswith(PREFIXES):
        raise ValueError(f"{root} is not an A1b measurement root in {install / 'temp'}")


def make_root(install: Path, code: Path, prefix: str = PREFIX) -> Path:
    root = install / "temp" / (prefix + datetime.now().strftime("%Y%m%d-%H%M%S"))
    check_place(install, root)
    root.mkdir()
    try:
        _junction(install / ".venv", root / ".venv")
        for name in LINKED_TREES:
            if (install / name).is_dir():
                _link_tree(install / name, root / name)
        for base, names in ((code, FROM_CODE), (install, FROM_INSTALL)):
            for name in names:
                source, target = base / name, root / name
                if source.is_dir():
                    shutil.copytree(source, target, ignore=IGNORED)
                elif source.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
        for name in EMPTY:
            (root / name).mkdir(parents=True, exist_ok=True)
    except BaseException:
        remove_root(install, root)
        raise
    return root


def remove_root(install: Path, root: Path) -> None:
    """Take every link down without entering it, then delete the test root."""
    check_place(install, root)
    if not root.exists():
        return
    for link in _links(root):
        if os.lstat(link).st_file_attributes & 0x10:  # FILE_ATTRIBUTE_DIRECTORY: a junction
            os.rmdir(link)  # removes the junction itself, never the folder it points to
        else:
            os.unlink(link)
    left = _links(root)
    if left:
        raise RuntimeError(f"links left in the test root, nothing deleted: {left}")
    shutil.rmtree(root)


def probe(ffprobe: Path, video: Path) -> dict:
    out = subprocess.run([str(ffprobe), "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:format=duration", "-of", "json", str(video)],
                         capture_output=True, text=True, timeout=120, check=True)
    info = json.loads(out.stdout)
    return {"width": int(info["streams"][0]["width"]), "height": int(info["streams"][0]["height"]),
            "duration_seconds": float(info["format"]["duration"])}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * MIB), b""):
            digest.update(block)
    return digest.hexdigest()


def cut_clip(ffmpeg: Path, source: Path, target: Path, start: float, seconds: float) -> None:
    """Stream copy into the test root (the original is only read)."""
    subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-ss", str(start), "-i", str(source),
                    "-t", str(seconds), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-sn", "-dn",
                    "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(target)],
                   check=True, timeout=600)


def write_plan(root: Path, tag: str, clip: Path, info: dict) -> Path:
    """An approved edit plan without operations: render-final then only re-encodes the clip.

    One plan per render: a completed render marks its plan FINAL_RENDER_COMPLETED.
    """
    plan = root / "work" / f"a1b-{tag}-edit-plan.json"
    plan.write_text(json.dumps({
        "status": "READY_FOR_FINAL_RENDER", "final_export_allowed": True,
        "source": {"path": str(clip), "sha256": sha256(clip), "duration_seconds": info["duration_seconds"]},
        "approved_operations": [],
    }), encoding="utf-8")
    return plan


# --- sampling --------------------------------------------------------------------------------------

class Sampler(threading.Thread):
    """NVML used MiB of GPU 0 every ``interval`` seconds: [(monotonic, used_mib)]."""

    def __init__(self, interval: float = 0.1) -> None:
        super().__init__(daemon=True)
        self.interval, self.samples, self.stop = interval, [], threading.Event()
        self.total_mib = self.gpu_name = self.driver = None
        self.ready = threading.Event()

    def run(self) -> None:
        import pynvml
        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            self.total_mib = int(pynvml.nvmlDeviceGetMemoryInfo(handle).total) // MIB
            self.gpu_name = str(pynvml.nvmlDeviceGetName(handle))
            self.driver = str(pynvml.nvmlSystemGetDriverVersion())
            self.ready.set()
            while not self.stop.is_set():
                self.samples.append((time.monotonic(), int(pynvml.nvmlDeviceGetMemoryInfo(handle).used) // MIB))
                self.stop.wait(self.interval)
        finally:
            pynvml.nvmlShutdown()

    def window(self, start: float, end: float) -> list[int]:
        return [used for moment, used in list(self.samples) if start <= moment <= end]


class Http:
    def __init__(self, port: int, token: str | None = None) -> None:
        self.base, self.token = f"http://127.0.0.1:{port}", token

    def call(self, method: str, path: str, body: dict | None = None, timeout: float = 15):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if self.token:
            request.add_header("X-BiliFlow-Token", self.token)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)


class Watcher(threading.Thread):
    """Every 0.25 s the test Control Center's running stage process; every 2 s whether the real one is idle."""

    def __init__(self, test: Http, real: Http, sampler: Sampler) -> None:
        super().__init__(daemon=True)
        self.test, self.real, self.sampler = test, real, sampler
        self.stop, self.lock = threading.Event(), threading.Lock()
        self.current: dict | None = None  # {job_id, stage, pid, start, baseline_mib}
        self.processes: list[dict] = []
        self.real_busy: list[dict] = []

    def run(self) -> None:
        last_real = 0.0
        while not self.stop.is_set():
            now = time.monotonic()
            try:
                active = self.test.call("GET", "/api/status").get("active")
            except (OSError, ValueError):
                active = None
            self._observe(active, now)
            if now - last_real >= 2.0:
                last_real = now
                try:  # GET only: the real Control Center is never written to
                    real = self.real.call("GET", "/api/status", timeout=5)
                    if real.get("active") or (real.get("queue") or {}).get("length"):
                        self.real_busy.append({"at": now, "active": real.get("active")})
                except (OSError, ValueError):
                    pass  # not running: idle
            self.stop.wait(0.25)
        self._observe(None, time.monotonic())

    def _observe(self, active: dict | None, now: float) -> None:
        key = (active["job_id"], active["stage"], active["pid"]) if active and active.get("pid") else None
        with self.lock:
            current = self.current
            if current and key != (current["job_id"], current["stage"], current["pid"]):
                current["end"] = now
                window = self.sampler.window(current["start"], now)
                current["peak_mib"] = max(window) if window else None
                current["peak_above_baseline_mib"] = (
                    current["peak_mib"] - current["baseline_mib"]
                    if window and current["baseline_mib"] is not None else None)
                self.processes.append(current)
                self.current = current = None
            if key and current is None:
                # As the scheduler reads it: the VRAM in use just before the process (its first second
                # imports Python and torch; the model loads later).
                before = self.sampler.window(now - 1.0, now)
                self.current = {"job_id": key[0], "stage": key[1], "pid": key[2], "start": now,
                                "baseline_mib": min(before) if before else None}

    def running(self) -> dict | None:
        with self.lock:
            return dict(self.current) if self.current else None


def render(root: Path, plan: Path, output: Path, sampler: Sampler) -> dict:
    """render-final through the test root's run.ps1 (Local\\BiliFlowFinalRender), as the scheduler runs it."""
    command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root / "scripts" / "run.ps1"),
               "render-final", "--plan", str(plan), "--output", str(output),
               "--max-output-bytes", str(DEFAULT_MAX_OUTPUT_BYTES),
               "--target-output-bytes", str(DEFAULT_TARGET_OUTPUT_BYTES)]
    environment = {key: value for key, value in os.environ.items() if key != "BILIFLOW_EXPORT_LANE"}
    start = time.monotonic()
    before = sampler.window(start - 3.0, start)
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=3600, env=environment)
    end = time.monotonic()
    window = sampler.window(start, end)
    manifest_path = output.with_name(output.name + ".manifest.json")
    encoding = {}
    if manifest_path.is_file():
        encoding = json.loads(manifest_path.read_text(encoding="utf-8")).get("encoding", {})
    return {
        "returncode": result.returncode, "start": start, "end": end, "seconds": round(end - start, 1),
        "before_max_mib": max(before) if before else None,
        "before_median_mib": statistics.median(before) if before else None,
        "peak_mib": max(window) if window else None,
        "encoder": encoding.get("encoder"), "nvenc_check": (encoding.get("encoder_choice") or {}).get("nvenc_check"),
        "fallback": encoding.get("fallback"), "encode_seconds": encoding.get("elapsed_seconds"),
        "error_tail": (result.stderr or result.stdout)[-400:] if result.returncode else None,
    }


# --- the run ---------------------------------------------------------------------------------------

def wait_for(predicate, timeout: float, what: str, step: float = 1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    raise TimeoutError(f"timed out waiting for {what}")


def job_for(test: Http, clip: Path) -> dict | None:
    return next((job for job in test.call("GET", "/api/jobs") if Path(job["source_path"]).name == clip.name), None)


def job_state(test: Http, job_id: int) -> str:
    return test.call("GET", f"/api/jobs/{job_id}")["job"]["state"]


def start_scan(test: Http, job_id: int, style: str) -> None:
    test.call("POST", f"/api/jobs/{job_id}/start", {
        "content_style": style, "profile": "careful", "detectors": DETECTORS,
        "ocr_recognition_batch_size": 1, "fast_scan": True,
    })


def healthy(test: Http) -> bool:
    try:
        return test.call("GET", "/healthz", timeout=3).get("status") == "ok"
    except (OSError, ValueError):
        return False


def scan_beside(test: Http, job_id: int, watcher: Watcher, sampler: Sampler, root: Path,
                plans: dict, infos: dict, renders: list) -> str:
    """Start the scan; launch each pending render once a Qwen2-VL stage holds a steady plateau."""
    pending = ["1080p", "small"]
    start_scan(test, job_id, "mixed")
    worker: threading.Thread | None = None
    deadline = time.monotonic() + 5400
    while time.monotonic() < deadline:
        state = job_state(test, job_id)
        idle_worker = worker is None or not worker.is_alive()
        if state in SETTLED and idle_worker:
            return state
        running = watcher.running()
        if pending and idle_worker and running and running["stage"] in QWEN_STAGES:
            now = time.monotonic()
            recent = sampler.window(now - 3.0, now)
            loaded = (running["baseline_mib"] is not None and recent and now - running["start"] > 5
                      and min(recent) >= running["baseline_mib"] + LOADED_ABOVE_BASELINE_MIB
                      and max(recent) - min(recent) < STEADY_SPREAD_MIB)
            if loaded:
                tag = pending.pop(0)
                beside = {"stage": running["stage"], "stage_pid": running["pid"],
                          "stage_baseline_mib": running["baseline_mib"]}

                def go(tag: str = tag, beside: dict = beside) -> None:
                    result = render(root, plans[(tag, "beside")], root / "output" / f"a1b-{tag}-beside.mp4", sampler)
                    after = watcher.running()
                    result.update(tag=tag, frame=infos[tag], **beside,
                                  stage_still_running=bool(after and after["pid"] == beside["stage_pid"]))
                    renders.append(result)
                    log(f"beside {tag}: {result}")

                worker = threading.Thread(target=go, daemon=True)
                worker.start()
        time.sleep(0.25)
    raise TimeoutError("the scan with renders beside it did not end")


def run(args: argparse.Namespace) -> int:
    install, code = Path(args.install).resolve(strict=True), Path(args.code).resolve(strict=True)
    evidence = Path(args.evidence).resolve()
    if args.port == REAL_PORT:
        raise SystemExit("the real Control Center's port is never used")
    real = Http(REAL_PORT)
    try:
        status = real.call("GET", "/api/status", timeout=5)
    except OSError:
        status = {}  # not running
    if status.get("active") or (status.get("queue") or {}).get("length"):
        raise SystemExit(f"the real Control Center is busy: {status.get('active')}, queue {status.get('queue')}")
    tools = install / "tools" / "ffmpeg" / "bin"
    root = make_root(install, code)
    log(f"test root {root}")
    clips, plans, infos = {}, {}, {}
    for tag, source in (("1080p", Path(args.source_1080)), ("small", Path(args.source_small))):
        clip = root / "input" / f"a1b-{tag}.mp4"
        cut_clip(tools / "ffmpeg.exe", source, clip, args.start_at, args.minutes * 60)
        infos[tag] = probe(tools / "ffprobe.exe", clip)
        clips[tag] = clip
        for phase in ("alone", "beside"):
            plans[(tag, phase)] = write_plan(root, f"{tag}-{phase}", clip, infos[tag])
        log(f"clip {tag}: {infos[tag]}")
    sampler = Sampler()
    sampler.start()
    sampler.ready.wait(10)
    environment = {**os.environ, "PYTHONPATH": str(root / "src")}
    environment.pop("BILIFLOW_EXPORT_LANE", None)
    center_log = (root / "logs" / "test-control-center.out.log").open("ab")
    center = subprocess.Popen(
        [str(root / ".venv" / "Scripts" / "python.exe"), "-m", "biliflow.control_entry", "--project-root", str(root),
         "--host", "127.0.0.1", "--port", str(args.port), "--stable-seconds", "5", "--no-import-existing"],
        cwd=root, env=environment, stdout=center_log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    log(f"test Control Center pid {center.pid} on port {args.port}")
    test = Http(args.port)
    watcher: Watcher | None = None
    alone: list[dict] = []
    beside: list[dict] = []
    marks: list[tuple[float, str]] = [(time.monotonic(), "start")]
    idle: list[int] = []
    jobs: dict[str, int] = {}
    states: dict[str, str] = {}
    error = None
    try:
        wait_for(lambda: healthy(test), 120, "the test Control Center")
        test.token = test.call("GET", "/api/session")["token"]
        watcher = Watcher(test, real, sampler)
        watcher.start()
        marks.append((time.monotonic(), "idle"))
        log("idle 15 s")
        time.sleep(15)
        idle = sampler.window(marks[-1][0], time.monotonic())
        marks.append((time.monotonic(), "alone"))
        for tag in ("1080p", "small"):
            result = render(root, plans[(tag, "alone")], root / "output" / f"a1b-{tag}-alone.mp4", sampler)
            result.update(tag=tag, frame=infos[tag])
            alone.append(result)
            log(f"alone {tag}: {result}")
            time.sleep(5)
        for tag in clips:
            jobs[tag] = wait_for(lambda tag=tag: job_for(test, clips[tag]), 600, f"the job of {tag}")["id"]
        marks.append((time.monotonic(), "scan"))
        log(f"scan alone: job {jobs['1080p']}")
        start_scan(test, jobs["1080p"], "live_action")
        states["scan"] = wait_for(lambda: (state := job_state(test, jobs["1080p"])) in SETTLED and state,
                                  5400, "the scan alone", step=2.0)
        log(f"scan alone ended {states['scan']}")
        time.sleep(5)
        marks.append((time.monotonic(), "beside"))
        log(f"scan with renders beside Qwen2-VL: job {jobs['small']}")
        states["beside"] = scan_beside(test, jobs["small"], watcher, sampler, root, plans, infos, beside)
        log(f"beside ended {states['beside']}")
    except BaseException as caught:  # noqa: BLE001 - recorded in the evidence, then raised again below
        error = f"{type(caught).__name__}: {caught}"
        log(f"stopped: {error}")
    finally:
        marks.append((time.monotonic(), "end"))
        try:
            if test.token:
                test.call("POST", "/api/shutdown", {"mode": "immediate"})
            center.wait(90)
        except Exception:  # noqa: BLE001 - our own child: stop its tree
            subprocess.run(["taskkill", "/PID", str(center.pid), "/T", "/F"], capture_output=True)
        center_log.close()
        if watcher:
            watcher.stop.set()
            watcher.join(10)
        sampler.stop.set()
        sampler.join(10)
    summary = summarize(sampler, watcher, idle, alone, beside, infos, marks, jobs, states, code, root, error)
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    write_seconds(evidence / "samples-1s.csv", sampler, marks)
    log(f"evidence in {evidence}")
    if args.keep_root:
        log(f"test root kept: {root}")
    else:
        remove_root(install, root)
        log("test root removed")
    return 1 if error else 0


def summarize(sampler, watcher, idle, alone, beside, infos, marks, jobs, states, code, root, error) -> dict:
    start = marks[0][0]
    head = subprocess.run(["git", "-C", str(code), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)

    def seconds(item: dict) -> dict:
        item["start_s"], item["end_s"] = round(item.pop("start") - start, 1), round(item.pop("end") - start, 1)
        return item

    processes = [seconds(item) for item in (watcher.processes if watcher else [])]
    for item in alone + beside:
        seconds(item)
        if item.get("peak_mib") is not None and item.get("before_max_mib") is not None:
            item["peak_above_before_mib"] = item["peak_mib"] - item["before_max_mib"]
    for item in processes:
        # A stage process that overlapped a render: its peak counts the render too.
        item["overlaps_render"] = any(r["start_s"] < item["end_s"] and item["start_s"] < r["end_s"] for r in beside)
    stage_peaks: dict[str, int] = {}
    for item in processes:
        value = item.get("peak_above_baseline_mib")
        if value is not None and not item["overlaps_render"]:
            stage_peaks[item["stage"]] = max(stage_peaks.get(item["stage"], value), value)
    return {
        "measured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "code_commit": head.stdout.strip(), "test_root": root.name, "error": error,
        "gpu": {"name": sampler.gpu_name, "driver": sampler.driver, "total_mib": sampler.total_mib},
        "settings": {"profile": "careful", "fast_scan": True, "ocr_recognition_batch_size": 1,
                     "detectors": DETECTORS, "scan_alone_style": "live_action", "beside_style": "mixed",
                     "max_output_bytes": DEFAULT_MAX_OUTPUT_BYTES},
        "clips": infos, "jobs": jobs, "job_states": states,
        "idle_mib": {"min": min(idle), "max": max(idle)} if idle else None,
        "renders_alone": alone, "renders_beside_qwen": beside,
        "stage_processes": processes,
        "stage_peak_above_baseline_mib": stage_peaks,
        "real_control_center_busy": watcher.real_busy if watcher else None,
        "phases": [{"phase": name, "at_s": round(moment - start, 1)} for moment, name in marks],
    }


def write_seconds(path: Path, sampler: Sampler, marks: list[tuple[float, str]]) -> None:
    """Min and max NVML used MiB per second, with the phase it fell in."""
    start = marks[0][0]
    rows: dict[int, list[int]] = {}
    for moment, used in sampler.samples:
        if moment >= start:
            rows.setdefault(int(moment - start), []).append(used)
    lines = ["second,min_mib,max_mib,phase"]
    for second in sorted(rows):
        phase = next((name for moment, name in reversed(marks) if moment - start <= second), "")
        lines.append(f"{second},{min(rows[second])},{max(rows[second])},{phase}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    go = commands.add_parser("run")
    go.add_argument("--install", required=True)
    go.add_argument("--code", required=True, help="the checkout whose src, scripts and config the test root runs")
    go.add_argument("--source-1080", required=True)
    go.add_argument("--source-small", required=True)
    go.add_argument("--evidence", required=True)
    go.add_argument("--minutes", type=float, default=10.0)
    go.add_argument("--start-at", type=float, default=600.0, help="where each clip starts in its video (seconds)")
    go.add_argument("--port", type=int, default=8791)
    go.add_argument("--keep-root", action="store_true")
    drop = commands.add_parser("remove")
    drop.add_argument("--install", required=True)
    drop.add_argument("--root", required=True)
    args = parser.parse_args()
    if args.command == "remove":
        remove_root(Path(args.install).resolve(strict=True), Path(args.root))
        return 0
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
