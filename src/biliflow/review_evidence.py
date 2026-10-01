"""Review evidence for one queue item: a timestamped frame strip and video access.

Safety reports keep only the strongest JPEG per interval and no per-frame
scores, so a reviewer who sees one frame can miss the part of an interval that
actually needs action. This module rebuilds the evidence read-only:

* ``item_evidence`` resolves an item's ``#interval:N`` references inside the
  queue's own reports and chooses strip timestamps (context frames over the
  detected time, every known seed, always the strongest frame).
* ``ReviewFrameCache`` extracts one strip frame lazily from the read-only source
  with CPU FFmpeg into ``cache/review-frames`` (bounded by ``cleanup.py``).
  The cache is outside ``reports/`` so Visual AI Audit and brand memory never
  pick these frames up.
* ``parse_range`` / ``stream_file`` serve the source video with HTTP Range.

Nothing here writes to queues, reports, decisions or source videos.
"""
from __future__ import annotations

import json
import math
import os
import re
import secrets
import subprocess
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

CHUNK_BYTES = 1024 * 1024
FRAME_WIDTH = 640
FRAME_TIMEOUT_SECONDS = 30.0
MIN_CONTEXT_FRAMES = 8
MAX_CONTEXT_FRAMES = 16
SECONDS_PER_CONTEXT_FRAME = 2.0
MAX_STRIP_FRAMES = 24
SEED_EXCLUSION_SECONDS = 0.4
TIME_TOLERANCE_SECONDS = 0.001
CACHE_RELATIVE = "cache/review-frames"
VIDEO_MIME_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}

_RANGE = re.compile(r"bytes=(\d*)-(\d*)")
_REF = re.compile(r"^(?P<report>[^#]+)#interval:(?P<index>\d+)$")
_REF_OVERLAP_TOLERANCE = 1.0  # seconds; a referenced interval must touch its item
_THUMBNAIL_SECONDS = re.compile(r"-(\d+(\.\d+)?)s\.(jpg|png)$", re.IGNORECASE)
_SHA256 = re.compile(r"^[0-9a-f]{16,64}$")
_EPSILON = 1e-6


