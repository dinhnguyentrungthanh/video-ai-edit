"""fix/top-logo-box gate: a window of job 145 exported before and after, the top logo read by OCR.

Runs after gate_rebuild.py jobs (its --work folder holds, per job, the user's queue in
baseline/ and the candidate's fresh queue in candidate/.../gate-fresh). For each window:

1. "before": the edit operations of the user's own queue (what was exported);
   "after": one BLUR on the candidate's fixed-text card whose region took in a
   visual-logo box (``fixed_text_logo``), alone, on its suggested region.
2. A lossless x264 copy of the window, both plans moved into it, the real
   render_final_output (libx264) for both (../scrolling-text/export_check.py).
3. Every STEP seconds EasyOCR (CPU) reads, in the copy and in both exports, the logo's
   box ("logo", the visual-logo region, PAD px around it) and the rows of the card's
   region before the fix ("line", full width: the site's scrolling line).

Reads only the sources, the copied queues and models/easyocr; writes only under --work,
a new folder in <install>/temp, and --output. Prints job ids, seconds and counts: no film
names, file names, OCR text or links.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scrolling-text"))
from export_check import (  # noqa: E402  (the part 1 export check)
    FFMPEG, FFPROBE, INSTALL, RENDER_PROGRAM, STEP, band_frames, queue_folder, readings, run_tree, window_clip,
    window_plan,
)

WINDOW = 100.0
PAD = 4

PLAN_PROGRAM = r"""
import json, sys
from pathlib import Path
import biliflow
from biliflow.review_workflow import approved_operations
job = json.loads(sys.argv[1])
payload = json.loads(Path(job["queue"]).read_text(encoding="utf-8"))
card = None
if job["film_card_only"]:
    cards = [item for item in payload["items"]
             if item.get("category") == "text" and isinstance(item.get("fixed_text_logo"), dict)]
    card = dict(max(cards, key=lambda item: float(item["end_seconds"]) - float(item["start_seconds"])))
    card.update(decision="BLUR", decision_region_source_pixels=card["suggested_region_source_pixels"])
    payload = {**payload, "items": [card]}
print(json.dumps({"code": biliflow.__file__, "operations": approved_operations(payload), "source": payload["source"],
                  "card": None if card is None else {
                      "region": card["suggested_region_source_pixels"],
                      "previous_region": card["fixed_text_logo"]["previous_region"],
                      "logo_regions": card["fixed_text_logo"]["logo_regions"],
                      "frame": card["source_frame_size"], "start": card["start_seconds"],
                      "end": card["end_seconds"]}}))
"""


def box_frames(video: Path, box: dict) -> list[bytes]:
    """RGB pixels of ``box``, every STEP seconds from the window's start."""
    raw = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-vf", f"fps={1 / STEP:g},crop={box['width']}:{box['height']}:{box['x']}:{box['y']}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, timeout=1800, check=True).stdout
    size = box["width"] * box["height"] * 3
    return [raw[offset:offset + size] for offset in range(0, len(raw) - size + 1, size)]


def padded(region: dict, frame: list[int]) -> dict:
    x, y = max(0, int(region["x"]) - PAD), max(0, int(region["y"]) - PAD)
    width = min(int(frame[0]) - x, int(region["width"]) + 2 * PAD) // 2 * 2
    height = min(int(frame[1]) - y, int(region["height"]) + 2 * PAD) // 2 * 2
    return {"x": x, "y": y, "width": width, "height": height}


def check_window(args, job_id: int, start: float, reader) -> dict:
    work, gate = Path(args.work), Path(args.gate_work)
    candidate = Path(args.candidate).resolve(strict=True)
    folder = queue_folder(job_id)
    root = work / f"job-{job_id}-{int(start)}"
    for name in ("input", "work", "output", "state", "reports/jobs"):
        (root / name).mkdir(parents=True, exist_ok=True)
    plans = {}
    for label, source, only in (("before", gate / "baseline" / folder, False),
                                ("after", gate / "candidate" / folder / "gate-fresh", True)):
        target = root / "reports" / "jobs" / f"{label}-{job_id}"
        shutil.copytree(source, target)
        plans[label] = run_tree(candidate, PLAN_PROGRAM, {
            "root": str(root), "queue": str(target / "review-queue.json"), "film_card_only": only})
    card = plans["after"]["card"]
    window = (start, start + WINDOW)
    clip, duration = window_clip(root, job_id, Path(plans["after"]["source"]["path"]), window)
    report = {"job": job_id, "window_film_seconds": [round(start, 3), round(start + duration, 3)],
              "card": card, "exports": {}}
    videos = {"source": clip}
    for label in ("before", "after"):
        plan = window_plan(root, label, plans[label]["operations"], clip, duration, start)
        output = root / "output" / f"job-{job_id}-window-{label}.mp4"
        report["exports"][label] = run_tree(candidate, RENDER_PROGRAM, {
            "root": str(root), "plan": str(plan), "output": str(output), "ffmpeg": str(FFMPEG),
            "ffprobe": str(FFPROBE)})
        operations = json.loads(plan.read_text(encoding="utf-8"))["approved_operations"]
        report["exports"][label]["operations_in_window"] = len(operations)
        videos[label] = output
    width = int(card["frame"][0])
    logo = padded(card["logo_regions"][0], card["frame"])
    line = card["previous_region"]
    line_band = (int(line["y"]) // 2 * 2, (int(line["y"]) + int(line["height"]) + 1) // 2 * 2)
    frames = {}
    for label, video in videos.items():
        frames[("logo", label)] = box_frames(video, logo)
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
    parser.add_argument("--gate-work", required=True, help="the --work folder of gate_rebuild.py jobs")
    parser.add_argument("--candidate", required=True, help="src folder of the candidate tree")
    parser.add_argument("--work", required=True, help="new folder under <install>/temp")
    parser.add_argument("--job", type=int, required=True)
    parser.add_argument("--start", type=float, action="append", required=True, help="window start, film seconds")
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
    results = [check_window(args, args.job, start, reader) for start in args.start]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"windows": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    for result in results:
        print(f"job {result['job']}: window {result['window_film_seconds']}, samples {result['sample_count']}, "
              f"operations {[result['exports'][label]['operations_in_window'] for label in ('before', 'after')]}, "
              f"exports {[result['exports'][label]['status'] for label in ('before', 'after')]}", flush=True)
        for band, labels in result["summary"].items():
            print(f"  {band}: {labels}", flush=True)


if __name__ == "__main__":
    main()
