"""Profiles, observations and temporary roots for the hardware-profile tests.

Every document is synthetic: example paths, no film names, no real domain. Roots live
under <install>/temp (a test root for hardware_resolve) and are always removed.
"""
from __future__ import annotations

import copy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from biliflow import recycle_bin
from biliflow.hardware_schema import (
    NVDEC_CODECS, FileStat, GpuInfo, Observation, UntestedFast, build_profile, canonical_sha256, profile_path,
    write_json_atomic, write_override, override_path,
)

TEMP_PARENT = recycle_bin.INSTALL_ROOT / "temp"
TORCH = "2.14.0+cu126"
ARCH_LIST = ("sm_50", "sm_60", "sm_61", "sm_70", "sm_75", "sm_80", "sm_86", "sm_90")
TURING = GpuInfo("NVIDIA GeForce RTX 2060", "7.5", 6144)
AMPERE = GpuInfo("NVIDIA GeForce RTX 3060", "8.6", 12288)
ADA = GpuInfo("NVIDIA GeForce RTX 4070", "8.9", 12282)
BLACKWELL = GpuInfo("NVIDIA GeForce RTX 5070", "12.0", 12227)
SMALL_AMPERE = GpuInfo("NVIDIA GeForce RTX 3050 Laptop GPU", "8.6", 4096)
FFMPEG_SHA = "72a489eccd008c2ec2c0a5856c5c75bc3d8bbfa90166c4566865c246445e6aa3"
FFMPEG_SIZE = 102856192
FFMPEG_MTIME = 1786512472000000000
CPU_MODEL = "Example CPU 6C/12T @ 2.60GHz"
WHEN = "2026-10-11T10:00:00+07:00"
NVIDIA_ADAPTER = {"vendor_id": "10de", "vendor": "nvidia", "device_id": "1f15", "name": "NVIDIA GeForce RTX 2060",
                  "driver_version": "32.0.16.1742"}
BASIC_DISPLAY = {"vendor_id": "10de", "vendor": "nvidia", "device_id": "1f15",
                 "name": "Microsoft Basic Display Adapter", "driver_version": None}
INTEL_ADAPTER = {"vendor_id": "8086", "vendor": "intel", "device_id": "9bc4", "name": "Intel(R) UHD Graphics",
                 "driver_version": "26.20.100.7642"}


def gpu_entry(gpu: GpuInfo) -> dict[str, Any]:
    return {"name": gpu.name, "compute_capability": gpu.compute_capability, "vram_mib": gpu.vram_mib}


def ffmpeg_detection(*, ok: bool = True, nvdec_trial: str = "ok", framemd5: bool | None = True) -> dict[str, Any]:
    present = {"path": "C:/example/tools/ffmpeg/bin/ffmpeg.exe", "version": "9.0.1", "sha256": FFMPEG_SHA,
               "size": FFMPEG_SIZE, "mtime_ns": FFMPEG_MTIME}
    return {
        "ok": ok, "error": None if ok else "ffmpeg.exe không chạy được",
        **(present if ok else {key: None for key in present}),
        "hwaccels_listed": ["cuda", "dxva2"] if ok else [],
        "encoders": {"libx264": {"listed": True, "trial": "ok", "error": None}} if ok else {},
        "nvdec": {"listed": ok, "trial": nvdec_trial if ok else "not_run",
                  "framemd5_equal_cpu": framemd5 if ok else None, "chain": "logo_routing"},
        "nvdec_codecs": {codec: {"trial": "ok" if ok else "not_run", "error": None} for codec in NVDEC_CODECS},
    }


def profile_document(kind: str = "cuda", *, gpus: tuple[GpuInfo, ...] | None = None, driver: str | None = "617.42",
                     torch: str | None = TORCH, arch_list: tuple[str, ...] = ARCH_LIST, ffmpeg_ok: bool = True,
                     nvdec_trial: str = "ok", framemd5: bool | None = True,
                     adapters: dict[str, Any] | None = None) -> dict[str, Any]:
    """A valid profile: ``cuda`` (one NVIDIA GPU), ``cpu`` (never had CUDA) or ``failed`` (driver gone)."""
    cuda = kind == "cuda"
    gpus = (TURING,) if gpus is None and cuda else (gpus or ())
    nvml = "ok" if cuda else "no_library"
    items = [NVIDIA_ADAPTER, INTEL_ADAPTER] if cuda else [BASIC_DISPLAY] if kind == "failed" else [INTEL_ADAPTER]
    detection = {
        "nvml": {"status": nvml, "error": None},
        "nvidia_smi": None,
        "torch_cuda": {
            "ok": cuda, "device_count": len(gpus) if cuda else 0,
            "device_name": gpus[0].name if cuda else None,
            "capability": gpus[0].compute_capability if cuda else None,
            "arch_list": list(arch_list) if cuda else [],
            "total_memory_bytes": gpus[0].vram_mib * 1048576 if cuda else None,
            "fp16_ok": True if cuda else None, "seconds": 2.5, "error": None if cuda else "cuda_not_available",
        },
        "ffmpeg": ffmpeg_detection(ok=ffmpeg_ok, nvdec_trial=nvdec_trial if cuda else "not_run",
                                   framemd5=framemd5 if cuda else None),
        "cpu": {"model": CPU_MODEL, "vendor": "GenuineIntel", "physical_cores": 6, "logical_cores": 12,
                "features": {"avx2": True, "avx512f": False}},
        "ram_bytes": 16 * 2 ** 30, "disk": {"root_total_bytes": 10 ** 12, "root_free_bytes": 5 * 10 ** 11},
        "power": {"battery_present": True, "ac_online": True},
        "adapters": adapters or {"ok": True, "error": None, "items": items},
    }
    gate_ffmpeg = {"version": "9.0.1", "sha256": FFMPEG_SHA, "size": FFMPEG_SIZE, "mtime_ns": FFMPEG_MTIME}
    gate_key = {"nvidia_gpus": [gpu_entry(gpu) for gpu in gpus], "nvidia_driver": driver if cuda else None,
                "torch_build": torch, "ffmpeg": gate_ffmpeg if ffmpeg_ok else None}
    failure = {"code": "cuda_unavailable", "step": "torch_probe", "message": "cuda_not_available"}
    had_cuda = kind != "cpu"
    return build_profile(
        written_at=WHEN, writer={"biliflow_version": "0.7.24", "mode": "measure", "launched_by": "script"},
        attempt={"result": "failed" if kind == "failed" else "ok", "failure": failure if kind == "failed" else None,
                 "started_at": WHEN, "finished_at": WHEN, "budget_seconds": 600},
        gate_key=gate_key, info={"os": "Windows 10.0.26300", "python": "3.11.16", "torch_cuda": "12.6",
                                 "cudnn": "91002"},
        detection=detection, measurements=[],
        history={"had_cuda": had_cuda, "first_cuda_at": WHEN if had_cuda else None})


