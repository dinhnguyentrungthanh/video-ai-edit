"""The acceptance observation of one ticket (M7; docs/SOURCE_ACCOUNTS_PLAN.md section 9.20): one normal click on
one file's ticket button with the session the user signed in on a test root, then a watch of the ticket page
that answers exactly that click. It never downloads.

Why: the ticket page's contract (its states and countdown, how its host's session is set up, which host serves
the file) shows only with a ticket, and the production reader asked for none before it was seen (its
``reads_tickets`` was False until this observation; it is True since, plan section 9.20). This is a separate path
for exactly one such ticket: it registers no reader, never changes ``RELEASE_FORMS_READER``, and its provider
refuses ``resolve`` (no worker, no transfer, no file probe of its own making).

``observe(root, url, …)``:

- the acceptance probe's guards (download_account_probe): a test root inside the install's temp/, its Control
  Center lock held, the ticket marker written right before the first click (a root with the marker is refused
  unless ``another_ticket``), one hidden run, one deadline, a cancel (Ctrl+C);
- the downloader's own path up to the click (download_account_sources and _pages): the film list read with the
  session, the chosen file (or the film's only file) found, its season opened when folded (a click of its own,
  which also writes the marker), the page read again right before the click (film, episode, file, trigger,
  season, request, method), exactly one control matching, and that control submitting exactly the entry's
  request and method (``_check_submission``: form owner, formaction, formmethod, no inline handler). A non-GET
  navigation, or any request to the entry's request, before the click stops the run before it
  (UNEXPECTED_SUBMIT);
- the click, once (no force, no script, no dialog closed), then the downloader's own provenance: only the page
  that answers this click's own POST through checked redirects is watched (``_TicketFlow.find`` and
  ``_answers_this_click``, checked again at every look). A second request to the entry's request ends the
  watch (EXTRA_SUBMIT); a sign-in page on the way ends it (TICKET_SIGNED_OUT) without marking the session;
- the watch (``ObserveFlow``): the page's own reader state (the source's reader ``ticket_page``) and the
  download button's attributes, recorded with their times each time they change (``events``), and the
  countdown's numbers (``ticks``), until the reader says ready, a challenge, an error, the page leaves or the
  wait is over; the last state is always recorded. Nothing on the page is clicked, changed or hurried; a ready
  state is never made by BiliFlow;
- in the browser every request is counted by method, host role and resource type (the session browser's
  ``request_hook``). The first request of any method but GET writes the ticket marker before it is answered (a
  page that submits by itself may have asked for a ticket; the request is refused when the marker cannot be
  written), and a request whose path has a ``download`` or ``play`` segment is refused (OBSERVE_REFUSED). That
  list of names is a tripwire, not the guard: the file hosts are not among the browser's hosts at all, so the
  session never reaches a file server, and the session browser aborts media requests anyway. When the watch
  cannot keep count any more (too many different URLs) the run ends (REQUEST_LOG_FULL) instead of counting
  short;
- the link of a ready page stays in memory only. Its bare host is checked (https, default port, no user, not a
  portal, ticket or page-script host) and, with ``save_host``, written alone into the root's Git-ignored source
  config as the file host (when that role is empty or holds exactly it); the config is read back and must have
  no problem, or the file goes back as it was;
- with ``probe_same_ticket`` (the host saved): that same link, never a new ticket, is probed as the provider
  would (``resolve_file`` through the downloader's cookie-free SafeHttp, no Referer) behind the probe's
  ``BudgetHttp``: at most ``budget`` bytes (1 MiB at most) of body for all its responses, each closed as soon as
  enough was read even when the server ignores Range, inside the observation's deadline.

The report, also written to ``state/account-probe/observation-<time>.json`` of the root, holds codes, counts,
times, states, path shapes (host roles, the structural words of ``PATH_WORDS``, ``{n}`` for numbers and
``{tok}`` for anything else) and the facts of page texts (``text_facts``: length, small numbers, words of a
fixed vocabulary, whether an IP address was shown): never a URL, a host name, a token, a cookie, a form value,
an address, a title, a file name or a text of the page. It is written even when something after the ready state
fails (``after_ready_error``).
"""
from __future__ import annotations

import argparse
import functools
import json
import math
import os
import re
import shutil
import signal
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urldefrag, urlsplit

from biliflow.control_center import SingleInstanceLock
from biliflow.download_account_config import ACCOUNT_CONFIG, SourceAccount, read_account_config
from biliflow.download_account_edge import BrowserFailed
from biliflow.download_account_listing import FileSelection, TicketPage, film_choices
from biliflow.download_account_pages import (
    TICKET_MISS_SECONDS,
    PageView,
    SignedOut,
    SourcePageReader,
    _file_link,
    _TicketFlow,
)
from biliflow.download_account_probe import (
    MARKER,
    MAX_PROBE_SECONDS,
    PROBE_BUDGET_BYTES,
    WORK,
    BudgetHttp,
    ProbeRefused,
    _file_facts,
    _listing_counts,
    _mark,
    _selection,
    checked_test_root,
)
from biliflow.download_account_release_forms import RELEASE_FORMS_READER
from biliflow.download_account_sources import AccountSourceProvider, SourceNeedsEpisodes, _Retry
from biliflow.download_accounts import AccountManager
from biliflow.download_http import Cancelled, HttpError, SafeHttp
from biliflow.download_links import LinkRejected, check_link
from biliflow.download_media_file import PlaylistLink, resolve_file
from biliflow.download_page_sources import SourceNeedsChoice
from biliflow.download_provider_config import HostList
from biliflow.download_runner import ProcessControl
from biliflow.download_source_types import (
    ResolveContext,
    ResolvedSource,
    SourceDeclined,
    SourceError,
    SourceLoginRequired,
)

