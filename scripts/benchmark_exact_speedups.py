"""Exact-output speedups: HEAD vs working tree on real video (task B, T1/T4/T5).

  r3           CPU only. Rebuilds the full-film R3 edge windows of the Troy HEAD
               baseline adult report, decodes them serially (workers=1, the HEAD
               behaviour) and with the default 2 workers; the cut lists must be
               identical and match the baseline report's cut_count.
  equivalence  GPU; run it through scripts/benchmark-exact-speedups.ps1 (shared
               GPU slot). The committed scanners (git show HEAD, written to
               reports/benchmarks/exact-speedups/baseline_*.py; the working tree is
               untouched) and the working-tree scanners run through the real CLI
               dispatch with the job pipeline's arguments on:
                 Troy excerpts troy-0400 (adult scene) and troy-5400 (battle):
                   adult scan with R3, shared live safety (fp16 violence) and
                   confirm-violence on the excerpt's violence report;
                 full Troy: confirm-violence on an evenly spaced subset of the HEAD
                   baseline trial's violence intervals (real seeks across the film);
                 Conan Movie 20 excerpt (0-300 s): animation safety (fp16) and the
                   visual-logo scan (VLM loop; both read the same routing cache).
               Every scan JSON must be byte-identical after removing metrics,
               created_at and verified_at and masking the report directories;
               every JPEG must have the same SHA-256. --parts limits the run
               (adult, live, confirm, animation, logo, full-confirm).
  ab           GPU; timing only. HEAD and working tree alternate (H, N, N, H), each
               run in its own process as in the pipeline: confirm-violence on an
               evenly spaced subset of the Troy baseline intervals and the
               visual-logo scan of the Conan excerpt (routing cache hit).
Everything is written under reports/benchmarks/exact-speedups/. Sources, jobs,
queues, annotations and brand memory are only read (brand memory SHA-256 is
recorded before and after).
"""
from __future__ import annotations

import argparse
import contextlib
import gc
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/benchmarks/exact-speedups"
FFMPEG = ROOT / "tools/ffmpeg/bin/ffmpeg.exe"
TROY_BASELINE = ROOT / "reports/jobs/troy-allgroups-full-speedbase-20261001-203927"
TROY_EXCERPTS = ROOT / "reports/benchmarks/live-safety-h/excerpts"
# (name, golden source key, start, duration)
EXCERPTS = (("troy-0400", "troy", 400.0, 150.0), ("troy-5400", "troy", 5400.0, 150.0),
            ("conan20-0000", "conan20", 0.0, 300.0))
CONFIRM_SUBSET = 40
AB_CONFIRM_INTERVALS = 24
AB_ORDER = ("head", "now", "now", "head")
PARTS = ("adult", "live", "confirm", "animation", "logo", "full-confirm")
IGNORED = ("metrics", "created_at", "verified_at")
BRAND_MEMORY = ROOT / "state" / "brand-memory.json"


def _source(key: str) -> Path:
    manifest = json.loads((ROOT / "annotations/golden/v1/segments.json").read_text(encoding="utf-8"))
    return ROOT / manifest["sources"][key]["path"]


def _excerpt(name: str, key: str, start: float, duration: float) -> Path:
    existing = TROY_EXCERPTS / f"{name}.mp4"
    if existing.exists():
        return existing
    target = OUT / "excerpts" / f"{name}.mp4"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-ss", str(start), "-t", str(duration),
                        "-i", str(_source(key)), "-map", "0:v:0", "-c", "copy", "-an", str(target)], check=True)
    return target


def _digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _code_fingerprint() -> dict:
    return {"git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "git_diff_src_sha256": hashlib.sha256(
                subprocess.check_output(["git", "diff", "--", "src"], cwd=ROOT)).hexdigest()}


