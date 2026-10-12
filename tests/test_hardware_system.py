"""E1 machine access: WindowsSystem, NVML through a fake pynvml, the registry walk (HARDWARE_PROFILE.md section 9)."""
from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
import types
import unittest
from importlib import metadata
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from biliflow import hardware_system as hs
from biliflow.hardware_detect import checked_nvml
from biliflow.hardware_schema import NVML_STATUSES, FileStat, GpuInfo
from biliflow.hardware_system import (
    CLASS_KEY, DISPLAY_CLASS_GUID, NOT_FOUND, PCI_ENUM_KEY, AdapterInfo, CommandResult, HardwareSystem, NvmlRead,
    PowerInfo, WindowsSystem, list_display_adapters,
)

ROOT = Path(__file__).resolve().parents[1]
TEMP = ROOT / "temp"
HEX4 = re.compile(r"[0-9a-f]{4}")
NVIDIA_DEVICE = "VEN_10DE&DEV_1F15&SUBSYS_13E21043&REV_A1"
INTEL_DEVICE = "VEN_8086&DEV_9BC4&SUBSYS_13E21043&REV_05"
USB_DEVICE = "VEN_8086&DEV_06ED&SUBSYS_13E21043&REV_00"
USB_CLASS_GUID = "{36fc9e60-c465-11cf-8056-444553540000}"


def completed(argv, code=0, out="", err=""):
    return subprocess.CompletedProcess(argv, code, stdout=out, stderr=err)


class ProtocolTests(unittest.TestCase):
    def test_windows_system_implements_every_protocol_method(self):
        names = {name for name in vars(HardwareSystem) if not name.startswith("_")}
        self.assertTrue({"run", "nvml", "adapters", "cpu", "power", "file_stat", "torch_build"} <= names)
        for name in sorted(names):
            with self.subTest(name):
                self.assertTrue(callable(getattr(WindowsSystem, name, None)))


class RunTests(unittest.TestCase):
    def test_run_passes_no_window_no_shell_no_stdin_and_the_timeout(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return completed(command, 0, "out", "err")

        with patch("biliflow.hardware_system.subprocess.run", side_effect=fake_run):
            result = WindowsSystem().run([Path("C:/x/tool.exe"), "-v"], timeout=7.5)
        self.assertEqual(result, CommandResult(0, "out", "err"))
        command, kwargs = calls[0]
        self.assertEqual(command, [str(Path("C:/x/tool.exe")), "-v"])
        self.assertIs(kwargs["shell"], False)
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["timeout"], 7.5)
        self.assertEqual((kwargs["capture_output"], kwargs["text"], kwargs["errors"]), (True, True, "replace"))
        self.assertIs(kwargs["check"], False)
        self.assertIsNone(kwargs["env"])
        if os.name == "nt":
            self.assertTrue(kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)

    def test_run_copies_the_environment_it_is_given(self):
        seen = {}
        environment = {"PYTHONPATH": "src"}
        with patch("biliflow.hardware_system.subprocess.run",
                   side_effect=lambda command, **kwargs: seen.update(kwargs) or completed(command)):
            WindowsSystem().run(["tool.exe"], timeout=1, env=environment)
        self.assertEqual(seen["env"], environment)
        self.assertIsNot(seen["env"], environment)

    def test_run_reports_timeouts_and_missing_programs_without_raising(self):
        cases = {
            "timeout": (subprocess.TimeoutExpired(["tool.exe"], 1), CommandResult(None, timed_out=True)),
            "missing": (FileNotFoundError(2, "not found"), CommandResult(None, error=NOT_FOUND)),
            "denied": (PermissionError(13, "denied"), CommandResult(None, error="PermissionError: [Errno 13] denied")),
        }
        for label, (error, expected) in cases.items():
            with self.subTest(label), patch("biliflow.hardware_system.subprocess.run", side_effect=error):
                self.assertEqual(WindowsSystem().run(["tool.exe"], timeout=1), expected)

    def test_run_refuses_a_shell_string_or_a_missing_timeout(self):
        with patch("biliflow.hardware_system.subprocess.run") as run:
            with self.assertRaises(TypeError):
                WindowsSystem().run("tool.exe -v", timeout=1)
            for timeout in (0, -1, None, float("inf"), float("nan"), True):
                with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                    WindowsSystem().run(["tool.exe"], timeout=timeout)
        run.assert_not_called()


