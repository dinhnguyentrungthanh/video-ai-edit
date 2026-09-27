from __future__ import annotations

import json
import subprocess
from pathlib import Path


def probe_video(ffprobe: Path, video: Path) -> dict:
    command = [
        str(ffprobe),
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(video),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return json.loads(completed.stdout)


def duration_seconds(probe: dict) -> float:
    value = probe.get("format", {}).get("duration")
    if value is not None:
        return float(value)
    durations = [
        float(stream["duration"])
        for stream in probe.get("streams", [])
        if stream.get("duration") is not None
    ]
    if not durations:
        raise ValueError("Video duration is unavailable")
    return max(durations)
