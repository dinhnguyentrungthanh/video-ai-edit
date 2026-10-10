"""Reading a film's pages and getting one file's ticket in a hidden session browser (M3, adapter "ticket-files").

The structure of a source's pages belongs to its ``SourcePageReader``: which part of a page is the download
area, the ids of the film, seasons, episodes and files, the page links of the list, what a ticket page shows
and what proves the session is signed out. A reader only reads what a page shows, through ``PageView``
(the page's URL and bounded reads of elements' attributes and text): it cannot click, run a script, add a
route or send a request. The only action it may ask for is declarative: an entry's ``reveal``, the
``details`` element its trigger sits in, which ``get_ticket`` opens itself (see below). ``PAGE_READERS``
holds the readers of real sources by adapter id (read-only): "release-forms"
(download_account_release_forms), written from a real source's pages checked with the user in M7. The
fixtures of the tests are never taken for a real site. A source without a reader is recognized but never
read. A reader whose ``reads_tickets`` is False (its ticket page was not checked yet) reads lists only:
``get_ticket`` refuses before any click, so no ticket of the account is spent on a page nobody can read.

What this module does with a reader, in the session browser of ``download_account_runs``:

- ``read_listing``: opens the pasted page and follows the list's own page links (https, the pasted page's
  own host, never the sign-in page, no fragment), at most ``max_pages`` pages with a short pause between
  them and only while the run has time left; a link seen twice is read once, so a pagination loop ends. A
  page of another film, a page that fails or a limit makes the list incomplete (download_account_listing),
  and so does a page link that is not followed (another host, even another portal host of the source, not
  https, the sign-in page: PAGE_NOT_FOLLOWED): the list never says it is whole while one of its pages was
  left out. A link back to a page already read, or to the same page (a fragment), is not such a link.
  It never asks for a ticket and never fetches a file. Which links of a page are the list's page links is
  the reader's choice (its pagination area only, adverts and other links left out): the session goes with
  every one of them that is followed, and every one that is not makes the list incomplete.
- ``get_ticket``: on the page where the chosen file is listed, clicks that file's own ticket control (as a
  user would), once. A control inside a folded season is first shown by a click on that season's own summary
  (the entry's ``reveal``; an open season is never clicked, it would fold). Then, with or without a season,
  the page is read again right before the click: it must still be the chosen film (its film id) and list
  exactly that file once, with the same control, season, request and method (TICKET_TARGET_CHANGED otherwise,
  nothing clicked); a control that matches no element or several is never clicked. Nothing forces a click,
  runs a script, closes a dialog or posts a form by itself: a page whose control cannot be clicked (a modal
  over it) ends the run with CLICK_FAILED. The click happens only while the run has time left for the
  ticket page's wait and, after it, for reading the state and closing the browser
  (``FINISH_RESERVE_SECONDS``: a run past its deadline gives no ticket), then waits for the ticket page of
  that file: a page on a ticket or portal host that the reader recognizes as the ticket of exactly that
  episode and file id. A source whose ticket page names no file (the reader's ``shows_ticket_ids`` is
  False) gets a ticket only from the page that answers this click's own request (the entry's ``request``,
  a form's action, sent with the entry's ``request_method``): the run's record of navigation requests
  (download_account_browser) must show exactly one request to it since the click, with that method (a GET of
  a form's action is not its POST; the redirects after it are GETs), followed only through the run's own hop
  pages into that page, a popup
  the film page opened, with nothing else navigating it; the first popup, a page on a ticket host or a file
  name is never taken for that file. Other pages that open meanwhile (a ticket of another file) are never
  used and are closed at the end; a page on any other host (an advert) is closed as soon as it shows that
  host, and a blank page is kept until it can be told apart. The wait on the ticket page is the page's own
  (a countdown, a button that appears): nothing shortens or skips it, and a challenge (CAPTCHA, a check) is
  reported, never answered. When the page shows its download button, the button's link is read, not
  followed: the browser never reaches the file servers, and the link must be https on one of the source's
  file hosts.
- Evidence: the source's sign-in page (``login_url``'s host and path, and its query items when it has
  any) or what the reader calls signed out raises ``SignedOut``, on the pasted page and on the ticket page.
  A later page of the list that shows it only makes the list incomplete (PAGE_SIGNED_OUT): the first page
  of the same run was signed in, so it may be a page this account cannot open. A ticket page that says it
  expired raises ``TicketExpired``. A 401/403 page without such evidence, a missing page, a slow page or a
  closed tab are their own errors, never a sign-in.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import parse_qsl, urldefrag, urljoin, urlsplit

from biliflow.download_account_browser import MAX_HOPS, Navigation, SessionBrowser, effective_submission
from biliflow.download_account_config import SourceAccount
from biliflow.download_account_edge import BrowserFailed, _host_of
from biliflow.download_account_listing import TICKET_STATES, FilmPage, ListingBuilder, TicketPage, checked_id
from biliflow.download_account_release_forms import RELEASE_FORMS_READER
from biliflow.download_http import Cancelled, HttpError
from biliflow.download_links import LinkRejected, check_link
from biliflow.download_source_types import SourceError

MAX_LISTING_PAGES = 40
LISTING_RESERVE_SECONDS = 15.0  # of the run's time, kept back from reading pages (to end with what was read)
POLL_SECONDS = 0.25
PAGE_PAUSE_SECONDS = 0.3  # between two pages of the list, so reading a long list is not a burst of requests
MIN_TICKET_SECONDS = 10.0  # the least time left in the run for a ticket click (a ticket is never minted to lapse)
FINISH_RESERVE_SECONDS = 5.0  # of the run's time, left after a ticket for reading the state and closing the browser
TICKET_MISS_SECONDS = 2.0  # how long a ticket page may stop showing its ticket (a re-render) before it failed
REVEAL_SECONDS = 3.0  # how long an opened season may take to show itself open


class SignedOut(Exception):
    """The source showed that the session is not signed in (its sign-in page, an explicit message)."""


class TicketExpired(Exception):
    """The ticket page said the ticket expired before its file could be fetched."""


@dataclass(frozen=True)
class Ticket:
    """The chosen file's download link (private: never stored, shown or logged)."""
    link: str = field(repr=False)


