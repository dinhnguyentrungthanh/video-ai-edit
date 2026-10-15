import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from biliflow.export_guards import ActionConflict, SOURCE_ARCHIVED_MESSAGE, SOURCE_ARCHIVED_STOP_REFUSAL
from biliflow.export_lanes import REASON_TEXT, VramRead
from biliflow.job_pipeline import PipelineStage, StageCommand
from biliflow.job_store import JobStore, sha256_file
from biliflow.scheduler import (
    EXPORT_DEFERRED_MESSAGES,
    EXPORT_LANE,
    EXPORT_RETIRE_MESSAGES,
    MAIN_LANE,
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
    _GpuStage,
    _LaneWake,
)
from biliflow.final_renderer import (
    EXPORT_LANE_ENVIRONMENT,
    PARALLEL_EXPORT_LANE,
    export_deferral_path,
    render_graph_path,
    render_progress_path,
    render_stall_path,
)
from biliflow.render_watchdog import STALL_SECONDS, stall_limit, stall_message


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
            # A stop kills the render-final process before it removes its graph file.
            graph = render_graph_path(root, output)
            graph.write_text("[0:v]null[vout]", encoding="utf-8")
            other_graph = render_graph_path(root, root / "output" / "other.mp4")
            other_graph.write_text("[0:v]null[vout]", encoding="utf-8")
            outside = root / "work" / "keep.partial.mp4"
            outside.write_bytes(b"keep")
            stall = render_stall_path(root, output)
            try:
                scheduler.queue_render(
                    job["id"], plan_path=plan, output_path=output,
                    max_output_bytes=None, target_output_bytes=None,
                )
                # The watch on FFmpeg stopped the render: its record goes with the rest.
                stall.write_text('{"schema": 1, "message": "dừng"}', encoding="utf-8")
                scheduler._cleanup_interrupted_stage(job["id"], "render")
                self.assertFalse(stall.exists())
                self.assertFalse(partial.exists())
                self.assertFalse(progress.exists())
                self.assertFalse(graph.exists())
                self.assertTrue(other_graph.exists())
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

    def test_cache_entry_past_the_path_limit_completes_the_stage_with_a_warning(self):
        # A deep install folder (140 characters): the reports and logs fit the Windows path limit,
        # the stage's cache entry (its .tmp folder) does not.
        with TemporaryDirectory() as directory:
            prefix = "deep-install-"
            width = 140 - len(directory) - 1 - len(prefix)
            if width < 1:
                self.skipTest("the temporary folder is too deep for a 140-character root")
            root = Path(directory) / (prefix + "x" * width)
            for name in ("input", "reports/jobs", "logs", "config", "scripts", "src/biliflow"):
                (root / name).mkdir(parents=True, exist_ok=True)
            for name in ("worker", "scanner", "license_policy", "control_center"):
                (root / "src/biliflow" / (name + ".py")).write_text("# original", encoding="utf-8")
            (root / "config" / "processing_profiles.json").write_text("{}", encoding="utf-8")
            (root / "scripts" / "run.ps1").write_text("run", encoding="utf-8")
            source = root / "input" / "video.mp4"
            source.write_bytes(b"video")
            source_sha = "b" * 64
            store = JobStore(root / "jobs.sqlite3")
            job = store.upsert_job(
                job_key="run-1", source_path=source, source_sha256=source_sha, source_size_bytes=5,
                source_mtime_ns=source.stat().st_mtime_ns, content_style="live_action", state="QUEUED",
            )
            store.replace_stages(job["id"], ["adult"])
            artifact = root / "reports" / "jobs" / "run-1" / "adult" / "scan.json"
            code = (
                "import json,pathlib; p=pathlib.Path(r'%s'); p.parent.mkdir(parents=True,exist_ok=True); "
                "p.write_text(json.dumps({'status':'COMPLETED','input_sha256':'%s'}),encoding='utf-8')"
            ) % (artifact, source_sha)
            definition = PipelineStage(
                "adult", "SCANNING_SAFETY", (StageCommand((sys.executable, "-c", code), (artifact,)),),
            )
            scheduler = JobScheduler(root, store)
            try:
                with patch("biliflow.stage_cache._long_paths_enabled", return_value=False):
                    scheduler._execute(store.get_job(job["id"]), store.stage(job["id"], "adult"), definition)
                self.assertEqual(store.stage(job["id"], "adult")["state"], "COMPLETED")
                self.assertNotEqual(store.get_job(job["id"])["state"], "FAILED")
                self.assertTrue(artifact.is_file())
                events = store.events(job["id"])
                skipped = [item for item in events if item["event_type"] == "STAGE_CACHE_SKIPPED"]
                self.assertEqual([item["level"] for item in skipped], ["WARN"])
                self.assertFalse({"STAGE_CACHE_STORED", "STAGE_FAILED"} & {item["event_type"] for item in events})
                log = next((root / "logs" / "control-center").glob(f"job-{job['id']}-adult-attempt-*.log"))
                self.assertIn("WARNING: Stage adult finished but was not saved to the stage cache",
                              log.read_text(encoding="utf-8"))
                cache_root = root / "cache" / "stage-results"
                self.assertEqual(list(cache_root.rglob("*")) if cache_root.exists() else [], [])
            finally:
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
    """One worker; waiting exports go before waiting scans, each kind in click order."""

    def test_waiting_exports_run_before_waiting_scans_each_in_click_order(self):
        # Perf plan A1a: Xuất video no longer waits behind scans clicked before it.
        a, b, c, d, e = (self.job(name) for name in "abcde")
        self.start(b)
        self.start(a)
        self.export(d)
        self.start(c)
        self.export(e)
        # Later updates (progress, heartbeats, metadata) never reorder the queue.
        self.store.update_job(b, progress=0.4)
        self.store.update_job(a, content_style="live_action")
        self.store.update_job(d, progress=1.0)
        self.assertEqual(
            self.order(), [(d, "export"), (e, "export"), (b, "scan"), (a, "scan"), (c, "scan")],
        )
        self.assertEqual(self.store.get_job(d)["current_stage"], "render")
        self.assertEqual(self.drain(), [d, e, b, a, c])

    def test_export_clicked_first_runs_before_a_later_scan(self):
        a, b = self.job("a"), self.job("b")
        self.export(a)
        self.start(b)
        self.assertEqual(self.drain(), [a, b])

    def test_export_clicked_after_a_scan_runs_first(self):
        a, b = self.job("a"), self.job("b")
        self.start(a)
        self.export(b)
        self.assertEqual(self.order(), [(b, "export"), (a, "scan")])
        self.assertEqual(self.drain(), [b, a])

    def test_priority_still_beats_click_order(self):
        a, b = self.job("a"), self.job("b")
        self.start(a)
        self.start(b)
        self.store.update_job(b, priority=50)
        self.assertEqual(self.drain(), [b, a])

    def test_priority_still_beats_the_export_preference(self):
        a, b, c = self.job("a"), self.job("b"), self.job("c")
        self.export(a)
        self.start(b)
        self.export(c)
        self.store.update_job(b, priority=50)
        self.store.update_job(c, priority=150)
        self.assertEqual(self.order(), [(b, "scan"), (a, "export"), (c, "export")])
        self.assertEqual(self.drain(), [b, a, c])

    def test_a_waiting_export_runs_at_the_next_stage_boundary_of_a_scan(self):
        self.stages = [
            PipelineStage("one", "SCANNING_TEXT", tuple()),
            PipelineStage("two", "SCANNING_LOGO", tuple()),
        ]
        a, b, c = self.job("a"), self.job("b"), self.job("c")
        self.start(a)
        self.start(c)
        _job, _stage, definition = self.scheduler._select()
        self.assertEqual((_job["id"], definition.name), (a, "one"))
        # The first stage of a is running (as _execute leaves it) when Xuất video lands.
        process = Mock(pid=4321)
        process.poll.return_value = None
        self.assertTrue(self.store.claim_queued(a, definition.job_state, definition.name))
        self.store.update_stage(a, "one", state="RUNNING", pid=process.pid)
        self.scheduler._process[MAIN_LANE], self.scheduler._active[MAIN_LANE] = process, (a, "one")
        with patch.object(self.scheduler, "_terminate_process") as terminate:
            self.export(b)
        # The running stage is never cut short.
        terminate.assert_not_called()
        self.assertEqual(self.scheduler.active["job_id"], a)
        self.assertEqual(self.store.get_job(a)["state"], "SCANNING_TEXT")
        self.assertEqual(self.store.stage(a, "one")["state"], "RUNNING")
        self.assertEqual(self.order(), [(b, "export"), (c, "scan")])
        # The stage ends as _execute ends it.
        self.scheduler._release_lane(MAIN_LANE)
        self.store.update_stage(a, "one", state="COMPLETED", progress=1.0, pid=None)
        self.scheduler._after_success(a, "one", definition)
        job = self.store.get_job(a)
        self.assertEqual((job["state"], job["queue_seq"]), ("QUEUED", 1))
        # At the boundary the export goes first; a keeps its place ahead of c.
        self.assertEqual(self.order(), [(b, "export"), (a, "scan"), (c, "scan")])
        job, stage, _definition = self.scheduler._select()
        self.assertEqual((job["id"], stage["name"]), (b, "render"))
        self.assertEqual(self.drain(), [b, a, c])

    def test_queue_order_is_the_order_select_takes(self):
        a, b, c, d = (self.job(name) for name in "abcd")
        self.start(a)
        self.export(b)
        self.start(c)
        self.export(d)
        self.store.update_job(c, priority=40)
        order = self.scheduler.queue_order()
        self.assertEqual([item["position"] for item in order], [1, 2, 3, 4])
        self.assertEqual([item["job_id"] for item in order], [c, b, d, a])
        self.assertEqual(self.drain(), [c, b, d, a])

    def test_a_resumed_or_retried_export_keeps_its_place_among_the_exports(self):
        a, b, c, e = self.job("a"), self.job("b"), self.job("c"), self.job("e")
        self.start(a)
        self.start(b)
        self.export(c)
        self.export(e)
        # Dừng ngay, then Tiếp tục: back before the scans and before the later export e.
        self.scheduler.pause_now(c)
        self.assertEqual(self.order(), [(e, "export"), (a, "scan"), (b, "scan")])
        self.scheduler.resume(c)
        expected = [(c, "export"), (e, "export"), (a, "scan"), (b, "scan")]
        self.assertEqual(self.order(), expected)
        # A failed render, then Thử lại bước lỗi: the same place.
        self.assertTrue(self.store.claim_queued(c, "RENDERING", "render"))
        self.store.update_stage(c, "render", state="FAILED_RETRYABLE", error="boom")
        self.store.update_job(c, state="FAILED", error="boom")
        self.assertEqual(self.order(), [(e, "export"), (a, "scan"), (b, "scan")])
        self.scheduler.retry(c)
        self.assertEqual(self.order(), expected)

    def test_a_rerun_of_a_cancelled_export_is_a_scan_at_the_back(self):
        a, b = self.job("a"), self.job("b")
        self.export(a)
        self.start(b)
        self.scheduler.cancel(a)
        self.scheduler.rerun(a)
        self.assertEqual(self.order(), [(b, "scan"), (a, "scan")])

    def test_a_broken_scan_behind_the_head_does_not_stop_the_worker(self):
        # As before A1a, the worker builds stage lists only up to the job it takes.
        a, b = self.job("a"), self.job("b")
        self.start(a)
        self.start(b)
        (self.root / "input" / "b.mp4").unlink()

        def stages(**arguments):
            Path(arguments["source"]).resolve(strict=True)
            return list(self.stages)

        with patch("biliflow.scheduler.pipeline_stages", side_effect=stages):
            self.assertEqual(self.scheduler._select()[0]["id"], a)

    def test_a_paused_queue_still_shows_the_order_it_will_run(self):
        a, b = self.job("a"), self.job("b")
        self.start(a)
        self.export(b)
        self.store.set_setting("scheduler_paused", True)
        self.assertIsNone(self.scheduler._select())
        self.assertEqual(self.order(), [(b, "export"), (a, "scan")])
        self.store.set_setting("scheduler_paused", False)
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
        a, b, c, d = self.job("a"), self.job("b"), self.job("c"), self.job("d")
        self.start(a)
        self.export(b)
        self.export(d)
        self.start(c)
        seq = self.store.get_job(b)["queue_seq"]
        self.scheduler.queue_render(
            b, plan_path=self.root / "work" / f"{b}.json", output_path=self.root / "output" / f"{b}.mp4",
        )
        self.assertEqual(self.store.get_job(b)["queue_seq"], seq)
        self.assertEqual(self.order(), [(b, "export"), (d, "export"), (a, "scan"), (c, "scan")])

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
        self.scheduler._executing[MAIN_LANE] = a
        with patch("biliflow.scheduler.run_local_queue_audit",
                   return_value={"result": "PASS", "summary": "ok"}) as audit:
            with self.assertRaisesRegex(ValueError, "đang được xử lý"):
                self.scheduler.resume(a)
            self.assertFalse(audit.called)
        self.scheduler._executing.pop(MAIN_LANE)
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


