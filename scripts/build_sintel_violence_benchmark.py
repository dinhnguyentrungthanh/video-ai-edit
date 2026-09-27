from __future__ import annotations

import csv
import hashlib
import subprocess
from pathlib import Path


CALIBRATION_POSITIVE = (
    43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54,
    55, 56, 57, 63, 64, 65, 68, 69, 70, 71, 72, 73,
)
HOLDOUT_POSITIVE = (
    539, 540, 559, 560, 565, 566, 567, 568, 569, 579, 580, 581,
    582, 583, 584, 585, 586, 587, 588, 589, 590, 597, 600, 601,
    602, 605, 606, 607,
)
CALIBRATION_NEGATIVE = (
    100, 110, 120, 130, 140, 150, 180, 190, 198, 210,
    220, 230, 245, 260, 270, 280, 290, 300, 310, 350,
    370, 390, 410, 430, 450, 480, 490, 500, 520, 530,
)
HOLDOUT_NEGATIVE = (
    75, 85, 95, 160, 170, 240, 250, 320, 330, 340,
    360, 380, 400, 420, 440, 460, 470, 510, 525, 535,
    620, 630, 640, 650, 660, 680, 700, 720, 740, 760,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    source = root / "input" / "Sintel-1024-surround.mp4"
    ffmpeg = root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    output = root / "benchmarks" / "violence-sintel-v1"
    source_digest = sha256(source)
    groups = (
        ("calibration", 1, CALIBRATION_POSITIVE, "opening human fight"),
        ("holdout", 1, HOLDOUT_POSITIVE, "final dragon fight"),
        ("calibration", 0, CALIBRATION_NEGATIVE, "non-action and hard negative"),
        ("holdout", 0, HOLDOUT_NEGATIVE, "independent non-action holdout"),
    )
    rows = []
    for split, label, timestamps, note in groups:
        polarity = "positive" if label else "negative"
        directory = output / split / polarity
        directory.mkdir(parents=True, exist_ok=True)
        for timestamp in timestamps:
            sample_id = f"sintel-{split}-{polarity}-{timestamp:04d}s"
            target = directory / f"{sample_id}.jpg"
            if not target.exists():
                subprocess.run(
                    [
                        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                        "-ss", str(timestamp), "-i", str(source), "-frames:v", "1",
                        "-q:v", "3", str(target),
                    ],
                    check=True,
                )
            rows.append(
                {
                    "sample_id": sample_id,
                    "image_path": target.relative_to(root).as_posix(),
                    "style": "animation",
                    "label": label,
                    "split": split,
                    "timestamp_seconds": timestamp,
                    "source": "Sintel open movie",
                    "source_sha256": source_digest,
                    "sha256": sha256(target),
                    "notes": note,
                }
            )
    manifest = root / "annotations" / "violence_sintel_benchmark_v1.csv"
    with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    video_rows = []
    for row in rows:
        split = row["split"]
        polarity = "positive" if row["label"] else "negative"
        clip_dir = output / "clips" / split / polarity
        clip_dir.mkdir(parents=True, exist_ok=True)
        clip = clip_dir / f"{row['sample_id']}.mp4"
        if not clip.exists():
            subprocess.run(
                [
                    str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", str(max(0.0, float(row["timestamp_seconds"]) - 1.0)),
                    "-i", str(source), "-t", "2.2", "-an", "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "25", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", str(clip),
                ],
                check=True,
            )
        video_rows.append(
            {
                "sample_id": row["sample_id"],
                "video_path": clip.relative_to(root).as_posix(),
                "style": row["style"],
                "label": row["label"],
                "split": split,
                "source": row["source"],
                "timestamp_seconds": row["timestamp_seconds"],
                "notes": row["notes"],
            }
        )
    video_manifest = root / "annotations" / "violence_sintel_video_benchmark_v1.csv"
    with video_manifest.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(video_rows[0]))
        writer.writeheader()
        writer.writerows(video_rows)
    print(
        f"manifest={manifest} video_manifest={video_manifest} samples={len(rows)} "
        f"positive={sum(row['label'] for row in rows)} "
        f"negative={sum(not row['label'] for row in rows)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
