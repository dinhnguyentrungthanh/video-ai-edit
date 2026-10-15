"""fix/cover-edge real check, step 2: job 141 before (11a059c) and after (the branch).

Runs after job141_prepare.py (the plan in <install>/temp/ce-job141). Reads the
source film only; writes only <install>/temp/ce-job141. Prints JSON (job id only).

1. The whole film's export graph, each code tree's, through FFmpeg on the real
   source for 1 s of output: 11a059c's is refused by delogo, the branch's runs.
2. The real render_final_output on 2140-2170 s: a lossless x264 copy of that half
   minute (qp 0, the source's yuv420p) and the plan's blur moved by -2140 s. Both
   trees; then the branch's export against the copy: how much of the source's fine
   detail is left inside the region and on the frame's last columns.
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)  # FFmpeg inherits it

INSTALL = Path(r"E:\DungChung\BiliFlow")
CANDIDATE = INSTALL / "temp" / "wt-cover-edge" / "src"
BASELINE = Path(sys.argv[1]).resolve(strict=True)  # git archive of 11a059c's src
ROOT = INSTALL / "temp" / "ce-job141"
FFMPEG = INSTALL / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
FFPROBE = INSTALL / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"
WINDOW = (2140.0, 2170.0)
WIDTH, HEIGHT = 1280, 534

GRAPH_PROGRAM = """
import inspect, json, sys
from pathlib import Path
import biliflow
from biliflow import final_renderer
job = json.loads(sys.argv[1])
plan = json.loads(Path(job["plan"]).read_text(encoding="utf-8"))
extra = {}
if "source_chroma" in inspect.signature(final_renderer._final_filter_graph).parameters:
    from biliflow.blur_filter import SourceChroma
    extra["source_chroma"] = SourceChroma.of("yuv420p")
graph, expected, wiring = final_renderer._final_filter_graph(
    operations=plan["approved_operations"], duration=float(plan["source"]["duration_seconds"]),
    has_audio=True, frame_size=(1280, 534), chroma_rows_divisor=2, **extra)
Path(job["graph"]).write_text(graph, encoding="utf-8")
print(json.dumps({"code": biliflow.__file__}))
"""

RENDER_PROGRAM = """
import json, sys
from pathlib import Path
import biliflow
from biliflow import final_renderer
job = json.loads(sys.argv[1])
real = final_renderer.run_watched
def two_threads(command, **kwargs):
    # A scan or an export of the Control Center may run beside this check.
    return real([*command[:-1], "-threads", "2", command[-1]], **kwargs)
final_renderer.run_watched = two_threads
try:
    manifest = final_renderer.render_final_output(
        project_root=Path(job["root"]), plan_path=Path(job["plan"]), output_path=Path(job["output"]),
        ffmpeg_path=Path(job["ffmpeg"]), ffprobe_path=Path(job["ffprobe"]))
    print(json.dumps({"code": biliflow.__file__, "status": manifest["status"],
                      "duration": manifest["output"]["duration_seconds"],
                      "encoder": manifest["encoding"].get("encoder"),
                      "filter_graph": manifest["encoding"].get("filter_graph"),
                      "full_decode_validation_passed": manifest["encoding"].get("full_decode_validation_passed")}))
except Exception as error:
    print(json.dumps({"code": biliflow.__file__, "error": str(error)[-600:]}))
