"""How the blurs of one kept segment are wired into the export's filter graph.

Until 2026-10-14 each regional blur split the picture the blur before it had
made ("nested" layout). FFmpeg 9.0.1's work per frame follows the number of
paths from the segment's input to its output, and nesting multiplies them by
every region (×4 for the cover method with a feathered edge). Measured from
about 0.1 to 0.6 µs per path and frame, depending on the filters on them: a
10-region plan of 786,432 paths took 0.46 s per frame, and the 25 regions of
job 134 (4**25 paths) never finished a frame
(docs/evidence/export-regions/README.md).

A segment whose nested graph stays within NESTED_PATH_LIMIT paths keeps it,
character for character. A larger one gets the "shared" layout: one split of a
picture feeds the main line and every branch that reads it, and a region's
branch reads the earliest picture that already holds every change an earlier
blur can make where the branch reads, while both are on. Each branch thus sees
the pixels it saw nested, and the paths add up instead of multiplying.

No later branch reads a picture from before the first regional overlay: for a
source other than 8-bit 4:2:0 that picture still has the source's pixel format,
while nested every later branch read the overlay's (measured on yuv420p10le,
yuv422p, yuv444p and yuv410p).

Long chains of overlapping regions could still multiply the paths. In a
segment with more than EXACT_REGION_LIMIT regions, past SHARED_PATH_BUDGET a
branch reads an older picture instead: its region is still blurred all over,
but it blurs a picture without the earlier blur it overlaps, so its pixels may
differ anywhere inside its own region (never outside it). A plain gblur over a
cover-method region then shows the logo blurred there, not covered. The export
manifest counts such regions. A segment of at most
EXACT_REGION_LIMIT regions always reads exactly: its plan exported before
(slowly, when its regions overlap, and as slowly now), and it keeps its pixels.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from biliflow.blur_filter import (
    CHROMA_420,
    COVER_MARGIN_PIXELS,
    COVER_METHOD,
    SourceChroma,
    regional_blur_branch,
    regional_blur_filters,
    regional_blur_paths,
)

FULL_FRAME = "FULL_FRAME"
NESTED_LAYOUT = "nested"
SHARED_LAYOUT = "shared"
# 5 regions with the cover method and a feathered edge (4**5), the most any
# export had before job 134: about 1 ms per frame more than one region.
NESTED_PATH_LIMIT = 1024
# At most a few ms per frame (0.1 to 0.6 µs per path and frame, as measured).
SHARED_PATH_BUDGET = 4096
# Before 2026-10-14 an export had at most 5 regions; 10 nested ones still ran
# (job 134's graph cut down to 10 regions) and 11 did not.
EXACT_REGION_LIMIT = 10
# Pixels beyond a region that its branch may read or its overlay change: crop
# rounds its corner down to whole chroma samples (up to 3 pixels for 4:1:0),
# an overlay at an odd position changes the chroma sample it shares with the
# pixel beside the region, and a cover region delogo refused before 2026-10-14
# is cut and laid from the even pixel left of and above it (blur_filter._cover_patch).
EDGE_PIXELS = 4
# Two time ranges closer than this count as meeting: between() includes both
# ends, and the times are written with 6 decimals.
TIME_SLACK_SECONDS = 0.001


@dataclass(frozen=True)
class SegmentBlur:
    """One blur of a kept segment: its region (or FULL_FRAME), its settings and its times in the segment."""

    region: dict | str
    sigma: float
    feather: int
    feather_mode: str
    method: str
    ranges: tuple[tuple[float, float], ...]

    @property
    def full_frame(self) -> bool:
        return self.region == FULL_FRAME

    @property
    def enable(self) -> str:
        expression = "+".join(
            f"between(t,{start:.6f},{end:.6f})" for start, end in self.ranges
        )
        return f"enable='{expression}'"

    @property
    def branch_paths(self) -> int:
        """Paths this blur adds per path of the picture it reads (none for a full-frame blur, which has no branch)."""
        if self.full_frame:
            return 0
        return regional_blur_paths(feather=self.feather, method=self.method)


@dataclass(frozen=True)
class SegmentLayout:
    """The filters of one segment's blurs, the label of its last picture and how they were wired."""

    filters: tuple[str, ...]
    output_label: str
    layout: str
    paths: int
    older_reads: int


