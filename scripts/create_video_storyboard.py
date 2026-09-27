from __future__ import annotations

import argparse
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=4.0)
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--columns", type=int, default=5)
    parser.add_argument("--rows", type=int, default=5)
    args = parser.parse_args()

    input_path = args.input.resolve(strict=True)
    ffmpeg_path = args.ffmpeg.resolve(strict=True)
    output_dir = args.output_dir.resolve()
    frames_dir = output_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-y",
        "-ss", str(args.start), "-i", str(input_path),
    ]
    if args.duration is not None:
        command.extend(["-t", str(args.duration)])
    command.extend([
        "-vf", f"fps=1/{args.interval},scale=320:-2", "-q:v", "4",
        str(frames_dir / "frame-%05d.jpg"),
    ])
    subprocess.run(command, check=True)

    paths = sorted(frames_dir.glob("frame-*.jpg"))
    per_sheet = args.columns * args.rows
    for sheet_index in range(math.ceil(len(paths) / per_sheet)):
        current = paths[sheet_index * per_sheet : (sheet_index + 1) * per_sheet]
        with Image.open(current[0]) as first:
            width, height = first.size
        label_height = 24
        sheet = Image.new(
            "RGB", (args.columns * width, args.rows * (height + label_height)), "black"
        )
        draw = ImageDraw.Draw(sheet)
        for local_index, path in enumerate(current):
            global_index = sheet_index * per_sheet + local_index
            timestamp = args.start + global_index * args.interval
            with Image.open(path) as source:
                image = source.convert("RGB")
            x = (local_index % args.columns) * width
            y = (local_index // args.columns) * (height + label_height)
            sheet.paste(image, (x, y))
            minutes, seconds = divmod(timestamp, 60)
            draw.text((x + 5, y + height + 4), f"{minutes:02.0f}:{seconds:04.1f}", fill="white")
        sheet.save(output_dir / f"storyboard-{sheet_index + 1:02d}.jpg", quality=88)
        sheet.close()
    print(f"frames={len(paths)} sheets={math.ceil(len(paths) / per_sheet)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
