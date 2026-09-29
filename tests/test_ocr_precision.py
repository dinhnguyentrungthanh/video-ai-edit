"""Opt-in float16 CRAFT detection wrapper; default fp32 path is untouched."""
import contextlib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from biliflow.ocr_precision import Float16Detector, validate_detect_precision, with_detect_precision


class Half:
    def __init__(self, name):
        self.name, self.cast = name, False

    def float(self):
        self.cast = True
        return self


class OcrPrecisionTests(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(validate_detect_precision("fp16"), "fp16")
        for value in ("fp8", None, 16, "FP16"):
            with self.assertRaises(ValueError):
                validate_detect_precision(value)

    def test_fp32_returns_same_reader_and_fp16_leaves_caller_untouched(self):
        net = object()
        reader = SimpleNamespace(device="cuda", detector=net, recognizer="rec")
        self.assertIs(with_detect_precision(reader, "fp32"), reader)
        variant = with_detect_precision(reader, "fp16")
        self.assertIsNot(variant, reader)
        self.assertIs(reader.detector, net)
        self.assertIsInstance(variant.detector, Float16Detector)
        self.assertIs(variant.detector.net, net)
        self.assertEqual(variant.recognizer, "rec")

    def test_fp16_requires_cuda_reader(self):
        with self.assertRaisesRegex(ValueError, "CUDA"):
            with_detect_precision(SimpleNamespace(device="cpu", detector=object()), "fp16")

    def test_wrapper_runs_under_autocast_and_returns_float32(self):
        entered = []
        outputs = (Half("y"), Half("feature"))
        net = mock.Mock(return_value=outputs, training=False)

        @contextlib.contextmanager
        def fake_autocast(**kwargs):
            entered.append(kwargs)
            yield
        with mock.patch("torch.autocast", fake_autocast):
            y, feature = Float16Detector(net)("x")
        net.assert_called_once_with("x")
        self.assertEqual(entered[0]["device_type"], "cuda")
        self.assertTrue(y.cast and feature.cast)
        self.assertFalse(Float16Detector(net).training)

    def test_scan_text_rejects_fp16_without_cuda_and_cli_default_is_fp32(self):
        from biliflow.cli import build_parser
        from biliflow.textscan import scan_text
        with self.assertRaisesRegex(ValueError, "requires CUDA"):
            scan_text(project_root=Path("."), input_path=Path("missing.mp4"), report_dir=Path("."),
                      model_dir=Path("."), ffmpeg_path=Path("."), ffprobe_path=Path("."),
                      device_name="cpu", detect_precision="fp16")
        args = ["scan-text", "--input", "m.mp4", "--report-dir", "reports/x"]
        self.assertEqual(build_parser().parse_args(args).detect_precision, "fp32")


if __name__ == "__main__":
    unittest.main()