class ReviewMediaError(Exception):
    """A review media request that must fail with an explicit HTTP status."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = int(status)


# ---------------------------------------------------------------------------
# Read-only JSON cache (reports are large and immutable once a scan finished).

_JSON_CACHE: "OrderedDict[str, tuple[int, int, Any]]" = OrderedDict()
_JSON_CACHE_LOCK = threading.Lock()
_JSON_CACHE_LIMIT = 16


def read_json_cached(path: Path) -> Any:
    """Parse ``path`` once per (mtime, size); callers must not mutate the result."""
    resolved = Path(path).resolve(strict=True)
    stat = resolved.stat()
    key = str(resolved).casefold() if os.name == "nt" else str(resolved)
    with _JSON_CACHE_LOCK:
        cached = _JSON_CACHE.get(key)
        if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            _JSON_CACHE.move_to_end(key)
            return cached[2]
    with resolved.open("rb") as handle:  # read-only
        payload = json.loads(handle.read().decode("utf-8"))
    with _JSON_CACHE_LOCK:
        _JSON_CACHE[key] = (stat.st_mtime_ns, stat.st_size, payload)
        _JSON_CACHE.move_to_end(key)
        while len(_JSON_CACHE) > _JSON_CACHE_LIMIT:
            _JSON_CACHE.popitem(last=False)
    return payload


# ---------------------------------------------------------------------------
# Evidence model


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _ms(value: float) -> int:
    return int(round(value * 1000))


def _seconds(value: float) -> float:
    return round(value, 3)


def find_item(queue: dict[str, Any], item_id: str) -> dict[str, Any]:
    for key in ("items", "advisory_items"):
        for item in queue.get(key) or []:
            if str(item.get("id")) == str(item_id):
                return item
    raise KeyError(f"Unknown review item: {item_id}")


def _normal_reference(value: str) -> str:
    return value.replace("\\", "/").strip()


def _resolve_report(root: Path, queue: dict[str, Any], report: str) -> Path | None:
    """Only a report listed in the queue and located under reports/ is readable."""
    normal = _normal_reference(report)
    listed = {_normal_reference(str(value)) for value in queue.get("reports") or []}
    if normal not in listed:
        return None
    parts = normal.split("/")
    if ".." in parts or normal.startswith("/") or ":" in normal:
        return None
    reports_root = (root / "reports").resolve()
    try:
        target = (root / normal).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if reports_root not in target.parents or not target.is_file():
        return None
    return target


def _thumbnail_seconds(value: Any) -> float | None:
    if not value:
        return None
    match = _THUMBNAIL_SECONDS.search(str(value))
    return float(match.group(1)) if match else None


def _detector_samples(interval: dict[str, Any], threshold: float | None) -> list[dict[str, float | None]] | None:
    """Phase B per-frame hits; ``None`` when the report predates them."""
    raw = interval.get("detector_samples")
    if not isinstance(raw, list):
        return None
    samples: list[dict[str, float | None]] = []
    for value in raw:
        if isinstance(value, dict):
            seconds = _number(
                value.get("timestamp_seconds", value.get("t", value.get("seconds")))
            )
            score = _number(value.get("score"))
        elif isinstance(value, (list, tuple)) and value:
            seconds = _number(value[0])
            score = _number(value[1]) if len(value) > 1 else None
        else:
            continue
        if seconds is None or seconds < 0:
            continue
        if score is not None and threshold is not None and score < threshold:
            continue
        samples.append({"t": _seconds(seconds), "score": score})
    return samples


def _resolve_refs(root: Path, queue: dict[str, Any], item: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    resolved: list[dict[str, Any]] = []
    ignored = 0
    for value in item.get("source_candidate_refs") or []:
        match = _REF.match(_normal_reference(str(value)))
        if not match:
            ignored += 1
            continue
        report_path = _resolve_report(root, queue, match.group("report"))
        if report_path is None:
            ignored += 1
            continue
        try:
            payload = read_json_cached(report_path)
        except (OSError, ValueError):
            ignored += 1
            continue
        intervals = payload.get("intervals") if isinstance(payload, dict) else None
        index = int(match.group("index"))
        if not isinstance(intervals, list) or index >= len(intervals) or not isinstance(intervals[index], dict):
            ignored += 1
            continue
        interval = intervals[index]
        start = _number(interval.get("start_seconds"))
        end = _number(interval.get("end_seconds"))
        if start is None or end is None:
            ignored += 1
            continue
        item_start, item_end = _number(item.get("start_seconds")), _number(item.get("end_seconds"))
        if item_start is not None and item_end is not None and (
                end < item_start - _REF_OVERLAP_TOLERANCE or start > item_end + _REF_OVERLAP_TOLERANCE):
            ignored += 1  # report rewritten after the queue was built: not this item's evidence
            continue
        sequence = interval.get("sequence_context") if isinstance(interval.get("sequence_context"), dict) else {}
        detector_start = _number(sequence.get("detector_start_seconds"))
        detector_end = _number(sequence.get("detector_end_seconds"))
        threshold = _number(payload.get("threshold"))
        resolved.append({
            "ref": _normal_reference(str(value)),
            "report": _normal_reference(match.group("report")),
            "interval": index,
            "start": _seconds(start),
            "end": _seconds(end),
            "threshold": threshold,
            "sample_fps": _number(payload.get("sample_fps")),
            "max_score": _number(interval.get("max_score")),
            "sample_count": int(_number(interval.get("sample_count")) or 0),
            "detector_start": _seconds(detector_start if detector_start is not None else start),
            "detector_end": _seconds(detector_end if detector_end is not None else end),
            "strongest_t": _thumbnail_seconds(interval.get("strongest_frame")),
            "detector_samples": _detector_samples(interval, threshold),
            "sequence_context": sequence or None,
            "shot_context": interval.get("shot_context") if isinstance(interval.get("shot_context"), dict) else None,
        })
    return resolved, ignored


def _union(windows: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for start, end in sorted(windows):
        if end < start:
            continue
        if merged and start <= merged[-1][1] + _EPSILON:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _snap(value: float, segment: tuple[float, float], fps: float | None) -> float:
    """Snap to the detector's 1/fps sample grid without leaving the segment."""
    start, end = segment
    if not fps or fps <= 0:
        return _seconds(value)
    snapped = round(value * fps) / fps
    if snapped < start - _EPSILON:
        snapped = math.ceil(start * fps - _EPSILON) / fps
    if snapped > end + _EPSILON:
        snapped = math.floor(end * fps + _EPSILON) / fps
    if snapped < start - _EPSILON or snapped > end + _EPSILON:
        return _seconds(value)  # segment shorter than one sample step
    return _seconds(snapped)


