"""The film list of an account source (download_account_listing) and the list walk of download_account_pages
with a fake browser and a fake reader: no browser, no network, no real site (``.example`` hosts only)."""
from __future__ import annotations

import json
import time
import unittest
from typing import Any

from biliflow.download_account_browser import Navigation
from biliflow.download_account_config import AdapterSpec, SourceAccount
from biliflow.download_account_edge import BrowserFailed
from biliflow.download_account_listing import (
    MAX_LABEL_CHARS,
    REASONS,
    SPECIALS_KEY,
    FilmPage,
    FileSelection,
    ListingBuilder,
    RawEntry,
    checked_id,
    checked_number,
    checked_size,
    clean_label,
    film_choices,
    plan_selection,
)
from biliflow.download_account_pages import PAGE_PAUSE_SECONDS, SignedOut, read_listing
from biliflow.download_http import Cancelled, HttpError
from biliflow.download_runner import ProcessControl
from biliflow.download_source_types import SourceError

PORTAL = "portal.alpha.example"
WWW = "www.alpha.example"
HOSTS = {"portal": (PORTAL, WWW), "tickets": ("tickets.alpha.example",), "files": ("files.alpha.example",)}
SOURCE = SourceAccount("alpha", AdapterSpec("ticket-files", "ttl"), "Nguồn alpha", f"https://{PORTAL}/login", HOSTS)
QUERY_SOURCE = SourceAccount("alpha", AdapterSpec("ticket-files", "ttl"), "Nguồn alpha",
                             f"https://{PORTAL}/index.php?do=login", HOSTS)
FILM_URL = f"https://{PORTAL}/film/F1"


def entry(episode: Any, variant: Any = "v1", *, season: Any = "s1", season_number: Any = 1, number: Any = None,
          role: str = "file", trigger: str | None = "#get", **values: Any) -> RawEntry:
    return RawEntry(role, episode, variant, season, season_number, values.pop("season_label", None), number,
                    values.pop("label", None), values.pop("special", False), values.pop("after", None),
                    values.pop("variant_label", None), values.pop("quality", "1080p"), values.pop("audio", "Vietsub"),
                    values.pop("size", None), values.pop("version", None), trigger)


def page(*entries: RawEntry, film: str = "F1", kind: str | None = "series", links: tuple[str, ...] = (),
         more: bool = False, title: str = "Phim thử") -> FilmPage:
    return FilmPage(film, title, kind, tuple(entries), links, more)


def build(*pages: FilmPage, **options: Any):
    builder = ListingBuilder("alpha", **options)
    for number, item in enumerate(pages, 1):
        builder.add(item, f"{FILM_URL}?page={number}")
    return builder.build()


