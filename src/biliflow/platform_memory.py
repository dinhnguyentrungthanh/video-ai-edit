"""Platform-logo memory (batch 4a, 2026-10-03): remember, convert and seed records.

A remembered platform logo (iQIYI, WeTV…) is a record of the studio-logo store
``state/studio-logo-memory.json`` (file schema 2) with ``memory_class:
"platform_logo"`` and decision BLUR, plus ``platform``, ``blur_region`` (a box
relative to the frame) and ``logo_frame_times`` (the stored frames that show
the logo on a dark background). Every studio-logo reader filters on
``memory_class == "studio_logo"``, so older code ignores these records; frame
JPEGs live under state/studio-logo-frames like a studio record's. A record
only makes build-review propose a BLUR card; a human still approves it.

The writers reuse brand_memory's own record helpers so both classes stay one
format. Conversions and seeds are dry runs unless ``apply``; ``apply`` writes
only when the memory still has the bytes the plan was made from, after a
backup to state/backups/studio-logo-memory-<ts>.json.

Nothing here deletes a frame (security review 2026-10-03): new frames go to a
folder nothing uses yet, and the frames of a replaced or forgotten record are
moved to state/backups/studio-logo-frames-<ts>/ after the memory no longer
points at them.
"""

from __future__ import annotations

import contextlib
import hashlib
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from biliflow.brand_memory import (
    STUDIO_LOGO_FRAMES_PATH,
    STUDIO_LOGO_JPEG_QUALITY,
    STUDIO_LOGO_MEMORY_PATH,
    _empty_studio_logo_memory,
    _parse_studio_logo_memory,
    _sign_studio_logo_frames,
    _studio_logo_v2_fields,
    _write_studio_logo_memory,
    load_studio_logo_memory,
    prepare_studio_logo_frames,
    studio_logo_eligible,
    studio_logo_signatures,
)
from biliflow.platform_logos import (
    PLATFORM_MEMORY_CLASS,
    REGION_PADDING,
    SourceProbe,
    ident_span,
    load_frame_image,
    logo_region,
    mask_rects,
)
from biliflow.platform_names import PLATFORMS

STUDIO_MEMORY_CLASS = "studio_logo"
MAX_BOX_SHARE = 0.25  # a "logo" covering over a quarter of the frame is a whole picture
UNKNOWN_PLATFORM_NAME = "(chưa rõ tên)"
MEMORY_CHANGED_MESSAGE = "Bộ nhớ logo vừa thay đổi; không ghi gì — hãy tải lại rồi làm lại."


class MemoryChanged(RuntimeError):
    """The memory file changed while a write was prepared; nothing of that write happened."""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


# ------------------------------------------------------------ frame folders


def move_frames_to_backups(root: Path, folder: object) -> Path | None:
    """Move a frames folder of state/studio-logo-frames to state/backups/studio-logo-frames-<ts>[-n]/.

    Never deletes: one rename on the same volume, so the folder moves whole or
    not at all (a file in use raises OSError and leaves it where it was).
    ``None`` when there is no such folder.
    """
    if not folder:
        return None
    base = (root / STUDIO_LOGO_FRAMES_PATH).resolve()
    source = (root / str(folder)).resolve()
    if source.parent != base or not source.is_dir():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = root / "state" / "backups" / f"studio-logo-frames-{stamp}"
    suffix = 1
    while (target / source.name).exists():
        target = target.with_name(f"studio-logo-frames-{stamp}-{suffix}")
        suffix += 1
    created = not target.exists()
    target.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(source, target / source.name)
    except OSError:
        if created:
            with contextlib.suppress(OSError):
                target.rmdir()  # leave no empty frames backup behind
        raise
    return target / source.name


def _move_frames_quietly(root: Path, records: list[dict[str, Any]]) -> None:
    """After the memory no longer points at them: frames to state/backups; a folder in use stays put."""
    for record in records:
        with contextlib.suppress(OSError):
            move_frames_to_backups(root, record.get("frames_folder"))


def in_fresh_folder(root: Path, prepared: dict[str, Any]) -> dict[str, Any]:
    """``prepared`` pointed at a frames folder nothing uses yet: its own name, else ``-2``, ``-3``…"""
    folder, index = str(prepared["folder"]), 2
    while os.path.lexists(root / folder) or os.path.lexists(root / f"{folder}.tmp"):
        folder = f"{prepared['folder']}-{index}"
        index += 1
    old = f"{prepared['folder']}/"
    frames = [
        {**frame, "image": f"{folder}/{frame['image'][len(old):]}"}
        if isinstance(frame, dict) and str(frame.get("image", "")).startswith(old) else frame
        for frame in prepared.get("frames") or []
    ]
    return {**prepared, "folder": folder, "frames": frames}


