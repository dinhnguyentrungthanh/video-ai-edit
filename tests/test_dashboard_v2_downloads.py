"""Dashboard V2 "Tải video" (live): the Node gate and the state sets the page shares with the backend.

verify-download.cjs checks download-core.js, download-view.js (remote text escaped, no local path)
and the adapter's download calls against a fake transport. Here: the buttons a page offers come from
the same state sets the worker uses, so the page never offers what the backend refuses (409).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from biliflow import control_center, download_probe, download_store, download_upkeep, download_worker

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
CORE_SETS = ("STATES", "STOPPABLE", "RESUMABLE", "CANCELLABLE", "RETRYABLE", "RENAMABLE", "FINAL", "RUNNING", "KEEPS_PART")


def _node(*args: str) -> subprocess.CompletedProcess:
    # Node writes UTF-8; without an explicit encoding Windows decodes it as cp1252.
    return subprocess.run([NODE, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120)


@unittest.skipUnless(NODE, "node is required for the Dashboard V2 gates")
class DownloadPageTests(unittest.TestCase):
    def test_the_node_gate_passes(self) -> None:
        completed = _node(str(ROOT / "dashboard_v2" / "verify-download.cjs"))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        summary = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(summary["failed"], 0)
        self.assertGreaterEqual(summary["passed"], 15)

    def test_the_state_sets_mirror_the_backend(self) -> None:
        script = ("const K=require('./dashboard_v2/download-core.js');"
                  f"console.log(JSON.stringify(Object.fromEntries({json.dumps(CORE_SETS)}.map(n=>[n,K[n]]))))")
        completed = _node("-e", script)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        core = {name: set(values) for name, values in json.loads(completed.stdout).items()}
        expected = {
            "STATES": set(download_store.STATES),
            "STOPPABLE": download_worker.STOPPABLE,
            "RESUMABLE": download_worker.RESUMABLE,
            "CANCELLABLE": download_worker.CANCELLABLE,
            "RETRYABLE": download_worker.RETRYABLE,
            "RENAMABLE": download_worker.RENAMABLE,
            "FINAL": download_store.FINAL_STATES,
            "RUNNING": download_store.SLOT_STATES,
            "KEEPS_PART": download_upkeep.TEMP_CLEANABLE,
        }
        for name in CORE_SETS:
            with self.subTest(set=name):
                self.assertEqual(core[name], set(expected[name]))

    def test_the_not_supported_badge_uses_the_codes_the_probe_gives(self) -> None:
        completed = _node("-e", "console.log(JSON.stringify(require('./dashboard_v2/download-core.js').UNSUPPORTED))")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        unsupported, _ = download_probe.classify_error("ERROR: Unsupported URL: https://x.example/", stage="probe")
        empty = download_probe.choose({"_type": "playlist", "extractor_key": "Generic", "entries": []}).code
        self.assertEqual(set(json.loads(completed.stdout)), {unsupported, empty})

    def test_live_page_serves_the_download_modules_and_the_demo_does_not_load_them(self) -> None:
        live = (ROOT / "dashboard_v2" / "live.html").read_text(encoding="utf-8")
        demo = (ROOT / "dashboard_v2" / "index.html").read_text(encoding="utf-8")
        for name in ("download-core.js", "download-view.js", "download-live.js"):
            with self.subTest(file=name):
                self.assertIn(name, control_center.DASHBOARD_V2_FILES)
                self.assertIn(f'<script defer src="{name}"></script>', live)
                self.assertNotIn(name, demo)
        self.assertLess(live.index("download-live.js"), live.index('src="app.js"'))
        self.assertEqual([name for name in control_center.DASHBOARD_V2_FILES if name.endswith(".cjs")], [])

    def test_no_backdrop_filter_rule_in_v2(self) -> None:
        """R4-B3: the review dialog flickered on the user's PC while any backdrop-filter was in use."""
        files = [*sorted((ROOT / "dashboard_v2").glob("*.css")), ROOT / "dashboard_v2" / "download-view.js",
                 ROOT / "dashboard_v2" / "download-live.js"]
        for path in files:
            with self.subTest(file=path.name):
                code = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
                self.assertIsNone(re.search(r"backdrop-filter\s*:", code))


if __name__ == "__main__":
    unittest.main()
