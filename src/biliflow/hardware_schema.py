"""Files of the hardware profile (docs/HARDWARE_PROFILE.md sections 3.1-3.6).

Reads, checks and writes ``state/hardware-profile.json``, its last-good copy and
``state/hardware-override.json``. A read never raises because of the content: a
bad file is reported as ``invalid``. Every write replaces the whole file.

Imported only by the hardware_* modules and the Control Center, never by a scan
stage, so nothing here is part of a stage-cache key. Standard library only.
A cached read hands every caller the same document: callers never mutate it.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

PROFILE_SCHEMA = "biliflow.hardware-profile"
PROFILE_VERSION = 1
READABLE_PROFILE_VERSIONS = frozenset({1})
OVERRIDE_SCHEMA = "biliflow.hardware-override"
OVERRIDE_VERSION = 1
READABLE_OVERRIDE_VERSIONS = frozenset({1})

PROFILE_FILE = "hardware-profile.json"
LAST_GOOD_FILE = "hardware-profile.last-good.json"
OVERRIDE_FILE = "hardware-override.json"
PROFILE_FILES = {"main": PROFILE_FILE, "last_good": LAST_GOOD_FILE}

MAX_FILE_BYTES = 256 * 1024
MAX_MEASUREMENTS = 32
MAX_MESSAGE = 300
REPLACE_ATTEMPTS = 3
REPLACE_PAUSE_SECONDS = 0.1  # times the attempt number

MODES = frozenset({"detect", "measure"})
LAUNCHERS = frozenset({"control-center", "script", "installer"})
RESULTS = frozenset({"ok", "failed"})
FAILURE_CODES = frozenset({
    "cuda_unavailable", "torch_probe_timeout", "torch_probe_crashed",
    "nvml_failed", "nvml_torch_mismatch", "adapters_unreadable",
})
PROBE_FAILURES = frozenset({"torch_probe_timeout", "torch_probe_crashed"})
NVML_STATUSES = frozenset({"ok", "no_library", "error"})
TRIALS = frozenset({"ok", "failed", "not_run"})
QUALITIES = frozenset({"clean", "contended", "battery"})
VENDORS = frozenset({"nvidia", "intel", "amd", "other"})
VENDOR_IDS = {"10de": "nvidia", "8086": "intel", "1002": "amd", "1022": "amd"}
NVIDIA_VENDOR_ID = "10de"
OVERRIDE_SOURCES = frozenset({"dashboard-pc"})
NVDEC_CODECS = ("h264", "hevc", "av1")

_HEX64 = re.compile(r"[0-9a-f]{64}")
_HEX4 = re.compile(r"[0-9a-f]{4}")
_CAPABILITY = re.compile(r"\d{1,2}\.\d{1,2}")
_DRIVER = re.compile(r"\d{1,4}\.\d{1,3}")
_FFMPEG_VERSION = re.compile(r"\d{1,3}(?:\.\d{1,4}){0,2}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")

# Vietnamese text for each recorded failure (docs/HARDWARE_PROFILE.md section 7.2).
FAILURE_MESSAGES = {
    "cuda_unavailable": ("Không dùng được CUDA. Khởi động lại hoặc cài lại driver NVIDIA rồi Đo lại; "
                         "hoặc bật Chỉ dùng CPU."),
    "torch_probe_timeout": ("Kiểm tra CUDA bằng torch quá giờ. Khởi động lại hoặc cài lại driver NVIDIA "
                            "rồi Đo lại; hoặc bật Chỉ dùng CPU."),
    "torch_probe_crashed": ("Kiểm tra CUDA bằng torch bị lỗi. Khởi động lại hoặc cài lại driver NVIDIA "
                            "rồi Đo lại; hoặc bật Chỉ dùng CPU."),
    "nvml_failed": "NVML lỗi; kiểm tra driver NVIDIA rồi Đo lại.",
    "nvml_torch_mismatch": "NVML và torch báo GPU khác nhau; kiểm tra driver NVIDIA rồi Đo lại.",
    "adapters_unreadable": ("Không xác định được máy có card NVIDIA hay không. "
                            "Bật Chỉ dùng CPU nếu máy không có card NVIDIA."),
}


class SchemaError(ValueError):
    """A document breaks the schema; ``where`` names the first bad field."""

    def __init__(self, where: str, problem: str):
        super().__init__(f"{where}: {problem}")
        self.where = where


@dataclass(frozen=True)
class ProfileRead:
    """One profile file: ``missing``, ``ok`` (``document`` set) or ``invalid`` (``error`` set)."""
    state: str
    document: dict[str, Any] | None = None
    error: str | None = None


@dataclass(frozen=True)
class UntestedFast:
    """The PC switch for the Turing fast families on an untested combination (section 5.6).

    It holds only for the card it was turned on for.
    """
    gpu_name: str
    compute_capability: str  # "M.m"
    enabled_at: str


@dataclass(frozen=True)
class OverrideRead:
    """The switch file: ``missing`` (both switches off), ``ok`` or ``invalid``."""
    state: str
    cpu_only: bool = False
    untested_fast: UntestedFast | None = None
    changed_at: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class AttemptOutcome:
    result: str
    failure_code: str | None
    had_cuda: bool


@dataclass(frozen=True)
class GpuInfo:
    name: str
    compute_capability: str  # "M.m"
    vram_mib: int


@dataclass(frozen=True)
class FileStat:
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class Observation:
    """The light observation (section 3.3), made by hardware_detect.observe() without a subprocess."""
    nvml: str  # "ok", "no_library" or "error"
    gpus: tuple[GpuInfo, ...]  # empty unless nvml == "ok"
    nvidia_driver: str | None
    nvidia_pci_seen: bool | None  # None: the registry could not be read
    torch_build: str | None
    ffmpeg: FileStat | None
    cpu_name: str | None


def gate_gpus(gate_key: Mapping[str, Any]) -> tuple[GpuInfo, ...]:
    return tuple(GpuInfo(gpu["name"], gpu["compute_capability"], gpu["vram_mib"])
                 for gpu in gate_key["nvidia_gpus"])


# --- JSON -------------------------------------------------------------------------------------

def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not allowed")


def loads_strict(data: bytes) -> Any:
    """JSON with a UTF-8 BOM allowed (PowerShell 5.1 writes one), no duplicate keys, no NaN."""
    text = data.decode("utf-8-sig")
    return json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constant)


def _read_limited(path: Path) -> tuple[str, Any, str | None]:
    """(state, value, error) for a JSON file of at most MAX_FILE_BYTES.

    ``state`` is ``ok``, ``missing``, ``unreadable`` (an I/O error, which says nothing about the
    content) or ``invalid`` (the content is too large or not strict JSON).
    """
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        return "missing", None, None
    except OSError as error:
        return "unreadable", None, f"không đọc được file ({type(error).__name__})"
    if len(data) > MAX_FILE_BYTES:
        return "invalid", None, "file lớn hơn 256 KiB"
    try:
        return "ok", loads_strict(data), None
    except (UnicodeDecodeError, ValueError) as error:
        return "invalid", None, f"JSON hỏng ({str(error)[:120]})"


def write_json_atomic(path: Path, document: Mapping[str, Any]) -> None:
    """Replace ``path`` whole: a random O_EXCL temp file in the same folder, fsync, os.replace."""
    data = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f"{path.name}.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    try:
        handle = os.open(temp, flags, 0o644)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(handle, view):]
            os.fsync(handle)
        finally:
            os.close(handle)
        for attempt in range(1, REPLACE_ATTEMPTS + 1):
            try:
                os.replace(temp, path)
                return
            except PermissionError:  # an antivirus or indexer may hold a fresh file for a moment
                if attempt == REPLACE_ATTEMPTS:
                    raise
                time.sleep(REPLACE_PAUSE_SECONDS * attempt)
    except BaseException:
        try:
            temp.unlink()
        except OSError:
            pass
        raise


# --- field checks -----------------------------------------------------------------------------

def _keys(value: Any, where: str, keys: Iterable[str]) -> dict[str, Any]:
    if type(value) is not dict:
        raise SchemaError(where, "phải là object")
    expected = set(keys)
    if set(value) != expected:
        extra, missing = sorted(set(value) - expected), sorted(expected - set(value))
        raise SchemaError(where, f"khóa lạ {extra}" if extra else f"thiếu khóa {missing}")
    return value


def _string(value: Any, where: str, limit: int, *, pattern: re.Pattern[str] | None = None,
            choices: frozenset[str] | None = None, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or not value or len(value) > limit:
        raise SchemaError(where, f"phải là chuỗi 1-{limit} ký tự")
    if pattern is not None and not pattern.fullmatch(value):
        raise SchemaError(where, "sai định dạng")
    if choices is not None and value not in choices:
        raise SchemaError(where, f"phải thuộc {sorted(choices)}")
    return value


def _integer(value: Any, where: str, *, low: int = 0, high: int = 2 ** 63 - 1,
             optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if type(value) is not int or not low <= value <= high:
        raise SchemaError(where, f"phải là số nguyên {low}..{high}")
    return value


def _number(value: Any, where: str, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if type(value) not in (int, float) or not math.isfinite(value):
        raise SchemaError(where, "phải là số hữu hạn")
    return float(value)


def _boolean(value: Any, where: str, *, optional: bool = False) -> bool | None:
    if value is None and optional:
        return None
    if type(value) is not bool:
        raise SchemaError(where, "phải là true hoặc false")
    return value


def _timestamp(value: Any, where: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    _string(value, where, 40)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise SchemaError(where, "không phải thời gian ISO-8601") from None
    if parsed.tzinfo is None:
        raise SchemaError(where, "thiếu múi giờ")
    return value


def _array(value: Any, where: str, limit: int) -> list[Any]:
    if type(value) is not list or len(value) > limit:
        raise SchemaError(where, f"phải là mảng tối đa {limit} phần tử")
    return value


def _message(value: Any, where: str) -> str | None:
    return _string(value, where, MAX_MESSAGE, optional=True)


# --- profile schema (section 3.2) -------------------------------------------------------------

def _check_gate_key(value: Any, where: str) -> None:
    gate = _keys(value, where, ("nvidia_gpus", "nvidia_driver", "torch_build", "ffmpeg"))
    for index, gpu in enumerate(_array(gate["nvidia_gpus"], f"{where}.nvidia_gpus", 8)):
        at = f"{where}.nvidia_gpus[{index}]"
        _keys(gpu, at, ("name", "compute_capability", "vram_mib"))
        _string(gpu["name"], f"{at}.name", 120)
        _string(gpu["compute_capability"], f"{at}.compute_capability", 5, pattern=_CAPABILITY)
        _integer(gpu["vram_mib"], f"{at}.vram_mib", high=10 ** 7)
    _string(gate["nvidia_driver"], f"{where}.nvidia_driver", 12, pattern=_DRIVER, optional=True)
    _string(gate["torch_build"], f"{where}.torch_build", 60, optional=True)
    if gate["ffmpeg"] is not None:
        ffmpeg = _keys(gate["ffmpeg"], f"{where}.ffmpeg", ("version", "sha256", "size", "mtime_ns"))
        _string(ffmpeg["version"], f"{where}.ffmpeg.version", 16, pattern=_FFMPEG_VERSION, optional=True)
        _string(ffmpeg["sha256"], f"{where}.ffmpeg.sha256", 64, pattern=_HEX64)
        _integer(ffmpeg["size"], f"{where}.ffmpeg.size")
        _integer(ffmpeg["mtime_ns"], f"{where}.ffmpeg.mtime_ns")


def _check_fingerprint(value: Any, where: str) -> None:
    fingerprint = _keys(value, where, ("sha256", "gate_key", "info"))
    _string(fingerprint["sha256"], f"{where}.sha256", 64, pattern=_HEX64)
    _check_gate_key(fingerprint["gate_key"], f"{where}.gate_key")
    info = _keys(fingerprint["info"], f"{where}.info", ("os", "python", "torch_cuda", "cudnn"))
    _string(info["os"], f"{where}.info.os", 120, optional=True)
    _string(info["python"], f"{where}.info.python", 40, optional=True)
    _string(info["torch_cuda"], f"{where}.info.torch_cuda", 20, optional=True)
    _string(info["cudnn"], f"{where}.info.cudnn", 20, optional=True)


def _check_torch(value: Any, where: str) -> None:
    torch = _keys(value, where, ("ok", "device_count", "device_name", "capability", "arch_list",
                                 "total_memory_bytes", "fp16_ok", "seconds", "error"))
    _boolean(torch["ok"], f"{where}.ok")
    _integer(torch["device_count"], f"{where}.device_count", high=64)
    _string(torch["device_name"], f"{where}.device_name", 120, optional=True)
    _string(torch["capability"], f"{where}.capability", 5, pattern=_CAPABILITY, optional=True)
    for index, arch in enumerate(_array(torch["arch_list"], f"{where}.arch_list", 32)):
        _string(arch, f"{where}.arch_list[{index}]", 16)
    _integer(torch["total_memory_bytes"], f"{where}.total_memory_bytes", optional=True)
    _boolean(torch["fp16_ok"], f"{where}.fp16_ok", optional=True)
    _number(torch["seconds"], f"{where}.seconds", optional=True)
    _message(torch["error"], f"{where}.error")


def _check_ffmpeg(value: Any, where: str) -> None:
    ffmpeg = _keys(value, where, ("ok", "error", "path", "version", "sha256", "size", "mtime_ns",
                                  "hwaccels_listed", "encoders", "nvdec", "nvdec_codecs"))
    _boolean(ffmpeg["ok"], f"{where}.ok")
    _message(ffmpeg["error"], f"{where}.error")
    _string(ffmpeg["path"], f"{where}.path", 1024, optional=True)
    _string(ffmpeg["version"], f"{where}.version", 16, pattern=_FFMPEG_VERSION, optional=True)
    _string(ffmpeg["sha256"], f"{where}.sha256", 64, pattern=_HEX64, optional=True)
    _integer(ffmpeg["size"], f"{where}.size", optional=True)
    _integer(ffmpeg["mtime_ns"], f"{where}.mtime_ns", optional=True)
    for index, name in enumerate(_array(ffmpeg["hwaccels_listed"], f"{where}.hwaccels_listed", 32)):
        _string(name, f"{where}.hwaccels_listed[{index}]", 40)
    encoders = ffmpeg["encoders"]
    if type(encoders) is not dict or len(encoders) > 32:
        raise SchemaError(f"{where}.encoders", "phải là object tối đa 32 encoder")
    for name, entry in encoders.items():
        at = f"{where}.encoders.{name}"
        _string(name, at, 40)
        _keys(entry, at, ("listed", "trial", "error"))
        _boolean(entry["listed"], f"{at}.listed")
        _string(entry["trial"], f"{at}.trial", 10, choices=TRIALS)
        _message(entry["error"], f"{at}.error")
    nvdec = _keys(ffmpeg["nvdec"], f"{where}.nvdec", ("listed", "trial", "framemd5_equal_cpu", "chain"))
    _boolean(nvdec["listed"], f"{where}.nvdec.listed")
    _string(nvdec["trial"], f"{where}.nvdec.trial", 10, choices=TRIALS)
    _boolean(nvdec["framemd5_equal_cpu"], f"{where}.nvdec.framemd5_equal_cpu", optional=True)
    _string(nvdec["chain"], f"{where}.nvdec.chain", 20, choices=frozenset({"logo_routing"}))
    codecs = _keys(ffmpeg["nvdec_codecs"], f"{where}.nvdec_codecs", NVDEC_CODECS)
    for codec in NVDEC_CODECS:
        at = f"{where}.nvdec_codecs.{codec}"
        _keys(codecs[codec], at, ("trial", "error"))
        _string(codecs[codec]["trial"], f"{at}.trial", 10, choices=TRIALS)
        _message(codecs[codec]["error"], f"{at}.error")


def _check_detection(value: Any, where: str) -> None:
    detection = _keys(value, where, ("nvml", "nvidia_smi", "torch_cuda", "ffmpeg", "cpu", "ram_bytes",
                                     "disk", "power", "adapters"))
    nvml = _keys(detection["nvml"], f"{where}.nvml", ("status", "error"))
    _string(nvml["status"], f"{where}.nvml.status", 12, choices=NVML_STATUSES)
    _message(nvml["error"], f"{where}.nvml.error")
    if detection["nvidia_smi"] is not None:
        smi = _keys(detection["nvidia_smi"], f"{where}.nvidia_smi",
                    ("ok", "driver_version", "gpus", "error"))
        _boolean(smi["ok"], f"{where}.nvidia_smi.ok")
        _string(smi["driver_version"], f"{where}.nvidia_smi.driver_version", 12, optional=True)
        for index, gpu in enumerate(_array(smi["gpus"], f"{where}.nvidia_smi.gpus", 8)):
            at = f"{where}.nvidia_smi.gpus[{index}]"
            _keys(gpu, at, ("name", "compute_capability", "vram_mib"))
            _string(gpu["name"], f"{at}.name", 120)
            _string(gpu["compute_capability"], f"{at}.compute_capability", 5, pattern=_CAPABILITY,
                    optional=True)
            _integer(gpu["vram_mib"], f"{at}.vram_mib", high=10 ** 7, optional=True)
        _message(smi["error"], f"{where}.nvidia_smi.error")
    _check_torch(detection["torch_cuda"], f"{where}.torch_cuda")
    _check_ffmpeg(detection["ffmpeg"], f"{where}.ffmpeg")
    cpu = _keys(detection["cpu"], f"{where}.cpu",
                ("model", "vendor", "physical_cores", "logical_cores", "features"))
    _string(cpu["model"], f"{where}.cpu.model", 120, optional=True)
    _string(cpu["vendor"], f"{where}.cpu.vendor", 40, optional=True)
    _integer(cpu["physical_cores"], f"{where}.cpu.physical_cores", high=4096, optional=True)
    _integer(cpu["logical_cores"], f"{where}.cpu.logical_cores", high=4096, optional=True)
    features = _keys(cpu["features"], f"{where}.cpu.features", ("avx2", "avx512f"))
    _boolean(features["avx2"], f"{where}.cpu.features.avx2", optional=True)
    _boolean(features["avx512f"], f"{where}.cpu.features.avx512f", optional=True)
    _integer(detection["ram_bytes"], f"{where}.ram_bytes", optional=True)
    disk = _keys(detection["disk"], f"{where}.disk", ("root_total_bytes", "root_free_bytes"))
    _integer(disk["root_total_bytes"], f"{where}.disk.root_total_bytes", optional=True)
    _integer(disk["root_free_bytes"], f"{where}.disk.root_free_bytes", optional=True)
    power = _keys(detection["power"], f"{where}.power", ("battery_present", "ac_online"))
    _boolean(power["battery_present"], f"{where}.power.battery_present", optional=True)
    _boolean(power["ac_online"], f"{where}.power.ac_online", optional=True)
    adapters = _keys(detection["adapters"], f"{where}.adapters", ("ok", "error", "items"))
    _boolean(adapters["ok"], f"{where}.adapters.ok")
    _message(adapters["error"], f"{where}.adapters.error")
    for index, item in enumerate(_array(adapters["items"], f"{where}.adapters.items", 16)):
        at = f"{where}.adapters.items[{index}]"
        _keys(item, at, ("vendor_id", "vendor", "device_id", "name", "driver_version"))
        _string(item["vendor_id"], f"{at}.vendor_id", 4, pattern=_HEX4)
        _string(item["vendor"], f"{at}.vendor", 10, choices=VENDORS)
        _string(item["device_id"], f"{at}.device_id", 4, pattern=_HEX4)
        _string(item["name"], f"{at}.name", 120, optional=True)
        _string(item["driver_version"], f"{at}.driver_version", 40, optional=True)


def _check_measurements(value: Any, where: str) -> None:
    for index, record in enumerate(_array(value, where, MAX_MEASUREMENTS)):
        at = f"{where}[{index}]"
        _keys(record, at, ("id", "kind", "subject", "at", "fingerprint_sha256", "clip", "wall_seconds",
                           "cpu_seconds", "value", "unit", "ok", "error", "quality"))
        _string(record["id"], f"{at}.id", 60)
        _string(record["kind"], f"{at}.kind", 40)
        _string(record["subject"], f"{at}.subject", 80)
        _timestamp(record["at"], f"{at}.at")
        _string(record["fingerprint_sha256"], f"{at}.fingerprint_sha256", 64, pattern=_HEX64)
        _string(record["clip"], f"{at}.clip", 80, optional=True)
        _number(record["wall_seconds"], f"{at}.wall_seconds", optional=True)
        _number(record["cpu_seconds"], f"{at}.cpu_seconds", optional=True)
        _number(record["value"], f"{at}.value", optional=True)
        _string(record["unit"], f"{at}.unit", 20, optional=True)
        _boolean(record["ok"], f"{at}.ok")
        _message(record["error"], f"{at}.error")
        _string(record["quality"], f"{at}.quality", 10, choices=QUALITIES)


def _check_cross(document: Mapping[str, Any]) -> None:
    attempt, history = document["attempt"], document["history"]
    torch = document["detection"]["torch_cuda"]
    nvml = document["detection"]["nvml"]
    gpus = document["fingerprint"]["gate_key"]["nvidia_gpus"]
    if (attempt["result"] == "ok") != (attempt["failure"] is None):
        raise SchemaError("attempt.failure", "chỉ bản thất bại mới có lỗi")
    if torch["ok"] and (torch["device_count"] < 1 or not history["had_cuda"]):
        raise SchemaError("detection.torch_cuda", "CUDA dùng được mà không có thiết bị hay lịch sử")
    if attempt["result"] == "ok" and torch["ok"]:
        if nvml["status"] != "ok" or torch["device_count"] != len(gpus):
            raise SchemaError("fingerprint.gate_key.nvidia_gpus", "NVML và torch không khớp")
    if attempt["result"] == "ok" and not torch["ok"] and history["had_cuda"]:
        raise SchemaError("history.had_cuda", "máy từng có CUDA mà lần đo không CUDA lại thành công")
    if not history["had_cuda"] and history["first_cuda_at"] is not None:
        raise SchemaError("history.first_cuda_at", "chưa từng có CUDA thì không có thời điểm")


def validate_profile(document: Any) -> None:
    """Raise SchemaError unless ``document`` is a valid profile (hashes included)."""
    doc = _keys(document, "profile", ("schema", "profile_version", "content_sha256", "written_at", "writer",
                                      "attempt", "fingerprint", "detection", "measurements", "history"))
    _string(doc["schema"], "schema", 40, choices=frozenset({PROFILE_SCHEMA}))
    if type(doc["profile_version"]) is not int or doc["profile_version"] not in READABLE_PROFILE_VERSIONS:
        raise SchemaError("profile_version", "phiên bản lạ")
    _string(doc["content_sha256"], "content_sha256", 64, pattern=_HEX64)
    _timestamp(doc["written_at"], "written_at")
    writer = _keys(doc["writer"], "writer", ("biliflow_version", "mode", "launched_by"))
    _string(writer["biliflow_version"], "writer.biliflow_version", 40)
    _string(writer["mode"], "writer.mode", 10, choices=MODES)
    _string(writer["launched_by"], "writer.launched_by", 20, choices=LAUNCHERS)
    attempt = _keys(doc["attempt"], "attempt", ("result", "failure", "started_at", "finished_at",
                                                "budget_seconds"))
    _string(attempt["result"], "attempt.result", 10, choices=RESULTS)
    if attempt["failure"] is not None:
        failure = _keys(attempt["failure"], "attempt.failure", ("code", "step", "message"))
        _string(failure["code"], "attempt.failure.code", 40, choices=FAILURE_CODES)
        _string(failure["step"], "attempt.failure.step", 40)
        _string(failure["message"], "attempt.failure.message", MAX_MESSAGE)
    _timestamp(attempt["started_at"], "attempt.started_at")
    _timestamp(attempt["finished_at"], "attempt.finished_at")
    _integer(attempt["budget_seconds"], "attempt.budget_seconds", low=1, high=3600)
    _check_fingerprint(doc["fingerprint"], "fingerprint")
    _check_detection(doc["detection"], "detection")
    _check_measurements(doc["measurements"], "measurements")
    history = _keys(doc["history"], "history", ("had_cuda", "first_cuda_at"))
    _boolean(history["had_cuda"], "history.had_cuda")
    _timestamp(history["first_cuda_at"], "history.first_cuda_at", optional=True)
    _check_cross(doc)
    if canonical_sha256(doc["fingerprint"]["gate_key"]) != doc["fingerprint"]["sha256"]:
        raise SchemaError("fingerprint.sha256", "không khớp gate_key")
    body = {key: value for key, value in doc.items() if key != "content_sha256"}
    if canonical_sha256(body) != doc["content_sha256"]:
        raise SchemaError("content_sha256", "không khớp nội dung")


def build_profile(*, written_at: str, writer: Mapping[str, Any], attempt: Mapping[str, Any],
                  gate_key: Mapping[str, Any], info: Mapping[str, Any], detection: Mapping[str, Any],
                  measurements: Sequence[Mapping[str, Any]], history: Mapping[str, Any]) -> dict[str, Any]:
    """A complete profile with both hashes; raises SchemaError when a part is malformed."""
    body: dict[str, Any] = {
        "schema": PROFILE_SCHEMA, "profile_version": PROFILE_VERSION, "written_at": written_at,
        "writer": dict(writer), "attempt": dict(attempt),
        "fingerprint": {"sha256": canonical_sha256(gate_key), "gate_key": dict(gate_key), "info": dict(info)},
        "detection": dict(detection), "measurements": [dict(record) for record in measurements],
        "history": dict(history),
    }
    document = {**body, "content_sha256": canonical_sha256(body)}
    validate_profile(document)
    return document


# --- reads, with a per-process cache keyed on (mtime_ns, size) --------------------------------

_READ_CACHE: dict[str, tuple[int, int, Any]] = {}
_READ_CACHE_LOCK = threading.Lock()
_READ_CACHE_LIMIT = 64
_UNCACHED_READS = frozenset({"missing", "unreadable"})  # the content itself was not read


def _cached(path: Path, parse: Callable[[str, Any, str | None], Any]) -> Any:
    """``parse`` the _read_limited result of ``path``; only a result about the content is cached.

    An I/O error (an antivirus or indexer holding the file for a moment) is reported, never kept:
    the next call reads the file again.
    """
    try:
        stat = path.stat()
    except OSError:
        return parse(*_read_limited(path))
    key = os.path.normcase(str(path))
    with _READ_CACHE_LOCK:
        hit = _READ_CACHE.get(key)
    if hit is not None and hit[0] == stat.st_mtime_ns and hit[1] == stat.st_size:
        return hit[2]
    state, value, error = _read_limited(path)
    result = parse(state, value, error)
    if state in _UNCACHED_READS:
        return result
    with _READ_CACHE_LOCK:
        if len(_READ_CACHE) >= _READ_CACHE_LIMIT:
            _READ_CACHE.clear()
        _READ_CACHE[key] = (stat.st_mtime_ns, stat.st_size, result)
    return result


def _parse_profile(state: str, value: Any, error: str | None) -> ProfileRead:
    if state == "missing":
        return ProfileRead("missing")
    if state != "ok":
        return ProfileRead("invalid", error=error)
    try:
        validate_profile(value)
    except SchemaError as problem:
        return ProfileRead("invalid", error=str(problem)[:MAX_MESSAGE])
    return ProfileRead("ok", document=value)


def profile_path(root: Path, which: str = "main") -> Path:
    return Path(root) / "state" / PROFILE_FILES[which]


def read_profile(root: Path, which: str = "main") -> ProfileRead:
    """``which`` is ``main`` or ``last_good``. Never raises because of the file's content."""
    return _cached(profile_path(root, which), _parse_profile)


