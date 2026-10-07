from __future__ import annotations

from collections.abc import Mapping


GORE_LABELS = (
    "blood", "blood_on_face", "blood_on_clothes", "blood_on_hands",
    "blood_splatter", "blood_on_weapon", "blood_from_mouth", "guro",
    "corpse", "blood_stain", "blood_from_eyes", "pink_blood",
    "severed_head", "blood_on_knife", "blood_in_hair", "blood_on_arm",
    "pool_of_blood", "severed_limb", "decapitation", "blood_on_leg",
    "blood_on_bandages", "blood_on_ground", "injury",
)

GORE_CONTEXT_LABELS = (
    "guro", "corpse", "injury", "blood_splatter", "pool_of_blood",
    "severed_head", "severed_limb", "decapitation", "blood_on_ground",
)

# Gore tag evidence (docs/ANIME_GORE_PLAN.md §2.1, step 1). The scanner records,
# for every gore interval, the strongest blood, injury and corpse tags over its
# temporally confirmed frames. Recording them changes no interval, score or
# threshold. Blood family: the 15 ``blood*`` tags of GORE_LABELS plus
# ``pool_of_blood``, combined with the probabilistic union (``pink_blood`` is a
# stylised tag and stays out). This is the only definition of the family.
GORE_BLOOD_FAMILY_LABELS = (
    "blood", "blood_on_face", "blood_on_clothes", "blood_on_hands",
    "blood_splatter", "blood_on_weapon", "blood_from_mouth", "blood_stain",
    "blood_from_eyes", "blood_on_knife", "blood_in_hair", "blood_on_arm",
    "pool_of_blood", "blood_on_leg", "blood_on_bandages", "blood_on_ground",
)
GORE_INJURY_LABEL = "injury"
GORE_CORPSE_LABEL = "corpse"
GORE_TAG_EVIDENCE_VERSION = 1
GORE_TAG_EVIDENCE_FIELDS = ("blood_family_max", "injury_max", "corpse_max")

DIRECT_VIOLENCE_LABELS = (
    "fighting_stance", "kicking", "punching", "fighting", "strangling",
    "attack", "incoming_attack", "stab", "hitting", "high_kick",
    "flying_kick",
)

DANGER_LABELS = ("fire", "explosion")

ADULT_EXPLICIT_LABELS = (
    "sex", "sex_from_behind", "group_sex", "penis", "pussy",
    "vaginal", "anus", "masturbation", "fellatio", "oral", "cum",
    "ejaculation", "anal", "paizuri", "handjob", "nude", "completely_nude",
    "nipples", "breasts_out", "penis_out",
)


def probabilistic_union(values: list[float]) -> float:
    remaining = 1.0
    for value in values:
        remaining *= 1.0 - max(0.0, min(1.0, float(value)))
    return 1.0 - remaining


def gore_tag_evidence_policy() -> dict:
    """The ``gore_tag_evidence_policy`` block of an animation gore report."""
    return {
        "version": GORE_TAG_EVIDENCE_VERSION,
        "blood_family_labels": list(GORE_BLOOD_FAMILY_LABELS),
        "blood_family_score": "probabilistic_union",
        "injury_label": GORE_INJURY_LABEL,
        "corpse_label": GORE_CORPSE_LABEL,
        "aggregation": "max over the temporally confirmed frames of the interval",
        "note": "Evidence only: intervals, scores and thresholds do not depend on it.",
    }


def gore_frame_tag_evidence(probabilities: Mapping[str, float]) -> dict:
    """Per-frame evidence from tag probabilities; the scanner computes the same on tensors."""
    blood = {label: float(probabilities.get(label, 0.0)) for label in GORE_BLOOD_FAMILY_LABELS}
    top = max(GORE_BLOOD_FAMILY_LABELS, key=lambda label: blood[label])
    return {
        "blood_family": probabilistic_union(list(blood.values())),
        "injury": float(probabilities.get(GORE_INJURY_LABEL, 0.0)),
        "corpse": float(probabilities.get(GORE_CORPSE_LABEL, 0.0)),
        "blood_label": top,
        "blood_label_score": blood[top],
    }


