import json
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow.job_pipeline import PipelineStage, StageCommand
from biliflow.job_store import JobStore
from biliflow.scheduler import JobScheduler
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



class QueueOrderTests(unittest.TestCase):
    """Click-order (FIFO) queue shared by scans and exports, with one worker."""

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

    def test_resume_refuses_a_job_with_nothing_left_to_run(self):
        a = self.job("a")
        self.start(a)
        self.store.update_stage(a, "preflight", state="COMPLETED")
        self.store.update_job(a, state="PAUSED", stop_mode="PAUSED", current_stage="preflight")
        with self.assertRaisesRegex(ValueError, "không còn bước nào"):
            self.scheduler.resume(a)
        self.assertEqual(self.store.get_job(a)["state"], "PAUSED")


if __name__ == "__main__":
    unittest.main()