class PageView:
    """What a reader sees of one page: its URL and bounded reads of elements (attributes and text)."""

    def __init__(self, browser: SessionBrowser, page: Any):
        self._browser = browser
        self._page = page

    @property
    def url(self) -> str:
        return self._browser.page_url(self._page)

    def read(self, selector: str, attributes: tuple[str, ...] = (), limit: int = 2000) -> list[dict[str, str | None]]:
        return self._browser.read(self._page, selector, attributes, limit)

    def first(self, selector: str, attributes: tuple[str, ...] = ()) -> dict[str, str | None] | None:
        found = self.read(selector, attributes, 1)
        return found[0] if found else None


class SourcePageReader(Protocol):
    def film_page(self, view: PageView) -> FilmPage | None:
        """The film page's list (FilmPage), or None when this is not a film page of the source."""

    def signed_out(self, view: PageView) -> bool:
        """True only on clear evidence that the session is not signed in."""

    def ticket_page(self, view: PageView) -> TicketPage | None:
        """The ticket page's state, or None when this page is not a ticket page (an advert, another page)."""


# Readers of real sources, by adapter id: added only once a source's page structure and signed-in evidence were
# checked on the real source with the user (M7). Read-only: a reader is code, never added at run time.
# "ticket-files" has none (its sources are recognized, never read).
PAGE_READERS: Mapping[str, SourcePageReader] = MappingProxyType({"release-forms": RELEASE_FORMS_READER})


def browser_error(error: BrowserFailed) -> Exception:
    """A failed navigation as the downloader's error: a slow or unreachable page may be tried again later."""
    if error.code in ("TIMEOUT", "NAVIGATION_TIMEOUT", "NETWORK", "DNS_FAILED"):
        return HttpError("NETWORK", error.message, retryable=True)
    return SourceError(error.code, error.message)


