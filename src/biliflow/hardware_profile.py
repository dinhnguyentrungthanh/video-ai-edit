"""Hardware profile: scan knobs, families and the choice of a configuration.

docs/HARDWARE_PROFILE.md sections 2, 4, 5 and 7. ``resolve`` and ``choose`` read only
the three state files (hardware_schema), the table in code (hardware_table) and
``job_purge.allowed_project_root``, plus the Observation the caller passes in: no
subprocess, torch, NVML or database. Before Đợt 2 nothing here reaches a scan stage:
job_pipeline still writes today's command lines and does not call speed_arguments.

Imported only by the hardware_* modules and the Control Center, never by a scan stage.
Module level: standard library and hardware_schema only. The decision order lives in
hardware_resolve; it, hardware_table (both take their types from here) and job_purge
are imported inside functions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

# read_profile, read_override, write_override and the observation types are re-exported.
from biliflow.hardware_schema import (  # noqa: F401
    FileStat, GpuInfo, Observation, OverrideRead, ProfileRead, UntestedFast, loads_strict, read_override,
    read_profile, write_override,
)

DEVICES = frozenset({"cuda", "cpu"})
PRECISIONS = frozenset({"fp32", "fp16"})
DECODES = frozenset({"cpu", "nvdec"})
# "default": no flag, today's behaviour. "off": matmul and cuDNN (convolution, RNN) in IEEE
# fp32 (torch.backends.fp32_precision = "ieee", section 5.6); it needs the Đợt 2 flag of cli.py.
TF32_MODES = frozenset({"default", "off"})
FORMS = ("prewarm", "plain", "cpu")
FAMILIES = ("fast", "standard_ocr8", "standard")  # highest first: the order of capping (section 2.3)
TASKS = frozenset({"scan", "export"})
ROW_STATUSES = frozenset({"gated", "ungated", "untested", "retired"})
CHOICE_ROW_STATUSES = frozenset({"gated", "ungated", "untested"})
SOURCES = frozenset({"profile", "last_good", "cpu_only", "no_cuda_machine"})
GPU_STAGES = ("animation_safety", "adult", "verify_adult", "live_safety", "gore", "violence",
              "confirm_violence", "text", "visual_logo", "localize_logo")
OCR_BATCH_SIZES = (1, 8)
FRAME_WINDOWS = (1, 4)
ROUTING_WORKERS = (1, 3)
EXPORT_V1 = ("libx264", "veryfast", 20, "vbv", 384000, "cpu_full_decode")
# The six routing options of the prewarm text command, in job_pipeline.py:197-202 order.
LOGO_CONFIG_OPTIONS = (
    ("--logo-sample-every", "logo_sample_every"),
    ("--logo-boundary-sample-every", "logo_boundary_sample_every"),
    ("--logo-boundary-seconds", "logo_boundary_seconds"),
    ("--logo-scene-change-threshold", "logo_scene_change_threshold"),
    ("--logo-coverage-bucket-seconds", "logo_coverage_bucket_seconds"),
    ("--logo-coverage-fallbacks-per-bucket", "logo_coverage_fallbacks_per_bucket"),
)

READY = "ready"
READY_LAST_GOOD = "ready_last_good"
READY_UNGATED = "ready_ungated"
READY_UNTESTED = "ready_untested"
NEEDS_MEASUREMENT = "needs_measurement"
INVALID = "invalid"
CUDA_UNAVAILABLE = "cuda_unavailable"
STALE_GPU = "stale_gpu"
STALE_TORCH = "stale_torch"
NO_GATED_ROW = "no_gated_row"
MULTI_GPU_UNSUPPORTED = "multi_gpu_unsupported"
OVERRIDE_INVALID = "override_invalid"
CPU_ROW_NOT_READY = "cpu_row_not_ready"
ARCH_UNSUPPORTED = "arch_unsupported"
DRIVER_TOO_OLD = "driver_too_old"
VRAM_TOO_SMALL = "vram_too_small"
READY_STATUSES = frozenset({READY, READY_LAST_GOOD, READY_UNGATED, READY_UNTESTED})
STATUSES = READY_STATUSES | frozenset({
    NEEDS_MEASUREMENT, INVALID, CUDA_UNAVAILABLE, STALE_GPU, STALE_TORCH, NO_GATED_ROW,
    MULTI_GPU_UNSUPPORTED, OVERRIDE_INVALID, CPU_ROW_NOT_READY, ARCH_UNSUPPORTED, DRIVER_TOO_OLD,
    VRAM_TOO_SMALL,
})
REASON_CODES = frozenset({
    "row_match", "family_capped", "last_good_used", "ungated_row", "not_observed",
    "driver_changed", "ffmpeg_changed", "ffmpeg_unavailable", "machine_changed",
    "nvdec_unverified", "nvdec_selftest_failed", "cpu_only_recorded",
    "c1_off_fp32", "fp16_anime_careful_only", "fp16_violence_l_only", "no_golden_gate",
    # option D (section 5.6)
    "untested_arch", "untested_torch", "untested_driver", "tf32_off", "untested_fast_on",
    "untested_fast_other_card", "arch_unsupported", "driver_too_old", "vram_too_small",
})
# Row caveats that hold only for the families whose knobs they describe (the others hold row-wide).
FAMILY_CAVEATS = frozenset({"c1_off_fp32", "fp16_anime_careful_only", "fp16_violence_l_only"})

TF32_FLAG_MESSAGE = ("Tắt TF32 cần cờ dòng lệnh mới của cli.py (Đợt 2); bản này chưa viết được "
                     "dòng lệnh cho cấu hình tắt TF32.")
NOT_OBSERVED_MESSAGE = "Chưa quan sát được phần cứng lúc xếp job; thử lại."
FROZEN_INVALID_MESSAGE = "Cấu hình phần cứng của job hỏng – Chạy lại"
FROZEN_SCHEMA = "biliflow.hw-choice"
FROZEN_VERSION = 1
WARNING_TEXTS = {
    "not_observed": "Chưa quan sát được phần cứng hiện tại; không đối chiếu được với hồ sơ.",
    "last_good_used": "File hồ sơ chính thiếu hoặc hỏng; đang dùng bản tốt gần nhất. Nên Đo lại.",
    "ffmpeg_changed": "ffmpeg.exe đã đổi từ lúc đo: giải mã routing bằng CPU, xuất bằng x264. Nên Đo lại.",
    "ffmpeg_unavailable": "Lúc đo không dùng được FFmpeg: giải mã routing bằng CPU, xuất bằng x264.",
    "machine_changed": "CPU khác với lúc đo (có thể thư mục được chép sang máy khác). Nên Đo lại.",
    "cpu_only_recorded": "Công tắc Chỉ dùng CPU đang bật; có hiệu lực cho job mới từ Đợt 2.",
    "tf32_off": "Tắt TF32: matmul và cuDNN chạy fp32 IEEE (cần cờ dòng lệnh của Đợt 2).",
    "untested_fast_other_card": "Công tắc đang bật cho card khác; bật lại cho card này.",
    "ungated_row": "Root thử: cấu hình này chưa qua cổng.",
}
_ARCH = re.compile(r"(sm|compute)_(\d{2,3})")
_CUDA_TAG = re.compile(r"[^+\s]+\+(cu\d{3,4})")
_DRIVER_NUMBER = re.compile(r"\d{1,5}(?:\.\d{1,5}){0,3}")
_HEX64 = re.compile(r"[0-9a-f]{64}")


def _member(value: Any, allowed: frozenset[str], name: str) -> None:
    if type(value) is not str or value not in allowed:
        raise ValueError(f"{name} phải thuộc {sorted(allowed)}")


def _whole(value: Any, allowed: tuple[int, ...], name: str) -> None:
    if type(value) is not int or value not in allowed:
        raise ValueError(f"{name} phải thuộc {list(allowed)}")


# --- knobs (section 2.2) ----------------------------------------------------------------------

@dataclass(frozen=True)
class Reason:
    code: str
    text: str

    def __post_init__(self) -> None:
        _member(self.code, REASON_CODES, "mã lý do")
        if type(self.text) is not str or not self.text:
            raise ValueError("lý do phải có nội dung")


@dataclass(frozen=True)
class ScanRequest:
    """What the user asked for a job (``fast_scan:{id}``, ``ocr_batch_size:{id}``)."""
    fast_scan: bool
    ocr_batch_size: int

    def __post_init__(self) -> None:
        if type(self.fast_scan) is not bool:
            raise ValueError("fast_scan phải là true hoặc false")
        _whole(self.ocr_batch_size, OCR_BATCH_SIZES, "ocr_batch_size")

    @property
    def family(self) -> str:
        if self.fast_scan:
            return "fast"
        return "standard_ocr8" if self.ocr_batch_size == 8 else "standard"


@dataclass(frozen=True)
class TextKnobs:
    detect_precision: str
    recognition_batch_size: int
    recognition_frame_window: int
    prewarm_logo_routing: bool
    logo_routing_workers: int
    logo_decode: str

    def __post_init__(self) -> None:
        _member(self.detect_precision, PRECISIONS, "text.detect_precision")
        _whole(self.recognition_batch_size, OCR_BATCH_SIZES, "text.recognition_batch_size")
        _whole(self.recognition_frame_window, FRAME_WINDOWS, "text.recognition_frame_window")
        if type(self.prewarm_logo_routing) is not bool:
            raise ValueError("text.prewarm_logo_routing phải là true hoặc false")
        _whole(self.logo_routing_workers, ROUTING_WORKERS, "text.logo_routing_workers")
        _member(self.logo_decode, DECODES, "text.logo_decode")


@dataclass(frozen=True)
class StageKnobs:
    stage: str
    device: str
    tf32: str
    text: TextKnobs | None = None
    routing_workers: int | None = None
    precision: str | None = None
    violence_precision: str | None = None


@dataclass(frozen=True)
class ScanKnobs:
    """Every knob of every scan stage; exactly one of the forms prewarm, plain, cpu."""
    device: str
    text: TextKnobs
    visual_logo_routing_workers: int
    animation_precision: str
    violence_precision: str
    tf32: str = "default"

    def __post_init__(self) -> None:
        _check_scan_knobs(self)

    @property
    def form(self) -> str:
        return _form_of(self) or ""

    def stage(self, name: str) -> StageKnobs:
        if name not in GPU_STAGES:
            raise ValueError(f"stage lạ: {name!r}")
        return StageKnobs(
            stage=name, device=self.device, tf32=self.tf32,
            text=self.text if name == "text" else None,
            routing_workers=self.visual_logo_routing_workers if name == "visual_logo" else None,
            precision=self.animation_precision if name == "animation_safety" else None,
            violence_precision=self.violence_precision if name == "live_safety" else None,
        )


def _check_scan_knobs(knobs: ScanKnobs) -> None:
    if type(knobs.text) is not TextKnobs:
        raise ValueError("text phải là TextKnobs")
    _member(knobs.device, DEVICES, "device")
    _whole(knobs.visual_logo_routing_workers, ROUTING_WORKERS, "visual_logo.routing_workers")
    _member(knobs.animation_precision, PRECISIONS, "animation_safety.precision")
    _member(knobs.violence_precision, PRECISIONS, "live_safety.violence_precision")
    _member(knobs.tf32, TF32_MODES, "tf32")
    text, cuda = knobs.text, knobs.device == "cuda"
    fp16 = "fp16" in (text.detect_precision, knobs.animation_precision, knobs.violence_precision)
    rules = (
        (fp16 and not cuda, "fp16 chỉ chạy trên cuda"),
        (text.recognition_batch_size > 1 and not cuda, "OCR batch lớn hơn 1 chỉ chạy trên cuda"),
        (text.recognition_frame_window > 1 and text.recognition_batch_size == 1,
         "frame window lớn hơn 1 cần OCR batch lớn hơn 1"),
        (not cuda and (text.prewarm_logo_routing or text.logo_decode != "cpu"),
         "trên cpu không khởi động routing và chỉ giải mã bằng cpu"),
        (text.logo_decode == "nvdec" and not (cuda and text.prewarm_logo_routing),
         "nvdec cần cuda và khởi động routing"),
        (text.logo_routing_workers != knobs.visual_logo_routing_workers,
         "số worker routing của text và visual_logo phải bằng nhau"),
        (not cuda and knobs.tf32 != "default", "tf32 chỉ đổi được trên cuda"),
    )
    for broken, problem in rules:
        if broken:
            raise ValueError(f"Núm quét không hợp lệ: {problem}")
    if _form_of(knobs) is None:
        raise ValueError("Núm quét không hợp lệ: không thuộc dạng prewarm, plain hay cpu")


def _form_of(knobs: ScanKnobs) -> str | None:
    text = knobs.text
    precisions = {text.detect_precision, knobs.animation_precision, knobs.violence_precision}
    if (knobs.device == "cuda" and text.prewarm_logo_routing and text.recognition_batch_size == 8
            and text.recognition_frame_window == 4 and text.logo_routing_workers == 3
            and knobs.visual_logo_routing_workers == 3 and precisions == {"fp16"}):
        return "prewarm"
    plain = (not text.prewarm_logo_routing and text.recognition_frame_window == 1
             and text.logo_routing_workers == 1 and knobs.visual_logo_routing_workers == 1
             and text.logo_decode == "cpu" and precisions == {"fp32"})
    if plain and knobs.device == "cuda":
        return "plain"
    if plain and knobs.device == "cpu" and text.recognition_batch_size == 1 and knobs.tf32 == "default":
        return "cpu"
    return None


def prewarm_knobs(decode: str, tf32: str = "default") -> ScanKnobs:
    return ScanKnobs("cuda", TextKnobs("fp16", 8, 4, True, 3, decode), 3, "fp16", "fp16", tf32)


def plain_knobs(batch: int, tf32: str = "default") -> ScanKnobs:
    return ScanKnobs("cuda", TextKnobs("fp32", batch, 1, False, 1, "cpu"), 1, "fp32", "fp32", tf32)


def cpu_knobs() -> ScanKnobs:
    return ScanKnobs("cpu", TextKnobs("fp32", 1, 1, False, 1, "cpu"), 1, "fp32", "fp32", "default")


# Today's command line of each family (fast with NVDEC); independent of table, profile and switches.
LEGACY_KNOBS: Mapping[str, ScanKnobs] = MappingProxyType({
    "fast": prewarm_knobs("nvdec"), "standard_ocr8": plain_knobs(8), "standard": plain_knobs(1),
})


@dataclass(frozen=True)
class SpeedArguments:
    text: tuple[object, ...]
    visual_logo: tuple[object, ...]
    animation_safety: tuple[object, ...]
    live_safety: tuple[object, ...]


def speed_arguments(knobs: ScanKnobs, *, config: Mapping[str, Any],
                    source_sha256: str | None) -> SpeedArguments:
    """The four tuples job_pipeline.py:189-220 builds today, inserted after ``"--device", <device>``."""
    if type(knobs) is not ScanKnobs:
        raise ValueError("speed_arguments cần ScanKnobs")
    if knobs.tf32 != "default":
        raise ValueError(TF32_FLAG_MESSAGE)
    text = knobs.text
    if knobs.form != "prewarm":
        batch = text.recognition_batch_size
        return SpeedArguments(("--recognition-batch-size", batch) if batch != 1 else (), (), (), ())
    logo = tuple(item for option, key in LOGO_CONFIG_OPTIONS for item in (option, config[key]))
    return SpeedArguments(
        (
            "--recognition-batch-size", text.recognition_batch_size,
            "--recognition-frame-window", text.recognition_frame_window,
            "--prewarm-logo-routing", *logo,
            "--logo-routing-workers", text.logo_routing_workers,
            "--logo-decode", text.logo_decode,  # always written: the CLI default is nvdec
            *(("--logo-source-sha256", source_sha256) if source_sha256 else ()),
            "--detect-precision", text.detect_precision,
        ),
        ("--routing-workers", knobs.visual_logo_routing_workers),
        ("--precision", knobs.animation_precision),
        ("--violence-precision", knobs.violence_precision),
    )


@dataclass(frozen=True)
class ExportKnobs:
    encoder: str
    preset: str
    crf: int
    size_guard: str
    audio_bitrate: int
    validate: str

    def __post_init__(self) -> None:
        values = (self.encoder, self.preset, self.crf, self.size_guard, self.audio_bitrate, self.validate)
        if tuple(map(type, values)) != tuple(map(type, EXPORT_V1)) or values != EXPORT_V1:
            raise ValueError("Bản xuất 1 chỉ có libx264 veryfast CRF 20, VBV, AAC 384 kb/s")


# --- results ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class FamilyOption:
    """One requested family on this machine: the family granted after capping, or a refusal."""
    requested: str
    family: str | None
    capped: bool
    knobs: ScanKnobs | None
    logo_decode: str | None  # prewarm form only
    tf32: str | None
    refusal: str | None  # the status when nothing is granted
    warnings: tuple[Reason, ...]


@dataclass(frozen=True)
class ProfileSummary:
    which: str  # "main" or "last_good"
    written_at: str
    result: str
    failure_code: str | None
    content_sha256: str
    fingerprint_sha256: str
    gpus: tuple[GpuInfo, ...]
    nvidia_driver: str | None
    torch_build: str | None


@dataclass(frozen=True)
class Resolution:
    """What resolve() found; the Settings page shows it and choose() builds on it."""
    status: str
    message: str
    source: str | None
    row_id: str | None
    row_status: str | None
    table_version: int | None
    families: Mapping[str, FamilyOption]
    reasons: tuple[Reason, ...]
    warnings: tuple[Reason, ...]  # of the state (observation, last-good, labels of section 5.6)
    caveats: tuple[Reason, ...]  # row-wide notes of the row; family ones are in FamilyOption
    test_root: bool
    cpu_only: bool
    override_state: str
    untested_fast: UntestedFast | None
    untested_fast_applies: bool
    main_state: str
    last_good_state: str
    profile: ProfileSummary | None
    observed_driver: str | None

    @property
    def ready(self) -> bool:
        return self.status in READY_STATUSES


@dataclass(frozen=True)
class ScanChoice:
    family: str
    requested_family: str
    knobs: ScanKnobs
    row_id: str
    row_status: str  # gated, ungated or untested
    table_version: int
    source: str
    profile_sha256: str | None
    fingerprint_sha256: str | None
    observed_driver: str | None
    reasons: tuple[Reason, ...]
    warnings: tuple[Reason, ...]
    task: str = "scan"

    def __post_init__(self) -> None:
        _member(self.family, frozenset(FAMILIES), "family")
        _member(self.requested_family, frozenset(FAMILIES), "requested_family")
        _member(self.row_status, CHOICE_ROW_STATUSES, "row_status")
        _member(self.source, SOURCES, "source")
        if type(self.knobs) is not ScanKnobs or self.task != "scan":
            raise ValueError("ScanChoice cần ScanKnobs")

    @property
    def device(self) -> str:
        return self.knobs.device

    def stage(self, name: str) -> StageKnobs:
        return self.knobs.stage(name)


@dataclass(frozen=True)
class ExportChoice:
    row_id: str
    row_status: str
    table_version: int
    knobs: ExportKnobs
    warnings: tuple[Reason, ...]
    task: str = "export"


class HardwareRefusal(ValueError):
    """choose("scan") refused: ``status`` of resolve(), ``code`` (the status or not_observed)."""

    def __init__(self, status: str, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


# --- helpers shared with hardware_resolve ----------------------------------------------------

def warning_reason(code: str) -> Reason:
    return Reason(code, WARNING_TEXTS[code])


def unique_reasons(reasons: tuple[Reason, ...]) -> tuple[Reason, ...]:
    return tuple(dict.fromkeys(reasons))


def refused_families(status: str) -> dict[str, FamilyOption]:
    return {family: FamilyOption(family, None, False, None, None, None, status, ()) for family in FAMILIES}


def driver_tuple(text: Any) -> tuple[int, ...] | None:
    """``"617.42"`` -> ``(617, 42)``; drivers are compared as numbers, never as strings."""
    if type(text) is not str or not _DRIVER_NUMBER.fullmatch(text):
        return None
    return tuple(int(part) for part in text.split("."))


def cuda_tag(torch_build: str | None) -> str | None:
    """``"2.14.0+cu126"`` -> ``"cu126"``; None for a CPU or unknown build."""
    match = _CUDA_TAG.fullmatch(torch_build or "")
    return match.group(1) if match else None


def capability_of(text: str) -> tuple[int, int]:
    major, minor = text.split(".")
    return int(major), int(minor)


def sm_name(capability: tuple[int, int]) -> str:
    return f"sm_{capability[0]}{capability[1]}"


def arch_supported(arch_list: Any, capability: tuple[int, int]) -> bool:
    """Section 5.6: ``sm_Mn`` of the card's major with n <= its minor, or ``compute_Mn`` <= the card."""
    for entry in arch_list if isinstance(arch_list, (list, tuple)) else ():
        match = _ARCH.fullmatch(entry) if type(entry) is str else None
        if match is None:
            continue  # arch-specific variants such as sm_90a run only on their own card: skipped
        code = (int(match.group(2)[:-1]), int(match.group(2)[-1]))
        if match.group(1) == "sm" and code[0] == capability[0] and code[1] <= capability[1]:
            return True
        if match.group(1) == "compute" and code <= capability:
            return True
    return False


