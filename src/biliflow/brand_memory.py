from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

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


def remember_review_item(
    root: Path, queue: dict[str, Any], item: dict[str, Any],
) -> dict[str, Any] | None:
    """Persist a local visual signature from an explicit human decision."""
    root = root.resolve(strict=True)
    source_sha256 = str(queue.get("source", {}).get("sha256", ""))
    key = f"{source_sha256}:{item.get('id', '')}"
    payload = load_brand_memory(root)
    payload["records"] = [record for record in payload["records"] if record.get("key") != key]
    if item.get("category") != "visual_logo" or item.get("decision") not in {
        "KEEP", "BLUR", "CUT"
    }:
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

STUDIO_LOGO_MEMORY_PATH = Path("state/studio-logo-memory.json")
STUDIO_LOGO_MEMORY_SCHEMA_VERSION = 1
STUDIO_LOGO_MINIMUM_SIMILARITY = 0.95
STUDIO_LOGO_GRID_SIZE = (32, 18)  # width, height of the colour grid
STUDIO_LOGO_MAXIMUM_CELL_DIFFERENCE = 20
_STUDIO_LOGO_MAX_SIGNATURES = 8  # frames stored per confirmed logo (matching checks every frame)


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
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != STUDIO_LOGO_MEMORY_SCHEMA_VERSION
        or not isinstance(payload.get("records"), list)
    ):
        raise ValueError("Unsupported studio-logo memory schema")
    return payload


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


def remember_studio_logo(
    root: Path, queue: dict[str, Any], item: dict[str, Any],
    signatures: list[dict[str, str]] | None = None,
    window_text: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Store a studio-logo signature for an explicit user KEEP of a full-frame logo card.

    ``window_text`` holds the OCR lines seen while the confirmed ident was on
    screen (``{"covered": bool, "texts": [...]}``). A later matching card may
    only show those lines; anything else keeps it in the main list.
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
    payload["records"].append(record)
    payload["updated_at"] = _now()
    _write_json(root / STUDIO_LOGO_MEMORY_PATH, payload)
    return record


def forget_studio_logo(root: Path, queue: dict[str, Any], item_id: str) -> bool:
    root = root.resolve(strict=True)
    if not (root / STUDIO_LOGO_MEMORY_PATH).exists():
        return False
    source_sha256 = str(queue.get("source", {}).get("sha256", ""))
    key = f"{source_sha256}:{item_id}"
    payload = load_studio_logo_memory(root)
    before = len(payload["records"])
    payload["records"] = [record for record in payload["records"] if record.get("key") != key]
    if len(payload["records"]) == before:
        return False
    payload["updated_at"] = _now()
    _write_json(root / STUDIO_LOGO_MEMORY_PATH, payload)
    return True


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
    signatures = studio_logo_signatures(root, item, strict=True)
    if not signatures:
        return None
    weakest: float | None = None
    worst_cells = 0
    matched: dict[str, dict[str, Any]] = {}
    for signature in signatures:
        best: tuple[tuple[float, int], dict[str, Any], float, int] | None = None
        for record in records:
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
                    best = ((similarity, -cells), record, similarity, cells)
        if best is None:
            return None
        _, record, similarity, cells = best
        matched.setdefault(str(record.get("key")), record)
        if weakest is None or similarity < weakest:
            weakest = similarity
        worst_cells = max(worst_cells, cells)
    assert weakest is not None and matched
    primary = next(iter(matched.values()))
    known_texts = list(dict.fromkeys(
        str(text) for record in matched.values()
        for text in ((record.get("window_text") or {}).get("texts") or [])
    ))
    return {
        "similarity": round(weakest, 6),
        "minimum_similarity": minimum_similarity,
        "cell_difference": worst_cells,
        "maximum_cell_difference": maximum_cell_difference,
        "memory_key": primary.get("key"),
        "memory_keys": list(matched),
        "labels": list(primary.get("labels") or []),
        "source_sha256": primary.get("source_sha256"),
        "review_item_id": primary.get("review_item_id"),
        "matched_frames": len(signatures),
        "known_texts": known_texts,
        "automatic_edit": False,
    }


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
            if isinstance(item, dict) and remember_review_item(root, queue, item) is not None:
                remembered += 1
    payload = load_brand_memory(root)
    return {
        "queues_scanned": queues,
        "records_written": len(payload["records"]),
        "decisions_processed": remembered,
        "memory_revision": memory_revision(payload),
        "automatic_edit": False,
    }
