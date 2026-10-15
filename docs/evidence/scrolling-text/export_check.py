"""fix/ticker-subtitle gate, part 1: the ticker windows of real jobs exported before and after.

Runs after gate_rebuild.py jobs (its --work folder holds, per job, the user's queue in
baseline/ and the queue rebuilt with keep_decisions=True in candidate/). For each job:

1. "before": the edit operations of the user's own queue (what was exported);
   "after": those of the rebuilt queue. A ticker card the user never decided gets
   BLUR through record_review_decision on the copy (what the user would press).
2. A lossless x264 copy (qp 0) of the window around the ticker card, the operations
   moved into it, and the real render_final_output (libx264, two threads, below-normal
   priority) for both plans.
3. Every 0.5 s, the band of the ticker card is read by EasyOCR (CPU, vi+en, as the
   text scan) in the window copy and in both exports: letters read, whether a reading
   of at least LETTERS letters remains, whether a web address remains.

Reads only the sources, the copied queues and models/easyocr; writes only under --work,
a new folder in <install>/temp. Prints job ids, seconds and counts: no film names,
file names, OCR text or links.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import psutil

psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)  # FFmpeg and OCR inherit it

INSTALL = Path(r"E:\DungChung\BiliFlow")
FFMPEG = INSTALL / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
FFPROBE = INSTALL / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"
LEAD, TRAIL = 6.0, 4.0  # seconds of the window before the card's start and after its end
STEP = 0.5
LETTERS = 6

PLAN_PROGRAM = r"""
import json, sys
from pathlib import Path
import biliflow
from biliflow.review_workflow import approved_operations, record_review_decision
job = json.loads(sys.argv[1])
queue = Path(job["queue"])
payload = json.loads(queue.read_text(encoding="utf-8"))
decided = []
for item in payload["items"]:
    if job["decide_scrolling"] and item.get("candidate_type") == "scrolling_text" and item.get("decision") is None:
        record_review_decision(project_root=Path(job["root"]), queue_path=queue, item_id=item["id"], decision="BLUR",
                               note="gate: the user's BLUR on the ticker card", transport="gate")
        decided.append(item["id"])
payload = json.loads(queue.read_text(encoding="utf-8"))
cards = [{"start": item["start_seconds"], "end": item["end_seconds"],
          "region": item.get("decision_region_source_pixels") or item.get("suggested_region_source_pixels"),
          "frame": item.get("source_frame_size")}
         for item in payload["items"] if item.get("candidate_type") == "scrolling_text"]
print(json.dumps({"code": biliflow.__file__, "operations": approved_operations(payload), "source": payload["source"],
                  "scrolling_cards": cards, "decided_now": len(decided)}))
"""

RENDER_PROGRAM = r"""
import json, sys
from pathlib import Path
import biliflow
from biliflow import final_renderer
job = json.loads(sys.argv[1])
real = final_renderer.run_watched
def two_threads(command, **kwargs):
    return real([*command[:-1], "-threads", "2", command[-1]], **kwargs)
final_renderer.run_watched = two_threads
manifest = final_renderer.render_final_output(
    project_root=Path(job["root"]), plan_path=Path(job["plan"]), output_path=Path(job["output"]),
    ffmpeg_path=Path(job["ffmpeg"]), ffprobe_path=Path(job["ffprobe"]))
print(json.dumps({"code": biliflow.__file__, "status": manifest["status"],
                  "encoder": manifest["encoding"].get("encoder"),
                  "full_decode_validation_passed": manifest["encoding"].get("full_decode_validation_passed")}))
"""


def run_tree(tree: Path, program: str, job: dict) -> dict:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("BILIFLOW_")}
    environment.update(PYTHONPATH=str(tree), TEMP=job["root"], TMP=job["root"], PYTHONDONTWRITEBYTECODE="1",
                       PYTHONIOENCODING="utf-8")
    done = subprocess.run([sys.executable, "-c", program, json.dumps(job)], env=environment, cwd=job["root"],
                          capture_output=True, text=True, encoding="utf-8", timeout=3600)
    if done.returncode:
        raise RuntimeError(done.stderr[-1500:])
    result = json.loads(done.stdout.strip().splitlines()[-1])
    if tree not in Path(result.pop("code")).resolve().parents:
        raise RuntimeError(f"biliflow was not imported from {tree}")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def queue_folder(job_id: int) -> Path:
    database = (INSTALL / "state" / "control-center.sqlite3").as_posix()
    connection = sqlite3.connect(f"file:///{database}?mode=ro", uri=True)
    try:
        [queue] = connection.execute("select active_queue_path from jobs where id = ?", (job_id,)).fetchone()
    finally:
        connection.close()
    return Path(queue).parent  # reports/jobs/<job_key>, relative to the install


def window_clip(root: Path, job_id: int, source: Path, window: tuple[float, float]) -> tuple[Path, float]:
    clip = root / "input" / f"job-{job_id}-window.mp4"
    subprocess.run(
        [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{window[0]:.3f}", "-i", str(source),
         "-t", f"{window[1] - window[0]:.3f}", "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-qp", "0",
         "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-threads", "2", "-c:a", "aac", "-b:a", "192k", str(clip)],
        capture_output=True, timeout=1800, check=True,
    )
    probe = json.loads(subprocess.run([str(FFPROBE), "-v", "error", "-show_format", "-of", "json", str(clip)],
                                      capture_output=True, text=True, timeout=120, check=True).stdout)
    return clip, float(probe["format"]["duration"])


def window_plan(root: Path, label: str, operations: list[dict], clip: Path, duration: float, offset: float) -> Path:
    moved = []
    for operation in operations:
        start, end = float(operation["start_seconds"]) - offset, float(operation["end_seconds"]) - offset
        if end > 0 and start < duration:
            moved.append({**operation, "start_seconds": max(0.0, start), "end_seconds": min(duration, end)})
    path = root / "work" / f"edit-plan-{label}.json"
    path.write_text(json.dumps({
        "status": "READY_FOR_FINAL_RENDER", "final_export_allowed": True,
        "source": {"path": str(clip), "duration_seconds": duration, "sha256": sha256(clip)},
        "approved_operations": moved, "render_settings": {"video_encoder": "libx264"},
    }, indent=1), encoding="utf-8")
    return path


def band_frames(video: Path, band: tuple[int, int], width: int) -> list[bytes]:
    """RGB rows of the band, every STEP seconds from the window's start."""
    top, bottom = band
    raw = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-vf", f"fps={1 / STEP:g},crop={width}:{bottom - top}:0:{top}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, timeout=1800, check=True).stdout
    size = width * (bottom - top) * 3
    return [raw[offset:offset + size] for offset in range(0, len(raw) - size + 1, size)]


