"""Removing one job from BiliFlow ("Xóa video gốc", "Xóa video"): what goes and what is protected.

The user's decisions of 2026-10-05 (docs/DELETE_FLOW_PLAN.md). A removal
deletes the job's rows (``JobStore.purge_job``), its own report folders
directly in ``reports/jobs`` (``<job_key>`` and ``<job_key>-run-*``) and its
Control Center logs, files first and rows last. The callers delete the
source video and the export manifest before that, with the checks of
their action. Never deleted here: output videos, a folder another job
still names, a benchmark run, a link, anything outside ``reports/jobs`` and
the logs folder, and no job of a golden set (``protected_reason``) or one an
archive or a legacy cleanup row still locks.

Every deleting function refuses a project root other than the install
root or a folder inside its ``temp/`` (tests), and anything reached through
an input/, output/, reports/ or logs/ folder that is a link or junction, so
code run from a worktree never deletes files of the main folder. This
module never imports control_center, scheduler or source_cleanup.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from biliflow import recycle_bin
from biliflow.job_store import SOURCE_ARCHIVED_STATES


INSTALL_ROOT = recycle_bin.INSTALL_ROOT
BENCHMARK_MARKER = ".biliflow-benchmark"
GOLDEN_GLOB = "annotations/golden/*/segments.json"
EXPORT_MANIFEST_SUFFIX = "-reviewed.mp4.manifest.json"
_JOB_LOG = re.compile(r"job-\d+-[^\\/]+\.log", re.IGNORECASE)
_WINERROR_SHARING = 32

REASON_GOLDEN = "Video thuộc bộ nhãn vàng dùng để chấm detector (annotations/golden); BiliFlow không xóa video này"
REASON_GOLDEN_UNREADABLE = (
    "Không đọc được {path}; tạm khóa mọi thao tác xóa để không xóa nhầm video của bộ nhãn vàng"
)
ROOT_MESSAGE = "Chỉ xóa được trong thư mục cài BiliFlow (hoặc thư mục temp của nó khi chạy test)."
FOLDER_LINK_MESSAGE = (
    "Thư mục “{name}” của BiliFlow là liên kết (symlink/junction) hoặc không phải thư mục thật; "
    "không xóa gì trong đó."
)
LOCKED_MESSAGE = "Video đang được lưu trữ hoặc đang chuyển vào Thùng rác; BiliFlow không xóa video này."
PATH_MESSAGE = "Không xóa “{path}”: đây không phải thư mục báo cáo hay log của một video."
IN_USE_MESSAGE = (
    "Video gốc đang được mở (ví dụ trong trang duyệt hoặc một trình xem video). "
    "Đóng nó rồi thử lại; video gốc vẫn còn."
)
DENIED_MESSAGE = "Windows không cho xóa video gốc (file chỉ đọc hoặc không có quyền); video gốc vẫn còn."
FAILED_MESSAGE = "Không xóa được video gốc ({error}); video gốc vẫn còn."
MANIFEST_NAME_MESSAGE = (
    "Chỉ xóa manifest của bản xuất đã duyệt (tên kết thúc bằng “-reviewed.mp4.manifest.json”) "
    "nằm ngay trong thư mục output."
)
MANIFEST_FAILED_MESSAGE = "Không xóa được manifest của bản xuất ({error}); file .mp4 vẫn còn."
LINK_MESSAGE = "là liên kết (symlink/junction); không xóa"
FILE_FAILED_MESSAGE = "{path}: {error}"

DELETE_WORDING = recycle_bin.Wording(
    outside="Video gốc không nằm trong thư mục input; không xóa.",
    link="Video gốc là liên kết (symlink/junction); không xóa.",
    path="Đường dẫn video gốc dài hơn 259 ký tự hoặc có ký tự không hợp lệ; không xóa.",
    size="Video gốc không còn đúng như lúc kiểm tra (đổi dung lượng hoặc không còn); không xóa.",
    capacity="", aborted="", error_rc="", not_moved="",
)
MANIFEST_WORDING = recycle_bin.Wording(
    outside=MANIFEST_NAME_MESSAGE,
    link="Manifest của bản xuất là liên kết (symlink/junction); không xóa.",
    path="Đường dẫn manifest của bản xuất dài hơn 259 ký tự hoặc có ký tự không hợp lệ; không xóa.",
    size="Manifest của bản xuất không còn là một file thường; không xóa.",
    capacity="", aborted="", error_rc="", not_moved="",
)


class DeleteRefused(ValueError):
    """Refused before anything was deleted."""


class DeleteFailed(RuntimeError):
    """Windows did not delete the file; it is still there."""


# --------------------------------------------------------------------------
# Where deleting is allowed.


def _strictly_under(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath([child, parent]) == parent and child != parent
    except ValueError:
        return False


def allowed_project_root(root: Any) -> bool:
    """``root`` is the install root or a folder inside its temp/ (where tests build theirs)."""
    try:
        resolved = os.path.normcase(str(Path(root).resolve()))
        install = os.path.normcase(str(INSTALL_ROOT.resolve()))
        temp = os.path.normcase(str((INSTALL_ROOT / "temp").resolve()))
    except (OSError, RuntimeError):
        return False
    return resolved == install or _strictly_under(resolved, temp)


def _require_root(root: Any) -> Path:
    if not allowed_project_root(root):
        raise DeleteRefused(ROOT_MESSAGE)
    return Path(root).resolve()


def _is_link_info(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & recycle_bin.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _is_link(path: Any) -> bool:
    try:
        return _is_link_info(os.lstat(path))
    except OSError:
        return False


def _real_folder(root: Path, name: str) -> bool:
    """``root/name`` is a folder at its own path: no link or junction from the root down to it.

    Otherwise a junctioned input/, output/ or logs/ would let a delete reach
    files outside the install (both sides of the path checks resolve to the
    target).
    """
    try:
        path = Path(root).resolve()
        for part in Path(name).parts:
            path = path / part
            if _is_link(path):
                return False
        return path.is_dir() and os.path.normcase(str(path.resolve())) == os.path.normcase(str(path))
    except (OSError, RuntimeError):
        return False


def _display(root: Path, path: Path) -> str:
    try:
        return Path(os.path.abspath(path)).relative_to(root).as_posix()
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------
# Golden sets: their jobs are never deleted.


@dataclass(frozen=True)
class GoldenIndex:
    job_ids: frozenset[int]
    sha256s: frozenset[str]
    unreadable: str | None  # the first segments.json that could not be read, relative to the root


_GOLDEN_CACHE: dict[str, tuple[tuple, GoldenIndex]] = {}
_GOLDEN_LOCK = threading.Lock()  # leaf lock: never held during IO


def clear_caches() -> None:
    with _GOLDEN_LOCK:
        _GOLDEN_CACHE.clear()


def _read_golden(root: Path, files: list[Path]) -> GoldenIndex:
    ids: set[int] = set()
    shas: set[str] = set()
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            sources = data.get("sources") if isinstance(data, dict) else None
            if not isinstance(sources, dict):
                raise ValueError("sources is not an object")
            for source in sources.values():
                if not isinstance(source, dict):
                    raise ValueError("a source is not an object")
                job_id, digest = source.get("job_id"), source.get("sha256")
                if isinstance(job_id, bool) or not isinstance(job_id, int) or not isinstance(digest, str) or not digest:
                    raise ValueError("a source has no job id or sha256")
                ids.add(job_id)
                shas.add(digest.lower())
        except (OSError, ValueError, RecursionError):
            return GoldenIndex(frozenset(), frozenset(), path.relative_to(root).as_posix())
    return GoldenIndex(frozenset(ids), frozenset(shas), None)


def golden_index(root: Any) -> GoldenIndex:
    """Job ids and SHA-256 of every golden-set source, cached by the files' stat."""
    root = Path(root)
    files = sorted(root.glob(GOLDEN_GLOB))
    stamp = []
    for path in files:
        try:
            info = path.stat()
            stamp.append((path.as_posix(), int(info.st_mtime_ns), int(info.st_size)))
        except OSError:
            stamp.append((path.as_posix(), -1, -1))
    key = os.path.normcase(os.path.abspath(root))
    with _GOLDEN_LOCK:
        cached = _GOLDEN_CACHE.get(key)
    if cached is not None and cached[0] == tuple(stamp):
        return cached[1]
    index = _read_golden(root, files)
    with _GOLDEN_LOCK:
        _GOLDEN_CACHE[key] = (tuple(stamp), index)
    return index