def write_profile(root: Path, document: Mapping[str, Any], *, last_good: bool = False) -> None:
    """Write the main profile and, for a successful attempt, the last-good copy after it."""
    validate_profile(document)
    write_json_atomic(profile_path(root, "main"), document)
    if last_good:
        if document["attempt"]["result"] != "ok":
            raise SchemaError("attempt.result", "chỉ bản thành công mới là last-good")
        write_json_atomic(profile_path(root, "last_good"), document)


# --- the CPU-only switch (section 3.6) --------------------------------------------------------

def override_path(root: Path) -> Path:
    return Path(root) / "state" / OVERRIDE_FILE


def _parse_override(state: str, value: Any, error: str | None) -> OverrideRead:
    if state == "missing":
        return OverrideRead("missing")
    if state != "ok":
        return OverrideRead("invalid", error=error)
    try:
        doc = _keys(value, "override", ("schema", "version", "cpu_only", "untested_fast", "changed_at",
                                        "source"))
        _string(doc["schema"], "schema", 40, choices=frozenset({OVERRIDE_SCHEMA}))
        if type(doc["version"]) is not int or doc["version"] not in READABLE_OVERRIDE_VERSIONS:
            raise SchemaError("version", "phiên bản lạ")
        _boolean(doc["cpu_only"], "cpu_only")
        untested_fast = _check_untested_fast(doc["untested_fast"])
        _timestamp(doc["changed_at"], "changed_at")
        _string(doc["source"], "source", 20, choices=OVERRIDE_SOURCES)
    except SchemaError as problem:
        return OverrideRead("invalid", error=str(problem)[:MAX_MESSAGE])
    return OverrideRead("ok", cpu_only=doc["cpu_only"], untested_fast=untested_fast,
                        changed_at=doc["changed_at"])


