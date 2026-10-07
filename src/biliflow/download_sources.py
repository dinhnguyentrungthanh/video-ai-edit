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
"""
from __future__ import annotations

import errno
import json
import re
from pathlib import Path
from typing import Callable, Iterable, Mapping, Protocol, Sequence
from urllib.parse import urlsplit

from biliflow.download_hls import SEGMENT_WORKERS, HlsTransfer, resolve_hls
from biliflow.download_http import HttpError, SafeHttp, Scope
from biliflow.download_links import LinkRejected, check_host, check_link
from biliflow.download_media_file import FileTransfer, PlaylistLink, resolve_file
from biliflow.download_runner import DownloadOutcome, Progress, ProcessControl, SizeGuard, stop_reason
from biliflow.download_source_types import ResolveContext, ResolvedSource, SourceError
from biliflow.download_transfer import LogBuffer, ProgressMeter, finished_media, mark_finished, read_json

MEDIA_SUFFIXES = (".mp4", ".m4v", ".mov", ".mkv", ".webm", ".ts")
PLAYLIST_SUFFIX = ".m3u8"
LOCAL_CONFIG = Path("config") / "download_providers.local.json"
PROVIDER_ID = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?")  # the id a provider class carries
SHOWN_ITEMS = 5  # of a list in a message of the downloads page
# Of one config value, and of the reason it was skipped: every fixed reason of the host check fits whole (the
# longest, with its "xn--" hint, is 148 characters); only one that names a long host is cut.
SHOWN_VALUE_CHARS, SHOWN_REASON_CHARS = 60, 160
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*")  # what may come before "://" in a link
_AFTER_HOST = re.compile(r"[/?#\\]")  # where the host of a link ends
# After the retries, these leave a good part on disk: the task ends INTERRUPTED and "Tiếp tục" continues it.
RESUMABLE_CODES = frozenset({"NETWORK", "SERVER_BUSY", "DNS_FAILED"})


class SourceProvider(Protocol):
    id: str
    label: str

    def claims(self, url: str) -> bool:
        """True when this provider handles ``url``; decided from the link alone (no network)."""

    def resolve(self, url: str, ctx: ResolveContext) -> ResolvedSource:
        """The media of ``url``; raises SourceDeclined (back to yt-dlp) or SourceError (FAILED)."""


class HostList:
    """Exact host names, checked and normalized like the host of a pasted link (lower case, IDNA, no trailing
    dot); an entry that is not a bare host name (a wildcard, a port, a URL, an IP address) is skipped. A link
    matches its own host only, never a suffix, a substring or a subdomain."""

    def __init__(self, hosts: Iterable[str]):
        names, skipped = set(), []
        for host in hosts:
            try:
                names.add(check_host(host))
            except LinkRejected as error:
                skipped.append((host, error.message))
        self.hosts = frozenset(names)
        self.skipped = tuple(skipped)  # (entry, why) of each entry that is not a bare host name

    def matches(self, url: str) -> bool:
        """The host is read by the link check itself (``check_link``, which uses ``urlsplit``), so a link it
        refuses, such as one with an account or a port, matches nothing, and the host that matched is the
        host the download connects to."""
        try:
            _url, host, _port = check_link(url)
        except LinkRejected:
            return False
        return host in self.hosts


def _without_link_parts(entry: str) -> str:
    """What of a link stays on the page (an account, a path or a query may hold a token):
    ``https://user:pw@video.example/watch?sig=1`` gives ``https://…@video.example/…``."""
    scheme, sep, rest = entry.partition("://")
    if not sep or not _SCHEME.fullmatch(scheme):  # "video.example/?next=https://…" has no scheme
        scheme, sep, rest = "", "", entry
    authority = _AFTER_HOST.split(rest, maxsplit=1)[0]
    tail = rest[len(authority):]
    shown = scheme + sep + ("…@" if "@" in authority else "") + authority.rpartition("@")[2]
    return shown + (tail if tail in ("", "/") else "/…")


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 3] + "…"


def _shown(value: object) -> str:
    """A config value as the downloads page may show it: JSON text cut short, a link without its account, path
    or query, a list or an object only named, and a lone surrogate escaped (the page's JSON is UTF-8)."""
    if isinstance(value, (list, dict)):
        return "[…]" if isinstance(value, list) else "{…}"
    if isinstance(value, str):
        value = _without_link_parts(value)
    text = json.dumps(value, ensure_ascii=False).encode("utf-8", "backslashreplace").decode("utf-8")
    return _cut(text, SHOWN_VALUE_CHARS)


def _listing(values: Sequence[object], limit: int = SHOWN_ITEMS) -> str:
    """The first few config values as the downloads page may show them (``_shown``)."""
    more = f" và {len(values) - limit} mục khác" if len(values) > limit else ""
    return ", ".join(_shown(value) for value in values[:limit]) + more


def _skipped(entries: Sequence[tuple[object, str]]) -> str:
    """The first few skipped entries, grouped by why: '"a", "b" (why); "c" (why) và 2 mục khác'."""
    groups: dict[str, list[object]] = {}
    for entry, why in entries[:SHOWN_ITEMS]:
        groups.setdefault(why, []).append(entry)
    more = f" và {len(entries) - SHOWN_ITEMS} mục khác" if len(entries) > SHOWN_ITEMS else ""
    return "; ".join(f"{_listing(group)} ({_cut(why.rstrip('.'), SHOWN_REASON_CHARS)})"
                     for why, group in groups.items()) + more


