from __future__ import annotations

from biliflow.performance import ScanPerformance
from biliflow.frame_prefetch import FramePrefetch

import html
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import time
import unicodedata
from difflib import SequenceMatcher
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psutil
from PIL import Image, ImageDraw, ImageFont

from biliflow.probe import duration_seconds, probe_video
from biliflow.storage import require_capacity


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


BLUR_ABSENCE_CONFIRM_SECONDS = 3.0
BLUR_POST_CONFIRM_TAIL_SECONDS = 0.5
LOW_CONFIDENCE_TOP_BANNER_THRESHOLD = 0.10


def _read_exact(stream, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            break
        chunks.extend(chunk)
    return bytes(chunks)


def _video_size(probe: dict) -> tuple[int, int]:
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "video":
            return int(stream["width"]), int(stream["height"])
    raise ValueError("Video stream is unavailable")


def _clock(seconds: float) -> str:
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _box_from_points(points: list[list[float]]) -> tuple[int, int, int, int]:
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return math.floor(min(xs)), math.floor(min(ys)), math.ceil(max(xs)), math.ceil(max(ys))


def _iou(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    first_area = max(0, first[2] - first[0]) * max(0, first[3] - first[1])
    second_area = max(0, second[2] - second[0]) * max(0, second[3] - second[1])
    union = first_area + second_area - intersection
    return intersection / union if union else 0.0


def _same_region(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> bool:
    if _iou(first, second) >= 0.20:
        return True
    first_center = ((first[0] + first[2]) / 2, (first[1] + first[3]) / 2)
    second_center = ((second[0] + second[2]) / 2, (second[1] + second[3]) / 2)
    first_width = max(1, first[2] - first[0])
    first_height = max(1, first[3] - first[1])
    return (
        abs(first_center[0] - second_center[0]) <= max(24, first_width * 0.45)
        and abs(first_center[1] - second_center[1]) <= max(18, first_height * 0.55)
    )


def _zone(box: tuple[int, int, int, int], width: int, height: int) -> str:
    center_x = (box[0] + box[2]) / 2 / width
    center_y = (box[1] + box[3]) / 2 / height
    if center_y >= 0.72 and 0.18 <= center_x <= 0.82:
        return "subtitle"
    vertical = "top" if center_y < 0.28 else "bottom" if center_y > 0.72 else "middle"
    horizontal = "left" if center_x < 0.30 else "right" if center_x > 0.70 else "center"
    return f"{vertical}-{horizontal}"


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _accept_detection(
    *, confidence: float, text: str, box: tuple[int, int, int, int],
    width: int, height: int, minimum_confidence: float,
) -> bool:
    compact = re.sub(r"[^0-9A-Za-zÀ-ỹĐđ]+", "", text)
    if len(compact) < 2:
        return False
    if confidence >= minimum_confidence:
        return True
    return (
        confidence >= LOW_CONFIDENCE_TOP_BANNER_THRESHOLD
        and _zone(box, width, height).startswith("top-")
        and len(compact) >= 12
    )


def _fold_vietnamese(value: str) -> str:
    value = value.lower().replace("đ", "d")
    return "".join(
        character
        for character in unicodedata.normalize("NFD", value)
        if unicodedata.category(character) != "Mn"
    )


def _normalized_ocr_text(value: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", _fold_vietnamese(value))


def _text_continuity(previous: str, current: str) -> bool:
    """Require textual continuity so changing subtitles do not become one long track."""
    first = _normalized_ocr_text(previous)
    second = _normalized_ocr_text(current)
    if not first or not second:
        return False
    if first == second:
        return True
    shorter, longer = sorted((first, second), key=len)
    if len(shorter) >= 4 and shorter in longer and len(shorter) / len(longer) >= 0.38:
        return True
    ratio = SequenceMatcher(None, first, second, autojunk=False).ratio()
    first_grams = {first[index:index + 3] for index in range(max(0, len(first) - 2))}
    second_grams = {second[index:index + 3] for index in range(max(0, len(second) - 2))}
    overlap = (
        len(first_grams & second_grams) / min(len(first_grams), len(second_grams))
        if first_grams and second_grams
        else 0.0
    )
    return ratio >= 0.48 or overlap >= 0.42


@dataclass
class Track:
    track_id: int
    first_seen: float
    last_seen: float
    last_box: tuple[int, int, int, int]
    zone: str
    observations: list[dict] = field(default_factory=list)
    best_frame_jpeg: bytes | None = None
    best_confidence: float = 0.0

    def add(self, detection: dict, frame: Image.Image) -> None:
        self.last_seen = detection["timestamp_seconds"]
        self.last_box = tuple(detection["box"])
        self.observations.append(detection)
        compact = re.sub(r"[^0-9A-Za-zÀ-ỹĐđ]+", "", detection["text"])
        long_top_banner = self.zone.startswith("top-") and len(compact) >= 12
        should_keep_preview = bool(detection["text"])
        if detection["confidence"] > self.best_confidence:
            self.best_confidence = detection["confidence"]
            if should_keep_preview:
                preview = frame.copy()
                output = io.BytesIO()
                preview.save(output, format="JPEG", quality=68, optimize=True)
                preview.close()
                self.best_frame_jpeg = output.getvalue()


def _summarize_track(
    track: Track, sample_every: float, observed_through_seconds: float | None = None
) -> dict:
    boxes = [observation["box"] for observation in track.observations]
    union_box = [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]
    texts = []
    for observation in track.observations:
        text = observation["text"]
        if text and text not in texts:
            texts.append(text)
    observed_span = max(0.0, track.last_seen - track.first_seen)
    first_absent_frame = track.last_seen + sample_every
    confirmed_absence = max(
        0.0,
        (observed_through_seconds - first_absent_frame)
        if observed_through_seconds is not None
        else 0.0,
    )
    endpoint_confirmed = confirmed_absence >= BLUR_ABSENCE_CONFIRM_SECONDS
    persistent = len(track.observations) >= 3 and observed_span >= sample_every * 2
    corner_or_side = track.zone not in {"middle-center", "top-center", "subtitle"}
    review_priority = (
        "high"
        if persistent and corner_or_side
        else "medium"
        if persistent
        else "low"
    )
    reason = (
        "Chữ lặp lại tại cùng vùng ở cạnh/góc khung hình"
        if review_priority == "high"
        else "Chữ lặp lại nhưng có thể thuộc nội dung phim"
        if persistent
        else "Chữ chỉ xuất hiện ngắn; cần người dùng kiểm tra"
    )
    return {
        "track_id": track.track_id,
        "start_seconds": round(track.first_seen, 3),
        "end_seconds": round(track.last_seen + sample_every, 3),
        "recommended_blur_end_seconds": round(
            first_absent_frame
            + BLUR_POST_CONFIRM_TAIL_SECONDS
            if endpoint_confirmed
            else first_absent_frame + BLUR_ABSENCE_CONFIRM_SECONDS,
            3,
        ),
        "endpoint_confirmed": endpoint_confirmed,
        "confirmed_absence_seconds": round(confirmed_absence, 3),
        "post_confirm_tail_seconds": BLUR_POST_CONFIRM_TAIL_SECONDS,
        "observations": len(track.observations),
        "zone": track.zone,
        "persistent": persistent,
        "review_priority": review_priority,
        "reason": reason,
        "max_confidence": round(track.best_confidence, 6),
        "union_box": union_box,
        "sample_text": texts[:8],
    }


def _box_overlap_over_smaller(first: list[int], second: list[int]) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    first_area = max(1, first[2] - first[0]) * max(1, first[3] - first[1])
    second_area = max(1, second[2] - second[0]) * max(1, second[3] - second[1])
    return intersection / min(first_area, second_area)


def _track_texts(track: dict) -> list[str]:
    return [
        str(value) for value in track.get("sample_text", [])
        if len(_normalized_ocr_text(str(value))) >= 6
    ]


def _consensus_overlay_box(boxes: list[list[int]]) -> list[int]:
    """Return the stable OCR footprint without one wide read expanding it.

    OCR occasionally joins a fixed corner watermark with nearby scene text.
    A plain min/max union then turns that one observation into a full-film blur
    strip.  Repeated overlays provide enough observations to keep the dominant
    size and position while retaining small localization variations.
    """
    if not boxes:
        raise ValueError("boxes cannot be empty")
    normalized = [[int(value) for value in box] for box in boxes]
    if len(normalized) < 4:
        return [
            min(box[0] for box in normalized), min(box[1] for box in normalized),
            max(box[2] for box in normalized), max(box[3] for box in normalized),
        ]
    widths = np.asarray([max(1, box[2] - box[0]) for box in normalized])
    heights = np.asarray([max(1, box[3] - box[1]) for box in normalized])
    median_width = float(np.median(widths))
    median_height = float(np.median(heights))
    median_box = [
        round(float(np.median([box[index] for box in normalized])))
        for index in range(4)
    ]
    inliers = [
        box for box in normalized
        if 0.55 * median_width <= box[2] - box[0] <= 1.80 * median_width
        and 0.55 * median_height <= box[3] - box[1] <= 1.80 * median_height
        and _box_overlap_over_smaller(box, median_box) >= 0.70
    ]
    if len(inliers) < max(3, math.ceil(len(normalized) * 0.55)):
        inliers = normalized
    return [
        min(box[0] for box in inliers), min(box[1] for box in inliers),
        max(box[2] for box in inliers), max(box[3] for box in inliers),
    ]


def _promote_repeated_corner_overlays(
    summaries: list[dict], video_duration: float,
) -> list[dict]:
    """Join intermittent OCR hits for a fixed corner overlay.

    Small logos and title marks are often readable only on some backgrounds.
    This pass promotes a review suggestion only when the same text family and
    region recur across a substantial part of the video. It never authorizes
    an edit.
    """
    corner_zones = {"top-left", "top-right", "bottom-left", "bottom-right"}
    candidates = [
        dict(track) for track in summaries
        if track.get("zone") in corner_zones
        and isinstance(track.get("union_box"), list)
        and len(track["union_box"]) == 4
        and _track_texts(track)
    ]
    groups: list[list[dict]] = []
    for track in sorted(candidates, key=lambda item: float(item["start_seconds"])):
        texts = _track_texts(track)
        match = None
        for group in groups:
            if group[0].get("zone") != track.get("zone"):
                continue
            region_matches = any(
                _box_overlap_over_smaller(
                    [int(value) for value in member["union_box"]],
                    [int(value) for value in track["union_box"]],
                ) >= 0.55
                for member in group
            )
            text_matches = any(
                _text_continuity(first, second)
                for member in group
                for first in _track_texts(member)
                for second in texts
            )
            if region_matches and text_matches:
                match = group
                break
        if match is None:
            groups.append([track])
        else:
            match.append(track)

    promoted_ids: set[int] = set()
    promoted: list[dict] = []
    minimum_span = max(12.0, min(60.0, video_duration * 0.20))
    for group in groups:
        span = max(float(item["end_seconds"]) for item in group) - min(
            float(item["start_seconds"]) for item in group
        )
        if len(group) < 4 or span < minimum_span:
            continue
        strongest = max(group, key=lambda item: float(item.get("max_confidence") or 0.0))
        merged = dict(strongest)
        boxes = [[int(value) for value in item["union_box"]] for item in group]
        ad_score = max(
            float(item.get("ad_probability") or 0.0)
            for item in group
        )
        brand_routed = any(
            item.get("routing") in {"REVIEW_AD_LIKELY", "REVIEW_POLICY_OVERRIDE"}
            for item in group
        )
        review_as_brand = brand_routed or ad_score >= 0.45
        merged.update({
            "start_seconds": round(min(float(item["start_seconds"]) for item in group), 3),
            "end_seconds": round(max(float(item["end_seconds"]) for item in group), 3),
            "recommended_blur_end_seconds": round(max(
                float(item.get("recommended_blur_end_seconds", item["end_seconds"]))
                for item in group
            ), 3),
            "union_box": _consensus_overlay_box(boxes),
            "sample_text": list(dict.fromkeys(
                text for item in group for text in _track_texts(item)
            ))[:8],
            "persistent": True,
            "review_priority": "high" if review_as_brand else "low",
            "review_candidate": review_as_brand,
            "routing": (
                "REVIEW_PERSISTENT_OVERLAY"
                if review_as_brand else "LIKELY_TITLE_OVERLAY"
            ),
            "candidate_type": (
                "persistent_overlay" if review_as_brand else "title_overlay"
            ),
            "suggested_decision": "BLUR" if review_as_brand else None,
            "reason": (
                "Chữ/logo cố định có thêm bằng chứng quảng cáo/thương hiệu; "
                "đề xuất làm mờ vùng và chờ người dùng duyệt"
                if review_as_brand
                else "Chữ cố định lặp lại nhưng điểm quảng cáo thấp; giữ như tiêu đề phim"
            ),
            "grouped_track_ids": [int(item["track_id"]) for item in group],
            "repeated_overlay_ad_score": round(ad_score, 6),
            "automatic_edit": False,
        })
        promoted.append(merged)
        promoted_ids.update(int(item["track_id"]) for item in group)

    if not promoted:
        return summaries
    return [
        track for track in summaries if int(track["track_id"]) not in promoted_ids
    ] + promoted


def _annotate(image: Image.Image, track: dict) -> Image.Image:
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    left, top, right, bottom = track["union_box"]
    colour = "#ff3b30" if track["review_priority"] == "high" else "#ffcc00"
    line_width = max(3, round(image.width / 320))
    draw.rectangle((left, top, right, bottom), outline=colour, width=line_width)
    label = f"REVIEW {track['track_id']} | {track['zone']}"
    font = ImageFont.load_default(size=max(12, round(image.width / 65)))
    label_box = draw.textbbox((left, max(0, top - 24)), label, font=font)
    draw.rectangle(label_box, fill="#111111")
    draw.text((left, max(0, top - 24)), label, fill=colour, font=font)
    return annotated


def _write_text_report(report_dir: Path, payload: dict) -> None:
    (report_dir / "text-scan.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    rows = []
    for track in payload["tracks"]:
        preview = track.get("preview")
        image = f'<img src="{html.escape(preview)}" loading="lazy">' if preview else ""
        sample_text = " / ".join(track["sample_text"]) or "(không đọc rõ)"
        semantic_scores = track.get("semantic_scores") or {}
        semantic = (
            f"{track.get('routing', 'OCR_ONLY')}"
            f"<br>ad={float(semantic_scores.get('advertisement', 0)):.3f}"
        )
        rows.append(
            "<tr>"
            f"<td>{track['track_id']}</td>"
            f"<td>{_clock(track['start_seconds'])}–{_clock(track['end_seconds'])}</td>"
            f"<td>{html.escape(track['zone'])}</td>"
            f"<td>{html.escape(track['review_priority'])}</td>"
            f"<td>{semantic}</td>"
            f"<td>{track['observations']}</td>"
            f"<td>{html.escape(sample_text)}</td>"
            f"<td>{image}</td>"
            "</tr>"
        )
    document = f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><title>BiliFlow text review</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;max-width:1280px;margin:32px auto;padding:0 20px;background:#111;color:#eee}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #444;padding:9px;text-align:left;vertical-align:top}}
th{{background:#252525;position:sticky;top:0}}img{{max-width:360px;max-height:210px}}code{{color:#b7e1ff}}
.warning{{padding:12px;background:#3b2d10;border-left:4px solid #e0a526}}
</style></head><body>
<h1>BiliFlow — rà soát chữ/quảng cáo</h1>
<p class="warning">Đây là danh sách để review. Hệ thống chưa tự blur vì chữ trong phim, phụ đề và quảng cáo có thể giống nhau.</p>
<p>Video: <code>{html.escape(payload['input'])}</code></p>
<p>Đã quét {payload['frames_scanned']} frame, mỗi {payload['sample_every_seconds']} giây; có {len(payload['tracks'])} vùng cần xem.</p>
<table><thead><tr><th>ID</th><th>Khoảng thời gian</th><th>Vùng</th><th>Ưu tiên</th><th>Phân loại</th><th>Số lần thấy</th><th>Chữ mẫu</th><th>Preview</th></tr></thead>
<tbody>{''.join(rows) if rows else '<tr><td colspan="8">Không phát hiện vùng chữ đạt ngưỡng.</td></tr>'}</tbody></table>
</body></html>"""
    (report_dir / "text-review.html").write_text(document, encoding="utf-8")


def scan_text(
    *,
    project_root: Path,
    input_path: Path,
    report_dir: Path,
    model_dir: Path,
    ffmpeg_path: Path,
    ffprobe_path: Path,
    sample_every: float = 3.0,
    analysis_width: int = 960,
    minimum_confidence: float = 0.35,
    max_report_tracks: int = 250,
    start_seconds: float = 0.0,
    duration_seconds_limit: float | None = None,
    languages: tuple[str, ...] = ("vi", "en"),
    device_name: str = "cuda",
    semantic_model_dir: Path | None = None,
    policy_path: Path | None = None,
    semantic_seed_path: Path | None = None,
    semantic_classifier=None,
    reader=None,
    prefetch_frames: int = 0,
) -> dict:
    performance = ScanPerformance()
    if not isinstance(prefetch_frames, int) or not 0 <= prefetch_frames <= 4:
        raise ValueError("prefetch_frames must be an integer from 0 to 4")
    input_path = input_path.resolve(strict=True)
    project_root = project_root.resolve(strict=True)
    report_dir = report_dir.resolve()
    model_dir = model_dir.resolve()
    ffmpeg_path = ffmpeg_path.resolve(strict=True)
    ffprobe_path = ffprobe_path.resolve(strict=True)
    reports_root = (project_root / "reports").resolve(strict=True)
    if report_dir != reports_root and reports_root not in report_dir.parents:
        raise ValueError(f"Report directory must stay inside {reports_root}")
    if sample_every <= 0 or analysis_width < 320:
        raise ValueError("sample_every must be positive and analysis_width must be at least 320")
    if not 0 <= minimum_confidence <= 1:
        raise ValueError("minimum_confidence must be between 0 and 1")
    if max_report_tracks <= 0:
        raise ValueError("max_report_tracks must be positive")
    if start_seconds < 0 or (duration_seconds_limit is not None and duration_seconds_limit <= 0):
        raise ValueError("start_seconds must be non-negative and duration_seconds must be positive")

    require_capacity(project_root, estimated_job_gb=2.0)
    probe = probe_video(ffprobe_path, input_path)
    video_duration = duration_seconds(probe)
    if start_seconds >= video_duration:
        raise ValueError("start_seconds must be inside the video")
    scan_duration = min(
        video_duration - start_seconds,
        duration_seconds_limit if duration_seconds_limit is not None else video_duration,
    )
    source_width, source_height = _video_size(probe)
    analysis_height = max(2, round(source_height * analysis_width / source_width / 2) * 2)

    with performance.measure('model_load'):
        if reader is None:
            try:
                import easyocr
            except ImportError as exc:
                raise RuntimeError("EasyOCR is not installed in the project environment") from exc
            model_dir.mkdir(parents=True, exist_ok=True)
            if not any(model_dir.glob("*.pth")):
                raise RuntimeError(
                    f"OCR model is missing in {model_dir}. Model download must be approved before scanning."
                )
            reader = easyocr.Reader(
                list(languages),
                gpu=device_name == "cuda",
                model_storage_directory=str(model_dir),
                user_network_directory=str(model_dir),
                download_enabled=False,
            )

    fps = 1.0 / sample_every
    frame_bytes = analysis_width * analysis_height * 3
    command = [
        str(ffmpeg_path), "-hide_banner", "-loglevel", "error",
        "-ss", str(start_seconds), "-i", str(input_path), "-t", str(scan_duration),
        "-vf", f"fps={fps},scale={analysis_width}:{analysis_height}:flags=bilinear",
        "-an", "-sn", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]
    report_dir.mkdir(parents=True, exist_ok=True)
    previews_dir = report_dir / "text-previews"
    previews_dir.mkdir(parents=True, exist_ok=True)

    process = psutil.Process(os.getpid())
    peak_rss = process.memory_info().rss
    started = time.perf_counter()
    frames_scanned = 0
    next_track_id = 1
    active_tracks: list[Track] = []
    all_tracks: list[Track] = []
    ffmpeg = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    frame_reader = FramePrefetch(ffmpeg, frame_bytes, _read_exact, depth=prefetch_frames)
    try:
        assert ffmpeg.stdout is not None
        frame_reader.__enter__()
        while True:
            data = performance.call('frame_pipe_wait', frame_reader.read)
            if not data:
                break
            if len(data) != frame_bytes:
                raise RuntimeError(f"Incomplete raw frame: {len(data)} of {frame_bytes} bytes")
            timestamp = start_seconds + frames_scanned * sample_every
            frame_array = np.frombuffer(data, dtype=np.uint8).reshape(
                (analysis_height, analysis_width, 3)
            )
            frame_image = Image.fromarray(frame_array)
            raw_detections = performance.call('model_step', reader.readtext,
                frame_array,
                detail=1,
                paragraph=False,
                batch_size=1,
                workers=0,
                decoder="greedy",
            )
            with performance.measure('tracking'):
                detections = []
                for points, text, confidence in raw_detections:
                    confidence = float(confidence)
                    box = _box_from_points(points)
                    if box[2] - box[0] < 8 or box[3] - box[1] < 6:
                        continue
                    cleaned_text = _clean_text(str(text))
                    if not _accept_detection(
                        confidence=confidence,
                        text=cleaned_text,
                        box=box,
                        width=analysis_width,
                        height=analysis_height,
                        minimum_confidence=minimum_confidence,
                    ):
                        continue
                    detections.append(
                        {
                            "timestamp_seconds": round(timestamp, 3),
                            "box": list(box),
                            "text": cleaned_text,
                            "confidence": confidence,
                            "zone": _zone(box, analysis_width, analysis_height),
                        }
                    )

                still_active = []
                for track in active_tracks:
                    if timestamp - track.last_seen <= sample_every * 2.5:
                        still_active.append(track)
                active_tracks = still_active
                used_tracks: set[int] = set()
                for detection in sorted(detections, key=lambda item: item["confidence"], reverse=True):
                    candidates = [
                        track
                        for track in active_tracks
                        if track.track_id not in used_tracks
                        and track.zone == detection["zone"]
                        and _same_region(track.last_box, tuple(detection["box"]))
                        and _text_continuity(
                            str(track.observations[-1]["text"]), str(detection["text"])
                        )
                    ]
                    if candidates:
                        track = max(candidates, key=lambda item: _iou(item.last_box, tuple(detection["box"])))
                    else:
                        track = Track(
                            track_id=next_track_id,
                            first_seen=timestamp,
                            last_seen=timestamp,
                            last_box=tuple(detection["box"]),
                            zone=detection["zone"],
                        )
                        next_track_id += 1
                        active_tracks.append(track)
                        all_tracks.append(track)
                    track.add(detection, frame_image)
                    used_tracks.add(track.track_id)

            frame_image.close()
            frames_scanned += 1
            if frames_scanned % 100 == 0:
                completed_video_seconds = min(scan_duration, frames_scanned * sample_every)
                percent = completed_video_seconds / scan_duration * 100 if scan_duration else 100
                print(
                    f"OCR progress: {percent:5.1f}% ({frames_scanned} frames)",
                    file=sys.stderr,
                    flush=True,
                )
            peak_rss = max(peak_rss, process.memory_info().rss)

        return_code = ffmpeg.wait()
        if return_code != 0:
            stderr = ffmpeg.stderr.read().decode("utf-8", errors="replace") if ffmpeg.stderr else ""
            raise RuntimeError(f"FFmpeg failed with exit code {return_code}: {stderr[-2000:]}")
    except Exception:
        if ffmpeg.poll() is None:
            ffmpeg.terminate()
            ffmpeg.wait(timeout=10)
        raise
    finally:
        frame_reader.__exit__(None, None, None)

    summaries = []
    tracks_by_id = {}
    for track in all_tracks:
        summary = _summarize_track(track, sample_every, start_seconds + scan_duration)
        summaries.append(summary)
        tracks_by_id[track.track_id] = track

    semantic_routing = False
    semantic_device = None
    with performance.measure('text_semantics'):
        if semantic_classifier is not None or semantic_model_dir is not None:
            from biliflow.text_semantics import (
                LocalEmbeddingTextClassifier,
                _representative_text,
                classify_text_track,
                load_text_policy,
            )

            if policy_path is None:
                raise ValueError("policy_path is required when semantic routing is enabled")
            policy = load_text_policy(policy_path)
            if semantic_classifier is None:
                if semantic_seed_path is None:
                    raise ValueError("semantic_seed_path is required for semantic routing")
                classifier = LocalEmbeddingTextClassifier(
                    semantic_model_dir, semantic_seed_path, device_name
                )
            else:
                classifier = semantic_classifier
            texts = [_representative_text(summary) for summary in summaries]
            score_rows = (
                classifier.classify_many(texts)
                if hasattr(classifier, "classify_many")
                else [classifier(text) for text in texts]
            )
            summaries = [
                classify_text_track(
                    summary,
                    semantic_scores=scores,
                    analysis_size=[analysis_width, analysis_height],
                    video_duration=video_duration,
                    policy=policy,
                )
                for summary, scores in zip(summaries, score_rows, strict=True)
            ]
            semantic_routing = True
            semantic_device = getattr(classifier, "device", device_name)

    summaries = _promote_repeated_corner_overlays(summaries, video_duration)

    if not semantic_routing:
        summaries = [
            summary for summary in summaries
            if summary["persistent"] or summary["max_confidence"] >= 0.65
        ]

    priority_order = {"high": 0, "medium": 1, "low": 2}
    summaries.sort(key=lambda item: (
        not bool(item.get("review_candidate", True)),
        priority_order[item["review_priority"]],
        item["start_seconds"],
    ))
    tracks_before_limit = len(summaries)
    full_routing_counts = {
        route: sum(summary.get("routing") == route for summary in summaries)
        for route in sorted(
            {summary.get("routing") for summary in summaries if summary.get("routing")}
        )
    }
    review_candidates = [
        summary for summary in summaries if summary.get("review_candidate", True)
    ]
    reference_tracks = [
        summary for summary in summaries if not summary.get("review_candidate", True)
    ]
    summaries = review_candidates + reference_tracks[
        :max(0, max_report_tracks - len(review_candidates))
    ]
    with performance.measure('preview_output'):
        for summary in summaries:
            track = tracks_by_id[summary["track_id"]]
            if track.best_frame_jpeg is not None:
                name = f"track-{track.track_id:05d}.jpg"
                with Image.open(io.BytesIO(track.best_frame_jpeg)) as stored_preview:
                    annotated = _annotate(stored_preview.convert("RGB"), summary)
                performance.call('preview_write', annotated.save, previews_dir / name, format="JPEG", quality=86, optimize=True)
                annotated.close()
                summary["preview"] = f"text-previews/{name}"

    elapsed = time.perf_counter() - started
    payload = {
        "schema_version": 2 if semantic_routing else 1,
        "status": "REVIEW_REQUIRED",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": str(input_path),
        "input_sha256": performance.call('source_hash', _sha256_file, input_path),
        "duration_seconds": video_duration,
        "scan_start_seconds": start_seconds,
        "scan_duration_seconds": scan_duration,
        "source_size": [source_width, source_height],
        "analysis_size": [analysis_width, analysis_height],
        "sample_every_seconds": sample_every,
        "minimum_confidence": minimum_confidence,
        "languages": list(languages),
        "device": device_name,
        "semantic_routing": semantic_routing,
        "semantic_device": semantic_device,
        "semantic_method": getattr(classifier, "method", None) if semantic_routing else None,
        "frames_scanned": frames_scanned,
        "tracks_before_limit": tracks_before_limit,
        "max_report_tracks": max_report_tracks,
        "tracks_omitted": max(0, tracks_before_limit - len(summaries)),
        "review_candidate_count": len(review_candidates),
        "routing_counts": full_routing_counts,
        "tracks": summaries,
        "metrics": {
            "frame_prefetch": {"requested_depth": prefetch_frames, "effective_depth": frame_reader.depth,
                               "buffer_budget_bytes": 32 * 1024**2},
            "performance": performance.snapshot(),
            "elapsed_seconds": round(elapsed, 3),
            "video_seconds_per_processing_second": round(scan_duration / elapsed, 3) if elapsed else None,
            "peak_process_ram_bytes": peak_rss,
        },
        "safety": {
            "automatic_blur": False,
            "automatic_delete": False,
            "note": "Only reviewed/approved tracks may be used to generate blur instructions.",
        },
    }
    _write_text_report(report_dir, payload)
    return payload
