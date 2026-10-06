"""V2 review dialog, batches R0–R4 (docs/DASHBOARD_V2_REVIEW_PLAN.md sections 7.1–7.5).

The new files are whitelisted and served on the PC listener and, with the access cookie, on the phone
listener; the CSP is unchanged, the new files have no blob:, inline script or fetch; the classic pages
stay byte-identical (D2); the R0 entries of PHONE_ALLOWED_POSTS and of the contract endpoints are unchanged (the
video download plan added its "Tải video" routes and endpoints, the delete flow its two PC-only endpoints); review-core.js
matches the classic page (verify-review.cjs) and, for S1, counts bulk actions like the real server.
R2: the bodies the dialog sends (review-core.js decisionBody / undoPlan, equal to the classic page's) are
POSTed to the real /api/jobs/<id>/review/decision|clear route on a temporary root with a synthetic queue,
and the queue it returns matches what the dialog shows after its optimistic change.
R3: bulk-keep / bulk-accept POSTed to the real route change exactly the number of items the dialog's confirm shows
(S1), and the export dialog's options, limits, gate and confirm sentence are those of export_dialog.py.
R4.2: the dialog through the real phone listener (cookie, at most 2 frames at once, the clip with Range, every POST in
PHONE_ALLOWED_POSTS, never finalize), driven by dashboard_v2/browser-check-review-phone.cjs.
Temporary roots and synthetic queues only; the phone listener runs on a fake LAN address (127.0.0.1).
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from biliflow import export_dialog, phone_access, review_workflow
from biliflow.control_center import (
    DASHBOARD_V2_CSP,
    DASHBOARD_V2_DIR,
    DASHBOARD_V2_FILES,
    ControlCenter,
    _dashboard_html,
    _handler_class,
    _phone_access,
    _phone_handler_class,
)
from biliflow.job_store import JobStore
from biliflow.review_evidence import ReviewFrameCache
from biliflow.review_workflow import _interactive_html
from biliflow.scheduler import JobScheduler

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
CLASSIC = json.loads((ROOT / "tests" / "fixtures" / "dashboard_v2_classic_pages.json").read_text(encoding="utf-8"))
NEW_FILES = ("review.css", "review-core.js", "review-detail.js", "review-media.js", "review-cards.js", "review.js")
FAKE_LAN = "127.0.0.1"
CSP_LITERAL = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
               "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'")
PHONE_POSTS_LITERAL = (
    r"/api/scheduler",
    r"/api/ai/check",
    r"/api/jobs/\d+/(?:start|resume|pause|stop-after-stage|cancel|retry|rerun|skip|unskip|hide|unhide)",
    r"/api/jobs/\d+/ai-audit",
    r"/api/jobs/\d+/review/(?:decision|clear|bulk-keep|bulk-accept|finalize)",
    # "Tải video" (video download plan, D2).
    r"/api/downloads",
    r"/api/downloads/\d+/(?:rename|choose|stop|resume|cancel|retry|remove)",
    r"/api/downloads/settings",
    r"/api/downloads/cleanup-temp",
)
# SHA-256 of JSON.stringify(contracts.js endpoints, keys sorted): 50 endpoints at the start of R0 (06c1dd42…),
# 63 with the 13 "Tải video" endpoints of the video download plan (D3, a28b19df…), 52 with deletePreview and delete
# of "Xóa video" (delete flow, 071106c5…), and 65 with both (test/download-delete).
ENDPOINTS_SHA256 = "489eca65d8a778651d939c4b25b06e1b5cc05f7d82b7de380361a628eedbfbd6"


def node(script: str) -> str:
    completed = subprocess.run([NODE, "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120)
    if completed.returncode:
        raise AssertionError(completed.stderr)
    return completed.stdout


def get(port, path, *, host, headers=None) -> tuple[int, dict, bytes]:
    """GET on 127.0.0.1; headers as {lower-case name: [values]} (a page can carry two CSP headers)."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request("GET", path, headers={"Host": host, **(headers or {})})
        response = connection.getresponse()
        parsed: dict[str, list[str]] = {}
        for key, value in response.getheaders():
            parsed.setdefault(key.lower(), []).append(value)
        return response.status, parsed, response.read()
    finally:
        connection.close()


