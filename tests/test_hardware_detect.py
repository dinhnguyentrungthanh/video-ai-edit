"""E1 detection: detect(), observe() and the torch probe against a fake machine (HARDWARE_PROFILE.md section 9)."""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import unittest
from pathlib import Path, PureWindowsPath
from unittest.mock import patch

from biliflow import hardware_detect as hd
from biliflow import hardware_profile as hp
from biliflow.hardware_schema import (
    FAILURE_MESSAGES, FileStat, GpuInfo, Observation, build_profile, classify_attempt, sanitize_message,
    write_profile,
)
from biliflow.hardware_system import (
    NOT_FOUND, AdapterInfo, AdapterRead, CommandResult, CpuInfo, HardwareSystem, NvmlRead, PlatformInfo, PowerInfo,
    WindowsSystem,
)
from tests.hardware_fixtures import TempRootCase

ROOT = Path(__file__).resolve().parents[1]
FAKE_ROOT = ROOT / "temp" / "hardware-detect-root"  # never created: detect() reads only through the fake
FFMPEG_DIR = FAKE_ROOT / "tools" / "ffmpeg" / "bin"
PYTHON = FAKE_ROOT / ".venv" / "Scripts" / "python.exe"
NOW = "2026-10-11T10:00:00+07:00"

RTX2060 = GpuInfo("NVIDIA GeForce RTX 2060", "7.5", 6144)
RTX3060 = GpuInfo("NVIDIA GeForce RTX 3060", "8.6", 12288)
NVIDIA_CARD = AdapterInfo("10de", "1f15", "NVIDIA GeForce RTX 2060", "32.0.16.1742")
INTEL_IGPU = AdapterInfo("8086", "9bc4", "Intel(R) UHD Graphics", "26.20.100.7642")
BASIC_DISPLAY = AdapterInfo("10de", "1f15", "Microsoft Basic Display Adapter", None)
AMPERE_CARD = AdapterInfo("10de", "2504", "NVIDIA GeForce RTX 3060", "32.0.16.1742")
ARCH_LIST =["sm_50", "sm_60", "sm_61", "sm_70", "sm_75", "sm_80", "sm_86", "sm_90"]

SMI_2060 = "NVIDIA GeForce RTX 2060, 6144, 617.42, 7.5\n"
VERSION_OUT = ("ffmpeg version 9.0.1-essentials_build-build.example Copyright (c) 2000-2026 the FFmpeg developers\n"
               "built with gcc 15.1.0\nconfiguration: --enable-gpl --enable-nvenc\n")
HWACCELS_OUT = "Hardware acceleration methods:\ncuda\nvaapi\ndxva2\nqsv\nd3d11va\nd3d12va\namf\n\n"
ENCODERS_OUT = (
    "Encoders:\n V..... = Video\n A..... = Audio\n S..... = Subtitle\n"
    " .F.... = Frame-level multithreading\n ------\n"
    " V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC (codec h264)\n"
    " V....D h264_nvenc           NVIDIA NVENC H.264 encoder (codec h264)\n"
    " V....D hevc_nvenc           NVIDIA NVENC hevc encoder (codec hevc)\n"
    " V..... h264_qsv             H.264 / AVC (Intel Quick Sync Video acceleration) (codec h264)\n"
    " V....D h264_amf             AMD AMF H.264 Encoder (codec h264)\n"
    " A....D aac                  AAC (Advanced Audio Coding)\n"
)
LISTINGS = {"-version": CommandResult(0, VERSION_OUT), "-hwaccels": CommandResult(0, HWACCELS_OUT),
            "-encoders": CommandResult(0, ENCODERS_OUT)}


def probe_payload(**changes):
    payload = {"ok": True, "device_count": 1, "device_name": "NVIDIA GeForce RTX 2060", "capability": "7.5",
               "arch_list": list(ARCH_LIST), "total_memory_bytes": 6442450944, "fp16_ok": True, "seconds": 2.5,
               "error": None, "torch_cuda": "12.6", "cudnn": "91002"}
    payload.update(changes)
    return payload


def probe_line(**changes) -> str:
    return json.dumps(probe_payload(**changes)) + "\n"


NOT_AVAILABLE = dict(ok=False, device_count=0, device_name=None, capability=None, arch_list=[],
                     total_memory_bytes=None, fp16_ok=None, error="cuda_not_available")


