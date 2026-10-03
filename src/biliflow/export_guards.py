"""Export and review-edit guards shared by the Control Center and the standalone review UI.

Neutral module: it imports only the standard library and
``biliflow.job_store`` (which imports no other biliflow module), never
``control_center``, ``review_workflow``, ``scheduler``, ``source_cleanup`` or
``recycle_bin``, so every server and ``source_cleanup`` can import it without
an import cycle. ``control_center._REVIEW_QUEUE_IO`` becomes an alias of
``REVIEW_QUEUE_IO`` (batch 3, step B5) and ``scheduler`` re-exports the two
skip refusals.

Lock order (the only one): ``REVIEW_QUEUE_IO`` first, then
``scheduler.job_action_lock``. No lock is held while hashing a file or calling
the Windows shell.

The standalone review UI (``serve_review_ui``) has no scheduler and no
JobStore: it reads the Control Center database read-only through
``control_center_job_facts`` and fails closed when it cannot.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote

from biliflow.job_store import (
    IN_PROCESS_STATES,
    RENDER_REQUEST_STATES,
    SOURCE_ARCHIVED_STATES,
    SOURCE_CLEANED_STATES,
)


# Serializes review-queue reads (page polling, evidence, strip frames, video)
# with review-queue writes. On Windows, Path.replace onto a file that another
# thread has open fails with WinError 5, which lost decisions while the focus
# page loaded frames for the next item.
REVIEW_QUEUE_IO = threading.RLock()


# Skipped videos ("Bỏ qua (không xuất)"); scheduler re-exports both names.
SKIPPED_REFUSAL = "Video đã được đánh dấu bỏ qua; bấm “Mở lại để xuất” trước."
SKIPPED_STOP_REFUSAL = (
    "Video đã được đánh dấu bỏ qua (không xuất); không có gì để dừng hoặc hủy."
)

# Export guards (ControlCenter.finalize order: skipped, in flight, busy,
# queue not ready, source cleaned, source missing).
SOURCE_MISSING_MESSAGE = "Video gốc không còn trong input; không thể xuất."
EXPORT_IN_FLIGHT_MESSAGE = (
    "Video này đang chờ xuất hoặc đang xuất; không xếp lệnh xuất thêm lần nữa."
)
REVIEW_EDIT_IN_FLIGHT_MESSAGE = (
    "Video đang chờ xuất hoặc đang xuất; hủy lệnh xuất trước khi đổi quyết định."
)
QUEUE_NOT_READY_MESSAGE = "Vẫn còn mục chưa có quyết định cuối cùng"
# Format with job_id=...
BUSY_EXPORT_MESSAGE = (
    "Video #{job_id} đang trong hàng đợi hoặc đang được xử lý; chờ xong rồi hãy xuất."
)

# A source moved to the Windows Recycle Bin by "Dọn video gốc" (latest
# source_cleanups row PENDING or RECYCLED) locks every action on its job.
SOURCE_CLEANED_MESSAGE = (
    "Video gốc đã được dọn vào Thùng rác. Chép lại video gốc vào input để chạy lại."
)
SOURCE_CLEANED_STOP_REFUSAL = (
    "Video gốc đã được dọn vào Thùng rác; không có gì để dừng hoặc hủy."
)
SOURCE_CLEANED_REVIEW_REFUSAL = (
    "Video gốc đã được dọn vào Thùng rác; chép lại video gốc vào input trước khi "
    "đổi quyết định duyệt."
)
SOURCE_CLEANED_MEDIA_MESSAGE = "Video gốc đã được dọn vào Thùng rác"

# A source moved into archive/sources/ by "Lưu trữ" (latest source_archives row
# PENDING, ARCHIVED or RESTORING) locks every action on its job the same way,
# until "Khôi phục bản xuất" brings it back.
SOURCE_ARCHIVED_MESSAGE = (
    "Video gốc đang ở kho lưu trữ. Bấm “Khôi phục bản xuất” để đưa video gốc về input trước."
)
SOURCE_ARCHIVED_STOP_REFUSAL = "Video gốc đang ở kho lưu trữ; không có gì để dừng hoặc hủy."
SOURCE_ARCHIVED_REVIEW_REFUSAL = (
    "Video gốc đang ở kho lưu trữ; bấm “Khôi phục bản xuất” trước khi đổi quyết định duyệt."
)
SOURCE_ARCHIVED_MEDIA_MESSAGE = "Video gốc đang ở kho lưu trữ"


class ActionConflict(Exception):
    """An action that conflicts with the job's current state: HTTP 409 ``{error, code}``.

    Not a ValueError (HTTP 400), so a handler never mistakes it for bad input.
    ``source_cleanup.CleanupConflict`` is a subclass.
    """

    def __init__(self, code: str, message: str, preview: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.preview = preview

# Standalone review UI (serve_review_ui) checks against the Control Center DB,
# read-only; it fails closed for every video the Control Center owns.
CONTROL_CENTER_STATE_UNREADABLE = (
    "Không đọc được trạng thái Control Center (state/control-center.sqlite3); "
    "hãy xuất video từ Dashboard."
)
# Format with job_id=..., state=...
CONTROL_CENTER_JOB_MESSAGE = (
    "Video này thuộc job #{job_id} của Control Center ({state}); hãy xuất từ Dashboard "
    "để giữ đúng hàng đợi, trạng thái bỏ qua và dọn video gốc."
)
STANDALONE_SKIPPED_EDIT_REFUSAL = (
    "Video đã được đánh dấu bỏ qua trong Control Center; mở Dashboard và bấm "
    "“Mở lại để xuất” trước khi đổi quyết định."
)

# A job whose export waits or runs. QUEUED alone is not enough: a waiting scan
# is QUEUED too, so the stage tells them apart.
RENDER_STATES = frozenset({"RENDERING", "VERIFYING"})
# The only states in which an unfinished render stage is an export the user
# paused, that failed, or that a restart interrupted, so Tiếp tục / Thử lại
# may run it (scheduler imports this set).
RESUMABLE_EXPORT_STATES = frozenset({"PAUSED", "FAILED", "INTERRUPTED_RECOVERABLE", "QUEUED"})
CONTROL_CENTER_DATABASE = Path("state") / "control-center.sqlite3"

DECISION_LABELS = {
    "KEEP": "Giữ nguyên", "BLUR": "Làm mờ", "CUT": "Cắt cảnh",
    "NEEDS_MORE_CONTEXT": "Cần xem thêm",
}


def review_summary(queue: dict[str, Any]) -> dict[str, Any]:
    """Compact counts of a review queue for the dashboard (no item data)."""
    items = list(queue.get("items") or [])
    decisions: dict[str, int] = {}
    for item in items:
        if item.get("decision"):
            key = str(item["decision"])
            decisions[key] = decisions.get(key, 0) + 1
    policy = queue.get("export_size_policy")
    summary = {
        "status": queue.get("status"),
        "main_items": len(items),
        "advisory_items": len(queue.get("advisory_items") or []),
        "pending": sum(1 for item in items if not item.get("decision")),
        "decisions": dict(sorted(decisions.items())),
        "export_size_policy": (
            {"mode": policy.get("mode"), "maximum_output_gb": policy.get("maximum_output_gb")}
            if isinstance(policy, dict) else None
        ),
    }
    summary["skip_eligible"] = skip_refusal(summary) is None
    return summary


def skip_refusal(summary: dict[str, Any]) -> str | None:
    """Why "Bỏ qua (không xuất)" is not allowed for this review, or None.

    User decision 2026-10-02: a fully reviewed queue with no main item
    (advisory candidates do not count), or whose every main decision is KEEP.
    """
    if summary.get("status") != "READY_FOR_EDIT_PLAN" or summary.get("pending"):
        return "Video còn mục chưa có quyết định cuối cùng; không thể bỏ qua."
    decisions = dict(summary.get("decisions") or {})
    other = {key: value for key, value in decisions.items() if key != "KEEP"}
    if other:
        named = ", ".join(
            f"{value} {DECISION_LABELS.get(key, key)}" for key, value in sorted(other.items())
        )
        return (
            f"Video có cảnh chính không phải Giữ nguyên ({named}); "
            "hãy xuất video thay vì bỏ qua."
        )
    if int(decisions.get("KEEP", 0)) != int(summary.get("main_items") or 0):
        return "Video còn mục chưa có quyết định cuối cùng; không thể bỏ qua."
    return None


def render_in_flight(
    job: dict[str, Any], next_pending_stage: str | None, *, worker_busy: bool,
) -> bool:
    """True while an export of ``job`` waits in the queue or runs.

    ``next_pending_stage`` is the name of the job's next PENDING or
    FAILED_RETRYABLE stage (or None); it only counts for a QUEUED job, so the
    caller may pass it for any state. ``worker_busy`` is
    ``scheduler.is_busy(job_id)`` (always False outside the Control Center).
    """
    state = job.get("state")
    current_stage = job.get("current_stage")
    if state in RENDER_STATES:
        return True
    if state == "QUEUED" and (current_stage == "render" or next_pending_stage == "render"):
        return True
    return bool(worker_busy) and current_stage == "render"


def export_state_refusal(
    job: dict[str, Any], *, in_flight: bool, worker_busy: bool,
) -> str | None:
    """Why the job's state forbids "Xuất video" now, or None (finalize checks 1-3)."""
    state = job.get("state")
    if state == "SKIPPED":
        return SKIPPED_REFUSAL
    if in_flight:
        return EXPORT_IN_FLIGHT_MESSAGE
    if state == "QUEUED" or state in IN_PROCESS_STATES or worker_busy:
        return BUSY_EXPORT_MESSAGE.format(job_id=job.get("id"))
    return None


