"""Dashboard V2 contract checks against the real Control Center (read-only, no server).

B2: every endpoint in dashboard_v2/contracts.js has a real route in control_center.py.
B3: every job state lands in exactly one V2 tab, the same one the old dashboard uses.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from biliflow import control_center, logo_memory_admin

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "dashboard_v2" / "contracts.js"
NODE = shutil.which("node")

# Every state the backend writes to jobs.state (job_store, scheduler, job_pipeline,
# control_center). A new state must be added here and mapped in contracts.js tab().
JOB_STATES = [
    "DISCOVERED", "NEEDS_METADATA", "QUEUED", "PREFLIGHT", "SCANNING_SAFETY", "SCANNING_TEXT",
    "SCANNING_LOGO", "LOCALIZING_REGIONS", "BUILDING_REVIEW", "AI_AUDITING", "WAITING_REVIEW",
    "READY_TO_EXPORT", "RENDERING", "VERIFYING", "PAUSED", "FAILED", "INTERRUPTED_RECOVERABLE",
    "CANCELLED", "COMPLETED", "SKIPPED",
]


def _node(script: str) -> str:
    return subprocess.run([NODE, "-e", script], cwd=ROOT, check=True, capture_output=True,
                          text=True, timeout=60).stdout


def _routes() -> tuple[list[str], list[str]]:
    """Route patterns of do_GET and do_POST (regexes) as written in control_center.py."""
    source = Path(control_center.__file__).read_text(encoding="utf-8")
    start_get, start_post = source.index("def do_GET"), source.index("def do_POST")
    end_post = source.index("return Handler", start_post)

    def patterns(block: str) -> list[str]:
        found = [m.group(1) for m in re.finditer(r're\.fullmatch\(r"([^"]+)", path\)', block)]
        found += [re.escape(m.group(1)) for m in re.finditer(r'path == "([^"]+)"', block)]
        found += [re.escape(m.group(1)) + ".+" for m in re.finditer(r'path\.startswith\("([^"]+)"\)', block)]
        return found

    get_routes = patterns(source[start_get:start_post])
    post_routes = patterns(source[start_post:end_post])
    get_routes += [re.escape(p) for p in (
        logo_memory_admin.PAGE_PATH, logo_memory_admin.API_LIST, logo_memory_admin.API_FRAME)]
    post_routes += [re.escape(p) for p in (logo_memory_admin.API_CLASS, logo_memory_admin.API_DELETE)]
    return get_routes, post_routes


@unittest.skipUnless(NODE, "node is required to read contracts.js")
class EndpointContractTests(unittest.TestCase):
    def test_every_contract_endpoint_has_a_real_route(self) -> None:
        endpoints = json.loads(_node(
            "console.log(JSON.stringify(require('./dashboard_v2/contracts.js').endpoints))"))
        get_routes, post_routes = _routes()
        self.assertGreater(len(get_routes), 8)
        self.assertGreater(len(post_routes), 8)
        missing = []
        for name, (method, template) in endpoints.items():
            concrete = template.replace("{id}", "7").replace("{path}", "reports/a.jpg")
            routes = get_routes if method == "GET" else post_routes
            if not any(re.fullmatch(pattern, concrete) for pattern in routes):
                missing.append(f"{name}: {method} {template}")
        self.assertEqual(missing, [])

    def test_contract_methods_match_the_route_table(self) -> None:
        endpoints = json.loads(_node(
            "console.log(JSON.stringify(require('./dashboard_v2/contracts.js').endpoints))"))
        get_routes, post_routes = _routes()
        wrong = []
        for name, (method, template) in endpoints.items():
            concrete = template.replace("{id}", "7").replace("{path}", "x")
            other = post_routes if method == "GET" else get_routes
            if any(re.fullmatch(pattern, concrete) for pattern in other) and \
                    template not in ("/api/source-archive/restore",):
                wrong.append(f"{name}: {method} {template} also matches the other method")
        self.assertEqual(wrong, [])

    def test_known_real_routes_are_not_forgotten_by_the_contract(self) -> None:
        """Informational guard: real routes V2 never calls are listed on purpose."""
        endpoints = json.loads(_node(
            "console.log(JSON.stringify(require('./dashboard_v2/contracts.js').endpoints))"))
        covered = {template for _, template in endpoints.values()}
        # /, /logo-memory page and POST-only helpers are not V2 data routes.
        self.assertIn("/api/jobs/{id}/review/finalize", covered)
        self.assertIn("/api/source-archive/restore", covered)
        self.assertNotIn("/api/source-cleanup/execute", covered)


@unittest.skipUnless(NODE, "node is required to read contracts.js")
class StateGroupTests(unittest.TestCase):
    def test_every_state_is_in_exactly_one_group_and_matches_the_old_dashboard(self) -> None:
        html = control_center._dashboard_html()
        scan = re.search(r"const SCAN_STATES=new Set\(\[[^\]]*\]\);", html)
        func = re.search(r"function jobTab\(j\)\{.*?\}\nfunction defaultJobTab", html, re.S)
        queue_kind = re.search(r"function queueKind\(j\)\{[^\n]*\}", html)
        self.assertTrue(scan and func and queue_kind)
        old_src = scan.group(0) + func.group(0).replace("\nfunction defaultJobTab", "") + queue_kind.group(0)
        cases = [{"state": s, "queue_kind": k} for s in JOB_STATES for k in (None, "scan", "export", "other")]
        script = (
            f"const C=require('./dashboard_v2/contracts.js');{old_src};"
            f"const cases={json.dumps(cases)};"
            "const out=cases.map(j=>({state:j.state,kind:j.queue_kind,v2:C.tab(j),old:jobTab(j),"
            "tabs:C.tabs.map(t=>t[0]).filter(t=>t!=='all')}));"
            "console.log(JSON.stringify(out));"
        )
        rows = json.loads(_node(script))
        for row in rows:
            self.assertIn(row["v2"], row["tabs"], row)
            self.assertEqual(row["v2"], row["old"], row)

    def test_unknown_queue_kind_is_waiting_not_scan_or_export(self) -> None:
        out = _node("const C=require('./dashboard_v2/contracts.js');"
                    "console.log(C.tab({state:'QUEUED',queue_kind:'other'}),C.tab({state:'QUEUED'}))")
        self.assertEqual(out.split(), ["waiting", "waiting"])

    def test_labels_cover_every_state(self) -> None:
        out = json.loads(_node(
            "console.log(JSON.stringify(Object.keys(require('./dashboard_v2/contracts.js').labels)))"))
        self.assertEqual([s for s in JOB_STATES if s not in out], [])


if __name__ == "__main__":
    unittest.main()
