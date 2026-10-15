"""A site watermark read again and again, and subtitle lines read as ads, at review-build time.

Jobs 134 and 143 (2026-10-15): a site's address sits just under the subtitles.
The text scan reads it in runs with gaps (a subtitle covers it, OCR misreads a
letter), so it came out as dozens of tracks; their cards then merged with the
dialogue cards between them into boxes over the subtitles. Single dialogue lines
scored as ad text also filled the main list.

``group_repeated_watermarks`` joins the tracks of one web address read again and
again at the same place (similar folded address, boxes overlapping by
WATERMARK_PLACE_IOU) into one track: one moment per run, from one read interval
before its first reading to its blur end, in the union of the runs' boxes,
suggested BLUR. One decision then edits every moment and only those. A general
rule for every site: nothing here names one.

``move_subtitle_lines`` sends a centred line of the subtitle zone read at most
SUBTITLE_LINE_MAXIMUM_READS times and routed only uncertain, in a film with a
stream of such lines, to the optional list. Ad detection in the subtitle band is
never weakened: a line with a web address (also one the reviewer blurred before),
a phone number, an @handle, a policy hit, an ad or overlay routing, an ad score of
SUBTITLE_LINE_MAXIMUM_AD or "advertisement" as its first semantic label, or one
that stays on screen keeps its main-list card exactly as before.

Both only regroup and suggest; the reviewer decides. Built from an existing text
scan, so old jobs need no rescan. Only ``review_workflow`` imports this module:
it is in no scan stage's cache key.
"""
from __future__ import annotations

import difflib
import statistics
from dataclasses import dataclass, field
from typing import Iterable

from biliflow.ad_text_memory import memory_key
from biliflow.ad_text_patterns import ad_text_tokens
from biliflow.scrolling_text import SCROLLING_CANDIDATE_TYPE, fold_alnum, web_address_tokens

REPEATED_WATERMARK_TYPE = "repeated_watermark"
REPEATED_WATERMARK_ROUTING = "REVIEW_REPEATED_WATERMARK"
DISCRETE = "discrete_detected_intervals"
# The runs of one mark sit at 0.55 or more from their group's median box (jobs 130-145);
# a box holding the mark and a subtitle line, or a wider line around it, at 0.1-0.4.
WATERMARK_PLACE_IOU = 0.50
WATERMARK_KEY_SIMILARITY = 0.75
WATERMARK_PREVIEWS = 6
# From this many runs on, the card's box is each edge's 10th/90th percentile of the
# runs' boxes (one OCR box grown over the subtitle line above no longer widens every
# moment's blur); fewer runs keep the union of their boxes.
WATERMARK_ROBUST_RUNS = 10
WATERMARK_EDGE_SHARE = 0.10
# A mark read in that many runs is still on screen between them: OCR read it in the
# source inside gaps of every length up to the longest (131.5 s) in jobs 130-145
# (docs/evidence/subtitle-band/gap_check.py). Its moments closer than this join.
WATERMARK_BRIDGE_SECONDS = 150.0
_FIXED_ROUTINGS = frozenset({"REVIEW_PERSISTENT_OVERLAY", "LIKELY_TITLE_OVERLAY", "REVIEW_POLICY_OVERRIDE"})
_FIXED_CANDIDATE_TYPES = frozenset({"persistent_overlay", "title_overlay", SCROLLING_CANDIDATE_TYPE})

SUBTITLE_ZONE = "subtitle"
SUBTITLE_LINE_MAXIMUM_READS = 2
SUBTITLE_LINE_CENTRE = 0.15  # the box's centre within this share of the width from the frame's centre
SUBTITLE_LINE_STREAM = 20  # subtitle-zone tracks read at most twice that make a film's subtitle stream
# Only these routings move: an ad, overlay, policy or web-address routing keeps its card.
_MOVABLE_ROUTINGS = frozenset({"REVIEW_UNCERTAIN"})
# A line the text semantics rank as advertisement first, or score this high for it
# (REVIEW_AD_LIKELY starts at 0.45), keeps its card even without an address.
SUBTITLE_LINE_MAXIMUM_AD = 0.40
SUBTITLE_LINE_REASON = "Giống một dòng phụ đề; không chặn xuất video"


def _box(track: dict) -> tuple[float, float, float, float] | None:
    box = track.get("union_box")
    if not isinstance(box, list) or len(box) != 4:
        return None
    x1, y1, x2, y2 = (float(value) for value in box)
    return (x1, y1, x2, y2) if x2 > x1 and y2 > y1 else None


