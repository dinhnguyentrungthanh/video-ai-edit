from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil
import torch
from PIL import Image
from transformers import AutoImageProcessor, AutoModelForImageClassification


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def binary_metrics(labels: list[int], predictions: list[int]) -> dict:
    if len(labels) != len(predictions):
        raise ValueError("labels and predictions must have the same length")
    tp = sum(a == 1 and b == 1 for a, b in zip(labels, predictions))
    tn = sum(a == 0 and b == 0 for a, b in zip(labels, predictions))
    fp = sum(a == 0 and b == 1 for a, b in zip(labels, predictions))
    fn = sum(a == 1 and b == 0 for a, b in zip(labels, predictions))

    def ratio(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 6) if denominator else None

    sensitivity = ratio(tp, tp + fn)
    specificity = ratio(tn, tn + fp)
    return {
        "samples": len(labels),
        "positive_samples": sum(labels),
        "negative_samples": len(labels) - sum(labels),
        "true_positive": tp,
        "true_negative": tn,
        "false_positive": fp,
        "false_negative": fn,
        "accuracy": ratio(tp + tn, len(labels)),
        "precision": ratio(tp, tp + fp),
        "recall": sensitivity,
        "specificity": specificity,
        "balanced_accuracy": (
            round((sensitivity + specificity) / 2, 6)
            if sensitivity is not None and specificity is not None else None
        ),
        "f1": ratio(2 * tp, 2 * tp + fp + fn),
    }


def _read_manifest(project_root: Path, manifest_path: Path) -> list[dict]:
    rows: list[dict] = []
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row_number, row in enumerate(csv.DictReader(handle), start=2):
            missing = {"sample_id", "image_path", "style", "label"} - set(row)
            if missing:
                raise ValueError(f"Manifest is missing columns: {sorted(missing)}")
            image_path = (project_root / row["image_path"]).resolve(strict=True)
            if project_root != image_path and project_root not in image_path.parents:
                raise ValueError(f"Row {row_number} leaves project root: {image_path}")
            label = int(row["label"])
            if label not in (0, 1):
                raise ValueError(f"Row {row_number} label must be 0 or 1")
            rows.append({**row, "label": label, "resolved_path": image_path})
    if not rows:
        raise ValueError("Manifest contains no samples")
    return rows