def _head_module(relative: str, name: str):
    code = subprocess.check_output(["git", "show", f"HEAD:{relative}"], cwd=ROOT)
    path = OUT / f"baseline_{name}.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(code)
    spec = importlib.util.spec_from_file_location(f"baseline_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve string annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def _head_patches() -> list:
    """Point the CLI dispatch (and R3's lazy shot_cuts import) at the committed code."""
    from biliflow import cli

    shot_cuts = _head_module("src/biliflow/shot_cuts.py", "shot_cuts")
    scanner = _head_module("src/biliflow/scanner.py", "scanner")
    content = _head_module("src/biliflow/content_scanner.py", "content_scanner")
    live = _head_module("src/biliflow/live_safety_scanner.py", "live_safety_scanner")
    live._load_classifier = content._load_classifier  # HEAD gore scorer, not the split one
    vlm = _head_module("src/biliflow/vlm_confirmation.py", "vlm_confirmation")
    animation = _head_module("src/biliflow/animation_safety_scanner.py", "animation_safety_scanner")
    logo = _head_module("src/biliflow/visual_logo_scanner.py", "visual_logo_scanner")
    return [
        mock.patch("biliflow.shot_cuts.find_window_cuts", shot_cuts.find_window_cuts),
        mock.patch.object(cli, "scan_nsfw", scanner.scan_nsfw),
        mock.patch.object(cli, "scan_live_safety", live.scan_live_safety),
        mock.patch.object(cli, "confirm_violence_report", vlm.confirm_violence_report),
        mock.patch.object(cli, "scan_animation_safety", animation.scan_animation_safety),
        mock.patch.object(cli, "scan_visual_logos", logo.scan_visual_logos),
    ]


def _run_cli(argv: list, *, head_patches: list | None, log: Path) -> float:
    from biliflow import cli

    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as handle, contextlib.ExitStack() as stack:
        for patch in head_patches or []:
            stack.enter_context(patch)
        stack.enter_context(mock.patch.object(sys, "argv", ["biliflow", *map(str, argv)]))
        stack.enter_context(contextlib.redirect_stdout(handle))
        started = time.perf_counter()
        try:
            code = cli.main()
        finally:
            seconds = time.perf_counter() - started
            _release_gpu_memory()
    if code != 0:
        raise RuntimeError(f"{argv[0]} exited with {code}; see {log}")
    return round(seconds, 3)


def _release_gpu_memory() -> None:
    """Each pipeline stage is its own process; release cached VRAM between in-process runs."""
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # telemetry-free best effort
        pass


def _gpu_memory_used_mib() -> int | None:
    try:
        output = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], text=True)
        return int(output.strip().splitlines()[0])
    except (OSError, subprocess.CalledProcessError, ValueError, IndexError):
        return None


def _normalized(directory: Path) -> dict:
    output = {}
    masks = [json.dumps(str(directory))[1:-1], directory.as_posix(), str(directory)]
    for path in sorted(directory.rglob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            for key in IGNORED:
                value.pop(key, None)
        text = json.dumps(value, sort_keys=True, ensure_ascii=False)
        for mask in masks:
            text = text.replace(mask, "<REPORT_DIR>")
        output[path.relative_to(directory).as_posix()] = json.loads(text)
    return output


def _images(directory: Path) -> dict[str, str]:
    return {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.rglob("*.jpg"))}


def _compare(head: Path, now: Path) -> dict:
    head_reports, now_reports = _normalized(head), _normalized(now)
    head_images, now_images = _images(head), _images(now)
    differing = sorted(name for name in set(head_reports) | set(now_reports)
                       if head_reports.get(name) != now_reports.get(name))
    return {"reports": sorted(now_reports), "reports_identical": not differing, "differing_reports": differing,
            "images": len(now_images), "images_identical": head_images == now_images}


def _phases(report: Path, key: str = "performance") -> dict:
    metrics = json.loads(report.read_text(encoding="utf-8")).get("metrics") or {}
    block = metrics.get(key) or {}
    return {name: round(value["wall_seconds"], 2) for name, value in (block.get("phases") or {}).items()}


def _confirm_pair(run_dir: Path, name: str, source_report: Path, head: list, intervals: list | None = None) -> dict:
    payload = json.loads(source_report.read_text(encoding="utf-8"))
    if intervals is not None:
        payload["intervals"] = intervals
    if not payload.get("intervals"):
        return {"skipped": "no violence intervals"}
    row = {"intervals": len(payload["intervals"]), "seconds": {}}
    for variant in ("head", "now"):
        directory = run_dir / f"{name}-{variant}" / "violence"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "scan.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        row["seconds"][variant] = _run_cli(
            ["confirm-violence", "--report", directory / "scan.json", "--output", directory / "scan-confirmed.json",
             "--device", "cuda"],
            head_patches=head if variant == "head" else None, log=run_dir / "logs" / f"{name}-{variant}.log")
    row.update(_compare(run_dir / f"{name}-head", run_dir / f"{name}-now"))
    row["phases"] = {variant: _phases(run_dir / f"{name}-{variant}/violence/scan-confirmed.json",
                                      "confirmation_performance") for variant in ("head", "now")}
    row["prefetch_phases_now"] = _phases(run_dir / f"{name}-now/violence/scan-confirmed.json",
                                         "confirmation_prefetch_performance")
    return row


