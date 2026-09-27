from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import timm
import torch
from PIL import Image
from safetensors.torch import load_file

from biliflow.animation_policy import DIRECT_VIOLENCE_LABELS, probabilistic_union


EXPLORATORY_LABELS = (
    "biting", "slashing", "weapon", "holding_weapon", "sword",
    "holding_sword", "dragon", "fire", "injury", "blood", "death", "corpse",
)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest", type=Path,
        default=root / "annotations" / "violence_sintel_benchmark_v1.csv",
    )
    parser.add_argument(
        "--report", type=Path,
        default=root / "reports" / "violence-sintel-policy-v1" / "scores.json",
    )
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    args = parser.parse_args()

    with args.manifest.resolve(strict=True).open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    model_path = root / "models" / "wd_vit_tagger_v3"
    config = json.loads((model_path / "config.json").read_text(encoding="utf-8"))
    with (model_path / "selected_tags.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        labels = [row["name"] for row in csv.DictReader(handle)]
    by_name = {label: index for index, label in enumerate(labels)}
    wanted = tuple(dict.fromkeys((*DIRECT_VIOLENCE_LABELS, *EXPLORATORY_LABELS)))
    missing = [label for label in wanted if label not in by_name]
    if missing:
        raise RuntimeError(f"Missing labels: {missing}")

    device = torch.device(args.device)
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

    results = []
    for offset in range(0, len(rows), args.batch_size):
        current = rows[offset : offset + args.batch_size]
        images = []
        for row in current:
            with Image.open(root / row["image_path"]) as source:
                images.append(source.convert("RGB"))
        with torch.inference_mode():
            vectors = torch.sigmoid(
                model(torch.stack([transform(image) for image in images]).to(device))
            ).cpu()
        for row, vector in zip(current, vectors):
            probabilities = {
                label: round(float(vector[by_name[label]]), 6) for label in wanted
            }
            direct_score = probabilistic_union(
                [probabilities[label] for label in DIRECT_VIOLENCE_LABELS]
            )
            expanded_score = probabilistic_union(
                [
                    probabilities[label]
                    for label in (*DIRECT_VIOLENCE_LABELS, "biting", "slashing")
                ]
            )
            results.append(
                {
                    "sample_id": row["sample_id"],
                    "split": row["split"],
                    "expected": int(row["label"]),
                    "timestamp_seconds": float(row["timestamp_seconds"]),
                    "direct_score": round(direct_score, 6),
                    "expanded_score": round(expanded_score, 6),
                    "probabilities": probabilities,
                }
            )
    payload = {
        "schema_version": 1,
        "manifest": str(args.manifest.resolve()),
        "model_revision": "7f6b584d0bd3f55c4531f14ba3d4761b2bccdc0f",
        "results": results,
    }
    report = args.report.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"report={report} samples={len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
