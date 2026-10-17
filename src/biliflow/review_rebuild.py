"""Dựng lại danh sách duyệt (giữ quyết định) (docs/handoff/D-rebuild-keep.md).

The button queues a new revision exactly as "Chạy lại" does (the same scan scope, the scan stages restored from
the stage cache while their keys hold), with one difference at its last stage: the new review queue carries the
decisions of the queue in use (``review_decision_carry``, its rules only).

The Control Center writes ``CARRY_REQUEST_NAME`` into the new revision's report folder before the job is queued
(``write_carry_request``); the pipeline's own ``build-review`` (``build_review_queue``) reads it beside its queue
(``read_carry_request``). The request names the old queue by its path under ``reports`` and its SHA-256: a queue
changed since, unreadable or of another source stops the build, so a queue is never built without the decisions it
was asked to keep. The scan stages and their cache keys are not involved (``cli.py`` is unchanged).
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from biliflow.review_decision_carry import DECIDED

CARRY_REQUEST_NAME = "decision-carry-request.json"
CARRY_REQUEST_VERSION = 1
REBUILD_STATES = frozenset({"WAITING_REVIEW", "READY_TO_EXPORT", "COMPLETED"})
STATE_NAMES = {
    "PAUSED": "Tạm dừng", "FAILED": "Lỗi", "INTERRUPTED_RECOVERABLE": "Gián đoạn", "CANCELLED": "Đã hủy",
    "DISCOVERED": "Chưa thiết lập", "NEEDS_METADATA": "Chờ thiết lập",
}
REBUILD_SKIPPED_REFUSAL = (
    "Video #{job_id} đang Bỏ qua (không xuất). Bấm “Mở lại để xuất” trước rồi dựng lại danh sách duyệt."
)
REBUILD_EXPORT_REFUSAL = "Video #{job_id} đang chờ xuất hoặc đang xuất; chờ xong rồi dựng lại danh sách duyệt."
REBUILD_BUSY_REFUSAL = (
    "Video #{job_id} đang trong hàng đợi hoặc đang được xử lý; chờ xong rồi dựng lại danh sách duyệt."
)
REBUILD_STATE_REFUSAL = (
    "Chỉ dựng lại được danh sách duyệt của video đang chờ duyệt, sẵn sàng xuất hoặc đã xuất "
    "(video #{job_id} đang ở trạng thái {state})."
)
REBUILD_NO_QUEUE_REFUSAL = "Video #{job_id} chưa có danh sách duyệt; dùng “Chạy lại kiểm tra”."
REBUILD_SOURCE_MISSING = (
    "Không tìm thấy video gốc của video #{job_id}; dựng lại cần video gốc (bước chuẩn bị kiểm SHA-256 của nó)."
)
QUEUE_MISMATCH = "Danh sách duyệt đang dùng không khớp video nguồn của job; chưa dựng lại để không mất quyết định."
REQUEST_INVALID = "Yêu cầu giữ quyết định không dùng được ({reason}); chưa dựng lại để không mất quyết định."


def rebuild_refusal(
    job: dict[str, Any], *, in_flight: bool, busy: bool, in_process: bool, source_present: bool,
) -> str | None:
    """Why the job's review cannot be rebuilt now (a cleaned or archived source is refused before, as for
    "Chạy lại"); None when it can."""
    job_id, state = int(job["id"]), str(job.get("state") or "")
    if state == "SKIPPED":
        return REBUILD_SKIPPED_REFUSAL.format(job_id=job_id)
    if in_flight:
        return REBUILD_EXPORT_REFUSAL.format(job_id=job_id)
    if busy or in_process or state == "QUEUED":
        return REBUILD_BUSY_REFUSAL.format(job_id=job_id)
    if state not in REBUILD_STATES:
        return REBUILD_STATE_REFUSAL.format(job_id=job_id, state=STATE_NAMES.get(state, state))
    if not job.get("active_queue_path"):
        return REBUILD_NO_QUEUE_REFUSAL.format(job_id=job_id)
    if not source_present:
        return REBUILD_SOURCE_MISSING.format(job_id=job_id)
    return None


def _same_path(first: object, second: object) -> bool:
    return os.path.normcase(os.path.abspath(str(first))) == os.path.normcase(os.path.abspath(str(second)))


def same_source(queue: dict, source_path: object, source_sha256: str | None) -> bool:
    """``queue`` was built for this source: the same path and, when both are known, the same SHA-256."""
    source = queue.get("source") if isinstance(queue.get("source"), dict) else {}
    sha = str(source.get("sha256") or "")
    return bool(source.get("path")) and _same_path(source["path"], source_path) and (
        not sha or not source_sha256 or sha.casefold() == str(source_sha256).casefold()
    )


def carry_request(root: Path, job: dict[str, Any], queue_path: Path, *, now: datetime | None = None) -> dict:
    """The request for ``job``'s active queue at ``queue_path`` (read and checked here): its path under the
    install, revision and SHA-256, and how many decisions it holds. ValueError when it is unreadable or names
    another source."""
    data = queue_path.read_bytes()
    try:
        queue = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError(REQUEST_INVALID.format(reason="danh sách duyệt đang dùng không đọc được")) from error
    if not isinstance(queue, dict) or not isinstance(queue.get("items"), list):
        raise ValueError(REQUEST_INVALID.format(reason="danh sách duyệt đang dùng không hợp lệ"))
    if not same_source(queue, job["source_path"], str(job["source_sha256"])):
        raise ValueError(QUEUE_MISMATCH)
    return {
        "version": CARRY_REQUEST_VERSION,
        "job_id": int(job["id"]),
        "from_queue": queue_path.resolve().relative_to(Path(root).resolve()).as_posix(),
        "from_revision": job.get("active_revision"),
        "from_sha256": hashlib.sha256(data).hexdigest(),
        "source_sha256": str(job["source_sha256"]),
        "decisions": sum(1 for item in queue["items"] if isinstance(item, dict) and item.get("decision") in DECIDED),
        "requested_at": (now or datetime.now().astimezone()).isoformat(timespec="seconds"),
    }


def new_revision_key(root: Path, job_key: str, *, now: datetime | None = None) -> str:
    """``<job_key>-run-<time>`` as "Chạy lại" names a revision, with ``-2``, ``-3`` … when that folder exists."""
    base = f"{job_key}-run-{(now or datetime.now()).strftime('%Y%m%d-%H%M%S')}"
    key, number = base, 1
    while (Path(root) / "reports" / "jobs" / key).exists():
        number += 1
        key = f"{base}-{number}"
    return key


def write_carry_request(root: Path, pipeline_key: str, request: dict) -> Path:
    """Create the new revision's report folder (never an existing one) with the request in it."""
    folder = Path(root) / "reports" / "jobs" / pipeline_key
    folder.mkdir(parents=True, exist_ok=False)
    path = folder / CARRY_REQUEST_NAME
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return path


