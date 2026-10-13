"""Golden Set v2: stream-copied clips of the labelled segments (docs/handoff/G-golden-clips.md).

v2 replaces the full films of v1/v1.1 (jobs 37, 38, 39) with clips cut at keyframes without
re-encoding, so every decoded frame of a segment is bit-identical to the same frame decoded
from the film. Times in v2 are relative to each clip; each clip records its origin in its film.
Labels are copied from v1 and v1.1 with their times shifted; v1 and v1.1 are only read.

The scanners sample on a grid anchored at second 0 of their input (``fps=`` filters), so a clip
whose origin is a multiple of every sampling period of the profile sees exactly the frames of a
whole-film run. ``plan_clips`` prefers such an origin; the remaining offset of each clip is
recorded in the manifest.
"""
from __future__ import annotations

import json
import math
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable

from biliflow.golden_set import SCHEMA_VERSION, canonical_sha256, file_sha256, now_iso, validate_manifest

PRE_ROLL_SECONDS = Fraction(35)        # the 30 s logo boundary window + the 5 s opening card
POST_ROLL_SECONDS = Fraction(36)       # the 30 s logo boundary window + the 6 s ending card
MERGE_GAP_SECONDS = Fraction(180)      # an off-grid clip this close after an on-grid one joins it
ALIGN_SEARCH_SECONDS = Fraction(300)   # how far before the pre-roll an on-grid keyframe is looked for
PERIOD_FPS_KEYS = ("animation_sample_fps", "adult_sample_fps", "gore_sample_fps", "violence_sample_fps")
PERIOD_SECONDS_KEYS = ("text_sample_every", "logo_sample_every", "logo_boundary_sample_every")
SAMPLING_PROFILE = "careful"           # the profile of every Golden evaluation run
CLIP_TIME_FIELDS = ("start_seconds", "end_seconds")
SUGGESTION_TIME_FIELDS = ("start_seconds", "end_seconds", "item_start_seconds", "item_end_seconds")
POSITION_TOLERANCE = 0.0011            # cards round positions to 3 decimals; shifting back may move the last one
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# --- sampling grid --------------------------------------------------------------------------

def sampling_periods(profiles: dict[str, Any], profile: str = SAMPLING_PROFILE) -> tuple[Fraction, ...]:
    """Every frame-sampling period (seconds) a scan of ``profile`` uses."""
    config = profiles[profile]
    periods = {1 / Fraction(str(config[key])) for key in PERIOD_FPS_KEYS}
    periods |= {Fraction(str(config[key])) for key in PERIOD_SECONDS_KEYS}
    return tuple(sorted(periods))


def on_grid(seconds: Fraction, periods: Iterable[Fraction]) -> bool:
    return all(seconds % period == 0 for period in periods)


def grid_offsets(seconds: Fraction, periods: Iterable[Fraction]) -> dict[str, float]:
    """How far each sampling grid of a clip lies from the film's grid, keyed by period ("1/8")."""
    return {str(period): float(seconds % period) for period in periods}


# --- probing ---------------------------------------------------------------------------------

def _run(argv: list[Any]) -> str:
    result = subprocess.run([str(a) for a in argv], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", creationflags=_NO_WINDOW)
    if result.returncode:
        raise RuntimeError(f"{Path(str(argv[0])).name} failed ({result.returncode}): {result.stderr.strip()[:400]}")
    return result.stdout


class VideoIndex:
    """Time base and keyframe pts of a file's first video stream."""

    def __init__(self, time_base: Fraction, keyframes: Iterable[int]):
        self.time_base = Fraction(time_base)
        self.keyframes = tuple(sorted(set(int(k) for k in keyframes)))
        if not self.keyframes:
            raise ValueError("Video không có keyframe")

    def seconds(self, pts: int) -> Fraction:
        return pts * self.time_base

    def at_or_before(self, seconds: Fraction) -> list[int]:
        return [k for k in self.keyframes if self.seconds(k) <= seconds]

    def previous(self, pts: int) -> int:
        earlier = [k for k in self.keyframes if k < pts]
        return earlier[-1] if earlier else pts


def probe_keyframes(ffprobe: Path, path: Path) -> VideoIndex:
    stream = json.loads(_run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=time_base", "-of", "json", path]))["streams"][0]
    out = _run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts,flags",
                "-of", "csv=p=0", path])
    keys = []
    for line in out.split():
        parts = line.strip().split(",")
        if len(parts) >= 2 and "K" in parts[1] and parts[0].lstrip("-").isdigit():
            keys.append(int(parts[0]))
    return VideoIndex(Fraction(stream["time_base"]), keys)


