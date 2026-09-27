from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import timm
import torch
from PIL import Image
from safetensors.torch import load_file

from biliflow.animation_policy import (
    ADULT_EXPLICIT_LABELS,
    DIRECT_VIOLENCE_LABELS,
    GORE_LABELS,
    adult_policy_decision,
    gore_policy_decision,
    probabilistic_union,
)
from biliflow.image_benchmark import _read_manifest, binary_metrics


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=root)
    parser.add_argument(
        "--adult-manifest", type=Path,
        default=root / "annotations" / "adult_poc_benchmark_v2.csv",
    )
    parser.add_argument(
        "--gore-manifest", type=Path,
        default=root / "annotations" / "gore_poc_benchmark.csv",
    )
    parser.add_argument(
        "--violence-manifest", type=Path,
        default=root / "annotations" / "violence_animation_aux_benchmark.csv",
    )
    parser.add_argument(
        "--model", type=Path, default=root / "models" / "wd_vit_tagger_v3"
    )
    parser.add_argument(
        "--report-dir", type=Path,
        default=root / "reports" / "animation-policy-poc-v2",
    )
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    parser.add_argument("--violence-threshold", type=float, default=0.05)
    args = parser.parse_args()

    project_root = args.project_root.resolve(strict=True)
    model_path = args.model.resolve(strict=True)
    device = torch.device(args.device)
    gore_rows = [
        row for row in _read_manifest(project_root, args.gore_manifest.resolve(strict=True))
        if row["style"] == "animation"
    ]
    violence_rows = _read_manifest(
        project_root, args.violence_manifest.resolve(strict=True)
    )
    adult_rows = [
        row for row in _read_manifest(
            project_root, args.adult_manifest.resolve(strict=True)
        )
        if row["style"] == "animation"
    ]
    config = json.loads((model_path / "config.json").read_text(encoding="utf-8"))
    with (model_path / "selected_tags.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        labels = [row["name"] for row in csv.DictReader(handle)]
    label_indices = {label: index for index, label in enumerate(labels)}
    wanted = tuple(
        dict.fromkeys((*ADULT_EXPLICIT_LABELS, *GORE_LABELS, *DIRECT_VIOLENCE_LABELS))
    )
    missing = [label for label in wanted if label not in label_indices]
    if missing:
        raise RuntimeError(f"Missing labels: {missing}")

    model = timm.create_model(
        config["architecture"], pretrained=False, num_classes=config["num_classes"],
        **config.get("model_args", {}),
    )
    model.load_state_dict(load_file(model_path / "model.safetensors"))
    model = model.to(device).eval()
    transform = timm.data.create_transform(
        input_size=tuple(config["pretrained_cfg"]["input_size"]),
        interpolation=config["pretrained_cfg"]["interpolation"],
        crop_pct=float(config["pretrained_cfg"]["crop_pct"]),
        mean=tuple(config["pretrained_cfg"]["mean"]),
        std=tuple(config["pretrained_cfg"]["std"]),
        is_training=False,
    )

    def evaluate(rows: list[dict], category: str) -> tuple[dict, list[dict]]:
        results = []
        for offset in range(0, len(rows), args.batch_size):
            current = rows[offset : offset + args.batch_size]
            images = []
            for row in current:
                with Image.open(row["resolved_path"]) as source:
                    images.append(source.convert("RGB"))
            with torch.inference_mode():
                vectors = torch.sigmoid(
                    model(torch.stack([transform(image) for image in images]).to(device))
                ).cpu()
            for row, vector in zip(current, vectors):
                probabilities = {label: float(vector[label_indices[label]]) for label in wanted}
                if category == "adult":
                    accepted, score, cooccurring, reason = adult_policy_decision(
                        probabilities
                    )
                    result = {
                        "sample_id": row["sample_id"], "expected": row["label"],
                        "predicted": int(accepted),
                        "score": round(score, 6),
                        "cooccurring_labels": cooccurring,
                        "reason": reason,
                    }
                elif category == "gore":
                    accepted, score, context_score, reason = gore_policy_decision(probabilities)
                    result = {
                        "sample_id": row["sample_id"], "expected": row["label"],
                        "predicted": int(accepted), "score": round(score, 6),
                        "context_score": round(context_score, 6), "reason": reason,
                    }
                else:
                    score = probabilistic_union(
                        [probabilities[label] for label in DIRECT_VIOLENCE_LABELS]
                    )
                    result = {
                        "sample_id": row["sample_id"], "expected": row["label"],
                        "predicted": int(score >= args.violence_threshold),
                        "score": round(score, 6), "reason": "direct_violence",
                    }
                results.append(result)
        metrics = binary_metrics(
            [result["expected"] for result in results],
            [result["predicted"] for result in results],
        )
        return metrics, results

    adult_metrics, adult_results = evaluate(adult_rows, "adult")
    gore_metrics, gore_results = evaluate(gore_rows, "gore")
    violence_metrics, violence_results = evaluate(violence_rows, "violence")
    payload = {
        "schema_version": 1,
        "benchmark_type": "animation_policy_v2",
        "adult": {"metrics": adult_metrics, "results": adult_results},
        "gore": {"metrics": gore_metrics, "results": gore_results},
        "violence": {
            "metrics": violence_metrics,
            "results": violence_results,
            "validity": "LEGACY_LABEL_MISMATCH",
            "note": (
                "The legacy positive set contains injury and fire danger scenes, "
                "not confirmed direct-violence actions. Metrics are diagnostic only."
            ),
        },
    }
    report_dir = args.report_dir.resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "benchmark.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
