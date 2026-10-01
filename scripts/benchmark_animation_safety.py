"""Phase H benchmark for scan-animation-safety (docs/CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md §17).

  excerpts     Stream-copy short excerpts of the Golden Set sources (bitstream
               unchanged) into reports/benchmarks/anime-safety-h/excerpts.
  equivalence  On each excerpt run the committed scanner (git HEAD, loaded from
               `git show`, working tree untouched) and the working-tree scanner in
               fp32, plus the working-tree scanner in fp16. Reports must be
               byte-identical except metrics/runtime/created_at and report paths;
               every JPEG is compared by SHA-256. fp16 differences are summarized.
  full         Whole Conan 20/21 films, stage only: HEAD fp32 vs working-tree fp32
               (H1, byte comparison) and working-tree fp16 (H2). Review queues are
               rebuilt with the same text/logo reports on both sides and compared
               item by item; the §17 gates are evaluated. Resumable (--run-dir).
Everything is written under reports/benchmarks/anime-safety-h/. Sources, jobs,
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
OUT = ROOT / "reports/benchmarks/anime-safety-h"
FFMPEG = ROOT / "tools/ffmpeg/bin/ffmpeg.exe"
FFPROBE = ROOT / "tools/ffmpeg/bin/ffprobe.exe"
MODEL = ROOT / "models/wd_vit_tagger_v3"
EXCERPTS = (("conan20-3300", "conan20", 3300.0, 150.0), ("conan21-0000", "conan21", 0.0, 150.0))
IGNORED = {"metrics", "runtime", "created_at"}


def _manifest() -> dict:
    return json.loads((ROOT / "annotations/golden/v1/segments.json").read_text(encoding="utf-8"))


def excerpts(args=None) -> list[Path]:
    manifest = _manifest()
    paths = []
    for name, source, start, duration in EXCERPTS:
        target = OUT / "excerpts" / f"{name}.mp4"
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-ss", str(start), "-t", str(duration),
                            "-i", str(ROOT / manifest["sources"][source]["path"]), "-map", "0:v:0", "-c", "copy",
                            "-an", str(target)], check=True)
        paths.append(target)
        print(f"excerpt {target.name}: {target.stat().st_size / 1e6:.1f} MB", flush=True)
    return paths


def _baseline_module():
    code = subprocess.check_output(["git", "show", "HEAD:src/biliflow/animation_safety_scanner.py"], cwd=ROOT)
    path = OUT / "baseline_animation_safety_scanner.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(code)
    spec = importlib.util.spec_from_file_location("baseline_animation_safety_scanner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(scanner, video: Path, report_dir: Path, **extra) -> float:
    started = time.perf_counter()
    payload = scanner(project_root=ROOT, input_path=video, report_dir=report_dir, model_path=MODEL,
                      ffmpeg_path=FFMPEG, ffprobe_path=FFPROBE, **extra)
    if payload["status"] != "COMPLETED":
        raise RuntimeError(f"{report_dir}: {payload['status']} {payload.get('error')}")
    return time.perf_counter() - started


def _normalized(report_dir: Path) -> dict:
    output = {}
    for path in sorted(report_dir.rglob("scan.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        for key in IGNORED:
            value.pop(key, None)
        text = json.dumps(value, sort_keys=True, ensure_ascii=False).replace(
            json.dumps(str(report_dir))[1:-1], "<REPORT_DIR>")
        output[path.relative_to(report_dir).as_posix()] = json.loads(text)
    return output


def _images(report_dir: Path) -> dict[str, str]:
    return {p.relative_to(report_dir).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(report_dir.rglob("*.jpg"))}


def _category_summary(report_dir: Path) -> dict:
    output = {}
    for category in ("adult", "gore", "violence"):
        report = json.loads((report_dir / category / "scan.json").read_text(encoding="utf-8"))
        output[category] = {"raw_hits": report["temporal_confirmation"]["raw_hit_count"],
                            "confirmed_hits": report["temporal_confirmation"]["confirmed_hit_count"],
                            "intervals": [(i["start_seconds"], i["end_seconds"]) for i in report["intervals"]]}
    return output


def equivalence(args) -> None:
    from biliflow.animation_safety_scanner import scan_animation_safety as current
    baseline = _baseline_module().scan_animation_safety
    run_dir = OUT / ("equivalence-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    results = {"git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
               "excerpts": {}}
    for video in excerpts():
        name = video.stem
        dirs = {label: run_dir / name / label for label in ("baseline-fp32", "current-fp32", "current-fp16")}
        seconds = {"baseline-fp32": _run(baseline, video, dirs["baseline-fp32"]),
                   "current-fp32": _run(current, video, dirs["current-fp32"]),
                   "current-fp16": _run(current, video, dirs["current-fp16"], precision="fp16")}
        identical_reports = _normalized(dirs["baseline-fp32"]) == _normalized(dirs["current-fp32"])
        identical_images = _images(dirs["baseline-fp32"]) == _images(dirs["current-fp32"])
        fp32, fp16 = _category_summary(dirs["current-fp32"]), _category_summary(dirs["current-fp16"])
        row = {"seconds": {k: round(v, 3) for k, v in seconds.items()},
               "h1_identical_reports": identical_reports, "h1_identical_images": identical_images,
               "images": len(_images(dirs["current-fp32"])),
               "fp16_vs_fp32": {c: {"fp32": fp32[c], "fp16": fp16[c], "same_intervals": fp32[c]["intervals"] == fp16[c]["intervals"]}
                                for c in fp32}}
        results["excerpts"][name] = row
        print(json.dumps({name: {k: row[k] for k in ("seconds", "h1_identical_reports", "h1_identical_images", "images")}}),
              flush=True)
        for category, value in row["fp16_vs_fp32"].items():
            print(f"  fp16 {category}: raw {value['fp32']['raw_hits']}->{value['fp16']['raw_hits']}, confirmed "
                  f"{value['fp32']['confirmed_hits']}->{value['fp16']['confirmed_hits']}, same intervals "
                  f"{value['same_intervals']}", flush=True)
    (run_dir / "equivalence.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(run_dir, flush=True)


# Full-film plan (§17.2). Order: same-source baseline before current for a fair same-session A/B.
FULL_RUNS = (("conan21", "baseline-fp32"), ("conan21", "current-fp32"), ("conan21", "current-fp16"),
             ("conan20", "current-fp32"), ("conan20", "current-fp16"))
CONAN20_TRIAL = ROOT / "reports/jobs/conan20-allgroups-full-fp16-20260929-193703"  # HEAD scanner, fp32
ALL_GROUPS = ["advertising", "adult", "gore", "violence"]
H1_MIN_SAVING_SECONDS = 30.0
H2_MIN_SAVING_SECONDS = 300.0


def _fixed_inputs(source: str) -> dict:
    """Text/logo reports held fixed on both sides of the review comparison (read-only)."""
    if source == "conan20":
        base = CONAN20_TRIAL
        return {"reference_safety": base / "animation-safety", "reference_queue": base / "review-queue.json",
                "text": base / "text/text-scan.json", "logo": base / "visual-logo/scan-localized.json"}
    import sqlite3
    connection = sqlite3.connect((ROOT / "state/control-center.sqlite3").as_uri() + "?mode=ro", uri=True)
    try:
        queue = connection.execute("SELECT queue_path FROM job_revisions WHERE job_id=37 AND revision=3").fetchone()[0]
    finally:
        connection.close()
    base = (ROOT / queue).parent
    return {"reference_safety": None, "reference_queue": None,
            "text": base / "text/text-scan.json", "logo": base / "visual-logo/scan-localized.json"}


def _gpu_state() -> str:
    try:
        return subprocess.check_output(["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu,clocks.sm",
                                        "--format=csv,noheader"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def full(args) -> None:
    """Five stage-only runs, byte comparison (H1), review-level fp16 comparison (H2), pre-registered gates."""
    from biliflow.animation_safety_scanner import scan_animation_safety as current
    from biliflow.golden_set import source_file
    from biliflow.review_workflow import build_review_queue
    from benchmark_full_advertising import diff_review_queues, render_review_diff, review_difference_rows

    manifest = _manifest()
    if args.run_dir:
        run_dir = Path(args.run_dir).resolve(strict=True)
        if not run_dir.is_relative_to(OUT):
            raise ValueError(f"--run-dir must be a full run under {OUT}")
    else:
        run_dir = OUT / ("full-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        run_dir.mkdir(parents=True)
    print(f"run dir: {run_dir}", flush=True)
    summary_path = run_dir / "summary.json"
    code = {"git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "git_diff_src_sha256": hashlib.sha256(subprocess.check_output(["git", "diff", "--", "src"], cwd=ROOT)).hexdigest()}
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {**code, "runs": {}}
    if {k: summary.get(k) for k in code} != code:
        raise SystemExit("Code changed since this full run started; start a new full run instead of resuming")
    invocation = len(summary.setdefault("invocations", []))
    summary["invocations"].append({"started_at": datetime.now().isoformat(timespec="seconds"), "gpu": _gpu_state()})
    baseline = None
    for source, variant in FULL_RUNS:
        key = f"{source}-{variant}"
        if summary["runs"].get(key, {}).get("status") == "COMPLETED":
            print(f"{key}: done earlier", flush=True)
            continue
        if (run_dir / key).exists():  # an interrupted attempt: keep its files, run into an empty folder
            attempt = 1
            while (run_dir / f"{key}.attempt-{attempt}").exists():
                attempt += 1
            (run_dir / key).rename(run_dir / f"{key}.attempt-{attempt}")
        video = source_file(ROOT, manifest, source)
        report_dir = run_dir / key / "animation-safety"
        if variant == "baseline-fp32":
            baseline = baseline or _baseline_module().scan_animation_safety
            scanner, extra = baseline, {}
        else:
            scanner, extra = current, ({"precision": "fp16"} if variant.endswith("fp16") else {})
        print(f"{key}: running (GPU {_gpu_state()})", flush=True)
        seconds = _run(scanner, video, report_dir, **extra)
        combined = json.loads((report_dir / "scan.json").read_text(encoding="utf-8"))
        phases = combined["metrics"]["performance"]["phases"]
        summary["runs"][key] = {"status": "COMPLETED", "seconds": round(seconds, 3),
                                "model_step_seconds": phases["model_step"]["wall_seconds"],
                                "wait_seconds": (phases.get("batch_wait") or phases.get("frame_pipe_wait"))["wall_seconds"],
                                "frames": combined["frames_scanned"], "gpu_after": _gpu_state(),
                                "invocation": invocation}
        _write_json(summary_path, summary)
        print(f"{key}: {seconds:.1f}s", flush=True)

    gates = {}
    for source in ("conan21", "conan20"):
        fixed = _fixed_inputs(source)
        fp32_dir = run_dir / f"{source}-current-fp32" / "animation-safety"
        fp16_dir = run_dir / f"{source}-current-fp16" / "animation-safety"
        reference = fixed["reference_safety"] or run_dir / f"{source}-baseline-fp32" / "animation-safety"
        h1 = {"reference": str(reference.relative_to(ROOT)),
              "reports_identical": _normalized(reference) == _normalized(fp32_dir),
              "images_identical": _images(reference) == _images(fp32_dir), "images": len(_images(fp32_dir))}
        queues = {}
        for variant, safety_dir in (("fp32", fp32_dir), ("fp16", fp16_dir)):
            queue_path = run_dir / f"{source}-review-{variant}" / "review-queue.json"
            reports = [safety_dir / c / "scan.json" for c in ("adult", "gore", "violence")] + [fixed["text"], fixed["logo"]]
            queues[variant] = build_review_queue(project_root=ROOT, report_paths=reports, queue_path=queue_path,
                                                 selected_detectors=ALL_GROUPS)
        rows, review_summary, problem = diff_review_queues(queues["fp32"], queues["fp16"])
        diff = {"review_summary": review_summary, "primary_items_unchanged": not problem,
                "differences": review_difference_rows(rows),
                "hits": {"fp32": _category_summary(fp32_dir), "fp16": _category_summary(fp16_dir)}}
        if fixed["reference_queue"] is not None:  # the same inputs rebuilt must equal the trial's own queue
            rebuilt, _, rebuilt_problem = diff_review_queues(
                json.loads(fixed["reference_queue"].read_text(encoding="utf-8")), queues["fp32"])
            diff["rebuilt_fp32_queue_equals_trial_queue"] = not rebuilt and not rebuilt_problem
        _write_json(run_dir / f"{source}-review-diff.json", diff)
        intro = (f"<p>Nguồn: <b>{source}</b> · stage an toàn FP32 vs FP16, cùng report chữ/logo.</p>"
                 f"<p>Mục chính không đổi: <b>{not problem}</b></p>")
        (run_dir / f"{source}-review-diff.html").write_text(render_review_diff(rows, review_summary, intro), encoding="utf-8")
        runs = summary["runs"]

        def saving(slow: str, fast: str) -> float | None:
            """Timing is compared only within one invocation (same session, same machine state)."""
            a, b = runs.get(f"{source}-{slow}"), runs.get(f"{source}-{fast}")
            if not a or not b or a.get("invocation") != b.get("invocation"):
                return None
            return round(a["seconds"] - b["seconds"], 1)

        saving_h2 = saving("current-fp32", "current-fp16")
        gates[source] = {"h1": h1, "h2_primary_items_unchanged": not problem, "h2_saving_seconds": saving_h2,
                         "h2_passed": (not problem) and saving_h2 is not None and saving_h2 >= H2_MIN_SAVING_SECONDS,
                         "review_summary": review_summary}
        if f"{source}-baseline-fp32" in runs:
            gates[source]["h1_saving_seconds"] = saving("baseline-fp32", "current-fp32")
    h1_saving = gates["conan21"].get("h1_saving_seconds")
    summary["gates"] = gates
    summary["h1_passed"] = all(g["h1"]["reports_identical"] and g["h1"]["images_identical"] for g in gates.values()) \
        and h1_saving is not None and h1_saving >= H1_MIN_SAVING_SECONDS
    summary["h2_passed"] = all(g["h2_passed"] for g in gates.values())
    _write_json(summary_path, summary)
    print(json.dumps({"h1_passed": summary["h1_passed"], "h2_passed": summary["h2_passed"],
                      "runs": {k: v["seconds"] for k, v in summary["runs"].items()},
                      "gates": {s: {k: v for k, v in g.items() if k != "review_summary"} for s, g in gates.items()}},
                     ensure_ascii=False, indent=2), flush=True)
    print(run_dir, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("excerpts")
    sub.add_parser("equivalence")
    f = sub.add_parser("full")
    f.add_argument("--run-dir", help="continue an interrupted full run; completed runs are kept")
    args = parser.parse_args()
    {"excerpts": excerpts, "equivalence": equivalence, "full": full}[args.command](args)


if __name__ == "__main__":
    main()
