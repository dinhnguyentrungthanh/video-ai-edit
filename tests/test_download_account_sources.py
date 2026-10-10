"""The account source provider (download_account_sources, M3) on real headless Edge with the fixture film site.

The film site (tests/account_source_fixtures.py) is the test's own structure on ``.example`` hosts, served by
the HTTPS fixture server of the browser tests through the session browser's checked client. Files are
self-made MKV clips probed and fetched by the downloader's cookie-free SafeHttp. Sessions are fake cookies in
the test vault (a fake protector). Nothing here is a real site, a real session or a real download.
"""
from __future__ import annotations

import json
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow import download_account_sources
from biliflow.download_account_config import AccountConfig
from biliflow.download_account_listing import FileSelection, plan_selection
from biliflow.download_account_runs import RunTimedOut, source_run_lock
from biliflow.download_account_sources import AccountSourceProvider, SourceNeedsEpisodes
from biliflow.download_http import Cancelled, HttpError, SafeHttp
from biliflow.download_media_file import PART_NAME
from biliflow.download_page_sources import SourceNeedsChoice
from biliflow.download_runner import ProcessControl
from biliflow.download_source_types import ResolveContext, SourceChanged, SourceError, SourceLoginRequired
from biliflow.download_sources import SourceTransfers
from tests.account_source_fixtures import TOKEN_MARK, FilmSite, FixtureReader, Item, TicketBehaviour, series
from tests.source_fixtures import FFMPEG, FFPROBE, HAVE_FFMPEG, NEED_FFMPEG, Reply, make_clip, public_resolver
from tests.test_download_account_browser import (
    ALPHA,
    CANARY_SID,
    DATA,
    FILES,
    HAVE_BROWSER,
    NEED_BROWSER,
    PORTAL,
    TEMP_PARENT,
    TICKETS,
    BrowserCase,
    cookie,
    edge_command_lines,
    setUpModule as browser_setup,
    tearDownModule as browser_teardown,
)

CLIPS: dict[str, bytes] = {}


def setUpModule():
    browser_setup()
    if HAVE_FFMPEG:
        DATA["clips_dir"] = TemporaryDirectory(dir=TEMP_PARENT, prefix="account-clips-")
        folder = Path(DATA["clips_dir"].name)
        CLIPS["mkv"] = make_clip(folder / "clip.mkv", seconds=3, container="mkv").read_bytes()
        CLIPS["mkv2"] = make_clip(folder / "clip2.mkv", seconds=2, container="mkv").read_bytes()


def tearDownModule():
    if "clips_dir" in DATA:
        DATA["clips_dir"].cleanup()
    browser_teardown()


class FilmChangedReader(FixtureReader):
    """From its ``at``-th film page read on, shows another film id (the page changed between the list and the
    ticket); ``reads`` counts the reads."""

    def __init__(self, at: int):
        self.at, self.reads = at, 0

    def film_page(self, view):
        film = super().film_page(view)
        self.reads += 1
        return replace(film, film="F9") if film is not None and self.reads >= self.at else film


class MethodChangedReader(FixtureReader):
    """Gives every file entry a POST ``request`` to its own action; from its ``at``-th film page read on, the
    same action with GET (the page changed the form between the list and the ticket); ``reads`` counts."""

    def __init__(self, at: int):
        self.at, self.reads = at, 0

    def film_page(self, view):
        film = super().film_page(view)
        self.reads += 1
        if film is None:
            return film
        method = "GET" if self.reads >= self.at else "POST"
        return replace(film, entries=tuple(
            replace(item, request=f"https://{PORTAL}/act/{item.variant}", request_method=method)
            if item.role == "file" else item for item in film.entries))


class GatedReader(FixtureReader):
    """Holds the first film page read until ``release`` (what the test does meanwhile happens mid-run)."""

    def __init__(self):
        self.entered, self.release = threading.Event(), threading.Event()

    def film_page(self, view):
        if not self.entered.is_set():
            self.entered.set()
            self.release.wait(20)
        return super().film_page(view)


@unittest.skipUnless(HAVE_BROWSER and HAVE_FFMPEG, f"{NEED_BROWSER}; {NEED_FFMPEG}")
class SiteCase(BrowserCase):
    def setUp(self):
        super().setUp()
        self.session = {"cookies": [cookie("sid", CANARY_SID, ".alpha.example")], "origins": []}
        self.connect(self.manager, self.session)
        self.task_dir = self.root / "task"
        self.task_dir.mkdir()
        self.logs: list[str] = []

    def site(self, **options) -> FilmSite:
        site = FilmSite(**options)
        site.install(self.server, PORTAL, TICKETS, FILES)
        return site

    def provider(self, manager=None, reader=None, **options) -> AccountSourceProvider:
        run = {"network": self.network, "browser_options": {"page_seconds": 10}, "run_seconds": 60,
               "grace_seconds": 2}
        run.update(options.pop("run", {}))
        return AccountSourceProvider(ALPHA, manager or self.manager, reader=reader or FixtureReader(),
                                     run_options=run, **options)

    def file_http(self) -> SafeHttp:
        return SafeHttp(resolver=public_resolver, connector=self.server.connector,
                        ssl_context=DATA["tls"].client_context())

    def context(self, previous=None, *, probing: bool = True, control=None) -> ResolveContext:
        return ResolveContext(http=self.file_http(), control=control or self.control, task_dir=self.task_dir,
                              ffprobe=FFPROBE if probing else None, previous=previous, log=self.logs.append)

    def chosen(self, episode: str, variant: str = "v1080") -> dict:
        return {"account_file": FileSelection("alpha", "F1", episode, variant).public()}

    def file_requests(self):
        return [item for item in self.server.requests if item.host == FILES]

    def assert_edge_gone(self):
        self.assertEqual(edge_command_lines(str(self.root)), [])
        self.assert_no_profile_left()