def context_times(segments: list[tuple[float, float]], fps: float | None) -> list[float]:
    """K midpoints of equal bins over the union of detected time (never in gaps)."""
    union = _union(segments)
    total = sum(end - start for start, end in union)
    if not union:
        return []
    if total <= _EPSILON:
        return [_seconds(union[0][0])]
    count = min(MAX_CONTEXT_FRAMES, max(MIN_CONTEXT_FRAMES, math.ceil(total / SECONDS_PER_CONTEXT_FRAME - _EPSILON)))
    width = total / count
    times: list[float] = []
    for index in range(count):
        offset = (index + 0.5) * width
        for start, end in union:
            length = end - start
            if offset <= length + _EPSILON:
                times.append(_snap(start + min(offset, length), (start, end), fps))
                break
            offset -= length
    unique: dict[int, float] = {}
    for value in times:
        unique.setdefault(_ms(value), value)
    return [unique[key] for key in sorted(unique)]


def _thin(seeds: list[dict[str, Any]], limit: int, strongest_t: float | None) -> list[dict[str, Any]]:
    """Keep ``limit`` evenly spaced seeds, always the first, last and strongest."""
    if len(seeds) <= limit:
        return list(seeds)
    mandatory = {0, len(seeds) - 1}
    if strongest_t is not None:
        mandatory.update(
            index for index, seed in enumerate(seeds)
            if abs(seed["t"] - strongest_t) <= TIME_TOLERANCE_SECONDS
        )
    others = [index for index in range(len(seeds)) if index not in mandatory]
    slots = max(0, limit - len(mandatory))
    chosen = set(mandatory)
    if slots and others:
        step = len(others) / slots
        chosen.update(others[min(len(others) - 1, int((slot + 0.5) * step))] for slot in range(slots))
    return [seeds[index] for index in sorted(chosen)]


