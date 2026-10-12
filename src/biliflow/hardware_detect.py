"""Hardware detection for the hardware profile (docs/HARDWARE_PROFILE.md sections 3.2, 3.3, 6.3 step 6).

``detect()`` gathers what a measurement records: NVML, nvidia-smi (recorded only, to compare with
NVML), the display adapters of the registry, CPU, RAM, disk, power, a torch probe in a child process
and the FFmpeg version, accelerators and encoders. It only reports: ``hardware_schema.classify_attempt``
judges and ``hardware_profile.resolve`` decides. ``observe()`` is the light observation: no
subprocess and no torch.

Every contact with the machine goes through ``hardware_system.HardwareSystem``; tests pass a fake.
Imported only by the hardware_* modules, never by a scan stage. Importing it loads no torch, pynvml
or psutil and starts no thread. ``python -m biliflow.hardware_detect torch-probe`` is the torch
child, the only place torch is imported; it prints exactly one JSON line.
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import re
import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from biliflow.hardware_schema import (
    MAX_MESSAGE, NVDEC_CODECS, NVIDIA_VENDOR_ID, NVML_STATUSES, VENDOR_IDS, FileStat, Observation,
    loads_strict, path_replacements, sanitize_message,
)
from biliflow.hardware_system import (
    NOT_FOUND, AdapterInfo, AdapterRead, CommandResult, CpuInfo, HardwareSystem, NvmlRead, PowerInfo,
    WindowsSystem, describe, short_text,
)

NVIDIA_SMI_TIMEOUT_SECONDS = 10.0
TORCH_PROBE_TIMEOUT_SECONDS = 120.0
FFMPEG_TIMEOUT_SECONDS = 30.0
ADAPTER_TIMEOUT_SECONDS = 5.0  # the registry walk checks it between keys

NVIDIA_SMI_QUERY = ("--query-gpu=name,memory.total,driver_version,compute_cap",
                    "--format=csv,noheader,nounits")
ENCODERS = ("libx264", "h264_nvenc", "hevc_nvenc", "av1_nvenc", "h264_qsv", "hevc_qsv", "h264_amf", "hevc_amf")
FFMPEG_LISTINGS = ("-version", "-hwaccels", "-encoders")
NVDEC_CHAIN = "logo_routing"
DETECT_STEPS = ("nvml", "nvidia_smi", "adapters", "system", "torch_probe", "ffmpeg")

TORCH_PROBE_MODULE = "biliflow.hardware_detect"
TORCH_PROBE_COMMAND = "torch-probe"
CUDA_NOT_AVAILABLE = "cuda_not_available"
CUDA_ERROR = "cuda_error"  # reading the device failed
MATMUL_FAILED = "cuda_matmul_failed"  # e.g. the installed torch has no kernel for this card
MATMUL_MISMATCH = "cuda_matmul_mismatch"  # fp32 on the GPU differs from the CPU
PROBE_TIMEOUT = "torch_probe_timeout"
PROBE_CRASHED = "torch_probe_crashed"
TORCH_FIELDS = ("ok", "device_count", "device_name", "capability", "arch_list", "total_memory_bytes",
                "fp16_ok", "seconds", "error")
PROBE_KEYS = frozenset(TORCH_FIELDS + ("torch_cuda", "cudnn"))

MAX_GPUS = 8  # schema limits (hardware_schema)
MAX_ADAPTERS = 16
MAX_COUNT = 4096
MAX_VRAM_MIB = 10 ** 7
MAX_BYTES = 2 ** 63 - 1
MAX_PROBE_ERROR = 2000  # the parent cuts it to MAX_MESSAGE after sanitizing
MATRIX_SIZE = 64

FFMPEG_MISSING = "Không có ffmpeg.exe trong thư mục FFmpeg."
UNKNOWN_ERROR = "lỗi không rõ"

_NOT_AVAILABLE = frozenset({"", "[N/A]", "N/A", "[Not Supported]"})
_NVML_DRIVER = re.compile(r"\d{1,4}\.\d{1,3}")
_CAPABILITY = re.compile(r"\d{1,2}\.\d{1,2}")
_DIGITS = re.compile(r"\d{1,9}")
_FFMPEG_VERSION = re.compile(r"ffmpeg version n?(\d{1,3}\.\d{1,4}(?:\.\d{1,4})?)(?![\d.])")
_HWACCEL = re.compile(r"[a-z0-9_]{1,40}")
_ENCODER_LINE = re.compile(r"\s*[VAS][A-Z.]{5}\s+([A-Za-z0-9_-]{1,40})(?:\s|$)")
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://\S+")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


@dataclass(frozen=True)
class Detection:
    """detect()'s result in the shapes of section 3.2. Callers never mutate the dicts."""
    gate_key: dict[str, Any]
    info: dict[str, Any]
    detection: dict[str, Any]


