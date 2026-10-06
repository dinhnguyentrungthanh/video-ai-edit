"""Xóa video and Dọn video mất gốc through the Control Center (docs/DELETE_FLOW_PLAN.md).

The preview and delete routes over real HTTP on port 0, the 400/403/409
answers, and the status() fields ``protected`` and ``delete`` of each card.

Every Control Center here is a stub built with ``ControlCenter.__new__`` on a
temporary root under ``<install>/temp`` (a real JobStore and JobScheduler, never
the project's state), with a deleter that records each call and then deletes
the temporary file with ``job_purge.delete_input_file`` (which refuses any
other root) and a fake Recycle Bin finder. The module setup also replaces
``recycle_bin._shell_delete`` with a function that fails the test.
"""

import hashlib
import http.client
import json
import threading
import unittest
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from biliflow import brand_memory
from biliflow import control_center as cc
from biliflow import job_delete, job_purge, recycle_bin, source_cleanup
from biliflow.control_center import ControlCenter, _handler_class, _phone_access, _phone_handler_class
from biliflow.job_store import JobStore
from biliflow.review_evidence import ReviewFrameCache
from biliflow.scheduler import JobScheduler
from biliflow.source_cleanup import SOURCE_FILE_LOCK
from tests.test_dashboard_v2_phone_hardening import FAKE_LAN, http as phone_http


TEMP_PARENT = recycle_bin.INSTALL_ROOT / "temp"
NOW = datetime.now(timezone.utc)

# Verbatim texts of the contract (docs/DELETE_FLOW_PLAN.md).
DELETED_CANCELLED = "Đã xóa vĩnh viễn video gốc và xóa video khỏi BiliFlow"
DELETED_LOST = "Đã xóa video khỏi BiliFlow"
JOB_IDS = "Chọn từ 1 đến 50 video mỗi lần dọn."
PREVIEW_ID = "Thiếu mã xem trước; hãy mở lại hộp thoại xóa video."
BUSY = "Đang xóa, lưu trữ hoặc khôi phục video; chờ lượt trước xong rồi thử lại."
CHANGED = "Danh sách đã thay đổi, hãy xem lại."
NOTHING = "Không có video nào xóa được trong danh sách đã chọn."
NOT_DELETABLE = "Chỉ xóa được video đã hủy hoặc video không còn video gốc"
STOPPING = "BiliFlow đang tắt; video này chưa được xóa."
CONFIRM_PERMANENT = (
    "Thiếu xác nhận xóa vĩnh viễn (trang này có thể đã cũ). Tải lại trang, mở lại hộp thoại, "
    "đánh dấu “Tôi hiểu” rồi xóa."
)
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


