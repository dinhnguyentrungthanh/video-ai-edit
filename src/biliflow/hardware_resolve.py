"""The decision order of resolve() (docs/HARDWARE_PROFILE.md section 7.1).

Step 8 (the GPU rows) follows section 5.6 (option D, the user's choice of 2026-10-11): a
gated row matching exactly gives ``ready``; any other single NVIDIA GPU runs the untested
path or gets a precise refusal (``arch_unsupported``, ``driver_too_old``, ``vram_too_small``).
Step 4 (a recorded failure) uses the same arch and driver rules before ``cuda_unavailable``:
a card the installed torch has no kernel for passes ``is_available()`` but fails the probe.

Split out of hardware_profile to keep both files small. Only hardware_profile.resolve()
imports it, inside the function, and turns any exception into the status ``invalid``.
Reads only the three state files, the table in code and job_purge.allowed_project_root;
never imported by a scan stage.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from biliflow.hardware_profile import (
    ARCH_UNSUPPORTED, CPU_ROW_NOT_READY, CUDA_UNAVAILABLE, DRIVER_TOO_OLD, FAMILIES, FAMILY_CAVEATS, INVALID,
    MULTI_GPU_UNSUPPORTED, NEEDS_MEASUREMENT, NO_GATED_ROW, OVERRIDE_INVALID, READY, READY_LAST_GOOD,
    READY_STATUSES, READY_UNGATED, READY_UNTESTED, STALE_GPU, STALE_TORCH, VRAM_TOO_SMALL, FamilyOption,
    ProfileSummary, Reason, Resolution, ScanKnobs, arch_supported, capability_of, cuda_tag, driver_tuple,
    refused_families, sm_name, unique_reasons, warning_reason,
)
from biliflow.hardware_schema import (
    FAILURE_MESSAGES, NVIDIA_VENDOR_ID, GpuInfo, Observation, OverrideRead, ProfileRead, gate_gpus,
    previous_had_cuda, read_override, read_profile,
)

Profile = tuple[str, Mapping[str, Any]]  # ("main" or "last_good", the document)


@dataclass(frozen=True)
class _Context:
    test_root: bool
    observed: Observation | None
    override: OverrideRead
    main: ProfileRead
    last_good: ProfileRead
    table: Any


def _is_test_root(root: Path) -> bool:
    """A test root (section 4.2): not this code's install root, and accepted by job_purge. Any error: no."""
    try:
        from biliflow import job_purge
        resolved = os.path.normcase(str(Path(root).resolve()))
        install = os.path.normcase(str(Path(job_purge.INSTALL_ROOT).resolve()))
        return resolved != install and job_purge.allowed_project_root(root) is True
    except Exception:  # noqa: BLE001 - an unknown root is never a test root
        return False


def resolve_root(root: Path, observed: Observation | None) -> Resolution:
    """Steps 0-10 of section 7.1; the first step that decides wins."""
    if observed is not None and type(observed) is not Observation:
        raise TypeError("observed phải là Observation hoặc None")
    from biliflow import hardware_table  # it takes its types from hardware_profile
    ctx = _Context(_is_test_root(root), observed, read_override(root), read_profile(root, "main"),
                   read_profile(root, "last_good"), hardware_table)
    if ctx.override.state == "invalid":
        return _result(ctx, OVERRIDE_INVALID, f"File công tắc phần cứng hỏng ({ctx.override.error}). "
                       "Bật hoặc tắt lại công tắc trên trang Cài đặt.")
    if ctx.override.cpu_only:
        return _cpu_branch(ctx, "cpu_only", None, (warning_reason("cpu_only_recorded"),))
    main, last_good = ctx.main, ctx.last_good
    if main.state == "ok":
        return _from_profile(ctx, ("main", main.document))
    if last_good.state == "ok" and observed is not None:
        return _from_profile(ctx, ("last_good", last_good.document))
    if main.state == "missing" and last_good.state == "missing":
        return _result(ctx, NEEDS_MEASUREMENT, "Chưa có hồ sơ phần cứng. Bấm Đo lại (cần tạm dừng hàng đợi).")
    if main.state == "invalid" or last_good.state == "invalid":
        detail = main.error if main.state == "invalid" else last_good.error
        return _result(ctx, INVALID, f"Hồ sơ phần cứng hỏng hoặc do bản mới hơn ghi ({detail}). Đo lại.")
    return _result(ctx, INVALID, "Chỉ có bản tốt gần nhất của hồ sơ mà chưa quan sát được phần cứng "
                   "để đối chiếu. Đo lại.", warnings=(warning_reason("not_observed"),))


