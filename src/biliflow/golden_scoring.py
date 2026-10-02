"""Golden Set scoring: review items vs human labels per segment (docs/QUALITY_PLAN.md §4, §7, §12).

Rules are fixed here before any measurement and must not be loosened after
seeing numbers. Only segments marked "complete" are scored, and only groups in
the evaluated queue's detection scope; everything else is reported as unscored.

Positives are BLUR/CUT labels and safety labels marked "có thật · giữ" (KEEP with
``content_present``: the content is really there and must reach review, the user keeps it on
export; action agreement is judged against KEEP). Only KEEP labels without ``content_present`` are
traps. Rows and fingerprints carry ``content_present`` only when it is set, so cards of earlier
labels are unchanged.
"""
from __future__ import annotations

from typing import Any

from biliflow.golden_set import (
    CATEGORY_GROUP, GROUPS, SPLITS, box_area, box_intersection, canonical_sha256, is_present_keep,
    normalize_region, now_iso, regions_compatible,
)

CAUGHT_COVERAGE = 0.80        # union of compatible items must cover this share of a label
REGION_COVERAGE = 0.85        # blur box must hide this share of the labelled box
REGION_MAX_AREA_RATIO = 4.0   # ...without being more than this many times larger
USEFUL_ITEM_FRACTION = 0.50   # main item counts as useful when this share lies inside positives
PRECISION_GATE_DROP = 0.02    # detector gate: main-item precision may drop at most 2 points
SPEED_GATE_TOLERANCE = 1      # speed gate: counts on holdout may differ by at most one item
TIME_GATE_RATIO = 1.05        # detector gate: pipeline wall time may grow at most 5%
FRAME_BOX_SHARE = 0.50        # a boxed item stands for a whole-frame label only if it covers half the frame
CAUGHT = ("caught", "advisory_only")
# Advertising items without a box are whole-frame events only when they propose cutting the scene;
# other box-less advertising items (VLM-rejected logo windows) have an unknown location.
FRAME_EVENT_TYPES = ("opening_promotion", "branded_end_card")
RULES = {"caught_coverage": CAUGHT_COVERAGE, "region_coverage": REGION_COVERAGE,
         "region_max_area_ratio": REGION_MAX_AREA_RATIO, "useful_item_fraction": USEFUL_ITEM_FRACTION,
         "region_compatible_min_shared": 0.30, "frame_box_share": FRAME_BOX_SHARE,
         "item_kinds": "box -> region; safety without box -> frame; advertising without box -> frame if CUT or "
                       "opening_promotion/branded_end_card, else unknown (never matches)"}
# Added to a card's rules only when it scored a scene card, so cards of queues without
# scene cards stay byte-identical to the ones scored before the rule existed.
SCENE_CARD_EXTENT_RULE = ("a scene card (scene_card, discrete detected moments) spans only its moments: "
                          "the gaps a decision on it leaves untouched neither catch labels nor count against it")


def queue_scope(queue: dict[str, Any]) -> set[str]:
    scope = queue.get("detection_scope")
    return set(scope.get("selected") or ()) & set(GROUPS) if isinstance(scope, dict) else set(GROUPS)


def scene_moments(item: dict[str, Any]) -> list[tuple[float, float]] | None:
    """The detected moments of a scene card (R1, 2026-10-01); ``None`` for every other item.

    A decision on a scene card edits only these moments (build_edit_plan), so a
    label in a gap between them is not caught by the card and the gap is not
    counted against its precision.
    """
    if not item.get("scene_card") or item.get("temporal_policy") != "discrete_detected_intervals":
        return None
    moments = []
    for value in item.get("detected_intervals") or []:
        if isinstance(value, dict):
            start = float(value["start_seconds"])
            moments.append((start, max(float(value["end_seconds"]), start + 0.5)))
    return sorted(moments) or None


