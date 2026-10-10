"""Which pasted links BiliFlow fetches itself (source providers), and the transfer that fetches them.

The registry asks each provider, in order, whether it claims a link (``claims``: the link only, no
network); a site provider is only asked about links whose host is one of the exact hosts the local config
gives it. A claimed link is resolved at PROBING (``resolve``) and fetched by ``SourceTransfers`` through
the checked HTTP client (``download_http``); every other link, and every link a provider declines,
goes to yt-dlp exactly as before.

Providers in the code:
- ``direct``: a link that names a media file (``.mp4``, ``.m4v``, ``.mov``, ``.mkv``, ``.webm``, ``.ts``) or
  an HLS playlist (``.m3u8``) on any public host. It reads that link only, never a web page.

Adding a provider (for a site whose videos the user may download): implement ``SourceProvider`` (an
``id``, a ``label``, ``claims`` and ``resolve``, which returns a ``ResolvedSource`` with a stable
identity and an ``http_file`` or ``hls`` transport), add its class to ``SITE_PROVIDERS`` and list its
exact host names in ``config/download_providers.local.json`` (ignored by Git: real host names never go
into the repository; see ``config/download_providers.example.json``). Hosts match exactly, never as a
substring. A provider reads public page or player data only; DRM, paywalls, logins, cookies and anti-bot
challenges stay out of scope (AGENTS.md), and its requests go through ``ResolveContext.http`` only.

The config only switches providers of the code on for hosts: it never names code to load or a command to
run. Hosts it lists for an id that no provider of the code has are recognized, not supported: those links
go to yt-dlp like any other, and the task says that BiliFlow has no reader for them
(``SourceRegistry.recognized_without_provider``).

Source accounts (``config/download_accounts.local.json``, docs/SOURCE_ACCOUNTS_PLAN.md) get one provider each
(``download_account_sources.AccountSourceProvider``, id = the source id), asked first and only about links of
that source's own exact hosts. It claims all of them, so such a link never goes to yt-dlp or an anonymous
provider, with or without a session: without the Control Center's account manager (``accounts``, wired in
M4) or a page reader for the source it ends with a clear reason instead. A source the account config left
out keeps its hosts (``dropped_account``): a link of one is refused when pasted, with the reason, unless a
public provider keeps the host (then it goes the anonymous way, without any session).
"""
from __future__ import annotations

import errno
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Mapping, Protocol, Sequence
from urllib.parse import urlsplit

from biliflow.download_hls import SEGMENT_WORKERS, HlsTransfer, resolve_hls
from biliflow.download_http import HttpError, SafeHttp, Scope
from biliflow.download_media_file import FileTransfer, PlaylistLink, resolve_file
# The ids, host lists and config helpers live in a leaf module that the source accounts share (no import cycle);
# they are imported here under their old names.
from biliflow.download_provider_config import (  # noqa: F401 - names other modules import from here
    LOCAL_CONFIG,
    PROVIDER_ID,
    SHOWN_ITEMS,
    SHOWN_REASON_CHARS,
    SHOWN_VALUE_CHARS,
    HostList,
    _cut,
    _listing,
    _shown,
    _skipped,
    _without_link_parts,
    describe_config_problems,
    read_provider_config,
)
from biliflow.download_runner import DownloadOutcome, Progress, ProcessControl, SizeGuard, stop_reason
from biliflow.download_source_types import ResolveContext, ResolvedSource, SourceError
from biliflow.download_transfer import LogBuffer, ProgressMeter, finished_media, mark_finished

if TYPE_CHECKING:
    from biliflow.download_account_config import DroppedSource
    from biliflow.download_accounts import AccountManager

MEDIA_SUFFIXES = (".mp4", ".m4v", ".mov", ".mkv", ".webm", ".ts")
PLAYLIST_SUFFIX = ".m3u8"
# After the retries, these leave a good part on disk: the task ends INTERRUPTED and "Tiếp tục" continues it.
RESUMABLE_CODES = frozenset({"NETWORK", "SERVER_BUSY", "DNS_FAILED"})
# A provider's own passing failures while it fetches a fresh link, before or during the transfer (its hidden browser
# did not start or run, a source account's state could not be read, a ticket page was slow): the part on disk is as
# it was, so the task ends INTERRUPTED too instead of FAILED, where only "Thử lại" (from byte 0) was left.
RESUMABLE_SOURCE_CODES = frozenset({"BROWSER_FAILED", "BROWSER_UNAVAILABLE", "ACCOUNT_STATE_ERROR", "TICKET_TIMEOUT"})


