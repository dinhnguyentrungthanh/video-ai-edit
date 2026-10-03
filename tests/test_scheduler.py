import hashlib
import json
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow.export_guards import ActionConflict, SOURCE_ARCHIVED_MESSAGE, SOURCE_ARCHIVED_STOP_REFUSAL
from biliflow.job_pipeline import PipelineStage, StageCommand
from biliflow.job_store import JobStore, sha256_file
from biliflow.scheduler import (
    EXPORT_RETIRE_MESSAGES,
    RESUME_SETTLED_REFUSAL,
    RESUME_STALE_EXPORT_REFUSAL,
    SKIPPED_REFUSAL,
    SKIPPED_STOP_REFUSAL,
    SOURCE_CLEANED_MESSAGE,
    SOURCE_CLEANED_STOP_REFUSAL,
    SOURCE_RESTORE_REJECTED_MESSAGE,
    SOURCE_RESTORE_WRONG_PATH_MESSAGE,
    SOURCE_RESTORED_MESSAGE,
    InputWatcher,
    JobScheduler,
)
from biliflow.final_renderer import render_progress_path


class SchedulerTests(unittest.TestCase):
    def test_exact_stage_cache_skips_subprocess_and_restores_new_revision(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in (
                "input", "reports/jobs", "logs", "config", "scripts",
                "src/biliflow",
            ):
                (root / name).mkdir(parents=True, exist_ok=True)
            (root / "src" / "biliflow" / "worker.py").write_text(
                "VERSION = 1\n", encoding="utf-8"
            )
            (root / "config" / "processing_profiles.json").write_text(
                "{}", encoding="utf-8"
            )
            (root / "scripts" / "run.ps1").write_text("run", encoding="utf-8")
            for name in ("scanner", "license_policy", "control_center"):
                (root / "src/biliflow" / (name + ".py")).write_text("# original", encoding="utf-8")
            source = root / "input" / "video.mp4"
            source.write_bytes(b"video")
            source_sha = "a" * 64
            store = JobStore(root / "jobs.sqlite3")
            job = store.upsert_job(
                job_key="run-2", source_path=source,
                source_sha256=source_sha, source_size_bytes=5,
                source_mtime_ns=source.stat().st_mtime_ns,
                content_style="live_action", state="QUEUED",
            )
            store.replace_stages(job["id"], ["adult"])
            scheduler = JobScheduler(root, store)
            old_root = root / "reports" / "jobs" / "run-1"
            old_artifact = old_root / "adult" / "scan.json"
            (old_artifact.parent / "thumbnails").mkdir(parents=True)
            (old_artifact.parent / "thumbnails" / "frame.jpg").write_bytes(b"jpg")
            old_artifact.write_text(
                json.dumps({"status": "COMPLETED", "input_sha256": source_sha}),
                encoding="utf-8",
            )
            old_command = (
                "missing-scanner", "--input", str(source),
                "--report-dir", str(old_artifact.parent),
            )
            scheduler._stage_cache.store(
                stage_name="adult", source_sha256=source_sha,
                source_path=source, report_root=old_root,
                commands=(old_command,), artifact_paths=(old_artifact,),
            )
            (root / "src/biliflow/control_center.py").write_text("# UI update", encoding="utf-8")
            new_root = root / "reports" / "jobs" / "run-2"
            new_artifact = new_root / "adult" / "scan.json"
            new_command = (
                "missing-scanner", "--input", str(source),
                "--report-dir", str(new_artifact.parent),
            )
            definition = PipelineStage(
                "adult", "SCANNING_SAFETY",
                (StageCommand(new_command, (new_artifact,)),), uses_gpu=True,
            )
            try:
                scheduler._execute(
                    store.get_job(job["id"]), store.stage(job["id"], "adult"), definition
                )
                self.assertEqual(store.stage(job["id"], "adult")["state"], "COMPLETED")
                self.assertTrue(new_artifact.is_file())
                self.assertTrue((new_artifact.parent / "thumbnails" / "frame.jpg").is_file())
                events = store.events(job["id"])
                self.assertTrue(any(item["event_type"] == "STAGE_CACHE_HIT" for item in events))
            finally:
                store.close()

    def test_interrupted_render_removes_only_its_partial_output(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("input", "reports", "work", "output", "logs", "config", "scripts"):
                (root / name).mkdir(parents=True, exist_ok=True)
            source = root / "input" / "video.mp4"
            source.write_bytes(b"video")
            store = JobStore(root / "jobs.sqlite3")
            job = store.upsert_job(
                job_key="video-11111111", source_path=source,
                source_sha256="1" * 64, source_size_bytes=5,
                source_mtime_ns=source.stat().st_mtime_ns,
                content_style="animation", state="READY_TO_EXPORT",
            )
            scheduler = JobScheduler(root, store)
            plan = root / "work" / "plan.json"
            output = root / "output" / "out.mp4"
            partial = root / "output" / "out.partial.mp4"
            partial.write_bytes(b"incomplete")
            progress = render_progress_path(root, output)
            progress.parent.mkdir(parents=True)
            progress.write_text("progress=continue\n", encoding="utf-8")
            outside = root / "work" / "keep.partial.mp4"
            outside.write_bytes(b"keep")
            try:
                scheduler.queue_render(
                    job["id"], plan_path=plan, output_path=output,
                    max_output_bytes=None, target_output_bytes=None,
                )
                scheduler._cleanup_interrupted_stage(job["id"], "render")
                self.assertFalse(partial.exists())
                self.assertFalse(progress.exists())
                self.assertTrue(outside.exists())
            finally:
                store.close()

    def test_render_command_preserves_each_jobs_size_policy(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("input", "reports", "work", "output", "logs", "config", "scripts"):
                (root / name).mkdir(parents=True, exist_ok=True)
            source = root / "input" / "video.mp4"
            source.write_bytes(b"video")
            store = JobStore(root / "jobs.sqlite3")
            job = store.upsert_job(
                job_key="video-11111111", source_path=source,
                source_sha256="1" * 64, source_size_bytes=5,
                source_mtime_ns=source.stat().st_mtime_ns,
                content_style="animation", state="READY_TO_EXPORT",
            )
            scheduler = JobScheduler(root, store)
            plan = root / "work" / "plan.json"
            output = root / "output" / "out.mp4"
            try:
                scheduler.queue_render(
                    job["id"], plan_path=plan, output_path=output,
                    max_output_bytes=5_000_000_000,
                    target_output_bytes=4_700_000_000,
                )
                with patch("biliflow.scheduler.pipeline_stages", return_value=[]):
                    command = scheduler._definitions(store.get_job(job["id"]))["render"].commands[0].argv
                self.assertIn("5000000000", command)
                self.assertIn("4700000000", command)
                self.assertNotIn("--no-output-size-limit", command)

                scheduler.queue_render(
                    job["id"], plan_path=plan, output_path=output,
                    max_output_bytes=None, target_output_bytes=None,
                )
                with patch("biliflow.scheduler.pipeline_stages", return_value=[]):
                    command = scheduler._definitions(store.get_job(job["id"]))["render"].commands[0].argv
                self.assertIn("--no-output-size-limit", command)
                self.assertNotIn("--max-output-bytes", command)
            finally:
                store.close()

    def test_jobs_queued_before_verify_adult_keep_their_stage_list(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("input", "reports/jobs", "logs", "config", "scripts"):
                (root / name).mkdir(parents=True, exist_ok=True)
            source = root / "input" / "video.mp4"
            source.write_bytes(b"video")
            store = JobStore(root / "jobs.sqlite3")
            job = store.upsert_job(
                job_key="video-22222222", source_path=source, source_sha256="2" * 64,
                source_size_bytes=5, source_mtime_ns=source.stat().st_mtime_ns,
                content_style="live_action", state="QUEUED",
            )
            scheduler = JobScheduler(root, store)
            calls = []

            def fake(**kwargs):
                calls.append(kwargs.get("adult_verification"))
                return []
            try:
                store.replace_stages(job["id"], ["preflight", "adult", "build_review"])  # old stored list
                with patch("biliflow.scheduler.pipeline_stages", side_effect=fake):
                    scheduler._definitions(store.get_job(job["id"]))
                store.replace_stages(job["id"], ["preflight", "adult", "verify_adult", "build_review"])
                with patch("biliflow.scheduler.pipeline_stages", side_effect=fake):
                    scheduler._definitions(store.get_job(job["id"]))
                self.assertEqual(calls, [False, None])
            finally:
                store.close()

    def test_start_job_queues_only_a_video_waiting_for_setup(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("input", "reports/jobs", "logs", "config", "scripts"):
                (root / name).mkdir(parents=True, exist_ok=True)
            source = root / "input" / "video.mp4"
            source.write_bytes(b"video")
            store = JobStore(root / "jobs.sqlite3")
            job = store.upsert_job(
                job_key="video-11111111", source_path=source,
                source_sha256="1" * 64, source_size_bytes=5,
                source_mtime_ns=source.stat().st_mtime_ns,
                state="NEEDS_METADATA",
            )
            scheduler = JobScheduler(root, store)
            definitions = [PipelineStage("preflight", "PREFLIGHT", tuple())]

            def queued():
                return sum(
                    1 for event in store.events(job["id"])
                    if event["event_type"] == "JOB_QUEUED"
                )

            try:
                with patch("biliflow.scheduler.pipeline_stages", return_value=definitions):
                    scheduler.start_job(
                        job["id"], content_style="live_action", profile="careful",
                        detector_groups=["advertising"],
                    )
                    self.assertEqual(store.get_job(job["id"])["state"], "QUEUED")
                    for state in ("QUEUED", "SCANNING_LOGO", "WAITING_REVIEW", "COMPLETED"):
                        store.update_job(job["id"], state=state)
                        with self.assertRaisesRegex(ValueError, "không xếp hàng lại"):
                            scheduler.start_job(
                                job["id"], content_style="animation", profile="fast",
                                detector_groups=["advertising", "adult", "gore", "violence"],
                            )
                        self.assertEqual(store.get_job(job["id"])["state"], state)
                self.assertEqual(queued(), 1)
                self.assertEqual(
                    store.setting(f"detector_groups:{job['id']}"), ["advertising"],
                )
                self.assertEqual(store.get_job(job["id"])["content_style"], "live_action")
            finally:
                store.close()

    def test_detector_groups_are_persisted_and_passed_to_pipeline(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("input", "reports/jobs", "logs", "config", "scripts"):
                (root / name).mkdir(parents=True, exist_ok=True)
            source = root / "input" / "video.mp4"
            source.write_bytes(b"video")
            store = JobStore(root / "jobs.sqlite3")
            job = store.upsert_job(
                job_key="video-11111111", source_path=source,
                source_sha256="1" * 64, source_size_bytes=5,
                source_mtime_ns=source.stat().st_mtime_ns,
            )
            scheduler = JobScheduler(root, store)
            definitions = [PipelineStage("preflight", "PREFLIGHT", tuple())]
            try:
                with patch(
                    "biliflow.scheduler.pipeline_stages",
                    return_value=definitions,
                ) as build:
                    scheduler.configure_and_queue(
                        job["id"], content_style="animation", profile="fast",
                        detector_groups=["advertising"],
                    )
                self.assertEqual(
                    store.setting(f"detector_groups:{job['id']}"),
                    ["advertising"],
                )
                self.assertEqual(
                    build.call_args.kwargs["detector_groups"],
                    ("advertising",),
                )
            finally:
                store.close()

    def test_rerun_uses_new_report_revision_and_clears_active_queue(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("input", "reports/jobs", "logs", "config", "scripts"):
                (root / name).mkdir(parents=True, exist_ok=True)
            source = root / "input" / "video.mp4"
            source.write_bytes(b"video")
            store = JobStore(root / "jobs.sqlite3")
            job = store.upsert_job(
                job_key="video-11111111", source_path=source,
                source_sha256="1" * 64, source_size_bytes=5,
                source_mtime_ns=source.stat().st_mtime_ns,
                content_style="animation", state="WAITING_REVIEW",
            )
            store.update_job(
                job["id"], active_queue_path="reports/old/review-queue.json",
                active_revision=1,
            )
            scheduler = JobScheduler(root, store)
            definitions = [PipelineStage("preflight", "PREFLIGHT", tuple())]
            try:
                with patch("biliflow.scheduler.pipeline_stages", return_value=definitions):
                    value = scheduler.rerun(
                        job["id"], detector_groups=["gore"],
                    )
                self.assertEqual(value["state"], "QUEUED")
                self.assertIsNone(value["active_queue_path"])
                self.assertIsNone(value["active_revision"])
                pipeline_key = store.setting(f"pipeline_key:{job['id']}")
                self.assertTrue(pipeline_key.startswith("video-11111111-run-"))
                self.assertEqual(store.stages(job["id"])[0]["state"], "PENDING")
                self.assertEqual(
                    store.setting(f"detector_groups:{job['id']}"), ["gore"]
                )
            finally:
                store.close()

    def test_completed_review_stage_activates_revision(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("input", "reports/jobs", "logs", "config", "scripts"):
                (root / name).mkdir(parents=True, exist_ok=True)
            source = root / "input" / "video.mp4"
            source.write_bytes(b"video")
            store = JobStore(root / "jobs.sqlite3")
            job = store.upsert_job(
                job_key="video-11111111", source_path=source,
                source_sha256="1" * 64, source_size_bytes=5,
                source_mtime_ns=source.stat().st_mtime_ns,
                content_style="animation", state="QUEUED",
            )
            store.replace_stages(job["id"], ["build_review"])
            queue_path = root / "reports" / "jobs" / job["job_key"] / "review-queue.json"
            code = (
                "import json,pathlib; p=pathlib.Path(r'%s'); p.parent.mkdir(parents=True,exist_ok=True); "
                "p.write_text(json.dumps({'status':'REVIEW_REQUIRED','updated_at':'now'}),encoding='utf-8')"
            ) % queue_path
            definition = PipelineStage(
                "build_review", "BUILDING_REVIEW",
                (StageCommand((sys.executable, "-c", code), (queue_path,)),),
            )
            scheduler = JobScheduler(root, store, poll_seconds=0.02)
            try:
                with patch("biliflow.scheduler.pipeline_stages", return_value=[definition]):
                    scheduler.start()
                    deadline = time.time() + 5
                    while time.time() < deadline:
                        if store.get_job(job["id"])["state"] == "WAITING_REVIEW":
                            break
                        time.sleep(0.03)
                    self.assertEqual(store.get_job(job["id"])["state"], "WAITING_REVIEW")
                    self.assertEqual(len(store.revisions(job["id"])), 1)
            finally:
                scheduler.shutdown(immediate=True)
                store.close()



class SchedulerFixture(unittest.TestCase):
    """A temp root with a real JobStore and JobScheduler and a patched stage list."""

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name in ("input", "reports/jobs", "logs", "config", "scripts", "work", "output"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.store = JobStore(self.root / "state" / "control-center.sqlite3")
        self.addCleanup(self.store.close)
        self.scheduler = JobScheduler(self.root, self.store)
        self.stages = [PipelineStage("preflight", "PREFLIGHT", tuple())]
        patcher = patch("biliflow.scheduler.pipeline_stages", side_effect=lambda **_: list(self.stages))
        patcher.start()
        self.addCleanup(patcher.stop)

    def job(self, name, *, state="NEEDS_METADATA"):
        source = self.root / "input" / f"{name}.mp4"
        source.write_bytes(name.encode())
        return int(self.store.upsert_job(
            job_key=name, source_path=source, source_sha256=name.ljust(64, "0"),
            source_size_bytes=len(name), source_mtime_ns=source.stat().st_mtime_ns,
            content_style="live_action", state=state,
        )["id"])

    def start(self, job_id):
        return self.scheduler.start_job(
            job_id, content_style="live_action", profile="careful", detector_groups=["advertising"],
        )

    def export(self, job_id):
        self.store.update_job(job_id, state="READY_TO_EXPORT", progress=1.0)
        return self.scheduler.queue_render(
            job_id, plan_path=self.root / "work" / f"{job_id}.json",
            output_path=self.root / "output" / f"{job_id}.mp4",
        )

    def order(self):
        return [(item["job_id"], item["kind"]) for item in self.scheduler.queue_order()]

    def drain(self):
        """Job ids in the order the worker takes them (each one then finishes)."""
        taken = []
        while (selection := self.scheduler._select()) is not None:
            job, _stage, definition = selection
            self.assertTrue(self.store.claim_queued(job["id"], definition.job_state, definition.name))
            self.store.update_job(job["id"], state="COMPLETED", current_stage=None)
            taken.append(int(job["id"]))
        return taken


class QueueOrderTests(SchedulerFixture):
    """Click-order (FIFO) queue shared by scans and exports, with one worker."""

    def test_scans_and_exports_share_one_queue_in_click_order(self):
        a, b, c, d = (self.job(name) for name in "abcd")
        self.start(b)
        self.start(a)
        self.export(d)
        self.start(c)
        # Later updates (progress, heartbeats, metadata) never reorder the queue.
        self.store.update_job(b, progress=0.4)
        self.store.update_job(a, content_style="live_action")
        self.assertEqual(self.order(), [(b, "scan"), (a, "scan"), (d, "export"), (c, "scan")])
        self.assertEqual(self.store.get_job(d)["current_stage"], "render")
        self.assertEqual(self.drain(), [b, a, d, c])

    def test_export_clicked_first_runs_before_a_later_scan(self):
        a, b = self.job("a"), self.job("b")
        self.export(a)
        self.start(b)
        self.assertEqual(self.drain(), [a, b])

    def test_priority_still_beats_click_order(self):
        a, b = self.job("a"), self.job("b")
        self.start(a)
        self.start(b)
        self.store.update_job(b, priority=50)
        self.assertEqual(self.drain(), [b, a])

    def test_a_multi_stage_scan_keeps_the_head_between_its_stages(self):
        self.stages = [
            PipelineStage("one", "SCANNING_TEXT", tuple()),
            PipelineStage("two", "SCANNING_LOGO", tuple()),
        ]
        a, b = self.job("a"), self.job("b")
        self.start(a)
        self.start(b)
        self.scheduler._execute(*self.scheduler._select())
        job = self.store.get_job(a)
        self.assertEqual((job["state"], job["queue_seq"]), ("QUEUED", 1))
        selection = self.scheduler._select()
        self.assertEqual((selection[0]["id"], selection[1]["name"]), (a, "two"))

    def test_resume_and_retry_keep_the_place_and_new_clicks_go_to_the_back(self):
        a, b, c, d = (self.job(name) for name in "abcd")
        for job_id in (a, b, c):
            self.start(job_id)
        # Tiếp tục after Dừng ngay: back at its old place, ahead of later clicks.
        self.scheduler.pause_now(a)
        self.assertEqual(self.order(), [(b, "scan"), (c, "scan")])
        self.scheduler.resume(a)
        self.assertEqual(self.order(), [(a, "scan"), (b, "scan"), (c, "scan")])
        # Thử lại bước lỗi keeps the place too.
        self.assertTrue(self.store.claim_queued(b, "PREFLIGHT", "preflight"))
        self.store.update_stage(b, "preflight", state="FAILED_RETRYABLE", error="boom")
        self.store.update_job(b, state="FAILED", error="boom")
        self.scheduler.retry(b)
        self.assertEqual(self.order(), [(a, "scan"), (b, "scan"), (c, "scan")])
        # A restart (recovery) followed by Tiếp tục keeps it as well.
        self.assertTrue(self.store.claim_queued(a, "PREFLIGHT", "preflight"))
        self.assertEqual(self.store.recover_interrupted(), 1)
        self.scheduler.resume(a)
        self.assertEqual(self.order()[0], (a, "scan"))
        # Chạy lại kiểm tra and Bắt đầu are new clicks: they go to the back.
        self.store.update_job(c, state="WAITING_REVIEW", active_queue_path="reports/c/review-queue.json",
                              active_revision=1)
        # A settled job gives its place back; the rerun click takes a new one.
        self.assertIsNone(self.store.get_job(c)["queue_seq"])
        self.scheduler.rerun(c)
        self.start(d)
        self.assertEqual(self.order(), [(a, "scan"), (b, "scan"), (c, "scan"), (d, "scan")])
        seqs = [self.store.get_job(job_id)["queue_seq"] for job_id in (a, b, c, d)]
        self.assertEqual(seqs, sorted(seqs))

    def test_rerun_clears_the_old_revision_and_refuses_a_second_click(self):
        a = self.job("a", state="WAITING_REVIEW")
        self.store.update_job(a, active_queue_path="reports/old/review-queue.json", active_revision=1,
                              progress=1.0)
        self.store.set_setting(f"render:{a}", {"output": "output/old.mp4"})
        value = self.scheduler.rerun(a, detector_groups=["gore"])
        self.assertEqual(value["state"], "QUEUED")
        self.assertIsNone(value["active_queue_path"])
        self.assertIsNone(value["active_revision"])
        self.assertEqual(value["progress"], 0.0)
        self.assertIsNone(self.store.setting(f"render:{a}"))
        with self.assertRaisesRegex(ValueError, "đang nằm trong hàng đợi"):
            self.scheduler.rerun(a)
        self.assertTrue(self.store.claim_queued(a, "SCANNING_TEXT", "preflight"))
        with self.assertRaisesRegex(ValueError, "Dừng job hiện tại"):
            self.scheduler.rerun(a)
        reruns = [e for e in self.store.events(a) if e["event_type"] == "JOB_RERUN_QUEUED"]
        self.assertEqual(len(reruns), 1)

    def test_pause_cancel_or_stop_between_select_and_execute_wins(self):
        for action, expected in (("pause_now", "PAUSED"), ("cancel", "CANCELLED"),
                                 ("stop_after_stage", "PAUSED")):
            with self.subTest(action=action):
                job_id = self.job(action)
                self.start(job_id)
                selection = self.scheduler._select()
                self.assertEqual(selection[0]["id"], job_id)
                getattr(self.scheduler, action)(job_id)
                self.scheduler._execute(*selection)
                job = self.store.get_job(job_id)
                self.assertEqual(job["state"], expected)
                self.assertNotEqual(self.store.stage(job_id, "preflight")["state"], "RUNNING")
                self.assertFalse(any(
                    event["event_type"] == "STAGE_STARTED" for event in self.store.events(job_id)
                ))
                self.assertIsNone(self.scheduler._select())

    def test_stop_after_stage_takes_a_waiting_job_out_of_the_queue(self):
        a, b = self.job("a"), self.job("b")
        self.start(a)
        self.start(b)
        value = self.scheduler.stop_after_stage(a)
        self.assertEqual((value["state"], value["stop_mode"]), ("PAUSED", "PAUSED"))
        self.assertEqual(self.store.events(a)[0]["message"], "Đã rút khỏi hàng đợi trước khi chạy")
        self.assertEqual(self.order(), [(b, "scan")])
        self.scheduler.resume(a)
        self.assertEqual(self.order(), [(a, "scan"), (b, "scan")])
        # A running job still pauses after its current stage.
        self.assertTrue(self.store.claim_queued(a, "PREFLIGHT", "preflight"))
        self.assertEqual(self.scheduler.stop_after_stage(a)["stop_mode"], "AFTER_STAGE")
        self.assertEqual(self.store.get_job(a)["state"], "PREFLIGHT")

    def test_interrupted_review_build_is_recovered_and_resumable(self):
        self.stages = [PipelineStage("build_review", "BUILDING_REVIEW", tuple())]
        a = self.job("a")
        self.start(a)
        job, stage, definition = self.scheduler._select()
        self.assertTrue(self.store.claim_queued(a, definition.job_state, definition.name))
        self.store.update_stage(a, "build_review", state="RUNNING", pid=1)
        self.assertEqual(self.store.recover_interrupted(), 1)
        self.assertEqual(self.store.get_job(a)["state"], "INTERRUPTED_RECOVERABLE")
        self.assertIsNone(self.scheduler._select())
        self.scheduler.resume(a)
        selection = self.scheduler._select()
        self.assertEqual((selection[0]["id"], selection[1]["name"]), (a, "build_review"))


    def test_finalizing_a_waiting_export_again_keeps_its_place(self):
        a, b, c = self.job("a"), self.job("b"), self.job("c")
        self.start(a)
        self.export(b)
        self.start(c)
        seq = self.store.get_job(b)["queue_seq"]
        self.scheduler.queue_render(
            b, plan_path=self.root / "work" / f"{b}.json", output_path=self.root / "output" / f"{b}.mp4",
        )
        self.assertEqual(self.store.get_job(b)["queue_seq"], seq)
        self.assertEqual(self.order(), [(a, "scan"), (b, "export"), (c, "scan")])

    def _crash_after_review_build(self, name, *, write_queue=True):
        """Run build_review up to COMPLETED, then die before _after_success."""
        self.stages = [PipelineStage("build_review", "BUILDING_REVIEW", tuple())]
        job_id = self.job(name)
        self.start(job_id)
        _job, _stage, definition = self.scheduler._select()
        self.assertTrue(self.store.claim_queued(job_id, definition.job_state, definition.name))
        queue = (self.root / "reports" / "jobs" / self.scheduler._pipeline_key(self.store.get_job(job_id))
                 / "review-queue.json")
        if write_queue:
            queue.parent.mkdir(parents=True, exist_ok=True)
            queue.write_text(json.dumps({
                "status": "READY_FOR_EDIT_PLAN", "updated_at": "2026-10-02T00:00:00+00:00", "items": [],
            }), encoding="utf-8")
        self.store.update_stage(job_id, "build_review", state="COMPLETED", progress=1.0)
        self.assertEqual(self.store.recover_interrupted(), 1)
        self.assertEqual(self.store.get_job(job_id)["state"], "INTERRUPTED_RECOVERABLE")
        return job_id, queue

    def test_restart_after_the_review_build_finished_records_its_result(self):
        a, queue = self._crash_after_review_build("a")
        with patch("biliflow.scheduler.run_local_queue_audit",
                   return_value={"result": "PASS", "summary": "ok"}):
            self.assertEqual(self.scheduler.recover_finished_stages(), [a])
        job = self.store.get_job(a)
        self.assertEqual(
            (job["state"], job["current_stage"], job["stop_mode"], job["error"], job["queue_seq"]),
            ("READY_TO_EXPORT", None, None, None, None),
        )
        self.assertEqual(job["active_queue_path"], queue.relative_to(self.root).as_posix())
        self.assertTrue((queue.parent / "structure-audit.json").is_file())
        self.assertIsNone(self.scheduler._select())
        # Not stuck: Chạy lại kiểm tra is accepted again.
        self.assertEqual(self.scheduler.rerun(a)["state"], "QUEUED")

    def test_resume_after_the_review_build_finished_records_its_result(self):
        a, _queue = self._crash_after_review_build("a")
        with patch("biliflow.scheduler.run_local_queue_audit",
                   return_value={"result": "PASS", "summary": "ok"}):
            value = self.scheduler.resume(a)
        self.assertEqual(value["state"], "READY_TO_EXPORT")
        self.assertEqual(self.order(), [])

    def test_resume_never_finishes_a_job_the_worker_is_still_finishing(self):
        # Recheck finding 2026-10-03: the worker clears _active when the subprocess
        # ends, before _after_success; a Tiếp tục in that window must not run
        # _after_success a second time.
        a, _queue = self._crash_after_review_build("a")
        self.scheduler._executing_job_id = a
        with patch("biliflow.scheduler.run_local_queue_audit",
                   return_value={"result": "PASS", "summary": "ok"}) as audit:
            with self.assertRaisesRegex(ValueError, "đang được xử lý"):
                self.scheduler.resume(a)
            self.assertFalse(audit.called)
        self.scheduler._executing_job_id = None
        self.store.update_job(a, state="BUILDING_REVIEW")
        with self.assertRaisesRegex(ValueError, "đang được xử lý"):
            self.scheduler.resume(a)
        self.assertEqual(self.store.get_job(a)["state"], "BUILDING_REVIEW")

    def test_review_build_without_its_queue_runs_again_after_resume(self):
        a, _queue = self._crash_after_review_build("a", write_queue=False)
        self.assertEqual(self.scheduler.recover_finished_stages(), [])
        self.assertEqual(self.store.stage(a, "build_review")["state"], "PENDING")
        self.assertEqual(self.store.get_job(a)["state"], "INTERRUPTED_RECOVERABLE")
        self.scheduler.resume(a)
        self.assertEqual(self.order(), [(a, "scan")])

    def _crash_after_render(self, name, *, write_output=True):
        job_id = self.job(name)
        self.export(job_id)
        _job, _stage, definition = self.scheduler._select()
        self.assertEqual(definition.name, "render")
        self.assertTrue(self.store.claim_queued(job_id, definition.job_state, definition.name))
        output = self.root / "output" / f"{job_id}.mp4"
        if write_output:
            output.write_bytes(b"rendered")
            output.with_suffix(".mp4.manifest.json").write_text(
                json.dumps({"status": "COMPLETED"}), encoding="utf-8",
            )
        self.store.update_stage(job_id, "render", state="COMPLETED", progress=1.0)
        self.assertEqual(self.store.recover_interrupted(), 1)
        return job_id, output

    def test_restart_after_the_render_finished_completes_the_export(self):
        a, output = self._crash_after_render("a")
        self.assertEqual(self.scheduler.recover_finished_stages(), [a])
        job = self.store.get_job(a)
        self.assertEqual((job["state"], job["current_stage"], job["error"]), ("COMPLETED", None, None))
        self.assertIn(output.relative_to(self.root).as_posix(),
                      [item["path"] for item in self.store.artifacts(a) if item["kind"] == "final_output"])
        self.assertEqual(self.order(), [])

    def test_render_without_its_output_runs_again_after_resume(self):
        a, _output = self._crash_after_render("a", write_output=False)
        self.assertEqual(self.scheduler.recover_finished_stages(), [])
        self.assertEqual(self.store.stage(a, "render")["state"], "PENDING")
        self.scheduler.resume(a)
        self.assertEqual(self.order(), [(a, "export")])

    def test_pause_or_cancel_during_a_stage_without_a_subprocess_is_kept(self):
        # Preflight (and a cache hit) runs no subprocess: Dừng ngay / Hủy only
        # change the job row, and the finished stage must not requeue the job.
        self.stages = [
            PipelineStage("preflight", "PREFLIGHT", tuple()),
            PipelineStage("two", "SCANNING_TEXT", tuple()),
        ]
        for action, expected in (("pause_now", "PAUSED"), ("cancel", "CANCELLED")):
            with self.subTest(action=action):
                job_id = self.job(action)
                self.start(job_id)
                selection = self.scheduler._select()
                with patch.object(self.scheduler, "_run_preflight",
                                  side_effect=lambda job: getattr(self.scheduler, action)(int(job["id"]))):
                    self.scheduler._execute(*selection)
                job = self.store.get_job(job_id)
                self.assertEqual((job["state"], job["current_stage"]), (expected, None))
                self.assertEqual(self.store.stage(job_id, "preflight")["state"], "COMPLETED")
                self.assertIsNone(self.scheduler._select())
        # Tiếp tục then continues with the next stage.
        self.scheduler.resume(self._paused_id())
        selection = self.scheduler._select()
        self.assertEqual(selection[1]["name"], "two")

    def _paused_id(self):
        return next(job["id"] for job in self.store.list_jobs() if job["state"] == "PAUSED")

    def test_a_stage_result_never_overwrites_a_concurrent_pause(self):
        self.stages = [
            PipelineStage("one", "SCANNING_TEXT", tuple()),
            PipelineStage("two", "SCANNING_LOGO", tuple()),
        ]
        a = self.job("a")
        self.start(a)
        _job, _stage, definition = self.scheduler._select()
        self.assertTrue(self.store.claim_queued(a, definition.job_state, definition.name))
        self.store.update_stage(a, "one", state="COMPLETED")
        # The pause lands between the read in _after_success and its write.
        real_get = self.store.get_job
        calls = {"n": 0}

        def get_then_pause(job_id):
            value = real_get(job_id)
            calls["n"] += 1
            if calls["n"] == 2:
                self.store.update_job(job_id, state="PAUSED", stop_mode="PAUSED")
            return value

        with patch.object(self.store, "get_job", side_effect=get_then_pause):
            self.scheduler._after_success(a, "one", definition)
        self.assertEqual(self.store.get_job(a)["state"], "PAUSED")

    def test_one_lock_serialises_every_job_action(self):
        self.assertIs(self.scheduler._start_lock, self.scheduler.job_action_lock)
        # Re-entrant: retry calls resume, finalize calls queue_render inside it.
        with self.scheduler.job_action_lock:
            with self.scheduler.job_action_lock:
                pass
        a = self.job("a")
        self.start(a)
        self.assertTrue(self.store.claim_queued(a, "PREFLIGHT", "preflight"))
        for action in (self.scheduler.resume, self.scheduler.retry):
            with self.assertRaisesRegex(ValueError, "đang được xử lý"):
                action(a)
        self.assertEqual(self.store.get_job(a)["state"], "PREFLIGHT")
        # Another thread waits for the lock instead of interleaving.
        import threading
        entered = threading.Event()
        with self.scheduler.job_action_lock:
            worker = threading.Thread(target=lambda: (self.scheduler.job_action_lock.acquire(),
                                                      entered.set(), self.scheduler.job_action_lock.release()))
            worker.start()
            self.assertFalse(entered.wait(0.2))
        worker.join(5)
        self.assertTrue(entered.is_set())

    def test_resume_refuses_a_job_with_nothing_left_to_run(self):
        a = self.job("a")
        self.start(a)
        self.store.update_stage(a, "preflight", state="COMPLETED")
        self.store.update_job(a, state="PAUSED", stop_mode="PAUSED", current_stage="preflight")
        with self.assertRaisesRegex(ValueError, "không còn bước nào"):
            self.scheduler.resume(a)
        self.assertEqual(self.store.get_job(a)["state"], "PAUSED")


class StaleExportRequestTests(SchedulerFixture):
    """Open item (b): a stopped or superseded export never leaves a render request behind."""

    def render_state(self, job_id):
        return self.store.stage(job_id, "render")["state"]

    def retired_events(self, job_id):
        return [event for event in self.store.events(job_id) if event["event_type"] == "EXPORT_REQUEST_RETIRED"]

    def snapshot(self, job_id):
        job = self.store.get_job(job_id)
        return (
            {key: job[key] for key in ("state", "current_stage", "queue_seq", "stop_mode", "updated_at")},
            self.store.stages(job_id), self.store.setting(f"render:{job_id}"),
        )

    def test_messages_are_verbatim(self):
        self.assertEqual(EXPORT_RETIRE_MESSAGES, {
            "cancel": "Đã hủy lệnh xuất video.",
            "review_changed": "Lệnh xuất cũ hết hiệu lực vì quyết định duyệt đã đổi; "
                              "bấm “Xuất video” để xuất lại.",
            "skipped": "Lệnh xuất cũ hết hiệu lực vì video đã được đánh dấu bỏ qua.",
            "output_exists": "Bản xuất của lần duyệt này đã có; không cần render lại.",
        })
        self.assertEqual(RESUME_STALE_EXPORT_REFUSAL,
                         "Lệnh xuất cũ không còn hiệu lực; bấm “Xuất video” để xuất lại.")
        self.assertEqual(
            RESUME_SETTLED_REFUSAL.format(job_id=12),
            "Video #12 đã có kết quả duyệt; dùng “Xuất video” hoặc “Chạy lại kiểm tra” thay vì Tiếp tục.",
        )

    def test_cancelling_a_waiting_export_retires_its_render(self):
        a, b = self.job("a"), self.job("b")
        self.export(a)
        self.start(b)
        render = self.store.setting(f"render:{a}")
        self.scheduler.cancel(a)
        job = self.store.get_job(a)
        self.assertEqual((job["state"], job["current_stage"], job["stop_mode"]), ("CANCELLED", "render", "CANCELLED"))
        stage = self.store.stage(a, "render")
        self.assertEqual((stage["state"], stage["error"]), ("CANCELLED", EXPORT_RETIRE_MESSAGES["cancel"]))
        self.assertIsNone(self.store.render_request(a))
        events = self.retired_events(a)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["message"], "Đã hủy lệnh xuất video.")
        self.assertEqual(events[0]["payload"], {"reason": "cancel", "stage_state": "PENDING", "render": render})
        # render:{id} stays as history; the worker and the queue never see the export again.
        self.assertEqual(self.store.setting(f"render:{a}"), render)
        self.assertEqual(self.order(), [(b, "scan")])
        with self.assertRaisesRegex(ValueError, "không còn bước nào"):
            self.scheduler.resume(a)
        self.assertEqual(self.drain(), [b])

    def test_cancelling_a_running_render_keeps_the_existing_path(self):
        a = self.job("a")
        self.export(a)
        self.assertTrue(self.store.claim_queued(a, "RENDERING", "render"))
        self.store.update_stage(a, "render", state="RUNNING", pid=4321)
        self.scheduler.cancel(a)
        stage = self.store.stage(a, "render")
        self.assertEqual((stage["state"], stage["pid"], stage["error"]), ("CANCELLED", None, "Cancelled by user"))
        self.assertEqual(self.store.get_job(a)["state"], "CANCELLED")
        self.assertEqual(self.retired_events(a), [])

    def test_a_paused_export_still_resumes_as_an_export(self):
        a, b = self.job("a"), self.job("b")
        self.export(a)
        self.start(b)
        self.scheduler.pause_now(a)
        self.assertEqual(self.render_state(a), "PENDING")
        self.scheduler.resume(a)
        self.assertEqual(self.order(), [(a, "export"), (b, "scan")])
        self.scheduler.stop_after_stage(a)
        self.assertEqual(self.store.get_job(a)["state"], "PAUSED")
        self.scheduler.resume(a)
        self.assertEqual(self.order(), [(a, "export"), (b, "scan")])
        self.assertEqual(self.retired_events(a), [])

    def test_resume_refuses_a_job_whose_review_is_settled(self):
        a = self.job("a")
        self.start(a)
        self.store.update_stage(a, "preflight", state="COMPLETED")
        for state in ("READY_TO_EXPORT", "WAITING_REVIEW", "COMPLETED"):
            with self.subTest(state=state):
                self.store.update_job(a, state=state, current_stage=None)
                # Even an old render request left PENDING never makes it resumable.
                self.store.ensure_stage(a, "render")
                self.store.update_stage(a, "render", state="PENDING")
                before = self.snapshot(a)
                for action in (self.scheduler.resume, self.scheduler.retry):
                    with self.assertRaises(ValueError) as caught:
                        action(a)
                    self.assertEqual(str(caught.exception), RESUME_SETTLED_REFUSAL.format(job_id=a))
                    self.assertEqual(self.snapshot(a), before)
                self.assertEqual(self.order(), [])

    def test_retry_refuses_a_settled_job_before_resetting_its_failed_stage(self):
        a = self.job("a")
        self.export(a)
        self.store.update_stage(a, "render", state="FAILED_RETRYABLE", error="boom")
        self.store.update_job(a, state="READY_TO_EXPORT", current_stage=None)
        before = self.snapshot(a)
        with self.assertRaisesRegex(ValueError, "đã có kết quả duyệt"):
            self.scheduler.retry(a)
        self.assertEqual(self.snapshot(a), before)
        self.assertEqual(self.render_state(a), "FAILED_RETRYABLE")

    def test_resume_refuses_a_stale_render_of_a_cancelled_job(self):
        a = self.job("a")
        self.start(a)
        self.store.update_stage(a, "preflight", state="COMPLETED")
        self.store.update_job(a, state="CANCELLED", stop_mode="CANCELLED", current_stage=None)
        # Inserted directly, as older code left it after a cancel.
        self.store.ensure_stage(a, "render")
        before = self.snapshot(a)
        with self.assertRaises(ValueError) as caught:
            self.scheduler.resume(a)
        self.assertEqual(str(caught.exception), RESUME_STALE_EXPORT_REFUSAL)
        self.assertEqual(self.snapshot(a), before)
        # Thử lại on a failed render of a cancelled job is refused before the stage reset.
        self.store.update_stage(a, "render", state="FAILED")
        before = self.snapshot(a)
        with self.assertRaisesRegex(ValueError, "Lệnh xuất cũ không còn hiệu lực"):
            self.scheduler.retry(a)
        self.assertEqual(self.snapshot(a), before)
        self.assertEqual(self.order(), [])

    def test_a_failed_export_is_still_retried(self):
        a = self.job("a")
        self.export(a)
        self.assertTrue(self.store.claim_queued(a, "RENDERING", "render"))
        self.store.update_stage(a, "render", state="FAILED_RETRYABLE", error="boom")
        self.store.update_job(a, state="FAILED", error="boom", current_stage="render")
        self.scheduler.retry(a)
        self.assertEqual(self.order(), [(a, "export")])
        self.assertEqual(self.render_state(a), "PENDING")

    def test_retire_render_request_skips_waiting_and_running_jobs(self):
        a = self.job("a")
        self.export(a)
        before = self.snapshot(a)
        self.assertFalse(self.scheduler.retire_render_request(a, "review_changed", clear_current_stage=True))
        self.assertEqual(self.snapshot(a), before)
        self.assertTrue(self.store.claim_queued(a, "RENDERING", "render"))
        self.store.update_stage(a, "render", state="RUNNING")
        before = self.snapshot(a)
        for reason in EXPORT_RETIRE_MESSAGES:
            self.assertFalse(self.scheduler.retire_render_request(a, reason, clear_current_stage=True))
        self.assertEqual(self.snapshot(a), before)
        # A RUNNING render is never retired, even when the job row says otherwise.
        self.store.update_job(a, state="PAUSED")
        self.assertFalse(self.scheduler.retire_render_request(a, "cancel"))
        self.assertEqual(self.render_state(a), "RUNNING")
        self.assertEqual(self.retired_events(a), [])
        with self.assertRaises(KeyError):
            self.scheduler.retire_render_request(a, "unknown")

    def test_retire_render_request_clears_the_render_marker(self):
        a = self.job("a")
        self.export(a)
        self.store.update_job(a, state="READY_TO_EXPORT")
        self.assertEqual(self.store.get_job(a)["current_stage"], "render")
        self.assertTrue(self.scheduler.retire_render_request(a, "review_changed", clear_current_stage=True))
        job = self.store.get_job(a)
        self.assertEqual((job["state"], job["current_stage"]), ("READY_TO_EXPORT", None))
        self.assertEqual(self.render_state(a), "CANCELLED")
        self.assertEqual(self.retired_events(a)[0]["payload"]["reason"], "review_changed")
        # Nothing left to retire: False, but the marker is still cleared.
        self.store.update_job(a, current_stage="render")
        self.assertFalse(self.scheduler.retire_render_request(a, "skipped", clear_current_stage=True))
        self.assertIsNone(self.store.get_job(a)["current_stage"])
        self.assertEqual(len(self.retired_events(a)), 1)
        # Without clear_current_stage the marker stays (Hủy keeps "Đã hủy xuất video").
        self.store.update_stage(a, "render", state="FAILED")
        self.store.update_job(a, current_stage="render")
        self.assertTrue(self.scheduler.retire_render_request(a, "output_exists"))
        self.assertEqual(self.store.get_job(a)["current_stage"], "render")
        self.assertEqual(self.retired_events(a)[0]["payload"]["stage_state"], "FAILED")
        # A finished render is history and stays COMPLETED.
        self.store.update_stage(a, "render", state="COMPLETED")
        self.assertFalse(self.scheduler.retire_render_request(a, "output_exists"))
        self.assertEqual(self.render_state(a), "COMPLETED")


class CleanedSourceLockTests(SchedulerFixture):
    """A source moved to the Recycle Bin (latest row PENDING or RECYCLED) locks every action."""

    def exported(self, name):
        job_id = self.job(name)
        self.export(job_id)
        self.assertTrue(self.store.claim_queued(job_id, "RENDERING", "render"))
        self.store.update_stage(job_id, "render", state="COMPLETED", progress=1.0)
        self.store.update_job(job_id, state="COMPLETED", current_stage=None, progress=1.0)
        self.store.set_setting(f"skip:{job_id}", {"decisions": {"KEEP": 1}})
        return job_id

    def clean(self, job_id, state="RECYCLED"):
        job = self.store.get_job(job_id)
        row_id = self.store.add_source_cleanup(
            job_id=job_id, kind="EXPORTED", source_path=job["source_path"],
            source_sha256=job["source_sha256"], size_bytes=job["source_size_bytes"],
            mtime_ns=job["source_mtime_ns"], output_path=f"output/{job_id}.mp4",
        )
        if state != "PENDING":
            self.store.finish_source_cleanup(row_id, state=state, verified=state == "RECYCLED")
        return row_id

    def snapshot(self, job_id):
        settings = {
            key: self.store.setting(f"{key}:{job_id}")
            for key in ("render", "skip", "pipeline_key", "detector_groups", "ocr_batch_size", "fast_scan")
        }
        return (self.store.get_job(job_id), self.store.stages(job_id), settings,
                len(self.store.events(job_id, limit=1000)), self.order())

    def actions(self, job_id):
        return {
            "start_job": lambda: self.start(job_id),
            "rerun": lambda: self.scheduler.rerun(job_id, detector_groups=["gore"]),
            "resume": lambda: self.scheduler.resume(job_id),
            "retry": lambda: self.scheduler.retry(job_id),
            "queue_render": lambda: self.scheduler.queue_render(
                job_id, plan_path=self.root / "work" / "new.json", output_path=self.root / "output" / "new.mp4",
            ),
            "stop_after_stage": lambda: self.scheduler.stop_after_stage(job_id),
            "pause_now": lambda: self.scheduler.pause_now(job_id),
            "cancel": lambda: self.scheduler.cancel(job_id),
        }

    def assert_refused(self, job_id, expected):
        for name, action in self.actions(job_id).items():
            with self.subTest(action=name):
                before = self.snapshot(job_id)
                with self.assertRaises(ValueError) as caught:
                    action()
                self.assertEqual(str(caught.exception), expected[name])
                self.assertEqual(self.snapshot(job_id), before)

    def test_every_action_refuses_a_recycled_source(self):
        a = self.exported("a")
        other = self.job("other")
        self.start(other)
        self.clean(a)
        self.assert_refused(a, {
            **dict.fromkeys(("start_job", "rerun", "resume", "retry", "queue_render"), SOURCE_CLEANED_MESSAGE),
            **dict.fromkeys(("stop_after_stage", "pause_now", "cancel"), SOURCE_CLEANED_STOP_REFUSAL),
        })
        self.assertEqual(self.order(), [(other, "scan")])

    def test_a_pending_cleanup_already_locks_the_job(self):
        a = self.exported("a")
        self.clean(a, state="PENDING")
        self.assert_refused(a, {
            **dict.fromkeys(("start_job", "rerun", "resume", "retry", "queue_render"), SOURCE_CLEANED_MESSAGE),
            **dict.fromkeys(("stop_after_stage", "pause_now", "cancel"), SOURCE_CLEANED_STOP_REFUSAL),
        })

    def test_a_skipped_job_keeps_its_skip_refusal_first(self):
        a = self.exported("a")
        self.store.update_job(a, state="SKIPPED")
        self.clean(a)
        self.assert_refused(a, {
            "start_job": SOURCE_CLEANED_MESSAGE, "rerun": SOURCE_CLEANED_MESSAGE,
            "queue_render": SOURCE_CLEANED_MESSAGE,
            "resume": SKIPPED_REFUSAL, "retry": SKIPPED_REFUSAL,
            "stop_after_stage": SKIPPED_STOP_REFUSAL, "pause_now": SKIPPED_STOP_REFUSAL,
            "cancel": SKIPPED_STOP_REFUSAL,
        })

    def test_the_cleaned_message_comes_before_every_other_refusal(self):
        # A video still waiting for setup (impossible in practice) gets the cleaned
        # message, not "không xếp hàng lại", and a failed stage is never reset.
        a = self.job("a")
        self.clean(a)
        with self.assertRaises(ValueError) as caught:
            self.start(a)
        self.assertEqual(str(caught.exception), SOURCE_CLEANED_MESSAGE)
        b = self.exported("b")
        self.store.update_stage(b, "render", state="FAILED_RETRYABLE", error="boom")
        self.store.update_job(b, state="FAILED")
        self.clean(b)
        before = self.snapshot(b)
        with self.assertRaises(ValueError) as caught:
            self.scheduler.retry(b)
        self.assertEqual(str(caught.exception), SOURCE_CLEANED_MESSAGE)
        self.assertEqual(self.snapshot(b), before)
        self.assertEqual(self.store.stage(b, "render")["state"], "FAILED_RETRYABLE")

    def test_a_failed_cleanup_does_not_lock_and_a_restore_unlocks(self):
        a = self.exported("a")
        self.clean(a, state="FAILED")
        self.assertEqual(self.scheduler.rerun(a)["state"], "QUEUED")
        self.store.update_job(a, state="COMPLETED", current_stage=None)
        row_id = self.clean(a)
        with self.assertRaisesRegex(ValueError, "Thùng rác"):
            self.scheduler.rerun(a)
        self.assertIsNotNone(self.store.mark_source_restored(row_id, mtime_ns=99))
        value = self.scheduler.rerun(a, detector_groups=["gore"])
        self.assertEqual(value["state"], "QUEUED")
        self.assertIsNone(self.store.setting(f"render:{a}"))
        self.assertIsNone(self.store.setting(f"skip:{a}"))
        self.assertEqual(self.order(), [(a, "scan")])

    def archive(self, job_id, state="ARCHIVED", kind="EXPORTED"):
        job = self.store.get_job(job_id)
        folder = self.root / "archive" / "sources" / job["job_key"]
        row_id = self.store.add_source_archive(
            job_id=job_id, kind=kind, source_path=job["source_path"],
            archive_path=str(folder / f"{job['job_key']}.mp4"),
            manifest_path=str(folder / "archive-manifest.json"),
            source_sha256=job["source_sha256"], size_bytes=job["source_size_bytes"],
            mtime_ns=job["source_mtime_ns"], queue_path=f"reports/jobs/{job['job_key']}/q.json",
            output_path=f"output/{job_id}.mp4" if kind == "EXPORTED" else None,
        )
        if state in ("ARCHIVED", "RESTORING", "RESTORED"):
            self.store.finish_source_archive(row_id, state="ARCHIVED")
        if state in ("RESTORING", "RESTORED"):
            self.store.begin_archive_restore(row_id)
        if state == "RESTORED":
            self.store.finish_archive_restore(row_id, mtime_ns=99, job_state=None)
        return row_id

    def test_archived_jobs_refuse_every_action(self):
        other = self.job("other")
        self.start(other)
        archived = {
            **dict.fromkeys(("start_job", "rerun", "resume", "retry", "queue_render"), SOURCE_ARCHIVED_MESSAGE),
            **dict.fromkeys(("stop_after_stage", "pause_now", "cancel"), SOURCE_ARCHIVED_STOP_REFUSAL),
        }
        # The latest archive row PENDING, ARCHIVED or RESTORING locks the job.
        for state in ("PENDING", "ARCHIVED", "RESTORING"):
            with self.subTest(state=state):
                job_id = self.exported(f"a-{state.lower()}")
                self.archive(job_id, state)
                self.assert_refused(job_id, archived)
        # A skipped job keeps its skip refusals first, as with a cleaned source.
        skipped = self.exported("skipped")
        self.store.update_job(skipped, state="SKIPPED")
        self.archive(skipped, kind="SKIPPED")
        self.assert_refused(skipped, {
            **archived, "resume": SKIPPED_REFUSAL, "retry": SKIPPED_REFUSAL,
            "stop_after_stage": SKIPPED_STOP_REFUSAL, "pause_now": SKIPPED_STOP_REFUSAL,
            "cancel": SKIPPED_STOP_REFUSAL,
        })
        self.assertEqual(self.order(), [(other, "scan")])
        # A failed archive does not lock; a restored one unlocks.
        failed = self.exported("failed")
        row_id = self.archive(failed, "PENDING")
        self.store.finish_source_archive(row_id, state="FAILED", error="x")
        self.assertEqual(self.scheduler.rerun(failed)["state"], "QUEUED")
        restored = self.exported("restored")
        self.archive(restored, "RESTORED")
        self.assertEqual(self.scheduler.rerun(restored)["state"], "QUEUED")


class CancelGuardTests(SchedulerFixture):
    """Hủy runs once: a repeat or a finished job is a conflict (HTTP 409), with no write and no event."""

    def cancel_events(self, job_id):
        return [event for event in self.store.events(job_id, limit=1000) if event["event_type"] == "JOB_CANCELLED"]

    def snapshot(self, job_id):
        return self.store.get_job(job_id), self.store.stages(job_id), len(self.store.events(job_id, limit=1000))

    def test_cancel_repeat_is_a_conflict_without_a_second_event(self):
        a = self.job("a")
        self.start(a)
        self.assertEqual(self.scheduler.cancel(a)["state"], "CANCELLED")
        self.assertEqual(len(self.cancel_events(a)), 1)
        before = self.snapshot(a)
        with self.assertRaises(ActionConflict) as caught:
            self.scheduler.cancel(a)
        self.assertNotIsInstance(caught.exception, ValueError)
        self.assertEqual((caught.exception.code, str(caught.exception)),
                         ("already_cancelled", f"Video #{a} đã được hủy trước đó; không hủy thêm lần nữa."))
        self.assertEqual(self.snapshot(a), before)
        # Another tab cancels between the check and the write: the write is conditional.
        b = self.job("b")
        self.start(b)
        real = self.store.update_job_if

        def another_tab_wins(job_id, **kwargs):
            self.store.update_job(job_id, state="CANCELLED", stop_mode="CANCELLED")
            return real(job_id, **kwargs)

        with patch.object(self.store, "update_job_if", side_effect=another_tab_wins):
            with self.assertRaises(ActionConflict) as caught:
                self.scheduler.cancel(b)
        self.assertEqual(caught.exception.code, "already_cancelled")
        self.assertEqual(self.cancel_events(b), [])
        # L1: a cancelled job is not sticky; "Chạy lại kiểm tra" still queues it.
        self.assertEqual(self.scheduler.rerun(a)["state"], "QUEUED")

    def test_cancel_refuses_completed(self):
        a = self.job("a")
        self.store.update_job(a, state="COMPLETED", progress=1.0)
        before = self.snapshot(a)
        with self.assertRaises(ActionConflict) as caught:
            self.scheduler.cancel(a)
        self.assertEqual((caught.exception.code, str(caught.exception)),
                         ("not_cancellable", f"Video #{a} đã hoàn tất; không có gì để hủy."))
        self.assertEqual(self.snapshot(a), before)
        # A skipped job keeps its own refusal (a ValueError, HTTP 400).
        self.store.update_job(a, state="SKIPPED")
        with self.assertRaises(ValueError) as caught:
            self.scheduler.cancel(a)
        self.assertEqual(str(caught.exception), SKIPPED_STOP_REFUSAL)
        # Every other settled or waiting state can still be cancelled.
        for state in ("WAITING_REVIEW", "READY_TO_EXPORT", "PAUSED", "FAILED", "NEEDS_METADATA"):
            with self.subTest(state=state):
                self.store.update_job(a, state=state)
                self.assertEqual(self.scheduler.cancel(a)["state"], "CANCELLED")
        self.assertEqual(len(self.cancel_events(a)), 5)


class InputWatcherRestoreTests(unittest.TestCase):
    """A7: a cleaned source comes back only at its path with its SHA-256.

    The watcher never moves a cleaned job's source_path, hashes each
    (path, size, mtime) once, and a file vanishing mid-scan is skipped.
    """

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        for name in ("input", "fake-recycle-bin", "reports/jobs", "logs", "work", "output"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.store = JobStore(self.root / "state" / "control-center.sqlite3")
        self.addCleanup(self.store.close)
        for target, value in (("probe_video", {}), ("duration_seconds", 12.0)):
            patcher = patch(f"biliflow.scheduler.{target}", return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("biliflow.scheduler.pipeline_stages",
                        side_effect=lambda **_: [PipelineStage("preflight", "PREFLIGHT", tuple())])
        patcher.start()
        self.addCleanup(patcher.stop)
        # Counts every hash the watcher makes (the real function still runs).
        patcher = patch("biliflow.scheduler.sha256_file", side_effect=sha256_file)
        self.hasher = patcher.start()
        self.addCleanup(patcher.stop)
        self.scheduler = JobScheduler(self.root, self.store)
        self.watcher = InputWatcher(self.root, self.store, stable_seconds=0)
        self.source = self.root / "input" / "Tập 11.mp4"
        self.original = b"original source bytes"
        self.digest = sha256_of(self.original)
        self.source.write_bytes(self.original)
        self.assertEqual(self.watcher.scan_once(), 1)
        self.job_id = int(self.store.list_jobs()[0]["id"])
        self.store.update_job(self.job_id, state="COMPLETED", content_style="live_action", progress=1.0)
        self.mtime_ns = self.source.stat().st_mtime_ns
        self.hasher.reset_mock()

    def recycle(self, *, reset=True, finish=True):
        """What execute_cleanup does: PENDING row, the file leaves input/, RECYCLED, watcher reset."""
        job = self.store.get_job(self.job_id)
        row_id = self.store.add_source_cleanup(
            job_id=self.job_id, kind="EXPORTED", source_path=str(self.source.resolve()),
            source_sha256=job["source_sha256"], size_bytes=job["source_size_bytes"],
            mtime_ns=job["source_mtime_ns"], output_path="output/tap-11-reviewed.mp4",
        )
        if not finish:
            return row_id
        self.binned = self.root / "fake-recycle-bin" / "$RABC123.mp4"
        os.replace(self.source, self.binned)
        self.assertIsNotNone(self.store.finish_source_cleanup(row_id, state="RECYCLED", verified=True))
        if reset:
            self.store.reset_watched_file(self.source)
        self.assertEqual(self.watcher.scan_once(), 0)  # nothing in input/ yet
        return row_id

    def copy_back(self, path, data, *, mtime_ns=None):
        path.write_bytes(data)
        if mtime_ns is not None:
            os.utime(path, ns=(mtime_ns, mtime_ns))

    def scans(self, count=3):
        return [self.watcher.scan_once() for _ in range(count)]

    def events(self, event_type):
        return [event for event in self.store.events(limit=1000) if event["event_type"] == event_type]

    def assert_still_locked(self, row_id):
        row = self.store.latest_source_cleanup(self.job_id)
        self.assertEqual((row["id"], row["state"]), (row_id, "RECYCLED"))
        self.assertTrue(self.store.source_cleaned(self.job_id))
        with self.assertRaises(ValueError) as caught:
            self.scheduler.rerun(self.job_id, detector_groups=["gore"])
        self.assertEqual(str(caught.exception), SOURCE_CLEANED_MESSAGE)

    def assert_old_job_kept(self):
        job = self.store.get_job(self.job_id)
        self.assertEqual(
            (job["source_path"], job["source_sha256"], job["source_size_bytes"], job["state"]),
            (str(self.source.resolve()), self.digest, len(self.original), "COMPLETED"),
        )
        return job

    def test_the_same_bytes_back_at_the_same_path_restore_the_job(self):
        row_id = self.recycle()
        self.assert_still_locked(row_id)
        # A Recycle Bin restore keeps the old mtime: only the reset makes it new.
        self.copy_back(self.source, self.original, mtime_ns=self.mtime_ns)
        self.assertEqual(self.scans(), [0, 0, 0])
        self.assertEqual(self.hasher.call_count, 1)
        row = self.store.latest_source_cleanup(self.job_id)
        self.assertEqual((row["id"], row["state"], row["restored_mtime_ns"]), (row_id, "RESTORED", self.mtime_ns))
        self.assertIsNotNone(row["restored_at"])
        self.assertFalse(self.store.source_cleaned(self.job_id))
        self.assertEqual(self.assert_old_job_kept()["source_mtime_ns"], self.mtime_ns)
        restored = self.events("SOURCE_RESTORED")
        self.assertEqual(len(restored), 1)
        self.assertEqual(
            (restored[0]["job_id"], restored[0]["level"], restored[0]["message"]),
            (self.job_id, "INFO", "Đã khôi phục video gốc (SHA-256 khớp)"),
        )
        self.assertEqual(SOURCE_RESTORED_MESSAGE, restored[0]["message"])
        self.assertEqual(restored[0]["payload"], {
            "path": str(self.source.resolve()), "size_bytes": len(self.original), "sha256": self.digest,
        })
        self.assertEqual(len(self.store.list_jobs()), 1)
        self.assertEqual(len(self.events("INPUT_DISCOVERED")), 1)
        self.assertEqual(self.events("SOURCE_RESTORE_WRONG_PATH") + self.events("SOURCE_RESTORE_REJECTED"), [])
        self.assertEqual(self.scheduler.rerun(self.job_id, detector_groups=["gore"])["state"], "QUEUED")

    def test_a_copy_with_a_new_mtime_restores_and_the_job_takes_the_new_mtime(self):
        row_id = self.recycle()
        new_mtime = self.mtime_ns + 5_000_000_000
        self.copy_back(self.source, self.original, mtime_ns=new_mtime)
        self.assertEqual(self.scans(), [0, 0, 0])
        self.assertEqual(self.hasher.call_count, 1)
        row = self.store.latest_source_cleanup(self.job_id)
        self.assertEqual((row["id"], row["state"], row["restored_mtime_ns"]), (row_id, "RESTORED", new_mtime))
        self.assertEqual(self.store.get_job(self.job_id)["source_mtime_ns"], new_mtime)

    def test_different_bytes_at_the_old_path_are_rejected_once_and_become_a_new_job(self):
        row_id = self.recycle()
        other = b"a different video at the same name"
        self.copy_back(self.source, other)
        self.assertEqual(self.scans(), [1, 0, 0])
        self.assertEqual(self.hasher.call_count, 1)
        rejected = self.events("SOURCE_RESTORE_REJECTED")
        self.assertEqual(len(rejected), 1)
        self.assertEqual(
            (rejected[0]["job_id"], rejected[0]["level"], rejected[0]["message"]),
            (self.job_id, "WARNING", "File mới ở đường dẫn cũ có SHA-256 khác video gốc; không dùng để chạy lại."),
        )
        self.assertEqual(SOURCE_RESTORE_REJECTED_MESSAGE, rejected[0]["message"])
        stat = self.source.stat()
        self.assertEqual(rejected[0]["payload"], {
            "path": str(self.source.resolve()), "size_bytes": len(other), "mtime_ns": stat.st_mtime_ns,
            "sha256": sha256_of(other), "expected_sha256": self.digest,
        })
        self.assert_still_locked(row_id)
        self.assert_old_job_kept()
        new_jobs = [job for job in self.store.list_jobs() if int(job["id"]) != self.job_id]
        self.assertEqual(len(new_jobs), 1)
        self.assertEqual(
            (new_jobs[0]["state"], new_jobs[0]["source_path"], new_jobs[0]["source_sha256"]),
            ("NEEDS_METADATA", str(self.source.resolve()), sha256_of(other)),
        )
        self.assertEqual(self.events("SOURCE_RESTORED"), [])

    def test_the_same_bytes_under_another_name_only_explain_the_right_name(self):
        row_id = self.recycle()
        renamed = self.root / "input" / "Tập 11 (1).mp4"
        self.copy_back(renamed, self.original)
        self.assertEqual(self.scans(), [0, 0, 0])
        self.assertEqual(self.hasher.call_count, 1)
        wrong = self.events("SOURCE_RESTORE_WRONG_PATH")
        self.assertEqual(len(wrong), 1)
        self.assertEqual(
            (wrong[0]["job_id"], wrong[0]["level"], wrong[0]["message"]),
            (self.job_id, "WARNING", "Đã thấy video gốc ở tên khác; hãy chép lại đúng tên “Tập 11.mp4” vào input."),
        )
        self.assertEqual(SOURCE_RESTORE_WRONG_PATH_MESSAGE.format(file_name="Tập 11.mp4"), wrong[0]["message"])
        self.assertEqual(wrong[0]["payload"], {
            "path": str(renamed.resolve()), "expected_path": str(self.source.resolve()), "sha256": self.digest,
        })
        self.assertEqual(len(self.store.list_jobs()), 1)
        self.assert_old_job_kept()
        self.assert_still_locked(row_id)
        # Copying it under the right name then restores the job.
        self.copy_back(self.source, self.original, mtime_ns=self.mtime_ns)
        self.assertEqual(self.scans(), [0, 0, 0])
        self.assertEqual(self.hasher.call_count, 2)
        self.assertEqual(self.store.latest_source_cleanup(self.job_id)["state"], "RESTORED")
        self.assertEqual(len(self.events("SOURCE_RESTORE_WRONG_PATH")), 1)

    def test_a_restored_file_waits_until_it_is_stable(self):
        row_id = self.recycle()
        self.watcher.stable_seconds = 60
        self.copy_back(self.source, self.original, mtime_ns=self.mtime_ns)
        self.assertEqual(self.scans(), [0, 0, 0])
        self.assertEqual(self.hasher.call_count, 0)
        self.assert_still_locked(row_id)
        self.assertEqual(self.events("SOURCE_RESTORED"), [])
        self.watcher.stable_seconds = 0  # the file has now been stable long enough
        self.assertEqual(self.scans(), [0, 0, 0])
        self.assertEqual(self.hasher.call_count, 1)
        self.assertEqual(self.store.latest_source_cleanup(self.job_id)["state"], "RESTORED")

    def test_a_restore_is_still_seen_when_the_watcher_row_was_never_reset(self):
        # A crash between finish RECYCLED and reset_watched_file: the restored
        # file has the size and mtime the watcher already marked as imported.
        row_id = self.recycle(reset=False)
        os.replace(self.binned, self.source)
        self.assertEqual(self.source.stat().st_mtime_ns, self.mtime_ns)
        self.assertEqual(self.scans(), [0, 0, 0])
        self.assertEqual(self.hasher.call_count, 1)
        row = self.store.latest_source_cleanup(self.job_id)
        self.assertEqual((row["id"], row["state"]), (row_id, "RESTORED"))
        self.assertEqual(len(self.events("SOURCE_RESTORED")), 1)

    def test_a_pending_cleanup_only_marks_a_copy_of_its_source(self):
        row_id = self.recycle(finish=False)
        copy = self.root / "input" / "copy.mp4"
        self.copy_back(copy, self.original)
        self.assertEqual(self.scans(), [0, 0, 0])
        self.assertEqual(self.hasher.call_count, 1)  # the original is still marked: not hashed
        row = self.store.latest_source_cleanup(self.job_id)
        self.assertEqual((row["id"], row["state"]), (row_id, "PENDING"))
        self.assertEqual(len(self.store.list_jobs()), 1)
        self.assert_old_job_kept()
        for event_type in ("SOURCE_RESTORED", "SOURCE_RESTORE_WRONG_PATH", "SOURCE_RESTORE_REJECTED"):
            self.assertEqual(self.events(event_type), [], event_type)

    def test_a_file_vanishing_mid_scan_is_skipped_and_the_scan_goes_on(self):
        gone_at_stat = self.root / "input" / "a gone at stat.mp4"
        gone_at_hash = self.root / "input" / "b gone at hash.mp4"
        stays = self.root / "input" / "c stays.mp4"
        for path in (gone_at_stat, gone_at_hash, stays):
            path.write_bytes(path.name.encode())
        real_stat = Path.stat
        calls = []

        def flaky_stat(path, *args, **kwargs):
            # is_file() still sees the file; the explicit stat() right after does not.
            if path.name == gone_at_stat.name:
                calls.append(path.name)
                if len(calls) > 1:
                    raise FileNotFoundError(2, "The system cannot find the file specified", str(path))
            return real_stat(path, *args, **kwargs)

        def vanishing_hash(path):
            if Path(path).name == gone_at_hash.name:
                Path(path).unlink()  # deleted between observe and the hash
            return sha256_file(path)

        self.hasher.side_effect = vanishing_hash
        # One loop of the real watcher thread body: WATCHER_ERROR is logged there.
        with patch.object(Path, "stat", flaky_stat), \
                patch.object(self.watcher._stop, "wait", side_effect=lambda _timeout: self.watcher._stop.set()):
            self.watcher._run()
        self.assertGreater(len(calls), 1)
        self.assertEqual(self.events("WATCHER_ERROR"), [])
        discovered = {Path(job["source_path"]).name for job in self.store.list_jobs()}
        self.assertEqual(discovered, {"Tập 11.mp4", "c stays.mp4"})
        # The file that only looked gone is picked up by the next scan.
        self.assertEqual(self.watcher.scan_once(), 1)
        self.assertIn("a gone at stat.mp4", {Path(job["source_path"]).name for job in self.store.list_jobs()})


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


if __name__ == "__main__":
    unittest.main()
