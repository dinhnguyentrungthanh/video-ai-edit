"""A self-made film site of a source account, for the M3 tests: film pages, ticket pages and a file server.

Everything runs on the HTTPS fixture server of the browser tests (``.example`` hosts, a throwaway CA): the
film pages on the portal host, ticket pages on the ticket host, files on the file host. ``FixtureReader``
reads exactly this structure. It is the test's own page structure, never a real site's: a real source gets
a reader only once its pages were checked with the user (M7).

The pages:
- ``/film/<film>?page=N``: ``main#film[data-film][data-kind]``, ``h1`` title, the download area
  ``#downloads .entry`` (ids and numbers in ``data-*``, a ``data-role`` marks a trailer or an ad inside it),
  promotions in ``aside.promo .entry`` (never files), page links in ``nav.pages a``, and an optional "load
  more" button without a link. Without the session cookie it redirects to the sign-in page ``/login``.
- ``/t/<episode>--<variant>``: the ticket page: ``#ticket[data-episode][data-file][data-state]``; it waits
  ``wait`` seconds (its own timer) and then shows ``a#dl`` with a fresh token link to the file host. A
  behaviour per file makes it expire, report the file gone, ask for a challenge, sign out, close itself, or
  show the ticket of another episode's file with the same variant id.
- ``/f/<file>?token=…``: the file, only for a token a ticket page gave out (403 otherwise), with an ETag.
- ``/ad``: an advert page on the portal; clicking a ticket link also opens it (and a popup to an outside host).
"""
from __future__ import annotations

import html
import itertools
import threading
from dataclasses import dataclass, field
from typing import Any

from biliflow.download_account_listing import FilmPage, RawEntry, checked_id
from biliflow.download_account_pages import PageView, TicketPage
from tests.source_fixtures import Reply, Seen

HTML = "text/html; charset=utf-8"
TOKEN_MARK = "BF-CANARY-ticket"
ENTRY_ATTRIBUTES = ("data-role", "data-season", "data-season-number", "data-season-label", "data-episode",
                    "data-number", "data-label", "data-special", "data-after", "data-variant", "data-variant-label",
                    "data-quality", "data-audio", "data-size", "data-version")


@dataclass(frozen=True)
class Item:
    """One file of the fixture film (an episode's variant), or a trailer or an ad (``role``)."""
    episode: str
    variant: str = "v1080"
    season: str | None = None
    season_number: int | None = None
    number: int | None = None
    label: str | None = None
    special: bool = False
    after: int | None = None
    quality: str | None = "1080p"
    audio: str | None = "Vietsub"
    size: int | None = None
    version: str | None = None
    role: str | None = None
    variant_label: str | None = None  # the source's own name of the variant (``data-variant-label``)

    def attributes(self) -> str:
        values = {"data-episode": self.episode, "data-variant": self.variant, "data-season": self.season,
                  "data-season-number": self.season_number, "data-number": self.number, "data-label": self.label,
                  "data-special": "1" if self.special else None, "data-after": self.after,
                  "data-quality": self.quality, "data-audio": self.audio, "data-size": self.size,
                  "data-version": self.version, "data-role": self.role, "data-variant-label": self.variant_label}
        return " ".join(f'{name}="{html.escape(str(value))}"' for name, value in values.items() if value is not None)


def series(seasons: dict[str, list[int]], *,
           variants: tuple[tuple[str, str, str], ...] = (("v1080", "1080p", "Vietsub"),),
           missing: dict[str, set[str]] | None = None) -> list[Item]:
    """Every episode of ``seasons`` ({season id: [numbers]}) in each variant (id, quality, audio), in the given
    order; ``missing``: episode id → variant ids it lacks."""
    items = []
    for season_number, (season, numbers) in enumerate(seasons.items(), start=1):
        for number in numbers:
            episode = f"{season}e{number}"
            for variant, quality, audio in variants:
                if variant in (missing or {}).get(episode, set()):
                    continue
                items.append(Item(episode, variant, season, season_number, number, f"Tập {number}",
                                  quality=quality, audio=audio, size=1000 + number))
    return items


