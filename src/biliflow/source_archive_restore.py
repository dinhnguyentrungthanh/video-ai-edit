"""Khôi phục bản xuất and the startup reconciliation of "Lưu trữ" (batch 4).

``restore_archive`` renames an archived source back to its original input
path, only while that path is free and the archived file still has its size
and SHA-256: an exported job returns to the review flow (its old export stays
in the Recycle Bin), a skipped job stays SKIPPED. ``reconcile_pending_archives``
settles, at startup, an archive or a restore that an interruption left
PENDING or RESTORING, without the recycler and without hashing.

Same rules as ``source_archive``: the non-blocking
``source_cleanup.SOURCE_FILE_LOCK`` first, then ``REVIEW_QUEUE_IO`` and
``scheduler.job_action_lock``; no lock while hashing or renaming; nothing in
``archive/`` is deleted or rewritten; never imports ``control_center`` or
``scheduler``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from biliflow import recycle_bin, source_cleanup
from biliflow import source_archive_files as files
from biliflow.export_guards import REVIEW_QUEUE_IO, ActionConflict
from biliflow.job_store import SOURCE_ARCHIVE_PHASES, sha256_file
from biliflow.source_archive import discard_row_temp, fail_archive, roll_back, settle_archived


PHASE_ORDER = {phase: index for index, phase in enumerate(SOURCE_ARCHIVE_PHASES)}

RESTORE_ID_MESSAGE = "Mã video không hợp lệ."
RESTORE_NOT_ARCHIVED_MESSAGE = "Video #{job_id} không có video gốc trong kho lưu trữ để khôi phục."
RESTORE_TARGET_EXISTS_MESSAGE = (
    "Trong input đã có file “{name}”. BiliFlow không ghi đè: dời file đó ra khỏi input rồi bấm "
    "“Khôi phục bản xuất” lại."
)
RESTORE_NO_INPUT_MESSAGE = "Không thấy thư mục “{folder}”; không khôi phục được."
RESTORE_MISSING_MESSAGE = "Không thấy video gốc “{name}” đúng dung lượng trong kho lưu trữ; không khôi phục được."
RESTORE_CHANGED_MESSAGE = "Video gốc “{name}” trong kho lưu trữ không còn khớp SHA-256 đã ghi; không khôi phục."
RESTORE_READ_MESSAGE = (
    "Không đọc được video gốc “{name}” trong kho lưu trữ để kiểm tra SHA-256 ({error}); chưa khôi phục."
)
RESTORE_MOVE_MESSAGE = "Không đưa được video gốc về input: {error}"
RESTORE_INTERRUPTED_MESSAGE = "Lần khôi phục trước bị gián đoạn; video gốc vẫn ở kho lưu trữ."
RESTORED_EXPORT_MESSAGE = (
    "Đã đưa video gốc của #{job_id} về input (SHA-256 khớp). Video về mục “Đang chờ duyệt” để "
    "xuất lại; bản xuất cũ vẫn ở Thùng rác."
)
RESTORED_SKIP_MESSAGE = (
    "Đã đưa video gốc của #{job_id} về input (SHA-256 khớp). Video vẫn ở mục “Hoàn tất” (đã bỏ qua)."
)

# The startup reconciliation of an interrupted archive.
INTERRUPTED_MESSAGE = "Bị gián đoạn trước khi lưu trữ; video gốc vẫn còn trong input."
INTERRUPTED_ROLLBACK_MESSAGE = "Bị gián đoạn trước khi lưu trữ xong; đã đưa video gốc về input."
UNKNOWN_PLACE_MESSAGE = (
    "Bị gián đoạn và không xác định được video gốc nằm ở input hay kho lưu trữ; "
    "hãy kiểm tra cả hai thư mục."
)
MANIFEST_INTERRUPTED_MESSAGE = "Bị gián đoạn; manifest của bản xuất vẫn còn trong output."
EXPORT_STILL_THERE_MESSAGE = "Bản xuất vẫn còn trong output dù đã ghi là đã vào Thùng rác."
# Nothing is settled on a guess: the row keeps its lock and the next start looks again.
NOWHERE_MESSAGE = (
    "Bị gián đoạn và không thấy video gốc ở input lẫn kho lưu trữ; video vẫn bị khóa. Hãy tìm lại file đó "
    "(BiliFlow xem lại mỗi lần khởi động)."
)
UNREADABLE_MESSAGE = (
    "Bị gián đoạn và không đọc được file để biết video gốc đang ở đâu; chưa xử lý gì, video vẫn bị khóa. "
    "BiliFlow xem lại khi khởi động lần sau."
)


# --------------------------------------------------------------------------
# "Khôi phục bản xuất".


def _restored_state(root: Path, store: Any, row: dict[str, Any]) -> str | None:
    """The state an exported job takes back (from its queue); None keeps a skipped job SKIPPED."""
    if row["kind"] != "EXPORTED":
        return None
    queue_value = str(store.get_job(int(row["job_id"])).get("active_queue_path") or "")
    try:
        with REVIEW_QUEUE_IO:
            status = json.loads((root / queue_value).read_text(encoding="utf-8")).get("status")
    except (OSError, ValueError, AttributeError):
        status = None
    return "READY_TO_EXPORT" if status == "READY_FOR_EDIT_PLAN" else "WAITING_REVIEW"


def _finish_restore(root: Path, store: Any, row: dict[str, Any]) -> dict[str, Any] | None:
    """The source is back at its input path: RESTORED, job state, watcher row and event."""
    source = Path(row["source_path"])
    info = os.stat(source)
    finished = store.finish_archive_restore(int(row["id"]), mtime_ns=info.st_mtime_ns,
                                            job_state=_restored_state(root, store, row))
    if finished is None:
        return None
    job_id = int(row["job_id"])
    # The file is known: the watcher neither re-hashes it nor makes a new job.
    store.observe_file(source, info.st_size, info.st_mtime_ns)
    store.mark_file_imported(source, job_id)
    message = (RESTORED_EXPORT_MESSAGE if row["kind"] == "EXPORTED" else RESTORED_SKIP_MESSAGE).format(job_id=job_id)
    store.add_event(job_id, "SOURCE_ARCHIVE_RESTORED", message,
                    payload={"archive_id": row["id"], "path": str(source), "archive_path": row["archive_path"],
                             "size_bytes": info.st_size, "sha256": row["source_sha256"]})
    return finished


def _abort_restore(store: Any, row: dict[str, Any], message: str, *, level: str = "WARNING") -> None:
    store.abort_archive_restore(int(row["id"]), error=message)
    store.add_event(int(row["job_id"]), "SOURCE_ARCHIVE_RESTORE_FAILED", message, level=level,
                    payload={"archive_id": row["id"], "reason": message})


def _check_copy(store: Any, row: dict[str, Any], target: Path, hasher: Callable[[Path], str]) -> None:
    """No lock: the archived file still has its size and SHA-256; aborts the restore otherwise."""
    try:
        same = (str(hasher(target)).lower() == str(row["source_sha256"]).lower()
                and files.file_with_size(target, row["size_bytes"]))
    except OSError as error:
        message = RESTORE_READ_MESSAGE.format(name=target.name, error=error)
        _abort_restore(store, row, message)
        raise ActionConflict("restore_failed", message) from error
    if not same:
        message = RESTORE_CHANGED_MESSAGE.format(name=target.name)
        _abort_restore(store, row, message)
        raise ActionConflict("archive_changed", message)


def restore_archive(root: Any, store: Any, scheduler: Any, job_id: Any, *,
                    hasher: Callable[[Path], str] = sha256_file) -> dict[str, Any]:
    """Rename the archived source back to its input path ("Khôi phục bản xuất").

    Only while the latest row is ARCHIVED, the input path is free and the
    archived file has its size and SHA-256. An exported job becomes
    READY_TO_EXPORT (or WAITING_REVIEW, from its queue) to export again; a
    skipped job stays SKIPPED. ValueError for a bad id, KeyError for an
    unknown job, ActionConflict ('busy', 'not_archived', 'target_exists',
    'input_missing', 'archive_missing', 'archive_changed', 'restore_failed')
    otherwise.
    """
    root = Path(root)
    if isinstance(job_id, bool) or not isinstance(job_id, int) or not 1 <= job_id <= source_cleanup.MAX_JOB_ID:
        raise ValueError(RESTORE_ID_MESSAGE)
    if not source_cleanup.SOURCE_FILE_LOCK.acquire(blocking=False):
        raise ActionConflict("busy", source_cleanup.SOURCE_BUSY_MESSAGE)
    try:
        with REVIEW_QUEUE_IO, scheduler.job_action_lock:
            store.get_job(job_id)
            row = store.latest_source_archive(job_id)
            if row is None or row["state"] != "ARCHIVED":
                raise ActionConflict("not_archived", RESTORE_NOT_ARCHIVED_MESSAGE.format(job_id=job_id))
            source, target = Path(row["source_path"]), Path(row["archive_path"])
            if os.path.lexists(source):
                raise ActionConflict("target_exists", RESTORE_TARGET_EXISTS_MESSAGE.format(name=source.name))
            if not source.parent.is_dir():
                raise ActionConflict("input_missing", RESTORE_NO_INPUT_MESSAGE.format(folder=source.parent))
            if not files.file_with_size(target, row["size_bytes"]) or files.is_link(target):
                raise ActionConflict("archive_missing", RESTORE_MISSING_MESSAGE.format(name=target.name))
            if store.begin_archive_restore(int(row["id"])) is None:
                raise ActionConflict("not_archived", RESTORE_NOT_ARCHIVED_MESSAGE.format(job_id=job_id))
        _check_copy(store, row, target, hasher)
        try:
            files.rename_without_overwrite(target, source)
        except FileExistsError:
            message = RESTORE_TARGET_EXISTS_MESSAGE.format(name=source.name)
            _abort_restore(store, row, message)
            raise ActionConflict("target_exists", message) from None
        except OSError as error:
            message = RESTORE_MOVE_MESSAGE.format(error=error)
            _abort_restore(store, row, message)
            raise ActionConflict("restore_failed", message) from error
        with REVIEW_QUEUE_IO, scheduler.job_action_lock:
            _finish_restore(root, store, row)
    finally:
        source_cleanup.SOURCE_FILE_LOCK.release()
    job = store.get_job(job_id)
    message = RESTORED_EXPORT_MESSAGE if row["kind"] == "EXPORTED" else RESTORED_SKIP_MESSAGE
    return {"job_id": job_id, "status": "RESTORED", "state": job["state"], "message": message.format(job_id=job_id)}


# --------------------------------------------------------------------------
# Startup reconciliation (no recycler, no hash).


def _find(finder: Callable, path: Path, size: Any, created_at: Any) -> str | None:
    since = source_cleanup._posix_seconds(created_at)
    if since is None or size is None:
        return None
    try:
        record = finder(path.anchor, str(path), int(size), since=since - 5)
    except Exception:  # noqa: BLE001 - an unreadable bin only means "not verified"
        return None
    return None if record is None else str(record)


def _places(root: Path, row: dict[str, Any]) -> dict[str, str]:
    """``files.presence`` of the row's source, archived copy and (when it has them) export files."""
    size = row["size_bytes"]
    places = {"input": files.presence(Path(row["source_path"]), size),
              "archive": files.presence(Path(row["archive_path"]), size)}
    if row.get("output_path"):
        places["output"] = files.presence(root / str(row["output_path"]), row.get("output_bytes"))
    if row.get("output_manifest_path"):
        places["output_manifest"] = files.presence(root / str(row["output_manifest_path"]),
                                                   row.get("output_manifest_bytes"))
    return places


