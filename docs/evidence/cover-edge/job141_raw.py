"""fix/cover-edge real check, step 3: the branch's window graph without the encoder.

The x264 export blurs the macroblocks beside a blur a little too; this compares
the graph's own output (rawvideo luma) with the lossless copy of the source at
moments while the blur is on: which pixels changed, and how much detail is left
on the frame's last columns. Reads <install>/temp/ce-job141 only; prints JSON.
"""
import json
import subprocess
import sys
from pathlib import Path

import psutil

psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)

INSTALL = Path(r"E:\DungChung\BiliFlow")
ROOT = INSTALL / "temp" / "ce-job141"
FFMPEG = INSTALL / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
sys.path.insert(0, str(INSTALL / "temp" / "wt-cover-edge" / "src"))
WIDTH, HEIGHT = 1280, 534

from biliflow import final_renderer  # noqa: E402
from biliflow.blur_filter import SourceChroma  # noqa: E402


def frame(arguments: list[str]) -> bytes:
    data = subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", *arguments],
                          capture_output=True, timeout=600, check=True).stdout
    assert len(data) == WIDTH * HEIGHT, len(data)
    return data


def main() -> None:
    plan = json.loads((ROOT / "work" / "edit-plan-2140-2170.json").read_text(encoding="utf-8"))
    clip = plan["source"]["path"]
    graph, _, _ = final_renderer._final_filter_graph(
        operations=plan["approved_operations"], duration=float(plan["source"]["duration_seconds"]),
        has_audio=False, frame_size=(WIDTH, HEIGHT), chroma_rows_divisor=2,  # the same video chains
        source_chroma=SourceChroma.of("yuv420p"))
    graph_file = ROOT / "work" / "window-graph-branch.txt"
    graph_file.write_text(graph, encoding="utf-8")
    (operation,) = plan["approved_operations"]
    region = operation["region_source_pixels"]
    x0, y0, x1, y1 = region["x"], region["y"], region["x"] + region["width"], region["y"] + region["height"]
    results = []
    for seconds in (12.0, 14.25, 16.5):
        source = frame(["-ss", f"{seconds:.3f}", "-i", clip, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"])
        output = frame(["-i", clip, "-/filter_complex", str(graph_file), "-map", "[vout]",
                        "-ss", f"{seconds:.3f}", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"])
        changed = [(x, y) for y in range(HEIGHT) for x in range(WIDTH)
                   if source[y * WIDTH + x] != output[y * WIDTH + x]]
        outside = [(x, y) for x, y in changed if not (x0 - 1 <= x < x1 and y0 - 1 <= y < y1)]
        columns = {}
        for column in (1279, 1278, 1277, 1276):
            values = [(source[y * WIDTH + column], output[y * WIDTH + column]) for y in range(36, 82)]
            same = sum(a == b for a, b in values)
            columns[str(column)] = {"rows_36_81_unchanged": same, "of": len(values)}
        region_pixels = (x1 - x0) * (y1 - y0)
        inside_changed = sum(1 for x, y in changed if x0 <= x < x1 and y0 <= y < y1)
        results.append({
            "film_seconds": round(2140 + seconds, 2),
            "changed_pixels_outside_the_region_and_its_even_pixel_left_and_above": len(outside),
            "changed_box": [min(x for x, _ in changed), min(y for _, y in changed),
                            max(x for x, _ in changed) + 1, max(y for _, y in changed) + 1] if changed else None,
            "region_pixels_changed": f"{inside_changed}/{region_pixels}",
            "frame_last_columns": columns,
        })
    print(json.dumps({"job": 141, "region": region, "raw_graph_output": results}, indent=1))


if __name__ == "__main__":
    main()
