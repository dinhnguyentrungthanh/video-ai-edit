"""fix/cover-edge real check, step 1: job 141's plan built in a temp root; what lies around 2140-2170 s.

Reads, never writes: the real control-center.sqlite3 (read-only URI), job 141's
review-queue.json and its source film. Writes only <install>/temp/ce-job141.
Prints job id, sizes and operations; no names.
"""
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import psutil

psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)

INSTALL = Path(r"E:\DungChung\BiliFlow")
WORKTREE = INSTALL / "temp" / "wt-cover-edge"
ROOT = INSTALL / "temp" / "ce-job141"
JOB_ID = 141
FFPROBE = INSTALL / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"
sys.path.insert(0, str(WORKTREE / "src"))

from biliflow import final_renderer  # noqa: E402
from biliflow.review_workflow import build_edit_plan  # noqa: E402


def queue_of_job() -> Path:
    database = (INSTALL / "state" / "control-center.sqlite3").as_posix()
    connection = sqlite3.connect(f"file:///{database}?mode=ro", uri=True)
    try:
        (queue,) = connection.execute("select active_queue_path from jobs where id=?", (JOB_ID,)).fetchone()
    finally:
        connection.close()
    return INSTALL / queue


def main() -> None:
    if ROOT.exists():
        raise SystemExit(f"{ROOT} exists: remove it first")
    for folder in ("input", "work", "output", "state", "reports/jobs/job-141"):
        (ROOT / folder).mkdir(parents=True)
    queue = ROOT / "reports" / "jobs" / "job-141" / "review-queue.json"
    shutil.copyfile(queue_of_job(), queue)
    plan_path = ROOT / "work" / "edit-plan.json"
    build_edit_plan(project_root=ROOT, queue_path=queue, plan_path=plan_path)
    final_renderer.authorize_final_from_resolved_review(project_root=ROOT, plan_path=plan_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["render_settings"] = {**(plan.get("render_settings") or {}), "video_encoder": "libx264"}
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    probe = final_renderer.probe_video(FFPROBE, Path(plan["source"]["path"]))
    video = next(item for item in probe["streams"] if item.get("codec_type") == "video")
    operations = plan["approved_operations"]
    near = [item for item in operations
            if float(item["start_seconds"]) < 2170 and float(item["end_seconds"]) > 2140]
    print(json.dumps({
        "job": JOB_ID, "duration": plan["source"]["duration_seconds"],
        "video": {key: video.get(key) for key in ("codec_name", "width", "height", "pix_fmt", "r_frame_rate")},
        "operations": len(operations),
        "cuts": [[item["start_seconds"], item["end_seconds"]] for item in operations if item["type"] == "cut"],
        "near_2140_2170": [{key: item.get(key) for key in ("id", "type", "start_seconds", "end_seconds",
                                                            "region_source_pixels", "blur")} for item in near],
        "regions_touching_right_or_bottom": [
            [item["region_source_pixels"], item["start_seconds"], item["end_seconds"], item["blur"].get("method"),
             item["blur"].get("edge_feather_pixels")]
            for item in operations if item["type"] == "blur" and isinstance(item["region_source_pixels"], dict)
            and (item["region_source_pixels"]["x"] + item["region_source_pixels"]["width"] >= int(video["width"]) - 3
                 or item["region_source_pixels"]["y"] + item["region_source_pixels"]["height"]
                 >= int(video["height"]) - 3)],
    }, indent=1))


if __name__ == "__main__":
    main()