def _discard_frames(folder: Path, names: list[str]) -> None:
    """Remove a folder this module just wrote: its own files by name, then the empty folder."""
    for name in names:
        with contextlib.suppress(OSError):
            (folder / name).unlink()
    with contextlib.suppress(OSError):
        folder.rmdir()


def _write_fresh_frames(root: Path, prepared: dict[str, Any]) -> dict[str, Any]:
    """Write the JPEGs of ``prepared`` into a fresh folder; returns ``prepared`` for that folder.

    Never overwrites or deletes an existing folder, so a replaced record keeps
    its frames until the memory no longer points at them. On failure the files
    written here are removed again.
    """
    base = root / STUDIO_LOGO_FRAMES_PATH
    base.mkdir(parents=True, exist_ok=True)
    fresh = in_fresh_folder(root, prepared)
    target = root / fresh["folder"]
    if target.resolve().parent != base.resolve():
        raise ValueError("Studio-logo frame folder must be inside state/studio-logo-frames")
    temporary = target.with_name(target.name + ".tmp")
    temporary.mkdir()
    names = [name for _, name, _ in fresh["stored"]]
    try:
        for _, name, data in fresh["stored"]:
            (temporary / name).write_bytes(data)
        os.rename(temporary, target)
    except OSError:
        _discard_frames(temporary, names)
        raise
    return fresh


