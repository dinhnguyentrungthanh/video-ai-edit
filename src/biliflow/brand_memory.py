from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import shutil
import sqlite3
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import quote

import cv2
import numpy as np


MEMORY_PATH = Path("state/brand-memory.json")
MEMORY_SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _empty_memory() -> dict[str, Any]:
    return {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "updated_at": _now(),
        "records": [],
        "safety": {
            "automatic_edit": False,
            "purpose": "Candidate routing only; every match still requires review",
        },
    }


def load_brand_memory(root: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    path = root / MEMORY_PATH
    if not path.exists():
        return _empty_memory()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != MEMORY_SCHEMA_VERSION:
        raise ValueError("Unsupported brand-memory schema")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("Brand-memory records must be a list")
    return payload


def memory_revision(payload: dict[str, Any]) -> str:
    """Return a stable revision for cache invalidation without exposing images."""
    compact = [
        {
            "key": item.get("key"),
            "phash": item.get("phash"),
            "relative_box": item.get("relative_box"),
            "decision": item.get("decision"),
            "memory_class": item.get("memory_class"),
        }
        for item in payload.get("records", [])
        if isinstance(item, dict)
    ]
    raw = json.dumps(compact, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def perceptual_hash(image_rgb: np.ndarray) -> str:
    """Create a compact DCT hash that tolerates resizing and mild compression."""
    if image_rgb.ndim not in {2, 3} or image_rgb.size == 0:
        raise ValueError("A non-empty grayscale or RGB image is required")
    if image_rgb.ndim == 3:
        gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
    else:
        gray = image_rgb
    resized = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    low = cv2.dct(resized)[:8, :8]
    median = float(np.median(low.reshape(-1)[1:]))
    bits = (low >= median).reshape(-1)
    value = 0
    for enabled in bits:
        value = (value << 1) | int(bool(enabled))
    return f"{value:016x}"


def hash_similarity(first: str, second: str) -> float:
    try:
        distance = (int(first, 16) ^ int(second, 16)).bit_count()
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, 1.0 - distance / 64.0)


def _relative_crop(
    image_rgb: np.ndarray, relative_box: Iterable[float] | None,
) -> np.ndarray:
    if relative_box is None:
        return image_rgb
    values = tuple(float(value) for value in relative_box)
    if len(values) != 4:
        return image_rgb
    height, width = image_rgb.shape[:2]
    left, top, box_width, box_height = values
    padding_x = box_width * 0.08
    padding_y = box_height * 0.08
    x1 = max(0, round((left - padding_x) * width))
    y1 = max(0, round((top - padding_y) * height))
    x2 = min(width, round((left + box_width + padding_x) * width))
    y2 = min(height, round((top + box_height + padding_y) * height))
    crop = image_rgb[y1:y2, x1:x2]
    return crop if crop.size else image_rgb


def _coerce_relative_box(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        left, top, width, height = (float(part) for part in value)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0 or left < 0 or top < 0:
        return None
    if left + width > 1.01 or top + height > 1.01:
        return None
    return left, top, width, height


def relative_box_to_region(
    relative_box: object, image_size: tuple[int, int],
) -> dict[str, int] | None:
    """Convert one normalized approved region into clipped image pixels."""
    values = _coerce_relative_box(relative_box)
    width, height = (int(image_size[0]), int(image_size[1]))
    if values is None or width <= 0 or height <= 0:
        return None
    left, top, box_width, box_height = values
    x1 = max(0, min(width - 1, round(left * width)))
    y1 = max(0, min(height - 1, round(top * height)))
    x2 = max(x1 + 1, min(width, round((left + box_width) * width)))
    y2 = max(y1 + 1, min(height, round((top + box_height) * height)))
    return {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1}


def approved_brand_memory_region(
    match: dict[str, Any] | None, image_size: tuple[int, int],
    *, minimum_similarity: float = 0.94,
) -> dict[str, int] | None:
    """Return the exact learned brand region only for a strong positive match."""
    if (
        not isinstance(match, dict)
        or match.get("memory_class") != "brand"
        or float(match.get("similarity", 0.0)) < minimum_similarity
    ):
        return None
    return relative_box_to_region(match.get("relative_box"), image_size)


def tighten_brand_foreground_region(
    frame_rgb: np.ndarray, region: dict[str, int],
) -> dict[str, int]:
    """Trim empty padding from a learned logo box using the current frame.

    Human-approved brand memory supplies the safe outer box.  The logo itself
    can occupy only part of that box, especially when an OCR/grounding model
    included generous vertical padding.  Local colour contrast identifies the
    foreground inside that outer box.  Conservative size gates make this a
    no-op on noisy or ambiguous backgrounds instead of risking a clipped logo.
    """
    if frame_rgb.ndim != 3 or frame_rgb.shape[2] != 3 or frame_rgb.size == 0:
        return dict(region)
    frame_height, frame_width = frame_rgb.shape[:2]
    try:
        x = max(0, int(region["x"]))
        y = max(0, int(region["y"]))
        width = min(int(region["width"]), frame_width - x)
        height = min(int(region["height"]), frame_height - y)
    except (KeyError, TypeError, ValueError):
        return dict(region)
    original = {"x": x, "y": y, "width": width, "height": height}
    if width < 32 or height < 18:
        return original
    roi = frame_rgb[y:y + height, x:x + width]
    if roi.size == 0:
        return original

    lab = cv2.cvtColor(roi, cv2.COLOR_RGB2LAB).astype(np.float32)
    local_background = cv2.GaussianBlur(
        lab, (0, 0), max(2.0, height * 0.12)
    )
    local_contrast = np.linalg.norm(lab - local_background, axis=2)
    border_width = max(2, round(min(width, height) * 0.08))
    border = np.concatenate((
        lab[:border_width].reshape(-1, 3),
        lab[-border_width:].reshape(-1, 3),
        lab[:, :border_width].reshape(-1, 3),
        lab[:, -border_width:].reshape(-1, 3),
    ))
    border_colour = np.median(border, axis=0)
    border_distance = np.linalg.norm(lab - border_colour, axis=2)
    local_threshold = max(8.0, float(np.percentile(local_contrast, 75)))
    colour_threshold = max(14.0, float(np.percentile(border_distance, 80)))
    mask = (
        (local_contrast >= local_threshold)
        & (border_distance >= colour_threshold)
    ).astype(np.uint8) * 255
    kernel_size = max(3, round(height * 0.04))
    if kernel_size % 2 == 0:
        kernel_size += 1
    kernel = np.ones((kernel_size, kernel_size), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8))
    active_y, active_x = np.where(mask > 0)
    active_ratio = len(active_x) / max(1, width * height)
    if not len(active_x) or not 0.008 <= active_ratio <= 0.60:
        return original

    padding = max(2, round(min(width, height) * 0.04))
    left = max(0, int(active_x.min()) - padding)
    top = max(0, int(active_y.min()) - padding)
    right = min(width, int(active_x.max()) + 1 + padding)
    bottom = min(height, int(active_y.max()) + 1 + padding)
    refined_width, refined_height = right - left, bottom - top
    area_ratio = (refined_width * refined_height) / max(1, width * height)
    if (
        refined_width < width * 0.35
        or refined_height < height * 0.25
        or area_ratio > 0.90
    ):
        return original
    return {
        "x": x + left,
        "y": y + top,
        "width": refined_width,
        "height": refined_height,
    }


def _region_geometry_compatible(
    current: tuple[float, float, float, float],
    learned: tuple[float, float, float, float],
) -> bool:
    """Reject pHash lookalikes that occupy a different part or scale of a frame."""
    cx, cy = current[0] + current[2] / 2, current[1] + current[3] / 2
    lx, ly = learned[0] + learned[2] / 2, learned[1] + learned[3] / 2
    horizontal_tolerance = max(0.04, 0.75 * max(current[2], learned[2]))
    vertical_tolerance = max(0.04, 0.75 * max(current[3], learned[3]))
    if abs(cx - lx) > horizontal_tolerance or abs(cy - ly) > vertical_tolerance:
        return False
    current_area = current[2] * current[3]
    learned_area = learned[2] * learned[3]
    area_ratio = current_area / max(learned_area, 1e-9)
    current_aspect = current[2] / max(current[3], 1e-9)
    learned_aspect = learned[2] / max(learned[3], 1e-9)
    aspect_ratio = current_aspect / max(learned_aspect, 1e-9)
    return 0.30 <= area_ratio <= 3.34 and 0.40 <= aspect_ratio <= 2.50


def match_brand_memory(
    frame_rgb: np.ndarray, records: Iterable[dict[str, Any]],
    *, minimum_similarity: float = 0.78,
) -> dict[str, Any] | None:
    """Match approved visual signatures without carrying edit decisions forward."""
    if not 0 <= minimum_similarity <= 1:
        raise ValueError("minimum_similarity must be between zero and one")
    best: tuple[float, dict[str, Any]] | None = None
    hashes: dict[tuple[float, float, float, float] | None, str] = {}
    for record in records:
        if record.get("decision") not in {"KEEP", "BLUR", "CUT"}:
            continue
        key = _coerce_relative_box(record.get("relative_box"))
        # Full-frame CUT/scene decisions are not logo signatures. Only a
        # learned region can route a frame into the brand-localization path.
        if key is None:
            continue
        if key not in hashes:
            hashes[key] = perceptual_hash(_relative_crop(frame_rgb, key))
        similarity = hash_similarity(hashes[key], str(record.get("phash", "")))
        if best is None or similarity > best[0]:
            best = similarity, record
    if best is None or best[0] < minimum_similarity:
        return None
    record = best[1]
    selected_box = _coerce_relative_box(record.get("relative_box"))
    if selected_box is None:
        return None
    return {
        "similarity": round(best[0], 6),
        "memory_key": record.get("key"),
        "labels": list(record.get("labels") or []),
        "candidate_type": record.get("candidate_type"),
        "memory_class": record.get(
            "memory_class",
            "non_brand" if record.get("decision") == "KEEP" else "brand",
        ),
        "source_sha256": record.get("source_sha256"),
        "review_item_id": record.get("review_item_id"),
        "relative_box": list(selected_box),
        "automatic_edit": False,
    }


def match_region_memory(
    frame_rgb: np.ndarray, relative_box: Iterable[float],
    records: Iterable[dict[str, Any]], *, minimum_similarity: float = 0.84,
) -> dict[str, Any] | None:
    """Match one localized region against approved brand and non-brand crops."""
    if not 0 <= minimum_similarity <= 1:
        raise ValueError("minimum_similarity must be between zero and one")
    current_box = _coerce_relative_box(relative_box)
    if current_box is None:
        return None
    current_hash = perceptual_hash(_relative_crop(frame_rgb, current_box))
    best: tuple[float, dict[str, Any]] | None = None
    for record in records:
        if record.get("decision") not in {"KEEP", "BLUR", "CUT"}:
            continue
        learned_box = _coerce_relative_box(record.get("relative_box"))
        if learned_box is None or not _region_geometry_compatible(current_box, learned_box):
            continue
        similarity = hash_similarity(current_hash, str(record.get("phash", "")))
        if best is None or similarity > best[0]:
            best = similarity, record
    if best is None or best[0] < minimum_similarity:
        return None
    record = best[1]
    return {
        "similarity": round(best[0], 6),
        "memory_key": record.get("key"),
        "labels": list(record.get("labels") or []),
        "candidate_type": record.get("candidate_type"),
        "memory_class": record.get(
            "memory_class",
            "non_brand" if record.get("decision") == "KEEP" else "brand",
        ),
        "source_sha256": record.get("source_sha256"),
        "review_item_id": record.get("review_item_id"),
        "relative_box": list(_coerce_relative_box(record.get("relative_box")) or ()),
        "automatic_edit": False,
    }


def _load_preview(root: Path, item: dict[str, Any]) -> tuple[np.ndarray, str] | None:
    for relative in item.get("preview_images") or []:
        try:
            path = (root / str(relative)).resolve(strict=True)
            if root != path and root not in path.parents:
                continue
            # cv2.imread cannot reliably open Unicode paths on Windows.
            encoded = np.fromfile(path, dtype=np.uint8)
            bgr = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
            if bgr is not None and bgr.size:
                return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), path.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
    return None