def queue_items(queue: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [(item, False) for item in queue.get("items") or []]
    rows += [(item, True) for item in queue.get("advisory_items") or []]
    items = []
    for item, advisory in rows:
        if item.get("category") not in CATEGORY_GROUP:
            continue
        start = float(item["start_seconds"])
        end = max(float(item["end_seconds"]), start + 0.5)  # instantaneous items keep a visible extent
        moments = scene_moments(item)
        group = CATEGORY_GROUP[item["category"]]
        region = normalize_region(item.get("suggested_region_source_pixels"))
        if region is not None:
            kind = "region"
        elif group != "advertising" or item.get("suggested_decision") == "CUT" \
                or item.get("candidate_type") in FRAME_EVENT_TYPES:
            kind = "frame"
        else:
            kind = "unknown"
        row = {"id": item.get("id"), "category": item["category"], "group": group,
               "start": start, "end": end, "advisory": advisory, "region": region, "kind": kind,
               "suggested_decision": item.get("suggested_decision"), "priority": item.get("priority"),
               "candidate_type": item.get("candidate_type"),
               "labels": [str(x) for x in (item.get("labels") or [])[:3]]}
        if moments is not None:
            row["moments"] = moments
        items.append(row)
    return items


def _overlaps(spans: list[tuple[float, float]], target: tuple[float, float]) -> bool:
    return any(clip(*span, *target) for span in spans)


def _overlap_length(spans: list[tuple[float, float]], target: tuple[float, float]) -> float:
    return union_length([c for c in (clip(*span, *target) for span in spans) if c])


def _spans_covered(spans: list[tuple[float, float]], intervals: list[tuple[float, float]]) -> float:
    """Share of the total length of ``spans`` covered by ``intervals``."""
    if len(spans) == 1:  # every item but a scene card: exactly the previous rule
        return covered_fraction(spans[0], intervals)
    total = sum(b - a for a, b in spans)
    if total <= 0:
        return 0.0
    return sum(covered_fraction(span, intervals) * (span[1] - span[0]) for span in spans) / total


def _extent(item: dict[str, Any]) -> float:
    """Edited length of a review item: its span, or the sum of a scene card's moments."""
    if "moments" in item:
        return sum(b - a for a, b in item["moments"])
    return item["end"] - item["start"]


def item_matches_label(item: dict[str, Any], label_region: dict[str, int] | None, frame_area: int) -> bool:
    """Spatial compatibility of one review item with one label of the same group (§4, §12.1)."""
    if item["kind"] == "unknown":
        return False
    if label_region is None:  # whole-frame label: a whole-frame item or a box covering half the frame
        return item["kind"] == "frame" or box_area(item["region"]) >= FRAME_BOX_SHARE * frame_area
    if item["kind"] == "frame":  # a full-frame safety flag covers any boxed safety label
        return item["group"] != "advertising"
    return regions_compatible(item["region"], label_region)


def clip(start: float, end: float, low: float, high: float) -> tuple[float, float] | None:
    a, b = max(start, low), min(end, high)
    return (a, b) if b > a else None


def union_length(intervals: list[tuple[float, float]]) -> float:
    total, current = 0.0, None
    for a, b in sorted(intervals):
        if current is None or a > current[1]:
            if current is not None:
                total += current[1] - current[0]
            current = [a, b]
        else:
            current[1] = max(current[1], b)
    return total + (current[1] - current[0] if current is not None else 0.0)


def covered_fraction(target: tuple[float, float], intervals: list[tuple[float, float]]) -> float:
    parts = [c for c in (clip(a, b, *target) for a, b in intervals) if c]
    length = target[1] - target[0]
    return union_length(parts) / length if length > 0 else 0.0


def region_quality(item_region: dict[str, int] | None, label_region: dict[str, int]) -> dict[str, Any]:
    if item_region is None:
        return {"ok": False, "reason": "no_region", "coverage": None, "area_ratio": None}
    coverage = box_intersection(item_region, label_region) / box_area(label_region)
    ratio = box_area(item_region) / box_area(label_region)
    return {"ok": coverage >= REGION_COVERAGE and ratio <= REGION_MAX_AREA_RATIO,
            "reason": None, "coverage": round(coverage, 4), "area_ratio": round(ratio, 4)}


def is_positive(label: dict[str, Any]) -> bool:
    """Content the detector must flag: BLUR/CUT labels and "có thật · giữ" (KEEP + content_present)."""
    return label["expected_action"] in ("BLUR", "CUT") or is_present_keep(label)


def is_trap(label: dict[str, Any]) -> bool:
    """A known false-positive spot: KEEP without content_present."""
    return label["expected_action"] == "KEEP" and not is_present_keep(label)


def labels_fingerprint(labels_doc: dict[str, Any]) -> str:
    complete = sorted(k for k, v in labels_doc["segments"].items() if v.get("status") == "complete")
    keys = ("id", "segment_id", "category", "start_seconds", "end_seconds", "region_source_pixels",
            "expected_action", "severity", "ambiguous")

    def row(event: dict[str, Any]) -> dict[str, Any]:
        value = {k: event.get(k) for k in keys}
        if is_present_keep(event):  # only when set, so fingerprints of earlier labels do not change
            value["content_present"] = True
        return value

    events = sorted((row(e) for e in labels_doc["events"] if e["segment_id"] in complete), key=lambda e: e["id"])
    return canonical_sha256({"complete_segments": complete, "events": events})


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def score(manifest: dict[str, Any], labels_doc: dict[str, Any], queues: dict[str, dict[str, Any]],
          timing: dict[str, Any] | None = None, approximate_suggestions: frozenset[str] | set[str] = frozenset(),
          ) -> dict[str, Any]:
    """Score review queues (keyed by manifest source key) against the complete labelled segments.

    Labels accepted from a suggestion listed in ``approximate_suggestions`` (dense logo windows
    that only carry a coarse routing box) keep their box for matching, but blur-region quality
    is not graded for them.
    """
    for key, queue in queues.items():
        expected = manifest["sources"][key]["sha256"]
        if (queue.get("source") or {}).get("sha256") != expected:
            raise ValueError(f"Review queue for {key} was built from a different source file")
    segments_out, labels_out, items_out = [], [], []
    unscored: dict[str, int] = {group: 0 for group in GROUPS}
    for segment in manifest["segments"]:
        state = labels_doc["segments"].get(segment["id"], {}).get("status", "unlabeled")
        queue = queues.get(segment["source"])
        row = {"id": segment["id"], "source": segment["source"], "split": segment["split"],
               "seconds": round(segment["end_seconds"] - segment["start_seconds"], 3), "status": state,
               "scored_groups": []}
        if "set" in segment:  # combined manifests (load_golden_sets) tag each segment with its set
            row["set"] = segment["set"]
        segments_out.append(row)
        if state != "complete" or queue is None:
            row["reason"] = "chưa xem hết đoạn" if state != "complete" else "không có review queue cho nguồn"
            continue
        scope = queue_scope(queue)
        row["scored_groups"] = sorted(scope)
        source = manifest["sources"][segment["source"]]
        frame_area = source["width"] * source["height"]
        low, high = segment["start_seconds"], segment["end_seconds"]
        items = []
        for item in queue_items(queue):
            if item["group"] not in scope:
                continue
            # spans: the time a decision on the item edits, inside this segment
            spans = [c for c in (clip(a, b, low, high) for a, b in item.get("moments") or [(item["start"], item["end"])])
                     if c]
            if spans:
                items.append(dict(item, spans=spans, clip=(spans[0][0], max(b for _, b in spans))))
        labels = []
        for event in labels_doc["events"]:
            if event["segment_id"] != segment["id"]:
                continue
            group = CATEGORY_GROUP[event["category"]]
            if group not in scope:
                unscored[group] += 1
                continue
            clipped = clip(event["start_seconds"], event["end_seconds"], low, high)
            if clipped:
                labels.append(dict(event, group=group, clip=clipped))
        for label in labels:
            compatible = [i for i in items if i["group"] == label["group"]
                          and _overlaps(i["spans"], label["clip"])
                          and item_matches_label(i, label["region_source_pixels"], frame_area)]
            out = {k: label[k] for k in ("id", "segment_id", "category", "group", "expected_action",
                                         "severity", "ambiguous", "start_seconds", "end_seconds",
                                         "region_source_pixels", "notes")}
            out.update(split=segment["split"], source=segment["source"],
                       clip=[round(label["clip"][0], 3), round(label["clip"][1], 3)])
            if is_present_keep(label):  # a positive whose agreement is judged against KEEP
                out["content_present"] = True
            if is_trap(label):
                hits = [i["id"] for i in compatible if i["suggested_decision"] in ("BLUR", "CUT")]
                out.update(status="trap_hit" if hits else "trap_ok", trap_item_ids=hits)
                labels_out.append(out)
                continue
            coverage = covered_fraction(label["clip"], [s for i in compatible for s in i["spans"]])
            main_coverage = covered_fraction(label["clip"], [s for i in compatible if not i["advisory"]
                                                             for s in i["spans"]])
            if label["ambiguous"]:
                status = "ambiguous"
            elif main_coverage >= CAUGHT_COVERAGE:
                status = "caught"
            elif coverage >= CAUGHT_COVERAGE:
                status = "advisory_only"
            else:
                status = "partial" if coverage > 0 else "missed"
            best = max(compatible, key=lambda i: (not i["advisory"],
                                                  _overlap_length(i["spans"], label["clip"]),
                                                  -abs(_extent(i) - (label["clip"][1] - label["clip"][0]))),
                       default=None)
            region = None
            approximate = label.get("from_suggestion") in approximate_suggestions
            out["region_approximate"] = approximate
            if status in CAUGHT and label["expected_action"] == "BLUR" and label["region_source_pixels"] and not approximate:
                # The item that best covers the label in time drives the blur for most of it.
                region = dict(region_quality(best["region"], label["region_source_pixels"]), item_id=best["id"])
            agrees = None
            if status in CAUGHT and best is not None and best["suggested_decision"] in ("BLUR", "CUT", "KEEP"):
                agrees = best["suggested_decision"] == label["expected_action"]
            out.update(status=status, coverage=round(coverage, 4), main_coverage=round(main_coverage, 4),
                       best_item_id=best["id"] if best else None, region=region, suggestion_agrees=agrees)
            labels_out.append(out)
        for item in items:
            length = sum(b - a for a, b in item["spans"])

            def compatible_labels(kind: str) -> list[tuple[float, float]]:
                chosen = []
                for label in labels:
                    positive = is_positive(label)
                    wanted = (kind == "positive" and positive and not label["ambiguous"]) or \
                             (kind == "ambiguous" and positive and label["ambiguous"])
                    if wanted and label["group"] == item["group"] and \
                            item_matches_label(item, label["region_source_pixels"], frame_area):
                        chosen.append(label["clip"])
                return chosen

            positive_share = _spans_covered(item["spans"], compatible_labels("positive"))
            if positive_share * length >= USEFUL_ITEM_FRACTION * length:
                verdict = "useful"
            elif positive_share == 0 and _spans_covered(item["spans"], compatible_labels("ambiguous")) > 0:
                verdict = "ambiguous_only"  # touches only ambiguous labels: excluded from precision
            else:
                verdict = "false_positive"
            traps = [label["id"] for label in labels if is_trap(label)
                     and label["group"] == item["group"] and item["suggested_decision"] in ("BLUR", "CUT")
                     and _overlaps(item["spans"], label["clip"])
                     and item_matches_label(item, label["region_source_pixels"], frame_area)]
            row = {"item_id": item["id"], "segment_id": segment["id"], "split": segment["split"],
                   "source": segment["source"], "group": item["group"], "category": item["category"],
                   "advisory": item["advisory"], "clip": [round(item["clip"][0], 3), round(item["clip"][1], 3)],
                   "region": item["region"], "kind": item["kind"],
                   "suggested_decision": item["suggested_decision"],
                   "priority": item["priority"], "candidate_type": item["candidate_type"],
                   "labels": item["labels"], "verdict": verdict, "trap_hits": traps}
            if "moments" in item:
                row["moments"] = [[round(a, 3), round(b, 3)] for a, b in item["spans"]]
            items_out.append(row)
    metrics = {group: {split: _metrics(group, split, segments_out, labels_out, items_out)
                       for split in (*SPLITS, "all")} for group in GROUPS}
    rules = dict(RULES, scene_card_extent=SCENE_CARD_EXTENT_RULE) if any("moments" in i for i in items_out) else RULES
    return {"schema_version": 1, "created_at": now_iso(), "rules": rules,
            "manifest_sha256": canonical_sha256(manifest), "labels_fingerprint": labels_fingerprint(labels_doc),
            "labels_revision": labels_doc.get("revision"), "segments": segments_out, "labels": labels_out,
            "items": items_out, "metrics": metrics, "unscored_labels": unscored, "timing": timing or {}}


def score_sets(manifest: dict[str, Any], labels_doc: dict[str, Any], parts: dict[str, dict[str, Any]],
               queues: dict[str, dict[str, Any]], timing: dict[str, Any] | None = None,
               approximate_suggestions: frozenset[str] | set[str] = frozenset(),
               ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Combined scorecard plus one sub-card per set, from ``load_golden_sets`` output.

    Each sub-card is ``score()`` on that set's own manifest and labels, so it keeps the set's pin
    and labels fingerprint and stays comparable with earlier cards of that set (compare --gate).
    The combined card carries a ``sets`` block (pin, revision, fingerprint per set); with a single
    set it is that set's card.
    """
    cards = {}
    for name, part in parts.items():
        own = {key: queue for key, queue in queues.items() if key in part["manifest"]["sources"]}
        cards[name] = dict(score(part["manifest"], part["labels"], own, timing, approximate_suggestions), set=name)
    if len(parts) == 1:
        combined = {key: value for key, value in next(iter(cards.values())).items() if key != "set"}
    else:
        combined = score(manifest, labels_doc, queues, timing, approximate_suggestions)
    combined["sets"] = {name: {"manifest_sha256": card["manifest_sha256"], "labels_revision": card["labels_revision"],
                               "labels_fingerprint": card["labels_fingerprint"],
                               "complete_segments": sum(1 for s in card["segments"] if s["status"] == "complete"),
                               "segments": len(card["segments"])}
                        for name, card in cards.items()}
    return combined, cards


def _metrics(group: str, split: str, segments: list[dict], labels: list[dict], items: list[dict]) -> dict[str, Any]:
    def chosen(row: dict) -> bool:
        return row["group"] == group and (split == "all" or row["split"] == split)

    scored_seconds = sum(s["seconds"] for s in segments
                         if group in s["scored_groups"] and (split == "all" or s["split"] == split))
    positives = [l for l in labels if chosen(l) and l["status"] in (*CAUGHT, "partial", "missed")]
    must = [l for l in positives if l["severity"] == "must_catch"]
    main = [i for i in items if chosen(i) and not i["advisory"] and i["verdict"] != "ambiguous_only"]
    advisory = [i for i in items if chosen(i) and i["advisory"] and i["verdict"] != "ambiguous_only"]
    regions = [l for l in positives if l.get("region") is not None]
    agreements = [l for l in positives if l.get("suggestion_agrees") is not None]
    useful = sum(i["verdict"] == "useful" for i in main)
    load = sum(1 for i in items if chosen(i))
    return {
        "scored_seconds": round(scored_seconds, 3),
        "labels": len(positives), "caught": sum(l["status"] in CAUGHT for l in positives),
        "caught_main": sum(l["status"] == "caught" for l in positives),
        "advisory_only": sum(l["status"] == "advisory_only" for l in positives),
        "partial": sum(l["status"] == "partial" for l in positives),
        "missed": sum(l["status"] == "missed" for l in positives),
        "must_catch": len(must), "must_caught": sum(l["status"] in CAUGHT for l in must),
        "recall_all": _ratio(sum(l["status"] in CAUGHT for l in positives), len(positives)),
        "recall_must": _ratio(sum(l["status"] in CAUGHT for l in must), len(must)),
        "main_items": len(main), "useful_main": useful, "false_positive_main": len(main) - useful,
        "precision_main": _ratio(useful, len(main)),
        "advisory_items": len(advisory),
        "advisory_noise": sum(i["verdict"] == "false_positive" for i in advisory),
        "advisory_noise_rate": _ratio(sum(i["verdict"] == "false_positive" for i in advisory), len(advisory)),
        "region_evaluated": len(regions), "region_ok": sum(l["region"]["ok"] for l in regions),
        "region_ok_rate": _ratio(sum(l["region"]["ok"] for l in regions), len(regions)),
        "suggestion_evaluated": len(agreements),
        "suggestion_agree": sum(bool(l["suggestion_agrees"]) for l in agreements),
        "ambiguous": sum(1 for l in labels if chosen(l) and l["status"] == "ambiguous"),
        "trap_hits": sum(1 for l in labels if chosen(l) and l["status"] == "trap_hit"),
        "review_load_per_hour": round(load / (scored_seconds / 3600), 2) if scored_seconds else None,
    }


def compare(baseline: dict[str, Any], candidate: dict[str, Any], gate: str | None = None) -> dict[str, Any]:
    """Label-level differences and, when ``gate`` is given, violations of the pre-registered gates."""
    result: dict[str, Any] = {"gate": gate, "violations": [], "comparable": True}
    if (baseline["labels_fingerprint"], baseline["manifest_sha256"]) != \
            (candidate["labels_fingerprint"], candidate["manifest_sha256"]):
        result["comparable"] = False
        result["violations"].append("Hai scorecard dùng bộ nhãn hoặc manifest khác nhau; không so được")
        return result
    before = {l["id"]: l for l in baseline["labels"]}
    after = {l["id"]: l for l in candidate["labels"]}
    changes = []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if (old or {}).get("status") != (new or {}).get("status"):
            reference = new or old
            changes.append({"id": key, "segment_id": reference["segment_id"], "split": reference["split"],
                            "group": reference["group"], "severity": reference.get("severity"),
                            "before": (old or {}).get("status"), "after": (new or {}).get("status")})
    result["label_changes"] = changes
    result["newly_caught"] = [c for c in changes if c["after"] in CAUGHT and c["before"] not in CAUGHT]
    result["newly_missed"] = [c for c in changes if c["before"] in CAUGHT and c["after"] not in CAUGHT]
    result["new_trap_hits"] = [c for c in changes if c["after"] == "trap_hit"]
    result["metric_deltas"] = {
        group: {split: {key: _delta(baseline["metrics"][group][split].get(key), candidate["metrics"][group][split].get(key))
                        for key in ("caught", "must_caught", "useful_main", "false_positive_main",
                                    "precision_main", "advisory_items", "region_ok", "trap_hits")}
                for split in ("holdout", "all")}
        for group in GROUPS}
    old_time = (baseline.get("timing") or {}).get("total_wall_seconds")
    new_time = (candidate.get("timing") or {}).get("total_wall_seconds")
    result["time"] = {"baseline": old_time, "candidate": new_time}
    violations = result["violations"]
    if gate == "detector":
        for group in GROUPS:
            b, c = baseline["metrics"][group]["all"], candidate["metrics"][group]["all"]
            if c["must_caught"] < b["must_caught"]:
                violations.append(f"{group}: recall must_catch giảm ({b['must_caught']} → {c['must_caught']})")
            if b["precision_main"] is not None and c["precision_main"] is not None and \
                    c["precision_main"] < b["precision_main"] - PRECISION_GATE_DROP:
                violations.append(f"{group}: precision mục chính giảm quá 2 điểm "
                                  f"({b['precision_main']} → {c['precision_main']})")
            elif b["precision_main"] is None and c["false_positive_main"] > 0:
                violations.append(f"{group}: mốc không có mục chính, bản mới thêm "
                                  f"{c['false_positive_main']} mục chính báo nhầm")
        for change in result["newly_missed"]:
            if change["split"] == "holdout" and change["severity"] == "must_catch":
                violations.append(f"Bỏ sót must_catch mới trên holdout: {change['id']}")
        if not old_time or not new_time:
            violations.append("Thiếu thời gian pipeline ở một trong hai scorecard; không kiểm được cổng +5% "
                              "(chấm bằng evaluate_golden.py run hoặc score --trial)")
        elif new_time > old_time * TIME_GATE_RATIO:
            violations.append(f"Thời gian pipeline tăng quá 5% ({old_time:.0f}s → {new_time:.0f}s)")
    elif gate == "speed":
        for group in GROUPS:
            b, c = baseline["metrics"][group]["holdout"], candidate["metrics"][group]["holdout"]
            for key in ("caught", "useful_main", "false_positive_main"):
                if abs(c[key] - b[key]) > SPEED_GATE_TOLERANCE:
                    violations.append(f"{group}/holdout: {key} lệch quá ±1 ({b[key]} → {c[key]})")
        missed = lambda card: sorted(l["id"] for l in card["labels"]
                                     if l.get("severity") == "must_catch" and l["status"] in ("partial", "missed"))
        if missed(baseline) != missed(candidate):
            violations.append("Danh sách bỏ sót must_catch thay đổi")
    elif gate is not None:
        raise ValueError("gate must be 'detector' or 'speed'")
    if gate is not None:
        for change in result["new_trap_hits"]:
            violations.append(f"Trúng bẫy mới: {change['id']}")
    return result


def _delta(old: Any, new: Any) -> Any:
    if isinstance(old, (int, float)) and isinstance(new, (int, float)):
        return {"before": old, "after": new, "delta": round(new - old, 4)}
    return {"before": old, "after": new, "delta": None}


GROUP_NAMES = {"advertising": "Quảng cáo (logo + chữ)", "adult": "18+", "gore": "Máu me", "violence": "Bạo lực"}
STATUS_NAMES = {"caught": "bắt được", "advisory_only": "chỉ ở advisory", "partial": "bắt một phần",
                "missed": "bỏ sót", "ambiguous": "mơ hồ", "trap_ok": "bẫy an toàn", "trap_hit": "trúng bẫy"}


SET_LIMITS = {
    "v1": "Golden Set v1: 3 nguồn (Troy, Conan 20, Conan 21), 2 cùng loạt Conan; đoạn chọn một phần theo chỗ "
          "detector từng báo, T3 là đối chứng.",
    "v1.1": "Golden Set v1.1: vẫn 3 nguồn đó (không thêm nguồn mới), đoạn chọn theo chỗ detector từng báo hoặc "
            "bỏ sót; không có thêm logo hãng phim; nhãn bạo lực phụ thuộc quy ước bạo lực người dùng chọn.",
}


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{100 * value:.1f}%"


def _action_text(label: dict[str, Any]) -> str:
    return "có thật · giữ" if label.get("content_present") else label["expected_action"]


def _set_names(card: dict[str, Any]) -> list[str]:
    if card.get("sets"):
        return list(card["sets"])
    if card.get("set"):
        return [card["set"]]
    tagged = list(dict.fromkeys(s["set"] for s in card["segments"] if s.get("set")))
    return tagged or ["v1"]


def scorecard_markdown(card: dict[str, Any], title: str, frames: dict[str, str] | None = None) -> str:
    frames = frames or {}
    revision = card["labels_revision"]
    if isinstance(revision, dict):
        revision = ", ".join(f"{name} {value}" for name, value in revision.items())
    lines = [f"# {title}", "", f"Tạo lúc {card['created_at']}. Bộ nhãn revision {revision}, "
             f"dấu vân `{card['labels_fingerprint'][:12]}`.", ""]
    complete = [s for s in card["segments"] if s["scored_groups"]]
    lines += [f"Đoạn được chấm: {len(complete)}/{len(card['segments'])} "
              f"({sum(s['seconds'] for s in complete) / 60:.1f} phút).", ""]
    if len(card.get("sets") or ()) > 1:
        lines += ["## Theo bộ nhãn", "", "| Bộ | Manifest | Revision | Dấu vân | Đoạn đã xem hết | Đoạn được chấm |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for name, info in card["sets"].items():
            scored = sum(1 for s in complete if s.get("set") == name)
            lines.append(f"| {name} | `{info['manifest_sha256'][:12]}` | {info['labels_revision']} "
                         f"| `{info['labels_fingerprint'][:12]}` | {info['complete_segments']}/{info['segments']} "
                         f"| {scored} |")
        lines += ["", "Cổng so sánh (compare --gate) dùng scorecard của từng bộ (scorecard-<bộ>.json); "
                  "scorecard gộp chỉ so được với scorecard gộp cùng các bộ và cùng nhãn.", ""]
    if card.get("timing"):
        lines += [f"Thời gian pipeline: {card['timing'].get('total_wall_seconds', 0):.0f} s.", ""]
    lines += ["## Chỉ số theo nhóm", "",
              "| Nhóm | Tập | Nhãn | Bắt được (chính/advisory) | Một phần | Bỏ sót | Recall must_catch | "
              "Precision mục chính | Nhiễu advisory | Vùng đạt | Bẫy | Tải duyệt/giờ |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for group in GROUPS:
        for split in ("all", "dev", "holdout"):
            m = card["metrics"][group][split]
            if not m["scored_seconds"]:
                continue
            lines.append(
                f"| {GROUP_NAMES[group]} | {split} | {m['labels']} | {m['caught']} ({m['caught_main']}/{m['advisory_only']}) "
                f"| {m['partial']} | {m['missed']} | {m['must_caught']}/{m['must_catch']} ({_pct(m['recall_must'])}) "
                f"| {m['useful_main']}/{m['main_items']} ({_pct(m['precision_main'])}) "
                f"| {m['advisory_noise']}/{m['advisory_items']} | {m['region_ok']}/{m['region_evaluated']} "
                f"| {m['trap_hits']} | {m['review_load_per_hour'] if m['review_load_per_hour'] is not None else '—'} |")
    unscored = {g: n for g, n in card["unscored_labels"].items() if n}
    if unscored:
        lines += ["", "Nhãn chưa chấm vì lượt đánh giá không chạy nhóm detector đó: " +
                  ", ".join(f"{GROUP_NAMES[g]} {n}" for g, n in unscored.items()) + "."]
    present = [l for l in card["labels"] if l.get("content_present")]
    if present:
        scored = [l for l in present if l["status"] != "ambiguous"]
        lines += ["", f"Nhãn có thật · giữ (nội dung có thật, người dùng giữ khi xuất; máy cần báo để duyệt, "
                      f"tính như nhãn dương, không phải bẫy): {len(present)}, bắt được "
                      f"{sum(l['status'] in CAUGHT for l in scored)}/{len(scored)}."]

    def image(key: str) -> str:
        return f" ![]({frames[key]})" if key in frames else ""

    problems = [l for l in card["labels"] if l["status"] in ("missed", "partial", "advisory_only", "trap_hit")]
    lines += ["", "## Nhãn bỏ sót, bắt một phần, chỉ ở advisory, trúng bẫy", ""]
    lines += [f"- `{l['id']}` {l['segment_id']} {l['clip'][0]:.1f}–{l['clip'][1]:.1f}s · {l['category']} "
              f"{_action_text(l)} {l.get('severity') or ''} · **{STATUS_NAMES[l['status']]}**"
              f"{' (phủ ' + _pct(l.get('coverage')) + ')' if l.get('coverage') is not None else ''}"
              f"{' · ' + l['notes'] if l.get('notes') else ''}{image(l['id'])}" for l in problems] or ["- Không có."]
    failures = [l for l in card["labels"] if l.get("region") and not l["region"]["ok"]]
    lines += ["", "## Vùng blur chưa đạt", ""]
    lines += [f"- `{l['id']}` {l['segment_id']} · che {_pct(l['region']['coverage'])}, rộng gấp "
              f"{l['region']['area_ratio'] if l['region']['area_ratio'] is not None else '—'} lần "
              f"(mục `{l['region']['item_id']}`){' · không có vùng' if l['region']['reason'] == 'no_region' else ''}"
              for l in failures] or ["- Không có."]
    false_positives = [i for i in card["items"] if not i["advisory"] and i["verdict"] == "false_positive"]
    lines += ["", "## Mục chính báo nhầm", ""]
    lines += [f"- `{i['item_id']}` {i['segment_id']} {i['clip'][0]:.1f}–{i['clip'][1]:.1f}s · {i['category']} "
              f"{i['priority'] or ''} đề xuất {i['suggested_decision'] or '—'} · {'; '.join(i['labels'])[:90]}"
              f"{image(i['item_id'] + '@' + i['segment_id'])}" for i in false_positives] or ["- Không có."]
    lines += ["", "## Giới hạn", ""]
    lines += [f"- {SET_LIMITS.get(name, f'Golden Set {name}.')}" for name in _set_names(card)]
    lines += ["- Không gồm Visual AI Audit (Codex) và bước xuất video.", ""]
    return "\n".join(lines)
