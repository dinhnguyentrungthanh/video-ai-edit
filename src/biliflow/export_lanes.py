"""When an export may run beside a scan (perf plan A1b, docs/PERF_INSTALL_PLAN.md section 5).

"Xuất song song" in Cài đặt is ``auto`` (the default) or ``off``. ``off`` is the
single worker of before: scans and exports one at a time, exports first, each
kind in click order. ``auto`` opens a second lane for ONE export, only while all
of these hold when it would start:

- the main lane runs a scan stage on the GPU (``uses_gpu`` and ``--device cuda``);
- the export asks for ``auto`` (never a plan forced to x264) and the root's
  hardware profile confirms NVENC for this very FFmpeg (no trial encode);
- the source's frame size is one whose NVENC memory was measured;
- NVML shows room for the NVENC session next to the largest scan stage (measured)
  plus a safety margin (``vram_decision``).

Anything else and the export waits for the main lane as before. x264 never runs
beside a scan: an NVENC export of the second lane that fails on its own or comes
out over the size limit stops (the renderer writes ``export_deferral_path``) and
is encoded with x264 by the main lane once the scan job running beside it is done.

The control plane only (scheduler, Control Center): nothing here is part of a
scan stage's cache key, and nothing loads torch or a hardware_* module on import.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from biliflow.export_encoder import AUTO, profile_nvenc_trial, requested_encoder
from biliflow.final_renderer import EXPORT_LANE_ENVIRONMENT, PARALLEL_EXPORT_LANE, export_deferral_path

MODE_SETTING = "parallel_export"
MODE_AUTO = "auto"
MODE_OFF = "off"
MODES = (MODE_AUTO, MODE_OFF)
DEFAULT_MODE = MODE_AUTO

# The environment of a render of the second lane (the renderer only defers there).
PARALLEL_RENDER_ENVIRONMENT = {EXPORT_LANE_ENVIRONMENT: PARALLEL_EXPORT_LANE}

MIB = 1024 * 1024

# Measured VRAM (MiB, NVML "used", GPU 0). None: not measured yet, so the second
# lane never opens. Evidence: docs/evidence/a1b/ (filled in by the A1b measurement).
#   NVENC_NEED_MIB: the NVENC export's own VRAM, measured while Qwen2-VL ran, by the
#     largest frame (width x height in pixels) it was measured for, smallest first.
#   SCAN_PEAK_MIB: the most VRAM a scan stage added above what was in use just
#     before it started (the Qwen2-VL stage); every scan stage is budgeted at it.
#   SAFETY_MARGIN_MIB: VRAM left free after both.
NVENC_NEED_MIB: tuple[tuple[int, int], ...] | None = None
SCAN_PEAK_MIB: int | None = None
SAFETY_MARGIN_MIB: int | None = None
# The long side of a frame NVENC memory was measured for (1080p landscape or portrait).
MAX_MEASURED_LONG_SIDE = 1920

# Why the second lane did or did not open (fixed words, never a tool's text).
OPENED = "opened"
NOT_MEASURED = "not_measured"
DEFERRED = "deferred"
NO_RENDER_SETTINGS = "no_render_settings"
PLAN_UNREADABLE = "plan_unreadable"
FORCED_X264 = "forced_x264"
NVENC_NOT_CONFIRMED = "nvenc_not_confirmed"
SIZE_UNKNOWN = "size_unknown"
SIZE_NOT_MEASURED = "size_not_measured"
VRAM_UNREADABLE = "vram_unreadable"
VRAM_SHORT = "vram_short"

# What the dashboard says for each (shown on the job's events).
REASON_TEXT = {
    OPENED: "Xuất song song với bước quét (NVENC, đủ VRAM).",
    NOT_MEASURED: "Chưa có số đo VRAM để xuất song song; xuất tuần tự.",
    DEFERRED: "Bản xuất này chờ encode lại bằng x264; xuất tuần tự.",
    NO_RENDER_SETTINGS: "Không đọc được lệnh xuất; xuất tuần tự.",
    PLAN_UNREADABLE: "Không đọc được kế hoạch xuất; xuất tuần tự.",
    FORCED_X264: "Kế hoạch xuất chọn x264; x264 không chạy song song với bước quét.",
    NVENC_NOT_CONFIRMED: "Hồ sơ phần cứng chưa xác nhận NVENC cho FFmpeg này; xuất tuần tự.",
    SIZE_UNKNOWN: "Không đọc được kích thước khung của video gốc; xuất tuần tự.",
    SIZE_NOT_MEASURED: "Kích thước khung lớn hơn mức đã đo VRAM của NVENC; xuất tuần tự.",
    VRAM_UNREADABLE: "Không đọc được VRAM của GPU; xuất tuần tự.",
    VRAM_SHORT: "VRAM còn trống không đủ cho NVENC cạnh bước quét; xuất tuần tự.",
}


def normalize_mode(value: Any) -> str:
    """``auto`` or ``off``; anything else is refused (ValueError)."""
    if value not in MODES:
        raise ValueError("Chế độ xuất song song phải là 'auto' hoặc 'off'")
    return value


def stored_mode(value: Any) -> str:
    """The mode a stored setting means; an unknown value is the default."""
    return value if value in MODES else DEFAULT_MODE


@dataclass(frozen=True)
class VramRead:
    total_mib: int
    used_mib: int
    free_mib: int


def read_vram() -> VramRead | None:
    """GPU 0's memory from NVML, or None (no NVIDIA driver, no pynvml, any NVML error).

    In process, a few milliseconds: no nvidia-smi and no torch (plan rule 3.4).
    """
    try:
        import pynvml
    except ImportError:
        return None
    try:
        pynvml.nvmlInit()
    except Exception:  # noqa: BLE001 - no driver or NVML library: no GPU to share
        return None
    try:
        info = pynvml.nvmlDeviceGetMemoryInfo(pynvml.nvmlDeviceGetHandleByIndex(0))
        return VramRead(int(info.total) // MIB, int(info.used) // MIB, int(info.free) // MIB)
    except Exception:  # noqa: BLE001 - an unreadable GPU never opens the lane
        return None
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:  # noqa: BLE001 - shutting NVML down cannot change the answer
            pass


def measured() -> bool:
    """Whether every VRAM number the decision needs was measured; until then the lane never opens
    and "auto" is the single worker of before (no NVML read, no lane event)."""
    return NVENC_NEED_MIB is not None and SCAN_PEAK_MIB is not None and SAFETY_MARGIN_MIB is not None


def nvenc_need_mib(width: int, height: int) -> int | None:
    """The measured NVENC VRAM for this frame, or None when no measurement covers it."""
    if NVENC_NEED_MIB is None or width <= 0 or height <= 0:
        return None
    if max(width, height) > MAX_MEASURED_LONG_SIDE:
        return None
    pixels = width * height
    for ceiling, need in NVENC_NEED_MIB:
        if pixels <= ceiling:
            return need
    return None


@dataclass(frozen=True)
class LaneDecision:
    parallel: bool
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return REASON_TEXT[self.reason]


def vram_decision(read: VramRead | None, *, baseline_mib: int | None, need_mib: int) -> LaneDecision:
    """Room for NVENC beside the scan: measured numbers only, no guess.

    The scan may still grow (a model loads later in its stage, or its next stage is
    the Qwen2-VL one), so it is budgeted at SCAN_PEAK_MIB above ``baseline_mib``, the
    VRAM in use just before its stage started (other programs, an export already
    running). What NVML shows now counts when it is more. The lane opens only when
    NVENC still leaves SAFETY_MARGIN_MIB free:

        total - max(used now, baseline + SCAN_PEAK_MIB) - need >= SAFETY_MARGIN_MIB
    """
    if SCAN_PEAK_MIB is None or SAFETY_MARGIN_MIB is None:
        return LaneDecision(False, NOT_MEASURED)
    if read is None or baseline_mib is None:
        return LaneDecision(False, VRAM_UNREADABLE)
    projected = max(read.used_mib, baseline_mib + SCAN_PEAK_MIB)
    left = read.total_mib - projected - need_mib
    detail = {
        "total_mib": read.total_mib, "used_mib": read.used_mib, "free_mib": read.free_mib,
        "baseline_mib": baseline_mib, "scan_peak_mib": SCAN_PEAK_MIB, "need_mib": need_mib,
        "margin_mib": SAFETY_MARGIN_MIB, "left_mib": left,
    }
    if left < SAFETY_MARGIN_MIB:
        return LaneDecision(False, VRAM_SHORT, detail)
    return LaneDecision(True, OPENED, detail)


@dataclass(frozen=True)
class ExportCandidate:
    """What the second lane needs to know about one waiting export, before VRAM."""
    eligible: bool
    reason: str
    need_mib: int | None = None
    width: int | None = None
    height: int | None = None
    output: Path | None = None


def _read_plan(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def frame_size(probe: dict) -> tuple[int, int] | None:
    """(width, height) of the first video stream of an ffprobe answer, or None."""
    stream = next(
        (item for item in probe.get("streams", []) if isinstance(item, dict) and item.get("codec_type") == "video"),
        None,
    )
    try:
        width, height = int(stream["width"]), int(stream["height"])  # type: ignore[index]
    except (TypeError, KeyError, ValueError):
        return None
    return (width, height) if width > 0 and height > 0 else None


def export_candidate(
    root: Path, render: Any, *,
    probe: Callable[[Path], dict],
) -> ExportCandidate:
    """Whether a waiting export may go to the second lane, VRAM aside.

    ``render`` is the job's ``render:{id}`` setting; ``probe`` returns ffprobe's
    answer for the source (only called once every other check passed).
    """
    if NVENC_NEED_MIB is None:
        return ExportCandidate(False, NOT_MEASURED)
    if not isinstance(render, dict) or not render.get("plan") or not render.get("output"):
        return ExportCandidate(False, NO_RENDER_SETTINGS)
    output = root / str(render["output"])
    if os.path.lexists(export_deferral_path(root, output)):
        return ExportCandidate(False, DEFERRED, output=output)
    plan = _read_plan(root / str(render["plan"]))
    source = plan.get("source") if plan else None
    if not isinstance(source, dict) or not source.get("path"):
        return ExportCandidate(False, PLAN_UNREADABLE, output=output)
    try:
        if requested_encoder(plan) != AUTO:
            return ExportCandidate(False, FORCED_X264, output=output)
    except ValueError:
        return ExportCandidate(False, PLAN_UNREADABLE, output=output)
    ffmpeg = root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    if profile_nvenc_trial(root, ffmpeg) != "ok":
        return ExportCandidate(False, NVENC_NOT_CONFIRMED, output=output)
    try:
        size = frame_size(probe(Path(str(source["path"]))))
    except Exception:  # noqa: BLE001 - an unreadable source keeps the export sequential
        size = None
    if size is None:
        return ExportCandidate(False, SIZE_UNKNOWN, output=output)
    need = nvenc_need_mib(*size)
    if need is None:
        return ExportCandidate(False, SIZE_NOT_MEASURED, width=size[0], height=size[1], output=output)
    return ExportCandidate(True, OPENED, need, size[0], size[1], output)
