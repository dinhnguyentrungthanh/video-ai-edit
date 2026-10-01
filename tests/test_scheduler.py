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


if __name__ == "__main__":
    unittest.main()
