from __future__ import annotations

import hashlib
import heapq
import json
import os
import subprocess
import time
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from pathlib import Path

import psutil
import torch
from PIL import Image
from transformers import AutoImageProcessor, AutoModelForImageClassification

from biliflow.intervals import compact_interval_thumbnails, group_hits, merge_intervals
from biliflow.probe import duration_seconds, probe_video
from biliflow.report import write_report
from biliflow.storage import require_capacity


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _nsfw_indices(model, target_labels: list[str] | None = None) -> list[int]:
    labels = {int(index): str(label).lower() for index, label in model.config.id2label.items()}
    if target_labels:
        wanted = {label.casefold() for label in target_labels}
        matches = [index for index, label in labels.items() if label.casefold() in wanted]
        if matches:
            return matches
    for index, label in labels.items():
        if "nsfw" in label or "porn" in label or "unsafe" in label:
            return [index]
    raise RuntimeError(f"Cannot identify NSFW label from model labels: {labels}")


def _read_exact(stream, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            break
        chunks.extend(chunk)
    return bytes(chunks)


def _score_summary(scores: list[float]) -> dict:
    if not scores:
        return {}
    ordered = sorted(scores)

    def percentile(fraction: float) -> float:
        position = (len(ordered) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return ordered[lower] * (1 - weight) + ordered[upper] * weight

    return {
        "min": round(ordered[0], 6),
        "mean": round(sum(ordered) / len(ordered), 6),
        "p50": round(percentile(0.50), 6),
        "p90": round(percentile(0.90), 6),
        "p95": round(percentile(0.95), 6),
        "p99": round(percentile(0.99), 6),
        "max": round(ordered[-1], 6),
    }


def temporal_confirm_hits(hits: list[dict], window_frames: int, minimum_hits: int) -> list[dict]:
    if window_frames <= 0 or minimum_hits <= 0 or minimum_hits > window_frames:
        raise ValueError("Invalid temporal confirmation settings")
    by_frame = {int(hit["frame_index"]): hit for hit in hits}
    confirmed: set[int] = set()
    for start in sorted(by_frame):
        members = [index for index in by_frame if start <= index < start + window_frames]
        if len(members) >= minimum_hits:
            confirmed.update(members)
    return [hit for hit in hits if int(hit["frame_index"]) in confirmed]


def complete_nsfw_sequence_context(
    intervals: list[dict],
    score_samples: list[dict],
    *,
    context_threshold: float,
    context_seconds: float,
    padding_seconds: float,
    duration_seconds: float,
) -> list[dict]:
    """Extend strong NSFW seeds with nearby moderate evidence.

    A high threshold remains responsible for creating every review interval.  A
    lower score can only extend an existing seed by a bounded amount, so an
    isolated moderate frame never creates a new 18+ finding on its own.  This
    fills the lead-in, cutaway and tail frames of one continuous adult scene
    without lowering the detector threshold across the whole video.
    """
    if not 0 <= context_threshold <= 1:
        raise ValueError("context_threshold must be between zero and one")
    if context_seconds < 0 or padding_seconds < 0:
        raise ValueError("context_seconds and padding_seconds cannot be negative")
    if not intervals or context_seconds == 0:
        return [dict(interval) for interval in intervals]

    samples = sorted(score_samples, key=lambda item: float(item["timestamp_seconds"]))
    timestamps = [float(sample["timestamp_seconds"]) for sample in samples]
    completed: list[dict] = []
    for interval in intervals:
        original_start = float(interval["start_seconds"])
        original_end = float(interval["end_seconds"])
        before = [
            sample for sample in samples[
                bisect_left(timestamps, original_start - context_seconds):
                bisect_left(timestamps, original_start)
            ]
            if float(sample["timestamp_seconds"]) < original_start
            and float(sample["score"]) >= context_threshold
        ]
        after = [
            sample for sample in samples[
                bisect_right(timestamps, original_end):
                bisect_right(timestamps, original_end + context_seconds)
            ]
            if original_end < float(sample["timestamp_seconds"])
            and float(sample["score"]) >= context_threshold
        ]
        start = original_start
        end = original_end
        if before:
            start = max(
                0.0,
                min(float(sample["timestamp_seconds"]) for sample in before) - padding_seconds,
            )
        if after:
            end = min(
                duration_seconds,
                max(float(sample["timestamp_seconds"]) for sample in after) + padding_seconds,
            )
        value = dict(interval)
        value["start_seconds"] = round(start, 3)
        value["end_seconds"] = round(end, 3)
        value["sequence_context"] = {
            "applied": start < original_start or end > original_end,
            "detector_start_seconds": round(original_start, 3),
            "detector_end_seconds": round(original_end, 3),
            "supporting_sample_count": len(before) + len(after),
            "context_threshold": context_threshold,
            "maximum_extension_seconds": context_seconds,
        }
        completed.append(value)
    return completed


def scan_nsfw(
    *,
    project_root: Path,
    input_path: Path,
    report_dir: Path,
    model_path: Path,
    ffmpeg_path: Path,
    ffprobe_path: Path,
    sample_fps: float,
    batch_size: int,
    top_k_candidates: int,
    threshold: float,
    merge_gap_seconds: float,
    padding_seconds: float,
    device_name: str,
    content_style: str = "unknown",
    temporal_window_frames: int = 5,
    temporal_minimum_hits: int = 3,
    review_merge_gap_seconds: float = 3.0,
    sequence_context_threshold: float = 0.70,
    sequence_context_seconds: float = 8.0,
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
    if not 0 <= sequence_context_threshold <= threshold <= 1:
        raise ValueError("NSFW thresholds must be ordered between zero and one")
    if merge_gap_seconds < 0 or review_merge_gap_seconds < 0:
        raise ValueError("NSFW merge gaps cannot be negative")
    if padding_seconds < 0 or sequence_context_seconds < 0:
        raise ValueError("NSFW padding and sequence context cannot be negative")
    input_stat = input_path.stat()
    require_capacity(project_root, estimated_job_gb=max(8.0, input_stat.st_size / 1024**3 * 5))

    probe = probe_video(ffprobe_path, input_path)
    video_duration = duration_seconds(probe)
    device = torch.device(device_name)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but PyTorch cannot access the GPU")

    processor = AutoImageProcessor.from_pretrained(model_path, local_files_only=True)
    model = AutoModelForImageClassification.from_pretrained(
        model_path, local_files_only=True, use_safetensors=True
    ).to(device)
    model.eval()
    manifest_path = model_path / "manifest.json"
    model_manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    nsfw_indices = _nsfw_indices(model, model_manifest.get("target_labels"))

    width = height = 448
    frame_bytes = width * height * 3
    vf = (
        f"fps={sample_fps},"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    )
    command = [
        str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-i", str(input_path),
        "-vf", vf, "-an", "-sn", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]

    report_dir.mkdir(parents=True, exist_ok=True)
    thumbs = report_dir / "thumbnails"
    thumbs.mkdir(parents=True, exist_ok=True)
    candidates_dir = report_dir / "candidates"
    candidates_dir.mkdir(parents=True, exist_ok=True)

    process = psutil.Process(os.getpid())
    peak_rss = process.memory_info().rss
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)

    started = time.perf_counter()
    frames_scanned = 0
    hits: list[dict] = []
    scores: list[float] = []
    score_samples: list[dict] = []
    candidate_heap: list[tuple[float, int, Image.Image]] = []
    batch: list[Image.Image] = []
    batch_indices: list[int] = []
    ffmpeg = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def process_batch() -> None:
        nonlocal peak_rss
        if not batch:
            return
        inputs = processor(images=batch, return_tensors="pt")
        inputs = {key: value.to(device) for key, value in inputs.items()}
        with torch.inference_mode():
            all_probabilities = torch.softmax(model(**inputs).logits, dim=-1).cpu()
        target_probabilities = all_probabilities[:, nsfw_indices]
        probabilities = target_probabilities.sum(dim=-1).tolist()
        best_targets = target_probabilities.argmax(dim=-1).tolist()
        labels = {
            int(index): str(label) for index, label in model.config.id2label.items()
        }
        predicted_labels = [labels[nsfw_indices[index]] for index in best_targets]
        for image, frame_index, score, predicted_label in zip(
            batch, batch_indices, probabilities, predicted_labels
        ):
            score = float(score)
            scores.append(score)
            score_samples.append(
                {
                    "frame_index": frame_index,
                    "timestamp_seconds": round(frame_index / sample_fps, 3),
                    "score": score,
                }
            )
            candidate = (score, frame_index, image.copy())
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
        confirmed_hits = temporal_confirm_hits(hits, temporal_window_frames, temporal_minimum_hits)
        confirmed_names = {hit["thumbnail"] for hit in confirmed_hits}
        for hit in hits:
            if hit["thumbnail"] not in confirmed_names:
                thumbnail = report_dir / hit["thumbnail"]
                if thumbnail.exists():
                    thumbnail.unlink()
        hits = confirmed_hits
    intervals = group_hits(hits, merge_gap_seconds, padding_seconds, video_duration)
    detector_interval_count = len(intervals)
    if content_style == "live_action":
        intervals = merge_intervals(intervals, review_merge_gap_seconds)
        intervals = complete_nsfw_sequence_context(
            intervals,
            score_samples,
            context_threshold=sequence_context_threshold,
            context_seconds=sequence_context_seconds,
            padding_seconds=padding_seconds,
            duration_seconds=video_duration,
        )
    retained_thumbnail_count = compact_interval_thumbnails(report_dir, hits, intervals)
    top_candidates = []
    for score, frame_index, image in sorted(candidate_heap, reverse=True):
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
                "thumbnail": f"candidates/{name}",
            }
        )
    payload = {
        "schema_version": 1,
        "scan_type": "nsfw",
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
        "temporal_confirmation": {
            "enabled": content_style == "animation",
            "window_frames": temporal_window_frames,
            "minimum_positive_frames": temporal_minimum_hits,
            "raw_hit_count": raw_hit_count,
            "confirmed_hit_count": len(hits),
        },
        "sequence_completion": {
            "enabled": content_style == "live_action",
            "seed_threshold": threshold,
            "context_threshold": sequence_context_threshold,
            "maximum_context_seconds": sequence_context_seconds,
            "review_merge_gap_seconds": review_merge_gap_seconds,
            "detector_interval_count": detector_interval_count,
            "completed_interval_count": len(intervals),
            "extended_interval_count": sum(
                bool(interval.get("sequence_context", {}).get("applied"))
                for interval in intervals
            ),
            "moderate_evidence_creates_new_interval": False,
        },
        "score_summary": _score_summary(scores),
        "top_candidates": top_candidates,
        "device": str(device),
        "runtime": {
            "torch": torch.__version__,
            "cuda": torch.version.cuda,
            "gpu": torch.cuda.get_device_name(device) if device.type == "cuda" else None,
        },
        "model": model_manifest,
        "intervals": intervals,
        "retained_interval_thumbnail_count": retained_thumbnail_count,
        "metrics": {
            "elapsed_seconds": round(elapsed, 3),
            "video_seconds_per_processing_second": round(video_duration / elapsed, 3) if elapsed else None,
            "peak_process_ram_bytes": peak_rss,
            "peak_cuda_memory_bytes": torch.cuda.max_memory_allocated(device) if device.type == "cuda" else 0,
        },
    }
    write_report(report_dir, payload)
    if status == "FAILED":
        raise RuntimeError(error_message)
    return payload