class OrderTest(unittest.TestCase):
    def test_episodes_go_by_number_within_seasons_by_number(self):
        listing = build(page(*[entry(f"s2e{n}", season="s2", season_number=2, number=n) for n in (2, 1)],
                             *[entry(f"s1e{n}", number=n) for n in (10, 2, 1, 11, 3)]))
        self.assertEqual([item.key for item in listing.episodes],
                         ["s1e1", "s1e2", "s1e3", "s1e10", "s1e11", "s2e1", "s2e2"])
        self.assertEqual([(season.key, season.label) for season in listing.seasons], [("s1", "Mùa 1"), ("s2", "Mùa 2")])

    def test_items_without_a_number_keep_their_place_and_numbers_are_never_read_from_titles(self):
        listing = build(page(entry("b", number=3), entry("x", label="Tập 1 (bản đẹp)"), entry("a", number=1),
                             entry("y", label="Tập 2"), entry("c", number="02")))
        self.assertEqual([item.key for item in listing.episodes], ["a", "x", "c", "y", "b"])
        self.assertEqual([item.number for item in listing.episodes], [1, None, 2, None, 3])

    def test_a_season_without_a_number_keeps_its_place(self):
        listing = build(page(entry("s2e1", season="s2", season_number=2, number=1),
                             entry("o1", season="ova", season_number=None, season_label="OVA", number=1),
                             entry("s1e1", number=1)))
        self.assertEqual([season.key for season in listing.seasons], ["s1", "ova", "s2"])
        self.assertEqual([item.key for item in listing.episodes], ["s1e1", "o1", "s2e1"])

    def test_specials_go_where_the_source_places_them_or_into_their_own_group_last(self):
        listing = build(page(entry("e2", number=2), entry("first", special=True, after=0),
                             entry("sp1", special=True, after=1), entry("e1", number=1),
                             entry("lost", special=True, after=7), entry("free", special=True),
                             entry("s2e1", season="s2", season_number=2, number=1)))
        self.assertEqual([item.key for item in listing.episodes], ["first", "e1", "sp1", "e2", "s2e1", "lost", "free"])
        self.assertEqual(listing.seasons[-1].key, SPECIALS_KEY)
        self.assertEqual(listing.seasons[-1].label, "Đặc biệt")
        self.assertEqual({item.key for item in listing.episodes if item.season == SPECIALS_KEY}, {"lost", "free"})

    def test_a_variant_is_a_file_of_its_episode_never_an_episode(self):
        listing = build(page(entry("e1", "hd", number=1), entry("e1", "sd", number=1, quality="480p"),
                             entry("e2", "hd", number=2)))
        self.assertEqual([(item.key, len(item.variants)) for item in listing.episodes], [("e1", 2), ("e2", 1)])

    def test_film_or_series(self):
        one = entry("main", season="", season_number=None)
        self.assertEqual(build(page(one, kind=None)).kind, "film")
        self.assertEqual(build(page(one, kind="film")).kind, "film")
        self.assertEqual(build(page(one, kind="series")).kind, "series")  # the source says it is a series
        self.assertEqual(build(page(entry("main"), kind="film")).kind, "series")  # it shows a season
        two = page(entry("a", season=""), entry("b", season=""), kind="film")
        self.assertEqual(build(two).kind, "series")  # two episodes are never one film


class CheckTest(unittest.TestCase):
    def test_ids_numbers_sizes_and_labels_are_checked_never_guessed(self):
        self.assertEqual([checked_id(value) for value in ("ep-1", " ep.2 ", 7, "", "a b", "ép", True, "-x")],
                         ["ep-1", "ep.2", "7", None, None, None, None, None])
        self.assertEqual([checked_number(value) for value in (2, "02", " 10 ", "2a", "١", -1, True, 2.0, 100_001)],
                         [2, 2, 10, None, None, None, None, None, None])
        self.assertEqual([checked_size(value) for value in (1024, "2048", 0, "1.5 GB", None)],
                         [1024, 2048, None, None, None])
        self.assertEqual(clean_label("  Tập\n1​  x "), "Tập 1 x")
        self.assertEqual(len(clean_label("x" * 500)), MAX_LABEL_CHARS)

    def test_entries_without_ids_or_a_trigger_cannot_be_chosen_and_make_the_list_incomplete(self):
        listing = build(page(entry("e1", number=1), entry(None, number=2, label="Tập 2"), entry("e3", None),
                             entry("e4", trigger=None), entry("bad id", number=5), entry("e6", season="s 1")))
        self.assertEqual([item.key for item in listing.episodes], ["e1"])
        self.assertEqual(listing.skipped, {"unreadable": 5})
        self.assertEqual((listing.complete, listing.reasons), (False, ("UNREADABLE_ITEMS",)))

    def test_a_trigger_is_short_text(self):
        listing = build(page(entry("e1", number=1, trigger="x" * 513), entry("e2", number=2, trigger=["#a"]),
                             entry("e3", number=3, trigger="#e3")))
        self.assertEqual(([item.key for item in listing.episodes], listing.skipped), (["e3"], {"unreadable": 2}))

    def test_trailers_and_adverts_are_left_out_by_their_role(self):
        listing = build(page(entry("trailer", role="trailer"), entry("e1", number=1), entry("ad", role="ad")))
        self.assertEqual([item.key for item in listing.episodes], ["e1"])
        self.assertEqual(listing.skipped, {"trailer": 1, "ad": 1})
        self.assertTrue(listing.complete)

    def test_the_same_item_twice_is_counted_and_two_contents_under_one_id_are_a_conflict(self):
        listing = build(page(entry("e1", number=1), entry("e1", number=1)), page(entry("e1", number=1)))
        self.assertEqual((listing.skipped, listing.complete), ({"duplicate": 2}, True))
        for pages in ((page(entry("e1", number=1), entry("e1", "v2", number=2)),),
                      (page(entry("e1", number=1, size=10)), page(entry("e1", number=1, size=20))),
                      (page(entry("e1", number=1), entry("e2", season="s1", season_number=2, number=2)),)):
            with self.subTest(pages=pages):
                listing = build(*pages)
                self.assertIn("CONFLICTING_ITEMS", listing.reasons)
                self.assertFalse(listing.complete)

    def test_limits_of_episodes_and_files(self):
        listing = build(page(*[entry(f"e{n}", number=n) for n in range(1, 6)]), max_episodes=3)
        self.assertEqual((len(listing.episodes), listing.reasons), (3, ("ITEM_LIMIT",)))
        listing = build(page(*[entry("e1", f"v{n}", number=1) for n in range(4)]), max_files=2)
        self.assertEqual((len(listing.episodes[0].variants), listing.reasons), (2, ("ITEM_LIMIT",)))

    def test_another_film_and_more_items_without_a_link(self):
        builder = ListingBuilder("alpha")
        self.assertTrue(builder.add(page(entry("e1", number=1)), FILM_URL))
        self.assertFalse(builder.add(page(entry("x1", number=1), film="F2"), FILM_URL + "?page=2"))
        self.assertTrue(builder.add(page(entry("e2", number=2), more=True), FILM_URL + "?page=3"))
        listing = builder.build()
        self.assertEqual(([item.key for item in listing.episodes], listing.reasons),
                         (["e1", "e2"], ("OTHER_FILM", "MORE_NOT_LINKED")))
        self.assertEqual(listing.public()["message"], "Một trang phân trang thuộc phim khác.")