def _iou(first: tuple, second: tuple) -> float:
    width = min(first[2], second[2]) - max(first[0], second[0])
    height = min(first[3], second[3]) - max(first[1], second[1])
    shared = max(0.0, width) * max(0.0, height)
    union = ((first[2] - first[0]) * (first[3] - first[1])
             + (second[2] - second[0]) * (second[3] - second[1]) - shared)
    return shared / union if union > 0 else 0.0


def _remembered_in(track: dict, remembered: tuple[str, ...]) -> list[str]:
    if not remembered:
        return []
    folded = [fold_alnum(text) for text in track.get("sample_text") or []]
    return [key for key in remembered if any(key in reading for reading in folded)]


def _similar(first: str, second: str) -> bool:
    return (first in second or second in first
            or difflib.SequenceMatcher(None, first, second).ratio() >= WATERMARK_KEY_SIMILARITY)


# --- One watermark read again and again ---------------------------------------------


@dataclass(frozen=True)
class _Sighting:
    index: int
    track: dict
    box: tuple[float, float, float, float]
    tokens: tuple[str, ...]
    remembered: tuple[str, ...]
    keys: frozenset[str]

    @property
    def start(self) -> float:
        return float(self.track["start_seconds"])


@dataclass
class _Group:
    members: list[_Sighting] = field(default_factory=list)

    def core(self) -> tuple[float, ...]:
        """The per-edge median box: one odd reading does not move the place."""
        return tuple(statistics.median(member.box[side] for member in self.members) for side in range(4))

    def accepts(self, sighting: _Sighting) -> bool:
        if _iou(sighting.box, self.core()) < WATERMARK_PLACE_IOU:
            return False
        keys = {key for member in self.members for key in member.keys}
        return any(_similar(key, other) for key in sighting.keys for other in keys)


def _sighting(index: int, track: object, remembered: tuple[str, ...]) -> _Sighting | None:
    if (
        not isinstance(track, dict)
        or track.get("candidate_type") in _FIXED_CANDIDATE_TYPES
        or str(track.get("routing") or "") in _FIXED_ROUTINGS
        or isinstance(track.get("fixed_text_overlay"), dict)
    ):
        return None
    box = _box(track)
    tokens = tuple(dict.fromkeys(
        token for text in track.get("sample_text") or [] for token in web_address_tokens(text)
    ))
    known = tuple(_remembered_in(track, remembered))
    keys = frozenset(filter(None, (memory_key(token) for token in tokens))) | frozenset(known)
    if box is None or not keys:
        return None
    return _Sighting(index=index, track=track, box=box, tokens=tokens, remembered=known, keys=keys)


def _moments(members: list[_Sighting], sample_every: float, scan_start: float) -> list[dict]:
    """One moment per run, from one read interval before it to its blur end; runs that touch join,
    and from WATERMARK_ROBUST_RUNS runs on, runs at most WATERMARK_BRIDGE_SECONDS apart."""
    bridge = WATERMARK_BRIDGE_SECONDS if len(members) >= WATERMARK_ROBUST_RUNS else 0.0
    moments: list[dict] = []
    for member in sorted(members, key=lambda value: value.start):
        start = round(max(scan_start, member.start - sample_every), 3)
        end = round(float(member.track.get("recommended_blur_end_seconds", member.track["end_seconds"])), 3)
        if moments and start - moments[-1]["end_seconds"] <= bridge:
            moments[-1]["end_seconds"] = max(moments[-1]["end_seconds"], end)
        else:
            moments.append({"start_seconds": start, "end_seconds": end})
    return moments


def _region(members: list[_Sighting]) -> list[int]:
    """The card's box in analysis pixels: the union of the runs' boxes, robust from WATERMARK_ROBUST_RUNS on."""
    share = WATERMARK_EDGE_SHARE if len(members) >= WATERMARK_ROBUST_RUNS else 0.0
    region = []
    for side in range(4):
        values = sorted(member.box[side] for member in members)
        rank = int(share * len(values))  # 10 % of the runs, rounded towards the outer edge
        region.append(round(values[rank] if side < 2 else values[len(values) - 1 - rank]))
    return region


def _spread(values: list[str], count: int) -> list[str]:
    if len(values) <= count:
        return values
    return [values[round(step * (len(values) - 1) / (count - 1))] for step in range(count)]


