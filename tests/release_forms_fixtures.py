"""A self-made film portal in the structure adapter "release-forms" reads (download_account_release_forms).

Modelled on what M7 observed on a real source, rebuilt by hand with ``.example`` hosts, made-up ids and titles:
no page, name, host or token of the real source is copied here. Served by the HTTPS fixture server of the
browser tests on the portal host.

- ``/phim/<film>``: ``div[data-movie-id]``, an ``h1`` title and ``section#download-files``. A series has
  ``.cd-download-season-list`` with ``details.cd-season`` (folded unless ``open_seasons``), each with
  ``<summary><span>Mùa N</span><span class="cd-download-season__count">…</span></summary>`` and
  ``.cd-season__body > article.cd-release`` cards; a film has ``.cd-release-list`` cards. A card: ``p.cd-
  release__name`` ("Tập N · <file name>" in a season), ``.cd-release__meta`` spans with "·" separator spans,
  and, signed in (the ``sid`` cookie), ``form[data-download-form][method=post][target=_blank]`` to
  ``/download-links/<file>/access`` with a hidden ``_token`` and a submit button; signed out, ``a.cd-action``
  to ``/login`` instead. Related films outside the area have cards of their own (never read).
- ``POST /download-links/<file>/access``: counted in ``posts``; mints a ticket token for that file and answers
  as ``redirect`` says: ``"302"`` (a redirect to ``/x/<token>`` on the ticket host, as M7 saw the new tab end
  there), ``"page"`` (a 200 page whose script goes there: not a redirect), ``"outside"`` (a redirect to a host
  outside the source) or ``"none"`` (a redirect without a Location).
- ``GET /x/<token>`` on the ticket host: the ticket page as M7 saw its structure: the file's name, and
  ``a#downloadBtn`` whose link (``/x/<token>/download`` on the file host) is in the page from the start, locked
  (``aria-disabled``, ``pointer-events-none``, ``data-state="loading"``) until a script ends the
  ``countdown`` (seconds) and sets ``data-state`` to ``ticket_end`` (unlocked only for ``"success"``;
  ``"loading"`` never ends). Names no file id, like the real one.
- ``GET /x/<token>/download`` on the file host: the clip; ``downloads`` records (token, time). ``file_ranges``
  False: the server ignores Range and sends the whole body with 200; ``file_delay``: a pause after each 64 KiB;
  ``file_status``: another status for every file request (a fresh link the server refuses).
- ``popups``: what a click on a card's ticket button opens first, by script, before the form opens its tab:
  ``"advert"`` (a page outside the source) and ``"other-ticket"`` (a ready ticket page of another file, on the
  ticket host, opened by script: never this click's answer).
- ``gate``: an inline script opens a modal ``dialog`` over the page at load (like the real page's gate, but
  without any outside script), so a user-like click on a card cannot happen.
- ``ticket_cookie``: the ticket page sets that value as its host's own cookie ``tk`` (host-only, HttpOnly).
- ``gate_script``: the same modal, closed only once the outside script at that URL (loaded with ``?_=<Date.now()>``
  added, as M7 saw the real page do) has run and set ``window.gateOk`` (M7 exception A, the source's
  ``page_script``): a browser that does not load it can never click a card.
- ``change_on_open``: opening a season renumbers its cards' "Tập N" labels (the page changed between reading
  it and asking for the ticket); the forms stay.
- ``film_on_open``: opening a season changes the page's ``data-movie-id`` to that id (the page became another
  film's between reading it and asking for the ticket); seasons, episodes and forms stay.
- ``auto_submit``: the film page submits its first download form by itself as it loads (never a user's click).
- ``extra_html``: more HTML at the end of the film page (a form a card's button names elsewhere, say);
  ``ticket_extra``: more HTML at the end of the ticket page (a player or file path it loads, say).
- ``popups`` may also hold ``"double-submit"``: the ticket button's click submits its form a second time by
  script, right after the click's own submit.
- ``ticket_sso``: the ticket host has a session of its own, made from the portal's by redirects, as M7 measured
  on the real source in BiliFlow's browser (the chain's shape only): without its cookie ``tks`` the ticket page
  redirects to the ticket host's ``/login?next=…``, which redirects to its ``/sso/redirect``, then to the
  portal's ``/sso/oauth/authorize`` (signed out: the portal's ``/login``), then back to the ticket host's
  ``/sso/oauth/callback``, which sets ``tks`` (host-only, HttpOnly) on its redirect to the ticket page: six
  redirects after the POST. ``sso_logins`` counts the portal's authorizations. During the countdown the
  button says the page checks for adverts, as the real one did.
"""
from __future__ import annotations

