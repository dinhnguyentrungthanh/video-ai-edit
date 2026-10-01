import hashlib
import hmac
import http.client
import json
import os
import subprocess
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from biliflow.control_center import (
    ControlCenter,
    _dashboard_html,
    _handler_class,
    _merge_visual_audit_batches,
)
from biliflow.job_store import JobStore
from biliflow.final_renderer import render_progress_path
from biliflow.review_evidence import ReviewFrameCache
from biliflow.review_workflow import record_review_decision


class ControlCenterVisualAuditTests(unittest.TestCase):
    def test_dashboard_preserves_metadata_drafts_across_refreshes(self):
        page = _dashboard_html()
        self.assertIn(
            "const detectorDrafts={};const metadataDrafts={};const rerunPanelDrafts={};",
            page,
        )
        self.assertIn("function metadataSelection(j)", page)
        self.assertIn("function captureMetadataDraft(id)", page)
        self.assertIn('onchange="captureMetadataDraft(${id})"', page)
        self.assertIn("content_style:metadata.content_style", page)
        self.assertIn("delete metadataDrafts[id]", page)

    def test_dashboard_preserves_open_rerun_panel_across_refreshes(self):
        page = _dashboard_html()
        self.assertIn("function captureRerunPanelDrafts()", page)
        self.assertIn("captureRerunPanelDrafts();const jobs=", page)
        self.assertIn('data-job-id="${id}" ${rerunPanelDrafts[id]?\'open\':\'\'}', page)
        self.assertIn("delete rerunPanelDrafts[id]", page)

    def test_dashboard_groups_jobs_and_shows_export_lifecycle(self):
        page = _dashboard_html()
        self.assertIn("function jobBucket(j)", page)
        self.assertIn("Đang chờ xử lý", page)
        self.assertIn("Đang chạy", page)
        self.assertIn("Hoàn tất", page)
        self.assertIn("function exportStatus(j)", page)
        self.assertIn("Đã xuất video", page)
        self.assertIn("Đang xuất video", page)
        self.assertIn("Chờ xuất video", page)
        self.assertIn('class="rerun-panel"', page)
        self.assertIn("function runPhase(j)", page)
        self.assertIn("Đang phân tích để duyệt", page)
        self.assertIn("function shortDuration(seconds)", page)
        self.assertIn("function completedAt(value)", page)
        self.assertIn("100% · Đã xuất video", page)
        self.assertIn("Hoàn thành lúc", page)
        self.assertIn('class="mini-progress"', page)

    def test_render_progress_uses_cut_adjusted_output_duration(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("output", "temp", "work"):
                (root / name).mkdir(parents=True, exist_ok=True)
            store = JobStore(root / "state" / "control-center.sqlite3")
            job = store.upsert_job(
                job_key="movie", source_path=root / "movie.mp4",
                source_sha256="abc", source_size_bytes=1,
                source_mtime_ns=1, duration_seconds=100,
                state="RENDERING",
            )
            plan = root / "work" / "plan.json"
            plan.write_text(json.dumps({
                "source": {"duration_seconds": 100},
                "approved_operations": [
                    {"type": "cut", "start_seconds": 10, "end_seconds": 20},
                ],
            }), encoding="utf-8")
            output = root / "output" / "movie.mp4"
            store.set_setting(f"render:{job['id']}", {
                "plan": "work/plan.json", "output": "output/movie.mp4",
            })
            progress = render_progress_path(root, output)
            progress.parent.mkdir(parents=True)
            progress.write_text(
                "out_time_us=45000000\nspeed=1.0x\nprogress=continue\n",
                encoding="utf-8",
            )
            center = ControlCenter.__new__(ControlCenter)
            center.root = root
            center.store = store
            try:
                value = center.render_progress_summary(store.get_job(job["id"]))
                self.assertEqual(value["percent"], 50.0)
                self.assertEqual(value["expected_duration_seconds"], 90.0)
            finally:
                store.close()

    def test_finalize_saves_per_video_unlimited_size_policy(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            queue_path = root / "reports" / "job" / "review-queue.json"
            queue_path.parent.mkdir(parents=True)
            (root / "work").mkdir()
            (root / "output").mkdir()
            queue_path.write_text(json.dumps({
                "status": "READY_FOR_EDIT_PLAN",
                "source": {"path": str(root / "movie.mp4"), "sha256": "abc"},
                "items": [], "counts": {"total": 0, "pending": 0},
            }), encoding="utf-8")
            store = JobStore(root / "state" / "control-center.sqlite3")
            job = store.upsert_job(
                job_key="movie", source_path=root / "movie.mp4",
                source_sha256="abc", source_size_bytes=1,
                source_mtime_ns=1, state="READY_TO_EXPORT",
            )
            store.update_job(
                job["id"], active_queue_path="reports/job/review-queue.json",
                active_revision=1,
            )
            center = ControlCenter.__new__(ControlCenter)
            center.root = root
            center.store = store
            center.scheduler = Mock()
            plan_path = root / "work" / "plan.json"
            output_path = root / "output" / "movie.mp4"
            try:
                with (
                    patch(
                        "biliflow.control_center.review_export_paths",
                        return_value=(plan_path, output_path, root / "work" / "job.json"),
                    ),
                    patch(
                        "biliflow.control_center.build_edit_plan",
                        return_value={"status": "READY_FOR_PREVIEW"},
                    ),
                    patch("biliflow.control_center.authorize_final_from_resolved_review"),
                ):
                    result = center.finalize(job["id"], size_mode="unlimited")
                saved = json.loads(queue_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["export_size_policy"]["mode"], "unlimited")
                self.assertIsNone(saved["export_size_policy"]["maximum_output_bytes"])
                self.assertEqual(result["export_size_policy"]["mode"], "unlimited")
                kwargs = center.scheduler.queue_render.call_args.kwargs
                self.assertIsNone(kwargs["max_output_bytes"])
                self.assertIsNone(kwargs["target_output_bytes"])
            finally:
                store.close()

    def test_audit_summary_is_scoped_to_active_queue_revision(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reports" / "rev-1").mkdir(parents=True)
            (root / "reports" / "rev-2").mkdir(parents=True)
            store = JobStore(root / "state" / "control-center.sqlite3")
            job = store.upsert_job(
                job_key="movie", source_path=root / "movie.mp4",
                source_sha256="abc", source_size_bytes=1,
                source_mtime_ns=1, state="WAITING_REVIEW",
            )
            job_id = int(job["id"])
            rev_1 = store.add_revision(
                job_id, "reports/rev-1/review-queue.json", "REVIEW_REQUIRED", None,
            )
            store.activate_revision(job_id, rev_1)
            old_audit = root / "reports" / "rev-1" / "ai-audit.json"
            old_audit.write_text(json.dumps({
                "result": "WARN", "summary": "old", "created_at": "old",
            }), encoding="utf-8")
            store.add_artifact(
                job_id, stage_name="ai_audit", kind="ai_audit",
                path="reports/rev-1/ai-audit.json",
            )
            store.add_event(
                job_id, "AI_AUDIT_COMPLETED", "old audit",
                payload={"queue_path": "reports/rev-1/review-queue.json"},
            )

            rev_2 = store.add_revision(
                job_id, "reports/rev-2/review-queue.json", "REVIEW_REQUIRED", None,
            )
            store.activate_revision(job_id, rev_2)
            center = ControlCenter.__new__(ControlCenter)
            center.root = root
            center.store = store
            center._audit_lock = threading.Lock()
            center._audit_jobs = {}

            self.assertIsNone(center.ai_audit_summary(job_id))

            new_audit = root / "reports" / "rev-2" / "ai-audit.json"
            new_audit.write_text(json.dumps({
                "result": "PASS", "summary": "new", "created_at": "new",
            }), encoding="utf-8")
            store.add_artifact(
                job_id, stage_name="ai_audit", kind="ai_audit",
                path="reports/rev-2/ai-audit.json",
            )
            self.assertEqual(
                center.ai_audit_summary(job_id),
                {
                    "state": "COMPLETED", "result": "PASS",
                    "summary": "new", "updated_at": "new",
                },
            )
            store.close()

    def test_batches_merge_without_losing_assessments(self):
        def batch(item_id, classification, confidence):
            return {
                "result": "PASS",
                "summary": "ok",
                "findings": [],
                "recommended_actions": [],
                "visual_assessments": [{
                    "item_id": item_id,
                    "classification": classification,
                    "suggested_decision": (
                        "KEEP" if classification == "film_content" else "BLUR"
                    ),
                    "confidence": confidence,
                    "region_assessment": "TIGHT",
                    "reasoning": "test",
                }],
                "visual_audit": {
                    "image_count": 1, "batch_limit": 12,
                    "requires_human_approval": True,
                },
                "_thread_id": f"thread-{item_id}",
            }

        merged = _merge_visual_audit_batches(
            [
                batch("one", "uncertain", 0.99),
                batch("one", "film_content", 0.91),
                batch("two", "external_brand", 0.98),
            ],
            expected_assessment_count=2,
        )
        self.assertEqual(merged["result"], "PASS")
        self.assertEqual(len(merged["visual_assessments"]), 2)
        values = {item["item_id"]: item for item in merged["visual_assessments"]}
        self.assertEqual(values["one"]["classification"], "film_content")
        self.assertEqual(merged["visual_audit"]["batch_count"], 3)
        self.assertEqual(merged["visual_audit"]["completed_assessment_count"], 2)

    def test_missing_assessment_turns_pass_into_warn(self):
        merged = _merge_visual_audit_batches(
            [{
                "result": "PASS", "summary": "ok", "findings": [],
                "recommended_actions": [], "visual_assessments": [],
                "visual_audit": {"image_count": 1, "batch_limit": 12},
                "_thread_id": "thread",
            }],
            expected_assessment_count=1,
        )
        self.assertEqual(merged["result"], "WARN")
        self.assertTrue(any("omitted" in value for value in merged["findings"]))


MEDIA_SHA = "f43cf94aadffb8c127c18fb23a51c58de2bdafcb2f05b1e91bd84be726fb19e9"
MEDIA_ITEM = "review-935e63a78271"
MEDIA_THUMBNAIL = "thumbnails/frame-00001889-944.500s.jpg"


class ControlCenterHttpTests(unittest.TestCase):
    """Real HTTP handler on port 0 with a stub center (no scheduler, watcher or jobs)."""

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.store = JobStore(self.root / "state" / "control-center.sqlite3")
        self.job_id, self.source = self.add_job("troy", "movie.mp4", MEDIA_SHA)
        center = ControlCenter.__new__(ControlCenter)
        center.root = self.root
        center.host = "127.0.0.1"
        center.token = "test-token"
        center.store = self.store
        center.frame_cache = ReviewFrameCache(self.root, self.root / "missing-ffmpeg.exe")
        center._stopping = threading.Event()
        self.center = center
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_class(center))
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.store.close()
        self.temp.cleanup()

    def add_job(self, name, source_name, sha):
        job_dir = f"reports/jobs/{name}"
        report = f"{job_dir}/adult/scan.json"
        source = self.root / "input" / source_name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(bytes(range(256)) * 16)
        (self.root / job_dir / "adult" / "thumbnails").mkdir(parents=True)
        (self.root / job_dir / "adult" / MEDIA_THUMBNAIL).write_bytes(b"\xff\xd8thumb\xff\xd9")
        (self.root / report).write_text(json.dumps({
            "scan_type": "nsfw", "sample_fps": 2.0, "threshold": 0.95,
            "intervals": [{
                "start_seconds": 928.5, "end_seconds": 950.5, "max_score": 0.999486,
                "strongest_frame": MEDIA_THUMBNAIL, "sample_count": 16,
                "sequence_context": {
                    "applied": True, "detector_start_seconds": 937.0,
                    "detector_end_seconds": 950.5, "supporting_sample_count": 2,
                    "context_threshold": 0.7, "maximum_extension_seconds": 8.0,
                },
            }],
        }), encoding="utf-8")
        (self.root / job_dir / "review-queue.json").write_text(json.dumps({
            "status": "REVIEW_REQUIRED",
            "source": {"path": str(source.resolve()), "sha256": sha, "duration_seconds": 11762.72},
            "reports": [report],
            "items": [{
                "id": MEDIA_ITEM, "category": "adult",
                "start_seconds": 928.5, "end_seconds": 950.5,
                "preview_images": [f"{job_dir}/adult/{MEDIA_THUMBNAIL}"],
                "source_candidate_refs": [f"{report}#interval:0"],
                "detected_intervals": [{"start_seconds": 928.5, "end_seconds": 950.5}],
            }],
            "advisory_items": [],
        }), encoding="utf-8")
        stat = source.stat()
        job = self.store.upsert_job(
            job_key=name, source_path=source, source_sha256=sha,
            source_size_bytes=stat.st_size, source_mtime_ns=stat.st_mtime_ns,
            state="WAITING_REVIEW",
        )
        self.store.update_job(
            job["id"], active_queue_path=f"{job_dir}/review-queue.json", active_revision=1,
        )
        return int(job["id"]), source

    def key(self, job_id):
        return hmac.new(
            b"test-token", f"review-media:{job_id}".encode(), hashlib.sha256,
        ).hexdigest()

    def request(self, path, *, host=None, method="GET", headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            sent = {"Host": host or f"127.0.0.1:{self.port}", **(headers or {})}
            connection.request(method, path, body=body, headers=sent)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_foreign_host_header_is_refused_on_every_route(self):
        routes = [
            "/", "/healthz", "/api/session", f"/review/{self.job_id}",
            f"/media/reports/jobs/troy/adult/{MEDIA_THUMBNAIL}",
            f"/api/jobs/{self.job_id}/review/session",
            f"/api/jobs/{self.job_id}/review/evidence?item={MEDIA_ITEM}",
            f"/api/jobs/{self.job_id}/review/video?k={self.key(self.job_id)}",
        ]
        for route in routes:
            for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1.evil.example",
                         f"localhost:{self.port}x", "", "[::2]"):
                with self.subTest(route=route, host=host):
                    status, _, body = self.request(route, host=host or " ")
                    self.assertEqual(status, 403)
                    self.assertNotIn(b"test-token", body)
                    self.assertNotIn(b"thumb", body)
            for host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}", "localhost",
                         f"[::1]:{self.port}", "LOCALHOST"):
                with self.subTest(route=route, host=host):
                    self.assertIn(self.request(route, host=host)[0], {200})
        status, _, body = self.request(
            "/api/scheduler", host="evil.example", method="POST",
            headers={"X-BiliFlow-Token": "test-token", "Content-Type": "application/json"},
            body=b'{"paused": true}',
        )
        self.assertEqual(status, 403)
        self.assertIsNone(self.store.setting("scheduler_paused"))
        status, _, _ = self.request(
            "/api/scheduler", method="POST", headers={"X-BiliFlow-Token": "wrong"}, body=b"{}",
        )
        self.assertEqual(status, 403)

    def test_review_page_and_media_still_work_on_localhost(self):
        status, headers, body = self.request(f"/review/{self.job_id}")
        self.assertEqual(status, 200)
        self.assertIn(b"test-token", body)
        status, headers, body = self.request(f"/media/reports/jobs/troy/adult/{MEDIA_THUMBNAIL}")
        self.assertEqual((status, body), (200, b"\xff\xd8thumb\xff\xd9"))

    def test_review_page_api_constant_is_scoped_to_the_job(self):
        status, _, body = self.request(f"/review/{self.job_id}")
        page = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn(f"const API='/api/jobs/{self.job_id}/review/';", page)
        self.assertEqual(page.count("'/api/"), 1)
        # Every route the page builds from API exists for this job.
        base = f"/api/jobs/{self.job_id}/review"
        for route in ("queue", "session", "resources", "export", f"evidence?item={MEDIA_ITEM}"):
            with self.subTest(route=route):
                self.assertEqual(self.request(f"{base}/{route}")[0], 200)

    def test_session_contains_a_per_job_media_key(self):
        status, _, body = self.request(f"/api/jobs/{self.job_id}/review/session")
        value = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(value["token"], "test-token")
        self.assertEqual(value["media_key"], self.key(self.job_id))
        other = json.loads(self.request("/api/jobs/77/review/session")[2])
        self.assertNotEqual(other["media_key"], value["media_key"])

    def test_evidence_route_is_read_only_json(self):
        queue_path = self.root / "reports/jobs/troy/review-queue.json"
        before = queue_path.read_bytes()
        status, headers, body = self.request(
            f"/api/jobs/{self.job_id}/review/evidence?item={MEDIA_ITEM}",
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        value = json.loads(body)
        self.assertIn({"t": 944.5, "kind": "strongest", "score": 0.999486}, value["frames"])
        self.assertTrue(any(937.5 <= frame["t"] <= 941.5 for frame in value["frames"]))
        self.assertEqual(value["video"], {"available": True, "mime": "video/mp4", "reason": None})
        self.assertEqual(queue_path.read_bytes(), before)
        self.assertEqual(self.request(f"/api/jobs/{self.job_id}/review/evidence")[0], 400)
        self.assertEqual(
            self.request(f"/api/jobs/{self.job_id}/review/evidence?item=nope")[0], 404,
        )
        self.assertEqual(self.request(f"/api/jobs/999/review/evidence?item={MEDIA_ITEM}")[0], 404)

    def test_video_streams_ranges_from_the_job_source(self):
        data = self.source.read_bytes()
        route = f"/api/jobs/{self.job_id}/review/video?k={self.key(self.job_id)}"
        status, headers, body = self.request(route, headers={"Range": "bytes=10-19"})
        self.assertEqual(status, 206)
        self.assertEqual(body, data[10:20])
        self.assertEqual(headers["Content-Range"], f"bytes 10-19/{len(data)}")
        self.assertEqual(headers["Accept-Ranges"], "bytes")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Content-Type"], "video/mp4")
        status, _, body = self.request(route)
        self.assertEqual((status, body), (200, data))
        status, headers, _ = self.request(route, headers={"Range": f"bytes={len(data)}-"})
        self.assertEqual(status, 416)
        self.assertEqual(headers["Content-Range"], f"bytes */{len(data)}")
        self.assertEqual(self.source.read_bytes(), data)

    def test_media_routes_need_the_jobs_key(self):
        base = f"/api/jobs/{self.job_id}/review"
        for route in (
            f"{base}/video", f"{base}/video?k=", f"{base}/video?k=wrong",
            f"{base}/video?k={self.key(self.job_id + 1)}",
            f"{base}/frame?item={MEDIA_ITEM}&t=939.5",
            f"{base}/frame?item={MEDIA_ITEM}&t=939.5&k=wrong",
            f"{base}/video?k=%C3%A9",
        ):
            with self.subTest(route=route):
                status, _, body = self.request(route)
                self.assertEqual(status, 403)
                self.assertNotIn(b"\x00\x01\x02", body)

    def test_unknown_job_is_404(self):
        key = self.key(999)
        self.assertEqual(self.request(f"/api/jobs/999/review/video?k={key}")[0], 404)
        self.assertEqual(
            self.request(f"/api/jobs/999/review/frame?item={MEDIA_ITEM}&t=939.5&k={key}")[0], 404,
        )

    def test_changed_source_is_409(self):
        route = f"/api/jobs/{self.job_id}/review/video?k={self.key(self.job_id)}"
        stat = self.source.stat()
        os.utime(self.source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
        self.assertEqual(self.request(route)[0], 409)
        evidence = json.loads(self.request(
            f"/api/jobs/{self.job_id}/review/evidence?item={MEDIA_ITEM}",
        )[2])
        self.assertEqual(evidence["video"]["reason"], "source_changed")
        self.assertFalse(evidence["video"]["available"])
        os.utime(self.source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(self.request(route)[0], 200)
        with self.source.open("ab") as handle:
            handle.write(b"!")
        os.utime(self.source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(self.request(route)[0], 409)
        self.assertEqual(self.request(
            f"/api/jobs/{self.job_id}/review/frame?item={MEDIA_ITEM}&t=939.5&k={self.key(self.job_id)}",
        )[0], 409)

    def test_queue_for_another_source_is_409(self):
        queue_path = self.root / "reports/jobs/troy/review-queue.json"
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        queue["source"]["sha256"] = "0" * 64
        queue_path.write_text(json.dumps(queue), encoding="utf-8")
        route = f"/api/jobs/{self.job_id}/review/video?k={self.key(self.job_id)}"
        self.assertEqual(self.request(route)[0], 409)

    def test_unplayable_container_is_415(self):
        job_id, _ = self.add_job("mkv", "movie.mkv", "a" * 64)
        status, _, _ = self.request(f"/api/jobs/{job_id}/review/video?k={self.key(job_id)}")
        self.assertEqual(status, 415)

    def test_frame_route_serves_only_strip_timestamps(self):
        def fake_run(command, **kwargs):
            Path(command[-1]).write_bytes(b"\xff\xd8strip\xff\xd9")
            return subprocess.CompletedProcess(command, 0, b"", b"")

        base = f"/api/jobs/{self.job_id}/review/frame?item={MEDIA_ITEM}&k={self.key(self.job_id)}"
        with patch("biliflow.review_evidence.subprocess.run", side_effect=fake_run) as run:
            status, headers, body = self.request(f"{base}&t=939.5")
            self.assertEqual((status, body), (200, b"\xff\xd8strip\xff\xd9"))
            self.assertEqual(headers["Content-Type"], "image/jpeg")
            self.assertEqual(headers["Cache-Control"], "no-store")
            for bad in ("&t=940", "&t=abc", "&t=", ""):
                with self.subTest(bad=bad):
                    self.assertEqual(self.request(f"{base}{bad}")[0], 400)
            self.assertEqual(run.call_count, 1)
        cached = self.root / "cache/review-frames" / MEDIA_SHA[:16] / "0000939500-w640.jpg"
        self.assertTrue(cached.is_file())
        self.assertEqual(
            self.request(f"/api/jobs/{self.job_id}/review/frame?item=nope&t=939.5&k={self.key(self.job_id)}")[0],
            404,
        )

    def make_decidable(self):
        # Fields the queue HTML writer needs (the media fixture omits them).
        queue_path = self.root / "reports/jobs/troy/review-queue.json"
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        queue["items"][0].update({"priority": "high", "labels": ["nsfw"], "max_score": 0.999})
        queue_path.write_text(json.dumps(queue), encoding="utf-8")

    def post_decision(self, decision):
        return self.request(
            f"/api/jobs/{self.job_id}/review/decision", method="POST",
            headers={"X-BiliFlow-Token": "test-token", "Content-Type": "application/json"},
            body=json.dumps({"id": MEDIA_ITEM, "decision": decision}).encode(),
        )

    def test_queue_reads_wait_for_an_in_flight_review_write(self):
        # Regression (review fix 1): the focus page reads the queue for frames,
        # evidence and polling while a decision swaps the queue file. On
        # Windows an open reader made Path.replace fail (WinError 5) and the
        # decision was lost, so reads and review writes are serialized.
        self.make_decidable()
        base = f"/api/jobs/{self.job_id}/review"
        entered, release = threading.Event(), threading.Event()
        order = []

        def slow_record(**kwargs):
            entered.set()
            release.wait(5)
            order.append("write")
            return record_review_decision(**kwargs)

        with patch("biliflow.control_center.record_review_decision", side_effect=slow_record):
            poster = threading.Thread(target=lambda: order.append(("decision", self.post_decision("CUT")[0])))
            poster.start()
            self.assertTrue(entered.wait(5))
            readers = [
                threading.Thread(target=lambda route=route: order.append((route, self.request(f"{base}/{route}")[0])))
                for route in ("queue", "resources", f"evidence?item={MEDIA_ITEM}",
                              f"frame?item={MEDIA_ITEM}&t=1&k={self.key(self.job_id)}")
            ]
            for thread in readers:
                thread.start()
            time.sleep(0.3)
            self.assertEqual(order, [], "queue reads must wait for the in-flight write")
            release.set()
            for thread in [poster, *readers]:
                thread.join(10)
        self.assertEqual(order[0], "write")
        statuses = dict(entry for entry in order[1:])
        self.assertEqual(statuses["decision"], 200)
        self.assertEqual(statuses["queue"], 200)
        self.assertEqual(statuses["resources"], 200)
        self.assertEqual(statuses[f"evidence?item={MEDIA_ITEM}"], 200)
        # t=1 is not a strip timestamp: the frame route answered after the lock, with 400.
        self.assertEqual(statuses[f"frame?item={MEDIA_ITEM}&t=1&k={self.key(self.job_id)}"], 400)

    def test_rapid_decisions_survive_concurrent_media_and_queue_reads(self):
        # Same race at full speed with the real writer: every decision is saved.
        self.make_decidable()
        base = f"/api/jobs/{self.job_id}/review"
        stop = threading.Event()
        read_errors = []

        def reader(route):
            while not stop.is_set():
                status = self.request(f"{base}/{route}")[0]
                if status != 200:
                    read_errors.append((route, status))

        readers = [
            threading.Thread(target=reader, args=(route,), daemon=True)
            for route in ("queue", f"evidence?item={MEDIA_ITEM}", "queue", f"evidence?item={MEDIA_ITEM}")
        ]
        for thread in readers:
            thread.start()
        failures = []
        try:
            for index in range(40):
                status, _, body = self.post_decision(("KEEP", "CUT")[index % 2])
                if status != 200:
                    failures.append((index, status, body[:200]))
        finally:
            stop.set()
            for thread in readers:
                thread.join(10)
        self.assertEqual(failures, [])
        self.assertEqual(read_errors, [])
        queue = json.loads((self.root / "reports/jobs/troy/review-queue.json").read_text(encoding="utf-8"))
        self.assertEqual(queue["items"][0]["decision"], "CUT")
        self.assertEqual(
            sum(1 for entry in queue["audit_log"] if entry["action"] == "DECIDE"), 40,
        )


if __name__ == "__main__":
    unittest.main()
