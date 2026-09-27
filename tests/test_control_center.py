import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from biliflow.control_center import (
    ControlCenter,
    _dashboard_html,
    _merge_visual_audit_batches,
)
from biliflow.job_store import JobStore
from biliflow.final_renderer import render_progress_path


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


if __name__ == "__main__":
    unittest.main()
