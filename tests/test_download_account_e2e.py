"""Source accounts end to end through Dashboard V2 (docs/SOURCE_ACCOUNTS_PLAN.md, M6).

The page, the real Control Center handler, DownloadService, DownloadWorker, the real AccountSourceProvider,
the real sign-in coordinator with its finish callback and the real verify_video, on a temporary root with a
self-made film site (tests/account_e2e_fixtures.py describes the harness and what is injected). Headless
Edge only: the sign-in "window" is the headless stand-in, never a visible window (a headed sign-in, a real
source's reader and verifier and real hosts are M7). E1 is the PC flow, E2 the phone listener (two phone
pages and a PC page on one film page: STALE_DRAFT across devices, group stop and resume, PC-only refusals), E3
the page whose mode is unknown and sign-ins that fail (fake windows only). tests/test_download_account_e2e_flows.py
(E4-E6) and tests/test_download_account_crossing.py (C1) import the same harness, never a TestCase of this module.

Run (Git Bash, from the worktree; the project FFmpeg, Playwright and the installed Edge are required, nothing
is downloaded; without them the tests are reported as skipped with the missing piece named, and
BILIFLOW_REQUIRE_E2E=1 makes that a failure of the module):

    export PYTHONPATH="<worktree>\\src;<worktree>" TEMP="E:\\DungChung\\BiliFlow\\temp" \\
        TMP="E:\\DungChung\\BiliFlow\\temp" BILIFLOW_FFMPEG="E:\\DungChung\\BiliFlow\\tools\\ffmpeg\\bin\\ffmpeg.exe" \\
        PYTHONIOENCODING=utf-8
    E:/DungChung/BiliFlow/.venv/Scripts/python.exe -m unittest tests.test_download_account_e2e -v

PYTHONPATH must name the worktree first, so the handler serves this worktree's dashboard_v2, and TEMP/TMP the
install's temp folder: setUpModule refuses another tree (the venv's editable install points at the main checkout).
Optional: BILIFLOW_E2E_SHOTS=<absolute folder outside the repository, empty or made by this harness> saves PNGs
(PC light/dark, phone 390 light and 375 dark, the PC's unknown-mode and sign-in states) and a results.json of the
checked values there (never a secret).

Root and ports: each test makes ``<worktree>\\temp\\m6-e2e-*`` (its databases, vault, input, temp) and
``<worktree>\\temp\\m6-dash-*`` (the dashboard Edge profiles), plus ``m6-clips-*`` per module and the
``session-browser-tls-*`` CA folder of the browser tests. Every listener (the Control Center handler, the
phone listener, the HTTPS fixture server, the session browser's black-hole proxy) binds 127.0.0.1:0; the
real Control Center (8765), its config, database, vault and input are never touched.

Stop: Ctrl+C ends the run; the tests' cleanups close the dashboard, stop the service (bounded), close the
listeners, wait for this root's Edge processes and delete the folders above after an absolute-path check.
After a hard kill, list what is left read-only (Get-CimInstance Win32_Process -Filter "Name='msedge.exe'",
look for command lines naming a ``m6-e2e-``/``m6-dash-`` folder), end only those processes, then delete only
those folders under ``<worktree>\\temp``.
"""
from __future__ import annotations

import json
import re
import threading
import time
import unittest
from dataclasses import replace
from typing import Any, Callable, Mapping
from unittest import mock
from urllib.parse import urlsplit

from biliflow.download_account_browser import BrowserFailed
from biliflow.download_account_login import LoginOutcome
from biliflow.download_accounts import LOGIN_END_CODES
from biliflow.download_files import verify_video
from biliflow.download_media_file import SNIFF_BYTES
from biliflow.phone_access import PC_ONLY_SOURCE_ACCOUNTS
from tests.account_e2e_fixtures import (
    CLIPS,
    EDGE_SECONDS,
    REPO_ROOT,
    E2EBase,
    module_setup,
)
from tests.account_source_fixtures import Item, item_key, series
from tests.test_download_account_browser import BETA_PORTAL, CANARY_SID, FIRST, PORTAL
from tests.test_download_account_login import Answers, FixtureVerifier, stored_sessions

VARIANTS = (("v1080", "1080p", "Vietsub"), ("v720", "720p", "Thuyết minh"))
KIND_1080, KIND_720 = "1080p|vietsub", "720p|thuyết minh"
BAIT = 'Tập 3 <img src=x onerror="window.__bf=1">'  # an episode label of the source, shown as text only
TITLE = "Phim thử"
BETA_PAGE = f"https://{BETA_PORTAL}/film/B1"
PICKS = ("s1e2", "s1e10", "s2e1")
CODES = ("S01E02", "S01E10", "S02E01")
SERVER_ROWS = [["Tập 1", "Tập 2", "Đặc biệt", BAIT, *(f"Tập {n}" for n in range(4, 11)), "Tập không số"],
               ["Tập 1", "Tập 2"]]
MS = 1000
# The page's draft debounce (a burst of changes is one draft), read from the page's own file.
SAVE_DELAY_MS = int(re.search(r"const SAVE_DELAY_MS = (\d+);",
                              (REPO_ROOT / "dashboard_v2" / "download-episodes.js").read_text("utf-8"))[1])
# E2: what the PC saves first, then what the phone confirms (the group's order).
PHONE_PICKS, PHONE_CODES = ("s1e2", "s1e10"), ("S01E02", "S01E10")
PHONES = (("phone-390-light", 390, 844, "light"), ("phone-375-dark", 375, 812, "dark"))
REMOTE_DRAFT = "Mở lựa chọn đã lưu"  # the poll's note that another device saved a newer choice (its button)
STALE_NOTICE = "Lựa chọn vừa được sửa ở thiết bị khác; đã mở lựa chọn đã lưu. Kiểm tra lại trước khi tải."
ACCOUNT_OPS = ("login", "cancel-login", "disconnect")
ACCOUNT_PATH = "/api/download-accounts/alpha/{}"
# E3 (1): how GET /api/phone-mode fails in the browser, and what the page then says (adapter.js deviceProblem).
UNKNOWN_MODES = (("503", "Control Center trả lỗi 503"),
                 ("404", "Control Center không có mục này (404), có thể là bản cũ"),
                 ("abort", "mất kết nối hoặc quá thời gian chờ"),
                 ("no-remote", "câu trả lời của Control Center thiếu thông tin này"),
                 ("timeout", "mất kết nối hoặc quá thời gian chờ"))
CHECKING = "Đang kiểm tra trang này mở trên PC hay qua điện thoại"


def is_account_path(path: str) -> bool:
    return path.startswith("/api/download-accounts")


def setUpModule():
    module_setup()  # its resources are module cleanups (tests/account_e2e_fixtures.py)


def film_pages() -> list[list[Item]]:
    """Film F1 on two pages: 12 numbered episodes in two seasons listed out of order (10 before 2), a special
    placed after episode 2, an episode without a number, a trailer; every episode in 1080p and 720p except
    s1e4 (no 720p), and s2e2 with a second 720p file of the same kind (ambiguous)."""
    items = series({"s1": [10, 2, 1, 3, 4, 5, 6, 7, 8, 9], "s2": [1, 2]}, variants=VARIANTS,
                   missing={"s1e4": {"v720"}})
    items = [replace(item, label=BAIT) if item.episode == "s1e3" else item for item in items]

    def of(*episodes: str) -> list[Item]:
        return [item for episode in episodes for item in items if item.episode == episode]

    def extra(episode: str, label: str, **options) -> list[Item]:
        return [Item(episode, variant, season="s1", season_number=1, label=label, quality=quality, audio=audio,
                     size=2000, **options) for variant, quality, audio in VARIANTS]
    first = of("s1e10", "s1e2", "s1e1", "s1e3", "s1e4", "s1e5") + [Item("trailer1", "t1", role="trailer",
                                                                        quality=None, audio=None)]
    second = (of("s1e6", "s1e7", "s1e8", "s1e9") + extra("sp1", "Đặc biệt", special=True, after=2)
              + extra("loose", "Tập không số") + of("s2e1", "s2e2")
              + [Item("s2e2", "v720b", season="s2", season_number=2, number=2, label="Tập 2", quality="720p",
                      audio="Thuyết minh", size=1002)])
    return [first, second]