def export_source_refusal(source_path: Path, *, cleaned: bool, archived: bool = False) -> str | None:
    """Why the source video cannot be exported, or None (finalize checks 5-6)."""
    if cleaned:
        return SOURCE_CLEANED_MESSAGE
    if archived:
        return SOURCE_ARCHIVED_MESSAGE
    if not Path(source_path).is_file():
        return SOURCE_MISSING_MESSAGE
    return None


def control_center_job_facts(root: Path, source_sha256: str) -> dict[str, Any] | None:
    """The Control Center job of a source, read-only; None when it has none.

    Opens ``state/control-center.sqlite3`` with ``mode=ro`` and never through
    JobStore, whose constructor migrates and writes. Any SQLite error raises
    ``ValueError(CONTROL_CENTER_STATE_UNREADABLE)``: callers fail closed.
    """
    if not source_sha256:
        # A queue without a source hash cannot belong to a Control Center job.
        return None
    database = Path(root) / CONTROL_CENTER_DATABASE
    if not database.is_file():
        return None
    try:
        connection = sqlite3.connect(
            f"file:{quote(database.as_posix())}?mode=ro", uri=True, timeout=5,
        )
        try:
            row = connection.execute(
                """SELECT id,state,current_stage FROM jobs
                WHERE lower(source_sha256)=lower(?) ORDER BY id LIMIT 1""",
                (str(source_sha256),),
            ).fetchone()
            if row is None:
                return None
            job_id = int(row[0])
            pending = connection.execute(
                """SELECT name FROM stages WHERE job_id=? AND state IN ('PENDING','FAILED_RETRYABLE')
                ORDER BY ordinal LIMIT 1""",
                (job_id,),
            ).fetchone()
            # The same query as JobStore.render_request: an export request
            # that is not finished (it may be paused, failed or interrupted).
            marks = ",".join("?" * len(RENDER_REQUEST_STATES))
            render_request = connection.execute(
                f"SELECT 1 FROM stages WHERE job_id=? AND name='render' AND state IN ({marks})",  # noqa: S608
                (job_id, *RENDER_REQUEST_STATES),
            ).fetchone() is not None
            cleaned = False
            has_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_cleanups'"
            ).fetchone() is not None
            if has_table:
                latest = connection.execute(
                    "SELECT state FROM source_cleanups WHERE job_id=? ORDER BY id DESC LIMIT 1",
                    (job_id,),
                ).fetchone()
                cleaned = latest is not None and latest[0] in SOURCE_CLEANED_STATES
            # Batch 4: an archived source ("Lưu trữ") locks the job the same way.
            archived = False
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_archives'"
            ).fetchone() is not None:
                latest = connection.execute(
                    "SELECT state FROM source_archives WHERE job_id=? ORDER BY id DESC LIMIT 1",
                    (job_id,),
                ).fetchone()
                archived = latest is not None and latest[0] in SOURCE_ARCHIVED_STATES
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise ValueError(CONTROL_CENTER_STATE_UNREADABLE) from error
    return {
        "job": {"id": job_id, "state": str(row[1]), "current_stage": row[2]},
        "next_pending_stage": None if pending is None else str(pending[0]),
        "render_request": render_request,
        "cleaned": cleaned,
        "archived": archived,
    }