class ReviewR0Serving(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.store = JobStore(self.root / "state" / "control-center.sqlite3")
        center = ControlCenter.__new__(ControlCenter)
        center.root, center.host, center.token, center.store = self.root, "127.0.0.1", "test-token", self.store
        center.frame_cache = ReviewFrameCache(self.root, self.root / "missing-ffmpeg.exe")
        center._stopping = threading.Event()
        center.scheduler = JobScheduler(self.root, self.store)  # never started
        center.recovered = 0
        center.start_ai_audit = mock.Mock(return_value=None)
        self.center = center
        self.pc = ThreadingHTTPServer(("127.0.0.1", 0), _handler_class(center))
        self.pc.daemon_threads = True
        self.pc_host = f"127.0.0.1:{self.pc.server_address[1]}"
        threading.Thread(target=self.pc.serve_forever, daemon=True).start()
        self.phone = _phone_access(center)
        self.phone._lan = lambda: FAKE_LAN  # never the real Wi-Fi

    def tearDown(self):
        self.phone.disable()
        self.pc.shutdown()
        self.pc.server_close()
        self.store.close()
        self.temp.cleanup()

    def pc_get(self, path):
        return get(self.pc.server_address[1], path, host=self.pc_host)

    def test_new_files_are_whitelisted_and_served_on_the_pc_and_on_the_phone_with_the_cookie(self):
        status = self.phone.enable(lambda access: _phone_handler_class(self.center, access), address=FAKE_LAN,
                                   check_address=lambda _a: None, check_port=lambda _p: None, port=0)
        port, host = status["port"], f"127.0.0.1:{status['port']}"
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        connection.request("POST", "/phone-login", body=f"code={status['code']}".encode(),
                           headers={"Host": host, "Content-Type": "application/x-www-form-urlencoded"})
        login = connection.getresponse(); login.read()
        cookie = login.getheader("Set-Cookie").split(";", 1)[0]
        connection.close()
        for name in NEW_FILES:
            with self.subTest(file=name):
                self.assertIn(name, DASHBOARD_V2_FILES)
                expected = (DASHBOARD_V2_DIR / name).read_bytes()
                code, headers, body = self.pc_get("/dashboard-v2/" + name)
                self.assertEqual((code, body), (200, expected))
                self.assertTrue(headers["content-type"][0].startswith("text/css" if name.endswith(".css") else "text/javascript"))
                code, _, _ = get(port, "/dashboard-v2/" + name, host=host)
                self.assertEqual(code, 401, "no cookie, no file")
                code, _, body = get(port, "/dashboard-v2/" + name, host=host, headers={"Cookie": cookie})
                self.assertEqual((code, body), (200, expected))

    def test_both_pages_and_the_demo_server_load_the_new_files_in_order(self):
        tags = ('<link rel="stylesheet" href="review.css">'
                '', '<script defer src="review-core.js"></script><script defer src="review-detail.js"></script><script defer src="review-media.js"></script>'
                '<script defer src="review-cards.js"></script><script defer src="review.js"></script><script defer src="app.js"></script>')
        for page in ("live.html", "index.html"):
            text = (DASHBOARD_V2_DIR / page).read_text(encoding="utf-8")
            with self.subTest(page=page):
                for tag in tags:
                    self.assertIn(tag, text)
                self.assertEqual(text.count('<dialog id="review-dialog" aria-labelledby="review-title"><div id="review-root"></div></dialog>'), 1)
        serve = (DASHBOARD_V2_DIR / "serve.py").read_text(encoding="utf-8")
        for name in NEW_FILES:
            self.assertIn(f'"{name}"', serve)

    def test_csp_is_unchanged_and_the_new_files_have_no_blob_inline_script_or_fetch(self):
        self.assertEqual(DASHBOARD_V2_CSP, CSP_LITERAL)
        code, headers, body = self.pc_get("/dashboard-v2/")
        self.assertEqual(code, 200)
        self.assertIn(CSP_LITERAL, headers["content-security-policy"])
        live = body.decode("utf-8")
        self.assertIn('content="' + CSP_LITERAL.replace("; frame-ancestors 'self'", "") + '"', live)
        for page in ("live.html", "index.html"):
            text = (DASHBOARD_V2_DIR / page).read_text(encoding="utf-8")
            self.assertEqual(re.findall(r"<script(?![^>]*\ssrc=)[^>]*>", text), [], page + ": inline script")
            self.assertEqual(re.findall(r"\son[a-z]+\s*=", text), [], page + ": inline handler")
        for path in sorted(DASHBOARD_V2_DIR.glob("*")):
            if path.suffix in {".js", ".css", ".html"}:
                with self.subTest(file=path.name):
                    text = path.read_text(encoding="utf-8")
                    # A blob: URL as a string, or built from a Blob (comments may say "no blob:").
                    self.assertIsNone(re.search(r"[\"'`]blob:|createObjectURL|new Blob\(", text), path.name)
                    if path.name != "adapter.js":
                        self.assertNotIn("fetch(", text, path.name)
        for name in NEW_FILES:
            text = (DASHBOARD_V2_DIR / name).read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\beval\(|new Function|\son[a-z]+=", text), name)
            self.assertLess(text.count("\n"), 800, name + " stays under 800 lines")

    def test_classic_pages_stay_byte_identical_d2(self):
        self.assertEqual(hashlib.sha256(_dashboard_html().encode()).hexdigest(), CLASSIC["dashboard_sha256"])
        self.assertEqual(hashlib.sha256(_interactive_html("test-token").replace("'/api/", "'/api/jobs/1/review/").encode()).hexdigest(),
                         CLASSIC["review_job_1_token_test_sha256"])
        code, _, body = self.pc_get("/review/1")
        self.assertEqual((code, hashlib.sha256(body).hexdigest()), (200, CLASSIC["review_job_1_token_test_sha256"]))
        code, _, body = self.pc_get("/")
        self.assertEqual((code, hashlib.sha256(body).hexdigest()), (200, CLASSIC["dashboard_sha256"]))

    def test_phone_posts_and_contract_endpoints_are_unchanged(self):
        self.assertEqual(tuple(p.pattern for p in phone_access.PHONE_ALLOWED_POSTS), PHONE_POSTS_LITERAL)
        if not NODE:
            self.skipTest("node is required to read contracts.js")
        digest = node("const c=require('crypto'),e=require('./dashboard_v2/contracts.js').endpoints;"
                      "process.stdout.write(c.createHash('sha256').update(JSON.stringify(Object.fromEntries(Object.entries(e).sort()))).digest('hex'))")
        self.assertEqual(digest, ENDPOINTS_SHA256)


@unittest.skipUnless(NODE, "node is required for the review gates")
class ReviewR0Logic(unittest.TestCase):
    def test_review_core_matches_the_classic_page(self):
        env = {**os.environ, "BILIFLOW_PYTHON": sys.executable}
        completed = subprocess.run([NODE, str(DASHBOARD_V2_DIR / "verify-review.cjs")], cwd=ROOT, env=env,
                                   capture_output=True, text=True, encoding="utf-8", timeout=180)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        summary = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(summary["failed"], 0)
        self.assertGreaterEqual(summary["passed"], 12)

    def test_s1_bulk_counts_are_what_the_server_changes(self):
        """S1: the dialog's bulk counts equal the number of items the real bulk functions change."""
        script = (
            "const vm=require('vm'),fs=require('fs'),R=require('./dashboard_v2/review-core.js');"
            "const ctx={window:{BFContracts:require('./dashboard_v2/contracts.js')},structuredClone};"
            "vm.runInNewContext(fs.readFileSync('dashboard_v2/mock-data.js','utf8'),ctx);"
            "const M=ctx.window.BFMock,j=M.create().jobs.find(x=>x.id===101),q=M.reviewQueue(j,{count:40});"
            "q.items[4].category='text';q.items[4].review_kind='logo_overlay';q.items[4].decision=null;"
            "const filters=R.FILTER_IDS.filter(f=>R.bulkFilters(f));"
            "process.stdout.write(JSON.stringify({queue:q,filters:Object.fromEntries(filters.map(f=>[f,R.bulkFilters(f)])),"
            "counts:Object.fromEntries(filters.map(f=>[f,[R.bulkCount(q,f,'keep'),R.bulkCount(q,f,'accept')]]))}))"
        )
        data = json.loads(node(script))
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / "reports").mkdir()
            path = root / "reports" / "review-queue.json"
            for name, server_filters in data["filters"].items():
                changed = []
                for function in (review_workflow.bulk_keep_review_items, review_workflow.bulk_accept_suggested_decisions):
                    path.write_text(json.dumps(data["queue"]), encoding="utf-8")
                    total = 0
                    for value in server_filters:
                        payload = function(project_root=root, queue_path=path, review_filter=value)
                        total += payload["audit_log"][-1]["changed_count"]
                    changed.append(total)
                with self.subTest(filter=name):
                    self.assertEqual(changed, data["counts"][name])
            self.assertEqual(sorted(p.name for p in root.rglob("*") if p.is_file()), ["review-queue.html", "review-queue.json"],
                             "the bulk functions only touched the temporary queue")


# R2: the dialog's bodies for a table of choices, and the item / counts the dialog shows after each one.
R2_PAYLOADS_JS = r"""
const vm=require('vm'),fs=require('fs'),R=require('./dashboard_v2/review-core.js');
const ctx={window:{BFContracts:require('./dashboard_v2/contracts.js')},structuredClone};
vm.runInNewContext(fs.readFileSync('dashboard_v2/mock-data.js','utf8'),ctx);
const M=ctx.window.BFMock,j=M.create().jobs.find(x=>x.id===101),queue=M.reviewQueue(j,{count:12,advisory:2});
const local=structuredClone(queue),find=id=>local.items.concat(local.advisory_items).find(x=>x.id===id),steps=[],undo=[];
const expect=(x,kind,body)=>({kind,body,status:200,item:{id:x.id,decision:x.decision,region:x.decision_region_source_pixels??null,note:x.decision_note??null},
  counts:R.countsFrom(local.items),queue_status:R.statusFrom(local.items)});
function decide(id,d,o){
  const x=find(id),plan=R.decisionBody(x,d,o||{});
  if(plan.error)throw new Error(plan.error);
  undo.push(R.undoEntry(x,!x.decision&&R.isAdvisoryItem(local,x)));
  // The server moves an advisory card into the main list when it is decided (the dialog then applies its queue).
  const a=local.advisory_items.indexOf(x);if(a>=0){local.advisory_items.splice(a,1);delete x.advisory;local.items.push(x);}
  R.applyDecision(local,x,d,plan.region,plan.note,plan.studio,plan.platform);
  steps.push(expect(x,'decision',plan.body));
}
function refused(id,d,o,label){const x=find(id),plan=R.decisionBody(x,d,o||{});steps.push({kind:'decision',body:plan.body||{id,decision:d,full_frame:false,note:null},status:400,label,local_error:plan.error||null});}
decide('gore-101-0004','KEEP');
decide('adult-101-0000','CUT');
decide('text-101-0003','NEEDS_MORE_CONTEXT');
decide('gore-101-0004','BLUR',{fullFrame:true});
decide('text-101-0003','BLUR');                                      // R2-K: "Làm mờ" of a region item
decide('visual_logo-101-0001','KEEP',{note:R.regionNote('KEEP')});   // region buttons
decide('visual_logo-101-0001','BLUR',{note:R.regionNote('BLUR')});
{const x=find('text-101-0003');undo.push(R.undoEntry(x,false));R.applyClear(local,x);steps.push(expect(x,'clear',{id:x.id}));}
{const e=undo.pop(),plan=R.undoPlan(e),x=find(e.id);R.applyDecision(local,x,e.prev.decision,e.prev.region,e.prev.note,e.prev.studio,e.prev.platform);steps.push(expect(x,plan.kind,plan.body));}
decide('advisory-101-0001','CUT');
decide('adult-101-0009','KEEP');                                     // after the Visual AI confirm
decide('visual_logo-101-0008','BLUR',{fullFrame:true});
refused('visual_logo-101-0002','KEEP',{studio:true},'studio memory: no readable preview on the temporary root');
refused('visual_logo-101-0002','BLUR',{platform:true},'platform memory: no frames on the temporary root');
refused('visual_logo-101-0008','BLUR',{},'BLUR without a region');
process.stdout.write(JSON.stringify({queue,steps}));
"""


class _TempJobFixture(unittest.TestCase):
    """A temporary root with a real JobStore, the real handler on port 0 and one job with a synthetic queue."""

    SOURCE, DURATION = "r2-demo.mp4", 60.0

    def write_source(self, source: Path) -> None:
        source.write_bytes(b"synthetic" * 64)

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        for name in ("input", "reports/jobs/r2-demo", "output", "state"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.store = JobStore(self.root / "state" / "control-center.sqlite3")
        self.addCleanup(self.store.close)
        center = ControlCenter.__new__(ControlCenter)
        center.root, center.host, center.token, center.store = self.root, "127.0.0.1", "test-token", self.store
        center.frame_cache = ReviewFrameCache(self.root, self.root / "missing-ffmpeg.exe")
        center._stopping = threading.Event()
        center.scheduler = JobScheduler(self.root, self.store)  # never started
        center.recovered = 0
        self.center = center
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_class(center))
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.data = json.loads(node(R2_PAYLOADS_JS))
        source = self.root / "input" / self.SOURCE
        self.write_source(source)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        queue = self.data["queue"]
        queue["source"] = {"path": str(source), "sha256": digest, "duration_seconds": self.DURATION}
        queue["reports"] = []
        self.queue_file = self.root / "reports" / "jobs" / "r2-demo" / "review-queue.json"
        self.queue_file.write_text(json.dumps(queue), encoding="utf-8")
        stat = source.stat()
        job = self.store.upsert_job(job_key="r2-demo", source_path=source, source_sha256=digest, source_size_bytes=stat.st_size,
                                    source_mtime_ns=stat.st_mtime_ns, content_style="animation", state="WAITING_REVIEW")
        self.job_id = int(job["id"])
        self.store.update_job(self.job_id, active_queue_path="reports/jobs/r2-demo/review-queue.json", active_revision=1, progress=1.0)

    def post(self, kind, body):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=20)
        try:
            connection.request("POST", f"/api/jobs/{self.job_id}/review/{kind}", body=json.dumps(body).encode(),
                               headers={"Host": f"127.0.0.1:{self.server.server_address[1]}", "Content-Type": "application/json",
                                        "X-BiliFlow-Token": "test-token"})
            response = connection.getresponse()
            return response.status, json.loads(response.read() or b"{}")
        finally:
            connection.close()