def _status_error(status: int, host: str) -> Exception | None:
    """A film page answered with an error status (and no sign-in evidence): its own error, never a sign-in."""
    if status in (401, 403):
        return SourceError("FORBIDDEN", f"{host} từ chối trang (HTTP {status}); không có dấu hiệu cần đăng nhập lại.")
    if status in (404, 410):
        return SourceError("UNAVAILABLE", f"{host} báo trang phim không còn (HTTP {status}).")
    if status == 429 or status >= 500:
        return HttpError("SERVER_BUSY", f"{host} đang bận hoặc lỗi (HTTP {status}).", retryable=True)
    if status >= 400:
        return SourceError("HTTP_ERROR", f"{host} trả về HTTP {status}.")
    return None


def is_sign_in_page(source: SourceAccount, url: str) -> bool:
    """``url`` is the source's sign-in page: its host and path, and every query item of ``login_url`` (a
    sign-in page such as ``/index.php?do=login`` is not every page of ``/index.php``)."""
    login = urlsplit(source.login_url)
    try:
        here = urlsplit(url)
    except ValueError:
        return False
    if (here.hostname or "") != (login.hostname or "") or here.path.rstrip("/") != login.path.rstrip("/"):
        return False
    shown = parse_qsl(here.query, keep_blank_values=True)
    return all(item in shown for item in parse_qsl(login.query, keep_blank_values=True))


def check_signed_in(source: SourceAccount, reader: SourcePageReader, view: PageView) -> None:
    if is_sign_in_page(source, view.url) or reader.signed_out(view) is True:
        raise SignedOut()


def check_running(browser: SessionBrowser) -> None:
    """Cancelled once the run was stopped (the caller's cancel or the run's deadline): its loops end at once
    and download_account_runs tells which of the two it was."""
    if browser.control.requested:
        raise Cancelled()


def _list_link(source: SourceAccount, base: str, link: str, host: str | None = None) -> str | None:
    """A page link of the list, absolute, https, on a portal host (``host`` when given), without its
    fragment and never the sign-in page; None otherwise."""
    try:
        url, found, port = check_link(urldefrag(urljoin(base, link))[0])
    except (LinkRejected, ValueError):
        return None
    if urlsplit(url).scheme != "https" or port != 443 or found not in source.hosts["portal"]:
        return None
    if (host is not None and found != host) or is_sign_in_page(source, url):
        return None
    return url


def read_listing(browser: SessionBrowser, reader: SourcePageReader, source: SourceAccount, url: str, *,
                 max_pages: int = MAX_LISTING_PAGES, reserve_seconds: float = LISTING_RESERVE_SECONDS,
                 stop: Callable[[ListingBuilder], bool] | None = None) -> ListingBuilder:
    """The list from ``url`` and its page links (see the module docstring). ``stop``: end as soon as it is
    True after a page (the page that made it True is then the browser's first page); pages left unread then
    make the list incomplete (PARTIAL_READ). The first page must be a film page of the source; later pages
    that fail only make the list incomplete."""
    builder = ListingBuilder(source.id)
    start = _list_link(source, url, url)
    if start is None:
        raise SourceError("ACCOUNT_PAGE_ONLY", f"Dán link trang phim trên {source.label} (không phải trang đăng "
                          "nhập, link vé hay link file).")
    host = urlsplit(start).hostname
    queue, seen = deque([start]), {start}
    page = browser.page()
    while queue:
        check_running(browser)
        if builder.pages >= max_pages:
            builder.note("PAGE_LIMIT")
            break
        if builder.pages and browser.stop_at is not None and browser.stop_at - time.monotonic() < reserve_seconds:
            builder.note("TIME_LIMIT")
            break
        current = queue.popleft()
        first = not builder.pages
        if not first:
            browser.wait(page, PAGE_PAUSE_SECONDS)
            check_running(browser)
        try:
            film = read_film_page(browser, reader, source, page, current, first=first)
        except SignedOut:
            if first:
                raise
            builder.note("PAGE_SIGNED_OUT")
            continue
        if film is None:
            builder.note("PAGE_FAILED")
            continue
        if not builder.add(film, current):
            continue
        for link in film.links:
            target = _list_link(source, current, link, host)
            if target is None:
                builder.note("PAGE_NOT_FOLLOWED")  # a page of the list the session may not go to
            elif target not in seen:
                seen.add(target)
                queue.append(target)
        if stop is not None and stop(builder):
            if queue:
                builder.note("PARTIAL_READ")
            return builder
    return builder