class PublicTest(unittest.TestCase):
    def listing(self, **values: Any):
        return build(page(entry("e1", number=1, size=1001, version="r1", **values),
                          entry("e1", "v2", number=1, quality="720p", trigger='a[data-secret="https://t.example/x"]'),
                          entry("e2", number=2), links=("https://portal.alpha.example/film/F1?page=2",)))

    def test_the_public_list_holds_ids_labels_order_and_sizes_only(self):
        listing = self.listing()
        public = listing.public()
        text = json.dumps(public, ensure_ascii=False)
        for private in ("https://", "page=", "#get", "data-secret", "r1"):
            self.assertNotIn(private, text)
        self.assertEqual((public["episode_count"], public["file_count"], public["complete"]), (2, 3, True))
        first = public["groups"][0]["episodes"][0]
        self.assertEqual((first["key"], first["order"], first["label"]), ("e1", 1, "Tập 1"))
        self.assertEqual(first["variants"][0], {"id": "v1", "label": "1080p · Vietsub", "kind": "1080p|vietsub",
                                                "quality": "1080p", "audio": "Vietsub", "size": 1001})
        self.assertEqual(public["variant_kinds"], [{"kind": "1080p|vietsub", "label": "1080p · Vietsub", "episodes": 2},
                                                   {"kind": "720p|vietsub", "label": "720p · Vietsub", "episodes": 1}])
        self.assertNotIn("#get", repr(listing))

    def test_the_fingerprint_follows_the_list_not_its_links(self):
        self.assertEqual(self.listing().public()["fingerprint"], self.listing().public()["fingerprint"])
        self.assertNotEqual(self.listing().public()["fingerprint"], self.listing(label="Tập một").public()["fingerprint"])

    def test_a_selection_is_four_ids_with_a_stable_key(self):
        selection = FileSelection("alpha", "F1", "e1", "v1")
        self.assertEqual(selection.key, FileSelection("alpha", "F1", "e1", "v1").key)
        self.assertTrue(selection.key.startswith("acct-"))
        self.assertEqual(len({selection.key, FileSelection("alpha", "F1", "e1", "v2").key,
                              FileSelection("alpha", "F1", "e1v", "1").key}), 3)
        self.assertEqual(FileSelection.from_mapping({**selection.public(), "version": "r1", "size": 5}), selection)
        for bad in (None, "e1", {"source": "alpha", "film": "F1", "episode": "e1"},
                    {**selection.public(), "episode": "https://x.example/"}):
            self.assertIsNone(FileSelection.from_mapping(bad))