OBSERVE_SECONDS = 480.0  # the whole observation: the hidden run (RUN_SECONDS and its grace), the probe after it
RUN_SECONDS = 300.0  # the hidden run: launch, list, click and the ticket page's wait
TICKET_SECONDS = 210.0  # the ticket page's own wait after the click (its countdown and checks)
REPORTS = Path("state") / "account-probe"
REFUSED_SEGMENTS = frozenset({"download", "play"})  # a file or player path: never requested by the browser here
ENDED_STATES = ("challenge", "error", "expired", "login", "gone")  # reader states that end the watch
MAX_EVENTS = 200  # recorded changes of the ticket page's state (the last one is always recorded)
MAX_TICKS = 600  # recorded changes of its countdown
MAX_URLS = 20000  # different URLs the watch counts (in memory only); beyond that the run ends
BUTTON = "a#downloadBtn"
BUTTON_ATTRIBUTES = ("href", "data-state", "aria-disabled", "class", "disabled", "role", "target", "download",
                     "rel", "onclick", "data-countdown", "data-seconds", "data-remaining")
COUNTERS = '[id*="countdown" i], [class*="countdown" i], [data-countdown], [data-seconds], [data-remaining]'
NOTE_SCOPE = "p, li, small, span, div, strong, em"
NOTE_WORDS = re.compile(r"hết hạn|\bIP\b|địa chỉ mạng|tiếp tục tải|tải tiếp", re.IGNORECASE)
# The words of a path kept in a shape; any other segment is {tok} (a name of the film could be a plain word).
PATH_WORDS = frozenset({"access", "ajax", "api", "d", "dl", "download", "download-links", "embed", "f", "file",
                        "files", "get", "login", "logout", "notifications", "phim", "play", "player", "stream", "v",
                        "watch", "x"})
# The words a page text may be reported with (``text_facts``); nothing else of a text is kept.
VOCABULARY = ("tải", "xuống", "ngay", "tiếp", "tải lại", "chờ", "vui lòng", "giây", "phút", "giờ", "ngày",
              "hết hạn", "ip", "địa chỉ", "mạng", "resume", "lỗi", "chặn", "quảng cáo", "thử lại", "sẵn sàng",
              "đăng nhập", "link", "liên kết", "xác minh", "trình duyệt")
_IPV4 = re.compile(r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?!\w|\.\d)")  # a sentence's full stop may follow
_IPV6 = re.compile(r"(?<![\w:])(?:[0-9a-f]{1,4}:){4,7}[0-9a-f]{1,4}(?![\w:])|[0-9a-f]{0,4}::[0-9a-f:]*",
                   re.IGNORECASE)
_NUMBER = re.compile(r"(?<![\w./@-])\d{1,3}(?![\w./@-])")  # a small number, not part of a word or longer number
_VOCABULARY = [(word, re.compile(rf"(?<!\w){re.escape(word)}(?!\w)")) for word in VOCABULARY]


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def shape(url: str | None, roles: Mapping[str, str]) -> str | None:
    """A URL without anything that identifies it: ``scheme:role:/word/{n}/{tok}`` (and ``?{q}`` when it has a
    query). The host becomes its role (``other`` outside the source); a segment is kept only when it is one of
    ``PATH_WORDS`` (lower-cased), digits become ``{n}`` and anything else ``{tok}``."""
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return "bad"
    segments = []
    for segment in parts.path.split("/"):
        if not segment:
            continue
        if segment.isdigit():
            segments.append("{n}")
        elif segment.lower() in PATH_WORDS:
            segments.append(segment.lower())
        else:
            segments.append("{tok}")
    query = "?{q}" if parts.query else ""
    return f"{parts.scheme}:{roles.get((parts.hostname or '').lower(), 'other')}:/" + "/".join(segments) + query


def _state_word(value: Any) -> bool:
    """A plain state word (``loading``, ``success``…), safe to report as it is."""
    return isinstance(value, str) and re.fullmatch(r"[a-z_-]{1,24}", value) is not None


