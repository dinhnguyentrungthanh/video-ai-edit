"""fix/top-logo-box: which review cards the rule changes, on every job of the install.

For every job with a review queue: its report folder copied into --work and its queue
built fresh by the candidate tree (gate_rebuild.py's copy and build). Lists each
fixed-text card whose region took in a visual-logo box: job id, card type and time,
region before and after, the logo boxes and the visual-logo cards' own windows.

Reads, never writes: the real control-center.sqlite3 (read-only URI), the report
folders, state/studio-logo-memory.json and state/studio-logo-frames (copied) and the
sources (FFmpeg reads short windows). Writes only under --work, a new folder in
<install>/temp, and --output. Prints job ids, seconds and boxes; no film names, file
names, OCR text or links.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scrolling-text"))
from gate_rebuild import (  # noqa: E402  (the jobs gate, beside this folder)
    FFMPEG, INSTALL, _new_work, build_options, copy_job, job_rows, prepare_root, read, run_build, write,
)


def queue_job_ids() -> list[int]:
    database = (INSTALL / "state" / "control-center.sqlite3").as_posix()
    connection = sqlite3.connect(f"file:///{database}?mode=ro", uri=True)
    try:
        rows = connection.execute("select id, active_queue_path from jobs where active_queue_path is not null").fetchall()
    finally:
        connection.close()
    return sorted(int(job_id) for job_id, queue in rows if (INSTALL / queue).is_file())


def joined_cards(queue: dict) -> list[dict]:
    cards = []
    for item in queue.get("items") or []:
        details = item.get("fixed_text_logo")
        if item.get("category") != "text" or not isinstance(details, dict):
            continue
        cards.append({
            "candidate_type": item.get("candidate_type"), "start": item["start_seconds"], "end": item["end_seconds"],
            "previous_region": details["previous_region"], "region": item["suggested_region_source_pixels"],
            "logo_regions": details["logo_regions"],
            "logo_windows": [(record.get("start_seconds"), record.get("end_seconds"))
                             for record in item.get("supporting_detections") or []
                             if record.get("category") == "visual_logo"],
        })
    return cards


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidate", required=True, help="src folder of the candidate tree")
    parser.add_argument("--work", required=True, help="new folder under <install>/temp")
    parser.add_argument("--output", required=True)
    parser.add_argument("--part", type=int, nargs=2, metavar=("INDEX", "COUNT"), default=(0, 1),
                        help="build every COUNT-th job from INDEX (runs side by side)")
    args = parser.parse_args()
    work, tree = _new_work(args.work), Path(args.candidate).resolve(strict=True)
    index, count = args.part
    job_ids = queue_job_ids()[index::count]
    rows = job_rows(job_ids)
    root = work / "candidate"
    prepare_root(root, with_memory=True)
    results = []
    for job_id in job_ids:
        old = read(rows[job_id]["queue"])
        queue_copy, reports, has_source = copy_job(root, job_id, rows[job_id])
        fresh = queue_copy.parent / "gate-fresh" / "review-queue.json"
        run_build(tree, {"root": str(root), "reports": [str(p) for p in reports], "queue": str(fresh),
                         "ffmpeg": str(FFMPEG), "options": build_options(old, memory=True)})
        cards = joined_cards(read(fresh))
        results.append({"job": job_id, "source_present": has_source, "joined": cards})
        for card in cards:
            print(f"job {job_id}: {card['candidate_type']} {card['start']}-{card['end']} region "
                  f"{card['previous_region']} -> {card['region']}, logo boxes {card['logo_regions']}, "
                  f"logo windows now {card['logo_windows']}", flush=True)
    write(Path(args.output), {"tree": str(tree), "jobs": results})
    print(f"jobs built {len(results)}, jobs with a joined logo "
          f"{sum(1 for result in results if result['joined'])}", flush=True)


if __name__ == "__main__":
    main()
