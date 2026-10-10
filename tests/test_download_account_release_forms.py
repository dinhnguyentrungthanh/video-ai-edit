"""Adapter "release-forms" (M7): its page reader and sign-in verifier, and the season opening of ``get_ticket``.

The film portal is the self-made fixture of tests/release_forms_fixtures.py (``.example`` hosts, made-up ids
and titles), served through the session browser's checked client on real headless Edge. Its ticket flow copies
the structure M7 saw once on the real source (a POST that redirects to a ticket page on the ticket host, which
names no file id; a download button locked by a countdown). The production reader asks for tickets since that
contract was observed (``reads_tickets`` True). ``ProductionReaderTest`` runs the registered reader itself, as
the product picks it for a source configured with adapter "release-forms"; the click path's other tests use
``TicketReader`` (its class with the ticket path pinned on, so a read can be changed) and ``ListOnlyReader``
(the ticket path off: refused before any click). Nothing here is a real site, session or download.
"""
from __future__ import annotations

import json
import time
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from biliflow.download_account_browser import SessionBrowser
from biliflow.download_account_config import read_account_config
from biliflow.download_account_edge import BrowserFailed
from biliflow.download_account_listing import FileSelection, FilmPage, ListingBuilder, RawEntry, plan_selection
from biliflow.download_account_login import LOGIN_VERIFIERS, Fetched, LoginView
from biliflow.download_account_pages import PAGE_READERS
from biliflow.download_account_release_forms import (
    NOTIFICATIONS_PATH,
    RELEASE_FORMS_READER,
    RELEASE_FORMS_VERIFIER,
    FilmPageReader,
    notifications_answer,
)
from biliflow.download_account_sources import AccountSourceProvider, SourceNeedsEpisodes
from biliflow.download_account_vault import SessionVault
from biliflow.download_accounts import AccountManager
from biliflow.download_http import Cancelled, HttpError, SafeHttp
from biliflow.download_runner import ProcessControl
from biliflow.download_source_types import ResolveContext, SourceError, SourceLoginRequired
from tests.release_forms_fixtures import TOKEN_VALUE, Card, ReleaseSite, season_cards
from tests.source_fixtures import FFPROBE, HAVE_FFMPEG, NEED_FFMPEG, Reply, make_clip, public_resolver
from tests.test_download_account_browser import (
    ALPHA,
    CANARY_SID,
    DATA,
    FILES,
    FIRST,
    HAVE_BROWSER,
    NEED_BROWSER,
    PORTAL,
    TEMP_PARENT,
    TICKETS,
    BrowserCase,
    cookie,
    setUpModule as browser_setup,
    tearDownModule as browser_teardown,
)
from tests.test_download_accounts import FakeProtector, GoodAcl

CLIP: dict[str, bytes] = {}
TICKET_COOKIE = "BF-CANARY-tk-3e1"
FILM_URL = f"https://{PORTAL}/phim/501"
GATE_SCRIPT_PATH = "/js/gate.js"
GATE_SCRIPT = f"https://ads.example{GATE_SCRIPT_PATH}"  # the source's page script (M7 exception A), as configured


def setUpModule():
    browser_setup()
    if HAVE_FFMPEG:
        DATA["release_clips"] = TemporaryDirectory(dir=TEMP_PARENT, prefix="release-clips-")
        CLIP["mkv"] = make_clip(Path(DATA["release_clips"].name) / "clip.mkv", seconds=2,
                                container="mkv").read_bytes()


def tearDownModule():
    if "release_clips" in DATA:
        DATA.pop("release_clips").cleanup()
    browser_teardown()


class TicketReader(FilmPageReader):
    """The production reader's class (list and ticket page) with its ticket path pinned on: the click path's tests
    and the subclasses below that change a read do not depend on the production switch."""
    reads_tickets = True


class ListOnlyReader(FilmPageReader):
    """The production reader's class with its ticket path off, like a reader whose ticket page was not checked:
    ``get_ticket`` refuses before any click (TICKET_UNSUPPORTED). The production reader itself is never changed."""
    reads_tickets = False


class NoRequestReader(TicketReader):
    """Reads every entry without its ``request``: a ticket page without ids can then never be tied to a click."""

    def film_page(self, view):
        film = super().film_page(view)
        return film and replace(film, entries=tuple(replace(item, request=None, request_method=None)
                                                    for item in film.entries))


class StepReader(TicketReader):
    """Calls ``step(view)`` when the film page is read the ``at``-th time (the read after the season opened)."""

    def __init__(self, at: int, step):
        self.at, self.step, self.reads = at, step, 0

    def film_page(self, view):
        self.reads += 1
        if self.reads == self.at:
            self.step(view)
        return super().film_page(view)


class ChangedReader(TicketReader):
    """From its ``at``-th film page read on, shows every entry with ``changes`` (the page changed between reading
    the list and the click)."""

    def __init__(self, at: int, **changes):
        self.at, self.changes, self.reads = at, changes, 0

    def film_page(self, view):
        film = super().film_page(view)
        self.reads += 1
        if film is None or self.reads < self.at:
            return film
        return replace(film, entries=tuple(replace(item, **self.changes) for item in film.entries))


