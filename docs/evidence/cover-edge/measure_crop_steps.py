"""The step FFmpeg's crop rounds a segment's first regional branch to, for every pixel format (fix/cover-edge).

For each pixel format the install's FFmpeg can convert to: one frame of testsrc2
(160 x 90) in that format through the export's own graph
(final_renderer._final_filter_graph) with one interior cover region whose crop
asks 39 x 23 pixels, alone and after a full-frame blur. FFmpeg's verbose log of
the first crop gives the size it made: 39 -> 39, 38 or 36 is a step of 1, 2 or 4.
Also records whether the format reaches the crop unchanged (no conversion before
it). pal8, which swscale cannot make, comes from paletteuse. Below-normal priority,
1 frame per run; writes only a temp folder under <install>/temp, deleted at the end,
and prints JSON lines.

  python docs/evidence/cover-edge/measure_crop_steps.py > crop-steps.jsonl
"""
from __future__ import annotations

import json
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
from biliflow.final_renderer import _final_filter_graph  # noqa: E402

INSTALL = Path(r"E:\DungChung\BiliFlow")
FFMPEG = INSTALL / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
FRAME = (160, 90)
REGION = {"x": 60, "y": 40, "width": 23, "height": 7}  # crop 39 x 23 at 52, 32; 8 px of margin everywhere
# Sources of the formats swscale cannot make.
SPECIAL_SOURCES = {
    "pal8": f"testsrc2=s={FRAME[0]}x{FRAME[1]}:r=25:d=0.04,split[a][b];[a]palettegen[p];[b][p]paletteuse",
}
CROP = re.compile(r"\[Parsed_crop_(\d+) @ [^\]]+\] w:\d+ h:\d+ (?:sar:\S+ )?-> w:(\d+) h:(\d+)")


def blur(region, sigma: float) -> dict:
    return {"id": f"b{sigma:g}", "type": "blur", "start_seconds": 0.0, "end_seconds": 1.0,
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


def step(asked: int, made: int) -> int | None:
    return {0: 1, 1: 2, 3: 4}.get(asked - made)


def run(graph: str, pix_fmt: str, folder: Path) -> dict:
    script = folder / "graph.txt"
    script.write_text(graph, encoding="utf-8")
    source = SPECIAL_SOURCES.get(
        pix_fmt, f"testsrc2=s={FRAME[0]}x{FRAME[1]}:r=25:d=0.04,format=yuv420p,format={pix_fmt}")
    done = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-nostdin", "-v", "verbose", "-f", "lavfi", "-i", source,
         "-/filter_complex", str(script), "-map", "[vout]", "-frames:v", "1", "-pix_fmt", "yuv420p",
         "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    crops = sorted((int(index), int(width), int(height)) for index, width, height in CROP.findall(done.stderr))
    if done.returncode or not crops:
        return {"error": (done.stderr.strip().splitlines() or ["?"])[-1][:200]}
    _, width, height = crops[0]
    converted = re.search(r"auto-inserting filter '[^']+' between the filter '[^']*' and the filter 'Parsed_crop_",
                          done.stderr) is not None
    return {"step": [step(39, width), step(23, height)], "made": [width, height], "converted_before_crop": converted}


def main() -> None:
    folder = Path(tempfile.mkdtemp(prefix="ce-crop-steps-", dir=INSTALL / "temp"))
    try:
        alone = _final_filter_graph(operations=[blur(REGION, 28)], duration=1.0, has_audio=False,
                                    frame_size=FRAME, chroma_rows_divisor=2)[0]
        after = _final_filter_graph(operations=[blur("FULL_FRAME", 10), blur(REGION, 28)], duration=1.0,
                                    has_audio=False, frame_size=FRAME, chroma_rows_divisor=2)[0]
        assert "exact" not in alone + after and "crop=39:23:52:32" in alone and "crop=39:23:52:32" in after
        for name, flags in pixel_formats():
            if flags[1] != "O" and name not in SPECIAL_SOURCES:  # swscale cannot make it: no test source
                print(json.dumps({"pix_fmt": name, "flags": flags, "skipped": "no swscale output"}), flush=True)
                continue
            print(json.dumps({"pix_fmt": name, "flags": flags, "alone": run(alone, name, folder),
                              "after_full_frame_blur": run(after, name, folder)}), flush=True)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