def _result(ctx: _Context, status: str, message: str, *, profile: Profile | None = None,
            source: str | None = None, row: Any = None, families: Mapping[str, FamilyOption] | None = None,
            reasons: tuple[Reason, ...] = (), warnings: tuple[Reason, ...] = (),
            applies: bool = False) -> Resolution:
    ready = status in READY_STATUSES
    if source is None and profile is not None:
        source = "profile" if profile[0] == "main" else "last_good"
    caveats = () if row is None or not ready else tuple(
        caveat for caveat in row.caveats if caveat.code not in FAMILY_CAVEATS)
    options = families if ready and families is not None else refused_families(status)
    observed = ctx.observed
    return Resolution(
        status=status, message=message, source=source,
        row_id=None if row is None else row.row_id, row_status=None if row is None else row.status,
        table_version=ctx.table.TABLE_VERSION, families=MappingProxyType(dict(options)),
        reasons=reasons, warnings=unique_reasons(warnings), caveats=caveats, test_root=ctx.test_root,
        cpu_only=ctx.override.cpu_only, override_state=ctx.override.state,
        untested_fast=ctx.override.untested_fast, untested_fast_applies=applies,
        main_state=ctx.main.state, last_good_state=ctx.last_good.state,
        profile=None if profile is None else _summary(profile),
        observed_driver=None if observed is None else observed.nvidia_driver,
    )


def _summary(profile: Profile) -> ProfileSummary:
    which, doc = profile
    gate, attempt = doc["fingerprint"]["gate_key"], doc["attempt"]
    failure = attempt["failure"]
    return ProfileSummary(
        which, doc["written_at"], attempt["result"], None if failure is None else failure["code"],
        doc["content_sha256"], doc["fingerprint"]["sha256"], gate_gpus(gate), gate["nvidia_driver"],
        gate["torch_build"])


def _from_profile(ctx: _Context, profile: Profile) -> Resolution:
    """Steps 4-8 on the chosen profile (main, or last-good with an observation)."""
    which, doc = profile
    lead = (warning_reason("last_good_used"),) if which == "last_good" else ()
    if doc["attempt"]["failure"] is not None:
        return _failed(ctx, profile, lead)
    if _never_had_cuda(ctx, doc):
        return _no_cuda_machine(ctx, profile, lead)
    gpus, observed = gate_gpus(doc["fingerprint"]["gate_key"]), ctx.observed
    if len(gpus) > 1 or (observed is not None and len(observed.gpus) > 1):
        return _result(ctx, MULTI_GPU_UNSUPPORTED, "Chưa hỗ trợ máy có nhiều GPU NVIDIA.",
                       profile=profile, warnings=lead)
    compared = _compare(ctx, profile, gpus, lead)
    if isinstance(compared, Resolution):
        return compared
    if not gpus:
        return _result(ctx, NO_GATED_ROW, "Hồ sơ không có GPU NVIDIA, mà máy không đủ điều kiện "
                       "'chưa từng có CUDA' (máy từng có CUDA, hoặc có file hồ sơ hỏng). Bật Chỉ dùng CPU "
                       "nếu máy không có card NVIDIA, hoặc Đo lại.", profile=profile, warnings=lead + compared)
    return _gpu_branch(ctx, profile, gpus[0], lead + compared)