def nested_paths(blurs: Sequence[SegmentBlur]) -> int:
    paths = 1
    for blur in blurs:
        paths *= 1 + blur.branch_paths
    return paths


def segment_blur_filters(
    *, index: int, input_label: str, blurs: Sequence[SegmentBlur],
    frame_size: tuple[int, int] | None, chroma_rows_divisor: int,
    source_chroma: SourceChroma = SourceChroma(),
) -> SegmentLayout:
    """The filters applying ``blurs`` in order to segment ``index``'s picture ``input_label``.

    ``source_chroma``: the chroma steps of the probed source (SourceChroma.of_stream).
    """
    paths = nested_paths(blurs)
    steps = branch_chroma_steps(blurs, source_chroma)
    if paths <= NESTED_PATH_LIMIT:
        return _nested(index, input_label, blurs, frame_size, chroma_rows_divisor, steps, paths)
    return _shared(index, input_label, blurs, frame_size, chroma_rows_divisor, steps)


def branch_chroma_steps(
    blurs: Sequence[SegmentBlur], source_chroma: SourceChroma,
) -> list[tuple[int, int]]:
    """The chroma step of the picture each blur's branch crops, in either layout.

    The first regional branch crops the segment's input in the source's format, or
    in the format a full-frame blur before it in the segment hands on; every later
    one crops a picture after the first regional overlay, whose format is 4:2:0
    (measured on FFmpeg 9.0.1, docs/evidence/cover-edge/README.md).
    """
    first = next((k for k, blur in enumerate(blurs) if not blur.full_frame), None)
    if first is not None and first > 0:
        step = source_chroma.after_full_frame
    else:
        step = source_chroma.first
    return [step if k == first else CHROMA_420 for k in range(len(blurs))]


def _nested(
    index: int, input_label: str, blurs: Sequence[SegmentBlur],
    frame_size: tuple[int, int] | None, chroma_rows_divisor: int,
    steps: Sequence[tuple[int, int]], paths: int,
) -> SegmentLayout:
    filters: list[str] = []
    current = input_label
    for blur_index, blur in enumerate(blurs):
        output = f"v{index}blur{blur_index}"
        if blur.full_frame:
            filters.append(f"[{current}]gblur=sigma={blur.sigma:g}:{blur.enable}[{output}]")
        else:
            filters.extend(regional_blur_filters(
                input_label=current, output_label=output,
                prefix=f"v{index}region{blur_index}", region=blur.region,
                sigma=blur.sigma, feather=blur.feather, feather_mode=blur.feather_mode,
                enable=blur.enable, method=blur.method, frame_size=frame_size,
                chroma_rows_divisor=chroma_rows_divisor, chroma_step=steps[blur_index],
            ))
        current = output
    return SegmentLayout(tuple(filters), current, NESTED_LAYOUT, paths, 0)


def _shared(
    index: int, input_label: str, blurs: Sequence[SegmentBlur],
    frame_size: tuple[int, int] | None, chroma_rows_divisor: int,
    steps: Sequence[tuple[int, int]],
) -> SegmentLayout:
    sources, paths, older_reads = shared_sources(blurs)
    readers: dict[int, list[int]] = {}
    for blur_index, source in enumerate(sources):
        if source is not None:
            readers.setdefault(source, []).append(blur_index)
    filters: list[str] = []
    main: dict[int, str] = {}

    def publish(version: int, label: str) -> None:
        # Picture ``version`` (0: the segment's input, k + 1: after blur k)
        # goes on along the main line and to every branch that reads it.
        branches = readers.get(version, [])
        if not branches:
            main[version] = label
            return
        outputs = [f"v{index}keep{version}", *(f"v{index}region{k}crop" for k in branches)]
        filters.append(f"[{label}]split={len(outputs)}" + "".join(f"[{name}]" for name in outputs))
        main[version] = outputs[0]

    publish(0, input_label)
    for blur_index, blur in enumerate(blurs):
        output = f"v{index}blur{blur_index}"
        if blur.full_frame:
            filters.append(f"[{main[blur_index]}]gblur=sigma={blur.sigma:g}:{blur.enable}[{output}]")
        else:
            filters.extend(regional_blur_branch(
                base_label=main[blur_index], crop_label=f"v{index}region{blur_index}crop",
                output_label=output, prefix=f"v{index}region{blur_index}", region=blur.region,
                sigma=blur.sigma, feather=blur.feather, feather_mode=blur.feather_mode,
                enable=blur.enable, method=blur.method, frame_size=frame_size,
                chroma_rows_divisor=chroma_rows_divisor, chroma_step=steps[blur_index],
            ))
        publish(blur_index + 1, output)
    return SegmentLayout(tuple(filters), main[len(blurs)], SHARED_LAYOUT, paths, older_reads)


