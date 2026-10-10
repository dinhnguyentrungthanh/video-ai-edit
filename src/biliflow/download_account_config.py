"""The source accounts of the local, Git-ignored ``config/download_accounts.local.json``.

A source account is a film source the user signs in to themself (docs/SOURCE_ACCOUNTS_PLAN.md):

    {"sources": {"<source-id>": {
        "adapter": "ticket-files",              # an adapter the code has (ACCOUNT_ADAPTERS)
        "label": "Tên hiển thị",                # optional, the id by default
        "login_url": "https://portal.example/login",
        "hosts": {"portal": ["portal.example"], "tickets": ["tickets.example"], "files": ["files.example"]},
        "page_script": "https://ads.example/gate.js"}}}  # optional (M7 exception A)

``page_script``: the one outside script the source's film page needs before it can be used (its own gate
opens a modal over the page when that script cannot load). An exact https URL (default port, a path, no
query, fragment or user name) on a host that is none of the source's hosts: it is a role of its own, never a
portal, ticket or file host, never in ``all_hosts`` (so never matched as the source's link, saved in the vault
or fetched by ``LoginView.fetch``). Only a hidden run that may ask for a ticket lets exactly that script load
(download_account_page_script); a list-only run and the sign-in window do not. The script then runs in the
film page with that page's rights: it can read the page and its cookies that are not HttpOnly. The user chose
on 2026-10-10 to keep this exception (AGENTS.md, Network).

Only data is read: never a module, a command, a path or a script, and never a password, a cookie or a
token. A source with any wrong value is left out as a whole (an exact host list that is silently shorter
could send a sign-in elsewhere); keys starting with "_" are comments. The reasons are in Vietnamese for
the downloads page and show a link only as its scheme and host (``download_provider_config._shown``).

A host belongs to one source only, and never to a public provider of ``download_providers.local.json``:
both sides of a conflict between two sources are left out, and a source that shares a host with a
provider is left out while the provider keeps its host (the anonymous path is unchanged).

A source left out keeps its reason and the hosts that could be read (``AccountConfig.dropped``): a link of
one of them is refused when it is pasted, with that reason (M4), unless a public provider keeps the host.
"""
from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from biliflow.download_links import LinkRejected, check_host, check_link
from biliflow.download_provider_config import (  # the same message helpers as the provider config
    PROVIDER_ID,
    PUBLIC_PROVIDER_IDS,
    SHOWN_ITEMS,
    HostList,
    _listing,
    _shown,
    read_provider_config,
)

ACCOUNT_CONFIG = Path("config") / "download_accounts.local.json"
HOST_ROLES = ("portal", "tickets", "files")
MAX_LABEL_CHARS = 60
SESSION_CHECKS = frozenset({"ttl", "live"})
_SOURCE_KEYS = frozenset({"adapter", "label", "login_url", "hosts", "page_script"})


@dataclass(frozen=True)
class AdapterSpec:
    """An account adapter the code has. ``session_check``: "ttl" when the adapter cannot check a session
    (a new sign-in is asked for after the fallback TTL), "live" when its verifier decides."""
    id: str
    session_check: str

    def __post_init__(self) -> None:
        if not PROVIDER_ID.fullmatch(self.id) or self.session_check not in SESSION_CHECKS:
            raise ValueError(f"Bad account adapter {self.id!r}/{self.session_check!r}")


# "ticket-files": a film page lists files, a file gives a download ticket (download_account_sources, M3). It
# has no page reader and no sign-in verifier in the code, so a source configured with it is recognized (never
# sent to yt-dlp) but cannot sign in or read pages; the tests give it fixture ones.
# "release-forms" (M7): the same flow on pages whose files are season groups of release cards with one
# download form each (download_account_release_forms); its reader and verifier were written from a real
# source's pages, checked with the user. Read-only: an adapter is code, never added at run time.
ACCOUNT_ADAPTERS: Mapping[str, AdapterSpec] = MappingProxyType(
    {spec.id: spec for spec in (AdapterSpec("ticket-files", "ttl"), AdapterSpec("release-forms", "ttl"))})
# Ids of the public providers: a source id never takes one (a task records the id of its provider, and an
# account source's provider carries the source id).
RESERVED_IDS = PUBLIC_PROVIDER_IDS
# Names Windows keeps for devices: a source id names a folder of the session vault, so it never takes one.
WINDOWS_DEVICE_NAMES = frozenset({"con", "prn", "aux", "nul", *(f"com{digit}" for digit in range(10)),
                                  *(f"lpt{digit}" for digit in range(10))})


@dataclass(frozen=True)
class SourceAccount:
    """One configured source. ``login_url`` and ``hosts`` are never shown on a page or in a log."""
    id: str
    adapter: AdapterSpec
    label: str
    login_url: str = field(repr=False)
    hosts: Mapping[str, tuple[str, ...]] = field(repr=False)
    # The one outside script its film page needs (module docstring); never one of ``hosts``.
    page_script: str | None = field(default=None, repr=False)

    @property
    def all_hosts(self) -> frozenset[str]:
        return frozenset(host for names in self.hosts.values() for host in names)