def _relative_decision_box(item: dict[str, Any], image: np.ndarray) -> list[float] | None:
    region = item.get("decision_region_source_pixels") or item.get(
        "suggested_region_source_pixels"
    )
    if not isinstance(region, dict):
        return None
    source_size = item.get("source_frame_size")
    if isinstance(source_size, list) and len(source_size) == 2:
        width, height = float(source_size[0]), float(source_size[1])
    else:
        height, width = image.shape[:2]
        right = int(region.get("x", 0)) + int(region.get("width", 0))
        bottom = int(region.get("y", 0)) + int(region.get("height", 0))
        if right > width or bottom > height:
            return None
    if width <= 0 or height <= 0:
        return None
    return [
        round(int(region["x"]) / width, 6),
        round(int(region["y"]) / height, 6),
        round(int(region["width"]) / width, 6),
        round(int(region["height"]) / height, 6),
    ]


# A repeated identical decision must leave state/brand-memory.json byte for
# byte unchanged: the visual_logo/localize_logo stage cache key hashes that
# file, so a rewrite that only bumps timestamps would discard valid caches.
_RECORD_IDENTITY_FIELDS = (
    "key", "source_sha256", "review_item_id", "decision", "memory_class",
    "labels", "candidate_type", "relative_box", "phash", "preview",
    "automatic_edit",
)


def _record_identity(record: dict[str, Any]) -> dict[str, Any]:
    """Everything a record says except its own ``created_at`` timestamp."""
    return {field: record.get(field) for field in _RECORD_IDENTITY_FIELDS}


def remember_review_item(
    root: Path, queue: dict[str, Any], item: dict[str, Any],
    *, skip_unchanged: bool = True,
) -> dict[str, Any] | None:
    """Persist a local visual signature from an explicit human decision.

    The memory file is rewritten only when its records change. A decision that
    leaves no signature (text, scene-level KEEP/CUT) and replaces nothing, or a
    repeated decision whose new record equals the stored one apart from
    ``created_at``, keeps the file and its ``updated_at`` untouched.
    """
    root = root.resolve(strict=True)
    source_sha256 = str(queue.get("source", {}).get("sha256", ""))
    key = f"{source_sha256}:{item.get('id', '')}"
    payload = load_brand_memory(root)
    stored_records = list(payload["records"])
    previous = [record for record in stored_records if record.get("key") == key]
    payload["records"] = [record for record in stored_records if record.get("key") != key]
    if item.get("category") != "visual_logo" or item.get("decision") not in {
        "KEEP", "BLUR", "CUT"
    }:
        if previous or not skip_unchanged:
            payload["updated_at"] = _now()
            _write_json(root / MEMORY_PATH, payload)
        return None
    preview = _load_preview(root, item)
    if preview is None:
        return None
    image, preview_path = preview
    relative_box = _relative_decision_box(item, image)
    if relative_box is None:
        # Scene-level CUT/KEEP decisions must not become logo signatures.
        if previous or not skip_unchanged:
            payload["updated_at"] = _now()
            _write_json(root / MEMORY_PATH, payload)
        return None
    crop = _relative_crop(image, relative_box)
    record = {
        "key": key,
        "source_sha256": source_sha256,
        "review_item_id": item.get("id"),
        "decision": item.get("decision"),
        "memory_class": (
            "non_brand" if item.get("decision") == "KEEP" else "brand"
        ),
        "labels": list(item.get("labels") or []),
        "candidate_type": item.get("candidate_type"),
        "relative_box": relative_box,
        "phash": perceptual_hash(crop),
        "preview": preview_path,
        "created_at": _now(),
        "automatic_edit": False,
    }
    if (
        skip_unchanged and len(previous) == 1
        and _record_identity(previous[0]) == _record_identity(record)
    ):
        return previous[0]
    payload["records"].append(record)
    payload["updated_at"] = _now()
    _write_json(root / MEMORY_PATH, payload)
    return record


def forget_review_item(root: Path, queue: dict[str, Any], item_id: str) -> bool:
    root = root.resolve(strict=True)
    source_sha256 = str(queue.get("source", {}).get("sha256", ""))
    key = f"{source_sha256}:{item_id}"
    payload = load_brand_memory(root)
    before = len(payload["records"])
    payload["records"] = [record for record in payload["records"] if record.get("key") != key]
    if len(payload["records"]) == before:
        return False
    payload["updated_at"] = _now()
    _write_json(root / MEMORY_PATH, payload)
    return True


