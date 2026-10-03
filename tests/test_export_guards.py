import hashlib
import os
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow import export_guards as guards
from biliflow.export_guards import (
    BUSY_EXPORT_MESSAGE,
    CONTROL_CENTER_JOB_MESSAGE,
    CONTROL_CENTER_STATE_UNREADABLE,
    EXPORT_IN_FLIGHT_MESSAGE,
    QUEUE_NOT_READY_MESSAGE,
    REVIEW_EDIT_IN_FLIGHT_MESSAGE,
    SKIPPED_REFUSAL,
    SOURCE_CLEANED_MESSAGE,
    SOURCE_CLEANED_REVIEW_REFUSAL,
    SOURCE_MISSING_MESSAGE,
    STANDALONE_SKIPPED_EDIT_REFUSAL,
    control_center_job_facts,
    export_source_refusal,
    export_state_refusal,
    render_in_flight,
    review_summary,
    skip_refusal,
    standalone_edit_refusal,
    standalone_export_refusal,
)
from biliflow.job_store import JobStore


_PATCHERS = []


def setUpModule():
    # Contract section 14 (R13): a module that imports control_center makes any
    # real Recycle Bin call fail. recycle_bin arrives in step A5, so it is optional.
    try:
        from biliflow import recycle_bin
    except ImportError:
        return
    if hasattr(recycle_bin, "_shell_delete"):
        patcher = patch.object(
            recycle_bin, "_shell_delete",
            side_effect=AssertionError("real Recycle Bin call in a test"),
        )
        patcher.start()
        _PATCHERS.append(patcher)


def tearDownModule():
    while _PATCHERS:
        _PATCHERS.pop().stop()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# (case, job state, current_stage, next pending stage, worker busy,
#  render_in_flight, export_state_refusal)
STATE_TABLE = (
    ("ready", "READY_TO_EXPORT", None, None, False, False, None),
    ("ready with a stale render", "READY_TO_EXPORT", None, "render", False, False, None),
    ("skipped", "SKIPPED", None, None, False, False, SKIPPED_REFUSAL),
    ("skipped with a stale render", "SKIPPED", "render", "render", False, False, SKIPPED_REFUSAL),
    ("queued export", "QUEUED", "render", "render", False, True, EXPORT_IN_FLIGHT_MESSAGE),
    ("queued scan", "QUEUED", None, "preflight", False, False, "busy"),
    ("queued, next stage render", "QUEUED", None, "render", False, True, EXPORT_IN_FLIGHT_MESSAGE),
    ("rendering", "RENDERING", "render", None, False, True, EXPORT_IN_FLIGHT_MESSAGE),
    ("verifying", "VERIFYING", "render", None, False, True, EXPORT_IN_FLIGHT_MESSAGE),
    ("scanning", "SCANNING_TEXT", "text", None, False, False, "busy"),
    ("worker finishing a render", "READY_TO_EXPORT", "render", None, True, True, EXPORT_IN_FLIGHT_MESSAGE),
    ("worker finishing a scan", "WAITING_REVIEW", "build_review", None, True, False, "busy"),
    ("cancelled export", "CANCELLED", "render", None, False, False, None),
    ("paused export", "PAUSED", "render", "render", False, False, None),
    ("completed", "COMPLETED", None, None, False, False, None),
)


