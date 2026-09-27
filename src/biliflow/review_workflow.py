from __future__ import annotations

import hashlib
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable

from biliflow.brand_memory import forget_review_item, remember_review_item
from biliflow.job_pipeline import DEFAULT_DETECTOR_GROUPS
from biliflow.blur_filter import (
    blur_feather_mode,
    blur_parameters,
    regional_blur_filters,
)
from biliflow.probe import duration_seconds, probe_video
from biliflow.final_renderer import (
    authorize_final_from_resolved_review,
    normalize_output_size_policy,
    render_final_output,
)


DECISIONS = ("KEEP", "BLUR", "CUT", "NEEDS_MORE_CONTEXT")
_PRIORITY_RANK = {"high": 0, "context": 1, "normal": 2}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _inside(root: Path, path: Path, label: str) -> Path:
    root = root.resolve(strict=True)
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"{label} must stay inside {root}")
    return resolved


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _item_id(category: str, start: float, end: float, evidence: str) -> str:
    token = f"{category}|{start:.3f}|{end:.3f}|{evidence}".encode("utf-8")
    return f"review-{hashlib.sha256(token).hexdigest()[:12]}"


def ensure_unique_review_item_ids(items: list[dict]) -> list[dict]:
    """Assign deterministic IDs that distinguish independent regions in one scene."""
    used: set[str] = set()
    for item in items:
        region = item.get("suggested_region_source_pixels")
        if isinstance(region, dict):
            region_token = ",".join(str(int(region[key])) for key in ("x", "y", "width", "height"))
        else:
            region_token = "full"
        identity = "|".join(str(value) for value in item.get("evidence") or [])
        identity += (
            f"|candidate:{item.get('candidate_type')}"
            f"|kind:{item.get('review_kind')}"
            f"|class:{item.get('region_classification')}"
            f"|region:{region_token}"
        )
        occurrence = 0
        while True:
            suffix = identity if occurrence == 0 else f"{identity}|duplicate:{occurrence}"
            candidate = _item_id(
                str(item.get("category") or "unknown"),
                float(item.get("start_seconds") or 0),
                float(item.get("end_seconds") or 0),
                suffix,
            )
            if candidate not in used:
                break
            occurrence += 1
        item["id"] = candidate
        used.add(candidate)
    return items


def _priority(interval: dict) -> str:
    explicit = str(interval.get("priority", "")).casefold()
    if explicit in _PRIORITY_RANK:
        return explicit
    reason = str(interval.get("reason", "")).casefold()
    if "high" in reason:
        return "high"
    return "context"


def promote_strong_adult_priorities(items: list[dict]) -> list[dict]:
    """Put sustained, high-confidence explicit scenes at the front of review.

    This only changes review ordering. It never creates a finding or assigns
    an edit decision, and therefore cannot turn weak evidence into an edit.
    """
    promoted = []
    for original in items:
        item = dict(original)
        labels = {str(value).strip().casefold() for value in item.get("labels") or []}
        try:
            score = float(item.get("max_score") or 0.0)
            duration = float(item.get("end_seconds") or 0.0) - float(
                item.get("start_seconds") or 0.0
            )
        except (TypeError, ValueError):
            score, duration = 0.0, 0.0
        if (
            item.get("category") == "adult"
            and labels.intersection({"porn", "hentai"})
            and score >= 0.99
            and duration >= 4.0
        ):
            item["priority"] = "high"
        promoted.append(item)
    return promoted


def _review_label(category: str, value: object) -> str | None:
    if value is None:
        return None
    label = str(value).strip()
    if not label:
        return None
    if category == "visual_logo":
        folded = " ".join(label.casefold().split())
        prompt_fragments = (
            "brand name when known",
            "when branding exists",
            "we are finding graphics",
            "graphics added before",
            "actual brand name",
        )
        if any(fragment in folded for fragment in prompt_fragments):
            return None
    return label


def union_pixel_regions(first: dict | None, second: dict | None) -> dict | None:
    if first is None:
        return dict(second) if second is not None else None
    if second is None:
        return dict(first)
    left = min(int(first["x"]), int(second["x"]))
    top = min(int(first["y"]), int(second["y"]))
    right = max(
        int(first["x"]) + int(first["width"]),
        int(second["x"]) + int(second["width"]),
    )
    bottom = max(
        int(first["y"]) + int(first["height"]),
        int(second["y"]) + int(second["height"]),
    )
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def tighten_text_region(region: dict) -> dict:
    """Tighten OCR bottom padding while preserving the travel path and top edge.

    EasyOCR's union is useful for horizontal motion but tends to retain more
    padding below a single text row. The trim scales with the detected row
    height, so it is not a fixed pixel band shared by videos.
    """
    output = {key: int(region[key]) for key in ("x", "y", "width", "height")}
    height = output["height"]
    if height >= 24:
        bottom_trim = min(8, max(2, round(height * 0.10)))
        if height - bottom_trim >= 12:
            output["height"] -= bottom_trim
    return output


def pixel_region_iou(first: dict | None, second: dict | None) -> float | None:
    if not isinstance(first, dict) or not isinstance(second, dict):
        return None
    ax1, ay1 = int(first["x"]), int(first["y"])
    ax2, ay2 = ax1 + int(first["width"]), ay1 + int(first["height"])
    bx1, by1 = int(second["x"]), int(second["y"])
    bx2, by2 = bx1 + int(second["width"]), by1 + int(second["height"])
    intersection = max(0, min(ax2, bx2) - max(ax1, bx1)) * max(
        0, min(ay2, by2) - max(ay1, by1)
    )
    union = max(0, ax2 - ax1) * max(0, ay2 - ay1) + max(
        0, bx2 - bx1
    ) * max(0, by2 - by1) - intersection
    return intersection / union if union else 0.0


def _intersection_over_smaller(first: dict, second: dict) -> float:
    ax1, ay1 = int(first["x"]), int(first["y"])
    ax2, ay2 = ax1 + int(first["width"]), ay1 + int(first["height"])
    bx1, by1 = int(second["x"]), int(second["y"])
    bx2, by2 = bx1 + int(second["width"]), by1 + int(second["height"])
    intersection = max(0, min(ax2, bx2) - max(ax1, bx1)) * max(
        0, min(ay2, by2) - max(ay1, by1)
    )
    smaller = min(
        max(1, int(first["width"]) * int(first["height"])),
        max(1, int(second["width"]) * int(second["height"])),
    )
    return intersection / smaller


def _dominant_region_cluster(regions: list[dict]) -> list[dict]:
    """Keep the repeated spatial target and reject nearby OCR outliers."""
    clusters: list[list[dict]] = []
    for region in sorted(
        regions, key=lambda value: int(value["width"]) * int(value["height"]),
    ):
        area = max(1, int(region["width"]) * int(region["height"]))
        for cluster in clusters:
            reference = cluster[0]
            reference_area = max(
                1, int(reference["width"]) * int(reference["height"]),
            )
            area_ratio = max(area, reference_area) / min(area, reference_area)
            if (
                area_ratio <= 2.0
                and _intersection_over_smaller(region, reference) >= 0.70
            ):
                cluster.append(region)
                break
        else:
            clusters.append([region])
    return max(
        clusters,
        key=lambda cluster: (
            len(cluster),
            -sum(int(item["width"]) * int(item["height"]) for item in cluster),
        ),
        default=[],
    )


def refine_persistent_logo_regions(items: list[dict]) -> list[dict]:
    """Use repeated tight observations to correct an oversized persistent box.

    Grounding is valuable for finding an unknown mark, but its box can include
    nearby artwork. Two independent, temporally separated tight detections of
    the same spatial mark are safer for the final blur. The human gate remains
    required; this only improves the proposed region shown for review.
    """
    for item in items:
        region = item.get("suggested_region_source_pixels")
        if (
            item.get("category") not in {"visual_logo", "text"}
            or item.get("candidate_type") != "persistent_overlay"
            or not isinstance(region, dict)
        ):
            continue
        area = int(region["width"]) * int(region["height"])
        supporting: list[dict] = []
        for other in items:
            other_region = other.get("suggested_region_source_pixels")
            if (
                other is item
                or other.get("category") != "visual_logo"
                or other.get("review_kind") == "title_overlay"
                or not isinstance(other_region, dict)
                or float(other["start_seconds"]) >= float(item["end_seconds"])
                or float(other["end_seconds"]) <= float(item["start_seconds"])
            ):
                continue
            other_area = int(other_region["width"]) * int(other_region["height"])
            sources = set(other.get("model_evidence", {}).get("region_sources") or [])
            if (
                "ocr" in sources
                and other_area <= area * 0.65
                and _intersection_over_smaller(region, other_region) >= 0.70
            ):
                supporting.append(other_region)
        supporting = _dominant_region_cluster(supporting)
        if len(supporting) < 2:
            continue
        refined = supporting[0]
        for candidate in supporting[1:]:
            refined = union_pixel_regions(refined, candidate)
        assert refined is not None
        refined_area = int(refined["width"]) * int(refined["height"])
        if (
            item.get("category") == "text"
            and refined_area / max(1, area) < 0.75
        ):
            method = "repeated_visual_ocr_consensus"
        elif 0.45 <= refined_area / max(1, area) < 0.75:
            method = "repeated_tight_ocr_support"
        else:
            # OCR can cover only the word inside a graphical emblem. Preserve
            # the full grounded mark in that case and remove just its padding.
            trim = max(
                2,
                round(min(int(region["width"]), int(region["height"])) * 0.05),
            )
            if int(region["width"]) <= trim * 2 or int(region["height"]) <= trim * 2:
                continue
            refined = {
                "x": int(region["x"]) + trim,
                "y": int(region["y"]) + trim,
                "width": int(region["width"]) - trim * 2,
                "height": int(region["height"]) - trim * 2,
            }
            method = "grounding_box_padding_trim"
        item["suggested_region_source_pixels"] = refined
        item["region_refinement"] = {
            "method": method,
            "support_count": len(supporting),
            "original_region": region,
            "refined_region": refined,
        }
    return items


def reconcile_persistent_overlay_items(
    items: list[dict], *, source_duration: float,
) -> list[dict]:
    """Join OCR and visual evidence for the same persistent watermark.

    A fixed brand can be readable by OCR in hundreds of scenes while the
    visual router confirms it in only a few sampled windows. Presenting those
    as separate review cards is confusing and can leave the early part of the
    film uncovered. One human decision should cover the corroborated timeline
    while retaining both report references for audit.
    """
    removed: set[int] = set()

    def retain_support(owner: dict, support: dict) -> None:
        """Keep compact, inspectable evidence for a card absorbed by a track."""
        record = {
            "id": support.get("id"),
            "category": support.get("category"),
            "candidate_type": support.get("candidate_type"),
            "start_seconds": support.get("start_seconds"),
            "end_seconds": support.get("end_seconds"),
            "region_source_pixels": support.get("suggested_region_source_pixels"),
            "region_classification": support.get("region_classification"),
            "source_candidate_refs": list(support.get("source_candidate_refs") or []),
        }
        records = owner.setdefault("supporting_detections", [])
        marker = (
            record["id"], record["start_seconds"], record["end_seconds"],
            tuple(record["source_candidate_refs"]),
        )
        if not any(
            (
                value.get("id"), value.get("start_seconds"), value.get("end_seconds"),
                tuple(value.get("source_candidate_refs") or []),
            ) == marker
            for value in records
        ):
            records.append(record)
        owner["supporting_candidate_count"] = len(records)
    persistent_owners = sorted(
        items,
        key=lambda item: 0 if item.get("category") == "visual_logo" else 1,
    )
    for visual in persistent_owners:
        visual_region = visual.get("suggested_region_source_pixels")
        if (
            id(visual) in removed
            or visual.get("category") not in {"visual_logo", "text"}
            or visual.get("candidate_type") != "persistent_overlay"
            or not isinstance(visual_region, dict)
        ):
            continue
        for text_item in items if visual.get("category") == "visual_logo" else []:
            text_region = text_item.get("suggested_region_source_pixels")
            if (
                text_item is visual
                or id(text_item) in removed
                or text_item.get("category") != "text"
                or text_item.get("candidate_type") != "persistent_overlay"
                or not isinstance(text_region, dict)
                or _intersection_over_smaller(visual_region, text_region) < 0.75
                or float(text_item["start_seconds"]) >= float(visual["end_seconds"])
                or float(text_item["end_seconds"]) <= float(visual["start_seconds"])
            ):
                continue
            visual["start_seconds"] = round(max(
                0.0,
                min(float(visual["start_seconds"]), float(text_item["start_seconds"])),
            ), 3)
            visual["end_seconds"] = round(min(
                source_duration,
                max(float(visual["end_seconds"]), float(text_item["end_seconds"])),
            ), 3)
            for key in (
                "labels", "reasons", "evidence", "preview_images",
                "source_candidate_refs",
            ):
                visual[key] = list(dict.fromkeys(
                    list(visual.get(key) or []) + list(text_item.get(key) or [])
                ))
            visual["detected_intervals"] = [{
                "start_seconds": visual["start_seconds"],
                "end_seconds": visual["end_seconds"],
            }]
            visual["suggested_decision"] = "BLUR"
            visual["temporal_policy"] = "continuous_persistent_overlay"
            visual["linked_detector_categories"] = ["text", "visual_logo"]
            visual["reasons"] = list(dict.fromkeys(
                visual["reasons"] + [
                    "OCR và visual-logo cùng xác nhận một watermark cố định; "
                    "một quyết định áp dụng cho toàn bộ khoảng xuất hiện"
                ]
            ))
            visual["id"] = _item_id(
                "visual_logo", visual["start_seconds"], visual["end_seconds"],
                "|".join(visual["evidence"]),
            )
            retain_support(visual, text_item)
            removed.add(id(text_item))
            break
        # Region-level confirmations and short OCR fragments fully contained
        # by the same watermark are supporting evidence, not extra decisions.
        # Absorb them so one click covers the complete approved timeline.
        for support in items:
            support_region = support.get("suggested_region_source_pixels")
            support_overlap = (
                _intersection_over_smaller(visual_region, support_region)
                if isinstance(support_region, dict) else 0.0
            )
            required_overlap = (
                0.60
                if support.get("region_classification") in {
                    "external_brand", "external_brand_candidate",
                }
                else 0.80
            )
            if (
                support is visual
                or id(support) in removed
                or support.get("category") not in {"visual_logo", "text"}
                or support.get("candidate_type") in {
                    "opening_promotion", "branded_end_card", "promotional_segment",
                    "scene_text", "subtitle", "title_overlay",
                }
                or support.get("review_kind") == "title_overlay"
                or not isinstance(support_region, dict)
                or support_overlap < required_overlap
                or float(support["start_seconds"]) < float(visual["start_seconds"])
                or float(support["end_seconds"]) > float(visual["end_seconds"])
                or (
                    support.get("decision") is not None
                    and support.get("decision") != visual.get("decision")
                )
            ):
                continue
            for key in (
                "labels", "reasons", "evidence", "preview_images",
                "source_candidate_refs",
            ):
                visual[key] = list(dict.fromkeys(
                    list(visual.get(key) or []) + list(support.get(key) or [])
                ))
            retain_support(visual, support)
            removed.add(id(support))
    return [item for item in items if id(item) not in removed]