def use_up_run(view, left: float = 12.0):
    """Sleep until only ``left`` seconds of the run remain: less than a ticket's least time plus the run's
    closing reserve (download_account_pages: MIN_TICKET_SECONDS + FINISH_RESERVE_SECONDS = 15)."""
    stop_at = view._browser.stop_at  # the test reaches the run's deadline through the view's browser
    time.sleep(max(stop_at - left - time.monotonic(), 0.0))


# ------------------------------------------------------------------------------------------ no browser
class NotificationsAnswerTest(unittest.TestCase):
    def test_only_the_exact_schema_with_a_whole_non_negative_count_is_evidence(self):
        cases = {
            '{"notifications": [], "unread_count": 0}': True,
            '{"notifications": [{"id": 1}], "unread_count": 3}': True,
            '{"notifications": [], "unread_count": true}': False,  # a bool is not a count
            '{"notifications": [], "unread_count": -1}': False,
            '{"notifications": [], "unread_count": 1.0}': False,
            '{"notifications": [], "unread_count": "0"}': False,
            '{"notifications": {}, "unread_count": 0}': False,
            '{"notifications": [], "unread_count": 0, "user": "x"}': False,
            '{"unread_count": 0}': False,
            '[]': False,
            '<!doctype html><p>trang chủ</p>': False,
            '': False,
        }
        for body, expected in cases.items():
            with self.subTest(body=body):
                self.assertIs(notifications_answer(body), expected)


class FakeView:
    def __init__(self, url, left=True, answer=None):
        self.url, self.left, self.answer, self.fetched = url, left, answer, []

    def left_sign_in(self):
        return self.left

    def fetch(self, url):
        self.fetched.append(url)
        return self.answer


class VerifierTest(unittest.TestCase):
    GOOD = Fetched(200, '{"notifications": [], "unread_count": 0}')

    def test_signed_in_only_on_the_fresh_notifications_answer_of_the_shown_portal_host(self):
        view = FakeView(f"https://{PORTAL}/", answer=self.GOOD)
        self.assertTrue(RELEASE_FORMS_VERIFIER.signed_in(view))
        self.assertEqual(view.fetched, [f"https://{PORTAL}{NOTIFICATIONS_PATH}"])

    def test_still_on_the_sign_in_page_is_never_evidence_and_fetches_nothing(self):
        view = FakeView(f"https://{PORTAL}/login", left=False, answer=self.GOOD)
        self.assertFalse(RELEASE_FORMS_VERIFIER.signed_in(view))
        self.assertEqual(view.fetched, [])

    def test_a_redirect_a_failure_or_another_answer_is_not_evidence(self):
        for answer in (None, Fetched(0, ""), Fetched(302, ""), Fetched(401, '{"message": "Unauthenticated."}'),
                       Fetched(200, "<!doctype html><p>Xin chào</p>"), Fetched(200, '{"unread_count": 0}')):
            with self.subTest(answer=answer):
                self.assertFalse(RELEASE_FORMS_VERIFIER.signed_in(FakeView(f"https://{PORTAL}/", answer=answer)))


class LeftSignInTest(unittest.TestCase):
    @staticmethod
    def view(url):
        page = SimpleNamespace(url=url)
        return LoginView(SimpleNamespace(source=ALPHA, context=SimpleNamespace(pages=[page])))

    def test_only_a_portal_page_other_than_the_sign_in_page(self):
        cases = {f"https://{PORTAL}/": True, f"https://{PORTAL}/phim/501": True,
                 f"https://{PORTAL}/login": False, f"https://{PORTAL}/login/": False,
                 f"https://{PORTAL}/login?error=1": False, f"https://{TICKETS}/": False,
                 f"https://{FILES}/": False, f"http://{PORTAL}/": False, f"https://{PORTAL}:8443/": False,
                 "https://portal.alpha.example.evil.example/": False, "about:blank": False, "": False}
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertIs(self.view(url).left_sign_in(), expected)


class RegistryTest(unittest.TestCase):
    def test_the_production_registries_hold_the_release_forms_adapter_only_and_are_read_only(self):
        self.assertEqual(dict(PAGE_READERS), {"release-forms": RELEASE_FORMS_READER})
        self.assertEqual(dict(LOGIN_VERIFIERS), {"release-forms": RELEASE_FORMS_VERIFIER})
        self.assertIs(type(RELEASE_FORMS_READER), FilmPageReader)
        self.assertIs(RELEASE_FORMS_READER.reads_tickets, True)  # its ticket page's contract was observed in M7
        self.assertIs(RELEASE_FORMS_READER.shows_ticket_ids, False)  # a ticket only through the click's provenance
        for registry in (PAGE_READERS, LOGIN_VERIFIERS):
            with self.assertRaises(TypeError):
                registry["ticket-files"] = object()  # type: ignore[index]
        self.assertNotIn("ticket-files", PAGE_READERS)

    def test_an_adapter_without_a_reader_is_still_unsupported(self):
        self.assertEqual(ALPHA.adapter.id, "ticket-files")
        provider = AccountSourceProvider(ALPHA, object(), reader=None)  # a manager, but no reader of its adapter
        self.assertIsNone(provider.reader)
        context = ResolveContext(http=SafeHttp(), control=ProcessControl(), task_dir=TEMP_PARENT)
        with self.assertRaises(SourceError) as caught:
            provider.resolve(FILM_URL, context)
        self.assertEqual(caught.exception.code, "READER_UNSUPPORTED")


