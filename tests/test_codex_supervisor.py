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