import html
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from tests.source_fixtures import Reply, Seen

HTML = "text/html; charset=utf-8"
TOKEN_VALUE = "BF-CANARY-csrf-7a1"


@dataclass(frozen=True)
class Card:
    """One release card: ``file`` (None: no form), ``prefix`` the "Tập N · " the page puts before the name
    (None: none), ``meta`` the meta texts, ``method`` its form's method, ``button`` more attributes of its submit
    button (a ``formmethod`` or ``formaction`` of its own)."""
    file: str | None
    name: str = "Film.Example.1080p.mkv"
    prefix: str | None = "Tập 1 · "
    meta: tuple[str, ...] = ("HD · 1080P", "Thuyết minh", "H264", "2.0 GB")
    method: str = "post"
    button: str = ""


def season_cards(numbers: list[int], first_file: int, *, meta: tuple[str, ...] | None = None) -> list[Card]:
    """Cards for episodes ``numbers`` (in that page order), file ids from ``first_file`` up in that order."""
    return [Card(str(first_file + index), f"Film.Example.E{number:02d}.mkv", f"Tập {number} · ",
                 meta or ("HD · 1080P", "Thuyết minh", "H264", "2.0 GB"))
            for index, number in enumerate(numbers)]


@dataclass
class ReleaseSite:
    film: str = "501"
    title: str = "Phim mẫu"
    seasons: list[tuple[str, list[Card]]] = field(default_factory=list)  # (label "Mùa N", cards)
    film_cards: list[Card] = field(default_factory=list)  # the film list (no seasons)
    open_seasons: bool = False
    gate: bool = False
    gate_script: str | None = None
    change_on_open: bool = False
    film_on_open: str | None = None
    cookie: str = "sid"
    body: bytes = b""  # what the file host serves for every file (a self-made clip)
    redirect: str = "302"
    countdown: float = 0.5
    ticket_end: str = "success"
    popups: tuple[str, ...] = ()
    ticket_cookie: str | None = None
    file_ranges: bool = True
    file_delay: float = 0.0
    file_status: int = 200
    auto_submit: bool = False
    extra_html: str = ""
    ticket_extra: str = ""
    ticket_sso: bool = False

    def __post_init__(self) -> None:
        self.lock = threading.Lock()
        self.posts: list[str] = []  # file ids of every POST the portal got, in order
        self.post_times: list[float] = []
        self.tokens: dict[str, str] = {}  # token -> file id, every ticket minted (by a POST or for a popup)
        self.downloads: list[tuple[str, float]] = []  # (token, time) of every file request
        self.hosts = ("", "", "")
        self.server: Any = None
        self.other_token = ""
        self.sso_logins = 0  # the portal's authorizations of a ticket host sign-in
        self.sso_codes: set[str] = set()

    def install(self, server: Any, portal: str, tickets: str, files: str) -> None:
        self.hosts, self.server = (portal, tickets, files), server
        server.route(f"/phim/{self.film}", self._film)
        server.route("/login", self._login)
        if self.ticket_sso:
            for path in ("/sso/redirect", "/sso/oauth/authorize", "/sso/oauth/callback"):
                server.route(path, self._sso)
        for card in self._all_cards():
            if card.file:
                server.route(f"/download-links/{card.file}/access", self._access)
        if "other-ticket" in self.popups:
            self.other_token = self._mint("888888")

    def _mint(self, file: str) -> str:
        with self.lock:
            token = f"{len(self.tokens) + 1:02d}{secrets.token_hex(15)}"
            self.tokens[token] = file
        self.server.route(f"/x/{token}", self._ticket)
        self.server.route(f"/x/{token}/download", self._file)
        return token

    def _file(self, seen: Seen, _number: int) -> Reply:
        token = seen.path.split("/")[2]
        if seen.host != self.hosts[2] or not self.body:
            return Reply(b"no file", 404, "text/plain")
        with self.lock:
            self.downloads.append((token, time.monotonic()))
        if self.file_status != 200:
            return Reply(b"denied", self.file_status, "text/plain")
        return Reply(self.body, content_type="video/x-matroska", etag=f'"{token}-1"', ranges=self.file_ranges,
                     delay=self.file_delay)

    def _login(self, seen: Seen, _number: int) -> Reply:
        if self.ticket_sso and seen.host == self.hosts[1]:  # the ticket host's own sign-in: through the portal
            after = seen.query.get("next", [""])[0]
            return self._redirect(f"https://{self.hosts[1]}/sso/redirect?next={quote(after, safe='')}")
        return Reply(b"<form id='cd-login-form'></form>", content_type=HTML)

    @staticmethod
    def _redirect(location: str, cookie: str | None = None) -> Reply:
        headers = {"Location": location}
        if cookie:
            headers["Set-Cookie"] = cookie
        return Reply(b"", 302, HTML, headers=headers)

    def _sso(self, seen: Seen, _number: int) -> Reply:
        after = seen.query.get("next", seen.query.get("state", [""]))[0]
        if not after.startswith("/x/"):
            return Reply(b"bad next", 400, "text/plain")
        if seen.path == "/sso/redirect" and seen.host == self.hosts[1]:
            return self._redirect(f"https://{self.hosts[0]}/sso/oauth/authorize?client=tickets"
                                  f"&state={quote(after, safe='')}")
        if seen.path == "/sso/oauth/authorize" and seen.host == self.hosts[0]:
            if not self._signed_in(seen):
                return self._redirect(f"https://{self.hosts[0]}/login")
            code = secrets.token_hex(12)
            with self.lock:
                self.sso_logins += 1
                self.sso_codes.add(code)
            return self._redirect(f"https://{self.hosts[1]}/sso/oauth/callback?code={code}"
                                  f"&state={quote(after, safe='')}")
        if seen.path == "/sso/oauth/callback" and seen.host == self.hosts[1]:
            with self.lock:
                known = seen.query.get("code", [""])[0] in self.sso_codes
            if not known:
                return Reply(b"bad code", 403, "text/plain")
            return self._redirect(f"https://{self.hosts[1]}{after}",
                                  f"tks={secrets.token_hex(8)}; Path=/; Secure; HttpOnly; SameSite=Lax")
        return Reply(b"wrong host", 404, "text/plain")

    def _ticket(self, seen: Seen, _number: int) -> Reply:
        token = seen.path.split("/")[2]
        if seen.host != self.hosts[1]:
            return Reply(b"wrong host", 404, "text/plain")
        if self.ticket_sso and not any(part.strip().startswith("tks=")
                                       for part in seen.headers.get("cookie", "").split(";")):
            return self._redirect(f"https://{self.hosts[1]}/login?next={quote(seen.path, safe='')}")
        name = next((card.name for card in self._all_cards() if card.file == self.tokens[token]), "Other.Film.mkv")
        end = "" if self.ticket_end == "loading" else (
            f"b.dataset.state = '{self.ticket_end}';"
            + ("b.removeAttribute('aria-disabled'); b.classList.remove('pointer-events-none');"
               "b.textContent = 'Tải Xuống Ngay';" if self.ticket_end == "success" else ""))
        page = (f'<!doctype html><meta charset="utf-8"><title>Tải xuống - {html.escape(name)}</title>'
                f'<h1>Tải Xuống Tệp Tin</h1><p>{html.escape(name)}</p><div id="countdownBox">Vui lòng chờ</div>'
                f'<a id="downloadBtn" href="https://{self.hosts[2]}/x/{token}/download" data-state="loading" '
                'aria-disabled="true" class="btn pointer-events-none">Đang kiểm tra quảng cáo...</a>'
                f"<script>setTimeout(() => {{ const b = document.getElementById('downloadBtn'); {end} }}, "
                f"{int(self.countdown * 1000)});</script>{self.ticket_extra}")
        headers = ({"Set-Cookie": f"tk={self.ticket_cookie}; Path=/; Secure; HttpOnly; SameSite=Lax"}
                   if self.ticket_cookie else {})
        return Reply(page.encode("utf-8"), content_type=HTML, headers=headers)

    @property
    def url(self) -> str:
        return f"https://{self.hosts[0]}/phim/{self.film}"

    def _all_cards(self) -> list[Card]:
        return [card for _label, cards in self.seasons for card in cards] + list(self.film_cards)

    def _signed_in(self, seen: Seen) -> bool:
        return any(part.strip().startswith(f"{self.cookie}=") for part in seen.headers.get("cookie", "").split(";"))

    def _film(self, seen: Seen, _number: int) -> Reply:
        if seen.host != self.hosts[0]:
            return Reply(b"wrong host", 404, "text/plain")
        return Reply(self.film_html(self._signed_in(seen)).encode("utf-8"), content_type=HTML)

    def _access(self, seen: Seen, _number: int) -> Reply:
        file = seen.path.split("/")[2]
        if seen.method != "POST" or not self._signed_in(seen):
            return Reply(b"", 302, HTML, headers={"Location": "/login"})
        with self.lock:
            self.posts.append(file)
            self.post_times.append(time.monotonic())
        ticket = f"https://{self.hosts[1]}/x/{self._mint(file)}"
        if self.redirect == "page":
            page = f'<!doctype html><script>location.href = "{ticket}";</script>'
            return Reply(page.encode("utf-8"), content_type=HTML)
        if self.redirect == "outside":
            return Reply(b"", 302, HTML, headers={"Location": ticket.replace(self.hosts[1], "outside.example")})
        if self.redirect == "none":
            return Reply(b"", 302, HTML)
        return Reply(b"", 302, HTML, headers={"Location": ticket})

    def tokens_of(self, file: str) -> list[str]:
        """The tokens minted for ``file``, in order."""
        with self.lock:
            return [token for token, owner in self.tokens.items() if owner == file]

    def card_html(self, card: Card, signed_in: bool) -> str:
        meta = '<span>·</span> '.join(f"<span>{html.escape(part)}</span>" for part in card.meta)
        if signed_in and card.file:
            action = (f'<form method="{card.method}" '
                      f'action="https://{self.hosts[0]}/download-links/{card.file}/access" '
                      'data-download-form target="_blank" rel="noopener">'
                      f'<input type="hidden" name="_token" value="{TOKEN_VALUE}">'
                      f'<button class="cd-action" type="submit"{card.button}>Lấy vé tải</button></form>')
        elif signed_in:
            action = ""
        else:
            action = f'<a class="cd-action" href="https://{self.hosts[0]}/login">Đăng nhập để tải</a>'
        name = html.escape((card.prefix or "") + card.name)
        return (f'<article class="cd-release"><div class="min-w-0"><p class="cd-release__name">{name}</p>'
                f'<div class="cd-release__meta">{meta}</div></div>{action}</article>')

    def film_html(self, signed_in: bool) -> str:
        parts = []
        if self.seasons:
            seasons = "".join(
                f'<details class="cd-season"{" open" if self.open_seasons else ""}><summary><span>{html.escape(label)}'
                f'</span><span class="cd-download-season__count">{len(cards)} file</span></summary>'
                f'<div class="cd-season__body">{"".join(self.card_html(card, signed_in) for card in cards)}</div>'
                '</details>' for label, cards in self.seasons)
            parts.append('<p class="cd-download-season-hint">Bấm vào mùa.</p>'
                         f'<div class="cd-download-season-list">{seasons}</div>')
        if self.film_cards:
            parts.append('<div class="cd-release-list">'
                         f'{"".join(self.card_html(card, signed_in) for card in self.film_cards)}</div>')
        related = self.card_html(Card("999999", "Other.Film.mkv", "Tập 1 · "), signed_in)
        gate = ('<dialog id="gate"><p>Cần tắt chặn quảng cáo</p><button id="retry">Kiểm tra lại</button></dialog>'
                "<script>document.getElementById('gate').showModal();"
                "document.getElementById('gate').addEventListener('cancel', e => e.preventDefault());</script>"
                if self.gate else "")
        if self.gate_script is not None:  # closed only by the outside script, once it has run
            gate = ('<dialog id="gate"><p>Cần tắt chặn quảng cáo</p></dialog>'
                    "<script>(() => { const g = document.getElementById('gate'); g.showModal();"
                    "g.addEventListener('cancel', e => e.preventDefault());"
                    f"const s = document.createElement('script'); s.src = '{self.gate_script}?_=' + Date.now();"
                    "s.onload = () => { if (window.gateOk) g.close(); }; document.head.appendChild(s); })();</script>")
        change = ("<script>document.querySelectorAll('details.cd-season').forEach(d => d.addEventListener('toggle', "
                  "() => { if (!d.open) return; d.querySelectorAll('.cd-release__name').forEach(p => { "
                  "p.textContent = p.textContent.replace(/^Tập (\\d+) ·/, 'Tập 9$1 ·'); }); }));</script>"
                  if self.change_on_open else "")
        if self.film_on_open is not None:  # on the summary's click itself, so it is done when the click returns
            change += ("<script>document.querySelectorAll('details.cd-season > summary').forEach(s => "
                       "s.addEventListener('click', () => document.querySelector('[data-movie-id]').setAttribute("
                       f"'data-movie-id', '{html.escape(self.film_on_open)}')));</script>")
        opens = []
        if "advert" in self.popups:
            opens.append("window.open('https://advert.example/ad');")
        if "other-ticket" in self.popups:
            opens.append(f"window.open('https://{self.hosts[1]}/x/{self.other_token}');")
        if opens:  # on the ticket button's click, before the form opens its own tab
            change += ("<script>document.querySelectorAll('form[data-download-form] button').forEach(b => "
                       f"b.addEventListener('click', () => {{ {' '.join(opens)} }}));</script>")
        if "double-submit" in self.popups:  # a second submit by script, right after the click's own
            change += ("<script>document.querySelectorAll('form[data-download-form] button').forEach(b => "
                       "b.addEventListener('click', () => setTimeout(() => b.form.requestSubmit(), 50)));</script>")
        if self.auto_submit:  # as the page is parsed: no click of anyone
            change += ("<script>const f = document.querySelector('#download-files form[data-download-form]');"
                       "if (f) f.requestSubmit();</script>")
        change += self.extra_html
        login = "" if signed_in else f'<a href="https://{self.hosts[0]}/login">Đăng nhập</a>'
        return (f'<!doctype html><meta charset="utf-8"><title>{html.escape(self.title)}</title>{login}'
                f'<main><div data-movie-id="{self.film}"><h1>{html.escape(self.title)}</h1></div>'
                f'<section id="download-files"><div class="cd-download-workbench__main">{"".join(parts)}</div>'
                f'</section><section class="related">{related}</section></main>{gate}{change}')
