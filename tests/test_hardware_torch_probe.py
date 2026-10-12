"""E1 torch probe, child side: run_torch_probe, _matmul_check and main() with fake torch modules.

The real child is never run here: it imports torch and makes a CUDA context (HARDWARE_PROFILE.md
section 9). The parent side (the child's argv and environment, parse_torch_probe) is tested in
test_hardware_detect.
"""
from __future__ import annotations

import io
import json
import math
import sys
import unittest
import warnings
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from biliflow import hardware_detect as hd
from biliflow.hardware_system import CommandResult
from tests.hardware_fixtures import ARCH_LIST as FIXTURE_ARCH_LIST

ARCH_LIST = list(FIXTURE_ARCH_LIST)
CUDA_ERROR = RuntimeError("CUDA error: unknown error\n"
                          "Compile with TORCH_USE_CUDA_DSA to enable device-side assertions.\n")


class FakeCuda:
    """torch.cuda of an RTX 3060. ``noise`` is printed as torch may print; ``properties_error`` is raised."""

    def __init__(self, available=True, warning=None, init_warning=None, noise=None, properties_error=None):
        self.available, self.warning, self.init_warning = available, warning, init_warning
        self.noise, self.properties_error = noise, properties_error

    def is_available(self):
        if self.noise:
            print(self.noise)
        if self.warning:
            warnings.warn(self.warning, UserWarning)
        return self.available

    def get_device_capability(self, index):
        if self.init_warning:  # torch warns about an unsupported card when CUDA first initializes
            warnings.warn(self.init_warning, UserWarning)
        return 8, 6

    def device_count(self):
        return 1

    def get_device_name(self, index):
        return "NVIDIA GeForce RTX 3060"

    def get_arch_list(self):
        return list(ARCH_LIST)

    def get_device_properties(self, index):
        if self.properties_error is not None:
            raise self.properties_error
        return SimpleNamespace(total_memory=12884901888)


def fake_torch(**cuda) -> SimpleNamespace:
    cudnn = SimpleNamespace(version=lambda: 91002)
    return SimpleNamespace(version=SimpleNamespace(cuda="12.6"), backends=SimpleNamespace(cudnn=cudnn),
                           cuda=FakeCuda(**cuda))


class TorchProbeChildTests(unittest.TestCase):
    """The child's logic with a fake torch module; the real child is never run (it makes a CUDA context)."""

    def probe(self, torch, matmul=(True, True)):
        side = matmul if isinstance(matmul, BaseException) else None
        with patch.object(hd, "_matmul_check", side_effect=side, return_value=matmul):
            payload = hd.run_torch_probe(torch, started=10.0, clock=lambda: 12.25)
        parsed = hd.parse_torch_probe(CommandResult(0, json.dumps(payload) + "\n"))  # the parent accepts it
        self.assertEqual(parsed, payload)
        return payload

    def test_cuda_not_available_explains_with_the_warning(self):
        payload = self.probe(fake_torch(available=False, warning="Found no NVIDIA driver on your system."))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "cuda_not_available: Found no NVIDIA driver on your system.")
        self.assertEqual((payload["torch_cuda"], payload["cudnn"], payload["seconds"]), ("12.6", "91002", 2.25))

    def test_cuda_not_available_without_a_warning(self):
        self.assertEqual(self.probe(fake_torch(available=False))["error"], "cuda_not_available")

    def test_working_cuda_reports_the_device(self):
        payload = self.probe(fake_torch())
        self.assertTrue(payload["ok"])
        self.assertEqual((payload["capability"], payload["device_count"], payload["arch_list"]),
                         ("8.6", 1, ARCH_LIST))
        self.assertIsNone(payload["error"])

    def test_unreadable_device_facts_are_a_cuda_error_not_a_crash(self):
        payload = self.probe(fake_torch(properties_error=CUDA_ERROR))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "cuda_error: RuntimeError: CUDA error: unknown error")
        self.assertEqual((payload["device_count"], payload["capability"], payload["arch_list"]), (0, None, []))

    def test_failing_matmul_keeps_the_device_facts_and_torch_explanation(self):
        error = RuntimeError("CUDA error: no kernel image is available for execution on the device\n"
                             "CUDA kernel errors might be asynchronously reported at some other API call.\n")
        torch = fake_torch(init_warning="Card with CUDA capability sm_86 is not compatible with this install.")
        payload = self.probe(torch, matmul=error)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "cuda_matmul_failed: RuntimeError: CUDA error: no kernel image is "
                                           "available for execution on the device: Card with CUDA capability "
                                           "sm_86 is not compatible with this install.")
        self.assertEqual((payload["capability"], payload["arch_list"]), ("8.6", ARCH_LIST))

    def test_a_working_card_ignores_warnings(self):
        payload = self.probe(fake_torch(init_warning="some deprecation"))
        self.assertEqual((payload["ok"], payload["error"]), (True, None))

    def test_wrong_fp32_result_is_a_mismatch(self):
        payload = self.probe(fake_torch(), matmul=(False, True))
        self.assertEqual((payload["ok"], payload["error"]), (False, "cuda_matmul_mismatch"))


