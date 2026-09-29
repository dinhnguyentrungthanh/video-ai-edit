"""Opt-in recognition-only batching; disabled in default scan_text runs.

EasyOCR's ordinary batch_size>1 pads every crop to a common maximum width.
These buckets preserve the per-crop width used by batch_size=1. GPU arithmetic
can still differ; output/quality equivalence must be benchmarked separately.

CrossFrameReader extends the same exact-width buckets across a small bounded
window of consecutive frames. Detection still runs once per frame with the
serial API and parameters; only recognition calls are shared.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field


MAX_BATCH_PIXELS = 8 * 64 * 512
DEFAULT_CROSS_FRAME_BYTE_BUDGET = 32 * 1024 * 1024
VALIDATED_READTEXT_OPTIONS = dict(detail=1, paragraph=False, batch_size=1, workers=0, decoder="greedy")


def _validate(reader, batch_size):
    if batch_size not in (1, 2, 4, 8):
        raise ValueError("Experimental OCR batch size must be 1, 2, 4 or 8")
    if reader.model_lang != "latin":
        raise ValueError("This experiment is validated only for the vi/en Latin recognizer")


def _easyocr_functions(image_list_fn=None, text_fn=None, image_height=None):
    if image_list_fn is None or text_fn is None or image_height is None:
        from easyocr.config import imgH
        from easyocr.recognition import get_text
        from easyocr.utils import get_image_list
        image_list_fn = image_list_fn or get_image_list
        text_fn = text_fn or get_text
        image_height = image_height or imgH
    return image_list_fn, text_fn, image_height


def prepare_same_width_crops(grey, horizontal, free, *, image_list_fn, image_height):
    """Crop each box exactly as serial recognize() does; returns [(width, crop)]."""
    prepared = []
    for horizontal_box, free_box in ([(box, None) for box in horizontal]
                                     + [(None, box) for box in free]):
        crops, width = image_list_fn(
            [horizontal_box] if horizontal_box is not None else [],
            [free_box] if free_box is not None else [], grey, model_height=image_height,
        )
        prepared.extend((int(width), crop) for crop in crops)
    return prepared


def recognize_prepared(reader, frames, *, batch_size, text_fn, image_height):
    """Recognize [[(width, crop)], ...] and return predictions per frame, in crop order.

    Crops from different frames share a call only when their padded width is
    identical, so every crop sees the same input tensor as serial EasyOCR.
    """
    groups = defaultdict(list)
    results = []
    for frame_index, crops in enumerate(frames):
        results.append([None] * len(crops))
        for crop_index, (width, crop) in enumerate(crops):
            groups[width].append((frame_index, crop_index, crop))
    ignore_char = "".join(set(reader.character) - set(reader.lang_char))
    for width, indexed in groups.items():
        # Bound input tensor area as well as crop count; very wide crops stay
        # serial. This is not a bound on total CUDA memory/activations.
        effective_batch = min(batch_size, max(1, MAX_BATCH_PIXELS // (image_height * width)))
        for offset in range(0, len(indexed), effective_batch):
            group = indexed[offset:offset + effective_batch]
            predictions = text_fn(
                reader.character, image_height, width, reader.recognizer, reader.converter,
                [crop for _, _, crop in group], ignore_char, "greedy", 5, effective_batch,
                0.1, 0.5, 0.003, 0, reader.device,
            )
            if len(predictions) != len(group):
                raise RuntimeError("OCR batch returned a different number of crops")
            for (frame_index, crop_index, _), prediction in zip(group, predictions, strict=True):
                results[frame_index][crop_index] = prediction
    for frame_result in results:
        if any(prediction is None for prediction in frame_result):
            raise RuntimeError("OCR batch left a crop without a prediction")
    return results


def recognize_same_width(reader, grey, horizontal, free, *, batch_size=8,
                         image_list_fn=None, text_fn=None, image_height=None):
    _validate(reader, batch_size)
    image_list_fn, text_fn, image_height = _easyocr_functions(image_list_fn, text_fn, image_height)
    crops = prepare_same_width_crops(grey, horizontal, free,
                                     image_list_fn=image_list_fn, image_height=image_height)
    return recognize_prepared(reader, [crops], batch_size=batch_size,
                              text_fn=text_fn, image_height=image_height)[0]


class SameWidthReader:
    """Strict experimental adapter for the existing scanner's OCR call only."""

    def __init__(self, reader, batch_size=8):
        self.reader = reader
        self.batch_size = batch_size

    def readtext(self, image, **options):
        if options != VALIDATED_READTEXT_OPTIONS:
            raise ValueError("Unvalidated OCR options; use the original reader")
        from easyocr.utils import reformat_input
        color, grey = reformat_input(image)
        horizontal, free = self.reader.detect(color, reformat=False)
        return recognize_same_width(self.reader, grey, horizontal[0], free[0], batch_size=self.batch_size)