def shared_sources(blurs: Sequence[SegmentBlur]) -> tuple[list[int | None], int, int]:
    """For each blur the picture its branch reads (None for a full-frame blur), the paths, and how many read an older picture than exactness needs.

    Picture 0 is the segment's input, picture k + 1 the one after blur k.
    """
    first = next((k for k, blur in enumerate(blurs) if not blur.full_frame), None)
    if first is None:
        return [None] * len(blurs), 1, 0
    # Up to the first regional blur only full-frame blurs (no branch) change the
    # picture, so every picture up to it has one path, and the one after it 1 + b.
    floor_paths = 1 + blurs[first].branch_paths
    later = sum(blur.branch_paths for blur in blurs[first + 1:])
    regions = sum(not blur.full_frame for blur in blurs)
    budget = (
        math.inf if regions <= EXACT_REGION_LIMIT
        else max(SHARED_PATH_BUDGET, floor_paths * (1 + later))
    )
    remaining = later
    paths = [1]
    sources: list[int | None] = []
    older_reads = 0
    for k, blur in enumerate(blurs):
        if blur.full_frame:
            sources.append(None)
            paths.append(paths[k])
            continue
        if k == first:
            # As nested: the picture after the full-frame blurs before it.
            source = exact = k
        else:
            remaining -= blur.branch_paths
            floor = first + 1
            exact = max(floor, _latest_meeting(blurs, k))
            source = exact
            while source > floor and paths[k] + blur.branch_paths * paths[source] + remaining * floor_paths > budget:
                source -= 1
        if source != exact:
            older_reads += 1
        sources.append(source)
        paths.append(paths[k] + blur.branch_paths * paths[source])
    return sources, paths[-1], older_reads


def _latest_meeting(blurs: Sequence[SegmentBlur], k: int) -> int:
    """The picture after the last blur before ``k`` that can change what ``k``'s branch reads while ``k`` is on (0: none)."""
    reads = _box(blurs[k], read=True)
    for j in range(k - 1, -1, -1):
        earlier = blurs[j]
        if not _times_meet(earlier.ranges, blurs[k].ranges):
            continue
        if earlier.full_frame or _boxes_meet(_box(earlier, read=False), reads):
            return j + 1
    return 0


def _box(blur: SegmentBlur, *, read: bool) -> tuple[int, int, int, int]:
    """The pixels (x0, y0, x1, y1) a regional blur's branch may read, or its overlay change."""
    region = blur.region
    assert isinstance(region, dict)
    margin = EDGE_PIXELS + (COVER_MARGIN_PIXELS if read and blur.method == COVER_METHOD else 0)
    x, y = int(region["x"]), int(region["y"])
    return (
        x - margin, y - margin,
        x + int(region["width"]) + margin, y + int(region["height"]) + margin,
    )


def _boxes_meet(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> bool:
    return (
        first[0] < second[2] and second[0] < first[2]
        and first[1] < second[3] and second[1] < first[3]
    )


def _times_meet(
    first: Sequence[tuple[float, float]], second: Sequence[tuple[float, float]],
) -> bool:
    return any(
        start_a <= end_b + TIME_SLACK_SECONDS and start_b <= end_a + TIME_SLACK_SECONDS
        for start_a, end_a in first for start_b, end_b in second
    )
