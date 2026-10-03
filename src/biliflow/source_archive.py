"""Lưu trữ / Khôi phục bản xuất: keep an exported or skipped source out of input/.

A user-clicked, per-video action of the Dashboard's "Hoàn tất" tab (batch 4).
For a job whose export passes the same checks as "Dọn video gốc"
(``source_cleanup.assess_job``), or that was skipped with a matching skip
record:

1. without any lock: hash the source (and the export) with
   ``source_cleanup.precheck``; for an exported job, read the bin (a shell
   query, never under a lock);
2. under ``REVIEW_QUEUE_IO`` then ``scheduler.job_action_lock`` (the only lock
   order): re-assess from fresh reads, compare the stats again, compare that
   bin reading with the export and its manifest, snapshot the review and
   write a PENDING ``source_archives`` row, which from then on locks the job;
3. without any lock: write the archive manifest to its own ``.tmp`` beside
   the target, rename the source from input/ into
   ``<root>/archive/sources/<job_key>/`` (same volume, never a copy, never over
   an existing file), hash it there, then, for an exported job only, move that
   job's export and its manifest to the Recycle Bin with the injected
   ``recycler``. A failure before the export left output/ (or any refusal of
   the recycler, which touches nothing) renames the source back and removes
   the ``.tmp``; the manifest gets its final name only when the archive is
   settled ARCHIVED (``settle_archived``), so it never describes a file the
   archive does not hold.

"Khôi phục bản xuất" and the startup reconciliation live in
``source_archive_restore``; the archive manifest and the moves that never
overwrite live in ``source_archive_files``.

Every source-file action shares ``source_cleanup.SOURCE_FILE_LOCK`` (taken
non-blocking: a second request is refused). This module never deletes or
rewrites anything in ``archive/`` (only its own unfinished ``.tmp`` manifest),
never writes reports, review decisions, brand/studio memory or other outputs,
and never imports ``control_center`` or ``scheduler``.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from biliflow import recycle_bin, source_cleanup
from biliflow import source_archive_files as files
from biliflow.export_guards import REVIEW_QUEUE_IO, ActionConflict
from biliflow.job_store import now_iso, sha256_file


_JOB_KEY = re.compile(r"[\w-]{1,120}")
_PREVIEW_ID = re.compile(r"[0-9a-f]{64}")
_UNSET = object()

# Request errors.
JOB_IDS_MESSAGE = "Chọn từ 1 đến 50 video mỗi lần lưu trữ."
PREVIEW_ID_MESSAGE = "Thiếu mã xem trước; hãy mở lại hộp thoại lưu trữ."
NOTHING_ELIGIBLE_MESSAGE = "Không có video nào lưu trữ được trong danh sách đã chọn."

# Why a video cannot be archived (its own rows first, then the cleanup checks).
REASON_PENDING = "Đang lưu trữ video gốc này"
REASON_ARCHIVED = "Video gốc đã được lưu trữ"
REASON_RESTORING = "Đang đưa video gốc này từ kho lưu trữ về input"
REASON_CLEANED = "Video gốc đang ở Thùng rác (đã dọn); khôi phục nó về input trước khi lưu trữ"
REASON_STATE = "Chỉ lưu trữ được video đã xuất hoặc đã bỏ qua (mục “Hoàn tất”)"
REASON_JOB_KEY = "Mã video không dùng được làm tên thư mục lưu trữ"
REASON_TARGET_EXISTS = "Kho lưu trữ đã có file “{name}” của video này; BiliFlow không ghi đè"
REASON_TARGET_LONG = "Đường dẫn trong kho lưu trữ sẽ dài hơn 259 ký tự; không lưu trữ được video này"
REASON_FOLDER = (
    "Thư mục “{name}” của kho lưu trữ là liên kết (symlink/junction) hoặc không phải thư mục; "
    "không lưu trữ"
)
REASON_OTHER_VOLUME = "Kho lưu trữ không cùng ổ đĩa với thư mục input; không lưu trữ"

# Per-video results and events.
ARCHIVED_MESSAGE = "Đã lưu trữ video gốc"
ARCHIVED_EXPORT_MESSAGE = "Đã lưu trữ video gốc; bản xuất đã vào Thùng rác"
UNVERIFIED_MESSAGE = (
    "Đã lưu trữ video gốc; bản xuất đã rời thư mục output nhưng không tìm thấy bản ghi "
    "trong Thùng rác; hãy kiểm tra Thùng rác."
)
STOPPING_MESSAGE = "BiliFlow đang tắt; video này chưa được lưu trữ."
STOPPED_ROLLBACK_MESSAGE = "BiliFlow đang tắt; đã đưa video gốc về input, chưa lưu trữ."
PENDING_EVENT_MESSAGE = (
    "Windows chưa trả lời; bản xuất đang chờ xác nhận chuyển vào Thùng rác, video gốc đã ở kho lưu trữ"
)
MANIFEST_WRITE_MESSAGE = "Không ghi được {name} vào kho lưu trữ: {error}"
MANIFEST_NAME_WARNING = "Chưa đặt được tên {name} cho manifest trong kho lưu trữ ({error}); nội dung vẫn ở {name}.tmp."
MANIFEST_MISSING_WARNING = "Không thấy manifest {name} (hay {name}.tmp) trong kho lưu trữ."
VERIFY_READ_MESSAGE = (
    "Không đọc được video gốc trong kho lưu trữ để kiểm tra SHA-256 ({error}); đã đưa video gốc về input."
)
MOVE_LOCKED_MESSAGE = (
    "Video gốc đang được mở (ví dụ đang phát trong trang duyệt). Đóng trang duyệt của video này "
    "rồi thử lại."
)
MOVE_FAILED_MESSAGE = "Không chuyển được video gốc vào kho lưu trữ: {error}"
SHA_MISMATCH_MESSAGE = "Video gốc trong kho lưu trữ không khớp SHA-256; đã đưa video gốc về input."
EXPORT_FAILED_MESSAGE = "Không chuyển được bản xuất vào Thùng rác ({error}); đã đưa video gốc về input."
MANIFEST_LEFT_MESSAGE = "Bản xuất đã vào Thùng rác nhưng manifest của nó thì chưa: {error}"
MANIFEST_LATE_MESSAGE = (
    "Bản xuất vào Thùng rác sau khi Windows trả lời chậm; manifest của nó vẫn còn trong output."
)
ROLLBACK_FAILED_MESSAGE = (
    "Không đưa được video gốc về input ({error}); video gốc vẫn ở “{archive}”. "
    "Hãy chép nó về “{source}” rồi khởi động lại BiliFlow."
)


@dataclass(frozen=True)
class ArchiveAssessment:
    job_id: int
    name: str
    eligible: bool
    reason: str | None
    kind: str | None
    source_path: str
    size_bytes: int | None
    mtime_ns: int | None
    archive_path: str | None
    output_path: str | None
    output_bytes: int | None
    output_sha256: str | None
    output_manifest_path: str | None
    output_manifest_bytes: int | None
    exported_at: str | None
    skipped_at: str | None


def parse_job_ids(value: Any) -> list[int]:
    """source_cleanup.parse_job_ids with the archive wording."""
    try:
        return source_cleanup.parse_job_ids(value)
    except ValueError:
        raise ValueError(JOB_IDS_MESSAGE) from None


def archive_folder(root: Any, job_key: str) -> Path:
    """``<root>/archive/sources/<job_key>``: the root is always the one given (never INSTALL_ROOT)."""
    return Path(root) / "archive" / "sources" / job_key


def _relative(root: Path, path: Any) -> str:
    return source_cleanup._relative(root, Path(path))


# --------------------------------------------------------------------------
# Eligibility (never hashes).


def _archive_reason(reason: str | None) -> str:
    """A "Dọn video gốc" refusal in the words of "Lưu trữ"."""
    if reason == source_cleanup.REASON_STATE:
        return REASON_STATE
    if reason == source_cleanup.REASON_RECYCLED:
        return REASON_CLEANED
    text = str(reason or "")
    return text.replace("trước khi dọn)", "trước khi lưu trữ)").replace("; không dọn.", "; không lưu trữ.")


def _own_refusal(cleanup_row: Any, archive_row: Any) -> str | None:
    archive_state = archive_row.get("state") if isinstance(archive_row, dict) else None
    if archive_state == "PENDING":
        return REASON_PENDING
    if archive_state == "ARCHIVED":
        return REASON_ARCHIVED
    if archive_state == "RESTORING":
        return REASON_RESTORING
    cleanup_state = cleanup_row.get("state") if isinstance(cleanup_row, dict) else None
    if cleanup_state == "PENDING":
        return source_cleanup.REASON_PENDING
    if cleanup_state == "RECYCLED":
        return REASON_CLEANED
    return None


def _base_values(job: dict[str, Any], base: Any) -> dict[str, Any]:
    if base is not None:
        values = {
            field: getattr(base, field) for field in (
                "job_id", "name", "kind", "source_path", "size_bytes", "mtime_ns", "output_path",
                "output_bytes", "output_sha256", "exported_at", "skipped_at",
            )
        }
    else:
        raw = str(job.get("source_path") or "")
        values = {
            "job_id": int(job["id"]), "name": Path(raw).name or str(job.get("job_key") or ""),
            "kind": source_cleanup.KIND_BY_STATE.get(job.get("state")),
            "source_path": os.path.abspath(raw) if raw else "", "size_bytes": job.get("source_size_bytes"),
            "mtime_ns": None, "output_path": None, "output_bytes": None, "output_sha256": None,
            "exported_at": None, "skipped_at": None,
        }
    values.update(archive_path=None, output_manifest_path=None, output_manifest_bytes=None)
    return values


def _target_refusal(root: Path, job: dict[str, Any], base: Any, values: dict[str, Any]) -> str | None:
    """The archive-only checks of an eligible cleanup assessment; fills ``values``."""
    job_key = str(job.get("job_key") or "")
    if _JOB_KEY.fullmatch(job_key) is None:
        return REASON_JOB_KEY
    source = Path(base.source_path)
    folder = archive_folder(root, job_key)
    target = folder / source.name
    values["archive_path"] = str(target)
    if max(len(str(target)), len(str(folder)) + 1 + files.MANIFEST_NAME_ROOM) > recycle_bin.MAX_PATH_CHARS:
        return REASON_TARGET_LONG
    for path in (root / "archive", root / "archive" / "sources", folder):
        if os.path.lexists(path) and (files.is_link(path) or not path.is_dir()):
            return REASON_FOLDER.format(name=_relative(root, path))
    if os.path.lexists(target):
        return REASON_TARGET_EXISTS.format(name=source.name)
    existing = next(path for path in (folder, folder.parent, folder.parent.parent, root) if path.exists())
    if files.device(existing) != files.device(root / "input"):
        return REASON_OTHER_VOLUME
    if base.kind != "EXPORTED":
        return None
    output = root / str(base.output_path)
    manifest = output.with_name(output.name + ".manifest.json")
    for path in (output, manifest):
        # Directly in output/, never a subfolder or an alternate data stream (the recycler's own rule).
        refusal = recycle_bin.export_path_refusal(os.path.abspath(path), output_root=root / "output")
        if refusal:
            return refusal
    values["output_manifest_path"] = _relative(root, manifest)
    values["output_manifest_bytes"] = int(manifest.stat().st_size)
    return None


def assess_archive(
    root: Any, store: Any, scheduler: Any, job: dict[str, Any], *,
    cleanup_row: Any = _UNSET, archive_row: Any = _UNSET, base: Any = None, fresh: bool = False,
) -> ArchiveAssessment:
    """Whether ``job``'s source may be archived now, and why not. Never hashes.

    ``cleanup_row``/``archive_row`` are the job's latest rows (None for none;
    omitted, read from the store). ``base`` is the job's
    ``source_cleanup.assess_job`` result when the caller already has it; it is
    computed (through the module, so tests can patch it) only when needed.
    """
    root = Path(root)
    job_id = int(job["id"])
    if cleanup_row is _UNSET:
        cleanup_row = store.latest_source_cleanup(job_id)
    if archive_row is _UNSET:
        archive_row = store.latest_source_archive(job_id)
    own = _own_refusal(cleanup_row, archive_row)
    if own is None and base is None:
        base = source_cleanup.assess_job(
            root, store, scheduler, job, latest_row=cleanup_row, archive_row=archive_row, fresh=fresh,
        )
    values = _base_values(job, None if own else base)
    reason = own or (None if base.eligible else _archive_reason(base.reason))
    if reason is None:
        try:
            reason = _target_refusal(root, job, base, values)
        except OSError as error:
            reason = source_cleanup.UNEXPECTED_MESSAGE.format(error=error)
    return ArchiveAssessment(eligible=reason is None, reason=reason, **values)


def archive_hint(root: Any, store: Any, scheduler: Any, job: dict[str, Any], *, cleanup_row: Any,
                 archive_row: Any, base: Any = None) -> dict[str, Any] | None:
    """The "Lưu trữ" hint of a "Hoàn tất" card (None for every other state). Never hashes."""
    if job.get("state") not in source_cleanup.ELIGIBLE_STATES:
        return None
    item = assess_archive(root, store, scheduler, job, cleanup_row=cleanup_row, archive_row=archive_row, base=base)
    return {
        "eligible": item.eligible, "kind": item.kind, "reason": item.reason, "size_bytes": item.size_bytes,
        "output_name": Path(item.output_path).name if item.output_path else None,
        "output_bytes": item.output_bytes, "manifest_bytes": item.output_manifest_bytes,
        "exported_at": item.exported_at, "skipped_at": item.skipped_at,
    }


def archive_row_summary(row: dict[str, Any] | None, check: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """A ``source_archives`` row for the Dashboard and the review page.

    ``check`` is the row's entry of ``recycle_check_summary('ARCHIVE_EXPORT')``:
    a later "Kiểm tra lại Thùng rác" that found the export verifies it.
    """
    if row is None:
        return None
    source_path, output = str(row.get("source_path") or ""), str(row.get("output_path") or "")
    found_later = bool(check and check.get("found"))
    state = row.get("state")
    return {
        "id": row.get("id"), "state": state, "kind": row.get("kind"), "size_bytes": row.get("size_bytes"),
        "file_name": Path(source_path).name if source_path else "", "source_path": source_path,
        "archive_path": row.get("archive_path"), "output_name": Path(output).name if output else None,
        "output_bytes": row.get("output_bytes"), "created_at": row.get("created_at"),
        "archived_at": row.get("archived_at"), "restored_at": row.get("restored_at"),
        "export_recycled": bool(row.get("export_recycled")),
        "export_verified": bool(row.get("export_verified")) or found_later,
        "export_verified_at_archive": bool(row.get("export_verified")),
        "export_verified_later_at": check.get("checked_at") if found_later else None,
        "export_rechecked_at": check.get("checked_at") if check else None,
        "warning": row.get("error") if state in ("ARCHIVED", "RESTORING") else None,
        "error": row.get("error") if state == "FAILED" else None,
    }


# --------------------------------------------------------------------------
# Preview.


def _preview_id(eligible: list[ArchiveAssessment]) -> str:
    rows = sorted([
        [item.job_id, os.path.normcase(item.source_path), item.size_bytes, item.mtime_ns, item.kind,
         item.output_path or "", item.output_bytes or 0, item.output_manifest_bytes or 0,
         os.path.normcase(item.archive_path or "")]
        for item in eligible
    ])
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def _freed(item: ArchiveAssessment) -> int:
    return int(item.output_bytes or 0) + int(item.output_manifest_bytes or 0) if item.kind == "EXPORTED" else 0


def _preview(root: Path, store: Any, scheduler: Any, job_ids: Any, *, bin_info: Callable[[Path], Any],
             fresh: bool) -> tuple[dict[str, Any], list[ArchiveAssessment]]:
    ids = parse_job_ids(job_ids)
    cleanups, archives = store.latest_source_cleanups(), store.latest_source_archives()
    eligible: list[ArchiveAssessment] = []
    ineligible: list[dict[str, Any]] = []
    for job_id in ids:
        try:
            job = store.get_job(job_id)
        except KeyError:
            ineligible.append({"job_id": job_id, "name": "",
                               "reason": source_cleanup.UNKNOWN_JOB_MESSAGE.format(job_id=job_id)})
            continue
        item = assess_archive(root, store, scheduler, job, cleanup_row=cleanups.get(job_id),
                              archive_row=archives.get(job_id), fresh=fresh)
        if item.eligible:
            eligible.append(item)
        else:
            ineligible.append({"job_id": job_id, "name": item.name, "reason": item.reason})
    freed = sum(_freed(item) for item in eligible)
    recycle, blocked = None, None
    # The bin matters only when an exported video is in the batch.
    if any(item.kind == "EXPORTED" for item in eligible):
        try:
            info = bin_info(root / "output")
        except recycle_bin.RecycleRefused as error:
            blocked = str(error)
        else:
            recycle = {
                "volume": info.volume, "used_bytes": info.used_bytes, "items": info.items,
                "max_bytes": info.max_bytes, "after_bytes": info.used_bytes + freed,
            }
            blocked = recycle_bin.capacity_refusal(info, freed, message=recycle_bin.EXPORT_CAPACITY_MESSAGE)
    preview = {
        "preview_id": _preview_id(eligible),
        "eligible": [
            {
                "job_id": item.job_id, "name": item.name, "file_name": Path(item.source_path).name,
                "kind": item.kind, "size_bytes": item.size_bytes,
                "archive_path": _relative(root, item.archive_path),
                "output_name": Path(item.output_path).name if item.output_path else None,
                "output_bytes": item.output_bytes, "manifest_bytes": item.output_manifest_bytes,
                "exported_at": item.exported_at, "skipped_at": item.skipped_at,
            }
            for item in eligible
        ],
        "ineligible": ineligible,
        "count": len(eligible),
        "archive_bytes": sum(int(item.size_bytes or 0) for item in eligible),
        "freed_bytes": freed,
        "recycle_bin": recycle,
        "blocked": blocked,
    }
    return preview, eligible


def preview_archive(root: Any, store: Any, scheduler: Any, job_ids: Any, *,
                    bin_info: Callable[[Path], Any], fresh: bool = False) -> dict[str, Any]:
    """What an archive of ``job_ids`` would do. Read-only: no hash, no write, no recycler."""
    preview, _ = _preview(Path(root), store, scheduler, job_ids, bin_info=bin_info, fresh=fresh)
    return preview


# --------------------------------------------------------------------------
# Execute.


def _result(item: ArchiveAssessment, status: str, message: str, *, output_bytes: int = 0,
            manifest_bytes: int = 0) -> dict[str, Any]:
    return {
        "job_id": item.job_id, "name": item.name, "status": status, "message": message,
        "size_bytes": item.size_bytes, "output_bytes": output_bytes, "manifest_bytes": manifest_bytes,
    }


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "archive_id": row.get("id"), "kind": row.get("kind"), "phase": row.get("phase"),
        "source_path": row.get("source_path"), "archive_path": row.get("archive_path"),
        "manifest_path": row.get("manifest_path"), "size_bytes": row.get("size_bytes"),
        "sha256": row.get("source_sha256"), "output_path": row.get("output_path"),
        "output_bytes": row.get("output_bytes"), "export_recycled": bool(row.get("export_recycled")),
        "export_verified": bool(row.get("export_verified")), "export_record": row.get("export_record"),
        "manifest_recycled": bool(row.get("manifest_recycled")), "manifest_record": row.get("manifest_record"),
        "warning": row.get("error"), "archived_at": row.get("archived_at"),
    }


def _name_manifest(row: dict[str, Any]) -> str | None:
    """The archive succeeded: give the row's manifest ``.tmp`` its final name; a warning when it cannot."""
    final = Path(str(row["manifest_path"]))
    temp = files.manifest_temp(final)
    if not os.path.lexists(temp):
        return None if os.path.lexists(final) else MANIFEST_MISSING_WARNING.format(name=final.name)
    try:
        files.publish_manifest(temp, final)
    except OSError as error:
        return MANIFEST_NAME_WARNING.format(name=final.name, error=error)
    return None