def resumable_error(error: Exception) -> bool:
    """A failure after which the part on disk is still good and "Tiếp tục" continues it."""
    if isinstance(error, HttpError):
        return error.code in RESUMABLE_CODES
    return type(error) is SourceError and error.code in RESUMABLE_SOURCE_CODES


class SourceProvider(Protocol):
    id: str
    label: str

    def claims(self, url: str) -> bool:
        """True when this provider handles ``url``; decided from the link alone (no network)."""

    def resolve(self, url: str, ctx: ResolveContext) -> ResolvedSource:
        """The media of ``url``; raises SourceDeclined (back to yt-dlp) or SourceError (FAILED)."""


class DirectMediaProvider:
    """A pasted link to a media file or an HLS playlist (see the module docstring)."""

    id = "direct"
    label = "Link file trực tiếp"
    hls_label = "Link HLS trực tiếp"

    def claims(self, url: str) -> bool:
        return urlsplit(url).path.lower().endswith((*MEDIA_SUFFIXES, PLAYLIST_SUFFIX))

    def resolve(self, url: str, ctx: ResolveContext) -> ResolvedSource:
        if urlsplit(url).path.lower().endswith(PLAYLIST_SUFFIX):
            return resolve_hls(url, ctx, provider=self.id, label=self.hls_label)
        try:
            return resolve_file(url, ctx, provider=self.id, label=self.label)
        except PlaylistLink:
            return resolve_hls(url, ctx, provider=self.id, label=self.hls_label)


# Native site providers, activated only for exact hosts from the Git-ignored local config.
from biliflow.download_page_sources import ArticleMp4Provider, PlayerHlsProvider

from biliflow.download_embedded_source import EmbeddedMediaProvider

SITE_PROVIDERS: tuple[type, ...] = (PlayerHlsProvider, ArticleMp4Provider, EmbeddedMediaProvider)

# Source accounts: one provider per configured source (see the module docstring).
from biliflow.download_account_config import read_account_config
from biliflow.download_account_sources import AccountSourceProvider


class SourceRegistry:
    def __init__(self, providers: Sequence[SourceProvider], *, site_hosts: Mapping[str, HostList] | None = None,
                 recognized: Mapping[str, HostList] | None = None, config_problems: Sequence[str] = (),
                 account_problems: Sequence[str] = (), dropped: Sequence[DroppedSource] = ()):
        ids = [provider.id for provider in providers]
        if len(set(ids)) != len(ids):
            raise ValueError(f"Duplicate provider ids: {ids}")
        self._providers = tuple(providers)
        self.config_problems = tuple(config_problems)  # what the local config ignored (read_provider_config)
        self.account_problems = tuple(account_problems)  # what the account config left out (shown from M5)
        # The exact hosts of each site provider: it is only asked about links of those hosts.
        self._site_hosts = {key: hosts for key, hosts in (site_hosts or {}).items() if key in ids}
        # Hosts the local config lists for an id that none of these providers has.
        self._recognized = {key: hosts for key, hosts in (recognized or {}).items() if key not in ids}
        # Sources the account config left out: their reason and hosts (refused at paste, see dropped_account).
        self._dropped = tuple((item.reason, HostList(item.hosts)) for item in dropped)
        self._public_ids = frozenset(provider.id for provider in providers if not hasattr(provider, "login_gate"))

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(provider.id for provider in self._providers)

    def provider_for(self, url: str) -> SourceProvider | None:
        """The first provider that claims ``url``. A site provider is asked only when the link's host is one of
        its exact hosts, so its own ``claims`` can narrow that choice (pages only, say), never widen it."""
        for provider in self._providers:
            hosts = self._site_hosts.get(provider.id)
            if (hosts is None or hosts.matches(url)) and provider.claims(url):
                return provider
        return None

    def get(self, provider_id: str | None) -> SourceProvider | None:
        return next((provider for provider in self._providers if provider.id == provider_id), None)

    def dropped_account(self, url: str) -> tuple[str, bool] | None:
        """For a link of a source the account config left out: its reason, and True when a public provider of the
        local config keeps that host (the link then goes the anonymous way, as before). None otherwise."""
        for reason, hosts in self._dropped:
            if hosts.matches(url):
                kept = any(listed.matches(url) for key, listed in self._site_hosts.items() if key in self._public_ids)
                return reason, kept
        return None

    def recognized_without_provider(self, url: str) -> str | None:
        """The id the local config lists this link's host under when the code has no provider of that id:
        BiliFlow recognizes the host but has no reader for it (the link still goes to yt-dlp)."""
        return next((key for key, hosts in self._recognized.items() if hosts.matches(url)), None)


