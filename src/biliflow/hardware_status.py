"""The JSON of GET /api/hardware-profile (docs/HARDWARE_PROFILE.md section 6.1).

Pure shaping of what resolve() returned, the profile document it used and the light observation;
hardware_manager adds ``task`` and ``can_remeasure``. Every free text goes through ``clean``, the
manager's sanitize_message with <root>, <install>, <code> and <user>; nothing here holds a token.

Imported only by hardware_manager, never by a scan stage. Standard library and hardware_profile
(with its hardware_schema) only; nothing runs at import.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from biliflow import hardware_profile
from biliflow.hardware_profile import FAMILIES, INVALID, Observation, Reason, Resolution, read_profile
from biliflow.hardware_schema import FAILURE_MESSAGES

Clean = Callable[[object], str]
FALLBACK_MESSAGE = "Lỗi nội bộ khi đọc hồ sơ phần cứng ({error}). Tải lại trang hoặc báo lỗi."


def payload(root: Path, resolution: Resolution, observed: Observation | None, clean: Clean) -> dict[str, Any]:
    """Every key of section 6.1 except ``task`` and ``can_remeasure``.

    ``observed`` None means the light observation failed: ``observed`` is null and the warnings
    carry ``not_observed``. ``message`` (resolve()'s own text) is added for the refusals.
    """
    which, document = shown_profile(root, resolution)
    return {
        "status": resolution.status, "applies_to_jobs": False, "message": clean(resolution.message),
        "row": row(resolution), "families": families(resolution),
        "reasons": reasons(resolution.reasons, clean),
        "warnings": reasons(merged_warnings(resolution, observed), clean),
        "cpu_only": {"value": resolution.cpu_only, "valid": resolution.override_state != "invalid"},
        "untested_fast": untested_fast(resolution),
        "profile": profile(resolution, which, document, clean),
        "detection": None if document is None else detection(document, clean),
        "observed": None if observed is None else observation(observed, clean),
    }


def fallback(error: BaseException) -> dict[str, Any]:
    """The answer when building the payload failed (a bug): the page still gets a status."""
    return {
        "status": INVALID, "applies_to_jobs": False, "message": FALLBACK_MESSAGE.format(error=type(error).__name__),
        "row": None, "families": {name: {"family": None, "refusal": INVALID} for name in FAMILIES},
        "reasons": [], "warnings": [], "cpu_only": {"value": False, "valid": False},
        "untested_fast": {"value": False, "gpu": None, "applies": False},
        "profile": {"present": False, "source": None}, "detection": None, "observed": None,
    }


def reasons(items: tuple[Reason, ...], clean: Clean) -> list[dict[str, str]]:
    return [{"code": reason.code, "text": clean(reason.text)} for reason in items]


def row(resolution: Resolution) -> dict[str, Any] | None:
    if resolution.row_id is None:
        return None
    return {"id": resolution.row_id, "status": resolution.row_status, "table_version": resolution.table_version}


def families(resolution: Resolution) -> dict[str, Any]:
    """Each requested family: the granted one (capped, tf32, logo_decode of the prewarm form) or its refusal."""
    result: dict[str, Any] = {}
    for name in FAMILIES:
        option = resolution.families.get(name)
        if option is None or option.family is None:
            result[name] = {"family": None, "refusal": resolution.status if option is None else option.refusal}
            continue
        entry: dict[str, Any] = {"family": option.family, "capped": option.capped, "tf32": option.tf32,
                                 "refusal": None}
        if option.logo_decode is not None:
            entry["logo_decode"] = option.logo_decode
        result[name] = entry
    return result


def merged_warnings(resolution: Resolution, observed: Observation | None) -> tuple[Reason, ...]:
    """Resolution.warnings + Resolution.caveats + every FamilyOption.warnings, each once.

    untested_fast_on sits in the first two; c1_off_fp32 only in the families.
    """
    items = resolution.warnings + resolution.caveats + tuple(
        warning for name in FAMILIES if name in resolution.families
        for warning in resolution.families[name].warnings)
    if observed is None and all(reason.code != "not_observed" for reason in items):
        items += (hardware_profile.warning_reason("not_observed"),)
    return hardware_profile.unique_reasons(items)


def untested_fast(resolution: Resolution) -> dict[str, Any]:
    """The switch of section 5.6: on or off, the card it was turned on for, and whether it holds here."""
    switch = resolution.untested_fast
    gpu = None if switch is None else {"name": switch.gpu_name, "compute_capability": switch.compute_capability,
                                       "enabled_at": switch.enabled_at}
    return {"value": switch is not None, "gpu": gpu, "applies": resolution.untested_fast_applies}


def shown_profile(root: Path, resolution: Resolution) -> tuple[str | None, dict[str, Any] | None]:
    """The profile the page describes: the one resolve() used, else the first readable file."""
    if resolution.profile is not None:
        which: str | None = resolution.profile.which
    else:
        which = next((name for name, state in (("main", resolution.main_state),
                                               ("last_good", resolution.last_good_state)) if state == "ok"), None)
    if which is None:
        return None, None
    read = read_profile(root, which)
    return (which, read.document) if read.state == "ok" else (None, None)


def profile(resolution: Resolution, which: str | None, document: dict[str, Any] | None,
            clean: Clean) -> dict[str, Any]:
    """The last measurement: when, its result and failure, and the power source while it ran."""
    states = {"main_state": resolution.main_state, "last_good_state": resolution.last_good_state}
    if document is None:
        return {"present": False, "source": None, **states}
    attempt = document["attempt"]
    failure = attempt["failure"]
    return {
        "present": True, "source": which, "written_at": document["written_at"], "result": attempt["result"],
        "failure": None if failure is None else {
            "code": failure["code"], "step": clean(failure["step"]),
            "text": FAILURE_MESSAGES.get(failure["code"], ""), "message": clean(failure["message"])},
        "power": dict(document["detection"]["power"]), "launched_by": document["writer"]["launched_by"],
        **states,
    }


def detection(document: dict[str, Any], clean: Clean) -> dict[str, Any]:
    """GPU, VRAM, driver, torch, CPU, RAM and the FFmpeg trials of the profile (never a path)."""
    gate, found = document["fingerprint"]["gate_key"], document["detection"]
    gpu = gate["nvidia_gpus"][0] if gate["nvidia_gpus"] else None
    ffmpeg, ram, cpu = found["ffmpeg"], found["ram_bytes"], found["cpu"]["model"]
    return {
        "gpu": None if gpu is None else clean(gpu["name"]),
        "compute_capability": None if gpu is None else gpu["compute_capability"],
        "vram_mib": None if gpu is None else gpu["vram_mib"], "gpu_count": len(gate["nvidia_gpus"]),
        "driver": gate["nvidia_driver"], "torch": gate["torch_build"],
        "cpu": None if cpu is None else clean(cpu), "ram_gib": None if ram is None else round(ram / 2 ** 30, 1),
        "ffmpeg": {
            "ok": ffmpeg["ok"], "version": ffmpeg["version"],
            "encoders": {name: entry["trial"] for name, entry in ffmpeg["encoders"].items()},
            "nvdec": {"trial": ffmpeg["nvdec"]["trial"],
                      "framemd5_equal_cpu": ffmpeg["nvdec"]["framemd5_equal_cpu"]},
            "nvdec_codecs": {codec: entry["trial"] for codec, entry in ffmpeg["nvdec_codecs"].items()},
        },
    }


def observation(observed: Observation, clean: Clean) -> dict[str, Any]:
    """What the light observation sees now (section 3.3)."""
    return {
        "nvml": observed.nvml, "driver": observed.nvidia_driver, "torch": observed.torch_build,
        "gpus": [{"name": clean(gpu.name), "compute_capability": gpu.compute_capability,
                  "vram_mib": gpu.vram_mib} for gpu in observed.gpus],
        "nvidia_pci_seen": observed.nvidia_pci_seen, "ffmpeg_found": observed.ffmpeg is not None,
        "cpu": None if observed.cpu_name is None else clean(observed.cpu_name),
    }