class TorchProbeMainTests(unittest.TestCase):
    """main() is the child's entry point; ``import torch`` gets the fake from sys.modules."""

    def test_main_refuses_other_arguments_silently(self):
        with patch("sys.stdout") as stdout:
            self.assertEqual(hd.main(["probe"]), 64)
            self.assertEqual(hd.main([]), 64)
        stdout.write.assert_not_called()

    def test_torch_probe_writes_one_json_line_and_keeps_torch_output_on_stderr(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(sys.modules, {"torch": fake_torch(noise="CUDA ready: example banner")}), \
                patch.object(hd, "_matmul_check", return_value=(True, True)), \
                patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            code = hd.main(["torch-probe"])
        self.assertEqual(code, 0)
        self.assertEqual(len(stdout.getvalue().splitlines()), 1)
        payload = hd.parse_torch_probe(CommandResult(0, stdout.getvalue()))
        self.assertEqual((payload["ok"], payload["capability"], payload["error"]), (True, "8.6", None))
        self.assertIn("CUDA ready: example banner", stderr.getvalue())
        self.assertNotIn("example banner", stdout.getvalue())


class FakeTensorTorch:
    """torch for _matmul_check on Python floats: no torch import, no CUDA context.

    A matmul on "cuda" adds ``fp32_offset`` to an fp32 result; ``fp16_first``, when set, replaces
    the first value of an fp16 result.
    """

    def __init__(self, *, fp32_offset: float = 0.0, fp16_first: float | None = None):
        self.fp32_offset, self.fp16_first = fp32_offset, fp16_first
        self.synchronized = 0
        self.cuda = SimpleNamespace(synchronize=self._synchronize)

    def _synchronize(self) -> None:
        self.synchronized += 1

    def on_device(self, values: list[float], device: str, dtype: str) -> tuple[float, ...]:
        if device != "cuda":
            return tuple(values)
        if dtype == "float16":
            return tuple(values) if self.fp16_first is None else (self.fp16_first, *values[1:])
        return tuple(value + self.fp32_offset for value in values)

    def linspace(self, start: float, end: float, steps: int, *, device: str) -> FakeTensor:
        step = (end - start) / (steps - 1)
        return FakeTensor(self, tuple(start + index * step for index in range(steps)), (steps,), device)

    def allclose(self, actual: FakeTensor, expected: FakeTensor, *, rtol: float, atol: float) -> bool:
        assert actual.device == expected.device, "torch compares tensors on one device"
        pairs = zip(actual.values, expected.values, strict=True)
        return all(abs(value - wanted) <= atol + rtol * abs(wanted) for value, wanted in pairs)

    def isfinite(self, tensor: FakeTensor) -> FakeTensor:
        return replace(tensor, values=tuple(math.isfinite(value) for value in tensor.values), dtype="bool")


@dataclass(frozen=True)
class FakeTensor:
    """A row-major tensor of Python values with only what _matmul_check uses; device and dtype are tracked."""
    torch: FakeTensorTorch
    values: tuple[Any, ...]
    shape: tuple[int, ...]
    device: str
    dtype: str = "float32"

    def reshape(self, rows: int, columns: int) -> FakeTensor:
        assert rows * columns == len(self.values)
        return replace(self, shape=(rows, columns))

    def __matmul__(self, other: FakeTensor) -> FakeTensor:
        assert (self.device, self.dtype) == (other.device, other.dtype), "torch never mixes them in a matmul"
        (rows, inner), (other_inner, columns) = self.shape, other.shape
        assert inner == other_inner
        left = [self.values[row * inner:(row + 1) * inner] for row in range(rows)]
        right = [other.values[column::columns] for column in range(columns)]
        product = [sum(a * b for a, b in zip(line, column)) for line in left for column in right]
        return replace(self, values=self.torch.on_device(product, self.device, self.dtype), shape=(rows, columns))

    def half(self) -> FakeTensor:
        return replace(self, dtype="float16")

    def float(self) -> FakeTensor:
        return replace(self, dtype="float32")

    def cpu(self) -> FakeTensor:
        return replace(self, device="cpu")

    def all(self) -> SimpleNamespace:
        return SimpleNamespace(item=lambda: all(self.values))


class MatmulCheckTests(unittest.TestCase):
    """_matmul_check's verdict (fp32 equals the CPU, fp16 is finite and close to fp32), in that order."""

    def test_verdicts(self):
        cases = {
            "equal results": ({}, (True, True)),
            "fp32 off by 1e-5 is within the tolerance": ({"fp32_offset": 1e-5}, (True, True)),
            "fp32 off by 1e-2": ({"fp32_offset": 1e-2}, (False, True)),
            "fp16 not finite": ({"fp16_first": math.nan}, (True, False)),
            "fp16 finite but far from fp32": ({"fp16_first": 1000.0}, (True, False)),
        }
        for label, (faults, expected) in cases.items():
            with self.subTest(label):
                torch = FakeTensorTorch(**faults)
                self.assertEqual(hd._matmul_check(torch), expected)
                self.assertEqual(torch.synchronized, 1)  # the GPU work is finished before it is compared


if __name__ == "__main__":
    unittest.main()
