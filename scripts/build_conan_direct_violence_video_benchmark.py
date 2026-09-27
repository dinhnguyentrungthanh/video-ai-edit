from __future__ import annotations

import csv
import subprocess
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    source = next((root / "input").glob("*Conan*.mp4"))
    ffmpeg = root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    audit = root / "annotations" / "violence_field_audit_v3.csv"
    output = root / "benchmarks" / "violence-conan-reviewed-v1"
    rows = []
    with audit.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["review_label"] not in {"POSITIVE", "NEGATIVE"}:
                continue
            label = int(row["review_label"] == "POSITIVE")
            timestamp = (float(row["start_seconds"]) + float(row["end_seconds"])) / 2
            polarity = "positive" if label else "negative"
            target = output / polarity / f"{row['sample_id']}.mp4"
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                subprocess.run(
                    [
                        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                        "-ss", str(max(0.0, timestamp - 1.0)), "-i", str(source),
                        "-t", "2.2", "-an", "-c:v", "libx264", "-preset", "veryfast",
                        "-crf", "25", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                        str(target),
                    ],
                    check=True,
                )
            rows.append(
                {
                    "sample_id": row["sample_id"],
                    "video_path": target.relative_to(root).as_posix(),
                    "style": "animation",
                    "label": label,
                    "source": str(source),
                    "timestamp_seconds": round(timestamp, 3),
                    "notes": "reviewed Conan direct-violence field interval",
                }
            )
    manifest = root / "annotations" / "violence_conan_reviewed_video_v1.csv"
    with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(
        f"manifest={manifest} samples={len(rows)} "
        f"positive={sum(row['label'] for row in rows)} "
        f"negative={sum(not row['label'] for row in rows)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
