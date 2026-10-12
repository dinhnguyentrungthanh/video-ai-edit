"""The machine behind hardware detection (docs/HARDWARE_PROFILE.md sections 3.2, 3.3, 6.3 step 6).

``HardwareSystem`` is hardware_detect's only door to the machine: commands, NVML, the display
adapters of the registry, CPU, RAM, disk, power, file facts and the installed torch build.
``WindowsSystem`` is the real one; tests pass a fake. Every library (pynvml, psutil, winreg,
ctypes) is imported inside the method that uses it, so importing this module loads none of them
and starts no thread. Imported only by hardware_detect, never by a scan stage. Standard library
and hardware_schema only.
"""
from __future__ import annotations

import contextlib
import hashlib
import math
import os
import platform
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISREG
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence

from biliflow.hardware_schema import FileStat, GpuInfo

NOT_FOUND = "not_found"  # CommandResult.error when the program does not exist
ADAPTERS_TIMEOUT = "timeout"  # AdapterRead.error when the registry walk passed its deadline

PCI_ENUM_KEY = r"SYSTEM\CurrentControlSet\Enum\PCI"
CLASS_KEY = r"SYSTEM\CurrentControlSet\Control\Class"
CPU_KEY = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
DISPLAY_CLASS_GUID = "{4d36e968-e325-11ce-bfc1-08002be10318}"
PF_AVX2_INSTRUCTIONS_AVAILABLE = 40
PF_AVX512F_INSTRUCTIONS_AVAILABLE = 41
ERROR_NO_MORE_ITEMS = 259
MAX_PATH_CHARS = 260
MIB = 1024 * 1024
SYSTEM_POWER_STATUS_BYTES = 12  # BYTE ACLineStatus, BYTE BatteryFlag, two more BYTEs, two DWORDs
BATTERY_FLAG_NO_BATTERY = 128
BATTERY_FLAG_UNKNOWN = 255  # psutil reads it as "no battery" too (255 & 128)
AC_LINE_STATES = {0: False, 1: True}  # ACLineStatus; 255 (unknown) and anything else is null