def benchmark_images(
    *,
    project_root: Path,
    manifest_path: Path,
    report_dir: Path,
    model_path: Path,
    positive_labels: tuple[str, ...],
    threshold: float,
    batch_size: int,
    device_name: str,
    max_error_thumbnails: int = 5,
) -> dict:
    project_root = project_root.resolve(strict=True)
    manifest_path = manifest_path.resolve(strict=True)
    model_path = model_path.resolve(strict=True)
    report_dir = report_dir.resolve()
    reports_root = (project_root / "reports").resolve(strict=True)
    if report_dir != reports_root and reports_root not in report_dir.parents:
        raise ValueError(f"Report directory must stay inside {reports_root}")
    if not 0 <= threshold <= 1 or batch_size <= 0:
        raise ValueError("threshold must be 0..1 and batch_size must be positive")

    rows = _read_manifest(project_root, manifest_path)
    device = torch.device(device_name)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but PyTorch cannot access the GPU")

    manifest = json.loads((model_path / "manifest.json").read_text(encoding="utf-8")) if (model_path / "manifest.json").exists() else {}
    backend = manifest.get("backend", "transformers")
    processor = None
    transform = None
    if backend == "transformers":
        processor = AutoImageProcessor.from_pretrained(model_path, local_files_only=True)
        model = AutoModelForImageClassification.from_pretrained(
            model_path, local_files_only=True, use_safetensors=True
        ).to(device)
        label_map = {int(index): str(label) for index, label in model.config.id2label.items()}
    elif backend in {"timm", "timm_multilabel"}:
        import timm
        from safetensors.torch import load_file

        config = json.loads((model_path / "config.json").read_text(encoding="utf-8"))
        if backend == "timm_multilabel":
            with (model_path / "selected_tags.csv").open("r", encoding="utf-8-sig", newline="") as handle:
                labels = [row["name"] for row in csv.DictReader(handle)]
        else:
            labels = list(config["pretrained_cfg"]["label_names"])
        label_map = {index: label for index, label in enumerate(labels)}
        model = timm.create_model(
            config["architecture"],
            pretrained=False,
            num_classes=config["num_classes"],
            **config.get("model_args", {}),
        )
        model.load_state_dict(load_file(model_path / "model.safetensors"))
        transform = timm.data.create_transform(
            input_size=tuple(config["pretrained_cfg"]["input_size"]),
            interpolation=config["pretrained_cfg"]["interpolation"],
            crop_pct=float(config["pretrained_cfg"]["crop_pct"]),
            mean=tuple(config["pretrained_cfg"]["mean"]),
            std=tuple(config["pretrained_cfg"]["std"]),
            is_training=False,
        )
    else:
        raise ValueError(f"Unsupported model backend: {backend}")
    model = model.to(device).eval()
    wanted = {label.casefold() for label in positive_labels}
    positive_indices = [index for index, label in label_map.items() if label.casefold() in wanted]
    if not positive_indices:
        raise ValueError(f"No positive labels found. Model labels: {label_map}")

    process = psutil.Process(os.getpid())
    peak_rss = process.memory_info().rss
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    started = time.perf_counter()
    results: list[dict] = []
    for offset in range(0, len(rows), batch_size):
        current = rows[offset : offset + batch_size]
        images = []
        for row in current:
            with Image.open(row["resolved_path"]) as source:
                images.append(source.convert("RGB"))
        with torch.inference_mode():
            if backend == "transformers":
                assert processor is not None
                inputs = {
                    key: value.to(device)
                    for key, value in processor(images=images, return_tensors="pt").items()
                }
                probabilities = torch.softmax(model(**inputs).logits, dim=-1).cpu()
            else:
                assert transform is not None
                pixels = torch.stack([transform(image) for image in images]).to(device)
                logits = model(pixels)
                probabilities = (
                    torch.sigmoid(logits) if backend == "timm_multilabel" else torch.softmax(logits, dim=-1)
                ).cpu()
        for row, vector in zip(current, probabilities):
            positive_score = float(
                1 - torch.prod(1 - vector[positive_indices])
                if backend == "timm_multilabel"
                else vector[positive_indices].sum()
            )
            best_index = int(vector.argmax())
            results.append(
                {
                    "sample_id": row["sample_id"],
                    "image_path": row["image_path"],
                    "style": row["style"],
                    "expected": row["label"],
                    "predicted": int(positive_score >= threshold),
                    "positive_score": round(positive_score, 6),
                    "top_label": label_map[best_index],
                    "top_score": round(float(vector[best_index]), 6),
                    "sha256": _sha256(row["resolved_path"]),
                    "source": row.get("source", ""),
                    "notes": row.get("notes", ""),
                }
            )
        peak_rss = max(peak_rss, process.memory_info().rss)

    elapsed = time.perf_counter() - started
    report_dir.mkdir(parents=True, exist_ok=True)
    errors_dir = report_dir / "errors"
    copied = 0
    for result in sorted(results, key=lambda item: abs(item["positive_score"] - threshold)):
        if result["expected"] == result["predicted"] or copied >= max_error_thumbnails:
            continue
        errors_dir.mkdir(parents=True, exist_ok=True)
        source_path = (project_root / result["image_path"]).resolve(strict=True)
        target = errors_dir / f"{result['sample_id']}{source_path.suffix.lower()}"
        shutil.copy2(source_path, target)
        result["error_thumbnail"] = f"errors/{target.name}"
        copied += 1

    payload = {
        "schema_version": 1,
        "benchmark_type": "image_classification",
        "status": "COMPLETED",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "model": manifest,
        "backend": backend,
        "model_labels": (
            label_map
            if len(label_map) <= 100
            else {index: label_map[index] for index in positive_indices}
        ),
        "model_label_count": len(label_map),
        "positive_labels": list(positive_labels),
        "threshold": threshold,
        "metrics": binary_metrics(
            [result["expected"] for result in results],
            [result["predicted"] for result in results],
        ),
        "metrics_by_style": {
            style: binary_metrics(
                [result["expected"] for result in results if result["style"] == style],
                [result["predicted"] for result in results if result["style"] == style],
            )
            for style in sorted({result["style"] for result in results})
        },
        "performance": {
            "elapsed_seconds": round(elapsed, 3),
            "images_per_second": round(len(results) / elapsed, 3) if elapsed else None,
            "peak_process_ram_bytes": peak_rss,
            "peak_cuda_memory_bytes": torch.cuda.max_memory_allocated(device) if device.type == "cuda" else 0,
            "device": str(device),
        },
        "results": results,
    }
    (report_dir / "benchmark.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return payload