@dataclass(frozen=True)
class DroppedSource:
    """A source left out of the config: why (the text of ``problems``) and the hosts its entry names that are
    bare host names (read loosely: the entry was refused, so a link of these hosts is refused at paste)."""
    id: str
    reason: str
    hosts: frozenset[str] = field(default=frozenset(), repr=False)


@dataclass(frozen=True)
class AccountConfig:
    sources: Mapping[str, SourceAccount]
    problems: tuple[str, ...] = ()
    dropped: tuple[DroppedSource, ...] = ()


class _Refused(Exception):
    """One source left out; ``reason`` is shown after its id."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _comment(key: object) -> bool:
    return isinstance(key, str) and key.startswith("_")


def _label(entry: Mapping[str, Any], source_id: str) -> str:
    raw = entry.get("label", source_id)
    if not isinstance(raw, str):
        raise _Refused('"label" phải là chuỗi.')
    label = raw.strip()
    if not label or len(label) > MAX_LABEL_CHARS:
        raise _Refused(f'"label" phải có 1–{MAX_LABEL_CHARS} ký tự.')
    if any(unicodedata.category(character) in ("Cc", "Cf", "Cs", "Zl", "Zp") for character in label):
        raise _Refused('"label" có ký tự điều khiển.')
    return label


def _hosts(entry: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    raw = entry.get("hosts")
    if not isinstance(raw, dict):
        raise _Refused('thiếu "hosts" dạng {"portal": [...], "tickets": [...], "files": [...]}.')
    unknown = [key for key in raw if key not in HOST_ROLES and not _comment(key)]
    if unknown:
        raise _Refused(f'"hosts" có khóa lạ {_listing(unknown)}; chỉ dùng portal, tickets, files.')
    result: dict[str, tuple[str, ...]] = {}
    for role in HOST_ROLES:
        names = raw.get(role, [])
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise _Refused(f'"hosts.{role}" phải là danh sách tên miền.')
        checked: list[str] = []
        for name in names:
            try:
                checked.append(check_host(name))
            except LinkRejected as error:
                raise _Refused(f'"hosts.{role}" có {_listing([name])} ({error.message.rstrip(".")}): mỗi host là '
                               "một tên miền trần, ví dụ portal.example.") from None
        result[role] = tuple(dict.fromkeys(checked))
    if not result["portal"]:
        raise _Refused('"hosts.portal" cần ít nhất một tên miền.')
    shared = set(result["files"]) & (set(result["portal"]) | set(result["tickets"]))
    if shared:
        raise _Refused(f'"hosts.files" trùng portal hay tickets ({_listing(sorted(shared))}): trình duyệt có phiên '
                       "không bao giờ được tới máy chủ file.")
    return result


def _login_url(entry: Mapping[str, Any], portal: Sequence[str]) -> str:
    raw = entry.get("login_url")
    if not isinstance(raw, str) or not raw:
        raise _Refused('thiếu "login_url".')
    if "#" in raw:
        raise _Refused(f'"login_url" {_listing([raw])} không được có phần "#…".')
    try:
        url, host, _port = check_link(raw)
    except LinkRejected as error:
        raise _Refused(f'"login_url" {_listing([raw])}: {error.message}') from None
    if urlsplit(url).scheme != "https":
        raise _Refused(f'"login_url" {_listing([raw])} phải dùng https.')
    if host not in portal:
        raise _Refused(f'"login_url" {_listing([raw])} phải nằm trên một host của "hosts.portal".')
    return url


def _page_script(entry: Mapping[str, Any], hosts: Mapping[str, tuple[str, ...]]) -> str | None:
    """The optional ``page_script`` (module docstring): an exact https URL on a host that is not the source's."""
    raw = entry.get("page_script")
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw or any(mark in raw for mark in "?#@"):
        raise _Refused('"page_script" phải là một URL https chính xác của một script (không có "?", "#" hay "@").')
    try:
        url, host, port = check_link(raw)
    except LinkRejected as error:
        raise _Refused(f'"page_script" {_listing([raw])}: {error.message}') from None
    if urlsplit(url).scheme != "https" or port != 443 or urlsplit(url).path in ("", "/"):
        raise _Refused(f'"page_script" {_listing([raw])} phải là https, cổng mặc định và có đường dẫn tới script.')
    if host in {name for names in hosts.values() for name in names}:
        raise _Refused(f'"page_script" {_listing([raw])} nằm trên một host của nguồn; script này là vai trò riêng, '
                       "không phải portal, tickets hay files.")
    return url


