"""Settings for the anime gore triage and the gore hint on review cards.

docs/ANIME_GORE_PLAN.md steps 1-3. The animation safety scanner records
``gore_tag_evidence`` (strongest blood-family, injury and corpse tag over the
temporally confirmed frames) for every gore interval. ``build-review`` reads it:

* every gore card shows a hint built from it (``gore_evidence_hint``); the hint
  never moves a card or suggests a decision;
* ``review_workflow.triage_anime_gore_items`` may move an undecided ANIMATION
  gore card to the optional list ("Ứng viên phụ") when the tagger saw neither
  blood nor a corpse on any of its intervals (rule C1). It never deletes one.

User decision 2026-10-02: scratches and bruises WITHOUT blood need not be
flagged; gore means real blood (drawn anime blood included); corpses stay
protected. Golden v1 r469 + v1.1 r121 carry that decision.

This module has no heavy imports so the review workflow can read it cheaply.
"""
from __future__ import annotations

from typing import Any

from biliflow.animation_policy import GORE_TAG_EVIDENCE_FIELDS, GORE_TAG_EVIDENCE_VERSION


# Rule C1 (plan §2.3): move only when the tagger sees no blood AND no corpse on
# every interval of the card, i.e. the gore detector fired on the injury tag
# alone (scratches, bruises). Both values are maxima over confirmed frames.
# Evidence: docs/ANIME_GORE_PLAN.md §2.3 and §3; temp/next/anime-gore/items-ff.csv
# (105 items of the two Golden animation films), lone-shots.csv (13 real-blood lone shots, none moved),
# plan-whatif.json (named rule "C_best_margin>=0.1": joint margin 0.17 IDR on
# protection tier C; nearest real blood: lone shot gs-C21E-0006, blood 0.138,
# corpse 0.032; nearest protected item 1146488859a3, corpse 0.030).
# The plan writes the thresholds as 0.07158 / 0.01041 (5-decimal rounding of the
# moved items' maxima) and as 0.0716 / 0.0104 in its summary; the latter are used.
# Two items of those films sit next to them: review-fee07d624ddb (blood 0.071582, moves)
# and review-f7a9432010f2 (corpse 0.0104065, stays).
GORE_TRIAGE_LEVELS: dict[str, dict[str, Any] | None] = {
    "off": None,
    "no_blood_no_corpse": {
        "rule": "C1", "blood_family_max": 0.0716, "corpse_max": 0.0104,
    },
}
# ON since 2026-10-07 at the user's request, after gates 4.1-4.7 passed (plan §6
# step 3): the user's blind check of every card C1 moves on the two Golden
# animation films (gate 4.6) and further animation (gate 4.7: no real blood moved,
# real blood >= 0.30). Moved cards stay in "Ứng viên phụ", which does not block an
# export. Measurements override it through ``build_review_queue(gore_triage_level=...)``;
# there is no CLI flag, because ``cli.py`` is in the cache key of every scan stage.
GORE_TRIAGE_LEVEL = "no_blood_no_corpse"
GORE_TRIAGE_EVIDENCE = (
    "docs/ANIME_GORE_PLAN.md; temp/next/anime-gore/plan-whatif.json; "
    "temp/next/anime-gore/items-ff.csv; temp/next/anime-gore/lone-shots.csv"
)

# The thresholds were measured on wd-vit-tagger-v3 at exactly this scan
# configuration; any other report is "uncalibrated" and moves nothing.
GORE_TRIAGE_MODEL_REPO = "SmilingWolf/wd-vit-tagger-v3"
GORE_TRIAGE_MODEL_REVISION = "7f6b584d0bd3f55c4531f14ba3d4761b2bccdc0f"
GORE_TRIAGE_SCAN_SAMPLE_FPS = 2.0
# The thresholds come from a full-film CUDA fp16 autocast re-score with the
# production settings (temp/next/anime-gore/g1_fullfilm.py, batch 8) that rebuilds
# the production fp16 reports interval for interval. Default (fast) jobs scan in
# fp16; an fp32 scan can move the evidence near the lines, so it is uncalibrated.
GORE_TRIAGE_SCAN_PRECISION = "fp16"
GORE_TRIAGE_SCAN_MERGE_GAP_SECONDS = 6.0
GORE_TRIAGE_SCAN_POLICY = {
    "high_threshold": 0.20,
    "low_threshold": 0.05,
    "context_threshold": 0.02,
    "cooccurrence_threshold": 0.03,
    "high_score_temporal_minimum_hits": 3,
    "context_temporal_minimum_hits": 4,
}
GORE_TRIAGE_TEMPORAL_WINDOW_FRAMES = 5

