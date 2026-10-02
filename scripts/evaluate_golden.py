"""Golden Set evaluation (docs/QUALITY_PLAN.md §5, §12.6). Read-only for jobs, queues, sources and labels.

  score    Score review queues (one per source, matched by SHA-256) against the
           segments marked "complete" in annotations/golden/<set>/events.json.
           --set (repeatable; default: every set directory with a segments.json)
           scores several sets together; --labels [SET=]path replaces one set's
           labels (a bare path means v1).
  run      Isolated full-film trials through scripts/benchmark_full_advertising.py
           (all detector groups on every film unless --troy-detectors narrows Troy,
           fast_scan as in production), after re-hashing each source once; then score.
  compare  Label-level differences between two scorecards. --gate detector|speed
           exits with code 1 when a pre-registered gate (§7) is violated.
Outputs go to reports/benchmarks/golden-<timestamp>/: scorecard.json (all chosen sets, with a
"sets" block), scorecard-<set>.json per set (same pin and fingerprint as a card of that set
alone, so gate on these), scorecard.md and frames/.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_ROOT = ROOT / "annotations/golden"
BENCHMARKS = ROOT / "reports/benchmarks"
FFMPEG = ROOT / "tools/ffmpeg/bin/ffmpeg.exe"
HARNESS = ROOT / "scripts/benchmark_full_advertising.py"
MAX_FALSE_POSITIVE_FRAMES = 60
RUNS = (("troy", 39), ("conan20", 38), ("conan21", 37))
ALL_GROUPS = ("advertising", "adult", "gore", "violence")


def _set_names(args) -> list[str]:
    """--set values, or every set directory under GOLDEN_ROOT that has a manifest."""
    return list(getattr(args, "set", None) or sorted(p.parent.name for p in GOLDEN_ROOT.glob("*/segments.json")))


def _label_overrides(values: list[str] | None) -> dict[str, Path]:
    """--labels values: SET=path for one set, or a bare path for v1 (backward compatible)."""
    from biliflow.golden_set import KNOWN_SETS
    overrides: dict[str, Path] = {}
    for value in values or []:
        name, separator, path = value.partition("=")
        name, path = (name, path) if separator and name in KNOWN_SETS else ("v1", value)
        if name in overrides:
            raise ValueError(f"--labels given twice for set {name}")
        overrides[name] = Path(path)
    return overrides


def _load(args):
    """(combined manifest, combined labels, parts) of the chosen sets; see load_golden_sets."""
    from biliflow.golden_set import load_golden_sets
    return load_golden_sets(GOLDEN_ROOT, _set_names(args), _label_overrides(getattr(args, "labels", None)))


def _queues(paths: list[Path], manifest: dict) -> tuple[dict, dict]:
    from biliflow.golden_set import file_sha256, read_json
    queues, provenance = {}, {}
    for path in paths:
        queue = read_json(path)
        sha = (queue.get("source") or {}).get("sha256")
        key = next((k for k, s in manifest["sources"].items() if s["sha256"] == sha), None)
        if key is None:
            raise ValueError(f"{path} is not built from a Golden Set source")
        if key in queues:
            raise ValueError(f"Two queues given for source {key}")
        queues[key] = queue
        provenance[key] = {"path": path.resolve().relative_to(ROOT).as_posix(), "sha256": file_sha256(path),
                           "detection_scope": queue.get("detection_scope")}
        studio = queue.get("studio_logo_memory")
        if isinstance(studio, dict):
            provenance[key]["studio_logo_memory"] = studio
            if studio.get("moved_to_candidates"):
                print(f"WARNING: {path} moved {studio['moved_to_candidates']} card(s) with the user's studio-logo "
                      "memory; this score depends on state/studio-logo-memory.json", flush=True)
    return queues, provenance


def _timing(trials: list[Path]) -> dict:
    if not trials:
        return {}
    rows = {}
    for trial in trials:
        manifest = json.loads((trial / "trial.json").read_text(encoding="utf-8"))
        rows[trial.name] = {"total_wall_seconds": manifest.get("total_wall_seconds"), "status": manifest.get("status"),
                            "git_head": manifest.get("git_head"), "detector_groups": manifest.get("detector_groups"),
                            "stages": {s["name"]: s.get("seconds") for s in manifest.get("stages", [])}}
    return {"total_wall_seconds": sum(r["total_wall_seconds"] or 0 for r in rows.values()), "trials": rows}


def _frames(card: dict, manifest: dict, out: Path) -> dict[str, str]:
    """JPEGs with the label/item box drawn, for problems only; kept under reports/benchmarks."""
    from biliflow.golden_set import source_file
    wanted = []
    for label in card["labels"]:
        if label["status"] in ("missed", "partial", "advisory_only", "trap_hit"):
            wanted.append((label["id"], label["source"], sum(label["clip"]) / 2, label["region_source_pixels"]))
    false_positives = [i for i in card["items"] if not i["advisory"] and i["verdict"] == "false_positive"]
    for item in false_positives[:MAX_FALSE_POSITIVE_FRAMES]:
        wanted.append((item["item_id"] + "@" + item["segment_id"], item["source"], sum(item["clip"]) / 2, item["region"]))
    frames = {}
    for key, source, seconds, region in wanted:
        target = out / "frames" / (key.replace("@", "_") + ".jpg")
        target.parent.mkdir(parents=True, exist_ok=True)
        draw = f"drawbox=x={region['x']}:y={region['y']}:w={region['width']}:h={region['height']}:color=red:t=6," \
            if region else ""
        subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{seconds:.3f}",
                        "-i", str(source_file(ROOT, manifest, source)), "-frames:v", "1",
                        "-vf", draw + "scale=640:-2", "-q:v", "4", str(target)], check=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        frames[key] = target.relative_to(out).as_posix()
    return frames


def _title(names: list[str]) -> str:
    return "Golden Set " + " + ".join(names) + " — scorecard"


def _write(combined: dict, cards: dict[str, dict], frames: bool, manifest: dict, tag: str | None = None) -> Path:
    from biliflow.golden_scoring import scorecard_markdown
    out = BENCHMARKS / ("golden-" + (f"{tag}-" if tag else "") + datetime.now().strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=False)
    (out / "scorecard.json").write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, card in cards.items():
        (out / f"scorecard-{name}.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    images = _frames(combined, manifest, out) if frames else {}
    (out / "scorecard.md").write_text(scorecard_markdown(combined, _title(list(cards)), images), encoding="utf-8")
    if len(cards) > 1:
        for name, card in cards.items():
            (out / f"scorecard-{name}.md").write_text(scorecard_markdown(card, _title([name]), images),
                                                     encoding="utf-8")
    print(out, flush=True)
    return out


def _approximate_suggestions(names: list[str]) -> set[str]:
    """Dense logo windows (coarse routing boxes) from each set's prefill suggestions."""
    approximate: set[str] = set()
    for name in names:
        path = BENCHMARKS / f"golden-{name}/prefill/suggestions.json"
        if path.exists():
            approximate |= {s["id"] for s in json.loads(path.read_text(encoding="utf-8"))["suggestions"]
                            if s.get("tier") == "dense" and s.get("candidate_type") == "dense_logo_window"}
    return approximate