def select_frames(
    *, segments: list[tuple[float, float]], fps: float | None,
    seeds: list[dict[str, Any]], strongest: dict[str, Any] | None,
    duration: float | None = None,
) -> list[dict[str, Any]]:
    """Strip timestamps: context frames, every known seed, always the strongest; at most 24."""
    context = context_times(segments, fps)
    strongest_t = strongest["t"] if strongest else None
    seed_list = sorted(
        {_ms(seed["t"]): seed for seed in seeds}.values(), key=lambda seed: seed["t"],
    )
    if strongest_t is not None:
        seed_list = [
            seed for seed in seed_list if abs(seed["t"] - strongest_t) > TIME_TOLERANCE_SECONDS
        ]

    def kept_context(anchors: list[float]) -> list[float]:
        return [
            value for value in context
            if all(abs(value - anchor) > SEED_EXCLUSION_SECONDS + _EPSILON for anchor in anchors)
        ]

    strongest_anchor = [strongest_t] if strongest_t is not None else []
    budget = MAX_STRIP_FRAMES - len(strongest_anchor)
    kept_seeds = seed_list
    while True:
        remaining_context = kept_context([seed["t"] for seed in kept_seeds] + strongest_anchor)
        overflow = len(remaining_context) + len(kept_seeds) - budget
        if overflow <= 0 or not kept_seeds:
            break
        target = max(0, len(kept_seeds) - overflow)
        thinned = _thin(seed_list, target, None)
        if len(thinned) >= len(kept_seeds):  # first/last cannot be dropped further
            remaining_context = remaining_context[: max(0, budget - len(thinned))]
            kept_seeds = thinned
            break
        kept_seeds = thinned

    frames: dict[int, dict[str, Any]] = {}
    for value in remaining_context:
        frames[_ms(value)] = {"t": _seconds(value), "kind": "context"}
    for seed in kept_seeds:
        entry: dict[str, Any] = {"t": _seconds(seed["t"]), "kind": "seed"}
        if seed.get("score") is not None:
            entry["score"] = round(float(seed["score"]), 6)
        frames[_ms(seed["t"])] = entry
    if strongest is not None:
        entry = {"t": _seconds(strongest_t), "kind": "strongest"}
        if strongest.get("score") is not None:
            entry["score"] = round(float(strongest["score"]), 6)
        frames[_ms(strongest_t)] = entry
    ordered = [frames[key] for key in sorted(frames)]
    if duration is not None:
        ordered = [frame for frame in ordered if 0 <= frame["t"] <= duration + _EPSILON]
    # At most 16 context + 2 seeds + strongest survive the loop above, so the cap holds.
    return ordered


def video_status(source_path: str | os.PathLike[str] | None) -> dict[str, Any]:
    """Whether the browser can be offered the source container (path not exposed)."""
    if not source_path:
        return {"available": False, "mime": None, "reason": "source_unknown"}
    path = Path(source_path)
    mime = VIDEO_MIME_TYPES.get(path.suffix.casefold())
    if mime is None:
        return {"available": False, "mime": None, "reason": "unsupported_container"}
    if not path.is_file():
        return {"available": False, "mime": mime, "reason": "source_missing"}
    return {"available": True, "mime": mime, "reason": None}