def _queue_source(queue: dict[str, Any]) -> dict[str, Any]:
    source = queue.get("source")
    return source if isinstance(source, dict) else {}


def standalone_export_refusal(root: Path, queue: dict[str, Any]) -> str | None:
    """Why the standalone review UI must not export this queue, or None.

    Fails closed: a video the Control Center owns (any state) is exported only
    from the Dashboard, and an unreadable Control Center database raises
    ValueError(CONTROL_CENTER_STATE_UNREADABLE).
    """
    if queue.get("status") != "READY_FOR_EDIT_PLAN":
        return QUEUE_NOT_READY_MESSAGE
    source = _queue_source(queue)
    facts = control_center_job_facts(root, str(source.get("sha256") or ""))
    if facts is not None:
        return CONTROL_CENTER_JOB_MESSAGE.format(
            job_id=facts["job"]["id"], state=facts["job"]["state"],
        )
    path = source.get("path")
    if not path or not Path(str(path)).is_file():
        return SOURCE_MISSING_MESSAGE
    return None


def standalone_edit_refusal(root: Path, queue: dict[str, Any]) -> str | None:
    """Why the standalone review UI must not change a decision of this queue, or None.

    Same rules as the Control Center review routes for an export in flight, a
    cleaned or archived source and a skipped video. The database read fails
    closed too.
    An export request that Tiếp tục or Thử lại would still run (a paused,
    failed or interrupted render) is refused as well: the Dashboard retires it
    when a decision changes, but this server has no scheduler, and the request
    would render the plan fixed at the old finalize. Hủy retires it.
    """
    facts = control_center_job_facts(root, str(_queue_source(queue).get("sha256") or ""))
    if facts is None:
        return None
    job = facts["job"]
    if render_in_flight(job, facts["next_pending_stage"], worker_busy=False):
        return REVIEW_EDIT_IN_FLIGHT_MESSAGE
    if facts.get("render_request") and job["state"] in RESUMABLE_EXPORT_STATES:
        return REVIEW_EDIT_IN_FLIGHT_MESSAGE
    if facts["cleaned"]:
        return SOURCE_CLEANED_REVIEW_REFUSAL
    if facts.get("archived"):
        return SOURCE_ARCHIVED_REVIEW_REFUSAL
    if job["state"] == "SKIPPED":
        return STANDALONE_SKIPPED_EDIT_REFUSAL
    return None