def _owned_nbytes(array) -> int:
    """Bytes kept alive by an array, including the parent buffer of a view."""
    base = getattr(array, "base", None)
    size = int(getattr(array, "nbytes", 0))
    while base is not None and hasattr(base, "nbytes"):
        size = max(size, int(base.nbytes))
        base = getattr(base, "base", None)
    return size


@dataclass
class CrossFrameStats:
    frames_prepared: int = 0
    frames_emitted: int = 0
    groups: int = 0
    budget_flushes: int = 0
    crops: int = 0
    recognition_calls: int = 0
    max_pending_bytes: int = 0
    max_group_frames: int = 0
    detect_seconds: float = 0.0
    recognize_seconds: float = 0.0
    group_sizes: list = field(default_factory=list)

    def as_dict(self):
        return {k: v for k, v in self.__dict__.items() if k != "group_sizes"} | {
            "mean_group_frames": (sum(self.group_sizes) / len(self.group_sizes)) if self.group_sizes else 0.0}


class CrossFrameReader:
    """Experimental: share exact-width recognition batches across consecutive frames.

    Frames are yielded back in input order with their own predictions. The
    window is bounded by frame count and by bytes held (frame + crop buffers);
    a frame that alone exceeds the budget is recognized on its own, never
    trimmed. Errors and interrupts propagate; pending frames are discarded.
    """

    def __init__(self, reader, *, batch_size=8, frame_window=4,
                 byte_budget=DEFAULT_CROSS_FRAME_BYTE_BUDGET,
                 image_list_fn=None, text_fn=None, image_height=None, clock=None):
        _validate(reader, batch_size)
        if not isinstance(frame_window, int) or not 1 <= frame_window <= 8:
            raise ValueError("frame_window must be an integer from 1 to 8")
        if byte_budget <= 0:
            raise ValueError("byte_budget must be positive")
        self.reader = reader
        self.batch_size = batch_size
        self.frame_window = frame_window
        self.byte_budget = byte_budget
        self.image_list_fn, self.text_fn, self.image_height = _easyocr_functions(
            image_list_fn, text_fn, image_height)
        if clock is None:
            from time import perf_counter as clock
        self.clock = clock
        self.stats = CrossFrameStats()

    def _prepare(self, image):
        from easyocr.utils import reformat_input
        started = self.clock()
        color, grey = reformat_input(image)
        horizontal, free = self.reader.detect(color, reformat=False)
        crops = prepare_same_width_crops(grey, horizontal[0], free[0],
                                         image_list_fn=self.image_list_fn, image_height=self.image_height)
        self.stats.detect_seconds += self.clock() - started
        self.stats.frames_prepared += 1
        return crops

    def _flush(self, pending):
        started = self.clock()
        predictions = recognize_prepared(self.reader, [crops for _, crops, _ in pending],
                                         batch_size=self.batch_size, text_fn=self.text_fn,
                                         image_height=self.image_height)
        self.stats.recognize_seconds += self.clock() - started
        self.stats.groups += 1
        self.stats.group_sizes.append(len(pending))
        self.stats.max_group_frames = max(self.stats.max_group_frames, len(pending))
        widths = defaultdict(int)
        for _, crops, _ in pending:
            for width, _ in crops:
                widths[width] += 1
        for width, count in widths.items():
            effective = min(self.batch_size, max(1, MAX_BATCH_PIXELS // (self.image_height * width)))
            self.stats.recognition_calls += -(-count // effective)
        for (item, _, _), frame_predictions in zip(pending, predictions, strict=True):
            self.stats.frames_emitted += 1
            yield item, frame_predictions

    def iter_readtext(self, items, image_of=lambda item: item, **options):
        """Yield (item, predictions) for every item, in order, with serial detection."""
        if options != VALIDATED_READTEXT_OPTIONS:
            raise ValueError("Unvalidated OCR options; use the original reader")
        pending = []
        pending_bytes = 0
        for item in items:
            image = image_of(item)
            crops = self._prepare(image)
            item_bytes = _owned_nbytes(image) + sum(_owned_nbytes(crop) for _, crop in crops)
            self.stats.crops += len(crops)
            if pending and pending_bytes + item_bytes > self.byte_budget:
                self.stats.budget_flushes += 1
                yield from self._flush(pending)
                pending, pending_bytes = [], 0
            pending.append((item, crops, item_bytes))
            pending_bytes += item_bytes
            self.stats.max_pending_bytes = max(self.stats.max_pending_bytes, pending_bytes)
            if len(pending) >= self.frame_window or pending_bytes >= self.byte_budget:
                yield from self._flush(pending)
                pending, pending_bytes = [], 0
        if pending:
            yield from self._flush(pending)
