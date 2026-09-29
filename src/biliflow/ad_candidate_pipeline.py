from __future__ import annotations

from biliflow.performance import ScanPerformance

import gc
import hashlib
import json
import math
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw


PROMOTIONAL_LABELS = (
    "a full-screen streaming service, distributor, or production company logo intro",
    "an external channel watermark or website advertisement overlaid on a video",
    "a promotional banner, sponsor badge, QR code, or brand name advertisement",
    "an end screen asking viewers to like, subscribe, or watch another video",
)
LEGITIMATE_LABELS = (
    "a normal movie or animation scene with characters and scenery and no advertising",
    "the title of the current movie or episode",
    "ordinary dialogue subtitles belonging to the movie",
)
GROUNDING_PROMPT = (
    "brand logo . channel watermark . website banner . sponsor logo . "
    "promotional badge . streaming platform logo . qr code ."
)


@dataclass(frozen=True)
class BenchmarkExample:
    id: str
    image: str
    expected_positive: bool
    expected_decision: str
    category: str
    expected_region: dict[str, int] | None
    queue: str
    labels: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "image": self.image,
            "expected_positive": self.expected_positive,
            "expected_decision": self.expected_decision,
            "category": self.category,
            "expected_region": self.expected_region,
            "queue": self.queue,
            "labels": list(self.labels),
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _first_existing_image(project_root: Path, item: dict[str, Any]) -> Path | None:
    candidates: list[str] = []
    for key in ("preview_images", "thumbnail_paths", "thumbnails"):
        value = item.get(key, [])
        if isinstance(value, str):
            candidates.append(value)
        elif isinstance(value, list):
            candidates.extend(str(entry) for entry in value)
    for candidate in candidates:
        path = Path(candidate)
        resolved = path if path.is_absolute() else project_root / path
        if resolved.is_file():
            return resolved.resolve()
    return None