class DiscoveryTest(SiteCase):
    def test_a_long_series_of_two_seasons_is_read_whole_in_number_order(self):
        variants = (("v1080", "1080p", "Vietsub"), ("v720", "720p", "Thuyết minh"))
        items = series({"s1": [10, 2, 1, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13], "s2": [2, 1, 3]}, variants=variants)
        trailer = Item("trailer1", "t1", role="trailer", quality=None, audio=None)
        pages = [items[:12] + [trailer], items[12:24], items[24:]]
        site = self.site(pages=pages, promos=[Item("promo1", "p1")])

        listing = self.provider().discover(site.url, self.control)

        self.assertTrue(listing.complete, listing.reasons)
        self.assertEqual((listing.kind, listing.pages, len(listing.episodes)), ("series", 3, 16))
        self.assertEqual([(item.season, item.number) for item in listing.episodes],
                         [("s1", n) for n in range(1, 14)] + [("s2", n) for n in (1, 2, 3)])
        self.assertEqual([season.key for season in listing.seasons], ["s1", "s2"])
        self.assertEqual({len(item.variants) for item in listing.episodes}, {2})
        self.assertEqual(listing.skipped, {"trailer": 1, "promo": 3})  # the promotions are on every page
        self.assertEqual(site.ticket_loads(), 0)  # reading the list asks for no ticket
        self.assertEqual(self.file_requests(), [])
        public = json.dumps(listing.public(), ensure_ascii=False)
        for secret in ("https://", "/film/", "/t/", CANARY_SID, TOKEN_MARK, "a.get"):
            self.assertNotIn(secret, public)
        self.assertEqual([page.query.get("page", ["1"]) for page in self.server.seen("/film/F1")],
                         [["1"], ["2"], ["3"]])  # each page read once
        self.assert_edge_gone()

    def test_specials_and_episodes_without_a_number_keep_the_sources_places(self):
        page = [Item("e2", season="s1", season_number=1, number=2), Item("e1", season="s1", season_number=1, number=1),
                Item("sp-after-1", season="s1", season_number=1, special=True, after=1, label="Đặc biệt 1"),
                Item("loose", season="s1", season_number=1, label="Tập không số"),
                Item("sp-free", season="s1", season_number=1, special=True, label="Hậu trường"),
                Item("e3", season="s1", season_number=1, number=3)]
        site = self.site(pages=[page])

        listing = self.provider().discover(site.url, self.control)

        self.assertEqual([item.key for item in listing.episodes], ["e1", "sp-after-1", "e2", "loose", "e3", "sp-free"])
        self.assertEqual([season.label for season in listing.seasons], ["Mùa 1", "Đặc biệt"])
        self.assertEqual(listing.episodes[-1].season, ":specials")

    def test_a_missing_variant_is_reported_and_never_replaced(self):
        variants = (("v1080", "1080p", "Vietsub"), ("v720", "720p", "Vietsub"))
        site = self.site(pages=[series({"s1": [1, 2, 3]}, variants=variants, missing={"s1e2": {"v1080"}})])
        listing = self.provider().discover(site.url, self.control)

        plan = plan_selection(listing, mode="all", variant_kind="1080p|vietsub")

        self.assertEqual([item.episode for item in plan.items], ["s1e1", "s1e3"])
        self.assertEqual(plan.missing, ("s1e2",))
        self.assertEqual(plan.confirm_label, "Tải 2 tập")

    def test_a_pagination_loop_and_repeated_items_are_read_once(self):
        items = series({"s1": [1, 2, 3, 4]})
        site = self.site(pages=[items[:2], items[1:4]], extra_links={2: ["?page=2", "/film/F1#top", "?page=2&x=1"]})
        listing = self.provider().discover(site.url, self.control)

        self.assertTrue(listing.complete, listing.reasons)
        self.assertEqual([item.number for item in listing.episodes], [1, 2, 3, 4])
        # Page 2 repeats episode 2; ?page=2&x=1 is another link (read once) that repeats page 2 whole.
        self.assertEqual((listing.pages, listing.skipped), (3, {"duplicate": 4}))
        self.assertEqual(len(self.server.seen("/film/F1")), 3)

    def test_limits_a_load_more_button_another_film_and_a_failing_page_make_it_incomplete(self):
        items = series({"s1": list(range(1, 7))})
        cases = [({"pages": [items[:2], items[2:4], items[4:]]}, {"max_pages": 2}, "PAGE_LIMIT"),
                 ({"pages": [items], "more_button": True}, {}, "MORE_NOT_LINKED"),
                 ({"pages": [items[:3], items[3:]], "film_on_page": {2: "F2"}}, {}, "OTHER_FILM"),
                 ({"pages": [items[:3], items[3:]], "status_of_page": {2: 500}}, {}, "PAGE_FAILED"),
                 ({"pages": [items], "extra_links": {1: ["https://other.example/film/F1?page=2"]}}, {},
                  "PAGE_NOT_FOLLOWED")]
        for site_options, provider_options, reason in cases:
            with self.subTest(reason=reason):
                site = self.site(**site_options)
                listing = self.provider(**provider_options).discover(site.url, self.control)
                self.assertFalse(listing.complete)
                self.assertIn(reason, listing.reasons)
                self.assertEqual(listing.public()["complete"], False)
                plan = plan_selection(listing, mode="all")
                self.assertEqual(plan.confirm_label, f"Tải {plan.count} tập đã thấy")

    def test_a_first_page_that_is_not_a_film_page_is_refused(self):
        site = self.site(pages=[series({"s1": [1]})], status_of_page={1: 404})
        with self.assertRaises(SourceError) as caught:
            self.provider().discover(site.url, self.control)
        self.assertEqual(caught.exception.code, "UNAVAILABLE")
        self.server.route("/plain", Reply(b"<p>no film</p>", content_type="text/html"))
        with self.assertRaises(SourceError) as caught:
            self.provider().discover(f"https://{PORTAL}/plain", self.control)
        self.assertEqual(caught.exception.code, "NOT_A_FILM_PAGE")

    def test_a_film_with_several_variants_asks_which_one_then_resolves_that_one(self):
        page = [Item("main", "v1080"), Item("main", "v720", quality="720p")]
        site = self.site(kind="film", pages=[page], files={"main--v720": CLIPS["mkv"]})
        with self.assertRaises(SourceNeedsChoice) as caught:
            self.provider().resolve(site.url, self.context())
        choices = caught.exception.choices
        self.assertEqual([item["title"] for item in choices], ["1080p · Vietsub", "720p · Vietsub"])
        self.assertEqual(site.ticket_loads(), 0)

        source = self.provider().resolve(site.url, self.context({"selection": choices[1]["selection"]}))

        self.assertEqual(source.identity["variant"], "v720")
        self.assertEqual(site.loads, {"main--v720": 1})

    def test_a_film_list_not_read_whole_is_never_taken_as_one_file(self):
        site = self.site(kind="film", pages=[[Item("main", "v720", quality="720p")]], more_button=True,
                         files={"main--v720": CLIPS["mkv"]})
        with self.assertRaises(SourceNeedsChoice) as caught:  # one file seen, but more may exist
            self.provider().resolve(site.url, self.context())
        self.assertEqual([item["title"] for item in caught.exception.choices],
                         ["720p · Vietsub · danh sách bản chưa đủ"])
        self.assertEqual(site.ticket_loads(), 0)

    def test_a_film_page_without_any_file_says_so(self):
        site = self.site(kind="film", pages=[[Item("trailer", "t1", role="trailer")]])
        for call in (lambda: self.provider().resolve(site.url, self.context()),
                     lambda: self.provider().discover(site.url, self.control)):
            with self.assertRaises(SourceError) as caught:
                call()
            self.assertEqual(caught.exception.code, "NO_FILES")
        self.assertEqual(site.ticket_loads(), 0)


