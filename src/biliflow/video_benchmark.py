from __future__ import annotations

import csv
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psutil
import torch
from PIL import Image
from safetensors.torch import load_file
from transformers import AutoConfig, AutoImageProcessor, AutoModelForVideoClassification, VideoMAEImageProcessor

from biliflow.image_benchmark import _sha256, binary_metrics
from biliflow.scanner import _read_exact
from biliflow.violence_scanner import aggregate_top_k_mean


def _read_rows(root: Path, manifest_path: Path) -> list[dict]:
    rows = []
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for number, row in enumerate(csv.DictReader(handle), start=2):
            missing = {"sample_id", "video_path", "style", "label"} - set(row)
            if missing:
                raise ValueError(f"Manifest is missing columns: {sorted(missing)}")
            path = (root / row["video_path"]).resolve(strict=True)
            if root != path and root not in path.parents:
                raise ValueError(f"Row {number} leaves project root: {path}")
            label = int(row["label"])
            if label not in (0, 1):
                raise ValueError(f"Row {number} label must be 0 or 1")
            rows.append({**row, "label": label, "resolved_path": path})
    return rows


def _decode_frames(ffmpeg_path: Path, video_path: Path, fps: float, count: int) -> list[Image.Image]:
    width = height = 256
    frame_bytes = width * height * 3
    command = [
        str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-i", str(video_path),
        "-vf", (
            f"fps={fps},scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
        ),
        "-frames:v", str(count), "-an", "-sn", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    frames = []
    assert process.stdout is not None
    for _ in range(count):
        data = _read_exact(process.stdout, frame_bytes)
        if len(data) != frame_bytes:
            break
        frames.append(Image.frombytes("RGB", (width, height), data))
    return_code = process.wait()
    if return_code != 0:
        stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        raise RuntimeError(f"FFmpeg failed for {video_path}: {stderr[-1000:]}")
    if len(frames) != count:
        raise RuntimeError(f"Expected {count} frames from {video_path}, got {len(frames)}")
    return frames


def benchmark_videos(
    *, project_root: Path, manifest_path: Path, report_dir: Path, model_path: Path,
    ffmpeg_path: Path, threshold: float = 0.5, sample_fps: float = 8.0,
    clip_frames: int = 16, device_name: str = "cuda",
) -> dict:
    root = project_root.resolve(strict=True)
    manifest_path = manifest_path.resolve(strict=True)
    report_dir = report_dir.resolve()
    reports_root = (root / "reports").resolve(strict=True)
    if report_dir != reports_root and reports_root not in report_dir.parents:
        raise ValueError(f"Report directory must stay inside {reports_root}")
    model_path = model_path.resolve(strict=True)
    ffmpeg_path = ffmpeg_path.resolve(strict=True)
    rows = _read_rows(root, manifest_path)
    device = torch.device(device_name)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but PyTorch cannot access the GPU")

    model_manifest = json.loads((model_path / "manifest.json").read_text(encoding="utf-8"))
    backend = model_manifest.get("backend")
    frame_window_backend = backend == "timm_frame_video"
    if frame_window_backend:
        import timm
        from torchvision.transforms import Compose, Normalize, Resize, ToTensor

        model = timm.create_model(
            model_manifest["architecture"], pretrained=False,
            num_classes=len(model_manifest["label_names"]),
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
        positive_indices = [int(model_manifest.get("positive_index", 1))]
        label_map = {
            index: label for index, label in enumerate(model_manifest["label_names"])
        }
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
        wanted = {label.casefold() for label in model_manifest.get("target_labels", [])}
        label_map = {int(index): str(label) for index, label in model.config.id2label.items()}
        positive_indices = [
            index for index, label in label_map.items() if label.casefold() in wanted
        ]
        if not positive_indices:
            positive_indices = [1]
    process = psutil.Process(os.getpid())
    peak_rss = process.memory_info().rss
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    started = time.perf_counter()
    results = []
    for row in rows:
        frames = _decode_frames(ffmpeg_path, row["resolved_path"], sample_fps, clip_frames)
        if frame_window_backend:
            pixels = torch.stack([frame_transform(frame) for frame in frames]).to(device)
            with torch.inference_mode():
                frame_scores = torch.softmax(model(pixels), dim=-1)[:, positive_indices[0]].cpu()
            score = aggregate_top_k_mean(
                frame_scores.tolist(), int(model_manifest.get("aggregation_top_k", 5))
            )
        else:
            arrays = [np.asarray(frame).copy() for frame in frames]
            assert processor is not None
            inputs = {key: value.to(device) for key, value in processor(arrays, return_tensors="pt").items()}
            with torch.inference_mode():
                vector = torch.softmax(model(**inputs).logits, dim=-1)[0].cpu()
            score = float(vector[positive_indices].sum())
        results.append({
            "sample_id": row["sample_id"], "video_path": row["video_path"],
            "style": row["style"], "expected": row["label"],
            "predicted": int(score >= threshold), "positive_score": round(score, 6),
            "sha256": _sha256(row["resolved_path"]), "source": row.get("source", ""),
            "notes": row.get("notes", ""),
        })
        for frame in frames:
            frame.close()
        peak_rss = max(peak_rss, process.memory_info().rss)

    elapsed = time.perf_counter() - started
    payload = {
        "schema_version": 1, "benchmark_type": "video_classification", "status": "COMPLETED",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(manifest_path), "manifest_sha256": _sha256(manifest_path),
        "model": model_manifest, "model_labels": label_map,
        "positive_indices": positive_indices, "threshold": threshold, "sample_fps": sample_fps,
        "clip_frames": clip_frames,
        "metrics": binary_metrics([r["expected"] for r in results], [r["predicted"] for r in results]),
        "metrics_by_style": {
            style: binary_metrics(
                [r["expected"] for r in results if r["style"] == style],
                [r["predicted"] for r in results if r["style"] == style],
            ) for style in sorted({r["style"] for r in results})
        },
        "performance": {
            "elapsed_seconds": round(elapsed, 3),
            "clips_per_second": round(len(results) / elapsed, 3) if elapsed else None,
            "peak_process_ram_bytes": peak_rss,
            "peak_cuda_memory_bytes": torch.cuda.max_memory_allocated(device) if device.type == "cuda" else 0,
            "device": str(device),
        },
        "results": results,
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "benchmark.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload
