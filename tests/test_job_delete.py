"""job_delete ("Xóa video", "Dọn video mất gốc"): cancelled and lost jobs, the preview and the delete.

Every root is a temporary folder under ``<install>/temp`` with a real JobStore
and JobScheduler (never the project's state); the deleting functions refuse
any other root. The deleter records each call, then deletes the temporary file
with ``job_purge.delete_input_file``. Rows of the old Recycle Bin cleanup are
written directly, with fake ``$I``/``$R`` files in a folder under the temp
root, and the Recycle Bin is only "read" through a fake finder. The module
setup replaces ``recycle_bin._shell_delete`` with a function that fails the
test, so nothing here can reach the real Recycle Bin.
"""

import hashlib
import inspect
import json
import os
import subprocess
import sys
import threading
import time
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow import job_delete, job_purge, recycle_bin, source_cleanup
from biliflow.export_guards import REVIEW_QUEUE_IO
from biliflow.export_identity import aware_datetime
from biliflow.job_delete import delete_hint, execute_delete, preview_delete
from biliflow.job_store import JOB_ROW_TABLES, JobStore, sha256_file
from biliflow.scheduler import JobScheduler
from biliflow.source_cleanup import SOURCE_FILE_LOCK, CleanupConflict


TEMP_PARENT = recycle_bin.INSTALL_ROOT / "temp"
NOW = datetime.now(timezone.utc)
FRESH_PREVIEW = object()  # execute() helper: compute the preview id first

# Verbatim texts of the contract (docs/DELETE_FLOW_PLAN.md).
DELETED_CANCELLED = "Đã xóa vĩnh viễn video gốc và xóa video khỏi BiliFlow"
DELETED_LOST = "Đã xóa video khỏi BiliFlow"
NOTHING = "Không có video nào xóa được trong danh sách đã chọn."
BUSY = "Đang xóa, lưu trữ hoặc khôi phục video; chờ lượt trước xong rồi thử lại."
PREVIEW_ID = "Thiếu mã xem trước; hãy mở lại hộp thoại xóa video."
JOB_IDS = "Chọn từ 1 đến 50 video mỗi lần dọn."
CHANGED = "Danh sách đã thay đổi, hãy xem lại."
STOPPING = "BiliFlow đang tắt; video này chưa được xóa."
NOT_DELETABLE = "Chỉ xóa được video đã hủy hoặc video không còn video gốc"
IN_BIN = (
    "Video gốc đã dọn vào Thùng rác trước đây vẫn còn trong Thùng rác; khôi phục nó về input "
    "hoặc xóa nó khỏi Thùng rác trước"
)
BIN_UNREADABLE = "Không đọc được Thùng rác để biết video gốc đã dọn trước đây còn ở đó không; thử lại sau"
CANCELLED_PARTIAL = (
    "Đã xóa video gốc nhưng còn dữ liệu chưa xóa được (",
    "). Video vẫn có trong danh sách và không còn video gốc; bấm “Dọn video mất gốc” để xóa nốt.",
)
LOST_PARTIAL = (
    "Còn dữ liệu của video chưa xóa được (",
    "). Video vẫn có trong danh sách; đóng file đang mở rồi bấm “Xóa video” lại.",
)
REASON_BUSY = "Video đang chạy hoặc đang xếp hàng"
REASON_RENDER = "Còn lệnh xuất video chưa xong"
REASON_AUDIT = "Đang chạy AI Audit cho video này; chờ xong rồi xóa"
REASON_PENDING = "Đang chuyển video gốc này vào Thùng rác"
REASON_ARCHIVED = "Video gốc đang ở kho lưu trữ"
REASON_OUTSIDE = "Video gốc không nằm trong thư mục input"
REASON_CHANGED = "Video gốc đã thay đổi so với lúc quét"
CHANGED_DURING_HASH = "Video gốc đã thay đổi trong lúc kiểm tra SHA-256"

_SHELL_PATCH = None


def _refuse_real_shell(*args, **kwargs):
    raise AssertionError("real Recycle Bin call in a test")


def setUpModule():
    global _SHELL_PATCH
    _SHELL_PATCH = patch.object(recycle_bin, "_shell_delete", new=_refuse_real_shell)
    _SHELL_PATCH.start()


def tearDownModule():
    global _SHELL_PATCH
    if _SHELL_PATCH is not None:
        _SHELL_PATCH.stop()
        _SHELL_PATCH = None


def iso(moment):
    return moment.astimezone().isoformat()


