"""User-authorized full advertising trials on an authorized production job's source.

  run      One complete advertising pipeline (preflight, OCR, visual logo,
           localization, review/structure audit) through the real stage
           commands, with an isolated SQLite store, a benchmark-marked report
           root and fresh routing/GroundingDINO caches. --fast-scan selects the
           Dashboard "Tăng tốc xử lý" option. --prewarm-overlap (E3b prototype)
           runs the logo stage's own routing, NVDEC-decoded, concurrently with
           the OCR stage so the logo stage reads a warm routing cache.
           No Visual AI, safety or export.
  compare  Read-only comparison of two trials: reports (runtime/confidence/cache
           telemetry excluded), preview/scanner JPEG hashes and review proposals.
The production job (--job-id, default 39 Troy; 38 Conan Movie 20; 37 Conan Movie 21), its review queue,
source file and brand memory are only read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
# Authorized production jobs (read-only): 39 Troy (live action), 38 Conan Movie 20 and
# 37 Conan Movie 21 (animation; Golden Set v1 sources, docs/QUALITY_PLAN.md).
AUTHORIZED_JOBS = {39: ("troy", "Troy", "live_action"), 38: ("conan20", "Movie 20", "animation"),
                   37: ("conan21", "Movie 21", "animation")}
COLD_STAGE = ROOT / "scripts/benchmark-cold-ad-stage.ps1"
TEXT_STAGE = ROOT / "scripts/benchmark-text-stage.ps1"
PREWARM = ROOT / "scripts/benchmark_prewarm_routing.py"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_original(job_id: int) -> dict:
    connection = sqlite3.connect((ROOT / "state/control-center.sqlite3").as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return dict(connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
    finally:
        connection.close()


def run(args) -> None:
    from biliflow.job_pipeline import validate_json_artifact
    from biliflow.job_store import JobStore, now_iso
    from biliflow.scheduler import JobScheduler

    if args.job_id not in AUTHORIZED_JOBS:
        raise ValueError(f"Job {args.job_id} is not authorized for full trials")
    slug, marker, style = AUTHORIZED_JOBS[args.job_id]
    original = read_original(args.job_id)
    if original["content_style"] != style or marker not in original["source_path"]:
        raise RuntimeError(f"Authorized source no longer matches job {args.job_id}")
    detectors = sorted(set(args.detectors))
    scope = "" if detectors == ["advertising"] else "-allgroups" if len(detectors) == 4 else "-" + "-".join(detectors)
    key = f"{slug}{scope}-full-{args.label}-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    evidence = ROOT / "reports/benchmarks" / key
    evidence.mkdir(parents=True, exist_ok=False)
    report_root = ROOT / "reports/jobs" / key
    report_root.mkdir(parents=True, exist_ok=False)
    # Marker before any queue exists: the Dashboard never auto-imports this trial.
    (report_root / ".biliflow-benchmark").write_text("Isolated trial; never auto-import", encoding="utf-8")
    cache = evidence / "cold-cache"
    source = Path(original["source_path"])
    queue = ROOT / original["active_queue_path"]
    brand = ROOT / "state/brand-memory.json"

    def snapshot():
        return {"source_size": source.stat().st_size, "source_mtime_ns": source.stat().st_mtime_ns,
                "review_sha256": digest(queue), "brand_memory_sha256": digest(brand),
                "original_job": read_original(args.job_id)}

    before = snapshot()
    store = JobStore(evidence / "trial.sqlite3")
    job = store.upsert_job(job_key=key, source_path=source, source_sha256=original["source_sha256"],
                           source_size_bytes=original["source_size_bytes"],
                           source_mtime_ns=original["source_mtime_ns"], content_style=style,
                           profile="careful", duration_seconds=original["duration_seconds"])
    scheduler = JobScheduler(ROOT, store)
    scheduler.configure_and_queue(job["id"], content_style=style, profile="careful",
                                  detector_groups=detectors, ocr_recognition_batch_size=1,
                                  fast_scan=args.fast_scan)
    definitions = scheduler._definitions(store.get_job(job["id"]))
    manifest = {
        "trial": key, "fast_scan": args.fast_scan, "prewarm_overlap": args.prewarm_overlap,
        "detect_fp16": args.detect_fp16, "detector_groups": detectors,
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "git_diff_src_sha256": hashlib.sha256(subprocess.check_output(["git", "diff", "--", "src"], cwd=ROOT)).hexdigest(),
        "scope": "advertising only; no Visual AI, safety detectors or export",
        "source_sha256": original["source_sha256"], "before": before,
        "cache_policy": ("No stage-result reuse; routing and GroundingDINO caches redirected to a new empty "
                         "namespace under this evidence directory. No production cache is read, written or "
                         "deleted. OS/model-file caching uncontrolled."),
        "stages": [], "status": "RUNNING", "started_at": now_iso(),
    }

    def save():
        (evidence / "trial.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    save()
    print(evidence, flush=True)
    total_start = perf_counter()
    try:
        for name, definition in definitions.items():
            print("START " + name, flush=True)
            store.update_job(job["id"], state=definition.job_state, current_stage=name)
            store.update_stage(job["id"], name, state="RUNNING", started_at=now_iso())
            row = {"name": name, "state": "RUNNING", "started_at": now_iso(), "commands": []}
            manifest["stages"].append(row)
            save()
            started = perf_counter()
            prewarm = None
            if name == "text" and args.prewarm_overlap:
                logo_argv = list(definitions["visual_logo"].commands[0].argv)
                cli_args = logo_argv[logo_argv.index(str(ROOT / "scripts/run.ps1")) + 1:]
                prewarm_argv = [sys.executable, str(PREWARM), *cli_args, "--decode", "nvdec"]
                row["prewarm"] = {"argv": prewarm_argv}
                prewarm_log = (evidence / "prewarm.log").open("w", encoding="utf-8")
                prewarm = subprocess.Popen(prewarm_argv, cwd=ROOT, stdout=prewarm_log, stderr=subprocess.STDOUT,
                                           env=dict(os.environ, BILIFLOW_BENCHMARK_CACHE=str(cache)),
                                           creationflags=subprocess.CREATE_NO_WINDOW)
            if name == "preflight":
                scheduler._run_preflight(store.get_job(job["id"]))
            else:
                for index, command in enumerate(definition.commands):
                    argv = list(command.argv)
                    if any(value in argv for value in ("scan-visual-logo", "augment-grounding-regions")):
                        argv[argv.index(str(ROOT / "scripts/run.ps1"))] = str(COLD_STAGE)
                    elif "--prewarm-logo-routing" in argv:
                        # Integrated prewarm child must also use the benchmark cache namespace.
                        argv[argv.index(str(ROOT / "scripts/run.ps1"))] = str(TEXT_STAGE)
                    if name == "text" and args.detect_fp16:
                        argv += ["--detect-precision", "fp16"]  # G1 trial only; pipeline unchanged
                    row["commands"].append({"production": list(command.argv), "executed": argv})
                    save()
                    env = dict(os.environ, BILIFLOW_BENCHMARK_CACHE=str(cache))
                    with (evidence / f"{name}-{index}.log").open("w", encoding="utf-8") as log:
                        subprocess.run(argv, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=True,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
                    for artifact in command.expected_artifacts:
                        validate_json_artifact(artifact)
                        scheduler._register_artifact(job["id"], name, artifact)
            if prewarm is not None:
                row["prewarm"]["ocr_command_seconds"] = perf_counter() - started
                code = prewarm.wait()
                prewarm_log.close()
                row["prewarm"].update(exit_code=code, finished_after_seconds=perf_counter() - started)
                if code:
                    raise RuntimeError(f"Routing prewarm failed with exit code {code}; see prewarm.log")
            if name == "visual_logo" and (args.prewarm_overlap or args.fast_scan):
                logo_report = json.loads((report_root / "visual-logo/scan.json").read_text(encoding="utf-8"))
                row["routing_cache_hit"] = logo_report["routing_cache"]["hit"]
                if not row["routing_cache_hit"]:
                    raise RuntimeError("Logo stage did not reuse the prewarmed routing cache")
            store.update_stage(job["id"], name, state="COMPLETED", progress=1, completed_at=now_iso())
            scheduler._after_success(job["id"], name, definition)
            row.update(state="COMPLETED", seconds=perf_counter() - started, completed_at=now_iso())
            save()
            print(f"DONE {name}: {row['seconds']:.3f}s", flush=True)
        manifest["status"] = "WAITING_REVIEW"
    except Exception as error:
        manifest.update(status="FAILED", error=str(error))
        raise
    finally:
        manifest.update(after=snapshot(), total_wall_seconds=perf_counter() - total_start, completed_at=now_iso())
        manifest["preserved_original_data"] = manifest["before"] == manifest["after"]
        save()
        store.close()
    print(json.dumps({"trial": key, "total_wall_seconds": manifest["total_wall_seconds"],
                      "stages": {s["name"]: round(s["seconds"], 3) for s in manifest["stages"]},
                      "preserved_original_data": manifest["preserved_original_data"]}), flush=True)


IGNORED_KEYS = {"metrics", "runtime", "created_at", "updated_at", "confidence", "max_confidence", "routing_cache"}
# Localizer telemetry already documented as run-dependent in earlier full trials.
LOCALIZER_TELEMETRY = {
    "/grounding_region_summary/elapsed_seconds", "/grounding_region_summary/inference/cache_hits",
    "/grounding_region_summary/inference/computed", "/grounding_region_summary/inference/elapsed_seconds",
    "/grounding_region_summary/inference/peak_cuda_bytes", "/region_localization_summary/elapsed_seconds",
}


def compare(args) -> None:
    from benchmark_ocr_contiguous import queue_projection
    bases = [ROOT / "reports/jobs" / key for key in (args.baseline, args.candidate)]
    for key in (args.baseline, args.candidate):
        if Path(key).name != key:
            raise ValueError("Trial keys must be directory names")

    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))

    def norm(value):
        if isinstance(value, dict):
            return {k: norm(v) for k, v in value.items() if k not in IGNORED_KEYS}
        if isinstance(value, list):
            return [norm(v) for v in value]
        if isinstance(value, str):
            for base in bases:
                for form in (str(base), base.as_posix(), base.relative_to(ROOT).as_posix()):
                    value = value.replace(form, "<REPORT>")
            return value
        return value

    def differences(a, b, path=""):
        if type(a) is not type(b):
            return [{"path": path, "baseline": a, "candidate": b}]
        if isinstance(a, dict):
            rows = []
            for k in sorted(a.keys() | b.keys()):
                if k not in a or k not in b:
                    rows.append({"path": f"{path}/{k}", "baseline": a.get(k), "candidate": b.get(k)})
                else:
                    rows.extend(differences(a[k], b[k], f"{path}/{k}"))
            return rows
        if isinstance(a, list):
            if len(a) != len(b):
                return [{"path": path, "baseline_count": len(a), "candidate_count": len(b)}]
            return [d for i, (x, y) in enumerate(zip(a, b)) for d in differences(x, y, f"{path}/{i}")]
        return [] if a == b else [{"path": path, "baseline": a, "candidate": b}]

    def jpegs(base):
        return {p.relative_to(base).as_posix(): digest(p) for p in base.rglob("*.jpg")}

    out = ROOT / "reports/benchmarks" / args.candidate
    result = {"baseline": args.baseline, "candidate": args.candidate, "reports": {}}
    for relative in ("text/text-scan.json", "visual-logo/scan.json", "visual-logo/scan-localized.json"):
        a, b = read(bases[0] / relative), read(bases[1] / relative)
        diff = differences(norm(a), norm(b))
        (out / (relative.replace("/", "-") + "-differences.json")).write_text(
            json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8")
        allowed = LOCALIZER_TELEMETRY if relative.endswith("localized.json") else set()
        result["reports"][relative] = {
            "difference_count": len(diff),
            "equal_excluding_documented_telemetry": all(row["path"] in allowed for row in diff),
            "frames_scanned": [a.get("frames_scanned"), b.get("frames_scanned")]}
    images = [jpegs(base) for base in bases]
    result["jpeg"] = {"baseline_count": len(images[0]), "candidate_count": len(images[1]),
                      "equal": images[0] == images[1]}
    queues = [read(base / "review-queue.json") for base in bases]

    def proposals(queue):
        projection = queue_projection(queue)
        for items in projection.values():
            for item in items:
                item.pop("decision", None)
                item.pop("decision_region_source_pixels", None)
        return projection
    result["review"] = {"proposals_equal": proposals(queues[0]) == proposals(queues[1]),
                        "counts": [{k: len(q[k]) for k in ("items", "advisory_items")} for q in queues],
                        "candidate_coverage": queues[1].get("candidate_coverage")}
    audit = read(bases[1] / "structure-audit.json")
    result["structure_audit"] = {k: audit.get(k) for k in ("result", "summary")}
    result["passed"] = (all(r["equal_excluding_documented_telemetry"] for r in result["reports"].values())
                        and result["jpeg"]["equal"] and result["review"]["proposals_equal"])
    (out / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2)[:6000])
    if not result["passed"]:
        raise SystemExit("Fast scan output differs from the standard trial")


REVIEW_FIELDS = ("category", "review_kind", "candidate_type", "start_seconds", "end_seconds", "suggested_decision",
                 "suggested_region_source_pixels", "labels", "priority", "detected_intervals", "region_classification")


def review_projection(item):
    return {k: item.get(k) for k in REVIEW_FIELDS}


def diff_review_queues(baseline: dict, candidate: dict) -> tuple[list[dict], dict, bool]:
    """Match primary and advisory items of two review queues (FP32 baseline vs FP16 candidate).

    Returns (rows, summary, primary_problem); rows hold unmatched or changed items only.
    """
    def overlap(a, b):
        start, end = max(a["start_seconds"], b["start_seconds"]), min(a["end_seconds"], b["end_seconds"])
        union = max(a["end_seconds"], b["end_seconds"]) - min(a["start_seconds"], b["start_seconds"])
        return max(0.0, end - start) / union if union > 0 else float(a["start_seconds"] == b["start_seconds"])

    def region_delta(a, b):
        ra, rb = a.get("suggested_region_source_pixels"), b.get("suggested_region_source_pixels")
        if not ra or not rb:
            return None if ra == rb else "only one side has a region"
        return max(abs(ra[k] - rb[k]) for k in ("x", "y", "width", "height"))

    rows, summary = [], {}
    for group in ("items", "advisory_items"):
        left = list(baseline.get(group, []))
        right = list(candidate.get(group, []))
        unmatched_right = list(right)
        identical = 0
        for item in left:
            exact = next((c for c in unmatched_right if review_projection(c) == review_projection(item)), None)
            if exact is not None:
                unmatched_right.remove(exact)
                identical += 1
                continue
            candidates = [c for c in unmatched_right if c["category"] == item["category"] and overlap(item, c) > 0]
            best = max(candidates, key=lambda c: overlap(item, c), default=None)
            if best is None:
                rows.append({"group": group, "kind": "chỉ có ở FP32 (mất khi dùng FP16)",
                             "baseline": item, "candidate": None})
                continue
            unmatched_right.remove(best)
            changes = {k: [item.get(k), best.get(k)] for k in REVIEW_FIELDS if item.get(k) != best.get(k)}
            rows.append({"group": group, "kind": "khác chi tiết", "baseline": item, "candidate": best,
                         "changes": changes, "region_delta_px": region_delta(item, best),
                         "decision_changed": item.get("suggested_decision") != best.get("suggested_decision")})
        for item in unmatched_right:
            rows.append({"group": group, "kind": "chỉ có ở FP16 (mục mới)", "baseline": None, "candidate": item})
        group_rows = [r for r in rows if r["group"] == group]
        summary[group] = {"baseline": len(left), "candidate": len(right), "identical": identical,
                          "changed": sum(1 for r in group_rows if r["kind"] == "khác chi tiết"),
                          "only_fp32": sum(1 for r in group_rows if r["candidate"] is None),
                          "only_fp16": sum(1 for r in group_rows if r["baseline"] is None)}
    primary_problem = any(r["group"] == "items" and (r["candidate"] is None or r["baseline"] is None
                                                     or r.get("decision_changed")) for r in rows)
    return rows, summary, primary_problem


def review_difference_rows(rows: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if k not in {"baseline", "candidate"}}
            | {"baseline": review_projection(r["baseline"]) if r["baseline"] else None,
               "candidate": review_projection(r["candidate"]) if r["candidate"] else None} for r in rows]


def render_review_diff(rows: list[dict], summary: dict, intro_html: str) -> str:
    """Vietnamese HTML of review-level differences with each side's preview images."""
    import base64
    import html as html_lib

    def images(item, limit=2):
        tags = []
        for path in (item or {}).get("preview_images", [])[:limit]:
            file = ROOT / path
            if file.is_file():
                data = base64.b64encode(file.read_bytes()).decode("ascii")
                tags.append(f'<img src="data:image/jpeg;base64,{data}" alt="">')
        return "".join(tags) or "<em>(không có ảnh)</em>"

    def describe(item):
        if not item:
            return "—"
        region = item.get("suggested_region_source_pixels") or {}
        return html_lib.escape(f"{item.get('category')} · {item.get('start_seconds')}–{item.get('end_seconds')} s · "
                               f"{item.get('suggested_decision')} · vùng {region}")

    cards = []
    for r in rows:
        extra = ""
        if r.get("changes"):
            extra = "<ul>" + "".join(
                f"<li><b>{html_lib.escape(k)}</b>: {html_lib.escape(str(v[0]))} → {html_lib.escape(str(v[1]))}</li>"
                for k, v in r["changes"].items()) + "</ul>"
        scope = "mục chính" if r["group"] == "items" else "advisory"
        cards.append(
            f'<section><h3>{html_lib.escape(r["kind"])} · {scope}</h3><div class="cols">'
            f'<div><h4>FP32 (hiện tại)</h4><p>{describe(r["baseline"])}</p>{images(r["baseline"])}</div>'
            f'<div><h4>FP16</h4><p>{describe(r["candidate"])}</p>{images(r["candidate"])}</div></div>{extra}</section>')
    style = ("body{font-family:system-ui,sans-serif;margin:24px;max-width:1200px}"
             "section{border:1px solid #ccc;border-radius:8px;padding:12px;margin:12px 0}"
             ".cols{display:grid;grid-template-columns:1fr 1fr;gap:16px}"
             "img{max-width:100%;margin:4px 0;border:1px solid #ddd}")
    body = "".join(cards) or "<p>Không có khác biệt nào ở mức review.</p>"
    return ('<!doctype html><html lang="vi"><head><meta charset="utf-8"><title>Khác biệt FP16 và FP32</title>'
            f"<style>{style}</style></head><body><h1>Khác biệt kết quả review: FP16 so với FP32</h1>"
            f"{intro_html}<p>Tóm tắt: {html_lib.escape(json.dumps(summary, ensure_ascii=False))}</p>{body}</body></html>")