def score_command(args) -> Path:
    from biliflow.golden_scoring import score_sets
    manifest, labels, parts = _load(args)
    queues, provenance = _queues([Path(p) for p in args.queue], manifest)
    combined, cards = score_sets(manifest, labels, parts, queues, _timing([Path(t) for t in args.trial or []]),
                                 _approximate_suggestions(list(parts)))
    paths = {name: str(part["labels_path"].resolve()) if part["labels_path"] else None for name, part in parts.items()}
    for name, card in cards.items():
        card["queues"] = {key: value for key, value in provenance.items() if key in parts[name]["manifest"]["sources"]}
        card["labels_path"] = paths[name]
        combined["sets"][name]["labels_path"] = paths[name]
        info = combined["sets"][name]
        note = " (no events.json yet)" if parts[name]["labels_missing"] else ""
        print(f"{name}: scored {info['complete_segments']}/{info['segments']} complete segments, labels revision "
              f"{info['labels_revision']}, fingerprint {info['labels_fingerprint'][:12]}{note}", flush=True)
    combined["queues"] = provenance
    combined["labels_path"] = paths[next(iter(paths))] if len(paths) == 1 else paths
    return _write(combined, cards, not args.no_frames, manifest, getattr(args, "tag", None))


def troy_detector_scope(requested: list[str] | None, manifest: dict, labels: dict) -> tuple[list[str], dict[str, int]]:
    """Detector groups for the Troy trial and the complete-segment Troy labels they would leave unscored.

    Without --troy-detectors every group runs, as in the baseline trial, so no label is left unscored.
    """
    from biliflow.golden_set import CATEGORY_GROUP
    detectors = list(requested or ALL_GROUPS)
    troy = {s["id"] for s in manifest["segments"] if s["source"] == "troy"
            and (labels["segments"].get(s["id"]) or {}).get("status") == "complete"}
    unscored: dict[str, int] = {}
    for event in labels["events"]:
        group = CATEGORY_GROUP[event["category"]]
        if event["segment_id"] in troy and group not in detectors:
            unscored[group] = unscored.get(group, 0) + 1
    return detectors, unscored