def _clock(seconds: float) -> str:
    whole = int(seconds)
    if whole >= 3600:
        return f"{whole // 3600}:{whole // 60 % 60:02d}:{whole % 60:02d}"
    return f"{whole // 60}:{whole % 60:02d}"


def _watermark_track(members: list[_Sighting], sample_every: float, scan_start: float) -> dict:
    members = sorted(members, key=lambda value: (value.start, value.index))
    anchor = max(members, key=lambda value: (len(value.tokens), -value.start))
    moments = _moments(members, sample_every, scan_start)
    bridged = len(members) >= WATERMARK_ROBUST_RUNS
    tokens = list(dict.fromkeys(token for member in members for token in member.tokens))
    known = list(dict.fromkeys(key for member in members for key in member.remembered))
    shown = ", ".join((tokens or known)[:2])
    web = {"tokens": tokens[:5]}
    if known:
        web["remembered"] = known[:5]
    group = dict(anchor.track)
    group.update({
        "track_id": members[0].track.get("track_id", members[0].index),
        "start_seconds": moments[0]["start_seconds"],
        "end_seconds": round(max(float(member.track["end_seconds"]) for member in members), 3),
        "recommended_blur_end_seconds": moments[-1]["end_seconds"],
        "temporal_policy": DISCRETE,
        "detected_intervals": moments,
        "union_box": _region(members),
        "sample_text": list(dict.fromkeys(
            str(text) for member in members for text in member.track.get("sample_text") or []
        ))[:8],
        "observations": sum(int(member.track.get("observations") or 0) for member in members),
        "max_confidence": max(float(member.track.get("max_confidence") or 0.0) for member in members),
        "ad_probability": max(float(member.track.get("ad_probability") or 0.0) for member in members),
        "policy_hits": list(dict.fromkeys(
            hit for member in members for hit in member.track.get("policy_hits") or []
        )),
        "persistent": any(bool(member.track.get("persistent")) for member in members),
        "review_candidate": True,
        "review_priority": "high",
        "routing": REPEATED_WATERMARK_ROUTING,
        "candidate_type": REPEATED_WATERMARK_TYPE,
        "suggested_decision": "BLUR",
        # A track without an id is named by its index in the report, as the queue names it.
        "grouped_track_ids": [int(member.track.get("track_id", member.index)) for member in members],
        "member_previews": _spread(list(dict.fromkeys(
            str(member.track["preview"]) for member in members if member.track.get("preview")
        )), WATERMARK_PREVIEWS),
        "automatic_edit": False,
        "web_address": web,
        "repeated_watermark": {
            "sightings": len(members),
            "moments": len(moments),
            "bridge_seconds": WATERMARK_BRIDGE_SECONDS if bridged else 0.0,
            "previous_routings": sorted({str(member.track.get("routing") or "") for member in members}),
        },
        "reason": (
            f"Cùng một địa chỉ web ({shown}) hiện lại {len(members)} lần ở cùng một chỗ, "
            f"{_clock(moments[0]['start_seconds'])}–{_clock(moments[-1]['end_seconds'])}: một thẻ cho mọi "
            f"lần ({len(moments)} khoảng"
            + (f"; hai lần đọc cách nhau tới {WATERMARK_BRIDGE_SECONDS:g} s được nối liền vì watermark vẫn còn "
               "trên hình giữa các lần đọc" if bridged else "")
            + "), chỉ làm mờ đúng chỗ đó trong các khoảng đó. Đề xuất làm mờ, chờ bạn duyệt."
        ),
    })
    return group


