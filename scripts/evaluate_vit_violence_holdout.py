from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import timm
import torch
from PIL import Image
from safetensors.torch import load_file
from torchvision.transforms import Compose, Normalize, Resize, ToTensor

from biliflow.image_benchmark import binary_metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    args = parser.parse_args()
    root = args.project_root.resolve(strict=True)
    rows = list(csv.DictReader(args.manifest.resolve(strict=True).open("r", encoding="utf-8-sig")))
    device = torch.device(args.device)
    model = timm.create_model("vit_base_patch16_224", pretrained=False, num_classes=2)
    incompatible = model.load_state_dict(load_file(args.model / "model.safetensors"), strict=False)
    if incompatible.missing_keys or incompatible.unexpected_keys:
        raise RuntimeError(f"Checkpoint mismatch: {incompatible}")
    model = model.to(device).eval()
    transform = Compose(
        [Resize((224, 224)), ToTensor(), Normalize((0.5,) * 3, (0.5,) * 3)]
    )
    started = time.perf_counter()
    results = []
    for offset in range(0, len(rows), 16):
        current = rows[offset : offset + 16]
        tensors = []
        for row in current:
            with Image.open((root / row["image_path"]).resolve(strict=True)) as image:
                tensors.append(transform(image.convert("RGB")))
        with torch.inference_mode():
            scores = torch.softmax(model(torch.stack(tensors).to(device)), dim=-1)[:, 1].cpu()
        for row, score in zip(current, scores):
            results.append(
                {
                    "sample_id": row["sample_id"],
                    "split": row["split"],
                    "expected": int(row["label"]),
                    "score": round(float(score), 8),
                }
            )

    calibration = [item for item in results if item["split"] == "calibration"]
    choices = []
    for integer in range(1, 100):
        threshold = integer / 100
        metrics = binary_metrics(
            [item["expected"] for item in calibration],
            [int(item["score"] >= threshold) for item in calibration],
        )
        if metrics["recall"] >= 0.80:
            choices.append((metrics["balanced_accuracy"], metrics["precision"], threshold, metrics))
    _, _, threshold, calibration_metrics = max(choices)
    summary = {"threshold_from_calibration": threshold, "calibration": calibration_metrics}
    for split in ("holdout", "all"):
        subset = results if split == "all" else [item for item in results if item["split"] == split]
        summary[split] = binary_metrics(
            [item["expected"] for item in subset],
            [int(item["score"] >= threshold) for item in subset],
        )
    payload = {
        "model": str(args.model.resolve()),
        "manifest": str(args.manifest.resolve()),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "summary": summary,
        "results": results,
    }
    print(json.dumps({"elapsed_seconds": payload["elapsed_seconds"], "summary": summary}, indent=2))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