@dataclass
class TicketBehaviour:
    """What the ticket page of one file does: ``wait`` seconds of its own timer, then ``state``."""
    wait: float = 0.3
    state: str = "ready"  # ready, expired, gone, challenge, error, login (a sign-in redirect), other (the ticket
    # of another episode's file with the same variant id), close (the tab closes itself)
    times: int | None = None  # this behaviour for the first N loads only, then "ready"


def item_key(item: Item) -> str:
    return f"{item.episode}--{item.variant}"


@dataclass
class FilmSite:
    """The film pages, ticket pages and files of one fixture film (module docstring)."""
    film: str = "F1"
    title: str = "Phim thử"
    kind: str = "series"
    pages: list[list[Item]] = field(default_factory=list)
    promos: list[Item] = field(default_factory=list)
    extra_links: dict[int, list[str]] = field(default_factory=dict)  # page number → more page links
    more_button: bool = False
    film_on_page: dict[int, str] = field(default_factory=dict)  # page number → another film id (a wrong page)
    status_of_page: dict[int, int] = field(default_factory=dict)  # page number → an error status
    signed_out_page: bool = False  # the film page says "not signed in" without redirecting
    cookie: str = "sid"
    session_value: str | None = None  # the session cookie's value the pages accept (None: any)
    tickets: dict[str, TicketBehaviour] = field(default_factory=dict)  # "<episode>--<variant>" → behaviour
    files: dict[str, bytes] = field(default_factory=dict)  # "<episode>--<variant>" → body
    etags: dict[str, str | None] = field(default_factory=dict)
    gone: set[str] = field(default_factory=set)  # files the file server answers 404 for
    rotate: str | None = None  # a Set-Cookie value the film page sends (a rotated session cookie)
    hang_script: bool = False  # the film page runs a script that never ends
    ads: bool = True  # a ticket click also opens an advert on the portal and a popup to an outside host
    refuse_files: int = 0  # the file server answers 403 to the first N requests with a valid token
    rotating: bool = False  # each film page wants the latest session value and sets the next one

    def __post_init__(self) -> None:
        self.lock = threading.Lock()
        self.tokens: dict[str, str] = {}  # token → file
        self.loads: dict[str, int] = {}
        self.counter = itertools.count(1)
        self.hosts = ("", "", "")
        self.current: str | None = None  # the session value a rotating site accepts now
        self.refused = 0
        # Every session value a film page set and every ticket token a ticket page gave out, in order: secrets the
        # tests look for in answers, pages and files (``tokens`` may lose entries a test removes; this never does).
        self.issued: list[str] = []

    def install(self, server: Any, portal: str, tickets: str, files: str) -> None:
        self.hosts = (portal, tickets, files)
        server.route(f"/film/{self.film}", self._film)
        server.route("/login", lambda seen, number: Reply(b"<p id='login'>Dang nhap</p>", content_type=HTML))
        server.route("/ad", lambda seen, number: Reply(b"<p>quang cao</p>", content_type=HTML))
        keys = {item_key(item) for page in self.pages for item in page} | set(self.tickets) | set(self.files)
        for key in keys | {item_key(item) for item in self.promos}:
            server.route(f"/t/{key}", self._ticket)
            server.route(f"/f/{key}", self._file)

    @property
    def url(self) -> str:
        return f"https://{self.hosts[0]}/film/{self.film}"

    # The pages -------------------------------------------------------------------------------------------

    @staticmethod
    def _cookies(seen: Seen) -> dict[str, str]:
        return dict(part.strip().split("=", 1) for part in seen.headers.get("cookie", "").split(";") if "=" in part)

    def _signed_in(self, seen: Seen) -> bool:
        cookies = self._cookies(seen)
        return self.cookie in cookies and (self.session_value is None or cookies[self.cookie] == self.session_value)

    def _film(self, seen: Seen, _number: int) -> Reply:
        if seen.host != self.hosts[0]:
            return Reply(b"wrong host", 404, "text/plain")
        if not self._signed_in(seen):
            return Reply(b"", 302, HTML, headers={"Location": "/login?next=film"})
        headers: dict[str, str] = {}
        if self.rotating:
            with self.lock:
                sent = self._cookies(seen).get(self.cookie)
                if self.current is not None and sent != self.current:
                    return Reply(b"", 302, HTML, headers={"Location": "/login?next=stale"})
                self.current = f"rot-{next(self.counter)}"
                self.issued.append(self.current)
            headers["Set-Cookie"] = f"{self.cookie}={self.current}; Domain=alpha.example; Path=/; Secure; HttpOnly"
        elif self.rotate:
            self.issued.append(self.rotate)
            headers["Set-Cookie"] = f"{self.cookie}={self.rotate}; Domain=alpha.example; Path=/; Secure; HttpOnly"
        number = int((seen.query.get("page") or ["1"])[0])
        if number in self.status_of_page:
            return Reply(b"<p>error</p>", self.status_of_page[number], HTML)
        if not 1 <= number <= len(self.pages):
            return Reply(b"no page", 404, "text/plain")
        return Reply(self.film_html(number).encode("utf-8"), content_type=HTML, headers=headers)

    def film_html(self, number: int) -> str:
        film = self.film_on_page.get(number, self.film)
        tickets = self.hosts[1]
        entries = "".join(
            f'<div class="entry" {item.attributes()}><span>{html.escape(item.label or item.episode)}</span>'
            f'<a class="get" target="_blank" href="https://{tickets}/t/{item_key(item)}">Lấy link</a></div>'
            for item in self.pages[number - 1])
        promos = "".join(f'<div class="entry" {item.attributes()}><a class="get" target="_blank" '
                         f'href="https://{tickets}/t/{item_key(item)}">Xem</a></div>' for item in self.promos)
        links = [f"?page={page}" if page > 1 else f"/film/{self.film}" for page in range(1, len(self.pages) + 1)
                 if page != number]
        links += self.extra_links.get(number, [])
        nav = "".join(f'<a href="{html.escape(link)}">{index}</a>' for index, link in enumerate(links, 1))
        more = '<button class="load-more">Tải thêm</button>' if self.more_button else ""
        signed_out = '<p id="signed-out">Bạn chưa đăng nhập</p>' if self.signed_out_page else ""
        hang = "<script>setTimeout(() => { while (true) {} }, 50);</script>" if self.hang_script else ""
        ads = ("<script>document.addEventListener('click', event => { if (event.target.closest('a.get')) {"
               " window.open('https://ads.evil.example/pop'); window.open('/ad'); } }, true);</script>"
               if self.ads else "")
        return (f'<!doctype html><meta charset="utf-8"><title>{html.escape(self.title)}</title>{signed_out}'
                f'<main id="film" data-film="{html.escape(film)}" data-kind="{self.kind}"><h1>{html.escape(self.title)}'
                f'</h1><aside class="promo">{promos}</aside><section id="downloads">{entries}</section>'
                f'<nav class="pages">{nav}</nav>{more}</main>{ads}{hang}')

    def _ticket(self, seen: Seen, _number: int) -> Reply:
        if seen.host != self.hosts[1]:
            return Reply(b"wrong host", 404, "text/plain")
        key = seen.path.rsplit("/", 1)[1]
        with self.lock:
            self.loads[key] = self.loads.get(key, 0) + 1
            behaviour = self.tickets.get(key, TicketBehaviour())
            if behaviour.times is not None and self.loads[key] > behaviour.times:
                behaviour = TicketBehaviour(wait=behaviour.wait)
            token = f"tok-{next(self.counter)}-{TOKEN_MARK}"
            self.tokens[token] = key
            self.issued.append(token)
        if behaviour.state == "login" or not self._signed_in(seen):
            return Reply(b"", 302, HTML, headers={"Location": f"https://{self.hosts[0]}/login?next=ticket"})
        episode, variant = key.split("--", 1)
        shown = "other-episode" if behaviour.state == "other" else episode
        final = "ready" if behaviour.state == "other" else behaviour.state
        link = f"https://{self.hosts[2]}/f/{key}?token={token}"
        script = (f"<script>setTimeout(() => {{ const t = document.getElementById('ticket'); if ('{final}' === "
                  f"'close') {{ window.close(); return; }} t.dataset.state = '{final}'; if ('{final}' === 'ready') {{ "
                  f"t.innerHTML = '<a id=\"dl\" href=\"{link}\">Tải xuống</a>'; }} }}, {int(behaviour.wait * 1000)});"
                  f"</script>")
        body = (f'<!doctype html><meta charset="utf-8"><div id="ticket" data-episode="{html.escape(shown)}" '
                f'data-file="{html.escape(variant)}" data-state="waiting">Đang chuẩn bị link…</div>{script}')
        return Reply(body.encode("utf-8"), content_type=HTML)

    def _file(self, seen: Seen, _number: int) -> Reply:
        if seen.host != self.hosts[2]:
            return Reply(b"wrong host", 404, "text/plain")
        key = seen.path.rsplit("/", 1)[1]
        with self.lock:
            valid = self.tokens.get((seen.query.get("token") or [""])[0]) == key
        if not valid:
            return Reply(b"expired", 403, "text/plain")
        with self.lock:
            refuse = self.refused < self.refuse_files
            self.refused += refuse
        if refuse:
            return Reply(b"refused", 403, "text/plain")
        if key in self.gone:
            return Reply(b"gone", 404, "text/plain")
        return Reply(self.files[key], content_type="video/x-matroska", etag=self.etags.get(key, f'"{key}-1"'))

    def ticket_loads(self) -> int:
        with self.lock:
            return sum(self.loads.values())


