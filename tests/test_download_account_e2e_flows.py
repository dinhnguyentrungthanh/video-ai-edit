"""Source accounts end to end, M6 wave 2 (docs/SOURCE_ACCOUNTS_PLAN.md, M6): expiry, scope, production defaults.

E4: a session that reaches its 3,600 s mark in the middle of an episode group. E5: an incomplete episode list, a
per-episode variant, the scope box and ITEMS_EXIST from the real page through the real route and worker. E7:
"Tải tất cả" of a complete series from the real page, with HTML bait in the film title and the variant label. E6:
the production constructors with no account source, and with the tracked example source (no reader, no verifier).
The harness (tests/account_e2e_fixtures.py) describes what is injected; the sign-in "window" is its headless
stand-in, never a visible window. RouteInventoryTest (no browser) keeps the production POST routes to a written
inventory without a global pause. Run and stop like tests/test_download_account_e2e.py:

    E:/DungChung/BiliFlow/.venv/Scripts/python.exe -m unittest tests.test_download_account_e2e_flows -v

Roots: ``<worktree>\\temp\\m6-e2e-*`` (two per E6 test), ``m6-dash-*``, ``m6-clips-*``; every listener binds
127.0.0.1:0. After a hard kill, clean up as the docstring of tests/test_download_account_e2e.py says.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
import unittest
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from unittest import mock

from biliflow import (
    download_account_api,
    download_account_http,
    download_account_runs,
    download_account_sources,
    download_api,
    job_purge,
)
from biliflow.control_center import _handler_class
from biliflow.download_account_api import AccountRuntime
from biliflow.download_account_config import read_account_config
from biliflow.download_account_login import LOGIN_VERIFIERS
from biliflow.download_account_pages import PAGE_READERS
from biliflow.download_account_tasks import LOGIN_RECHECK_SECONDS
from biliflow.download_account_vault import SessionVault
from biliflow.download_accounts import AccountManager
from biliflow.download_api import DownloadService
from biliflow.download_http import SafeHttp
from biliflow.download_runner import YtDlpRunner
from biliflow.download_sources import default_registry
from biliflow.download_store import DownloadStore
from biliflow.download_worker import DownloadWorker
from biliflow.job_store import JobStore
from tests.account_e2e_fixtures import (
    CLIPS,
    EDGE_SECONDS,
    POST_ALLOWED,
    REPO_ROOT,
    ROOT_PREFIX,
    SNIFF,
    E2EBase,
    E2EClock,
    GatedReply,
    RequestLog,
    check_registries,
    logged,
    make_temp,
    module_setup,
    remove_tree,
)
from tests.account_source_fixtures import FilmSite, series
from tests.source_fixtures import FFMPEG, FFPROBE, Reply
from tests.test_download_account_browser import (
    BETA_PORTAL,
    CANARY_SID,
    FILES,
    FIRST,
    PORTAL,
    TICKETS,
    cookie,
    edge_command_lines,
)
from tests.test_download_account_http import Resolver
from tests.test_download_account_login import Answers, FixtureVerifier
from tests.test_download_accounts import FakeProtector, GoodAcl
from tests.test_download_worker import FAKE, GB

MS = 1000
TTL_MARK = 3601  # one second past the 3,600 s fallback of a "ttl" source
ROTATED = f"{CANARY_SID}-rot"  # the session cookie the fixture's film pages rotate to (a secret too)
BETA_SID = f"{CANARY_SID}-beta"
LOGIN = '#dl-accounts [data-action="dl-account"][data-op="login"]'
BADGE = "#dl-account-state .badge"
PAGE = "pc-light"  # the harness's default dashboard page name
# E7: HTML bait in what the source names (the film's title, a variant's label), shown as text only (drawn as markup
# it would make an <img> and a CSP violation, both noted by DOM_WATCH).
TITLE_BAIT = 'Phim mồi <img src=x onerror="window.__bf=1">'
VARIANT_BAIT = '<img src=x onerror="window.__bf=2"> 1080p'
# Every POST route of the downloader and the account API, written out by hand: a new route must be added here on
# purpose. None of them pauses everything (plan 9.10 point 11: no global pause; the slots stay 1 to 3).
EXPECTED_POST_ROUTES = frozenset({
    r"/api/downloads", r"/api/downloads/settings", r"/api/downloads/cleanup-temp",
    r"/api/downloads/(\d+)/(rename|choose|stop|resume|cancel|retry|remove)",
    r"/api/downloads/(\d+)/episodes/(draft|confirm)",
    r"/api/downloads/groups/(\d+)/(stop|resume|cancel|retry|remove)",
    r"/api/download-accounts/([a-z0-9][a-z0-9-]{0,39})/(login|cancel-login|disconnect)",
})
PAUSE_PATHS = ("/api/downloads/pause", "/api/downloads/pause-all", "/api/downloads/resume-all",
               "/api/downloads/stop-all", "/api/downloads/settings/pause", "/api/downloads/groups/pause",
               "/api/download-accounts/pause", "/api/download-accounts/alpha/pause")
# One path of each POST the E2E pages and steps make (the harness's POST_ALLOWED): each is a production route.
ALLOWED_SAMPLES = ("/api/downloads", "/api/downloads/settings", "/api/downloads/7/choose", "/api/downloads/7/resume",
                   "/api/downloads/7/episodes/draft", "/api/downloads/7/episodes/confirm",
                   "/api/downloads/groups/7/stop", "/api/downloads/groups/7/resume",
                   "/api/download-accounts/alpha/login", "/api/download-accounts/alpha/cancel-login",
                   "/api/download-accounts/alpha/disconnect", "/api/download-accounts/demo-portal/login")


def setUpModule():
    module_setup()  # its resources are module cleanups (tests/account_e2e_fixtures.py)


class Recorder:
    """Stands in for one method of a live object (``mock.patch.object``): calls it and keeps when it was called
    and what it returned (or raised)."""

    def __init__(self, original: Callable[..., Any]):
        self.original = original
        self._lock = threading.Lock()
        self.calls: list[tuple[float, Any]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        at = time.monotonic()
        try:
            result = self.original(*args, **kwargs)
        except BaseException as error:
            with self._lock:
                self.calls.append((at, error))
            raise
        with self._lock:
            self.calls.append((at, result))
        return result

    def times(self) -> list[float]:
        with self._lock:
            return [at for at, _ in self.calls]

    def results(self) -> list[Any]:
        with self._lock:
            return [result for _, result in self.calls]


class FlowCase(E2EBase):
    """The helpers of the flows below (no tests)."""

    def record(self, target: Any, name: str) -> Recorder:
        recorder = Recorder(getattr(target, name))
        patcher = mock.patch.object(target, name, recorder)
        patcher.start()
        self.addCleanup(patcher.stop)
        return recorder

    def paste(self, page: Any, url: str) -> int:
        """Paste ``url`` into the page like the user (link, rights box, Thêm); the new task's id."""
        known = {task["id"] for task in self.store.list_tasks()}
        page.fill("#dl-urls", url)
        page.check("#dl-rights")
        page.click('[data-action="dl-add"]')
        return self.wait_until(lambda: next((task["id"] for task in self.store.list_tasks()
                                             if task["id"] not in known and task["url"] == url), None), 60,
                               f"the pasted link {url}")

    def open_episodes(self, page: Any, task_id: int, count: int) -> None:
        from playwright.sync_api import expect
        button = page.locator(f'[data-action="dl-op"][data-op="episodes"][data-id="{task_id}"]')
        expect(button).to_be_visible(timeout=60 * MS)
        button.click()
        expect(page.locator("#dl-ep-root .dl-ep-row")).to_have_count(count, timeout=60 * MS)

    def sent(self, path: str) -> list[dict]:
        """The bodies the page itself POSTed to ``path`` (its own request events)."""
        return [json.loads(body) for _name, method, url, body in self.page_events["requests"]
                if method == "POST" and url.endswith(path)]

    def answers(self, path: str) -> list[tuple[int, dict]]:
        return [(item["status"], json.loads(item["json"])) for item in self.log.find("POST", path)]

    def file_route(self, key: str, decide: Callable[[int, Reply, dict], Any]) -> list[dict]:
        """Wrap the fixture file ``/f/<key>``: every request is recorded; ``decide(n, reply, entry)`` answers the
        n-th transfer request (not the probe's sample read) whose token the site accepted."""
        path, seen_list = f"/f/{key}", []
        original = self.server.routes[path]

        def route(seen, number):
            reply = original(seen, number)
            entry = {"range": seen.headers.get("range", ""), "if_range": seen.headers.get("if-range"),
                     "token": (seen.query.get("token") or [""])[0], "at": time.monotonic()}
            seen_list.append(entry)
            if reply.status != 200 or SNIFF.fullmatch(entry["range"]):
                return reply
            entry["transfer"] = sum(1 for item in seen_list if "transfer" in item) + 1
            return decide(entry["transfer"], reply, entry)
        self.server.route(path, route)
        return seen_list


