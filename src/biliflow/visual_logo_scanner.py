from __future__ import annotations

from biliflow.performance import ScanPerformance

import base64
import gzip
import hashlib
import html
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import psutil

from biliflow.brand_memory import (
    approved_brand_memory_region,
    load_brand_memory,
    match_brand_memory,
    memory_revision,
)
from biliflow.florence_regions import box_iou
from biliflow.probe import duration_seconds, probe_video
from biliflow.storage import require_capacity


VISUAL_LOGO_PROMPT = (
    "Classify only external branding added around or over the movie. External branding includes "
    "a standalone company/platform/studio logo or ident, watermark, ad overlay, branded end card, "
    "or company logo in credits; a one-letter emblem counts. Some images are zoomed crops from "
    "the same frame so small corner and upper-screen marks remain visible. Ordinary dialogue subtitles, the "
    "movie title, person names, and signs/billboards/packages/screens inside the story are not "
    "external branding. Output exactly NO, YES, or YES | actual brand name."
)

VISUAL_LOGO_RETRY_PROMPT = (
    "Do these frames contain external logo, watermark, sponsor, platform, or studio branding "
    "that is separate from the story? Reply exactly NO or YES | actual brand name."
)

BOUNDARY_SCENE_PROMPT = (
    "Judge the full scene, ignoring any small persistent corner watermark. Reply exactly "
    "PROMO_FULL_FRAME when the frames are an advertisement, unrelated movie poster, subscribe "
    "card, sponsor/platform/studio ident, or branded intro/end card; MOVIE_CONTENT when they are "
    "the actual story or the title card of the current episode; otherwise UNCERTAIN."
)

ROUTING_CACHE_SCHEMA_VERSION = 2
ROUTING_ALGORITHM_VERSION = 2
APPROVED_BRAND_MEMORY_SIMILARITY = 0.94


def _inside(root: Path, path: Path, label: str) -> Path:
    root = root.resolve(strict=True)
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"{label} must stay inside {root}")
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validated_source_sha256(path: Path, expected: str | None = None) -> str:
    """Verify cache identity from content so a changed file can never reuse another scan."""
    if expected is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", str(expected)):
        raise ValueError("source_sha256 must contain 64 hexadecimal characters")
    actual = _sha256(path)
    if expected is not None and actual != str(expected).casefold():
        raise ValueError("Source checksum changed before visual-logo scan")
    return actual


