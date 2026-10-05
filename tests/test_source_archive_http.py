"""Lưu trữ, Khôi phục bản xuất and Ẩn / Hiện lại through the Control Center (batch 4).

The archive preview, archive, restore and bin re-check routes over real HTTP
on port 0 with their 400/403/409 answers, the status() fields, and the cancel,
hide and unhide routes of a cancelled job.

Every Control Center here is a stub built with ``ControlCenter.__new__`` on a
temporary root under ``<install>/temp`` (a real JobStore and JobScheduler,
never the project's state) with a fake export recycler that moves the file
into a folder outside the root, a fake ``bin_info`` with the real numbers of
drive E: and a fake record finder. The module setup also replaces
``recycle_bin._shell_delete`` with a function that fails the test, so nothing
here can reach the real Recycle Bin.
"""

import hashlib
import http.client
import json
import os
import threading
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from biliflow import control_center as cc
from biliflow import recycle_bin, source_cleanup
from biliflow.control_center import ControlCenter, _handler_class
from biliflow.job_store import JobStore
from biliflow.recycle_bin import BinInfo, RecycleRefused, RecycleResult
from biliflow.review_evidence import ReviewFrameCache
from biliflow.review_workflow import review_export_paths
from biliflow.scheduler import JobScheduler


GUID = "{2fd9f59c-d156-40e6-b5c9-93b787892ee9}"
MAX_BYTES = 52_157_218_816  # MaxCapacity 49741 MiB
USED_BYTES = 11_823_971_925
ITEMS = 7
NEAR_FULL = MAX_BYTES - recycle_bin.CAPACITY_MARGIN_BYTES
NOW = datetime.now(timezone.utc)
TEMP_PARENT = recycle_bin.INSTALL_ROOT / "temp"
RECORD = "E:\\$Recycle.Bin\\S-1-5-21-1000\\$IREC123.mp4"

JOB_IDS_MESSAGE = "Chọn từ 1 đến 50 video mỗi lần lưu trữ."
PREVIEW_ID_MESSAGE = "Thiếu mã xem trước; hãy mở lại hộp thoại lưu trữ."
NOTHING_ELIGIBLE_MESSAGE = "Không có video nào lưu trữ được trong danh sách đã chọn."
PREVIEW_CHANGED_MESSAGE = "Danh sách đã thay đổi, hãy xem lại."
BUSY_MESSAGE = "Đang dọn, lưu trữ hoặc khôi phục video gốc; chờ lượt trước xong rồi thử lại."
UNCONFIGURED_MESSAGE = "Chưa cấu hình Thùng rác cho Control Center này."
REASON_STATE = "Chỉ lưu trữ được video đã xuất hoặc đã bỏ qua (mục “Hoàn tất”)"
REASON_ARCHIVED = "Video gốc đã được lưu trữ"
CLEANUP_REASON_ARCHIVED = "Video gốc đang ở kho lưu trữ"
RESTORE_ID_MESSAGE = "Mã video không hợp lệ."
HOST_REFUSAL = "Địa chỉ truy cập không hợp lệ"
TOKEN_REFUSAL = "Phiên Control Center không hợp lệ"

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


def item(item_id, decision="KEEP", decided_at=None):
    return {
        "id": item_id, "category": "adult", "start_seconds": 1.0, "end_seconds": 2.0,
        "decision": decision, "decided_at": decided_at,
    }