def discard_row_temp(store: Any, row_id: int) -> None:
    """The source is (back) in input/: the row's manifest ``.tmp``, written by its own run, describes nothing."""
    row = store.get_source_archive(row_id)
    if row is not None and row.get("manifest_path"):
        files.discard_manifest_temp(files.manifest_temp(row["manifest_path"]))


def settle_archived(store: Any, row_id: int, *, warning: str | None = None, **facts: Any) -> dict | None:
    """PENDING -> ARCHIVED: name the manifest, reset the watcher and log; None when settled already.

    ``settle_archived``, ``fail_archive`` and ``roll_back`` are shared with the
    startup reconciliation (``source_archive_restore``).
    """
    current = store.get_source_archive(row_id)
    if current is None or current["state"] != "PENDING":
        return None
    text = "; ".join(part for part in (warning, _name_manifest(current)) if part) or None
    row = store.finish_source_archive(row_id, state="ARCHIVED", error=text, **facts)
    if row is None:
        return None
    store.reset_watched_file(Path(row["source_path"]))
    exported = row["kind"] == "EXPORTED"
    if exported and not row["export_verified"]:
        store.add_event(row["job_id"], "SOURCE_ARCHIVE_UNVERIFIED", UNVERIFIED_MESSAGE, level="WARNING",
                        payload=_payload(row))
    else:
        store.add_event(row["job_id"], "SOURCE_ARCHIVED",
                        ARCHIVED_EXPORT_MESSAGE if exported else ARCHIVED_MESSAGE, payload=_payload(row))
    return row


