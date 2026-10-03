from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from biliflow.blur_filter import (
    blur_feather_mode,
    blur_parameters,
    regional_blur_filters,
)
from biliflow.probe import duration_seconds, probe_video
from biliflow.resource_lock import project_resource_lock
from biliflow.storage import require_capacity


DEFAULT_MAX_OUTPUT_BYTES = 3_500_000_000
DEFAULT_TARGET_OUTPUT_BYTES = 3_300_000_000
MIN_CUSTOM_OUTPUT_GB = 0.05
MAX_CUSTOM_OUTPUT_GB = 1_000.0


def normalize_output_size_policy(
    mode: str | None = "default", custom_limit_gb: object = None,
) -> dict:
    """Return one auditable output-size policy for a single video export."""
    normalized_mode = str(mode or "default").strip().lower()
    if normalized_mode == "default":
        return {
            "mode": "default",
            "maximum_output_bytes": DEFAULT_MAX_OUTPUT_BYTES,
            "target_output_bytes": DEFAULT_TARGET_OUTPUT_BYTES,
            "maximum_output_gb": 3.5,
        }
    if normalized_mode == "unlimited":
        return {
            "mode": "unlimited",
            "maximum_output_bytes": None,
            "target_output_bytes": None,
            "maximum_output_gb": None,
        }
    if normalized_mode != "custom":
        raise ValueError("Chế độ dung lượng phải là mặc định, tùy chỉnh hoặc không giới hạn")
    try:
        maximum_gb = float(custom_limit_gb)
    except (TypeError, ValueError) as error:
        raise ValueError("Hãy nhập giới hạn dung lượng tùy chỉnh theo GB") from error
    if (
        not math.isfinite(maximum_gb)
        or maximum_gb < MIN_CUSTOM_OUTPUT_GB
        or maximum_gb > MAX_CUSTOM_OUTPUT_GB
    ):
        raise ValueError(
            f"Giới hạn tùy chỉnh phải từ {MIN_CUSTOM_OUTPUT_GB:g} đến "
            f"{MAX_CUSTOM_OUTPUT_GB:g} GB"
        )
    maximum_bytes = int(round(maximum_gb * 1_000_000_000))
    target_ratio = DEFAULT_TARGET_OUTPUT_BYTES / DEFAULT_MAX_OUTPUT_BYTES
    target_bytes = min(maximum_bytes - 1, int(maximum_bytes * target_ratio))
    return {
        "mode": "custom",
        "maximum_output_bytes": maximum_bytes,
        "target_output_bytes": target_bytes,
        "maximum_output_gb": maximum_gb,
    }


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _inside(root: Path, path: Path, label: str) -> Path:
    root = root.resolve(strict=True)
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"{label} must stay inside {root}")
    return resolved


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def render_progress_path(project_root: Path, output_path: Path) -> Path:
    """Return a small temporary FFmpeg telemetry file for one output path."""
    root = project_root.resolve()
    output = output_path.resolve()
    identity = hashlib.sha256(str(output).casefold().encode("utf-8")).hexdigest()[:20]
    return root / "temp" / "render-progress" / f"{identity}.progress"