def _read_exact(stream, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            break
        chunks.extend(chunk)
    return bytes(chunks)


def _video_size(probe: dict) -> tuple[int, int]:
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "video":
            return int(stream["width"]), int(stream["height"])
    raise ValueError("Video stream is unavailable")


def _correlation(first: np.ndarray, second: np.ndarray) -> float:
    a = first.astype(np.float32).reshape(-1)
    b = second.astype(np.float32).reshape(-1)
    if a.std() < 1e-5 or b.std() < 1e-5:
        return 0.0
    value = float(np.corrcoef(a, b)[0, 1])
    return value if np.isfinite(value) else 0.0


def _rgb_background_distance(frame: np.ndarray, background: np.ndarray) -> np.ndarray:
    """The same Euclidean distance, without a generic last-axis reduction.

    RGB pixels and the channel medians are integers or half integers here, so
    all three squared terms and their sum are exactly representable in float64.
    Keep the original subtraction dtype, square root and normalization.
    """
    delta = frame.astype(np.float32) - background
    squared = delta[:, :, 0] * delta[:, :, 0]
    squared += delta[:, :, 1] * delta[:, :, 1]
    squared += delta[:, :, 2] * delta[:, :, 2]
    np.sqrt(squared, out=squared)
    return squared / 441.673


def logo_candidate_features(
    frame_rgb: np.ndarray, previous_rgb: np.ndarray | None = None
) -> dict[str, float]:
    """Score generic visual-brand layouts without using brand names or OCR text."""
    frame = cv2.resize(frame_rgb, (320, 180), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(frame, cv2.COLOR_RGB2HSV)
    gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
    height, width = gray.shape
    border_width = max(4, round(min(width, height) * 0.06))
    border = np.concatenate(
        (
            frame[:border_width].reshape(-1, 3),
            frame[-border_width:].reshape(-1, 3),
            frame[:, :border_width].reshape(-1, 3),
            frame[:, -border_width:].reshape(-1, 3),
        )
    )
    background = np.median(border, axis=0)
    colour_distance = _rgb_background_distance(frame, background)
    active = colour_distance >= 0.12
    active_ratio = float(active.mean())
    center = active[round(height * 0.18):round(height * 0.82), round(width * 0.18):round(width * 0.82)]
    center_fraction = float(center.sum() / max(1, active.sum()))
    border_std = float(np.mean(np.std(border.astype(np.float32), axis=0)))
    background_uniformity = max(0.0, 1.0 - border_std / 70.0)
    active_saturation = float(hsv[:, :, 1][active].mean() / 255.0) if active.any() else 0.0
    edges = cv2.Canny(gray, 60, 160)
    edge_density = float((edges > 0).mean())
    edge_strength = min(1.0, edge_density / 0.10)
    dark_border = float((cv2.cvtColor(border.reshape(-1, 1, 3), cv2.COLOR_RGB2GRAY) < 45).mean())
    ident_score = (
        0.26 * center_fraction
        + 0.22 * background_uniformity
        + 0.18 * active_saturation
        + 0.16 * edge_strength
        + 0.18 * dark_border
    )
    if not 0.002 <= active_ratio <= 0.68:
        ident_score *= 0.25

    overlay_score = 0.0
    corner_correlation = 0.0
    if previous_rgb is not None:
        previous = cv2.resize(previous_rgb, (320, 180), interpolation=cv2.INTER_AREA)
        previous_gray = cv2.cvtColor(previous, cv2.COLOR_RGB2GRAY)
        previous_edges = cv2.Canny(previous_gray, 60, 160)
        crop_h, crop_w = round(height * 0.28), round(width * 0.28)
        slices = (
            (slice(0, crop_h), slice(0, crop_w)),
            (slice(0, crop_h), slice(width - crop_w, width)),
            (slice(height - crop_h, height), slice(0, crop_w)),
            (slice(height - crop_h, height), slice(width - crop_w, width)),
        )
        correlations = [_correlation(edges[y, x], previous_edges[y, x]) for y, x in slices]
        densities = [float((edges[y, x] > 0).mean()) for y, x in slices]
        eligible = [corr for corr, density in zip(correlations, densities, strict=True) if density >= 0.018]
        corner_correlation = max(eligible, default=0.0)
        center_slice = (slice(round(height * 0.25), round(height * 0.75)), slice(round(width * 0.25), round(width * 0.75)))
        center_correlation = _correlation(edges[center_slice], previous_edges[center_slice])
        persistence_advantage = max(0.0, corner_correlation - max(0.0, center_correlation))
        overlay_score = min(1.0, 0.65 * max(0.0, corner_correlation) + 0.70 * persistence_advantage)

    return {
        "score": round(max(ident_score, overlay_score), 6),
        "ident_score": round(ident_score, 6),
        "overlay_score": round(overlay_score, 6),
        "active_ratio": round(active_ratio, 6),
        "center_fraction": round(center_fraction, 6),
        "background_uniformity": round(background_uniformity, 6),
        "corner_correlation": round(corner_correlation, 6),
    }


_FOCUS_TILES = (
    ("top_left", 0.00, 0.00, 0.46, 0.48),
    ("top_center", 0.23, 0.00, 0.77, 0.48),
    ("top_right", 0.54, 0.00, 1.00, 0.48),
    ("middle_left", 0.00, 0.20, 0.46, 0.82),
    ("middle_right", 0.54, 0.20, 1.00, 0.82),
    ("bottom_left", 0.00, 0.52, 0.46, 1.00),
    ("bottom_center", 0.23, 0.52, 0.77, 1.00),
    ("bottom_right", 0.54, 0.52, 1.00, 1.00),
)

# A corner watermark occupies a small band, not a quadrant. The coarse tiles
# above stay wide so regional scoring keeps its recall, but the box handed to
# region localization is narrowed to these strips. A quadrant-sized "focus"
# accepts a box on a character's face as being in the right place.
_LOCALIZATION_FOCUS_TILES = {
    "top_left": (0.00, 0.00, 0.30, 0.20),
    "top_center": (0.28, 0.00, 0.72, 0.16),
    "top_right": (0.70, 0.00, 1.00, 0.20),
    "middle_left": (0.00, 0.20, 0.22, 0.82),
    "middle_right": (0.78, 0.20, 1.00, 0.82),
    "bottom_left": (0.00, 0.80, 0.30, 1.00),
    "bottom_center": (0.28, 0.84, 0.72, 1.00),
    "bottom_right": (0.70, 0.80, 1.00, 1.00),
}


def localization_focus_box(
    focus_region: str | None, frame_size: tuple[int, int],
) -> list[float] | None:
    """Narrow a coarse router tile to the strip a watermark can actually occupy.

    Returns None when the router did not commit to a corner (``full`` or an
    approved-memory route), leaving the caller's existing behaviour unchanged.
    """
    strip = _LOCALIZATION_FOCUS_TILES.get(str(focus_region or ""))
    if strip is None:
        return None
    width, height = frame_size
    left, top, right, bottom = strip
    return [
        float(width) * left, float(height) * top,
        float(width) * right, float(height) * bottom,
    ]


def regional_logo_candidate_features(
    frame_rgb: np.ndarray, previous_rgb: np.ndarray | None = None,
) -> dict[str, object]:
    """Score the full frame and overlapping regions for small branding.

    Full-frame VLM input made a small stylized mark easy to miss when a title
    or call-to-action dominated the image. The regional score is only a recall
    router: it decides which windows and crops the semantic model sees and can
    never authorize an edit.
    """
    height, width = frame_rgb.shape[:2]
    full = logo_candidate_features(frame_rgb, previous_rgb)
    best_name = "full"
    best_box = (0, 0, width, height)
    best_features = full
    for name, left, top, right, bottom in _FOCUS_TILES:
        x1, y1 = round(width * left), round(height * top)
        x2, y2 = round(width * right), round(height * bottom)
        crop = frame_rgb[y1:y2, x1:x2]
        previous_crop = (
            previous_rgb[y1:y2, x1:x2]
            if previous_rgb is not None else None
        )
        if crop.size == 0:
            continue
        features = logo_candidate_features(crop, previous_crop)
        if float(features["score"]) > float(best_features["score"]):
            best_name = name
            best_box = (x1, y1, x2, y2)
            best_features = features
    return {
        **full,
        "score": round(max(float(full["score"]), float(best_features["score"])), 6),
        "full_frame_score": float(full["score"]),
        "regional_score": float(best_features["score"]),
        "focus_region": best_name,
        "focus_box_analysis": list(best_box),
    }


def _frame_change_score(first: np.ndarray, second: np.ndarray) -> float:
    if first.shape != second.shape:
        return 0.0
    return float(np.mean(np.abs(first.astype(np.float32) - second.astype(np.float32))) / 255.0)


def _evidence_timestamp(item: dict) -> float:
    """Return the actual thumbnail time, including reports from older versions."""
    value = item.get("strongest_timestamp_seconds")
    if value is not None:
        return float(value)
    for key in ("strongest_frame", "audit_frame"):
        match = re.search(r"-(\d+(?:\.\d+)?)s\.jpg$", str(item.get(key) or ""))
        if match:
            return float(match.group(1))
    return (
        float(item.get("start_seconds") or 0.0)
        + float(item.get("end_seconds") or 0.0)
    ) / 2


def consolidate_end_card_intervals(
    intervals: list[dict], rejected: list[dict], *, duration: float,
    boundary_seconds: float, window_seconds: float,
    transition_samples: list[tuple[float, float]] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Combine a branded final sequence into one review item.

    A single VLM miss between two confirmed windows must not split an end card.
    This only creates a CUT suggestion for the human review gate; it does not
    record a decision or modify the source.
    """
    if duration <= 0 or boundary_seconds < 0 or window_seconds <= 0:
        raise ValueError("Invalid end-card consolidation limits")
    floor = max(0.0, duration - boundary_seconds)
    boundary_positive = sorted(
        (item for item in intervals if float(item["end_seconds"]) > floor),
        key=lambda item: float(item["start_seconds"]),
    )
    full_scene_seeds = [
        item for item in boundary_positive
        if item.get("candidate_type") == "closing_promotion"
        or str(
            item.get("visual_logo_confirmation", {})
            .get("boundary_scene_context", {}).get("state") or ""
        ) == "PROMO_FULL_FRAME"
    ]
    # A persistent corner watermark often remains over black frames at the end.
    # It is still a regional BLUR candidate, not evidence to cut the full scene.
    if not full_scene_seeds:
        return intervals, rejected
    final_positive = boundary_positive
    if len(final_positive) < 2:
        return intervals, rejected
    latest = final_positive[-1]
    if float(latest["end_seconds"]) < duration - window_seconds - 0.05:
        return intervals, rejected

    chain = [latest]
    for candidate in reversed(final_positive[:-1]):
        if float(chain[0]["start_seconds"]) - float(candidate["end_seconds"]) <= window_seconds + 0.05:
            chain.insert(0, candidate)
        else:
            break
    if len(chain) < 2:
        return intervals, rejected

    detected_start = float(chain[0]["start_seconds"])
    refined_start = detected_start
    candidates = [
        (timestamp, score) for timestamp, score in (transition_samples or [])
        if detected_start - min(1.5, window_seconds) <= timestamp <= detected_start + 0.5
    ]
    if candidates:
        timestamp, score = max(candidates, key=lambda item: item[1])
        if score >= 0.08:
            refined_start = timestamp

    labels = []
    reasons = []
    for item in chain:
        label = str(item.get("predicted_label", "")).strip()
        if label and label != "Visual brand/logo candidate":
            labels.append(label)
        if item.get("reason"):
            reasons.append(str(item["reason"]))
    strongest = max(chain, key=lambda item: float(item.get("max_score") or 0.0))
    in_interval = [
        item for item in chain if _evidence_timestamp(item) >= refined_start - 0.001
    ]
    representative = max(
        in_interval or chain,
        key=lambda item: float(item.get("max_score") or 0.0),
    )
    ordered_evidence = [representative] + [
        item for item in (in_interval or chain) if item is not representative
    ]
    frames = [
        str(item["strongest_frame"])
        for item in ordered_evidence if item.get("strongest_frame")
    ]
    combined = dict(strongest)
    if representative.get("strongest_frame"):
        combined["strongest_frame"] = representative["strongest_frame"]
    if representative.get("audit_frame"):
        combined["audit_frame"] = representative["audit_frame"]
    combined["strongest_timestamp_seconds"] = round(
        _evidence_timestamp(representative), 3
    )
    combined.update({
        "start_seconds": round(max(0.0, refined_start), 3),
        "end_seconds": round(duration, 3),
        "predicted_label": labels[0] if labels else "Branded end card / channel promotion",
        "priority": "high",
        "candidate_type": "branded_end_card",
        "suggested_decision": "CUT",
        "reason": "Temporal boundary evidence grouped branded final windows; one human decision covers the complete end card",
        "supporting_frames": list(dict.fromkeys(frames))[:3],
        "temporal_confirmation": {
            "method": "final_boundary_bridge",
            "confirmed_window_count": len(chain),
            "detected_start_seconds": round(detected_start, 3),
            "refined_start_seconds": round(refined_start, 3),
            "bridged_single_window_gaps": True,
            "automatic_edit": False,
        },
    })
    chain_ids = {id(item) for item in chain}
    retained = [item for item in intervals if id(item) not in chain_ids]
    retained.append(combined)
    return sorted(retained, key=lambda item: float(item["start_seconds"])), rejected


def consolidate_opening_promotion_intervals(
    intervals: list[dict], *, scan_start: float,
    boundary_seconds: float, window_seconds: float,
) -> list[dict]:
    """Join consecutive full-frame opening promotions into one CUT suggestion."""
    if scan_start < 0 or boundary_seconds < 0 or window_seconds <= 0:
        raise ValueError("Invalid opening-promotion consolidation limits")
    opening = sorted(
        (
            item for item in intervals
            if item.get("candidate_type") == "opening_promotion"
            and float(item["start_seconds"]) < scan_start + boundary_seconds
        ),
        key=lambda item: float(item["start_seconds"]),
    )
    if not opening or float(opening[0]["start_seconds"]) > scan_start + 0.05:
        return intervals
    chain = [opening[0]]
    for candidate in opening[1:]:
        if float(candidate["start_seconds"]) - float(chain[-1]["end_seconds"]) <= window_seconds + 0.05:
            chain.append(candidate)
        else:
            break
    if len(chain) < 2:
        return intervals
    strongest = max(chain, key=lambda item: float(item.get("max_score") or 0.0))
    combined = dict(strongest)
    combined.update({
        "start_seconds": round(scan_start, 3),
        "end_seconds": round(float(chain[-1]["end_seconds"]), 3),
        "predicted_label": "Full-frame opening promotion / branded intro",
        "priority": "high",
        "candidate_type": "opening_promotion",
        "suggested_decision": "CUT",
        "reason": "Local boundary semantics grouped consecutive full-frame promotional windows",
        "supporting_frames": list(dict.fromkeys(
            str(item["strongest_frame"]) for item in chain if item.get("strongest_frame")
        ))[:3],
        "temporal_confirmation": {
            "method": "opening_boundary_semantics",
            "confirmed_window_count": len(chain),
            "automatic_edit": False,
        },
    })
    chain_ids = {id(item) for item in chain}
    retained = [item for item in intervals if id(item) not in chain_ids]
    retained.append(combined)
    return sorted(retained, key=lambda item: float(item["start_seconds"]))


def _focus_geometry(item: dict) -> tuple[float, float, float, float] | None:
    box = item.get("visual_logo_confirmation", {}).get("features", {}).get(
        "focus_box_analysis"
    )
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        values = tuple(float(value) for value in box)
    except (TypeError, ValueError):
        return None
    if values[2] <= values[0] or values[3] <= values[1]:
        return None
    return values


def _partition_by_focus_geometry(
    group: list[dict], *, minimum_iou: float = 0.30,
) -> list[list[dict]]:
    """Split a semantic group into clusters that occupy the same place.

    A shared label and a shared coarse tile do not make two detections the same
    mark: a quadrant-sized tile holds both a corner watermark and a character's
    face. Without this, one generic label lets unrelated regions inherit each
    other's timeline, so a box seen in a single window can be promoted to a
    persistent overlay covering the film.
    """
    clusters: list[tuple[list[dict], tuple[float, float, float, float] | None]] = []
    for item in group:
        geometry = _focus_geometry(item)
        if geometry is None:
            # Reports without a focus box keep their previous grouping.
            if clusters:
                clusters[0][0].append(item)
            else:
                clusters.append(([item], None))
            continue
        for members, reference in clusters:
            if reference is not None and box_iou(geometry, reference) >= minimum_iou:
                members.append(item)
                break
        else:
            clusters.append(([item], geometry))
    return [members for members, _ in clusters]


def consolidate_persistent_overlay_intervals(
    intervals: list[dict], *, duration: float, window_seconds: float,
    transition_samples: list[tuple[float, float]] | None = None,
) -> list[dict]:
    """Group repeated regional branding into one timeline review item."""
    if duration <= 0 or window_seconds <= 0:
        raise ValueError("Invalid persistent-overlay consolidation limits")
    end_cards = [item for item in intervals if item.get("candidate_type") == "branded_end_card"]
    content_end = min(
        (float(item["start_seconds"]) for item in end_cards), default=duration,
    )
    groups: dict[str, list[dict]] = {}
    for item in intervals:
        # Boundary promotions and other already-classified scenes have their
        # own finite timeline.  They must never be recycled as evidence for a
        # persistent overlay, otherwise an opening ident can be stretched to
        # the end of the movie.
        if item.get("candidate_type") is not None:
            continue
        features = item.get("visual_logo_confirmation", {}).get("features", {})
        focus = str(features.get("focus_region", "full"))
        if focus == "full":
            continue
        memory = features.get("brand_memory")
        group_key = None
        if (
            isinstance(memory, dict)
            and memory.get("memory_class") == "brand"
            and item.get("visual_logo_confirmation", {}).get("confirmation_source")
            == "approved_brand_memory"
        ):
            relative_box = memory.get("relative_box")
            if isinstance(relative_box, list) and len(relative_box) == 4:
                # Several approved records can describe the same watermark.
                # Geometry identifies the visual target without tying the
                # detector to a source-specific memory key or brand name.
                group_key = "approved-brand-memory"
        if group_key is None:
            label = str(item.get("predicted_label") or "").strip().casefold()
            if label and label != "visual brand/logo candidate":
                group_key = f"semantic:{focus}:{label}"
        # A generic YES in several unrelated frames proves neither a stable
        # identity nor a continuous overlay. Keep those windows separate for
        # review instead of stretching them to the end of the movie.
        if group_key is None:
            continue
        groups.setdefault(group_key, []).append(item)
    # A semantic key carries a label and a coarse tile but no geometry, so one
    # generic label can merge a corner watermark with whatever else shared the
    # tile. Split each group into clusters that occupy the same place first.
    clustered: list[tuple[str, list[dict]]] = []
    for identity, group in groups.items():
        partitions = _partition_by_focus_geometry(group)
        for index, members in enumerate(partitions):
            suffix = "" if len(partitions) == 1 else f":cluster{index}"
            clustered.append((f"{identity}{suffix}", members))
    eligible = [
        (identity, sorted(group, key=lambda item: float(item["start_seconds"])))
        for identity, group in clustered
        if len(group) >= 4
        and float(max(group, key=lambda item: float(item["end_seconds"]))["end_seconds"])
        - float(min(group, key=lambda item: float(item["start_seconds"]))["start_seconds"])
        >= min(60.0, duration * 0.25)
    ]
    if not eligible:
        return intervals
    grouped_ids: set[int] = set()
    combined_items: list[dict] = []
    grouped_ranges: list[tuple[str, float, float]] = []
    for identity, group in sorted(eligible, key=lambda value: value[0]):
        strongest_features = max(
            group, key=lambda item: float(item.get("max_score") or 0.0)
        ).get("visual_logo_confirmation", {}).get("features", {})
        focus = str(strongest_features.get("focus_region", "full"))
        detected_start = float(group[0]["start_seconds"])
        refined_start = detected_start
        candidates = [
            (timestamp, score) for timestamp, score in (transition_samples or [])
            if detected_start - window_seconds <= timestamp <= detected_start + 0.5
        ]
        if candidates:
            timestamp, score = max(candidates, key=lambda item: item[1])
            if score >= 0.08:
                refined_start = timestamp
        samples = [group[0], group[len(group) // 2], group[-1]]
        strongest = max(group, key=lambda item: float(item.get("max_score") or 0.0))
        combined = dict(strongest)
        combined.update({
            "start_seconds": round(max(0.0, refined_start), 3),
            "end_seconds": round(content_end, 3),
            "predicted_label": "Persistent external logo / watermark",
            "priority": "high",
            "candidate_type": "persistent_overlay",
            "reason": "Repeated regional visual-brand confirmations were grouped into one human review item",
            "supporting_frames": list(dict.fromkeys(
                str(item["strongest_frame"]) for item in samples if item.get("strongest_frame")
            )),
            "region_seed_analysis": strongest.get(
                "visual_logo_confirmation", {}
            ).get("features", {}).get("focus_box_analysis"),
            "temporal_confirmation": {
                "method": "repeated_focus_region",
                "focus_region": focus,
                "confirmed_sample_count": len(group),
                "detected_start_seconds": round(detected_start, 3),
                "refined_start_seconds": round(refined_start, 3),
                "automatic_edit": False,
            },
        })
        combined_items.append(combined)
        grouped_ids.update(id(item) for item in group)
        grouped_ranges.append((focus, refined_start, content_end))

    retained = []
    for item in intervals:
        if id(item) in grouped_ids:
            continue
        item_focus = str(
            item.get("visual_logo_confirmation", {}).get("features", {}).get("focus_region", "full")
        )
        covered = any(
            item_focus == focus and start <= float(item["start_seconds"]) < end
            for focus, start, end in grouped_ranges
        )
        if covered and item.get("candidate_type") is None and str(
            item.get("predicted_label", "")
        ) == "Visual brand/logo candidate":
            continue
        retained.append(item)
    retained.extend(combined_items)
    return sorted(retained, key=lambda item: float(item["start_seconds"]))


def select_window_evidence(frames: list[dict], maximum: int = 2) -> list[dict]:
    """Keep the strongest frame plus a late frame so short overlays survive."""
    if maximum <= 0:
        raise ValueError("maximum must be positive")
    if not frames:
        return []
    strongest = max(
        frames, key=lambda item: (float(item["features"]["score"]), -float(item["timestamp_seconds"]))
    )
    selected = [strongest]
    if maximum > 1:
        latest = max(frames, key=lambda item: float(item["timestamp_seconds"]))
        if latest is not strongest:
            selected.append(latest)
    if len(selected) < maximum:
        for candidate in sorted(frames, key=lambda item: float(item["timestamp_seconds"])):
            if candidate not in selected:
                selected.append(candidate)
            if len(selected) >= maximum:
                break
    return selected


def parse_logo_answer(answer: str) -> tuple[str, str | None]:
    normalized = answer.strip()
    folded = normalized.casefold()
    if folded.startswith("remove"):
        parts = re.split(r"\s*[|:]\s*", normalized, maxsplit=1)
        name = parts[1].strip() if len(parts) == 2 and parts[1].strip() else None
        return "CONFIRMED", name
    if folded.startswith("keep"):
        return "REJECTED", None
    if folded.startswith("uncertain"):
        return "UNCERTAIN", None
    if folded.startswith("yes"):
        parts = re.split(r"\s*[|:]\s*", normalized, maxsplit=1)
        name = parts[1].strip() if len(parts) == 2 and parts[1].strip() else None
        if name and is_instruction_echo(name):
            return "UNCERTAIN", None
        return "CONFIRMED", name
    if folded.startswith("no"):
        return "REJECTED", None
    return "UNCERTAIN", None


def parse_boundary_scene_answer(answer: str) -> str:
    folded = " ".join(answer.strip().upper().split())
    if folded.startswith("PROMO_FULL_FRAME"):
        return "PROMO_FULL_FRAME"
    if folded.startswith("MOVIE_CONTENT"):
        return "MOVIE_CONTENT"
    return "UNCERTAIN"


def is_instruction_echo(text: str) -> bool:
    folded = " ".join(text.casefold().split())
    fragments = (
        "when known",
        "when branding",
        "we are finding",
        "graphics added before",
        "external branding added",
        "reply exactly",
        "actual brand name",
    )
    return any(fragment in folded for fragment in fragments)


def _iter_frames(
    *, ffmpeg_path: Path, input_path: Path, start: float, duration: float,
    sample_every: float, width: int, height: int,
):
    frame_bytes = width * height * 3
    command = [
        str(ffmpeg_path), "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-i", str(input_path), "-t", f"{duration:.3f}",
        "-vf", f"fps={1.0 / sample_every},scale={width}:{height}:flags=bilinear",
        "-an", "-sn", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    index = 0
    try:
        assert process.stdout is not None
        while True:
            data = _read_exact(process.stdout, frame_bytes)
            if not data:
                break
            if len(data) != frame_bytes:
                raise RuntimeError(f"Incomplete raw frame: {len(data)} of {frame_bytes} bytes")
            frame = np.frombuffer(data, dtype=np.uint8).reshape((height, width, 3)).copy()
            yield round(start + index * sample_every, 3), frame
            index += 1
        code = process.wait()
        if code:
            error = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
            raise RuntimeError(f"FFmpeg failed with exit code {code}: {error[-2000:]}")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def _jpeg(frame_rgb: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(
        ".jpg", cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR),
        [cv2.IMWRITE_JPEG_QUALITY, 88],
    )
    if not ok:
        raise RuntimeError("Could not encode logo candidate frame")
    return encoded.tobytes()


def _routing_cache_key(source_sha256: str, settings: dict) -> str:
    raw = json.dumps(
        {
            "source_sha256": source_sha256,
            "algorithm_version": ROUTING_ALGORITHM_VERSION,
            "cache_schema_version": ROUTING_CACHE_SCHEMA_VERSION,
            "settings": settings,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _routing_cache_path(root: Path, source_sha256: str, cache_key: str) -> Path:
    return root / "cache" / "visual-logo" / source_sha256 / f"routing-{cache_key}.json.gz"


def _write_routing_cache(
    path: Path, *, source_sha256: str, cache_key: str,
    windows: dict[tuple[float, float], list[dict]],
    window_sample_counts: dict[tuple[float, float], int],
    boundary_keys: set[tuple[float, float]],
    boundary_transitions: list[tuple[float, float]],
    frames_scanned: int, heuristic_hits: int, coverage_fallback_count: int,
    scene_routed_count: int,
) -> None:
    serialized_windows = []
    for key in sorted(windows):
        # Routing examines every frame's regional evidence. Keeping only the
        # strongest/latest VLM images changes which windows get selected on a
        # cache hit. Preserve original order, features and bytes; reduce to two
        # frames only after candidate selection, exactly as in a cold scan.
        evidence = windows[key]
        serialized_windows.append({
            "key": list(key),
            "sample_count": int(window_sample_counts.get(key, len(windows[key]))),
            "frames": [{
                "timestamp_seconds": item["timestamp_seconds"],
                "jpeg": base64.b64encode(item["jpeg"]).decode("ascii"),
                "focus_jpeg": (
                    base64.b64encode(item["focus_jpeg"]).decode("ascii")
                    if item.get("focus_jpeg") else None
                ),
                "features": item["features"],
            } for item in evidence],
        })
    payload = {
        "schema_version": ROUTING_CACHE_SCHEMA_VERSION,
        "source_sha256": source_sha256,
        "cache_key": cache_key,
        "windows": serialized_windows,
        "boundary_keys": [list(key) for key in sorted(boundary_keys)],
        "boundary_transitions": boundary_transitions,
        "frames_scanned": frames_scanned,
        "heuristic_hits": heuristic_hits,
        "coverage_fallback_count": coverage_fallback_count,
        "scene_routed_count": scene_routed_count,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    temporary.replace(path)


def _read_routing_cache(
    path: Path, *, source_sha256: str, cache_key: str,
) -> dict | None:
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        if (
            payload.get("schema_version") != ROUTING_CACHE_SCHEMA_VERSION
            or payload.get("source_sha256") != source_sha256
            or payload.get("cache_key") != cache_key
        ):
            return None
        windows: dict[tuple[float, float], list[dict]] = {}
        counts: dict[tuple[float, float], int] = {}
        for entry in payload.get("windows", []):
            key = tuple(float(value) for value in entry["key"])
            if len(key) != 2:
                return None
            windows[key] = [{
                "timestamp_seconds": float(item["timestamp_seconds"]),
                "jpeg": base64.b64decode(item["jpeg"]),
                "focus_jpeg": (
                    base64.b64decode(item["focus_jpeg"])
                    if item.get("focus_jpeg") else None
                ),
                "features": item["features"],
            } for item in entry.get("frames", [])]
            counts[key] = int(entry.get("sample_count", len(windows[key])))
        if not windows:
            return None
        payload["decoded_windows"] = windows
        payload["decoded_counts"] = counts
        payload["decoded_boundary_keys"] = {
            tuple(float(value) for value in key)
            for key in payload.get("boundary_keys", [])
            if len(key) == 2
        }
        return payload
    except (OSError, EOFError, ValueError, KeyError, json.JSONDecodeError):
        return None


def retain_coverage_candidate(
    buckets: dict[int, list[dict]], bucket: int, candidate: dict,
    *, maximum_per_bucket: int,
) -> None:
    """Keep only the strongest fallback frames so memory stays bounded."""
    if maximum_per_bucket <= 0:
        raise ValueError("maximum_per_bucket must be positive")
    values = buckets.setdefault(bucket, [])
    values.append(candidate)
    values.sort(
        key=lambda item: (
            -float(item["features"]["score"]), float(item["timestamp_seconds"])
        )
    )
    del values[maximum_per_bucket:]


def _route_with_brand_memory(
    frame_rgb: np.ndarray, features: dict, memory_records: list[dict],
) -> dict:
    if not memory_records:
        return features
    match = match_brand_memory(frame_rgb, memory_records, minimum_similarity=0.84)
    if match is None:
        return features
    output = dict(features)
    output["brand_memory"] = match
    if match.get("memory_class") == "brand":
        output["score"] = round(
            max(float(output["score"]), 0.52 + 0.48 * match["similarity"]), 6
        )
        region = approved_brand_memory_region(
            match, (frame_rgb.shape[1], frame_rgb.shape[0])
        )
        if region is not None:
            output["focus_box_analysis"] = [
                region["x"], region["y"],
                region["x"] + region["width"],
                region["y"] + region["height"],
            ]
            output["focus_region"] = "approved_brand_memory"
    return output


_ROUTING_WORKER_MEMORY: list[dict] = []


def _init_routing_worker(memory_records: list[dict], opencv_threads: int | None) -> None:
    global _ROUTING_WORKER_MEMORY
    _ROUTING_WORKER_MEMORY = memory_records
    if opencv_threads is not None:
        cv2.setNumThreads(opencv_threads)


def _route_frame_in_worker(frame_rgb: np.ndarray, previous_rgb: np.ndarray | None):
    started = time.perf_counter()
    features = regional_logo_candidate_features(frame_rgb, previous_rgb)
    middle = time.perf_counter()
    features = _route_with_brand_memory(frame_rgb, features, _ROUTING_WORKER_MEMORY)
    return features, middle - started, time.perf_counter() - middle


class RoutingPool:
    """Frame-parallel CPU routing with results consumed strictly in input order.

    Both routing functions are pure functions of (frame, previous frame,
    memory records), so each worker computes exactly what the serial loop
    would; only the wall-clock schedule changes. In-flight frames are bounded.
    Workers are spawned processes: the entry point must be ``python -m biliflow``
    or a script guarded by ``if __name__ == "__main__"``, as for any
    multiprocessing code on Windows.
    """

    def __init__(self, workers: int, memory_records: list[dict], *,
                 opencv_threads: int | None = 1, in_flight_per_worker: int = 4):
        if not isinstance(workers, int) or isinstance(workers, bool) or not 2 <= workers <= 8:
            raise ValueError("routing workers must be an integer from 2 to 8")
        from concurrent.futures import ProcessPoolExecutor
        self.workers = workers
        self.max_in_flight = workers * in_flight_per_worker
        self.opencv_threads = opencv_threads
        self.worker_feature_seconds = 0.0
        self.worker_brand_memory_seconds = 0.0
        self.frames = 0
        self._executor = ProcessPoolExecutor(
            max_workers=workers, initializer=_init_routing_worker,
            initargs=(memory_records, opencv_threads),
        )

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)

    def metrics(self) -> dict:
        return {
            "workers": self.workers, "max_in_flight": self.max_in_flight,
            "worker_opencv_threads": self.opencv_threads, "frames": self.frames,
            "worker_feature_seconds": round(self.worker_feature_seconds, 6),
            "worker_brand_memory_seconds": round(self.worker_brand_memory_seconds, 6),
        }

    def iterate(self, frames, performance: ScanPerformance):
        from collections import deque
        pending = deque()
        previous = None

        def collect():
            timestamp, frame, future = pending.popleft()
            features, feature_seconds, memory_seconds = performance.call('logo_routing_wait', future.result)
            self.worker_feature_seconds += feature_seconds
            self.worker_brand_memory_seconds += memory_seconds
            self.frames += 1
            return timestamp, frame, features

        for timestamp, frame in frames:
            pending.append((timestamp, frame, self._executor.submit(_route_frame_in_worker, frame, previous)))
            previous = frame
            if len(pending) >= self.max_in_flight:
                yield collect()
        while pending:
            yield collect()


def _iter_routed_frames(frames, memory_records: list[dict], performance: ScanPerformance,
                        pool: RoutingPool | None = None):
    """Yield (timestamp, frame, routed features) in order; serial without a pool."""
    if pool is not None:
        yield from pool.iterate(frames, performance)
        return
    previous = None
    for timestamp, frame in frames:
        features = performance.call('logo_feature_extraction', regional_logo_candidate_features, frame, previous)
        features = performance.call('logo_brand_memory', _route_with_brand_memory, frame, features, memory_records)
        yield timestamp, frame, features
        previous = frame


def write_visual_logo_audit_html(report_dir: Path, records: list[dict]) -> Path:
    cards = []
    for record in sorted(records, key=lambda item: float(item["start_seconds"])):
        confirmation = record.get("visual_logo_confirmation", {})
        state = str(confirmation.get("state", "UNKNOWN"))
        answer = html.escape(str(confirmation.get("answer", "")))
        frame = html.escape(str(record.get("audit_frame", "")))
        start = max(0, int(float(record["start_seconds"])))
        end = max(0, int(float(record["end_seconds"])))
        clock = lambda value: f"{value // 3600:02d}:{(value % 3600) // 60:02d}:{value % 60:02d}"
        cards.append(
            f'<article class="card" data-state="{html.escape(state)}">'
            f'<img loading="lazy" src="{frame}" alt="{clock(start)}">'
            f'<div><strong>{clock(start)}–{clock(end)}</strong> '
            f'<span>{html.escape(state)}</span></div><p>{answer}</p></article>'
        )
    document = """<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Visual brand/logo exhaustive audit</title>
<style>body{font-family:system-ui;margin:20px;background:#101216;color:#eee}button{margin:0 6px 16px 0;padding:8px 12px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}.card{background:#1b1f27;padding:10px;border-radius:8px}.card img{width:100%;aspect-ratio:16/9;object-fit:contain;background:#000}.card p{font-size:12px;color:#bbb;word-break:break-word}.card span{float:right;color:#f6c453}.hidden{display:none}</style></head><body>
<h1>Audit brand/logo toàn timeline</h1><p>Mỗi thẻ là một cửa sổ 5 giây. REJECTED vẫn được giữ để kiểm tra false negative; ảnh không cho phép tự động chỉnh video.</p>
<button onclick="filterCards('ALL')">Tất cả</button><button onclick="filterCards('CONFIRMED')">Confirmed</button><button onclick="filterCards('UNCERTAIN')">Uncertain</button><button onclick="filterCards('REJECTED')">Rejected</button>
<div class="grid">""" + "".join(cards) + """</div><script>function filterCards(state){document.querySelectorAll('.card').forEach(card=>card.classList.toggle('hidden',state!=='ALL'&&card.dataset.state!==state));}</script></body></html>"""
    output = report_dir / "audit.html"
    output.write_text(document, encoding="utf-8")
    return output


def _window_key(timestamp: float, window_seconds: float) -> tuple[float, float]:
    start = int(timestamp // window_seconds) * window_seconds
    return round(start, 3), round(start + window_seconds, 3)


def effective_sample_every(sample_every: float, exhaustive: bool) -> float:
    """Cap recall-first scans at two samples per second across the full timeline."""
    return min(sample_every, 0.5) if exhaustive else sample_every


def adaptive_candidate_budget(
    *, scan_duration: float, requested_maximum: int, boundary_window_count: int,
) -> int:
    """Bound expensive VLM work while retaining time-distributed coverage."""
    if scan_duration <= 0 or requested_maximum <= 0 or boundary_window_count < 0:
        raise ValueError("Invalid adaptive candidate budget inputs")
    five_minute_buckets = max(1, int(np.ceil(scan_duration / 300.0)))
    # Long videos need enough room for every new candidate plus periodic
    # verification of already-approved persistent brands. The old 12/bucket
    # target routinely saturated at 80 windows and silently dropped most of a
    # feature-length film before semantic confirmation.
    target = max(40, boundary_window_count + 18 * five_minute_buckets)
    return min(requested_maximum, target)


def _approved_brand_key(frames: list[dict]) -> str | None:
    """Return the approved memory identity dominating a candidate window."""
    if not frames:
        return None
    strongest = max(
        frames, key=lambda item: float(item.get("features", {}).get("score", 0.0))
    )
    match = strongest.get("features", {}).get("brand_memory")
    if not isinstance(match, dict):
        return None
    if (
        match.get("memory_class") != "brand"
        or float(match.get("similarity", 0.0)) < APPROVED_BRAND_MEMORY_SIMILARITY
    ):
        return None
    value = str(match.get("memory_key") or "").strip()
    return value or None


def _approved_brand_box(frames: list[dict]) -> tuple[float, float, float, float] | None:
    """Return a normalized xyxy box for a strong approved-memory match."""
    if _approved_brand_key(frames) is None:
        return None
    strongest = max(
        frames, key=lambda item: float(item.get("features", {}).get("score", 0.0))
    )
    match = strongest.get("features", {}).get("brand_memory")
    relative = match.get("relative_box") if isinstance(match, dict) else None
    if not isinstance(relative, (list, tuple)) or len(relative) != 4:
        return None
    try:
        left, top, width, height = (float(value) for value in relative)
    except (TypeError, ValueError):
        return None
    if left < 0 or top < 0 or width <= 0 or height <= 0:
        return None
    if left + width > 1.01 or top + height > 1.01:
        return None
    return left, top, left + width, top + height


def _approved_brand_time_groups(
    keys: list[tuple[float, float]] | set[tuple[float, float]],
    windows: dict[tuple[float, float], list[dict]],
    *,
    scan_start: float,
    coverage_bucket_seconds: float,
    minimum_iou: float = 0.30,
) -> list[list[tuple[float, float]]]:
    """Cluster approved brand matches by time bucket and learned geometry.

    Brand memory stores one record per reviewed item, so using ``memory_key`` as
    the track identity fragments one physical watermark into many fake tracks.
    Geometry is the stable identity required here: a representative is retained
    for every distinct approved region in every coverage bucket. Records without
    a valid learned box conservatively fall back to their exact memory key.
    """
    if coverage_bucket_seconds <= 0:
        raise ValueError("coverage_bucket_seconds must be positive")
    buckets: dict[int, list[dict[str, object]]] = {}
    for key in sorted(keys):
        memory_key = _approved_brand_key(windows[key])
        if memory_key is None:
            continue
        bucket = _coverage_bucket(
            key, scan_start=scan_start,
            coverage_bucket_seconds=coverage_bucket_seconds,
        )
        geometry = _approved_brand_box(windows[key])
        groups = buckets.setdefault(bucket, [])
        for group in groups:
            reference = group["geometry"]
            same_geometry = (
                geometry is not None
                and reference is not None
                and box_iou(geometry, reference) >= minimum_iou
            )
            same_fallback = (
                geometry is None
                and reference is None
                and group["memory_key"] == memory_key
            )
            if same_geometry or same_fallback:
                group["keys"].append(key)
                break
        else:
            groups.append({
                "geometry": geometry,
                "memory_key": memory_key,
                "keys": [key],
            })
    return [
        list(group["keys"])
        for bucket in sorted(buckets)
        for group in buckets[bucket]
    ]


def _candidate_focus_class(frames: list[dict]) -> str:
    """Separate localized overlay evidence from generic full-scene routing.

    A full-frame temporal score is deliberately broad: it can surface an ident,
    but on slowly moving footage it also fires on ordinary movie frames.  Those
    frames are coverage samples for a time bucket, while a regional hit is a
    concrete localization lead and must reach the semantic model individually.
    """
    if _approved_brand_key(frames) is not None:
        return "approved_brand"
    if any(
        str(item.get("features", {}).get("focus_region") or "full")
        not in {"full", "approved_brand_memory"}
        and float(item.get("features", {}).get("regional_score", 0.0))
        >= float(item.get("features", {}).get("full_frame_score", 0.0)) + 0.04
        for item in frames
    ):
        return "regional"
    return "full_frame"


def _coverage_bucket(
    key: tuple[float, float], *, scan_start: float, coverage_bucket_seconds: float,
) -> int:
    return max(0, int((key[0] - scan_start) // coverage_bucket_seconds))


def _full_frame_representatives(
    keys: list[tuple[float, float]],
    windows: dict[tuple[float, float], list[dict]],
    *, maximum_per_bucket: int = 2,
) -> list[tuple[float, float]]:
    """Choose both ident-like and persistence-like evidence per time bucket."""
    if maximum_per_bucket <= 0:
        raise ValueError("maximum_per_bucket must be positive")

    def strongest_features(key: tuple[float, float]) -> dict:
        return max(
            (item.get("features", {}) for item in windows[key]),
            key=lambda value: float(value.get("score", 0.0)),
        )

    selected: list[tuple[float, float]] = []
    remaining = list(keys)
    rankings = (
        lambda key: (
            float(strongest_features(key).get("ident_score", 0.0)),
            float(strongest_features(key).get("score", 0.0)),
            -key[0],
        ),
        lambda key: (
            float(strongest_features(key).get("overlay_score", 0.0)),
            float(strongest_features(key).get("corner_correlation", 0.0)),
            float(strongest_features(key).get("score", 0.0)),
            -key[0],
        ),
    )
    for rank in rankings:
        if len(selected) >= maximum_per_bucket or not remaining:
            break
        winner = max(remaining, key=rank)
        selected.append(winner)
        remaining.remove(winner)
    for key in sorted(
        remaining,
        key=lambda item: (
            -float(strongest_features(item).get("score", 0.0)), item[0],
        ),
    ):
        if len(selected) >= maximum_per_bucket:
            break
        selected.append(key)
    return selected


def candidate_selection_coverage(
    windows: dict[tuple[float, float], list[dict]],
    selected: list[tuple[float, float]],
    *,
    scan_start: float,
    coverage_bucket_seconds: float,
) -> dict[str, object]:
    """Describe semantic coverage without treating coverage samples as misses.

    Regional candidates contain a concrete overlay location and are never
    collapsed. Generic full-frame candidates are represented by two different
    samples per five-minute bucket: one ident-like and one persistence-like.
    This prevents normal low-motion scenes from making a long scan structurally
    impossible while preserving time-distributed checks for full-screen promos.
    """
    selected_set = set(selected)
    novel = {key for key, frames in windows.items() if _approved_brand_key(frames) is None}
    known = set(windows) - novel

    regional = {
        key for key in novel if _candidate_focus_class(windows[key]) == "regional"
    }
    full_frame = novel - regional

    full_frame_groups: dict[int, set[tuple[float, float]]] = {}
    for key in full_frame:
        bucket = _coverage_bucket(
            key, scan_start=scan_start,
            coverage_bucket_seconds=coverage_bucket_seconds,
        )
        full_frame_groups.setdefault(bucket, set()).add(key)
    full_frame_required = {
        bucket: min(2, len(keys)) for bucket, keys in full_frame_groups.items()
    }
    full_frame_selected = {
        bucket: len(keys & selected_set)
        for bucket, keys in full_frame_groups.items()
    }
    missing_full_frame_representatives = sum(
        max(0, required - full_frame_selected.get(bucket, 0))
        for bucket, required in full_frame_required.items()
    )

    known_groups = _approved_brand_time_groups(
        known, windows, scan_start=scan_start,
        coverage_bucket_seconds=coverage_bucket_seconds,
    )
    selected_known_groups = sum(
        bool(set(group) & selected_set) for group in known_groups
    )
    regional_omitted = regional - selected_set
    missing_known_groups = len(known_groups) - selected_known_groups
    return {
        "complete": (
            not regional_omitted
            and missing_full_frame_representatives == 0
            and missing_known_groups == 0
        ),
        "strategy": "all_regional_plus_two_full_frame_and_each_approved_geometry_track_per_time_bucket",
        "novel_candidate_windows": len(novel),
        "novel_candidate_windows_selected": len(novel & selected_set),
        "novel_candidate_windows_omitted": (
            len(regional_omitted) + missing_full_frame_representatives
        ),
        "regional_candidate_windows": len(regional),
        "regional_candidate_windows_selected": len(regional & selected_set),
        "regional_candidate_windows_omitted": len(regional_omitted),
        "full_frame_candidate_windows": len(full_frame),
        "full_frame_windows_selected": len(full_frame & selected_set),
        "full_frame_windows_collapsed": len(full_frame - selected_set),
        "full_frame_time_groups": len(full_frame_groups),
        "full_frame_representatives_required": sum(full_frame_required.values()),
        "full_frame_representatives_missing": missing_full_frame_representatives,
        "approved_brand_candidate_windows": len(known),
        "approved_brand_windows_selected": len(known & selected_set),
        "approved_brand_windows_collapsed": len(known - selected_set),
        "approved_brand_time_groups": len(known_groups),
        "approved_brand_time_groups_selected": selected_known_groups,
        "approved_brand_time_groups_missing": missing_known_groups,
    }


def _add_window_frame(
    windows: dict[tuple[float, float], list[dict]], key: tuple[float, float],
    *, timestamp: float, frame: np.ndarray, features: dict,
) -> None:
    focus_box = features.get("focus_box_analysis")
    focus_jpeg = None
    if isinstance(focus_box, list) and len(focus_box) == 4:
        x1, y1, x2, y2 = (int(value) for value in focus_box)
        focus = frame[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
        if focus.size and str(features.get("focus_region")) != "full":
            focus_jpeg = _jpeg(focus)
    windows.setdefault(key, []).append(
        {
            "timestamp_seconds": timestamp, "jpeg": _jpeg(frame),
            "focus_jpeg": focus_jpeg, "features": features,
        }
    )


def select_candidate_windows(
    windows: dict[tuple[float, float], list[dict]],
    boundary_keys: set[tuple[float, float]],
    max_candidate_windows: int,
    *,
    scan_start: float,
    scan_end: float,
    coverage_bucket_seconds: float = 300.0,
) -> list[tuple[float, float]]:
    """Prioritize localized evidence and sample generic full-scene evidence."""
    if max_candidate_windows <= 0 or coverage_bucket_seconds <= 0:
        raise ValueError("Candidate and coverage limits must be positive")

    def score(key: tuple[float, float]) -> float:
        return max(item["features"]["score"] for item in windows[key])

    selected = sorted(
        (key for key in boundary_keys if key in windows),
        key=lambda key: key[0],
    )[:max_candidate_windows]
    remaining_budget = max_candidate_windows - len(selected)
    if remaining_budget <= 0:
        return selected
    interior = [key for key in windows if key not in boundary_keys]
    bucket_count = max(1, int(np.ceil(max(0.0, scan_end - scan_start) / coverage_bucket_seconds)))
    selected_set = set(selected)

    def bucket_for(key: tuple[float, float]) -> int:
        return min(
            bucket_count - 1,
            max(0, int((key[0] - scan_start) // coverage_bucket_seconds)),
        )

    def add_distributed(candidates: list[tuple[float, float]]) -> None:
        if not candidates or len(selected) >= max_candidate_windows:
            return
        available = max_candidate_windows - len(selected)
        by_bucket: dict[int, list[tuple[float, float]]] = {}
        for key in candidates:
            by_bucket.setdefault(bucket_for(key), []).append(key)
        per_bucket = max(1, available // max(1, len(by_bucket)))
        for bucket in sorted(by_bucket):
            for key in sorted(
                by_bucket[bucket], key=lambda item: (-score(item), item[0])
            )[:per_bucket]:
                if len(selected) >= max_candidate_windows:
                    return
                if key not in selected_set:
                    selected.append(key)
                    selected_set.add(key)
        if len(selected) >= max_candidate_windows:
            return
        for key in sorted(candidates, key=lambda item: (-score(item), item[0])):
            if len(selected) >= max_candidate_windows:
                return
            if key in selected_set:
                continue
            selected.append(key)
            selected_set.add(key)

    novel = [key for key in interior if _approved_brand_key(windows[key]) is None]
    known = [key for key in interior if _approved_brand_key(windows[key]) is not None]
    regional = [
        key for key in novel if _candidate_focus_class(windows[key]) == "regional"
    ]
    regional_set = set(regional)
    full_frame = [key for key in novel if key not in regional_set]

    # A regional hit is a concrete logo-location lead. Do not collapse it into
    # time-bucket coverage, even when a long movie has many such hits.
    add_distributed(regional)

    # A generic full-frame hit has no localized target and frequently comes
    # from ordinary low-motion footage. Send two complementary representatives
    # per time bucket before using spare capacity for additional samples.
    full_frame_by_bucket: dict[int, list[tuple[float, float]]] = {}
    for key in full_frame:
        full_frame_by_bucket.setdefault(bucket_for(key), []).append(key)
    representatives = []
    for bucket in sorted(full_frame_by_bucket):
        representatives.extend(_full_frame_representatives(
            full_frame_by_bucket[bucket], windows, maximum_per_bucket=2,
        ))
    add_distributed(representatives)

    # One representative for each approved geometry track in every five-minute
    # bucket preserves temporal coverage. A memory key identifies one reviewed
    # example, not the physical logo track, so grouping by it fragments a single
    # watermark and can starve later buckets of semantic slots.
    approved_groups = _approved_brand_time_groups(
        known, windows, scan_start=scan_start,
        coverage_bucket_seconds=coverage_bucket_seconds,
    )
    representatives = [
        max(group, key=lambda key: (score(key), -key[0]))
        for group in approved_groups
    ]
    add_distributed(representatives)
    add_distributed(full_frame)
    add_distributed(known)
    return selected


def scan_visual_logos(
    *, project_root: Path, input_path: Path, report_dir: Path, model_path: Path,
    ffmpeg_path: Path, ffprobe_path: Path, device_name: str = "cuda",
    sample_every: float = 2.0, boundary_sample_every: float = 0.25,
    boundary_seconds: float = 30.0, candidate_threshold: float = 0.52,
    window_seconds: float = 5.0, max_candidate_windows: int = 80,
    start_seconds: float = 0.0, duration_seconds_limit: float | None = None,
    exhaustive: bool = False, source_sha256: str | None = None,
    scene_change_threshold: float = 0.22,
    coverage_bucket_seconds: float = 300.0,
    coverage_fallbacks_per_bucket: int = 2,
    routing_workers: int = 1,
) -> dict:
    performance = ScanPerformance()
    if (not isinstance(routing_workers, int) or isinstance(routing_workers, bool)
            or not 1 <= routing_workers <= 8):
        raise ValueError("routing_workers must be an integer from 1 to 8")
    root = project_root.resolve(strict=True)
    input_path = input_path.resolve(strict=True)
    report_dir = _inside((root / "reports").resolve(strict=True), report_dir, "Report directory")
    model_path = _inside((root / "models").resolve(strict=True), model_path, "Model path").resolve(strict=True)
    ffmpeg_path = ffmpeg_path.resolve(strict=True)
    ffprobe_path = ffprobe_path.resolve(strict=True)
    if sample_every <= 0 or boundary_sample_every <= 0 or boundary_seconds < 0:
        raise ValueError("Sampling intervals must be positive and boundary_seconds cannot be negative")
    if not 0 <= candidate_threshold <= 1 or window_seconds <= 0 or max_candidate_windows <= 0:
        raise ValueError("Invalid logo candidate configuration")
    if not 0 <= scene_change_threshold <= 1:
        raise ValueError("scene_change_threshold must be between zero and one")
    if coverage_bucket_seconds <= 0 or coverage_fallbacks_per_bucket <= 0:
        raise ValueError("Coverage fallback settings must be positive")
    require_capacity(root, estimated_job_gb=1.0)
    probe = probe_video(ffprobe_path, input_path)
    full_duration = duration_seconds(probe)
    if start_seconds < 0 or start_seconds >= full_duration:
        raise ValueError("start_seconds must be inside the video")
    scan_duration = min(
        full_duration - start_seconds,
        duration_seconds_limit if duration_seconds_limit is not None else full_duration,
    )
    if scan_duration <= 0:
        raise ValueError("duration_seconds must be positive")
    source_width, source_height = _video_size(probe)
    analysis_width = 320
    analysis_height = max(2, round(source_height * analysis_width / source_width / 2) * 2)
    scan_end = start_seconds + scan_duration
    effective_sample_interval = effective_sample_every(sample_every, exhaustive)
    input_sha256 = performance.call('source_hash', validated_source_sha256, input_path, source_sha256)
    brand_memory = load_brand_memory(root)
    memory_records = [
        item for item in brand_memory.get("records", []) if isinstance(item, dict)
    ]
    memory_rev = memory_revision(brand_memory)
    cache_settings = {
        "sample_every": effective_sample_interval,
        "boundary_sample_every": boundary_sample_every,
        "boundary_seconds": boundary_seconds,
        "candidate_threshold": candidate_threshold,
        "window_seconds": window_seconds,
        "start_seconds": start_seconds,
        "scan_duration": scan_duration,
        "analysis_size": [analysis_width, analysis_height],
        "scene_change_threshold": scene_change_threshold,
        "coverage_bucket_seconds": coverage_bucket_seconds,
        "coverage_fallbacks_per_bucket": coverage_fallbacks_per_bucket,
        "exhaustive": exhaustive,
        "brand_memory_revision": memory_rev,
    }
    cache_key = _routing_cache_key(input_sha256, cache_settings)
    cache_path = _routing_cache_path(root, input_sha256, cache_key)
    cached = None if exhaustive else _read_routing_cache(
        cache_path, source_sha256=input_sha256, cache_key=cache_key,
    )
    windows: dict[tuple[float, float], list[dict]] = {}
    window_sample_counts: dict[tuple[float, float], int] = {}
    boundary_keys: set[tuple[float, float]] = set()
    process = psutil.Process(os.getpid())
    peak_rss = process.memory_info().rss
    started = time.perf_counter()
    frames_scanned = 0
    heuristic_hits = 0
    coverage_fallback_count = 0
    scene_routed_count = 0
    routing_cache_hit = cached is not None
    parallel_routing_metrics = None

    boundary_ranges: list[tuple[float, float]] = []
    boundary_transitions: list[tuple[float, float]] = []
    for range_start, range_end in (
        (0.0, min(full_duration, boundary_seconds)),
        (max(0.0, full_duration - boundary_seconds), full_duration),
    ):
        left, right = max(start_seconds, range_start), min(scan_end, range_end)
        if right > left and not any(abs(left - a) < 0.01 and abs(right - b) < 0.01 for a, b in boundary_ranges):
            boundary_ranges.append((left, right))
    if cached is not None:
        windows = cached["decoded_windows"]
        window_sample_counts = cached["decoded_counts"]
        boundary_keys = cached["decoded_boundary_keys"]
        boundary_transitions = [
            (float(item[0]), float(item[1]))
            for item in cached.get("boundary_transitions", [])
        ]
        frames_scanned = int(cached.get("frames_scanned", 0))
        heuristic_hits = int(cached.get("heuristic_hits", 0))
        coverage_fallback_count = int(cached.get("coverage_fallback_count", 0))
        scene_routed_count = int(cached.get("scene_routed_count", 0))
        print(f"Visual-logo routing cache hit: {cache_path}", flush=True)
    else:
        routing_pool = RoutingPool(routing_workers, memory_records) if routing_workers > 1 else None
        try:
            for left, right in boundary_ranges:
                boundary_previous = None
                boundary_frames = performance.iterate("frame_pipe_wait", _iter_frames(
                    ffmpeg_path=ffmpeg_path, input_path=input_path, start=left,
                    duration=right - left, sample_every=boundary_sample_every,
                    width=analysis_width, height=analysis_height,
                ))
                for timestamp, frame, features in _iter_routed_frames(
                    boundary_frames, memory_records, performance, routing_pool,
                ):
                    if boundary_previous is not None:
                        boundary_transitions.append(
                            (timestamp, _frame_change_score(boundary_previous, frame))
                        )
                    key = _window_key(timestamp, window_seconds)
                    key = (key[0], min(full_duration, key[1]))
                    boundary_keys.add(key)
                    _add_window_frame(windows, key, timestamp=timestamp, frame=frame, features=features)
                    window_sample_counts[key] = window_sample_counts.get(key, 0) + 1
                    frames_scanned += 1
                    boundary_previous = frame

            previous = None
            coverage_candidates: dict[int, list[dict]] = {}
            sampled_frames = performance.iterate("frame_pipe_wait", _iter_frames(
                ffmpeg_path=ffmpeg_path, input_path=input_path, start=start_seconds,
                duration=scan_duration, sample_every=effective_sample_interval,
                width=analysis_width, height=analysis_height,
            ))
            for timestamp, frame, features in _iter_routed_frames(
                sampled_frames, memory_records, performance, routing_pool,
            ):
                in_dense_boundary = any(left <= timestamp < right for left, right in boundary_ranges)
                change_score = _frame_change_score(previous, frame) if previous is not None else 0.0
                scene_route = (
                    change_score >= scene_change_threshold
                    and float(features["score"]) >= max(0.30, candidate_threshold * 0.70)
                )
                candidate = exhaustive or float(features["score"]) >= candidate_threshold or scene_route
                key = _window_key(timestamp, window_seconds)
                key = (key[0], min(full_duration, key[1]))
                if not in_dense_boundary and candidate:
                    _add_window_frame(windows, key, timestamp=timestamp, frame=frame, features=features)
                    window_sample_counts[key] = window_sample_counts.get(key, 0) + 1
                    heuristic_hits += 1
                    scene_routed_count += int(scene_route and float(features["score"]) < candidate_threshold)
                elif not in_dense_boundary:
                    bucket = max(0, int((timestamp - start_seconds) // coverage_bucket_seconds))
                    retain_coverage_candidate(
                        coverage_candidates, bucket,
                        {
                            "timestamp_seconds": timestamp,
                            "frame": frame.copy(),
                            "features": features,
                        },
                        maximum_per_bucket=coverage_fallbacks_per_bucket,
                    )
                previous = frame
                frames_scanned += 1
                if frames_scanned % 500 == 0:
                    print(f"Visual-logo candidate scan: {frames_scanned} frames", flush=True)
                peak_rss = max(peak_rss, process.memory_info().rss)

            for candidates in coverage_candidates.values():
                for candidate in candidates:
                    timestamp = float(candidate["timestamp_seconds"])
                    key = _window_key(timestamp, window_seconds)
                    key = (key[0], min(full_duration, key[1]))
                    if key in windows:
                        continue
                    _add_window_frame(
                        windows, key, timestamp=timestamp, frame=candidate["frame"],
                        features=candidate["features"],
                    )
                    window_sample_counts[key] = 1
                    coverage_fallback_count += 1
            if not exhaustive:
                _write_routing_cache(
                    cache_path, source_sha256=input_sha256, cache_key=cache_key,
                    windows=windows, window_sample_counts=window_sample_counts,
                    boundary_keys=boundary_keys,
                    boundary_transitions=boundary_transitions,
                    frames_scanned=frames_scanned, heuristic_hits=heuristic_hits,
                    coverage_fallback_count=coverage_fallback_count,
                    scene_routed_count=scene_routed_count,
                )
        finally:
            if routing_pool is not None:
                routing_pool.close()
                parallel_routing_metrics = routing_pool.metrics()

    candidate_windows_before_limit = len(windows)
    effective_candidate_limit = adaptive_candidate_budget(
        scan_duration=scan_duration,
        requested_maximum=max_candidate_windows,
        boundary_window_count=len(boundary_keys),
    )
    ranked_keys = (
        sorted(windows)
        if exhaustive else select_candidate_windows(
            windows,
            boundary_keys,
            effective_candidate_limit,
            scan_start=start_seconds,
            scan_end=scan_end,
            coverage_bucket_seconds=coverage_bucket_seconds,
        )
    )
    selection_coverage = candidate_selection_coverage(
        windows,
        ranked_keys,
        scan_start=start_seconds,
        coverage_bucket_seconds=coverage_bucket_seconds,
    )

    with performance.measure('model_load'):
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor

        if device_name == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is not available")
        processor = AutoProcessor.from_pretrained(
            model_path, local_files_only=True, min_pixels=168 * 168, max_pixels=320 * 320
        )
        dtype = torch.float16 if device_name == "cuda" else torch.float32
        model = AutoModelForImageTextToText.from_pretrained(
            model_path, local_files_only=True, dtype=dtype
        ).to(device_name)
        model.eval()
        if device_name == "cuda":
            torch.cuda.reset_peak_memory_stats()

    report_dir.mkdir(parents=True, exist_ok=True)
    thumbnails_dir = report_dir / "thumbnails"
    thumbnails_dir.mkdir(parents=True, exist_ok=True)
    audit_thumbnails_dir = report_dir / "audit-thumbnails"
    audit_thumbnails_dir.mkdir(parents=True, exist_ok=True)
    intervals = []
    rejected = []
    answer_counts = {"CONFIRMED": 0, "REJECTED": 0, "UNCERTAIN": 0}
    memory_match_count = 0
    with tempfile.TemporaryDirectory(prefix="biliflow-logo-vlm-") as temporary:
        temporary_root = Path(temporary)
        for index, key in enumerate(sorted(ranked_keys), start=1):
            candidates = select_window_evidence(windows[key], maximum=2)
            strongest = candidates[0]
            frame_paths = []
            for frame_index, candidate in enumerate(candidates, start=1):
                path = temporary_root / f"window-{index:04d}-frame-{frame_index}.jpg"
                path.write_bytes(candidate["jpeg"])
                frame_paths.append(path)
                if candidate.get("focus_jpeg") and len(frame_paths) < 5:
                    crop_path = temporary_root / f"window-{index:04d}-frame-{frame_index}-crop.jpg"
                    crop_path.write_bytes(candidate["focus_jpeg"])
                    frame_paths.append(crop_path)
                if len(frame_paths) >= 3:
                    break
            def ask(prompt: str) -> str:
                messages = [{
                    "role": "user",
                    "content": (
                        [{"type": "image", "path": str(path)} for path in frame_paths]
                        + [{"type": "text", "text": prompt}]
                    ),
                }]
                with performance.measure('model_step'):
                    inputs = processor.apply_chat_template(
                        messages, add_generation_prompt=True, tokenize=True,
                        return_dict=True, return_tensors="pt",
                    ).to(model.device)
                    with torch.inference_mode():
                        generated = model.generate(**inputs, do_sample=False, max_new_tokens=20)
                    return processor.decode(
                        generated[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True
                    ).strip()

            memory_match = strongest["features"].get("brand_memory")
            confirmation_source = "qwen_local"
            initial_answer = None
            if (
                isinstance(memory_match, dict)
                and float(memory_match.get("similarity", 0.0))
                >= APPROVED_BRAND_MEMORY_SIMILARITY
                and memory_match.get("memory_class") == "brand"
            ):
                state = "CONFIRMED"
                labels = [str(value) for value in memory_match.get("labels", []) if str(value).strip()]
                brand_name = labels[0] if labels else "Known approved external brand"
                answer = f"MEMORY_MATCH | {brand_name}"
                confirmation_source = "approved_brand_memory"
                memory_match_count += 1
            else:
                answer = ask(VISUAL_LOGO_PROMPT)
                if is_instruction_echo(answer):
                    initial_answer = answer
                    answer = ask(VISUAL_LOGO_RETRY_PROMPT)
                state, brand_name = parse_logo_answer(answer)
            boundary_scene = None
            boundary_scene_answer = None
            if key in boundary_keys:
                boundary_scene_answer = ask(BOUNDARY_SCENE_PROMPT)
                boundary_scene = parse_boundary_scene_answer(boundary_scene_answer)
                if boundary_scene == "PROMO_FULL_FRAME" and state == "REJECTED":
                    state = "UNCERTAIN"
                    brand_name = "Full-frame promotional material"
            answer_counts[state] += 1
            if confirmation_source == "approved_brand_memory":
                reason = "A visual signature from an earlier human-approved brand item matched; review is still required"
            elif state == "CONFIRMED":
                reason = "Local visual-language model confirmed branding/logo evidence"
            elif state == "UNCERTAIN":
                reason = "Visual-language answer was uncertain; human review required"
            else:
                reason = "Visual-language model rejected this window; retained in exhaustive audit"
            record = {
                "start_seconds": round(max(start_seconds, key[0]), 3),
                "end_seconds": round(min(scan_end, key[1]), 3),
                "max_score": strongest["features"]["score"],
                "sample_count": int(window_sample_counts.get(key, len(windows[key]))),
                "predicted_label": brand_name or "Visual brand/logo candidate",
                "priority": "high" if state == "CONFIRMED" else "context",
                "reason": reason,
                "strongest_timestamp_seconds": round(
                    float(strongest["timestamp_seconds"]), 3
                ),
                "visual_logo_confirmation": {
                    "state": state, "answer": answer,
                    "confirmation_source": confirmation_source,
                    "initial_instruction_echo": initial_answer,
                    "retry_count": 1 if initial_answer is not None else 0,
                    "frames_sampled": len(frame_paths),
                    "boundary_window": key in boundary_keys,
                    "features": strongest["features"],
                },
            }
            if boundary_scene is not None:
                record["visual_logo_confirmation"]["boundary_scene_context"] = {
                    "state": boundary_scene,
                    "answer": boundary_scene_answer,
                    "model": "qwen_local",
                }
            if boundary_scene == "PROMO_FULL_FRAME":
                record["candidate_type"] = (
                    "opening_promotion"
                    if key[0] < start_seconds + boundary_seconds else "closing_promotion"
                )
                record["suggested_decision"] = "CUT"
                record["priority"] = "high"
                record["reason"] = (
                    "Local visual-language model classified the boundary window as full-frame promotional material"
                )
            audit_name = f"window-{index:04d}-{strongest['timestamp_seconds']:.3f}s.jpg"
            (audit_thumbnails_dir / audit_name).write_bytes(strongest["jpeg"])
            record["audit_frame"] = f"audit-thumbnails/{audit_name}"
            if state == "REJECTED":
                if key in boundary_keys and key[0] <= start_seconds + window_seconds + 0.05:
                    name = f"opening-boundary-{strongest['timestamp_seconds']:.3f}s.jpg"
                    (thumbnails_dir / name).write_bytes(strongest["jpeg"])
                    record["strongest_frame"] = f"thumbnails/{name}"
                rejected.append(record)
            else:
                name = f"logo-{len(intervals) + 1:04d}-{strongest['timestamp_seconds']:.3f}s.jpg"
                (thumbnails_dir / name).write_bytes(strongest["jpeg"])
                record["strongest_frame"] = f"thumbnails/{name}"
                intervals.append(record)
            print(
                f"Visual-logo VLM {index}/{len(ranked_keys)} {key[0]:.1f}-{key[1]:.1f}s: {state} {answer}",
                flush=True,
            )

    intervals = consolidate_opening_promotion_intervals(
        intervals, scan_start=start_seconds,
        boundary_seconds=boundary_seconds, window_seconds=window_seconds,
    )
    intervals, rejected = consolidate_end_card_intervals(
        intervals, rejected, duration=full_duration,
        boundary_seconds=boundary_seconds, window_seconds=window_seconds,
        transition_samples=boundary_transitions,
    )
    intervals = consolidate_persistent_overlay_intervals(
        intervals, duration=full_duration, window_seconds=window_seconds,
        transition_samples=boundary_transitions,
    )
    if not any(float(item["start_seconds"]) < start_seconds + window_seconds for item in intervals):
        opening = next(
            (
                item for item in sorted(rejected, key=lambda value: float(value["start_seconds"]))
                if item.get("strongest_frame")
                and float(item["start_seconds"]) <= start_seconds + 0.05
            ),
            None,
        )
        if opening is not None:
            rejected.remove(opening)
            opening.update({
                "priority": "context",
                "candidate_type": "opening_boundary",
                "predicted_label": "Opening boundary review",
                "reason": "The first video window is retained once so external intros are not silently missed",
                "visual_logo_confirmation": {
                    **opening.get("visual_logo_confirmation", {}),
                    "state": "UNCERTAIN",
                    "promoted_from_rejected_boundary": True,
                },
            })
            intervals.append(opening)
            intervals.sort(key=lambda item: float(item["start_seconds"]))

    elapsed = time.perf_counter() - started
    payload = {
        "schema_version": 1,
        "scan_type": "visual_logo",
        "status": "REVIEW_REQUIRED" if intervals else "COMPLETED",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "input": str(input_path),
        "input_size_bytes": input_path.stat().st_size,
        "input_sha256": input_sha256,
        "duration_seconds": full_duration,
        "analysis_size": [analysis_width, analysis_height],
        "scan_start_seconds": start_seconds,
        "scan_duration_seconds": scan_duration,
        "sampling": {
            "sample_every_seconds": effective_sample_interval,
            "requested_sample_every_seconds": sample_every,
            "boundary_sample_every_seconds": boundary_sample_every,
            "boundary_seconds": boundary_seconds,
            "window_seconds": window_seconds,
            "candidate_threshold": candidate_threshold,
            "max_candidate_windows": max_candidate_windows,
            "effective_candidate_windows_limit": effective_candidate_limit,
            "coverage_mode": "exhaustive" if exhaustive else "ranked",
            "scene_change_threshold": scene_change_threshold,
            "coverage_bucket_seconds": coverage_bucket_seconds,
            "coverage_fallbacks_per_bucket": coverage_fallbacks_per_bucket,
        },
        "prompt": VISUAL_LOGO_PROMPT,
        "frames_scanned": frames_scanned,
        "heuristic_hits": heuristic_hits,
        "scene_routed_windows": scene_routed_count,
        "coverage_fallback_windows": coverage_fallback_count,
        "candidate_windows": len(ranked_keys),
        "candidate_windows_before_limit": candidate_windows_before_limit,
        "candidate_windows_omitted": max(0, candidate_windows_before_limit - len(ranked_keys)),
        "candidate_selection_coverage": selection_coverage,
        "answer_counts": answer_counts,
        "brand_memory": {
            "revision": memory_rev,
            "record_count": len(memory_records),
            "high_confidence_matches": memory_match_count,
            "automatic_edit": False,
        },
        "routing_cache": {
            "hit": routing_cache_hit,
            "key": cache_key,
            "path": cache_path.relative_to(root).as_posix(),
            "bytes": cache_path.stat().st_size if cache_path.exists() else 0,
        },
        "intervals": intervals,
        "rejected_windows": rejected,
        "model": json.loads((model_path / "manifest.json").read_text(encoding="utf-8")),
        "metrics": {
            "performance": performance.snapshot(),
            "routing_workers": routing_workers,
            "parallel_routing": parallel_routing_metrics,
            "elapsed_seconds": round(elapsed, 3),
            "video_seconds_per_processing_second": round(scan_duration / elapsed, 3) if elapsed else None,
            "peak_process_ram_bytes": peak_rss,
            "peak_cuda_memory_bytes": int(torch.cuda.max_memory_allocated()) if device_name == "cuda" else 0,
        },
        "safety": {
            "automatic_edit": False,
            "external_media_upload": False,
            "note": "Every retained visual-logo interval requires human confirmation before editing. Exhaustive audit frames are evidence only and never authorize an edit.",
        },
    }
    referenced_thumbnails = {
        Path(str(interval["strongest_frame"])).name
        for interval in intervals
        if interval.get("strongest_frame")
    }
    referenced_thumbnails.update(
        Path(str(value)).name
        for interval in intervals
        for value in interval.get("supporting_frames", [])
    )
    for thumbnail in thumbnails_dir.glob("logo-*.jpg"):
        if thumbnail.name not in referenced_thumbnails:
            thumbnail.unlink(missing_ok=True)
    referenced_audit_thumbnails = {
        Path(str(item["audit_frame"])).name
        for item in [*intervals, *rejected]
        if item.get("audit_frame")
    }
    for thumbnail in audit_thumbnails_dir.glob("window-*.jpg"):
        if thumbnail.name not in referenced_audit_thumbnails:
            thumbnail.unlink(missing_ok=True)
    temporary_output = report_dir / "scan.json.tmp"
    temporary_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_output.replace(report_dir / "scan.json")
    if exhaustive:
        write_visual_logo_audit_html(report_dir, [*intervals, *rejected])
    return payload