@unittest.skipUnless(NODE, "node is required to build the dialog's payloads")
class ReviewR2Writes(_TempJobFixture):
    """The real route on a temporary root: the dialog's bodies are accepted and give the state the dialog shows."""

    def test_the_dialog_bodies_give_the_state_the_dialog_shows(self):
        sent = 0
        for index, step in enumerate(self.data["steps"]):
            with self.subTest(step=index, body=step["body"]):
                path = f"/api/jobs/{self.job_id}/review/{step['kind']}"
                self.assertTrue(any(p.fullmatch(path) for p in phone_access.PHONE_ALLOWED_POSTS), "an existing allowed route")
                allowed = {"id"} if step["kind"] == "clear" else {"id", "decision", "full_frame", "note", "remember_studio_logo", "remember_platform_logo"}
                self.assertLessEqual(set(step["body"]), allowed, "no extra field")
                for flag in ("remember_studio_logo", "remember_platform_logo"):
                    if flag in step["body"]:
                        self.assertIs(step["body"][flag], True, "memory flags only when true")
                before = self.queue_file.read_bytes()
                status, payload = self.post(step["kind"], step["body"])
                self.assertEqual(status, step["status"], payload)
                if status != 200:
                    self.assertIsInstance(payload.get("error"), str)
                    self.assertTrue(payload["error"])
                    self.assertEqual(self.queue_file.read_bytes(), before, "a refused write leaves the queue byte-identical")
                    continue
                sent += 1
                item = next(x for x in payload["items"] if x["id"] == step["item"]["id"])
                self.assertEqual({"id": item["id"], "decision": item["decision"], "region": item["decision_region_source_pixels"],
                                  "note": item["decision_note"]}, step["item"])
                self.assertEqual(payload["counts"], step["counts"])
                self.assertEqual(payload["status"], step["queue_status"])
                self.assertEqual(json.loads(self.queue_file.read_text(encoding="utf-8"))["items"], payload["items"], "the queue on disk is the one returned")
        self.assertEqual(sent, 12)
        self.assertIn(self.data["steps"][-1]["local_error"], ("Mục này chưa có vùng được định vị; hãy chọn Làm mờ cả cảnh.",),
                      "the dialog refuses it before sending, like the server")
        self.assertEqual(self.store.get_job(self.job_id)["state"], "WAITING_REVIEW")
        written = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())
        self.assertTrue(all(p.startswith(("reports/", "state/", "input/r2-demo.mp4")) for p in written), written)
        self.assertEqual(self.root.joinpath("input", "r2-demo.mp4").read_bytes(), b"synthetic" * 64, "the source is never touched")