def read_render_progress(
    path: Path, *, expected_duration_seconds: float,
) -> dict | None:
    """Read the latest complete FFmpeg `-progress` values without locking it."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    values: dict[str, str] = {}
    for line in content.splitlines():
        key, separator, value = line.partition("=")
        if separator and key and value:
            values[key] = value
    try:
        out_time_us = int(values.get("out_time_us") or values.get("out_time_ms") or 0)
    except ValueError:
        out_time_us = 0
    encoded_seconds = max(0.0, out_time_us / 1_000_000)
    expected = max(0.001, float(expected_duration_seconds))
    percent = min(100.0, max(0.0, encoded_seconds * 100 / expected))
    speed_text = str(values.get("speed") or "").strip()
    try:
        speed = float(speed_text.removesuffix("x"))
    except ValueError:
        speed = None
    remaining = max(0.0, expected - encoded_seconds)
    eta_seconds = remaining / speed if speed and speed > 0 else None
    try:
        total_size = int(values.get("total_size") or 0)
    except ValueError:
        total_size = 0
    progress_value = str(values.get("progress") or "continue").strip().lower()
    return {
        "state": "VERIFYING" if progress_value == "end" else "RENDERING",
        "percent": round(100.0 if progress_value == "end" else percent, 1),
        "encoded_seconds": round(encoded_seconds, 3),
        "expected_duration_seconds": round(expected, 3),
        "speed": speed,
        "speed_text": speed_text or None,
        "eta_seconds": round(eta_seconds, 1) if eta_seconds is not None else None,
        "total_size_bytes": total_size,
        "frame": values.get("frame"),
        "fps": values.get("fps"),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _move_into_place(partial: Path, output_path: Path) -> None:
    """Give the validated render its export name; never replaces a file already there.

    A file that appeared at the export path during the render (a copy, a
    leftover) stays untouched and the render is discarded. On Windows
    os.rename itself refuses an existing target.
    """
    try:
        if os.path.lexists(output_path):
            raise FileExistsError(f"Output already exists: {output_path}")
        os.rename(partial, output_path)
    except OSError:
        partial.unlink(missing_ok=True)
        raise


def _merge_cuts(operations: list[dict], duration: float) -> list[tuple[float, float]]:
    raw = sorted(
        (
            max(0.0, float(item["start_seconds"])),
            min(duration, float(item["end_seconds"])),
        )
        for item in operations if item["type"] == "cut"
    )
    merged: list[list[float]] = []
    for start, end in raw:
        if start >= end:
            raise ValueError("CUT operation has an empty interval")
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _kept_segments(cuts: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    segments = []
    cursor = 0.0
    for start, end in cuts:
        if start > cursor:
            segments.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration:
        segments.append((cursor, duration))
    if not segments:
        raise ValueError("Edit plan removes the entire source")
    return segments


def _overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> bool:
    return max(start_a, start_b) < min(end_a, end_b)


def _region_overlap_over_smaller(first: dict, second: dict) -> float:
    ax1, ay1 = int(first["x"]), int(first["y"])
    ax2, ay2 = ax1 + int(first["width"]), ay1 + int(first["height"])
    bx1, by1 = int(second["x"]), int(second["y"])
    bx2, by2 = bx1 + int(second["width"]), by1 + int(second["height"])
    intersection = max(0, min(ax2, bx2) - max(ax1, bx1)) * max(
        0, min(ay2, by2) - max(ay1, by1)
    )
    smaller = min(
        max(1, int(first["width"]) * int(first["height"])),
        max(1, int(second["width"]) * int(second["height"])),
    )
    return intersection / smaller


def _same_regional_blur_target(first: dict, second: dict) -> bool:
    first_area = max(1, int(first["width"]) * int(first["height"]))
    second_area = max(1, int(second["width"]) * int(second["height"]))
    return (
        max(first_area, second_area) / min(first_area, second_area) <= 2.0
        and _region_overlap_over_smaller(first, second) >= 0.70
    )


def _prune_redundant_blurs(blurs: list[dict]) -> list[dict]:
    """Drop short blur filters already covered by a longer equivalent blur.

    Review keeps every approved item's provenance, but rendering the same
    persistent watermark again in dozens of five-second windows creates one
    FFmpeg split/gblur/overlay branch per decision. A feature-length source can
    then spawn more than a thousand filter threads before producing one frame.
    """
    ordered = sorted(
        blurs,
        key=lambda item: (
            -(float(item["end_seconds"]) - float(item["start_seconds"])),
            float(item["start_seconds"]),
        ),
    )
    retained: list[dict] = []
    for candidate in ordered:
        candidate_region = candidate.get("region_source_pixels")
        redundant = False
        for covering in retained:
            if (
                float(covering["start_seconds"]) > float(candidate["start_seconds"]) + 1e-6
                or float(covering["end_seconds"]) < float(candidate["end_seconds"]) - 1e-6
            ):
                continue
            covering_region = covering.get("region_source_pixels")
            if covering_region == "FULL_FRAME":
                if blur_parameters(covering) == blur_parameters(candidate):
                    redundant = True
                    break
            if (
                isinstance(covering_region, dict)
                and isinstance(candidate_region, dict)
                and _same_regional_blur_target(covering_region, candidate_region)
                and blur_parameters(covering) == blur_parameters(candidate)
                and blur_feather_mode(covering) == blur_feather_mode(candidate)
            ):
                redundant = True
                break
        if not redundant:
            retained.append(candidate)
    return sorted(retained, key=lambda item: float(item["start_seconds"]))


def _merge_time_ranges(values: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for start, end in sorted(values):
        if merged and start <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _blur_group_key(blur: dict) -> tuple:
    region = blur.get("region_source_pixels")
    if isinstance(region, dict):
        region_key: object = tuple(
            int(region[key]) for key in ("x", "y", "width", "height")
        )
    else:
        region_key = region
    sigma, feather = blur_parameters(blur)
    return region_key, sigma, feather, blur_feather_mode(blur)


def build_final_filter_graph(
    *, operations: list[dict], duration: float, has_audio: bool,
) -> tuple[str, float]:
    cuts = _merge_cuts(operations, duration)
    segments = _kept_segments(cuts, duration)
    blurs = _prune_redundant_blurs([
        item for item in operations if item["type"] == "blur"
    ])
    graph: list[str] = []
    video_labels = []
    audio_labels = []
    for index, (segment_start, segment_end) in enumerate(segments):
        current = f"v{index}trim"
        graph.append(
            f"[0:v:0]trim=start={segment_start:.6f}:end={segment_end:.6f},"
            f"setpts=PTS-STARTPTS[{current}]"
        )
        grouped_blurs: dict[tuple, tuple[dict, list[tuple[float, float]]]] = {}
        for blur in blurs:
            if not _overlap(
                segment_start, segment_end,
                float(blur["start_seconds"]), float(blur["end_seconds"]),
            ):
                continue
            key = _blur_group_key(blur)
            if key not in grouped_blurs:
                grouped_blurs[key] = (blur, [])
            grouped_blurs[key][1].append((
                max(segment_start, float(blur["start_seconds"])) - segment_start,
                min(segment_end, float(blur["end_seconds"])) - segment_start,
            ))
        for blur_index, (blur, ranges) in enumerate(grouped_blurs.values()):
            region = blur.get("region_source_pixels")
            sigma, feather = blur_parameters(blur)
            feather_mode = blur_feather_mode(blur)
            enabled_ranges = _merge_time_ranges(ranges)
            expression = "+".join(
                f"between(t,{start:.6f},{end:.6f})"
                for start, end in enabled_ranges
            )
            enable = f"enable='{expression}'"
            if region == "FULL_FRAME":
                output = f"v{index}blur{blur_index}"
                graph.append(
                    f"[{current}]gblur=sigma={sigma:g}:{enable}"
                    f"[{output}]"
                )
                current = output
            elif isinstance(region, dict):
                output = f"v{index}blur{blur_index}"
                graph.extend(regional_blur_filters(
                    input_label=current, output_label=output,
                    prefix=f"v{index}region{blur_index}", region=region,
                    sigma=sigma, feather=feather, feather_mode=feather_mode,
                    enable=enable,
                ))
                current = output
            else:
                raise ValueError(f"BLUR operation has no valid region: {blur.get('id')}")
        video_label = f"v{index}"
        graph.append(f"[{current}]setpts=PTS-STARTPTS[{video_label}]")
        video_labels.append(f"[{video_label}]")
        if has_audio:
            audio_label = f"a{index}"
            graph.append(
                f"[0:a:0]atrim=start={segment_start:.6f}:end={segment_end:.6f},"
                f"asetpts=PTS-STARTPTS[{audio_label}]"
            )
            audio_labels.append(f"[{audio_label}]")
    if len(segments) == 1:
        graph.append(f"{video_labels[0]}null[vout]")
        if has_audio:
            graph.append(f"{audio_labels[0]}anull[aout]")
    elif has_audio:
        inputs = "".join(
            value for pair in zip(video_labels, audio_labels) for value in pair
        )
        graph.append(f"{inputs}concat=n={len(segments)}:v=1:a=1[vout][aout]")
    else:
        graph.append(
            f"{''.join(video_labels)}concat=n={len(segments)}:v=1:a=0[vout]"
        )
    cut_seconds = sum(end - start for start, end in cuts)
    return ";".join(graph), duration - cut_seconds


def expected_output_duration(*, operations: list[dict], duration: float) -> float:
    cuts = _merge_cuts(operations, duration)
    return duration - sum(end - start for start, end in cuts)


def approve_previews(
    *, project_root: Path, plan_path: Path, preview_manifest_path: Path,
    actor: str = "user", allow_sampled: bool = False,
) -> tuple[dict, dict]:
    root = project_root.resolve(strict=True)
    plan_path = _inside((root / "work").resolve(strict=True), plan_path, "Edit plan path")
    manifest_path = _inside(
        (root / "previews").resolve(strict=True), preview_manifest_path,
        "Preview manifest path",
    )
    plan = _read_json(plan_path.resolve(strict=True))
    manifest = _read_json(manifest_path.resolve(strict=True))
    if plan.get("status") != "READY_FOR_PREVIEW":
        raise ValueError("Edit plan is not awaiting preview approval")
    if manifest.get("status") != "PREVIEW_REVIEW_REQUIRED":
        raise ValueError("Preview manifest is not awaiting approval")
    planned_ids = {item["id"] for item in plan.get("approved_operations", [])}
    preview_ids = {item["operation_id"] for item in manifest.get("previews", [])}
    preview_scope = manifest.get("preview_scope", "ALL")
    sampled_approval = preview_scope == "SAMPLED"
    if sampled_approval:
        if not allow_sampled:
            raise ValueError(
                "Sampled preview approval requires explicit allow_sampled authorization"
            )
        if not preview_ids or not preview_ids.issubset(planned_ids):
            raise ValueError("Sampled preview set is not a valid subset of the edit plan")
    elif planned_ids != preview_ids:
        raise ValueError("Preview set does not match the edit plan")
    approved_at = _now()
    manifest["status"] = "APPROVED"
    approval = {
        "actor": actor,
        "approved_at": approved_at,
        "scope": "SAMPLED" if sampled_approval else "ALL",
    }
    if sampled_approval:
        approval["sampled_operation_ids"] = sorted(preview_ids)
        approval["authorization"] = "explicit_sampled_preview_approval"
    manifest["approval"] = approval
    plan["status"] = "READY_FOR_FINAL_RENDER"
    plan["preview_manifest"] = manifest_path.resolve().relative_to(root).as_posix()
    plan["preview_approval"] = dict(approval)
    plan["final_export_requested"] = True
    plan["final_export_allowed"] = True
    plan["next_gate"] = "Render final output and validate size, duration, video and audio"
    _write_json(manifest_path, manifest)
    _write_json(plan_path, plan)
    return plan, manifest


def authorize_final_from_resolved_review(
    *, project_root: Path, plan_path: Path, actor: str = "local_user",
) -> dict:
    """Use the resolved scene-review page as the single final export gate."""
    root = project_root.resolve(strict=True)
    plan_path = _inside((root / "work").resolve(strict=True), plan_path, "Edit plan path")
    plan = _read_json(plan_path.resolve(strict=True))
    if plan.get("status") != "READY_FOR_PREVIEW":
        raise ValueError("Edit plan is not awaiting final review authorization")
    review_queue_value = plan.get("review_queue")
    if not review_queue_value:
        raise ValueError("Edit plan has no review queue provenance")
    queue_path = _inside(
        (root / "reports").resolve(strict=True), root / str(review_queue_value),
        "Review queue path",
    ).resolve(strict=True)
    queue = _read_json(queue_path)
    unresolved = [
        item["id"] for item in queue.get("items", [])
        if item.get("decision") in {None, "NEEDS_MORE_CONTEXT"}
    ]
    if unresolved or queue.get("status") != "READY_FOR_EDIT_PLAN":
        raise ValueError(
            f"Review queue is not complete: {len(unresolved)} items remain unresolved"
        )
    if queue.get("source", {}).get("sha256") != plan.get("source", {}).get("sha256"):
        raise ValueError("Review queue and edit plan source checksums do not match")
    approved_at = _now()
    plan["status"] = "READY_FOR_FINAL_RENDER"
    plan["preview_manifest"] = None
    plan["preview_approval"] = {
        "actor": actor,
        "approved_at": approved_at,
        "scope": "RESOLVED_REVIEW_QUEUE",
        "authorization": "explicit_finalize_button",
        "queue_updated_at": queue.get("updated_at"),
    }
    plan["final_export_requested"] = True
    plan["final_export_allowed"] = True
    plan["next_gate"] = "Render final output and validate size, duration, video and audio"
    _write_json(plan_path, plan)
    return plan


def _render_final_output_unlocked(
    *, project_root: Path, plan_path: Path, output_path: Path,
    ffmpeg_path: Path, ffprobe_path: Path,
    max_output_bytes: int | None = DEFAULT_MAX_OUTPUT_BYTES,
    target_output_bytes: int | None = DEFAULT_TARGET_OUTPUT_BYTES,
) -> dict:
    root = project_root.resolve(strict=True)
    plan_path = _inside((root / "work").resolve(strict=True), plan_path, "Edit plan path").resolve(strict=True)
    # Checked as given, before it is resolved: a file or a link there (even a
    # broken one) is refused, never followed, before FFmpeg runs.
    if os.path.lexists(output_path):
        raise FileExistsError(f"Output already exists: {output_path}")
    output_path = _inside((root / "output").resolve(strict=True), output_path, "Output path")
    ffmpeg_path = ffmpeg_path.resolve(strict=True)
    ffprobe_path = ffprobe_path.resolve(strict=True)
    size_limited = max_output_bytes is not None
    if size_limited:
        if (
            target_output_bytes is None
            or max_output_bytes <= 0
            or target_output_bytes <= 0
            or target_output_bytes >= max_output_bytes
        ):
            raise ValueError("Output size limits are invalid")
    elif target_output_bytes is not None:
        raise ValueError("Unlimited output must not define a target size")
    plan = _read_json(plan_path)
    if plan.get("status") != "READY_FOR_FINAL_RENDER" or not plan.get("final_export_allowed"):
        raise ValueError("Final render is not approved")
    source = Path(plan["source"]["path"]).resolve(strict=True)
    source_hash_before = _sha256(source)
    if source_hash_before != plan["source"].get("sha256"):
        raise ValueError("Source checksum changed after approval")
    source_probe = probe_video(ffprobe_path, source)
    duration = float(plan["source"]["duration_seconds"])
    if abs(duration_seconds(source_probe) - duration) > 0.1:
        raise ValueError("Source duration changed after approval")
    streams = source_probe.get("streams", [])
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if video_stream is None:
        raise ValueError("Source has no video stream")
    graph, expected_duration = build_final_filter_graph(
        operations=plan.get("approved_operations", []),
        duration=duration,
        has_audio=audio_stream is not None,
    )
    audio_bitrate = 384_000 if audio_stream is not None else 0
    overhead_fraction = 0.03
    video_maxrate = None
    video_target_bitrate = None
    if size_limited:
        assert target_output_bytes is not None
        budget_bits_per_second = int(
            target_output_bytes * 8 * (1 - overhead_fraction) / expected_duration
        )
        video_maxrate = budget_bits_per_second - audio_bitrate
        if video_maxrate < 300_000:
            raise ValueError(
                "Video is too long for the configured output size limit at the "
                "minimum safe bitrate"
            )
        source_video_bitrate = int(video_stream.get("bit_rate") or video_maxrate)
        video_target_bitrate = min(int(source_video_bitrate * 1.15), video_maxrate)
        estimated_output_bytes = target_output_bytes
    else:
        # CRF controls visual quality in unlimited mode.  Reserve conservatively
        # from source size while the global free-space policy still protects E:.
        estimated_output_bytes = max(source.stat().st_size, int(source.stat().st_size * 1.5))
    require_capacity(root, estimated_job_gb=max(1.0, estimated_output_bytes / 1024**3))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_name(output_path.stem + ".partial" + output_path.suffix)
    # A link here would make FFmpeg write wherever it points.
    if os.path.lexists(partial):
        raise FileExistsError(f"Partial output already exists: {partial}")
    progress_path = render_progress_path(root, output_path)
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.unlink(missing_ok=True)
    command = [
        str(ffmpeg_path), "-hide_banner", "-loglevel", "error",
        "-stats_period", "1", "-progress", str(progress_path), "-nostats",
        "-i", str(source),
        "-filter_complex", graph, "-map", "[vout]",
    ]
    if audio_stream is not None:
        command += ["-map", "[aout]"]
    command += [
        "-map_metadata", "0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
    ]
    if size_limited:
        assert video_maxrate is not None and video_target_bitrate is not None
        command += [
            "-maxrate", str(video_maxrate), "-bufsize", str(video_maxrate * 2),
            "-b:v", str(video_target_bitrate),
        ]
    command += ["-pix_fmt", "yuv420p"]
    if audio_stream is not None:
        command += ["-c:a", "aac", "-b:a", str(audio_bitrate)]
    command += ["-movflags", "+faststart", "-max_muxing_queue_size", "2048", str(partial)]
    started = time.perf_counter()
    result = subprocess.run(command, capture_output=True, text=True)
    elapsed = time.perf_counter() - started
    if result.returncode:
        partial.unlink(missing_ok=True)
        raise RuntimeError(result.stderr.strip() or "Final FFmpeg render failed")
    size = partial.stat().st_size
    if max_output_bytes is not None and size > max_output_bytes:
        partial.unlink(missing_ok=True)
        raise RuntimeError(
            f"Rendered output exceeded the configured hard size limit "
            f"({max_output_bytes} bytes): {size} bytes"
        )
    output_probe = probe_video(ffprobe_path, partial)
    actual_duration = duration_seconds(output_probe)
    if abs(actual_duration - expected_duration) > 0.25:
        partial.unlink(missing_ok=True)
        raise RuntimeError(
            f"Output duration mismatch: expected={expected_duration:.3f}, actual={actual_duration:.3f}"
        )
    output_streams = output_probe.get("streams", [])
    if not any(item.get("codec_type") == "video" for item in output_streams):
        partial.unlink(missing_ok=True)
        raise RuntimeError("Rendered output has no video stream")
    if audio_stream is not None and not any(
        item.get("codec_type") == "audio" for item in output_streams
    ):
        partial.unlink(missing_ok=True)
        raise RuntimeError("Rendered output lost the audio stream")
    decode_started = time.perf_counter()
    decode_command = [
        str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-i", str(partial),
        "-map", "0:v:0",
    ]
    if audio_stream is not None:
        decode_command += ["-map", "0:a:0"]
    decode_command += ["-f", "null", os.devnull]
    decode_result = subprocess.run(decode_command, capture_output=True, text=True)
    decode_elapsed = time.perf_counter() - decode_started
    if decode_result.returncode:
        partial.unlink(missing_ok=True)
        raise RuntimeError(
            decode_result.stderr.strip() or "Rendered output failed full decode validation"
        )
    # Both hashes before the file takes the export's name, so that a failure or
    # a stop here leaves only the partial (which the scheduler removes), never
    # an export without its manifest.
    output_hash = _sha256(partial)
    source_hash_after = _sha256(source)
    if source_hash_after != source_hash_before:
        partial.unlink(missing_ok=True)
        raise RuntimeError("Source checksum changed during render")
    _move_into_place(partial, output_path)
    manifest = {
        "schema_version": 1,
        "status": "COMPLETED",
        "created_at": _now(),
        "edit_plan": plan_path.relative_to(root).as_posix(),
        "source": {
            **plan["source"], "sha256_after_render": source_hash_after,
            "modified": False,
        },
        "output": {
            "path": output_path.relative_to(root).as_posix(),
            "bytes": size, "sha256": output_hash,
            "duration_seconds": round(actual_duration, 3),
            "expected_duration_seconds": round(expected_duration, 3),
            "maximum_bytes": max_output_bytes,
            "size_limit_mode": "limited" if size_limited else "unlimited",
            "within_size_limit": (
                size <= max_output_bytes if max_output_bytes is not None else None
            ),
            "within_3_5_gb_limit": size <= DEFAULT_MAX_OUTPUT_BYTES,
            "video_streams": sum(item.get("codec_type") == "video" for item in output_streams),
            "audio_streams": sum(item.get("codec_type") == "audio" for item in output_streams),
        },
        "encoding": {
            "video": (
                "libx264 CRF 20 with VBV size guard"
                if size_limited else "libx264 CRF 20 without output size cap"
            ),
            "video_target_bitrate": video_target_bitrate,
            "video_maxrate": video_maxrate,
            "audio": f"AAC {audio_bitrate} bps" if audio_stream is not None else None,
            "elapsed_seconds": round(elapsed, 3),
            "full_decode_validation_seconds": round(decode_elapsed, 3),
            "full_decode_validation_passed": True,
            "video_seconds_per_processing_second": round(expected_duration / elapsed, 3),
            "gpu_used": False,
        },
        "operations": plan.get("approved_operations", []),
    }
    manifest_path = output_path.with_suffix(output_path.suffix + ".manifest.json")
    _write_json(manifest_path, manifest)
    plan["status"] = "FINAL_RENDER_COMPLETED"
    plan["final_output"] = manifest["output"]
    plan["final_manifest"] = manifest_path.relative_to(root).as_posix()
    plan["next_gate"] = "Review final output before upload or archive"
    _write_json(plan_path, plan)
    preview_manifest_value = plan.get("preview_manifest")
    if preview_manifest_value:
        preview_manifest_path = _inside(
            (root / "previews").resolve(strict=True),
            root / str(preview_manifest_value),
            "Preview manifest path",
        ).resolve(strict=True)
        preview_manifest = _read_json(preview_manifest_path)
        safety = preview_manifest.setdefault("safety", {})
        safety["final_export_created"] = True
        safety["source_modified"] = False
        preview_manifest["final_output_manifest"] = manifest_path.relative_to(root).as_posix()
        _write_json(preview_manifest_path, preview_manifest)
    return manifest


def render_final_output(
    *, project_root: Path, plan_path: Path, output_path: Path,
    ffmpeg_path: Path, ffprobe_path: Path,
    max_output_bytes: int | None = DEFAULT_MAX_OUTPUT_BYTES,
    target_output_bytes: int | None = DEFAULT_TARGET_OUTPUT_BYTES,
) -> dict:
    """Serialize full renders across sessions to bound CPU, heat and disk I/O."""
    with project_resource_lock(project_root, "final-render"):
        return _render_final_output_unlocked(
            project_root=project_root, plan_path=plan_path, output_path=output_path,
            ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path,
            max_output_bytes=max_output_bytes,
            target_output_bytes=target_output_bytes,
        )