def read_provider_config(root: Path) -> tuple[dict[str, HostList], tuple[str, ...]]:
    """The host lists of ``{"providers": {"<id>": {"hosts": [...]}}}`` in the local, Git-ignored config ({}
    without the file), and what of it is ignored, in Vietnamese for the downloads page.

    Only the host lists are read: an entry never names code to load or a command to run (a provider's class
    comes from SITE_PROVIDERS), its other keys are ignored, and so is an id that no provider could carry."""
    path = root / LOCAL_CONFIG
    try:
        present = path.is_file()
    except OSError:  # not even its attributes can be read: reported below as a file that cannot be read
        present = True
    if not present:
        return {}, ()
    try:
        providers = read_json(path).get("providers")
    except RecursionError:  # absurdly nested JSON
        providers = None
    if not isinstance(providers, dict):
        return {}, ('Không đọc được file (cần JSON dạng {"providers": {"<id>": {"hosts": [...]}}}), '
                    "nên chưa bật bộ đọc nguồn nào.",)
    result: dict[str, HostList] = {}
    problems: list[str] = []
    for provider_id, entry in providers.items():
        hosts = entry.get("hosts") if isinstance(entry, dict) else None
        if not PROVIDER_ID.fullmatch(provider_id):
            problems.append(f"Bỏ qua id {_listing([provider_id])}: chỉ dùng chữ thường, số và gạch nối.")
        elif not isinstance(hosts, list):
            problems.append(f'Bỏ qua "{provider_id}": thiếu danh sách "hosts".')
        else:
            listed = HostList(host for host in hosts if isinstance(host, str))
            result[provider_id] = listed
            skipped = [*listed.skipped, *((host, "không phải chuỗi") for host in hosts if not isinstance(host, str))]
            if skipped:
                problems.append(f'"{provider_id}" bỏ qua {_skipped(skipped)}: mỗi host là một tên miền trần, '
                                "ví dụ video.example.")
    return result, tuple(problems)


def describe_config_problems(problems: Sequence[str]) -> str | None:
    """One line for the downloads page about what the local config ignored; None when nothing was."""
    if not problems:
        return None
    more = f" (và {len(problems) - SHOWN_ITEMS} lỗi khác)" if len(problems) > SHOWN_ITEMS else ""
    return f"{LOCAL_CONFIG.as_posix()}: " + " ".join(problems[:SHOWN_ITEMS]) + more


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


class SourceRegistry:
    def __init__(self, providers: Sequence[SourceProvider], *, site_hosts: Mapping[str, HostList] | None = None,
                 recognized: Mapping[str, HostList] | None = None, config_problems: Sequence[str] = ()):
        ids = [provider.id for provider in providers]
        if len(set(ids)) != len(ids):
            raise ValueError(f"Duplicate provider ids: {ids}")
        self._providers = tuple(providers)
        self.config_problems = tuple(config_problems)  # what the local config ignored (read_provider_config)
        # The exact hosts of each site provider: it is only asked about links of those hosts.
        self._site_hosts = {key: hosts for key, hosts in (site_hosts or {}).items() if key in ids}
        # Hosts the local config lists for an id that none of these providers has.
        self._recognized = {key: hosts for key, hosts in (recognized or {}).items() if key not in ids}

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

    def recognized_without_provider(self, url: str) -> str | None:
        """The id the local config lists this link's host under when the code has no provider of that id:
        BiliFlow recognizes the host but has no reader for it (the link still goes to yt-dlp)."""
        return next((key for key, hosts in self._recognized.items() if hosts.matches(url)), None)


def default_registry(root: Path, *, site_providers: Sequence[type] = SITE_PROVIDERS) -> SourceRegistry:
    """Site providers that have hosts in the local config (exact hosts first), then the direct links; the
    hosts of every other configured id are only recognized (``recognized_without_provider``)."""
    for factory in site_providers:
        if not isinstance(factory.id, str) or not PROVIDER_ID.fullmatch(factory.id):
            raise ValueError(f"Provider id {factory.id!r} does not match {PROVIDER_ID.pattern}")
    hosts, problems = read_provider_config(root)
    sites = [factory(hosts[factory.id]) for factory in site_providers if factory.id in hosts]
    return SourceRegistry([*sites, DirectMediaProvider()], site_hosts={site.id: hosts[site.id] for site in sites},
                          recognized=hosts, config_problems=problems)


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
        if isinstance(error, SourceError):
            return DownloadOutcome(False, error.code, error.message)
        if isinstance(error, HttpError):
            return DownloadOutcome(False, error.code, error.message, resumable=error.code in RESUMABLE_CODES)
        if isinstance(error, OSError):  # the parts already written stay intact: these can be continued
            if error.errno == errno.ENOSPC or getattr(error, "winerror", None) == 112:
                return DownloadOutcome(False, "DISK_FULL", "Ổ đĩa hết chỗ khi ghi file tạm.", resumable=True)
            if isinstance(error, PermissionError):
                return DownloadOutcome(False, "FILE_HELD", "File tạm đang bị chương trình khác giữ (diệt virus, "
                                       "lập chỉ mục).", resumable=True)
            return DownloadOutcome(False, "DISK_ERROR", f"Lỗi ghi file tạm ({type(error).__name__}).")
        raise error
