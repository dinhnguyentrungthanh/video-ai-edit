"""fix/ticker-subtitle gate, part 2: a window of a repeated watermark's moments exported before and after.

Runs after gate_rebuild.py jobs (its --work folder holds, per job, the user's queue in
baseline/ and the candidate's fresh queue in candidate/.../gate-fresh). For each job:

1. The window: the first WINDOW seconds of the subtitle-band repeated-watermark card
   of the candidate's fresh queue (LEAD seconds before its first moment), or its moments
   up to TRAIL seconds after the last when they are shorter.
2. "before": the edit operations of the user's own queue (what was exported);
   "after": the operations of one BLUR on that card alone (approved_operations of a
   queue holding only it, decided BLUR on its suggested region).
3. A lossless x264 copy of the window, both plans moved into it, the real
   render_final_output (libx264) for both (export_check.py).
4. Every STEP seconds EasyOCR (CPU) reads two bands in the copy and in both exports:
   the card's region ("watermark") and the SUBTITLE_ROWS source rows above it
   ("subtitles"). Per sample: letters read, a reading of at least LETTERS letters, a web
   address; and whether the sample lies inside one of the card's moments.

Reads only the sources, the copied queues and models/easyocr; writes only under --work,
a new folder in <install>/temp. Prints job ids, seconds and counts: no film names, file
names, OCR text or links.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scrolling-text"))
from export_check import (  # noqa: E402  (the part 1 export check, beside this folder)
    FFMPEG, FFPROBE, INSTALL, LETTERS, RENDER_PROGRAM, STEP, band_frames, queue_folder, readings, run_tree,
    window_clip, window_plan,
)

LEAD, TRAIL = 3.0, 3.0
WINDOW = 100.0
SUBTITLE_ROWS = 72  # source rows above the watermark's region read as the subtitle band

PLAN_PROGRAM = r"""
import json, sys
from pathlib import Path
import biliflow
from biliflow.review_workflow import approved_operations
job = json.loads(sys.argv[1])
payload = json.loads(Path(job["queue"]).read_text(encoding="utf-8"))
cards = [item for item in payload["items"] if item.get("candidate_type") == "repeated_watermark"]
card = None
if job["only_watermark"]:
    bottom = [item for item in cards
              if item["suggested_region_source_pixels"]["y"] > 0.6 * item["source_frame_size"][1]]
    card = dict(max(bottom, key=lambda item: len(item["detected_intervals"])))
    card.update(decision="BLUR", decision_region_source_pixels=card["suggested_region_source_pixels"])
    payload = {**payload, "items": [card]}
print(json.dumps({"code": biliflow.__file__, "operations": approved_operations(payload), "source": payload["source"],
                  "card": None if card is None else {
                      "moments": card["detected_intervals"], "region": card["suggested_region_source_pixels"],
                      "frame": card["source_frame_size"]}}))
