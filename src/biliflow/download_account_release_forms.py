"""Adapter "release-forms" (M7): the page reader and sign-in verifier of a film portal whose pages list files as
release cards with one download form each (docs/SOURCE_ACCOUNTS_PLAN.md). Written from a real source's pages,
read with the user signed in in M7; the source's host, sign-in URL and film names are only in the Git-ignored
local config and the private survey, never here. Only the structure is described here.

The film page (``FilmPageReader``; ``PageView`` reads only, it cannot click or run a script):

- the film id is the page's ``[data-movie-id]`` (digits); the title its first ``h1``;
- the download area is ``#download-files``; nothing outside it is read (related films, trailers, comments);
- a series shows seasons as ``details.cd-season`` elements of ``.cd-download-season-list``, folded at first.
  A season's number is the number of its label ``<summary><span>Mùa N</span>…``; its cards are
  ``.cd-season__body > article.cd-release``. A card's episode number is the ``Tập N ·`` the page puts before
  the file name in ``.cd-release__name`` (never a number read from the file name itself);
- a film shows its cards in ``.cd-release-list`` without seasons or episode numbers;
- a card's meta is the ``span``s of ``.cd-release__meta`` (quality, audio, codec, size as text; the ``·``
  spans between them are separators);
- signed in, a card holds one ``form[data-download-form]`` that POSTs to ``/download-links/<file id>/access``
  on the page's own host (in a new tab); signed out, it holds a link to the sign-in page instead. A form that
  is not a POST, or one of whose submit controls (``button``, ``input`` submit or image; more than eight are
  not all read) has its own ``formaction`` or ``formmethod`` (even empty), makes its card unreadable. The
  clicked control itself is checked again right before the click, form owner included (download_account_pages).

Identity (download_account_listing): the variant id is the file id of the form. The source shows no episode
id, so a series episode's key is normalized from the season and episode numbers the page labels
(``s<season>:e<episode>``, as numbers: ``s2:e2``); two cards with the same key are two variants of that
episode. A film is one episode, ``film:<film id>``, and its cards are its variants. A card without a readable
number, form or file id cannot be chosen (the list says it is incomplete); a card of a film list on a series
page is not given a guessed number. A season's cards sit in a folded ``details``: each entry's ``reveal``
names that season by the form it holds, so the runtime opens exactly that season before the one click.

Signed out: the download area has a card link to ``/login`` on the page's host and no download form.

Tickets (one ticket page seen on the real source in M7, in the user's own browser; its structure only): the
card's form opens the ticket page in a new tab on the source's ticket host. That page names the file by its
name and size only, never by an id, so ``shows_ticket_ids`` is False: the runtime takes a ticket only from the
page that answers the click's own POST (each entry's ``request``, the form's action, with ``request_method``
"POST": a GET of that action is never taken for it; download_account_pages).
Its download button is ``a#downloadBtn``; its link is in the page from the start, but the page's own script
keeps the button locked (``aria-disabled="true"``, class ``pointer-events-none``) and its ``data-state`` not
``success`` until its countdown and its checks end. ``ticket_page``: ``success``, unlocked, with a link: ready
(the link is read, never followed by the browser); ``blocked``: a challenge (the page's own check was not
passed; BiliFlow never answers or removes it); ``error``: an error; anything else, the link included: waiting.

One ticket observed in BiliFlow's own session browser on a test root (M7, download_account_observe; plan
section 9.20 has the measured facts, structure only):

- the POST redirects to the ticket page; without a session of its own, the ticket host signs in from the
  portal's session by redirects within that same chain (its ``/login``, a step of its own, the portal's
  authorization, its callback, the ticket page again: six redirects after the POST, under MAX_HOPS); its own
  cookies are then saved with the session; no sign-in of its own was asked for;
- the button stayed locked (``data-state="loading"``, a text about the page's advert check) during an 8-second
  countdown, then ``success`` and unlocked; the advert check passed in BiliFlow's browser; the reader said ready
  exactly then;
- the link is on a third host of the source (its file host), a ``…/<token>/download`` path without query; a
  cookie-free Range request without Referer got 206 with a strong ETag (MKV with video and audio).

``reads_tickets`` is True on this observed contract (the user decided so after a review, 2026-10-10). It adds
no way around a check: a ticket is still asked for only through the entry's request and method, the clicked
control's own submission, the click's own redirects and the page's countdown (download_account_pages), and
its link is taken only on a configured file host (TICKET_LINK_REFUSED otherwise).

Not seen yet: a ``blocked``, ``error``, expired or signed-out ticket page. None of those ever gives a link: a
challenge or an error ends the ticket with its own code, and a page that leaves, closes or never turns ready
ends it when the ticket wait does. The page's text about the link's lifetime and resuming on the same network
was not measured, and one Range from byte 0 with a strong ETag does not prove a resume across tickets or
networks.

The sign-in verifier (``NotificationsVerifier``): the window shows a page of a portal host that is not the
sign-in page, and a fresh fetch of ``/ajax/notifications`` on that host (from the shown page, with its own
cookies, redirects not followed) answers 200 with a JSON object of exactly ``notifications`` (a list) and
``unread_count`` (a whole number >= 0). Signed out, that address redirects to the sign-in page (seen in M7),
which the fetch does not follow. A name, an avatar, a new URL or a 200 HTML page is never evidence.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlsplit

from biliflow.download_account_listing import FilmPage, RawEntry, TicketPage, checked_id

if TYPE_CHECKING:  # the views are passed in; this module never imports the browser (no import cycle)
    from biliflow.download_account_login import LoginView
    from biliflow.download_account_pages import PageView

AREA = "#download-files"
SEASON_LIST = f"{AREA} .cd-download-season-list"
FILM_LIST = f"{AREA} .cd-release-list"
FORM = "form[data-download-form]"
FORM_PATH = re.compile(r"/download-links/([0-9]{1,12})/access")
SEASON_LABEL = re.compile(r"Mùa\s+([0-9]{1,4})")
EPISODE_PREFIX = re.compile(r"Tập\s+([0-9]{1,5})\s+·")
FILM_ID = re.compile(r"[0-9]{1,12}")
TICKET_BUTTON = "a#downloadBtn"
TICKET_BUTTON_ATTRIBUTES = ("href", "data-state", "aria-disabled", "class")
SIGN_IN_PATH = "/login"
NOTIFICATIONS_PATH = "/ajax/notifications"
MAX_SEASONS = 60
MAX_CARDS = 600  # cards read per page; more makes the list incomplete (MORE_NOT_LINKED)
SUBMITS = ':is(button, input[type="submit" i], input[type="image" i])'  # the controls that can submit a form
MAX_SUBMITS = 8  # of them in one card's form; more makes the card unreadable
SEPARATOR = "·"


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _file_id(view: PageView, action: str | None) -> str | None:
    """The file id of a download form's action: https, the page's own host, default port, exactly
    ``/download-links/<digits>/access`` without a query or fragment; None otherwise."""
    if not action:
        return None
    try:
        target = urlsplit(urljoin(view.url, action))
        port = target.port
    except ValueError:
        return None
    if (target.scheme != "https" or port not in (None, 443) or target.query or target.fragment
            or target.username or target.password or (target.hostname or "").lower() != _host(view.url)):
        return None
    found = FORM_PATH.fullmatch(target.path)
    return found.group(1) if found else None


def _trigger(file_id: str) -> str:
    return f'{AREA} {FORM}[action$="/download-links/{file_id}/access"] button[type="submit"]'


def _season_of(file_id: str) -> str:
    return f'{SEASON_LIST} > details.cd-season:has({FORM}[action$="/download-links/{file_id}/access"])'


def unnumbered(card: RawEntry) -> RawEntry:
    """A card whose episode cannot be told from the page's labels: kept as a file without an episode key, so
    the list counts it as unreadable (incomplete) instead of guessing one."""
    return replace(card, episode=None, reveal=None)


class FilmPageReader:
    """The film page and signed-out evidence of adapter "release-forms" (module docstring)."""

    reads_tickets = True  # its ticket page's contract was observed in M7 (docstring); every ticket check stays
    shows_ticket_ids = False  # the ticket page names no file id: a ticket only through the click's provenance

    def film_page(self, view: PageView) -> FilmPage | None:
        if not view.read(AREA, (), 1):
            return None
        film = ((view.first("[data-movie-id]", ("data-movie-id",)) or {}).get("data-movie-id") or "").strip()
        if not FILM_ID.fullmatch(film):
            return None
        title = ((view.first("h1") or {}).get("text") or "").strip()
        seasons = len(view.read(f"{SEASON_LIST} > details", (), MAX_SEASONS + 1))
        if seasons:
            entries, more = self._series(view, seasons)
            loose, cut = self._cards(view, FILM_LIST, MAX_CARDS)
            entries += [unnumbered(card) for card in loose]  # a film list on a series page: no number to use
            return FilmPage(film, title, "series", tuple(entries), (), more or cut or seasons > MAX_SEASONS)
        cards, more = self._cards(view, FILM_LIST, MAX_CARDS)
        entries = [replace(card, episode=f"film:{film}", episode_label=title or None) for card in cards]
        return FilmPage(film, title, "film", tuple(entries), (), more)

    def _series(self, view: PageView, count: int) -> tuple[list[RawEntry], bool]:
        entries: list[RawEntry] = []
        more = False
        for index in range(1, min(count, MAX_SEASONS) + 1):
            scope = f"{SEASON_LIST} > details.cd-season:nth-of-type({index})"
            if not view.read(scope, (), 1):
                continue  # another kind of details element: not a season
            label = ((view.first(f"{scope} > summary > span") or {}).get("text") or "").strip()
            found = SEASON_LABEL.fullmatch(label)
            season = int(found.group(1)) if found else None
            cards, cut = self._cards(view, f"{scope} > .cd-season__body", MAX_CARDS - len(entries))
            more = more or cut
            for card in cards:
                episode = EPISODE_PREFIX.match(card.episode_label or "")
                if season is None or episode is None or card.variant is None:
                    entries.append(unnumbered(card))  # no number from the page's labels: never guessed
                    continue
                number = int(episode.group(1))
                entries.append(replace(card, season=f"s{season}", season_number=season, season_label=f"Mùa {season}",
                                       episode=f"s{season}:e{number}", episode_number=number,
                                       episode_label=f"Tập {number}", reveal=_season_of(str(card.variant))))
        return entries, more

    def _cards(self, view: PageView, scope: str, room: int) -> tuple[list[RawEntry], bool]:
        """The release cards directly in ``scope``, at most ``room`` of them, each as an entry without episode or
        season (the caller adds them; ``episode_label`` holds the card's name for now). True when cards were
        left unread."""
        room = max(room, 0)
        total = len(view.read(f"{scope} > article", (), room + 1))
        cards: list[RawEntry] = []
        for index in range(1, min(total, room) + 1):
            card = f"{scope} > article.cd-release:nth-of-type({index})"
            if not view.read(card, (), 1):
                continue  # another kind of article (not a release card): never a file
            name = ((view.first(f"{card} .cd-release__name") or {}).get("text") or "").strip()
            meta = [(item.get("text") or "").strip() for item in view.read(f"{card} .cd-release__meta > span")]
            parts = [part for part in meta if part and part != SEPARATOR]
            forms = view.read(f"{card} {FORM}", ("action", "method"), 2)
            # Every control in the form that could submit it: one with its own formaction or formmethod would send
            # another request than the form's, and more than MAX_SUBMITS of them are not all read. A control
            # elsewhere that names this form (its ``form`` attribute) is checked on the clicked control itself,
            # right before the click (download_account_pages._check_submission).
            own = view.read(f"{card} {FORM} {SUBMITS}", ("formaction", "formmethod"), MAX_SUBMITS + 1)
            file_id = None
            if (len(forms) == 1 and (forms[0].get("method") or "").lower() == "post" and len(own) <= MAX_SUBMITS
                    and not any(item.get("formaction") is not None or item.get("formmethod") is not None
                                for item in own)):
                file_id = checked_id(_file_id(view, forms[0].get("action")))
            cards.append(RawEntry(
                "file", variant=file_id, episode_label=name or None, variant_label=f" {SEPARATOR} ".join(parts) or None,
                quality=parts[0] if len(parts) == 4 else None, audio=parts[1] if len(parts) == 4 else None,
                trigger=_trigger(file_id) if file_id else None,
                request=f"https://{_host(view.url)}/download-links/{file_id}/access" if file_id else None,
                request_method="POST" if file_id else None))  # the form is checked as a POST above
        return cards, total > room

    def signed_out(self, view: PageView) -> bool:
        if view.read(f"{AREA} {FORM}", (), 1):
            return False
        here = _host(view.url)
        for link in view.read(f"{AREA} article.cd-release a[href]", ("href",), 50):
            target = link.get("href") or ""
            try:
                path = urlsplit(target).path
            except ValueError:
                continue
            if _host(target) == here and path.rstrip("/") == SIGN_IN_PATH:
                return True
        return False

    def ticket_page(self, view: PageView) -> TicketPage | None:
        """The ticket page's state (module docstring); None when the page shows no single download button."""
        buttons = view.read(TICKET_BUTTON, TICKET_BUTTON_ATTRIBUTES, 2)
        if len(buttons) != 1:
            return None
        button = buttons[0]
        state = (button.get("data-state") or "").strip()
        if state == "blocked":
            return TicketPage(None, None, "challenge")
        if state == "error":
            return TicketPage(None, None, "error")
        locked = ((button.get("aria-disabled") or "").strip().lower() == "true"
                  or "pointer-events-none" in (button.get("class") or "").split())
        if state == "success" and not locked and button.get("href"):
            return TicketPage(None, None, "ready", button["href"])
        return TicketPage(None, None, "waiting")  # the countdown or the page's checks are still running


class NotificationsVerifier:
    """Sign-in evidence of adapter "release-forms" (module docstring)."""

    def signed_in(self, view: LoginView) -> bool:
        url = view.url
        if not view.left_sign_in():
            return False
        fetched = view.fetch(f"https://{_host(url)}{NOTIFICATIONS_PATH}")
        if fetched is None or fetched.status != 200:
            return False
        return notifications_answer(fetched.body)


def notifications_answer(body: str) -> bool:
    """True only for a JSON object of exactly ``notifications`` (a list) and ``unread_count`` (an int >= 0)."""
    try:
        value: Any = json.loads(body)
    except (ValueError, RecursionError):
        return False
    if not isinstance(value, dict) or set(value) != {"notifications", "unread_count"}:
        return False
    count = value["unread_count"]
    return isinstance(value["notifications"], list) and type(count) is int and count >= 0


RELEASE_FORMS_READER = FilmPageReader()
RELEASE_FORMS_VERIFIER = NotificationsVerifier()