class TicketTest(SiteCase):
    def series_site(self, **options) -> FilmSite:
        items = series({"s1": [1, 2, 3]})
        files = {f"s1e{n}--v1080": CLIPS["mkv"] for n in (1, 2, 3)}
        return self.site(pages=[items[:2], items[2:]], files=files, **options)

    def test_only_the_chosen_file_gets_a_ticket_and_adverts_are_left_out(self):
        site = self.series_site()
        source = self.provider().resolve(site.url, self.context(self.chosen("s1e3")))

        self.assertEqual((source.identity["episode"], source.identity["variant"]), ("s1e3", "v1080"))
        self.assertEqual(site.loads, {"s1e3--v1080": 1})  # one ticket, of that file only
        self.assertGreaterEqual(self.server.count("/ad"), 1)  # the advert popup opened and was not used
        self.assertEqual(source.title, "Phim thử · Tập 3 · 1080p · Vietsub")
        self.assertEqual(len(self.server.seen("/film/F1")), 2)  # read until the file was seen
        self.assertEqual({item.host for item in self.server.requests} - {PORTAL, TICKETS, FILES}, set())
        self.assert_edge_gone()

    def test_the_ticket_pages_own_wait_is_kept(self):
        site = self.series_site(tickets={"s1e1--v1080": TicketBehaviour(wait=2.5)})
        started = time.monotonic()
        self.provider().resolve(site.url, self.context(self.chosen("s1e1")))
        file_seen = self.file_requests()[0]
        self.assertGreaterEqual(time.monotonic() - started, 2.5)
        self.assertTrue(file_seen.query["token"][0].endswith(TOKEN_MARK))

    def test_an_expired_ticket_is_asked_for_again_a_bounded_number_of_times(self):
        site = self.series_site(tickets={"s1e2--v1080": TicketBehaviour(state="expired", times=1)})
        self.provider().resolve(site.url, self.context(self.chosen("s1e2")))
        self.assertEqual(site.loads, {"s1e2--v1080": 2})

        site = self.series_site(tickets={"s1e2--v1080": TicketBehaviour(state="expired")})
        with self.assertRaises(SourceError) as caught:
            self.provider().resolve(site.url, self.context(self.chosen("s1e2")))
        self.assertEqual(caught.exception.code, "TICKET_EXPIRED")
        self.assertEqual(site.loads, {"s1e2--v1080": 2})
        self.assertEqual(self.manager.status("alpha")["state"], "CONNECTED")  # not a sign-in problem

    def test_ticket_page_failures_keep_their_own_codes(self):
        # A tab that closes itself after it was found as the ticket page (1.5 s; one closing at once is never
        # found: TICKET_NOT_OPENED, like a ticket page of another file).
        cases = [("other", "TICKET_NOT_OPENED", 0.2), ("close", "TICKET_TAB_CLOSED", 1.5),
                 ("gone", "UNAVAILABLE", 0.2), ("challenge", "SOURCE_CHALLENGE", 0.2), ("error", "TICKET_FAILED", 0.2)]
        for state, code, wait in cases:
            with self.subTest(state=state):
                site = self.series_site(tickets={"s1e1--v1080": TicketBehaviour(state=state, wait=wait)})
                with self.assertRaises(SourceError) as caught:
                    self.provider(ticket_seconds=4).resolve(site.url, self.context(self.chosen("s1e1")))
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(self.file_requests(), [])
                self.assertEqual(self.manager.status("alpha")["state"], "CONNECTED")
        self.assert_edge_gone()

    def test_the_file_server_refusing_a_fresh_ticket_asks_for_another_one_then_reports_it(self):
        site = self.series_site(refuse_files=1)
        source = self.provider().resolve(site.url, self.context(self.chosen("s1e1")))
        self.assertEqual(source.estimated_bytes, len(CLIPS["mkv"]))
        self.assertEqual(site.loads, {"s1e1--v1080": 2})

        site = self.series_site(refuse_files=10)
        with self.assertRaises(SourceError) as caught:
            self.provider().resolve(site.url, self.context(self.chosen("s1e1")))
        self.assertEqual(caught.exception.code, "TICKET_REFUSED")
        self.assertEqual(self.manager.status("alpha")["state"], "CONNECTED")

    def test_a_deleted_file_or_a_file_that_left_the_list(self):
        site = self.series_site(gone={"s1e1--v1080"})
        with self.assertRaises(HttpError) as caught:
            self.provider().resolve(site.url, self.context(self.chosen("s1e1")))
        self.assertEqual(caught.exception.code, "UNAVAILABLE")

        with self.assertRaises(SourceChanged):
            self.provider().resolve(site.url, self.context(self.chosen("s1e9")))
        with self.assertRaises(SourceChanged):
            self.provider().resolve(site.url, self.context({"account_file": FileSelection(
                "alpha", "F2", "s1e1", "v1080").public()}))

    def test_the_page_read_again_before_the_click_must_still_be_the_chosen_film(self):
        # The chosen file is on page 1 of a film list of two pages: after reading the whole list the browser
        # goes back to page 1, whose film id is checked again before the ticket click.
        pages = [[Item("main", "v1080")], [Item("main", "v720", quality="720p")]]
        site = self.site(kind="film", pages=pages, files={"main--v1080": CLIPS["mkv"]})
        with self.assertRaises(SourceNeedsChoice) as caught:
            self.provider().resolve(site.url, self.context())
        choice = next(item["selection"] for item in caught.exception.choices if item["title"].startswith("1080p"))

        self.provider().resolve(site.url, self.context({"selection": choice}))
        self.assertEqual(site.loads, {"main--v1080": 1})

        reader = FilmChangedReader(3)  # reads 1-2: the list; 3: page 1 again, which is now another film
        with self.assertRaises(SourceChanged):
            self.provider(reader=reader).resolve(site.url, self.context({"selection": choice}))
        self.assertEqual(reader.reads, 3)
        self.assertEqual(site.loads, {"main--v1080": 1})  # no further ticket
        self.assert_edge_gone()

    def test_the_page_read_again_on_the_way_back_must_keep_the_entrys_method(self):
        # Same two-page film list; the entries carry a POST request. Back on page 1 (read 3), the same action is
        # now a GET: `_ticket` refuses right there, before get_ticket's own re-read and the click.
        pages = [[Item("main", "v1080")], [Item("main", "v720", quality="720p")]]
        site = self.site(kind="film", pages=pages, files={"main--v1080": CLIPS["mkv"]})
        with self.assertRaises(SourceNeedsChoice) as caught:
            self.provider().resolve(site.url, self.context())
        choice = next(item["selection"] for item in caught.exception.choices if item["title"].startswith("1080p"))

        # This fixture's trigger is a link that submits no form, so an entry naming a POST request is never
        # clicked (get_ticket's submission check, right before the click): unchanged, the run gets that far.
        with self.assertRaises(SourceError) as caught:
            self.provider(reader=MethodChangedReader(99)).resolve(site.url, self.context({"selection": choice}))
        self.assertEqual(caught.exception.code, "TICKET_TARGET_CHANGED")
        self.assertEqual(site.loads, {})

        reader = MethodChangedReader(3)
        with self.assertRaises(SourceChanged):  # changed on the way back: `_ticket` stops it before get_ticket
            self.provider(reader=reader).resolve(site.url, self.context({"selection": choice}))
        self.assertEqual(reader.reads, 3)
        self.assertEqual(site.loads, {})  # no ticket
        self.assert_edge_gone()

    def test_links_of_ticket_and_file_hosts_are_refused_with_a_hint(self):
        for url in (f"https://{TICKETS}/t/s1e1--v1080", f"https://{FILES}/f/x.mkv"):
            with self.subTest(url=url), self.assertRaises(SourceError) as caught:
                self.provider().resolve(url, self.context())
            self.assertEqual(caught.exception.code, "ACCOUNT_PAGE_ONLY")
        self.assertEqual(self.server.requests, [])


