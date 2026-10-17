"""Keep the reviewer's decisions when a review queue is built again from its scan reports.

A rebuild (``build_review_queue(keep_decisions=True)``) can change cards: a newer
review step may join pieces into one longer card (scrolling text, 2026-10-15) or
start a card earlier. Each decided card of the previous queue is carried:

- to the new card with the same id and the same moments: exactly, the reviewed
  interval included. Such an old card (or one whose id an optional card has)
  decides no other card;
- else to a new card showing the same thing (same category, at least 80 % of the
  shorter card's time shared, boxes overlapping by 75 % of the smaller one):
  - BLUR always. On a card of one span its time and box widen to cover the old
    ones too (a blur never shrinks). On a card of separate moments
    (``discrete_detected_intervals``) it keeps the new card's own region; an
    old card that region and those moments do not cover comes back unchanged
    beside it;
  - KEEP, CUT and NEEDS_MORE_CONTEXT only to a card that shows nothing the old
    one did not: its time inside the reviewed time, its box inside the old box,
    and neither card of separate moments (a KEEP of one piece is not a KEEP of a
    whole line, and a CUT never reaches time nobody reviewed);
- when the old cards matching one new card disagree, that card stays open and
  names them in ``decision_conflict``.

Every decided card that was carried nowhere comes back unchanged (with
``migration_status``), so no decision is lost and none is invented.
"""
from __future__ import annotations

from typing import Iterable

DECIDED = frozenset({"KEEP", "BLUR", "CUT", "NEEDS_MORE_CONTEXT"})
DECISION_FIELDS = (
    "decision", "decision_note", "decision_region_source_pixels", "decision_blur_edge_mode",
    "decision_actor", "decision_transport", "decided_at", "studio_logo_memory", "platform_logo_memory",
)
CARRY_TIME_OVERLAP = 0.80  # share of the shorter card's time both cards cover
CARRY_REGION_OVERLAP = 0.75  # intersection over the smaller box
CARRY_INSIDE = 0.80  # share of the new card's box inside the old one for a carried KEEP/CUT
CARRY_TIME_TOLERANCE = 0.05  # seconds a carried KEEP/CUT card may reach past the reviewed time
DISCRETE = "discrete_detected_intervals"
PRESERVED_DECIDED = "preserved_decided_from_previous_queue"
PRESERVED_DECIDED_REASON = (
    "Quyết định cũ của bạn được giữ nguyên khi dựng lại danh sách duyệt "
    "(không có thẻ mới nào trùng đúng thẻ này)"
)
# Why a decided card came back unchanged (summary only, "Dựng lại danh sách duyệt (giữ quyết định)"):
# old cards disagreeing on one new card; a KEEP/CUT/NEEDS_MORE_CONTEXT meeting only a card that shows more;
# a BLUR whose moments or box lie outside the separate moments of the new card; no new card showing it.
RESTORED_REASONS = ("conflict", "not_wider", "moments_outside", "no_match")


def _box(value: object) -> dict | None:
    if not isinstance(value, dict) or not all(key in value for key in ("x", "y", "width", "height")):
        return None
    return {key: int(value[key]) for key in ("x", "y", "width", "height")}


def _overlap_over_smaller(first: dict, second: dict) -> float:
    width = min(first["x"] + first["width"], second["x"] + second["width"]) - max(first["x"], second["x"])
    height = min(first["y"] + first["height"], second["y"] + second["height"]) - max(first["y"], second["y"])
    smaller = min(first["width"] * first["height"], second["width"] * second["height"])
    return max(0, width) * max(0, height) / smaller if smaller > 0 else 0.0


def _union(boxes: Iterable[dict]) -> dict:
    boxes = list(boxes)
    left = min(box["x"] for box in boxes)
    top = min(box["y"] for box in boxes)
    right = max(box["x"] + box["width"] for box in boxes)
    bottom = max(box["y"] + box["height"] for box in boxes)
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def _span(item: dict) -> tuple[float, float]:
    return float(item.get("start_seconds") or 0.0), float(item.get("end_seconds") or 0.0)


def _shared_seconds(first: dict, second: dict) -> float:
    (first_start, first_end), (second_start, second_end) = _span(first), _span(second)
    return max(0.0, min(first_end, second_end) - max(first_start, second_start))


def _same_thing(old: dict, new: dict) -> bool:
    if old.get("category") != new.get("category"):
        return False
    shorter = min(max(0.0, _span(old)[1] - _span(old)[0]), max(0.0, _span(new)[1] - _span(new)[0]))
    if shorter <= 0 or _shared_seconds(old, new) / shorter < CARRY_TIME_OVERLAP:
        return False
    old_box = _box(old.get("suggested_region_source_pixels")) or _box(old.get("decision_region_source_pixels"))
    new_box = _box(new.get("suggested_region_source_pixels"))
    if old_box is not None and new_box is not None:
        return _overlap_over_smaller(old_box, new_box) >= CARRY_REGION_OVERLAP
    return old.get("candidate_type") == new.get("candidate_type")