class StopBeforeLaunchTests(SchedulerFixture):
    """Hủy or Dừng ngay landing after the worker claimed an export never lets it start (A-export.md:362)."""

    def setUp(self):
        super().setUp()
        popen = patch("biliflow.scheduler.subprocess.Popen")
        self.popen = popen.start()
        self.addCleanup(popen.stop)
        self.popen.return_value.pid = 4321
        self.popen.return_value.poll.return_value = 0
        self.popen.return_value.wait.return_value = 0

    def waiting_export(self, name):
        self.popen.reset_mock()
        job_id = self.job(name)
        self.export(job_id)
        selection = self.scheduler._select()
        self.assertEqual((selection[0]["id"], selection[2].name), (job_id, "render"))
        return job_id, selection

    def assert_not_started(self, job_id, job_state, stage_state):
        self.popen.assert_not_called()
        job = self.store.get_job(job_id)
        self.assertEqual((job["state"], job["stop_mode"]), (job_state, job_state))
        self.assertEqual(self.store.stage(job_id, "render")["state"], stage_state)
        self.assertFalse(self.scheduler.is_busy(job_id))
        self.assertIsNone(self.scheduler.active)

    def test_a_stop_between_the_claim_and_the_running_stage_never_starts_the_export(self):
        for action, job_state, stage_state in (("cancel", "CANCELLED", "CANCELLED"),
                                               ("pause_now", "PAUSED", "PENDING")):
            with self.subTest(action=action):
                job_id, selection = self.waiting_export(action)
                real_claim = self.store.claim_queued

                def claim_then_stop(claimed_id, state, stage):
                    claimed = real_claim(claimed_id, state, stage)
                    getattr(self.scheduler, action)(claimed_id)
                    return claimed

                with patch.object(self.store, "claim_queued", side_effect=claim_then_stop):
                    self.scheduler._execute(*selection)
                self.assert_not_started(job_id, job_state, stage_state)
                self.assertFalse(any(
                    event["event_type"] == "STAGE_STARTED" for event in self.store.events(job_id)
                ))
                self.assertIsNone(self.scheduler._select())
                if action == "pause_now":
                    # Tiếp tục still runs it as an export, at the front of the queue.
                    self.scheduler.resume(job_id)
                    self.assertEqual(self.order(), [(job_id, "export")])
                    self.scheduler.cancel(job_id)

    def test_a_stop_after_the_running_stage_but_before_the_process_never_starts_it(self):
        for action, job_state, stage_state in (("cancel", "CANCELLED", "CANCELLED"),
                                               ("pause_now", "PAUSED", "PENDING")):
            with self.subTest(action=action):
                job_id, selection = self.waiting_export(action)
                real_restore = self.scheduler._stage_cache.restore

                def stop_then_restore(**kwargs):
                    self.assertEqual(self.store.stage(job_id, "render")["state"], "RUNNING")
                    getattr(self.scheduler, action)(job_id)
                    return real_restore(**kwargs)

                with patch.object(self.scheduler._stage_cache, "restore", side_effect=stop_then_restore):
                    self.scheduler._execute(*selection)
                self.assert_not_started(job_id, job_state, stage_state)
                self.assertFalse(any(
                    event["event_type"] == "STAGE_FAILED" for event in self.store.events(job_id)
                ))
                if action == "pause_now":
                    self.scheduler.cancel(job_id)

    def test_a_cancel_racing_the_launch_still_stops_the_export(self):
        # Hủy writes CANCELLED while the worker launches the render: the launch
        # either sees the write or Hủy sees the running process and stops it.
        job_id, selection = self.waiting_export("race")
        stopped, cancel_writing, launched = threading.Event(), threading.Event(), threading.Event()
        process = Mock(pid=4321)
        process.poll.side_effect = lambda: 1 if stopped.is_set() else None
        process.wait.side_effect = lambda *args, **kwargs: (stopped.wait(10), 1)[1]

        def launch(*args, **kwargs):
            self.assertTrue(cancel_writing.wait(5), "Hủy never reached its write")
            launched.set()
            return process

        self.popen.side_effect = launch
        real_update_job_if = self.store.update_job_if

        def write_after_the_launch(target, **kwargs):
            if kwargs.get("state") == "CANCELLED":
                cancel_writing.set()
                self.assertTrue(launched.wait(5), "the worker never launched the render")
            return real_update_job_if(target, **kwargs)

        terminated = []

        def terminate(_process):
            terminated.append(True)
            stopped.set()

        worker = threading.Thread(target=self.scheduler._execute, args=selection, daemon=True)
        try:
            with patch.object(self.scheduler, "_terminate_process", side_effect=terminate), \
                    patch.object(self.store, "update_job_if", side_effect=write_after_the_launch):
                worker.start()
                self.scheduler.cancel(job_id)
                worker.join(10)
                self.assertFalse(worker.is_alive(), "the cancelled render kept running")
        finally:
            stopped.set()
            worker.join(10)
        self.assertEqual(terminated, [True])
        job = self.store.get_job(job_id)
        self.assertEqual((job["state"], job["stop_mode"]), ("CANCELLED", "CANCELLED"))
        self.assertEqual(self.store.stage(job_id, "render")["state"], "CANCELLED")
        self.assertFalse(self.scheduler.is_busy(job_id))


