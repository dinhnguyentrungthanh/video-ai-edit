"""Files of "Lưu trữ": the archive manifest and moves that never overwrite (batch 4).

Shared by ``source_archive`` (archive) and ``source_archive_restore`` (restore
and the startup reconciliation). Nothing here deletes or rewrites a file in
``archive/``: a manifest is written to its own ``.tmp`` (exclusive create,
fsync) before the source moves and gets its free final name only when the
archive succeeded (a rollback removes only that ``.tmp``), so a manifest never
describes a file the archive does not hold; every move is ``os.rename`` after
an ``lexists`` check, never a copy and never over an existing file.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from biliflow import recycle_bin, source_cleanup
from biliflow.export_guards import review_summary


MANIFEST_NAME = "archive-manifest.json"
MANIFEST_SCHEMA_VERSION = 1
# Room for a numbered manifest name ("archive-manifest-12.json").
MANIFEST_NAME_ROOM = len("archive-manifest-999.json")
_ITEM_FIELDS = ("id", "category", "start_seconds", "end_seconds", "detected_interval", "decided_at")
# What ``presence`` sees at a path.
PRESENT, ABSENT, OTHER, UNREADABLE = "present", "absent", "other", "unreadable"


def presence(path: Any, size: Any) -> str:
    """PRESENT (a regular file, not a link, of exactly ``size`` bytes), ABSENT (nothing there),
    OTHER (something else is there) or UNREADABLE (lstat failed for another reason, e.g. a
    sharing violation: nothing may be decided on it)."""
    try:
        info = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return ABSENT
    except (OSError, TypeError, ValueError):
        return UNREADABLE
    if not stat.S_ISREG(info.st_mode) or size is None or info.st_size != int(size):
        return OTHER
    return PRESENT


def file_with_size(path: Any, size: Any) -> bool:
    """A regular file (not a link) of exactly ``size`` bytes."""
    return presence(path, size) == PRESENT


def is_link(path: Path) -> bool:
    """A symlink or any other reparse point (a junction)."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return os.path.islink(path) or bool(
        getattr(info, "st_file_attributes", 0) & recycle_bin.FILE_ATTRIBUTE_REPARSE_POINT
    )


def device(path: Path) -> int:
    return int(os.stat(path).st_dev)


def rename_without_overwrite(source: Path, target: Path) -> None:
    """os.rename that never replaces a file (Windows refuses anyway; checked first everywhere)."""
    if os.path.lexists(target):
        raise FileExistsError(errno.EEXIST, "File exists", str(target))
    os.rename(source, target)


def _decisions(queue: dict[str, Any]) -> list[dict[str, Any]]:
    """Every decided item of the queue: its interval, region, edge mode, actor, note and time."""
    snapshot = []
    for group in ("items", "advisory_items"):
        for entry in queue.get(group) or []:
            if not isinstance(entry, dict) or (group == "advisory_items" and entry.get("decision") is None):
                continue
            value = {key: entry[key] for key in _ITEM_FIELDS if key in entry}
            value.update({key: entry[key] for key in entry if key.startswith("decision")})
            value["advisory"] = group == "advisory_items"
            snapshot.append(value)
    return snapshot


def _edit_plan(root: Path, value: Any) -> dict[str, Any]:
    if not value:
        return {"path": None, "missing": True}
    path = root / str(value)
    try:
        if not source_cleanup._under(path.resolve(), root.resolve()) or not path.is_file():
            return {"path": str(value), "missing": True}
        return {"path": str(value), "missing": False, "content": json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return {"path": str(value), "missing": True, "unreadable": True}


def snapshot(root: Path, store: Any, job: dict[str, Any], item: Any, checked: dict[str, Any]) -> dict[str, Any]:
    """The archive manifest of ``item`` (an ``ArchiveAssessment``) without its id.

    Read under REVIEW_QUEUE_IO (the caller holds it). ``checked`` is the
    ``source_cleanup.precheck`` result: the hashes and stats of the source and
    the export.
    """
    queue_value = str(job.get("active_queue_path") or "")
    data = (root / queue_value).read_bytes()
    queue = json.loads(data.decode("utf-8"))
    export = None
    if item.kind == "EXPORTED":
        manifest = json.loads((root / str(item.output_manifest_path)).read_text(encoding="utf-8"))
        export = {
            "output_path": item.output_path, "output_sha256": checked["output_sha256"],
            "output_bytes": item.output_bytes, "exported_at": item.exported_at,
            "manifest_path": item.output_manifest_path, "manifest_bytes": item.output_manifest_bytes,
            "manifest": manifest, "edit_plan": _edit_plan(root, manifest.get("edit_plan")),
        }
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION, "kind": "biliflow_source_archive",
        "job": {key: job.get(key) for key in (
            "id", "job_key", "state", "content_style", "profile", "duration_seconds", "active_revision",
            "active_queue_path",
        )},
        "source": {
            "original_path": item.source_path, "archive_path": item.archive_path,
            "file_name": Path(item.source_path).name, "sha256": checked["sha256"],
            "size_bytes": item.size_bytes, "mtime_ns": int(checked["stat"].st_mtime_ns),
            "duration_seconds": job.get("duration_seconds"),
        },
        "review": {
            "queue_path": queue_value, "revision": job.get("active_revision"),
            "queue_sha256": hashlib.sha256(data).hexdigest(), "queue_bytes": len(data),
            "status": queue.get("status"), "export_size_policy": queue.get("export_size_policy"),
            "summary": review_summary(queue), "decisions": _decisions(queue),
        },
        "export": export,
        "skip": store.setting(f"skip:{int(job['id'])}"),
        "render_request": store.setting(f"render:{int(job['id'])}"),
    }


def free_manifest_path(folder: Path) -> Path:
    """``archive-manifest.json`` or the next free numbered name: nothing in archive/ is rewritten."""
    candidate, number = folder / MANIFEST_NAME, 2
    while os.path.lexists(candidate) or os.path.lexists(manifest_temp(candidate)):
        candidate, number = folder / f"archive-manifest-{number}.json", number + 1
    return candidate


def manifest_temp(path: Any) -> Path:
    """The unfinished twin of a manifest path: ``<name>.tmp``."""
    return Path(path).with_name(Path(path).name + ".tmp")


def write_manifest_temp(path: Path, document: dict[str, Any]) -> Path:
    """Write ``document`` to a new ``.tmp`` beside ``path`` (exclusive create, fsync); returns it.

    ``publish_manifest`` gives it the final name once the archive succeeded;
    ``discard_manifest_temp`` removes it when the source is (back) in input/,
    so no manifest describes a file the archive does not hold.
    """
    temp = manifest_temp(path)
    data = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
    created = False
    try:
        with open(temp, "xb") as handle:  # exclusive: never over an existing file
            created = True
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        if created:
            discard_manifest_temp(temp)
        raise
    return temp


def publish_manifest(temp: Path, path: Path) -> None:
    """Give a written manifest ``.tmp`` its final, free name (never over a file)."""
    rename_without_overwrite(temp, path)


def discard_manifest_temp(temp: Path) -> None:
    """Remove this module's own unfinished manifest ``.tmp``, never any other file."""
    name = Path(temp).name
    if not (name.startswith(MANIFEST_NAME.removesuffix(".json")) and name.endswith(".json.tmp")):
        raise ValueError(f"Not an archive manifest .tmp: {temp}")
    try:
        os.remove(temp)
    except OSError:
        pass