def fake_pynvml(*, init_error: str | None = None, query_error: bool = False,
                gpus=(("NVIDIA GeForce RTX 2060", (7, 5), 6442450944),)) -> types.ModuleType:
    module = types.ModuleType("pynvml")

    class NVMLError(Exception):
        pass

    module.NVMLError = NVMLError
    module.NVMLError_LibraryNotFound = type("NVMLError_LibraryNotFound", (NVMLError,), {})
    module.NVMLError_DriverNotLoaded = type("NVMLError_DriverNotLoaded", (NVMLError,), {})
    module.calls = []

    def init():
        module.calls.append("init")
        if init_error is not None:
            raise getattr(module, init_error)("NVML init failed")

    def count():
        if query_error:
            raise NVMLError("Unknown Error")
        return len(gpus)

    module.nvmlInit = init
    module.nvmlShutdown = lambda: module.calls.append("shutdown")
    module.nvmlSystemGetDriverVersion = lambda: "617.42"
    module.nvmlDeviceGetCount = count
    module.nvmlDeviceGetHandleByIndex = lambda index: index
    module.nvmlDeviceGetName = lambda handle: gpus[handle][0]
    module.nvmlDeviceGetCudaComputeCapability = lambda handle: gpus[handle][1]
    module.nvmlDeviceGetMemoryInfo = lambda handle: SimpleNamespace(total=gpus[handle][2])
    return module


class NvmlTests(unittest.TestCase):
    def read(self, module):
        with patch.dict(sys.modules, {"pynvml": module}):
            return WindowsSystem().nvml()

    def test_reads_driver_and_gpus_then_shuts_down(self):
        module = fake_pynvml(gpus=(("NVIDIA GeForce RTX 2060", (7, 5), 6442450944),
                                   (b"NVIDIA GeForce RTX 3060", (8, 6), 12884901888)))
        read = self.read(module)
        self.assertEqual(read, NvmlRead("ok", "617.42", (GpuInfo("NVIDIA GeForce RTX 2060", "7.5", 6144),
                                                         GpuInfo("NVIDIA GeForce RTX 3060", "8.6", 12288))))
        self.assertEqual(module.calls, ["init", "shutdown"])

    def test_missing_library_is_no_library_never_zero_gpus(self):
        module = fake_pynvml(init_error="NVMLError_LibraryNotFound")
        read = self.read(module)
        self.assertEqual(read.status, "no_library")
        self.assertEqual(module.calls, ["init"])

    def test_other_failures_are_errors(self):
        cases = {"driver not loaded": fake_pynvml(init_error="NVMLError_DriverNotLoaded"),
                 "query": fake_pynvml(query_error=True), "package missing": None}
        for label, module in cases.items():
            with self.subTest(label):
                read = self.read(module)
                self.assertEqual((read.status, read.gpus, read.driver), ("error", (), None))
                self.assertTrue(read.error)
        self.assertEqual(cases["query"].calls, ["init", "shutdown"])


class FakeRegistry:
    """``tree`` maps a key path to (subkeys, values); paths in ``denied`` raise PermissionError."""

    def __init__(self, tree, denied=()):
        self.tree, self.denied = tree, set(denied)

    def _node(self, path):
        if path in self.denied:
            raise PermissionError(13, "Access is denied")
        if path not in self.tree:
            raise FileNotFoundError(2, "missing key")
        return self.tree[path]

    def subkeys(self, path):
        return tuple(self._node(path)[0])

    def values(self, path, names):
        try:
            values = self._node(path)[1]
        except FileNotFoundError:
            return {}
        return {name: values[name] for name in names if name in values}


