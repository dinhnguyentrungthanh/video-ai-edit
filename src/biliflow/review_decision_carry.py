"""Keep the reviewer's decisions when a review queue is built again from its scan reports.

A rebuild (``build_review_queue(keep_decisions=True)``) can change cards: a newer
review step may join pieces into one longer card (scrolling text, 2026-10-15) or
start a card earlier. Each decided card of the previous queue is carried:

- to the new card with the same id: exactly, the reviewed interval included;
- else to a new card showing the same thing (same category, at least 80 % of the
  shorter card's time shared, boxes overlapping by 75 % of the smaller one):
  BLUR always, its time and box widened to cover the old ones too (a blur never
  shrinks); KEEP, CUT and NEEDS_MORE_CONTEXT only to a card the old one covered
  for 80 % of its time (a KEEP of one piece is not a KEEP of a whole line);
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
CARRY_SAME_SPAN = 0.80  # share of the new card's time a carried KEEP/CUT covered
PRESERVED_DECIDED = "preserved_decided_from_previous_queue"
PRESERVED_DECIDED_REASON = (
    "Quyết định cũ của bạn được giữ nguyên khi dựng lại danh sách duyệt "
    "(không có thẻ mới nào trùng đúng thẻ này)"
)


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


def _covers_new_card(old: dict, new: dict) -> bool:
    duration = _span(new)[1] - _span(new)[0]
    return duration > 0 and _shared_seconds(old, new) / duration >= CARRY_SAME_SPAN


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
    if new["decision"] == "BLUR":
        _widen_blur(new, sources)
    new["decision_carried_from"] = [str(source["id"]) for source in sources]


def _carry_main(new: dict, decided: list[dict], by_id: dict[str, dict], carried: set[str]) -> bool:
    """Carry the old decision(s) onto one new main card; True when they conflict."""
    if new.get("decision") is not None:
        return False
    exact = by_id.get(str(new.get("id")))
    if exact is not None:
        _copy_exact(new, exact)
        carried.add(str(exact["id"]))
        return False
    sources = [old for old in decided if _same_thing(old, new)]
    if len({source["decision"] for source in sources}) > 1:
        new["decision_conflict"] = sorted(str(source["id"]) for source in sources)
        return True
    if sources and (
        sources[0]["decision"] == "BLUR" or all(_covers_new_card(source, new) for source in sources)
    ):
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
    carried: set[str] = set()
    conflicts = 0
    output = []
    for item in items:
        new = dict(item)
        conflicts += _carry_main(new, decided, by_id, carried)
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
    restored = 0
    for old in decided:
        if str(old["id"]) in carried or str(old["id"]) in present:
            continue
        kept = dict(old)
        kept["migration_status"] = PRESERVED_DECIDED
        kept["reasons"] = list(dict.fromkeys(list(kept.get("reasons") or []) + [PRESERVED_DECIDED_REASON]))
        output.append(kept)
        restored += 1
    return output, optional, {
        "previous_decisions": len(decided),
        "carried": len(carried),
        "restored_unchanged": restored,
        "conflicts": conflicts,
    }