def _keep_locked(store: Any, row: dict[str, Any], message: str, places: dict[str, str], *, event: str) -> None:
    """Settle nothing: the row stays PENDING or RESTORING (the job stays locked); an ERROR event says why."""
    store.add_event(int(row["job_id"]), event, message, level="ERROR",
                    payload={"stage": "reconcile", "archive_id": row["id"], "places": places})


def _settle_archived_row(root: Path, store: Any, row: dict[str, Any], places: dict[str, str],
                         finder: Callable) -> bool:
    """The source is in archive/ only: roll back an archive that had not reached its export, else settle it."""
    row_id, job_id = int(row["id"]), int(row["job_id"])
    phase = PHASE_ORDER.get(row.get("phase"), 0)
    output_there = places.get("output") == files.PRESENT
    verified = phase >= PHASE_ORDER["SOURCE_VERIFIED"]
    if (row["kind"] != "EXPORTED" and not verified) or (output_there and phase < PHASE_ORDER["EXPORT_RECYCLED"]):
        status, _ = roll_back(store, row_id, job_id, row["source_path"], row["archive_path"],
                              INTERRUPTED_ROLLBACK_MESSAGE, stage="reconcile")
        return status == "FAILED"
    if row["kind"] != "EXPORTED":
        return settle_archived(store, row_id) is not None
    facts: dict[str, Any] = {}
    if not output_there and not row.get("export_recycled"):
        output = root / str(row.get("output_path") or "")
        record = _find(finder, output, row.get("output_bytes"), row.get("created_at"))
        facts.update(export_recycled=True, export_verified=record is not None, export_record=record)
    warning = EXPORT_STILL_THERE_MESSAGE if output_there else None
    if row.get("output_manifest_path") and not row.get("manifest_recycled"):
        if places.get("output_manifest") == files.PRESENT:
            warning = warning or MANIFEST_INTERRUPTED_MESSAGE
        elif phase >= PHASE_ORDER["MANIFEST_RECYCLING"]:
            manifest = root / str(row["output_manifest_path"])
            record = _find(finder, manifest, row.get("output_manifest_bytes"), row.get("created_at"))
            facts.update(manifest_recycled=True, manifest_record=record)
    return settle_archived(store, row_id, warning=warning, **facts) is not None


