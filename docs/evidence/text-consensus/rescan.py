"""Text-scan consensus gate (session D, 2026-10-16): rescan the text stage on copies, then compare.

``scan`` (GPU: ask the user first, run only with the real queue empty) runs this checkout's text
stage on each source with the production fast-scan arguments and no track cap, into a new folder
under <install>/temp. It also keeps what the corner grouping received (``promote-input.json``) and
the reads of every long corner track (``anchor-reads.json``); both hold OCR text and stay in temp.

``gate`` (CPU) makes three text reports of each scan:
  baseline   the group box before this change (``overlay_box_sources.consensus_box``), cap 250;
             checked against main's own grouping on the saved grouping input;
  candidate  this change, cap 250 (what the text stage now writes);
  uncapped   this change, every track (item 6: measured only, not a proposed change);
builds the review queues of the jobs with the other reports of their active revision, scores the
golden set and prints counts (``summary.json``: job ids, counts, boxes and seconds only).

  .venv\\Scripts\\python.exe docs\\evidence\\text-consensus\\rescan.py scan --work temp\\tc-scan --jobs 145 130 --golden
  .venv\\Scripts\\python.exe docs\\evidence\\text-consensus\\rescan.py gate --work temp\\tc-scan
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

TREE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(TREE / "src"))
_spec = importlib.util.spec_from_file_location("gate_rebuild", TREE / "docs/evidence/scrolling-text/gate_rebuild.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)
INSTALL, FFMPEG = gate.INSTALL, gate.FFMPEG
FFPROBE = INSTALL / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"
UNCAPPED = 1_000_000
REPORT_CAP = 250
CORNERS = {"top-left", "top-right", "bottom-left", "bottom-right"}
# Job 145's fixed logo, measured on its exports (1280 x 536 frame).
LOGO_145 = {"x": 865, "y": 55, "width": 281, "height": 29}


def _new_folder(path: str) -> Path:
    folder = Path(path).resolve()
    if folder.exists() or INSTALL / "temp" not in folder.parents:
        raise SystemExit(f"{path} must be a new folder under <install>/temp")
    return folder


def _golden_label(name: str) -> str:
    return "golden-" + re.sub(r"[^0-9A-Za-z_.-]+", "_", name)


def _text_report(reports: list[Path]) -> Path:
    [report] = [path for path in reports if path.name == "text-scan.json"]
    return report


def _sources(job_ids: list[int], golden: bool) -> list[dict]:
    """(label, source, the report it replaces) for every scan."""
    sources = []
    for job_id, row in sorted(gate.job_rows(job_ids).items()):
        reports = [INSTALL / Path(p) for p in gate.read(row["queue"]).get("reports") or []]
        if not row["source"].is_file():
            raise SystemExit(f"job {job_id} has no source video")
        sources.append({"label": f"job-{job_id}", "source": row["source"], "original": _text_report(reports)})
    if golden:
        for name, value in gate.read(gate.GOLDEN_BASELINE / "scorecard-v2.json")["queues"].items():
            queue = gate.read(INSTALL / value["path"])
            original = _text_report([INSTALL / Path(p) for p in queue.get("reports") or []])
            source = Path(str(gate.read(original)["input"]))
            if not source.is_file():
                source = INSTALL / "annotations" / "golden" / "v2" / "media" / source.name
            if not source.is_file():
                raise SystemExit(f"golden queue {name} has no source")
            sources.append({"label": _golden_label(name), "source": source, "original": original})
    return sources


def scan_command(args) -> None:
    from biliflow import textscan
    from biliflow.text_semantics import LocalEmbeddingTextClassifier
    import easyocr

    work = _new_folder(args.work)
    cuda = args.device == "cuda"
    sources = [entry for entry in _sources(args.jobs or [], args.golden) if not args.only or entry["label"] in args.only]
    reader = easyocr.Reader(["vi", "en"], gpu=cuda, model_storage_directory=str(INSTALL / "models" / "easyocr"),
                            user_network_directory=str(INSTALL / "models" / "easyocr"), download_enabled=False)
    classifier = LocalEmbeddingTextClassifier(INSTALL / "models" / "multilingual_minilm_text_semantics",
                                              INSTALL / "annotations" / "text_semantics_seed_v1.json", args.device)
    captured: dict = {}
    real_promote, real_summarize = textscan._promote_repeated_corner_overlays, textscan._summarize_track

    def promote(summaries, video_duration):
        captured["promote"] = {"video_duration": video_duration, "summaries": json.loads(json.dumps(summaries))}
        return real_promote(summaries, video_duration)

    def summarize(track, sample_every, observed_through_seconds=None):
        if track.zone in CORNERS and len(track.observations) >= textscan.OVERLAY_ANCHOR_MINIMUM_READINGS:
            captured["anchors"].append({"track_id": track.track_id, "zone": track.zone, "reads": [
                [obs["timestamp_seconds"], obs["box"], obs["text"]] for obs in track.observations]})
        return real_summarize(track, sample_every, observed_through_seconds)

    textscan._promote_repeated_corner_overlays, textscan._summarize_track = promote, summarize
    for entry in sources:
        original = gate.read(entry["original"])
        root = work / "scans" / entry["label"]
        (root / "reports").mkdir(parents=True)
        captured.clear()
        captured["anchors"] = []
        started = time.perf_counter()
        payload = textscan.scan_text(
            project_root=root, input_path=entry["source"], report_dir=root / "reports" / "scan" / "text",
            model_dir=INSTALL / "models" / "easyocr", ffmpeg_path=FFMPEG, ffprobe_path=FFPROBE,
            sample_every=float(original["sample_every_seconds"]), analysis_width=int(original["analysis_size"][0]),
            max_report_tracks=args.max_report_tracks, duration_seconds_limit=args.seconds,
            device_name=args.device, semantic_classifier=classifier,
            policy_path=TREE / "config" / "text_review_policy.json", reader=reader,
            recognition_batch_size=8 if cuda else 1, recognition_frame_window=4 if cuda else 1,
            detect_precision="fp16" if cuda else "fp32",
        )
        gate.write(root / "promote-input.json", captured["promote"])
        gate.write(root / "anchor-reads.json", captured["anchors"])
        gate.write(root / "scan-info.json", {
            "label": entry["label"], "original": str(entry["original"]), "seconds": round(time.perf_counter() - started, 1),
            "duration_seconds": payload["duration_seconds"], "tracks_before_limit": payload["tracks_before_limit"],
            "max_report_tracks": args.max_report_tracks,
        })
        print(f"{entry['label']}: {payload['tracks_before_limit']} tracks, "
              f"{time.perf_counter() - started:.0f} s", flush=True)


# --- gate ------------------------------------------------------------------------------


def variants(uncapped: dict) -> dict[str, dict]:
    from biliflow.textscan import _limit_report_tracks

    capped_tracks = _limit_report_tracks(uncapped["tracks"], REPORT_CAP)
    candidate = {**uncapped, "tracks": capped_tracks, "max_report_tracks": REPORT_CAP,
                 "tracks_omitted": max(0, uncapped["tracks_before_limit"] - len(capped_tracks))}
    baseline_tracks = []
    for track in capped_tracks:
        sources = track.get("overlay_box_sources")
        track = {key: value for key, value in track.items() if key not in {"footprint_box", "overlay_box_sources"}}
        if sources:
            track["union_box"] = list(sources["consensus_box"])
        baseline_tracks.append(track)
    return {"baseline": {**candidate, "tracks": baseline_tracks}, "candidate": candidate, "uncapped": uncapped}


def main_grouping(main_tree: Path, promote_input: Path) -> dict[int, list[int]]:
    """{group track id: union box} from main's own grouping on the saved input."""
    program = ("import json,sys\nfrom biliflow.textscan import _promote_repeated_corner_overlays as p\n"
               "d=json.load(open(sys.argv[1],encoding='utf-8'))\n"
               "print(json.dumps({t['track_id']:t['union_box'] for t in p(d['summaries'],d['video_duration']) "
               "if t.get('grouped_track_ids')}))")
    done = subprocess.run([sys.executable, "-c", program, str(promote_input)], env=gate._environment(main_tree / "src"),
                          capture_output=True, text=True, encoding="utf-8", timeout=600, check=True)
    return {int(key): value for key, value in json.loads(done.stdout).items()}