def entry(season: int | None, number: int | None, file: str, quality: str = "HD · 1080P",
          audio: str = "Thuyết minh") -> RawEntry:
    """An entry as the reader makes it (``s<season>:e<number>``; None numbers: an unreadable card)."""
    key = f"s{season}:e{number}" if season is not None and number is not None else None
    return RawEntry("file", episode=key, variant=file, season=f"s{season}" if season else None,
                    season_number=season, season_label=f"Mùa {season}" if season else None, episode_number=number,
                    episode_label=f"Tập {number}" if number is not None else None, quality=quality, audio=audio,
                    variant_label=f"{quality} · {audio}", trigger=f"#t{file}", reveal=f"#s{season}")


def listing_of(*entries: RawEntry):
    builder = ListingBuilder("alpha")
    builder.add(FilmPage("501", "Phim mẫu", "series", tuple(entries)), FILM_URL)
    return builder.build()


class IdentityTest(unittest.TestCase):
    def test_the_same_episode_number_in_two_seasons_is_two_episodes(self):
        listing = listing_of(entry(1, 2, "11"), entry(2, 2, "21"))
        self.assertEqual([item.key for item in listing.episodes], ["s1:e2", "s2:e2"])
        keys = {listing.selection(item, item.variants[0]).key for item in listing.episodes}
        self.assertEqual(len(keys), 2)

    def test_two_files_of_one_episode_are_its_variants_and_one_is_chosen_per_episode(self):
        listing = listing_of(entry(1, 1, "11"), entry(1, 1, "12", quality="HD · 720P"), entry(1, 2, "13"))
        self.assertEqual([(item.key, [v.id for v in item.variants]) for item in listing.episodes],
                         [("s1:e1", ["11", "12"]), ("s1:e2", ["13"])])
        plan = plan_selection(listing, mode="all", variant_kind="hd · 1080p|thuyết minh")
        self.assertEqual([(item.episode, item.variant) for item in plan.items], [("s1:e1", "11"), ("s1:e2", "13")])

    def test_order_is_by_number_whatever_the_page_order_and_the_file_ids(self):
        forward = listing_of(entry(1, 1, "90"), entry(1, 2, "80"), entry(1, 10, "70"), entry(2, 1, "10"))
        backward = listing_of(entry(2, 1, "10"), entry(1, 10, "70"), entry(1, 2, "80"), entry(1, 1, "90"))
        for listing in (forward, backward):
            self.assertEqual([item.key for item in listing.episodes], ["s1:e1", "s1:e2", "s1:e10", "s2:e1"])
        self.assertEqual(forward.public()["fingerprint"], backward.public()["fingerprint"])

    def test_a_card_without_numbers_is_unreadable_and_the_list_incomplete(self):
        listing = listing_of(entry(1, 1, "11"), entry(None, None, "12"))
        self.assertFalse(listing.complete)
        self.assertIn("UNREADABLE_ITEMS", listing.reasons)
        self.assertEqual([item.key for item in listing.episodes], ["s1:e1"])

    def test_one_file_id_shown_twice_differently_in_one_episode_is_a_conflict(self):
        listing = listing_of(entry(1, 1, "11"), entry(1, 1, "11", quality="HD · 720P"))
        self.assertIn("CONFLICTING_ITEMS", listing.reasons)

    def test_another_episode_key_is_another_identity(self):
        before = listing_of(entry(1, 2, "11"))
        after = listing_of(entry(1, 3, "11"))  # the same file under another label: not the same chosen item
        self.assertNotEqual(before.public()["fingerprint"], after.public()["fingerprint"])
        self.assertIsNone(after.find(FileSelection("alpha", "501", "s1:e2", "11")))

    def test_the_navigation_keeps_trigger_season_request_and_its_method(self):
        action = f"https://{PORTAL}/a/22"
        listing = listing_of(entry(2, 2, "21"), replace(entry(2, 3, "22"), request=action, request_method="POST"))
        self.assertEqual(listing.navigation[("s2:e2", "21")], (FILM_URL, "#t21", "#s2", None, None))
        self.assertEqual(listing.navigation[("s2:e3", "22")], (FILM_URL, "#t22", "#s2", action, "POST"))
        for name, changes in (("not https", {"request": f"http://{PORTAL}/a/23", "request_method": "POST"}),
                              ("a request without its method", {"request": action}),
                              ("a method without a request", {"request_method": "POST"}),
                              ("another method", {"request": action, "request_method": "PUT"}),
                              ("a lower-case method", {"request": action, "request_method": "post"})):
            with self.subTest(name):
                bad = listing_of(replace(entry(2, 4, "23"), **changes))
                self.assertIn("UNREADABLE_ITEMS", bad.reasons)
                self.assertNotIn(("s2:e4", "23"), bad.navigation)


class FakePage:
    """A ticket page for ``ticket_page`` alone: ``buttons`` are what ``a#downloadBtn`` matches."""

    def __init__(self, *buttons):
        self.url, self.buttons = f"https://{TICKETS}/x/abc", list(buttons)

    def read(self, selector, attributes=(), limit=2000):
        assert selector == "a#downloadBtn", selector
        return [{name: button.get(name) for name in attributes} for button in self.buttons][:limit]


