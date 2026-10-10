"""The acceptance observation of one ticket (M7, download_account_observe): one click, the ticket page that answers
it watched, its bare file host saved only when asked, and that same link probed within 1 MiB only when asked.

The film portal is the self-made fixture of tests/release_forms_fixtures.py (``.example`` hosts, made-up ids and
titles) on real headless Edge, as in the release-forms and probe tests; the observation's own root holds a
source config without a file host, like the real source's before M7. Nothing here is a real site, session or
download.
"""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from biliflow.download_account_browser import effective_submission
from biliflow.download_account_config import AdapterSpec, SourceAccount, read_account_config
from biliflow import download_account_observe as observe_module
from biliflow.download_account_listing import FileSelection, TicketPage
from biliflow.download_account_observe import (
    ObserveFlow,
    ObservingProvider,
    ObservingReader,
    RequestWatch,
    link_facts,
    main,
    observe,
    save_file_host,
    shape,
    text_facts,
)
from biliflow.download_account_probe import MARKER, PROBE_BUDGET_BYTES, ProbeRefused
from biliflow.download_account_release_forms import RELEASE_FORMS_READER
from biliflow.download_account_vault import SessionVault
from biliflow.download_accounts import AccountManager
from biliflow.download_http import SafeHttp
from biliflow.download_media_file import FileTransfer
from biliflow.download_source_types import SourceError
from tests.release_forms_fixtures import TOKEN_VALUE, Card, ReleaseSite
from tests.source_fixtures import FFPROBE, public_resolver
from tests.test_download_account_browser import CANARY_SID, DATA, FILES, FIRST, PORTAL, TEMP_PARENT, TICKETS, cookie
from tests.test_download_accounts import FakeProtector, GoodAcl
from tests.test_download_account_release_forms import (
    ReleaseCase,
    setUpModule as release_setup,
    tearDownModule as release_teardown,
)

MIB = 1024 * 1024
SCRIPT_URL = "https://ads.script.example/gate.js"
SOURCE = SourceAccount("alpha", AdapterSpec("release-forms", "ttl"), "Nguồn alpha", f"https://{PORTAL}/login",
                       {"portal": (PORTAL,), "tickets": (TICKETS,), "files": ()}, SCRIPT_URL)


def setUpModule():
    release_setup()


def tearDownModule():
    release_teardown()


def write_config(root: Path, files: list[str] | None = None) -> Path:
    path = root / "config" / "download_accounts.local.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sources": {"alpha": {
        "adapter": "release-forms", "label": "Nguồn alpha", "login_url": f"https://{PORTAL}/login",
        "hosts": {"portal": [PORTAL], "tickets": [TICKETS], "files": files or []}}}}, indent=2), encoding="utf-8")
    return path


# ------------------------------------------------------------------------------------------ no browser
class ShapeTest(unittest.TestCase):
    ROLES = {PORTAL: "portal", TICKETS: "tickets"}

    def test_a_shape_keeps_roles_words_and_masks_ids_and_tokens(self):
        self.assertEqual(shape(f"https://{PORTAL}/download-links/3401/access", self.ROLES),
                         "https:portal:/download-links/{n}/access")
        self.assertEqual(shape(f"https://{TICKETS}/x/01ab9f3c2d7e4f5a6b7c8d9e0f1a2b/download?sig=abc", self.ROLES),
                         "https:tickets:/x/{tok}/download?{q}")
        self.assertEqual(shape("https://elsewhere.example/a1/Play", self.ROLES), "https:other:/{tok}/play")
        # a plain word of a path may be a name: only the structural words of PATH_WORDS are kept
        self.assertEqual(shape(f"https://{PORTAL}/phim/ten-phim-mau/abcdefghijklmnop", self.ROLES),
                         "https:portal:/phim/{tok}/{tok}")
        self.assertIsNone(shape(None, self.ROLES))

    def test_text_facts_keep_only_numbers_and_words_of_the_vocabulary(self):
        facts = text_facts("Link hết hạn sau 1 giờ; tải tiếp trong 12 giờ trên cùng IP 203.0.113.5 hoặc "
                           "2001:db8::7 từ phim.example, user@mail.example, Ten.Phim.2024.1080p.mkv, mã "
                           "01ab9f3c2d7e4f5a6b7c")
        text = json.dumps(facts, ensure_ascii=False)
        for secret in ("phim.example", "phim", "user", "mail", "203", "113", "db8", "2024", "1080", "mkv", "01ab"):
            self.assertNotIn(secret, text)
        self.assertEqual((facts["ip_address"], facts["numbers"]), (True, ["1", "12"]))
        self.assertTrue({"hết hạn", "giờ", "tải", "tiếp", "ip", "link"} <= set(facts["words"]))
        self.assertTrue(text_facts("cùng IP 203.0.113.5.")["ip_address"])  # before a full stop too
        countdown = text_facts("Vui lòng chờ 00:15 (5 giây)")
        self.assertEqual((countdown["ip_address"], countdown["numbers"]), (False, ["00", "15", "5"]))
        self.assertIsNone(text_facts(""))