def readings(reader, frame: bytes, band: tuple[int, int], width: int) -> dict:
    import numpy
    from biliflow.scrolling_text import fold_alnum, web_address_tokens
    image = numpy.frombuffer(frame, dtype=numpy.uint8).reshape(band[1] - band[0], width, 3)
    texts = [str(text) for _, text, confidence in reader.readtext(image) if float(confidence) >= 0.3]
    folded = [fold_alnum(text) for text in texts]
    return {"letters": sum(len(value) for value in folded),
            "readable": any(len(value) >= LETTERS for value in folded),
            "web_address": any(web_address_tokens(text) for text in texts)}


def check_job(args, job_id: int, reader) -> dict:
    work = Path(args.work)
    gate = Path(args.gate_work)
    candidate = Path(args.candidate).resolve(strict=True)
    folder = queue_folder(job_id)
    root = work / f"job-{job_id}"
    for name in ("input", "work", "output", "state", "reports/jobs"):
        (root / name).mkdir(parents=True, exist_ok=True)
    plans = {}
    for label, side, decide in (("before", "baseline", False), ("after", "candidate", True)):
        target = root / "reports" / "jobs" / f"{label}-{job_id}"
        shutil.copytree(gate / side / folder, target)
        plans[label] = run_tree(candidate, PLAN_PROGRAM, {
            "root": str(root), "queue": str(target / "review-queue.json"), "decide_scrolling": decide})
    [card] = plans["after"]["scrolling_cards"]
    window = (max(0.0, card["start"] - LEAD), card["end"] + TRAIL)
    clip, duration = window_clip(root, job_id, Path(plans["after"]["source"]["path"]), window)
    report = {"job": job_id, "window_film_seconds": [round(window[0], 3), round(window[0] + duration, 3)],
              "card": {"start": card["start"], "end": card["end"], "region": card["region"]},
              "decided_now": plans["after"]["decided_now"], "exports": {}}
    videos = {"source": clip}
    for label in ("before", "after"):
        plan = window_plan(root, label, plans[label]["operations"], clip, duration, window[0])
        output = root / "output" / f"job-{job_id}-window-{label}.mp4"
        report["exports"][label] = run_tree(candidate, RENDER_PROGRAM, {
            "root": str(root), "plan": str(plan), "output": str(output), "ffmpeg": str(FFMPEG),
            "ffprobe": str(FFPROBE)})
        report["exports"][label]["operations_in_window"] = len(json.loads(plan.read_text(encoding="utf-8"))
                                                               ["approved_operations"])
        videos[label] = output
    width = int(card["frame"][0])
    region = card["region"]
    band = (max(0, int(region["y"]) - 4) // 2 * 2, (int(region["y"]) + int(region["height"]) + 4) // 2 * 2)
    frames = {label: band_frames(video, band, width) for label, video in videos.items()}
    rows = []
    for index in range(min(len(values) for values in frames.values())):
        film_seconds = round(window[0] + index * STEP, 2)
        rows.append({"film_seconds": film_seconds,
                     "in_card": card["start"] <= film_seconds <= card["end"],
                     **{label: readings(reader, frames[label][index], band, width) for label in frames}})
    report["samples"] = rows

    def seconds(label: str, key: str) -> list[float]:
        return [row["film_seconds"] for row in rows if row[label][key]]

    report["summary"] = {
        label: {"readable_seconds": seconds(label, "readable"), "web_address_seconds": seconds(label, "web_address"),
                "max_letters_in_card": max((row[label]["letters"] for row in rows if row["in_card"]), default=0)}
        for label in ("source", "before", "after")
    }
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
        print(f"job {result['job']}: card {result['card']['start']}-{result['card']['end']} s, window "
              f"{result['window_film_seconds']}", flush=True)
        for label, summary in result["summary"].items():
            print(f"  {label}: readable at {summary['readable_seconds']}, web address at "
                  f"{summary['web_address_seconds']}, most letters in the card {summary['max_letters_in_card']}",
                  flush=True)


if __name__ == "__main__":
    main()