def _check_untested_fast(value: Any) -> UntestedFast | None:
    if value is None:
        return None
    switch = _keys(value, "untested_fast", ("gpu_name", "compute_capability", "enabled_at"))
    _string(switch["gpu_name"], "untested_fast.gpu_name", 120)
    _string(switch["compute_capability"], "untested_fast.compute_capability", 5, pattern=_CAPABILITY)
    _timestamp(switch["enabled_at"], "untested_fast.enabled_at")
    return UntestedFast(switch["gpu_name"], switch["compute_capability"], switch["enabled_at"])


def read_override(root: Path) -> OverrideRead:
    return _cached(override_path(root), _parse_override)


def write_override(root: Path, *, cpu_only: bool, untested_fast: UntestedFast | None, now: datetime,
                   source: str = "dashboard-pc") -> None:
    """Write both switches; only the Control Center's hardware manager calls this, under its lock."""
    if type(cpu_only) is not bool:
        raise ValueError("cpu_only phải là true hoặc false")
    if untested_fast is not None and type(untested_fast) is not UntestedFast:
        raise ValueError("untested_fast phải là UntestedFast hoặc None")
    if source not in OVERRIDE_SOURCES or now.tzinfo is None:
        raise ValueError("nguồn hoặc thời gian không hợp lệ")
    switch = None if untested_fast is None else {
        "gpu_name": untested_fast.gpu_name, "compute_capability": untested_fast.compute_capability,
        "enabled_at": untested_fast.enabled_at,
    }
    try:
        _check_untested_fast(switch)
    except SchemaError as problem:
        raise ValueError(str(problem)) from None
    write_json_atomic(override_path(root), {
        "schema": OVERRIDE_SCHEMA, "version": OVERRIDE_VERSION, "cpu_only": cpu_only,
        "untested_fast": switch, "changed_at": now.isoformat(timespec="seconds"), "source": source,
    })