def text_facts(text: str | None) -> dict[str, Any] | None:
    """What a page text says, without the text: its length, whether it showed an IP address, its small numbers
    (at most three digits, not part of a word, an address or a longer number) and the words of ``VOCABULARY`` it
    holds. Nothing else of it (a host, an address, an e-mail, a name, a title) is kept."""
    if not text:
        return None
    flat = " ".join(text.split())
    shown_ip = bool(_IPV4.search(flat) or _IPV6.search(flat))
    bare = _IPV6.sub(" ", _IPV4.sub(" ", flat))
    lowered = flat.lower()
    return {"length": len(flat), "ip_address": shown_ip, "numbers": _NUMBER.findall(bare)[:6],
            "words": [word for word, pattern in _VOCABULARY if pattern.search(lowered)]}


class RequestWatch:
    """The observation's request hook (download_account_browser ``request_hook``): every request the session
    browser is about to answer, counted by method, host role and resource type; a path with a ``download`` or
    ``play`` segment refused. ``on_submit`` is called once, before the first request of any method but GET is
    answered (the ticket marker); if it fails, that request and every later one of a method but GET is refused.
    The URLs themselves are kept in memory only, counted before and after the click, to count those of the
    entry's request; past ``MAX_URLS`` different URLs ``dropped`` is set and the counts are no longer whole."""

    def __init__(self, roles: Mapping[str, str], on_submit: Callable[[], None] | None = None):
        self.roles = dict(roles)
        self.on_submit = on_submit
        self._lock = threading.Lock()
        self.clicked = False
        self.submitted = False  # a request of a method but GET was seen (a ticket may have been asked for)
        self.mark_failed = False
        self.urls: dict[tuple[str, bool], int] = {}  # (URL without fragment, after the click) -> requests
        self.non_get = {False: 0, True: 0}  # requests of a method but GET, before and after the click
        self.dropped = False
        self.counts: dict[str, int] = {}
        self.refused = 0

    def __call__(self, method: str, url: str, kind: str) -> str | None:
        try:
            path = urlsplit(url).path
        except ValueError:
            path = ""
        refuse = any(part.lower() in REFUSED_SEGMENTS for part in path.split("/"))
        with self._lock:
            if method != "GET" and not self.submitted:
                self.submitted = True
                try:
                    if self.on_submit is not None:
                        self.on_submit()
                except Exception:  # noqa: BLE001 - no marker, no such request
                    self.mark_failed = True
            code = "OBSERVE_MARK_FAILED" if method != "GET" and self.mark_failed else (
                "OBSERVE_REFUSED" if refuse else None)
            key = f"{method} {self.roles.get(_host(url), 'other')} {kind}" + (" refused" if code else "")
            self.counts[key] = self.counts.get(key, 0) + 1
            seen = (urldefrag(url)[0], self.clicked)
            if seen in self.urls or len(self.urls) < MAX_URLS:
                self.urls[seen] = self.urls.get(seen, 0) + 1
            else:
                self.dropped = True
            if method != "GET":
                self.non_get[self.clicked] += 1
            if code is not None:
                self.refused += 1
        return code

    def click(self) -> None:
        with self._lock:
            self.clicked = True

    def asked(self, request: str | None, *, after: bool) -> int:
        """Requests to ``request`` (any method or type) before or after the click."""
        if request is None:
            return 0
        with self._lock:
            return self.urls.get((request, after), 0)

    def posts(self, *, after: bool) -> int:
        """Requests of any method but GET before or after the click."""
        with self._lock:
            return self.non_get[after]

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {"counts": dict(sorted(self.counts.items())), "refused": self.refused,
                    "kept_whole": not self.dropped, "submitted": self.submitted, "mark_failed": self.mark_failed}


@dataclass
class TicketObservation:
    """What the watch saw: its outcome, the redacted facts and, when the page said ready, the link (private)."""
    outcome: str
    facts: dict[str, Any] = field(default_factory=dict)
    link: str | None = field(default=None, repr=False)


class ObservationEnded(SourceError):
    """The observation ended before or during the watch: ``observation`` holds what it saw by then."""

    def __init__(self, observation: TicketObservation):
        super().__init__(observation.outcome, f"Quan sát vé dừng: {observation.outcome}.")
        self.observation = observation


class ObservingReader:
    """The source's reader for the observation only: its film page and signed-out evidence as they are, ticket
    asking on, and a ready ticket page shown as still waiting, so no flow can ever take a link through it (the
    watch reads the source's own reader itself)."""

    def __init__(self, base: SourcePageReader):
        self.base = base
        self.reads_tickets = True
        self.shows_ticket_ids = getattr(base, "shows_ticket_ids", True)

    def film_page(self, view: PageView) -> Any:
        return self.base.film_page(view)

    def signed_out(self, view: PageView) -> bool:
        return self.base.signed_out(view)

    def ticket_page(self, view: PageView) -> TicketPage | None:
        shown = self.base.ticket_page(view)
        if shown is not None and shown.state == "ready":
            return TicketPage(shown.episode, shown.file, "waiting")
        return shown