class FakeSystem:
    """HardwareSystem stand-in; every command must be an argv list with a finite timeout."""

    def __init__(self, *, nvml=None, adapters=None, probe=None, smi=None, ffmpeg_present=True, listings=None,
                 sha_error=None, system_directory=PureWindowsPath(r"C:\Windows\System32")):
        self._nvml = nvml if nvml is not None else NvmlRead("ok", "617.42", (RTX2060,))
        self._adapters = adapters if adapters is not None else AdapterRead(True, None, (NVIDIA_CARD, INTEL_IGPU))
        self.probe = probe if probe is not None else CommandResult(0, probe_line())
        self.smi = smi if smi is not None else CommandResult(0, SMI_2060)
        self.ffmpeg_present, self.sha_error = ffmpeg_present, sha_error
        self.listings = dict(LISTINGS if listings is None else listings)
        self._system_directory = system_directory
        self.calls: list[tuple[list[str], float, dict | None]] = []
        self.adapter_timeouts: list[float] = []
        self.stats: list[Path] = []

    def run(self, argv, *, timeout, env=None):
        assert type(argv) is list, "a command is an argv list, never a shell string"
        assert all(type(part) is str for part in argv)
        assert type(timeout) in (int, float) and 0 < timeout < math.inf, "every command has a timeout"
        self.calls.append((list(argv), timeout, env))
        name = PureWindowsPath(argv[0]).name.lower()
        if name == "nvidia-smi.exe":
            return self.smi
        if name == "ffmpeg.exe":
            assert argv[1] == "-hide_banner"
            return self.listings[argv[2]]
        if argv[1:] == ["-B", "-m", "biliflow.hardware_detect", "torch-probe"]:
            return self.probe
        raise AssertionError(f"unexpected command {argv}")

    def nvml(self):
        return self._nvml

    def adapters(self, *, timeout):
        assert 0 < timeout < math.inf
        self.adapter_timeouts.append(timeout)
        return self._adapters

    def cpu(self):
        return CpuInfo("Intel(R) Core(TM) i7-10750H CPU @ 2.60GHz", "GenuineIntel", True, False)

    def cpu_counts(self):
        return 6, 12

    def ram_bytes(self):
        return 16988123136

    def disk_usage(self, path):
        assert Path(path) == Path(Path(path).anchor), "disk usage of the drive, not of a folder"
        return 1000203087872, 609052971008

    def power(self):
        return PowerInfo(True, True)

    def file_stat(self, path):
        self.stats.append(Path(path))
        return FileStat(102856192, 1786512472000000000) if self.ffmpeg_present else None

    def file_sha256(self, path):
        if self.sha_error is not None:
            raise self.sha_error
        return "72a489eccd008c2ec2c0a5856c5c75bc3d8bbfa90166c4566865c246445e6aa3"

    def torch_build(self):
        return "2.14.0+cu126"

    def platform_info(self):
        return PlatformInfo("Windows 10.0.26300", "3.11.16")

    def system_directory(self):
        return self._system_directory

    def commands(self, name: str) -> list[list[str]]:
        return [argv for argv, _timeout, _env in self.calls if PureWindowsPath(argv[0]).name.lower() == name]


class RealChildStderr(FakeSystem):
    """Runs a real Python child (never the torch probe) with the probe's own environment in its place.

    On a pipe that child writes stderr in the ANSI code page unless the environment asks for UTF-8;
    WindowsSystem.run decodes UTF-8, so a non-ASCII path would come back garbled.
    """
    CODE = "import sys\nprint('OSError: cannot load ' + sys.argv[1], file=sys.stderr)\nsys.exit(1)\n"

    def __init__(self, path: str):
        super().__init__()
        self.path = path

    def run(self, argv, *, timeout, env=None):
        if argv[1:] != ["-B", "-m", "biliflow.hardware_detect", "torch-probe"]:
            return super().run(argv, timeout=timeout, env=env)
        self.calls.append((list(argv), timeout, env))
        return WindowsSystem().run([sys.executable, "-B", "-c", self.CODE, self.path], timeout=60, env=env)


