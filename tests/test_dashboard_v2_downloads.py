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

from biliflow import (control_center, download_episode_names, download_probe, download_store, download_upkeep,
                      download_worker)

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
CORE_SETS = ("STATES", "STOPPABLE", "RESUMABLE", "CANCELLABLE", "RETRYABLE", "RENAMABLE", "FINAL", "RUNNING", "KEEPS_PART")
# The gates' own tests (M6): every top-level test('…') a gate file declares must run and pass, by name and in order,
# and their number is pinned here, so a lost test cannot hide behind an added one (update the number with the change).
DOWNLOAD_GATE_TESTS = 22
ACCOUNT_GATE_TESTS = 55
GATE_TEST = re.compile(r"^test\('((?:[^'\\\n]|\\.)*)'", re.MULTILINE)
# download-fake-accounts.cjs (the M5 browser checks' server) in memory: a series page split into a group, then the
# names of every episode task (the seeded groups' and the new one's).
FAKE_NAMES_SCRIPT = """
const F = require('./dashboard_v2/download-fake-accounts.cjs');
let rows = [], next = 40;
const fake = F.create({task: (id, state, extra) => ({id, state, ...extra}), tasks: () => rows,
  push: row => { rows = [...rows, row]; }, removeTasks: ids => { rows = rows.filter(x => !ids.has(x.id)); },
  nextId: () => next++});
rows = fake.seed();
const page = fake.handleGet('/api/downloads/18/episodes')[1];
const picked = ['a-s1e2', 'a-s1e7', 'a-s1e10', 'a-s2e1', 'a-sp1', 'a-sp2'];
const [status, made] = fake.handlePost('/api/downloads/18/episodes/confirm', {idempotency_key: 'names-check-1',
  fingerprint: page.fingerprint, selection: {mode: 'pick', episodes: picked, variant_kind: 'k720-sub'}});
const groups = fake.snapshotExtras().groups;
console.log(JSON.stringify({status, made: made.group.id, listing: page.listing, picked, groups,
  tasks: rows.map(fake.decorate)}));
"""


def _node(*args: str) -> subprocess.CompletedProcess:
    # Node writes UTF-8; without an explicit encoding Windows decodes it as cp1252.
    return subprocess.run([NODE, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120)


def _declared_tests(gate: Path) -> list[str]:
    """The names of a gate's top-level test('…', …) declarations, in source order, as the gate prints them."""
    return [re.sub(r"\\(.)", r"\1", name) for name in GATE_TEST.findall(gate.read_text(encoding="utf-8"))]


@unittest.skipUnless(NODE, "node is required for the Dashboard V2 gates")
class DownloadPageTests(unittest.TestCase):
    def _assert_gate_passes(self, name: str, expected: int) -> None:
        """The gate exits 0, prints OK for each declared test in order and sums them up as passed, none failed; the
        file declares exactly `expected` tests."""
        gate = ROOT / "dashboard_v2" / name
        completed = _node(str(gate))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        lines = completed.stdout.strip().splitlines()
        declared = _declared_tests(gate)
        self.assertEqual([line[3:] for line in lines if line.startswith("OK ")], declared, "every declared test ran")
        self.assertEqual(json.loads(lines[-1]), {"passed": len(declared), "failed": 0})
        self.assertEqual(len(declared), expected, f"{name} declares {len(declared)} tests: update the number with them")

    def test_the_node_gate_passes(self) -> None:
        self._assert_gate_passes("verify-download.cjs", DOWNLOAD_GATE_TESTS)

    def test_the_source_account_gate_passes(self) -> None:
        """M5 (docs/SOURCE_ACCOUNTS_PLAN.md 9.17): account panel, episode dialog and groups. M6: STALE_PREVIEW,
        the phone's episode and group controls, "Tải các tập còn lại" keeping every check, the fake's ITEMS_EXIST,
        each part of a variant's label shown once, "Chọn tất cả" / "Bỏ chọn tất cả" (plan P3), no global pause on
        the PC, the phone or an unknown mode (P6), and markup from a source written as text where it was not checked."""
        self._assert_gate_passes("verify-download-accounts.cjs", ACCOUNT_GATE_TESTS)

    def test_the_fake_server_names_episodes_like_the_backend(self) -> None:
        """M6: the fake server of the M5 browser checks gives the names download_groups and the worker give: an
        ordinal of at least 3 digits, the code from the source's numbers, markup as spaces, the MKV kept."""
        completed = _node("-e", FAKE_NAMES_SCRIPT)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        fake = json.loads(completed.stdout)
        self.assertEqual(fake["status"], 200)
        seasons = {episode["key"]: season for season in fake["listing"]["groups"] for episode in season["episodes"]}
        picked = [episode for season in fake["listing"]["groups"] for episode in season["episodes"]
                  if episode["key"] in fake["picked"]]  # the group's order is the list's order
        groups = {group["id"]: group for group in fake["groups"]}
        named, finished = [], []
        for task in fake["tasks"]:
            place = task.get("group")
            if not place:
                continue
            group = groups[place["group_id"]]
            width = download_episode_names.ordinal_width(group["total"])
            film = task.get("desired_name") or group["title"]
            with self.subTest(task=task["id"]):
                if place["group_id"] == fake["made"]:
                    episode = picked[place["ordinal"] - 1]
                    season = seasons[episode["key"]]
                    self.assertEqual(place["code"], download_episode_names.episode_code(
                        season_number=None if season["special"] else season["number"], episode_number=episode["number"],
                        special=episode["special"], label=episode["label"]))
                self.assertEqual(place["planned_name"],
                                 download_episode_names.planned_stem(place["ordinal"], width, film, place["code"]))
                named.append(place["planned_name"])
                if task["state"] == "COMPLETED":
                    stem = download_episode_names.planned_stem(place["ordinal"], width, film, place["code"], ".mkv")
                    self.assertEqual(task["output_name"], f"{stem}.mkv")
                    finished.append(task["output_name"])
        self.assertEqual(len([name for name in named if name.endswith(("SP01", "SP02"))]), 2, named)
        self.assertGreaterEqual(len(named), 16)
        self.assertEqual(len(finished), 4)
        self.assertTrue(all(re.match(r"\d{3} - ", name) for name in named), named)
        self.assertFalse(any("<" in name or ">" in name for name in named + finished))

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
        for name in ("download-core.js", "download-view.js", "download-episodes.js", "download-live.js"):
            with self.subTest(file=name):
                self.assertIn(name, control_center.DASHBOARD_V2_FILES)
                self.assertIn(f'<script defer src="{name}"></script>', live)
                self.assertNotIn(name, demo)
        self.assertLess(live.index("download-live.js"), live.index('src="app.js"'))
        self.assertLess(live.index("download-episodes.js"), live.index("download-live.js"))
        self.assertEqual([name for name in control_center.DASHBOARD_V2_FILES if name.endswith(".cjs")], [])

    def test_no_backdrop_filter_rule_in_v2(self) -> None:
        """R4-B3: the review dialog flickered on the user's PC while any backdrop-filter was in use."""
        files = [*sorted((ROOT / "dashboard_v2").glob("*.css")), ROOT / "dashboard_v2" / "download-view.js",
                 ROOT / "dashboard_v2" / "download-live.js", ROOT / "dashboard_v2" / "download-episodes.js"]
        for path in files:
            with self.subTest(file=path.name):
                code = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
                self.assertIsNone(re.search(r"backdrop-filter\s*:", code))


if __name__ == "__main__":
    unittest.main()