class ObserveFlow(_TicketFlow):
    """``get_ticket``'s flow for the observation: the downloader's own click provenance, then a watch of the
    ticket page instead of taking its link (module docstring). Made right before the click."""

    def __init__(self, *args: Any, watch: RequestWatch, base: SourcePageReader, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.watch, self.base = watch, base
        self.roles = watch.roles
        self.events: list[dict[str, Any]] = []
        self.ticks: list[dict[str, Any]] = []
        self._last_tick: dict[str, Any] | None = None
        self.found_after: float | None = None
        self.link: str | None = None
        self.clicked_at = time.monotonic()
        self.cookies_before = self._cookie_roles()
        if watch.dropped:
            raise ObservationEnded(self._observation("REQUEST_LOG_FULL", clicked=False))
        early = [step for step in self.browser.navigations() if step.method != "GET"]
        if early or watch.asked(self.request, after=False):
            raise ObservationEnded(self._observation("UNEXPECTED_SUBMIT", clicked=False))
        # get_ticket clicks right after making the flow: from here every request counts as the click's (a
        # season's opening click before it does not).
        watch.click()
        self.clicked_at = time.monotonic()

    # What the flow saw ----------------------------------------------------------------------------------

    def _cookie_roles(self) -> dict[str, int]:
        """How many cookies of the session apply to each host role (never their names or values)."""
        try:
            cookies = self.browser.storage_state().get("cookies", [])
        except Exception:  # noqa: BLE001 - a fact for the report only
            return {}
        counts: dict[str, int] = {}
        for role in ("portal", "tickets"):
            hosts = [host for host, owner in self.roles.items() if owner == role]
            for cookie in cookies:
                domain = str(cookie.get("domain") or "").lower()
                bare = domain.lstrip(".")
                if any(host == bare or (domain.startswith(".") and host.endswith("." + bare)) for host in hosts):
                    counts[role] = counts.get(role, 0) + 1
        return counts

    def _frame_name(self, frame: Any, ticket: Any) -> str:
        if frame is None:
            return "new-page"
        if frame is self.browser.main_frame(self.page):
            return "film"
        if ticket is not None and frame is self.browser.main_frame(ticket):
            return "ticket"
        return "other"

    def _chain(self, ticket: Any) -> list[dict[str, Any]]:
        steps = self.browser.navigations(self.mark)
        return [{"n": step.number - self.mark, "method": step.method, "to": shape(step.url, self.roles),
                 "frame": self._frame_name(step.frame, ticket), "target": shape(step.target, self.roles),
                 "status": step.status, "refused": step.refused} for step in steps[:60]]

    def _snapshot(self, view: PageView, real: TicketPage | None) -> dict[str, Any]:
        buttons = view.read(BUTTON, BUTTON_ATTRIBUTES, 2)
        button = buttons[0] if len(buttons) == 1 else {}
        counters = view.read(COUNTERS, (), 10)
        counted = text_facts(" ".join(item.get("text") or "" for item in counters))
        classes = (button.get("class") or "").split()
        state = button.get("data-state")
        aria = button.get("aria-disabled")
        return {"reader": real.state if real is not None else None, "buttons": len(buttons),
                # an attribute's value is reported only when it is a plain state word
                "data_state": state if state is None or _state_word(state) else "other",
                "aria_disabled": aria if aria in (None, "true", "false") else "other",
                "locked_class": "pointer-events-none" in classes,
                "disabled": button.get("disabled") is not None, "href": shape(button.get("href"), self.roles),
                "other_attributes": sorted(name for name in BUTTON_ATTRIBUTES[5:] if button.get(name) is not None),
                "counters": len(counters), "countdown": counted["numbers"] if counted else [],
                "button_text": text_facts(button.get("text"))}

    def _page_facts(self, ticket: Any) -> dict[str, Any]:
        """The ticket page's notes on expiry and IP, and its signs of a session (counts only)."""
        if ticket is None or ticket not in self.browser.pages():
            return {}
        view = PageView(self.browser, ticket)
        try:
            texts = [item.get("text") or "" for item in view.read(NOTE_SCOPE, (), 400)]
            links = [item.get("href") or "" for item in view.read("a[href]", ("href",), 300)]
            forms = [str(item.get("method") or "get").lower() for item in view.read("form", ("method",), 20)]
            passwords = len(view.read('input[type="password" i]', (), 5))
        except BrowserFailed:
            return {"unreadable": True}
        notes: list[dict[str, Any]] = []
        for text in texts:
            for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
                facts = text_facts(sentence) if NOTE_WORDS.search(sentence) and len(sentence) <= 400 else None
                if facts and facts not in notes:
                    notes.append(facts)
        paths = [urlsplit(link).path.lower() for link in links if link.startswith("http")]
        forms = [method if method in ("get", "post", "dialog") else "other" for method in forms]
        return {"notes": notes[:6], "forms": forms, "password_inputs": passwords,
                "sign_in_links": sum(1 for path in paths if re.search(r"log-?in|dang-?nhap|sign-?in", path)),
                "sign_out_links": sum(1 for path in paths if re.search(r"log-?out|dang-?xuat|sign-?out", path)),
                "url": shape(self.browser.page_url(ticket), self.roles)}

    def _observation(self, outcome: str, ticket: Any = None, *, clicked: bool = True) -> TicketObservation:
        facts: dict[str, Any] = {"clicked": clicked, "events": self.events, "ticks": self.ticks}
        if clicked:
            facts.update(
                seconds_to_ticket_page=self.found_after,
                seconds_watched=round(time.monotonic() - self.clicked_at, 1),
                chain=self._chain(ticket), navigations_whole=not self.browser.navigations_dropped,
                requests_to_entry={"before_click": self.watch.asked(self.request, after=False),
                                   "after_click": self.watch.asked(self.request, after=True)},
                non_get_requests={"before_click": self.watch.posts(after=False),
                                  "after_click": self.watch.posts(after=True)},
                cookies={"before_click": self.cookies_before, "after": self._cookie_roles()},
                page=self._page_facts(ticket))
        else:
            facts.update(requests_to_entry={"before_click": self.watch.asked(self.request, after=False)},
                         non_get_navigations=[step.method for step in self.browser.navigations()
                                              if step.method != "GET"][:10])
        return TicketObservation(outcome, facts, self.link if outcome == "READY" else None)

    # The flow -------------------------------------------------------------------------------------------

    def find(self) -> Any:
        try:
            ticket = super().find()
        except ObservationEnded:
            raise
        except SignedOut:
            raise ObservationEnded(self._observation("TICKET_SIGNED_OUT")) from None
        except SourceError as error:
            raise ObservationEnded(self._observation(error.code)) from None
        self.found_after = round(time.monotonic() - self.clicked_at, 2)
        return ticket

    def _ticket_of(self, candidate: Any) -> TicketPage | None:
        if self.watch.dropped:
            raise ObservationEnded(self._observation("REQUEST_LOG_FULL", candidate))
        if self.watch.asked(self.request, after=True) > 1:
            raise ObservationEnded(self._observation("EXTRA_SUBMIT", candidate))
        return super()._ticket_of(candidate)

    def _record(self, snapshot: dict[str, Any], *, final: bool = False) -> None:
        """A change of the page's state into ``events`` (the ``final`` one even past MAX_EVENTS), a change of its
        countdown or button text into ``ticks``."""
        now = round(time.monotonic() - self.clicked_at, 2)
        tick = {name: snapshot[name] for name in ("countdown", "button_text")}
        if tick != self._last_tick and len(self.ticks) < MAX_TICKS:
            self.ticks.append({"t": now, **tick})
            self._last_tick = tick
        state = {name: value for name, value in snapshot.items() if name not in tick}
        last = self.events[-1]["page"] if self.events else None
        if state != last and (len(self.events) < MAX_EVENTS or final):
            self.events.append({"t": now, "page": state})

    def wait_ready(self, ticket: Any) -> TicketObservation:  # type: ignore[override]
        missing_since: float | None = None
        while self._time_left():
            try:
                shown = self._ticket_of(ticket)
            except BrowserFailed:
                shown = None
            except SignedOut:
                return self._observation("TICKET_SIGNED_OUT", ticket)
            if ticket not in self.browser.pages():
                return self._observation("TICKET_TAB_CLOSED")
            if shown is None:
                missing_since = missing_since or time.monotonic()
                if time.monotonic() - missing_since > TICKET_MISS_SECONDS:
                    return self._observation("TICKET_LEFT", ticket)
                self._pump(ticket)
                continue
            missing_since = None
            view = PageView(self.browser, ticket)
            try:
                real = self.base.ticket_page(view)
                state = real.state if real is not None else None
                self._record(self._snapshot(view, real), final=state in ("ready", *ENDED_STATES))
            except BrowserFailed:
                self._pump(ticket)
                continue
            if state == "ready":
                self.link = real.link if real is not None else None
                return self._observation("READY", ticket)
            if state in ENDED_STATES:
                return self._observation(f"TICKET_{state.upper()}", ticket)
            self._pump(ticket)
        return self._observation("TICKET_TIMEOUT", ticket)


class ObservingProvider(AccountSourceProvider):
    """The account provider of the observation: the downloader's own list and click path with ``ObserveFlow``;
    one hidden run, one click, never a file. ``resolve`` is refused."""

    def __init__(self, source: SourceAccount, manager: AccountManager, *, watch: RequestWatch,
                 base_reader: SourcePageReader = RELEASE_FORMS_READER, **options: Any):
        super().__init__(source, manager, reader=ObservingReader(base_reader), ticket_attempts=1, **options)
        self.ticket_flow = functools.partial(ObserveFlow, watch=watch, base=base_reader)

    def resolve(self, url: str, ctx: ResolveContext) -> ResolvedSource:
        raise SourceError("OBSERVE_ONLY", "Bộ quan sát vé không tải hay probe file qua provider.")

    def observe_once(self, url: str, wanted: FileSelection | None, control: Any) -> TicketObservation:
        self._check_ready(url)
        try:
            found = self._session_run(url, wanted, control, ticket=True)
        except ObservationEnded as ended:
            return ended.observation
        except _Retry as retry:
            return TicketObservation(retry.code)
        if found.ticket is None:
            if found.listing.kind == "series":
                raise SourceNeedsEpisodes(found.listing.public())
            raise SourceNeedsChoice(film_choices(found.listing))
        if not isinstance(found.ticket, TicketObservation):  # never: this provider's flow is ObserveFlow
            raise SourceError("OBSERVE_ONLY", "Lượt quan sát trả về một vé thường.")
        return found.ticket


# The link of a ready ticket --------------------------------------------------------------------------------


def link_facts(link: str | None, source: SourceAccount) -> tuple[str | None, dict[str, Any]]:
    """(the bare file host, or None, and the redacted facts of the link). The host is given only for an https link
    on the default port, without a user, on a host that is none of the source's portal or ticket hosts and not
    its page script's host."""
    if not link:
        return None, {"present": False}
    try:
        url, host, port = check_link(link)
    except LinkRejected as rejected:
        return None, {"present": True, "rejected": rejected.code}
    parts = urlsplit(url)
    script = _host(source.page_script or "")
    others = set(source.hosts["portal"]) | set(source.hosts["tickets"]) | ({script} if script else set())
    facts = {"present": True, "https": parts.scheme == "https", "default_port": port == 443,
             "user": bool(parts.username or parts.password), "query": bool(parts.query),
             "fragment": bool(urlsplit(link).fragment), "shape": shape(url, {}), "length": len(url),
             "host_is_source_role": host in others, "host_is_files_role": host in source.hosts["files"]}
    usable = facts["https"] and facts["default_port"] and not facts["user"] and not facts["host_is_source_role"]
    return (host if usable else None), facts


def save_file_host(root: Path, source_id: str, host: str) -> dict[str, Any]:
    """Write ``host`` alone as the file host of ``source_id`` in the root's local config (when that role is
    empty or holds exactly it); the config is read back and must have no problem, else the file is restored."""
    path = root / ACCOUNT_CONFIG
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
        hosts = data["sources"][source_id]["hosts"]
    except (OSError, ValueError, KeyError, TypeError):
        return {"saved": False, "reason": "CONFIG_UNREADABLE"}
    files = list(hosts.get("files") or [])
    if files == [host]:
        return {"saved": True, "already": True}
    if files:
        return {"saved": False, "reason": "FILES_ROLE_HOLDS_ANOTHER_HOST"}
    hosts["files"] = [host]
    if not _replace_bytes(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")):
        return {"saved": False, "reason": "CONFIG_WRITE_FAILED"}
    config = read_account_config(root)
    saved = config.sources.get(source_id)
    if config.problems or saved is None or tuple(saved.hosts["files"]) != (host,):
        restored = _replace_bytes(path, raw)
        return {"saved": False, "reason": "CONFIG_INVALID_RESTORED" if restored else "CONFIG_RESTORE_FAILED",
                "problems": len(config.problems)}
    return {"saved": True, "already": False}


def _replace_bytes(path: Path, data: bytes) -> bool:
    """``path`` replaced by ``data`` at once (a temporary file beside it, then ``os.replace``); False, with the
    file as it was and no temporary file left, when that cannot be done."""
    temporary = path.with_name(path.name + ".observe.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def probe_link(root: Path, source: SourceAccount, link: str, client: BudgetHttp, ffprobe: Path | None,
               control: ProcessControl) -> dict[str, Any]:
    """The same link, probed as the provider's own probe does (download_account_sources ``_probe``), through
    ``client`` (cookie-free, no Referer, one byte budget). Never a transfer."""
    (root / WORK).mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="observe-", dir=root / WORK))
    context = ResolveContext(http=client, control=control, task_dir=work, ffprobe=ffprobe)  # type: ignore[arg-type]
    try:
        url = _file_link(source, link)
        resolved = resolve_file(url, context, provider=source.id, label=source.label)
        return {"outcome": "PROBED", "file": _file_facts(resolved)}
    except PlaylistLink:
        return {"outcome": "UNSUPPORTED_FORMAT"}
    except SourceDeclined:
        return {"outcome": "FILE_NOT_VIDEO"}
    except Cancelled:
        return {"outcome": "CANCELLED"}
    except HttpError as error:
        return {"outcome": error.code, "status": error.status}
    except SourceError as error:
        return {"outcome": error.code}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# The observation --------------------------------------------------------------------------------------------