class FakeStageProcess:
    """A stage subprocess that ends when the test says (finish) or the scheduler stops it."""

    def __init__(self, argv, kwargs, pid, on_success):
        self.argv = tuple(str(value) for value in argv)
        self.kwargs = kwargs
        self.pid = pid
        if "render-final" in self.argv:
            self.kind = f"render:{Path(self.argv[self.argv.index('--output') + 1]).stem}"
        else:
            self.kind = self.argv[1]
        self._on_success = on_success
        self._lock = threading.Lock()
        self._ended = threading.Event()
        self.returncode = None

    def finish(self, code=0):
        with self._lock:
            if self._ended.is_set():
                return
            if code == 0:
                self._on_success(self)
            self.returncode = code
            self._ended.set()

    def poll(self):
        return self.returncode if self._ended.is_set() else None

    def wait(self, timeout=None):
        if not self._ended.wait(10 if timeout is None else timeout):
            raise subprocess.TimeoutExpired(self.argv, timeout)
        return self.returncode


class ExportLaneTests(SchedulerFixture):
    """Perf plan A1b: one NVENC export beside a GPU scan stage, only when the machine allows it.

    Measured VRAM numbers are given here (the real ones come from the A1b
    measurement); stage processes are fakes the test ends, so both lanes really
    run at once in the scheduler's own threads.
    """

    FULL_HD = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}]}

    def setUp(self):
        super().setUp()
        self.device = "cuda"
        stages = patch("biliflow.scheduler.pipeline_stages",
                       side_effect=lambda **arguments: self.scan_stages(arguments["job_key"]))
        stages.start()
        self.addCleanup(stages.stop)
        measured = patch.multiple(
            "biliflow.export_lanes",
            NVENC_NEED_MIB=((1280 * 720, 300), (1920 * 1080, 500)), SCAN_PEAK_MIB=6000, SAFETY_MARGIN_MIB=1000,
        )
        measured.start()
        self.addCleanup(measured.stop)
        profile = patch("biliflow.export_lanes.profile_nvenc_trial", return_value="ok")
        self.profile = profile.start()
        self.addCleanup(profile.stop)
        # 12 GiB, 1000 MiB in use: room for the scan's 6000 MiB, NVENC's 500 and the 1000 margin.
        self.vram = VramRead(total_mib=12288, used_mib=1000, free_mib=11288)
        self.scheduler.read_vram = Mock(side_effect=lambda: self.vram)
        self.probe = self.FULL_HD
        probe = patch.object(self.scheduler, "_probe_frame", side_effect=lambda source: self.probe)
        probe.start()
        self.addCleanup(probe.stop)
        self.scheduler.poll_seconds = 0.05
        self.launched = []
        self.launch_lock = threading.Lock()
        popen = patch("biliflow.scheduler.subprocess.Popen", side_effect=self.launch)
        popen.start()
        self.addCleanup(popen.stop)
        self.terminated = []
        terminate = patch.object(self.scheduler, "_terminate_process", side_effect=self.terminate)
        terminate.start()
        self.addCleanup(terminate.stop)
        # Runs first: the lanes stop while the fakes above are still in place.
        self.addCleanup(self.stop_lanes)

    # A1c: the real pipeline starts with preflight (CPU, no process) and ends with
    # build_review (CPU); cpu_before puts CPU stages with a process before the scans.
    preflight = False
    cpu_before: tuple[str, ...] = ()
    review = False

    def scan_stages(self, job_key):
        def cpu(name, state):
            return PipelineStage(name, state, (StageCommand(("python", f"{job_key}:{name}"), ()),), uses_gpu=False)

        return [
            *([PipelineStage("preflight", "PREFLIGHT", tuple())] if self.preflight else []),
            *(cpu(name, "SCANNING_SAFETY") for name in self.cpu_before),
            *(PipelineStage(name, state, (StageCommand(("python", f"{job_key}:{name}", "--device", self.device), ()),),
                            uses_gpu=True)
              for name, state in (("scan_one", "SCANNING_TEXT"), ("scan_two", "SCANNING_LOGO"))),
            *([cpu("build_review", "BUILDING_REVIEW")] if self.review else []),
        ]

    def launch(self, argv, **kwargs):
        with self.launch_lock:
            process = FakeStageProcess(argv, kwargs, 5000 + len(self.launched), self.succeed)
            self.launched.append(process)
        return process

    def terminate(self, process):
        self.terminated.append(process.kind)
        process.finish(1)

    @staticmethod
    def succeed(process):
        if process.kind.startswith("render:"):
            output = Path(process.argv[process.argv.index("--output") + 1])
            output.write_bytes(b"video")
            output.with_name(output.name + ".manifest.json").write_text(
                json.dumps({"status": "COMPLETED"}), encoding="utf-8",
            )

    def stop_lanes(self):
        self.scheduler._draining.set()
        self.scheduler._stop.set()
        self.scheduler._wake.set()
        with self.launch_lock:
            launched = list(self.launched)
        for process in launched:
            process.finish(1)
        for thread in self.scheduler._threads.values():
            thread.join(10)

    def kinds(self):
        with self.launch_lock:
            return [process.kind for process in self.launched]

    def wait_until(self, predicate, message="condition never held", timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail(f"{message}; started: {self.kinds()}")

    def wait_for(self, kind, count=1):
        """The ``count``-th process of this kind the scheduler started."""
        def found():
            with self.launch_lock:
                return [process for process in self.launched if process.kind == kind]
        self.wait_until(lambda: len(found()) >= count, f"{kind} #{count} never started")
        return found()[count - 1]

    def export_auto(self, job_id, render_settings=None):
        """Xuất video with an approved plan of the job's source ("auto" unless told otherwise)."""
        plan = {"status": "READY_FOR_FINAL_RENDER", "source": {"path": self.store.get_job(job_id)["source_path"]}}
        if render_settings is not None:
            plan["render_settings"] = render_settings
        (self.root / "work" / f"{job_id}.json").write_text(json.dumps(plan), encoding="utf-8")
        return self.export(job_id)

    def write_deferral(self, job_id, reason):
        """The record the renderer leaves when it hands an export of the export lane back."""
        path = export_deferral_path(self.root, self.root / "output" / f"{job_id}.mp4")
        path.parent.mkdir(parents=True, exist_ok=True)
        fallback = None if reason == "not_nvenc" else {
            "from": "h264_nvenc", "reason": reason, "returncode": 1, "message": None, "elapsed_seconds": 1.0,
        }
        path.write_text(json.dumps({
            "schema": 1, "created_at": "2026-10-14T10:00:00+07:00",
            "plan": f"work/{job_id}.json", "output": f"output/{job_id}.mp4", "reason": reason,
            "encoder_choice": {"requested": "auto", "nvenc_check": "profile_ok"} if fallback else None,
            "fallback": fallback, "nvenc_elapsed_seconds": 1.0,
        }), encoding="utf-8")
        return path

    def events_of(self, job_id, event_type):
        return [event for event in self.store.events(job_id, limit=1000) if event["event_type"] == event_type]

    def both_running(self):
        """Scan job a on the GPU in the main lane, export b beside it in the export lane."""
        a, b = self.job("a"), self.job("b")
        self.start(a)
        self.export_auto(b)
        self.scheduler.start()
        scan = self.wait_for("a:scan_one")
        render = self.wait_for(f"render:{b}")
        self.wait_until(lambda: len(self.scheduler.active_lanes) == 2, "both lanes never ran")
        return a, b, scan, render

    def test_auto_runs_one_nvenc_export_beside_a_gpu_scan_stage(self):
        a, b, scan, render = self.both_running()
        self.assertEqual(
            [(item["lane"], item["job_id"], item["stage"], item["pid"]) for item in self.scheduler.active_lanes],
            [(MAIN_LANE, a, "scan_one", scan.pid), (EXPORT_LANE, b, "render", render.pid)],
        )
        # The single dict the classic dashboard and "Đo lại" read: the main lane's stage.
        self.assertEqual(self.scheduler.active, {"job_id": a, "stage": "scan_one", "pid": scan.pid})
        # Only the export lane's render gets the lane variable (the renderer then never runs x264).
        self.assertEqual(render.kwargs["env"][EXPORT_LANE_ENVIRONMENT], PARALLEL_EXPORT_LANE)
        self.assertNotIn("env", scan.kwargs)
        (event,) = self.events_of(b, "EXPORT_LANE_PARALLEL")
        self.assertEqual(
            {key: event["payload"][key] for key in
             ("reason", "scan_job_id", "scan_stage", "baseline_mib", "scan_peak_mib", "need_mib", "left_mib",
              "width", "height")},
            {"reason": "opened", "scan_job_id": a, "scan_stage": "scan_one", "baseline_mib": 1000,
             "scan_peak_mib": 6000, "need_mib": 500, "left_mib": 12288 - 7000 - 500, "width": 1920, "height": 1080},
        )
        self.assertEqual(self.events_of(b, "EXPORT_LANE_SEQUENTIAL"), [])
        render.finish(0)
        self.wait_until(lambda: self.store.get_job(b)["state"] == "COMPLETED", "the export never completed")
        # The scan stage beside it runs on, untouched.
        self.assertIsNone(scan.poll())
        self.assertEqual(self.store.get_job(a)["state"], "SCANNING_TEXT")
        scan.finish(0)
        self.wait_for("a:scan_two").finish(0)
        self.wait_until(lambda: not self.scheduler.is_busy(a), "the scan job never finished")
        self.assertEqual(self.terminated, [])
        self.assertEqual(self.kinds(), ["a:scan_one", f"render:{b}", "a:scan_two"])

    def test_off_runs_exports_and_scans_one_at_a_time_as_before(self):
        self.assertEqual(self.scheduler.set_parallel_export_mode("off"), "off")
        a, b = self.job("a"), self.job("b")
        self.start(a)
        self.export_auto(b)
        self.scheduler.start()
        render = self.wait_for(f"render:{b}")
        self.assertNotIn("env", render.kwargs)
        time.sleep(0.5)  # ten looks of each lane: nothing starts beside the export
        self.assertEqual(self.kinds(), [f"render:{b}"])
        render.finish(0)
        scan = self.wait_for("a:scan_one")
        c = self.job("c")
        self.export_auto(c)
        time.sleep(0.5)  # nor beside the scan stage
        self.assertEqual(self.kinds(), [f"render:{b}", "a:scan_one"])
        self.assertIsNone(self.scheduler._gpu_stage)
        scan.finish(0)
        # At the stage boundary the export goes first (A1a), in the main lane.
        later = self.wait_for(f"render:{c}")
        self.assertNotIn("env", later.kwargs)
        self.assertEqual([item["lane"] for item in self.scheduler.active_lanes], [MAIN_LANE])
        later.finish(0)
        self.wait_for("a:scan_two").finish(0)
        self.wait_until(lambda: not self.scheduler.is_busy(a), "the scan job never finished")
        self.assertEqual(self.kinds(), [f"render:{b}", "a:scan_one", f"render:{c}", "a:scan_two"])
        # "Tắt" reads no VRAM and records no lane decision: exactly the single worker of before.
        self.scheduler.read_vram.assert_not_called()
        for job_id in (b, c):
            self.assertEqual(self.events_of(job_id, "EXPORT_LANE_SEQUENTIAL"), [])
            self.assertEqual(self.events_of(job_id, "EXPORT_LANE_PARALLEL"), [])

    def test_a_stop_of_one_lane_never_stops_the_other(self):
        self.scheduler.start()
        cases = (("cancel", EXPORT_LANE), ("pause_now", EXPORT_LANE), ("cancel", MAIN_LANE), ("pause_now", MAIN_LANE))
        for index, (action, lane) in enumerate(cases):
            with self.subTest(action=action, lane=lane):
                a, b = self.job(f"a{index}"), self.job(f"b{index}")
                self.start(a)
                self.export_auto(b)
                scan, render = self.wait_for(f"a{index}:scan_one"), self.wait_for(f"render:{b}")
                if lane == EXPORT_LANE:
                    (stopped, stopped_job, stage), (running, running_job) = (render, b, "render"), (scan, a)
                else:
                    (stopped, stopped_job, stage), (running, running_job) = (scan, a, "scan_one"), (render, b)
                running_state = self.store.get_job(running_job)["state"]
                getattr(self.scheduler, action)(stopped_job)
                self.assertEqual(self.terminated[-1], stopped.kind)
                self.assertIsNone(running.poll())
                self.wait_until(lambda: not self.scheduler.is_busy(stopped_job), "the stopped lane never ended")
                self.assertEqual([item["job_id"] for item in self.scheduler.active_lanes], [running_job])
                expected = ("CANCELLED", "CANCELLED") if action == "cancel" else ("PAUSED", "PENDING")
                self.assertEqual((self.store.get_job(stopped_job)["state"], self.store.stage(stopped_job, stage)["state"]),
                                 expected)
                self.assertEqual(self.events_of(stopped_job, "STAGE_FAILED"), [])
                self.assertEqual(self.events_of(stopped_job, "EXPORT_DEFERRED"), [])
                self.assertEqual(self.store.get_job(running_job)["state"], running_state)
                self.assertIsNone(running.poll())
                # The other lane's stage ends as usual.
                running.finish(0)
                if running_job == a:
                    self.wait_for(f"a{index}:scan_two").finish(0)
                self.wait_until(lambda: not self.scheduler.is_busy(running_job), "the other lane never ended")
                if running_job == b:
                    self.assertEqual(self.store.get_job(b)["state"], "COMPLETED")
        self.assertEqual(len(self.terminated), len(cases))

    def test_is_busy_covers_each_lane_until_its_result_is_recorded(self):
        # Xóa video gốc and Lưu trữ refuse a busy job: the gap after a lane's process
        # ended and before its result is written must count as busy too.
        a, b, scan, render = self.both_running()
        real = self.scheduler._after_success
        entered = {a: threading.Event(), b: threading.Event()}
        release = threading.Event()

        def slow(job_id, name, definition):
            entered[job_id].set()
            release.wait(10)
            return real(job_id, name, definition)

        try:
            with patch.object(self.scheduler, "_after_success", side_effect=slow):
                render.finish(0)
                self.assertTrue(entered[b].wait(10))
                self.assertEqual(self.store.stage(b, "render")["state"], "COMPLETED")
                self.assertEqual([item["lane"] for item in self.scheduler.active_lanes], [MAIN_LANE])
                self.assertTrue(self.scheduler.is_busy(b))
                self.assertTrue(self.scheduler._lane_busy(EXPORT_LANE))
                scan.finish(0)
                self.assertTrue(entered[a].wait(10))
                self.assertEqual(self.scheduler.active_lanes, [])
                self.assertIsNone(self.scheduler.active)
                self.assertTrue(self.scheduler.is_busy(a))
                self.assertTrue(self.scheduler.is_busy(b))
                release.set()
                self.wait_until(lambda: not self.scheduler.is_busy(b), "the export lane never recorded its result")
        finally:
            release.set()
        self.assertEqual(self.store.get_job(b)["state"], "COMPLETED")
        self.wait_for("a:scan_two").finish(0)
        self.wait_until(lambda: not self.scheduler.is_busy(a), "the scan job never finished")

    def test_an_immediate_shutdown_stops_both_lanes(self):
        a, b, scan, render = self.both_running()
        self.scheduler.shutdown(immediate=True, timeout=10)
        self.assertEqual(sorted(self.terminated), sorted([scan.kind, render.kind]))
        self.assertFalse(any(thread.is_alive() for thread in self.scheduler._threads.values()))
        for job_id, stage in ((a, "scan_one"), (b, "render")):
            self.assertEqual((self.store.get_job(job_id)["state"], self.store.stage(job_id, stage)["state"]),
                             ("PAUSED", "PENDING"))
        self.assertEqual(self.scheduler.active_lanes, [])
        self.assertEqual(self.kinds(), [scan.kind, render.kind])

    def test_a_graceful_shutdown_waits_for_both_lanes_to_reach_their_boundary(self):
        a, b, scan, render = self.both_running()
        done = threading.Event()
        stopper = threading.Thread(
            target=lambda: (self.scheduler.shutdown(timeout=10), done.set()), daemon=True,
        )
        stopper.start()
        self.wait_until(
            lambda: all(self.store.get_job(job_id)["stop_mode"] == "AFTER_STAGE" for job_id in (a, b)),
            "shutdown never asked both jobs to stop after their stage",
        )
        render.finish(0)
        self.wait_until(lambda: self.store.get_job(b)["state"] == "COMPLETED", "the export never completed")
        self.assertFalse(done.is_set())  # the scan stage still runs
        self.assertIsNone(scan.poll())
        scan.finish(0)
        self.assertTrue(done.wait(10))
        stopper.join(10)
        self.assertEqual(self.terminated, [])
        self.assertFalse(any(thread.is_alive() for thread in self.scheduler._threads.values()))
        job = self.store.get_job(a)
        self.assertEqual((job["state"], job["stop_mode"]), ("PAUSED", "PAUSED"))
        self.assertEqual(self.store.stage(a, "scan_two")["state"], "PENDING")
        # Nothing started while the lanes drained.
        self.assertEqual(self.kinds(), [scan.kind, render.kind])

    def test_an_export_handed_back_runs_with_x264_in_the_main_lane_after_the_scan_job(self):
        a, b, scan, render = self.both_running()
        c = self.job("c")
        self.start(c)  # a scan job clicked later
        # NVENC failed beside the scan: the renderer left its record and stopped.
        self.write_deferral(b, "encoder_error")
        render.finish(2)
        self.wait_until(lambda: self.events_of(b, "EXPORT_DEFERRED"), "the export was never handed back")
        (event,) = self.events_of(b, "EXPORT_DEFERRED")
        self.assertEqual((event["level"], event["message"], event["payload"]["reason"]),
                         ("WARNING", EXPORT_DEFERRED_MESSAGES["encoder_error"], "encoder_error"))
        self.assertEqual(event["payload"]["fallback"]["from"], "h264_nvenc")
        job = self.store.get_job(b)
        self.assertEqual((job["state"], job["current_stage"]), ("QUEUED", "render"))
        self.assertEqual(self.store.stage(b, "render")["state"], "PENDING")
        self.assertEqual(self.events_of(b, "STAGE_FAILED"), [])
        # The scan job that ran beside it finishes first; nothing cuts in.
        scan.finish(0)
        second = self.wait_for("a:scan_two")
        time.sleep(0.3)
        self.assertEqual(self.kinds(), ["a:scan_one", f"render:{b}", "a:scan_two"])
        second.finish(0)
        # Then the export, in the main lane with no lane variable (x264 as A3), before the later scan job.
        again = self.wait_for(f"render:{b}", count=2)
        self.assertNotIn("env", again.kwargs)
        self.assertEqual([item["lane"] for item in self.scheduler.active_lanes], [MAIN_LANE])
        self.assertEqual(self.kinds()[-1], f"render:{b}")
        again.finish(0)
        self.wait_until(lambda: self.store.get_job(b)["state"] == "COMPLETED", "the export never completed")
        self.wait_for("c:scan_one").finish(0)
        self.wait_for("c:scan_two").finish(0)
        self.wait_until(lambda: not self.scheduler.is_busy(c), "the later scan never finished")
        self.assertEqual(len(self.events_of(b, "EXPORT_LANE_PARALLEL")), 1)

    def write_stall(self, job_id, seconds):
        """The record the renderer leaves when the watch on FFmpeg stopped its render (render_watchdog)."""
        message = stall_message(idle_seconds=seconds, encoded=None, expected_seconds=90.0)
        path = render_stall_path(self.root, self.root / "output" / f"{job_id}.mp4")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schema": 1, "created_at": "2026-10-14T21:00:00+07:00",
            "plan": f"work/{job_id}.json", "output": f"output/{job_id}.mp4", "lane": "parallel",
            "message": message, "idle_seconds": seconds, "encoded_seconds": None,
        }, ensure_ascii=False), encoding="utf-8")
        return path, message

    def assert_failed_by_the_watch(self, job_id, message):
        self.wait_until(lambda: self.store.get_job(job_id)["state"] == "FAILED", "the stalled export never failed")
        job = self.store.get_job(job_id)
        self.assertEqual((job["error"], job["current_stage"]), (message, "render"))
        stage = self.store.stage(job_id, "render")
        self.assertEqual((stage["state"], stage["error"]), ("FAILED_RETRYABLE", message))
        (event,) = self.events_of(job_id, "STAGE_FAILED")
        self.assertEqual((event["level"], event["message"]), ("ERROR", message))
        self.assertEqual(self.events_of(job_id, "EXPORT_DEFERRED"), [])
        self.assertNotIn(job_id, self.scheduler._sequential_exports)

    def test_an_export_the_watch_stopped_beside_a_scan_fails_with_its_message_and_is_not_handed_back(self):
        a, b, scan, render = self.both_running()
        record, message = self.write_stall(b, stall_limit(longest_cut_seconds=0.0, parallel=True))
        self.assertIn("không tiến triển trong 20 phút", message)
        render.finish(2)  # what the renderer's process returns after RenderStalled
        self.assert_failed_by_the_watch(b, message)
        # Its record goes with the stage's other files; nothing sends it to the main lane.
        self.assertFalse(record.exists())
        self.assertFalse(export_deferral_path(self.root, self.root / "output" / f"{b}.mp4").exists())
        # The scan beside it runs on, and the export does not start again by itself.
        self.assertIsNone(scan.poll())
        scan.finish(0)
        self.wait_for("a:scan_two").finish(0)
        self.wait_until(lambda: not self.scheduler.is_busy(a), "the scan job never finished")
        time.sleep(0.3)
        self.assertEqual(self.kinds(), ["a:scan_one", f"render:{b}", "a:scan_two"])
        self.assertEqual(self.store.get_job(b)["state"], "FAILED")

    def test_a_stalled_export_is_never_handed_back_even_beside_a_deferral_record(self):
        a, b, scan, render = self.both_running()
        _, message = self.write_stall(b, stall_limit(longest_cut_seconds=0.0, parallel=True))
        # A record that would hand it back (the renderer never writes both for one render).
        self.write_deferral(b, "encoder_error")
        render.finish(2)
        self.assert_failed_by_the_watch(b, message)
        scan.finish(0)
        self.wait_for("a:scan_two").finish(0)
        self.wait_until(lambda: not self.scheduler.is_busy(a), "the scan job never finished")
        time.sleep(0.3)
        self.assertEqual(self.kinds().count(f"render:{b}"), 1)

    def test_an_export_the_watch_stopped_in_the_main_lane_fails_with_its_message(self):
        self.assertEqual(self.scheduler.set_parallel_export_mode("off"), "off")
        b = self.job("b")
        self.export_auto(b)
        self.scheduler.start()
        render = self.wait_for(f"render:{b}")
        self.assertNotIn("env", render.kwargs)
        record, message = self.write_stall(b, STALL_SECONDS)
        self.assertIn("không tiến triển trong 10 phút (chưa xuất được khung hình nào)", message)
        render.finish(2)
        self.assert_failed_by_the_watch(b, message)
        self.assertFalse(record.exists())

    def test_a_render_that_fails_without_a_stall_record_keeps_the_generic_error(self):
        self.assertEqual(self.scheduler.set_parallel_export_mode("off"), "off")
        b = self.job("b")
        self.export_auto(b)
        self.scheduler.start()
        render = self.wait_for(f"render:{b}")
        # A record that cannot be read is not a stall.
        path = render_stall_path(self.root, self.root / "output" / f"{b}.mp4")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        render.finish(2)
        self.wait_until(lambda: self.store.get_job(b)["state"] == "FAILED", "the export never failed")
        self.assertTrue(self.store.get_job(b)["error"].startswith("Stage process exited with code 2"))

    def running_export(self, name, reason):
        """An export the export lane is running (RENDERING, its render stage RUNNING) and its record."""
        job_id = self.job(name)
        self.export_auto(job_id)
        self.assertTrue(self.store.claim_queued(job_id, "RENDERING", "render"))
        self.assertTrue(self.store.start_stage(job_id, "render", job_state="RENDERING", attempt=1))
        self.write_deferral(job_id, reason)
        return job_id

    def test_a_hand_back_never_overwrites_a_stop_that_landed_first(self):
        for action, job_state, stage_state in (("cancel", "CANCELLED", "CANCELLED"),
                                               ("pause_now", "PAUSED", "PENDING")):
            with self.subTest(action=action):
                b = self.running_export(f"b-{action}", "size_limit")
                real = self.store.requeue_running_stage

                def stop_first(job_id, name, **kwargs):
                    getattr(self.scheduler, action)(job_id)
                    return real(job_id, name, **kwargs)

                with patch.object(self.store, "requeue_running_stage", side_effect=stop_first):
                    self.scheduler._hand_back_export(b)
                job = self.store.get_job(b)
                self.assertEqual((job["state"], job["stop_mode"]), (job_state, job_state))
                self.assertEqual(self.store.stage(b, "render")["state"], stage_state)
                self.assertEqual(self.events_of(b, "EXPORT_DEFERRED"), [])
                self.assertNotIn(b, self.scheduler._sequential_exports)

    def test_a_hand_back_during_a_stop_after_the_stage_pauses_the_export(self):
        b = self.running_export("b", "not_nvenc")
        self.store.update_job(b, stop_mode="AFTER_STAGE")
        self.scheduler._hand_back_export(b)
        job = self.store.get_job(b)
        self.assertEqual((job["state"], job["stop_mode"], job["current_stage"]), ("PAUSED", "PAUSED", "render"))
        self.assertEqual(self.store.stage(b, "render")["state"], "PENDING")
        (event,) = self.events_of(b, "EXPORT_DEFERRED")
        self.assertEqual((event["message"], event["payload"]), (EXPORT_DEFERRED_MESSAGES["not_nvenc"],
                                                                {"reason": "not_nvenc", "fallback": None}))
        # Tiếp tục: an export again, taken by the main lane.
        self.scheduler.resume(b)
        job, stage, _definition = self.scheduler._select()
        self.assertEqual((job["id"], stage["name"]), (b, "render"))

    def test_an_export_the_lane_turns_down_runs_first_in_the_main_lane(self):
        cases = (
            ("vram_short", {"vram": VramRead(total_mib=8192, used_mib=1000, free_mib=7192)}),
            ("vram_unreadable", {"vram": None}),
            ("forced_x264", {"render_settings": {"video_encoder": "libx264"}}),
            ("nvenc_not_confirmed", {"profile": "failed"}),
            ("nvenc_not_confirmed", {"profile": None}),
            ("size_not_measured", {"probe": {"streams": [{"codec_type": "video", "width": 3840, "height": 2160}]}}),
            ("size_unknown", {"probe": {"streams": []}}),
        )
        for index, (reason, case) in enumerate(cases):
            with self.subTest(reason=reason, case=case):
                self.vram = case.get("vram", VramRead(total_mib=12288, used_mib=1000, free_mib=11288))
                self.profile.return_value = case.get("profile", "ok")
                self.probe = case.get("probe", self.FULL_HD)
                a, b = self.job(f"a{index}"), self.job(f"b{index}")
                self.start(a)
                self.export_auto(b, render_settings=case.get("render_settings"))
                job, _stage, definition = self.scheduler._select()
                self.assertEqual((job["id"], definition.name), (b, "render"))
                self.scheduler._select()  # judged once per export request
                (event,) = self.events_of(b, "EXPORT_LANE_SEQUENTIAL")
                self.assertEqual(event["payload"]["reason"], reason)
                self.assertEqual(event["message"], REASON_TEXT[reason])
                # Nor does the export lane take it beside a GPU stage.
                self.scheduler._gpu_stage = _GpuStage(index + 1, a, "scan_one", 1000)
                try:
                    self.assertIsNone(self.scheduler._select(EXPORT_LANE))
                finally:
                    self.scheduler._gpu_stage = None
                self.scheduler.cancel(a)
                self.scheduler.cancel(b)

    def test_until_the_vram_is_measured_auto_is_the_single_worker_of_before(self):
        # As shipped before the A1b measurement: no NVML read, no lane event, no export lane.
        for index, name in enumerate(("NVENC_NEED_MIB", "SCAN_PEAK_MIB", "SAFETY_MARGIN_MIB")):
            with self.subTest(unmeasured=name), patch(f"biliflow.export_lanes.{name}", None):
                # (not "a0": job() pads the name with "0" into its fake SHA-256, as "a" below)
                a, b = self.job(f"u{index}a"), self.job(f"u{index}b")
                self.start(a)
                self.export_auto(b)
                job, _stage, definition = self.scheduler._select()
                self.assertEqual((job["id"], definition.name), (b, "render"))
                self.scheduler._gpu_stage = _GpuStage(index + 1, a, "scan_one", 1000)
                try:
                    self.assertIsNone(self.scheduler._select(EXPORT_LANE))
                finally:
                    self.scheduler._gpu_stage = None
                self.assertEqual(self.events_of(b, "EXPORT_LANE_SEQUENTIAL"), [])
                self.scheduler.cancel(a)
                self.scheduler.cancel(b)
        with patch.multiple("biliflow.export_lanes", NVENC_NEED_MIB=None, SCAN_PEAK_MIB=None, SAFETY_MARGIN_MIB=None):
            a, b = self.job("a"), self.job("b")
            self.start(a)
            self.export_auto(b)
            self.scheduler.start()
            render = self.wait_for(f"render:{b}")
            self.assertNotIn("env", render.kwargs)
            time.sleep(0.5)  # nothing starts beside the export
            self.assertEqual(self.kinds(), [f"render:{b}"])
            render.finish(0)
            scan = self.wait_for("a:scan_one")
            self.assertIsNone(self.scheduler._gpu_stage)
            scan.finish(0)
            self.wait_for("a:scan_two").finish(0)
            self.wait_until(lambda: not self.scheduler.is_busy(a), "the scan job never finished")
            self.assertEqual(self.kinds(), [f"render:{b}", "a:scan_one", "a:scan_two"])
        self.scheduler.read_vram.assert_not_called()
        self.assertEqual(self.events_of(b, "EXPORT_LANE_SEQUENTIAL") + self.events_of(b, "EXPORT_LANE_PARALLEL"), [])

    def test_an_export_never_waits_for_an_export_lane_whose_thread_ended(self):
        a, b = self.job("a"), self.job("b")
        self.start(a)
        self.export_auto(b)
        job, stage, _definition = self.scheduler._select()
        self.assertEqual((job["id"], stage["name"]), (a, "scan_one"))  # b would go beside the scan
        ended = threading.Thread(target=lambda: None)
        ended.start()
        ended.join()
        self.scheduler._threads[EXPORT_LANE] = ended  # an error ended the export lane's thread
        job, stage, _definition = self.scheduler._select()
        self.assertEqual((job["id"], stage["name"]), (b, "render"))

    def test_a_scan_stage_on_the_cpu_never_gets_an_export_beside_it(self):
        self.device = "cpu"
        a, b = self.job("a"), self.job("b")
        self.start(a)
        self.export_auto(b)
        job, _stage, definition = self.scheduler._select()
        self.assertEqual((job["id"], definition.name), (b, "render"))
        self.scheduler.read_vram.assert_not_called()
        self.assertEqual(self.events_of(b, "EXPORT_LANE_SEQUENTIAL"), [])
        self.scheduler.cancel(b)
        # Nor in the scheduler's threads: no VRAM baseline, no export lane.
        c = self.job("c")
        self.export_auto(c)
        self.scheduler.start()
        self.wait_for(f"render:{c}").finish(0)
        scan = self.wait_for("a:scan_one")
        self.assertIsNone(self.scheduler._gpu_stage)
        scan.finish(0)
        self.wait_for("a:scan_two").finish(0)
        self.wait_until(lambda: not self.scheduler.is_busy(a), "the scan job never finished")
        self.scheduler.read_vram.assert_not_called()

    def test_the_export_lane_runs_only_beside_a_gpu_stage_of_the_main_lane(self):
        a, b = self.job("a"), self.job("b")
        self.start(a)
        self.export_auto(b)
        self.assertIsNone(self.scheduler._select(EXPORT_LANE))  # nothing on the GPU
        self.scheduler._gpu_stage = _GpuStage(1, a, "scan_one", 1000)
        self.store.set_setting("scheduler_paused", True)
        self.assertIsNone(self.scheduler._select(EXPORT_LANE))  # Tạm dừng hàng đợi stops both lanes
        self.store.set_setting("scheduler_paused", False)
        self.scheduler.set_parallel_export_mode("off")
        self.assertIsNone(self.scheduler._select(EXPORT_LANE))
        self.scheduler.set_parallel_export_mode("auto")
        self.scheduler._executing[EXPORT_LANE] = 99  # one export at a time
        self.assertIsNone(self.scheduler._select(EXPORT_LANE))
        del self.scheduler._executing[EXPORT_LANE]
        job, stage, _definition = self.scheduler._select(EXPORT_LANE)
        self.assertEqual((job["id"], stage["name"]), (b, "render"))
        self.assertEqual(self.scheduler._parallel_decisions[b].detail["scan_job_id"], a)
        with self.assertRaises(ValueError):
            self.scheduler._select("other")

    def test_the_main_lane_runs_the_gpu_scan_when_the_export_will_go_beside_it(self):
        a, b, c = self.job("a"), self.job("b"), self.job("c")
        self.start(a)
        self.export_auto(b)
        self.export_auto(c)
        job, stage, _definition = self.scheduler._select()
        self.assertEqual((job["id"], stage["name"]), (a, "scan_one"))
        # While the export lane runs b, c waits for it (one render at a time) and the scan goes on.
        self.scheduler._executing[EXPORT_LANE] = b
        self.assertTrue(self.store.claim_queued(b, "RENDERING", "render"))
        job, stage, _definition = self.scheduler._select()
        self.assertEqual((job["id"], stage["name"]), (a, "scan_one"))
        self.scheduler.cancel(a)
        self.assertIsNone(self.scheduler._select())
        del self.scheduler._executing[EXPORT_LANE]
        job, stage, _definition = self.scheduler._select()
        self.assertEqual((job["id"], stage["name"]), (c, "render"))

    def test_a_new_export_request_starts_clean(self):
        a, b = self.job("a"), self.job("b")
        self.start(a)
        self.export_auto(b)
        record = self.write_deferral(b, "encoder_error")
        self.scheduler._sequential_exports.add(b)
        # Handed back, it waits only for the scan job it ran beside (none here).
        self.assertEqual(self.scheduler._select()[0]["id"], b)
        self.export_auto(b)  # Xuất video again
        self.assertFalse(record.exists())
        self.assertNotIn(b, self.scheduler._sequential_exports)
        job, stage, _definition = self.scheduler._select()
        self.assertEqual((job["id"], stage["name"]), (a, "scan_one"))  # NVENC beside the scan again

    def test_a_lane_never_clears_the_other_lanes_wake_up(self):
        wake = _LaneWake((MAIN_LANE, EXPORT_LANE))
        wake.set()
        started = time.monotonic()
        wake.wait(EXPORT_LANE, 5)  # at once; clears only the export lane's flag
        wake.wait(MAIN_LANE, 5)  # still set for the main lane: at once too
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(any(event.is_set() for event in wake._events.values()))
        self.assertIsInstance(self.scheduler._wake, _LaneWake)

    def test_the_switch_is_auto_or_off(self):
        self.assertEqual(self.scheduler.parallel_export_mode(), "auto")
        with self.assertRaises(ValueError):
            self.scheduler.set_parallel_export_mode("fast")
        self.assertIsNone(self.store.setting("parallel_export"))
        self.store.set_setting("parallel_export", "sideways")
        self.assertEqual(self.scheduler.parallel_export_mode(), "auto")
        self.scheduler.set_parallel_export_mode("off")
        self.assertEqual(self.scheduler.parallel_export_mode(), "off")

    # Perf plan A1c: a film that has not started yet waits at preflight (CPU, no process).

    def new_film(self, name="a"):
        """A film queued for its first scan: its next stage is preflight."""
        self.preflight = True
        job_id = self.job(name)
        self.start(job_id)
        return job_id

    def complete(self, job_id, *names):
        for name in names:
            self.store.update_stage(job_id, name, state="COMPLETED", progress=1.0)

    def assert_takes(self, job_id, stage_name, lane=MAIN_LANE):
        job, stage, _definition = self.scheduler._select(lane)
        self.assertEqual((job["id"], stage["name"]), (job_id, stage_name))

    def held_preflight(self):
        """_run_preflight that waits for the test: (entered, release, patcher)."""
        entered, release = threading.Event(), threading.Event()

        def preflight(job):
            entered.set()
            release.wait(10)

        self.addCleanup(release.set)
        return entered, release, patch.object(self.scheduler, "_run_preflight", side_effect=preflight)

    def test_a_new_film_scans_while_queued_exports_go_beside_its_first_gpu_stage(self):
        # The case seen on 2026-10-14: a film waiting at preflight behind two exports.
        a = self.new_film()
        b, c = self.job("b"), self.job("c")
        self.export_auto(b)
        self.export_auto(c)
        entered, release, preflight = self.held_preflight()
        with preflight:
            self.scheduler.start()
            self.assertTrue(entered.wait(10))
            time.sleep(0.3)  # several looks of each lane while the film is checked
            # Neither export ran first: the main lane checks the film, the export lane waits for its GPU stage.
            self.assertEqual(self.kinds(), [])
            self.assertEqual(self.scheduler.active_lanes, [])
            self.assertTrue(self.scheduler.is_busy(a))
            self.assertEqual([self.store.get_job(job_id)["state"] for job_id in (a, b, c)],
                             ["PREFLIGHT", "QUEUED", "QUEUED"])
            release.set()
            scan = self.wait_for("a:scan_one")
            first = self.wait_for(f"render:{b}")
        self.wait_until(lambda: len(self.scheduler.active_lanes) == 2, "both lanes never ran")
        self.assertEqual(
            [(item["lane"], item["job_id"], item["stage"]) for item in self.scheduler.active_lanes],
            [(MAIN_LANE, a, "scan_one"), (EXPORT_LANE, b, "render")],
        )
        self.assertEqual(first.kwargs["env"][EXPORT_LANE_ENVIRONMENT], PARALLEL_EXPORT_LANE)
        self.assertNotIn("env", scan.kwargs)
        first.finish(0)
        # The second export goes beside the same GPU stage once the export lane is free.
        second = self.wait_for(f"render:{c}")
        self.assertEqual(second.kwargs["env"][EXPORT_LANE_ENVIRONMENT], PARALLEL_EXPORT_LANE)
        second.finish(0)
        self.wait_until(lambda: all(self.store.get_job(job_id)["state"] == "COMPLETED" for job_id in (b, c)),
                        "the exports never completed")
        self.assertIsNone(scan.poll())
        scan.finish(0)
        self.wait_for("a:scan_two").finish(0)
        self.wait_until(lambda: not self.scheduler.is_busy(a), "the scan job never finished")
        self.assertEqual(self.kinds(), ["a:scan_one", f"render:{b}", f"render:{c}", "a:scan_two"])
        self.assertEqual(self.terminated, [])
        for job_id in (b, c):
            (event,) = self.events_of(job_id, "EXPORT_LANE_PARALLEL")
            self.assertEqual((event["payload"]["scan_job_id"], event["payload"]["scan_stage"]), (a, "scan_one"))
            self.assertEqual(self.events_of(job_id, "EXPORT_LANE_SEQUENTIAL"), [])

    def test_at_preflight_the_export_is_judged_for_the_films_first_gpu_stage(self):
        a = self.new_film()
        b = self.job("b")
        self.export_auto(b)
        self.assert_takes(a, "preflight")
        self.scheduler.read_vram.assert_called_once_with()
        # Preflight runs no process: the export lane has no GPU stage to go beside yet.
        self.assertIsNone(self.scheduler._select(EXPORT_LANE))
        # At the GPU stage it is judged again (A1b) and the scan goes first again.
        self.complete(a, "preflight")
        self.assert_takes(a, "scan_one")
        self.assertEqual(self.scheduler.read_vram.call_count, 2)
        self.assertEqual(self.events_of(b, "EXPORT_LANE_SEQUENTIAL"), [])
        self.assertNotIn(b, self.scheduler._sequential_exports)

    def test_an_export_turned_down_at_preflight_runs_first_in_the_main_lane(self):
        cases = (
            ("vram_short", {"vram": VramRead(total_mib=8192, used_mib=1000, free_mib=7192)}),
            # A machine without an NVIDIA GPU: no NVML, no NVENC in the hardware profile.
            ("vram_unreadable", {"vram": None}),
            ("nvenc_not_confirmed", {"profile": "failed", "vram": None}),
            # The hardware profile was never measured ("Đo lại" not pressed yet).
            ("nvenc_not_confirmed", {"profile": None}),
            ("forced_x264", {"render_settings": {"video_encoder": "libx264"}}),
        )
        for index, (reason, case) in enumerate(cases):
            with self.subTest(reason=reason, case=case):
                self.vram = case.get("vram", VramRead(total_mib=12288, used_mib=1000, free_mib=11288))
                self.profile.return_value = case.get("profile", "ok")
                a, b = self.new_film(f"a{index}"), self.job(f"b{index}")
                self.export_auto(b, render_settings=case.get("render_settings"))
                self.assert_takes(b, "render")
                self.assert_takes(b, "render")  # judged once per export request
                (event,) = self.events_of(b, "EXPORT_LANE_SEQUENTIAL")
                self.assertEqual((event["payload"]["reason"], event["message"]), (reason, REASON_TEXT[reason]))
                if reason == "vram_short":
                    self.assertEqual(
                        {key: event["payload"][key] for key in ("total_mib", "baseline_mib", "need_mib", "left_mib")},
                        {"total_mib": 8192, "baseline_mib": 1000, "need_mib": 500, "left_mib": 8192 - 7000 - 500},
                    )
                # The export done, the film goes on as before.
                self.assertTrue(self.store.claim_queued(b, "RENDERING", "render"))
                self.store.update_job(b, state="COMPLETED", current_stage=None)
                self.assert_takes(a, "preflight")
                self.scheduler.cancel(a)

    def test_vram_short_at_the_gpu_stage_after_preflight_puts_the_export_first(self):
        a = self.new_film()
        b = self.job("b")
        self.export_auto(b)
        self.assert_takes(a, "preflight")
        self.complete(a, "preflight")
        # Another program took VRAM while the film was checked.
        self.vram = VramRead(total_mib=12288, used_mib=6000, free_mib=6288)
        self.assert_takes(b, "render")
        (event,) = self.events_of(b, "EXPORT_LANE_SEQUENTIAL")
        self.assertEqual(event["payload"]["reason"], "vram_short")

    def test_off_or_unmeasured_vram_keeps_the_export_ahead_of_a_new_film(self):
        self.scheduler.set_parallel_export_mode("off")
        a, b = self.new_film(), self.job("b")
        self.export_auto(b)
        self.assert_takes(b, "render")
        self.scheduler.set_parallel_export_mode("auto")
        with patch("biliflow.export_lanes.SCAN_PEAK_MIB", None):
            self.assert_takes(b, "render")
        self.scheduler.read_vram.assert_not_called()
        self.assertEqual(self.events_of(b, "EXPORT_LANE_SEQUENTIAL") + self.events_of(b, "EXPORT_LANE_PARALLEL"), [])
        self.assertEqual(self.store.get_job(a)["state"], "QUEUED")

    def test_a_film_with_no_gpu_stage_ahead_keeps_the_export_first(self):
        self.review = True
        cases = (
            # Every scan stage runs on the CPU.
            ("cpu device", lambda film: None, "cpu"),
            # Waiting at preflight with every GPU stage already done.
            ("gpu stages done", lambda film: self.complete(film, "scan_one", "scan_two"), "cuda"),
            # Half-way: only build_review (CPU) is left.
            ("only build_review left",
             lambda film: self.complete(film, "preflight", "scan_one", "scan_two"), "cuda"),
        )
        for index, (label, prepare, device) in enumerate(cases):
            with self.subTest(case=label):
                self.device = device
                a, b = self.new_film(f"a{index}"), self.job(f"b{index}")
                prepare(a)
                self.export_auto(b)
                self.assert_takes(b, "render")
                self.scheduler.read_vram.assert_not_called()
                self.assertEqual(self.events_of(b, "EXPORT_LANE_SEQUENTIAL"), [])
                self.scheduler.cancel(a)
                self.scheduler.cancel(b)

    def test_only_short_cpu_stages_are_looked_through(self):
        # A CPU stage with its own process before the first GPU stage is not short: the export goes first.
        self.cpu_before = ("prepare",)
        a, b = self.new_film(), self.job("b")
        self.export_auto(b)
        self.assert_takes(b, "render")
        self.scheduler.read_vram.assert_not_called()
        self.assertEqual(self.events_of(b, "EXPORT_LANE_SEQUENTIAL"), [])
        self.assertEqual(self.store.get_job(a)["state"], "QUEUED")

    def test_a_film_half_way_is_judged_at_its_own_gpu_stage_as_before(self):
        a = self.new_film()
        self.complete(a, "preflight", "scan_one")
        b = self.job("b")
        self.export_auto(b)
        self.assert_takes(a, "scan_two")
        self.scheduler.read_vram.assert_called_once_with()

    def test_a_film_that_leaves_the_queue_at_preflight_lets_the_export_run(self):
        def fail(film):
            selection = self.scheduler._select()
            with patch.object(self.scheduler, "_run_preflight", side_effect=FileNotFoundError("Source is missing")):
                self.scheduler._execute(*selection)
            self.assertEqual(self.store.get_job(film)["state"], "FAILED")

        for index, (label, leave) in enumerate((
            ("preflight fails", fail),
            ("Dừng ngay", self.scheduler.pause_now),
            ("Hủy", self.scheduler.cancel),
        )):
            with self.subTest(case=label):
                a, b = self.new_film(f"a{index}"), self.job(f"b{index}")
                self.export_auto(b)
                self.assert_takes(a, "preflight")
                leave(a)
                self.assert_takes(b, "render")
                self.scheduler.cancel(b)

    def test_gpu_stages_restored_from_the_cache_still_let_the_export_run_at_build_review(self):
        # A cache hit runs no process: the export lane never gets a GPU stage to go beside.
        self.review = True
        a, b = self.new_film(), self.job("b")
        self.export_auto(b)
        for stage in ("preflight", "scan_one", "scan_two"):
            self.assert_takes(a, stage)
            self.assertIsNone(self.scheduler._select(EXPORT_LANE))
            self.complete(a, stage)
        # build_review (CPU) is next: no GPU stage left, the export goes first as before.
        self.assert_takes(b, "render")
        self.assertEqual(self.events_of(b, "EXPORT_LANE_SEQUENTIAL"), [])

    def test_the_lookahead_follows_the_stages_the_store_hands_out_next(self):
        # A GPU stage to try again (FAILED_RETRYABLE) after preflight counts like a waiting one.
        a, b = self.new_film(), self.job("b")
        self.store.update_stage(a, "scan_one", state="FAILED_RETRYABLE")
        self.export_auto(b)
        self.assert_takes(a, "preflight")
        self.scheduler.cancel(a)
        self.scheduler.cancel(b)
        # A stored stage this pipeline no longer defines stops the lookahead: the export goes first.
        self.cpu_before = ("retired",)
        c = self.new_film("c")
        self.cpu_before = ()
        d = self.job("d")
        self.export_auto(d)
        self.assert_takes(d, "render")
        self.assertEqual(self.store.get_job(c)["state"], "QUEUED")

    def test_an_export_never_waits_at_preflight_for_an_export_lane_whose_thread_ended(self):
        a, b = self.new_film(), self.job("b")
        self.export_auto(b)
        self.assert_takes(a, "preflight")
        ended = threading.Thread(target=lambda: None)
        ended.start()
        ended.join()
        self.scheduler._threads[EXPORT_LANE] = ended
        self.assert_takes(b, "render")

    def test_a_handed_back_export_never_waits_for_a_new_film_at_preflight(self):
        a = self.new_film()
        self.complete(a, "preflight", "scan_one")
        b = self.job("b")
        c = self.new_film("c")  # clicked later, never started
        self.export_auto(b)
        # b ran beside a's scan_one; NVENC failed and the renderer handed it back.
        self.scheduler._last_scan_job_id = a
        self.write_deferral(b, "encoder_error")
        self.scheduler._sequential_exports.add(b)
        # The scan job it ran beside finishes first, as in A1b...
        self.assert_takes(a, "scan_two")
        self.complete(a, "scan_two")
        self.store.update_job(a, state="WAITING_REVIEW", current_stage=None)
        # ...then the export, with x264 in the main lane, before the new film's preflight.
        self.assert_takes(b, "render")
        self.assertIsNone(self.scheduler._select(EXPORT_LANE))
        self.scheduler.read_vram.assert_not_called()
        self.assertEqual(self.store.get_job(c)["state"], "QUEUED")

    def test_the_render_watch_still_stops_an_export_that_went_beside_a_new_film(self):
        a = self.new_film()
        b = self.job("b")
        self.export_auto(b)
        entered, release, preflight = self.held_preflight()
        with preflight:
            self.scheduler.start()
            self.assertTrue(entered.wait(10))
            release.set()
            scan = self.wait_for("a:scan_one")
            render = self.wait_for(f"render:{b}")
        record, message = self.write_stall(b, stall_limit(longest_cut_seconds=0.0, parallel=True))
        render.finish(2)
        self.assert_failed_by_the_watch(b, message)
        self.assertFalse(record.exists())
        self.assertIsNone(scan.poll())
        scan.finish(0)
        self.wait_for("a:scan_two").finish(0)
        self.wait_until(lambda: not self.scheduler.is_busy(a), "the scan job never finished")
        self.assertEqual(self.kinds(), ["a:scan_one", f"render:{b}", "a:scan_two"])


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