R3_BULK_JS = r"""
const vm=require('vm'),fs=require('fs'),R=require('./dashboard_v2/review-core.js');
const ctx={window:{BFContracts:require('./dashboard_v2/contracts.js')},structuredClone};
vm.runInNewContext(fs.readFileSync('dashboard_v2/mock-data.js','utf8'),ctx);
const M=ctx.window.BFMock,j=M.create().jobs.find(x=>x.id===101),queue=M.reviewQueue(j,{count:40,advisory:3});
queue.items[4].category='text';queue.items[4].review_kind='logo_overlay';queue.items[4].decision=null;
queue.items[7].suggested_region_source_pixels=null;queue.items[7].decision=null;   // a BLUR suggestion without a region: accept skips it
const cases=[];
for(const f of R.FILTER_IDS)for(const kind of ['bulkKeep','bulkAccept']){const plan=R.bulkPlan(queue,f,kind);cases.push({filter:f,kind,filters:plan.filters||null,count:plan.count||0,error:plan.error||null,confirm:plan.confirm||null});}
process.stdout.write(JSON.stringify({queue,cases}));
"""


@unittest.skipUnless(NODE, "node is required to build the dialog's bulk plans")
class ReviewR3Bulk(_TempJobFixture):
    """S1 on the real route: the count in the dialog's confirm is the number of items the server changes."""

    def test_bulk_writes_change_the_count_the_confirm_shows(self):
        data = json.loads(node(R3_BULK_JS))
        queue = data["queue"]
        queue["source"] = json.loads(self.queue_file.read_text(encoding="utf-8"))["source"]
        queue["reports"] = []
        checked = 0
        for case in data["cases"]:
            with self.subTest(filter=case["filter"], kind=case["kind"]):
                if case["filters"] is None:
                    self.assertIn(case["error"], ("Bộ lọc này không hỗ trợ thao tác hàng loạt.", "Không có mục chưa duyệt trong bộ lọc này.",
                                                  "Không có đề xuất chưa duyệt trong bộ lọc này."))
                    continue
                self.queue_file.write_text(json.dumps(queue), encoding="utf-8")
                route = "bulk-keep" if case["kind"] == "bulkKeep" else "bulk-accept"
                changed = 0
                for value in case["filters"]:
                    status, payload = self.post(route, {"filter": value})
                    self.assertEqual(status, 200, payload)
                    changed += payload["audit_log"][-1]["changed_count"]
                self.assertEqual(changed, case["count"], case["confirm"])
                self.assertIn(f" {case['count']} ", case["confirm"])
                checked += 1
        self.assertGreaterEqual(checked, 12)
        # Visual AI and "Ứng viên phụ" are refused by the dialog; the server refuses them too.
        for value in ("visual_ai", "candidates", "ads"):
            status, payload = self.post("bulk-keep", {"filter": value})
            self.assertEqual(status, 400, value)
            self.assertEqual(payload["error"], "Bộ lọc hàng loạt không hợp lệ")
        self.assertEqual(self.root.joinpath("input", "r2-demo.mp4").read_bytes(), b"synthetic" * 64, "the source is never touched")


