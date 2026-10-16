"""fix/text-consensus-logo gate: a window of job 145 exported before and after, the top logo read by OCR.

Runs after ``rescan.py gate``: its --work folder holds gate/job-145/candidate/.../gate-fresh, the queue
built from the rescanned text report and the active revision's logo reports (no visual-logo card at
175-180 s). For each window:

1. "before": the edit operations of the user's own queue (what was exported);
   "after": one BLUR on the candidate's film-long text card that covers the logo, alone, on its
   suggested region.
2. A lossless x264 copy of the window, both plans moved into it, the real render_final_output
   (libx264) for both (../scrolling-text/export_check.py).
3. Every STEP seconds EasyOCR (CPU) reads, in the copy and in both exports, the logo's box measured
   on the exports ("logo", PAD px around it) and the rows of the user's top card ("line", full
   width: the site's scrolling line).

Reads only the source, the user's queue, the gate's queue and models/easyocr; writes only under
--work (a new folder in <install>/temp) and --output. Prints job ids, seconds and counts: no film
names, file names, OCR text or links.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("top_logo_export", HERE.parent / "top-logo-box" / "export_logo.py")
top_logo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(top_logo)
from export_check import (  # noqa: E402  (loaded on sys.path by the module above)
    FFMPEG, FFPROBE, INSTALL, RENDER_PROGRAM, STEP, band_frames, queue_folder, readings, run_tree, window_clip,
    window_plan,
)

JOB = 145
LOGO = {"x": 865, "y": 55, "width": 281, "height": 29}  # measured on job 145's exports (1280 x 536)

PLAN_PROGRAM = r"""
import json, sys
from pathlib import Path
import biliflow
from biliflow.review_workflow import approved_operations
job = json.loads(sys.argv[1])
payload = json.loads(Path(job["queue"]).read_text(encoding="utf-8"))
logo = job["logo"]

def covers(region):
    return (region["x"] <= logo["x"] and region["y"] <= logo["y"]
            and region["x"] + region["width"] >= logo["x"] + logo["width"]
            and region["y"] + region["height"] >= logo["y"] + logo["height"])

card = None
if job["film_card_only"]:
    cards = [item for item in payload["items"]
             if item.get("category") == "text" and item.get("candidate_type") == "persistent_overlay"
             and isinstance(item.get("suggested_region_source_pixels"), dict)
             and covers(item["suggested_region_source_pixels"])]
    card = dict(max(cards, key=lambda item: float(item["end_seconds"]) - float(item["start_seconds"])))
    card.update(decision="BLUR", decision_region_source_pixels=card["suggested_region_source_pixels"])
    payload = {**payload, "items": [card]}
top = [item for item in payload["items"] if item.get("decision") == "BLUR" and item.get("category") == "text"
       and isinstance(item.get("decision_region_source_pixels"), dict)
       and item["decision_region_source_pixels"]["y"] < 120]
print(json.dumps({"code": biliflow.__file__, "operations": approved_operations(payload), "source": payload["source"],
                  "frame": payload["items"][0].get("source_frame_size") if payload["items"] else None,
                  "card": None if card is None else {
                      "region": card["suggested_region_source_pixels"], "start": card["start_seconds"],
                      "end": card["end_seconds"]},
                  "top_card": None if not top else max(top, key=lambda item: float(item["end_seconds"])
                                                       - float(item["start_seconds"]))["decision_region_source_pixels"]}))