def _table() -> Any:
    from biliflow import hardware_table  # imports its types from this module
    return hardware_table


def resolve(root: Path, *, observed: Observation | None) -> Resolution:
    """The hardware status of ``root`` (section 7.1); never raises (the Settings page shows it).

    Whether ``root`` is a test root is decided inside (section 4.2), never passed in.
    """
    try:
        from biliflow import hardware_resolve  # loads hardware_table, and job_purge for the test root
        return hardware_resolve.resolve_root(Path(root), observed)
    except Exception as error:  # noqa: BLE001 - a bug here must show as a status, not break a page
        return Resolution(
            status=INVALID, source=None, row_id=None, row_status=None, table_version=None,
            message=f"Lỗi nội bộ khi tính cấu hình phần cứng ({type(error).__name__}). Đo lại hoặc báo lỗi.",
            families=MappingProxyType(refused_families(INVALID)), reasons=(), warnings=(), caveats=(),
            test_root=False, cpu_only=False, override_state="unknown", untested_fast=None,
            untested_fast_applies=False, main_state="unknown", last_good_state="unknown", profile=None,
            observed_driver=None)


# --- choose -----------------------------------------------------------------------------------

def choose(task: str, *, root: Path, request: ScanRequest | None = None,
           observed: Observation | None = None) -> ScanChoice | ExportChoice:
    """``scan``: a ScanChoice or HardwareRefusal. ``export``: always an ExportChoice (version 1)."""
    if task == "export":
        resolution = resolve(root, observed=observed)
        table = _table()
        warnings = resolution.warnings
        if observed is None:
            warnings = unique_reasons(warnings + (warning_reason("not_observed"),))
        return ExportChoice(table.EXPORT_ROW_ID, "gated", table.TABLE_VERSION, table.EXPORT_KNOBS, warnings)
    if task != "scan":
        raise ValueError("task phải là scan hoặc export")
    if type(request) is not ScanRequest:
        raise ValueError("choose('scan') cần ScanRequest")
    resolution = resolve(root, observed=observed)
    if observed is None:
        raise HardwareRefusal(resolution.status, "not_observed", NOT_OBSERVED_MESSAGE)
    if resolution.status not in READY_STATUSES:
        raise HardwareRefusal(resolution.status, resolution.status, resolution.message)
    option = resolution.families[request.family]
    if option.knobs is None or option.family is None:
        raise HardwareRefusal(resolution.status, option.refusal or NO_GATED_ROW,
                              f"Cấu hình {resolution.row_id} không có họ nào không vượt quá {request.family}.")
    profile = resolution.profile
    return ScanChoice(
        family=option.family, requested_family=request.family, knobs=option.knobs,
        row_id=resolution.row_id, row_status=resolution.row_status, table_version=resolution.table_version,
        source=resolution.source, profile_sha256=None if profile is None else profile.content_sha256,
        fingerprint_sha256=None if profile is None else profile.fingerprint_sha256,
        observed_driver=resolution.observed_driver, reasons=resolution.reasons,
        warnings=unique_reasons(resolution.warnings + resolution.caveats + option.warnings))