def _failed(ctx: _Context, profile: Profile, lead: tuple[Reason, ...]) -> Resolution:
    """Step 4: a recorded failure is refused, never run on the CPU.

    When torch saw the card but could not use it, the reason is named as in section 5.6: no code
    for the card in the installed torch, or an NVML driver below the minimum of the torch build.
    """
    doc = profile[1]
    # torch that did use CUDA (an NVML failure) says nothing about the card's code or the driver
    refusal = None if doc["detection"]["torch_cuda"]["ok"] else _failure_refusal(ctx.table, doc)
    if refusal is not None:
        status, message = refusal
        return _result(ctx, status, message, profile=profile, reasons=(Reason(status, message),), warnings=lead)
    code = doc["attempt"]["failure"]["code"]
    return _result(ctx, CUDA_UNAVAILABLE, FAILURE_MESSAGES[code], profile=profile, warnings=lead)


def _failure_refusal(table: Any, doc: Mapping[str, Any]) -> tuple[str, str] | None:
    """arch_unsupported when torch saw the card (capability, arch_list) without code for it; else
    driver_too_old when the NVML driver is below the known minimum of the torch build; else None."""
    torch_cuda, gate = doc["detection"]["torch_cuda"], doc["fingerprint"]["gate_key"]
    if torch_cuda["capability"] is not None and torch_cuda["arch_list"]:
        card = torch_cuda["device_name"] or next((gpu["name"] for gpu in gate["nvidia_gpus"]), "card NVIDIA")
        refusal = _arch_refusal(torch_cuda["arch_list"], torch_cuda["capability"], card, gate["torch_build"])
        if refusal is not None:
            return refusal
    if cuda_tag(gate["torch_build"]) in table.CUDA_DRIVER_MIN and driver_tuple(gate["nvidia_driver"]) is not None:
        return _driver_refusal(table, gate["nvidia_driver"], gate["torch_build"])
    return None


def _never_had_cuda(ctx: _Context, doc: Mapping[str, Any]) -> bool:
    """Section 3.5: no readable file had CUDA, none is broken, no 10DE adapter, NVML saw no GPU."""
    adapters = doc["detection"]["adapters"]
    return (not previous_had_cuda(ctx.main, ctx.last_good) and adapters["ok"]
            and all(item["vendor_id"] != NVIDIA_VENDOR_ID for item in adapters["items"])
            and not doc["fingerprint"]["gate_key"]["nvidia_gpus"])


def _no_cuda_machine(ctx: _Context, profile: Profile, lead: tuple[Reason, ...]) -> Resolution:
    """Step 5: the CPU branch, unless the observation sees (or cannot rule out) an NVIDIA card."""
    observed = ctx.observed
    if observed is None:
        return _cpu_branch(ctx, "no_cuda_machine", profile, lead + (warning_reason("not_observed"),))
    if observed.gpus or observed.nvidia_pci_seen is not False:
        return _result(ctx, STALE_GPU, "Máy có (hoặc không loại trừ được) card NVIDIA, mà hồ sơ ghi máy "
                       "chưa từng có CUDA. Đo lại.", profile=profile, warnings=lead)
    return _cpu_branch(ctx, "no_cuda_machine", profile, lead + _soft_warnings(profile[1], observed))