def read_film_page(browser: SessionBrowser, reader: SourcePageReader, source: SourceAccount, page: Any, url: str,
                   *, first: bool) -> FilmPage | None:
    """One page of the list; None for a later page that failed. The first page's failure is raised. A page
    without the source's film id is not a film page."""
    browser.step = "FILM_PAGE"  # the run's step for a failure's report (SessionBrowser.step)
    try:
        landed: Navigation = browser.navigate(url, page)
    except BrowserFailed as error:
        if first:
            raise browser_error(error) from None
        return None
    view = PageView(browser, page)
    check_signed_in(source, reader, view)
    failure = _status_error(landed.status, _host_of(landed.url))
    film = reader.film_page(view) if failure is None else None
    if film is not None and checked_id(film.film) is None:
        film = None
    if first and failure is not None:
        raise failure
    if first and film is None:
        raise SourceError("NOT_A_FILM_PAGE", f"Trang này không có danh sách tải phim của {source.label}.")
    return film


def _file_link(source: SourceAccount, link: str | None) -> str:
    refused = SourceError("TICKET_LINK_REFUSED", "Nút tải của vé không trỏ tới máy chủ file của nguồn; không tải.")
    if not link:
        raise refused
    try:
        url, host, port = check_link(link)
    except LinkRejected:
        raise refused from None
    if urlsplit(url).scheme != "https" or port != 443 or host not in source.hosts["files"]:
        raise refused
    return url


