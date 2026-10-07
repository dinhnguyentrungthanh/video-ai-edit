from __future__ import annotations

import contextlib
import difflib
import hashlib
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import unicodedata
import urllib.parse
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable

from biliflow.adult_verification import (
    ADULT_TRIAGE_EVIDENCE,
    ADULT_TRIAGE_LEVELS,
    CREDITS_LABEL,
    CREDITS_VERIFIER_GUARD,
    TRIAGE_SCAN_SAMPLE_FPS,
    TRIAGE_SCAN_THRESHOLD,
    VERIFIER_CALIBRATED_REVISION,
    VERIFIER_FRAME_SIZE,
    VERIFIER_MODEL_DIR,
    VERIFIER_SAMPLE_FPS,
    VERIFIER_TARGET_LABEL,
    normalize_adult_triage_level,
    verification_is_calibrated,
)
from biliflow.brand_memory import (
    STUDIO_LOGO_MAXIMUM_CELL_DIFFERENCE,
    STUDIO_LOGO_MEMORY_PATH,
    STUDIO_LOGO_MINIMUM_SIMILARITY,
    compare_studio_logo,
    forget_review_item,
    load_studio_logo_memory,
    match_studio_logo,
    prepare_studio_logo_frames,
    refresh_studio_logo_masks,
    relative_box_to_region,
    remember_review_item,
    remember_studio_logo as remember_studio_logo_record,
    studio_logo_eligible,
    studio_logo_signatures,
    studio_logo_window_frames,
)
from biliflow.gore_triage import (
    GORE_TRIAGE_EVIDENCE,
    GORE_TRIAGE_LEVELS,
    GORE_TRIAGE_MODEL_REPO,
    GORE_TRIAGE_MODEL_REVISION,
    GORE_TRIAGE_SCAN_PRECISION,
    GORE_TRIAGE_SCAN_SAMPLE_FPS,
    gore_evidence_hint,
    gore_rule_matches,
    gore_scan_is_calibrated,
    normalize_gore_triage_level,
    valid_gore_tag_evidence,
)
from biliflow.job_pipeline import DEFAULT_DETECTOR_GROUPS
from biliflow.platform_cards import (
    ENDING_LABEL,
    build_platform_cards,
    ensure_forced_ending_card,
    link_platform_cards,
    overlay_regions,
)
from biliflow.platform_logos import SourceProbe, platform_memory_records, platform_memory_runs
from biliflow.platform_memory import (
    UNKNOWN_PLATFORM_NAME,
    MemoryChanged,
    forget_remembered_logo,
    in_fresh_folder,
    platform_entry,
    platform_logo_eligible,
    prepare_platform_logo_frames,
    remember_platform_logo as remember_platform_logo_record,
    source_box,
)
from biliflow.platform_names import match_platform_texts
from biliflow.blur_filter import (
    COVER_METHOD,
    DEFAULT_SIGMA,
    blur_feather_mode,
    blur_method,
    blur_parameters,
    cover_sigma,
    regional_blur_filters,
)
from biliflow.probe import duration_seconds, probe_video
from biliflow.export_dialog import (
    EXPORT_CUSTOM_GB_ATTRIBUTES,
    EXPORT_DIALOG_JS,
    export_size_options_html,
)
# serve_review_ui fails closed for every video the Control Center owns
# (export_guards imports no module that imports this one).
from biliflow.export_guards import (
    EXPORT_PATH_TAKEN_MESSAGE,
    standalone_edit_refusal,
    standalone_export_refusal,
)
from biliflow.export_identity import export_paths, render_identity, verified_export
from biliflow.http_guards import (
    REQUEST_TIMEOUT_MESSAGE,
    REQUEST_TIMEOUT_SECONDS,
    content_length,
    loopback_bind_address,
    require_loopback_host,
)
from biliflow.final_renderer import (
    authorize_final_from_resolved_review,
    normalize_output_size_policy,
    render_final_output,
)


DECISIONS = ("KEEP", "BLUR", "CUT", "NEEDS_MORE_CONTEXT")
_PRIORITY_RANK = {"high": 0, "context": 1, "normal": 2}
# Cards BiliFlow always shows once (lead decision A1, 2026-10-03): the first
# window (visual-logo scanner) and the last seconds (build-review). They ask
# about the whole scene: a localized box is display evidence only, and no
# corroboration gate (quarantine, revalidation, title / in-film text guards,
# overlay reconciliation) moves or drops them; nor a platform-logo card.
FORCED_BOUNDARY_TYPES = frozenset({"opening_boundary", "ending_boundary"})
PLATFORM_LOGO_TYPE = "platform_logo"
GATE_PROTECTED_TYPES = FORCED_BOUNDARY_TYPES | {PLATFORM_LOGO_TYPE}
# The studio-logo memory route (R3b) still moves a forced card whose every
# preview repeats a studio logo the user kept and remembered (the licence card
# "giữ & nhớ" exists exactly for this).
FORCED_BOUNDARY_STUDIO_MOVE = True


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


def _coverage(inner: dict, outer: dict) -> float:
    """Share of ``inner`` that lies inside ``outer``."""
    ax1, ay1 = int(inner["x"]), int(inner["y"])
    ax2, ay2 = ax1 + int(inner["width"]), ay1 + int(inner["height"])
    bx1, by1 = int(outer["x"]), int(outer["y"])
    bx2, by2 = bx1 + int(outer["width"]), by1 + int(outer["height"])
    intersection = max(0, min(ax2, bx2) - max(ax1, bx1)) * max(
        0, min(ay2, by2) - max(ay1, by1)
    )
    return intersection / max(1, int(inner["width"]) * int(inner["height"]))


def _has_approved_brand_region(item: dict) -> bool:
    """The proposed box is the region a user approved in brand memory."""
    evidence = item.get("model_evidence") or {}
    return "brand_memory" in set(evidence.get("region_sources") or [])


def _is_text_line_fragment(part: dict, whole: dict) -> bool:
    """Whether ``whole`` continues the text line of ``part`` well beyond it.

    A complete OCR read of a mark is often a few pixels wider than a tight
    read (padding). A fragment such as "Online.net" of "PhimOnline.net" misses
    at least one glyph: the fuller reads extend it along the line by half a
    text height or more and it spans clearly less of the line. Reads that are
    much taller than the fragment describe an emblem around the text, not the
    same line, and keep the existing emblem handling.
    """
    horizontal = int(whole["width"]) >= int(whole["height"])
    start, length, cross = ("x", "width", "height") if horizontal else ("y", "height", "width")
    part_start, part_length = int(part[start]), int(part[length])
    whole_start, whole_length = int(whole[start]), int(whole[length])
    if int(whole[cross]) > int(part[cross]) * 1.5:
        return False
    beyond = max(
        part_start - whole_start,
        (whole_start + whole_length) - (part_start + part_length),
    )
    return (
        part_length <= whole_length * 0.85
        and beyond >= int(part[cross]) * 0.5
    )


