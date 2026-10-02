from __future__ import annotations

from biliflow.performance import ScanPerformance

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

from biliflow.frame_prefetch import BatchPrefetch
from biliflow.intervals import compact_interval_thumbnails, group_hits, merge_intervals
from biliflow.probe import duration_seconds, probe_video
from biliflow.report import write_report
from biliflow.source_hash import BackgroundSha256
from biliflow.storage import require_capacity

BATCH_PREFETCH_DEPTH = 2


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


# Opt-in shot completion ("R3" in the C1 study). Measured on one labelled film
# only, so scan_nsfw keeps it off unless shot_completion=True is passed.
SHOT_COMPLETION_MAXIMUM_EXTENSION_SECONDS = 8.0
SHOT_COMPLETION_REACH_SECONDS = 20.0
SHOT_COMPLETION_MINIMUM_SEED_SAMPLES = 5
SHOT_COMPLETION_MINIMUM_CONTEXT_SHARE = 0.40
SHOT_COMPLETION_WINDOW_MARGIN_SECONDS = 0.5
_EDGE_EPSILON = 1e-6


def _union_seconds(windows: list[tuple[float, float]]) -> float:
    total = 0.0
    current: list[float] | None = None
    for start, end in sorted(windows):
        if current is not None and start <= current[1]:
            current[1] = max(current[1], end)
            continue
        if current is not None:
            total += current[1] - current[0]
        current = [start, end]
    if current is not None:
        total += current[1] - current[0]
    return total


