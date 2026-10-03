import hashlib
import hmac
import http.client
import json
import os
import re
import shutil
import subprocess
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from biliflow.control_center import (
    ControlCenter,
    _dashboard_html,
    _handler_class,
    _merge_visual_audit_batches,
)
from biliflow.job_pipeline import PipelineStage
from biliflow.job_store import IN_PROCESS_STATES, JobStore
from biliflow.final_renderer import render_progress_path
from biliflow.review_evidence import ReviewFrameCache
from biliflow.review_workflow import record_review_decision
from biliflow.scheduler import STARTABLE_STATES, JobScheduler


class ControlCenterVisualAuditTests(unittest.TestCase):
    def test_dashboard_preserves_metadata_drafts_across_refreshes(self):
        page = _dashboard_html()
        self.assertIn(
            "const detectorDrafts={};const metadataDrafts={};const ocrDrafts={};"
            "const speedDrafts={};const draftJobKeys={};const rerunPanelDrafts={};",
            page,
        )
        self.assertIn("function metadataSelection(j)", page)
        self.assertIn("function captureMetadataDraft(id)", page)
        self.assertIn('onchange="captureMetadataDraft(${id})"', page)
        self.assertIn("content_style:metadata.content_style", page)
        self.assertIn("delete metadataDrafts[id]", page)

    def test_dashboard_drafts_persist_in_local_storage_until_own_start(self):
        page = _dashboard_html()
        self.assertIn("const DRAFT_PREFIX='biliflow.jobDraft.'", page)
        # Every storage access tolerates a blocked or private-mode localStorage.
        self.assertIn("function storageGet(key){try{const raw=window.localStorage.getItem(key)", page)
        self.assertIn("function storageSet(key,value){try{", page)
        self.assertIn("loadStoredDrafts();try{window.addEventListener('storage'", page)
        self.assertIn("saveDraft(id);const all=document.getElementById(`det-all-${id}`)", page)
        self.assertIn('onchange="ocrDrafts[${j.id}]=Number(this.value);saveDraft(${j.id})"', page)
        self.assertIn('onchange="speedDrafts[${j.id}]=this.checked;saveDraft(${j.id})"', page)
        # Drafts are cleared only after the job's own successful start or rerun.
        start = page[page.index("async function start(id,button)"):]
        start = start[:start.index("\n")]
        self.assertLess(start.index("await post(`/api/jobs/${id}/start`"), start.index("clearDraft(id)"))
        # A refusal clears the draft only when the job has already left setup.
        self.assertIn("if(job&&!needsSetup(job)){clearDraft(id);", start)
        self.assertIn("else notify(`Không thể bắt đầu #${id}: ${e.message}`,true)", start)
        self.assertIn("discardPendingLoads();clearDraft(id);delete rerunPanelDrafts[id]", page)

    def test_dashboard_ignores_stale_status_and_defers_rebuild_during_clicks(self):
        page = _dashboard_html()
        self.assertIn(
            "async function load(){const seq=++loadSeq;const next=await json('/api/status');"
            "if(seq<=appliedSeq)return false;appliedSeq=seq;status=next;render();return true}",
            page,
        )
        self.assertIn("function discardPendingLoads(){appliedSeq=Math.max(appliedSeq,loadSeq)}", page)
        self.assertIn("function renderJobs(force=false){if(!force&&jobsInteracting()){deferJobsRender();return}", page)
        self.assertIn("box.addEventListener('pointerdown'", page)
        # A focused select or text field (the card's custom export size) defers the rebuild.
        self.assertIn("(active.tagName==='SELECT'||(active.tagName==='INPUT'&&active.type!=='checkbox'))", page)
        self.assertIn("(async()=>{watchJobsInteraction();watchHeaderHeight();await refreshToken();", page)
        # Setup cards keep a stable id-ascending order in their own section.
        self.assertIn("['Cần thiết lập',items.filter(needsSetup).sort(byId)]", page)
        self.assertIn("function byId(a,b){return a.id-b.id}", page)

    def test_dashboard_confirms_a_changed_or_sensitive_start_scope(self):
        page = _dashboard_html()
        self.assertIn("const LAST_SCOPE_KEY='biliflow.lastDetectorScope'", page)
        self.assertIn("const SENSITIVE_DETECTORS=['adult','gore','violence']", page)
        self.assertIn("Bắt đầu #${id} ${job?videoName(job):''} với các nhóm: ", page)
        self.assertIn("if(!detectors||!metadata||!confirmStartScope(id,detectors)){release();return}", page)
        self.assertIn("storageSet(LAST_SCOPE_KEY,detectors)", page)
        # The untouched picker still defaults to every detector group.
        self.assertIn("new Set(detectorDrafts[j.id]||j.detector_groups||opts.map(x=>x.id))", page)

    def test_dashboard_start_ignores_a_second_click_while_starting(self):
        page = _dashboard_html()
        self.assertIn("const startingJobs=new Set();", page)
        self.assertIn(
            "async function start(id,button){if(startingJobs.has(id))return;startingJobs.add(id);", page,
        )
        self.assertIn(
            "onclick=\"start(${id},this)\" ${startingJobs.has(id)?'disabled':''}>"
            "${startingJobs.has(id)?'Đang bắt đầu…':'Bắt đầu'}</button>",
            page,
        )
        self.assertIn("if(!j||!needsSetup(j))startingJobs.delete(id)", page)

    def test_dashboard_video_name_splits_windows_and_posix_paths(self):
        page = _dashboard_html()
        backslash = chr(92)
        self.assertIn("split(/[" + backslash * 2 + "/]/)", page)

    def test_dashboard_tab_bar_is_sticky(self):
        page = _dashboard_html()
        style = page[page.index("<style>"):page.index("</style>")]
        tabs = style[style.index(".job-tabs{"):]
        tabs = tabs[:tabs.index("}")]
        self.assertIn("position:sticky;top:var(--header-h,64px);z-index:3;background:rgba(20,25,35,.98)", tabs)
        # overflow:hidden on the workspace would make it the sticky container.
        self.assertIn(".workspace{overflow:clip}", style)
        self.assertNotIn(".workspace{overflow:hidden", style)
        self.assertIn("header{", style)
        self.assertIn("position:sticky;top:0;z-index:4", style)
        self.assertIn(
            "function syncHeaderHeight(){try{const h=document.querySelector('header'),"
            "r=document.documentElement;if(h&&r&&r.style)r.style.setProperty('--header-h',", page,
        )
        self.assertIn("window.addEventListener('resize',syncHeaderHeight)", page)
        self.assertIn("if(h&&window.ResizeObserver)new ResizeObserver(syncHeaderHeight).observe(h)", page)
        self.assertIn(
            "function selectJobTab(tab){activeJobTab=tab;renderJobs(true);keepTabsInView();revealActiveTab()}", page,
        )
        # The tab bar keeps its accessible name.
        self.assertIn('<nav id="job-tabs" class="job-tabs" aria-label="Trạng thái video">', page)

    def run_dashboard_harness(self, *arguments):
        page = _dashboard_html()
        script = page[page.index("<script>") + len("<script>"):page.rindex("</script>")]
        harness = Path(__file__).with_name("fixtures") / "dashboard_harness.js"
        with TemporaryDirectory() as directory:
            script_path = Path(directory) / "page.js"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [shutil.which("node"), str(harness), str(script_path), *arguments],
                capture_output=True, text=True, encoding="utf-8", timeout=60,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout.strip().splitlines()[-1])

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_dashboard_queue_positions_and_header_height_in_node(self):
        out = self.run_dashboard_harness("queue")
        # The header height feeds the sticky tab offset (the main scenario boots
        # without documentElement and must not throw).
        self.assertEqual(out["header_h"], "72px")
        self.assertEqual(out["resize_listener"], 1)
        # The first non-empty stage tab is shown; each card sits in its stage tab.
        self.assertEqual(out["default_tab"], "export")
        self.assertEqual(
            [(card["id"], card["tab"]) for card in out["cards"]],
            [(64, "waiting"), (62, "scan_queue"), (61, "scan_queue"), (63, "review"), (60, "export")],
        )
        self.assertEqual(out["export_headings"], [["Chờ xuất", 1]])
        cards = {card["id"]: card for card in out["cards"]}
        self.assertEqual(
            [(cards[i]["badge"], cards[i]["queue"]) for i in (60, 62, 61)],
            [("Chờ xuất video", "Thứ tự chờ: #1"), ("Chờ chạy cảnh", "Thứ tự chờ: #2"),
             ("Chờ chạy cảnh", "Thứ tự chờ: #3")],
        )
        self.assertEqual(cards[60]["exportValue"], "Chờ xuất video")
        self.assertEqual(
            cards[60]["exportDetail"],
            "Thứ tự chờ: #1 trong 3 việc; xuất video và quét cảnh chạy lần lượt theo thứ tự bấm.",
        )
        self.assertEqual(
            cards[62]["scanDetail"],
            "Thứ tự chờ: #2 trong 3 việc; quét cảnh và xuất video chạy lần lượt theo thứ tự bấm.",
        )
        # A paused job and a reviewed job are not in the queue.
        self.assertIsNone(cards[64]["queue"])
        self.assertIsNone(cards[63]["queue"])
        self.assertEqual(out["worker"], "Đang chờ · 3 việc trong hàng đợi")
        self.assertIn("Không có video trong mục này.", out["scanning_tab"])
        # Pausing the scheduler keeps every position and says so.
        paused = {card["id"]: card for card in out["paused"]}
        self.assertEqual(paused[61]["queue"], "Thứ tự chờ: #3")
        self.assertEqual(
            paused[62]["scanDetail"],
            "Hàng đợi đang tạm dừng — bấm “Chạy hàng đợi” để tiếp tục (vẫn giữ thứ tự).",
        )
        self.assertEqual(out["paused_worker"], "Scheduler tạm dừng · 3 việc giữ nguyên thứ tự")

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_dashboard_behaviour_in_node_stale_poll_drafts_and_confirm(self):
        out = self.run_dashboard_harness()
        # Setup cards wait in "Đang chờ xử lý" by id; queued scans have their own tab
        # in click order (#44 before #45), and it is the first non-empty tab.
        self.assertEqual(out["initial_tab"], "scan_queue")
        self.assertEqual(out["initial_order"], [46, 47, 48])
        self.assertEqual(out["initial_scan_queue"], [44, 45])
        self.assertEqual(out["posts"], [{"id": 46, "detectors": ["advertising"]}])
        self.assertEqual(out["confirms_for_46"], [])
        # The slow poll issued before Start(46) is discarded: no ghost setup card.
        self.assertFalse(out["stale_applied"])
        for snapshot in (out["after_start"], out["after_stale_poll"]):
            card = next(card for card in snapshot if card["id"] == 46)
            self.assertEqual(card["badge"], "Chờ chạy cảnh")
            self.assertEqual(card["queue"], "Thứ tự chờ: #3")
            self.assertEqual(card["tab"], "scan_queue")
            self.assertFalse(card["hasStart"])
        # The user's tab does not jump after Start.
        self.assertEqual(out["tab_after_start"], "waiting")
        self.assertEqual(
            [card["id"] for card in out["after_stale_poll"] if card["hasStart"]], [47, 48],
        )
        # Drafts survive a re-render and a reload; #46's draft went with its own start.
        for key in ("rerender_47", "reload_47"):
            self.assertEqual(out[key]["detectors"], ["advertising", "adult"])
            self.assertEqual(out[key]["ocr"], 8)
        self.assertEqual(out["storage_keys"], ["biliflow.jobDraft.47", "biliflow.lastDetectorScope"])
        self.assertEqual(out["last_scope"], ["advertising"])
        self.assertFalse(out["reload_46"]["hasStart"])
        # A scope different from the previous start asks first; cancel posts nothing.
        self.assertEqual(
            out["confirm_48"],
            ["Bắt đầu #48 Tập 48.mp4 với các nhóm: Quảng cáo / logo, 18+, Máu me, Bạo lực?"],
        )
        self.assertEqual(out["posts_after_cancel"], 0)
        self.assertTrue(out["draft_48_kept_after_cancel"])
        # The refusal for a job that already left setup is neutral and drops its ghost draft.
        self.assertEqual(
            out["refused_notice"],
            "Video #48 không còn chờ thiết lập (Chờ chạy cảnh); không cần bắt đầu lại.",
        )
        self.assertFalse(out["refused_notice_is_error"])
        self.assertFalse(out["draft_48_kept_after_refusal"])
        # #48 has a Windows source path; the card title shows only the file name.
        self.assertEqual(out["title_48"], "#48 · Tập 48.mp4")
        # A refusal while the job still waits for setup is an error and keeps the draft.
        self.assertEqual(out["error_notice_47"], "Không thể bắt đầu #47: Lỗi thử nghiệm")
        self.assertTrue(out["error_notice_47_is_error"])
        self.assertTrue(out["draft_47_kept_after_error"])
        self.assertTrue(out["button_47_restored"])
        # A double click posts once, confirms once and shows no error.
        self.assertTrue(out["button_47_busy"])
        self.assertTrue(out["card_47_while_pending"]["startDisabled"])
        self.assertEqual(out["double_posts"], 1)
        self.assertEqual(out["double_confirms"], 1)
        self.assertFalse(out["double_notice_is_error"])
        self.assertEqual(out["starting_after_double"], 0)
        self.assertEqual(out["card_47_after_double"]["badge"], "Chờ chạy cảnh")
        self.assertEqual(out["card_47_after_double"]["queue"], "Thứ tự chờ: #4")
        self.assertTrue(out["render_deferred_while_pressed"])
        self.assertTrue(out["rendered_after_release"])

    def test_structure_status_shows_first_finding(self):
        page = _dashboard_html()
        self.assertIn("detail:(result!=='PASS'&&a.first_finding)||a.summary||", page)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reports" / "rev-1").mkdir(parents=True)
            store = JobStore(root / "state" / "control-center.sqlite3")
            try:
                job = store.upsert_job(
                    job_key="movie", source_path=root / "movie.mp4",
                    source_sha256="abc", source_size_bytes=1,
                    source_mtime_ns=1, state="WAITING_REVIEW",
                )
                job_id = int(job["id"])
                revision = store.add_revision(
                    job_id, "reports/rev-1/review-queue.json", "REVIEW_REQUIRED", None,
                )
                store.activate_revision(job_id, revision)
                (root / "reports" / "rev-1" / "structure-audit.json").write_text(json.dumps({
                    "result": "WARN", "summary": "generic", "created_at": "now",
                    "findings": ["Kiểm tra logo bằng AI đã xét 175/520 đoạn ứng viên", "second"],
                }), encoding="utf-8")
                store.add_artifact(
                    job_id, stage_name="structure_audit", kind="structure_audit",
                    path="reports/rev-1/structure-audit.json",
                )
                center = ControlCenter.__new__(ControlCenter)
                center.root = root
                center.store = store
                summary = center.structure_audit_summary(job_id)
                self.assertEqual(summary["summary"], "generic")
                self.assertEqual(
                    summary["first_finding"], "Kiểm tra logo bằng AI đã xét 175/520 đoạn ứng viên",
                )
                self.assertFalse(summary["outdated_rule"])
                # A BLOCK stored by the old rule for a detector-only shortfall is
                # flagged as outdated (the artifact itself is not rewritten).
                audit_path = root / "reports" / "rev-1" / "structure-audit.json"
                detector_only = {
                    "complete": False, "reference_complete": True, "missing_refs": [],
                    "reports": [{"reference_complete": True, "detector_complete": False}],
                }
                stored = {
                    "result": "BLOCK", "summary": "old", "created_at": "now",
                    "findings": ["Candidate coverage manifest is incomplete."],
                    "candidate_coverage": detector_only,
                }
                audit_path.write_text(json.dumps(stored), encoding="utf-8")
                before = audit_path.read_bytes()
                summary = center.structure_audit_summary(job_id)
                self.assertEqual(summary["result"], "BLOCK")
                self.assertTrue(summary["outdated_rule"])
                self.assertIn("quy tắc cũ", summary["first_finding"])
                self.assertEqual(audit_path.read_bytes(), before)
                # A real reference gap keeps the stored BLOCK text unchanged.
                for coverage in (
                    dict(detector_only, missing_refs=["r#1"]),
                    {"complete": False, "missing_refs": [], "reports": []},
                ):
                    audit_path.write_text(json.dumps(dict(stored, candidate_coverage=coverage)), encoding="utf-8")
                    summary = center.structure_audit_summary(job_id)
                    self.assertFalse(summary["outdated_rule"])
                    self.assertEqual(summary["first_finding"], "Candidate coverage manifest is incomplete.")
            finally:
                store.close()
        self.assertIn("a.outdated_rule?`${result} (quy tắc cũ)`:result", page)

    def test_dashboard_preserves_open_rerun_panel_across_refreshes(self):
        page = _dashboard_html()
        self.assertIn("function captureRerunPanelDrafts()", page)
        self.assertIn("captureRerunPanelDrafts();captureExportPanelDrafts();const jobs=", page)
        self.assertIn('data-job-id="${id}" ${rerunPanelDrafts[id]?\'open\':\'\'}', page)
        self.assertIn("delete rerunPanelDrafts[id]", page)

    def test_dashboard_groups_jobs_and_shows_export_lifecycle(self):
        page = _dashboard_html()
        self.assertIn("function jobTab(j)", page)
        self.assertNotIn("function jobBucket(j)", page)
        self.assertNotIn("function runPhase(j)", page)
        self.assertIn(
            "const JOB_TABS=[['waiting','Đang chờ xử lý'],['scan_queue','Đang chờ chạy cảnh để duyệt'],"
            "['scanning','Đang chạy cảnh'],['review','Đang chờ duyệt'],['export','Đang chạy xuất video'],"
            "['completed','Hoàn tất']];", page,
        )
        self.assertIn("const DEFAULT_TAB_ORDER=['scanning','export','scan_queue','review','waiting','completed'];", page)
        for heading in ("Cần duyệt cảnh", "Đã duyệt xong — chờ xuất hoặc bỏ qua", "Đang xuất", "Chờ xuất",
                        "Đã xuất video", "Đã bỏ qua (không xuất)", "Cần thiết lập",
                        "Tạm dừng / lỗi / có thể tiếp tục", "Đã hủy"):
            with self.subTest(heading=heading):
                self.assertIn(f"'{heading}'", page)
        self.assertIn("function exportStatus(j)", page)
        self.assertIn("Đang xuất video", page)
        self.assertIn("Chờ xuất video", page)
        self.assertIn('class="rerun-panel"', page)
        self.assertIn('class="export-panel"', page)
        self.assertIn("function shortDuration(seconds)", page)
        self.assertIn("function completedAt(value)", page)
        self.assertIn("100% · Đã xuất video", page)
        self.assertIn("Hoàn thành lúc", page)
        self.assertIn('class="mini-progress"', page)
        self.assertIn(".job[data-bucket=\"scanning\"],.job[data-bucket=\"export\"]{border-color:#2e5872}", page)
        # Skip and its undo, with the same texts as the server.
        self.assertIn("Bỏ qua (không xuất)", page)
        self.assertIn("Mở lại để xuất", page)
        self.assertIn("SKIPPED:['Đã bỏ qua — không xuất','complete']", page)
        self.assertIn("Video gốc không còn trong input; không thể xuất.", page)
        self.assertNotIn("__EXPORT", page)
        self.assertNotIn("__SOURCE", page)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_every_job_state_lands_in_exactly_one_tab_in_node(self):
        out = self.run_dashboard_harness("tabs")
        harness = (Path(__file__).with_name("fixtures") / "dashboard_harness.js").read_text(encoding="utf-8")
        fixture_states = set(re.findall(r"\['([A-Z_]+)', '[a-z_]+'", harness))
        # Every state the code can write is in the fixture (plus an unknown one).
        source = Path(__file__).resolve().parents[1] / "src" / "biliflow"
        written = set(IN_PROCESS_STATES) | set(STARTABLE_STATES)
        for name in ("scheduler.py", "control_center.py", "job_store.py", "job_import.py", "job_pipeline.py"):
            text = (source / name).read_text(encoding="utf-8")
            written |= set(re.findall(r"state\s*=\s*['\"]([A-Z][A-Z_]+)['\"]", text))
            written |= set(re.findall(r"state='([A-Z][A-Z_]+)'", text))
            written |= set(re.findall(r'PipelineStage\(\s*"[a-z_]+",\s*"([A-Z_]+)"', text))
        written |= {"SKIPPED", "COMPLETED", "CANCELLED", "PAUSED", "FAILED", "QUEUED",
                    "WAITING_REVIEW", "READY_TO_EXPORT", "INTERRUPTED_RECOVERABLE"}
        # Stage rows have their own states; these never reach jobs.state.
        written -= {"PENDING", "RUNNING", "FAILED_RETRYABLE"}
        self.assertLessEqual(written, fixture_states, written - fixture_states)
        self.assertIn("WEIRD", fixture_states)
        ids = [card[0] for card in out["cards"]]
        # Each card appears in exactly one tab, the expected one, with that bucket colour.
        self.assertEqual(sorted(ids), sorted(int(key) for key in out["expected"]))
        self.assertEqual(len(ids), len(set(ids)))
        for card_id, tab, bucket in out["cards"]:
            with self.subTest(card=card_id):
                self.assertEqual(tab, out["expected"][str(card_id)])
                self.assertEqual(bucket, tab)
        # Labels and order are exact; the counts sum to the job total.
        bar = out["bar"]["tabs"]
        self.assertEqual([tab[1] for tab in bar], [
            "Đang chờ xử lý", "Đang chờ chạy cảnh để duyệt", "Đang chạy cảnh", "Đang chờ duyệt",
            "Đang chạy xuất video", "Hoàn tất",
        ])
        self.assertEqual(sum(tab[2] for tab in bar), len(ids))
        for key, _label, count in bar:
            self.assertEqual(count, sum(1 for card in out["cards"] if card[1] == key), key)
        self.assertEqual(out["default_tab"], "scanning")
        self.assertEqual(out["scan_order"], [130, 102])
        self.assertEqual(out["headings"]["review"], [
            ["Cần duyệt cảnh", 1], ["Đã duyệt xong — chờ xuất hoặc bỏ qua", 1],
        ])
        self.assertEqual(out["headings"]["export"], [["Đang xuất", 2], ["Chờ xuất", 1]])
        self.assertEqual(out["headings"]["completed"], [["Đã xuất video", 1], ["Đã bỏ qua (không xuất)", 1]])
        self.assertEqual(out["headings"]["waiting"], [
            ["Cần thiết lập", 2], ["Tạm dừng / lỗi / có thể tiếp tục", 7], ["Đã hủy", 1],
        ])
        # A poll never moves the user away from the tab they are reading.
        self.assertEqual(out["tab_after_poll"], "completed")

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_dashboard_skip_and_unskip_in_node(self):
        out = self.run_dashboard_harness("skip")
        before = {card["id"]: card for card in out["before"]}
        # Offered for 0 main items (#60) and all-KEEP (#44), not with a BLUR (#41).
        self.assertEqual({i: before[i]["hasSkip"] for i in (60, 44, 41)}, {60: True, 44: True, 41: False})
        skipped = before[70]
        self.assertEqual((skipped["tab"], skipped["badge"], skipped["exportValue"]),
                         ("completed", "Đã bỏ qua — không xuất", "Không xuất video"))
        self.assertTrue(skipped["hasUnskip"])
        self.assertFalse(skipped["hasCancel"])
        self.assertTrue(skipped["hasRerun"])
        self.assertFalse(skipped["hasExport"])
        self.assertEqual(out["cancel_actions"], 0)
        self.assertEqual(
            out["confirm_text"],
            "Đánh dấu #60 Tập 30.mp4 là xong mà không xuất video?\nVideo không có cảnh chính nào cần duyệt. "
            "Không tạo bản xuất; video gốc, report và quyết định duyệt được giữ nguyên. Video sẽ chuyển sang "
            "mục “Hoàn tất”; có thể bấm “Mở lại để xuất” sau.",
        )
        self.assertEqual(out["skip_actions"], [{"id": 60, "name": "skip", "body": {}}])
        self.assertEqual(out["skip_confirms"], 1)
        self.assertEqual(out["skip_notice"],
                         "Đã đánh dấu #60 xong (không xuất video). Video đã chuyển sang mục “Hoàn tất”.")
        self.assertEqual(out["tab_after_skip"], "review")
        self.assertEqual((out["after_skip"]["tab"], out["after_skip"]["badge"]),
                         ("completed", "Đã bỏ qua — không xuất"))
        self.assertEqual(out["completed_after_skip"], [["Đã bỏ qua (không xuất)", 2]])
        self.assertEqual(out["unskip_actions"], [{"id": 70, "name": "unskip", "body": {}}])
        self.assertEqual(
            out["unskip_confirm"],
            "Mở lại #70 Tập 40.mp4 để xuất video?\nVideo sẽ quay về mục “Đang chờ duyệt” "
            "(Đã duyệt xong — chờ xuất hoặc bỏ qua).",
        )
        self.assertEqual((out["after_unskip"]["tab"], out["after_unskip"]["badge"]), ("review", "Sẵn sàng xuất"))
        self.assertTrue(out["after_unskip"]["hasExport"])
        self.assertEqual(out["refused_notice"], "Không thể bỏ qua #44: Video có cảnh chính không phải Giữ nguyên")
        self.assertTrue(out["refused_is_error"])
        self.assertEqual(out["refused_tab"], "review")

    def test_card_action_results_show_where_the_user_is(self):
        page = _dashboard_html()
        # The notice is a toast fixed to the viewport, not a line at the top of the page.
        self.assertIn(".notice{display:none;position:fixed;left:16px;right:16px;bottom:16px;z-index:30;", page)
        self.assertIn('id="notice" class="notice" role="status" title="Bấm để ẩn"', page)
        # The card's export error is also written into its own panel.
        self.assertIn('<span class="export-error" role="alert">${esc(draft.error)}</span>', page)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_dashboard_card_export_in_node(self):
        out = self.run_dashboard_harness("export")
        before = {card["id"]: card for card in out["before"]}
        self.assertEqual(out["review_headings"], [["Đã duyệt xong — chờ xuất hoặc bỏ qua", 4]])
        self.assertEqual((before[60]["hasExport"], before[60]["exportDisabled"]), (True, False))
        # Disabled with the reason: an unfinished review, a missing source.
        self.assertEqual((before[61]["exportDisabled"], before[61]["exportReason"]),
                         (True, "Vẫn còn mục chưa có quyết định cuối cùng."))
        self.assertEqual((before[1]["exportDisabled"], before[1]["exportReason"]),
                         (True, "Video gốc không còn trong input; không thể xuất."))
        self.assertEqual(before[1]["exportValue"], "Thiếu video gốc")
        # A queued or running export has no export panel.
        for job_id in (63, 64):
            self.assertFalse(before[job_id]["hasExport"])
            self.assertEqual(before[job_id]["tab"], "export")
        self.assertEqual(out["preset_62"], ["custom", "2"])
        self.assertEqual(out["kept_after_load"], {"open": True, "mode": "custom", "gb": "2.5"})
        self.assertEqual(out["cancel"], {
            "actions": 0, "open": True,
            "confirm": "Khóa các lựa chọn hiện tại và bắt đầu xuất video hoàn chỉnh (tối đa 2,5 GB)?",
        })
        self.assertEqual(out["invalid"], {
            "actions": 0, "confirms": 0, "notice": "Giới hạn tùy chỉnh phải từ 0,05 đến 1.000 GB.", "error": True,
        })
        refused = out["refused"]
        self.assertEqual((refused["actions"], refused["open"], refused["error"], refused["openAtPost"]),
                         (1, True, True, [False]))
        self.assertEqual(refused["notice"], "Không gửi được lệnh xuất: Video gốc không còn trong input; không thể xuất.")
        self.assertEqual(refused["panelError"], refused["notice"])
        self.assertTrue(refused["openAfterRender"])
        ok = out["ok"]
        self.assertEqual(ok["actions"], [{"id": 60, "name": "review/finalize", "body": {
            "size_mode": "custom", "max_output_gb": 2.5, "description": "tối đa 2,5 GB"}}])
        self.assertEqual(ok["confirms"], ["Khóa các lựa chọn hiện tại và bắt đầu xuất video hoàn chỉnh (tối đa 2,5 GB)?"])
        self.assertEqual(ok["openAtPost"], [False])
        self.assertEqual(ok["notice"],
                         "Đã xếp #60 vào hàng đợi xuất video (lượt 2/2). Theo dõi ở mục “Đang chạy xuất video”.")
        self.assertEqual((ok["tab"], ok["card"]["badge"], ok["card"]["hasExport"]), ("export", "Chờ xuất video", False))
        self.assertEqual(ok["noticeClass"], "notice show")
        self.assertEqual(out["gate"], {"actions": 0, "alerts": ["Vẫn còn mục chưa có quyết định cuối cùng."]})

    def test_status_exposes_the_workers_queue_order(self):
        from biliflow.scheduler import JobScheduler
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "input").mkdir()
            store = JobStore(root / "state" / "control-center.sqlite3")
            center = ControlCenter.__new__(ControlCenter)
            center.root = root
            center.store = store
            center.recovered = 0
            center._audit_lock = threading.Lock()
            center._audit_jobs = {}
            center.scheduler = JobScheduler(root, store)
            ids = {}
            for name, sha in (("a", "1"), ("b", "2"), ("c", "3"), ("d", "4")):
                source = root / "input" / f"{name}.mp4"
                source.write_bytes(b"video")
                ids[name] = int(store.upsert_job(
                    job_key=name, source_path=source, source_sha256=sha * 64,
                    source_size_bytes=5, source_mtime_ns=source.stat().st_mtime_ns,
                    content_style="animation", state="NEEDS_METADATA",
                )["id"])
            try:
                definitions = [PipelineStage("preflight", "PREFLIGHT", tuple())]
                with (
                    patch("biliflow.scheduler.pipeline_stages", return_value=definitions),
                    patch("biliflow.control_center._resources", return_value={}),
                    patch("biliflow.control_center.storage_status"),
                ):
                    for name in ("c", "a"):
                        center.scheduler.start_job(
                            ids[name], content_style="animation", profile="careful",
                            detector_groups=["advertising"],
                        )
                    store.update_job(ids["b"], state="READY_TO_EXPORT", progress=1.0)
                    center.scheduler.queue_render(
                        ids["b"], plan_path=root / "work" / "plan.json",
                        output_path=root / "output" / "b.mp4",
                    )
                    # Touching an earlier job does not move it behind later clicks.
                    store.update_job(ids["c"], progress=0.1)
                    value = center.status()
                    selected = center.scheduler._select()
                jobs = {job["id"]: job for job in value["jobs"]}
                self.assertEqual(
                    [(jobs[ids[n]]["queue_position"], jobs[ids[n]]["queue_kind"]) for n in "cab"],
                    [(1, "scan"), (2, "scan"), (3, "export")],
                )
                self.assertIsNone(jobs[ids["d"]]["queue_position"])
                self.assertEqual(jobs[ids["b"]]["current_stage"], "render")
                self.assertEqual(value["queue"], {"length": 3, "paused": False})
                self.assertEqual(selected[0]["id"], ids["c"])
            finally:
                store.close()

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
            (root / "movie.mp4").write_bytes(b"x")
            center = ControlCenter.__new__(ControlCenter)
            center.root = root
            center.store = store
            center.scheduler = Mock(job_action_lock=threading.RLock())
            center.scheduler.is_busy.return_value = False
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


