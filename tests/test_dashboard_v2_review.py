"""V2 review dialog, batch R0 (docs/DASHBOARD_V2_REVIEW_PLAN.md section 7.1).

The new files are whitelisted and served on the PC listener and, with the access cookie, on the phone
listener; the CSP is unchanged, the new files have no blob:, inline script or fetch; the classic pages
stay byte-identical (D2); PHONE_ALLOWED_POSTS and the contract endpoints are unchanged; review-core.js
matches the classic page (verify-review.cjs) and, for S1, counts bulk actions like the real server.
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
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from biliflow import phone_access, review_workflow
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
)
# SHA-256 of JSON.stringify(contracts.js endpoints, keys sorted) at the start of R0 (50 endpoints).
ENDPOINTS_SHA256 = "06c1dd42737de31d040ae78dd9d31aaf00b8b320f861cb4a0bfff429b35f0c9a"


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


if __name__ == "__main__":
    unittest.main()
