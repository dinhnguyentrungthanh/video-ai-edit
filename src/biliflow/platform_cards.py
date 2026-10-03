"""Review cards for video-platform idents, the forced ending card and their links (batch 4a).

``build_platform_cards`` turns the readings of ``platform_logos`` (OCR tracks,
logo-model labels, remembered-logo runs) into one main-list BLUR card per
ident. ``ensure_forced_ending_card`` shows the last 6 s of a video once, like
the scanner's forced first-window card (the iQIYI outro starts 4.04–4.24 s
before the end and never lines up with the 5 s logo windows).
``link_platform_cards`` lets a whole-scene card that only repeats a platform
ident suggest KEEP and names the platform card on optional duplicates.
Everything here only proposes; a human approves every decision.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Iterable

from biliflow.platform_logos import (
    DECODE_MAX_FRAMES,
    PROBE_MARGIN_SECONDS,
    PROBE_MAX_SECONDS,
    SourceProbe,
    box_from_region,
    bright_box,
    cluster_hits,
    fallback_region,
    ident_span,
    logo_region,
    mask_rects,
    platform_text_hits,
    region_from_box,
    scale_box,
    union_box,
    write_previews,
)
from biliflow.platform_names import PLATFORMS

PLATFORM_LOGO_TYPE = "platform_logo"
ENDING_BOUNDARY_TYPE = "ending_boundary"
FORCED_ENDING_SECONDS = 6.0
# A region-less main card already spanning [D-4.5, D-0.25] shows the ending.
ENDING_COVERED_FROM = 4.5
ENDING_COVERED_TO = 0.25
ENDING_PREVIEW_OFFSETS = (5.5, 2.0, 0.3)
ENDING_LABEL = "Ending boundary review"
ENDING_REASON = (
    "BiliFlow luôn đưa 6 giây cuối video ra một lần để bạn tự kiểm tra logo nền tảng hay đoạn kết "
    "ngoài phim; thẻ này không khoanh vùng logo nào."
)
LINK_MIN_COVERAGE = 0.5
PREVIEW_FOLDER = "platform-logo"
_ANSWER_CHARACTERS = 80
_TEXTS_SHOWN = 3


def card_id(category: str, start: float, end: float, evidence: str) -> str:
    """Provisional id (review_workflow._item_id); build-review assigns the final one."""
    token = f"{category}|{start:.3f}|{end:.3f}|{evidence}".encode("utf-8")
    return f"review-{hashlib.sha256(token).hexdigest()[:12]}"


def clock(seconds: float) -> str:
    """m:ss as the review page shows times (whole seconds, rounded down)."""
    total = max(0, math.floor(float(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def overlay_regions(cards: Iterable[dict], frame_size: tuple[int, int] | None) -> list[dict]:
    """This queue's persistent watermark boxes as relative mask regions."""
    if not frame_size:
        return []
    regions = []
    for card in cards:
        box = box_from_region(card.get("suggested_region_source_pixels"))
        if card.get("candidate_type") != "persistent_overlay" or box is None:
            continue
        size = card.get("source_frame_size")
        width, height = (size if isinstance(size, list) and len(size) == 2 else frame_size)
        if float(width) <= 0 or float(height) <= 0:
            continue
        regions.append({
            "box": [box[0] / float(width), box[1] / float(height),
                    (box[2] - box[0]) / float(width), (box[3] - box[1]) / float(height)],
            "item_id": card.get("id"), "category": card.get("category"),
        })
    return regions


# ------------------------------------------------------------ platform cards


def _group_span(group: dict) -> tuple[float, float]:
    """Seconds a group covers: its readings' windows and the runs it already holds."""
    starts = [hit["window"][0] for hit in group["hits"]] + [run["start"] for run in group["runs"]]
    ends = [hit["window"][1] for hit in group["hits"]] + [run["end"] for run in group["runs"]]
    return min(starts), max(ends)