def protected_reason(root: Any, job: dict[str, Any], *, index: GoldenIndex | None = None) -> str | None:
    """Why ``job`` must not be deleted (a golden-set source, or an unreadable golden set), or None."""
    index = golden_index(root) if index is None else index
    if index.unreadable:
        return REASON_GOLDEN_UNREADABLE.format(path=index.unreadable)
    if int(job["id"]) in index.job_ids or str(job.get("source_sha256") or "").lower() in index.sha256s:
        return REASON_GOLDEN
    return None


# --------------------------------------------------------------------------
# The files of a job.


def _report_folder(jobs_dir: Path, value: Any) -> str | None:
    """The folder name directly in reports/jobs that a recorded path lies in, or None."""
    text = str(value or "").replace("\\", "/")
    parts = Path(text).parts
    if len(parts) >= 3 and parts[0].casefold() == "reports" and parts[1].casefold() == "jobs":
        return parts[2]
    if os.path.isabs(text):
        child, parent = os.path.normcase(os.path.abspath(text)), os.path.normcase(os.path.abspath(jobs_dir))
        if _strictly_under(child, parent):
            return Path(os.path.relpath(child, parent)).parts[0]
    return None


def _owner_key(name: str, keys: Iterable[str]) -> str | None:
    """The longest job key whose report folder ``name`` is (``<key>`` or ``<key>-run-*``)."""
    folded = name.casefold()
    best: str | None = None
    for key in keys:
        candidate = key.casefold()
        if (folded == candidate or folded.startswith(candidate + "-run-")) and (
            best is None or len(key) > len(best)
        ):
            best = key
    return best


