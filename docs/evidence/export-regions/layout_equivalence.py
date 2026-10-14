"""Nested vs shared layout: framemd5 of the same random plan both ways, per source pixel format.

Usage: python layout_equivalence.py <src_dir> <plans> <seed> <pix_fmt> [pix_fmt ...]
Forces each layout by changing blur_layout.NESTED_PATH_LIMIT (0: shared, huge: nested).
"""
import hashlib
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from biliflow import blur_layout  # noqa: E402
from biliflow.final_renderer import _final_filter_graph  # noqa: E402

FF = os.environ["BILIFLOW_FFMPEG"]  # the install's tools/ffmpeg/bin/ffmpeg.exe
W, H, DURATION = 320, 180, 4.0


def plan(rng: random.Random) -> list[dict]:
    ops = []
    for k in range(rng.randrange(1, 11)):
        if rng.random() < 0.1:
            region = "FULL_FRAME"
        else:
            w, h = rng.randrange(8, 140), rng.randrange(8, 70)
            # Half the regions crowd one corner so that many overlap.
            if rng.random() < 0.5:
                x, y = rng.randrange(0, 90), rng.randrange(0, 50)
            else:
                x, y = rng.randrange(0, W - w), rng.randrange(0, H - h)
            region = {"x": x, "y": y, "width": w, "height": h}
        start = round(rng.uniform(0, DURATION - 0.3), 2)
        end = round(min(DURATION, start + rng.uniform(0.2, DURATION)), 2)
        if rng.random() < 0.2:
            start, end = 0.0, DURATION
        blur = {"sigma": rng.choice([8, 28]), "edge_feather_pixels": rng.choice([0, 2, 4]),
                "edge_feather_mode": rng.choice(["all_edges", "vertical_only"])}
        if region != "FULL_FRAME":
            blur["method"] = rng.choice(["gblur", "delogo_blur_v1"])
        ops.append({"type": "blur", "id": f"b{k}", "start_seconds": start, "end_seconds": end,
                    "region_source_pixels": region, "blur": blur})
    if rng.random() < 0.3:
        start = round(rng.uniform(0.5, 2.5), 2)
        ops.append({"type": "cut", "id": "c", "start_seconds": start, "end_seconds": start + 0.6})
    return ops


def graph(ops: list[dict], limit: int, divisor: int) -> tuple[str, dict]:
    blur_layout.NESTED_PATH_LIMIT = limit
    text, _, wiring = _final_filter_graph(operations=ops, duration=DURATION, has_audio=False,
                                          frame_size=(W, H), chroma_rows_divisor=divisor)
    return text, wiring


def digest(pix_fmt: str, text: str, folder: Path) -> str:
    path = folder / "g.fg"
    path.write_text(text, encoding="utf-8")
    src = f"testsrc2=size={W}x{H}:rate=25:duration={DURATION},format={pix_fmt}"
    out = subprocess.run(
        [FF, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", src, "-/filter_complex", str(path),
         "-map", "[vout]", "-pix_fmt", "yuv420p", "-f", "framemd5", "-"],
        capture_output=True, timeout=300,
    )
    if out.returncode:
        return "ERROR " + out.stderr.decode("utf-8", "replace").strip()[-300:]
    out = out.stdout
    lines = [line for line in out.splitlines() if not line.startswith(b"#")]
    return hashlib.sha256(b"\n".join(lines)).hexdigest()


if __name__ == "__main__":
    count, rng = int(sys.argv[2]), random.Random(int(sys.argv[3]))
    formats = sys.argv[4:]
    bad = 0
    with tempfile.TemporaryDirectory() as folder:  # set TEMP to <install>/temp
        for index in range(count):
            ops = plan(rng)
            for pix_fmt in formats:
                divisor = 4 if pix_fmt == "yuv410p" else 2
                nested, _ = graph(ops, 10**18, divisor)
                shared, wiring = graph(ops, 0, divisor)
                assert wiring["layout"] == "shared", wiring
                a, b = digest(pix_fmt, nested, Path(folder)), digest(pix_fmt, shared, Path(folder))
                same = a == b
                if a.startswith("ERROR") or b.startswith("ERROR"):
                    print(index, pix_fmt, "nested:", a[:200], "| shared:", b[:200], flush=True)
                if not same or wiring["regions_reading_older_picture"]:
                    bad += not same
                    print(index, pix_fmt, "SAME" if same else "DIFFERENT", wiring, flush=True)
    print("plans", count, "formats", formats, "different", bad)
