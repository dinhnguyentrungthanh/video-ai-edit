"""Dọn video gốc: move exported or skipped source videos to the Windows Recycle Bin.

A user-clicked, per-video cleanup for jobs in the Dashboard's "Hoàn tất" tab
(COMPLETED or SKIPPED). It decides eligibility without hashing (status hints
and the preview), then for each confirmed video:

1. without any lock: hash the source (and, for an export, the output) and
   compare the stat before and after;
2. under ``REVIEW_QUEUE_IO`` then ``scheduler.job_action_lock`` (the only lock
   order): re-assess from fresh reads, compare the stat again, check the bin's
   capacity, and write a PENDING ``source_cleanups`` row, which from then on
   locks every action on the job;
3. without any lock again: call the injected ``recycler`` (the shell can take
   up to 60 s) and settle the row as RECYCLED or FAILED, or leave it PENDING
   for the late callback or for ``reconcile_pending_cleanups`` at startup.

There is no default recycler anywhere: ``execute_cleanup`` must be given one,
so a test or a half-built server can never reach the real Recycle Bin. This
module only reads queues, manifests and edit plans. It never writes reports,
output, work, brand/studio memory or review decisions, and never imports
``control_center`` or ``scheduler`` (the scheduler object is passed in).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from biliflow import recycle_bin
from biliflow.export_guards import (
    REVIEW_QUEUE_IO,
    ActionConflict,
    render_in_flight,
    review_summary,
    skip_refusal,
)
from biliflow.export_identity import (
    PROBLEM_CHANGED,
    PROBLEM_DECODE,
    PROBLEM_MANIFEST,
    PROBLEM_NO_OPERATIONS,
    PROBLEM_OLDER,
    aware_datetime as _aware_datetime,
    export_manifest_path,
    manifest_problem,
    output_candidates,
)
from biliflow.job_store import IN_PROCESS_STATES, SOURCE_ARCHIVED_STATES, now_iso, sha256_file
from biliflow.review_workflow import queue_render_identity


MAX_CLEANUP_JOBS = 50
MAX_JOB_ID = 2**31 - 1
ELIGIBLE_STATES = frozenset({"COMPLETED", "SKIPPED"})
KIND_BY_STATE = {"COMPLETED": "EXPORTED", "SKIPPED": "SKIPPED"}
# The one execute lock (the Control Center has none of its own). Only ever
# taken with acquire(blocking=False): a second click is refused, never queued.
# Batch 4 shares it as SOURCE_FILE_LOCK: "Dọn video gốc", "Lưu trữ",
# "Khôi phục bản xuất" and "Kiểm tra lại Thùng rác" never run at the same time.
_EXECUTE_LOCK = threading.Lock()
SOURCE_FILE_LOCK = _EXECUTE_LOCK
_CACHE_LIMIT = 256
_PREVIEW_ID = re.compile(r"[0-9a-f]{64}")

# Request errors.
JOB_IDS_MESSAGE = "Chọn từ 1 đến 50 video mỗi lần dọn."
PREVIEW_ID_MESSAGE = "Thiếu mã xem trước; hãy mở lại hộp thoại dọn video gốc."
BUSY_MESSAGE = "Đang dọn video gốc; chờ lần dọn trước xong rồi thử lại."
PREVIEW_CHANGED_MESSAGE = "Danh sách đã thay đổi, hãy xem lại."
NOTHING_ELIGIBLE_MESSAGE = "Không có video nào dọn được trong danh sách đã chọn."
UNKNOWN_JOB_MESSAGE = "Không tìm thấy video #{job_id}"

# Why a video cannot be cleaned (first match wins, in this order).
REASON_PENDING = "Đang chuyển video gốc này vào Thùng rác"
REASON_RECYCLED = "Video gốc đã được dọn trước đó"
REASON_STATE = "Chỉ dọn được video đã xuất hoặc đã bỏ qua (mục “Hoàn tất”)"
REASON_BUSY = "Video đang chạy hoặc đang xếp hàng"
REASON_RENDER_REQUEST = "Còn lệnh xuất video chưa xong"
REASON_SOURCE_MISSING = "Video gốc không còn trong thư mục input"
REASON_SOURCE_OUTSIDE = "Video gốc không nằm trong thư mục input"
REASON_SOURCE_CHANGED = "Video gốc đã thay đổi so với lúc quét"
REASON_QUEUE_UNREADABLE = "Không đọc được danh sách duyệt của video"
REASON_OUTPUT_STALE = (
    "Bản xuất hiện có không ứng với lần duyệt mới nhất "
    "(mở “Duyệt cảnh” và xuất lại trước khi dọn)"
)
REASON_NO_OUTPUT = "Chưa có video xuất hợp lệ"
# The current review was exported (its final_output artifact names exactly
# this path) but the file is no longer in output/ (moved or renamed by hand).
REASON_OUTPUT_MOVED = "Không thấy bản xuất trong thư mục output (đã bị dời hoặc đổi tên?)"
REASON_MANIFEST = "Manifest xuất không khớp video gốc"
REASON_DECODE = "Bản xuất chưa qua kiểm tra giải mã toàn bộ"
REASON_OUTPUT_CHANGED = "Video xuất đã thay đổi so với manifest"
# The export found for this review was rendered with other render settings:
# an export made before the operations hash keeps a legacy name that did not
# see every render field (for example another blur edge mode). Exporting again
# renders the current decisions under their own name.
REASON_OUTPUT_OLDER = (
    "Bản xuất hiện có không khớp quyết định duyệt hiện tại (mở “Duyệt cảnh” và xuất lại trước khi dọn)"
)
REASON_SKIP_RECORD = "Bản ghi bỏ qua không ứng với lần duyệt hiện tại"

# Per-video results and events of an execute.
STOPPING_MESSAGE = "BiliFlow đang tắt; video này chưa được dọn."
SOURCE_CHANGED_DURING_HASH = "Video gốc đã thay đổi trong lúc kiểm tra SHA-256"
NOT_RUN_MESSAGE = "Chưa chạy: lần chuyển trước chưa xong."
PENDING_EVENT_MESSAGE = (
    "Windows chưa trả lời; video gốc đang chờ xác nhận chuyển vào Thùng rác"
)
RECYCLED_MESSAGE = "Đã chuyển video gốc vào Thùng rác"
UNVERIFIED_MESSAGE = (
    "Video gốc đã rời thư mục input nhưng không tìm thấy bản ghi trong Thùng rác; "
    "hãy kiểm tra Thùng rác."
)
UNEXPECTED_MESSAGE = "Lỗi không mong đợi: {error}"
INTERRUPTED_MESSAGE = "Bị gián đoạn trước khi chuyển; video gốc vẫn còn."

# Batch 4: an archived source cannot be cleaned (restore it first).
REASON_ARCHIVED = "Video gốc đang ở kho lưu trữ"
# Any action on source files while another one runs (archive, restore, re-check).
SOURCE_BUSY_MESSAGE = (
    "Đang dọn, lưu trữ hoặc khôi phục video gốc; chờ lượt trước xong rồi thử lại."
)
# "Kiểm tra lại Thùng rác" (format {what}: "video gốc" or "bản xuất").
RECHECK_KINDS = {"source_cleanup": "SOURCE_CLEANUP", "archive_export": "ARCHIVE_EXPORT"}
# A record of this row's own move is written before (or, by Windows, a moment after) the row
# settled; one written later than this is a later move of the same path and size (by hand, by
# another job, after a re-export): it never answers for this row.
RECHECK_UNTIL_SLACK_SECONDS = 60
RECHECK_KIND_MESSAGE = "Loại kiểm tra Thùng rác không hợp lệ."
RECHECK_ID_MESSAGE = "Mã bản ghi không hợp lệ."
RECHECK_UNKNOWN_MESSAGE = "Không tìm thấy bản ghi #{subject_id}."
RECHECK_NOT_NEEDED_MESSAGE = "Bản ghi này không còn cần kiểm tra lại Thùng rác."
RECHECK_ALREADY_MESSAGE = "Thùng rác đã có bản ghi của file này; không cần kiểm tra lại."
RECHECK_UNREADABLE_MESSAGE = "Không đọc được Thùng rác; thử lại sau."
RECHECK_FOUND_MESSAGE = "Đã thấy {what} trong Thùng rác của Windows."
RECHECK_MISSING_MESSAGE = (
    "Vẫn chưa thấy {what} trong Thùng rác của Windows; hãy mở Thùng rác để kiểm tra. "
    "BiliFlow không thay đổi gì."
)
RECHECK_ACTOR = "control_center_user"


class CleanupConflict(ActionConflict):
    """The cleanup was not started (HTTP 409); deliberately not a ValueError.

    ``code`` is 'preview_changed', 'bin_capacity', 'bin_unavailable' or 'busy';
    ``preview`` is the fresh preview (None for 'busy').
    """


@dataclass(frozen=True)
class Assessment:
    job_id: int
    name: str
    eligible: bool
    reason: str | None
    kind: str | None
    source_path: str
    size_bytes: int | None
    mtime_ns: int | None
    output_path: str | None
    output_bytes: int | None
    output_sha256: str | None
    exported_at: str | None
    skipped_at: str | None


def parse_job_ids(value: Any) -> list[int]:
    """1..50 distinct job ids, sorted, from a list of ints or an ``'a,b'`` string."""
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
        if any(not part or not part.isascii() or not part.isdigit() or len(part) > 10 for part in parts):
            raise ValueError(JOB_IDS_MESSAGE)
        ids = [int(part) for part in parts]
    elif isinstance(value, (list, tuple)):
        if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
            raise ValueError(JOB_IDS_MESSAGE)
        ids = [int(item) for item in value]
    else:
        raise ValueError(JOB_IDS_MESSAGE)
    if any(not 1 <= item <= MAX_JOB_ID for item in ids):
        raise ValueError(JOB_IDS_MESSAGE)
    unique = sorted(set(ids))
    if not 1 <= len(unique) <= MAX_CLEANUP_JOBS:
        raise ValueError(JOB_IDS_MESSAGE)
    return unique


# --------------------------------------------------------------------------
# Read-only facts, cached by (normcase path, mtime_ns, size).

_MISSING = object()
_CACHE: OrderedDict[tuple, Any] = OrderedDict()
_CACHE_LOCK = threading.Lock()  # leaf lock: never held during IO


def _cache_get(key: tuple) -> Any:
    with _CACHE_LOCK:
        value = _CACHE.get(key, _MISSING)
        if value is not _MISSING:
            _CACHE.move_to_end(key)
        return value


def _cache_put(key: tuple, value: Any) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = value
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_LIMIT:
            _CACHE.popitem(last=False)


def _stat_key(info: os.stat_result) -> tuple[int, int, int, int]:
    return (int(info.st_dev), int(info.st_ino), int(info.st_size), int(info.st_mtime_ns))


def _parse_queue_facts(root: Path, data: bytes) -> dict[str, Any]:
    """What cleanup needs from a review queue (never mutated by callers)."""
    queue = json.loads(data.decode("utf-8"))
    if not isinstance(queue, dict):
        raise ValueError("Review queue is not a JSON object")
    # What an export of the current decisions renders (None when it cannot be
    # built), and where that export may be: its own name, then the legacy one.
    render = queue_render_identity(queue)
    candidates = output_candidates(root, queue, render)
    latest: datetime | None = None
    decided_at_valid = True
    for item in queue.get("items") or []:
        if not isinstance(item, dict) or item.get("decided_at") is None:
            continue
        moment = _aware_datetime(item.get("decided_at"))
        if moment is None:
            decided_at_valid = False
        elif latest is None or moment > latest:
            latest = moment
    return {
        "status": queue.get("status"),
        "output_candidates": candidates,
        "max_decided_at": latest,
        "decided_at_valid": decided_at_valid,
        "render_identity": render,
        "summary": review_summary(queue),
    }


def _queue_facts(root: Path, queue_path: Path, *, fresh: bool) -> dict[str, Any]:
    """Queue facts, read under REVIEW_QUEUE_IO (the queue's writers hold it too)."""
    with REVIEW_QUEUE_IO:
        before = os.stat(queue_path)
        key = ("queue", os.path.normcase(str(queue_path)), int(before.st_mtime_ns), int(before.st_size))
        if not fresh:
            cached = _cache_get(key)
            if cached is not _MISSING:
                return cached
        data = queue_path.read_bytes()
        after = os.stat(queue_path)
    facts = _parse_queue_facts(root, data)
    if (int(after.st_mtime_ns), int(after.st_size)) == key[2:]:
        _cache_put(key, facts)
    return facts


def _json_document(kind: str, path: Path, *, fresh: bool) -> Any:
    """A parsed JSON file (manifest or edit plan), cached by its stat."""
    before = os.stat(path)
    key = (kind, os.path.normcase(str(path)), int(before.st_mtime_ns), int(before.st_size))
    if not fresh:
        cached = _cache_get(key)
        if cached is not _MISSING:
            return cached
    value = json.loads(path.read_bytes().decode("utf-8"))
    after = os.stat(path)
    if (int(after.st_mtime_ns), int(after.st_size)) == key[2:]:
        _cache_put(key, value)
    return value


def _under(path: Path, root: Path) -> bool:
    """``path`` is strictly inside ``root`` (both resolved), compared case-insensitively."""
    child, parent = os.path.normcase(str(path)), os.path.normcase(str(root))
    try:
        return os.path.commonpath([child, parent]) == parent and child != parent
    except ValueError:
        return False


def _folded(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value or "").replace("\\", "/")).casefold()