def probe_clip(ffprobe: Path, path: Path) -> dict[str, Any]:
    data = json.loads(_run([ffprobe, "-v", "error", "-show_entries",
                            "format=start_time,duration:stream=index,codec_type,codec_name,width,height,"
                            "r_frame_rate,start_time", "-of", "json", path]))
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    return {"format_start_time": data["format"].get("start_time"), "video_start_time": video.get("start_time"),
            "duration_seconds": round(float(data["format"]["duration"]), 6), "width": int(video["width"]),
            "height": int(video["height"]), "fps": round(float(Fraction(video["r_frame_rate"])), 6),
            "streams": [s["codec_type"] for s in data["streams"]]}


# --- plan ------------------------------------------------------------------------------------

def choose_origin(index: VideoIndex, target: Fraction, periods: tuple[Fraction, ...]) -> int:
    """Latest keyframe at or before ``target``; an on-grid one within ALIGN_SEARCH_SECONDS wins."""
    before = index.at_or_before(target)
    if target <= 0 or not before:
        if index.keyframes[0] != 0:
            raise ValueError("Keyframe đầu tiên không ở pts 0")
        return 0
    aligned = [k for k in before if index.seconds(k) >= target - ALIGN_SEARCH_SECONDS
               and on_grid(index.seconds(k), periods)]
    return (aligned or before)[-1]


def choose_end(index: VideoIndex, target: Fraction, duration: Fraction) -> int | None:
    """First keyframe at or after ``target`` (the clip ends just before it); None = to the end of the film."""
    if target >= duration:
        return None
    after = [k for k in index.keyframes if index.seconds(k) >= target]
    return after[0] if after else None


def plan_clips(windows: list[dict[str, Any]], indexes: dict[str, VideoIndex], durations: dict[str, float],
               periods: tuple[Fraction, ...]) -> list[dict[str, Any]]:
    """Clips covering every window (a labelled segment, or an unscored benchmark window).

    A scored window gets PRE_ROLL/POST_ROLL margins; windows whose clips overlap or touch share
    one clip, and an off-grid clip within MERGE_GAP_SECONDS after an on-grid one joins it.
    """
    clips: list[dict[str, Any]] = []
    for row in sorted(windows, key=lambda w: (w["source"], not w["scored"], float(w["start"]))):
        index, scored = indexes[row["source"]], bool(row["scored"])
        pre, post = (PRE_ROLL_SECONDS, POST_ROLL_SECONDS) if scored else (Fraction(0), Fraction(0))
        origin = choose_origin(index, Fraction(str(row["start"])) - pre, periods)
        end = choose_end(index, Fraction(str(row["end"])) + post, Fraction(str(durations[row["source"]])))
        last = clips[-1] if clips and clips[-1]["source"] == row["source"] and clips[-1]["scored"] == scored else None
        if last is not None and _joins(last, origin, index, periods):
            last["end_pts"] = None if last["end_pts"] is None or end is None else max(last["end_pts"], end)
            last["windows"].append(_window(row))
            continue
        clips.append({"source": row["source"], "job_id": row["job_id"], "scored": scored, "origin_pts": origin,
                      "end_pts": end, "windows": [_window(row)]})
    for clip in clips:
        index = indexes[clip["source"]]
        origin = index.seconds(clip["origin_pts"])
        clip.update(key=f"j{clip['job_id']}-{int(origin):05d}", time_base=str(index.time_base),
                    origin_seconds=origin,
                    end_seconds=None if clip["end_pts"] is None else index.seconds(clip["end_pts"]),
                    on_grid=on_grid(origin, periods), grid_offsets_seconds=grid_offsets(origin, periods))
    keys = [clip["key"] for clip in clips]
    if len(set(keys)) != len(keys):
        raise ValueError("Hai clip trùng tên")
    return clips


