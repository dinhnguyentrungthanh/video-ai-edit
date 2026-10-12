"""The video encoder of an export (perf plan A3, docs/PERF_INSTALL_PLAN.md section 5).

Every export chooses its encoder (``"auto"``, the user's approval of 2026-10-12):
NVENC where it works, x264 otherwise. An edit plan forces x264 with
``render_settings.video_encoder: "libx264"`` (the gates of export changes compare
that path byte for byte); a missing key means ``"auto"``.

Whether NVENC works comes from the hardware profile of the root (session E's
trial, ``detection.ffmpeg.encoders.h264_nvenc``) when the profile was measured
with this very FFmpeg; otherwise from one short trial encode.

An NVENC export that FFmpeg ends on its own error (GPU busy, out of memory, an
encoder error) or that comes out over the size limit is encoded again with x264;
the manifest records why (``encoding.fallback``). A stop is never such an error.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LIBX264 = "libx264"
H264_NVENC = "h264_nvenc"
AUTO = "auto"
REQUESTS = (LIBX264, AUTO)
# How the export is checked after it is written (the decode of every frame on the CPU).
VALIDATION_METHOD = "cpu_full_decode"

# NVENC settings the user approved on 2026-10-12 after the A3 measurement
# (docs/evidence/perf-export: -rc vbr -cq 25 under the same -maxrate/-bufsize as x264).
NVENC_PRESET = "p5"
NVENC_CQ = 25
NVENC_OPTIONS = ("-tune", "hq", "-spatial-aq", "1", "-rc-lookahead", "20")

TRIAL_FRAMES = 25
TRIAL_TIMEOUT_SECONDS = 60

# Why an "auto" export did or did not get NVENC (fixed words, never FFmpeg's text).
NOT_ASKED = "not_asked"
PROFILE_OK = "profile_ok"
PROFILE_FAILED = "profile_failed"
TRIAL_OK = "trial_ok"
TRIAL_FAILED = "trial_failed"
TRIAL_DID_NOT_FINISH = "trial_did_not_finish"

# Why an NVENC export was encoded again with x264 (encoding.fallback.reason).
SIZE_LIMIT = "size_limit"
ENCODER_ERROR = "encoder_error"
# Windows ends a crashed process with an NTSTATUS error code, from this value up.
NTSTATUS_ERROR = 0xC0000000
# A stop (Tạm dừng, Hủy, the Control Center closing) kills the render's whole
# process tree (scheduler.terminate_process_tree, taskkill /T /F). Should FFmpeg
# go first, the x264 retry waits this long: the stop ends this process meanwhile.
STOP_GRACE_SECONDS = 3.0
FALLBACK_MESSAGE_LINES = 3
FALLBACK_MESSAGE_CHARACTERS = 500


@dataclass(frozen=True)
class EncoderChoice:
    encoder: str
    requested: str
    nvenc_check: str

    def record(self) -> dict[str, str]:
        return {"requested": self.requested, "nvenc_check": self.nvenc_check}


def requested_encoder(plan: dict) -> str:
    """The plan's ``render_settings.video_encoder``; ``auto`` when it is not set."""
    settings = plan.get("render_settings")
    if settings is None:
        return AUTO
    if not isinstance(settings, dict):
        raise ValueError("render_settings must be an object")
    value = settings.get("video_encoder", AUTO)
    if value not in REQUESTS:
        raise ValueError(f"Unsupported video encoder request: {value!r}")
    return value


def _same_ffmpeg(recorded: dict, ffmpeg_path: Path) -> bool:
    try:
        info = os.stat(ffmpeg_path)
        same_path = os.path.normcase(str(Path(recorded["path"]).resolve())) == os.path.normcase(
            str(Path(ffmpeg_path).resolve())
        )
    except (KeyError, TypeError, OSError, RuntimeError):
        return False
    return same_path and recorded.get("size") == info.st_size and recorded.get("mtime_ns") == info.st_mtime_ns


def profile_nvenc_trial(project_root: Path, ffmpeg_path: Path) -> str | None:
    """``ok`` or ``failed`` from the root's hardware profile, None when it holds no result for this FFmpeg."""
    from biliflow.hardware_schema import read_profile

    read = read_profile(Path(project_root))
    if read.state != "ok" or not isinstance(read.document, dict):
        return None
    ffmpeg: Any = (read.document.get("detection") or {}).get("ffmpeg")
    if not isinstance(ffmpeg, dict) or ffmpeg.get("ok") is not True or not _same_ffmpeg(ffmpeg, ffmpeg_path):
        return None
    entry = (ffmpeg.get("encoders") or {}).get(H264_NVENC)
    trial = entry.get("trial") if isinstance(entry, dict) else None
    return trial if trial in ("ok", "failed") else None


