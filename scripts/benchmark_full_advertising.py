"""User-authorized full advertising trials on production job #39's source.

  run      One complete advertising pipeline (preflight, OCR, visual logo,
           localization, review/structure audit) through the real stage
           commands, with an isolated SQLite store, a benchmark-marked report
           root and fresh routing/GroundingDINO caches. --fast-scan selects the
           Dashboard "Tăng tốc xử lý" option. No Visual AI, safety or export.
  compare  Read-only comparison of two trials: reports (runtime/confidence/cache
           telemetry excluded), preview/scanner JPEG hashes and review proposals.
Production job #39, its review queue, source file and brand memory are only read.
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
PRODUCTION_JOB_ID = 39
COLD_STAGE = ROOT / "scripts/benchmark-cold-ad-stage.ps1"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_original() -> dict:
    connection = sqlite3.connect((ROOT / "state/control-center.sqlite3").as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return dict(connection.execute("SELECT * FROM jobs WHERE id=?", (PRODUCTION_JOB_ID,)).fetchone())
    finally:
        connection.close()


def run(args) -> None:
    from biliflow.job_pipeline import validate_json_artifact
    from biliflow.job_store import JobStore, now_iso
    from biliflow.scheduler import JobScheduler

    original = read_original()
    if original["content_style"] != "live_action" or "Troy" not in original["source_path"]:
        raise RuntimeError("Authorized source no longer matches Troy job 39")
    key = f"troy-full-{args.label}-" + datetime.now().strftime("%Y%m%d-%H%M%S")
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
                "review_sha256": digest(queue), "brand_memory_sha256": digest(brand), "original_job": read_original()}

    before = snapshot()
    store = JobStore(evidence / "trial.sqlite3")
    job = store.upsert_job(job_key=key, source_path=source, source_sha256=original["source_sha256"],
                           source_size_bytes=original["source_size_bytes"],
                           source_mtime_ns=original["source_mtime_ns"], content_style="live_action",
                           profile="careful", duration_seconds=original["duration_seconds"])
    scheduler = JobScheduler(ROOT, store)
    scheduler.configure_and_queue(job["id"], content_style="live_action", profile="careful",
                                  detector_groups=["advertising"], ocr_recognition_batch_size=1,
                                  fast_scan=args.fast_scan)
    definitions = scheduler._definitions(store.get_job(job["id"]))
    manifest = {
        "trial": key, "fast_scan": args.fast_scan,
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
            if name == "preflight":
                scheduler._run_preflight(store.get_job(job["id"]))
            else:
                for index, command in enumerate(definition.commands):
                    argv = list(command.argv)
                    if any(value in argv for value in ("scan-visual-logo", "augment-grounding-regions")):
                        argv[argv.index(str(ROOT / "scripts/run.ps1"))] = str(COLD_STAGE)
                    row["commands"].append({"production": list(command.argv), "executed": argv})
                    save()
                    env = dict(os.environ, BILIFLOW_BENCHMARK_CACHE=str(cache))
                    with (evidence / f"{name}-{index}.log").open("w", encoding="utf-8") as log:
                        subprocess.run(argv, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=True,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
                    for artifact in command.expected_artifacts:
                        validate_json_artifact(artifact)
                        scheduler._register_artifact(job["id"], name, artifact)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--label", required=True, choices=("standard", "fast"))
    r.add_argument("--fast-scan", action="store_true")
    c = sub.add_parser("compare")
    c.add_argument("--baseline", required=True)
    c.add_argument("--candidate", required=True)
    args = parser.parse_args()
    if args.command == "run" and args.fast_scan != (args.label == "fast"):
        raise ValueError("--label fast requires --fast-scan and vice versa")
    {"run": run, "compare": compare}[args.command](args)


if __name__ == "__main__":
    main()