FFMPEG = shutil.which("ffmpeg") or next((str(p) for p in [ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"] if p.is_file()), None)


@unittest.skipUnless(NODE and FFMPEG, "node and ffmpeg are required for the phone listener check")
class ReviewR4PhoneListener(_TempJobFixture):
    """R4.2: the review dialog through the real phone listener (127.0.0.1, never the Wi-Fi address) on a temporary
    root: a synthetic VP8 clip made by ffmpeg, real frames, the cookie, at most 2 frames at once on the server, the
    clip with Range (206), every POST in PHONE_ALLOWED_POSTS (decision, clear, bulk-keep; never finalize), no request
    without the cookie after the code. The browser part is dashboard_v2/browser-check-review-phone.cjs (Playwright)."""

    SOURCE, DURATION = "r4-phone.webm", 70.0

    def write_source(self, source: Path) -> None:
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=70:size=320x180:rate=10",
                        "-c:v", "libvpx", "-b:v", "150k", "-g", "10", str(source)], check=True, timeout=180)

    def setUp(self):
        super().setUp()
        self.center.frame_cache = ReviewFrameCache(self.root, Path(FFMPEG))
        self.center.start_ai_audit = mock.Mock(side_effect=AssertionError("no AI audit"))
        self.center._audit_jobs, self.center._audit_lock = {}, threading.Lock()  # read by /api/status
        self.center.ai_status = mock.Mock(return_value={"ready": False, "message": "AI tắt trong test", "login_running": False,
                                                        "config": {"enabled": False, "model": "", "reasoning_effort": ""}})
        # Report previews served by /media from the temporary reports folder (synthetic posters of the demo).
        previews = self.root / "reports" / "jobs" / "r2-demo" / "previews"
        previews.mkdir(parents=True)
        queue = json.loads(self.queue_file.read_text(encoding="utf-8"))
        for item in queue["items"] + queue.get("advisory_items", []):
            moved = []
            for name in item.get("preview_images") or []:
                target = previews / Path(name).name
                if not target.exists():
                    shutil.copyfile(DASHBOARD_V2_DIR / "assets" / Path(name).name, target)
                moved.append(target.relative_to(self.root).as_posix())
            item["preview_images"] = moved
        self.queue_file.write_text(json.dumps(queue), encoding="utf-8")
        self.log: list[dict] = []
        self.lock = threading.Lock()
        self.frames_now = self.frames_max = 0
        test = self

        def factory(access):
            base = _phone_handler_class(self.center, access)

            class Logged(base):
                def send_response(self, code, message=None):
                    self._logged = code
                    super().send_response(code, message)

                def record(self, method, run):
                    path = urllib.parse.urlsplit(self.path).path
                    frame = path.endswith("/review/frame")
                    if frame:
                        with test.lock:
                            test.frames_now += 1
                            test.frames_max = max(test.frames_max, test.frames_now)
                    try:
                        run()
                    finally:
                        if frame:
                            with test.lock:
                                test.frames_now -= 1
                        with test.lock:
                            test.log.append({"method": method, "path": path, "range": self.headers.get("Range"),
                                             "cookie": phone_access.COOKIE_NAME + "=" in (self.headers.get("Cookie") or ""),
                                             "status": getattr(self, "_logged", None)})

                def do_GET(self):
                    self.record("GET", super().do_GET)

                def do_POST(self):
                    self.record("POST", super().do_POST)

            return Logged

        self.phone = _phone_access(self.center)
        self.phone._lan = lambda: FAKE_LAN  # never the real Wi-Fi
        self.addCleanup(self.phone.disable)
        self.status = self.phone.enable(factory, address=FAKE_LAN, check_address=lambda _a: None, check_port=lambda _p: None, port=0)

    def test_the_dialog_works_through_the_phone_listener(self):
        port = self.status["port"]
        env = {**os.environ, "BILIFLOW_PHONE_BASE": f"http://127.0.0.1:{port}", "BILIFLOW_PHONE_CODE": self.status["code"],
               "BILIFLOW_PHONE_JOB": str(self.job_id), "BILIFLOW_PHONE_POSTS": json.dumps([p.pattern for p in phone_access.PHONE_ALLOWED_POSTS])}
        completed = subprocess.run([NODE, str(DASHBOARD_V2_DIR / "browser-check-review-phone.cjs")], cwd=ROOT, env=env,
                                   capture_output=True, text=True, encoding="utf-8", timeout=600)
        if completed.stdout.startswith("SKIP"):
            self.skipTest(completed.stdout.strip())
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(result["passed"], 6, completed.stderr)
        with self.lock:
            log = list(self.log)
        # The cookie: before the code only the form; after it every request carries the cookie.
        login = next(i for i, r in enumerate(log) if r["method"] == "POST" and r["path"] == "/phone-login")
        self.assertTrue(all(r["status"] == 401 for r in log[:login]), log[:login])
        after = [r for r in log[login + 1:]]
        self.assertEqual([r for r in after if not r["cookie"] or r["status"] in (401, 403)], [], "every request after the code has the cookie")
        # Frames: real JPEGs from the synthetic clip, never more than 2 at once on the server.
        frames = [r for r in after if r["path"].endswith("/review/frame")]
        self.assertGreaterEqual(len(frames), 3)
        self.assertTrue(all(r["status"] == 200 for r in frames), frames)
        self.assertLessEqual(self.frames_max, 2)
        # The clip: Range requests answered 206.
        videos = [r for r in after if r["path"].endswith("/review/video")]
        self.assertTrue(any(r["range"] and r["status"] == 206 for r in videos), videos)
        # Writes: decision, clear (its undo), bulk-keep; each in PHONE_ALLOWED_POSTS, never finalize, all accepted.
        posts = [r for r in after if r["method"] == "POST"]
        kinds = [r["path"].rsplit("/", 1)[-1] for r in posts]
        self.assertEqual(kinds, ["decision", "clear", "bulk-keep"])
        for r in posts:
            self.assertEqual(r["status"], 200, r)
            self.assertTrue(any(p.fullmatch(r["path"]) for p in phone_access.PHONE_ALLOWED_POSTS), r["path"])
            self.assertIsNone(phone_access.pc_only_reason(r["path"]))
        queue = json.loads(self.queue_file.read_text(encoding="utf-8"))
        self.assertTrue(all(x.get("decision") for x in queue["items"]), "Giữ tất cả kept every undecided item")
        # Report previews through /media; nothing reaches a PC-only route; no error answer.
        self.assertTrue(any(urllib.parse.unquote(r["path"]).startswith("/media/reports/") and r["status"] == 200 for r in after))
        self.assertEqual([r for r in after if re.search(r"source-(cleanup|archive|recycle)|job-delete|/shutdown|logo-memory", r["path"])], [])
        self.assertEqual([r for r in after if r["status"] is None or r["status"] >= 400], [])
        source = self.root / "input" / self.SOURCE
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), queue["source"]["sha256"], "the source is never touched")

    def test_review_operations_are_phone_operations(self):
        data = json.loads(node("const C=require('./dashboard_v2/contracts.js');process.stdout.write(JSON.stringify({e:C.endpoints,pc:C.pcOnlyOps}))"))
        for name in ("decision", "clear", "bulkKeep", "bulkAccept", "finalize"):
            with self.subTest(op=name):
                method, path = data["e"][name]
                self.assertEqual(method, "POST")
                concrete = path.replace("{id}", str(self.job_id))
                self.assertTrue(any(p.fullmatch(concrete) for p in phone_access.PHONE_ALLOWED_POSTS), concrete)
                self.assertIsNone(phone_access.pc_only_reason(concrete))
                self.assertNotIn(name, data["pc"])
        for name in ("queue", "reviewSession", "evidence", "frame", "video", "resources", "reviewExport"):
            self.assertEqual(data["e"][name][0], "GET", name)