def box_changes(candidate: dict) -> list[dict]:
    rows = []
    for track in candidate["tracks"]:
        sources = track.get("overlay_box_sources")
        if sources:
            rows.append({"track": track["track_id"], "zone": track["zone"], "before": sources["consensus_box"],
                         "after": track["union_box"], "suggested": track.get("suggested_decision"),
                         "anchors": [{key: anchor[key] for key in ("track_id", "footprint_box", "observations", "joined")}
                                     for anchor in sources["anchors"]]})
    return rows


def _install_text_report(report: Path, payload: dict, previews: Path) -> None:
    gate.write(report, {**payload, "input": gate.read(report)["input"]})
    target = report.parent / "text-previews"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(previews, target)


def _logo_cards(queue: dict) -> list[dict]:
    """Main-list cards whose region covers at least half of job 145's logo, at 1000 s."""
    cards = []
    for item in queue.get("items") or []:
        region = item.get("suggested_region_source_pixels")
        if not isinstance(region, dict) or not item["start_seconds"] <= 1000.0 <= item["end_seconds"]:
            continue
        left, top = max(region["x"], LOGO_145["x"]), max(region["y"], LOGO_145["y"])
        right = min(region["x"] + region["width"], LOGO_145["x"] + LOGO_145["width"])
        bottom = min(region["y"] + region["height"], LOGO_145["y"] + LOGO_145["height"])
        share = max(0, right - left) * max(0, bottom - top) / (LOGO_145["width"] * LOGO_145["height"])
        if share >= 0.5:
            cards.append({"category": item.get("category"), "type": item.get("candidate_type"),
                          "suggested": item.get("suggested_decision"), "start": item["start_seconds"],
                          "end": item["end_seconds"], "region": region, "logo_share": round(share, 3),
                          "fixed_text_logo": item.get("fixed_text_logo")})
    return cards


