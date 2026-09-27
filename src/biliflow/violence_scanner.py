from __future__ import annotations

import heapq
import json
import os
import subprocess
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psutil
import torch
from PIL import Image
from safetensors.torch import load_file
from transformers import AutoConfig, AutoImageProcessor, AutoModelForVideoClassification, VideoMAEImageProcessor

from biliflow.intervals import compact_interval_thumbnails, group_hits, merge_intervals
from biliflow.probe import duration_seconds, probe_video
from biliflow.report import write_report
from biliflow.scanner import _read_exact, _score_summary, sha256_file
from biliflow.storage import require_capacity


def aggregate_top_k_mean(scores: list[float], top_k: int) -> float:
    if not scores or top_k <= 0:
        raise ValueError("scores must be non-empty and top_k must be positive")
    selected = sorted(scores, reverse=True)[: min(top_k, len(scores))]
    return sum(selected) / len(selected)


def scan_violence(
    *,
    project_root: Path,
    input_path: Path,
    report_dir: Path,
    model_path: Path,
    ffmpeg_path: Path,
    ffprobe_path: Path,
    sample_fps: float = 8.0,
    clip_frames: int = 16,
    stride_frames: int = 8,
    top_k_candidates: int = 20,
    threshold: float = 0.5,
    high_threshold: float | None = None,
    merge_gap_seconds: float = 2.0,
    review_merge_gap_seconds: float = 0.0,
    padding_seconds: float = 1.0,
    device_name: str = "cuda",
    content_style: str = "live_action",
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
    if sample_fps <= 0 or clip_frames <= 0 or stride_frames <= 0 or top_k_candidates <= 0:
        raise ValueError("Sampling and candidate settings must be positive")
    if not 0 <= threshold <= 1 or (
        high_threshold is not None and not threshold <= high_threshold <= 1
    ):
        raise ValueError("violence thresholds must be ordered between zero and one")
    if merge_gap_seconds < 0 or review_merge_gap_seconds < 0:
        raise ValueError("merge gaps cannot be negative")

    input_stat = input_path.stat()
    require_capacity(project_root, estimated_job_gb=max(4.0, input_stat.st_size / 1024**3 * 3))
    video_duration = duration_seconds(probe_video(ffprobe_path, input_path))
    device = torch.device(device_name)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but PyTorch cannot access the GPU")

    manifest = json.loads((model_path / "manifest.json").read_text(encoding="utf-8"))
    backend = manifest.get("backend")
    frame_window_backend = backend == "timm_frame_video"
    if frame_window_backend:
        import timm
        from torchvision.transforms import Compose, Normalize, Resize, ToTensor

        model = timm.create_model(
            manifest["architecture"], pretrained=False,
            num_classes=len(manifest["label_names"]),
        )
        incompatible = model.load_state_dict(
            load_file(model_path / "model.safetensors"), strict=False
        )
        if incompatible.missing_keys or incompatible.unexpected_keys:
            raise RuntimeError(f"Checkpoint mismatch: {incompatible}")
        frame_transform = Compose([
            Resize((224, 224)), ToTensor(),
            Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
        ])
        positive_indices = [int(manifest.get("positive_index", 1))]
        aggregation_top_k = int(manifest.get("aggregation_top_k", 5))
        processor = None
    else:
        processor = (
            AutoImageProcessor.from_pretrained(model_path, local_files_only=True)
            if (model_path / "preprocessor_config.json").exists()
            else VideoMAEImageProcessor(size={"shortest_edge": 224}, crop_size={"height": 224, "width": 224})
        )
    if backend == "transformers_video_multiclass":
        config = AutoConfig.from_pretrained(model_path, local_files_only=True)
        model = AutoModelForVideoClassification.from_config(config)
        state = load_file(model_path / "model.safetensors")
        for layer in range(config.num_hidden_layers):
            base = f"videomae.encoder.layer.{layer}.attention.attention."
            query_bias = state.pop(base + "q_bias")
            value_bias = state.pop(base + "v_bias")
            state[base + "query.bias"] = query_bias
            state[base + "key.bias"] = torch.zeros_like(query_bias)
            state[base + "value.bias"] = value_bias
        incompatible = model.load_state_dict(state, strict=False)
        if incompatible.missing_keys or incompatible.unexpected_keys:
            raise RuntimeError(f"Checkpoint mismatch: {incompatible}")
    elif not frame_window_backend:
        model = AutoModelForVideoClassification.from_pretrained(
            model_path, local_files_only=True, use_safetensors=True
        )
    model = model.to(device).eval()
    if not frame_window_backend:
        wanted = {label.casefold() for label in manifest.get("target_labels", [])}
        positive_indices = [
            int(index) for index, label in model.config.id2label.items()
            if str(label).casefold() in wanted
        ] or [1]

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
    windows_scored = 0
    scores: list[float] = []
    hits: list[dict] = []
    candidate_heap: list[tuple[float, int, Image.Image]] = []
    window: deque[Image.Image] = deque(maxlen=clip_frames)
    frame_score_window: deque[float] = deque(maxlen=clip_frames)
    ffmpeg = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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
            window.append(Image.frombytes("RGB", (width, height), data))
            frames_scanned += 1
            if len(window) < clip_frames or (frames_scanned - clip_frames) % stride_frames != 0:
                continue
            if frame_window_backend:
                new_frame_count = (
                    clip_frames if windows_scored == 0 else min(stride_frames, clip_frames)
                )
                new_frames = list(window)[-new_frame_count:]
                pixels = torch.stack([frame_transform(frame) for frame in new_frames]).to(device)
                with torch.inference_mode():
                    frame_probabilities = torch.softmax(model(pixels), dim=-1)[:, positive_indices[0]]
                frame_score_window.extend(frame_probabilities.cpu().tolist())
                if len(frame_score_window) != clip_frames:
                    raise RuntimeError("Frame score cache is not aligned with the video window")
                score = aggregate_top_k_mean(
                    list(frame_score_window), aggregation_top_k
                )
            else:
                frames = [np.asarray(frame).copy() for frame in window]
                assert processor is not None
                inputs = {
                    key: value.to(device)
                    for key, value in processor(frames, return_tensors="pt").items()
                }
                with torch.inference_mode():
                    probabilities = torch.softmax(model(**inputs).logits, dim=-1)[0].cpu()
                score = float(probabilities[positive_indices].sum())
            scores.append(score)
            windows_scored += 1
            center_frame_index = frames_scanned - 1 - clip_frames // 2
            timestamp = center_frame_index / sample_fps
            preview = window[clip_frames // 2].copy()
            candidate = (score, windows_scored, preview)
            if len(candidate_heap) < top_k_candidates:
                heapq.heappush(candidate_heap, candidate)
            elif score > candidate_heap[0][0]:
                removed = heapq.heapreplace(candidate_heap, candidate)
                removed[2].close()
            else:
                preview.close()
            if score >= threshold:
                name = f"clip-{windows_scored:06d}-{timestamp:.3f}s.jpg"
                window[clip_frames // 2].save(thumbs / name, "JPEG", quality=82, optimize=True)
                hits.append({
                    "frame_index": windows_scored,
                    "timestamp_seconds": round(timestamp, 3),
                    "score": score,
                    "predicted_label": "Violent",
                    "thumbnail": f"thumbnails/{name}",
                })
            peak_rss = max(peak_rss, process.memory_info().rss)
        return_code = ffmpeg.wait()
        if return_code != 0:
            stderr = ffmpeg.stderr.read().decode("utf-8", errors="replace") if ffmpeg.stderr else ""
            raise RuntimeError(f"FFmpeg failed with exit code {return_code}: {stderr[-2000:]}")
        final_stat = input_path.stat()
        if final_stat.st_size != input_stat.st_size or final_stat.st_mtime_ns != input_stat.st_mtime_ns:
            raise RuntimeError("Input video changed while it was being scanned")
    except Exception as exc:
        status = "FAILED"
        error_message = str(exc)
        if ffmpeg.poll() is None:
            ffmpeg.terminate()
            ffmpeg.wait(timeout=10)

    elapsed = time.perf_counter() - started
    intervals = group_hits(hits, merge_gap_seconds, padding_seconds, video_duration)
    intervals = merge_intervals(intervals, review_merge_gap_seconds)
    for interval in intervals:
        interval["priority"] = (
            "high"
            if high_threshold is not None and interval["max_score"] >= high_threshold
            else "context"
        )
    retained_thumbnail_count = compact_interval_thumbnails(report_dir, hits, intervals)
    top_candidates = []
    for score, window_index, image in sorted(candidate_heap, reverse=True):
        center_frame_index = (
            (window_index - 1) * stride_frames + clip_frames - 1 - clip_frames // 2
        )
        timestamp = center_frame_index / sample_fps
        name = f"candidate-{window_index:06d}-{timestamp:.3f}s.jpg"
        image.save(candidates_dir / name, "JPEG", quality=82, optimize=True)
        image.close()
        top_candidates.append({
            "rank": len(top_candidates) + 1,
            "frame_index": window_index,
            "timestamp_seconds": round(timestamp, 3),
            "score": round(score, 6),
            "predicted_label": "Violent",
            "thumbnail": f"candidates/{name}",
        })

    payload = {
        "schema_version": 1,
        "scan_type": "violence",
        "status": status,
        "error": error_message,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": str(input_path),
        "input_size_bytes": input_stat.st_size,
        "input_sha256": sha256_file(input_path),
        "duration_seconds": video_duration,
        "content_style": content_style,
        "sample_fps": sample_fps,
        "clip_frames": clip_frames,
        "clip_seconds": round(clip_frames / sample_fps, 3),
        "stride_frames": stride_frames,
        "frames_scanned": frames_scanned,
        "windows_scored": windows_scored,
        "threshold": threshold,
        "high_threshold": high_threshold,
        "review_merge_gap_seconds": review_merge_gap_seconds,
        "target_label": "Violent",
        "score_summary": _score_summary(scores),
        "top_candidates": top_candidates,
        "device": str(device),
        "runtime": {
            "torch": torch.__version__, "cuda": torch.version.cuda,
            "gpu": torch.cuda.get_device_name(device) if device.type == "cuda" else None,
        },
        "model": manifest,
        "intervals": intervals,
        "priority_counts": {
            "high": sum(interval["priority"] == "high" for interval in intervals),
            "context": sum(interval["priority"] == "context" for interval in intervals),
        },
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