def trial_command(ffmpeg_path: Path) -> list[str]:
    """One second of a test picture through NVENC, written nowhere."""
    return [
        str(ffmpeg_path), "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=s=320x240:r=25", "-frames:v", str(TRIAL_FRAMES),
        "-c:v", H264_NVENC, "-preset", NVENC_PRESET, "-pix_fmt", "yuv420p", "-f", "null", "-",
    ]


def trial_nvenc(ffmpeg_path: Path) -> str:
    """``TRIAL_OK``, ``TRIAL_FAILED`` or ``TRIAL_DID_NOT_FINISH``."""
    try:
        result = subprocess.run(
            trial_command(ffmpeg_path), capture_output=True, text=True, timeout=TRIAL_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return TRIAL_DID_NOT_FINISH
    return TRIAL_OK if result.returncode == 0 else TRIAL_FAILED


def choose_video_encoder(*, project_root: Path, ffmpeg_path: Path, plan: dict) -> EncoderChoice:
    requested = requested_encoder(plan)
    if requested == LIBX264:
        return EncoderChoice(LIBX264, requested, NOT_ASKED)
    recorded = profile_nvenc_trial(project_root, ffmpeg_path)
    if recorded is not None:
        check = PROFILE_OK if recorded == "ok" else PROFILE_FAILED
    else:
        check = trial_nvenc(ffmpeg_path)
    encoder = H264_NVENC if check in (PROFILE_OK, TRIAL_OK) else LIBX264
    return EncoderChoice(encoder, requested, check)


def video_arguments(
    encoder: str, *, maxrate: int | None, target_bitrate: int | None,
    cq: int = NVENC_CQ, preset: str = NVENC_PRESET,
) -> list[str]:
    """The video encoder arguments of the export command (from ``-c:v`` to before ``-pix_fmt``).

    x264 keeps the export's exact arguments. NVENC encodes for a constant quality
    (``-cq``, ``-b:v 0``) under the same -maxrate/-bufsize size guard.
    """
    if encoder == LIBX264:
        arguments = ["-c:v", LIBX264, "-preset", "veryfast", "-crf", "20"]
        if maxrate is not None:
            arguments += ["-maxrate", str(maxrate), "-bufsize", str(maxrate * 2), "-b:v", str(target_bitrate)]
        return arguments
    if encoder == H264_NVENC:
        arguments = ["-c:v", H264_NVENC, "-preset", preset, "-rc", "vbr", "-cq", str(cq), "-b:v", "0"]
        if maxrate is not None:
            arguments += ["-maxrate", str(maxrate), "-bufsize", str(maxrate * 2)]
        return arguments + list(NVENC_OPTIONS)
    raise ValueError(f"Unsupported video encoder: {encoder!r}")


def failed_by_itself(returncode: int, stderr: str) -> bool:
    """FFmpeg ended on its own error, not because a stop killed it.

    taskkill /F ends FFmpeg with exit code 1 before it prints anything. FFmpeg's
    own errors print a message (the export runs with ``-loglevel error``) and a
    crash ends with an NTSTATUS code. A source that makes FFmpeg print decode
    errors could pass a stop for an error here: STOP_GRACE_SECONDS covers that.
    """
    if not returncode:
        return False
    return bool(stderr.strip()) or returncode >= NTSTATUS_ERROR


def size_fallback(size: int, seconds: float) -> dict:
    """``encoding.fallback`` of an NVENC export over the size limit."""
    return {"from": H264_NVENC, "reason": SIZE_LIMIT, "bytes": size, "elapsed_seconds": round(seconds, 3)}


def error_fallback(returncode: int, stderr: str, seconds: float) -> dict:
    """``encoding.fallback`` of an NVENC export FFmpeg ended on its own error.

    The message keeps FFmpeg's first lines: the cause ("No capable devices found",
    "Error while opening encoder"); the lines after it only report the threads ending.
    """
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    message = " | ".join(lines[:FALLBACK_MESSAGE_LINES])[:FALLBACK_MESSAGE_CHARACTERS]
    return {
        "from": H264_NVENC, "reason": ENCODER_ERROR, "returncode": returncode,
        "message": message or None, "elapsed_seconds": round(seconds, 3),
    }


def video_description(encoder: str, *, size_limited: bool, cq: int = NVENC_CQ, preset: str = NVENC_PRESET) -> str:
    guard = "with VBV size guard" if size_limited else "without output size cap"
    if encoder == LIBX264:
        return f"libx264 CRF 20 {guard}"
    return f"h264_nvenc {preset} VBR CQ {cq} {guard}"