# --- detect() (section 6.3 step 6) --------------------------------------------------------------

def detect(root: Path, *, ffmpeg_dir: Path, python_exe: Path, system: HardwareSystem | None = None,
           progress: Callable[[str], None] | None = None) -> Detection:
    """What a measurement records. It reports only; classify_attempt decides the outcome.

    ``progress`` is called with each name of DETECT_STEPS before that step runs.
    """
    machine = system if system is not None else WindowsSystem()
    step = progress if progress is not None else _no_progress
    replacements = detect_replacements(root, ffmpeg_dir=ffmpeg_dir, python_exe=python_exe)
    step("nvml")
    nvml = checked_nvml(machine.nvml())
    step("nvidia_smi")
    smi = _nvidia_smi(machine, replacements)
    step("adapters")
    adapters = _adapters_dict(machine.adapters(timeout=ADAPTER_TIMEOUT_SECONDS), replacements)
    step("system")
    cpu = _cpu_dict(machine.cpu(), machine.cpu_counts())
    ram = _bounded_int(machine.ram_bytes(), MAX_BYTES)
    disk = _disk_dict(machine, root)
    power = _power_dict(machine.power())
    step("torch_probe")
    torch_cuda, probe_info = _torch_probe(machine, python_exe, replacements)
    step("ffmpeg")
    ffmpeg, ffmpeg_key = _ffmpeg(machine, ffmpeg_dir, replacements)
    platform_info = machine.platform_info()
    gate_key = {
        "nvidia_gpus": [{"name": gpu.name, "compute_capability": gpu.compute_capability, "vram_mib": gpu.vram_mib}
                        for gpu in nvml.gpus],
        "nvidia_driver": nvml.driver,
        "torch_build": short_text(machine.torch_build(), 60),
        "ffmpeg": ffmpeg_key,
    }
    info = {"os": short_text(platform_info.os, 120), "python": short_text(platform_info.python, 40), **probe_info}
    detection = {
        "nvml": {"status": nvml.status, "error": None if nvml.error is None else _clean(nvml.error, replacements)},
        "nvidia_smi": smi, "torch_cuda": torch_cuda, "ffmpeg": ffmpeg, "cpu": cpu, "ram_bytes": ram,
        "disk": disk, "power": power, "adapters": adapters,
    }
    return Detection(gate_key, info, detection)


def _no_progress(_step: str) -> None:
    return None


def detect_replacements(root: Path, *, ffmpeg_dir: Path, python_exe: Path) -> tuple[tuple[str, str], ...]:
    """path_replacements plus <ffmpeg>, <python> and <venv> (torch's own files live there).

    Each label stands for an absolute folder; a relative path ("." would match every dot) or a
    drive root is never a label.
    """
    python_dir = Path(os.path.abspath(python_exe)).parent
    folders = [(Path(os.path.abspath(ffmpeg_dir)), "<ffmpeg>"), (python_dir, "<python>")]
    if python_dir.name.lower() == "scripts":
        folders.append((python_dir.parent, "<venv>"))
    return path_replacements(root, *((folder, label) for folder, label in folders if len(folder.parts) > 1))


def checked_nvml(read: NvmlRead) -> NvmlRead:
    """NVML's answer in the profile's shapes; anything else is reported as an NVML error."""
    if read.status not in NVML_STATUSES:
        return NvmlRead("error", error=f"trạng thái NVML lạ: {str(read.status)[:40]}")
    if read.status != "ok":
        return NvmlRead(read.status, error=read.error or UNKNOWN_ERROR)
    problem = _nvml_problem(read)
    if problem is not None:
        return NvmlRead("error", error=f"NVML trả dữ liệu lạ: {problem}")
    return NvmlRead("ok", driver=read.driver, gpus=tuple(read.gpus))