def without_runner_encoding():
    """os.environ without the runner's own PYTHONIOENCODING/PYTHONUTF8: the child would inherit them."""
    kept = {key: value for key, value in os.environ.items() if key.upper() not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    return patch.dict(os.environ, kept, clear=True)


def run_detect(system: FakeSystem, **kwargs) -> hd.Detection:
    return hd.detect(FAKE_ROOT, ffmpeg_dir=FFMPEG_DIR, python_exe=PYTHON, system=system, **kwargs)


def profile_of(result: hd.Detection, *, previous: bool = False):
    """classify_attempt, then a complete profile through build_profile (which validates it)."""
    outcome = classify_attempt(result.detection, result.gate_key, previous=previous)
    failure = None if outcome.failure_code is None else {
        "code": outcome.failure_code, "step": "detect", "message": FAILURE_MESSAGES[outcome.failure_code]}
    document = build_profile(
        written_at=NOW, writer={"biliflow_version": "0.7.24", "mode": "measure", "launched_by": "script"},
        attempt={"result": outcome.result, "failure": failure, "started_at": NOW, "finished_at": NOW,
                 "budget_seconds": 600},
        gate_key=result.gate_key, info=result.info, detection=result.detection, measurements=[],
        history={"had_cuda": outcome.had_cuda, "first_cuda_at": NOW if outcome.had_cuda else None})
    return outcome, document


class DetectShapeTests(unittest.TestCase):
    def test_rtx2060_machine_gives_a_valid_ok_profile(self):
        result = run_detect(FakeSystem())
        outcome, document = profile_of(result)
        self.assertEqual((outcome.result, outcome.failure_code, outcome.had_cuda), ("ok", None, True))
        self.assertEqual(result.gate_key["nvidia_gpus"],
                         [{"name": "NVIDIA GeForce RTX 2060", "compute_capability": "7.5", "vram_mib": 6144}])
        self.assertEqual(result.gate_key["nvidia_driver"], "617.42")
        self.assertEqual(result.gate_key["torch_build"], "2.14.0+cu126")
        self.assertEqual(result.gate_key["ffmpeg"]["version"], "9.0.1")
        self.assertEqual(result.info, {"os": "Windows 10.0.26300", "python": "3.11.16", "torch_cuda": "12.6",
                                       "cudnn": "91002"})
        self.assertEqual(document["detection"]["adapters"]["items"][0]["vendor"], "nvidia")
        self.assertEqual(document["detection"]["power"], {"battery_present": True, "ac_online": True})

    def test_nvidia_smi_is_queried_from_system32_with_the_capability_query(self):
        system = FakeSystem()
        result = run_detect(system)
        argv, timeout, _env = next(call for call in system.calls if call[0][0].lower().endswith("nvidia-smi.exe"))
        self.assertEqual(argv, [r"C:\Windows\System32\nvidia-smi.exe",
                                "--query-gpu=name,memory.total,driver_version,compute_cap",
                                "--format=csv,noheader,nounits"])
        self.assertEqual(timeout, 10.0)
        self.assertEqual(result.detection["nvidia_smi"], {
            "ok": True, "driver_version": "617.42", "error": None,
            "gpus": [{"name": "NVIDIA GeForce RTX 2060", "compute_capability": "7.5", "vram_mib": 6144}]})

    def test_torch_probe_runs_this_module_in_a_child_with_a_120_second_timeout(self):
        system = FakeSystem()
        with without_runner_encoding():
            run_detect(system)
        argv, timeout, env = next(call for call in system.calls if call[0][1:2] == ["-B"])
        self.assertEqual(argv, [str(PYTHON), "-B", "-m", "biliflow.hardware_detect", "torch-probe"])
        self.assertEqual(timeout, 120.0)
        source = str(Path(hd.__file__).resolve().parents[1])
        self.assertEqual(env["PYTHONPATH"].split(os.pathsep)[0], source)
        self.assertEqual(env["CUDA_DEVICE_ORDER"], "PCI_BUS_ID")  # torch numbers the devices as NVML does
        self.assertEqual(env["PYTHONIOENCODING"], "utf-8")  # stderr in the encoding the parent decodes

    def test_fake_system_implements_every_protocol_method(self):
        names = {name for name in vars(HardwareSystem) if not name.startswith("_")}
        self.assertTrue(names)
        self.assertEqual({name for name in names if not callable(getattr(FakeSystem, name, None))}, set())

    def test_progress_names_every_step_in_order(self):
        steps: list[str] = []
        run_detect(FakeSystem(), progress=steps.append)
        self.assertEqual(tuple(steps), hd.DETECT_STEPS)

    def test_adapters_are_listed_with_a_deadline(self):
        system = FakeSystem()
        run_detect(system)
        self.assertEqual(system.adapter_timeouts, [hd.ADAPTER_TIMEOUT_SECONDS])

    def test_odd_nvml_data_is_reported_as_an_nvml_error(self):
        cases = {
            "driver": NvmlRead("ok", "32.0.16.1742", (RTX2060,)),
            "capability": NvmlRead("ok", "617.42", (GpuInfo("NVIDIA GeForce RTX 2060", "sm_75", 6144),)),
            "name": NvmlRead("ok", "617.42", (GpuInfo("", "7.5", 6144),)),
            "status": NvmlRead("maybe"),
            "more than 8 GPUs": NvmlRead("ok", "617.42", (RTX2060,) * 9),  # the schema keeps at most 8
            "negative VRAM": NvmlRead("ok", "617.42", (GpuInfo(RTX2060.name, "7.5", -1),)),
            "VRAM not an int": NvmlRead("ok", "617.42", (GpuInfo(RTX2060.name, "7.5", True),)),
            "VRAM out of range": NvmlRead("ok", "617.42", (GpuInfo(RTX2060.name, "7.5", 10 ** 8),)),
        }
        for label, read in cases.items():
            with self.subTest(label):
                result = run_detect(FakeSystem(nvml=read))
                self.assertEqual(result.detection["nvml"]["status"], "error")
                self.assertEqual(result.gate_key["nvidia_gpus"], [])
                self.assertIsNone(result.gate_key["nvidia_driver"])
                profile_of(result)  # an NVML error, never a schema error

    def test_more_than_16_adapters_keep_the_nvidia_ones(self):
        others = tuple(AdapterInfo("1414", f"{index:04x}", "Remote display", None) for index in range(20))
        result = run_detect(FakeSystem(adapters=AdapterRead(True, None, others + (NVIDIA_CARD,))))
        items = result.detection["adapters"]["items"]
        self.assertEqual(len(items), 16)
        self.assertEqual(items[0]["vendor_id"], "10de")
        self.assertEqual(items[1]["vendor"], "other")


class DetectOutcomeTests(unittest.TestCase):
    """Section 9, "E1, nhận diện": detect reports, classify_attempt decides, the profile validates."""

    def test_no_nvml_library_and_only_an_intel_igpu_is_a_cpu_machine(self):
        system = FakeSystem(nvml=NvmlRead("no_library", error="Không có nvml.dll"),
                            adapters=AdapterRead(True, None, (INTEL_IGPU,)), smi=CommandResult(None, error=NOT_FOUND),
                            probe=CommandResult(0, probe_line(**NOT_AVAILABLE)))
        result = run_detect(system)
        outcome, document = profile_of(result)
        self.assertEqual((outcome.result, outcome.failure_code, outcome.had_cuda), ("ok", None, False))
        self.assertEqual(result.detection["nvml"]["status"], "no_library")
        self.assertIsNone(result.detection["nvidia_smi"])
        self.assertEqual((result.gate_key["nvidia_gpus"], result.gate_key["nvidia_driver"]), ([], None))
        self.assertFalse(document["history"]["had_cuda"])

    def test_nvml_ok_but_cuda_not_available_fails_as_cuda_unavailable(self):
        result = run_detect(FakeSystem(probe=CommandResult(0, probe_line(**NOT_AVAILABLE))))
        for previous in (False, True):
            with self.subTest(previous=previous):
                outcome, document = profile_of(result, previous=previous)
                self.assertEqual((outcome.result, outcome.failure_code), ("failed", "cuda_unavailable"))
                self.assertEqual(outcome.had_cuda, previous)
                self.assertEqual(document["detection"]["torch_cuda"]["error"], "cuda_not_available")

    def test_probe_timeout_fails_as_torch_probe_timeout(self):
        result = run_detect(FakeSystem(probe=CommandResult(None, timed_out=True)))
        outcome, _document = profile_of(result)
        self.assertEqual(outcome.failure_code, "torch_probe_timeout")
        self.assertEqual(result.detection["torch_cuda"]["error"], "torch_probe_timeout")
        self.assertEqual(result.info["torch_cuda"], None)

    def test_broken_probe_output_fails_as_torch_probe_crashed(self):
        good = probe_payload()
        missing = {key: value for key, value in good.items() if key != "cudnn"}
        cases = {
            "nonzero exit": CommandResult(1, "", "Traceback\nModuleNotFoundError: No module named 'torch'\n"),
            "no line": CommandResult(0, "\n\n"),
            "garbage": CommandResult(0, "CUDA ready!\n"),
            "two lines": CommandResult(0, probe_line() + probe_line()),
            "unknown key": CommandResult(0, json.dumps({**good, "extra": 1}) + "\n"),
            "missing key": CommandResult(0, json.dumps(missing) + "\n"),
            "wrong type": CommandResult(0, probe_line(device_count="1")),
            "bool count": CommandResult(0, probe_line(device_count=True)),
            "ok without device": CommandResult(0, probe_line(device_count=0)),
            "failed without error": CommandResult(0, probe_line(ok=False)),
            "nan": CommandResult(0, probe_line().replace("2.5", "NaN")),
            "duplicate key": CommandResult(0, probe_line().replace('{"ok": true', '{"ok": true, "ok": true')),
            "cannot start": CommandResult(None, error="OSError: bad image"),
        }
        for label, probe in cases.items():
            with self.subTest(label):
                result = run_detect(FakeSystem(probe=probe))
                outcome, document = profile_of(result)
                self.assertEqual(outcome.failure_code, "torch_probe_crashed")
                self.assertTrue(document["detection"]["torch_cuda"]["error"].startswith("torch_probe_crashed: "))
                self.assertFalse(document["detection"]["torch_cuda"]["ok"])

    def test_nvml_and_torch_disagreeing_fails_as_a_mismatch(self):
        cases = {
            "capability": (NvmlRead("ok", "617.42", (RTX2060,)), probe_line(capability="8.6")),
            "count": (NvmlRead("ok", "617.42", (RTX2060,)), probe_line(device_count=2)),
        }
        for label, (nvml, line) in cases.items():
            with self.subTest(label):
                outcome, document = profile_of(run_detect(FakeSystem(nvml=nvml, probe=CommandResult(0, line))))
                self.assertEqual((outcome.result, outcome.failure_code), ("failed", "nvml_torch_mismatch"))
                self.assertEqual(document["attempt"]["failure"]["code"], "nvml_torch_mismatch")

    def test_nvml_error_with_working_cuda_fails_as_nvml_failed(self):
        result = run_detect(FakeSystem(nvml=NvmlRead("error", error="NVMLError_Unknown: Unknown Error")))
        outcome, _document = profile_of(result)
        self.assertEqual(outcome.failure_code, "nvml_failed")

    def test_two_gpus_are_recorded_as_they_are(self):
        nvml = NvmlRead("ok", "617.42", (RTX2060, RTX2060))
        smi = CommandResult(0, SMI_2060 * 2)
        result = run_detect(FakeSystem(nvml=nvml, smi=smi, probe=CommandResult(0, probe_line(device_count=2))))
        outcome, document = profile_of(result)
        self.assertEqual(outcome.result, "ok")
        self.assertEqual(len(document["fingerprint"]["gate_key"]["nvidia_gpus"]), 2)
        self.assertEqual(len(document["detection"]["nvidia_smi"]["gpus"]), 2)

    def test_nvidia_card_without_driver_is_never_a_cpu_machine(self):
        system = FakeSystem(nvml=NvmlRead("no_library", error="Không có nvml.dll"),
                            adapters=AdapterRead(True, None, (BASIC_DISPLAY, INTEL_IGPU)),
                            smi=CommandResult(None, error=NOT_FOUND),
                            probe=CommandResult(0, probe_line(**NOT_AVAILABLE)))
        outcome, document = profile_of(run_detect(system))
        self.assertEqual((outcome.result, outcome.failure_code, outcome.had_cuda),
                         ("failed", "cuda_unavailable", False))
        self.assertEqual(document["detection"]["adapters"]["items"][0]["name"], "Microsoft Basic Display Adapter")

    def test_unreadable_adapters_fail_as_adapters_unreadable(self):
        error = f"PermissionError: [WinError 5] Access is denied: '{FAKE_ROOT}'"
        cases = {
            "unreadable": (AdapterRead(False, error), "PermissionError: [WinError 5] Access is denied: '<root>'"),
            "timeout": (AdapterRead(False, "timeout"), "timeout"),
        }
        for label, (adapters, expected_error) in cases.items():
            with self.subTest(label):
                system = FakeSystem(nvml=NvmlRead("no_library", error="Không có nvml.dll"), adapters=adapters,
                                    probe=CommandResult(0, probe_line(**NOT_AVAILABLE)))
                result = run_detect(system)
                outcome, _document = profile_of(result)
                self.assertEqual(outcome.failure_code, "adapters_unreadable")
                self.assertEqual(result.detection["adapters"]["items"], [])
                self.assertFalse(result.detection["adapters"]["ok"])
                self.assertEqual(result.detection["adapters"]["error"], expected_error)
                self.assertNotIn(str(FAKE_ROOT), result.detection["adapters"]["error"])

    def test_ampere_card_is_recorded_as_it_is(self):
        nvml = NvmlRead("ok", "617.42", (RTX3060,))
        probe = probe_line(device_name="NVIDIA GeForce RTX 3060", capability="8.6",
                           total_memory_bytes=12884901888)
        result = run_detect(FakeSystem(nvml=nvml, smi=CommandResult(0, "NVIDIA GeForce RTX 3060, 12288, 617.42, 8.6\n"),
                                       probe=CommandResult(0, probe)))
        outcome, document = profile_of(result)
        self.assertEqual(outcome.result, "ok")
        self.assertEqual(document["detection"]["torch_cuda"]["capability"], "8.6")
        self.assertEqual(document["detection"]["torch_cuda"]["arch_list"], ARCH_LIST)
        self.assertEqual(document["fingerprint"]["gate_key"]["nvidia_gpus"][0]["compute_capability"], "8.6")

    def test_card_without_a_kernel_in_torch_keeps_its_facts(self):
        line = probe_line(ok=False, device_name="NVIDIA GeForce RTX 5090", capability="12.0", fp16_ok=None,
                          total_memory_bytes=34190917632,
                          error="cuda_matmul_failed: RuntimeError: CUDA error: no kernel image is available")
        nvml = NvmlRead("ok", "617.42", (GpuInfo("NVIDIA GeForce RTX 5090", "12.0", 32607),))
        result = run_detect(FakeSystem(nvml=nvml, probe=CommandResult(0, line)))
        outcome, document = profile_of(result)
        self.assertEqual(outcome.failure_code, "cuda_unavailable")
        torch_cuda = document["detection"]["torch_cuda"]
        self.assertEqual((torch_cuda["capability"], torch_cuda["device_count"]), ("12.0", 1))
        self.assertEqual(torch_cuda["arch_list"], ARCH_LIST)
        self.assertTrue(torch_cuda["error"].startswith("cuda_matmul_failed"))


class NvidiaSmiTests(unittest.TestCase):
    """nvidia-smi is recorded only; it never changes the outcome."""

    def outcome(self, smi: CommandResult | None):
        result = run_detect(FakeSystem(smi=smi) if smi is not None else FakeSystem())
        return result, classify_attempt(result.detection, result.gate_key, previous=False)

    def test_missing_nvidia_smi_is_null(self):
        result, outcome = self.outcome(CommandResult(None, error=NOT_FOUND))
        self.assertIsNone(result.detection["nvidia_smi"])
        self.assertEqual(outcome.result, "ok")
        profile_of(result)

    def test_not_available_values_are_null(self):
        result, outcome = self.outcome(CommandResult(0, "NVIDIA GeForce RTX 2060, [N/A], [N/A], [N/A]\n"))
        self.assertEqual(result.detection["nvidia_smi"], {
            "ok": True, "driver_version": None, "error": None,
            "gpus": [{"name": "NVIDIA GeForce RTX 2060", "compute_capability": None, "vram_mib": None}]})
        self.assertEqual(outcome.result, "ok")

    def test_disagreeing_with_nvml_is_only_recorded(self):
        _baseline, expected = self.outcome(None)
        cases = {
            "other card": CommandResult(0, "NVIDIA GeForce RTX 3060, 12288, 620.01, 8.6\n"),
            "two cards": CommandResult(0, SMI_2060 * 2),
            "no card": CommandResult(0, ""),
            "failed": CommandResult(6, "No devices were found\n"),
            "timeout": CommandResult(None, timed_out=True),
            "garbage": CommandResult(0, "617.42, NVIDIA GeForce RTX 2060\n"),
            "odd driver": CommandResult(0, "NVIDIA GeForce RTX 2060, 6144, 32.0.16.1742, 7.5\n"),
        }
        for label, smi in cases.items():
            with self.subTest(label):
                result, outcome = self.outcome(smi)
                self.assertEqual(outcome, expected)
                self.assertEqual(result.gate_key["nvidia_gpus"][0]["compute_capability"], "7.5")
                profile_of(result)

    def test_failures_are_described_without_the_output(self):
        result, _outcome = self.outcome(CommandResult(6, "No devices were found\n"))
        self.assertEqual(result.detection["nvidia_smi"]["error"], "nvidia-smi: thoát mã 6")
        result, _outcome = self.outcome(CommandResult(0, "617.42, NVIDIA GeForce RTX 2060\n"))
        self.assertFalse(result.detection["nvidia_smi"]["ok"])
        self.assertNotIn("NVIDIA GeForce", result.detection["nvidia_smi"]["error"])

    def test_parse_handles_a_comma_in_the_name(self):
        driver, gpus = hd.parse_nvidia_smi("NVIDIA RTX A2000, Laptop GPU, 4096, 617.42, 8.6\n")
        self.assertEqual(driver, "617.42")
        self.assertEqual(gpus, [{"name": "NVIDIA RTX A2000, Laptop GPU", "compute_capability": "8.6",
                                 "vram_mib": 4096}])

    def test_no_system32_folder_is_a_failure_not_a_command(self):
        system = FakeSystem(system_directory=None)
        result = run_detect(system)
        self.assertFalse(result.detection["nvidia_smi"]["ok"])
        self.assertEqual(system.commands("nvidia-smi.exe"), [])


class FfmpegTests(unittest.TestCase):
    def test_listings_are_recorded_and_no_trial_runs(self):
        result = run_detect(FakeSystem())
        ffmpeg = result.detection["ffmpeg"]
        self.assertTrue(ffmpeg["ok"])
        self.assertEqual(ffmpeg["path"], str(FFMPEG_DIR / "ffmpeg.exe"))
        self.assertEqual(ffmpeg["hwaccels_listed"], ["cuda", "vaapi", "dxva2", "qsv", "d3d11va", "d3d12va", "amf"])
        listed = {name: entry["listed"] for name, entry in ffmpeg["encoders"].items()}
        self.assertEqual(listed, {"libx264": True, "h264_nvenc": True, "hevc_nvenc": True, "av1_nvenc": False,
                                  "h264_qsv": True, "hevc_qsv": False, "h264_amf": True, "hevc_amf": False})
        self.assertEqual({entry["trial"] for entry in ffmpeg["encoders"].values()}, {"not_run"})
        self.assertEqual(ffmpeg["nvdec"], {"listed": True, "trial": "not_run", "framemd5_equal_cpu": None,
                                           "chain": "logo_routing"})
        self.assertEqual(ffmpeg["nvdec_codecs"], {codec: {"trial": "not_run", "error": None}
                                                  for codec in ("h264", "hevc", "av1")})
        self.assertEqual(result.gate_key["ffmpeg"], {
            "version": "9.0.1", "sha256": "72a489eccd008c2ec2c0a5856c5c75bc3d8bbfa90166c4566865c246445e6aa3",
            "size": 102856192, "mtime_ns": 1786512472000000000})

    def test_only_the_listings_run_each_with_a_timeout(self):
        system = FakeSystem()
        run_detect(system)
        commands = system.commands("ffmpeg.exe")
        self.assertEqual([argv[1:] for argv in commands],
                         [["-hide_banner", "-version"], ["-hide_banner", "-hwaccels"], ["-hide_banner", "-encoders"]])
        self.assertTrue(all(timeout == hd.FFMPEG_TIMEOUT_SECONDS for argv, timeout, _env in system.calls
                            if argv in commands))

    def test_av1_encoder_is_listed_when_ffmpeg_lists_it(self):
        listings = {**LISTINGS, "-encoders": CommandResult(0, " V....D av1_nvenc   NVIDIA NVENC av1 encoder\n")}
        ffmpeg = run_detect(FakeSystem(listings=listings)).detection["ffmpeg"]
        self.assertTrue(ffmpeg["encoders"]["av1_nvenc"]["listed"])
        self.assertFalse(ffmpeg["encoders"]["libx264"]["listed"])

    def test_missing_ffmpeg_is_recorded_and_runs_nothing(self):
        system = FakeSystem(ffmpeg_present=False)
        result = run_detect(system)
        outcome, document = profile_of(result)
        self.assertEqual(outcome.result, "ok")
        self.assertFalse(document["detection"]["ffmpeg"]["ok"])
        self.assertIsNone(document["fingerprint"]["gate_key"]["ffmpeg"])
        self.assertEqual(system.commands("ffmpeg.exe"), [])
        self.assertFalse(any(entry["listed"] for entry in document["detection"]["ffmpeg"]["encoders"].values()))

    def test_a_failing_listing_stops_the_others_and_keeps_the_file_facts(self):
        listings = {**LISTINGS, "-hwaccels": CommandResult(None, timed_out=True)}
        system = FakeSystem(listings=listings)
        result = run_detect(system)
        _outcome, document = profile_of(result)
        self.assertEqual(document["detection"]["ffmpeg"]["error"], "ffmpeg -hwaccels: quá giờ")
        self.assertEqual(len(system.commands("ffmpeg.exe")), 2)
        self.assertEqual(document["fingerprint"]["gate_key"]["ffmpeg"]["size"], 102856192)

    def test_version_keeps_only_the_number(self):
        result = run_detect(FakeSystem())
        self.assertEqual(result.detection["ffmpeg"]["version"], "9.0.1")
        everything = json.dumps([result.gate_key, result.info, result.detection])
        self.assertNotIn("build.example", everything)
        self.assertNotIn("essentials", everything)

    def test_version_parsing(self):
        cases = {
            "ffmpeg version 9.0.1-essentials_build-build.example Copyright": "9.0.1",
            "ffmpeg version n7.1 Copyright (c) 2000-2024": "7.1",
            "ffmpeg version 7.1.1.2-full": None,
            "ffmpeg version N-12345-gabcdef-build.example": None,
            "ffmpeg version 2026-08-10-git-abcdef-full_build-build.example": None,
            "": None,
        }
        for line, expected in cases.items():
            with self.subTest(line):
                self.assertEqual(hd.ffmpeg_version(line + "\nconfiguration: --enable-gpl\n"), expected)


class SanitizingTests(unittest.TestCase):
    def test_error_texts_lose_local_paths_and_urls(self):
        crash = CommandResult(1, "", f"Traceback\nOSError: cannot load {FAKE_ROOT}\\.venv\\Lib\\torch\\lib\\c10.dll\n")
        nvml = NvmlRead("error", error="NVMLError: see https://docs.example/nvml for help")
        sha = OSError(13, "Permission denied", str(FFMPEG_DIR / "ffmpeg.exe"))
        result = run_detect(FakeSystem(probe=crash, nvml=nvml, sha_error=sha))
        torch_error = result.detection["torch_cuda"]["error"]
        self.assertTrue(torch_error.startswith("torch_probe_crashed: thoát mã 1: OSError: cannot load "
                                               "<venv>\\Lib\\torch\\lib\\c10.dll"))
        self.assertNotIn(str(FAKE_ROOT), torch_error)
        self.assertEqual(result.detection["nvml"]["error"], "NVMLError: see <url> for help")
        self.assertIn("<ffmpeg>", result.detection["ffmpeg"]["error"])
        self.assertNotIn(str(FFMPEG_DIR), result.detection["ffmpeg"]["error"])
        profile_of(result)

    def test_a_venv_outside_the_root_is_labelled(self):
        python = Path(r"D:\Example Install\.venv\Scripts\python.exe")
        crash = CommandResult(1, "", r"OSError: cannot load D:\Example Install\.venv\Lib\torch\lib\c10.dll" + "\n")
        result = hd.detect(FAKE_ROOT, ffmpeg_dir=FFMPEG_DIR, python_exe=python, system=FakeSystem(probe=crash))
        self.assertEqual(result.detection["torch_cuda"]["error"],
                         "torch_probe_crashed: thoát mã 1: OSError: cannot load <venv>\\Lib\\torch\\lib\\c10.dll")

    def test_a_non_ascii_path_in_the_child_stderr_is_still_labelled(self):
        python = Path("D:/Thư mục Đ/.venv/Scripts/python.exe")  # only a label source; never created
        library = python.parents[1] / "Lib" / "torch" / "lib" / "c10.dll"
        system = RealChildStderr(str(library))
        with without_runner_encoding():
            result = hd.detect(FAKE_ROOT, ffmpeg_dir=FFMPEG_DIR, python_exe=python, system=system)
        self.assertEqual(result.detection["torch_cuda"]["error"],
                         "torch_probe_crashed: thoát mã 1: OSError: cannot load <venv>\\Lib\\torch\\lib\\c10.dll")

    def test_relative_paths_and_drive_roots_are_never_labels(self):
        cases = {"relative": (Path("tools/ffmpeg/bin"), Path("python.exe")),
                 "drive root": (Path("C:/"), Path("C:/python.exe"))}
        for label, (ffmpeg_dir, python_exe) in cases.items():
            with self.subTest(label):
                pairs = hd.detect_replacements(FAKE_ROOT, ffmpeg_dir=ffmpeg_dir, python_exe=python_exe)
                self.assertTrue(all(Path(prefix).is_absolute() and len(Path(prefix).parts) > 1
                                    for prefix, _label in pairs), pairs)
                self.assertEqual(sanitize_message("ffmpeg 9.0.1 at C:\\x.y", pairs), "ffmpeg 9.0.1 at C:\\x.y")

    def test_error_texts_are_cut_at_300_characters(self):
        long = probe_line(ok=False, device_count=0, device_name=None, capability=None, arch_list=[],
                          total_memory_bytes=None, fp16_ok=None, error="cuda_not_available: " + "x" * 1500)
        result = run_detect(FakeSystem(probe=CommandResult(0, long)))
        self.assertEqual(len(result.detection["torch_cuda"]["error"]), 300)
        profile_of(result)


class TorchProbeParsingTests(unittest.TestCase):
    def test_canned_child_output_is_parsed(self):
        payload = hd.parse_torch_probe(CommandResult(0, probe_line() + "\n"))
        self.assertEqual(payload, probe_payload())
        self.assertEqual(set(payload), hd.PROBE_KEYS)

    def test_a_timeout_is_never_parsed_as_output(self):
        with self.assertRaises(hd.ProbeError):
            hd.parse_torch_probe(CommandResult(None, probe_line(), timed_out=True))


class ObserveTests(unittest.TestCase):
    class NoCommands(FakeSystem):
        def run(self, argv, *, timeout, env=None):
            raise AssertionError("observe() never runs a subprocess")

    def test_observe_reads_without_a_subprocess(self):
        system = self.NoCommands()
        observed = hd.observe(FAKE_ROOT, ffmpeg_exe=FFMPEG_DIR / "ffmpeg.exe", system=system)
        self.assertEqual(observed, Observation(
            nvml="ok", gpus=(RTX2060,), nvidia_driver="617.42", nvidia_pci_seen=True, torch_build="2.14.0+cu126",
            ffmpeg=FileStat(102856192, 1786512472000000000),
            cpu_name="Intel(R) Core(TM) i7-10750H CPU @ 2.60GHz"))
        self.assertEqual(system.stats, [FFMPEG_DIR / "ffmpeg.exe"])

    def test_observe_unknowns(self):
        cases = {
            "timeout": (AdapterRead(False, "timeout"), None),
            "unreadable": (AdapterRead(False, "OSError: x"), None),
            "intel only": (AdapterRead(True, None, (INTEL_IGPU,)), False),
            "basic display": (AdapterRead(True, None, (BASIC_DISPLAY,)), True),
        }
        for label, (adapters, seen) in cases.items():
            with self.subTest(label):
                system = self.NoCommands(nvml=NvmlRead("error", error="NVMLError: x"), adapters=adapters)
                observed = hd.observe(FAKE_ROOT, ffmpeg_exe=None, system=system)
                self.assertEqual((observed.nvml, observed.gpus, observed.nvidia_driver), ("error", (), None))
                self.assertIs(observed.nvidia_pci_seen, seen)
                self.assertIsNone(observed.ffmpeg)
                self.assertEqual(system.stats, [])

    def test_observe_imports_no_torch(self):
        code = (
            "import sys\n"
            "from pathlib import Path\n"
            "from biliflow import hardware_detect as hd\n"
            "from biliflow.hardware_schema import FileStat\n"
            "from biliflow.hardware_system import AdapterRead, CpuInfo, NvmlRead\n"
            "class Machine:\n"
            "    def run(self, *a, **k): raise AssertionError('no subprocess')\n"
            "    def nvml(self): return NvmlRead('no_library', error='none')\n"
            "    def adapters(self, *, timeout): return AdapterRead(True, None, ())\n"
            "    def torch_build(self): return '2.14.0+cu126'\n"
            "    def file_stat(self, path): return FileStat(1, 2)\n"
            "    def cpu(self): return CpuInfo('cpu', 'vendor', None, None)\n"
            "observed = hd.observe(Path('.'), ffmpeg_exe='ffmpeg.exe', system=Machine())\n"
            "print(observed.nvidia_pci_seen, 'torch' in sys.modules, 'pynvml' in sys.modules)\n"
        )
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=120,
                                check=True, env=env)
        self.assertEqual(result.stdout.split(), ["False", "False", "False"])