def _source(source_id: object, entry: object, adapters: Mapping[str, AdapterSpec]) -> SourceAccount:
    if not isinstance(source_id, str) or not PROVIDER_ID.fullmatch(source_id):
        raise _Refused("id chỉ dùng chữ thường, số và gạch nối (tối đa 40 ký tự).")
    if source_id in RESERVED_IDS:
        raise _Refused("id này là id của một bộ đọc nguồn công khai.")
    if source_id in WINDOWS_DEVICE_NAMES:
        raise _Refused("id này là tên thiết bị Windows giữ riêng (con, nul, com1…).")
    if not isinstance(entry, dict):
        raise _Refused("mục phải là object JSON.")
    unknown = [key for key in entry if key not in _SOURCE_KEYS and not _comment(key)]
    if unknown:
        raise _Refused(f"có khóa lạ {_listing(unknown)}; chỉ dùng adapter, label, login_url, hosts, page_script.")
    adapter_id = entry.get("adapter")
    if not isinstance(adapter_id, str) or adapter_id not in adapters:
        raise _Refused(f'"adapter" {_listing([adapter_id])} chưa có trong BiliFlow.')
    hosts = _hosts(entry)
    return SourceAccount(source_id, adapters[adapter_id], _label(entry, source_id),
                         _login_url(entry, hosts["portal"]), hosts, _page_script(entry, hosts))


def _loose_hosts(entry: object) -> frozenset[str]:
    """Every bare host name listed under "hosts" of a refused entry (wrong keys and values are skipped)."""
    raw = entry.get("hosts") if isinstance(entry, dict) else None
    names: set[str] = set()
    for value in raw.values() if isinstance(raw, dict) else ():
        for name in value if isinstance(value, list) else ():
            if not isinstance(name, str):
                continue
            try:
                names.add(check_host(name))
            except LinkRejected:
                continue
    return frozenset(names)


def _refused(source_id: object, reason: str) -> str:
    shown = source_id if isinstance(source_id, str) and PROVIDER_ID.fullmatch(source_id) else _shown(source_id)
    return f'Bỏ qua nguồn "{shown}": {reason}'


def _read(path: Path) -> tuple[Any, str | None]:
    try:
        present = path.is_file()
    except OSError:  # not even its attributes can be read
        present = True
    if not present:
        return None, None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig")), None  # PowerShell 5.1 writes a BOM
    except (OSError, ValueError, RecursionError):
        return None, "Không đọc được file JSON, nên chưa có nguồn tài khoản nào."


def _conflicts(accepted: Mapping[str, SourceAccount],
               provider_hosts: Mapping[str, HostList]) -> dict[str, str]:
    """The reason each source with a shared host is left out: a host of two sources, or of a provider."""
    owners: dict[str, list[str]] = {}
    for source in accepted.values():
        for host in source.all_hosts:
            owners.setdefault(host, []).append(source.id)
    public = {host for listed in provider_hosts.values() for host in listed.hosts}
    reasons: dict[str, str] = {}
    for source_id, source in accepted.items():
        shared = sorted(host for host in source.all_hosts if len(owners[host]) > 1)
        clash = sorted(source.all_hosts & public)
        if shared:
            reasons[source_id] = f"host {_listing(shared)} thuộc nhiều nguồn tài khoản."
        elif clash:
            reasons[source_id] = f"host {_listing(clash)} đã có trong config/download_providers.local.json."
    return reasons


def read_account_config(root: Path, *, adapters: Mapping[str, AdapterSpec] = ACCOUNT_ADAPTERS,
                        provider_hosts: Mapping[str, HostList] | None = None) -> AccountConfig:
    """The valid sources of ``<root>/config/download_accounts.local.json`` and why the others were left out
    (no file: no source and no problem). ``provider_hosts`` defaults to the local provider config."""
    data, failure = _read(root / ACCOUNT_CONFIG)
    if failure:
        return AccountConfig({}, (failure,))
    if data is None:
        return AccountConfig({})
    sources = data.get("sources") if isinstance(data, dict) else None
    if not isinstance(sources, dict):
        return AccountConfig({}, ('Cần JSON dạng {"sources": {"<id>": {...}}}, nên chưa có nguồn tài khoản nào.',))
    if provider_hosts is None:
        provider_hosts = read_provider_config(root)[0]
    problems: list[str] = []
    dropped: list[DroppedSource] = []
    accepted: dict[str, SourceAccount] = {}
    for source_id, entry in sources.items():
        if _comment(source_id):
            continue
        try:
            accepted[source_id] = _source(source_id, entry, adapters)
        except _Refused as refused:
            problems.append(_refused(source_id, refused.reason))
            dropped.append(DroppedSource(str(source_id)[:40], problems[-1], _loose_hosts(entry)))
    conflicts = _conflicts(accepted, provider_hosts)
    for source_id, reason in conflicts.items():
        problems.append(_refused(source_id, reason))
        dropped.append(DroppedSource(source_id, problems[-1], accepted[source_id].all_hosts))
    valid = {source_id: source for source_id, source in accepted.items() if source_id not in conflicts}
    return AccountConfig(valid, tuple(problems), tuple(dropped))


def describe_account_problems(problems: Sequence[str]) -> str | None:
    """One line for the downloads page about what the account config left out; None when nothing was."""
    if not problems:
        return None
    more = f" (và {len(problems) - SHOWN_ITEMS} lỗi khác)" if len(problems) > SHOWN_ITEMS else ""
    return f"{ACCOUNT_CONFIG.as_posix()}: " + " ".join(problems[:SHOWN_ITEMS]) + more
