"""A fixed logo beside the fixed text line that reads the same name (review build only).

Job 145 (session D, 2026-10-15): the source site's name is a fixed logo at the top
right, just under the site's scrolling line. The text scan read the logo all film
long, but its fixed-overlay track also took pieces of the line above, and the scan's
consensus box kept the line's box and dropped the logo's (12 joined tracks, most of
them pieces of the line). The film-long card blurred the line and left the logo. The
visual-logo scan read the same name with OCR in the logo's own box, in one window.

A visual-logo card whose OCR reading is a text a fixed-text card read on its own, and
whose box touches that card's box, shows the same mark:

- the fixed-text card's suggested region becomes the union of both boxes (a);
- the visual-logo card takes the fixed-text card's time (b); the persistent-overlay
  reconciliation then normally joins it into that card as supporting evidence.

Only a text card suggesting BLUR and a plain visual brand candidate not suggesting KEEP
take part. A general rule: nothing names a site. Fresh cards have no decision yet; a card
that has one is never changed (old decisions are carried later, by
``review_decision_carry``). Only ``review_workflow`` imports this module, so no scan
stage's cache key covers it.
"""
from __future__ import annotations

import difflib
import re
from typing import Callable

from biliflow.scrolling_text import fold_alnum
from biliflow.subtitle_band import REPEATED_WATERMARK_TYPE

FIXED_TEXT_TYPES = frozenset({"persistent_overlay", REPEATED_WATERMARK_TYPE})
# A plain visual brand candidate only. A persistent visual logo owns its watermark in the
# reconciliation: once inside the text card's box it would absorb that card and blur only
# its own box. Opening, ending and end cards keep their own time.
LOGO_CARD_TYPES = frozenset({None})
# A logo box already this much inside the text card's box needs nothing: the
# reconciliation joins it as before.
LOGO_ALREADY_COVERED = 0.95
MINIMUM_READING_LETTERS = 6
READING_SIMILARITY = 0.75
# The logo's box lies under or over the text line: at least half of its width in the
# line's columns, at most 8 px or half its own height away, at most 2.5 times as tall.
LOGO_COLUMN_SHARE = 0.50
LOGO_GAP_PIXELS = 8
LOGO_GAP_SHARE = 0.50
LOGO_HEIGHT_RATIO = 2.5
TEXT_CARD_REASON = (
    "Vùng làm mờ gồm cả logo ngay sát dòng chữ này: visual-logo đọc được cùng chữ ở đó"
)
LOGO_CARD_REASON = (
    "Cùng chỗ, cùng chữ với thẻ chữ cố định {start}–{end}; dùng thời gian của thẻ đó"
)
_MODEL_TOKENS = re.compile(r"</?s>|<pad>|<unk>")


def _readings(item: dict, generic_label: Callable[[str], bool]) -> list[str]:
    """Folded OCR readings of a card: no scanner label, grounding prompt or model token."""
    readings = []
    for label in item.get("labels") or []:
        cleaned = _MODEL_TOKENS.sub("", str(label)).strip()
        key = fold_alnum(cleaned)
        if cleaned and not generic_label(cleaned) and len(key) >= MINIMUM_READING_LETTERS:
            readings.append(key)
    return list(dict.fromkeys(readings))


def _same_text(logo: list[str], text: list[str]) -> bool:
    """The logo reads what the text card read on its own, not one word of a longer line."""
    return any(
        line in mark or difflib.SequenceMatcher(None, mark, line).ratio() >= READING_SIMILARITY
        for mark in logo for line in text
    )


def _region(item: dict) -> dict | None:
    region = item.get("suggested_region_source_pixels")
    if not isinstance(region, dict) or not {"x", "y", "width", "height"} <= set(region):
        return None
    return {key: int(region[key]) for key in ("x", "y", "width", "height")}


def _covered(logo: dict, text: dict) -> bool:
    width = min(text["x"] + text["width"], logo["x"] + logo["width"]) - max(text["x"], logo["x"])
    height = min(text["y"] + text["height"], logo["y"] + logo["height"]) - max(text["y"], logo["y"])
    return max(0, width) * max(0, height) >= LOGO_ALREADY_COVERED * logo["width"] * logo["height"]


def _touches(text: dict, logo: dict) -> bool:
    columns = (min(text["x"] + text["width"], logo["x"] + logo["width"])
               - max(text["x"], logo["x"]))
    gap = max(text["y"], logo["y"]) - min(text["y"] + text["height"], logo["y"] + logo["height"])
    return (
        not _covered(logo, text)
        and columns >= LOGO_COLUMN_SHARE * logo["width"]
        and gap <= max(LOGO_GAP_PIXELS, LOGO_GAP_SHARE * logo["height"])
        and logo["height"] <= LOGO_HEIGHT_RATIO * text["height"]
    )