def _window(row: dict[str, Any]) -> dict[str, Any]:
    return {"segment_id": row.get("segment_id"), "start": float(row["start"]), "end": float(row["end"])}


def _joins(last: dict[str, Any], origin: int, index: VideoIndex, periods: tuple[Fraction, ...]) -> bool:
    if last["end_pts"] is None or origin <= last["end_pts"]:
        return True
    gap = index.seconds(origin) - index.seconds(last["end_pts"])
    return gap <= MERGE_GAP_SECONDS and on_grid(index.seconds(last["origin_pts"]), periods) \
        and not on_grid(index.seconds(origin), periods)


# --- cut and verify --------------------------------------------------------------------------

def _ceil_us(seconds: Fraction) -> str:
    return f"{math.ceil(seconds * 1_000_000) / 1_000_000:.6f}"


def _floor_us(seconds: Fraction) -> str:
    return f"{math.floor(seconds * 1_000_000) / 1_000_000:.6f}"


def cut_clip(ffmpeg: Path, source: Path, index: VideoIndex, origin_pts: int, end_pts: int | None,
             target: Path, audio: bool = True) -> None:
    """Stream-copy [origin keyframe, end keyframe) of ``source`` into ``target`` (never overwritten).

    The seek point is rounded up to the microsecond so ffmpeg lands on the origin keyframe itself.
    """
    if target.exists():
        raise FileExistsError(f"{target} đã có; không ghi đè")
    temporary = target.with_name(target.stem + ".partial.mp4")
    temporary.unlink(missing_ok=True)  # an earlier interrupted cut of this same clip
    argv: list[Any] = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-ss",
                       _ceil_us(index.seconds(origin_pts)), "-i", source]
    if end_pts is not None:
        argv += ["-t", _floor_us(index.seconds(end_pts - origin_pts))]
    argv += ["-map", "0:v:0", *(["-map", "0:a:0?"] if audio else ["-an"]), "-c", "copy", "-map_metadata", "-1",
             "-map_chapters", "-1", "-movflags", "+faststart", "-f", "mp4", temporary]
    target.parent.mkdir(parents=True, exist_ok=True)
    _run(argv)
    temporary.replace(target)


def frame_hashes(ffmpeg: Path, input_args: list[Any]) -> list[tuple[int, str]]:
    """(pts in the stream's own time base, MD5) of every decoded video frame."""
    out = _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", *input_args, "-map", "0:v:0",
                "-fps_mode", "passthrough", "-enc_time_base:v", "demux", "-f", "framemd5", "-"])
    rows = []
    for line in out.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = [part.strip() for part in line.split(",")]
        rows.append((int(parts[2]), parts[5]))
    return rows


def compare_frames(clip: list[tuple[int, str]], film: list[tuple[int, str]], origin_pts: int,
                   end_pts: int | None) -> dict[str, Any]:
    """Gate (a) on frame lists: every film frame in [origin, end) is in the clip, bit-identical, at
    clip pts + origin_pts; the clip starts at pts 0; extra clip frames only at or after ``end_pts``."""
    film_map = dict(film)
    expected = [pts for pts, _ in film if pts >= origin_pts and (end_pts is None or pts < end_pts)]
    shifted = [(pts + origin_pts, digest) for pts, digest in clip]
    present = {pts for pts, _ in shifted}
    mismatched = [pts for pts, digest in shifted if pts in film_map and film_map[pts] != digest]
    unknown = [pts for pts, _ in shifted if pts not in film_map]
    missing = [pts for pts in expected if pts not in present]
    extra = [pts for pts, _ in shifted if end_pts is not None and pts >= end_pts]
    starts_at_zero = bool(clip) and min(pts for pts, _ in clip) == 0
    return {"clip_frames": len(clip), "expected_frames": len(expected), "missing": len(missing),
            "mismatched": len(mismatched), "unknown": len(unknown), "extra_after_end": len(extra),
            "starts_at_zero": starts_at_zero,
            "ok": bool(expected) and not missing and not mismatched and not unknown and starts_at_zero,
            "first_problem_pts": (missing + mismatched + unknown)[:5]}


