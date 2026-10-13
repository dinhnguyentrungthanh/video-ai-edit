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
    """Return a reader using ``precision`` for detection; the caller's reader is untouched.

    The fp16 (fast scan) reader also builds the CRAFT input on the GPU
    (``ocr_detect_gpu``; bit-identical, measured only on this path). The fp32
    path stays EasyOCR's own.
    """
    import copy
    if validate_detect_precision(precision) == "fp32":
        return reader
    if not str(getattr(reader, "device", "")).startswith("cuda"):
        raise ValueError("fp16 text detection requires a CUDA OCR reader")
    variant = copy.copy(reader)  # shares the loaded models; only .detector and .get_textbox differ
    variant.detector = Float16Detector(reader.detector)
    original_textbox = getattr(reader, "get_textbox", None)
    if original_textbox is not None:
        from easyocr.detection import get_textbox as craft_get_textbox
        if original_textbox is craft_get_textbox:  # CRAFT only; other detectors keep their own
            from biliflow.ocr_detect_gpu import gpu_textbox
            variant.get_textbox = gpu_textbox(original_textbox)
    return variant
