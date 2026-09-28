from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from biliflow.job_pipeline import safe_job_key
from biliflow.job_store import JobStore, sha256_file
from biliflow.probe import duration_seconds, probe_video


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".m4v", ".webm"}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _queue_sort_value(payload: dict[str, Any], path: Path) -> str:
    return str(payload.get("updated_at") or payload.get("created_at") or path.stat().st_mtime_ns)


def _infer_style(root: Path, queues: list[dict[str, Any]]) -> str:
    styles: set[str] = set()
    for queue in queues:
        for value in queue.get("reports", []):
            report = _read_json(root / str(value))
            style = str((report or {}).get("content_style", ""))
            if style in {"animation", "live_action"}:
                styles.add(style)
    if len(styles) > 1:
        return "mixed"
    if styles:
        return next(iter(styles))
    return "unknown"


def _is_benchmark_queue(path: Path, reports_root: Path) -> bool:
    if path.is_relative_to(reports_root / "benchmarks"):
        return True
    for directory in path.parents:
        if not directory.is_relative_to(reports_root):
            break
        if (directory / ".biliflow-benchmark").is_file():
            return True
    return False


def import_existing_project(root: Path, store: JobStore) -> dict[str, int]:
    """Import source files and historical review queues without modifying them."""
    root = root.resolve(strict=True)
    input_root = (root / "input").resolve(strict=True)
    ffprobe = root / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"
    queue_rows: list[tuple[Path, dict[str, Any]]] = []
    for path in (root / "reports").rglob("review-queue.json"):
        if _is_benchmark_queue(path, root / "reports"):
            continue
        payload = _read_json(path)
        if payload and payload.get("source", {}).get("sha256"):
            queue_rows.append((path, payload))

    by_source_path: dict[Path, list[tuple[Path, dict[str, Any]]]] = {}
    by_sha: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    for path, payload in queue_rows:
        source = Path(str(payload["source"]["path"])).resolve()
        by_source_path.setdefault(source, []).append((path, payload))
        by_sha.setdefault(str(payload["source"]["sha256"]), []).append((path, payload))

    imported = 0
    revisions = 0
    for source in sorted(input_root.iterdir()):
        if not source.is_file() or source.suffix.casefold() not in VIDEO_EXTENSIONS:
            continue
        stat = source.stat()
        matching = by_source_path.get(source.resolve(), [])
        if matching:
            source_hash = str(matching[0][1]["source"]["sha256"])
            duration = float(matching[0][1]["source"].get("duration_seconds") or 0) or None
        else:
            source_hash = sha256_file(source)
            known = by_sha.get(source_hash, [])
            matching = known
            duration = None
        if duration is None:
            duration = duration_seconds(probe_video(ffprobe, source))
        matching.sort(key=lambda item: _queue_sort_value(item[1], item[0]))
        style = _infer_style(root, [item[1] for item in matching]) if matching else "unknown"
        state = "NEEDS_METADATA" if style == "unknown" else "DISCOVERED"
        job = store.upsert_job(
            job_key=safe_job_key(source, source_hash), source_path=source,
            source_sha256=source_hash, source_size_bytes=stat.st_size,
            source_mtime_ns=stat.st_mtime_ns, duration_seconds=duration,
            content_style=style, state=state,
        )
        if style != "unknown" and job["content_style"] != style:
            job = store.update_job(job["id"], content_style=style)
        # Historical queues already identify these paths. Avoid hashing every
        # old video again after 60 seconds; preflight/final render still verify
        # the checksum before any new processing or output.
        store.observe_file(source, stat.st_size, stat.st_mtime_ns)
        store.mark_file_imported(source, int(job["id"]))
        imported += 1
        active_revision = None
        for queue_path, payload in matching:
            relative = queue_path.resolve().relative_to(root).as_posix()
            revision = store.add_revision(
                job["id"], relative, payload.get("status"), payload.get("updated_at"),
            )
            store.add_artifact(
                job["id"], stage_name="import", kind="review_queue",
                path=relative, bytes_count=queue_path.stat().st_size,
            )
            active_revision = revision
            revisions += 1
        if active_revision is not None:
            store.activate_revision(job["id"], active_revision)
            active_queue = matching[-1][1]
            next_state = (
                "READY_TO_EXPORT"
                if active_queue.get("status") == "READY_FOR_EDIT_PLAN"
                else "WAITING_REVIEW"
            )
            store.update_job(job["id"], state=next_state, error=None)

        # A verified final manifest wins over queue state.
        for manifest_path in (root / "output").glob("*.manifest.json"):
            manifest = _read_json(manifest_path)
            if not manifest or manifest.get("status") != "COMPLETED":
                continue
            if manifest.get("source", {}).get("sha256") != source_hash:
                continue
            output_value = manifest.get("output", {}).get("path")
            output_path = root / str(output_value) if output_value else manifest_path.with_suffix("")
            if output_path.exists():
                store.add_artifact(
                    job["id"], stage_name="render", kind="final_output",
                    path=output_path.resolve().relative_to(root).as_posix(),
                    sha256=manifest.get("output", {}).get("sha256"),
                    bytes_count=output_path.stat().st_size,
                )
                store.update_job(job["id"], state="COMPLETED", progress=1.0, error=None)
                break

    store.add_event(
        None, "PROJECT_IMPORT", f"Imported {imported} sources and {revisions} queue revisions",
        payload={"sources": imported, "revisions": revisions},
    )
    return {"sources": imported, "revisions": revisions}