class StateGuardTests(unittest.TestCase):
    def test_render_in_flight_and_export_state_refusal_table(self):
        for case, state, stage, pending, busy, flight, refusal in STATE_TABLE:
            with self.subTest(case=case):
                job = {"id": 7, "state": state, "current_stage": stage}
                self.assertIs(render_in_flight(job, pending, worker_busy=busy), flight)
                expected = BUSY_EXPORT_MESSAGE.format(job_id=7) if refusal == "busy" else refusal
                self.assertEqual(
                    export_state_refusal(job, in_flight=flight, worker_busy=busy), expected,
                )

    def test_busy_message_names_the_job(self):
        self.assertEqual(
            export_state_refusal({"id": 42, "state": "QUEUED"}, in_flight=False, worker_busy=False),
            "Video #42 đang trong hàng đợi hoặc đang được xử lý; chờ xong rồi hãy xuất.",
        )

    def test_render_in_flight_matches_the_control_center_check(self):
        from biliflow.control_center import ControlCenter

        class Store:
            def __init__(self, pending):
                self.pending = pending

            def next_pending_stage(self, job_id):
                return None if self.pending is None else {"name": self.pending}

        class Scheduler:
            def __init__(self, busy):
                self.busy = busy

            def is_busy(self, job_id):
                return self.busy

        for case, state, stage, pending, busy, flight, _refusal in STATE_TABLE:
            with self.subTest(case=case):
                center = ControlCenter.__new__(ControlCenter)
                center.store = Store(pending)
                center.scheduler = Scheduler(busy)
                job = {"id": 7, "state": state, "current_stage": stage}
                self.assertIs(center._export_in_flight(job), flight)

    def test_export_source_refusal(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "video.mp4"
            self.assertEqual(export_source_refusal(source, cleaned=False), SOURCE_MISSING_MESSAGE)
            self.assertEqual(export_source_refusal(source, cleaned=True), SOURCE_CLEANED_MESSAGE)
            source.write_bytes(b"video")
            self.assertIsNone(export_source_refusal(source, cleaned=False))
            # A cleaned job stays locked even after a file with that name came back.
            self.assertEqual(export_source_refusal(source, cleaned=True), SOURCE_CLEANED_MESSAGE)
            self.assertEqual(export_source_refusal(Path(directory), cleaned=False), SOURCE_MISSING_MESSAGE)


def item(item_id, decision=None):
    return {"id": item_id, "start_seconds": 1.0, "end_seconds": 2.0, "decision": decision}


SUMMARY_FIXTURES = (
    {},
    {"status": "READY_FOR_EDIT_PLAN", "items": []},
    {"status": "READY_FOR_EDIT_PLAN", "items": [], "advisory_items": [item("x")]},
    {"status": "READY_FOR_EDIT_PLAN", "items": [item("a", "KEEP"), item("b", "KEEP")]},
    {"status": "READY_FOR_EDIT_PLAN", "items": [item("a", "KEEP"), item("b", "BLUR"), item("c", "CUT"),
                                                item("d", "BLUR")]},
    {"status": "READY_FOR_EDIT_PLAN", "items": [item("a", "NEEDS_MORE_CONTEXT"), item("b", "OTHER")]},
    {"status": "REVIEW_REQUIRED", "items": [item("a", "KEEP"), item("b")]},
    {"status": "REVIEW_REQUIRED", "items": [item("a", "KEEP")]},
    {"status": "READY_FOR_EDIT_PLAN", "items": [item("a", "KEEP")],
     "export_size_policy": {"mode": "custom", "maximum_output_gb": 4.5, "maximum_output_bytes": 1}},
    {"status": "READY_FOR_EDIT_PLAN", "items": [item("a", "KEEP")], "export_size_policy": "bad"},
)


class ReviewSummaryParityTests(unittest.TestCase):
    """The copies match control_center byte for byte (after B5 they are the same objects)."""

    def test_same_summary_and_skip_refusal_as_the_control_center(self):
        from biliflow import control_center as cc

        self.assertEqual(guards.DECISION_LABELS, cc.DECISION_LABELS)
        for index, queue in enumerate(SUMMARY_FIXTURES):
            with self.subTest(fixture=index):
                summary = review_summary(queue)
                self.assertEqual(summary, cc.review_summary(queue))
                self.assertEqual(skip_refusal(summary), cc.skip_refusal(summary))
                self.assertEqual(summary["skip_eligible"], skip_refusal(summary) is None)

    def test_skip_refusal_names_the_other_decisions(self):
        summary = review_summary(SUMMARY_FIXTURES[4])
        self.assertEqual(summary["decisions"], {"BLUR": 2, "CUT": 1, "KEEP": 1})
        self.assertEqual(
            skip_refusal(summary),
            "Video có cảnh chính không phải Giữ nguyên (2 Làm mờ, 1 Cắt cảnh); "
            "hãy xuất video thay vì bỏ qua.",
        )
        self.assertIsNone(skip_refusal(review_summary(SUMMARY_FIXTURES[2])))


SHA = "ab" * 32


class ControlCenterDatabase(unittest.TestCase):
    """A temp project root whose Control Center DB is written by a real JobStore, then closed."""

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / "input").mkdir()
        self.source = self.root / "input" / "video.mp4"
        self.source.write_bytes(b"video")
        self.database = self.root / "state" / "control-center.sqlite3"
        store = JobStore(self.database)
        try:
            self.job_id = int(store.upsert_job(
                job_key="video-abababab", source_path=self.source, source_sha256=SHA,
                source_size_bytes=5, source_mtime_ns=self.source.stat().st_mtime_ns,
                content_style="animation", state="READY_TO_EXPORT",
            )["id"])
            store.upsert_job(
                job_key="other", source_path=self.root / "input" / "other.mp4", source_sha256="cd" * 32,
                source_size_bytes=5, source_mtime_ns=1,
            )
        finally:
            store.close()

    def store(self):
        store = JobStore(self.database)
        self.addCleanup(store.close)
        return store

    def change(self, **values):
        store = JobStore(self.database)
        try:
            store.update_job(self.job_id, **values)
        finally:
            store.close()

    def queue_render(self, *, current_stage="render"):
        store = JobStore(self.database)
        try:
            store.ensure_stage(self.job_id, "render")
            store.mark_queued(self.job_id, reseq=True, current_stage=current_stage)
        finally:
            store.close()

    def add_cleanup(self, state):
        """Append a source_cleanups row that ends in ``state``, through the real JobStore."""
        store = JobStore(self.database)
        try:
            row_id = store.add_source_cleanup(
                job_id=self.job_id, kind="EXPORTED", source_path=str(self.source),
                source_sha256=SHA, size_bytes=5, mtime_ns=1,
            )
            if state != "PENDING":
                store.finish_source_cleanup(row_id, state="FAILED" if state == "FAILED" else "RECYCLED")
            if state == "RESTORED":
                store.mark_source_restored(row_id, mtime_ns=2)
            self.assertEqual(store.latest_source_cleanup(self.job_id)["state"], state)
        finally:
            store.close()

    def queue(self, *, status="READY_FOR_EDIT_PLAN", sha=SHA, path=None):
        return {"status": status, "items": [],
                "source": {"path": str(path or self.source), "sha256": sha}}


