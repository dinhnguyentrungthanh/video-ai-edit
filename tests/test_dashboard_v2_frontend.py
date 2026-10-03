"""Dashboard V2 frontend gates (Node): prototype contracts and the ControlCenterAdapter.

No server, network, database or video: verify.cjs checks the presenter contracts and the
demo store, verify-adapter.cjs drives the adapter through a fake transport.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node is required for the Dashboard V2 gates")
class DashboardV2NodeGates(unittest.TestCase):
    def run_gate(self, script: str) -> dict:
        completed = subprocess.run([NODE, str(ROOT / "dashboard_v2" / script)], cwd=ROOT,
                                   capture_output=True, text=True, timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        summary = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(summary["failed"], 0)
        return summary

    def test_prototype_contracts(self) -> None:
        self.assertGreaterEqual(self.run_gate("verify.cjs")["passed"], 28)

    def test_adapter_against_a_fake_transport(self) -> None:
        self.assertGreaterEqual(self.run_gate("verify-adapter.cjs")["passed"], 15)

    def test_every_javascript_file_parses(self) -> None:
        for path in sorted((ROOT / "dashboard_v2").glob("*.*js")):
            with self.subTest(file=path.name):
                completed = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True, timeout=60)
                self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
