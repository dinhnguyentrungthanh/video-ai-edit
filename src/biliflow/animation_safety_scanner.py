from __future__ import annotations

import csv
import heapq
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil
import timm
import torch
from PIL import Image
from safetensors.torch import load_file

from biliflow.animation_policy import (
    ADULT_EXPLICIT_LABELS,
    DANGER_LABELS,
    DIRECT_VIOLENCE_LABELS,
    GORE_CONTEXT_LABELS,
    GORE_LABELS,
)
from biliflow.intervals import compact_interval_thumbnails, group_hits
from biliflow.probe import duration_seconds, probe_video
from biliflow.report import write_report
from biliflow.scanner import _read_exact, _score_summary, sha256_file, temporal_confirm_hits
from biliflow.storage import require_capacity


def _union(probabilities: torch.Tensor, indices: list[int]) -> torch.Tensor:
    return 1 - torch.prod(1 - probabilities[:, indices], dim=1)


def _label_indices(labels: list[str], wanted: tuple[str, ...]) -> list[int]:
    by_name = {label.casefold(): index for index, label in enumerate(labels)}
    missing = [label for label in wanted if label.casefold() not in by_name]
    if missing:
        raise RuntimeError(f"Model is missing required labels: {missing}")
    return [by_name[label.casefold()] for label in wanted]


def _push_candidate(
    heap: list[tuple[float, int, Image.Image, str]],
    *,
    score: float,
    frame_index: int,
    image: Image.Image,
    predicted_label: str,
    limit: int,
) -> None:
    candidate = (score, frame_index, image.copy(), predicted_label)
    if len(heap) < limit:
        heapq.heappush(heap, candidate)
    elif score > heap[0][0]:
        removed = heapq.heapreplace(heap, candidate)
        removed[2].close()
    else:
        candidate[2].close()


def _save_top_candidates(
    report_dir: Path,
    heap: list[tuple[float, int, Image.Image, str]],
    sample_fps: float,
) -> list[dict]:
    destination = report_dir / "candidates"
    destination.mkdir(parents=True, exist_ok=True)
    output = []
    for score, frame_index, image, predicted_label in sorted(heap, reverse=True):
        timestamp = frame_index / sample_fps
        name = f"candidate-{frame_index:08d}-{timestamp:.3f}s.jpg"
        image.save(destination / name, format="JPEG", quality=82, optimize=True)
        image.close()
        output.append(
            {
                "rank": len(output) + 1,
                "frame_index": frame_index,
                "timestamp_seconds": round(timestamp, 3),
                "score": round(score, 6),
                "predicted_label": predicted_label,
                "thumbnail": f"candidates/{name}",
            }
        )
    return output


