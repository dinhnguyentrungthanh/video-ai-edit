"""Scrolling site text and web addresses read without their dot, at review-build time.

A source site scrolls a line ("<site>.COM chia sẻ dữ liệu phim …") through the top
band of the frame. The text scan reads the frame every few seconds, so the line
comes out as several short tracks: an entry piece at the right edge, wide pieces
and exit pieces at the left edge, often with the domain's dot lost ("…COM",
"… COM"). Read one by one, the pieces looked like scene text or low-ad text and
the blur started at the second reading (jobs 107, 108, 140; 2026-10-15).

``promote_scrolling_text`` joins the pieces of one crossing into one track: the
whole band the line scrolls through, from one read interval before the first
reading (the line was already on screen then) to one read interval after the
last (``end_seconds`` of the text scan), one interval more on a side where the
reading at the frame's edge is missing; suggested BLUR. ``promote_web_address_tracks``
sends every other track with a web address to the main list with a BLUR
suggestion. Both only regroup and suggest; the reviewer decides.

Built from an existing text scan, so old jobs need no rescan. Only
``review_workflow`` imports this module: it is in no scan stage's cache key.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

# --- Web addresses --------------------------------------------------------------

_DOTTED_TLDS = (
    "com|net|org|vn|tv|me|io|xyz|cc|co|info|top|club|link|live|online|site|biz|asia|pro|"
    "app|fun|vip|bet|win|us|to|ly|gg"
)
# A domain whose dot OCR lost is only trusted with its name in capitals and for
# these endings: "nấu cơm", "xem tv", "SMART TV" or "Saigon, VN" stay ordinary text.
_DOTLESS_TLDS = "COM|NET|ORG|VN|TV"
_WEB_ADDRESS_PATTERNS = (
    re.compile(r"(?i)https?://|(?<![\w.@-])www(?![\w])"),
    # name.tld and sub.name.tld, the ending in any case (OCR reads "cOM", "aPP")
    re.compile(
        rf"(?<![\w.@-])(?:[A-Za-z0-9][A-Za-z0-9-]{{0,62}}\.)*[A-Za-z0-9][A-Za-z0-9-]{{1,62}}"
        rf"\.(?i:{_DOTTED_TLDS})(?![\w])"
    ),
    # NAME. COM, NAME .COM: the dot read with a space next to it
    re.compile(rf"(?<![\w.@-])[A-Z0-9][A-Z0-9-]{{2,62}}(?:\s+\.\s*|\.\s+)(?:{_DOTLESS_TLDS})(?![\w])"),
    # NAME,COM, NAME ,COM: the dot read as a comma ("SAIGON, VN" is a place)
    re.compile(r"(?<![\w.@-])[A-Z0-9][A-Z0-9-]{2,62}\s*,\s*(?:COM|NET|ORG)(?![\w])"),
    # NAMECOM: the dot lost
    re.compile(rf"(?<![\w.@-])[A-Z0-9][A-Z0-9-]{{2,62}}(?:{_DOTLESS_TLDS})(?![\w])"),
    # NAME COM, NAME NET: the dot read as a space ("THE NET", "LIVE TV" are words)
    re.compile(r"(?<![\w.@-])(?:[A-Z0-9][A-Z0-9-]{2,62}\sCOM|[A-Z0-9][A-Z0-9-]{3,62}\s(?:NET|ORG))(?![\w])"),
)
# Ordinary words that end like a dot-less domain.
_DOTLESS_WORDS = frozenset({
    "SITCOM", "TELECOM", "INTERCOM", "DOTCOM", "INTERNET", "CABINET", "PLANET", "MAGNET",
    "GARNET", "SONNET", "BONNET", "HORNET", "CORNET", "SIGNET", "BAYONET", "CLARINET",
    "DRAGNET", "CYBORG",
})
# Endings that are also words: "Wait.Me", "Stop.To" are two sentences with the space lost.
_WORD_TLDS = frozenset({
    "me", "to", "us", "pro", "top", "fun", "win", "live", "link", "club", "site", "app", "bet",
    "vip", "co", "cc", "io", "ly", "gg", "info", "online",
})


def _sentence_break(token: str) -> bool:
    ending = token.rsplit(".", 1)[-1] if "." in token else ""
    return ending.lower() in _WORD_TLDS and ending[:1].isupper() and ending[1:].islower()


def web_address_tokens(text: object) -> list[str]:
    """Web addresses in one OCR reading, also when OCR lost the dot or added a space.

    A general rule for every site: ``name.tld`` and ``sub.name.tld``, ``www``/``http``,
    and with the name in capitals ``NAME. COM``, ``NAME,COM``, ``NAMECOM`` (COM, NET,
    ORG, VN, TV), ``NAME COM`` and ``NAME NET``.
    """
    found = []
    for pattern in _WEB_ADDRESS_PATTERNS:
        for match in pattern.finditer(str(text or "")):
            token = match.group(0).strip()
            if re.sub(r"[^A-Z]", "", token.upper()) in _DOTLESS_WORDS or _sentence_break(token):
                continue
            found.append(token)
    return list(dict.fromkeys(found))


def _track_web_addresses(track: dict) -> list[str]:
    return list(dict.fromkeys(
        token for text in track.get("sample_text") or [] for token in web_address_tokens(text)
    ))


SCROLLING_CANDIDATE_TYPE = "scrolling_text"
SCROLLING_ROUTING = "REVIEW_SCROLLING_TEXT"
WEB_ADDRESS_ROUTING = "REVIEW_WEB_ADDRESS"
# Routings that already put a track in the main list without the low-ad advisory path.
_MAIN_LIST_ROUTINGS = frozenset({
    "REVIEW_AD_LIKELY", "REVIEW_PERSISTENT_OVERLAY", "REVIEW_POLICY_OVERRIDE", SCROLLING_ROUTING,
})


def promote_web_address_tracks(payload: dict, remembered: Iterable[str] = ()) -> dict:
    """A track with a web address is a main-list card suggesting BLUR, never only optional.

    Its routing becomes ``REVIEW_WEB_ADDRESS`` unless it is already a main-list
    routing (an ad routing keeps its own). ``remembered``: folded addresses the
    reviewer blurred before (``ad_text_memory``); a track whose folded reading
    holds one counts as having a web address. The payload is returned unchanged
    (the same object) when no track has a web address; untouched tracks stay the
    same objects.
    """
    remembered = tuple(remembered)
    output_tracks = []
    changed = False
    for track in payload.get("tracks") or []:
        eligible = isinstance(track, dict) and track.get("candidate_type") != SCROLLING_CANDIDATE_TYPE
        tokens = _track_web_addresses(track) if eligible else []
        folded = [fold_alnum(text) for text in track.get("sample_text") or []] if eligible and remembered else []
        known = [key for key in remembered if any(key in reading for reading in folded)]
        if not tokens and not known:
            output_tracks.append(track)
            continue
        routing = str(track.get("routing") or "")
        promoted = dict(track)
        web = {"tokens": tokens[:5], "previous_routing": routing or None}
        if known:
            web["remembered"] = known[:5]
        promoted.update({
            "review_candidate": True,
            "review_priority": "high",
            "routing": routing if routing in _MAIN_LIST_ROUTINGS else WEB_ADDRESS_ROUTING,
            "suggested_decision": "BLUR",
            "web_address": web,
            "reason": " ".join(filter(None, [
                str(track.get("reason") or "").strip(),
                f"Có địa chỉ web ({', '.join(tokens[:3])}) — đề xuất làm mờ, chờ bạn duyệt." if tokens else
                f"Giống địa chỉ web bạn đã làm mờ trước đây ({', '.join(known[:3])}) — đề xuất làm mờ, "
                "chờ bạn duyệt.",
            ])),
        })
        if promoted.get("candidate_type") == "title_overlay":
            promoted["candidate_type"] = None
        output_tracks.append(promoted)
        changed = True
    if not changed:
        return payload
    output = dict(payload)
    output["tracks"] = output_tracks
    return output


# --- Scrolling text -------------------------------------------------------------

SCROLLING_TOP_BAND = 0.25  # a band's centre above this share of the height (below 1 - it: bottom)
SCROLLING_BAND_OVERLAP = 0.50  # vertical overlap of two boxes, share of the lower box
SCROLLING_MAXIMUM_TRACK_SECONDS = 45.0  # a piece of one crossing; longer text stands still
SCROLLING_LINK_LETTERS = 6  # letters a reading needs to link pieces by their text
SCROLLING_LINK_SIMILARITY = 0.75
SCROLLING_LONG_READING = 12  # letters of the longest reading of a crossing
SCROLLING_EDGE = 0.05  # share of the width within which a box touches the frame's edge
SCROLLING_SLIDE_LETTERS = 6  # letters a moved reading lost at one end and gained at the other
SCROLLING_SLIDE_WIDTH = 0.50  # width share of both pieces whose readings prove a slide
SCROLLING_PIECE_HEIGHT = 0.50  # an unlinked piece is at least this share of the band's height
SCROLLING_TAIL_SHARE = 0.30  # width share a last reading may still cover without one more interval
_FIXED_ROUTINGS = frozenset({"REVIEW_PERSISTENT_OVERLAY", "LIKELY_TITLE_OVERLAY", "REVIEW_POLICY_OVERRIDE"})
_FIXED_CANDIDATE_TYPES = frozenset({"persistent_overlay", "title_overlay"})


def fold_alnum(value: object) -> str:
    """Lower-case letters and digits without Vietnamese marks ("Đ" → "d")."""
    text = str(value).casefold().replace("đ", "d")
    text = "".join(
        character for character in unicodedata.normalize("NFD", text)
        if unicodedata.category(character) != "Mn"
    )
    return re.sub(r"[^0-9a-z]+", "", text)


@dataclass(frozen=True)
class _Piece:
    track: dict
    index: int
    start: float
    end: float
    box: tuple[float, float, float, float]
    readings: tuple[str, ...]  # folded readings of at least SCROLLING_LINK_LETTERS letters

    @property
    def height(self) -> float:
        return max(1.0, self.box[3] - self.box[1])


def _vertical_overlap(first: tuple, second: tuple) -> float:
    shared = min(first[3], second[3]) - max(first[1], second[1])
    return max(0.0, shared) / max(1.0, min(first[3] - first[1], second[3] - second[1]))


def _band_side(box: tuple, height: float) -> str | None:
    centre = (box[1] + box[3]) / 2.0 / height
    if centre < SCROLLING_TOP_BAND:
        return "top"
    if centre > 1.0 - SCROLLING_TOP_BAND:
        return "bottom"
    return None


def _alignment(first: str, second: str) -> tuple[bool, bool]:
    """(the readings show the same stretch of one line, ``second`` is ``first`` moved on).

    The longest shared run fixes how the two readings line up; the stretch both
    cover must then match at SCROLLING_LINK_SIMILARITY (OCR misreads letters).
    Moved on: ``second`` lost at least SCROLLING_SLIDE_LETTERS letters at one end,
    gained as many at the other and shares SCROLLING_LONG_READING letters with
    ``first``. A reading that only grew ("Studio", then "Estudio de Doblaje" in
    end credits) or shrank did not move, nor did two subtitles that share a few
    words ("…với anh", then "Em nói anh…").
    """
    if min(len(first), len(second)) < SCROLLING_LINK_LETTERS:
        return False, False
    matcher = difflib.SequenceMatcher(None, first, second, autojunk=False)
    block = matcher.find_longest_match(0, len(first), 0, len(second))
    if block.size < 4:
        return False, False
    shift = block.a - block.b  # where ``second`` starts within ``first``
    first_from, first_to = max(0, shift), min(len(first), shift + len(second))
    second_from = max(0, -shift)
    shared = first_to - first_from
    if shared < SCROLLING_LINK_LETTERS:
        return False, False
    ratio = difflib.SequenceMatcher(
        None, first[first_from:first_to], second[second_from:second_from + shared], autojunk=False,
    ).ratio()
    if ratio < SCROLLING_LINK_SIMILARITY:
        return False, False
    gained_at_end = (shift + len(second)) - len(first)
    moved = shared >= SCROLLING_LONG_READING and (
        shift >= SCROLLING_SLIDE_LETTERS and gained_at_end >= SCROLLING_SLIDE_LETTERS
        or shift <= -SCROLLING_SLIDE_LETTERS and gained_at_end <= -SCROLLING_SLIDE_LETTERS
    )
    return True, moved


def _related(first: _Piece, second: _Piece) -> bool:
    return any(
        _alignment(one, other)[0] for one in first.readings for other in second.readings
    )


def _slides(pieces: list[_Piece], width: float) -> bool:
    """A later reading is an earlier one moved on, both read on pieces spanning half the frame."""
    readings = sorted(
        (piece.start, order, reading)
        for piece in pieces if piece.box[2] - piece.box[0] >= SCROLLING_SLIDE_WIDTH * width
        for order, reading in enumerate(piece.readings)
    )
    for position, (_, _, earlier) in enumerate(readings):
        for _, _, later in readings[position + 1:]:
            if _alignment(earlier, later)[1]:
                return True
    return False


def _direction(pieces: list[_Piece], width: float) -> str | None:
    """How the line crossed: an entry piece at one edge, later an exit piece at the other."""
    edge = SCROLLING_EDGE * width
    at_right = [p for p in pieces if p.box[2] >= width - edge and p.box[0] > edge]
    at_left = [p for p in pieces if p.box[0] <= edge and p.box[2] < width - edge]
    if any(entry.start < leaving.start for entry in at_right for leaving in at_left):
        return "right_to_left"
    if any(entry.start < leaving.start for entry in at_left for leaving in at_right):
        return "left_to_right"
    return None


def _lead_and_tail(members: list[_Piece], width: float, sample_every: float) -> tuple[float, float, str | None]:
    """(seconds before the first reading, extra seconds after the last track's end, direction).

    One read interval before the first reading, two when that reading is not an
    entry piece at the edge (the line had been crossing unseen longer). After
    the last reading the text scan already adds one interval; one more when that
    reading still covered more than SCROLLING_TAIL_SHARE of the width: the
    line's last letters at the frame's edge are often not read (job 140, the
    self-made clip). Without a known direction both extras apply.
    """
    direction = _direction(members, width)
    edge = SCROLLING_EDGE * width
    first_start = min(piece.start for piece in members)
    last_end = max(piece.end for piece in members)
    first = [piece for piece in members if piece.start == first_start]
    last = [piece for piece in members if piece.end == last_end]
    if direction == "right_to_left":
        entry_seen = any(p.box[2] >= width - edge and p.box[0] > edge for p in first)
        remaining = max(p.box[2] for p in last) / width
    elif direction == "left_to_right":
        entry_seen = any(p.box[0] <= edge and p.box[2] < width - edge for p in first)
        remaining = max(width - p.box[0] for p in last) / width
    else:
        entry_seen, remaining = False, 1.0
    lead = sample_every if entry_seen else 2 * sample_every
    tail = sample_every if remaining > SCROLLING_TAIL_SHARE else 0.0
    return lead, tail, direction


def _union_find(count: int, linked: Iterable[tuple[int, int]]) -> list[list[int]]:
    parent = list(range(count))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for first, second in linked:
        parent[find(first)] = find(second)
    groups: dict[int, list[int]] = {}
    for index in range(count):
        groups.setdefault(find(index), []).append(index)
    return list(groups.values())


def _eligible_pieces(tracks: list, height: float, side: str) -> list[_Piece]:
    pieces = []
    for index, track in enumerate(tracks):
        box = track.get("union_box") if isinstance(track, dict) else None
        if (
            not isinstance(box, list) or len(box) != 4
            or str(track.get("routing") or "") in _FIXED_ROUTINGS
            or track.get("candidate_type") in _FIXED_CANDIDATE_TYPES
            or track.get("grouped_track_ids")
        ):
            continue
        start, end = float(track["start_seconds"]), float(track["end_seconds"])
        values = tuple(float(value) for value in box)
        if end - start > SCROLLING_MAXIMUM_TRACK_SECONDS or _band_side(values, height) != side:
            continue
        readings = tuple(dict.fromkeys(
            folded for folded in (fold_alnum(text) for text in track.get("sample_text") or [])
            if len(folded) >= SCROLLING_LINK_LETTERS
        ))
        pieces.append(_Piece(track, index, start, end, values, readings))
    return pieces


def _near_in_time(first: _Piece, second: _Piece, sample_every: float) -> bool:
    return max(first.start, second.start) <= min(first.end, second.end) + sample_every + 0.001


def _crossings(pieces: list[_Piece], width: float, sample_every: float) -> list[list[_Piece]]:
    """Linked pieces of one band that crossed the frame or slid, joined when they meet in time."""
    ordered = sorted((piece for piece in pieces if piece.readings), key=lambda piece: piece.start)
    links = []
    for first_index, first in enumerate(ordered):
        for second_index in range(first_index + 1, len(ordered)):
            second = ordered[second_index]
            if second.start > first.end + sample_every + 0.001:
                break
            if (
                _vertical_overlap(first.box, second.box) >= SCROLLING_BAND_OVERLAP
                and _related(first, second)
            ):
                links.append((first_index, second_index))
    moving = []
    for members in _union_find(len(ordered), links):
        group = [ordered[index] for index in members]
        if max(len(reading) for piece in group for reading in piece.readings) < SCROLLING_LONG_READING:
            continue
        if _direction(group, width) is not None or _slides(group, width):
            moving.append(group)
    # Two halves of one line (job 107) are two crossings that meet in time.
    moving.sort(key=lambda group: min(piece.start for piece in group))
    joined: list[list[_Piece]] = []
    for group in moving:
        if joined and any(
            _near_in_time(one, other, sample_every)
            and _vertical_overlap(one.box, other.box) >= SCROLLING_BAND_OVERLAP
            for one in joined[-1] for other in group
        ):
            joined[-1].extend(group)
        else:
            joined.append(list(group))
    return joined


def _absorb_band_pieces(
    group: list[_Piece], pieces: list[_Piece], taken: set[int], sample_every: float,
) -> list[_Piece]:
    """Other pieces in the band while the line crosses it (short or unlinked readings).

    OCR splits the line into pieces whose text links to nothing (a stretch in the
    middle of the frame read once). Anything else inside the band then is under the
    band's blur anyway; only a subtitle line keeps its own track.
    """
    band = (0.0, min(p.box[1] for p in group), 1.0, max(p.box[3] for p in group))
    window_start = min(p.start for p in group) - sample_every - 0.001
    window_end = max(p.end for p in group) + sample_every + 0.001
    members = list(group)
    grouped = {piece.index for piece in group}
    for piece in pieces:
        if (
            piece.index in taken or piece.index in grouped
            or piece.track.get("routing") == "LIKELY_SUBTITLE"
            or piece.start < window_start or piece.end > window_end
            or piece.height < SCROLLING_PIECE_HEIGHT * (band[3] - band[1])
            or _vertical_overlap(piece.box, band) < SCROLLING_BAND_OVERLAP
        ):
            continue
        members.append(piece)
    return members


def _clock(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _scrolling_track(
    members: list[_Piece], *, side: str, width: float, sample_every: float, scan_start: float,
) -> dict:
    members = sorted(members, key=lambda piece: (piece.start, piece.box[0]))
    anchor = max(
        members, key=lambda piece: (max((len(r) for r in piece.readings), default=0), -piece.start),
    )
    first_read = min(piece.start for piece in members)
    last_end = max(piece.end for piece in members)
    lead, tail, direction = _lead_and_tail(members, width, sample_every)
    end = last_end + tail
    blur_end = max(
        float(piece.track.get("recommended_blur_end_seconds", piece.end)) for piece in members
    ) + tail
    texts = list(dict.fromkeys(
        str(text) for piece in members for text in piece.track.get("sample_text") or []
    ))
    web = list(dict.fromkeys(
        token for piece in members for token in _track_web_addresses(piece.track)
    ))
    band = "dải trên" if side == "top" else "dải dưới"
    merged = dict(anchor.track)
    merged.update({
        "start_seconds": round(max(scan_start, first_read - lead), 3),
        "end_seconds": round(end, 3),
        "recommended_blur_end_seconds": round(max(blur_end, end), 3),
        "union_box": [
            0, round(min(piece.box[1] for piece in members)),
            round(width), round(max(piece.box[3] for piece in members)),
        ],
        "sample_text": texts[:8],
        "observations": sum(int(piece.track.get("observations") or 0) for piece in members),
        "ad_probability": max(float(piece.track.get("ad_probability") or 0.0) for piece in members),
        "policy_hits": list(dict.fromkeys(
            hit for piece in members for hit in piece.track.get("policy_hits") or []
        )),
        "persistent": False,
        "review_candidate": True,
        "review_priority": "high",
        "routing": SCROLLING_ROUTING,
        "candidate_type": SCROLLING_CANDIDATE_TYPE,
        "suggested_decision": "BLUR",
        # A track without an id is named by its index in the report, as the queue names it.
        "grouped_track_ids": [int(piece.track.get("track_id", piece.index)) for piece in members],
        "member_previews": list(dict.fromkeys(
            str(piece.track["preview"]) for piece in members if piece.track.get("preview")
        )),
        "automatic_edit": False,
        "scrolling_text": {
            "band": side,
            "direction": direction,
            "first_read_seconds": round(first_read, 3),
            "last_read_seconds": round(last_end - sample_every, 3),
            "track_count": len(members),
            "lead_seconds": lead,
            "tail_seconds": round(sample_every + tail, 3),
            "web_address": web[:5],
        },
        "reason": (
            f"Dòng chữ chạy ngang {band} khung hình (OCR đọc được {len(members)} mảnh, "
            f"{_clock(first_read)}–{_clock(last_end - sample_every)})"
            + (f", có địa chỉ web {web[0]}" if web else "")
            + ". Đề xuất làm mờ cả dải, từ trước lần thấy đầu tiên tới sau lần thấy cuối "
            "(giữa hai lần đọc máy không nhìn thấy gì); chờ bạn duyệt."
        ),
    })
    return merged


def promote_scrolling_text(payload: dict) -> dict:
    """Join the OCR pieces of each crossing of a scrolling line into one track.

    A crossing: tracks in one horizontal band (top or bottom) whose readings are
    stretches of one line, that touched one frame edge and then the other, or
    whose later wide reading is an earlier one moved on (``_slides``). Pieces of the
    band while it crosses (short, unlinked readings) join it. The track covers
    the whole band, from one read interval before the first reading to the last
    track's end, and suggests BLUR. The payload is returned unchanged (the same
    object) when no crossing is found.
    """
    tracks = [track for track in payload.get("tracks") or [] if isinstance(track, dict)]
    analysis = payload.get("analysis_size") or []
    sample_every = float(payload.get("sample_every_seconds") or 0.0)
    if not tracks or len(analysis) != 2 or not analysis[0] or not analysis[1] or sample_every <= 0:
        return payload
    width, height = float(analysis[0]), float(analysis[1])
    scan_start = float(payload.get("scan_start_seconds") or 0.0)
    promoted: list[dict] = []
    absorbed: set[int] = set()
    for side in ("top", "bottom"):
        pieces = _eligible_pieces(tracks, height, side)
        for group in _crossings(pieces, width, sample_every):
            if any(piece.index in absorbed for piece in group):
                continue
            members = _absorb_band_pieces(group, pieces, absorbed, sample_every)
            absorbed.update(piece.index for piece in members)
            promoted.append(_scrolling_track(
                members, side=side, width=width, sample_every=sample_every, scan_start=scan_start,
            ))
    if not promoted:
        return payload
    output = dict(payload)
    output["tracks"] = [
        track for index, track in enumerate(tracks) if index not in absorbed
    ] + promoted
    output["scrolling_text_tracks"] = [
        {"track_id": track.get("track_id"), "grouped_track_ids": track["grouped_track_ids"],
         **track["scrolling_text"]}
        for track in promoted
    ]
    return output