class ControlCenterJobFactsTests(ControlCenterDatabase):
    def test_no_database_or_no_matching_job_gives_none(self):
        with TemporaryDirectory() as other:
            self.assertIsNone(control_center_job_facts(Path(other), SHA))
        self.assertIsNone(control_center_job_facts(self.root, "ef" * 32))
        self.assertIsNone(control_center_job_facts(self.root, ""))

    def test_facts_of_a_waiting_export_match_the_sha_case_insensitively(self):
        self.queue_render()
        facts = control_center_job_facts(self.root, SHA.upper())
        self.assertEqual(facts, {
            "job": {"id": self.job_id, "state": "QUEUED", "current_stage": "render"},
            "next_pending_stage": "render", "render_request": True, "cleaned": False,
        })

    def test_cleaned_follows_the_latest_cleanup_row(self):
        self.assertFalse(control_center_job_facts(self.root, SHA)["cleaned"])
        for state, cleaned in (("PENDING", True), ("FAILED", False), ("RECYCLED", True),
                               ("RESTORED", False), ("PENDING", True)):
            with self.subTest(state=state):
                self.add_cleanup(state)
                self.assertIs(control_center_job_facts(self.root, SHA)["cleaned"], cleaned)

    def test_a_database_without_the_cleanup_table_is_not_cleaned(self):
        self.add_cleanup("RECYCLED")
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("DROP TABLE source_cleanups")
            connection.commit()
        finally:
            connection.close()
        before = digest(self.database)
        facts = control_center_job_facts(self.root, SHA)
        self.assertEqual(facts["job"]["state"], "READY_TO_EXPORT")
        self.assertFalse(facts["cleaned"])
        self.assertIsNone(facts["next_pending_stage"])
        # The read never creates the table (or anything else) in the database.
        self.assertEqual(digest(self.database), before)

    def test_reading_never_changes_the_database_bytes(self):
        self.queue_render()
        self.add_cleanup("PENDING")
        before = digest(self.database)
        for _ in range(3):
            control_center_job_facts(self.root, SHA)
            standalone_edit_refusal(self.root, self.queue())
            standalone_export_refusal(self.root, self.queue())
        self.assertEqual(digest(self.database), before)

    def test_an_unreadable_database_fails_closed(self):
        self.database.write_bytes(b"this is not a sqlite database" * 100)
        with self.assertRaises(ValueError) as caught:
            control_center_job_facts(self.root, SHA)
        self.assertEqual(str(caught.exception), CONTROL_CENTER_STATE_UNREADABLE)
        self.assertIsInstance(caught.exception.__cause__, sqlite3.Error)

    def test_a_database_without_jobs_fails_closed(self):
        self.database.unlink()
        sqlite3.connect(self.database).close()
        with self.assertRaisesRegex(ValueError, "Không đọc được trạng thái Control Center"):
            control_center_job_facts(self.root, SHA)