def tree_digest(path):
    digest = hashlib.sha256()
    for file in sorted(p for p in Path(path).rglob("*") if p.is_file()):
        digest.update(file.relative_to(path).as_posix().encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class RecordingDeleter:
    """Records every call, then deletes the temporary file with job_purge.delete_input_file.

    ``modes`` by file name: "unexpected" raises before deleting,
    "unexpected-after-delete" raises after it, "refused" raises
    DeleteRefused before deleting, and a threading.Event blocks until it is set.
    """

    def __init__(self):
        self.calls = []
        self.modes = {}

    def __call__(self, path, *, allowed_root, expected_size):
        self.calls.append({"path": path, "allowed_root": allowed_root, "expected_size": expected_size})
        mode = self.modes.get(Path(path).name)
        if isinstance(mode, threading.Event):
            mode.wait(10)
            mode = None
        if mode == "unexpected":
            raise RuntimeError("boom")
        if mode == "refused":
            raise job_purge.DeleteRefused("Không xóa: lý do của bộ xóa")
        job_purge.delete_input_file(path, allowed_root=allowed_root, expected_size=expected_size)
        if mode == "unexpected-after-delete":
            raise RuntimeError("boom after the delete")


class FakeFinder:
    """recycle_bin.find_recycle_record stand-in: records each call, returns ``result`` or raises it."""

    def __init__(self):
        self.result = None
        self.calls = []

    def __call__(self, volume_root, original, size, *, since, until=None):
        self.calls.append({"volume_root": volume_root, "original": original, "size": size, "since": since})
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class DeleteFixture(unittest.TestCase):
    """A temp root under <install>/temp with a real JobStore and JobScheduler (never the project's state)."""

    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        temp = TemporaryDirectory(dir=TEMP_PARENT)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        for name in ("input", "reports/jobs", "output", "work", "state", "logs/control-center"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        bin_temp = TemporaryDirectory(dir=TEMP_PARENT)
        self.addCleanup(bin_temp.cleanup)
        self.bin_dir = Path(bin_temp.name).resolve()
        self.store = JobStore(self.root / "state" / "control-center.sqlite3")
        self.addCleanup(self.store.close)
        self.scheduler = JobScheduler(self.root, self.store)
        # No scan stages: rerun queues a job without running anything.
        patcher = patch("biliflow.scheduler.pipeline_stages", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)
        source_cleanup._CACHE.clear()
        self.addCleanup(source_cleanup._CACHE.clear)
        job_purge.clear_caches()
        self.addCleanup(job_purge.clear_caches)
        self.deleter = RecordingDeleter()
        self.finder = FakeFinder()
        self.hashed = []

    def hasher(self, path):
        self.hashed.append(Path(path))
        return sha256_file(Path(path))

    # ----------------------------------------------------------- jobs
    def make_job(self, name, *, state, source=None):
        """A job with its report folder, its source and the watcher row of that file."""
        source = source or self.root / "input" / f"{name}.mp4"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(name.encode() * 512)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        folder = self.root / "reports" / "jobs" / name
        folder.mkdir(parents=True)
        queue = {
            "status": "READY_FOR_EDIT_PLAN", "updated_at": iso(NOW),
            "source": {"path": str(source), "sha256": digest, "duration_seconds": 60.0},
            "reports": [], "items": [], "advisory_items": [],
        }
        (folder / "review-queue.json").write_text(json.dumps(queue), encoding="utf-8")
        info = source.stat()
        job = self.store.upsert_job(
            job_key=name, source_path=source, source_sha256=digest,
            source_size_bytes=info.st_size, source_mtime_ns=info.st_mtime_ns,
            content_style="animation", state=state,
        )
        job_id = int(job["id"])
        self.store.update_job(
            job_id, active_queue_path=f"reports/jobs/{name}/review-queue.json", active_revision=1, progress=1.0,
        )
        self.store.observe_file(source, info.st_size, info.st_mtime_ns)
        self.store.mark_file_imported(source, job_id)
        return job_id

    def make_lost(self, name, *, state="COMPLETED"):
        """A job whose source is no longer in input/ (moved or deleted by hand)."""
        job_id = self.make_job(name, state=state)
        self.source_of(job_id).unlink()
        return job_id

    def write_export(self, name):
        """An export and its manifest in output/: "Xóa video" never touches either."""
        video = self.root / "output" / f"{name}-reviewed.mp4"
        video.write_bytes(b"OUTPUT" + name.encode() * 100)
        manifest = video.with_suffix(".mp4.manifest.json")
        manifest.write_text(json.dumps({"status": "COMPLETED"}), encoding="utf-8")
        return video, manifest

    def write_log(self, job_id, stage="scan_safety"):
        log = self.root / "logs" / "control-center" / f"job-{job_id}-{stage}-attempt-1.log"
        log.write_bytes(b"log" * 5)
        return log

    def legacy_row(self, job_id, *, state="RECYCLED", recorded=True, emptied=False):
        """A row of the old Recycle Bin cleanup (jobs 40-60), its source moved to a fake bin folder.

        ``recorded``: the row names the bin record ($I) it found; ``emptied``:
        the user has since emptied the bin (neither $I nor $R is left). A
        PENDING or FAILED row leaves the source where it is.
        """
        job = self.store.get_job(job_id)
        source = Path(job["source_path"])
        row_id = self.store.add_source_cleanup(
            job_id=job_id, kind="EXPORTED", source_path=str(source.resolve()),
            source_sha256=job["source_sha256"], size_bytes=job["source_size_bytes"],
            mtime_ns=job["source_mtime_ns"],
        )
        if state == "PENDING":
            return row_id, None
        if state == "FAILED":
            self.store.finish_source_cleanup(row_id, state="FAILED", error="old failure")
            return row_id, None
        token = uuid.uuid4().hex[:6].upper()
        record = self.bin_dir / f"$I{token}{source.suffix}"
        if emptied:
            source.unlink()
        else:
            os.replace(source, self.bin_dir / f"$R{token}{source.suffix}")
            record.write_bytes(b"record")
        self.store.finish_source_cleanup(
            row_id, state="RECYCLED", verified=recorded, recycle_record=str(record) if recorded else None,
        )
        return row_id, record

    def add_archive(self, job_id, *, state):
        """A "Lưu trữ" row: PENDING, ARCHIVED and RESTORING keep the source in archive/."""
        job = self.store.get_job(job_id)
        source = Path(job["source_path"])
        folder = self.root / "archive" / "sources" / str(job["job_key"])
        row_id = self.store.add_source_archive(
            job_id=job_id, kind="SKIPPED", source_path=str(source.resolve()),
            archive_path=str(folder / source.name), manifest_path=str(folder / "archive-manifest.json"),
            source_sha256=job["source_sha256"], size_bytes=job["source_size_bytes"],
            mtime_ns=job["source_mtime_ns"], queue_path=job["active_queue_path"],
        )
        if state in ("ARCHIVED", "RESTORING"):
            self.store.finish_source_archive(row_id, state="ARCHIVED")
        if state == "RESTORING":
            self.store.begin_archive_restore(row_id)
        if state == "FAILED":
            self.store.finish_source_archive(row_id, state="FAILED", error="old failure")
        return row_id

    def write_golden(self, value, version="v1"):
        path = self.root / "annotations" / "golden" / version / "segments.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
        return path

    def source_of(self, job_id):
        return Path(self.store.get_job(job_id)["source_path"])

    # ----------------------------------------------------------- calls
    def preview(self, ids, **kwargs):
        kwargs.setdefault("finder", self.finder)
        return preview_delete(self.root, self.store, self.scheduler, ids, **kwargs)

    def execute(self, ids, preview_id=FRESH_PREVIEW, **kwargs):
        if preview_id is FRESH_PREVIEW:
            preview_id = self.preview(ids, audit_running=kwargs.get("audit_running"))["preview_id"]
        kwargs.setdefault("deleter", self.deleter)
        kwargs.setdefault("finder", self.finder)
        kwargs.setdefault("hasher", self.hasher)
        return execute_delete(self.root, self.store, self.scheduler, ids, preview_id, **kwargs)

    def reason(self, job_id, **kwargs):
        preview = self.preview([job_id], **kwargs)
        self.assertEqual(preview["eligible"], [], preview)
        return preview["ineligible"][0]["reason"]

    def hint(self, job_id, audit_running=None):
        """The status() card hint, built from the same rows status() reads."""
        row = self.store.latest_source_cleanup(job_id)
        checks = self.store.recycle_check_summary("SOURCE_CLEANUP")
        return delete_hint(
            self.root, self.store, self.scheduler, self.store.get_job(job_id), latest_row=row,
            archive_row=self.store.latest_source_archive(job_id),
            check=checks.get(int(row["id"])) if row else None,
            audit_running=audit_running, index=job_purge.golden_index(self.root),
        )

    def events(self, job_id, kind):
        return [event for event in self.store.events(job_id) if event["event_type"] == kind]

    def leftover_rows(self, job_id):
        """What still names ``job_id`` in the database ({} once the job is removed)."""
        found = {}
        with self.store._lock:
            connection = self.store._connection
            for table in ("jobs", *JOB_ROW_TABLES):
                column = "id" if table == "jobs" else "job_id"
                count = connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (job_id,)  # noqa: S608 - fixed names
                ).fetchone()[0]
                if count:
                    found[table] = count
            keys = [row[0] for row in connection.execute("SELECT key FROM settings").fetchall()
                    if str(row[0]).endswith(f":{job_id}")]
        if keys:
            found["settings"] = keys
        return found

    def watcher_row(self, path):
        with self.store._lock:
            row = self.store._connection.execute(
                "SELECT * FROM watcher_files WHERE path=?", (str(Path(path).resolve()),)
            ).fetchone()
        return None if row is None else dict(row)


class ModuleContractTests(unittest.TestCase):
    def test_import_never_loads_the_control_center_or_the_scheduler(self):
        code = (
            "import sys\n"
            "import biliflow.job_delete\n"
            "print('biliflow.control_center' in sys.modules, 'biliflow.scheduler' in sys.modules)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=True,
        )
        self.assertEqual(result.stdout.split(), ["False", "False"])

    def test_the_deleter_and_the_finder_are_always_given(self):
        parameters = inspect.signature(execute_delete).parameters
        for name in ("deleter", "finder"):
            self.assertEqual(parameters[name].kind, inspect.Parameter.KEYWORD_ONLY)
            self.assertIs(parameters[name].default, inspect.Parameter.empty)
        preview = inspect.signature(preview_delete).parameters
        self.assertIs(preview["finder"].default, inspect.Parameter.empty)
        self.assertNotIn("deleter", preview)
        for name in ("recycler", "bin_info"):
            self.assertNotIn(name, parameters)
            self.assertNotIn(name, preview)

    def test_texts(self):
        self.assertEqual(
            (job_delete.DELETED_LOST_MESSAGE, job_delete.NOTHING_ELIGIBLE_MESSAGE, job_delete.BUSY_MESSAGE,
             job_delete.PREVIEW_ID_MESSAGE, job_delete.REASON_NOT_DELETABLE, job_delete.REASON_IN_BIN,
             job_delete.REASON_BIN_UNREADABLE, source_cleanup.DELETED_MESSAGE),
            (DELETED_LOST, NOTHING, BUSY, PREVIEW_ID, NOT_DELETABLE, IN_BIN, BIN_UNREADABLE, DELETED_CANCELLED),
        )
        self.assertEqual(job_delete.LOST_PARTIAL_MESSAGE.format(errors="x"), "x".join(LOST_PARTIAL))
        self.assertEqual(source_cleanup.PARTIAL_MESSAGE.format(errors="x"), "x".join(CANCELLED_PARTIAL))


class CancelledDeleteTests(DeleteFixture):
    def test_a_cancelled_video_loses_its_source_and_its_job_but_never_an_export(self):
        job_id = self.make_job("tap5", state="CANCELLED")
        kept = self.make_job("tap6", state="CANCELLED")
        source = self.source_of(job_id).resolve()
        size = source.stat().st_size
        run = self.root / "reports" / "jobs" / "tap5-run-20260101-010101"
        run.mkdir()
        (run / "scan.json").write_text("{}", encoding="utf-8")
        bench = self.root / "reports" / "jobs" / "tap5-run-bench"
        bench.mkdir()
        (bench / job_purge.BENCHMARK_MARKER).write_text("", encoding="utf-8")
        log, kept_log = self.write_log(job_id), self.write_log(kept)
        _, manifest = self.write_export("tap5")
        self.store.set_setting(f"render:{job_id}", {"size_mode": "default"})
        self.store.add_event(job_id, "NOTE", "an event", level="INFO", payload={})
        outputs = tree_digest(self.root / "output")
        kept_folder = tree_digest(self.root / "reports" / "jobs" / "tap6")

        preview = self.preview([job_id])
        reports = job_purge.files_bytes([self.root / "reports" / "jobs" / "tap5", run, log])
        self.assertEqual(preview["eligible"], [{
            "job_id": job_id, "name": "tap5.mp4", "file_name": "tap5.mp4", "source_path": str(source),
            "kind": "CANCELLED", "state": "CANCELLED", "size_bytes": size, "reports_bytes": reports,
        }])
        self.assertEqual((preview["ineligible"], preview["count"], preview["total_bytes"], preview["reports_bytes"]),
                         ([], 1, size, reports))
        self.assertEqual((self.deleter.calls, self.hashed), ([], []))

        result = self.execute([job_id], preview["preview_id"])
        self.assertEqual(result, {
            "results": [{"job_id": job_id, "name": "tap5.mp4", "status": "DELETED", "message": DELETED_CANCELLED,
                         "size_bytes": size}],
            "deleted_count": 1, "deleted_bytes": size, "failed_count": 0, "partial_count": 0,
        })
        self.assertEqual(self.deleter.calls,
                         [{"path": source, "allowed_root": self.root / "input", "expected_size": size}])
        self.assertEqual(self.hashed, [source])
        self.assertFalse(os.path.lexists(source))
        self.assertEqual(self.leftover_rows(job_id), {})
        for path in (self.root / "reports" / "jobs" / "tap5", run, log):
            self.assertFalse(os.path.lexists(path), path)
        watcher = self.watcher_row(source)
        self.assertEqual((watcher["size_bytes"], watcher["mtime_ns"], watcher["imported_job_id"]), (-1, -1, None))
        # Kept: the benchmark run, every file in output/ and everything of the other job.
        self.assertTrue((bench / job_purge.BENCHMARK_MARKER).is_file())
        self.assertEqual(tree_digest(self.root / "output"), outputs)
        self.assertTrue(manifest.is_file())
        self.assertEqual(tree_digest(self.root / "reports" / "jobs" / "tap6"), kept_folder)
        self.assertTrue(kept_log.is_file())
        self.assertTrue(self.source_of(kept).is_file())
        self.assertIn("jobs", self.leftover_rows(kept))

    def test_a_cancelled_source_must_still_be_the_scanned_file_in_input(self):
        outside = self.make_job("outside", state="CANCELLED", source=self.root / "work" / "outside.mp4")
        self.assertEqual(self.reason(outside), REASON_OUTSIDE)
        grown = self.make_job("grown", state="CANCELLED")
        with self.source_of(grown).open("ab") as handle:
            handle.write(b"more")
        self.assertEqual(self.reason(grown), REASON_CHANGED)
        linked = self.make_job("linked", state="CANCELLED")
        with patch.object(recycle_bin, "path_refusal", return_value=job_purge.DELETE_WORDING.link):
            self.assertEqual(self.reason(linked), "Video gốc là liên kết (symlink/junction); không xóa.")
        folder = self.make_job("folder", state="CANCELLED")
        path = self.source_of(folder)
        path.unlink()
        path.mkdir()
        self.assertEqual(self.reason(folder), job_purge.DELETE_WORDING.size)
        self.assertEqual(self.hint(folder)["reason"], job_purge.DELETE_WORDING.size)
        self.assertEqual(self.deleter.calls, [])
        self.assertTrue(path.is_dir())


class LostDeleteTests(DeleteFixture):
    def test_lost_videos_go_in_any_settled_state_without_hashing_or_deleting_a_file(self):
        states = ("COMPLETED", "SKIPPED", "CANCELLED", "FAILED", "WAITING_REVIEW", "READY_TO_EXPORT", "PAUSED")
        ids = {state: self.make_lost(f"lost-{state.lower()}", state=state) for state in states}
        _, manifest = self.write_export("lost-completed")
        outputs = tree_digest(self.root / "output")
        preview = self.preview(list(ids.values()))
        self.assertEqual(
            [(entry["job_id"], entry["kind"], entry["state"], entry["size_bytes"]) for entry in preview["eligible"]],
            [(ids[state], "LOST", state, 0) for state in states],
        )
        self.assertEqual((preview["count"], preview["total_bytes"]), (len(states), 0))
        result = self.execute(list(ids.values()), preview["preview_id"])
        self.assertEqual({(entry["status"], entry["message"], entry["size_bytes"]) for entry in result["results"]},
                         {("DELETED", DELETED_LOST, 0)})
        self.assertEqual((result["deleted_count"], result["deleted_bytes"]), (len(states), 0))
        self.assertEqual((self.deleter.calls, self.hashed), ([], []))
        for job_id in ids.values():
            self.assertEqual(self.leftover_rows(job_id), {})
        self.assertEqual(tree_digest(self.root / "output"), outputs)  # the manifest too
        self.assertTrue(manifest.is_file())

    def test_a_failed_legacy_cleanup_or_archive_goes_with_its_lost_job(self):
        cleaned = self.make_job("tap1", state="COMPLETED")
        self.legacy_row(cleaned, state="FAILED")
        self.source_of(cleaned).unlink()
        archived = self.make_job("tap2", state="SKIPPED")
        self.add_archive(archived, state="FAILED")
        self.source_of(archived).unlink()
        result = self.execute([cleaned, archived])
        self.assertEqual([entry["status"] for entry in result["results"]], ["DELETED", "DELETED"])
        for job_id in (cleaned, archived):
            self.assertEqual(self.leftover_rows(job_id), {})

    def test_the_preview_lists_both_kinds_and_every_refusal_in_id_order(self):
        lost = self.make_lost("tap1")
        cancelled = self.make_job("tap2", state="CANCELLED")
        done = self.make_job("tap3", state="COMPLETED")
        size = self.source_of(cancelled).stat().st_size
        preview = self.preview([done, 999, cancelled, lost, lost])
        self.assertEqual([(entry["job_id"], entry["kind"]) for entry in preview["eligible"]],
                         [(lost, "LOST"), (cancelled, "CANCELLED")])
        self.assertEqual(preview["ineligible"], [
            {"job_id": done, "name": "tap3.mp4", "reason": NOT_DELETABLE},
            {"job_id": 999, "name": "", "reason": "Không tìm thấy video #999"},
        ])
        self.assertEqual((preview["count"], preview["total_bytes"]), (2, size))
        for bad in ([], "abc", [0], [True], list(range(1, 52))):
            with self.subTest(bad=bad), self.assertRaises(ValueError) as caught:
                self.preview(bad)
            self.assertEqual(str(caught.exception), JOB_IDS)


class RefusalTests(DeleteFixture):
    def test_a_job_that_still_has_its_source_is_offered_only_once_cancelled(self):
        for state in ("COMPLETED", "SKIPPED", "WAITING_REVIEW", "READY_TO_EXPORT", "FAILED", "PAUSED"):
            with self.subTest(state=state):
                job_id = self.make_job(f"state-{state.lower()}", state=state)
                self.assertEqual(self.reason(job_id), NOT_DELETABLE)
                self.assertIsNone(self.hint(job_id))
        cancelled = self.make_job("cancelled", state="CANCELLED")
        size = self.source_of(cancelled).stat().st_size
        self.assertEqual(self.hint(cancelled),
                         {"eligible": True, "kind": "CANCELLED", "reason": None, "size_bytes": size})

    def test_an_archived_source_is_not_lost(self):
        for state in ("PENDING", "ARCHIVED", "RESTORING"):
            with self.subTest(state=state):
                job_id = self.make_job(f"archived-{state.lower()}", state="SKIPPED")
                self.add_archive(job_id, state=state)
                self.source_of(job_id).unlink()  # it lies in archive/ now
                self.assertEqual(self.reason(job_id), REASON_ARCHIVED)
                self.assertIsNone(self.hint(job_id))

    def test_running_queued_exporting_and_audited_jobs_wait(self):
        busy = self.make_lost("busy")
        with patch.object(self.scheduler, "is_busy", side_effect=lambda job_id: job_id == busy):
            self.assertEqual(self.reason(busy), REASON_BUSY)
            self.assertEqual(self.hint(busy)["reason"], REASON_BUSY)
        for state in ("QUEUED", "RENDERING", "SCANNING_SAFETY", "VERIFYING"):
            with self.subTest(state=state):
                self.assertEqual(self.reason(self.make_lost(f"s-{state.lower()}", state=state)), REASON_BUSY)
        render = self.make_lost("render")
        self.store.ensure_stage(render, "render")
        self.store.update_stage(render, "render", state="PENDING")
        self.assertEqual(self.reason(render), REASON_RENDER)
        audited = self.make_lost("audited")
        self.assertEqual(self.reason(audited, audit_running=lambda value: value == audited), REASON_AUDIT)
        self.assertEqual(self.preview([audited], audit_running=lambda value: False)["count"], 1)
        cancelled = self.make_job("cancelled", state="CANCELLED")
        self.assertEqual(self.reason(cancelled, audit_running=lambda value: value == cancelled), REASON_AUDIT)
        pending = self.make_job("pending", state="COMPLETED")
        self.legacy_row(pending, state="PENDING")
        self.source_of(pending).unlink()
        self.assertEqual(self.reason(pending), REASON_PENDING)
        self.assertEqual(self.deleter.calls, [])

    def test_golden_jobs_are_never_deleted_by_id_or_by_sha(self):
        by_id = self.make_job("tap37", state="CANCELLED")
        by_sha = self.make_lost("tap38")
        free = self.make_lost("tap40")
        self.write_golden({"sources": {
            "a": {"job_id": by_id, "sha256": "f" * 64, "path": "a.mp4"},
            "b": {"job_id": 999, "sha256": self.store.get_job(by_sha)["source_sha256"].upper()},
        }})
        preview = self.preview([by_id, by_sha, free])
        self.assertEqual([entry["job_id"] for entry in preview["eligible"]], [free])
        self.assertEqual(preview["ineligible"], [
            {"job_id": by_id, "name": "tap37.mp4", "reason": job_purge.REASON_GOLDEN},
            {"job_id": by_sha, "name": "tap38.mp4", "reason": job_purge.REASON_GOLDEN},
        ])
        self.assertEqual(self.hint(by_id), {
            "eligible": False, "kind": "CANCELLED", "reason": job_purge.REASON_GOLDEN, "size_bytes": 0,
        })
        self.assertEqual(self.hint(by_sha), {
            "eligible": False, "kind": "LOST", "reason": job_purge.REASON_GOLDEN, "size_bytes": 0,
        })
        result = self.execute([by_id, by_sha, free], preview["preview_id"])
        self.assertEqual([(entry["job_id"], entry["status"]) for entry in result["results"]], [(free, "DELETED")])
        self.assertTrue(self.source_of(by_id).is_file())
        for job_id in (by_id, by_sha):
            self.assertIn("jobs", self.leftover_rows(job_id))
        self.assertEqual(self.deleter.calls, [])

    def test_an_unreadable_golden_set_blocks_every_deletion(self):
        job_id = self.make_lost("tap12")
        self.write_golden("{not json")
        reason = job_purge.REASON_GOLDEN_UNREADABLE.format(path="annotations/golden/v1/segments.json")
        self.assertEqual(self.reason(job_id), reason)
        with self.assertRaises(ValueError) as caught:
            self.execute([job_id])
        self.assertEqual(str(caught.exception), NOTHING)
        self.assertIn("jobs", self.leftover_rows(job_id))


class LegacyRecycleBinTests(DeleteFixture):
    """Jobs 40-60: the old "Dọn video gốc" moved their source to the Recycle Bin."""

    def test_a_source_still_in_the_bin_keeps_its_job_until_the_bin_lets_it_go(self):
        job_id = self.make_job("tap40", state="COMPLETED")
        _, record = self.legacy_row(job_id)
        twin = record.with_name("$R" + record.name[2:])
        self.assertEqual(self.reason(job_id), IN_BIN)
        self.assertEqual(self.hint(job_id), {"eligible": False, "kind": "LOST", "reason": IN_BIN, "size_bytes": 0})
        # Restored from the bin into input/: the job has its source again.
        os.replace(twin, self.source_of(job_id))
        self.assertEqual(self.reason(job_id), NOT_DELETABLE)
        self.assertIsNone(self.hint(job_id))
        # Deleted from the bin (an orphan $I may stay): the job can go, and nothing in the bin is touched.
        self.source_of(job_id).unlink()
        self.assertTrue(self.hint(job_id)["eligible"])
        result = self.execute([job_id])
        self.assertEqual(result["results"][0]["message"], DELETED_LOST)
        self.assertEqual(self.leftover_rows(job_id), {})
        self.assertTrue(record.is_file())
        self.assertEqual(self.finder.calls, [])  # a known record answers by itself

    def test_an_emptied_bin_lets_the_job_go(self):
        job_id = self.make_job("tap41", state="COMPLETED")
        self.legacy_row(job_id, emptied=True)
        self.assertEqual(self.execute([job_id])["deleted_count"], 1)
        self.assertEqual(self.finder.calls, [])

    def test_a_record_found_by_a_later_recheck_keeps_the_job(self):
        job_id = self.make_job("tap42", state="COMPLETED")
        size = self.store.get_job(job_id)["source_size_bytes"]
        row_id, record = self.legacy_row(job_id, recorded=False)
        self.store.add_recycle_check(
            kind="SOURCE_CLEANUP", subject_id=row_id, job_id=job_id, path=str(self.source_of(job_id)),
            size_bytes=size, found=True, recycle_record=str(record), actor="test",
        )
        self.assertEqual(self.reason(job_id), IN_BIN)
        self.assertEqual(self.hint(job_id)["reason"], IN_BIN)
        self.assertEqual(self.finder.calls, [])

    def test_an_unverified_cleanup_asks_the_bin_in_previews_only(self):
        job_id = self.make_job("tap43", state="COMPLETED")
        self.legacy_row(job_id, recorded=False)
        row = self.store.latest_source_cleanup(job_id)
        # A status hint never reads the bin.
        self.assertEqual(self.hint(job_id), {"eligible": True, "kind": "LOST", "reason": None, "size_bytes": 0})
        self.assertEqual(self.finder.calls, [])
        self.finder.result = str(self.bin_dir / "$IFOUND.mp4")
        self.assertEqual(self.reason(job_id), IN_BIN)
        self.assertEqual(self.finder.calls, [{
            "volume_root": Path(row["source_path"]).anchor, "original": row["source_path"],
            "size": row["size_bytes"], "since": aware_datetime(row["created_at"]).timestamp() - 5,
        }])
        for error in (OSError("access denied"), ValueError("half-written $I"), RuntimeError("no bin")):
            with self.subTest(error=error):
                self.finder.result = error
                self.assertEqual(self.reason(job_id), BIN_UNREADABLE)
        self.finder.result = None
        self.finder.calls.clear()
        preview = self.preview([job_id])
        self.assertEqual(preview["count"], 1)
        self.assertEqual(self.execute([job_id], preview["preview_id"])["deleted_count"], 1)
        # Once by the preview and once by the execute's own preview, never under the locks.
        self.assertEqual(len(self.finder.calls), 2)


class ExecuteTests(DeleteFixture):
    def test_a_stale_preview_id_is_a_conflict_with_the_new_preview(self):
        lost = self.make_lost("tap1")
        cancelled = self.make_job("tap2", state="CANCELLED")
        old = self.preview([lost, cancelled])
        self.source_of(cancelled).unlink()  # gone by hand after the dialog opened: now a lost job
        with self.assertRaises(CleanupConflict) as caught:
            self.execute([lost, cancelled], old["preview_id"])
        self.assertEqual((caught.exception.code, str(caught.exception)), ("preview_changed", CHANGED))
        self.assertEqual(caught.exception.preview, self.preview([lost, cancelled]))
        self.assertEqual([entry["kind"] for entry in caught.exception.preview["eligible"]], ["LOST", "LOST"])
        self.assertEqual(self.deleter.calls, [])
        for job_id in (lost, cancelled):
            self.assertIn("jobs", self.leftover_rows(job_id))

    def test_bad_requests_and_a_root_outside_the_install_change_nothing(self):
        job_id = self.make_lost("tap1")
        preview_id = self.preview([job_id])["preview_id"]
        for bad in (None, "", 42, preview_id.upper(), preview_id[:-1], preview_id + "0"):
            with self.subTest(bad=bad), self.assertRaises(ValueError) as caught:
                self.execute([job_id], bad)
            self.assertEqual(str(caught.exception), PREVIEW_ID)
        with self.assertRaises(ValueError) as caught:
            self.execute("abc", preview_id)
        self.assertEqual(str(caught.exception), JOB_IDS)
        with patch.object(job_purge, "INSTALL_ROOT", self.root / "work"), \
                self.assertRaises(ValueError) as caught:
            self.execute([job_id], preview_id)
        self.assertEqual(str(caught.exception), job_purge.ROOT_MESSAGE)
        self.assertIn("jobs", self.leftover_rows(job_id))
        with self.assertRaises(ValueError) as caught:
            self.execute([self.make_job("tap2", state="COMPLETED")])
        self.assertEqual(str(caught.exception), NOTHING)

    def test_one_lock_for_every_action_on_source_files(self):
        cancelled = self.make_job("tap1", state="CANCELLED")
        lost = self.make_lost("tap2")
        release = threading.Event()
        self.deleter.modes["tap1.mp4"] = release
        first_id = self.preview([cancelled])["preview_id"]
        lost_id = self.preview([lost])["preview_id"]
        outcome = {}
        worker = threading.Thread(target=lambda: outcome.update(result=self.execute([cancelled], first_id)))
        worker.start()
        try:
            self.assertTrue(wait_until(lambda: len(self.deleter.calls) == 1))
            self.assertTrue(source_cleanup.cleanup_running())
            self.assertFalse(source_cleanup.wait_idle(0.1))
            with self.assertRaises(CleanupConflict) as caught:
                self.execute([lost], lost_id)
            self.assertEqual((caught.exception.code, str(caught.exception), caught.exception.preview),
                             ("busy", BUSY, None))
            with self.assertRaises(CleanupConflict) as caught:
                source_cleanup.execute_cleanup(
                    self.root, self.store, self.scheduler, [lost], "0" * 64, deleter=self.deleter,
                )
            self.assertEqual((caught.exception.code, str(caught.exception)), ("busy", source_cleanup.BUSY_MESSAGE))
        finally:
            release.set()
            worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome["result"]["deleted_count"], 1)
        self.assertTrue(source_cleanup.wait_idle(1.0))
        self.assertFalse(SOURCE_FILE_LOCK.locked())
        with patch.object(recycle_bin, "operations_in_progress", return_value=frozenset({"E:\\x.mp4"})), \
                self.assertRaises(CleanupConflict) as caught:
            self.execute([lost], lost_id)
        self.assertEqual(caught.exception.code, "busy")
        self.assertEqual(self.execute([lost], lost_id)["deleted_count"], 1)

    def test_a_cancelled_source_whose_bytes_changed_is_never_deleted(self):
        job_id = self.make_job("tap1", state="CANCELLED")
        source = self.source_of(job_id)
        source.write_bytes(bytes(reversed(source.read_bytes())))  # same size, other bytes
        result = self.execute([job_id])
        self.assertEqual([(entry["status"], entry["message"]) for entry in result["results"]],
                         [("FAILED", REASON_CHANGED)])
        self.assertEqual(self.events(job_id, "JOB_DELETE_FAILED")[0]["payload"],
                         {"stage": "precheck", "reason": REASON_CHANGED})
        self.assertEqual(self.deleter.calls, [])
        self.assertTrue(source.is_file())
        self.assertIn("jobs", self.leftover_rows(job_id))

    def test_a_source_touched_between_hashing_and_the_lock_is_refused(self):
        job_id = self.make_job("tap1", state="CANCELLED")
        source = self.source_of(job_id)
        real = source_cleanup.precheck

        def precheck(*args, **kwargs):
            value = real(*args, **kwargs)
            info = source.stat()
            os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
            return value

        with patch.object(source_cleanup, "precheck", side_effect=precheck):
            result = self.execute([job_id])
        self.assertEqual([(entry["status"], entry["message"]) for entry in result["results"]],
                         [("FAILED", CHANGED_DURING_HASH)])
        self.assertEqual(self.events(job_id, "JOB_DELETE_FAILED")[0]["payload"]["stage"], "recheck")
        self.assertEqual(self.deleter.calls, [])
        self.assertTrue(source.is_file())

    def test_a_lost_source_copied_back_before_the_lock_keeps_its_job(self):
        cancelled = self.make_job("tap1", state="CANCELLED")
        lost = self.make_lost("tap2")
        copied = self.root / "input" / "tap2.mp4"

        def hasher(path):
            digest = self.hasher(path)
            copied.write_bytes(b"tap2" * 512)  # copied back while the first video is hashed
            return digest

        result = self.execute([cancelled, lost], hasher=hasher)
        self.assertEqual([(entry["job_id"], entry["status"], entry["message"]) for entry in result["results"]], [
            (cancelled, "DELETED", DELETED_CANCELLED), (lost, "FAILED", NOT_DELETABLE),
        ])
        self.assertEqual(self.events(lost, "JOB_DELETE_FAILED")[0]["payload"],
                         {"stage": "recheck", "reason": NOT_DELETABLE})
        self.assertTrue(copied.is_file())
        self.assertIn("jobs", self.leftover_rows(lost))

    def test_a_golden_set_or_an_audit_that_starts_while_hashing_is_caught_by_the_recheck(self):
        golden = self.make_job("tap37", state="CANCELLED")
        audited = self.make_lost("tap38")
        auditing = set()

        def hasher(path):
            self.write_golden({"sources": {"a": {"job_id": golden, "sha256": "f" * 64}}})
            auditing.add(audited)
            return self.hasher(path)

        result = self.execute([golden, audited], hasher=hasher, audit_running=auditing.__contains__)
        self.assertEqual([(entry["status"], entry["message"]) for entry in result["results"]], [
            ("FAILED", job_purge.REASON_GOLDEN), ("FAILED", REASON_AUDIT),
        ])
        self.assertEqual(self.deleter.calls, [])
        self.assertTrue(self.source_of(golden).is_file())
        for job_id in (golden, audited):
            self.assertEqual(self.events(job_id, "JOB_DELETE_FAILED")[0]["payload"]["stage"], "recheck")

    def test_a_shutdown_marks_the_remaining_videos_not_run(self):
        ids = [self.make_lost("tap1"), self.make_lost("tap2")]
        stop = threading.Event()
        real = job_purge.remove_job

        def remove(*args, **kwargs):
            stop.set()  # BiliFlow starts shutting down during the first removal
            return real(*args, **kwargs)

        with patch.object(job_purge, "remove_job", side_effect=remove):
            result = self.execute(ids, should_stop=stop.is_set)
        self.assertEqual([(entry["status"], entry["message"]) for entry in result["results"]], [
            ("DELETED", DELETED_LOST), ("NOT_RUN", STOPPING),
        ])
        self.assertIn("jobs", self.leftover_rows(ids[1]))

    def test_unexpected_errors_fail_or_leave_a_job_to_finish_later(self):
        before, after, hashing, refused, removing = (
            self.make_job(f"tap{index}", state="CANCELLED") for index in (1, 2, 3, 4, 5)
        )
        lost = self.make_lost("tap6")
        sizes = {job_id: self.source_of(job_id).stat().st_size for job_id in (after, removing)}
        self.deleter.modes.update({"tap1.mp4": "unexpected", "tap2.mp4": "unexpected-after-delete",
                                   "tap4.mp4": "refused"})
        hash_source = self.source_of(hashing).resolve()
        real_remove = job_purge.remove_job

        def hasher(path):
            if Path(path) == hash_source:
                raise RuntimeError("hash boom")
            return self.hasher(path)

        def remove(root, store, job):
            if int(job["id"]) in (removing, lost):
                raise RuntimeError("purge boom")
            return real_remove(root, store, job)

        with patch.object(job_purge, "remove_job", side_effect=remove):
            result = self.execute([before, after, hashing, refused, removing, lost], hasher=hasher)
        purge_error = "Lỗi không mong đợi: purge boom"
        self.assertEqual([(entry["status"], entry["message"]) for entry in result["results"]], [
            ("FAILED", "Lỗi không mong đợi: boom"),
            ("DELETED", DELETED_CANCELLED),  # the file went before the error: the removal goes on
            ("FAILED", "Lỗi không mong đợi: hash boom"),
            ("FAILED", "Không xóa: lý do của bộ xóa"),
            ("PARTIAL", purge_error.join(CANCELLED_PARTIAL)),
            ("PARTIAL", purge_error.join(LOST_PARTIAL)),
        ])
        self.assertEqual(
            (result["deleted_count"], result["failed_count"], result["partial_count"], result["deleted_bytes"]),
            (1, 3, 2, sizes[after] + sizes[removing]),
        )
        event = self.events(before, "JOB_DELETE_FAILED")[0]
        self.assertEqual((event["level"], event["payload"]),
                         ("ERROR", {"stage": "delete", "reason": "Lỗi không mong đợi: boom"}))
        event = self.events(hashing, "JOB_DELETE_FAILED")[0]
        self.assertEqual((event["level"], event["payload"]["stage"]), ("ERROR", "unexpected"))
        for job_id in (before, hashing, refused):
            self.assertTrue(self.source_of(job_id).is_file())
        self.assertEqual(self.leftover_rows(after), {})
        # The source is gone but the job stays, now a lost one for "Xóa video" or "Dọn video mất gốc".
        self.assertFalse(os.path.lexists(self.root / "input" / "tap5.mp4"))
        for job_id in (removing, lost):
            event = self.events(job_id, "JOB_DELETE_PARTIAL")[0]
            self.assertEqual((event["level"], event["payload"]), ("WARNING", {"errors": [purge_error]}))
            self.assertEqual(self.hint(job_id), {"eligible": True, "kind": "LOST", "reason": None, "size_bytes": 0})

    def test_no_deadlock_with_finalize_rerun_and_status_hints(self):
        deleted = self.make_job("tap1", state="CANCELLED")
        rerun_job = self.make_job("tap2", state="COMPLETED")
        hint_job = self.make_lost("tap3")
        entered, release = threading.Event(), threading.Event()

        def blocking(path, **kwargs):
            entered.set()
            release.wait(10)
            return self.deleter(path, **kwargs)

        outcome = {}
        deleting = threading.Thread(target=lambda: outcome.update(result=self.execute([deleted], deleter=blocking)))
        deleting.start()
        self.assertTrue(entered.wait(10))

        def finalize_like():
            with REVIEW_QUEUE_IO, self.scheduler.job_action_lock:
                outcome["finalize"] = True

        def rerun():
            outcome["rerun"] = self.scheduler.rerun(rerun_job)["state"]

        def hint():
            outcome["hint"] = self.hint(hint_job)

        others = [threading.Thread(target=target) for target in (finalize_like, rerun, hint)]
        for thread in others:
            thread.start()
        time.sleep(0.2)
        # The two that take the locks wait behind the locked section (lock order, not a deadlock) ...
        self.assertNotIn("finalize", outcome)
        self.assertNotIn("rerun", outcome)
        release.set()
        released = time.monotonic()
        for thread in [deleting, *others]:
            thread.join(5)
        self.assertLess(time.monotonic() - released, 5)
        self.assertFalse(any(thread.is_alive() for thread in [deleting, *others]))
        # ... and all of them finish once it is released.
        self.assertEqual(outcome["result"]["deleted_count"], 1)
        self.assertTrue(outcome["finalize"])
        self.assertEqual(outcome["rerun"], "QUEUED")
        self.assertTrue(outcome["hint"]["eligible"])