def owned_report_dirs(root: Any, store: Any, job: dict[str, Any]) -> list[Path]:
    """The job's own folders directly in reports/jobs, by name; never one another job names,
    a benchmark run (``.biliflow-benchmark``) or a link, and none at all when reports/ or
    reports/jobs is a link."""
    jobs_dir = Path(root) / "reports" / "jobs"
    if not _real_folder(root, "reports/jobs"):
        return []
    job_id, key = int(job["id"]), str(job["job_key"])
    keys = {str(other["job_key"]) for other in store.list_jobs()} | {key}
    named_by_others = {
        folder.casefold()
        for other_id, value in store.recorded_paths()
        if other_id != job_id and (folder := _report_folder(jobs_dir, value)) is not None
    }
    owned = []
    with os.scandir(jobs_dir) as entries:
        for entry in entries:
            path = Path(entry.path)
            if (
                _owner_key(entry.name, keys) != key
                or _is_link(path)
                or not entry.is_dir(follow_symlinks=False)
                or entry.name.casefold() in named_by_others
                or os.path.lexists(path / BENCHMARK_MARKER)
            ):
                continue
            owned.append(path)
    return sorted(owned, key=lambda path: path.name)


def job_log_files(root: Any, job_id: int) -> list[Path]:
    """The job's Control Center logs: logs/control-center/job-<id>-*.log."""
    folder = Path(root) / "logs" / "control-center"
    prefix = f"job-{int(job_id)}-"
    if not _real_folder(root, "logs/control-center"):
        return []  # never a log reached through a link (it lies outside the install)
    try:
        with os.scandir(folder) as entries:
            found = [
                Path(entry.path) for entry in entries
                if entry.name.casefold().startswith(prefix) and _JOB_LOG.fullmatch(entry.name)
                and entry.is_file(follow_symlinks=False) and not _is_link(entry.path)
            ]
    except OSError:
        return []
    return sorted(found, key=lambda path: path.name)


def job_files(root: Any, store: Any, job: dict[str, Any]) -> list[Path]:
    """What removing ``job`` deletes on disk: its report folders, then its logs."""
    return [*owned_report_dirs(root, store, job), *job_log_files(root, int(job["id"]))]


def _tree_bytes(path: Path) -> int:
    try:
        info = os.lstat(path)
    except OSError:
        return 0
    if _is_link_info(info):
        return 0
    if stat.S_ISREG(info.st_mode):
        return int(info.st_size)
    if not stat.S_ISDIR(info.st_mode):
        return 0
    total = 0
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                total += _tree_bytes(Path(entry.path))
    except OSError:
        pass
    return total


def files_bytes(paths: Iterable[Any]) -> int:
    """Bytes of the files and folders listed (links are not followed; a missing path is 0)."""
    return sum(_tree_bytes(Path(path)) for path in paths)


def _removable(root: Path, path: Path) -> bool:
    """A folder directly in reports/jobs (never a benchmark run), or a job log directly in
    logs/control-center; neither folder may be reached through a link."""
    absolute = os.path.normcase(os.path.abspath(path))
    parent, name = os.path.dirname(absolute), os.path.basename(absolute)
    if name in ("", ".", ".."):
        return False
    if parent == os.path.normcase(str(root / "reports" / "jobs")):
        return _real_folder(root, "reports/jobs") and not os.path.lexists(Path(path) / BENCHMARK_MARKER)
    if parent == os.path.normcase(str(root / "logs" / "control-center")):
        return _JOB_LOG.fullmatch(name) is not None and _real_folder(root, "logs/control-center")
    return False


def _remove_one(path: Path) -> str | None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as error:
        return str(error)
    if _is_link_info(info):
        return LINK_MESSAGE
    if stat.S_ISDIR(info.st_mode):
        failures: list[str] = []

        def on_error(_function: Any, failed: str, exc_info: Any) -> None:
            failures.append(f"{os.path.basename(failed)}: {exc_info[1]}")

        shutil.rmtree(path, onerror=on_error)  # never follows a link or junction inside
        return failures[0] if failures else None
    try:
        os.remove(path)
    except FileNotFoundError:
        return None
    except OSError as error:
        return str(error)
    return None