def scan_animation_safety(
    *,
    project_root: Path,
    input_path: Path,
    report_dir: Path,
    model_path: Path,
    ffmpeg_path: Path,
    ffprobe_path: Path,
    sample_fps: float = 2.0,
    batch_size: int = 8,
    top_k_candidates: int = 20,
    adult_threshold: float = 0.15,
    adult_cooccurrence_threshold: float = 0.05,
    adult_minimum_cooccurring_labels: int = 3,
    gore_high_threshold: float = 0.20,
    gore_low_threshold: float = 0.05,
    gore_context_threshold: float = 0.02,
    gore_cooccurrence_threshold: float = 0.03,
    violence_threshold: float = 0.05,
    violence_high_threshold: float = 0.20,
    danger_threshold: float = 0.05,
    merge_gap_seconds: float = 2.0,
    gore_merge_gap_seconds: float = 6.0,
    padding_seconds: float = 1.0,
    temporal_window_frames: int = 5,
    temporal_minimum_hits: int = 3,
    gore_context_temporal_minimum_hits: int = 4,
    device_name: str = "cuda",
) -> dict:
    project_root = project_root.resolve(strict=True)
    input_path = input_path.resolve(strict=True)
    report_dir = report_dir.resolve()
    model_path = model_path.resolve(strict=True)
    ffmpeg_path = ffmpeg_path.resolve(strict=True)
    ffprobe_path = ffprobe_path.resolve(strict=True)
    reports_root = (project_root / "reports").resolve(strict=True)
    if report_dir != reports_root and reports_root not in report_dir.parents:
        raise ValueError(f"Report directory must stay inside {reports_root}")
    if (
        sample_fps <= 0 or batch_size <= 0 or top_k_candidates <= 0
        or merge_gap_seconds < 0 or gore_merge_gap_seconds < 0
    ):
        raise ValueError("Sampling and candidate settings must be positive")
    thresholds = (
        adult_threshold, adult_cooccurrence_threshold, gore_high_threshold,
        gore_low_threshold, gore_context_threshold,
        gore_cooccurrence_threshold, violence_threshold, violence_high_threshold,
        danger_threshold,
    )
    if any(not 0 <= value <= 1 for value in thresholds):
        raise ValueError("Thresholds must be between zero and one")
    if gore_high_threshold < gore_low_threshold:
        raise ValueError("Gore high threshold cannot be below low threshold")
    if adult_minimum_cooccurring_labels <= 0:
        raise ValueError("Adult cooccurrence requirement must be positive")
    if not 1 <= gore_context_temporal_minimum_hits <= temporal_window_frames:
        raise ValueError("Invalid gore context temporal settings")

    input_stat = input_path.stat()
    require_capacity(
        project_root,
        estimated_job_gb=max(4.0, input_stat.st_size / 1024**3 * 3),
    )
    video_duration = duration_seconds(probe_video(ffprobe_path, input_path))
    device = torch.device(device_name)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but PyTorch cannot access the GPU")

    config = json.loads((model_path / "config.json").read_text(encoding="utf-8"))
    manifest = json.loads((model_path / "manifest.json").read_text(encoding="utf-8"))
    with (model_path / "selected_tags.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        labels = [row["name"] for row in csv.DictReader(handle)]
    gore_indices = _label_indices(labels, GORE_LABELS)
    context_indices = _label_indices(labels, GORE_CONTEXT_LABELS)
    violence_indices = _label_indices(labels, DIRECT_VIOLENCE_LABELS)
    danger_indices = _label_indices(labels, DANGER_LABELS)
    adult_indices = _label_indices(labels, ADULT_EXPLICIT_LABELS)

    model = timm.create_model(
        config["architecture"],
        pretrained=False,
        num_classes=config["num_classes"],
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

    adult_dir = report_dir / "adult"
    gore_dir = report_dir / "gore"
    violence_dir = report_dir / "violence"
    for category_dir in (adult_dir, gore_dir, violence_dir):
        (category_dir / "thumbnails").mkdir(parents=True, exist_ok=True)

    width = height = 256
    frame_bytes = width * height * 3
    command = [
        str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-i", str(input_path),
        "-vf",
        (
            f"fps={sample_fps},scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
        ),
        "-an", "-sn", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]

    process = psutil.Process(os.getpid())
    peak_rss = process.memory_info().rss
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    started = time.perf_counter()
    frames_scanned = 0
    adult_hits: list[dict] = []
    gore_hits: list[dict] = []
    violence_hits: list[dict] = []
    adult_scores: list[float] = []
    gore_scores: list[float] = []
    violence_scores: list[float] = []
    danger_scores: list[float] = []
    adult_heap: list[tuple[float, int, Image.Image, str]] = []
    gore_heap: list[tuple[float, int, Image.Image, str]] = []
    violence_heap: list[tuple[float, int, Image.Image, str]] = []
    batch: list[Image.Image] = []
    batch_indices: list[int] = []
    ffmpeg = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def process_batch() -> None:
        nonlocal peak_rss
        if not batch:
            return
        pixels = torch.stack([transform(image) for image in batch]).to(device)
        with torch.inference_mode():
            probabilities = torch.sigmoid(model(pixels)).cpu()
        adult_batch = _union(probabilities, adult_indices)
        gore_batch = _union(probabilities, gore_indices)
        context_batch = _union(probabilities, context_indices)
        violence_batch = _union(probabilities, violence_indices)
        danger_batch = _union(probabilities, danger_indices)
        adult_best = probabilities[:, adult_indices].argmax(dim=1).tolist()
        gore_best = probabilities[:, gore_indices].argmax(dim=1).tolist()
        violence_best = probabilities[:, violence_indices].argmax(dim=1).tolist()
        gore_cooccurrence = (
            probabilities[:, gore_indices] >= gore_cooccurrence_threshold
        ).sum(dim=1).tolist()
        adult_cooccurrence = (
            probabilities[:, adult_indices] >= adult_cooccurrence_threshold
        ).sum(dim=1).tolist()

        for position, (image, frame_index) in enumerate(zip(batch, batch_indices)):
            timestamp = frame_index / sample_fps
            adult_score = float(adult_batch[position])
            gore_score = float(gore_batch[position])
            context_score = float(context_batch[position])
            violence_score = float(violence_batch[position])
            danger_score = float(danger_batch[position])
            adult_label = labels[adult_indices[adult_best[position]]]
            gore_label = labels[gore_indices[gore_best[position]]]
            violence_label = labels[violence_indices[violence_best[position]]]
            adult_scores.append(adult_score)
            gore_scores.append(gore_score)
            violence_scores.append(violence_score)
            danger_scores.append(danger_score)
            _push_candidate(
                adult_heap, score=adult_score, frame_index=frame_index, image=image,
                predicted_label=adult_label, limit=top_k_candidates,
            )
            _push_candidate(
                gore_heap, score=gore_score, frame_index=frame_index, image=image,
                predicted_label=gore_label, limit=top_k_candidates,
            )
            _push_candidate(
                violence_heap, score=violence_score, frame_index=frame_index,
                image=image, predicted_label=violence_label, limit=top_k_candidates,
            )

            if (
                adult_score >= adult_threshold
                and adult_cooccurrence[position] >= adult_minimum_cooccurring_labels
            ):
                name = f"frame-{frame_index:08d}-{timestamp:.3f}s.jpg"
                image.save(
                    adult_dir / "thumbnails" / name,
                    format="JPEG", quality=82, optimize=True,
                )
                adult_hits.append(
                    {
                        "frame_index": frame_index,
                        "timestamp_seconds": round(timestamp, 3),
                        "score": adult_score,
                        "cooccurring_labels": int(adult_cooccurrence[position]),
                        "predicted_label": adult_label,
                        "reason": "explicit_anatomy_or_act",
                        "thumbnail": f"thumbnails/{name}",
                    }
                )

            high_score = gore_score >= gore_high_threshold
            context_confirmed = (
                gore_score >= gore_low_threshold
                and (
                    context_score >= gore_context_threshold
                    or gore_cooccurrence[position] >= 2
                )
            )
            if high_score or context_confirmed:
                name = f"frame-{frame_index:08d}-{timestamp:.3f}s.jpg"
                image.save(gore_dir / "thumbnails" / name, format="JPEG", quality=82, optimize=True)
                gore_hits.append(
                    {
                        "frame_index": frame_index,
                        "timestamp_seconds": round(timestamp, 3),
                        "score": gore_score,
                        "context_score": context_score,
                        "cooccurring_labels": int(gore_cooccurrence[position]),
                        "predicted_label": gore_label,
                        "reason": "high_score" if high_score else "context_confirmed",
                        "thumbnail": f"thumbnails/{name}",
                    }
                )
            if violence_score >= violence_threshold:
                name = f"frame-{frame_index:08d}-{timestamp:.3f}s.jpg"
                image.save(
                    violence_dir / "thumbnails" / name,
                    format="JPEG", quality=82, optimize=True,
                )
                violence_hits.append(
                    {
                        "frame_index": frame_index,
                        "timestamp_seconds": round(timestamp, 3),
                        "score": violence_score,
                        "danger_score": danger_score,
                        "predicted_label": violence_label,
                        "reason": (
                            "direct_violence_high"
                            if violence_score >= violence_high_threshold
                            else "direct_violence_context"
                        ),
                        "thumbnail": f"thumbnails/{name}",
                    }
                )
        peak_rss = max(peak_rss, process.memory_info().rss)
        batch.clear()
        batch_indices.clear()

    status = "COMPLETED"
    error_message = None
    try:
        assert ffmpeg.stdout is not None
        while True:
            data = _read_exact(ffmpeg.stdout, frame_bytes)
            if not data:
                break
            if len(data) != frame_bytes:
                raise RuntimeError(
                    f"Incomplete raw frame: {len(data)} of {frame_bytes} bytes"
                )
            batch.append(Image.frombytes("RGB", (width, height), data))
            batch_indices.append(frames_scanned)
            frames_scanned += 1
            if len(batch) >= batch_size:
                process_batch()
        process_batch()
        return_code = ffmpeg.wait()
        if return_code != 0:
            stderr = (
                ffmpeg.stderr.read().decode("utf-8", errors="replace")
                if ffmpeg.stderr else ""
            )
            raise RuntimeError(
                f"FFmpeg failed with exit code {return_code}: {stderr[-2000:]}"
            )
        final_stat = input_path.stat()
        if (
            final_stat.st_size != input_stat.st_size
            or final_stat.st_mtime_ns != input_stat.st_mtime_ns
        ):
            raise RuntimeError("Input video changed while it was being scanned")
    except KeyboardInterrupt:
        status = "INTERRUPTED"
        error_message = "Stopped by user"
        ffmpeg.terminate()
        ffmpeg.wait(timeout=10)
    except Exception as exc:
        status = "FAILED"
        error_message = str(exc)
        if ffmpeg.poll() is None:
            ffmpeg.terminate()
            ffmpeg.wait(timeout=10)

    elapsed = time.perf_counter() - started
    raw_counts = {
        "adult": len(adult_hits), "gore": len(gore_hits),
        "violence": len(violence_hits),
    }
    adult_hits = temporal_confirm_hits(
        adult_hits, temporal_window_frames, temporal_minimum_hits
    )
    gore_high_hits = temporal_confirm_hits(
        [hit for hit in gore_hits if hit["reason"] == "high_score"],
        temporal_window_frames,
        temporal_minimum_hits,
    )
    gore_context_hits = temporal_confirm_hits(
        [hit for hit in gore_hits if hit["reason"] == "context_confirmed"],
        temporal_window_frames,
        gore_context_temporal_minimum_hits,
    )
    gore_hits = sorted(
        [*gore_high_hits, *gore_context_hits],
        key=lambda hit: int(hit["frame_index"]),
    )
    violence_hits = temporal_confirm_hits(
        violence_hits, temporal_window_frames, temporal_minimum_hits
    )
    for category_dir, all_hits, confirmed_hits in (
        (adult_dir, "adult", adult_hits),
        (gore_dir, "gore", gore_hits),
        (violence_dir, "violence", violence_hits),
    ):
        confirmed_names = {hit["thumbnail"] for hit in confirmed_hits}
        for thumbnail in (category_dir / "thumbnails").glob("*.jpg"):
            relative = f"thumbnails/{thumbnail.name}"
            if relative not in confirmed_names:
                thumbnail.unlink(missing_ok=True)

    shared_metrics = {
        "elapsed_seconds": round(elapsed, 3),
        "video_seconds_per_processing_second": (
            round(video_duration / elapsed, 3) if elapsed else None
        ),
        "peak_process_ram_bytes": peak_rss,
        "peak_cuda_memory_bytes": (
            torch.cuda.max_memory_allocated(device) if device.type == "cuda" else 0
        ),
        "shared_inference": True,
    }
    input_hash = sha256_file(input_path)
    common = {
        "schema_version": 2,
        "status": status,
        "error": error_message,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": str(input_path),
        "input_size_bytes": input_stat.st_size,
        "input_sha256": input_hash,
        "duration_seconds": video_duration,
        "sample_fps": sample_fps,
        "frames_scanned": frames_scanned,
        "content_style": "animation",
        "device": str(device),
        "runtime": {
            "torch": torch.__version__,
            "cuda": torch.version.cuda,
            "gpu": torch.cuda.get_device_name(device) if device.type == "cuda" else None,
        },
        "model": manifest,
        "metrics": shared_metrics,
        "safety": {
            "automatic_edit": False,
            "note": "Every candidate requires review; a scan cannot prove that a video is safe.",
        },
    }
    reports = {}
    for category, category_dir, hits, scores, heap, threshold, target_labels in (
        (
            "adult", adult_dir, adult_hits, adult_scores, adult_heap,
            adult_threshold, ADULT_EXPLICIT_LABELS,
        ),
        (
            "gore", gore_dir, gore_hits, gore_scores, gore_heap,
            gore_high_threshold, GORE_LABELS,
        ),
        (
            "violence", violence_dir, violence_hits, violence_scores,
            violence_heap, violence_threshold, DIRECT_VIOLENCE_LABELS,
        ),
    ):
        category_merge_gap = (
            gore_merge_gap_seconds if category == "gore" else merge_gap_seconds
        )
        intervals = group_hits(
            hits, category_merge_gap, padding_seconds, video_duration
        )
        retained = compact_interval_thumbnails(category_dir, hits, intervals)
        top_candidates = _save_top_candidates(category_dir, heap, sample_fps)
        payload = {
            **common,
            "scan_type": category,
            "threshold": threshold,
            "target_labels": list(target_labels),
            "temporal_confirmation": {
                "enabled": True,
                "window_frames": temporal_window_frames,
                "minimum_positive_frames": temporal_minimum_hits,
                "raw_hit_count": raw_counts[category],
                "confirmed_hit_count": len(hits),
            },
            "score_summary": _score_summary(scores),
            "top_candidates": top_candidates,
            "intervals": intervals,
            "retained_interval_thumbnail_count": retained,
            "merge_gap_seconds": category_merge_gap,
        }
        if category == "adult":
            payload["adult_policy"] = {
                "threshold": adult_threshold,
                "cooccurrence_threshold": adult_cooccurrence_threshold,
                "minimum_cooccurring_labels": adult_minimum_cooccurring_labels,
                "score_aggregation": "probabilistic_union",
                "temporal_minimum_hits": temporal_minimum_hits,
            }
        elif category == "gore":
            payload["two_tier_policy"] = {
                "high_threshold": gore_high_threshold,
                "low_threshold": gore_low_threshold,
                "context_threshold": gore_context_threshold,
                "cooccurrence_threshold": gore_cooccurrence_threshold,
                "high_score_temporal_minimum_hits": temporal_minimum_hits,
                "context_temporal_minimum_hits": gore_context_temporal_minimum_hits,
            }
        else:
            payload["danger_separation"] = {
                "labels": list(DANGER_LABELS),
                "threshold": danger_threshold,
                "score_summary": _score_summary(danger_scores),
                "note": "Danger scores are recorded but do not trigger violence candidates.",
            }
            payload["priority_policy"] = {
                "high_threshold": violence_high_threshold,
                "context_threshold": violence_threshold,
            }
        write_report(category_dir, payload)
        reports[category] = {
            "report": str(category_dir / "scan.json"),
            "raw_hit_count": raw_counts[category],
            "confirmed_hit_count": len(hits),
            "interval_count": len(intervals),
            "retained_interval_thumbnail_count": retained,
        }

    summary = {
        **common,
        "scan_type": "animation_safety_combined",
        "reports": reports,
        "danger_score_summary": _score_summary(danger_scores),
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "scan.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if status == "FAILED":
        raise RuntimeError(error_message)
    return summary