class LinkFactsTest(unittest.TestCase):
    def test_only_an_https_link_on_a_host_of_no_other_role_gives_its_bare_host(self):
        host, facts = link_facts(f"https://{FILES}/x/01ab9f3c2d7e4f5a6b7c8d/download", SOURCE)
        self.assertEqual(host, FILES)
        self.assertEqual((facts["https"], facts["default_port"], facts["query"]), (True, True, False))
        self.assertEqual(facts["shape"], "https:other:/x/{tok}/download")
        self.assertNotIn(FILES, json.dumps(facts))
        for link in (f"http://{FILES}/x/t/download", f"https://{FILES}:8443/x/t/download",
                     f"https://user@{FILES}/x/t/download", f"https://{PORTAL}/x/t/download",
                     f"https://{TICKETS}/x/t/download", "https://ads.script.example/x/t/download", None, "nope"):
            with self.subTest(link=link):
                self.assertIsNone(link_facts(link, SOURCE)[0])


class SaveHostTest(unittest.TestCase):
    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        self._root = TemporaryDirectory(dir=TEMP_PARENT, prefix="account-observe-")
        self.addCleanup(self._root.cleanup)
        self.root = Path(self._root.name)

    def test_the_bare_host_is_written_alone_into_an_empty_file_role(self):
        path = write_config(self.root)
        self.assertEqual(save_file_host(self.root, "alpha", FILES), {"saved": True, "already": False})
        config = read_account_config(self.root)
        self.assertEqual((config.problems, config.sources["alpha"].hosts["files"]), ((), (FILES,)))
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["sources"]["alpha"]["hosts"]["files"], [FILES])
        self.assertEqual(save_file_host(self.root, "alpha", FILES), {"saved": True, "already": True})
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_another_host_in_the_file_role_is_never_replaced(self):
        path = write_config(self.root, ["cdn.alpha.example"])
        before = path.read_bytes()
        self.assertEqual(save_file_host(self.root, "alpha", FILES)["reason"], "FILES_ROLE_HOLDS_ANOTHER_HOST")
        self.assertEqual(path.read_bytes(), before)

    def test_a_host_the_config_refuses_leaves_the_file_as_it_was(self):
        path = write_config(self.root)
        before = path.read_bytes()
        result = save_file_host(self.root, "alpha", PORTAL)  # a portal host cannot also be a file host
        self.assertEqual(result["reason"], "CONFIG_INVALID_RESTORED")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(save_file_host(self.root, "missing", FILES)["reason"], "CONFIG_UNREADABLE")

    def test_a_config_that_cannot_be_replaced_stays_as_it_was_without_a_temporary_file(self):
        path = write_config(self.root)
        before = path.read_bytes()
        with mock.patch.object(observe_module.os, "replace", side_effect=PermissionError("held")):
            self.assertEqual(save_file_host(self.root, "alpha", FILES)["reason"], "CONFIG_WRITE_FAILED")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual([item.name for item in path.parent.iterdir() if item.name.endswith(".tmp")], [])