def tree_digest(path):
    digest = hashlib.sha256()
    for file in sorted(p for p in Path(path).rglob("*") if p.is_file()):
        digest.update(file.relative_to(path).as_posix().encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


class RecordingDeleter:
    """Records every call, then deletes the temporary file with job_purge.delete_input_file."""

    def __init__(self):
        self.calls = []

    def __call__(self, path, *, allowed_root, expected_size):
        self.calls.append({"path": path, "allowed_root": allowed_root, "expected_size": expected_size})
        job_purge.delete_input_file(path, allowed_root=allowed_root, expected_size=expected_size)


class PhoneClient:
    """Mixin: the real phone listener of the stub Control Center, on 127.0.0.1 (never the Wi-Fi address).

    The access code gives the cookie; ``via_phone`` sends it with the session token and the listener's Origin.
    """

    def open_phone(self):
        access = _phone_access(self.center)
        access._lan = lambda: FAKE_LAN  # never the real Wi-Fi
        self.addCleanup(access.disable)
        status = access.enable(lambda value: _phone_handler_class(self.center, value), address=FAKE_LAN,
                               check_address=lambda _a: None, check_port=lambda _p: None, port=0)
        self.phone_port, self.phone_host = status["port"], f"127.0.0.1:{status['port']}"
        answer, headers, _ = phone_http(self.phone_port, "POST", "/phone-login", host=self.phone_host,
                                  headers={"Content-Type": "application/x-www-form-urlencoded"},
                                  body=f"code={status['code']}".encode())
        self.assertEqual(answer, 200)
        self.phone_cookie = headers["set-cookie"][0].split(";", 1)[0]

    def via_phone(self, method, path, body=None, *, cookie=True, token="test-token", origin=None, host=None):
        headers = {"Content-Type": "application/json", "Origin": origin or f"http://{self.phone_host}"}
        if cookie:
            headers["Cookie"] = self.phone_cookie
        if token:
            headers["X-BiliFlow-Token"] = token
        payload = json.dumps(body or {}).encode() if method == "POST" else b""
        status, _, reply = phone_http(self.phone_port, method, path, host=host or self.phone_host, headers=headers,
                                      body=payload)
        return status, json.loads(reply or b"null")


class DeleteHttpFixture(unittest.TestCase):
    """A stub Control Center on a temp root under <install>/temp (never the project's state)."""

    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        temp = TemporaryDirectory(dir=TEMP_PARENT)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        for name in ("input", "reports/jobs", "output", "work", "state", "logs/control-center"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        # The brand and studio-logo memories and the session file of state/: a deletion leaves them.
        for name, payload in (
            ("brand-memory.json", brand_memory._empty_memory()),
            ("studio-logo-memory.json", brand_memory._empty_studio_logo_memory()),
            ("control-center.json", {"schema_version": 1, "port": 0}),
        ):
            (self.root / "state" / name).write_text(json.dumps(payload), encoding="utf-8")
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
        self.deleter = RecordingDeleter()
        self.finder = Mock(return_value=None)
        center.source_deleter = self.deleter
        center.record_finder = self.finder
        # "Xóa video" never needs the Recycle Bin's size or moves anything there.
        center.bin_info = Mock(side_effect=AssertionError("no Recycle Bin size"))
        center.export_recycler = Mock(side_effect=AssertionError("no Recycle Bin move"))
        self.center = center
        cc._SUMMARY_CACHE.clear()
        source_cleanup._CACHE.clear()
        self.addCleanup(source_cleanup._CACHE.clear)
        job_purge.clear_caches()
        self.addCleanup(job_purge.clear_caches)
        for target, kwargs in (
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
    def make_job(self, name, *, state):
        source = self.root / "input" / f"{name}.mp4"
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
        self.store.update_job(
            job["id"], active_queue_path=f"reports/jobs/{name}/review-queue.json", active_revision=1, progress=1.0,
        )
        return int(job["id"])

    def make_lost(self, name, *, state="COMPLETED"):
        job_id = self.make_job(name, state=state)
        self.source_of(job_id).unlink()
        return job_id

    def legacy_unverified_row(self, job_id):
        """The old cleanup moved the source to the bin and found no record of it."""
        job = self.store.get_job(job_id)
        row_id = self.store.add_source_cleanup(
            job_id=job_id, kind="EXPORTED", source_path=str(Path(job["source_path"]).resolve()),
            source_sha256=job["source_sha256"], size_bytes=job["source_size_bytes"], mtime_ns=1,
        )
        self.store.finish_source_cleanup(row_id, state="RECYCLED", verified=False)
        self.source_of(job_id).unlink()

    def write_golden(self, value):
        path = self.root / "annotations" / "golden" / "v1" / "segments.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def source_of(self, job_id):
        return Path(self.store.get_job(job_id)["source_path"])

    def job_exists(self, job_id):
        try:
            self.store.get_job(job_id)
        except KeyError:
            return False
        return True

    def db_dump(self):
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

    def all_files(self):
        return {name: tree_digest(self.root / name) for name in ("input", "reports", "output", "work", "state")}

    def cards(self):
        status, value = self.get("/api/status")
        self.assertEqual(status, 200, value)
        return {job["id"]: job for job in value["jobs"]}

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
            data = response.read()
            if response.getheader("Content-Type", "").startswith("application/json"):
                return response.status, json.loads(data or b"{}")
            return response.status, data
        finally:
            connection.close()

    def get(self, path, *, host=None):
        return self.request("GET", path, host=host)

    def post(self, path, *, token="test-token", host=None, body=None):
        return self.request("POST", path, token=token, host=host, body=body)

    def preview(self, ids):
        status, body = self.get(f"/api/job-delete/preview?ids={','.join(str(x) for x in ids)}")
        self.assertEqual(status, 200, body)
        return body

    def delete(self, ids, preview_id, **extra):
        body = {"job_ids": list(ids), "preview_id": preview_id, "confirm_permanent": True, **extra}
        return self.post("/api/job-delete", body=body)


class PreviewRouteTests(DeleteHttpFixture):
    def test_the_preview_lists_both_kinds_and_is_read_only(self):
        cancelled = self.make_job("tap1", state="CANCELLED")
        lost = self.make_lost("tap2")
        done = self.make_job("tap3", state="COMPLETED")
        dump, files = self.db_dump(), self.all_files()
        hashed = AssertionError("a preview never hashes")
        with patch.object(source_cleanup, "sha256_file", side_effect=hashed), \
                patch("biliflow.job_store.sha256_file", side_effect=hashed):
            preview = self.preview([done, lost, cancelled, 999])
        self.assertEqual(preview, job_delete.preview_delete(
            self.root, self.store, self.center.scheduler, [done, lost, cancelled, 999], finder=self.finder,
        ))
        self.assertEqual([(entry["job_id"], entry["kind"]) for entry in preview["eligible"]],
                         [(cancelled, "CANCELLED"), (lost, "LOST")])
        self.assertEqual(preview["ineligible"], [
            {"job_id": done, "name": "tap3.mp4", "reason": NOT_DELETABLE},
            {"job_id": 999, "name": "", "reason": "Không tìm thấy video #999"},
        ])
        self.assertEqual((self.db_dump(), self.all_files()), (dump, files))
        self.assertEqual(self.deleter.calls, [])

    def test_bad_lists_are_400_and_foreign_hosts_403(self):
        lost = self.make_lost("tap1")
        for query in ("?ids=", "?ids=abc", "?ids=0", "", "?ids=1,,2", "?ids=-1",
                      f"?ids={','.join(str(value) for value in range(1, 52))}"):
            with self.subTest(query=query):
                self.assertEqual(self.get(f"/api/job-delete/preview{query}"), (400, {"error": JOB_IDS}))
        for host in ("evil.example", "127.0.0.1.evil.example", "[::2]"):
            with self.subTest(host=host):
                status, body = self.get(f"/api/job-delete/preview?ids={lost}", host=host)
                self.assertEqual((status, body), (403, {"error": HOST_REFUSAL}))

    def test_the_bin_is_read_only_for_an_unverified_legacy_cleanup(self):
        job_id = self.make_job("tap41", state="COMPLETED")
        self.legacy_unverified_row(job_id)
        self.finder.return_value = "E:\\$Recycle.Bin\\S-1\\$IABCDEF.mp4"
        reason = self.preview([job_id])["ineligible"][0]["reason"]
        self.assertEqual(reason, job_delete.REASON_IN_BIN)
        self.assertEqual(self.finder.call_count, 1)
        # The class default (no bin wired) answers "unreadable", never "gone".
        del self.center.record_finder
        reason = self.preview([job_id])["ineligible"][0]["reason"]
        self.assertEqual(reason, job_delete.REASON_BIN_UNREADABLE)


class DeleteRouteTests(DeleteHttpFixture):
    def test_a_confirmed_delete_removes_the_videos_and_the_dashboard_follows(self):
        cancelled = self.make_job("tap1", state="CANCELLED")
        lost = self.make_lost("tap2")
        kept = self.make_job("tap3", state="COMPLETED")
        video = self.root / "output" / "tap2-reviewed.mp4"
        video.write_bytes(b"OUTPUT")
        video.with_suffix(".mp4.manifest.json").write_text("{}", encoding="utf-8")
        outputs = tree_digest(self.root / "output")
        state_json = {path.name: path.read_bytes() for path in (self.root / "state").glob("*.json")}
        size = self.source_of(cancelled).stat().st_size
        cards = self.cards()
        self.assertEqual(cards[cancelled]["delete"],
                         {"eligible": True, "kind": "CANCELLED", "reason": None, "size_bytes": size})
        self.assertEqual(cards[lost]["delete"], {"eligible": True, "kind": "LOST", "reason": None, "size_bytes": 0})
        self.assertIsNone(cards[kept]["delete"])
        self.assertEqual({card["protected"] for card in cards.values()}, {None})

        preview = self.preview([cancelled, lost])
        status, result = self.delete([cancelled, lost], preview["preview_id"])
        self.assertEqual(status, 200, result)
        self.assertEqual([(entry["job_id"], entry["status"], entry["message"]) for entry in result["results"]], [
            (cancelled, "DELETED", DELETED_CANCELLED), (lost, "DELETED", DELETED_LOST),
        ])
        self.assertEqual((result["deleted_count"], result["deleted_bytes"]), (2, size))
        self.assertEqual(list(self.cards()), [kept])
        self.assertFalse(self.job_exists(cancelled) or self.job_exists(lost))
        self.assertFalse((self.root / "input" / "tap1.mp4").exists())
        self.assertTrue(self.source_of(kept).is_file())
        self.assertEqual(tree_digest(self.root / "output"), outputs)
        self.assertEqual({path.name: path.read_bytes() for path in (self.root / "state").glob("*.json")},
                         state_json)
        self.center.bin_info.assert_not_called()
        self.center.export_recycler.assert_not_called()

    def test_post_needs_the_local_host_the_token_and_the_permanent_confirmation(self):
        lost = self.make_lost("tap1")
        preview_id = self.preview([lost])["preview_id"]
        dump = self.db_dump()
        body = {"job_ids": [lost], "preview_id": preview_id, "confirm_permanent": True}
        for host, token, error in (
            ("evil.example", "test-token", HOST_REFUSAL),
            ("127.0.0.1.evil.example", "test-token", HOST_REFUSAL),
            (None, None, TOKEN_REFUSAL),
            (None, "wrong", TOKEN_REFUSAL),
        ):
            with self.subTest(host=host, token=token):
                self.assertEqual(self.post("/api/job-delete", host=host, token=token, body=body),
                                 (403, {"error": error}))
        for flag in ({}, {"confirm_permanent": False}, {"confirm_permanent": "true"}, {"confirm_permanent": 1}):
            with self.subTest(flag=flag):
                status, payload = self.post("/api/job-delete",
                                            body={"job_ids": [lost], "preview_id": preview_id, **flag})
                self.assertEqual((status, payload), (400, {"error": CONFIRM_PERMANENT}))
        self.assertEqual(self.db_dump(), dump)
        self.assertEqual(self.delete([lost], preview_id)[0], 200)

    def test_bad_bodies_are_400_and_never_reach_the_deleter(self):
        cancelled = self.make_job("tap1", state="CANCELLED")
        done = self.make_job("tap2", state="COMPLETED")
        preview_id = self.preview([cancelled])["preview_id"]
        cases = [
            ({"job_ids": str(cancelled), "preview_id": preview_id}, JOB_IDS),
            ({"job_ids": cancelled, "preview_id": preview_id}, JOB_IDS),
            ({"preview_id": preview_id}, JOB_IDS),
            ({"job_ids": [], "preview_id": preview_id}, JOB_IDS),
            ({"job_ids": [True], "preview_id": preview_id}, JOB_IDS),
            ({"job_ids": [cancelled]}, PREVIEW_ID),
            ({"job_ids": [cancelled], "preview_id": preview_id.upper()}, PREVIEW_ID),
            ({"job_ids": [cancelled], "preview_id": 42}, PREVIEW_ID),
        ]
        for body, error in cases:
            with self.subTest(body=body):
                status, payload = self.post("/api/job-delete", body={**body, "confirm_permanent": True})
                self.assertEqual((status, payload), (400, {"error": error}))
        status, payload = self.delete([done], self.preview([done])["preview_id"])
        self.assertEqual((status, payload), (400, {"error": NOTHING}))
        self.assertEqual(self.deleter.calls, [])
        self.assertTrue(self.source_of(cancelled).is_file())

    def test_conflicts_are_409(self):
        lost = self.make_lost("tap1")
        preview_id = self.preview([lost])["preview_id"]
        self.assertTrue(SOURCE_FILE_LOCK.acquire(blocking=False))
        try:
            self.assertEqual(self.delete([lost], preview_id), (409, {"error": BUSY, "code": "busy"}))
            self.assertTrue(self.get("/api/status")[1]["source_cleanup_running"])
        finally:
            SOURCE_FILE_LOCK.release()
        status, payload = self.delete([lost], "0" * 64)
        self.assertEqual((status, payload["code"], payload["error"]), (409, "preview_changed", CHANGED))
        self.assertEqual(payload["preview"]["preview_id"], preview_id)
        self.assertTrue(self.job_exists(lost))
        self.assertEqual(self.delete([lost], preview_id)[1]["deleted_count"], 1)

    def test_a_shutdown_an_audit_and_a_missing_deleter_change_nothing(self):
        lost = self.make_lost("tap1")
        cancelled = self.make_job("tap2", state="CANCELLED")
        preview_id = self.preview([lost])["preview_id"]
        self.center._stopping.set()
        status, result = self.delete([lost], preview_id)
        self.assertEqual((status, [(entry["status"], entry["message"]) for entry in result["results"]]),
                         (200, [("NOT_RUN", STOPPING)]))
        self.center._stopping.clear()
        self.center._audit_jobs[lost] = "reports/jobs/tap1/review-queue.json"
        self.assertEqual(self.preview([lost])["ineligible"][0]["reason"], source_cleanup.REASON_AUDIT)
        self.center._audit_jobs.clear()
        del self.center.source_deleter  # the class default refuses every file
        status, result = self.delete([cancelled], self.preview([cancelled])["preview_id"])
        self.assertEqual((status, result["results"][0]["status"], result["results"][0]["message"]),
                         (200, "FAILED", cc.UNCONFIGURED_DELETE_MESSAGE))
        self.assertTrue(self.source_of(cancelled).is_file())
        self.assertTrue(self.job_exists(lost) and self.job_exists(cancelled))


class StatusFieldTests(DeleteHttpFixture):
    def test_golden_jobs_are_protected_and_status_never_reads_the_bin(self):
        golden = self.make_job("tap37", state="COMPLETED")
        golden_lost = self.make_lost("tap38")
        unverified = self.make_job("tap41", state="COMPLETED")
        self.legacy_unverified_row(unverified)
        self.write_golden({"sources": {
            "a": {"job_id": golden, "sha256": "f" * 64}, "b": {"job_id": golden_lost, "sha256": "e" * 64},
        }})
        self.center.record_finder = Mock(side_effect=AssertionError("status never reads the Recycle Bin"))
        with patch.object(job_purge, "golden_index", wraps=job_purge.golden_index) as index:
            cards = self.cards()
        self.assertEqual(index.call_count, 1)  # one read of the golden sets per status()
        self.assertEqual(cards[golden]["protected"], job_purge.REASON_GOLDEN)
        self.assertIsNone(cards[golden]["delete"])  # it still has its source and is not cancelled
        self.assertEqual(cards[golden_lost]["protected"], job_purge.REASON_GOLDEN)
        self.assertEqual(cards[golden_lost]["delete"],
                         {"eligible": False, "kind": "LOST", "reason": job_purge.REASON_GOLDEN, "size_bytes": 0})
        self.assertIsNone(cards[unverified]["protected"])
        self.assertEqual(cards[unverified]["delete"],
                         {"eligible": True, "kind": "LOST", "reason": None, "size_bytes": 0})
        self.center.record_finder.assert_not_called()

    def test_a_failing_hint_marks_only_its_card(self):
        broken = self.make_lost("tap1")
        fine = self.make_lost("tap2")
        real = job_delete.delete_hint

        def hint(root, store, scheduler, job, **kwargs):
            if int(job["id"]) == broken:
                raise PermissionError("Access is denied")
            return real(root, store, scheduler, job, **kwargs)

        with patch.object(job_delete, "delete_hint", side_effect=hint):
            cards = self.cards()
        self.assertEqual(cards[broken]["delete"], {
            "eligible": False, "kind": None, "reason": "Không kiểm tra được: Access is denied", "size_bytes": 0,
        })
        self.assertTrue(cards[fine]["delete"]["eligible"])


class AuditStartTests(DeleteHttpFixture):
    def test_an_audit_cannot_start_for_a_job_a_delete_just_removed(self):
        job_id = self.make_lost("tap1")
        entered, release = threading.Event(), threading.Event()
        real_remove = job_purge.remove_job

        def remove(root, store, job):
            entered.set()
            release.wait(10)
            return real_remove(root, store, job)

        outcome = {}
        preview_id = self.preview([job_id])["preview_id"]
        with patch.object(job_purge, "remove_job", side_effect=remove):
            deleting = threading.Thread(target=lambda: outcome.update(
                result=self.center.job_delete_run([job_id], preview_id, True)))
            deleting.start()
            self.assertTrue(entered.wait(10))

            def start():
                try:
                    self.center.start_ai_audit(job_id)
                except KeyError as error:
                    outcome["audit"] = error

            starting = threading.Thread(target=start)
            starting.start()
            starting.join(0.2)
            self.assertTrue(starting.is_alive())  # waits for the locked removal ...
            release.set()
            deleting.join(10)
            starting.join(10)
        self.assertEqual(outcome["result"]["deleted_count"], 1)
        self.assertIsInstance(outcome["audit"], KeyError)  # ... then finds the job gone
        self.assertEqual(self.center._audit_jobs, {})
        self.assertFalse((self.root / "reports" / "jobs" / "tap1").exists())


class PhoneListenerTests(PhoneClient, DeleteHttpFixture):
    """"Hủy" then "Xóa video", and "Dọn video mất gốc", from the phone (the user's choice, 2026-10-06)."""

    def setUp(self):
        super().setUp()
        self.open_phone()

    def confirm(self, ids):
        status, preview = self.via_phone("GET", "/api/job-delete/preview?ids=" + ",".join(str(x) for x in ids))
        self.assertEqual(status, 200, preview)
        return {"job_ids": list(ids), "preview_id": preview["preview_id"], "confirm_permanent": True}

    def test_a_video_waiting_for_setup_is_cancelled_then_deleted_from_the_phone(self):
        waiting = self.make_job("tap69", state="NEEDS_METADATA")
        other = self.make_job("tap70", state="NEEDS_METADATA")
        source = self.source_of(waiting)
        outputs = tree_digest(self.root / "output")
        self.assertEqual(self.via_phone("POST", f"/api/jobs/{waiting}/cancel")[0], 200)
        self.assertEqual(self.store.get_job(waiting)["state"], "CANCELLED")
        self.assertTrue(source.is_file(), "Hủy keeps the source")
        body = self.confirm([waiting])
        status, result = self.via_phone("POST", "/api/job-delete", body)
        self.assertEqual(status, 200, result)
        self.assertEqual([(entry["job_id"], entry["status"], entry["message"]) for entry in result["results"]],
                         [(waiting, "DELETED", DELETED_CANCELLED)])
        self.assertEqual(len(self.deleter.calls), 1)
        self.assertFalse(source.exists())
        self.assertFalse(self.job_exists(waiting))
        self.assertEqual(self.store.get_job(other)["state"], "NEEDS_METADATA")
        self.assertTrue(self.source_of(other).is_file())
        self.assertEqual(tree_digest(self.root / "output"), outputs)

    def test_lost_videos_leave_from_the_phone_and_output_stays(self):
        lost = [self.make_lost("tap1"), self.make_lost("tap2")]
        (self.root / "output" / "tap1-reviewed.mp4").write_bytes(b"OUTPUT")
        outputs = tree_digest(self.root / "output")
        status, result = self.via_phone("POST", "/api/job-delete", self.confirm(lost))
        self.assertEqual(status, 200, result)
        self.assertEqual([(entry["job_id"], entry["status"], entry["message"]) for entry in result["results"]],
                         [(lost[0], "DELETED", DELETED_LOST), (lost[1], "DELETED", DELETED_LOST)])
        self.assertFalse(any(self.job_exists(job_id) for job_id in lost))
        self.assertEqual(self.deleter.calls, [])
        self.assertEqual(tree_digest(self.root / "output"), outputs)

    def test_the_phone_still_needs_the_cookie_the_token_its_origin_and_the_confirmation(self):
        cancelled = self.make_job("tap1", state="CANCELLED")
        body = self.confirm([cancelled])
        dump = self.db_dump()
        self.assertEqual(self.via_phone("GET", f"/api/job-delete/preview?ids={cancelled}", cookie=False)[0], 401)
        self.assertEqual(self.via_phone("POST", "/api/job-delete", body, cookie=False)[0], 401)
        self.assertEqual(self.via_phone("POST", "/api/job-delete", body, token=None), (403, {"error": TOKEN_REFUSAL}))
        self.assertEqual(self.via_phone("POST", "/api/job-delete", body, origin="http://evil.example")[0], 403)
        self.assertEqual(self.via_phone("POST", "/api/job-delete", body, host=f"evil.example:{self.phone_port}"),
                         (403, {"error": HOST_REFUSAL}))
        for flag in ({"confirm_permanent": False}, {"confirm_permanent": "true"}):
            with self.subTest(flag=flag):
                self.assertEqual(self.via_phone("POST", "/api/job-delete", {**body, **flag}),
                                 (400, {"error": CONFIRM_PERMANENT}))
        self.assertEqual(self.db_dump(), dump)
        self.assertEqual(self.deleter.calls, [])
        self.assertTrue(self.source_of(cancelled).is_file())


if __name__ == "__main__":
    unittest.main()