def group_repeated_watermarks(payload: dict, remembered: Iterable[str] = ()) -> dict:
    """Join the tracks of one web address read again and again at one place into one track.

    ``remembered``: folded addresses the reviewer blurred before (``ad_text_memory``);
    a track whose folded reading holds one counts as having that address. Fixed
    overlays, title overlays and scrolling lines keep their own tracks. The payload
    is returned unchanged (the same object) when no address is read twice at one
    place; its tracks are never modified.
    """
    remembered = tuple(remembered)
    tracks = list(payload.get("tracks") or [])
    sample_every = float(payload.get("sample_every_seconds") or 0.0)
    scan_start = float(payload.get("scan_start_seconds") or 0.0)
    sightings = [
        sighting for sighting in (_sighting(index, track, remembered) for index, track in enumerate(tracks))
        if sighting is not None
    ]
    groups: list[_Group] = []
    for sighting in sorted(sightings, key=lambda value: (value.start, value.index)):
        target = next((group for group in groups if group.accepts(sighting)), None)
        if target is None:
            groups.append(_Group([sighting]))
        else:
            target.members.append(sighting)
    joined = [group.members for group in groups if len(group.members) >= 2]
    if not joined:
        return payload
    absorbed = {member.index for members in joined for member in members}
    promoted = [_watermark_track(members, sample_every, scan_start) for members in joined]
    output = dict(payload)
    output["tracks"] = [track for index, track in enumerate(tracks) if index not in absorbed] + promoted
    output["repeated_watermark_tracks"] = [
        {"track_id": track["track_id"], "grouped_track_ids": track["grouped_track_ids"],
         "moments": len(track["detected_intervals"])}
        for track in promoted
    ]
    return output


# --- Subtitle lines read as ads -------------------------------------------------------


def _ad_evidence(track: dict, remembered: tuple[str, ...]) -> bool:
    texts = [str(text) for text in track.get("sample_text") or []]
    try:
        ad_score = float(track.get("ad_probability") or 0.0)
    except (TypeError, ValueError):
        return True  # an unreadable score proves nothing: the line keeps its card
    return bool(
        list(track.get("policy_hits") or [])
        or isinstance(track.get("web_address"), dict)
        or track.get("semantic_top_label") == "advertisement"
        or ad_score >= SUBTITLE_LINE_MAXIMUM_AD
        or any(web_address_tokens(text) or ad_text_tokens(text) or "@" in text for text in texts)
        or _remembered_in(track, remembered)
    )


def _read_at_most_twice(track: dict) -> bool:
    """Read at most SUBTITLE_LINE_MAXIMUM_READS times; a track whose report does not say is not."""
    reads = track.get("observations")
    return isinstance(reads, int) and not isinstance(reads, bool) and reads <= SUBTITLE_LINE_MAXIMUM_READS


def _subtitle_stream(tracks: list) -> int:
    return sum(
        1 for track in tracks
        if isinstance(track, dict) and track.get("zone") == SUBTITLE_ZONE and _read_at_most_twice(track)
    )


def _subtitle_line(track: object, width: float, remembered: tuple[str, ...]) -> bool:
    if not isinstance(track, dict):
        return False
    box = _box(track)
    return (
        track.get("zone") == SUBTITLE_ZONE
        and track.get("review_candidate") is not False
        and str(track.get("routing") or "") in _MOVABLE_ROUTINGS
        and track.get("candidate_type") is None
        and not track.get("persistent")
        and not isinstance(track.get("fixed_text_overlay"), dict)
        and _read_at_most_twice(track)
        and box is not None
        and abs((box[0] + box[2]) / 2 - width / 2) <= SUBTITLE_LINE_CENTRE * width
        and not _ad_evidence(track, remembered)
    )


def move_subtitle_lines(payload: dict, remembered: Iterable[str] = ()) -> dict:
    """Send centred subtitle-zone lines read as uncertain ad text to the optional list.

    A moved track gets ``review_candidate: False`` (no main-list card) and
    ``subtitle_line`` (its previous routing); ``review_workflow`` shows it among the
    optional candidates, suggested KEEP. Only in a film with at least
    SUBTITLE_LINE_STREAM subtitle-zone tracks read at most twice. The payload is
    returned unchanged (the same object) when no line moves; its tracks are never
    modified.
    """
    remembered = tuple(remembered)
    tracks = list(payload.get("tracks") or [])
    analysis = payload.get("analysis_size") or []
    if len(analysis) != 2 or not analysis[0] or _subtitle_stream(tracks) < SUBTITLE_LINE_STREAM:
        return payload
    width = float(analysis[0])
    output_tracks = []
    moved = []
    for track in tracks:
        if not _subtitle_line(track, width, remembered):
            output_tracks.append(track)
            continue
        line = dict(track)
        line["review_candidate"] = False
        line["subtitle_line"] = {"previous_routing": str(track.get("routing") or "")}
        line["reason"] = " ".join(filter(None, [str(track.get("reason") or "").strip(), SUBTITLE_LINE_REASON + "."]))
        output_tracks.append(line)
        moved.append(line.get("track_id"))
    if not moved:
        return payload
    output = dict(payload)
    output["tracks"] = output_tracks
    output["subtitle_line_tracks"] = moved
    return output
