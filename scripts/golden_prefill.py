"""Golden Set preparation (docs/QUALITY_PLAN.md §12.4). Never edits jobs, queues or sources.

--set v1 (default) or --set v1.1 chooses annotations/golden/<set> and
reports/benchmarks/golden-<set>/prefill for every command.

  manifest  v1: check the three authorized sources against the Control Center SQLite
            (read-only), re-hash them and write annotations/golden/v1/segments.json
            once. v1.1: re-check size and SHA-256 of the v1 sources, copy v1's
            sources block verbatim, pin the v1 manifest and add GOLDEN_V1_1_SEGMENTS
            (refused when one overlaps a v1 segment, and refused for the real
            annotations directory while GOLDEN_V1_1_PENDING_DECISION is set).
            --output writes elsewhere (dry run). Refuses to replace a different
            existing manifest.
  collect   Build labeling suggestions from every revision of jobs 37/38/39, the
            latest isolated benchmark queue per source/scope and any dense scans,
            into reports/benchmarks/golden-<set>/prefill/suggestions.json.
            Suggestions that labels already handled are kept (marked stale) even
            when the inputs no longer produce them; golden evaluation trials are
            never used as inputs.
  dense     Per segment: scan-text sampled every second and scan-visual-logo
            --exhaustive, isolated under reports/benchmarks/golden-<set>/prefill/dense
            with a benchmark-only routing cache. Resumable; skips finished scans.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_ROOT = ROOT / "annotations/golden"
GOLDEN_DIR = GOLDEN_ROOT / "v1"
MANIFEST = GOLDEN_DIR / "segments.json"
PREFILL = ROOT / "reports/benchmarks/golden-v1/prefill"
SUGGESTIONS = PREFILL / "suggestions.json"
FFPROBE = ROOT / "tools/ffmpeg/bin/ffprobe.exe"
RUN = ROOT / "scripts/run.ps1"
COLD_STAGE = ROOT / "scripts/benchmark-cold-ad-stage.ps1"
DENSE_TEXT_KEEP = ("REVIEW_", "LOW_AD")
DENSE_TEXT_CONTENT = ("LIKELY_CREDITS", "LIKELY_TITLE_OVERLAY")


def _jobs() -> dict[int, dict]:
    connection = sqlite3.connect((ROOT / "state/control-center.sqlite3").as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        jobs = {row["id"]: dict(row) for row in connection.execute("SELECT * FROM jobs")}
        revisions = [dict(row) for row in connection.execute(
            "SELECT job_id, revision, queue_path FROM job_revisions ORDER BY job_id, revision")]
    finally:
        connection.close()
    for job in jobs.values():
        job["revisions"] = [r for r in revisions if r["job_id"] == job["id"]]
    return jobs


def use_set(name: str) -> None:
    """Point GOLDEN_DIR, MANIFEST, PREFILL and SUGGESTIONS at one Golden Set."""
    global GOLDEN_DIR, MANIFEST, PREFILL, SUGGESTIONS
    GOLDEN_DIR = GOLDEN_ROOT / name
    MANIFEST = GOLDEN_DIR / "segments.json"
    PREFILL = ROOT / f"reports/benchmarks/golden-{name}/prefill"
    SUGGESTIONS = PREFILL / "suggestions.json"


def _read_manifest() -> dict:
    from biliflow.golden_set import read_json
    if not MANIFEST.exists():
        raise RuntimeError(f"{MANIFEST} does not exist; build it first with the manifest command")
    return read_json(MANIFEST)


def build_manifest(args) -> None:
    from biliflow.golden_set import GOLDEN_V1_1_PENDING_DECISION, canonical_sha256, read_json, write_json_atomic
    name = getattr(args, "set", "v1")
    target = Path(args.output).resolve() if getattr(args, "output", None) else MANIFEST
    if name == "v1.1" and GOLDEN_V1_1_PENDING_DECISION and target.resolve().is_relative_to(GOLDEN_ROOT.resolve()):
        raise RuntimeError("GOLDEN_V1_1_SEGMENTS is still a proposal waiting for the user's violence-rule decision "
                           "(GOLDEN_V1_1_PENDING_DECISION); not writing into annotations/golden. "
                           "Use --output for a dry run.")
    manifest = _v1_manifest() if name == "v1" else _verified_extension(name)
    if target.exists():
        existing = read_json(target)
        same = {k: v for k, v in existing.items() if k != "created_at"} == \
               {k: v for k, v in manifest.items() if k != "created_at"}
        if not same:
            raise RuntimeError(f"{target} exists with different content; not replacing it")
        print("manifest unchanged; sources verified", flush=True)
        return
    write_json_atomic(target, manifest)
    segments = manifest["segments"]
    total = sum(s["end_seconds"] - s["start_seconds"] for s in segments)
    extends = f", extends {manifest['extends']} {manifest['extends_manifest_sha256'][:12]}" \
        if manifest.get("extends") else ""
    print(f"wrote {target} ({len(segments)} segments, {total / 60:.1f} min, "
          f"sha {canonical_sha256(manifest)[:12]}{extends})", flush=True)


def extension_manifest(name: str, parent: dict, specs) -> dict:
    """Manifest of a set extending ``parent``: its sources verbatim, a pin of it, and new segments only.

    Refuses segments on unknown sources and any duplicate id or overlap with the parent or each other.
    """
    from biliflow.golden_set import (SCHEMA_VERSION, SET_PARENT, canonical_sha256, check_disjoint_segments,
                                     now_iso, validate_manifest)
    if SET_PARENT.get(name) != parent.get("golden_set"):
        raise RuntimeError(f"Golden Set {name} does not extend {parent.get('golden_set')}")
    sources = parent["sources"]
    segments = []
    for sid, key, start, end, split, purpose in specs:
        if key not in sources:
            raise RuntimeError(f"Segment {sid}: source {key} is not in the {parent['golden_set']} manifest")
        segments.append({"id": sid, "source": key, "start_seconds": start,
                         "end_seconds": min(end, math.floor(sources[key]["duration_seconds"] * 1000) / 1000),
                         "split": split, "purpose": purpose})
    check_disjoint_segments([(parent["golden_set"], s) for s in parent["segments"]] + [(name, s) for s in segments])
    return validate_manifest({"schema_version": SCHEMA_VERSION, "golden_set": name, "extends": parent["golden_set"],
                              "extends_manifest_sha256": canonical_sha256(parent), "created_at": now_iso(),
                              "sources": sources, "segments": segments})


def _verified_extension(name: str) -> dict:
    """Extension manifest after re-checking size and SHA-256 of every parent source."""
    from biliflow.golden_set import SET_PARENT, SET_SEGMENTS, read_json, source_file, validate_manifest
    parent = validate_manifest(read_json(GOLDEN_ROOT / SET_PARENT[name] / "segments.json"))
    for key, source in parent["sources"].items():
        print(f"verifying {key} size and SHA-256 ({source['size_bytes'] / 1e9:.1f} GB)...", flush=True)
        source_file(ROOT, parent, key, full_hash=True)
    return extension_manifest(name, parent, SET_SEGMENTS[name])


def _v1_manifest() -> dict:
    from biliflow.golden_set import (GOLDEN_SET, GOLDEN_V1_SEGMENTS, GOLDEN_V1_SOURCES, SCHEMA_VERSION,
                                     file_sha256, now_iso, validate_manifest)
    jobs = _jobs()
    sources = {}
    for key, spec in GOLDEN_V1_SOURCES.items():
        job = jobs[spec["job_id"]]
        path = Path(job["source_path"]).resolve(strict=True)
        if spec["marker"] not in path.name or job["content_style"] != spec["content_style"]:
            raise RuntimeError(f"Job {spec['job_id']} no longer points at the {key} source")
        if path.stat().st_size != job["source_size_bytes"]:
            raise RuntimeError(f"{key}: size differs from the Control Center record")
        print(f"hashing {key} ({path.stat().st_size / 1e9:.1f} GB)...", flush=True)
        digest = file_sha256(path)
        if digest != job["source_sha256"]:
            raise RuntimeError(f"{key}: SHA-256 differs from the Control Center record")
        probe = json.loads(subprocess.check_output(
            [str(FFPROBE), "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,r_frame_rate", "-of", "json", str(path)]))["streams"][0]
        numerator, denominator = (int(x) for x in probe["r_frame_rate"].split("/"))
        sources[key] = {"job_id": spec["job_id"], "path": path.relative_to(ROOT).as_posix(),
                        "size_bytes": path.stat().st_size, "sha256": digest,
                        "duration_seconds": job["duration_seconds"], "width": probe["width"],
                        "height": probe["height"], "fps": round(numerator / denominator, 6),
                        "content_style": job["content_style"]}
    segments = [{"id": sid, "source": key, "start_seconds": start,
                 "end_seconds": min(end, math.floor(sources[key]["duration_seconds"] * 1000) / 1000), "split": split,
                 "purpose": purpose} for sid, key, start, end, split, purpose in GOLDEN_V1_SEGMENTS]
    return validate_manifest({"schema_version": SCHEMA_VERSION, "golden_set": GOLDEN_SET,
                              "created_at": now_iso(), "sources": sources, "segments": segments})


def _latest_benchmark_queues(manifest: dict) -> list[tuple[str, Path]]:
    hashes = {s["sha256"] for s in manifest["sources"].values()}
    latest: dict[tuple, tuple[str, Path]] = {}
    for marker in sorted((ROOT / "reports/jobs").glob("*/.biliflow-benchmark")):
        queue_path = marker.parent / "review-queue.json"
        if not queue_path.exists() or not _is_hint_trial(marker.parent.name):
            continue
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        sha = (queue.get("source") or {}).get("sha256")
        if sha not in hashes:
            continue
        scope = tuple(sorted(((queue.get("detection_scope") or {}).get("selected")) or ("all",)))
        stamp = marker.parent.name[-15:]  # ...-YYYYMMDD-HHMMSS
        if (sha, scope) not in latest or stamp > latest[(sha, scope)][0]:
            latest[(sha, scope)] = (stamp, queue_path)
    return [(f"bench:{path.parent.name}", path) for _, path in sorted(latest.values())]


def _is_hint_trial(name: str) -> bool:
    """Golden evaluation trials score the labels; they must not feed the labeling hints."""
    return "-full-golden-" not in name


def _dense_suggestions(manifest: dict) -> list[dict]:
    from biliflow.golden_set import normalize_region
    rows = []
    for segment in manifest["segments"]:
        base = PREFILL / "dense" / segment["id"]
        low, high = segment["start_seconds"], segment["end_seconds"]

        def add(category, start, end, region, suggested, advisory, priority, kind, labels, origin):
            start, end = max(float(start), low), min(max(float(end), float(start) + 0.5), high)
            if end > start:
                rows.append({"segment_id": segment["id"], "category": category,
                             "group": "advertising", "start_seconds": round(start, 3),
                             "end_seconds": round(end, 3), "item_start_seconds": round(start, 3),
                             "item_end_seconds": round(end, 3), "region_source_pixels": region,
                             "suggested_action": suggested, "advisory": advisory, "priority": priority,
                             "candidate_type": kind, "labels": labels[:5], "tier": "dense",
                             "origins": [origin], "prior_decisions": []})

        text_path = base / "text/text-scan.json"
        if text_path.exists():
            report = json.loads(text_path.read_text(encoding="utf-8"))
            sx = report["source_size"][0] / report["analysis_size"][0]
            sy = report["source_size"][1] / report["analysis_size"][1]
            for track in report["tracks"]:
                routing = str(track.get("routing", ""))
                # Hints keep every ad-like track (policy hit, review candidate, "advertisement" top label).
                # Credits/title cards otherwise never become hints (the watermark comes from the review
                # queues); other tracks do when routed REVIEW_*/LOW_AD*, persistent, or ad_probability >= 0.10.
                # Non-persistent scene text/subtitles below 0.10 are dropped: the labeler still watches them.
                ad_like = track.get("policy_hits") or track.get("review_candidate") \
                    or track.get("semantic_top_label") == "advertisement"
                keep = ad_like or (routing not in DENSE_TEXT_CONTENT and (
                    routing.startswith(DENSE_TEXT_KEEP) or track.get("persistent")
                    or float(track.get("ad_probability") or 0) >= 0.10))
                if not keep:
                    continue
                x1, y1, x2, y2 = track["union_box"]
                region = normalize_region({"x": x1 * sx, "y": y1 * sy, "width": (x2 - x1) * sx,
                                           "height": (y2 - y1) * sy})
                add("text", track["start_seconds"], track["end_seconds"], region, track.get("suggested_decision"),
                    not track.get("review_candidate"), track.get("review_priority"), track.get("routing"),
                    [str(t) for t in track.get("sample_text") or []], f"dense:{segment['id']}:text")
        logo_path = base / "visual-logo/scan.json"
        if logo_path.exists():
            report = json.loads(logo_path.read_text(encoding="utf-8"))
            width, height = manifest["sources"][segment["source"]]["width"], manifest["sources"][segment["source"]]["height"]
            sx, sy = width / report["analysis_size"][0], height / report["analysis_size"][1]
            for interval in report["intervals"]:
                confirmation = interval.get("visual_logo_confirmation") or {}
                box = (confirmation.get("features") or {}).get("focus_box_analysis")
                region = None
                if box:
                    x1, y1, x2, y2 = box
                    region = normalize_region({"x": x1 * sx, "y": y1 * sy, "width": (x2 - x1) * sx,
                                               "height": (y2 - y1) * sy})
                add("visual_logo", interval["start_seconds"], interval["end_seconds"], region, None, False,
                    interval.get("priority"), "dense_logo_window",
                    [str(interval.get("predicted_label")), str(confirmation.get("answer"))],
                    f"dense:{segment['id']}:logo")
    return rows


def collect(args) -> None:
    from biliflow.golden_set import (GOLDEN_V1_SOURCES, canonical_sha256, carry_forward_resolved, file_sha256,
                                     merge_suggestions, now_iso, queue_suggestions, read_json, write_json_atomic)
    manifest = _read_manifest()
    jobs = _jobs()
    inputs, rows = [], []
    queues = []
    for key, spec in GOLDEN_V1_SOURCES.items():
        for revision in jobs[spec["job_id"]]["revisions"]:
            queues.append((f"job{spec['job_id']}-r{revision['revision']}", ROOT / revision["queue_path"]))
    queues += _latest_benchmark_queues(manifest)
    for origin, path in queues:
        if not path.exists():
            print(f"skip missing {origin}: {path}", flush=True)
            continue
        found = queue_suggestions(read_json(path), manifest, origin)
        inputs.append({"origin": origin, "path": path.relative_to(ROOT).as_posix(), "sha256": file_sha256(path),
                       "suggestions": len(found)})
        rows += found
    dense = _dense_suggestions(manifest)
    if dense:
        inputs.append({"origin": "dense", "path": (PREFILL / "dense").relative_to(ROOT).as_posix(),
                       "suggestions": len(dense)})
    merged = merge_suggestions(rows + dense)
    labels_path = GOLDEN_DIR / "events.json"
    if labels_path.exists():
        before = len(merged)
        merged = carry_forward_resolved(merged, read_json(labels_path)["suggestion_resolutions"])
        if len(merged) > before:
            print(f"kept {len(merged) - before} handled suggestions that the inputs no longer produce "
                  "(marked stale)", flush=True)
    write_json_atomic(SUGGESTIONS, {"schema_version": 1, "created_at": now_iso(),
                                    "manifest_sha256": canonical_sha256(manifest), "inputs": inputs,
                                    "suggestions": merged})
    for segment in manifest["segments"]:
        mine = [s for s in merged if s["segment_id"] == segment["id"]]
        print(f"{segment['id']:5} review={sum(s['tier'] == 'review' for s in mine):3} "
              f"dense={sum(s['tier'] == 'dense' for s in mine):3} "
              f"prior={sum(bool(s['prior_decision']) for s in mine):3}", flush=True)
    print(f"wrote {SUGGESTIONS} ({len(merged)} suggestions from {len(inputs)} inputs)", flush=True)


def dense(args) -> None:
    from biliflow.golden_set import source_file
    manifest = _read_manifest()
    chosen = [s for s in manifest["segments"] if not args.segments or s["id"] in args.segments]
    cache = PREFILL / "dense-cache"
    env = dict(os.environ, BILIFLOW_BENCHMARK_CACHE=str(cache))
    for segment in chosen:
        source = manifest["sources"][segment["source"]]
        path = source_file(ROOT, manifest, segment["source"])
        base = PREFILL / "dense" / segment["id"]
        span = ["--start-seconds", str(segment["start_seconds"]),
                "--duration-seconds", str(round(segment["end_seconds"] - segment["start_seconds"], 3))]
        commands = [
            ("text", base / "text/text-scan.json",
             ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(RUN), "scan-text",
              "--input", str(path), "--report-dir", str(base / "text"), *span, "--sample-every", "1.0",
              "--max-report-tracks", "2000", "--recognition-batch-size", "8", "--recognition-frame-window", "4",
              "--detect-precision", "fp16", "--decode", "nvdec"]),
            ("logo", base / "visual-logo/scan.json",
             ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(COLD_STAGE),
              "scan-visual-logo", "--input", str(path), "--report-dir", str(base / "visual-logo"), *span,
              "--exhaustive", "--routing-workers", "3", "--decode", "nvdec", "--source-sha256", source["sha256"]]),
        ]
        for name, report, argv in commands:
            if report.exists():
                print(f"{segment['id']} {name}: done earlier", flush=True)
                continue
            report.parent.mkdir(parents=True, exist_ok=True)
            print(f"{segment['id']} {name}: running", flush=True)
            with (base / f"{name}.log").open("w", encoding="utf-8") as log:
                subprocess.run(argv, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            print(f"{segment['id']} {name}: finished", flush=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    from biliflow.golden_set import KNOWN_SETS
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", default="v1", choices=KNOWN_SETS, help="Golden Set to prepare (default v1)")
    common = argparse.ArgumentParser(add_help=False)  # --set is also accepted after the command
    common.add_argument("--set", default=argparse.SUPPRESS, choices=KNOWN_SETS, help="same as --set before the command")
    sub = parser.add_subparsers(dest="command", required=True)
    m = sub.add_parser("manifest", parents=[common])
    m.add_argument("--output", help="write the manifest to this file instead of annotations/golden/<set> (dry run)")
    sub.add_parser("collect", parents=[common])
    d = sub.add_parser("dense", parents=[common])
    d.add_argument("--segments", nargs="*")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(argv)
    use_set(args.set)
    {"manifest": build_manifest, "collect": collect, "dense": dense}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
