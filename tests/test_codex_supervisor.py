import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow.codex_supervisor import (
    codex_subprocess_environment,
    collect_visual_evidence,
    load_ai_config,
    resolve_codex_command,
    resolve_codex_home,
    queue_coverage_findings,
    queue_quality_findings,
    queue_integrity_blockers,
    run_ai_audit,
    run_local_queue_audit,
    save_ai_config,
)


class FakeClient:
    def __init__(self, root):
        self.responses = iter([
            {"method": "item/agentMessage/delta", "params": {"delta": '{"result":"PASS","summary":"ok",'}},
            {"method": "item/agentMessage/delta", "params": {"delta": '"findings":[],"recommended_actions":[]}'}},
            {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
        ])
        self.calls = []

    def send(self, method, params=None, request=True):
        self.calls.append((method, params, request))
        return len(self.calls) if request else None

    def wait_response(self, identifier, timeout=30):
        if identifier == 3:
            return {"thread": {"id": "thread-1"}}
        return {}

    def next_message(self, timeout):
        return next(self.responses)

    def close(self):
        pass


class FakeVisualClient(FakeClient):
    def __init__(self, root):
        super().__init__(root)
        payload = {
            "result": "PASS",
            "summary": "visual ok",
            "findings": [],
            "recommended_actions": [],
            "visual_assessments": [{
                "item_id": "logo-1",
                "classification": "external_brand",
                "suggested_decision": "BLUR",
                "confidence": 0.99,
                "region_assessment": "TIGHT",
                "reasoning": "Visible external brand mark.",
            }],
        }
        self.responses = iter([
            {
                "method": "item/agentMessage/delta",
                "params": {"delta": json.dumps(payload)},
            },
            {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
        ])


class FakeResumeVisualClient(FakeVisualClient):
    def wait_response(self, identifier, timeout=30):
        if identifier == 3:
            return {"thread": {"id": "shared-supervisor"}}
        return {}


class FakeBlockClient(FakeClient):
    def __init__(self, root):
        super().__init__(root)
        self.responses = iter([
            {"method": "item/agentMessage/delta", "params": {"delta": '{"result":"BLOCK","summary":"qualitative",'}},
            {"method": "item/agentMessage/delta", "params": {"delta": '"findings":["possible issue"],"recommended_actions":[]}'}},
            {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
        ])


class CodexSupervisorTests(unittest.TestCase):
    def test_local_structure_audit_uses_no_model_or_quota(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            queue_path = root / "reports" / "job" / "review-queue.json"
            queue_path.parent.mkdir(parents=True)
            queue_path.write_text(json.dumps({
                "source": {"duration_seconds": 100, "sha256": "abc"},
                "reports": [], "items": [],
                "candidate_coverage": {
                    "complete": True, "missing_refs": [], "reports": [],
                },
                "detection_scope": {
                    "selected": ["advertising"],
                    "skipped": ["adult", "gore", "violence"],
                },
            }), encoding="utf-8")
            result = run_local_queue_audit(
                root=root, job={"job_key": "job"}, queue_path=queue_path,
            )
            self.assertEqual(result["result"], "PASS")
            self.assertFalse(result["execution"]["uses_codex"])
            self.assertFalse(result["execution"]["uses_chatgpt_quota"])
            self.assertEqual(
                result["detection_scope"]["selected"], ["advertising"]
            )

    def test_ai_block_without_deterministic_failure_is_advisory_warn(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            queue_path = root / "reports" / "job" / "review-queue.json"
            queue_path.parent.mkdir(parents=True)
            queue_path.write_text(json.dumps({
                "source": {"duration_seconds": 100, "sha256": "abc"},
                "reports": [], "items": [],
                "candidate_coverage": {
                    "complete": True, "missing_refs": [], "reports": [],
                },
            }), encoding="utf-8")
            self.assertEqual(queue_integrity_blockers(root, json.loads(
                queue_path.read_text(encoding="utf-8")
            )), [])
            result = run_ai_audit(
                root=root, job={"job_key": "job"}, queue_path=queue_path,
                connection_checker=lambda _root: {"ready": True},
                client_factory=FakeBlockClient,
            )
            self.assertEqual(result["result"], "WARN")
            self.assertEqual(result["deterministic_gate"]["status"], "PASS")
            self.assertTrue(any("advisory" in value for value in result["findings"]))

    def test_missing_report_is_a_deterministic_blocker(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            queue = {
                "source": {"duration_seconds": 100, "sha256": "abc"},
                "reports": ["reports/missing.json"], "items": [],
                "candidate_coverage": {
                    "complete": True, "missing_refs": [], "reports": [],
                },
            }
            blockers = queue_integrity_blockers(root, queue)
            self.assertTrue(any("missing" in value.casefold() for value in blockers))

    def test_preflight_catches_partial_opening_cut_and_untrimmed_logo_blur(self):
        queue = {
            "items": [
                {
                    "category": "visual_logo", "candidate_type": None,
                    "start_seconds": 0, "end_seconds": 5, "decision": "CUT",
                    "labels": ["Thẻ quảng bá phim khác"], "reasons": [],
                },
                {
                    "category": "visual_logo", "candidate_type": None,
                    "start_seconds": 5, "end_seconds": 10, "decision": "BLUR",
                    "labels": ["Logo"], "reasons": [],
                    "suggested_region_source_pixels": {"x": 130, "y": 104, "width": 159, "height": 67},
                    "model_evidence": {"region_sources": ["ocr"]},
                },
                {
                    "category": "visual_logo", "candidate_type": "persistent_overlay",
                    "start_seconds": 6, "end_seconds": 100, "decision": "BLUR",
                    "suggested_region_source_pixels": {"x": 90, "y": 10, "width": 250, "height": 210},
                    "decision_region_source_pixels": {"x": 90, "y": 10, "width": 250, "height": 210},
                },
                {
                    "category": "visual_logo", "candidate_type": None,
                    "start_seconds": 80, "end_seconds": 85, "decision": "BLUR",
                    "suggested_region_source_pixels": {"x": 128, "y": 104, "width": 162, "height": 67},
                    "model_evidence": {"region_sources": ["ocr"]},
                },
            ]
        }
        findings, actions = queue_quality_findings(queue)
        self.assertTrue(any("adjacent branded window" in value for value in findings))
        self.assertTrue(any("untrimmed grounding box" in value for value in findings))
        self.assertGreaterEqual(len(actions), 2)

    def test_preflight_accepts_a_kept_opening_studio_ident(self):
        # R3a/R3b (2026-10-01): a studio ident has no pre-selected CUT; KEEP is a valid human decision.
        ident = {"category": "visual_logo", "candidate_type": "opening_promotion", "start_seconds": 5,
                 "end_seconds": 10, "decision": "KEEP", "labels": [], "reasons": []}
        for extra in ({"opening_ident": True, "suggestion_withheld": {"reason": "opening_studio_ident_without_ad_text"}},
                      {"studio_logo_memory": {"remembered": True}}):
            findings, _ = queue_quality_findings({"items": [dict(ident, **extra)]})
            self.assertFalse(any("instead of CUT" in value for value in findings), extra)
        findings, _ = queue_quality_findings({"items": [ident]})
        self.assertTrue(any("instead of CUT" in value for value in findings))
        findings, _ = queue_quality_findings({"items": [dict(ident, decision="BLUR", opening_ident=True)]})
        self.assertTrue(any("instead of CUT" in value for value in findings))

    def test_preflight_catches_ocr_only_blur_that_leaves_graphical_logo_visible(self):
        queue = {"items": [
            {
                "category": "visual_logo", "candidate_type": "persistent_overlay",
                "start_seconds": 6, "end_seconds": 100, "decision": "BLUR",
                "suggested_region_source_pixels": {"x": 90, "y": 10, "width": 250, "height": 210},
                "decision_region_source_pixels": {"x": 128, "y": 104, "width": 162, "height": 67},
            },
            {
                "category": "visual_logo", "candidate_type": None,
                "start_seconds": 6, "end_seconds": 10, "decision": "BLUR",
                "suggested_region_source_pixels": {"x": 128, "y": 104, "width": 160, "height": 67},
                "model_evidence": {"region_sources": ["ocr"]},
            },
            {
                "category": "visual_logo", "candidate_type": None,
                "start_seconds": 80, "end_seconds": 85, "decision": "BLUR",
                "suggested_region_source_pixels": {"x": 130, "y": 104, "width": 159, "height": 67},
                "model_evidence": {"region_sources": ["ocr"]},
            },
        ]}
        findings, actions = queue_quality_findings(queue)
        self.assertTrue(any("too small" in value for value in findings))
        self.assertTrue(any("full grounded emblem" in value for value in actions))

    def test_auto_uses_user_codex_home_for_desktop_login(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            codex_home = root / ".codex"
            codex_home.mkdir()
            with patch.dict(
                os.environ,
                {"USERPROFILE": str(root), "CODEX_HOME": ""},
                clear=False,
            ):
                config = load_ai_config(root)
                self.assertEqual(resolve_codex_home(config), codex_home.resolve())
                self.assertEqual(
                    codex_subprocess_environment(config)["CODEX_HOME"],
                    str(codex_home.resolve()),
                )

    def test_auto_discovers_codex_desktop_when_path_is_missing(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "OpenAI" / "Codex" / "bin" / "version-1" / "codex.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"codex")
            with (
                patch("biliflow.codex_supervisor.shutil.which", return_value=None),
                patch.dict(os.environ, {"LOCALAPPDATA": str(root)}),
            ):
                self.assertEqual(
                    resolve_codex_command(root), str(executable.resolve())
                )

    def test_audit_sends_paths_only_and_is_advisory(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            queue_path = root / "reports" / "job" / "review-queue.json"
            queue_path.parent.mkdir(parents=True)
            (queue_path.parent / "scan.json").write_text(json.dumps({
                "status": "COMPLETED", "input_sha256": "abc",
                "duration_seconds": 100, "error": None,
            }), encoding="utf-8")
            queue_path.write_text(json.dumps({
                "source": {"sha256": "abc", "duration_seconds": 100},
                "reports": ["reports/job/scan.json"], "items": [],
                "candidate_coverage": {
                    "complete": True, "missing_refs": [],
                    "reports": [{"complete": True}],
                },
            }), encoding="utf-8")
            result = run_ai_audit(
                root=root, job={"job_key": "job"}, queue_path=queue_path,
                connection_checker=lambda _root: {"ready": True},
                client_factory=FakeClient,
            )
            self.assertEqual(result["result"], "PASS")
            self.assertEqual(result["authority"], "ADVISORY_ONLY")
            self.assertIn("no media", result["privacy"])
            self.assertEqual(result["model"], "gpt-5.6-luna")
            self.assertEqual(result["reasoning_effort"], "medium")

    def test_config_pins_economy_model_and_rejects_expensive_effort(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            default = load_ai_config(root)
            self.assertEqual(default["model"], "gpt-5.6-luna")
            self.assertEqual(default["reasoning_effort"], "medium")
            updated = save_ai_config(root, {"model": "gpt-5.6-sol", "reasoning_effort": "high"})
            self.assertEqual(updated["model"], "gpt-5.6-sol")
            self.assertEqual(updated["reasoning_effort"], "high")
            with self.assertRaises(ValueError):
                save_ai_config(root, {"reasoning_effort": "xhigh"})
            with self.assertRaises(ValueError):
                save_ai_config(root, {"model": "gpt-6-luna"})


    def test_visual_audit_attaches_only_bounded_report_images(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            queue_path = root / "reports" / "job" / "review-queue.json"
            image_path = queue_path.parent / "logo.jpg"
            queue_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"jpeg")
            queue_path.write_text(json.dumps({
                "source": {"sha256": "abc", "duration_seconds": 20},
                "reports": [],
                "candidate_coverage": {
                    "complete": True, "missing_refs": [], "reports": [],
                },
                "items": [{
                    "id": "logo-1", "category": "visual_logo",
                    "candidate_type": "persistent_overlay",
                    "priority": "high", "start_seconds": 1, "end_seconds": 19,
                    "preview_images": ["reports/job/logo.jpg"],
                }],
            }), encoding="utf-8")
            holder = {}
            def factory(value):
                holder["client"] = FakeVisualClient(value)
                return holder["client"]

            result = run_ai_audit(
                root=root, job={"job_key": "job"}, queue_path=queue_path,
                visual_opt_in=True,
                connection_checker=lambda _root: {"ready": True},
                client_factory=factory,
            )
            turn = next(
                params for method, params, _request in holder["client"].calls
                if method == "turn/start"
            )
            images = [value for value in turn["input"] if value["type"] == "localImage"]
            self.assertEqual(len(images), 1)
            self.assertEqual(Path(images[0]["path"]), image_path.resolve())
            self.assertTrue(result["visual_audit"]["enabled_for_this_job"])
            self.assertEqual(result["visual_audit"]["image_count"], 1)
            self.assertEqual(result["visual_assessments"][0]["item_id"], "logo-1")
            self.assertEqual(result["authority"], "ADVISORY_ONLY")

    def test_visual_audit_reuses_shared_supervisor_thread(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            queue_path = root / "reports" / "job" / "review-queue.json"
            image_path = queue_path.parent / "logo.jpg"
            queue_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"jpeg")
            queue_path.write_text(json.dumps({
                "source": {"sha256": "abc", "duration_seconds": 20},
                "reports": [],
                "candidate_coverage": {
                    "complete": True, "missing_refs": [], "reports": [],
                },
                "items": [{
                    "id": "logo-1", "category": "visual_logo",
                    "candidate_type": "persistent_overlay",
                    "priority": "high", "start_seconds": 1,
                    "end_seconds": 19,
                    "preview_images": ["reports/job/logo.jpg"],
                }],
            }), encoding="utf-8")
            holder = {}

            def factory(value):
                holder["client"] = FakeResumeVisualClient(value)
                return holder["client"]

            result = run_ai_audit(
                root=root, job={"job_key": "job"}, queue_path=queue_path,
                thread_id="shared-supervisor", visual_opt_in=True,
                connection_checker=lambda _root: {"ready": True},
                client_factory=factory,
            )
            methods = [method for method, _params, _request in holder["client"].calls]
            self.assertIn("thread/resume", methods)
            self.assertNotIn("thread/start", methods)
            self.assertEqual(result["_thread_id"], "shared-supervisor")
            self.assertEqual(
                result["session_policy"], "shared_ai_supervisor_thread"
            )

    def test_visual_evidence_prioritizes_logo_and_stays_in_reports(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            reports = root / "reports"
            reports.mkdir()
            for name in ("logo.jpg", "gore.jpg"):
                (reports / name).write_bytes(b"jpeg")
            outside = root / "outside.jpg"
            outside.write_bytes(b"jpeg")
            evidence = collect_visual_evidence(
                root,
                {"items": [
                    {
                        "id": "gore", "category": "gore", "priority": "high",
                        "start_seconds": 1, "preview_images": ["reports/gore.jpg"],
                    },
                    {
                        "id": "logo", "category": "visual_logo", "priority": "normal",
                        "start_seconds": 2,
                        "source_frame_size": [1920, 1080],
                        "suggested_region_source_pixels": {
                            "x": 1500, "y": 20, "width": 300, "height": 80,
                        },
                        "preview_images": ["reports/logo.jpg", "outside.jpg"],
                    },
                ]},
                max_images=1, max_images_per_item=3,
            )
            self.assertEqual([value["item_id"] for value in evidence], ["logo"])
            self.assertEqual(evidence[0]["detail"], "high")
            self.assertEqual(evidence[0]["source_frame_size"], [1920, 1080])
            self.assertEqual(evidence[0]["review_kind"], None)

    def test_duplicate_review_ids_are_integrity_blockers(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            blockers = queue_integrity_blockers(root, {
                "source": {"duration_seconds": 10},
                "reports": [],
                "items": [
                    {"id": "same", "start_seconds": 0, "end_seconds": 1},
                    {"id": "same", "start_seconds": 2, "end_seconds": 3},
                ],
            })
            self.assertTrue(any("duplicate item IDs" in value for value in blockers))

    # Nhất Âu Xuân tập 10 (2026-10-02): every candidate the logo scanner
    # reported is in the queue, but its semantic budget (175 windows) left
    # 135 regional leads and 18 full-frame representatives unchecked.
    TRUNCATED_LOGO_REPORT = {
        "report": "reports/x/visual-logo/scan-localized.json",
        "source_candidates": 27, "represented_candidates": 27,
        "reference_complete": True, "detector_complete": False, "complete": False,
        "detector_details": {
            "complete": False,
            "novel_candidate_windows": 520, "novel_candidate_windows_selected": 175,
            "novel_candidate_windows_omitted": 153,
            "regional_candidate_windows": 310, "regional_candidate_windows_selected": 175,
            "regional_candidate_windows_omitted": 135,
            "full_frame_representatives_required": 18,
            "full_frame_representatives_missing": 18,
            "approved_brand_time_groups_missing": 0,
        },
    }

    def write_queue(self, root, coverage):
        queue_path = root / "reports" / "job" / "review-queue.json"
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        queue_path.write_text(json.dumps({
            "source": {"duration_seconds": 100, "sha256": "abc"},
            "reports": [], "items": [], "candidate_coverage": coverage,
        }), encoding="utf-8")
        return queue_path

    def truncated_coverage(self, **changes):
        coverage = {
            "complete": False, "reference_complete": True, "detectors_complete": False,
            "missing_refs": [], "reports": [dict(self.TRUNCATED_LOGO_REPORT)],
        }
        coverage.update(changes)
        return coverage

    def test_detector_budget_truncation_is_warn_not_block(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            queue_path = self.write_queue(root, self.truncated_coverage())
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(queue_integrity_blockers(root, queue), [])
            result = run_local_queue_audit(
                root=root, job={"job_key": "job"}, queue_path=queue_path,
            )
            self.assertEqual(result["result"], "WARN")
            self.assertEqual(result["findings"][0], (
                "Kiểm tra logo bằng AI đã xét 175/520 đoạn ứng viên: 135 đoạn vùng góc "
                "và 18 đoạn toàn khung chưa được AI xem (giới hạn 175)."
            ))
            self.assertIn("không chặn duyệt", result["summary"])
            self.assertTrue(any("--exhaustive" in value for value in result["recommended_actions"]))

    def test_approved_brand_shortfall_has_its_own_ratio(self):
        # Troy run-20260927-205904: every novel window was covered; only the
        # approved-brand time groups were budget limited.
        troy = dict(self.TRUNCATED_LOGO_REPORT, detector_details={
            "complete": False,
            "novel_candidate_windows": 672, "novel_candidate_windows_selected": 193,
            "novel_candidate_windows_omitted": 0,
            "regional_candidate_windows_omitted": 0,
            "full_frame_representatives_missing": 0,
            "approved_brand_time_groups": 510,
            "approved_brand_time_groups_selected": 223,
            "approved_brand_time_groups_missing": 287,
        })
        findings, _ = queue_coverage_findings({"candidate_coverage": self.truncated_coverage(reports=[troy])})
        self.assertEqual(findings, [
            "Kiểm tra logo bằng AI đã xét đủ các đoạn ứng viên mới; logo đã duyệt: "
            "AI đã xem 223/510 nhóm thời gian, 287 nhóm chưa xem (giới hạn đoạn ứng viên mới: 193)."
        ])
        self.assertNotIn("193/672", findings[0])
        # Conan 20: novel and approved shortfalls are reported as separate clauses.
        conan = dict(self.TRUNCATED_LOGO_REPORT, detector_details={
            "complete": False,
            "novel_candidate_windows": 907, "novel_candidate_windows_selected": 412,
            "novel_candidate_windows_omitted": 409,
            "regional_candidate_windows_omitted": 366,
            "full_frame_representatives_missing": 43,
            "approved_brand_time_groups": 23,
            "approved_brand_time_groups_selected": 2,
            "approved_brand_time_groups_missing": 21,
        })
        findings, _ = queue_coverage_findings({"candidate_coverage": self.truncated_coverage(reports=[conan])})
        self.assertEqual(findings, [
            "Kiểm tra logo bằng AI đã xét 412/907 đoạn ứng viên: 366 đoạn vùng góc và "
            "43 đoạn toàn khung chưa được AI xem (giới hạn 412); logo đã duyệt: "
            "AI đã xem 2/23 nhóm thời gian, 21 nhóm chưa xem."
        ])

    def test_missing_refs_still_blocks(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            queue_path = self.write_queue(root, self.truncated_coverage(missing_refs=["r#1"]))
            result = run_local_queue_audit(
                root=root, job={"job_key": "job"}, queue_path=queue_path,
            )
            self.assertEqual(result["result"], "BLOCK")
            self.assertIn("Candidate coverage manifest is incomplete.", result["findings"])

    def test_reference_incomplete_report_still_blocks(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            report = dict(self.TRUNCATED_LOGO_REPORT, reference_complete=False)
            for coverage in (
                self.truncated_coverage(reports=[report]),
                self.truncated_coverage(reference_complete=False),
            ):
                queue_path = self.write_queue(root, coverage)
                result = run_local_queue_audit(
                    root=root, job={"job_key": "job"}, queue_path=queue_path,
                )
                self.assertEqual(result["result"], "BLOCK")

    def test_legacy_coverage_without_reference_flag_still_blocks(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for coverage in (
                {"complete": False, "missing_refs": [], "reports": []},
                {"complete": True, "missing_refs": [], "reports": [{"complete": False}]},
            ):
                queue_path = self.write_queue(root, coverage)
                result = run_local_queue_audit(
                    root=root, job={"job_key": "job"}, queue_path=queue_path,
                )
                self.assertEqual(result["result"], "BLOCK")

    def test_ai_audit_detector_truncation_not_forced_block(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            queue_path = self.write_queue(root, self.truncated_coverage())
            for factory in (FakeClient, FakeBlockClient):
                result = run_ai_audit(
                    root=root, job={"job_key": "job"}, queue_path=queue_path,
                    connection_checker=lambda _root: {"ready": True},
                    client_factory=factory,
                )
                self.assertEqual(result["deterministic_gate"]["status"], "PASS")
                self.assertEqual(result["deterministic_gate"]["blockers"], [])
                self.assertEqual(result["result"], "WARN")
                self.assertTrue(any(
                    "175/520" in value and "135" in value and "18 đoạn toàn khung" in value
                    for value in result["findings"]
                ))


    def test_config_never_allows_media_or_api_authentication(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            with self.assertRaises(ValueError):
                save_ai_config(root, {"send_media": True})
            with self.assertRaises(ValueError):
                save_ai_config(root, {"authentication": "api_key"})


if __name__ == "__main__":
    unittest.main()
