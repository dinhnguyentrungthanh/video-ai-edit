"""The provider of a source account (adapter "ticket-files", M3): a pasted film page of the source's portal.

One ``AccountSourceProvider`` per configured source; its id is the source id (never a public provider's id,
download_account_config). The registry asks it about links of the source's own exact hosts only, and it
claims every one of them: a link of a source account never goes to yt-dlp or to an anonymous provider, not
even without a session or after an auth error. Only a film page of a portal host is read; a link of a
ticket or file host is refused with a hint (the user never fetches ticket or media links).

``resolve(url, ctx)`` (a SourceProvider; M4 wires it to the queue):

1. One hidden run (download_account_runs: the source's lock, one deadline, its own Edge ended if it hangs)
   with the session of this manager's root and Windows account. No session, an expired one or one the
   source refused: ``SourceLoginRequired`` (source id, generation, reason); a session that cannot be read
   now (permission, disk) keeps its own code, not a sign-in. Nothing opens a sign-in window.
2. Without a chosen file (the probe of a pasted page): the film list is read whole (download_account_pages,
   bounded). A series raises ``SourceNeedsEpisodes`` with the public list (M4 stores it, M5 shows
   "Tải tất cả" / "Chọn tập"); a film with several variants raises ``SourceNeedsChoice`` (the single-film
   path); a film with one file goes on. No ticket is asked for while a choice is open.
   A film list that was not read whole (download_account_listing) is never taken as having one file: the
   user chooses, and every choice says the list is incomplete. A page without any readable file: NO_FILES.
3. With a chosen file (``ctx.previous``: the probe's identity on a refresh, ``{"selection": key}`` after the
   film choice, ``{"account_file": ids}`` for an episode task of M4): the list is read until that file is
   seen; it must be the same film, episode and variant (else SOURCE_CHANGED). Then exactly that file's
   ticket is asked for and its download link read (download_account_pages).
4. The link is probed by ``resolve_file`` through ``ctx.http``: the downloader's cookie-free SafeHttp (a
   bounded GET with Range, the container and stream check of the sample), never with the session. The
   identity is the source's: source, film, episode and variant ids, the source's version mark of the file
   and the probed size (never the ticket, a token or a title). The plan has ``strict_versions``, so a part is
   continued only for the same version of the file (download_media_file). MKV stays MKV. The result names the
   session that issued the ticket (``issuer``: the generation of the run's own lease, checked current when the
   run ended, never one read later), so the downloader keeps the link in memory for that session only
   (download_account_tickets); ``recheck`` probes such a kept link again the same way (no browser, no ticket).

The queue side (M4): ``login_gate`` tells the dispatcher, from the account's row only (no vault, no browser),
that a link waits for a sign-in before any slot is taken; ``usable_generation`` tells it when a sign-in
landed; ``account_sid`` is the Windows account whose session this provider uses. A hidden run whose rotated
cookies could not be saved, or whose temporary profile could not be deleted, is reported through
``ResolveContext.notice`` and kept in ``warnings`` for the source's status: the session is never deleted
or extended for it, and it is never taken for an expired sign-in.

Errors: the sign-in page or the reader's signed-out evidence marks that session invalid (only its own
generation: a newer session is never touched) and raises ``SourceLoginRequired``; the same evidence from a
run whose session was replaced meanwhile asks again with the new one. A ticket that expired, a run whose
session changed while it ran, and a fresh link the file server refuses at once (401/403) are asked for
again, at most ``ticket_attempts`` times in all. A 403 page without sign-in evidence, a missing page or file,
a slow page or a network error keep their own codes. Messages never hold a link, a ticket or a cookie.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from biliflow.download_account_browser import SessionBrowser
from biliflow.download_account_config import SourceAccount
from biliflow.download_account_edge import BrowserFailed, BrowserUnavailable
from biliflow.download_account_listing import (
    REASONS,
    Episode,
    FileSelection,
    Listing,
    Variant,
    checked_id,
    film_choices,
)
from biliflow.download_account_pages import (
    MAX_LISTING_PAGES,
    PAGE_READERS,
    SignedOut,
    SourcePageReader,
    Ticket,
    TicketExpired,
    browser_error,
    get_ticket,
    read_film_page,
    read_listing,
)
from biliflow.download_account_runs import SessionRun, run_with_session
from biliflow.download_accounts import AccountError, AccountManager, LoginRequired, SessionLease
from biliflow.download_http import Cancelled, HttpError
from biliflow.download_links import LinkRejected, check_link
from biliflow.download_media_file import PlaylistLink, resolve_file
from biliflow.download_page_sources import SourceNeedsChoice
from biliflow.download_provider_config import HostList
from biliflow.download_source_types import (
    ResolveContext,
    ResolvedSource,
    SourceChanged,
    SourceDeclined,
    SourceError,
    SourceLoginRequired,
    TicketIssuer,
)

TICKET_ATTEMPTS = 2  # tickets one resolve may ask for (an expired or refused one is asked for again once)
TICKET_WAIT_SECONDS = 120.0  # the ticket page's own wait, within the run's deadline
# Warnings of a hidden run that never change the session (M4): shown on the task and the source's status.
WARNINGS = {
    "SESSION_ROTATION_NOT_SAVED": "Nguồn vừa đổi cookie phiên nhưng BiliFlow không lưu được bản mới (lưu lỗi, phiên "
                                  "vừa đổi hoặc nguồn đã đăng xuất); phiên đã lưu giữ nguyên, có thể cần đăng nhập "
                                  "lại sớm hơn.",
    "PROFILE_LEFT": "Chưa xóa được hồ sơ trình duyệt tạm của lượt ẩn; BiliFlow xóa nó ở lần khởi động sau.",
    "SESSION_STATE_UNREAD": "Không đọc được phiên của trình duyệt ẩn sau lượt chạy; kết quả của lượt vẫn được dùng, "
                            "phiên đã lưu giữ nguyên (có thể cần đăng nhập lại sớm hơn).",
}
_RETRY_MESSAGES = {
    "TICKET_EXPIRED": "Vé tải của file đã chọn hết hạn cả lần lấy lại; thử lại sau.",
    "TICKET_REFUSED": "Máy chủ file từ chối cả vé mới (HTTP 401/403); chưa có dấu hiệu cần đăng nhập lại.",
    "SESSION_CHANGED": "Phiên của nguồn vừa đổi trong lúc lấy vé; thử lại.",
}


class SourceNeedsEpisodes(SourceError):
    """A series: the user chooses "Tải tất cả" or "Chọn tập" and a variant (M4 stores the list, M5 shows it).
    ``listing``: the public list (ids, labels, order, sizes; no link, ticket or cookie)."""

    def __init__(self, listing: Mapping[str, Any]):
        super().__init__("NEEDS_EPISODES", "Phim nhiều tập: chọn Tải tất cả hoặc Chọn tập rồi bấm Tải N tập.")
        self.listing = dict(listing)


class _Retry(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class _Found:
    """What one hidden run found: the list (whole, or up to the chosen file) and, when asked, the ticket."""
    listing: Listing
    selection: FileSelection | None = None
    episode: Episode | None = None
    variant: Variant | None = None
    ticket: Ticket | None = None
    generation: int | None = None  # the generation of the lease whose run got the ticket (_session_run)


@dataclass(frozen=True)
class _Failed:
    """An error of the run's own pages, returned as a value so the run still keeps its rotated cookies."""
    error: Exception