# ---------------------------------------------------------------------------
# Studio-logo memory (user decision 2026-10-01, R3b).
#
# A separate file from the brand memory above: brand records drive logo BLUR
# routing, studio records only remember an opening/closing studio ident the user
# explicitly confirmed with "Đây là logo hãng phim — giữ & nhớ". A later card may
# go to the optional list only when EVERY preview frame repeats a record twice:
#   * 64-bit full-frame pHash >= 0.95 (static idents repeat at 1.000, the best
#     non-ident frame of three films reached 0.781,
#     temp/next/review-load/studio-ident-phash.json), and
#   * a 32x18 colour grid whose largest cell difference is <= 20. The pHash alone
#     barely notices a small overlay (a corner URL on the Toho ident still scores
#     1.000, a banner over the bottom 10-15 % of the WB ident 0.969); the grid
#     does. Measured on the 320x180 previews: the same ident across Conan 20/21
#     and every Troy run differs by at most 6, a JPEG q40 re-encode by 14, a
#     5-px corner text by 23-26, every URL, banner or box tried by >= 36.
# Text inside the ident is checked separately (review_workflow: any OCR line in
# the window that the confirmed ident did not show keeps the card required).
# Nothing is ever edited automatically.
#
# Schema 2 (user decision 2026-10-02, temp/studio-mask-design/design.json):
#   * frames: every frame of the card's window, decoded again from the source
#     with the visual-logo scanner's own pipeline (cpu, 320 px, bilinear, JPEG
#     q88) at the native frame rate and self-checked byte for byte against the
#     card's preview. Clean encodes of a series run 0.15-0.23 s behind the
#     remembered episode; native-fps frames close that gap (grid 1-5).
#   * ignored_regions: the persistent-overlay watermark regions of the same
#     source the user decided BLUR. They are filled with grey 128 in BOTH the
#     stored and the candidate frame before the pHash and grid; an overlay
#     anywhere else still breaks the match. Content placed entirely inside an
#     ignored region passes the picture check by design: text there is left to
#     the mandatory OCR window-text check, but a non-text graphic there (a logo,
#     QR code or image banner) is NOT seen by OCR; only the visual-logo
#     scanner's own region proposals (full_frame_logo_ad_evidence) can still
#     flag it. Within the user's decision: those areas are ordinary picture in
#     a clean episode.
#   * a stored frame must carry picture outside the mask (grid range > 2 x the
#     cell limit): a black fade frame matched ~190 black cards of other films.
# Schema-1 records (no ``frames``) are matched exactly as before. The JPEGs live
# under state/studio-logo-frames (never cache/), are removed only together with
# their record (the user's own different decision on the remembered card) and
# are never touched by the cache cleanup policy.

STUDIO_LOGO_MEMORY_PATH = Path("state/studio-logo-memory.json")
STUDIO_LOGO_FRAMES_PATH = Path("state/studio-logo-frames")
STUDIO_LOGO_MEMORY_SCHEMA_VERSION = 2
_STUDIO_LOGO_READABLE_SCHEMAS = {1, 2}
STUDIO_LOGO_MINIMUM_SIMILARITY = 0.95
STUDIO_LOGO_GRID_SIZE = (32, 18)  # width, height of the colour grid
STUDIO_LOGO_MAXIMUM_CELL_DIFFERENCE = 20
_STUDIO_LOGO_MAX_SIGNATURES = 8  # frames stored per confirmed logo (matching checks every frame)
STUDIO_LOGO_MASK_PADDING = (0.01, 0.02)  # of the width, of the height
STUDIO_LOGO_MASK_FILL = 128
STUDIO_LOGO_MASK_MAX_AREA = 0.20
STUDIO_LOGO_MIN_FRAME_RANGE = 2 * STUDIO_LOGO_MAXIMUM_CELL_DIFFERENCE
STUDIO_LOGO_MAX_FRAMES = 250
STUDIO_LOGO_ANALYSIS_WIDTH = 320  # visual_logo_scanner analysis width
STUDIO_LOGO_JPEG_QUALITY = 88  # visual_logo_scanner._jpeg
_STUDIO_LOGO_DEDUP_CELLS = 2
_STUDIO_LOGO_DECODE_TIMEOUT_SECONDS = 30.0
_STUDIO_LOGO_OVERLAY_CATEGORIES = {"visual_logo", "text"}
_STUDIO_LOGO_PREVIEW_TIME = re.compile(r"-(\d+(?:\.\d+)?)s\.jpe?g$", re.IGNORECASE)


def _empty_studio_logo_memory() -> dict[str, Any]:
    return {
        "schema_version": STUDIO_LOGO_MEMORY_SCHEMA_VERSION,
        "updated_at": _now(),
        "records": [],
        "safety": {
            "automatic_edit": False,
            "purpose": "User-confirmed studio logos; a match only moves a card to the optional list",
            "minimum_similarity": STUDIO_LOGO_MINIMUM_SIMILARITY,
            "maximum_cell_difference": STUDIO_LOGO_MAXIMUM_CELL_DIFFERENCE,
        },
    }