class DashboardFlowsTest(FlowCase):
    """E4 and E5 on the PC page (see each test's docstring)."""

    # ------------------------------------------------------------------------------------------------ E4
    def test_session_expiry_mid_group_waits_then_resumes_in_order_after_a_new_sign_in(self):
        """E4. Alpha signed in on the page at T0 (its film pages rotate the cookie); beta signed in at T0+1800
        with its page waiting for a choice. Three episodes: S01E01's transfer runs across the 3,600 s mark,
        S01E02's transfer is cut at half and its next request after the mark is refused, S01E03 waits for a slot.
        S01E02 waits for a sign-in with its part, S01E03 waits before any slot or page, S01E01 finishes; no loop,
        no page, no window; the session's time is never extended; beta is untouched. A sign-in whose window
        closes wakes nothing; the next one (generation 2) wakes both in order, and S01E02 continues its part."""
        from playwright.sync_api import expect

        title, keys = "Phim hết phiên", [f"s1e{n}--v1080" for n in range(1, 5)]
        site = self.install_site(film="E4", title=title, kind="series", pages=[series({"s1": [1, 2, 3, 4]})],
                                 files={key: CLIPS["a"] for key in keys}, rotate=ROTATED)
        rotations, leases = self.record(self.manager, "save_rotated"), self.record(self.manager, "session_for")
        resumes, wakes = self.record(self.worker, "_resume_waiting_logins"), self.record(self.runtime, "_wake")
        self.windows.use("edge", FixtureVerifier())
        page = self.pump = self.open_dashboard()
        badge, login = page.locator(BADGE), page.locator(LOGIN)

        # T0: the user's Đăng nhập; then the series page, read with the session (its cookie rotates).
        expect(badge).to_have_text("Chưa đăng nhập", timeout=60 * MS)
        login.click()
        expect(badge).to_have_text("Đã kết nối", timeout=EDGE_SECONDS * MS)
        t0 = self.manager.status("alpha")
        self.assertEqual((t0["authenticated_at"], self.manager.session_gate("alpha")),
                         (self.clock.start.isoformat(), (True, None, 1)))
        pid = self.paste(page, site.url)
        self.wait_state(pid, "NEEDS_CHOICE", timeout=EDGE_SECONDS)
        self.assertIn(True, rotations.results())  # the rotated cookie was saved under generation 1

        # T0+1800: beta signed in (the manager's own steps) and its page waiting for a choice.
        self.clock.at(1800)
        beta_site = self.serve_site(FilmSite(film="B1", title="Phim beta", kind="series",
                                             pages=[series({"b1": [1, 2]})], session_value=BETA_SID, ads=False),
                                    BETA_PORTAL, BETA_PORTAL, BETA_PORTAL)
        self.secrets.update({ROTATED, BETA_SID})
        self.manager.complete_login(self.manager.begin_login("beta"),
                                    {"cookies": [cookie("sid", BETA_SID, BETA_PORTAL)], "origins": []})
        beta_status = self.manager.status("beta")
        bid = self.paste(page, beta_site.url)
        self.wait_state(bid, "NEEDS_CHOICE", timeout=EDGE_SECONDS)
        self.wait_event(bid, "NEEDS_CHOICE")
        beta_events, beta_reads = self.kinds(bid), self.server.count("/film/B1")

        # The file routes: S01E01 held in its transfer; S01E02 cut at half once S01E01 is held, then refused
        # after the clock passes the mark; S01E03 only recorded.
        half = len(CLIPS["a"]) // 2
        held, release, cut = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        mark: dict[str, Any] = {}

        def first(number, reply, entry):
            return GatedReply(reply, release, held) if number == 1 else reply

        def second(number, reply, entry):
            if number == 1:
                return GatedReply(replace(reply, cut_after=half), held, cut, chunk=64)
            if number == 2:
                self.clock.at(TTL_MARK)
                mark.update(held=held.is_set(), released=release.is_set(), token=entry["token"],
                            requests=len(self.server.requests), pages=self.server.count("/film/E4"))
                return Reply(b"expired", 403, "text/plain")
            return reply
        seen = {keys[0]: self.file_route(keys[0], first), keys[1]: self.file_route(keys[1], second),
                keys[2]: self.file_route(keys[2], lambda number, reply, entry: reply)}

        # Three episodes, one file each, through Chọn tập.
        self.assertEqual(self.worker.slots(), 2)
        self.open_episodes(page, pid, 4)
        page.check('input[name="dl-ep-mode"][value="pick"]')
        for index in range(3):
            page.check(f'input[data-ep-pick="{index}"]')
        confirm = page.locator("#dl-ep-confirm")
        expect(confirm).to_have_text("Tải 3 tập", timeout=60 * MS)
        expect(confirm).to_be_enabled(timeout=60 * MS)
        confirm.click()
        expect(page.locator("dialog#dl-episodes")).to_be_hidden(timeout=60 * MS)
        gid = self.worker.groups.summaries()[0]["id"]
        members = self.worker.group_detail(gid)["members"]
        ids = [member["task_id"] for member in members]
        self.assertEqual([member["code"] for member in members], ["S01E01", "S01E02", "S01E03"])
        places = {task_id: (task["group_id"], task["member_id"], task["queued_at"])
                  for task_id in ids for task in [self.store.get(task_id)]}

        # The mark: S01E02 and S01E03 wait for a sign-in; S01E01 is still transferring.
        waiting = ("WAITING_LOGIN", "SOURCE_LOGIN_REQUIRED", "alpha", "SESSION_EXPIRED", 1)

        def why(task_id):
            task = self.store.get(task_id)
            return (task["state"], task["error_code"], task["login_source"], task["login_reason"],
                    task["login_generation"])
        self.wait_until(lambda: all(why(task_id) == waiting for task_id in ids[1:]), 2 * EDGE_SECONDS,
                        "S01E02 and S01E03 waiting for a sign-in")
        self.assertEqual((mark["held"], mark["released"]), (True, False))  # S01E01 was mid-transfer at the mark
        self.assertEqual(self.store.get(ids[0])["state"], "DOWNLOADING")
        self.wait_until(lambda: self.worker.running_ids() == {ids[0]}, 30, "only S01E01 holding a slot")
        part = self.root / "temp" / "downloads" / str(ids[1]) / "media.part"
        kept = self.check("kept_part", part.stat().st_size)
        self.assertEqual(kept, half)  # S01E02's part stays for the next sign-in
        e3 = self.store.get(ids[2])
        self.assertIsNone(e3["temp_dir"])  # parked before a slot: never probed
        self.assertEqual((seen[keys[2]], site.loads.get(keys[2])), ([], None))
        self.assertEqual(self.server.count("/film/E4"), mark["pages"])  # no film page after the mark

        # Waiting: no loop, no hidden run, no window, no request, over five polls; the session's time is not
        # extended by the rotation saves, the reads or the polls.
        started, requests = time.monotonic(), len(self.server.requests)
        lease_calls, resume_calls = len(leases.times()), len(resumes.times())
        self.wait_polls(PAGE, 5)
        window = time.monotonic() - started
        self.assertEqual([self.kinds(task_id).count("WAITING_LOGIN") for task_id in ids[1:]], [1, 1])
        self.assertEqual((len(self.server.requests), len(leases.times())), (requests, lease_calls))
        self.assertLessEqual(len(resumes.times()) - resume_calls, window / LOGIN_RECHECK_SECONDS + 1)
        self.assertEqual(self.windows.calls, ["edge"])
        self.assertEqual([why(task_id) for task_id in ids[1:]], [waiting, waiting])
        self.assertEqual(edge_command_lines(str(self.root)), [])  # no hidden browser of this root while they wait
        status = self.manager.status("alpha")
        self.assertEqual((status["state"], status["error_code"], status["authenticated_at"], status["recheck_at"]),
                         ("NEEDS_LOGIN", "SESSION_EXPIRED", t0["authenticated_at"], t0["recheck_at"]))
        shown = next(item for item in self.api("GET", "/api/downloads")[1]["accounts"]["sources"]
                     if item["id"] == "alpha")
        self.assertEqual((shown["authenticated_at"], shown["recheck_at"], shown["waiting_tasks"]),
                         (t0["authenticated_at"], t0["recheck_at"], 2))
        expect(badge).to_have_text("Hết phiên", timeout=30 * MS)
        expect(page.locator("#dl-account-state .dl-account-waiting")).to_have_text("2 lượt chờ đăng nhập")
        expect(login).to_have_text("Đăng nhập lại")
        self.assertEqual(self.manager.session_gate("beta"), (True, None, 1))  # the other source is usable
        self.assertEqual(self.manager.status("beta"), beta_status)
        self.keep_page_text(page)
        self.shot(page, "pc-light-expired")

        # The running transfer ends past the mark without any new request.
        release.set()
        self.wait_state(ids[0], "COMPLETED", timeout=EDGE_SECONDS)
        self.assertEqual(len(self.server.requests), mark["requests"])

        # Đăng nhập lại, window closed: the callback runs, the next pass wakes nothing (generation 1 failed).
        self.windows.use("fake", Answers(), closes_after=1)
        noted = len(self.dom_notes)
        login.click()
        self.wait_until(lambda: (self.runtime.last_login.get("alpha") or {}).get("code") == "LOGIN_WINDOW_CLOSED",
                        60, "the closed window's outcome")
        woke = self.wait_until(lambda: [at for at in wakes.times() if at > started], 30, "the callback's wake")[-1]
        self.wait_until(lambda: any(at > woke for at in resumes.times()), 60, "a dispatch pass after the callback")
        self.assertEqual([why(task_id) for task_id in ids[1:]], [waiting, waiting])
        self.assertEqual(self.manager.session_gate("alpha"), (False, "SESSION_EXPIRED", 1))
        expect(badge).to_have_text("Hết phiên", timeout=30 * MS)
        self.look(page)  # the watch notes the badge shown now: the notes since the press are never empty
        badges = self.check("badges_window_closed", self.notes(PAGE, "badge", noted))
        self.assertNotIn("Đã kết nối", badges)  # not even for a moment
        self.assertEqual(badges[-1:], ["Hết phiên"])
        self.assertEqual(len(self.server.requests), mark["requests"])

        # Đăng nhập lại with the source's evidence: generation 2 wakes both, in order, in their places.
        self.windows.use("edge", FixtureVerifier())
        login.click()
        expect(badge).to_have_text("Đã kết nối", timeout=EDGE_SECONDS * MS)
        self.assertEqual(self.manager.session_gate("alpha"), (True, None, 2))
        self.assertEqual(self.manager.status("alpha")["authenticated_at"], self.clock.now.isoformat())
        self.assertEqual(self.windows.calls, ["edge", "fake", "edge"])
        self.assertEqual([item["status"] for item in self.log.find("POST", "/api/download-accounts/alpha/login")],
                         [202, 202, 202])
        for task_id in ids:
            self.wait_state(task_id, "COMPLETED", timeout=2 * EDGE_SECONDS)
        resumed = [next(event["id"] for event in self.store.events(task_id) if event["kind"] == "LOGIN_RESUMED")
                   for task_id in ids[1:]]
        self.assertLess(resumed[0], resumed[1])  # group order
        self.assertNotIn("LOGIN_RESUMED", self.kinds(ids[0]))
        self.assertEqual({task_id: (task["group_id"], task["member_id"], task["queued_at"])
                          for task_id in ids for task in [self.store.get(task_id)]}, places)
        ordinals = [(member["ordinal"], member["task_id"]) for member in self.worker.group_detail(gid)["members"]]
        self.assertEqual(ordinals, [(1, ids[0]), (2, ids[1]), (3, ids[2])])

        # S01E02 continued its part from a fresh ticket of the same file version.
        after = [entry for entry in seen[keys[1]] if entry["at"] > woke]
        transfers = [entry for entry in after if not SNIFF.fullmatch(entry["range"])]
        self.check("e2_after_sign_in", [(entry["range"], entry["if_range"]) for entry in after])
        self.assertEqual((transfers[0]["range"], transfers[0]["if_range"]), (f"bytes={kept}-", f'"{keys[1]}-1"'))
        self.assertNotEqual(transfers[0]["token"], mark["token"])
        self.assertIn(f"Tải nối từ {kept} byte đã có.", self.store.log_lines(ids[1]))
        names = [f"{ordinal:03d} - {title} - S01E0{ordinal}.mkv" for ordinal in (1, 2, 3)]
        self.assertEqual(self.check("input", self.input_names()), names)
        for name in names:
            self.assertEqual((self.root / "input" / name).read_bytes(), CLIPS["a"])
        # One ticket per probe, which serves the transfer after it (download_account_tickets); S01E02 one more after
        # the sign-in (its kept link went when it waited for the sign-in).
        self.assertEqual(self.check("ticket_loads", dict(site.loads)), {keys[0]: 1, keys[1]: 2, keys[2]: 1})

        # Beta was never touched: its page, its session, its time.
        self.assertEqual((self.store.get(bid)["state"], self.kinds(bid), self.server.count("/film/B1")),
                         ("NEEDS_CHOICE", beta_events, beta_reads))
        self.assertEqual(self.manager.status("beta"), beta_status)
        self.assertEqual(self.manager.session_gate("beta"), (True, None, 1))
        self.keep_page_text(page)
        self.shot(page, "pc-light-resumed")

        self.assert_no_scan_and_no_yt_dlp()
        self.secret_scan()
        self.assertTrue(self.server.connections)
        self.assert_network_kept((PORTAL, TICKETS, FILES, BETA_PORTAL), looked_up=(PORTAL, TICKETS, BETA_PORTAL),
                                 exact=True)
        self.assert_routes_kept()
        self.assert_page_clean()
        self.results["completed"] = True

    # ------------------------------------------------------------------------------------------------ E5
    def test_an_incomplete_list_and_a_repeated_page_offer_only_what_is_new(self):
        """E5. A series page whose list was not read whole (a "load more" button): the dialog says so, "Tải 1
        tập đã thấy" stays off until the scope box is ticked and the server refuses a confirm without it; the
        episode's own 720p file lands. The page pasted again with that episode and a new one: ITEMS_EXIST, and
        "Tải các tập còn lại" makes a group of the new one only. Pasted a third time with only that episode:
        ITEMS_EXIST once, no "Tải các tập còn lại", nothing more is sent."""
        from playwright.sync_api import expect

        title = "Phim chưa đủ tập"
        variants = (("v1080", "1080p", "Vietsub"), ("v720", "720p", "Vietsub"))
        items = series({"s1": [1, 2, 3]}, variants=variants)
        site = self.install_site(film="F2", title=title, kind="series", pages=[items], more_button=True,
                                 files={f"{item.episode}--{item.variant}": CLIPS["a"] if item.variant == "v1080"
                                        else CLIPS["b"] for item in items}, session_value=CANARY_SID)
        self.connect_alpha()
        page = self.pump = self.open_dashboard()
        confirm, why = page.locator("#dl-ep-confirm"), page.locator("#dl-ep-why")
        scope_note = "Đánh dấu ô xác nhận: danh sách chưa đầy đủ."

        # 1) The incomplete list, a per-episode 720p, the scope box.
        pid = self.paste(page, site.url)
        self.wait_state(pid, "NEEDS_CHOICE", timeout=EDGE_SECONDS)
        status, listing = self.api("GET", f"/api/downloads/{pid}/episodes")
        self.assertEqual((status, listing["listing"]["complete"]), (200, False))
        first = listing["listing"]["groups"][0]["episodes"][0]
        kinds = [item["kind"] for item in listing["listing"]["variant_kinds"]]
        self.open_episodes(page, pid, 3)
        expect(page.locator(".dl-ep-incomplete")).to_contain_text("Danh sách chưa đầy đủ")
        page.check('input[name="dl-ep-mode"][value="pick"]')
        page.check('input[data-ep-pick="0"]')
        page.check('input[name="dl-ep-how"][value="each"]')
        page.locator('select[data-ep-variant="0"]').select_option(
            str([item["id"] for item in first["variants"]].index("v720")))
        expect(confirm).to_have_text("Tải 1 tập đã thấy", timeout=60 * MS)
        expect(why).to_have_text(scope_note, timeout=60 * MS)
        expect(confirm).to_be_disabled()
        draft = self.worker.episodes(pid)["draft"]
        self.assertEqual((draft["episodes"], draft["variants"]), (["s1e1"], {"s1e1": "v720"}))
        path = f"/api/downloads/{pid}/episodes/confirm"
        forced = self.page_post(page, path, json.dumps({
            "selection": {"mode": "pick", "episodes": ["s1e1"], "variants": {"s1e1": "v720"}},
            "fingerprint": listing["fingerprint"], "idempotency_key": "m6-e5-no-scope"}))
        self.assertEqual(forced, [400, "SCOPE_NOT_CONFIRMED"])  # the contract's 400 (plan, confirm errors)
        refused = self.answers(path)[0][1]
        self.assertEqual((refused["count"], refused["confirm_label"]), (1, "Tải 1 tập đã thấy"))
        self.assertEqual((self.worker.groups.summaries(), self.store.get(pid)["state"]), ([], "NEEDS_CHOICE"))
        self.shot(page, "pc-light-scope", full_page=False)
        page.check("#dl-ep-scope")
        expect(confirm).to_be_enabled(timeout=30 * MS)
        confirm.click()
        expect(page.locator("dialog#dl-episodes")).to_be_hidden(timeout=60 * MS)
        self.assertEqual([status for status, _ in self.answers(path)], [400, 200])
        self.assertEqual([(body.get("confirm_scope"), body.get("skip_existing")) for body in self.sent(path)
                          if body.get("idempotency_key") != "m6-e5-no-scope"], [(True, None)])
        gid = self.answers(path)[1][1]["group"]["id"]
        (member,) = self.worker.group_detail(gid)["members"]
        self.wait_state(member["task_id"], "COMPLETED", timeout=2 * EDGE_SECONDS)
        name = f"001 - {title} - S01E01.mkv"
        self.assertEqual((self.root / "input" / name).read_bytes(), CLIPS["b"])
        self.assertEqual(dict(site.loads), {"s1e1--v720": 1})  # the probe's ticket serves the transfer

        # 2) Pasted again: S01E01 and S01E02 in 720p; S01E01 is listed; the rest is one request.
        pid2 = self.paste(page, site.url)
        self.wait_state(pid2, "NEEDS_CHOICE", timeout=EDGE_SECONDS)
        self.open_episodes(page, pid2, 3)
        page.check('input[name="dl-ep-mode"][value="pick"]')
        page.check('input[data-ep-pick="0"]')
        page.check('input[data-ep-pick="1"]')
        page.check('input[name="dl-ep-how"][value="kind"]')
        page.locator("#dl-ep-kind").select_option(str(kinds.index(next(kind for kind in kinds
                                                                      if kind.startswith("720p")))))
        expect(confirm).to_have_text("Tải 2 tập đã thấy", timeout=60 * MS)
        page.check("#dl-ep-scope")
        expect(confirm).to_be_enabled(timeout=30 * MS)
        confirm.click()
        existing = page.locator(".dl-ep-existing")
        expect(existing).to_contain_text("1 tập đã có trong danh sách tải", timeout=60 * MS)
        expect(existing).to_contain_text("Tập 1")
        path2 = f"/api/downloads/{pid2}/episodes/confirm"
        (refused,) = self.answers(path2)
        self.assertEqual((refused[0], refused[1]["code"], [item["episode"] for item in refused[1]["existing"]]),
                         (409, "ITEMS_EXIST", ["s1e1"]))
        rest = page.locator('[data-action="dl-ep-rest"]')
        expect(rest).to_be_enabled()
        existing.scroll_into_view_if_needed()
        self.shot(page, "pc-light-items-exist", full_page=False)
        rest.click()
        expect(page.locator("dialog#dl-episodes")).to_be_hidden(timeout=60 * MS)
        self.assertEqual([status for status, _ in self.answers(path2)], [409, 200])
        bodies = self.sent(path2)
        self.assertEqual([(body["confirm_scope"], body.get("skip_existing")) for body in bodies],
                         [(True, None), (True, True)])
        made = self.answers(path2)[1][1]
        self.assertEqual(([item["episode"] for item in made["existing"]], made["group"]["total"]), (["s1e1"], 1))
        (member2,) = self.worker.group_detail(made["group"]["id"])["members"]
        self.assertEqual(member2["code"], "S01E02")
        self.wait_state(member2["task_id"], "COMPLETED", timeout=2 * EDGE_SECONDS)
        self.assertEqual((self.root / "input" / f"001 - {title} - S01E02.mkv").read_bytes(), CLIPS["b"])

        # 3) Pasted a third time with only S01E01: one refusal, no "Tải các tập còn lại", nothing more.
        pid3 = self.paste(page, site.url)
        self.wait_state(pid3, "NEEDS_CHOICE", timeout=EDGE_SECONDS)
        self.open_episodes(page, pid3, 3)
        page.check('input[name="dl-ep-mode"][value="pick"]')
        page.check('input[data-ep-pick="0"]')
        page.check('input[name="dl-ep-how"][value="kind"]')
        page.locator("#dl-ep-kind").select_option(str(kinds.index(next(kind for kind in kinds
                                                                      if kind.startswith("720p")))))
        expect(confirm).to_have_text("Tải 1 tập đã thấy", timeout=60 * MS)
        page.check("#dl-ep-scope")
        expect(confirm).to_be_enabled(timeout=30 * MS)
        confirm.click()
        expect(existing).to_contain_text("Mọi tập đã chọn đều đã có", timeout=60 * MS)
        path3 = f"/api/downloads/{pid3}/episodes/confirm"
        posts = len(self.log.find("POST"))
        self.wait_polls(PAGE, 2)
        expect(page.locator('[data-action="dl-ep-rest"]')).to_have_count(0)
        self.assertEqual(len(self.log.find("POST")), posts)
        self.assertEqual([(status, body["code"]) for status, body in self.answers(path3)], [(409, "ITEMS_EXIST")])
        self.assertEqual(len(self.worker.groups.summaries()), 2)
        self.keep_page_text(page)
        existing.scroll_into_view_if_needed()
        self.shot(page, "pc-light-all-exist", full_page=False)

        # Only the chosen 720p files were asked for (one file per episode).
        self.assertEqual(self.check("ticket_loads", dict(site.loads)), {"s1e1--v720": 1, "s1e2--v720": 1})
        self.assertEqual(sorted({item.path for item in self.server.requests if item.path.startswith("/f/")}),
                         ["/f/s1e1--v720", "/f/s1e2--v720"])
        self.assertEqual(self.input_names(), sorted([name, f"001 - {title} - S01E02.mkv"]))
        self.assert_no_scan_and_no_yt_dlp()
        self.secret_scan()
        self.assert_network_kept(exact=True)
        self.assert_routes_kept()
        # Console errors only for the refusals the steps asked for: SCOPE_NOT_CONFIRMED (400) and ITEMS_EXIST (409).
        self.assert_page_clean(rf"status of 400 \(Bad Request\) @ {path}$",
                               rf"status of 409 \(Conflict\) @ /api/downloads/({pid2}|{pid3})/episodes/confirm$")
        self.results["completed"] = True

    # ------------------------------------------------------------------------------------------------ E7
    def test_all_episodes_of_a_complete_series_make_one_group_and_html_bait_stays_text(self):
        """E7 (plan 9.10 points 4-5, 9.17; R80). A complete series of three episodes whose film title and variant
        label carry HTML bait, alpha connected. The page task's row and Chọn tập's title show the film's title
        as text, every row the variant label; in "Chọn tập", Chọn tất cả ticks every episode and Bỏ chọn tất cả
        clears them (nothing can then be confirmed); "Tải tất cả các tập đang có" then confirms "Tải 3 tập" with
        one POST of mode "all", and one group of the three episodes lands in input in order. The group card and its
        member rows show the bait as text; no page held an <img> other than the dashboard's own assets and no
        Content Security Policy violation happened at any moment (DOM_WATCH). The dashboard's CSP would block the
        bait's inline handler even if the bait were drawn, so these notes, not the handler, show the escaping."""
        from playwright.sync_api import expect

        items = [replace(item, variant_label=VARIANT_BAIT) for item in series({"s1": [1, 2, 3]})]
        site = self.install_site(film="F15", title=TITLE_BAIT, kind="series", pages=[items],
                                 files={f"{item.episode}--{item.variant}": CLIPS["b"] for item in items},
                                 session_value=CANARY_SID)
        self.connect_alpha()
        page = self.pump = self.open_dashboard()
        pid = self.paste(page, site.url)
        self.wait_state(pid, "NEEDS_CHOICE", timeout=EDGE_SECONDS)
        row = page.locator(f'article.download-item[data-download-id="{pid}"]')
        expect(row).to_contain_text(f"Phim: {TITLE_BAIT}", timeout=60 * MS)

        # Chọn tập: the film's title and the variant labels as text; Chọn tất cả, then Bỏ chọn tất cả.
        self.open_episodes(page, pid, 3)
        expect(page.locator("#dl-ep-title")).to_have_text(f"Chọn tập: {TITLE_BAIT}")
        shown = self.check("variant_rows", page.locator("#dl-ep-root .dl-ep-row small").all_inner_texts())
        self.assertEqual([text.split(" · ", 1)[0] for text in shown], [VARIANT_BAIT] * 3)  # then quality · size
        confirm, why = page.locator("#dl-ep-confirm"), page.locator("#dl-ep-why")
        picks = page.locator("#dl-ep-root input[data-ep-pick]:checked")
        page.check('input[name="dl-ep-mode"][value="pick"]')
        page.click('[data-action="dl-ep-all"]')
        expect(picks).to_have_count(3)
        expect(confirm).to_have_text("Tải 3 tập", timeout=60 * MS)
        expect(confirm).to_be_enabled(timeout=60 * MS)
        page.click('[data-action="dl-ep-none"]')
        expect(picks).to_have_count(0)
        self.wait_until(lambda: (draft := self.worker.episodes(pid)["draft"] or {}).get("mode") == "pick"
                        and draft.get("episodes") == [], 60, "the empty pick saved")
        expect(why).not_to_contain_text("Đang lưu", timeout=60 * MS)  # the page has the empty pick's answer
        expect(confirm).to_be_disabled()
        self.check("why_nothing_picked", why.inner_text())
        page.check('input[name="dl-ep-mode"][value="all"]')
        expect(page.locator('input[name="dl-ep-mode"][value="all"] + span')).to_have_text(
            "Tải tất cả các tập đang có (3 tập)")
        self.wait_until(lambda: (self.worker.episodes(pid)["draft"] or {}).get("mode") == "all", 60,
                        "the whole list saved as the draft")
        expect(confirm).to_have_text("Tải 3 tập", timeout=60 * MS)
        expect(confirm).to_be_enabled(timeout=60 * MS)
        expect(page.locator("#dl-ep-root img")).to_have_count(0)
        self.keep_page_text(page)
        self.shot(page, "pc-light-all", full_page=False)

        # Tải 3 tập: one POST of mode "all", one group of the three episodes in order.
        path = f"/api/downloads/{pid}/episodes/confirm"
        confirm.click()
        expect(page.locator("dialog#dl-episodes")).to_be_hidden(timeout=60 * MS)
        ((status, answer),) = self.answers(path)
        self.assertEqual((status, answer["replay"], answer["group"]["total"]), (200, False, 3))
        (body,) = self.sent(path)
        self.assertEqual(body["selection"]["mode"], "all")
        self.assertNotIn("episodes", body["selection"])
        gid = answer["group"]["id"]
        members = self.worker.group_detail(gid)["members"]
        self.assertEqual([(member["ordinal"], member["code"], member["variant_label"]) for member in members],
                         [(ordinal, f"S01E0{ordinal}", VARIANT_BAIT) for ordinal in (1, 2, 3)])
        ids = [member["task_id"] for member in members]
        for task_id in ids:
            self.wait_state(task_id, "COMPLETED", timeout=2 * EDGE_SECONDS)
        names = [Path(self.store.get(task_id)["output_path"]).name for task_id in ids]
        self.assertEqual(self.check("input", self.input_names()), names)  # sorted by name = the group's order
        for ordinal, name in enumerate(names, 1):
            self.assertTrue(name.startswith(f"{ordinal:03d} - ") and name.endswith(f" - S01E0{ordinal}.mkv"), name)
            self.assertEqual(set(name) & set('<>"'), set(), name)  # the title's markup never reaches a file name
            self.assertEqual((self.root / "input" / name).read_bytes(), CLIPS["b"])
        self.assertEqual(self.check("ticket_loads", dict(site.loads)), {f"s1e{n}--v1080": 1 for n in (1, 2, 3)})

        # The group card, its member rows and the episodes' rows: the bait as text, no <img>.
        card = page.locator(f"#dl-group-{gid}")
        expect(card).to_contain_text("Đã xong 3/3 tập", timeout=60 * MS)
        expect(card.locator(f"#dl-group-title-{gid}")).to_have_text(TITLE_BAIT)
        card.locator(f'[data-group-members="{gid}"] summary').click()
        member_rows = card.locator(".dl-members li")
        expect(member_rows).to_have_count(3, timeout=60 * MS)
        for ordinal, (task_id, name) in enumerate(zip(ids, names), 1):
            expect(member_rows.nth(ordinal - 1).locator(".dl-member-name")).to_contain_text(
                f"S01E0{ordinal} · Tập {ordinal} · {VARIANT_BAIT}")
            item = page.locator(f'article.download-item[data-download-id="{task_id}"]')
            expect(item).to_contain_text(f"Nhóm N{gid} · tập {ordinal}/3 · S01E0{ordinal}", timeout=30 * MS)
            expect(item).to_contain_text(f"Đã vào input: {name}")
            expect(item.locator("img")).to_have_count(0)
        expect(card.locator("img")).to_have_count(0)
        expect(row.locator("img")).to_have_count(0)
        # A bait drawn as markup anywhere on the page, even for a moment, makes an <img> (noted) whose inline
        # handler the dashboard's CSP blocks, which raises a securitypolicyviolation event (noted too). The CSP
        # keeps window.__bf unset either way, so the handler not running proves nothing about the escaping.
        self.look(page)
        self.assertEqual((self.notes(PAGE, "img"), self.notes(PAGE, "csp")), ([], []))
        self.keep_page_text(page)
        self.shot(page, "pc-light-all-done")

        self.assert_no_scan_and_no_yt_dlp()
        self.secret_scan()
        self.assert_network_kept(exact=True)
        self.assert_routes_kept()
        self.assert_page_clean()  # no foreign <img> or CSP violation at any moment, no console error
        self.results["completed"] = True


