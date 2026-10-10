"""The film list of an account source (M3): film, seasons, episodes and their files (variants), in order,
with stable identities and the selection types M4 and M5 build on. No browser and no network here.

A page reader (download_account_pages) hands over what one page shows as ``FilmPage``s; ``ListingBuilder``
checks and merges them into a ``Listing``:

- Only entries the reader found in the page's download area (``role == "file"``) are files. Trailers, adverts
  and anything else are left out by that structural role (counted in ``skipped``), never by a guess on a
  title, and nothing here ever takes "the first video of the page".
- Identity: source + film id + episode key + variant id (``ID``: letters, digits and ``._:-``). The film and
  variant ids are the source's own. The episode key is the source's episode id when it has one; a source
  that shows no episode id but labels its seasons and episodes with numbers in its page structure gets a
  key normalized from those two numbers (``s2:e2``, by its reader), never from a file name, a title, the
  order of the page or a file id. An entry without them cannot be chosen and makes the list incomplete; no
  id is ever made up from a title, a size or a link. A variant (quality, audio) is a file of its episode,
  never an episode: two files with the same episode key are two variants of one episode. The key says
  nothing about the file's version: a part is continued only for the same version (download_media_file).
- Order: seasons by number, episodes by number within a season (2 before 10); items without a number keep
  their place in the source order, and numbered items fill the numbered places in number order. A special
  episode goes where the source places it (``after`` episode N of its season; 0 = before the first);
  without such a place it goes, in source order, into one "Đặc biệt" group after every season. Nothing
  guesses a number or a place.
- ``complete`` is False, with a reason, whenever the reader could not show that every page was read: a
  limit of pages, files or time, a page that failed, a page link that was not followed, a read that ended at
  the chosen file, more items without a page link to follow, ids missing or one id with two different
  contents. A single page is never called the whole series, and a selection of "all" on such a list says it
  holds only the episodes seen (``SelectionPlan.note``).
- ``public()`` holds ids, labels, numbers, order and sizes only: no page link, ticket, signed URL, cookie or
  session state. The private part (where each file's ticket is asked for) stays in ``navigation``.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
MAX_LABEL_CHARS = 200
MAX_NUMBER = 100_000
MAX_EPISODES = 1000
MAX_FILES = 5000
MAX_TRIGGER_CHARS = 512  # a reader's way to ask for one file's ticket (a selector it built from checked ids)
MAX_REQUEST_CHARS = 2048  # the URL that ticket control asks for (``RawEntry.request``)
REQUEST_METHODS = frozenset({"GET", "POST"})  # how it asks (``RawEntry.request_method``)
# Where and how one file's ticket is asked for (private): page URL, trigger, reveal or None, request or None and
# its method or None.
TicketRoute = tuple[str, str, str | None, str | None, str | None]
MODES = ("all", "pick")
SPECIALS_KEY = ":specials"  # the group of specials without a place (never a source id: ids start alnum)
SPECIALS_LABEL = "Đặc biệt"
REASONS = {
    "PAGE_LIMIT": "Danh sách có nhiều trang hơn giới hạn đọc.",
    "ITEM_LIMIT": "Danh sách có nhiều tập hoặc file hơn giới hạn đọc.",
    "TIME_LIMIT": "Hết thời gian đọc danh sách.",
    "PAGE_FAILED": "Một trang của danh sách không mở được.",
    "PAGE_SIGNED_OUT": "Một trang của danh sách chuyển sang trang đăng nhập (trang đầu vẫn đăng nhập).",
    "PAGE_NOT_FOLLOWED": "Có liên kết trang của danh sách không được đọc (khác host của trang đã dán, không phải "
                         "https hoặc là trang đăng nhập).",
    "PARTIAL_READ": "Chỉ đọc danh sách đến trang có file đã chọn.",
    "MORE_NOT_LINKED": "Trang còn tập khác nhưng không có liên kết trang để đọc tiếp.",
    "OTHER_FILM": "Một trang phân trang thuộc phim khác.",
    "UNREADABLE_ITEMS": "Có mục tải không có mã ổn định nên không chọn được.",
    "CONFLICTING_ITEMS": "Có mục trùng mã mà khác nội dung.",
}


def clean_label(value: object, limit: int = MAX_LABEL_CHARS) -> str:
    """A label of the page as text: control characters dropped, white space collapsed, cut short."""
    text = "".join(" " if unicodedata.category(character) in ("Cc", "Cf", "Zl", "Zp") else character
                   for character in str(value if value is not None else ""))
    return " ".join(text.split())[:limit]


def checked_id(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    text = value.strip() if isinstance(value, str) else str(value) if isinstance(value, int) else ""
    return text if ID.fullmatch(text) else None


def checked_number(value: object) -> int | None:
    """A whole number the source gives (``2``, ``"02"``); None for anything else (never parsed from a title)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        number = int(value.strip())
    else:
        return None
    return number if 0 <= number <= MAX_NUMBER else None


