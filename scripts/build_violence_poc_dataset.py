from __future__ import annotations

import csv
import subprocess
from pathlib import Path


def extract(ffmpeg: Path, source: Path, timestamp: float, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-ss", str(timestamp),
        "-i", str(source), "-t", "2.2", "-an", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "25", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target),
    ], check=True)


def extract_center_frame(ffmpeg: Path, source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-ss", "1.0",
        "-i", str(source), "-frames:v", "1", "-q:v", "3", str(target),
    ], check=True)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    ffmpeg = root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    tears = root / "temp" / "tears_of_steel_720p.mov"
    conan = next((root / "input").glob("*.mp4"))
    anime_safe = root / "previews" / "conan-ad-blur-v8-lookahead-03m03s-03m38s.mp4"
    output = root / "benchmarks" / "violence-poc"
    rows = []

    groups = [
        ("live_action", 1, tears, [425, 427, 437, 439, 441, 447, 449, 451, 469, 471], "Tears of Steel verified combat/explosion sequence"),
        ("live_action", 0, tears, list(range(120, 360, 12)), "Tears of Steel dialogue/non-fight"),
        ("animation", 1, conan, [4900, 4901, 4902, 4903, 4904, 5288, 5289, 5290, 5291, 5292], "Conan injury/fire danger sequence"),
        ("animation", 0, anime_safe, [1.0 + index * 1.5 for index in range(20)], "Conan verified non-violent ad sequence"),
    ]
    for style, label, source, times, note in groups:
        polarity = "pos" if label else "neg"
        for index, timestamp in enumerate(times, start=1):
            sample_id = f"violence-{style}-{polarity}-{index:03d}"
            target = output / style / polarity / f"{sample_id}.mp4"
            extract(ffmpeg, source, timestamp, target)
            rows.append({
                "sample_id": sample_id,
                "video_path": target.relative_to(root).as_posix(),
                "style": style, "label": label,
                "source": str(source), "timestamp_seconds": timestamp, "notes": note,
            })
    manifest = root / "annotations" / "violence_poc_benchmark.csv"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    image_rows = []
    for row in rows:
        if row["style"] != "animation":
            continue
        clip = root / row["video_path"]
        frame = output / "animation" / "center_frames" / f"{row['sample_id']}.jpg"
        extract_center_frame(ffmpeg, clip, frame)
        image_rows.append({
            "sample_id": row["sample_id"], "image_path": frame.relative_to(root).as_posix(),
            "style": "animation", "label": row["label"], "source": row["source"],
            "notes": row["notes"],
        })
    image_manifest = root / "annotations" / "violence_animation_aux_benchmark.csv"
    with image_manifest.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(image_rows[0]))
        writer.writeheader(); writer.writerows(image_rows)
    print(f"WROTE {len(rows)} clips to {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