class SelectionPlanTest(unittest.TestCase):
    def series(self, missing: frozenset[str] = frozenset(), complete: bool = True):
        entries = []
        for number in range(1, 13):
            entries.append(entry(f"e{number}", "hd", number=number))
            if f"e{number}" not in missing:
                entries.append(entry(f"e{number}", "sd", number=number, quality="720p"))
        return build(page(*entries, more=not complete))

    def test_nothing_is_chosen_without_a_mode_and_a_variant(self):
        listing = self.series()
        for options in ({"mode": None}, {"mode": "auto"}, {"mode": "all"}, {"mode": "pick", "episodes": ()},
                        {"mode": "pick", "episodes": ("e99",), "variant_kind": "1080p|vietsub"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                plan_selection(listing, **options)

    def test_all_episodes_in_one_variant_with_the_missing_ones_reported(self):
        listing = self.series(missing=frozenset({"e4", "e11"}))
        plan = plan_selection(listing, mode="all", variant_kind="720p|vietsub")
        self.assertEqual([item.episode for item in plan.items], [f"e{n}" for n in range(1, 13) if n not in (4, 11)])
        self.assertEqual({item.variant for item in plan.items}, {"sd"})  # never another variant in their place
        self.assertEqual((plan.missing, plan.ambiguous, plan.confirm_label), (("e4", "e11"), (), "Tải 10 tập"))

    def test_picked_episodes_in_list_order_with_a_variant_per_episode(self):
        listing = self.series()
        plan = plan_selection(listing, mode="pick", episodes=("e10", "e2"), variants={"e2": "sd", "e10": "hd"})
        self.assertEqual([(item.episode, item.variant) for item in plan.items], [("e2", "sd"), ("e10", "hd")])
        self.assertEqual(plan.confirm_label, "Tải 2 tập")

    def test_two_files_of_the_wanted_kind_are_shown_and_none_is_taken(self):
        listing = build(page(entry("e1", "a", number=1), entry("e1", "b", number=1), entry("e2", "a", number=2)))
        plan = plan_selection(listing, mode="all", variant_kind="1080p|vietsub")
        self.assertEqual(([item.episode for item in plan.items], plan.ambiguous), (["e2"], ("e1",)))

    def test_an_incomplete_list_says_so_on_the_button(self):
        plan = plan_selection(self.series(complete=False), mode="all", variant_kind="1080p|vietsub")
        self.assertEqual((plan.complete, plan.reasons, plan.confirm_label),
                         (False, ("MORE_NOT_LINKED",), "Tải 12 tập đã thấy"))
        self.assertEqual(plan.note, "Danh sách chưa đầy đủ (Trang còn tập khác nhưng không có liên kết trang để đọc "
                                    "tiếp): chỉ gồm 12 tập trong phần đã thấy, có thể còn tập chưa đọc được.")
        self.assertEqual(plan_selection(self.series(), mode="all", variant_kind="1080p|vietsub").note, "")

    def test_a_film_list_not_read_whole_says_so_in_its_choices(self):
        film = build(page(entry("main", "hd", season=""), kind="film", more=True))
        self.assertEqual([item["title"] for item in film_choices(film)], ["1080p · Vietsub · danh sách bản chưa đủ"])

    def test_a_film_offers_its_files_and_a_series_never_does(self):
        film = build(page(entry("main", "hd", season=""), entry("main", "sd", season="", quality="720p"), kind="film"))
        choices = film_choices(film)
        self.assertEqual([item["title"] for item in choices], ["1080p · Vietsub", "720p · Vietsub"])
        self.assertEqual(choices[0]["selection"], FileSelection("alpha", "F1", "main", "hd").key)
        with self.assertRaises(ValueError):
            film_choices(self.series())


class FakeBrowser:
    """Pages by URL: (status, landed URL, FilmPage or None) or a BrowserFailed code."""

    def __init__(self, pages: dict[str, Any], *, stop_at: float | None = None):
        self.pages_by_url = pages
        self.stop_at = stop_at
        self.control = ProcessControl()
        self.visited: list[str] = []
        self.waits: list[float] = []
        self.current = ""

    def page(self) -> str:
        return "page"

    def wait(self, page: Any, seconds: float) -> bool:
        self.waits.append(seconds)
        return True

    def navigate(self, url: str, page: Any = None) -> Navigation:
        self.visited.append(url)
        answer = self.pages_by_url.get(url, (404, url, None))
        if isinstance(answer, str):
            raise BrowserFailed(answer, PORTAL)
        status, landed, _film = answer
        self.current = landed
        return Navigation(landed, status)

    def page_url(self, page: Any) -> str:
        return self.current

    def film_at(self, url: str) -> FilmPage | None:
        answer = self.pages_by_url.get(url)
        return answer[2] if isinstance(answer, tuple) else None


class FakeReader:
    def __init__(self, browser: FakeBrowser, signed_out: bool = False):
        self.browser = browser
        self.signed_out_answer = signed_out

    def film_page(self, view) -> FilmPage | None:
        return self.browser.film_at(view.url)

    def signed_out(self, view) -> bool:
        return self.signed_out_answer

    def ticket_page(self, view):
        return None


def walk(pages: dict[str, Any], url: str = FILM_URL, **options: Any):
    browser = FakeBrowser(pages, stop_at=options.pop("stop_at", None))
    reader = FakeReader(browser, options.pop("signed_out", False))
    return read_listing(browser, reader, SOURCE, url, **options), browser


class ReadListingTest(unittest.TestCase):
    def test_page_links_are_followed_once_inside_the_portal_only_and_the_others_make_it_incomplete(self):
        refused = ("http://portal.alpha.example/film/F1?page=3", "https://portal.alpha.example:8443/film/F1?page=3",
                   "https://tickets.alpha.example/film/F1", "https://evil.example/film/F1", "javascript:alert(1)",
                   f"https://{WWW}/film/F1?page=4", "/login", "/login/?next=x")
        followed = ("?page=2", "/film/F1#top", "?page=2", "https://PORTAL.alpha.example/film/F1?page=3")
        pages = {FILM_URL: (200, FILM_URL, page(entry("e1", number=1), links=followed + refused)),
                 f"{FILM_URL}?page=2": (200, f"{FILM_URL}?page=2", page(entry("e2", number=2), links=("/film/F1",))),
                 f"{FILM_URL}?page=3": (200, f"{FILM_URL}?page=3", page(entry("e3", number=3)))}
        builder, browser = walk(pages)
        # Another portal host, the sign-in page and everything outside the portal are never followed.
        self.assertEqual(browser.visited, [FILM_URL, f"{FILM_URL}?page=2", f"{FILM_URL}?page=3"])
        self.assertEqual(browser.waits, [PAGE_PAUSE_SECONDS] * 2)  # a pause before each later page
        listing = builder.build()
        # ...but a page link the reader declared and the session did not follow means the list may miss pages.
        self.assertEqual(([item.key for item in listing.episodes], listing.complete, listing.reasons),
                         (["e1", "e2", "e3"], False, ("PAGE_NOT_FOLLOWED",)))
        for link in refused:
            with self.subTest(link=link):
                builder, _browser = walk({FILM_URL: (200, FILM_URL, page(entry("e1", number=1), links=(link,)))})
                self.assertEqual(builder.reasons, ["PAGE_NOT_FOLLOWED"])

    def test_links_back_to_pages_already_read_keep_the_list_whole(self):
        second = f"{FILM_URL}?page=2"
        pages = {FILM_URL: (200, FILM_URL, page(entry("e1", number=1),
                                               links=("?page=2", "#top", "", "/film/F1#list", FILM_URL))),
                 second: (200, second, page(entry("e2", number=2),
                                            links=("/film/F1", FILM_URL, "?page=2", "?page=2#top", f"{second}#x")))}
        builder, browser = walk(pages)
        listing = builder.build()
        self.assertEqual(browser.visited, [FILM_URL, second])
        self.assertEqual((listing.complete, listing.reasons, [item.key for item in listing.episodes]),
                         (True, (), ["e1", "e2"]))
        self.assertEqual(plan_selection(listing, mode="all").confirm_label, "Tải 2 tập")

    def test_a_page_on_another_portal_host_is_not_read_and_the_list_says_it_holds_only_what_was_seen(self):
        next_page = f"https://{WWW}/film/F1?page=2"  # a portal host of the source, but not the pasted page's host
        builder, browser = walk({FILM_URL: (200, FILM_URL, page(entry("e1", number=1), links=(next_page,))),
                                 next_page: (200, next_page, page(entry("e2", number=2)))})
        listing = builder.build()
        self.assertEqual(browser.visited, [FILM_URL])
        self.assertEqual((listing.complete, listing.reasons), (False, ("PAGE_NOT_FOLLOWED",)))
        self.assertEqual(listing.public()["message"], REASONS["PAGE_NOT_FOLLOWED"])
        plan = plan_selection(listing, mode="all")
        self.assertEqual((plan.complete, plan.confirm_label), (False, "Tải 1 tập đã thấy"))
        self.assertIn("chỉ gồm 1 tập trong phần đã thấy", plan.note)

    def test_a_link_outside_the_portal_is_refused_before_any_page(self):
        for url in ("https://tickets.alpha.example/t/1", "http://portal.alpha.example/film/F1", "https://evil.example/",
                    f"https://{PORTAL}/login", f"https://{PORTAL}/login/?next=film"):
            with self.subTest(url=url), self.assertRaises(SourceError) as caught:
                walk({}, url)
            self.assertEqual(caught.exception.code, "ACCOUNT_PAGE_ONLY")

    def test_limits_of_pages_and_time(self):
        pages = {FILM_URL: (200, FILM_URL, page(entry("e1", number=1), links=("?page=2",))),
                 f"{FILM_URL}?page=2": (200, f"{FILM_URL}?page=2", page(entry("e2", number=2)))}
        builder, browser = walk(pages, max_pages=1)
        self.assertEqual((builder.reasons, browser.visited), (["PAGE_LIMIT"], [FILM_URL]))
        builder, browser = walk(pages, stop_at=time.monotonic() + 5, reserve_seconds=10)
        self.assertEqual((builder.reasons, browser.visited), (["TIME_LIMIT"], [FILM_URL]))

    def test_a_later_page_that_fails_makes_the_list_incomplete_and_the_first_one_is_an_error(self):
        pages = {FILM_URL: (200, FILM_URL, page(entry("e1", number=1), links=("?page=2", "?page=3"))),
                 f"{FILM_URL}?page=2": "NAVIGATION_TIMEOUT", f"{FILM_URL}?page=3": (500, f"{FILM_URL}?page=3", None)}
        builder, _browser = walk(pages)
        self.assertEqual(builder.reasons, ["PAGE_FAILED"])
        cases = {"NAVIGATION_TIMEOUT": (HttpError, "NETWORK"), "DNS_FAILED": (HttpError, "NETWORK"),
                 "HOST_NOT_ALLOWED": (SourceError, "HOST_NOT_ALLOWED")}
        for code, (kind, expected) in cases.items():
            with self.subTest(code=code), self.assertRaises(kind) as caught:
                walk({FILM_URL: code})
            self.assertEqual(caught.exception.code, expected)

    def test_the_first_page_must_be_a_film_page(self):
        for answer, code in (((404, FILM_URL, None), "UNAVAILABLE"), ((403, FILM_URL, None), "FORBIDDEN"),
                             ((200, FILM_URL, None), "NOT_A_FILM_PAGE")):
            with self.subTest(code=code), self.assertRaises(SourceError) as caught:
                walk({FILM_URL: answer})
            self.assertEqual(caught.exception.code, code)
        with self.assertRaises(HttpError) as caught:
            walk({FILM_URL: (503, FILM_URL, None)})
        self.assertEqual((caught.exception.code, caught.exception.retryable), ("SERVER_BUSY", True))

    def test_a_sign_in_link_with_a_query_is_told_by_its_query(self):
        film_url = f"https://{PORTAL}/index.php?film=F1"
        browser = FakeBrowser({film_url: (200, film_url, page(entry("e1")))})
        builder = read_listing(browser, FakeReader(browser), QUERY_SOURCE, film_url)
        self.assertEqual(builder.pages, 1)  # another page of /index.php is not the sign-in page
        browser = FakeBrowser({film_url: (200, f"https://{PORTAL}/index.php?next=film&do=login", None)})
        with self.assertRaises(SignedOut):
            read_listing(browser, FakeReader(browser), QUERY_SOURCE, film_url)
        with self.assertRaises(SourceError) as caught:
            read_listing(browser, FakeReader(browser), QUERY_SOURCE, f"https://{PORTAL}/index.php?do=login")
        self.assertEqual(caught.exception.code, "ACCOUNT_PAGE_ONLY")

    def test_a_later_page_on_the_sign_in_page_only_makes_the_list_incomplete(self):
        pages = {FILM_URL: (200, FILM_URL, page(entry("e1", number=1), links=("?page=2", "?page=3"))),
                 f"{FILM_URL}?page=2": (200, f"https://{PORTAL}/login?next=2", None),
                 f"{FILM_URL}?page=3": (200, f"{FILM_URL}?page=3", page(entry("e3", number=3)))}
        builder, _browser = walk(pages)
        self.assertEqual((builder.reasons, builder.pages), (["PAGE_SIGNED_OUT"], 2))

    def test_a_page_without_a_film_id_is_not_a_film_page(self):
        with self.assertRaises(SourceError) as caught:
            walk({FILM_URL: (200, FILM_URL, page(entry("e1"), film="bad id"))})
        self.assertEqual(caught.exception.code, "NOT_A_FILM_PAGE")
        pages = {FILM_URL: (200, FILM_URL, page(entry("e1", number=1), links=("?page=2",))),
                 f"{FILM_URL}?page=2": (200, f"{FILM_URL}?page=2", page(entry("e2"), film=""))}
        builder, _browser = walk(pages)
        self.assertEqual(builder.reasons, ["PAGE_FAILED"])

    def test_sign_in_evidence_ends_the_walk(self):
        with self.assertRaises(SignedOut):
            walk({FILM_URL: (200, f"https://{PORTAL}/login/?next=film", None)})
        with self.assertRaises(SignedOut):
            walk({FILM_URL: (200, FILM_URL, page(entry("e1")))}, signed_out=True)
        builder, _browser = walk({FILM_URL: (200, FILM_URL, page(entry("e1")))})
        self.assertEqual(builder.pages, 1)

    def test_a_stop_condition_and_a_cancel(self):
        pages = {FILM_URL: (200, FILM_URL, page(entry("e1", number=1), links=("?page=2",))),
                 f"{FILM_URL}?page=2": (200, f"{FILM_URL}?page=2", page(entry("e2", number=2)))}
        builder, browser = walk(pages, stop=lambda seen: ("e1", "v1") in seen.navigation)
        self.assertEqual(browser.visited, [FILM_URL])
        self.assertEqual(builder.navigation[("e1", "v1")], (FILM_URL, "#get", None, None, None))
        self.assertEqual(builder.reasons, ["PARTIAL_READ"])  # page 2 was left unread
        builder, browser = walk(pages, stop=lambda seen: ("e2", "v1") in seen.navigation)
        self.assertEqual((browser.visited, builder.reasons), ([FILM_URL, f"{FILM_URL}?page=2"], []))
        browser = FakeBrowser(pages)
        browser.control.request("cancel")
        with self.assertRaises(Cancelled):
            read_listing(browser, FakeReader(browser), SOURCE, FILM_URL)
        self.assertEqual(browser.visited, [])


if __name__ == "__main__":
    unittest.main()