def machine_registry(**extra_instances):
    """An RTX 2060 and an Intel iGPU with drivers, a USB controller, and ``extra_instances`` devices."""
    devices = {
        NVIDIA_DEVICE: {"ClassGUID": DISPLAY_CLASS_GUID.upper(), "Driver": DISPLAY_CLASS_GUID + "\\0002",
                        "DeviceDesc": "@oem62.inf,%nvidia_dev.1f15%;NVIDIA GeForce RTX 2060"},
        INTEL_DEVICE: {"ClassGUID": DISPLAY_CLASS_GUID, "Driver": DISPLAY_CLASS_GUID + "\\0001",
                       "DeviceDesc": "@oem55.inf,%iCFL%;Intel(R) UHD Graphics"},
        USB_DEVICE: {"ClassGUID": USB_CLASS_GUID, "DeviceDesc": "USB Controller"},
        **extra_instances,
    }
    tree = {PCI_ENUM_KEY: (list(devices) + ["CC_0600"], {}), f"{PCI_ENUM_KEY}\\CC_0600": ([], {})}
    for device, values in devices.items():
        tree[f"{PCI_ENUM_KEY}\\{device}"] = (["4&1a2b3c4d&0&0008"], {})
        tree[f"{PCI_ENUM_KEY}\\{device}\\4&1a2b3c4d&0&0008"] = ([], values)
    tree[f"{CLASS_KEY}\\{DISPLAY_CLASS_GUID}\\0001"] = ([], {"DriverVersion": "26.20.100.7642"})
    tree[f"{CLASS_KEY}\\{DISPLAY_CLASS_GUID}\\0002"] = ([], {"DriverVersion": "32.0.16.1742"})
    return tree


def walk(tree, *, denied=(), clock=None):
    return list_display_adapters(FakeRegistry(tree, denied), deadline=100.0, clock=clock or (lambda: 0.0))


class AdapterTests(unittest.TestCase):
    def test_display_adapters_with_ids_from_the_pci_key(self):
        read = walk(machine_registry())
        self.assertTrue(read.ok)
        self.assertEqual(read.items, (AdapterInfo("10de", "1f15", "NVIDIA GeForce RTX 2060", "32.0.16.1742"),
                                      AdapterInfo("8086", "9bc4", "Intel(R) UHD Graphics", "26.20.100.7642")))

    def test_card_without_driver_is_found_by_its_class_code(self):
        bare = {"VEN_10DE&DEV_2684&SUBSYS_00000000&REV_A1": {
            "CompatibleIDs": ["PCI\\VEN_10DE&DEV_2684&REV_A1", "PCI\\VEN_10DE&CC_030000", "PCI\\CC_030000"],
            "DeviceDesc": "Video Controller"}}
        read = walk(machine_registry(**bare))
        self.assertIn(AdapterInfo("10de", "2684", "Video Controller", None), read.items)

    def test_basic_display_adapter_keeps_the_nvidia_vendor_id(self):
        basic = {"VEN_10DE&DEV_1F15&SUBSYS_00000000&REV_A1": {
            "ClassGUID": DISPLAY_CLASS_GUID, "Driver": DISPLAY_CLASS_GUID + "\\0009",
            "DeviceDesc": "@display.inf,%basicdisplay%;Microsoft Basic Display Adapter"}}
        read = walk(machine_registry(**basic))
        self.assertIn(AdapterInfo("10de", "1f15", "Microsoft Basic Display Adapter", None), read.items)

    def test_instances_without_class_are_skipped(self):
        read = walk(machine_registry(**{"VEN_1B21&DEV_2142&SUBSYS_00000000&REV_00": {}}))
        self.assertEqual([item.vendor_id for item in read.items], ["10de", "8086"])

    def test_unreadable_class_entry_only_loses_the_driver_version(self):
        denied = [f"{CLASS_KEY}\\{DISPLAY_CLASS_GUID}\\0002"]
        read = walk(machine_registry(), denied=denied)
        self.assertTrue(read.ok)
        self.assertEqual(read.items[0], AdapterInfo("10de", "1f15", "NVIDIA GeForce RTX 2060", None))

    def test_unreadable_enumeration_is_not_ok(self):
        for denied in ([PCI_ENUM_KEY], [f"{PCI_ENUM_KEY}\\{NVIDIA_DEVICE}"]):
            with self.subTest(denied=denied):
                read = walk(machine_registry(), denied=denied)
                self.assertEqual((read.ok, read.items), (False, ()))
                self.assertTrue(read.error.startswith("PermissionError"))

    def test_the_deadline_stops_the_walk(self):
        ticks = iter(range(1000))
        read = list_display_adapters(FakeRegistry(machine_registry()), deadline=3.0, clock=lambda: next(ticks))
        self.assertEqual(read, hs.AdapterRead(False, "timeout"))


