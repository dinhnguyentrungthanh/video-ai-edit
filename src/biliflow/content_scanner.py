from __future__ import annotations

import heapq
import csv
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil
import torch
from PIL import Image
from safetensors.torch import load_file

from biliflow.intervals import compact_interval_thumbnails, group_hits
from biliflow.probe import duration_seconds, probe_video
from biliflow.report import write_report
from biliflow.scanner import _read_exact, _score_summary, sha256_file, temporal_confirm_hits
from biliflow.storage import require_capacity


def _load_classifier(
    kind: str, model_path: Path, device: torch.device,
    target_labels: tuple[str, ...] | None = None,
):
    if kind in {"gore", "violence_animation"}:
        import timm

        config = json.loads((model_path / "config.json").read_text(encoding="utf-8"))
        manifest = json.loads((model_path / "manifest.json").read_text(encoding="utf-8"))
        backend = manifest.get("backend", "timm")
        if backend == "timm_multilabel":
            with (model_path / "selected_tags.csv").open(
                "r", encoding="utf-8-sig", newline=""
            ) as handle:
                labels = [row["name"] for row in csv.DictReader(handle)]
            wanted = {
                label.casefold()
                for label in (target_labels or tuple(manifest["target_labels"]))
            }
            target_indices = [
                index for index, label in enumerate(labels) if label.casefold() in wanted
            ]
        else:
            labels = list(config["pretrained_cfg"]["label_names"])
            target_indices = [labels.index("NSFL")]
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

        def score(images: list[Image.Image]) -> tuple[list[float], list[str]]:
            pixels = torch.stack([transform(image) for image in images]).to(device)
            with torch.inference_mode():
                logits = model(pixels)
                probabilities = (
                    torch.sigmoid(logits)
                    if backend == "timm_multilabel"
                    else torch.softmax(logits, dim=-1)
                ).cpu()
            target_scores = (
                1 - torch.prod(1 - probabilities[:, target_indices], dim=1)
                if backend == "timm_multilabel"
                else probabilities[:, target_indices].sum(dim=1)
            )
            target_vectors = probabilities[:, target_indices]
            best_targets = target_vectors.argmax(dim=1).tolist()
            return (
                target_scores.tolist(),
                [labels[target_indices[index]] for index in best_targets],
            )

        return score, labels, target_indices

    if kind == "violence":
        import timm
        from torchvision import transforms

        model = timm.create_model(
            "vit_base_patch16_224", pretrained=False, num_classes=2
        )
        model.load_state_dict(load_file(model_path / "model.safetensors"))
        model = model.to(device).eval()
        transform = transforms.Compose(
            [
                transforms.Resize((224, 224)),
                transforms.ToTensor(),
                transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
            ]
        )
        # Training notebook uses ImageFolder classes ['non_violence', 'violence'].
        labels = ["non_violence", "violence"]
        target_index = 1

        def score(images: list[Image.Image]) -> tuple[list[float], list[str]]:
            pixels = torch.stack([transform(image) for image in images]).to(device)
            with torch.inference_mode():
                probabilities = torch.softmax(model(pixels), dim=-1).cpu()
            return (
                probabilities[:, target_index].tolist(),
                [labels[index] for index in probabilities.argmax(dim=-1).tolist()],
            )

        return score, labels, [target_index]

    raise ValueError(f"Unsupported content classifier: {kind}")