def _reconcile_archive(root: Path, store: Any, row: dict[str, Any], finder: Callable) -> bool:
    row_id, job_id = int(row["id"]), int(row["job_id"])
    places = _places(root, row)
    in_input, in_archive = places["input"] == files.PRESENT, places["archive"] == files.PRESENT
    if files.UNREADABLE in (places["input"], places["archive"]):
        _keep_locked(store, row, UNREADABLE_MESSAGE, places, event="SOURCE_ARCHIVE_FAILED")
        return False
    if in_input and not in_archive:
        discard_row_temp(store, row_id)
        fail_archive(store, row_id, job_id, "reconcile", INTERRUPTED_MESSAGE)
        return True
    if in_input:
        # In both places: the job may use its input file; the archived copy stays where it is.
        store.finish_source_archive(row_id, state="FAILED", error=UNKNOWN_PLACE_MESSAGE)
        store.add_event(job_id, "SOURCE_ARCHIVE_FAILED", UNKNOWN_PLACE_MESSAGE, level="ERROR",
                        payload={"stage": "reconcile", "archive_id": row_id, "places": places})
        return True
    if not in_archive:
        _keep_locked(store, row, NOWHERE_MESSAGE, places, event="SOURCE_ARCHIVE_FAILED")
        return False
    if files.UNREADABLE in places.values():
        _keep_locked(store, row, UNREADABLE_MESSAGE, places, event="SOURCE_ARCHIVE_FAILED")
        return False
    return _settle_archived_row(root, store, row, places, finder)