def observe(root: Path, url: str, *, chosen: FileSelection | None = None, seconds: float = OBSERVE_SECONDS,
            save_host: bool = False, probe_same_ticket: bool = False, budget: int = PROBE_BUDGET_BYTES,
            another_ticket: bool = False, ffprobe: Path | None = None, http: SafeHttp | None = None,
            manager: AccountManager | None = None, base_reader: SourcePageReader | None = None,
            run_options: Mapping[str, Any] | None = None, control: ProcessControl | None = None) -> dict[str, Any]:
    """One observed ticket of ``url`` on the test ``root`` (module docstring); returns the report. ``http``,
    ``manager``, ``base_reader`` and ``run_options`` are the tests' injection; the command line passes none."""
    if not (math.isfinite(seconds) and 0 < seconds <= MAX_PROBE_SECONDS):
        raise ValueError(f"seconds must be more than 0 and at most {MAX_PROBE_SECONDS:.0f}")
    if probe_same_ticket and not save_host:
        raise ValueError("The same ticket is probed only once its file host is saved")
    if not 0 < budget <= PROBE_BUDGET_BYTES:
        raise ValueError(f"budget must be 1 to {PROBE_BUDGET_BYTES} bytes")
    root = checked_test_root(root)
    if manager is not None and os.path.normcase(str(manager.root.resolve())) != os.path.normcase(str(root)):
        raise ValueError("The account manager must be the test root's")
    try:
        lock = SingleInstanceLock(root / "state" / "control-center.lock")
    except RuntimeError:
        raise ProbeRefused("CONTROL_CENTER_RUNNING", "Control Center của root thử đang chạy; dừng nó trước khi quan "
                           "sát vé.") from None
    own_manager = None
    try:
        if (root / MARKER).exists() and not another_ticket:
            raise ProbeRefused("TICKET_ALREADY_ASKED", "Root thử này đã có một lượt đã bấm lấy vé; không lấy thêm vé "
                               "khi chưa được yêu cầu rõ.")
        config = manager.config if manager is not None else read_account_config(root)
        if config.problems:
            raise ProbeRefused("CONFIG_INVALID", f"Config nguồn của root thử có {len(config.problems)} lỗi.")
        source = next((item for item in config.sources.values() if HostList(item.all_hosts).matches(url)), None)
        if source is None:
            raise ProbeRefused("NOT_A_SOURCE", "Link không thuộc nguồn nào trong config của root thử.")
        if manager is None:
            manager = own_manager = AccountManager(root, config)
        return _observe(root, source, manager, url, chosen, seconds, save_host, probe_same_ticket, budget, ffprobe,
                        http, base_reader or RELEASE_FORMS_READER, run_options, control or ProcessControl())
    finally:
        if own_manager is not None:
            own_manager.close()
        lock.close()


