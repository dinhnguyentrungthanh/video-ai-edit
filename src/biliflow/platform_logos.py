"""Video-platform idents (iQIYI, WeTV, Youku…) as BLUR review cards (batch 4a, 2026-10-03).

User decision: the iQIYI ident at the start and at the end of each episode is
blurred over its logo, never cut. Build-review turns every OCR track or logo
model reading that names a video platform, and every run of frames repeating
a platform logo the user remembered, into one card per ident: the span snaps
to the cut to black before the ident and to the last frame showing the logo,
the region is the union of the logo pixels on the dark frames. A card is only
a suggestion; a human approves every decision.

Build-time only: no scan stage imports this module, so no stage cache key
changes. FFmpeg decodes short source windows exactly like the visual-logo
scanner (cpu, 320 px wide, native frame rate), so a card's preview frames pass
the byte-for-byte self-check of ``brand_memory.studio_logo_window_frames``.
"""

from __future__ import annotations

import math
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from biliflow.brand_memory import (
    STUDIO_LOGO_FRAMES_PATH,
    STUDIO_LOGO_MAXIMUM_CELL_DIFFERENCE,
    STUDIO_LOGO_MIN_FRAME_RANGE,
    STUDIO_LOGO_MINIMUM_SIMILARITY,
    studio_logo_frame_signature,
    studio_logo_mask_area,
    studio_logo_mask_rects,
)
from biliflow.platform_names import PLATFORMS, match_platform_text, match_platform_texts

ANALYSIS_WIDTH = 320  # visual_logo_scanner analysis width
DARK_LEVEL = 48  # a pixel whose brightest channel is at most this is dark
DARK_FRAME_SHARE = 0.995  # share of dark pixels outside the padded logo box
SPAN_BOX_PADDING = 0.5  # of the logo box width/height, each side
PRESENT_MIN_PIXELS = 20
PRESENT_MIN_SHARE = 0.02  # of the padded logo box
MAX_DARK_LEAD_SECONDS = 3.0  # black before the first logo frame kept in the span
REGION_MIN_SHARE = 0.001
REGION_MAX_SHARE = 0.15
REGION_MAX_MEAN = 20.0
REGION_MIN_DENSITY = 0.15  # logo pixels / their bounding box; fade-ins stay below
REGION_ANCHOR_SLACK = 0.5  # of the anchor bbox size, each side
REGION_MIN_ANCHOR_SHARE = 0.10  # a frame needs a tenth of the anchor's logo pixels
REGION_PADDING = (0.03, 0.03)  # of the frame width, of the frame height
FALLBACK_REGION_PADDING = (0.5, 0.35)  # of the OCR box width, height
OCR_BOUND_MARGIN = 1.5  # seconds around the sampled OCR labels
CUT_SEARCH_SECONDS = 3.0  # beyond the OCR bounds
PROBE_MARGIN_SECONDS = 8.0
PROBE_MAX_SECONDS = 40.0
CLUSTER_GAP_SECONDS = 3.0
DECODE_TIMEOUT_SECONDS = 30.0
DECODE_MAX_FRAMES = 1200
PREVIEW_COUNT = 3

Box = tuple[float, float, float, float]  # x0, y0, x1, y1 (end exclusive)
_PREVIEW_TIME = re.compile(r"-(\d+(?:\.\d+)?)s\.jpe?g$", re.IGNORECASE)


# ----------------------------------------------------------------- geometry