def verify_clip(ffmpeg: Path, source: Path, clip_path: Path, index: VideoIndex, origin_pts: int,
                end_pts: int | None) -> dict[str, Any]:
    """Decode the clip and the film (from the keyframe before the origin, so a frame that needs
    the previous group of pictures would show) and compare them with ``compare_frames``."""
    film_args: list[Any] = ["-copyts", "-ss", _floor_us(index.seconds(index.previous(origin_pts)))]
    if end_pts is not None:
        film_args += ["-to", _ceil_us(index.seconds(end_pts) + 1)]
    film = frame_hashes(ffmpeg, [*film_args, "-i", source])
    clip = frame_hashes(ffmpeg, ["-i", clip_path])
    return compare_frames(clip, film, origin_pts, end_pts)


def window_source(root: Path, job_id: int, start: float, duration: float,
                  golden_root: Path | None = None) -> tuple[Path, float]:
    """(file, start within it) of a window of a Golden film, for benchmark excerpts.

    An active clip set (v2) that covers the whole window wins (scored or benchmark clip);
    otherwise a film source of that job in an active set (v1/v1.1 before v2 is activated).
    """
    from biliflow.golden_set import is_clip, read_json
    golden_root = Path(golden_root or Path(root) / "annotations/golden")
    film = None
    for path in sorted(golden_root.glob("*/segments.json")):
        for source in read_json(path)["sources"].values():
            origin = source.get("origin") or {}
            if is_clip(source) and origin.get("job_id") == job_id:
                begin = Fraction(origin["origin_pts"]) * Fraction(origin["time_base"])
                end = None if origin.get("end_pts") is None else Fraction(origin["end_pts"]) * Fraction(origin["time_base"])
                if begin <= Fraction(str(start)) and (end is None or Fraction(str(start)) + Fraction(str(duration)) <= end):
                    return path.parent / source["path"], float(Fraction(str(start)) - begin)
            elif not is_clip(source) and source.get("job_id") == job_id and film is None:
                film = Path(root) / source["path"]
    if film is None:
        raise ValueError(f"Không có clip hay phim vàng nào của job {job_id} chứa {start}+{duration} s")
    return film, float(start)


def film_source(root: Path, job_id: int, golden_root: Path | None = None) -> Path:
    """The whole original film of a Golden job, while an active set keeps it (v1/v1.1, or a film source of v2)."""
    from biliflow.golden_set import is_clip, read_json
    golden_root = Path(golden_root or Path(root) / "annotations/golden")
    for path in sorted(golden_root.glob("*/segments.json")):
        for source in read_json(path)["sources"].values():
            if not is_clip(source) and source.get("job_id") == job_id:
                return Path(root) / source["path"]
    raise SystemExit(f"Cần phim gốc của job {job_id} cho lượt cả phim, nhưng bộ vàng đang hoạt động chỉ còn clip "
                     "của job này (Golden v2); lượt cả phim cũ của nó chỉ còn là bằng chứng lịch sử")


# --- shifting into clip time -----------------------------------------------------------------

def shifted(value: Any, offset: Fraction) -> float:
    return float(Fraction(str(value)) - offset)