def run_command(args) -> None:
    from biliflow.golden_set import source_file
    manifest, labels, _ = _load(args)  # fail before an hour of trials if labels are unusable
    jobs = dict(RUNS)
    unknown = [key for key in manifest["sources"] if key not in jobs]
    if unknown:
        raise ValueError(f"No trial job is defined for source(s) {', '.join(unknown)}")
    troy_detectors, unscored = troy_detector_scope(args.troy_detectors, manifest, labels)
    if unscored:
        print("WARNING: --troy-detectors leaves Troy labels unscored: " +
              ", ".join(f"{group} {count}" for group, count in sorted(unscored.items())) +
              "; the scorecard cannot be gated against a baseline that scored them", flush=True)
    for key in manifest["sources"]:  # sources shared by several sets are merged, so each is hashed once
        print(f"verifying {key} SHA-256...", flush=True)
        source_file(ROOT, manifest, key, full_hash=True)
    queues, trials = [], []
    for key, job_id in RUNS:
        if key not in manifest["sources"]:
            continue
        detectors = troy_detectors if key == "troy" else list(ALL_GROUPS)
        argv = [sys.executable, str(HARNESS), "run", "--label", "golden", "--job-id", str(job_id), "--fast-scan",
                "--detectors", *detectors]
        print("RUN " + " ".join(argv[2:]), flush=True)
        summary = _run_trial(argv)
        trials.append(ROOT / "reports/benchmarks" / summary["trial"])
        queues.append(ROOT / "reports/jobs" / summary["trial"] / "review-queue.json")
        print(f"DONE {key}: {summary['total_wall_seconds']:.0f}s, original data preserved="
              f"{summary['preserved_original_data']}", flush=True)
    args.queue, args.trial = [str(q) for q in queues], [str(t) for t in trials]
    score_command(args)


def _run_trial(argv: list[str]) -> dict:
    """Stream the harness output (progress, errors) and return its final JSON summary line."""
    lines: list[str] = []
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    with subprocess.Popen(argv, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace") as process:
        for line in process.stdout:
            print("  " + line.rstrip(), flush=True)
            lines.append(line)
        code = process.wait()
    if code:
        raise RuntimeError(f"Harness failed with exit code {code}; the evidence directory printed above holds "
                           "trial.json and the stage logs. Trials finished earlier in this run are kept.")
    for line in reversed(lines):
        try:
            summary = json.loads(line)
        except ValueError:
            continue
        if isinstance(summary, dict) and "trial" in summary:
            return summary
    raise RuntimeError("Harness finished without its JSON summary line")


def compare_command(args) -> int:
    from biliflow.golden_scoring import compare
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    result = compare(baseline, candidate, args.gate)
    target = Path(args.candidate).with_name(f"compare-{Path(args.baseline).parent.name}.json")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"newly caught: {len(result.get('newly_caught', []))}, newly missed: {len(result.get('newly_missed', []))}, "
          f"new trap hits: {len(result.get('new_trap_hits', []))}", flush=True)
    for violation in result["violations"]:
        print("GATE FAIL: " + violation, flush=True)
    print(f"wrote {target}", flush=True)
    return 1 if result["violations"] else 0


def main() -> int:
    from biliflow.golden_set import KNOWN_SETS
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    set_help = "Golden Set to score (repeatable); default: every annotations/golden/<set> with a segments.json"
    labels_help = "events.json replacing one set's labels: SET=path, or a bare path for v1 (repeatable)"
    s = sub.add_parser("score")
    s.add_argument("--queue", action="append", required=True, help="review-queue.json; one per source")
    s.add_argument("--trial", action="append", help="benchmark evidence directory with trial.json (timing)")
    s.add_argument("--no-frames", action="store_true")
    s.add_argument("--set", action="append", choices=KNOWN_SETS, help=set_help)
    s.add_argument("--labels", action="append", help=labels_help)
    s.add_argument("--tag", help="name part of the output directory, e.g. smoke")
    r = sub.add_parser("run")
    r.add_argument("--troy-detectors", nargs="+", default=None, choices=ALL_GROUPS,
                   help="narrow the Troy trial (default: all groups, as in the baseline)")
    r.add_argument("--no-frames", action="store_true")
    r.add_argument("--set", action="append", choices=KNOWN_SETS, help=set_help)
    r.add_argument("--labels", action="append", help=labels_help)
    c = sub.add_parser("compare")
    c.add_argument("--baseline", required=True)
    c.add_argument("--candidate", required=True)
    c.add_argument("--gate", choices=("detector", "speed"))
    args = parser.parse_args()
    if args.command == "score":
        score_command(args)
    elif args.command == "run":
        run_command(args)
    else:
        return compare_command(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