def tree_digest(path):
    digest = hashlib.sha256()
    for file in sorted(p for p in Path(path).rglob("*") if p.is_file()):
        digest.update(file.relative_to(path).as_posix().encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


class FakeBin:
    def __init__(self, used=USED_BYTES, error=None):
        self.used, self.error = used, error
        self.calls = []

    def __call__(self, path):
        self.calls.append(Path(path))
        if self.error is not None:
            raise self.error
        return BinInfo("E:", "E:\\", GUID, MAX_BYTES, self.used, ITEMS)


class FakeRecycler:
    """Moves the file into a bin folder outside the root; ``verified=False`` finds no record."""

    def __init__(self, bin_dir):
        self.bin_dir = Path(bin_dir)
        self.calls = []
        self.verified = True

    def __call__(self, path, *, allowed_root, expected_size, timeout, on_late_result):
        self.calls.append({"path": Path(path), "allowed_root": Path(allowed_root), "expected_size": expected_size})
        token = uuid.uuid4().hex[:6].upper()
        os.replace(path, self.bin_dir / f"$R{token}{Path(path).suffix}")
        return RecycleResult(str(path), expected_size, self.verified, RECORD if self.verified else None, 0.01)


class FakeFinder:
    def __init__(self):
        self.calls = []
        self.record = None

    def __call__(self, volume_root, original, size, *, since, until=None):
        self.calls.append((volume_root, original, size))
        return self.record


class ArchiveHttpFixture(unittest.TestCase):
    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        temp = TemporaryDirectory(dir=TEMP_PARENT)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        for name in ("input", "reports/jobs", "output", "work", "state", "logs"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        bin_temp = TemporaryDirectory(dir=TEMP_PARENT)
        self.addCleanup(bin_temp.cleanup)
        self.bin_dir = Path(bin_temp.name).resolve()
        self.store = JobStore(self.root / "state" / "control-center.sqlite3")
        self.addCleanup(self.store.close)
        center = ControlCenter.__new__(ControlCenter)
        center.root = self.root
        center.host = "127.0.0.1"
        center.token = "test-token"
        center.store = self.store
        center.recovered = 0
        center._audit_lock = threading.Lock()
        center._audit_jobs = {}
        center._stopping = threading.Event()
        center.scheduler = JobScheduler(self.root, self.store)
        center.frame_cache = ReviewFrameCache(self.root, self.root / "missing-ffmpeg.exe")
        self.recycler = FakeRecycler(self.bin_dir)
        self.bin = FakeBin()
        self.finder = FakeFinder()
        center.export_recycler = self.recycler
        center.bin_info = self.bin
        center.record_finder = self.finder
        self.center = center
        cc._SUMMARY_CACHE.clear()
        source_cleanup._CACHE.clear()
        self.addCleanup(source_cleanup._CACHE.clear)
        for target, kwargs in (
            ("biliflow.scheduler.pipeline_stages", {"return_value": []}),
            ("biliflow.control_center._resources", {"return_value": {}}),
            ("biliflow.control_center.storage_status", {"return_value": SimpleNamespace(as_dict=lambda: {})}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    # ----------------------------------------------------------- jobs
    def make_job(self, name, *, state, items, status="READY_FOR_EDIT_PLAN"):
        source = self.root / "input" / f"{name}.mp4"
        source.write_bytes(name.encode() * 512)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        folder = self.root / "reports" / "jobs" / name
        folder.mkdir(parents=True)
        queue = {
            "status": status, "updated_at": iso(NOW),
            "source": {"path": str(source), "sha256": digest, "duration_seconds": 60.0},
            "reports": [], "items": items, "advisory_items": [],
        }
        (folder / "review-queue.json").write_text(json.dumps(queue), encoding="utf-8")
        info = source.stat()
        job = self.store.upsert_job(
            job_key=name, source_path=source, source_sha256=digest, source_size_bytes=info.st_size,
            source_mtime_ns=info.st_mtime_ns, content_style="animation", state=state,
        )
        self.store.update_job(job["id"], active_queue_path=f"reports/jobs/{name}/review-queue.json",
                              active_revision=1, progress=1.0)
        return int(job["id"])

    def queue(self, job_id):
        return json.loads((self.root / self.store.get_job(job_id)["active_queue_path"]).read_text(encoding="utf-8"))

    def output_of(self, job_id):
        return review_export_paths(self.root, self.queue(job_id))[1]

    def make_exported_job(self, name):
        decided = iso(NOW - timedelta(hours=1))
        job_id = self.make_job(name, state="COMPLETED", items=[item("a", decided_at=decided)])
        job = self.store.get_job(job_id)
        plan_path, output, _ = review_export_paths(self.root, self.queue(job_id))
        output.write_bytes(b"OUTPUT" + name.encode() * 300)
        output_sha = hashlib.sha256(output.read_bytes()).hexdigest()
        manifest = {
            "schema_version": 1, "status": "COMPLETED", "created_at": iso(NOW),
            "edit_plan": plan_path.relative_to(self.root).as_posix(),
            "source": {"path": job["source_path"], "sha256": job["source_sha256"], "duration_seconds": 60.0,
                       "sha256_after_render": job["source_sha256"], "modified": False},
            "output": {"path": output.relative_to(self.root).as_posix(), "bytes": output.stat().st_size,
                       "sha256": output_sha},
            "encoding": {"full_decode_validation_passed": True},
        }
        output.with_name(output.name + ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        plan_path.write_text(json.dumps({"status": "FINAL_RENDER_COMPLETED",
                                         "review_queue": job["active_queue_path"]}), encoding="utf-8")
        self.store.add_artifact(job_id, stage_name="render", kind="final_output",
                                path=output.relative_to(self.root).as_posix(), sha256=output_sha,
                                bytes_count=output.stat().st_size)
        return job_id

    def make_skipped_job(self, name):
        job_id = self.make_job(name, state="READY_TO_EXPORT", items=[item("a", decided_at=iso(NOW))])
        self.center.skip_export(job_id)
        return job_id

    def source_of(self, job_id):
        return Path(self.store.get_job(job_id)["source_path"])

    def db_dump(self):
        with self.store._lock:
            tables = [row[0] for row in self.store._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
            return {table: [tuple(row) for row in self.store._connection.execute(
                f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()] for table in tables}

    def status_jobs(self):
        return {job["id"]: job for job in self.center.status()["jobs"]}

    # ------------------------------------------------- real HTTP on port 0
    def serve(self):
        if getattr(self, "server", None) is not None:
            return
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_class(self.center))
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, *, token="test-token", host=None, body=None):
        self.serve()
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            headers = {"Host": host or f"127.0.0.1:{self.port}", "Content-Type": "application/json"}
            if token:
                headers["X-BiliFlow-Token"] = token
            payload = json.dumps(body or {}).encode() if method == "POST" else None
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read() or b"{}")
        finally:
            connection.close()

    def get(self, path, *, host=None):
        return self.request("GET", path, host=host)

    def post(self, path, *, token="test-token", host=None, body=None):
        return self.request("POST", path, token=token, host=host, body=body)

    def preview(self, ids):
        status, body = self.get(f"/api/source-archive/preview?ids={','.join(str(x) for x in ids)}")
        self.assertEqual(status, 200, body)
        return body

    def archive(self, ids, preview_id=None):
        if preview_id is None:
            preview_id = self.preview(ids)["preview_id"]
        return self.post("/api/source-archive", body={"job_ids": list(ids), "preview_id": preview_id})

    def restore(self, job_id):
        return self.post("/api/source-archive/restore", body={"job_id": job_id})


class PreviewRouteTests(ArchiveHttpFixture):
    def test_preview_read_only_and_400(self):
        exported, skipped = self.make_exported_job("tap12"), self.make_skipped_job("tap13")
        waiting = self.make_job("tap14", state="WAITING_REVIEW", items=[item("a")], status="REVIEW_REQUIRED")
        dump, files = self.db_dump(), tree_digest(self.root)
        body = self.preview([waiting, skipped, exported])
        self.assertEqual([(x["job_id"], x["kind"], x["archive_path"]) for x in body["eligible"]], [
            (exported, "EXPORTED", "archive/sources/tap12/tap12.mp4"),
            (skipped, "SKIPPED", "archive/sources/tap13/tap13.mp4"),
        ])
        self.assertEqual(body["ineligible"], [{"job_id": waiting, "name": "tap14.mp4", "reason": REASON_STATE}])
        self.assertEqual((body["count"], body["recycle_bin"]["used_bytes"], body["blocked"]), (2, USED_BYTES, None))
        self.assertEqual((self.db_dump(), tree_digest(self.root)), (dump, files))
        self.assertFalse((self.root / "archive").exists())
        self.assertEqual((self.recycler.calls, self.finder.calls), ([], []))
        self.serve()
        for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1.evil.example"):
            with self.subTest(host=host):
                status, payload = self.get(f"/api/source-archive/preview?ids={exported}", host=host)
                self.assertEqual((status, payload), (403, {"error": HOST_REFUSAL}))
        too_many = ",".join(str(value) for value in range(1, 52))
        for query in ("?ids=", "?ids=abc", "?ids=0", f"?ids={too_many}", "", "?ids=1,,2", "?other=1"):
            with self.subTest(query=query):
                status, payload = self.get(f"/api/source-archive/preview{query}")
                self.assertEqual((status, payload), (400, {"error": JOB_IDS_MESSAGE}))
        # A stub without a bin shows the preview as blocked (only an exported video needs it).
        del self.center.bin_info
        self.assertEqual(self.preview([exported])["blocked"], UNCONFIGURED_MESSAGE)
        self.assertIsNone(self.preview([skipped])["blocked"])


class ArchiveRouteTests(ArchiveHttpFixture):
    def test_post_needs_host_and_token(self):
        exported = self.make_exported_job("tap12")
        preview_id = self.preview([exported])["preview_id"]
        dump, inputs = self.db_dump(), tree_digest(self.root / "input")
        for path, body in (("/api/source-archive", {"job_ids": [exported], "preview_id": preview_id}),
                           ("/api/source-archive/restore", {"job_id": exported}),
                           ("/api/source-recycle-check", {"kind": "archive_export", "id": 1})):
            for host, token, error in (("evil.example", "test-token", HOST_REFUSAL),
                                       ("127.0.0.1.evil.example", "test-token", HOST_REFUSAL),
                                       (None, None, TOKEN_REFUSAL), (None, "wrong", TOKEN_REFUSAL)):
                with self.subTest(path=path, host=host, token=token):
                    status, payload = self.post(path, host=host, token=token, body=body)
                    self.assertEqual((status, payload), (403, {"error": error}))
        self.assertEqual((self.recycler.calls, self.finder.calls), ([], []))
        self.assertEqual((self.db_dump(), tree_digest(self.root / "input")), (dump, inputs))
        self.assertFalse((self.root / "archive").exists())
        status, result = self.archive([exported], preview_id)
        self.assertEqual((status, result["archived_count"], result["results"][0]["status"]), (200, 1, "ARCHIVED"))

    def test_archive_409_codes(self):
        exported, other = self.make_exported_job("tap12"), self.make_exported_job("tap15")
        preview_id = self.preview([exported])["preview_id"]
        for body, error in (
            ({"job_ids": str(exported), "preview_id": preview_id}, JOB_IDS_MESSAGE),
            ({"job_ids": [], "preview_id": preview_id}, JOB_IDS_MESSAGE),
            ({"job_ids": [exported]}, PREVIEW_ID_MESSAGE),
            ({"job_ids": [exported], "preview_id": "x" * 64}, PREVIEW_ID_MESSAGE),
        ):
            with self.subTest(body=body):
                status, payload = self.post("/api/source-archive", body=body)
                self.assertEqual((status, payload), (400, {"error": error}))
        source = self.source_of(exported)
        info = source.stat()
        os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))
        status, payload = self.archive([exported], preview_id)
        self.assertEqual((status, payload["code"], payload["error"]), (409, "preview_changed", PREVIEW_CHANGED_MESSAGE))
        self.assertNotEqual(payload["preview"]["preview_id"], preview_id)
        self.assertTrue(source_cleanup.SOURCE_FILE_LOCK.acquire(blocking=False))
        try:
            status, payload = self.archive([exported])
        finally:
            source_cleanup.SOURCE_FILE_LOCK.release()
        self.assertEqual((status, payload), (409, {"error": BUSY_MESSAGE, "code": "busy"}))
        self.center.bin_info = FakeBin(used=NEAR_FULL)
        status, payload = self.archive([exported])
        self.assertEqual((status, payload["code"]), (409, "bin_capacity"))
        self.assertTrue(payload["error"].startswith("Không thể lưu trữ: Thùng rác của ổ E: đang chứa "))
        self.center.bin_info = FakeBin(error=RecycleRefused("Ổ E: không phải ổ cứng cố định."))
        status, payload = self.archive([exported])
        self.assertEqual((status, payload["code"], payload["error"]),
                         (409, "bin_unavailable", "Ổ E: không phải ổ cứng cố định."))
        self.center.bin_info = self.bin
        waiting = self.make_job("tap16", state="WAITING_REVIEW", items=[item("a")])
        status, payload = self.archive([waiting])
        self.assertEqual((status, payload), (400, {"error": NOTHING_ELIGIBLE_MESSAGE}))
        self.assertEqual(self.recycler.calls, [])
        self.assertFalse((self.root / "archive").exists())
        status, result = self.archive([exported, other])
        self.assertEqual((status, [entry["status"] for entry in result["results"]]), (200, ["ARCHIVED", "ARCHIVED"]))
        names = [self.output_of(job_id).name for job_id in (exported, other)]
        self.assertEqual([call["path"].name for call in self.recycler.calls],
                         [names[0], names[0] + ".manifest.json", names[1], names[1] + ".manifest.json"])
        self.assertEqual({call["allowed_root"] for call in self.recycler.calls}, {self.root / "output"})
        status, payload = self.archive([exported])
        self.assertEqual((status, payload), (400, {"error": NOTHING_ELIGIBLE_MESSAGE}))

    def test_restore_route(self):
        exported, skipped = self.make_exported_job("tap12"), self.make_skipped_job("tap13")
        self.assertEqual(self.archive([exported, skipped])[0], 200)
        self.assertFalse(self.source_of(exported).exists())
        status, result = self.restore(exported)
        self.assertEqual((status, result["status"], result["state"]), (200, "RESTORED", "READY_TO_EXPORT"))
        self.assertTrue(self.source_of(exported).is_file())
        status, payload = self.restore(exported)
        self.assertEqual((status, payload["code"]), (409, "not_archived"))
        # A file at the input path is never overwritten.
        self.source_of(skipped).write_bytes(b"another file")
        status, payload = self.restore(skipped)
        self.assertEqual((status, payload["code"]), (409, "target_exists"))
        self.assertEqual(self.source_of(skipped).read_bytes(), b"another file")
        self.source_of(skipped).unlink()
        status, result = self.restore(skipped)
        self.assertEqual((status, result["state"]), (200, "SKIPPED"))
        for body in ({}, {"job_id": str(skipped)}, {"job_id": 0}, {"job_id": True}, {"job_id": 1.5}):
            with self.subTest(body=body):
                status, payload = self.post("/api/source-archive/restore", body=body)
                self.assertEqual((status, payload), (400, {"error": RESTORE_ID_MESSAGE}))
        status, _ = self.restore(9999)
        self.assertEqual(status, 400)
        jobs = self.status_jobs()
        self.assertEqual((jobs[exported]["state"], jobs[exported]["source_archived"],
                          jobs[exported]["source_archive"]["state"], jobs[exported]["archive"]),
                         ("READY_TO_EXPORT", False, "RESTORED", None))
        self.assertEqual((jobs[skipped]["state"], jobs[skipped]["source_archived"], jobs[skipped]["archive"]["eligible"]),
                         ("SKIPPED", False, True))

    def test_recheck_route(self):
        exported = self.make_exported_job("tap12")
        self.recycler.verified = False
        status, result = self.archive([exported])
        self.assertEqual((status, result["results"][0]["status"]), (200, "UNVERIFIED"))
        row = self.store.latest_source_archive(exported)
        summary = self.status_jobs()[exported]["source_archive"]
        self.assertEqual((summary["export_recycled"], summary["export_verified"]), (True, False))
        for body, error in (({"kind": "archive", "id": row["id"]}, "Loại kiểm tra Thùng rác không hợp lệ."),
                            ({"kind": "archive_export"}, "Mã bản ghi không hợp lệ."),
                            ({"kind": "archive_export", "id": "1"}, "Mã bản ghi không hợp lệ.")):
            with self.subTest(body=body):
                status, payload = self.post("/api/source-recycle-check", body=body)
                self.assertEqual((status, payload), (400, {"error": error}))
        status, result = self.post("/api/source-recycle-check", body={"kind": "archive_export", "id": row["id"]})
        self.assertEqual((status, result["found"], result["job_id"]), (200, False, exported))
        self.finder.record = RECORD
        status, result = self.post("/api/source-recycle-check", body={"kind": "archive_export", "id": row["id"]})
        self.assertEqual((status, result["found"], result["record"]), (200, True, RECORD))
        self.assertEqual(self.finder.calls[-1][1:], (str(self.output_of(exported).resolve()), row["output_bytes"]))
        summary = self.status_jobs()[exported]["source_archive"]
        self.assertEqual((summary["export_verified"], summary["export_verified_at_archive"],
                          summary["export_verified_later_at"]), (True, False, result["checked_at"]))
        status, payload = self.post("/api/source-recycle-check", body={"kind": "archive_export", "id": row["id"]})
        self.assertEqual((status, payload["code"]), (409, "already_verified"))
        self.assertEqual(self.store.latest_source_archive(exported), row)


class StatusAndHideTests(ArchiveHttpFixture):
    def test_status_exposes_archive_and_hidden(self):
        exported, skipped = self.make_exported_job("tap12"), self.make_skipped_job("tap13")
        waiting = self.make_job("tap14", state="WAITING_REVIEW", items=[item("a")], status="REVIEW_REQUIRED")
        output = self.output_of(exported)
        manifest = output.with_name(output.name + ".manifest.json")
        jobs = self.status_jobs()
        self.assertEqual(jobs[exported]["archive"], {
            "eligible": True, "kind": "EXPORTED", "reason": None, "size_bytes": self.source_of(exported).stat().st_size,
            "output_name": output.name, "output_bytes": output.stat().st_size,
            "manifest_bytes": manifest.stat().st_size, "exported_at": jobs[exported]["cleanup"]["exported_at"],
            "skipped_at": None,
        })
        self.assertEqual((jobs[skipped]["archive"]["kind"], jobs[skipped]["archive"]["output_name"],
                          jobs[skipped]["archive"]["manifest_bytes"]), ("SKIPPED", None, None))
        self.assertEqual((jobs[waiting]["archive"], jobs[waiting]["cleanup"], jobs[waiting]["source_archive"],
                          jobs[waiting]["source_archived"], jobs[waiting]["hidden_at"]), (None, None, None, False, None))
        self.assertEqual((self.bin.calls, self.recycler.calls), ([], []))
        self.archive([exported])
        jobs = self.status_jobs()
        archived = jobs[exported]
        self.assertEqual((archived["state"], archived["source_archived"], archived["source_present"],
                          archived["archive"]["eligible"], archived["archive"]["reason"],
                          archived["cleanup"]["reason"]),
                         ("COMPLETED", True, False, False, REASON_ARCHIVED, CLEANUP_REASON_ARCHIVED))
        summary = archived["source_archive"]
        self.assertEqual(
            {key: summary[key] for key in ("state", "kind", "file_name", "archive_path", "output_name",
                                           "export_recycled", "export_verified", "warning", "error")},
            {"state": "ARCHIVED", "kind": "EXPORTED", "file_name": "tap12.mp4",
             "archive_path": str(self.root / "archive" / "sources" / "tap12" / "tap12.mp4"),
             "output_name": output.name, "export_recycled": True, "export_verified": True, "warning": None,
             "error": None},
        )
        self.assertTrue(summary["archived_at"])
        # The review page payload carries the same two keys (L6).
        status, review = self.get(f"/api/jobs/{exported}/review/export")
        self.assertEqual((status, review["status"], review["source_cleaned"], review["source_archived"],
                          review["source_archive"]), (200, "COMPLETED", False, True, {**summary, "warning": None}))
        status, review = self.get(f"/api/jobs/{skipped}/review/export")
        self.assertEqual((status, review["source_archived"], review["source_archive"]), (200, False, None))

    def test_cancel_repeat_409_and_hide_unhide_routes(self):
        queued = self.make_job("tap20", state="QUEUED", items=[item("a")])
        waiting = self.make_job("tap21", state="WAITING_REVIEW", items=[item("a")])
        reports = tree_digest(self.root / "reports")
        status, job = self.post(f"/api/jobs/{queued}/cancel")
        self.assertEqual((status, job["state"], job["hidden_at"]), (200, "CANCELLED", None))
        status, payload = self.post(f"/api/jobs/{queued}/cancel")
        self.assertEqual((status, payload), (409, {
            "error": f"Video #{queued} đã được hủy trước đó; không hủy thêm lần nữa.", "code": "already_cancelled",
        }))
        status, payload = self.post(f"/api/jobs/{waiting}/hide")
        self.assertEqual((status, payload), (409, {
            "error": f"Video #{waiting} chưa bị hủy; chỉ ẩn được video đã hủy.", "code": "not_cancelled",
        }))
        status, payload = self.post(f"/api/jobs/{queued}/unhide")
        self.assertEqual((status, payload), (409, {"error": f"Video #{queued} không bị ẩn.", "code": "not_hidden"}))
        status, payload = self.post(f"/api/jobs/{queued}/hide", token=None)
        self.assertEqual((status, payload), (403, {"error": TOKEN_REFUSAL}))
        status, hidden = self.post(f"/api/jobs/{queued}/hide")
        self.assertEqual((status, hidden["state"]), (200, "CANCELLED"))
        self.assertTrue(hidden["hidden_at"])
        status, payload = self.post(f"/api/jobs/{queued}/hide")
        self.assertEqual((status, payload), (409, {
            "error": f"Video #{queued} đã được ẩn khỏi danh sách.", "code": "already_hidden",
        }))
        self.assertEqual(self.status_jobs()[queued]["hidden_at"], hidden["hidden_at"])
        status, shown = self.post(f"/api/jobs/{queued}/unhide")
        self.assertEqual((status, shown["state"], shown["hidden_at"]), (200, "CANCELLED", None))
        status, _ = self.post("/api/jobs/9999/hide")
        self.assertEqual(status, 400)
        # Display only: reports and the source stay; one event per change.
        self.assertEqual(tree_digest(self.root / "reports"), reports)
        self.assertTrue(self.source_of(queued).is_file())
        kinds = [event["event_type"] for event in self.store.events(queued)]
        self.assertEqual([kinds.count(kind) for kind in ("JOB_CANCELLED", "JOB_HIDDEN", "JOB_UNHIDDEN")], [1, 1, 1])


class ClassDefaultTests(ArchiveHttpFixture):
    def test_class_defaults_refuse(self):
        for name in ("export_recycler", "record_finder"):
            with self.subTest(name=name), self.assertRaises(RuntimeError) as caught:
                getattr(ControlCenter, name)(Path("E:/x-reviewed.mp4"), allowed_root=Path("E:/"), expected_size=1,
                                             timeout=1.0, on_late_result=None)
            self.assertEqual(str(caught.exception), UNCONFIGURED_MESSAGE)
        with self.assertRaises(ValueError) as caught:  # job_purge.DeleteRefused
            ControlCenter.source_deleter(Path("E:/input/x.mp4"), allowed_root=Path("E:/input"), expected_size=1)
        self.assertEqual(str(caught.exception), cc.UNCONFIGURED_DELETE_MESSAGE)
        with self.assertRaises(RecycleRefused):
            ControlCenter.bin_info(Path("E:/"))
        # A stub that never got the real export recycler: the archive is undone, the export stays.
        exported = self.make_exported_job("tap12")
        del self.center.export_recycler
        status, result = self.archive([exported])
        self.assertEqual((status, result["results"][0]["status"], result["results"][0]["message"]), (
            200, "FAILED",
            f"Không chuyển được bản xuất vào Thùng rác ({UNCONFIGURED_MESSAGE}); đã đưa video gốc về input.",
        ))
        self.assertTrue(self.source_of(exported).is_file())
        self.assertTrue(self.output_of(exported).is_file())
        self.assertIsNone(self.store.source_lock(exported))
        self.center.export_recycler = self.recycler
        self.recycler.verified = False
        self.assertEqual(self.archive([exported])[1]["results"][0]["status"], "UNVERIFIED")
        del self.center.record_finder
        row = self.store.latest_source_archive(exported)
        status, payload = self.post("/api/source-recycle-check", body={"kind": "archive_export", "id": row["id"]})
        self.assertEqual((status, payload["code"]), (409, "bin_unavailable"))
        self.assertEqual(self.store.recycle_checks("ARCHIVE_EXPORT", row["id"]), [])


if __name__ == "__main__":
    unittest.main()
