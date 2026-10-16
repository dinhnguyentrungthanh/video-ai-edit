"""Task 2 gate (fix/text-consensus-logo, 2026-10-16): a film-long fixed-text card of subtitle dialogue
suggests no BLUR.

  rescanned  jobs 130, 134, 136, 143 and 145, every revision: the revision's reports with the text
             report of item 6's rescan (``--scans``: the --work folder of ``rescan.py scan``).
  stored     the 25 jobs of the subtitle-band gate, active revision, stored reports.
  golden     the Golden Set v2 sources with item 6's rescanned text reports, scored and compared.

Each job queue is built fresh by ``--before-src`` (7fa0e0e) and by this checkout, with the user's
memories (copied read-only), and rebuilt by this checkout with keep_decisions=True over a copy of
the revision's queue. Per build: the fixed-text cards in the subtitle band (centre below 70 % of the
height, between 18 % and 82 % of the width), the band's repeated-watermark cards, and the
subtitle-band ad tracks (a web address, phone number, @handle or policy hit) that a main-list card
cites (``main_list``) and that a card suggesting BLUR or CUT cites (``action_list``). ``weakened`` and
``action_weakened`` list the tracks cited before and not after. Writes ``<--work>/summary.json``: job
ids, counts, seconds and regions only (no OCR text, no names).

  .venv\\Scripts\\python.exe docs\\evidence\\text-consensus\\dialogue_gate.py --part rescanned --scans temp\\tc-scan --work temp\\tc-d1
"""
from __future__ import annotations

import argparse
import importlib.util
import shutil
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("text_consensus_limit_gate", HERE / "limit_gate.py")
limit_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(limit_gate)
gate, rescan, INSTALL, FFMPEG, TREE = limit_gate.gate, limit_gate.rescan, limit_gate.INSTALL, limit_gate.FFMPEG, limit_gate.TREE

BAND_TOP, BAND_X = 0.70, (0.18, 0.82)
RESCANNED_JOBS = [130, 134, 136, 143, 145]
STORED_JOBS = [62, 63, 64, 65, 66, 70, 71, 72, 99, 100, 101, 102, 103, 107, 108, 112, 118, 128,
               130, 134, 136, 140, 143, 145, 147]
ACTIONS = {"BLUR", "CUT"}


def revisions(job_ids: list[int], *, active_only: bool) -> list[dict]:
    database = (INSTALL / "state" / "control-center.sqlite3").as_posix()
    connection = sqlite3.connect(f"file:///{database}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "select r.job_id, r.revision, r.queue_path, r.is_active, j.source_path from job_revisions r "
            f"join jobs j on j.id = r.job_id where r.job_id in ({','.join('?' * len(job_ids))}) "
            "order by r.job_id, r.revision", job_ids,
        ).fetchall()
    finally:
        connection.close()
    return [{"job": int(job), "revision": int(revision), "queue": INSTALL / queue, "source": Path(source)}
            for job, revision, queue, active, source in rows if active or not active_only]


def _in_band(item: dict) -> bool:
    region, size = item.get("suggested_region_source_pixels"), item.get("source_frame_size")
    if not isinstance(region, dict) or not isinstance(size, list) or len(size) != 2 or not size[0] or not size[1]:
        return False
    centre_x = (region["x"] + region["width"] / 2) / size[0]
    centre_y = (region["y"] + region["height"] / 2) / size[1]
    return centre_y >= BAND_TOP and BAND_X[0] <= centre_x <= BAND_X[1]


def band_text_cards(queue: dict) -> list[dict]:
    rows = []
    for item in queue.get("items") or []:
        details = item.get("fixed_text_overlay")
        if item.get("category") == "text" and isinstance(details, dict) and _in_band(item):
            rows.append({
                "start": item["start_seconds"], "end": item["end_seconds"], "rule": details.get("rule"),
                "whole_film": details.get("whole_film"), "region": item.get("suggested_region_source_pixels"),
                "suggested": item.get("suggested_decision"), "decision": item.get("decision"),
                "dialogue": (details.get("subtitle_dialogue") or {}).get("line_consensus"),
                "supports": len(item.get("supporting_detections") or []),
            })
    return rows


def band_watermarks(queue: dict) -> list[dict]:
    return [{"start": item["start_seconds"], "end": item["end_seconds"], "suggested": item.get("suggested_decision"),
             "decision": item.get("decision"), "region": item.get("suggested_region_source_pixels")}
            for item in queue.get("items") or []
            if item.get("candidate_type") == "repeated_watermark" and _in_band(item)]


def _action_refs(items: list[dict]) -> set[str]:
    return gate._cited_tracks([item for item in items if item.get("suggested_decision") in ACTIONS])


def summarize(queue: dict, ad_tracks: list[str]) -> dict:
    summary = gate.summarize(queue, ad_tracks)
    action = _action_refs(queue.get("items") or [])
    summary["subtitle_band_ads"]["action_list"] = sorted(ref for ref in ad_tracks if ref in action)
    summary["band_text_cards"] = band_text_cards(queue)
    summary["band_watermarks"] = band_watermarks(queue)
    summary["duration_seconds"] = (queue.get("source") or {}).get("duration_seconds")
    return summary