class _TicketFlow:
    """One ticket: the click, the ticket page among the pages that opened, and its wait (module docstring).
    Everything it knows about this click (the pages before it, its request and method, the navigation number at
    the click) is its own: one flow per click, never kept on the reader."""

    def __init__(self, browser: SessionBrowser, reader: SourcePageReader, source: SourceAccount, page: Any,
                 episode_id: str, file_id: str, until: float, request: str | None = None,
                 request_method: str | None = None):
        self.browser, self.reader, self.source, self.page = browser, reader, source, page
        self.episode_id, self.file_id = episode_id, file_id
        self.until = until
        self.before = list(browser.pages())
        self.film_url = browser.page_url(page)
        self.ticket_hosts = set(source.hosts["tickets"]) | set(source.hosts["portal"])
        # A reader whose ticket pages name no file (``shows_ticket_ids`` False) gets a ticket only through the
        # provenance of this click's own request (``_answers_this_click``).
        self.ids_shown = getattr(reader, "shows_ticket_ids", True) is not False
        self.request = None if request is None else urldefrag(request)[0]
        self.request_method = request_method  # "POST" for a release-forms form: a GET of its action is not it
        self.mark = browser.navigation_number  # taken right before the click: later requests are the click's

    def _time_left(self) -> bool:
        check_running(self.browser)
        return time.monotonic() < self.until

    def _ticket_of(self, candidate: Any) -> TicketPage | None:
        """The ticket page of the chosen file, or None (still loading, an advert, another page or file)."""
        url = self.browser.page_url(candidate)
        if candidate is self.page and url == self.film_url:
            return None
        if _host_of(url) not in self.ticket_hosts:
            return None
        view = PageView(self.browser, candidate)
        check_signed_in(self.source, self.reader, view)
        shown = self.reader.ticket_page(view)
        if shown is None or shown.state not in TICKET_STATES:
            return None
        if self.ids_shown:
            if shown.episode != self.episode_id or shown.file != self.file_id:
                return None
        elif not self._answers_this_click(candidate):
            return None
        return shown

    def _answers_this_click(self, candidate: Any) -> bool:
        """``candidate`` shows the answer to this click's own request (the chosen entry's ``request``), reached
        only through the run's own hop pages: exactly one navigation request to that URL since the click, sent
        with the entry's own method (``request_method``: a GET of a form's action is not the form's POST; the
        redirects after it are the browser's own GETs and keep no method rule); each
        redirect after it followed by the next navigation of the candidate's frame to its target; the last one
        answered 2xx at the URL the candidate shows; nothing else navigated that frame since the click. A popup
        must have been opened by the film page after the click (its first request has no frame yet; a later
        one must be the candidate's). An advert's popup, a ticket another script or a second submit opened, a
        redirect that was refused, lost or led elsewhere, or a page that navigated on by itself is never this
        file's ticket. The record of the run's navigations must be whole."""
        if self.request is None or self.request_method is None or self.browser.navigations_dropped:
            return False
        frame = self.browser.main_frame(candidate)
        if frame is None:
            return False
        steps = self.browser.navigations(self.mark)
        asked = [step for step in steps if step.url == self.request]
        if len(asked) != 1:
            return False
        first = asked[0]
        if first.method != self.request_method:
            return False
        if candidate is self.page:
            if first.frame is not frame:
                return False
        elif (candidate in self.before or self.browser.opener(candidate) is not self.page
              or first.frame not in (None, frame)):
            return False
        chain = [first]
        while chain[-1].target is not None:
            if len(chain) > MAX_HOPS:
                return False
            target = urldefrag(chain[-1].target)[0]
            following = [step for step in steps if step.number > chain[-1].number and step.frame is frame]
            if not following or following[0].url != target:
                return False  # the frame went somewhere else first, or the redirect was never followed
            chain.append(following[0])
        last = chain[-1]
        if last.status is None or not 200 <= last.status < 300:
            return False
        if urldefrag(self.browser.page_url(candidate))[0] != last.url:
            return False
        in_frame = [step.number for step in steps if step.frame is frame]
        return in_frame == [step.number for step in chain if step.frame is frame]

    def _pump(self, page: Any) -> None:
        if not self.browser.wait(page, POLL_SECONDS) and not self.browser.wait(self.page, POLL_SECONDS):
            time.sleep(POLL_SECONDS)  # both pages have gone: never a busy loop until the time is up

    def _shown(self, candidate: Any) -> TicketPage | None:
        """``_ticket_of``, with a page that cannot be read right now (loading, closing) taken as not yet."""
        try:
            return self._ticket_of(candidate)
        except BrowserFailed:
            return None

    def _close_if_foreign(self, candidate: Any) -> None:
        """A new page that already shows a host outside the ticket and portal hosts (an advert) is closed at
        once, so adverts never fill the browser's page limit; a blank page is kept until it shows a host."""
        if candidate is self.page or candidate in self.before:
            return
        host = _host_of(self.browser.page_url(candidate))
        if host and host not in self.ticket_hosts:
            self.browser.close_page(candidate)

    def find(self) -> Any:
        while self._time_left():
            for candidate in self.browser.pages():
                if self._shown(candidate) is not None:
                    return candidate
                self._close_if_foreign(candidate)
            self._pump(self.page)
        raise SourceError("TICKET_NOT_OPENED", "Không thấy trang vé của file đã chọn sau khi bấm lấy vé.")

    def wait_ready(self, ticket: Any) -> Ticket:
        missing_since: float | None = None
        while self._time_left():
            try:
                shown = self._ticket_of(ticket)
            except BrowserFailed:
                shown = None  # unreadable for now (a reload); a closed tab is told below
            if ticket not in self.browser.pages():
                raise SourceError("TICKET_TAB_CLOSED", "Trang vé đã đóng trước khi có link tải.")
            if shown is None:
                missing_since = missing_since or time.monotonic()
                if time.monotonic() - missing_since > TICKET_MISS_SECONDS:
                    raise SourceError("TICKET_FAILED", "Trang vé đã chuyển đi nơi khác trước khi có link tải.")
                self._pump(ticket)
                continue
            missing_since = None
            if shown.state == "ready":
                return Ticket(_file_link(self.source, shown.link))
            if shown.state == "expired":
                raise TicketExpired()
            if shown.state == "login":
                raise SignedOut()
            if shown.state == "gone":
                raise SourceError("UNAVAILABLE", "Nguồn báo file đã chọn không còn.")
            if shown.state == "challenge":
                raise SourceError("SOURCE_CHALLENGE", "Nguồn đòi người dùng thao tác (ví dụ CAPTCHA); BiliFlow không "
                                  "vượt qua bước này.")
            if shown.state == "error":
                raise SourceError("TICKET_FAILED", "Trang vé báo lỗi khi lấy link tải.")
            self._pump(ticket)
        raise SourceError("TICKET_TIMEOUT", "Trang vé chưa có link tải trong thời gian chờ.")

    def close_others(self, keep: Any = None) -> None:
        for candidate in self.browser.pages():
            if candidate is not self.page and candidate is not keep and candidate not in self.before:
                self.browser.close_page(candidate)