def group_safety_review_events(
    items: list[dict], *, maximum_gap_seconds: float = 6.0,
    maximum_span_seconds: float = 30.0,
) -> list[dict]:
    """Group nearby safety detections into one review decision.

    The original intervals remain discrete.  ``build_edit_plan`` expands an
    approved action back to those intervals, so a gap used only for review
    context is never cut or blurred.
    """
    if maximum_gap_seconds < 0 or maximum_span_seconds <= 0:
        raise ValueError("Invalid safety event grouping limits")
    safety_categories = {"adult", "gore", "violence"}
    ordered = sorted(items, key=lambda item: (item["category"], item["start_seconds"]))
    grouped: list[dict] = []
    for original in ordered:
        item = dict(original)
        previous = grouped[-1] if grouped else None
        suggestions_compatible = (
            previous is not None
            and (
                previous.get("suggested_decision") is None
                or item.get("suggested_decision") is None
                or previous.get("suggested_decision") == item.get("suggested_decision")
            )
        )
        can_group = (
            previous is not None
            and previous.get("category") == item.get("category")
            and item.get("category") in safety_categories
            and previous.get("decision") is None
            and item.get("decision") is None
            and float(item["start_seconds"]) <= (
                float(previous["end_seconds"]) + maximum_gap_seconds
            )
            and float(item["end_seconds"]) - float(previous["start_seconds"])
            <= maximum_span_seconds
            and suggestions_compatible
        )
        if not can_group:
            grouped.append(item)
            continue
        previous["end_seconds"] = max(
            float(previous["end_seconds"]), float(item["end_seconds"])
        )
        scores = [
            value for value in (previous.get("max_score"), item.get("max_score"))
            if value is not None
        ]
        previous["max_score"] = max(scores) if scores else None
        previous["priority"] = min(
            (previous["priority"], item["priority"]),
            key=lambda value: _PRIORITY_RANK.get(value, 2),
        )
        for key in (
            "labels", "reasons", "evidence", "preview_images",
            "source_candidate_refs",
        ):
            previous[key] = list(dict.fromkeys(
                list(previous.get(key) or []) + list(item.get(key) or [])
            ))
        intervals = list(previous.get("detected_intervals") or [])
        for detected in item.get("detected_intervals") or [{
            "start_seconds": item["start_seconds"],
            "end_seconds": item["end_seconds"],
        }]:
            marker = (
                float(detected["start_seconds"]), float(detected["end_seconds"]),
            )
            if not any(
                (
                    float(existing["start_seconds"]),
                    float(existing["end_seconds"]),
                ) == marker
                for existing in intervals
            ):
                intervals.append(dict(detected))
        previous["detected_intervals"] = sorted(
            intervals, key=lambda value: value["start_seconds"]
        )
        previous["candidate_type"] = "review_event_group"
        previous["temporal_policy"] = "discrete_detected_intervals"
        previous["event_detection_count"] = len(previous["detected_intervals"])
        previous["id"] = _item_id(
            previous["category"], previous["start_seconds"],
            previous["end_seconds"], "|".join(previous["evidence"]),
        )
    return grouped


