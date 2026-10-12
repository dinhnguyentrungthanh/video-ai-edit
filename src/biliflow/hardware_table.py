"""The table of hardware configurations (docs/HARDWARE_PROFILE.md section 5).

Constants only. A ``row_id`` never changes: another combination gets a new id and a row no
longer used stays as ``retired``. Every edit raises TABLE_VERSION. Only hardware_resolve
(through hardware_profile.resolve) and hardware_profile.choose import this module, inside
functions; no scan stage does, so an edit changes no stage-cache key, only the command lines
of jobs frozen afterwards. Drivers are compared as numbers (hardware_profile.driver_tuple).
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from biliflow.hardware_profile import (
    FAMILIES, ROW_STATUSES, ExportKnobs, Reason, ScanKnobs, cpu_knobs, driver_tuple, plain_knobs,
    prewarm_knobs,
)

TABLE_VERSION = 1
VENDORS = frozenset({"nvidia", "none"})
GATED_TORCH = "2.14.0+cu126"
UNTESTED_ROW_ID = "nvidia-untested"
UNTESTED_FAST_ROW_ID = "nvidia-untested-fast"
EXPORT_ROW_ID = "export-x264"
# Smallest VRAM that ever ran the whole pipeline (RTX 2060 6 GB); below it Qwen2-VL and the other
# models may run out of memory part-way (section 5.6, condition 3).
UNTESTED_MIN_VRAM_MIB = 6144
# Lowest Windows driver NVIDIA lists for the CUDA runtime a torch build bundles (section 5.6).
CUDA_DRIVER_MIN: Mapping[str, str] = MappingProxyType({"cu126": "561.17"})
CUDA_DRIVER_MIN_EVIDENCE: Mapping[str, str] = MappingProxyType({
    "cu126": ("NVIDIA CUDA Toolkit release notes, table 'Corresponding Driver Versions': CUDA 12.6 "
              "Update 3 on Windows needs >= 561.17 (GA >= 560.76, Update 1-2 >= 560.94). The installed "
              "torch 2.14.0+cu126 bundles cudart64_12.dll 12.6 and cublas64_12.dll 12.6.4, the cuBLAS of "
              "12.6 Update 3 (docs/HARDWARE_PROFILE.md section 5.6)."),
})
# The three risks the confirmation dialog of the untested fast switch states (section 5.6).
UNTESTED_FAST_RISKS = (
    "Trên Ampere trở lên, tích chập 'fp32' mặc định chạy TF32 (BiliFlow tắt TF32 trong chế độ này), "
    "và kernel fp16 cũng khác Turing, nên kết quả có thể lệch so với RTX 2060.",
    "Một ứng viên bị bỏ sót trên đường chưa qua cổng sẽ không bao giờ hiện trong danh sách duyệt, "
    "nên không ai thấy.",
    "Số worker và số làn chưa tăng (để sau B1 và A1b), nên chưa dùng hết sức card.",
)


@dataclass(frozen=True)
class Match:
    """Section 5.2. Compute capability exact ("7.5"); drivers "major.minor", compared as numbers."""
    vendor: str
    compute_capability: str | None
    min_vram_mib: int | None
    gpu_count: int | None
    torch_builds: frozenset[str]
    driver_min: str | None
    driver_max: str | None

    def __post_init__(self) -> None:
        if self.vendor not in VENDORS or type(self.torch_builds) is not frozenset:
            raise ValueError("Match: vendor hoặc torch_builds sai")
        for driver in (self.driver_min, self.driver_max):
            if driver is not None and driver_tuple(driver) is None:
                raise ValueError(f"Match: driver {driver!r} sai định dạng")


@dataclass(frozen=True)
class Row:
    row_id: str
    status: str  # gated, ungated, untested or retired
    match: Match
    families: Mapping[str, ScanKnobs]
    default_family: str
    nvdec_verified: frozenset[tuple[str, str]]  # (driver, SHA-256 of ffmpeg.exe) past the pixel gate
    free_fields: Mapping[str, tuple[object, ...]]  # identical-result knobs chosen by timing (section 5.5)
    runnable_after: str | None  # for example "E3"
    evidence: tuple[str, ...]
    caveats: tuple[Reason, ...]

    def __post_init__(self) -> None:
        if self.status not in ROW_STATUSES or type(self.match) is not Match:
            raise ValueError(f"Row {self.row_id}: status hoặc match sai")
        if type(self.families) is not MappingProxyType or type(self.free_fields) is not MappingProxyType:
            raise ValueError(f"Row {self.row_id}: families và free_fields phải bất biến")
        if not self.families or not set(self.families) <= set(FAMILIES):
            raise ValueError(f"Row {self.row_id}: họ lạ")
        if any(type(knobs) is not ScanKnobs for knobs in self.families.values()):
            raise ValueError(f"Row {self.row_id}: mỗi họ phải là ScanKnobs")
        if self.default_family not in self.families or type(self.nvdec_verified) is not frozenset:
            raise ValueError(f"Row {self.row_id}: default_family hoặc nvdec_verified sai")
        if any(type(caveat) is not Reason for caveat in self.caveats):
            raise ValueError(f"Row {self.row_id}: caveats phải là Reason")


def _row(row_id: str, status: str, match: Match, families: dict[str, ScanKnobs], default_family: str, *,
         nvdec_verified: frozenset[tuple[str, str]] = frozenset(), runnable_after: str | None = None,
         evidence: tuple[str, ...], caveats: tuple[Reason, ...]) -> Row:
    return Row(row_id, status, match, MappingProxyType(dict(families)), default_family, nvdec_verified,
               MappingProxyType({}), runnable_after, evidence, caveats)


C1_OFF_FP32 = Reason("c1_off_fp32", "Họ fp32 (chuẩn, OCR 8) tắt luật máu anime C1: C1 chỉ hiệu chỉnh cho "
                     "report fp16 ở 2 fps (gore_triage.py:65, :114-115).")
FP16_ANIME_CAREFUL_ONLY = Reason("fp16_anime_careful_only", "Anime fp16 mới chỉ đo trên hồ sơ careful; hồ sơ "
                                 "fast vẫn dùng fp16 như hôm nay.")
FP16_VIOLENCE_L_ONLY = Reason("fp16_violence_l_only", "Bạo lực fp16 mới chỉ được chứng minh trên phim L.")
NO_GOLDEN_GATE = Reason("no_golden_gate", "Chưa núm tốc độ nào qua bộ Golden.")
UNTESTED_FAST_ON = Reason("untested_fast_on", "Chế độ nhanh fp16 trên card chưa qua cổng (công tắc riêng đang "
                          "bật). Rủi ro: " + " ".join(f"({index}) {risk}" for index, risk in
                                                    enumerate(UNTESTED_FAST_RISKS, start=1)))
TURING_CAVEATS = (C1_OFF_FP32, FP16_ANIME_CAREFUL_ONLY, FP16_VIOLENCE_L_ONLY, NO_GOLDEN_GATE)

ROWS: tuple[Row, ...] = (
    _row(
        "nvidia-sm75-torch2.14.0+cu126", "gated",
        Match("nvidia", "7.5", 6144, 1, frozenset({GATED_TORCH}), "576.80", None),
        {"standard": plain_knobs(1), "standard_ocr8": plain_knobs(8), "fast": prewarm_knobs("nvdec")}, "fast",
        # SHA-256 of the install's tools/ffmpeg/bin/ffmpeg.exe, read only; its mtime predates the NVDEC gate.
        nvdec_verified=frozenset({("576.80", "72a489eccd008c2ec2c0a5856c5c75bc3d8bbfa90166c4566865c246445e6aa3")}),
        evidence=(
            "Every gate: RTX 2060 6 GB, scan profile careful, torch 2.14.0+cu126 (docs/HARDWARE_PROFILE.md 5.3)",
            "standard: reference path of the OCR-8 gate and of fast vs standard; on L (ads only) standard and "
            "fast agree (docs/SCAN_PERFORMANCE.md:57-77; docs/CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md:640)",
            "standard_ocr8: identical excerpts, confidence within 6e-6 (docs/SCAN_PERFORMANCE.md:531-547); "
            "whole film L: identical tracks and previews (:634-641)",
            "fast: OCR 8 + window 4 (docs/SCAN_PERFORMANCE.md:115-133, :146-154); workers identical by "
            "construction plus whole film L (:79-91); NVDEC pixel-identical "
            "(docs/CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md:469-473, :520); overlap with routing prewarm (:549-575); "
            "CRAFT fp16 on L and H (:644-696); anime fp16 on H and a second animated film (:706-758); "
            "violence fp16 on L only (:815-826)",
            "nvdec_verified: the pixel gate ran on driver 576.80 (docs/CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md:520)",
        ),
        caveats=TURING_CAVEATS,
    ),
    _row(
        "cpu-fp32-torch2.14.0+cu126", "ungated",
        Match("none", None, None, None, frozenset({GATED_TORCH}), None, None),
        {"standard": cpu_knobs()}, "standard", runnable_after="E3",
        evidence=(
            "docs/HARDWARE_PROFILE.md 5.3 row 2: blocked until E3 (Florence gets float16 input, "
            "scripts/localize_visual_logo_report.py:135, while the model loads in fp32 on CPU, :215); "
            "gated after E4 (Golden gate of L, H, T against GPU fp32, docs/PERF_INSTALL_PLAN.md:217-221)",
        ),
        caveats=(C1_OFF_FP32, NO_GOLDEN_GATE),
    ),
    _row(
        UNTESTED_ROW_ID, "untested",
        Match("nvidia", None, None, None, frozenset(), None, None),
        {"standard": plain_knobs(1, tf32="off")}, "standard",
        evidence=("docs/HARDWARE_PROFILE.md 5.6 and 10: option D, chosen by the user on 2026-10-11; standard "
                  "mode with TF32 off on a card, torch build or driver that no gated row covers",),
        caveats=(C1_OFF_FP32, NO_GOLDEN_GATE),
    ),
    _row(
        UNTESTED_FAST_ROW_ID, "untested",
        Match("nvidia", None, None, None, frozenset(), None, None),
        {"standard": plain_knobs(1, tf32="off"), "standard_ocr8": plain_knobs(8, tf32="off"),
         "fast": prewarm_knobs("nvdec", tf32="off")}, "fast",
        evidence=("docs/HARDWARE_PROFILE.md 5.6 and 10: option D; the Turing families with TF32 off, only "
                  "while the PC switch is on for this card; NVDEC only through the self-test of 5.5",),
        caveats=(UNTESTED_FAST_ON,) + TURING_CAVEATS,
    ),
)

# Version 1 of the export, today's final_renderer.py:592-612, :642-656 (never refused).
EXPORT_KNOBS = ExportKnobs("libx264", "veryfast", 20, "vbv", 384000, "cpu_full_decode")
EXPORT_EVIDENCE = ("final_renderer.py:592-612, :642-656: libx264 veryfast CRF 20, VBV size guard, AAC 384 kb/s, "
                   "full CPU decode check (docs/HARDWARE_PROFILE.md 5.3)",)