# --- "had CUDA" and the outcome of a measurement (section 3.5) --------------------------------

def previous_had_cuda(*reads: ProfileRead) -> bool:
    """True when any earlier file shows CUDA, and also when a file exists but cannot be read."""
    for read in reads:
        if read.state == "invalid":
            return True
        if read.state == "ok" and read.document is not None:
            if read.document["history"]["had_cuda"] or read.document["detection"]["torch_cuda"]["ok"]:
                return True
    return False


def classify_attempt(detection: Mapping[str, Any], gate_key: Mapping[str, Any], *,
                     previous: bool) -> AttemptOutcome:
    """The table of section 3.5: ok, or which failure, and the new monotonic ``had_cuda``."""
    torch, nvml = detection["torch_cuda"], detection["nvml"]
    gpus = gate_key["nvidia_gpus"]
    error = torch["error"] or ""
    probe_failure = next((code for code in sorted(PROBE_FAILURES) if error.startswith(code)), None)
    if torch["ok"]:
        if nvml["status"] != "ok":
            return AttemptOutcome("failed", "nvml_failed", True)
        if torch["device_count"] != len(gpus) or (gpus and gpus[0]["compute_capability"] != torch["capability"]):
            return AttemptOutcome("failed", "nvml_torch_mismatch", True)
        return AttemptOutcome("ok", None, True)
    if probe_failure is not None:
        return AttemptOutcome("failed", probe_failure, previous)
    if previous:
        return AttemptOutcome("failed", "cuda_unavailable", True)
    adapters = detection["adapters"]
    if not adapters["ok"]:
        return AttemptOutcome("failed", "adapters_unreadable", False)
    if any(item["vendor_id"] == NVIDIA_VENDOR_ID for item in adapters["items"]) or gpus:
        return AttemptOutcome("failed", "cuda_unavailable", False)
    if nvml["status"] == "error":
        return AttemptOutcome("failed", "nvml_failed", False)
    return AttemptOutcome("ok", None, False)