def _reconcile_restore(root: Path, store: Any, row: dict[str, Any]) -> bool:
    places = _places(root, {**row, "output_path": None, "output_manifest_path": None})
    if files.UNREADABLE in places.values():
        _keep_locked(store, row, UNREADABLE_MESSAGE, places, event="SOURCE_ARCHIVE_RESTORE_FAILED")
        return False
    in_input, in_archive = places["input"] == files.PRESENT, places["archive"] == files.PRESENT
    if in_input and not in_archive:
        return _finish_restore(root, store, row) is not None
    if in_archive and not in_input:
        _abort_restore(store, row, RESTORE_INTERRUPTED_MESSAGE)
    else:
        _abort_restore(store, row, UNKNOWN_PLACE_MESSAGE, level="ERROR")
    return True


def reconcile_pending_archives(root: Any, store: Any, *, finder: Callable) -> list[int]:
    """Settle the PENDING and RESTORING rows an interruption left; returns the row ids settled.

    Never calls the recycler and never hashes. ``finder`` is
    ``recycle_bin.find_recycle_record``. A row whose export still has a shell
    call running in this process is left alone. Nothing is settled on a guess:
    a file that cannot be read (only "not there" counts as absent), a source in
    neither place, or a rollback that cannot rename the source back leaves the
    row PENDING or RESTORING (the job stays locked) with an ERROR event, and
    the next start looks again. Only an archive that is settled ARCHIVED keeps
    a manifest under its final name; a rolled-back one loses its ``.tmp``.
    """
    root = Path(root)
    running = {os.path.normcase(path) for path in recycle_bin.operations_in_progress()}
    settled: list[int] = []
    for row in store.pending_source_archives():
        paths = [row.get("output_path"), row.get("output_manifest_path")]
        if any(value and os.path.normcase(os.path.abspath(root / str(value))) in running for value in paths):
            continue
        try:
            done = (_reconcile_restore(root, store, row) if row["state"] == "RESTORING"
                    else _reconcile_archive(root, store, row, finder))
        except Exception as error:  # noqa: BLE001 - one row never stops the others
            try:
                store.add_event(int(row["job_id"]), "SOURCE_ARCHIVE_FAILED",
                                source_cleanup.UNEXPECTED_MESSAGE.format(error=error), level="ERROR",
                                payload={"stage": "reconcile", "archive_id": row["id"]})
            except Exception:  # noqa: BLE001
                pass
            continue
        if done:
            settled.append(int(row["id"]))
    return settled