class SessionTest(SiteCase):
    def series_site(self, **options) -> FilmSite:
        return self.site(pages=[series({"s1": [1, 2]})], files={"s1e1--v1080": CLIPS["mkv"]}, **options)

    def test_no_session_or_an_expired_one_needs_a_sign_in_and_opens_nothing(self):
        site = self.series_site()
        generation = self.manager.session_for("alpha").generation
        self.clock.at(3600)
        with self.assertRaises(SourceLoginRequired) as caught:
            self.provider().resolve(site.url, self.context(self.chosen("s1e1")))
        self.assertEqual((caught.exception.source_id, caught.exception.generation, caught.exception.reason),
                         ("alpha", generation, "SESSION_EXPIRED"))
        self.manager.disconnect("alpha")
        with self.assertRaises(SourceLoginRequired) as caught:
            self.provider().discover(site.url, self.control)
        self.assertEqual((caught.exception.code, caught.exception.reason), ("SOURCE_LOGIN_REQUIRED", "NOT_CONNECTED"))
        self.assertEqual(self.server.requests, [])  # no browser run without a session
        self.assertNotEqual(self.manager.status("alpha")["state"], "LOGGING_IN")  # no sign-in window either

    def test_sign_in_evidence_marks_only_that_session_invalid(self):
        cases = [({"session_value": "another"}, {}, "a redirect to the sign-in page"),
                 ({"signed_out_page": True}, {}, "a signed-out message"),
                 ({}, {"s1e1--v1080": TicketBehaviour(state="login")}, "a ticket page sending to sign-in")]
        for options, tickets, case in cases:
            with self.subTest(case=case):
                self.connect(self.manager, self.session)
                generation = self.manager.session_for("alpha").generation
                site = self.series_site(tickets=tickets, **options)
                with self.assertRaises(SourceLoginRequired) as caught:
                    self.provider().resolve(site.url, self.context(self.chosen("s1e1")))
                self.assertEqual((caught.exception.generation, caught.exception.reason),
                                 (generation, "SESSION_REJECTED"))
                status = self.manager.status("alpha")
                self.assertEqual((status["state"], status["error_code"]), ("NEEDS_LOGIN", "SESSION_REJECTED"))

    def test_a_403_page_a_server_error_or_a_network_failure_is_not_a_sign_in(self):
        site = self.series_site(status_of_page={1: 403})
        with self.assertRaises(SourceError) as caught:
            self.provider().discover(site.url, self.control)
        self.assertEqual(caught.exception.code, "FORBIDDEN")
        site.status_of_page = {1: 503}
        with self.assertRaises(HttpError) as caught:
            self.provider().discover(site.url, self.control)
        self.assertEqual((caught.exception.code, caught.exception.retryable), ("SERVER_BUSY", True))
        self.resolver.answers[PORTAL] = []
        with self.assertRaises(HttpError) as caught:
            self.provider().discover(site.url, self.control)
        self.assertEqual((caught.exception.code, caught.exception.retryable), ("NETWORK", True))
        self.assertEqual(self.manager.status("alpha")["state"], "CONNECTED")

    def stored_cookie(self) -> str:
        return {item["name"]: item["value"] for item in self.manager.session_for("alpha").state["cookies"]}["sid"]

    def test_rotated_cookies_are_saved_without_extending_the_session(self):
        site = self.series_site(rotate="rotated-1")
        before = self.manager.status("alpha")
        self.clock.at(1800)
        self.provider().discover(site.url, self.control)
        after = self.manager.status("alpha")
        self.assertEqual(self.stored_cookie(), "rotated-1")
        self.assertEqual((after["authenticated_at"], after["recheck_at"]), (before["authenticated_at"],
                                                                            before["recheck_at"]))
        self.clock.at(3600)
        with self.assertRaises(SourceLoginRequired):
            self.provider().discover(site.url, self.control)

    def test_two_runs_of_one_source_take_turns_and_keep_each_others_cookies(self):
        site = self.site(pages=[series({"s1": [1, 2]})], rotating=True)
        results: list[object] = []

        def discover() -> None:
            try:
                results.append(self.provider().discover(site.url, ProcessControl()).complete)
            except Exception as error:  # noqa: BLE001 - the test reports it
                results.append(error)
        threads = [threading.Thread(target=discover) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(120)
        self.assertEqual(results, [True, True])
        self.assertEqual(self.stored_cookie(), site.current)  # the second run started from the first's cookie

    def test_a_disconnect_or_a_new_sign_in_during_a_resolve(self):
        site = self.series_site()
        for case in ("disconnect", "new sign-in"):
            with self.subTest(case=case):
                self.connect(self.manager, self.session)
                reader = GatedReader()
                outcome: list[object] = []
                worker = threading.Thread(target=lambda: outcome.append(self.capture(lambda: self.provider(
                    reader=reader).resolve(site.url, self.context(self.chosen("s1e1"))))))
                worker.start()
                self.assertTrue(reader.entered.wait(30))
                if case == "disconnect":
                    self.manager.disconnect("alpha")
                else:
                    self.connect(self.manager, {"cookies": [cookie("sid", "new-session", ".alpha.example")],
                                                "origins": []})
                new_generation = self.manager.status("alpha")
                reader.release.set()
                worker.join(120)
                if case == "disconnect":
                    self.assertIsInstance(outcome[0], SourceLoginRequired)
                    self.assertEqual(outcome[0].reason, "NOT_CONNECTED")
                    self.assertEqual(self.manager.status("alpha")["state"], "NOT_CONNECTED")
                else:
                    self.assertEqual(outcome[0].identity["episode"], "s1e1")  # asked again with the new session
                    self.assertEqual(self.stored_cookie(), "new-session")  # never overwritten by the old run
                    self.assertEqual(self.manager.status("alpha"), new_generation)

    @staticmethod
    def capture(action):
        try:
            return action()
        except Exception as error:  # noqa: BLE001 - the test inspects it
            return error

    def test_a_cancel_while_waiting_for_the_sources_browser_ends_at_once(self):
        site = self.series_site()
        lock = source_run_lock(self.manager, "alpha")
        control = ProcessControl()
        outcome: list[object] = []
        with lock:
            worker = threading.Thread(target=lambda: outcome.append(self.capture(
                lambda: self.provider().discover(site.url, control))))
            worker.start()
            time.sleep(0.5)
            started = time.monotonic()
            control.request("cancel")
            worker.join(10)
            self.assertLess(time.monotonic() - started, 2)
        self.assertIsInstance(outcome[0], Cancelled)
        self.assertEqual(self.server.requests, [])

    def test_the_runs_deadline_ends_a_page_stuck_in_a_script(self):
        site = self.series_site(hang_script=True)
        started = time.monotonic()
        with self.assertRaises(RunTimedOut) as caught:
            self.provider(run={"run_seconds": 6, "grace_seconds": 2}).discover(site.url, self.control)
        self.assertLess(time.monotonic() - started, 25)
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(self.manager.status("alpha")["state"], "CONNECTED")
        self.assert_edge_gone()

    def test_a_cancel_during_a_ticket_wait_stops_its_browser(self):
        site = self.series_site(tickets={"s1e1--v1080": TicketBehaviour(wait=30)})
        control = ProcessControl()
        threading.Timer(4, control.request, ("cancel",)).start()
        started = time.monotonic()
        with self.assertRaises(Cancelled):
            self.provider().resolve(site.url, self.context(self.chosen("s1e1"), control=control))
        self.assertLess(time.monotonic() - started, 15)
        self.assert_edge_gone()

    def test_cookies_rotated_before_a_cancel_are_kept(self):
        site = self.series_site(rotate="rotated-before-cancel", tickets={"s1e1--v1080": TicketBehaviour(wait=30)})
        before = self.manager.status("alpha")
        control = ProcessControl()
        threading.Timer(4, control.request, ("cancel",)).start()
        with self.assertRaises(Cancelled):
            self.provider().resolve(site.url, self.context(self.chosen("s1e1"), control=control))
        self.assertEqual(self.stored_cookie(), "rotated-before-cancel")  # the next run sends the source's new one
        after = self.manager.status("alpha")
        self.assertEqual((after["authenticated_at"], after["recheck_at"]), (before["authenticated_at"],
                                                                            before["recheck_at"]))

    def test_a_ticket_is_never_asked_for_without_time_left_to_read_it(self):
        site = self.series_site()
        with self.assertRaises(HttpError) as caught:  # 12 s less the 5 s kept for the end: under the 10 s a ticket needs
            self.provider(run={"run_seconds": 12}).resolve(site.url, self.context(self.chosen("s1e1")))
        self.assertEqual((caught.exception.code, caught.exception.retryable), ("NETWORK", True))
        self.assertEqual(site.ticket_loads(), 0)
        self.assertEqual(self.manager.status("alpha")["state"], "CONNECTED")


class TransferTest(SiteCase):
    """The file goes through the downloader's FileTransfer (cookie-free SafeHttp, strict versions); a ticket
    that expires in the middle of a transfer is replaced by a new ticket of the same file."""

    def run_download(self, site: FilmSite, provider: AccountSourceProvider, source):
        transfers = SourceTransfers(self.file_http(), ffmpeg=FFMPEG, ffprobe=FFPROBE)
        transfers.files.retries = 1
        logs: list[str] = []

        def refresh():
            fresh = provider.resolve(site.url, self.context(source.identity, probing=False))
            self.assertEqual(fresh.identity_key, source.identity_key)
            return fresh
        outcome = transfers.download(source, refresh, self.task_dir, ProcessControl(), on_progress=lambda item: None,
                                     on_log=logs.extend)
        return outcome, logs

    def cut_then_expire(self, site: FilmSite, cut: int) -> None:
        """The first whole-file request stops after ``cut`` bytes and its ticket expires with it."""
        original, done = site._file, []

        def route(seen, number):
            reply = original(seen, number)
            if not done and "range" not in seen.headers and reply.status == 200:
                done.append(True)
                with site.lock:
                    site.tokens.pop(seen.query["token"][0], None)
                reply.cut_after = cut
            return reply
        self.server.route("/f/s1e1--v1080", route)

    def test_an_expired_ticket_mid_transfer_is_refreshed_and_the_same_version_continues(self):
        site = self.site(pages=[series({"s1": [1]})], files={"s1e1--v1080": CLIPS["mkv"]})
        provider = self.provider()
        source = provider.resolve(site.url, self.context(self.chosen("s1e1")))
        cut = len(CLIPS["mkv"]) // 3
        self.cut_then_expire(site, cut)

        outcome, logs = self.run_download(site, provider, source)

        self.assertTrue(outcome.ok, outcome)
        self.assertEqual(outcome.final_path.read_bytes(), CLIPS["mkv"])
        self.assertEqual(outcome.final_path.suffix, ".mkv")
        last = self.file_requests()[-1]
        self.assertEqual((last.headers["range"], last.headers["if-range"]), (f"bytes={cut}-", '"s1e1--v1080-1"'))
        self.assertEqual(site.loads, {"s1e1--v1080": 2})  # the probe's ticket (cut, then refused) and the new one
        self.assertIn("Máy chủ từ chối link (có thể đã hết hạn); lấy lại nguồn rồi tải tiếp.", logs)
        for request in self.file_requests():
            self.assertNotIn("cookie", request.headers)
        for line in logs:
            self.assertNotIn(TOKEN_MARK, line)

    def test_a_new_ticket_for_a_changed_file_of_the_same_size_starts_again(self):
        site = self.site(pages=[series({"s1": [1]})], files={"s1e1--v1080": CLIPS["mkv"]})
        provider = self.provider()
        source = provider.resolve(site.url, self.context(self.chosen("s1e1")))
        self.cut_then_expire(site, len(CLIPS["mkv"]) // 3)
        replaced = CLIPS["mkv"].replace(b"Lavf", b"LavX")  # another version of the file, the same size
        self.assertNotEqual(replaced, CLIPS["mkv"])
        original_ticket = site._ticket

        def new_version(seen, number):  # the next ticket points at the new version
            site.files["s1e1--v1080"], site.etags["s1e1--v1080"] = replaced, '"s1e1--v1080-2"'
            return original_ticket(seen, number)
        self.server.route("/t/s1e1--v1080", new_version)

        outcome, logs = self.run_download(site, provider, source)

        self.assertTrue(outcome.ok, outcome)
        self.assertEqual(outcome.final_path.read_bytes(), replaced)  # whole new version, never a mix
        self.assertTrue(any("tải lại từ đầu" in line for line in logs), logs)
        self.assertNotIn("range", self.file_requests()[-1].headers)


    def test_an_expired_session_never_stops_a_running_transfer_but_a_new_ticket_needs_a_sign_in(self):
        site = self.site(pages=[series({"s1": [1]})], files={"s1e1--v1080": CLIPS["mkv"]})
        provider = self.provider()
        source = provider.resolve(site.url, self.context(self.chosen("s1e1")))
        self.clock.at(3600)  # the session expires after the probe

        outcome, _logs = self.run_download(site, provider, source)  # the file needs no session

        self.assertTrue(outcome.ok, outcome)
        self.assertEqual(outcome.final_path.read_bytes(), CLIPS["mkv"])

        self.task_dir = self.root / "task-2"
        self.task_dir.mkdir()
        self.cut_then_expire(site, len(CLIPS["mkv"]) // 3)
        outcome, _logs = self.run_download(site, provider, source)  # its ticket expires mid-transfer

        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.code, "SOURCE_LOGIN_REQUIRED")  # the new ticket needs a sign-in (M4: WAITING_LOGIN)
        self.assertEqual((self.task_dir / PART_NAME).stat().st_size, len(CLIPS["mkv"]) // 3)  # bytes kept
        self.assertEqual(site.loads, {"s1e1--v1080": 1})  # no browser ran with the expired session


class SecretTest(SiteCase):
    def test_bait_tokens_and_cookies_never_reach_public_output_messages_or_files(self):
        site = self.site(pages=[series({"s1": [1, 2]})], files={"s1e1--v1080": CLIPS["mkv"]},
                         tickets={"s1e2--v1080": TicketBehaviour(state="expired")})
        provider = self.provider()
        texts = [json.dumps(provider.discover(site.url, self.control).public(), ensure_ascii=False)]
        source = provider.resolve(site.url, self.context(self.chosen("s1e1")))
        texts += [json.dumps(source.public(), ensure_ascii=False), repr(source), repr(source.plan), source.title]
        for previous in (self.chosen("s1e2"), self.chosen("s1e9")):
            try:
                provider.resolve(site.url, self.context(previous))
            except Exception as error:  # noqa: BLE001 - its text and repr are what is checked
                texts += [str(error), repr(error), getattr(error, "message", "")]
        site.session_value = "another"
        try:
            provider.resolve(site.url, self.context(self.chosen("s1e1")))
        except SourceLoginRequired as error:
            texts += [str(error), repr(error), repr(vars(error))]
        texts += self.logs
        texts += [json.dumps(self.manager.statuses())]
        for path in self.root.rglob("*"):
            if path.is_file() and not path.name.startswith("session-"):  # the vault holds the fake session
                texts.append(path.read_bytes().decode("latin-1"))
        joined = "\n".join(texts)
        for secret in (CANARY_SID, TOKEN_MARK, f"https://{FILES}", "/t/s1e1"):
            self.assertNotIn(secret, joined)


class RunOptionsTest(unittest.TestCase):
    def test_a_hidden_run_has_no_window_one_deadline_and_bounded_limits(self):
        from biliflow.download_account_runs import run_with_session
        bad = [{"browser_options": {"headed": True}}, {"browser_options": {"stop_at": 1.0}},
               {"run_seconds": 0}, {"run_seconds": 601}, {"grace_seconds": 0}, {"grace_seconds": 61}]
        for options in bad:
            with self.subTest(options=options), self.assertRaises(ValueError):
                run_with_session(object(), "alpha", lambda browser: None, control=ProcessControl(), **options)


class RegistryTest(unittest.TestCase):
    def test_a_link_of_an_account_source_never_goes_to_yt_dlp_or_an_anonymous_provider(self):
        """At the registry: the account provider claims every host of its source and refuses with SourceError, never
        SourceDeclined (the worker's only way to yt-dlp). Through the worker: the next test."""
        from biliflow.download_sources import SourceRegistry, default_registry
        with TemporaryDirectory(dir=TEMP_PARENT, prefix="account-registry-") as folder:
            root = Path(folder)
            (root / "config").mkdir()
            (root / "config" / "download_accounts.local.json").write_text(json.dumps({"sources": {"alpha": {
                "adapter": "ticket-files", "label": "Nguồn alpha", "login_url": f"https://{PORTAL}/login",
                "hosts": {"portal": [PORTAL], "tickets": [TICKETS], "files": [FILES]}}}}), encoding="utf-8")
            registry: SourceRegistry = default_registry(root)
        self.assertEqual(registry.ids, ("alpha", "direct"))
        context = ResolveContext(http=SafeHttp(), control=ProcessControl(), task_dir=Path(folder))
        for url, code in ((f"https://{PORTAL}/film/F1", "ACCOUNT_NOT_READY"),
                          (f"https://{FILES}/f/movie.mkv", "ACCOUNT_PAGE_ONLY"),
                          (f"https://{TICKETS}/t/1", "ACCOUNT_PAGE_ONLY")):
            with self.subTest(url=url):
                provider = registry.provider_for(url)
                self.assertEqual(provider.id, "alpha")
                with self.assertRaises(SourceError) as caught:  # never SourceDeclined (yt-dlp)
                    provider.resolve(url, context)
                self.assertEqual(caught.exception.code, code)
        self.assertEqual(registry.provider_for("https://other.example/movie.mkv").id, "direct")

    def test_a_pasted_link_of_an_account_source_ends_in_its_provider_and_never_starts_yt_dlp(self):
        """R22 ("never falls back to yt-dlp or an anonymous provider") through the real DownloadWorker: the portal,
        ticket and file links of a configured source (no manager, as default_registry gives without one) are
        pasted and run. Each ends FAILED with its account provider's own code; the fake yt-dlp never ran (its
        call log stays absent), no task has a SOURCE_DECLINED event, the anonymous direct provider resolved
        nothing although the file link ends in .mkv, and nothing was connected. With a manager and the example
        config, through the Control Center: test_download_account_e2e_flows (E6)."""
        import sys
        from unittest import mock

        from biliflow.download_runner import YtDlpRunner
        from biliflow.download_sources import DirectMediaProvider, default_registry
        from biliflow.download_store import DownloadStore
        from biliflow.download_worker import DownloadWorker
        from tests.test_download_worker import FAKE
        connects: list[tuple[str, int]] = []

        def refuse(address: str, port: int, timeout: float):
            connects.append((address, port))
            raise OSError("no connection in this test")
        codes = {f"https://{PORTAL}/film/F1": "ACCOUNT_NOT_READY", f"https://{FILES}/f/movie.mkv": "ACCOUNT_PAGE_ONLY",
                 f"https://{TICKETS}/t/1": "ACCOUNT_PAGE_ONLY"}
        with TemporaryDirectory(dir=TEMP_PARENT, prefix="account-registry-") as folder:
            root = Path(folder)
            (root / "config").mkdir()
            (root / "config" / "download_accounts.local.json").write_text(json.dumps({"sources": {"alpha": {
                "adapter": "ticket-files", "label": "Nguồn alpha", "login_url": f"https://{PORTAL}/login",
                "hosts": {"portal": [PORTAL], "tickets": [TICKETS], "files": [FILES]}}}}), encoding="utf-8")
            store = DownloadStore(root / "state" / "downloads.sqlite3")
            runner = YtDlpRunner(root, command_prefix=[sys.executable, str(FAKE)], deno_path=None,
                                 env_extra={"FAKE_YTDLP_SCENARIO": str(root / "no-scenario.json"),
                                            "FAKE_YTDLP_LOG": str(root / "ytdlp-calls.jsonl")})
            worker = DownloadWorker(root, store, runner=runner, resolver=public_resolver,
                                    sources=default_registry(root), http=SafeHttp(resolver=public_resolver,
                                                                                  connector=refuse),
                                    space_probe=lambda _root: (10 ** 13, 10 ** 11),
                                    cache_pruner=lambda _root: {"removed_files": 0})
            try:
                with mock.patch.object(DirectMediaProvider, "resolve", autospec=True,
                                       side_effect=AssertionError("the direct provider was asked")) as direct:
                    tasks = worker.add(list(codes), rights_confirmed=True)
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline and (store.next_queued(exclude=worker.running_ids())
                                                           or worker.running_ids()):
                        worker.dispatch()
                        worker.wait_idle(30)
                ended = {task["url"]: store.get(task["id"]) for task in tasks}
                declined = [task["url"] for task in tasks
                            if "SOURCE_DECLINED" in [event["kind"] for event in store.events(task["id"])]]
                yt_dlp_ran = (root / "ytdlp-calls.jsonl").exists()
            finally:
                worker.shutdown(10)
                store.close()
        self.assertEqual({url: (task["state"], task["error_code"]) for url, task in ended.items()},
                         {url: ("FAILED", code) for url, code in codes.items()})
        self.assertFalse(yt_dlp_ran)
        self.assertEqual(declined, [])
        direct.assert_not_called()
        self.assertEqual(connects, [])

    def test_an_adapter_without_a_page_reader_is_never_read(self):
        from biliflow.download_account_login import LOGIN_VERIFIERS
        from biliflow.download_account_pages import PAGE_READERS
        # Production has a reader and a verifier for "release-forms" only (M7); ALPHA's "ticket-files" has neither.
        self.assertEqual((set(PAGE_READERS), set(LOGIN_VERIFIERS)), ({"release-forms"}, {"release-forms"}))
        self.assertEqual(ALPHA.adapter.id, "ticket-files")
        provider = AccountSourceProvider(ALPHA, object(), reader=None)  # a manager, but no reader
        context = ResolveContext(http=SafeHttp(), control=ProcessControl(), task_dir=TEMP_PARENT)
        with self.assertRaises(SourceError) as caught:
            provider.resolve(f"https://{PORTAL}/film/F1", context)
        self.assertEqual(caught.exception.code, "READER_UNSUPPORTED")

    def test_an_account_config_source_takes_no_public_provider_id(self):
        from biliflow.download_account_config import RESERVED_IDS
        from biliflow.download_sources import SITE_PROVIDERS, DirectMediaProvider
        self.assertEqual(RESERVED_IDS, {DirectMediaProvider.id, *(item.id for item in SITE_PROVIDERS)})
        self.assertIsInstance(AccountConfig({}), AccountConfig)


class RequirementValuesTest(unittest.TestCase):
    def test_one_resolve_asks_for_at_most_two_tickets(self):
        """R59 (plan 9.12: an expired ticket, one the file server refuses or a session changed in the run is asked
        for again, "tối đa 2 vé mỗi lần resolve"): TICKET_ATTEMPTS = 2, the default of every AccountSourceProvider."""
        self.assertEqual(download_account_sources.TICKET_ATTEMPTS, 2)
        self.assertEqual(AccountSourceProvider(ALPHA).ticket_attempts, 2)


if __name__ == "__main__":
    unittest.main()
