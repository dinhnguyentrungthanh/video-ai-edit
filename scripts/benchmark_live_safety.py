"""Phase H-live benchmark (docs/CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md §18).

  equivalence  On stream-copied Troy excerpts run the committed adult (`scan`) and
               live-safety (`scan-live-safety`) scanners (git HEAD via `git show`,
               working tree untouched) and the working-tree scanners with the exact
               pipeline arguments; reports must be byte-identical except
               metrics/runtime/created_at and report paths, JPEGs by SHA-256. The
               working-tree live scan also runs with --violence-precision fp16 and
               its violence differences are summarized.
Everything is written under reports/benchmarks/live-safety-h/. Sources, jobs,
queues and caches are only read.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/benchmarks/live-safety-h"
FFMPEG = ROOT / "tools/ffmpeg/bin/ffmpeg.exe"
# (name, start, duration): an adult scene and a battle stretch of Troy.
EXCERPTS = (("troy-0400", 400.0, 150.0), ("troy-5400", 5400.0, 150.0))
IGNORED = {"metrics", "runtime", "created_at"}


def _source() -> Path:
    manifest = json.loads((ROOT / "annotations/golden/v1/segments.json").read_text(encoding="utf-8"))
    return ROOT / manifest["sources"]["troy"]["path"]


def excerpts() -> list[Path]:
    paths = []
    for name, start, duration in EXCERPTS:
        target = OUT / "excerpts" / f"{name}.mp4"
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-ss", str(start), "-t", str(duration),
                            "-i", str(_source()), "-map", "0:v:0", "-c", "copy", "-an", str(target)], check=True)
        paths.append(target)
    return paths


def _head_module(relative: str, name: str):
    code = subprocess.check_output(["git", "show", f"HEAD:{relative}"], cwd=ROOT)
    path = OUT / f"baseline_{name}.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(code)
    spec = importlib.util.spec_from_file_location(f"baseline_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _adult_kwargs(video: Path, report_dir: Path) -> dict:
    """Exactly the arguments the careful live-action pipeline passes to `scan` (job_pipeline.py)."""
    from biliflow.cli import build_parser
    a = build_parser().parse_args(["scan", "--input", str(video), "--report-dir", str(report_dir),
                                   "--sample-fps", "2.0", "--review-merge-gap-seconds", "3.0",
                                   "--sequence-context-threshold", "0.7", "--sequence-context-seconds", "8.0",
                                   "--content-style", "live_action", "--device", "cuda"])
    return dict(project_root=ROOT, input_path=a.input, report_dir=a.report_dir, model_path=a.model,
                ffmpeg_path=a.ffmpeg, ffprobe_path=a.ffprobe, sample_fps=a.sample_fps, batch_size=a.batch_size,
                top_k_candidates=a.top_k, threshold=a.threshold, merge_gap_seconds=a.merge_gap_seconds,
                padding_seconds=a.padding_seconds, device_name=a.device, content_style=a.content_style,
                temporal_window_frames=a.temporal_window_frames, temporal_minimum_hits=a.temporal_minimum_hits,
                review_merge_gap_seconds=a.review_merge_gap_seconds,
                sequence_context_threshold=a.sequence_context_threshold,
                sequence_context_seconds=a.sequence_context_seconds)


def _live_kwargs(video: Path, report_dir: Path) -> dict:
    from biliflow.cli import build_parser
    a = build_parser().parse_args(["scan-live-safety", "--input", str(video), "--report-dir", str(report_dir),
                                   "--gore-sample-fps", "2.0", "--violence-sample-fps", "8.0", "--device", "cuda"])
    return dict(project_root=ROOT, input_path=a.input, report_dir=a.report_dir, gore_model_path=a.gore_model,
                violence_model_path=a.violence_model, ffmpeg_path=a.ffmpeg, ffprobe_path=a.ffprobe,
                gore_sample_fps=a.gore_sample_fps, violence_sample_fps=a.violence_sample_fps,
                gore_batch_size=a.gore_batch_size, clip_frames=a.clip_frames, stride_frames=a.stride_frames,
                top_k_candidates=a.top_k, gore_threshold=a.gore_threshold, violence_threshold=a.violence_threshold,
                gore_merge_gap_seconds=a.merge_gap_seconds, violence_merge_gap_seconds=a.merge_gap_seconds,
                padding_seconds=a.padding_seconds, device_name=a.device)


def _timed(function, **kwargs) -> float:
    started = time.perf_counter()
    result = function(**kwargs)
    statuses = [result.get("status")] if "status" in result else [v["status"] for v in result.values()]
    if any(status != "COMPLETED" for status in statuses):
        raise RuntimeError(f"scan did not complete: {statuses}")
    return time.perf_counter() - started


def _normalized(report_dir: Path) -> dict:
    output = {}
    masks = [json.dumps(str(report_dir))[1:-1], report_dir.as_posix()]
    for path in sorted(report_dir.rglob("scan.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        for key in IGNORED:
            value.pop(key, None)
        text = json.dumps(value, sort_keys=True, ensure_ascii=False)
        for mask in masks:
            text = text.replace(mask, "<REPORT_DIR>")
        output[path.relative_to(report_dir).as_posix()] = json.loads(text)
    return output


def _images(report_dir: Path) -> dict[str, str]:
    return {p.relative_to(report_dir).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(report_dir.rglob("*.jpg"))}


def _violence_summary(report_dir: Path) -> dict:
    report = json.loads((report_dir / "violence/scan.json").read_text(encoding="utf-8"))
    return {"windows": report["windows_scored"], "hits": len(report.get("hits", [])) if "hits" in report else None,
            "intervals": [(i["start_seconds"], i["end_seconds"]) for i in report["intervals"]],
            "max": report["score_summary"].get("max")}


def equivalence(args) -> None:
    from biliflow.live_safety_scanner import scan_live_safety as live_now
    from biliflow.scanner import scan_nsfw as adult_now
    adult_head = _head_module("src/biliflow/scanner.py", "scanner").scan_nsfw
    live_head = _head_module("src/biliflow/live_safety_scanner.py", "live_safety_scanner").scan_live_safety
    run_dir = OUT / ("equivalence-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    results = {"git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
               "excerpts": {}}
    for video in excerpts():
        base = run_dir / video.stem
        seconds = {
            "adult-head": _timed(adult_head, **_adult_kwargs(video, base / "adult-head")),
            "adult-now": _timed(adult_now, **_adult_kwargs(video, base / "adult-now")),
            "live-head": _timed(live_head, **_live_kwargs(video, base / "live-head")),
            "live-now": _timed(live_now, **_live_kwargs(video, base / "live-now")),
            "live-fp16": _timed(live_now, **_live_kwargs(video, base / "live-fp16"), violence_precision="fp16"),
        }
        row = {"seconds": {k: round(v, 3) for k, v in seconds.items()}}
        for kind in ("adult", "live"):
            head, now = base / f"{kind}-head", base / f"{kind}-now"
            row[f"{kind}_identical_reports"] = _normalized(head) == _normalized(now)
            row[f"{kind}_identical_images"] = _images(head) == _images(now)
            row[f"{kind}_images"] = len(_images(now))
        fp32, fp16 = _violence_summary(base / "live-now"), _violence_summary(base / "live-fp16")
        gore_same = _normalized(base / "live-now").get("gore/scan.json") == _normalized(base / "live-fp16").get("gore/scan.json")
        row["fp16"] = {"violence_fp32": fp32, "violence_fp16": fp16, "same_intervals": fp32["intervals"] == fp16["intervals"],
                       "gore_report_identical": gore_same}
        results["excerpts"][video.stem] = row
        print(json.dumps({video.stem: row}, ensure_ascii=False), flush=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "equivalence.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(run_dir, flush=True)


# Full film. HEAD fp32 adult/live reports come from the 30/09 Troy trial (committed scanners);
# text/logo for the review rebuilds come from the 29/09 Troy advertising trial.
HEAD_TRIAL = ROOT / "reports/jobs/troy-allgroups-full-fast-20260930-075806"
ADVERTISING_TRIAL = ROOT / "reports/jobs/troy-full-fp16-20260929-173943"
ALL_GROUPS = ["advertising", "adult", "gore", "violence"]
FULL_RUNS = (("adult", "now"), ("live", "now"), ("live", "fp16"), ("confirm", "now"), ("confirm", "fp16"))
L1_MIN_SAVING_SECONDS = 30.0
L2_MIN_SAVING_SECONDS = 300.0


def _code_fingerprint() -> dict:
    return {"git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "git_diff_src_sha256": hashlib.sha256(subprocess.check_output(["git", "diff", "--", "src"], cwd=ROOT)).hexdigest()}


def _compare_dirs(head: Path, now: Path) -> dict:
    return {"reports_identical": _normalized(head) == _normalized(now), "images_identical": _images(head) == _images(now),
            "images": len(_images(now))}


def full(args) -> None:
    """Stage-only full Troy runs with the working tree, byte comparison with HEAD, review-level fp16 gate."""
    from benchmark_full_advertising import diff_review_queues, render_review_diff, review_difference_rows
    from biliflow.live_safety_scanner import scan_live_safety
    from biliflow.review_workflow import build_review_queue
    from biliflow.scanner import scan_nsfw
    from biliflow.vlm_confirmation import confirm_violence_report

    if args.run_dir:
        run_dir = Path(args.run_dir).resolve(strict=True)
        if not run_dir.is_relative_to(OUT):
            raise ValueError(f"--run-dir must be a full run under {OUT}")
    else:
        run_dir = OUT / ("full-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        run_dir.mkdir(parents=True)
    print(f"run dir: {run_dir}", flush=True)
    summary_path = run_dir / "summary.json"
    code = _code_fingerprint()
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {**code, "runs": {}}
    if {k: summary.get(k) for k in code} != code:
        raise SystemExit("Code changed since this full run started; start a new full run instead of resuming")
    source = _source()

    def save():
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    for kind, variant in FULL_RUNS:
        key = f"{kind}-{variant}"
        if summary["runs"].get(key, {}).get("status") == "COMPLETED":
            print(f"{key}: done earlier", flush=True)
            continue
        print(f"{key}: running", flush=True)
        started = time.perf_counter()
        if kind == "adult":
            target = run_dir / key
            if target.exists():
                target.rename(run_dir / f"{key}.attempt-{time.strftime('%H%M%S')}")
            scan_nsfw(**_adult_kwargs(source, target))
            report = target / "scan.json"
        elif kind == "live":
            target = run_dir / key
            if target.exists():
                target.rename(run_dir / f"{key}.attempt-{time.strftime('%H%M%S')}")
            extra = {"violence_precision": "fp16"} if variant == "fp16" else {}
            scan_live_safety(**_live_kwargs(source, target), **extra)
            report = target / "violence/scan.json"
        else:  # VLM confirmation beside the matching violence report, exactly as the pipeline runs it
            violence = run_dir / f"live-{variant}" / "violence"
            confirm_violence_report(project_root=ROOT, report_path=violence / "scan.json",
                                    output_path=violence / "scan-confirmed.json",
                                    model_path=ROOT / "models/qwen2_vl_2b_instruct", device_name="cuda")
            report = violence / "scan-confirmed.json"
        payload = json.loads(report.read_text(encoding="utf-8"))
        summary["runs"][key] = {"status": "COMPLETED", "seconds": round(time.perf_counter() - started, 3),
                                "elapsed_seconds": (payload.get("metrics") or {}).get("elapsed_seconds"),
                                "phases": {k: round(v["wall_seconds"], 1) for k, v in
                                           ((payload.get("metrics") or {}).get("performance") or {}).get("phases", {}).items()}}
        save()
        print(f"{key}: {summary['runs'][key]['seconds']:.1f}s", flush=True)

    head_adult = json.loads((HEAD_TRIAL / "adult/scan.json").read_text(encoding="utf-8"))
    head_live = json.loads((HEAD_TRIAL / "violence/scan.json").read_text(encoding="utf-8"))
    runs = summary["runs"]
    l1 = {"adult": _compare_dirs(HEAD_TRIAL / "adult", run_dir / "adult-now"),
          "gore": _compare_dirs(HEAD_TRIAL / "gore", run_dir / "live-now/gore"),
          "violence": _compare_dirs(HEAD_TRIAL / "violence", run_dir / "live-now/violence"),
          # scanner-internal elapsed times; HEAD comes from the 30/09 session (cross-session comparison)
          "adult_saving_seconds": round(head_adult["metrics"]["elapsed_seconds"] - runs["adult-now"]["elapsed_seconds"], 1),
          "live_saving_seconds": round(head_live["metrics"]["elapsed_seconds"] - runs["live-now"]["elapsed_seconds"], 1)}
    queues = {}
    for variant in ("now", "fp16"):
        live = run_dir / f"live-{variant}"
        reports = [run_dir / "adult-now/scan.json", live / "gore/scan.json", live / "violence/scan-confirmed.json",
                   ADVERTISING_TRIAL / "text/text-scan.json", ADVERTISING_TRIAL / "visual-logo/scan-localized.json"]
        queues[variant] = build_review_queue(project_root=ROOT, report_paths=reports,
                                             queue_path=run_dir / f"review-{variant}/review-queue.json",
                                             selected_detectors=ALL_GROUPS)
    rows, review_summary, problem = diff_review_queues(queues["now"], queues["fp16"])
    (run_dir / "review-diff.json").write_text(json.dumps(
        {"review_summary": review_summary, "primary_items_unchanged": not problem,
         "differences": review_difference_rows(rows)}, ensure_ascii=False, indent=2), encoding="utf-8")
    intro = ("<p>Troy, stage an toàn phim người đóng: bạo lực FP32 vs FP16 (cùng adult, chữ, logo; "
             f"mỗi bên có VLM xác nhận riêng).</p><p>Mục chính không đổi: <b>{not problem}</b></p>")
    (run_dir / "review-diff.html").write_text(render_review_diff(rows, review_summary, intro), encoding="utf-8")
    fp16_saving = round(runs["live-now"]["seconds"] - runs["live-fp16"]["seconds"], 1)
    summary["gates"] = {
        "l1": l1,
        "l1_passed": all(l1[k]["reports_identical"] and l1[k]["images_identical"] for k in ("adult", "gore", "violence"))
                     and l1["adult_saving_seconds"] >= L1_MIN_SAVING_SECONDS
                     and l1["live_saving_seconds"] >= L1_MIN_SAVING_SECONDS,
        "l2_primary_items_unchanged": not problem, "l2_review_summary": review_summary,
        "l2_saving_seconds": fp16_saving,
        "l2_passed": (not problem) and fp16_saving >= L2_MIN_SAVING_SECONDS,
        "confirm_seconds": {v: runs[f"confirm-{v}"]["seconds"] for v in ("now", "fp16")},
    }
    summary["completed_at"] = datetime.now().isoformat(timespec="seconds")
    save()
    print(json.dumps(summary["gates"], ensure_ascii=False, indent=2), flush=True)
    print("FULL RUN DONE", flush=True)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("equivalence")
    f = sub.add_parser("full")
    f.add_argument("--run-dir", help="continue an interrupted full run; completed runs are kept")
    args = parser.parse_args()
    {"equivalence": equivalence, "full": full}[args.command](args)


if __name__ == "__main__":
    main()