def _compare(ctx: _Context, profile: Profile, gpus: tuple[GpuInfo, ...],
             lead: tuple[Reason, ...]) -> Resolution | tuple[Reason, ...]:
    """Step 7: the observation against gate_key (table of section 3.3); soft warnings when it holds."""
    doc, observed = profile[1], ctx.observed
    if observed is None:
        return (warning_reason("not_observed"),) + _soft_warnings(doc, None)
    gate = doc["fingerprint"]["gate_key"]
    if gpus and observed.nvml != "ok":
        return _result(ctx, CUDA_UNAVAILABLE, "Không đọc được GPU NVIDIA qua NVML (driver hỏng hay đã gỡ). "
                       "Khởi động lại hoặc cài lại driver NVIDIA rồi Đo lại; hoặc bật Chỉ dùng CPU.",
                       profile=profile, warnings=lead)
    if tuple(observed.gpus) != gpus:
        return _result(ctx, STALE_GPU, f"GPU khác với lúc đo (nay: {_gpu_text(tuple(observed.gpus))}; "
                       f"lúc đo: {_gpu_text(gpus)}). Đo lại.", profile=profile, warnings=lead)
    if observed.torch_build != gate["torch_build"]:
        return _result(ctx, STALE_TORCH, f"Bản torch đã đổi (nay: {observed.torch_build}; lúc đo: "
                       f"{gate['torch_build']}). Đo lại.", profile=profile, warnings=lead)
    return _soft_warnings(doc, observed)


def _soft_warnings(doc: Mapping[str, Any], observed: Observation | None) -> tuple[Reason, ...]:
    gate_ffmpeg, detection = doc["fingerprint"]["gate_key"]["ffmpeg"], doc["detection"]
    warnings: list[Reason] = []
    if gate_ffmpeg is None or not detection["ffmpeg"]["ok"]:
        warnings.append(warning_reason("ffmpeg_unavailable"))
    elif observed is not None and (observed.ffmpeg is None or (observed.ffmpeg.size, observed.ffmpeg.mtime_ns)
                                   != (gate_ffmpeg["size"], gate_ffmpeg["mtime_ns"])):
        warnings.append(warning_reason("ffmpeg_changed"))
    model = detection["cpu"]["model"]
    if observed is not None and None not in (observed.cpu_name, model) and observed.cpu_name != model:
        warnings.append(warning_reason("machine_changed"))
    return tuple(warnings)


def _gpu_text(gpus: tuple[GpuInfo, ...]) -> str:
    return ", ".join(f"{gpu.name} ({gpu.compute_capability}, {gpu.vram_mib} MiB)" for gpu in gpus) or "không có GPU"


def _in_range(driver: str | None, match: Any) -> bool:
    value = driver_tuple(driver)
    low, high = driver_tuple(match.driver_min), driver_tuple(match.driver_max)
    if value is None or (match.driver_min is not None and low is None):
        return False
    return (low is None or value >= low) and (high is None or value <= high)


def _range_text(match: Any) -> str:
    if match.driver_max is None:
        return f"≥ {match.driver_min}"
    return f"{match.driver_min}–{match.driver_max}"


# --- step 8: the GPU rows, with section 5.6 for every combination without a gated row --------

def _gpu_branch(ctx: _Context, profile: Profile, gpu: GpuInfo, warnings: tuple[Reason, ...]) -> Resolution:
    doc = profile[1]
    gate, observed = doc["fingerprint"]["gate_key"], ctx.observed
    driver = observed.nvidia_driver if observed is not None and observed.nvidia_driver else gate["nvidia_driver"]
    torch = gate["torch_build"]
    row = _gated_match(ctx.table, gpu, torch, driver)
    if row is None:
        return _untested(ctx, profile, gpu, torch, driver, warnings)
    if driver != gate["nvidia_driver"]:
        warnings += (Reason("driver_changed", f"Driver đã đổi từ {gate['nvidia_driver']} sang {driver}, "
                            "vẫn trong khoảng đã qua cổng."),)
    reason = Reason("row_match", f"{gpu.name} ({gpu.compute_capability}), torch {torch}, "
                    f"driver {driver} {_range_text(row.match)}")
    status = READY if profile[0] == "main" else READY_LAST_GOOD
    return _result(ctx, status, f"Khớp cấu hình đã qua cổng {row.row_id}.", profile=profile, row=row,
                   families=_families(row, doc, driver, warnings), reasons=(reason,), warnings=warnings)


