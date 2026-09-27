from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import numpy as np
import torch
import timm
from safetensors.torch import load_file
from torchvision.transforms import Compose, Normalize, Resize, ToTensor

from biliflow.image_benchmark import binary_metrics
from biliflow.video_benchmark import _decode_frames


def _best_threshold(labels: list[int], scores: list[float]) -> dict:
    choices = []
    for integer in range(1, 100):
        threshold = integer / 100
        metrics = binary_metrics(labels, [int(score >= threshold) for score in scores])
        if metrics["recall"] >= 0.80:
            choices.append((metrics["balanced_accuracy"], metrics["precision"], threshold, metrics))
    if not choices:
        return {"threshold": None, "metrics": None}
    _, _, threshold, metrics = max(choices)
    return {"threshold": threshold, "metrics": metrics}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-fps", type=float, default=8.0)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    args = parser.parse_args()
    root = args.project_root.resolve(strict=True)
    rows = list(csv.DictReader(args.manifest.resolve(strict=True).open("r", encoding="utf-8-sig")))
    device = torch.device(args.device)
    model = timm.create_model("vit_base_patch16_224", pretrained=False, num_classes=2)
    incompatible = model.load_state_dict(
        load_file(args.model / "model.safetensors"), strict=False
    )
    if incompatible.missing_keys or incompatible.unexpected_keys:
        raise RuntimeError(f"Checkpoint mismatch: {incompatible}")
    model = model.to(device).eval()
    processor = Compose(
        [
            Resize((224, 224)),
            ToTensor(),
            Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
        ]
    )
    results = []
    started = time.perf_counter()
    for index, row in enumerate(rows, start=1):
        frames = _decode_frames(
            args.ffmpeg.resolve(strict=True),
            (root / row["video_path"]).resolve(strict=True),
            args.sample_fps,
            16,
        )
        try:
            inputs = torch.stack([processor(frame) for frame in frames]).to(device)
            with torch.inference_mode():
                frame_scores = torch.softmax(model(inputs), dim=-1)[:, 1].cpu().numpy()
            sorted_scores = np.sort(frame_scores)
            results.append(
                {
                    "sample_id": row["sample_id"],
                    "style": row["style"],
                    "expected": int(row["label"]),
                    "frame_scores": [round(float(score), 8) for score in frame_scores],
                    "mean": float(np.mean(frame_scores)),
                    "max": float(np.max(frame_scores)),
                    "top3_mean": float(np.mean(sorted_scores[-3:])),
                    "top5_mean": float(np.mean(sorted_scores[-5:])),
                    "p75": float(np.percentile(frame_scores, 75)),
                }
            )
        finally:
            for frame in frames:
                frame.close()
        if index % 10 == 0:
            print(f"evaluated {index}/{len(rows)} clips", flush=True)

    aggregations = ("mean", "max", "top3_mean", "top5_mean", "p75")
    summary = {}
    for aggregation in aggregations:
        summary[aggregation] = {}
        for style in ("all", *sorted({item["style"] for item in results})):
            subset = results if style == "all" else [item for item in results if item["style"] == style]
            summary[aggregation][style] = _best_threshold(
                [item["expected"] for item in subset],
                [item[aggregation] for item in subset],
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