def shift_queue(queue: dict[str, Any], offset: Fraction, duration: float, sha256: str) -> dict[str, Any]:
    """A review queue moved into a clip's time: items touching the clip, every time shifted
    (scene-card moments included), the source pinned to the clip. Nothing else changes."""
    moved = json.loads(json.dumps(queue))
    moved["source"] = dict(moved.get("source") or {}, sha256=sha256)
    for key in ("items", "advisory_items"):
        kept = []
        for item in moved.get(key) or []:
            start, end = shifted(item["start_seconds"], offset), shifted(item["end_seconds"], offset)
            if max(end, start + 0.5) <= 0 or start >= duration:
                continue
            item["start_seconds"], item["end_seconds"] = start, end
            for interval in item.get("detected_intervals") or []:
                if isinstance(interval, dict):
                    for field in CLIP_TIME_FIELDS:
                        interval[field] = shifted(interval[field], offset)
            kept.append(item)
        moved[key] = kept
    return moved


def shift_event(event: dict[str, Any], offset: Fraction, duration: float) -> dict[str, Any]:
    """A label in clip time, clamped to the clip (its segment lies inside the clip, so the scored part is unchanged)."""
    moved = dict(event)
    start = max(0.0, shifted(event["start_seconds"], offset))
    end = min(float(duration), shifted(event["end_seconds"], offset))
    if end <= start:
        raise ValueError(f"Nhãn {event.get('id')} nằm ngoài clip")
    moved["start_seconds"], moved["end_seconds"] = start, end
    return moved


def shift_suggestion(suggestion: dict[str, Any], offset: Fraction) -> dict[str, Any]:
    moved = dict(suggestion)
    for field in SUGGESTION_TIME_FIELDS:
        if isinstance(moved.get(field), (int, float)):
            moved[field] = shifted(moved[field], offset)
    return moved


# --- v2 manifest and labels ------------------------------------------------------------------