NVML_NO_LIBRARY = "Không có nvml.dll (chưa cài driver NVIDIA)."

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_PCI_IDS = re.compile(r"VEN_([0-9A-Fa-f]{4})&DEV_([0-9A-Fa-f]{4})")
_DISPLAY_CLASS_CODE = re.compile(r"PCI\\CC_03", re.IGNORECASE)  # display or 3D controller, even without a driver
_DRIVER_KEY = re.compile(r"\{[0-9A-Fa-f-]{36}\}\\\d{4}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


# --- what the machine answers -------------------------------------------------------------------

@dataclass(frozen=True)
class CommandResult:
    """``returncode`` is None when the command timed out or could not start (``error``)."""
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str | None = None  # NOT_FOUND, or the OSError that kept the program from starting


@dataclass(frozen=True)
class NvmlRead:
    status: str  # "ok", "no_library" (unknown, not "no GPU") or "error"
    driver: str | None = None
    gpus: tuple[GpuInfo, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class AdapterInfo:
    vendor_id: str  # 4 lowercase hex digits from VEN_xxxx of the PCI id, never from the name
    device_id: str
    name: str | None
    driver_version: str | None


@dataclass(frozen=True)
class AdapterRead:
    ok: bool
    error: str | None = None
    items: tuple[AdapterInfo, ...] = ()


@dataclass(frozen=True)
class CpuInfo:
    model: str | None
    vendor: str | None
    avx2: bool | None
    avx512f: bool | None


@dataclass(frozen=True)
class PowerInfo:
    battery_present: bool | None
    ac_online: bool | None


@dataclass(frozen=True)
class PlatformInfo:
    os: str | None
    python: str | None


class HardwareSystem(Protocol):
    """The only door to the machine. WindowsSystem is the real one; tests pass a fake."""

    def run(self, argv: Sequence[str], *, timeout: float,
            env: Mapping[str, str] | None = None) -> CommandResult: ...

    def nvml(self) -> NvmlRead: ...

    def adapters(self, *, timeout: float) -> AdapterRead: ...

    def cpu(self) -> CpuInfo: ...

    def cpu_counts(self) -> tuple[int | None, int | None]: ...  # (physical, logical)

    def ram_bytes(self) -> int | None: ...

    def disk_usage(self, path: Path) -> tuple[int, int] | None: ...  # (total, free)

    def power(self) -> PowerInfo: ...

    def file_stat(self, path: Path) -> FileStat | None: ...  # None: missing or not a regular file

    def file_sha256(self, path: Path) -> str: ...  # raises OSError

    def torch_build(self) -> str | None: ...

    def platform_info(self) -> PlatformInfo: ...

    def system_directory(self) -> Path | None: ...


class RegistryReader(Protocol):
    """Read-only HKEY_LOCAL_MACHINE access, so the adapter walk can be tested with a fake."""

    def subkeys(self, path: str) -> tuple[str, ...]: ...  # FileNotFoundError when the key is missing

    def values(self, path: str, names: Sequence[str]) -> dict[str, object]: ...  # present values only


# --- the real machine ---------------------------------------------------------------------------

class WindowsSystem:
    """subprocess, pynvml, winreg, kernel32 and psutil; each library is imported when it is used."""

    def run(self, argv: Sequence[str], *, timeout: float,
            env: Mapping[str, str] | None = None) -> CommandResult:
        command = checked_argv(argv, timeout)
        try:
            done = subprocess.run(command, shell=False, stdin=subprocess.DEVNULL, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=timeout,
                                  creationflags=_CREATE_NO_WINDOW, env=None if env is None else dict(env),
                                  check=False)
        except subprocess.TimeoutExpired:
            return CommandResult(None, timed_out=True)
        except FileNotFoundError:
            return CommandResult(None, error=NOT_FOUND)
        except OSError as error:
            return CommandResult(None, error=describe(error))
        return CommandResult(done.returncode, done.stdout or "", done.stderr or "")

    def nvml(self) -> NvmlRead:
        try:
            import pynvml
        except ImportError as error:  # the package is a dependency: its absence is an error, not "no GPU"
            return NvmlRead("error", error=f"pynvml: {describe(error)}")
        try:
            pynvml.nvmlInit()
        except pynvml.NVMLError_LibraryNotFound:
            return NvmlRead("no_library", error=NVML_NO_LIBRARY)
        except Exception as error:  # NVMLError, a binding mismatch or a loader error: reported, never "no GPU"
            return NvmlRead("error", error=describe(error))
        try:
            return _read_nvml(pynvml)
        except Exception as error:
            return NvmlRead("error", error=describe(error))
        finally:
            with contextlib.suppress(pynvml.NVMLError):  # the read is complete either way
                pynvml.nvmlShutdown()

    def adapters(self, *, timeout: float) -> AdapterRead:
        return list_display_adapters(_WinRegistry(), deadline=time.monotonic() + timeout)

    def cpu(self) -> CpuInfo:
        try:
            values = _WinRegistry().values(CPU_KEY, ("ProcessorNameString", "VendorIdentifier"))
        except (ImportError, OSError):  # recorded as unknown (null), never guessed
            values = {}
        avx2, avx512f = _processor_features()
        return CpuInfo(short_text(values.get("ProcessorNameString"), 120),
                       short_text(values.get("VendorIdentifier"), 40), avx2, avx512f)

    def cpu_counts(self) -> tuple[int | None, int | None]:
        psutil = _psutil()
        if psutil is None:
            return None, None
        try:
            return psutil.cpu_count(logical=False), psutil.cpu_count(logical=True)
        except (psutil.Error, OSError):
            return None, None

    def ram_bytes(self) -> int | None:
        psutil = _psutil()
        if psutil is None:
            return None
        try:
            return int(psutil.virtual_memory().total)
        except (psutil.Error, OSError):
            return None

    def disk_usage(self, path: Path) -> tuple[int, int] | None:
        psutil = _psutil()
        if psutil is None:
            return None
        try:
            usage = psutil.disk_usage(str(path))
        except (psutil.Error, OSError):
            return None
        return int(usage.total), int(usage.free)

    def power(self) -> PowerInfo:
        """psutil's battery. Its None stands both for "no system battery" and for "status unknown",
        so Windows' own SYSTEM_POWER_STATUS tells the two apart (_power_without_battery)."""
        psutil = _psutil()
        if psutil is None:
            return PowerInfo(None, None)
        try:
            battery = psutil.sensors_battery()
        except (psutil.Error, OSError, RuntimeError, AttributeError, NotImplementedError):
            return PowerInfo(None, None)
        if battery is None:
            return _power_without_battery(_system_power_status())
        plugged = battery.power_plugged
        return PowerInfo(True, plugged if type(plugged) is bool else None)

    def file_stat(self, path: Path) -> FileStat | None:
        try:
            result = os.stat(path)
        except OSError:
            return None
        return FileStat(result.st_size, result.st_mtime_ns) if S_ISREG(result.st_mode) else None

    def file_sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(MIB), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def torch_build(self) -> str | None:
        from importlib import metadata  # the installed distribution's version; never import torch here
        try:
            return metadata.version("torch")
        except (metadata.PackageNotFoundError, OSError):
            return None

    def platform_info(self) -> PlatformInfo:
        """The OS from sys.getwindowsversion(); never platform.system() or platform.version().

        On Python 3.11 those run ``ver`` through a shell, without a timeout or CREATE_NO_WINDOW.
        """
        windows = getattr(sys, "getwindowsversion", None)
        version = windows() if windows is not None else None
        name = None if version is None else f"Windows {version.major}.{version.minor}.{version.build}"
        return PlatformInfo(short_text(name, 120), short_text(platform.python_version(), 40))

    def system_directory(self) -> Path | None:
        """System32 from Windows (GetSystemDirectoryW); %SystemRoot%\\System32 only when that fails."""
        try:
            import ctypes
            buffer = ctypes.create_unicode_buffer(MAX_PATH_CHARS)
            length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
        except (ImportError, AttributeError, OSError):
            length = 0
        if 0 < length < MAX_PATH_CHARS:
            return Path(buffer.value)
        system_root = os.environ.get("SystemRoot")
        return Path(system_root) / "System32" if system_root else None


class _WinRegistry:
    def subkeys(self, path: str) -> tuple[str, ...]:
        import winreg
        names: list[str] = []
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ) as key:
            while True:
                try:
                    names.append(winreg.EnumKey(key, len(names)))
                except OSError as error:
                    if getattr(error, "winerror", None) == ERROR_NO_MORE_ITEMS:
                        return tuple(names)
                    raise

    def values(self, path: str, names: Sequence[str]) -> dict[str, object]:
        import winreg
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ)
        except FileNotFoundError:
            return {}
        found: dict[str, object] = {}
        with key:
            for name in names:
                try:
                    found[name] = winreg.QueryValueEx(key, name)[0]
                except FileNotFoundError:
                    continue
        return found


