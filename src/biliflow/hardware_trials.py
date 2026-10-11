"""FFmpeg trials of the hardware measurement (docs/HARDWARE_PROFILE.md section 6.3 steps 7-8).

A synthetic clip (lavfi testsrc2 + noise, never a user video, never in ``input\\``), then:
5-second encodes with libx264 (the export's veryfast CRF 20) and the NVENC encoders; NVDEC decodes
per codec, each compared by framemd5 with the CPU decode of the same clip; and the logo-routing
chain on the CPU and on NVDEC, built from ``nvdec.input_arguments``/``sampling_filter`` exactly as
``visual_logo_scanner._iter_frames`` builds it, with only the output swapped for a framemd5 file.

A failing trial is only recorded (``detection.ffmpeg``): it never fails the attempt. Error text is
the last line FFmpeg wrote, without links or local path prefixes, cut to 300 characters.
Imported only by hardware_measure, never by a scan stage.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence

from biliflow import nvdec
from biliflow.hardware_detect import parse_encoders
from biliflow.hardware_schema import MAX_MESSAGE, NVDEC_CODECS, sanitize_message
from biliflow.hardware_tools import ToolResult, ToolRunner, decoded

CLIP_NAME = "clip.mp4"
CLIP_WIDTH, CLIP_HEIGHT, CLIP_FPS, CLIP_SECONDS = 1920, 1080, 30, 20
CLIP_LABEL = "syn-1080p30-20s"  # measurement "clip" is "<label>:<sha256>", at most 80 characters
CLIP_FILTER = "format=yuv420p,noise=alls=10:allf=t:all_seed=12345"
CLIP_TIMEOUT_SECONDS = 300.0  # single-threaded by contract: about 80-100 s on the 2026 PC
PROBE_TIMEOUT_SECONDS = 30.0
LISTING_TIMEOUT_SECONDS = 30.0
ENCODE_SECONDS = 5
ENCODE_TIMEOUT_SECONDS = 90.0
VALIDATE_TIMEOUT_SECONDS = 60.0
CODEC_CLIP_SECONDS = 2
CODEC_CLIP_SCALE = "scale=640:360:flags=bilinear"
CODEC_CLIP_TIMEOUT_SECONDS = 120.0
DECODE_TIMEOUT_SECONDS = 60.0
NVDEC_DOWNLOAD = "hwdownload,format=nv12,format=yuv420p"  # the download of nvdec.sampling_filter
ROUTING_WIDTH, ROUTING_HEIGHT = 320, 180  # visual_logo_scanner: analysis width 320 of a 1920x1080 source
# (case, start, duration, sample_every): the careful profile's logo grid, its boundary grid and a seek.
ROUTING_CASES = (("logo", 0.0, 20.0, 2.0), ("boundary", 0.0, 20.0, 0.25), ("seek", 7.0, 10.0, 0.25))
ROUTING_CHAIN = "logo_routing"

ENCODE_TRIALS = ("libx264", "h264_nvenc", "hevc_nvenc", "av1_nvenc")  # QSV and AMF are only listed
ENCODE_OPTIONS: Mapping[str, tuple[str, ...]] = {"libx264": ("-preset", "veryfast", "-crf", "20")}
ENCODE_SUBJECTS: Mapping[str, str] = {"libx264": "libx264:veryfast:crf20"}
SOFTWARE_AV1 = ("libsvtav1", "libaom-av1", "librav1e")
CODEC_ENCODERS: Mapping[str, tuple[str, ...]] = {"h264": ("libx264",), "hevc": ("libx265",), "av1": SOFTWARE_AV1}
CODEC_ENCODER_OPTIONS: Mapping[str, tuple[str, ...]] = {
    "libx264": ("-preset", "veryfast", "-crf", "23"),
    "libx265": ("-preset", "ultrafast", "-crf", "28"),
    "libsvtav1": ("-preset", "10", "-crf", "40"),
    "libaom-av1": ("-cpu-used", "8", "-row-mt", "1", "-crf", "40"),
    "librav1e": ("-speed", "10", "-qp", "120"),
}

NO_CUDA_HWACCEL = "FFmpeg không liệt kê hwaccel cuda."
UNKNOWN_ERROR = "lỗi không rõ"
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://\S+")


@dataclass(frozen=True)
class Timing:
    """One timed trial, turned into a measurement record by hardware_measure."""
    kind: str
    subject: str
    wall_seconds: float | None
    value: float | None
    unit: str
    ok: bool
    error: str | None


@dataclass(frozen=True)
class TrialContext:
    runner: ToolRunner
    ffmpeg: Path
    ffprobe: Path
    run_dir: Path
    replacements: tuple[tuple[str, str], ...]
    progress: Callable[[str], None]  # step name; raises Cancelled or BudgetExceeded
    note: Callable[[str], None]  # one line for the measurement log


@dataclass(frozen=True)
class TrialReport:
    """The trial parts of detection.ffmpeg, the clip's SHA-256 and the timings."""
    clip_sha256: str | None
    encoders: dict[str, dict[str, Any]]
    nvdec: dict[str, Any]
    nvdec_codecs: dict[str, dict[str, Any]]
    timings: tuple[Timing, ...]