def _relative(root: Path, path: Path) -> str:
    resolved_root, resolved = root.resolve(), path.resolve()
    try:
        return resolved.relative_to(resolved_root).as_posix()
    except ValueError:
        return resolved.as_posix()


# --------------------------------------------------------------------------
# Eligibility (never hashes).

_UNSET = object()


class _Refused(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _check_plan_names_active_queue(root: Path, job: dict[str, Any], plan_value: Any,
                                   *, fresh: bool) -> None:
    """Refuses an export whose edit plan was built from another review; raises _Refused."""
    if not plan_value:
        return
    plan_path = root / str(plan_value)
    # As written first: resolving a UNC or device path makes Windows contact that machine.
    if not _under(Path(os.path.abspath(plan_path)), Path(os.path.abspath(root))):
        raise _Refused(REASON_OUTPUT_STALE)
    if not _under(plan_path.resolve(), root.resolve()):
        raise _Refused(REASON_OUTPUT_STALE)
    if not plan_path.is_file():
        # A plan removed by the 7-day work cleanup is not a refusal.
        return
    try:
        plan = _json_document("plan", plan_path, fresh=fresh)
    except (OSError, ValueError) as error:
        raise _Refused(REASON_OUTPUT_STALE) from error
    review_queue = plan.get("review_queue") if isinstance(plan, dict) else None
    if not review_queue or _folded(review_queue) != _folded(job.get("active_queue_path")):
        raise _Refused(REASON_OUTPUT_STALE)


_REASON_BY_PROBLEM = {
    PROBLEM_MANIFEST: REASON_MANIFEST,
    PROBLEM_DECODE: REASON_DECODE,
    PROBLEM_CHANGED: REASON_OUTPUT_CHANGED,
    PROBLEM_OLDER: REASON_OUTPUT_OLDER,
}


def _exported_facts(root: Path, store: Any, job: dict[str, Any], facts: dict[str, Any],
                    *, fresh: bool) -> dict[str, Any]:
    """Checks of an EXPORTED video's output, manifest and plan; raises _Refused.

    The export is the first candidate on disk (the review's own name, then
    the legacy name of an export made before the operations hash) that its
    manifest proves; when none does, the refusal of the first one on disk.
    """
    job_id = int(job["id"])
    candidates: list[Path] = facts["output_candidates"]
    present = [output for output in candidates if output.is_file()]
    if not present:
        expected = {_folded(_relative(root, output)) for output in candidates}
        exports = [
            _folded(artifact.get("path")) for artifact in store.artifacts(job_id)
            if artifact.get("kind") == "final_output"
        ]
        if expected.intersection(exports):
            # This review was exported; the file just left output/.
            raise _Refused(REASON_OUTPUT_MOVED)
        raise _Refused(REASON_OUTPUT_STALE if exports else REASON_NO_OUTPUT)
    refusals: list[_Refused] = []
    for output in present:
        try:
            return _proven_output_facts(root, job, facts, output, fresh=fresh)
        except _Refused as refusal:
            refusals.append(refusal)
    raise refusals[0]


def _proven_output_facts(root: Path, job: dict[str, Any], facts: dict[str, Any], output: Path,
                         *, fresh: bool) -> dict[str, Any]:
    """The facts of ``output`` when its manifest proves it renders the review; raises _Refused."""
    try:
        manifest = _json_document("manifest", export_manifest_path(output), fresh=fresh)
    except (OSError, ValueError) as error:
        raise _Refused(REASON_MANIFEST) from error
    # The renderer records the operations it applied: the export matches the
    # review when the current decisions would render the same ones.
    # Re-recording a decision (an undo, a misclick) moves decided_at but
    # changes nothing here, and finalize then keeps the existing export. The
    # same holds for a rerun whose new revision has the same items and
    # decisions: the export is the same, and the old edit plan still names the
    # previous queue, so the plan check does not apply here.
    problem = manifest_problem(
        root, output, manifest, source_sha256=job.get("source_sha256"), render=facts["render_identity"],
        source_path=job.get("source_path"),
    )
    if problem == PROBLEM_NO_OPERATIONS:
        # A manifest without operations: fall back to the timestamps and the plan.
        created_at = _aware_datetime(manifest["created_at"])
        latest = facts["max_decided_at"]
        if not facts["decided_at_valid"] or (latest is not None and latest > created_at):
            raise _Refused(REASON_OUTPUT_OLDER)
        _check_plan_names_active_queue(root, job, manifest.get("edit_plan"), fresh=fresh)
    elif problem is not None:
        raise _Refused(_REASON_BY_PROBLEM[problem])
    described = manifest["output"]
    return {
        "output_path": _relative(root, output),
        "output_bytes": int(described["bytes"]),
        "output_sha256": str(described["sha256"]).lower(),
        "exported_at": str(manifest["created_at"]),
    }


def _skipped_facts(store: Any, job: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    """Checks of a SKIPPED video's skip record against the current review; raises _Refused."""
    record = store.setting(f"skip:{int(job['id'])}")
    summary = facts["summary"]
    valid = (
        isinstance(record, dict)
        and record.get("queue_path") == job.get("active_queue_path")
        and record.get("revision") == job.get("active_revision")
        and facts["status"] == "READY_FOR_EDIT_PLAN"
        and skip_refusal(summary) is None
        and summary.get("decisions") == record.get("decisions")
    )
    if not valid:
        raise _Refused(REASON_SKIP_RECORD)
    skipped_at = record.get("skipped_at")
    return {"skipped_at": None if skipped_at is None else str(skipped_at)}


def assess_job(
    root: Any, store: Any, scheduler: Any, job: dict[str, Any], *,
    latest_row: Any = _UNSET, archive_row: Any = _UNSET, fresh: bool = False,
) -> Assessment:
    """Whether ``job``'s source may go to the Recycle Bin now, and why not. Never hashes.

    ``latest_row`` is the job's latest ``source_cleanups`` row and
    ``archive_row`` its latest ``source_archives`` row (None for none);
    omitted, each is read from the store. ``fresh=True`` re-reads the queue,
    manifest and plan instead of using the cache.
    """
    root = Path(root)
    job_id = int(job["id"])
    state = job.get("state")
    raw_source = str(job.get("source_path") or "")
    source = Path(raw_source) if raw_source else None
    name = (source.name if source is not None else "") or str(job.get("job_key") or "")
    resolved_source = ""
    if source is not None:
        try:
            resolved_source = str(source.resolve())
        except (OSError, RuntimeError):
            resolved_source = os.path.abspath(raw_source)
    size_bytes = job.get("source_size_bytes")
    values: dict[str, Any] = {
        "job_id": job_id, "name": name, "kind": KIND_BY_STATE.get(state),
        "source_path": resolved_source, "size_bytes": size_bytes, "mtime_ns": None,
        "output_path": None, "output_bytes": None, "output_sha256": None,
        "exported_at": None, "skipped_at": None,
    }
    if latest_row is _UNSET:
        latest_row = store.latest_source_cleanup(job_id)
    if archive_row is _UNSET:
        archive_row = store.latest_source_archive(job_id)

    def refused(reason: str) -> Assessment:
        return Assessment(eligible=False, reason=reason, **values)

    row_state = latest_row.get("state") if isinstance(latest_row, dict) else None
    if row_state == "PENDING":
        return refused(REASON_PENDING)
    if row_state == "RECYCLED":
        return refused(REASON_RECYCLED)
    if isinstance(archive_row, dict) and archive_row.get("state") in SOURCE_ARCHIVED_STATES:
        return refused(REASON_ARCHIVED)
    if state not in ELIGIBLE_STATES:
        return refused(REASON_STATE)
    busy = bool(scheduler.is_busy(job_id))
    pending = store.next_pending_stage(job_id) if state == "QUEUED" else None
    if (
        busy or state == "QUEUED" or state in IN_PROCESS_STATES
        or render_in_flight(job, (pending or {}).get("name"), worker_busy=busy)
    ):
        return refused(REASON_BUSY)
    if store.render_request(job_id) is not None:
        return refused(REASON_RENDER_REQUEST)
    if source is None or not source.is_file():
        return refused(REASON_SOURCE_MISSING)
    try:
        info = os.stat(raw_source)
    except OSError:
        return refused(REASON_SOURCE_MISSING)
    values["mtime_ns"] = int(info.st_mtime_ns)
    input_root = root / "input"
    if not _under(Path(resolved_source), input_root.resolve()):
        return refused(REASON_SOURCE_OUTSIDE)
    # The unresolved path, so that a link or junction is seen and refused.
    refusal = recycle_bin.path_refusal(os.path.abspath(raw_source), allowed_root=input_root)
    if refusal:
        return refused(refusal)
    if isinstance(size_bytes, bool) or int(info.st_size) != size_bytes:
        return refused(REASON_SOURCE_CHANGED)
    queue_value = job.get("active_queue_path")
    try:
        if not queue_value:
            raise ValueError("no active review queue")
        queue_path = (root / str(queue_value)).resolve()
        if not _under(queue_path, (root / "reports").resolve()):
            raise ValueError("review queue outside reports")
        facts = _queue_facts(root, queue_path, fresh=fresh)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
        # RuntimeError: a link loop, or JSON nested too deep (RecursionError).
        return refused(REASON_QUEUE_UNREADABLE)
    try:
        if state == "COMPLETED":
            details = _exported_facts(root, store, job, facts, fresh=fresh)
        else:
            details = _skipped_facts(store, job, facts)
    except _Refused as refusal_reason:
        return refused(refusal_reason.reason)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError):
        # A file that changed under us, a link loop or a malformed record
        # (RuntimeError covers JSON nested too deep): refuse, never pass.
        return refused(REASON_MANIFEST if state == "COMPLETED" else REASON_SKIP_RECORD)
    values.update(details)
    return Assessment(eligible=True, reason=None, **values)


def cleanup_hint(root: Any, store: Any, scheduler: Any, job: dict[str, Any], *, latest_row: Any,
                 archive_row: Any = _UNSET, assessment: Assessment | None = None) -> dict | None:
    """The Dashboard hint of a "Hoàn tất" card (None for every other state). Never hashes.

    ``assessment`` is an assess_job result the caller already has (the
    Control Center shares one between this hint and the archive hint).
    """
    if job.get("state") not in ELIGIBLE_STATES:
        return None
    if assessment is None:
        assessment = assess_job(root, store, scheduler, job, latest_row=latest_row, archive_row=archive_row)
    return {
        "eligible": assessment.eligible,
        "kind": assessment.kind,
        "reason": assessment.reason,
        "size_bytes": assessment.size_bytes,
        "output_name": Path(assessment.output_path).name if assessment.output_path else None,
        "output_bytes": assessment.output_bytes,
        "exported_at": assessment.exported_at,
        "skipped_at": assessment.skipped_at,
    }


def check_fields(verified_at_move: bool, check: dict[str, Any] | None) -> dict[str, Any]:
    """The effective verification of a move: what the move saw, or a later re-check that found it."""
    found_later = bool(check and check.get("found"))
    return {
        "verified": bool(verified_at_move) or found_later,
        "verified_at_cleanup": bool(verified_at_move),
        "verified_later_at": check.get("checked_at") if found_later else None,
        "rechecked_at": check.get("checked_at") if check else None,
    }


def cleanup_row_summary(row: dict[str, Any] | None, check: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """A ``source_cleanups`` row for the Dashboard (status() and the review export payload).

    ``check`` is the row's entry of ``JobStore.recycle_check_summary('SOURCE_CLEANUP')``.
    """
    if row is None:
        return None
    source_path = str(row.get("source_path") or "")
    return {
        "id": row.get("id"),
        "state": row.get("state"),
        "kind": row.get("kind"),
        "size_bytes": row.get("size_bytes"),
        "file_name": Path(source_path).name if source_path else "",
        "source_path": source_path,
        "created_at": row.get("created_at"),
        "finished_at": row.get("finished_at"),
        "restored_at": row.get("restored_at"),
        **check_fields(bool(row.get("verified")), check),
        "error": row.get("error"),
    }


# --------------------------------------------------------------------------
# Preview.


def _preview_id(eligible: list[Assessment]) -> str:
    rows = sorted([
        [item.job_id, os.path.normcase(item.source_path), item.size_bytes, item.mtime_ns, item.kind,
         item.output_path or "", item.output_bytes or 0]
        for item in eligible
    ])
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def _preview(root: Path, store: Any, scheduler: Any, job_ids: list[int], *,
             bin_info: Callable[[Path], Any], fresh: bool) -> tuple[dict[str, Any], list[Assessment]]:
    ids = parse_job_ids(job_ids)
    latest = store.latest_source_cleanups()
    archives = store.latest_source_archives()
    eligible: list[Assessment] = []
    ineligible: list[dict[str, Any]] = []
    for job_id in ids:
        try:
            job = store.get_job(job_id)
        except KeyError:
            ineligible.append({"job_id": job_id, "name": "", "reason": UNKNOWN_JOB_MESSAGE.format(job_id=job_id)})
            continue
        assessment = assess_job(root, store, scheduler, job, latest_row=latest.get(job_id),
                                archive_row=archives.get(job_id), fresh=fresh)
        if assessment.eligible:
            eligible.append(assessment)
        else:
            ineligible.append({"job_id": job_id, "name": assessment.name, "reason": assessment.reason})
    eligible.sort(key=lambda item: item.job_id)
    ineligible.sort(key=lambda item: item["job_id"])
    total = sum(int(item.size_bytes or 0) for item in eligible)
    blocked: str | None = None
    try:
        info = bin_info(root / "input")
    except recycle_bin.RecycleRefused as error:
        recycle = None
        blocked = str(error)
    else:
        recycle = {
            "volume": info.volume, "used_bytes": info.used_bytes, "items": info.items,
            "max_bytes": info.max_bytes, "after_bytes": info.used_bytes + total,
        }
        if eligible:
            blocked = recycle_bin.capacity_refusal(info, total)
    preview = {
        "preview_id": _preview_id(eligible),
        "eligible": [
            {
                "job_id": item.job_id, "name": item.name, "file_name": Path(item.source_path).name,
                "source_path": item.source_path, "size_bytes": item.size_bytes, "kind": item.kind,
                "output_path": item.output_path,
                "output_name": Path(item.output_path).name if item.output_path else None,
                "output_bytes": item.output_bytes, "exported_at": item.exported_at,
                "skipped_at": item.skipped_at,
            }
            for item in eligible
        ],
        "ineligible": ineligible,
        "count": len(eligible),
        "total_bytes": total,
        "recycle_bin": recycle,
        "blocked": blocked,
    }
    return preview, eligible


def preview_cleanup(root: Any, store: Any, scheduler: Any, job_ids: list[int], *,
                    bin_info: Callable[[Path], Any], fresh: bool = False) -> dict[str, Any]:
    """What a cleanup of ``job_ids`` would do. Read-only: no hash, no write, no recycler."""
    preview, _ = _preview(Path(root), store, scheduler, job_ids, bin_info=bin_info, fresh=fresh)
    return preview


# --------------------------------------------------------------------------
# Execute.


def _result(assessment: Assessment, status: str, message: str) -> dict[str, Any]:
    return {
        "job_id": assessment.job_id, "name": assessment.name, "status": status,
        "message": message, "size_bytes": assessment.size_bytes,
    }


def _recycled_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": row.get("source_path"), "size_bytes": row.get("size_bytes"),
        "sha256": row.get("source_sha256"), "kind": row.get("kind"),
        "output_path": row.get("output_path"), "output_sha256": row.get("output_sha256"),
        "output_bytes": row.get("output_bytes"), "exported_at": row.get("exported_at"),
        "skipped_at": row.get("skipped_at"), "recycle_record": row.get("recycle_record"),
        "verified": bool(row.get("verified")), "recycled_at": now_iso(),
    }


def _settle_recycled(store: Any, row_id: int, *, verified: bool, record: str | None) -> dict[str, Any] | None:
    """PENDING -> RECYCLED, reset the watcher and log; None when the row was settled already."""
    row = store.finish_source_cleanup(
        row_id, state="RECYCLED", verified=verified, recycle_record=record,
    )
    if row is None:
        return None
    store.reset_watched_file(Path(row["source_path"]))
    if verified:
        store.add_event(row["job_id"], "SOURCE_RECYCLED", RECYCLED_MESSAGE, payload=_recycled_payload(row))
    else:
        store.add_event(
            row["job_id"], "SOURCE_RECYCLE_UNVERIFIED", UNVERIFIED_MESSAGE,
            level="WARNING", payload=_recycled_payload(row),
        )
    return row


def _recycle_payload(context: dict[str, Any], error: str) -> dict[str, Any]:
    return {
        "stage": "recycle", "path": context["path"], "size_bytes": context["size_bytes"],
        "sha256": context["sha256"], "error": error,
    }


def _source_still_there(path: str, size: int) -> bool:
    try:
        return os.path.isfile(path) and os.stat(path).st_size == size
    except OSError:
        return False


def _settle_failure(store: Any, row_id: int, job_id: int, context: dict[str, Any],
                    error: BaseException) -> tuple[str, str]:
    """Settle a recycle that raised: (result status FAILED or PENDING, message)."""
    if isinstance(error, (recycle_bin.RecycleRefused, recycle_bin.RecycleFailed)):
        message, status = str(error), "FAILED"
    else:
        message = UNEXPECTED_MESSAGE.format(error=error)
        # A file that left input/ (or changed) stays PENDING for reconciliation.
        status = "FAILED" if _source_still_there(context["path"], context["size_bytes"]) else "PENDING"
    if status == "FAILED":
        store.finish_source_cleanup(row_id, state="FAILED", error=message)
    store.add_event(
        job_id, "SOURCE_CLEANUP_FAILED", message, level="ERROR",
        payload=_recycle_payload(context, message),
    )
    return status, message


def _late_callback(store: Any, row_id: int, job_id: int, context: dict[str, Any]) -> Callable:
    """Settles the PENDING row when Windows answers after the timeout (recycle thread)."""

    def on_late_result(result: Any, error: BaseException | None) -> None:
        try:
            if error is None and result is not None:
                _settle_recycled(
                    store, row_id, verified=bool(result.verified), record=result.record_path,
                )
            else:
                _settle_failure(
                    store, row_id, job_id, context,
                    error if error is not None else RuntimeError("no result"),
                )
        except Exception:  # noqa: BLE001 - e.g. the store was closed at shutdown
            pass

    return on_late_result


def precheck(root: Path, assessment: Any, job_sha: str,
             hasher: Callable[[Path], str]) -> tuple[dict | None, str | None]:
    """Hash the source (and output) with no lock held: (facts, None) or (None, reason).

    ``assessment`` needs source_path, kind, output_path and output_sha256
    (an Assessment, or source_archive's ArchiveAssessment).
    """
    source = assessment.source_path
    try:
        before = os.stat(source)
    except OSError:
        return None, REASON_SOURCE_MISSING
    try:
        digest = str(hasher(Path(source))).lower()
    except OSError as error:
        return None, UNEXPECTED_MESSAGE.format(error=error)
    if not job_sha or digest != job_sha.lower():
        return None, REASON_SOURCE_CHANGED
    output_before = output_sha = None
    if assessment.kind == "EXPORTED":
        output = root / str(assessment.output_path)
        try:
            output_before = os.stat(output)
            output_sha = str(hasher(output)).lower()
            output_after = os.stat(output)
        except OSError:
            return None, REASON_OUTPUT_CHANGED
        if output_sha != assessment.output_sha256 or _stat_key(output_after) != _stat_key(output_before):
            return None, REASON_OUTPUT_CHANGED
    try:
        after = os.stat(source)
    except OSError:
        return None, REASON_SOURCE_MISSING
    if _stat_key(after) != _stat_key(before):
        return None, SOURCE_CHANGED_DURING_HASH
    return {"stat": before, "sha256": digest, "output_stat": output_before, "output_sha256": output_sha}, None


def _recheck(root: Path, store: Any, scheduler: Any, assessment: Assessment, checked: dict[str, Any],
             bin_info: Callable[[Path], Any]) -> str | None:
    """Inside REVIEW_QUEUE_IO and job_action_lock: why the hashed video may no longer go, or None."""
    job = store.get_job(assessment.job_id)
    fresh = assess_job(root, store, scheduler, job, fresh=True)
    if not fresh.eligible:
        return fresh.reason
    same = (
        fresh.kind, os.path.normcase(fresh.source_path), fresh.size_bytes, fresh.output_path,
        fresh.output_bytes, fresh.output_sha256,
    ) == (
        assessment.kind, os.path.normcase(assessment.source_path), assessment.size_bytes,
        assessment.output_path, assessment.output_bytes, assessment.output_sha256,
    )
    if not same or str(job.get("source_sha256") or "").lower() != checked["sha256"]:
        return PREVIEW_CHANGED_MESSAGE
    try:
        current = os.stat(assessment.source_path)
    except OSError:
        return REASON_SOURCE_MISSING
    if _stat_key(current) != _stat_key(checked["stat"]):
        return SOURCE_CHANGED_DURING_HASH
    if checked["output_stat"] is not None:
        try:
            output_now = os.stat(root / str(assessment.output_path))
        except OSError:
            return REASON_OUTPUT_CHANGED
        if _stat_key(output_now) != _stat_key(checked["output_stat"]):
            return REASON_OUTPUT_CHANGED
    try:
        refusal = recycle_bin.capacity_refusal(bin_info(root / "input"), int(assessment.size_bytes or 0))
    except recycle_bin.RecycleRefused as error:
        return str(error)
    return refusal


def _clean_one(root: Path, store: Any, scheduler: Any, assessment: Assessment, *,
               recycler: Callable, bin_info: Callable[[Path], Any],
               hasher: Callable[[Path], str], timeout: float) -> tuple[dict[str, Any], bool]:
    """One video through the three sections; returns (result, stop_the_rest)."""
    job_id = assessment.job_id
    job_sha = str(store.get_job(job_id).get("source_sha256") or "")
    checked, reason = precheck(root, assessment, job_sha, hasher)
    if reason is not None:
        store.add_event(
            job_id, "SOURCE_CLEANUP_FAILED", reason, level="WARNING",
            payload={"stage": "precheck", "reason": reason},
        )
        return _result(assessment, "FAILED", reason), False
    with REVIEW_QUEUE_IO, scheduler.job_action_lock:
        reason = _recheck(root, store, scheduler, assessment, checked, bin_info)
        row_id = None
        if reason is None:
            row_id = store.add_source_cleanup(
                job_id=job_id, kind=assessment.kind, source_path=assessment.source_path,
                source_sha256=checked["sha256"], size_bytes=int(assessment.size_bytes),
                mtime_ns=int(checked["stat"].st_mtime_ns), output_path=assessment.output_path,
                output_sha256=checked["output_sha256"], output_bytes=assessment.output_bytes,
                exported_at=assessment.exported_at, skipped_at=assessment.skipped_at,
            )
    if row_id is None:
        store.add_event(
            job_id, "SOURCE_CLEANUP_FAILED", reason, level="WARNING",
            payload={"stage": "recheck", "reason": reason},
        )
        return _result(assessment, "FAILED", reason), False
    context = {"path": assessment.source_path, "size_bytes": int(assessment.size_bytes), "sha256": checked["sha256"]}
    try:
        outcome = recycler(
            Path(assessment.source_path), allowed_root=root / "input",
            expected_size=int(assessment.size_bytes), timeout=timeout,
            on_late_result=_late_callback(store, row_id, job_id, context),
        )
    except recycle_bin.RecycleTimeout as error:
        store.add_event(
            job_id, "SOURCE_CLEANUP_PENDING", PENDING_EVENT_MESSAGE, level="WARNING",
            payload=dict(context),
        )
        return _result(assessment, "PENDING", str(error)), True
    except Exception as error:  # noqa: BLE001 - every failure is settled and reported
        status, message = _settle_failure(store, row_id, job_id, context, error)
        return _result(assessment, status, message), False
    try:
        _settle_recycled(store, row_id, verified=bool(outcome.verified), record=outcome.record_path)
    except Exception as error:  # noqa: BLE001
        # The file left input/; the row stays PENDING until the next startup reconciles it.
        return _result(assessment, "PENDING", UNEXPECTED_MESSAGE.format(error=error)), False
    if outcome.verified:
        return _result(assessment, "RECYCLED", RECYCLED_MESSAGE), False
    return _result(assessment, "UNVERIFIED", UNVERIFIED_MESSAGE), False


def execute_cleanup(
    root: Any, store: Any, scheduler: Any, job_ids: Any, preview_id: Any, *,
    recycler: Callable, bin_info: Callable[[Path], Any],
    hasher: Callable[[Path], str] = sha256_file, timeout: float = 60.0,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Move the confirmed videos' sources to the Recycle Bin, one by one.

    ``recycler`` has no default (the signature of
    ``recycle_bin.send_to_recycle_bin``); ``bin_info`` is called with
    ``root / 'input'``. Raises ValueError for a bad request, CleanupConflict
    when nothing may start; otherwise every eligible video gets a result.
    """
    root = Path(root)
    ids = parse_job_ids(job_ids)
    if not isinstance(preview_id, str) or _PREVIEW_ID.fullmatch(preview_id) is None:
        raise ValueError(PREVIEW_ID_MESSAGE)
    if not _EXECUTE_LOCK.acquire(blocking=False):
        raise CleanupConflict("busy", BUSY_MESSAGE)
    try:
        if recycle_bin.operations_in_progress():
            raise CleanupConflict("busy", BUSY_MESSAGE)
        preview, eligible = _preview(root, store, scheduler, ids, bin_info=bin_info, fresh=True)
        if preview["preview_id"] != preview_id:
            raise CleanupConflict("preview_changed", PREVIEW_CHANGED_MESSAGE, preview)
        if preview["recycle_bin"] is None:
            raise CleanupConflict("bin_unavailable", preview["blocked"], preview)
        if preview["blocked"]:
            raise CleanupConflict("bin_capacity", preview["blocked"], preview)
        if not eligible:
            raise ValueError(NOTHING_ELIGIBLE_MESSAGE)
        results: list[dict[str, Any]] = []
        stopped = False
        for assessment in eligible:
            if stopped:
                results.append(_result(assessment, "NOT_RUN", NOT_RUN_MESSAGE))
                continue
            if should_stop is not None and should_stop():
                results.append(_result(assessment, "NOT_RUN", STOPPING_MESSAGE))
                continue
            try:
                result, stopped = _clean_one(
                    root, store, scheduler, assessment, recycler=recycler, bin_info=bin_info,
                    hasher=hasher, timeout=timeout,
                )
            except Exception as error:  # noqa: BLE001 - one video never aborts the others
                # Raised before a row was written (the recycle step settles its own errors).
                message = UNEXPECTED_MESSAGE.format(error=error)
                try:
                    store.add_event(
                        assessment.job_id, "SOURCE_CLEANUP_FAILED", message, level="WARNING",
                        payload={"stage": "precheck", "reason": message},
                    )
                except Exception:  # noqa: BLE001
                    pass
                result = _result(assessment, "FAILED", message)
            results.append(result)
    finally:
        _EXECUTE_LOCK.release()
    moved = [item for item in results if item["status"] in ("RECYCLED", "UNVERIFIED")]
    return {
        "results": results,
        "recycled_count": len(moved),
        "recycled_bytes": sum(int(item["size_bytes"] or 0) for item in moved),
        "failed_count": sum(1 for item in results if item["status"] == "FAILED"),
        "pending": sum(1 for item in results if item["status"] == "PENDING"),
    }


# --------------------------------------------------------------------------
# Startup reconciliation and shutdown.


def _posix_seconds(value: Any) -> float | None:
    moment = _aware_datetime(value)
    return None if moment is None else moment.timestamp()


def reconcile_pending_cleanups(root: Any, store: Any, *, finder: Callable) -> list[int]:
    """Settle the PENDING rows left by an interrupted cleanup; returns the row ids settled.

    A source still in input/ with its size was never moved (FAILED); otherwise
    it left, and ``finder`` (``recycle_bin.find_recycle_record``) tells whether
    the bin has it (RECYCLED, verified or not). A row whose shell call is still
    running in this process is left alone.
    """
    running = {os.path.normcase(path) for path in recycle_bin.operations_in_progress()}
    settled: list[int] = []
    for row in store.pending_source_cleanups():
        source = str(row.get("source_path") or "")
        if os.path.normcase(os.path.abspath(source)) in running:
            continue
        size = int(row.get("size_bytes") or 0)
        if _source_still_there(source, size):
            if store.finish_source_cleanup(row["id"], state="FAILED", error=INTERRUPTED_MESSAGE) is not None:
                store.add_event(
                    row["job_id"], "SOURCE_CLEANUP_FAILED", INTERRUPTED_MESSAGE, level="WARNING",
                    payload={"stage": "reconcile", "path": source, "size_bytes": size},
                )
                settled.append(int(row["id"]))
            continue
        since = _posix_seconds(row.get("created_at"))
        record = None
        if since is not None:
            try:
                record = finder(Path(source).anchor, source, size, since=since - 5)
            except Exception:  # noqa: BLE001 - an unreadable bin only means "not verified"
                record = None
        if _settle_recycled(store, int(row["id"]), verified=record is not None, record=record) is not None:
            settled.append(int(row["id"]))
    return settled


# --------------------------------------------------------------------------
# "Kiểm tra lại Thùng rác": read the bin again for a move that was not verified.


def _recheck_subject(root: Path, store: Any, kind: str, subject_id: int) -> dict[str, Any]:
    """What to look for: {job_id, path, size, since, until, what}; ValueError or ActionConflict otherwise.

    Only a job's latest row qualifies, so no later row of that job exists; ``until``
    (the row's own settle time plus RECHECK_UNTIL_SLACK_SECONDS) is earlier than any
    later row's start and also shuts out a later move of the same path by hand or by
    another job.
    """
    if kind == "SOURCE_CLEANUP":
        row = store.get_source_cleanup(subject_id)
    else:
        row = store.get_source_archive(subject_id)
    if row is None:
        raise ValueError(RECHECK_UNKNOWN_MESSAGE.format(subject_id=subject_id))
    job_id = int(row["job_id"])
    since = _posix_seconds(row.get("created_at"))
    if kind == "SOURCE_CLEANUP":
        latest = store.latest_source_cleanup(job_id)
        current = latest is not None and latest["id"] == row["id"] and row["state"] == "RECYCLED"
        verified = bool(row.get("verified"))
        path, size, what = str(row.get("source_path") or ""), row.get("size_bytes"), "video gốc"
        settled = _posix_seconds(row.get("finished_at"))
    else:
        latest = store.latest_source_archive(job_id)
        current = (
            latest is not None and latest["id"] == row["id"] and row["state"] == "ARCHIVED"
            and row.get("kind") == "EXPORTED" and bool(row.get("export_recycled"))
            and bool(row.get("output_path"))
        )
        verified = bool(row.get("export_verified"))
        output = str(row.get("output_path") or "")
        path = str((root / output).resolve()) if output else ""
        size, what = row.get("output_bytes"), "bản xuất"
        settled = _posix_seconds(row.get("archived_at"))
    if not current or since is None or not path or size is None:
        raise ActionConflict("not_recheckable", RECHECK_NOT_NEEDED_MESSAGE)
    if verified or store.recycle_check_summary(kind).get(int(row["id"]), {}).get("found"):
        raise ActionConflict("already_verified", RECHECK_ALREADY_MESSAGE)
    until = None if settled is None else settled + RECHECK_UNTIL_SLACK_SECONDS
    return {"job_id": job_id, "path": path, "size": int(size), "since": since - 5, "until": until, "what": what}


def recheck_recycle_record(root: Any, store: Any, kind: Any, subject_id: Any, *,
                           finder: Callable) -> dict[str, Any]:
    """Look in the Recycle Bin again for an unverified move and append what was seen.

    ``kind`` is 'source_cleanup' (the latest RECYCLED cleanup row of a job,
    verified 0) or 'archive_export' (the latest ARCHIVED archive row of an
    exported job whose export was recycled, export_verified 0); ``finder`` is
    ``recycle_bin.find_recycle_record``. Only appends a ``recycle_checks`` row
    and a SOURCE_RECYCLE_VERIFIED or SOURCE_RECYCLE_STILL_UNVERIFIED event: the
    cleanup or archive row is never changed. Raises ValueError for a bad
    request and ActionConflict ('busy', 'not_recheckable', 'already_verified',
    'bin_unavailable') when nothing was written.
    """
    root = Path(root)
    if not isinstance(kind, str) or kind not in RECHECK_KINDS:
        raise ValueError(RECHECK_KIND_MESSAGE)
    if isinstance(subject_id, bool) or not isinstance(subject_id, int) or not 1 <= subject_id <= MAX_JOB_ID:
        raise ValueError(RECHECK_ID_MESSAGE)
    table_kind = RECHECK_KINDS[kind]
    if not SOURCE_FILE_LOCK.acquire(blocking=False):
        raise ActionConflict("busy", SOURCE_BUSY_MESSAGE)
    try:
        subject = _recheck_subject(root, store, table_kind, subject_id)
        try:
            record = finder(Path(subject["path"]).anchor, subject["path"], subject["size"],
                            since=subject["since"], until=subject["until"])
        except Exception as error:  # noqa: BLE001 - an unreadable bin writes nothing
            raise ActionConflict("bin_unavailable", RECHECK_UNREADABLE_MESSAGE) from error
        found = record is not None
        record = None if record is None else str(record)
        check_id = store.add_recycle_check(
            kind=table_kind, subject_id=subject_id, job_id=subject["job_id"], path=subject["path"],
            size_bytes=subject["size"], found=found, recycle_record=record, actor=RECHECK_ACTOR,
        )
        payload = {
            "kind": table_kind, "subject_id": subject_id, "path": subject["path"],
            "size_bytes": subject["size"], "recycle_record": record, "check_id": check_id,
        }
        message = (RECHECK_FOUND_MESSAGE if found else RECHECK_MISSING_MESSAGE).format(what=subject["what"])
        store.add_event(
            subject["job_id"], "SOURCE_RECYCLE_VERIFIED" if found else "SOURCE_RECYCLE_STILL_UNVERIFIED",
            message, level="INFO" if found else "WARNING", payload=payload,
        )
        checked_at = store.recycle_checks(table_kind, subject_id)[-1]["checked_at"]
    finally:
        SOURCE_FILE_LOCK.release()
    return {
        "kind": kind, "id": subject_id, "job_id": subject["job_id"], "found": found,
        "record": record, "checked_at": checked_at, "message": message,
    }


def cleanup_running() -> bool:
    """A cleanup, archive, restore or re-check runs, or a shell call has not answered yet."""
    return _EXECUTE_LOCK.locked() or bool(recycle_bin.operations_in_progress())


def wait_idle(timeout: float) -> bool:
    """Wait (up to ``timeout`` seconds) until no cleanup runs; True when idle."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    while cleanup_running():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))
    return True
