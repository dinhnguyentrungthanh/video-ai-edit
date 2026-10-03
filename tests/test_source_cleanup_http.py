"""Dọn video gốc through the Control Center (batch 3, step B8).

The preview and cleanup routes over real HTTP on port 0, the status() fields,
the 409 conflicts, the lock order with finalize and review decisions, the
startup reconciliation and the shutdown wait.

Every Control Center here is a stub built with ``ControlCenter.__new__`` on a
temporary root (a real JobStore and JobScheduler, never the project's state),
with a fake recycler that moves the file into a folder outside the root and a
fake ``bin_info`` with the real numbers of drive E:. The one real
``ControlCenter(...)`` (startup test) never calls its recycler. The module setup
also replaces ``recycle_bin._shell_delete`` with a function that fails the
test, so nothing here can reach the real Recycle Bin.
"""

import hashlib
import http.client
import json
import os
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from biliflow import brand_memory
from biliflow import control_center as cc
from biliflow import recycle_bin, source_cleanup
from biliflow.control_center import ControlCenter, _handler_class
from biliflow.job_store import JobStore
from biliflow.recycle_bin import BinInfo, RecycleRefused, RecycleResult
from biliflow.review_evidence import ReviewFrameCache
from biliflow.review_workflow import approved_operations, review_export_paths
from biliflow.scheduler import JobScheduler


GUID = "{2fd9f59c-d156-40e6-b5c9-93b787892ee9}"
MAX_BYTES = 52_157_218_816  # MaxCapacity 49741 MiB
USED_BYTES = 11_823_971_925
ITEMS = 7
NEAR_FULL = MAX_BYTES - recycle_bin.CAPACITY_MARGIN_BYTES
NOW = datetime.now(timezone.utc)

# Verbatim texts of the contract (temp/ui-plan/batch3/contract.md).
JOB_IDS_MESSAGE = "Chọn từ 1 đến 50 video mỗi lần dọn."
PREVIEW_ID_MESSAGE = "Thiếu mã xem trước; hãy mở lại hộp thoại dọn video gốc."
BUSY_MESSAGE = "Đang dọn video gốc; chờ lần dọn trước xong rồi thử lại."
PREVIEW_CHANGED_MESSAGE = "Danh sách đã thay đổi, hãy xem lại."
NOTHING_ELIGIBLE_MESSAGE = "Không có video nào dọn được trong danh sách đã chọn."
UNCONFIGURED_MESSAGE = "Chưa cấu hình Thùng rác cho Control Center này."
RECYCLED_MESSAGE = "Đã chuyển video gốc vào Thùng rác"
STOPPING_MESSAGE = "BiliFlow đang tắt; video này chưa được dọn."
INTERRUPTED_MESSAGE = "Bị gián đoạn trước khi chuyển; video gốc vẫn còn."
REASON_RECYCLED = "Video gốc đã được dọn trước đó"
REASON_STATE = "Chỉ dọn được video đã xuất hoặc đã bỏ qua (mục “Hoàn tất”)"
REASON_SOURCE_MISSING = "Video gốc không còn trong thư mục input"
REASON_OUTPUT_OLDER = (
    "Bản xuất hiện có không khớp quyết định duyệt hiện tại (mở “Duyệt cảnh” và xuất lại trước khi dọn)"
)
REASON_OUTPUT_STALE = (
    "Bản xuất hiện có không ứng với lần duyệt mới nhất (mở “Duyệt cảnh” và xuất lại trước khi dọn)"
)
HOST_REFUSAL = "Địa chỉ truy cập không hợp lệ"
TOKEN_REFUSAL = "Phiên Control Center không hợp lệ"

_SHELL_PATCH = None


def _refuse_real_shell(*args, **kwargs):
    raise AssertionError("real Recycle Bin call in a test")


def setUpModule():
    """Contract R13: no test in this module may reach the real Windows Recycle Bin."""
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
    """bin_info(path) -> BinInfo of drive E: (or an error); records every call."""

    def __init__(self, used=USED_BYTES, error=None):
        self.used, self.error = used, error
        self.calls = []

    def __call__(self, path):
        self.calls.append(Path(path))
        if self.error is not None:
            raise self.error
        return BinInfo("E:", "E:\\", GUID, MAX_BYTES, self.used, ITEMS)


class FakeRecycler:
    """Moves the file into a bin folder outside the root and records every call."""

    def __init__(self, bin_dir):
        self.bin_dir = Path(bin_dir)
        self.calls = []

    def __call__(self, path, *, allowed_root, expected_size, timeout, on_late_result):
        self.calls.append({
            "path": path, "allowed_root": allowed_root, "expected_size": expected_size,
            "timeout": timeout,
        })
        token = uuid.uuid4().hex[:6].upper()
        os.replace(path, self.bin_dir / f"$R{token}{Path(path).suffix}")
        record = self.bin_dir / f"$I{token}{Path(path).suffix}"
        record.write_bytes(b"record")
        return RecycleResult(str(path), expected_size, True, str(record), 0.01)