def _gated_match(table: Any, gpu: GpuInfo, torch: str | None, driver: str | None) -> Any:
    for row in table.ROWS:
        match = row.match
        if row.status != "gated" or match.vendor != "nvidia":
            continue
        if (match.compute_capability == gpu.compute_capability and gpu.vram_mib >= (match.min_vram_mib or 0)
                and match.gpu_count in (None, 1) and torch in match.torch_builds and _in_range(driver, match)):
            return row
    return None


def _arch_refusal(arch_list: Any, capability: str, card: str, torch: str | None) -> tuple[str, str] | None:
    """Section 5.6 condition 1, the one arch rule of steps 4 and 8: torch has code for the card, or why not."""
    parsed = capability_of(capability)
    if arch_supported(arch_list, parsed):
        return None
    have = " ".join(arch_list) or "không có"
    return ARCH_UNSUPPORTED, (f"Bản torch {torch} không có mã cho {sm_name(parsed)} của {card} "
                              f"(có: {have}). Cần bản torch mới hơn (bộ cài, C5).")


def _driver_refusal(table: Any, driver: str | None, torch: str | None) -> tuple[str, str] | None:
    """Section 5.6 condition 2: the driver reaches CUDA_DRIVER_MIN of the torch build (an unknown build fails)."""
    tag = cuda_tag(torch)
    minimum = table.CUDA_DRIVER_MIN.get(tag) if tag else None
    if minimum is None:
        return DRIVER_TOO_OLD, f"Chưa biết driver tối thiểu cho bản torch {torch}; cần thêm vào bảng (C5)."
    value = driver_tuple(driver)
    if value is not None and value >= driver_tuple(minimum):
        return None
    shown = driver if value is not None else "(không đọc được)"
    return DRIVER_TOO_OLD, (f"Driver {shown} thấp hơn {minimum}, mức tối thiểu cho CUDA {tag[2:-1]}.{tag[-1]} "
                            f"của bản torch {torch}. Cập nhật driver NVIDIA rồi Đo lại.")


def _untested_refusal(table: Any, doc: Mapping[str, Any], gpu: GpuInfo, torch: str | None,
                      driver: str | None) -> tuple[str, str] | None:
    """Section 5.6, conditions 1-3 in order: the first that fails names its status and says why."""
    refusal = (_arch_refusal(doc["detection"]["torch_cuda"]["arch_list"], gpu.compute_capability, gpu.name, torch)
               or _driver_refusal(table, driver, torch))
    if refusal is not None:
        return refusal
    if gpu.vram_mib < table.UNTESTED_MIN_VRAM_MIB:
        return VRAM_TOO_SMALL, (f"VRAM {gpu.vram_mib} MiB nhỏ hơn {table.UNTESTED_MIN_VRAM_MIB} MiB, mức nhỏ "
                                "nhất từng chạy hết pipeline (RTX 2060 6 GB).")
    return None


def _untested_labels(table: Any, gpu: GpuInfo, torch: str | None, driver: str | None) -> tuple[Reason, ...]:
    """Section 5.6: untested_arch on every untested choice, plus untested_torch / untested_driver.

    A part is untested when no gated row of this compute capability covers it; for a generation
    without a gated row, when no gated NVIDIA row covers it.
    """
    sm = sm_name(capability_of(gpu.compute_capability))
    gated = [row for row in table.ROWS if row.status == "gated" and row.match.vendor == "nvidia"]
    reference = [row for row in gated if row.match.compute_capability == gpu.compute_capability] or gated
    labels = [Reason("untested_arch", f"Chưa qua cổng trên đời card này ({sm})")]
    if not any(torch in row.match.torch_builds for row in reference):
        builds = ", ".join(sorted({build for row in reference for build in row.match.torch_builds})) or "không có"
        labels.append(Reason("untested_torch", f"Bản torch {torch} chưa qua cổng trên đời card này ({sm}); "
                             f"đã qua cổng: {builds}"))
    if not any(_in_range(driver, row.match) for row in reference):
        ranges = ", ".join(_range_text(row.match) for row in reference) or "không có"
        labels.append(Reason("untested_driver", f"Driver {driver} chưa qua cổng trên đời card này ({sm}); "
                             f"đã qua cổng: {ranges}"))
    return tuple(labels)


