from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


@dataclass(frozen=True)
class RegionProposal:
    box: tuple[float, float, float, float]
    sources: tuple[str, ...]
    labels: tuple[str, ...]


def box_iou(first: Iterable[float], second: Iterable[float]) -> float:
    a = tuple(float(value) for value in first)
    b = tuple(float(value) for value in second)
    if len(a) != 4 or len(b) != 4:
        raise ValueError("Boxes must contain x1, y1, x2, y2")
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    first_area = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    second_area = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = first_area + second_area - intersection
    return intersection / union if union else 0.0


def quad_to_box(quad: Iterable[float]) -> tuple[float, float, float, float]:
    values = tuple(float(value) for value in quad)
    if len(values) != 8:
        raise ValueError("OCR quadrilateral must contain four x/y points")
    xs, ys = values[0::2], values[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def _valid_box(
    box: tuple[float, float, float, float],
    image_size: tuple[int, int],
    *,
    maximum_area_ratio: float,
    minimum_side_pixels: float,
) -> bool:
    width, height = image_size
    x1, y1, x2, y2 = box
    box_width, box_height = x2 - x1, y2 - y1
    if box_width < minimum_side_pixels or box_height < minimum_side_pixels:
        return False
    area_ratio = box_width * box_height / max(1.0, float(width * height))
    return area_ratio <= maximum_area_ratio


def consolidate_region_proposals(
    *,
    grounding_boxes: Iterable[Iterable[float]],
    grounding_labels: Iterable[str],
    ocr_quads: Iterable[Iterable[float]],
    ocr_labels: Iterable[str],
    image_size: tuple[int, int],
    maximum_area_ratio: float = 0.72,
    minimum_side_pixels: float = 3.0,
    merge_iou: float = 0.45,
) -> list[RegionProposal]:
    """Filter Florence hallucinations and merge duplicate localization boxes.

    This function only localizes an interval already confirmed by the semantic
    VLM. It must never be used to decide whether a frame contains advertising.
    """
    if not 0 < maximum_area_ratio <= 1 or not 0 <= merge_iou <= 1:
        raise ValueError("Invalid region proposal thresholds")
    candidates: list[RegionProposal] = []
    for raw_box, label in zip(grounding_boxes, grounding_labels, strict=False):
        box = tuple(float(value) for value in raw_box)
        if len(box) == 4 and _valid_box(
            box, image_size, maximum_area_ratio=maximum_area_ratio,
            minimum_side_pixels=minimum_side_pixels,
        ):
            candidates.append(RegionProposal(box, ("grounding",), (str(label),)))
    for raw_quad, label in zip(ocr_quads, ocr_labels, strict=False):
        box = quad_to_box(raw_quad)
        if _valid_box(
            box, image_size, maximum_area_ratio=maximum_area_ratio,
            minimum_side_pixels=minimum_side_pixels,
        ):
            candidates.append(RegionProposal(box, ("ocr",), (str(label),)))

    merged: list[RegionProposal] = []
    for candidate in sorted(
        candidates,
        key=lambda item: -(
            (item.box[2] - item.box[0]) * (item.box[3] - item.box[1])
        ),
    ):
        match_index = next(
            (
                index for index, existing in enumerate(merged)
                if box_iou(candidate.box, existing.box) >= merge_iou
            ),
            None,
        )
        if match_index is None:
            merged.append(candidate)
            continue
        existing = merged[match_index]
        sources = tuple(dict.fromkeys(existing.sources + candidate.sources))
        labels = tuple(dict.fromkeys(existing.labels + candidate.labels))
        # Prefer the OCR box when available because it normally hugs text more
        # closely than phrase grounding. Otherwise keep the first/larger box.
        preferred = candidate.box if "ocr" in candidate.sources else existing.box
        merged[match_index] = RegionProposal(preferred, sources, labels)
    return merged


_SITE_MARK_PATTERN = re.compile(
    r"[a-z0-9][a-z0-9-]{1,30}"
    r"\.(?:com|net|org|tv|io|co|vn|me|media|info|biz|site|xyz|online|cc|to|vip)\b"
)


def _clean_label(raw_label: str) -> str:
    return str(raw_label).casefold().replace("</s>", "").replace("<s>", "").strip()


def looks_like_site_mark(label: str) -> bool:
    """Detect a hosting/site style watermark without hardcoding any brand.

    A persistent watermark is normally a bare domain or one compact word token.
    This is a structural test on the OCR string, so an unseen site is ranked
    correctly without being added to any list.
    """
    text = _clean_label(label)
    if not text or len(text) > 40:
        return False
    if _SITE_MARK_PATTERN.search(text):
        return True
    compact = re.sub(r"[^a-z0-9]+", "", text)
    # A plain dictionary word such as "welcome" or "courtesy" is story text,
    # not structural watermark evidence.  Keep only compact tokens with a
    # non-word signature (mixed digits) or a repeated brand-like stem such as
    # "bilibili"; other single-word brands still need exact VLM/OCR agreement.
    repeated_stem = (
        len(compact) >= 6
        and len(compact) % 2 == 0
        and compact[: len(compact) // 2] == compact[len(compact) // 2 :]
    )
    mixed_digits = len(compact) >= 5 and any(
        char.isdigit() for char in compact
    ) and any(char.isalpha() for char in compact)
    return compact == text and (repeated_stem or mixed_digits)


def select_compact_overlay_proposals(
    proposals: Iterable[RegionProposal],
    image_size: tuple[int, int],
    *,
    maximum_area_ratio: float = 0.04,
    border_fraction: float = 0.18,
) -> list[RegionProposal]:
    """Keep only boxes shaped and placed like a persistent overlay watermark.

    Two properties define the target and exclude frame-level leakage. It is
    small: a site watermark covers a low single-digit percentage of the frame,
    while a grounding box that spans half the scene does not. It is peripheral:
    an overlay sits in the outer margin so it does not obscure the picture,
    while a subject's face sits in the image area. Area alone is not enough,
    because a box drawn over a head can be small and still be wrong.

    The result may be empty so the caller can fall back to advisory review
    rather than propose a region that is confidently wrong.
    """
    if not 0 < maximum_area_ratio <= 1:
        raise ValueError("maximum_area_ratio must be within (0, 1]")
    if not 0 < border_fraction < 0.5:
        raise ValueError("border_fraction must be within (0, 0.5)")
    width, height = float(image_size[0]), float(image_size[1])
    frame_area = max(1.0, width * height)
    left_limit, right_limit = width * border_fraction, width * (1.0 - border_fraction)
    top_limit, bottom_limit = height * border_fraction, height * (1.0 - border_fraction)
    kept: list[RegionProposal] = []
    for item in proposals:
        x1, y1, x2, y2 = item.box
        if max(0.0, x2 - x1) * max(0.0, y2 - y1) / frame_area > maximum_area_ratio:
            continue
        center_x, center_y = (x1 + x2) / 2, (y1 + y2) / 2
        peripheral = (
            center_x <= left_limit or center_x >= right_limit
            or center_y <= top_limit or center_y >= bottom_limit
        )
        if peripheral:
            kept.append(item)
    return kept


def select_brand_region_proposals(
    proposals: Iterable[RegionProposal], confirmed_brand: str | None
) -> list[RegionProposal]:
    """Prefer OCR regions compatible with the brand already confirmed by Qwen.

    The brand is an input from the semantic stage, never a coded brand list. A
    single-letter OCR result is accepted only when it is the confirmed brand's
    initial, which covers graphical initials such as the Netflix N.
    """
    items = list(proposals)
    brand = re.sub(r"[^a-z0-9]+", "", (confirmed_brand or "").casefold())
    if not brand:
        supported = [item for item in items if len(item.sources) > 1]
        return supported or items

    matched: list[RegionProposal] = []
    for item in items:
        if "ocr" not in item.sources:
            continue
        for raw_label in item.labels:
            clean_label = raw_label.casefold().replace("</s>", "").replace("<s>", "")
            label = re.sub(r"[^a-z0-9]+", "", clean_label)
            if not label:
                continue
            if (
                brand in label
                or (len(label) >= 4 and label in brand)
                or (len(label) == 1 and label == brand[0])
            ):
                matched.append(item)
                break
    if matched:
        return matched
    supported = [item for item in items if len(item.sources) > 1]
    return supported or items


def select_focus_region_proposals(
    proposals: Iterable[RegionProposal],
    focus_box: Iterable[float] | None,
    *,
    allow_fallback: bool = False,
) -> list[RegionProposal]:
    """Keep localized boxes whose center lies in a coarse detector focus tile.

    This fails closed: when the coarse router points at a region and no box
    lands there, the result is empty rather than every box in the frame. A
    proposal that contradicts the router is worse than no proposal, because it
    sends the reviewer to a region the detector never flagged. Pass
    ``allow_fallback`` only where a superset is genuinely wanted.
    """
    items = list(proposals)
    if focus_box is None:
        return items
    values = tuple(float(value) for value in focus_box)
    if len(values) != 4:
        return items
    left, top, right, bottom = values
    focused = [
        item for item in items
        if left <= (item.box[0] + item.box[2]) / 2 <= right
        and top <= (item.box[1] + item.box[3]) / 2 <= bottom
    ]
    if focused or not allow_fallback:
        return focused
    return items


def prioritize_brand_region_proposals(
    proposals: Iterable[RegionProposal], confirmed_brand: str | None,
) -> list[RegionProposal]:
    """Put the most useful blur proposal first without discarding alternatives."""
    items = list(proposals)
    if confirmed_brand:
        return items

    def rank(item: RegionProposal) -> tuple[int, float]:
        site_like = "ocr" in item.sources and any(
            looks_like_site_mark(label) for label in item.labels
        )
        if site_like:
            # Read text that is structurally a site mark outranks a bare
            # grounding box: the mark itself is stronger evidence than a
            # phrase-grounding guess about where a logo might be.
            source_rank = 0
        elif len(item.sources) > 1:
            source_rank = 1
        elif "grounding" in item.sources:
            source_rank = 2
        else:
            source_rank = 3
        area = max(0.0, item.box[2] - item.box[0]) * max(0.0, item.box[3] - item.box[1])
        return source_rank, area

    return sorted(items, key=rank)
