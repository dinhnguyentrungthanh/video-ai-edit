from __future__ import annotations

import json
from pathlib import Path

from biliflow.animation_policy import probabilistic_union


def metrics(rows: list[dict], field: str, threshold: float) -> dict:
    tp = sum(row["expected"] == 1 and row[field] >= threshold for row in rows)
    fn = sum(row["expected"] == 1 and row[field] < threshold for row in rows)
    tn = sum(row["expected"] == 0 and row[field] < threshold for row in rows)
    fp = sum(row["expected"] == 0 and row[field] >= threshold for row in rows)
    recall = tp / (tp + fn) if tp + fn else 0.0
    specificity = tn / (tn + fp) if tn + fp else 0.0
    precision = tp / (tp + fp) if tp + fp else 0.0
    return {
        "tp": tp, "fn": fn, "tn": tn, "fp": fp,
        "recall": round(recall, 6), "specificity": round(specificity, 6),
        "precision": round(precision, 6),
        "balanced_accuracy": round((recall + specificity) / 2, 6),
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    path = root / "reports" / "violence-sintel-policy-v1" / "scores.json"
    rows = json.loads(path.read_text(encoding="utf-8"))["results"]
    for row in rows:
        probabilities = row["probabilities"]
        row["weapon_score"] = probabilistic_union(
            [
                probabilities[label]
                for label in ("weapon", "holding_weapon", "sword", "holding_sword")
            ]
        )
        row["combat_score"] = probabilistic_union(
            [row["direct_score"], row["weapon_score"]]
        )
    for split in ("calibration", "holdout"):
        current = [row for row in rows if row["split"] == split]
        print(f"SPLIT {split}")
        for field in ("direct_score", "expanded_score", "weapon_score", "combat_score"):
            for threshold in (
                0.01, 0.02, 0.03, 0.04, 0.05, 0.075, 0.10, 0.15, 0.20,
                0.25, 0.30, 0.40, 0.50,
            ):
                print(field, threshold, json.dumps(metrics(current, field, threshold)))
    holdout_positive = [
        row for row in rows if row["split"] == "holdout" and row["expected"] == 1
    ]
    holdout_negative = [
        row for row in rows if row["split"] == "holdout" and row["expected"] == 0
    ]
    print("LABEL DISTRIBUTIONS HOLDOUT")
    labels = holdout_positive[0]["probabilities"]
    distributions = []
    for label in labels:
        positives = [row["probabilities"][label] for row in holdout_positive]
        negatives = [row["probabilities"][label] for row in holdout_negative]
        distributions.append(
            (
                sum(positives) / len(positives), label, max(positives),
                sum(negatives) / len(negatives), max(negatives),
            )
        )
    for pos_mean, label, pos_max, neg_mean, neg_max in sorted(distributions, reverse=True):
        print(
            f"{label:20} pos_mean={pos_mean:.4f} pos_max={pos_max:.4f} "
            f"neg_mean={neg_mean:.4f} neg_max={neg_max:.4f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