def _nvml_problem(read: NvmlRead) -> str | None:
    if not isinstance(read.driver, str) or not _NVML_DRIVER.fullmatch(read.driver):
        return "phiên bản driver"
    if len(read.gpus) > MAX_GPUS:
        return f"hơn {MAX_GPUS} GPU"
    for gpu in read.gpus:
        if not isinstance(gpu.name, str) or not 0 < len(gpu.name) <= 120 or _CONTROL.search(gpu.name):
            return "tên GPU"
        if not isinstance(gpu.compute_capability, str) or not _CAPABILITY.fullmatch(gpu.compute_capability):
            return "compute capability"
        if type(gpu.vram_mib) is not int or not 0 <= gpu.vram_mib <= MAX_VRAM_MIB:
            return "VRAM"
    return None


# --- nvidia-smi: recorded only, to compare with NVML --------------------------------------------

def _nvidia_smi(machine: HardwareSystem, replacements: Sequence[tuple[str, str]]) -> dict[str, Any] | None:
    """detection.nvidia_smi; None when nvidia-smi.exe is not installed in System32 (no NVIDIA driver)."""
    folder = machine.system_directory()
    if folder is None:
        return _smi_failure("nvidia-smi: không tìm được thư mục System32")
    result = machine.run([str(Path(folder) / "nvidia-smi.exe"), *NVIDIA_SMI_QUERY],
                         timeout=NVIDIA_SMI_TIMEOUT_SECONDS)
    if result.error == NOT_FOUND:
        return None
    problem = command_problem(result)
    if problem is not None:
        return _smi_failure(_clean(f"nvidia-smi: {problem}", replacements))
    try:
        driver, gpus = parse_nvidia_smi(result.stdout)
    except ValueError as error:  # the messages are fixed text, never the program's output
        return _smi_failure(f"nvidia-smi: kết quả không đọc được ({error})")
    return {"ok": True, "driver_version": driver, "gpus": gpus, "error": None}


def parse_nvidia_smi(stdout: str) -> tuple[str | None, list[dict[str, Any]]]:
    """(driver, gpus) from ``name, memory.total, driver_version, compute_cap`` lines; "[N/A]" is null."""
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if len(lines) > MAX_GPUS:
        raise ValueError(f"hơn {MAX_GPUS} GPU")
    drivers: set[str] = set()
    gpus: list[dict[str, Any]] = []
    for line in lines:
        parts = [part.strip() for part in line.rsplit(",", 3)]  # a name may hold a comma, the numbers never
        if len(parts) != 4:
            raise ValueError("dòng không đủ 4 cột")
        name, memory, driver, capability = parts
        if name in _NOT_AVAILABLE or len(name) > 120 or _CONTROL.search(name):
            raise ValueError("tên GPU")
        vram = _smi_value(memory, _DIGITS)
        if vram is not None and int(vram) > MAX_VRAM_MIB:
            raise ValueError("VRAM")
        gpus.append({"name": name, "compute_capability": _smi_value(capability, _CAPABILITY),
                     "vram_mib": None if vram is None else int(vram)})
        version = _smi_value(driver, _NVML_DRIVER)
        if version is not None:
            drivers.add(version)
    if len(drivers) > 1:
        raise ValueError("driver khác nhau giữa các dòng")
    return (next(iter(drivers)) if drivers else None), gpus


def _smi_value(text: str, pattern: re.Pattern[str]) -> str | None:
    if text in _NOT_AVAILABLE:
        return None
    if not pattern.fullmatch(text):
        raise ValueError("giá trị lạ")
    return text


def _smi_failure(error: str) -> dict[str, Any]:
    return {"ok": False, "driver_version": None, "gpus": [], "error": error}


def command_problem(result: CommandResult) -> str | None:
    """Why a command failed, without its output (it may hold a build banner or a path)."""
    if result.timed_out:
        return "quá giờ"
    if result.error is not None:
        return f"không chạy được ({result.error})"
    if result.returncode != 0:
        return f"thoát mã {result.returncode}"
    return None