def _dominant_region_cluster(regions: list[dict]) -> list[dict]:
    """Keep the repeated spatial target and reject nearby OCR outliers."""
    clusters: list[list[dict]] = []
    # Geometry breaks area ties so the result does not depend on item order.
    for region in sorted(
        regions, key=lambda value: (
            int(value["width"]) * int(value["height"]),
            int(value["y"]), int(value["x"]),
            int(value["height"]), int(value["width"]),
        ),
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
    # Every track is judged against the boxes as built, and the refinements are
    # applied afterwards: a track refined earlier in the list must not become
    # evidence for the next one, so the result does not depend on item order.
    built_regions = {
        id(item): item.get("suggested_region_source_pixels") for item in items
    }
    refinements: list[tuple[dict, dict]] = []
    for item in items:
        region = built_regions[id(item)]
        if (
            item.get("category") not in {"visual_logo", "text"}
            or item.get("candidate_type") != "persistent_overlay"
            or not isinstance(region, dict)
            # A user-approved brand-memory box is never replaced by OCR reads.
            or _has_approved_brand_region(item)
        ):
            continue
        area = int(region["width"]) * int(region["height"])
        supporting: list[dict] = []
        wider_reads: list[dict] = []
        for other in items:
            other_region = built_regions[id(other)]
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
            sources = set((other.get("model_evidence") or {}).get("region_sources") or [])
            if (
                "ocr" not in sources
                or _intersection_over_smaller(region, other_region) < 0.70
            ):
                continue
            if other_area <= area * 0.65:
                supporting.append(other_region)
            elif other_area <= area * 2.0:
                # Too large to prove the box is oversized, but still a read of
                # the same spot, possibly with a little more padding than the
                # track box; used below to detect a fragmentary consensus.
                wider_reads.append(other_region)
        supporting = _dominant_region_cluster(supporting)
        if len(supporting) < 2:
            continue
        refined = supporting[0]
        for candidate in supporting[1:]:
            refined = union_pixel_regions(refined, candidate)
        assert refined is not None
        refined_area = int(refined["width"]) * int(refined["height"])
        # OCR can repeatedly read only part of a mark, e.g. "Online.net" of
        # the "PhimOnline.net" watermark, and that fragment then looks like a
        # tight consensus. When repeated fuller reads contain it and continue
        # its text line, shrinking to the fragment would leave readable parts
        # of the mark unblurred: use the extent of those fuller reads instead.
        # Fuller reads that only add padding do not stop a legitimate trim.
        fuller_reads = _dominant_region_cluster([
            value for value in wider_reads
            if int(value["width"]) * int(value["height"]) > refined_area
            and _intersection_over_smaller(refined, value) >= 0.70
        ])
        full_mark = None
        for candidate in fuller_reads:
            full_mark = union_pixel_regions(full_mark, candidate)
        if (
            len(fuller_reads) >= 2
            and full_mark is not None
            and _is_text_line_fragment(refined, full_mark)
        ):
            refinements.append((item, {
                "method": "repeated_full_mark_ocr_support",
                "support_count": len(fuller_reads),
                "original_region": region,
                # A fuller read only needs to contain 70% of the fragment, so
                # keep the fragment inside the box as well.
                "refined_region": union_pixel_regions(full_mark, refined),
                "ocr_fragment_region": refined,
                "ocr_fragment_support_count": len(supporting),
            }))
            continue
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
        refinements.append((item, {
            "method": method,
            "support_count": len(supporting),
            "original_region": region,
            "refined_region": refined,
        }))
    for item, refinement in refinements:
        item["suggested_region_source_pixels"] = refinement["refined_region"]
        item["region_refinement"] = refinement
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
    # A box the user approved in brand memory owns its watermark: it is
    # reconciled first, and an OCR or unapproved visual box absorbs it only
    # when that box covers the approved one.
    persistent_owners = sorted(
        items,
        key=lambda item: (
            0 if item.get("category") == "visual_logo"
            and _has_approved_brand_region(item)
            else 1 if item.get("category") == "visual_logo" else 2
        ),
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
        owner_is_approved_brand = _has_approved_brand_region(visual)
        # The approved box joins an OCR track of the same mark at the overlap
        # at which the track would otherwise have absorbed that brand card.
        required_track_overlap = 0.60 if owner_is_approved_brand else 0.75
        for text_item in items if visual.get("category") == "visual_logo" else []:
            text_region = text_item.get("suggested_region_source_pixels")
            if (
                text_item is visual
                or id(text_item) in removed
                or text_item.get("category") != "text"
                or text_item.get("candidate_type") != "persistent_overlay"
                or not isinstance(text_region, dict)
                or _intersection_over_smaller(visual_region, text_region)
                < required_track_overlap
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
        # A persistent OCR track already joined to this card is evidence of
        # the same mark: a card inside that track's box that proposes the same
        # action is support even when the owner's own (for example approved)
        # box is tighter. Cards without a proposal keep their advisory path.
        # The card must still overlap the owner's own box (0.60, as for an
        # external brand): only that box is blurred after one approval, so a
        # card elsewhere in a long track stays its own decision.
        track_regions = [
            record["region_source_pixels"]
            for record in visual.get("supporting_detections") or []
            if record.get("category") == "text"
            and record.get("candidate_type") == "persistent_overlay"
            and isinstance(record.get("region_source_pixels"), dict)
        ]
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
            inside_joined_track = (
                isinstance(support_region, dict)
                and support_overlap >= 0.60
                and support.get("suggested_decision") is not None
                and support.get("suggested_decision") == visual.get("suggested_decision")
                and any(
                    _coverage(support_region, track_region) >= 0.80
                    for track_region in track_regions
                )
            )
            if (
                support is visual
                or id(support) in removed
                or support.get("category") not in {"visual_logo", "text"}
                or support.get("candidate_type") in {
                    "opening_promotion", "branded_end_card", "promotional_segment",
                    "scene_text", "subtitle", "title_overlay", *GATE_PROTECTED_TYPES,
                }
                or support.get("review_kind") == "title_overlay"
                or not isinstance(support_region, dict)
                # An approved brand box is only absorbed by another owner
                # whose own box covers it; a fragment would lose part of it.
                or (
                    _has_approved_brand_region(support)
                    and not owner_is_approved_brand
                    and _coverage(support_region, visual_region) < 0.95
                )
                or (support_overlap < required_overlap and not inside_joined_track)
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


# One card per fight / blood scene (user decision 2026-10-01, R1). Violence and
# gore moments of the same category whose gap is shorter than 20 s share one
# card, at most 300 s from its first to its last second. 18+ never forms scene
# cards: a scene there can mix BLUR and KEEP moments, so it keeps the short
# 6 s / 30 s event groups below.
SCENE_CARD_KINDS = {"violence": "fight", "gore": "blood"}
SCENE_CARD_MAXIMUM_GAP_SECONDS = 20.0  # strict: a gap of exactly 20 s starts a new card
SCENE_CARD_MAXIMUM_SPAN_SECONDS = 300.0


def application_intervals(item: dict) -> list[dict]:
    """The time an approved action on ``item`` edits, as ``build_edit_plan`` applies it."""
    if item.get("temporal_policy") == "discrete_detected_intervals":
        intervals = [
            {"start_seconds": float(value["start_seconds"]), "end_seconds": float(value["end_seconds"])}
            for value in item.get("detected_intervals") or []
            if isinstance(value, dict)
        ]
        if intervals:
            return intervals
    return [{"start_seconds": float(item["start_seconds"]), "end_seconds": float(item["end_seconds"])}]


def _union_moments(intervals: Iterable[dict]) -> list[dict]:
    moments: list[dict] = []
    for value in sorted(intervals, key=lambda v: (float(v["start_seconds"]), float(v["end_seconds"]))):
        start, end = float(value["start_seconds"]), float(value["end_seconds"])
        if moments and start <= moments[-1]["end_seconds"]:
            moments[-1]["end_seconds"] = round(max(moments[-1]["end_seconds"], end), 3)
        else:
            moments.append({"start_seconds": round(start, 3), "end_seconds": round(end, 3)})
    return moments


def group_safety_review_events(
    items: list[dict], *, maximum_gap_seconds: float = 6.0,
    maximum_span_seconds: float = 30.0,
    scene_gap_seconds: float = SCENE_CARD_MAXIMUM_GAP_SECONDS,
    scene_span_seconds: float = SCENE_CARD_MAXIMUM_SPAN_SECONDS,
) -> list[dict]:
    """Group nearby safety detections into one review decision.

    The original intervals remain discrete.  ``build_edit_plan`` expands an
    approved action back to those intervals, so a gap used only for review
    context is never cut or blurred.

    Violence and gore form scene cards (``scene_card``): every member card
    becomes one detected moment holding exactly the time an action on that
    member alone would have edited, so a decision on the scene card edits the
    union of its moments and never a gap between them. 18+ keeps the short
    ``maximum_gap_seconds`` / ``maximum_span_seconds`` event groups.
    """
    if (
        maximum_gap_seconds < 0 or maximum_span_seconds <= 0
        or scene_gap_seconds < 0 or scene_span_seconds <= 0
    ):
        raise ValueError("Invalid safety event grouping limits")
    safety_categories = {"adult", "gore", "violence"}
    ordered = sorted(items, key=lambda item: (item["category"], item["start_seconds"]))
    grouped: list[dict] = []
    for original in ordered:
        item = dict(original)
        previous = grouped[-1] if grouped else None
        # A scene card may show no suggestion while its members carry one, so
        # compare against every explicit suggestion already inside the card.
        explicit = {
            value for value in (
                previous["scene_card"]["member_suggestions"]
                if previous is not None and previous.get("scene_card")
                else [previous.get("suggested_decision") if previous is not None else None]
            ) if value is not None
        }
        suggestions_compatible = previous is not None and (
            item.get("suggested_decision") is None
            or explicit <= {item.get("suggested_decision")}
        )
        scene = item.get("category") in SCENE_CARD_KINDS
        if scene and previous is not None:
            close_enough = float(item["start_seconds"]) - float(previous["end_seconds"]) < scene_gap_seconds
            short_enough = (
                max(float(previous["end_seconds"]), float(item["end_seconds"]))
                - float(previous["start_seconds"]) <= scene_span_seconds
            )
        elif previous is not None:
            close_enough = float(item["start_seconds"]) <= (
                float(previous["end_seconds"]) + maximum_gap_seconds
            )
            short_enough = (
                float(item["end_seconds"]) - float(previous["start_seconds"])
                <= maximum_span_seconds
            )
        can_group = (
            previous is not None
            and previous.get("category") == item.get("category")
            and item.get("category") in safety_categories
            and previous.get("decision") is None
            and item.get("decision") is None
            and close_enough
            and short_enough
            and suggestions_compatible
        )
        if not can_group:
            grouped.append(item)
            continue
        if scene:
            _merge_scene_moment(previous, item, scene_gap_seconds, scene_span_seconds)
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


def _merge_scene_moment(
    card: dict, member: dict, gap_seconds: float, span_seconds: float,
) -> None:
    """Add ``member`` to the scene card ``card`` (same category, both undecided)."""
    moments = list(card["detected_intervals"]) if card.get("scene_card") else application_intervals(card)
    moments = _union_moments([*moments, *application_intervals(member)])
    card["start_seconds"] = round(min(float(card["start_seconds"]), float(member["start_seconds"])), 3)
    card["end_seconds"] = round(max(float(card["end_seconds"]), float(member["end_seconds"])), 3)
    scores = [value for value in (card.get("max_score"), member.get("max_score")) if value is not None]
    card["max_score"] = max(scores) if scores else None
    card["priority"] = min(
        (card["priority"], member["priority"]),
        key=lambda value: _PRIORITY_RANK.get(value, 2),
    )
    # A scene card keeps a suggestion only when every member had that same one,
    # so accepting suggestions in bulk never cuts a moment that had none.
    member_suggestions = list(dict.fromkeys([
        *(card["scene_card"]["member_suggestions"] if card.get("scene_card")
          else [card.get("suggested_decision")]),
        member.get("suggested_decision"),
    ]))
    card["suggested_decision"] = member_suggestions[0] if len(member_suggestions) == 1 else None
    for key in ("labels", "reasons", "evidence", "preview_images", "source_candidate_refs"):
        card[key] = list(dict.fromkeys(list(card.get(key) or []) + list(member.get(key) or [])))
    card["detected_intervals"] = moments
    card["candidate_type"] = "review_event_group"
    card["temporal_policy"] = "discrete_detected_intervals"
    card["event_detection_count"] = len(moments)
    card["scene_card"] = {
        "kind": SCENE_CARD_KINDS[card["category"]],
        "moment_count": len(moments),
        "detected_seconds": round(sum(m["end_seconds"] - m["start_seconds"] for m in moments), 3),
        "span_seconds": round(card["end_seconds"] - card["start_seconds"], 3),
        "maximum_gap_seconds": gap_seconds,
        "maximum_span_seconds": span_seconds,
        "applies_to": "detected_intervals_only",
        "member_suggestions": member_suggestions,
    }
    card["id"] = _item_id(
        card["category"], card["start_seconds"], card["end_seconds"], "|".join(card["evidence"]),
    )


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
            and original.get("candidate_type") not in GATE_PROTECTED_TYPES
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


CONTENT_STYLES = ("animation", "live_action", "mixed", "unknown")
# Tolerance for a source interval that must still overlap the item that cites it.
_ADULT_REF_OVERLAP_TOLERANCE = 1.0


def _candidate_reference(value: object) -> tuple[str, int] | None:
    report, separator, index = str(value).rpartition("#interval:")
    if not separator or not report:
        return None
    try:
        position = int(index)
    except ValueError:
        return None
    return (report.replace("\\", "/"), position) if position >= 0 else None


def _calibrated_nsfw_scan(payload: dict) -> bool:
    try:
        return (
            abs(float(payload.get("sample_fps")) - TRIAGE_SCAN_SAMPLE_FPS) < 1e-9
            and abs(float(payload.get("threshold")) - TRIAGE_SCAN_THRESHOLD) < 1e-9
        )
    except (TypeError, ValueError):
        return False


def triage_adult_items(
    items: list[dict], scan_payloads: dict[str, dict],
    job_content_style: str | None, level: str | None = None,
) -> tuple[list[dict], list[dict], dict]:
    """Move weak, undecided live-action 18+ candidates to the optional list.

    Returns ``(required, advisory, audit)`` in the style of
    ``revalidate_preserved_review_items``. Nothing is deleted: a moved item keeps
    every interval and source reference, appears under "Xem tất cả ứng viên"
    and returns to the main list as soon as the reviewer decides it. Settings
    and evidence live in ``biliflow.adult_verification``.

    Only items with ``category == "adult"``, no decision, and every source
    reference resolving to a live-action nsfw-nano scan run at the calibrated
    settings are considered, and only when the job is ``live_action`` (mixed,
    animation and an unknown style move nothing). R1 moves an item whose every
    interval is labelled "hentai" (credits). The two-signal rule moves an item
    only when n_seeds < k and the verifier's NSFW max < t; a missing, failed or
    uncalibrated verification keeps the item in the main list.
    """
    level = normalize_adult_triage_level(level)
    settings = ADULT_TRIAGE_LEVELS[level]
    two_signal = settings["two_signal"]
    # A verified copy may also be listed under its scan.json path; report it once.
    reports_by_payload: dict[int, str] = {}
    for report, payload in scan_payloads.items():
        reports_by_payload.setdefault(id(payload), report)
    audit: dict = {
        "level": level,
        "content_style": job_content_style,
        "credits_rule": bool(settings["credits_rule"]),
        "two_signal": dict(two_signal) if two_signal else None,
        "scan_calibration": {
            "sample_fps": TRIAGE_SCAN_SAMPLE_FPS, "threshold": TRIAGE_SCAN_THRESHOLD,
        },
        "verifier": {
            "model": VERIFIER_MODEL_DIR, "target_label": VERIFIER_TARGET_LABEL,
            "calibrated_revision": VERIFIER_CALIBRATED_REVISION,
            "sample_fps": VERIFIER_SAMPLE_FPS, "frame_size": VERIFIER_FRAME_SIZE,
        },
        "verification_reports": {
            report: str((payload.get("adult_verification") or {}).get("state") or "MISSING")
            for report, payload in sorted(scan_payloads.items())
            if payload.get("scan_type") == "nsfw" and reports_by_payload.get(id(payload)) == report
        },
        "evidence": ADULT_TRIAGE_EVIDENCE,
        "applied": False,
        "reason": None,
        "evaluated_items": 0,
        "moved_items": 0,
        "moved_seconds": 0.0,
        "moved_by_rule": {"credits": 0, "two_signal": 0},
        "kept_by_reason": {},
        "note": (
            "Moved items stay in advisory_items with every interval and source reference; "
            "none is deleted or marked safe."
        ),
    }
    if not settings["credits_rule"] and two_signal is None:
        audit["reason"] = "level_off"
        return list(items), [], audit
    if job_content_style != "live_action":
        audit["reason"] = (
            "content_style_missing" if not job_content_style
            else "content_style_not_live_action"
        )
        return list(items), [], audit
    audit["applied"] = True
    kept_by_reason: dict[str, int] = {}
    required: list[dict] = []
    advisory: list[dict] = []
    for original in items:
        if original.get("category") != "adult":
            required.append(original)
            continue
        if original.get("decision") is not None:
            # A human decision is never moved, whatever the detectors say.
            kept_by_reason["decided"] = kept_by_reason.get("decided", 0) + 1
            required.append(original)
            continue
        audit["evaluated_items"] += 1
        try:
            item_start = float(original.get("start_seconds"))
            item_end = float(original.get("end_seconds"))
        except (TypeError, ValueError):
            item_start = item_end = None
        resolved: list[tuple[dict, dict]] = []
        seen_intervals: set[tuple[int, int]] = set()
        problem = None
        references = list(dict.fromkeys(
            str(value) for value in original.get("source_candidate_refs") or []
        ))
        if not references:
            problem = "no_source_refs"
        for reference in references:
            parsed = _candidate_reference(reference)
            payload = scan_payloads.get(parsed[0]) if parsed else None
            intervals = payload.get("intervals") if isinstance(payload, dict) else None
            if (
                parsed is None or not isinstance(intervals, list)
                or parsed[1] >= len(intervals) or not isinstance(intervals[parsed[1]], dict)
            ):
                problem = "unresolved_source_refs"
                break
            if payload.get("scan_type") != "nsfw" or payload.get("content_style") != "live_action":
                problem = "not_live_action_nsfw_scan"
                break
            interval = intervals[parsed[1]]
            try:
                overlaps = item_start is not None and (
                    float(interval["start_seconds"]) <= item_end + _ADULT_REF_OVERLAP_TOLERANCE
                    and float(interval["end_seconds"]) >= item_start - _ADULT_REF_OVERLAP_TOLERANCE
                )
            except (KeyError, TypeError, ValueError):
                overlaps = False
            if not overlaps:
                problem = "stale_source_refs"
                break
            # A scan.json reference and its verified alias name the same interval:
            # count its seeds once.
            if (id(payload), parsed[1]) not in seen_intervals:
                seen_intervals.add((id(payload), parsed[1]))
                resolved.append((payload, interval))
        info: dict = {
            "outcome": "kept", "rule": None, "reason": problem, "level": level,
            "n_seeds": None, "labels": [], "verifier_max": None,
            "k": two_signal["k"] if two_signal else None,
            "t": two_signal["t"] if two_signal else None,
            "model": VERIFIER_MODEL_DIR, "revision": None,
        }
        rule = None
        if problem is None:
            interval_labels = [
                str(interval.get("predicted_label") or "").strip().casefold()
                for _, interval in resolved
            ]
            n_seeds = 0
            for _, interval in resolved:
                try:
                    n_seeds += max(0, int(interval.get("sample_count") or 0))
                except (TypeError, ValueError):
                    pass
            verifications = [interval.get("adult_verification") for _, interval in resolved]
            calibrated = [verification_is_calibrated(value) for value in verifications]
            verifier_max = (
                max(float(value["nsfw_max"]) for value in verifications) if all(calibrated) else None
            )
            revisions = sorted({
                str(value.get("revision")) for value in verifications
                if isinstance(value, dict) and value.get("revision")
            })
            info.update(
                n_seeds=n_seeds,
                labels=sorted(set(interval_labels) - {""}),
                verifier_max=round(verifier_max, 6) if verifier_max is not None else None,
                revision=revisions[0] if len(revisions) == 1 else (revisions or None),
            )
            if not all(_calibrated_nsfw_scan(payload) for payload, _ in resolved):
                info["reason"] = "uncalibrated_scan"
            elif settings["credits_rule"] and all(
                label == CREDITS_LABEL for label in interval_labels
            ) and not (verifier_max is not None and verifier_max >= CREDITS_VERIFIER_GUARD):
                rule = "credits"
            elif two_signal is None:
                info["reason"] = "no_rule_for_level"
            elif any(not isinstance(value, dict) for value in verifications):
                info["reason"] = "verification_missing"
            elif any(value.get("state") != "SCORED" for value in verifications):
                info["reason"] = "verification_failed"
            elif verifier_max is None:
                info["reason"] = "verification_uncalibrated"
            elif n_seeds < two_signal["k"] and verifier_max < two_signal["t"]:
                rule = "two_signal"
            else:
                info["reason"] = "strong_evidence"
        if rule is None:
            item = dict(original)
            item["adult_triage"] = info
            reason = str(info["reason"])
            kept_by_reason[reason] = kept_by_reason.get(reason, 0) + 1
            required.append(item)
            continue
        info.update(outcome="advisory", rule=rule, reason=None)
        if rule == "credits":
            sentence = (
                "Mọi khung 18+ của mục này chỉ mang nhãn 'hentai' (thường là chữ hoặc "
                "credits trên nền tối) trong phim người đóng; chuyển sang Ứng viên phụ, "
                "không xóa và không coi là đã an toàn"
            )
        else:
            sentence = (
                f"Hai bộ kiểm cùng yếu: chỉ {info['n_seeds']} khung vượt ngưỡng 18+ "
                f"(dưới {two_signal['k']}) và bộ kiểm thứ hai chấm NSFW cao nhất "
                f"{info['verifier_max']:.2f} (dưới {two_signal['t']:g}); chuyển sang "
                "Ứng viên phụ, không xóa và không coi là đã an toàn"
            )
        item = dict(original)
        item["advisory"] = True
        item["priority"] = "context"
        # Weak evidence is not proof of safety: no action is suggested.
        item["suggested_decision"] = None
        item["adult_triage"] = info
        item["reasons"] = list(dict.fromkeys(list(item.get("reasons") or []) + [sentence]))
        audit["moved_items"] += 1
        audit["moved_by_rule"][rule] += 1
        if item_start is not None and item_end is not None:
            audit["moved_seconds"] += max(0.0, item_end - item_start)
        advisory.append(item)
    audit["moved_seconds"] = round(audit["moved_seconds"], 3)
    audit["kept_by_reason"] = dict(sorted(kept_by_reason.items()))
    return required, advisory, audit


def _resolve_gore_intervals(
    item: dict, scan_payloads: dict[str, dict],
) -> tuple[list[tuple[dict, dict]], str | None]:
    """The gore scan intervals an item cites, or the reason they cannot be used.

    Every source reference must resolve to an interval of an animation gore
    report that still overlaps the item; a reference listed twice (a report and
    its alias) counts once.
    """
    try:
        item_start = float(item.get("start_seconds"))
        item_end = float(item.get("end_seconds"))
    except (TypeError, ValueError):
        return [], "stale_source_refs"
    references = list(dict.fromkeys(str(value) for value in item.get("source_candidate_refs") or []))
    if not references:
        return [], "no_source_refs"
    resolved: list[tuple[dict, dict]] = []
    seen: set[tuple[int, int]] = set()
    for reference in references:
        parsed = _candidate_reference(reference)
        payload = scan_payloads.get(parsed[0]) if parsed else None
        intervals = payload.get("intervals") if isinstance(payload, dict) else None
        if (
            parsed is None or not isinstance(intervals, list)
            or parsed[1] >= len(intervals) or not isinstance(intervals[parsed[1]], dict)
        ):
            return [], "unresolved_source_refs"
        if payload.get("scan_type") != "gore" or payload.get("content_style") != "animation":
            return [], "not_animation_gore_scan"
        interval = intervals[parsed[1]]
        try:
            overlaps = (
                float(interval["start_seconds"]) <= item_end + _ADULT_REF_OVERLAP_TOLERANCE
                and float(interval["end_seconds"]) >= item_start - _ADULT_REF_OVERLAP_TOLERANCE
            )
        except (KeyError, TypeError, ValueError):
            overlaps = False
        if not overlaps:
            return [], "stale_source_refs"
        if (id(payload), parsed[1]) not in seen:
            seen.add((id(payload), parsed[1]))
            resolved.append((payload, interval))
    return resolved, None


def _combined_gore_evidence(intervals: list[dict]) -> dict:
    evidence = [interval["gore_tag_evidence"] for interval in intervals]
    strongest = max(evidence, key=lambda value: float(value.get("strongest_blood_label_score") or 0.0))
    return {
        "blood_family_max": max(float(value["blood_family_max"]) for value in evidence),
        "injury_max": max(float(value["injury_max"]) for value in evidence),
        "corpse_max": max(float(value["corpse_max"]) for value in evidence),
        "confirmed_frames": sum(int(value.get("confirmed_frames") or 0) for value in evidence),
        "intervals": len(evidence),
        "strongest_blood_label": strongest.get("strongest_blood_label"),
    }


def annotate_gore_tag_evidence(items: list[dict], scan_payloads: dict[str, dict]) -> list[dict]:
    """Carry the scanner's gore tag evidence and a one-line hint onto gore cards.

    docs/ANIME_GORE_PLAN.md step 1: the card shows what the tagger saw (blood,
    only an injury, maybe a corpse). Values are maxima over every interval the
    card cites (confirmed frames are summed). A card gets them only when every
    one of its intervals carries evidence; nothing is moved, reordered or
    suggested here.
    """
    output = []
    for original in items:
        if original.get("category") != "gore":
            output.append(original)
            continue
        item = dict(original)
        item.pop("gore_tag_evidence", None)
        item.pop("gore_hint", None)
        resolved, problem = _resolve_gore_intervals(item, scan_payloads)
        intervals = [interval for _, interval in resolved]
        if problem is None and all(
            valid_gore_tag_evidence(interval.get("gore_tag_evidence")) for interval in intervals
        ):
            evidence = _combined_gore_evidence(intervals)
            item["gore_tag_evidence"] = evidence
            hint = gore_evidence_hint(evidence)
            if hint:
                item["gore_hint"] = hint
        output.append(item)
    return output


def triage_anime_gore_items(
    items: list[dict], scan_payloads: dict[str, dict],
    job_content_style: str | None, level: str | None = None,
) -> tuple[list[dict], list[dict], dict]:
    """Move undecided anime gore cards where the tagger saw no blood and no corpse.

    docs/ANIME_GORE_PLAN.md step 3, rule C1, in the style of
    ``triage_adult_items``: returns ``(required, advisory, audit)``. A moved card
    keeps every interval and source reference, appears under "Xem tất cả ứng
    viên" and returns to the main list as soon as the reviewer decides it.

    Only ``category == "gore"`` items with no decision are considered, and only
    when the job is ``animation``. Every source reference must resolve to an
    animation gore report scanned at the calibrated settings whose interval
    carries ``gore_tag_evidence``; a card moves only when EVERY interval passes
    the rule. Missing or uncalibrated evidence keeps the card in the main list.
    """
    level = normalize_gore_triage_level(level)
    settings = GORE_TRIAGE_LEVELS[level]
    audit: dict = {
        "level": level,
        "content_style": job_content_style,
        "rule": dict(settings) if settings else None,
        "scan_calibration": {
            "model": GORE_TRIAGE_MODEL_REPO, "revision": GORE_TRIAGE_MODEL_REVISION,
            "sample_fps": GORE_TRIAGE_SCAN_SAMPLE_FPS, "precision": GORE_TRIAGE_SCAN_PRECISION,
        },
        "evidence": GORE_TRIAGE_EVIDENCE,
        "applied": False,
        "reason": None,
        "evaluated_items": 0,
        "moved_items": 0,
        "moved_intervals": 0,
        "moved_seconds": 0.0,
        "moved_detected_seconds": 0.0,
        "kept_by_reason": {},
        "note": (
            "Moved items stay in advisory_items with every interval and source reference; "
            "none is deleted or marked safe."
        ),
    }
    if settings is None:
        audit["reason"] = "level_off"
        return list(items), [], audit
    if job_content_style != "animation":
        audit["reason"] = (
            "content_style_missing" if not job_content_style
            else "content_style_not_animation"
        )
        return list(items), [], audit
    audit["applied"] = True
    kept_by_reason: dict[str, int] = {}
    required: list[dict] = []
    advisory: list[dict] = []
    for original in items:
        if original.get("category") != "gore":
            required.append(original)
            continue
        if original.get("decision") is not None:
            # A human decision is never moved, whatever the tagger says.
            kept_by_reason["decided"] = kept_by_reason.get("decided", 0) + 1
            required.append(original)
            continue
        audit["evaluated_items"] += 1
        resolved, problem = _resolve_gore_intervals(original, scan_payloads)
        info: dict = {
            "outcome": "kept", "rule": None, "reason": problem, "level": level,
            "intervals": len(resolved), "blood_family_max": None, "corpse_max": None,
            "thresholds": {
                "blood_family_max": settings["blood_family_max"],
                "corpse_max": settings["corpse_max"],
            },
        }
        moved = False
        if problem is None:
            evidence = [interval.get("gore_tag_evidence") for _, interval in resolved]
            if not all(gore_scan_is_calibrated(payload) for payload, _ in resolved):
                info["reason"] = "uncalibrated_scan"
            elif not all(valid_gore_tag_evidence(value) for value in evidence):
                info["reason"] = "evidence_missing"
            else:
                info.update(
                    blood_family_max=round(max(float(v["blood_family_max"]) for v in evidence), 6),
                    corpse_max=round(max(float(v["corpse_max"]) for v in evidence), 6),
                )
                if all(gore_rule_matches(value, settings) for value in evidence):
                    moved = True
                else:
                    info["reason"] = "blood_or_corpse_seen"
        if not moved:
            item = dict(original)
            item["gore_triage"] = info
            reason = str(info["reason"])
            kept_by_reason[reason] = kept_by_reason.get(reason, 0) + 1
            required.append(item)
            continue
        info.update(outcome="advisory", rule=settings["rule"], reason=None)
        sentence = (
            f"Tagger không thấy máu (cao nhất {info['blood_family_max']:.5f}, ngưỡng "
            f"{settings['blood_family_max']:g}) và không thấy xác (cao nhất {info['corpse_max']:.5f}, "
            f"ngưỡng {settings['corpse_max']:g}) ở mọi khoảnh khắc; vết xước/bầm không máu "
            "không cần báo, nên chuyển sang Ứng viên phụ, không xóa và không coi là đã an toàn"
        )
        item = dict(original)
        item["advisory"] = True
        item["priority"] = "context"
        # No blood seen is not proof of safety: no action is suggested.
        item["suggested_decision"] = None
        item["gore_triage"] = info
        item["reasons"] = list(dict.fromkeys(list(item.get("reasons") or []) + [sentence]))
        audit["moved_items"] += 1
        audit["moved_intervals"] += len(resolved)
        audit["moved_seconds"] += max(
            0.0, float(item["end_seconds"]) - float(item["start_seconds"])
        )
        audit["moved_detected_seconds"] += sum(
            max(0.0, value["end_seconds"] - value["start_seconds"])
            for value in application_intervals(item)
        )
        advisory.append(item)
    audit["moved_seconds"] = round(audit["moved_seconds"], 3)
    audit["moved_detected_seconds"] = round(audit["moved_detected_seconds"], 3)
    audit["kept_by_reason"] = dict(sorted(kept_by_reason.items()))
    return required, advisory, audit


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


# Display only (job 42 "Nhất Âu Xuân - Tập 12", 2026-10-02): the scanner keeps
# the first window once even when the visual-language model answered NO and
# rewrites its state to UNCERTAIN, so the card read as "AI found a logo". The
# raw answer and the promotion flag let the page say what the model saw;
# ``vlm_confirmation`` stays the routed state other passes read.
_VLM_ANSWER_DISPLAY_CHARACTERS = 80
_EVIDENCE_LABELS_SHOWN = 3


def _raw_vlm_evidence(interval: dict) -> dict:
    confirmation = interval.get("visual_logo_confirmation")
    confirmation = confirmation if isinstance(confirmation, dict) else {}
    evidence: dict = {}
    answer = " ".join(str(confirmation.get("answer") or "").split())
    if answer:
        evidence["vlm_answer"] = answer[:_VLM_ANSWER_DISPLAY_CHARACTERS]
    if confirmation.get("promoted_from_rejected_boundary") or interval.get(
        "promoted_from_rejected_boundary"
    ):
        evidence["promoted_from_rejected_boundary"] = True
    # The second boundary prompt can turn a logo NO into a promotion card
    # (PROMO_FULL_FRAME); without it the page would say "AI said NO" on a CUT card.
    scene = confirmation.get("boundary_scene_context")
    scene_state = str((scene if isinstance(scene, dict) else {}).get("state") or "").strip()
    if scene_state:
        evidence["vlm_scene"] = scene_state[:_VLM_ANSWER_DISPLAY_CHARACTERS]
    return evidence


# Labels the scanner writes itself (predicted_label fallbacks); they name the
# kind of card, never what is on screen.
_SCANNER_LABELS = frozenset({
    "Persistent external logo / watermark",
    "Visual brand/logo candidate",
    "Opening boundary review",
    ENDING_LABEL,
    "Full-frame promotional material",
    "Full-frame opening promotion / branded intro",
    "Branded end card / channel promotion",
    "Known approved external brand",
})


def _generic_label(label: str) -> bool:
    """A scanner label or a lowercase grounding prompt ("a company logo")."""
    return label in _SCANNER_LABELS or (
        label == label.lower() and label.replace(" ", "").isalpha()
    )


def _clean_labels(values: object) -> list[str]:
    return list(dict.fromkeys(
        cleaned for cleaned in (
            re.sub(r"</?s>", "", str(value)).strip() for value in values or []
        ) if cleaned
    ))


def _evidence_labels(values: object) -> list[str]:
    """Up to three proposal labels, readings ("Motchillv.ph") before generic prompts."""
    labels = _clean_labels(values)
    generic = [label for label in labels if _generic_label(label)]
    return ([label for label in labels if label not in generic] + generic)[:_EVIDENCE_LABELS_SHOWN]


def _specific_label(values: object, fallback: str = "watermark") -> str:
    """The first reading of a card ("XEMBZ.NET"), skipping scanner and prompt labels."""
    return next(
        (label for label in _clean_labels(values) if not _generic_label(label)), fallback,
    )


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
        # A forced boundary card asks about the whole window; a box there (jobs
        # 51/60: an OCR "</s>C" in a corner) made it a region card that the
        # quarantine then hid (A1).
        forced_boundary = interval_candidate_type in FORCED_BOUNDARY_TYPES
        whole_scene = full_scene_cut or forced_boundary
        proposal_items = [None] if whole_scene else (usable_proposals or [None])
        # Display only: the boxes such a scene card drops, so the page can show
        # where the model looked (Tập 10: the site watermark that has its own
        # card). They never become a region or a suggestion.
        evidence_regions = []
        for proposal in usable_proposals if whole_scene else []:
            x, y, width, height = [int(value) for value in proposal["blur_region_px"]]
            if width > 0 and height > 0:
                evidence_regions.append({
                    "x": max(0, x), "y": max(0, y), "width": width, "height": height,
                    "sources": [str(value) for value in proposal.get("sources", [])],
                    "labels": _evidence_labels(proposal.get("labels")),
                    "region_classification": proposal.get("region_classification"),
                })
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
            if forced_boundary:
                suggested_decision = None
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
                    **_raw_vlm_evidence(interval),
                },
                **({
                    "evidence_regions": [dict(value) for value in evidence_regions],
                    "evidence_frame_size": interval.get("region_localization", {}).get("frame_size"),
                } if evidence_regions else {}),
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
                **_raw_vlm_evidence(interval),
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


# Opening studio idents (user decision 2026-10-01, R3a/R3b). Ad evidence is any
# website / phone / @handle token, ad-routed text, or a brand region that is not
# the film's persistent watermark (that watermark is its own card).
_AD_TEXT_PATTERNS = (
    re.compile(r"\b(?:https?|www)\b", re.IGNORECASE),
    re.compile(
        r"[a-z0-9][a-z0-9-]*\s?[.,·•]\s?(?:com|net|org|vn|tv|me|io|xyz|cc|co|info|top|club|"
        r"link|live|online|site|biz|asia|pro|app|fun|vip|bet|win|us|to|ly|gg)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b[a-z0-9][a-z0-9-]{2,}\s(?:com|net|org|vn|tv|xyz)\b", re.IGNORECASE),
    re.compile(r"(?<!\d)(?:\+?\d[\s.\-]?){8,}(?!\d)"),
    re.compile(r"(?<![\w.])@[a-z0-9_.]{3,}", re.IGNORECASE),
)
_AD_TEXT_ROUTINGS = {"REVIEW_AD_LIKELY"}
_PERSISTENT_TEXT_ROUTINGS = {"REVIEW_PERSISTENT_OVERLAY"}
_BRAND_REGION_CLASSES = {
    "external_brand", "external_brand_candidate", "branded_end_card", "promotional_segment",
}
_STRONG_BRAND_MEMORY_SIMILARITY = 0.94
# R3a only proves the absence of website / phone / handle tokens and ad routing.
# A bare site or brand word ("PHIMMOI", "KUBET") reads like a studio name to
# those checks, so the card never claims "no ad text" when OCR read any line:
# it lists the lines and leaves the call to the reviewer (finding 2026-10-01).
STUDIO_IDENT_REASON = (
    "Đoạn mở đầu giống logo hãng phim: không đọc thấy chữ nào (không có website, số điện thoại "
    "hay chữ quảng cáo) nên BiliFlow không đề xuất Cắt. Hãy xem rồi tự chọn."
)
_STUDIO_IDENT_SHOWN_TEXTS = 6
_STUDIO_IDENT_STORED_TEXTS = 20


def studio_ident_reason(texts: list[str]) -> str:
    """Why an opening ident has no pre-selected Cut, naming every OCR line read in its window."""
    if not texts:
        return STUDIO_IDENT_REASON
    shown = ", ".join(texts[:_STUDIO_IDENT_SHOWN_TEXTS]) + (", …" if len(texts) > _STUDIO_IDENT_SHOWN_TEXTS else "")
    return (
        "Đoạn mở đầu giống logo hãng phim: không thấy website hay số điện thoại nên BiliFlow không "
        f"đề xuất Cắt. Chữ đọc được: {shown} — nếu là tên web/thương hiệu lạ hãy Cắt. Hãy xem rồi tự chọn."
    )


def ad_text_tokens(text: str) -> list[str]:
    """Website, domain, phone or @handle tokens in one OCR reading."""
    found = []
    for pattern in _AD_TEXT_PATTERNS:
        found += [match.group(0).strip() for match in pattern.finditer(str(text or ""))]
    return list(dict.fromkeys(value for value in found if value))


def _resolved_intervals(item: dict, scan_payloads: dict[str, dict]) -> list[dict]:
    resolved = []
    for value in item.get("source_candidate_refs") or []:
        report, separator, index = str(value).replace("\\", "/").rpartition("#interval:")
        payload = scan_payloads.get(report)
        if not separator or payload is None or not index.isdigit():
            continue
        intervals = payload.get("intervals") or []
        if int(index) < len(intervals) and isinstance(intervals[int(index)], dict):
            resolved.append(intervals[int(index)])
    return resolved


def _text_scan_payloads(scan_payloads: dict[str, dict]) -> list[dict]:
    """Every OCR text scan once (a verified report may be listed under two names)."""
    unique: dict[int, dict] = {}
    for payload in scan_payloads.values():
        if isinstance(payload, dict) and "tracks" in payload:
            unique.setdefault(id(payload), payload)
    return list(unique.values())


def _window_text_tracks(
    scan_payloads: dict[str, dict], start: float, end: float,
) -> tuple[bool, list[dict]]:
    """(a text scan covers start..end, the non-persistent OCR tracks overlapping it).

    The film's persistent watermark track is left out: it is reviewed as its own card.
    """
    covered = False
    tracks: list[dict] = []
    for payload in _text_scan_payloads(scan_payloads):
        scan_start = float(payload.get("scan_start_seconds") or 0.0)
        scan_duration = payload.get("scan_duration_seconds")
        scan_end = (
            scan_start + float(scan_duration) if scan_duration is not None
            else float(payload.get("duration_seconds") or 0.0)
        )
        if scan_start <= start + 0.001 and scan_end >= end - 0.001:
            covered = True
        for track in payload.get("tracks") or []:
            if not isinstance(track, dict):
                continue
            track_start = float(track.get("start_seconds") or 0.0)
            track_end = float(track.get("end_seconds") or track_start)
            if track_end < start or track_start > end:
                continue
            if (
                track.get("persistent")
                or track.get("candidate_type") == "persistent_overlay"
                or track.get("routing") in _PERSISTENT_TEXT_ROUTINGS
            ):
                continue
            tracks.append(track)
    return covered, tracks


# R3b text guard: a confirmed studio logo remembers the OCR lines seen while it
# was on screen. A later card matching it by picture may only show those lines;
# any other line (a site name such as "BZNET" has no URL token and is routed as
# scene text) keeps the card in the main list. One-character reads are noise.
_OCR_TEXT_MINIMUM_CHARACTERS = 2
_OCR_TEXT_NEAR_RATIO = 0.80
# The similarity ratio is symmetric, so on its own a long remembered line absorbs
# a word joined onto it ("A TimeWarner Company PHIMMOI", "BROS88"). A near match
# must also cover the reading itself: at most one of its characters (one misread)
# may be missing from the remembered line.
_OCR_TEXT_MAXIMUM_UNMATCHED_CHARACTERS = 1


def normalize_ocr_text(text: object) -> str:
    """Upper-case letters and digits only: "PICTU R E $" -> "PICTURE"."""
    return "".join(character for character in str(text or "").upper() if character.isalnum())


def ocr_text_known(text: object, known_texts: Iterable[object]) -> bool:
    """Whether one OCR reading repeats a remembered line (OCR noise tolerated, nothing added)."""
    value = normalize_ocr_text(text)
    if len(value) < _OCR_TEXT_MINIMUM_CHARACTERS:
        return True
    for known in known_texts:
        stored = normalize_ocr_text(known)
        if not stored:
            continue
        if value == stored or (len(value) >= 3 and value in stored):
            return True
        matcher = difflib.SequenceMatcher(None, value, stored, autojunk=False)
        unmatched = len(value) - sum(block.size for block in matcher.get_matching_blocks())
        if unmatched <= _OCR_TEXT_MAXIMUM_UNMATCHED_CHARACTERS and matcher.ratio() >= _OCR_TEXT_NEAR_RATIO:
            return True
    return False


def studio_ident_window_text(item: dict, scan_payloads: dict[str, dict]) -> dict:
    """OCR lines on screen during a full-frame logo card (what a studio-logo record remembers)."""
    covered, tracks = _window_text_tracks(
        scan_payloads, float(item["start_seconds"]), float(item["end_seconds"]),
    )
    texts = [
        str(value).strip() for track in tracks for value in track.get("sample_text") or []
        if str(value).strip()
    ]
    return {"covered": covered, "texts": list(dict.fromkeys(texts))}


def unconfirmed_window_texts(
    item: dict, scan_payloads: dict[str, dict], known_texts: Iterable[object],
) -> list[str] | None:
    """OCR lines in the card's window that the confirmed studio logo did not show; ``None`` if unscanned."""
    known = list(known_texts)
    window = studio_ident_window_text(item, scan_payloads)
    if not window["covered"]:
        return None
    return [text for text in window["texts"] if not ocr_text_known(text, known)]


def _inside_persistent_region(region: dict | None, persistent: list[dict]) -> bool:
    if not isinstance(region, dict):
        return False
    return any(_intersection_over_smaller(region, other) >= 0.50 for other in persistent)


def full_frame_logo_ad_evidence(
    item: dict, items: list[dict], scan_payloads: dict[str, dict],
) -> list[str] | None:
    """Ad evidence inside a full-frame logo card's window; ``None`` when it cannot be checked.

    Checked: every non-persistent OCR text track overlapping the window (tokens,
    ad routing, semantic label, user policy terms), the card's own region
    proposals and strong brand-memory matches. The film's persistent watermark
    is excluded because it is reviewed as its own card. Without a text scan that
    covers the window the absence of ad text is unknown, so nothing is relaxed.
    """
    start, end = float(item["start_seconds"]), float(item["end_seconds"])
    covered, tracks = _window_text_tracks(scan_payloads, start, end)
    if not covered:
        return None
    evidence: list[str] = []
    for track in tracks:
        texts = [str(value) for value in track.get("sample_text") or []]
        tokens = [token for text in texts for token in ad_text_tokens(text)]
        if tokens:
            evidence.append(f"text_token:{tokens[0]}")
        if track.get("routing") in _AD_TEXT_ROUTINGS:
            evidence.append(f"text_routing:{track.get('routing')}")
        if track.get("semantic_top_label") == "advertisement":
            evidence.append("text_semantic:advertisement")
        if track.get("policy_hits"):
            evidence.append("text_policy_term")
    persistent = [
        other["suggested_region_source_pixels"] for other in items
        if other is not item
        and other.get("category") == "visual_logo"
        and other.get("candidate_type") == "persistent_overlay"
        and isinstance(other.get("suggested_region_source_pixels"), dict)
        and float(other["start_seconds"]) <= start + 0.001
        and float(other["end_seconds"]) >= end - 0.001
    ]
    for interval in _resolved_intervals(item, scan_payloads):
        localization = interval.get("region_localization") or {}
        frame_size = localization.get("frame_size") or item.get("source_frame_size")
        for proposal in localization.get("proposals") or []:
            if not isinstance(proposal, dict):
                continue
            box = proposal.get("blur_region_px")
            region = (
                {"x": int(box[0]), "y": int(box[1]), "width": int(box[2]), "height": int(box[3])}
                if isinstance(box, list) and len(box) == 4 else None
            )
            branded = (
                proposal.get("region_classification") in _BRAND_REGION_CLASSES
                or str(proposal.get("suggested_decision") or "").upper() in {"BLUR", "CUT"}
                or any(ad_text_tokens(label) for label in proposal.get("labels") or [])
            )
            if branded and not _inside_persistent_region(region, persistent):
                evidence.append(f"brand_region:{proposal.get('region_classification')}")
        features = ((interval.get("visual_logo_confirmation") or {}).get("features") or {})
        memory = features.get("brand_memory")
        if (
            isinstance(memory, dict)
            and memory.get("memory_class") == "brand"
            and float(memory.get("similarity") or 0.0) >= _STRONG_BRAND_MEMORY_SIMILARITY
        ):
            region = None
            if isinstance(frame_size, list) and len(frame_size) == 2:
                region = relative_box_to_region(memory.get("relative_box"), tuple(frame_size))
            if not _inside_persistent_region(region, persistent):
                evidence.append("brand_memory_match")
    return list(dict.fromkeys(evidence))


def withhold_studio_ident_suggestions(
    items: list[dict], scan_payloads: dict[str, dict],
) -> list[dict]:
    """Drop the pre-selected CUT of an opening studio ident without ad text (R3a).

    The card stays in the main list; only the suggestion is withheld, so a
    reviewer who wants the opening removed still chooses Cut explicitly. Every
    OCR line read in the window is kept in ``suggestion_withheld.window_texts``
    and named in the reason: a site or brand word without a URL token is not
    ad evidence here, so the card must not claim that no ad text was seen.
    """
    for item in items:
        if (
            item.get("category") != "visual_logo"
            or item.get("candidate_type") != "opening_promotion"
            or item.get("decision") is not None
            or item.get("suggested_decision") != "CUT"
            or isinstance(item.get("suggested_region_source_pixels"), dict)
        ):
            continue
        evidence = full_frame_logo_ad_evidence(item, items, scan_payloads)
        if evidence is None or evidence:
            continue
        texts = studio_ident_window_text(item, scan_payloads)["texts"]
        item["suggestion_withheld"] = {
            "previous_suggested_decision": item["suggested_decision"],
            "reason": "opening_studio_ident_without_ad_text",
            "checked": ["ocr_text_tracks", "region_proposals", "brand_memory"],
            "window_texts": texts[:_STUDIO_IDENT_STORED_TEXTS],
            "window_text_count": len(texts),
        }
        item["suggested_decision"] = None
        item["opening_ident"] = True
        item["reasons"] = list(dict.fromkeys(list(item.get("reasons") or []) + [studio_ident_reason(texts)]))
    return items


def route_confirmed_studio_logos(
    root: Path, items: list[dict], scan_payloads: dict[str, dict],
    memory: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Move a logo card matching a user-confirmed studio logo to the optional list (R3b).

    A full-frame, undecided logo card moves only when (1) every preview frame
    repeats a confirmed studio logo by pHash (>= 0.95) AND by its colour grid
    (no local overlay), (2) a text scan covers its window and shows no ad
    evidence, and (3) every OCR line in the window is one the confirmed logo
    showed. Everything else stays required; a picture match blocked by (2) or
    (3) is marked ``studio_logo_match_blocked`` so the reviewer sees why.
    Without a confirmed studio logo nothing changes. A schema-2 record ignores
    the watermark regions the user blurred: text inside them is still caught by
    (3), but a non-text graphic inside them can be caught only by the card's
    own region proposals in (2), and not where the episode has its own
    watermark card — OCR cannot see it.
    """
    for item in items:
        item.pop("studio_logo_match_blocked", None)
        item.pop("studio_logo_compared", None)
    memory = load_studio_logo_memory(root) if memory is None else memory
    if not memory.get("records"):
        return items, []
    kept: list[dict] = []
    moved: list[dict] = []
    for item in items:
        if item.get("candidate_type") in FORCED_BOUNDARY_TYPES and not FORCED_BOUNDARY_STUDIO_MOVE:
            kept.append(item)
            continue
        # match_studio_logo accepts full-frame logo cards only (studio_logo_eligible).
        match = (
            match_studio_logo(root, item, memory["records"])
            if item.get("decision") is None else None
        )
        if match is None:
            # Display only: how close the picture came, so a card that stays
            # required says why (Tập 12: best pHash 0.656, grid 197).
            compared = (
                compare_studio_logo(root, item, memory["records"])
                if item.get("decision") is None else None
            )
            if compared is not None:
                item["studio_logo_compared"] = compared
            kept.append(item)
            continue
        evidence = full_frame_logo_ad_evidence(item, items, scan_payloads)
        unknown = unconfirmed_window_texts(item, scan_payloads, match.get("known_texts") or [])
        if evidence is None or unknown is None or evidence or unknown:
            item["studio_logo_match_blocked"] = {
                "similarity": match["similarity"],
                "memory_key": match.get("memory_key"),
                "text_scan_covered": evidence is not None and unknown is not None,
                "ad_evidence": list(evidence or []),
                "unconfirmed_texts": list(unknown or [])[:10],
            }
            kept.append(item)
            continue
        routed = dict(item)
        routed["advisory"] = True
        routed["suggested_decision"] = "KEEP"
        routed["studio_logo_match"] = match
        # A schema-2 record ignored the watermark regions the user blurred; say so.
        overlay = (
            "không có lớp phủ lạ ngoài vùng watermark đã làm mờ"
            if match.get("masked_regions") else "không có lớp phủ lạ"
        )
        routed["reasons"] = list(dict.fromkeys(list(item.get("reasons") or []) + [
            f"Khớp logo hãng phim bạn đã xác nhận giữ (giống {match['similarity']:.2f}, "
            f"ngưỡng {STUDIO_LOGO_MINIMUM_SIMILARITY:.2f}; {overlay}); không thấy chữ lạ "
            "hay quảng cáo — chuyển sang Ứng viên phụ, không chặn xuất",
        ]))
        moved.append(routed)
    return kept, moved


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
        "promotional_segment", *GATE_PROTECTED_TYPES,
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


# Fixed on-screen text (job 40 "Nhất Âu Xuân - Tập 10", 2026-10-02). A site
# watermark that OCR read in 865 of 869 frames ("Motchillv.ph") was routed as
# scene text because it is short and scored low as an advertisement, and a faint
# "PHIM ĐƯỢC CẬP NHẬT NHANH NHẤT TẠI MOTCHILLV.PH" line was only read in pieces,
# so the export blurred neither. Text that stays at one place for most of the
# film is a watermark candidate whatever its semantic score: it becomes one
# persistent-overlay card for its own box. The reviewer still decides; BLUR is
# only suggested with ad evidence (ad routing, a web address, a visual-logo
# confirmation of the same box, or the site name of another such watermark).
FIXED_TEXT_MINIMUM_COVERAGE = 0.50  # share of the scanned time one track was read
FIXED_TEXT_MINIMUM_SPAN_SECONDS = 60.0
FIXED_TEXT_WHOLE_FILM_SPAN = 0.50  # observed span share from which a card covers the whole scan
RECURRING_TEXT_MINIMUM_TRACKS = 8
RECURRING_TEXT_MINIMUM_LONG_READINGS = 3
RECURRING_TEXT_LONG_READING_CHARS = 12
RECURRING_TEXT_FRAGMENT_CHARS = 6
RECURRING_TEXT_FRAGMENT_SIMILARITY = 0.75
RECURRING_TEXT_MINIMUM_SPAN_SHARE = 0.30
FIXED_TEXT_VISUAL_OVERLAP = 0.60
_FIXED_TEXT_SKIPPED_ROUTINGS = {
    "REVIEW_PERSISTENT_OVERLAY", "REVIEW_POLICY_OVERRIDE", "LIKELY_TITLE_OVERLAY",
}
_FIXED_TEXT_AD_ROUTINGS = {"REVIEW_AD_LIKELY", "REVIEW_POLICY_OVERRIDE"}
_FIXED_TEXT_DOMAIN = re.compile(r"\b[a-z0-9][a-z0-9-]{2,}\.[a-z]{2,6}\b", re.IGNORECASE)
_FIXED_TEXT_SITE_TOKEN_CHARS = 6


def _fold_text(value: str) -> str:
    value = str(value).casefold().replace("đ", "d")
    return "".join(
        character for character in unicodedata.normalize("NFD", value)
        if unicodedata.category(character) != "Mn"
    )


def _fold_alnum(value: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", _fold_text(value))


def _fragment_similarity(fragment: str, text: str) -> float:
    """How well ``fragment`` matches some same-length window of ``text``."""
    if not fragment or not text:
        return 0.0
    if fragment in text:
        return 1.0
    size = len(fragment)
    matcher = difflib.SequenceMatcher(None, autojunk=False)
    matcher.set_seq1(fragment)
    if size >= len(text):
        matcher.set_seq2(text)
        return matcher.ratio()
    best = 0.0
    for start in range(len(text) - size + 1):
        matcher.set_seq2(text[start:start + size])
        best = max(best, matcher.ratio())
    return best


def _fixed_text_ad_evidence(tracks: list[dict]) -> list[str]:
    evidence = []
    for track in tracks:
        routing = str(track.get("routing") or "")
        if routing in _FIXED_TEXT_AD_ROUTINGS:
            evidence.append(f"OCR định tuyến quảng cáo ({routing})")
        if float(track.get("ad_probability") or 0.0) >= 0.45:
            evidence.append(f"điểm quảng cáo {float(track['ad_probability']):.2f}")
        for text in track.get("sample_text") or []:
            if _FIXED_TEXT_DOMAIN.search(str(text)) or any(
                pattern.search(str(text)) for pattern in _AD_TEXT_PATTERNS
            ):
                evidence.append(f"địa chỉ web/liên hệ: {text}")
        # A whole-film platform mark (WeTV, iQIYI) is a third-party watermark.
        platform = match_platform_texts(track.get("sample_text") or [])
        if platform is not None:
            evidence.append(f"tên nền tảng video: {platform['name']}")
    return list(dict.fromkeys(evidence))[:5]


def _site_tokens(tracks: list[dict]) -> set[str]:
    """Folded words of a watermark's web address ("motchillv" in "Motchillv.ph")."""
    tokens = set()
    for track in tracks:
        for text in track.get("sample_text") or []:
            for match in _FIXED_TEXT_DOMAIN.finditer(str(text)):
                for word in re.split(r"[^0-9a-z]+", _fold_text(match.group(0))):
                    if len(word) >= _FIXED_TEXT_SITE_TOKEN_CHARS:
                        tokens.add(word)
    return tokens


def _vertical_band_overlap(first: list[float], second: list[float]) -> bool:
    shared = min(first[3], second[3]) - max(first[1], second[1])
    return shared >= 0.5 * min(first[3] - first[1], second[3] - second[1])


def _recurring_text_groups(tracks: list[dict]) -> list[list[dict]]:
    """Tracks reading pieces of one line of text at one place (union-find)."""
    entries = []
    for track in tracks:
        box = track.get("union_box")
        if (
            str(track.get("routing") or "") in _FIXED_TEXT_SKIPPED_ROUTINGS
            or track.get("candidate_type") == "persistent_overlay"
            or not isinstance(box, list) or len(box) != 4
        ):
            continue
        texts = [_fold_alnum(text) for text in track.get("sample_text") or []]
        texts = [
            (text, Counter(text)) for text in texts
            if len(text) >= RECURRING_TEXT_FRAGMENT_CHARS
        ]
        if texts:
            entries.append((track, [float(value) for value in box], texts))
    parent = list(range(len(entries)))

    def related(first_texts: list, second_texts: list) -> bool:
        for one, one_counts in first_texts:
            for other, other_counts in second_texts:
                (short, short_counts), (long, long_counts) = sorted(
                    ((one, one_counts), (other, other_counts)), key=lambda value: len(value[0]),
                )
                # Shared letters bound any window's ratio; skip hopeless pairs cheaply.
                shared = sum((short_counts & long_counts).values())
                if (
                    shared >= RECURRING_TEXT_FRAGMENT_SIMILARITY * len(short)
                    and _fragment_similarity(short, long) >= RECURRING_TEXT_FRAGMENT_SIMILARITY
                ):
                    return True
        return False

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for first in range(len(entries)):
        _, first_box, first_texts = entries[first]
        for second in range(first + 1, len(entries)):
            _, second_box, second_texts = entries[second]
            if (
                find(first) == find(second)
                or not _vertical_band_overlap(first_box, second_box)
                or min(first_box[2], second_box[2]) <= max(first_box[0], second_box[0])
            ):
                continue
            if related(first_texts, second_texts):
                parent[find(first)] = find(second)
    components: dict[int, list] = {}
    for index, entry in enumerate(entries):
        components.setdefault(find(index), []).append(entry)
    return [
        [track for track, _, _ in members]
        for members in components.values()
        if len(members) >= RECURRING_TEXT_MINIMUM_TRACKS
        and sum(
            1 for _, _, texts in members
            if max(len(text) for text, _ in texts) >= RECURRING_TEXT_LONG_READING_CHARS
        ) >= RECURRING_TEXT_MINIMUM_LONG_READINGS
    ]


def promote_fixed_text_overlays(payload: dict) -> dict:
    """Turn text that stays at one place for most of the film into one track card.

    Rule ``whole_film_text``: one persistent OCR track read in at least half of
    the scanned time. Rule ``recurring_fixed_text``: at least eight tracks that
    read pieces of the same line at the same place over 30% of the scan (a faint
    line OCR only reads now and then). Their tracks are replaced by one
    ``persistent_overlay`` track; nothing else changes. The payload is returned
    unchanged (the same object) when no rule applies.
    """
    tracks = [track for track in payload.get("tracks") or [] if isinstance(track, dict)]
    duration = float(payload.get("duration_seconds") or 0.0)
    scan_start = float(payload.get("scan_start_seconds") or 0.0)
    scan_duration = float(payload.get("scan_duration_seconds") or duration)
    sample_every = float(payload.get("sample_every_seconds") or 0.0)
    if not tracks or scan_duration <= 0:
        return payload
    scan_end = min(duration, scan_start + scan_duration) if duration > 0 else scan_start + scan_duration
    promoted: list[dict] = []
    absorbed: set[int] = set()

    def build(base: dict, members: list[dict], rule: str, coverage: float | None) -> dict:
        start = min(float(track["start_seconds"]) for track in members)
        end = max(
            float(track.get("recommended_blur_end_seconds", track["end_seconds"]))
            for track in members
        )
        whole_film = end - start >= FIXED_TEXT_WHOLE_FILM_SPAN * scan_duration
        boxes = [[float(value) for value in track["union_box"]] for track in members]
        texts = list(dict.fromkeys(
            str(text) for track in sorted(members, key=lambda value: -len(
                max((str(text) for text in value.get("sample_text") or []), key=len, default="")
            ))
            for text in track.get("sample_text") or []
        ))
        merged = dict(base)
        merged.update({
            "start_seconds": round(scan_start if whole_film else start, 3),
            "end_seconds": round(scan_end if whole_film else end, 3),
            "recommended_blur_end_seconds": round(scan_end if whole_film else end, 3),
            "union_box": [
                round(min(box[0] for box in boxes)), round(min(box[1] for box in boxes)),
                round(max(box[2] for box in boxes)), round(max(box[3] for box in boxes)),
            ],
            "sample_text": texts[:8],
            "observations": sum(int(track.get("observations") or 0) for track in members),
            "persistent": True,
            "review_candidate": True,
            "review_priority": "high",
            "routing": "REVIEW_PERSISTENT_OVERLAY",
            "candidate_type": "persistent_overlay",
            "grouped_track_ids": [int(track["track_id"]) for track in members if "track_id" in track],
            "automatic_edit": False,
            "fixed_text_overlay": {
                "rule": rule,
                "observed_start_seconds": round(start, 3),
                "observed_end_seconds": round(end, 3),
                "whole_film": whole_film,
                "track_count": len(members),
                "coverage": round(coverage, 4) if coverage is not None else None,
                "ad_evidence": _fixed_text_ad_evidence(members),
            },
        })
        return merged

    for track in tracks:
        if (
            not track.get("persistent")
            or str(track.get("routing") or "") in _FIXED_TEXT_SKIPPED_ROUTINGS
            or track.get("candidate_type") == "persistent_overlay"
            or not isinstance(track.get("union_box"), list) or len(track["union_box"]) != 4
        ):
            continue
        span = float(track["end_seconds"]) - float(track["start_seconds"])
        coverage = int(track.get("observations") or 0) * sample_every / scan_duration
        if (
            coverage >= FIXED_TEXT_MINIMUM_COVERAGE
            and span >= max(FIXED_TEXT_MINIMUM_SPAN_SECONDS, FIXED_TEXT_WHOLE_FILM_SPAN * scan_duration)
        ):
            promoted.append(build(track, [track], "whole_film_text", min(1.0, coverage)))
            absorbed.add(id(track))
    remaining = [track for track in tracks if id(track) not in absorbed]
    for members in _recurring_text_groups(remaining):
        start = min(float(track["start_seconds"]) for track in members)
        end = max(float(track["end_seconds"]) for track in members)
        if end - start < max(
            FIXED_TEXT_MINIMUM_SPAN_SECONDS, RECURRING_TEXT_MINIMUM_SPAN_SHARE * scan_duration,
        ):
            continue
        anchor = max(members, key=lambda value: (
            len(max((_fold_alnum(text) for text in value.get("sample_text") or []), key=len, default="")),
            -float(value["start_seconds"]),
        ))
        promoted.append(build(anchor, members, "recurring_fixed_text", None))
        absorbed.update(id(track) for track in members)
    if not promoted:
        return payload
    # A faint line naming the site of a watermark found above carries that
    # watermark's ad evidence ("TẠI MOTCHILLV PH" next to "Motchillv.ph").
    for overlay in promoted:
        details = overlay["fixed_text_overlay"]
        if details["ad_evidence"]:
            continue
        folded = [_fold_alnum(text) for text in overlay.get("sample_text") or []]
        for other in promoted:
            if other is overlay or not other["fixed_text_overlay"]["ad_evidence"]:
                continue
            shared = sorted(
                token for token in _site_tokens([other])
                if any(token in text for text in folded)
            )
            if shared:
                details["ad_evidence"] = [f"cùng tên trang với watermark {shared[0]}"]
                break
    for overlay in promoted:
        details = overlay["fixed_text_overlay"]
        overlay["suggested_decision"] = "BLUR" if details["ad_evidence"] else None
        place = (
            "suốt video" if details["whole_film"]
            else f"{_clock_text(details['observed_start_seconds'])}–"
                 f"{_clock_text(details['observed_end_seconds'])}"
        )
        overlay["reason"] = (
            ("Chữ cố định một chỗ, OCR đọc được ở "
             f"{details['coverage']:.0%} thời gian quét" if details["rule"] == "whole_film_text"
             else f"Cùng một dòng chữ mờ ở một chỗ, OCR đọc được {details['track_count']} lần")
            + f" ({place}); giống watermark/logo trang web. "
            + (
                "Bằng chứng quảng cáo: " + "; ".join(details["ad_evidence"])
                + " — đề xuất làm mờ vùng này, chờ bạn duyệt"
                if details["ad_evidence"] else
                "Chưa có bằng chứng quảng cáo — bạn tự quyết làm mờ hay giữ"
            )
        )
    output = dict(payload)
    output["tracks"] = [
        track for track in payload.get("tracks") or []
        if not isinstance(track, dict) or id(track) not in absorbed
    ] + promoted
    output["fixed_text_overlays"] = [
        {
            "track_id": overlay.get("track_id"),
            "grouped_track_ids": overlay["grouped_track_ids"],
            **overlay["fixed_text_overlay"],
        }
        for overlay in promoted
    ]
    return output


def _clock_text(seconds: float) -> str:
    total = max(0, int(round(float(seconds))))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def corroborate_fixed_text_overlays(items: list[dict]) -> list[dict]:
    """A visual-logo confirmation of the same box is ad evidence for a fixed text card."""
    for item in items:
        details = item.get("fixed_text_overlay")
        region = item.get("suggested_region_source_pixels")
        if (
            not isinstance(details, dict)
            or item.get("category") != "text"
            or item.get("decision") is not None
            or item.get("suggested_decision") is not None
            or not isinstance(region, dict)
        ):
            continue
        confirmations = [
            other for other in items
            if other.get("category") == "visual_logo"
            and (other.get("model_evidence") or {}).get("vlm_confirmation") == "CONFIRMED"
            and isinstance(other.get("suggested_region_source_pixels"), dict)
            and _intersection_over_smaller(region, other["suggested_region_source_pixels"])
            >= FIXED_TEXT_VISUAL_OVERLAP
            and float(other["start_seconds"]) < float(item["end_seconds"])
            and float(other["end_seconds"]) > float(item["start_seconds"])
        ]
        if not confirmations:
            continue
        details["ad_evidence"] = [
            f"visual-logo xác nhận logo tại cùng vùng ở {len(confirmations)} đoạn"
        ]
        item["suggested_decision"] = "BLUR"
        item["reasons"] = list(dict.fromkeys(list(item.get("reasons") or []) + [
            f"Visual-logo xác nhận logo tại đúng vùng này ở {len(confirmations)} đoạn — "
            "đề xuất làm mờ toàn bộ thời gian của thẻ, chờ bạn duyệt"
        ]))
    return items


EVIDENCE_REGION_COVERED_OVERLAP = 0.50
_COVERED_BY_LABEL_CHARACTERS = 60


def link_full_scene_logo_evidence(
    items: list[dict], extra_items: Iterable[dict] = (),
) -> list[dict]:
    """Display only: name the watermark card that already owns an evidence box.

    A full-scene logo card (``evidence_regions``) whose box overlaps a
    persistent-overlay card of the same time by at least half of the smaller
    box gets ``covered_by`` / ``covered_by_label`` on that box, so the page can
    say the box is the site watermark with its own card. Owners are main-list
    cards; ``extra_items`` (optional cards) are only annotated. Run after the
    final ID assignment: every ID changes there. Nothing else is touched.
    """
    owners = [
        item for item in items
        if item.get("category") in {"visual_logo", "text"}
        and item.get("candidate_type") in {"persistent_overlay", PLATFORM_LOGO_TYPE}
        and isinstance(item.get("suggested_region_source_pixels"), dict)
    ]
    for item in [*items, *extra_items]:
        boxes = item.get("evidence_regions")
        if not isinstance(boxes, list):
            continue
        for box in boxes:
            if not isinstance(box, dict):
                continue
            box.pop("covered_by", None)
            box.pop("covered_by_label", None)
            for owner in owners:
                if (
                    owner is item
                    or float(owner["start_seconds"]) >= float(item["end_seconds"])
                    or float(owner["end_seconds"]) <= float(item["start_seconds"])
                    or _intersection_over_smaller(box, owner["suggested_region_source_pixels"])
                    < EVIDENCE_REGION_COVERED_OVERLAP
                ):
                    continue
                # The owner's first label is often the scanner's own
                # "Persistent external logo / watermark"; show its reading.
                label = _specific_label(owner.get("labels"))
                box["covered_by"] = owner.get("id")
                box["covered_by_label"] = label[:_COVERED_BY_LABEL_CHARACTERS]
                break
    return items


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
                **({"fixed_text_overlay": {
                    key: list(value) if isinstance(value, list) else value
                    for key, value in track["fixed_text_overlay"].items()
                }} if isinstance(track.get("fixed_text_overlay"), dict) else {}),
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


# Lines of one text block sit at most about two line heights apart (Troy's
# opening narration); a corner watermark and a bottom line sit five or more.
TEXT_MERGE_MAXIMUM_LINE_GAP = 3.0


def _text_regions_adjacent(first: dict | None, second: dict | None) -> bool:
    if not isinstance(first, dict) or not isinstance(second, dict):
        return True
    gap = max(int(first["y"]), int(second["y"])) - min(
        int(first["y"]) + int(first["height"]), int(second["y"]) + int(second["height"]),
    )
    return gap <= TEXT_MERGE_MAXIMUM_LINE_GAP * min(int(first["height"]), int(second["height"]))


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
        if previous is not None and previous["category"] == item["category"] == "text":
            # Texts far apart on the frame are separate decisions: their union box
            # would blur everything between them (a corner watermark and a bottom
            # line became one 839x483 box on 2026-10-02).
            visual_regions_compatible = _text_regions_adjacent(
                previous.get("suggested_region_source_pixels"),
                item.get("suggested_region_source_pixels"),
            )
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
            if item.get("evidence_regions"):
                # Display only: keep every window's evidence boxes once.
                boxes = [dict(value) for value in current.get("evidence_regions") or []]
                for box in item["evidence_regions"]:
                    if not any(
                        all(box.get(key) == other.get(key) for key in ("x", "y", "width", "height"))
                        for other in boxes
                    ):
                        boxes.append(dict(box))
                current["evidence_regions"] = boxes
                current.setdefault("evidence_frame_size", item.get("evidence_frame_size"))
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
            or item.get("candidate_type") in GATE_PROTECTED_TYPES
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
                *GATE_PROTECTED_TYPES,
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
    """Isolated plan, output and job-state paths of the export of the current decisions.

    Named by what the render applies (export_identity.export_paths): another
    blur edge mode or detected intervals get their own output. An unfinished
    review keeps the older decision hash.
    """
    return export_paths(root, queue, queue_render_identity(queue))


def queue_render_identity(queue: dict) -> list[dict] | None:
    """render_identity of the queue's edit-plan operations; None while they cannot be built."""
    try:
        return render_identity(approved_operations(queue))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def existing_review_export(root: Path, queue: dict) -> tuple[Path, dict] | None:
    """(output, manifest) of the export of the current decisions already on disk.

    Only a file its manifest proves (export_identity.manifest_problem), under
    its own name or the legacy name of an export made before the operations
    hash; None otherwise.
    """
    return verified_export(root, queue, queue_render_identity(queue))


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


def _verified_report_alias(root: Path, reports_root: Path, payload: dict) -> str | None:
    """Scan report a verified 18+ copy was made from, once proven unchanged.

    A verified copy whose scan.json changed afterwards would show stale
    intervals, so it is refused instead of silently reviewed.
    """
    verification = payload.get("adult_verification")
    if not isinstance(verification, dict):
        return None
    relative = str(verification.get("source_report") or "").replace("\\", "/")
    expected = str(verification.get("source_report_sha256") or "")
    if not relative or not expected:
        return None
    try:
        source = _inside(reports_root, root / relative, "Verified source report")
    except ValueError:
        return None
    if not source.is_file():
        return None
    if _sha256(source) != expected:
        raise ValueError(
            f"18+ verification is stale: {relative} changed after verify-adult; "
            "run verify-adult again"
        )
    return _relative(root, source)


BENCHMARK_MARKER_NAME = ".biliflow-benchmark"
BENCHMARK_ENVIRONMENT_VARIABLE = "BILIFLOW_BENCHMARK_CACHE"


def studio_logo_memory_allowed(queue_path: Path, environment: dict | None = None) -> bool:
    """Whether a production queue build may use the user's studio-logo memory.

    Benchmark trials (``scripts/benchmark_full_advertising.py``, Golden ``run``)
    set ``BILIFLOW_BENCHMARK_CACHE`` for every stage and mark their report folder
    with ``.biliflow-benchmark``; their queues must not depend on user state.
    """
    environment = os.environ if environment is None else environment
    if str(environment.get(BENCHMARK_ENVIRONMENT_VARIABLE) or "").strip():
        return False
    folder = Path(queue_path).resolve().parent
    for directory in (folder, *folder.parents):
        if (directory / BENCHMARK_MARKER_NAME).exists():
            return False
        if directory.name == "reports":
            break
    return True


def _source_frame_size(payloads: dict[str, dict]) -> tuple[int, int] | None:
    """The source frame size a report states (text scans and fixtures carry ``source_size``)."""
    for payload in payloads.values():
        sizes = [payload.get("source_size")] + [
            (interval.get("region_localization") or {}).get("frame_size")
            for interval in payload.get("intervals") or [] if isinstance(interval, dict)
        ]
        for size in sizes:
            if isinstance(size, list) and len(size) == 2 and all(
                isinstance(value, (int, float)) and value > 0 for value in size
            ):
                return int(size[0]), int(size[1])
    return None


def _add_platform_and_ending_cards(
    root: Path, items: list[dict], advisory_items: list[dict], payloads: dict[str, dict], *,
    source_path: Path, duration: float, queue_dir: Path, memory: dict | None, ffmpeg_path: Path,
) -> dict:
    """Platform-ident BLUR cards (batch 4a) and the forced last-6-s card, added to ``items``."""
    probe = SourceProbe(source_path, ffmpeg_path)
    frame_size = _source_frame_size(payloads)
    regions = overlay_regions([*items, *advisory_items], frame_size)
    runs: list[dict] = []
    memory_summary = None
    records = platform_memory_records(memory) if memory is not None else []
    if records:
        info = probe.info()
        if info is not None:
            regions = overlay_regions([*items, *advisory_items], tuple(info["source_size"]))
        runs, memory_summary = platform_memory_runs(
            root, records, probe, duration=duration, queue_regions=regions,
        )
    cards, summary = build_platform_cards(
        root, items, advisory_items, payloads, source=source_path, duration=duration,
        frame_size=frame_size, queue_dir=queue_dir, masks=None, memory_runs=runs, probe=probe,
    )
    items.extend(cards)
    ending = ensure_forced_ending_card(
        root, items, payloads, duration=duration, queue_dir=queue_dir, probe=probe,
    )
    if ending is not None:
        items.append(ending)
    summary.update(
        memory_used=memory is not None, memory_records=len(records), memory=memory_summary,
        ending_card=ending is not None, probe_reason=probe.reason,
    )
    return summary


def build_review_queue(
    *, project_root: Path, report_paths: list[Path], queue_path: Path,
    merge_gap_seconds: float = 1.0,
    selected_detectors: list[str] | None = None,
    content_style: str | None = None,
    adult_triage_level: str | None = None,
    use_studio_logo_memory: bool = False,
    ffmpeg_path: Path | None = None,
    gore_triage_level: str | None = None,
) -> dict:
    """Build one review queue from scan reports.

    ``content_style`` is the job's confirmed style; only ``live_action`` lets
    ``triage_adult_items`` move weak 18+ candidates to the optional list. Callers
    that omit it (older scripts, benchmarks) get the queue without that triage.
    ``adult_triage_level`` overrides ``ADULT_TRIAGE_LEVEL`` for measurements.
    ``gore_triage_level`` overrides ``GORE_TRIAGE_LEVEL`` (off by default); only
    an ``animation`` job lets ``triage_anime_gore_items`` move gore cards.
    ``use_studio_logo_memory`` routes cards repeating a user-confirmed studio
    logo (``state/studio-logo-memory.json``) to the optional list. It is off by
    default so benchmarks and Golden measurements never depend on user state;
    the production ``build-review`` command turns it on outside benchmark trials
    (``studio_logo_memory_allowed``); remembered platform logos match only then.
    ``ffmpeg_path`` (default ``tools/ffmpeg/bin/ffmpeg.exe``) decodes the short
    windows of platform idents and of the forced ending card; without it those
    cards keep their OCR bounds.
    """
    if content_style is not None and content_style not in CONTENT_STYLES:
        raise ValueError(f"Unknown content style: {content_style}")
    adult_triage_level = normalize_adult_triage_level(adult_triage_level)
    gore_triage_level = normalize_gore_triage_level(gore_triage_level)
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
    scan_payloads: dict[str, dict] = {}
    # Text reports after the fixed-overlay promotion: a platform name inside a
    # fixed watermark stays with that watermark's card.
    platform_payloads: dict[str, dict] = {}
    detector_coverage_by_report: dict[str, dict] = {}
    for candidate in report_paths:
        report_path = _inside(reports_root, candidate, "Report path")
        report_path = report_path.resolve(strict=True)
        payload = _read_json(report_path)
        if payload.get("status") not in {"COMPLETED", "REVIEW_REQUIRED"}:
            raise ValueError(f"Report is not reviewable: {report_path}")
        verification_alias = _verified_report_alias(root, reports_root, payload)
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
        scan_payloads[relative_report] = payload
        if verification_alias:
            # Unresolved items preserved from a queue built on the unverified
            # scan cite scan.json; the verified copy holds the same intervals.
            scan_payloads.setdefault(verification_alias, payload)
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
            text_payload = promote_fixed_text_overlays(payload)
            platform_payloads[relative_report] = text_payload
            title_references.extend(_title_overlay_references(text_payload))
            in_film_text_references.extend(_in_film_text_references(text_payload))
            items.extend(_text_items(root, report_path, text_payload))
            advisory_items.extend(_advisory_text_items(root, report_path, text_payload))
        else:
            platform_payloads[relative_report] = payload
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
    items = corroborate_fixed_text_overlays(items)
    items = reconcile_persistent_overlay_items(
        items, source_duration=source_duration,
    )
    items, quarantined_visual_items = _quarantine_uncorroborated_visual_regions(items)
    advisory_items.extend(quarantined_visual_items)
    studio_memory: dict = {"records": []}
    if use_studio_logo_memory:
        try:
            studio_memory = load_studio_logo_memory(root)
        except (OSError, ValueError) as error:
            # An unreadable memory only means nothing moves: every card stays required.
            studio_memory = {"records": [], "error": str(error)}
    # Platform idents and the forced ending card join after the gates above
    # (they are never quarantined) and before preserved cards are restored.
    platform_summary = _add_platform_and_ending_cards(
        root, items, advisory_items, platform_payloads,
        source_path=source_path, duration=source_duration, queue_dir=queue_path.parent,
        memory=studio_memory if use_studio_logo_memory else None,
        ffmpeg_path=ffmpeg_path or root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
    )
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
    # Opening studio idents: no pre-selected CUT without ad evidence (R3a), and a
    # card repeating a user-confirmed studio logo goes to the optional list (R3b).
    items = withhold_studio_ident_suggestions(items, scan_payloads)
    items, studio_logo_items = route_confirmed_studio_logos(
        root, items, scan_payloads, studio_memory,
    )
    advisory_items.extend(studio_logo_items)
    # After preserved items are restored and re-checked, before ordering: a
    # moved item must not be promoted, and a decided one is never moved.
    items, triaged_adult_items, adult_triage = triage_adult_items(
        items, scan_payloads, content_style, adult_triage_level,
    )
    advisory_items.extend(triaged_adult_items)
    # Anime gore (docs/ANIME_GORE_PLAN.md): every gore card shows what the tagger
    # saw; rule C1 may then move undecided cards with neither blood nor corpse.
    items = annotate_gore_tag_evidence(items, scan_payloads)
    advisory_items = annotate_gore_tag_evidence(advisory_items, scan_payloads)
    items, triaged_gore_items, gore_triage = triage_anime_gore_items(
        items, scan_payloads, content_style, gore_triage_level,
    )
    advisory_items.extend(triaged_gore_items)
    items = promote_strong_adult_priorities(items)
    items = sorted(items, key=lambda item: (
        _PRIORITY_RANK.get(item["priority"], 2),
        item["start_seconds"], item["category"],
    ))
    ensure_unique_review_item_ids([*items, *advisory_items])
    # After every reconciliation (preserved items included) and the final IDs.
    link_platform_cards(items, advisory_items)
    link_full_scene_logo_evidence(items, advisory_items)
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
        "content_style": content_style,
        "adult_triage": adult_triage,
        "gore_triage": gore_triage,
        "scene_cards": {
            "categories": dict(SCENE_CARD_KINDS),
            "maximum_gap_seconds": SCENE_CARD_MAXIMUM_GAP_SECONDS,
            "maximum_span_seconds": SCENE_CARD_MAXIMUM_SPAN_SECONDS,
            "cards": sum(1 for item in items if item.get("scene_card")),
            "applies_to": "detected_intervals_only",
        },
        "platform_logos": platform_summary,
        "studio_logo_memory": {
            "used": bool(use_studio_logo_memory),
            "confirmed_logos": sum(
                1 for record in studio_memory.get("records") or []
                if isinstance(record, dict) and record.get("memory_class", "studio_logo") == "studio_logo"
            ),
            "minimum_similarity": STUDIO_LOGO_MINIMUM_SIMILARITY,
            "maximum_cell_difference": STUDIO_LOGO_MAXIMUM_CELL_DIFFERENCE,
            "moved_to_candidates": len(studio_logo_items),
            "kept_required_after_picture_match": sum(
                1 for item in items if item.get("studio_logo_match_blocked")
            ),
            "error": studio_memory.get("error"),
        },
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
    remember_studio_logo: bool = False,
    remember_platform_logo: bool = False,
) -> dict:
    """Record one human decision.

    ``remember_studio_logo`` is the explicit "Đây là logo hãng phim — giữ & nhớ"
    action: it requires KEEP on a full-frame logo card and also stores the card's
    preview signatures and the OCR lines of its window in the separate
    studio-logo memory. An unreadable studio-logo memory refuses that action
    before anything is written. Any other decision on that card removes its
    studio-logo record again; that clean-up never fails the decision.
    The record (schema 2) also stores every frame of the card's window and
    ignores the watermark cards of this source the user decided BLUR; a later
    decision on such a watermark card updates those records the same quiet way.
    ``remember_platform_logo`` is "Đây là logo nền tảng — làm mờ & nhớ" (batch 4a):
    BLUR on a platform-logo card or a full-frame logo card, stored as a
    ``platform_logo`` record of the same memory. On a full-frame card the BLUR
    region is the logo box found in the window and the interval tightens to
    the ident (the original stays in ``detected_interval``). Any other
    decision forgets a record of either class. Both remember actions are
    stored before the decision itself; nothing remembered is ever deleted (the
    memory is backed up and old frames move to state/backups).
    """
    root = project_root.resolve(strict=True)
    queue_path = _inside((root / "reports").resolve(strict=True), queue_path, "Queue path")
    payload = _read_json(queue_path.resolve(strict=True))
    decision = decision.upper()
    if decision not in DECISIONS:
        raise ValueError(f"Decision must be one of {', '.join(DECISIONS)}")
    if remember_platform_logo and remember_studio_logo:
        raise ValueError("Chỉ chọn một: logo hãng phim (giữ & nhớ) hoặc logo nền tảng (làm mờ & nhớ)")
    if remember_studio_logo and decision != "KEEP":
        raise ValueError("Chỉ có thể ghi nhớ logo hãng phim khi chọn Giữ nguyên")
    if remember_platform_logo and decision != "BLUR":
        raise ValueError("Chỉ có thể ghi nhớ logo nền tảng khi chọn Làm mờ")
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
    studio_signatures = None
    studio_window_text = None
    studio_frames = None
    if remember_studio_logo:
        if not studio_logo_eligible(item):
            raise ValueError("Chỉ ghi nhớ được logo hãng phim cho thẻ logo toàn khung hình")
        studio_signatures = studio_logo_signatures(root, item)
        if not studio_signatures:
            raise ValueError("Không đọc được ảnh xem trước của thẻ này để ghi nhớ logo hãng phim")
        try:
            load_studio_logo_memory(root)  # fail before the decision is written
        except (OSError, ValueError) as error:
            raise ValueError(
                f"Không đọc được bộ nhớ logo hãng phim ({STUDIO_LOGO_MEMORY_PATH.as_posix()}); "
                f"chưa ghi quyết định: {error}"
            ) from error
        studio_window_text = _queue_window_text(root, payload, item)
        # Schema 2 (user decision 2026-10-02): every frame of the window, decoded again
        # exactly like the scanner (about 0.3 s for 5 s), with the watermark regions the
        # user blurred ignored. Falls back to the previews alone, never raises.
        studio_frames = prepare_studio_logo_frames(
            root, payload, item,
            studio_logo_window_frames(root, payload, item, root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"),
        )
        if not studio_frames["frames"]:
            raise ValueError(
                "Ảnh của thẻ này gần như một màu (đen/mờ dần), không có hình logo để ghi nhớ; "
                "chưa ghi quyết định"
            )
        if note is None:
            note = "Người duyệt xác nhận đây là logo hãng phim — giữ nguyên và ghi nhớ"
    platform_frames = None
    platform = None
    if remember_platform_logo:
        platform_frames, platform, region, start_seconds, end_seconds = _prepare_platform_memory(
            root, payload, item, region=region, full_frame=full_frame,
            start_seconds=start_seconds, end_seconds=end_seconds,
        )
        if note is None:
            note = "Người duyệt xác nhận đây là logo nền tảng video — làm mờ vùng logo và ghi nhớ"
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
    item.pop("studio_logo_memory", None)
    item.pop("platform_logo_memory", None)
    if remember_platform_logo and platform_frames is not None:
        item["platform_logo_memory"] = {
            "remembered": True, "at": item["decided_at"], "platform": platform,
            "logo_frames": len(platform_frames["platform"]["logo_frame_times"]),
            "blur_region": platform_frames["platform"]["blur_region"],
            **_studio_memory_frames_summary(platform_frames),
        }
    if remember_studio_logo:
        item["studio_logo_memory"] = {
            "remembered": True, "at": item["decided_at"],
            "frames": len((studio_frames or {}).get("frames") or studio_signatures or []),
            "text_scan_covered": bool((studio_window_text or {}).get("covered")),
            "window_texts": list((studio_window_text or {}).get("texts") or [])[:20],
        }
        if studio_frames is not None:
            item["studio_logo_memory"].update(_studio_memory_frames_summary(studio_frames))
    payload["updated_at"] = _now()
    payload["status"] = _queue_status(payload["items"])
    payload["counts"] = _counts(payload["items"])
    entry = {
        "at": item["decided_at"], "action": "DECIDE", "item_id": item_id,
        "decision": decision, "actor": actor, "transport": transport,
        "original_interval": original_interval,
        "reviewed_interval": {
            "start_seconds": item["start_seconds"],
            "end_seconds": item["end_seconds"],
        },
    }
    if remember_studio_logo:
        entry["remember_studio_logo"] = True
    if remember_platform_logo:
        entry["remember_platform_logo"] = True
    payload.setdefault("audit_log", []).append(entry)
    payload["audit_log"] = payload["audit_log"][-1000:]
    if remember_studio_logo or remember_platform_logo:
        # Before the decision is saved (security review 2026-10-03): a memory another
        # writer keeps changing refuses both, and nothing remembered is ever deleted.
        _remember_logo_record(
            root, payload, item, studio=remember_studio_logo, studio_signatures=studio_signatures,
            studio_window_text=studio_window_text, studio_frames=studio_frames,
            platform_frames=platform_frames, platform=platform,
        )
    _write_json(queue_path, payload)
    remember_review_item(root, payload, item)
    if not (remember_studio_logo or remember_platform_logo):
        # Also when the flag is gone (rebuilt queue): any other decision forgets
        # the card's record, a studio or a platform logo alike.
        _forget_remembered_logo_quietly(root, payload, item_id)
    if item.get("candidate_type") == "persistent_overlay":
        _refresh_studio_logo_masks_after_write(root, queue_path, payload, entry)
    _render_queue_html(root, queue_path, payload)
    return payload


MEMORY_WRITE_ATTEMPTS = 3  # a remembered-logo write reads the memory again when another writer changed it


def _retry_memory_write(write: Callable[[], Any]) -> Any:
    """Run a remembered-logo write again, on a fresh read, after another writer changed the memory."""
    for attempt in range(1, MEMORY_WRITE_ATTEMPTS + 1):
        try:
            return write()
        except MemoryChanged:
            if attempt == MEMORY_WRITE_ATTEMPTS:
                raise
    return None


def _remember_logo_record(
    root: Path, queue: dict, item: dict, *, studio: bool, studio_signatures: list | None,
    studio_window_text: dict | None, studio_frames: dict | None, platform_frames: dict | None,
    platform: dict | None,
) -> None:
    """Store "giữ & nhớ" / "làm mờ & nhớ"; ValueError (nothing saved) when the memory keeps changing.

    The card's old record of either class is backed up and its frames moved to
    state/backups first; brand_memory would delete them when it replaces a
    studio record, and its new frames go to a folder nothing uses yet.
    """
    try:
        if studio:
            _retry_memory_write(lambda: forget_remembered_logo(root, queue, item["id"]))
            remember_studio_logo_record(
                root, queue, item, studio_signatures, window_text=studio_window_text,
                frames=in_fresh_folder(root, studio_frames) if studio_frames is not None else None,
            )
        else:
            _retry_memory_write(lambda: remember_platform_logo_record(
                root, queue, item, platform_frames, platform=platform))
    except MemoryChanged as error:
        raise ValueError(str(error)) from error


def _item_frame_size(root: Path, queue: dict, item: dict) -> list[int] | None:
    """The source frame size of a card: its own, the queue's, else probed from the source."""
    for size in (item.get("source_frame_size"), (queue.get("source") or {}).get("frame_size")):
        if isinstance(size, list) and len(size) == 2 and all(
            isinstance(value, (int, float)) and value > 0 for value in size
        ):
            return [int(size[0]), int(size[1])]
    source = (queue.get("source") or {}).get("path")
    info = SourceProbe(Path(str(source)) if source else None,
                       root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe").info()
    return list(info["source_size"]) if info else None


def _prepare_platform_memory(
    root: Path, queue: dict, item: dict, *, region: tuple[int, int, int, int] | None,
    full_frame: bool, start_seconds: float | None, end_seconds: float | None,
) -> tuple[dict, dict | None, tuple[int, int, int, int] | None, float | None, float | None]:
    """Check and prepare "làm mờ & nhớ" before the decision is written (raises ValueError)."""
    if not platform_logo_eligible(item):
        raise ValueError(
            "Chỉ ghi nhớ được logo nền tảng cho thẻ logo nền tảng hoặc thẻ logo toàn khung hình"
        )
    try:
        load_studio_logo_memory(root)  # fail before the decision is written
    except (OSError, ValueError) as error:
        raise ValueError(
            f"Không đọc được bộ nhớ logo ({STUDIO_LOGO_MEMORY_PATH.as_posix()}); "
            f"chưa ghi quyết định: {error}"
        ) from error
    prepared = prepare_platform_logo_frames(
        root, queue, item,
        studio_logo_window_frames(root, queue, item, root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"),
    )
    details = prepared["platform"]
    if not details["logo_frame_times"]:
        raise ValueError(
            "Không thấy hình logo trên nền tối trong thẻ này để ghi nhớ logo nền tảng "
            f"({details['reason']}); chưa ghi quyết định"
        )
    known = item.get("platform_logo") if isinstance(item.get("platform_logo"), dict) else {}
    platform = platform_entry(known.get("key")) or platform_entry(
        (match_platform_texts(_queue_window_text(root, queue, item).get("texts") or []) or {}).get("key")
    ) or {"key": "unknown", "name": UNKNOWN_PLATFORM_NAME}
    if not region and not full_frame and not isinstance(item.get("suggested_region_source_pixels"), dict):
        # A full-frame card: blur the logo box found in the window, over the ident only.
        box = source_box(details["blur_region"], _item_frame_size(root, queue, item))
        if box is None:
            raise ValueError("Không xác định được kích thước khung hình để làm mờ vùng logo; chưa ghi quyết định")
        region = (box["x"], box["y"], box["width"], box["height"])
        span = details.get("span")
        if span and start_seconds is None and end_seconds is None:
            start_seconds = max(float(item["start_seconds"]), float(span["start"]))
            end_seconds = min(float(item["end_seconds"]), float(span["end"]))
            if not end_seconds > start_seconds:
                start_seconds = end_seconds = None
    return prepared, platform, region, start_seconds, end_seconds


def _studio_memory_frames_summary(frames: dict) -> dict:
    """What the review card shows about a schema-2 studio-logo record (or its prepared frames)."""
    summary = {
        "frames": len(frames.get("frames") or []),
        "frames_source": frames.get("frames_source"),
        "frames_reason": frames.get("frames_reason"),
        "window": frames.get("window"),
        "ignored_regions": [
            {"item_id": region.get("item_id"), "category": region.get("category")}
            for region in frames.get("ignored_regions") or []
        ],
        "mask_refused": bool(frames.get("mask_refused")),
    }
    if frames.get("frames_missing"):
        summary["frames_missing"] = True
    return summary


def _forget_remembered_logo_quietly(root: Path, queue: dict, item_id: str) -> None:
    """Drop a card's remembered logo (studio or platform) after a saved decision; never fails it.

    ``build_review_queue`` ignores an unreadable memory too, so no card can move
    because of the record that could not be removed here. A different decision
    on the remembered card is the user's own withdrawal of that memory; nothing
    is deleted (security review 2026-10-03): the memory is backed up and the
    record's frame JPEGs move to state/backups/studio-logo-frames-<ts>/.
    """
    try:
        _retry_memory_write(lambda: forget_remembered_logo(root, queue, item_id))
    except (OSError, ValueError, MemoryChanged):
        pass


def _refresh_studio_logo_masks_quietly(root: Path, queue: dict) -> int:
    """Re-sign remembered studio logos after a watermark decision; never fails the decision."""
    try:
        return refresh_studio_logo_masks(root, queue)
    except (OSError, ValueError):
        return 0


def _refresh_studio_logo_masks_after_write(
    root: Path, queue_path: Path, payload: dict, entry: dict,
) -> int:
    """After a saved decision on a watermark card: update the remembered logos of this source.

    BLUR adds the card's region to what those records ignore; KEEP, CUT or a
    clear removes it. When a record changed, the audit entry and the remembered
    cards of this queue say so and the queue is saved again.
    """
    refreshed = _refresh_studio_logo_masks_quietly(root, payload)
    if not refreshed:
        return 0
    entry["studio_logo_masks_refreshed"] = refreshed
    try:
        records = {
            str(record.get("key")): record for record in load_studio_logo_memory(root)["records"]
            if isinstance(record, dict) and isinstance(record.get("frames"), list)
        }
    except (OSError, ValueError):
        records = {}
    source_sha256 = str((payload.get("source") or {}).get("sha256", ""))
    for card in [*payload.get("items", []), *payload.get("advisory_items", [])]:
        for field in ("studio_logo_memory", "platform_logo_memory"):
            memory = card.get(field) if isinstance(card, dict) else None
            record = records.get(f"{source_sha256}:{card.get('id')}") if isinstance(memory, dict) else None
            if record is None:
                continue
            # The whole schema-2 summary: a record upgraded by studio-logo-upgrade
            # gets its frames_source and window on the card only here.
            memory.pop("frames_missing", None)
            memory.update(_studio_memory_frames_summary(record), mask_updated_at=record.get("mask_updated_at"))
    _write_json(queue_path, payload)
    return refreshed


def _queue_window_text(root: Path, queue: dict, item: dict) -> dict:
    """OCR lines in ``item``'s window, read from the queue's own text scans."""
    reports_root = (root / "reports").resolve(strict=True)
    payloads: dict[str, dict] = {}
    for relative in queue.get("reports") or []:
        try:
            path = _inside(reports_root, root / str(relative), "Report path").resolve(strict=True)
            payload = _read_json(path)
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and "tracks" in payload:
            payloads[str(relative)] = payload
    return studio_ident_window_text(item, payloads)



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
    item.pop("studio_logo_memory", None)
    item.pop("platform_logo_memory", None)
    payload["updated_at"] = _now()
    payload["status"] = _queue_status(payload["items"])
    payload["counts"] = _counts(payload["items"])
    entry = {
        "at": payload["updated_at"], "action": "CLEAR", "item_id": item_id,
        "actor": actor, "transport": transport,
    }
    payload.setdefault("audit_log", []).append(entry)
    payload["audit_log"] = payload["audit_log"][-1000:]
    _write_json(queue_path, payload)
    forget_review_item(root, payload, item_id)
    _forget_remembered_logo_quietly(root, payload, item_id)
    if item.get("candidate_type") == "persistent_overlay":
        _refresh_studio_logo_masks_after_write(root, queue_path, payload, entry)
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
    entry = {
        "at": decided_at, "action": "BULK_ACCEPT_SUGGESTIONS",
        "filter": review_filter, "changed_count": len(accepted),
        "accepted": accepted, "actor": actor, "transport": transport,
    }
    payload.setdefault("audit_log", []).append(entry)
    payload["audit_log"] = payload["audit_log"][-1000:]
    _write_json(queue_path, payload)
    accepted_ids = {value["item_id"] for value in accepted}
    for item in changed:
        if item.get("id") in accepted_ids:
            remember_review_item(root, payload, item)
    if any(
        item.get("id") in accepted_ids and item.get("candidate_type") == "persistent_overlay"
        for item in changed
    ):
        _refresh_studio_logo_masks_after_write(root, queue_path, payload, entry)
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
        "evidence_preview": {
            "on_demand_ffmpeg_frames": True, "uses_gpu": False,
            "encodes_video": False,
        },
    }


def _interactive_html(token: str) -> str:
    """Focus-mode review page: one item at a time, a shared player and lazy evidence.

    The page is a plain template (not an f-string) and the session token is
    substituted once. Every API path is built from ``const API='/api/'`` so the
    Control Center can rewrite it to ``/api/jobs/<id>/review/``. Rendering
    rules: polling never re-creates the focus card or resets the player; it
    only patches counts, list statuses and the side panel of the focus item
    when that item's visible state changed elsewhere.
    """
    page = r"""<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>BiliFlow Review</title><link rel="icon" href="data:,">
<style>
:root{--bg:#0d1117;--card:#161b22;--card2:#1c232d;--line:#2a323d;--text:#e9eef5;--muted:#8d99a8;--keep:#2fbf71;--blur:#f2a93b;--cut:#ef5466;--more:#7c8ba1;--violence:#ff7a45;--adult:#e05bd0;--gore:#ef5466;--ad:#4da3ff;--accent:#4da3ff;--cat:#ff7a45;--hh:112px}
*{box-sizing:border-box}html{color-scheme:dark}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
button,select,input{font:inherit}button{cursor:pointer}button:disabled{opacity:.45;cursor:not-allowed}[hidden]{display:none!important}:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
body.saving button{pointer-events:none;opacity:.65}
header{position:sticky;top:0;z-index:20;background:rgba(13,17,23,.94);backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:12px 20px}
.top{display:flex;align-items:center;gap:10px 14px;flex-wrap:wrap}h1.title{margin:0;font-size:18px;font-weight:700;min-width:0;max-width:52ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.count{color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}
.progress{flex:1;min-width:140px;height:6px;background:#232a34;border-radius:6px;overflow:hidden}.progress span{display:block;height:100%;width:0;background:var(--keep);transition:width .25s}
.back{background:transparent;border:1px solid var(--line);color:var(--muted);padding:6px 10px;border-radius:9px;font-size:13px;white-space:nowrap}.back:hover{color:var(--text);border-color:var(--muted)}
details.export{position:relative}details.export>summary{list-style:none;cursor:pointer;display:flex;gap:6px;align-items:center;border:1px solid var(--line);border-radius:9px;padding:6px 10px;font-size:13px;color:var(--muted);white-space:nowrap}details.export>summary::-webkit-details-marker{display:none}details.export>summary:hover{border-color:var(--muted)}
details.export>summary .label{color:var(--text);font-weight:600}details.export>summary b{font-weight:600}details.export>summary b:empty{display:none}details.export>summary .chev{font-size:11px;transition:transform .2s}details.export[open]>summary .chev{transform:rotate(180deg)}details.export.ready>summary{border-color:var(--keep)}details.export.ready>summary b{color:var(--keep)}
.export-body{position:absolute;right:0;top:calc(100% + 8px);width:min(760px,calc(100vw - 32px));max-height:calc(100vh - 120px);overflow:auto;z-index:30;background:var(--card);border:1px solid var(--line);border-radius:12px;box-shadow:0 18px 50px rgba(0,0,0,.55);padding:14px;display:grid;gap:12px}.summary{color:var(--muted);font-size:13px}
.chips{display:flex;gap:8px;margin-top:10px;overflow-x:auto;padding-bottom:2px;align-items:center;scrollbar-width:thin}
.chip{border:1px solid var(--line);background:transparent;color:var(--muted);padding:6px 12px;border-radius:999px;font-size:14px;white-space:nowrap}.chip:hover{color:var(--text)}.chip.on{background:var(--text);color:#0d1117;border-color:var(--text);font-weight:600}
.chip-select{background:transparent;color:var(--muted);border:1px dashed var(--line);border-radius:999px;padding:6px 10px;font-size:14px;max-width:190px}.chip-select.on{color:#0d1117;background:var(--text);border-style:solid;font-weight:600}.chip-select option{background:var(--card);color:var(--text)}
.warn-line{margin-top:8px;color:#ffd38a;font-size:13px}
#resources{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px}.resource{background:var(--card2);border:1px solid var(--line);padding:8px 10px;border-radius:9px;color:var(--muted);font-size:13px}.resource strong{display:block;font-size:15px;margin-top:2px;color:var(--text)}.resource-note{grid-column:1/-1;color:var(--muted);font-size:13px}
#export-panel{display:flex;gap:12px;align-items:center;flex-wrap:wrap}#export-panel label{display:flex;gap:7px;align-items:center;color:var(--muted)}#export-panel select,#export-panel input{background:var(--bg);color:var(--text);border:1px solid var(--line);border-radius:7px;padding:7px}#custom-output-gb{width:90px}
#finalize{background:var(--cut);color:#fff;border:0;border-radius:9px;padding:9px 14px;font-weight:700}#export-status{flex-basis:100%;color:var(--muted);font-size:13px}#export-status.error{color:var(--cut)}
.export-notice{border:1px solid var(--line);border-radius:9px;padding:6px 10px;font-size:13px;color:var(--muted);flex:0 1 auto;min-width:0;max-width:34ch;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.top.has-notice .progress{min-width:48px}.top.has-notice h1.title{max-width:36ch}.export-notice.running{color:var(--accent);border-color:var(--accent)}.export-notice.ok{color:var(--keep);border-color:var(--keep)}.export-notice.error{color:var(--cut);border-color:var(--cut)}
.layout{display:grid;grid-template-columns:280px minmax(0,1fr);gap:16px;max-width:1360px;margin:14px auto 28px;padding:0 16px}
.list{background:var(--card);border:1px solid var(--line);border-radius:16px;overflow:hidden;align-self:start;position:sticky;top:calc(var(--hh) + 12px);display:flex;flex-direction:column;max-height:calc(100vh - var(--hh) - 28px)}
.list h3{margin:0;padding:12px 14px;font-size:14px;color:var(--muted);border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;gap:8px;font-weight:600}
.sheet-close{display:none;background:transparent;border:0;color:var(--muted);font-size:18px;line-height:1;padding:0 4px;margin-left:8px}
.rows{position:relative;overflow:auto;flex:1;min-height:0;overscroll-behavior:contain}
.row{display:grid;grid-template-columns:10px minmax(0,1fr) auto;gap:10px;align-items:center;width:100%;padding:9px 14px;border:0;border-bottom:1px solid #1f2630;background:transparent;color:var(--text);font-size:14px;text-align:left;content-visibility:auto;contain-intrinsic-size:auto 46px}
.row:hover{background:#1b222c}.row.on{background:#22303f;box-shadow:inset 3px 0 0 var(--accent)}.row small{color:var(--muted);display:block;font-size:12px;font-variant-numeric:tabular-nums}
.dot{width:10px;height:10px;border-radius:50%}
.st{font-size:12px;font-weight:700;padding:2px 8px;border-radius:999px;white-space:nowrap}.st-keep{background:rgba(47,191,113,.15);color:var(--keep)}.st-blur{background:rgba(242,169,59,.15);color:var(--blur)}.st-cut{background:rgba(239,84,102,.15);color:var(--cut)}.st-more{background:rgba(124,139,161,.22);color:#c3cedb}.st-pending{background:#2a323d;color:var(--muted)}
.list-empty{padding:20px 14px;color:var(--muted);font-size:14px}
.list-foot{border-top:1px solid var(--line);padding:10px;display:grid;gap:8px}.list-foot button{background:var(--card2);border:1px solid var(--line);color:var(--text);padding:8px;border-radius:9px;font-size:13px}.list-foot button:hover{border-color:var(--muted)}
.sheet-backdrop{display:none}main{min-width:0}.mobile-list{display:none}
.navbar{display:flex;gap:8px;align-items:center;margin-bottom:10px}.navbar button{background:var(--card);border:1px solid var(--line);color:var(--text);padding:8px 12px;border-radius:9px;font-size:14px;white-space:nowrap}.navbar button:hover:not(:disabled){border-color:var(--muted)}
.navbar .grow{flex:1}.save{color:var(--muted);font-size:13px}.save:empty{display:none}
.auto{color:var(--muted);font-size:13px;display:flex;gap:6px;align-items:center;cursor:pointer;user-select:none}.auto input{accent-color:var(--keep);margin:0}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;overflow:hidden;display:grid;grid-template-columns:minmax(0,1.55fr) minmax(280px,1fr)}
.media{background:#000;display:flex;flex-direction:column;min-width:0}
.player{position:relative;aspect-ratio:16/9;background:#000;overflow:hidden}
.player video,.player .poster{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:#000}.player .poster{z-index:1}.player.covered video{visibility:hidden}
.player .loading{position:absolute;inset:0;display:grid;place-items:center;color:var(--muted);font-size:14px;z-index:1}
.player .play{position:absolute;inset:0;margin:auto;width:68px;height:68px;border-radius:50%;border:0;background:rgba(255,255,255,.92);display:grid;place-items:center;box-shadow:0 6px 24px rgba(0,0,0,.45);z-index:2;transition:opacity .15s}
.player .play::after{content:"";margin-left:6px;border-left:22px solid #111;border-top:14px solid transparent;border-bottom:14px solid transparent}
.player.playing .play{opacity:0}.player.playing:hover .play{opacity:.85}.player.playing .play::after{margin:0;width:20px;height:22px;border:0;border-left:7px solid #111;border-right:7px solid #111}.player.no-video .play{display:none}
.player .time{position:absolute;left:12px;bottom:12px;background:rgba(0,0,0,.7);padding:3px 9px;border-radius:6px;font-variant-numeric:tabular-nums;font-size:13px;z-index:2}
.player .pnote{position:absolute;left:12px;right:12px;top:12px;background:rgba(0,0,0,.78);padding:6px 10px;border-radius:8px;font-size:13px;color:#ffd38a;z-index:2}
.timeline{position:relative;height:30px;background:var(--card2);border-top:1px solid #000;cursor:pointer}
.timeline .seg{position:absolute;top:12px;height:6px;background:#2c3542;border-radius:4px}.timeline .win{position:absolute;top:11px;height:8px;background:rgba(255,122,69,.3);border-radius:4px}
.timeline .hit{position:absolute;top:9px;width:3px;height:12px;margin-left:-1px;border-radius:2px;background:var(--violence)}
.timeline .peak{position:absolute;top:8px;width:14px;height:14px;margin-left:-7px;border-radius:50%;background:#fff;border:3px solid var(--cat)}
.timeline .head{position:absolute;top:3px;bottom:3px;width:2px;margin-left:-1px;background:var(--accent);border-radius:1px;pointer-events:none}
.timeline .gapline{position:absolute;top:14px;height:2px;background:repeating-linear-gradient(90deg,#3a4452 0 4px,transparent 4px 8px)}.timeline .mo{position:absolute;top:10px;height:10px;border-radius:4px;background:#5b3b2f;background:color-mix(in srgb,var(--cat) 45%,#1c232d)}.timeline .mo.on{background:var(--cat)}
.thumb i{position:absolute;right:3px;top:3px;background:rgba(0,0,0,.72);font-size:10px;line-height:13px;font-style:normal;font-weight:700;padding:0 4px;border-radius:4px}
.moments{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}.focus-box .mchip,.mchip{display:inline-flex;gap:6px;align-items:center;width:auto;margin:0;background:transparent;border:1px solid var(--line);color:var(--text);border-radius:999px;padding:3px 9px 3px 3px;font-size:13px;font-weight:500;font-variant-numeric:tabular-nums}.focus-box .mchip:hover{background:transparent}.mchip b{display:inline-grid;place-items:center;min-width:20px;height:20px;border-radius:999px;background:#2a323d;font-size:11px}.mchip span{color:var(--accent);font-size:11px}.focus-box .mchip:hover{border-color:var(--muted)}.focus-box .mchip.on{border-color:var(--cat)}.mchip.on b{background:var(--cat);color:#0d1117}
.applies{font-size:13px;color:#c9d3df;background:var(--card2);border:1px dashed var(--line);border-radius:10px;padding:8px 10px}.side>.applies{margin-top:auto}.side>.applies+.decide{margin-top:0}
.studio-wrap{display:grid;gap:6px}.studio{border:1px solid var(--line);background:#202833;color:var(--text);border-radius:10px;padding:10px 8px;font-size:14px;font-weight:600}.studio:hover{border-color:var(--muted)}.studio.sel{border-color:var(--keep);background:rgba(47,191,113,.14)}.studio.platform.sel{border-color:var(--blur);background:rgba(242,169,59,.14)}.studio-wrap small{color:var(--muted);font-size:12px;text-align:center}
.strip{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));gap:6px;padding:10px;background:var(--card2)}
.thumb{position:relative;display:block;padding:0;border-radius:8px;overflow:hidden;border:2px solid transparent;background:#0b0e13;aspect-ratio:16/9;color:var(--text)}
.thumb img{display:block;width:100%;height:100%;object-fit:cover}.thumb img:not([src]){visibility:hidden}
.thumb span{position:absolute;left:3px;bottom:3px;background:rgba(0,0,0,.72);font-size:10px;line-height:13px;padding:0 4px;border-radius:4px;font-variant-numeric:tabular-nums}
.thumb.hit{border-color:rgba(255,122,69,.6)}.thumb.peak{border-color:#fff}.thumb.peak::after{content:"Rõ nhất";position:absolute;top:3px;left:3px;background:#fff;color:#111;font-size:10px;line-height:13px;font-weight:700;padding:0 5px;border-radius:4px}.thumb.on{box-shadow:0 0 0 2px var(--accent)}
.thumb.ghost{cursor:default;background:linear-gradient(90deg,#141a22,#1f2732,#141a22);background-size:200% 100%;animation:shimmer 1.2s linear infinite}@keyframes shimmer{to{background-position:-200% 0}}@media (prefers-reduced-motion:reduce){.thumb.ghost{animation:none}}
.media-region{padding:10px;display:grid;gap:10px;background:#000;align-content:start}
.media-region canvas.region-frame{display:block;width:100%;height:auto;border-radius:8px;background:#090b0e}.media-region canvas.region-crop{display:block;max-width:100%;max-height:150px;width:auto;height:auto;justify-self:center;border:2px solid var(--cut);border-radius:8px;background:#090b0e}
.media-region canvas.evidence-frame{display:block;width:100%;height:auto;border-radius:8px;background:#090b0e}.evidence-legend{color:#ffd38a;font-size:13px}.ai-verdict{padding:9px 10px;border-radius:8px;background:#2b2412;border:1px solid #7a6420;color:#ffe7a8;font-size:14px}.studio-wrap small.studio-note{color:#ffd38a}
.region-more{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.shots{display:grid;gap:8px}.shots img{display:block;width:100%;height:auto;max-height:420px;object-fit:contain;border-radius:8px;background:#090b0e}.no-media{padding:40px 16px;color:var(--muted);text-align:center}
.side{padding:20px 20px 18px;display:flex;flex-direction:column;gap:14px;min-width:0}
.pill{display:inline-flex;align-items:center;gap:6px;font-weight:700;font-size:13px;letter-spacing:.04em;padding:4px 10px;border-radius:999px;background:rgba(255,255,255,.06);background:color-mix(in srgb,var(--cat) 15%,transparent);color:var(--cat);width:max-content}.pill::before{content:"";width:8px;height:8px;border-radius:50%;background:currentColor}
h2{margin:0;font-size:24px;font-variant-numeric:tabular-nums}.sub{color:var(--muted);margin-top:2px}.hint{font-size:13px;color:#9cc7f5}
.focus-box{background:var(--card2);border:1px solid var(--line);border-radius:12px;padding:12px 14px}.focus-box b{color:#fff}.focus-box button{margin-top:10px;width:100%;background:transparent;border:1px solid var(--accent);color:var(--accent);padding:9px;border-radius:9px;font-weight:600;font-size:14px}.focus-box button:hover{background:rgba(77,163,255,.1)}.focus-box small{display:block;margin-top:8px;color:var(--muted)}
.decide-head{font-size:13px;color:var(--muted);margin-bottom:-6px}
body.export-locked .decide button,body.export-locked .region-decide button,body.export-locked [data-act="clear"],body.export-locked [data-act="studio"],body.export-locked [data-act="platform"],body.export-locked .list-foot button{opacity:.45;cursor:not-allowed;filter:none}
.decide{display:grid;grid-template-columns:1fr 1fr;gap:10px}.side>.decide{margin-top:auto}
.decide button{border:0;border-radius:12px;padding:14px 10px;font-size:16px;font-weight:700;color:#0d1117;display:flex;flex-direction:column;align-items:center;gap:2px}.decide button:hover{filter:brightness(1.08)}
.decide small{font-weight:500;font-size:12px;opacity:.75}.decide .k{background:var(--keep)}.decide .b{background:var(--blur)}.decide .c{background:var(--cut);color:#fff}.decide .m{background:#2a323d;color:var(--text)}.decide button.sel{box-shadow:0 0 0 3px var(--card),0 0 0 5px #fff}.decide button.sel small{opacity:1;font-weight:700}
.chosen{font-size:13px;color:var(--muted);text-align:center}.chosen b{color:var(--text)}.linkish{background:none;border:0;padding:0;color:var(--accent);text-decoration:underline;font-size:13px}
details.tech{font-size:13px;color:var(--muted)}details.tech>summary{list-style:none;cursor:pointer;text-align:center;width:max-content;margin:0 auto;border-bottom:1px dashed var(--muted)}details.tech>summary::-webkit-details-marker{display:none}details.tech>summary::after{content:" ▸"}details.tech[open]>summary::after{content:" ▾"}
.tech-body{display:grid;gap:8px;margin-top:10px;text-align:left}.tech-body .meta{color:var(--muted);overflow-wrap:anywhere}
.scope-detail{padding:9px 10px;border-radius:8px;background:#17283d;border:1px solid #315c85;color:#d7eaff}.scope-detail strong{display:block;margin-bottom:4px}.scope-detail.track{background:#153b31;border-color:#28765d;color:#baf4dc}.scope-detail.advisory{background:#3b2c12;border-color:#8b651d;color:#ffe1a0}
.coverage{padding:8px 10px;border-radius:8px;background:#153b31;color:#89e4bd;font-size:13px}.visual-ai{padding:9px 10px;border-radius:8px;background:#172f4b;border:1px solid #386d9d;color:#cce7ff}.region-detail{padding:8px 10px;border-radius:8px;background:#301b20;border:1px solid #8c3947;color:#ffd6dc}
.evidence{padding:8px 10px;border-radius:8px;background:var(--card2);border:1px solid var(--line);color:#c9d3df}.evidence strong{display:block;margin-bottom:4px;color:var(--text)}.labels{color:#c9d3df;overflow-wrap:anywhere}
.decision-block{padding:10px;border:1px solid var(--line);border-radius:12px;background:var(--card2)}.decision-block strong{display:block;margin-bottom:4px}.decision-block small{display:block;margin-bottom:8px;color:var(--muted);font-size:12px}
.region-decide{display:grid;grid-template-columns:1fr 1fr;gap:8px}.region-decide button{border:1px solid var(--line);border-radius:10px;padding:10px 8px;font-size:13px;font-weight:600;color:var(--text);background:#202833}.region-decide .rk.sel{background:rgba(47,191,113,.18);border-color:var(--keep)}.region-decide .rb.sel{background:rgba(242,169,59,.18);border-color:var(--blur)}
.empty{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:40px 24px;color:var(--muted);text-align:center}
.next{margin:14px 0 0;color:var(--muted);font-size:13px;text-align:center}.next .keys{display:block;margin-top:4px;opacity:.75}
@media (max-width:820px){header{position:static;padding:12px 16px}h1.title{font-size:17px;flex:1;max-width:none}.progress{order:3;flex-basis:100%}details.export{order:4}.export-notice{order:4;flex-basis:100%;max-width:none;white-space:normal;overflow-wrap:anywhere}.back{order:5;margin-left:auto;font-size:12px;padding:5px 8px}.export-body{left:0;right:auto}
.layout{grid-template-columns:1fr;margin-top:12px}.list{display:none;position:fixed;left:0;right:0;bottom:0;top:auto;z-index:40;max-height:78vh;border-radius:16px 16px 0 0;box-shadow:0 -10px 40px rgba(0,0,0,.6)}.list.open{display:flex}.sheet-close{display:inline-block}.sheet-backdrop.open{display:block;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:39}
.mobile-list{display:block;width:100%;text-align:left;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 14px;margin-bottom:10px;color:var(--muted);font-size:14px}.mobile-list b{color:var(--text)}
.navbar{flex-wrap:wrap}.navbar button{flex:1;padding:8px 6px}.navbar .grow{display:none}.navbar .auto{flex-basis:100%;justify-content:center}.save{flex-basis:100%;text-align:center;order:5}
.card{grid-template-columns:1fr}.strip{grid-template-columns:repeat(4,minmax(0,1fr))}.side{padding:16px}.decide button{padding:13px 8px;font-size:15px}h2{font-size:21px}.next .keys{display:none}}
</style></head><body>
<header id="top"><div class="top"><h1 class="title" id="title">Duyệt nội dung</h1><span class="count" id="count">Đang tải…</span><div class="progress" aria-hidden="true"><span id="progress"></span></div><details class="export" id="export-section"><summary><span class="label">Xuất video</span><b id="export-summary"></b><span class="chev" aria-hidden="true">▾</span></summary><div class="export-body"><div class="summary" id="summary">Đang tải…</div><section id="resources"></section><section id="export-panel"><label>Dung lượng video<select id="output-size-mode" onchange="toggleCustomOutputSize()">__EXPORT_SIZE_OPTIONS__</select></label><label id="custom-size-wrap" hidden>Tối đa<input id="custom-output-gb" __EXPORT_CUSTOM_GB__ value="3.5">GB</label><button id="finalize" type="button" onclick="finalizeExport()" disabled>Hoàn tất duyệt và xuất video</button><span id="export-status">Hãy giải quyết toàn bộ mục trước khi xuất.</span></section></div></details><span class="export-notice" id="export-notice" role="status" aria-live="polite" hidden></span><button class="back" type="button" onclick="location.href='/'">← Quay lại Dashboard</button></div>
<div class="chips" id="chips" role="toolbar" aria-label="Bộ lọc"><button class="chip on" type="button" data-filter="pending" id="chip-pending">Chưa duyệt</button><button class="chip" type="button" data-filter="adult">18+</button><button class="chip" type="button" data-filter="gore">Máu me</button><button class="chip" type="button" data-filter="violence">Bạo lực</button><button class="chip" type="button" data-filter="ads">Quảng cáo</button><button class="chip" type="button" data-filter="all">Tất cả</button><select id="more-filter" class="chip-select" aria-label="Lọc khác"><option value="">Lọc khác…</option><option value="high">Ưu tiên cao</option><option value="visual_ai">Visual AI</option><option value="visual_logo">Logo</option><option value="text">Chữ</option><option value="candidates" id="candidate-filter">Ứng viên phụ</option></select></div>
<div class="warn-line" id="scope-warning" hidden></div></header>
<div class="layout"><aside class="list" id="list" aria-label="Danh sách mục"><h3><span id="list-title">Chưa duyệt</span><span><span id="list-count">0</span><button class="sheet-close" id="sheet-close" type="button" aria-label="Đóng danh sách">✕</button></span></h3><div class="rows" id="rows"></div><div class="list-foot"><button class="accept" type="button" onclick="bulkAccept()">Duyệt tất cả đề xuất đang lọc</button><button class="bulk" type="button" onclick="bulkKeep()">Giữ nguyên tất cả đang lọc</button></div></aside><div class="sheet-backdrop" id="sheet-backdrop"></div>
<main><button class="mobile-list" id="mobile-list" type="button">Mục <b id="mobile-pos">–</b> · <u>Danh sách để chọn lại ▾</u></button>
<div class="navbar"><button id="prev" type="button" title="Mục trước (phím ←)">← Trước</button><button id="next" type="button" title="Mục sau (phím →)">Sau →</button><button id="undo" type="button" disabled>↶ Hoàn tác</button><span class="save" id="save-state" aria-live="polite"></span><span class="grow"></span><label class="auto"><input type="checkbox" id="auto-next" checked> Tự sang mục chưa duyệt kế tiếp</label></div>
<section class="card" id="focus" hidden><div class="media"><div id="media-safety"><div class="player covered" id="player"><video id="video" preload="none" playsinline disablepictureinpicture></video><img class="poster" id="poster" alt="" hidden><div class="loading" id="poster-loading" hidden>Đang tải khung hình…</div><button class="play" id="play-btn" type="button" aria-label="Phát hoặc dừng (Space)"></button><div class="time" id="ptime"></div><div class="pnote" id="pnote" hidden></div></div><div class="timeline" id="timeline" title="Bấm để tua tới thời điểm này"></div><div class="strip" id="strip"></div></div><div class="media-region" id="media-region" hidden></div></div><div class="side" id="side"></div></section>
<div class="empty" id="empty">Đang tải…</div><p class="next" id="next-note"><span id="next-text"></span><span class="keys">Phím 1–4 chọn · ←/→ chuyển mục · Space phát/dừng · Z hoàn tác</span></p></main></div>
<script>
const API='/api/';
__EXPORT_DIALOG_JS__let token=__BILIFLOW_REVIEW_TOKEN__;let mediaKey=null;let queue=null;let resources=null;let exportJob={status:'IDLE'};let filter='pending';let focusId=null;let previousFocusId=null;let autoNext=true;let busy=false;let exportSettingsInitialized=false;let queueRefreshRunning=false;let exportRequestInFlight=false;let exportNoticeActive=false;let exportError='';
let writeChain=Promise.resolve();let pendingWrites=0;let localEpoch=0;let resyncNeeded=false;let reopenAfterResync=null;let sessionRefresh=null;let resourcesTimer=null;let prefetchTimer=null;let saveTimer=null;let techOpen=false;let sheetOpen=false;let mediaGen=0;let regionKey='';let sideItemId=null;
let itemMap=new Map();let listIds=[];const rowById=new Map();const sticky=new Set();const undoStack=[];
const evidenceCache=new Map();const evidenceLoading=new Map();const frameBlobs=new Map();const frameUrgent=[];const frameLater=[];let frameActive=0;
const pstate={id:null,start:0,end:0,reveal:false,loaded:false,pending:null,seekFor:null,available:true,reason:null,srcKey:null,want:null,keyRetries:0,moments:null,mi:-1,seq:false,stopAt:null};
const reviewStats={fullRenders:0,focusRenders:0,sideRenders:0,listBuilds:0,rowUpdates:0,polls:0,pollChanges:0,evidenceFetches:0,frameFetches:0,writeRetries:0,writeFailures:0,sessionRefreshes:0,mediaKeyChanges:0,frameKeyRetries:0,videoErrors:0};window.reviewStats=reviewStats;
try{autoNext=localStorage.getItem('biliflow.review.autoNext')!=='0';}catch(_error){}
const $=s=>document.querySelector(s);const video=$('#video'),playerBox=$('#player'),posterImg=$('#poster');
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clock=s=>{const m=Math.floor(s/60),v=(s-m*60).toFixed(1).padStart(4,'0');return `${String(m).padStart(2,'0')}:${v}`;};
const mmss=s=>{const v=Math.max(0,Math.floor(Number(s)||0));return `${Math.floor(v/60)}:${String(v%60).padStart(2,'0')}`;};
const mmssTenth=s=>{const v=Math.max(0,Number(s)||0),m=Math.floor(v/60);return `${m}:${(v-m*60).toFixed(1).padStart(4,'0')}`;};
const span=x=>`${mmss(x.start_seconds)}–${mmss(x.end_seconds)}`;
function durationText(seconds){const s=Math.max(0,Number(seconds)||0);return s<120?`${Math.max(1,Math.round(s))} giây`:`${Math.round(s/60)} phút`;}
const size=n=>n>1073741824?`${(n/1073741824).toFixed(1)} GB`:`${(n/1048576).toFixed(1)} MB`;
function setText(el,value){if(el&&el.textContent!==value)el.textContent=value;}
function setHtml(el,value){if(el&&el.__html!==value){el.__html=value;el.innerHTML=value;}}
async function readJson(response){let data=null;try{data=await response.json();}catch(_error){}if(!response.ok){const error=new Error(data?.error||`Máy chủ trả về lỗi ${response.status}`);error.status=response.status;throw error;}return data;}
function offlineError(){const error=new Error('Mất kết nối với Review. Hãy tải lại trang; các lựa chọn đã lưu trước đó vẫn được giữ nguyên.');error.status=0;return error;}
async function requestJson(path,options={}){let response;try{response=await fetch(path,options);}catch(_error){throw offlineError();}return readJson(response);}
const SAFETY={adult:['18+','var(--adult)'],gore:['Máu me','var(--gore)'],violence:['Bạo lực','var(--violence)']};
const KIND_NAMES={logo_overlay:'Logo',logo_candidate:'Ứng viên logo',opening_promotion:'Quảng cáo mở đầu',text_candidate:'Ứng viên chữ',title_overlay:'Tiêu đề phim',in_film_text:'Chữ trong phim',platform_logo:'Logo nền tảng'};
const FILTER_TITLES={pending:'Chưa duyệt',adult:'18+',gore:'Máu me',violence:'Bạo lực',ads:'Quảng cáo',all:'Tất cả mục',high:'Ưu tiên cao',visual_ai:'Visual AI',visual_logo:'Logo',text:'Chữ',candidates:'Ứng viên phụ'};
const STATUS={KEEP:['Giữ','st-keep'],BLUR:['Làm mờ','st-blur'],CUT:['Cắt','st-cut'],NEEDS_MORE_CONTEXT:['Cần xem','st-more']};
function isSafety(x){return !!x&&Object.prototype.hasOwnProperty.call(SAFETY,x.category);}
const SCENE_WORDS={violence:'Trận đánh',gore:'Cảnh máu',adult:'Nhóm 18+'};
function momentsOf(x){if(!x||x.temporal_policy!=='discrete_detected_intervals')return [];return (x.detected_intervals||[]).map(d=>({start:Number(d.start_seconds),end:Number(d.end_seconds)})).filter(m=>Number.isFinite(m.start)&&Number.isFinite(m.end)&&m.end>=m.start).sort((a,b)=>a.start-b.start);}
function isScene(x){return isSafety(x)&&momentsOf(x).length>1;}
function momentTotal(ms){return ms.reduce((s,m)=>s+(m.end-m.start),0);}
function sceneTitle(x){return `${SCENE_WORDS[x.category]||catName(x)} · ${momentsOf(x).length} khoảnh khắc`;}
function sceneSpan(x){return `tổng ${mmss(momentTotal(momentsOf(x)))} (trải dài ${mmss(x.start_seconds)}–${mmss(x.end_seconds)})`;}
function sceneHeader(x){return `${sceneTitle(x)} · ${sceneSpan(x)}`;}
function appliesLine(x){const ms=momentsOf(x);return `Quyết định chỉ áp dụng cho ${ms.length} khoảnh khắc này (tổng ${mmss(momentTotal(ms))}); khoảng trống giữa chúng giữ nguyên.`;}
function momentIndex(t,ms){return ms.findIndex(m=>t>=m.start-.05&&t<=m.end+.05);}
function studioEligible(x){return !!x&&x.category==='visual_logo'&&!x.suggested_region_source_pixels&&x.candidate_type!=='persistent_overlay';}
function catName(x){if(isSafety(x))return SAFETY[x.category][0];if(x.review_kind==='opening_promotion'&&x.opening_ident)return 'Logo mở đầu';if(x.category==='visual_logo'&&x.candidate_type==='opening_boundary')return 'Kiểm tra đoạn mở đầu';if(x.category==='visual_logo'&&x.candidate_type==='ending_boundary')return 'Kiểm tra đoạn kết';if(x.category==='visual_logo'&&x.candidate_type==='platform_logo')return `Logo nền tảng ${x.platform_logo?.name||''}`.trim();if(x.category==='visual_logo'&&x.candidate_type==null&&!x.suggested_region_source_pixels)return 'Logo toàn khung (chưa khoanh vùng)';return KIND_NAMES[x.review_kind]||(x.category==='text'?'Chữ':x.category==='visual_logo'?'Logo / quảng cáo':String(x.category||'Khác'));}
function sceneLogo(x){return !!x&&x.category==='visual_logo'&&!x.suggested_region_source_pixels&&(x.candidate_type==null||['opening_boundary','ending_boundary','opening_promotion','branded_end_card'].includes(x.candidate_type));}
function hasPlayer(x){return isSafety(x)||sceneLogo(x);}
function catColor(x){return isSafety(x)?SAFETY[x.category][1]:'var(--ad)';}
function statusOf(x){return STATUS[x.decision]||['Chưa duyệt','st-pending'];}
function isLogoItem(x){return x.category==='visual_logo'||x.review_kind==='logo_overlay'||x.review_kind==='logo_candidate';}
function isAdItem(x){return x.category==='visual_logo'||x.category==='text'||isLogoItem(x);}
function visible(x){if(filter==='pending')return !x.decision||sticky.has(x.id);if(filter==='high')return x.priority==='high';if(filter==='all')return true;if(filter==='visual_ai')return !!x.ai_visual_audit;if(filter==='visual_logo')return isLogoItem(x);if(filter==='ads')return isAdItem(x);if(filter==='text')return x.category==='text'&&x.review_kind!=='logo_overlay';return x.category===filter;}
function byTime(a,b){return (Number(a.start_seconds)-Number(b.start_seconds))||(Number(a.end_seconds)-Number(b.end_seconds))||String(a.id).localeCompare(String(b.id));}
function filteredItems(){if(!queue)return [];const data=filter==='candidates'?(queue.advisory_items||[]).slice():queue.items.filter(visible);return data.sort(byTime);}
function indexQueue(){itemMap=new Map();for(const x of queue.items||[])itemMap.set(x.id,x);for(const x of queue.advisory_items||[])if(!itemMap.has(x.id))itemMap.set(x.id,x);}
function countsFrom(items){const decisions={KEEP:0,BLUR:0,CUT:0,NEEDS_MORE_CONTEXT:0};let pending=0;for(const x of items){if(Object.prototype.hasOwnProperty.call(decisions,x.decision))decisions[x.decision]++;else pending++;}return{total:items.length,pending,decisions};}
function statusFrom(items){if(items.some(x=>x.decision==='NEEDS_MORE_CONTEXT'))return 'NEEDS_MORE_CONTEXT';if(items.some(x=>!x.decision))return 'REVIEW_REQUIRED';return 'READY_FOR_EDIT_PLAN';}
function queueIdentity(value){if(!value)return '';const reports=(value.reports||[]).map(x=>typeof x==='string'?x:(x.path||x.report||JSON.stringify(x))).join('|');return [value.created_at||'',value.source?.input_sha256||'',reports].join('::');}
function queueVersion(value){if(!value)return '';const counts=value.counts||{};return [queueIdentity(value),value.updated_at||'',value.status||'',counts.total||0,counts.pending||0].join('::');}
async function load(){try{const [q,r,e,s]=await Promise.all([requestJson(API+'queue',{cache:'no-store'}),requestJson(API+'resources',{cache:'no-store'}).catch(()=>null),requestJson(API+'export',{cache:'no-store'}).catch(()=>({status:'IDLE'})),requestJson(API+'session',{cache:'no-store'}).catch(()=>null)]);queue=q;resources=r;exportJob=e||{status:'IDLE'};updateExportNotice();if(s){if(s.token)token=s.token;mediaKey=s.media_key||null;}indexQueue();if(!countsFrom(queue.items).pending&&queue.items.length)filter='all';initializeExportSettings();const started=performance.now();render();reviewStats.firstRenderMs=Math.round((performance.now()-started)*10)/10;reviewStats.readyAtMs=Math.round(performance.now());}catch(error){setText($('#count'),error.message);setText($('#empty'),error.message);setText($('#summary'),error.message);}}
function render(){reviewStats.fullRenders++;renderChips();renderList();if(!focusId||!itemMap.has(focusId)||!listIds.includes(focusId))focusId=pickFocus();updateHeader();renderFocus();}
function renderChips(){document.querySelectorAll('#chips .chip').forEach(b=>b.classList.toggle('on',b.dataset.filter===filter));const more=$('#more-filter'),extra=['high','visual_ai','visual_logo','text','candidates'].includes(filter);more.value=extra?filter:'';more.classList.toggle('on',extra);}
function sourceTitle(){const path=String(queue?.source?.path||''),name=path.split(/[\\/]/).pop().replace(/\.[^.]+$/,'').replace(/[._]+/g,' ').trim(),year=name.match(/^(.*?\b(?:19|20)\d{2})\b/);return (year?year[1]:name)||'video';}
function updateHeader(){if(!queue)return;const c=countsFrom(queue.items),done=c.total-c.pending,advisory=(queue.advisory_items||[]).length;setText($('#count'),`${done}/${c.total} xong`);const width=`${c.total?done/c.total*100:100}%`;if($('#progress').style.width!==width)$('#progress').style.width=width;setText($('#chip-pending'),`Chưa duyệt (${c.pending})`);setText($('#candidate-filter'),`Ứng viên phụ (${advisory})`);const title=`Duyệt nội dung · ${sourceTitle()}`;setText($('#title'),title);if(document.title!==title)document.title=title;const names={advertising:'Quảng cáo / logo',adult:'18+',gore:'Máu me',violence:'Bạo lực'},skipped=((queue.detection_scope||{}).skipped||[]).map(x=>names[x]||x),warn=$('#scope-warning');warn.hidden=!skipped.length;if(skipped.length)setText(warn,`Không quét trong lượt này: ${skipped.join(', ')}. Ít mục hơn không có nghĩa các nhóm này đã an toàn.`);setText($('#next-text'),nextNote(c));renderExport();updateNavState();}
function reviewDoneHint(){return exportJob&&exportJob.source_cleaned?'Video gốc đã được dọn vào Thùng rác; trang duyệt chỉ để xem.':exportJob&&exportJob.source_archived?'Video gốc đang ở kho lưu trữ; bấm “Khôi phục bản xuất” trên Dashboard để xuất lại.':'Bấm “Xuất video” ở trên để xuất.';}
function nextNote(c){if(!c.pending)return `Đã duyệt đủ mọi mục chính. ${reviewDoneHint()}`;let inList=0;for(const id of listIds){const x=itemMap.get(id);if(x&&!x.decision)inList++;}const where=filter==='pending'||inList===c.pending?'':` (${inList} trong bộ lọc này)`;return `Còn ${c.pending} mục chưa duyệt${where} · ${autoNext?'mục kế tiếp tự hiện sau khi chọn':'bấm “Sau →” để sang mục kế'}`;}
function renderExport(){if(!queue)return;const c=countsFrom(queue.items),done=c.total-c.pending,scope=queue.detection_scope||{},scopeNames={advertising:'Quảng cáo / logo',adult:'18+',gore:'Máu me',violence:'Bạo lực'},selected=(scope.selected||[]).map(x=>scopeNames[x]||x),skipped=(scope.skipped||[]).map(x=>scopeNames[x]||x);let fullTracks=0,rangeTracks=0;for(const x of queue.items){if(x.candidate_type==='persistent_overlay'){if(trackCoversFullVideo(x))fullTracks++;else rangeTracks++;}}const advisory=(queue.advisory_items||[]).length,visual=queue.visual_ai_audit?.assessment_count||0,status=queue.status;
setText($('#summary'),`${done}/${c.total} mục chính đã duyệt · ${c.pending} mục chính còn lại · ${fullTracks} track toàn video · ${rangeTracks} track theo khoảng · ${advisory} ứng viên phụ · Đã quét: ${selected.length?selected.join(', '):'phạm vi cũ'} · Visual AI ${visual} mục · Trạng thái: ${status}`);
if(resources){const range=resources.estimated_preview_megabytes_range||[0,0];setHtml($('#resources'),`<div class="resource">Video nguồn<strong>${size(resources.source_bytes)}</strong></div><div class="resource">Ảnh và report<strong>${size(resources.report_bytes)}</strong></div><div class="resource">Ổ E còn trống<strong>${size(resources.disk_free_bytes)}</strong></div><div class="resource">Preview dự kiến<strong>${resources.estimated_preview_seconds} giây · khoảng ${range[0]}–${range[1]} MB</strong></div><div class="resource-note">Review không chạy model AI. Khung xem nhanh được trích bằng FFmpeg (CPU) khi mở từng mục, lưu tạm có giới hạn và tự dọn; video phát thẳng từ file gốc, không tạo bản sao. ${skipped.length?`Không quét trong lượt này: ${skipped.join(', ')}. Ít thẻ hơn không có nghĩa các nhóm này đã an toàn. `:''}Có ${c.total} mục chính bắt buộc duyệt và ${advisory} ứng viên phụ không chặn xuất. Track toàn video, track theo khoảng và nhóm sự kiện được ghi riêng; nhóm sự kiện chỉ áp dụng các khoảng phát hiện gốc, không sửa khoảng trống.</div>`);}
const active=['QUEUED','RENDERING'].includes(exportJob.status),skippedExport=exportJob.status==='SKIPPED',cleaned=!!exportJob.source_cleaned,archived=!!exportJob.source_archived,locked=active||cleaned||archived,ready=status==='READY_FOR_EDIT_PLAN'&&!active&&!skippedExport&&!cleaned&&!archived;$('#finalize').disabled=!ready||exportRequestInFlight;document.body.classList.toggle('export-locked',locked);for(const b of document.querySelectorAll('.list-foot button')){b.disabled=locked;b.title=locked?(cleaned?SOURCE_CLEANED_LOCK_MESSAGE:archived?SOURCE_ARCHIVED_LOCK_MESSAGE:EXPORT_LOCK_MESSAGE):'';}const exportText={IDLE:pendingDescription(),WAITING_REVIEW:pendingDescription(),READY_TO_EXPORT:'Đã duyệt đủ. Bạn có thể xuất video.',QUEUED:'Đã xếp hàng xuất video.',RENDERING:'Đang render và kiểm tra video…',COMPLETED:`Hoàn tất: ${exportJob.output||''}`,FAILED:`Xuất thất bại: ${exportJob.error||'không rõ lỗi'}`,SKIPPED:'Video đã được đánh dấu bỏ qua (không xuất). Bấm “Mở lại để xuất” ở Dashboard nếu muốn xuất video.'};const when=formatStamp(exportJob.source_cleanup?.finished_at),cleanedText=`${exportJob.status==='COMPLETED'?`Hoàn tất: ${exportJob.output||''}. `:''}Video gốc đã được dọn vào Thùng rác${when?` lúc ${when}`:''}. Chép lại đúng tên “${exportJob.source_name||''}” vào input để xuất lại hoặc sửa quyết định.`,archivedWhen=formatStamp(exportJob.source_archive?.archived_at),archivedText=`${exportJob.status==='COMPLETED'?`Hoàn tất: ${exportJob.output||''}. `:''}Video gốc đang ở kho lưu trữ${archivedWhen?` từ ${archivedWhen}`:''}. Bấm “Khôi phục bản xuất” trên Dashboard để xuất lại hoặc sửa quyết định.`;setText($('#export-status'),exportError||(cleaned?cleanedText:archived?archivedText:exportText[exportJob.status])||pendingDescription());$('#export-status').classList.toggle('error',!!exportError);
setText($('#export-summary'),active?'đang xuất…':cleaned?'đã dọn video gốc':archived?'video gốc ở kho lưu trữ':skippedExport?'đã bỏ qua':exportJob.status==='COMPLETED'?'đã xuất':exportJob.status==='FAILED'?'lỗi':ready?'sẵn sàng':status==='NEEDS_MORE_CONTEXT'?'còn mục cần xem':'');$('#export-section').classList.toggle('ready',ready);}
function showExportNotice(text,tone,detail){const el=$('#export-notice');if(!el)return;$('#top .top')?.classList.toggle('has-notice',!!text);if(!text){el.hidden=true;setText(el,'');el.removeAttribute('title');return;}setText(el,text);const full=detail||text;if(el.title!==full)el.title=full;const cls=`export-notice ${tone||'running'}`;if(el.className!==cls)el.className=cls;el.hidden=false;}
function exportNoticeText(job){const p=(job&&job.render_progress)||{},percent=Number(p.percent);switch(job&&job.status){case 'QUEUED':return['Đã xếp hàng xuất video.','running'];case 'RENDERING':if(p.state==='VERIFYING')return['Đang kiểm tra video đã xuất…','running'];if(Number.isFinite(percent)&&percent>0){const eta=Number.isFinite(Number(p.eta_seconds))&&p.eta_seconds!=null?` · còn khoảng ${Math.max(1,Math.ceil(Number(p.eta_seconds)/60))} phút`:'';return[`Đang xuất video: ${percent.toLocaleString('vi-VN',{maximumFractionDigits:1})}%${eta}`,'running'];}return['Đang xuất video…','running'];case 'COMPLETED':{const output=String(job.output||''),name=output.split(/[\\/]/).pop();return[`Đã xuất xong: ${name}`,'ok',output?`Đã xuất xong: ${output}`:''];}case 'FAILED':return[`Xuất video thất bại: ${job.error||'không rõ lỗi'}`,'error'];default:return null;}}
function updateExportNotice(){if(['QUEUED','RENDERING'].includes(exportJob.status))exportNoticeActive=true;if(!exportNoticeActive)return;const value=exportNoticeText(exportJob);if(value)showExportNotice(value[0],value[1],value[2]);}
function initializeExportSettings(){if(exportSettingsInitialized)return;const choice=exportPolicyChoice(queue.export_size_policy);$('#output-size-mode').value=choice.mode;if(choice.mode==='custom')$('#custom-output-gb').value=choice.gb;exportSettingsInitialized=true;toggleCustomOutputSize();}
function toggleCustomOutputSize(){$('#custom-size-wrap').hidden=$('#output-size-mode').value!=='custom';}
function outputSizeSelection(){return exportSizeSelection($('#output-size-mode').value,$('#custom-output-gb').value);}
function pendingDescription(){const pending=queue.items.filter(x=>!x.decision),names={violence:'Bạo lực',gore:'Máu me',adult:'18+',visual_logo:'Logo / quảng cáo',text:'Chữ'};if(!pending.length)return 'Đã duyệt đủ. Bạn có thể xuất video.';return `Còn ${pending.length} mục chưa duyệt: ${pending.slice(0,3).map(x=>`${names[x.category]||x.category} ${clock(x.start_seconds)}–${clock(x.end_seconds)}`).join('; ')}. Hãy chọn Giữ nguyên, Làm mờ cả cảnh, Cắt cảnh hoặc Cần xem thêm.`;}
function rowHtml(x){const [label,cls]=statusOf(x),scene=isScene(x),name=scene?`${SCENE_WORDS[x.category]||catName(x)} (${momentsOf(x).length})`:catName(x);return `<button type="button" class="row${x.id===focusId?' on':''}" data-id="${esc(x.id)}"${scene?` title="${esc(sceneHeader(x))}"`:''}><span class="dot" style="background:${catColor(x)}"></span><span>${esc(name)}<small>${span(x)}</small></span><span class="st ${cls}">${label}</span></button>`;}
function renderList(){reviewStats.listBuilds++;const data=filteredItems(),rows=$('#rows');listIds=data.map(x=>x.id);rowById.clear();rows.innerHTML=data.length?data.map(rowHtml).join(''):'<div class="list-empty">Không có mục nào trong bộ lọc này.</div>';for(const el of rows.children)if(el.dataset.id)rowById.set(el.dataset.id,el);setText($('#list-title'),FILTER_TITLES[filter]||'Danh sách');setText($('#list-count'),String(data.length));markActiveRow();}
function updateListStatuses(){const data=filteredItems();if(data.length!==listIds.length||data.some((x,i)=>x.id!==listIds[i])){renderList();return;}for(const x of data){const row=rowById.get(x.id);if(!row)continue;const [label,cls]=statusOf(x),pill=row.lastElementChild;if(pill.textContent!==label){pill.textContent=label;pill.className=`st ${cls}`;reviewStats.rowUpdates++;}}}
function markActiveRow(){const old=$('#rows .row.on');if(old&&old.dataset.id!==focusId)old.classList.remove('on');const row=rowById.get(focusId);if(row){row.classList.add('on');ensureRowVisible(row);}}
function ensureRowVisible(row){const box=$('#rows');if(!box.clientHeight)return;const top=row.offsetTop,bottom=top+row.offsetHeight;if(top<box.scrollTop)box.scrollTop=top;else if(bottom>box.scrollTop+box.clientHeight)box.scrollTop=bottom-box.clientHeight;}
function updateNavState(){const i=listIds.indexOf(focusId);$('#prev').disabled=!listIds.length||i===0;$('#next').disabled=!listIds.length||i===listIds.length-1;const undoButton=$('#undo'),last=undoStack[undoStack.length-1],item=last&&itemMap.get(last.id);undoButton.disabled=!undoStack.length||busy;undoButton.title=item?(last.advisory?`Lựa chọn cho ứng viên phụ ${catName(item)} ${span(item)} không hoàn tác được (phím Z để xem lý do)`:`Hoàn tác lựa chọn cho ${catName(item)} ${span(item)} (phím Z)`):'Chưa có lựa chọn nào trong phiên này để hoàn tác';setText($('#mobile-pos'),listIds.length?`${i>=0?i+1:'–'}/${listIds.length}`:'0/0');}
function pickFocus(){for(const id of listIds){const x=itemMap.get(id);if(x&&!x.decision)return id;}return listIds[0]||null;}
function nextUndecided(fromId){const n=listIds.length;if(!n)return null;let start=listIds.indexOf(fromId);if(start<0){const current=itemMap.get(fromId);start=-1;if(current)for(let i=0;i<n;i++){const x=itemMap.get(listIds[i]);if(x&&byTime(x,current)<0)start=i;}}for(let k=1;k<=n;k++){const id=listIds[(start+k+n)%n],x=itemMap.get(id);if(id!==fromId&&x&&!x.decision)return id;}return null;}
function selectItem(id){if(!id||!itemMap.has(id))return;closeSheet();if(id===focusId)return;previousFocusId=focusId;focusId=id;renderFocus();}
function step(delta){if(!listIds.length)return;const i=listIds.indexOf(focusId),j=i<0?(delta>0?0:listIds.length-1):Math.min(listIds.length-1,Math.max(0,i+delta));selectItem(listIds[j]);}
function setFilter(next){if(!next||next===filter||!queue)return;filter=next;sticky.clear();renderChips();renderList();if(!listIds.includes(focusId)){previousFocusId=focusId;focusId=pickFocus();renderFocus();}else updateNavState();updateHeader();}
function openSheet(){sheetOpen=true;$('#list').classList.add('open');$('#sheet-backdrop').classList.add('open');const row=rowById.get(focusId);if(row)ensureRowVisible(row);}
function closeSheet(){if(!sheetOpen)return;sheetOpen=false;$('#list').classList.remove('open');$('#sheet-backdrop').classList.remove('open');}
function renderFocus(){reviewStats.focusRenders++;mediaGen++;const x=itemMap.get(focusId),card=$('#focus'),empty=$('#empty');markActiveRow();updateNavState();if(!x){card.hidden=true;empty.hidden=false;setText(empty,!queue?'Đang tải…':countsFrom(queue.items).pending?'Không có mục nào trong bộ lọc này.':`Đã duyệt đủ ${queue.items.length} mục chính. ${reviewDoneHint()}`);pausePlayer();sideItemId=null;return;}
card.hidden=false;empty.hidden=true;card.style.setProperty('--cat',catColor(x));const safety=isSafety(x),player=hasPlayer(x);$('#media-safety').hidden=!player;$('#media-region').hidden=safety;if(player)setupSafetyMedia(x);else pausePlayer();if(safety){if(regionKey){regionKey='';$('#media-region').textContent='';}}else renderRegionMedia(x,true);renderSide(x);keepFrames();schedulePrefetch();}
function previewSrc(x){const p=(x.preview_images||[])[0];return p?'/media/'+encodeURIComponent(p):null;}
function thumbTime(p){const m=/-(\d+(?:\.\d+)?)s\.(?:jpg|jpeg|png)$/i.exec(String(p||''));return m?Number(m[1]):null;}
function setupSafetyMedia(x){const start=Number(x.start_seconds),end=Number(x.end_seconds);if(pstate.id!==x.id||pstate.start!==start||pstate.end!==end){pausePlayer();pstate.id=x.id;pstate.start=start;pstate.end=end;pstate.seekFor=null;pstate.pending=null;pstate.moments=isScene(x)?momentsOf(x):null;pstate.mi=-1;pstate.seq=false;pstate.stopAt=null;coverVideo();setPoster(null,!!mediaKey);hideNote();setTime(start);}playerBox.classList.toggle('no-video',!videoAllowed());const gen=mediaGen,cached=evidenceCache.get(x.id);if(cached!==undefined||!mediaKey){const ev=cached===undefined?null:cached;renderTimeline(x,ev);renderStrip(x,ev);if(!ev&&!pstate.reveal)setPoster(previewSrc(x));return;}renderTimeline(x,null);renderStrip(x,undefined);loadEvidence(x.id).then(ev=>{if(gen!==mediaGen||focusId!==x.id)return;renderTimeline(x,ev);renderStrip(x,ev);if(!ev&&!pstate.reveal)setPoster(previewSrc(x));playerBox.classList.toggle('no-video',!videoAllowed());renderSide(x);});}
function pickStrip(frames,n=8){const list=(frames||[]).slice().sort((a,b)=>a.t-b.t);if(list.length<=n)return list;const chosen=new Set(),strongest=list.findIndex(f=>f.kind==='strongest');if(strongest>=0)chosen.add(strongest);const take=(indexes,slots)=>{if(slots<=0||!indexes.length)return;if(indexes.length<=slots){indexes.forEach(i=>chosen.add(i));return;}for(let s=0;s<slots;s++)chosen.add(indexes[Math.min(indexes.length-1,Math.floor((s+.5)*indexes.length/slots))]);};const seeds=list.map((f,i)=>f.kind==='seed'&&!chosen.has(i)?i:-1).filter(i=>i>=0);take(seeds,Math.ceil((n-chosen.size)/2));take(list.map((_f,i)=>chosen.has(i)?-1:i).filter(i=>i>=0),n-chosen.size);return [...chosen].sort((a,b)=>a-b).slice(0,n).map(i=>list[i]);}
function pickSceneStrip(frames,ms,n=8){const list=(frames||[]).filter(f=>momentIndex(f.t,ms)>=0).sort((a,b)=>a.t-b.t);if(list.length<=n)return list;const rank=f=>f.kind==='strongest'?3:f.kind==='seed'?2:1,groups=ms.map(()=>[]);list.forEach(f=>groups[momentIndex(f.t,ms)].push(f));let order=ms.map((_m,i)=>i).filter(i=>groups[i].length);if(order.length>n){const strong=list.find(f=>f.kind==='strongest'),picked=new Set(strong?[momentIndex(strong.t,ms)]:[]);for(let s=0;picked.size<n&&s<order.length;s++)picked.add(order[Math.min(order.length-1,Math.floor((s+.5)*order.length/n))]);order=[...picked];}const chosen=new Set();for(const i of order){if(chosen.size>=n)break;const mid=(ms[i].start+ms[i].end)/2;chosen.add(groups[i].slice().sort((a,b)=>rank(b)-rank(a)||(Number(b.score)||0)-(Number(a.score)||0)||Math.abs(a.t-mid)-Math.abs(b.t-mid))[0]);}if(chosen.size<n)for(const f of pickStrip(list.filter(f=>!chosen.has(f)),n-chosen.size))chosen.add(f);return [...chosen].sort((a,b)=>a.t-b.t);}
function pickFor(x,frames){return isScene(x)?pickSceneStrip(frames,momentsOf(x),8):pickStrip(frames,8);}
function stripFrames(x,ev){if(ev&&mediaKey&&(ev.frames||[]).length&&!['source_cleaned','source_missing'].includes(ev?.video?.reason))return pickFor(x,ev.frames).map(f=>({t:f.t,kind:f.kind,remote:true}));const ms=momentsOf(x),inside=p=>{const t=thumbTime(p);return !isScene(x)||t==null||momentIndex(t,ms)>=0;};return (x.preview_images||[]).filter(inside).slice(0,8).map(p=>({t:thumbTime(p),kind:'preview',src:'/media/'+encodeURIComponent(p)}));}
function renderStrip(x,ev){const strip=$('#strip'),gen=String(mediaGen);strip.dataset.gen=gen;if(ev===undefined){strip.innerHTML='<div class="thumb ghost"></div>'.repeat(8);return;}const frames=stripFrames(x,ev),label=(x.end_seconds-x.start_seconds)<30?mmssTenth:mmss,ms=isScene(x)?momentsOf(x):null,badge=t=>{if(!ms||t==null)return '';const k=momentIndex(t,ms);return k>=0?`<i>${k+1}</i>`:'';};strip.innerHTML=frames.map((f,i)=>`<button type="button" class="thumb${f.kind==='strongest'?' peak':f.kind==='seed'?' hit':''}" data-i="${i}" data-t="${f.t==null?'':f.t}" aria-label="Khung ${f.t==null?i+1:label(f.t)}"><img alt="" loading="lazy" decoding="async"${f.src?` src="${esc(f.src)}"`:''}>${f.t==null?'':`<span>${label(f.t)}</span>`}${badge(f.t)}</button>`).join('');const remote=frames.map((f,i)=>({f,i})).filter(v=>v.f.remote);if(!remote.length)return;const peak=remote.find(v=>v.f.kind==='strongest')||remote[0],order=[peak,...remote.filter(v=>v!==peak)],promises=queueFrames(x.id,order.map(v=>v.f.t),true);order.forEach((v,k)=>promises[k].then(obj=>{if(!obj||strip.dataset.gen!==gen)return;const img=strip.querySelector(`.thumb[data-i="${v.i}"] img`);if(img)img.src=obj;if(v===peak&&pstate.id===x.id&&!pstate.reveal)setPoster(obj);}));}
function tlPos(t){const len=Math.max(.001,pstate.end-pstate.start);return 2+96*Math.min(1,Math.max(0,(Number(t)-pstate.start)/len));}
function thin(values,limit){if(values.length<=limit)return values;const out=[];for(let s=0;s<limit;s++)out.push(values[Math.floor((s+.5)*values.length/limit)]);return out;}
function renderTimeline(x,ev){const bar=(a,b,cls,extra='')=>{const left=tlPos(a),right=tlPos(b);return `<div class="${cls}"${extra} style="left:${left.toFixed(2)}%;width:${Math.max(.6,right-left).toFixed(2)}%"></div>`;};const segments=ev?.detected_intervals?.length?ev.detected_intervals:(x.detected_intervals||[]).map(d=>({start:d.start_seconds,end:d.end_seconds}));let html;if(isScene(x)){html='<div class="gapline" style="left:2%;width:96%"></div>'+momentsOf(x).map((m,i)=>bar(m.start,m.end,'mo',` data-i="${i}" title="Khoảnh khắc ${i+1}: ${mmss(m.start)}–${mmss(m.end)}"`)).join('');}else html=(segments.length?segments:[{start:x.start_seconds,end:x.end_seconds}]).map(s=>bar(s.start,s.end,'seg')).join('');const seeds=ev?.seeds;if(seeds&&!seeds.known)html+=(seeds.windows||[]).map(w=>bar(w.start,w.end,'win')).join('');const ticks=seeds?.known?thin((seeds.samples||[]).map(s=>s.t),120):(ev?.frames||[]).filter(f=>f.kind==='seed').map(f=>f.t);html+=ticks.map(t=>`<div class="hit" style="left:${tlPos(t).toFixed(2)}%"></div>`).join('');const peak=ev?.strongest?.t??thumbTime((x.preview_images||[])[0]);if(peak!=null)html+=`<div class="peak" style="left:${tlPos(peak).toFixed(2)}%" title="Rõ nhất lúc ${mmss(peak)}"></div>`;html+='<div class="head" id="thead" hidden></div>';$('#timeline').innerHTML=html;markMoment();}
function renderRegionMedia(x,force){const owner=regionOwner(x),r=owner&&(owner.suggested_region_source_pixels||owner.decision_region_source_pixels),key=JSON.stringify([x.id,owner?.id||null,r||null,(x.preview_images||[]).slice(0,3),x.evidence_regions||null]);if(!force&&key===regionKey)return;regionKey=key;const box=$('#media-region');box.innerHTML=regionMediaHtml(x,owner,r);drawRegionPreviews(box);}
function regionMediaHtml(x,owner,r){const images=(x.preview_images||[]).slice(0,3),src=p=>'/media/'+encodeURIComponent(p);if(!images.length)return '<div class="no-media">Không có ảnh xem trước cho mục này.</div>';if(sceneLogo(x)&&owner&&owner.id!==x.id&&r&&r!=='FULL_FRAME'&&Array.isArray(owner.source_frame_size))return evidenceMediaHtml(x,images,src,owner,r);if(!owner||!r||r==='FULL_FRAME'||!Array.isArray(owner.source_frame_size))return evidenceMediaHtml(x,images,src);const data=p=>`data-src="${src(p)}" data-x="${Number(r.x)}" data-y="${Number(r.y)}" data-w="${Number(r.width)}" data-h="${Number(r.height)}" data-sw="${Number(owner.source_frame_size[0])}" data-sh="${Number(owner.source_frame_size[1])}"`;return `<canvas class="region-frame" ${data(images[0])}></canvas><canvas class="region-crop" ${data(images[0])}></canvas>${images.length>1?`<div class="region-more">${images.slice(1).map(p=>`<canvas class="region-frame" ${data(p)}></canvas>`).join('')}</div>`:''}`;}
function evidenceMediaHtml(x,images,src,approved,r){const shots=`<div class="shots">${images.map(p=>`<img src="${src(p)}" loading="lazy" alt="">`).join('')}</div>`;if(x.category!=='visual_logo')return shots;const boxes=(Array.isArray(x.evidence_regions)?x.evidence_regions:[]).filter(b=>b&&Number(b.width)>0&&Number(b.height)>0),size=x.evidence_frame_size||x.source_frame_size||approved?.source_frame_size,marks=boxes.map(b=>({x:Number(b.x),y:Number(b.y),w:Number(b.width),h:Number(b.height),c:b.covered_by?1:0,o:String(b.covered_by||'')})),own=approved&&r&&r!=='FULL_FRAME'&&Array.isArray(size)&&Array.isArray(approved.source_frame_size)&&Number(approved.source_frame_size[0])>0&&Number(approved.source_frame_size[1])>0;if(own){const fx=Number(size[0])/Number(approved.source_frame_size[0]),fy=Number(size[1])/Number(approved.source_frame_size[1]);marks.push({x:Number(r.x)*fx,y:Number(r.y)*fy,w:Number(r.width)*fx,h:Number(r.height)*fy,a:1,o:String(approved.id)});}if(!marks.length||!Array.isArray(size))return `<div class="evidence-legend">Không có khung: AI không định vị vùng logo nào trong ảnh này; thẻ hỏi về cả cảnh.</div>${shots}`;const data=esc(JSON.stringify(marks)),covered=boxes.some(b=>b.covered_by),legend=[];if(boxes.length)legend.push(`Khung vàng: ${boxesFromMemory(boxes)?'vùng được định vị (bộ nhớ thương hiệu)':'vùng AI định vị'}, chỉ để tham khảo — không phải vùng sẽ làm mờ${covered?' · watermark đã có thẻ riêng':''}`);if(own)legend.push(`Khung đỏ: vùng ${esc(readingLabel(approved,'logo/watermark'))} đã được duyệt làm mờ ở thẻ riêng`);return `<div class="evidence-legend">${legend.join(' · ')}</div>${images.map(p=>`<canvas class="evidence-frame" data-src="${src(p)}" data-boxes="${data}" data-sw="${Number(size[0])}" data-sh="${Number(size[1])}"></canvas>`).join('')}`;}
function drawEvidencePreviews(root){root.querySelectorAll('canvas.evidence-frame').forEach(canvas=>{const image=new Image();image.onload=()=>{if(!canvas.isConnected)return;let boxes=[];try{boxes=JSON.parse(canvas.dataset.boxes||'[]');}catch(_error){}const sourceW=Number(canvas.dataset.sw),sourceH=Number(canvas.dataset.sh),ctx=canvas.getContext('2d');canvas.width=Math.min(960,image.naturalWidth);canvas.height=Math.round(canvas.width*image.naturalHeight/image.naturalWidth);ctx.drawImage(image,0,0,canvas.width,canvas.height);if(!(sourceW>0&&sourceH>0))return;const scale=Math.min(image.naturalWidth/sourceW,image.naturalHeight/sourceH),offsetX=(image.naturalWidth-sourceW*scale)/2,offsetY=(image.naturalHeight-sourceH*scale)/2,k=canvas.width/image.naturalWidth,tagged=new Set(boxes.filter(b=>b.a).map(b=>b.o)),tagY=(by,b)=>by>16?by-5:by+b.h*scale*k+14;ctx.font='bold 13px system-ui,sans-serif';ctx.lineWidth=3;ctx.strokeStyle='#ffc233';ctx.fillStyle='#ffc233';for(const b of boxes.filter(b=>!b.a)){const bx=(b.x*scale+offsetX)*k,by=(b.y*scale+offsetY)*k;ctx.setLineDash([10,6]);ctx.strokeRect(bx,by,b.w*scale*k,b.h*scale*k);ctx.setLineDash([]);if(b.c&&!tagged.has(b.o)){tagged.add(b.o);ctx.fillText('watermark — đã có thẻ riêng',bx,tagY(by,b));}}ctx.lineWidth=4;ctx.strokeStyle='#ff304f';ctx.fillStyle='#ff304f';for(const b of boxes.filter(b=>b.a)){const bx=(b.x*scale+offsetX)*k,by=(b.y*scale+offsetY)*k;ctx.strokeRect(bx,by,b.w*scale*k,b.h*scale*k);ctx.fillText('đã duyệt làm mờ ở thẻ riêng',bx,tagY(by,b));}};image.src=canvas.dataset.src;});}
function drawRegionPreviews(root){drawEvidencePreviews(root);root.querySelectorAll('canvas.region-frame,canvas.region-crop').forEach(canvas=>{const image=new Image();image.onload=()=>{if(!canvas.isConnected)return;const sourceW=Number(canvas.dataset.sw),sourceH=Number(canvas.dataset.sh),scale=Math.min(image.naturalWidth/sourceW,image.naturalHeight/sourceH),offsetX=(image.naturalWidth-sourceW*scale)/2,offsetY=(image.naturalHeight-sourceH*scale)/2,sx=Number(canvas.dataset.x)*scale+offsetX,sy=Number(canvas.dataset.y)*scale+offsetY,sw=Number(canvas.dataset.w)*scale,sh=Number(canvas.dataset.h)*scale,ctx=canvas.getContext('2d');if(canvas.classList.contains('region-crop')){const padX=sw*.12,padY=sh*.18,x=Math.max(0,sx-padX),y=Math.max(0,sy-padY),w=Math.min(image.naturalWidth-x,sw+padX*2),h=Math.min(image.naturalHeight-y,sh+padY*2);canvas.width=360;canvas.height=Math.max(100,Math.round(360*h/w));ctx.drawImage(image,x,y,w,h,0,0,canvas.width,canvas.height);ctx.strokeStyle='#ff304f';ctx.lineWidth=5;ctx.strokeRect((sx-x)/w*canvas.width,(sy-y)/h*canvas.height,sw/w*canvas.width,sh/h*canvas.height);}else{canvas.width=Math.min(960,image.naturalWidth);canvas.height=Math.round(canvas.width*image.naturalHeight/image.naturalWidth);ctx.drawImage(image,0,0,canvas.width,canvas.height);const kx=canvas.width/image.naturalWidth,ky=canvas.height/image.naturalHeight;ctx.fillStyle='rgba(255,48,79,.15)';ctx.fillRect(sx*kx,sy*ky,sw*kx,sh*ky);ctx.strokeStyle='#ff304f';ctx.lineWidth=4;ctx.strokeRect(sx*kx,sy*ky,sw*kx,sh*ky);}};image.src=canvas.dataset.src;});}
function renderSide(x){const html=isSafety(x)?safetySide(x):adSide(x),side=$('#side');if(sideItemId===x.id&&side.__html===html)return;side.__html=html;side.innerHTML=html;sideItemId=x.id;reviewStats.sideRenders++;markMoment();}
function markMoment(){const i=pstate.id===focusId?pstate.mi:-1;document.querySelectorAll('#side .mchip,#timeline .mo').forEach(el=>el.classList.toggle('on',Number(el.dataset.i)===i));}
function videoReason(info){if(!mediaKey)return 'Trang này chỉ có ảnh xem trước, không phát video.';const reasons={unsupported_container:'Trình duyệt không phát được định dạng video này; hãy xem dải khung hình.',source_changed:'Video nguồn đã thay đổi sau khi quét; chỉ xem được khung hình.',source_missing:'Không tìm thấy video nguồn.',source_cleaned:'Video gốc đã được dọn vào Thùng rác; chỉ xem được ảnh đã lưu trong report.',source_unknown:'Không rõ video nguồn.',decode_error:'Trình duyệt không giải mã được video này; hãy xem dải khung hình.'};return reasons[info?.reason]||'Không phát được video trong trình duyệt; hãy xem dải khung hình.';}
function currentVideoInfo(ev){return pstate.reason?{reason:pstate.reason}:ev?.video;}
function safetySubtitle(x,ev){const scene=isScene(x),parts=[scene?sceneSpan(x):durationText(x.end_seconds-x.start_seconds)],seeds=ev?.seeds;if(seeds&&seeds.count>0&&!scene){if(seeds.known)parts.push(`máy nghi ngờ ở ${seeds.count} khung`);else{const w=seeds.windows||[];parts.push(w.length===1?`máy nghi ngờ ở ${seeds.count} khung trong ${mmss(w[0].start)}–${mmss(w[0].end)}`:`máy nghi ngờ ở ${seeds.count} khung trong ${w.length} đoạn`);}}const n=(x.detected_intervals||[]).length;if(n>1&&!scene)parts.push(`${n} khoảng phát hiện`);return parts.join(' · ');}
function suggestionLine(x){const parts=[];if(x.studio_logo_match)parts.push(`Khớp logo hãng phim bạn đã xác nhận giữ (giống ${Math.round(100*Number(x.studio_logo_match.similarity||0))}%), không thấy chữ lạ hay quảng cáo`);else if(x.suggested_decision)parts.push(`Đề xuất: ${esc(actionName(x,x.suggested_decision))}`);if(x.suggestion_withheld&&!x.decision&&!x.studio_logo_match)parts.push(esc(studioWithheldLine(x.suggestion_withheld)));if(x.studio_logo_match_blocked&&!x.decision)parts.push(esc(studioBlockedLine(x.studio_logo_match_blocked)));if(x.platform_logo_link&&!x.decision)parts.push(`Logo nền tảng ${esc(x.platform_logo_link.platform||'')} ở đoạn này đã có thẻ riêng làm mờ vùng logo — thẻ này chỉ hỏi về cả cảnh: Giữ nguyên nếu phần còn lại là nội dung phim`);else if(x.covered_by&&!x.decision)parts.push(`Trùng thẻ “${esc(x.covered_by_label||'Logo nền tảng')}” ở danh sách chính — logo đó đã có thẻ làm mờ riêng`);if(x.advisory)parts.push('Ứng viên phụ — không chặn xuất, chỉ áp dụng nếu bạn chọn');return parts.length?`<div class="hint">${parts.join(' · ')}</div>`:'';}
function decideButtons(x){const d=x.decision,full=d==='BLUR'&&x.decision_region_source_pixels==='FULL_FRAME',b=(cls,decision,label,key,selected)=>`<button type="button" class="${cls}${selected?' sel':''}" data-act="decide" data-decision="${decision}"${decision==='BLUR'?' data-full="1"':''} aria-pressed="${selected?'true':'false'}">${label}<small>${selected?'✓ đã chọn':`phím ${key}`}</small></button>`;return `<div class="decide">${b('k','KEEP','Giữ nguyên',1,d==='KEEP')}${b('b','BLUR','Làm mờ cả cảnh',2,full)}${b('c','CUT','Cắt cảnh',3,d==='CUT')}${b('m','NEEDS_MORE_CONTEXT','Cần xem thêm',4,d==='NEEDS_MORE_CONTEXT')}</div>`;}
function decisionLabel(x){if(x.decision==='BLUR'&&x.platform_logo_memory?.remembered){const m=x.platform_logo_memory,name=m.platform&&m.platform.key!=='unknown'&&m.platform.name?` ${m.platform.name}`:'';return `Làm mờ logo · đã nhớ là logo nền tảng${name}`;}if(x.decision==='KEEP'&&x.studio_logo_memory?.remembered){const m=x.studio_logo_memory;return m.frames!=null?`Giữ nguyên · đã nhớ là logo hãng phim (${Number(m.frames)} khung${m.ignored_regions?.length?', bỏ qua watermark đã làm mờ':''})`:'Giữ nguyên · đã nhớ là logo hãng phim';}if(x.decision==='BLUR'&&x.decision_region_source_pixels==='FULL_FRAME')return isScene(x)?`Làm mờ toàn cảnh trong ${momentsOf(x).length} khoảnh khắc`:'Làm mờ toàn cảnh';if(x.decision==='BLUR')return isLogoItem(x)?'Làm mờ logo':'Làm mờ vùng chữ/logo';if(x.decision==='NEEDS_MORE_CONTEXT')return 'Cần xem thêm';return actionName(x,x.decision);}
function chosenLine(x){return x.decision?`<div class="chosen">Đã chọn: <b>${esc(decisionLabel(x))}</b> · <button type="button" class="linkish" data-act="clear">Bỏ chọn</button></div>`:'';}
function momentChips(x){return `<div class="moments" role="group" aria-label="Các khoảnh khắc">${momentsOf(x).map((m,i)=>`<button type="button" class="mchip" data-act="moment" data-i="${i}" title="Phát riêng khoảnh khắc ${i+1}"><b>${i+1}</b>${mmss(m.start)}–${mmss(m.end)}<span>▶</span></button>`).join('')}</div>`;}
function safetySide(x){const ev=evidenceCache.get(x.id),peak=ev?.strongest?.t??thumbTime((x.preview_images||[])[0]),playable=videoAllowed()&&(!ev?.video||ev.video.available),scene=isScene(x),n=momentsOf(x).length,heading=scene?`<h2>${esc(sceneTitle(x))}</h2>`:`<h2>${mmss(x.start_seconds)} – ${mmss(x.end_seconds)}</h2>`,play=scene?`<button type="button" data-act="seq" title="Phát từng khoảnh khắc theo thứ tự, bỏ qua khoảng trống giữa chúng">▶ Phát lần lượt ${n} khoảnh khắc</button>`:'<button type="button" data-act="play">▶ Phát đoạn này</button>',watch=scene?'':'Xem đoạn này rồi chọn bên dưới.';return `<span class="pill">${esc(catName(x).toUpperCase())}</span><div>${heading}<div class="sub">${esc(safetySubtitle(x,ev))}</div></div>${suggestionLine(x)}<div class="focus-box">${peak!=null?`Rõ nhất lúc <b>${mmss(peak)}</b> (khung viền trắng). `:''}${watch}${playable?play:`<small>${esc(videoReason(currentVideoInfo(ev)))}</small>`}${scene?momentChips(x):''}</div>${scene?`<div class="applies">${esc(appliesLine(x))}</div>`:''}${decideButtons(x)}${chosenLine(x)}${techDetails(x,ev,true)}`;}
function scopeShort(x){const scope=decisionScope(x);if(scope.kind==='advisory')return 'Ứng viên phụ — không chặn xuất';if(scope.kind==='track')return trackCoversFullVideo(x)?'Một quyết định cho toàn video':'Một quyết định cho cả khoảng xuất hiện';if(scope.kind==='grouped')return `${(x.detected_intervals||[]).length} khoảnh khắc phát hiện`;return 'Chỉ đoạn này';}
function adSide(x){const owner=regionOwner(x),r=owner&&(owner.suggested_region_source_pixels||owner.decision_region_source_pixels);return `<span class="pill">${esc(catName(x).toUpperCase())}</span><div><h2>${mmss(x.start_seconds)} – ${mmss(x.end_seconds)}</h2><div class="sub">${esc(durationText(x.end_seconds-x.start_seconds))} · ${esc(scopeShort(x))}</div></div>${sceneLogo(x)||!(owner&&r&&r!=='FULL_FRAME')?aiVerdictHtml(x):''}${platformVerdictHtml(x)}${suggestionLine(x)}${sceneLogo(x)?logoPlayHtml(x):''}${overlapCoverage(x)}${regionControlsHtml(x,owner,r)}<div class="decide-head">Quyết định cho toàn cảnh ${esc(catName(x))} · ${span(x)}</div>${decideButtons(x)}${studioHtml(x)}${platformHtml(x)}${chosenLine(x)}${techDetails(x,null,false)}`;}
function aiVerdictHtml(x){if(!x||x.category!=='visual_logo'||x.suggested_region_source_pixels)return '';if(x.candidate_type==='ending_boundary')return endingVerdictHtml(x);const m=x.model_evidence||{},answer=String(m.vlm_answer||'').trim(),promoted=!!m.promoted_from_rejected_boundary,memory=memoryMatch(m),promo=String(m.vlm_scene||'').toUpperCase()==='PROMO_FULL_FRAME',no=/^no\b/i.test(answer),yes=/^yes\b/i.test(answer),boundary=promoted||x.candidate_type==='opening_boundary',boxes=(Array.isArray(x.evidence_regions)?x.evidence_regions:[]).filter(Boolean),covered=boxes.filter(b=>b.covered_by),open=boxes.length>covered.length,who=boxesFromMemory(boxes)?'bộ nhớ thương hiệu':'AI',coveredText=covered.length?` ${covered.length===boxes.length?(boxes.length===1?'Vùng logo duy nhất':'Mọi vùng logo'):covered.length===1?'Một vùng logo':`${covered.length} vùng logo`} ${who} khoanh ở đoạn này là watermark ${[...new Set(covered.map(b=>String(b.covered_by_label||'').trim()||'watermark'))].map(esc).join(', ')} — đã có thẻ riêng; thẻ này chỉ hỏi về cả đoạn.`:'',boundaryText='BiliFlow luôn đưa 5 giây đầu video ra một lần để bạn tự kiểm tra intro ngoài phim; thẻ này không khoanh vùng logo nào — chọn Giữ nguyên nếu là nội dung phim/logo hãng, Cắt cảnh nếu là intro ngoài.';let text;if(memory){const name=memoryBrandName(m);text=`Khớp hình một logo thương hiệu bạn đã duyệt trước đó${name?` (${esc(name)})`:''} — không phải câu trả lời của AI; vẫn cần bạn duyệt lại, quyết định áp dụng cho cả cảnh.${open?` Khung vàng là vùng ${who} định vị, chỉ để tham khảo.`:''}${coveredText}`;}else if(promo&&!yes)text=`AI ${no?'trả lời KHÔNG thấy logo riêng':'chưa chắc có logo riêng'} trong ${span(x)}, nhưng nhận định cả cảnh là quảng cáo / intro toàn khung — chọn Cắt cảnh nếu đúng là quảng cáo hay intro ngoài, Giữ nguyên nếu là nội dung phim.${coveredText}`;else if(promoted||no)text=`AI trả lời KHÔNG thấy logo hay chữ quảng cáo trong ${span(x)}. ${boundary?boundaryText:'Thẻ này không khoanh vùng logo nào — chọn Giữ nguyên nếu là nội dung phim, Cắt cảnh nếu là quảng cáo.'}`;else if(yes||(answer&&m.vlm_confirmation==='CONFIRMED')){text=open?`AI trả lời CÓ thấy logo/thương hiệu trong khung; khung vàng là vùng ${who} khoanh, chỉ để tham khảo — quyết định áp dụng cho cả cảnh.`:covered.length?'AI trả lời CÓ thấy logo/thương hiệu trong khung nhưng không định vị được logo nào khác ngoài watermark đã có thẻ riêng — quyết định áp dụng cho cả cảnh.':'AI trả lời CÓ thấy logo/thương hiệu trong khung nhưng không định vị được vị trí — quyết định áp dụng cho cả cảnh.';text+=coveredText;}else if(!answer)text=`Thẻ cũ: chưa lưu câu trả lời gốc của AI.${boundary?` ${boundaryText}`:''}`;else text=`AI trả lời “${esc(answer)}” (chưa chắc chắn). ${boundary?boundaryText:'Thẻ này không khoanh vùng logo nào — quyết định áp dụng cho cả cảnh.'}`;return `<div class="ai-verdict">${text}</div>`;}
function endingVerdictHtml(x){const m=x.model_evidence||{},tails=(Array.isArray(m.tail_windows)?m.tail_windows:[]).filter(w=>w&&String(w.answer||'').trim()).slice(-3),answers=tails.length?` AI (Qwen) ở các cửa sổ cuối: ${tails.map(w=>`${mmss(w.start_seconds)}–${mmss(w.end_seconds)} “${esc(String(w.answer).trim())}”`).join('; ')}.`:'',boxes=(Array.isArray(x.evidence_regions)?x.evidence_regions:[]).filter(Boolean).length?' Khung vàng là vùng AI định vị ở cuối video, chỉ để tham khảo.':'';return `<div class="ai-verdict">BiliFlow luôn đưa 6 giây cuối video ra một lần để bạn tự kiểm tra logo nền tảng hay đoạn kết ngoài phim; thẻ này không khoanh vùng logo nào — chọn Giữ nguyên nếu là nội dung phim, Cắt cảnh nếu là đoạn kết ngoài phim; nếu chỉ có logo nền tảng (iQIYI, WeTV…) trên nền tối, bấm “Đây là logo nền tảng — làm mờ &amp; nhớ”.${answers}${boxes}</div>`;}
function platformVerdictHtml(x){const p=x&&x.candidate_type==='platform_logo'?x.platform_logo:null;if(!p)return '';const name=esc(p.name||'nền tảng video'),found=Array.isArray(p.detections)?p.detections:[],ocr=found.find(d=>d.kind==='ocr_text'),model=found.find(d=>d.kind!=='ocr_text'&&d.kind!=='platform_memory'),memory=found.filter(d=>d.kind==='platform_memory'),parts=[];if(ocr)parts.push(`OCR đọc “${esc(ocr.text)}” lúc ${mmss(ocr.observed_seconds)} — logo nền tảng ${name}`);else if(model)parts.push(`Mô hình logo đọc “${esc(model.text)}” — logo nền tảng ${name}`);if(memory.length)parts.push(`Khớp logo nền tảng ${name} bạn đã nhớ (giống ${Math.round(100*Math.max(...memory.map(d=>Number(d.similarity)||0)))}%)`);const loose=p.snap&&p.snap.method&&p.snap.method!=='dark_run'?' Không thấy đoạn màn hình đen quanh logo nên khoảng thời gian chỉ là ước lượng — hãy xem kỹ đầu và cuối đoạn.':'';return `<div class="ai-verdict">${parts.join(' · ')||`Logo nền tảng ${name}`}; đề xuất Làm mờ vùng logo ${mmss(x.start_seconds)}–${mmss(x.end_seconds)}, không cắt cảnh — khung đỏ là vùng sẽ làm mờ.${loose}</div>`;}
function memoryMatch(m){return m?.vlm_source==='approved_brand_memory'||/^MEMORY_MATCH\b/i.test(String(m?.vlm_answer||''));}
function memoryBrandName(m){return readingLabel({labels:[String(m?.vlm_answer||'').replace(/^MEMORY_MATCH\s*\|?\s*/i,'')]},'');}
function boxesFromMemory(boxes){return boxes.length>0&&boxes.every(b=>Array.isArray(b.sources)&&b.sources.length>0&&b.sources.every(s=>s==='brand_memory'));}
function readingLabel(o,fallback){const scanner=['Persistent external logo / watermark','Visual brand/logo candidate','Opening boundary review','Ending boundary review','Full-frame promotional material','Full-frame opening promotion / branded intro','Branded end card / channel promotion','Known approved external brand'];return (o?.labels||[]).map(v=>String(v??'').replace(/<\/?s>/g,'').trim()).find(l=>l&&!scanner.includes(l)&&!(l===l.toLowerCase()&&/^[\p{L} ]+$/u.test(l)))||fallback;}
function logoPlayHtml(x){const ev=evidenceCache.get(x.id),playable=videoAllowed()&&(!ev?.video||ev.video.available);return `<div class="focus-box">Thẻ hỏi về cả đoạn ${span(x)}: xem đoạn này rồi chọn bên dưới.${playable?'<button type="button" data-act="play">▶ Phát đoạn này</button>':`<small>${esc(videoReason(currentVideoInfo(ev)))}</small>`}</div>`;}
function studioWithheldLine(w){const head='Không đề xuất sẵn: đoạn mở đầu giống logo hãng phim';if(!Array.isArray(w.window_texts))return `${head}, không thấy website hay số điện thoại. Hãy xem cả chữ trên hình: nếu có tên web/thương hiệu lạ hãy Cắt`;const texts=w.window_texts,total=Math.max(texts.length,Number(w.window_text_count)||0);if(!total)return `${head}, không đọc thấy chữ, website hay số điện thoại nào. Hãy xem rồi tự chọn`;return `${head}, không thấy website hay số điện thoại. Chữ đọc được: ${texts.slice(0,6).join(', ')}${total>6?', …':''} — nếu là tên web/thương hiệu lạ hãy Cắt`;}
function studioBlockedLine(b){const texts=(b.unconfirmed_texts||[]).slice(0,4).join(', ');const why=texts?`có chữ lạ: ${texts}`:(b.ad_evidence||[]).length?'có dấu hiệu quảng cáo':'chưa quét chữ trong đoạn này';return `Hình giống logo hãng phim đã nhớ nhưng ${why} — vẫn để ở danh sách chính, hãy xem kỹ`;}
function studioHtml(x){if(!studioEligible(x))return '';const remembered=x.decision==='KEEP'&&!!x.studio_logo_memory?.remembered;return `<div class="studio-wrap">${studioCompareLine(x)}<button type="button" class="studio${remembered?' sel':''}" data-act="studio" aria-pressed="${remembered}">${remembered?'✓ Đã nhớ là logo hãng phim (giữ nguyên)':'Đây là logo hãng phim — giữ &amp; nhớ'}</button><small>Giữ nguyên đoạn này và nhớ hình logo cùng chữ trên đó${studioTextsNote(x)}. Lần quét sau, thẻ có ảnh trùng khớp từ 95% với một khung bất kỳ của logo này sẽ nằm ở Ứng viên phụ — trừ khi có chữ lạ, website hay lớp phủ (banner) nằm ngoài vùng watermark đã làm mờ; khi đó thẻ vẫn ở danh sách chính.${studioFramesNote(x)}</small>${studioMaskNote(x)}</div>`;}
function platformEligible(x){return !!x&&x.category==='visual_logo'&&(x.candidate_type==='platform_logo'||studioEligible(x));}
function platformRemembered(x){return x.decision==='BLUR'&&x.platform_logo_memory?.remembered?x.platform_logo_memory:null;}
function platformHtml(x){if(!platformEligible(x))return '';const m=platformRemembered(x);return `<div class="studio-wrap"><button type="button" class="studio platform${m?' sel':''}" data-act="platform" aria-pressed="${!!m}">${m?'✓ Đã nhớ là logo nền tảng (làm mờ)':'Đây là logo nền tảng — làm mờ &amp; nhớ'}</button><small>${platformNote(x,m)}</small></div>`;}
function platformNote(x,m){if(m){if(m.logo_frames==null)return '';const name=m.platform&&m.platform.key!=='unknown'&&m.platform.name?` ${esc(m.platform.name)}`:'';return `Đã nhớ logo nền tảng${name}: ${Number(m.logo_frames)} khung có logo trên nền tối. Lần quét sau, đầu hoặc cuối tập khác có hình trùng khớp sẽ thành thẻ Làm mờ vùng logo — vẫn chờ bạn duyệt.`;}const where=x.suggested_region_source_pixels?'vùng khoanh đỏ':'vùng logo BiliFlow tìm thấy trên nền tối trong đoạn này (không làm mờ cả khung; khoảng thời gian thu lại đúng lúc logo hiện)';return `Làm mờ ${where} và nhớ hình logo. Lần quét sau, logo nền tảng trùng khớp ở đầu hoặc cuối tập khác sẽ thành thẻ Làm mờ — vẫn chờ bạn duyệt. Dùng cho logo nền tảng phát hành (iQIYI, WeTV…), không dùng cho logo hãng phim hay giấy phép.`;}
function studioCompareLine(x){const c=x.studio_logo_compared;if(!c||x.decision||x.studio_logo_match)return '';const pct=Math.floor(100*Number(c.best_similarity||0)),need=Math.round(100*Number(c.minimum_similarity??.95)),cells=c.best_cell_difference,limit=Number(c.maximum_cell_difference??20),grid=pct>=need&&cells!=null&&Number(cells)>limit?` nhưng lệch màu ${Number(cells)} (cần ≤ ${limit})`:'';return `<small class="studio-note">Đã so với ${Number(c.records)||0} logo hãng phim bạn đã nhớ: giống nhất ${pct}% (cần ≥ ${need}%)${grid} — chưa khớp${Number(c.masked_regions)>0?' (đã bỏ qua vùng watermark đã làm mờ)':''}</small>`;}
function studioRemembered(x){return x.decision==='KEEP'&&x.studio_logo_memory?.remembered?x.studio_logo_memory:null;}
function studioFramesNote(x){const m=studioRemembered(x);if(m){if(m.frames==null)return '';if(m.frames_missing)return ' Không còn ảnh khung hình đã nhớ (state/studio-logo-frames) nên không cập nhật được vùng watermark — logo này tạm thời không khớp thẻ nào cho tới khi bạn nhớ lại nó.';if(m.frames_source==='source_video')return ` Đã nhớ ${Number(m.frames)} khung trong đoạn ${span(x)}.`;if(m.frames_source)return ` Chỉ nhớ ${Number(m.frames)} ảnh xem trước (không đọc được video gốc), không phải cả đoạn ${span(x)} — tập khác có thể không khớp.`;return ` Chỉ nhớ ${Number(m.frames)} ảnh xem trước của thẻ này, không phải cả đoạn ${span(x)}.`;}const shots=(x.preview_images||[]).slice(0,8),times=shots.map(thumbTime).filter(t=>t!=null).map(mmssTenth);return ` Sẽ nhớ mọi khung hình trong đoạn ${span(x)} (giải mã lại từ video gốc đúng như lúc quét, tối đa 250 khung); nếu không đọc được video gốc thì chỉ nhớ ${shots.length} ảnh xem trước${times.length?` (lúc ${times.join(', ')})`:''}.`;}
function studioMaskNote(x){const m=studioRemembered(x);if(m&&m.frames==null)return '';if(m&&m.frames_source){const parts=[];if(m.mask_refused)parts.push('Vùng watermark đã làm mờ quá lớn (trên 20% khung hình) nên không bỏ qua — logo được nhớ nguyên ảnh.');else if((m.ignored_regions||[]).length)parts.push(`Đang bỏ qua ${m.ignored_regions.length} vùng watermark đã làm mờ.`);if(m.mask_updated_at)parts.push('Đã cập nhật logo hãng phim đã nhớ theo vùng watermark bạn vừa chọn.');return parts.length?`<small class="studio-note">${parts.join(' ')}</small>`:'';}const overlays=(queue?.items||[]).filter(o=>o.id!==x.id&&o.candidate_type==='persistent_overlay'&&Number(o.start_seconds)<Number(x.end_seconds)&&Number(o.end_seconds)>Number(x.start_seconds)),blurred=overlays.filter(o=>o.decision==='BLUR'&&o.decision_region_source_pixels!=='FULL_FRAME'&&(o.category==='text'||o.category==='visual_logo')),pending=overlays.length>blurred.length||(x.evidence_regions||[]).some(b=>b&&b.covered_by&&!blurred.some(o=>o.id===b.covered_by));if(m)return pending||blurred.length?'<small class="studio-note">Ảnh đã nhớ đang có watermark/lớp phủ — tập không có lớp phủ này sẽ không khớp.</small>':'';const notes=[];if(blurred.length)notes.push(`<small class="studio-note">Sẽ bỏ qua ${blurred.length} vùng watermark bạn đã chọn làm mờ (${blurred.map(o=>esc(readingLabel(o,'watermark'))).join(', ')}) khi so khớp — tập không có watermark hoặc có watermark ở đúng chỗ đó vẫn khớp; chữ, website, banner hay lớp phủ ở chỗ khác vẫn giữ thẻ ở danh sách chính.</small>`);if(pending)notes.push('<small class="studio-note">Ảnh đang có watermark/lớp phủ chưa được chọn Làm mờ. Nếu bạn chọn Làm mờ thẻ watermark đó (trước hay sau khi bấm nút này), BiliFlow sẽ tự bỏ qua vùng đó trong logo đã nhớ; nếu không, tập không có lớp phủ này sẽ không khớp.</small>');return notes.join('');}
function studioTextsNote(x){const texts=x.studio_logo_memory?.remembered?[]:(x.suggestion_withheld?.window_texts||[]);return texts.length?` (sẽ nhớ cả chữ: ${esc(texts.slice(0,6).join(', '))}${texts.length>6?', …':''} — nếu trong đó có tên web/thương hiệu lạ, đừng bấm nút này mà hãy Cắt)`:'';}
function techDetails(x,ev,withRegionControls){const owner=regionOwner(x),r=owner&&(owner.suggested_region_source_pixels||owner.decision_region_source_pixels),category=x.review_kind||x.category,score=x.max_score==null?'—':Number(x.max_score).toFixed(3),parts=[`<div class="meta">${esc(category)} · ưu tiên ${esc(x.priority||'—')} · điểm ${score} · ${clock(x.start_seconds)}–${clock(x.end_seconds)} · ${esc(x.id)}</div>`,scopeBlock(x)];if(isSafety(x))parts.push(overlapCoverage(x));if(!isSafety(x)||(owner&&r&&r!=='FULL_FRAME'))parts.push(regionDetailHtml(x,owner,r));if(withRegionControls)parts.push(regionControlsHtml(x,owner,r));parts.push(labelsReasonsHtml(x),visualAiHtml(x),evidenceHtml(ev));if(x.model_evidence)parts.push(aiModelLine(x),`<div class="labels">AI cục bộ: ${esc(JSON.stringify(x.model_evidence))}</div>`);return `<details class="tech"${techOpen?' open':''}><summary>Chi tiết kỹ thuật</summary><div class="tech-body">${parts.join('')}</div></details>`;}
function viText(value){const text=String(value??''),names={'The first video window is retained once so external intros are not silently missed':'Luôn giữ 5 giây đầu video một lần để không bỏ sót intro ngoài phim','Opening boundary review':'Kiểm tra đoạn mở đầu','Ending boundary review':'Kiểm tra đoạn kết','Visual brand/logo candidate':'Ứng viên logo/thương hiệu','Local visual-language model confirmed branding/logo evidence':'AI hình ảnh cục bộ xác nhận có logo/thương hiệu trong khung','Visual-language answer was uncertain; human review required':'AI hình ảnh trả lời không chắc chắn; cần người duyệt','Visual-language model rejected this window; retained in exhaustive audit':'AI hình ảnh đã loại cửa sổ này; vẫn lưu trong audit đầy đủ','A visual signature from an earlier human-approved brand item matched; review is still required':'Khớp hình một logo thương hiệu bạn đã duyệt trước đó; vẫn cần duyệt lại','Full-frame opening promotion / branded intro':'Quảng bá / intro thương hiệu toàn khung ở đầu video','Local boundary semantics grouped consecutive full-frame promotional windows':'Các cửa sổ quảng bá toàn khung liên tiếp ở đầu video được gom thành một thẻ','Branded end card / channel promotion':'End card thương hiệu / quảng bá kênh','Temporal boundary evidence grouped branded final windows; one human decision covers the complete end card':'Các cửa sổ thương hiệu ở cuối video được gom; một quyết định áp dụng cho cả end card','Persistent external logo / watermark':'Logo / watermark bên ngoài cố định','Repeated regional visual-brand confirmations were grouped into one human review item':'Nhiều lần xác nhận logo ở cùng vùng được gom thành một thẻ duyệt','Full-frame promotional material':'Quảng cáo / intro toàn khung','Local visual-language model classified the boundary window as full-frame promotional material':'AI hình ảnh cục bộ nhận định đoạn đầu/cuối video là quảng cáo toàn khung','Known approved external brand':'Thương hiệu bên ngoài bạn đã duyệt'};return names[text]||text;}
function labelsReasonsHtml(x){const labels=(x.labels||[]).map(viText),reasons=(x.reasons||[]).map(viText);return `${labels.length?`<div class="labels">Nhãn: ${esc(labels.join(', '))}</div>`:''}${reasons.length?`<div class="labels">Lý do: ${esc(reasons.join(' · '))}</div>`:''}`;}
function aiModelLine(x){const m=x.model_evidence||{};if(x.category!=='visual_logo'||!('vlm_confirmation' in m||m.vlm_answer))return '';const score=x.max_score==null?'—':Number(x.max_score).toFixed(3),scenes={PROMO_FULL_FRAME:'quảng cáo / intro toàn khung',MOVIE_CONTENT:'nội dung phim',UNCERTAIN:'chưa chắc'},scene=m.vlm_scene?`; AI (Qwen) về cả cảnh: ${esc(scenes[m.vlm_scene]||m.vlm_scene)} (${esc(m.vlm_scene)})`:'';if(memoryMatch(m)){const name=memoryBrandName(m);return `<div class="labels">Bộ nhớ thương hiệu: khớp hình logo bạn đã duyệt trước đó${name?` (${esc(name)})`:''}, AI không được hỏi về logo; trạng thái ${esc(m.vlm_confirmation||'—')}${scene}; điểm ${score} là điểm xếp hạng (độ giống với bộ nhớ hoặc điểm hình học), không phải độ tin cậy AI</div>`;}return `<div class="labels">AI (Qwen): trả lời ${m.vlm_answer?`“${esc(m.vlm_answer)}”`:'— (thẻ cũ, chưa lưu câu trả lời gốc)'}; trạng thái ${esc(m.vlm_confirmation||'—')}${m.promoted_from_rejected_boundary?' (đổi từ câu trả lời KHÔNG để giữ 5 giây đầu)':''}${scene}; điểm ${score} là điểm hình học, không phải độ tin cậy AI</div>`;}
function evidenceHtml(ev){if(!ev)return '';const seeds=ev.seeds||{},windows=seeds.windows||[],context=ev.context||{},rows=[`Ngưỡng máy dò ${seeds.threshold??'—'} · lấy mẫu ${ev.sample_fps??'—'} khung/giây · ${seeds.known?'đã lưu thời điểm từng khung nghi ngờ':'bản quét cũ: chưa lưu thời điểm từng khung nghi ngờ'}`];if(ev.strongest)rows.push(`Điểm cao nhất ${Number(ev.strongest.score??0).toFixed(3)} lúc ${clock(ev.strongest.t)}`);if(windows.length)rows.push(`Cửa sổ máy dò: ${windows.map(w=>`${clock(w.start)}–${clock(w.end)} (${w.count} khung)`).join('; ')}`);if((context.extended||[]).length)rows.push(`Mở rộng theo ngữ cảnh: ${context.extended.map(w=>`${clock(w.start)}–${clock(w.end)}`).join('; ')}${context.threshold!=null?` (ngưỡng ${context.threshold})`:''}`);if(ev.ignored_ref_count)rows.push(`${ev.ignored_ref_count} tham chiếu không đọc được đã bỏ qua`);return `<div class="evidence"><strong>Bằng chứng máy dò</strong>${rows.map(v=>`<div>${esc(v)}</div>`).join('')}</div>`;}
function visualAiHtml(x){const a=x.ai_visual_audit;return a?`<div class="visual-ai"><strong>Visual AI:</strong> ${esc(a.classification)} · tin cậy ${Math.round(100*Number(a.confidence||0))}% · đề xuất ${esc(actionName(x,a.suggested_decision))} · vùng ${esc(a.region_assessment)}<br>${esc(a.reasoning)}</div>`:'';}
function regionDetailHtml(x,owner,r){const borrowed=owner&&owner.id!==x.id,regionStatus=regionName(owner);return owner&&r&&r!=='FULL_FRAME'?`<div class="region-detail">Chỉ nội dung nằm trong khung đỏ này đang được phân loại. Vùng khoanh đỏ: <strong>${esc(regionStatus)}</strong> · x=${Number(r.x)}, y=${Number(r.y)}, rộng=${Number(r.width)}, cao=${Number(r.height)}${borrowed?` · vùng liên kết áp dụng ${clock(owner.start_seconds)}–${clock(owner.end_seconds)}`:''}</div>`:'<div class="region-detail">Chưa có vùng được định vị nên không thể phân loại logo hay tiêu đề một cách an toàn.</div>';}
function regionControlsHtml(x,owner,r){if(!owner||!r||r==='FULL_FRAME')return '';const borrowed=owner.id!==x.id,ownerDecision=owner.decision;return `<div class="decision-block"><strong>${borrowed?'Xử lý riêng vùng logo khoanh đỏ':'Phân loại vùng khoanh đỏ'}</strong>${borrowed?`<small>Vùng logo áp dụng ${clock(owner.start_seconds)}–${clock(owner.end_seconds)}. Quyết định toàn cảnh bên dưới chỉ áp dụng ${clock(x.start_seconds)}–${clock(x.end_seconds)}; nếu chọn Cắt cả cảnh, đoạn bị cắt không cần làm mờ.</small>`:'<small>Chỉ lựa chọn theo phần nằm trong khung đỏ, không theo logo hoặc chữ ở vị trí khác trong ảnh.</small>'}<div class="region-decide"><button type="button" class="rk${ownerDecision==='KEEP'?' sel':''}" data-act="region" data-owner="${esc(owner.id)}" data-decision="KEEP">Đây là tiêu đề/nội dung phim — giữ lại</button><button type="button" class="rb${ownerDecision==='BLUR'?' sel':''}" data-act="region" data-owner="${esc(owner.id)}" data-decision="BLUR">Đây là logo thương hiệu — làm mờ</button></div></div>`;}
function actionName(x,decision){if(decision==='KEEP')return 'Giữ nguyên';if(decision==='CUT')return 'Cắt cả cảnh';if(decision==='BLUR')return x.suggested_region_source_pixels?(isLogoItem(x)?'Làm mờ logo':'Làm mờ vùng chữ/logo'):'Làm mờ toàn cảnh';if(decision==='NEEDS_MORE_CONTEXT')return 'Cần xem thêm';return decision;}
function trackCoversFullVideo(x){const duration=Number(queue.source?.duration_seconds||0),tolerance=Math.max(1.5,duration*.0005);return duration>0&&Number(x.start_seconds)<=tolerance&&Number(x.end_seconds)>=duration-tolerance;}
function decisionScope(x){const intervals=Array.isArray(x.detected_intervals)?x.detected_intervals:[],from=clock(x.start_seconds),to=clock(x.end_seconds);if(x.advisory)return{kind:'advisory',title:'Ứng viên kiểm tra thêm — chưa thuộc quyết định chính',detail:`Bằng chứng chưa đủ để ghép mục này vào track chính. Thẻ chính khác không tự xử lý mục này. Nếu bạn chọn một hành động, mục sẽ được đưa vào kế hoạch và chỉ áp dụng ${from}–${to}.`};if(x.candidate_type==='persistent_overlay'||x.temporal_policy==='continuous_persistent_overlay'){const full=trackCoversFullVideo(x),support=Number(x.supporting_candidate_count||0);return{kind:'track',title:full?'QUYẾT ĐỊNH TOÀN VIDEO':'QUYẾT ĐỊNH TOÀN KHOẢNG XUẤT HIỆN',detail:`Một lựa chọn cho vùng khoanh đỏ áp dụng từ ${from} đến ${to}${full?' — toàn bộ video':''}. ${support?`Track này đại diện thêm ${support} lần phát hiện cùng vùng đã lưu trong Audit. `:''}Logo ở vị trí hoặc track khác vẫn cần quyết định riêng.`};}if(x.temporal_policy==='discrete_detected_intervals'&&intervals.length>1)return{kind:'grouped',title:`${(SCENE_WORDS[x.category]||'Nhóm sự kiện').toUpperCase()} — ${intervals.length} KHOẢNH KHẮC`,detail:`Một lựa chọn được áp dụng riêng cho ${intervals.length} khoảnh khắc phát hiện trong ${from}–${to} (${momentsOf(x).map(m=>`${clock(m.start)}–${clock(m.end)}`).join('; ')}); các khoảng trống giữa chúng không bị cắt hoặc làm mờ.`};if(intervals.length>1)return{kind:'grouped',title:`Đại diện cho ${intervals.length} lần phát hiện đã gom`,detail:`Các lần phát hiện gần nhau đã được gom thành cửa sổ ${from}–${to}; quyết định áp dụng toàn bộ cửa sổ này. Không tự lan sang cảnh khác.`};return{kind:'single',title:'CHỈ ĐOẠN HIỆN TẠI',detail:`Quyết định chỉ áp dụng ${from}–${to}. Đây không phải lựa chọn đại diện cho mọi quảng cáo hoặc logo cùng loại trong toàn phim.`};}
function scopeBlock(x){const scope=decisionScope(x);return `<div class="scope-detail ${scope.kind}"><strong>Phạm vi áp dụng: ${esc(scope.title)}</strong>${esc(scope.detail)}</div>`;}
function regionOverlap(a,b){if(!a||!b||a==='FULL_FRAME'||b==='FULL_FRAME')return 0;const left=Math.max(a.x,b.x),top=Math.max(a.y,b.y),right=Math.min(a.x+a.width,b.x+b.width),bottom=Math.min(a.y+a.height,b.y+b.height),intersection=Math.max(0,right-left)*Math.max(0,bottom-top),smaller=Math.min(a.width*a.height,b.width*b.height);return smaller?intersection/smaller:0;}
function overlapCoverage(x){const covered=queue.items.filter(other=>other.id!==x.id&&other.decision==='BLUR'&&other.start_seconds<x.end_seconds&&other.end_seconds>x.start_seconds);if(!covered.length)return '';const persistent=covered.filter(other=>other.candidate_type==='persistent_overlay'&&other.decision_region_source_pixels&&other.decision_region_source_pixels!=='FULL_FRAME');const full=covered.filter(other=>other.decision_region_source_pixels==='FULL_FRAME');const parts=[];if(persistent.length){const owner=persistent[0],r=owner.decision_region_source_pixels,current=x.suggested_region_source_pixels||x.decision_region_source_pixels,label=esc(readingLabel(owner,viText((owner.labels||[])[0]||'')||'logo/watermark')),scope=trackCoversFullVideo(owner)?'toàn video':`${clock(owner.start_seconds)}–${clock(owner.end_seconds)}`;if(!current){const drawn=!isSafety(x)&&regionOwner(x)?.id===owner.id;parts.push(`Track <strong>${label}</strong> đã được duyệt làm mờ ${scope} ở thẻ riêng${drawn?' (khung đỏ trong ảnh)':''}. Thẻ này không có vùng riêng — quyết định bên dưới áp dụng cho cả đoạn ${span(x)}.`);}else{const same=regionOverlap(r,current)>=.6;parts.push(same?`Track <strong>${label}</strong> cùng vùng này đã được duyệt làm mờ ${scope}; thẻ hiện tại chỉ là bằng chứng hỗ trợ.`:`Track <strong>${label}</strong> ở vùng khác đã được duyệt làm mờ ${scope} (x=${Number(r.x)}, y=${Number(r.y)}, rộng=${Number(r.width)}, cao=${Number(r.height)}). Vùng đỏ hiện tại vẫn là ứng viên riêng.`);}}if(full.length)parts.push(`${full.length} đoạn trùng thời gian đã được duyệt làm mờ toàn cảnh.`);return parts.length?`<div class="coverage">${parts.join(' ')}</div>`:'';}
function regionOwner(x){if(x.suggested_region_source_pixels&&Array.isArray(x.source_frame_size))return x;if(x.advisory)return null;return queue.items.find(other=>other.id!==x.id&&isLogoItem(other)&&other.decision==='BLUR'&&(other.suggested_region_source_pixels||other.decision_region_source_pixels)&&Array.isArray(other.source_frame_size)&&other.start_seconds<x.end_seconds&&other.end_seconds>x.start_seconds)||null;}
function regionName(owner){if(owner?.decision==='BLUR')return 'logo thương hiệu đã xác nhận';if(owner?.decision==='KEEP')return 'tiêu đề/nội dung phim đã xác nhận';const names={movie_title:'tiêu đề phim',approved_non_brand:'nội dung phim đã xác nhận',external_brand:'logo thương hiệu',external_brand_candidate:'ứng viên logo thương hiệu',branded_end_card:'end-card thương hiệu',promotional_segment:'đoạn quảng bá',unknown:'chưa phân loại'};return names[owner?.region_classification]||'vùng chưa phân loại';}
function refreshFocusIfChanged(){const x=itemMap.get(focusId);if(!x){focusId=pickFocus();renderFocus();return;}if(hasPlayer(x)&&pstate.id===x.id&&(pstate.start!==Number(x.start_seconds)||pstate.end!==Number(x.end_seconds))){renderFocus();return;}if(!isSafety(x))renderRegionMedia(x,false);renderSide(x);updateNavState();}
async function refreshQueue(){if(!queue||pendingWrites||busy||queueRefreshRunning||document.hidden)return;queueRefreshRunning=true;reviewStats.polls++;const epoch=localEpoch;try{const latest=await requestJson(API+'queue',{cache:'no-store'});if(epoch!==localEpoch||pendingWrites)return;if(queueVersion(latest)!==queueVersion(queue)){reviewStats.pollChanges++;await applyQueueUpdate(latest);}}catch(_error){}finally{queueRefreshRunning=false;}}
async function applyQueueUpdate(latest){if(queueIdentity(latest)!==queueIdentity(queue)){queue=latest;indexQueue();evidenceCache.clear();sticky.clear();undoStack.length=0;pruneFrames(new Set());exportSettingsInitialized=false;try{exportJob=await requestJson(API+'export',{cache:'no-store'});updateExportNotice();resources=await requestJson(API+'resources',{cache:'no-store'});}catch(_error){}initializeExportSettings();render();return;}queue=latest;indexQueue();if(filter==='pending')for(const id of listIds){if(itemMap.get(id)?.decision)sticky.add(id);}updateListStatuses();updateHeader();refreshFocusIfChanged();scheduleResources();}
function loadEvidence(id){if(evidenceCache.has(id))return Promise.resolve(evidenceCache.get(id));if(!mediaKey)return Promise.resolve(null);let pending=evidenceLoading.get(id);if(pending)return pending;reviewStats.evidenceFetches++;pending=requestJson(`${API}evidence?item=${encodeURIComponent(id)}`,{cache:'no-store'}).then(value=>{evidenceCache.set(id,value);if(value?.video&&value.video.available===false){pstate.available=false;pstate.reason=value.video.reason||null;}return value;}).catch(()=>null).finally(()=>evidenceLoading.delete(id));evidenceLoading.set(id,pending);return pending;}
function frameKey(id,t){return `${id}\n${t}`;}
function frameUrl(id,t,key=mediaKey){return `${API}frame?item=${encodeURIComponent(id)}&t=${encodeURIComponent(String(t))}&k=${encodeURIComponent(key||'')}`;}
function queueFrames(id,times,urgent){const promote=[],promises=times.map(t=>{const key=frameKey(id,t);let entry=frameBlobs.get(key);if(!entry){entry={item:id,t,obj:null,queued:true,urgent:false,retries:0,resolve:null};entry.promise=new Promise(resolve=>{entry.resolve=resolve;});frameBlobs.set(key,entry);if(!urgent)frameLater.push(key);}if(urgent&&entry.queued&&!entry.urgent){entry.urgent=true;const k=frameLater.indexOf(key);if(k>=0)frameLater.splice(k,1);promote.push(key);}return entry.promise;});if(promote.length)frameUrgent.unshift(...promote);pumpFrames();return promises;}
async function fetchFrame(key,entry){const used=mediaKey;let r;try{r=await fetch(frameUrl(entry.item,entry.t,used),{cache:'no-store'});}catch(_error){return null;}if(r.ok)return r.blob().catch(()=>null);if(r.status===403&&entry.retries<2&&frameBlobs.get(key)===entry){entry.retries++;reviewStats.frameKeyRetries++;if(used===mediaKey)await refreshSession();if(mediaKey&&mediaKey!==used&&frameBlobs.get(key)===entry)return 'retry';}return null;}
function pumpFrames(){while(frameActive<2){const key=frameUrgent.length?frameUrgent.shift():frameLater.shift();if(key===undefined)return;const entry=frameBlobs.get(key);if(!entry||!entry.queued)continue;entry.queued=false;frameActive++;reviewStats.frameFetches++;fetchFrame(key,entry).then(result=>{frameActive--;if(result==='retry'){entry.queued=true;frameUrgent.unshift(key);}else if(result&&frameBlobs.get(key)===entry){entry.obj=URL.createObjectURL(result);entry.resolve(entry.obj);}else{if(frameBlobs.get(key)===entry)frameBlobs.delete(key);entry.resolve(null);}pumpFrames();});}}
function pruneFrames(keep){for(const [url,entry] of frameBlobs){if(keep.has(entry.item))continue;frameBlobs.delete(url);if(entry.obj)URL.revokeObjectURL(entry.obj);if(entry.queued)entry.resolve(null);}}
function keepFrames(){pruneFrames(new Set([focusId,previousFocusId,nextUndecided(focusId)].filter(Boolean)));}
function schedulePrefetch(){clearTimeout(prefetchTimer);prefetchTimer=setTimeout(prefetchNext,600);}
async function prefetchNext(){const from=focusId,nextId=nextUndecided(from);if(!nextId||!mediaKey)return;const x=itemMap.get(nextId);if(!x||!isSafety(x))return;const ev=await loadEvidence(nextId);if(!ev||focusId!==from||nextUndecided(from)!==nextId)return;const frames=pickFor(x,ev.frames).sort((a,b)=>(b.kind==='strongest')-(a.kind==='strongest'));queueFrames(nextId,frames.map(f=>f.t),false);}
function videoAllowed(){return !!mediaKey&&pstate.available;}
function coverVideo(){playerBox.classList.add('covered');pstate.reveal=false;const head=$('#thead');if(head)head.hidden=true;}
function setPoster(src,loading=false){if(src){if(posterImg.getAttribute('src')!==src)posterImg.src=src;posterImg.hidden=false;}else{posterImg.removeAttribute('src');posterImg.hidden=true;}$('#poster-loading').hidden=!(loading&&!src);}
function revealVideo(){playerBox.classList.remove('covered');posterImg.hidden=true;$('#poster-loading').hidden=true;pstate.reveal=true;}
function showNote(text){const note=$('#pnote');note.textContent=text;note.hidden=false;}
function hideNote(){$('#pnote').hidden=true;}
function pausePlayer(){if(!video.paused)video.pause();}
function setTime(t){const ms=pstate.moments;let label=`${mmss(t)} / đoạn ${mmss(pstate.start)}–${mmss(pstate.end)}`;if(ms){const k=momentIndex(t,ms);label=k>=0?`${mmss(t)} · khoảnh khắc ${k+1}/${ms.length} (${mmss(ms[k].start)}–${mmss(ms[k].end)})`:`${mmss(t)} · giữa hai khoảnh khắc`;}setText($('#ptime'),label);const head=$('#thead');if(head){head.style.left=`${tlPos(t).toFixed(2)}%`;head.hidden=!pstate.reveal;}}
function ensureVideo(){if(!videoAllowed())return false;if(!pstate.loaded){pstate.loaded=true;pstate.srcKey=mediaKey;video.preload='metadata';video.src=`${API}video?k=${encodeURIComponent(mediaKey)}`;}return true;}
function seekTo(t,play){if(!ensureVideo())return false;const id=pstate.id;pstate.want={id,t,play:!!play};const run=()=>{if(pstate.id!==id)return;pstate.seekFor=id;try{video.currentTime=t;}catch(_error){}if(play){const p=video.play();if(p&&p.catch)p.catch(()=>{});}};if(video.readyState>=1)run();else pstate.pending=run;return true;}
function setMoment(i){pstate.mi=i;markMoment();}
function playMoment(i,sequence){const ms=pstate.moments;if(!ms||!ms[i])return;if(!videoAllowed()){showNote(videoReason(currentVideoInfo(evidenceCache.get(pstate.id))));return;}pstate.seq=!!sequence;pstate.stopAt=ms[i].end;setMoment(i);seekTo(ms[i].start,true);}
function playSequence(){playMoment(0,true);}
function nextMomentAfter(t){const ms=pstate.moments||[];return ms.findIndex(m=>m.start>t+.01);}
function momentGuard(){const ms=pstate.moments;if(!ms||video.paused||!pstate.reveal||pstate.seekFor!==pstate.id)return;const t=video.currentTime,k=momentIndex(t,ms);if(pstate.stopAt!=null&&t>=pstate.stopAt-.03){const next=pstate.mi+1;if(pstate.seq&&next<ms.length){playMoment(next,true);return;}video.pause();pstate.stopAt=null;pstate.seq=false;return;}if(k<0){const next=nextMomentAfter(t);if(pstate.seq&&next>=0){playMoment(next,true);return;}video.pause();pstate.stopAt=null;return;}if(k!==pstate.mi)setMoment(k);}
function watchMoments(){if(!pstate.moments||video.paused)return;momentGuard();requestAnimationFrame(watchMoments);}
function playRange(){if(!videoAllowed()){showNote(videoReason(currentVideoInfo(evidenceCache.get(pstate.id))));return;}const ms=pstate.moments;if(ms){const t=video.currentTime,k=pstate.reveal?momentIndex(t,ms):-1;if(k>=0&&t<ms[k].end-.2){pstate.seq=true;pstate.stopAt=ms[k].end;setMoment(k);pstate.want={id:pstate.id,t,play:true};const p=video.play();if(p&&p.catch)p.catch(()=>{});return;}const next=pstate.reveal?nextMomentAfter(t):-1;playMoment(next>0?next:0,true);return;}if(pstate.reveal&&video.currentTime>=pstate.start&&video.currentTime<pstate.end-.2){pstate.want={id:pstate.id,t:video.currentTime,play:true};const p=video.play();if(p&&p.catch)p.catch(()=>{});return;}seekTo(pstate.start,true);}
function togglePlay(){const x=itemMap.get(focusId);if(!x||!hasPlayer(x))return;if(!video.paused){video.pause();return;}playRange();}
function releaseVideo(){pstate.loaded=false;pstate.pending=null;video.pause();video.removeAttribute('src');video.load();coverVideo();}
video.addEventListener('loadedmetadata',()=>{pstate.keyRetries=0;const run=pstate.pending;pstate.pending=null;if(run)run();});
video.addEventListener('seeked',()=>{if(pstate.seekFor===pstate.id){revealVideo();setTime(video.currentTime);}});
video.addEventListener('playing',()=>{if(pstate.seekFor===pstate.id)revealVideo();playerBox.classList.add('playing');if(pstate.moments)requestAnimationFrame(watchMoments);});
video.addEventListener('pause',()=>playerBox.classList.remove('playing'));
video.addEventListener('timeupdate',()=>{if(!pstate.reveal)return;const t=video.currentTime;setTime(t);if(pstate.moments){momentGuard();return;}if(!video.paused&&t>=pstate.end)video.pause();});
video.addEventListener('error',()=>{if(!pstate.loaded)return;const used=pstate.srcKey,w=pstate.want&&pstate.want.id===pstate.id?pstate.want:null,now=video.currentTime,wasPlaying=playerBox.classList.contains('playing'),code=video.error?video.error.code:0,want=w?{t:pstate.reveal&&Number.isFinite(now)&&now>=pstate.start&&now<=pstate.end?now:w.t,play:w.play||wasPlaying}:null;pstate.loaded=false;pstate.pending=null;playerBox.classList.remove('playing');coverVideo();videoFailed(used,want,code);});
function restorePoster(){const x=itemMap.get(focusId);if(!x||!hasPlayer(x)||pstate.reveal)return;const strong=$('#strip .thumb.peak img')?.getAttribute('src');setPoster(strong||previewSrc(x));}
async function probeVideo(key){if(!key)return 0;try{const r=await fetch(`${API}video?k=${encodeURIComponent(key)}`,{headers:{Range:'bytes=0-0'},cache:'no-store'});try{if(r.body)r.body.cancel();}catch(_error){}return r.status;}catch(_error){return 0;}}
async function videoFailed(used,want,code=0){const id=pstate.id;reviewStats.videoErrors++;const status=used&&used!==mediaKey?403:await probeVideo(used);if(status===403&&pstate.keyRetries<2){if(used===mediaKey)await refreshSession();if(mediaKey&&mediaKey!==used){pstate.keyRetries++;if(pstate.id===id&&!pstate.loaded&&want)seekTo(want.t,want.play);return;}}const reason={404:'source_missing',409:'source_changed',410:'source_cleaned',415:'unsupported_container'}[status]||(status>=200&&status<300&&(code===3||code===4)?'decode_error':null);restorePoster();if(!reason){if(pstate.id===id)showNote('Chưa tải được video lúc này (mất kết nối hoặc phiên Review vừa đổi). Bấm ▶ để thử lại; dải khung hình bên dưới vẫn xem được.');return;}pstate.available=false;pstate.reason=reason;playerBox.classList.add('no-video');const x=itemMap.get(focusId);if(x&&hasPlayer(x))renderSide(x);showNote(videoReason({reason}));}
function refreshSession(){if(sessionRefresh)return sessionRefresh;reviewStats.sessionRefreshes++;sessionRefresh=requestJson(API+'session',{cache:'no-store'}).then(s=>{if(s?.token)token=s.token;const before=mediaKey;if(s?.media_key)mediaKey=s.media_key;if(mediaKey!==before){mediaKeyChanged();return true;}return false;}).catch(()=>false).finally(()=>{sessionRefresh=null;});return sessionRefresh;}
function mediaKeyChanged(){reviewStats.mediaKeyChanges++;if(pstate.loaded&&video.paused){pstate.loaded=false;pstate.pending=null;video.removeAttribute('src');video.load();coverVideo();restorePoster();}const x=itemMap.get(focusId);if(!x||!hasPlayer(x))return;playerBox.classList.toggle('no-video',!videoAllowed());if(evidenceCache.get(x.id)&&$('#strip .thumb:not(.ghost) img:not([src])'))renderStrip(x,evidenceCache.get(x.id));renderSide(x);}
function isAdvisoryItem(x){return !!x&&(!!x.advisory||(queue?.advisory_items||[]).includes(x));}
function pushUndo(item,advisory=false){undoStack.push({id:item.id,advisory,prev:{decision:item.decision||null,region:item.decision_region_source_pixels??null,note:item.decision_note??null,studio:!!item.studio_logo_memory?.remembered,platform:!!item.platform_logo_memory?.remembered}});if(undoStack.length>100)undoStack.shift();}
function syncLocalCounts(){queue.counts=countsFrom(queue.items);queue.status=statusFrom(queue.items);}
function applyLocalDecision(item,decision,region,note,studio=false,platform=false){item.decision=decision;item.decision_region_source_pixels=region;item.decision_note=note;item.decided_at=new Date().toISOString();if(studio)item.studio_logo_memory={remembered:true};else delete item.studio_logo_memory;if(platform)item.platform_logo_memory={remembered:true};else delete item.platform_logo_memory;syncLocalCounts();}
function applyLocalClear(item){item.decision=null;item.decision_region_source_pixels=null;item.decision_note=null;item.decided_at=null;delete item.studio_logo_memory;delete item.platform_logo_memory;syncLocalCounts();}
function afterLocalChange(id,advance){if(filter==='pending'&&listIds.includes(id))sticky.add(id);updateListStatuses();updateHeader();if(advance){const next=nextUndecided(id);if(next&&next!==focusId){previousFocusId=focusId;focusId=next;renderFocus();return;}}const x=itemMap.get(focusId);if(x)renderSide(x);updateNavState();}
function setSaveState(){clearTimeout(saveTimer);const el=$('#save-state');if(pendingWrites){setText(el,'Đang lưu…');return;}setText(el,'Đã lưu');saveTimer=setTimeout(()=>setText(el,''),1500);}
function scheduleResources(){clearTimeout(resourcesTimer);resourcesTimer=setTimeout(async()=>{try{resources=await requestJson(API+'resources',{cache:'no-store'});renderExport();}catch(_error){}},1500);}
async function resync(){try{await applyQueueUpdate(await requestJson(API+'queue',{cache:'no-store'}));}catch(error){alert(error.message);}const id=reopenAfterResync;reopenAfterResync=null;if(id&&itemMap.has(id)){if(!listIds.includes(id))setFilter('all');selectItem(id);}}
const WRITE_RETRY_MS=[300,900];
async function postWrite(kind,body){for(let attempt=1;;attempt++){try{return await postJson(kind,body);}catch(error){error.attempts=attempt;const transient=!error.status||error.status>=500;if(!transient||attempt>WRITE_RETRY_MS.length)throw error;reviewStats.writeRetries++;await new Promise(resolve=>setTimeout(resolve,WRITE_RETRY_MS[attempt-1]));}}}
function writeFailureMessage(kind,body,error){const x=itemMap.get(body?.id),where=x?`${catName(x)} ${span(x)}`:String(body?.id||''),what=kind==='clear'?'bỏ chọn':`“${body?.remember_platform_logo?'Đây là logo nền tảng — làm mờ & nhớ':body?.decision==='BLUR'&&body?.full_frame?'Làm mờ cả cảnh':x?actionName(x,body?.decision):String(body?.decision||'')}”`,raw=String(error?.message||''),detail=error?.status>=500&&/WinError|Errno|denied|[\\/]/i.test(raw)?'máy chủ chưa ghi được file hàng đợi (file đang bị đọc hoặc khóa)':raw;return `Chưa lưu được lựa chọn ${what} cho mục ${where}${error?.attempts>1?` (đã thử ${error.attempts} lần)`:''}. Mục này sẽ trở về trạng thái đã lưu trên máy và được mở lại để bạn chọn lại. Chi tiết: ${detail}`;}
function enqueueWrite(kind,body){pendingWrites++;localEpoch++;setSaveState();updateNavState();const run=()=>postWrite(kind,body).then(payload=>{if(pendingWrites===1&&payload&&Array.isArray(payload.items))return applyQueueUpdate(payload);}).catch(error=>{resyncNeeded=true;reviewStats.writeFailures++;if(body?.id)reopenAfterResync=body.id;alert(writeFailureMessage(kind,body,error));}).finally(()=>{pendingWrites--;setSaveState();if(!pendingWrites){if(resyncNeeded){resyncNeeded=false;resync();}scheduleResources();}});writeChain=writeChain.then(run,run);return writeChain;}
async function postJson(kind,body,retried=false){let response;try{response=await fetch(API+kind,{method:'POST',headers:{'Content-Type':'application/json','X-BiliFlow-Token':token},body:JSON.stringify(body)});}catch(_error){throw offlineError();}if(response.status===403&&!retried){await refreshSession();return postJson(kind,body,true);}return readJson(response);}
function sceneBlurMessage(x){const ms=momentsOf(x);return `Làm mờ toàn bộ khung hình trong ${ms.length} khoảnh khắc (tổng ${mmss(momentTotal(ms))})? Khoảng trống giữa các khoảnh khắc giữ nguyên.`;}
const EXPORT_LOCK_MESSAGE='Video đang chờ xuất hoặc đang xuất; hủy lệnh xuất trước khi đổi quyết định.';
const SOURCE_CLEANED_LOCK_MESSAGE='Video gốc đã được dọn vào Thùng rác; trang duyệt chỉ để xem. Chép lại video gốc vào input để sửa quyết định hoặc xuất lại.';
const SOURCE_ARCHIVED_LOCK_MESSAGE='Video gốc đang ở kho lưu trữ; trang duyệt chỉ để xem. Bấm “Khôi phục bản xuất” trên Dashboard để sửa quyết định hoặc xuất lại.';
function formatStamp(value){if(!value)return '';const date=new Date(value);if(Number.isNaN(date.getTime()))return '';return new Intl.DateTimeFormat('vi-VN',{hour:'2-digit',minute:'2-digit',second:'2-digit',day:'2-digit',month:'2-digit',year:'numeric'}).format(date);}
function decisionsLocked(){return ['QUEUED','RENDERING'].includes(exportJob.status)||!!exportJob.source_cleaned||!!exportJob.source_archived;}
function refuseWhileExporting(){if(!decisionsLocked())return false;alert(exportJob.source_cleaned?SOURCE_CLEANED_LOCK_MESSAGE:exportJob.source_archived?SOURCE_ARCHIVED_LOCK_MESSAGE:EXPORT_LOCK_MESSAGE);return true;}
async function decide(id,decision,needsFullFrame=false,note=null,rememberStudio=false,rememberPlatform=false){if(busy||!queue||refuseWhileExporting())return;try{const item=itemMap.get(id);if(!item)throw new Error('Không tìm thấy mục này trong hàng đợi hiện tại.');const ai=item.ai_visual_audit,aiDecision=ai?.suggested_decision,confidence=Number(ai?.confidence||0);if(aiDecision&&confidence>=.9&&decision!==aiDecision&&['KEEP','BLUR','CUT'].includes(aiDecision)&&['KEEP','BLUR','CUT'].includes(decision)){const message=`Visual AI tin cậy ${Math.round(confidence*100)}% đề xuất “${actionName(item,aiDecision)}” vì vùng đỏ được nhận là ${ai.classification||'nội dung phim'}. Bạn vẫn muốn chọn “${actionName(item,decision)}” cho đúng vùng đỏ này?`;if(!confirm(message))return;}let full_frame=false;if(decision==='BLUR'&&needsFullFrame){full_frame=isScene(item)?confirm(sceneBlurMessage(item)):confirm('Bạn có xác nhận làm mờ toàn bộ khung hình trong đoạn này?');if(!full_frame)return;}const platform=!!rememberPlatform&&decision==='BLUR'&&!full_frame&&platformEligible(item);if(decision==='BLUR'&&!full_frame&&!platform&&!item.suggested_region_source_pixels)throw new Error('Mục này chưa có vùng được định vị; hãy chọn Làm mờ cả cảnh.');const studio=!!rememberStudio&&decision==='KEEP'&&studioEligible(item);pushUndo(item,!item.decision&&isAdvisoryItem(item));applyLocalDecision(item,decision,decision==='BLUR'?(full_frame?'FULL_FRAME':item.suggested_region_source_pixels):null,note,studio,platform);afterLocalChange(id,autoNext&&id===focusId);enqueueWrite('decision',platform?{id,decision,full_frame,note,remember_platform_logo:true}:studio?{id,decision,full_frame,note,remember_studio_logo:true}:{id,decision,full_frame,note});}catch(e){alert(e.message);}}
async function clearDecision(id){if(busy||!queue||refuseWhileExporting())return;const item=itemMap.get(id);if(!item||!item.decision)return;pushUndo(item);applyLocalClear(item);afterLocalChange(id,false);enqueueWrite('clear',{id});}
function undo(){if(busy||!queue||refuseWhileExporting())return;const entry=undoStack.pop();if(!entry){updateNavState();return;}const item=itemMap.get(entry.id);if(!item){alert('Mục cần hoàn tác không còn trong hàng đợi hiện tại.');updateNavState();return;}if(entry.advisory){if(item.id!==focusId&&listIds.includes(item.id)){previousFocusId=focusId;focusId=item.id;renderFocus();}else updateNavState();alert(advisoryUndoMessage(item));return;}const prev=entry.prev;if(prev.decision){applyLocalDecision(item,prev.decision,prev.region,prev.note,prev.studio,prev.platform);const body={id:item.id,decision:prev.decision,full_frame:prev.region==='FULL_FRAME',note:prev.note};if(prev.studio)body.remember_studio_logo=true;if(prev.platform)body.remember_platform_logo=true;enqueueWrite('decision',body);}else{applyLocalClear(item);enqueueWrite('clear',{id:item.id});}if(filter==='pending'&&listIds.includes(item.id))sticky.add(item.id);updateListStatuses();updateHeader();if(item.id!==focusId&&listIds.includes(item.id)){previousFocusId=focusId;focusId=item.id;renderFocus();}else renderSide(itemMap.get(focusId)||item);updateNavState();}
function advisoryUndoMessage(x){return `Không hoàn tác được lựa chọn cho ứng viên phụ ${catName(x)} ${span(x)}: khi bạn chọn, mục này đã được chuyển vào danh sách chính. Bỏ chọn lúc này sẽ biến nó thành mục bắt buộc chưa duyệt và chặn xuất video, nên lựa chọn “${decisionLabel(x)}” được giữ nguyên. Nếu muốn đổi, hãy chọn lại Giữ nguyên, Làm mờ, Cắt hoặc Cần xem thêm cho mục này.`;}
function keyDecision(n){const x=itemMap.get(focusId);if(!x)return;const choice={1:['KEEP',false],2:['BLUR',true],3:['CUT',false],4:['NEEDS_MORE_CONTEXT',false]}[n];if(choice)decide(x.id,choice[0],choice[1]);}
function isTyping(target){if(!target||target===document.body)return false;if(target.isContentEditable||target.tagName==='TEXTAREA'||target.tagName==='SELECT')return true;return target.tagName==='INPUT'&&!['checkbox','radio','button','submit','reset'].includes(String(target.type).toLowerCase());}
function spaceActivates(target){return !!(target&&target!==document.body&&target.closest&&target.closest('button,summary,a[href],input,select,textarea,label,[role="button"],[contenteditable="true"]'));}
function onKeyDown(e){if(e.defaultPrevented||e.ctrlKey||e.metaKey||e.altKey||isTyping(e.target))return;const k=e.key;if(k==='Escape'&&(sheetOpen||$('#export-section').open)){closeSheet();$('#export-section').open=false;return;}if(e.repeat&&k!=='ArrowLeft'&&k!=='ArrowRight')return;if(k>='1'&&k<='4'&&k.length===1){e.preventDefault();keyDecision(Number(k));}else if(k==='ArrowLeft'){e.preventDefault();step(-1);}else if(k==='ArrowRight'){e.preventDefault();step(1);}else if(k===' '||k==='Spacebar'){if(spaceActivates(e.target))return;e.preventDefault();togglePlay();}else if(k==='z'||k==='Z'){e.preventDefault();undo();}}
async function runBlocking(task){if(busy)return;busy=true;document.body.classList.add('saving');updateNavState();try{await writeChain;await task();}catch(e){alert(e.message);}finally{busy=false;document.body.classList.remove('saving');updateNavState();}}
function bulkFilters(){return {pending:['pending'],all:['all'],high:['high'],adult:['adult'],gore:['gore'],violence:['violence'],text:['text'],visual_logo:['visual_logo'],ads:['visual_logo','text']}[filter]||null;}
async function bulkKeep(){if(!queue||refuseWhileExporting())return;const filters=bulkFilters();if(!filters){alert('Bộ lọc này không hỗ trợ thao tác hàng loạt.');return;}const count=queue.items.filter(visible).filter(x=>!x.decision).length;if(!count){alert('Không có mục chưa duyệt trong bộ lọc này.');return;}if(!confirm(`Giữ nguyên ${count} mục chưa duyệt đang hiển thị? Thao tác này không blur hoặc cắt video.`))return;await runBlocking(async()=>{let payload=null;for(const value of filters)payload=await postJson('bulk-keep',{filter:value});if(payload)await applyQueueUpdate(payload);scheduleResources();});}
async function bulkAccept(){if(!queue||refuseWhileExporting())return;const filters=bulkFilters();if(!filters){alert('Bộ lọc này không hỗ trợ thao tác hàng loạt.');return;}const count=queue.items.filter(visible).filter(x=>!x.decision&&x.suggested_decision).length;if(!count){alert('Không có đề xuất chưa duyệt trong bộ lọc này.');return;}if(!confirm(`Áp dụng ${count} đề xuất đang hiển thị? Bạn vẫn có thể bỏ chọn từng mục trước khi xuất.`))return;await runBlocking(async()=>{let payload=null;for(const value of filters)payload=await postJson('bulk-accept',{filter:value});if(payload)await applyQueueUpdate(payload);scheduleResources();});}
async function finalizeExport(){if(exportRequestInFlight)return;exportRequestInFlight=true;const panel=$('#export-section');try{await writeChain;if(queue.status!=='READY_FOR_EDIT_PLAN'){alert(EXPORT_GATE_MESSAGE);return;}let selection;try{selection=outputSizeSelection();}catch(e){exportError=e.message;return;}exportError='';if(!confirm(exportConfirmText(selection)))return;panel.open=false;panel.querySelector('summary')?.focus?.();exportNoticeActive=true;showExportNotice('Đang gửi lệnh xuất video…','running');renderExport();try{let response;try{response=await fetch(API+'finalize',{method:'POST',headers:{'Content-Type':'application/json','X-BiliFlow-Token':token},body:JSON.stringify(selection)});}catch(_error){throw offlineError();}exportJob=await readJson(response);queue.export_size_policy=exportJob.export_size_policy||queue.export_size_policy;updateExportNotice();}catch(e){exportNoticeActive=false;exportError=`Không gửi được lệnh xuất: ${e.message}`;showExportNotice(exportError,'error');panel.open=true;$('#finalize')?.focus?.();}}finally{exportRequestInFlight=false;renderExport();}}
document.querySelectorAll('#chips .chip').forEach(b=>b.addEventListener('click',()=>setFilter(b.dataset.filter)));
$('#more-filter').addEventListener('change',e=>{if(e.target.value)setFilter(e.target.value);e.target.blur();});
$('#rows').addEventListener('click',e=>{const row=e.target.closest('.row');if(row)selectItem(row.dataset.id);});
$('#side').addEventListener('click',e=>{const b=e.target.closest('button[data-act]');if(!b)return;const x=itemMap.get(focusId);if(!x)return;const act=b.dataset.act;b.blur();if(act==='decide')decide(x.id,b.dataset.decision,b.dataset.full==='1');else if(act==='seq')playSequence();else if(act==='moment')playMoment(Number(b.dataset.i),false);else if(act==='studio'){if(!(x.decision==='KEEP'&&x.studio_logo_memory?.remembered))decide(x.id,'KEEP',false,null,true);}else if(act==='platform'){if(!platformRemembered(x))decide(x.id,'BLUR',false,null,false,true);}else if(act==='region')decide(b.dataset.owner,b.dataset.decision,false,b.dataset.decision==='KEEP'?'Đã xác nhận vùng khoanh đỏ là tiêu đề hoặc nội dung hợp lệ của phim':'Đã xác nhận vùng khoanh đỏ là logo thương hiệu');else if(act==='clear')clearDecision(x.id);else if(act==='play')playRange();});
$('#side').addEventListener('toggle',e=>{if(e.target.matches&&e.target.matches('details.tech'))techOpen=e.target.open;},true);
$('#strip').addEventListener('click',e=>{const b=e.target.closest('.thumb');if(!b||b.classList.contains('ghost'))return;const img=b.querySelector('img'),t=b.dataset.t===''?null:Number(b.dataset.t);$('#strip').querySelectorAll('.thumb.on').forEach(n=>n.classList.remove('on'));b.classList.add('on');b.blur();if(t!=null&&Number.isFinite(t)){if(pstate.moments){pstate.seq=false;pstate.stopAt=null;setMoment(momentIndex(t,pstate.moments));}setTime(t);if(videoAllowed()){pausePlayer();seekTo(t,false);}}if(img&&img.getAttribute('src')&&!pstate.reveal)setPoster(img.getAttribute('src'));});
$('#timeline').addEventListener('click',e=>{const box=e.currentTarget.getBoundingClientRect();if(!box.width||!videoAllowed())return;const ratio=((e.clientX-box.left)/box.width*100-2)/96;let t=pstate.start+Math.min(1,Math.max(0,ratio))*(pstate.end-pstate.start);const ms=pstate.moments;if(ms){let k=momentIndex(t,ms);if(k<0){k=nextMomentAfter(t);if(k<0)k=ms.length-1;t=ms[k].start;}pausePlayer();pstate.seq=false;pstate.stopAt=null;setMoment(k);}setTime(t);seekTo(t,false);});
$('#play-btn').addEventListener('click',e=>{e.currentTarget.blur();togglePlay();});
playerBox.addEventListener('click',e=>{if(e.target===video||e.target===posterImg)togglePlay();});
$('#prev').addEventListener('click',e=>{e.currentTarget.blur();step(-1);});
$('#next').addEventListener('click',e=>{e.currentTarget.blur();step(1);});
$('#undo').addEventListener('click',e=>{e.currentTarget.blur();undo();});
$('#auto-next').checked=autoNext;$('#auto-next').addEventListener('change',e=>{autoNext=e.target.checked;try{localStorage.setItem('biliflow.review.autoNext',autoNext?'1':'0');}catch(_error){}if(queue)updateHeader();});
$('#mobile-list').addEventListener('click',openSheet);$('#sheet-close').addEventListener('click',closeSheet);$('#sheet-backdrop').addEventListener('click',closeSheet);
document.addEventListener('keydown',onKeyDown);
document.addEventListener('click',e=>{if(!e.detail||!e.target.closest)return;const owner=e.target.closest('button,label');if(owner)setTimeout(()=>{const a=document.activeElement;if(a&&a!==document.body&&owner.contains(a)&&!isTyping(a))a.blur();},0);});
document.addEventListener('click',e=>{const panel=$('#export-section');if(panel.open&&!panel.contains(e.target))panel.open=false;});
document.addEventListener('visibilitychange',()=>{if(document.hidden)pausePlayer();});
window.addEventListener('pagehide',releaseVideo);
if(window.ResizeObserver)new ResizeObserver(()=>document.documentElement.style.setProperty('--hh',`${$('#top').offsetHeight}px`)).observe($('#top'));
load();
setInterval(refreshQueue,3000);
setInterval(async()=>{if(['QUEUED','RENDERING'].includes(exportJob.status)){try{exportJob=await requestJson(API+'export',{cache:'no-store'});renderExport();updateExportNotice();}catch(_error){}}},3000);
</script></body></html>"""
    page = (
        page.replace("__EXPORT_DIALOG_JS__", EXPORT_DIALOG_JS)
        .replace("__EXPORT_SIZE_OPTIONS__", export_size_options_html())
        .replace("__EXPORT_CUSTOM_GB__", EXPORT_CUSTOM_GB_ATTRIBUTES)
    )
    return page.replace("__BILIFLOW_REVIEW_TOKEN__", json.dumps(str(token)).replace("<", "\\u003c"))


# Standalone review UI routes that change decisions (export_guards.standalone_edit_refusal).
STANDALONE_EDIT_ROUTES = frozenset({"/api/decision", "/api/clear", "/api/bulk-keep", "/api/bulk-accept"})


def serve_review_ui(
    *, project_root: Path, queue_path: Path, host: str = "127.0.0.1", port: int = 8765,
) -> None:
    require_loopback_host(host)
    # Imported here: control_center imports this module at load time.
    from biliflow.control_center import _host_allowed as host_allowed

    root = project_root.resolve(strict=True)
    reports_root = (root / "reports").resolve(strict=True)
    queue_path = _inside(reports_root, queue_path, "Queue path").resolve(strict=True)
    token = secrets.token_urlsafe(24)
    write_lock = threading.Lock()
    export_lock = threading.Lock()

    def export_status() -> dict:
        queue = _read_json(queue_path)
        _, _, job_path = review_export_paths(root, queue)
        state = _read_json(job_path) if job_path.exists() else {}
        if state and state.get("status") != "COMPLETED":
            return state  # queued, rendering or failed
        existing = existing_review_export(root, queue)
        if existing is None:
            # Never exported, or the export left output/: start_export renders it again.
            return {"status": "IDLE"}
        return state or {
            "status": "COMPLETED",
            "output": existing[0].relative_to(root).as_posix(),
        }

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
        # Before anything is written: an unfinished review, a video the
        # Control Center owns (exported only from the Dashboard, so its queue,
        # skip and source cleanup stay right) and a missing source are refused.
        # An unreadable Control Center database raises ValueError too.
        refusal = standalone_export_refusal(root, queue)
        if refusal:
            raise ValueError(refusal)
        policy = normalize_output_size_policy(size_mode, max_output_gb)
        planned = {**queue, "export_size_policy": policy}
        plan_path, output_path, job_path = review_export_paths(root, planned)
        state = _read_json(job_path) if job_path.exists() else {}
        in_flight = state.get("status") in {"QUEUED", "RENDERING"}
        existing = None if in_flight else existing_review_export(root, planned)
        if not in_flight and existing is None and os.path.lexists(output_path):
            # Before the queue is written: a file (or a link, even a broken one)
            # no manifest proves is neither the export nor overwritten by a render.
            raise ValueError(EXPORT_PATH_TAKEN_MESSAGE.format(name=output_path.name))
        queue["export_size_policy"] = policy
        _write_json(queue_path, queue)
        if in_flight:
            return state
        if existing is not None:
            if state.get("status") == "COMPLETED":
                return state
            return {
                "status": "COMPLETED",
                "output": existing[0].relative_to(root).as_posix(),
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
        # A client that stops sending its request cannot hold a thread
        # (StreamRequestHandler sets it on the socket; nothing here streams).
        timeout = REQUEST_TIMEOUT_SECONDS

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

        def _host_allowed(self) -> bool:
            # Same DNS-rebinding guard as the Control Center: the page embeds
            # the session token and /media/ serves 18+ thumbnails.
            if host_allowed(self.headers.get("Host"), host):
                return True
            self._json(403, {"error": "Địa chỉ truy cập không hợp lệ"})
            return False

        def do_GET(self) -> None:
            if not self._host_allowed():
                return
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/":
                self._send(200, _interactive_html(token).encode("utf-8"), "text/html; charset=utf-8")
                return
            if parsed.path == "/api/queue":
                # Reads share the write lock: on Windows a decision's
                # Path.replace fails while another thread has the queue open.
                with write_lock:
                    payload = _read_json(queue_path)
                self._json(200, payload)
                return
            if parsed.path == "/api/session":
                self._json(200, {"token": token})
                return
            if parsed.path == "/api/resources":
                with write_lock:
                    payload = review_resource_status(project_root=root, queue_path=queue_path)
                self._json(200, payload)
                return
            if parsed.path == "/api/export":
                with write_lock:
                    payload = export_status()
                self._json(200, payload)
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
            if not self._host_allowed():
                return
            if self.headers.get("X-BiliFlow-Token") != token:
                self._json(403, {"error": "Phiên review không hợp lệ"})
                return
            try:
                length = content_length(self.headers)
                if length > 65536:
                    raise ValueError("Dữ liệu gửi lên quá lớn")
                body = json.loads(self.rfile.read(length) or b"{}")
                with write_lock:
                    if self.path in STANDALONE_EDIT_ROUTES:
                        # Checked before any write: the queue stays byte-identical
                        # while the Control Center exports, cleaned or skipped it.
                        refusal = standalone_edit_refusal(root, _read_json(queue_path))
                        if refusal:
                            raise ValueError(refusal)
                    if self.path == "/api/decision":
                        updated = record_review_decision(
                            project_root=root, queue_path=queue_path,
                            item_id=str(body["id"]), decision=str(body["decision"]),
                            note=body.get("note"), full_frame=bool(body.get("full_frame", False)),
                            actor="local_user", transport="local_ui",
                            remember_studio_logo=body.get("remember_studio_logo") is True,
                            remember_platform_logo=body.get("remember_platform_logo") is True,
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
            except (KeyError, TypeError, ValueError, RecursionError) as error:
                # RecursionError: a body nested too deep for json.loads.
                self._json(400, {"error": str(error)})
            except TimeoutError:
                # The body stopped arriving (an OSError, but no disk write failed).
                self.close_connection = True
                with contextlib.suppress(OSError):
                    self._json(408, {"error": REQUEST_TIMEOUT_MESSAGE})
            except OSError:
                self._json(500, {"error": "Không thể ghi lựa chọn xuống ổ đĩa"})

    server = ThreadingHTTPServer((loopback_bind_address(host), port), Handler)
    print(f"BiliFlow review UI: http://{host}:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def approved_operations(payload: dict) -> list[dict]:
    """The edit-plan operations of a fully decided review queue; writes nothing.

    build_edit_plan stores exactly this list as ``approved_operations`` and the
    renderer copies it into the export manifest as ``operations``.
    review_export_paths names the export by their render fields, and an
    existing export is reused or cleaned only when its manifest operations
    match the current ones (export_identity.manifest_problem): an export made
    before that hash has a legacy name that missed some of these fields (for
    example ``decision_blur_edge_mode``).
    """
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
                    "sigma": cover_sigma(region) if isinstance(region, dict) else DEFAULT_SIGMA,
                    "edge_feather_pixels": adaptive_feather,
                    "edge_feather_mode": item.get("decision_blur_edge_mode") or "all_edges",
                    "region_policy": "ocr_union_asymmetric_tight_v3",
                }
                if isinstance(region, dict):
                    # A region (logo, text) is filled from the picture around it, then blurred
                    # (blur_filter.COVER_METHOD); a whole-frame BLUR keeps the plain blur.
                    operation["blur"]["method"] = COVER_METHOD
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
    return sorted(
        [operation for operation in operations if operation["type"] != "cut"] + merged_cuts,
        key=lambda operation: operation["start_seconds"],
    )


def build_edit_plan(*, project_root: Path, queue_path: Path, plan_path: Path) -> dict:
    root = project_root.resolve(strict=True)
    queue_path = _inside((root / "reports").resolve(strict=True), queue_path, "Queue path")
    plan_path = _inside((root / "work").resolve(strict=True), plan_path, "Edit plan path")
    payload = _read_json(queue_path.resolve(strict=True))
    operations = approved_operations(payload)
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
                    method=blur_method(operation), frame_size=(source_width, source_height),
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

