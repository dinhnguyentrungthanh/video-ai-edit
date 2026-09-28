"""Opt-in recognition-only batching; disabled in default scan_text runs.

EasyOCR's ordinary batch_size>1 pads every crop to a common maximum width.
These buckets preserve the per-crop width used by batch_size=1. GPU arithmetic
can still differ; output/quality equivalence must be benchmarked separately.
"""
from __future__ import annotations

from collections import defaultdict


MAX_BATCH_PIXELS = 8 * 64 * 512


def recognize_same_width(reader, grey, horizontal, free, *, batch_size=8,
                         image_list_fn=None, text_fn=None, image_height=None):
    if batch_size not in (1, 2, 4, 8):
        raise ValueError("Experimental OCR batch size must be 1, 2, 4 or 8")
    if reader.model_lang != "latin":
        raise ValueError("This experiment is validated only for the vi/en Latin recognizer")
    if image_list_fn is None or text_fn is None or image_height is None:
        from easyocr.config import imgH
        from easyocr.recognition import get_text
        from easyocr.utils import get_image_list
        image_list_fn = image_list_fn or get_image_list
        text_fn = text_fn or get_text
        image_height = image_height or imgH
    groups = defaultdict(list)
    count = 0
    for horizontal_box, free_box in ([(box, None) for box in horizontal]
                                     + [(None, box) for box in free]):
        crops, width = image_list_fn(
            [horizontal_box] if horizontal_box is not None else [],
            [free_box] if free_box is not None else [], grey, model_height=image_height,
        )
        for crop in crops:
            groups[int(width)].append((count, crop))
            count += 1
    result = [None] * count
    ignore_char = "".join(set(reader.character) - set(reader.lang_char))
    for width, indexed in groups.items():
        # Bound input tensor area as well as crop count; very wide crops stay
        # serial. This is not a bound on total CUDA memory/activations.
        effective_batch = min(batch_size, max(1, MAX_BATCH_PIXELS // (image_height * width)))
        for offset in range(0, len(indexed), effective_batch):
            group = indexed[offset:offset + effective_batch]
            predictions = text_fn(
                reader.character, image_height, width, reader.recognizer, reader.converter,
                [crop for _, crop in group], ignore_char, "greedy", 5, effective_batch,
                0.1, 0.5, 0.003, 0, reader.device,
            )
            if len(predictions) != len(group):
                raise RuntimeError("OCR batch returned a different number of crops")
            for (index, _), prediction in zip(group, predictions, strict=True):
                result[index] = prediction
    return result


class SameWidthReader:
    """Strict experimental adapter for the existing scanner's OCR call only."""

    def __init__(self, reader, batch_size=8):
        self.reader = reader
        self.batch_size = batch_size

    def readtext(self, image, **options):
        expected = dict(detail=1, paragraph=False, batch_size=1, workers=0, decoder="greedy")
        if options != expected:
            raise ValueError("Unvalidated OCR options; use the original reader")
        from easyocr.utils import reformat_input
        color, grey = reformat_input(image)
        horizontal, free = self.reader.detect(color, reformat=False)
        return recognize_same_width(self.reader, grey, horizontal[0], free[0], batch_size=self.batch_size)