def item_evidence(root: Path, queue: dict[str, Any], item_id: str) -> dict[str, Any]:
    """Frame strip, seed summary and video availability for one queue item (read-only)."""
    root = Path(root).resolve()
    item = find_item(queue, item_id)
    start = _number(item.get("start_seconds")) or 0.0
    end = _number(item.get("end_seconds"))
    end = start if end is None or end < start else end
    detected = [
        (_number(value.get("start_seconds")), _number(value.get("end_seconds")))
        for value in item.get("detected_intervals") or []
        if isinstance(value, dict)
    ]
    segments = [
        (max(start, low), min(end, high))
        for low, high in detected
        if low is not None and high is not None and min(end, high) >= max(start, low)
    ] or [(start, end)]
    segments = _union(segments)

    refs, ignored = _resolve_refs(root, queue, item)
    strongest_ref = max(
        (ref for ref in refs if ref["strongest_t"] is not None),
        key=lambda ref: (ref["max_score"] if ref["max_score"] is not None else -1.0),
        default=None,
    )
    fps_ref = strongest_ref or (refs[0] if refs else None)
    fps = fps_ref["sample_fps"] if fps_ref else None
    threshold = fps_ref["threshold"] if fps_ref else None

    known = bool(refs) and all(ref["detector_samples"] is not None for ref in refs)
    samples: list[dict[str, Any]] | None = None
    if known:
        samples = sorted(
            {_ms(sample["t"]): sample for ref in refs for sample in ref["detector_samples"]}.values(),
            key=lambda sample: sample["t"],
        )
    strongest = (
        {"t": _seconds(strongest_ref["strongest_t"]), "score": strongest_ref["max_score"]}
        if strongest_ref else None
    )
    # Known seeds: every Phase B sample, or (legacy) each reference's own strongest frame.
    seed_points = list(samples or []) if known else [
        {"t": ref["strongest_t"], "score": ref["max_score"]}
        for ref in refs if ref["strongest_t"] is not None
    ]
    duration = _number((queue.get("source") or {}).get("duration_seconds"))
    frames = select_frames(
        segments=segments, fps=fps, seeds=seed_points, strongest=strongest, duration=duration,
    )

    windows = sorted(
        ({"start": ref["detector_start"], "end": ref["detector_end"],
          "count": (len(ref["detector_samples"]) if ref["detector_samples"] is not None else ref["sample_count"])}
         for ref in refs),
        key=lambda window: (window["start"], window["end"]),
    )
    extended: list[dict[str, float]] = []
    supporting = 0
    context_threshold = None
    maximum_extension = None
    applied = False
    for ref in refs:
        sequence = ref["sequence_context"] or {}
        if not sequence:
            continue
        applied = applied or bool(sequence.get("applied"))
        supporting += int(_number(sequence.get("supporting_sample_count")) or 0)
        context_threshold = _number(sequence.get("context_threshold")) if context_threshold is None else context_threshold
        maximum_extension = _number(sequence.get("maximum_extension_seconds")) if maximum_extension is None else maximum_extension
        if ref["start"] < ref["detector_start"] - _EPSILON:
            extended.append({"start": ref["start"], "end": ref["detector_start"]})
        if ref["end"] > ref["detector_end"] + _EPSILON:
            extended.append({"start": ref["detector_end"], "end": ref["end"]})
    return {
        "schema_version": 1,
        "item_id": str(item.get("id")),
        "category": str(item.get("category") or ""),
        "start": _seconds(start),
        "end": _seconds(end),
        "sample_fps": fps,
        "detected_intervals": [{"start": _seconds(low), "end": _seconds(high)} for low, high in segments],
        "frames": frames,
        "seeds": {
            "known": known,
            "count": len(samples) if known else sum(ref["sample_count"] for ref in refs),
            "threshold": threshold,
            "samples": samples,
            "windows": windows,
        },
        "strongest": strongest,
        "context": {
            "applied": applied,
            "threshold": context_threshold,
            "supporting_sample_count": supporting,
            "maximum_extension_seconds": maximum_extension,
            "extended": sorted(extended, key=lambda value: value["start"]),
            "shot_completion": any(ref["shot_context"] for ref in refs),
        },
        "refs": [
            {key: ref[key] for key in ("ref", "interval", "max_score", "sample_count", "detector_start", "detector_end", "strongest_t")}
            for ref in refs
        ],
        "ignored_ref_count": ignored,
        "video": video_status((queue.get("source") or {}).get("path")),
    }


def strip_time(evidence: dict[str, Any], seconds: Any) -> float:
    """Return the strip timestamp matching ``seconds`` (within 1 ms) or raise ValueError."""
    value = _number(seconds)
    if value is None:
        raise ValueError("Frame time must be a number")
    for frame in evidence.get("frames") or []:
        if abs(float(frame["t"]) - value) <= TIME_TOLERANCE_SECONDS + _EPSILON:
            return float(frame["t"])
    raise ValueError("Frame time is not part of this item's evidence strip")


# ---------------------------------------------------------------------------
# Lazy CPU frame extraction

_FFMPEG_SLOTS = threading.BoundedSemaphore(2)
_TARGET_LOCKS: dict[str, list] = {}  # target -> [lock, users]; dropped when unused
_TARGET_LOCKS_GUARD = threading.Lock()


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) | int(
        getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    )