def _normalise_region(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    keys = ("x", "y", "width", "height")
    try:
        region = {key: int(round(float(value[key]))) for key in keys}
    except (KeyError, TypeError, ValueError):
        return None
    if region["width"] <= 0 or region["height"] <= 0:
        return None
    return region


def build_regression_manifest(
    project_root: Path,
    regression_path: Path,
    *,
    positive_limit: int = 24,
    negative_limit: int = 24,
) -> list[BenchmarkExample]:
    """Resolve saved human decisions to a deterministic, balanced image set."""

    project_root = project_root.resolve()
    payload = _read_json(regression_path)
    rows = payload.get("examples", [])
    queue_cache: dict[Path, dict[str, Any]] = {}
    positives: list[BenchmarkExample] = []
    negatives: list[BenchmarkExample] = []
    seen_images: set[Path] = set()

    for row in rows:
        category = str(row.get("category", ""))
        if category not in {"visual_logo", "text"}:
            continue
        queue_value = row.get("queue")
        if not queue_value:
            continue
        queue_path = Path(str(queue_value))
        queue_path = queue_path if queue_path.is_absolute() else project_root / queue_path
        queue_path = queue_path.resolve()
        if not queue_path.is_file():
            continue
        queue = queue_cache.setdefault(queue_path, _read_json(queue_path))
        item_id = str(row.get("review_item_id", ""))
        item = next(
            (candidate for candidate in queue.get("items", []) if str(candidate.get("id")) == item_id),
            None,
        )
        if item is None:
            continue
        image_path = _first_existing_image(project_root, item)
        if image_path is None or image_path in seen_images:
            continue
        decision = str(row.get("expected_decision", "")).upper()
        if decision not in {"KEEP", "BLUR", "CUT"}:
            continue
        seen_images.add(image_path)
        expected_region = (
            _normalise_region(row.get("region_source_pixels"))
            or _normalise_region(item.get("decision_region_source_pixels"))
            or _normalise_region(item.get("suggested_region_source_pixels"))
        )
        example = BenchmarkExample(
            id=item_id,
            image=str(image_path),
            expected_positive=decision in {"BLUR", "CUT"},
            expected_decision=decision,
            category=category,
            expected_region=expected_region,
            queue=str(queue_path),
            labels=tuple(str(label) for label in item.get("labels", [])),
        )
        (positives if example.expected_positive else negatives).append(example)

    positives.sort(key=lambda item: (item.category, item.id))
    negatives.sort(key=lambda item: (item.category, item.id))
    if positive_limit > 0:
        positives = positives[:positive_limit]
    if negative_limit > 0:
        negatives = negatives[:negative_limit]
    selected = positives + negatives
    selected.sort(key=lambda item: item.id)
    if not selected:
        raise ValueError("Không tìm thấy ảnh regression hợp lệ để benchmark.")
    return selected


def box_iou_xywh(first: dict[str, int], second: dict[str, int]) -> float:
    ax1, ay1 = first["x"], first["y"]
    ax2, ay2 = ax1 + first["width"], ay1 + first["height"]
    bx1, by1 = second["x"], second["y"]
    bx2, by2 = bx1 + second["width"], by1 + second["height"]
    width = max(0, min(ax2, bx2) - max(ax1, bx1))
    height = max(0, min(ay2, by2) - max(ay1, by1))
    intersection = width * height
    union = first["width"] * first["height"] + second["width"] * second["height"] - intersection
    return intersection / union if union > 0 else 0.0


def binary_metrics(expected: Iterable[bool], predicted: Iterable[bool]) -> dict[str, Any]:
    pairs = list(zip(expected, predicted, strict=True))
    tp = sum(want and got for want, got in pairs)
    tn = sum((not want) and (not got) for want, got in pairs)
    fp = sum((not want) and got for want, got in pairs)
    fn = sum(want and (not got) for want, got in pairs)
    return {
        "count": len(pairs),
        "true_positive": tp,
        "true_negative": tn,
        "false_positive": fp,
        "false_negative": fn,
        "recall": round(tp / (tp + fn), 6) if tp + fn else None,
        "precision": round(tp / (tp + fp), 6) if tp + fp else None,
        "specificity": round(tn / (tn + fp), 6) if tn + fp else None,
        "accuracy": round((tp + tn) / len(pairs), 6) if pairs else None,
    }


def _image_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _model_revision(model_path: Path) -> str:
    manifest = model_path / "manifest.json"
    if not manifest.is_file():
        return model_path.name
    return str(_read_json(manifest).get("revision") or model_path.name)


def _cache_path(
    project_root: Path,
    engine: str,
    image_path: Path,
    model_path: Path,
    settings: dict[str, Any],
) -> Path:
    identity = {
        "image_sha256": _image_digest(image_path),
        "model_revision": _model_revision(model_path),
        "settings": settings,
    }
    key = hashlib.sha256(
        json.dumps(identity, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    return project_root / "cache" / "ad_candidate_pipeline" / engine / f"{key}.json"


def _read_cache(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return _read_json(path)
    except (OSError, ValueError):
        return None


def _write_cache(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _device_name(requested: str) -> str:
    import torch

    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA được yêu cầu nhưng PyTorch không thấy GPU.")
    return requested


def rank_images_siglip(
    project_root: Path,
    examples: list[BenchmarkExample],
    model_path: Path,
    *,
    device_name: str,
    batch_size: int,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    import torch
    from transformers import AutoModel, AutoProcessor

    device_name = _device_name(device_name)
    settings = {
        "positive_labels": PROMOTIONAL_LABELS,
        "negative_labels": LEGITIMATE_LABELS,
    }
    results: dict[str, dict[str, Any]] = {}
    pending: list[tuple[BenchmarkExample, Path]] = []
    cache_hits = 0
    for example in examples:
        image_path = Path(example.image)
        cache = _cache_path(project_root, "siglip", image_path, model_path, settings)
        cached = _read_cache(cache)
        if cached is not None:
            results[example.id] = cached["result"]
            cache_hits += 1
        else:
            pending.append((example, cache))

    started = time.perf_counter()
    peak_bytes = 0
    if pending:
        processor = AutoProcessor.from_pretrained(model_path, local_files_only=True)
        dtype = torch.float16 if device_name == "cuda" else torch.float32
        model = AutoModel.from_pretrained(
            model_path,
            local_files_only=True,
            dtype=dtype,
        ).to(device_name)
        model.eval()
        labels = list(PROMOTIONAL_LABELS + LEGITIMATE_LABELS)
        if device_name == "cuda":
            torch.cuda.reset_peak_memory_stats()
        for offset in range(0, len(pending), max(1, batch_size)):
            batch = pending[offset : offset + max(1, batch_size)]
            images = [Image.open(example.image).convert("RGB") for example, _ in batch]
            inputs = processor(
                text=labels,
                images=images,
                padding="max_length",
                return_tensors="pt",
            )
            inputs = {
                key: value.to(device_name)
                if not (device_name == "cuda" and value.is_floating_point())
                else value.to(device_name, dtype=dtype)
                for key, value in inputs.items()
            }
            with torch.inference_mode():
                output = model(**inputs)
            probabilities = output.logits_per_image.float().sigmoid().cpu()
            for index, (example, cache) in enumerate(batch):
                row = probabilities[index].tolist()
                promo = max(row[: len(PROMOTIONAL_LABELS)])
                legitimate = max(row[len(PROMOTIONAL_LABELS) :])
                result = {
                    "promotional_score": round(float(promo), 6),
                    "legitimate_score": round(float(legitimate), 6),
                    "margin": round(float(promo - legitimate), 6),
                    "label_scores": {
                        label: round(float(score), 6)
                        for label, score in zip(labels, row, strict=True)
                    },
                }
                results[example.id] = result
                _write_cache(cache, {"created_at": _utc_now(), "result": result})
            for image in images:
                image.close()
        if device_name == "cuda":
            peak_bytes = int(torch.cuda.max_memory_allocated())
        del model, processor
        gc.collect()
        if device_name == "cuda":
            torch.cuda.empty_cache()
    return results, {
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "cache_hits": cache_hits,
        "computed": len(pending),
        "peak_cuda_bytes": peak_bytes,
    }


def detect_grounding_dino(
    project_root: Path,
    examples: list[BenchmarkExample],
    model_path: Path,
    *,
    device_name: str,
    threshold: float,
    text_threshold: float,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    import torch
    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

    device_name = _device_name(device_name)
    settings = {
        "prompt": GROUNDING_PROMPT,
        "threshold": threshold,
        "text_threshold": text_threshold,
    }
    results: dict[str, dict[str, Any]] = {}
    pending: list[tuple[BenchmarkExample, Path]] = []
    cache_hits = 0
    for example in examples:
        image_path = Path(example.image)
        cache = _cache_path(project_root, "grounding_dino", image_path, model_path, settings)
        cached = _read_cache(cache)
        if cached is not None:
            results[example.id] = cached["result"]
            cache_hits += 1
        else:
            pending.append((example, cache))

    started = time.perf_counter()
    peak_bytes = 0
    if pending:
        processor = AutoProcessor.from_pretrained(model_path, local_files_only=True)
        dtype = torch.float16 if device_name == "cuda" else torch.float32
        model = AutoModelForZeroShotObjectDetection.from_pretrained(
            model_path,
            local_files_only=True,
            dtype=dtype,
        ).to(device_name)
        model.eval()
        if device_name == "cuda":
            torch.cuda.reset_peak_memory_stats()
        for example, cache in pending:
            with Image.open(example.image) as loaded:
                image = loaded.convert("RGB")
            inputs = processor(images=image, text=GROUNDING_PROMPT, return_tensors="pt")
            inputs = {
                key: value.to(device_name)
                if not (device_name == "cuda" and value.is_floating_point())
                else value.to(device_name, dtype=dtype)
                for key, value in inputs.items()
            }
            with torch.inference_mode():
                output = model(**inputs)
            processed = processor.post_process_grounded_object_detection(
                output,
                input_ids=inputs.get("input_ids"),
                threshold=threshold,
                text_threshold=text_threshold,
                target_sizes=[(image.height, image.width)],
            )[0]
            boxes = _grounding_boxes(processed)
            max_iou = None
            if example.expected_region:
                max_iou = max(
                    (box_iou_xywh(example.expected_region, box) for box in boxes),
                    default=0.0,
                )
            result = {
                "detected": bool(boxes),
                "boxes": boxes,
                "max_score": max((box["score"] for box in boxes), default=0.0),
                "max_expected_region_iou": (
                    round(float(max_iou), 6) if max_iou is not None else None
                ),
                "image_width": image.width,
                "image_height": image.height,
            }
            results[example.id] = result
            _write_cache(cache, {"created_at": _utc_now(), "result": result})
            image.close()
        if device_name == "cuda":
            peak_bytes = int(torch.cuda.max_memory_allocated())
        del model, processor
        gc.collect()
        if device_name == "cuda":
            torch.cuda.empty_cache()
    return results, {
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "cache_hits": cache_hits,
        "computed": len(pending),
        "peak_cuda_bytes": peak_bytes,
    }


def _save_contact_sheet(
    report_dir: Path,
    examples: list[BenchmarkExample],
    records: dict[str, dict[str, Any]],
) -> str | None:
    chosen = sorted(
        examples,
        key=lambda example: (
            records[example.id]["expected_positive"]
            == records[example.id]["cascade_predicted"],
            abs(records[example.id]["ranker"]["margin"]),
        ),
    )[:12]
    if not chosen:
        return None
    width, height = 320, 220
    canvas = Image.new("RGB", (width * 3, height * math.ceil(len(chosen) / 3)), "#121821")
    draw = ImageDraw.Draw(canvas)
    for index, example in enumerate(chosen):
        with Image.open(example.image) as loaded:
            image = loaded.convert("RGB")
            image.thumbnail((width, height - 35))
        x = (index % 3) * width
        y = (index // 3) * height
        canvas.paste(image, (x, y))
        record = records[example.id]
        colour = "#63d59b" if record["expected_positive"] == record["cascade_predicted"] else "#ff6b7a"
        draw.text(
            (x + 5, y + height - 30),
            f"{example.expected_decision} / {'HIT' if record['cascade_predicted'] else 'KEEP'}",
            fill=colour,
        )
    path = report_dir / "error-sample-contact-sheet.jpg"
    canvas.save(path, quality=88)
    return str(path.resolve())


def benchmark_ad_candidates(
    project_root: Path,
    regression_path: Path,
    report_dir: Path,
    grounding_model_path: Path,
    ranker_model_path: Path,
    *,
    device_name: str = "cuda",
    batch_size: int = 8,
    positive_limit: int = 24,
    negative_limit: int = 24,
    grounding_threshold: float = 0.25,
    text_threshold: float = 0.20,
    rank_margin_threshold: float = 0.0,
) -> dict[str, Any]:
    """Benchmark local ad/logo candidate models without mutating any review queue."""

    report_dir.mkdir(parents=True, exist_ok=True)
    examples = build_regression_manifest(
        project_root,
        regression_path,
        positive_limit=positive_limit,
        negative_limit=negative_limit,
    )
    ranker, ranker_metrics = rank_images_siglip(
        project_root,
        examples,
        ranker_model_path,
        device_name=device_name,
        batch_size=batch_size,
    )
    detector, detector_metrics = detect_grounding_dino(
        project_root,
        examples,
        grounding_model_path,
        device_name=device_name,
        threshold=grounding_threshold,
        text_threshold=text_threshold,
    )

    records: dict[str, dict[str, Any]] = {}
    for example in examples:
        rank_predicted = ranker[example.id]["margin"] >= rank_margin_threshold
        detector_predicted = bool(detector[example.id]["detected"])
        cascade_predicted = rank_predicted and detector_predicted
        records[example.id] = {
            **example.as_dict(),
            "ranker": ranker[example.id],
            "detector": detector[example.id],
            "rank_predicted": rank_predicted,
            "detector_predicted": detector_predicted,
            "cascade_predicted": cascade_predicted,
        }

    expected = [example.expected_positive for example in examples]
    rank_predictions = [records[example.id]["rank_predicted"] for example in examples]
    detector_predictions = [records[example.id]["detector_predicted"] for example in examples]
    cascade_predictions = [records[example.id]["cascade_predicted"] for example in examples]
    ranked = sorted(examples, key=lambda item: ranker[item.id]["margin"], reverse=True)
    top_count = max(1, math.ceil(len(ranked) / 2))
    positive_total = sum(expected)
    positive_in_top_half = sum(item.expected_positive for item in ranked[:top_count])
    ious = [
        detector[example.id]["max_expected_region_iou"]
        for example in examples
        if detector[example.id]["max_expected_region_iou"] is not None
    ]
    blur_region_ious = [
        detector[example.id]["max_expected_region_iou"]
        for example in examples
        if example.expected_decision == "BLUR"
        and detector[example.id]["max_expected_region_iou"] is not None
    ]
    grounding_region_recall = (
        sum(value >= 0.05 for value in blur_region_ious) / len(blur_region_ious)
        if blur_region_ious else 0.0
    )
    grounding_tight_region_rate = (
        sum(value >= 0.50 for value in blur_region_ious) / len(blur_region_ious)
        if blur_region_ious else 0.0
    )
    ranker_top_half_recall = (
        positive_in_top_half / positive_total if positive_total else 0.0
    )
    summary = {
        "schema_version": 1,
        "created_at": _utc_now(),
        "mode": "OFFLINE_BENCHMARK_ONLY",
        "production_enabled": False,
        "automatic_edit": False,
        "human_approval_required": True,
        "regression_manifest": str(regression_path.resolve()),
        "models": {
            "ranker": str(ranker_model_path.resolve()),
            "detector": str(grounding_model_path.resolve()),
        },
        "settings": {
            "device": device_name,
            "batch_size": batch_size,
            "positive_limit": positive_limit,
            "negative_limit": negative_limit,
            "grounding_threshold": grounding_threshold,
            "text_threshold": text_threshold,
            "rank_margin_threshold": rank_margin_threshold,
        },
        "sample": {
            "count": len(examples),
            "positive": positive_total,
            "negative": len(examples) - positive_total,
        },
        "metrics": {
            "siglip_ranker": binary_metrics(expected, rank_predictions),
            "grounding_dino": binary_metrics(expected, detector_predictions),
            "cascade": binary_metrics(expected, cascade_predictions),
            "ranker_top_half_recall": round(ranker_top_half_recall, 6),
            "detector_mean_expected_region_iou": (
                round(sum(ious) / len(ious), 6) if ious else None
            ),
            "grounding_blur_region_recall_iou_0_05": round(grounding_region_recall, 6),
            "grounding_blur_tight_region_rate_iou_0_50": round(
                grounding_tight_region_rate, 6
            ),
            "grounding_blur_region_samples": len(blur_region_ious),
            "timing": {
                "siglip": ranker_metrics,
                "grounding_dino": detector_metrics,
            },
        },
        "acceptance_gate": {
            "candidate_router": {
                "required_recall": 0.85,
                "required_specificity": 0.60,
                "passed": (
                    (binary_metrics(expected, cascade_predictions)["recall"] or 0.0) >= 0.85
                    and (binary_metrics(expected, cascade_predictions)["specificity"] or 0.0) >= 0.60
                ),
            },
            "grounding_region_proposer": {
                "required_blur_region_recall_iou_0_05": 0.85,
                "passed": grounding_region_recall >= 0.85,
                "allowed_role": "supplemental_region_proposal_after_semantic_confirmation",
                "automatic_edit_allowed": False,
            },
            "siglip_ranker": {
                "required_top_half_recall": 0.85,
                "passed": ranker_top_half_recall >= 0.85,
                "production_enabled": False,
            },
            "passed": False,
        },
        "records": [records[example.id] for example in examples],
    }
    summary["contact_sheet"] = _save_contact_sheet(report_dir, examples, records)
    output_path = report_dir / "benchmark.json"
    output_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary



def _intersection_over_smaller(first: dict[str, Any], second: dict[str, Any]) -> float:
    ax1, ay1 = float(first["x"]), float(first["y"])
    ax2, ay2 = ax1 + float(first["width"]), ay1 + float(first["height"])
    bx1, by1 = float(second["x"]), float(second["y"])
    bx2, by2 = bx1 + float(second["width"]), by1 + float(second["height"])
    intersection = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(
        0.0, min(ay2, by2) - max(ay1, by1)
    )
    smaller = min(
        float(first["width"]) * float(first["height"]),
        float(second["width"]) * float(second["height"]),
    )
    return intersection / smaller if smaller > 0 else 0.0


def select_grounding_fallback(
    boxes: list[dict[str, Any]],
    existing_regions: list[dict[str, Any]],
    *,
    image_size: tuple[int, int],
    focus_region: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Select one conservative DINO box that expands/supports an existing candidate."""

    width, height = image_size
    image_area = max(1, width * height)
    ranked: list[tuple[tuple[float, ...], dict[str, Any]]] = []
    for box in boxes:
        area = int(box["width"]) * int(box["height"])
        area_ratio = area / image_area
        if area_ratio < 0.0001 or area_ratio > 0.35:
            continue
        overlap = max(
            (_intersection_over_smaller(box, region) for region in existing_regions),
            default=0.0,
        )
        center_x = int(box["x"]) + int(box["width"]) / 2
        center_y = int(box["y"]) + int(box["height"]) / 2
        focus_match = bool(
            focus_region
            and float(focus_region["x"]) <= center_x
            <= float(focus_region["x"]) + float(focus_region["width"])
            and float(focus_region["y"]) <= center_y
            <= float(focus_region["y"]) + float(focus_region["height"])
        )
        if existing_regions and overlap < 0.50 and not focus_match:
            continue
        label = str(box.get("label", "")).casefold()
        label_rank = 1.0 if any(
            token in label for token in ("brand logo", "channel watermark", "website banner")
        ) else 0.0
        ranked.append(((
            overlap,
            1.0 if focus_match else 0.0,
            label_rank,
            float(box.get("score", 0.0)),
            -area_ratio,
        ), box))
    if not ranked:
        return None
    selected = max(ranked, key=lambda item: item[0])[1]
    for region in existing_regions:
        if (
            box_iou_xywh(selected, region) >= 0.75
            and int(selected["width"]) * int(selected["height"])
            <= int(region["width"]) * int(region["height"]) * 1.20
        ):
            return None
    return selected


def _grounding_boxes(processed: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert one post-processed GroundingDINO result into region dicts.

    transformers 5.x decodes the labels with ``batch_decode``, which returns
    ``['']`` for an empty list; a frame without any detection above threshold
    therefore reports one label and zero boxes. Such a frame has no regions.
    Any other length mismatch still fails loudly.
    """
    box_values = processed["boxes"].cpu().tolist()
    labels = processed.get("text_labels", processed.get("labels", []))
    if not box_values:
        return []
    boxes: list[dict[str, Any]] = []
    for box, score, label in zip(
        box_values,
        processed["scores"].float().cpu().tolist(),
        labels,
        strict=True,
    ):
        x1, y1, x2, y2 = box
        boxes.append({
            "x": int(round(x1)),
            "y": int(round(y1)),
            "width": max(1, int(round(x2 - x1))),
            "height": max(1, int(round(y2 - y1))),
            "score": round(float(score), 6),
            "label": str(label),
        })
    return boxes


def _run_extractions(commands: list[list[str]], workers: int = 4, runner=subprocess.run) -> None:
    if not commands:
        return
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(runner, command, check=True, capture_output=True) for command in commands]
        try:
            for future in futures:
                future.result()
        except BaseException:
            for future in futures:
                future.cancel()
            raise


def augment_grounding_regions(
    project_root: Path,
    report_path: Path,
    output_path: Path,
    model_path: Path,
    ffmpeg_path: Path,
    *,
    device_name: str = "cuda",
    threshold: float = 0.25,
    text_threshold: float = 0.20,
) -> dict[str, Any]:
    """Add at most one DINO fallback box to already confirmed visual intervals."""

    performance = ScanPerformance()
    report = _read_json(report_path)
    if report.get("scan_type") != "visual_logo":
        raise ValueError("Input must be a visual-logo scan report")
    intervals = report.get("intervals", [])
    examples: list[BenchmarkExample] = []
    interval_by_id: dict[str, dict[str, Any]] = {}
    extractions: list[list[str]] = []
    for index, interval in enumerate(intervals):
        confirmation = interval.get("visual_logo_confirmation", {})
        if confirmation.get("state") not in {"CONFIRMED", "UNCERTAIN"}:
            continue
        localized_proposals = interval.get("region_localization", {}).get("proposals", [])
        if any(
            isinstance(proposal, dict)
            and "brand_memory" in (proposal.get("sources") or [])
            and proposal.get("region_classification") == "external_brand"
            for proposal in localized_proposals
        ):
            continue
        features = confirmation.get("features", {})
        opening_promotion = (
            float(interval.get("start_seconds", 0.0)) < 30.0
            and bool(confirmation.get("boundary_window"))
            and float(features.get("full_frame_score") or 0.0) >= 0.65
        )
        if opening_promotion:
            continue
        timestamp = interval.get("region_localization", {}).get("timestamp_seconds")
        if timestamp is None:
            timestamp = (
                float(interval.get("start_seconds", 0.0))
                + float(interval.get("end_seconds", 0.0))
            ) / 2
        identifier = f"interval-{index:05d}"
        frame_dir = output_path.parent / "grounding-frames"
        frame_dir.mkdir(parents=True, exist_ok=True)
        frame_path = frame_dir / f"{identifier}-{float(timestamp):.3f}s.jpg"
        if not frame_path.is_file():
            video_path = Path(str(report.get("input", "")))
            if not video_path.is_file():
                raise FileNotFoundError(f"Input video is missing: {video_path}")
            extractions.append([
                str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{float(timestamp):.3f}", "-i", str(video_path),
                "-frames:v", "1", "-q:v", "2", str(frame_path),
            ])
        examples.append(BenchmarkExample(
            id=identifier,
            image=str(frame_path.resolve()),
            expected_positive=True,
            expected_decision="BLUR",
            category="visual_logo",
            expected_region=None,
            queue=str(report_path.resolve()),
            labels=tuple(),
        ))
        interval_by_id[identifier] = interval
    # Same commands as before, at most four FFmpeg processes at a time; each
    # writes its own file, so the frames given to DINO are unchanged.
    performance.call("frame_extract", _run_extractions, extractions)

    started = time.perf_counter()
    detections, inference_metrics = performance.call("model_and_cache", detect_grounding_dino,
        project_root,
        examples,
        model_path,
        device_name=device_name,
        threshold=threshold,
        text_threshold=text_threshold,
    ) if examples else ({}, {
        "elapsed_seconds": 0.0, "cache_hits": 0, "computed": 0, "peak_cuda_bytes": 0,
    })
    added = 0
    for example in examples:
        interval = interval_by_id[example.id]
        localization = interval.setdefault("region_localization", {})
        proposals = localization.setdefault("proposals", [])
        frame_width = int(detections[example.id]["image_width"])
        frame_height = int(detections[example.id]["image_height"])
        localization["frame_size"] = [frame_width, frame_height]
        existing_regions: list[dict[str, Any]] = []
        for proposal in proposals:
            value = proposal.get("blur_region_px")
            if isinstance(value, list) and len(value) == 4:
                existing_regions.append({
                    "x": int(value[0]), "y": int(value[1]),
                    "width": int(value[2]), "height": int(value[3]),
                })
        features = interval.get("visual_logo_confirmation", {}).get("features", {})
        raw_focus = features.get("focus_box_analysis")
        focus_region = None
        analysis_size = report.get("analysis_size")
        if (
            isinstance(raw_focus, list) and len(raw_focus) == 4
            and isinstance(analysis_size, list) and len(analysis_size) == 2
        ):
            scale_x = frame_width / float(analysis_size[0])
            scale_y = frame_height / float(analysis_size[1])
            focus_region = {
                "x": float(raw_focus[0]) * scale_x,
                "y": float(raw_focus[1]) * scale_y,
                "width": (float(raw_focus[2]) - float(raw_focus[0])) * scale_x,
                "height": (float(raw_focus[3]) - float(raw_focus[1])) * scale_y,
            }
        if len(existing_regions) > 1 and interval.get("candidate_type") != "persistent_overlay":
            continue
        selected = select_grounding_fallback(
            detections[example.id]["boxes"],
            existing_regions,
            image_size=(frame_width, frame_height),
            focus_region=focus_region,
        )
        if selected is None:
            continue
        padding = max(6, round(min(frame_width, frame_height) * 0.012))
        x = max(0, int(selected["x"]) - padding)
        y = max(0, int(selected["y"]) - padding)
        right = min(frame_width, int(selected["x"]) + int(selected["width"]) + padding)
        bottom = min(frame_height, int(selected["y"]) + int(selected["height"]) + padding)
        proposals.insert(0, {
            "box_xyxy": [
                int(selected["x"]), int(selected["y"]),
                int(selected["x"]) + int(selected["width"]),
                int(selected["y"]) + int(selected["height"]),
            ],
            "blur_region_px": [x, y, max(1, right - x), max(1, bottom - y)],
            "sources": ["grounding_dino"],
            "labels": [str(selected.get("label", "brand logo candidate"))],
            "score": float(selected.get("score", 0.0)),
            # DINO localizes a phrase but does not identify the pixels as the
            # same brand observed by the frame-level VLM.  Keep it as a
            # recoverable candidate; region OCR or brand memory must provide
            # the independent proof needed for a BLUR suggestion.
            "region_classification": "unknown",
            "classification_reason": (
                "GroundingDINO localized a possible mark; exact-region brand proof is still required"
            ),
            "suggested_decision": None,
            "requires_human_review": True,
            "automatic_edit": False,
        })
        localization["proposal_count"] = len(proposals)
        localization["models"] = list(dict.fromkeys(
            [str(localization.get("model", "")), "IDEA-Research/grounding-dino-tiny"]
        ))
        added += 1

    summary = {
        "created_at": _utc_now(),
        "routing": "confirmed visual interval -> GroundingDINO supplemental region",
        "intervals_considered": len(examples),
        "intervals_with_added_region": added,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "inference": inference_metrics,
        "automatic_edit": False,
        "requires_human_review": True,
    }
    report.setdefault("metrics", {})["grounding_performance"] = performance.snapshot()
    report["grounding_region_summary"] = summary
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output_path)
    return summary