class FakePsutil(types.ModuleType):
    class Error(Exception):
        pass

    def __init__(self, battery=None, battery_error=None):
        super().__init__("psutil")
        self.battery, self.battery_error = battery, battery_error

    def sensors_battery(self):
        if self.battery_error is not None:
            raise self.battery_error
        return self.battery

    def disk_usage(self, path):
        raise self.Error("no such drive")


class PsutilTests(unittest.TestCase):
    def power(self, module, status=None):
        """``status`` is (ACLineStatus, BatteryFlag) of GetSystemPowerStatus, None when unreadable."""
        with patch.dict(sys.modules, {"psutil": module}), \
                patch.object(hs, "_system_power_status", return_value=status) as windows:
            return WindowsSystem().power(), windows.called

    def test_power_with_a_battery_comes_from_psutil(self):
        cases = {
            "laptop plugged in": (FakePsutil(SimpleNamespace(power_plugged=True)), PowerInfo(True, True)),
            "laptop on battery": (FakePsutil(SimpleNamespace(power_plugged=False)), PowerInfo(True, False)),
            "plug state unknown": (FakePsutil(SimpleNamespace(power_plugged=None)), PowerInfo(True, None)),
            "sensor error": (FakePsutil(battery_error=RuntimeError("x")), PowerInfo(None, None)),
            "psutil missing": (None, PowerInfo(None, None)),
        }
        for label, (module, expected) in cases.items():
            with self.subTest(label):
                self.assertEqual(self.power(module), (expected, False))

    def test_psutil_without_a_battery_asks_windows_which_none_it_is(self):
        # psutil returns None for BatteryFlag 128 (no system battery) and 255 (unknown) alike.
        cases = {
            "no system battery on mains power": ((1, 128), PowerInfo(False, True)),
            "no system battery, mains state unknown": ((255, 128), PowerInfo(False, None)),
            "unknown battery on mains power": ((1, 255), PowerInfo(None, True)),
            "unknown battery on battery power": ((0, 255), PowerInfo(None, False)),
            "everything unknown": ((255, 255), PowerInfo(None, None)),
            "battery appeared between the two reads": ((1, 1), PowerInfo(None, True)),
            "status unreadable": (None, PowerInfo(None, None)),
        }
        for label, (status, expected) in cases.items():
            with self.subTest(label):
                self.assertEqual(self.power(FakePsutil(None), status), (expected, True))

    def test_disk_error_is_unknown(self):
        with patch.dict(sys.modules, {"psutil": FakePsutil()}):
            self.assertIsNone(WindowsSystem().disk_usage(Path("Z:/")))