def gate_job(work: Path, label: str, reports_by_variant: dict[str, dict], previews: Path) -> dict:
    job_id = int(label.split("-")[1])
    row = gate.job_rows([job_id])[job_id]
    old = gate.read(row["queue"])
    runs = dict(reports_by_variant)
    first_run = INSTALL / "reports" / "jobs" / row["queue"].parent.name.split("-run-")[0]
    if job_id == 145 and first_run.is_dir() and first_run != row["queue"].parent:
        runs["candidate-rev1-logo"] = reports_by_variant["candidate"]
    result = {"job": job_id}
    for variant, payload in runs.items():
        root = work / "gate" / label / variant
        gate.prepare_root(root, with_memory=True)
        queue_copy, reports, _ = gate.copy_job(root, job_id, row)
        text_report = _text_report(reports)
        _install_text_report(text_report, payload, previews)
        if variant == "candidate-rev1-logo":
            # The first revision's logo reports: its visual-logo card at 175-180 s is there.
            target = text_report.parents[1] / "visual-logo"
            shutil.rmtree(target)
            shutil.copytree(first_run / "visual-logo", target)
        fresh = queue_copy.parent / "gate-fresh" / "review-queue.json"
        gate.run_build(TREE / "src", {"root": str(root), "reports": [str(p) for p in reports], "queue": str(fresh),
                                      "ffmpeg": str(FFMPEG), "options": gate.build_options(old, memory=True)})
        queue = gate.read(fresh)
        summary = gate.summarize(queue, gate.subtitle_band_ad_tracks(root, reports, TREE / "src"))
        if job_id == 145:
            summary["logo_cards"] = _logo_cards(queue)
        result[variant] = summary
    base, cand = result["baseline"]["subtitle_band_ads"], result["candidate"]["subtitle_band_ads"]
    result["subtitle_band_weakened"] = sorted(set(base["main_list"]) - set(cand["main_list"]))
    return result