def discard_carry_request(path: Path) -> None:
    """Undo write_carry_request when the job could not be queued: the request, then its folder if empty."""
    path.unlink(missing_ok=True)
    try:
        path.parent.rmdir()
    except OSError:
        pass  # not empty: something else is in it, so it stays


def read_carry_request(root: Path, queue_path: Path) -> tuple[dict, dict] | None:
    """(request, old queue) when a request lies beside ``queue_path``; None without one. ValueError when it
    cannot be used: not a plain file, unreadable, its queue outside ``reports``, changed since or unreadable."""
    path = queue_path.parent / CARRY_REQUEST_NAME
    if not os.path.lexists(path):
        return None

    def refuse(reason: str) -> ValueError:
        return ValueError(REQUEST_INVALID.format(reason=reason))

    if path.is_symlink() or not path.is_file():
        raise refuse("không phải tệp thường")
    try:
        request = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise refuse("không đọc được yêu cầu") from error
    if not isinstance(request, dict) or request.get("version") != CARRY_REQUEST_VERSION:
        raise refuse("phiên bản yêu cầu không đúng")
    reports = (Path(root) / "reports").resolve()
    old = (Path(root) / str(request.get("from_queue") or "")).resolve()
    if reports not in old.parents or old == queue_path.resolve() or old.name != "review-queue.json":
        raise refuse("danh sách cũ nằm ngoài reports")
    try:
        data = old.read_bytes()
    except OSError as error:
        raise refuse("không đọc được danh sách duyệt cũ") from error
    if hashlib.sha256(data).hexdigest() != request.get("from_sha256"):
        raise refuse("danh sách duyệt cũ đã đổi sau khi bấm dựng lại")
    try:
        queue = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise refuse("danh sách duyệt cũ không đọc được") from error
    if not isinstance(queue, dict) or not isinstance(queue.get("items"), list):
        raise refuse("danh sách duyệt cũ không hợp lệ")
    return request, queue