def remove_job_files(root: Any, paths: Iterable[Any]) -> list[str]:
    """Delete the listed report folders and logs; one message per path that stayed.

    Raises DeleteRefused, deleting nothing, for a root outside the install
    (and its temp/) or a path that is not a folder directly in reports/jobs
    or a job log directly in logs/control-center. A path already gone is fine.
    """
    root = _require_root(root)
    targets = [Path(path) for path in paths]
    for path in targets:
        if not _removable(root, path):
            raise DeleteRefused(PATH_MESSAGE.format(path=_display(root, path)))
    errors = []
    for path in targets:
        error = _remove_one(path)
        if error:
            errors.append(FILE_FAILED_MESSAGE.format(path=_display(root, path), error=error))
    return errors


def remove_job(root: Any, store: Any, job: dict[str, Any]) -> list[str]:
    """Delete the job's files, then (only when every one went) all its rows. Returns what stayed.

    The caller holds REVIEW_QUEUE_IO and job_action_lock and has already
    handled the source video. Run again after a failure, it finishes.
    Raises DeleteRefused, deleting nothing, while an archive row
    (PENDING/ARCHIVED/RESTORING) or a legacy cleanup row (PENDING) locks the
    job: those rows are never "repaired" by a removal.
    """
    job_id = int(job["id"])
    archive = store.latest_source_archive(job_id)
    cleanup = store.latest_source_cleanup(job_id)
    if (archive and archive.get("state") in SOURCE_ARCHIVED_STATES) or (
        cleanup and cleanup.get("state") == "PENDING"
    ):
        raise DeleteRefused(LOCKED_MESSAGE)
    errors = remove_job_files(root, job_files(root, store, job))
    if errors:
        return errors
    store.purge_job(int(job["id"]))
    return []


# --------------------------------------------------------------------------
# The two files outside reports a removal may delete.


def delete_input_file(path: Any, *, allowed_root: Any, expected_size: int) -> None:
    """Delete one source video in input/ for good (no Recycle Bin).

    ``allowed_root`` must be the input folder of an allowed project root.
    Raises DeleteRefused when the path is not a plain file in it or no longer
    has ``expected_size`` (nothing deleted), DeleteFailed when Windows keeps it.
    """
    allowed = Path(allowed_root)
    if allowed.name.casefold() != "input" or not allowed_project_root(allowed.parent):
        raise DeleteRefused(ROOT_MESSAGE)
    if not _real_folder(allowed.parent, allowed.name):
        raise DeleteRefused(FOLDER_LINK_MESSAGE.format(name=allowed.name))
    absolute = os.path.abspath(str(path))
    refusal = recycle_bin.path_refusal(absolute, allowed_root=allowed, wording=DELETE_WORDING)
    if refusal:
        raise DeleteRefused(refusal)
    try:
        info = os.lstat(absolute)
    except OSError as error:
        raise DeleteRefused(DELETE_WORDING.size) from error
    if isinstance(expected_size, bool) or int(info.st_size) != int(expected_size):
        raise DeleteRefused(DELETE_WORDING.size)
    try:
        os.remove(absolute)
    except FileNotFoundError as error:
        raise DeleteRefused(DELETE_WORDING.size) from error
    except PermissionError as error:
        if getattr(error, "winerror", None) == _WINERROR_SHARING:
            raise DeleteFailed(IN_USE_MESSAGE) from error
        raise DeleteFailed(DENIED_MESSAGE) from error
    except OSError as error:
        raise DeleteFailed(FAILED_MESSAGE.format(error=error)) from error


def delete_export_manifest(root: Any, output_path: Any) -> str | None:
    """Delete the manifest beside the export ``output_path`` (relative to the root); the .mp4 stays.

    Only ``…-reviewed.mp4.manifest.json`` directly in output/. Returns why it
    stayed, or None (deleted, or already gone).
    """
    root = _require_root(root)
    value = str(output_path or "")
    if not value:
        return MANIFEST_NAME_MESSAGE
    manifest = Path(os.path.abspath(str(root / value) + ".manifest.json"))
    if (
        not manifest.name.casefold().endswith(EXPORT_MANIFEST_SUFFIX)
        or os.path.normcase(str(manifest.parent)) != os.path.normcase(str(root / "output"))
    ):
        return MANIFEST_NAME_MESSAGE
    if not _real_folder(root, "output"):
        return FOLDER_LINK_MESSAGE.format(name="output")
    if not os.path.lexists(manifest):
        return None
    refusal = recycle_bin.path_refusal(str(manifest), allowed_root=root / "output", wording=MANIFEST_WORDING)
    if refusal:
        return refusal
    try:
        os.remove(manifest)
    except FileNotFoundError:
        return None
    except OSError as error:
        return MANIFEST_FAILED_MESSAGE.format(error=error)
    return None