# --- adapters, CPU, disk, power -----------------------------------------------------------------

def _adapters_dict(read: AdapterRead, replacements: Sequence[tuple[str, str]]) -> dict[str, Any]:
    if not read.ok:
        return {"ok": False, "error": _clean(read.error or UNKNOWN_ERROR, replacements), "items": []}
    items = [{"vendor_id": item.vendor_id, "vendor": VENDOR_IDS.get(item.vendor_id, "other"),
              "device_id": item.device_id, "name": item.name, "driver_version": item.driver_version}
             for item in _bounded_adapters(read.items)]
    return {"ok": True, "error": None, "items": items}


def _bounded_adapters(items: Sequence[AdapterInfo]) -> tuple[AdapterInfo, ...]:
    """Duplicates collapsed; past the schema limit, NVIDIA cards are kept first (they decide had_cuda)."""
    unique = tuple(dict.fromkeys(items))
    if len(unique) <= MAX_ADAPTERS:
        return unique
    nvidia = tuple(item for item in unique if item.vendor_id == NVIDIA_VENDOR_ID)
    others = tuple(item for item in unique if item.vendor_id != NVIDIA_VENDOR_ID)
    return (nvidia + others)[:MAX_ADAPTERS]


def _cpu_dict(cpu: CpuInfo, counts: tuple[int | None, int | None]) -> dict[str, Any]:
    physical, logical = counts
    return {"model": short_text(cpu.model, 120), "vendor": short_text(cpu.vendor, 40),
            "physical_cores": _bounded_int(physical, MAX_COUNT), "logical_cores": _bounded_int(logical, MAX_COUNT),
            "features": {"avx2": _flag(cpu.avx2), "avx512f": _flag(cpu.avx512f)}}


def _disk_dict(machine: HardwareSystem, root: Path) -> dict[str, Any]:
    anchor = Path(root).resolve().anchor
    usage = machine.disk_usage(Path(anchor)) if anchor else None
    total, free = usage if usage is not None else (None, None)
    return {"root_total_bytes": _bounded_int(total, MAX_BYTES), "root_free_bytes": _bounded_int(free, MAX_BYTES)}


def _power_dict(power: PowerInfo) -> dict[str, Any]:
    return {"battery_present": _flag(power.battery_present), "ac_online": _flag(power.ac_online)}


# --- FFmpeg: listings only; the trials are E2's --------------------------------------------------