def _groups(hits: list[dict], runs: Iterable[dict]) -> list[dict]:
    """One group per ident: clustered readings, then each remembered-logo run joins the group
    it overlaps (within 1 s) or starts its own (runs of two records on one ident share a group)."""
    groups = [{"platform": cluster[0]["platform"], "hits": cluster, "runs": []}
              for cluster in cluster_hits(hits)]
    for run in sorted(runs, key=lambda value: (value["start"], value["end"])):
        target = next((
            group for group in groups
            if run["platform"] in {group["platform"], "unknown"}
            and _group_span(group)[0] <= run["end"] + 1.0
            and _group_span(group)[1] >= run["start"] - 1.0
        ), None)
        if target is None:
            target = {"platform": run["platform"], "hits": [], "runs": []}
            groups.append(target)
        target["runs"].append(run)
    return groups


def _source_box(hit: dict, frame_size: tuple[int, int]) -> tuple[float, float, float, float] | None:
    box = hit.get("box_source_px")
    if box is None:
        return None
    size = hit.get("frame_size")
    if isinstance(size, list) and len(size) == 2 and float(size[0]) > 0 and float(size[1]) > 0:
        box = scale_box(tuple(box), frame_size[0] / float(size[0]), frame_size[1] / float(size[1]))
    return tuple(float(value) for value in box)


def _run_box(run: dict, frame_size: tuple[int, int]) -> tuple[float, float, float, float] | None:
    box = run.get("box_relative")
    if not isinstance(box, list) or len(box) != 4:
        return None
    x, y, width, height = (float(value) for value in box)
    if width <= 0 or height <= 0:
        return None
    return (x * frame_size[0], y * frame_size[1], (x + width) * frame_size[0], (y + height) * frame_size[1])


def _detections(hits: list[dict], runs: list[dict]) -> list[dict]:
    detections = []
    for hit in hits:
        detection = {
            "kind": hit["kind"], "text": hit["text"], "observed_seconds": hit["observed_seconds"],
            "label_seconds": hit["label_seconds"], "ref": hit["ref"],
        }
        if hit.get("box_source_px") is not None:
            detection["box_source_px"] = [round(value, 1) for value in hit["box_source_px"]]
        if hit.get("confidence") is not None:
            detection["confidence"] = hit["confidence"]
        detections.append(detection)
    for run in runs:
        detections.append({
            "kind": "platform_memory", "record_key": run.get("record_key"),
            "similarity": run.get("similarity"), "cell_difference": run.get("cell_difference"),
            "start_seconds": run.get("start"), "end_seconds": run.get("end"), "frames": run.get("frames"),
        })
    return detections


def _reason(name: str, hits: list[dict], runs: list[dict], span: dict, start: float, end: float) -> str:
    parts = []
    ocr = next((hit for hit in hits if hit["kind"] == "ocr_text"), None)
    other = next((hit for hit in hits if hit["kind"] != "ocr_text"), None)
    if ocr is not None:
        parts.append(f"OCR đọc “{ocr['text']}” lúc {clock(ocr['observed_seconds'])} — tên nền tảng video {name}")
    elif other is not None:
        parts.append(
            f"Mô hình logo đọc “{other['text']}” trong {clock(other['window'][0])}–{clock(other['window'][1])}"
            f" — tên nền tảng video {name}")
    if runs:
        best = max(run["similarity"] for run in runs)
        parts.append(f"khớp logo nền tảng {name} bạn đã nhớ (giống {best:.0%})")
    text = (
        "; ".join(parts) + ". Đây là logo của nền tảng phát hành, không phải cảnh phim: đề xuất "
        f"Làm mờ vùng logo {clock(start)}–{clock(end)}, không cắt cảnh; chờ bạn duyệt."
    )
    if span["method"] != "dark_run":
        basis = "các lần chuyển cảnh" if span["method"] == "hard_cuts" else "thời điểm đọc được tên"
        text += f" Không thấy đoạn màn hình đen quanh logo ({span.get('reason')}); khoảng thời gian lấy theo {basis}."
    return text