"""


def run_tree(tree: Path, program: str, job: dict) -> dict:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("BILIFLOW_")}
    environment.update(PYTHONPATH=str(tree), TEMP=str(ROOT), TMP=str(ROOT), PYTHONDONTWRITEBYTECODE="1",
                       PYTHONIOENCODING="utf-8")
    done = subprocess.run([sys.executable, "-c", program, json.dumps(job)], env=environment, cwd=ROOT,
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


def whole_film_graphs(plan_path: Path, source: Path) -> dict:
    """Each tree's graph of the whole film, through FFmpeg for 1 s: FFmpeg sets every filter up first."""
    results = {}
    for label, tree in (("baseline_11a059c", BASELINE), ("branch", CANDIDATE)):
        graph = ROOT / "work" / f"whole-film-graph-{label}.txt"
        run_tree(tree, GRAPH_PROGRAM, {"plan": str(plan_path), "graph": str(graph)})
        done = subprocess.run(
            [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-i", str(source), "-/filter_complex", str(graph),
             "-map", "[vout]", "-map", "[aout]", "-t", "1", "-threads", "2", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        )
        text = graph.read_text(encoding="utf-8")
        results[label] = {
            "ffmpeg_exit_code": done.returncode,
            "refused_by_delogo": "Logo area is outside of the frame" in done.stderr,
            "cover_chain": next(part for part in text.split(";") if "delogo" in part)[:260],
        }
    return results


def window_plan(plan: dict) -> Path:
    """The plan's operations within WINDOW, on a lossless copy of that half minute of the source."""
    clip = ROOT / "input" / "job-141-2140-2170.mp4"
    subprocess.run(
        [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{WINDOW[0]:g}", "-i", plan["source"]["path"],
         "-t", f"{WINDOW[1] - WINDOW[0]:g}", "-map", "0:v:0", "-map", "0:a:0", "-c:v", "libx264", "-qp", "0",
         "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-threads", "2", "-c:a", "aac", "-b:a", "192k", str(clip)],
        capture_output=True, timeout=1800, check=True,
    )
    probe = json.loads(subprocess.run(
        [str(FFPROBE), "-v", "error", "-show_format", "-of", "json", str(clip)],
        capture_output=True, text=True, timeout=120, check=True).stdout)
    duration = float(probe["format"]["duration"])
    operations = []
    for item in plan["approved_operations"]:
        start, end = float(item["start_seconds"]) - WINDOW[0], float(item["end_seconds"]) - WINDOW[0]
        if end <= 0 or start >= duration:
            continue
        operations.append({**item, "start_seconds": max(0.0, start), "end_seconds": min(duration, end)})
    path = ROOT / "work" / "edit-plan-2140-2170.json"
    path.write_text(json.dumps({
        "status": "READY_FOR_FINAL_RENDER", "final_export_allowed": True,
        "source": {"path": str(clip), "duration_seconds": duration, "sha256": sha256(clip)},
        "approved_operations": operations,
        "render_settings": {"video_encoder": "libx264"},
    }, indent=1), encoding="utf-8")
    return path


def luma(video: Path, seconds: float) -> bytes:
    frame = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-ss", f"{seconds:.3f}", "-i", str(video),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, timeout=300, check=True).stdout
    assert len(frame) == WIDTH * HEIGHT, len(frame)
    return frame


def kept(source: bytes, output: bytes, pixels: list[tuple[int, int]], *, line: bool) -> dict:
    """Share of the source's fine detail the export keeps at ``pixels``, and the source's detail per pixel."""
    def high_pass(frame):
        if line:
            values = [frame[y * WIDTH + x] for x, y in pixels]
            return [values[k] - (values[k - 1] + values[k] + values[k + 1]) / 3 for k in range(1, len(values) - 1)]
        return [frame[y * WIDTH + x] - sum(frame[(y + dy) * WIDTH + x + dx] for dy in (-1, 0, 1)
                                           for dx in (-1, 0, 1)) / 9 for x, y in pixels]

    a, b = high_pass(source), high_pass(output)
    energy = sum(value * value for value in a)
    return {"kept": round(sum(p * q for p, q in zip(a, b)) / energy, 3) if energy else None,
            "source_detail_per_pixel": round(energy / max(1, len(a)), 1)}


def coverage(clip: Path, output: Path, region: dict, on: tuple[float, float]) -> list[dict]:
    x0, y0 = region["x"], region["y"]
    x1, y1 = x0 + region["width"], y0 + region["height"]
    rows = range(36, 82)  # the patch is laid from y=34; its top and bottom 2 rows fade into delogo's picture
    checks = []
    for seconds in (on[0] + 1.0, (on[0] + on[1]) / 2, on[1] - 1.0, on[0] - 3.0, on[1] + 3.0):
        source, exported = luma(clip, seconds), luma(output, seconds)
        state = "on" if on[0] <= seconds <= on[1] else "off"
        checks.append({
            "seconds_in_window": round(seconds, 2), "film_seconds": round(seconds + WINDOW[0], 2), "blur": state,
            "inside_4px_in": kept(source, exported, [(x, y) for y in range(y0 + 4, y1 - 4, 2)
                                                     for x in range(x0 + 4, x1 - 4, 2)], line=False),
            "frame_columns": {str(column): kept(source, exported, [(column, y) for y in rows], line=True)
                              for column in (1279, 1278, 1277, 1276, 1272)},
            "column_581_left_of_region": kept(source, exported, [(581, y) for y in rows], line=True),
            "row_32_above_region": kept(source, exported, [(x, 32) for x in range(x0, x1)], line=True),
        })
    return checks


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # the report may hold Vietnamese messages
    plan_path = ROOT / "work" / "edit-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    report = {"job": 141, "whole_film_graph_first_second": whole_film_graphs(plan_path, Path(plan["source"]["path"]))}
    window = window_plan(plan)
    report["window_operations"] = json.loads(window.read_text(encoding="utf-8"))["approved_operations"]
    exports = {}
    for label, tree in (("baseline_11a059c", BASELINE), ("branch", CANDIDATE)):
        output = ROOT / "output" / f"job-141-2140-2170-{label}.mp4"
        started = time.monotonic()
        exports[label] = run_tree(tree, RENDER_PROGRAM, {
            "root": str(ROOT), "plan": str(window), "output": str(output), "ffmpeg": str(FFMPEG),
            "ffprobe": str(FFPROBE)})
        exports[label]["seconds"] = round(time.monotonic() - started, 1)
    report["window_exports"] = exports
    (operation,) = report["window_operations"]
    branch_output = ROOT / "output" / "job-141-2140-2170-branch.mp4"
    report["coverage"] = coverage(ROOT / "input" / "job-141-2140-2170.mp4", branch_output,
                                  operation["region_source_pixels"],
                                  (operation["start_seconds"], operation["end_seconds"]))
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
