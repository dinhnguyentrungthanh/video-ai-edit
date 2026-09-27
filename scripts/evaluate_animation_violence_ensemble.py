from __future__ import annotations

import csv
import json
from pathlib import Path

from biliflow.image_benchmark import binary_metrics


VIDEO_THRESHOLD = 0.61
WD_HIGH_THRESHOLD = 0.20


def add_prediction(row: dict, *, source_video: str, split: str) -> dict:
    video_positive = float(row["video_score"]) >= VIDEO_THRESHOLD
    wd_positive = float(row["wd_score"]) >= WD_HIGH_THRESHOLD
    return {
        **row,
        "source_video": source_video,
        "split": split,
        "predicted": int(video_positive or wd_positive),
        "trigger": (
            "video_and_wd" if video_positive and wd_positive
            else "video" if video_positive
            else "wd_high" if wd_positive
            else "none"
        ),
    }


def metrics(rows: list[dict]) -> dict:
    return binary_metrics(
        [int(row["expected"]) for row in rows],
        [int(row["predicted"]) for row in rows],
    )


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sintel_video = json.loads(
        (root / "reports" / "violence-sintel-videomae-v1" / "benchmark.json").read_text(
            encoding="utf-8"
        )
    )
    sintel_wd = json.loads(
        (root / "reports" / "violence-sintel-policy-v1" / "scores.json").read_text(
            encoding="utf-8"
        )
    )
    wd_by_id = {row["sample_id"]: row for row in sintel_wd["results"]}
    results = []
    for row in sintel_video["results"]:
        wd = wd_by_id[row["sample_id"]]
        results.append(
            add_prediction(
                {
                    "sample_id": row["sample_id"],
                    "expected": int(row["expected"]),
                    "video_score": float(row["positive_score"]),
                    "wd_score": float(wd["direct_score"]),
                },
                source_video="Sintel",
                split=wd["split"],
            )
        )

    conan_video = json.loads(
        (root / "reports" / "violence-conan-videomae-v1" / "benchmark.json").read_text(
            encoding="utf-8"
        )
    )
    video_by_id = {row["sample_id"]: row for row in conan_video["results"]}
    with (root / "annotations" / "violence_field_audit_v3.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        for row in csv.DictReader(handle):
            if row["review_label"] not in {"POSITIVE", "NEGATIVE"}:
                continue
            video = video_by_id[row["sample_id"]]
            results.append(
                add_prediction(
                    {
                        "sample_id": row["sample_id"],
                        "expected": int(row["review_label"] == "POSITIVE"),
                        "video_score": float(video["positive_score"]),
                        "wd_score": float(row["score"]),
                    },
                    source_video="Conan",
                    split="reviewed_field",
                )
            )

    payload = {
        "schema_version": 1,
        "benchmark_type": "animation_violence_ensemble",
        "policy": {
            "decision": "video_score >= 0.61 OR wd_direct_score >= 0.20",
            "video_model": "KingTechnician/videomae-small-finetuned-kinetics-xd-violence-binary",
            "video_threshold": VIDEO_THRESHOLD,
            "wd_model": "SmilingWolf/wd-vit-tagger-v3",
            "wd_high_threshold": WD_HIGH_THRESHOLD,
        },
        "dataset": {
            "distinct_videos": 2,
            "samples": len(results),
            "positive_samples": sum(row["expected"] for row in results),
            "negative_samples": sum(not row["expected"] for row in results),
            "note": (
                "Sintel frames were manually annotated from a full-film storyboard; "
                "Conan labels are reviewed field candidates and remain provisional."
            ),
        },
        "metrics": metrics(results),
        "metrics_by_video": {
            video: metrics([row for row in results if row["source_video"] == video])
            for video in ("Sintel", "Conan")
        },
        "metrics_by_split": {
            split: metrics([row for row in results if row["split"] == split])
            for split in ("calibration", "holdout", "reviewed_field")
        },
        "results": results,
    }
    destination = root / "reports" / "animation-violence-ensemble-v1" / "benchmark.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ("policy", "dataset", "metrics", "metrics_by_video", "metrics_by_split")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
