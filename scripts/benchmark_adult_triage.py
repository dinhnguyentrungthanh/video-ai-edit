"""Reproducible 18+ triage benchmark (docs/ADULT_FALSE_ALARM_PLAN.md step 4, gates §4.1-§4.4).

  run   1. Copies Troy's adult report (scan.json, thumbnails, candidates) into
           reports/benchmarks/adult-triage-<timestamp>/ and runs verify-adult on the copy
           through scripts/run.ps1, which waits for the shared GPU slot, so it never runs
           beside another GPU job. Its wall time is the stage cost.
        2. Rebuilds the three Golden queues (Troy, Conan 20, Conan 21: exactly the queues of
           the baseline scorecard) from their existing reports at every triage level
           (off, credits, conservative, balanced) into the same directory.
        3. Scores the baseline queues and every level against the current Golden v1 and v1.1
           labels and runs the detector gate per set (compare --gate detector), with the
           baseline trials' timing for the baseline and, for the levels, a derived Troy trial
           whose timing adds the measured verify_adult stage (and R3 shot completion when the
           base trial is the R3-derived one, the default).
        4. Checks Troy rev 4 against the user's decisions: every BLUR stays in the main list,
           moved false alarms equal the pre-registered counts (+-1), the lone clear-nude shot
           test over temp/adult-fp/task-b-signals/frame-truth-spans.csv (copied into the
           evidence directory), and production verifier scores against the task C CPU
           measurement (+-0.01, gate 4.4).
        Writes summary.json and summary.md; exits 1 when a pre-registered gate fails.

  --verified PATH        reuse an existing scan-verified.json of the same adult report (no GPU)
  --simulate-verifier    use the task C CPU scores instead of the GPU verifier: checks this
                         script only; the output is marked simulated and is never evidence.
Read-only for jobs, queues, sources, labels and brand memory (checked before/after).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

BASELINE = ROOT / "reports/benchmarks/golden-baseline-v1v11-20261001-095204"
TROY_REV4_QUEUE = ROOT / ("reports/jobs/tm2-troy-2004-directors-cut-1080p-bluray-dd5-1-x-f43cf94a"
                          "-run-20260930-230655/review-queue.json")
FRAME_TRUTH = ROOT / "temp/adult-fp/task-b-signals/frame-truth-spans.csv"
CPU_SCORES = ROOT / "temp/adult-fp/task-c-verifier/safety_m_items.json"
R3_DERIVED_TRIAL = ROOT / "reports/benchmarks/r3-validation-20261001/derived-trial/troy-allgroups-full-fast-20260930-162534-r3"
TRIALS = {
    "troy": ROOT / "reports/benchmarks/troy-allgroups-full-fast-20260930-162534",
    "conan20": ROOT / "reports/benchmarks/conan20-allgroups-full-fast-20260930-073627",
    "conan21": ROOT / "reports/benchmarks/conan21-allgroups-full-golden-20260930-222720",
}
STYLES = {"troy": "live_action", "conan20": "animation", "conan21": "animation"}
LEVELS = ("off", "credits", "conservative", "balanced")
# Pre-registered before measuring (plan §4.3 req. 3 with t = 0.7, temp/adult-fp/plan/and-grid.json):
# Troy rev 4 false alarms (user KEEP) moved to the optional list, +-1.
EXPECTED_TROY_MOVED = {"off": 0, "credits": 7, "conservative": 41, "balanced": 59}
MOVED_TOLERANCE = 1
# Plan §4.1, pre-registered for labels v1 r469 + v1.1 r115 (temp/adult-triage/gate/preregistration.md):
# and-grid.json precision with the rev 4 item 1:54:29.5 (gs-T6-0004, 17 seeds, never moved) now useful.
# Golden 18+ main-list precision (all) must be at least useful/main.
EXPECTED_GOLDEN_PRECISION = {"credits": (6, 23), "conservative": (6, 16), "balanced": (6, 9)}
# Plan §4.2 (and the user's implied-nudity decision): these Troy moments stay in the main list.
MAIN_ANCHORS = {
    "T2 6:54.5 nude group in bed": 420.0,
    "gs-T5-0004 17:16.5 couple framed at the shoulders": 1040.0,
    "T6 1:53:16 sexual activity": 6800.0,
    "gs-T6-0004 1:54:29.5 woman under a sheet": 6875.0,
}
VERIFIER_TOLERANCE = 0.01  # gate 4.4
COVERAGE = 0.80


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def covered(span: tuple[float, float], intervals: list[tuple[float, float]]) -> float:
    low, high = span
    if high <= low:
        return 0.0
    total, cursor = 0.0, low
    for start, end in sorted(intervals):
        start, end = max(start, cursor), min(end, high)
        if end > start:
            total += end - start
            cursor = end
    return total / (high - low)


# --- inputs and their protection --------------------------------------------------------------

def baseline_queues() -> dict[str, Path]:
    card = read_json(BASELINE / "scorecard.json")
    queues = {}
    for key, value in card["queues"].items():
        path = ROOT / value["path"]
        if sha256(path) != value["sha256"]:
            raise RuntimeError(f"Baseline queue for {key} changed since the baseline scorecard: {path}")
        queues[key] = path
    if set(queues) != set(STYLES):
        raise RuntimeError(f"Expected the Troy, Conan 20 and Conan 21 queues, found {sorted(queues)}")
    return queues


def protected_files(queues: dict[str, Path], troy_adult: Path) -> list[Path]:
    files = [*queues.values(), TROY_REV4_QUEUE, troy_adult, ROOT / "state/brand-memory.json"]
    for queue in queues.values():
        files += [ROOT / value for value in read_json(queue)["reports"]]
    files += sorted((ROOT / "annotations/golden").glob("*/events.json"))
    return sorted({path.resolve() for path in files if path.is_file()})


def snapshot(files: list[Path]) -> dict[str, str]:
    return {relative(path): sha256(path) for path in files}


# --- verification ------------------------------------------------------------------------------

def copy_adult_report(report: Path, target: Path) -> Path:
    if target.exists():
        raise RuntimeError(f"Evidence directory already has {target}")
    target.mkdir(parents=True)
    shutil.copy2(report, target / "scan.json")
    for name in ("thumbnails", "candidates"):
        if (report.parent / name).is_dir():
            shutil.copytree(report.parent / name, target / name)
    return target / "scan.json"


def run_verifier(scan: Path, output: Path, log: Path) -> float:
    argv = [shutil.which("powershell.exe") or "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(ROOT / "scripts/run.ps1"), "verify-adult", "--report", str(scan),
            "--output", str(output), "--device", "cuda"]
    started = perf_counter()
    with log.open("w", encoding="utf-8") as handle:
        subprocess.run(argv, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT, check=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return perf_counter() - started


def simulate_verifier(scan: Path, output: Path, rev4: dict) -> None:
    """Per-interval scores cut from the task C CPU series of the rev 4 item holding each interval."""
    from biliflow.adult_verification import (VERIFIER_CALIBRATED_REVISION, VERIFIER_FRAME_SIZE,
                                             VERIFIER_MODEL_DIR, VERIFIER_PREPROCESSING,
                                             VERIFIER_SAMPLE_FPS, VERIFIER_TARGET_LABEL, interval_span)
    payload = read_json(scan)
    measured = {row["id"]: row for row in read_json(CPU_SCORES)["items"] if row["set"] == "troy_rev4"}
    owner = {}
    for item in rev4["items"]:
        if item["category"] == "adult":
            for ref in item.get("source_candidate_refs") or []:
                owner[int(ref.rsplit("#interval:", 1)[1])] = measured[item["id"]]
    for index, interval in enumerate(payload["intervals"]):
        start, duration = interval_span(interval)
        row = owner.get(index)
        values = [] if row is None else [
            value for k, value in enumerate(row["nsfw_series"])
            if start - 0.25 <= row["start"] + 0.5 * k <= start + duration + 0.25
        ]
        common = {"sample_fps": VERIFIER_SAMPLE_FPS, "frame_size": VERIFIER_FRAME_SIZE,
                  "preprocessing": VERIFIER_PREPROCESSING, "model": VERIFIER_MODEL_DIR,
                  "revision": VERIFIER_CALIBRATED_REVISION, "target_label": VERIFIER_TARGET_LABEL,
                  "decoded_span_seconds": [round(start, 3), round(start + duration, 3)], "simulated": True}
        interval["adult_verification"] = (
            {"state": "SCORED", "nsfw_max": max(values), "nsfw_frames": len(values), "nsfw_scores": values, **common}
            if values else {"state": "FAILED", "error": "no CPU frames", "nsfw_max": None, "nsfw_frames": 0, **common})
    payload["adult_verification"] = {
        "state": "COMPLETED", "simulated": True, "source_report": relative(scan),
        "source_report_sha256": sha256(scan),
        "note": "SIMULATED from temp/adult-fp/task-c-verifier/safety_m_items.json; not evidence",
    }
    write_json(output, payload)


# --- queues and scoring ------------------------------------------------------------------------

def rebuild(queues: dict[str, Path], troy_adult_verified: Path, evidence: Path) -> dict[str, dict[str, Path]]:
    from biliflow.review_workflow import build_review_queue
    built: dict[str, dict[str, Path]] = {}
    for level in LEVELS:
        built[level] = {}
        for key, path in queues.items():
            queue = read_json(path)
            reports = [ROOT / value for value in queue["reports"]]
            if key == "troy":
                reports = [troy_adult_verified if Path(value).as_posix().endswith("/adult/scan.json") else value
                           for value in reports]
                if troy_adult_verified not in reports:
                    raise RuntimeError("The Troy baseline queue has no live-action adult report")
            target = evidence / "queues" / level / f"{key}-review-queue.json"
            build_review_queue(project_root=ROOT, report_paths=reports, queue_path=target,
                               merge_gap_seconds=float(queue.get("merge_gap_seconds") or 1.0),
                               selected_detectors=(queue.get("detection_scope") or {}).get("selected"),
                               content_style=STYLES[key], adult_triage_level=level)
            built[level][key] = target
    return built


def derived_trial(base: Path, verify_seconds: float | None, evidence: Path, note: str) -> Path:
    manifest = read_json(base / "trial.json")
    stages = []
    for stage in manifest["stages"]:
        stages.append(stage)
        if stage["name"] == "adult" and verify_seconds is not None:
            stages.append({"name": "verify_adult", "state": "COMPLETED", "seconds": verify_seconds})
    target = evidence / "derived-trial" / (base.name + "-verify-adult")
    write_json(target / "trial.json", dict(
        manifest, trial=target.name, stages=stages, derived=True, derived_from=relative(base / "trial.json"),
        derived_note=note, added_verify_adult_seconds=verify_seconds,
        total_wall_seconds=(manifest.get("total_wall_seconds") or 0) + (verify_seconds or 0)))
    return target


def score(queue_paths: dict[str, Path], trials: list[Path], out: Path) -> tuple[dict, dict]:
    import evaluate_golden
    from biliflow.golden_scoring import score_sets, scorecard_markdown
    args = SimpleNamespace(set=None, labels=None)
    manifest, labels, parts = evaluate_golden._load(args)
    queues, provenance = evaluate_golden._queues(list(queue_paths.values()), manifest)
    combined, cards = score_sets(manifest, labels, parts, queues, evaluate_golden._timing(trials),
                                 evaluate_golden._approximate_suggestions(list(parts)))
    combined["queues"] = provenance
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "scorecard.json", combined)
    for name, card in cards.items():
        card["queues"] = {key: value for key, value in provenance.items() if key in parts[name]["manifest"]["sources"]}
        write_json(out / f"scorecard-{name}.json", card)
    (out / "scorecard.md").write_text(scorecard_markdown(combined, "18+ triage - " + out.name), encoding="utf-8")
    return combined, cards


def item_rows(queue: dict) -> list[tuple]:
    rows = [("items", item) for item in queue.get("items") or []]
    rows += [("advisory_items", item) for item in queue.get("advisory_items") or []]
    return sorted((kind, item.get("id"), item.get("category"), item.get("start_seconds"), item.get("end_seconds"))
                  for kind, item in rows if item.get("category") != "adult")


# --- gates ------------------------------------------------------------------------------------

def golden_gates(level: str, baseline: dict, card: dict, base_cards: dict, cards: dict) -> dict:
    from biliflow.golden_scoring import compare
    violations = []
    comparisons = {name: compare(base_cards[name], cards[name], "detector") for name in cards}
    for name, result in comparisons.items():
        violations += [f"{name}: {value}" for value in result["violations"]]
    adult = card["metrics"]["adult"]["all"]
    before_status = {label["id"]: label["status"] for label in baseline["labels"] if label["group"] == "adult"}
    lost = [f"{label['id']} {before_status.get(label['id'])} -> {label['status']}" for label in card["labels"]
            if label["group"] == "adult" and before_status.get(label["id"]) == "caught" and label["status"] != "caught"]
    if lost:
        violations.append(f"18+ labels no longer caught in the main list: {lost}")
    if adult["advisory_only"] > baseline["metrics"]["adult"]["all"]["advisory_only"]:
        violations.append(f"18+ labels caught only in the optional list: {adult['advisory_only']}")
    useful_moved = [item["item_id"] for item in card["items"]
                    if item["group"] == "adult" and item["advisory"] and item["verdict"] == "useful"]
    if useful_moved:
        violations.append(f"18+ items that hold a positive label were moved: {useful_moved}")
    t4_main = [item["item_id"] for item in card["items"]
               if item["group"] == "adult" and item["segment_id"] == "T4" and not item["advisory"]]
    if level not in ("off",) and t4_main:
        violations.append(f"T4 credits items still in the main list: {t4_main}")
    # Plan §4.1 absolute 18+ conditions: every label in the main list, none optional-only or partial.
    if adult["caught_main"] != adult["labels"] or adult["partial"] or adult["advisory_only"] \
            or adult["must_caught"] != adult["must_catch"]:
        violations.append(f"§4.1: 18+ labels not all caught in the main list: caught_main {adult['caught_main']}/"
                          f"{adult['labels']}, must {adult['must_caught']}/{adult['must_catch']}, "
                          f"partial {adult['partial']}, advisory_only {adult['advisory_only']}")
    if level in EXPECTED_GOLDEN_PRECISION:
        useful, main = EXPECTED_GOLDEN_PRECISION[level]
        if adult["useful_main"] * main < useful * adult["main_items"]:
            violations.append(f"§4.1: 18+ main precision {adult['useful_main']}/{adult['main_items']} "
                              f"below the pre-registered {useful}/{main}")
    before = baseline["metrics"]["adult"]["all"]
    return {
        "violations": violations,
        "adult_all": {key: adult.get(key) for key in ("labels", "caught_main", "advisory_only", "partial", "missed",
                                                       "must_catch", "must_caught", "main_items", "useful_main",
                                                       "false_positive_main", "precision_main", "advisory_items")},
        "adult_precision_by_split": {
            split: f"{card['metrics']['adult'][split]['useful_main']}/{card['metrics']['adult'][split]['main_items']}"
            for split in ("all", "dev", "holdout")},
        "adult_precision_main": {"baseline": before["precision_main"], "candidate": adult["precision_main"],
                                 "baseline_items": f"{before['useful_main']}/{before['main_items']}",
                                 "candidate_items": f"{adult['useful_main']}/{adult['main_items']}"},
        "compare": {name: {"violations": result["violations"], "time": result.get("time")}
                    for name, result in comparisons.items()},
    }


def troy_gates(level: str, queue: dict, rev4: dict, verified: dict, k_t: dict | None) -> dict:
    violations = []
    main = [(i["start_seconds"], i["end_seconds"]) for i in queue["items"] if i["category"] == "adult"]
    advisory = [(i["start_seconds"], i["end_seconds"]) for i in queue["advisory_items"] if i["category"] == "adult"]
    for name, second in MAIN_ANCHORS.items():
        if not any(start <= second <= end for start, end in main):
            violations.append(f"§4.2 anchor not in the main list: {name}")
    outcome = {"BLUR": {"kept": 0, "moved": 0, "split": 0}, "KEEP": {"kept": 0, "moved": 0, "split": 0}}
    seconds = {"BLUR": {"kept": 0.0, "moved": 0.0, "split": 0.0}, "KEEP": {"kept": 0.0, "moved": 0.0, "split": 0.0}}
    moved_blur = []
    for item in rev4["items"]:
        if item["category"] != "adult" or item.get("decision") not in outcome:
            continue
        span = (item["start_seconds"], item["end_seconds"])
        state = ("kept" if covered(span, main) >= COVERAGE
                 else "moved" if covered(span, advisory) >= COVERAGE else "split")
        outcome[item["decision"]][state] += 1
        seconds[item["decision"]][state] += span[1] - span[0]
        if item["decision"] == "BLUR" and state != "kept":
            moved_blur.append(span)
    if moved_blur:
        violations.append(f"§4.3 req. 1: rev 4 BLUR items left the main list: {moved_blur}")
    expected = EXPECTED_TROY_MOVED[level]
    if abs(outcome["KEEP"]["moved"] - expected) > MOVED_TOLERANCE:
        violations.append(f"§4.3 req. 3: {outcome['KEEP']['moved']} false alarms moved, expected {expected} +-1")
    frames = []
    for interval in verified["intervals"]:
        verification = interval.get("adult_verification") or {}
        if verification.get("state") == "SCORED":
            start = verification["decoded_span_seconds"][0]
            frames += [(start + index / verification["sample_fps"], value)
                       for index, value in enumerate(verification["nsfw_scores"])]
    lone = []
    with FRAME_TRUTH.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            low, high = float(row["start"]), float(row["end"])
            values = [value for second, value in frames if low - 0.25 <= second <= high + 0.25]
            nsfw = max(values) if values else None
            seeds = int(row["seeds"])
            would_move = bool(k_t) and seeds < k_t["k"] and (nsfw is None or nsfw < k_t["t"])
            lone.append({"span": f"{row['start_tc']}-{row['end_tc']}", "kind": row["kind"],
                         "nudity": row["nudity_18plus"] == "True", "seeds": seeds,
                         "verifier_max": None if nsfw is None else round(nsfw, 4), "would_move": would_move})
            if would_move and row["nudity_18plus"] == "True":
                violations.append(f"§4.3 req. 2: lone clear-nude shot {row['start_tc']} would move")
    return {"violations": violations, "rev4_outcome": outcome,
            "rev4_seconds": {decision: {state: round(value, 3) for state, value in row.items()}
                             for decision, row in seconds.items()},
            "lone_shots": lone}


def verifier_equivalence(verified: dict, rev4: dict) -> dict:
    """Gate 4.4: production NSFW max per rev 4 item vs the task C CPU measurement."""
    measured = {row["id"]: row for row in read_json(CPU_SCORES)["items"] if row["set"] == "troy_rev4"}
    rows, over = [], []
    for item in rev4["items"]:
        if item["category"] != "adult" or item["id"] not in measured:
            continue
        indices = [int(ref.rsplit("#interval:", 1)[1]) for ref in item.get("source_candidate_refs") or []]
        verifications = [verified["intervals"][index].get("adult_verification") or {} for index in indices]
        if not verifications or any(value.get("state") != "SCORED" for value in verifications):
            over.append({"item": item["id"], "reason": "not scored"})
            continue
        production = max(value["nsfw_max"] for value in verifications)
        spans = [tuple(value["decoded_span_seconds"]) for value in verifications]
        full = covered((item["start_seconds"], item["end_seconds"]), spans) >= 0.999
        difference = production - measured[item["id"]]["nsfw_max"]
        row = {"item": item["id"], "start": item["start_seconds"], "production": round(production, 4),
               "measured": measured[item["id"]]["nsfw_max"], "difference": round(difference, 4),
               "item_span_fully_decoded": full}
        rows.append(row)
        if abs(difference) > VERIFIER_TOLERANCE:
            over.append(row)
    blocking = [row for row in over if row.get("item_span_fully_decoded", True)]
    return {"items": len(rows), "max_abs_difference": max((abs(r["difference"]) for r in rows), default=None),
            "over_tolerance": over, "violations": [f"§4.4: verifier differs by > {VERIFIER_TOLERANCE}: {row}"
                                                    for row in blocking],
            "note": "Items whose span has undecoded gaps between intervals may differ legitimately "
                    "(task C decoded whole item spans); they are listed but do not fail the gate."}


# --- main --------------------------------------------------------------------------------------

def run(args) -> int:
    from biliflow.adult_verification import ADULT_TRIAGE_LEVELS
    queues = baseline_queues()
    troy_queue = read_json(queues["troy"])
    troy_adult = Path(args.troy_adult_report).resolve() if args.troy_adult_report else next(
        ROOT / value for value in troy_queue["reports"] if value.endswith("/adult/scan.json"))
    rev4 = read_json(TROY_REV4_QUEUE)
    rev4_scan = read_json(TROY_REV4_QUEUE.parent / "adult/scan.json")
    same_intervals = read_json(troy_adult)["intervals"] == rev4_scan["intervals"]
    protected = protected_files(queues, troy_adult)
    before = snapshot(protected)
    simulated = bool(args.simulate_verifier)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    evidence = ROOT / "reports/benchmarks" / (f"adult-triage-{'simulated-' if simulated else ''}{stamp}")
    evidence.mkdir(parents=True, exist_ok=False)
    print(evidence, flush=True)
    shutil.copy2(FRAME_TRUTH, evidence / FRAME_TRUTH.name)
    scan = copy_adult_report(troy_adult, evidence / "troy" / "adult")
    output = scan.with_name("scan-verified.json")
    verify_seconds = None
    if simulated:
        if not same_intervals:
            raise RuntimeError("--simulate-verifier needs the rev 4 adult intervals (task C scored those)")
        simulate_verifier(scan, output, rev4)
    elif args.verified:
        source = Path(args.verified).resolve()
        stripped = [{key: value for key, value in interval.items() if key != "adult_verification"}
                    for interval in read_json(source)["intervals"]]
        if stripped != read_json(scan)["intervals"]:
            raise RuntimeError("--verified does not hold the intervals of the chosen adult report")
        shutil.copy2(source, output)
        verify_seconds = (read_json(output).get("metrics") or {}).get("verification_elapsed_seconds")
    else:
        print("verify-adult (waits for the shared GPU slot)...", flush=True)
        verify_seconds = run_verifier(scan, output, evidence / "verify-adult.log")
        print(f"verify-adult: {verify_seconds:.1f}s", flush=True)
    verified = read_json(output)

    built = rebuild(queues, output, evidence)
    base_trial = R3_DERIVED_TRIAL if (R3_DERIVED_TRIAL / "trial.json").is_file() and not args.no_r3_timing else TRIALS["troy"]
    troy_trial = derived_trial(base_trial, verify_seconds, evidence, (
        "NOT a measured full trial: the Troy trial timing plus the verify_adult stage wall time measured by this "
        "benchmark (scripts/run.ps1 verify-adult, startup and model load included)"
        + ("; the base already adds R3 shot completion (r3-validation-20261001)" if base_trial == R3_DERIVED_TRIAL else "")
        + (". SIMULATED verifier: no stage time measured." if simulated else "")))
    baseline_card, baseline_cards = score(queues, list(TRIALS.values()), evidence / "golden" / "baseline")
    summary = {
        "evidence": relative(evidence), "simulated": simulated, "created_at": datetime.now().astimezone().isoformat(),
        "scope": "18+ group only (other groups must be unchanged); Troy (live action), Conan 20/21 (animation)",
        "troy_adult_report": relative(troy_adult), "troy_rev4_queue": relative(TROY_REV4_QUEUE),
        "troy_adult_intervals_equal_rev4": same_intervals,
        "verify_adult_seconds": verify_seconds, "timing_base_trial": relative(base_trial),
        "verification": verified.get("adult_verification"),
        "levels": {}, "violations": [],
    }
    if not simulated and same_intervals:
        summary["verifier_equivalence"] = verifier_equivalence(verified, rev4)
        summary["violations"] += summary["verifier_equivalence"]["violations"]
    off_queues = {key: read_json(path) for key, path in built["off"].items()}
    summary["control_vs_baseline_non_adult_identical"] = {
        key: item_rows(off_queues[key]) == item_rows(read_json(queues[key])) for key in queues}
    for level in LEVELS:
        level_queues = {key: read_json(path) for key, path in built[level].items()}
        trials = [TRIALS["conan20"], TRIALS["conan21"], troy_trial if level != "off" else TRIALS["troy"]]
        card, cards = score(built[level], trials, evidence / "golden" / level)
        row = golden_gates(level, baseline_card, card, baseline_cards, cards)
        row["other_groups_identical_to_off"] = {
            key: item_rows(level_queues[key]) == item_rows(off_queues[key]) for key in queues}
        for key, same in row["other_groups_identical_to_off"].items():
            if not same:
                row["violations"].append(f"§4.1: non-adult items changed in {key}")
        for key in ("conan20", "conan21"):
            moved = [i for i in level_queues[key]["advisory_items"] if i["category"] == "adult"
                     and i.get("adult_triage")]
            if moved:
                row["violations"].append(f"animation queue {key} moved 18+ items")
        row["troy_triage"] = level_queues["troy"]["adult_triage"]
        if same_intervals:
            troy = troy_gates(level, level_queues["troy"], rev4, verified, ADULT_TRIAGE_LEVELS[level]["two_signal"])
            row.update(troy)
            row["violations"] = row["violations"] + troy["violations"]
        else:
            row["rev4_checks"] = "skipped: the chosen adult report differs from rev 4 (e.g. R3 intervals)"
        if level == "off":
            # The control has no triage: its differences from the stored baseline are reported
            # (code drift since the baseline), the gates apply to the triage levels.
            row["control_notes"], row["violations"] = row["violations"], []
        summary["levels"][level] = row
        summary["violations"] += [f"{level}: {value}" for value in row["violations"]]
    after = snapshot(protected)
    summary["preserved_original_data"] = before == after
    if before != after:
        summary["violations"].append("Protected inputs changed during the benchmark: " + json.dumps(
            sorted(key for key in before if before.get(key) != after.get(key))))
    write_json(evidence / "summary.json", summary)
    lines = [f"# 18+ triage benchmark{' (SIMULATED, not evidence)' if simulated else ''}", "",
             f"Evidence: `{relative(evidence)}`; verify_adult: {verify_seconds} s; inputs preserved: "
             f"{summary['preserved_original_data']}", "",
             "| Level | Troy moved (KEEP/BLUR) | KEEP seconds moved | 18+ precision main (all · dev · holdout) "
             "| Violations |", "|---|---|---|---|---|"]
    for level, row in summary["levels"].items():
        outcome = row.get("rev4_outcome") or {}
        moved_seconds = (row.get("rev4_seconds") or {}).get("KEEP", {}).get("moved")
        split = row["adult_precision_by_split"]
        lines.append(f"| {level} | {outcome.get('KEEP', {}).get('moved')}/{outcome.get('BLUR', {}).get('moved')} | "
                     f"{moved_seconds} | {split['all']} · {split['dev']} · {split['holdout']} | "
                     f"{len(row['violations'])} |")
    lines += ["", *[f"- {value}" for value in summary["violations"]]]
    (evidence / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"evidence": relative(evidence), "violations": summary["violations"]}, ensure_ascii=False,
                     indent=2), flush=True)
    return 1 if summary["violations"] else 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    source = r.add_mutually_exclusive_group()
    source.add_argument("--verified", help="existing scan-verified.json of the same adult report (no GPU)")
    source.add_argument("--simulate-verifier", action="store_true",
                        help="use task C CPU scores; checks this script only, never evidence")
    r.add_argument("--troy-adult-report", help="adult scan.json to verify (default: the baseline Troy trial's)")
    r.add_argument("--no-r3-timing", action="store_true",
                   help="time against the plain Troy trial instead of the R3-derived one")
    args = parser.parse_args()
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
