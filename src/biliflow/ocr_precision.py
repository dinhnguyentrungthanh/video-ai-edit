"""Optional float16 autocast for EasyOCR's CRAFT text detector (opt-in).

Only the detector forward pass runs under ``torch.autocast(float16)``; its score
and link maps are returned as float32 so EasyOCR's OpenCV post-processing and
all thresholds are unchanged. Recognition stays float32. This is NOT
bit-identical to float32: on 150 frames 147 matched exactly and three showed
2 px box shifts or a dropped sub-threshold box (docs/CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md
§15). Full-film review items were identical on Troy and Conan Movie 20 (all
detector groups, §16), so "Tăng tốc xử lý" (fast_scan) uses it; the standard
path stays float32.
"""
from __future__ import annotations

DETECT_PRECISIONS = ("fp32", "fp16")


def validate_detect_precision(value: object) -> str:
    if value not in DETECT_PRECISIONS:
        raise ValueError("detect precision must be 'fp32' or 'fp16'")
    return str(value)


class Float16Detector:
    """Callable wrapper that EasyOCR's ``get_textbox`` uses like the module."""

    def __init__(self, net):
        self.net = net

    def __call__(self, x):
        import torch
        with torch.autocast(device_type="cuda", dtype=torch.float16):
            y, feature = self.net(x)
        return y.float(), feature.float()

    def __getattr__(self, name):
        return getattr(self.net, name)


def with_detect_precision(reader, precision: str):
    """Return a reader using ``precision`` for detection; the caller's reader is untouched."""
    import copy
    if validate_detect_precision(precision) == "fp32":
        return reader
    if not str(getattr(reader, "device", "")).startswith("cuda"):
        raise ValueError("fp16 text detection requires a CUDA OCR reader")
    variant = copy.copy(reader)  # shares the loaded models; only .detector differs
    variant.detector = Float16Detector(reader.detector)
    return variant