class TicketPageTest(unittest.TestCase):
    LINK = f"https://{FILES}/x/abc/download"

    def shown(self, **attributes):
        button = {"href": self.LINK, "data-state": "success", "aria-disabled": None, "class": "btn"}
        button.update(attributes)
        return RELEASE_FORMS_READER.ticket_page(FakePage(button))

    def test_only_an_unlocked_success_button_with_a_link_is_ready(self):
        ready = self.shown()
        self.assertEqual((ready.state, ready.link, ready.episode, ready.file), ("ready", self.LINK, None, None))

    def test_a_link_in_the_page_is_not_a_ready_ticket(self):
        for attributes in ({"data-state": "loading", "aria-disabled": "true", "class": "btn pointer-events-none"},
                           {"data-state": "loading"}, {"data-state": "default"}, {"data-state": None},
                           {"aria-disabled": "true"}, {"class": "btn pointer-events-none"}, {"data-state": "ready"},
                           {"href": None}):
            with self.subTest(attributes=attributes):
                shown = self.shown(**attributes)
                self.assertEqual((shown.state, shown.link), ("waiting", None))

    def test_blocked_is_a_challenge_and_error_an_error(self):
        self.assertEqual(self.shown(**{"data-state": "blocked"}).state, "challenge")
        self.assertEqual(self.shown(**{"data-state": "error"}).state, "error")

    def test_no_button_or_two_buttons_is_not_a_ticket_page(self):
        self.assertIsNone(RELEASE_FORMS_READER.ticket_page(FakePage()))
        button = {"href": self.LINK, "data-state": "success"}
        self.assertIsNone(RELEASE_FORMS_READER.ticket_page(FakePage(button, button)))


# ----------------------------------------------------------------------------------------- browser
@unittest.skipUnless(HAVE_BROWSER and HAVE_FFMPEG, f"{NEED_BROWSER}; {NEED_FFMPEG}")
class ReleaseCase(BrowserCase):
    def setUp(self):
        super().setUp()
        self.connect(self.manager, {"cookies": [cookie("sid", CANARY_SID, ".alpha.example")], "origins": []})
        self.task_dir = self.root / "task"
        self.task_dir.mkdir()

    def site(self, **options) -> ReleaseSite:
        site = ReleaseSite(body=CLIP["mkv"], **options)
        site.install(self.server, PORTAL, TICKETS, FILES)
        return site

    def provider(self, reader=None, *, page_seconds: float = 10, run_seconds: float = 90,
                 **options) -> AccountSourceProvider:
        run = {"network": self.network, "browser_options": {"page_seconds": page_seconds},
               "run_seconds": run_seconds, "grace_seconds": 2}
        return AccountSourceProvider(ALPHA, self.manager, reader=reader or RELEASE_FORMS_READER, run_options=run,
                                     **options)

    def context(self, previous=None, control=None) -> ResolveContext:
        http = SafeHttp(resolver=public_resolver, connector=self.server.connector,
                        ssl_context=DATA["tls"].client_context())
        return ResolveContext(http=http, control=control or self.control, task_dir=self.task_dir, ffprobe=FFPROBE,
                              previous=previous)

    @staticmethod
    def chosen(episode: str, variant: str) -> dict:
        return {"account_file": FileSelection("alpha", "501", episode, variant).public()}

    def two_seasons(self, **options) -> ReleaseSite:
        """Season 2 first on the page; season 1 lists 10, 2, 1 (and a 720p file of episode 1); ids rise in page
        order, so they follow neither the episodes nor the seasons; "Tập 02" has a leading zero."""
        season2 = [Card("2201", "Film.Example.S2E02.mkv", "Tập 02 · "), Card("2202", "Film.Example.S2E01.mkv")]
        season1 = season_cards([10, 2, 1], 1101) + [
            Card("1104", "Film.Example.E01.720p.mkv", "Tập 1 · ", ("HD · 720P", "Thuyết minh", "H264", "1.0 GB"))]
        return self.site(seasons=[("Mùa 2", season2), ("Mùa 1", season1)], **options)