def _records_of(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return [record for record in payload["records"] if isinstance(record, dict) and record.get("key") == key]


def _without(payload: dict[str, Any], key: str) -> list[Any]:
    return [record for record in payload["records"] if not (isinstance(record, dict) and record.get("key") == key)]


def _write_memory_or_discard(
    root: Path, path: Path, snapshot: bytes | None, payload: dict[str, Any], written: dict[str, Any],
) -> None:
    """Write the memory if it still has ``snapshot``; otherwise (or on failure) drop the new frames."""
    try:
        if not memory_unchanged(path, snapshot):
            raise MemoryChanged(MEMORY_CHANGED_MESSAGE)
        _write_studio_logo_memory(root, payload)
    except Exception:
        _discard_frames(root / written["folder"], [name for _, name, _ in written["stored"]])
        raise


def platform_logo_eligible(item: dict[str, Any]) -> bool:
    """A platform-logo card, or a full-frame logo card (studio-logo rules)."""
    return item.get("category") == "visual_logo" and (
        item.get("candidate_type") == PLATFORM_MEMORY_CLASS or studio_logo_eligible(item)
    )


def platform_entry(key: object) -> dict[str, Any] | None:
    """``{"key", "name"}`` of a known platform key."""
    return {"key": str(key), "name": PLATFORMS[str(key)]["name"]} if str(key) in PLATFORMS else None


def _decode(data: bytes) -> np.ndarray | None:
    bgr = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    return None if bgr is None or not bgr.size else cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def _relative_box(box: list[float], size: tuple[int, int]) -> list[float]:
    width, height = float(size[0]), float(size[1])
    return [round(box[0] / width, 6), round(box[1] / height, 6),
            round((box[2] - box[0]) / width, 6), round((box[3] - box[1]) / height, 6)]


def derive_platform_region(
    frames: list[tuple[float, np.ndarray]], regions: object = (),
) -> dict[str, Any]:
    """The logo frames and blur box of a stored window (``reason`` set when it is refused).

    Refused with ``no_logo_frames`` when no frame shows a logo on a dark
    background (a licence card or a picture), and with ``box_too_large`` when
    the padded box covers over a quarter of the frame.
    """
    if not frames:
        return {"reason": "no_frames", "logo_frame_times": [], "blur_region": None}
    shape = frames[0][1].shape
    region = logo_region(frames, None, mask_rects(regions, shape))
    if region is None:
        return {"reason": "no_logo_frames", "logo_frame_times": [], "blur_region": None}
    size = (int(shape[1]), int(shape[0]))
    box = _relative_box(region["box"], size)
    share = box[2] * box[3]
    blur = {"box": box, "method": "logo_pixels", "frames": len(region["frames"]),
            "padding": list(REGION_PADDING), "analysis_size": list(size)}
    result = {"reason": None, "logo_frame_times": region["frames"], "blur_region": blur,
              "box_share": round(share, 4), "anchor_seconds": region["anchor_seconds"],
              "analysis_box": region["box"]}
    if share > MAX_BOX_SHARE:
        result.update(reason="box_too_large", logo_frame_times=[], blur_region=None)
    return result


def source_box(blur_region: dict | None, frame_size: object) -> dict[str, int] | None:
    """A relative blur box in source pixels."""
    if not isinstance(blur_region, dict) or not isinstance(frame_size, (list, tuple)) or len(frame_size) != 2:
        return None
    try:
        x, y, width, height = (float(value) for value in blur_region["box"])
        frame_width, frame_height = float(frame_size[0]), float(frame_size[1])
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0 or frame_width <= 0 or frame_height <= 0:
        return None
    x0 = max(0, math.floor(x * frame_width + 1e-6))
    y0 = max(0, math.floor(y * frame_height + 1e-6))
    x1 = min(int(frame_width), math.ceil((x + width) * frame_width - 1e-6))
    y1 = min(int(frame_height), math.ceil((y + height) * frame_height - 1e-6))
    return {"x": x0, "y": y0, "width": max(1, x1 - x0), "height": max(1, y1 - y0)}


# ----------------------------------------------------------------- remember


def prepare_platform_logo_frames(
    root: Path, queue: dict[str, Any], item: dict[str, Any],
    window_frames: dict[str, Any] | None = None,
    ignored_regions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """What a platform-logo record stores, computed without writing anything.

    The studio-logo preparation (decoded window frames + previews, ignored
    watermark regions) plus ``platform``: the logo frames, the blur box and,
    for a decoded window, the logo span (cut to black to last logo frame).
    """
    prepared = prepare_studio_logo_frames(root, queue, item, window_frames, ignored_regions)
    frames = sorted(
        ((float(moment), image) for moment, _, data in prepared["stored"]
         if moment is not None and (image := _decode(data)) is not None),
        key=lambda value: value[0],  # never compares two images that share a time
    )
    derived = derive_platform_region(frames, prepared["ignored_regions"])
    span = None
    if derived["reason"] is None and prepared["frames_source"] == "source_video":
        masks = mask_rects(prepared["ignored_regions"], frames[0][1].shape)
        span = ident_span(frames, derived["anchor_seconds"], tuple(derived["analysis_box"]), masks,
                          bounds=tuple(prepared["window"] or (frames[0][0], frames[-1][0])))
        if span["method"] != "dark_run":
            span = None
    prepared["platform"] = {**derived, "span": span}
    return prepared


def remember_platform_logo(
    root: Path, queue: dict[str, Any], item: dict[str, Any], prepared: dict[str, Any],
    *, platform: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Store a platform-logo record for an explicit user BLUR ("làm mờ & nhớ").

    Replaces any record of the same card (either class) without deleting
    anything: the memory is backed up, the new frames go to a fresh folder, the
    memory is written only while it still has the bytes read first
    (``MemoryChanged`` otherwise, with nothing written), and the replaced
    record's frames are then moved to state/backups. ``None`` when the card is
    not BLUR, not eligible or shows no logo frame.
    """
    root = root.resolve(strict=True)
    details = prepared.get("platform") or {}
    if item.get("decision") != "BLUR" or not platform_logo_eligible(item) or not details.get("logo_frame_times"):
        return None
    source_sha256 = str((queue.get("source") or {}).get("sha256", ""))
    key = f"{source_sha256}:{item.get('id', '')}"
    path, snapshot = memory_snapshot(root)
    payload = _parse_studio_logo_memory(snapshot) if snapshot is not None else _empty_studio_logo_memory()
    replaced = _records_of(payload, key)
    if snapshot is not None:
        backup_memory(root, snapshot)
    if not memory_unchanged(path, snapshot):
        raise MemoryChanged(MEMORY_CHANGED_MESSAGE)
    written = _write_fresh_frames(root, prepared)
    payload["records"] = _without(payload, key)
    record = {
        "key": key,
        "source_sha256": source_sha256,
        "review_item_id": item.get("id"),
        "decision": "BLUR",
        "memory_class": PLATFORM_MEMORY_CLASS,
        "platform": platform,
        "labels": list(item.get("labels") or []),
        "candidate_type": item.get("candidate_type"),
        "review_kind": item.get("review_kind"),
        "start_seconds": item.get("start_seconds"),
        "end_seconds": item.get("end_seconds"),
        "signatures": studio_logo_signatures(root, item),
        "window_text": {"covered": False, "texts": []},
        "blur_region": details["blur_region"],
        "logo_frame_times": list(details["logo_frame_times"]),
        "created_at": _now(),
        "confirmed_by": "user",
        "automatic_edit": False,
    }
    record.update(_studio_logo_v2_fields(written))
    payload["records"].append(record)
    _write_memory_or_discard(root, path, snapshot, payload, written)
    _move_frames_quietly(root, replaced)
    return record


def forget_remembered_logo(root: Path, queue: dict[str, Any], item_id: str) -> bool:
    """Remove a card's remembered logo, studio or platform (the user chose another decision).

    Nothing is deleted: the memory is backed up and written only while it
    still has the bytes read first (``MemoryChanged`` otherwise, with nothing
    written); the record's frames are then moved to state/backups. ``False``,
    with nothing written, when the card has no record.
    """
    root = root.resolve(strict=True)
    path, snapshot = memory_snapshot(root)
    if snapshot is None:
        return False
    payload = _parse_studio_logo_memory(snapshot)
    key = f"{str((queue.get('source') or {}).get('sha256', ''))}:{item_id}"
    removed = _records_of(payload, key)
    if not removed:
        return False
    backup_memory(root, snapshot)
    if not memory_unchanged(path, snapshot):
        raise MemoryChanged(MEMORY_CHANGED_MESSAGE)
    payload["records"] = _without(payload, key)
    _write_studio_logo_memory(root, payload)
    _move_frames_quietly(root, removed)
    return True


# ------------------------------------------------------- convert and seed


def stored_frames(root: Path, record: dict[str, Any]) -> list[tuple[float, np.ndarray]]:
    """Every stored frame of a record with a time (state/studio-logo-frames only)."""
    frames = []
    for entry in record.get("stored_frames") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("t"), (int, float)):
            continue
        image = load_frame_image(root, entry.get("image"))
        if image is not None:
            frames.append((float(entry["t"]), image))
    return sorted(frames, key=lambda value: value[0])


def backup_memory(root: Path, snapshot: bytes) -> Path:
    """Write ``snapshot`` to state/backups/studio-logo-memory-<ts>[-n].json (never overwrites)."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = root / "state" / "backups" / f"studio-logo-memory-{stamp}.json"
    suffix = 1
    while backup.exists():
        backup = backup.with_name(f"studio-logo-memory-{stamp}-{suffix}.json")
        suffix += 1
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(snapshot)
    return backup


def memory_unchanged(path: Path, snapshot: bytes | None) -> bool:
    """The memory file still has ``snapshot`` (or is still missing when it was)."""
    if snapshot is None:
        return not path.exists()
    try:
        return path.read_bytes() == snapshot
    except OSError:
        return False


def memory_snapshot(root: Path) -> tuple[Path, bytes | None]:
    """The memory path and its bytes (``None`` when there is no memory yet)."""
    path = root / STUDIO_LOGO_MEMORY_PATH
    return path, (path.read_bytes() if path.exists() else None)


def flip_record_class(
    record: dict[str, Any], to: str, *, platform: dict[str, Any] | None, derived: dict[str, Any] | None,
    actor: str,
) -> None:
    """Turn a studio record into a platform one (with ``derived``) or back; keeps every other field."""
    record["converted_from"] = {
        "memory_class": record.get("memory_class"), "decision": record.get("decision"),
        "at": _now(), "by": actor,
    }
    if to == PLATFORM_MEMORY_CLASS:
        assert derived is not None
        record.update(memory_class=PLATFORM_MEMORY_CLASS, decision="BLUR", platform=platform,
                      blur_region=derived["blur_region"], logo_frame_times=list(derived["logo_frame_times"]))
    else:
        record.update(memory_class=STUDIO_MEMORY_CLASS, decision="KEEP")
        for name in ("platform", "blur_region", "logo_frame_times"):
            record.pop(name, None)


def convert_plan(root: Path, record: dict[str, Any] | None, to: str) -> dict[str, Any]:
    """Read-only: whether ``record`` can become class ``to`` and the derived region."""
    if record is None:
        return {"status": "missing", "reason": "no_record_with_this_key"}
    current = record.get("memory_class")
    if current == to:
        return {"status": "already", "reason": None}
    if to == STUDIO_MEMORY_CLASS:
        if current != PLATFORM_MEMORY_CLASS:
            return {"status": "refused", "reason": "not_a_platform_logo_record"}
        return {"status": "convertible", "reason": None, "derived": None}
    if current != STUDIO_MEMORY_CLASS:
        return {"status": "refused", "reason": "not_a_studio_logo_record"}
    frames = stored_frames(root, record)
    if not frames:
        return {"status": "refused", "reason": "no_stored_frames"}
    derived = derive_platform_region(frames, record.get("ignored_regions") or [])
    if derived["reason"] is not None:
        return {"status": "refused", "reason": derived["reason"], "derived": derived}
    return {"status": "convertible", "reason": None, "derived": derived}


def convert_logo_memory_class(
    root: Path, keys: list[str], *, to: str, platform: str | None = None, apply: bool = False,
    actor: str = "platform-logo-convert", expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Turn remembered studio logos into platform logos (or back); dry run by default.

    A studio record becomes a platform record only when its stored frames show
    a logo on a dark background whose padded box covers at most a quarter of
    the frame (the licence-card records are refused). ``apply`` backs up the
    memory, then writes only when its bytes are still those of the plan.
    ``expected_sha256`` (the "Bộ nhớ logo" page) aborts before planning when
    the memory is no longer the one the caller showed.
    """
    if to not in {PLATFORM_MEMORY_CLASS, STUDIO_MEMORY_CLASS}:
        raise ValueError("to must be platform_logo or studio_logo")
    entry_platform = platform_entry(platform) if platform else None
    if to == PLATFORM_MEMORY_CLASS and entry_platform is None:
        raise ValueError(f"platform must be one of {', '.join(PLATFORMS)}")
    root = root.resolve(strict=True)
    report: dict[str, Any] = {"dry_run": not apply, "to": to, "platform": entry_platform, "records": [],
                              "backup": None, "converted": 0, "automatic_edit": False}
    path, snapshot = memory_snapshot(root)
    if snapshot is None:
        report["error"] = "memory_missing"
        return report
    report["memory_sha256"] = hashlib.sha256(snapshot).hexdigest()
    if expected_sha256 is not None and report["memory_sha256"] != expected_sha256:
        report.update(aborted="memory_changed", message=MEMORY_CHANGED_MESSAGE)
        return report
    payload = _parse_studio_logo_memory(snapshot)
    records = {str(record.get("key")): record for record in payload["records"] if isinstance(record, dict)}
    plans = []
    for key in dict.fromkeys(keys):  # a repeated key would flip its record twice
        plan = convert_plan(root, records.get(key), to)
        entry: dict[str, Any] = {"key": key, "status": plan["status"], "reason": plan["reason"]}
        derived = plan.get("derived")
        if derived:
            entry.update(logo_frames=len(derived["logo_frame_times"]), box_share=derived.get("box_share"),
                         blur_region=derived["blur_region"])
        report["records"].append(entry)
        if plan["status"] == "convertible":
            plans.append((records[key], entry, derived))
    if apply and plans:
        if not memory_unchanged(path, snapshot):
            report.update(aborted="memory_changed", message=MEMORY_CHANGED_MESSAGE)
            return report
        report["backup"] = backup_memory(root, snapshot).relative_to(root).as_posix()
        if not memory_unchanged(path, snapshot):  # a writer landed during the backup
            report.update(aborted="memory_changed", message=MEMORY_CHANGED_MESSAGE)
            return report
        for record, entry, derived in plans:
            flip_record_class(record, to, platform=entry_platform, derived=derived, actor=actor)
            entry["status"] = "converted"
            report["converted"] += 1
        _write_studio_logo_memory(root, payload)
    return report


def _seed_folder(sha256: str, start: float, end: float) -> str:
    name = re.sub(r"[^A-Za-z0-9_-]", "_", f"seed-{start:.3f}-{end:.3f}")
    return (STUDIO_LOGO_FRAMES_PATH / f"{sha256[:16]}-{name}").as_posix()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def seed_platform_logo(
    root: Path, *, video: Path, start: float, end: float, platform: str, apply: bool = False,
    ffmpeg_path: Path | None = None, ffprobe_path: Path | None = None, actor: str = "platform-logo-seed",
) -> dict[str, Any]:
    """Build a platform-logo record from any video window (e.g. an export's ending ident).

    The window is decoded like the visual-logo scanner (cpu, 320 px, native
    rate). Dry run by default; ``apply`` backs up the memory, writes the frame
    JPEGs and the record only when the memory bytes are still those read first.
    """
    entry_platform = platform_entry(platform)
    if entry_platform is None:
        raise ValueError(f"platform must be one of {', '.join(PLATFORMS)}")
    if not 0 <= float(start) < float(end):
        raise ValueError("start must be before end")
    root = root.resolve(strict=True)
    video = Path(video).resolve(strict=True)
    ffmpeg_path = ffmpeg_path or root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    report: dict[str, Any] = {"dry_run": not apply, "platform": entry_platform, "video": str(video),
                              "window": [float(start), float(end)], "backup": None, "seeded": False,
                              "automatic_edit": False}
    path, snapshot = memory_snapshot(root)
    report["memory_sha256"] = hashlib.sha256(snapshot).hexdigest() if snapshot is not None else None
    probe = SourceProbe(video, ffmpeg_path, ffprobe_path)
    frames = probe.frames(float(start), float(end))
    info = probe.info()
    if not frames or info is None:
        report.update(status="refused", reason=probe.reason or "decode_empty")
        return report
    from biliflow.visual_logo_scanner import _jpeg

    sha256 = _file_sha256(video)
    folder = _seed_folder(sha256, float(start), float(end))
    stored, seen = [], set()
    for moment, frame in frames:
        data = _jpeg(frame)
        if data in seen:
            continue
        seen.add(data)
        stored.append((round(float(moment), 3), f"{float(moment):.3f}.jpg", data))
    decoded = [(moment, name, image) for moment, name, data in stored if (image := _decode(data)) is not None]
    derived = derive_platform_region([(moment, image) for moment, _, image in decoded])
    report.update(source_sha256=sha256, decoded_frames=len(frames), stored_frames=len(stored),
                  logo_frames=len(derived["logo_frame_times"]), box_share=derived.get("box_share"),
                  blur_region=derived["blur_region"])
    if derived["reason"] is not None:
        report.update(status="refused", reason=derived["reason"])
        return report
    width, height = info["analysis_size"]
    prepared = {
        "folder": folder, "stored": stored,
        "frames": _sign_studio_logo_frames(
            [(moment, f"{folder}/{name}", image) for moment, name, image in decoded], []),
        "ignored_regions": [], "mask_refused": None, "frames_source": "source_video", "frames_reason": None,
        "pipeline": {"analysis_size": [width, height], "scale": "bilinear", "jpeg_quality": STUDIO_LOGO_JPEG_QUALITY,
                     "decoder": "cpu", "fps": round(info["rate"], 6), "source_fps": round(info["rate"], 6),
                     "self_checked_previews": 0},
        "window": [round(float(start), 3), round(float(end), 3)],
    }
    key = f"{sha256}:seed-{float(start):.3f}-{float(end):.3f}"
    record = {
        "key": key, "source_sha256": sha256, "review_item_id": None, "decision": "BLUR",
        "memory_class": PLATFORM_MEMORY_CLASS, "platform": entry_platform,
        "labels": [f"Logo nền tảng {entry_platform['name']}"], "candidate_type": PLATFORM_MEMORY_CLASS,
        "review_kind": PLATFORM_MEMORY_CLASS, "start_seconds": float(start), "end_seconds": float(end),
        "signatures": [], "window_text": {"covered": False, "texts": []},
        "blur_region": derived["blur_region"], "logo_frame_times": list(derived["logo_frame_times"]),
        "seeded_from": {"path": str(video), "sha256": sha256, "start": float(start), "end": float(end)},
        "created_at": _now(), "confirmed_by": "user", "created_by": actor, "automatic_edit": False,
    }
    report.update(status="seedable", key=key)
    if not apply:
        return report
    if not memory_unchanged(path, snapshot):
        report.update(status="aborted", reason="memory_changed")
        return report
    payload = _parse_studio_logo_memory(snapshot) if snapshot is not None else load_studio_logo_memory(root)
    if any(isinstance(value, dict) and value.get("key") == key for value in payload["records"]):
        report.update(status="refused", reason="already_seeded")
        return report
    if snapshot is not None:
        report["backup"] = backup_memory(root, snapshot).relative_to(root).as_posix()
    written = _write_fresh_frames(root, prepared)
    record.update(_studio_logo_v2_fields(written))
    payload["records"].append(record)
    try:
        _write_memory_or_discard(root, path, snapshot, payload, written)
    except MemoryChanged:
        report.update(status="aborted", reason="memory_changed")
        return report
    report.update(status="seeded", seeded=True)
    return report