def revalidate_preserved_review_items(
    items: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Drop stale protection from candidates carried over from an older queue.

    ``candidate_type`` and ``suggested_decision`` on a preserved item were
    written by the detector that ran before the upgrade. Trusting them keeps a
    region protected forever: a type such as ``persistent_overlay`` exempts the
    item from every later corroboration gate, so a region the current detectors
    no longer propose can still demand a blur over the whole film. A preserved
    region that the current run does not corroborate stays visible as an
    advisory candidate instead, which keeps the no-silent-discard guarantee
    without letting an obsolete guess block the queue.
    """
    required: list[dict] = []
    advisory: list[dict] = []
    for original in items:
        sources = set((original.get("model_evidence") or {}).get("region_sources") or [])
        stale = (
            original.get("migration_status") == "preserved_unresolved_from_previous_queue"
            and original.get("category") == "visual_logo"
            and original.get("decision") is None
            and original.get("region_classification") in {None, "unknown"}
            and isinstance(original.get("suggested_region_source_pixels"), dict)
        )
        if not stale:
            required.append(original)
            continue
        item = dict(original)
        item["priority"] = "context"
        item["candidate_type"] = "stale_preserved_logo_region"
        item["review_kind"] = "logo_candidate"
        item["suggested_decision"] = "KEEP"
        item["advisory"] = True
        item["region_revalidation"] = {
            "outcome": "demoted_uncorroborated_after_detector_upgrade",
            "previous_candidate_type": original.get("candidate_type"),
            "previous_suggested_decision": original.get("suggested_decision"),
            "previous_priority": original.get("priority"),
            "region_sources": sorted(sources),
        }
        item["reasons"] = list(dict.fromkeys(
            list(item.get("reasons") or []) + [
                "Ứng viên giữ lại từ queue cũ nhưng detector hiện tại không còn "
                "khoanh vùng này với bằng chứng độc lập; chuyển sang mục tham "
                "khảo thay vì bắt buộc xử lý"
            ]
        ))
        advisory.append(item)
    return required, advisory


def preserve_unresolved_review_items(
    previous_items: list[dict], current_items: list[dict],
) -> list[dict]:
    """Never silently discard a candidate that still awaits a human decision.

    Detector upgrades may merge or suppress an older candidate. When rebuilding
    the same queue, unresolved items remain visible until the reviewer explicitly
    chooses KEEP, BLUR, CUT, or otherwise clears them in a later workflow.
    """
    output = [dict(item) for item in current_items]

    def equivalent(previous: dict, current: dict) -> bool:
        if previous.get("category") != current.get("category"):
            return False
        previous_start = float(previous.get("start_seconds") or 0.0)
        previous_end = float(previous.get("end_seconds") or 0.0)
        current_start = float(current.get("start_seconds") or 0.0)
        current_end = float(current.get("end_seconds") or 0.0)
        overlap = max(0.0, min(previous_end, current_end) - max(previous_start, current_start))
        smaller_duration = min(
            max(0.0, previous_end - previous_start),
            max(0.0, current_end - current_start),
        )
        if smaller_duration <= 0 or overlap / smaller_duration < 0.80:
            return False
        previous_region = previous.get("suggested_region_source_pixels")
        current_region = current.get("suggested_region_source_pixels")
        if isinstance(previous_region, dict) and isinstance(current_region, dict):
            return _intersection_over_smaller(previous_region, current_region) >= 0.75
        return previous.get("candidate_type") == current.get("candidate_type")

    current_ids = {str(item.get("id")) for item in output}
    for previous in previous_items:
        decision = previous.get("decision")
        if decision not in {None, "NEEDS_MORE_CONTEXT"}:
            continue
        item_id = str(previous.get("id") or "")
        if (
            not item_id
            or item_id in current_ids
            or any(equivalent(previous, current) for current in output)
        ):
            continue
        restored = dict(previous)
        restored["migration_status"] = "preserved_unresolved_from_previous_queue"
        restored["reasons"] = list(dict.fromkeys(
            list(restored.get("reasons") or [])
            + ["Ứng viên chưa được người dùng quyết định nên được giữ lại sau khi nâng cấp detector"]
        ))
        output.append(restored)
        current_ids.add(item_id)
    return output


def _scan_items(root: Path, report_path: Path, payload: dict) -> list[dict]:
    raw_category = str(payload.get("scan_type") or "unknown")
    category = "adult" if raw_category == "nsfw" else raw_category
    evidence = _relative(root, report_path)
    items = []
    for interval_index, interval in enumerate(payload.get("intervals", [])):
        start = float(interval["start_seconds"])
        end = float(interval["end_seconds"])
        thumbnail = interval.get("strongest_frame")
        preview_images = []
        if thumbnail:
            preview_images.append(
                _relative(root, report_path.parent / str(thumbnail))
            )
        label = _review_label(category, interval.get("predicted_label"))
        reason = interval.get("reason")
        proposals = interval.get("region_localization", {}).get("proposals", [])
        preview_values = interval.get("supporting_frames", [])
        for value in preview_values:
            relative = _relative(root, report_path.parent / str(value))
            if relative not in preview_images:
                preview_images.append(relative)
        confirmation = interval.get("visual_logo_confirmation", {})
        features = confirmation.get("features", {})
        boundary_context = confirmation.get("boundary_scene_context", {})
        opening_promotion = (
            category == "visual_logo"
            and interval.get("candidate_type") in {None, "opening_promotion"}
            and start < 30.0
            and end <= 30.0
            and bool(confirmation.get("boundary_window"))
            and str(boundary_context.get("state") or "UNCERTAIN") != "MOVIE_CONTENT"
            and float(features.get("full_frame_score") or 0.0) >= 0.65
        )
        interval_candidate_type = (
            "opening_promotion" if opening_promotion else interval.get("candidate_type")
        )
        usable_proposals = [
            proposal for proposal in proposals
            if isinstance(proposal, dict)
            and isinstance(proposal.get("blur_region_px"), list)
            and len(proposal["blur_region_px"]) == 4
        ]
        # Full-frame promotions are reviewed as one scene CUT. Region-level
        # OCR/grounding boxes inside opening/closing cards would otherwise create
        # redundant BLUR decisions and leave branded content visible.
        full_scene_cut = opening_promotion or interval_candidate_type == "branded_end_card"
        proposal_items = [None] if full_scene_cut else (usable_proposals or [None])
        for proposal_index, proposal in enumerate(proposal_items):
            region = None
            proposal_labels: list[str] = []
            region_classification = None
            proposal_sources: list[str] = []
            if proposal is not None:
                x, y, width, height = [int(value) for value in proposal["blur_region_px"]]
                if width > 0 and height > 0:
                    region = {
                        "x": max(0, x), "y": max(0, y),
                        "width": width, "height": height,
                    }
                proposal_labels = [
                    str(value) for value in proposal.get("labels", []) if str(value).strip()
                ]
                proposal_sources = [str(value) for value in proposal.get("sources", [])]
                region_classification = proposal.get("region_classification")
            # A frame-level suggestion cannot authorize a localized box.  For
            # visual proposals, only the proposal's own region proof may
            # suggest BLUR.  Interval suggestions still apply when no regional
            # proposal exists (for example non-visual safety detectors).
            if proposal is not None and category == "visual_logo":
                regional_suggestion = (proposal or {}).get("suggested_decision")
                if (
                    not regional_suggestion
                    and (
                        region_classification == "external_brand"
                        or interval_candidate_type == "persistent_overlay"
                    )
                ):
                    regional_suggestion = interval.get("suggested_decision", "")
            else:
                regional_suggestion = (
                    (proposal or {}).get("suggested_decision")
                    or interval.get("suggested_decision", "")
                )
            suggested_decision = str(regional_suggestion or "").upper() or None
            if full_scene_cut:
                suggested_decision = "CUT"
            if suggested_decision not in DECISIONS:
                suggested_decision = None
            if suggested_decision == "BLUR" and region is None:
                suggested_decision = None
            if region_classification in {"movie_title", "subtitle", "scene_text"}:
                suggested_decision = "KEEP"
            region_token = (
                "full"
                if region is None else
                f"{region['x']},{region['y']},{region['width']},{region['height']}"
            )
            review_kind = (
                str(interval_candidate_type)
                if full_scene_cut else
                "title_overlay"
                if region_classification == "movie_title" else
                "logo_overlay" if category == "visual_logo" else category
            )
            items.append({
                "id": _item_id(
                    category, start, end,
                    f"{evidence}|proposal:{proposal_index}|{region_token}",
                ),
                "category": category,
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "max_score": interval.get("max_score"),
                "priority": _priority(interval),
                "labels": list(dict.fromkeys(
                    ([str(label)] if label else []) + proposal_labels
                )),
                "reasons": [str(reason)] if reason else [],
                "evidence": [evidence],
                "preview_images": preview_images,
                "suggested_region_source_pixels": region,
                "source_frame_size": interval.get("region_localization", {}).get("frame_size"),
                "candidate_type": interval_candidate_type,
                "review_kind": review_kind,
                "region_classification": region_classification,
                "model_evidence": {
                    "vlm_confirmation": interval.get("visual_logo_confirmation", {}).get("state"),
                    "vlm_source": interval.get("visual_logo_confirmation", {}).get("confirmation_source"),
                    "region_sources": proposal_sources,
                },
                "suggested_decision": suggested_decision,
                "source_candidate_refs": [f"{evidence}#interval:{interval_index}"],
                "detected_intervals": [{
                    "start_seconds": round(start, 3),
                    "end_seconds": round(end, 3),
                }],
                "decision": None,
                "decision_note": None,
                "decision_region_source_pixels": None,
                "decision_actor": None,
                "decision_transport": None,
                "decided_at": None,
            })
    return items


def _advisory_scan_items(root: Path, report_path: Path, payload: dict) -> list[dict]:
    """Expose rejected visual candidates without making them block export."""
    if str(payload.get("scan_type")) != "visual_logo":
        return []
    evidence = _relative(root, report_path)
    items = []
    for index, interval in enumerate(payload.get("rejected_windows", [])):
        audit_frame = interval.get("audit_frame")
        if not audit_frame:
            continue
        start = float(interval["start_seconds"])
        end = float(interval["end_seconds"])
        preview = _relative(root, report_path.parent / str(audit_frame))
        confirmation = interval.get("visual_logo_confirmation", {})
        item = {
            "id": _item_id("visual_logo", start, end, f"{evidence}|advisory:{index}"),
            "category": "visual_logo",
            "start_seconds": round(start, 3),
            "end_seconds": round(end, 3),
            "max_score": interval.get("max_score"),
            "priority": "context",
            "labels": [str(interval.get("predicted_label") or "Ứng viên logo đã bị model loại")],
            "reasons": [str(interval.get("reason") or "Ứng viên audit tùy chọn")],
            "evidence": [evidence],
            "preview_images": [preview],
            "suggested_region_source_pixels": None,
            "source_frame_size": payload.get("source_size"),
            "candidate_type": "rejected_logo_candidate",
            "review_kind": "logo_candidate",
            "region_classification": "unknown",
            "model_evidence": {
                "vlm_confirmation": confirmation.get("state"),
                "vlm_source": confirmation.get("confirmation_source"),
            },
            "suggested_decision": "KEEP",
            "decision": None,
            "decision_note": None,
            "decision_region_source_pixels": None,
            "decision_actor": None,
            "decision_transport": None,
            "decided_at": None,
            "advisory": True,
        }
        items.append(item)
    return items


def _low_ad_optional_track(track: dict) -> bool:
    """Recognize both new and legacy low-ad non-overlay OCR routing."""
    routing = str(track.get("routing") or "")
    return (
        routing == "LOW_AD_UNCERTAIN"
        or (
            routing.startswith("REVIEW_UNCERTAIN")
            and "ad_probability" in track
            and float(track.get("ad_probability") or 0.0) < 0.20
            and not list(track.get("policy_hits") or [])
            and track.get("candidate_type") != "persistent_overlay"
        )
    )


def _advisory_text_items(root: Path, report_path: Path, payload: dict) -> list[dict]:
    """Expose low-ad OCR ambiguity without making it block export.

    The semantic report remains the source of truth and retains every OCR
    track.  This view lets a reviewer recover an unusual advertisement that
    scored poorly without forcing ordinary scene fragments through the main
    review queue.
    """
    advisory_tracks = []
    for track in payload.get("tracks", []):
        if not _low_ad_optional_track(track):
            continue
        clone = dict(track)
        clone["review_candidate"] = True
        clone["routing"] = "ADVISORY_LOW_AD"
        advisory_tracks.append(clone)
    if not advisory_tracks:
        return []
    advisory_payload = dict(payload)
    advisory_payload["tracks"] = advisory_tracks
    items = _text_items(root, report_path, advisory_payload)
    for item in items:
        item["priority"] = "context"
        item["candidate_type"] = "low_ad_text_candidate"
        item["review_kind"] = "text_candidate"
        item["suggested_decision"] = "KEEP"
        item["advisory"] = True
        item["reasons"] = list(dict.fromkeys(
            list(item.get("reasons") or [])
            + ["Ứng viên OCR điểm quảng cáo thấp; không chặn xuất video"]
        ))
    return items


def _quarantine_uncorroborated_visual_regions(
    items: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Move frame-confirmation leakage to optional audit candidates.

    Qwen confirms that a *frame* contains branding.  It does not prove that
    every Florence/Grounding/OCR box in that frame is the brand.  A proposal
    supported by only one regional source, with no regional classification or
    action, therefore stays visible as an advisory candidate until it gains
    region-specific evidence.  Confirmed brands, persistent overlays, scene
    cuts, and multi-source proposals remain in the required queue.
    """
    required: list[dict] = []
    advisory: list[dict] = []
    protected_types = {
        "persistent_overlay", "opening_promotion", "branded_end_card",
        "promotional_segment",
    }
    localization_only_sources = {"grounding", "grounding_dino", "ocr"}
    for original in items:
        sources = set((original.get("model_evidence") or {}).get("region_sources") or [])
        vlm_source = (original.get("model_evidence") or {}).get("vlm_source")
        in_film_content = (
            original.get("category") == "visual_logo"
            and original.get("candidate_type") in {
                "scene_text", "subtitle",
            }
            and original.get("suggested_decision") == "KEEP"
        )
        uncorroborated = (
            original.get("category") == "visual_logo"
            and original.get("candidate_type") not in protected_types
            and original.get("decision") is None
            and original.get("suggested_decision") is None
            and original.get("region_classification") in {None, "unknown"}
            and isinstance(original.get("suggested_region_source_pixels"), dict)
            and vlm_source == "qwen_local"
            and bool(sources)
            and sources.issubset(localization_only_sources)
        )
        if not uncorroborated and not in_film_content:
            required.append(original)
            continue
        item = dict(original)
        item["priority"] = "context"
        item["candidate_type"] = (
            "in_film_content_reference" if in_film_content
            else "uncorroborated_logo_region"
        )
        item["review_kind"] = (
            "in_film_content_reference" if in_film_content
            else "logo_candidate"
        )
        # Unknown regional evidence is not a negative classification.  Leave
        # it without an action suggestion so the optional UI cannot imply
        # that a possible missed advertisement was proven safe.
        item["suggested_decision"] = "KEEP" if in_film_content else None
        item["advisory"] = True
        advisory_reason = (
            "Vùng đã được đối chiếu là chữ/nội dung trong phim; giữ ở mục tham "
            "khảo và không bắt người dùng duyệt"
            if in_film_content else
            "AI xác nhận có logo ở khung hình nhưng vùng này chưa có bằng chứng "
            "độc lập rằng chính vùng khoanh là logo"
        )
        item["reasons"] = list(dict.fromkeys(
            list(item.get("reasons") or []) + [advisory_reason]
        ))
        advisory.append(item)
    return required, advisory


def _text_items(root: Path, report_path: Path, payload: dict) -> list[dict]:
    evidence = _relative(root, report_path)
    source_size = payload.get("source_size") or []
    analysis_size = payload.get("analysis_size") or []
    scale_x = (
        float(source_size[0]) / float(analysis_size[0])
        if len(source_size) == 2 and len(analysis_size) == 2 and analysis_size[0]
        else 1.0
    )
    scale_y = (
        float(source_size[1]) / float(analysis_size[1])
        if len(source_size) == 2 and len(analysis_size) == 2 and analysis_size[1]
        else 1.0
    )
    frame_size = (
        [int(source_size[0]), int(source_size[1])]
        if len(source_size) == 2
        and int(source_size[0]) > 0 and int(source_size[1]) > 0
        else None
    )
    items = []
    for track_index, track in enumerate(payload.get("tracks", [])):
        if track.get("review_candidate") is False or _low_ad_optional_track(track):
            continue
        is_logo_overlay = (
            track.get("candidate_type") == "persistent_overlay"
            and track.get("routing") == "REVIEW_PERSISTENT_OVERLAY"
        )
        item_category = "text"
        start = float(track["start_seconds"])
        end = float(track.get("recommended_blur_end_seconds", track["end_seconds"]))
        preview_images = []
        if track.get("preview"):
            preview_images.append(
                _relative(root, report_path.parent / str(track["preview"]))
            )
        region = None
        box = track.get("union_box")
        if isinstance(box, list) and len(box) == 4:
            x1, y1, x2, y2 = [float(value) for value in box]
            left = max(0, round(x1 * scale_x))
            top = max(0, round(y1 * scale_y))
            right = max(left + 1, round(x2 * scale_x))
            bottom = max(top + 1, round(y2 * scale_y))
            if frame_size is not None:
                left = min(frame_size[0] - 1, left)
                top = min(frame_size[1] - 1, top)
                right = min(frame_size[0], max(left + 1, right))
                bottom = min(frame_size[1], max(top + 1, bottom))
            region = {
                "x": left, "y": top,
                "width": right - left, "height": bottom - top,
            }
        labels = [str(value) for value in track.get("sample_text", [])[:5]]
        items.append(
            {
                "id": _item_id(item_category, start, end, evidence),
                "category": item_category,
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "max_score": track.get("ad_probability", track.get("max_confidence")),
                "priority": str(track.get("review_priority", "normal")),
                "labels": labels,
                "reasons": [str(track.get("reason"))] if track.get("reason") else [],
                "evidence": [evidence],
                "preview_images": preview_images,
                "suggested_region_source_pixels": region,
                "source_frame_size": frame_size,
                "suggested_blur_edge_mode": (
                    "vertical_only"
                    if region is not None and len(source_size) == 2
                    and region["x"] == 0
                    and region["width"] >= round(source_size[0])
                    else None
                ),
                "candidate_type": track.get("candidate_type"),
                "review_kind": "logo_overlay" if is_logo_overlay else "text",
                "suggested_decision": (
                    str(track.get("suggested_decision", "")).upper()
                    if str(track.get("suggested_decision", "")).upper() in DECISIONS
                    else None
                ),
                "source_candidate_refs": [
                    f"{evidence}#track:{track.get('track_id', track_index)}"
                ],
                "detected_intervals": [{
                    "start_seconds": round(start, 3),
                    "end_seconds": round(end, 3),
                }],
                "decision": None,
                "decision_note": None,
                "decision_region_source_pixels": None,
                "decision_actor": None,
                "decision_transport": None,
                "decided_at": None,
            }
        )
    return items


def _merge_items(items: Iterable[dict], maximum_gap_seconds: float) -> list[dict]:
    if maximum_gap_seconds < 0:
        raise ValueError("maximum_gap_seconds cannot be negative")
    ordered = sorted(items, key=lambda item: (item["category"], item["start_seconds"]))
    merged: list[dict] = []
    for item in ordered:
        previous = merged[-1] if merged else None
        same_candidate_type = (
            previous is None
            or previous["category"] != item["category"]
            or item["category"] not in {"visual_logo", "text"}
            or previous.get("candidate_type") == item.get("candidate_type")
        )
        suggestions_compatible = (
            previous is None
            or previous.get("suggested_decision") is None
            or item.get("suggested_decision") is None
            or previous.get("suggested_decision") == item.get("suggested_decision")
        )
        visual_regions_compatible = True
        if previous is not None and previous["category"] == item["category"] == "visual_logo":
            overlap = pixel_region_iou(
                previous.get("suggested_region_source_pixels"),
                item.get("suggested_region_source_pixels"),
            )
            # Low overlap merely means two detectors pointed somewhere in the
            # same neighbourhood.  Merging at 5% allowed a proven OCR site
            # mark to donate its BLUR action to a different DINO box.  Require
            # the boxes to describe substantially the same pixels.
            visual_regions_compatible = overlap is None or overlap >= 0.50
        if (
            previous
            and previous["category"] == item["category"]
            and item["start_seconds"] <= previous["end_seconds"] + maximum_gap_seconds
            and same_candidate_type
            and suggestions_compatible
            and visual_regions_compatible
        ):
            current = previous
            current["end_seconds"] = max(current["end_seconds"], item["end_seconds"])
            scores = [value for value in (current.get("max_score"), item.get("max_score")) if value is not None]
            current["max_score"] = max(scores) if scores else None
            current["priority"] = min(
                (current["priority"], item["priority"]),
                key=lambda value: _PRIORITY_RANK.get(value, 2),
            )
            for key in (
                "labels", "reasons", "evidence", "preview_images",
                "source_candidate_refs",
            ):
                current[key] = list(dict.fromkeys(
                    list(current.get(key) or []) + list(item.get(key) or [])
                ))
            intervals = list(current.get("detected_intervals") or [])
            for detected in item.get("detected_intervals") or []:
                marker = (
                    float(detected["start_seconds"]),
                    float(detected["end_seconds"]),
                )
                if not any(
                    (
                        float(existing["start_seconds"]),
                        float(existing["end_seconds"]),
                    ) == marker
                    for existing in intervals
                ):
                    intervals.append(dict(detected))
            current["detected_intervals"] = intervals
            current_suggestion = current.get("suggested_decision")
            item_suggestion = item.get("suggested_decision")
            if current_suggestion is None:
                current["suggested_decision"] = item_suggestion
            elif item_suggestion is not None and item_suggestion != current_suggestion:
                current["suggested_decision"] = None
            if current.get("candidate_type") is None:
                current["candidate_type"] = item.get("candidate_type")
            if current["category"] == "text":
                current["suggested_region_source_pixels"] = union_pixel_regions(
                    current.get("suggested_region_source_pixels"),
                    item.get("suggested_region_source_pixels"),
                )
                if item.get("suggested_blur_edge_mode") == "vertical_only":
                    current["suggested_blur_edge_mode"] = "vertical_only"
            elif current.get("suggested_region_source_pixels") is None:
                current["suggested_region_source_pixels"] = item.get(
                    "suggested_region_source_pixels"
                )
            current["id"] = _item_id(
                current["category"], current["start_seconds"],
                current["end_seconds"], "|".join(current["evidence"]),
            )
        else:
            merged.append(dict(item))
    for item in merged:
        if item["category"] == "text" and isinstance(
            item.get("suggested_region_source_pixels"), dict
        ):
            item["suggested_region_source_pixels"] = tighten_text_region(
                item["suggested_region_source_pixels"]
            )
    return sorted(
        merged,
        key=lambda item: (
            _PRIORITY_RANK.get(item["priority"], 2), item["start_seconds"], item["category"]
        ),
    )


def _counts(items: list[dict]) -> dict:
    decisions = {decision: 0 for decision in DECISIONS}
    pending = 0
    for item in items:
        if item.get("decision") in decisions:
            decisions[item["decision"]] += 1
        else:
            pending += 1
    return {"total": len(items), "pending": pending, "decisions": decisions}


def _queue_status(items: list[dict]) -> str:
    if any(item.get("decision") == "NEEDS_MORE_CONTEXT" for item in items):
        return "NEEDS_MORE_CONTEXT"
    if any(item.get("decision") is None for item in items):
        return "REVIEW_REQUIRED"
    return "READY_FOR_EDIT_PLAN"


def _title_overlay_references(payload: dict) -> list[dict]:
    source_size = payload.get("source_size") or []
    analysis_size = payload.get("analysis_size") or []
    if (
        len(source_size) != 2 or len(analysis_size) != 2
        or not analysis_size[0] or not analysis_size[1]
    ):
        return []
    scale_x = float(source_size[0]) / float(analysis_size[0])
    scale_y = float(source_size[1]) / float(analysis_size[1])
    references = []
    for track in payload.get("tracks", []):
        if track.get("routing") != "LIKELY_TITLE_OVERLAY":
            continue
        box = track.get("union_box")
        if not isinstance(box, list) or len(box) != 4:
            continue
        x1, y1, x2, y2 = [float(value) for value in box]
        references.append({
            "start_seconds": float(track["start_seconds"]),
            "end_seconds": float(track.get("recommended_blur_end_seconds", track["end_seconds"])),
            "region": {
                "x": max(0, round(x1 * scale_x)),
                "y": max(0, round(y1 * scale_y)),
                "width": max(1, round((x2 - x1) * scale_x)),
                "height": max(1, round((y2 - y1) * scale_y)),
            },
            "labels": [str(value) for value in track.get("sample_text", [])[:3]],
        })
    return references


def _in_film_text_references(payload: dict) -> list[dict]:
    """Map strong non-ad OCR evidence into source-pixel guard regions.

    A frame-level logo classifier can correctly observe a watermark elsewhere
    in the frame while a region model points at a book, sign, subtitle, or
    interface label inside the story.  These references let the queue classify
    that selected region rather than inheriting the frame-level brand label.
    """
    source_size = payload.get("source_size") or []
    analysis_size = payload.get("analysis_size") or []
    if (
        len(source_size) != 2 or len(analysis_size) != 2
        or not analysis_size[0] or not analysis_size[1]
    ):
        return []
    source_width, source_height = int(source_size[0]), int(source_size[1])
    scale_x = source_width / float(analysis_size[0])
    scale_y = source_height / float(analysis_size[1])
    duration = float(payload.get("duration_seconds") or 0.0)
    boundary_margin = min(30.0, duration * 0.02) if duration > 0 else 0.0
    references = []
    for track in payload.get("tracks", []):
        routing = str(track.get("routing") or "")
        start = float(track.get("start_seconds") or 0.0)
        end = float(track.get("recommended_blur_end_seconds", track.get("end_seconds") or 0.0))
        visual = track.get("visual_features") or {}
        likely_scene = routing in {"LIKELY_SCENE_TEXT", "LIKELY_SUBTITLE"}
        low_ad_short_text = (
            routing == "REVIEW_UNCERTAIN"
            and not bool(track.get("persistent"))
            and not bool(visual.get("overlay_signal"))
            and float(track.get("ad_probability") or 0.0) <= 0.18
            and end - start <= 15.0
            and str(track.get("zone") or "") == "subtitle"
        )
        if not (likely_scene or low_ad_short_text):
            continue
        # Opening and closing boundary text remains eligible for full-scene
        # promotion/credit review and must not be suppressed by this guard.
        if duration > 0 and (
            start < boundary_margin or end > duration - boundary_margin
        ):
            continue
        box = track.get("union_box")
        if not isinstance(box, list) or len(box) != 4:
            continue
        x1, y1, x2, y2 = [float(value) for value in box]
        left = max(0, min(source_width - 1, round(x1 * scale_x)))
        top = max(0, min(source_height - 1, round(y1 * scale_y)))
        right = max(left + 1, min(source_width, round(x2 * scale_x)))
        bottom = max(top + 1, min(source_height, round(y2 * scale_y)))
        references.append({
            "start_seconds": start,
            "end_seconds": end,
            "region": {
                "x": left, "y": top,
                "width": right - left, "height": bottom - top,
            },
            "labels": [str(value) for value in track.get("sample_text", [])[:3]],
            "routing": routing,
            "ad_probability": float(track.get("ad_probability") or 0.0),
            "classification": (
                "subtitle" if routing == "LIKELY_SUBTITLE" else "scene_text"
            ),
        })
    return references


def _guard_title_overlays(items: list[dict], references: list[dict]) -> list[dict]:
    for item in items:
        if item.get("category") != "visual_logo":
            continue
        region = item.get("suggested_region_source_pixels")
        evidence = item.get("model_evidence", {})
        region_sources = set(evidence.get("region_sources") or [])
        if (
            not isinstance(region, dict)
            or evidence.get("vlm_source") == "approved_brand_memory"
            or "brand_memory" in region_sources
        ):
            continue
        for reference in references:
            overlaps_time = (
                float(item["start_seconds"]) < float(reference["end_seconds"])
                and float(item["end_seconds"]) > float(reference["start_seconds"])
            )
            if not overlaps_time or pixel_region_iou(region, reference["region"]) < 0.50:
                continue
            title = " / ".join(reference["labels"]) or "chữ tiêu đề lặp lại"
            item["review_kind"] = "title_overlay"
            item["candidate_type"] = "title_overlay"
            item["suggested_decision"] = "KEEP"
            item["priority"] = "context"
            item["labels"] = list(dict.fromkeys(
                item.get("labels", []) + [f"Có khả năng là tiêu đề phim: {title}"]
            ))
            item["reasons"] = list(dict.fromkeys(
                item.get("reasons", [])
                + ["Vùng logo đề xuất chồng với OCR tiêu đề có điểm quảng cáo thấp"]
            ))
            break
    return items


def _guard_in_film_text(items: list[dict], references: list[dict]) -> list[dict]:
    """Keep a localized in-story text region separate from frame branding."""
    for item in items:
        if item.get("category") != "visual_logo":
            continue
        region = item.get("suggested_region_source_pixels")
        evidence = item.get("model_evidence", {})
        region_sources = set(evidence.get("region_sources") or [])
        if (
            not isinstance(region, dict)
            or item.get("candidate_type") in {
                "persistent_overlay", "opening_promotion", "branded_end_card",
            }
            or evidence.get("vlm_source") == "approved_brand_memory"
            or "brand_memory" in region_sources
        ):
            continue
        for reference in references:
            overlaps_time = (
                float(item["start_seconds"]) < float(reference["end_seconds"])
                and float(item["end_seconds"]) > float(reference["start_seconds"])
            )
            if (
                not overlaps_time
                or _intersection_over_smaller(region, reference["region"]) < 0.65
            ):
                continue
            classification = str(reference.get("classification") or "scene_text")
            sample = " / ".join(reference.get("labels") or []) or "chữ trong cảnh"
            item["review_kind"] = "in_film_text"
            item["candidate_type"] = classification
            item["region_classification"] = classification
            item["suggested_decision"] = "KEEP"
            item["priority"] = "context"
            item["labels"] = list(dict.fromkeys(
                item.get("labels", []) + [f"OCR nội dung phim: {sample}"]
            ))
            item["reasons"] = list(dict.fromkeys(
                item.get("reasons", []) + [
                    "Vùng được chọn chồng khớp chữ ngắn trong cảnh có điểm quảng cáo thấp"
                ]
            ))
            item.setdefault("model_evidence", {})["in_film_text_guard"] = {
                "routing": reference.get("routing"),
                "ad_probability": reference.get("ad_probability"),
                "overlap_over_smaller": round(
                    _intersection_over_smaller(region, reference["region"]), 6
                ),
            }
            break
    return items


def review_export_paths(root: Path, queue: dict) -> tuple[Path, Path, Path]:
    """Derive isolated plan, output and job-state paths from reviewed decisions."""
    source = Path(str(queue["source"]["path"]))
    source_hash = str(queue["source"].get("sha256") or "nohash")[:8]
    decisions = [
        {
            "id": item.get("id"), "decision": item.get("decision"),
            "start": item.get("start_seconds"), "end": item.get("end_seconds"),
            "region": item.get("decision_region_source_pixels"),
        }
        for item in queue.get("items", [])
    ]
    export_policy = queue.get("export_size_policy")
    identity: object = decisions
    if isinstance(export_policy, dict) and export_policy.get("mode") not in {None, "default"}:
        identity = {"decisions": decisions, "export_size_policy": export_policy}
    decision_hash = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:8]
    slug = re.sub(r"[^\w.-]+", "-", source.stem, flags=re.UNICODE).strip("-._")
    slug = (slug or "video")[:80]
    key = f"{slug}-{source_hash}-{decision_hash}"
    return (
        root / "work" / f"{key}-edit-plan.json",
        root / "output" / f"{key}-reviewed.mp4",
        root / "work" / f"{key}-export-job.json",
    )


