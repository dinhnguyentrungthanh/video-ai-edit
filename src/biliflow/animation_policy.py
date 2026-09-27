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