def fail_archive(store: Any, row_id: int, job_id: int, stage: str, message: str) -> None:
    """PENDING -> FAILED with the source in input/ (nothing moved, or moved back)."""
    store.finish_source_archive(row_id, state="FAILED", error=message)
    store.add_event(job_id, "SOURCE_ARCHIVE_FAILED", message, level="WARNING",
                    payload={"stage": stage, "archive_id": row_id, "reason": message})


def roll_back(store: Any, row_id: int, job_id: int, source_path: Any, archive_path: Any, message: str, *,
              stage: str) -> tuple[str, str]:
    """Rename the archived source back to input/: ('FAILED', message), or ('PENDING', why not)."""
    source, target = Path(source_path), Path(archive_path)
    try:
        files.rename_without_overwrite(target, source)
    except OSError as error:
        text = ROLLBACK_FAILED_MESSAGE.format(error=error, archive=target, source=source)
        # The row stays PENDING: the job stays locked until someone resolves it.
        store.add_event(job_id, "SOURCE_ARCHIVE_FAILED", text, level="ERROR",
                        payload={"stage": "rollback", "archive_id": row_id, "reason": message})
        return "PENDING", text
    discard_row_temp(store, row_id)
    fail_archive(store, row_id, job_id, stage, message)
    return "FAILED", message