class DetectToResolveTests(TempRootCase):
    """End to end on fakes: detect() and classify_attempt build a profile under a temporary root; observe()
    on the same machine then feeds resolve() (sections 3.3, 3.5, 5.6 and 7.1)."""

    def measure_then_resolve(self, system: FakeSystem) -> hp.Resolution:
        result = hd.detect(self.root, ffmpeg_dir=FFMPEG_DIR, python_exe=PYTHON, system=system)
        outcome, document = profile_of(result)
        self.assertEqual((outcome.result, outcome.failure_code, outcome.had_cuda), ("ok", None, True))
        write_profile(self.root, document, last_good=True)
        commands = len(system.calls)
        observed = hd.observe(self.root, ffmpeg_exe=FFMPEG_DIR / "ffmpeg.exe", system=system)
        self.assertEqual(len(system.calls), commands)  # the light observation runs no command
        resolution = hp.resolve(self.root, observed=observed)
        self.assertEqual((resolution.source, resolution.profile.content_sha256),
                         ("profile", document["content_sha256"]))
        return resolution

    def test_rtx2060_machine_resolves_to_the_gated_turing_row(self):
        resolution = self.measure_then_resolve(FakeSystem())
        self.assertEqual((resolution.status, resolution.row_id, resolution.row_status),
                         (hp.READY, "nvidia-sm75-torch2.14.0+cu126", "gated"))
        self.assertEqual(resolution.warnings, ())  # same GPU, driver, torch, ffmpeg.exe and CPU as measured
        families = resolution.families
        self.assertEqual({name: (option.family, option.tf32) for name, option in families.items()},
                         {name: (name, "default") for name in hp.FAMILIES})
        self.assertEqual(families["standard"].knobs, hp.plain_knobs(1))
        # No NVDEC self-test before E2 and driver 617.42 is not the verified 576.80: routing decodes on the CPU.
        self.assertEqual(families["fast"].knobs, hp.prewarm_knobs("cpu"))
        self.assertIn("nvdec_unverified", [warning.code for warning in families["fast"].warnings])

    def test_rtx3060_machine_with_todays_torch_runs_untested_standard_with_tf32_off(self):
        system = FakeSystem(
            nvml=NvmlRead("ok", "617.42", (RTX3060,)), adapters=AdapterRead(True, None, (AMPERE_CARD, INTEL_IGPU)),
            smi=CommandResult(0, "NVIDIA GeForce RTX 3060, 12288, 617.42, 8.6\n"),
            probe=CommandResult(0, probe_line(device_name=RTX3060.name, capability="8.6",
                                              total_memory_bytes=12884901888)))
        resolution = self.measure_then_resolve(system)
        self.assertEqual((resolution.status, resolution.row_id, resolution.row_status),
                         (hp.READY_UNTESTED, "nvidia-untested", "untested"))
        self.assertEqual([(warning.code, warning.text) for warning in resolution.warnings],
                         [("untested_arch", "Chưa qua cổng trên đời card này (sm_86)"),
                          ("tf32_off", hp.WARNING_TEXTS["tf32_off"])])  # torch and driver are within the gated row
        self.assertFalse(resolution.untested_fast_applies)
        for requested, option in resolution.families.items():
            with self.subTest(requested=requested):
                self.assertEqual((option.family, option.capped, option.tf32),
                                 ("standard", requested != "standard", "off"))
                self.assertEqual(option.knobs, hp.plain_knobs(1, tf32="off"))
                self.assertEqual(option.knobs.device, "cuda")


if __name__ == "__main__":
    unittest.main()