class PiecesTest(unittest.TestCase):
    def test_the_watch_counts_and_refuses_file_and_player_paths(self):
        watch = RequestWatch({PORTAL: "portal", TICKETS: "tickets"})
        self.assertIsNone(watch("GET", f"https://{PORTAL}/download-links/1/access", "document"))
        self.assertEqual(watch("GET", f"https://{TICKETS}/x/t/download", "document"), "OBSERVE_REFUSED")
        self.assertEqual(watch("GET", f"https://{TICKETS}/play/t", "media"), "OBSERVE_REFUSED")
        watch.click()
        watch("POST", f"https://{PORTAL}/download-links/1/access#x", "document")
        self.assertEqual((watch.asked(f"https://{PORTAL}/download-links/1/access", after=False),
                          watch.asked(f"https://{PORTAL}/download-links/1/access", after=True)), (1, 1))
        self.assertEqual((watch.posts(after=False), watch.posts(after=True)), (0, 1))
        summary = watch.summary()
        self.assertEqual((summary["refused"], summary["submitted"], summary["kept_whole"]), (2, True, True))
        self.assertNotIn(PORTAL, json.dumps(summary))

    def test_the_first_request_but_a_get_writes_the_marker_before_it_is_answered(self):
        marks = []
        watch = RequestWatch({}, on_submit=lambda: marks.append("mark"))
        self.assertIsNone(watch("GET", f"https://{PORTAL}/phim/1", "document"))
        self.assertEqual(marks, [])
        self.assertIsNone(watch("POST", f"https://{PORTAL}/ajax/x", "xhr"))
        self.assertIsNone(watch("POST", f"https://{PORTAL}/download-links/1/access", "document"))
        self.assertEqual(marks, ["mark"])  # once

        def broken():
            raise OSError("disk")

        watch = RequestWatch({}, on_submit=broken)
        self.assertEqual(watch("POST", f"https://{PORTAL}/download-links/1/access", "document"),
                         "OBSERVE_MARK_FAILED")  # no marker, no such request
        self.assertEqual(watch("PUT", f"https://{PORTAL}/x", "xhr"), "OBSERVE_MARK_FAILED")
        self.assertIsNone(watch("GET", f"https://{PORTAL}/phim/1", "document"))
        self.assertTrue(watch.summary()["mark_failed"])

    def test_a_watch_that_cannot_count_every_url_says_so(self):
        with mock.patch.object(observe_module, "MAX_URLS", 2):
            watch = RequestWatch({})
            for number in range(3):
                watch("GET", f"https://{PORTAL}/p/{number}", "image")
            watch("GET", f"https://{PORTAL}/p/0", "image")  # a URL already counted is still counted
        self.assertTrue(watch.dropped)
        self.assertEqual((watch.asked(f"https://{PORTAL}/p/0", after=False), watch.summary()["kept_whole"]),
                         (2, False))

    def test_the_last_state_of_the_ticket_page_is_recorded_past_the_limit(self):
        flow = object.__new__(ObserveFlow)  # only its record: no browser
        flow.events, flow.ticks, flow._last_tick, flow.clicked_at = [], [], None, 0.0
        with mock.patch.object(observe_module, "MAX_EVENTS", 2), mock.patch.object(observe_module, "MAX_TICKS", 2):
            for number, state in enumerate(("loading", "loading", "checking", "busy")):
                flow._record({"data_state": state, "countdown": [str(number)], "button_text": None})
            flow._record({"data_state": "success", "countdown": [], "button_text": None}, final=True)
        self.assertEqual([event["page"]["data_state"] for event in flow.events], ["loading", "checking", "success"])
        self.assertEqual([tick["countdown"] for tick in flow.ticks], [["0"], ["1"]])

    def test_the_observing_reader_never_shows_a_ready_link_and_its_provider_never_resolves(self):
        class Ready:
            shows_ticket_ids = False

            def ticket_page(self, view):
                return TicketPage(None, None, "ready", "https://files.alpha.example/x/t/download")

        reader = ObservingReader(Ready())
        self.assertEqual((reader.ticket_page(None).state, reader.ticket_page(None).link), ("waiting", None))
        self.assertEqual((reader.reads_tickets, reader.shows_ticket_ids), (True, False))
        ObservingReader(RELEASE_FORMS_READER)
        self.assertEqual(vars(RELEASE_FORMS_READER), {})  # the production reader is left as it is: nothing set on it
        provider = ObservingProvider(SOURCE, None, watch=RequestWatch({}))  # type: ignore[arg-type]
        with self.assertRaises(SourceError) as caught:
            provider.resolve(f"https://{PORTAL}/phim/1", None)  # type: ignore[arg-type]
        self.assertEqual(caught.exception.code, "OBSERVE_ONLY")

    def test_the_submission_a_click_sends_follows_html(self):
        base = {"tag": "button", "type": "submit", "disabled": False, "form": True, "formaction": None,
                "formmethod": None, "action": f"https://{PORTAL}/download-links/1/access", "method": "post",
                "document": f"https://{PORTAL}/phim/1", "base": f"https://{PORTAL}/phim/1"}
        action = f"https://{PORTAL}/download-links/1/access"
        cases = [
            ({}, (action, "POST")),
            ({"type": None}, (action, "POST")),  # a button submits by default
            ({"formaction": "/download-links/2/access"}, (f"https://{PORTAL}/download-links/2/access", "POST")),
            ({"formaction": ""}, (f"https://{PORTAL}/phim/1", "POST")),  # an empty action is the document
            ({"formmethod": "get"}, (action, "GET")),
            ({"formmethod": "bogus"}, (action, "GET")),
            ({"method": None}, (action, "GET")),
            ({"action": "../x#frag"}, (f"https://{PORTAL}/x", "POST")),
            ({"type": "button"}, None), ({"disabled": True}, None), ({"form": False}, None),
            ({"method": "dialog"}, None), ({"tag": "input", "type": "submit"}, (action, "POST")),
            ({"tag": "input", "type": "text"}, None), ({"tag": "input", "type": "image"}, (action, "POST")),
            ({"formmethod": ""}, (action, "GET")),  # an empty formmethod is an invalid one: GET
        ]
        for change, expected in cases:
            with self.subTest(change=change):
                self.assertEqual(effective_submission({**base, **change}), expected)


