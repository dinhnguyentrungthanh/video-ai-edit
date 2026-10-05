import hashlib
import hmac
import http.client
import importlib
import importlib.util
import json
import os
import re
import shutil
import socket
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
from biliflow.http_guards import CONTENT_LENGTH_MESSAGE, REQUEST_TIMEOUT_SECONDS
from biliflow.job_pipeline import PipelineStage
from biliflow.job_store import IN_PROCESS_STATES, JobStore
from biliflow.final_renderer import render_progress_path
from biliflow.review_evidence import ReviewFrameCache
from biliflow.review_workflow import record_review_decision
from biliflow.scheduler import STARTABLE_STATES, JobScheduler


_SHELL_DELETE_PATCH = None


def setUpModule():
    """Contract R13: no test in this module may reach the real Windows Recycle Bin."""
    global _SHELL_DELETE_PATCH
    if importlib.util.find_spec("biliflow.recycle_bin") is None:  # batch 3 step A5 not landed yet
        return
    recycle_bin = importlib.import_module("biliflow.recycle_bin")

    def refuse(*_args, **_kwargs):
        raise AssertionError("real Recycle Bin call in a test")

    _SHELL_DELETE_PATCH = patch.object(recycle_bin, "_shell_delete", refuse)
    _SHELL_DELETE_PATCH.start()


def tearDownModule():
    global _SHELL_DELETE_PATCH
    if _SHELL_DELETE_PATCH is not None:
        _SHELL_DELETE_PATCH.stop()
        _SHELL_DELETE_PATCH = None