"""


def check_job(args, job_id: int, reader) -> dict:
    work, gate = Path(args.work), Path(args.gate_work)
    candidate = Path(args.candidate).resolve(strict=True)
    folder = queue_folder(job_id)
    root = work / f"job-{job_id}"
    for name in ("input", "work", "output", "state", "reports/jobs"):
        (root / name).mkdir(parents=True, exist_ok=True)
    plans = {}
    for label, source, only in (("before", gate / "baseline" / folder, False),
                                ("after", gate / "candidate" / folder / "gate-fresh", True)):
        target = root / "reports" / "jobs" / f"{label}-{job_id}"
        shutil.copytree(source, target)
        plans[label] = run_tree(candidate, PLAN_PROGRAM, {
            "root": str(root), "queue": str(target / "review-queue.json"), "only_watermark": only})
    card = plans["after"]["card"]
    moments = [(float(m["start_seconds"]), float(m["end_seconds"])) for m in card["moments"]]
    window = (max(0.0, moments[0][0] - LEAD), min(moments[-1][1] + TRAIL, moments[0][0] + WINDOW))
    shown = [(start, min(end, window[1])) for start, end in moments if start < window[1]]
    clip, duration = window_clip(root, job_id, Path(plans["after"]["source"]["path"]), window)
    report = {"job": job_id, "window_film_seconds": [round(window[0], 3), round(window[0] + duration, 3)],
              "moments_in_window": [list(m) for m in shown], "region": card["region"], "exports": {}}
    videos = {"source": clip}
    for label in ("before", "after"):
        plan = window_plan(root, label, plans[label]["operations"], clip, duration, window[0])
        output = root / "output" / f"job-{job_id}-window-{label}.mp4"
        report["exports"][label] = run_tree(candidate, RENDER_PROGRAM, {
            "root": str(root), "plan": str(plan), "output": str(output), "ffmpeg": str(FFMPEG),
            "ffprobe": str(FFPROBE)})
        report["exports"][label]["operations_in_window"] = len(
            json.loads(plan.read_text(encoding="utf-8"))["approved_operations"])
        videos[label] = output
    width, region = int(card["frame"][0]), card["region"]
    top, bottom = int(region["y"]), int(region["y"]) + int(region["height"])
    bands = {"watermark": (max(0, top - 4) // 2 * 2, (bottom + 4) // 2 * 2),
             "subtitles": (max(0, top - SUBTITLE_ROWS) // 2 * 2, max(2, top - 2) // 2 * 2)}
    rows = []
    frames = {(name, label): band_frames(video, band, width)
              for name, band in bands.items() for label, video in videos.items()}
    for index in range(min(len(values) for values in frames.values())):
        film_seconds = round(window[0] + index * STEP, 2)
        row = {"film_seconds": film_seconds, "in_moment": any(a <= film_seconds <= b for a, b in shown)}
        for name, band in bands.items():
            row[name] = {label: readings(reader, frames[(name, label)][index], band, width) for label in videos}
        rows.append(row)
    report["samples"] = rows
    report["summary"] = {
        name: {label: {
            "readable_in_moments": sum(1 for row in rows if row["in_moment"] and row[name][label]["readable"]),
            "readable_between": sum(1 for row in rows if not row["in_moment"] and row[name][label]["readable"]),
            "web_address_in_moments": sum(1 for row in rows if row["in_moment"] and row[name][label]["web_address"]),
            "web_address_between": sum(
                1 for row in rows if not row["in_moment"] and row[name][label]["web_address"]),
        } for label in videos} for name in bands}
    report["samples_in_moments"] = sum(1 for row in rows if row["in_moment"])
    report["samples_between"] = sum(1 for row in rows if not row["in_moment"])
    return report


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gate-work", required=True, help="the --work folder of gate_rebuild.py jobs")
    parser.add_argument("--candidate", required=True, help="src folder of the candidate tree")
    parser.add_argument("--work", required=True, help="new folder under <install>/temp")
    parser.add_argument("--job", type=int, action="append", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    work = Path(args.work).resolve()
    if work.exists() or INSTALL / "temp" not in work.parents:
        raise SystemExit("--work must be a new folder under <install>/temp")
    sys.path.insert(0, str(Path(args.candidate).resolve(strict=True)))
    import easyocr
    model = INSTALL / "models" / "easyocr"
    reader = easyocr.Reader(["vi", "en"], gpu=False, model_storage_directory=str(model),
                            user_network_directory=str(model), download_enabled=False)
    results = [check_job(args, job_id, reader) for job_id in args.job]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"jobs": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    for result in results:
        print(f"job {result['job']}: window {result['window_film_seconds']}, moments {result['moments_in_window']}, "
              f"samples in moments {result['samples_in_moments']}, between {result['samples_between']}, "
              f"operations {[result['exports'][label]['operations_in_window'] for label in ('before', 'after')]}",
              flush=True)
        for name, labels in result["summary"].items():
            for label, summary in labels.items():
                print(f"  {name} {label}: {summary}", flush=True)


if __name__ == "__main__":
    main()