def complete_nsfw_shot_context(
    intervals: list[dict],
    score_samples: list[dict],
    find_cuts,
    *,
    seed_threshold: float,
    context_threshold: float,
    padding_seconds: float,
    duration_seconds: float,
    maximum_extension_seconds: float = SHOT_COMPLETION_MAXIMUM_EXTENSION_SECONDS,
    reach_seconds: float = SHOT_COMPLETION_REACH_SECONDS,
    minimum_seed_samples: int = SHOT_COMPLETION_MINIMUM_SEED_SAMPLES,
    minimum_context_share: float = SHOT_COMPLETION_MINIMUM_CONTEXT_SHARE,
    window_margin_seconds: float = SHOT_COMPLETION_WINDOW_MARGIN_SECONDS,
) -> tuple[list[dict], dict]:
    """Move interval edges out to the hard cut of the adult shot they sit in.

    Runs after ``complete_nsfw_sequence_context``. A start edge moves back to the
    last cut at or before its first supporting sample (``start + padding``) only
    when that cut lies at most ``maximum_extension_seconds`` before the edge and
    the whole shot (from that cut to the next cut, at most ``reach_seconds``
    past the sample) holds at least ``minimum_seed_samples`` samples at the seed
    threshold and at least ``minimum_context_share`` of its samples at the
    context threshold. End edges mirror this. Without a cut in reach, or when
    the shot fails that check, the edge stays. Edges only move outward, the
    interval count never changes, and the thresholds are the scan's own.

    ``find_cuts(windows)`` returns cut timestamps inside the given windows. It is
    called once, only with windows around edges whose window already holds
    ``minimum_seed_samples`` seeds. Returns (intervals, statistics).
    """
    if not 0 <= context_threshold <= seed_threshold <= 1:
        raise ValueError("NSFW thresholds must be ordered between zero and one")
    if min(padding_seconds, maximum_extension_seconds, reach_seconds, window_margin_seconds) < 0:
        raise ValueError("Shot completion distances cannot be negative")
    if minimum_seed_samples < 1 or not 0 <= minimum_context_share <= 1:
        raise ValueError("Shot completion evidence gate is invalid")

    samples = sorted(score_samples, key=lambda item: float(item["timestamp_seconds"]))
    times = [float(sample["timestamp_seconds"]) for sample in samples]
    values = [float(sample["score"]) for sample in samples]

    def window_values(low: float, high: float) -> list[float]:
        return values[bisect_left(times, low):bisect_left(times, high)]

    def seed_count(low: float, high: float) -> int:
        return sum(value >= seed_threshold for value in window_values(low, high))

    def shot_evidence(low: float, high: float) -> dict:
        shot = window_values(low, high)
        seeds = sum(value >= seed_threshold for value in shot)
        share = sum(value >= context_threshold for value in shot) / len(shot) if shot else 0.0
        return {
            "shot_start_seconds": round(low, 3),
            "shot_end_seconds": round(high, 3),
            "sample_count": len(shot),
            "seed_sample_count": seeds,
            "context_share": round(share, 4),
            "gate_passed": bool(shot) and seeds >= minimum_seed_samples and share >= minimum_context_share,
        }

    # Pass 1: decide which edges need cuts; only those windows are decoded.
    plans = []
    for interval in intervals:
        start = float(interval["start_seconds"])
        end = float(interval["end_seconds"])
        start_anchor = start + padding_seconds
        end_anchor = end - padding_seconds
        edges = {}
        if start <= _EDGE_EPSILON:
            edges["start"] = {"status": "at_video_start"}
        elif seed_count(start - maximum_extension_seconds, start_anchor + reach_seconds) < minimum_seed_samples:
            edges["start"] = {"status": "too_few_seeds_nearby"}
        else:
            edges["start"] = {"status": "pending", "window": (
                max(0.0, start - maximum_extension_seconds - window_margin_seconds),
                min(duration_seconds, start_anchor + reach_seconds + window_margin_seconds),
            )}
        if end >= duration_seconds - _EDGE_EPSILON:
            edges["end"] = {"status": "at_video_end"}
        elif seed_count(end_anchor - reach_seconds, end + maximum_extension_seconds) < minimum_seed_samples:
            edges["end"] = {"status": "too_few_seeds_nearby"}
        else:
            edges["end"] = {"status": "pending", "window": (
                max(0.0, end_anchor - reach_seconds - window_margin_seconds),
                min(duration_seconds, end + maximum_extension_seconds + window_margin_seconds),
            )}
        plans.append((interval, start, end, start_anchor, end_anchor, edges))

    windows = [
        edge["window"] for *_, edges in plans for edge in edges.values() if edge["status"] == "pending"
    ]
    cuts = sorted(float(cut) for cut in find_cuts(windows)) if windows else []

    def cuts_between(low: float, high: float) -> list[float]:
        return cuts[bisect_left(cuts, low - _EDGE_EPSILON):bisect_right(cuts, high + _EDGE_EPSILON)]

    # Pass 2: move edges whose shot passes the evidence gate.
    completed: list[dict] = []
    status_counts: dict[str, int] = {}
    start_extended = end_extended = extended_intervals = 0
    extension_total = 0.0
    for interval, start, end, start_anchor, end_anchor, edges in plans:
        new_start, new_end = start, end
        start_edge = edges["start"]
        if start_edge["status"] == "pending":
            low, high = start_edge.pop("window")
            reachable = [cut for cut in cuts_between(start - maximum_extension_seconds, start_anchor)
                         if low <= cut <= high]
            if not reachable:
                start_edge["status"] = "no_cut_within_reach"
            elif reachable[-1] >= start - _EDGE_EPSILON:
                start_edge.update(status="cut_inside_interval", cut_seconds=round(reachable[-1], 3))
            else:
                cut = reachable[-1]
                following = [value for value in cuts_between(start_anchor, start_anchor + reach_seconds)
                             if value > start_anchor and low <= value <= high]
                shot_end = following[0] if following else start_anchor + reach_seconds
                evidence = shot_evidence(cut, shot_end)
                start_edge.update(cut_seconds=round(cut, 3), **evidence)
                if evidence["gate_passed"]:
                    new_start = cut
                    start_edge.update(status="extended", extension_seconds=round(start - cut, 3))
                else:
                    start_edge["status"] = "evidence_gate_failed"
        end_edge = edges["end"]
        if end_edge["status"] == "pending":
            low, high = end_edge.pop("window")
            reachable = [cut for cut in cuts_between(end_anchor, end + maximum_extension_seconds)
                         if cut > end_anchor and low <= cut <= high]
            if not reachable:
                end_edge["status"] = "no_cut_within_reach"
            elif reachable[0] <= end + _EDGE_EPSILON:
                end_edge.update(status="cut_inside_interval", cut_seconds=round(reachable[0], 3))
            else:
                cut = reachable[0]
                preceding = [value for value in cuts_between(end_anchor - reach_seconds, end_anchor)
                             if low <= value <= high]
                shot_start = preceding[-1] if preceding else end_anchor - reach_seconds
                evidence = shot_evidence(shot_start, cut)
                end_edge.update(cut_seconds=round(cut, 3), **evidence)
                if evidence["gate_passed"]:
                    new_end = min(duration_seconds, cut)
                    end_edge.update(status="extended", extension_seconds=round(new_end - end, 3))
                else:
                    end_edge["status"] = "evidence_gate_failed"
        for edge in (start_edge, end_edge):
            status_counts[edge["status"]] = status_counts.get(edge["status"], 0) + 1
        applied = new_start < start or new_end > end
        start_extended += new_start < start
        end_extended += new_end > end
        extended_intervals += applied
        extension_total += (start - new_start) + (new_end - end)
        value = dict(interval)
        value["start_seconds"] = round(new_start, 3)
        value["end_seconds"] = round(new_end, 3)
        value["shot_context"] = {
            "applied": applied,
            "input_start_seconds": round(start, 3),
            "input_end_seconds": round(end, 3),
            "start": start_edge,
            "end": end_edge,
        }
        completed.append(value)

    statistics = {
        "edge_count": 2 * len(intervals),
        "edge_status_counts": dict(sorted(status_counts.items())),
        "decoded_edge_count": len(windows),
        "decoded_window_seconds": round(_union_seconds(windows), 3),
        "cut_count": len(cuts),
        "extended_start_edge_count": start_extended,
        "extended_end_edge_count": end_extended,
        "extended_interval_count": extended_intervals,
        "added_interval_seconds": round(extension_total, 3),
    }
    return completed, statistics


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
    shot_completion: bool = False,
) -> dict:
    performance = ScanPerformance()
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

    with performance.measure('model_load'):
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
    prefetch_stats: dict = {}
    # Same digest as hashing after the scan; it overlaps decoding and inference
    # and is awaited before the report is written.
    input_hasher = BackgroundSha256(input_path)
    ffmpeg = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def read_frame() -> Image.Image | None:
        data = _read_exact(ffmpeg.stdout, frame_bytes)
        if not data:
            return None
        if len(data) != frame_bytes:
            raise RuntimeError(f"Incomplete raw frame: {len(data)} of {frame_bytes} bytes")
        return Image.frombytes("RGB", (width, height), data)

    def prepare_batch(images: list[Image.Image]):
        return processor(images=images, return_tensors="pt")

    def process_batch(batch: list[Image.Image], batch_indices: list[int], prepared) -> None:
        nonlocal peak_rss
        with performance.measure('model_step'):
            inputs = {key: value.to(device) for key, value in prepared.items()}
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
                performance.call('preview_write', image.save, thumbs / name, format="JPEG", quality=82, optimize=True)
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

    status = "COMPLETED"
    error_message = None
    try:
        assert ffmpeg.stdout is not None
        # Frame reads and the image processor for batch n+1 overlap the forward of
        # batch n; batches and their order are exactly those of the serial loop.
        prefetch = BatchPrefetch(ffmpeg, read_frame, prepare_batch, batch_size, depth=BATCH_PREFETCH_DEPTH)
        handed_over = 0
        try:
            with prefetch:
                for batch, batch_indices, prepared in performance.iterate('batch_wait', prefetch):
                    handed_over = batch_indices[-1] + 1  # the in-flight batch counts, as in the serial loop
                    process_batch(batch, batch_indices, prepared)
                return_code = ffmpeg.wait()
        finally:
            # A producer that stopped on its own read exactly the frames the serial loop would have.
            frames_scanned = prefetch.frames_read if prefetch.finished else handed_over
            prefetch_stats.update(depth=prefetch.depth, read_seconds=round(prefetch.read_seconds, 6),
                                  prepare_seconds=round(prefetch.prepare_seconds, 6))
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
    shot_completion_report = {
        "enabled": bool(shot_completion) and content_style == "live_action",
        "status": "DISABLED",
        "seed_threshold": threshold,
        "context_threshold": sequence_context_threshold,
        "maximum_extension_seconds": SHOT_COMPLETION_MAXIMUM_EXTENSION_SECONDS,
        "shot_reach_seconds": SHOT_COMPLETION_REACH_SECONDS,
        "minimum_seed_samples": SHOT_COMPLETION_MINIMUM_SEED_SAMPLES,
        "minimum_context_share": SHOT_COMPLETION_MINIMUM_CONTEXT_SHARE,
        "moderate_evidence_creates_new_interval": False,
    }
    if shot_completion_report["enabled"]:
        if status != "COMPLETED":
            shot_completion_report["status"] = "SKIPPED_SCAN_NOT_COMPLETED"
        else:
            from biliflow import shot_cuts

            shot_completion_report["cut_detector"] = shot_cuts.DEFAULT_SETTINGS.as_dict()
            try:
                with performance.measure('shot_completion'):
                    intervals, shot_statistics = complete_nsfw_shot_context(
                        intervals,
                        score_samples,
                        lambda windows: shot_cuts.find_window_cuts(ffmpeg_path, input_path, windows),
                        seed_threshold=threshold,
                        context_threshold=sequence_context_threshold,
                        padding_seconds=padding_seconds,
                        duration_seconds=video_duration,
                    )
            except Exception as exc:
                # Opt-in refinement only: keep the sequence-completed intervals.
                shot_completion_report.update(status="FAILED", error=str(exc))
            else:
                shot_completion_report.update(status="APPLIED", **shot_statistics)
    elif shot_completion:
        shot_completion_report["status"] = "NOT_APPLICABLE_CONTENT_STYLE"
    retained_thumbnail_count = compact_interval_thumbnails(report_dir, hits, intervals)
    top_candidates = []
    for score, frame_index, image in sorted(candidate_heap, reverse=True):
        timestamp = frame_index / sample_fps
        name = f"candidate-{frame_index:08d}-{timestamp:.3f}s.jpg"
        performance.call('preview_write', image.save, candidates_dir / name, format="JPEG", quality=82, optimize=True)
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
        "input_sha256": performance.call('source_hash', input_hasher.result),
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
        "shot_completion": shot_completion_report,
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
            "performance": performance.snapshot(),
            "elapsed_seconds": round(elapsed, 3),
            "video_seconds_per_processing_second": round(video_duration / elapsed, 3) if elapsed else None,
            "peak_process_ram_bytes": peak_rss,
            "peak_cuda_memory_bytes": torch.cuda.max_memory_allocated(device) if device.type == "cuda" else 0,
            "batch_prefetch": prefetch_stats,
        },
    }
    write_report(report_dir, payload)
    if status == "FAILED":
        raise RuntimeError(error_message)
    return payload