# --- the frozen per-job value (section 4.3; written from Đợt 2 only) ------------------------

@dataclass(frozen=True)
class FrozenScan:
    family: str
    knobs: ScanKnobs
    legacy: bool  # True: no stored value, today's command line (LEGACY_KNOBS)
    row_id: str | None = None
    row_status: str | None = None
    table_version: int | None = None
    source: str | None = None
    frozen_at: str | None = None
    profile_sha256: str | None = None
    fingerprint_sha256: str | None = None
    observed_driver: str | None = None
    warnings: tuple[Reason, ...] = ()

    @property
    def device(self) -> str:
        return self.knobs.device


@dataclass(frozen=True)
class FrozenInvalid:
    error: str
    message: str = FROZEN_INVALID_MESSAGE


FROZEN_KEYS = frozenset({
    "schema", "version", "frozen_at", "source", "row_id", "row_status", "table_version", "family",
    "requested", "device", "knobs", "profile_sha256", "fingerprint_sha256", "observed_driver", "warnings",
})


def _knobs_value(knobs: ScanKnobs) -> dict[str, Any]:
    text = knobs.text
    return {
        "text": {"detect_precision": text.detect_precision, "recognition_batch_size": text.recognition_batch_size,
                 "recognition_frame_window": text.recognition_frame_window,
                 "prewarm_logo_routing": text.prewarm_logo_routing,
                 "logo_routing_workers": text.logo_routing_workers, "logo_decode": text.logo_decode},
        "visual_logo": {"routing_workers": knobs.visual_logo_routing_workers},
        "animation_safety": {"precision": knobs.animation_precision},
        "live_safety": {"violence_precision": knobs.violence_precision},
        "tf32": knobs.tf32,
    }


def freeze_value(choice: ScanChoice, request: ScanRequest, now: datetime) -> dict[str, Any]:
    """The JSON value of the per-job hardware setting (section 4.3, with ``knobs.tf32``)."""
    if type(choice) is not ScanChoice or type(request) is not ScanRequest:
        raise ValueError("freeze_value cần ScanChoice và ScanRequest")
    if type(now) is not datetime or now.tzinfo is None:
        raise ValueError("thời điểm đóng băng phải có múi giờ")
    if choice.requested_family != request.family:
        raise ValueError("yêu cầu không khớp lựa chọn")
    return {
        "schema": FROZEN_SCHEMA, "version": FROZEN_VERSION, "frozen_at": now.isoformat(timespec="seconds"),
        "source": choice.source, "row_id": choice.row_id, "row_status": choice.row_status,
        "table_version": choice.table_version, "family": choice.family,
        "requested": {"fast_scan": request.fast_scan, "ocr_batch_size": request.ocr_batch_size},
        "device": choice.knobs.device, "knobs": _knobs_value(choice.knobs),
        "profile_sha256": choice.profile_sha256, "fingerprint_sha256": choice.fingerprint_sha256,
        "observed_driver": choice.observed_driver,
        "warnings": [{"code": warning.code, "text": warning.text} for warning in choice.warnings],
    }


