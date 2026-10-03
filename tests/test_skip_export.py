import hashlib
import http.client
import importlib
import importlib.util
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow import control_center as cc
from biliflow import export_guards
from biliflow.control_center import (
    EXPORT_IN_FLIGHT_MESSAGE,
    REVIEW_EDIT_IN_FLIGHT_MESSAGE,
    SOURCE_MISSING_MESSAGE,
    ControlCenter,
    _handler_class,
    review_summary,
    skip_refusal,
)
from biliflow.export_guards import (
    EXPORT_PATH_TAKEN_MESSAGE,
    QUEUE_NOT_READY_MESSAGE,
    SOURCE_CLEANED_MEDIA_MESSAGE,
    SOURCE_CLEANED_MESSAGE,
    SOURCE_CLEANED_REVIEW_REFUSAL,
)
from biliflow.final_renderer import normalize_output_size_policy
from biliflow.job_import import import_existing_project
from biliflow.job_pipeline import PipelineStage
from biliflow.job_store import JobStore
from biliflow.review_workflow import approved_operations, review_export_paths
from biliflow.scheduler import (
    EXPORT_RETIRE_MESSAGES,
    RESUME_SETTLED_REFUSAL,
    SKIPPED_REFUSAL,
    SKIPPED_STOP_REFUSAL,
    JobScheduler,
)


_SHELL_DELETE_PATCH = None


def setUpModule():
    """Contract R13: no test in this module may reach the real Windows Recycle Bin."""
    global _SHELL_DELETE_PATCH
    if importlib.util.find_spec("biliflow.recycle_bin") is None:  # batch 3 step A5 not landed yet
        return
    recycle_bin = importlib.import_module("biliflow.recycle_bin")

    def refuse(*_args, **_kwargs):
        raise AssertionError("real Recycle Bin call in a test")

    _SHELL_DELETE_PATCH = patch.object(recycle_bin, "_shell_delete", refuse)
    _SHELL_DELETE_PATCH.start()


def tearDownModule():
    global _SHELL_DELETE_PATCH
    if _SHELL_DELETE_PATCH is not None:
        _SHELL_DELETE_PATCH.stop()
        _SHELL_DELETE_PATCH = None


def item(item_id, decision=None):
    return {"id": item_id, "category": "adult", "start_seconds": 1.0, "end_seconds": 2.0, "decision": decision}