def checked_size(value: object) -> int | None:
    """Bytes the page gives as a whole number; None for anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        value = int(value.strip())
    return value if isinstance(value, int) and value > 0 else None


@dataclass(frozen=True)
class RawEntry:
    """One file of a page as the reader saw it (values of the page, checked by ``ListingBuilder``).
    ``role``: "file" when it is in the page's download area; anything else (trailer, ad…) is left out.
    ``trigger``: how the reader asks for this file's ticket on that page (private, never public).
    ``reveal``: when the trigger sits in a closed ``<details>`` (a season the page shows folded), the selector
    of exactly that ``details`` element, which the runtime opens before the click (download_account_pages);
    None when the trigger is already shown. A selector, never code: the reader gets no other action."""
    role: str
    episode: object = None
    variant: object = None
    season: object = None
    season_number: object = None
    season_label: object = None
    episode_number: object = None
    episode_label: object = None
    special: bool = False
    after: object = None
    variant_label: object = None
    quality: object = None
    audio: object = None
    size: object = None
    version: object = None
    trigger: str | None = field(default=None, repr=False)
    reveal: str | None = field(default=None, repr=False)
    # The https URL the trigger asks the source for (a form's action, a link's target), as the page names it:
    # a ticket page that shows no file id is this file's only when it answers that request of the run's own
    # click (download_account_pages). None when the reader cannot tell it.
    request: str | None = field(default=None, repr=False)
    # How the trigger sends ``request`` ("POST" for a form posted, "GET" for a link): only a first request of that
    # method answers the click. Given with ``request`` and only with it.
    request_method: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class TicketPage:
    """What a ticket page shows: the episode and file ids it is for (the ids of the film list), its state
    and, when ready, the download link."""
    episode: str | None
    file: str | None
    state: str
    link: str | None = field(default=None, repr=False)


TICKET_STATES = frozenset({"waiting", "ready", "expired", "gone", "login", "challenge", "error"})


@dataclass(frozen=True)
class FilmPage:
    """What one page of the film shows. ``links``: other pages of the same list (pagination, seasons), as
    found on the page (private). ``more_unlinked``: the page shows more items without a link to them."""
    film: object
    title: object
    kind: str | None
    entries: tuple[RawEntry, ...]
    links: tuple[str, ...] = field(default=(), repr=False)
    more_unlinked: bool = False


@dataclass(frozen=True)
class Variant:
    id: str
    label: str
    quality: str | None = None
    audio: str | None = None
    size: int | None = None  # bytes, when the page gives a number
    version: str | None = None  # the source's own version mark of the file, when it gives one

    @property
    def kind(self) -> str:
        """What matches "the same variant" in every episode: quality and audio, else the label (case-folded)."""
        parts = [part.casefold() for part in (self.quality, self.audio) if part]
        return "|".join(parts) if parts else self.label.casefold()

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "kind": self.kind, "quality": self.quality,
                "audio": self.audio, "size": self.size}


@dataclass(frozen=True)
class Season:
    key: str  # the source's season id; "" when the source shows no seasons; SPECIALS_KEY for placeless specials
    number: int | None
    label: str
    special: bool = False


@dataclass(frozen=True)
class Episode:
    key: str
    season: str
    number: int | None
    label: str
    position: int  # where it was first seen, across the pages (1-based)
    special: bool
    after: int | None
    variants: tuple[Variant, ...]

    def variant(self, variant_id: str) -> Variant | None:
        return next((item for item in self.variants if item.id == variant_id), None)


@dataclass(frozen=True)
class FileSelection:
    """One file to download: the stable ids, nothing else (no link, no ticket)."""
    source: str
    film: str
    episode: str
    variant: str

    @property
    def key(self) -> str:
        """A short key of the four ids (M4 blocks a second task of the same file with it)."""
        text = json.dumps([self.source, self.film, self.episode, self.variant], separators=(",", ":"))
        return "acct-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:40]

    def public(self) -> dict[str, str]:
        return {"source": self.source, "film": self.film, "episode": self.episode, "variant": self.variant}

    @classmethod
    def from_mapping(cls, value: object) -> FileSelection | None:
        """The ids of a stored selection or identity; None when ``value`` is not one."""
        if not isinstance(value, Mapping):
            return None
        source, film, episode, variant = (checked_id(value.get(name))
                                          for name in ("source", "film", "episode", "variant"))
        if source is None or film is None or episode is None or variant is None:
            return None
        return cls(source, film, episode, variant)


@dataclass(frozen=True)
class Listing:
    source: str
    film: str
    title: str
    kind: str  # "film" (one episode) or "series"
    seasons: tuple[Season, ...]  # in order, the specials group last when there is one
    episodes: tuple[Episode, ...]  # in order
    complete: bool
    reasons: tuple[str, ...] = ()
    skipped: Mapping[str, int] = field(default_factory=dict)
    pages: int = 0
    # (episode, variant) -> (page URL, trigger, reveal or None, request or None, its method or None): where and
    # how that file's ticket is asked for.
    navigation: Mapping[tuple[str, str], TicketRoute] = field(default_factory=dict, repr=False, compare=False)

    def episode(self, key: str) -> Episode | None:
        return next((item for item in self.episodes if item.key == key), None)

    def selection(self, episode: Episode, variant: Variant) -> FileSelection:
        return FileSelection(self.source, self.film, episode.key, variant.id)

    def find(self, wanted: FileSelection) -> tuple[Episode, Variant] | None:
        if (wanted.source, wanted.film) != (self.source, self.film):
            return None
        episode = self.episode(wanted.episode)
        variant = episode.variant(wanted.variant) if episode else None
        return (episode, variant) if episode and variant else None

    def public(self) -> dict[str, Any]:
        """The preview M4 stores and M5 shows: ids, labels, numbers, order and sizes only."""
        groups = []
        for season in self.seasons:
            members = [episode for episode in self.episodes if episode.season == season.key]
            groups.append({"key": season.key, "number": season.number, "label": season.label,
                           "special": season.special, "episodes": [
                               {"key": episode.key, "number": episode.number, "label": episode.label,
                                "special": episode.special, "order": self.episodes.index(episode) + 1,
                                "variants": [variant.public() for variant in episode.variants]}
                               for episode in members]})
        body = {"source": self.source, "film": self.film, "title": self.title, "kind": self.kind, "groups": groups}
        kinds: dict[str, dict[str, Any]] = {}
        for episode in self.episodes:
            for variant in episode.variants:
                entry = kinds.setdefault(variant.kind, {"kind": variant.kind, "label": variant.label, "episodes": 0})
                entry["episodes"] += 1
        return {**body, "complete": self.complete, "reasons": list(self.reasons),
                "message": REASONS.get(self.reasons[0]) if self.reasons else None,
                "episode_count": len(self.episodes), "file_count": sum(len(item.variants) for item in self.episodes),
                "variant_kinds": list(kinds.values()), "skipped": dict(self.skipped), "pages": self.pages,
                "fingerprint": hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False)
                                              .encode("utf-8")).hexdigest()}


class ListingBuilder:
    """Merges the pages of one film (see the module docstring)."""

    def __init__(self, source: str, *, max_episodes: int = MAX_EPISODES, max_files: int = MAX_FILES):
        self.source = source
        self.max_episodes, self.max_files = max_episodes, max_files
        self.film: str | None = None
        self.title = ""
        self.kind: str | None = None
        self.pages = 0
        self.reasons: list[str] = []
        self.skipped: dict[str, int] = {}
        self._seasons: dict[str, Season] = {}
        self._episodes: dict[str, dict[str, Any]] = {}
        self._files = 0
        self.navigation: dict[tuple[str, str], TicketRoute] = {}

    def note(self, reason: str) -> None:
        if reason not in self.reasons:
            self.reasons.append(reason)

    def add(self, page: FilmPage, page_url: str) -> bool:
        """Merge one page; False (and OTHER_FILM) for a page of another film, which is not merged."""
        film = checked_id(page.film)
        if film is None:
            raise ValueError("A film page needs the source's film id")
        if self.film is None:
            self.film, self.title = film, clean_label(page.title) or film
            self.kind = page.kind if page.kind in ("film", "series") else None
        elif film != self.film:
            self.note("OTHER_FILM")
            return False
        self.pages += 1
        if page.more_unlinked:
            self.note("MORE_NOT_LINKED")
        for entry in page.entries:
            self._add_entry(entry, page_url)
        return True

    def _skip(self, why: str) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + 1

    def _season(self, entry: RawEntry, season_id: str) -> None:
        number = checked_number(entry.season_number)
        label = clean_label(entry.season_label) or (f"Mùa {number}" if number is not None else "")
        known = self._seasons.setdefault(season_id, Season(season_id, number, label))
        if known.number != number:
            self.note("CONFLICTING_ITEMS")

    def _add_entry(self, entry: RawEntry, page_url: str) -> None:
        if entry.role != "file":
            self._skip(clean_label(entry.role, 20) or "other")
            return
        episode_id, variant_id = checked_id(entry.episode), checked_id(entry.variant)
        season_id = checked_id(entry.season) if entry.season not in (None, "") else ""
        trigger, reveal, request = entry.trigger, entry.reveal, entry.request
        method = entry.request_method
        if (episode_id is None or variant_id is None or season_id is None or not isinstance(trigger, str)
                or not trigger or len(trigger) > MAX_TRIGGER_CHARS
                or (reveal is not None and (not isinstance(reveal, str) or not reveal
                                            or len(reveal) > MAX_TRIGGER_CHARS))
                or (request is not None and (not isinstance(request, str) or not request.startswith("https://")
                                             or len(request) > MAX_REQUEST_CHARS))
                or (method is not None and (not isinstance(method, str) or method not in REQUEST_METHODS))
                or (request is None) != (method is None)):
            self._skip("unreadable")
            self.note("UNREADABLE_ITEMS")
            return
        self._season(entry, season_id)
        number = checked_number(entry.episode_number)
        shown = " · ".join(text for text in (clean_label(entry.quality, 40), clean_label(entry.audio, 40)) if text)
        variant = Variant(variant_id, clean_label(entry.variant_label) or shown or variant_id,
                          clean_label(entry.quality, 40) or None, clean_label(entry.audio, 40) or None,
                          checked_size(entry.size), checked_id(entry.version))
        episode = {"key": episode_id, "season": season_id, "number": number,
                   "label": clean_label(entry.episode_label) or (f"Tập {number}" if number is not None else episode_id),
                   "special": bool(entry.special), "after": checked_number(entry.after)}
        known = self._episodes.get(episode_id)
        if known is None:
            if len(self._episodes) >= self.max_episodes:
                self.note("ITEM_LIMIT")
                return
            known = self._episodes[episode_id] = {**episode, "position": len(self._episodes) + 1, "variants": {}}
        elif any(known[name] != episode[name] for name in episode):
            self.note("CONFLICTING_ITEMS")
            return
        same = known["variants"].get(variant_id)
        if same is not None:
            if same != variant:
                self.note("CONFLICTING_ITEMS")
            else:
                self._skip("duplicate")
            return
        if self._files >= self.max_files:
            self.note("ITEM_LIMIT")
            return
        known["variants"][variant_id] = variant
        self._files += 1
        self.navigation[(episode_id, variant_id)] = (page_url, trigger, reveal, request, method)

    def build(self) -> Listing:
        if self.film is None:
            raise ValueError("No page of the film was read")
        episodes = [Episode(item["key"], item["season"], item["number"], item["label"], item["position"],
                            item["special"], item["after"], tuple(item["variants"].values()))
                    for item in self._episodes.values() if item["variants"]]
        seasons, ordered = order_episodes(list(self._seasons.values()), episodes)
        single = len(ordered) == 1 and not any(season.key for season in seasons)
        kind = "film" if self.kind != "series" and single else "series"
        return Listing(self.source, self.film, self.title, kind, tuple(seasons), tuple(ordered),
                       not self.reasons, tuple(self.reasons), dict(self.skipped), self.pages, dict(self.navigation))


def _numbered_in_place(items: Sequence[Any], number: Any) -> list[Any]:
    """Source order, except that the numbered items fill the places of numbered items in number order (ties
    keep their source order); items without a number keep their own places."""
    numbered = iter(sorted((item for item in items if number(item) is not None), key=number))
    return [next(numbered) if number(item) is not None else item for item in items]


def order_episodes(seasons: Iterable[Season], episodes: Iterable[Episode]) -> tuple[list[Season], list[Episode]]:
    """The seasons and episodes in the order of the module docstring; the placeless specials group last."""
    by_position = sorted(episodes, key=lambda item: item.position)
    season_list = _numbered_in_place(list(seasons), lambda item: item.number)
    ordered: list[Episode] = []
    trailing: list[Episode] = []
    for season in season_list:
        members = [item for item in by_position if item.season == season.key]
        regular = _numbered_in_place([item for item in members if not item.special], lambda item: item.number)
        numbers = {item.number for item in regular if item.number is not None}
        placed = [item for item in members if item.special and item.after is not None
                  and (item.after == 0 or item.after in numbers)]
        trailing += [item for item in members if item.special and item not in placed]
        result = [item for item in placed if item.after == 0]
        for item in regular:
            result.append(item)
            if item.number is not None:
                result += [special for special in placed if special.after == item.number and special not in result]
        ordered += result
    used = [season for season in season_list if any(item.season == season.key for item in ordered)]
    if trailing:
        trailing.sort(key=lambda item: item.position)
        ordered += [Episode(item.key, SPECIALS_KEY, item.number, item.label, item.position, True, None, item.variants)
                    for item in trailing]
        used.append(Season(SPECIALS_KEY, None, SPECIALS_LABEL, True))
    return used, ordered


# Selection (M4 stores it, M5 shows it) --------------------------------------------------------------

@dataclass(frozen=True)
class SelectionPlan:
    """What "Tải N tập" would start: one file per chosen episode, in list order. ``missing``: chosen episodes
    without the wanted variant (shown, never replaced by another one); ``ambiguous``: episodes where the wanted
    kind matches two files (shown, none taken). ``complete``: the list behind it was read whole."""
    mode: str
    items: tuple[FileSelection, ...]
    missing: tuple[str, ...]
    ambiguous: tuple[str, ...]
    complete: bool
    reasons: tuple[str, ...] = ()

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def confirm_label(self) -> str:
        return f"Tải {self.count} tập" if self.complete else f"Tải {self.count} tập đã thấy"

    @property
    def note(self) -> str:
        """What the plan covers when the list was not read whole (empty when it was): only the episodes seen."""
        if self.complete:
            return ""
        why = REASONS.get(self.reasons[0], "").rstrip(".") if self.reasons else ""
        reason = f" ({why})" if why else ""
        return (f"Danh sách chưa đầy đủ{reason}: chỉ gồm {self.count} tập trong phần đã thấy, "
                "có thể còn tập chưa đọc được.")


def plan_selection(listing: Listing, *, mode: str, episodes: Sequence[str] = (), variant_kind: str | None = None,
                   variants: Mapping[str, str] | None = None) -> SelectionPlan:
    """The files of an explicit choice: ``mode`` "all" (every episode the list has) or "pick" (``episodes``,
    keys of the list). The variant: one ``variant_kind`` for every episode, or ``variants`` per episode; none
    needed only when each chosen episode has a single file. Nothing is chosen by default (ValueError)."""
    if mode not in MODES:
        raise ValueError("Choose a mode: all or pick")
    if mode == "all":
        chosen = list(listing.episodes)
    else:
        unknown = [key for key in episodes if listing.episode(key) is None]
        if not episodes or unknown:
            raise ValueError("Pick at least one episode of this list")
        wanted = set(episodes)
        chosen = [episode for episode in listing.episodes if episode.key in wanted]
    if variant_kind is None and variants is None and any(len(item.variants) != 1 for item in chosen):
        raise ValueError("Choose a variant")
    items, missing, ambiguous = [], [], []
    for episode in chosen:
        if variants is not None:
            matches = [item for item in episode.variants if item.id == variants.get(episode.key)]
        elif variant_kind is not None:
            matches = [item for item in episode.variants if item.kind == variant_kind]
        else:
            matches = list(episode.variants)
        if not matches:
            missing.append(episode.key)
        elif len(matches) > 1:
            ambiguous.append(episode.key)
        else:
            items.append(listing.selection(episode, matches[0]))
    return SelectionPlan(mode, tuple(items), tuple(missing), tuple(ambiguous), listing.complete, listing.reasons)


def film_choices(listing: Listing) -> list[dict[str, str]]:
    """A film's files as the choice of the single-film path (``SourceNeedsChoice``): selection key and label.
    A list that was not read whole says so in every label (the user chooses knowing it)."""
    if listing.kind != "film":
        raise ValueError("Only a film has one list of files")
    episode = listing.episodes[0]
    note = "" if listing.complete else " · danh sách bản chưa đủ"
    return [{"selection": listing.selection(episode, variant).key, "title": variant.label + note}
            for variant in episode.variants]