def clip_source(clip: dict[str, Any], origin_source: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    """Manifest entry of one cut clip (``info``: path, sha256, size_bytes and probe_clip fields)."""
    entry = {"kind": "clip", "path": info["path"], "sha256": info["sha256"], "size_bytes": info["size_bytes"],
             "duration_seconds": info["duration_seconds"], "width": info["width"], "height": info["height"],
             "fps": info["fps"], "content_style": origin_source["content_style"], "scored": clip["scored"],
             "origin": {"job_id": clip["job_id"], "sha256": origin_source["sha256"],
                        "origin_seconds": float(clip["origin_seconds"]), "origin_pts": clip["origin_pts"],
                        "time_base": clip["time_base"],
                        "end_seconds": None if clip["end_seconds"] is None else float(clip["end_seconds"]),
                        "end_pts": clip["end_pts"], "on_sampling_grid": clip["on_grid"],
                        "grid_offsets_seconds": clip["grid_offsets_seconds"]}}
    if not clip["scored"]:
        entry["purpose"] = "benchmark (không chấm điểm)"
        entry["windows"] = [{"origin_start_seconds": w["start"], "origin_end_seconds": w["end"],
                             "start_seconds": shifted(w["start"], clip["origin_seconds"]),
                             "end_seconds": shifted(w["end"], clip["origin_seconds"])} for w in clip["windows"]]
    return entry


def build_v2(parts: dict[str, dict[str, Any]], clips: list[dict[str, Any]], infos: dict[str, dict[str, Any]],
             periods: tuple[Fraction, ...], approximate: Iterable[str], created_at: str | None = None,
             films: dict[str, str] | None = None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """(manifest, labels, first history entry) of v2 from the v1/v1.1 ``parts`` of load_golden_sets.

    Segment and label ids, regions, actions and statuses are kept. Segments on a clip move into clip
    time; segments of a source in ``films`` (v1/v1.1 key -> v2 key) stay on that whole film, whose
    manifest entry is copied unchanged, and their labels are copied as they are.
    """
    from biliflow.golden_scoring import labels_fingerprint
    created_at, films = created_at or now_iso(), films or {}
    clip_of_segment = {w["segment_id"]: clip for clip in clips for w in clip["windows"] if w["segment_id"]}
    sources: dict[str, Any] = {}
    for clip in clips:
        origin_source = next(part["manifest"]["sources"][clip["source"]] for part in parts.values()
                             if clip["source"] in part["manifest"]["sources"])
        sources[clip["key"]] = clip_source(clip, origin_source, infos[clip["key"]])
    for old, new in films.items():
        sources[new] = dict(next(part["manifest"]["sources"][old] for part in parts.values()
                                 if old in part["manifest"]["sources"]))
    segments, events, statuses, resolutions, decisions = [], [], {}, {}, {}
    retired, reopened, owner = [], [], {}
    for name, part in parts.items():
        labels = part["labels"]
        offset_of = {}  # segments on clips only; a film keeps its times
        for segment in part["manifest"]["segments"]:
            if segment["source"] in films:
                segments.append({"id": segment["id"], "source": films[segment["source"]],
                                 "start_seconds": segment["start_seconds"], "end_seconds": segment["end_seconds"],
                                 "split": segment["split"], "purpose": segment.get("purpose", ""), "from_set": name})
                continue
            clip = clip_of_segment[segment["id"]]
            offset_of[segment["id"]] = (clip["origin_seconds"], sources[clip["key"]]["duration_seconds"])
            segments.append({"id": segment["id"], "source": clip["key"],
                             "start_seconds": shifted(segment["start_seconds"], clip["origin_seconds"]),
                             "end_seconds": shifted(segment["end_seconds"], clip["origin_seconds"]),
                             "split": segment["split"], "purpose": segment.get("purpose", ""), "from_set": name,
                             "origin_start_seconds": segment["start_seconds"],
                             "origin_end_seconds": segment["end_seconds"]})
        events += [shift_event(e, *offset_of[e["segment_id"]]) if e["segment_id"] in offset_of else dict(e)
                   for e in labels["events"]]
        statuses.update(labels["segments"])
        for key, value in (labels.get("suggestion_resolutions") or {}).items():
            moved = dict(value)
            if isinstance(value.get("suggestion"), dict) and value.get("segment_id") in offset_of:
                moved["suggestion"] = shift_suggestion(value["suggestion"], offset_of[value["segment_id"]][0])
            _put_unique(resolutions, key, moved, "suggestion_resolutions")
        for key, value in (labels.get("watermark_decisions") or {}).items():
            decisions[key] = _merge_decision(decisions.get(key), value, name, owner.setdefault(key, name))
        retired += [x for x in labels.get("retired_event_ids") or () if x not in retired]
        reopened += [x for x in labels.get("reopened_suggestions") or () if x not in reopened]
    manifest = {"schema_version": SCHEMA_VERSION, "golden_set": "v2", "created_at": created_at,
                "derived_from": {name: {"manifest_sha256": part["manifest_sha256"],
                                        "labels_sha256": file_sha256(part["labels_path"]),
                                        "labels_revision": part["labels"].get("revision"),
                                        "labels_fingerprint": labels_fingerprint(part["labels"]),
                                        "segments": len(part["manifest"]["segments"]),
                                        "events": len(part["labels"]["events"])} for name, part in parts.items()},
                "sampling": {"profile": SAMPLING_PROFILE, "periods_seconds": [str(p) for p in periods]},
                "approximate_suggestions": sorted(set(approximate)),
                "sources": sources, "segments": segments}
    validate_manifest(manifest)
    labels = {"schema_version": SCHEMA_VERSION, "golden_set": "v2", "manifest_sha256": canonical_sha256(manifest),
              "revision": 0, "updated_at": created_at, "segments": statuses, "events": events,
              "suggestion_resolutions": resolutions, "watermark_decisions": decisions,
              "retired_event_ids": retired, "reopened_suggestions": reopened}
    history = {"at": created_at, "actor": "migration", "action": "migrate_from_" + "_".join(parts), "revision": 0,
               "before": manifest["derived_from"],
               "after": {"manifest_sha256": labels["manifest_sha256"], "events": len(events),
                         "segments": len(segments), "clips": len(clips), "films": len(films)}}
    return manifest, labels, history


def _merge_decision(earlier: dict[str, Any] | None, value: dict[str, Any], name: str,
                    first: str) -> dict[str, Any]:
    """One film-long watermark answered in two sets (each set asked it for its own segments).

    The same answer on the same box becomes one entry listing both sets' labels, with both
    original records kept under ``sets``; different answers are refused.
    """
    if earlier is None:
        return value
    if (earlier.get("decision"), earlier.get("region_source_pixels")) != \
            (value.get("decision"), value.get("region_source_pixels")):
        raise ValueError(f"watermark_decisions: hai bộ trả lời khác nhau cho cùng watermark ({name})")
    originals = dict(earlier.get("sets") or {first: earlier}, **{name: value})
    merged = {key: item for key, item in earlier.items() if key not in ("sets", "events", "at")}
    merged["at"] = min(str(record.get("at") or "") for record in originals.values())
    events = [event for record in originals.values() for event in record.get("events") or ()]
    if events:
        merged["events"] = events
    merged["sets"] = originals
    return merged


def _put_unique(target: dict[str, Any], key: str, value: Any, what: str) -> None:
    if key in target and target[key] != value:
        raise ValueError(f"{what}: khóa {key} có ở hai bộ với nội dung khác nhau")
    target[key] = value


def migrate_suggestions(payloads: dict[str, dict[str, Any]], offsets: dict[str, Fraction],
                        manifest_sha256: str) -> dict[str, Any]:
    """The labeling page's suggestions of v1/v1.1 (ids kept), pinned to the v2 manifest: those of a segment
    on a clip move into clip time, those of a segment on a whole film (no offset) stay as they are."""
    rows, inputs = [], []
    for name, payload in payloads.items():
        moved = [shift_suggestion(s, offsets[s["segment_id"]]) if s["segment_id"] in offsets else dict(s)
                 for s in payload["suggestions"]]
        rows += moved
        inputs.append({"origin": f"migrated:{name}", "manifest_sha256": payload.get("manifest_sha256"),
                       "suggestions": len(moved)})
    return {"schema_version": 1, "created_at": now_iso(), "manifest_sha256": manifest_sha256, "inputs": inputs,
            "suggestions": rows}


# --- gate (b1): the same detector output scores the same -------------------------------------

LABEL_FIELDS = ("segment_id", "category", "group", "expected_action", "severity", "ambiguous", "split", "status",
                "coverage", "main_coverage", "best_item_id", "region", "suggestion_agrees", "region_approximate",
                "content_present", "trap_item_ids")
ITEM_FIELDS = ("split", "group", "category", "advisory", "region", "kind", "suggested_decision", "priority",
               "candidate_type", "labels", "verdict", "trap_hits")


def compare_shifted_cards(film_card: dict[str, Any], clip_card: dict[str, Any],
                          offsets: dict[str, Fraction]) -> list[str]:
    """Differences between a card of the films and a card of the clips scored from the same queues.

    ``offsets`` maps a segment id to its clip origin; positions are compared after shifting back.
    Empty means identical: every metric, every label and every scored item.
    """
    problems = []
    for group, splits in film_card["metrics"].items():
        for split, values in splits.items():
            other = clip_card["metrics"].get(group, {}).get(split)
            if values != other:
                problems.append(f"metrics {group}/{split}: {values} != {other}")
    if film_card["unscored_labels"] != clip_card["unscored_labels"]:
        problems.append(f"unscored labels {film_card['unscored_labels']} != {clip_card['unscored_labels']}")
    segments = {s["id"]: s for s in clip_card["segments"]}
    for segment in film_card["segments"]:
        other = segments.get(segment["id"])
        if other is None or (segment["status"], segment["scored_groups"]) != (other["status"], other["scored_groups"]) \
                or abs(segment["seconds"] - other["seconds"]) > POSITION_TOLERANCE:
            problems.append(f"segment {segment['id']} differs")
    problems += _compare_rows({l["id"]: l for l in film_card["labels"]}, {l["id"]: l for l in clip_card["labels"]},
                              LABEL_FIELDS, offsets, "label")
    problems += _compare_rows({(i["item_id"], i["segment_id"]): i for i in film_card["items"]},
                              {(i["item_id"], i["segment_id"]): i for i in clip_card["items"]},
                              ITEM_FIELDS, offsets, "item")
    return problems


def _compare_rows(film: dict, clip: dict, fields: tuple[str, ...], offsets: dict[str, Fraction], what: str) -> list[str]:
    problems = [f"{what} {key} only in the film card" for key in sorted(set(film) - set(clip), key=str)]
    problems += [f"{what} {key} only in the clip card" for key in sorted(set(clip) - set(film), key=str)]
    for key in sorted(set(film) & set(clip), key=str):
        a, b = film[key], clip[key]
        problems += [f"{what} {key} {field}: {a.get(field)!r} != {b.get(field)!r}"
                     for field in fields if a.get(field) != b.get(field)]
        offset = float(offsets[a["segment_id"]])
        if len(a.get("moments") or []) != len(b.get("moments") or []):
            problems.append(f"{what} {key} moments differ in number")
        for left, right in [(a.get("clip"), b.get("clip"))] + list(zip(a.get("moments") or [], b.get("moments") or [])):
            if left is None and right is None:
                continue
            if left is None or right is None or any(abs(x - (y + offset)) > POSITION_TOLERANCE
                                                    for x, y in zip(left, right)):
                problems.append(f"{what} {key} position {left} != {right} + {offset:.6f}")
    return problems


# --- gate b2: the same code on the films and on the clips --------------------------------------

OUTCOME_FIELDS = ("status", "coverage", "main_coverage", "suggestion_agrees")


def _outcome(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {**{field: row.get(field) for field in OUTCOME_FIELDS}, "region_ok": (row.get("region") or {}).get("ok")}


def _item_counts(card: dict) -> dict[str, dict[str, dict[str, int]]]:
    """segment -> group -> counts of scored items by kind and verdict (plus trap hits)."""
    counts: dict[str, dict[str, dict[str, int]]] = {}
    for item in card["items"]:
        row = counts.setdefault(item["segment_id"], {}).setdefault(item["group"], {})
        kind = f"{'advisory' if item['advisory'] else 'main'}_{item['verdict']}"
        row[kind] = row.get(kind, 0) + 1
        if item.get("trap_hits"):
            row["trap_hits"] = row.get("trap_hits", 0) + len(item["trap_hits"])
    return counts


def run_differences(film_card: dict[str, Any], clip_card: dict[str, Any]) -> dict[str, Any]:
    """Gate (b2): per-label outcome of one code version on the films (v1 + v1.1) and on the clips (v2).

    Not a pass/fail gate: the clips change what the detectors see (no whole-film context), so the
    user reviews every changed label before the clip run becomes the v2 baseline. Only ids, groups
    and outcomes are listed (no notes, no OCR text).
    """
    film = {row["id"]: row for row in film_card["labels"]}
    clip = {row["id"]: row for row in clip_card["labels"]}
    labels = []
    for key in sorted(set(film) | set(clip)):
        base = film.get(key) or clip[key]
        left, right = _outcome(film.get(key)), _outcome(clip.get(key))
        labels.append({"id": key, "segment_id": base["segment_id"], "group": base["group"],
                       "category": base["category"], "severity": base["severity"],
                       "film": left, "clip": right, "same": left == right})
    film_items, clip_items = _item_counts(film_card), _item_counts(clip_card)
    items = {segment: {group: {"film": film_items.get(segment, {}).get(group, {}),
                               "clip": clip_items.get(segment, {}).get(group, {})}
                       for group in sorted(set(film_items.get(segment, {})) | set(clip_items.get(segment, {})))}
             for segment in sorted(set(film_items) | set(clip_items))}
    metrics = {group: {"film": film_card["metrics"].get(group, {}).get("all"),
                       "clip": clip_card["metrics"].get(group, {}).get("all")}
               for group in sorted(set(film_card["metrics"]) | set(clip_card["metrics"]))}
    return {"labels": labels, "changed": [row["id"] for row in labels if not row["same"]],
            "items": items, "items_changed": sorted(segment for segment, groups in items.items()
                                                    if any(g["film"] != g["clip"] for g in groups.values())),
            "metrics": metrics}