def _discrete(item: dict) -> bool:
    return item.get("temporal_policy") == DISCRETE


def _moments(item: dict) -> list[tuple[float, float]]:
    if _discrete(item):
        intervals = [
            (round(float(value["start_seconds"]), 3), round(float(value["end_seconds"]), 3))
            for value in item.get("detected_intervals") or [] if isinstance(value, dict)
        ]
        if intervals:
            return sorted(intervals)
    return [tuple(round(value, 3) for value in _span(item))]


def _same_moments(old: dict, new: dict) -> bool:
    return _discrete(old) == _discrete(new) and _moments(old) == _moments(new)


def _inside(inner: dict | None, outer: dict | None) -> bool:
    """``inner`` lies inside ``outer`` for CARRY_INSIDE of its area (two missing boxes: True)."""
    if inner is None or outer is None:
        return inner is None and outer is None
    width = min(inner["x"] + inner["width"], outer["x"] + outer["width"]) - max(inner["x"], outer["x"])
    height = min(inner["y"] + inner["height"], outer["y"] + outer["height"]) - max(inner["y"], outer["y"])
    area = inner["width"] * inner["height"]
    return area > 0 and max(0, width) * max(0, height) / area >= CARRY_INSIDE


def _old_box(old: dict) -> dict | None:
    return _box(old.get("decision_region_source_pixels")) or _box(old.get("suggested_region_source_pixels"))


def _shows_no_more(old: dict, new: dict) -> bool:
    """A KEEP, CUT or NEEDS_MORE_CONTEXT of ``old`` may resolve ``new``: nothing unreviewed."""
    if _discrete(old) or _discrete(new):
        return False
    (old_start, old_end), (new_start, new_end) = _span(old), _span(new)
    return (
        new_start >= old_start - CARRY_TIME_TOLERANCE and new_end <= old_end + CARRY_TIME_TOLERANCE
        and _inside(_box(new.get("suggested_region_source_pixels")), _old_box(old))
    )


def _covered_by_moments(old: dict, new: dict) -> bool:
    """``old``'s reviewed moments (its span for a card of one) and blur region lie inside ``new``'s
    moments and region."""
    if not all(
        any(moment_start - CARRY_TIME_TOLERANCE <= start and end <= moment_end + CARRY_TIME_TOLERANCE
            for moment_start, moment_end in _moments(new))
        for start, end in _moments(old)
    ):
        return False
    region = old.get("decision_region_source_pixels")
    if region == "FULL_FRAME":
        return new.get("decision_region_source_pixels") == "FULL_FRAME"
    return _inside(_box(region), _box(new.get("decision_region_source_pixels")))


def _copy_exact(new: dict, old: dict) -> None:
    for field in DECISION_FIELDS:
        if field in old:
            new[field] = old[field]
    if "detected_interval" in old:  # the reviewer's own interval
        new["detected_interval"] = old["detected_interval"]
        new["start_seconds"], new["end_seconds"] = old["start_seconds"], old["end_seconds"]
    new["decision_carried_from"] = [str(old["id"])]


def _widen_blur(new: dict, sources: list[dict]) -> None:
    """A carried blur covers the new card and every old box and interval it replaces."""
    chosen = [source.get("decision_region_source_pixels") for source in sources]
    if any(region == "FULL_FRAME" for region in chosen):
        new["decision_region_source_pixels"] = "FULL_FRAME"
    else:
        boxes = [box for box in (_box(region) for region in chosen) if box is not None]
        suggested = _box(new.get("suggested_region_source_pixels"))
        if suggested is not None:
            boxes.append(suggested)
        if boxes:
            new["decision_region_source_pixels"] = _union(boxes)
        frame = new.get("source_frame_size")
        region = _box(new.get("decision_region_source_pixels"))
        if (
            new.get("suggested_blur_edge_mode") == "vertical_only" and region is not None
            and isinstance(frame, list) and len(frame) == 2
            and region["x"] == 0 and region["width"] >= int(frame[0])
        ):
            new["decision_blur_edge_mode"] = "vertical_only"
    start = min([_span(new)[0], *(_span(source)[0] for source in sources)])
    end = max([_span(new)[1], *(_span(source)[1] for source in sources)])
    if (start, end) != _span(new):
        new["detected_interval"] = {"start_seconds": _span(new)[0], "end_seconds": _span(new)[1]}
        new["start_seconds"], new["end_seconds"] = round(start, 3), round(end, 3)