class ReadTest(ReleaseCase):
    def test_folded_seasons_are_read_in_number_order_without_any_ticket(self):
        site = self.two_seasons()

        listing = self.provider().discover(site.url, self.control)

        self.assertTrue(listing.complete, listing.reasons)
        self.assertEqual((listing.film, listing.title, listing.kind), ("501", "Phim mẫu", "series"))
        self.assertEqual([season.key for season in listing.seasons], ["s1", "s2"])
        self.assertEqual([(item.key, item.label, [v.id for v in item.variants]) for item in listing.episodes],
                         [("s1:e1", "Tập 1", ["1103", "1104"]), ("s1:e2", "Tập 2", ["1102"]),
                          ("s1:e10", "Tập 10", ["1101"]), ("s2:e1", "Tập 1", ["2202"]),
                          ("s2:e2", "Tập 2", ["2201"])])
        variant = listing.episodes[0].variants[0]
        self.assertEqual((variant.label, variant.quality, variant.audio),
                         ("HD · 1080P · Thuyết minh · H264 · 2.0 GB", "HD · 1080P", "Thuyết minh"))
        self.assertEqual(site.posts, [])  # reading the list asks for no ticket
        public = json.dumps(listing.public(), ensure_ascii=False)
        for secret in ("https://", "download-links", TOKEN_VALUE, CANARY_SID, "999999", "cd-season"):
            self.assertNotIn(secret, public)

    def test_a_series_paste_waits_for_the_episode_choice(self):
        site = self.two_seasons()
        with self.assertRaises(SourceNeedsEpisodes):
            self.provider().resolve(site.url, self.context())
        self.assertEqual(site.posts, [])

    def test_a_film_is_one_episode_whose_files_are_its_variants(self):
        site = self.site(film_cards=[Card("3301", "Film.Example.2160p.mkv", None,
                                          ("4K · 2160P", "Thuyết minh", "H265", "17.4 GB")),
                                     Card("3302", "Film.Example.1080p.mkv", None,
                                          ("HD · 1080P", "Phụ đề", "H264", "4.0 GB"))])

        listing = self.provider().discover(site.url, self.control)

        self.assertEqual((listing.kind, listing.complete), ("film", True))
        self.assertEqual([(item.key, [v.id for v in item.variants]) for item in listing.episodes],
                         [("film:501", ["3301", "3302"])])

    def test_cards_without_a_label_number_or_a_form_are_unreadable_never_guessed(self):
        site = self.site(seasons=[("Mùa 1", [Card("4401", "Film.Example.S01E05.mkv", None), Card(None),
                                             *season_cards([1], 4402)])])

        listing = self.provider().discover(site.url, self.control)

        self.assertFalse(listing.complete)
        self.assertIn("UNREADABLE_ITEMS", listing.reasons)
        self.assertEqual([item.key for item in listing.episodes], ["s1:e1"])  # S01E05 in the name is not read

    def test_a_card_whose_form_is_not_a_post_or_whose_button_sends_elsewhere_is_unreadable(self):
        site = self.site(film_cards=[
            Card("3701", "Film.Example.1080p.mkv", None),
            Card("3702", "Film.Example.Get.mkv", None, method="get"),
            Card("3703", "Film.Example.Formmethod.mkv", None, button=' formmethod="get"'),
            Card("3704", "Film.Example.Formaction.mkv", None,
                 button=f' formaction="https://{PORTAL}/download-links/3701/access"')])

        listing = self.provider().discover(site.url, self.control)

        self.assertFalse(listing.complete)
        self.assertIn("UNREADABLE_ITEMS", listing.reasons)
        self.assertEqual([(item.key, [v.id for v in item.variants]) for item in listing.episodes],
                         [("film:501", ["3701"])])
        self.assertEqual(site.posts, [])

    def test_a_signed_out_page_asks_for_a_sign_in(self):
        site = self.site(seasons=[("Mùa 1", season_cards([1], 5501))], cookie="other")
        with self.assertRaises(SourceLoginRequired):
            self.provider().discover(site.url, self.control)
        self.assertEqual(site.posts, [])