def _roles(source: SourceAccount) -> dict[str, str]:
    roles = {host: role for role, hosts in source.hosts.items() for host in hosts}
    if source.page_script:
        roles.setdefault(_host(source.page_script), "script")
    return roles


def _observe(root: Path, source: SourceAccount, manager: AccountManager, url: str, chosen: FileSelection | None,
             seconds: float, save_host: bool, probe_same_ticket: bool, budget: int, ffprobe: Path | None,
             http: SafeHttp | None, base_reader: SourcePageReader, run_options: Mapping[str, Any] | None,
             control: ProcessControl) -> dict[str, Any]:
    roles = _roles(source)
    watch = RequestWatch(roles, on_submit=lambda: _mark(root, "SUBMITTED"))
    clicks: list[str] = []  # the ticket click (made right after ObserveFlow marked the watch clicked)
    reveals: list[str] = []  # a folded season's opening click before it

    def before_click() -> None:  # synchronous, before the browser clicks: a ticket may be asked for from here
        if not clicks and not reveals:
            _mark(root, "CLICKED")
        (clicks if watch.clicked else reveals).append("click")

    options = dict(run_options or {})
    options.setdefault("run_seconds", RUN_SECONDS)
    options["browser_options"] = {**dict(options.get("browser_options") or {}), "before_click": before_click,
                                  "request_hook": watch}
    provider = ObservingProvider(source, manager, watch=watch, base_reader=base_reader, run_options=options,
                                 ticket_seconds=TICKET_SECONDS)
    if chosen is not None:
        chosen = FileSelection(source.id, chosen.film, chosen.episode, chosen.variant)
    timer = threading.Timer(seconds, control.request, ("cancel",))
    timer.daemon = True
    started = time.monotonic()
    timer.start()
    report: dict[str, Any] = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        observation = _watch(provider, url, chosen, control, report)
        if observation is not None and observation.outcome == "READY":
            try:
                _after_ready(root, source, observation, report, save_host, probe_same_ticket, budget, ffprobe,
                             http, roles, control)
            except Exception as error:  # noqa: BLE001 - the one ticket's observation is still reported
                report["after_ready_error"] = type(error).__name__
    finally:
        timer.cancel()
    if report["outcome"] == "CANCELLED" and time.monotonic() - started >= seconds:
        report["outcome"] = "DEADLINE"
    report.update(seconds=round(time.monotonic() - started, 1), clicks=len(clicks), reveal_clicks=len(reveals),
                  requests=watch.summary())
    if clicks or reveals or watch.submitted:
        try:
            _mark(root, report["outcome"])
        except OSError:
            report["marker_error"] = True
    report["ticket_marker"] = (root / MARKER).exists()
    report["report_file"] = _write(root, report)
    return report