MEDIA_SHA = "f43cf94aadffb8c127c18fb23a51c58de2bdafcb2f05b1e91bd84be726fb19e9"
MEDIA_ITEM = "review-935e63a78271"
MEDIA_THUMBNAIL = "thumbnails/frame-00001889-944.500s.jpg"


class ControlCenterHttpTests(unittest.TestCase):
    """Real HTTP handler on port 0 with a stub center (no scheduler, watcher or jobs)."""

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.store = JobStore(self.root / "state" / "control-center.sqlite3")
        self.job_id, self.source = self.add_job("troy", "movie.mp4", MEDIA_SHA)
        center = ControlCenter.__new__(ControlCenter)
        center.root = self.root
        center.host = "127.0.0.1"
        center.token = "test-token"
        center.store = self.store
        center.frame_cache = ReviewFrameCache(self.root, self.root / "missing-ffmpeg.exe")
        center._stopping = threading.Event()
        # Never started: review writes only take its job_action_lock and read its state.
        center.scheduler = JobScheduler(self.root, self.store)
        self.center = center
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_class(center))
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.store.close()
        self.temp.cleanup()

    def add_job(self, name, source_name, sha):
        job_dir = f"reports/jobs/{name}"
        report = f"{job_dir}/adult/scan.json"
        source = self.root / "input" / source_name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(bytes(range(256)) * 16)
        (self.root / job_dir / "adult" / "thumbnails").mkdir(parents=True)
        (self.root / job_dir / "adult" / MEDIA_THUMBNAIL).write_bytes(b"\xff\xd8thumb\xff\xd9")
        (self.root / report).write_text(json.dumps({
            "scan_type": "nsfw", "sample_fps": 2.0, "threshold": 0.95,
            "intervals": [{
                "start_seconds": 928.5, "end_seconds": 950.5, "max_score": 0.999486,
                "strongest_frame": MEDIA_THUMBNAIL, "sample_count": 16,
                "sequence_context": {
                    "applied": True, "detector_start_seconds": 937.0,
                    "detector_end_seconds": 950.5, "supporting_sample_count": 2,
                    "context_threshold": 0.7, "maximum_extension_seconds": 8.0,
                },
            }],
        }), encoding="utf-8")
        (self.root / job_dir / "review-queue.json").write_text(json.dumps({
            "status": "REVIEW_REQUIRED",
            "source": {"path": str(source.resolve()), "sha256": sha, "duration_seconds": 11762.72},
            "reports": [report],
            "items": [{
                "id": MEDIA_ITEM, "category": "adult",
                "start_seconds": 928.5, "end_seconds": 950.5,
                "preview_images": [f"{job_dir}/adult/{MEDIA_THUMBNAIL}"],
                "source_candidate_refs": [f"{report}#interval:0"],
                "detected_intervals": [{"start_seconds": 928.5, "end_seconds": 950.5}],
            }],
            "advisory_items": [],
        }), encoding="utf-8")
        stat = source.stat()
        job = self.store.upsert_job(
            job_key=name, source_path=source, source_sha256=sha,
            source_size_bytes=stat.st_size, source_mtime_ns=stat.st_mtime_ns,
            state="WAITING_REVIEW",
        )
        self.store.update_job(
            job["id"], active_queue_path=f"{job_dir}/review-queue.json", active_revision=1,
        )
        return int(job["id"]), source

    def key(self, job_id):
        return hmac.new(
            b"test-token", f"review-media:{job_id}".encode(), hashlib.sha256,
        ).hexdigest()

    def request(self, path, *, host=None, method="GET", headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            sent = {"Host": host or f"127.0.0.1:{self.port}", **(headers or {})}
            connection.request(method, path, body=body, headers=sent)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_foreign_host_header_is_refused_on_every_route(self):
        routes = [
            "/", "/healthz", "/api/session", f"/review/{self.job_id}",
            f"/media/reports/jobs/troy/adult/{MEDIA_THUMBNAIL}",
            f"/api/jobs/{self.job_id}/review/session",
            f"/api/jobs/{self.job_id}/review/evidence?item={MEDIA_ITEM}",
            f"/api/jobs/{self.job_id}/review/video?k={self.key(self.job_id)}",
        ]
        for route in routes:
            for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1.evil.example",
                         f"localhost:{self.port}x", "", "[::2]"):
                with self.subTest(route=route, host=host):
                    status, _, body = self.request(route, host=host or " ")
                    self.assertEqual(status, 403)
                    self.assertNotIn(b"test-token", body)
                    self.assertNotIn(b"thumb", body)
            for host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}", "localhost",
                         f"[::1]:{self.port}", "LOCALHOST"):
                with self.subTest(route=route, host=host):
                    self.assertIn(self.request(route, host=host)[0], {200})
        status, _, body = self.request(
            "/api/scheduler", host="evil.example", method="POST",
            headers={"X-BiliFlow-Token": "test-token", "Content-Type": "application/json"},
            body=b'{"paused": true}',
        )
        self.assertEqual(status, 403)
        self.assertIsNone(self.store.setting("scheduler_paused"))
        status, _, _ = self.request(
            "/api/scheduler", method="POST", headers={"X-BiliFlow-Token": "wrong"}, body=b"{}",
        )
        self.assertEqual(status, 403)
        for action in ("skip", "unskip", "review/finalize"):
            route = f"/api/jobs/{self.job_id}/{action}"
            with self.subTest(route=route):
                for host, token in (("evil.example", "test-token"), (None, "wrong"), (None, None)):
                    headers = {"Content-Type": "application/json"}
                    if token:
                        headers["X-BiliFlow-Token"] = token
                    status, _, _ = self.request(route, host=host, method="POST", headers=headers, body=b"{}")
                    self.assertEqual(status, 403)
                self.assertEqual(self.store.get_job(self.job_id)["state"], "WAITING_REVIEW")

    def test_review_page_and_media_still_work_on_localhost(self):
        status, headers, body = self.request(f"/review/{self.job_id}")
        self.assertEqual(status, 200)
        self.assertIn(b"test-token", body)
        status, headers, body = self.request(f"/media/reports/jobs/troy/adult/{MEDIA_THUMBNAIL}")
        self.assertEqual((status, body), (200, b"\xff\xd8thumb\xff\xd9"))

    def test_review_page_api_constant_is_scoped_to_the_job(self):
        status, _, body = self.request(f"/review/{self.job_id}")
        page = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn(f"const API='/api/jobs/{self.job_id}/review/';", page)
        self.assertEqual(page.count("'/api/"), 1)
        # Every route the page builds from API exists for this job.
        base = f"/api/jobs/{self.job_id}/review"
        for route in ("queue", "session", "resources", "export", f"evidence?item={MEDIA_ITEM}"):
            with self.subTest(route=route):
                self.assertEqual(self.request(f"{base}/{route}")[0], 200)

    def test_export_route_reports_errors_and_render_progress(self):
        route = f"/api/jobs/{self.job_id}/review/export"
        self.assertEqual(
            json.loads(self.request(route)[2]),
            {"status": "WAITING_REVIEW", "output": None, "error": None, "render_progress": None},
        )
        self.store.update_job(self.job_id, state="FAILED", error="boom", current_stage="render")
        value = json.loads(self.request(route)[2])
        self.assertEqual((value["status"], value["error"]), ("FAILED", "boom"))
        self.store.update_job(self.job_id, state="RENDERING", error=None)
        value = json.loads(self.request(route)[2])
        self.assertEqual(value["status"], "RENDERING")
        self.assertIsNone(value["error"])
        self.assertEqual(value["render_progress"], {"state": "STARTING", "percent": 0.0})

    def test_session_contains_a_per_job_media_key(self):
        status, _, body = self.request(f"/api/jobs/{self.job_id}/review/session")
        value = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(value["token"], "test-token")
        self.assertEqual(value["media_key"], self.key(self.job_id))
        other = json.loads(self.request("/api/jobs/77/review/session")[2])
        self.assertNotEqual(other["media_key"], value["media_key"])

    def test_evidence_route_is_read_only_json(self):
        queue_path = self.root / "reports/jobs/troy/review-queue.json"
        before = queue_path.read_bytes()
        status, headers, body = self.request(
            f"/api/jobs/{self.job_id}/review/evidence?item={MEDIA_ITEM}",
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        value = json.loads(body)
        self.assertIn({"t": 944.5, "kind": "strongest", "score": 0.999486}, value["frames"])
        self.assertTrue(any(937.5 <= frame["t"] <= 941.5 for frame in value["frames"]))
        self.assertEqual(value["video"], {"available": True, "mime": "video/mp4", "reason": None})
        self.assertEqual(queue_path.read_bytes(), before)
        self.assertEqual(self.request(f"/api/jobs/{self.job_id}/review/evidence")[0], 400)
        self.assertEqual(
            self.request(f"/api/jobs/{self.job_id}/review/evidence?item=nope")[0], 404,
        )
        self.assertEqual(self.request(f"/api/jobs/999/review/evidence?item={MEDIA_ITEM}")[0], 404)

    def test_video_streams_ranges_from_the_job_source(self):
        data = self.source.read_bytes()
        route = f"/api/jobs/{self.job_id}/review/video?k={self.key(self.job_id)}"
        status, headers, body = self.request(route, headers={"Range": "bytes=10-19"})
        self.assertEqual(status, 206)
        self.assertEqual(body, data[10:20])
        self.assertEqual(headers["Content-Range"], f"bytes 10-19/{len(data)}")
        self.assertEqual(headers["Accept-Ranges"], "bytes")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Content-Type"], "video/mp4")
        status, _, body = self.request(route)
        self.assertEqual((status, body), (200, data))
        status, headers, _ = self.request(route, headers={"Range": f"bytes={len(data)}-"})
        self.assertEqual(status, 416)
        self.assertEqual(headers["Content-Range"], f"bytes */{len(data)}")
        self.assertEqual(self.source.read_bytes(), data)

    def test_media_routes_need_the_jobs_key(self):
        base = f"/api/jobs/{self.job_id}/review"
        for route in (
            f"{base}/video", f"{base}/video?k=", f"{base}/video?k=wrong",
            f"{base}/video?k={self.key(self.job_id + 1)}",
            f"{base}/frame?item={MEDIA_ITEM}&t=939.5",
            f"{base}/frame?item={MEDIA_ITEM}&t=939.5&k=wrong",
            f"{base}/video?k=%C3%A9",
        ):
            with self.subTest(route=route):
                status, _, body = self.request(route)
                self.assertEqual(status, 403)
                self.assertNotIn(b"\x00\x01\x02", body)

    def test_unknown_job_is_404(self):
        key = self.key(999)
        self.assertEqual(self.request(f"/api/jobs/999/review/video?k={key}")[0], 404)
        self.assertEqual(
            self.request(f"/api/jobs/999/review/frame?item={MEDIA_ITEM}&t=939.5&k={key}")[0], 404,
        )

    def test_changed_source_is_409(self):
        route = f"/api/jobs/{self.job_id}/review/video?k={self.key(self.job_id)}"
        stat = self.source.stat()
        os.utime(self.source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
        self.assertEqual(self.request(route)[0], 409)
        evidence = json.loads(self.request(
            f"/api/jobs/{self.job_id}/review/evidence?item={MEDIA_ITEM}",
        )[2])
        self.assertEqual(evidence["video"]["reason"], "source_changed")
        self.assertFalse(evidence["video"]["available"])
        os.utime(self.source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(self.request(route)[0], 200)
        with self.source.open("ab") as handle:
            handle.write(b"!")
        os.utime(self.source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(self.request(route)[0], 409)
        self.assertEqual(self.request(
            f"/api/jobs/{self.job_id}/review/frame?item={MEDIA_ITEM}&t=939.5&k={self.key(self.job_id)}",
        )[0], 409)

    def test_queue_for_another_source_is_409(self):
        queue_path = self.root / "reports/jobs/troy/review-queue.json"
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        queue["source"]["sha256"] = "0" * 64
        queue_path.write_text(json.dumps(queue), encoding="utf-8")
        route = f"/api/jobs/{self.job_id}/review/video?k={self.key(self.job_id)}"
        self.assertEqual(self.request(route)[0], 409)

    def test_unplayable_container_is_415(self):
        job_id, _ = self.add_job("mkv", "movie.mkv", "a" * 64)
        status, _, _ = self.request(f"/api/jobs/{job_id}/review/video?k={self.key(job_id)}")
        self.assertEqual(status, 415)

    def test_frame_route_serves_only_strip_timestamps(self):
        def fake_run(command, **kwargs):
            Path(command[-1]).write_bytes(b"\xff\xd8strip\xff\xd9")
            return subprocess.CompletedProcess(command, 0, b"", b"")

        base = f"/api/jobs/{self.job_id}/review/frame?item={MEDIA_ITEM}&k={self.key(self.job_id)}"
        with patch("biliflow.review_evidence.subprocess.run", side_effect=fake_run) as run:
            status, headers, body = self.request(f"{base}&t=939.5")
            self.assertEqual((status, body), (200, b"\xff\xd8strip\xff\xd9"))
            self.assertEqual(headers["Content-Type"], "image/jpeg")
            self.assertEqual(headers["Cache-Control"], "no-store")
            for bad in ("&t=940", "&t=abc", "&t=", ""):
                with self.subTest(bad=bad):
                    self.assertEqual(self.request(f"{base}{bad}")[0], 400)
            self.assertEqual(run.call_count, 1)
        cached = self.root / "cache/review-frames" / MEDIA_SHA[:16] / "0000939500-w640.jpg"
        self.assertTrue(cached.is_file())
        self.assertEqual(
            self.request(f"/api/jobs/{self.job_id}/review/frame?item=nope&t=939.5&k={self.key(self.job_id)}")[0],
            404,
        )

    def make_decidable(self):
        # Fields the queue HTML writer needs (the media fixture omits them).
        queue_path = self.root / "reports/jobs/troy/review-queue.json"
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        queue["items"][0].update({"priority": "high", "labels": ["nsfw"], "max_score": 0.999})
        queue_path.write_text(json.dumps(queue), encoding="utf-8")

    def post_decision(self, decision):
        return self.request(
            f"/api/jobs/{self.job_id}/review/decision", method="POST",
            headers={"X-BiliFlow-Token": "test-token", "Content-Type": "application/json"},
            body=json.dumps({"id": MEDIA_ITEM, "decision": decision}).encode(),
        )

    def test_queue_reads_wait_for_an_in_flight_review_write(self):
        # Regression (review fix 1): the focus page reads the queue for frames,
        # evidence and polling while a decision swaps the queue file. On
        # Windows an open reader made Path.replace fail (WinError 5) and the
        # decision was lost, so reads and review writes are serialized.
        self.make_decidable()
        base = f"/api/jobs/{self.job_id}/review"
        entered, release = threading.Event(), threading.Event()
        order = []

        def slow_record(**kwargs):
            entered.set()
            release.wait(5)
            order.append("write")
            return record_review_decision(**kwargs)

        with patch("biliflow.control_center.record_review_decision", side_effect=slow_record):
            poster = threading.Thread(target=lambda: order.append(("decision", self.post_decision("CUT")[0])))
            poster.start()
            self.assertTrue(entered.wait(5))
            readers = [
                threading.Thread(target=lambda route=route: order.append((route, self.request(f"{base}/{route}")[0])))
                for route in ("queue", "resources", f"evidence?item={MEDIA_ITEM}",
                              f"frame?item={MEDIA_ITEM}&t=1&k={self.key(self.job_id)}")
            ]
            for thread in readers:
                thread.start()
            time.sleep(0.3)
            self.assertEqual(order, [], "queue reads must wait for the in-flight write")
            release.set()
            for thread in [poster, *readers]:
                thread.join(10)
        self.assertEqual(order[0], "write")
        statuses = dict(entry for entry in order[1:])
        self.assertEqual(statuses["decision"], 200)
        self.assertEqual(statuses["queue"], 200)
        self.assertEqual(statuses["resources"], 200)
        self.assertEqual(statuses[f"evidence?item={MEDIA_ITEM}"], 200)
        # t=1 is not a strip timestamp: the frame route answered after the lock, with 400.
        self.assertEqual(statuses[f"frame?item={MEDIA_ITEM}&t=1&k={self.key(self.job_id)}"], 400)

    def test_studio_logo_flag_is_passed_only_when_explicitly_true(self):
        # R3b "Đây là logo hãng phim — giữ & nhớ" posts remember_studio_logo: true.
        self.make_decidable()
        seen = []

        def capture(**kwargs):
            seen.append(kwargs.get("remember_studio_logo"))
            return json.loads((self.root / "reports/jobs/troy/review-queue.json").read_text(encoding="utf-8"))

        with patch("biliflow.control_center.record_review_decision", side_effect=capture):
            for flag in (True, "yes", None):
                body = {"id": MEDIA_ITEM, "decision": "KEEP"}
                if flag is not None:
                    body["remember_studio_logo"] = flag
                status = self.request(
                    f"/api/jobs/{self.job_id}/review/decision", method="POST",
                    headers={"X-BiliFlow-Token": "test-token", "Content-Type": "application/json"},
                    body=json.dumps(body).encode(),
                )[0]
                self.assertEqual(status, 200)
        self.assertEqual(seen, [True, False, False])

    def test_rapid_decisions_survive_concurrent_media_and_queue_reads(self):
        # Same race at full speed with the real writer: every decision is saved.
        self.make_decidable()
        base = f"/api/jobs/{self.job_id}/review"
        stop = threading.Event()
        read_errors = []

        def reader(route):
            while not stop.is_set():
                status = self.request(f"{base}/{route}")[0]
                if status != 200:
                    read_errors.append((route, status))

        readers = [
            threading.Thread(target=reader, args=(route,), daemon=True)
            for route in ("queue", f"evidence?item={MEDIA_ITEM}", "queue", f"evidence?item={MEDIA_ITEM}")
        ]
        for thread in readers:
            thread.start()
        failures = []
        try:
            for index in range(40):
                status, _, body = self.post_decision(("KEEP", "CUT")[index % 2])
                if status != 200:
                    failures.append((index, status, body[:200]))
        finally:
            stop.set()
            for thread in readers:
                thread.join(10)
        self.assertEqual(failures, [])
        self.assertEqual(read_errors, [])
        queue = json.loads((self.root / "reports/jobs/troy/review-queue.json").read_text(encoding="utf-8"))
        self.assertEqual(queue["items"][0]["decision"], "CUT")
        self.assertEqual(
            sum(1 for entry in queue["audit_log"] if entry["action"] == "DECIDE"), 40,
        )

    def test_start_only_configures_a_job_waiting_for_setup(self):
        from biliflow.scheduler import JobScheduler
        self.center.scheduler = JobScheduler(self.root, self.store)
        headers = {"X-BiliFlow-Token": "test-token", "Content-Type": "application/json"}
        body = json.dumps({
            "content_style": "live_action", "profile": "careful",
            "detectors": ["advertising"], "ocr_recognition_batch_size": 1, "fast_scan": True,
        }).encode()

        def queued_events(job_id):
            return [event for event in self.store.events(job_id) if event["event_type"] == "JOB_QUEUED"]

        # A stale browser card cannot re-queue a job that already left setup.
        status, _, raw = self.request(
            f"/api/jobs/{self.job_id}/start", method="POST", headers=headers, body=body,
        )
        self.assertEqual(status, 400)
        self.assertIn("không xếp hàng lại", json.loads(raw)["error"])
        self.assertEqual(self.store.get_job(self.job_id)["state"], "WAITING_REVIEW")
        self.assertEqual(queued_events(self.job_id), [])
        source = self.root / "input" / "new.mp4"
        source.write_bytes(b"video")
        job = self.store.upsert_job(
            job_key="new", source_path=source, source_sha256="2" * 64,
            source_size_bytes=5, source_mtime_ns=source.stat().st_mtime_ns,
            state="NEEDS_METADATA",
        )
        with patch(
            "biliflow.scheduler.pipeline_stages",
            return_value=[PipelineStage("preflight", "PREFLIGHT", tuple())],
        ):
            status, _, raw = self.request(
                f"/api/jobs/{job['id']}/start", method="POST", headers=headers, body=body,
            )
            self.assertEqual(status, 200, raw)
            self.assertEqual(self.store.get_job(job["id"])["state"], "QUEUED")
            self.assertEqual(self.store.setting(f"detector_groups:{job['id']}"), ["advertising"])
            # A second Start from the same stale card is refused, not re-queued.
            status, _, _ = self.request(
                f"/api/jobs/{job['id']}/start", method="POST", headers=headers, body=body,
            )
        self.assertEqual(status, 400)
        self.assertEqual(len(queued_events(job["id"])), 1)


if __name__ == "__main__":
    unittest.main()