# --- error text that may leave the process ----------------------------------------------------

def sanitize_message(text: object, replacements: Sequence[tuple[str, str]] = (),
                     limit: int = MAX_MESSAGE) -> str:
    """Replace local path prefixes with placeholders, drop control characters, cut to ``limit``."""
    value = str(text)
    for prefix, label in sorted(replacements, key=lambda pair: len(pair[0]), reverse=True):
        if not prefix:
            continue
        # str(OSError) shows its filename through repr(), which doubles every backslash.
        for variant in {prefix, prefix.replace("\\", "/"), prefix.replace("\\", "\\\\")}:
            value = re.sub(re.escape(variant), lambda _match, label=label: label, value, flags=re.IGNORECASE)
    value = _CONTROL.sub(" ", value).strip()
    return value[:limit]


def path_replacements(root: Path, *extra: tuple[Path | str, str]) -> tuple[tuple[str, str], ...]:
    """<root>, <code> (this checkout) and <user> (the profile folder), plus ``extra`` pairs."""
    pairs: list[tuple[str, str]] = [(str(Path(root).resolve()), "<root>"),
                                    (str(Path(__file__).resolve().parents[2]), "<code>")]
    home = os.environ.get("USERPROFILE") or str(Path.home())
    if home:
        pairs.append((home, "<user>"))
    pairs.extend((str(path), label) for path, label in extra)
    return tuple(pairs)