def _watch(provider: ObservingProvider, url: str, chosen: FileSelection | None, control: ProcessControl,
           report: dict[str, Any]) -> TicketObservation | None:
    try:
        observation = provider.observe_once(url, chosen, control)
    except SourceNeedsEpisodes as needs:
        report.update(outcome="NEEDS_EPISODES", listing=_listing_counts(needs.listing))
        return None
    except SourceNeedsChoice as needs:
        report.update(outcome="NEEDS_CHOICE", choices=len(needs.choices))
        return None
    except SourceLoginRequired as needs:
        report.update(outcome=needs.code, reason=needs.reason)
        return None
    except Cancelled:
        report["outcome"] = "CANCELLED"
        return None
    except HttpError as error:
        report.update(outcome=error.code, status=error.status)
        return None
    except SourceError as error:
        report["outcome"] = error.code
        return None
    except Exception as error:  # noqa: BLE001 - the report (and the marker of a click) is still written
        report.update(outcome="OBSERVE_ERROR", error=type(error).__name__)
        return None
    report["outcome"] = observation.outcome
    report["observation"] = observation.facts
    return observation


def _after_ready(root: Path, source: SourceAccount, observation: TicketObservation, report: dict[str, Any],
                 save_host: bool, probe_same_ticket: bool, budget: int, ffprobe: Path | None,
                 http: SafeHttp | None, roles: Mapping[str, str], control: ProcessControl) -> None:
    ready_at = time.monotonic()
    host, facts = link_facts(observation.link, source)
    report["link"] = facts
    if host is None or not save_host:
        report["host_saved"] = {"saved": False, "reason": "NOT_ASKED" if host is not None else "LINK_NOT_USABLE"}
        return
    report["host_saved"] = save_file_host(root, source.id, host)
    if not probe_same_ticket or not report["host_saved"]["saved"]:
        return
    config = read_account_config(root)
    updated = config.sources.get(source.id)
    if updated is None or config.problems:
        report["probe"] = {"outcome": "CONFIG_INVALID"}
        return
    file_roles = {**roles, **{name: "files" for name in updated.hosts["files"]}}
    client = BudgetHttp(http or SafeHttp(), budget, lambda link: file_roles.get(_host(link), "other"))
    report["probe"] = probe_link(root, updated, observation.link or "", client, ffprobe, control)
    report["probe"].update(seconds_after_ready=round(time.monotonic() - ready_at, 1),
                           budget={"limit": client.budget, "used": client.used},
                           responses=[asdict(item) for item in client.responses])