class TicketTest(ReleaseCase):
    def test_a_reader_that_reads_no_tickets_spends_no_ticket(self):
        site = self.two_seasons()
        with self.assertRaises(SourceError) as caught:
            self.provider(ListOnlyReader()).resolve(site.url, self.context(self.chosen("s2:e2", "2201")))
        self.assertEqual(caught.exception.code, "TICKET_UNSUPPORTED")
        self.assertEqual((site.posts, site.downloads), ([], []))  # no click asked for a ticket, no file was read
        self.assertEqual([item for item in self.server.requests if item.method != "GET" or item.host == FILES], [])

    def test_a_folded_season_is_opened_and_exactly_one_ticket_is_asked_for(self):
        site = self.two_seasons()

        resolved = self.provider(TicketReader()).resolve(site.url, self.context(self.chosen("s2:e2", "2201")))

        self.assertEqual(site.posts, ["2201"])
        self.assertEqual((resolved.identity["episode"], resolved.identity["variant"]), ("s2:e2", "2201"))
        self.assertEqual({token for token, _at in site.downloads}, set(site.tokens_of("2201")))
        self.assertEqual({item.host for item in self.server.requests} - {PORTAL, TICKETS, FILES}, set())

    def test_an_open_season_is_not_clicked_again(self):
        site = self.two_seasons(open_seasons=True)  # a click on its summary would fold it and hide the form
        self.provider(TicketReader()).resolve(site.url, self.context(self.chosen("s1:e10", "1101")))
        self.assertEqual(site.posts, ["1101"])

    def test_one_file_in_two_seasons_is_never_clicked(self):
        site = self.site(seasons=[("Mùa 1", season_cards([1], 6601)), ("Mùa 2", season_cards([1], 6601))])
        with self.assertRaises(SourceError) as caught:
            self.provider(TicketReader()).resolve(site.url, self.context(self.chosen("s1:e1", "6601")))
        self.assertEqual(caught.exception.code, "TICKET_TARGET_UNCLEAR")
        self.assertEqual(site.posts, [])

    def test_a_form_shown_twice_on_a_film_page_is_never_clicked(self):
        site = self.site(film_cards=[Card("8801", "Film.Example.mkv", None), Card("8801", "Film.Example.mkv", None)])
        with self.assertRaises(SourceError) as caught:
            self.provider(TicketReader()).resolve(site.url, self.context(self.chosen("film:501", "8801")))
        self.assertEqual(caught.exception.code, "TICKET_TARGET_UNCLEAR")
        self.assertEqual(site.posts, [])

    def test_a_card_that_changed_once_its_season_opened_is_never_clicked(self):
        site = self.two_seasons(change_on_open=True)
        with self.assertRaises(SourceError) as caught:
            self.provider(TicketReader()).resolve(site.url, self.context(self.chosen("s2:e2", "2201")))
        self.assertEqual(caught.exception.code, "TICKET_TARGET_CHANGED")
        self.assertEqual(site.posts, [])

    def test_a_page_that_became_another_film_once_its_season_opened_is_never_clicked(self):
        site = self.two_seasons(film_on_open="999")  # same seasons, episodes and forms; another film id
        with self.assertRaises(SourceError) as caught:
            self.provider(TicketReader()).resolve(site.url, self.context(self.chosen("s2:e2", "2201")))
        self.assertEqual(caught.exception.code, "TICKET_TARGET_CHANGED")
        self.assertEqual(site.posts, [])

    def test_an_entry_whose_action_or_method_changed_once_its_season_opened_is_never_clicked(self):
        for changes in ({"request_method": "GET"}, {"request": f"https://{PORTAL}/download-links/2202/access"}):
            with self.subTest(changes=changes):
                site = self.two_seasons()
                reader = ChangedReader(2, **changes)  # read 1: the list; 2: after the season opened
                with self.assertRaises(SourceError) as caught:
                    self.provider(reader).resolve(site.url, self.context(self.chosen("s2:e2", "2201")))
                self.assertEqual(caught.exception.code, "TICKET_TARGET_CHANGED")
                self.assertEqual((reader.reads, site.posts), (2, []))

    def test_a_button_that_would_send_another_request_is_never_clicked(self):
        elsewhere = f'<form id="elsewhere" method="post" action="https://{PORTAL}/download-links/9405/access"></form>'
        for file, button, extra in (("3305", ' form="elsewhere"', elsewhere), ("3306", ' onclick="return true"', ""),
                                    ("3307", " disabled", "")):
            with self.subTest(button=button):
                site = self.site(film_cards=[Card(file, "Film.Example.mkv", None, button=button)], extra_html=extra)
                with self.assertRaises(SourceError) as caught:
                    self.provider(TicketReader()).resolve(site.url, self.context(self.chosen("film:501", file)))
                self.assertEqual(caught.exception.code, "TICKET_TARGET_CHANGED")
                self.assertEqual(site.posts, [])

    def test_a_film_without_seasons_is_read_again_before_the_click(self):
        site = self.site(film_cards=[Card("3301", "Film.Example.mkv", None)])
        reader = StepReader(0, lambda view: None)  # counts reads only
        self.provider(reader).resolve(site.url, self.context(self.chosen("film:501", "3301")))
        self.assertEqual((reader.reads, site.posts), (2, ["3301"]))  # the list, then the page again

        site = self.site(film="502", film_cards=[Card("3302", "Film.Example.mkv", None)])
        reader = StepReader(2, lambda view: view._page.evaluate(
            "document.querySelector('[data-movie-id]').setAttribute('data-movie-id', '999')"))
        with self.assertRaises(SourceError) as caught:
            self.provider(reader).resolve(site.url, self.context(
                {"account_file": FileSelection("alpha", "502", "film:502", "3302").public()}))
        self.assertEqual(caught.exception.code, "TICKET_TARGET_CHANGED")
        self.assertEqual((reader.reads, site.posts), (2, []))

    def test_the_ticket_pages_countdown_is_kept_before_its_link_is_used(self):
        site = self.two_seasons(countdown=2.5)
        self.provider(TicketReader()).resolve(site.url, self.context(self.chosen("s1:e1", "1103")))
        self.assertEqual(site.posts, ["1103"])
        first_file = min(at for _token, at in site.downloads)
        self.assertGreaterEqual(first_file - site.post_times[0], 2.5)  # the link was in the page from the start

    def test_a_blocked_failed_or_never_ready_ticket_page_keeps_its_own_code(self):
        for end, code in (("blocked", "SOURCE_CHALLENGE"), ("error", "TICKET_FAILED"), ("loading", "TICKET_TIMEOUT")):
            with self.subTest(end=end):
                site = self.site(film_cards=[Card("3401", "Film.Example.mkv", None)], ticket_end=end)
                with self.assertRaises(SourceError) as caught:
                    self.provider(TicketReader(), ticket_seconds=4).resolve(site.url, self.context())
                self.assertEqual(caught.exception.code, code)
                self.assertEqual((site.posts, site.downloads), (["3401"], []))
                self.assertEqual(self.manager.status("alpha")["state"], "CONNECTED")  # never a sign-in

    def test_popups_opened_before_the_ticket_are_never_taken_for_it(self):
        # An advert outside the source and a ready ticket page of another file on the ticket host open first,
        # by script; only the page that answers the click's own POST is this file's ticket.
        site = self.two_seasons(popups=("advert", "other-ticket"))
        self.provider(TicketReader()).resolve(site.url, self.context(self.chosen("s2:e2", "2201")))
        self.assertEqual(site.posts, ["2201"])
        self.assertEqual([token for token, _at in site.downloads], site.tokens_of("2201")[:1] * len(site.downloads))
        self.assertNotIn(site.other_token, {token for token, _at in site.downloads})
        self.assertGreaterEqual(self.server.count(f"/x/{site.other_token}"), 1)  # it did open, and was not used

    def test_a_ticket_reached_otherwise_than_by_the_clicks_own_redirects_is_never_used(self):
        # "page": the POST answers a page whose script goes to the ticket (not a redirect the run followed);
        # "outside": a redirect out of the source (refused); "none": a redirect without a target.
        for redirect in ("page", "outside", "none"):
            with self.subTest(redirect=redirect):
                site = self.site(film_cards=[Card("3501", "Film.Example.mkv", None)], redirect=redirect)
                with self.assertRaises(SourceError) as caught:
                    self.provider(TicketReader(), ticket_seconds=4).resolve(site.url, self.context())
                self.assertEqual(caught.exception.code, "TICKET_NOT_OPENED")
                self.assertEqual((site.posts, site.downloads), (["3501"], []))

    def test_without_the_entrys_request_a_ticket_page_without_ids_is_never_used(self):
        site = self.site(film_cards=[Card("3601", "Film.Example.mkv", None)])
        with self.assertRaises(SourceError) as caught:
            self.provider(NoRequestReader(), ticket_seconds=4).resolve(site.url, self.context())
        self.assertEqual(caught.exception.code, "TICKET_NOT_OPENED")
        self.assertEqual((site.posts, site.downloads), (["3601"], []))

    def test_the_ticket_hosts_own_cookie_stays_with_the_session_and_the_file_host_gets_none(self):
        # The portal's cookie is the portal's only (the real ticket host is another site): the ticket host never
        # gets it. The cookie the ticket host sets in a run is saved with the source's session (own_state and the
        # run's save) and sent to that host in the next run; the file host gets no cookie (SafeHttp's probe).
        self.connect(self.manager, {"cookies": [cookie("sid", CANARY_SID, PORTAL)], "origins": []})
        site = self.site(film_cards=[Card("7901", "Film.Example.mkv", None)], ticket_cookie=TICKET_COOKIE)
        for _ in range(2):
            self.provider(TicketReader()).resolve(site.url, self.context())
        saved = self.manager.session_for("alpha").state["cookies"]
        self.assertEqual(sorted((item["name"], item["domain"]) for item in saved), [("sid", PORTAL), ("tk", TICKETS)])
        tickets = [item.headers.get("cookie", "") for item in self.server.requests
                   if item.host == TICKETS and item.path.startswith("/x/")]
        self.assertEqual([TICKET_COOKIE in header for header in tickets], [False, True])
        self.assertNotIn(CANARY_SID, "".join(tickets))
        files = [item.headers.get("cookie") for item in self.server.requests if item.host == FILES]
        self.assertTrue(files)
        self.assertEqual(set(files), {None})
        self.assertEqual(site.posts, ["7901", "7901"])

    def test_the_ticket_host_signs_in_from_the_portals_session_within_the_clicks_own_chain(self):
        # The chain M7 measured in BiliFlow's browser: the POST, the ticket page, the ticket host's /login, a step
        # of its own, the portal's authorization, its callback, the ticket page again (six redirects, all the
        # click's own). Its cookie is saved with the session, so the next run goes straight to the ticket page.
        self.connect(self.manager, {"cookies": [cookie("sid", CANARY_SID, PORTAL)], "origins": []})
        site = self.site(film_cards=[Card("7911", "Film.Example.mkv", None),
                                     Card("7912", "Film.Example.720p.mkv", None,
                                          meta=("HD · 720P", "Thuyết minh", "H264", "1.0 GB"))], ticket_sso=True)

        first = self.provider(TicketReader()).resolve(site.url, self.context(self.chosen("film:501", "7911")))
        self.assertEqual((site.posts, site.sso_logins, first.identity["variant"]), (["7911"], 1, "7911"))
        saved = self.manager.session_for("alpha").state["cookies"]
        self.assertEqual(sorted((item["name"], item["domain"]) for item in saved), [("sid", PORTAL), ("tks", TICKETS)])

        self.provider(TicketReader()).resolve(site.url, self.context(self.chosen("film:501", "7912")))
        self.assertEqual((site.posts, site.sso_logins), (["7911", "7912"], 1))  # no second sign-in of its own
        self.assertEqual({token for token, _at in site.downloads}, set(site.tokens_of("7911") + site.tokens_of("7912")))
        tickets = [item for item in self.server.requests if item.host == TICKETS]
        self.assertNotIn(CANARY_SID, "".join(item.headers.get("cookie", "") for item in tickets))
        self.assertEqual({item.headers.get("cookie") for item in self.server.requests if item.host == FILES}, {None})

    def test_a_modal_over_the_page_stops_the_click_and_nothing_is_posted(self):
        site = self.site(film_cards=[Card("7701", "Film.Example.mkv", None)], gate=True)
        with self.assertRaises(SourceError) as caught:
            self.provider(TicketReader(), page_seconds=3).resolve(site.url, self.context())
        self.assertEqual(caught.exception.code, "CLICK_FAILED")
        self.assertEqual(site.posts, [])

    def test_a_cancel_after_the_season_opened_ends_the_run_before_the_click(self):
        site = self.two_seasons()
        reader = StepReader(2, lambda view: self.control.request("cancel"))  # read 1: the list; 2: after opening
        with self.assertRaises(Cancelled):
            self.provider(reader).resolve(site.url, self.context(self.chosen("s2:e2", "2201")))
        self.assertEqual(reader.reads, 2)
        self.assertEqual(site.posts, [])

    def test_too_little_run_time_left_after_the_season_opened_ends_the_run_before_the_click(self):
        site = self.two_seasons()
        reader = StepReader(2, use_up_run)
        with self.assertRaises(HttpError) as caught:
            self.provider(reader, run_seconds=40).resolve(site.url, self.context(self.chosen("s2:e2", "2201")))
        self.assertEqual(caught.exception.code, "NETWORK")
        self.assertEqual(reader.reads, 2)
        self.assertEqual(site.posts, [])