class ReviewFrameCache:
    """One 640 px JPEG per (source, millisecond), extracted on demand with CPU FFmpeg."""

    def __init__(self, root: Path, ffmpeg: Path, *, timeout_seconds: float = FRAME_TIMEOUT_SECONDS):
        self.root = Path(root).resolve()
        self.base = self.root / CACHE_RELATIVE
        self.ffmpeg = Path(ffmpeg)
        self.timeout_seconds = float(timeout_seconds)

    def target(self, source_sha256: str, seconds: float) -> Path:
        digest = str(source_sha256 or "").casefold()
        if not _SHA256.match(digest):
            raise ValueError("Source sha256 is required for the frame cache")
        value = _number(seconds)
        if value is None or value < 0:
            raise ValueError("Frame time must be a non-negative number")
        return self.base / digest[:16] / f"{_ms(value):010d}-w{FRAME_WIDTH}.jpg"

    def command(self, source: Path, seconds: float, output: Path) -> list[str]:
        return [
            str(self.ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-ss", f"{seconds:.3f}", "-i", str(source),
            "-map", "0:v:0", "-frames:v", "1", "-an", "-sn", "-dn",
            "-vf", f"scale={FRAME_WIDTH}:-2", "-q:v", "5", "-threads", "2",
            str(output),
        ]

    def frame(self, source: Path, source_sha256: str, seconds: float) -> Path:
        target = self.target(source_sha256, seconds)
        key = str(target)
        with _TARGET_LOCKS_GUARD:
            entry = _TARGET_LOCKS.setdefault(key, [threading.Lock(), 0])
            entry[1] += 1
        try:
            return self._extract(source, seconds, target, entry[0])
        finally:
            with _TARGET_LOCKS_GUARD:
                entry[1] -= 1
                if entry[1] == 0 and _TARGET_LOCKS.get(key) is entry:
                    del _TARGET_LOCKS[key]

    def _extract(self, source: Path, seconds: float, target: Path, lock: threading.Lock) -> Path:
        with lock:
            if target.is_file() and target.stat().st_size > 0:
                return target
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.stem}.{secrets.token_hex(4)}.tmp.jpg")
            try:
                with _FFMPEG_SLOTS:
                    subprocess.run(
                        self.command(Path(source), _ms(float(seconds)) / 1000.0, temporary),
                        check=True, capture_output=True, stdin=subprocess.DEVNULL,
                        timeout=self.timeout_seconds, creationflags=_creation_flags(),
                    )
                if not temporary.is_file() or temporary.stat().st_size == 0:
                    raise RuntimeError("FFmpeg produced no frame")
                os.replace(temporary, target)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError("FFmpeg frame extraction timed out") from error
            except subprocess.CalledProcessError as error:
                detail = (error.stderr or b"").decode("utf-8", "replace").strip()[-300:]
                raise RuntimeError(f"FFmpeg frame extraction failed: {detail or error.returncode}") from error
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            return target


# ---------------------------------------------------------------------------
# HTTP Range streaming


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Inclusive byte range of a single-range request; None means the whole file. Raises on 416."""
    if not header:
        return None
    match = _RANGE.fullmatch(header.strip())
    if not match or match.group(1) == match.group(2) == "":
        raise ValueError("unsupported range")
    if match.group(1) == "":
        start, end = max(0, size - int(match.group(2))), size - 1
    else:
        start = int(match.group(1))
        end = min(int(match.group(2)), size - 1) if match.group(2) else size - 1
    if start >= size or start > end:
        raise ValueError("unsatisfiable range")
    return start, end


def stream_file(handler: Any, path: Path, mime: str, stop_event: threading.Event | None = None) -> int:
    """Send ``path`` read-only with 200/206/416; returns the number of body bytes written."""
    size = Path(path).stat().st_size
    try:
        wanted = parse_range(handler.headers.get("Range"), size)
    except ValueError:
        handler.send_response(416)
        handler.send_header("Content-Range", f"bytes */{size}")
        handler.send_header("Content-Length", "0")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.end_headers()
        return 0
    start, end = wanted or (0, size - 1)
    handler.send_response(206 if wanted else 200)
    handler.send_header("Content-Type", mime)
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Length", str(max(0, end - start + 1)))
    if wanted:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    written = 0
    try:
        with Path(path).open("rb") as source:
            source.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                if stop_event is not None and stop_event.is_set():
                    break
                chunk = source.read(min(CHUNK_BYTES, remaining))
                if not chunk:
                    break
                handler.wfile.write(chunk)
                written += len(chunk)
                remaining -= len(chunk)
    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, TimeoutError):
        pass  # the browser cancels range requests while seeking
    return written