def gore_interval_tag_evidence(hits: list[dict]) -> dict | None:
    """``gore_tag_evidence`` of one interval: maxima over its confirmed hits.

    Every hit must carry ``tag_evidence`` (``gore_frame_tag_evidence``); an
    interval with a hit that has none gets no evidence rather than a partial one.
    """
    frames = [hit.get("tag_evidence") for hit in hits]
    if not frames or any(not isinstance(frame, dict) for frame in frames):
        return None
    strongest = max(frames, key=lambda frame: float(frame["blood_label_score"]))
    return {
        "blood_family_max": round(max(float(frame["blood_family"]) for frame in frames), 6),
        "injury_max": round(max(float(frame["injury"]) for frame in frames), 6),
        "corpse_max": round(max(float(frame["corpse"]) for frame in frames), 6),
        "confirmed_frames": len(frames),
        "strongest_blood_label": str(strongest["blood_label"]),
        "strongest_blood_label_score": round(float(strongest["blood_label_score"]), 6),
    }


def _gore_hit_groups(hits: list[dict], merge_gap_seconds: float) -> list[list[dict]]:
    """The hits behind each interval of ``intervals.group_hits``, in interval order.

    The same grouping rule as ``group_hits``. It lives here and not in
    ``intervals.py`` because that file is in the cache key of every safety scan
    stage (``cache_dependencies``): editing it would make all of them rescan.
    ``attach_gore_tag_evidence`` checks the groups against the intervals.
    """
    if not hits:
        return []
    ordered = sorted(hits, key=lambda item: item["timestamp_seconds"])
    groups: list[list[dict]] = [[ordered[0]]]
    for hit in ordered[1:]:
        if hit["timestamp_seconds"] - groups[-1][-1]["timestamp_seconds"] <= merge_gap_seconds:
            groups[-1].append(hit)
        else:
            groups.append([hit])
    return groups


def attach_gore_tag_evidence(
    intervals: list[dict], hits: list[dict], merge_gap_seconds: float,
) -> list[dict]:
    """Add ``gore_tag_evidence`` to the intervals ``group_hits`` built from ``hits``.

    The hits are regrouped with the same rule as ``group_hits``
    (``_gore_hit_groups``), so group n is interval n; nothing else in an interval
    is touched. Should the groups ever disagree with the intervals (count, size
    or strongest score), no interval gets evidence (and the gore triage then
    moves nothing) instead of the scan failing or citing the wrong frames.
    """
    groups = _gore_hit_groups(hits, merge_gap_seconds)
    if len(groups) != len(intervals) or any(
        len(group) != int(interval.get("sample_count") or 0)
        or round(max(float(hit["score"]) for hit in group), 6) != interval.get("max_score")
        for group, interval in zip(groups, intervals)
    ):
        return intervals
    for group, interval in zip(groups, intervals):
        evidence = gore_interval_tag_evidence(group)
        if evidence is not None:
            interval["gore_tag_evidence"] = evidence
    return intervals


def adult_policy_decision(
    probabilities: Mapping[str, float],
    *,
    threshold: float = 0.15,
    cooccurrence_threshold: float = 0.05,
    minimum_cooccurring_labels: int = 3,
) -> tuple[bool, float, int, str]:
    score = probabilistic_union(
        [probabilities.get(label, 0.0) for label in ADULT_EXPLICIT_LABELS]
    )
    cooccurring = sum(
        probabilities.get(label, 0.0) >= cooccurrence_threshold
        for label in ADULT_EXPLICIT_LABELS
    )
    accepted = score >= threshold and cooccurring >= minimum_cooccurring_labels
    reason = "explicit_anatomy_or_act" if accepted else "below_policy"
    return accepted, score, cooccurring, reason


def gore_policy_decision(
    probabilities: Mapping[str, float],
    *,
    high_threshold: float = 0.20,
    low_threshold: float = 0.05,
    context_threshold: float = 0.02,
    cooccurrence_threshold: float = 0.03,
) -> tuple[bool, float, float, str]:
    gore_score = probabilistic_union(
        [probabilities.get(label, 0.0) for label in GORE_LABELS]
    )
    context_score = probabilistic_union(
        [probabilities.get(label, 0.0) for label in GORE_CONTEXT_LABELS]
    )
    cooccurring = sum(
        probabilities.get(label, 0.0) >= cooccurrence_threshold
        for label in GORE_LABELS
    )
    if gore_score >= high_threshold:
        return True, gore_score, context_score, "high_score"
    if gore_score >= low_threshold and (
        context_score >= context_threshold or cooccurring >= 2
    ):
        return True, gore_score, context_score, "context_confirmed"
    return False, gore_score, context_score, "below_policy"