def _untested(ctx: _Context, profile: Profile, gpu: GpuInfo, torch: str | None, driver: str | None,
              warnings: tuple[Reason, ...]) -> Resolution:
    """Section 5.6: refuse with a precise status, or run the untested row (never refused for being untested)."""
    table, doc = ctx.table, profile[1]
    refusal = _untested_refusal(table, doc, gpu, torch, driver)
    if refusal is not None:
        status, message = refusal
        return _result(ctx, status, message, profile=profile, reasons=(Reason(status, message),),
                       warnings=warnings)
    switch = ctx.override.untested_fast
    applies = switch is not None and (switch.gpu_name, switch.compute_capability) == (
        gpu.name, gpu.compute_capability)
    warnings += _untested_labels(table, gpu, torch, driver) + (warning_reason("tf32_off"),)
    if applies:
        warnings += (table.UNTESTED_FAST_ON,)  # also a caveat of that row; choose() keeps one copy
    elif switch is not None:
        warnings += (warning_reason("untested_fast_other_card"),)
    row_id = table.UNTESTED_FAST_ROW_ID if applies else table.UNTESTED_ROW_ID
    row = next(row for row in table.ROWS if row.row_id == row_id)
    mode = "chế độ nhanh theo công tắc riêng" if applies else "chế độ chuẩn"
    message = (f"Chưa có cấu hình đã qua cổng cho {gpu.name} ({gpu.compute_capability}), torch {torch}, "
               f"driver {driver}: chạy {mode}, tắt TF32.")
    return _result(ctx, READY_UNTESTED, message, profile=profile, row=row,
                   families=_families(row, doc, driver, warnings), warnings=warnings, applies=applies)


# --- step 9: the CPU branch ------------------------------------------------------------------

def _cpu_branch(ctx: _Context, source: str, profile: Profile | None,
                warnings: tuple[Reason, ...]) -> Resolution:
    """Reached only from the CPU-only switch or a machine that never had CUDA (section 3.5)."""
    observed = ctx.observed
    doc = profile[1] if profile is not None else (ctx.main.document if ctx.main.state == "ok" else None)
    if observed is not None:
        torch = observed.torch_build
    else:  # not observed: the profile's torch build is shown, and choose() refuses
        torch = None if doc is None else doc["fingerprint"]["gate_key"]["torch_build"]
        warnings += (warning_reason("not_observed"),)
    rows = [row for row in ctx.table.ROWS if row.match.vendor == "none" and row.status != "retired"
            and torch is not None and torch in row.match.torch_builds]
    if not rows:
        message = (f"Không có dòng CPU cho bản torch {torch}." if torch is not None
                   else "Chưa biết bản torch (chưa quan sát được phần cứng); không chọn được dòng CPU.")
        return _result(ctx, NO_GATED_ROW, message, profile=profile, source=source, warnings=warnings)
    row = rows[0]
    if row.runnable_after is not None:
        return _result(ctx, CPU_ROW_NOT_READY, f"Đường CPU chưa chạy được trước {row.runnable_after}.",
                       profile=profile, source=source, row=row, warnings=warnings)
    if row.status == "ungated" and not ctx.test_root:
        return _result(ctx, NO_GATED_ROW, "Đường CPU chưa qua cổng; thư mục cài thật chỉ chạy cấu hình "
                       "đã qua cổng.", profile=profile, source=source, row=row, warnings=warnings)
    if row.status == "ungated":
        status, reasons = READY_UNGATED, (warning_reason("ungated_row"),)
    else:
        status, reasons = READY, (Reason("row_match", f"CPU, torch {torch}"),)
    return _result(ctx, status, f"Chạy trên CPU theo {row.row_id}.", profile=profile, source=source,
                   row=row, families=_families(row, None, None, warnings), reasons=reasons, warnings=warnings)