def scan_content(
    *,
    kind: str,
    project_root: Path,
    input_path: Path,
    report_dir: Path,
    model_path: Path,
    ffmpeg_path: Path,
    ffprobe_path: Path,
    sample_fps: float = 1.0,
    batch_size: int = 16,
    top_k_candidates: int = 20,
    threshold: float = 0.5,
    merge_gap_seconds: float = 2.0,
    padding_seconds: float = 1.0,
    device_name: str = "cuda",
    content_style: str = "live_action",
    temporal_window_frames: int = 5,
    temporal_minimum_hits: int = 3,
    target_labels: tuple[str, ...] | None = None,
) -> dict:
    input_path = input_path.resolve(strict=True)
    project_root = project_root.resolve(strict=True)
    report_dir = report_dir.resolve()
    model_path = model_path.resolve(strict=True)
    ffmpeg_path = ffmpeg_path.resolve(strict=True)
    ffprobe_path = ffprobe_path.resolve(strict=True)
    reports_root = (project_root / "reports").resolve(strict=True)
    if report_dir != reports_root and reports_root not in report_dir.parents:
        raise ValueError(f"Report directory must stay inside {reports_root}")
    if sample_fps <= 0 or batch_size <= 0 or top_k_candidates <= 0:
        raise ValueError("sample_fps, batch_size and top_k_candidates must be positive")
    if not 0 <= threshold <= 1:
        raise ValueError("threshold must be between zero and one")

    input_stat = input_path.stat()
    require_capacity(project_root, estimated_job_gb=max(4.0, input_stat.st_size / 1024**3 * 3))
    video_duration = duration_seconds(probe_video(ffprobe_path, input_path))
    device = torch.device(device_name)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but PyTorch cannot access the GPU")
    scorer, labels, target_indices = _load_classifier(
        kind, model_path, device, target_labels=target_labels
    )

    width = height = 256
    frame_bytes = width * height * 3
    command = [
        str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-i", str(input_path),
        "-vf", (
            f"fps={sample_fps},scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
        ),
        "-an", "-sn", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]

    report_dir.mkdir(parents=True, exist_ok=True)
    thumbs = report_dir / "thumbnails"
    candidates_dir = report_dir / "candidates"
    thumbs.mkdir(parents=True, exist_ok=True)
    candidates_dir.mkdir(parents=True, exist_ok=True)
    process = psutil.Process(os.getpid())
    peak_rss = process.memory_info().rss
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)

    started = time.perf_counter()
    frames_scanned = 0
    hits: list[dict] = []
    scores: list[float] = []
    candidate_heap: list[tuple[float, int, Image.Image, str]] = []
    batch: list[Image.Image] = []
    batch_indices: list[int] = []
    ffmpeg = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def process_batch() -> None:
        nonlocal peak_rss
        if not batch:
            return
        probabilities, predicted_labels = scorer(batch)
        for image, frame_index, score, predicted_label in zip(
            batch, batch_indices, probabilities, predicted_labels
        ):
            score = float(score)
            scores.append(score)
            candidate = (score, frame_index, image.copy(), predicted_label)
            if len(candidate_heap) < top_k_candidates:
                heapq.heappush(candidate_heap, candidate)
            elif score > candidate_heap[0][0]:
                removed = heapq.heapreplace(candidate_heap, candidate)
                removed[2].close()
            else:
                candidate[2].close()
            if score >= threshold:
                timestamp = frame_index / sample_fps
                name = f"frame-{frame_index:08d}-{timestamp:.3f}s.jpg"
                image.save(thumbs / name, format="JPEG", quality=82, optimize=True)
                hits.append(
                    {
                        "frame_index": frame_index,
                        "timestamp_seconds": round(timestamp, 3),
                        "score": score,
                        "predicted_label": predicted_label,
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
                raise RuntimeError(f"Incomplete raw frame: {len(data)} of {frame_bytes} bytes")
            batch.append(Image.frombytes("RGB", (width, height), data))
            batch_indices.append(frames_scanned)
            frames_scanned += 1
            if len(batch) >= batch_size:
                process_batch()
        process_batch()
        return_code = ffmpeg.wait()
        if return_code != 0:
            stderr = ffmpeg.stderr.read().decode("utf-8", errors="replace") if ffmpeg.stderr else ""
            raise RuntimeError(f"FFmpeg failed with exit code {return_code}: {stderr[-2000:]}")
        final_stat = input_path.stat()
        if final_stat.st_size != input_stat.st_size or final_stat.st_mtime_ns != input_stat.st_mtime_ns:
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
    raw_hit_count = len(hits)
    if content_style == "animation":
        confirmed_hits = temporal_confirm_hits(
            hits, temporal_window_frames, temporal_minimum_hits
        )
        confirmed_names = {hit["thumbnail"] for hit in confirmed_hits}
        for hit in hits:
            if hit["thumbnail"] not in confirmed_names:
                (report_dir / hit["thumbnail"]).unlink(missing_ok=True)
        hits = confirmed_hits
    intervals = group_hits(hits, merge_gap_seconds, padding_seconds, video_duration)
    retained_thumbnail_count = compact_interval_thumbnails(report_dir, hits, intervals)
    top_candidates = []
    for score, frame_index, image, predicted_label in sorted(candidate_heap, reverse=True):
        timestamp = frame_index / sample_fps
        name = f"candidate-{frame_index:08d}-{timestamp:.3f}s.jpg"
        image.save(candidates_dir / name, format="JPEG", quality=82, optimize=True)
        image.close()
        top_candidates.append(
            {
                "rank": len(top_candidates) + 1,
                "frame_index": frame_index,
                "timestamp_seconds": round(timestamp, 3),
                "score": round(score, 6),
                "predicted_label": predicted_label,
                "thumbnail": f"candidates/{name}",
            }
        )
    manifest_path = model_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload = {
        "schema_version": 1,
        "scan_type": "violence" if kind == "violence_animation" else kind,
        "status": status,
        "error": error_message,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": str(input_path),
        "input_size_bytes": input_stat.st_size,
        "input_sha256": sha256_file(input_path),
        "duration_seconds": video_duration,
        "sample_fps": sample_fps,
        "frames_scanned": frames_scanned,
        "threshold": threshold,
        "content_style": content_style,
        "target_labels": [labels[index] for index in target_indices],
        "model_label_count": len(labels),
        "temporal_confirmation": {
            "enabled": content_style == "animation",
            "window_frames": temporal_window_frames,
            "minimum_positive_frames": temporal_minimum_hits,
            "raw_hit_count": raw_hit_count,
            "confirmed_hit_count": len(hits),
        },
        "score_summary": _score_summary(scores),
        "top_candidates": top_candidates,
        "device": str(device),
        "runtime": {
            "torch": torch.__version__,
            "cuda": torch.version.cuda,
            "gpu": torch.cuda.get_device_name(device) if device.type == "cuda" else None,
        },
        "model": manifest,
        "intervals": intervals,
        "retained_interval_thumbnail_count": retained_thumbnail_count,
        "metrics": {
            "elapsed_seconds": round(elapsed, 3),
            "video_seconds_per_processing_second": round(video_duration / elapsed, 3) if elapsed else None,
            "peak_process_ram_bytes": peak_rss,
            "peak_cuda_memory_bytes": torch.cuda.max_memory_allocated(device) if device.type == "cuda" else 0,
        },
        "safety": {
            "automatic_edit": False,
            "note": "Every candidate requires review; a scan cannot prove that a video is safe.",
        },
    }
    write_report(report_dir, payload)
    if status == "FAILED":
        raise RuntimeError(error_message)
    return payload
