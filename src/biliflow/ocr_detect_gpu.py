"""CRAFT detection input built on the GPU for EasyOCR (fast scan only).

EasyOCR prepares every frame on the CPU (``resize_aspect_ratio`` then
``normalizeMeanVariance``: a float32 canvas padded to a multiple of 32, minus
the mean, divided by the variance) and only then copies it to the GPU. Here the
uint8 frame is copied and the same arithmetic runs on the GPU. The result is
bit-identical: a same-size resize is a copy, so only frames that need no resize
take this path; the subtraction and division run in float64 and are rounded to
float32, and since float64 carries more than 2 * 24 + 2 bits that double
rounding equals numpy's correctly rounded float32 result. Every other case
(CPU, a frame that is not uint8 RGB, a resize, EasyOCR's character estimate)
calls EasyOCR's own ``get_textbox`` unchanged.

Measured on two films (docs/handoff/B-scan.md, "Đợt OCR"): identical
text-scan reports and per-frame boxes, stage text 11-14 % faster.
"""
from __future__ import annotations

import numpy as np

MEAN = (0.485, 0.456, 0.406)
VARIANCE = (0.229, 0.224, 0.225)


def _is_cuda(device) -> bool:
    return str(device).startswith("cuda")


def _padded(size: int) -> int:
    return size + (32 - size % 32) % 32


def _unresized_ratio(image, canvas_size, mag_ratio) -> float | None:
    """EasyOCR's resize ratio when the frame keeps its size, else None."""
    if (not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.ndim != 3
            or image.shape[2] != 3 or image.size == 0):
        return None
    height, width, _ = image.shape
    target_size = min(mag_ratio * max(height, width), canvas_size)  # as resize_aspect_ratio
    ratio = target_size / max(height, width)
    if (int(height * ratio), int(width * ratio)) != (height, width):
        return None
    return ratio


def craft_input(image: np.ndarray, device):
    """The 1x3xH32xW32 float32 tensor EasyOCR would build from a uint8 RGB frame that keeps its size."""
    import torch
    height, width, _ = image.shape
    # Frames from the FFmpeg pipe are read-only; torch needs a writable buffer.
    frame = torch.from_numpy(np.require(image, dtype=np.uint8, requirements=("C", "W"))).to(device)
    canvas = torch.zeros((_padded(height), _padded(width), 3), dtype=torch.float64, device=device)
    canvas[:height, :width] = frame
    mean = torch.tensor(np.array([value * 255.0 for value in MEAN], dtype=np.float32),
                        dtype=torch.float64, device=device)
    variance = torch.tensor(np.array([value * 255.0 for value in VARIANCE], dtype=np.float32),
                            dtype=torch.float64, device=device)
    shifted = (canvas - mean).to(torch.float32).to(torch.float64)
    normalized = (shifted / variance).to(torch.float32)
    return normalized.permute(2, 0, 1).unsqueeze(0).contiguous()


def _detect(net, image, ratio, text_threshold, link_threshold, low_text, poly, device):
    """EasyOCR's ``test_net`` for one unresized frame, with the input built on ``device``."""
    import torch
    from easyocr import detection
    x = craft_input(image, device)
    ratio_h = ratio_w = 1 / ratio
    with torch.no_grad():
        y, _feature = net(x)
    polys_list = []
    for out in y:
        score_text = out[:, :, 0].cpu().data.numpy()
        score_link = out[:, :, 1].cpu().data.numpy()
        boxes, polys, _mapper = detection.getDetBoxes(
            score_text, score_link, text_threshold, link_threshold, low_text, poly, False)
        boxes = detection.adjustResultCoordinates(boxes, ratio_w, ratio_h)
        polys = detection.adjustResultCoordinates(polys, ratio_w, ratio_h)
        for k in range(len(polys)):
            if polys[k] is None:
                polys[k] = boxes[k]
        polys_list.append(polys)
    return polys_list


def gpu_textbox(original):
    """Wrap EasyOCR's CRAFT ``get_textbox``; anything this path does not cover calls ``original``."""
    def get_textbox(detector, image, canvas_size, mag_ratio, text_threshold, link_threshold, low_text,
                    poly, device, optimal_num_chars=None, **kwargs):
        ratio = None
        if optimal_num_chars is None and _is_cuda(device):
            ratio = _unresized_ratio(image, canvas_size, mag_ratio)
        if ratio is None:
            return original(detector, image, canvas_size=canvas_size, mag_ratio=mag_ratio,
                            text_threshold=text_threshold, link_threshold=link_threshold,
                            low_text=low_text, poly=poly, device=device,
                            optimal_num_chars=optimal_num_chars, **kwargs)
        polys_list = _detect(detector, image, ratio, text_threshold, link_threshold, low_text, poly, device)
        return [[np.array(box).astype(np.int32).reshape((-1)) for box in polys] for polys in polys_list]
    get_textbox.original = original
    return get_textbox
