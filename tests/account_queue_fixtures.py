"""Fakes for the M4 queue tests of source accounts (docs/SOURCE_ACCOUNTS_PLAN.md 9.14).

- ``series``/``film``: made-up film lists built by the real ``ListingBuilder`` (ids, numbers, variants), so
  their ``public()`` and fingerprint are the code's own.
- ``QueueProvider``: the real ``AccountSourceProvider`` (its queue side: ``login_gate``, ``usable_generation``,
  ``account_sid``, ``warnings``, ``_check_ready``) whose ``resolve`` reads the made-up list instead of a hidden
  browser run and asks the session's state from the real manager (no vault read, no browser, no ticket). A
  chosen file is served by the fixture server on 127.0.0.1 through the downloader's own SafeHttp. ``more``: other
  made-up lists of the same source, each read for the page whose last path part is its film id.
- ``account_manager``: a real ``AccountManager`` on a temporary root with the fake protector and ACL of
  tests/test_download_accounts.py, for one Windows account (SID).

Hosts are ``.example`` only; titles are made up.
"""
from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from biliflow.download_account_config import AccountConfig, AdapterSpec, SourceAccount
from biliflow.download_account_listing import FilmPage, Listing, ListingBuilder, RawEntry, film_choices
from biliflow.download_account_sources import AccountSourceProvider, SourceNeedsEpisodes, _wanted
from biliflow.download_account_vault import SessionVault
from biliflow.download_accounts import AccountManager
from biliflow.download_media_file import resolve_file
from biliflow.download_page_sources import SourceNeedsChoice
from biliflow.download_source_types import ResolveContext, ResolvedSource, SourceChanged, SourceLoginRequired
from tests.test_download_accounts import Clock, FakeProtector, GoodAcl, state

SID = "S-1-5-21-1-2-3-1001"
OTHER_SID = "S-1-5-21-1-2-3-1002"
TITLE = "Phim thử nghiệm"


def account_source(source_id: str = "alpha") -> SourceAccount:
    host = f"{source_id}.example"
    return SourceAccount(source_id, AdapterSpec("ticket-files", "ttl"), f"Nguồn {source_id}", f"https://{host}/login",
                         {"portal": (host,), "tickets": (f"tickets.{host}",), "files": (f"files.{host}",)})


def page_url(source_id: str = "alpha", film: str = "f1") -> str:
    return f"http://{source_id}.example/film/{film}"


def file_path(episode: str, variant: str) -> str:
    return f"/files/{episode}--{variant}.bin"


def file_url(source_id: str, episode: str, variant: str) -> str:
    return f"http://files.{source_id}.example{file_path(episode, variant)}"


def entry(episode: str, variant: str, *, season: str | None = "s1", season_number: int | None = 1,
          number: int | None = None, label: str | None = None, special: bool = False, after: int | None = None,
          quality: str | None = "1080p", audio: str | None = "Vietsub", size: int | None = 1000) -> RawEntry:
    return RawEntry("file", episode=episode, variant=variant, season=season, season_number=season_number,
                    episode_number=number, episode_label=label, special=special, after=after, quality=quality,
                    audio=audio, size=size, trigger=f"#t-{episode}-{variant}")


def series(count: int = 12, *, source_id: str = "alpha", film: str = "f1", title: str = TITLE,
           seasons: tuple[tuple[int, int], ...] | None = None, variants: tuple[tuple[str, str], ...] = (
               ("v720", "720p"), ("v1080", "1080p")), extra: tuple[RawEntry, ...] = (), complete: bool = True,
           sizes: bool = True) -> Listing:
    """A made-up series: ``seasons`` ((number, episodes), …), or one season of ``count`` episodes; each episode
    has every variant of ``variants`` (id, quality)."""
    entries = []
    for season_number, episodes in seasons or ((1, count),):
        for number in range(1, episodes + 1):
            for variant, quality in variants:
                entries.append(entry(f"s{season_number}e{number}", variant, season=f"s{season_number}",
                                     season_number=season_number, number=number, quality=quality,
                                     size=1000 + number if sizes else None))
    builder = ListingBuilder(source_id)
    builder.add(FilmPage(film, title, "series", tuple(entries) + extra, more_unlinked=not complete),
                page_url(source_id, film))
    return builder.build()