def gate_golden(work: Path, reports: dict[str, dict[str, dict]], previews: dict[str, Path]) -> dict:
    script = TREE / "scripts" / "evaluate_golden.py"
    cards = {}
    for variant in ("baseline", "candidate", "uncapped"):
        root = work / "gate" / "golden" / variant
        gate.prepare_root(root, with_memory=False)
        command = [sys.executable, str(script), "score", "--root", str(root), "--golden-root",
                   str(INSTALL / "annotations" / "golden"), "--set", "v2", "--no-frames", "--tag", f"tc-{variant}"]
        for name, value in gate.read(gate.GOLDEN_BASELINE / "scorecard-v2.json")["queues"].items():
            folder = INSTALL / Path(value["path"]).parent
            target = root / folder.relative_to(INSTALL)
            shutil.copytree(folder, target)
            original = gate.read(folder / "review-queue.json")
            queue_reports = [root / Path(p) for p in original.get("reports") or []]
            for report in queue_reports:
                gate._point_to_golden_clip(report)
            label = _golden_label(name)
            _install_text_report(_text_report(queue_reports), reports[label][variant], previews[label])
            queue = target / "gate-fresh" / "review-queue.json"
            gate.run_build(TREE / "src", {"root": str(root), "reports": [str(p) for p in queue_reports], "queue": str(queue),
                                          "ffmpeg": str(FFMPEG), "options": gate.build_options(original, memory=False)})
            command += ["--queue", str(queue)]
        done = subprocess.run(command, env=gate._environment(TREE / "src"), capture_output=True, text=True,
                              encoding="utf-8", timeout=3600)
        if done.returncode:
            raise RuntimeError(done.stderr[-1500:])
        [card] = sorted((root / "reports" / "benchmarks").glob(f"golden-*tc-{variant}*/scorecard-v2.json"))
        cards[variant] = card
    compared = {}
    for label, baseline, candidate in (("baseline_vs_candidate", cards["baseline"], cards["candidate"]),
                                       ("v2_baseline_tag_vs_candidate", gate.GOLDEN_BASELINE / "scorecard-v2.json", cards["candidate"]),
                                       ("candidate_vs_uncapped", cards["candidate"], cards["uncapped"])):
        done = subprocess.run([sys.executable, str(script), "compare", "--baseline", str(baseline), "--candidate",
                               str(candidate), "--gate", "detector"], env=gate._environment(TREE / "src"),
                              capture_output=True, text=True, encoding="utf-8", timeout=600)
        compared[label] = {"exit": done.returncode, "output": done.stdout[-6000:]}
        print(f"== golden {label}: exit {done.returncode}\n{done.stdout[-6000:]}", flush=True)
    return {"scorecards": {key: str(value) for key, value in cards.items()}, "compare": compared}


def item6(candidate: dict, uncapped: dict, queues: dict | None) -> dict:
    kept = {track["track_id"] for track in candidate["tracks"]}
    omitted = [track for track in uncapped["tracks"] if track["track_id"] not in kept]
    by_zone: dict[str, int] = {}
    for track in omitted:
        key = f"{track.get('zone')}/{track.get('routing')}"
        by_zone[key] = by_zone.get(key, 0) + 1
    row = {"omitted": len(omitted), "omitted_by_zone_routing": dict(sorted(by_zone.items()))}
    if queues:
        for key in ("items", "suggested_blur", "web_address_items", "advisory_text_items"):
            row[f"{key}_capped_uncapped"] = [queues["candidate"][key], queues["uncapped"][key]]
        row["scrolling_cards_capped_uncapped"] = [len(queues["candidate"]["scrolling_cards"]),
                                                  len(queues["uncapped"]["scrolling_cards"])]
        row["watermark_cards_capped_uncapped"] = [len(queues["candidate"]["repeated_watermark_cards"]),
                                                  len(queues["uncapped"]["repeated_watermark_cards"])]
    return row