def gate_revision(work: Path, trees: dict[str, Path], row: dict, text_report: dict | None,
                  previews: Path | None) -> dict:
    old = gate.read(row["queue"])
    result = {"job": row["job"], "revision": row["revision"]}
    for side, tree in trees.items():
        root = work / f"job-{row['job']}-r{row['revision']}" / side
        gate.prepare_root(root, with_memory=True)
        memory = INSTALL / "state" / "ad-text-memory.json"
        if memory.is_file():
            shutil.copyfile(memory, root / "state" / "ad-text-memory.json")
        queue_copy, reports, has_source = gate.copy_job(root, row["job"], row)
        if text_report is not None:
            rescan._install_text_report(rescan._text_report(reports), text_report, previews)
        fresh = queue_copy.parent / "gate-fresh" / "review-queue.json"
        job = {"root": str(root), "reports": [str(p) for p in reports], "queue": str(fresh), "ffmpeg": str(FFMPEG),
               "options": gate.build_options(old, memory=True)}
        gate.run_build(tree, job)
        ad_tracks = gate.subtitle_band_ad_tracks(root, reports, TREE / "src")
        result[side] = summarize(gate.read(fresh), ad_tracks)
        if side == "after" and has_source:
            gate.run_build(tree, {**job, "queue": str(queue_copy), "keep_decisions": True})
            rebuilt = gate.read(queue_copy)
            result["rebuild"] = summarize(rebuilt, ad_tracks)
            result["rebuild"]["decisions_kept"] = gate.decisions_kept(old, rebuilt)
        result["source_present"] = has_source
    before, after = result["before"]["subtitle_band_ads"], result["after"]["subtitle_band_ads"]
    result["weakened"] = sorted(set(before["main_list"]) - set(after["main_list"]))
    result["action_weakened"] = sorted(set(before["action_list"]) - set(after["action_list"]))
    return result


def _line(result: dict) -> str:
    before, after = result["before"], result["after"]
    cards = lambda side: [(card["suggested"], card["dialogue"]) for card in side["band_text_cards"]]
    rebuild = result.get("rebuild")
    return (f"job {result['job']} r{result['revision']}: items {before['items']} -> {after['items']}, band text cards "
            f"{cards(before)} -> {cards(after)}, band watermarks {[(w['suggested'], w['end']) for w in after['band_watermarks']]}"
            f", band ads main {len(before['subtitle_band_ads']['main_list'])} -> {len(after['subtitle_band_ads']['main_list'])}"
            f" action {len(before['subtitle_band_ads']['action_list'])} -> {len(after['subtitle_band_ads']['action_list'])}"
            f" (weakened {len(result['weakened'])}, action {len(result['action_weakened'])})"
            + (f", rebuild lost {len(rebuild['decisions_kept']['lost'])} of {rebuild['decisions_kept']['old_decided']}"
               if rebuild else ""))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--part", required=True, choices=("rescanned", "stored", "golden"))
    parser.add_argument("--scans", help="the --work folder of rescan.py scan (rescanned, golden)")
    parser.add_argument("--work", required=True, help="new folder under <install>/temp")
    parser.add_argument("--before-src", required=True, help="src folder that builds the \"before\" queues")
    parser.add_argument("--job", type=int, action="append", help="limit the stored part to these job ids")
    args = parser.parse_args()
    trees = {"before": Path(args.before_src).resolve(strict=True), "after": TREE / "src"}
    work = rescan._new_folder(args.work)
    summary: dict = {"part": args.part, "trees": {key: str(value) for key, value in trees.items()}, "jobs": []}
    if args.part in {"rescanned", "golden"}:
        scans = Path(args.scans).resolve(strict=True) / "scans"
        reports = {scan.name: limit_gate.after_report(gate.read(scan / "reports" / "scan" / "text" / "text-scan.json"))
                   for scan in sorted(scans.iterdir())}
        previews = {label: scans / label / "reports" / "scan" / "text" / "text-previews" for label in reports}
    if args.part == "rescanned":
        for row in revisions(RESCANNED_JOBS, active_only=False):
            label = f"job-{row['job']}"
            summary["jobs"].append(gate_revision(work, trees, row, reports[label], previews[label]))
            print(_line(summary["jobs"][-1]), flush=True)
    elif args.part == "stored":
        for row in revisions(args.job or STORED_JOBS, active_only=True):
            summary["jobs"].append(gate_revision(work, trees, row, None, None))
            print(_line(summary["jobs"][-1]), flush=True)
    else:
        golden = {label: {"before": report, "after": report} for label, report in reports.items()
                  if label.startswith("golden-")}
        summary["golden"] = limit_gate.gate_golden(work, golden, previews, trees)
    gate.write(work / "summary.json", summary)


if __name__ == "__main__":
    main()