def film(*, source_id: str = "alpha", variants: tuple[str, ...] = ("v1080",)) -> Listing:
    builder = ListingBuilder(source_id)
    builder.add(FilmPage("m1", "Phim lẻ thử", "film", tuple(entry("m1", variant, season=None, season_number=None)
                                                            for variant in variants)), page_url(source_id, "m1"))
    return builder.build()


def account_manager(root: Path, config: AccountConfig, *, user_sid: str = SID, clock: Clock | None = None,
                    protector: FakeProtector | None = None) -> AccountManager:
    vault = SessionVault(root, protector=protector or FakeProtector(), acl=GoodAcl(user_sid))
    return AccountManager(root, config, vault=vault, clock=clock or Clock())


def connect(manager: AccountManager, source_id: str = "alpha", value: str = "v1") -> dict[str, Any]:
    """A made-up session saved through the manager's own sign-in steps (no window)."""
    attempt = manager.begin_login(source_id)
    return manager.complete_login(attempt, state(value, source_id))


def episode_of(ctx: ResolveContext) -> str | None:
    """The episode (or film) id a resolve is for: an episode task's probe or a download's fresh source; None for
    a pasted page."""
    previous = ctx.previous or {}
    chosen = previous.get("account_file") if isinstance(previous.get("account_file"), dict) else previous
    return chosen.get("episode") if isinstance(chosen, dict) else None


class QueueProvider(AccountSourceProvider):
    """See the module docstring. ``errors``: exceptions raised by the next resolves, in turn (a probe or a
    download's fresh source); ``before``: a hook run at each resolve; ``calls``: the ``previous`` of each."""

    def __init__(self, source: SourceAccount, manager: AccountManager | None, listing: Listing, *,
                 more: tuple[Listing, ...] = ()):
        super().__init__(source, manager, reader=object())  # a reader exists: only resolve reads pages, here none
        self.listing = listing
        self.pages = {item.film: item for item in (*more, listing)}
        self.errors: list[BaseException] = []
        self.before: Callable[[ResolveContext], None] | None = None
        self.calls: list[Any] = []
        self._calls_lock = threading.Lock()

    def link_for(self, episode: str, variant: str) -> str:
        """The file link a resolve hands out (the same every time here; tests/test_download_account_reuse mints a
        ticket per resolve)."""
        return file_url(self.id, episode, variant)

    def listing_for(self, url: str) -> Listing:
        """The made-up list of the pasted page (its last path part names the film), else ``listing``."""
        return self.pages.get(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1], self.listing)

    def show(self, listing: Listing) -> None:
        """The page of ``listing.film`` now lists ``listing`` (episodes added on the site, a list read whole or
        not): every later resolve of that page reads it, a probe and an episode's fresh source alike."""
        if listing.film == self.listing.film:
            self.listing = listing
        self.pages[listing.film] = listing

    def resolve(self, url: str, ctx: ResolveContext) -> ResolvedSource:
        self._check_ready(url)
        with self._calls_lock:
            self.calls.append(ctx.previous)
            error = self.errors.pop(0) if self.errors else None
        if self.before is not None:
            self.before(ctx)
        usable, reason, generation = self.manager.session_gate(self.id)
        if not usable:
            raise SourceLoginRequired(self.id, generation, reason or "NOT_CONNECTED", self.label)
        if error is not None:
            raise error
        listing = self.listing_for(url)
        wanted = _wanted(ctx.previous)
        if wanted is None:
            if listing.kind == "series":
                raise SourceNeedsEpisodes(listing.public())
            if len(listing.episodes[0].variants) > 1:
                raise SourceNeedsChoice(film_choices(listing))
            episode = listing.episodes[0]
            wanted = listing.selection(episode, episode.variants[0])
        elif isinstance(wanted, str):
            wanted = next(listing.selection(item, variant) for item in listing.episodes
                          for variant in item.variants if listing.selection(item, variant).key == wanted)
        found = listing.find(wanted)
        if found is None:
            raise SourceChanged("mục đã chọn")
        episode, variant = found
        file = resolve_file(self.link_for(episode.key, variant.id), ctx, provider=self.id, label=self.label)
        identity = {"kind": "account-file", **wanted.public(), "version": variant.version,
                    "size": file.estimated_bytes}
        parts = [listing.title] + ([episode.label] if listing.kind == "series" else []) + [variant.label]
        return replace(file, identity=identity, title=" · ".join(part for part in parts if part)[:300],
                       plan=replace(file.plan, strict_versions=True))