# --- commands ------------------------------------------------------------------------------------

def clip_command(ffmpeg: Path, out: Path) -> list[str]:
    """The same bytes every time: single-threaded x264 of a fixed lavfi source with a fixed noise seed."""
    source = f"testsrc2=size={CLIP_WIDTH}x{CLIP_HEIGHT}:rate={CLIP_FPS}:duration={CLIP_SECONDS}"
    return [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", source,
            "-vf", CLIP_FILTER, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-g", "60",
            "-pix_fmt", "yuv420p", "-threads", "1", "-an", "-fflags", "+bitexact", "-movflags", "+faststart",
            str(out)]


def probe_command(ffprobe: Path, clip: Path) -> list[str]:
    return [str(ffprobe), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(clip)]


def encode_command(ffmpeg: Path, clip: Path, out: Path, encoder: str) -> list[str]:
    """The export's shape (final_renderer.py, without the size cap, filters and audio), 5 seconds."""
    return [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-i", str(clip), "-map", "0:v:0",
            "-t", f"{ENCODE_SECONDS:.3f}", "-map_metadata", "0", "-c:v", encoder, *ENCODE_OPTIONS.get(encoder, ()),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-max_muxing_queue_size", "2048", str(out)]


def validate_command(ffmpeg: Path, encoded: Path) -> list[str]:
    """The export's full CPU decode check (final_renderer.py), video only."""
    return [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-i", str(encoded), "-map", "0:v:0",
            "-f", "null", os.devnull]


def codec_clip_command(ffmpeg: Path, clip: Path, out: Path, encoder: str) -> list[str]:
    return [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-i", str(clip), "-map", "0:v:0",
            "-t", f"{CODEC_CLIP_SECONDS:.3f}", "-vf", CODEC_CLIP_SCALE, "-c:v", encoder,
            *CODEC_ENCODER_OPTIONS[encoder], "-pix_fmt", "yuv420p", "-an", str(out)]


def codec_decode_command(ffmpeg: Path, source: Path, out: Path, backend: str) -> list[str]:
    chain = NVDEC_DOWNLOAD if backend == "nvdec" else "format=yuv420p"
    return [str(ffmpeg), "-hide_banner", "-loglevel", "error", *nvdec.input_arguments(backend),
            "-i", str(source), "-map", "0:v:0", "-vf", chain, "-an", "-sn", "-f", "framemd5", str(out)]


def routing_command(ffmpeg: Path, clip: Path, out: Path, *, start: float, duration: float,
                    sample_every: float, backend: str, width: int = ROUTING_WIDTH,
                    height: int = ROUTING_HEIGHT) -> list[str]:
    """visual_logo_scanner._iter_frames' argv with ``-f rawvideo ... pipe:1`` swapped for a framemd5 file."""
    return [str(ffmpeg), "-hide_banner", "-loglevel", "error",
            *nvdec.input_arguments(backend),
            "-ss", f"{start:.3f}", "-i", str(clip), "-t", f"{duration:.3f}",
            "-vf", nvdec.sampling_filter(1.0 / sample_every, width, height, backend),
            "-an", "-sn", "-f", "framemd5", "-pix_fmt", "rgb24", str(out)]


# --- small helpers -------------------------------------------------------------------------------

def clean_error(text: object, replacements: Sequence[tuple[str, str]]) -> str:
    return sanitize_message(_URL.sub("<url>", str(text)), replacements, MAX_MESSAGE) or UNKNOWN_ERROR


def failure_text(result: ToolResult, replacements: Sequence[tuple[str, str]]) -> str | None:
    """None for a success; otherwise the last line FFmpeg wrote (or why it did not run), cleaned."""
    if result.ok:
        return None
    if result.timed_out:
        return "quá giờ"
    if result.error is not None:
        return clean_error(f"không chạy được ({result.error})", replacements)
    lines = [line.strip() for line in decoded(result.stderr).splitlines() if line.strip()]
    return clean_error(lines[-1] if lines else f"thoát mã {result.returncode}", replacements)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def framemd5_lines(path: Path) -> list[str]:
    """The data lines of a framemd5 file (the '#' header lines name the build and are ignored)."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]


def first_difference(cpu: Sequence[str], other: Sequence[str]) -> int | None:
    """None when both are equal and not empty; else the first index that differs."""
    if cpu and list(cpu) == list(other):
        return None
    return next((index for index, (a, b) in enumerate(zip(cpu, other)) if a != b), min(len(cpu), len(other)))


def _entry(trial: str, error: str | None) -> dict[str, Any]:
    return {"trial": trial, "error": error}


# --- the trials ----------------------------------------------------------------------------------

def run_trials(context: TrialContext, ffmpeg_detection: Mapping[str, Any]) -> TrialReport:
    """Every trial in order; nothing runs when detection found no usable ffmpeg.exe."""
    encoders = {name: dict(entry) for name, entry in ffmpeg_detection["encoders"].items()}
    routing = dict(ffmpeg_detection["nvdec"])
    codecs = {codec: dict(entry) for codec, entry in ffmpeg_detection["nvdec_codecs"].items()}
    if not ffmpeg_detection["ok"]:
        return TrialReport(None, encoders, routing, codecs, ())
    cuda = "cuda" in ffmpeg_detection["hwaccels_listed"]
    context.progress("clip")
    clip = context.run_dir / CLIP_NAME
    made = context.runner.run(clip_command(context.ffmpeg, clip), timeout=CLIP_TIMEOUT_SECONDS)
    problem = failure_text(made, context.replacements)
    if problem is not None:
        reason = clean_error(f"Không tạo được clip tổng hợp: {problem}", context.replacements)
        skipped = {name: ({**entry, "error": reason} if name in ENCODE_TRIALS and entry["listed"] else entry)
                   for name, entry in encoders.items()}
        return TrialReport(None, skipped, routing, {codec: _entry("not_run", reason) for codec in NVDEC_CODECS}, ())
    digest = file_sha256(clip)
    context.progress("encoders")
    encoders, encode_timings = encode_trials(context, clip, encoders)
    context.progress("nvdec_codecs")
    codecs = codec_trials(context, clip, cuda=cuda)
    context.progress("logo_routing")
    routing, routing_timings = routing_trials(context, clip, routing, cuda=cuda)
    return TrialReport(digest, encoders, routing, codecs, encode_timings + routing_timings)


def encode_trials(context: TrialContext, clip: Path,
                  encoders: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, dict[str, Any]], tuple[Timing, ...]]:
    results = {name: dict(entry) for name, entry in encoders.items()}
    timings: list[Timing] = []
    for name in ENCODE_TRIALS:
        entry = results.get(name)
        if entry is None or not entry["listed"]:
            continue
        out = context.run_dir / f"encode-{name}.mp4"
        encoded = context.runner.run(encode_command(context.ffmpeg, clip, out, name), timeout=ENCODE_TIMEOUT_SECONDS)
        problem = failure_text(encoded, context.replacements)
        if problem is None:
            checked = context.runner.run(validate_command(context.ffmpeg, out), timeout=VALIDATE_TIMEOUT_SECONDS)
            failed = failure_text(checked, context.replacements)
            problem = None if failed is None else clean_error(f"kiểm tra giải mã: {failed}", context.replacements)
        results[name] = {"listed": True, "trial": "ok" if problem is None else "failed", "error": problem}
        fps = ENCODE_SECONDS * CLIP_FPS / encoded.seconds if problem is None and encoded.seconds > 0 else None
        timings.append(Timing("encode", ENCODE_SUBJECTS.get(name, name),
                              encoded.seconds if encoded.returncode is not None else None,
                              None if fps is None else round(fps, 3), "fps", problem is None, problem))
    return results, tuple(timings)


def codec_trials(context: TrialContext, clip: Path, *, cuda: bool) -> dict[str, dict[str, Any]]:
    """nvdec_codecs: a short clip per codec from a software encoder, NVDEC framemd5 against the CPU's."""
    if not cuda:
        return {codec: _entry("not_run", NO_CUDA_HWACCEL) for codec in NVDEC_CODECS}
    listing = context.runner.run([str(context.ffmpeg), "-hide_banner", "-encoders"], timeout=LISTING_TIMEOUT_SECONDS)
    problem = failure_text(listing, context.replacements)
    if problem is not None:
        reason = clean_error(f"Không liệt kê được encoder: {problem}", context.replacements)
        return {codec: _entry("not_run", reason) for codec in NVDEC_CODECS}
    available = parse_encoders(decoded(listing.stdout))
    return {codec: _codec_trial(context, clip, codec, available) for codec in NVDEC_CODECS}


def _codec_trial(context: TrialContext, clip: Path, codec: str, available: frozenset[str]) -> dict[str, Any]:
    candidates = CODEC_ENCODERS[codec]
    encoder = next((name for name in candidates if name in available), None)
    if encoder is None:
        return _entry("not_run", f"Không có encoder phần mềm để tạo clip {codec} ({', '.join(candidates)}).")
    source = context.run_dir / f"codec-{codec}.mkv"
    made = context.runner.run(codec_clip_command(context.ffmpeg, clip, source, encoder),
                              timeout=CODEC_CLIP_TIMEOUT_SECONDS)
    problem = failure_text(made, context.replacements)
    if problem is not None:
        return _entry("not_run", clean_error(f"Không tạo được clip {codec} bằng {encoder}: {problem}",
                                             context.replacements))
    outputs = {}
    for backend in ("cpu", "nvdec"):
        outputs[backend] = context.run_dir / f"codec-{codec}-{backend}.framemd5"
        decoded_run = context.runner.run(codec_decode_command(context.ffmpeg, source, outputs[backend], backend),
                                         timeout=DECODE_TIMEOUT_SECONDS)
        problem = failure_text(decoded_run, context.replacements)
        if problem is not None:
            trial = "not_run" if backend == "cpu" else "failed"
            label = "Giải mã CPU lỗi" if backend == "cpu" else "Giải mã NVDEC lỗi"
            return _entry(trial, clean_error(f"{label}: {problem}", context.replacements))
    difference = first_difference(framemd5_lines(outputs["cpu"]), framemd5_lines(outputs["nvdec"]))
    if difference is not None:
        return _entry("failed", f"framemd5 của NVDEC khác giải mã CPU (khung {difference}).")
    return _entry("ok", None)


# --- the logo-routing self-check (section 5.5) ---------------------------------------------------

def nvdec_probe_runner(runner: ToolRunner) -> Callable[..., Any]:
    """nvdec.resolve_backend's ``runner(command, capture_output=True, timeout=120)`` through ToolRunner.

    stderr stays bytes (resolve_backend decodes it); Cancelled and BudgetExceeded pass through.
    """
    def run(command: Sequence[str], capture_output: bool = True, timeout: float = 120.0) -> Any:
        result = runner.run(command, timeout=timeout)
        if result.timed_out:
            raise subprocess.TimeoutExpired(list(command), timeout)
        if result.error is not None:
            raise OSError(result.error)
        return SimpleNamespace(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
    return run


def _routing_cases(context: TrialContext, clip: Path, backend: str) -> tuple[float, str | None, list[Path]]:
    """(wall seconds, failure, framemd5 files) of the three cases on one backend."""
    wall, outputs = 0.0, []
    for case, start, duration, sample_every in ROUTING_CASES:
        out = context.run_dir / f"routing-{case}-{backend}.framemd5"
        result = context.runner.run(routing_command(context.ffmpeg, clip, out, start=start, duration=duration,
                                                    sample_every=sample_every, backend=backend),
                                    timeout=DECODE_TIMEOUT_SECONDS)
        problem = failure_text(result, context.replacements)
        if problem is not None:
            return wall, problem, outputs
        wall += result.seconds
        outputs.append(out)
    return round(wall, 3), None, outputs


def _routing_timing(backend: str, wall: float, problem: str | None) -> Timing:
    decoded_seconds = sum(duration for _case, _start, duration, _every in ROUTING_CASES)
    value = round(decoded_seconds / wall, 3) if problem is None and wall > 0 else None
    return Timing("decode", f"{backend}:{ROUTING_CHAIN}", wall if problem is None else None, value, "x_realtime",
                  problem is None, problem)


def _clip_is_verified(context: TrialContext, clip: Path) -> dict[str, Any] | None:
    """The ffprobe dict of the clip when it is the H.264/yuv420p stream NVDEC is verified for."""
    result = context.runner.run(probe_command(context.ffprobe, clip), timeout=PROBE_TIMEOUT_SECONDS)
    if not result.ok:
        return None
    try:
        probe = json.loads(decoded(result.stdout))
    except ValueError:
        return None
    streams = probe.get("streams") if type(probe) is dict else None
    video = next((s for s in streams or () if type(s) is dict and s.get("codec_type") == "video"), {})
    return probe if (video.get("codec_name"), video.get("pix_fmt")) in nvdec.VERIFIED_STREAMS else None


def routing_trials(context: TrialContext, clip: Path, routing: Mapping[str, Any], *,
                   cuda: bool) -> tuple[dict[str, Any], tuple[Timing, ...]]:
    """detection.ffmpeg.nvdec and the decode timings: CPU first, then resolve_backend's probe, then NVDEC."""
    result = {**routing, "trial": "not_run", "framemd5_equal_cpu": None, "chain": ROUTING_CHAIN}
    cpu_wall, cpu_problem, cpu_outputs = _routing_cases(context, clip, "cpu")
    timings = [_routing_timing("cpu", cpu_wall, cpu_problem)]
    if cpu_problem is not None or not cuda:
        return result, tuple(timings)
    probe = _clip_is_verified(context, clip)
    if probe is None:
        context.note("Tự kiểm NVDEC không chạy: clip không phải H.264 yuv420p, hoặc ffprobe lỗi.")
        return result, tuple(timings)
    effective, reason = nvdec.resolve_backend("nvdec", probe=probe, ffmpeg_path=context.ffmpeg, input_path=clip,
                                              start_seconds=0.0, runner=nvdec_probe_runner(context.runner))
    if effective != "nvdec":
        context.note(clean_error(f"Tự kiểm NVDEC lỗi: {reason}", context.replacements))
        return {**result, "trial": "failed"}, tuple(timings)
    nvdec_wall, nvdec_problem, nvdec_outputs = _routing_cases(context, clip, "nvdec")
    timings.append(_routing_timing("nvdec", nvdec_wall, nvdec_problem))
    if nvdec_problem is not None:
        context.note(clean_error(f"Tự kiểm NVDEC lỗi: {nvdec_problem}", context.replacements))
        return {**result, "trial": "failed"}, tuple(timings)
    equal = all(first_difference(framemd5_lines(cpu), framemd5_lines(gpu)) is None
                for cpu, gpu in zip(cpu_outputs, nvdec_outputs))
    return {**result, "trial": "ok", "framemd5_equal_cpu": equal}, tuple(timings)