@unittest.skipUnless(NODE, "node is required to read contracts.js")
class ReviewR3ExportDialog(unittest.TestCase):
    """R3.3: the V2 export dialog (contracts.js + app.js exportModal) against export_dialog.py."""

    def test_options_limits_gate_and_sentence_match_export_dialog_py(self):
        script = (
            "const C=require('./dashboard_v2/contracts.js'),vm=require('vm'),box={};"
            "vm.createContext(box);vm.runInContext(process.argv[1]+';globalThis.out={exportSizeSelection,exportConfirmText,exportPolicyChoice,EXPORT_GATE_MESSAGE,EXPORT_SIZE_OPTIONS};',box);"
            "const P=box.out,rows=[];"
            "for(const [m,g] of [['default',''],['unlimited',''],['custom','2.5'],['custom','0.05'],['custom','1000'],['custom','0.04'],['custom','1000.1'],['custom',''],['custom','12.75']]){"
            "let a=null,b=null,ea=null,eb=null;try{a=P.exportSizeSelection(m,g);}catch(e){ea=e.message;}try{b=C.exportSelection(m,g);}catch(e){eb=e.message;}"
            "rows.push({m,g,ea,eb,body:b,ta:a&&P.exportConfirmText(a),tb:b&&C.exportConfirmText(b),keys:b&&Object.keys(b)});}"
            "const pol=[null,{mode:'custom',maximum_output_gb:7.5},{mode:'unlimited',maximum_output_gb:9},{mode:'x'}].map(p=>[P.exportPolicyChoice(p),C.exportPolicyChoice(p)]);"
            "process.stdout.write(JSON.stringify({rows,pol,options:C.EXPORT_SIZE_OPTIONS,gate:C.EXPORT_GATE_MESSAGE,attrs:C.EXPORT_CUSTOM_GB.attributes,value:C.EXPORT_CUSTOM_GB.value}))"
        )
        completed = subprocess.run([NODE, "-e", script, export_dialog.EXPORT_DIALOG_JS], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        data = json.loads(completed.stdout)
        self.assertEqual([tuple(x) for x in data["options"]], list(export_dialog.EXPORT_SIZE_OPTIONS))
        self.assertEqual(data["gate"], export_dialog.EXPORT_GATE_MESSAGE)
        self.assertEqual(data["attrs"], export_dialog.EXPORT_CUSTOM_GB_ATTRIBUTES)
        self.assertEqual(data["value"], export_dialog.EXPORT_CUSTOM_GB_DEFAULT)
        for row in data["rows"]:
            with self.subTest(mode=row["m"], gb=row["g"]):
                self.assertEqual(row["eb"], row["ea"], "same limits and refusal text")
                if row["body"]:
                    self.assertEqual(row["tb"], row["ta"], "same confirm sentence")
                    self.assertLessEqual(set(row["keys"]), {"size_mode", "max_output_gb"}, "the body stays {size_mode, max_output_gb?}")
        for classic, v2 in data["pol"]:
            self.assertEqual(v2, classic)
        app = (DASHBOARD_V2_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn("C.EXPORT_SIZE_OPTIONS.map(", app)
        self.assertIn("C.EXPORT_CUSTOM_GB.attributes", app)
        self.assertIn("C.exportConfirmText(C.exportSelection(", app)
        self.assertIn("mutate('finalize',j,selection)", app, "finalize only from the dialog's confirm button")


if __name__ == "__main__":
    unittest.main()