class RouteInventoryTest(unittest.TestCase):
    """No browser: the production POST routes stay within a written inventory, none pauses everything, and every
    POST the E2E harness lets a page or a step make (POST_ALLOWED) is a production route."""

    def test_post_routes_are_the_written_inventory_without_a_global_pause(self):
        routes = (*download_api.POST_ROUTES, *download_account_api.POST_ROUTES)
        self.assertEqual(sorted(set(routes) - EXPECTED_POST_ROUTES), [])
        self.assertEqual([route for route in routes if "pause" in route.lower()], [])
        self.assertEqual([(path, route) for path in PAUSE_PATHS for route in routes if re.fullmatch(route, path)], [])
        for sample in ALLOWED_SAMPLES:
            self.assertEqual(sum(1 for pattern in POST_ALLOWED if pattern.fullmatch(sample)), 1, sample)
            self.assertTrue(any(re.fullmatch(route, sample) for route in routes), sample)
        self.assertEqual([pattern.pattern for pattern in POST_ALLOWED
                          if not any(pattern.fullmatch(sample) for sample in ALLOWED_SAMPLES)], [])


class RefusedWindows:
    """The coordinator's launcher where no window may open: every call is recorded and fails."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    def __call__(self, source, *_args: Any) -> Any:
        self.calls.append(getattr(source, "id", source))
        raise AssertionError("no sign-in window may open in this test")


class ProductionDefaultsTest(FlowCase):
    """E6: the production constructors of the Control Center's downloader, behind the real handler."""

    def setUp(self) -> None:
        # Not E2EBase.setUp: no fixture source and no test manager; each step builds its own Control Center.
        self.init_records()
        self.addCleanup(self._write_results)

    def control_center(self, build: Callable[[Path], DownloadService]) -> DownloadService:
        """A temporary root, ``build(root)``'s service started behind the real handler on 127.0.0.1:0."""
        root = self.root = make_temp(ROOT_PREFIX)
        self.addCleanup(remove_tree, root, timeout=60.0)
        self.assertTrue(job_purge.allowed_project_root(root))
        (root / "config").mkdir()
        (root / "input").mkdir()
        service = self.service = build(root)
        self.store = service.store
        self.jobs = JobStore(root / "state" / "control-center.sqlite3")
        self.addCleanup(self.jobs.close)
        self.center = self._center()
        self.log = RequestLog()
        pc = ThreadingHTTPServer(("127.0.0.1", 0), logged(_handler_class(self.center), self.log, "pc"))
        pc.daemon_threads = True
        self.pc_port = pc.server_address[1]
        threading.Thread(target=pc.serve_forever, name="m6-pc", daemon=True).start()
        self.addCleanup(pc.server_close)
        self.addCleanup(pc.shutdown)
        service.start()
        self.addCleanup(self.stop, service)
        self.assertIsNone(service.start_error)
        return service

    def stop(self, service: DownloadService) -> None:
        service.stop()
        self.assertTrue(service.wait_closed(60), "the download service did not close within 60 s")
        self.assertIsNone(service.close_error)

    def test_no_source_and_an_unsupported_source_show_guidance_and_never_fall_back(self):
        """E6. (a) No account config: the service the Control Center builds has no manager; the page names the
        local and the example config file and has no source to pick; a forced sign-in is 503; no vault folder.
        (b) The tracked example source with the production verifiers and readers (its adapter "ticket-files" has
        neither; only "release-forms" has them): "Chưa hỗ trợ" without Đăng nhập; a forced sign-in is 409
        LOGIN_UNSUPPORTED before any window; a pasted link of that source
        ends READER_UNSUPPORTED, never waits for a sign-in, never reaches yt-dlp, a hidden run, a browser, a
        session network or a connection (only the worker's link check looks its host up)."""
        from playwright.sync_api import expect

        check_registries()
        self.assertEqual((set(LOGIN_VERIFIERS), set(PAGE_READERS)), ({"release-forms"}, {"release-forms"}))

        # (a) control_center.py: DownloadService(root, cleanable=..., bin_reader=...), nothing else. Nothing is
        # pasted here: its runner is the real yt-dlp.
        service = self.control_center(lambda root: DownloadService(root, cleanable=lambda: (0, 0), bin_reader=None))
        self.assertEqual((service.accounts.manager, service.accounts.coordinator), (None, None))
        page = self.pump = self.open_dashboard("pc-no-source")
        panel = page.locator("#dl-accounts")
        expect(panel).to_contain_text("config/download_accounts.local.json", timeout=60 * MS)
        expect(panel).to_contain_text("config/download_accounts.example.json")
        expect(page.locator("#dl-account-source")).to_have_count(0)
        expect(page.locator('[data-action="dl-account"]')).to_have_count(0)
        self.wait_polls("pc-no-source", 2)
        self.assertEqual(self.log.find("POST"), [])
        status, snapshot = self.api("GET", "/api/downloads")
        self.assertEqual((status, snapshot["accounts"]["sources"]), (200, []))
        self.assertEqual(self.page_post(page, "/api/download-accounts/alpha/login"), [503, "ACCOUNT_NOT_READY"])
        self.assertFalse((self.root / "state" / "source-accounts").exists())
        self.assertEqual(self.store.list_tasks(), [])
        texts = "\n".join([item["json"] for item in self.log.items() if item["json"]] + [page.content()])
        for spelling in (str(self.root), str(self.root).replace("\\", "/"), json.dumps(str(self.root))[1:-1]):
            self.assert_absent(spelling.casefold(), texts.casefold())
        self.assert_routes_kept(("/api/download-accounts/alpha/login", 503))
        # Nothing was pasted (no task above), so neither yt-dlp nor any provider ran; this service's runner is
        # the real yt-dlp, whose calls the fake's log cannot show, so only the scan side is checked here.
        self.assert_no_scan()
        self.look(page)  # the watch notes the current count again: never an empty list
        self.assertEqual(set(self.notes("pc-no-source", "account-buttons")), {"0"})  # never a button, even briefly
        self.shot(page, "pc-light-no-source", full_page=False)

        # (b) The tracked example config as the local one; the production verifiers, readers and registry. The
        # account provider's hidden runs, its browsers and any session network it would build are recorded
        # (each wrapper calls the real one): an unsupported source must reach none of them.
        runs = self.record(download_account_sources, "run_with_session")
        browsers = self.record(download_account_runs, "_run_browser")
        networks = self.record(download_account_http, "SessionNetwork")
        windows, connects, resolver = RefusedWindows(), [], Resolver()

        def refuse_connect(address: str, port: int, timeout: float) -> Any:
            connects.append((address, port))
            raise OSError("no connection in this test")

        def build(root: Path) -> DownloadService:
            (root / "config" / "download_accounts.local.json").write_bytes(
                (REPO_ROOT / "config" / "download_accounts.example.json").read_bytes())
            self.clock = E2EClock()
            self.manager = AccountManager(root, read_account_config(root), clock=self.clock, vault=SessionVault(
                root, protector=FakeProtector(), acl=GoodAcl(FIRST)))
            runtime = AccountRuntime(root, manager=self.manager, coordinator_options={"launcher": windows})
            store = DownloadStore(root / "state" / "downloads.sqlite3")
            runner = YtDlpRunner(root, command_prefix=[sys.executable, str(FAKE)], deno_path=None,
                                 env_extra={"FAKE_YTDLP_SCENARIO": str(root / "no-scenario.json"),
                                            "FAKE_YTDLP_LOG": str(root / "ytdlp-calls.jsonl")})
            worker = DownloadWorker(root, store, runner=runner, ffmpeg=FFMPEG, ffprobe=FFPROBE, resolver=resolver,
                                    space_probe=lambda _root: (10_000 * GB, 100 * GB),
                                    cache_pruner=lambda _root: {"removed_files": 0},
                                    sources=default_registry(root, accounts=self.manager),
                                    http=SafeHttp(resolver=resolver, connector=refuse_connect))
            return DownloadService(root, cleanable=lambda: (0, 0), bin_reader=None, store=store, worker=worker,
                                   accounts=runtime)
        service = self.control_center(build)
        self.addCleanup(self.edge_left)
        url = "https://portal.example/film/x"
        provider = service.worker.sources.provider_for(url)
        verifiers = service.accounts.coordinator.verifiers
        self.assertEqual((provider.id, provider.reader, set(verifiers)), ("demo-portal", None, {"release-forms"}))
        self.assertNotIn(self.manager.config.sources["demo-portal"].adapter.id, verifiers)
        page = self.pump = self.open_dashboard("pc-unsupported")
        expect(page.locator(BADGE)).to_have_text("Chưa hỗ trợ", timeout=60 * MS)
        panel = page.locator("#dl-accounts")
        expect(panel).to_contain_text("chưa biết cách xác nhận đăng nhập của nguồn này")
        expect(panel).to_contain_text("chưa đọc được trang phim của nguồn này")
        expect(page.locator("#dl-account-source option")).to_have_text(["Nguồn mẫu"])
        expect(page.locator('[data-action="dl-account"]')).to_have_count(0)
        source = self.api("GET", "/api/downloads")[1]["accounts"]["sources"][0]
        self.assertEqual((source["id"], source["state"], source["login_supported"], source["reader_supported"]),
                         ("demo-portal", "NOT_CONNECTED", False, False))
        self.assertEqual(self.page_post(page, "/api/download-accounts/demo-portal/login"),
                         [409, "LOGIN_UNSUPPORTED"])
        self.assertEqual((windows.calls, self.manager.status("demo-portal")["state"]), ([], "NOT_CONNECTED"))

        task_id = self.paste(page, url)
        task = self.wait_state(task_id, "FAILED", "WAITING_LOGIN", "COMPLETED", timeout=60)
        self.assertEqual((task["state"], task["error_code"], task["account_owner"]),
                         ("FAILED", "READER_UNSUPPORTED", FIRST))
        self.assertNotIn("WAITING_LOGIN", self.kinds(task_id))
        expect(page.locator(f'article.download-item[data-download-id="{task_id}"]')).to_contain_text(
            "chưa có bộ đọc trang", timeout=30 * MS)
        # The worker's own link check looked the host up (a test resolver: no DNS); nothing connected, no window,
        # no hidden run, no browser and no session network of the account provider (its runs would build one).
        self.assertEqual((set(resolver.calls), connects, windows.calls), ({"portal.example"}, [], []))
        self.assertEqual((runs.times(), browsers.times(), networks.times()), ([], [], []))
        self.assertFalse(list(self.manager.vault.source_folder("demo-portal").glob("session-*")))
        self.keep_page_text(page)
        self.shot(page, "pc-light-unsupported", full_page=False)
        self.assert_no_scan_and_no_yt_dlp()  # this service runs the fake yt-dlp, which logs every call
        self.secret_scan()
        self.assert_routes_kept(("/api/download-accounts/demo-portal/login", 409))
        # Console errors only for the forced sign-ins of (a) (503) and (b) (409).
        self.assert_page_clean(r"status of 503 \(Service Unavailable\) @ /api/download-accounts/alpha/login$",
                               r"status of 409 \(Conflict\) @ /api/download-accounts/demo-portal/login$")
        self.look(page)
        self.assertEqual(set(self.notes("pc-unsupported", "account-buttons")), {"0"})
        badges = self.notes("pc-unsupported", "badge")
        self.assertNotIn("Đã kết nối", badges)
        self.assertEqual(badges[-1:], ["Chưa hỗ trợ"])
        check_registries()
        self.results["completed"] = True

    def edge_left(self) -> None:
        """No Edge of this root and no left browser profile (the harness's check, for this step's root)."""
        root = self.root
        deadline = time.monotonic() + 30
        while (left := edge_command_lines(str(root))) and time.monotonic() < deadline:
            time.sleep(1)
        self.assertEqual(left, [])
        folder = self.manager.vault.browser_folder()
        self.assertEqual(list(folder.iterdir()) if folder.exists() else [], [])


if __name__ == "__main__":
    unittest.main()