def gate_command(args) -> None:
    work = Path(args.work).resolve(strict=True)
    if INSTALL / "temp" not in work.parents:
        raise SystemExit("--work must be a scan folder under <install>/temp")
    if (work / "gate").exists():
        raise SystemExit("gate already ran in this folder")
    main_tree = work / "main-tree"
    if not main_tree.exists():
        main_tree.mkdir()
        archive = subprocess.run(["git", "-C", str(TREE), "archive", args.main_commit, "src"], capture_output=True, check=True)
        subprocess.run(["tar", "-x", "-C", str(main_tree)], input=archive.stdout, check=True)
    summary: dict = {"box_changes": {}, "baseline_check": {}, "jobs": {}, "item6": {}}
    reports: dict[str, dict[str, dict]] = {}
    previews: dict[str, Path] = {}
    for scan in sorted((work / "scans").iterdir()):
        label = scan.name
        uncapped = gate.read(scan / "reports" / "scan" / "text" / "text-scan.json")
        reports[label] = variants(uncapped)
        previews[label] = scan / "reports" / "scan" / "text" / "text-previews"
        summary["box_changes"][label] = box_changes(reports[label]["candidate"])
        ours = {track["track_id"]: track["union_box"] for track in reports[label]["baseline"]["tracks"]
                if track.get("grouped_track_ids")}
        theirs = main_grouping(main_tree, scan / "promote-input.json")
        summary["baseline_check"][label] = {
            "groups": len(theirs), "same": sum(1 for key in ours if theirs.get(key) == ours[key]),
            "different": sorted(key for key in ours if theirs.get(key) != ours[key]),
            "not_in_capped": sorted(set(theirs) - set(ours)),  # left out by the 250 cap
        }
    for label in sorted(label for label in reports if label.startswith("job-")):
        summary["jobs"][label] = gate_job(work, label, reports[label], previews[label])
        summary["item6"][label] = item6(reports[label]["candidate"], reports[label]["uncapped"], summary["jobs"][label])
        print(f"{label}: done", flush=True)
    golden = [label for label in reports if label.startswith("golden-")]
    every_golden = {_golden_label(name) for name in gate.read(gate.GOLDEN_BASELINE / "scorecard-v2.json")["queues"]}
    if golden and set(golden) != every_golden:
        print(f"golden skipped: {len(every_golden - set(golden))} golden sources not scanned", flush=True)
    elif golden:
        summary["golden"] = gate_golden(work, reports, previews)
        for label in golden:
            summary["item6"][label] = item6(reports[label]["candidate"], reports[label]["uncapped"], None)
    gate.write(work / "gate" / "summary.json", summary)
    print(json.dumps({key: summary[key] for key in ("box_changes", "baseline_check")}, indent=1), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan")
    scan.add_argument("--work", required=True)
    scan.add_argument("--jobs", type=int, nargs="*")
    scan.add_argument("--golden", action="store_true")
    scan.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    scan.add_argument("--only", nargs="*", help="scan only these labels (dry runs)")
    scan.add_argument("--seconds", type=float, help="scan only the first seconds of each source (dry runs)")
    scan.add_argument("--max-report-tracks", type=int, default=UNCAPPED,
                      help="the stage's track limit (default: none; 250 is production, for limit_gate.py)")
    check = commands.add_parser("gate")
    check.add_argument("--work", required=True)
    check.add_argument("--main-commit", default="419377f")
    args = parser.parse_args()
    {"scan": scan_command, "gate": gate_command}[args.command](args)


if __name__ == "__main__":
    main()
