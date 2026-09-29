"""Optional NVDEC decoding for sampled scanner frames.

Frames stay on the GPU until the ``fps`` filter has dropped the unsampled
ones; only kept frames are downloaded, converted to yuv420p and scaled on the
CPU exactly as the software path scales them. Phase E1 verified identical
framemd5 output (pts and RGB24 pixels) for 8-bit yuv420p H.264 sources only,
so every other stream falls back to software decoding.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

DECODE_BACKENDS = ("cpu", "nvdec")
NVDEC_INPUT_ARGS = ("-hwaccel", "cuda", "-hwaccel_output_format", "cuda")
VERIFIED_STREAMS = {("h264", "yuv420p")}


def validate_backend(value: object) -> str:
    if value not in DECODE_BACKENDS:
        raise ValueError("decode backend must be 'cpu' or 'nvdec'")
    return str(value)


def input_arguments(backend: str) -> tuple[str, ...]:
    return NVDEC_INPUT_ARGS if validate_backend(backend) == "nvdec" else ()


def sampling_filter(fps: float, width: int, height: int, backend: str) -> str:
    scale = f"scale={width}:{height}:flags=bilinear"
    if validate_backend(backend) == "nvdec":
        return f"fps={fps},hwdownload,format=nv12,format=yuv420p,{scale}"
    return f"fps={fps},{scale}"


def _video_stream(probe: dict) -> dict:
    return next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {})


def resolve_backend(requested: str, *, probe: dict, ffmpeg_path: Path, input_path: Path,
                    start_seconds: float, runner=subprocess.run) -> tuple[str, str | None]:
    """Return (effective backend, fallback reason). Decides before any frame is scanned."""
    if validate_backend(requested) == "cpu":
        return "cpu", None
    stream = _video_stream(probe)
    identity = (stream.get("codec_name"), stream.get("pix_fmt"))
    if identity not in VERIFIED_STREAMS:
        return "cpu", f"NVDEC output not verified for {identity[0]}/{identity[1]}"
    # Exercise the same hardware filter path on one frame at the scan start.
    command = [str(ffmpeg_path), "-hide_banner", "-loglevel", "error", *NVDEC_INPUT_ARGS,
               "-ss", str(start_seconds), "-i", str(input_path), "-frames:v", "1",
               "-vf", "hwdownload,format=nv12,format=yuv420p", "-an", "-sn", "-f", "null", "-"]
    try:
        result = runner(command, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        return "cpu", f"NVDEC probe failed: {type(error).__name__}"
    if result.returncode != 0:
        detail = (result.stderr or b"").decode("utf-8", errors="replace").strip().splitlines()[-1:] or [""]
        return "cpu", f"NVDEC probe failed: {detail[0][:200]}"
    return "nvdec", None