class FileTests(unittest.TestCase):
    def setUp(self):
        TEMP.mkdir(parents=True, exist_ok=True)
        temp = TemporaryDirectory(dir=TEMP)
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)

    def test_stat_and_sha256_of_a_regular_file(self):
        path = self.folder / "ffmpeg.exe"
        path.write_bytes(b"abc")
        machine = WindowsSystem()
        self.assertEqual(machine.file_stat(path), FileStat(3, path.stat().st_mtime_ns))
        self.assertEqual(machine.file_sha256(path), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")

    def test_missing_file_or_folder_has_no_stat(self):
        machine = WindowsSystem()
        self.assertIsNone(machine.file_stat(self.folder / "missing.exe"))
        self.assertIsNone(machine.file_stat(self.folder))
        with self.assertRaises(OSError):
            machine.file_sha256(self.folder / "missing.exe")

    def test_torch_build_comes_from_the_installed_distribution(self):
        with patch("importlib.metadata.version", return_value="2.14.0+cu126") as version:
            self.assertEqual(WindowsSystem().torch_build(), "2.14.0+cu126")
        version.assert_called_once_with("torch")
        with patch("importlib.metadata.version", side_effect=metadata.PackageNotFoundError("torch")):
            self.assertIsNone(WindowsSystem().torch_build())


class PlatformTests(unittest.TestCase):
    def test_platform_info_runs_no_command(self):
        # platform.system()/version() run "ver" through a shell on Python 3.11 unless uname is cached.
        with patch.object(platform, "_uname_cache", None), \
                patch("subprocess.Popen", side_effect=AssertionError("a command was started")):
            info = WindowsSystem().platform_info()
        self.assertEqual(info.python, platform.python_version())
        if sys.platform == "win32":
            version = sys.getwindowsversion()
            self.assertEqual(info.os, f"Windows {version.major}.{version.minor}.{version.build}")

    def test_platform_without_a_windows_version_is_unknown(self):
        with patch.object(hs, "sys", SimpleNamespace()):
            self.assertIsNone(WindowsSystem().platform_info().os)


@unittest.skipUnless(sys.platform == "win32", "registry, NVML and kernel32 are Windows features")
class RealWindowsReadOnlyTests(unittest.TestCase):
    """Reads NVML, the registry, kernel32 and psutil only: never nvidia-smi, FFmpeg or the torch probe."""

    def test_nvml_has_the_profile_shapes(self):
        read = checked_nvml(WindowsSystem().nvml())
        self.assertIn(read.status, NVML_STATUSES)
        if read.status == "ok":
            self.assertRegex(read.driver, r"^\d{1,4}\.\d{1,3}$")
            for gpu in read.gpus:
                self.assertRegex(gpu.compute_capability, r"^\d{1,2}\.\d{1,2}$")
                self.assertGreater(gpu.vram_mib, 0)
        else:
            self.assertEqual(read.gpus, ())

    def test_adapters_have_the_profile_shapes_and_agree_with_nvml(self):
        machine = WindowsSystem()
        adapters = machine.adapters(timeout=5.0)
        self.assertTrue(adapters.ok, adapters.error)
        self.assertLessEqual(len(adapters.items), 64)
        for item in adapters.items:
            self.assertRegex(item.vendor_id, HEX4)
            self.assertRegex(item.device_id, HEX4)
            self.assertTrue(item.name is None or 0 < len(item.name) <= 120)
        nvml = checked_nvml(machine.nvml())
        if nvml.status == "ok" and nvml.gpus:
            self.assertIn("10de", {item.vendor_id for item in adapters.items})

    def test_cpu_and_psutil_have_the_profile_shapes(self):
        machine = WindowsSystem()
        cpu = machine.cpu()
        self.assertTrue(cpu.model and len(cpu.model) <= 120)
        self.assertIn(cpu.avx2, (True, False))
        physical, logical = machine.cpu_counts()
        self.assertGreaterEqual(logical, physical)
        self.assertGreater(machine.ram_bytes(), 0)
        total, free = machine.disk_usage(Path(ROOT.anchor))
        self.assertGreaterEqual(total, free)
        power = machine.power()
        self.assertIn(power.battery_present, (True, False))
        self.assertTrue(machine.system_directory().is_dir())

    def test_system_power_status_is_read_from_kernel32(self):
        status = hs._system_power_status()
        self.assertIsNotNone(status)
        ac_line, battery_flag = status
        self.assertTrue(0 <= ac_line <= 255 and 0 <= battery_flag <= 255, status)


class ModuleContractTests(unittest.TestCase):
    def test_import_loads_no_torch_pynvml_or_psutil_and_starts_no_thread(self):
        code = (
            "import sys, threading\n"
            "before = threading.active_count()\n"
            "import biliflow.hardware_system, biliflow.hardware_detect\n"
            "biliflow.hardware_system.WindowsSystem().torch_build()\n"
            "print(threading.active_count() == before)\n"
            "print(*(name in sys.modules for name in ('torch', 'pynvml', 'psutil', 'biliflow.hardware_profile',"
            " 'biliflow.hardware_table', 'biliflow.control_center', 'biliflow.nvdec')))\n"
            "print(','.join(sorted(name for name in sys.modules if name.split('.')[0] == 'biliflow')))\n"
        )
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=120,
                                check=True, env=env)
        # The torch child imports this module: only the light package __init__ and these three come along.
        loaded = "biliflow,biliflow.hardware_detect,biliflow.hardware_schema,biliflow.hardware_system"
        self.assertEqual(result.stdout.split(), ["True"] + ["False"] * 7 + [loaded])

    def test_sources_use_static_imports_and_no_shell(self):
        for module in ("hardware_system", "hardware_detect"):
            with self.subTest(module):
                source = (ROOT / "src" / "biliflow" / f"{module}.py").read_text(encoding="utf-8")
                for banned in ("shell=True", "import_module", "__import__", "os.system", "print("):
                    self.assertNotIn(banned, source)


if __name__ == "__main__":
    unittest.main()
