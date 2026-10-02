"""Stage-level full-film equivalence of the exact-output speedups (task B) against HEAD trials.

  run      Re-runs, with the working tree, only the stages the speedups touch, on the
           FULL film, with exactly the commands (arguments, wrappers, GPU slot) the HEAD
           baseline trial executed; only the report root and the benchmark cache
           namespace change. Untouched stages are not re-run; their baseline outputs
           are reused (the OCR report, and the routing cache that the OCR stage's
           prewarm wrote, copied into a new namespace so the logo stage reads exactly
           what the baseline logo stage read; the GroundingDINO cache starts empty as
           in the baseline).
             troy     adult (R3 on), verify_adult, live_safety, confirm_violence,
                      visual_logo, localize_logo (Florence, then GroundingDINO)
             conan20  animation_safety, visual_logo, localize_logo
  compare  Read-only. Every scan JSON of the re-run stages must equal the baseline after
           removing performance/timestamp fields (metrics, elapsed_seconds,
           peak_cuda_bytes, *_at) and routing_cache.path, and masking the report roots;
           an upstream-report SHA-256 (verify-adult) is checked against the file it
           names on each side and then masked. Every JPEG must have the same SHA-256.
           Review queues are rebuilt from the baseline and from the candidate reports
           (each side's files copied to the baseline's relative report paths inside its
           own temporary project root, because review item ids hash the report path)
           with ONE build code per comparison (the committed HEAD package extracted with
           git archive, and the working tree), so edits to review_workflow.py made by
           other work cannot be attributed to the speedups. A dry edit plan (fixed
           decision policy, built in a temporary root under temp/) must match too.
Everything is written under reports/benchmarks/exact-speedups/stages-<timestamp>/.
Source videos, production jobs, queues, state and brand memory are only read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/benchmarks/exact-speedups"
BRAND_MEMORY = ROOT / "state/brand-memory.json"
PYTHON = ROOT / ".venv/Scripts/python.exe"
BASELINES = {
    "troy": "troy-allgroups-full-speedbase-20261001-203927",
    "conan20": "conan20-allgroups-full-speedbase-20261001-215759",
}
# Stages whose code the speedups touch (T1 confirm, T5a hash in adult/live/animation,
# T5b R3 in adult, T5d logo VLM loop); verify_adult and localize_logo are re-run so the
# candidate chain feeding the review queue is produced end to end by the new code.
RERUN = {
    "troy": ("adult", "verify_adult", "live_safety", "confirm_violence", "visual_logo", "localize_logo"),
    "conan20": ("animation_safety", "visual_logo", "localize_logo"),
}
# Report folders written by the re-run stages (compared file by file).
STAGE_FOLDERS = {
    "troy": ("adult", "gore", "violence", "visual-logo"),
    "conan20": ("animation-safety", "visual-logo"),
}
# Baseline reports reused unchanged by the candidate queue (stage not re-run).
REUSED_REPORTS = {"troy": ("text/text-scan.json",), "conan20": ("text/text-scan.json",)}
WATCHED_SOURCES = (
    "vlm_confirmation.py", "frame_prefetch.py", "live_safety_scanner.py", "scanner.py",
    "animation_safety_scanner.py", "shot_cuts.py", "visual_logo_scanner.py", "source_hash.py",
    "adult_verification.py", "florence_regions.py", "ad_candidate_pipeline.py", "cli.py",
    "brand_memory.py", "content_scanner.py", "review_workflow.py",
)


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _fingerprint() -> dict:
    sources = ROOT / "src/biliflow"
    return {
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "git_diff_src_sha256": hashlib.sha256(
            subprocess.check_output(["git", "diff", "--", "src"], cwd=ROOT, stderr=subprocess.DEVNULL)).hexdigest(),
        "files": {path.name: _sha(path) for path in sorted(sources.glob("*.py"))},
    }


def _changed_files(before: dict, after: dict) -> list[str]:
    names = set(before["files"]) | set(after["files"])
    return sorted(name for name in names if before["files"].get(name) != after["files"].get(name))


def _protected_snapshot(trials: dict) -> dict:
    """Read-only digests of production data the run must not touch."""
    snapshot = {"brand_memory_sha256": _sha(BRAND_MEMORY)}
    for film, trial in trials.items():
        original = trial["before"]["original_job"]
        source = Path(original["source_path"])
        queue = ROOT / original["active_queue_path"]
        snapshot[film] = {"source_size": source.stat().st_size, "source_mtime_ns": source.stat().st_mtime_ns,
                          "active_queue_sha256": _sha(queue)}
    return snapshot


class LoadSampler:
    """CPU and GPU load every few seconds, as in the baseline trial's evidence."""

    def __init__(self, directory: Path, every: float = 5.0):
        self.directory, self.every = directory, every
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        import psutil

        cpu = (self.directory / "cpu-load.csv").open("w", encoding="utf-8")
        gpu = (self.directory / "gpu-load.csv").open("w", encoding="utf-8")
        cpu.write("time,cpu_percent,available_mib\n")
        gpu.write("time,utilization_gpu_percent,memory_used_mib\n")
        psutil.cpu_percent(None)
        try:
            while not self.stop.wait(self.every):
                now = datetime.now().isoformat(timespec="seconds")
                cpu.write(f"{now},{psutil.cpu_percent(None)},{psutil.virtual_memory().available // 2**20}\n")
                cpu.flush()
                try:
                    value = subprocess.check_output(
                        ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"],
                        text=True, creationflags=subprocess.CREATE_NO_WINDOW).strip().splitlines()[0]
                    gpu.write(f"{now},{value.replace(' ', '')}\n")
                    gpu.flush()
                except (OSError, subprocess.CalledProcessError, IndexError):
                    pass
        finally:
            cpu.close()
            gpu.close()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(timeout=30)
        return False