def _copy_matched(new: dict, sources: list[dict]) -> None:
    latest = max(sources, key=lambda source: str(source.get("decided_at") or ""))
    for field in DECISION_FIELDS:
        if field in latest:
            new[field] = latest[field]
    if new["decision"] == "BLUR" and not _discrete(new):
        _widen_blur(new, sources)
    elif new["decision"] == "BLUR":
        # Separate moments: the new card's own region; old blurs it misses stay beside it.
        suggested = _box(new.get("suggested_region_source_pixels"))
        if suggested is not None:
            new["decision_region_source_pixels"] = suggested
    new["decision_carried_from"] = [str(source["id"]) for source in sources]


def _carry_main(new: dict, decided: list[dict], by_id: dict[str, dict], carried: set[str], homed: set[str]) -> bool:
    """Carry the old decision(s) onto one new main card; True when they conflict."""
    if new.get("decision") is not None:
        return False
    exact = by_id.get(str(new.get("id")))
    if exact is not None and _same_moments(exact, new):
        _copy_exact(new, exact)
        carried.add(str(exact["id"]))
        return False
    sources = [old for old in decided if str(old["id"]) not in homed and _same_thing(old, new)]
    if len({source["decision"] for source in sources}) > 1:
        new["decision_conflict"] = sorted(str(source["id"]) for source in sources)
        return True
    if not sources:
        return False
    if sources[0]["decision"] == "BLUR":
        _copy_matched(new, sources)
        carried.update(
            str(source["id"]) for source in sources
            if not _discrete(new) or _covered_by_moments(source, new)
        )
    elif all(_shows_no_more(source, new) for source in sources):
        _copy_matched(new, sources)
        carried.update(str(source["id"]) for source in sources)
    return False


def carry_review_decisions(
    previous_items: list[dict], items: list[dict], advisory_items: list[dict],
) -> tuple[list[dict], list[dict], dict]:
    """(main cards with the old decisions, optional cards, a summary). Inputs are not modified."""
    decided = [
        item for item in previous_items
        if isinstance(item, dict) and item.get("decision") in DECIDED and item.get("id")
    ]
    by_id = {str(item["id"]): item for item in decided}
    # An old card whose own card is still there (its id, and for a main card its moments) goes to it
    # and decides no other card (job 134 rev 2: a KEEP on a subtitle line inside the box and time of
    # the grown watermark card left that card open as a conflict).
    homed = {
        str(item["id"]) for item in items
        if item.get("decision") is None and str(item.get("id")) in by_id
        and _same_moments(by_id[str(item["id"])], item)
    } | {
        str(item["id"]) for item in advisory_items
        if item.get("decision") is None and str(item.get("id")) in by_id
    }
    carried: set[str] = set()
    conflicts = 0
    output = []
    for item in items:
        new = dict(item)
        conflicts += _carry_main(new, decided, by_id, carried, homed)
        output.append(new)
    optional = []
    for item in advisory_items:
        exact = by_id.get(str(item.get("id")))
        if exact is None or item.get("decision") is not None:
            optional.append(item)
            continue
        moved = dict(item)
        moved.pop("advisory", None)  # a decided card belongs to the main list
        _copy_exact(moved, exact)
        carried.add(str(exact["id"]))
        output.append(moved)
    present = {str(item.get("id")) for item in output}
    conflicted = {str(old_id) for item in output for old_id in item.get("decision_conflict") or []}
    reasons = dict.fromkeys(RESTORED_REASONS, 0)
    restored = 0
    for old in decided:
        if str(old["id"]) in carried:
            continue
        reasons[_restored_reason(old, items, conflicted)] += 1
        kept = dict(old)
        if str(old["id"]) in present:
            # A new card took this id (other moments): the old decision stays under its own name.
            suffix = 1
            while f'{old["id"]}-previous-{suffix}' in present:
                suffix += 1
            kept["id"] = f'{old["id"]}-previous-{suffix}'
            kept["previous_id"] = str(old["id"])
            present.add(kept["id"])
        kept["migration_status"] = PRESERVED_DECIDED
        kept["reasons"] = list(dict.fromkeys(list(kept.get("reasons") or []) + [PRESERVED_DECIDED_REASON]))
        output.append(kept)
        restored += 1
    return output, optional, {
        "previous_decisions": len(decided),
        "carried": len(carried),
        "restored_unchanged": restored,
        "conflicts": conflicts,
        "restored_reasons": reasons,
    }


def _restored_reason(old: dict, items: list[dict], conflicted: set[str]) -> str:
    """Which RESTORED_REASONS sent ``old`` back unchanged (``items``: the new main cards)."""
    if str(old["id"]) in conflicted:
        return "conflict"
    matches = [new for new in items if _same_thing(old, new)]
    if old["decision"] == "BLUR" and any(_discrete(new) for new in matches):
        return "moments_outside"
    if old["decision"] != "BLUR" and matches:
        return "not_wider"
    return "no_match"