class DraftHold:
    """Stands in for DownloadWorker.save_episode_draft: records each draft body and, until ``stop``, holds the
    call at the server until the test releases it (bounded), then runs the real method."""

    def __init__(self, original: Callable[[int, Mapping[str, Any]], dict]):
        self.original = original
        self.bodies: list[dict] = []
        self._waiting: list[threading.Event] = []
        self._lock = threading.Lock()
        self._on = True

    def __call__(self, task_id: int, body: Mapping[str, Any]) -> dict:
        go = threading.Event()
        with self._lock:
            self.bodies.append(json.loads(json.dumps(body)))
            if self._on:
                self._waiting.append(go)
            else:
                go.set()
        go.wait(60)
        with self._lock:
            if go in self._waiting:
                self._waiting.remove(go)
        return self.original(task_id, body)

    def held(self) -> int:
        with self._lock:
            return sum(1 for go in self._waiting if not go.is_set())

    def release(self) -> None:
        with self._lock:
            for go in self._waiting:
                go.set()

    def stop(self) -> None:
        with self._lock:
            self._on = False
        self.release()


class DashboardFlowTest(E2EBase):
    """E1-E3: the dashboard on the PC and phone listeners over the real wiring (see each test's docstring)."""

    def site(self):
        pages = film_pages()
        files = {item_key(item): CLIPS["a"] if item.variant == "v1080" else CLIPS["b"]
                 for page in pages for item in page if item.role is None}
        return self.install_site(film="F1", title=TITLE, kind="series", pages=pages, promos=[Item("promo1", "p1")],
                                 files=files, session_value=CANARY_SID)

    def test_pc_sign_in_wakes_the_page_and_picked_episodes_land_in_input(self):
        """(a) A pasted series page and a beta page wait for a sign-in without a slot or a loop. (b) One press of
        Đăng nhập: one POST, the window's evidence, generation 1, the page wakes in its place, beta waits. (c) The
        page's list, read without a ticket, opens with GETs only, in the server's order, the bait as text. (d) 720p
        for all is blocked by a missing and an ambiguous episode. (e) Three picks in 1080p as serial drafts that a
        reload keeps. (f) A lost confirm answer (while the page's list polls fail too) and a double click make one
        group. (g) The first episode is held (two slots): no percent, then it finishes last; names 001-003 keep the
        group order, MKV bytes as served, verified by verify_video. (h) Group, snapshot and rows agree; of the temp
        files only the page task's own empty folder stays. (i) No scan, no yt-dlp. (j) No secret anywhere."""
        from playwright.sync_api import expect

        site = self.site()
        self.windows.use("edge", FixtureVerifier())
        page = self.open_dashboard()
        self.pump = page
        badge = page.locator("#dl-account-state .badge")
        expect(badge).to_have_text("Chưa đăng nhập", timeout=60 * MS)
        self.assertEqual(self.log.find("POST"), [])  # nothing is posted at load

        # (a) Both pages wait for a sign-in, without a slot, a browser run or a loop.
        page.fill("#dl-urls", f"{site.url}\n{BETA_PAGE}")
        page.check("#dl-rights")
        page.click('[data-action="dl-add"]')

        def both_waiting():
            tasks = {task["url"]: task for task in self.store.list_tasks()}
            pair = tasks.get(site.url), tasks.get(BETA_PAGE)
            return pair if all(task and task["state"] == "WAITING_LOGIN" for task in pair) else None
        page_task, beta_task = self.wait_until(both_waiting, 60, "both pages waiting for a sign-in")
        pid = page_task["id"]
        for task, source in ((page_task, "alpha"), (beta_task, "beta")):
            self.assertEqual((task["login_source"], task["login_reason"], task["account_owner"]),
                             (source, "NOT_CONNECTED", FIRST))
        queued_at = page_task["queued_at"]
        polls = self.log.count("GET", "/api/downloads")
        self.wait_until(lambda: self.log.count("GET", "/api/downloads") >= polls + 3, 60, "three more list polls")
        self.assertEqual([self.kinds(task["id"]).count("WAITING_LOGIN") for task in (page_task, beta_task)], [1, 1])
        self.assertEqual(self.server.requests, [])  # no page, ticket or file was asked for
        self.assertEqual(self.windows.calls, [])
        self.assertNotIn(pid, self.worker.running_ids())
        row = page.locator(f'article.download-item[data-download-id="{pid}"]')
        expect(page.locator("#dl-account-state .dl-account-waiting")).to_have_text("1 lượt chờ đăng nhập",
                                                                                 timeout=30 * MS)
        row.locator('[data-action="dl-account-show"]').click()  # only shows the panel: no sign-in
        self.assertEqual(page.evaluate("() => document.activeElement && document.activeElement.id"),
                         "dl-account-source")
        self.assertEqual(self.log.find("POST", lambda path: path.startswith("/api/download-accounts")), [])

        # (b) One press of Đăng nhập: one POST, the fixture's evidence, generation 1, the page wakes.
        page.click('#dl-accounts [data-action="dl-account"][data-op="login"]')
        expect(badge).to_have_text("Đã kết nối", timeout=EDGE_SECONDS * MS)
        logins = self.log.find("POST", "/api/download-accounts/alpha/login")
        self.assertEqual([item["status"] for item in logins], [202])
        self.assertEqual(json.loads(logins[0]["json"])["source"]["state"], "LOGGING_IN")  # 202 is never CONNECTED
        self.assertEqual(self.log.find("POST", lambda path: path.startswith("/api/download-accounts")), logins)
        status = self.manager.status("alpha")
        self.assertEqual((status["state"], status["authenticated_at"]), ("CONNECTED", self.clock.start.isoformat()))
        self.assertEqual(self.manager.session_gate("alpha"), (True, None, 1))
        self.assertEqual((self.windows.calls, self.windows.permits), (["edge"], ["HeadedPermit"]))
        woken = self.wait_until(lambda: (task := self.store.get(pid))["state"] != "WAITING_LOGIN" and task, 60,
                                "the page woken by the sign-in")
        self.assertEqual(woken["queued_at"], queued_at)  # its place in the queue is kept
        self.assertIn("LOGIN_RESUMED", self.kinds(pid))
        self.assertEqual(self.store.get(beta_task["id"])["state"], "WAITING_LOGIN")
        self.assertEqual(self.manager.status("beta")["state"], "NOT_CONNECTED")
        expect(page.locator("#dl-account-state .dl-account-waiting")).to_have_count(0, timeout=30 * MS)
        self.keep_page_text(page)

        # (c) The list waits for episodes; opening Chọn tập only reads; rows in the server's order.
        page_task = self.wait_state(pid, "NEEDS_CHOICE", timeout=EDGE_SECONDS)
        self.assertEqual(page_task["probe"]["choice_kind"], "episodes")
        self.assertEqual(site.loads, {})  # reading the list asked for no ticket
        status, listing = self.api("GET", f"/api/downloads/{pid}/episodes")
        self.assertEqual(status, 200)
        groups = listing["listing"]["groups"]
        self.assertEqual(self.check("server_rows", [[item["label"] for item in group["episodes"]]
                                                    for group in groups]), SERVER_ROWS)
        self.assertTrue(listing["listing"]["complete"])
        keys = [item["key"] for group in groups for item in group["episodes"]]
        kinds = [item["kind"] for item in listing["listing"]["variant_kinds"]]
        posts = len(self.log.find("POST"))
        episodes = page.locator(f'[data-action="dl-op"][data-op="episodes"][data-id="{pid}"]')
        expect(episodes).to_be_visible(timeout=60 * MS)
        episodes.click()
        rows = page.locator("#dl-ep-root .dl-ep-row")
        expect(rows).to_have_count(len(keys), timeout=60 * MS)
        shown = page.locator("#dl-ep-root .dl-ep-name").all_inner_texts()
        self.assertEqual(self.check("dialog_rows", shown), [label for group in SERVER_ROWS for label in group])
        self.assertLess(shown.index("Tập 2"), shown.index("Tập 10"))
        expect(page.locator("#dl-ep-root .dl-ep-season")).to_have_count(2)
        self.assertEqual(len(self.log.find("POST")), posts)  # opening the dialog posted nothing
        self.assertTrue(page.evaluate("() => window.__bf === undefined"))
        expect(page.locator("#dl-ep-root img")).to_have_count(0)
        self.keep_page_text(page)
        self.shot(page, "pc-light-episodes", full_page=False)

        # (d) 720p for every episode: one is missing, one is ambiguous; nothing can be confirmed.
        confirm_path = f"/api/downloads/{pid}/episodes/confirm"
        page.check('input[name="dl-ep-mode"][value="all"]')
        page.check('input[name="dl-ep-how"][value="kind"]')
        page.locator("#dl-ep-kind").select_option(str(kinds.index(KIND_720)))
        expect(page.locator("#dl-ep-why")).to_have_text("Có tập không có bản đã chọn; chọn bản khác hoặc bỏ các tập "
                                                        "đó.", timeout=60 * MS)
        confirm = page.locator("#dl-ep-confirm")
        expect(confirm).to_be_disabled()
        plan = self.worker.episodes(pid)["plan"]
        self.assertEqual(self.check("plan_720", ([item["episode"] for item in plan["missing"]],
                                                 [item["episode"] for item in plan["ambiguous"]])),
                         (["s1e4"], ["s2e2"]))
        expect(page.locator(f"#dl-ep-row-{keys.index('s1e4')} .dl-ep-flag")).to_have_text("Thiếu bản đã chọn")
        expect(page.locator(f"#dl-ep-row-{keys.index('s2e2')} .dl-ep-flag")).to_have_text("Hai file cùng bản")
        self.assertEqual(self.log.find("POST", confirm_path), [])

        # (e) Three picks in 1080p: drafts one at a time with the list's fingerprint and the last revision; a
        # reload of the page keeps them. The server holds each draft of this step until the test lets it go, so
        # the user's next changes are made while one is out: they wait for it and go in the next one.
        draft_path = f"/api/downloads/{pid}/episodes/draft"

        def sent() -> list[dict]:
            return [json.loads(body) for _name, method, url, body in self.page_events["requests"]
                    if method == "POST" and url.endswith(draft_path)]
        hold = DraftHold(self.worker.save_episode_draft)
        self.addCleanup(hold.stop)
        before = len(sent())
        with mock.patch.object(self.worker, "save_episode_draft", hold):
            page.check('input[name="dl-ep-mode"][value="pick"]')
            self.wait_until(lambda: hold.held() == 1, 60, "the first draft of the picks at the server")
            for key in PICKS[:2]:
                page.check(f'input[data-ep-pick="{keys.index(key)}"]')
            page.wait_for_timeout(3 * SAVE_DELAY_MS)  # the page's save timer has run meanwhile
            self.assertEqual(len(sent()), before + 1)  # the two picks wait for the draft that is out
            hold.release()
            self.wait_until(lambda: hold.held() == 1 and len(hold.bodies) == 2, 60, "the second draft")
            page.check(f'input[data-ep-pick="{keys.index(PICKS[2])}"]')
            page.locator("#dl-ep-kind").select_option(str(kinds.index(KIND_1080)))
            page.wait_for_timeout(3 * SAVE_DELAY_MS)
            self.assertEqual(len(sent()), before + 2)
            hold.stop()
            expect(confirm).to_have_text("Tải 3 tập", timeout=60 * MS)
            expect(confirm).to_be_enabled(timeout=60 * MS)
        self.assertEqual([(body["selection"]["episodes"], body["selection"]["variant_kind"]) for body in hold.bodies],
                         [([], KIND_720), (list(PICKS[:2]), KIND_720), (list(PICKS), KIND_1080)])
        bodies = sent()
        answers = self.log.find("POST", draft_path)
        self.assertEqual([item["status"] for item in answers], [200] * len(bodies))
        revisions = [listing["revision"]] + [json.loads(item["json"])["revision"] for item in answers]
        self.assertEqual(self.check("draft_revisions", [body["revision"] for body in bodies]), revisions[:-1])
        self.assertEqual(revisions, list(range(revisions[0], revisions[0] + len(revisions))))  # one step each
        self.assertEqual({body["fingerprint"] for body in bodies}, {listing["fingerprint"]})
        for earlier, later in zip(answers, answers[1:]):
            self.assertLessEqual(earlier["ended"], later["started"])  # never two drafts at once
        saved = self.worker.episodes(pid)
        self.assertEqual((saved["draft"]["mode"], saved["draft"]["episodes"], saved["draft"]["variant_kind"]),
                         ("pick", list(PICKS), KIND_1080))
        page.reload()
        expect(episodes).to_be_visible(timeout=60 * MS)
        episodes.click()
        expect(rows).to_have_count(len(keys), timeout=60 * MS)
        expect(page.locator('input[name="dl-ep-mode"][value="pick"]')).to_be_checked()
        expect(confirm).to_have_text("Tải 3 tập", timeout=60 * MS)
        expect(confirm).to_be_enabled()
        picked = page.eval_on_selector_all("#dl-ep-root input[data-ep-pick]:checked",
                                           "boxes => boxes.map(box => Number(box.dataset.epPick))")
        self.assertEqual(sorted(picked), sorted(keys.index(key) for key in PICKS))
        expect(page.locator("#dl-ep-kind")).to_have_value(str(kinds.index(KIND_1080)))
        self.shot(page, "pc-light-picked", full_page=False)

        # (f) The confirm answer is lost (the server made the group) while the list polls fail too; the user's
        # double click sends the same request once: the server replays the one group.
        gate = self.gate_file("s1e2--v1080")  # (g) S01E02's probe is held from its first byte
        self.assertEqual(self.worker.slots(), 2)  # (g)'s order below: the held episode keeps one of two slots
        lost: list = []
        dropped: list[str] = []

        def lose_answer(route):
            response = route.fetch()
            lost.append((response.status, response.json()))
            route.abort()

        def drop_list(route):
            if route.request.method == "GET":
                dropped.append(route.request.url)
                route.abort()
            else:
                route.continue_()
        list_url = re.compile(rf"^http://127\.0\.0\.1:{self.pc_port}/api/downloads$")
        page.route(f"**{confirm_path}", lose_answer)
        page.route(list_url, drop_list)
        confirm.click()
        expect(page.locator("#dl-ep-root .modal-error")).to_contain_text("Mất kết nối khi gửi", timeout=60 * MS)
        expect(confirm).to_be_enabled()
        self.wait_until(lambda: dropped, 60, "a list poll of the page dropped while the confirm answer is lost")
        page.unroute(f"**{confirm_path}", lose_answer)
        confirm.dblclick()
        expect(page.locator("dialog#dl-episodes")).to_be_hidden(timeout=60 * MS)
        page.unroute(list_url, drop_list)
        self.assertEqual([(status, body["replay"], body["group"]["total"]) for status, body in lost],
                         [(200, False, 3)])
        self.check("dropped_list_polls", len(dropped))
        answers = self.log.find("POST", confirm_path)
        self.assertEqual(self.check("confirm_posts", [(item["status"], json.loads(item["json"])["replay"])
                                                      for item in answers]), [(200, False), (200, True)])
        self.assertEqual(len({json.loads(item["json"])["group"]["id"] for item in answers}), 1)
        gid = json.loads(answers[0]["json"])["group"]["id"]
        self.assertEqual([(group["id"], group["total"]) for group in self.worker.groups.summaries()], [(gid, 3)])
        card = page.locator(f"#dl-group-{gid}")
        expect(card).to_be_visible(timeout=60 * MS)

        # (g) While S01E02 is held its size is unknown: the group shows no percent and a running chip. The other
        # two finish first; then S01E02. Names keep the group order; bytes are the served MKV; verified.
        members = self.worker.group_detail(gid)["members"]
        ids = [member["task_id"] for member in members]
        self.assertEqual([member["code"] for member in members], list(CODES))
        self.wait_until(gate.entered.is_set, EDGE_SECONDS, "S01E02's file being asked for")
        self.assertIsNone(self.worker.groups.summary(gid)["percent"])
        expect(card.locator(".dl-chip-running")).to_be_visible(timeout=60 * MS)
        expect(card.locator(".download-item-progress strong")).to_have_text("")
        expect(card.locator('[role="progressbar"]')).to_have_count(0)
        self.wait_until(lambda: all(self.store.get(task_id)["state"] == "COMPLETED" for task_id in ids[1:]),
                        2 * EDGE_SECONDS, "S01E10 and S02E01 in input")
        self.assertEqual(self.store.get(ids[0])["state"], "PROBING")
        self.assertIsNone(self.worker.groups.summary(gid)["percent"])
        expect(card).to_contain_text("Đã xong 2/3 tập", timeout=60 * MS)
        expect(card.locator(".dl-chip-running")).to_be_visible()
        expect(card.locator(".download-item-progress strong")).to_have_text("")
        self.shot(page, "pc-light-held")
        gate.open()
        self.wait_state(ids[0], "COMPLETED", timeout=EDGE_SECONDS)
        expect(card).to_contain_text("Đã xong 3/3 tập", timeout=60 * MS)
        order = sorted(ids, key=lambda task_id: next(event["id"] for event in self.store.events(task_id)
                                                     if event["kind"] == "COMPLETED"))
        self.assertEqual(self.check("publish_order", [ids.index(task_id) + 1 for task_id in order]), [2, 3, 1])
        names = [f"{ordinal:03d} - {TITLE} - {code}.mkv" for ordinal, code in enumerate(CODES, 1)]
        self.assertEqual(self.check("input", self.input_names()), names)
        for name in names:
            self.assertEqual((self.root / "input" / name).read_bytes(), CLIPS["a"])
        # One ticket per episode: its probe's ticket serves its transfer (download_account_tickets).
        self.assertEqual(self.check("ticket_loads", dict(site.loads)), {f"{key}--v1080": 1 for key in PICKS})
        self.assertIs(self.worker.verifier, verify_video)
        for task_id in ids:
            verify = self.store.get(task_id)["verify"]
            self.assertEqual((verify["ok"], verify["video_codec"], verify["audio_codec"]), (True, "h264", "aac"))

        # (h) The group detail, the snapshot's places and the rows agree; the page task became the group.
        status, detail = self.api("GET", f"/api/downloads/groups/{gid}")
        self.assertEqual(status, 200)
        self.assertEqual([(member["ordinal"], member["code"], member["task_id"], member["task_state"])
                          for member in detail["members"]],
                         [(ordinal, code, task_id, "COMPLETED") for ordinal, (code, task_id) in
                          enumerate(zip(CODES, ids), 1)])
        status, snapshot = self.api("GET", "/api/downloads")
        places = {task["id"]: task["group"] for task in snapshot["tasks"] if task["group"]}
        summary = next(group for group in snapshot["groups"] if group["id"] == gid)
        self.assertEqual((summary["done"], summary["total"], summary["percent"], summary["finished"]),
                         (detail["group"]["done"], detail["group"]["total"], 100, True))
        for ordinal, (task_id, code, name) in enumerate(zip(ids, CODES, names), 1):
            place = places[task_id]
            self.assertEqual((place["group_id"], place["ordinal"], place["total"], place["code"]),
                             (gid, ordinal, 3, code))
            item = page.locator(f'article.download-item[data-download-id="{task_id}"]')
            expect(item).to_contain_text(f"Nhóm N{gid} · tập {ordinal}/3 · {code}", timeout=30 * MS)
            expect(item).to_contain_text(f"Đã vào input: {name}")
        expect(row).to_contain_text(f"Đã tách thành nhóm N{gid} (3 tập)", timeout=30 * MS)
        self.assertEqual(self.store.get(pid)["state"], "EXPANDED")
        # Temp files: each finished episode's folder is gone; only the page task's own folder (made by its probe,
        # empty) stays, until Xóa nhóm removes it with the group (download_account_tasks.remove_group). The worker
        # removes an episode's folder only after its COMPLETED event (DownloadWorker._publish), so a bounded wait.
        downloads = self.worker.downloads_dir
        self.wait_until(lambda: sorted(path.name for path in downloads.iterdir()) == [str(pid)], 60,
                        "the finished episodes' temp folders removed")
        self.assertEqual(self.check("temp_downloads", sorted(path.name for path in downloads.iterdir())), [str(pid)])
        self.assertEqual(list((downloads / str(pid)).iterdir()), [])
        self.assertEqual([self.store.get(task_id)["temp_file"] for task_id in ids], [None] * 3)
        self.keep_page_text(page)
        self.shot(page, "pc-light-done")

        # PC dark: the same finished page in a second context.
        dark = self.open_dashboard("pc-dark", color_scheme="dark")
        expect(dark.locator(f"#dl-group-{gid}")).to_contain_text("Đã xong 3/3 tập", timeout=60 * MS)
        self.keep_page_text(dark)
        self.shot(dark, "pc-dark-done")

        # (i) The downloader never queued a scan and never ran yt-dlp; (j) secrets and the kept checks. The only
        # console errors are the requests (f) dropped: the lost confirm answer and the list polls.
        self.assert_no_scan_and_no_yt_dlp()
        self.secret_scan()
        self.assert_network_kept(exact=True)
        self.assert_routes_kept()
        self.assert_page_clean(rf"net::ERR_FAILED @ /api/downloads(/{pid}/episodes/confirm)?$")
        self.results["completed"] = True

    # ------------------------------------------------------------------ helpers of E2 and E3
    def open_episodes(self, page: Any, task_id: int, rows: int) -> None:
        """Chọn tập of the page task on ``page``: the dialog with its ``rows`` episodes."""
        from playwright.sync_api import expect
        button = page.locator(f'[data-action="dl-op"][data-op="episodes"][data-id="{task_id}"]')
        expect(button).to_be_visible(timeout=60 * MS)
        button.click()
        expect(page.locator("#dl-ep-root .dl-ep-row")).to_have_count(rows, timeout=60 * MS)

    def picked(self, page: Any) -> list[int]:
        return sorted(page.eval_on_selector_all("#dl-ep-root input[data-ep-pick]:checked",
                                                "boxes => boxes.map(box => Number(box.dataset.epPick))"))

    def sent(self, page_name: str, method: str, path: str) -> list[str | None]:
        """The bodies of the requests the page ``page_name`` itself sent to ``path`` (the browser's side)."""
        return [body for name, sent_method, url, body in self.page_events["requests"]
                if name == page_name and sent_method == method and url.split("?", 1)[0].endswith(path)]

    def account_posts(self) -> list[tuple[str, str, Any]]:
        """Every account POST a listener answered (listener, path, status)."""
        return [(item["listener"], item["path"], item["status"]) for item in self.log.find("POST", is_account_path)]

    def account_requests_sent(self, page_name: str) -> list[tuple[str, str]]:
        """Every request the page ``page_name`` sent to an account route (method, path)."""
        return [(method, urlsplit(url).path) for name, method, url, _body in self.page_events["requests"]
                if name == page_name and is_account_path(urlsplit(url).path)]

    def record_wakes(self) -> tuple[list[float], list[tuple[float, list[int]]]]:
        """Test-side wrappers around the real code (both call through, nothing else changes): when the sign-in's
        finish callback woke the dispatcher, and what each later look at the waiting tasks woke."""
        wakes: list[float] = []
        passes: list[tuple[float, list[int]]] = []
        wake = self.runtime._wake  # noqa: SLF001 - DownloadService attached the worker's wake_logins here
        resume = self.worker._resume_waiting_logins  # noqa: SLF001 - the dispatcher's look at WAITING_LOGIN

        def recorded_wake() -> None:
            wakes.append(time.monotonic())
            wake()

        def recorded_resume() -> list[int]:
            woken = resume()
            passes.append((time.monotonic(), list(woken)))
            return woken
        self.runtime._wake = recorded_wake  # noqa: SLF001
        self.addCleanup(setattr, self.runtime, "_wake", wake)
        patcher = mock.patch.object(self.worker, "_resume_waiting_logins", recorded_resume)
        patcher.start()
        self.addCleanup(patcher.stop)
        return wakes, passes

    # ------------------------------------------------------------------ E2
    def test_phone_views_accounts_but_drives_episodes_and_groups(self):
        """E2. Two phones (390x844 light, 375x812 dark) on the real phone listener, signed in by typing the code,
        with alpha connected on the PC through the manager's own steps: the account panel is status only, with no
        account button ever drawn. The light phone pastes the film page (read with the saved session) and opens
        Chọn tập (GETs only, the bait as text). The PC saves a choice first; the phone, still on the older revision,
        changes its own: the real route answers STALE_DRAFT, the phone reloads the PC's saved choice with the
        notice, and neither page confirms by itself. The phone then confirms "Tải 2 tập" (one 200 on the phone
        listener; the PC's open dialog learns it from its poll). The dark phone stops the group while S01E02 is
        held mid-transfer and resumes it: the episode stops with its part and continues it with Range/If-Range.
        Both land in input. Forced account POSTs from both phones get 403 pc_only before any window or status
        change. No sideways scroll at either width; no secret in any phone or PC answer or page."""
        from playwright.sync_api import expect

        site = self.site()
        self.connect_alpha()  # the manager's own sign-in steps: no window (the launcher refuses one)
        self.assertEqual(self.manager.session_gate("alpha"), (True, None, 1))
        phones = {name: self.open_phone_page(width=width, height=height, color_scheme=scheme)
                  for name, width, height, scheme in PHONES}
        light, dark = phones["phone-390-light"], phones["phone-375-dark"]
        self.pump = light
        for name, phone in phones.items():
            panel = phone.locator("#dl-accounts")
            expect(panel.locator("#dl-account-state .badge")).to_have_text("Đã kết nối", timeout=60 * MS)
            expect(panel.locator(".pc-only-note")).to_contain_text("chỉ làm trên PC")
            expect(phone.locator('[data-action="dl-account"]')).to_have_count(0)
            self.assert_no_side_scroll(phone, f"{name} list")
        self.assertEqual(self.check("phone_mode_answers", sorted({json.loads(item["json"])["remote"] for item in
                                                                  self.log.find("GET", "/api/phone-mode", "phone")})),
                         [True])

        # The light phone pastes the film page; the PC's session reads its list (no window): NEEDS_CHOICE.
        light.fill("#dl-urls", site.url)
        light.check("#dl-rights")
        light.click('[data-action="dl-add"]')
        self.wait_until(lambda: self.log.count("POST", "/api/downloads", "phone") == 1, 60, "the phone's paste")
        self.assertEqual([item["status"] for item in self.log.find("POST", "/api/downloads", "phone")], [200])
        pid = next(task["id"] for task in self.store.list_tasks() if task["url"] == site.url)
        page_task = self.wait_state(pid, "NEEDS_CHOICE", timeout=EDGE_SECONDS)
        self.assertEqual(page_task["probe"]["choice_kind"], "episodes")
        status, listing = self.api("GET", f"/api/downloads/{pid}/episodes")
        self.assertEqual(status, 200)
        keys = [item["key"] for group in listing["listing"]["groups"] for item in group["episodes"]]
        kinds = [item["kind"] for item in listing["listing"]["variant_kinds"]]
        first_revision = listing["revision"]

        # Both phones open Chọn tập: GETs only (over the phone listener), the bait as text, no sideways scroll.
        episodes_path, draft_path = f"/api/downloads/{pid}/episodes", f"/api/downloads/{pid}/episodes/draft"
        confirm_path = f"/api/downloads/{pid}/episodes/confirm"
        posts = len(self.log.find("POST"))
        for name, phone in phones.items():
            self.open_episodes(phone, pid, len(keys))
            expect(phone.locator(f"#dl-ep-row-{keys.index('s1e3')} .dl-ep-name")).to_have_text(BAIT)
            expect(phone.locator("#dl-ep-root img")).to_have_count(0)
            self.assertTrue(phone.evaluate("() => window.__bf === undefined"))
            self.assert_no_side_scroll(phone, f"{name} episodes")
            self.keep_page_text(phone)
        self.assertEqual(len(self.log.find("POST")), posts)  # opening the dialog posted nothing
        self.assertGreaterEqual(len([item for item in self.log.find("GET", episodes_path, "phone")
                                     if item["status"] == 200]), 2)
        dark.locator('dialog#dl-episodes .dl-ep-buttons [data-action="dl-ep-close"]').click()
        expect(dark.locator("dialog#dl-episodes")).to_be_hidden(timeout=30 * MS)

        # The PC saves its choice first: S01E02 and S01E10 in 1080p (serial drafts; the last revision is the PC's).
        pc = self.open_dashboard()
        self.open_episodes(pc, pid, len(keys))
        pc.check('input[name="dl-ep-mode"][value="pick"]')
        for key in PHONE_PICKS:
            pc.check(f'input[data-ep-pick="{keys.index(key)}"]')
        pc.check('input[name="dl-ep-how"][value="kind"]')
        pc.locator("#dl-ep-kind").select_option(str(kinds.index(KIND_1080)))
        pc_confirm = pc.locator("#dl-ep-confirm")
        expect(pc_confirm).to_have_text("Tải 2 tập", timeout=60 * MS)
        expect(pc_confirm).to_be_enabled(timeout=60 * MS)
        saved = self.worker.episodes(pid)
        self.assertEqual((saved["draft"]["mode"], saved["draft"]["episodes"], saved["draft"]["variant_kind"]),
                         ("pick", list(PHONE_PICKS), KIND_1080))
        pc_revision = saved["revision"]
        self.assertGreater(pc_revision, first_revision)
        self.wait_until(lambda: 0 < len(self.log.find("POST", draft_path, "pc"))
                        == len(self.sent("pc-light", "POST", draft_path)), 30, "the PC's drafts in the log")
        self.assertEqual({item["status"] for item in self.log.find("POST", draft_path, "pc")}, {200})

        # The phone, still on the older revision, sees the note of its poll, then changes its choice anyway. The
        # server holds that draft while the user makes one more change (it waits for the one that is out); the real
        # route then answers STALE_DRAFT and the phone opens the PC's saved choice with the notice.
        notices = light.locator("#dl-ep-root .notice")
        expect(notices.filter(has_text=REMOTE_DRAFT)).to_be_visible(timeout=60 * MS)
        hold = DraftHold(self.worker.save_episode_draft)
        self.addCleanup(hold.stop)
        with mock.patch.object(self.worker, "save_episode_draft", hold):
            light.check('input[name="dl-ep-mode"][value="pick"]')
            self.wait_until(lambda: hold.held() == 1, 60, "the phone's draft at the server")
            light.check(f'input[data-ep-pick="{keys.index("s1e1")}"]')
            light.wait_for_timeout(3 * SAVE_DELAY_MS)  # negative check: the page's save timer has run meanwhile
            self.assertEqual(len(self.sent("phone-390-light", "POST", draft_path)), 1)
            hold.stop()
            expect(notices.filter(has_text=STALE_NOTICE)).to_be_visible(timeout=60 * MS)
        self.assertEqual([(body["revision"], body["selection"]) for body in hold.bodies],
                         [(first_revision, {"mode": "pick", "episodes": []})])
        stale = self.wait_until(lambda: self.log.find("POST", draft_path, "phone"), 30, "the phone's draft in the log")
        self.assertEqual(self.check("phone_drafts", [(item["status"], json.loads(item["json"])["code"])
                                                     for item in stale]), [(409, "STALE_DRAFT")])
        expect(light.locator('input[name="dl-ep-mode"][value="pick"]')).to_be_checked()
        expect(light.locator("#dl-ep-kind")).to_have_value(str(kinds.index(KIND_1080)))
        light_confirm = light.locator("#dl-ep-confirm")
        expect(light_confirm).to_have_text("Tải 2 tập", timeout=60 * MS)
        expect(light_confirm).to_be_enabled()
        self.assertEqual(self.picked(light), sorted(keys.index(key) for key in PHONE_PICKS))
        self.assert_no_side_scroll(light, "phone-390-light stale draft")
        self.keep_page_text(light)
        self.shot(light, "phone-390-light-stale", full_page=False)
        # Neither page confirms by itself, and the change made while the stale draft was out is not sent later.
        self.wait_polls("phone-390-light", 2)
        self.wait_polls("pc-light", 2)
        self.assertEqual(self.log.find("POST", confirm_path), [])
        self.assertEqual([self.sent(name, "POST", confirm_path) for name in ("phone-390-light", "pc-light")], [[], []])
        self.assertEqual(len(self.sent("phone-390-light", "POST", draft_path)), 1)
        self.assertEqual(self.worker.episodes(pid)["revision"], pc_revision)

        # The phone confirms "Tải 2 tập": one 200 on the phone listener; the PC's dialog learns it from its poll.
        gate = self.gate_file("s1e2--v1080", transfer=True)  # S01E02 is held in its transfer
        light_confirm.click()
        expect(light.locator("dialog#dl-episodes")).to_be_hidden(timeout=60 * MS)
        self.wait_until(lambda: self.log.count("POST", confirm_path) == 1, 30, "the confirm in the log")
        confirms = self.log.find("POST", confirm_path)
        self.assertEqual([(item["listener"], item["status"]) for item in confirms], [("phone", 200)])
        answer = json.loads(confirms[0]["json"])
        gid = answer["group"]["id"]
        self.assertEqual((answer["replay"], answer["group"]["total"]), (False, 2))
        expect(pc.locator("#dl-ep-root")).to_contain_text(f"đã được tách thành nhóm tập #{gid}", timeout=60 * MS)
        expect(pc.locator("#dl-ep-confirm")).to_have_count(0)
        members = self.worker.group_detail(gid)["members"]
        self.assertEqual([member["code"] for member in members], list(PHONE_CODES))
        ids = [member["task_id"] for member in members]

        # The dark phone stops the group while S01E02 is mid-transfer (S01E10 already in input), then resumes it.
        card = dark.locator(f"#dl-group-{gid}")
        expect(card).to_be_visible(timeout=60 * MS)
        self.wait_state(ids[1], "COMPLETED", timeout=2 * EDGE_SECONDS)
        part = self.worker.downloads_dir / str(ids[0]) / "media.part"
        # Under way: the held answer's first bytes arrived and count as downloaded (the part file itself is written
        # through a buffer that the held answer's trickle never fills; the stop below closes it).
        self.wait_until(lambda: gate.entered.is_set() and part.is_file()
                        and (self.store.get(ids[0])["downloaded_bytes"] or 0) > 0, EDGE_SECONDS,
                        "S01E02's transfer under way")
        self.assertEqual(self.store.get(ids[0])["state"], "DOWNLOADING")
        expect(card).to_contain_text("Đã xong 1/2 tập", timeout=60 * MS)
        expect(card.locator(".dl-chip-running")).to_be_visible(timeout=60 * MS)
        self.assert_no_side_scroll(dark, "phone-375-dark group")
        stop_path, resume_path = f"/api/downloads/groups/{gid}/stop", f"/api/downloads/groups/{gid}/resume"
        card.locator('[data-action="dl-group-op"][data-op="stop"]').click()
        self.wait_until(lambda: self.log.count("POST", stop_path) == 1, 60, "the group stop")
        self.assertEqual([(item["listener"], item["status"]) for item in self.log.find("POST", stop_path)],
                         [("phone", 200)])
        self.wait_state(ids[0], "STOPPED", timeout=60)
        kept = self.check("kept_part_bytes", part.stat().st_size)
        self.assertTrue(0 < kept < len(CLIPS["a"]))
        self.assertEqual(self.store.get(ids[1])["state"], "COMPLETED")  # the finished episode is untouched
        gate.open()  # its held answer was cut by the stop; the server thread may end now
        resume = card.locator('[data-action="dl-group-op"][data-op="resume"]')
        expect(resume).to_be_enabled(timeout=60 * MS)
        self.keep_page_text(dark)
        self.shot(dark, "phone-375-dark-stopped")
        resume.click()
        self.wait_until(lambda: self.log.count("POST", resume_path) == 1, 60, "the group resume")
        self.assertEqual([(item["listener"], item["status"]) for item in self.log.find("POST", resume_path)],
                         [("phone", 200)])
        self.wait_state(ids[0], "COMPLETED", timeout=2 * EDGE_SECONDS)
        self.assertIn("RESUMED", self.kinds(ids[0]))
        asked = [(seen.headers.get("range"), seen.headers.get("if-range"))
                 for seen in self.server.seen("/f/s1e2--v1080")]
        sample = (f"bytes=0-{SNIFF_BYTES - 1}", None)  # a probe's read of the file's start
        # The probe, the held transfer with the probe's link; after Tiếp tục one check of the link kept in memory
        # (no ticket), then the part continued from its last byte for the same file version (never from byte 0).
        self.assertEqual(self.check("s1e2_file_requests", asked),
                         [sample, (None, None), sample, (f"bytes={kept}-", '"s1e2--v1080-1"')])

        # Both episodes in input with the served bytes, in the group's order; both phones show it.
        names = [f"{ordinal:03d} - {TITLE} - {code}.mkv" for ordinal, code in enumerate(PHONE_CODES, 1)]
        self.assertEqual(self.check("input", self.input_names()), names)
        for name in names:
            self.assertEqual((self.root / "input" / name).read_bytes(), CLIPS["a"])
        for task_id in ids:
            self.assertEqual(self.store.get(task_id)["verify"]["ok"], True)
        # Tickets only for the picked variant: one per episode (its probe's), none for the resume.
        self.assertEqual(self.check("ticket_loads", dict(site.loads)), {"s1e2--v1080": 1, "s1e10--v1080": 1})
        for name, phone in phones.items():
            expect(phone.locator(f"#dl-group-{gid}")).to_contain_text("Đã xong 2/2 tập", timeout=60 * MS)
            self.assert_no_side_scroll(phone, f"{name} done")
            self.keep_page_text(phone)
            self.shot(phone, f"{name}-done")

        # Forced account POSTs from inside both phone pages: refused before any body, window or status change.
        status_before = self.manager.status("alpha")
        forced = {f"{op} 390": self.page_post(light, ACCOUNT_PATH.format(op)) for op in ACCOUNT_OPS}
        forced["login 375"] = self.page_post(dark, ACCOUNT_PATH.format("login"))
        self.assertEqual(self.check("forced_account_posts", forced), {name: [403, "pc_only"] for name in forced})
        self.assertEqual((self.windows.calls, self.runtime.coordinator.active()), ([], []))
        self.assertEqual(self.manager.status("alpha"), status_before)
        self.assertEqual(self.manager.session_gate("alpha"), (True, None, 1))
        # Each refusal was logged before its answer went out, so all are there now that the pages have them.
        refused = self.log.find("POST", is_account_path)
        self.assertEqual([(item["listener"], item["path"], item["status"]) for item in refused],
                         [("phone", ACCOUNT_PATH.format(op), 403) for op in (*ACCOUNT_OPS, "login")])
        for item in refused:
            self.assertEqual(json.loads(item["json"]), {"error": PC_ONLY_SOURCE_ACCOUNTS, "code": "pc_only"})

        # No account button was drawn on either phone at any moment, and the badge only ever read "Đã kết nối"
        # (or nothing, before the panel): the watch notes the current state again first, so the notes are never
        # empty. The phones' answers carry no secret.
        for name, phone in phones.items():
            self.look(phone)
            buttons = self.check(f"{name}_account_buttons", sorted(set(self.notes(name, "account-buttons"))))
            self.assertEqual(buttons, ["0"], name)
            badges = self.notes(name, "badge")
            self.assertLessEqual(set(badges), {"", "Đã kết nối"}, name)  # the session never moved
            self.assertEqual(badges[-1], "Đã kết nối", name)
        self.assertTrue([item for item in self.log.items() if item["listener"] == "phone" and item["json"]])
        self.keep_page_text(pc)
        self.assert_no_scan_and_no_yt_dlp()
        self.secret_scan()
        self.assert_network_kept(exact=True)
        self.assert_routes_kept()
        # Console errors only for the answers the steps asked for: the stale draft (409) and the refused POSTs.
        self.assert_page_clean(rf"status of 409 \(Conflict\) @ /api/downloads/{pid}/episodes/draft$",
                               r"status of 403 \(Forbidden\) @ /api/download-accounts/alpha/"
                               r"(login|cancel-login|disconnect)$")
        self.results["completed"] = True

    # ------------------------------------------------------------------ E3
    def check_unknown_mode(self, page: Any, row: Any, login: Any, variant: str, problem: str) -> None:
        """E3 (1) for one way GET /api/phone-mode fails in the browser (page.route, the page's side only): after a
        reload the mode is unknown, so no account button is drawn (DOM_WATCH), the waiting row offers no sign-in
        and nothing is posted over two list polls; Kiểm tra lại then gets the real Control Center's answer
        (remote false) and the buttons come back. ``timeout``: no answer at all; the page shows its check pending
        (button off), then ends it by its own 15 s limit. The test holds every request it lets through until the
        user's Kiểm tra lại, so a background retry can never answer first."""
        from playwright.sync_api import expect
        held: list[Any] = []
        mode = {"now": variant}

        def answer(route: Any) -> None:
            now = mode["now"]
            if now in ("timeout", "hold"):
                held.append(route)  # unanswered: the page's own limit, or the test's release, ends it
            elif now == "pass":
                route.fallback()  # no other handler: on to the real listener
            elif now == "abort":
                route.abort()
            elif now == "no-remote":
                route.fulfill(status=200, content_type="application/json", body='{"enabled": true}')
            else:
                route.fulfill(status=int(now), content_type="application/json", body='{"error": "fixture"}')
        posts = self.account_posts()
        page.route("**/api/phone-mode", answer)
        mark = len(self.dom_notes)
        page.reload()
        expect(page.locator("#dl-mode")).to_have_text("Chưa rõ PC hay điện thoại", timeout=60 * MS)
        unknown = page.locator("#dl-accounts .dl-mode-unknown")
        check = unknown.locator('[data-action="dl-mode-check"]')
        if variant == "timeout":
            expect(unknown).to_contain_text(CHECKING, timeout=60 * MS)
            expect(check).to_be_disabled()
            self.wait_polls("pc-light", 2)
            expect(unknown).to_contain_text(CHECKING)  # still no answer: still unknown, still no button
            expect(page.locator('[data-action="dl-account"]')).to_have_count(0)
        expect(unknown).to_contain_text(f"({problem})", timeout=60 * MS)
        expect(page.locator('[data-action="dl-account"]')).to_have_count(0)
        expect(row).to_contain_text("Lượt này chờ phiên đăng nhập của")
        expect(row.locator('[data-action="dl-account-show"]')).to_have_count(0)
        self.wait_polls("pc-light", 2)
        self.assertEqual(self.account_posts(), posts)
        self.assertEqual(self.account_requests_sent("pc-light"), [])
        # Since the reload the page drew no account button at any moment: the watch, asked to note the current
        # count again, saw only 0 in this document (never an empty list).
        self.look(page)
        self.assertEqual(set(self.notes("pc-light", "account-buttons", mark)), {"0"}, variant)
        self.shot(page, f"pc-mode-{variant}", full_page=False)

        # Kiểm tra lại: the request it shares or sends is held, then passed on to the real listener.
        mode["now"] = "hold"
        gets = self.log.count("GET", "/api/phone-mode", "pc")
        check.click()
        expect(unknown).to_contain_text(CHECKING, timeout=30 * MS)
        mode["now"] = "pass"
        for route in held:  # fallback (never raises): a request the page gave up on (its 15 s limit) goes nowhere
            route.fallback()
        page.unroute("**/api/phone-mode", answer)
        expect(login).to_be_visible(timeout=60 * MS)
        expect(page.locator("#dl-mode")).to_have_text("Control Center", timeout=60 * MS)
        expect(row.locator('[data-action="dl-account-show"]')).to_be_visible(timeout=60 * MS)
        real = self.wait_until(lambda: self.log.find("GET", "/api/phone-mode", "pc")[gets:], 30,
                               "the real /api/phone-mode answer in the log")
        self.assertTrue(all(json.loads(item["json"])["remote"] is False for item in real), variant)
        self.assertEqual(self.account_posts(), posts)
        self.check(f"unknown_mode_{variant}", {"held": len(held), "real_answers": len(real),
                                               "buttons": self.notes("pc-light", "account-buttons", mark)})

    def assert_sign_in_failed(self, page: Any, waiting: dict[str, Any], code: str, mark: int,
                              wakes: list[float], passes: list[tuple[float, list[int]]], count: int) -> None:
        """After E3's ``count``-th sign-in, which ended ``code``: GET /api/downloads shows that last sign-in and a
        source still not connected; the finish callback woke the dispatcher once more and the look that followed
        woke nothing; over three list polls the badge never read "Đã kết nối" and the page task still waits, in
        its place, for the same (no) generation; no session file exists."""
        from playwright.sync_api import expect

        def ended():
            status, snapshot = self.api("GET", "/api/downloads")
            source = next(item for item in snapshot["accounts"]["sources"] if item["id"] == "alpha")
            return source if (source["last_login"] or {}).get("code") == code and not source["login_running"] \
                else None
        source = self.wait_until(ended, 90, f"the sign-in ending {code}")
        message = LoginOutcome("alpha", code).message
        stored = code if code in LOGIN_END_CODES else "LOGIN_FAILED"  # the manager keeps its own end codes only
        self.assertEqual((source["state"], source["error_code"], source["last_login"]["connected"],
                          source["last_login"]["message"]), ("NOT_CONNECTED", stored, False, message))
        expect(page.locator("#dl-accounts .dl-account-warn")).to_contain_text(message, timeout=60 * MS)
        expect(page.locator("#dl-account-state .badge")).to_have_text("Chưa đăng nhập", timeout=60 * MS)
        self.wait_until(lambda: len(wakes) == count and any(at > wakes[-1] for at, _woken in passes), 60,
                        "the dispatcher's look after the sign-in's callback")
        self.assertEqual(len(wakes), count)
        self.assertEqual({tuple(woken) for at, woken in passes if at > wakes[-1]}, {()})
        self.wait_polls("pc-light", 3)
        task = self.store.get(waiting["id"])
        self.assertEqual((task["state"], task["login_source"], task["login_reason"], task["login_generation"],
                          task["queued_at"]), ("WAITING_LOGIN", "alpha", "NOT_CONNECTED", None, waiting["queued_at"]))
        kinds = self.kinds(waiting["id"])
        self.assertEqual((kinds.count("WAITING_LOGIN"), "LOGIN_RESUMED" in kinds), (1, False))
        # Never "Đã kết nối", even for a moment: the watch notes the badge again first, so the notes since the
        # press end with the badge shown now (a sign-in that failed at once may never have shown another one).
        self.look(page)
        badges = self.notes("pc-light", "badge", mark)
        self.assertNotIn("Đã kết nối", badges)
        self.assertEqual(badges[-1:], ["Chưa đăng nhập"])
        self.assertEqual(stored_sessions(self.manager), [])
        self.assertFalse(self.manager.session_gate("alpha")[0])
        self.assertEqual(self.manager.status("alpha")["state"], "NOT_CONNECTED")
        self.check(f"sign_in_{code}", {"badges": self.notes("pc-light", "badge", mark), "wakes": len(wakes)})

    def test_unknown_mode_and_failed_sign_ins_never_post_or_connect(self):
        """E3. The PC page with a pasted alpha page waiting for a sign-in (fake windows only: no session Edge). (1)
        When GET /api/phone-mode fails in the browser (503, 404, a dropped connection, an answer without
        ``remote``, no answer at all), the page is never taken for the PC: no account button at any moment, no
        sign-in button on the waiting row, no account POST over two polls; Kiểm tra lại brings the real answer and
        the buttons back. (2) Đăng nhập, then Hủy while the window waits for its evidence: one 202, one
        cancel-login 200, and the evidence that comes after the cancel is refused (LOGIN_CANCELLED). (3) The user
        closes the window (LOGIN_WINDOW_CLOSED). (4) The login page fails on the network (LOGIN_PAGE_FAILED). After
        each: never "Đã kết nối", the code in GET /api/downloads, the real callback's wake woke nothing, the task
        still waits for the same (no) generation, no session file."""
        from playwright.sync_api import expect

        site = self.site()  # installed only so that a wrong wake would show as a request for the film page
        self.windows.use("fake", Answers())
        page = self.open_dashboard()
        self.pump = page
        login = page.locator('#dl-accounts [data-action="dl-account"][data-op="login"]')
        expect(login).to_be_visible(timeout=60 * MS)
        page.fill("#dl-urls", site.url)
        page.check("#dl-rights")
        page.click('[data-action="dl-add"]')
        waiting = self.wait_until(lambda: next((task for task in self.store.list_tasks() if task["url"] == site.url
                                                and task["state"] == "WAITING_LOGIN"), None), 60,
                                  "the page waiting for a sign-in")
        self.assertEqual((waiting["login_source"], waiting["login_reason"], waiting["login_generation"]),
                         ("alpha", "NOT_CONNECTED", None))
        row = page.locator(f'article.download-item[data-download-id="{waiting["id"]}"]')
        expect(row.locator('[data-action="dl-account-show"]')).to_be_visible(timeout=60 * MS)
        wakes, passes = self.record_wakes()

        # (1) The page's mode is unknown: nothing that signs in is offered or sent.
        for variant, problem in UNKNOWN_MODES:
            self.check_unknown_mode(page, row, login, variant, problem)
        self.assertEqual((self.windows.calls, wakes, self.account_posts()), ([], [], []))

        login_path, cancel_path = ACCOUNT_PATH.format("login"), ACCOUNT_PATH.format("cancel-login")
        badge = page.locator("#dl-account-state .badge")

        # (2) Đăng nhập, then Hủy while the window waits for the source's evidence; the evidence comes after.
        hold = threading.Event()
        self.addCleanup(hold.set)
        late = Answers(True, before=lambda _calls: hold.wait(60))
        self.windows.use("fake", late)
        mark = len(self.dom_notes)
        login.click()
        self.wait_until(lambda: self.log.count("POST", login_path) == 1, 60, "the press of Đăng nhập")
        started = self.log.find("POST", login_path)[0]
        self.assertEqual(started["status"], 202)
        source = json.loads(started["json"])["source"]
        self.assertEqual((source["state"], source["login_running"]), ("LOGGING_IN", True))  # a 202 is not CONNECTED
        self.wait_until(lambda: late.calls == 1, 60, "the window waiting for the source's evidence")
        expect(badge).to_have_text("Đang đăng nhập", timeout=60 * MS)
        cancel = page.locator('#dl-accounts [data-action="dl-account"][data-op="cancel-login"]')
        expect(cancel).to_be_visible(timeout=60 * MS)
        expect(login).to_have_count(0)
        self.shot(page, "pc-signing-in", full_page=False)
        cancel.click()
        self.wait_until(lambda: self.log.count("POST", cancel_path) == 1, 60, "the press of Hủy đăng nhập")
        cancelled = self.log.find("POST", cancel_path)[0]
        self.assertEqual((cancelled["status"], json.loads(cancelled["json"])["cancelled"]), (200, True))
        hold.set()  # the window's "signed in" arrives now, after the cancel
        self.assert_sign_in_failed(page, waiting, "LOGIN_CANCELLED", mark, wakes, passes, 1)
        self.assertEqual((late.calls, late.answers), (1, []))  # the evidence was given, and not taken

        # (3) The user closes the window; (4) the login page cannot be reached (network).
        cases = (("LOGIN_WINDOW_CLOSED", Answers(), {"closes_after": 1}),
                 ("LOGIN_PAGE_FAILED", Answers(), {"navigate_error": BrowserFailed("NETWORK", PORTAL)}))
        for count, (code, verifier, window) in enumerate(cases, 2):
            self.windows.use("fake", verifier, **window)
            mark = len(self.dom_notes)
            expect(login).to_be_visible(timeout=60 * MS)
            login.click()
            self.wait_until(lambda: self.log.count("POST", login_path) == count, 60, f"Đăng nhập for {code}")
            started = self.log.find("POST", login_path)[-1]
            self.assertEqual(started["status"], 202)
            self.assertNotEqual(json.loads(started["json"])["source"]["state"], "CONNECTED")
            self.assert_sign_in_failed(page, waiting, code, mark, wakes, passes, count)
        self.assertEqual(cases[1][1].calls, 0)  # the page never opened: nothing was asked
        self.shot(page, "pc-sign-in-failed")

        # Three windows, one per press, all fake; one POST per press; nothing reached the film site.
        self.assertEqual((self.windows.calls, self.windows.permits), (["fake"] * 3, ["HeadedPermit"] * 3))
        self.assertEqual(self.account_posts(), [("pc", login_path, 202), ("pc", cancel_path, 200),
                                                ("pc", login_path, 202), ("pc", login_path, 202)])
        self.assertEqual(self.account_requests_sent("pc-light"),
                         [("POST", login_path), ("POST", cancel_path), ("POST", login_path), ("POST", login_path)])
        self.assertEqual((self.server.requests, self.resolver.calls), ([], []))
        badges = self.notes("pc-light", "badge")
        self.assertNotIn("Đã kết nối", badges)
        self.assertEqual(badges[-1:], ["Chưa đăng nhập"])  # the last look above: the watch was alive throughout
        self.keep_page_text(page)
        self.assert_no_scan_and_no_yt_dlp()
        self.secret_scan()
        self.assert_network_kept()
        self.assert_routes_kept()
        # Console errors only for the answers (1) made up in the browser for /api/phone-mode.
        self.assert_page_clean(r" @ /api/phone-mode$")
        self.results["completed"] = True


if __name__ == "__main__":
    unittest.main()
