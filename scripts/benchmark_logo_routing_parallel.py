"""Bounded frame-parallel logo routing experiment; never touches production jobs.

Subcommands:
  cpu       In-memory routing (features + brand memory) on original-source
            frames: serial vs RoutingPool(k); exact output comparison.
  pipeline  Actual cold scan_visual_logos control flow (FFmpeg decode included),
            stopped before the VLM loads; routing caches are intercepted, never
            read or written. Compares windows, features, JPEG hashes, selection.
  overlap   Contention test: the real OCR stage CLI (scan-text, cross-frame
            mode) alone vs concurrently with full-film parallel routing.
  route-once  Internal: one routing run (used by overlap as a child process).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import psutil

from biliflow import visual_logo_scanner as scanner
from biliflow.brand_memory import load_brand_memory
from biliflow.performance import ScanPerformance
from biliflow.probe import probe_video

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = ROOT / "tools/ffmpeg/bin/ffmpeg.exe"
FFPROBE = ROOT / "tools/ffmpeg/bin/ffprobe.exe"
CPU_CASES = [("*Troy*", 0, 30, .25), ("*Troy*", 48, 60, 2), ("*Troy*", 418, 60, 2),
             ("*Movie 21*", 4200, 60, 2), ("*Movie 21*", 6630, 60, 2), ("*Movie 20*", 240, 60, 2)]
PRODUCTION_QUEUE = ROOT / ("reports/jobs/tm2-troy-2004-directors-cut-1080p-bluray-dd5-1-x-"
                           "f43cf94a-run-20260927-234709/review-queue.json")


def source(pattern):
    found = list((ROOT / "input").glob(pattern + ".mp4"))
    if len(found) != 1:
        raise ValueError(f"Expected one source for {pattern}")
    return found[0].resolve(strict=True)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_state():
    return {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "src_diff_sha256": hashlib.sha256(subprocess.check_output(["git", "diff", "--", "src"], cwd=ROOT)).hexdigest()}


def output_dir(prefix):
    out = ROOT / "reports/benchmarks" / (prefix + datetime.now().strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=False)
    (out / ".biliflow-benchmark").write_text("isolated logo routing benchmark\n", encoding="utf-8")
    print(out, flush=True)
    return out


class ResourceSampler:
    """Peak RSS / CPU of this process plus children (FFmpeg, routing workers)."""

    def __init__(self, interval=0.5):
        self.interval, self.peak_rss, self.samples = interval, 0, []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        me = psutil.Process()
        while not self._stop.wait(self.interval):
            try:
                procs = [me] + me.children(recursive=True)
                self.peak_rss = max(self.peak_rss, sum(p.memory_info().rss for p in procs if p.is_running()))
                self.samples.append(psutil.cpu_percent(None))
            except psutil.Error:
                pass

    def __enter__(self):
        psutil.cpu_percent(None)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()

    def result(self):
        return {"peak_rss_bytes_incl_children": self.peak_rss,
                "mean_system_cpu_percent": statistics.mean(self.samples) if self.samples else None}


def cpu(args):
    out = output_dir("logo-routing-parallel-cpu-")
    memory = [r for r in load_brand_memory(ROOT)["records"] if isinstance(r, dict)]
    brand_before = digest(ROOT / "state/brand-memory.json")
    cases, stats = [], {}
    for pattern, start, duration, interval in CPU_CASES:
        src = source(pattern)
        stats[str(src)] = (src.stat().st_size, src.stat().st_mtime_ns)
        w, h = scanner._video_size(probe_video(FFPROBE, src))
        height = max(2, round(h * 320 / w / 2) * 2)
        frames = list(scanner._iter_frames(ffmpeg_path=FFMPEG, input_path=src, start=start, duration=duration,
                                           sample_every=interval, width=320, height=height))
        cases.append({"source": src.name, "start": start, "frames": frames})
    modes = {"S": None, **{f"P{k}": (k, 1) for k in args.workers}, "P4t": (4, None)}
    order = list(modes) + list(reversed(modes))
    runs, reference = [], None
    for label in order:
        config = modes[label]
        began = perf_counter()
        pool = scanner.RoutingPool(config[0], memory, opencv_threads=config[1]) if config else None
        try:
            outputs = [[f for _, _, f in scanner._iter_routed_frames(iter(c["frames"]), memory,
                                                                      ScanPerformance(), pool)] for c in cases]
        finally:
            if pool is not None:
                pool.close()
        seconds = perf_counter() - began
        reference = reference or outputs
        runs.append({"mode": label, "seconds": seconds, "exact": outputs == reference,
                     **({"pool": pool.metrics()} if pool else {})})
        print(json.dumps({k: v for k, v in runs[-1].items() if k != "pool"}), flush=True)
    medians = {m: statistics.median(r["seconds"] for r in runs if r["mode"] == m) for m in modes}
    result = {"scope": "In-memory CPU routing only (pool start/stop included; decode excluded)",
              **git_state(), "frames": sum(len(c["frames"]) for c in cases), "cpu_count": psutil.cpu_count(),
              "order": order, "runs": runs, "medians": medians,
              "speedup_vs_serial": {m: medians["S"] / v for m, v in medians.items()},
              "all_exact": all(r["exact"] for r in runs),
              "sources_unchanged": all((Path(k).stat().st_size, Path(k).stat().st_mtime_ns) == v
                                       for k, v in stats.items()),
              "brand_memory_unchanged": digest(ROOT / "state/brand-memory.json") == brand_before}
    (out / "cpu.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("medians", "speedup_vs_serial", "all_exact", "sources_unchanged",
                                             "brand_memory_unchanged")}, indent=2))
    if not result["all_exact"]:
        raise SystemExit("Parallel routing differs from serial; do not enable")


class RoutingCaptured(Exception):
    pass


def run_pipeline_once(src, start, duration, workers, directory):
    """Actual cold routing control flow; returns (snapshot, timings)."""
    snapshot, timing = {}, {}
    choose, hasher = scanner.select_candidate_windows, scanner.validated_source_sha256

    def timed_hash(*a, **k):
        began = perf_counter()
        try:
            return hasher(*a, **k)
        finally:
            timing["source_hash_seconds"] = perf_counter() - began

    def capture_write(_path, **payload):
        snapshot["routing"] = {k: v for k, v in payload.items() if k not in {"windows", "cache_key", "window_sample_counts",
                                                                             "boundary_keys"}}
        snapshot["routing"]["window_sample_counts"] = {str(k): v for k, v in payload["window_sample_counts"].items()}
        snapshot["routing"]["boundary_keys"] = sorted(map(list, payload["boundary_keys"]))

    def capture_selection(windows, boundaries, maximum, **kwargs):
        timing["routing_done"] = perf_counter()
        selected = choose(windows, boundaries, maximum, **kwargs)
        snapshot["selected"] = [list(k) for k in selected]
        snapshot["windows"] = [{"key": list(key), "frames": [
            {**{k: v for k, v in f.items() if k not in {"jpeg", "focus_jpeg"}},
             "jpeg_sha256": hashlib.sha256(f["jpeg"]).hexdigest(),
             "focus_sha256": hashlib.sha256(f["focus_jpeg"]).hexdigest() if f.get("focus_jpeg") else None}
            for f in windows[key]]} for key in sorted(windows)]
        snapshot["vlm_evidence"] = {str(key): [
            {"t": f["timestamp_seconds"], "jpeg": hashlib.sha256(f["jpeg"]).hexdigest(),
             "focus": hashlib.sha256(f["focus_jpeg"]).hexdigest() if f.get("focus_jpeg") else None}
            for f in scanner.select_window_evidence(windows[key], maximum=2)] for key in sorted(selected)}
        raise RoutingCaptured()

    began = perf_counter()
    with ResourceSampler() as sampler, \
            patch.object(scanner, "_read_routing_cache", return_value=None), \
            patch.object(scanner, "_write_routing_cache", side_effect=capture_write), \
            patch.object(scanner, "select_candidate_windows", side_effect=capture_selection), \
            patch.object(scanner, "validated_source_sha256", side_effect=timed_hash):
        try:
            scanner.scan_visual_logos(
                project_root=ROOT, input_path=src, report_dir=directory,
                model_path=ROOT / "models/qwen2_vl_2b_instruct", ffmpeg_path=FFMPEG, ffprobe_path=FFPROBE,
                start_seconds=start, duration_seconds_limit=duration, sample_every=2.0,
                boundary_sample_every=.25, boundary_seconds=30.0, max_candidate_windows=80,
                routing_workers=workers)
        except RoutingCaptured:
            pass
        else:
            raise RuntimeError("Scanner did not reach candidate selection")
    total = timing["routing_done"] - began
    return snapshot, {"routing_seconds_incl_hash": total,
                      "routing_seconds": total - timing["source_hash_seconds"],
                      "source_hash_seconds": timing["source_hash_seconds"], **sampler.result()}


def pipeline(args):
    out = output_dir("logo-routing-parallel-pipeline-")
    brand_before, queue_before = digest(ROOT / "state/brand-memory.json"), digest(PRODUCTION_QUEUE)
    cases = ([(args.full_source, 0, None)] if args.full_source else
             [("*Troy*", 0, 600), ("*Movie 21*", 4000, 600)])
    order = list(args.order)
    modes = {"S": 1, "P": args.workers}
    runs, reference = [], {}
    for index, label in enumerate(order):
        rows = []
        for case_index, (pattern, start, duration) in enumerate(cases):
            src = source(pattern)
            stat_before = (src.stat().st_size, src.stat().st_mtime_ns)
            snapshot, timing = run_pipeline_once(src, start, duration, modes[label],
                                                 out / f"run-{index}-{label}" / f"case-{case_index}")
            reference.setdefault(case_index, snapshot)
            rows.append({"case": case_index, "source": src.name, "start": start, "duration": duration, **timing,
                         "windows": len(snapshot["windows"]), "selected": len(snapshot["selected"]),
                         "frames_scanned": snapshot["routing"]["frames_scanned"],
                         "equal_to_first_serial": snapshot == reference[case_index],
                         "source_stat_unchanged": stat_before == (src.stat().st_size, src.stat().st_mtime_ns)})
        runs.append({"index": index, "mode": label, "workers": modes[label], "cases": rows,
                     "routing_seconds": sum(r["routing_seconds"] for r in rows),
                     "passed": all(r["equal_to_first_serial"] and r["source_stat_unchanged"] for r in rows)})
        print(json.dumps({k: v for k, v in runs[-1].items() if k != "cases"}
                         | {"peak_rss_mib": max(r["peak_rss_bytes_incl_children"] for r in rows) // 2**20,
                            "cpu_percent": [round(r["mean_system_cpu_percent"] or 0) for r in rows]}), flush=True)
    medians = {m: statistics.median(r["routing_seconds"] for r in runs if r["mode"] == m) for m in dict.fromkeys(order)}
    result = {"scope": "Actual cold visual-logo routing control flow up to candidate selection; no VLM, "
                       "localization, review or export; routing cache intercepted",
              **git_state(), "workers": args.workers, "order": order, "runs": runs, "medians": medians,
              "change_percent": (medians["P"] / medians["S"] - 1) * 100 if {"S", "P"} <= set(medians) else None,
              "all_passed": all(r["passed"] for r in runs),
              "brand_memory_unchanged": digest(ROOT / "state/brand-memory.json") == brand_before,
              "production_queue_unchanged": digest(PRODUCTION_QUEUE) == queue_before}
    (out / "pipeline.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "reference_snapshots.json").write_text(json.dumps(reference, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("medians", "change_percent", "all_passed", "brand_memory_unchanged",
                                             "production_queue_unchanged")}, indent=2))
    if not (result["all_passed"] and result["brand_memory_unchanged"] and result["production_queue_unchanged"]):
        raise SystemExit("Parallel routing pipeline check failed; do not enable")


def route_once(args):
    directory = Path(args.out).resolve()
    if not directory.is_relative_to(ROOT / "reports/benchmarks"):
        raise ValueError("route-once output must be under reports/benchmarks")
    if args.low_priority:
        # Windows children (FFmpeg, pool workers) inherit BELOW_NORMAL.
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    snapshot, timing = run_pipeline_once(source(args.source), 0, None, args.workers, directory / "scan")
    (directory / "route.json").write_text(json.dumps({"timing": timing, "snapshot": snapshot}, ensure_ascii=False),
                                          encoding="utf-8")


def overlap(args):
    """OCR stage alone (A) vs OCR stage + concurrent full-film routing (O)."""
    import os
    import sys
    out = output_dir("stage-overlap-")
    reference = json.loads((ROOT / args.routing_reference).read_text(encoding="utf-8"))["0"]
    brand_before, queue_before = digest(ROOT / "state/brand-memory.json"), digest(PRODUCTION_QUEUE)
    src = source(args.source)
    stat_before = (src.stat().st_size, src.stat().st_mtime_ns)
    python = sys.executable
    runs, ocr_reference = [], None
    for index, label in enumerate(args.order):
        run_dir = out / f"run-{index}-{label}"
        text_dir = run_dir / "text"
        ocr_cmd = [python, "-m", "biliflow", "scan-text", "--input", str(src), "--report-dir", str(text_dir),
                   "--sample-every", "3.0", "--device", "cuda",
                   "--recognition-batch-size", "8", "--recognition-frame-window", str(args.window)]
        # O: normal-priority routing with --workers; L: BELOW_NORMAL routing with --low-workers.
        workers = args.low_workers if label == "L" else args.workers
        route_cmd = [python, str(Path(__file__).resolve()), "route-once", "--workers", str(workers),
                     "--source", args.source, "--out", str(run_dir / "routing"),
                     *(["--low-priority"] if label == "L" else [])]
        run_dir.mkdir(parents=True)
        (run_dir / "routing").mkdir()
        began = perf_counter()
        with ResourceSampler() as sampler, (run_dir / "ocr.log").open("w", encoding="utf-8") as ocr_log,                 (run_dir / "routing.log").open("w", encoding="utf-8") as route_log:
            ocr = subprocess.Popen(ocr_cmd, cwd=ROOT, stdout=ocr_log, stderr=subprocess.STDOUT, env=os.environ.copy())
            route = (subprocess.Popen(route_cmd, cwd=ROOT, stdout=route_log, stderr=subprocess.STDOUT,
                                      env=os.environ.copy()) if label in {"O", "L"} else None)
            try:
                finished = {}
                while len(finished) < (2 if route else 1):
                    for name, proc in (("ocr", ocr), ("routing", route)):
                        if proc is not None and name not in finished and proc.poll() is not None:
                            finished[name] = perf_counter() - began
                            if proc.returncode:
                                raise RuntimeError(f"{name} child failed with {proc.returncode}; see {run_dir}")
                    sampler._stop.wait(0.5)
            finally:
                for proc in (ocr, route):
                    if proc is not None and proc.poll() is None:
                        proc.kill()
        report = json.loads((text_dir / "text-scan.json").read_text(encoding="utf-8"))
        ocr_reference = ocr_reference or report
        row = {"index": index, "mode": label, "ocr_wall_seconds": finished["ocr"],
               "ocr_report_elapsed_seconds": report["metrics"]["elapsed_seconds"],
               "ocr_model_step_seconds": report["metrics"]["performance"]["phases"]["model_step"]["wall_seconds"],
               "ocr_equal_to_first": _normalize(report) == _normalize(ocr_reference),
               "tracks": len(report["tracks"]), **sampler.result()}
        if route:
            routed = json.loads((run_dir / "routing" / "route.json").read_text(encoding="utf-8"))
            row.update({"routing_wall_seconds": finished["routing"],
                        "routing_seconds": routed["timing"]["routing_seconds"],
                        "routing_equal_to_reference": json.loads(json.dumps(routed["snapshot"])) == reference})
        row["both_finished_seconds"] = max(finished.values())
        runs.append(row)
        print(json.dumps(row), flush=True)
    result = {"scope": "Real OCR stage CLI with and without concurrent full-film CPU routing; "
                       "no VLM, localization, review or export", **git_state(),
              "order": list(args.order), "workers": args.workers, "window": args.window, "runs": runs,
              "medians": {m: {k: statistics.median(r[k] for r in runs if r["mode"] == m)
                              for k in ("ocr_wall_seconds", "both_finished_seconds")}
                          for m in dict.fromkeys(args.order)},
              "all_equal": all(r["ocr_equal_to_first"] and r.get("routing_equal_to_reference", True) for r in runs),
              "source_stat_unchanged": stat_before == (src.stat().st_size, src.stat().st_mtime_ns),
              "brand_memory_unchanged": digest(ROOT / "state/brand-memory.json") == brand_before,
              "production_queue_unchanged": digest(PRODUCTION_QUEUE) == queue_before}
    (out / "overlap.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("medians", "all_equal", "source_stat_unchanged",
                                             "brand_memory_unchanged", "production_queue_unchanged")}, indent=2))
    if not result["all_equal"]:
        raise SystemExit("Overlap changed OCR or routing output")


def _normalize(value):
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()
                if k not in {"metrics", "created_at", "runtime", "confidence", "max_confidence"}}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("cpu")
    c.add_argument("--workers", type=int, nargs="+", default=[2, 3, 4, 5])
    p = sub.add_parser("pipeline")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--order", default="SPPS")
    p.add_argument("--full-source", help="input glob to route the whole film instead of two 10-minute excerpts")
    o = sub.add_parser("overlap")
    o.add_argument("--source", default="*Troy*")
    o.add_argument("--workers", type=int, default=4)
    o.add_argument("--window", type=int, default=4)
    o.add_argument("--order", default="AOOA")
    o.add_argument("--low-workers", type=int, default=2)
    o.add_argument("--routing-reference", required=True,
                   help="project-relative reference_snapshots.json from a full-film pipeline run")
    r = sub.add_parser("route-once")
    r.add_argument("--source", required=True)
    r.add_argument("--workers", type=int, required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--low-priority", action="store_true")
    args = parser.parse_args()
    {"cpu": cpu, "pipeline": pipeline, "overlap": overlap, "route-once": route_once}[args.command](args)


if __name__ == "__main__":
    main()