def default_registry(root: Path, *, site_providers: Sequence[type] = SITE_PROVIDERS,
                     accounts: AccountManager | None = None) -> SourceRegistry:
    """The account sources' providers (their exact hosts), then site providers that have hosts in the local
    config (exact hosts), then the direct links; the hosts of every other configured id are only recognized
    (``recognized_without_provider``). ``accounts``: the Control Center's account manager (M4); its config is
    used when given, else the account config is read here and its providers refuse every link."""
    for factory in site_providers:
        if not isinstance(factory.id, str) or not PROVIDER_ID.fullmatch(factory.id):
            raise ValueError(f"Provider id {factory.id!r} does not match {PROVIDER_ID.pattern}")
    hosts, problems = read_provider_config(root)
    sites = [factory(hosts[factory.id]) for factory in site_providers if factory.id in hosts]
    account_config = accounts.config if accounts is not None else read_account_config(root, provider_hosts=hosts)
    taken = {DirectMediaProvider.id, *(site.id for site in sites)}
    owned = [AccountSourceProvider(source, accounts) for source in account_config.sources.values()
             if source.id not in taken]
    site_hosts = {**{site.id: hosts[site.id] for site in sites}, **{owner.id: owner.hosts for owner in owned}}
    return SourceRegistry([*owned, *sites, DirectMediaProvider()], site_hosts=site_hosts, recognized=hosts,
                          config_problems=problems, account_problems=account_config.problems,
                          dropped=account_config.dropped)


class SourceTransfers:
    """The worker's counterpart of ``YtDlpRunner.download`` for a resolved source."""

    def __init__(self, http: SafeHttp, *, ffmpeg: Path, ffprobe: Path | None, segment_workers: int = SEGMENT_WORKERS):
        self.files = FileTransfer(http, ffmpeg, ffprobe)
        self.hls = HlsTransfer(http, ffmpeg, ffprobe, workers=segment_workers)

    def download(self, source: ResolvedSource, refresh: Callable[[], ResolvedSource], task_dir: Path,
                 control: ProcessControl, *, on_progress: Callable[[Progress], None],
                 on_log: Callable[[list[str]], None], on_start: Callable[[int, float], None] | None = None,
                 guard: SizeGuard | None = None) -> DownloadOutcome:
        """Every thread and process of the transfer has ended when this returns (a cancel then deletes
        the task folder safely)."""
        segmented = source.transport == "hls"
        meter = ProgressMeter(on_progress, basis="fragments" if segmented else "bytes",
                              total_bytes=None if segmented else source.estimated_bytes,
                              fragments_total=source.fragments if segmented else None)
        finished = finished_media(task_dir, source.identity_key)
        if finished is not None:
            meter.reuse(finished.stat().st_size, (source.fragments or 0) if segmented else 0)
            meter.emit(force=True)
            on_log([f"Dùng lại {finished.name} đã tải xong ở lần trước; kiểm tra lại trước khi đưa vào input."])
            return DownloadOutcome(True, final_path=finished)
        scope = Scope(control)
        log = LogBuffer(on_log)
        if guard is not None:
            guard.watch(on_trip=scope.abort)
        try:
            transfer = self.hls if segmented else self.files
            final = transfer.run(source, refresh, task_dir, scope, meter, log, guard, on_start)
            mark_finished(task_dir, source.identity_key, final)
        except Exception as error:  # noqa: BLE001 - mapped to an outcome; programming errors are re-raised
            return self._failed(error, control, guard)
        finally:
            if guard is not None:
                guard.stop()
            scope.close()
            log.flush()
            meter.emit(force=True)
        return DownloadOutcome(True, final_path=final)

    @staticmethod
    def _failed(error: Exception, control: ProcessControl, guard: SizeGuard | None) -> DownloadOutcome:
        if control.requested:
            return DownloadOutcome(False, *stop_reason(control))
        if guard is not None and guard.tripped:
            return DownloadOutcome(False, "TOO_LARGE", guard.message())
        if isinstance(error, (SourceError, HttpError)):
            return DownloadOutcome(False, error.code, error.message, resumable=resumable_error(error))
        if isinstance(error, OSError):  # the parts already written stay intact: these can be continued
            if error.errno == errno.ENOSPC or getattr(error, "winerror", None) == 112:
                return DownloadOutcome(False, "DISK_FULL", "Ổ đĩa hết chỗ khi ghi file tạm.", resumable=True)
            if isinstance(error, PermissionError):
                return DownloadOutcome(False, "FILE_HELD", "File tạm đang bị chương trình khác giữ (diệt virus, "
                                       "lập chỉ mục).", resumable=True)
            return DownloadOutcome(False, "DISK_ERROR", f"Lỗi ghi file tạm ({type(error).__name__}).")
        raise error
