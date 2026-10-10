"""Provider ids, exact host lists and the local provider config: the shared leaf of the source registry.

``download_sources`` (the registry), ``download_account_config`` (the source accounts) and the session vault
all need the id pattern and the page-safe message helpers; the registry also needs the account providers.
Keeping these here, with no import of either side, means neither imports the other through them.

``PUBLIC_PROVIDER_IDS`` are the ids of the anonymous providers of the code (``direct`` and ``SITE_PROVIDERS``
in ``download_sources``): a source account never takes one, because a task records the id of its
provider. A test keeps this set equal to the registry's own ids.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Sequence

from biliflow.download_links import LinkRejected, check_host, check_link
from biliflow.download_transfer import read_json

LOCAL_CONFIG = Path("config") / "download_providers.local.json"
PROVIDER_ID = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?")  # the id a provider class carries
PUBLIC_PROVIDER_IDS = frozenset({"direct", "player-hls", "article-mp4", "embedded-media"})
SHOWN_ITEMS = 5  # of a list in a message of the downloads page
# Of one config value, and of the reason it was skipped: every fixed reason of the host check fits whole (the
# longest, with its "xn--" hint, is 148 characters); only one that names a long host is cut.
SHOWN_VALUE_CHARS, SHOWN_REASON_CHARS = 60, 160
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*")  # what may come before "://" in a link
_AFTER_HOST = re.compile(r"[/?#\\]")  # where the host of a link ends


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
