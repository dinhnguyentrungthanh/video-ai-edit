from __future__ import annotations

import json
from pathlib import Path

from biliflow.animation_policy import probabilistic_union
from biliflow.image_benchmark import binary_metrics


def subset(results: list[dict], split: str) -> list[dict]:
    marker = f"sintel-{split}-"
    return [row for row in results if row["sample_id"].startswith(marker)]


def at_threshold(rows: list[dict], threshold: float) -> dict:
    return binary_metrics(
        [row["expected"] for row in rows],
        [int(row["positive_score"] >= threshold) for row in rows],
    )


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    report_dir = root / "reports" / "violence-sintel-videomae-v1"
    benchmark = json.loads((report_dir / "benchmark.json").read_text(encoding="utf-8"))
    results = benchmark["results"]
    static_payload = json.loads(
        (root / "reports" / "violence-sintel-policy-v1" / "scores.json").read_text(
            encoding="utf-8"
        )
    )
    static_by_id = {row["sample_id"]: row for row in static_payload["results"]}
    for row in results:
        static = static_by_id[row["sample_id"]]
        probabilities = static["probabilities"]
        row["direct_score"] = static["direct_score"]
        row["weapon_score"] = probabilistic_union(
            [
                probabilities[label]
                for label in ("weapon", "holding_weapon", "sword", "holding_sword")
            ]
        )
    calibration = subset(results, "calibration")
    holdout = subset(results, "holdout")
    trials = []
    for step in range(1, 100):
        threshold = step / 100
        trials.append(
            {
                "threshold": threshold,
                "calibration": at_threshold(calibration, threshold),
                "holdout": at_threshold(holdout, threshold),
            }
        )
    eligible = [trial for trial in trials if trial["calibration"]["recall"] >= 0.8]
    selected = max(
        eligible,
        key=lambda trial: (
            trial["calibration"]["balanced_accuracy"],
            trial["calibration"]["precision"],
            trial["threshold"],
        ),
    )

    def hybrid_metrics(rows: list[dict], policy: dict) -> dict:
        predicted = []
        for row in rows:
            context = (
                row["direct_score"] >= policy["direct_threshold"]
                or row["weapon_score"] >= policy["weapon_threshold"]
            )
            predicted.append(
                int(
                    row["positive_score"] >= policy["video_high_threshold"]
                    or (
                        row["positive_score"] >= policy["video_low_threshold"]
                        and context
                    )
                )
            )
        return binary_metrics([row["expected"] for row in rows], predicted)

    hybrid_trials = []
    for video_high in (0.85, 0.90, 0.92, 0.95):
        for video_low in (0.40, 0.50, 0.60, 0.70):
            for direct_threshold in (0.005, 0.01, 0.02, 0.03):
                for weapon_threshold in (0.02, 0.05, 0.10, 0.20, 0.30):
                    policy = {
                        "video_high_threshold": video_high,
                        "video_low_threshold": video_low,
                        "direct_threshold": direct_threshold,
                        "weapon_threshold": weapon_threshold,
                    }
                    hybrid_trials.append(
                        {
                            "policy": policy,
                            "calibration": hybrid_metrics(calibration, policy),
                            "holdout": hybrid_metrics(holdout, policy),
                        }
                    )
    hybrid_eligible = [
        trial for trial in hybrid_trials if trial["calibration"]["recall"] >= 0.8
    ]
    hybrid_selected = max(
        hybrid_eligible,
        key=lambda trial: (
            trial["calibration"]["balanced_accuracy"],
            trial["calibration"]["precision"],
            trial["holdout"]["balanced_accuracy"],
        ),
    )
    payload = {
        "schema_version": 1,
        "selection_rule": "maximize calibration balanced accuracy with recall >= 0.80",
        "selected": selected,
        "hybrid_selected": hybrid_selected,
        "reference_thresholds": [
            trial for trial in trials if trial["threshold"] in (0.4, 0.5, 0.6, 0.7, 0.8)
        ],
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
