import hashlib
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow.export_identity import legacy_export_paths
from biliflow.job_import import import_existing_project
from biliflow.job_pipeline import PipelineStage
from biliflow.job_store import JobStore
from biliflow.review_workflow import approved_operations, review_export_paths
from biliflow.scheduler import InputWatcher, JobScheduler


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
            "items": [{"id": "a", "category": "advertising", "decision": "BLUR", "start_seconds": 1,
                       "end_seconds": 2, "reasons": ["logo"], "evidence": [],
                       "decision_region_source_pixels": {"x": 0, "y": 0, "width": 96, "height": 48}}],
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

    def write_manifest(self, target, queue=None, **changes):
        """The export the renderer leaves at ``target``: the file and a manifest that proves it."""
        target.write_bytes(b"rendered")
        manifest = {
            "status": "COMPLETED", "created_at": "2026-10-01T01:00:00+00:00",
            "source": {"path": str(self.source), "sha256": self.digest, "modified": False},
            "output": {"path": target.relative_to(self.root).as_posix(), "bytes": target.stat().st_size,
                       "sha256": "f" * 64},
            "encoding": {"full_decode_validation_passed": True},
            "operations": approved_operations(self.queue if queue is None else queue),
        }
        manifest.update(changes)
        target.with_suffix(target.suffix + ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

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
        self.write_manifest(stale_output, stale)
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

    def clean(self, state="RECYCLED", job_id=None):
        """A "Dọn video gốc" row as execute_cleanup leaves it (the watcher row is reset)."""
        job = self.store.get_job(self.job_id if job_id is None else job_id)
        source = Path(job["source_path"])
        row_id = self.store.add_source_cleanup(
            job_id=int(job["id"]), kind="EXPORTED", source_path=str(source.resolve()),
            source_sha256=job["source_sha256"], size_bytes=job["source_size_bytes"],
            mtime_ns=job["source_mtime_ns"], output_path="output/movie-reviewed.mp4",
        )
        if state == "RECYCLED":  # the file left input/
            source.unlink()
            self.store.finish_source_cleanup(row_id, state=state, verified=True)
            self.store.reset_watched_file(source)
        elif state == "FAILED":  # the file never moved
            self.store.finish_source_cleanup(row_id, state=state, error="File đang được mở")
        return row_id

    def identity(self, job_id=None):
        job = self.store.get_job(self.job_id if job_id is None else job_id)
        keys = ("source_path", "source_sha256", "source_size_bytes", "source_mtime_ns", "state",
                "active_queue_path", "active_revision", "content_style", "progress", "updated_at")
        return ({key: job[key] for key in keys}, self.store.revisions(int(job["id"])),
                self.store.artifacts(int(job["id"])), len(self.store.list_jobs()))

    def test_a_different_file_at_a_cleaned_path_is_left_to_the_watcher(self):
        # The queue still names input/movie.mp4: without the cleaned-path skip the
        # import would take the old sha from it and give the job this file's size/mtime.
        self.store.update_job(self.job_id, state="COMPLETED", progress=1.0)
        row_id = self.clean()
        self.source.write_bytes(b"a different video at the old name")
        before = self.identity()
        with patch("biliflow.job_import.sha256_file", side_effect=AssertionError("hashed by import")), \
                patch("biliflow.job_import.probe_video", side_effect=AssertionError("probed by import")):
            self.restart()
        self.assertEqual(self.identity(), before)
        self.assertEqual(self.store.latest_source_cleanup(self.job_id)["state"], "RECYCLED")
        # The import did not mark the path either: the watcher hashes it once
        # stable, rejects it for the old job and makes it a new job.
        watcher = InputWatcher(self.root, self.store, stable_seconds=0)
        with patch("biliflow.scheduler.probe_video", return_value={}), \
                patch("biliflow.scheduler.duration_seconds", return_value=12.0):
            self.assertEqual(watcher.scan_once(), 1)
        rejected = [event for event in self.store.events(self.job_id) if event["event_type"] == "SOURCE_RESTORE_REJECTED"]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["payload"]["expected_sha256"], self.digest)
        self.assertEqual(self.store.latest_source_cleanup(self.job_id)["id"], row_id)
        self.assertEqual(len(self.store.list_jobs()), 2)

    def test_a_cleaned_path_no_queue_names_is_never_hashed_or_imported(self):
        # A job the watcher discovered (no historical queue names its path):
        # the import must not hash the new file there and make it a job itself,
        # or the watcher would never reject it for the cleaned job.
        other = self.root / "input" / "other.mp4"
        other.write_bytes(b"other source")
        other_id = int(self.store.upsert_job(
            job_key="other", source_path=other, source_sha256=hashlib.sha256(b"other source").hexdigest(),
            source_size_bytes=12, source_mtime_ns=other.stat().st_mtime_ns,
            content_style="live_action", state="COMPLETED",
        )["id"])
        self.clean(job_id=other_id)
        other.write_bytes(b"a replacement video")
        before = self.identity(other_id)
        with patch("biliflow.job_import.sha256_file", side_effect=AssertionError("hashed by import")), \
                patch("biliflow.job_import.probe_video", side_effect=AssertionError("probed by import")):
            self.restart()
        self.assertEqual(self.identity(other_id), before)
        watcher = InputWatcher(self.root, self.store, stable_seconds=0)
        with patch("biliflow.scheduler.probe_video", return_value={}), \
                patch("biliflow.scheduler.duration_seconds", return_value=12.0):
            self.assertEqual(watcher.scan_once(), 1)
        rejected = [event for event in self.store.events(other_id) if event["event_type"] == "SOURCE_RESTORE_REJECTED"]
        self.assertEqual(len(rejected), 1)
        self.assertTrue(self.store.source_cleaned(other_id))

    def test_the_cleaned_video_under_another_name_does_not_move_the_job(self):
        self.store.update_job(self.job_id, state="COMPLETED", progress=1.0)
        self.clean()
        renamed = self.root / "input" / "movie (restored).mp4"
        renamed.write_bytes(b"source")
        before = self.identity()
        with patch("biliflow.job_import.probe_video", side_effect=AssertionError("probed by import")):
            self.restart()
        self.assertEqual(self.identity(), before)
        self.assertEqual(self.store.get_job(self.job_id)["source_path"], str(self.source.resolve()))
        self.assertTrue(self.store.source_cleaned(self.job_id))

    def test_a_pending_cleanup_is_left_alone_by_the_import(self):
        # The shell call has not answered: the file is still there, unchanged.
        self.store.update_job(self.job_id, state="COMPLETED", progress=1.0)
        self.clean(state="PENDING")
        self.source.write_bytes(b"source, but rewritten")
        before = self.identity()
        with patch("biliflow.job_import.sha256_file", side_effect=AssertionError("hashed by import")):
            self.restart()
        self.assertEqual(self.identity(), before)
        self.assertEqual(self.store.latest_source_cleanup(self.job_id)["state"], "PENDING")

    def test_a_restored_or_failed_cleanup_imports_as_before(self):
        # Only PENDING/RECYCLED rows lock the job; the export of the active
        # review still completes a job whose source came back.
        for state in ("RESTORED", "FAILED"):
            with self.subTest(state=state):
                self.store.update_job(self.job_id, state="READY_TO_EXPORT", progress=0.0)
                row_id = self.clean(state="RECYCLED" if state == "RESTORED" else "FAILED")
                if state == "RESTORED":
                    self.source.write_bytes(b"source")
                    self.assertIsNotNone(self.store.mark_source_restored(
                        row_id, mtime_ns=self.source.stat().st_mtime_ns))
                self.assertEqual(self.store.latest_source_cleanup(self.job_id)["state"], state)
                self.write_manifest(review_export_paths(self.root, self.queue)[1])
                after = self.restart()
                self.assertEqual((after["state"], after["progress"]), ("COMPLETED", 1.0))
                self.assertEqual(after["source_mtime_ns"], self.source.stat().st_mtime_ns)
                self.assertEqual(len(self.store.list_jobs()), 1)

    def test_a_file_its_manifest_does_not_prove_does_not_complete_the_job(self):
        output = review_export_paths(self.root, self.queue)[1]
        for name, changes in (
            ("size", {"output": {"path": output.relative_to(self.root).as_posix(), "bytes": 3, "sha256": "f" * 64}}),
            ("decode", {"encoding": {}}),
            ("operations", {"operations": []}),
            ("beside another file", {"output": {"path": "output/other-reviewed.mp4", "bytes": 8, "sha256": "f" * 64}}),
        ):
            with self.subTest(case=name):
                if name == "beside another file":
                    (self.root / "output" / "other-reviewed.mp4").write_bytes(b"rendered")
                self.write_manifest(output, **changes)
                self.assertEqual(self.restart()["state"], "READY_TO_EXPORT")
                self.assertFalse([item for item in self.store.artifacts(self.job_id) if item["kind"] == "final_output"])
        # The review's own path with no manifest at all.
        output.with_suffix(".mp4.manifest.json").unlink()
        output.write_bytes(b"not a video")
        self.assertEqual(self.restart()["state"], "READY_TO_EXPORT")

    def test_an_export_named_before_the_operations_hash_completes_the_job(self):
        legacy = legacy_export_paths(self.root, self.queue)[1]
        self.assertNotEqual(legacy, review_export_paths(self.root, self.queue)[1])
        self.write_manifest(legacy)
        after = self.restart()
        self.assertEqual((after["state"], after["progress"]), ("COMPLETED", 1.0))
        outputs = [item["path"] for item in self.store.artifacts(self.job_id) if item["kind"] == "final_output"]
        self.assertEqual(outputs, [legacy.relative_to(self.root).as_posix()])

    def test_a_hostile_manifest_in_output_never_stops_the_start(self):
        # On main a manifest like these in output/ aborted the import, so the
        # Control Center could not start at all.
        stray = self.root / "output" / "stray-reviewed.mp4.manifest.json"
        for name, text in (
            ("source is a string", json.dumps({"status": "COMPLETED", "source": "x"})),
            ("output is a string", json.dumps({"status": "COMPLETED", "source": {"sha256": self.digest},
                                               "output": "x"})),
            ("NUL in the output path", json.dumps({"status": "COMPLETED", "source": {"sha256": self.digest},
                                                   "output": {"path": "output/bad\0name-reviewed.mp4"}})),
            ("nested too deep", "[" * 100_000),
        ):
            with self.subTest(case=name):
                stray.write_text(text, encoding="utf-8")
                self.assertEqual(self.restart()["state"], "READY_TO_EXPORT")
        # A proven export beside it still completes the job.
        self.write_manifest(legacy_export_paths(self.root, self.queue)[1])
        self.assertEqual(self.restart()["state"], "COMPLETED")

    def test_a_hostile_review_queue_never_stops_the_start(self):
        # On main each of these in reports/ aborted the import (KeyError, ValueError).
        broken = self.root / "reports" / "jobs" / "broken" / "review-queue.json"
        broken.parent.mkdir(parents=True)
        source = dict(self.queue["source"])
        for name, changes in (
            ("no path", {"source": {"sha256": self.digest}}),
            ("NUL in the path", {"source": dict(source, path="input/bad\0name.mp4")}),
            ("text duration", {"source": dict(source, duration_seconds="dài")}),
            ("status is an object", {"status": {"x": 1}}),
            ("reports is a number", {"reports": 5}),
        ):
            with self.subTest(case=name):
                broken.write_text(json.dumps(dict(self.queue, **changes)), encoding="utf-8")
                self.assertEqual(self.restart()["state"], "READY_TO_EXPORT")
                self.assertEqual(len(self.store.revisions(self.job_id)), 1)

    def test_an_output_folder_reached_through_a_junction_still_imports(self):
        # .resolve().relative_to(root) raised ValueError when output/ is a junction elsewhere.
        try:
            import _winapi
            create_junction = _winapi.CreateJunction
        except (ImportError, AttributeError):
            self.skipTest("directory junctions need Windows")
        elsewhere = TemporaryDirectory()
        self.addCleanup(elsewhere.cleanup)
        junction = self.root / "output"
        junction.rmdir()
        create_junction(elsewhere.name, str(junction))
        self.addCleanup(os.rmdir, junction)
        legacy = legacy_export_paths(self.root, self.queue)[1]
        self.write_manifest(legacy)
        after = self.restart()
        self.assertEqual((after["state"], after["progress"]), ("COMPLETED", 1.0))
        outputs = [item["path"] for item in self.store.artifacts(self.job_id) if item["kind"] == "final_output"]
        self.assertEqual(outputs, [legacy.relative_to(self.root).as_posix()])

    def test_an_old_name_export_of_another_edge_mode_does_not_complete_the_job(self):
        legacy = legacy_export_paths(self.root, self.queue)[1]
        self.write_manifest(legacy)
        changed = json.loads(json.dumps(self.queue))
        changed["items"][0]["decision_blur_edge_mode"] = "vertical_only"
        self.queue_path.write_text(json.dumps(changed), encoding="utf-8")
        self.assertEqual(legacy_export_paths(self.root, changed)[1], legacy)
        self.assertEqual(self.restart()["state"], "READY_TO_EXPORT")

    def first_import_state(self, **changes):
        """The job state a first import (no database) gives the source of one old export."""
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
            manifest = {
                "status": "COMPLETED", "created_at": "2026-10-01T01:00:00+00:00",
                "source": {"sha256": self.digest, "modified": False},
                "output": {"path": "output/old-export.mp4", "bytes": 8, "sha256": "f" * 64},
                "encoding": {"full_decode_validation_passed": True},
            }
            manifest.update(changes)
            (root / "output" / "old-export.mp4.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            store = JobStore(root / "state" / "jobs.sqlite3")
            try:
                import_existing_project(root, store)
                return store.list_jobs()[0]["state"]
            finally:
                store.close()

    def test_first_import_still_uses_any_completed_manifest(self):
        # Any review's export of the source, but only a file its manifest proves.
        self.assertEqual(self.first_import_state(), "COMPLETED")
        for changes in (
            {"encoding": {}},
            {"output": {"path": "output/old-export.mp4", "bytes": 3, "sha256": "f" * 64}},
            {"source": {"sha256": self.digest, "modified": True}},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(self.first_import_state(**changes), "READY_TO_EXPORT")


if __name__ == "__main__":
    unittest.main()