def box_from_region(region: object) -> Box | None:
    if not isinstance(region, dict):
        return None
    try:
        x, y = float(region["x"]), float(region["y"])
        width, height = float(region["width"]), float(region["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return (x, y, x + width, y + height)


def region_from_box(box: Box, frame_size: tuple[int, int]) -> dict[str, int]:
    """Whole source pixels covering ``box``, clipped to the frame."""
    width, height = int(frame_size[0]), int(frame_size[1])
    x0 = max(0, min(width - 1, math.floor(box[0])))
    y0 = max(0, min(height - 1, math.floor(box[1])))
    x1 = max(x0 + 1, min(width, math.ceil(box[2])))
    y1 = max(y0 + 1, min(height, math.ceil(box[3])))
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def scale_box(box: Box, factor_x: float, factor_y: float) -> Box:
    return (box[0] * factor_x, box[1] * factor_y, box[2] * factor_x, box[3] * factor_y)


def pad_box(box: Box, pad_x: float, pad_y: float, width: float, height: float) -> Box:
    """``box`` grown by ``pad_x``/``pad_y`` pixels on each side, clipped to the frame."""
    return (max(0.0, box[0] - pad_x), max(0.0, box[1] - pad_y),
            min(float(width), box[2] + pad_x), min(float(height), box[3] + pad_y))


def union_box(boxes: Iterable[Box]) -> Box | None:
    values = list(boxes)
    if not values:
        return None
    return (min(b[0] for b in values), min(b[1] for b in values),
            max(b[2] for b in values), max(b[3] for b in values))


def _int_box(box: Box, width: int, height: int) -> tuple[int, int, int, int]:
    return (max(0, math.floor(box[0])), max(0, math.floor(box[1])),
            min(width, math.ceil(box[2])), min(height, math.ceil(box[3])))


def mask_rects(regions: object, shape: tuple[int, ...]) -> list[tuple[int, int, int, int]]:
    """Padded mask rectangles on a frame of ``shape``; none when they cover over 20 % of it."""
    if not regions or studio_logo_mask_area(regions, shape) > 0.20:
        return []
    return studio_logo_mask_rects(shape, regions)


def _ignored(shape: tuple[int, ...], rects: Iterable[tuple[int, int, int, int]]) -> np.ndarray:
    ignored = np.zeros((int(shape[0]), int(shape[1])), dtype=bool)
    for x0, y0, x1, y1 in rects:
        ignored[y0:y1, x0:x1] = True
    return ignored


def _non_dark(frame: np.ndarray) -> np.ndarray:
    return frame.max(axis=2) > DARK_LEVEL


# --------------------------------------------------------------------- hits


def _preview_time(value: object) -> float | None:
    match = _PREVIEW_TIME.search(str(value or ""))
    return float(match.group(1)) if match else None


def _payloads(scan_payloads: dict[str, dict]) -> list[tuple[str, dict]]:
    seen: set[int] = set()
    unique = []
    for report, payload in scan_payloads.items():
        if isinstance(payload, dict) and id(payload) not in seen:
            seen.add(id(payload))
            unique.append((report, payload))
    return unique


def _track_hits(report: str, payload: dict) -> list[dict]:
    sample_every = float(payload.get("sample_every_seconds") or 3.0)
    source, analysis = payload.get("source_size"), payload.get("analysis_size")
    scale = None
    if isinstance(source, list) and isinstance(analysis, list) and len(source) == len(analysis) == 2:
        if float(analysis[0]) > 0 and float(analysis[1]) > 0:
            scale = (float(source[0]) / float(analysis[0]), float(source[1]) / float(analysis[1]))
    hits = []
    for track in payload.get("tracks") or []:
        # A name inside a fixed watermark belongs to that watermark's own card.
        if (
            not isinstance(track, dict) or track.get("persistent")
            or track.get("candidate_type") == "persistent_overlay" or track.get("grouped_track_ids")
        ):
            continue
        match = track.get("platform_name")
        if not isinstance(match, dict) or match.get("key") not in PLATFORMS:
            match = match_platform_texts(track.get("sample_text") or [])
        if match is None:
            continue
        try:
            label = float(track["start_seconds"])
            last_label = max(label, float(track.get("end_seconds") or label + sample_every) - sample_every)
        except (KeyError, TypeError, ValueError):
            continue
        box = track.get("union_box")
        box_source = None
        if scale and isinstance(box, list) and len(box) == 4:
            box_source = scale_box(tuple(float(value) for value in box), *scale)
        hits.append({
            "platform": match["key"], "kind": "ocr_text", "text": str(match["text"]),
            "rule": match.get("rule"), "label_seconds": round(label, 3),
            "observed_seconds": round(label + sample_every / 2, 3),
            "last_observed_seconds": round(last_label + sample_every / 2, 3),
            "window": [round(label - OCR_BOUND_MARGIN, 3),
                       round(last_label + sample_every + OCR_BOUND_MARGIN, 3)],
            "box_source_px": box_source, "confidence": track.get("max_confidence"),
            "ref": f"{report}#track:{track.get('track_id')}", "report": report,
            "preview": _beside(report, track.get("preview")),
        })
    return hits


def _beside(report: str, relative: object) -> str | None:
    """A report-relative image path (text-previews/…, thumbnails/…) relative to the root."""
    return (Path(report).parent / str(relative)).as_posix() if relative else None


def _visual_hits(report: str, payload: dict) -> list[dict]:
    hits = []
    for key in ("intervals", "rejected_windows"):
        for index, interval in enumerate(payload.get(key) or []):
            if not isinstance(interval, dict):
                continue
            try:
                start, end = float(interval["start_seconds"]), float(interval["end_seconds"])
            except (KeyError, TypeError, ValueError):
                continue
            hint = _preview_time(interval.get("strongest_frame") or interval.get("audit_frame"))
            if hint is None or not start <= hint <= end:
                hint = (start + end) / 2
            confirmation = interval.get("visual_logo_confirmation") or {}
            readings = [(str(confirmation.get("answer") or ""), "vlm_answer", None),
                        (str(interval.get("predicted_label") or ""), "vlm_label", None)]
            localization = interval.get("region_localization") or {}
            for proposal in localization.get("proposals") or []:
                box = proposal.get("blur_region_px") if isinstance(proposal, dict) else None
                region = None
                if isinstance(box, list) and len(box) == 4 and float(box[2]) > 0 and float(box[3]) > 0:
                    region = (float(box[0]), float(box[1]),
                              float(box[0]) + float(box[2]), float(box[1]) + float(box[3]))
                for label in (proposal.get("labels") or []) if isinstance(proposal, dict) else []:
                    readings.append((re.sub(r"</?s>", "", str(label)).strip(), "region_label", region))
            for text, kind, region in readings:
                match = match_platform_text(text)
                if match is None:
                    continue
                hits.append({
                    "platform": match["key"], "kind": kind, "text": match["text"],
                    "rule": match.get("rule"), "label_seconds": round(start, 3),
                    "observed_seconds": round(hint, 3), "last_observed_seconds": round(hint, 3),
                    "window": [round(start, 3), round(end, 3)], "box_source_px": region,
                    "frame_size": localization.get("frame_size"), "confidence": None,
                    "ref": f"{report}#{'interval' if key == 'intervals' else 'rejected'}:{index}",
                    "report": report,
                    "preview": _beside(report, interval.get("strongest_frame") or interval.get("audit_frame")),
                })
    return hits


def platform_text_hits(scan_payloads: dict[str, dict]) -> list[dict]:
    """Every OCR track and logo-model reading that names a video platform.

    ``observed_seconds`` is the frame OCR read (label + half the sample step);
    tracks absorbed into a persistent overlay are left to that overlay's card.
    Pass the text payloads after ``promote_fixed_text_overlays``.
    """
    hits: list[dict] = []
    for report, payload in _payloads(scan_payloads):
        if "tracks" in payload:
            hits.extend(_track_hits(report, payload))
        elif payload.get("scan_type") == "visual_logo":
            hits.extend(_visual_hits(report, payload))
    return sorted(hits, key=lambda hit: (hit["platform"], hit["window"][0], hit["ref"]))


def cluster_hits(hits: list[dict], gap: float = CLUSTER_GAP_SECONDS) -> list[list[dict]]:
    """Overlapping hits of one platform (windows at most ``gap`` apart) form one ident."""
    clusters: list[list[dict]] = []
    for hit in sorted(hits, key=lambda value: (value["platform"], value["window"][0])):
        last = clusters[-1] if clusters else None
        if (
            last is not None and last[0]["platform"] == hit["platform"]
            and hit["window"][0] <= max(value["window"][1] for value in last) + gap
        ):
            last.append(hit)
        else:
            clusters.append([hit])
    return clusters


# ------------------------------------------------------------------- decode


def _video_rate(stream: dict) -> float | None:
    for key in ("avg_frame_rate", "r_frame_rate"):
        numerator, _, denominator = str(stream.get(key) or "").partition("/")
        try:
            rate = float(numerator) / float(denominator or 1)
        except (ValueError, ZeroDivisionError):
            continue
        if math.isfinite(rate) and rate > 0:
            return rate
    return None


class SourceProbe:
    """Short source windows decoded like the visual-logo scanner; never raises.

    ``frames`` returns ``(t, rgb)`` pairs at the analysis size (320 px wide,
    even height) or ``[]`` with ``reason`` set (source or FFmpeg missing,
    probe failure, decode error, 30 s timeout).
    """

    def __init__(self, source: Path | None, ffmpeg_path: Path | None, ffprobe_path: Path | None = None):
        self.source = Path(source) if source else None
        self.ffmpeg = Path(ffmpeg_path) if ffmpeg_path else None
        self.ffprobe = Path(ffprobe_path) if ffprobe_path else (
            self.ffmpeg.with_name(self.ffmpeg.name.replace("ffmpeg", "ffprobe")) if self.ffmpeg else None
        )
        self.reason: str | None = None
        self.decodes = 0
        self.decoded_frames = 0
        self._info: dict | None = None
        self._probed = False

    def info(self) -> dict | None:
        """``{"rate", "source_size", "analysis_size"}`` or ``None`` (``reason`` says why)."""
        if self._probed:
            return self._info
        self._probed = True
        try:
            if self.source is None or not self.source.is_file():
                self.reason = "source_missing"
                return None
            if self.ffmpeg is None or not self.ffmpeg.is_file():
                self.reason = "ffmpeg_missing"
                return None
            from biliflow.probe import probe_video

            probe = probe_video(self.ffprobe, self.source)
            stream = next(value for value in probe.get("streams", []) if value.get("codec_type") == "video")
            width, height = int(stream["width"]), int(stream["height"])
            rate = _video_rate(stream)
        except (OSError, ValueError, KeyError, TypeError, StopIteration, subprocess.SubprocessError):
            self.reason = "probe_failed"
            return None
        if rate is None or width <= 0 or height <= 0:
            self.reason = "probe_failed"
            return None
        analysis_height = max(2, round(height * ANALYSIS_WIDTH / width / 2) * 2)
        self._info = {"rate": rate, "source_size": (width, height),
                      "analysis_size": (ANALYSIS_WIDTH, analysis_height)}
        return self._info

    def frames(self, start: float, end: float, step: float | None = None) -> list[tuple[float, np.ndarray]]:
        info = self.info()
        if info is None or not end > start:
            return []
        from biliflow.visual_logo_scanner import _iter_frames

        step = step or 1.0 / info["rate"]
        width, height = info["analysis_size"]
        # On the frame grid, so a frame's label is its own time (8.00, not 8.02).
        start = max(0.0, math.floor(start * info["rate"] + 1e-6) / info["rate"])
        frames: list[tuple[float, np.ndarray]] = []
        failures: list[str] = []

        def run() -> None:
            iterator = _iter_frames(
                ffmpeg_path=self.ffmpeg, input_path=self.source, start=start,
                duration=end - start, sample_every=step, width=width, height=height,
                decode_backend="cpu",
            )
            try:
                for moment, frame in iterator:
                    if moment >= end - 1e-6 or len(frames) >= DECODE_MAX_FRAMES:
                        break
                    frames.append((moment, frame))
            except Exception as error:  # noqa: BLE001 - a probe failure only drops the probe
                failures.append(type(error).__name__)
            finally:
                iterator.close()  # stops FFmpeg at once when the window ends early

        worker = threading.Thread(target=run, name="platform-logo-probe", daemon=True)
        worker.start()
        worker.join(DECODE_TIMEOUT_SECONDS)
        self.decodes += 1
        if worker.is_alive():
            self.reason = "decode_timeout"
            return []
        if failures or not frames:
            self.reason = "decode_failed" if failures else "decode_empty"
            return []
        self.decoded_frames += len(frames)
        return list(frames)


# --------------------------------------------------------------- span/region


def _frame_step(frames: list[tuple[float, np.ndarray]]) -> float:
    steps = sorted(b[0] - a[0] for a, b in zip(frames, frames[1:]) if b[0] > a[0])
    return steps[len(steps) // 2] if steps else 0.04


def _nearest(frames: list[tuple[float, np.ndarray]], moment: float) -> int:
    return min(range(len(frames)), key=lambda index: abs(frames[index][0] - moment))


def _cut_bounds(frames, hint: float, last_hint: float, bounds: tuple[float, float]) -> tuple[
        float | None, float | None]:
    from biliflow.shot_cuts import detect_cuts, frame_changes

    cuts = detect_cuts(frame_changes(frames))
    before = [cut for cut in cuts if bounds[0] - CUT_SEARCH_SECONDS <= cut <= hint]
    after = [cut for cut in cuts if last_hint < cut <= bounds[1] + CUT_SEARCH_SECONDS]
    return (max(before) if before else None), (min(after) if after else None)


def ident_span(
    frames: list[tuple[float, np.ndarray]], hint: float, box: Box, masks: Iterable = (),
    duration: float | None = None, *, bounds: tuple[float, float] | None = None,
    last_hint: float | None = None,
) -> dict[str, Any]:
    """Snap one ident to the cut to black before it and the last frame showing the logo.

    A dark frame has at least 99.5 % of its pixels outside ``box`` (padded by
    half its size) and outside ``masks`` no brighter than 48; the logo is
    present when the padded box holds max(20, 2 %) bright pixels. The span is
    the dark run around ``hint``: from its first frame (the cut to black) to
    the last frame with the logo plus one frame. When the hint frame is not
    dark (a logo over the picture) the nearest hard cuts around the readings
    bound it, else the OCR ``bounds``. ``masks`` are pixel rectangles.
    """
    last_hint = hint if last_hint is None else max(hint, last_hint)
    bounds = bounds or (hint - 2 * OCR_BOUND_MARGIN, last_hint + 2 * OCR_BOUND_MARGIN)
    fallback = {"start": bounds[0], "end": bounds[1], "method": "ocr_bounds", "start_cut": False}
    if not frames:
        return {**fallback, "reason": "no_frames"}
    height, width = frames[0][1].shape[:2]
    step = _frame_step(frames)
    padded = _int_box(pad_box(box, (box[2] - box[0]) * SPAN_BOX_PADDING,
                              (box[3] - box[1]) * SPAN_BOX_PADDING, width, height), width, height)
    ignored = _ignored((height, width), masks)
    outside = ~ignored
    outside[padded[1]:padded[3], padded[0]:padded[2]] = False
    inside = ~ignored[padded[1]:padded[3], padded[0]:padded[2]]
    outside_total = int(outside.sum())
    present_minimum = max(PRESENT_MIN_PIXELS, PRESENT_MIN_SHARE * max(1, int(inside.sum())))
    rows = []
    for moment, frame in frames:
        bright = _non_dark(frame)
        dark = outside_total == 0 or int(bright[outside].sum()) <= (1 - DARK_FRAME_SHARE) * outside_total
        logo = int(bright[padded[1]:padded[3], padded[0]:padded[2]][inside].sum())
        rows.append((moment, dark, logo >= present_minimum))
    index = _nearest(frames, hint)
    reason = "hint_frame_not_dark"
    if rows[index][1]:
        first = last = index
        while first > 0 and rows[first - 1][1]:
            first -= 1
        while last + 1 < len(rows) and rows[last + 1][1]:
            last += 1
        present = [position for position in range(first, last + 1) if rows[position][2]]
        if present:
            start, start_cut = rows[first][0], first > 0
            lead = rows[present[0]][0] - MAX_DARK_LEAD_SECONDS
            reason = None if start_cut else "dark_run_starts_before_window"
            if start < lead - 1e-6:
                start, start_cut, reason = lead, False, "long_dark_lead"
            end = rows[present[-1]][0] + step
            if duration is not None:
                end = min(end, float(duration))
            return {"start": round(start, 3), "end": round(end, 3), "method": "dark_run",
                    "start_cut": start_cut, "reason": reason}
        reason = "no_logo_on_dark_frames"
    cut_start, cut_end = _cut_bounds(frames, hint, last_hint, bounds)
    if cut_start is None and cut_end is None:
        return {**fallback, "reason": reason}
    return {"start": round(cut_start if cut_start is not None else bounds[0], 3),
            "end": round(cut_end if cut_end is not None else bounds[1], 3),
            "method": "hard_cuts", "start_cut": cut_start is not None, "reason": reason}


def logo_region(
    frames: list[tuple[float, np.ndarray]], span: tuple[float, float] | None = None,
    masks: Iterable = (),
) -> dict[str, Any] | None:
    """The logo pixels of an ident: union of its dark frames' bright pixels, padded 3 %.

    A frame counts when 0.1–15 % of its unmasked pixels are bright, its mean is
    at most 20, the bright pixels fill at least 15 % of their bounding box
    (fade-ins stay far below) and that box lies inside the fullest frame's box
    widened by half its size, with at least a tenth of its pixels (particles
    flying in stay out). Returns analysis-pixel ``box`` and the frame times.
    """
    candidates = []
    for moment, frame in frames:
        if span is not None and not span[0] - 1e-6 <= moment < span[1] - 1e-6:
            continue
        height, width = frame.shape[:2]
        ignored = _ignored((height, width), masks)
        total = max(1, int((~ignored).sum()))
        bright = _non_dark(frame) & ~ignored
        count = int(bright.sum())
        if not REGION_MIN_SHARE <= count / total <= REGION_MAX_SHARE:
            continue
        if float(frame[~ignored].mean()) > REGION_MAX_MEAN:
            continue
        ys, xs = np.nonzero(bright)
        bbox = (float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1))
        if count / ((bbox[2] - bbox[0]) * (bbox[3] - bbox[1])) < REGION_MIN_DENSITY:
            continue
        candidates.append((moment, count, bbox, (width, height)))
    if not candidates:
        return None
    anchor = max(candidates, key=lambda value: (value[1], -value[0]))
    ax0, ay0, ax1, ay1 = anchor[2]
    slack_x, slack_y = (ax1 - ax0) * REGION_ANCHOR_SLACK, (ay1 - ay0) * REGION_ANCHOR_SLACK
    kept = [
        value for value in candidates
        if value[1] >= REGION_MIN_ANCHOR_SHARE * anchor[1]
        and value[2][0] >= ax0 - slack_x and value[2][1] >= ay0 - slack_y
        and value[2][2] <= ax1 + slack_x and value[2][3] <= ay1 + slack_y
    ]
    width, height = anchor[3]
    union = union_box(value[2] for value in kept)
    assert union is not None
    box = pad_box(union, REGION_PADDING[0] * width, REGION_PADDING[1] * height, width, height)
    return {
        "box": [round(value, 3) for value in box], "frames": [round(value[0], 3) for value in kept],
        "anchor_seconds": round(anchor[0], 3), "analysis_size": [width, height],
        "method": "logo_pixels", "padding": list(REGION_PADDING),
    }


def fallback_region(box_source: Box, frame_size: tuple[int, int]) -> Box:
    """The OCR box padded by half its width and a third of its height (Chinese text beside it)."""
    width, height = box_source[2] - box_source[0], box_source[3] - box_source[1]
    return pad_box(box_source, width * FALLBACK_REGION_PADDING[0], height * FALLBACK_REGION_PADDING[1],
                   frame_size[0], frame_size[1])


def bright_box(frame: np.ndarray, masks: Iterable = ()) -> Box | None:
    """Bounding box of the bright unmasked pixels of one frame (analysis pixels)."""
    bright = _non_dark(frame) & ~_ignored(frame.shape, masks)
    ys, xs = np.nonzero(bright)
    if not xs.size:
        return None
    return (float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1))


def write_previews(
    frames: list[tuple[float, np.ndarray]], moments: Iterable[float], folder: Path, prefix: str,
) -> list[Path]:
    """JPEG q88 (the scanner's encoder) of the frames nearest ``moments``: ``<prefix>-<t>s.jpg``."""
    from biliflow.visual_logo_scanner import _jpeg

    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for moment in moments:
        if not frames:
            break
        frame_time, frame = frames[_nearest(frames, moment)]
        path = folder / f"{prefix}-{frame_time:.3f}s.jpg"
        if path in written:
            continue
        path.write_bytes(_jpeg(frame))
        written.append(path)
    return written


# ------------------------------------------------------------ memory runs
# A platform-logo record lives in state/studio-logo-memory.json (schema 2) with
# ``memory_class: "platform_logo"`` and decision BLUR; studio-logo readers skip
# it. Its ``logo_frame_times`` name the stored frames that show the logo.

PLATFORM_MEMORY_CLASS = "platform_logo"
MEMORY_PROBE_SECONDS = 30.0  # head and tail of the source
MEMORY_SAMPLE_FRAMES = 5  # every 5th frame is compared
MEMORY_RUN_GAP_SECONDS = 1.0


def platform_memory_records(memory: dict | None) -> list[dict]:
    """Remembered platform logos that can match: BLUR records with logo frames."""
    return [
        record for record in (memory or {}).get("records") or []
        if isinstance(record, dict) and record.get("memory_class") == PLATFORM_MEMORY_CLASS
        and record.get("decision") == "BLUR" and record.get("logo_frame_times")
    ]


def load_frame_image(root: Path, relative: object) -> np.ndarray | None:
    """One stored frame JPEG as RGB, only from inside state/studio-logo-frames."""
    import cv2

    try:
        base = (root / STUDIO_LOGO_FRAMES_PATH).resolve()
        path = (root / str(relative)).resolve(strict=True)
        if base not in path.parents:
            return None
        bgr = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    except (OSError, ValueError, cv2.error):  # cv2.error: an empty or truncated file
        return None
    if bgr is None or not bgr.size:
        return None
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def record_logo_frames(root: Path, record: dict) -> list[tuple[float, np.ndarray]]:
    """The stored frames of a record listed in its ``logo_frame_times``."""
    wanted = {round(float(value), 3) for value in record.get("logo_frame_times") or []
              if isinstance(value, (int, float))}
    frames = []
    for entry in record.get("stored_frames") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("t"), (int, float)):
            continue
        if round(float(entry["t"]), 3) not in wanted:
            continue
        image = load_frame_image(root, entry.get("image"))
        if image is not None:
            frames.append((float(entry["t"]), image))
    return frames


