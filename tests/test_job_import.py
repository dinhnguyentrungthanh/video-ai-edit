import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow.job_import import import_existing_project
from biliflow.job_pipeline import PipelineStage
from biliflow.job_store import JobStore
from biliflow.review_workflow import review_export_paths
from biliflow.scheduler import JobScheduler


class JobImportTests(unittest.TestCase):
    def test_benchmark_queues_cannot_replace_production_revision(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("input", "reports/production", "reports/benchmarks/native/run",
                         "reports/jobs/isolated/nested", "output", "state"):
                (root / name).mkdir(parents=True, exist_ok=True)
            source = root / "input/movie.mp4"
            source.write_bytes(b"source")
            queue = {"status": "REVIEW_REQUIRED", "updated_at": "2026-01-01",
                "source": {"path": str(source), "sha256": hashlib.sha256(b"source").hexdigest(),
                           "duration_seconds": 12}, "reports": [], "items": [{"decision": "BLUR"}]}
            production = root / "reports/production/review-queue.json"
            production.write_text(json.dumps(queue), encoding="utf-8")
            original = production.read_bytes()
            queue.update(updated_at="2026-12-01", items=[])
            for name in ("reports/benchmarks/native/run", "reports/jobs/isolated/nested"):
                (root / name / "review-queue.json").write_text(json.dumps(queue), encoding="utf-8")
            (root / "reports/jobs/isolated/.biliflow-benchmark").write_text("Isolated trial", encoding="utf-8")
            store = JobStore(root / "state/jobs.sqlite3")
            try:
                self.assertEqual(import_existing_project(root, store)["revisions"], 1)
                self.assertEqual(store.list_jobs()[0]["active_queue_path"], "reports/production/review-queue.json")
                self.assertEqual(production.read_bytes(), original)
            finally:
                store.close()

    def test_import_preserves_queue_and_activates_latest(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("input", "reports/r1", "output", "state"):
                (root / name).mkdir(parents=True, exist_ok=True)
            source = root / "input" / "movie.mp4"
            source.write_bytes(b"source")
            digest = hashlib.sha256(b"source").hexdigest()
            report = root / "reports" / "r1" / "scan.json"
            report.write_text(json.dumps({"content_style": "animation"}), encoding="utf-8")
            queue = {
                "status": "REVIEW_REQUIRED", "updated_at": "2026-01-01T00:00:00+00:00",
                "source": {"path": str(source), "sha256": digest, "duration_seconds": 12.0},
                "reports": ["reports/r1/scan.json"], "items": [],
            }
            queue_path = root / "reports" / "r1" / "review-queue.json"
            original = json.dumps(queue)
            queue_path.write_text(original, encoding="utf-8")
            store = JobStore(root / "state" / "jobs.sqlite3")
            try:
                result = import_existing_project(root, store)
                self.assertEqual(result, {"sources": 1, "revisions": 1})
                job = store.list_jobs()[0]
                self.assertEqual(job["content_style"], "animation")
                self.assertEqual(job["state"], "WAITING_REVIEW")
                self.assertEqual(queue_path.read_text(encoding="utf-8"), original)
            finally:
                store.close()



class RestartImportTests(unittest.TestCase):
    """A Control Center restart must not overwrite what the user queued or decided."""

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        for name in ("input", "reports/jobs/movie", "output", "state", "work", "logs"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.source = self.root / "input" / "movie.mp4"
        self.source.write_bytes(b"source")
        self.digest = hashlib.sha256(b"source").hexdigest()
        report = self.root / "reports/jobs/movie/scan.json"
        report.write_text(json.dumps({"content_style": "animation"}), encoding="utf-8")
        self.queue = {
            "status": "READY_FOR_EDIT_PLAN", "updated_at": "2026-10-01T00:00:00+00:00",
            "source": {"path": str(self.source), "sha256": self.digest, "duration_seconds": 12.0},
            "reports": ["reports/jobs/movie/scan.json"],
            "items": [{"id": "a", "decision": "BLUR", "start_seconds": 1, "end_seconds": 2}],
        }
        self.queue_path = self.root / "reports/jobs/movie/review-queue.json"
        self.queue_path.write_text(json.dumps(self.queue), encoding="utf-8")
        self.store = JobStore(self.root / "state" / "control-center.sqlite3")
        self.addCleanup(self.store.close)
        self.assertEqual(import_existing_project(self.root, self.store)["sources"], 1)
        self.job_id = int(self.store.list_jobs()[0]["id"])
        self.assertEqual(self.store.get_job(self.job_id)["state"], "READY_TO_EXPORT")

    def restart(self):
        """What ControlCenter.__init__ runs on every start."""
        self.store.recover_interrupted()
        import_existing_project(self.root, self.store)
        JobScheduler(self.root, self.store).recover_finished_stages()
        return self.store.get_job(self.job_id)

    def write_manifest(self, output):
        output.write_bytes(b"rendered")
        manifest = output.with_suffix(output.suffix + ".manifest.json")
        manifest.write_text(json.dumps({
            "status": "COMPLETED", "source": {"sha256": self.digest},
            "output": {"path": output.relative_to(self.root).as_posix(), "sha256": "f" * 64},
        }), encoding="utf-8")

    def test_protected_states_survive_a_restart_unchanged(self):
        # Old behaviour (temp/ui-plan/import_restart_check.py): every one of
        # these became READY_TO_EXPORT and a queued job silently left the queue.
        for state in ("QUEUED", "PAUSED", "INTERRUPTED_RECOVERABLE", "FAILED", "CANCELLED", "COMPLETED",
                      "SKIPPED"):
            with self.subTest(state=state):
                self.store.mark_queued(self.job_id, reseq=True)
                self.store.update_job(self.job_id, state=state, error="kept", content_style="mixed")
                before = self.store.get_job(self.job_id)
                after = self.restart()
                for key in ("state", "queue_seq", "queued_at", "active_queue_path",
                            "active_revision", "error", "content_style", "progress", "current_stage"):
                    self.assertEqual(after[key], before[key], key)

    def test_a_queued_rerun_survives_a_restart(self):
        # An export of the previous review exists; it must not mark the rerun done.
        self.write_manifest(review_export_paths(self.root, self.queue)[1])
        scheduler = JobScheduler(self.root, self.store)
        with patch("biliflow.scheduler.pipeline_stages",
                   return_value=[PipelineStage("preflight", "PREFLIGHT", tuple())]):
            queued = scheduler.rerun(self.job_id, detector_groups=["gore"])
            after = self.restart()
            self.assertEqual(scheduler.queue_order(), [{"job_id": self.job_id, "position": 1, "kind": "scan"}])
        self.assertEqual(after["state"], "QUEUED")
        self.assertIsNone(after["active_queue_path"])
        self.assertIsNone(after["active_revision"])
        self.assertEqual(after["queue_seq"], queued["queue_seq"])
        self.assertEqual(len(self.store.revisions(self.job_id)), 1)
        self.assertNotIn("final_output", {item["kind"] for item in self.store.artifacts(self.job_id)})

    def test_a_queued_export_survives_a_restart(self):
        scheduler = JobScheduler(self.root, self.store)
        plan, output, _ = review_export_paths(self.root, self.queue)
        queued = scheduler.queue_render(self.job_id, plan_path=plan, output_path=output)
        with patch("biliflow.scheduler.pipeline_stages", return_value=[]):
            after = self.restart()
            order = scheduler.queue_order()
        self.assertEqual((after["state"], after["current_stage"]), ("QUEUED", "render"))
        self.assertEqual(after["queue_seq"], queued["queue_seq"])
        self.assertEqual(order, [{"job_id": self.job_id, "position": 1, "kind": "export"}])
        self.assertEqual(self.store.setting(f"render:{self.job_id}")["output"],
                         output.relative_to(self.root).as_posix())

    def test_only_the_export_of_the_active_review_completes_a_reviewed_job(self):
        # An export made from different decisions (an older review) is ignored.
        stale = dict(self.queue, items=[{"id": "a", "decision": "KEEP", "start_seconds": 1, "end_seconds": 2}])
        stale_output = review_export_paths(self.root, stale)[1]
        self.assertNotEqual(stale_output, review_export_paths(self.root, self.queue)[1])
        self.write_manifest(stale_output)
        self.assertEqual(self.restart()["state"], "READY_TO_EXPORT")
        self.assertFalse([item for item in self.store.artifacts(self.job_id) if item["kind"] == "final_output"])
        # The export of the active review does complete it.
        self.write_manifest(review_export_paths(self.root, self.queue)[1])
        after = self.restart()
        self.assertEqual((after["state"], after["progress"]), ("COMPLETED", 1.0))
        outputs = [item["path"] for item in self.store.artifacts(self.job_id) if item["kind"] == "final_output"]
        self.assertEqual(outputs, [review_export_paths(self.root, self.queue)[1].relative_to(self.root).as_posix()])
        # A later restart leaves the completed job alone.
        self.assertEqual(self.restart()["state"], "COMPLETED")

    def test_a_crash_after_the_render_finished_completes_the_export_on_restart(self):
        scheduler = JobScheduler(self.root, self.store)
        plan, output, _ = review_export_paths(self.root, self.queue)
        scheduler.queue_render(self.job_id, plan_path=plan, output_path=output)
        with patch("biliflow.scheduler.pipeline_stages", return_value=[]):
            _job, _stage, definition = scheduler._select()
            self.assertTrue(self.store.claim_queued(self.job_id, definition.job_state, definition.name))
            # The render wrote its output and manifest and the stage completed,
            # then the process died before the job was marked COMPLETED.
            self.write_manifest(output)
            self.store.update_stage(self.job_id, "render", state="COMPLETED", progress=1.0)
            after = self.restart()
        self.assertEqual((after["state"], after["current_stage"], after["error"]), ("COMPLETED", None, None))
        self.assertIn(output.relative_to(self.root).as_posix(),
                      [item["path"] for item in self.store.artifacts(self.job_id) if item["kind"] == "final_output"])

    def test_a_crash_after_the_review_build_finished_opens_the_new_review_on_restart(self):
        stages = [PipelineStage("scan", "SCANNING_TEXT", tuple()),
                  PipelineStage("build_review", "BUILDING_REVIEW", tuple())]
        scheduler = JobScheduler(self.root, self.store)
        with patch("biliflow.scheduler.pipeline_stages", side_effect=lambda **_: list(stages)):
            scheduler.rerun(self.job_id)
            for name in ("scan", "build_review"):
                _job, _stage, definition = scheduler._select()
                self.assertEqual(definition.name, name)
                self.assertTrue(self.store.claim_queued(self.job_id, definition.job_state, name))
                self.store.update_stage(self.job_id, name, state="COMPLETED", progress=1.0)
                if name == "scan":
                    scheduler._after_success(self.job_id, name, definition)
            # build_review wrote the new queue, then the process died.
            key = scheduler._pipeline_key(self.store.get_job(self.job_id))
            queue_path = self.root / "reports" / "jobs" / key / "review-queue.json"
            queue_path.parent.mkdir(parents=True, exist_ok=True)
            queue_path.write_text(json.dumps(dict(
                self.queue, status="REVIEW_REQUIRED", updated_at="2026-10-02T00:00:00+00:00",
                items=[{"id": "n", "decision": None, "start_seconds": 1, "end_seconds": 2}],
            )), encoding="utf-8")
            after = self.restart()
            self.assertEqual(scheduler.queue_order(), [])
        self.assertEqual((after["state"], after["current_stage"], after["error"]), ("WAITING_REVIEW", None, None))
        self.assertEqual(after["active_queue_path"], queue_path.relative_to(self.root).as_posix())
        self.assertEqual(after["active_revision"], 2)

    def test_first_import_still_uses_any_completed_manifest(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for name in ("input", "reports/r1", "output", "state"):
                (root / name).mkdir(parents=True, exist_ok=True)
            source = root / "input" / "movie.mp4"
            source.write_bytes(b"source")
            queue = dict(self.queue, source=dict(self.queue["source"], path=str(source)), reports=[])
            (root / "reports/r1/review-queue.json").write_text(json.dumps(queue), encoding="utf-8")
            output = root / "output" / "old-export.mp4"
            output.write_bytes(b"rendered")
            (root / "output" / "old-export.mp4.manifest.json").write_text(json.dumps({
                "status": "COMPLETED", "source": {"sha256": self.digest},
                "output": {"path": "output/old-export.mp4"},
            }), encoding="utf-8")
            store = JobStore(root / "state" / "jobs.sqlite3")
            try:
                import_existing_project(root, store)
                self.assertEqual(store.list_jobs()[0]["state"], "COMPLETED")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