def _adult_argv(video: Path, target: Path) -> list:
    return ["scan", "--input", video, "--report-dir", target, "--sample-fps", "2.0",
            "--review-merge-gap-seconds", "3.0", "--sequence-context-threshold", "0.7",
            "--sequence-context-seconds", "8.0", "--content-style", "live_action", "--device", "cuda",
            "--shot-completion"]


def _live_argv(video: Path, target: Path) -> list:
    return ["scan-live-safety", "--input", video, "--report-dir", target, "--gore-sample-fps", "2.0",
            "--violence-sample-fps", "8.0", "--device", "cuda", "--violence-precision", "fp16"]


def _animation_argv(video: Path, target: Path) -> list:
    return ["scan-animation-safety", "--input", video, "--report-dir", target, "--sample-fps", "2.0",
            "--device", "cuda", "--precision", "fp16"]


def _logo_argv(video: Path, target: Path) -> list:
    return ["scan-visual-logo", "--input", video, "--report-dir", target, "--sample-every", "2.0",
            "--boundary-sample-every", "0.25", "--boundary-seconds", "30.0", "--max-candidate-windows", "420",
            "--scene-change-threshold", "0.22", "--coverage-bucket-seconds", "300.0",
            "--coverage-fallbacks-per-bucket", "2", "--device", "cuda", "--routing-workers", "3"]