def rehash(document: dict[str, Any]) -> dict[str, Any]:
    """A copy with both hashes recomputed, so one broken field is the only fault."""
    copied = copy.deepcopy(document)
    copied["fingerprint"]["sha256"] = canonical_sha256(copied["fingerprint"]["gate_key"])
    body = {key: value for key, value in copied.items() if key != "content_sha256"}
    copied["content_sha256"] = canonical_sha256(body)
    return copied


def cpu_with_had_cuda() -> dict[str, Any]:
    """A CPU-only 'ok' profile that claims CUDA history: broken by the cross-check of section 3.4."""
    document = copy.deepcopy(profile_document("cpu"))
    document["history"] = {"had_cuda": True, "first_cuda_at": WHEN}
    return rehash(document)


def failed_on_card(gpu: GpuInfo, *, driver: str = "617.42", torch: str = TORCH,
                   arch_list: tuple[str, ...] = ARCH_LIST, error: str | None = None,
                   seen_by_torch: bool = True) -> dict[str, Any]:
    """A failed first measurement where NVML saw ``gpu`` but torch could not use it.

    ``seen_by_torch``: torch reported the card (capability, arch_list) and the probe matmul
    failed, as for a card the installed torch has no kernel for; otherwise torch said CUDA is
    not available (an old driver), with no device facts.
    """
    document = copy.deepcopy(profile_document("cuda", gpus=(gpu,), driver=driver, torch=torch,
                                              arch_list=arch_list))
    facts = {"device_count": 1, "device_name": gpu.name, "capability": gpu.compute_capability,
             "arch_list": list(arch_list), "total_memory_bytes": gpu.vram_mib * 1048576} if seen_by_torch else {
        "device_count": 0, "device_name": None, "capability": None, "arch_list": [], "total_memory_bytes": None}
    default_error = ("cuda_matmul_failed: RuntimeError: CUDA error: no kernel image is available"
                     if seen_by_torch else "cuda_not_available: driver too old")
    document["detection"]["torch_cuda"].update(ok=False, fp16_ok=None, error=error or default_error, **facts)
    document["attempt"].update(result="failed", failure={"code": "cuda_unavailable", "step": "torch_probe",
                                                         "message": error or default_error})
    document["history"] = {"had_cuda": False, "first_cuda_at": None}
    return rehash(document)


def observation(gpus: tuple[GpuInfo, ...] = (TURING,), *, nvml: str = "ok", driver: str | None = "617.42",
                pci_seen: bool | None = True, torch: str | None = TORCH,
                ffmpeg: FileStat | None = FileStat(FFMPEG_SIZE, FFMPEG_MTIME),
                cpu: str | None = CPU_MODEL) -> Observation:
    return Observation(nvml, tuple(gpus), driver, pci_seen, torch, ffmpeg, cpu)


CPU_OBSERVATION = observation((), nvml="no_library", driver=None, pci_seen=False)


class TempRootCase(unittest.TestCase):
    """A fresh project root under <install>/temp for each test, removed afterwards."""

    def setUp(self) -> None:
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        temp = TemporaryDirectory(dir=TEMP_PARENT)
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.new_root("root")

    def new_root(self, name: str) -> Path:
        root = self.base / name
        (root / "state").mkdir(parents=True)
        return root

    def write(self, which: str, document: Any, root: Path | None = None) -> None:
        """Write a profile as is (no validation), so a broken one can be stored too."""
        target = profile_path(root or self.root, which)
        if isinstance(document, bytes):
            target.write_bytes(document)
        else:
            write_json_atomic(target, document)

    def switch(self, *, cpu_only: bool = False, untested_fast: UntestedFast | None = None,
               root: Path | None = None) -> None:
        from datetime import datetime, timezone
        write_override(root or self.root, cpu_only=cpu_only, untested_fast=untested_fast,
                       now=datetime(2026, 10, 11, 3, 0, tzinfo=timezone.utc))

    def broken_switch(self, root: Path | None = None) -> None:
        override_path(root or self.root).write_bytes(b"{not json")