def _preview_moments(region: dict | None, start: float, end: float) -> list[float]:
    """The first logo frame, the fullest one and the last one (three distinct frames when possible)."""
    if region and region["frames"]:
        frames = region["frames"]
        moments = list(dict.fromkeys([frames[0], region["anchor_seconds"], frames[-1]]))
        if len(moments) < 3:
            moments = list(dict.fromkeys([frames[0], frames[len(frames) // 2], frames[-1]]))
        return sorted(moments)
    return [start + (end - start) * share for share in (0.25, 0.5, 0.75)]


def _decodable(probe: SourceProbe, start: float, end: float, focus: float) -> tuple[float, float]:
    """The probe window, shrunk around ``focus`` when its native-rate frames would pass the
    decode cap (a 50/60 fps source); the decode would otherwise silently drop the window's end."""
    info = probe.info()
    if info is None or not info.get("rate"):
        return start, end
    limit = (DECODE_MAX_FRAMES - 2) / float(info["rate"])
    if end - start <= limit:
        return start, end
    start = min(max(start, focus - limit / 2), end - limit)
    return start, start + limit


def _platform_card(
    root: Path, group: dict, *, probe: SourceProbe, duration: float, frame_size: tuple[int, int],
    queue_dir: Path, regions: list[dict],
) -> tuple[dict | None, str | None]:
    hits, runs = group["hits"], group["runs"]
    key = group["platform"]
    name = PLATFORMS[key]["name"] if key in PLATFORMS else next(
        (run.get("name") for run in runs if run.get("name")), "nền tảng video")
    ocr = [hit for hit in hits if hit["kind"] == "ocr_text"]
    best = max(ocr, key=lambda hit: float(hit.get("confidence") or 0.0)) if ocr else (hits[0] if hits else None)
    hint = best["observed_seconds"] if best else runs[0]["hint"]
    last_hint = max([hit["last_observed_seconds"] for hit in hits] + [run["hint"] for run in runs])
    bounds = (
        min([hit["window"][0] for hit in hits] + [run["start"] - 1.0 for run in runs]),
        max([hit["window"][1] for hit in hits] + [run["end"] + 1.0 for run in runs]),
    )
    box_source = next((box for hit in ([best] if best else []) + hits
                       if (box := _source_box(hit, frame_size)) is not None), None)
    run_boxes = [box for run in runs if (box := _run_box(run, frame_size)) is not None]
    window_start = max(0.0, max(min(bounds[0], hint) - PROBE_MARGIN_SECONDS, hint - PROBE_MAX_SECONDS / 2))
    window_end = min(float(duration), min(max(bounds[1], last_hint) + PROBE_MARGIN_SECONDS,
                                          hint + PROBE_MAX_SECONDS / 2))
    window_start, window_end = _decodable(probe, window_start, window_end,
                                          (min(bounds[0], hint) + max(bounds[1], last_hint)) / 2)
    frames = probe.frames(window_start, window_end)
    region = None
    span = {"start": bounds[0], "end": bounds[1], "method": "ocr_bounds", "start_cut": False,
            "reason": probe.reason or "no_frames"}
    analysis = None
    if frames:
        analysis_height, analysis_width = frames[0][1].shape[:2]
        analysis = (analysis_width / frame_size[0], analysis_height / frame_size[1])
        masks = mask_rects(regions, frames[0][1].shape)
        probe_box = box_source or (run_boxes[0] if run_boxes else None)
        box = scale_box(probe_box, *analysis) if probe_box else bright_box(
            min(frames, key=lambda value: abs(value[0] - hint))[1], masks)
        if box is not None:
            span = ident_span(frames, hint, box, masks, duration, bounds=bounds, last_hint=last_hint)
            region = logo_region(frames, (span["start"], span["end"]), masks)
    start = round(max(0.0, float(span["start"])), 3)
    end = round(min(float(duration), float(span["end"])), 3)
    if not end > start:
        return None, "empty_span"
    if region is not None and analysis is not None:
        blur_box, region_method = scale_box(tuple(region["box"]), 1 / analysis[0], 1 / analysis[1]), "logo_pixels"
    elif box_source is not None:
        blur_box, region_method = fallback_region(box_source, frame_size), "ocr_box_padded"
    else:
        blur_box, region_method = None, None
    # Logo pixels measured in this video win: a remembered layout (e.g. the outro's) can match
    # a frame of another ident and would only widen the blur. The record box fills in otherwise.
    if run_boxes and region is None:
        blur_box = union_box([box for box in [blur_box, *run_boxes] if box is not None])
        region_method = f"{region_method}+platform_memory" if region_method else "platform_memory"
    if blur_box is None:
        return None, "no_region"
    folder = queue_dir / PREVIEW_FOLDER
    previews = [
        path.resolve().relative_to(root.resolve()).as_posix()
        for path in write_previews(frames, _preview_moments(region, start, end), folder, key)
    ] if frames else list(dict.fromkeys(hit["preview"] for hit in hits if hit.get("preview")))[:3]
    texts = list(dict.fromkeys(hit["text"] for hit in hits))
    evidence = [f"platform:{key}", *sorted({hit["report"] for hit in hits}),
                *[f"platform-memory:{run.get('record_key')}" for run in runs]]
    scores = [float(hit["confidence"]) for hit in hits if hit.get("confidence") is not None]
    scores += [float(run["similarity"]) for run in runs]
    card = {
        "id": card_id("visual_logo", start, end, "|".join(evidence)),
        "category": "visual_logo",
        "start_seconds": start,
        "end_seconds": end,
        "max_score": round(max(scores), 6) if scores else None,
        "priority": "high",
        "labels": [f"Logo nền tảng {name}", *texts[:_TEXTS_SHOWN]],
        "reasons": [_reason(name, hits, runs, span, start, end)],
        "evidence": evidence,
        "preview_images": previews,
        "suggested_region_source_pixels": region_from_box(blur_box, frame_size),
        "source_frame_size": [int(frame_size[0]), int(frame_size[1])],
        "candidate_type": PLATFORM_LOGO_TYPE,
        "review_kind": PLATFORM_LOGO_TYPE,
        "region_classification": PLATFORM_LOGO_TYPE,
        "model_evidence": {"vlm_source": "platform_rule", "region_sources": ["platform_probe"]},
        "platform_logo": {
            "key": key, "name": name, "detections": _detections(hits, runs),
            "snap": {"method": span["method"], "start_cut": bool(span.get("start_cut")),
                     "reason": span.get("reason")},
            "region": {"method": region_method, "frames": len(region["frames"]) if region else 0,
                       "anchor_seconds": region["anchor_seconds"] if region else None},
        },
        "suggested_decision": "BLUR",
        "source_candidate_refs": [],
        "detected_intervals": [{"start_seconds": start, "end_seconds": end}],
        "decision": None,
        "decision_note": None,
        "decision_region_source_pixels": None,
        "decision_actor": None,
        "decision_transport": None,
        "decided_at": None,
    }
    return card, None


def build_platform_cards(
    root: Path, items: list[dict], advisory: list[dict], scan_payloads: dict[str, dict], *,
    source: Path | None, duration: float, frame_size: tuple[int, int] | None, queue_dir: Path,
    masks: list[dict] | None = None, memory_runs: Iterable[dict] = (), probe: SourceProbe | None = None,
) -> tuple[list[dict], dict[str, Any]]:
    """One BLUR card per platform ident (OCR/logo-model readings and remembered-logo runs).

    ``scan_payloads`` hold the text payloads after ``promote_fixed_text_overlays``
    (a name inside a fixed watermark stays with that watermark's card).
    ``masks`` are relative watermark regions; by default this queue's
    persistent-overlay boxes. Previews go to ``<queue dir>/platform-logo/``.
    """
    root = root.resolve()
    probe = probe or SourceProbe(source, None)
    hits = platform_text_hits(scan_payloads)
    runs = list(memory_runs)
    diagnostics: dict[str, Any] = {"hits": len(hits), "memory_runs": len(runs), "cards": 0, "skipped": []}
    if not hits and not runs:
        return [], diagnostics
    info = probe.info()
    if info is not None:
        frame_size = tuple(info["source_size"])
    if not frame_size:
        diagnostics["skipped"].append({"reason": "frame_size_unknown"})
        return [], diagnostics
    regions = overlay_regions([*items, *advisory], frame_size) if masks is None else list(masks)
    cards = []
    for group in _groups(hits, runs):
        card, reason = _platform_card(root, group, probe=probe, duration=duration, frame_size=frame_size,
                                      queue_dir=queue_dir, regions=regions)
        if card is None:
            diagnostics["skipped"].append({"platform": group["platform"], "reason": reason})
        else:
            cards.append(card)
    diagnostics.update(cards=len(cards), probe_reason=probe.reason, decodes=probe.decodes,
                       decoded_frames=probe.decoded_frames)
    return cards, diagnostics


# --------------------------------------------------------- forced ending card


def _reaches_end(payload: dict, duration: float) -> bool:
    try:
        end = float(payload.get("scan_start_seconds") or 0.0) + float(payload["scan_duration_seconds"])
    except (KeyError, TypeError, ValueError):
        return False
    return end >= float(duration) - 0.5


def _tail_windows(report: str, payload: dict, start: float) -> list[tuple[str, dict]]:
    windows = []
    for key in ("intervals", "rejected_windows"):
        for interval in payload.get(key) or []:
            try:
                if isinstance(interval, dict) and float(interval["end_seconds"]) > start:
                    windows.append((report, interval))
            except (KeyError, TypeError, ValueError):
                continue
    return sorted(windows, key=lambda value: float(value[1]["start_seconds"]))


def ensure_forced_ending_card(
    root: Path, items: list[dict], scan_payloads: dict[str, dict], *, duration: float, queue_dir: Path,
    probe: SourceProbe | None = None,
) -> dict | None:
    """The last 6 s once in the main list, unless a whole-scene card already shows them.

    Only for a visual-logo scan that reached the end of the video. The card
    carries the model answers of the tail windows and their boxes as display
    evidence; it has no region and no suggestion of its own.
    """
    root = root.resolve()
    duration = float(duration)
    reports = []
    seen: set[int] = set()
    for report, payload in scan_payloads.items():
        if isinstance(payload, dict) and payload.get("scan_type") == "visual_logo" and id(payload) not in seen:
            seen.add(id(payload))
            if _reaches_end(payload, duration):
                reports.append((report, payload))
    if not reports or duration <= FORCED_ENDING_SECONDS + 5.0:
        return None
    for item in items:
        if (
            item.get("category") == "visual_logo" and not item.get("advisory")
            and not isinstance(item.get("suggested_region_source_pixels"), dict)
            and float(item["start_seconds"]) <= duration - ENDING_COVERED_FROM
            and float(item["end_seconds"]) >= duration - ENDING_COVERED_TO
        ):
            return None
    start = round(duration - FORCED_ENDING_SECONDS, 3)
    tail = [value for report, payload in reports for value in _tail_windows(report, payload, start)]
    answers, boxes, thumbnails, evidence_size = [], [], [], None
    for report, interval in tail:
        confirmation = interval.get("visual_logo_confirmation") or {}
        answers.append({
            "start_seconds": round(float(interval["start_seconds"]), 3),
            "end_seconds": round(float(interval["end_seconds"]), 3),
            "state": confirmation.get("state"),
            "answer": " ".join(str(confirmation.get("answer") or "").split())[:_ANSWER_CHARACTERS],
        })
        localization = interval.get("region_localization") or {}
        evidence_size = evidence_size or localization.get("frame_size")
        for proposal in localization.get("proposals") or []:
            box = proposal.get("blur_region_px") if isinstance(proposal, dict) else None
            if isinstance(box, list) and len(box) == 4 and int(box[2]) > 0 and int(box[3]) > 0:
                boxes.append({
                    "x": max(0, int(box[0])), "y": max(0, int(box[1])), "width": int(box[2]),
                    "height": int(box[3]), "sources": [str(value) for value in proposal.get("sources") or []],
                    "labels": [str(value) for value in proposal.get("labels") or []][:3],
                    "region_classification": proposal.get("region_classification"),
                })
        thumbnail = interval.get("strongest_frame") or interval.get("audit_frame")
        if thumbnail:
            thumbnails.append((Path(report).parent / str(thumbnail)).as_posix())
    frames = probe.frames(start, duration) if probe is not None else []
    info = probe.info() if probe is not None else None
    frame_size = list(info["source_size"]) if info else evidence_size
    if frames:
        previews = [
            path.resolve().relative_to(root).as_posix()
            for path in write_previews(frames, [duration - offset for offset in ENDING_PREVIEW_OFFSETS],
                                       queue_dir / PREVIEW_FOLDER, "ending")
        ]
    else:
        previews = list(dict.fromkeys(thumbnails))[-3:]
    evidence = [report for report, _ in reports]
    card = {
        "id": card_id("visual_logo", start, duration, "|".join(evidence) + "|ending_boundary"),
        "category": "visual_logo",
        "start_seconds": start,
        "end_seconds": round(duration, 3),
        "max_score": None,
        "priority": "context",
        "labels": [ENDING_LABEL],
        "reasons": [ENDING_REASON],
        "evidence": evidence,
        "preview_images": previews,
        "suggested_region_source_pixels": None,
        "source_frame_size": frame_size,
        "candidate_type": ENDING_BOUNDARY_TYPE,
        "review_kind": "logo_overlay",
        "region_classification": None,
        "model_evidence": {"vlm_confirmation": None, "vlm_source": "forced_ending", "region_sources": [],
                           "tail_windows": answers},
        "suggested_decision": None,
        "source_candidate_refs": [],
        "detected_intervals": [{"start_seconds": start, "end_seconds": round(duration, 3)}],
        "decision": None,
        "decision_note": None,
        "decision_region_source_pixels": None,
        "decision_actor": None,
        "decision_transport": None,
        "decided_at": None,
    }
    if boxes:
        card["evidence_regions"] = boxes
        card["evidence_frame_size"] = evidence_size
    return card


# -------------------------------------------------------------------- links


def _covered_share(item: dict, owners: list[dict]) -> tuple[float, dict | None]:
    """Share of ``item``'s window the owners cover, and the owner covering most of it."""
    start, end = float(item["start_seconds"]), float(item["end_seconds"])
    spans = []
    for owner in owners:
        low, high = max(start, float(owner["start_seconds"])), min(end, float(owner["end_seconds"]))
        if high > low:
            spans.append((low, high, owner))
    if not spans or not end > start:
        return 0.0, None
    spans.sort(key=lambda value: (value[0], value[1]))
    covered, reach = 0.0, start
    for low, high, _ in spans:
        if high > reach:
            covered += high - max(low, reach)
            reach = high
    return covered / (end - start), max(spans, key=lambda value: value[1] - value[0])[2]


def _regions_overlap(first: object, second: object) -> bool:
    a, b = box_from_region(first), box_from_region(second)
    if a is None or b is None:
        return True
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return False
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return width * height >= 0.5 * smaller


def link_platform_cards(items: list[dict], advisory: list[dict]) -> None:
    """After the final ids: whole-scene cards repeating a platform ident suggest KEEP.

    An undecided main card without a region and without a suggestion whose
    window a platform card covers by at least half gets ``platform_logo_link``
    and KEEP (the logo itself is blurred by the platform card). An optional
    card covered the same way names it in ``covered_by``. Nothing else moves.
    """
    owners = [item for item in items if item.get("candidate_type") == PLATFORM_LOGO_TYPE
              and isinstance(item.get("suggested_region_source_pixels"), dict)]
    owner_ids = {id(owner) for owner in owners}
    for item in items:
        # A link restored with a preserved card is recomputed, never trusted.
        link = item.pop("platform_logo_link", None)
        if link and item.get("decision") is None and item.get("suggested_decision") == "KEEP":
            item["suggested_decision"] = None
        if (
            id(item) in owner_ids or item.get("category") != "visual_logo" or item.get("decision") is not None
            or isinstance(item.get("suggested_region_source_pixels"), dict)
            or item.get("suggested_decision") is not None
        ):
            continue
        share, owner = _covered_share(item, owners)
        if owner is None or share < LINK_MIN_COVERAGE:
            continue
        name = (owner.get("platform_logo") or {}).get("name") or "nền tảng"
        item["platform_logo_link"] = {"card_id": owner.get("id"), "platform": name, "coverage": round(share, 3)}
        item["suggested_decision"] = "KEEP"
    for item in advisory:
        item.pop("covered_by", None)
        item.pop("covered_by_label", None)
        share, owner = _covered_share(item, [
            owner for owner in owners
            if _regions_overlap(item.get("suggested_region_source_pixels"), owner["suggested_region_source_pixels"])
        ])
        if owner is not None and share >= LINK_MIN_COVERAGE:
            item["covered_by"] = owner.get("id")
            item["covered_by_label"] = (owner.get("labels") or ["Logo nền tảng"])[0]