def equivalence(args) -> None:
    parts = set(args.parts.split(",")) if args.parts else set(PARTS)
    unknown = parts - set(PARTS)
    if unknown:
        raise SystemExit(f"Unknown parts: {sorted(unknown)}")
    if "confirm" in parts:
        parts.add("live")  # the excerpt confirmation reads the working tree's live-safety report
    run_dir = OUT / ("equivalence-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    run_dir.mkdir(parents=True)
    print(f"run dir: {run_dir}", flush=True)
    summary = {**_code_fingerprint(), "started_at": datetime.now().isoformat(timespec="seconds"),
               "parts": sorted(parts), "brand_memory_sha256_before": _digest(BRAND_MEMORY),
               # VRAM held by other processes before any model loads (6 GB card; the VLM peaks near 4.6 GB).
               "gpu_memory_used_mib_at_start": _gpu_memory_used_mib(), "runs": {}}
    summary_path = run_dir / "summary.json"

    def save():
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    head = _head_patches()
    for name, key, start, duration in EXCERPTS:
        labels = [label for label in (("adult", "live") if key == "troy" else ("animation", "logo"))
                  if label in parts]
        if not labels:
            continue
        video = _excerpt(name, key, start, duration)
        base = run_dir / name
        commands = {"adult": (_adult_argv, ("head", "now")), "live": (_live_argv, ("now", "head")),
                    "animation": (_animation_argv, ("head", "now")),
                    # The first run writes the routing cache; HEAD and the working tree then both read it.
                    "logo": (_logo_argv, ("warm", "head", "now"))}
        row = {"seconds": {}}
        for label in labels:
            build, order = commands[label]
            for variant in order:
                row["seconds"][f"{label}-{variant}"] = _run_cli(
                    build(video, base / f"{label}-{variant}"), head_patches=head if variant == "head" else None,
                    log=base / "logs" / f"{label}-{variant}.log")
            row[label] = _compare(base / f"{label}-head", base / f"{label}-now")
        if "adult" in labels:
            row["adult"]["phases"] = {v: _phases(base / f"adult-{v}/scan.json") for v in ("head", "now")}
            shot = json.loads((base / "adult-now/scan.json").read_text(encoding="utf-8"))["shot_completion"]
            row["adult"]["shot_completion"] = {k: shot.get(k) for k in
                                               ("status", "decoded_edge_count", "cut_count", "extended_interval_count")}
        if "live" in labels:
            row["live"]["phases"] = {v: _phases(base / f"live-{v}/gore/scan.json") for v in ("head", "now")}
            if "confirm" in parts:
                row["confirm"] = _confirm_pair(base, "confirm", base / "live-now/violence/scan.json", head)
        if "logo" in labels:
            row["logo"]["routing_cache_hit"] = {
                v: json.loads((base / f"logo-{v}/scan.json").read_text(encoding="utf-8"))["routing_cache"]["hit"]
                for v in ("warm", "head", "now")}
            row["logo"]["phases"] = {v: _phases(base / f"logo-{v}/scan.json") for v in ("head", "now")}
        summary["runs"][name] = row
        save()
        print(json.dumps({name: row}, ensure_ascii=False), flush=True)

    if "full-confirm" in parts:
        baseline = TROY_BASELINE / "violence" / "scan.json"
        subset = _spread(json.loads(baseline.read_text(encoding="utf-8"))["intervals"], CONFIRM_SUBSET)
        summary["runs"]["troy-full-confirm"] = _confirm_pair(run_dir / "troy-full", "confirm", baseline, head, subset)
        summary["runs"]["troy-full-confirm"]["baseline_report"] = baseline.relative_to(ROOT).as_posix()
    summary["brand_memory_sha256_after"] = _digest(BRAND_MEMORY)
    checks = {}
    for name, row in summary["runs"].items():
        items = {label: row[label] for label in ("adult", "live", "confirm", "animation", "logo") if label in row}
        if "reports_identical" in row:
            items = {"confirm": row}
        for label, item in items.items():
            if "reports_identical" in item:
                checks[f"{name}/{label}"] = item["reports_identical"] and item["images_identical"]
    summary["identity_checks"] = checks
    summary["all_identical"] = bool(checks) and all(checks.values())
    summary["brand_memory_unchanged"] = summary["brand_memory_sha256_before"] == summary["brand_memory_sha256_after"]
    summary["code_unchanged_during_run"] = _code_fingerprint() == {k: summary[k] for k in ("git_head", "git_diff_src_sha256")}
    summary["completed_at"] = datetime.now().isoformat(timespec="seconds")
    save()
    print(json.dumps({k: summary[k] for k in ("all_identical", "brand_memory_unchanged", "code_unchanged_during_run")}),
          flush=True)
    print("EQUIVALENCE DONE", flush=True)


def _spread(items: list, count: int) -> list:
    step = max(1, len(items) // count)
    return items[::step][:count]


def one(args) -> None:
    """One timing run in this process (called by ab)."""
    target = Path(args.target)
    head = _head_patches() if args.variant == "head" else None
    if args.kind == "confirm":
        baseline = TROY_BASELINE / "violence" / "scan.json"
        payload = json.loads(baseline.read_text(encoding="utf-8"))
        payload["intervals"] = _spread(payload["intervals"], AB_CONFIRM_INTERVALS)
        directory = target / "violence"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "scan.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        argv = ["confirm-violence", "--report", directory / "scan.json", "--output",
                directory / "scan-confirmed.json", "--device", "cuda"]
    else:
        video = _excerpt(*EXCERPTS[2])
        argv = _logo_argv(video, target / "logo")
    seconds = _run_cli(argv, head_patches=head, log=target / "run.log")
    print(json.dumps({"seconds": seconds}), flush=True)


def ab(args) -> None:
    run_dir = OUT / ("ab-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    run_dir.mkdir(parents=True)
    print(f"run dir: {run_dir}", flush=True)
    summary = {**_code_fingerprint(), "order": AB_ORDER, "confirm_intervals": AB_CONFIRM_INTERVALS, "runs": []}
    for kind in args.kinds.split(","):
        for index, variant in enumerate(AB_ORDER):
            target = run_dir / kind / f"{index}-{variant}"
            vram = _gpu_memory_used_mib()
            started = time.perf_counter()
            subprocess.run([sys.executable, str(Path(__file__).resolve()), "one", "--kind", kind,
                            "--variant", variant, "--target", str(target)], check=True, env=os.environ.copy())
            seconds = round(time.perf_counter() - started, 3)
            report = (target / "violence" / "scan-confirmed.json") if kind == "confirm" else (target / "logo" / "scan.json")
            metrics = json.loads(report.read_text(encoding="utf-8"))["metrics"]
            block = metrics.get("confirmation_performance" if kind == "confirm" else "performance") or {}
            prefetch = metrics.get("confirmation_prefetch_performance" if kind == "confirm"
                                   else "vlm_prefetch_performance") or {}
            row = {"kind": kind, "variant": variant, "process_seconds": seconds, "gpu_memory_used_mib_before": vram,
                   "elapsed_seconds": metrics.get("confirmation_elapsed_seconds", metrics.get("elapsed_seconds")),
                   "peak_cuda_memory_bytes": metrics.get("confirmation_peak_cuda_memory_bytes",
                                                         metrics.get("peak_cuda_memory_bytes")),
                   "phases": {k: (round(v["wall_seconds"], 3), v["calls"]) for k, v in (block.get("phases") or {}).items()},
                   "producer_phases": {k: round(v["wall_seconds"], 3) for k, v in (prefetch.get("phases") or {}).items()}}
            summary["runs"].append(row)
            print(json.dumps(row), flush=True)
            (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    means = {}
    for row in summary["runs"]:
        entry = means.setdefault(f"{row['kind']}-{row['variant']}", {"runs": 0, "elapsed_seconds": 0.0, "model_step": 0.0,
                                                                     "frame_extract": 0.0, "prefetch_wait": 0.0})
        entry["runs"] += 1
        entry["elapsed_seconds"] += row["elapsed_seconds"] or 0.0
        for phase in ("model_step", "frame_extract", "prefetch_wait"):
            entry[phase] += row["phases"].get(phase, (0.0, 0))[0]
    summary["means"] = {key: {k: (round(v / value["runs"], 3) if k != "runs" else v) for k, v in value.items()}
                        for key, value in means.items()}
    summary["code_unchanged_during_run"] = _code_fingerprint() == {k: summary[k] for k in ("git_head", "git_diff_src_sha256")}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary["means"], indent=2), flush=True)
    print("AB DONE", flush=True)


def r3(args) -> None:
    from biliflow import shot_cuts
    from biliflow.scanner import (
        SHOT_COMPLETION_MAXIMUM_EXTENSION_SECONDS as EXTENSION,
        SHOT_COMPLETION_REACH_SECONDS as REACH,
        SHOT_COMPLETION_WINDOW_MARGIN_SECONDS as MARGIN,
        _union_seconds,
    )

    report = json.loads((TROY_BASELINE / "adult" / "scan.json").read_text(encoding="utf-8"))
    duration = float(report["duration_seconds"])
    padding = 1.0  # scan --padding-seconds default, as in the pipeline
    undecoded = {"at_video_start", "at_video_end", "too_few_seeds_nearby"}
    windows = []
    for interval in report["intervals"]:
        context = interval["shot_context"]
        start, end = float(context["input_start_seconds"]), float(context["input_end_seconds"])
        if context["start"]["status"] not in undecoded:
            windows.append((max(0.0, start - EXTENSION - MARGIN), min(duration, start + padding + REACH + MARGIN)))
        if context["end"]["status"] not in undecoded:
            windows.append((max(0.0, end - padding - REACH - MARGIN), min(duration, end + EXTENSION + MARGIN)))
    statistics = report["shot_completion"]
    source = Path(report["input"])
    result = {"baseline_report": (TROY_BASELINE / "adult/scan.json").relative_to(ROOT).as_posix(),
              "decoded_edge_count": len(windows), "baseline_decoded_edge_count": statistics["decoded_edge_count"],
              "decoded_window_seconds": round(_union_seconds(windows), 3),
              "baseline_decoded_window_seconds": statistics["decoded_window_seconds"],
              "merged_windows": len(shot_cuts.merge_windows(windows)), "seconds": {}, "cuts": {}}
    for label, workers in (("serial", 1), ("parallel_2", 2), ("parallel_2_repeat", 2), ("serial_repeat", 1)):
        started = time.perf_counter()
        cuts = shot_cuts.find_window_cuts(FFMPEG, source, windows, workers=workers)
        result["seconds"][label] = round(time.perf_counter() - started, 3)
        result["cuts"][label] = cuts
        print(label, result["seconds"][label], len(cuts), flush=True)
    serial = result["cuts"]["serial"]
    result["identical"] = all(value == serial for value in result["cuts"].values())
    result["cut_count"] = len(serial)
    result["baseline_cut_count"] = statistics["cut_count"]
    result["matches_baseline"] = (len(serial) == statistics["cut_count"]
                                  and result["decoded_window_seconds"] == statistics["decoded_window_seconds"])
    result["speedup"] = round((result["seconds"]["serial"] + result["seconds"]["serial_repeat"])
                              / (result["seconds"]["parallel_2"] + result["seconds"]["parallel_2_repeat"]), 3)
    result["cuts"] = {"serial": serial}
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / ("r3-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
    target.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "cuts"}, indent=2), flush=True)
    print(target, flush=True)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    equivalence_parser = sub.add_parser("equivalence")
    equivalence_parser.add_argument("--parts", help="comma-separated subset of " + ",".join(PARTS))
    sub.add_parser("r3")
    ab_parser = sub.add_parser("ab")
    ab_parser.add_argument("--kinds", default="confirm,logo")
    one_parser = sub.add_parser("one")
    one_parser.add_argument("--kind", choices=("confirm", "logo"), required=True)
    one_parser.add_argument("--variant", choices=("head", "now"), required=True)
    one_parser.add_argument("--target", required=True)
    args = parser.parse_args()
    {"equivalence": equivalence, "r3": r3, "ab": ab, "one": one}[args.command](args)


if __name__ == "__main__":
    main()