@unittest.skipUnless(sys.platform == "win32", "open files block a delete only on Windows")
class OpenFileTests(DeleteFixture):
    def test_an_open_source_is_never_deleted(self):
        job_id = self.make_job("tap1", state="CANCELLED")
        source = self.source_of(job_id)
        with source.open("rb"):
            result = self.execute([job_id])
        self.assertEqual([(entry["status"], entry["message"]) for entry in result["results"]],
                         [("FAILED", job_purge.IN_USE_MESSAGE)])
        event = self.events(job_id, "JOB_DELETE_FAILED")[0]
        self.assertEqual((event["level"], event["payload"]),
                         ("ERROR", {"stage": "delete", "reason": job_purge.IN_USE_MESSAGE}))
        self.assertTrue(source.is_file())
        self.assertTrue((self.root / "reports" / "jobs" / "tap1").is_dir())
        self.assertIn("jobs", self.leftover_rows(job_id))

    def test_a_locked_report_file_keeps_the_job_until_it_is_free(self):
        cancelled = self.make_job("tap1", state="CANCELLED")
        lost = self.make_lost("tap2")
        size = self.source_of(cancelled).stat().st_size
        locked = {}
        for job_id, name in ((cancelled, "tap1"), (lost, "tap2")):
            path = self.root / "reports" / "jobs" / name / "frames" / "a.jpg"
            path.parent.mkdir()
            path.write_bytes(b"x" * 10)
            locked[job_id] = path
        log = self.write_log(lost)
        with locked[cancelled].open("rb"), locked[lost].open("rb"):
            result = self.execute([cancelled, lost])
        first, second = result["results"]
        self.assertEqual((first["status"], second["status"]), ("PARTIAL", "PARTIAL"))
        self.assertTrue(first["message"].startswith(CANCELLED_PARTIAL[0] + "reports/jobs/tap1: a.jpg: "),
                        first["message"])
        self.assertTrue(first["message"].endswith(CANCELLED_PARTIAL[1]), first["message"])
        self.assertTrue(second["message"].startswith(LOST_PARTIAL[0] + "reports/jobs/tap2: a.jpg: "),
                        second["message"])
        self.assertTrue(second["message"].endswith(LOST_PARTIAL[1]), second["message"])
        self.assertEqual((result["deleted_count"], result["partial_count"], result["deleted_bytes"]), (0, 2, size))
        self.assertFalse(os.path.lexists(self.root / "input" / "tap1.mp4"))
        self.assertFalse(os.path.lexists(log))  # the other files went
        for job_id in (cancelled, lost):
            self.assertIn("jobs", self.leftover_rows(job_id))
            self.assertEqual(len(self.events(job_id, "JOB_DELETE_PARTIAL")), 1)
        # Once the files are free, "Xóa video" takes the rest (the cancelled one is a lost job now).
        self.assertEqual(self.hint(cancelled), {"eligible": True, "kind": "LOST", "reason": None, "size_bytes": 0})
        result = self.execute([cancelled, lost])
        self.assertEqual([(entry["status"], entry["message"]) for entry in result["results"]],
                         [("DELETED", DELETED_LOST)] * 2)
        for job_id in (cancelled, lost):
            self.assertEqual(self.leftover_rows(job_id), {})


if __name__ == "__main__":
    unittest.main()