def _roll_back_item(store: Any, row_id: int, item: ArchiveAssessment, message: str, *, stage: str) -> tuple[str, str]:
    return roll_back(store, row_id, item.job_id, item.source_path, item.archive_path, message, stage=stage)


def _recheck(root: Path, store: Any, scheduler: Any, item: ArchiveAssessment, checked: dict[str, Any],
             manifest_stat: Any, reading: Any) -> str | None:
    """Inside REVIEW_QUEUE_IO and job_action_lock: why the hashed video may no longer go, or None.

    ``reading`` is the bin as read just before the locks (``_export_facts``).
    """
    job = store.get_job(item.job_id)
    fresh = assess_archive(root, store, scheduler, job, fresh=True)
    if not fresh.eligible:
        return fresh.reason
    identity = ("kind", "size_bytes", "output_path", "output_bytes", "output_sha256", "output_manifest_bytes")
    same = all(getattr(fresh, name) == getattr(item, name) for name in identity) and all(
        os.path.normcase(str(getattr(fresh, name))) == os.path.normcase(str(getattr(item, name)))
        for name in ("source_path", "archive_path")
    )
    if not same or str(job.get("source_sha256") or "").lower() != checked["sha256"]:
        return source_cleanup.PREVIEW_CHANGED_MESSAGE
    try:
        if source_cleanup._stat_key(os.stat(item.source_path)) != source_cleanup._stat_key(checked["stat"]):
            return source_cleanup.SOURCE_CHANGED_DURING_HASH
    except OSError:
        return source_cleanup.REASON_SOURCE_MISSING
    if item.kind != "EXPORTED":
        return None
    try:
        for path, before in ((item.output_path, checked["output_stat"]), (item.output_manifest_path, manifest_stat)):
            if source_cleanup._stat_key(os.stat(root / str(path))) != source_cleanup._stat_key(before):
                return source_cleanup.REASON_OUTPUT_CHANGED
    except OSError:
        return source_cleanup.REASON_OUTPUT_CHANGED
    return recycle_bin.capacity_refusal(reading, _freed(item), message=recycle_bin.EXPORT_CAPACITY_MESSAGE)