def _union(first: dict, second: dict) -> dict:
    left, top = min(first["x"], second["x"]), min(first["y"], second["y"])
    right = max(first["x"] + first["width"], second["x"] + second["width"])
    bottom = max(first["y"] + first["height"], second["y"] + second["height"])
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def _intervals(item: dict) -> list[tuple[float, float]]:
    values = [
        (float(value["start_seconds"]), float(value["end_seconds"]))
        for value in item.get("detected_intervals") or [] if isinstance(value, dict)
    ]
    return values or [(float(item["start_seconds"]), float(item["end_seconds"]))]


def _overlaps_in_time(text: dict, logo: dict) -> bool:
    start, end = float(logo["start_seconds"]), float(logo["end_seconds"])
    return any(first < end and start < last for first, last in _intervals(text))


def _merged(intervals: list[tuple[float, float]]) -> list[dict]:
    moments: list[list[float]] = []
    for start, end in sorted(intervals):
        if moments and start <= moments[-1][1]:
            moments[-1][1] = max(moments[-1][1], end)
        else:
            moments.append([start, end])
    return [{"start_seconds": round(start, 3), "end_seconds": round(end, 3)} for start, end in moments]


def _clock(seconds: float) -> str:
    minutes, rest = divmod(int(round(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{rest:02d}" if hours else f"{minutes}:{rest:02d}"


def _logo_card(logo: dict, text: dict) -> dict:
    """The visual-logo card on the text card's time (b), keeping its own window."""
    moments = _merged(_intervals(text) + [(float(logo["start_seconds"]), float(logo["end_seconds"]))])
    updated = {
        **logo,
        "start_seconds": moments[0]["start_seconds"],
        "end_seconds": moments[-1]["end_seconds"],
        "detected_intervals": moments,
        "fixed_text_logo": {
            "text_card": text.get("id"),
            "previous_interval": {"start_seconds": logo["start_seconds"], "end_seconds": logo["end_seconds"]},
        },
        "reasons": list(dict.fromkeys([
            *(logo.get("reasons") or []),
            LOGO_CARD_REASON.format(start=_clock(float(text["start_seconds"])),
                                    end=_clock(float(text["end_seconds"]))),
        ])),
    }
    if text.get("temporal_policy"):
        updated["temporal_policy"] = text["temporal_policy"]
    else:
        updated.pop("temporal_policy", None)
    return updated


def _text_card(text: dict, logos: list[dict]) -> dict:
    """The fixed-text card whose region also covers the logo boxes beside it (a)."""
    region = previous = _region(text)
    assert region is not None
    for logo in logos:
        region = _union(region, _region(logo))
    return {
        **text,
        "suggested_region_source_pixels": region,
        "fixed_text_logo": {
            "previous_region": previous,
            "logo_regions": [_region(logo) for logo in logos],
            "logo_cards": [logo.get("id") for logo in logos],
        },
        "reasons": list(dict.fromkeys([*(text.get("reasons") or []), TEXT_CARD_REASON])),
    }


def _undecided(item: dict) -> bool:
    return item.get("decision") is None


def join_fixed_text_logos(items: list[dict], *, generic_label: Callable[[str], bool]) -> list[dict]:
    """``items`` with each matched fixed-text card and visual-logo card updated; inputs unchanged.

    ``generic_label`` tells a scanner label or grounding prompt from an OCR reading.
    """
    # Only a text card suggesting BLUR takes a logo in: the reconciliation then joins the
    # logo card into it, and a KEEP or no suggestion would hide the logo's BLUR.
    texts = [
        item for item in items
        if item.get("category") == "text" and item.get("candidate_type") in FIXED_TEXT_TYPES
        and item.get("suggested_decision") == "BLUR"
        and _undecided(item) and _region(item) is not None and _readings(item, generic_label)
    ]
    matches: dict[int, list[dict]] = {}
    logo_text: dict[int, dict] = {}
    for logo in items:
        sources = set((logo.get("model_evidence") or {}).get("region_sources") or [])
        logo_region = _region(logo)
        readings = _readings(logo, generic_label)
        if (logo.get("category") != "visual_logo" or logo.get("candidate_type") not in LOGO_CARD_TYPES
                or logo.get("review_kind") == "title_overlay" or logo.get("suggested_decision") == "KEEP"
                or "ocr" not in sources
                or logo_region is None or not readings or not _undecided(logo)):
            continue
        candidates = [
            text for text in texts
            if _touches(_region(text), logo_region) and _overlaps_in_time(text, logo)
            and _same_text(readings, _readings(text, generic_label))
        ]
        if not candidates:
            continue
        # The line the logo sits closest to; a tie goes to the longer card.
        text = min(candidates, key=lambda value: (
            abs((_region(value)["y"] + _region(value)["height"] / 2) - (logo_region["y"] + logo_region["height"] / 2)),
            -(float(value["end_seconds"]) - float(value["start_seconds"])),
        ))
        matches.setdefault(id(text), []).append(logo)
        logo_text[id(logo)] = text
    if not matches:
        return items
    updated = []
    for item in items:
        if id(item) in matches:
            updated.append(_text_card(item, matches[id(item)]))
        elif id(item) in logo_text:
            updated.append(_logo_card(item, logo_text[id(item)]))
        else:
            updated.append(item)
    return updated