def _failure_words(code: str, error: Exception) -> str:
    """``code`` and what the browser's error says in fixed words: its own code when it is more precise, and
    where and what kind (``BrowserFailed.detail``). Never a host or the error's text."""
    if not isinstance(error, BrowserFailed):
        return code
    parts = [code] + ([error.code] if error.code != code else []) + ([error.detail[:80]] if error.detail else [])
    return ", ".join(parts)


def _log_stats(run: SessionRun[Any], ticket: bool, log: Callable[[str], None] | None) -> None:
    """One line in the task's log on where the hidden run's time went and how its session loaded and was read
    (numbers and fixed words only)."""
    stats = run.stats
    if log is None or not stats:
        return
    got = " (có vé)" if ticket and getattr(run.value, "ticket", None) is not None else ""
    loaded = {1: "; phiên nạp không kèm IndexedDB", 2: "; phiên nạp chỉ cookie"}.get(stats.get("session_loaded"), "")
    if loaded:
        loaded += f" (IndexedDB: {stats.get('load_cause') or 'OTHER'})"
    kept = (f"; không đọc được IndexedDB mới ({stats.get('save_cause') or 'OTHER'}): lưu cookie mới và dữ liệu "
            "trang đã nạp" if stats.get("indexed_db_kept") else "")
    try:
        log(f"Lượt ẩn{got}: {stats.get('seconds', 0):.0f} s; mở Edge và nạp phiên "
            f"{stats.get('launch_seconds', 0):.0f} s; {stats.get('answered', 0)} yêu cầu qua mạng, chờ "
            f"{stats.get('network_seconds', 0):.0f} s; bỏ qua {stats.get('skipped', 0)} ảnh/font{loaded}{kept}.")
    except Exception:  # noqa: BLE001 - a log line never changes the run's result
        pass