def tree_digest(path):
    digest = hashlib.sha256()
    for file in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(file.relative_to(path).as_posix().encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


class SkipFixture(unittest.TestCase):
    """A temp root with a real JobStore and JobScheduler (never the project's state)."""

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        for name in ("input", "reports/jobs", "output", "work", "state", "logs"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
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
        self.center = center
        cc._SUMMARY_CACHE.clear()
        # No scan stages here: a queued job's only pending stage is its render.
        patcher = patch("biliflow.scheduler.pipeline_stages", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_job(self, name, items, *, advisory=0, state="READY_TO_EXPORT", status=None):
        source = self.root / "input" / f"{name}.mp4"
        source.write_bytes(name.encode() * 64)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if status is None:
            status = (
                "NEEDS_MORE_CONTEXT" if any(x["decision"] == "NEEDS_MORE_CONTEXT" for x in items)
                else "REVIEW_REQUIRED" if any(not x["decision"] for x in items) else "READY_FOR_EDIT_PLAN"
            )
        folder = self.root / "reports" / "jobs" / name
        folder.mkdir(parents=True)
        (folder / "scan.json").write_text('{"intervals": []}', encoding="utf-8")
        queue = {
            "status": status, "updated_at": "2026-10-03T00:00:00+07:00",
            "source": {"path": str(source), "sha256": digest, "duration_seconds": 60.0},
            "reports": [f"reports/jobs/{name}/scan.json"],
            "items": items, "advisory_items": [item(f"adv-{i}") for i in range(advisory)],
        }
        (folder / "review-queue.json").write_text(json.dumps(queue), encoding="utf-8")
        stat = source.stat()
        job = self.store.upsert_job(
            job_key=name, source_path=source, source_sha256=digest,
            source_size_bytes=stat.st_size, source_mtime_ns=stat.st_mtime_ns,
            content_style="animation", state=state,
        )
        self.store.update_job(job["id"], active_queue_path=f"reports/jobs/{name}/review-queue.json",
                              active_revision=1, progress=1.0)
        return int(job["id"])

    def queue_file(self, job_id):
        return self.root / self.store.get_job(job_id)["active_queue_path"]

    def snapshot(self, job_id):
        job = self.store.get_job(job_id)
        return {
            "queue": hashlib.sha256(self.queue_file(job_id).read_bytes()).hexdigest(),
            "source": hashlib.sha256(Path(job["source_path"]).read_bytes()).hexdigest(),
            "reports": tree_digest(self.root / "reports"),
            "output": tree_digest(self.root / "output"),
            "input": tree_digest(self.root / "input"),
        }

    def events(self, job_id, kind):
        return [event for event in self.store.events(job_id) if event["event_type"] == kind]

    def stage_state(self, job_id, name="render"):
        return {x["name"]: x["state"] for x in self.store.stages(job_id)}.get(name)

    # ----------------------------------------------- real HTTP on port 0
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

    def post(self, path, *, token="test-token", host=None, body=None):
        return self.request("POST", path, token=token, host=host, body=body)

    def get(self, path):
        return self.request("GET", path)

    def finalize(self, job_id, **kwargs):
        with (
            patch("biliflow.control_center.build_edit_plan", return_value={"status": "READY_FOR_PREVIEW"}),
            patch("biliflow.control_center.authorize_final_from_resolved_review"),
        ):
            return self.center.finalize(job_id, **kwargs)

    def finalize_queued(self, job_id):
        self.assertEqual(self.finalize(job_id)["status"], "QUEUED")


class SharedGuardTests(unittest.TestCase):
    """B5: control_center uses export_guards, the same objects, under the old names."""

    def test_control_center_shares_the_export_guards(self):
        # One lock: source cleanup and the Control Center serialize the same queue IO.
        self.assertIs(cc._REVIEW_QUEUE_IO, export_guards.REVIEW_QUEUE_IO)
        for name in (
            "DECISION_LABELS", "review_summary", "skip_refusal", "SOURCE_MISSING_MESSAGE",
            "EXPORT_IN_FLIGHT_MESSAGE", "REVIEW_EDIT_IN_FLIGHT_MESSAGE", "QUEUE_NOT_READY_MESSAGE",
            "SOURCE_CLEANED_MESSAGE", "SOURCE_CLEANED_REVIEW_REFUSAL", "SOURCE_CLEANED_MEDIA_MESSAGE",
            "render_in_flight", "export_state_refusal", "export_source_refusal",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(cc, name), getattr(export_guards, name))
        self.assertIs(cc.SKIPPED_REFUSAL, export_guards.SKIPPED_REFUSAL)


class SkipExportTests(SkipFixture):
    """Bỏ qua (không xuất), Mở lại để xuất and the finalize guards."""

    def test_skip_a_video_without_main_items_touches_no_file(self):
        job_id = self.make_job("tap30", [], advisory=193)
        before = self.snapshot(job_id)
        value = self.center.skip_export(job_id)
        self.assertEqual((value["state"], value["progress"], value["queue_seq"]), ("SKIPPED", 1.0, None))
        self.assertEqual(self.snapshot(job_id), before)
        record = self.store.setting(f"skip:{job_id}")
        self.assertEqual(
            {key: record[key] for key in ("queue_path", "revision", "main_items", "advisory_items", "decisions", "actor")},
            {"queue_path": f"reports/jobs/tap30/review-queue.json", "revision": 1, "main_items": 0,
             "advisory_items": 193, "decisions": {}, "actor": "control_center_user"},
        )
        self.assertTrue(record["skipped_at"])
        events = self.events(job_id, "JOB_SKIPPED")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"], record)
        # Never selected by the worker.
        self.assertIsNone(self.center.scheduler._select())

    def test_skip_a_video_whose_every_decision_is_keep(self):
        job_id = self.make_job("tap14", [item("a", "KEEP"), item("b", "KEEP")])
        value = self.center.skip_export(job_id)
        self.assertEqual(value["state"], "SKIPPED")
        self.assertEqual(self.store.setting(f"skip:{job_id}")["decisions"], {"KEEP": 2})

    def test_skip_refusals_leave_everything_unchanged(self):
        cases = {
            "blur": (self.make_job("blur", [item("a", "KEEP"), item("b", "BLUR")]), "không phải Giữ nguyên"),
            "cut": (self.make_job("cut", [item("a", "CUT")]), "1 Cắt cảnh"),
            "more": (self.make_job("more", [item("a", "NEEDS_MORE_CONTEXT")], state="WAITING_REVIEW"), "Chỉ bỏ qua"),
            "waiting": (self.make_job("waiting", [item("a")], state="WAITING_REVIEW"), "Chỉ bỏ qua"),
            "completed": (self.make_job("completed", [], state="COMPLETED"), "Chỉ bỏ qua"),
        }
        no_queue = self.make_job("noqueue", [])
        self.store.update_job(no_queue, active_queue_path=None, active_revision=None)
        cases["no_queue"] = (no_queue, "danh sách duyệt")
        broken = self.make_job("broken", [])
        self.queue_file(broken).write_text("{", encoding="utf-8")
        cases["broken"] = (broken, "danh sách duyệt")
        for name, (job_id, message) in cases.items():
            with self.subTest(case=name):
                before = self.store.get_job(job_id)
                with self.assertRaisesRegex(ValueError, message):
                    self.center.skip_export(job_id)
                self.assertEqual(self.store.get_job(job_id), before)
                self.assertIsNone(self.store.setting(f"skip:{job_id}"))
                self.assertEqual(self.events(job_id, "JOB_SKIPPED"), [])

    def test_skip_is_refused_while_an_export_is_queued_or_running(self):
        queued = self.make_job("queued", [])
        self.center.scheduler.queue_render(queued, plan_path=self.root / "work" / "q.json",
                                           output_path=self.root / "output" / "q.mp4")
        rendering = self.make_job("rendering", [])
        self.center.scheduler.queue_render(rendering, plan_path=self.root / "work" / "r.json",
                                           output_path=self.root / "output" / "r.mp4")
        self.assertTrue(self.store.claim_queued(rendering, "RENDERING", "render"))
        scanning = self.make_job("scanning", [])
        self.store.update_job(scanning, state="SCANNING_TEXT")
        busy = self.make_job("busy", [])
        for job_id, message in ((queued, "đang chờ xuất"), (rendering, "đang chờ xuất"),
                                (scanning, "đang được xử lý")):
            with self.subTest(job=job_id):
                state = self.store.get_job(job_id)["state"]
                with self.assertRaisesRegex(ValueError, message):
                    self.center.skip_export(job_id)
                self.assertEqual(self.store.get_job(job_id)["state"], state)
        with patch.object(self.center.scheduler, "is_busy", return_value=True):
            with self.assertRaisesRegex(ValueError, "đang được xử lý"):
                self.center.skip_export(busy)
        self.assertEqual(self.store.get_job(busy)["state"], "READY_TO_EXPORT")

    def status_summary(self, job_id):
        with (
            patch("biliflow.control_center._resources", return_value={}),
            patch("biliflow.control_center.storage_status"),
        ):
            jobs = {job["id"]: job for job in self.center.status()["jobs"]}
        return jobs[job_id]["review_summary"]

    def test_a_stale_pending_render_does_not_block_the_skip(self):
        # An export interrupted by a restart, cancelled or paused leaves its render
        # stage behind. Hủy retires it at once; for the others the review sync
        # that returns the job to READY_TO_EXPORT retires it (batch 3, B6), so
        # Tiếp tục can never render the old plan and the skip is still allowed.
        for name, stop, reason in (
            ("restart", lambda job_id: self.store.update_job(job_id, state="INTERRUPTED_RECOVERABLE"),
             "review_changed"),
            ("cancel", self.center.scheduler.cancel, "cancel"),
            ("pause", self.center.scheduler.pause_now, "review_changed"),
        ):
            with self.subTest(case=name):
                job_id = self.make_job(name, [item("a", "KEEP")])
                self.center.scheduler.queue_render(job_id, plan_path=self.root / "work" / f"{name}.json",
                                                   output_path=self.root / "output" / f"{name}.mp4")
                render = self.store.setting(f"render:{job_id}")
                self.assertTrue(self.store.claim_queued(job_id, "RENDERING", "render"))
                stop(job_id)
                queue = json.loads(self.queue_file(job_id).read_text(encoding="utf-8"))
                self.center.sync_queue_state(job_id, queue)
                job = self.store.get_job(job_id)
                self.assertEqual((job["state"], job["current_stage"]), ("READY_TO_EXPORT", None))
                self.assertEqual(self.stage_state(job_id), "CANCELLED")
                retired = self.events(job_id, "EXPORT_REQUEST_RETIRED")
                self.assertEqual([event["payload"]["reason"] for event in retired], [reason])
                self.assertEqual(retired[0]["message"], EXPORT_RETIRE_MESSAGES[reason])
                self.assertEqual(retired[0]["payload"]["stage_state"], "PENDING")
                # render:{id} stays as history; nothing waits in the queue.
                self.assertEqual(retired[0]["payload"]["render"], render)
                self.assertEqual(self.store.setting(f"render:{job_id}"), render)
                self.assertEqual(self.center.scheduler.queue_order(), [])
                # The card offers the button only when the server accepts it.
                self.assertTrue(self.status_summary(job_id)["skip_eligible"])
                self.assertEqual(self.center.skip_export(job_id)["state"], "SKIPPED")
                # Nothing was left for the skip to retire.
                self.assertEqual(len(self.events(job_id, "EXPORT_REQUEST_RETIRED")), 1)

    def test_status_never_offers_a_skip_the_server_refuses(self):
        ready = self.make_job("ready", [])
        self.assertTrue(self.status_summary(ready)["skip_eligible"])
        with patch.object(self.center.scheduler, "is_busy", return_value=True):
            self.assertFalse(self.status_summary(ready)["skip_eligible"])
            with self.assertRaises(ValueError):
                self.center.skip_export(ready)
        skipped = self.make_job("skipped", [])
        self.center.skip_export(skipped)
        self.assertFalse(self.status_summary(skipped)["skip_eligible"])

    def test_stop_actions_refuse_a_skipped_video(self):
        job_id = self.make_job("tap30", [])
        self.center.skip_export(job_id)
        before = (self.store.get_job(job_id), self.store.setting(f"skip:{job_id}"))
        scheduler = self.center.scheduler
        for action in (scheduler.cancel, scheduler.pause_now, scheduler.stop_after_stage):
            with self.subTest(action=action.__name__):
                with self.assertRaises(ValueError) as caught:
                    action(job_id)
                self.assertEqual(str(caught.exception), SKIPPED_STOP_REFUSAL)
                self.assertEqual((self.store.get_job(job_id), self.store.setting(f"skip:{job_id}")), before)
        self.assertEqual(self.events(job_id, "JOB_CANCELLED") + self.events(job_id, "JOB_PAUSED"), [])

    def test_unskip_returns_the_video_to_ready_to_export(self):
        job_id = self.make_job("tap30", [], advisory=3)
        self.center.skip_export(job_id)
        before = self.snapshot(job_id)
        value = self.center.unskip_export(job_id)
        self.assertEqual(value["state"], "READY_TO_EXPORT")
        self.assertIsNone(self.store.setting(f"skip:{job_id}"))
        self.assertEqual(self.snapshot(job_id), before)
        events = self.events(job_id, "JOB_UNSKIPPED")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["skip"]["advisory_items"], 3)
        with self.assertRaisesRegex(ValueError, "không ở trạng thái Đã bỏ qua"):
            self.center.unskip_export(job_id)
        # It can be skipped again.
        self.assertEqual(self.center.skip_export(job_id)["state"], "SKIPPED")

    def test_a_skipped_video_is_refused_by_finalize_resume_and_retry(self):
        job_id = self.make_job("tap30", [])
        self.center.skip_export(job_id)
        queue_before = self.queue_file(job_id).read_bytes()
        with self.assertRaises(ValueError) as caught:
            self.center.finalize(job_id)
        self.assertEqual(str(caught.exception), "Video đã được đánh dấu bỏ qua; bấm “Mở lại để xuất” trước.")
        self.assertEqual(SKIPPED_REFUSAL, str(caught.exception))
        for action in (self.center.scheduler.resume, self.center.scheduler.retry):
            with self.assertRaisesRegex(ValueError, "Mở lại để xuất"):
                action(job_id)
        self.assertEqual(self.queue_file(job_id).read_bytes(), queue_before)
        self.assertEqual(self.store.get_job(job_id)["state"], "SKIPPED")
        self.assertIsNone(self.store.setting(f"render:{job_id}"))

    def test_rerun_clears_the_skip_record(self):
        job_id = self.make_job("tap30", [])
        self.center.skip_export(job_id)
        with patch("biliflow.scheduler.pipeline_stages",
                   return_value=[PipelineStage("preflight", "PREFLIGHT", tuple())]):
            value = self.center.scheduler.rerun(job_id)
        self.assertEqual(value["state"], "QUEUED")
        self.assertIsNone(self.store.setting(f"skip:{job_id}"))

    def test_a_restart_keeps_the_skip(self):
        job_id = self.make_job("tap30", [])
        self.center.skip_export(job_id)
        import_existing_project(self.root, self.store)
        self.assertEqual(self.store.get_job(job_id)["state"], "SKIPPED")

    # ------------------------------------------------------- review decisions
    def test_decisions_keep_a_skip_while_it_stays_allowed(self):
        job_id = self.make_job("tap14", [item("a", "KEEP"), item("b", "KEEP")])
        self.center.skip_export(job_id)
        queue = json.loads(self.queue_file(job_id).read_text(encoding="utf-8"))
        self.center.sync_queue_state(job_id, queue)
        self.assertEqual(self.store.get_job(job_id)["state"], "SKIPPED")
        self.assertEqual(self.events(job_id, "JOB_SKIP_SUPERSEDED"), [])

    def test_a_decision_that_forbids_the_skip_reopens_the_video(self):
        for decisions, status, expected in (
            (["KEEP", "BLUR"], "READY_FOR_EDIT_PLAN", "READY_TO_EXPORT"),
            (["KEEP", None], "REVIEW_REQUIRED", "WAITING_REVIEW"),
        ):
            with self.subTest(decisions=decisions):
                job_id = self.make_job(f"tap-{decisions[1]}", [item("a", "KEEP"), item("b", "KEEP")])
                self.center.skip_export(job_id)
                queue = json.loads(self.queue_file(job_id).read_text(encoding="utf-8"))
                queue["items"] = [item("a", decisions[0]), item("b", decisions[1])]
                queue["status"] = status
                self.center.sync_queue_state(job_id, queue)
                self.assertEqual(self.store.get_job(job_id)["state"], expected)
                self.assertIsNone(self.store.setting(f"skip:{job_id}"))
                events = self.events(job_id, "JOB_SKIP_SUPERSEDED")
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["payload"]["state"], expected)
                self.assertEqual(events[0]["payload"]["skip"]["decisions"], {"KEEP": 2})

    def test_decisions_never_move_a_queued_or_running_export(self):
        queued = self.make_job("queued", [item("a", "BLUR")])
        self.center.scheduler.queue_render(queued, plan_path=self.root / "work" / "q.json",
                                           output_path=self.root / "output" / "q.mp4")
        rendering = self.make_job("rendering", [item("a", "BLUR")])
        self.center.scheduler.queue_render(rendering, plan_path=self.root / "work" / "r.json",
                                           output_path=self.root / "output" / "r.mp4")
        self.assertTrue(self.store.claim_queued(rendering, "RENDERING", "render"))
        seq = self.store.get_job(queued)["queue_seq"]
        for status in ("REVIEW_REQUIRED", "READY_FOR_EDIT_PLAN"):
            for job_id, state in ((queued, "QUEUED"), (rendering, "RENDERING")):
                with self.subTest(status=status, job=job_id):
                    self.center.sync_queue_state(job_id, {"status": status, "items": [item("a")]})
                    self.assertEqual(self.store.get_job(job_id)["state"], state)
        self.assertEqual(self.store.get_job(queued)["queue_seq"], seq)
        # The normal flow is unchanged for a job that is not exporting.
        waiting = self.make_job("waiting", [item("a")], state="WAITING_REVIEW")
        self.center.sync_queue_state(waiting, {"status": "READY_FOR_EDIT_PLAN", "items": [item("a", "BLUR")]})
        self.assertEqual(self.store.get_job(waiting)["state"], "READY_TO_EXPORT")
        self.store.update_job(waiting, state="COMPLETED")
        self.center.sync_queue_state(waiting, {"status": "REVIEW_REQUIRED", "items": [item("a")]})
        self.assertEqual(self.store.get_job(waiting)["state"], "WAITING_REVIEW")

    # ---------------------------------------------------------------- finalize
    def test_finalize_stores_the_review_page_size_policy(self):
        for mode, gb in (("default", None), ("custom", 2.5), ("unlimited", None)):
            with self.subTest(mode=mode):
                job_id = self.make_job(f"policy-{mode}", [item("a", "BLUR")])
                result = self.finalize(job_id, size_mode=mode, max_output_gb=gb)
                expected = normalize_output_size_policy(mode, gb)
                saved = json.loads(self.queue_file(job_id).read_text(encoding="utf-8"))
                self.assertEqual(saved["export_size_policy"], expected)
                self.assertEqual(result["export_size_policy"], expected)
                self.assertEqual(result["status"], "QUEUED")
                render = self.store.setting(f"render:{job_id}")
                self.assertEqual(render["max_output_bytes"], expected["maximum_output_bytes"])
                self.assertEqual(self.center.scheduler.queue_order()[-1]["kind"], "export")

    def test_finalize_guards_run_before_any_write(self):
        job_id = self.make_job("guard", [item("a", "BLUR")])
        self.finalize(job_id)
        queue_before = self.queue_file(job_id).read_bytes()
        render_before = self.store.setting(f"render:{job_id}")
        seq = self.store.get_job(job_id)["queue_seq"]
        # Already queued for export, then rendering: refused, nothing rewritten.
        for state in ("QUEUED", "RENDERING"):
            with self.subTest(state=state):
                if state == "RENDERING":
                    self.assertTrue(self.store.claim_queued(job_id, "RENDERING", "render"))
                with self.assertRaises(ValueError) as caught:
                    self.finalize(job_id, size_mode="unlimited")
                self.assertEqual(str(caught.exception), EXPORT_IN_FLIGHT_MESSAGE)
                self.assertEqual(self.queue_file(job_id).read_bytes(), queue_before)
                self.assertEqual(self.store.setting(f"render:{job_id}"), render_before)
                self.assertEqual(self.store.get_job(job_id)["state"], state)
        self.assertEqual(self.store.get_job(job_id)["queue_seq"], seq)
        # A missing source cannot be exported.
        missing = self.make_job("missing", [item("a", "BLUR")])
        Path(self.store.get_job(missing)["source_path"]).unlink()
        missing_before = self.queue_file(missing).read_bytes()
        with self.assertRaises(ValueError) as caught:
            self.finalize(missing, size_mode="unlimited")
        self.assertEqual(str(caught.exception), SOURCE_MISSING_MESSAGE)
        self.assertEqual(self.queue_file(missing).read_bytes(), missing_before)
        self.assertEqual(self.store.get_job(missing)["state"], "READY_TO_EXPORT")
        self.assertIsNone(self.store.setting(f"render:{missing}"))
        # An unfinished review is refused before the policy is written.
        unfinished = self.make_job("unfinished", [item("a")], state="WAITING_REVIEW")
        unfinished_before = self.queue_file(unfinished).read_bytes()
        with self.assertRaises(ValueError) as caught:
            self.finalize(unfinished)
        self.assertEqual(str(caught.exception), "Vẫn còn mục chưa có quyết định cuối cùng")
        self.assertEqual(str(caught.exception), QUEUE_NOT_READY_MESSAGE)
        self.assertEqual(self.queue_file(unfinished).read_bytes(), unfinished_before)
        # A busy worker refuses with the same text as before batch 3 (export_guards).
        busy = self.make_job("busy", [item("a", "BLUR")])
        busy_before = self.queue_file(busy).read_bytes()
        with patch.object(self.center.scheduler, "is_busy", return_value=True):
            with self.assertRaises(ValueError) as caught:
                self.finalize(busy)
        self.assertEqual(
            str(caught.exception),
            f"Video #{busy} đang trong hàng đợi hoặc đang được xử lý; chờ xong rồi hãy xuất.",
        )
        self.assertEqual(self.queue_file(busy).read_bytes(), busy_before)

    # ------------------------------------------------------------------ status
    def test_status_reports_review_summary_source_and_skip(self):
        ready = self.make_job("ready", [], advisory=2)
        keep = self.make_job("keep", [item("a", "KEEP")])
        blur = self.make_job("blur", [item("a", "BLUR"), item("b", "KEEP")])
        waiting = self.make_job("waiting", [item("a")], state="WAITING_REVIEW")
        skipped = self.make_job("skipped", [])
        self.center.skip_export(skipped)
        done = self.make_job("done", [], state="COMPLETED")
        Path(self.store.get_job(blur)["source_path"]).unlink()
        with (
            patch("biliflow.control_center._resources", return_value={}),
            patch("biliflow.control_center.storage_status"),
            patch("biliflow.control_center._read_json", wraps=cc._read_json) as reads,
        ):
            first = {job["id"]: job for job in self.center.status()["jobs"]}
            parsed = reads.call_count
            second = {job["id"]: job for job in self.center.status()["jobs"]}
            self.assertEqual(reads.call_count, parsed)
            # A changed queue is parsed again on the next poll.
            path = self.queue_file(waiting)
            queue = json.loads(path.read_text(encoding="utf-8"))
            queue["items"][0]["decision"] = "KEEP"
            queue["status"] = "READY_FOR_EDIT_PLAN"
            path.write_text(json.dumps(queue), encoding="utf-8")
            third = {job["id"]: job for job in self.center.status()["jobs"]}
            self.assertEqual(reads.call_count, parsed + 1)
        self.assertEqual(first, second)
        self.assertEqual(first[ready]["review_summary"], {
            "status": "READY_FOR_EDIT_PLAN", "main_items": 0, "advisory_items": 2, "pending": 0,
            "decisions": {}, "export_size_policy": None, "skip_eligible": True,
        })
        self.assertTrue(first[keep]["review_summary"]["skip_eligible"])
        self.assertFalse(first[blur]["review_summary"]["skip_eligible"])
        self.assertEqual(first[blur]["review_summary"]["decisions"], {"BLUR": 1, "KEEP": 1})
        self.assertEqual((first[waiting]["review_summary"]["status"], first[waiting]["review_summary"]["pending"]),
                         ("REVIEW_REQUIRED", 1))
        self.assertEqual(third[waiting]["review_summary"]["decisions"], {"KEEP": 1})
        self.assertEqual(first[skipped]["skip"]["main_items"], 0)
        self.assertIsNone(first[ready]["skip"])
        self.assertIsNone(first[done]["review_summary"])
        self.assertFalse(first[blur]["source_present"])
        self.assertTrue(first[ready]["source_present"])

    def test_skip_refusal_rules(self):
        def summary(items, status="READY_FOR_EDIT_PLAN", advisory=0):
            return review_summary({"status": status, "items": items, "advisory_items": [{}] * advisory})
        self.assertIsNone(skip_refusal(summary([], advisory=193)))
        self.assertIsNone(skip_refusal(summary([item("a", "KEEP")] * 3)))
        self.assertIn("không phải Giữ nguyên", skip_refusal(summary([item("a", "KEEP"), item("b", "BLUR")])))
        self.assertIn("1 Cần xem thêm", skip_refusal(summary([item("a", "NEEDS_MORE_CONTEXT")])))
        self.assertIn("chưa có quyết định", skip_refusal(summary([item("a")], status="REVIEW_REQUIRED")))
        # A queue that claims READY with an undecided item is still refused.
        self.assertIn("chưa có quyết định", skip_refusal(summary([item("a")])))


class SkipHttpTests(SkipFixture):
    """The skip and unskip routes over real HTTP (port 0), with the token and Host checks."""

    def setUp(self):
        super().setUp()
        self.serve()

    def test_skip_and_unskip_routes(self):
        job_id = self.make_job("tap30", [])
        self.assertEqual(self.post(f"/api/jobs/{job_id}/skip", token=None)[0], 403)
        self.assertEqual(self.post(f"/api/jobs/{job_id}/skip", host="evil.example")[0], 403)
        self.assertEqual(self.store.get_job(job_id)["state"], "READY_TO_EXPORT")
        status, body = self.post(f"/api/jobs/{job_id}/skip")
        self.assertEqual((status, body["state"]), (200, "SKIPPED"))
        status, body = self.post(f"/api/jobs/{job_id}/skip")
        self.assertEqual(status, 400)
        self.assertIn("đã được đánh dấu bỏ qua", body["error"])
        status, body = self.post(f"/api/jobs/{job_id}/review/finalize")
        self.assertEqual((status, body["error"]), (400, SKIPPED_REFUSAL))
        status, body = self.post(f"/api/jobs/{job_id}/unskip")
        self.assertEqual((status, body["state"]), (200, "READY_TO_EXPORT"))
        blur = self.make_job("blur", [item("a", "BLUR")])
        status, body = self.post(f"/api/jobs/{blur}/skip")
        self.assertEqual(status, 400)
        self.assertIn("hãy xuất video thay vì bỏ qua", body["error"])

    def test_stop_routes_refuse_a_skipped_video(self):
        job_id = self.make_job("tap30", [])
        self.post(f"/api/jobs/{job_id}/skip")
        for action in ("cancel", "pause", "stop-after-stage"):
            with self.subTest(action=action):
                status, body = self.post(f"/api/jobs/{job_id}/{action}")
                self.assertEqual((status, body["error"]), (400, SKIPPED_STOP_REFUSAL))
                job = self.store.get_job(job_id)
                self.assertEqual((job["state"], job["stop_mode"]), ("SKIPPED", None))
                self.assertIsNotNone(self.store.setting(f"skip:{job_id}"))

    def test_review_edits_are_refused_while_an_export_waits_or_runs(self):
        queued = self.make_job("queued", [item("a", "KEEP"), item("b", "KEEP")])
        self.finalize_queued(queued)
        rendering = self.make_job("rendering", [item("a", "KEEP"), item("b", "KEEP")])
        self.finalize_queued(rendering)
        self.assertTrue(self.store.claim_queued(rendering, "RENDERING", "render"))
        edits = (
            ("decision", {"id": "b", "decision": "BLUR", "full_frame": True}),
            ("clear", {"id": "a"}),
            ("bulk-keep", {"filter": "pending"}),
            ("bulk-accept", {"filter": "pending"}),
        )
        for job_id, state in ((queued, "QUEUED"), (rendering, "RENDERING")):
            render = self.store.setting(f"render:{job_id}")
            before = self.queue_file(job_id).read_bytes()
            for action, body in edits:
                with self.subTest(state=state, action=action):
                    status, payload = self.post(f"/api/jobs/{job_id}/review/{action}", body=body)
                    self.assertEqual((status, payload["error"]), (400, REVIEW_EDIT_IN_FLIGHT_MESSAGE))
                    self.assertEqual(self.queue_file(job_id).read_bytes(), before)
                    self.assertEqual(self.store.get_job(job_id)["state"], state)
                    self.assertEqual(self.store.setting(f"render:{job_id}"), render)
        # Hủy ends the export; the decisions can then be changed again.
        self.center.scheduler.cancel(queued)
        cleared = {"status": "REVIEW_REQUIRED", "items": [item("a"), item("b", "KEEP")]}
        with patch("biliflow.control_center.clear_review_decision", return_value=cleared) as clear:
            status, payload = self.post(f"/api/jobs/{queued}/review/clear", body={"id": "a"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(clear.call_count, 1)
        self.assertEqual(self.store.get_job(queued)["state"], "WAITING_REVIEW")


class StaleExportTests(SkipFixture):
    """B6 (open item (b)): an old export request is retired when the review changes,
    on a skip and when finalize finds the export already made."""

    def paused_export(self, name, decisions=("BLUR",)):
        job_id = self.make_job(name, [item(chr(97 + index), value) for index, value in enumerate(decisions)])
        self.finalize_queued(job_id)
        self.assertTrue(self.store.claim_queued(job_id, "RENDERING", "render"))
        self.center.scheduler.pause_now(job_id)
        job = self.store.get_job(job_id)
        self.assertEqual((job["state"], job["current_stage"], job["stop_mode"]), ("PAUSED", "render", "PAUSED"))
        self.assertEqual(self.stage_state(job_id), "PENDING")
        return job_id

    def test_a_decision_retires_a_paused_export(self):
        job_id = self.paused_export("paused")
        render = self.store.setting(f"render:{job_id}")
        ready = {"status": "READY_FOR_EDIT_PLAN", "items": [item("a", "KEEP")]}
        with patch("biliflow.control_center.record_review_decision", return_value=ready) as record:
            status, payload = self.post(f"/api/jobs/{job_id}/review/decision",
                                        body={"id": "a", "decision": "KEEP"})
        self.assertEqual((status, record.call_count), (200, 1), payload)
        job = self.store.get_job(job_id)
        self.assertEqual((job["state"], job["current_stage"]), ("READY_TO_EXPORT", None))
        self.assertEqual(self.stage_state(job_id), "CANCELLED")
        retired = self.events(job_id, "EXPORT_REQUEST_RETIRED")
        self.assertEqual([event["payload"]["reason"] for event in retired], ["review_changed"])
        self.assertEqual(retired[0]["message"], EXPORT_RETIRE_MESSAGES["review_changed"])
        self.assertEqual(self.store.setting(f"render:{job_id}"), render)
        # A stale tab's Tiếp tục cannot render the old plan.
        seq = self.store.get_job(job_id)["queue_seq"]
        for action in (self.center.scheduler.resume, self.center.scheduler.retry):
            with self.subTest(action=action.__name__):
                with self.assertRaises(ValueError) as caught:
                    action(job_id)
                self.assertEqual(str(caught.exception), RESUME_SETTLED_REFUSAL.format(job_id=job_id))
                self.assertEqual(self.store.get_job(job_id)["state"], "READY_TO_EXPORT")
                self.assertEqual(self.store.get_job(job_id)["queue_seq"], seq)
                self.assertEqual(self.stage_state(job_id), "CANCELLED")
        self.assertEqual(self.center.scheduler.queue_order(), [])
        # Xuất video queues a new request from the new decisions.
        self.finalize_queued(job_id)
        self.assertEqual(self.stage_state(job_id), "PENDING")
        self.assertEqual(self.center.scheduler.queue_order()[-1]["kind"], "export")

    def test_decisions_on_a_settled_job_write_no_retire_event(self):
        job_id = self.make_job("ready", [item("a", "KEEP")])
        queue = json.loads(self.queue_file(job_id).read_text(encoding="utf-8"))
        self.center.sync_queue_state(job_id, queue)
        self.assertEqual(self.store.get_job(job_id)["state"], "READY_TO_EXPORT")
        self.assertIsNone(self.stage_state(job_id))
        self.assertEqual(self.events(job_id, "EXPORT_REQUEST_RETIRED"), [])

    def test_finalize_shortcut_retires_the_old_request(self):
        job_id = self.paused_export("shortcut", decisions=("KEEP",))
        queue = json.loads(self.queue_file(job_id).read_text(encoding="utf-8"))
        queue["export_size_policy"] = normalize_output_size_policy("default", None)
        _plan, output, _job = review_export_paths(self.root, queue)
        output.write_bytes(b"already exported")
        # A file no manifest proves is neither the export nor overwritten.
        with self.assertRaises(ValueError) as caught:
            self.finalize(job_id)
        self.assertEqual(str(caught.exception), EXPORT_PATH_TAKEN_MESSAGE.format(name=output.name))
        self.assertEqual((self.store.get_job(job_id)["state"], self.stage_state(job_id)), ("PAUSED", "PENDING"))
        self.assertEqual(output.read_bytes(), b"already exported")
        job = self.store.get_job(job_id)
        output.with_name(output.name + ".manifest.json").write_text(json.dumps({
            "status": "COMPLETED", "created_at": "2026-10-03T12:00:00+07:00",
            "source": {"path": job["source_path"], "sha256": job["source_sha256"], "modified": False},
            "output": {"path": output.relative_to(self.root).as_posix(), "bytes": output.stat().st_size,
                       "sha256": hashlib.sha256(output.read_bytes()).hexdigest()},
            "encoding": {"full_decode_validation_passed": True},
            "operations": approved_operations(queue),
        }), encoding="utf-8")
        result = self.finalize(job_id)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["output"], output.relative_to(self.root).as_posix())
        job = self.store.get_job(job_id)
        self.assertEqual(
            (job["state"], job["progress"], job["current_stage"], job["stop_mode"]),
            ("COMPLETED", 1.0, None, None),
        )
        self.assertEqual(self.stage_state(job_id), "CANCELLED")
        retired = self.events(job_id, "EXPORT_REQUEST_RETIRED")
        self.assertEqual([event["payload"]["reason"] for event in retired], ["output_exists"])
        self.assertEqual(retired[0]["message"], EXPORT_RETIRE_MESSAGES["output_exists"])
        self.assertEqual(self.center.scheduler.queue_order(), [])
        with self.assertRaises(ValueError) as caught:
            self.center.scheduler.resume(job_id)
        self.assertEqual(str(caught.exception), RESUME_SETTLED_REFUSAL.format(job_id=job_id))

    def test_skip_retires_a_failed_render(self):
        job_id = self.make_job("failed", [item("a", "KEEP")])
        self.center.scheduler.queue_render(job_id, plan_path=self.root / "work" / "f.json",
                                           output_path=self.root / "output" / "f.mp4")
        # A render that failed before batch 3 and a job put back to READY_TO_EXPORT.
        self.store.update_stage(job_id, "render", state="FAILED", error="ffmpeg failed")
        self.store.update_job(job_id, state="READY_TO_EXPORT", current_stage="render")
        value = self.center.skip_export(job_id)
        self.assertEqual((value["state"], value["current_stage"]), ("SKIPPED", None))
        self.assertEqual(self.stage_state(job_id), "CANCELLED")
        retired = self.events(job_id, "EXPORT_REQUEST_RETIRED")
        self.assertEqual([event["payload"]["reason"] for event in retired], ["skipped"])
        self.assertEqual(retired[0]["payload"]["stage_state"], "FAILED")
        self.assertEqual(retired[0]["message"], EXPORT_RETIRE_MESSAGES["skipped"])
        job = self.store.get_job(job_id)
        self.assertEqual((job["state"], job["current_stage"]), ("SKIPPED", None))


class CleanedSourceHttpTests(SkipFixture):
    """B7: a video whose source is in the Recycle Bin (or on its way there) is read-only.

    Rows come from store.add_source_cleanup (+ finish_source_cleanup); no recycler is ever involved.
    """

    EDITS = (
        ("decision", {"id": "b", "decision": "CUT"}),
        ("clear", {"id": "a"}),
        ("bulk-keep", {"filter": "pending"}),
        ("bulk-accept", {"filter": "pending"}),
    )

    def clean(self, job_id, state, *, kind="EXPORTED"):
        """Record a cleanup of the job's source; latest row in ``state`` (PENDING, RECYCLED or FAILED)."""
        job = self.store.get_job(job_id)
        source = Path(job["source_path"])
        row = self.store.add_source_cleanup(
            job_id=job_id, kind=kind, source_path=str(source.resolve()),
            source_sha256=job["source_sha256"], size_bytes=job["source_size_bytes"],
            mtime_ns=job["source_mtime_ns"],
        )
        if state != "PENDING":
            self.store.finish_source_cleanup(row, state=state, verified=state == "RECYCLED",
                                             error="Windows báo lỗi" if state == "FAILED" else None)
        if state == "RECYCLED":
            source.unlink()  # The shell moved it to the Recycle Bin.
        self.assertEqual(self.store.latest_source_cleanup(job_id)["state"], state)
        return row

    def frozen(self, job_id):
        job = self.store.get_job(job_id)
        return {
            "job": job, "skip": self.store.setting(f"skip:{job_id}"),
            "render": self.store.setting(f"render:{job_id}"),
            "queue": self.queue_file(job_id).read_bytes(),
            "stages": self.store.stages(job_id), "output": tree_digest(self.root / "output"),
            "work": tree_digest(self.root / "work"),
        }

    def refuse_writes(self):
        """Every review writer fails the test if a refused route ever reaches it."""
        stack = []
        for name in ("record_review_decision", "clear_review_decision", "bulk_keep_review_items",
                     "bulk_accept_suggested_decisions", "build_edit_plan",
                     "authorize_final_from_resolved_review"):
            patcher = patch(f"biliflow.control_center.{name}",
                            side_effect=AssertionError(f"{name} reached on a cleaned video"))
            patcher.start()
            stack.append(patcher)
        return stack

    def test_a_cleaned_video_refuses_every_change(self):
        for latest in ("RECYCLED", "PENDING"):
            with self.subTest(latest=latest):
                done = self.make_job(f"done-{latest}", [item("a", "KEEP"), item("b", "BLUR")],
                                     state="COMPLETED")
                skipped = self.make_job(f"skipped-{latest}", [item("a", "KEEP")])
                self.center.skip_export(skipped)
                self.clean(done, latest)
                self.clean(skipped, latest, kind="SKIPPED")
                expected = [
                    (done, "review/finalize", {"size_mode": "unlimited"}, SOURCE_CLEANED_MESSAGE),
                    (done, "skip", {}, SOURCE_CLEANED_MESSAGE),
                    (skipped, "unskip", {}, SOURCE_CLEANED_MESSAGE),
                    (skipped, "skip", {}, SOURCE_CLEANED_MESSAGE),
                    (skipped, "review/finalize", {}, SKIPPED_REFUSAL),
                ]
                expected += [(done, f"review/{action}", body, SOURCE_CLEANED_REVIEW_REFUSAL)
                             for action, body in self.EDITS]
                expected += [(skipped, f"review/{action}", body, SOURCE_CLEANED_REVIEW_REFUSAL)
                             for action, body in self.EDITS]
                before = {job_id: self.frozen(job_id) for job_id in (done, skipped)}
                patchers = self.refuse_writes()
                try:
                    for job_id, action, body, message in expected:
                        with self.subTest(job=job_id, action=action):
                            status, payload = self.post(f"/api/jobs/{job_id}/{action}", body=body)
                            self.assertEqual((status, payload.get("error")), (400, message))
                            self.assertEqual(self.frozen(job_id), before[job_id])
                finally:
                    for patcher in patchers:
                        patcher.stop()
                self.assertEqual(self.events(done, "EXPORT_QUEUED"), [])
                # The direct calls refuse the same way.
                for call, job_id, message in (
                    (self.center.skip_export, done, SOURCE_CLEANED_MESSAGE),
                    (self.center.unskip_export, skipped, SOURCE_CLEANED_MESSAGE),
                    (self.center.ensure_review_editable, done, SOURCE_CLEANED_REVIEW_REFUSAL),
                    (self.finalize, done, SOURCE_CLEANED_MESSAGE),
                ):
                    with self.assertRaises(ValueError) as caught:
                        call(job_id)
                    self.assertEqual(str(caught.exception), message)

    def test_cleaned_media_answers_410_and_the_page_knows_why(self):
        for latest in ("RECYCLED", "PENDING"):
            with self.subTest(latest=latest):
                job_id = self.make_job(f"tap12-{latest}", [item("a", "KEEP")], state="COMPLETED")
                status, evidence = self.get(f"/api/jobs/{job_id}/review/evidence?item=a")
                self.assertEqual((status, evidence["video"]["reason"]), (200, None))
                frame_t = evidence["frames"][0]["t"]
                self.clean(job_id, latest)
                key = self.center.media_key(job_id)
                status, body = self.get(f"/api/jobs/{job_id}/review/video?k={key}")
                self.assertEqual((status, body), (410, {"error": SOURCE_CLEANED_MEDIA_MESSAGE}))
                status, body = self.get(f"/api/jobs/{job_id}/review/frame?item=a&t={frame_t}&k={key}")
                self.assertEqual((status, body), (410, {"error": SOURCE_CLEANED_MEDIA_MESSAGE}))
                # The media key is still checked first.
                self.assertEqual(self.get(f"/api/jobs/{job_id}/review/video?k=wrong")[0], 403)
                status, evidence = self.get(f"/api/jobs/{job_id}/review/evidence?item=a")
                self.assertEqual(status, 200)
                self.assertEqual(evidence["video"], {"available": False, "mime": None, "reason": "source_cleaned"})
                status, export = self.get(f"/api/jobs/{job_id}/review/export")
                self.assertEqual(status, 200)
                self.assertIs(export["source_cleaned"], True)
                self.assertEqual(export["source_name"], f"tap12-{latest}.mp4")
                self.assertEqual(export["status"], "COMPLETED")

    def test_review_evidence_reasons(self):
        job_id = self.make_job("reasons", [item("a", "KEEP")])
        source = Path(self.store.get_job(job_id)["source_path"])
        source.write_bytes(b"changed" * 100)
        self.assertEqual(self.center.review_evidence(job_id, "a")["video"]["reason"], "source_changed")
        source.unlink()
        self.assertEqual(self.center.review_evidence(job_id, "a")["video"]["reason"], "source_missing")
        self.clean(job_id, "PENDING")
        self.assertEqual(self.center.review_evidence(job_id, "a")["video"]["reason"], "source_cleaned")

    def test_a_failed_cleanup_locks_nothing(self):
        job_id = self.make_job("failed", [item("a", "KEEP")])
        self.clean(job_id, "FAILED")
        self.assertFalse(self.store.source_cleaned(job_id))
        status, export = self.get(f"/api/jobs/{job_id}/review/export")
        self.assertEqual((status, export["source_cleaned"], export["source_name"]), (200, False, "failed.mp4"))
        status, evidence = self.get(f"/api/jobs/{job_id}/review/evidence?item=a")
        self.assertEqual((status, evidence["video"]["available"]), (200, True))
        status, body = self.get(f"/api/jobs/{job_id}/review/video?k={self.center.media_key(job_id)}")
        self.assertEqual((status, body), (200, b"failed" * 64))
        self.center.ensure_review_editable(job_id)
        ready = {"status": "READY_FOR_EDIT_PLAN", "items": [item("a", "KEEP")]}
        with patch("biliflow.control_center.record_review_decision", return_value=ready) as record:
            status, payload = self.post(f"/api/jobs/{job_id}/review/decision", body={"id": "a", "decision": "KEEP"})
        self.assertEqual((status, record.call_count), (200, 1), payload)
        self.assertEqual(self.center.skip_export(job_id)["state"], "SKIPPED")
        self.assertEqual(self.center.unskip_export(job_id)["state"], "READY_TO_EXPORT")
        self.assertEqual(self.finalize(job_id)["status"], "QUEUED")


if __name__ == "__main__":
    unittest.main()