# ----------------------------------------------------------------------------------------- browser
class ObserveTest(ReleaseCase):
    def setUp(self):
        super().setUp()
        transfer = mock.patch.object(FileTransfer, "run", side_effect=AssertionError("a transfer started"))
        transfer.start()
        self.addCleanup(transfer.stop)
        # The observation's own root: a config like the real source's before M7 (no file host yet).
        self.obs_root = self.root / "observe-root"
        self.config_path = write_config(self.obs_root)
        config = read_account_config(self.obs_root)
        self.assertEqual(config.problems, ())
        self.obs_manager = AccountManager(self.obs_root, config, vault=SessionVault(
            self.obs_root, protector=FakeProtector(), acl=GoodAcl(FIRST)), clock=self.clock)
        self.addCleanup(self.obs_manager.close)
        self.obs_manager.complete_login(self.obs_manager.begin_login("alpha"),
                                        {"cookies": [cookie("sid", CANARY_SID, ".alpha.example")], "origins": []})

    def run_observe(self, site: ReleaseSite, **options) -> dict:
        http = SafeHttp(resolver=public_resolver, connector=self.server.connector,
                        ssl_context=DATA["tls"].client_context())
        run = {"network": self.network, "browser_options": {"page_seconds": 10}, "run_seconds": 90,
               "grace_seconds": 2}
        return observe(self.obs_root, site.url, http=http, manager=self.obs_manager, run_options=run,
                       ffprobe=FFPROBE, **options)

    def requests_to(self, host: str) -> list:
        return [item for item in self.server.requests if item.host == host]

    def files_role(self) -> list:
        return json.loads(self.config_path.read_text(encoding="utf-8"))["sources"]["alpha"]["hosts"]["files"]

    def assert_nothing_secret(self, report: dict, site: ReleaseSite) -> None:
        text = json.dumps(report, ensure_ascii=False)
        for secret in ("https://", PORTAL, TICKETS, FILES, TOKEN_VALUE, CANARY_SID, "Film.Example", "Phim mẫu",
                       "203.0", "user@", "mail.example", *site.tokens):
            self.assertNotIn(secret, text)
        written = (self.obs_root / report["report_file"]).read_text(encoding="utf-8")
        for secret in (PORTAL, TICKETS, FILES, CANARY_SID, *site.tokens):
            self.assertNotIn(secret, written)

    def test_a_ready_ticket_is_watched_after_one_post_and_nothing_reaches_the_file_host(self):
        notes = ("<p>Link chưa dùng hết hạn sau 1 giờ. Tải tiếp được trong 12 giờ trên cùng IP 203.0.113.5.</p>"
                 '<span class="account-name">user@mail.example</span>')
        site = self.site(film_cards=[Card("3401", "Film.Example.mkv", None)], countdown=1.5, ticket_extra=notes)

        report = self.run_observe(site)

        self.assertEqual(report["outcome"], "READY", report)
        self.assertEqual((site.posts, site.downloads, self.requests_to(FILES)), (["3401"], [], []))
        self.assertEqual((report["clicks"], report["reveal_clicks"], report["ticket_marker"]), (1, 0, True))
        self.assertEqual(json.loads((self.obs_root / MARKER).read_text(encoding="utf-8"))["outcome"], "READY")
        facts = report["observation"]
        said = facts["page"]["notes"]
        self.assertIn(text_facts("Link chưa dùng hết hạn sau 1 giờ."), said)
        self.assertEqual(text_facts("Link chưa dùng hết hạn sau 1 giờ.")["words"], ["giờ", "hết hạn", "link"])
        self.assertTrue(any(note["ip_address"] and note["numbers"] == ["12"] for note in said), said)
        self.assertGreaterEqual(len(facts["ticks"]), 1)
        self.assertEqual(facts["events"][0]["page"]["counters"], 1)
        first, last = facts["events"][0]["page"], facts["events"][-1]["page"]
        self.assertEqual((first["reader"], first["data_state"], first["locked_class"], first["aria_disabled"]),
                         ("waiting", "loading", True, "true"))
        self.assertEqual((last["reader"], last["data_state"], last["locked_class"], last["aria_disabled"]),
                         ("ready", "success", False, None))
        self.assertEqual(last["href"], "https:other:/x/{tok}/download")
        self.assertGreater(facts["events"][-1]["t"], 1.0)  # the page's own countdown, not hurried
        self.assertEqual(facts["requests_to_entry"], {"before_click": 0, "after_click": 1})
        post = facts["chain"][0]
        self.assertEqual((post["method"], post["to"], post["frame"], post["target"]),
                         ("POST", "https:portal:/download-links/{n}/access", "new-page", "https:tickets:/x/{tok}"))
        self.assertEqual([(step["method"], step["frame"], step["status"]) for step in facts["chain"][1:]],
                         [("GET", "ticket", 200)])
        self.assertEqual(report["link"]["shape"], "https:other:/x/{tok}/download")
        self.assertEqual(report["host_saved"], {"saved": False, "reason": "NOT_ASKED"})
        self.assertEqual(self.files_role(), [])
        self.assert_nothing_secret(report, site)

    def test_the_bare_host_is_saved_and_the_same_ticket_is_probed_within_the_budget(self):
        site = self.site(film_cards=[Card("3402", "Film.Example.mkv", None)])

        report = self.run_observe(site, save_host=True, probe_same_ticket=True)

        self.assertEqual(report["outcome"], "READY", report)
        self.assertEqual(report["host_saved"], {"saved": True, "already": False})
        self.assertEqual(self.files_role(), [FILES])
        self.assertEqual(read_account_config(self.obs_root).problems, ())
        self.assertEqual(site.posts, ["3402"])  # one ticket only
        files = self.requests_to(FILES)
        self.assertEqual([item.headers.get("range") for item in files], [f"bytes=0-{MIB - 1}"])
        self.assertEqual([(item.headers.get("cookie"), item.headers.get("referer")) for item in files], [(None, None)])
        self.assertEqual([token for token, _at in site.downloads], site.tokens_of("3402"))  # that same ticket
        probe = report["probe"]
        self.assertEqual(probe["outcome"], "PROBED", probe)
        self.assertLessEqual(probe["budget"]["used"], PROBE_BUDGET_BYTES)
        self.assertEqual((probe["file"]["container"], probe["file"]["ranges"], probe["file"]["validator"]),
                         ("matroska", True, "etag"))
        self.assertEqual(list(self.obs_root.rglob("media.part")), [])
        self.assert_nothing_secret(report, site)

    def test_a_blocked_ticket_page_is_a_challenge_without_a_host_or_a_probe(self):
        site = self.site(film_cards=[Card("3403", "Film.Example.mkv", None)], ticket_end="blocked")

        report = self.run_observe(site, save_host=True, probe_same_ticket=True)

        self.assertEqual(report["outcome"], "TICKET_CHALLENGE")
        self.assertEqual((site.posts, site.downloads, self.files_role()), (["3403"], [], []))
        self.assertNotIn("probe", report)
        self.assertEqual(report["observation"]["events"][-1]["page"]["data_state"], "blocked")

    def test_a_page_that_submits_by_itself_before_the_click_is_never_clicked(self):
        site = self.site(film_cards=[Card("3404", "Film.Example.mkv", None)], auto_submit=True)

        report = self.run_observe(site)

        self.assertEqual(report["outcome"], "UNEXPECTED_SUBMIT", report)
        # no click, but the page's own submit may have asked for a ticket: the marker was written before it
        self.assertEqual((report["clicks"], report["ticket_marker"], report["requests"]["submitted"]), (0, True, True))
        self.assertEqual(site.posts, ["3404"])  # the page's own submit, never BiliFlow's click
        with self.assertRaises(ProbeRefused):
            self.run_observe(site)
        self.assertEqual(site.posts, ["3404"])

    def test_a_button_that_submits_another_form_is_never_clicked(self):
        elsewhere = (f'<form id="elsewhere" method="post" action="https://{PORTAL}/download-links/9405/access">'
                     "</form>")
        site = self.site(film_cards=[Card("3405", "Film.Example.mkv", None, button=' form="elsewhere"')],
                         extra_html=elsewhere)

        report = self.run_observe(site)

        self.assertEqual(report["outcome"], "TICKET_TARGET_CHANGED", report)
        self.assertEqual((site.posts, report["clicks"], report["ticket_marker"]), ([], 0, False))

    def test_a_second_submit_by_the_page_ends_the_watch_and_no_link_is_kept(self):
        site = self.site(film_cards=[Card("3406", "Film.Example.mkv", None)], popups=("double-submit",),
                         countdown=2.0)

        report = self.run_observe(site, save_host=True, probe_same_ticket=True)

        self.assertEqual(report["outcome"], "EXTRA_SUBMIT", report)
        self.assertEqual(site.posts, ["3406", "3406"])  # the click's and the page's second one
        self.assertEqual((site.downloads, self.files_role()), ([], []))
        self.assertNotIn("link", report)

    def test_file_and_player_paths_are_refused_in_the_browser(self):
        extra = (f'<img src="https://{TICKETS}/x/play/poster.png">'
                 f'<iframe src="https://{TICKETS}/download/frame"></iframe>')
        site = self.site(film_cards=[Card("3407", "Film.Example.mkv", None)], ticket_extra=extra)

        report = self.run_observe(site)

        self.assertEqual(report["outcome"], "READY", report)
        paths = [item.path for item in self.requests_to(TICKETS)]
        self.assertFalse([path for path in paths if "/play" in path or path.startswith("/download")], paths)
        self.assertGreaterEqual(report["requests"]["refused"], 2)

    def test_the_ticket_hosts_sign_in_chain_is_watched_as_the_clicks_own(self):
        # The shape M7 recorded on the real source: six redirects after the POST, through the ticket host's own
        # sign-in from the portal's session; its cookie appears only after the click.
        self.obs_manager.complete_login(self.obs_manager.begin_login("alpha"),
                                        {"cookies": [cookie("sid", CANARY_SID, PORTAL)], "origins": []})
        site = self.site(film_cards=[Card("3411", "Film.Example.mkv", None)], ticket_sso=True)

        report = self.run_observe(site)

        self.assertEqual(report["outcome"], "READY", report)
        facts = report["observation"]
        self.assertEqual([(step["method"], step["to"]) for step in facts["chain"]], [
            ("POST", "https:portal:/download-links/{n}/access"), ("GET", "https:tickets:/x/{tok}"),
            ("GET", "https:tickets:/login?{q}"), ("GET", "https:tickets:/{tok}/{tok}?{q}"),
            ("GET", "https:portal:/{tok}/{tok}/{tok}?{q}"), ("GET", "https:tickets:/{tok}/{tok}/{tok}?{q}"),
            ("GET", "https:tickets:/x/{tok}")])
        self.assertEqual(facts["chain"][-1]["status"], 200)
        self.assertEqual((facts["cookies"]["before_click"], facts["cookies"]["after"]),
                         ({"portal": 1}, {"portal": 1, "tickets": 1}))
        self.assertEqual((site.posts, site.sso_logins, site.downloads), (["3411"], 1, []))
        self.assert_nothing_secret(report, site)

    def test_a_folded_seasons_opening_click_is_not_the_ticket_click(self):
        site = self.two_seasons()

        report = self.run_observe(site, chosen=FileSelection("alpha", "501", "s2:e2", "2201"))

        self.assertEqual(report["outcome"], "READY", report)
        self.assertEqual((report["clicks"], report["reveal_clicks"], site.posts), (1, 1, ["2201"]))
        self.assertEqual(report["observation"]["requests_to_entry"], {"before_click": 0, "after_click": 1})
        self.assertEqual(report["observation"]["non_get_requests"], {"before_click": 0, "after_click": 1})

    def test_a_failure_after_the_ready_state_still_writes_the_report(self):
        site = self.site(film_cards=[Card("3410", "Film.Example.mkv", None)])
        with mock.patch.object(observe_module, "save_file_host", side_effect=RuntimeError("disk")):
            report = self.run_observe(site, save_host=True, probe_same_ticket=True)
        self.assertEqual((report["outcome"], report["after_ready_error"]), ("READY", "RuntimeError"), report)
        self.assertNotIn("probe", report)
        self.assertEqual((site.posts, site.downloads), (["3410"], []))
        self.assertTrue((self.obs_root / report["report_file"]).is_file())
        self.assertEqual(json.loads((self.obs_root / MARKER).read_text(encoding="utf-8"))["outcome"], "READY")

    def test_a_ticket_page_that_never_gets_ready_times_out_without_a_link(self):
        site = self.site(film_cards=[Card("3408", "Film.Example.mkv", None)], ticket_end="loading")
        http = SafeHttp(resolver=public_resolver, connector=self.server.connector,
                        ssl_context=DATA["tls"].client_context())
        run = {"network": self.network, "browser_options": {"page_seconds": 10}, "run_seconds": 30,
               "grace_seconds": 2}

        report = observe(self.obs_root, site.url, http=http, manager=self.obs_manager, run_options=run,
                         save_host=True)

        self.assertEqual(report["outcome"], "TICKET_TIMEOUT", report)
        self.assertEqual((site.posts, site.downloads, self.files_role()), (["3408"], [], []))
        self.assertEqual(report["observation"]["events"][-1]["page"]["reader"], "waiting")

    def test_a_root_that_may_have_spent_its_ticket_asks_for_no_other(self):
        site = self.site(film_cards=[Card("3409", "Film.Example.mkv", None)])
        (self.obs_root / MARKER).parent.mkdir(parents=True, exist_ok=True)
        (self.obs_root / MARKER).write_text("{}", encoding="utf-8")
        with self.assertRaises(ProbeRefused) as caught:
            self.run_observe(site)
        self.assertEqual(caught.exception.code, "TICKET_ALREADY_ASKED")
        self.assertEqual(site.posts, [])


class CommandLineTest(unittest.TestCase):
    def test_the_command_line_refuses_without_reading_anything(self):
        for argv in (["--root", "x", "--url", "https://a.example/p", "--probe-same-ticket"],
                     ["--root", "x"], ["--root", "x", "--url", "u", "--url-file", "f"],
                     ["--root", "x", "--url", "u", "--budget", str(MIB + 1)],
                     ["--root", "x", "--url", "u", "--seconds", "nan"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), \
                    mock.patch("sys.stderr", io.StringIO()):
                main(argv)
        with self.assertRaises(ValueError):
            observe(TEMP_PARENT, "https://a.example/p", probe_same_ticket=True)


if __name__ == "__main__":
    unittest.main()
