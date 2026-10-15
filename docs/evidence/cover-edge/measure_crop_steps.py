"""The step FFmpeg's crop rounds a segment's first regional branch to, for every pixel format (fix/cover-edge).

For each pixel format the install's FFmpeg knows (hardware formats excepted): one
frame of 160 x 90 testsrc2 in that format through the export's own graph
(final_renderer._final_filter_graph) with one interior cover region whose crop
asks 39 x 23 pixels, alone and after a full-frame blur, with -pix_fmt yuv420p as
the export. FFmpeg's verbose log of the first crop gives the size it made:
39 -> 39, 38 or 36 is a step of 1, 2 or 4. Also records whether the format reaches
the crop unchanged (no conversion before it). A format swscale cannot make comes
from paletteuse (pal8) or from raw bytes (rawvideo). Below-normal priority, 1
frame per run; writes only a temp folder under <install>/temp, deleted at the end,
and prints JSON lines.

  python docs/evidence/cover-edge/measure_crop_steps.py > crop-steps.jsonl
  python docs/evidence/cover-edge/measure_crop_steps.py --mode transformed > crop-steps-transformed.jsonl
  python docs/evidence/cover-edge/measure_crop_steps.py --mode preview > crop-steps-preview.jsonl

--mode transformed: the same on a 160 x 160 source carrying a display matrix
(-display_rotation 90, 180, 270 and 30, -display_hflip, -display_vflip): FFmpeg
then puts transpose, hflip/vflip or rotate in front of the export's graph; and
small angles (0.4, 1, -1, 2 and 89.6 degrees), where fftools rounds the angle to
whole degrees and inserts a filter only beyond 1 degree, at 1 degree it reads as 359.
--mode preview: the command of review_workflow.render_edit_previews (-ss/-t before
-i, regional_blur_filters on [0:v], the default encoder of an .mp4).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import psutil

psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)  # FFmpeg inherits it

CHECKOUT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(CHECKOUT / "src"))
from biliflow.blur_filter import NO_ROUNDING, regional_blur_filters  # noqa: E402
from biliflow.final_renderer import _final_filter_graph  # noqa: E402

INSTALL = Path(r"E:\DungChung\BiliFlow")
FFMPEG = INSTALL / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
REGION = {"x": 60, "y": 40, "width": 23, "height": 7}  # crop 39 x 23 at 52, 32; 8 px of margin everywhere
TRANSFORMS = {
    "rotate-90": ["-display_rotation", "90"], "rotate-180": ["-display_rotation", "180"],
    "rotate-270": ["-display_rotation", "270"], "rotate-30": ["-display_rotation", "30"],
    "hflip": ["-display_hflip"], "vflip": ["-display_vflip"],
    "rotate-0.4": ["-display_rotation", "0.4"], "rotate-1": ["-display_rotation", "1"],
    "rotate-minus-1": ["-display_rotation", "-1"], "rotate-2": ["-display_rotation", "2"],
    "rotate-89.6": ["-display_rotation", "89.6"],
}
CROP = re.compile(r"\[Parsed_crop_(\d+) @ [^\]]+\] w:\d+ h:\d+ (?:sar:\S+ )?-> w:(\d+) h:(\d+)")
RAW_FRAME_BYTES = 160 * 160 * 16  # more than one frame of any format


def blur(region, sigma: float, end: float = 1.0) -> dict:
    return {"id": f"b{sigma:g}", "type": "blur", "start_seconds": 0.0, "end_seconds": end,
            "region_source_pixels": region,
            "blur": {"sigma": sigma, "edge_feather_pixels": 2, "edge_feather_mode": "all_edges",
                     "method": "delogo_blur_v1" if region != "FULL_FRAME" else "gblur"}}


def pixel_formats() -> list[tuple[str, str]]:
    listing = subprocess.run([str(FFMPEG), "-hide_banner", "-pix_fmts"], capture_output=True, text=True,
                             timeout=60, check=True).stdout
    formats = []
    for line in listing.splitlines():
        match = re.match(r"^([IOHPB.]{5}) (\S+)\s+\d+\s+\d+", line)
        if match:
            formats.append((match[2], match[1]))
    return formats


def source_arguments(name: str, flags: str, frame: tuple[int, int], folder: Path) -> tuple[list[str], str]:
    """The input of one frame in format ``name`` (testsrc2, paletteuse or raw bytes) and how it was made."""
    size = f"{frame[0]}x{frame[1]}"
    if name == "pal8":
        return ["-f", "lavfi", "-i", f"testsrc2=s={size}:r=25:d=0.12,split[a][b];[a]palettegen[p];[b][p]paletteuse"], \
            "paletteuse"
    if flags[1] == "O":
        return ["-f", "lavfi", "-i", f"testsrc2=s={size}:r=25:d=0.12,format=yuv420p,format={name}"], "testsrc2"
    raw = folder / "raw.bin"
    if not raw.exists():
        raw.write_bytes(os.urandom(RAW_FRAME_BYTES * 3))
    return ["-f", "rawvideo", "-pixel_format", name, "-video_size", size, "-framerate", "25", "-i", str(raw)], \
        "rawvideo"


def step(asked: int, made: int) -> int | None:
    return {0: 1, 1: 2, 3: 4}.get(asked - made)


def first_crop(command: list[str]) -> dict:
    done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    crops = sorted((int(index), int(width), int(height)) for index, width, height in CROP.findall(done.stderr))
    if done.returncode or not crops:
        return {"error": (done.stderr.strip().splitlines() or ["?"])[-1][:200]}
    _, width, height = crops[0]
    converted = re.search(r"auto-inserting filter '[^']+' between the filter '[^']*' and the filter 'Parsed_crop_",
                          done.stderr) is not None
    return {"step": [step(39, width), step(23, height)], "made": [width, height], "converted_before_crop": converted}


def export_run(graph: str, inputs: list[str], folder: Path) -> dict:
    script = folder / "graph.txt"
    script.write_text(graph, encoding="utf-8")
    return first_crop([str(FFMPEG), "-hide_banner", "-nostdin", "-v", "verbose", *inputs,
                       "-/filter_complex", str(script), "-map", "[vout]", "-frames:v", "1", "-pix_fmt", "yuv420p",
                       "-f", "null", "-"])


def export_graphs(frame: tuple[int, int]) -> tuple[str, str]:
    alone = _final_filter_graph(operations=[blur(REGION, 28)], duration=1.0, has_audio=False,
                                frame_size=frame, chroma_rows_divisor=2)[0]
    after = _final_filter_graph(operations=[blur("FULL_FRAME", 10), blur(REGION, 28)], duration=1.0,
                                has_audio=False, frame_size=frame, chroma_rows_divisor=2)[0]
    # The same start and length: the full-frame blur stays first, as listed.
    assert "exact" not in alone + after and "crop=39:23:52:32" in alone and "crop=39:23:52:32" in after
    assert after.index("gblur=sigma=10:") < after.index("crop=39:23:52:32")
    return alone, after


def preview_graph(frame: tuple[int, int]) -> str:
    graph = ";".join(regional_blur_filters(
        input_label="0:v", output_label="v", prefix="preview", region=REGION, sigma=28, feather=2,
        feather_mode="all_edges", enable="enable='between(t,0.000,0.120)'", method="delogo_blur_v1",
        frame_size=frame, chroma_step=NO_ROUNDING))
    assert "exact" not in graph and "crop=39:23:52:32" in graph
    return graph


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("export", "transformed", "preview"), default="export")
    parser.add_argument("--only", nargs="*", help="pixel formats (default: all)")
    args = parser.parse_args()
    mode = args.mode
    frame = (160, 160) if mode == "transformed" else (160, 90)
    folder = Path(tempfile.mkdtemp(prefix="ce-crop-steps-", dir=INSTALL / "temp"))
    try:
        if mode == "preview":
            graph = preview_graph(frame)
        else:
            alone, after = export_graphs(frame)
        for name, flags in pixel_formats():
            if args.only and name not in args.only:
                continue
            if flags[2] == "H":
                print(json.dumps({"pix_fmt": name, "flags": flags, "skipped": "hardware format"}), flush=True)
                continue
            inputs, made_by = source_arguments(name, flags, frame, folder)
            row = {"pix_fmt": name, "flags": flags, "source": made_by}
            if mode == "export":
                row.update(alone=export_run(alone, inputs, folder), after_full_frame_blur=export_run(after, inputs, folder))
            elif mode == "transformed":
                for transform, arguments in TRANSFORMS.items():
                    row[transform] = {"alone": export_run(alone, [*arguments, *inputs], folder),
                                      "after_full_frame_blur": export_run(after, [*arguments, *inputs], folder)}
            else:
                output = folder / "preview.mp4"
                row["preview"] = first_crop([
                    str(FFMPEG), "-hide_banner", "-nostdin", "-v", "verbose", "-y", "-ss", "0.000", "-t", "0.120",
                    *inputs, "-filter_complex", graph, "-map", "[v]", "-frames:v", "1", str(output)])
            print(json.dumps(row), flush=True)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