def _ffmpeg(machine: HardwareSystem, ffmpeg_dir: Path,
            replacements: Sequence[tuple[str, str]]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """(detection.ffmpeg, gate_key.ffmpeg). A missing or unreadable ffmpeg.exe gives gate_key.ffmpeg None."""
    exe = Path(os.path.abspath(Path(ffmpeg_dir) / "ffmpeg.exe"))
    found = machine.file_stat(exe)
    if found is None:
        return _ffmpeg_record(exe, error=FFMPEG_MISSING), None
    try:
        digest = machine.file_sha256(exe)
    except OSError as error:
        return _ffmpeg_record(exe, error=_clean(f"không đọc được ffmpeg.exe ({describe(error)})", replacements)), None
    outputs: dict[str, str] = {}
    problem: str | None = None
    for flag in FFMPEG_LISTINGS:
        result = machine.run([str(exe), "-hide_banner", flag], timeout=FFMPEG_TIMEOUT_SECONDS)
        failure = command_problem(result)
        if failure is not None:
            problem = _clean(f"ffmpeg {flag}: {failure}", replacements)
            break  # a broken ffmpeg would cost the same timeout again
        outputs[flag] = result.stdout
    version = ffmpeg_version(outputs.get("-version", ""))
    record = _ffmpeg_record(exe, error=problem, version=version, digest=digest, found=found,
                            hwaccels=parse_hwaccels(outputs.get("-hwaccels", "")),
                            encoders=parse_encoders(outputs.get("-encoders", "")))
    return record, {"version": version, "sha256": digest, "size": found.size, "mtime_ns": found.mtime_ns}


def _ffmpeg_record(exe: Path, *, error: str | None, version: str | None = None, digest: str | None = None,
                   found: FileStat | None = None, hwaccels: tuple[str, ...] = (),
                   encoders: frozenset[str] = frozenset()) -> dict[str, Any]:
    path = str(exe)
    return {
        "ok": error is None, "error": error, "path": path if len(path) <= 1024 else None, "version": version,
        "sha256": digest, "size": None if found is None else found.size,
        "mtime_ns": None if found is None else found.mtime_ns, "hwaccels_listed": list(hwaccels),
        "encoders": {name: {"listed": name in encoders, "trial": "not_run", "error": None} for name in ENCODERS},
        "nvdec": {"listed": "cuda" in hwaccels, "trial": "not_run", "framemd5_equal_cpu": None, "chain": NVDEC_CHAIN},
        "nvdec_codecs": {codec: {"trial": "not_run", "error": None} for codec in NVDEC_CODECS},
    }


def ffmpeg_version(stdout: str) -> str | None:
    """Only the numeric version (``9.0.1``); the rest of the line names the build's site and is dropped."""
    lines = stdout.strip().splitlines()
    match = _FFMPEG_VERSION.match(lines[0].strip()) if lines else None
    return match.group(1) if match else None


def parse_hwaccels(stdout: str) -> tuple[str, ...]:
    names = (line.strip() for line in stdout.splitlines())
    return tuple(dict.fromkeys(name for name in names if _HWACCEL.fullmatch(name)))[:32]


def parse_encoders(stdout: str) -> frozenset[str]:
    return frozenset(match.group(1) for line in stdout.splitlines() if (match := _ENCODER_LINE.match(line)))


# --- the torch probe: parent side ----------------------------------------------------------------

class ProbeError(ValueError):
    """The torch child broke its contract; the message says how."""


def torch_probe_command(python_exe: Path | str) -> list[str]:
    return [str(python_exe), "-B", "-m", TORCH_PROBE_MODULE, TORCH_PROBE_COMMAND]


def _child_environment() -> dict[str, str]:
    """The child imports this checkout's code first, whatever PYTHONPATH this process was given.

    CUDA_DEVICE_ORDER=PCI_BUS_ID numbers the devices as NVML does (torch's default puts the
    fastest first), so torch's device 0 is NVML's GPU 0 when the two are compared.
    PYTHONIOENCODING=utf-8: on a pipe the child would write stderr in the ANSI code page, and the
    parent reads UTF-8; a non-ASCII path in a traceback would then be garbled past the path labels.
    """
    source = str(Path(__file__).resolve().parents[1])
    current = os.environ.get("PYTHONPATH", "")
    return {**os.environ, "PYTHONPATH": os.pathsep.join(part for part in (source, current) if part),
            "CUDA_DEVICE_ORDER": "PCI_BUS_ID", "PYTHONIOENCODING": "utf-8"}


def _torch_probe(machine: HardwareSystem, python_exe: Path,
                 replacements: Sequence[tuple[str, str]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(detection.torch_cuda, the probe's part of fingerprint.info)."""
    started = time.monotonic()
    result = machine.run(torch_probe_command(python_exe), timeout=TORCH_PROBE_TIMEOUT_SECONDS,
                         env=_child_environment())
    elapsed = round(max(time.monotonic() - started, 0.0), 3)
    no_info = {"torch_cuda": None, "cudnn": None}
    if result.timed_out:
        return _probe_failure(PROBE_TIMEOUT, elapsed), no_info
    try:
        payload = parse_torch_probe(result)
    except ProbeError as problem:
        return _probe_failure(_clean(f"{PROBE_CRASHED}: {problem}", replacements), elapsed), no_info
    error = payload["error"]
    torch = {**{key: payload[key] for key in TORCH_FIELDS}, "arch_list": list(payload["arch_list"]),
             "error": None if error is None else _clean(error, replacements)}
    return torch, {"torch_cuda": payload["torch_cuda"], "cudnn": payload["cudnn"]}


def _probe_failure(error: str, seconds: float) -> dict[str, Any]:
    return {"ok": False, "device_count": 0, "device_name": None, "capability": None, "arch_list": [],
            "total_memory_bytes": None, "fp16_ok": None, "seconds": seconds, "error": error}


def parse_torch_probe(result: CommandResult) -> dict[str, Any]:
    """The child's single JSON line, checked strictly; raises ProbeError (a crash) otherwise."""
    if result.timed_out:
        raise ProbeError("quá giờ")
    if result.error is not None:
        raise ProbeError(f"không chạy được python ({result.error})")
    if result.returncode != 0:
        raise ProbeError(f"thoát mã {result.returncode}{_last_line(result.stderr)}")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ProbeError(f"in {len(lines)} dòng thay vì 1")
    try:
        payload = loads_strict(lines[0].encode("utf-8"))
    except (UnicodeError, ValueError):
        raise ProbeError("dòng in ra không phải JSON") from None
    problem = _probe_problem(payload)
    if problem is not None:
        raise ProbeError(problem)
    return payload


def _probe_problem(payload: Any) -> str | None:
    if type(payload) is not dict or set(payload) != PROBE_KEYS:
        return "khóa của kết quả sai"
    if type(payload["ok"]) is not bool or type(payload["device_count"]) is not int \
            or not 0 <= payload["device_count"] <= 64:
        return "ok hoặc device_count sai kiểu"
    for key, limit in (("device_name", 120), ("torch_cuda", 20), ("cudnn", 20), ("error", MAX_PROBE_ERROR)):
        value = payload[key]
        if value is not None and (type(value) is not str or not 0 < len(value) <= limit):
            return f"{key} sai kiểu"
    capability, arch = payload["capability"], payload["arch_list"]
    if capability is not None and (type(capability) is not str or not _CAPABILITY.fullmatch(capability)):
        return "capability sai kiểu"
    if type(arch) is not list or len(arch) > 32 or any(type(a) is not str or not 0 < len(a) <= 16 for a in arch):
        return "arch_list sai kiểu"
    memory, fp16, seconds = payload["total_memory_bytes"], payload["fp16_ok"], payload["seconds"]
    if memory is not None and (type(memory) is not int or not 0 <= memory <= MAX_BYTES):
        return "total_memory_bytes sai kiểu"
    if fp16 is not None and type(fp16) is not bool:
        return "fp16_ok sai kiểu"
    if seconds is not None and (type(seconds) not in (int, float) or not 0 <= seconds < math.inf):
        return "seconds sai kiểu"
    if payload["ok"] and (payload["device_count"] < 1 or capability is None or payload["error"] is not None):
        return "ok mà thiếu thiết bị hay compute capability"
    if not payload["ok"] and payload["error"] is None:
        return "thất bại mà không có lỗi"
    return None


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return f": {lines[-1][:200]}" if lines else ""


# --- the torch probe: child side (only here is torch imported) ----------------------------------

def run_torch_probe(torch: Any, *, started: float, clock: Callable[[], float] = time.perf_counter) -> dict[str, Any]:
    """The child's measurement; ``torch`` is the imported module (a fake in tests).

    The device facts are kept when the matmul fails, so the profile shows which card and which
    ``arch_list`` did not work together (resolve's ARCH_UNSUPPORTED reads them, section 5.6).
    """
    versions = {"torch_cuda": short_text(torch.version.cuda, 20), "cudnn": _cudnn_version(torch)}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        found = _probe_device(torch)
    error = found.get("error")
    note = next((str(item.message).strip() for item in caught if str(item.message).strip()), "")
    if error is not None and note:  # torch explains a missing driver or an unsupported card only in a warning
        found = {**found, "error": f"{error}: {note}"}
    if found.get("error") is not None:
        found = {**found, "error": found["error"][:MAX_PROBE_ERROR]}
    base = {"ok": False, "device_count": 0, "device_name": None, "capability": None, "arch_list": [],
            "total_memory_bytes": None, "fp16_ok": None, "error": None}
    return {**base, **versions, **found, "seconds": round(max(clock() - started, 0.0), 3)}


def _probe_device(torch: Any) -> dict[str, Any]:
    if not torch.cuda.is_available():
        return {"error": CUDA_NOT_AVAILABLE}
    try:
        facts = _cuda_facts(torch)
    except Exception as error:  # a CUDA error is a finding of the probe, not a crash of it
        return {"error": f"{CUDA_ERROR}: {_first_line(error)}"}
    try:
        fp32_ok, fp16_ok = _matmul_check(torch)
    except Exception as error:
        return {**facts, "error": f"{MATMUL_FAILED}: {_first_line(error)}"}
    if not fp32_ok:
        return {**facts, "fp16_ok": fp16_ok, "error": MATMUL_MISMATCH}
    return {**facts, "ok": True, "fp16_ok": fp16_ok}


def _first_line(error: BaseException) -> str:
    """A CUDA error spans several lines of advice; its first line says what failed."""
    lines = str(error).strip().splitlines()
    return f"{type(error).__name__}: {lines[0].strip()}" if lines else type(error).__name__


def _cudnn_version(torch: Any) -> str | None:
    try:
        value = torch.backends.cudnn.version()
    except (RuntimeError, OSError):
        return None
    return None if value is None else short_text(str(value), 20)


def _cuda_facts(torch: Any) -> dict[str, Any]:
    major, minor = torch.cuda.get_device_capability(0)
    return {
        "device_count": int(torch.cuda.device_count()),
        "device_name": short_text(torch.cuda.get_device_name(0), 120),
        "capability": f"{int(major)}.{int(minor)}",
        "arch_list": [str(arch)[:16] for arch in torch.cuda.get_arch_list()][:32],
        "total_memory_bytes": int(torch.cuda.get_device_properties(0).total_memory),
    }


def _matmul_check(torch: Any) -> tuple[bool, bool]:
    """(fp32 on the GPU equals the CPU, fp16 is finite and close to fp32): a small matmul, synchronized."""
    matrix = torch.linspace(-1.0, 1.0, MATRIX_SIZE * MATRIX_SIZE, device="cuda").reshape(MATRIX_SIZE, MATRIX_SIZE)
    full = matrix @ matrix
    half = (matrix.half() @ matrix.half()).float()
    torch.cuda.synchronize()
    reference = matrix.cpu() @ matrix.cpu()
    fp32_ok = bool(torch.allclose(full.cpu(), reference, rtol=1e-3, atol=1e-3))
    fp16_ok = bool(torch.isfinite(half).all().item()) and bool(torch.allclose(half, full, rtol=1e-2, atol=1e-1))
    return fp32_ok, fp16_ok


def main(argv: Sequence[str] | None = None) -> int:
    """``torch-probe`` prints one JSON line and exits 0; any other argument exits 64 silently."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments != [TORCH_PROBE_COMMAND]:
        return 64
    started = time.perf_counter()
    with contextlib.redirect_stdout(sys.stderr):  # whatever torch prints must not reach the JSON line
        import torch
        payload = run_torch_probe(torch, started=started)
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return 0


# --- observe() (section 3.3) -------------------------------------------------------------------

def observe(root: Path, *, ffmpeg_exe: Path | str | None, system: HardwareSystem | None = None) -> Observation:
    """The light observation: never a subprocess, never torch. ``root`` is kept for the contract."""
    machine = system if system is not None else WindowsSystem()
    nvml = checked_nvml(machine.nvml())
    adapters = machine.adapters(timeout=ADAPTER_TIMEOUT_SECONDS)
    seen = any(item.vendor_id == NVIDIA_VENDOR_ID for item in adapters.items) if adapters.ok else None
    return Observation(
        nvml=nvml.status, gpus=nvml.gpus, nvidia_driver=nvml.driver, nvidia_pci_seen=seen,
        torch_build=short_text(machine.torch_build(), 60),
        ffmpeg=machine.file_stat(Path(ffmpeg_exe)) if ffmpeg_exe else None,
        cpu_name=short_text(machine.cpu().model, 120),
    )


# --- small helpers ------------------------------------------------------------------------------

def _clean(text: object, replacements: Sequence[tuple[str, str]]) -> str:
    """Error text for the profile: no URL, no local path prefix, no control character, at most 300 chars."""
    return sanitize_message(_URL.sub("<url>", str(text)), replacements, MAX_MESSAGE) or UNKNOWN_ERROR


def _bounded_int(value: object, high: int) -> int | None:
    return value if type(value) is int and 0 <= value <= high else None


def _flag(value: object) -> bool | None:
    return value if type(value) is bool else None


if __name__ == "__main__":
    raise SystemExit(main())
