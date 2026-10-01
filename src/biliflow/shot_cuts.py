"""Hard-cut detection inside short source windows.

Used only by the opt-in NSFW shot completion (``scanner.complete_nsfw_shot_context``).
FFmpeg's ``select='gt(scene,0.3)'`` misses most cuts on film sources carried at
25 fps with one repeated frame about every second: the frame after each repeat
spikes as high as a real cut in a dark scene. This detector (ported from the
C1 study) therefore:

* decodes only the requested windows, every frame, scaled to 160x66 RGB;
* compares each frame with the previous decoded frame: mean absolute luma
  change, mean absolute HSV change (PySceneDetect's content value) and half the
  L1 distance between normalised 8x8x8 RGB histograms;
* ignores repeated frames (luma change below 0.3), both as cut candidates and
  as neighbours;
* marks a cut when the histogram distance is at least 0.12, or when the HSV
  change is at least 8 and at least 3 times the mean HSV change of the
  non-repeated neighbours within 3 frame positions (plus one frame of
  tolerance) on either side;
* keeps cuts at least 0.2 s apart.

A cut time is the timestamp of the first frame of the new shot, on the NSFW
scan timeline (source timestamps minus the file start time).
"""
from __future__ import annotations

import re
import subprocess
import sys
import threading
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

_SHOWINFO_FRAME = re.compile(r"\bn:\s*(\d+)\s+pts:\s*(-?\d+)\s+pts_time:")
_SHOWINFO_TIME_BASE = re.compile(r"config in time_base:\s*(\d+)/(\d+)")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


@dataclass(frozen=True)
class CutDetectorSettings:
    histogram_cut: float = 0.12
    content_cut_minimum: float = 8.0
    content_cut_ratio: float = 3.0
    neighbour_frames: int = 3
    repeated_frame_luma_delta: float = 0.3
    minimum_cut_gap_seconds: float = 0.2
    frame_width: int = 160
    frame_height: int = 66

    def as_dict(self) -> dict:
        return asdict(self)


DEFAULT_SETTINGS = CutDetectorSettings()


@dataclass(frozen=True)
class FrameChange:
    """Change between one decoded frame and the frame decoded before it."""

    timestamp_seconds: float
    luma_delta: float
    content_delta: float
    histogram_delta: float


class FrameDifferencer:
    """Consecutive-frame change features for RGB uint8 frames of one window."""

    def __init__(self) -> None:
        import cv2

        self._cv2 = cv2
        self._previous: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None

    def push(self, rgb: np.ndarray) -> tuple[float, float, float] | None:
        """Return (luma, content, histogram) change versus the previous frame."""
        cv2 = self._cv2
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.int16)
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).astype(np.int16)
        histogram = cv2.calcHist(
            [rgb], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256]
        ).ravel()
        histogram /= max(1.0, float(histogram.sum()))
        previous = self._previous
        self._previous = (gray, hsv, histogram)
        if previous is None:
            return None
        previous_gray, previous_hsv, previous_histogram = previous
        luma = float(np.abs(gray - previous_gray).mean())
        content = float(np.abs(hsv - previous_hsv).reshape(-1, 3).mean(axis=0).mean())
        histogram_delta = float(0.5 * np.abs(histogram - previous_histogram).sum())
        return luma, content, histogram_delta


def frame_changes(frames: Iterable[tuple[float, np.ndarray]]) -> list[FrameChange]:
    """Features for (timestamp, RGB frame) pairs; the first frame has no predecessor."""
    differencer = FrameDifferencer()
    changes = []
    for timestamp, rgb in frames:
        values = differencer.push(rgb)
        if values is not None:
            changes.append(FrameChange(float(timestamp), *values))
    return changes