class CleanupHttpFixture(unittest.TestCase):
    """SkipFixture of test_skip_export plus exported jobs, a fake recycler and a fake bin."""

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        for name in ("input", "reports/jobs", "output", "work", "state", "logs"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        # The brand and studio-logo memories (empty, real schema) and the session file
        # of state/: a cleanup must leave every one of them byte for byte.
        for name, payload in (
            ("brand-memory.json", brand_memory._empty_memory()),
            ("studio-logo-memory.json", brand_memory._empty_studio_logo_memory()),
            ("control-center.json", {"schema_version": 1, "port": 0}),
        ):
            (self.root / "state" / name).write_text(json.dumps(payload), encoding="utf-8")
        bin_temp = TemporaryDirectory()
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
        center.recycler = self.recycler
        center.bin_info = self.bin
        self.center = center
        cc._SUMMARY_CACHE.clear()
        source_cleanup._CACHE.clear()
        self.addCleanup(source_cleanup._CACHE.clear)
        for target, kwargs in (
            # No scan stages here: a queued job's only pending stage is its render.
            ("biliflow.scheduler.pipeline_stages", {"return_value": []}),
            # status() without psutil and disk scans.
            ("biliflow.control_center._resources", {"return_value": {}}),
            ("biliflow.control_center.storage_status",
             {"return_value": SimpleNamespace(as_dict=lambda: {})}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    # ----------------------------------------------------------- jobs
    def make_job(self, name, *, state, items, status="READY_FOR_EDIT_PLAN", source_name=None):
        source = self.root / "input" / (source_name or f"{name}.mp4")
        source.write_bytes(name.encode() * 512)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        folder = self.root / "reports" / "jobs" / name
        folder.mkdir(parents=True)
        (folder / "scan.json").write_text('{"intervals": []}', encoding="utf-8")
        queue = {
            "status": status, "updated_at": iso(NOW),
            "source": {"path": str(source), "sha256": digest, "duration_seconds": 60.0},
            "reports": [f"reports/jobs/{name}/scan.json"], "items": items, "advisory_items": [],
        }
        (folder / "review-queue.json").write_text(json.dumps(queue), encoding="utf-8")
        info = source.stat()
        job = self.store.upsert_job(
            job_key=name, source_path=source, source_sha256=digest,
            source_size_bytes=info.st_size, source_mtime_ns=info.st_mtime_ns,
            content_style="animation", state=state,
        )
        self.store.update_job(
            job["id"], active_queue_path=f"reports/jobs/{name}/review-queue.json",
            active_revision=1, progress=1.0,
        )
        return int(job["id"])

    def queue(self, job_id):
        path = self.root / self.store.get_job(job_id)["active_queue_path"]
        return json.loads(path.read_text(encoding="utf-8"))

    def output_of(self, job_id):
        return review_export_paths(self.root, self.queue(job_id))[1]

    def manifest_of(self, job_id):
        output = self.output_of(job_id)
        return json.loads(output.with_suffix(output.suffix + ".manifest.json").read_text(encoding="utf-8"))

    def make_exported_job(self, name, *, decided_at=None, plan_queue=None, source_name=None,
                          items=None, operations=False):
        """A COMPLETED job with a verified export of its current review (like the renderer writes).

        ``operations=True`` adds the manifest's ``operations`` (the queue's approved operations).
        """
        decided = iso(NOW - timedelta(hours=1)) if decided_at is None else decided_at
        job_id = self.make_job(
            name, state="COMPLETED", source_name=source_name,
            items=items or [item("a", decided_at=decided), item("b", decided_at=decided)],
        )
        job = self.store.get_job(job_id)
        plan_path, output, _ = review_export_paths(self.root, self.queue(job_id))
        output.write_bytes(b"OUTPUT" + name.encode() * 300)
        output_sha = hashlib.sha256(output.read_bytes()).hexdigest()
        manifest = {
            "schema_version": 1, "status": "COMPLETED", "created_at": iso(NOW),
            "edit_plan": plan_path.relative_to(self.root).as_posix(),
            "source": {
                "path": job["source_path"], "sha256": job["source_sha256"], "duration_seconds": 60.0,
                "sha256_after_render": job["source_sha256"], "modified": False,
            },
            "output": {
                "path": output.relative_to(self.root).as_posix(), "bytes": output.stat().st_size,
                "sha256": output_sha,
            },
            "encoding": {"full_decode_validation_passed": True},
        }
        if operations:
            manifest["operations"] = approved_operations(self.queue(job_id))
        output.with_suffix(output.suffix + ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        plan_path.write_text(json.dumps({
            "status": "FINAL_RENDER_COMPLETED",
            "review_queue": job["active_queue_path"] if plan_queue is None else plan_queue,
        }), encoding="utf-8")
        self.store.add_artifact(
            job_id, stage_name="render", kind="final_output",
            path=output.relative_to(self.root).as_posix(), sha256=output_sha,
            bytes_count=output.stat().st_size,
        )
        return job_id

    def make_skipped_job(self, name):
        """A video the user marked Bỏ qua (không xuất) through the real skip_export."""
        job_id = self.make_job(name, state="READY_TO_EXPORT", items=[item("a", decided_at=iso(NOW))])
        self.center.skip_export(job_id)
        return job_id

    def source_of(self, job_id):
        return Path(self.store.get_job(job_id)["source_path"])

    def events(self, job_id, kind):
        return [event for event in self.store.events(job_id) if event["event_type"] == kind]

    def db_dump(self):
        """Every row of every table: a read-only route must leave it identical."""
        with self.store._lock:
            tables = [row[0] for row in self.store._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()]
            return {
                table: [tuple(row) for row in self.store._connection.execute(
                    f'SELECT * FROM "{table}" ORDER BY rowid'
                ).fetchall()]
                for table in tables
            }

    def kept_files(self):
        """Digests of what a cleanup must never touch (reports, output, work, state/*.json)."""
        state = hashlib.sha256()
        for path in sorted((self.root / "state").glob("*.json")):
            state.update(path.name.encode() + path.read_bytes())
        return {
            "reports": tree_digest(self.root / "reports"),
            "output": tree_digest(self.root / "output"),
            "work": tree_digest(self.root / "work"),
            "state_json": state.hexdigest(),
        }

    # ------------------------------------------------- real HTTP on port 0
    def serve(self):
        if getattr(self, "server", None) is not None:
            return
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_class(self.center))
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, *, token="test-token", host=None, body=None, raw=None):
        self.serve()
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            headers = {"Host": host or f"127.0.0.1:{self.port}", "Content-Type": "application/json"}
            if token:
                headers["X-BiliFlow-Token"] = token
            payload = None
            if method == "POST":
                payload = raw if raw is not None else json.dumps(body or {}).encode()
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            data = response.read()
            if response.getheader("Content-Type", "").startswith("application/json"):
                return response.status, json.loads(data or b"{}")
            return response.status, data
        finally:
            connection.close()

    def get(self, path, *, host=None):
        return self.request("GET", path, host=host)

    def post(self, path, *, token="test-token", host=None, body=None, raw=None):
        return self.request("POST", path, token=token, host=host, body=body, raw=raw)

    def preview(self, ids):
        status, body = self.get(f"/api/source-cleanup/preview?ids={','.join(str(x) for x in ids)}")
        self.assertEqual(status, 200, body)
        return body

    def clean(self, ids, preview_id):
        return self.post("/api/source-cleanup", body={"job_ids": list(ids), "preview_id": preview_id})

    def finalize(self, job_id):
        with (
            patch("biliflow.control_center.build_edit_plan", return_value={"status": "READY_FOR_PREVIEW"}),
            patch("biliflow.control_center.authorize_final_from_resolved_review"),
        ):
            return self.center.finalize(job_id)


class PreviewRouteTests(CleanupHttpFixture):
    """GET /api/source-cleanup/preview: what a cleanup would do, without doing anything."""

    def test_preview_lists_exported_and_skipped_videos_and_is_read_only(self):
        exported = self.make_exported_job("tap12", source_name="Tập 12.mp4")
        skipped = self.make_skipped_job("tap60")
        older = self.make_exported_job("tap37", decided_at=iso(NOW + timedelta(minutes=5)))
        other_review = self.make_exported_job("tap38", plan_queue="reports/jobs/old/review-queue.json")
        missing = self.make_exported_job("tap3")
        self.source_of(missing).unlink()
        ready = self.make_job("tap5", state="READY_TO_EXPORT", items=[item("a", decided_at=iso(NOW))])
        dump, files = self.db_dump(), self.kept_files()
        inputs = tree_digest(self.root / "input")
        hashed = AssertionError("a preview never hashes")
        with patch.object(source_cleanup, "sha256_file", side_effect=hashed), \
                patch("biliflow.job_store.sha256_file", side_effect=hashed):
            ids = [ready, missing, other_review, older, skipped, exported, 999]
            status, preview = self.get(f"/api/source-cleanup/preview?ids={','.join(map(str, ids))}")
        self.assertEqual(status, 200, preview)

        exported_job = self.store.get_job(exported)
        skipped_job = self.store.get_job(skipped)
        output = self.output_of(exported)
        manifest = self.manifest_of(exported)
        total = exported_job["source_size_bytes"] + skipped_job["source_size_bytes"]
        self.assertEqual(preview["eligible"], [
            {
                "job_id": exported, "name": "Tập 12.mp4", "file_name": "Tập 12.mp4",
                "source_path": str(self.source_of(exported).resolve()),
                "size_bytes": exported_job["source_size_bytes"], "kind": "EXPORTED",
                "output_path": output.relative_to(self.root).as_posix(), "output_name": output.name,
                "output_bytes": manifest["output"]["bytes"], "exported_at": manifest["created_at"],
                "skipped_at": None,
            },
            {
                "job_id": skipped, "name": "tap60.mp4", "file_name": "tap60.mp4",
                "source_path": str(self.source_of(skipped).resolve()),
                "size_bytes": skipped_job["source_size_bytes"], "kind": "SKIPPED",
                "output_path": None, "output_name": None, "output_bytes": None, "exported_at": None,
                "skipped_at": self.store.setting(f"skip:{skipped}")["skipped_at"],
            },
        ])
        self.assertEqual(preview["ineligible"], [
            {"job_id": older, "name": "tap37.mp4", "reason": REASON_OUTPUT_OLDER},
            {"job_id": other_review, "name": "tap38.mp4", "reason": REASON_OUTPUT_STALE},
            {"job_id": missing, "name": "tap3.mp4", "reason": REASON_SOURCE_MISSING},
            {"job_id": ready, "name": "tap5.mp4", "reason": REASON_STATE},
            {"job_id": 999, "name": "", "reason": "Không tìm thấy video #999"},
        ])
        self.assertEqual((preview["count"], preview["total_bytes"], preview["blocked"]), (2, total, None))
        self.assertEqual(preview["recycle_bin"], {
            "volume": "E:", "used_bytes": USED_BYTES, "items": ITEMS, "max_bytes": MAX_BYTES,
            "after_bytes": USED_BYTES + total,
        })
        self.assertRegex(preview["preview_id"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            preview["preview_id"],
            source_cleanup.preview_cleanup(
                self.root, self.store, self.center.scheduler, [exported, skipped], bin_info=FakeBin(),
            )["preview_id"],
        )
        # Read-only: the bin is asked once (about input/), nothing is moved or written.
        self.assertEqual(self.bin.calls, [self.root / "input"])
        self.assertEqual(self.recycler.calls, [])
        self.assertEqual(self.db_dump(), dump)
        self.assertEqual(self.kept_files(), files)
        self.assertEqual(tree_digest(self.root / "input"), inputs)

    def test_preview_refuses_foreign_hosts_and_bad_id_lists(self):
        exported = self.make_exported_job("tap12")
        self.serve()
        for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1.evil.example", "[::2]"):
            with self.subTest(host=host):
                status, body = self.get(f"/api/source-cleanup/preview?ids={exported}", host=host)
                self.assertEqual((status, body), (403, {"error": HOST_REFUSAL}))
        too_many = ",".join(str(value) for value in range(1, 52))
        for query in ("?ids=", "?ids=abc", "?ids=0", f"?ids={too_many}", "", "?ids=1,,2", "?ids=-1",
                      "?ids=1.5", "?ids=%EF%BC%91", "?other=1"):
            with self.subTest(query=query):
                status, body = self.get(f"/api/source-cleanup/preview{query}")
                self.assertEqual((status, body), (400, {"error": JOB_IDS_MESSAGE}))
        # 50 ids is the limit, unknown ids are listed as such.
        status, body = self.get(f"/api/source-cleanup/preview?ids={','.join(str(v) for v in range(1, 51))}")
        self.assertEqual((status, body["count"], len(body["ineligible"])), (200, 1, 49))
        self.assertEqual(self.recycler.calls, [])

    def test_a_stub_without_a_bin_shows_the_preview_as_blocked(self):
        exported = self.make_exported_job("tap12")
        del self.center.bin_info  # the class default of a stub that never got a real bin
        preview = self.preview([exported])
        self.assertEqual((preview["count"], preview["recycle_bin"], preview["blocked"]),
                         (1, None, UNCONFIGURED_MESSAGE))
        self.center.bin_info = FakeBin(used=NEAR_FULL)
        preview = self.preview([exported])
        self.assertEqual(preview["recycle_bin"]["used_bytes"], NEAR_FULL)
        self.assertTrue(preview["blocked"].startswith("Không thể dọn: Thùng rác của ổ E: đang chứa "))

    def test_a_re_recorded_decision_and_the_finalize_shortcut_keep_the_export_cleanable(self):
        # Fix pass: the same KEEP posted again after the export (an undo after
        # a misclick re-posts it) writes a newer decided_at; finalize then finds
        # the export of these decisions and marks the job COMPLETED without a
        # render, so the manifest keeps its older created_at.
        fields = {"preview_images": [], "priority": "high", "labels": ["nsfw"], "max_score": 0.999}
        decided = iso(NOW - timedelta(hours=1))
        job_id = self.make_exported_job(
            "tap13", operations=True,
            items=[dict(item("a", decided_at=decided), **fields), dict(item("b", decided_at=decided), **fields)],
        )
        self.assertEqual(self.preview([job_id])["count"], 1)
        status, body = self.post(f"/api/jobs/{job_id}/review/decision", body={"id": "a", "decision": "KEEP"})
        self.assertEqual((status, body["status"]), (200, "READY_FOR_EDIT_PLAN"), body)
        self.assertEqual(self.store.get_job(job_id)["state"], "READY_TO_EXPORT")
        newest = max(datetime.fromisoformat(x["decided_at"]) for x in self.queue(job_id)["items"])
        self.assertGreater(newest, datetime.fromisoformat(self.manifest_of(job_id)["created_at"]))
        self.assertEqual(self.preview([job_id])["ineligible"][0]["reason"], REASON_STATE)
        result = self.finalize(job_id)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(self.store.get_job(job_id)["state"], "COMPLETED")
        preview = self.preview([job_id])
        self.assertEqual(([x["job_id"] for x in preview["eligible"]], preview["ineligible"]), ([job_id], []))


class CleanupRouteTests(CleanupHttpFixture):
    """POST /api/source-cleanup: auth, the 200 result, the 409 conflicts and the 400 errors."""

    def test_post_needs_the_local_host_and_the_session_token(self):
        exported = self.make_exported_job("tap12")
        preview_id = self.preview([exported])["preview_id"]
        dump, inputs = self.db_dump(), tree_digest(self.root / "input")
        body = {"job_ids": [exported], "preview_id": preview_id}
        for host, token, error in (
            ("evil.example", "test-token", HOST_REFUSAL),
            ("127.0.0.1.evil.example", "test-token", HOST_REFUSAL),
            (None, None, TOKEN_REFUSAL),
            (None, "wrong", TOKEN_REFUSAL),
            (None, "test-token ", TOKEN_REFUSAL),
        ):
            with self.subTest(host=host, token=token):
                status, payload = self.post("/api/source-cleanup", host=host, token=token, body=body)
                self.assertEqual((status, payload), (403, {"error": error}))
        self.assertEqual(self.recycler.calls, [])
        self.assertEqual(self.db_dump(), dump)
        self.assertEqual(tree_digest(self.root / "input"), inputs)
        # The same body with the right Host and token is accepted.
        status, result = self.post("/api/source-cleanup", body=body)
        self.assertEqual((status, result["recycled_count"]), (200, 1))

    def test_a_confirmed_cleanup_recycles_and_the_dashboard_follows(self):
        exported = self.make_exported_job("tap12", source_name="Tập 12.mp4")
        skipped = self.make_skipped_job("tap60")
        preview = self.preview([skipped, exported])
        ids = [entry["job_id"] for entry in preview["eligible"]]
        self.assertEqual(ids, [exported, skipped])
        sources = {job_id: self.source_of(job_id).resolve() for job_id in ids}
        sizes = {job_id: self.store.get_job(job_id)["source_size_bytes"] for job_id in ids}
        files = self.kept_files()
        status, result = self.clean(ids, preview["preview_id"])
        self.assertEqual(status, 200, result)
        self.assertEqual(result, {
            "results": [
                {"job_id": exported, "name": "Tập 12.mp4", "status": "RECYCLED",
                 "message": RECYCLED_MESSAGE, "size_bytes": sizes[exported]},
                {"job_id": skipped, "name": "tap60.mp4", "status": "RECYCLED",
                 "message": RECYCLED_MESSAGE, "size_bytes": sizes[skipped]},
            ],
            "recycled_count": 2, "recycled_bytes": sizes[exported] + sizes[skipped],
            "failed_count": 0, "pending": 0,
        })
        self.assertEqual(self.recycler.calls, [
            {"path": sources[job_id], "allowed_root": self.root / "input",
             "expected_size": sizes[job_id], "timeout": 60.0}
            for job_id in ids
        ])
        # Only the sources left: reports, output, work and state/*.json are unchanged.
        self.assertEqual(self.kept_files(), files)
        for job_id in ids:
            self.assertFalse(sources[job_id].exists())
            event = self.events(job_id, "SOURCE_RECYCLED")
            self.assertEqual(len(event), 1)
            self.assertEqual((event[0]["message"], event[0]["payload"]["path"]),
                             (RECYCLED_MESSAGE, str(sources[job_id])))
        status, value = self.get("/api/status")
        self.assertEqual(status, 200)
        self.assertFalse(value["source_cleanup_running"])
        jobs = {job["id"]: job for job in value["jobs"]}
        for job_id, kind in ((exported, "EXPORTED"), (skipped, "SKIPPED")):
            with self.subTest(job_id=job_id):
                card = jobs[job_id]
                row = card["source_cleanup"]
                self.assertEqual(
                    (row["state"], row["kind"], row["verified"], row["file_name"], row["size_bytes"],
                     row["source_path"], row["error"]),
                    ("RECYCLED", kind, True, sources[job_id].name, sizes[job_id], str(sources[job_id]), None),
                )
                self.assertTrue(card["source_cleaned"])
                self.assertFalse(card["source_present"])
                self.assertEqual((card["cleanup"]["eligible"], card["cleanup"]["kind"], card["cleanup"]["reason"]),
                                 (False, kind, REASON_RECYCLED))
        # The review page learns it too (read-only page, B4).
        status, value = self.get(f"/api/jobs/{exported}/review/export")
        self.assertEqual((status, value["source_cleaned"], value["source_name"]), (200, True, "Tập 12.mp4"))
        self.assertEqual((value["source_cleanup"]["state"], value["source_cleanup"]["file_name"]),
                         ("RECYCLED", "Tập 12.mp4"))
        # The same confirmation sent again runs nothing: the list changed.
        status, payload = self.clean(ids, preview["preview_id"])
        self.assertEqual((status, payload["code"], payload["preview"]["count"]), (409, "preview_changed", 0))
        self.assertEqual(len(self.recycler.calls), 2)

    def test_a_stale_preview_id_is_409_with_the_new_preview(self):
        exported = self.make_exported_job("tap12")
        old = self.preview([exported])
        source = self.source_of(exported)
        info = source.stat()
        os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))
        status, payload = self.clean([exported], old["preview_id"])
        self.assertEqual(status, 409)
        self.assertEqual((payload["error"], payload["code"]), (PREVIEW_CHANGED_MESSAGE, "preview_changed"))
        new = self.preview([exported])
        self.assertNotEqual(new["preview_id"], old["preview_id"])
        self.assertEqual(payload["preview"], new)
        status, payload = self.clean([exported], "f" * 64)
        self.assertEqual((status, payload["code"]), (409, "preview_changed"))
        self.assertEqual(self.recycler.calls, [])
        self.assertIsNone(self.store.latest_source_cleanup(exported))
        status, result = self.clean([exported], new["preview_id"])
        self.assertEqual((status, result["results"][0]["status"]), (200, "RECYCLED"))

    def test_a_full_or_unavailable_bin_is_409_before_anything_runs(self):
        exported = self.make_exported_job("tap12")
        preview_id = self.preview([exported])["preview_id"]
        self.center.bin_info = FakeBin(used=NEAR_FULL)
        status, payload = self.clean([exported], preview_id)
        self.assertEqual((status, payload["code"]), (409, "bin_capacity"))
        self.assertTrue(payload["error"].startswith("Không thể dọn: Thùng rác của ổ E: đang chứa "))
        self.assertEqual(payload["preview"]["blocked"], payload["error"])
        self.assertEqual(payload["preview"]["preview_id"], preview_id)
        not_fixed = recycle_bin.NOT_FIXED_MESSAGE.format(volume="E:")
        self.center.bin_info = FakeBin(error=RecycleRefused(not_fixed))
        status, payload = self.clean([exported], preview_id)
        self.assertEqual((status, payload["error"], payload["code"]), (409, not_fixed, "bin_unavailable"))
        self.assertEqual((payload["preview"]["recycle_bin"], payload["preview"]["blocked"]), (None, not_fixed))
        del self.center.bin_info  # the class default refuses too
        status, payload = self.clean([exported], preview_id)
        self.assertEqual((status, payload["error"], payload["code"]),
                         (409, UNCONFIGURED_MESSAGE, "bin_unavailable"))
        self.assertEqual(self.recycler.calls, [])
        self.assertIsNone(self.store.latest_source_cleanup(exported))
        self.assertTrue(self.source_of(exported).is_file())

    def test_a_running_cleanup_is_409_busy(self):
        exported = self.make_exported_job("tap12")
        preview_id = self.preview([exported])["preview_id"]
        self.assertTrue(source_cleanup._EXECUTE_LOCK.acquire(blocking=False))
        try:
            status, payload = self.clean([exported], preview_id)
            self.assertEqual((status, payload), (409, {"error": BUSY_MESSAGE, "code": "busy"}))
            self.assertTrue(self.get("/api/status")[1]["source_cleanup_running"])
        finally:
            source_cleanup._EXECUTE_LOCK.release()
        self.assertEqual(self.recycler.calls, [])
        self.assertFalse(self.get("/api/status")[1]["source_cleanup_running"])
        self.assertEqual(self.clean([exported], preview_id)[0], 200)

    def test_bad_bodies_are_400_and_never_reach_the_recycler(self):
        exported = self.make_exported_job("tap12")
        ready = self.make_job("tap5", state="READY_TO_EXPORT", items=[item("a", decided_at=iso(NOW))])
        preview_id = self.preview([exported])["preview_id"]
        cases = [
            ({"job_ids": str(exported), "preview_id": preview_id}, JOB_IDS_MESSAGE),
            ({"job_ids": exported, "preview_id": preview_id}, JOB_IDS_MESSAGE),
            ({"job_ids": {"id": exported}, "preview_id": preview_id}, JOB_IDS_MESSAGE),
            ({"preview_id": preview_id}, JOB_IDS_MESSAGE),
            ({"job_ids": [], "preview_id": preview_id}, JOB_IDS_MESSAGE),
            ({"job_ids": [True], "preview_id": preview_id}, JOB_IDS_MESSAGE),
            ({"job_ids": [str(exported)], "preview_id": preview_id}, JOB_IDS_MESSAGE),
            ({"job_ids": list(range(1, 52)), "preview_id": preview_id}, JOB_IDS_MESSAGE),
            ({"job_ids": [exported]}, PREVIEW_ID_MESSAGE),
            ({"job_ids": [exported], "preview_id": None}, PREVIEW_ID_MESSAGE),
            ({"job_ids": [exported], "preview_id": preview_id.upper()}, PREVIEW_ID_MESSAGE),
            ({"job_ids": [exported], "preview_id": preview_id[:-1]}, PREVIEW_ID_MESSAGE),
            ({"job_ids": [exported], "preview_id": 42}, PREVIEW_ID_MESSAGE),
        ]
        for body, error in cases:
            with self.subTest(body=body):
                status, payload = self.post("/api/source-cleanup", body=body)
                self.assertEqual((status, payload), (400, {"error": error}))
        # Nothing eligible in the list (its own, correct preview id).
        empty = self.preview([ready])
        status, payload = self.clean([ready], empty["preview_id"])
        self.assertEqual((status, payload), (400, {"error": NOTHING_ELIGIBLE_MESSAGE}))
        status, payload = self.post("/api/source-cleanup", raw=b"[1]")
        self.assertEqual((status, payload), (400, {"error": "JSON object required"}))
        self.assertEqual(self.recycler.calls, [])
        self.assertIsNone(self.store.latest_source_cleanup(exported))

    def test_a_shutdown_leaves_the_videos_not_run(self):
        exported = self.make_exported_job("tap12")
        preview_id = self.preview([exported])["preview_id"]
        self.center._stopping.set()
        status, result = self.clean([exported], preview_id)
        self.assertEqual(status, 200)
        self.assertEqual((result["results"][0]["status"], result["results"][0]["message"], result["recycled_count"]),
                         ("NOT_RUN", STOPPING_MESSAGE, 0))
        self.assertEqual(self.recycler.calls, [])
        self.assertIsNone(self.store.latest_source_cleanup(exported))


class LockOrderTests(CleanupHttpFixture):
    def test_no_deadlock_with_finalize_review_decisions_and_status(self):
        cleaned = self.make_exported_job("tap12")
        to_export = self.make_job("tap20", state="READY_TO_EXPORT", items=[item("a", decided_at=iso(NOW))])
        # The fields the real queue writer needs (record_review_decision runs for real).
        undecided = dict(
            item("a", decision=None), preview_images=[], priority="high", labels=["nsfw"], max_score=0.999,
        )
        to_decide = self.make_job("tap21", state="WAITING_REVIEW", status="REVIEW_REQUIRED", items=[undecided])
        preview_id = self.preview([cleaned])["preview_id"]
        self.serve()
        entered, release = threading.Event(), threading.Event()
        original = self.store.add_source_cleanup

        def blocking(**kwargs):
            # Inside REVIEW_QUEUE_IO and job_action_lock (the cleanup's locked section).
            entered.set()
            release.wait(10)
            return original(**kwargs)

        outcome = {}
        with patch.object(self.store, "add_source_cleanup", side_effect=blocking):
            cleaner = threading.Thread(
                target=lambda: outcome.update(cleanup=self.clean([cleaned], preview_id)),
            )
            cleaner.start()
            self.assertTrue(entered.wait(10))
            others = [
                threading.Thread(target=lambda: outcome.update(finalize=self.finalize(to_export))),
                threading.Thread(target=lambda: outcome.update(decision=self.post(
                    f"/api/jobs/{to_decide}/review/decision", body={"id": "a", "decision": "KEEP"},
                ))),
                threading.Thread(target=lambda: outcome.update(status=self.center.status())),
            ]
            for thread in others:
                thread.start()
            time.sleep(0.3)
            # All of them wait behind the locked section (the one lock order) ...
            self.assertEqual(set(outcome), set())
            release.set()
            released = time.monotonic()
            for thread in [cleaner, *others]:
                thread.join(10)
            self.assertLessEqual(time.monotonic() - released, 10)
        self.assertFalse(any(thread.is_alive() for thread in [cleaner, *others]))
        # ... and every one of them finishes once it is released.
        status, result = outcome["cleanup"]
        self.assertEqual((status, result["recycled_count"]), (200, 1), result)
        self.assertEqual(outcome["finalize"]["status"], "QUEUED")
        status, decided = outcome["decision"]
        self.assertEqual((status, decided["status"]), (200, "READY_FOR_EDIT_PLAN"), decided)
        self.assertEqual(self.store.get_job(to_decide)["state"], "READY_TO_EXPORT")
        self.assertEqual(self.store.get_job(to_export)["state"], "QUEUED")
        self.assertIn(cleaned, {job["id"] for job in outcome["status"]["jobs"]})


class StatusFieldTests(CleanupHttpFixture):
    def test_status_hints_parse_each_queue_once_and_never_hash_or_query_the_bin(self):
        exported = self.make_exported_job("tap12")
        skipped = self.make_skipped_job("tap60")
        ready = self.make_job("tap5", state="READY_TO_EXPORT", items=[item("a", decided_at=iso(NOW))])
        hashed = AssertionError("status never hashes")
        with patch.object(source_cleanup, "sha256_file", side_effect=hashed), \
                patch("biliflow.job_store.sha256_file", side_effect=hashed), \
                patch.object(source_cleanup, "_parse_queue_facts",
                             wraps=source_cleanup._parse_queue_facts) as parse:
            first = self.center.status()
            second = self.center.status()
            # One parse per finished job (exported and skipped); none for the others.
            self.assertEqual(parse.call_count, 2)
        self.assertEqual(first["jobs"], second["jobs"])
        self.assertFalse(first["source_cleanup_running"])
        jobs = {job["id"]: job for job in first["jobs"]}
        output = self.output_of(exported)
        manifest = self.manifest_of(exported)
        self.assertEqual(jobs[exported]["cleanup"], {
            "eligible": True, "kind": "EXPORTED", "reason": None,
            "size_bytes": self.store.get_job(exported)["source_size_bytes"], "output_name": output.name,
            "output_bytes": manifest["output"]["bytes"], "exported_at": manifest["created_at"],
            "skipped_at": None,
        })
        self.assertEqual(
            (jobs[skipped]["cleanup"]["eligible"], jobs[skipped]["cleanup"]["kind"],
             jobs[skipped]["cleanup"]["skipped_at"]),
            (True, "SKIPPED", self.store.setting(f"skip:{skipped}")["skipped_at"]),
        )
        self.assertIsNone(jobs[ready]["cleanup"])
        for job in jobs.values():
            self.assertIsNone(job["source_cleanup"])
            self.assertFalse(job["source_cleaned"])
        self.assertEqual((self.bin.calls, self.recycler.calls), ([], []))

    def test_status_rows_follow_pending_failed_and_recycled_cleanups(self):
        jobs = {state: self.make_exported_job(f"tap-{state.lower()}") for state in ("PENDING", "FAILED", "RECYCLED")}
        for state, job_id in jobs.items():
            source = self.source_of(job_id)
            row_id = self.store.add_source_cleanup(
                job_id=job_id, kind="EXPORTED", source_path=str(source.resolve()),
                source_sha256=self.store.get_job(job_id)["source_sha256"],
                size_bytes=self.store.get_job(job_id)["source_size_bytes"], mtime_ns=1,
            )
            if state != "PENDING":
                self.store.finish_source_cleanup(row_id, state=state, verified=True,
                                                 error="Lỗi" if state == "FAILED" else None)
        cards = {job["id"]: job for job in self.center.status()["jobs"]}
        for state, job_id in jobs.items():
            with self.subTest(state=state):
                card = cards[job_id]
                self.assertEqual(card["source_cleanup"]["state"], state)
                self.assertEqual(card["source_cleaned"], state in ("PENDING", "RECYCLED"))
        self.assertEqual(cards[jobs["PENDING"]]["cleanup"]["reason"], "Đang chuyển video gốc này vào Thùng rác")
        self.assertEqual(cards[jobs["RECYCLED"]]["cleanup"]["reason"], REASON_RECYCLED)
        # A failed cleanup locks nothing: the video can be cleaned again.
        self.assertTrue(cards[jobs["FAILED"]]["cleanup"]["eligible"])
        self.assertEqual(cards[jobs["FAILED"]]["source_cleanup"]["error"], "Lỗi")

    def test_a_failing_hint_marks_only_its_card(self):
        broken = self.make_exported_job("tap12")
        skipped = self.make_skipped_job("tap60")
        ready = self.make_job("tap5", state="READY_TO_EXPORT", items=[item("a", decided_at=iso(NOW))])
        real_assess = source_cleanup.assess_job

        def assess(root, store, scheduler, job, **kwargs):
            if int(job["id"]) == broken:
                raise PermissionError("Access is denied")
            return real_assess(root, store, scheduler, job, **kwargs)

        with patch.object(source_cleanup, "assess_job", side_effect=assess):
            status, value = self.get("/api/status")
        self.assertEqual(status, 200)
        jobs = {job["id"]: job for job in value["jobs"]}
        self.assertEqual(jobs[broken]["cleanup"], {
            "eligible": False, "kind": None, "reason": "Không kiểm tra được: Access is denied",
            "size_bytes": None, "output_name": None, "output_bytes": None,
            "exported_at": None, "skipped_at": None,
        })
        self.assertTrue(jobs[skipped]["cleanup"]["eligible"])
        self.assertIsNone(jobs[ready]["cleanup"])


class StartupAndShutdownTests(unittest.TestCase):
    def test_startup_settles_pending_rows_even_without_the_import(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "input").mkdir()
            (root / "state").mkdir()
            store = JobStore(root / "state" / "control-center.sqlite3")
            rows = {}
            for name in ("present", "gone"):
                source = root / "input" / f"{name}.mp4"
                source.write_bytes(name.encode() * 512)
                info = source.stat()
                job = store.upsert_job(
                    job_key=name, source_path=source,
                    source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    source_size_bytes=info.st_size, source_mtime_ns=info.st_mtime_ns,
                    content_style="animation", state="COMPLETED",
                )
                rows[name] = (int(job["id"]), store.add_source_cleanup(
                    job_id=int(job["id"]), kind="EXPORTED", source_path=str(source.resolve()),
                    source_sha256=job["source_sha256"], size_bytes=info.st_size,
                    mtime_ns=info.st_mtime_ns,
                ), source.resolve(), info.st_size)
            # The shell had moved this one before the crash.
            gone_source = rows["gone"][2]
            bin_temp = TemporaryDirectory()
            self.addCleanup(bin_temp.cleanup)
            os.replace(gone_source, Path(bin_temp.name) / "$RABCDEF.mp4")
            store.close()
            finder_calls = []

            def finder(volume_root, original, size, *, since):
                finder_calls.append((volume_root, original, size, since))
                return "E:\\$Recycle.Bin\\S-1\\$IABCDEF.mp4"

            with patch.object(recycle_bin, "find_recycle_record", side_effect=finder), \
                    patch("biliflow.control_center.import_existing_project") as imported:
                center = ControlCenter(root, port=0, import_existing=False)
            try:
                imported.assert_not_called()
                self.assertEqual(sorted(center.cleanup_reconciled), sorted([rows["present"][1], rows["gone"][1]]))
                present_job = rows["present"][0]
                row = center.store.latest_source_cleanup(present_job)
                self.assertEqual((row["state"], row["error"]), ("FAILED", INTERRUPTED_MESSAGE))
                self.assertFalse(center.store.source_cleaned(present_job))
                self.assertTrue(rows["present"][2].is_file())
                event = [e for e in center.store.events(present_job) if e["event_type"] == "SOURCE_CLEANUP_FAILED"]
                self.assertEqual((event[0]["level"], event[0]["payload"]["stage"]), ("WARNING", "reconcile"))
                gone_job = rows["gone"][0]
                row = center.store.latest_source_cleanup(gone_job)
                self.assertEqual((row["state"], row["verified"], row["recycle_record"]),
                                 ("RECYCLED", True, "E:\\$Recycle.Bin\\S-1\\$IABCDEF.mp4"))
                self.assertTrue(center.store.source_cleaned(gone_job))
                # Only the moved file was looked up in the bin, on its own volume.
                self.assertEqual([item[:3] for item in finder_calls],
                                 [(gone_source.anchor, str(gone_source), rows["gone"][3])])
                # The only place the real functions are bound (never called here).
                self.assertIs(center.recycler, recycle_bin.send_to_recycle_bin)
                self.assertIs(center.bin_info, recycle_bin.volume_bin_info)
            finally:
                center.store.close()
                center.lock.close()

    def test_the_class_defaults_refuse_and_never_reach_the_shell(self):
        stub = ControlCenter.__new__(ControlCenter)
        for recycler in (ControlCenter.recycler, stub.recycler):
            with self.assertRaises(RuntimeError) as caught:
                recycler(Path("E:\\input\\x.mp4"), allowed_root=Path("E:\\input"), expected_size=1,
                         timeout=1.0, on_late_result=None)
            self.assertEqual(str(caught.exception), UNCONFIGURED_MESSAGE)
        for bin_info in (ControlCenter.bin_info, stub.bin_info):
            with self.assertRaises(RecycleRefused) as caught:
                bin_info(Path("E:\\input"))
            self.assertEqual(str(caught.exception), UNCONFIGURED_MESSAGE)
        self.assertIsNot(ControlCenter.recycler, recycle_bin.send_to_recycle_bin)
        self.assertIsNot(ControlCenter.bin_info, recycle_bin.volume_bin_info)

    def stub(self, root):
        center = ControlCenter.__new__(ControlCenter)
        center.root = root
        center.host, center.port = "127.0.0.1", 0
        center._stopping = threading.Event()
        center.server = None
        center.watcher = Mock()
        center.scheduler = Mock()
        center.stop_ai_audits = Mock()
        center.stop_ai_login = Mock()
        manager = Mock()
        center.store, center.lock = manager.store, manager.lock
        return center, manager

    def test_stop_and_serve_wait_for_a_running_cleanup_before_closing_the_store(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            center, manager = self.stub(root)
            with patch.object(source_cleanup, "wait_idle",
                              side_effect=lambda timeout: manager.wait_idle(timeout=timeout)):
                center.stop()
            self.assertEqual(manager.mock_calls,
                             [call.wait_idle(timeout=90.0), call.store.close(), call.lock.close()])
            # serve() failing to start (port taken) runs the same shutdown in its finally.
            center, manager = self.stub(root)
            with patch.object(source_cleanup, "wait_idle",
                              side_effect=lambda timeout: manager.wait_idle(timeout=timeout)), \
                    patch("biliflow.control_center.ThreadingHTTPServer", side_effect=OSError("port in use")):
                with self.assertRaises(OSError):
                    center.serve()
            self.assertEqual(manager.mock_calls,
                             [call.wait_idle(timeout=90.0), call.store.close(), call.lock.close()])

    def test_serve_outlives_an_api_shutdown_until_stop_has_closed_the_store(self):
        # Fix pass: /api/shutdown runs stop() on a daemon thread; its
        # server.shutdown() ends serve_forever in the main thread. serve() must
        # not return (and let the interpreter exit, killing the stop and recycle
        # threads) before stop() has waited for the cleanup and closed the store.
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "state").mkdir()
            center, manager = self.stub(root)
            center.token = "test-token"
            center._stopped = threading.Event()
            entered, release = threading.Event(), threading.Event()

            def slow_wait_idle(timeout):
                entered.set()
                release.wait(10)
                manager.wait_idle(timeout=timeout)

            def run_serve():
                center.serve()
                manager.serve_returned()

            with patch.object(source_cleanup, "wait_idle", side_effect=slow_wait_idle), \
                    patch("builtins.print"):
                serving = threading.Thread(target=run_serve, daemon=True)
                serving.start()
                deadline = time.monotonic() + 10
                while center.server is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertIsNotNone(center.server)
                # What the /api/shutdown route does.
                threading.Thread(target=center.stop, kwargs={"immediate": False}, daemon=True).start()
                self.assertTrue(entered.wait(10))
                # stop() is past server.shutdown(): serve_forever has returned.
                serving.join(0.5)
                self.assertTrue(serving.is_alive(), "serve() returned before stop() finished")
                release.set()
                serving.join(10)
            self.assertFalse(serving.is_alive())
            self.assertEqual(manager.mock_calls, [
                call.wait_idle(timeout=90.0), call.store.close(), call.lock.close(), call.serve_returned(),
            ])
            self.assertFalse((root / "state" / "control-center.json").exists())


class SharedNamesTests(unittest.TestCase):
    def test_route_texts_match_the_cleanup_module(self):
        self.assertEqual(source_cleanup.JOB_IDS_MESSAGE, JOB_IDS_MESSAGE)
        self.assertEqual(source_cleanup.PREVIEW_ID_MESSAGE, PREVIEW_ID_MESSAGE)
        self.assertEqual(source_cleanup.BUSY_MESSAGE, BUSY_MESSAGE)
        self.assertEqual(cc.UNCONFIGURED_RECYCLE_BIN_MESSAGE, UNCONFIGURED_MESSAGE)
        # Not a ValueError: the route answers 409, never the generic 400.
        self.assertFalse(issubclass(source_cleanup.CleanupConflict, ValueError))


if __name__ == "__main__":
    unittest.main()