def _export_facts(root: Path, item: ArchiveAssessment,
                  bin_info: Callable[[Path], Any]) -> tuple[Any, Any, str | None]:
    """Before the locks: (the export manifest's stat, the bin reading, why not)."""
    try:
        manifest_stat = os.stat(root / str(item.output_manifest_path))
    except OSError:
        return None, None, source_cleanup.REASON_MANIFEST
    try:
        # A registry read and a shell query: never while a lock is held.
        return manifest_stat, bin_info(root / "output"), None
    except recycle_bin.RecycleRefused as error:
        return manifest_stat, None, str(error)


def _move_error(error: OSError, item: ArchiveAssessment) -> str:
    if isinstance(error, FileExistsError):
        return REASON_TARGET_EXISTS.format(name=Path(item.source_path).name)
    winerror = getattr(error, "winerror", None)
    if isinstance(error, PermissionError) or winerror in (5, 32, 33):
        return MOVE_LOCKED_MESSAGE
    if getattr(error, "errno", None) == errno.EXDEV or winerror == 17:
        return REASON_OTHER_VOLUME
    return MOVE_FAILED_MESSAGE.format(error=error)


def _late_export_callback(root: Path, store: Any, row_id: int, item: ArchiveAssessment) -> Callable:
    """Settles the PENDING row when Windows answers after the timeout (recycle thread)."""

    def on_late_result(result: Any, error: BaseException | None) -> None:
        try:
            if error is None and result is not None:
                if store.set_source_archive_phase(
                    row_id, "EXPORT_RECYCLED", export_recycled=True, export_verified=bool(result.verified),
                    export_record=result.record_path,
                ):
                    settle_archived(store, row_id, warning=MANIFEST_LATE_MESSAGE)
            elif files.file_with_size(root / str(item.output_path), item.output_bytes):
                _roll_back_item(store, row_id, item, EXPORT_FAILED_MESSAGE.format(error=error), stage="recycle")
            # Otherwise the export left output/ without an answer: the next start settles it.
        except Exception:  # noqa: BLE001 - e.g. the store was closed at shutdown
            pass

    return on_late_result