class _SignedFrames:
    """pHash values and colour grids of one record's logo frames, masked once."""

    def __init__(self, signatures: list[dict]):
        import base64

        self.hashes = [int(value["phash"], 16) for value in signatures]
        self.grids = np.stack([
            np.frombuffer(base64.b64decode(value["grid"]), dtype=np.uint8).astype(np.int16)
            for value in signatures
        ])

    def best(self, signature: dict) -> tuple[float, int] | None:
        """(similarity, cell difference) of the closest passing stored frame, or ``None``."""
        import base64

        value = int(signature["phash"], 16)
        candidate = np.frombuffer(base64.b64decode(signature["grid"]), dtype=np.uint8).astype(np.int16)
        cells = np.abs(self.grids - candidate).max(axis=1)
        best = None
        for index, stored in enumerate(self.hashes):
            similarity = max(0.0, 1.0 - (value ^ stored).bit_count() / 64.0)
            difference = int(cells[index])
            if similarity < STUDIO_LOGO_MINIMUM_SIMILARITY or difference > STUDIO_LOGO_MAXIMUM_CELL_DIFFERENCE:
                continue
            if best is None or (similarity, -difference) > (best[0], -best[1]):
                best = (similarity, difference)
        return best


def as_stored_frame(frame: np.ndarray) -> np.ndarray:
    """A decoded frame after the scanner's JPEG q88 round trip, as stored frames are."""
    import cv2
    from biliflow.visual_logo_scanner import _jpeg

    bgr = cv2.imdecode(np.frombuffer(_jpeg(frame), dtype=np.uint8), cv2.IMREAD_COLOR)
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def _memory_windows(duration: float) -> list[tuple[float, float]]:
    """The first and last 30 s; the whole video when they meet (60 s or shorter)."""
    head = (0.0, min(MEMORY_PROBE_SECONDS, duration))
    tail = (max(0.0, duration - MEMORY_PROBE_SECONDS), duration)
    return [(0.0, duration)] if tail[0] <= head[1] else [head, tail]