def _unclear(why: str) -> SourceError:
    return SourceError("TICKET_TARGET_UNCLEAR", f"Không xác định chắc nút lấy vé của file đã chọn ({why}); "
                       "BiliFlow không bấm.")


def _count(browser: SessionBrowser, page: Any, selector: str) -> int:
    return len(browser.read(page, selector, (), 2))


def _reveal(browser: SessionBrowser, page: Any, reveal: str) -> None:
    """Open the one ``details`` element ``reveal`` names, as a user would (a click on its own summary), unless
    it is open already (a click would fold it). Exactly one element must match, and it must show itself open
    within REVEAL_SECONDS (cut by the run's deadline like every browser call); nothing else is touched. Opening
    a season asks the source for nothing."""
    found = browser.read(page, reveal, ("open",), 2)
    if len(found) != 1:
        raise _unclear("không có hoặc có nhiều nhóm chứa file")
    if found[0].get("open") is not None:
        return
    check_running(browser)
    browser.click(page, f"{reveal} > summary")
    shown_by = time.monotonic() + REVEAL_SECONDS
    while _count(browser, page, f"{reveal}[open]") != 1:
        check_running(browser)
        if time.monotonic() >= shown_by:
            raise _unclear("nhóm tập không mở ra")
        browser.wait(page, POLL_SECONDS)


def _still_listed(browser: SessionBrowser, reader: SourcePageReader, page: Any, film_id: str, trigger: str,
                  reveal: str | None, request: str | None, request_method: str | None, episode_id: str,
                  file_id: str) -> None:
    """Right before the click (after the season was opened, if any): the page is still film ``film_id`` and lists
    exactly that file once, with the same trigger, season, request and method. Another film, a missing file or
    one shown differently: TICKET_TARGET_CHANGED; the same file shown several times alike: TICKET_TARGET_UNCLEAR."""
    film = reader.film_page(PageView(browser, page))
    if film is None or checked_id(film.film) != film_id:
        raise SourceError("TICKET_TARGET_CHANGED", "Trang nguồn không còn là phim đã chọn trước khi lấy vé; "
                          "BiliFlow không bấm.")
    same = [entry for entry in film.entries if entry.role == "file"
            and checked_id(entry.episode) == episode_id and checked_id(entry.variant) == file_id]
    if not same or any((item.trigger, item.reveal, item.request, item.request_method)
                       != (trigger, reveal, request, request_method) for item in same):
        raise SourceError("TICKET_TARGET_CHANGED", "Mục đã chọn trên trang nguồn đã đổi trước khi lấy vé; "
                          "BiliFlow không bấm.")
    if len(same) > 1:
        raise _unclear("trang hiện file này nhiều lần")