# --- families, capping and NVDEC (sections 2.3 and 5.5) --------------------------------------

def _families(row: Any, doc: Mapping[str, Any] | None, driver: str | None,
              warnings: tuple[Reason, ...]) -> dict[str, FamilyOption]:
    """Each requested family capped to the highest family of the row not above it."""
    codes = {warning.code for warning in warnings}
    options: dict[str, FamilyOption] = {}
    for requested in FAMILIES:
        granted = next((family for family in FAMILIES[FAMILIES.index(requested):] if family in row.families),
                       None)
        if granted is None:
            options[requested] = FamilyOption(requested, None, False, None, None, None, NO_GATED_ROW, ())
            continue
        knobs, notes = _decode_rule(row, row.families[granted], doc, driver, codes)
        if granted != requested:
            notes = (Reason("family_capped", f"Máy chưa có họ {requested}; job dùng họ {granted}."),) + notes
        notes += tuple(caveat for caveat in row.caveats if _caveat_applies(caveat.code, knobs))
        options[requested] = FamilyOption(
            requested, granted, granted != requested, knobs,
            knobs.text.logo_decode if knobs.form == "prewarm" else None, knobs.tf32, None, notes)
    return options


def _caveat_applies(code: str, knobs: ScanKnobs) -> bool:
    if code == "c1_off_fp32":
        return knobs.animation_precision == "fp32"
    if code == "fp16_anime_careful_only":
        return knobs.animation_precision == "fp16"
    if code == "fp16_violence_l_only":
        return knobs.violence_precision == "fp16"
    return False  # row-wide caveats go to Resolution.caveats


def _decode_rule(row: Any, knobs: ScanKnobs, doc: Mapping[str, Any] | None, driver: str | None,
                 codes: set[str]) -> tuple[ScanKnobs, tuple[Reason, ...]]:
    """Section 5.5: NVDEC only on a verified (driver, ffmpeg.exe) pair or a passed self-test."""
    if knobs.form != "prewarm":
        return knobs, ()
    blocker = _nvdec_blocker(row, doc, driver, codes)
    chosen = replace(knobs, text=replace(knobs.text, logo_decode="nvdec" if blocker is None else "cpu"))
    return chosen, () if blocker is None or blocker.code in codes else (blocker,)


def _nvdec_blocker(row: Any, doc: Mapping[str, Any] | None, driver: str | None,
                   codes: set[str]) -> Reason | None:
    for code in ("ffmpeg_changed", "ffmpeg_unavailable"):
        if code in codes:
            return warning_reason(code)
    if doc is None:
        return warning_reason("ffmpeg_unavailable")
    gate, nvdec = doc["fingerprint"]["gate_key"], doc["detection"]["ffmpeg"]["nvdec"]
    if nvdec["trial"] == "failed" or nvdec["framemd5_equal_cpu"] is False:
        return Reason("nvdec_selftest_failed", "Phép tự kiểm NVDEC thất bại: giải mã routing bằng CPU "
                      "(kết quả giống hệt).")
    if gate["ffmpeg"] is not None and (driver, gate["ffmpeg"]["sha256"]) in row.nvdec_verified:
        return None
    if nvdec["trial"] == "ok" and nvdec["framemd5_equal_cpu"] is True and gate["nvidia_driver"] == driver:
        return None
    return Reason("nvdec_unverified", f"Cặp driver {driver} và ffmpeg.exe chưa được kiểm NVDEC: giải mã "
                  "routing bằng CPU (kết quả giống hệt). Đo lại để tự kiểm.")