def _nominal_frame_interval(times: Sequence[float]) -> float:
    steps = sorted(b - a for a, b in zip(times, times[1:]) if b > a)
    if not steps:
        return 0.04
    return steps[len(steps) // 2]


def detect_cuts(
    changes: Sequence[FrameChange], settings: CutDetectorSettings = DEFAULT_SETTINGS
) -> list[float]:
    """Cut timestamps (first frame of each new shot) for one contiguous window."""
    if not changes:
        return []
    times = [change.timestamp_seconds for change in changes]
    frame_interval = _nominal_frame_interval(times)
    window = settings.neighbour_frames
    neighbour_reach = window * frame_interval + frame_interval + 0.001
    repeated = settings.repeated_frame_luma_delta
    count = len(changes)
    cuts: list[float] = []
    for index, change in enumerate(changes):
        if change.luma_delta < repeated:
            continue
        neighbours = [
            changes[other].content_delta
            for other in range(max(0, index - window - 2), min(count, index + window + 3))
            if other != index
            and abs(times[other] - times[index]) <= neighbour_reach
            and changes[other].luma_delta >= repeated
        ]
        baseline = sum(neighbours) / len(neighbours) if neighbours else 0.0
        is_cut = change.histogram_delta >= settings.histogram_cut or (
            change.content_delta >= settings.content_cut_minimum
            and change.content_delta >= settings.content_cut_ratio * max(baseline, 1e-6)
        )
        if is_cut and (not cuts or times[index] - cuts[-1] >= settings.minimum_cut_gap_seconds):
            cuts.append(times[index])
    return cuts


def merge_windows(
    windows: Iterable[tuple[float, float]], *, join_gap_seconds: float = 1.0
) -> list[tuple[float, float]]:
    """Sorted union of windows; windows closer than the gap are decoded as one."""
    merged: list[list[float]] = []
    for start, end in sorted((float(a), float(b)) for a, b in windows):
        if end <= start:
            continue
        if merged and start <= merged[-1][1] + join_gap_seconds:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def window_frame_changes(
    ffmpeg_path: Path,
    input_path: Path,
    start: float,
    end: float,
    *,
    settings: CutDetectorSettings = DEFAULT_SETTINGS,
    threads: int = 2,
) -> list[FrameChange]:
    """Decode every frame of [start, end) and return consecutive-frame features.

    Frame timestamps come from FFmpeg's showinfo filter (integer pts and the
    filter time base), so they do not assume a constant frame rate. Input
    seeking without ``-copyts`` makes pts relative to the seek point, which is
    added back to land on the NSFW scan timeline.
    """
    seek = f"{max(0.0, float(start)):.3f}"
    offset = float(seek)
    duration = float(end) - offset
    if duration <= 0:
        return []
    width, height = settings.frame_width, settings.frame_height
    frame_bytes = width * height * 3
    command = [
        str(ffmpeg_path), "-hide_banner", "-nostats", "-loglevel", "info",
        "-threads", str(max(1, int(threads))), "-filter_threads", "1",
        "-skip_loop_filter", "all",
        "-ss", seek, "-t", f"{duration:.3f}", "-i", str(input_path),
        # No -map: FFmpeg picks the same video stream as the NSFW scan command.
        "-an", "-sn", "-dn", "-fps_mode", "passthrough",
        "-vf", f"scale={width}:{height}:flags=area,format=rgb24,showinfo=checksum=0",
        "-f", "rawvideo", "pipe:1",
    ]
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_NO_WINDOW
    )
    pts_values: list[int] = []
    time_base: list[tuple[int, int]] = []
    stderr_tail: deque[str] = deque(maxlen=40)

    def read_stderr() -> None:
        assert process.stderr is not None
        for raw in process.stderr:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if "showinfo" in line:
                match = _SHOWINFO_FRAME.search(line)
                if match:
                    pts_values.append(int(match.group(2)))
                    continue
                match = _SHOWINFO_TIME_BASE.search(line)
                if match and not time_base:
                    time_base.append((int(match.group(1)), int(match.group(2))))
                continue
            stderr_tail.append(line)

    reader = threading.Thread(target=read_stderr, name="biliflow-shot-cut-stderr", daemon=True)
    reader.start()
    # One entry per decoded frame; the first frame has no predecessor (None).
    features: list[tuple[float, float, float] | None] = []
    try:
        assert process.stdout is not None
        differencer = FrameDifferencer()
        while True:
            data = _read_exact(process.stdout, frame_bytes)
            if not data:
                break
            if len(data) != frame_bytes:
                raise RuntimeError(f"Incomplete raw frame: {len(data)} of {frame_bytes} bytes")
            features.append(differencer.push(np.frombuffer(data, np.uint8).reshape(height, width, 3)))
        return_code = process.wait()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        reader.join(timeout=10)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
    if return_code != 0:
        raise RuntimeError(
            f"FFmpeg cut decode failed with exit code {return_code}: " + "\n".join(stderr_tail)[-2000:]
        )
    if not features:
        return []
    if not time_base or len(pts_values) != len(features):
        raise RuntimeError(
            f"FFmpeg reported {len(pts_values)} frame timestamps for {len(features)} frames"
        )
    numerator, denominator = time_base[0]
    return [
        FrameChange(round(offset + pts_values[index] * numerator / denominator, 6), *values)
        for index, values in enumerate(features)
        if values is not None
    ]


def _read_exact(stream, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            break
        chunks.extend(chunk)
    return bytes(chunks)


def window_cuts(
    ffmpeg_path: Path,
    input_path: Path,
    start: float,
    end: float,
    *,
    settings: CutDetectorSettings = DEFAULT_SETTINGS,
    threads: int = 2,
) -> list[float]:
    changes = window_frame_changes(
        ffmpeg_path, input_path, start, end, settings=settings, threads=threads
    )
    return detect_cuts(changes, settings)


def find_window_cuts(
    ffmpeg_path: Path,
    input_path: Path,
    windows: Iterable[tuple[float, float]],
    *,
    settings: CutDetectorSettings = DEFAULT_SETTINGS,
    threads: int = 2,
) -> list[float]:
    """Cuts inside the union of the windows; each part of the source is decoded once."""
    cuts: list[float] = []
    for start, end in merge_windows(windows):
        cuts.extend(
            window_cuts(ffmpeg_path, input_path, start, end, settings=settings, threads=threads)
        )
    return sorted(set(cuts))