def _read_trial(film: str) -> dict:
    return json.loads((ROOT / "reports/benchmarks" / BASELINES[film] / "trial.json").read_text(encoding="utf-8"))


def _substitute(argv: list[str], old_root: Path, new_root: Path) -> list[str]:
    result = [value.replace(str(old_root), str(new_root)) for value in argv]
    if any(BASELINES_KEY in value for value in result for BASELINES_KEY in BASELINES.values()):
        raise RuntimeError(f"Baseline path left in command: {result}")
    return result


def run(args) -> None:
    films = [film for film in args.films.split(",") if film]
    if not films or set(films) - set(BASELINES):
        raise SystemExit(f"--films must be a subset of {sorted(BASELINES)}")
    run_dir = OUT / ("stages-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    (run_dir / "logs").mkdir(parents=True)
    trials = {film: _read_trial(film) for film in films}
    for film, trial in trials.items():
        if trial.get("status") != "WAITING_REVIEW" or not trial.get("preserved_original_data"):
            raise RuntimeError(f"Baseline trial for {film} is not a finished, preserved trial")
    manifest = {
        "kind": "stage-level full-film equivalence (working tree vs HEAD baseline trials)",
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "fingerprint_start": _fingerprint(), "protected_before": _protected_snapshot(trials),
        "gpu_mutex": "every stage command takes Local\\BiliFlowGpuInference (run.ps1 / benchmark wrappers)",
        "films": {},
    }
    manifest_path = run_dir / "run.json"

    def save():
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    save()
    print(run_dir, flush=True)
    previous = manifest["fingerprint_start"]
    with LoadSampler(run_dir):
        for film in films:
            trial = trials[film]
            baseline_root = ROOT / "reports/jobs" / BASELINES[film]
            candidate_root = run_dir / "jobs" / film
            candidate_root.mkdir(parents=True)
            (candidate_root / ".biliflow-benchmark").write_text("Isolated trial; never auto-import", encoding="utf-8")
            cache = run_dir / "cache" / film
            baseline_cache = ROOT / "reports/benchmarks" / BASELINES[film] / "cold-cache"
            # Routing cache written by the baseline OCR stage's prewarm (not re-run); the
            # GroundingDINO cache is not copied, so localization runs cold as in the baseline.
            shutil.copytree(baseline_cache / "visual-logo", cache / "visual-logo")
            row = {"baseline_trial": BASELINES[film], "candidate_root": candidate_root.relative_to(ROOT).as_posix(),
                   "cache": cache.relative_to(ROOT).as_posix(),
                   "copied_routing_cache": {path.relative_to(cache).as_posix(): _sha(path)
                                            for path in sorted((cache / "visual-logo").rglob("*")) if path.is_file()},
                   "reused_baseline_stages": [stage["name"] for stage in trial["stages"]
                                              if stage["name"] not in RERUN[film]],
                   "stages": []}
            manifest["films"][film] = row
            save()
            by_name = {stage["name"]: stage for stage in trial["stages"]}
            for name in RERUN[film]:
                stage = by_name[name]
                record = {"name": name, "baseline_seconds": stage["seconds"], "commands": []}
                row["stages"].append(record)
                started = time.perf_counter()
                for index, command in enumerate(stage["commands"]):
                    argv = _substitute(command["executed"], baseline_root, candidate_root)
                    record["commands"].append(argv)
                    save()
                    print(f"START {film} {name}-{index}", flush=True)
                    env = dict(os.environ, BILIFLOW_BENCHMARK_CACHE=str(cache), PYTHONIOENCODING="utf-8")
                    command_started = time.perf_counter()
                    with (run_dir / "logs" / f"{film}-{name}-{index}.log").open("w", encoding="utf-8") as log:
                        subprocess.run(argv, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=True,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
                    record.setdefault("command_seconds", []).append(round(time.perf_counter() - command_started, 3))
                record["seconds"] = round(time.perf_counter() - started, 3)
                current = _fingerprint()
                record["source_files_changed_during_stage"] = _changed_files(previous, current)
                previous = current
                save()
                print(f"DONE {film} {name}: {record['seconds']:.1f}s (baseline {stage['seconds']:.1f}s)", flush=True)
                if name == "visual_logo":
                    report = json.loads((candidate_root / "visual-logo/scan.json").read_text(encoding="utf-8"))
                    record["routing_cache_hit"] = report["routing_cache"]["hit"]
                    if not record["routing_cache_hit"]:
                        raise RuntimeError("Logo stage did not reuse the copied routing cache")
            row["rerun_seconds"] = round(sum(stage["seconds"] for stage in row["stages"]), 3)
            row["baseline_seconds_same_stages"] = round(sum(stage["baseline_seconds"] for stage in row["stages"]), 3)
            save()
    manifest["fingerprint_end"] = _fingerprint()
    manifest["source_files_changed_during_run"] = _changed_files(manifest["fingerprint_start"],
                                                                 manifest["fingerprint_end"])
    manifest["protected_after"] = _protected_snapshot(trials)
    manifest["protected_data_unchanged"] = manifest["protected_before"] == manifest["protected_after"]
    manifest["completed_at"] = datetime.now().isoformat(timespec="seconds")
    save()
    print(json.dumps({film: {"rerun_seconds": row["rerun_seconds"],
                             "baseline_seconds_same_stages": row["baseline_seconds_same_stages"]}
                      for film, row in manifest["films"].items()}), flush=True)


TIMESTAMP_KEYS = {"created_at", "verified_at", "updated_at", "started_at", "completed_at"}
# Performance telemetry; the full trials already document these as run-dependent.
PERFORMANCE_KEYS = {"metrics", "elapsed_seconds", "peak_cuda_bytes"}
DETECTORS = ["advertising", "adult", "gore", "violence"]
STYLES = {"troy": "live_action", "conan20": "animation"}
# Scanner-internal phase blocks written by each re-run stage (file, metrics key).
STAGE_BLOCKS = {
    "adult": (("adult/scan.json", "performance"),),
    "verify_adult": (("adult/scan-verified.json", "verification_performance"),),
    "live_safety": (("gore/scan.json", "performance"),),
    "confirm_violence": (("violence/scan-confirmed.json", "confirmation_performance"),
                         ("violence/scan-confirmed.json", "confirmation_prefetch_performance")),
    "visual_logo": (("visual-logo/scan.json", "performance"), ("visual-logo/scan.json", "vlm_prefetch_performance")),
    "localize_logo": (("visual-logo/scan-florence.json", "localization_performance"),
                      ("visual-logo/scan-localized.json", "grounding_performance")),
    "animation_safety": (("animation-safety/scan.json", "performance"),),
}
HEAD_SRC = ROOT / "temp/stage-equivalence-head-src"
PLAN_ROOT = ROOT / "temp/stage-equivalence-plans"
QUEUE_ROOT = ROOT / "temp/stage-equivalence-queues"
# Runs in a child interpreter whose PYTHONPATH selects the build code (HEAD package or
# working tree): rebuild one queue from scan reports, then a dry edit plan from it.
BUILD_AND_PLAN = r'''
import inspect, json, shutil, sys
from pathlib import Path
spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
import biliflow
from biliflow import review_workflow as rw
module = Path(biliflow.__file__).resolve()
if not module.is_relative_to(Path(spec["src"]).resolve()):
    raise SystemExit(f"wrong biliflow package: {module}")
queue_path = Path(spec["queue"])
kwargs = dict(project_root=Path(spec["root"]), report_paths=[Path(p) for p in spec["reports"]],
              queue_path=queue_path, merge_gap_seconds=1.0, selected_detectors=spec["detectors"],
              content_style=spec["style"], adult_triage_level=None)
if "use_studio_logo_memory" in inspect.signature(rw.build_review_queue).parameters:
    # Same value the working-tree CLI passes for build-review without --no-studio-logo-memory.
    kwargs["use_studio_logo_memory"] = rw.studio_logo_memory_allowed(queue_path)
queue = rw.build_review_queue(**kwargs)

def decide(item):
    suggested, region = item.get("suggested_decision"), item.get("suggested_region_source_pixels")
    if suggested in ("KEEP", "CUT"):
        return suggested, None
    if suggested == "BLUR":
        return "BLUR", region or "FULL_FRAME"
    if item["category"] == "adult":
        return "CUT", None
    if item["category"] in ("violence", "gore"):
        return "BLUR", "FULL_FRAME"
    return ("BLUR", region) if region else ("KEEP", None)

plan_root = Path(spec["plan_root"])
if plan_root.exists():
    shutil.rmtree(plan_root)
(plan_root / "reports").mkdir(parents=True)
(plan_root / "work").mkdir()
decided = json.loads(queue_path.read_text(encoding="utf-8"))
for item in decided["items"]:
    item["decision"], item["decision_region_source_pixels"] = decide(item)
(plan_root / "reports" / "queue.json").write_text(json.dumps(decided, ensure_ascii=False), encoding="utf-8")
plan = rw.build_edit_plan(project_root=plan_root, queue_path=plan_root / "reports" / "queue.json",
                          plan_path=plan_root / "work" / "plan.json")
shutil.copy2(plan_root / "work" / "plan.json", spec["plan_out"])
shutil.rmtree(plan_root)
print(json.dumps({"module": str(module), "items": len(queue["items"]),
                  "advisory_items": len(queue.get("advisory_items") or []),
                  "operations": len(plan["approved_operations"]),
                  "decisions": {d: sum(1 for i in decided["items"] if i["decision"] == d)
                                for d in ("KEEP", "BLUR", "CUT")}}))
'''


def _masker(pairs: list[tuple[Path, str]]):
    """Replace each spelling (absolute/relative, \\ or /) of a path with a placeholder."""
    replacements = []
    for path, placeholder in pairs:
        relative = path.relative_to(ROOT)
        for form, separator in ((str(path), "\\"), (path.as_posix(), "/"),
                                (relative.as_posix(), "/"), (str(relative), "\\")):
            replacements.append((form, placeholder.replace("/", separator)))
    replacements.sort(key=lambda pair: -len(pair[0]))

    def mask(text: str) -> str:
        for old, new in replacements:
            text = text.replace(old, new)
        return text
    return mask


def _verify_upstream_hashes(value, side: str, checks: list, path: str = ""):
    """verify-adult records the SHA-256 of the scan.json it read (which holds metrics and
    timestamps); check it against that file on this side, then mask it."""
    if isinstance(value, dict):
        if "source_report_sha256" in value and "source_report" in value:
            actual = _sha(ROOT / str(value["source_report"]).replace("\\", "/"))
            ok = actual == value["source_report_sha256"]
            checks.append({"side": side, "path": path, "source_report": value["source_report"], "matches_file": ok})
            value["source_report_sha256"] = "<SHA256 OF source_report, verified>" if ok else "<STALE>"
        for key, item in value.items():
            _verify_upstream_hashes(item, side, checks, f"{path}/{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _verify_upstream_hashes(item, side, checks, f"{path}/{index}")


def _normalize(value, mask, ignored: set, path: str = ""):
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            child = f"{path}/{key}"
            if key in TIMESTAMP_KEYS or key in PERFORMANCE_KEYS:
                ignored.add(child)
                continue
            if key == "routing_cache" and isinstance(item, dict) and "path" in item:
                ignored.add(child + "/path")
                item = {k: v for k, v in item.items() if k != "path"}
            output[key] = _normalize(item, mask, ignored, child)
        return output
    if isinstance(value, list):
        return [_normalize(item, mask, ignored, f"{path}/{index}") for index, item in enumerate(value)]
    if isinstance(value, str):
        return mask(value)
    return value


def _differences(a, b, path: str = "", out: list | None = None, limit: int = 25) -> list:
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if type(a) is not type(b):
        out.append({"path": path, "baseline": repr(a)[:160], "candidate": repr(b)[:160]})
    elif isinstance(a, dict):
        for key in sorted(a.keys() | b.keys()):
            if key not in a or key not in b:
                out.append({"path": f"{path}/{key}", "baseline": "present" if key in a else "missing",
                            "candidate": "present" if key in b else "missing"})
            else:
                _differences(a[key], b[key], f"{path}/{key}", out, limit)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append({"path": path, "baseline": f"len {len(a)}", "candidate": f"len {len(b)}"})
        for index, (x, y) in enumerate(zip(a, b)):
            _differences(x, y, f"{path}/{index}", out, limit)
    elif a != b:
        out.append({"path": path, "baseline": repr(a)[:160], "candidate": repr(b)[:160]})
    return out


def _compare_json(base_value, cand_value, base_mask, cand_mask) -> dict:
    # Work on copies: upstream-hash masking edits the values in place.
    base_value, cand_value = json.loads(json.dumps(base_value)), json.loads(json.dumps(cand_value))
    ignored_base, ignored_cand, checks = set(), set(), []
    _verify_upstream_hashes(base_value, "baseline", checks)
    _verify_upstream_hashes(cand_value, "candidate", checks)
    a = _normalize(base_value, base_mask, ignored_base)
    b = _normalize(cand_value, cand_mask, ignored_cand)
    rows = _differences(a, b)
    patterns = sorted({re.sub(r"/\d+(?=/|$)", "/N", path) for path in ignored_base | ignored_cand})
    result = {"equal": not rows and all(check["matches_file"] for check in checks),
              "ignored_field_paths": patterns}
    if ignored_base != ignored_cand:
        result["ignored_paths_only_on_one_side"] = sorted(ignored_base ^ ignored_cand)[:20]
    if checks:
        result["upstream_hash_checks"] = checks
    if rows:
        result["first_differences"] = rows
    return result


def _cache_roots(film: str, run_dir: Path) -> tuple[Path, Path]:
    return ROOT / "reports/benchmarks" / BASELINES[film] / "cold-cache", run_dir / "cache" / film


def _compare_scan_reports(film: str, run_dir: Path, baseline_root: Path, candidate_root: Path) -> dict:
    base_cache, cand_cache = _cache_roots(film, run_dir)
    base_mask = _masker([(baseline_root, "<REPORT_ROOT>"), (base_cache, "<CACHE_ROOT>")])
    cand_mask = _masker([(candidate_root, "<REPORT_ROOT>"), (cand_cache, "<CACHE_ROOT>")])
    result = {"files": {}, "folders": {}}
    for folder in STAGE_FOLDERS[film]:
        sides = [{path.relative_to(root).as_posix(): path for path in (root / folder).rglob("*") if path.is_file()}
                 for root in (baseline_root, candidate_root)]
        summary = {"json": [0, 0], "jpg": [0, 0], "other": [0, 0], "missing": []}
        for name in sorted(sides[0].keys() | sides[1].keys()):
            if name not in sides[0] or name not in sides[1]:
                summary["missing"].append({"file": name, "baseline": name in sides[0], "candidate": name in sides[1]})
                continue
            base_path, cand_path = sides[0][name], sides[1][name]
            suffix = base_path.suffix.lower()
            kind = "json" if suffix == ".json" else "jpg" if suffix in (".jpg", ".jpeg") else "other"
            if kind == "json":
                row = _compare_json(json.loads(base_path.read_text(encoding="utf-8")),
                                    json.loads(cand_path.read_text(encoding="utf-8")), base_mask, cand_mask)
                result["files"][name] = row
                equal = row["equal"]
            else:
                equal = _sha(base_path) == _sha(cand_path)
                if not equal and kind == "other":
                    equal = base_mask(base_path.read_text(encoding="utf-8")) == cand_mask(
                        cand_path.read_text(encoding="utf-8"))
                    result["files"][name] = {"equal": equal, "note": "bytes differ; compared with masked roots"}
                elif not equal:
                    result["files"][name] = {"equal": False, "note": "SHA-256 differs"}
            summary[kind][0] += int(equal)
            summary[kind][1] += 1
        summary["equal"] = not summary["missing"] and all(done == total for done, total in
                                                          (summary["json"], summary["jpg"], summary["other"]))
        result["folders"][folder] = summary
    result["all_equal"] = all(row["equal"] for row in result["folders"].values())
    return result


def _head_package() -> Path:
    import io
    import tarfile

    if HEAD_SRC.exists():
        shutil.rmtree(HEAD_SRC)
    HEAD_SRC.mkdir(parents=True)
    archive = subprocess.check_output(["git", "archive", "--format=tar", "HEAD", "src"], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(HEAD_SRC)
    return HEAD_SRC / "src"


def _stage_project(film: str, side: str, baseline_root: Path, candidate_root: Path) -> Path:
    """Temporary project root holding one side's reports at the baseline's relative paths.

    Review item ids hash the report path (``_item_id``), so both queues must read their
    reports from the same relative paths to be comparable. The files are byte copies:
    the baseline side copies the baseline reports; the candidate side copies the re-run
    stages' reports plus the reused baseline OCR folder.
    """
    project = QUEUE_ROOT / f"{film}-{side}"
    if project.exists():
        shutil.rmtree(project)
    target = project / baseline_root.relative_to(ROOT)
    for folder in (*STAGE_FOLDERS[film], *sorted({Path(name).parts[0] for name in REUSED_REPORTS[film]})):
        reused = any(Path(name).parts[0] == folder for name in REUSED_REPORTS[film])
        source = baseline_root if side == "baseline" or reused else candidate_root
        shutil.copytree(source / folder, target / folder)
    # verify-adult names the scan.json it read by its real path and the builder checks
    # that file's SHA-256; place the same bytes at that path inside this root too.
    for verified in sorted(target.rglob("scan-verified.json")):
        named = json.loads(verified.read_text(encoding="utf-8")).get("adult_verification", {}).get("source_report")
        if named and not (project / named).exists():
            (project / named).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / named, project / named)
    (project / "reports" / "rebuild").mkdir(parents=True)
    return project


def _build(run_dir: Path, film: str, side: str, code: str, src: Path, project: Path) -> dict:
    target = run_dir / "queues" / f"{film}-{side}-{code}"
    target.mkdir(parents=True)
    report_dir = project / ROOT.joinpath("reports/jobs", BASELINES[film]).relative_to(ROOT)
    queue = project / "reports" / "rebuild" / f"review-queue-{code}.json"
    spec = {"src": str(src), "root": str(project), "reports": [str(report_dir / name) for name in QUEUE_REPORTS[film]],
            "queue": str(queue), "detectors": DETECTORS, "style": STYLES[film],
            "plan_root": str(PLAN_ROOT / f"{film}-{side}-{code}"), "plan_out": str(target / "dry-edit-plan.json")}
    (target / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    script = run_dir / "queues" / "build_and_plan.py"
    if not script.exists():
        script.write_text(BUILD_AND_PLAN.lstrip(), encoding="utf-8")
    # As in the trials' build_review stage: the benchmark cache variable is set, so the
    # working-tree builder ignores user studio-logo state (HEAD has no such state).
    env = dict(os.environ, PYTHONPATH=str(src), PYTHONIOENCODING="utf-8",
               BILIFLOW_BENCHMARK_CACHE=str(_cache_roots(film, run_dir)[1]))
    completed = subprocess.run([str(PYTHON), str(script), str(target / "spec.json")], cwd=ROOT, env=env,
                               capture_output=True, text=True, encoding="utf-8",
                               creationflags=subprocess.CREATE_NO_WINDOW)
    (target / "build.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
    if completed.returncode:
        raise RuntimeError(f"Queue build failed for {film}-{side}-{code}; see {target / 'build.log'}")
    shutil.copy2(queue, target / "review-queue.json")
    return {"dir": target, **json.loads(completed.stdout.strip().splitlines()[-1])}


# Same reports, same order as the baseline trials' build-review commands.
QUEUE_REPORTS = {
    "troy": ["adult/scan-verified.json", "gore/scan.json", "violence/scan-confirmed.json", "text/text-scan.json",
             "visual-logo/scan-localized.json"],
    "conan20": ["animation-safety/adult/scan.json", "animation-safety/gore/scan.json",
                "animation-safety/violence/scan.json", "text/text-scan.json", "visual-logo/scan-localized.json"],
}


def _compare_queues(film: str, run_dir: Path, baseline_root: Path, candidate_root: Path, sources: dict) -> dict:
    projects = {side: _stage_project(film, side, baseline_root, candidate_root) for side in ("baseline", "candidate")}
    masks = {side: _masker([(project, "<PROJECT>")]) for side, project in projects.items()}
    layout = ("each side's report files copied to reports/jobs/" + BASELINES[film]
              + "/... inside its own temporary project root (item ids hash the report path)")
    result = {}
    try:
        for code, src in sources.items():
            built = {side: _build(run_dir, film, side, code, src, projects[side]) for side in projects}
            row = {"layout": layout, **{side: {k: v for k, v in info.items() if k != "dir"}
                                        for side, info in built.items()}}
            queues = {side: json.loads((info["dir"] / "review-queue.json").read_text(encoding="utf-8"))
                      for side, info in built.items()}
            plans = {side: json.loads((info["dir"] / "dry-edit-plan.json").read_text(encoding="utf-8"))
                     for side, info in built.items()}
            pair = (masks["baseline"], masks["candidate"])
            for part in ("items", "advisory_items"):
                row[part] = _compare_json({part: queues["baseline"].get(part)},
                                          {part: queues["candidate"].get(part)}, *pair)
            row["whole_queue"] = _compare_json(queues["baseline"], queues["candidate"], *pair)
            row["dry_edit_plan"] = _compare_json(plans["baseline"], plans["candidate"], *pair)
            row["equal"] = all(row[key]["equal"] for key in ("items", "advisory_items", "whole_queue", "dry_edit_plan"))
            result[code] = row
    finally:
        for project in projects.values():
            shutil.rmtree(project, ignore_errors=True)
    return result


def _phases(path: Path, key: str) -> dict | None:
    if not path.is_file():
        return None
    block = (json.loads(path.read_text(encoding="utf-8")).get("metrics") or {}).get(key)
    if not isinstance(block, dict) or "phases" not in block:
        return None
    return {"total_wall_seconds": round(block.get("total_wall_seconds", 0.0), 1),
            "phases": {name: round(value["wall_seconds"], 1) for name, value in block["phases"].items()}}


def _load_means(rows: list[tuple[datetime, float]], start: datetime, end: datetime) -> float | None:
    values = [value for moment, value in rows if start <= moment <= end]
    return round(sum(values) / len(values), 1) if values else None


def _read_load(path: Path, column: str, day: str | None) -> list[tuple[datetime, float]]:
    import csv

    if not path.is_file():
        return []
    rows = []
    with path.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            try:
                moment = row["time"] if day is None else f"{day}T{row['time']}"
                rows.append((datetime.fromisoformat(moment), float(row[column])))
            except (KeyError, ValueError):
                continue
    return rows


def _timings(film: str, run_dir: Path, manifest: dict, baseline_root: Path, candidate_root: Path) -> dict:
    trial = _read_trial(film)
    evidence = ROOT / "reports/benchmarks" / BASELINES[film]
    base_cpu = _read_load(evidence / "cpu-load.csv", "total_cpu_pct", trial["started_at"][:10])
    base_gpu = _read_load(evidence / "gpu-load.csv", "gpu_util_pct", trial["started_at"][:10])
    cand_cpu = _read_load(run_dir / "cpu-load.csv", "cpu_percent", None)
    cand_gpu = _read_load(run_dir / "gpu-load.csv", "utilization_gpu_percent", None)
    by_name = {stage["name"]: stage for stage in trial["stages"]}
    stages = {}
    for record in manifest["films"][film]["stages"]:
        name = record["name"]
        base_stage = by_name[name]
        base_start = datetime.fromisoformat(base_stage["started_at"]).replace(tzinfo=None)
        base_end = datetime.fromisoformat(base_stage["completed_at"]).replace(tzinfo=None)
        logs = sorted((run_dir / "logs").glob(f"{film}-{name}-*.log"))
        cand_start = datetime.fromtimestamp(min(path.stat().st_ctime for path in logs))
        cand_end = datetime.fromtimestamp(max(path.stat().st_mtime for path in logs))
        stages[name] = {
            "baseline_seconds": round(record["baseline_seconds"], 1), "candidate_seconds": round(record["seconds"], 1),
            "delta_seconds": round(record["seconds"] - record["baseline_seconds"], 1),
            "mean_cpu_percent": {"baseline": _load_means(base_cpu, base_start, base_end),
                                 "candidate": _load_means(cand_cpu, cand_start, cand_end)},
            "mean_gpu_util_percent": {"baseline": _load_means(base_gpu, base_start, base_end),
                                      "candidate": _load_means(cand_gpu, cand_start, cand_end)},
            "phases": {f"{file}:{key}": {"baseline": _phases(baseline_root / file, key),
                                         "candidate": _phases(candidate_root / file, key)}
                       for file, key in STAGE_BLOCKS[name]},
        }
    total_base = sum(row["baseline_seconds"] for row in stages.values())
    total_cand = sum(row["candidate_seconds"] for row in stages.values())
    return {"stages": stages, "rerun_stages_baseline_seconds": round(total_base, 1),
            "rerun_stages_candidate_seconds": round(total_cand, 1),
            "rerun_stages_delta_seconds": round(total_cand - total_base, 1),
            "baseline_trial_total_seconds": round(trial["total_wall_seconds"], 1),
            "projected_trial_total_seconds": round(trial["total_wall_seconds"] + total_cand - total_base, 1)}


def compare(args) -> None:
    run_dir = args.run_dir.resolve()
    if not run_dir.is_relative_to(OUT):
        raise SystemExit(f"--run-dir must be under {OUT}")
    manifest = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    if "completed_at" not in manifest:
        raise SystemExit("The run did not complete")
    if (run_dir / "queues").exists():
        raise SystemExit("Queues were already built for this run; compare once per run directory")
    summary = {"run": run_dir.relative_to(ROOT).as_posix(), "compared_at": datetime.now().isoformat(timespec="seconds"),
               "fingerprint": _fingerprint(), "brand_memory_sha256_before": _sha(BRAND_MEMORY),
               "ignored": {"keys_anywhere": sorted(TIMESTAMP_KEYS | PERFORMANCE_KEYS),
                           "other": ["routing_cache/path", "report-root spellings masked",
                                     "verify-adult source_report_sha256 checked against its file, then masked"]},
               "films": {}}
    sources = {"head_build_code": _head_package(), "tree_build_code": ROOT / "src"}
    (run_dir / "queues").mkdir()
    (run_dir / "queues" / ".biliflow-benchmark").write_text("Isolated trial; never auto-import", encoding="utf-8")
    summary["build_code"] = {"head_build_code": "git archive HEAD src -> " + HEAD_SRC.relative_to(ROOT).as_posix(),
                             "tree_build_code_review_workflow_sha256": _sha(ROOT / "src/biliflow/review_workflow.py")}
    try:
        for film in manifest["films"]:
            baseline_root = ROOT / "reports/jobs" / BASELINES[film]
            candidate_root = ROOT / manifest["films"][film]["candidate_root"]
            print(f"compare {film}", flush=True)
            summary["films"][film] = {
                "scan_reports": _compare_scan_reports(film, run_dir, baseline_root, candidate_root),
                "review": _compare_queues(film, run_dir, baseline_root, candidate_root, sources),
                "timings": _timings(film, run_dir, manifest, baseline_root, candidate_root),
            }
    finally:
        shutil.rmtree(HEAD_SRC, ignore_errors=True)
        shutil.rmtree(PLAN_ROOT, ignore_errors=True)
        shutil.rmtree(QUEUE_ROOT, ignore_errors=True)
    summary["brand_memory_sha256_after"] = _sha(BRAND_MEMORY)
    summary["tree_review_workflow_changed_during_compare"] = (
        summary["build_code"]["tree_build_code_review_workflow_sha256"] != _sha(ROOT / "src/biliflow/review_workflow.py"))
    summary["all_identical"] = all(
        row["scan_reports"]["all_equal"] and all(review["equal"] for review in row["review"].values())
        for row in summary["films"].values())
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str),
                                          encoding="utf-8")
    print(json.dumps({"all_identical": summary["all_identical"],
                      "films": {film: {"scan_reports": row["scan_reports"]["all_equal"],
                                       "review": {code: review["equal"] for code, review in row["review"].items()},
                                       "rerun_delta_seconds": row["timings"]["rerun_stages_delta_seconds"]}
                                for film, row in summary["films"].items()}}, indent=1), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--films", default="troy,conan20")
    c = sub.add_parser("compare")
    c.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "run":
        run(args)
    else:
        compare(args)


if __name__ == "__main__":
    main()