def _write(root: Path, report: Mapping[str, Any]) -> str:
    folder = root / REPORTS
    folder.mkdir(parents=True, exist_ok=True)
    name = f"observation-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.json"
    (folder / name).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return (REPORTS / name).as_posix()


def _seconds(value: str) -> float:
    seconds = float(value)
    if not (math.isfinite(seconds) and 0 < seconds <= MAX_PROBE_SECONDS):
        raise argparse.ArgumentTypeError(f"--seconds is more than 0 and at most {MAX_PROBE_SECONDS:.0f}")
    return seconds


def _url_of(args: argparse.Namespace, parser: argparse.ArgumentParser) -> str:
    """The film page: ``--url``, or the first line of ``--url-file`` (so the link is not on a command line)."""
    if (args.url is None) == (args.url_file is None):
        parser.error("give exactly one of --url and --url-file")
    if args.url is not None:
        return str(args.url)
    try:
        lines = args.url_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        parser.error("--url-file cannot be read")
    return lines[0].strip() if lines else ""


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Vietnamese messages through a pipe of another code page
    parser = argparse.ArgumentParser(prog="python -m biliflow.download_account_observe",
                                     description="M7 acceptance observation of one ticket (a test root only).")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--url")
    parser.add_argument("--url-file", type=Path)
    parser.add_argument("--file", type=_selection, help="FILM/EPISODE/VARIANT of the file to ask a ticket for")
    parser.add_argument("--save-host", action="store_true", help="write the ready link's bare host as the file host")
    parser.add_argument("--probe-same-ticket", action="store_true",
                        help="probe that same link (at most --budget bytes) once its host is saved")
    parser.add_argument("--budget", type=int, default=PROBE_BUDGET_BYTES)
    parser.add_argument("--seconds", type=_seconds, default=OBSERVE_SECONDS)
    parser.add_argument("--ffprobe", type=Path, default=os.environ.get("BILIFLOW_FFPROBE") or None)
    parser.add_argument("--another-ticket", action="store_true")
    args = parser.parse_args(argv)
    if not 0 < args.budget <= PROBE_BUDGET_BYTES:
        parser.error(f"--budget must be 1 to {PROBE_BUDGET_BYTES}")
    if args.probe_same_ticket and not args.save_host:
        parser.error("--probe-same-ticket needs --save-host")
    url = _url_of(args, parser)
    control = ProcessControl()
    previous = signal.signal(signal.SIGINT, lambda *_: control.request("cancel"))  # the run ends as a cancel
    try:
        report = observe(args.root, url, chosen=args.file, seconds=args.seconds, save_host=args.save_host,
                         probe_same_ticket=args.probe_same_ticket, budget=args.budget,
                         another_ticket=args.another_ticket, ffprobe=args.ffprobe, control=control)
    except ProbeRefused as refused:
        print(json.dumps({"refused": refused.code, "message": refused.message}, ensure_ascii=False))
        return 2
    finally:
        signal.signal(signal.SIGINT, previous)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["outcome"] == "READY" else 1


if __name__ == "__main__":
    sys.exit(main())