def _recycle_manifest(root: Path, store: Any, row_id: int, item: ArchiveAssessment, *, recycler: Callable,
                      timeout: float) -> tuple[str | None, dict[str, Any], bool]:
    """The export's manifest after the export: (warning, row facts, stop the batch). Never fatal."""
    store.set_source_archive_phase(row_id, "MANIFEST_RECYCLING")
    path = root / str(item.output_manifest_path)
    try:
        outcome = recycler(path, allowed_root=root / "output", expected_size=int(item.output_manifest_bytes),
                           timeout=timeout, on_late_result=lambda result, error: None)
    except recycle_bin.RecycleTimeout as error:
        return MANIFEST_LEFT_MESSAGE.format(error=error), {}, True
    except Exception as error:  # noqa: BLE001 - the export is already in the bin: a warning only
        return MANIFEST_LEFT_MESSAGE.format(error=error), {}, False
    return None, {"manifest_recycled": True, "manifest_record": outcome.record_path}, False


def _place_source(store: Any, row_id: int, item: ArchiveAssessment, document: dict) -> dict | None:
    """The manifest ``.tmp``, then the rename into archive/; a result when it stopped."""
    job_id, source, target = item.job_id, Path(item.source_path), Path(item.archive_path)
    manifest_path = Path(document["manifest_path"])
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = files.write_manifest_temp(manifest_path, document)
    except Exception as error:  # noqa: BLE001 - e.g. text that is not UTF-8; nothing has moved yet
        message = MANIFEST_WRITE_MESSAGE.format(name=manifest_path.name, error=error)
        fail_archive(store, row_id, job_id, "manifest", message)
        return _result(item, "FAILED", message)
    try:
        files.rename_without_overwrite(source, target)
    except OSError as error:
        files.discard_manifest_temp(temp)  # nothing moved, so no manifest may describe it
        message = _move_error(error, item)
        fail_archive(store, row_id, job_id, "move", message)
        return _result(item, "FAILED", message)
    store.set_source_archive_phase(row_id, "SOURCE_MOVED")
    return None


def _verify_copy(store: Any, row_id: int, item: ArchiveAssessment, checked: dict[str, Any],
                 hasher: Callable[[Path], str]) -> dict | None:
    """The archived copy against the precheck; a result (after the rollback) when it is not the same."""
    target = Path(item.archive_path)
    try:
        moved = os.stat(target)
        same = (
            str(hasher(target)).lower() == checked["sha256"] and moved.st_size == checked["stat"].st_size
            and moved.st_mtime_ns == checked["stat"].st_mtime_ns
        )
    except OSError as error:
        status, message = _roll_back_item(store, row_id, item, VERIFY_READ_MESSAGE.format(error=error),
                                          stage="verify")
        return _result(item, status, message)
    if not same:
        status, message = _roll_back_item(store, row_id, item, SHA_MISMATCH_MESSAGE, stage="verify")
        return _result(item, status, message)
    store.set_source_archive_phase(row_id, "SOURCE_VERIFIED", source_verified=True)
    return None