class StandaloneRefusalTests(ControlCenterDatabase):
    def test_export_refusal_order(self):
        # (1) the queue status comes first, before the database is read.
        self.database.write_bytes(b"garbage" * 100)
        self.assertEqual(
            standalone_export_refusal(self.root, self.queue(status="REVIEW_REQUIRED")),
            QUEUE_NOT_READY_MESSAGE,
        )
        # (2) an unreadable database fails closed.
        with self.assertRaisesRegex(ValueError, "hãy xuất video từ Dashboard"):
            standalone_export_refusal(self.root, self.queue())
        # R10: a queue without a source hash never consults the database.
        self.assertIsNone(standalone_export_refusal(self.root, self.queue(sha="")))

    def test_a_video_the_control_center_owns_is_exported_from_the_dashboard(self):
        for state in ("READY_TO_EXPORT", "SKIPPED", "COMPLETED", "WAITING_REVIEW"):
            with self.subTest(state=state):
                self.change(state=state)
                self.assertEqual(
                    standalone_export_refusal(self.root, self.queue()),
                    CONTROL_CENTER_JOB_MESSAGE.format(job_id=self.job_id, state=state),
                )
        # The message is checked before the source: a missing source changes nothing.
        self.source.unlink()
        self.assertEqual(
            standalone_export_refusal(self.root, self.queue()),
            f"Video này thuộc job #{self.job_id} của Control Center (WAITING_REVIEW); hãy xuất từ "
            "Dashboard để giữ đúng hàng đợi, trạng thái bỏ qua và dọn video gốc.",
        )

    def test_a_video_without_a_job_needs_its_source(self):
        other = self.root / "input" / "free.mp4"
        queue = self.queue(sha="ef" * 32, path=other)
        self.assertEqual(standalone_export_refusal(self.root, queue), SOURCE_MISSING_MESSAGE)
        other.write_bytes(b"free")
        self.assertIsNone(standalone_export_refusal(self.root, queue))
        # Without any Control Center database the source check still runs.
        self.database.unlink()
        self.assertIsNone(standalone_export_refusal(self.root, queue))
        other.unlink()
        self.assertEqual(standalone_export_refusal(self.root, queue), SOURCE_MISSING_MESSAGE)
        self.assertEqual(
            standalone_export_refusal(self.root, {"status": "READY_FOR_EDIT_PLAN"}),
            SOURCE_MISSING_MESSAGE,
        )

    def test_edit_refusal_order(self):
        # No Control Center job: the standalone page may edit.
        self.assertIsNone(standalone_edit_refusal(self.root, self.queue(sha="ef" * 32)))
        self.assertIsNone(standalone_edit_refusal(self.root, self.queue(sha="")))
        self.assertIsNone(standalone_edit_refusal(self.root, self.queue()))
        for state in ("WAITING_REVIEW", "COMPLETED", "CANCELLED"):
            self.change(state=state)
            self.assertIsNone(standalone_edit_refusal(self.root, self.queue()), state)
        # A skipped video is reopened from the Dashboard first.
        self.change(state="SKIPPED")
        self.assertEqual(standalone_edit_refusal(self.root, self.queue()), STANDALONE_SKIPPED_EDIT_REFUSAL)
        # A cleaned source comes before the skip.
        self.add_cleanup("RECYCLED")
        self.assertEqual(standalone_edit_refusal(self.root, self.queue()), SOURCE_CLEANED_REVIEW_REFUSAL)
        # An export waiting or running comes first of all.
        self.queue_render()
        self.assertEqual(standalone_edit_refusal(self.root, self.queue()), REVIEW_EDIT_IN_FLIGHT_MESSAGE)
        self.change(state="RENDERING")
        self.assertEqual(standalone_edit_refusal(self.root, self.queue()), REVIEW_EDIT_IN_FLIGHT_MESSAGE)
        # A waiting scan is not an export.
        self.change(state="QUEUED", current_stage=None)
        store = JobStore(self.database)
        try:
            store.update_stage(self.job_id, "render", state="CANCELLED")
        finally:
            store.close()
        self.assertEqual(standalone_edit_refusal(self.root, self.queue()), SOURCE_CLEANED_REVIEW_REFUSAL)

    def set_render_stage(self, state):
        store = JobStore(self.database)
        try:
            store.ensure_stage(self.job_id, "render")
            store.update_stage(self.job_id, "render", state=state)
        finally:
            store.close()

    def test_an_export_request_that_resume_or_retry_would_run_refuses_edits(self):
        # Fix pass: a paused, failed or interrupted export keeps its render stage
        # (the plan fixed at finalize). The Dashboard retires it when a decision
        # changes; review-ui has no scheduler, so it refuses until Hủy retires it.
        for job_state, stage_state in (
            ("PAUSED", "PENDING"), ("INTERRUPTED_RECOVERABLE", "PENDING"),
            ("FAILED", "FAILED"), ("FAILED", "FAILED_RETRYABLE"), ("PAUSED", "RUNNING"),
        ):
            with self.subTest(job=job_state, stage=stage_state):
                self.change(state=job_state, current_stage="render")
                self.set_render_stage(stage_state)
                self.assertFalse(render_in_flight(
                    {"state": job_state, "current_stage": "render"}, "render", worker_busy=False))
                self.assertIs(control_center_job_facts(self.root, SHA)["render_request"], True)
                self.assertEqual(standalone_edit_refusal(self.root, self.queue()), REVIEW_EDIT_IN_FLIGHT_MESSAGE)
        # Hủy (or a Dashboard edit) retires the request: the stage is CANCELLED.
        self.set_render_stage("CANCELLED")
        self.assertIs(control_center_job_facts(self.root, SHA)["render_request"], False)
        self.assertIsNone(standalone_edit_refusal(self.root, self.queue()))
        # A finished export is no request.
        self.change(state="COMPLETED", current_stage=None)
        self.set_render_stage("COMPLETED")
        self.assertIsNone(standalone_edit_refusal(self.root, self.queue()))
        # An old request in a state Tiếp tục refuses to run is not refused here.
        for job_state in ("CANCELLED", "READY_TO_EXPORT", "WAITING_REVIEW"):
            with self.subTest(stale=job_state):
                self.change(state=job_state, current_stage=None)
                self.set_render_stage("PENDING")
                self.assertIsNone(standalone_edit_refusal(self.root, self.queue()))

    def test_a_paused_export_of_the_real_scheduler_refuses_edits(self):
        # The reviewer's repro: Xuất video, then "Dừng sau bước này" before the
        # render starts. Tiếp tục would render the plan fixed at that finalize.
        from biliflow.scheduler import JobScheduler

        store = JobStore(self.database)
        try:
            scheduler = JobScheduler(self.root, store)
            (self.root / "work").mkdir(exist_ok=True)
            (self.root / "output").mkdir(exist_ok=True)
            scheduler.queue_render(
                self.job_id, plan_path=self.root / "work" / "plan.json",
                output_path=self.root / "output" / "video-reviewed.mp4",
            )
            scheduler.stop_after_stage(self.job_id)
            job = store.get_job(self.job_id)
            pending = store.next_pending_stage(self.job_id)
        finally:
            store.close()
        self.assertEqual((job["state"], job["current_stage"], pending["name"]), ("PAUSED", "render", "render"))
        self.assertEqual(standalone_edit_refusal(self.root, self.queue()), REVIEW_EDIT_IN_FLIGHT_MESSAGE)
        # Hủy retires the request; the page may edit again.
        store = JobStore(self.database)
        try:
            JobScheduler(self.root, store).cancel(self.job_id)
        finally:
            store.close()
        self.assertIsNone(standalone_edit_refusal(self.root, self.queue()))

    def test_resumable_export_states_are_the_schedulers(self):
        from biliflow import scheduler

        self.assertIs(scheduler.RESUMABLE_EXPORT_STATES, guards.RESUMABLE_EXPORT_STATES)
        self.assertEqual(guards.RESUMABLE_EXPORT_STATES,
                         frozenset({"PAUSED", "FAILED", "INTERRUPTED_RECOVERABLE", "QUEUED"}))

    def test_edits_fail_closed_on_an_unreadable_database(self):
        self.database.write_bytes(b"garbage" * 100)
        with self.assertRaises(ValueError) as caught:
            standalone_edit_refusal(self.root, self.queue())
        self.assertEqual(str(caught.exception), CONTROL_CENTER_STATE_UNREADABLE)


class ImportIsolationTests(unittest.TestCase):
    def test_import_pulls_in_no_server_or_scheduler_module(self):
        source_root = Path(guards.__file__).resolve().parents[1]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(source_root)
        code = (
            "import sys, biliflow.export_guards as g; "
            "assert g.__file__.startswith(sys.argv[1]), g.__file__; "
            "bad = [m for m in ('biliflow.control_center', 'biliflow.review_workflow', "
            "'biliflow.scheduler', 'biliflow.source_cleanup', 'biliflow.recycle_bin') if m in sys.modules]; "
            "assert not bad, bad; print('ok')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, str(source_root)], capture_output=True, text=True,
            env=environment, timeout=60, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ok")

    def test_scheduler_reexports_the_skip_refusals(self):
        from biliflow import scheduler

        self.assertIs(scheduler.SKIPPED_REFUSAL, guards.SKIPPED_REFUSAL)
        self.assertIs(scheduler.SKIPPED_STOP_REFUSAL, guards.SKIPPED_STOP_REFUSAL)


if __name__ == "__main__":
    unittest.main()