# Card hint, for presentation only (plan §2.1): it never classifies.
GORE_HINT_BLOOD_SEEN = 0.5
GORE_HINT_BLOOD_NONE = GORE_TRIAGE_LEVELS["no_blood_no_corpse"]["blood_family_max"]
GORE_HINT_CORPSE = 0.05
# Above the C1 corpse level a card is protected as a possible corpse even with a weak tag,
# so the hint says so instead of staying silent on exactly those cards.
GORE_HINT_CORPSE_WEAK = GORE_TRIAGE_LEVELS["no_blood_no_corpse"]["corpse_max"]


def normalize_gore_triage_level(value: str | None) -> str:
    level = GORE_TRIAGE_LEVEL if value is None else str(value)
    if level not in GORE_TRIAGE_LEVELS:
        raise ValueError("Gore triage level must be one of " + ", ".join(GORE_TRIAGE_LEVELS))
    return level


def _close(value: Any, expected: float) -> bool:
    try:
        return abs(float(value) - float(expected)) < 1e-9
    except (TypeError, ValueError):
        return False


def gore_scan_is_calibrated(payload: dict) -> bool:
    """True only for an animation gore report scanned like the C1 measurement."""
    policy = payload.get("two_tier_policy")
    model = payload.get("model")
    evidence_policy = payload.get("gore_tag_evidence_policy")
    temporal = payload.get("temporal_confirmation")
    runtime = payload.get("runtime")
    return (
        isinstance(policy, dict) and isinstance(model, dict)
        and isinstance(evidence_policy, dict) and isinstance(temporal, dict)
        and isinstance(runtime, dict)
        and evidence_policy.get("version") == GORE_TAG_EVIDENCE_VERSION
        and str(model.get("repo_id") or "") == GORE_TRIAGE_MODEL_REPO
        and str(model.get("revision") or "") == GORE_TRIAGE_MODEL_REVISION
        and str(runtime.get("precision") or "") == GORE_TRIAGE_SCAN_PRECISION
        and _close(payload.get("sample_fps"), GORE_TRIAGE_SCAN_SAMPLE_FPS)
        and _close(payload.get("merge_gap_seconds"), GORE_TRIAGE_SCAN_MERGE_GAP_SECONDS)
        and _close(temporal.get("window_frames"), GORE_TRIAGE_TEMPORAL_WINDOW_FRAMES)
        and all(_close(policy.get(key), value) for key, value in GORE_TRIAGE_SCAN_POLICY.items())
    )


def valid_gore_tag_evidence(evidence: Any) -> bool:
    if not isinstance(evidence, dict):
        return False
    for key in GORE_TAG_EVIDENCE_FIELDS:
        try:
            value = float(evidence.get(key))
        except (TypeError, ValueError):
            return False
        if not 0.0 <= value <= 1.0:  # also rejects NaN
            return False
    return True


def gore_rule_matches(evidence: dict, settings: dict) -> bool:
    """C1 on one interval's (or one card's maximum) evidence."""
    return (
        float(evidence["blood_family_max"]) <= settings["blood_family_max"]
        and float(evidence["corpse_max"]) <= settings["corpse_max"]
    )


def gore_evidence_hint(evidence: dict | None) -> str | None:
    """One Vietnamese line for the card: what the tagger saw (not a verdict)."""
    if not valid_gore_tag_evidence(evidence):
        return None
    blood = float(evidence["blood_family_max"])
    injury = float(evidence["injury_max"])
    corpse = float(evidence["corpse_max"])
    if blood >= GORE_HINT_BLOOD_SEEN:
        parts = ["thấy máu"]
    elif blood > GORE_HINT_BLOOD_NONE:
        parts = ["máu yếu, nên xem kỹ"]
    elif injury > blood:
        parts = ["chỉ vết thương, không thấy máu"]
    else:
        parts = ["không thấy máu"]
    if corpse >= GORE_HINT_CORPSE:
        parts.append("có thể là xác")
    elif corpse > GORE_HINT_CORPSE_WEAK:
        parts.append("có dấu hiệu xác (yếu), nên xem kỹ")
    return "Tagger: " + " · ".join(parts)