class FixtureReader:
    """The page structure of the fixture site above (see the module docstring); only reads ``PageView``."""

    def film_page(self, view: PageView) -> FilmPage | None:
        main = view.first("main#film", ("data-film", "data-kind"))
        if main is None:
            return None
        heading = view.first("main#film h1")
        entries = [self._entry(item, "file") for item in view.read("#downloads .entry", ENTRY_ATTRIBUTES)]
        entries += [self._entry(item, "promo") for item in view.read("aside.promo .entry", ENTRY_ATTRIBUTES)]
        links = tuple(item["href"] for item in view.read("nav.pages a", ("href",)) if item.get("href"))
        return FilmPage(main.get("data-film"), heading["text"] if heading else "", main.get("data-kind"),
                        tuple(entries), links, view.first("button.load-more") is not None)

    @staticmethod
    def _entry(item: dict[str, str | None], area: str) -> RawEntry:
        role = item.get("data-role") or ("file" if area == "file" else "promo")
        episode, variant = item.get("data-episode"), item.get("data-variant")
        ids = (checked_id(episode), checked_id(variant))  # the selector is built from checked ids only
        trigger = (f'#downloads .entry[data-episode="{ids[0]}"][data-variant="{ids[1]}"] a.get'
                   if role == "file" and None not in ids else None)
        return RawEntry(role, episode, variant, item.get("data-season"), item.get("data-season-number"),
                        item.get("data-season-label"), item.get("data-number"), item.get("data-label"),
                        item.get("data-special") == "1", item.get("data-after"), item.get("data-variant-label"),
                        item.get("data-quality"), item.get("data-audio"), item.get("data-size"),
                        item.get("data-version"), trigger)

    def signed_out(self, view: PageView) -> bool:
        return view.first("#signed-out") is not None

    def ticket_page(self, view: PageView) -> TicketPage | None:
        ticket = view.first("#ticket", ("data-episode", "data-file", "data-state"))
        if ticket is None:
            return None
        link = view.first("#ticket a#dl", ("href",))
        return TicketPage(ticket.get("data-episode"), ticket.get("data-file"), ticket.get("data-state") or "",
                          link.get("href") if link else None)