def platform_memory_runs(
    root: Path, records: list[dict], probe: SourceProbe, *, duration: float,
    queue_regions: list[dict] | None = None,
) -> tuple[list[dict], dict[str, Any]]:
    """Runs of head/tail frames repeating a remembered platform logo.

    Every 5th frame of the first and last 30 s is compared with each record's
    logo frames, both masked with the record's ignored regions and this
    queue's watermark boxes (none when they cover over 20 %): pHash >= 0.95
    and colour grid <= 20, and the frame must carry picture (range > 40), so
    black and fading frames never match. Any matching frame starts a run;
    matches at most 1 s apart extend it.
    """
    diagnostics: dict[str, Any] = {"records": len(records), "runs": 0, "reason": None}
    if not records:
        return [], diagnostics
    info = probe.info()
    if info is None:
        diagnostics["reason"] = probe.reason
        return [], diagnostics
    shape = (info["analysis_size"][1], info["analysis_size"][0])
    prepared = []
    for record in records:
        stored = record_logo_frames(root, record)
        if not stored:
            continue
        regions = list(record.get("ignored_regions") or []) + list(queue_regions or [])
        if regions and studio_logo_mask_area(regions, shape) > 0.20:
            regions = []
        signed = _SignedFrames([studio_logo_frame_signature(image, regions) for _, image in stored])
        prepared.append((record, regions, signed))
    diagnostics["records_with_frames"] = len(prepared)
    step = MEMORY_SAMPLE_FRAMES / info["rate"]
    runs: list[dict] = []
    for start, end in _memory_windows(float(duration)):
        # Stored frames are scanner JPEGs (q88); the candidates go through the
        # same encoder so thin logo text compares artefact for artefact.
        frames = [(moment, as_stored_frame(frame)) for moment, frame in probe.frames(start, end, step)]
        if not frames:
            diagnostics["reason"] = probe.reason
            continue
        for record, regions, signed in prepared:
            matched = []
            for moment, frame in frames:
                signature = studio_logo_frame_signature(frame, regions)
                if signature["range"] <= STUDIO_LOGO_MIN_FRAME_RANGE:
                    continue
                best = signed.best(signature)
                if best is not None:
                    matched.append((moment, *best))
            runs.extend(_runs_of(record, matched, step))
    diagnostics["runs"] = len(runs)
    return sorted(runs, key=lambda run: run["start"]), diagnostics


def _runs_of(record: dict, matched: list[tuple[float, float, int]], step: float) -> list[dict]:
    groups: list[list[tuple[float, float, int]]] = []
    for value in matched:
        if groups and value[0] - groups[-1][-1][0] <= MEMORY_RUN_GAP_SECONDS + 1e-6:
            groups[-1].append(value)
        else:
            groups.append([value])
    platform = record.get("platform") if isinstance(record.get("platform"), dict) else {}
    key = platform.get("key") if platform.get("key") in PLATFORMS else "unknown"
    blur = record.get("blur_region") if isinstance(record.get("blur_region"), dict) else {}
    return [{
        "platform": key, "name": platform.get("name") or PLATFORMS.get(key, {}).get("name"),
        "record_key": record.get("key"), "start": round(group[0][0], 3),
        "end": round(group[-1][0] + step, 3), "hint": round(group[len(group) // 2][0], 3),
        "similarity": round(min(value[1] for value in group), 6),
        "cell_difference": max(value[2] for value in group), "frames": len(group),
        "box_relative": blur.get("box"),
    } for group in groups]