"""


def check_window(args, start: float, reader) -> dict:
    work, gate_work = Path(args.work), Path(args.gate_work)
    candidate = Path(args.candidate).resolve(strict=True)
    folder = queue_folder(JOB)
    root = work / f"job-{JOB}-{int(start)}"
    for name in ("input", "work", "output", "state", "reports/jobs"):
        (root / name).mkdir(parents=True, exist_ok=True)
    fresh = gate_work / "gate" / f"job-{JOB}" / "candidate" / folder / "gate-fresh" / "review-queue.json"
    plans = {}
    for label, queue, only in (("before", INSTALL / folder / "review-queue.json", False), ("after", fresh, True)):
        target = root / "reports" / "jobs" / f"{label}-{JOB}"
        target.mkdir(parents=True)
        shutil.copyfile(queue, target / "review-queue.json")
        plans[label] = run_tree(candidate, PLAN_PROGRAM, {
            "root": str(root), "queue": str(target / "review-queue.json"), "film_card_only": only, "logo": LOGO})
    window = (start, start + top_logo.WINDOW)
    clip, duration = window_clip(root, JOB, Path(plans["after"]["source"]["path"]), window)
    report = {"job": JOB, "window_film_seconds": [round(start, 3), round(start + duration, 3)],
              "card": plans["after"]["card"], "user_top_card": plans["before"]["top_card"], "exports": {}}
    videos = {"source": clip}
    for label in ("before", "after"):
        plan = window_plan(root, label, plans[label]["operations"], clip, duration, start)
        output = root / "output" / f"job-{JOB}-window-{label}.mp4"
        report["exports"][label] = run_tree(candidate, RENDER_PROGRAM, {
            "root": str(root), "plan": str(plan), "output": str(output), "ffmpeg": str(FFMPEG),
            "ffprobe": str(FFPROBE)})
        report["exports"][label]["operations_in_window"] = len(
            json.loads(plan.read_text(encoding="utf-8"))["approved_operations"])
        videos[label] = output
    frame = plans["after"]["frame"]
    width = int(frame[0])
    logo = top_logo.padded(LOGO, frame)
    line = plans["before"]["top_card"]
    if line is None:
        raise SystemExit("the user's queue has no BLUR text card in the top band to read the line from")
    line_band = (int(line["y"]) // 2 * 2, (int(line["y"]) + int(line["height"]) + 1) // 2 * 2)
    frames = {}
    for label, video in videos.items():
        frames[("logo", label)] = top_logo.box_frames(video, logo)
        frames[("line", label)] = band_frames(video, line_band, width)
    rows = []
    for index in range(min(len(values) for values in frames.values())):
        row = {"film_seconds": round(start + index * STEP, 2)}
        for label in videos:
            row.setdefault("logo", {})[label] = readings(
                reader, frames[("logo", label)][index], (0, logo["height"]), logo["width"])
            row.setdefault("line", {})[label] = readings(
                reader, frames[("line", label)][index], line_band, width)
        rows.append(row)
    report["samples"] = rows
    report["summary"] = {
        band: {label: {"readable": sum(1 for row in rows if row[band][label]["readable"]),
                       "web_address": sum(1 for row in rows if row[band][label]["web_address"])}
               for label in videos}
        for band in ("logo", "line")
    }
    report["sample_count"] = len(rows)
    return report


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gate-work", required=True, help="the --work folder of rescan.py gate")
    parser.add_argument("--candidate", required=True, help="src folder of the candidate tree")
    parser.add_argument("--work", required=True, help="new folder under <install>/temp")
    parser.add_argument("--start", type=float, action="append", required=True, help="window start, film seconds")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    work = Path(args.work).resolve()
    if work.exists() or INSTALL / "temp" not in work.parents:
        raise SystemExit("--work must be a new folder under <install>/temp")
    if INSTALL / "temp" not in Path(args.output).resolve().parents:
        raise SystemExit("--output must be under <install>/temp")
    sys.path.insert(0, str(Path(args.candidate).resolve(strict=True)))
    import easyocr
    model = INSTALL / "models" / "easyocr"
    reader = easyocr.Reader(["vi", "en"], gpu=False, model_storage_directory=str(model),
                            user_network_directory=str(model), download_enabled=False)
    results = [check_window(args, start, reader) for start in args.start]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"windows": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    for result in results:
        print(f"job {result['job']}: window {result['window_film_seconds']}, samples {result['sample_count']}, "
              f"card {result['card']}, user top card {result['user_top_card']}, "
              f"operations {[result['exports'][label]['operations_in_window'] for label in ('before', 'after')]}, "
              f"exports {[result['exports'][label]['status'] for label in ('before', 'after')]}", flush=True)
        for band, labels in result["summary"].items():
            print(f"  {band}: {labels}", flush=True)


if __name__ == "__main__":
    main()