def _wanted(previous: Mapping[str, Any] | None) -> FileSelection | str | None:
    if not previous:
        return None
    if isinstance(previous.get("account_file"), Mapping):
        return FileSelection.from_mapping(previous["account_file"])
    if previous.get("kind") == "account-file":
        return FileSelection.from_mapping(previous)
    selection = previous.get("selection")
    return selection if isinstance(selection, str) and selection else None


class AccountSourceProvider:
    """See the module docstring. ``manager``: the Control Center's AccountManager (None until M4 wires it: every
    link of the source is then refused, never handed elsewhere). ``reader``: the page reader of the source's
    adapter (``PAGE_READERS``; the tests give a fixture reader). ``run_options``: the tests' injection into
    ``run_with_session`` (network, limits); production code never passes them."""

    # The flow ``get_ticket`` uses after its click: None is the downloader's own. Only the acceptance
    # observation's subclass (download_account_observe), which never resolves a file, sets another.
    ticket_flow: Any = None

    def __init__(self, source: SourceAccount, manager: AccountManager | None = None, *,
                 reader: SourcePageReader | None = None, run_options: Mapping[str, Any] | None = None,
                 ticket_attempts: int = TICKET_ATTEMPTS, ticket_seconds: float = TICKET_WAIT_SECONDS,
                 max_pages: int = MAX_LISTING_PAGES):
        if not 1 <= ticket_attempts <= 5:
            raise ValueError("ticket_attempts must be 1 to 5")
        self.id = source.id
        self.label = source.label
        self.source = source
        self.manager = manager
        self.reader = reader if reader is not None else PAGE_READERS.get(source.adapter.id)
        self.hosts = HostList(source.all_hosts)
        self.ticket_attempts = ticket_attempts
        self.ticket_seconds = ticket_seconds
        self.max_pages = max_pages
        self._run_options = dict(run_options or {})
        self.warnings: dict[str, dict[str, str]] = {}  # code -> the latest warning of that kind (source status)

    def claims(self, url: str) -> bool:
        return self.hosts.matches(url)

    # The queue side (M4) ----------------------------------------------------------------------------------

    @property
    def account_sid(self) -> str | None:
        """The Windows account whose session this provider uses (None without a manager)."""
        return self.manager.account_sid if self.manager is not None else None

    def login_gate(self, url: str) -> SourceLoginRequired | None:
        """The sign-in ``url`` waits for, read before a slot is taken (no vault, no browser, no window): None when
        the saved session may be used, or when the link fails for another reason its probe reports (not a
        portal page, no manager, no page reader) or the account's row cannot be read now."""
        try:
            self._check_ready(url)
        except SourceError:
            return None
        assert self.manager is not None
        try:
            usable, reason, generation = self.manager.session_gate(self.id)
        except (sqlite3.Error, AccountError):
            return None
        if usable:
            return None
        return SourceLoginRequired(self.id, generation, reason or "NOT_CONNECTED", self.label)

    def usable_generation(self) -> int | None:
        """The generation of a session that may be used now (the dispatcher wakes the tasks that waited for an
        older one); None without one."""
        if self.manager is None:
            return None
        try:
            usable, _reason, generation = self.manager.session_gate(self.id)
        except (sqlite3.Error, AccountError):
            return None
        return generation if usable else None

    # The SourceProvider side --------------------------------------------------------------------------

    def resolve(self, url: str, ctx: ResolveContext) -> ResolvedSource:
        self._check_ready(url)
        wanted = _wanted(ctx.previous)
        last = "TICKET_EXPIRED"
        for _attempt in range(self.ticket_attempts):
            try:
                found = self._session_run(url, wanted, ctx.control, ticket=True, notice=ctx.notice, log=ctx.log)
            except _Retry as retry:
                last = retry.code
                continue
            if found.ticket is None:
                if found.listing.kind == "series":
                    raise SourceNeedsEpisodes(found.listing.public())
                raise SourceNeedsChoice(film_choices(found.listing))
            try:
                return self._probe(found, ctx)
            except HttpError as error:
                if isinstance(error, Cancelled) or error.code not in ("FORBIDDEN", "LOGIN_REQUIRED"):
                    raise
                last = "TICKET_REFUSED"  # a fresh ticket refused at once: ask for another one
            wanted = found.selection
        raise SourceError(last, _RETRY_MESSAGES[last])

    def discover(self, url: str, control: Any) -> Listing:
        """The film list only, without any ticket (what M4 stores for "Tải tất cả" / "Chọn tập")."""
        self._check_ready(url)
        try:
            return self._session_run(url, None, control, ticket=False).listing
        except _Retry as retry:
            raise SourceError(retry.code, _RETRY_MESSAGES[retry.code]) from None

    # Inside ----------------------------------------------------------------------------------------

    def _check_ready(self, url: str) -> None:
        try:
            host = check_link(url)[1]  # the host the registry matched (normalized like a pasted link)
        except LinkRejected:
            host = ""
        if host not in self.source.hosts["portal"]:
            raise SourceError("ACCOUNT_PAGE_ONLY", f"Dán link trang phim trên {self.label}; BiliFlow tự lấy vé và "
                              "link file, không cần link vé hay link file.")
        if self.manager is None:
            raise SourceError("ACCOUNT_NOT_READY", f"Tải bằng tài khoản nguồn {self.label} chưa được bật trong bản "
                              "này; BiliFlow không tải link này bằng cách khác.")
        if self.reader is None:
            raise SourceError("READER_UNSUPPORTED", f"BiliFlow chưa có bộ đọc trang của {self.label} (cấu trúc trang "
                              "thật chưa được kiểm); không tải link này bằng cách khác.")

    def _browser_hosts(self) -> frozenset[str]:
        """The portal and ticket hosts: the hidden browser never reaches a file server."""
        return frozenset(self.source.hosts["portal"]) | frozenset(self.source.hosts["tickets"])

    def _session_run(self, url: str, wanted: FileSelection | str | None, control: Any, *, ticket: bool,
                     notice: Callable[[str, str], None] | None = None,
                     log: Callable[[str], None] | None = None) -> _Found:
        def action(browser: SessionBrowser) -> _Found | _Failed:
            try:
                return self._walk(browser, url, wanted, ticket)
            except Cancelled:
                raise
            except (SignedOut, TicketExpired, SourceError, HttpError, BrowserFailed) as error:
                if browser.control.requested:
                    raise  # the run was stopped: download_account_runs makes it Cancelled or RunTimedOut
                return _Failed(error)

        assert self.manager is not None
        try:
            # The source's page script (M7 exception A) loads only in a run that may ask for a ticket.
            run = run_with_session(self.manager, self.source.id, action, control=control,
                                   hosts=self._browser_hosts(), page_script=ticket, **self._run_options)
        except LoginRequired as error:
            raise SourceLoginRequired(self.source.id, error.generation, error.code, self.label) from None
        except (BrowserUnavailable, BrowserFailed) as error:
            code = "BROWSER_UNAVAILABLE" if isinstance(error, BrowserUnavailable) else "BROWSER_FAILED"
            words = _failure_words(code, error)
            raise SourceError(code, f"Trình duyệt ẩn của nguồn không chạy được ({words}).") from None
        except AccountError as error:  # the session cannot be read now, or the source left the config
            raise SourceError(error.code, error.message) from None
        except sqlite3.Error:
            raise SourceError("ACCOUNT_STATE_ERROR", "Không đọc được trạng thái tài khoản nguồn (cơ sở dữ liệu bận "
                              "hoặc lỗi); thử lại sau.") from None
        self._warn(run, notice)
        _log_stats(run, ticket, log)
        if control.requested:
            raise Cancelled()  # the run ended just as the caller stopped it: nothing it found is used
        lease = run.lease
        if isinstance(run.value, _Failed):
            self._raise_failure(run.value.error, lease)
        found = run.value
        if found.ticket is not None and lease is not None and not self._current(lease):
            raise _Retry("SESSION_CHANGED")  # a ticket of a session the user replaced or disconnected meanwhile
        if found.ticket is not None and lease is not None:
            found = replace(found, generation=lease.generation)  # the session that issued it, never one read later
        return found

    def _warn(self, run: SessionRun[Any], notice: Callable[[str, str], None] | None) -> None:
        codes = ((["SESSION_ROTATION_NOT_SAVED"] if run.saved is False else [])
                 + (["SESSION_STATE_UNREAD"] if run.state_unread else [])
                 + (["PROFILE_LEFT"] if run.profile_left else []))
        for code in codes:
            message = WARNINGS[code]
            if code == "SESSION_STATE_UNREAD":
                message = f"{message} ({run.state_unread[:80]})"  # fixed words only (SessionRun.state_unread)
            self.warnings[code] = {"code": code, "message": message,
                                   "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            if notice is not None:
                try:
                    notice(code, message)
                except Exception:  # noqa: BLE001 - a warning never changes the run's result
                    pass

    def _current(self, lease: SessionLease) -> bool:
        try:
            return self.manager is not None and self.manager.lease_is_current(lease)
        except (sqlite3.Error, AccountError):  # the database failed, or the source left the config
            return False

    def _raise_failure(self, error: Exception, lease: SessionLease | None) -> None:
        if isinstance(error, SignedOut):
            if lease is None or not self._current(lease):
                raise _Retry("SESSION_CHANGED")  # evidence about a session that is no longer the saved one
            try:
                marked = self.manager.mark_invalid(lease)  # type: ignore[union-attr] - only that generation
            except (sqlite3.Error, AccountError):
                marked = True  # not recorded; the evidence about this session still stands
            if not marked:
                raise _Retry("SESSION_CHANGED")  # a new sign-in landed just now: ask again with it
            raise SourceLoginRequired(self.source.id, lease.generation, "SESSION_REJECTED", self.label)
        if isinstance(error, TicketExpired):
            raise _Retry("TICKET_EXPIRED")
        if isinstance(error, BrowserFailed):
            raise browser_error(error)
        raise error

    def _walk(self, browser: SessionBrowser, url: str, wanted: FileSelection | str | None, ticket: bool) -> _Found:
        assert self.reader is not None
        if isinstance(wanted, FileSelection):
            return self._walk_to(browser, url, wanted)
        builder = read_listing(browser, self.reader, self.source, url, max_pages=self.max_pages)
        listing = builder.build()
        if not listing.episodes:
            why = f" ({REASONS[listing.reasons[0]]})" if listing.reasons else ""
            raise SourceError("NO_FILES", f"Trang phim không có file tải nào đọc được{why}.")
        if listing.kind == "series" or not ticket:
            return _Found(listing)
        episode = listing.episodes[0]
        if isinstance(wanted, str):
            chosen = next((variant for variant in episode.variants
                           if listing.selection(episode, variant).key == wanted), None)
            if chosen is None:
                raise SourceChanged("bản phim đã chọn không còn")
        elif len(episode.variants) == 1 and listing.complete:
            chosen = episode.variants[0]
        else:
            return _Found(listing)  # several variants, or a list not read whole: the user chooses first
        return self._ticket(browser, listing, episode, chosen)

    def _walk_to(self, browser: SessionBrowser, url: str, wanted: FileSelection) -> _Found:
        assert self.reader is not None
        if wanted.source != self.source.id:
            raise SourceChanged("nguồn")
        target = (wanted.episode, wanted.variant)
        builder = read_listing(browser, self.reader, self.source, url, max_pages=self.max_pages,
                               stop=lambda seen: target in seen.navigation)
        if builder.film != wanted.film:
            raise SourceChanged("phim")
        listing = builder.build()
        found = listing.find(wanted)
        if found is None:
            if listing.complete:
                raise SourceChanged("mục đã chọn")
            raise SourceError("ITEM_NOT_FOUND", "Chưa thấy file đã chọn trong phần danh sách đọc được (danh sách "
                              "chưa đầy đủ); thử lại sau.")
        return self._ticket(browser, listing, *found)

    def _ticket(self, browser: SessionBrowser, listing: Listing, episode: Episode, variant: Variant) -> _Found:
        assert self.reader is not None
        page_url, trigger, reveal, request, method = listing.navigation[(episode.key, variant.id)]
        page = browser.page()
        if browser.page_url(page) != page_url:  # the whole list was read: back to the page that lists the file
            film = read_film_page(browser, self.reader, self.source, page, page_url, first=True)
            if film is None or checked_id(film.film) != listing.film:
                raise SourceChanged("phim")
            fresh = [entry for entry in film.entries if entry.role == "file"
                     and checked_id(entry.episode) == episode.key and checked_id(entry.variant) == variant.id]
            if (len(fresh) != 1 or not fresh[0].trigger
                    or (fresh[0].request, fresh[0].request_method) != (request, method)):  # another action
                raise SourceChanged("mục đã chọn")
            trigger, reveal = fresh[0].trigger, fresh[0].reveal
        # get_ticket reads the page once more right before its click: still this film, file, trigger, season,
        # request and method.
        got = get_ticket(browser, self.reader, self.source, page, trigger, episode.key, variant.id,
                         film_id=listing.film, wait_seconds=self.ticket_seconds, reveal=reveal, request=request,
                         request_method=method, flow_type=self.ticket_flow)
        return _Found(listing, listing.selection(episode, variant), episode, variant, got)

    def _probe(self, found: _Found, ctx: ResolveContext) -> ResolvedSource:
        """The ticket's file through the cookie-free SafeHttp of the downloader (see the module docstring)."""
        assert found.ticket is not None and found.selection is not None
        file = self._probe_link(found.ticket.link, ctx)
        listing, episode, variant = found.listing, found.episode, found.variant
        assert episode is not None and variant is not None
        identity = {"kind": "account-file", **found.selection.public(), "version": variant.version,
                    "size": file.estimated_bytes}
        parts = [listing.title] + ([episode.label] if listing.kind == "series" else []) + [variant.label]
        issuer = (TicketIssuer(self.manager.owner, self.id, found.generation)
                  if self.manager is not None and found.generation is not None else None)
        return replace(file, identity=identity, title=" · ".join(part for part in parts if part)[:300],
                       plan=replace(file.plan, strict_versions=True), issuer=issuer)

    def recheck(self, source: ResolvedSource, ctx: ResolveContext) -> ResolvedSource:
        """A link this provider resolved before (kept for its task, download_account_tickets), probed again by the
        same bounded GET of its first bytes: no browser, no session, no ticket. The source comes back with the
        answer's own plan (validator, size: the same link does not prove the same version) and the size in its
        identity, everything else as it was. Errors as at the probe: an HttpError (401/403/404…, NETWORK) as the
        client raised it, a playlist or a body that is not a video as UNSUPPORTED_FORMAT / FILE_NOT_VIDEO."""
        file = self._probe_link(source.media_url, ctx)
        return replace(source, plan=replace(file.plan, strict_versions=True), estimated_bytes=file.estimated_bytes,
                       identity={**source.identity, "size": file.estimated_bytes})

    def _probe_link(self, link: str, ctx: ResolveContext) -> ResolvedSource:
        try:
            return resolve_file(link, ctx, provider=self.id, label=self.label)
        except PlaylistLink:
            raise SourceError("UNSUPPORTED_FORMAT", "Link của vé là danh sách HLS; nguồn tài khoản chỉ tải file.") from None
        except SourceDeclined:
            raise SourceError("FILE_NOT_VIDEO", "Máy chủ file không trả về video (có thể cần phiên đăng nhập; BiliFlow "
                              "không gửi phiên tới máy chủ file).") from None