def _check_submission(browser: SessionBrowser, page: Any, trigger: str, request: str, request_method: str | None
                      ) -> None:
    """Right before the click, for an entry with a ``request``: the one control the trigger matches must submit
    exactly that request with that method, as the browser itself would send it (download_account_browser
    ``effective_submission``: the control's own ``formaction``/``formmethod``, its form owner, which may be a form
    elsewhere on the page through its ``form`` attribute, and that form's action and method), with no inline
    ``onclick`` or ``onsubmit``. Anything else: TICKET_TARGET_CHANGED, nothing clicked."""
    controls = browser.submission(page, trigger)
    if len(controls) != 1:
        raise _unclear("không có hoặc có nhiều nút khớp")
    control = controls[0]
    sent = effective_submission(control)
    if (sent is None or control.get("onclick") or control.get("onsubmit")
            or sent != (urldefrag(request)[0], request_method)):
        raise SourceError("TICKET_TARGET_CHANGED", "Nút lấy vé của mục đã chọn không gửi đúng yêu cầu đã đọc (form, "
                          "action hay method khác); BiliFlow không bấm.")


def get_ticket(browser: SessionBrowser, reader: SourcePageReader, source: SourceAccount, page: Any, trigger: str,
               episode_id: str, file_id: str, *, film_id: str, wait_seconds: float,
               reveal: str | None = None, request: str | None = None,
               request_method: str | None = None, flow_type: type[_TicketFlow] | None = None) -> Ticket:
    """The download link of file ``file_id`` of episode ``episode_id`` of film ``film_id`` from the page ``page``
    shows it on (see the module docstring). Before the one click: a reader that cannot read ticket pages is
    refused; the file's season (``reveal``) is opened when it is folded; then, with or without a season, the
    page is read again and must still be film ``film_id`` listing exactly that file with the same trigger,
    season, request and method (TICKET_TARGET_CHANGED otherwise); the trigger must match exactly one element
    (never "the first of several"), and for an entry with a ``request`` that one control must submit exactly that
    request and method (``_check_submission``). A stop or the deadline between these steps ends the run before
    the click. The click is made once; it is never repeated here. ``flow_type``: the acceptance observation's flow
    (download_account_observe), which watches the ticket page instead of taking its link; None is the
    downloader's own ``_TicketFlow``."""
    if getattr(reader, "reads_tickets", True) is not True:
        raise SourceError("TICKET_UNSUPPORTED", "BiliFlow chưa đọc được trang vé của nguồn này (cấu trúc trang vé "
                          "chưa được kiểm); không xin vé.")
    browser.step = "TICKET_CHECK"  # the run's step for a failure's report (SessionBrowser.step)
    if reveal is not None:
        _reveal(browser, page, reveal)
    check_running(browser)
    _still_listed(browser, reader, page, film_id, trigger, reveal, request, request_method, episode_id, file_id)
    if _count(browser, page, trigger) != 1:
        raise _unclear("không có hoặc có nhiều nút khớp")
    if request is not None:
        _check_submission(browser, page, trigger, request, request_method)
    check_running(browser)
    # The ticket page's time is counted from the click, after the steps above, and must fit in the run.
    now = time.monotonic()
    until = now + wait_seconds
    if browser.stop_at is not None:
        until = min(until, browser.stop_at - FINISH_RESERVE_SECONDS)  # a ticket after the deadline is never used
    if until - now < min(wait_seconds, MIN_TICKET_SECONDS):  # the click would mint a ticket nobody can read
        raise HttpError("NETWORK", "Lượt đọc trang nguồn không còn đủ thời gian để lấy vé; thử lại sau.",
                        retryable=True)
    flow = (flow_type or _TicketFlow)(browser, reader, source, page, episode_id, file_id, until, request,
                                      request_method)
    browser.step = "TICKET_CLICK"
    browser.click(page, trigger)
    ticket_page = None
    try:
        browser.step = "TICKET_FIND"
        ticket_page = flow.find()
        browser.step = "TICKET_WAIT"
        return flow.wait_ready(ticket_page)
    finally:
        browser.step = "TICKET_CLOSE"
        flow.close_others()
        if ticket_page is not None and ticket_page is not page:
            browser.close_page(ticket_page)