def _recycle_export(root: Path, store: Any, row_id: int, item: ArchiveAssessment, *, recycler: Callable,
                    timeout: float) -> tuple[dict, bool]:
    """An exported job's export, then its manifest; (result, stop_the_rest)."""
    job_id = item.job_id
    store.set_source_archive_phase(row_id, "EXPORT_RECYCLING")
    output = root / str(item.output_path)
    try:
        outcome = recycler(output, allowed_root=root / "output", expected_size=int(item.output_bytes),
                           timeout=timeout, on_late_result=_late_export_callback(root, store, row_id, item))
    except recycle_bin.RecycleTimeout as error:
        store.add_event(job_id, "SOURCE_ARCHIVE_PENDING", PENDING_EVENT_MESSAGE, level="WARNING",
                        payload={"archive_id": row_id, "output_path": item.output_path})
        return _result(item, "PENDING", str(error)), True
    except Exception as error:  # noqa: BLE001 - every failure is settled and reported
        # A refusal touched nothing (the export may have left output/ on its own meanwhile);
        # after any other error only an export still in output/ proves the source may go back.
        if isinstance(error, recycle_bin.RecycleRefused) or files.file_with_size(output, item.output_bytes):
            status, message = _roll_back_item(store, row_id, item, EXPORT_FAILED_MESSAGE.format(error=error),
                                              stage="recycle")
            return _result(item, status, message), False
        message = source_cleanup.UNEXPECTED_MESSAGE.format(error=error)
        # The export left output/ without a result: the next start settles the row.
        store.add_event(job_id, "SOURCE_ARCHIVE_FAILED", message, level="ERROR",
                        payload={"stage": "recycle", "archive_id": row_id, "reason": message})
        return _result(item, "PENDING", message), False
    store.set_source_archive_phase(
        row_id, "EXPORT_RECYCLED", export_recycled=True, export_verified=bool(outcome.verified),
        export_record=outcome.record_path,
    )
    warning, facts, stop = _recycle_manifest(root, store, row_id, item, recycler=recycler, timeout=timeout)
    settle_archived(store, row_id, warning=warning, **facts)
    freed = {"output_bytes": int(item.output_bytes or 0),
             "manifest_bytes": int(item.output_manifest_bytes or 0) if facts else 0}
    if outcome.verified:
        return _result(item, "ARCHIVED", ARCHIVED_EXPORT_MESSAGE, **freed), stop
    return _result(item, "UNVERIFIED", UNVERIFIED_MESSAGE, **freed), stop


def _move_and_recycle(root: Path, store: Any, row_id: int, item: ArchiveAssessment, document: dict,
                      checked: dict[str, Any], *, recycler: Callable, hasher: Callable[[Path], str],
                      timeout: float, should_stop: Callable[[], bool] | None) -> tuple[dict, bool]:
    """Section 3 (no lock): manifest, rename, verify, then the export; (result, stop_the_rest)."""
    if should_stop is not None and should_stop():
        fail_archive(store, row_id, item.job_id, "stop", STOPPING_MESSAGE)
        return _result(item, "NOT_RUN", STOPPING_MESSAGE), False
    stopped = _place_source(store, row_id, item, document) or _verify_copy(store, row_id, item, checked, hasher)
    if stopped is not None:
        return stopped, False
    if item.kind != "EXPORTED":
        settle_archived(store, row_id)
        return _result(item, "ARCHIVED", ARCHIVED_MESSAGE), False
    if should_stop is not None and should_stop():
        status, message = _roll_back_item(store, row_id, item, STOPPED_ROLLBACK_MESSAGE, stage="stop")
        return _result(item, "NOT_RUN" if status == "FAILED" else status, message), False
    return _recycle_export(root, store, row_id, item, recycler=recycler, timeout=timeout)


