"""Second-stage verifier and triage settings for live-action 18+ candidates.

docs/ADULT_FALSE_ALARM_PLAN.md (step 2, verifier "V-c"). ``verify_adult_report``
re-decodes every interval of an nsfw-nano scan report (``scan_type == "nsfw"``)
at 2 fps with the scanner's own 448 px letterbox, scores each frame with the NSFW
head of ``image_safety_classifier_m`` and writes a copy of the report in which
every interval gains an ``adult_verification`` block. Intervals are never
dropped, added, moved or reordered: the copy only carries scores.

``build-review`` (``review_workflow.triage_adult_items``) is the only reader of
the scores. It may move a weak, undecided live-action candidate to the optional
list ("Ứng viên phụ"); it never deletes one. A missing, failed or uncalibrated
verification moves nothing.

Heavy imports (torch, timm, PIL) stay inside functions so the review workflow
and the Control Center can import the settings below cheaply.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# --- Settings: the only place that defines the 18+ verifier and triage ---------
# Evidence: docs/ADULT_FALSE_ALARM_PLAN.md §0-§6; per-item verifier scores in
# temp/adult-fp/task-c-verifier/safety_m_items.json; k x t grid with Troy rev 4
# decisions, the lone clear-nude shot test and Golden precision in
# temp/adult-fp/plan/and-grid.json (stack-results.json for the single rules).

# Verifier model and preprocessing, exactly as measured in task C (gate 4.4
# compares production scores with that measurement within +-0.01).
VERIFIER_MODEL_DIR = "image_safety_classifier_m"
VERIFIER_TARGET_LABEL = "NSFW"
# Thresholds below were calibrated with this weight revision only. Another
# revision is "uncalibrated" and moves nothing until it is measured again.
VERIFIER_CALIBRATED_REVISION = "a5ce9eec1ac11773ca9ff44f45b1bb6591631562"
VERIFIER_SAMPLE_FPS = 2.0
VERIFIER_FRAME_SIZE = 448
VERIFIER_BATCH_SIZE = 16
VERIFIER_MINIMUM_SPAN_SECONDS = 0.5
VERIFIER_PREPROCESSING = (
    "ffmpeg -ss <start> -t max(0.5,end-start) -i <source> "
    "-vf fps=2,scale=448:448:force_original_aspect_ratio=decrease,"
    "pad=448:448:(ow-iw)/2:(oh-ih)/2 rgb24; timm create_transform(config.json "
    "pretrained_cfg, eval); softmax; p(NSFW)"
)

# A "seed" is an nsfw-nano sample at or above the scan threshold (interval
# sample_count). Seed counts are only comparable at the settings they were
# measured with (careful profile: 2 fps, threshold 0.95), so other scans keep
# every candidate in the main list.
TRIAGE_SCAN_SAMPLE_FPS = 2.0
TRIAGE_SCAN_THRESHOLD = 0.95

# R1: on live action, an item whose every interval is labelled "hentai" by
# nsfw-nano is credits / text on a dark background (7/7 such Troy rev 4 items,
# 0 real nudity; the real item with the most hentai seeds had 1/83).
CREDITS_LABEL = "hentai"
# The credits rule is skipped when the second-stage verifier scores the item at or above this
# (all Troy credits items score <= 0.13; a real-nudity interval whose strongest frame nano
# happens to label "hentai" therefore stays in the main list).
CREDITS_VERIFIER_GUARD = 0.7

# Two-signal rule: move only when BOTH signals are weak, n_seeds < k AND
# verifier NSFW max < t. t = 0.7 (not 0.8) because the user counts implied
# nudity (blanket, framed at the shoulders) as 18+ to confirm (decision
# 2026-10-01, plan §5 condition 7): gs-T5-0004 has NSFW 0.818 and needs a
# margin >= 0.1. Measured on Troy rev 4 with R1 (and-grid.json):
#   k=2, t=0.7: 41 of 86 false alarms move (474.5 s), 0 BLUR and 0 lone
#               clear-nude shots move; Golden 18+ main precision 5/23 -> 5/16.
#   k=5, t=0.7: 59 of 86 move (642.0 s), 0 BLUR and 0 lone shots move; 5/9.
# Weakest real nudity: 2 seeds with NSFW 0.92; lowest NSFW 0.692 with 28 seeds.
ADULT_TRIAGE_LEVELS: dict[str, dict[str, Any]] = {
    "off": {"credits_rule": False, "two_signal": None},
    "credits": {"credits_rule": True, "two_signal": None},
    "conservative": {"credits_rule": True, "two_signal": {"k": 2, "t": 0.7}},
    # Stays OFF by default: enable only after a second live-action film passes
    # gate 4.6 and the user agrees (decision 2026-10-01; no second film yet).
    "balanced": {"credits_rule": True, "two_signal": {"k": 5, "t": 0.7}},
}
# The level every job uses (user decision 2026-10-01: conservative now).
ADULT_TRIAGE_LEVEL = "conservative"
ADULT_TRIAGE_EVIDENCE = (
    "docs/ADULT_FALSE_ALARM_PLAN.md; temp/adult-fp/plan/and-grid.json; "
    "temp/adult-fp/task-c-verifier/safety_m_items.json"
)


def normalize_adult_triage_level(value: str | None) -> str:
    level = ADULT_TRIAGE_LEVEL if value is None else str(value)
    if level not in ADULT_TRIAGE_LEVELS:
        raise ValueError(
            "Adult triage level must be one of " + ", ".join(ADULT_TRIAGE_LEVELS)
        )
    return level


def verification_is_calibrated(verification: Any) -> bool:
    """True only for a SCORED interval measured exactly like the calibration."""
    if not isinstance(verification, dict) or verification.get("state") != "SCORED":
        return False
    value = verification.get("nsfw_max")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        same_rate = abs(float(verification.get("sample_fps")) - VERIFIER_SAMPLE_FPS) < 1e-9
        frames = int(verification.get("nsfw_frames") or 0)
    except (TypeError, ValueError):
        return False
    return (
        0.0 <= float(value) <= 1.0
        and same_rate
        and frames > 0
        and verification.get("frame_size") == VERIFIER_FRAME_SIZE
        and verification.get("model") == VERIFIER_MODEL_DIR
        and verification.get("target_label") == VERIFIER_TARGET_LABEL
        and verification.get("revision") == VERIFIER_CALIBRATED_REVISION
    )


# --- Verification --------------------------------------------------------------

def _inside(root: Path, path: Path, label: str) -> Path:
    root = root.resolve(strict=True)
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"{label} must stay inside {root}")
    return resolved


def interval_span(interval: dict) -> tuple[float, float]:
    """(seek start, decode duration) for one report interval."""
    start = max(0.0, float(interval["start_seconds"]))
    end = float(interval["end_seconds"])
    if not end >= start:
        raise ValueError(f"interval ends before it starts ({start} > {end})")
    return start, max(VERIFIER_MINIMUM_SPAN_SECONDS, end - start)


def decode_command(ffmpeg_path: Path, video_path: Path, start: float, duration: float) -> list[str]:
    """The task C measurement command (fast input seek; same filter as scan_nsfw)."""
    size = VERIFIER_FRAME_SIZE
    return [
        str(ffmpeg_path), "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(video_path),
        "-vf", (
            f"fps={VERIFIER_SAMPLE_FPS:g},"
            f"scale={size}:{size}:force_original_aspect_ratio=decrease,"
            f"pad={size}:{size}:(ow-iw)/2:(oh-ih)/2"
        ),
        "-an", "-sn", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]


class _ActiveProcess:
    """The FFmpeg process the producer is reading, for IteratorPrefetch shutdown."""

    def __init__(self):
        self.process: subprocess.Popen | None = None

    def poll(self):
        process = self.process
        return 0 if process is None else process.poll()

    def terminate(self):
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()

    def kill(self):
        process = self.process
        if process is not None and process.poll() is None:
            process.kill()

    def wait(self, timeout=None):
        process = self.process
        return 0 if process is None else process.wait(timeout=timeout)


def _interval_frame_batches(ffmpeg_path: Path, video_path: Path, intervals: list[dict],
                            batch_size: int, holder: _ActiveProcess, cancelled: threading.Event):
    """Yield ("frames", i, images), then ("done", i, count) or ("failed", i, error) per interval.

    One interval failing never stops the others; its partial frames are discarded
    by the consumer when "failed" arrives. ``cancelled`` stops before the next
    FFmpeg process starts once the consumer has left.
    """
    from PIL import Image

    from biliflow.scanner import _read_exact

    size = VERIFIER_FRAME_SIZE
    frame_bytes = size * size * 3
    for index, interval in enumerate(intervals):
        if cancelled.is_set():
            return
        try:
            start, duration = interval_span(interval)
        except (KeyError, TypeError, ValueError) as error:
            yield ("failed", index, f"Invalid interval: {error}")
            continue
        process = None
        drain = None
        errors = bytearray()
        try:
            process = subprocess.Popen(
                decode_command(ffmpeg_path, video_path, start, duration),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            holder.process = process
            # FFmpeg must never block on a full stderr pipe while we read frames.
            drain = threading.Thread(target=_drain, args=(process.stderr, errors), daemon=True)
            drain.start()
            if cancelled.is_set():
                return
            batch = []
            count = 0
            while True:
                data = _read_exact(process.stdout, frame_bytes)
                if not data:
                    break
                if len(data) != frame_bytes:
                    raise RuntimeError(f"Incomplete raw frame: {len(data)} of {frame_bytes} bytes")
                batch.append(Image.frombytes("RGB", (size, size), data))
                count += 1
                if len(batch) == batch_size:
                    yield ("frames", index, batch)
                    batch = []
            if batch:
                yield ("frames", index, batch)
            code = process.wait()
            drain.join(timeout=10)
            if code != 0:
                stderr = bytes(errors).decode("utf-8", errors="replace")
                raise RuntimeError(f"FFmpeg failed with exit code {code}: {stderr[-2000:]}")
            yield ("done", index, count)
        except Exception as error:  # this interval only; the next one still runs
            yield ("failed", index, str(error))
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
                if drain is not None:
                    drain.join(timeout=10)
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
            holder.process = None


def _drain(stream, buffer: bytearray, keep: int = 8192) -> None:
    try:
        for chunk in iter(lambda: stream.read(4096), b""):
            buffer.extend(chunk)
            if len(buffer) > keep:
                del buffer[:-keep]
    except (OSError, ValueError):  # stream closed while stopping
        pass


def _load_scorer(model_path: Path, device_name: str):
    """NSFW probability per image from the gore loader (same weights and transform)."""
    import torch

    from biliflow.content_scanner import _load_classifier

    manifest = json.loads((model_path / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("backend", "timm") != "timm":
        raise ValueError("The 18+ verifier requires the single-label timm safety classifier")
    device = torch.device(device_name)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but PyTorch cannot access the GPU")
    score, labels, target_indices = _load_classifier(
        "gore", model_path, device, target_labels=(VERIFIER_TARGET_LABEL,),
    )
    if [labels[index] for index in target_indices] != [VERIFIER_TARGET_LABEL]:
        raise ValueError("The safety classifier has no NSFW class")

    def nsfw(images) -> list[float]:
        return [float(value) for value in score(images)[0]]

    return nsfw, manifest, device


def _peak_cuda_memory(device) -> int:
    try:
        import torch

        if getattr(device, "type", None) == "cuda":
            return int(torch.cuda.max_memory_allocated(device))
    except Exception:  # telemetry only
        pass
    return 0


def _verification_fields(manifest: dict) -> dict:
    return {
        "sample_fps": VERIFIER_SAMPLE_FPS,
        "frame_size": VERIFIER_FRAME_SIZE,
        "preprocessing": VERIFIER_PREPROCESSING,
        "model": VERIFIER_MODEL_DIR,
        "revision": manifest.get("revision"),
        "target_label": VERIFIER_TARGET_LABEL,
    }


def verify_adult_report(
    *,
    project_root: Path,
    report_path: Path,
    output_path: Path,
    model_path: Path,
    ffmpeg_path: Path,
    device_name: str = "cuda",
    batch_size: int = VERIFIER_BATCH_SIZE,
) -> dict:
    """Write ``output_path``: the nsfw report plus per-interval NSFW verification.

    Per-interval decode failures are recorded as ``state: FAILED`` (no triage
    move). A model/GPU failure, an invalid report or a source that changed since
    the scan raises without writing the output, so the stage fails visibly and
    no partial verification is cached.
    """
    from biliflow.frame_prefetch import IteratorPrefetch
    from biliflow.performance import ScanPerformance
    from biliflow.source_hash import BackgroundSha256

    root = project_root.resolve(strict=True)
    reports_root = (root / "reports").resolve(strict=True)
    models_root = (root / "models").resolve(strict=True)
    report_path = _inside(reports_root, report_path, "Report path").resolve(strict=True)
    output_path = _inside(reports_root, output_path, "Output path")
    model_path = _inside(models_root, model_path, "Model path").resolve(strict=True)
    ffmpeg_path = Path(ffmpeg_path).resolve(strict=True)
    if output_path.parent != report_path.parent:
        raise ValueError("Verified report must stay beside its source report so previews remain valid")
    if output_path == report_path:
        raise ValueError("Verified report must not overwrite the scan report")
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")

    report_bytes = report_path.read_bytes()
    source = json.loads(report_bytes.decode("utf-8"))
    if not isinstance(source, dict) or source.get("scan_type") != "nsfw":
        raise ValueError("Only nsfw (18+) scan reports can be verified")
    if source.get("status") not in {"COMPLETED", "REVIEW_REQUIRED"}:
        raise ValueError("Source report is not complete")
    if "adult_verification" in source:
        raise ValueError("Report is already verified; verify the original scan.json")
    video_path = Path(str(source["input"])).resolve(strict=True)
    expected_sha256 = str(source.get("input_sha256") or "")
    intervals = copy.deepcopy(list(source.get("intervals") or []))

    performance = ScanPerformance()
    started = time.perf_counter()
    manifest: dict = json.loads((model_path / "manifest.json").read_text(encoding="utf-8"))
    scores: dict[int, list[float]] = {index: [] for index in range(len(intervals))}
    outcomes: dict[int, tuple[str, Any]] = {}
    peak_cuda_memory = 0
    if intervals:
        if not expected_sha256:
            raise ValueError("Scan report has no input_sha256; cannot prove the source is unchanged")
        input_stat = video_path.stat()
        hasher = BackgroundSha256(video_path)
        try:
            with performance.measure("model_load"):
                scorer, manifest, device = _load_scorer(model_path, device_name)
            holder = _ActiveProcess()
            cancelled = threading.Event()
            batches = _interval_frame_batches(
                ffmpeg_path, video_path, intervals, batch_size, holder, cancelled,
            )
            # Decoding the next interval overlaps the model on the current one;
            # every frame is scored exactly once, in interval order.
            with IteratorPrefetch(holder, batches, depth=4) as prefetch:
                try:
                    for kind, index, value in performance.iterate("frame_wait", prefetch):
                        if kind == "frames":
                            with performance.measure("model_step"):
                                values = scorer(value)
                            if len(values) != len(value):
                                raise RuntimeError("Verifier returned a wrong number of scores")
                            scores[index].extend(values)
                            for image in value:
                                image.close()
                        else:
                            outcomes[index] = (kind, value)
                finally:
                    cancelled.set()
            peak_cuda_memory = _peak_cuda_memory(device)
            final_stat = video_path.stat()
            if (final_stat.st_size, final_stat.st_mtime_ns) != (input_stat.st_size, input_stat.st_mtime_ns):
                raise RuntimeError("Input video changed while it was being verified")
            with performance.measure("source_hash_wait"):
                actual_sha256 = hasher.result()
            if actual_sha256 != expected_sha256:
                raise RuntimeError("Input video differs from the one the 18+ scan analysed (SHA-256 mismatch)")
        except BaseException:
            hasher.cancel()
            raise

    fields = _verification_fields(manifest)
    scored = failed = frames_scored = 0
    for index, interval in enumerate(intervals):
        kind, value = outcomes.get(index, ("failed", "Interval was not decoded"))
        values = scores[index]
        if kind == "done" and values and int(value) == len(values):
            verification = {
                "state": "SCORED",
                "nsfw_max": round(max(values), 6),
                "nsfw_frames": len(values),
                "nsfw_scores": [round(item, 4) for item in values],
                **fields,
            }
            scored += 1
            frames_scored += len(values)
        else:
            error = value if kind == "failed" else "No frame was decoded"
            verification = {
                "state": "FAILED", "error": str(error)[:2000],
                "nsfw_max": None, "nsfw_frames": 0, **fields,
            }
            failed += 1
        try:
            start, duration = interval_span(interval)
            verification["decoded_span_seconds"] = [round(start, 3), round(start + duration, 3)]
        except (KeyError, TypeError, ValueError):
            pass
        interval["adult_verification"] = verification

    payload = copy.deepcopy(source)
    payload["intervals"] = intervals
    payload["verified_at"] = datetime.now(timezone.utc).isoformat()
    payload["adult_verification"] = {
        "method": "second_stage_nsfw_verifier",
        "state": "FAILED" if failed and not scored else "PARTIAL" if failed else "COMPLETED",
        "source_report": report_path.relative_to(root).as_posix(),
        "source_report_sha256": hashlib.sha256(report_bytes).hexdigest(),
        "model_manifest": {
            key: manifest.get(key)
            for key in ("model", "repo_id", "revision", "license_spdx", "approval_status")
        },
        **fields,
        "calibrated_revision": VERIFIER_CALIBRATED_REVISION,
        "interval_count": len(intervals),
        "scored_interval_count": scored,
        "failed_interval_count": failed,
        "frames_scored": frames_scored,
        "device": device_name,
        "note": (
            "Scores only. Intervals are never removed; build-review may move weak, undecided "
            "live-action candidates to the optional list, and a failed interval moves nothing."
        ),
    }
    metrics = payload.setdefault("metrics", {})
    metrics["verification_elapsed_seconds"] = round(time.perf_counter() - started, 3)
    metrics["verification_peak_cuda_memory_bytes"] = peak_cuda_memory
    metrics["verification_performance"] = performance.snapshot()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output_path)
    return payload