def checked_argv(argv: Sequence[str], timeout: float) -> list[str]:
    """A list of arguments (never a shell string) and a finite, positive timeout."""
    if isinstance(argv, (str, bytes)):
        raise TypeError("lệnh phải là danh sách tham số, không phải chuỗi shell")
    if type(timeout) not in (int, float) or not 0 < timeout < math.inf:
        raise ValueError("mỗi lệnh phải có thời hạn")
    return [str(part) for part in argv]


def _psutil() -> Any | None:
    try:
        import psutil
    except ImportError:
        return None
    return psutil


def _processor_features() -> tuple[bool | None, bool | None]:
    try:
        import ctypes
        present = ctypes.windll.kernel32.IsProcessorFeaturePresent
        return (bool(present(PF_AVX2_INSTRUCTIONS_AVAILABLE)),
                bool(present(PF_AVX512F_INSTRUCTIONS_AVAILABLE)))
    except (ImportError, AttributeError, OSError):
        return None, None


def _system_power_status() -> tuple[int, int] | None:
    """(ACLineStatus, BatteryFlag) from kernel32.GetSystemPowerStatus; None when it cannot be read."""
    try:
        import ctypes
        status = (ctypes.c_ubyte * SYSTEM_POWER_STATUS_BYTES)()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return None
    except (ImportError, AttributeError, OSError):
        return None
    return int(status[0]), int(status[1])


def _power_without_battery(status: tuple[int, int] | None) -> PowerInfo:
    """psutil saw no battery: only BatteryFlag 128 (and not 255, unknown) means the machine has none.

    The mains state is ACLineStatus as Windows gives it; an unknown state stays null, never guessed.
    """
    if status is None:
        return PowerInfo(None, None)
    ac_line, battery_flag = status
    no_battery = battery_flag != BATTERY_FLAG_UNKNOWN and bool(battery_flag & BATTERY_FLAG_NO_BATTERY)
    return PowerInfo(False if no_battery else None, AC_LINE_STATES.get(ac_line))