def parse_frozen(raw: Any, request: ScanRequest | None) -> FrozenScan | FrozenInvalid:
    """The stored value, or today's command line for a legacy job (None); never raises."""
    try:
        value = loads_strict(raw.encode("utf-8")) if type(raw) is str else raw
        if value is None:
            if type(request) is not ScanRequest:
                return FrozenInvalid("job cũ thiếu yêu cầu fast_scan/ocr_batch_size")
            return FrozenScan(request.family, LEGACY_KNOBS[request.family], True)
        return _parse_frozen_value(value)
    except Exception as error:  # noqa: BLE001 - this sits on the status path and must not raise
        return FrozenInvalid(f"{type(error).__name__}: {str(error)[:200]}")


def _exact(value: Any, keys: tuple[str, ...] | frozenset[str], where: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError(f"{where}: phải là object với đúng các khóa {sorted(keys)}")
    return value


def _optional(value: Any, pattern: re.Pattern[str], where: str) -> str | None:
    if value is not None and (type(value) is not str or not pattern.fullmatch(value)):
        raise ValueError(f"{where}: sai định dạng")
    return value


def _parse_frozen_value(value: Any) -> FrozenScan:
    doc = _exact(value, FROZEN_KEYS, "giá trị")
    if doc["schema"] != FROZEN_SCHEMA or type(doc["version"]) is not int or doc["version"] != FROZEN_VERSION:
        raise ValueError("schema hoặc phiên bản lạ")
    frozen_at = doc["frozen_at"]
    if type(frozen_at) is not str or datetime.fromisoformat(frozen_at).tzinfo is None:
        raise ValueError("frozen_at phải có múi giờ")
    _member(doc["source"], SOURCES, "source")
    _member(doc["row_status"], CHOICE_ROW_STATUSES, "row_status")
    _member(doc["family"], frozenset(FAMILIES), "family")
    if type(doc["row_id"]) is not str or not 0 < len(doc["row_id"]) <= 80:
        raise ValueError("row_id sai")
    if type(doc["table_version"]) is not int or doc["table_version"] < 1:
        raise ValueError("table_version sai")
    request = ScanRequest(**_exact(doc["requested"], ("fast_scan", "ocr_batch_size"), "requested"))
    knobs = _knobs_from_value(doc["knobs"], doc["device"])
    if not _family_fits(doc["family"], knobs):
        raise ValueError("family không khớp knobs")
    if FAMILIES.index(doc["family"]) < FAMILIES.index(request.family):
        raise ValueError("family vượt quá yêu cầu")
    if type(doc["warnings"]) is not list:
        raise ValueError("warnings phải là mảng")
    warnings = tuple(Reason(**_exact(item, ("code", "text"), "warnings[]")) for item in doc["warnings"])
    return FrozenScan(
        doc["family"], knobs, False, doc["row_id"], doc["row_status"], doc["table_version"], doc["source"],
        frozen_at, _optional(doc["profile_sha256"], _HEX64, "profile_sha256"),
        _optional(doc["fingerprint_sha256"], _HEX64, "fingerprint_sha256"),
        _optional(doc["observed_driver"], _DRIVER_NUMBER, "observed_driver"), warnings)


def _knobs_from_value(value: Any, device: Any) -> ScanKnobs:
    knobs = _exact(value, ("text", "visual_logo", "animation_safety", "live_safety", "tf32"), "knobs")
    text = _exact(knobs["text"], ("detect_precision", "recognition_batch_size", "recognition_frame_window",
                                  "prewarm_logo_routing", "logo_routing_workers", "logo_decode"), "knobs.text")
    logo = _exact(knobs["visual_logo"], ("routing_workers",), "knobs.visual_logo")
    animation = _exact(knobs["animation_safety"], ("precision",), "knobs.animation_safety")
    live = _exact(knobs["live_safety"], ("violence_precision",), "knobs.live_safety")
    return ScanKnobs(device, TextKnobs(**text), logo["routing_workers"], animation["precision"],
                     live["violence_precision"], knobs["tf32"])


def _family_fits(family: str, knobs: ScanKnobs) -> bool:
    form, batch = knobs.form, knobs.text.recognition_batch_size
    if family == "fast":
        return form == "prewarm"
    if family == "standard_ocr8":
        return form == "plain" and batch == 8
    return (form == "plain" and batch == 1) or form == "cpu"


if __name__ == "__main__":  # "Đo lại" (section 6.3): the measurement lives in hardware_measure
    from biliflow import hardware_measure
    raise SystemExit(hardware_measure.main())