def _dashboard_function(script, name):
    """Source of one named JS function of the dashboard script (brace matched)."""
    match = re.search(rf"(?:async )?function {re.escape(name)}\(", script)
    if match is None:
        raise AssertionError(f"function {name} is missing from the dashboard")
    start = script.index("{", match.end())
    depth = 0
    for index in range(start, len(script)):
        if script[index] == "{":
            depth += 1
        elif script[index] == "}":
            depth -= 1
            if depth == 0:
                return script[match.start():index + 1]
    raise AssertionError(f"function {name} is not closed")


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
        # Batch 4: a tab click scrolls to the top of its list, up or down, stuck or not.
        self.assertIn(
            "function selectJobTab(tab){activeJobTab=tab;renderJobs(true);scrollToJobList();revealActiveTab()}", page,
        )
        self.assertNotIn("keepTabsInView", page)
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
        # "Chạy lại kiểm tra" says where the new revision waits, read from the poll after the POST.
        self.assertEqual(out["rerun_posts"], [{"id": 63, "rerun": True, "detectors": ["advertising"]}])
        self.assertEqual(
            out["rerun_notice"],
            "Đã xếp #63 chạy lại theo phạm vi mới trong một revision riêng (lượt 4/4).",
        )
        self.assertFalse(out["rerun_notice_is_error"])

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
        # Start says where #46 waits, built from the poll after the POST (#44, #45 were clicked first).
        self.assertEqual(out["start_notice"], "Đã xếp #46 Tập 46.mp4 vào hàng đợi quét cảnh (lượt 3/3).")
        self.assertFalse(out["start_notice_is_error"])
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
        # Stage rows, source_cleanups and source_archives rows have their own states;
        # these never reach jobs.state.
        written -= {"PENDING", "RUNNING", "FAILED_RETRYABLE", "RECYCLED", "RESTORED", "ARCHIVED", "RESTORING"}
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

    def test_dashboard_cleanup_markup_and_texts(self):
        from biliflow.export_guards import SOURCE_CLEANED_MESSAGE

        page = _dashboard_html()
        # The dialog sits outside #jobs, so the 3 s rebuild never touches it.
        dialog = page.index('<dialog id="cleanup-dialog" aria-labelledby="cleanup-title">')
        self.assertGreater(dialog, page.index('<section id="jobs" class="job-list"></section></section></main>'))
        self.assertLess(dialog, page.index("<script>"))
        self.assertIn(
            '<form class="cleanup-form" method="dialog" onsubmit="return false"><h2 id="cleanup-title">'
            'Chuyển video gốc vào Thùng rác</h2><div id="cleanup-dialog-body" class="cleanup-scroll"></div>'
            '<div class="cleanup-actions"><button type="button" id="cleanup-cancel" onclick="closeCleanupDialog()">'
            'Hủy</button><button type="button" class="danger" id="cleanup-confirm" onclick="confirmCleanup()" '
            'disabled>Chuyển vào Thùng rác</button></div></form></dialog>',
            page,
        )
        self.assertIn(f"const SOURCE_CLEANED_MESSAGE='{SOURCE_CLEANED_MESSAGE}';", page)
        self.assertNotIn("__SOURCE_CLEANED", page)
        self.assertNotIn("__SOURCE", page)
        self.assertNotIn("__EXPORT", page)
        for text in (
            "Đã dọn video gốc · ", " (đang ở Thùng rác)",
            " · Windows chưa xác nhận bản ghi trong Thùng rác; hãy kiểm tra Thùng rác.",
            "Đang dọn video gốc…", "Đã khôi phục video gốc (SHA-256 khớp)",
            "Lần dọn trước không thành công: ", "Không còn video gốc trong input", "Chưa dọn được: ",
            " Chọn</label>", ">Dọn video gốc</button>", "Đang dọn video gốc; chờ lượt hiện tại xong.",
            "Chép lại video gốc vào input để chạy lại (đúng tên: ",
            'role="group" aria-label="Dọn video gốc"', "Dọn video gốc: ", " video dọn được · đã chọn ",
            "Chọn tất cả video dọn được", ">Bỏ chọn</button>", "Dọn video gốc đã chọn (",
            "Chỉ chuyển vào Thùng rác của Windows, không xóa vĩnh viễn.",
            "Mỗi lần dọn tối đa 50 video; đã chọn 50 video đầu tiên.",
            "Không lấy được danh sách dọn video gốc: ",
            "Các video gốc dưới đây sẽ được chuyển vào Thùng rác của Windows (không xóa vĩnh viễn). Report, "
            "quyết định duyệt, bộ nhớ logo/studio và video đã xuất được giữ nguyên. Dung lượng chỉ được giải "
            "phóng khi bạn dọn sạch Thùng rác; trước đó bạn có thể khôi phục video từ Thùng rác.",
            '<p class="cleanup-alert" role="alert">', "<th>Video gốc</th><th>Dung lượng</th>"
            "<th>Video đã xuất</th><th>Xuất lúc</th>", 'data-label="Video gốc"', 'data-label="Dung lượng"',
            'data-label="Video đã xuất"', 'data-label="Xuất lúc"', "Đã bỏ qua (không xuất)", "Bỏ qua lúc ",
            "Tổng cộng: ${n} video · ", " sẽ được giải phóng sau khi dọn sạch Thùng rác.",
            "Thùng rác của ổ ${rb.volume} đang chứa ", " / giới hạn ", "; sau khi chuyển: ",
            '<p class="cleanup-block" role="alert">', "Không có video nào dọn được trong lựa chọn này.",
            "Không thể dọn:", "Chuyển ${n} video vào Thùng rác",
            "Đang kiểm tra SHA-256 và chuyển vào Thùng rác…",
            "Có thể mất vài phút với nhiều video; đừng tắt BiliFlow.",
            "/api/source-cleanup/preview?ids=", "post('/api/source-cleanup',{job_ids:ids,preview_id:p.preview_id})",
            "Đã chuyển ${n} video gốc vào Thùng rác (", "). Dung lượng được giải phóng khi bạn dọn sạch Thùng rác.",
            "Không chuyển được video gốc nào vào Thùng rác.", " Không dọn được #${x.job_id}: ",
            ": đã chuyển nhưng Windows chưa xác nhận bản ghi trong Thùng rác; hãy kiểm tra Thùng rác.",
            "Danh sách đã thay đổi, hãy xem lại.", "Không dọn được: ${e.message}",
        ):
            with self.subTest(text=text):
                self.assertIn(text, page)
        # In-memory selection only; the cleanup code never touches localStorage.
        self.assertIn(
            "const cleanupSelection=new Set();let cleanupPreview=null,cleanupOpening=false,cleanupPosting=false;",
            page,
        )
        script = page[page.index("<script>"):page.rindex("</script>")]
        for name in ("pruneCleanupSelection", "toggleCleanup", "selectAllCleanup", "clearCleanupSelection",
                     "updateCleanupToolbar", "cleanupControls", "cleanupToolbar", "openCleanup",
                     "renderCleanupDialog", "confirmCleanup", "closeCleanupDialog", "watchCleanupDialog"):
            with self.subTest(function=name):
                body = _dashboard_function(script, name)
                self.assertNotIn("storage", body.casefold())
        # Errors keep the server's code and body, so a 409 can carry the new preview.
        self.assertIn(
            "if(!r.ok){const e=Error(v.error||r.statusText);e.status=r.status;e.code=v.code||null;e.body=v;throw e}",
            page,
        )
        # The selection is pruned on every render, before the cards are built.
        self.assertIn("const jobs=status.jobs||[];pruneCleanupSelection(jobs);", page)
        self.assertIn("(tab==='completed'&&groups.completed.length?cleanupToolbar(groups.completed):'')", page)
        # The rerun panel template is unchanged for a video that still has its source.
        self.assertIn("isSourceCleaned(j)?cleanedRerun(j):`<details class=\"rerun-panel\" data-job-id=\"${id}\"", page)
        # Esc cannot close the dialog while the request runs; the dialog listeners are set at boot.
        self.assertIn("d.addEventListener('cancel',e=>{if(cleanupPosting)e.preventDefault()})", page)
        self.assertIn("watchCleanupDialog();(async()=>{", page)
        style = page[page.index("<style>"):page.index("</style>")]
        base = style[:style.index("@media(max-width:1050px)")]
        for rule in (".cleanup-toolbar{", ".cleanup-pick{", ".source-line{", ".cleanup-note{",
                     "#cleanup-dialog{width:min(900px,calc(100vw - 24px));", "max-height:calc(100vh - 24px);",
                     "#cleanup-dialog::backdrop{", ".cleanup-form{display:flex;flex-direction:column;",
                     ".cleanup-scroll{", "overflow:auto", ".cleanup-table-wrap{overflow-x:auto;",
                     ".cleanup-table{", ".cleanup-alert{", ".cleanup-block{", ".cleanup-actions{position:sticky;bottom:0;"):
            with self.subTest(rule=rule):
                self.assertIn(rule, base)
        # <dialog> defaults to a white background: colours are explicit.
        dialog_rule = base[base.index("#cleanup-dialog{"):]
        dialog_rule = dialog_rule[:dialog_rule.index("}")]
        self.assertIn("background:#141923;color:#e8ecf4", dialog_rule)
        phone = style[style.index("@media(max-width:680px)"):]
        for rule in (".cleanup-toolbar button{flex:1 1 100%}", ".cleanup-table thead{display:none}",
                     ".cleanup-table tr,.cleanup-table td{display:block}",
                     '.cleanup-table td::before{content:attr(data-label) ": "', ".cleanup-actions button{flex:1}"):
            with self.subTest(rule=rule):
                self.assertIn(rule, phone)

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
        # A paused export and a cancelled one (render stage retired) wait in "Đang chờ xử lý".
        self.assertEqual(
            (before[65]["tab"], before[65]["exportValue"], before[65]["exportDetail"]),
            ("waiting", "Xuất video tạm dừng",
             "Bấm “Tiếp tục” để xuất tiếp; lệnh xuất giữ vị trí cũ trong hàng đợi."),
        )
        self.assertEqual(
            (before[66]["tab"], before[66]["exportValue"], before[66]["exportDetail"]),
            ("waiting", "Đã hủy xuất video",
             "Mở “Duyệt cảnh” rồi bấm “Hoàn tất duyệt và xuất video” để xếp lệnh xuất mới."),
        )
        self.assertFalse(before[65]["hasExport"])
        self.assertFalse(before[66]["hasExport"])
        self.assertEqual(out["preset_62"], ["custom", "2"])
        self.assertEqual(out["kept_after_load"], {"open": True, "mode": "custom", "gb": "2.5"})
        # A focused number field (the custom size) defers the 3 s rebuild until it loses focus.
        self.assertEqual(out["focused_type"], "number")
        self.assertTrue(out["deferred_while_typing"])
        self.assertTrue(out["rendered_after_blur"])
        self.assertEqual(out["kept_after_blur"], {"open": True, "mode": "custom", "gb": "2.5"})
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

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_dashboard_source_cleanup_in_node(self):
        out = self.run_dashboard_harness("cleanup")
        cards = out["cards"]
        self.assertEqual(out["headings"], [["Đã xuất video", 5], ["Đã bỏ qua (không xuất)", 2]])
        # Eligible: exported #42, skipped #60 and #45 whose source came back (RESTORED).
        for job_id in ("42", "60", "45"):
            with self.subTest(job=job_id):
                self.assertTrue(cards[job_id]["hasCleanupPick"])
                self.assertTrue(cards[job_id]["hasCleanupButton"])
                self.assertFalse(cards[job_id]["cleanupButtonDisabled"])
                self.assertFalse(cards[job_id]["cleanupChecked"])
        self.assertIsNone(cards["42"]["sourceLine"])
        self.assertTrue(cards["45"]["sourceLine"].startswith("Đã khôi phục video gốc (SHA-256 khớp) lúc "))
        self.assertTrue(cards["60"]["hasUnskip"])
        # #41 was cleaned: read-only card, rerun disabled with the exact file name to copy back.
        cleaned = cards["41"]
        self.assertTrue(cleaned["sourceLine"].startswith("Đã dọn video gốc · 244 MB · lúc "), cleaned["sourceLine"])
        self.assertTrue(cleaned["sourceLine"].endswith("(đang ở Thùng rác)"), cleaned["sourceLine"])
        self.assertEqual(cleaned["sourceTone"], "complete")
        self.assertTrue(cleaned["rerunDisabled"])
        self.assertFalse(cleaned["hasRerun"])
        self.assertFalse(cleaned["hasCleanupPick"])
        self.assertFalse(cleaned["hasCleanupButton"])
        self.assertEqual(cleaned["cleanupNote"], "Chép lại video gốc vào input để chạy lại (đúng tên: Tập 11.mp4)")
        # A cleaned skipped video cannot be reopened; an unverified move says so.
        skipped = cards["70"]
        self.assertTrue(skipped["unskipDisabled"])
        self.assertFalse(skipped["hasUnskip"])
        self.assertTrue(skipped["sourceLine"].endswith(
            "(đang ở Thùng rác) · Windows chưa xác nhận bản ghi trong Thùng rác; hãy kiểm tra Thùng rác."))
        self.assertEqual(skipped["cleanupNote"], "Chép lại video gốc vào input để chạy lại (đúng tên: Tập 40.mp4)")
        # Its "Xuất video" box no longer says the source is kept.
        self.assertTrue(skipped["exportDetail"].startswith("Đã bỏ qua lúc "), skipped["exportDetail"])
        self.assertTrue(skipped["exportDetail"].endswith(
            "; report và quyết định duyệt được giữ nguyên; video gốc đã được dọn vào Thùng rác."), skipped["exportDetail"])
        self.assertTrue(cards["60"]["exportDetail"].endswith(
            "; video gốc, report và quyết định duyệt được giữ nguyên."), cards["60"]["exportDetail"])
        # Not cleanable: the reason, or the missing source; no checkbox.
        # (The real jobs 37/38: their export of the current review left output/.)
        self.assertEqual(cards["37"]["sourceLine"],
                         "Chưa dọn được: Không thấy bản xuất trong thư mục output (đã bị dời hoặc đổi tên?)")
        self.assertFalse(cards["37"]["hasCleanupPick"])
        self.assertTrue(cards["37"]["hasRerun"])
        self.assertEqual((cards["3"]["sourceLine"], cards["3"]["sourceTone"]), ("Không còn video gốc trong input", "error"))
        self.assertFalse(cards["3"]["hasCleanupPick"])
        # The toolbar heads the tab.
        toolbar = out["toolbar0"]
        self.assertTrue(toolbar["first"])
        self.assertEqual(toolbar["summary"], "Dọn video gốc: 3 video dọn được · đã chọn 0 (0 MB)")
        self.assertEqual(toolbar["run"], {"text": "Dọn video gốc đã chọn (0)", "disabled": True, "title": ""})
        self.assertEqual(toolbar["all"]["text"], "Chọn tất cả video dọn được")
        self.assertTrue(toolbar["none"]["disabled"])
        self.assertEqual(toolbar["note"], "Chỉ chuyển vào Thùng rác của Windows, không xóa vĩnh viễn.")
        self.assertEqual(out["selection0"], [])
        # A focused checkbox does not hold the rebuild back (only text fields and selects do).
        self.assertTrue(out["focused_checkbox"])
        self.assertTrue(out["checkbox_focus_rendered"])
        # Select all, untick #45 in place, survive a poll, prune when a video leaves the tab.
        self.assertEqual(out["after_all"]["selection"], [42, 45, 60])
        self.assertEqual(out["after_all"]["checked"], {"42": True, "45": True, "60": True})
        self.assertEqual(out["after_all"]["toolbar"]["run"]["text"], "Dọn video gốc đã chọn (3)")
        self.assertFalse(out["after_all"]["toolbar"]["run"]["disabled"])
        toggle = out["after_toggle"]
        self.assertEqual(toggle["selection"], [42, 60])
        self.assertTrue(toggle["html_unchanged"])
        self.assertEqual((toggle["run_text"], toggle["run_disabled"]), ("Dọn video gốc đã chọn (2)", False))
        self.assertEqual(toggle["summary"], "Dọn video gốc: 3 video dọn được · đã chọn 2 (477 MB)")
        self.assertEqual(out["after_load"]["selection"], [42, 60])
        self.assertEqual(out["after_load"]["checked"], {"42": True, "45": False, "60": True})
        self.assertEqual(out["after_ready"], [42])
        self.assertEqual(out["after_back"], [42])
        self.assertEqual(out["preview_error"], {
            "notice": "Không lấy được danh sách dọn video gốc: Chọn từ 1 đến 50 video mỗi lần dọn.",
            "error": True, "open": False,
        })
        # The preview dialog (ids deduplicated and sorted).
        preview = out["preview"]
        self.assertEqual(preview["calls"], ["42,60"])
        self.assertEqual((preview["open"], preview["shown"], preview["focused"]), (True, 1, 1))
        for text in ("#42 Tập 12.mp4", "240 MB", "Đã bỏ qua (không xuất)", "Bỏ qua lúc ",
                     "Tổng cộng: 2 video · 477 MB sẽ được giải phóng sau khi dọn sạch Thùng rác.",
                     "Thùng rác của ổ E: đang chứa 11,0 GB / giới hạn 48,6 GB; sau khi chuyển: 11,5 GB.",
                     'data-label="Dung lượng"'):
            with self.subTest(text=text):
                self.assertIn(text, preview["body"])
        self.assertEqual((preview["confirm"], preview["confirmDisabled"]), ("Chuyển 2 video vào Thùng rác", False))
        # Hủy posts nothing, and confirm without an open preview posts nothing.
        self.assertEqual(out["cancel"], {"posts": 0, "open": False, "preview_cleared": True})
        # Two clicks on confirm: one POST of the eligible ids with the preview id; Esc and Hủy do nothing meanwhile.
        posting = out["posting"]
        self.assertEqual(posting["confirm"], "Đang kiểm tra SHA-256 và chuyển vào Thùng rác…")
        self.assertTrue(posting["confirmDisabled"])
        self.assertTrue(posting["cancelDisabled"])
        self.assertTrue(posting["esc_prevented"])
        self.assertTrue(posting["wait_shown"])
        self.assertTrue(posting["open_after_close_click"])
        # A non-cancelable Esc closes the <dialog> natively: it reopens while the POST runs.
        self.assertTrue(posting["open_after_forced_close"])
        self.assertEqual(posting["reshown"], 1)
        self.assertTrue(posting["preview_kept"])
        ok = out["ok"]
        self.assertEqual(ok["posts"], [{"job_ids": [42, 60], "preview_id": "a" * 64}])
        self.assertTrue(ok["notice"].startswith("Đã chuyển 2 video gốc vào Thùng rác ("), ok["notice"])
        self.assertEqual(ok["notice"], "Đã chuyển 2 video gốc vào Thùng rác (477 MB). "
                                       "Dung lượng được giải phóng khi bạn dọn sạch Thùng rác.")
        self.assertFalse(ok["error"])
        self.assertFalse(ok["open"])
        self.assertEqual(ok["selection"], [])
        self.assertEqual(ok["picks"], [False, False])
        for line in ok["lines"]:
            self.assertTrue(line.startswith("Đã dọn video gốc · ") and line.endswith("(đang ở Thùng rác)"), line)
        self.assertFalse(ok["esc_prevented_when_idle"])
        # A blocked preview cannot be confirmed.
        blocked = out["blocked"]
        self.assertTrue(blocked["confirmDisabled"])
        self.assertEqual(blocked["posts"], 0)
        self.assertIn('<p class="cleanup-block" role="alert">Không thể dọn: Thùng rác của ổ E: đang chứa 11,0 GB',
                      blocked["body"])
        # 409 preview_changed: the dialog stays open with the new list; the next POST uses the new id.
        changed = out["changed"]
        self.assertTrue(changed["open"])
        self.assertIn('<p class="cleanup-alert" role="alert">Danh sách đã thay đổi, hãy xem lại.</p>', changed["body"])
        self.assertEqual(changed["preview_id"], "b" * 64)
        self.assertFalse(changed["confirmDisabled"])
        self.assertFalse(changed["cancelDisabled"])
        busy = out["busy"]
        self.assertEqual(busy["body_sent"], {"job_ids": [45], "preview_id": "b" * 64})
        self.assertTrue(busy["open"])
        self.assertIn("Đang dọn video gốc; chờ lần dọn trước xong rồi thử lại.", busy["body"])
        self.assertIn("Không dọn được: boom", out["other_error"]["body"])
        self.assertFalse(out["other_error"]["confirmDisabled"])
        # A partial failure is an error notice; the failed video stays selectable and selected.
        partial = out["partial"]
        self.assertIn("Không dọn được #60: File đang được mở", partial["notice"])
        self.assertTrue(partial["notice"].startswith("Đã chuyển 1 video gốc vào Thùng rác ("))
        self.assertTrue(partial["error"])
        self.assertEqual(partial["selection"], [60])
        self.assertTrue(partial["line60"].startswith("Lần dọn trước không thành công: File đang được mở"))
        self.assertTrue(partial["pick60"])
        self.assertTrue(partial["line45"].startswith("Đã dọn video gốc · "))
        # Nothing cleanable in the selection.
        nothing = out["nothing"]
        self.assertTrue(nothing["confirmDisabled"])
        self.assertIn("Không có video nào dọn được trong lựa chọn này.", nothing["body"])
        self.assertIn("<p>Không thể dọn:</p><ul><li>#3 Tập 3.mp4 — Video gốc không còn trong thư mục input</li></ul>",
                      nothing["body"])
        # A running cleanup disables the card button and the toolbar run button.
        running = out["running"]
        self.assertTrue(running["card"])
        self.assertEqual(running["title"], "Đang dọn video gốc; chờ lượt hiện tại xong.")
        self.assertTrue(running["toolbar"]["run"]["disabled"])
        self.assertTrue(running["static_run_disabled"])
        # "Chọn tất cả" stops at 50 videos, the lowest ids first.
        limit = out["limit"]
        self.assertEqual((limit["count"], limit["eligible"], limit["first50"]), (50, 56, True))
        self.assertEqual(limit["notice"], "Mỗi lần dọn tối đa 50 video; đã chọn 50 video đầu tiên.")
        self.assertEqual(limit["toolbar"]["run"]["text"], "Dọn video gốc đã chọn (50)")

    def test_dashboard_cancel_and_archive_markup_texts(self):
        page = _dashboard_html()
        script = page[page.index("<script>"):page.rindex("</script>")]
        # Batch 4c: Hủy asks first and cannot be sent twice; no "Bỏ hủy"/reopen (lead decision L1).
        self.assertIn(
            "const cancellingJobs=new Set();const hidingJobs=new Set();const recheckingRows=new Set();"
            "const foldOpen={cancelled:false,hidden:false,archived:false};", page,
        )
        self.assertIn(
            "onclick=\"cancelJob(${id},this)\" ${cancellingJobs.has(id)?'disabled':''}>"
            "${cancellingJobs.has(id)?'Đang hủy…':'Hủy'}</button>", page,
        )
        self.assertNotIn("act(${id},'cancel')", page)
        for text in ("Bỏ hủy", "/reopen", "reopenJob"):
            with self.subTest(absent=text):
                self.assertNotIn(text, page)
        self.assertIn(
            "Hủy video #${j.id} ${videoName(j)}?\\n“Hủy” dừng hẳn việc quét hoặc lệnh xuất đang chờ/đang chạy "
            "của video này. Video chuyển vào nhóm “Đã hủy” ở cuối mục “Đang chờ xử lý” (có thể “Ẩn khỏi danh "
            "sách”); report, quyết định duyệt và video gốc giữ nguyên. Muốn làm lại: bấm “Chạy lại kiểm tra”, "
            "hoặc mở “Duyệt cảnh” nếu video đã có danh sách duyệt.\\n• “Dừng sau bước”/“Dừng ngay” chỉ tạm "
            "dừng; “Tiếp tục” chạy tiếp từ chỗ cũ.\\n• “Bỏ qua (không xuất)” dành cho video đã duyệt xong: "
            "đánh dấu xong mà không xuất, chuyển sang “Hoàn tất”.", page,
        )
        for text in (
            "Đang hủy…", "Đã hủy #${id}. Video nằm trong nhóm “Đã hủy” ở cuối mục “Đang chờ xử lý”.",
            "if(err.code==='already_cancelled')notify(err.message)", "Không thể hủy #${id}: ",
            # Hidden jobs: a closed fold with compact rows; only cancelled cards can be hidden.
            "if(j.state==='CANCELLED')a.push(hideButton(j))",
            'title="Chỉ ẩn khỏi danh sách; không xóa report, quyết định duyệt hay video gốc.">',
            "'Ẩn khỏi danh sách'", "'Đang ẩn…'", "'Hiện lại'", "'Đang hiện lại…'", "Đã hủy${stamp?` · ẩn lúc ${stamp}`:''}",
            "Đã ẩn #${id} khỏi danh sách. Mở “Đã ẩn” ở cuối mục “Đang chờ xử lý” để hiện lại.",
            "Đã hiện lại #${id} trong nhóm “Đã hủy”.",
            "function isHidden(j){return !!j&&j.state==='CANCELLED'&&!!j.hidden_at}",
            "hidden=jobs.filter(isHidden);jobs.forEach(j=>{if(!isHidden(j))groups[jobTab(j)].push(j)})",
            "['Đã hủy',items.filter(j=>j.state==='CANCELLED').sort(byRecent),'cancelled']",
            "['Đã ẩn',hidden.slice().sort(byHidden),'hidden']",
            '<details class="phase-group phase-fold" data-fold="${key}" ${foldOpen[key]?\'open\':\'\'} '
            "ontoggle=\"foldOpen['${key}']=this.open\"><summary class=\"phase-heading\">",
            "dropForeignDrafts();captureFoldState();captureRerunPanelDrafts();",
            # "Kiểm tra lại Thùng rác" only reads the bin.
            "if(cleanupRecheckable(j))a.push(recheckButton('source_cleanup',j.source_cleanup.id,id))",
            'title="Chỉ đọc Thùng rác của Windows để tìm bản ghi; không chuyển hay xóa file nào.">',
            "'Kiểm tra lại Thùng rác'", "'Đang kiểm tra…'", "post('/api/source-recycle-check',{kind,id:rowId})",
            "Không kiểm tra lại được Thùng rác cho #${jobId}: ", "Đã thấy trong Thùng rác khi kiểm tra lại",
            " Lần kiểm tra lại gần nhất${s?` (${s})`:''} vẫn chưa thấy.",
        ):
            with self.subTest(text=text):
                self.assertIn(text, page)
        for name in ("cancelJob", "setHidden", "hideJob", "unhideJob", "recheckBin", "foldSection",
                     "captureFoldState", "hiddenRow", "scrollToJobList", "revealActiveTab"):
            with self.subTest(function=name):
                self.assertNotIn("storage", _dashboard_function(script, name).casefold())
        style = page[page.index("<style>"):page.index("</style>")]
        for rule in (".phase-fold>summary{cursor:pointer;list-style:none}", '.phase-fold>summary::before{content:"▸";',
                     '.phase-fold[open]>summary::before{content:"▾"}', ".hidden-list{", ".hidden-row{",
                     "@media(max-width:680px){.hidden-row button{flex:1 1 100%}}"):
            with self.subTest(rule=rule):
                self.assertIn(rule, style)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_dashboard_tab_switch_scrolls_to_the_list_in_node(self):
        out = self.run_dashboard_harness("scroll")
        # Booting and polling never scroll; the list top sits at 600 - 72 (header) - 56 (tab bar).
        self.assertEqual(out["boot_calls"], 0)
        self.assertEqual(out["poll_calls"], 0)
        # Below or above the list (deep in a long tab), a tab click lands on the start of the list.
        self.assertEqual(out["below"], {"calls": [[472, "auto"]], "y": 472})
        self.assertEqual(out["above"], {"calls": [[472, "auto"]], "y": 472})
        # Already there (±1 px): nothing moves.
        self.assertEqual(out["aligned"], {"calls": [], "y": 472})
        self.assertEqual(out["near"], {"calls": [], "y": 473})
        # Computed after the render: an empty tab, then a full one, both land on the list.
        self.assertEqual(out["empty"], {"calls": [[472, "auto"]], "y": 472})
        self.assertEqual(out["full"], {"calls": [], "y": 472})
        # The bar scrolls sideways to show the active tab, to the right and back to the left.
        self.assertEqual(out["bar_right"], 900 + 160 - 600)
        self.assertEqual(out["bar_left"], 100)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_dashboard_cancel_hide_and_unhide_in_node(self):
        out = self.run_dashboard_harness("cancelled")
        # Hidden #4 is left out of the counts; stale hidden_at on a FAILED job (#8) is ignored.
        self.assertEqual([tab[2] for tab in out["bar"]], [4, 1, 0, 0, 0, 1])
        self.assertEqual(out["headings"], [
            ["Tạm dừng / lỗi / có thể tiếp tục", 2], ["Đã hủy", 2], ["Đã ẩn", 1],
        ])
        # Both folds start closed; cancelled cards newest first; hidden ones as compact rows.
        self.assertEqual(out["folds"], [
            {"key": "cancelled", "open": False, "label": "Đã hủy", "count": 2, "ids": [2, 3]},
            {"key": "hidden", "open": False, "label": "Đã ẩn", "count": 1, "ids": [4]},
        ])
        [row] = out["hidden_rows"]
        self.assertEqual((row["id"], row["name"], row["text"], row["disabled"]), (4, "#4 · Tập 4.mp4", "Hiện lại", False))
        self.assertRegex(row["when"], r"^Đã hủy · ẩn lúc \d{2}:\d{2}:\d{2} \d{2}/\d{2}/\d{4}$")
        # A cancelled card offers "Ẩn khỏi danh sách" and "Chạy lại kiểm tra", not Hủy.
        cancelled = out["card2"]
        self.assertEqual((cancelled["tab"], cancelled["badge"]), ("waiting", "Đã hủy"))
        self.assertFalse(cancelled["hasCancel"])
        self.assertTrue(cancelled["hasHide"])
        self.assertEqual(cancelled["hideTitle"],
                         "Chỉ ẩn khỏi danh sách; không xóa report, quyết định duyệt hay video gốc.")
        self.assertTrue(cancelled["hasRerun"])
        for key in ("card6", "card8"):
            with self.subTest(card=key):
                self.assertEqual(out[key]["cancelButton"], {"disabled": False, "text": "Hủy"})
                self.assertFalse(out[key]["hasHide"])
        # Declining the confirm posts nothing; the text is lead decision L3.
        declined = out["declined"]
        self.assertEqual(declined["posts"], 0)
        self.assertEqual(declined["card"], {"disabled": False, "text": "Hủy"})
        self.assertEqual(
            declined["confirm"],
            "Hủy video #6 Tập 6.mp4?\n“Hủy” dừng hẳn việc quét hoặc lệnh xuất đang chờ/đang chạy của video này. "
            "Video chuyển vào nhóm “Đã hủy” ở cuối mục “Đang chờ xử lý” (có thể “Ẩn khỏi danh sách”); report, "
            "quyết định duyệt và video gốc giữ nguyên. Muốn làm lại: bấm “Chạy lại kiểm tra”, hoặc mở “Duyệt cảnh” "
            "nếu video đã có danh sách duyệt.\n• “Dừng sau bước”/“Dừng ngay” chỉ tạm dừng; “Tiếp tục” chạy tiếp từ "
            "chỗ cũ.\n• “Bỏ qua (không xuất)” dành cho video đã duyệt xong: đánh dấu xong mà không xuất, chuyển "
            "sang “Hoàn tất”.",
        )
        # A double click asks once and posts once; the button (and a re-render) show "Đang hủy…".
        self.assertEqual(out["busy"], {"disabled": True, "text": "Đang hủy…"})
        self.assertEqual(out["busy_card"], {"disabled": True, "text": "Đang hủy…"})
        done = out["cancelled"]
        self.assertEqual(done["posts"], [{"id": 6, "name": "cancel"}])
        self.assertEqual(done["confirms"], 1)
        self.assertEqual(done["notice"], "Đã hủy #6. Video nằm trong nhóm “Đã hủy” ở cuối mục “Đang chờ xử lý”.")
        self.assertFalse(done["error"])
        self.assertEqual(done["button"], {"disabled": False, "text": "Hủy"})
        self.assertEqual([(f["key"], f["ids"]) for f in done["folds"]], [("cancelled", [6, 2, 3]), ("hidden", [4])])
        # A stale tab: the server refuses a second cancel (409); the page reloads and says so calmly.
        repeat = out["repeat"]
        self.assertEqual(repeat["posts"], [{"id": 5, "name": "cancel"}])
        self.assertEqual(repeat["notice"], "Video #5 đã được hủy trước đó; không hủy thêm lần nữa.")
        self.assertFalse(repeat["error"])
        self.assertEqual(repeat["folds"][0]["ids"], [6, 5, 2, 3])
        # Ẩn khỏi danh sách: the card leaves "Đã hủy" and the tab count; its row can be shown again at once.
        hidden = out["hidden"]
        self.assertEqual(hidden["posts"], [{"id": 3, "name": "hide"}])
        self.assertEqual(hidden["notice"],
                         "Đã ẩn #3 khỏi danh sách. Mở “Đã ẩn” ở cuối mục “Đang chờ xử lý” để hiện lại.")
        self.assertFalse(hidden["error"])
        self.assertEqual([(f["key"], f["ids"]) for f in hidden["folds"]], [("cancelled", [6, 5, 2]), ("hidden", [3, 4])])
        self.assertEqual([(r["id"], r["text"], r["disabled"]) for r in hidden["rows"]],
                         [(3, "Hiện lại", False), (4, "Hiện lại", False)])
        self.assertEqual(hidden["count"], 4)
        # The fold the user opened stays open across polls; the other stays closed.
        self.assertEqual(out["kept_open"], [["cancelled", False], ["hidden", True]])
        unhidden = out["unhidden"]
        self.assertEqual(unhidden["posts"], [{"id": 4, "name": "unhide"}])
        self.assertEqual(unhidden["notice"], "Đã hiện lại #4 trong nhóm “Đã hủy”.")
        self.assertEqual([(f["key"], f["open"], f["ids"]) for f in unhidden["folds"]],
                         [("cancelled", False, [6, 5, 2, 4]), ("hidden", True, [3])])
        self.assertEqual(out["refused"], {"notice": "Video #2 không bị ẩn.", "error": False})
        # Only hidden jobs waiting: the tab counts 0, is not chosen by default, and still lists them.
        only = out["only_hidden"]
        self.assertEqual(only["default_tab"], "completed")
        self.assertEqual(only["bar"], [["waiting", 0], ["scan_queue", 0], ["scanning", 0], ["review", 0],
                                       ["export", 0], ["completed", 1]])
        self.assertEqual(only["folds"], [{"key": "hidden", "open": False, "label": "Đã ẩn", "count": 1, "ids": [20]}])
        self.assertFalse(only["empty"])

    def test_dashboard_archive_markup_and_texts(self):
        from biliflow.export_guards import SOURCE_ARCHIVED_MESSAGE

        page = _dashboard_html()
        script = page[page.index("<script>"):page.rindex("</script>")]
        # Batch 4d: the archive <dialog> is static markup beside the cleanup one; its listeners are set at boot.
        self.assertIn(
            '<dialog id="archive-dialog" aria-labelledby="archive-title"><form class="cleanup-form" method="dialog" '
            'onsubmit="return false"><h2 id="archive-title">Lưu trữ video gốc</h2><div id="archive-dialog-body" '
            'class="cleanup-scroll"></div><div class="cleanup-actions"><button type="button" id="archive-cancel" '
            'onclick="closeArchiveDialog()">Hủy</button><button type="button" class="warn" id="archive-confirm" '
            'onclick="confirmArchive()" disabled>Lưu trữ</button></div></form></dialog>', page,
        )
        self.assertIn("watchArchiveDialog();watchCleanupDialog();(async()=>{", page)
        self.assertIn(f"const SOURCE_ARCHIVED_MESSAGE='{SOURCE_ARCHIVED_MESSAGE}';", page)
        self.assertNotIn("__SOURCE", page)
        for text in (
            # One "Chọn" box for both actions; the shared selection feeds both toolbar buttons.
            " Chọn</label>", "if(cleanupEligible(j)||archiveEligible(j))a.push(pickControl(j));",
            "if(archiveEligible(j))a.push(archiveControls(j));if(isArchived(j))a.push(archivedControls(j));",
            "if(!cleanupEligible(j)&&!archiveEligible(j))cleanupSelection.delete(id)",
            "Lưu trữ đã chọn (", " video lưu trữ được · đã chọn ", ">Lưu trữ</button>",
            # An archived card: badge, fold, restore, locked rerun and "Mở lại để xuất".
            "['Đã lưu trữ',items.filter(isArchived).sort(byArchived),'archived']", "'Đã lưu trữ'", "'Đang lưu trữ'",
            "a.push(isArchived(j)?lockedRerun(j):isSourceCleaned(j)?cleanedRerun(j):",
            "${esc(isArchived(j)?SOURCE_ARCHIVED_MESSAGE:SOURCE_CLEANED_MESSAGE)}\">Mở lại để xuất</button>",
            "'Khôi phục bản xuất'", "'Đang khôi phục…'", "recheckButton('archive_export',a.id,id)",
            "Đã lưu trữ · video gốc ", " trong kho lưu trữ", " đã vào Thùng rác", "Đang lưu trữ video gốc…",
            "Đang đưa video gốc từ kho lưu trữ về input…", "Lần lưu trữ trước không thành công: ",
            "Chưa lưu trữ được: ", "Đã thấy bản xuất trong Thùng rác khi kiểm tra lại",
            # The dialog and its results.
            "/api/source-archive/preview?ids=", "post('/api/source-archive',{job_ids:ids,preview_id:p.preview_id})",
            "Không lấy được danh sách lưu trữ: ", "Không thể lưu trữ:", "Lưu trữ ${n} video",
            "Đang kiểm tra SHA-256 và lưu trữ…", "Không lưu trữ được: ${e.message}", "Không lưu trữ được video gốc nào.",
            "d.addEventListener('cancel',e=>{if(archivePosting)e.preventDefault()})",
            # "Khôi phục bản xuất" asks first.
            "Khôi phục bản xuất cho #${j.id} ${videoName(j)}?\\nVideo gốc được đưa từ kho lưu trữ về input "
            "(kiểm tra SHA-256); ", "video về mục “Đang chờ duyệt” để xuất lại; bản xuất cũ vẫn ở Thùng rác tới khi "
            "bạn dọn sạch.", "video vẫn ở mục “Hoàn tất” (đã bỏ qua).",
            "post('/api/source-archive/restore',{job_id:id})", "Không khôi phục được #${id}: ",
        ):
            with self.subTest(text=text):
                self.assertIn(text, page)
        self.assertNotIn(" Chọn để dọn</label>", page)
        for name in ("isArchived", "archiveEligible", "pickControl", "archiveControls", "archivedControls",
                     "lockedRerun", "openSelectedArchive", "openArchive", "renderArchiveDialog", "archiveRow",
                     "confirmArchive", "closeArchiveDialog", "watchArchiveDialog", "archiveResultText",
                     "restoreArchive", "archiveCounts", "updateCleanupToolbar"):
            with self.subTest(function=name):
                self.assertNotIn("storage", _dashboard_function(script, name).casefold())
        style = page[page.index("<style>"):page.index("</style>")]
        dialog_rule = style[style.index("#archive-dialog{"):]
        self.assertIn("background:#141923;color:#e8ecf4", dialog_rule[:dialog_rule.index("}")])
        for rule in ("#archive-dialog::backdrop{", ".archive-badge{", ".archive-note{"):
            with self.subTest(rule=rule):
                self.assertIn(rule, style)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_dashboard_archive_restore_and_recheck_in_node(self):
        out = self.run_dashboard_harness("archive")
        stamp = re.compile(r"\d{2}:\d{2}:\d{2} \d{2}/\d{2}/\d{4}")
        # "Hoàn tất": archived videos sit in a closed "Đã lưu trữ" fold (newest archive first) and still count.
        self.assertEqual(out["headings"], [["Đã xuất video", 2], ["Đã bỏ qua (không xuất)", 1], ["Đã lưu trữ", 3]])
        self.assertEqual(out["folds"], [{"key": "archived", "open": False, "label": "Đã lưu trữ", "count": 3,
                                         "ids": [75, 74, 73]}])
        self.assertEqual(out["completed_count"], 6)
        cards = out["cards"]
        for job_id in ("70", "71"):
            self.assertEqual({key: cards[job_id][key] for key in ("pick", "cleanup", "archive", "badge", "line")},
                             {"pick": " Chọn", "cleanup": True, "archive": True, "badge": None, "line": None})
        self.assertEqual({key: cards["72"][key] for key in ("pick", "cleanup", "archive", "line", "tone")}, {
            "pick": " Chọn", "cleanup": True, "archive": False, "tone": "waiting",
            "line": "Chưa lưu trữ được: Kho lưu trữ đã có file “Tập 72.mp4” của video này; BiliFlow không ghi đè",
        })
        exported = cards["73"]
        self.assertEqual({key: exported[key] for key in ("pick", "cleanup", "archive", "badge", "restore", "recheck",
                                                         "tone", "rerun_disabled")}, {
            "pick": None, "cleanup": False, "archive": False, "badge": "Đã lưu trữ",
            "restore": {"disabled": False, "text": "Khôi phục bản xuất"},
            "recheck": {"kind": "archive_export", "row": 1, "disabled": False, "text": "Kiểm tra lại Thùng rác"},
            "tone": "waiting", "rerun_disabled": True,
        })
        self.assertEqual(stamp.sub("<t>", exported["line"]),
                         "Đã lưu trữ · video gốc Tập 73.mp4 (286 MB) trong kho lưu trữ · bản xuất ep-73-reviewed.mp4 "
                         "đã vào Thùng rác lúc <t> · Windows chưa xác nhận bản ghi của bản xuất trong Thùng rác; hãy "
                         "kiểm tra Thùng rác.")
        skipped = cards["74"]
        self.assertEqual((skipped["badge"], skipped["unskip_disabled"], skipped["rerun_disabled"], skipped["recheck"],
                          skipped["tone"], stamp.sub("<t>", skipped["line"])),
                         ("Đã lưu trữ", True, True, None, "complete",
                          "Đã lưu trữ · video gốc Tập 74.mp4 (286 MB) trong kho lưu trữ lúc <t>"))
        self.assertEqual({key: cards["75"][key] for key in ("badge", "restore", "line", "tone", "rerun_disabled")},
                         {"badge": "Đang lưu trữ", "restore": None, "line": "Đang lưu trữ video gốc…",
                          "tone": "running", "rerun_disabled": True})
        # The toolbar: archive counts only what can be archived; #72 is counted for "Dọn" only.
        self.assertEqual((out["toolbar0"]["text"], out["toolbar0"]["disabled"]), ("Lưu trữ đã chọn (0)", True))
        self.assertEqual(out["toolbar1"], {
            "archive": {"text": "Lưu trữ đã chọn (2)", "disabled": False,
                        "summary": "Lưu trữ: 2 video lưu trữ được · đã chọn 2 (572 MB). Video gốc vào kho lưu trữ "
                                   "(thư mục archive), bản xuất vào Thùng rác."},
            "cleanup": "Dọn video gốc đã chọn (3)", "selection": [70, 71, 72],
        })
        preview = out["preview"]
        self.assertEqual((preview["calls"], preview["open"], preview["confirm"], preview["confirmDisabled"],
                          preview["rows"], preview["alert"]),
                         (["70,71"], True, "Lưu trữ 2 video", False,
                          ["archive/sources/ep-70/Tập 70.mp4", "archive/sources/ep-71/Tập 71.mp4"], None))
        self.assertEqual(preview["summary"], "Tổng cộng: 2 video · 572 MB video gốc vào kho lưu trữ · 100 MB bản xuất "
                                             "vào Thùng rác (giải phóng khi bạn dọn sạch Thùng rác).")
        self.assertTrue(preview["bin"].startswith("Thùng rác của ổ E: đang chứa "), preview["bin"])
        # Refusals keep the dialog open: busy shows the server's text, a changed list shows the new preview.
        self.assertEqual((out["busy"]["open"], out["busy"]["alert"], out["busy"]["confirm"]),
                         (True, "Đang dọn, lưu trữ hoặc khôi phục video gốc; chờ lượt trước xong rồi thử lại.",
                          "Lưu trữ 2 video"))
        self.assertEqual((out["changed"]["alert"], out["changed"]["confirm"], out["changed"]["rows"]),
                         ("Danh sách đã thay đổi, hãy xem lại.", "Lưu trữ 1 video", ["archive/sources/ep-70/Tập 70.mp4"]))
        posting = out["posting"]
        self.assertEqual((posting["confirm"], posting["confirmDisabled"], posting["cancelDisabled"],
                          posting["esc_prevented"], posting["wait_shown"], posting["open_after_close_click"]),
                         ("Đang kiểm tra SHA-256 và lưu trữ…", True, True, True, True, True))
        archived = out["archived"]
        self.assertEqual((archived["post"], archived["posts"], archived["open"], archived["selection"],
                          archived["error"], archived["folds"]),
                         ({"job_ids": [70], "preview_id": "c" * 64}, 3, False, [71, 72], False,
                          [["archived", [70, 75, 74, 73]]]))
        self.assertEqual(archived["notice"], "Đã lưu trữ 1 video gốc; bản xuất (100 MB) đã vào Thùng rác, dung lượng "
                                             "được giải phóng khi bạn dọn sạch Thùng rác.")
        self.assertEqual((archived["card"]["badge"], archived["card"]["pick"], archived["card"]["tone"]),
                         ("Đã lưu trữ", None, "complete"))
        # A skipped video: only the source moves, the bin is not involved.
        self.assertEqual((out["skipped_preview"]["bin"], out["skipped_preview"]["summary"]),
                         (None, "Tổng cộng: 1 video · 286 MB video gốc vào kho lưu trữ."))
        self.assertEqual((out["skipped"]["notice"], out["skipped"]["error"], out["skipped"]["card"]["unskip_disabled"]),
                         ("Đã lưu trữ 1 video gốc.", False, True))
        # "Kiểm tra lại Thùng rác" for the export only reads the bin; the card follows.
        recheck = out["recheck"]
        self.assertEqual((recheck["posts"], recheck["notice"], recheck["error"], recheck["card"]["recheck"],
                          recheck["card"]["tone"]),
                         ([{"kind": "archive_export", "id": 1}], "Đã thấy bản xuất trong Thùng rác của Windows.", False,
                          None, "complete"))
        self.assertTrue(stamp.sub("<t>", recheck["card"]["line"]).endswith(
            " · Đã thấy bản xuất trong Thùng rác khi kiểm tra lại lúc <t>."), recheck["card"]["line"])
        # "Khôi phục bản xuất" asks first; declining posts nothing.
        self.assertEqual(out["declined"], {"posts": 0, "confirm": (
            "Khôi phục bản xuất cho #73 Tập 73.mp4?\nVideo gốc được đưa từ kho lưu trữ về input (kiểm tra SHA-256); "
            "video về mục “Đang chờ duyệt” để xuất lại; bản xuất cũ vẫn ở Thùng rác tới khi bạn dọn sạch."
        )})
        self.assertEqual(out["restored"], {
            "posts": [{"job_id": 73}], "notice": "Đã đưa video gốc của #73 về input (SHA-256 khớp).", "error": False,
            "tab": "review", "folds": [["archived", [71, 70, 75, 74]]],
        })
        refused = out["refused"]
        self.assertEqual((refused["notice"], refused["error"], refused["card"]["restore"], refused["card"]["badge"]), (
            "Không khôi phục được #74: Trong input đã có file “Tập 74.mp4”. BiliFlow không ghi đè: dời file đó ra "
            "khỏi input rồi bấm “Khôi phục bản xuất” lại.", True, {"disabled": False, "text": "Khôi phục bản xuất"},
            "Đã lưu trữ",
        ))
        self.assertTrue(refused["confirm"].endswith("; video vẫn ở mục “Hoàn tất” (đã bỏ qua)."), refused["confirm"])
        # While a source-file action runs, restore and archive wait.
        self.assertEqual((out["running"]["restore"], out["running"]["toolbar"]["disabled"]),
                         ({"disabled": True, "text": "Khôi phục bản xuất"}, True))

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
            # Batch 3 (B8): the cleanup preview (read-only, no token, like every GET).
            "/api/source-cleanup/preview?ids=1",
            "/api/job-delete/preview?ids=1",
        ]
        deleter = Mock(side_effect=AssertionError("deleter reached"))
        self.center.source_deleter = deleter
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
        # Batch 3 (B8): the cleanup and delete POSTs need the local Host and the session token.
        cleanup_body = json.dumps(
            {"job_ids": [self.job_id], "preview_id": "a" * 64, "confirm_permanent": True}
        ).encode()
        for route in ("/api/source-cleanup", "/api/job-delete"):
            for host, token in (("evil.example", "test-token"), (f"evil.example:{self.port}", "test-token"),
                                (None, "wrong"), (None, None)):
                with self.subTest(route=route, host=host, token=token):
                    headers = {"Content-Type": "application/json"}
                    if token:
                        headers["X-BiliFlow-Token"] = token
                    status, _, body = self.request(route, host=host, method="POST", headers=headers,
                                                   body=cleanup_body)
                    self.assertEqual(status, 403)
                    self.assertNotIn(b"test-token", body)
        deleter.assert_not_called()
        self.assertTrue(self.source.is_file())
        self.assertIsNone(self.store.latest_source_cleanup(self.job_id))

    def test_every_response_forbids_framing_by_another_site(self):
        # Security review (L4): pages, JSON, streamed media, refusals and the server's own errors
        # all say SAMEORIGIN (a page of this Control Center may still frame another one).
        cases = [
            ("GET", "/", None, 200),
            ("GET", f"/review/{self.job_id}", None, 200),
            ("GET", "/logo-memory", None, 200),
            ("GET", "/healthz", None, 200),
            ("GET", f"/api/jobs/{self.job_id}/review/video?k={self.key(self.job_id)}", None, 200),
            ("GET", "/no-such-page", None, 404),
            ("GET", "/", "evil.example", 403),
            ("POST", "/api/scheduler", None, 403),
            ("PUT", "/", None, 501),
        ]
        for method, path, host, expected in cases:
            with self.subTest(method=method, path=path, host=host):
                body = b"{}" if method != "GET" else None
                headers = {"Content-Type": "application/json"} if body else None
                status, sent, _ = self.request(path, host=host, method=method, headers=headers, body=body)
                self.assertEqual(status, expected)
                self.assertEqual(sent.get("X-Frame-Options"), "SAMEORIGIN")
                self.assertEqual(sent.get("Content-Security-Policy"), "frame-ancestors 'self'")

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
        # Batch 3 (B7, B8): the review page also learns whether the source was
        # cleaned, and the latest cleanup row (none here); batch 4 adds the archive.
        self.assertEqual(
            json.loads(self.request(route)[2]),
            {"status": "WAITING_REVIEW", "output": None, "error": None, "render_progress": None,
             "source_cleaned": False, "source_name": "movie.mp4", "source_cleanup": None,
             "source_archived": False, "source_archive": None},
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

    def test_platform_logo_flag_is_passed_only_when_explicitly_true(self):
        # Batch 4a "Đây là logo nền tảng — làm mờ & nhớ" posts remember_platform_logo: true.
        self.make_decidable()
        seen = []

        def capture(**kwargs):
            seen.append(kwargs.get("remember_platform_logo"))
            return json.loads((self.root / "reports/jobs/troy/review-queue.json").read_text(encoding="utf-8"))

        with patch("biliflow.control_center.record_review_decision", side_effect=capture):
            for flag in (True, "yes", None):
                body = {"id": MEDIA_ITEM, "decision": "BLUR"}
                if flag is not None:
                    body["remember_platform_logo"] = flag
                status = self.request(
                    f"/api/jobs/{self.job_id}/review/decision", method="POST",
                    headers={"X-BiliFlow-Token": "test-token", "Content-Type": "application/json"},
                    body=json.dumps(body).encode(),
                )[0]
                self.assertEqual(status, 200)
        self.assertEqual(seen, [True, False, False])

    def test_logo_memory_routes_are_served_and_writes_need_the_token(self):
        # "Bộ nhớ logo" (batch 4a): the page and the listing are plain GETs; a delete needs the
        # session token and the memory sha the page loaded (409 otherwise, nothing written).
        status, headers, body = self.request("/logo-memory")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertIn("<title>Bộ nhớ logo", body.decode("utf-8"))
        status, _, body = self.request("/api/logo-memory")
        self.assertEqual(status, 200)
        listing = json.loads(body)
        self.assertEqual((listing["memory_sha256"], listing["records"]), (None, []))
        memory = self.root / "state" / "studio-logo-memory.json"
        memory.write_text(json.dumps({"schema_version": 2, "records": []}), encoding="utf-8")
        before = memory.read_bytes()
        request = {"key": "a" * 64 + ":review-x", "expected_sha256": "0" * 64}
        status, _, body = self.request(
            "/api/logo-memory/delete", method="POST",
            headers={"Content-Type": "application/json"}, body=json.dumps(request).encode(),
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"], "Phiên Control Center không hợp lệ")
        status, _, body = self.request(
            "/api/logo-memory/delete", method="POST",
            headers={"X-BiliFlow-Token": "test-token", "Content-Type": "application/json"},
            body=json.dumps(request).encode(),
        )
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["code"], "memory_changed")
        self.assertEqual(memory.read_bytes(), before)
        self.assertFalse((self.root / "state" / "backups").exists())

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

    # ---------------- Request limits (review: negative Content-Length, no read timeout)
    def raw_exchange(self, head, body=b"", *, wait=5.0):
        """Raw request bytes; (everything answered, seconds until the server closed or ``wait`` ran out)."""
        started = time.monotonic()
        chunks = []
        with socket.create_connection(("127.0.0.1", self.port), timeout=wait) as client:
            client.sendall(head.encode("latin-1") + body)
            while True:
                try:
                    chunk = client.recv(65536)
                except TimeoutError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
        return b"".join(chunks), time.monotonic() - started

    def post_head(self, length):
        return (
            f"POST /api/scheduler HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
            "X-BiliFlow-Token: test-token\r\nContent-Type: application/json\r\n"
            f"Content-Length: {length}\r\n\r\n"
        )

    def test_an_invalid_content_length_is_refused_at_once(self):
        # A negative length made rfile.read(-1) wait until the client closed the connection.
        for value in ("-1", "+5", "abc", "1e3", ""):
            with self.subTest(value=value):
                response, elapsed = self.raw_exchange(self.post_head(value))
                self.assertTrue(response.startswith(b"HTTP/1.0 400 "), response[:80])
                self.assertIn(CONTENT_LENGTH_MESSAGE.encode("utf-8"), response)
                self.assertLess(elapsed, 3)
        self.assertIsNone(self.store.setting("scheduler_paused"))

    def test_a_request_that_stops_arriving_is_closed(self):
        self.assertEqual(_handler_class(self.center).timeout, REQUEST_TIMEOUT_SECONDS)
        self.server.RequestHandlerClass.timeout = 0.5
        response, elapsed = self.raw_exchange(self.post_head(20), b'{"paused"')
        self.assertLess(elapsed, 3)
        self.assertTrue(response == b"" or response.startswith(b"HTTP/1.0 408 "), response[:80])
        self.assertIsNone(self.store.setting("scheduler_paused"))
        response, elapsed = self.raw_exchange(f"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n")
        self.assertLess(elapsed, 3)
        self.assertEqual(response, b"")
        self.assertEqual(self.request("/healthz")[0], 200)

    def test_a_body_nested_too_deep_is_refused(self):
        # json.loads raises RecursionError (not a ValueError) for it.
        body = b"[" * 60000
        response, elapsed = self.raw_exchange(self.post_head(len(body)), body)
        self.assertTrue(response.startswith(b"HTTP/1.0 400 "), response[:80])
        self.assertLess(elapsed, 3)
        self.assertIsNone(self.store.setting("scheduler_paused"))

    def test_a_paused_player_still_receives_the_whole_video(self):
        # The request timeout must not cut a stream the player stops reading for a while.
        self.server.RequestHandlerClass.timeout = 0.3
        data = bytes(range(256)) * (32 * 4096)
        self.source.write_bytes(data)
        stat = self.source.stat()
        self.store.upsert_job(
            job_key="troy", source_path=self.source, source_sha256=MEDIA_SHA,
            source_size_bytes=stat.st_size, source_mtime_ns=stat.st_mtime_ns, state="WAITING_REVIEW",
        )
        route = f"/api/jobs/{self.job_id}/review/video?k={self.key(self.job_id)}"
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.connect()
            connection.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 16)
            connection.request("GET", route, headers={"Host": f"127.0.0.1:{self.port}"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            first = response.read(1 << 20)
            time.sleep(1.0)
            rest = response.read()
        finally:
            connection.close()
        self.assertEqual(len(first) + len(rest), len(data))
        self.assertTrue(first + rest == data)


if __name__ == "__main__":
    unittest.main()