def _archive_one(root: Path, store: Any, scheduler: Any, item: ArchiveAssessment, *, recycler: Callable,
                 bin_info: Callable[[Path], Any], hasher: Callable[[Path], str], timeout: float,
                 should_stop: Callable[[], bool] | None) -> tuple[dict[str, Any], bool]:
    """One video through the three sections; returns (result, stop_the_rest)."""
    job_id = item.job_id
    checked, reason = source_cleanup.precheck(root, item, str(store.get_job(job_id).get("source_sha256") or ""),
                                              hasher)
    manifest_stat, reading = None, None
    if reason is None and item.kind == "EXPORTED":
        manifest_stat, reading, reason = _export_facts(root, item, bin_info)
    stage, row_id, document = "precheck", None, None
    if reason is None:
        stage = "recheck"
        with REVIEW_QUEUE_IO, scheduler.job_action_lock:
            reason = _recheck(root, store, scheduler, item, checked, manifest_stat, reading)
            if reason is None:
                job = store.get_job(job_id)
                try:
                    document = files.snapshot(root, store, job, item, checked)
                except (OSError, ValueError, TypeError, KeyError, AttributeError):
                    reason = source_cleanup.REASON_QUEUE_UNREADABLE
            if reason is None:
                manifest_path = files.free_manifest_path(Path(item.archive_path).parent)
                row_id = store.add_source_archive(
                    job_id=job_id, kind=item.kind, source_path=item.source_path,
                    archive_path=item.archive_path, manifest_path=str(manifest_path),
                    source_sha256=checked["sha256"], size_bytes=int(item.size_bytes),
                    mtime_ns=int(checked["stat"].st_mtime_ns), queue_path=str(job.get("active_queue_path") or ""),
                    revision=job.get("active_revision"), output_path=item.output_path,
                    output_sha256=checked["output_sha256"], output_bytes=item.output_bytes,
                    output_manifest_path=item.output_manifest_path,
                    output_manifest_bytes=item.output_manifest_bytes, exported_at=item.exported_at,
                    skipped_at=item.skipped_at,
                )
                document.update(archive_id=row_id, created_at=now_iso(), manifest_path=str(manifest_path))
    if row_id is None:
        store.add_event(job_id, "SOURCE_ARCHIVE_FAILED", reason, level="WARNING",
                        payload={"stage": stage, "reason": reason})
        return _result(item, "FAILED", reason), False
    return _move_and_recycle(root, store, row_id, item, document, checked, recycler=recycler, hasher=hasher,
                             timeout=timeout, should_stop=should_stop)


def execute_archive(
    root: Any, store: Any, scheduler: Any, job_ids: Any, preview_id: Any, *,
    recycler: Callable, bin_info: Callable[[Path], Any],
    hasher: Callable[[Path], str] = sha256_file, timeout: float = 60.0,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Archive the confirmed videos one by one.

    ``recycler`` has no default (the signature of
    ``recycle_bin.send_export_to_recycle_bin``) and is called only for the
    export of an exported job and its manifest; ``bin_info`` is called with
    ``root / 'output'`` and only when an exported job is in the batch. Raises
    ValueError for a bad request and ActionConflict ('busy',
    'preview_changed', 'bin_unavailable', 'bin_capacity') when nothing may
    start; otherwise every eligible video gets a result.
    """
    root = Path(root)
    ids = parse_job_ids(job_ids)
    if not isinstance(preview_id, str) or _PREVIEW_ID.fullmatch(preview_id) is None:
        raise ValueError(PREVIEW_ID_MESSAGE)
    if not source_cleanup.SOURCE_FILE_LOCK.acquire(blocking=False):
        raise ActionConflict("busy", source_cleanup.SOURCE_BUSY_MESSAGE)
    try:
        if recycle_bin.operations_in_progress():
            raise ActionConflict("busy", source_cleanup.SOURCE_BUSY_MESSAGE)
        preview, eligible = _preview(root, store, scheduler, ids, bin_info=bin_info, fresh=True)
        if preview["preview_id"] != preview_id:
            raise ActionConflict("preview_changed", source_cleanup.PREVIEW_CHANGED_MESSAGE, preview)
        if any(item.kind == "EXPORTED" for item in eligible) and preview["recycle_bin"] is None:
            raise ActionConflict("bin_unavailable", preview["blocked"], preview)
        if preview["blocked"]:
            raise ActionConflict("bin_capacity", preview["blocked"], preview)
        if not eligible:
            raise ValueError(NOTHING_ELIGIBLE_MESSAGE)
        results: list[dict[str, Any]] = []
        stopped = False
        for item in eligible:
            if stopped:
                results.append(_result(item, "NOT_RUN", source_cleanup.NOT_RUN_MESSAGE))
                continue
            if should_stop is not None and should_stop():
                results.append(_result(item, "NOT_RUN", STOPPING_MESSAGE))
                continue
            try:
                result, stopped = _archive_one(root, store, scheduler, item, recycler=recycler,
                                               bin_info=bin_info, hasher=hasher, timeout=timeout,
                                               should_stop=should_stop)
            except Exception as error:  # noqa: BLE001 - one video never aborts the others
                message = source_cleanup.UNEXPECTED_MESSAGE.format(error=error)
                try:
                    store.add_event(item.job_id, "SOURCE_ARCHIVE_FAILED", message, level="ERROR",
                                    payload={"stage": "unexpected", "reason": message})
                except Exception:  # noqa: BLE001
                    pass
                result = _result(item, "FAILED", message)
            results.append(result)
    finally:
        source_cleanup.SOURCE_FILE_LOCK.release()
    moved = [item for item in results if item["status"] in ("ARCHIVED", "UNVERIFIED")]
    return {
        "results": results,
        "archived_count": len(moved),
        "archived_bytes": sum(int(item["size_bytes"] or 0) for item in moved),
        "freed_bytes": sum(item["output_bytes"] + item["manifest_bytes"] for item in moved),
        "failed_count": sum(1 for item in results if item["status"] == "FAILED"),
        "pending": sum(1 for item in results if item["status"] == "PENDING"),
    }