def _render_queue_html(root: Path, queue_path: Path, payload: dict) -> None:
    rows = []
    for item in payload["items"]:
        images = []
        for value in item["preview_images"][:3]:
            image_path = root / value
            relative = os.path.relpath(image_path, queue_path.parent).replace("\\", "/")
            images.append(f'<img src="{html.escape(relative)}" loading="lazy">')
        score = "" if item.get("max_score") is None else f'{float(item["max_score"]):.3f}'
        decision = item.get("decision") or "CHƯA DUYỆT"
        rows.append(
            "<tr>"
            f'<td><code>{html.escape(item["id"])}</code></td>'
            f'<td>{html.escape(item["category"])}</td>'
            f'<td>{item["start_seconds"]:.3f}–{item["end_seconds"]:.3f}s</td>'
            f'<td>{html.escape(item["priority"])}</td><td>{score}</td>'
            f'<td>{html.escape(", ".join(item["labels"]))}</td>'
            f'<td>{"".join(images)}</td><td><strong>{html.escape(decision)}</strong></td>'
            "</tr>"
        )
    document = f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><title>BiliFlow review queue</title>
<style>body{{font:15px system-ui;margin:24px;background:#111;color:#eee}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #444;padding:8px;vertical-align:top}}th{{background:#242424;position:sticky;top:0}}img{{max-width:240px;max-height:150px;margin:2px}}code{{color:#8bd5ff}}.warning{{padding:12px;background:#3b2f12;border:1px solid #9d7925}}</style></head><body>
<h1>Hàng đợi xác nhận BiliFlow</h1>
<p class="warning">Chưa có chỉnh sửa nào được thực hiện. Mỗi mục phải được chọn KEEP, BLUR, CUT hoặc NEEDS_MORE_CONTEXT trước khi tạo edit plan.</p>
<p>Trạng thái: <strong>{html.escape(payload["status"])}</strong> · Tổng: {payload["counts"]["total"]} · Chưa duyệt: {payload["counts"]["pending"]}</p>
<table><thead><tr><th>ID</th><th>Nhóm</th><th>Thời gian</th><th>Ưu tiên</th><th>Điểm</th><th>Nhãn</th><th>Ảnh</th><th>Quyết định</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
</body></html>"""
    queue_path.with_suffix(".html").write_text(document, encoding="utf-8")


def build_review_queue(
    *, project_root: Path, report_paths: list[Path], queue_path: Path,
    merge_gap_seconds: float = 1.0,
    selected_detectors: list[str] | None = None,
) -> dict:
    root = project_root.resolve(strict=True)
    reports_root = (root / "reports").resolve(strict=True)
    queue_path = _inside(reports_root, queue_path, "Queue path")
    previous_queue = None
    if queue_path.is_file():
        try:
            candidate_previous = _read_json(queue_path)
            if isinstance(candidate_previous, dict):
                previous_queue = candidate_previous
        except (OSError, ValueError, json.JSONDecodeError):
            previous_queue = None
    if not report_paths:
        raise ValueError("At least one report is required")
    source_path: Path | None = None
    source_sha256: str | None = None
    source_duration: float | None = None
    items: list[dict] = []
    advisory_items: list[dict] = []
    title_references: list[dict] = []
    in_film_text_references: list[dict] = []
    used_reports = []
    detector_coverage_by_report: dict[str, dict] = {}
    for candidate in report_paths:
        report_path = _inside(reports_root, candidate, "Report path")
        report_path = report_path.resolve(strict=True)
        payload = _read_json(report_path)
        if payload.get("status") not in {"COMPLETED", "REVIEW_REQUIRED"}:
            raise ValueError(f"Report is not reviewable: {report_path}")
        current_source = Path(str(payload["input"])).resolve(strict=True)
        if source_path is None:
            source_path = current_source
        elif current_source != source_path:
            raise ValueError("All reports must refer to the same source video")
        current_sha = payload.get("input_sha256")
        if current_sha:
            if source_sha256 and current_sha != source_sha256:
                raise ValueError("Report source checksums do not match")
            source_sha256 = str(current_sha)
        current_duration = float(payload["duration_seconds"])
        if source_duration is not None and abs(current_duration - source_duration) > 0.1:
            raise ValueError("Report source durations do not match")
        source_duration = current_duration
        relative_report = _relative(root, report_path)
        used_reports.append(relative_report)
        selection_coverage = payload.get("candidate_selection_coverage")
        if isinstance(selection_coverage, dict):
            detector_coverage_by_report[relative_report] = {
                "complete": bool(selection_coverage.get("complete")),
                "details": selection_coverage,
            }
        elif payload.get("scan_type") == "visual_logo":
            # Older reports had no explicit coverage manifest. A positive
            # omitted count proves that some routed windows never reached the
            # semantic model, so the queue must not claim full coverage.
            omitted = int(payload.get("candidate_windows_omitted") or 0)
            detector_coverage_by_report[relative_report] = {
                "complete": omitted == 0,
                "details": {
                    "legacy_report": True,
                    "candidate_windows_omitted": omitted,
                },
            }
        if "tracks" in payload:
            title_references.extend(_title_overlay_references(payload))
            in_film_text_references.extend(_in_film_text_references(payload))
            items.extend(_text_items(root, report_path, payload))
            advisory_items.extend(_advisory_text_items(root, report_path, payload))
        else:
            items.extend(_scan_items(root, report_path, payload))
            advisory_items.extend(_advisory_scan_items(root, report_path, payload))
    assert source_path is not None and source_duration is not None
    expected_candidate_refs = sorted({
        str(reference)
        for item in items
        for reference in item.get("source_candidate_refs") or []
    })
    for item in items:
        item["start_seconds"] = round(max(0.0, float(item["start_seconds"])), 3)
        item["end_seconds"] = round(min(
            source_duration, float(item["end_seconds"]),
        ), 3)
        clamped_intervals = []
        for detected in item.get("detected_intervals") or []:
            detected_start = max(0.0, float(detected["start_seconds"]))
            detected_end = min(source_duration, float(detected["end_seconds"]))
            if detected_start < detected_end:
                clamped_intervals.append({
                    "start_seconds": round(detected_start, 3),
                    "end_seconds": round(detected_end, 3),
                })
        item["detected_intervals"] = clamped_intervals
    items = _merge_items(items, merge_gap_seconds)
    items = group_safety_review_events(items)
    items = _guard_title_overlays(items, title_references)
    items = _guard_in_film_text(items, in_film_text_references)
    items = refine_persistent_logo_regions(items)
    items = reconcile_persistent_overlay_items(
        items, source_duration=source_duration,
    )
    items, quarantined_visual_items = _quarantine_uncorroborated_visual_regions(items)
    advisory_items.extend(quarantined_visual_items)
    if previous_queue is not None:
        previous_source = previous_queue.get("source", {})
        same_source = (
            str(previous_source.get("path") or "") == str(source_path)
            and (
                not source_sha256
                or not previous_source.get("sha256")
                or str(previous_source.get("sha256")) == source_sha256
            )
        )
        if same_source:
            items = preserve_unresolved_review_items(
                list(previous_queue.get("items") or []), items,
            )
            # Preserved items bypass every gate above, so re-check them here.
            # Without this the queue only ever ratchets: one bad region stays
            # required through every later detector upgrade.
            items, stale_visual_items = revalidate_preserved_review_items(items)
            advisory_items.extend(stale_visual_items)
            # Old unresolved cards are restored after the first reconciliation.
            # Re-run both safe compaction passes so a detector upgrade cannot
            # resurrect dozens of cards already represented by one track/event.
            items = reconcile_persistent_overlay_items(
                items, source_duration=source_duration,
            )
            items = group_safety_review_events(items)
    items = promote_strong_adult_priorities(items)
    items = sorted(items, key=lambda item: (
        _PRIORITY_RANK.get(item["priority"], 2),
        item["start_seconds"], item["category"],
    ))
    ensure_unique_review_item_ids([*items, *advisory_items])
    represented_candidate_refs = sorted({
        str(reference)
        for item in [*items, *advisory_items]
        for reference in item.get("source_candidate_refs") or []
    })
    represented_set = set(represented_candidate_refs)
    coverage_reports = []
    for report in used_reports:
        prefix = f"{report}#"
        expected_for_report = [
            value for value in expected_candidate_refs if value.startswith(prefix)
        ]
        represented_for_report = [
            value for value in represented_candidate_refs if value.startswith(prefix)
        ]
        detector_coverage = detector_coverage_by_report.get(report, {
            "complete": True, "details": None,
        })
        reference_complete = set(expected_for_report).issubset(represented_set)
        coverage_reports.append({
            "report": report,
            "source_candidates": len(expected_for_report),
            "represented_candidates": len(represented_for_report),
            "reference_complete": reference_complete,
            "detector_complete": bool(detector_coverage["complete"]),
            "detector_details": detector_coverage["details"],
            "complete": reference_complete and bool(detector_coverage["complete"]),
        })
    references_complete = set(expected_candidate_refs).issubset(represented_set)
    detectors_complete = all(
        bool(value["complete"]) for value in detector_coverage_by_report.values()
    )
    payload = {
        "schema_version": 1,
        "status": _queue_status(items),
        "created_at": _now(),
        "updated_at": _now(),
        "source": {
            "path": str(source_path), "sha256": source_sha256,
            "duration_seconds": round(source_duration, 3),
        },
        "reports": used_reports,
        "detection_scope": {
            "selected": list(selected_detectors or []),
            "skipped": [
                value for value in DEFAULT_DETECTOR_GROUPS
                if selected_detectors is not None and value not in selected_detectors
            ],
            "explicit": selected_detectors is not None,
            "all_selected": (
                selected_detectors is not None
                and set(selected_detectors) == set(DEFAULT_DETECTOR_GROUPS)
            ),
        },
        "merge_gap_seconds": merge_gap_seconds,
        "allowed_decisions": list(DECISIONS),
        "counts": _counts(items),
        "items": items,
        "advisory_items": advisory_items,
        "candidate_coverage": {
            "complete": references_complete and detectors_complete,
            "reference_complete": references_complete,
            "detectors_complete": detectors_complete,
            "source_candidate_count": len(expected_candidate_refs),
            "represented_source_candidate_count": len(represented_candidate_refs),
            "queue_item_count": len(items),
            "collapsed_by_deduplication": max(
                0, len(expected_candidate_refs) - len(items),
            ),
            "missing_refs": [
                value for value in expected_candidate_refs
                if value not in represented_set
            ],
            "reports": coverage_reports,
        },
        "audit_log": [],
        "safety": {
            "automatic_edit": False,
            "edit_plan_requires_all_items_resolved": True,
        },
    }
    _write_json(queue_path, payload)
    _render_queue_html(root, queue_path, payload)
    return payload


def refresh_review_priorities(*, project_root: Path, queue_path: Path) -> dict:
    """Re-rank an existing queue without rescanning or changing decisions."""
    root = project_root.resolve(strict=True)
    reports_root = (root / "reports").resolve(strict=True)
    queue_path = _inside(reports_root, queue_path, "Queue path").resolve(strict=True)
    payload = _read_json(queue_path)
    payload["items"] = sorted(
        promote_strong_adult_priorities(list(payload.get("items") or [])),
        key=lambda item: (
            _PRIORITY_RANK.get(item.get("priority", "normal"), 2),
            float(item.get("start_seconds") or 0.0),
            str(item.get("category") or ""),
        ),
    )
    payload["counts"] = _counts(payload["items"])
    payload["status"] = _queue_status(payload["items"])
    payload["updated_at"] = _now()
    _write_json(queue_path, payload)
    _render_queue_html(root, queue_path, payload)
    return payload


def record_review_decision(
    *, project_root: Path, queue_path: Path, item_id: str, decision: str,
    note: str | None = None, region: tuple[int, int, int, int] | None = None,
    full_frame: bool = False, start_seconds: float | None = None,
    end_seconds: float | None = None, blur_edge_mode: str | None = None,
    actor: str = "local_user",
    transport: str = "local_cli",
) -> dict:
    root = project_root.resolve(strict=True)
    queue_path = _inside((root / "reports").resolve(strict=True), queue_path, "Queue path")
    payload = _read_json(queue_path.resolve(strict=True))
    decision = decision.upper()
    if decision not in DECISIONS:
        raise ValueError(f"Decision must be one of {', '.join(DECISIONS)}")
    matches = [item for item in payload["items"] if item["id"] == item_id]
    if not matches:
        advisory_matches = [
            item for item in payload.get("advisory_items", []) if item["id"] == item_id
        ]
        if len(advisory_matches) == 1:
            item = advisory_matches[0]
            payload["advisory_items"].remove(item)
            item.pop("advisory", None)
            payload["items"].append(item)
            matches = [item]
    if len(matches) != 1:
        raise ValueError(f"Unknown or duplicate review item: {item_id}")
    item = matches[0]
    if (start_seconds is None) != (end_seconds is None):
        raise ValueError("Adjusted review interval requires both start and end")
    original_interval = None
    if start_seconds is not None and end_seconds is not None:
        source_duration = float(payload["source"]["duration_seconds"])
        adjusted_start = float(start_seconds)
        adjusted_end = float(end_seconds)
        if not 0 <= adjusted_start < adjusted_end <= source_duration + 0.001:
            raise ValueError("Adjusted review interval is outside the source duration")
        original_interval = {
            "start_seconds": float(item["start_seconds"]),
            "end_seconds": float(item["end_seconds"]),
        }
        item.setdefault("detected_interval", original_interval)
        item["start_seconds"] = round(adjusted_start, 3)
        item["end_seconds"] = round(min(adjusted_end, source_duration), 3)
    selected_region = None
    if decision == "BLUR":
        if blur_edge_mode not in {None, "all_edges", "vertical_only"}:
            raise ValueError("Blur edge mode must be all_edges or vertical_only")
        if full_frame:
            selected_region = "FULL_FRAME"
        elif region:
            x, y, width, height = region
            if min(x, y) < 0 or width <= 0 or height <= 0:
                raise ValueError("Blur region must have non-negative x/y and positive width/height")
            selected_region = {"x": x, "y": y, "width": width, "height": height}
        elif item.get("suggested_region_source_pixels"):
            selected_region = item["suggested_region_source_pixels"]
        else:
            raise ValueError("BLUR requires a region or explicit full-frame approval")
    item["decision"] = decision
    item["decision_note"] = note
    item["decision_region_source_pixels"] = selected_region
    item["decision_blur_edge_mode"] = (
        (blur_edge_mode or item.get("suggested_blur_edge_mode"))
        if decision == "BLUR" else None
    )
    item["decision_actor"] = actor
    item["decision_transport"] = transport
    item["decided_at"] = _now()
    payload["updated_at"] = _now()
    payload["status"] = _queue_status(payload["items"])
    payload["counts"] = _counts(payload["items"])
    payload.setdefault("audit_log", []).append(
        {
            "at": item["decided_at"], "action": "DECIDE", "item_id": item_id,
            "decision": decision, "actor": actor, "transport": transport,
            "original_interval": original_interval,
            "reviewed_interval": {
                "start_seconds": item["start_seconds"],
                "end_seconds": item["end_seconds"],
            },
        }
    )
    payload["audit_log"] = payload["audit_log"][-1000:]
    _write_json(queue_path, payload)
    remember_review_item(root, payload, item)
    _render_queue_html(root, queue_path, payload)
    return payload



def apply_visual_ai_assessments(
    *, project_root: Path, queue_path: Path, audit_payload: dict,
) -> dict:
    """Attach advisory visual findings without making review decisions."""
    root = project_root.resolve(strict=True)
    queue_path = _inside(
        (root / "reports").resolve(strict=True), queue_path, "Queue path"
    ).resolve(strict=True)
    payload = _read_json(queue_path)
    items = {str(item.get("id")): item for item in payload.get("items", [])}
    applied = 0
    promoted_suggestions = 0
    for raw in list(audit_payload.get("visual_assessments") or []):
        if not isinstance(raw, dict):
            continue
        item = items.get(str(raw.get("item_id")))
        if item is None:
            continue
        confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0.0)))
        assessment = {
            "classification": str(raw.get("classification") or "uncertain"),
            "suggested_decision": str(
                raw.get("suggested_decision") or "NEEDS_MORE_CONTEXT"
            ),
            "confidence": round(confidence, 4),
            "region_assessment": str(
                raw.get("region_assessment") or "NOT_APPLICABLE"
            ),
            "reasoning": str(raw.get("reasoning") or ""),
            "authority": "ADVISORY_ONLY",
        }
        item["ai_visual_audit"] = assessment
        item["ai_visual_suggested_decision"] = assessment["suggested_decision"]
        suggestion = assessment["suggested_decision"]
        if item.get("suggestion_source") == "visual_ai":
            item.pop("suggested_decision", None)
            item.pop("suggestion_source", None)
        elif item.get("suggestion_source") == "visual_ai_conflict":
            previous = item.pop("suggestion_conflict", {})
            local_suggestion = previous.get("local_suggestion")
            if local_suggestion in {"KEEP", "BLUR", "CUT"}:
                item["suggested_decision"] = local_suggestion
            item.pop("suggestion_source", None)
        local_suggestion = item.get("suggested_decision")
        high_confidence_conflict = (
            not item.get("decision")
            and local_suggestion in {"KEEP", "BLUR", "CUT"}
            and suggestion in {"KEEP", "BLUR", "CUT"}
            and suggestion != local_suggestion
            and confidence >= 0.90
        )
        if high_confidence_conflict:
            item["suggestion_conflict"] = {
                "local_suggestion": local_suggestion,
                "visual_ai_suggestion": suggestion,
                "visual_ai_confidence": round(confidence, 4),
            }
            item.pop("suggested_decision", None)
            item["suggestion_source"] = "visual_ai_conflict"
        else:
            item.pop("suggestion_conflict", None)
            can_promote = (
                not item.get("decision")
                and not item.get("suggested_decision")
                and suggestion in {"KEEP", "BLUR", "CUT"}
                and (
                    suggestion != "BLUR"
                    or isinstance(item.get("suggested_region_source_pixels"), dict)
                )
            )
            if can_promote:
                item["suggested_decision"] = suggestion
                item["suggestion_source"] = "visual_ai"
                promoted_suggestions += 1
        applied += 1

    created_at = str(audit_payload.get("created_at") or _now())
    payload["visual_ai_audit"] = {
        "created_at": created_at,
        "model": audit_payload.get("model"),
        "reasoning_effort": audit_payload.get("reasoning_effort"),
        "image_count": int(
            (audit_payload.get("visual_audit") or {}).get("image_count") or 0
        ),
        "assessment_count": applied,
        "promoted_suggestion_count": promoted_suggestions,
        "authority": "ADVISORY_ONLY",
        "human_edit_approval_required": True,
    }
    payload["updated_at"] = _now()
    payload.setdefault("audit_log", []).append({
        "at": payload["updated_at"],
        "action": "VISUAL_AI_ANNOTATE",
        "assessment_count": applied,
        "promoted_suggestion_count": promoted_suggestions,
        "actor": "ai_visual_audit",
        "transport": "codex_app_server",
    })
    payload["audit_log"] = payload["audit_log"][-1000:]
    _write_json(queue_path, payload)
    _render_queue_html(root, queue_path, payload)
    return payload

def clear_review_decision(
    *, project_root: Path, queue_path: Path, item_id: str,
    actor: str = "local_user", transport: str = "local_cli",
) -> dict:
    root = project_root.resolve(strict=True)
    queue_path = _inside((root / "reports").resolve(strict=True), queue_path, "Queue path")
    payload = _read_json(queue_path.resolve(strict=True))
    matches = [item for item in payload["items"] if item["id"] == item_id]
    if len(matches) != 1:
        raise ValueError(f"Unknown or duplicate review item: {item_id}")
    item = matches[0]
    item["decision"] = None
    item["decision_note"] = None
    item["decision_region_source_pixels"] = None
    item["decision_actor"] = None
    item["decision_transport"] = None
    item["decided_at"] = None
    payload["updated_at"] = _now()
    payload["status"] = _queue_status(payload["items"])
    payload["counts"] = _counts(payload["items"])
    payload.setdefault("audit_log", []).append(
        {
            "at": payload["updated_at"], "action": "CLEAR", "item_id": item_id,
            "actor": actor, "transport": transport,
        }
    )
    payload["audit_log"] = payload["audit_log"][-1000:]
    _write_json(queue_path, payload)
    forget_review_item(root, payload, item_id)
    _render_queue_html(root, queue_path, payload)
    return payload


def bulk_keep_review_items(
    *, project_root: Path, queue_path: Path, review_filter: str,
    actor: str = "local_user", transport: str = "local_ui",
) -> dict:
    root = project_root.resolve(strict=True)
    queue_path = _inside((root / "reports").resolve(strict=True), queue_path, "Queue path")
    payload = _read_json(queue_path.resolve(strict=True))
    allowed_filters = {
        "pending", "high", "all", "gore", "violence", "adult", "text", "visual_logo"
    }
    if review_filter not in allowed_filters:
        raise ValueError("Bộ lọc hàng loạt không hợp lệ")

    def selected(item: dict) -> bool:
        if item.get("decision") is not None:
            return False
        if review_filter in {"pending", "all"}:
            return True
        if review_filter == "high":
            return item.get("priority") == "high"
        return item.get("category") == review_filter

    changed = [item for item in payload["items"] if selected(item)]
    decided_at = _now()
    for item in changed:
        item["decision"] = "KEEP"
        item["decision_note"] = "Bulk keep from filtered review view"
        item["decision_region_source_pixels"] = None
        item["decision_actor"] = actor
        item["decision_transport"] = transport
        item["decided_at"] = decided_at
    payload["updated_at"] = decided_at
    payload["status"] = _queue_status(payload["items"])
    payload["counts"] = _counts(payload["items"])
    payload.setdefault("audit_log", []).append(
        {
            "at": decided_at, "action": "BULK_KEEP", "filter": review_filter,
            "changed_count": len(changed), "actor": actor, "transport": transport,
        }
    )
    payload["audit_log"] = payload["audit_log"][-1000:]
    _write_json(queue_path, payload)
    for item in changed:
        remember_review_item(root, payload, item)
    _render_queue_html(root, queue_path, payload)
    return payload


def bulk_accept_suggested_decisions(
    *, project_root: Path, queue_path: Path, review_filter: str,
    actor: str = "local_user", transport: str = "local_ui",
) -> dict:
    """Accept explicit detector suggestions with one human review action."""
    root = project_root.resolve(strict=True)
    queue_path = _inside((root / "reports").resolve(strict=True), queue_path, "Queue path")
    payload = _read_json(queue_path.resolve(strict=True))
    allowed_filters = {
        "pending", "high", "all", "gore", "violence", "adult", "text", "visual_logo"
    }
    if review_filter not in allowed_filters:
        raise ValueError("Bộ lọc hàng loạt không hợp lệ")

    def selected(item: dict) -> bool:
        if item.get("decision") is not None or item.get("suggested_decision") not in DECISIONS:
            return False
        if review_filter in {"pending", "all"}:
            return True
        if review_filter == "high":
            return item.get("priority") == "high"
        return item.get("category") == review_filter

    changed = [item for item in payload["items"] if selected(item)]
    decided_at = _now()
    accepted = []
    for item in changed:
        decision = str(item["suggested_decision"])
        region = item.get("suggested_region_source_pixels") if decision == "BLUR" else None
        if decision == "BLUR" and region is None:
            continue
        item["decision"] = decision
        item["decision_note"] = "Human accepted the detector suggestion from the filtered review view"
        item["decision_region_source_pixels"] = region
        item["decision_blur_edge_mode"] = (
            item.get("suggested_blur_edge_mode") if decision == "BLUR" else None
        )
        item["decision_actor"] = actor
        item["decision_transport"] = transport
        item["decided_at"] = decided_at
        accepted.append({"item_id": item["id"], "decision": decision})
    payload["updated_at"] = decided_at
    payload["status"] = _queue_status(payload["items"])
    payload["counts"] = _counts(payload["items"])
    payload.setdefault("audit_log", []).append(
        {
            "at": decided_at, "action": "BULK_ACCEPT_SUGGESTIONS",
            "filter": review_filter, "changed_count": len(accepted),
            "accepted": accepted, "actor": actor, "transport": transport,
        }
    )
    payload["audit_log"] = payload["audit_log"][-1000:]
    _write_json(queue_path, payload)
    accepted_ids = {value["item_id"] for value in accepted}
    for item in changed:
        if item.get("id") in accepted_ids:
            remember_review_item(root, payload, item)
    _render_queue_html(root, queue_path, payload)
    return payload


def _path_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def review_resource_status(*, project_root: Path, queue_path: Path) -> dict:
    root = project_root.resolve(strict=True)
    queue_path = _inside((root / "reports").resolve(strict=True), queue_path, "Queue path")
    payload = _read_json(queue_path.resolve(strict=True))
    source = Path(payload["source"]["path"]).resolve(strict=True)
    report_directories = {
        (root / value).resolve(strict=True).parent for value in payload.get("reports", [])
    }
    report_bytes = sum(_path_size(path) for path in report_directories)
    preview_seconds = 0.0
    operation_count = 0
    for item in payload["items"]:
        if item.get("decision") == "CUT":
            preview_seconds += 6.0
            operation_count += 1
        elif item.get("decision") == "BLUR":
            preview_seconds += item["end_seconds"] - item["start_seconds"] + 6.0
            operation_count += 1
    disk = shutil.disk_usage(root)
    return {
        "source_bytes": source.stat().st_size,
        "report_bytes": report_bytes,
        "queue_bytes": queue_path.stat().st_size,
        "disk_free_bytes": disk.free,
        "selected_operation_count": operation_count,
        "estimated_preview_seconds": round(preview_seconds, 1),
        "estimated_preview_megabytes_range": [
            round(preview_seconds * 0.25, 1), round(preview_seconds * 1.5, 1)
        ],
        "review_mode": {
            "runs_ai": False, "runs_ffmpeg": False, "encodes_video": False,
            "expected_heat": "low",
        },
        "preview_render": {
            "encoder": "CPU libx264", "uses_gpu": False,
            "expected_heat": "moderate_during_render_only",
            "full_export_enabled": False,
        },
    }


def _interactive_html(token: str) -> str:
    return f"""<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>BiliFlow Review</title>
<style>
:root{{--bg:#0e1116;--card:#171c24;--line:#303846;--text:#eef3fa;--muted:#9aa8ba;--blue:#4da3ff;--green:#3ccf91;--red:#ff657a;--amber:#f4ba4a}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px system-ui,sans-serif}}body.saving button{{pointer-events:none;opacity:.65}}header{{position:sticky;top:0;z-index:4;background:#0e1116f2;border-bottom:1px solid var(--line);padding:16px 24px}}header .back{{position:absolute;right:24px;top:16px}}h1{{margin:0 190px 8px 0;font-size:22px}}#summary{{color:var(--muted)}}.bar{{height:8px;background:#252b35;border-radius:8px;margin-top:10px;overflow:hidden}}.bar span{{display:block;height:100%;background:var(--green)}}#resources{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;padding:12px 24px 0}}.resource{{background:#141922;border:1px solid var(--line);padding:10px;border-radius:9px}}.resource strong{{display:block;font-size:17px;margin-top:3px}}.resource-note{{grid-column:1/-1;color:var(--muted)}}#export-panel{{margin:12px 24px 0;padding:12px;background:#141922;border:1px solid var(--line);border-radius:9px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}}#export-panel button{{background:#6b2632;font-weight:700}}#export-status{{color:var(--muted)}}nav{{display:flex;gap:8px;flex-wrap:wrap;padding:14px 24px}}button{{border:1px solid var(--line);background:#242b36;color:var(--text);padding:9px 12px;border-radius:8px;cursor:pointer}}button:disabled{{opacity:.45;cursor:not-allowed}}button:hover{{border-color:var(--blue)}}button.active{{outline:2px solid var(--blue)}}button.bulk{{background:#15523d}}button.accept{{margin-left:auto;background:#17466d}}button.candidate-filter{{background:#664a14;border-color:#a87821;font-weight:700}}main{{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:14px;padding:0 24px 30px}}article{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}}article.high{{border-color:#855f1e}}.meta{{display:flex;gap:8px;flex-wrap:wrap;color:var(--muted);margin-bottom:10px}}.badge{{padding:3px 7px;border-radius:12px;background:#252c37}}.suggestion{{color:#91c9ff;border:1px solid #345b80}}.scope-detail{{margin:8px 0;padding:9px 10px;border-radius:8px;background:#17283d;border:1px solid #315c85;color:#d7eaff}}.scope-detail strong{{display:block;margin-bottom:4px}}.scope-detail.track{{background:#153b31;border-color:#28765d;color:#baf4dc}}.scope-detail.advisory{{background:#3b2c12;border-color:#8b651d;color:#ffe1a0}}.coverage{{margin:8px 0;padding:8px 10px;border-radius:8px;background:#153b31;color:#89e4bd}}.visual-ai{{margin:8px 0;padding:9px 10px;border-radius:8px;background:#172f4b;border:1px solid #386d9d;color:#cce7ff}}.images{{display:flex;gap:8px;overflow:auto;min-height:150px}}img{{height:160px;max-width:100%;object-fit:contain;border-radius:7px;background:#090b0e}}.region-pair{{display:flex;gap:6px;align-items:center;flex:0 0 auto}}canvas.region-frame{{height:160px;width:auto;max-width:420px;border-radius:7px;background:#090b0e}}canvas.region-crop{{width:auto;height:auto;max-width:240px;max-height:140px;border:2px solid var(--red);border-radius:7px;background:#090b0e}}.region-detail{{margin:8px 0;padding:8px 10px;border-radius:8px;background:#301b20;border:1px solid #8c3947;color:#ffd6dc}}.labels{{min-height:42px;margin:10px 0;color:#d7e0ec}}.actions{{display:flex;gap:7px;flex-wrap:wrap}}.decision-block{{width:100%;padding:10px;border:1px solid var(--line);border-radius:9px;background:#121720}}.decision-block strong,.decision-block small{{display:block;margin-bottom:8px}}.decision-block small{{color:var(--muted)}}.decision-buttons{{display:flex;gap:7px;flex-wrap:wrap}}.keep{{background:#164a38}}.blur{{background:#17466d}}.blur-full{{background:#514086}}.cut{{background:#6b2632}}.context{{background:#664a14}}.clear{{margin-left:auto}}.selected{{outline:2px solid white}}.empty{{padding:40px;color:var(--muted)}}
#export-panel label{{display:flex;gap:7px;align-items:center;color:var(--muted)}}#export-panel select,#export-panel input{{background:#0e1116;color:var(--text);border:1px solid var(--line);border-radius:7px;padding:8px}}#custom-size-wrap[hidden]{{display:none}}#custom-output-gb{{width:90px}}#export-status{{flex-basis:100%}}
</style></head><body><header><h1>Xác nhận nội dung BiliFlow</h1><button class="back" onclick="location.href='/'">← Quay lại Dashboard</button><div id="summary">Đang tải…</div><div class="bar"><span id="progress" style="width:0"></span></div></header>
<section id="resources"></section><section id="export-panel"><label>Dung lượng video<select id="output-size-mode" onchange="toggleCustomOutputSize()"><option value="default">Tối đa 3,5 GB (mặc định)</option><option value="custom">Giới hạn tùy chỉnh</option><option value="unlimited">Không giới hạn dung lượng</option></select></label><label id="custom-size-wrap" hidden>Tối đa<input id="custom-output-gb" type="number" min="0.05" max="1000" step="0.1" value="3.5">GB</label><button id="finalize" onclick="finalizeExport()" disabled>Hoàn tất duyệt và xuất video</button><span id="export-status">Hãy giải quyết toàn bộ mục trước khi xuất.</span></section><nav><button data-filter="pending" class="active">Chưa duyệt</button><button id="candidate-filter" class="candidate-filter" data-filter="candidates">Ứng viên phụ</button><button data-filter="high">Ưu tiên cao</button><button data-filter="all">Tất cả mục chính</button><button data-filter="visual_ai">Visual AI</button><button data-filter="visual_logo">Logo / quảng cáo</button><button data-filter="adult">18+</button><button data-filter="gore">Máu me</button><button data-filter="violence">Bạo lực</button><button data-filter="text">Chữ</button><button class="accept" onclick="bulkAccept()">Duyệt tất cả đề xuất đang lọc</button><button class="bulk" onclick="bulkKeep()">Giữ nguyên tất cả đang lọc</button></nav><main id="items"></main>
<script>
let token={json.dumps(token)};let queue=null;let resources=null;let exportJob={{status:'IDLE'}};let filter='pending';let saving=false;let exportSettingsInitialized=false;let queueRefreshRunning=false;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const clock=s=>{{const m=Math.floor(s/60),v=(s-m*60).toFixed(1).padStart(4,'0');return `${{String(m).padStart(2,'0')}}:${{v}}`;}};
async function readJson(response){{let data=null;try{{data=await response.json();}}catch(_error){{}}if(!response.ok)throw new Error(data?.error||`Máy chủ trả về lỗi ${{response.status}}`);return data;}}
async function requestJson(path,options={{}}){{let response;try{{response=await fetch(path,options);}}catch(_error){{throw new Error('Mất kết nối với Review. Hãy tải lại trang; các lựa chọn đã lưu trước đó vẫn được giữ nguyên.');}}return readJson(response);}}
async function load(){{try{{[queue,resources,exportJob]=await Promise.all([requestJson('/api/queue',{{cache:'no-store'}}),requestJson('/api/resources',{{cache:'no-store'}}),requestJson('/api/export',{{cache:'no-store'}})]);initializeExportSettings();render();}}catch(error){{document.querySelector('#summary').textContent=error.message;}}}}
function queueIdentity(value){{if(!value)return '';const reports=(value.reports||[]).map(x=>typeof x==='string'?x:(x.path||x.report||JSON.stringify(x))).join('|');return [value.created_at||'',value.source?.input_sha256||'',reports].join('::');}}
function queueVersion(value){{if(!value)return '';const counts=value.counts||{{}};return [queueIdentity(value),value.updated_at||'',value.status||'',counts.total||0,counts.pending||0].join('::');}}
async function refreshQueue(){{if(saving||queueRefreshRunning)return;queueRefreshRunning=true;try{{const latest=await requestJson('/api/queue',{{cache:'no-store'}});if(queueVersion(latest)!==queueVersion(queue)){{const revisionChanged=queueIdentity(latest)!==queueIdentity(queue);queue=latest;if(revisionChanged){{exportSettingsInitialized=false;exportJob=await requestJson('/api/export',{{cache:'no-store'}});}}resources=await requestJson('/api/resources',{{cache:'no-store'}});initializeExportSettings();render();}}}}catch(_error){{}}finally{{queueRefreshRunning=false;}}}}
function isLogoItem(x){{return x.category==='visual_logo'||x.review_kind==='logo_overlay'||x.review_kind==='logo_candidate';}}
function visible(x){{if(filter==='pending')return !x.decision;if(filter==='high')return x.priority==='high';if(filter==='all')return true;if(filter==='visual_ai')return !!x.ai_visual_audit;if(filter==='visual_logo')return isLogoItem(x);if(filter==='text')return x.category==='text'&&x.review_kind!=='logo_overlay';return x.category===filter;}}
const size=n=>n>1073741824?`${{(n/1073741824).toFixed(1)}} GB`:`${{(n/1048576).toFixed(1)}} MB`;
function initializeExportSettings(){{if(exportSettingsInitialized)return;const policy=queue.export_size_policy||{{mode:'default',maximum_output_gb:3.5}},mode=['default','custom','unlimited'].includes(policy.mode)?policy.mode:'default';document.querySelector('#output-size-mode').value=mode;if(mode==='custom'&&Number(policy.maximum_output_gb)>0)document.querySelector('#custom-output-gb').value=Number(policy.maximum_output_gb);exportSettingsInitialized=true;toggleCustomOutputSize();}}
function toggleCustomOutputSize(){{document.querySelector('#custom-size-wrap').hidden=document.querySelector('#output-size-mode').value!=='custom';}}
function outputSizeSelection(){{const mode=document.querySelector('#output-size-mode').value;if(mode==='unlimited')return{{size_mode:'unlimited',description:'không giới hạn dung lượng'}};if(mode==='default')return{{size_mode:'default',description:'tối đa 3,5 GB'}};const maximum=Number(document.querySelector('#custom-output-gb').value);if(!Number.isFinite(maximum)||maximum<0.05||maximum>1000)throw new Error('Giới hạn tùy chỉnh phải từ 0,05 đến 1.000 GB.');return{{size_mode:'custom',max_output_gb:maximum,description:`tối đa ${{maximum.toLocaleString('vi-VN')}} GB`}};}}
function pendingDescription(){{const pending=queue.items.filter(x=>!x.decision),names={{violence:'Bạo lực',gore:'Máu me',adult:'18+',visual_logo:'Logo / quảng cáo',text:'Chữ'}};if(!pending.length)return 'Đã duyệt đủ. Bạn có thể xuất video.';return `Còn ${{pending.length}} mục chưa duyệt: ${{pending.slice(0,3).map(x=>`${{names[x.category]||x.category}} ${{clock(x.start_seconds)}}–${{clock(x.end_seconds)}}`).join('; ')}}. Hãy chọn Giữ nguyên cảnh, Làm mờ toàn cảnh, Cắt cả cảnh hoặc Cần xem thêm.`;}}
function render(){{const c=queue.counts,done=c.total-c.pending,scope=queue.detection_scope||{{}},scopeNames={{advertising:'Quảng cáo / logo',adult:'18+',gore:'Máu me',violence:'Bạo lực'}},selected=(scope.selected||[]).map(x=>scopeNames[x]||x),skipped=(scope.skipped||[]).map(x=>scopeNames[x]||x);const fullTracks=queue.items.filter(x=>x.candidate_type==='persistent_overlay'&&trackCoversFullVideo(x)).length,rangeTracks=queue.items.filter(x=>x.candidate_type==='persistent_overlay'&&!trackCoversFullVideo(x)).length,advisory=(queue.advisory_items||[]).length,visual=queue.visual_ai_audit?.assessment_count||0;document.querySelector('#summary').textContent=`${{done}}/${{c.total}} mục chính đã duyệt · ${{c.pending}} mục chính còn lại · ${{fullTracks}} track toàn video · ${{rangeTracks}} track theo khoảng · ${{advisory}} ứng viên phụ · Đã quét: ${{selected.length?selected.join(', '):'phạm vi cũ'}} · Visual AI ${{visual}} mục · Trạng thái: ${{queue.status}}`;document.querySelector('#candidate-filter').textContent=`Ứng viên phụ (${{advisory}})`;document.querySelector('#progress').style.width=`${{c.total?done/c.total*100:100}}%`;if(resources){{const range=resources.estimated_preview_megabytes_range;document.querySelector('#resources').innerHTML=`<div class="resource">Video nguồn<strong>${{size(resources.source_bytes)}}</strong></div><div class="resource">Ảnh và report<strong>${{size(resources.report_bytes)}}</strong></div><div class="resource">Ổ E còn trống<strong>${{size(resources.disk_free_bytes)}}</strong></div><div class="resource">Preview dự kiến<strong>${{resources.estimated_preview_seconds}} giây · khoảng ${{range[0]}}–${{range[1]}} MB</strong></div><div class="resource-note">Review chỉ tải ảnh và không chạy model. ${{skipped.length?`Không quét trong lượt này: ${{skipped.join(', ')}}. Ít thẻ hơn không có nghĩa các nhóm này đã an toàn. `:''}}Có ${{c.total}} mục chính bắt buộc duyệt và ${{advisory}} ứng viên phụ không chặn xuất. Track toàn video, track theo khoảng và nhóm sự kiện được ghi riêng; nhóm sự kiện chỉ áp dụng các khoảng phát hiện gốc, không sửa khoảng trống.</div>`;}}const active=['QUEUED','RENDERING'].includes(exportJob.status);const ready=queue.status==='READY_FOR_EDIT_PLAN'&&!active;document.querySelector('#finalize').disabled=!ready;const exportText={{IDLE:pendingDescription(),WAITING_REVIEW:pendingDescription(),READY_TO_EXPORT:'Đã duyệt đủ. Bạn có thể xuất video.',QUEUED:'Đã xếp hàng xuất video.',RENDERING:'Đang render và kiểm tra video…',COMPLETED:`Hoàn tất: ${{exportJob.output||''}}`,FAILED:`Xuất thất bại: ${{exportJob.error||'không rõ lỗi'}}`}};document.querySelector('#export-status').textContent=exportText[exportJob.status]||pendingDescription();const source=filter==='candidates'?(queue.advisory_items||[]):queue.items;const data=filter==='candidates'?source:source.filter(visible);document.querySelector('#items').innerHTML=data.length?data.map(card).join(''):'<div class="empty">Không có mục nào trong bộ lọc này.</div>';drawRegionPreviews();}}
function actionName(x,decision){{if(decision==='KEEP')return 'Giữ nguyên';if(decision==='CUT')return 'Cắt cả cảnh';if(decision==='BLUR')return x.suggested_region_source_pixels?(isLogoItem(x)?'Làm mờ logo':'Làm mờ vùng chữ/logo'):'Làm mờ toàn cảnh';return decision;}}
function trackCoversFullVideo(x){{const duration=Number(queue.source?.duration_seconds||0),tolerance=Math.max(1.5,duration*.0005);return duration>0&&Number(x.start_seconds)<=tolerance&&Number(x.end_seconds)>=duration-tolerance;}}
function decisionScope(x){{const intervals=Array.isArray(x.detected_intervals)?x.detected_intervals:[],from=clock(x.start_seconds),to=clock(x.end_seconds);if(x.advisory)return{{kind:'advisory',title:'Ứng viên kiểm tra thêm — chưa thuộc quyết định chính',detail:`Bằng chứng chưa đủ để ghép mục này vào track chính. Thẻ chính khác không tự xử lý mục này. Nếu bạn chọn một hành động, mục sẽ được đưa vào kế hoạch và chỉ áp dụng ${{from}}–${{to}}.`}};if(x.candidate_type==='persistent_overlay'||x.temporal_policy==='continuous_persistent_overlay'){{const full=trackCoversFullVideo(x),support=Number(x.supporting_candidate_count||0);return{{kind:'track',title:full?'QUYẾT ĐỊNH TOÀN VIDEO':'QUYẾT ĐỊNH TOÀN KHOẢNG XUẤT HIỆN',detail:`Một lựa chọn cho vùng khoanh đỏ áp dụng từ ${{from}} đến ${{to}}${{full?' — toàn bộ video':''}}. ${{support?`Track này đại diện thêm ${{support}} lần phát hiện cùng vùng đã lưu trong Audit. `:''}}Logo ở vị trí hoặc track khác vẫn cần quyết định riêng.`}};}}if(x.temporal_policy==='discrete_detected_intervals'&&intervals.length>1)return{{kind:'grouped',title:`NHÓM SỰ KIỆN — ${{intervals.length}} khoảng phát hiện`,detail:`Một lựa chọn được áp dụng riêng cho ${{intervals.length}} khoảng gốc trong ${{from}}–${{to}}; các khoảng trống giữa chúng không bị cắt hoặc làm mờ.`}};if(intervals.length>1)return{{kind:'grouped',title:`Đại diện cho ${{intervals.length}} lần phát hiện đã gom`,detail:`Các lần phát hiện gần nhau đã được gom thành cửa sổ ${{from}}–${{to}}; quyết định áp dụng toàn bộ cửa sổ này. Không tự lan sang cảnh khác.`}};return{{kind:'single',title:'CHỈ ĐOẠN HIỆN TẠI',detail:`Quyết định chỉ áp dụng ${{from}}–${{to}}. Đây không phải lựa chọn đại diện cho mọi quảng cáo hoặc logo cùng loại trong toàn phim.`}};}}
function scopeBlock(x){{const scope=decisionScope(x);return `<div class="scope-detail ${{scope.kind}}"><strong>Phạm vi áp dụng: ${{esc(scope.title)}}</strong>${{esc(scope.detail)}}</div>`;}}
function regionOverlap(a,b){{if(!a||!b||a==='FULL_FRAME'||b==='FULL_FRAME')return 0;const left=Math.max(a.x,b.x),top=Math.max(a.y,b.y),right=Math.min(a.x+a.width,b.x+b.width),bottom=Math.min(a.y+a.height,b.y+b.height),intersection=Math.max(0,right-left)*Math.max(0,bottom-top),smaller=Math.min(a.width*a.height,b.width*b.height);return smaller?intersection/smaller:0;}}
function overlapCoverage(x){{const covered=queue.items.filter(other=>other.id!==x.id&&other.decision==='BLUR'&&other.start_seconds<x.end_seconds&&other.end_seconds>x.start_seconds);if(!covered.length)return '';const persistent=covered.filter(other=>other.candidate_type==='persistent_overlay'&&other.decision_region_source_pixels&&other.decision_region_source_pixels!=='FULL_FRAME');const full=covered.filter(other=>other.decision_region_source_pixels==='FULL_FRAME');const parts=[];if(persistent.length){{const owner=persistent[0],r=owner.decision_region_source_pixels,current=x.suggested_region_source_pixels||x.decision_region_source_pixels,same=regionOverlap(r,current)>=.6,label=esc((owner.labels||[])[0]||'logo/watermark'),scope=trackCoversFullVideo(owner)?'toàn video':`${{clock(owner.start_seconds)}}–${{clock(owner.end_seconds)}}`;parts.push(same?`Track <strong>${{label}}</strong> cùng vùng này đã được duyệt làm mờ ${{scope}}; thẻ hiện tại chỉ là bằng chứng hỗ trợ.`:`Track <strong>${{label}}</strong> ở vùng khác đã được duyệt làm mờ ${{scope}} (x=${{r.x}}, y=${{r.y}}, rộng=${{r.width}}, cao=${{r.height}}). Vùng đỏ hiện tại vẫn là ứng viên riêng.`);}}if(full.length)parts.push(`${{full.length}} đoạn trùng thời gian đã được duyệt làm mờ toàn cảnh.`);return parts.length?`<div class="coverage">${{parts.join(' ')}}</div>`:'';}}
function regionOwner(x){{if(x.suggested_region_source_pixels&&Array.isArray(x.source_frame_size))return x;if(x.advisory)return null;return queue.items.find(other=>other.id!==x.id&&isLogoItem(other)&&other.decision==='BLUR'&&(other.suggested_region_source_pixels||other.decision_region_source_pixels)&&Array.isArray(other.source_frame_size)&&other.start_seconds<x.end_seconds&&other.end_seconds>x.start_seconds)||null;}}
function regionName(owner){{if(owner?.decision==='BLUR')return 'logo thương hiệu đã xác nhận';if(owner?.decision==='KEEP')return 'tiêu đề/nội dung phim đã xác nhận';const names={{movie_title:'tiêu đề phim',approved_non_brand:'nội dung phim đã xác nhận',external_brand:'logo thương hiệu',external_brand_candidate:'ứng viên logo thương hiệu',branded_end_card:'end-card thương hiệu',promotional_segment:'đoạn quảng bá',unknown:'chưa phân loại'}};return names[owner?.region_classification]||'vùng chưa phân loại';}}
function regionImages(x,owner){{if(!owner)return x.preview_images.slice(0,3).map(p=>`<img src="/media/${{encodeURIComponent(p)}}" loading="lazy">`).join('');const r=owner.suggested_region_source_pixels||owner.decision_region_source_pixels;if(!r||r==='FULL_FRAME')return x.preview_images.slice(0,3).map(p=>`<img src="/media/${{encodeURIComponent(p)}}" loading="lazy">`).join('');return x.preview_images.slice(0,3).map((p,index)=>`<div class="region-pair"><canvas class="region-frame" data-src="/media/${{encodeURIComponent(p)}}" data-x="${{r.x}}" data-y="${{r.y}}" data-w="${{r.width}}" data-h="${{r.height}}" data-sw="${{owner.source_frame_size[0]}}" data-sh="${{owner.source_frame_size[1]}}"></canvas>${{index===0?`<canvas class="region-crop" data-src="/media/${{encodeURIComponent(p)}}" data-x="${{r.x}}" data-y="${{r.y}}" data-w="${{r.width}}" data-h="${{r.height}}" data-sw="${{owner.source_frame_size[0]}}" data-sh="${{owner.source_frame_size[1]}}"></canvas>`:''}}</div>`).join('');}}
function drawRegionPreviews(){{document.querySelectorAll('canvas.region-frame,canvas.region-crop').forEach(canvas=>{{const image=new Image();image.onload=()=>{{const sourceW=Number(canvas.dataset.sw),sourceH=Number(canvas.dataset.sh),scale=Math.min(image.naturalWidth/sourceW,image.naturalHeight/sourceH),offsetX=(image.naturalWidth-sourceW*scale)/2,offsetY=(image.naturalHeight-sourceH*scale)/2,sx=Number(canvas.dataset.x)*scale+offsetX,sy=Number(canvas.dataset.y)*scale+offsetY,sw=Number(canvas.dataset.w)*scale,sh=Number(canvas.dataset.h)*scale,ctx=canvas.getContext('2d');if(canvas.classList.contains('region-crop')){{const padX=sw*.12,padY=sh*.18,x=Math.max(0,sx-padX),y=Math.max(0,sy-padY),w=Math.min(image.naturalWidth-x,sw+padX*2),h=Math.min(image.naturalHeight-y,sh+padY*2);canvas.width=360;canvas.height=Math.max(100,Math.round(360*h/w));ctx.drawImage(image,x,y,w,h,0,0,canvas.width,canvas.height);ctx.strokeStyle='#ff304f';ctx.lineWidth=5;ctx.strokeRect((sx-x)/w*canvas.width,(sy-y)/h*canvas.height,sw/w*canvas.width,sh/h*canvas.height);}}else{{canvas.width=Math.min(640,image.naturalWidth);canvas.height=Math.round(canvas.width*image.naturalHeight/image.naturalWidth);ctx.drawImage(image,0,0,canvas.width,canvas.height);const kx=canvas.width/image.naturalWidth,ky=canvas.height/image.naturalHeight;ctx.fillStyle='rgba(255,48,79,.15)';ctx.fillRect(sx*kx,sy*ky,sw*kx,sh*ky);ctx.strokeStyle='#ff304f';ctx.lineWidth=4;ctx.strokeRect(sx*kx,sy*ky,sw*kx,sh*ky);}}}};image.src=canvas.dataset.src;}});}}
function card(x){{const owner=regionOwner(x),r=owner&&(owner.suggested_region_source_pixels||owner.decision_region_source_pixels),imgs=regionImages(x,owner),d=x.decision,ownerDecision=owner?.decision,full=d==='BLUR'&&x.decision_region_source_pixels==='FULL_FRAME',suggestion=x.suggested_decision?`<span class="badge suggestion">Đề xuất: ${{esc(actionName(x,x.suggested_decision))}}</span>`:'',category=x.review_kind||x.category,visualAI=x.ai_visual_audit?`<div class="visual-ai"><strong>Visual AI:</strong> ${{esc(x.ai_visual_audit.classification)}} · tin cậy ${{Math.round(100*Number(x.ai_visual_audit.confidence||0))}}% · đề xuất ${{esc(actionName(x,x.ai_visual_audit.suggested_decision))}} · vùng ${{esc(x.ai_visual_audit.region_assessment)}}<br>${{esc(x.ai_visual_audit.reasoning)}}</div>`:'',evidence=x.model_evidence?`<div class="labels">AI cục bộ: ${{esc(JSON.stringify(x.model_evidence))}}</div>`:'',borrowed=owner&&owner.id!==x.id,regionStatus=regionName(owner),regionDetail=owner&&r&&r!=='FULL_FRAME'?`<div class="region-detail">Chỉ nội dung nằm trong khung đỏ này đang được phân loại. Vùng khoanh đỏ: <strong>${{esc(regionStatus)}}</strong> · x=${{r.x}}, y=${{r.y}}, rộng=${{r.width}}, cao=${{r.height}}${{borrowed?` · vùng liên kết áp dụng ${{clock(owner.start_seconds)}}–${{clock(owner.end_seconds)}}`:''}}</div>`:'<div class="region-detail">Chưa có vùng được định vị nên không thể phân loại logo hay tiêu đề một cách an toàn.</div>',regionControls=owner&&r&&r!=='FULL_FRAME'?`<div class="decision-block"><strong>${{borrowed?'Xử lý riêng vùng logo khoanh đỏ':'Phân loại vùng khoanh đỏ'}}</strong>${{borrowed?`<small>Vùng logo áp dụng ${{clock(owner.start_seconds)}}–${{clock(owner.end_seconds)}}. Quyết định toàn cảnh bên dưới chỉ áp dụng ${{clock(x.start_seconds)}}–${{clock(x.end_seconds)}}; nếu chọn Cắt cả cảnh, đoạn bị cắt không cần làm mờ.</small>`:'<small>Chỉ lựa chọn theo phần nằm trong khung đỏ, không theo logo hoặc chữ ở vị trí khác trong ảnh.</small>'}}<div class="decision-buttons"><button class="keep ${{ownerDecision==='KEEP'?'selected':''}}" onclick="decide('${{owner.id}}','KEEP',false,'Đã xác nhận vùng khoanh đỏ là tiêu đề hoặc nội dung hợp lệ của phim')">Đây là tiêu đề/nội dung phim — giữ lại</button><button class="blur ${{ownerDecision==='BLUR'?'selected':''}}" onclick="decide('${{owner.id}}','BLUR',false,'Đã xác nhận vùng khoanh đỏ là logo thương hiệu')">Đây là logo thương hiệu — làm mờ</button></div></div>`:'';return `<article class="${{esc(x.priority)}}"><div class="meta"><span class="badge">${{esc(category)}}</span><span class="badge">${{esc(x.priority)}}</span>${{suggestion}}<span>${{clock(x.start_seconds)}}–${{clock(x.end_seconds)}}</span><span>điểm ${{x.max_score==null?'—':Number(x.max_score).toFixed(3)}}</span></div><div class="images">${{imgs}}</div>${{scopeBlock(x)}}${{regionDetail}}${{overlapCoverage(x)}}<div class="labels">${{esc(x.labels.join(', ')||x.reasons.join(', '))}}</div>${{visualAI}}${{evidence}}<div class="actions">${{regionControls}}<div class="decision-block"><strong>Quyết định cho toàn cảnh ${{esc(category)}} · ${{clock(x.start_seconds)}}–${{clock(x.end_seconds)}}</strong><div class="decision-buttons"><button class="keep ${{d==='KEEP'?'selected':''}}" onclick="decide('${{x.id}}','KEEP')">Giữ nguyên cảnh</button><button class="blur-full ${{full?'selected':''}}" onclick="decide('${{x.id}}','BLUR',true)">Làm mờ toàn cảnh</button><button class="cut ${{d==='CUT'?'selected':''}}" onclick="decide('${{x.id}}','CUT')">Cắt cả cảnh</button><button class="context ${{d==='NEEDS_MORE_CONTEXT'?'selected':''}}" onclick="decide('${{x.id}}','NEEDS_MORE_CONTEXT')">Cần xem thêm</button>${{d?`<button class="clear" onclick="clearDecision('${{x.id}}')">Bỏ chọn</button>`:''}}</div></div></div></article>`;}}
async function post(path,body,retried=false){{if(saving)return;saving=true;document.body.classList.add('saving');try{{let response;try{{response=await fetch(path,{{method:'POST',headers:{{'Content-Type':'application/json','X-BiliFlow-Token':token}},body:JSON.stringify(body)}});}}catch(_error){{throw new Error('Mất kết nối với Review. Hãy tải lại trang; các lựa chọn đã lưu trước đó vẫn được giữ nguyên.');}}if(response.status===403&&!retried){{const session=await requestJson('/api/session',{{cache:'no-store'}});token=session.token;saving=false;document.body.classList.remove('saving');return await post(path,body,true);}}queue=await readJson(response);resources=await requestJson('/api/resources',{{cache:'no-store'}});render();}}finally{{saving=false;document.body.classList.remove('saving');}}}}
async function decide(id,decision,needsFullFrame=false,note=null){{try{{const item=queue.items.find(x=>x.id===id),ai=item?.ai_visual_audit,aiDecision=ai?.suggested_decision,confidence=Number(ai?.confidence||0);if(aiDecision&&confidence>=.9&&decision!==aiDecision&&['KEEP','BLUR','CUT'].includes(aiDecision)&&['KEEP','BLUR','CUT'].includes(decision)){{const message=`Visual AI tin cậy ${{Math.round(confidence*100)}}% đề xuất “${{actionName(item,aiDecision)}}” vì vùng đỏ được nhận là ${{ai.classification||'nội dung phim'}}. Bạn vẫn muốn chọn “${{actionName(item,decision)}}” cho đúng vùng đỏ này?`;if(!confirm(message))return;}}let full_frame=false;if(decision==='BLUR'&&needsFullFrame){{full_frame=confirm('Bạn có xác nhận làm mờ toàn bộ khung hình trong đoạn này?');if(!full_frame)return;}}await post('/api/decision',{{id,decision,full_frame,note}});}}catch(e){{alert(e.message);}}}}
async function clearDecision(id){{try{{await post('/api/clear',{{id}});}}catch(e){{alert(e.message);}}}}
async function bulkKeep(){{const count=queue.items.filter(visible).filter(x=>!x.decision).length;if(!count){{alert('Không có mục chưa duyệt trong bộ lọc này.');return;}}if(!confirm(`Giữ nguyên ${{count}} mục chưa duyệt đang hiển thị? Thao tác này không blur hoặc cắt video.`))return;try{{await post('/api/bulk-keep',{{filter}});}}catch(e){{alert(e.message);}}}}
async function bulkAccept(){{const count=queue.items.filter(visible).filter(x=>!x.decision&&x.suggested_decision).length;if(!count){{alert('Không có đề xuất chưa duyệt trong bộ lọc này.');return;}}if(!confirm(`Áp dụng ${{count}} đề xuất đang hiển thị? Bạn vẫn có thể bỏ chọn từng mục trước khi xuất.`))return;try{{await post('/api/bulk-accept',{{filter}});}}catch(e){{alert(e.message);}}}}
async function finalizeExport(){{if(queue.status!=='READY_FOR_EDIT_PLAN'){{alert('Vẫn còn mục chưa có quyết định cuối cùng.');return;}}try{{const selection=outputSizeSelection();if(!confirm(`Khóa các lựa chọn hiện tại và bắt đầu xuất video hoàn chỉnh (${{selection.description}})?`))return;const response=await fetch('/api/finalize',{{method:'POST',headers:{{'Content-Type':'application/json','X-BiliFlow-Token':token}},body:JSON.stringify(selection)}});exportJob=await readJson(response);queue.export_size_policy=exportJob.export_size_policy||queue.export_size_policy;render();}}catch(e){{alert(e.message);}}}}
document.querySelectorAll('nav button[data-filter]').forEach(b=>b.onclick=()=>{{document.querySelectorAll('nav button[data-filter]').forEach(x=>x.classList.remove('active'));b.classList.add('active');filter=b.dataset.filter;render();}});load();
setInterval(refreshQueue,3000);
setInterval(async()=>{{if(['QUEUED','RENDERING'].includes(exportJob.status)){{try{{exportJob=await requestJson('/api/export',{{cache:'no-store'}});render();}}catch(_error){{}}}}}},3000);
</script></body></html>"""


def serve_review_ui(
    *, project_root: Path, queue_path: Path, host: str = "127.0.0.1", port: int = 8765,
) -> None:
    root = project_root.resolve(strict=True)
    reports_root = (root / "reports").resolve(strict=True)
    queue_path = _inside(reports_root, queue_path, "Queue path").resolve(strict=True)
    token = secrets.token_urlsafe(24)
    write_lock = threading.Lock()
    export_lock = threading.Lock()

    def export_status() -> dict:
        queue = _read_json(queue_path)
        _, output_path, job_path = review_export_paths(root, queue)
        if job_path.exists():
            return _read_json(job_path)
        if output_path.exists():
            return {
                "status": "COMPLETED",
                "output": output_path.relative_to(root).as_posix(),
            }
        return {"status": "IDLE"}

    def run_export(plan_path: Path, output_path: Path, job_path: Path) -> None:
        with export_lock:
            state = _read_json(job_path)
            policy = state.get("export_size_policy") or normalize_output_size_policy()
            state["status"] = "RENDERING"
            state["started_at"] = _now()
            _write_json(job_path, state)
            try:
                manifest = render_final_output(
                    project_root=root, plan_path=plan_path, output_path=output_path,
                    ffmpeg_path=root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
                    ffprobe_path=root / "tools" / "ffmpeg" / "bin" / "ffprobe.exe",
                    max_output_bytes=policy["maximum_output_bytes"],
                    target_output_bytes=policy["target_output_bytes"],
                )
                state.update({
                    "status": "COMPLETED", "completed_at": _now(),
                    "output": manifest["output"]["path"],
                    "bytes": manifest["output"]["bytes"],
                    "duration_seconds": manifest["output"]["duration_seconds"],
                    "within_size_limit": manifest["output"]["within_size_limit"],
                    "within_3_5_gb_limit": manifest["output"]["within_3_5_gb_limit"],
                })
            except Exception as error:
                state.update({
                    "status": "FAILED", "completed_at": _now(),
                    "error": str(error),
                })
                print(f"BiliFlow background export failed: {error}", flush=True)
            _write_json(job_path, state)

    def start_export(*, size_mode: str = "default", max_output_gb: object = None) -> dict:
        queue = _read_json(queue_path)
        if queue.get("status") != "READY_FOR_EDIT_PLAN":
            raise ValueError("Vẫn còn mục chưa có quyết định cuối cùng")
        policy = normalize_output_size_policy(size_mode, max_output_gb)
        queue["export_size_policy"] = policy
        _write_json(queue_path, queue)
        plan_path, output_path, job_path = review_export_paths(root, queue)
        if job_path.exists():
            existing = _read_json(job_path)
            if existing.get("status") in {"QUEUED", "RENDERING", "COMPLETED"}:
                return existing
        if output_path.exists():
            return {
                "status": "COMPLETED",
                "output": output_path.relative_to(root).as_posix(),
            }
        build_edit_plan(project_root=root, queue_path=queue_path, plan_path=plan_path)
        authorize_final_from_resolved_review(
            project_root=root, plan_path=plan_path, actor="local_review_ui",
        )
        state = {
            "schema_version": 1, "status": "QUEUED", "created_at": _now(),
            "queue": _relative(root, queue_path),
            "plan": plan_path.relative_to(root).as_posix(),
            "output": output_path.relative_to(root).as_posix(),
            "maximum_output_bytes": policy["maximum_output_bytes"],
            "export_size_policy": policy,
        }
        _write_json(job_path, state)
        threading.Thread(
            target=run_export, args=(plan_path, output_path, job_path),
            name=f"biliflow-export-{output_path.stem}", daemon=True,
        ).start()
        return state

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            return

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict) -> None:
            self._send(
                status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/":
                self._send(200, _interactive_html(token).encode("utf-8"), "text/html; charset=utf-8")
                return
            if parsed.path == "/api/queue":
                self._json(200, _read_json(queue_path))
                return
            if parsed.path == "/api/session":
                self._json(200, {"token": token})
                return
            if parsed.path == "/api/resources":
                self._json(200, review_resource_status(project_root=root, queue_path=queue_path))
                return
            if parsed.path == "/api/export":
                self._json(200, export_status())
                return
            if parsed.path.startswith("/media/"):
                relative = urllib.parse.unquote(parsed.path.removeprefix("/media/"))
                try:
                    target = _inside(reports_root, root / relative, "Media path").resolve(strict=True)
                except (FileNotFoundError, ValueError):
                    self._json(404, {"error": "Không tìm thấy ảnh"})
                    return
                content_type = "image/jpeg" if target.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
                self._send(200, target.read_bytes(), content_type)
                return
            self._json(404, {"error": "Không tìm thấy"})

        def do_POST(self) -> None:
            if self.headers.get("X-BiliFlow-Token") != token:
                self._json(403, {"error": "Phiên review không hợp lệ"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 65536:
                    raise ValueError("Dữ liệu gửi lên quá lớn")
                body = json.loads(self.rfile.read(length) or b"{}")
                with write_lock:
                    if self.path == "/api/decision":
                        updated = record_review_decision(
                            project_root=root, queue_path=queue_path,
                            item_id=str(body["id"]), decision=str(body["decision"]),
                            note=body.get("note"), full_frame=bool(body.get("full_frame", False)),
                            actor="local_user", transport="local_ui",
                        )
                    elif self.path == "/api/clear":
                        updated = clear_review_decision(
                            project_root=root, queue_path=queue_path, item_id=str(body["id"]),
                            actor="local_user", transport="local_ui",
                        )
                    elif self.path == "/api/bulk-keep":
                        updated = bulk_keep_review_items(
                            project_root=root, queue_path=queue_path,
                            review_filter=str(body["filter"]),
                            actor="local_user", transport="local_ui",
                        )
                    elif self.path == "/api/bulk-accept":
                        updated = bulk_accept_suggested_decisions(
                            project_root=root, queue_path=queue_path,
                            review_filter=str(body["filter"]),
                            actor="local_user", transport="local_ui",
                        )
                    elif self.path == "/api/finalize":
                        job = start_export(
                            size_mode=str(body.get("size_mode") or "default"),
                            max_output_gb=body.get("max_output_gb"),
                        )
                        self._json(202, job)
                        return
                    else:
                        self._json(404, {"error": "Không tìm thấy"})
                        return
                self._json(200, updated)
            except (KeyError, TypeError, ValueError) as error:
                self._json(400, {"error": str(error)})
            except OSError:
                self._json(500, {"error": "Không thể ghi lựa chọn xuống ổ đĩa"})

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"BiliFlow review UI: http://{host}:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def build_edit_plan(*, project_root: Path, queue_path: Path, plan_path: Path) -> dict:
    root = project_root.resolve(strict=True)
    queue_path = _inside((root / "reports").resolve(strict=True), queue_path, "Queue path")
    plan_path = _inside((root / "work").resolve(strict=True), plan_path, "Edit plan path")
    payload = _read_json(queue_path.resolve(strict=True))
    unresolved = [
        item["id"] for item in payload["items"]
        if item.get("decision") in {None, "NEEDS_MORE_CONTEXT"}
    ]
    if unresolved:
        raise ValueError(
            f"Cannot create edit plan: {len(unresolved)} review items remain unresolved"
        )
    source_duration = float(payload["source"]["duration_seconds"])
    operations = []
    for item in payload["items"]:
        if item["decision"] == "KEEP":
            continue
        discrete = item.get("temporal_policy") == "discrete_detected_intervals"
        application_intervals = (
            list(item.get("detected_intervals") or [])
            if discrete else [{
                "start_seconds": item["start_seconds"],
                "end_seconds": item["end_seconds"],
            }]
        )
        for interval_index, detected in enumerate(application_intervals):
            provenance_interval = (
                detected if discrete else item.get("detected_interval") or detected
            )
            operation_id = f'op-{item["id"].removeprefix("review-")}'
            if len(application_intervals) > 1:
                operation_id = f"{operation_id}-{interval_index + 1:02d}"
            operation = {
                "id": operation_id,
                "type": item["decision"].lower(),
                "category": item["category"],
                "start_seconds": max(0.0, float(detected["start_seconds"])),
                "end_seconds": min(source_duration, float(detected["end_seconds"])),
                "reason": item.get("decision_note") or ", ".join(item["reasons"]),
                "review_item_id": item["id"],
                "review_item_ids": [item["id"]],
                "detected_intervals": [{
                    "review_item_id": item["id"],
                    "start_seconds": provenance_interval["start_seconds"],
                    "end_seconds": provenance_interval["end_seconds"],
                }],
                "evidence": item["evidence"],
            }
            if operation["start_seconds"] >= operation["end_seconds"]:
                raise ValueError(
                    f"Reviewed operation is outside the source: {item['id']}"
                )
            if item["decision"] == "BLUR":
                operation["region_source_pixels"] = item[
                    "decision_region_source_pixels"
                ]
                region = operation["region_source_pixels"]
                adaptive_feather = (
                    min(4, max(2, int(region["height"]) // 24))
                    if isinstance(region, dict) else 0
                )
                operation["blur"] = {
                    "sigma": 28,
                    "edge_feather_pixels": adaptive_feather,
                    "edge_feather_mode": item.get("decision_blur_edge_mode") or "all_edges",
                    "region_policy": "ocr_union_asymmetric_tight_v3",
                }
            operations.append(operation)
    cuts = sorted(
        (operation for operation in operations if operation["type"] == "cut"),
        key=lambda operation: operation["start_seconds"],
    )
    merged_cuts = []
    for operation in cuts:
        if merged_cuts and operation["start_seconds"] <= merged_cuts[-1]["end_seconds"]:
            previous = merged_cuts[-1]
            previous["end_seconds"] = max(previous["end_seconds"], operation["end_seconds"])
            previous["review_item_ids"].extend(operation["review_item_ids"])
            previous["detected_intervals"].extend(operation["detected_intervals"])
            previous["evidence"] = list(dict.fromkeys(previous["evidence"] + operation["evidence"]))
            previous["reason"] = "; ".join(dict.fromkeys(
                part for part in (previous["reason"], operation["reason"]) if part
            ))
        else:
            merged_cuts.append({
                **operation,
                "review_item_ids": list(operation["review_item_ids"]),
                "detected_intervals": list(operation["detected_intervals"]),
                "evidence": list(operation["evidence"]),
            })
    operations = sorted(
        [operation for operation in operations if operation["type"] != "cut"] + merged_cuts,
        key=lambda operation: operation["start_seconds"],
    )
    plan = {
        "schema_version": 2,
        "status": "READY_FOR_PREVIEW",
        "created_at": _now(),
        "source": payload["source"],
        "review_queue": _relative(root, queue_path),
        "review_counts": payload["counts"],
        "export_size_policy": payload.get("export_size_policy")
        or normalize_output_size_policy(),
        "approved_operations": operations,
        "final_export_requested": False,
        "final_export_allowed": False,
        "next_gate": "Render and approve short previews before any full export",
    }
    _write_json(plan_path, plan)
    return plan


def _run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "FFmpeg failed")


def render_edit_previews(
    *, project_root: Path, plan_path: Path, output_dir: Path,
    ffmpeg_path: Path, ffprobe_path: Path, context_seconds: float = 3.0,
    operation_ids: tuple[str, ...] | None = None,
) -> dict:
    root = project_root.resolve(strict=True)
    plan_path = _inside(root, plan_path, "Edit plan path").resolve(strict=True)
    previews_root = (root / "previews").resolve(strict=True)
    output_dir = _inside(previews_root, output_dir, "Preview directory")
    ffmpeg_path = ffmpeg_path.resolve(strict=True)
    ffprobe_path = ffprobe_path.resolve(strict=True)
    if context_seconds <= 0:
        raise ValueError("context_seconds must be positive")
    plan = _read_json(plan_path)
    if plan.get("status") != "READY_FOR_PREVIEW":
        raise ValueError("Edit plan is not ready for preview")
    source = Path(plan["source"]["path"]).resolve(strict=True)
    expected_sha256 = plan["source"].get("sha256")
    if expected_sha256 and _sha256(source) != expected_sha256:
        raise ValueError("Source checksum changed after review; previews are blocked")
    duration = float(plan["source"]["duration_seconds"])
    source_probe = probe_video(ffprobe_path, source)
    if abs(duration_seconds(source_probe) - duration) > 0.1:
        raise ValueError("Source duration changed after review; previews are blocked")
    has_audio = any(
        stream.get("codec_type") == "audio"
        for stream in source_probe.get("streams", [])
    )
    video_stream = next(
        (stream for stream in source_probe.get("streams", []) if stream.get("codec_type") == "video"),
        None,
    )
    if video_stream is None:
        raise ValueError("Source has no video stream")
    source_width = int(video_stream["width"])
    source_height = int(video_stream["height"])
    output_dir.mkdir(parents=True, exist_ok=True)
    operations = plan.get("approved_operations", [])
    requested_ids = set(operation_ids or ())
    if requested_ids:
        known_ids = {operation["id"] for operation in operations}
        missing_ids = requested_ids - known_ids
        if missing_ids:
            raise ValueError(
                "Preview operation is not in the edit plan: "
                + ", ".join(sorted(missing_ids))
            )
        operations = [
            operation for operation in operations
            if operation["id"] in requested_ids
        ]
    if not operations:
        raise ValueError("Edit plan has no operations to preview")
    rendered = []
    for operation in operations:
        start = float(operation["start_seconds"])
        end = float(operation["end_seconds"])
        if not 0 <= start < end <= duration + 0.1:
            raise ValueError(f"Invalid operation interval: {operation['id']}")
        target = output_dir / f"{operation['id']}-{operation['type']}.mp4"
        if operation["type"] == "blur":
            window_start = max(0.0, start - context_seconds)
            window_end = min(duration, end + context_seconds)
            relative_start = start - window_start
            relative_end = end - window_start
            region = operation["region_source_pixels"]
            if region == "FULL_FRAME":
                sigma, _ = blur_parameters(operation)
                video_filter = (
                    f"gblur=sigma={sigma:g}:enable='between(t,{relative_start:.3f},{relative_end:.3f})'"
                )
                command = [
                    str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", f"{window_start:.3f}", "-t", f"{window_end-window_start:.3f}",
                    "-i", str(source), "-vf", video_filter, "-map", "0:v:0",
                ]
            else:
                x, y, width, height = (
                    int(region["x"]), int(region["y"]),
                    int(region["width"]), int(region["height"]),
                )
                if x + width > source_width or y + height > source_height:
                    raise ValueError(
                        f"Blur region exceeds source frame for {operation['id']}"
                    )
                sigma, feather = blur_parameters(operation)
                feather_mode = blur_feather_mode(operation)
                filter_complex = ";".join(regional_blur_filters(
                    input_label="0:v", output_label="v", prefix="preview",
                    region=region, sigma=sigma, feather=feather,
                    feather_mode=feather_mode,
                    enable=f"enable='between(t,{relative_start:.3f},{relative_end:.3f})'",
                ))
                command = [
                    str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", f"{window_start:.3f}", "-t", f"{window_end-window_start:.3f}",
                    "-i", str(source), "-filter_complex", filter_complex, "-map", "[v]",
                ]
            if has_audio:
                command += ["-map", "0:a?"]
        elif operation["type"] == "cut":
            before = max(0.0, start - context_seconds)
            after = min(duration, end + context_seconds)
            if has_audio:
                filter_complex = (
                    f"[0:v]trim=start={before:.3f}:end={start:.3f},setpts=PTS-STARTPTS[v0];"
                    f"[0:a]atrim=start={before:.3f}:end={start:.3f},asetpts=PTS-STARTPTS[a0];"
                    f"[0:v]trim=start={end:.3f}:end={after:.3f},setpts=PTS-STARTPTS[v1];"
                    f"[0:a]atrim=start={end:.3f}:end={after:.3f},asetpts=PTS-STARTPTS[a1];"
                    "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]"
                )
                command = [
                    str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                    "-filter_complex", filter_complex, "-map", "[v]", "-map", "[a]",
                ]
            else:
                filter_complex = (
                    f"[0:v]trim=start={before:.3f}:end={start:.3f},setpts=PTS-STARTPTS[v0];"
                    f"[0:v]trim=start={end:.3f}:end={after:.3f},setpts=PTS-STARTPTS[v1];"
                    "[v0][v1]concat=n=2:v=1:a=0[v]"
                )
                command = [
                    str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                    "-filter_complex", filter_complex, "-map", "[v]",
                ]
        else:
            raise ValueError(f"Unsupported preview operation: {operation['type']}")
        command += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(target),
        ]
        _run(command)
        rendered.append(
            {
                "operation_id": operation["id"], "type": operation["type"],
                "path": _relative(root, target), "bytes": target.stat().st_size,
                "duration_seconds": round(duration_seconds(probe_video(ffprobe_path, target)), 3),
            }
        )
    manifest = {
        "schema_version": 1,
        "status": "PREVIEW_REVIEW_REQUIRED",
        "created_at": _now(),
        "edit_plan": _relative(root, plan_path),
        "source": plan["source"],
        "preview_scope": "SAMPLED" if requested_ids else "ALL",
        "sampled_operation_ids": sorted(requested_ids),
        "previews": rendered,
        "safety": {"final_export_created": False, "source_modified": False},
    }
    _write_json(output_dir / "preview-manifest.json", manifest)
    return manifest