def _read_nvml(pynvml: Any) -> NvmlRead:
    driver = _text(pynvml.nvmlSystemGetDriverVersion())
    count = int(pynvml.nvmlDeviceGetCount())
    return NvmlRead("ok", driver=driver, gpus=tuple(_nvml_gpu(pynvml, index) for index in range(count)))


def _nvml_gpu(pynvml: Any, index: int) -> GpuInfo:
    handle = pynvml.nvmlDeviceGetHandleByIndex(index)
    major, minor = pynvml.nvmlDeviceGetCudaComputeCapability(handle)
    total = pynvml.nvmlDeviceGetMemoryInfo(handle).total
    return GpuInfo(_text(pynvml.nvmlDeviceGetName(handle)), f"{int(major)}.{int(minor)}", int(total) // MIB)


# --- display adapters (section 3.2, detection.adapters) ----------------------------------------

class _DeadlineExceeded(Exception):
    pass


def list_display_adapters(registry: RegistryReader, *, deadline: float,
                          clock: Callable[[], float] = time.monotonic) -> AdapterRead:
    """Display adapters of Enum\\PCI, present or not; a read error or the deadline gives ok False.

    The deadline is checked between registry keys, so a walk over many devices stops in time.
    """
    try:
        items = tuple(_walk_display_adapters(registry, deadline, clock))
    except _DeadlineExceeded:
        return AdapterRead(False, ADAPTERS_TIMEOUT)
    except (ImportError, OSError) as error:  # ImportError: no winreg off Windows
        return AdapterRead(False, describe(error))
    return AdapterRead(True, None, items)


def _walk_display_adapters(registry: RegistryReader, deadline: float,
                           clock: Callable[[], float]) -> Iterator[AdapterInfo]:
    for device in registry.subkeys(PCI_ENUM_KEY):
        _check_deadline(deadline, clock)
        ids = _PCI_IDS.search(device)
        if ids is None:
            continue
        device_key = f"{PCI_ENUM_KEY}\\{device}"
        for instance in registry.subkeys(device_key):
            _check_deadline(deadline, clock)
            values = registry.values(f"{device_key}\\{instance}",
                                     ("ClassGUID", "CompatibleIDs", "DeviceDesc", "Driver"))
            if _is_display(values):
                yield AdapterInfo(ids.group(1).lower(), ids.group(2).lower(),
                                  _device_name(values.get("DeviceDesc")),
                                  _driver_version(registry, values.get("Driver")))


def _check_deadline(deadline: float, clock: Callable[[], float]) -> None:
    if clock() >= deadline:
        raise _DeadlineExceeded


def _is_display(values: Mapping[str, object]) -> bool:
    guid = values.get("ClassGUID")
    if isinstance(guid, str) and guid.lower() == DISPLAY_CLASS_GUID:
        return True
    compatible = values.get("CompatibleIDs")  # a card without any driver has no ClassGUID yet
    return isinstance(compatible, (list, tuple)) and any(
        isinstance(entry, str) and _DISPLAY_CLASS_CODE.match(entry) for entry in compatible)


def _device_name(description: object) -> str | None:
    if not isinstance(description, str):
        return None
    if description.startswith("@") and ";" in description:  # "@oemNN.inf,%token%;Name"
        description = description.split(";", 1)[1]
    return short_text(description, 120)


def _driver_version(registry: RegistryReader, driver: object) -> str | None:
    """DriverVersion of the Control\\Class entry the instance names; a detail, never a decision.

    An unreadable entry gives None: the vendor id, which is what decides, was already read.
    """
    if not isinstance(driver, str) or not _DRIVER_KEY.fullmatch(driver):
        return None
    try:
        found = registry.values(f"{CLASS_KEY}\\{driver}", ("DriverVersion",))
    except OSError:
        return None
    return short_text(found.get("DriverVersion"), 40)


# --- small helpers ------------------------------------------------------------------------------

def describe(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def short_text(value: object, limit: int) -> str | None:
    """A one-line string cut to ``limit`` characters; anything else (or blank) is None."""
    if not isinstance(value, str):
        return None
    text = _CONTROL.sub(" ", value).strip()[:limit].strip()
    return text or None


def _text(value: object) -> str:
    return (value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)).strip()
