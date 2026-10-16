"""Item 6 gate (fix/text-consensus-logo, 2026-10-16): the 250-track limit keeps ad evidence.

Reads the scans of ``rescan.py scan`` (``--scans``: its --work folder) and makes two text reports
of each:
  before  the limit of ca17c07: review candidates, platform names, the earliest references up to 250;
  after   this branch's text stage: those, every reference with ad evidence or a web address, and
          the rest in ``omitted_tracks`` without previews.
A scan made with no limit is cut here (exact: the stage cuts after its sort, so cutting a stored
uncapped report is what the stage writes); a scan made with the limit of 250 by this branch is
"after" itself, and "before" is cut from it (the tracks kept before are all in it, in order).

Each variant's queues are built with its own code: ``--before-src`` (ca17c07's tree) for
"before", this checkout for "after". For the jobs it builds review queues with the user's memories (studio logos and remembered
addresses, copied read-only) and the other reports of the active revision; for the golden set,
queues without memories, scored and compared. Writes ``<--work>/gate/summary.json``: job ids,
counts, seconds and sizes only (no OCR text).

  .venv\\Scripts\\python.exe docs\\evidence\\text-consensus\\limit_gate.py --scans temp\\tc-scan --work temp\\tc-limit
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("text_consensus_rescan", HERE / "rescan.py")
rescan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rescan)
gate, INSTALL, FFMPEG, TREE = rescan.gate, rescan.INSTALL, rescan.FFMPEG, rescan.TREE
CAP = rescan.REPORT_CAP
BAND_SHARE = 0.70  # a card whose region's centre is below this share of the frame height: the subtitle band


def _old_limit(summaries: list[dict]) -> list[dict]:
    """``_limit_report_tracks`` of ca17c07."""
    candidates = [summary for summary in summaries if summary.get("review_candidate", True)]
    references = [summary for summary in summaries if not summary.get("review_candidate", True)]
    platform = [summary for summary in references if summary.get("platform_name")]
    others = [summary for summary in references if not summary.get("platform_name")]
    return candidates + platform + others[:max(0, CAP - len(candidates) - len(platform))]


def after_report(scanned: dict) -> dict:
    """What this branch's text stage writes with the limit of 250."""
    from biliflow.textscan import MAX_OMITTED_TRACK_RECORDS, _limit_report_tracks

    if scanned.get("max_report_tracks") == CAP:
        return scanned
    listed = _limit_report_tracks(scanned["tracks"], CAP)
    ids = {id(track) for track in listed}
    omitted = [{key: value for key, value in track.items() if key != "preview"}
               for track in scanned["tracks"] if id(track) not in ids]
    return {**scanned, "tracks": listed, "max_report_tracks": CAP,
            "tracks_omitted": max(0, scanned["tracks_before_limit"] - len(listed)),
            "omitted_tracks_unrecorded": max(0, len(omitted) - MAX_OMITTED_TRACK_RECORDS),
            "omitted_tracks": omitted[:MAX_OMITTED_TRACK_RECORDS]}


def before_report(after: dict) -> dict:
    listed = _old_limit(after["tracks"])
    payload = {key: value for key, value in after.items() if key not in {"omitted_tracks", "omitted_tracks_unrecorded"}}
    return {**payload, "tracks": listed, "tracks_omitted": max(0, after["tracks_before_limit"] - len(listed))}