def load_studio_logo_memory(root: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    path = root / STUDIO_LOGO_MEMORY_PATH
    if not path.exists():
        return _empty_studio_logo_memory()
    return _parse_studio_logo_memory(path.read_bytes())


def _parse_studio_logo_memory(data: bytes) -> dict[str, Any]:
    payload = json.loads(data.decode("utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") not in _STUDIO_LOGO_READABLE_SCHEMAS
        or not isinstance(payload.get("records"), list)
    ):
        raise ValueError("Unsupported studio-logo memory schema")
    return payload


def _write_studio_logo_memory(root: Path, payload: dict[str, Any]) -> None:
    """Every write uses schema 2; schema-1 records inside stay as they are."""
    payload["schema_version"] = STUDIO_LOGO_MEMORY_SCHEMA_VERSION
    payload["updated_at"] = _now()
    _write_json(root / STUDIO_LOGO_MEMORY_PATH, payload)


def _load_image(root: Path, relative: object) -> np.ndarray | None:
    try:
        path = (root / str(relative)).resolve(strict=True)
        if root != path and root not in path.parents:
            return None
        encoded = np.fromfile(path, dtype=np.uint8)  # Unicode-safe on Windows
        bgr = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    except (OSError, ValueError):
        return None
    if bgr is None or not bgr.size:
        return None
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def studio_logo_grid(image_rgb: np.ndarray) -> str:
    """The frame as a 32x18 RGB grid (area average), base64; a local overlay changes its cells."""
    if image_rgb.ndim == 2:
        image_rgb = cv2.cvtColor(image_rgb, cv2.COLOR_GRAY2RGB)
    if image_rgb.ndim != 3 or image_rgb.shape[2] != 3 or not image_rgb.size:
        raise ValueError("A non-empty RGB image is required")
    small = cv2.resize(image_rgb, STUDIO_LOGO_GRID_SIZE, interpolation=cv2.INTER_AREA)
    return base64.b64encode(np.ascontiguousarray(small, dtype=np.uint8).tobytes()).decode("ascii")


def grid_difference(first: object, second: object) -> int | None:
    """Largest per-cell colour difference of two ``studio_logo_grid`` values; ``None`` if one is invalid."""
    width, height = STUDIO_LOGO_GRID_SIZE
    try:
        values = [
            np.frombuffer(base64.b64decode(str(value), validate=True), dtype=np.uint8)
            for value in (first, second)
        ]
    except (TypeError, ValueError):  # binascii.Error is a ValueError
        return None
    if any(value.size != width * height * 3 for value in values):
        return None
    return int(np.abs(values[0].astype(np.int16) - values[1].astype(np.int16)).max())


def studio_logo_signatures(
    root: Path, item: dict[str, Any], *, strict: bool = False,
) -> list[dict[str, str]] | None:
    """Full-frame pHash and colour grid of the preview frames of one review card.

    For remembering (default): the readable frames among the first eight.
    ``strict`` (matching): every preview frame, or ``None`` when the card has no
    preview or any one of them cannot be read, so no frame is ever skipped.
    """
    root = root.resolve(strict=True)
    previews = list(item.get("preview_images") or [])
    if not strict:
        previews = previews[:_STUDIO_LOGO_MAX_SIGNATURES]
    signatures = []
    for relative in previews:
        image = _load_image(root, relative)
        if image is None:
            if strict:
                return None
            continue
        signatures.append({
            "preview": str(relative), "phash": perceptual_hash(image), "grid": studio_logo_grid(image),
        })
    if strict and not signatures:
        return None
    return signatures


def studio_logo_eligible(item: dict[str, Any]) -> bool:
    """A full-frame logo card (no localized region); watermark tracks never qualify."""
    return (
        item.get("category") == "visual_logo"
        and not isinstance(item.get("suggested_region_source_pixels"), dict)
        and item.get("candidate_type") != "persistent_overlay"
    )


def _frame_size(value: object) -> tuple[float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            width, height = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
        if width > 0 and height > 0:
            return width, height
    return None


def studio_logo_ignored_regions(
    queue: dict[str, Any], item: dict[str, Any],
) -> list[dict[str, Any]]:
    """Watermark regions a studio-logo record of ``item`` ignores (user decision 2026-10-02).

    Only persistent-overlay text or logo cards of the same queue that the user
    decided BLUR and whose interval overlaps the card's window count. KEEP, CUT,
    undecided and full-frame decisions add nothing. Each region is stored as a
    box relative to the source frame ``[x, y, width, height]``, so it scales to
    any preview size.
    """
    try:
        start, end = float(item["start_seconds"]), float(item["end_seconds"])
    except (KeyError, TypeError, ValueError):
        return []
    queue_size = _frame_size((queue.get("source") or {}).get("frame_size"))
    regions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for other in [*(queue.get("items") or []), *(queue.get("advisory_items") or [])]:
        if (
            not isinstance(other, dict)
            or other.get("candidate_type") != "persistent_overlay"
            or other.get("category") not in _STUDIO_LOGO_OVERLAY_CATEGORIES
            or other.get("decision") != "BLUR"
            or str(other.get("id")) in seen
        ):
            continue
        try:
            if not (float(other["start_seconds"]) < end and float(other["end_seconds"]) > start):
                continue
        except (KeyError, TypeError, ValueError):
            continue
        region = other.get("decision_region_source_pixels")
        if region == "FULL_FRAME":
            continue
        if not isinstance(region, dict):
            region = other.get("suggested_region_source_pixels")
        size = _frame_size(other.get("source_frame_size")) or queue_size
        if not isinstance(region, dict) or size is None:
            continue
        try:
            x, y = float(region["x"]), float(region["y"])
            width, height = float(region["width"]), float(region["height"])
        except (KeyError, TypeError, ValueError):
            continue
        if width <= 0 or height <= 0 or x < 0 or y < 0:
            continue
        seen.add(str(other.get("id")))
        regions.append({
            "box": [round(x / size[0], 6), round(y / size[1], 6),
                    round(width / size[0], 6), round(height / size[1], 6)],
            "item_id": other.get("id"),
            "category": other.get("category"),
            "decided_at": other.get("decided_at"),
        })
    return regions


def _region_boxes(regions: object) -> tuple[tuple[float, float, float, float], ...]:
    boxes = []
    for region in regions or []:
        value = region.get("box") if isinstance(region, dict) else region
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            continue
        try:
            left, top, width, height = (float(part) for part in value)
        except (TypeError, ValueError):
            continue
        if width > 0 and height > 0:
            boxes.append((left, top, width, height))
    return tuple(boxes)


def _region_identity(regions: object) -> list[tuple[str, tuple[float, ...]]]:
    return sorted(
        (str(region.get("item_id")), tuple(round(value, 6) for value in box))
        for region in regions or [] if isinstance(region, dict)
        for box in _region_boxes([region])
    )


def studio_logo_mask_rects(
    shape: tuple[int, ...], regions: object,
    padding: tuple[float, float] = STUDIO_LOGO_MASK_PADDING,
) -> list[tuple[int, int, int, int]]:
    """Pixel rectangles ``(x0, y0, x1, y1)`` of the ignored regions on an image of ``shape``.

    Padded by 1 % of the width and 2 % of the height: a watermark's anti-aliased
    edge and an OCR box a few pixels off the remembered one stay inside.
    """
    height, width = int(shape[0]), int(shape[1])
    rects = []
    for left, top, box_width, box_height in _region_boxes(regions):
        x0 = max(0, math.floor((left - padding[0]) * width))
        y0 = max(0, math.floor((top - padding[1]) * height))
        x1 = min(width, math.ceil((left + box_width + padding[0]) * width))
        y1 = min(height, math.ceil((top + box_height + padding[1]) * height))
        if x1 > x0 and y1 > y0:
            rects.append((x0, y0, x1, y1))
    return rects


def studio_logo_mask_area(regions: object, shape: tuple[int, ...]) -> float:
    """Share of the frame covered by the padded ignored regions (their union)."""
    rects = studio_logo_mask_rects(shape, regions)
    if not rects:
        return 0.0
    canvas = np.zeros((int(shape[0]), int(shape[1])), dtype=bool)
    for x0, y0, x1, y1 in rects:
        canvas[y0:y1, x0:x1] = True
    return float(canvas.mean())


def _studio_logo_capped_regions(
    regions: list[dict[str, Any]], shape: tuple[int, ...],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """The regions to ignore, or none when their padded union exceeds 20 % of the frame.

    A refused mask keeps the regions it refused (``candidate_regions``) so that a
    later refresh from another queue of the same source can still merge them.
    """
    area = studio_logo_mask_area(regions, shape)
    if area > STUDIO_LOGO_MASK_MAX_AREA:
        return [], {
            "reason": "area_cap", "area": round(area, 4), "regions": len(regions),
            "candidate_regions": list(regions),
        }
    return list(regions), None


def _cells_clear_of(rects: list[tuple[int, int, int, int]], shape: tuple[int, ...]) -> np.ndarray:
    grid_width, grid_height = STUDIO_LOGO_GRID_SIZE
    height, width = int(shape[0]), int(shape[1])
    keep = np.ones((grid_height, grid_width), dtype=bool)
    for x0, y0, x1, y1 in rects:
        keep[
            int(y0 * grid_height // height):int(math.ceil(y1 * grid_height / height)),
            int(x0 * grid_width // width):int(math.ceil(x1 * grid_width / width)),
        ] = False
    return keep


def studio_logo_frame_signature(
    image_rgb: np.ndarray, regions: object = (), *, picture_range: bool = True,
) -> dict[str, Any]:
    """Masked pHash and colour grid of one frame, and its picture range outside the mask.

    Ignored regions are filled with grey 128 before both measures, in the stored
    and in the candidate frame alike. Without regions the pHash and grid equal
    ``perceptual_hash`` and ``studio_logo_grid`` of the frame. ``range`` is the
    largest per-channel spread of the 32x18 grid over the cells that do not
    touch the mask: a flat candidate can only pass the grid test against a
    stored frame whose range is at most twice the cell limit.
    """
    if image_rgb.ndim == 2:
        image_rgb = cv2.cvtColor(image_rgb, cv2.COLOR_GRAY2RGB)
    rects = studio_logo_mask_rects(image_rgb.shape, regions)
    filled = image_rgb
    if rects:
        filled = image_rgb.copy()
        for x0, y0, x1, y1 in rects:
            filled[y0:y1, x0:x1] = STUDIO_LOGO_MASK_FILL
    signature: dict[str, Any] = {"phash": perceptual_hash(filled), "grid": studio_logo_grid(filled)}
    if picture_range:
        grid = cv2.resize(image_rgb, STUDIO_LOGO_GRID_SIZE, interpolation=cv2.INTER_AREA).astype(np.int16)
        values = grid[_cells_clear_of(rects, image_rgb.shape)]
        signature["range"] = int((values.max(axis=0) - values.min(axis=0)).max()) if values.size else 0
    return signature


def studio_logo_frame_informative(image_rgb: np.ndarray, regions: object = ()) -> bool:
    """Whether a frame carries picture outside the mask (black, flat and fade frames do not)."""
    return studio_logo_frame_signature(image_rgb, regions)["range"] > STUDIO_LOGO_MIN_FRAME_RANGE


def _preview_time(relative: object) -> float | None:
    match = _STUDIO_LOGO_PREVIEW_TIME.search(str(relative))
    return float(match.group(1)) if match else None


def _read_inside(root: Path, relative: object) -> bytes | None:
    try:
        path = (root / str(relative)).resolve(strict=True)
        if root != path and root not in path.parents:
            return None
        return path.read_bytes()
    except (OSError, ValueError):
        return None


def _video_rate(stream: dict[str, Any]) -> float | None:
    for key in ("avg_frame_rate", "r_frame_rate"):
        numerator, _, denominator = str(stream.get(key) or "").partition("/")
        try:
            rate = float(numerator) / float(denominator or 1)
        except (ValueError, ZeroDivisionError):
            continue
        if math.isfinite(rate) and rate > 0:
            return rate
    return None


def _decode_studio_logo_window(
    ffmpeg_path: Path, source: Path, start: float, end: float, step: float,
    width: int, height: int,
) -> tuple[list[tuple[float, bytes]], str | None]:
    # The scanner's own decoder and encoder, so a frame is the scanner's frame byte for byte.
    from biliflow.visual_logo_scanner import _iter_frames, _jpeg

    frames: list[tuple[float, bytes]] = []
    failures: list[str] = []

    def run() -> None:
        try:
            for timestamp, frame in _iter_frames(
                ffmpeg_path=ffmpeg_path, input_path=source, start=start, duration=end - start,
                sample_every=step, width=width, height=height, decode_backend="cpu",
            ):
                if timestamp >= end - 1e-6 or len(frames) >= STUDIO_LOGO_MAX_FRAMES:
                    break
                frames.append((timestamp, _jpeg(frame)))
        except Exception as error:  # noqa: BLE001 - remembering must never fail on a decode error
            failures.append(type(error).__name__)

    worker = threading.Thread(target=run, name="studio-logo-frames", daemon=True)
    worker.start()
    worker.join(_STUDIO_LOGO_DECODE_TIMEOUT_SECONDS)
    if worker.is_alive():
        return [], "decode_timeout"
    if failures:
        return [], "decode_failed"
    if not frames:
        return [], "decode_empty"
    return list(frames), None


def studio_logo_window_frames(
    root: Path, queue: dict[str, Any], item: dict[str, Any], ffmpeg_path: Path,
    ffprobe_path: Path | None = None,
) -> dict[str, Any]:
    """Every frame of the card's window, decoded again exactly like the visual-logo scanner.

    One cpu ffmpeg process (``visual_logo_scanner._iter_frames`` and ``_jpeg``:
    320 px wide, even height, bilinear, JPEG q88) at the native frame rate, at
    most ``STUDIO_LOGO_MAX_FRAMES``. The result is used only when every preview
    JPEG of the card whose time lies in the window is reproduced byte for byte,
    which proves the same source and the same pipeline. Otherwise, and on any
    ffmpeg error or after 30 s, ``frames`` is empty, ``frames_source`` is
    ``preview_only`` and ``reason`` says why; this never raises.
    """
    root = root.resolve(strict=True)
    try:
        start, end = float(item["start_seconds"]), float(item["end_seconds"])
    except (KeyError, TypeError, ValueError):
        start = end = 0.0
    result: dict[str, Any] = {
        "frames": [], "frames_source": "preview_only", "reason": None, "pipeline": None,
        "window": [round(start, 3), round(end, 3)],
    }

    def fallback(reason: str) -> dict[str, Any]:
        result["reason"] = reason
        return result

    if not end > start:
        return fallback("invalid_window")
    source = (queue.get("source") or {}).get("path")
    try:
        source_path = Path(str(source)) if source else None
        if source_path is None or not source_path.is_file():
            return fallback("source_missing")
        ffmpeg = Path(ffmpeg_path)
        if not ffmpeg.is_file():
            return fallback("ffmpeg_missing")
    except OSError:
        return fallback("source_missing")
    expected = [
        data for relative in item.get("preview_images") or []
        if (moment := _preview_time(relative)) is not None and start <= moment < end
        and (data := _read_inside(root, relative)) is not None
    ]
    if not expected:
        return fallback("self_check_unavailable")
    ffprobe = Path(ffprobe_path) if ffprobe_path else ffmpeg.with_name(ffmpeg.name.replace("ffmpeg", "ffprobe"))
    try:
        from biliflow.probe import probe_video

        probe = probe_video(ffprobe, source_path)
        stream = next(value for value in probe.get("streams", []) if value.get("codec_type") == "video")
        source_width, source_height = int(stream["width"]), int(stream["height"])
        rate = _video_rate(stream)
    except (OSError, ValueError, KeyError, TypeError, StopIteration, subprocess.SubprocessError):
        return fallback("probe_failed")
    if rate is None or source_width <= 0 or source_height <= 0:
        return fallback("probe_failed")
    width = STUDIO_LOGO_ANALYSIS_WIDTH
    height = max(2, round(source_height * width / source_width / 2) * 2)
    step = max(1.0 / rate, (end - start) / STUDIO_LOGO_MAX_FRAMES)
    frames, error = _decode_studio_logo_window(ffmpeg, source_path, start, end, step, width, height)
    if error:
        return fallback(error)
    decoded = {data for _, data in frames}
    if any(data not in decoded for data in expected):
        return fallback("self_check_failed")
    result.update(
        frames=frames, frames_source="source_video", reason=None,
        pipeline={
            "analysis_size": [width, height], "scale": "bilinear",
            "jpeg_quality": STUDIO_LOGO_JPEG_QUALITY, "decoder": "cpu",
            "fps": round(1.0 / step, 6), "source_fps": round(rate, 6),
            "self_checked_previews": len(expected),
        },
    )
    return result


def _studio_logo_frames_folder(source_sha256: str, item_id: object) -> str:
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(item_id or "item"))[:80]
    return (STUDIO_LOGO_FRAMES_PATH / f"{str(source_sha256)[:16]}-{safe_id}").as_posix()


def _sign_studio_logo_frames(
    images: list[tuple[float | None, str, np.ndarray]], regions: object,
) -> list[dict[str, Any]]:
    """Masked signatures of the frames worth storing: informative, deduplicated, at most 250."""
    kept: list[dict[str, Any]] = []
    by_hash: dict[str, list[str]] = {}
    for moment, image_path, image in images:
        signature = studio_logo_frame_signature(image, regions)
        if signature["range"] <= STUDIO_LOGO_MIN_FRAME_RANGE:
            continue
        same = by_hash.setdefault(signature["phash"], [])
        if any(
            (difference := grid_difference(signature["grid"], grid)) is not None
            and difference <= _STUDIO_LOGO_DEDUP_CELLS
            for grid in same
        ):
            continue
        same.append(signature["grid"])
        kept.append({
            "t": moment, "image": image_path, "phash": signature["phash"], "grid": signature["grid"],
        })
        if len(kept) >= STUDIO_LOGO_MAX_FRAMES:
            break
    return kept


def _decode_jpeg(data: bytes) -> np.ndarray | None:
    bgr = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if bgr is None or not bgr.size:
        return None
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def prepare_studio_logo_frames(
    root: Path, queue: dict[str, Any], item: dict[str, Any],
    window_frames: dict[str, Any] | None = None,
    ignored_regions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """What a schema-2 record stores, computed without writing anything.

    The decoded window frames (``studio_logo_window_frames``) plus the card's own
    previews (the first eight; a preview equal to a decoded frame is not stored
    twice), signed with the ignored regions, guarded and deduplicated. Without
    decoded frames the record holds the previews only (``preview_only``).
    """
    root = root.resolve(strict=True)
    try:
        window = [round(float(item["start_seconds"]), 3), round(float(item["end_seconds"]), 3)]
    except (KeyError, TypeError, ValueError):
        window = None
    window_frames = window_frames or {
        "frames": [], "frames_source": "preview_only", "reason": "not_decoded", "pipeline": None,
    }
    regions = studio_logo_ignored_regions(queue, item) if ignored_regions is None else list(ignored_regions)
    source_sha256 = str((queue.get("source") or {}).get("sha256", ""))
    folder = _studio_logo_frames_folder(source_sha256, item.get("id"))
    stored: list[tuple[float | None, str, bytes]] = []
    seen: set[bytes] = set()
    for moment, data in window_frames.get("frames") or []:
        name = f"{float(moment):.3f}.jpg"
        if data in seen:
            continue
        seen.add(data)
        stored.append((round(float(moment), 3), name, data))
    previews = list(item.get("preview_images") or [])[:_STUDIO_LOGO_MAX_SIGNATURES]
    for index, relative in enumerate(previews):
        data = _read_inside(root, relative)
        if data is None or data in seen:
            continue
        seen.add(data)
        stored.append((_preview_time(relative), f"preview-{index:02d}.jpg", data))
    images = [
        (moment, f"{folder}/{name}", image) for moment, name, data in stored
        if (image := _decode_jpeg(data)) is not None
    ]
    pipeline = window_frames.get("pipeline") or None
    if pipeline and pipeline.get("analysis_size"):
        shape: tuple[int, ...] = (int(pipeline["analysis_size"][1]), int(pipeline["analysis_size"][0]))
    elif images:
        shape = images[0][2].shape
    else:
        shape = (180, 320)
    regions, refused = _studio_logo_capped_regions(regions, shape)
    return {
        "folder": folder,
        "stored": stored,
        "frames": _sign_studio_logo_frames(images, regions),
        "ignored_regions": regions,
        "mask_refused": refused,
        "frames_source": window_frames.get("frames_source") or "preview_only",
        "frames_reason": window_frames.get("reason"),
        "pipeline": pipeline,
        "window": window,
        "decoded_frames": len(window_frames.get("frames") or []),
    }


def _remove_studio_logo_frames(root: Path, record: dict[str, Any]) -> None:
    """Delete a record's frame folder (only ever together with the record itself)."""
    folder = record.get("frames_folder")
    if not folder:
        return
    base = (root / STUDIO_LOGO_FRAMES_PATH).resolve()
    path = (root / str(folder)).resolve()
    if path.parent != base or not path.is_dir():
        return
    shutil.rmtree(path)


def _write_studio_logo_frames(root: Path, prepared: dict[str, Any]) -> None:
    base = root / STUDIO_LOGO_FRAMES_PATH
    base.mkdir(parents=True, exist_ok=True)
    folder = root / prepared["folder"]
    if folder.resolve().parent != base.resolve():
        raise ValueError("Studio-logo frame folder must be inside state/studio-logo-frames")
    temporary = folder.with_name(folder.name + ".tmp")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir()
    for _, name, data in prepared["stored"]:
        (temporary / name).write_bytes(data)
    if folder.exists():
        shutil.rmtree(folder)
    temporary.replace(folder)


def _studio_logo_v2_fields(prepared: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_version": 2,
        "ignored_regions": prepared["ignored_regions"],
        "mask": {
            "padding": list(STUDIO_LOGO_MASK_PADDING), "fill": STUDIO_LOGO_MASK_FILL,
            "max_area": STUDIO_LOGO_MASK_MAX_AREA, "min_frame_range": STUDIO_LOGO_MIN_FRAME_RANGE,
        },
        "mask_refused": prepared["mask_refused"],
        "frames": prepared["frames"],
        "stored_frames": [
            {"t": moment, "image": f"{prepared['folder']}/{name}"} for moment, name, _ in prepared["stored"]
        ],
        "frames_folder": prepared["folder"],
        "frames_source": prepared["frames_source"],
        "frames_reason": prepared["frames_reason"],
        "pipeline": prepared["pipeline"],
        "window": prepared["window"],
        "mask_updated_at": _now(),
    }


def _studio_logo_has_frames(record: dict[str, Any]) -> bool:
    return isinstance(record.get("frames"), list)


def remember_studio_logo(
    root: Path, queue: dict[str, Any], item: dict[str, Any],
    signatures: list[dict[str, str]] | None = None,
    window_text: dict[str, Any] | None = None,
    *, frames: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Store a studio-logo signature for an explicit user KEEP of a full-frame logo card.

    ``window_text`` holds the OCR lines seen while the confirmed ident was on
    screen (``{"covered": bool, "texts": [...]}``). A later matching card may
    only show those lines; anything else keeps it in the main list.
    ``frames`` (``prepare_studio_logo_frames``) makes a schema-2 record: its
    frame JPEGs are written under state/studio-logo-frames and matching uses
    them with the ignored watermark regions. Without it the record is the
    schema-1 record of before. The preview ``signatures`` are always kept.
    """
    root = root.resolve(strict=True)
    if item.get("decision") != "KEEP" or not studio_logo_eligible(item):
        return None
    signatures = studio_logo_signatures(root, item) if signatures is None else signatures
    if not signatures:
        return None
    texts = [str(value) for value in (window_text or {}).get("texts") or []]
    source_sha256 = str(queue.get("source", {}).get("sha256", ""))
    key = f"{source_sha256}:{item.get('id', '')}"
    payload = load_studio_logo_memory(root)
    for replaced in payload["records"]:
        if isinstance(replaced, dict) and replaced.get("key") == key:
            _remove_studio_logo_frames(root, replaced)
    payload["records"] = [record for record in payload["records"] if record.get("key") != key]
    record = {
        "key": key,
        "source_sha256": source_sha256,
        "review_item_id": item.get("id"),
        "decision": "KEEP",
        "memory_class": "studio_logo",
        "labels": list(item.get("labels") or []),
        "candidate_type": item.get("candidate_type"),
        "review_kind": item.get("review_kind"),
        "start_seconds": item.get("start_seconds"),
        "end_seconds": item.get("end_seconds"),
        "signatures": signatures,
        "window_text": {"covered": bool((window_text or {}).get("covered")), "texts": texts},
        "created_at": _now(),
        "confirmed_by": "user",
        "automatic_edit": False,
    }
    if frames is not None:
        _write_studio_logo_frames(root, frames)
        record.update(_studio_logo_v2_fields(frames))
    payload["records"].append(record)
    _write_studio_logo_memory(root, payload)
    return record


def forget_studio_logo(root: Path, queue: dict[str, Any], item_id: str) -> bool:
    """Remove the record of a card (the user chose another decision on it) and its frame JPEGs."""
    root = root.resolve(strict=True)
    if not (root / STUDIO_LOGO_MEMORY_PATH).exists():
        return False
    source_sha256 = str(queue.get("source", {}).get("sha256", ""))
    key = f"{source_sha256}:{item_id}"
    payload = load_studio_logo_memory(root)
    removed = [record for record in payload["records"] if record.get("key") == key]
    if not removed:
        return False
    payload["records"] = [record for record in payload["records"] if record.get("key") != key]
    _write_studio_logo_memory(root, payload)
    for record in removed:
        if isinstance(record, dict):
            _remove_studio_logo_frames(root, record)
    return True


def refresh_studio_logo_masks(root: Path, queue: dict[str, Any]) -> int:
    """Re-sign the schema-2 records of ``queue``'s source after a watermark decision changed.

    The ignored regions are merged by watermark card id: the regions of every
    persistent-overlay card of ``queue`` are recomputed from it (BLUR adds a
    region; KEEP, CUT or a clear removes it; a new region replaces it), while
    regions from cards that ``queue`` does not hold (another queue of the same
    source, e.g. the run that holds the remembered card) are kept. The 20 % cap
    applies to the union. When the regions changed, every stored JPEG is signed
    again with the guard re-applied. If none of the stored JPEGs can be read
    (state/studio-logo-frames missing), the old masked frames are dropped
    (``frames`` = [], ``frames_missing``): the record stops matching until the
    user remembers the logo again, instead of keeping a mask the user withdrew.
    Schema-1 records are never touched and no record is ever deleted. Returns
    the number of records updated.
    """
    root = root.resolve(strict=True)
    if not (root / STUDIO_LOGO_MEMORY_PATH).exists():
        return 0
    source_sha256 = str((queue.get("source") or {}).get("sha256", ""))
    if not source_sha256:
        return 0
    queue_overlays = {
        str(card.get("id"))
        for card in [*(queue.get("items") or []), *(queue.get("advisory_items") or [])]
        if isinstance(card, dict) and card.get("candidate_type") == "persistent_overlay"
    }
    payload = load_studio_logo_memory(root)
    updated = 0
    for record in payload["records"]:
        if (
            not isinstance(record, dict)
            or not _studio_logo_has_frames(record)
            or record.get("source_sha256") != source_sha256
        ):
            continue
        window = record.get("window") or [record.get("start_seconds"), record.get("end_seconds")]
        try:
            span = {"start_seconds": float(window[0]), "end_seconds": float(window[1])}
        except (TypeError, ValueError, IndexError):
            continue
        previous = record.get("ignored_regions") or []
        refused_before = record.get("mask_refused")
        if not previous and isinstance(refused_before, dict):
            previous = refused_before.get("candidate_regions") or []
        merged = [
            region for region in previous
            if isinstance(region, dict) and str(region.get("item_id")) not in queue_overlays
        ] + studio_logo_ignored_regions(queue, span)
        images = [
            (entry.get("t"), str(entry.get("image")), image)
            for entry in record.get("stored_frames") or []
            if isinstance(entry, dict) and (image := _load_image(root, entry.get("image"))) is not None
        ]
        pipeline = record.get("pipeline") or {}
        size = pipeline.get("analysis_size") if isinstance(pipeline, dict) else None
        if isinstance(size, list) and len(size) == 2:
            shape: tuple[int, ...] = (int(size[1]), int(size[0]))
        else:
            shape = images[0][2].shape if images else (180, 320)
        regions, refused = _studio_logo_capped_regions(merged, shape)
        if (
            _region_identity(regions) == _region_identity(record.get("ignored_regions"))
            and _region_identity((refused or {}).get("candidate_regions"))
            == _region_identity((refused_before or {}).get("candidate_regions")
                                if isinstance(refused_before, dict) else None)
            and bool(refused) == bool(refused_before)
        ):
            continue
        record["ignored_regions"] = regions
        record["mask_refused"] = refused
        if images:
            record["frames"] = _sign_studio_logo_frames(images, regions)
            record.pop("frames_missing", None)
        else:
            record["frames"] = []
            record["frames_missing"] = True
        record["mask_updated_at"] = _now()
        updated += 1
    if updated:
        _write_studio_logo_memory(root, payload)
    return updated


class _StudioLogoCandidate:
    """The preview frames of one card and their (masked) signatures, computed once."""

    def __init__(self, images: list[np.ndarray]):
        self.images = images
        self._signatures: dict[tuple[int, tuple], dict[str, Any]] = {}

    def signature(self, index: int, boxes: tuple = ()) -> dict[str, Any]:
        key = (index, boxes)
        if key not in self._signatures:
            self._signatures[key] = studio_logo_frame_signature(
                self.images[index], boxes, picture_range=False,
            )
        return self._signatures[key]


def _studio_logo_preview_images(root: Path, item: dict[str, Any]) -> list[np.ndarray] | None:
    """Every preview frame of a card, or ``None`` when one cannot be read (strict, as matching needs)."""
    root = root.resolve(strict=True)
    images = []
    for relative in item.get("preview_images") or []:
        image = _load_image(root, relative)
        if image is None:
            return None
        images.append(image)
    return images or None


def _prepared_studio_frames(record: dict[str, Any]) -> tuple[list[Any], list[int], np.ndarray]:
    width, height = STUDIO_LOGO_GRID_SIZE
    moments, hashes, grids = [], [], []
    for frame in record.get("frames") or []:
        if not isinstance(frame, dict):
            continue
        try:
            value = int(str(frame.get("phash")), 16)
            grid = np.frombuffer(base64.b64decode(str(frame.get("grid")), validate=True), dtype=np.uint8)
        except (TypeError, ValueError):
            continue
        if grid.size != width * height * 3:
            continue
        moments.append(frame.get("t"))
        hashes.append(value)
        grids.append(grid)
    array = np.stack(grids).astype(np.int16) if grids else np.zeros((0, width * height * 3), np.int16)
    return moments, hashes, array


def _studio_frame_ranks(
    signature: dict[str, Any], prepared: tuple[list[Any], list[int], np.ndarray],
) -> list[tuple[float, int, Any]]:
    """(similarity, cell difference, stored time) of one candidate frame against every stored frame."""
    moments, hashes, grids = prepared
    if not hashes:
        return []
    value = int(signature["phash"], 16)
    candidate = np.frombuffer(base64.b64decode(signature["grid"]), dtype=np.uint8).astype(np.int16)
    cells = np.abs(grids - candidate).max(axis=1)
    return [
        (max(0.0, 1.0 - (value ^ stored).bit_count() / 64.0), int(cells[index]), moments[index])
        for index, stored in enumerate(hashes)
    ]


def match_studio_logo(
    root: Path, item: dict[str, Any], records: Iterable[dict[str, Any]],
    *, minimum_similarity: float = STUDIO_LOGO_MINIMUM_SIMILARITY,
    maximum_cell_difference: int = STUDIO_LOGO_MAXIMUM_CELL_DIFFERENCE,
) -> dict[str, Any] | None:
    """Match when EVERY preview frame of ``item`` repeats a confirmed studio logo.

    A frame repeats a stored frame when the pHash similarity is at least
    ``minimum_similarity`` AND no cell of the colour grid differs by more than
    ``maximum_cell_difference``. A card with an unreadable preview, a card
    without previews and a stored frame without a grid never match.
    ``known_texts`` in the result are the OCR lines of the matched records.
    A schema-2 record compares the frame, with that record's ignored watermark
    regions filled in both images, against each of its stored window frames;
    its displayed cell difference is the smallest among the passing frames.
    Nothing inside an ignored region is compared: text there is left to the
    OCR window-text check, and a non-text graphic there (logo, QR code, image
    banner) is not detected by this picture check at all.
    Schema-1 records are compared exactly as before.
    """
    if not 0 < minimum_similarity <= 1:
        raise ValueError("minimum_similarity must be in (0, 1]")
    if not 0 <= maximum_cell_difference <= 255:
        raise ValueError("maximum_cell_difference must be in [0, 255]")
    records = [
        record for record in records
        if isinstance(record, dict) and record.get("decision") == "KEEP"
        and record.get("memory_class") == "studio_logo"
    ]
    if not records or not studio_logo_eligible(item):
        return None
    images = _studio_logo_preview_images(root, item)
    if not images:
        return None
    candidate = _StudioLogoCandidate(images)
    prepared = {
        index: (_region_boxes(record.get("ignored_regions")), _prepared_studio_frames(record))
        for index, record in enumerate(records) if _studio_logo_has_frames(record)
    }
    weakest: float | None = None
    worst_cells = 0
    matched: dict[str, dict[str, Any]] = {}
    stored_times: list[Any] = []
    for frame_index in range(len(images)):
        best: tuple[tuple[float, int], dict[str, Any], float, int, Any] | None = None
        for record_index, record in enumerate(records):
            if record_index in prepared:
                boxes, frames = prepared[record_index]
                top: tuple[tuple[float, int], float, int, Any] | None = None
                display: int | None = None
                for similarity, cells, moment in _studio_frame_ranks(
                    candidate.signature(frame_index, boxes), frames,
                ):
                    if similarity < minimum_similarity or cells > maximum_cell_difference:
                        continue
                    if top is None or (similarity, -cells) > top[0]:
                        top = ((similarity, -cells), similarity, cells, moment)
                    display = cells if display is None else min(display, cells)
                if top is not None and display is not None and (best is None or top[0] > best[0]):
                    best = (top[0], record, top[1], display, top[3])
                continue
            signature = candidate.signature(frame_index)
            for stored in record.get("signatures") or []:
                if not isinstance(stored, dict):
                    continue
                similarity = hash_similarity(signature["phash"], str(stored.get("phash", "")))
                if similarity < minimum_similarity:
                    continue
                cells = grid_difference(signature["grid"], stored.get("grid"))
                if cells is None or cells > maximum_cell_difference:
                    continue
                if best is None or (similarity, -cells) > best[0]:
                    best = ((similarity, -cells), record, similarity, cells, None)
        if best is None:
            return None
        _, record, similarity, cells, moment = best
        matched.setdefault(str(record.get("key")), record)
        stored_times.append(moment)
        if weakest is None or similarity < weakest:
            weakest = similarity
        worst_cells = max(worst_cells, cells)
    assert weakest is not None and matched
    primary = next(iter(matched.values()))
    known_texts = list(dict.fromkeys(
        str(text) for record in matched.values()
        for text in ((record.get("window_text") or {}).get("texts") or [])
    ))
    result = {
        "similarity": round(weakest, 6),
        "minimum_similarity": minimum_similarity,
        "cell_difference": worst_cells,
        "maximum_cell_difference": maximum_cell_difference,
        "memory_key": primary.get("key"),
        "memory_keys": list(matched),
        "labels": list(primary.get("labels") or []),
        "source_sha256": primary.get("source_sha256"),
        "review_item_id": primary.get("review_item_id"),
        "matched_frames": len(images),
        "known_texts": known_texts,
        "automatic_edit": False,
    }
    if any(_studio_logo_has_frames(record) for record in matched.values()):
        result["masked_regions"] = max(
            len(_region_boxes(record.get("ignored_regions"))) for record in matched.values()
            if _studio_logo_has_frames(record)
        )
        result["frames_source"] = primary.get("frames_source")
        result["matched_stored_t"] = stored_times
    return result


def compare_studio_logo(
    root: Path, item: dict[str, Any], records: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    """Read-only, display-only: how close a card came to the confirmed studio logos.

    The same records, signatures and measures as ``match_studio_logo``, without
    its thresholds: each preview frame takes its closest stored frame (highest
    pHash similarity, then smallest grid difference) and the card reports its
    weakest frame, because a match needs every frame. ``None`` when nothing could
    be compared (no record, an ineligible card or an unreadable preview). Against
    a schema-2 record the frames are compared with its watermark regions ignored
    and ``masked_regions`` says how many.
    """
    records = [
        record for record in records
        if isinstance(record, dict) and record.get("decision") == "KEEP"
        and record.get("memory_class") == "studio_logo"
    ]
    if not records or not studio_logo_eligible(item):
        return None
    images = _studio_logo_preview_images(root, item)
    if not images:
        return None
    candidate = _StudioLogoCandidate(images)
    prepared = {
        index: (_region_boxes(record.get("ignored_regions")), _prepared_studio_frames(record))
        for index, record in enumerate(records) if _studio_logo_has_frames(record)
    }
    weakest: tuple[tuple[float, int], float, int | None, int | None] | None = None
    for frame_index in range(len(images)):
        best: tuple[tuple[float, int], float, int | None, int | None] | None = None
        for record_index, record in enumerate(records):
            if record_index in prepared:
                boxes, frames = prepared[record_index]
                for similarity, cells, _ in _studio_frame_ranks(candidate.signature(frame_index, boxes), frames):
                    rank = (similarity, -cells)
                    if best is None or rank > best[0]:
                        best = (rank, similarity, cells, len(boxes))
                continue
            signature = candidate.signature(frame_index)
            for stored in record.get("signatures") or []:
                if not isinstance(stored, dict):
                    continue
                similarity = hash_similarity(signature["phash"], str(stored.get("phash", "")))
                cells = grid_difference(signature["grid"], stored.get("grid"))
                rank = (similarity, -(256 if cells is None else cells))
                if best is None or rank > best[0]:
                    best = (rank, similarity, cells, None)
        if best is None:
            return None
        if weakest is None or best[0] < weakest[0]:
            weakest = best
    assert weakest is not None
    result = {
        "records": len(records),
        "frames": len(images),
        "best_similarity": round(weakest[1], 4),
        "best_cell_difference": weakest[2],
        "minimum_similarity": STUDIO_LOGO_MINIMUM_SIMILARITY,
        "maximum_cell_difference": STUDIO_LOGO_MAXIMUM_CELL_DIFFERENCE,
    }
    if weakest[3] is not None:
        result["masked_regions"] = weakest[3]
    return result


def _control_center_queue_paths(root: Path, source_sha256: str) -> list[str] | None:
    """Active queue paths of the Control Center jobs of a source (read-only); ``None`` if unknown."""
    database = root / "state" / "control-center.sqlite3"
    if not database.is_file():
        return None
    try:
        connection = sqlite3.connect(f"file:{quote(database.as_posix())}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "select active_queue_path from jobs where source_sha256=?", (source_sha256,),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    return [str(row[0]).replace("\\", "/") for row in rows if row[0]]


def _plan_studio_logo_upgrade(
    root: Path, record: dict[str, Any], ffmpeg_path: Path, ffprobe_path: Path | None,
) -> tuple[dict[str, Any] | None, str | None, dict[str, Any]]:
    """Read-only lookup of the queue, card and source a schema-1 record came from."""
    details: dict[str, Any] = {}
    signatures = record.get("signatures") or []
    preview = signatures[0].get("preview") if signatures and isinstance(signatures[0], dict) else None
    parts = PurePosixPath(str(preview or "")).parts
    if len(parts) < 4:
        return None, "no_preview_path", details
    queue_relative = (PurePosixPath(*parts[:-3]) / "review-queue.json").as_posix()
    details["queue"] = queue_relative
    reports = (root / "reports").resolve()
    try:
        queue_path = (root / queue_relative).resolve(strict=True)
        if reports not in queue_path.parents:
            return None, "queue_outside_reports", details
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "queue_missing", details
    if not isinstance(queue, dict):
        return None, "queue_unreadable", details
    source = queue.get("source") or {}
    if str(source.get("sha256", "")) != str(record.get("source_sha256", "")):
        return None, "source_sha_mismatch", details
    item = next((
        value for value in queue.get("items") or []
        if isinstance(value, dict) and value.get("id") == record.get("review_item_id")
    ), None)
    if (
        item is None or item.get("decision") != "KEEP"
        or not (item.get("studio_logo_memory") or {}).get("remembered")
    ):
        return None, "card_not_remembered_in_queue", details
    try:
        if not Path(str(source.get("path") or "")).is_file():
            return None, "source_missing", details
    except OSError:
        return None, "source_missing", details
    jobs = _control_center_queue_paths(root, str(record.get("source_sha256", "")))
    details["control_center_queue_paths"] = jobs
    if jobs and queue_relative not in jobs:
        return None, "control_center_queue_mismatch", details
    window = studio_logo_window_frames(root, queue, item, ffmpeg_path, ffprobe_path)
    details["frames_source"] = window["frames_source"]
    if window["frames_source"] != "source_video":
        return None, str(window.get("reason")), details
    prepared = prepare_studio_logo_frames(root, queue, item, window)
    details.update(
        decoded_frames=prepared["decoded_frames"], frames=len(prepared["frames"]),
        window=prepared["window"], pipeline=prepared["pipeline"],
        ignored_regions=[
            {"item_id": region["item_id"], "category": region["category"], "box": region["box"]}
            for region in prepared["ignored_regions"]
        ],
        mask_refused=prepared["mask_refused"],
    )
    return prepared, None, details


def upgrade_studio_logo_memory(
    root: Path, *, apply: bool = False, ffmpeg_path: Path | None = None,
    ffprobe_path: Path | None = None,
) -> dict[str, Any]:
    """Add schema-2 frames and masks to remembered studio logos (``studio-logo-upgrade``).

    Dry run by default. A record is upgraded only when its own queue (the folder
    of its first preview), the KEEP + remembered card in it and the source video
    are found and the decoded window reproduces the card's preview byte for byte.
    ``apply`` first copies the memory to state/backups/studio-logo-memory-<ts>.json,
    then only ADDS the schema-2 fields: no record and no old signature is ever
    removed. Anything else stays a schema-1 record, which matches as before.

    Run it with the Control Center stopped: this process does not hold the
    Control Center's queue lock and decoding takes seconds per record. The plan
    is made from one snapshot of the memory file; ``apply`` writes only when the
    file still has exactly those bytes, checked before the frame JPEGs are
    written and again right before the memory is replaced. Otherwise (a
    remember or forget landed meanwhile) nothing is kept and the report says
    ``aborted: memory_changed_during_upgrade``; re-run it. The backup holds the
    bytes that were actually replaced.
    """
    root = root.resolve(strict=True)
    ffmpeg_path = ffmpeg_path or root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    report: dict[str, Any] = {
        "dry_run": not apply, "memory": STUDIO_LOGO_MEMORY_PATH.as_posix(), "records": [],
        "backup": None, "upgraded": 0, "automatic_edit": False,
    }
    memory_path = root / STUDIO_LOGO_MEMORY_PATH
    if not memory_path.exists():
        return report
    snapshot = memory_path.read_bytes()
    report["memory_sha256"] = hashlib.sha256(snapshot).hexdigest()
    payload = _parse_studio_logo_memory(snapshot)
    plans: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for record in payload["records"]:
        entry: dict[str, Any] = {
            "key": record.get("key") if isinstance(record, dict) else None,
            "review_item_id": record.get("review_item_id") if isinstance(record, dict) else None,
        }
        report["records"].append(entry)
        if not isinstance(record, dict) or record.get("memory_class") != "studio_logo":
            entry.update(status="skipped", reason="not_a_studio_logo_record")
            continue
        if _studio_logo_has_frames(record):
            entry.update(status="already_v2", reason=None)
            continue
        prepared, reason, details = _plan_studio_logo_upgrade(root, record, ffmpeg_path, ffprobe_path)
        entry.update(details)
        if prepared is None:
            entry.update(
                status="kept_v1", reason=reason,
                message="Hãy bấm lại 'Đây là logo hãng phim — giữ & nhớ' để nhớ đủ khung hình",
            )
            continue
        entry.update(status="upgradable", reason=None)
        plans.append((record, prepared, entry))
    if apply and plans:
        if not _studio_logo_memory_unchanged(memory_path, snapshot):
            return _abort_studio_logo_upgrade(report)
        written: list[str] = []
        for _, prepared, _ in plans:
            _write_studio_logo_frames(root, prepared)
            written.append(prepared["folder"])
        if not _studio_logo_memory_unchanged(memory_path, snapshot):
            _remove_unreferenced_studio_logo_frames(root, written)
            return _abort_studio_logo_upgrade(report)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = root / "state" / "backups" / f"studio-logo-memory-{stamp}.json"
        suffix = 1
        while backup.exists():
            backup = backup.with_name(f"studio-logo-memory-{stamp}-{suffix}.json")
            suffix += 1
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(snapshot)  # exactly the bytes replaced below
        report["backup"] = backup.relative_to(root).as_posix()
        for record, prepared, entry in plans:
            for name, value in _studio_logo_v2_fields(prepared).items():
                record.setdefault(name, value)
            record.setdefault("upgraded_at", _now())
            record.setdefault("upgraded_by", "studio-logo-upgrade")
            entry["status"] = "upgraded"
            report["upgraded"] += 1
        _write_studio_logo_memory(root, payload)
    return report


def _studio_logo_memory_unchanged(path: Path, snapshot: bytes) -> bool:
    try:
        return path.read_bytes() == snapshot
    except OSError:
        return False


def _abort_studio_logo_upgrade(report: dict[str, Any]) -> dict[str, Any]:
    report.update(
        aborted="memory_changed_during_upgrade",
        message=(
            "memory changed during upgrade, re-run — Bộ nhớ logo hãng phim vừa thay đổi trong lúc "
            "nâng cấp (Control Center đang chạy?); không ghi gì. Hãy tắt Control Center rồi chạy lại."
        ),
        backup=None, upgraded=0,
    )
    return report


def _remove_unreferenced_studio_logo_frames(root: Path, folders: list[str]) -> None:
    """After an aborted upgrade: drop the frame folders it wrote that no current record uses."""
    try:
        records = load_studio_logo_memory(root)["records"]
    except (OSError, ValueError):
        return  # unknown which folders are in use: keep them all
    used = {str(record.get("frames_folder")) for record in records if isinstance(record, dict)}
    for folder in folders:
        if folder not in used:
            _remove_studio_logo_frames(root, {"frames_folder": folder})


def rebuild_brand_memory(root: Path) -> dict[str, Any]:
    """Rebuild signatures from saved review queues; invalid legacy images are skipped."""
    root = root.resolve(strict=True)
    _write_json(root / MEMORY_PATH, _empty_memory())
    queues = 0
    remembered = 0
    for path in sorted((root / "reports").rglob("review-queue.json")):
        try:
            queue = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(queue, dict):
            continue
        queues += 1
        for item in queue.get("items", []):
            # A rebuild keeps the historical replace-and-append order.
            if isinstance(item, dict) and remember_review_item(
                root, queue, item, skip_unchanged=False,
            ) is not None:
                remembered += 1
    payload = load_brand_memory(root)
    return {
        "queues_scanned": queues,
        "records_written": len(payload["records"]),
        "decisions_processed": remembered,
        "memory_revision": memory_revision(payload),
        "automatic_edit": False,
    }