class ProductionReaderTest(ReleaseCase):
    """The registered reader itself, as the product runs it: no reader is passed, so the provider takes it from
    ``PAGE_READERS`` for a source whose config (the Git-ignored file, in its own form) names adapter
    "release-forms" with a file host and the page script of exception A."""

    def product_source(self):
        path = self.root / "config" / "download_accounts.local.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"sources": {"alpha": {
            "adapter": "release-forms", "label": "Nguồn alpha", "login_url": f"https://{PORTAL}/login",
            "hosts": {"portal": [PORTAL], "tickets": [TICKETS], "files": [FILES]},
            "page_script": GATE_SCRIPT}}}), encoding="utf-8")
        config = read_account_config(self.root)
        self.assertEqual(config.problems, ())
        manager = AccountManager(self.root, config, clock=self.clock,  # the same root and database
                                 vault=SessionVault(self.root, protector=FakeProtector(), acl=GoodAcl(FIRST)))
        self.addCleanup(manager.close)
        self.connect(manager, {"cookies": [cookie("sid", CANARY_SID, PORTAL)], "origins": []})
        return config.sources["alpha"], manager

    def test_the_registered_reader_takes_one_ticket_through_the_sign_in_chain_and_the_countdown(self):
        source, manager = self.product_source()
        site = self.site(film_cards=[Card("7921", "Film.Example.mkv", None)], ticket_sso=True, countdown=2.0,
                         gate_script=GATE_SCRIPT)
        self.server.route(GATE_SCRIPT_PATH, Reply(b"window.gateOk = 1;", content_type="text/javascript"))
        run = {"network": self.network, "browser_options": {"page_seconds": 10}, "run_seconds": 90,
               "grace_seconds": 2}
        provider = AccountSourceProvider(source, manager, run_options=run)

        self.assertIs(provider.reader, RELEASE_FORMS_READER)
        resolved = provider.resolve(site.url, self.context())  # the film's only file, chosen alone

        self.assertEqual((site.posts, site.sso_logins, resolved.identity["variant"]), (["7921"], 1, "7921"))
        sent = [(item.method, item.host, item.path) for item in self.server.requests if item.method != "GET"]
        self.assertEqual(sent, [("POST", PORTAL, "/download-links/7921/access")])  # the form's own POST, once
        self.assertGreaterEqual(min(at for _token, at in site.downloads) - site.post_times[0], 2.0)  # countdown
        self.assertEqual({token for token, _at in site.downloads}, set(site.tokens_of("7921")))
        files = [item for item in self.server.requests if item.host == FILES]
        self.assertTrue(files)
        self.assertEqual({(item.headers.get("cookie"), item.headers.get("referer")) for item in files}, {(None, None)})
        tickets = "".join(item.headers.get("cookie", "") for item in self.server.requests if item.host == TICKETS)
        self.assertNotIn(CANARY_SID, tickets)  # the portal's cookie stays the portal's
        saved = manager.session_for("alpha").state["cookies"]
        self.assertEqual(sorted((item["name"], item["domain"]) for item in saved), [("sid", PORTAL), ("tks", TICKETS)])
        self.assertEqual(self.hosts_seen() - {PORTAL, TICKETS, FILES, "ads.example"}, set())

    def test_the_ticket_is_still_used_when_the_browser_state_cannot_be_read_after_it(self):
        # What the user's first real run may have met: Playwright reads the state after the ticket by reopening
        # the closed ticket page's origin; when that read fails, the ticket is used and the saved session stays.
        source, manager = self.product_source()
        site = self.site(film_cards=[Card("7922", "Film.Example.mkv", None)], ticket_sso=True, countdown=1.0,
                         gate_script=GATE_SCRIPT)
        self.server.route(GATE_SCRIPT_PATH, Reply(b"window.gateOk = 1;", content_type="text/javascript"))
        run = {"network": self.network, "browser_options": {"page_seconds": 10}, "run_seconds": 90,
               "grace_seconds": 2}
        provider = AccountSourceProvider(source, manager, run_options=run)
        notices: list[str] = []
        lines: list[str] = []
        context = replace(self.context(), notice=lambda code, message: notices.append(code), log=lines.append)
        unreadable = BrowserFailed("SESSION_READ_FAILED", detail="INDEXEDDB")

        with mock.patch.object(SessionBrowser, "storage_state", autospec=True, side_effect=unreadable):
            resolved = provider.resolve(site.url, context)

        self.assertEqual((site.posts, resolved.identity["variant"]), (["7922"], "7922"))  # one ticket, used
        self.assertEqual(notices, ["SESSION_STATE_UNREAD"])
        saved = manager.session_for("alpha").state["cookies"]
        self.assertEqual([(item["name"], item["domain"]) for item in saved], [("sid", PORTAL)])  # as it was
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("Lượt ẩn (có vé): "), lines[0])


if __name__ == "__main__":
    unittest.main()
