"""Item 6 gate: the end of job 145 exported before and after, the site's mark under the subtitles read by OCR.

Runs after ``limit_gate.py``: its --work folder holds gate/job-145/after/.../gate-fresh, the queue
built from the text report with this branch's track limit (the user's memories on). From --start
(the end of the old watermark card, 2,313 s) to the end of the film:

1. "before": the edit operations of the user's own queue (what was exported);
   "after": one BLUR on each subtitle-band ``repeated_watermark`` card of the gate's queue that
   suggests BLUR, alone, on its suggested region.
2. A lossless x264 copy of the window, both plans moved into it, the real render_final_output
   (libx264) for both (../scrolling-text/export_check.py).
3. Every STEP seconds EasyOCR (CPU) reads the cards' region (PAD px around it) in the copy and in
   both exports.

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
    INSTALL, RENDER_PROGRAM, FFMPEG, FFPROBE, STEP, queue_folder, readings, run_tree, window_clip, window_plan,
)

JOB = 145
BAND_SHARE = 0.70  # a region whose centre is below this share of the frame height: the subtitle band

PLAN_PROGRAM = r"""
import json, sys
from pathlib import Path
import biliflow
from biliflow.review_workflow import approved_operations
job = json.loads(sys.argv[1])
payload = json.loads(Path(job["queue"]).read_text(encoding="utf-8"))
cards = []
if job["band_cards_only"]:
    for item in payload["items"]:
        region, size = item.get("suggested_region_source_pixels"), item.get("source_frame_size")
        if (item.get("candidate_type") == "repeated_watermark" and item.get("suggested_decision") == "BLUR"
                and isinstance(region, dict) and size
                and (region["y"] + region["height"] / 2) / size[1] >= job["band_share"]):
            card = dict(item)
            card.update(decision="BLUR", decision_region_source_pixels=region)
            cards.append(card)
    payload = {**payload, "items": cards}
print(json.dumps({"code": biliflow.__file__, "operations": approved_operations(payload), "source": payload["source"],
                  "frame": cards[0]["source_frame_size"] if cards else None,
                  "cards": [{"region": card["suggested_region_source_pixels"], "start": card["start_seconds"],
                             "end": card["end_seconds"]} for card in cards]}))
"""


def _union(regions: list[dict]) -> dict:
    left = min(region["x"] for region in regions)
    top = min(region["y"] for region in regions)
    right = max(region["x"] + region["width"] for region in regions)
    bottom = max(region["y"] + region["height"] for region in regions)
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def check_tail(args, reader) -> dict:
    work, gate_work = Path(args.work), Path(args.gate_work)
    candidate = Path(args.candidate).resolve(strict=True)
    folder = queue_folder(JOB)
    root = work / f"job-{JOB}-tail"
    for name in ("input", "work", "output", "state", "reports/jobs"):
        (root / name).mkdir(parents=True, exist_ok=True)
    fresh = gate_work / "gate" / f"job-{JOB}" / "after" / folder / "gate-fresh" / "review-queue.json"
    plans = {}
    for label, queue, only in (("before", INSTALL / folder / "review-queue.json", False), ("after", fresh, True)):
        target = root / "reports" / "jobs" / f"{label}-{JOB}"
        target.mkdir(parents=True)
        shutil.copyfile(queue, target / "review-queue.json")
        plans[label] = run_tree(candidate, PLAN_PROGRAM, {
            "root": str(root), "queue": str(target / "review-queue.json"), "band_cards_only": only,
            "band_share": BAND_SHARE})
    cards = plans["after"]["cards"]
    if not cards:
        raise SystemExit("the gate's queue has no subtitle-band watermark card suggesting BLUR")
    source = Path(plans["after"]["source"]["path"])
    duration = float(plans["after"]["source"]["duration_seconds"])
    clip, length = window_clip(root, JOB, source, (args.start, duration))
    report = {"job": JOB, "window_film_seconds": [round(args.start, 3), round(args.start + length, 3)],
              "film_seconds": duration, "cards": cards, "exports": {}}
    videos = {"source": clip}
    for label in ("before", "after"):
        plan = window_plan(root, label, plans[label]["operations"], clip, length, args.start)
        output = root / "output" / f"job-{JOB}-tail-{label}.mp4"
        report["exports"][label] = run_tree(candidate, RENDER_PROGRAM, {
            "root": str(root), "plan": str(plan), "output": str(output), "ffmpeg": str(FFMPEG),
            "ffprobe": str(FFPROBE)})
        report["exports"][label]["operations_in_window"] = len(
            json.loads(plan.read_text(encoding="utf-8"))["approved_operations"])
        videos[label] = output
    mark = top_logo.padded(_union([card["region"] for card in cards]), plans["after"]["frame"])
    frames = {label: top_logo.box_frames(video, mark) for label, video in videos.items()}
    rows = []
    for index in range(min(len(values) for values in frames.values())):
        rows.append({"film_seconds": round(args.start + index * STEP, 2), **{
            label: readings(reader, frames[label][index], (0, mark["height"]), mark["width"]) for label in videos}})
    report["mark_box"] = mark
    report["sample_count"] = len(rows)
    report["summary"] = {label: {"readable": sum(1 for row in rows if row[label]["readable"]),
                                 "web_address": sum(1 for row in rows if row[label]["web_address"])}
                         for label in videos}
    readable_after = [row["film_seconds"] for row in rows if row["after"]["readable"]]
    report["after_readable_seconds"] = readable_after
    report["samples"] = rows
    return report


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gate-work", required=True, help="the --work folder of limit_gate.py")
    parser.add_argument("--candidate", required=True, help="src folder of the candidate tree")
    parser.add_argument("--work", required=True, help="new folder under <install>/temp")
    parser.add_argument("--start", type=float, default=2313.0, help="window start, film seconds")
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
    result = check_tail(args, reader)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"job {result['job']}: window {result['window_film_seconds']} of {result['film_seconds']}, "
          f"samples {result['sample_count']}, cards {[(card['start'], card['end']) for card in result['cards']]}, "
          f"operations {[result['exports'][label]['operations_in_window'] for label in ('before', 'after')]}, "
          f"exports {[result['exports'][label]['status'] for label in ('before', 'after')]}", flush=True)
    print(f"  mark: {result['summary']}", flush=True)
    print(f"  after, readable at: {result['after_readable_seconds']}", flush=True)


if __name__ == "__main__":
    main()