def review_diff(args) -> None:
    """G1: logo branch must be identical; list every OCR/review difference for the user."""
    bases = {"baseline": ROOT / "reports/jobs" / args.baseline, "candidate": ROOT / "reports/jobs" / args.candidate}
    for key in (args.baseline, args.candidate):
        if Path(key).name != key:
            raise ValueError("Trial keys must be directory names")
    out = ROOT / "reports/benchmarks" / args.candidate

    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))

    def norm(value, base):
        if isinstance(value, dict):
            return {k: norm(v, base) for k, v in value.items() if k not in IGNORED_KEYS}
        if isinstance(value, list):
            return [norm(v, base) for v in value]
        if isinstance(value, str):
            for form in (str(base), base.as_posix(), base.relative_to(ROOT).as_posix()):
                value = value.replace(form, "<REPORT>")
            return value
        return value

    def logo_jpegs(base):
        return {p.relative_to(base).as_posix(): digest(p) for p in (base / "visual-logo").rglob("*.jpg")}

    logo = {}
    for relative in ("visual-logo/scan.json", "visual-logo/scan-localized.json"):
        a = norm(read(bases["baseline"] / relative), bases["baseline"])
        b = norm(read(bases["candidate"] / relative), bases["candidate"])
        for side in (a, b):  # documented localizer timing telemetry only
            for section in ("grounding_region_summary", "region_localization_summary"):
                part = side.get(section, {})
                part.pop("elapsed_seconds", None)
                part.get("inference", {}).pop("elapsed_seconds", None)
                part.get("inference", {}).pop("peak_cuda_bytes", None)
        logo[relative] = a == b
    logo["jpegs_identical"] = logo_jpegs(bases["baseline"]) == logo_jpegs(bases["candidate"])
    safety = {}
    for folder in ("animation-safety", "adult", "live-safety", "gore", "violence"):
        for report in sorted((bases["baseline"] / folder).rglob("*.json")) if (bases["baseline"] / folder).is_dir() else []:
            relative = report.relative_to(bases["baseline"]).as_posix()
            other = bases["candidate"] / relative
            safety[relative] = other.is_file() and norm(read(report), bases["baseline"]) == norm(read(other), bases["candidate"])
        folder_images = [base / folder for base in bases.values()]
        if folder_images[0].is_dir():
            hashes = [{q.relative_to(d).as_posix(): digest(q) for q in d.rglob("*.jpg")} if d.is_dir() else {}
                      for d in folder_images]
            safety[f"{folder}/*.jpg"] = hashes[0] == hashes[1]

    rows, summary, primary_problem = diff_review_queues(
        read(bases["baseline"] / "review-queue.json"), read(bases["candidate"] / "review-queue.json"))
    texts = {side: read(base / "text/text-scan.json") for side, base in bases.items()}
    text_summary = {k: [texts["baseline"].get(k), texts["candidate"].get(k)]
                    for k in ("frames_scanned", "tracks_before_limit", "review_candidate_count", "routing_counts")}
    result = {"baseline": args.baseline, "candidate": args.candidate, "logo_branch_identical": logo,
              "safety_reports_identical": safety,
              "review_summary": summary, "text_summary": text_summary,
              "primary_items_unchanged": not primary_problem,
              "differences": review_difference_rows(rows)}
    (out / "review-diff.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    intro = (f"<p>Lượt FP32: <code>{args.baseline}</code> · Lượt FP16: <code>{args.candidate}</code></p>"
             f"<p>Nhánh logo giống hệt: <b>{all(logo.values())}</b> · Mục chính không đổi: <b>{not primary_problem}</b></p>")
    (out / "review-diff.html").write_text(render_review_diff(rows, summary, intro), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("logo_branch_identical", "safety_reports_identical",
                                             "review_summary", "text_summary",
                                             "primary_items_unchanged")}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--label", required=True, choices=("standard", "fast", "overlap", "fp16", "golden"))
    r.add_argument("--detect-fp16", action="store_true")
    r.add_argument("--job-id", type=int, default=39, choices=sorted(AUTHORIZED_JOBS))
    r.add_argument("--detectors", nargs="+", default=["advertising"],
                   choices=("advertising", "adult", "gore", "violence"))
    r.add_argument("--fast-scan", action="store_true")
    r.add_argument("--prewarm-overlap", action="store_true")
    d = sub.add_parser("review-diff")
    d.add_argument("--baseline", required=True)
    d.add_argument("--candidate", required=True)
    c = sub.add_parser("compare")
    c.add_argument("--baseline", required=True)
    c.add_argument("--candidate", required=True)
    args = parser.parse_args()
    if args.command == "run":
        expected = {"standard": (False, False, False), "fast": (True, False, False),
                    "overlap": (True, True, False), "fp16": (True, False, True),
                    "golden": (True, False, False)}[args.label]
        if (args.fast_scan, args.prewarm_overlap, args.detect_fp16) != expected:
            raise ValueError("--label must match --fast-scan/--prewarm-overlap/--detect-fp16")
    {"run": run, "compare": compare, "review-diff": review_diff}[args.command](args)


if __name__ == "__main__":
    main()