def _report_bytes(payload: dict) -> int:
    """text-scan.json as the stage writes it (textscan._write_text_report)."""
    return len(json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"))


def sizes(payload: dict, previews: Path) -> dict:
    listed = [track for track in payload["tracks"] if track.get("preview")]
    return {"tracks": len(payload["tracks"]), "omitted_records": len(payload.get("omitted_tracks") or []),
            "omitted_unrecorded": payload.get("omitted_tracks_unrecorded", 0),
            "report_bytes": _report_bytes(payload), "previews": len(listed),
            "preview_bytes": sum((previews / Path(track["preview"]).name).stat().st_size for track in listed)}


def remembered_keys() -> tuple[str, ...]:
    path = INSTALL / "state" / "ad-text-memory.json"
    if not path.is_file():
        return ()
    return tuple(dict.fromkeys(str(record["key"]) for record in gate.read(path)["records"]))


def readmitted(after: dict, keys: tuple[str, ...]) -> int:
    from biliflow.ad_text_memory import readmit_remembered_tracks

    return len(readmit_remembered_tracks(after, keys).get("readmitted_track_ids") or [])


def _band_watermark_end(queue: dict) -> float | None:
    ends = [float(item["end_seconds"]) for item in queue.get("items") or []
            if item.get("candidate_type") == "repeated_watermark" and gate._band(item) == "bottom"]
    return max(ends) if ends else None


def _band_overlays(queue: dict) -> list[dict]:
    """Film-long text overlays in the subtitle band (task 2's cards), for the record."""
    rows = []
    for item in queue.get("items") or []:
        region, height = item.get("suggested_region_source_pixels"), gate._frame_height(item)
        if (item.get("category") == "text" and item.get("candidate_type") == "persistent_overlay"
                and isinstance(region, dict) and height
                and (region["y"] + region["height"] / 2) / height >= BAND_SHARE):
            rows.append({"start": item["start_seconds"], "end": item["end_seconds"], "region": region,
                         "suggested": item.get("suggested_decision")})
    return rows


def gate_job(work: Path, label: str, reports_by_variant: dict[str, dict], previews: Path,
             trees: dict[str, Path]) -> dict:
    job_id = int(label.split("-")[1])
    row = gate.job_rows([job_id])[job_id]
    old = gate.read(row["queue"])
    result = {"job": job_id}
    for variant, payload in reports_by_variant.items():
        root = work / "gate" / label / variant
        gate.prepare_root(root, with_memory=True)
        memory = INSTALL / "state" / "ad-text-memory.json"
        if memory.is_file():
            shutil.copyfile(memory, root / "state" / "ad-text-memory.json")
        queue_copy, reports, _ = gate.copy_job(root, job_id, row)
        rescan._install_text_report(rescan._text_report(reports), payload, previews)
        fresh = queue_copy.parent / "gate-fresh" / "review-queue.json"
        gate.run_build(trees[variant], {"root": str(root), "reports": [str(p) for p in reports], "queue": str(fresh),
                                         "ffmpeg": str(FFMPEG), "options": gate.build_options(old, memory=True)})
        queue = gate.read(fresh)
        summary = gate.summarize(queue, gate.subtitle_band_ad_tracks(root, reports, TREE / "src"))
        summary["band_watermark_end"] = _band_watermark_end(queue)
        summary["band_overlays"] = _band_overlays(queue)
        summary["queue_bytes"] = fresh.stat().st_size
        result[variant] = summary
    before, after = result["before"]["subtitle_band_ads"], result["after"]["subtitle_band_ads"]
    result["subtitle_band_weakened"] = sorted(set(before["main_list"]) - set(after["main_list"]))
    return result


def gate_golden(work: Path, reports: dict[str, dict[str, dict]], previews: dict[str, Path],
                trees: dict[str, Path]) -> dict:
    script = TREE / "scripts" / "evaluate_golden.py"
    cards = {}
    for variant in ("before", "after"):
        root = work / "gate" / "golden" / variant
        gate.prepare_root(root, with_memory=False)
        command = [sys.executable, str(script), "score", "--root", str(root), "--golden-root",
                   str(INSTALL / "annotations" / "golden"), "--set", "v2", "--no-frames", "--tag", f"tl-{variant}"]
        for name, value in gate.read(gate.GOLDEN_BASELINE / "scorecard-v2.json")["queues"].items():
            folder = INSTALL / Path(value["path"]).parent
            target = root / folder.relative_to(INSTALL)
            shutil.copytree(folder, target)
            original = gate.read(folder / "review-queue.json")
            queue_reports = [root / Path(p) for p in original.get("reports") or []]
            for report in queue_reports:
                gate._point_to_golden_clip(report)
            label = rescan._golden_label(name)
            rescan._install_text_report(rescan._text_report(queue_reports), reports[label][variant], previews[label])
            queue = target / "gate-fresh" / "review-queue.json"
            gate.run_build(trees[variant], {"root": str(root), "reports": [str(p) for p in queue_reports],
                                          "queue": str(queue), "ffmpeg": str(FFMPEG),
                                          "options": gate.build_options(original, memory=False)})
            command += ["--queue", str(queue)]
        done = subprocess.run(command, env=gate._environment(TREE / "src"), capture_output=True, text=True,
                              encoding="utf-8", timeout=3600)
        if done.returncode:
            raise RuntimeError(done.stderr[-1500:])
        [card] = sorted((root / "reports" / "benchmarks").glob(f"golden-*tl-{variant}*/scorecard-v2.json"))
        cards[variant] = card
    compared = {}
    for label, baseline, candidate in (("before_vs_after", cards["before"], cards["after"]),
                                       ("v2_baseline_tag_vs_after", gate.GOLDEN_BASELINE / "scorecard-v2.json",
                                        cards["after"])):
        done = subprocess.run([sys.executable, str(script), "compare", "--baseline", str(baseline), "--candidate",
                               str(candidate), "--gate", "detector"], env=gate._environment(TREE / "src"),
                              capture_output=True, text=True, encoding="utf-8", timeout=600)
        compared[label] = {"exit": done.returncode, "output": done.stdout[-6000:]}
        print(f"== golden {label}: exit {done.returncode}\n{done.stdout[-6000:]}", flush=True)
    return {"scorecards": {key: str(value) for key, value in cards.items()}, "compare": compared}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scans", required=True, help="the --work folder of rescan.py scan")
    parser.add_argument("--work", required=True, help="new folder under <install>/temp")
    parser.add_argument("--before-src", default=str(TREE / "src"), help="src folder that builds the \"before\" queues")
    parser.add_argument("--no-golden", action="store_true")
    args = parser.parse_args()
    trees = {"before": Path(args.before_src).resolve(strict=True), "after": TREE / "src"}
    scans = Path(args.scans).resolve(strict=True) / "scans"
    work = rescan._new_folder(args.work)
    keys = remembered_keys()
    summary: dict = {"remembered_addresses": len(keys), "trees": {key: str(value) for key, value in trees.items()},
                     "reports": {}, "jobs": {}}
    reports: dict[str, dict[str, dict]] = {}
    previews: dict[str, Path] = {}
    for scan in sorted(scans.iterdir()):
        label = scan.name
        text = scan / "reports" / "scan" / "text"
        scanned = gate.read(text / "text-scan.json")
        after = after_report(scanned)
        reports[label] = {"before": before_report(after), "after": after}
        previews[label] = text / "text-previews"
        summary["reports"][label] = {
            "duration_seconds": scanned["duration_seconds"], "tracks_before_limit": scanned["tracks_before_limit"],
            "scanned_with_limit": scanned.get("max_report_tracks"),
            "before": sizes(reports[label]["before"], previews[label]),
            "after": sizes(after, previews[label]),
            "readmitted_with_memory": readmitted(after, keys),
        }
        print(f"{label}: {json.dumps(summary['reports'][label])}", flush=True)
    for label in sorted(label for label in reports if label.startswith("job-")):
        summary["jobs"][label] = gate_job(work, label, reports[label], previews[label], trees)
        job = summary["jobs"][label]
        print(f"{label}: watermark end {job['before']['band_watermark_end']} -> {job['after']['band_watermark_end']}"
              f" of {summary['reports'][label]['duration_seconds']:.1f}; items {job['before']['items']} -> "
              f"{job['after']['items']}; band weakened {len(job['subtitle_band_weakened'])}", flush=True)
    golden = [label for label in reports if label.startswith("golden-")]
    every_golden = {rescan._golden_label(name) for name in gate.read(gate.GOLDEN_BASELINE / "scorecard-v2.json")["queues"]}
    if args.no_golden or not golden:
        print("golden skipped", flush=True)
    elif set(golden) != every_golden:
        print(f"golden skipped: {len(every_golden - set(golden))} golden sources not scanned", flush=True)
    else:
        summary["golden"] = gate_golden(work, reports, previews, trees)
    gate.write(work / "gate" / "summary.json", summary)


if __name__ == "__main__":
    main()
