"""Xóa video: remove a cancelled job, or a job whose source is gone, from BiliFlow.

The user's decisions of 2026-10-05 (docs/DELETE_FLOW_PLAN.md):

- CANCELLED: a cancelled job whose source is still at its path in input/.
  The source is deleted for good after the SHA-256 check (as "Xóa video
  gốc" does), then the job.
- LOST: a job whose source is no longer at its recorded path and is not in
  the archive (any state that is not running, queued or exporting). Only the
  job goes. "Dọn video mất gốc" is this action on every such job at once.

Nothing in output/ is touched. Removing a job is ``job_purge.remove_job``
(rows, its own report folders, its logs), files first and rows last.
Golden-set jobs are refused, and so is a job whose source the old "Dọn video
gốc" moved to the Recycle Bin while the file is still there (restoring it
would bring the job back). Shares SOURCE_FILE_LOCK and the lock order of
source_cleanup; never imports control_center or scheduler.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from biliflow import job_purge, recycle_bin, source_cleanup
from biliflow.export_guards import REVIEW_QUEUE_IO, render_in_flight
from biliflow.export_identity import aware_datetime
from biliflow.job_store import IN_PROCESS_STATES, SOURCE_ARCHIVED_STATES, sha256_file
from biliflow.source_cleanup import SOURCE_FILE_LOCK, CleanupConflict, parse_job_ids

KIND_CANCELLED = "CANCELLED"
KIND_LOST = "LOST"
_PREVIEW_ID = re.compile(r"[0-9a-f]{64}")
_UNSET = object()

PREVIEW_ID_MESSAGE = "Thiếu mã xem trước; hãy mở lại hộp thoại xóa video."
BUSY_MESSAGE = "Đang xóa, lưu trữ hoặc khôi phục video; chờ lượt trước xong rồi thử lại."
NOTHING_ELIGIBLE_MESSAGE = "Không có video nào xóa được trong danh sách đã chọn."
REASON_NOT_DELETABLE = "Chỉ xóa được video đã hủy hoặc video không còn video gốc"
REASON_IN_BIN = (
    "Video gốc đã dọn vào Thùng rác trước đây vẫn còn trong Thùng rác; khôi phục nó về input "
    "hoặc xóa nó khỏi Thùng rác trước"
)
REASON_BIN_UNREADABLE = "Không đọc được Thùng rác để biết video gốc đã dọn trước đây còn ở đó không; thử lại sau"
DELETED_LOST_MESSAGE = "Đã xóa video khỏi BiliFlow"
LOST_PARTIAL_MESSAGE = (
    "Còn dữ liệu của video chưa xóa được ({errors}). Video vẫn có trong danh sách; "
    "đóng file đang mở rồi bấm “Xóa video” lại."
)


@dataclass(frozen=True)
class DeleteAssessment:
    job_id: int
    name: str
    state: str
    kind: str | None  # CANCELLED, LOST, or None for a job "Xóa video" does not apply to
    eligible: bool
    reason: str | None
    source_path: str
    size_bytes: int  # what deleting the source frees (0 for a lost one)
    mtime_ns: int | None
    # What source_cleanup.precheck reads (this action never has an export).
    output_path: None = None
    output_sha256: None = None


# --------------------------------------------------------------------------
# Eligibility (never hashes).


def _record_present(record: str) -> bool:
    """A bin record (``$I…``) whose file (``$R…``) is still in the Recycle Bin."""
    folder, name = os.path.split(record)
    if not name.upper().startswith("$I"):
        return False
    return os.path.lexists(record) and os.path.lexists(os.path.join(folder, "$R" + name[2:]))


def _bin_refusal(row: dict[str, Any], check: dict[str, Any] | None, finder: Callable | None) -> str | None:
    """Why a job the old cleanup recycled must stay: its source may still be in the Recycle Bin.

    A known record (the cleanup's, or one a re-check found) answers by itself.
    Otherwise ``finder`` (recycle_bin.find_recycle_record) looks for the same
    path and size; None skips that read (status hints, the locked recheck).
    """
    known = [value for value in (row.get("recycle_record"), (check or {}).get("record")) if value]
    if any(_record_present(str(record)) for record in known):
        return REASON_IN_BIN
    if known or finder is None:
        return None
    path, size = str(row.get("source_path") or ""), row.get("size_bytes")
    if not path or size is None:
        return REASON_BIN_UNREADABLE
    moment = aware_datetime(row.get("created_at"))
    since = moment.timestamp() - 5 if moment is not None else 0.0
    try:
        found = finder(Path(path).anchor, path, int(size), since=since)
    except Exception:  # noqa: BLE001 - an unreadable bin never lets the job go
        return REASON_BIN_UNREADABLE
    return REASON_IN_BIN if found else None


def assess_delete(
    root: Any, store: Any, scheduler: Any, job: dict[str, Any], *,
    latest_row: Any = _UNSET, archive_row: Any = _UNSET, check: Any = _UNSET,
    finder: Callable | None = None, audit_running: Callable[[int], bool] | None = None,
    index: job_purge.GoldenIndex | None = None,
) -> DeleteAssessment:
    """Whether "Xóa video" may remove ``job`` now, and why not. Never hashes.

    ``latest_row`` / ``archive_row`` are the job's latest source_cleanups and
    source_archives rows and ``check`` the re-check summary of that cleanup
    row; omitted, each is read from the store.
    """
    root = Path(root)
    job_id = int(job["id"])
    state = str(job.get("state") or "")
    raw = str(job.get("source_path") or "")
    name = (Path(raw).name if raw else "") or str(job.get("job_key") or "")
    try:
        resolved = str(Path(raw).resolve()) if raw else ""
    except (OSError, RuntimeError):
        resolved = os.path.abspath(raw)
    values: dict[str, Any] = {
        "job_id": job_id, "name": name, "state": state, "source_path": resolved,
        "size_bytes": 0, "mtime_ns": None,
    }
    if latest_row is _UNSET:
        latest_row = store.latest_source_cleanup(job_id)
    if archive_row is _UNSET:
        archive_row = store.latest_source_archive(job_id)

    def refused(reason: str, kind: str | None) -> DeleteAssessment:
        return DeleteAssessment(kind=kind, eligible=False, reason=reason, **values)

    if isinstance(archive_row, dict) and archive_row.get("state") in SOURCE_ARCHIVED_STATES:
        return refused(source_cleanup.REASON_ARCHIVED, None)  # in archive/, not lost
    present = bool(raw) and os.path.lexists(raw)
    if present and state != "CANCELLED":
        return refused(REASON_NOT_DELETABLE, None)
    kind = KIND_CANCELLED if present else KIND_LOST
    protected = job_purge.protected_reason(root, job, index=index)
    if protected:
        return refused(protected, kind)
    row_state = latest_row.get("state") if isinstance(latest_row, dict) else None
    if row_state == "PENDING":
        return refused(source_cleanup.REASON_PENDING, kind)
    busy = bool(scheduler.is_busy(job_id))
    pending = store.next_pending_stage(job_id) if state == "QUEUED" else None
    if (
        busy or state == "QUEUED" or state in IN_PROCESS_STATES
        or render_in_flight(job, (pending or {}).get("name"), worker_busy=busy)
    ):
        return refused(source_cleanup.REASON_BUSY, kind)
    if store.render_request(job_id) is not None:
        return refused(source_cleanup.REASON_RENDER_REQUEST, kind)
    if audit_running is not None and audit_running(job_id):
        return refused(source_cleanup.REASON_AUDIT, kind)
    if kind == KIND_LOST:
        if row_state == "RECYCLED":
            if check is _UNSET:
                check = store.recycle_check_summary("SOURCE_CLEANUP").get(int(latest_row["id"]))
            reason = _bin_refusal(latest_row, check, finder)
            if reason:
                return refused(reason, kind)
        return DeleteAssessment(kind=kind, eligible=True, reason=None, **values)
    # A cancelled job's source goes for good: the checks of "Xóa video gốc".
    input_root = root / "input"
    if not source_cleanup._under(Path(resolved), input_root.resolve()):
        return refused(source_cleanup.REASON_SOURCE_OUTSIDE, kind)
    # The unresolved path, so that a link or junction (or a folder) is seen and refused.
    refusal = recycle_bin.path_refusal(os.path.abspath(raw), allowed_root=input_root, wording=job_purge.DELETE_WORDING)
    if refusal:
        return refused(refusal, kind)
    try:
        info = os.lstat(raw)
    except OSError:
        return refused(source_cleanup.REASON_SOURCE_MISSING, kind)
    size = job.get("source_size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or int(info.st_size) != size:
        return refused(source_cleanup.REASON_SOURCE_CHANGED, kind)
    values.update(size_bytes=size, mtime_ns=int(info.st_mtime_ns))
    return DeleteAssessment(kind=kind, eligible=True, reason=None, **values)


def delete_hint(root: Any, store: Any, scheduler: Any, job: dict[str, Any], *, latest_row: Any,
                archive_row: Any, check: Any, audit_running: Callable[[int], bool] | None,
                index: job_purge.GoldenIndex | None) -> dict[str, Any] | None:
    """The "Xóa video" hint of a card: only for a cancelled job or one whose source is gone.

    Never hashes and never reads the Recycle Bin (only a known record's files).
    """
    assessment = assess_delete(
        root, store, scheduler, job, latest_row=latest_row, archive_row=archive_row, check=check,
        finder=None, audit_running=audit_running, index=index,
    )
    if assessment.kind is None:
        return None
    return {
        "eligible": assessment.eligible, "kind": assessment.kind, "reason": assessment.reason,
        "size_bytes": assessment.size_bytes,
    }


# --------------------------------------------------------------------------
# Preview.


def _preview_id(eligible: list[DeleteAssessment]) -> str:
    rows = sorted([
        [item.job_id, os.path.normcase(item.source_path), item.size_bytes, item.mtime_ns, item.kind, item.state]
        for item in eligible
    ])
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def _preview(root: Path, store: Any, scheduler: Any, job_ids: Any, *, finder: Callable | None,
             audit_running: Callable[[int], bool] | None) -> tuple[dict[str, Any], list[DeleteAssessment]]:
    ids = parse_job_ids(job_ids)
    latest = store.latest_source_cleanups()
    archives = store.latest_source_archives()
    checks = store.recycle_check_summary("SOURCE_CLEANUP")
    index = job_purge.golden_index(root)
    eligible: list[DeleteAssessment] = []
    ineligible: list[dict[str, Any]] = []
    reports: dict[int, int] = {}
    for job_id in ids:
        try:
            job = store.get_job(job_id)
        except KeyError:
            ineligible.append({
                "job_id": job_id, "name": "", "reason": source_cleanup.UNKNOWN_JOB_MESSAGE.format(job_id=job_id),
            })
            continue
        row = latest.get(job_id)
        assessment = assess_delete(
            root, store, scheduler, job, latest_row=row, archive_row=archives.get(job_id),
            check=checks.get(int(row["id"])) if row else None, finder=finder,
            audit_running=audit_running, index=index,
        )
        if assessment.eligible:
            eligible.append(assessment)
            reports[job_id] = job_purge.files_bytes(job_purge.job_files(root, store, job))
        else:
            ineligible.append({"job_id": job_id, "name": assessment.name, "reason": assessment.reason})
    preview = {
        "preview_id": _preview_id(eligible),
        "eligible": [
            {
                "job_id": item.job_id, "name": item.name, "file_name": Path(item.source_path).name,
                "source_path": item.source_path, "kind": item.kind, "state": item.state,
                "size_bytes": item.size_bytes, "reports_bytes": reports[item.job_id],
            }
            for item in eligible
        ],
        "ineligible": ineligible,
        "count": len(eligible),
        "total_bytes": sum(item.size_bytes for item in eligible),
        "reports_bytes": sum(reports.values()),
    }
    return preview, eligible


def preview_delete(root: Any, store: Any, scheduler: Any, job_ids: Any, *, finder: Callable | None,
                   audit_running: Callable[[int], bool] | None = None) -> dict[str, Any]:
    """What "Xóa video" would remove. Read-only: no hash, no write, no deleter.

    ``finder`` (recycle_bin.find_recycle_record) only reads the Recycle Bin
    for a source the old cleanup moved there without a known record.
    """
    preview, _ = _preview(Path(root), store, scheduler, job_ids, finder=finder, audit_running=audit_running)
    return preview


# --------------------------------------------------------------------------
# Execute.


def _result(item: DeleteAssessment, status: str, message: str) -> dict[str, Any]:
    return {"job_id": item.job_id, "name": item.name, "status": status, "message": message,
            "size_bytes": item.size_bytes}


def _note(store: Any, job_id: int, kind: str, message: str, *, level: str, payload: dict[str, Any]) -> None:
    """An event on a job that stays; a failure to write it never changes the result."""
    try:
        store.add_event(job_id, kind, message, level=level, payload=payload)
    except Exception:  # noqa: BLE001 - e.g. the store was closed at shutdown
        pass


def _failed(store: Any, item: DeleteAssessment, stage: str, reason: str, *, level: str = "WARNING") -> dict[str, Any]:
    _note(store, item.job_id, "JOB_DELETE_FAILED", reason, level=level, payload={"stage": stage, "reason": reason})
    return _result(item, "FAILED", reason)


def _partial(store: Any, item: DeleteAssessment, errors: list[str], *, level: str = "WARNING") -> dict[str, Any]:
    template = source_cleanup.PARTIAL_MESSAGE if item.kind == KIND_CANCELLED else LOST_PARTIAL_MESSAGE
    message = template.format(errors="; ".join(errors))
    _note(store, item.job_id, "JOB_DELETE_PARTIAL", message, level=level, payload={"errors": errors})
    return _result(item, "PARTIAL", message)


def _recheck(root: Path, store: Any, scheduler: Any, item: DeleteAssessment, checked: dict[str, Any] | None,
             audit_running: Callable[[int], bool] | None) -> str | None:
    """Inside REVIEW_QUEUE_IO and job_action_lock: why the video may no longer go, or None.

    The Recycle Bin is not read again here: the preview just did.
    """
    job = store.get_job(item.job_id)
    fresh = assess_delete(root, store, scheduler, job, finder=None, audit_running=audit_running)
    if not fresh.eligible:
        return fresh.reason
    if (fresh.kind, os.path.normcase(fresh.source_path), fresh.size_bytes, fresh.state) != (
        item.kind, os.path.normcase(item.source_path), item.size_bytes, item.state,
    ):
        return source_cleanup.PREVIEW_CHANGED_MESSAGE
    if checked is None:
        return None
    if str(job.get("source_sha256") or "").lower() != checked["sha256"]:
        return source_cleanup.PREVIEW_CHANGED_MESSAGE
    try:
        current = os.stat(item.source_path)
    except OSError:
        return source_cleanup.REASON_SOURCE_MISSING
    if source_cleanup._stat_key(current) != source_cleanup._stat_key(checked["stat"]):
        return source_cleanup.SOURCE_CHANGED_DURING_HASH
    return None


def _delete_one(root: Path, store: Any, scheduler: Any, item: DeleteAssessment, *, deleter: Callable,
                hasher: Callable[[Path], str], audit_running: Callable[[int], bool] | None) -> dict[str, Any]:
    """One video: hash a cancelled job's source with no lock, then re-check and remove under both locks."""
    checked = None
    if item.kind == KIND_CANCELLED:
        job_sha = str(store.get_job(item.job_id).get("source_sha256") or "")
        checked, reason = source_cleanup.precheck(root, item, job_sha, hasher)
        if reason is not None:
            return _failed(store, item, "precheck", reason)
    with REVIEW_QUEUE_IO, scheduler.job_action_lock:
        reason = _recheck(root, store, scheduler, item, checked, audit_running)
        if reason is not None:
            return _failed(store, item, "recheck", reason)
        if item.kind == KIND_CANCELLED:
            try:
                deleter(Path(item.source_path), allowed_root=root / "input", expected_size=int(item.size_bytes))
            except Exception as error:  # noqa: BLE001 - whether the file is still there decides
                if os.path.lexists(item.source_path):
                    known = isinstance(error, (job_purge.DeleteRefused, job_purge.DeleteFailed))
                    message = str(error) if known else source_cleanup.UNEXPECTED_MESSAGE.format(error=error)
                    return _failed(store, item, "delete", message, level="ERROR")
        try:
            errors = job_purge.remove_job(root, store, store.get_job(item.job_id))
        except Exception as error:  # noqa: BLE001 - the job stays (without its source)
            errors = [source_cleanup.UNEXPECTED_MESSAGE.format(error=error)]
    if errors:
        return _partial(store, item, errors)
    if item.kind == KIND_CANCELLED:
        return _result(item, "DELETED", source_cleanup.DELETED_MESSAGE)
    return _result(item, "DELETED", DELETED_LOST_MESSAGE)


def _unexpected(store: Any, item: DeleteAssessment, message: str) -> dict[str, Any]:
    """An error nothing above expected: PARTIAL once a cancelled job's source is gone, else FAILED."""
    if item.kind == KIND_CANCELLED and not os.path.lexists(item.source_path):
        return _partial(store, item, [message], level="ERROR")
    return _failed(store, item, "unexpected", message, level="ERROR")


def execute_delete(
    root: Any, store: Any, scheduler: Any, job_ids: Any, preview_id: Any, *,
    deleter: Callable, finder: Callable | None, hasher: Callable[[Path], str] = sha256_file,
    should_stop: Callable[[], bool] | None = None,
    audit_running: Callable[[int], bool] | None = None,
) -> dict[str, Any]:
    """Remove the confirmed cancelled or lost videos from BiliFlow, one by one.

    ``deleter`` has no default (``job_purge.delete_input_file`` in the Control
    Center). Raises ValueError for a bad request or a root outside the install
    (and its temp/), CleanupConflict ('preview_changed' or 'busy') when nothing
    may start; otherwise every eligible video gets a result: DELETED, PARTIAL
    (a file of the job stayed, so the job stays and can be removed again),
    FAILED (nothing changed) or NOT_RUN (BiliFlow is stopping).
    """
    root = Path(root)
    ids = parse_job_ids(job_ids)
    if not isinstance(preview_id, str) or _PREVIEW_ID.fullmatch(preview_id) is None:
        raise ValueError(PREVIEW_ID_MESSAGE)
    if not job_purge.allowed_project_root(root):
        raise ValueError(job_purge.ROOT_MESSAGE)
    if not SOURCE_FILE_LOCK.acquire(blocking=False):
        raise CleanupConflict("busy", BUSY_MESSAGE)
    try:
        if recycle_bin.operations_in_progress():
            raise CleanupConflict("busy", BUSY_MESSAGE)
        preview, eligible = _preview(root, store, scheduler, ids, finder=finder, audit_running=audit_running)
        if preview["preview_id"] != preview_id:
            raise CleanupConflict("preview_changed", source_cleanup.PREVIEW_CHANGED_MESSAGE, preview)
        if not eligible:
            raise ValueError(NOTHING_ELIGIBLE_MESSAGE)
        results: list[dict[str, Any]] = []
        for item in eligible:
            if should_stop is not None and should_stop():
                results.append(_result(item, "NOT_RUN", source_cleanup.STOPPING_MESSAGE))
                continue
            try:
                result = _delete_one(root, store, scheduler, item, deleter=deleter, hasher=hasher,
                                     audit_running=audit_running)
            except Exception as error:  # noqa: BLE001 - one video never aborts the others
                result = _unexpected(store, item, source_cleanup.UNEXPECTED_MESSAGE.format(error=error))
            results.append(result)
    finally:
        SOURCE_FILE_LOCK.release()
    gone = [item for item in results if item["status"] in ("DELETED", "PARTIAL")]
    return {
        "results": results,
        "deleted_count": sum(1 for item in results if item["status"] == "DELETED"),
        "deleted_bytes": sum(int(item["size_bytes"] or 0) for item in gone),
        "failed_count": sum(1 for item in results if item["status"] == "FAILED"),
        "partial_count": sum(1 for item in results if item["status"] == "PARTIAL"),
    }
