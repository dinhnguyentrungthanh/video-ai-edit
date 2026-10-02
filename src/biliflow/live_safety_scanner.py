from __future__ import annotations

from biliflow.performance import ScanPerformance

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

from biliflow.content_scanner import _load_classifier
from biliflow.frame_prefetch import IteratorPrefetch
from biliflow.intervals import compact_interval_thumbnails, group_hits, merge_intervals
from biliflow.probe import duration_seconds, probe_video
from biliflow.report import write_report
from biliflow.scanner import _read_exact, _score_summary
from biliflow.source_hash import BackgroundSha256
from biliflow.storage import require_capacity
from biliflow.violence_scanner import aggregate_top_k_mean

VIOLENCE_PRECISIONS = ("fp32", "fp16")
EVENT_PREFETCH_DEPTH = 16


def scan_live_safety(
    *,
    project_root: Path,
    input_path: Path,
    report_dir: Path,
    gore_model_path: Path,
    violence_model_path: Path,
    ffmpeg_path: Path,
    ffprobe_path: Path,
    gore_sample_fps: float = 2.0,
    violence_sample_fps: float = 8.0,
    gore_batch_size: int = 16,
    clip_frames: int = 16,
    stride_frames: int = 8,
    top_k_candidates: int = 20,
    gore_threshold: float = 0.5,
    violence_threshold: float = 0.28,
    gore_merge_gap_seconds: float = 2.0,
    violence_merge_gap_seconds: float = 2.0,
    padding_seconds: float = 1.0,
    device_name: str = "cuda",
    violence_precision: str = "fp32",
) -> dict:
    """Scan live-action gore and violence from one exact FFmpeg decode.

    FFmpeg creates two independent filter branches before packing them side by
    side. The gore branch runs its original fps filter and is repeated only for
    transport; every Nth left frame is byte-identical to the old 2 fps stream.
    The violence branch remains byte-identical to the old 8 fps stream.
    Reading, unpacking and the violence transform run on a producer thread, one
    window ahead, in exactly the serial order. The source SHA-256 is computed on
    a background thread during the scan and awaited before the reports are
    written. ``violence_precision="fp16"`` (opt-in, not bit-identical) autocasts
    only the violence ViT forward.
    """
    performance = ScanPerformance()
    project_root = project_root.resolve(strict=True)
    input_path = input_path.resolve(strict=True)
    report_dir = report_dir.resolve()
    gore_model_path = gore_model_path.resolve(strict=True)
    violence_model_path = violence_model_path.resolve(strict=True)
    ffmpeg_path = ffmpeg_path.resolve(strict=True)
    ffprobe_path = ffprobe_path.resolve(strict=True)
    reports_root = (project_root / "reports").resolve(strict=True)
    if report_dir != reports_root and reports_root not in report_dir.parents:
        raise ValueError(f"Report directory must stay inside {reports_root}")
    if gore_sample_fps <= 0 or violence_sample_fps <= 0:
        raise ValueError("Sampling rates must be positive")
    ratio = violence_sample_fps / gore_sample_fps
    rounded_ratio = round(ratio)
    if rounded_ratio <= 0 or abs(ratio - rounded_ratio) > 1e-9:
        raise ValueError("Violence fps must be an integer multiple of gore fps")
    if gore_batch_size <= 0 or clip_frames <= 0 or stride_frames <= 0:
        raise ValueError("Batch and temporal window settings must be positive")
    if not 0 <= gore_threshold <= 1 or not 0 <= violence_threshold <= 1:
        raise ValueError("Thresholds must be between zero and one")
    if violence_precision not in VIOLENCE_PRECISIONS:
        raise ValueError("violence precision must be 'fp32' or 'fp16'")
    if violence_precision == "fp16" and device_name != "cuda":
        raise ValueError("fp16 violence inference requires CUDA")

    input_stat = input_path.stat()
    require_capacity(
        project_root,
        estimated_job_gb=max(4.0, input_stat.st_size / 1024**3 * 3),
    )
    video_duration = duration_seconds(probe_video(ffprobe_path, input_path))
    device = torch.device(device_name)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but PyTorch cannot access the GPU")

    with performance.measure('model_load'):
        gore_scorer, gore_labels, gore_target_indices = _load_classifier(
            "gore", gore_model_path, device
        )
        gore_manifest = json.loads(
            (gore_model_path / "manifest.json").read_text(encoding="utf-8")
        )
        violence_manifest = json.loads(
            (violence_model_path / "manifest.json").read_text(encoding="utf-8")
        )
        if violence_manifest.get("backend") != "timm_frame_video":
            raise ValueError("Shared live safety currently requires timm_frame_video violence")

        import timm
        from torchvision.transforms import Compose, Normalize, Resize, ToTensor

        violence_model = timm.create_model(
            violence_manifest["architecture"], pretrained=False,
            num_classes=len(violence_manifest["label_names"]),
        )
        incompatible = violence_model.load_state_dict(
            load_file(violence_model_path / "model.safetensors"), strict=False
        )
        if incompatible.missing_keys or incompatible.unexpected_keys:
            raise RuntimeError(f"Checkpoint mismatch: {incompatible}")
        violence_model = violence_model.to(device).eval()
        violence_transform = Compose([
            Resize((224, 224)), ToTensor(),
            Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
        ])
        violence_positive_index = int(violence_manifest.get("positive_index", 1))
        aggregation_top_k = int(violence_manifest.get("aggregation_top_k", 5))

    gore_dir = report_dir / "gore"
    violence_dir = report_dir / "violence"
    for target in (gore_dir, violence_dir):
        (target / "thumbnails").mkdir(parents=True, exist_ok=True)
        (target / "candidates").mkdir(parents=True, exist_ok=True)

    width = height = 256
    packed_width = width * 2
    packed_frame_bytes = packed_width * height * 3
    command = [
        str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-i", str(input_path),
        "-filter_complex",
        (
            "[0:v]split=2[gore][violence];"
            f"[gore]fps={gore_sample_fps},"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
            f"fps={violence_sample_fps}[gore_transport];"
            f"[violence]fps={violence_sample_fps},"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2[violence_native];"
            "[gore_transport][violence_native]hstack=inputs=2[out]"
        ),
        "-map", "[out]", "-an", "-sn", "-f", "rawvideo",
        "-pix_fmt", "rgb24", "pipe:1",
    ]

    process = psutil.Process(os.getpid())
    peak_rss = process.memory_info().rss
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    started = time.perf_counter()

    gore_frames_scanned = 0
    gore_hits: list[dict] = []
    gore_scores: list[float] = []
    gore_heap: list[tuple[float, int, Image.Image, str]] = []
    gore_batch: list[Image.Image] = []
    gore_batch_indices: list[int] = []

    violence_frames_scanned = 0
    violence_windows_scored = 0
    violence_scores: list[float] = []
    violence_hits: list[dict] = []
    violence_heap: list[tuple[float, int, Image.Image]] = []
    violence_frame_scores: deque[float] = deque(maxlen=clip_frames)
    produced = {"violence_frames": 0}

    def process_gore_batch() -> None:
        nonlocal peak_rss
        if not gore_batch:
            return
        probabilities, predicted_labels = performance.call('model_step', gore_scorer, gore_batch)
        for image, frame_index, raw_score, predicted_label in zip(
            gore_batch, gore_batch_indices, probabilities, predicted_labels
        ):
            score = float(raw_score)
            gore_scores.append(score)
            candidate = (score, frame_index, image.copy(), predicted_label)
            if len(gore_heap) < top_k_candidates:
                heapq.heappush(gore_heap, candidate)
            elif score > gore_heap[0][0]:
                removed = heapq.heapreplace(gore_heap, candidate)
                removed[2].close()
            else:
                candidate[2].close()
            if score >= gore_threshold:
                timestamp = frame_index / gore_sample_fps
                name = f"frame-{frame_index:08d}-{timestamp:.3f}s.jpg"
                performance.call('preview_write', image.save,
                    gore_dir / "thumbnails" / name,
                    format="JPEG", quality=82, optimize=True,
                )
                gore_hits.append({
                    "frame_index": frame_index,
                    "timestamp_seconds": round(timestamp, 3),
                    "score": score,
                    "predicted_label": predicted_label,
                    "thumbnail": f"thumbnails/{name}",
                })
            image.close()
        peak_rss = max(peak_rss, process.memory_info().rss)
        gore_batch.clear()
        gore_batch_indices.clear()

    def process_violence_window(pixels: torch.Tensor, frames_read: int, center: Image.Image) -> None:
        nonlocal violence_windows_scored, violence_frames_scanned, peak_rss
        violence_frames_scanned = frames_read
        with performance.measure('violence_model_step'):
            pixels = pixels.to(device)
            with torch.inference_mode():
                if violence_precision == "fp16":
                    with torch.autocast(device_type="cuda", dtype=torch.float16):
                        logits = violence_model(pixels)
                    probabilities = torch.softmax(logits.float(), dim=-1)[:, violence_positive_index]
                else:
                    probabilities = torch.softmax(
                        violence_model(pixels), dim=-1
                    )[:, violence_positive_index]
            violence_frame_scores.extend(probabilities.cpu().tolist())

        if len(violence_frame_scores) != clip_frames:
            raise RuntimeError("Frame score cache is not aligned with the video window")
        score = aggregate_top_k_mean(
            list(violence_frame_scores), aggregation_top_k
        )
        violence_scores.append(score)
        violence_windows_scored += 1
        center_frame_index = (
            violence_frames_scanned - 1 - clip_frames // 2
        )
        timestamp = center_frame_index / violence_sample_fps
        preview = center.copy()
        candidate = (score, violence_windows_scored, preview)
        if len(violence_heap) < top_k_candidates:
            heapq.heappush(violence_heap, candidate)
        elif score > violence_heap[0][0]:
            removed = heapq.heapreplace(violence_heap, candidate)
            removed[2].close()
        else:
            preview.close()
        if score >= violence_threshold:
            name = (
                f"clip-{violence_windows_scored:06d}-{timestamp:.3f}s.jpg"
            )
            performance.call('preview_write', center.save,
                violence_dir / "thumbnails" / name,
                "JPEG", quality=82, optimize=True,
            )
            violence_hits.append({
                "frame_index": violence_windows_scored,
                "timestamp_seconds": round(timestamp, 3),
                "score": score,
                "predicted_label": "Violent",
                "thumbnail": f"thumbnails/{name}",
            })
        peak_rss = max(peak_rss, process.memory_info().rss)

    # Same digest as hashing after the scan; it overlaps decoding and inference
    # and is awaited before any report is written.
    input_hasher = BackgroundSha256(input_path)
    ffmpeg = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def events():
        """Serial read order: per packed frame, the violence window (if due) then the gore frame."""
        window: deque[Image.Image] = deque(maxlen=clip_frames)
        windows = 0
        packed_frame_index = 0
        while True:
            data = _read_exact(ffmpeg.stdout, packed_frame_bytes)
            if not data:
                return
            if len(data) != packed_frame_bytes:
                raise RuntimeError(
                    f"Incomplete packed frame: {len(data)} of {packed_frame_bytes} bytes"
                )
            packed = np.frombuffer(data, dtype=np.uint8).reshape(
                height, packed_width, 3
            )
            window.append(Image.frombytes(
                "RGB", (width, height), packed[:, width:, :].tobytes()
            ))
            produced["violence_frames"] += 1
            if (
                len(window) >= clip_frames
                and (produced["violence_frames"] - clip_frames) % stride_frames == 0
            ):
                new_frame_count = clip_frames if windows == 0 else min(stride_frames, clip_frames)
                pixels = torch.stack([
                    violence_transform(frame) for frame in list(window)[-new_frame_count:]
                ])
                windows += 1
                yield ("violence", pixels, produced["violence_frames"], window[clip_frames // 2])
            if packed_frame_index % rounded_ratio == 0:
                yield ("gore", Image.frombytes(
                    "RGB", (width, height), packed[:, :width, :].tobytes()
                ))
            packed_frame_index += 1

    status = "COMPLETED"
    error_message = None
    try:
        assert ffmpeg.stdout is not None
        prefetch = IteratorPrefetch(ffmpeg, events(), depth=EVENT_PREFETCH_DEPTH)
        try:
            with prefetch:
                for event in performance.iterate('event_wait', prefetch):
                    if event[0] == "violence":
                        process_violence_window(*event[1:])
                        continue
                    gore_batch.append(event[1])
                    gore_batch_indices.append(gore_frames_scanned)
                    gore_frames_scanned += 1
                    if len(gore_batch) >= gore_batch_size:
                        process_gore_batch()
                process_gore_batch()
                return_code = ffmpeg.wait()
        finally:
            if prefetch.finished:  # the producer read exactly the frames the serial loop would have
                violence_frames_scanned = produced["violence_frames"]
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
    input_hash = performance.call('source_hash', input_hasher.result)
    peak_cuda = (
        torch.cuda.max_memory_allocated(device) if device.type == "cuda" else 0
    )
    runtime = {
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(device) if device.type == "cuda" else None,
        "violence_precision": violence_precision,
    }
    shared_metrics = {
        "performance": performance.snapshot(),
        "elapsed_seconds": round(elapsed, 3),
        "video_seconds_per_processing_second": (
            round(video_duration / elapsed, 3) if elapsed else None
        ),
        "peak_process_ram_bytes": peak_rss,
        "peak_cuda_memory_bytes": peak_cuda,
        "shared_decode": True,
        "shared_decode_categories": ["gore", "violence"],
    }
    common = {
        "schema_version": 1,
        "status": status,
        "error": error_message,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": str(input_path),
        "input_size_bytes": input_stat.st_size,
        "input_sha256": input_hash,
        "duration_seconds": video_duration,
        "content_style": "live_action",
        "device": str(device),
        "runtime": runtime,
        "safety": {
            "automatic_edit": False,
            "note": "Every candidate requires review; a scan cannot prove that a video is safe.",
        },
    }

    gore_intervals = group_hits(
        gore_hits, gore_merge_gap_seconds, padding_seconds, video_duration
    )
    gore_retained = compact_interval_thumbnails(
        gore_dir, gore_hits, gore_intervals
    )
    gore_top_candidates = []
    for score, frame_index, image, predicted_label in sorted(
        gore_heap, reverse=True
    ):
        timestamp = frame_index / gore_sample_fps
        name = f"candidate-{frame_index:08d}-{timestamp:.3f}s.jpg"
        performance.call('preview_write', image.save,
            gore_dir / "candidates" / name,
            format="JPEG", quality=82, optimize=True,
        )
        image.close()
        gore_top_candidates.append({
            "rank": len(gore_top_candidates) + 1,
            "frame_index": frame_index,
            "timestamp_seconds": round(timestamp, 3),
            "score": round(score, 6),
            "predicted_label": predicted_label,
            "thumbnail": f"candidates/{name}",
        })
    gore_payload = {
        **common,
        "scan_type": "gore",
        "sample_fps": gore_sample_fps,
        "frames_scanned": gore_frames_scanned,
        "threshold": gore_threshold,
        "target_labels": [gore_labels[index] for index in gore_target_indices],
        "model_label_count": len(gore_labels),
        "temporal_confirmation": {
            "enabled": False,
            "window_frames": 5,
            "minimum_positive_frames": 3,
            "raw_hit_count": len(gore_hits),
            "confirmed_hit_count": len(gore_hits),
        },
        "score_summary": _score_summary(gore_scores),
        "top_candidates": gore_top_candidates,
        "model": gore_manifest,
        "intervals": gore_intervals,
        "retained_interval_thumbnail_count": gore_retained,
        "metrics": shared_metrics,
    }
    write_report(gore_dir, gore_payload)

    violence_intervals = group_hits(
        violence_hits, violence_merge_gap_seconds, padding_seconds, video_duration
    )
    violence_intervals = merge_intervals(violence_intervals, 0.0)
    for interval in violence_intervals:
        interval["priority"] = "context"
    violence_retained = compact_interval_thumbnails(
        violence_dir, violence_hits, violence_intervals
    )
    violence_top_candidates = []
    for score, window_index, image in sorted(violence_heap, reverse=True):
        center_frame_index = (
            (window_index - 1) * stride_frames
            + clip_frames - 1 - clip_frames // 2
        )
        timestamp = center_frame_index / violence_sample_fps
        name = f"candidate-{window_index:06d}-{timestamp:.3f}s.jpg"
        performance.call('preview_write', image.save,
            violence_dir / "candidates" / name,
            "JPEG", quality=82, optimize=True,
        )
        image.close()
        violence_top_candidates.append({
            "rank": len(violence_top_candidates) + 1,
            "frame_index": window_index,
            "timestamp_seconds": round(timestamp, 3),
            "score": round(score, 6),
            "predicted_label": "Violent",
            "thumbnail": f"candidates/{name}",
        })
    violence_payload = {
        **common,
        "scan_type": "violence",
        "sample_fps": violence_sample_fps,
        "clip_frames": clip_frames,
        "clip_seconds": round(clip_frames / violence_sample_fps, 3),
        "stride_frames": stride_frames,
        "frames_scanned": violence_frames_scanned,
        "windows_scored": violence_windows_scored,
        "threshold": violence_threshold,
        "high_threshold": None,
        "review_merge_gap_seconds": 0.0,
        "target_label": "Violent",
        "score_summary": _score_summary(violence_scores),
        "top_candidates": violence_top_candidates,
        "model": violence_manifest,
        "intervals": violence_intervals,
        "priority_counts": {
            "high": 0,
            "context": len(violence_intervals),
        },
        "retained_interval_thumbnail_count": violence_retained,
        "metrics": shared_metrics,
    }
    write_report(violence_dir, violence_payload)
    if status == "FAILED":
        raise RuntimeError(error_message)
    return {"gore": gore_payload, "violence": violence_payload}
