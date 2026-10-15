from __future__ import annotations

import math
import re
from dataclasses import dataclass

# How a regional BLUR hides its region. "gblur" (the default, and every edit plan
# written before the cover method existed) only blurs the region, so a bright logo
# on a dark frame stays readable. "delogo_blur_v1" (user decision 2026-10-04, measured
# on real frames) first fills the region from the picture around it (FFmpeg delogo),
# then blurs it, so neither the shape nor the colour of the logo remains.
GBLUR = "gblur"
COVER_METHOD = "delogo_blur_v1"
BLUR_METHODS = frozenset({GBLUR, COVER_METHOD})
DEFAULT_SIGMA = 28
# Pixels of picture around the region that delogo reads to fill it.
COVER_MARGIN_PIXELS = 8
# The cover blur grows with the region: sigma 28 barely softens a 100-pixel-high logo.
COVER_SIGMA_FRACTION = 0.6
# alphamerge of the one-frame feather mask: after the mask's only frame it keeps
# repeating it until the colour branch ends. These are FFmpeg's defaults, written
# out so that a change of default cannot leave later frames without the mask.
MASK_REPEAT_ALPHAMERGE = "alphamerge=eof_action=repeat:repeatlast=1:shortest=0"
# Pixels per chroma sample (across, down) of the picture a branch crops. FFmpeg's
# crop rounds its size and corner down to whole chroma samples of that picture
# (FFmpeg 9.0.1, docs/evidence/cover-edge/README.md). A segment's first regional
# branch crops the source's format (SourceChroma); every later branch crops an
# overlay's, 4:2:0.
CHROMA_420 = (2, 2)
# No rounding: the cover chain of every region is the one of before 2026-10-14.
NO_ROUNDING = (1, 1)
_SUBSAMPLING = {"444": (1, 1), "422": (2, 1), "420": (2, 2), "440": (1, 2), "411": (4, 1), "410": (4, 4)}
_PLANAR_YUV = re.compile(r"yuv[aj]?(4[1-4][0-4])p(?:\d+(?:msb)?(?:le|be))?")
# Semi-planar YUV: nv12/nv21 and p010le…p016be are 4:2:0, nv16/nv20 and p2xx 4:2:2.
_SEMI_PLANAR = re.compile(r"(?:nv(12|21|16|20le|20be|24|42))|p([024])1[026](?:le|be)")
_SEMI_PLANAR_STEPS = {
    "12": (2, 2), "21": (2, 2), "16": (2, 1), "20le": (2, 1), "20be": (2, 1), "24": (1, 1), "42": (1, 1),
    "0": (2, 2), "2": (2, 1), "4": (1, 1),
}
# Converted to 4:2:0 before any crop: crop takes no packed subsampled or bitstream format.
_CONVERTED_BEFORE_CROP = frozenset({
    "yuyv422", "uyvy422", "yvyu422", "y210le", "y212le", "y216le", "uyyvyy411", "monow", "monob",
})
# A display matrix: 3 x 3 entries, 16.16 fixed point but the last column (2.30).
_DISPLAY_MATRIX_ENTRIES = 9
_FIXED_ONE = 1 << 16
# ffprobe prints a display matrix as three rows, each after its byte offset ("00000001:").
_DISPLAY_MATRIX_OFFSET = re.compile(r"^\s*[0-9a-fA-F]{8}:", re.MULTILINE)
# Formats a full-frame gblur takes (apart from 4:2:0 ones), so the first regional
# branch after it still crops the source's own format. Every other format reaches
# that branch as 4:2:0.
_KEPT_THROUGH_GBLUR = frozenset({
    "yuv444p", "yuvj444p", "yuv444p9le", "yuv444p10le", "yuv444p12le", "yuv444p14le", "yuv444p16le",
    "yuva444p", "yuva444p9le", "yuva444p10le", "yuva444p12le", "yuva444p16le",
    "gray", "gray9le", "gray10le", "gray12le", "gray14le", "gray16le", "grayf32le",
    "gbrp", "gbrp9le", "gbrp10le", "gbrp12le", "gbrp14le", "gbrp16le", "gbrpf32le",
    "gbrap", "gbrap10le", "gbrap12le", "gbrap16le", "gbrapf32le",
    "yuv422p", "yuvj422p", "yuv422p9le", "yuv422p10le", "yuv422p12le", "yuv422p14le", "yuv422p16le",
    "yuva422p", "yuva422p9le", "yuva422p10le", "yuva422p12le", "yuva422p16le",
    "yuv440p", "yuvj440p", "yuv440p12le", "yuv411p", "yuvj411p", "yuv410p",
})


def blur_parameters(operation: dict) -> tuple[float, int]:
    settings = operation.get("blur") or {}
    sigma = max(1.0, float(settings.get("sigma", DEFAULT_SIGMA)))
    feather = max(0, int(settings.get("edge_feather_pixels", 0)))
    return sigma, feather


def blur_method(operation: dict) -> str:
    settings = operation.get("blur") or {}
    method = str(settings.get("method", GBLUR))
    if method not in BLUR_METHODS:
        raise ValueError(f"Unsupported blur method: {method}")
    return method


@dataclass(frozen=True)
class SourceChroma:
    """The chroma step a segment's first regional branch crops the source with.

    ``first`` when no full-frame blur comes before that branch in its segment,
    ``after_full_frame`` when one does. Measured on FFmpeg 9.0.1 for every pixel
    format it can make (docs/evidence/cover-edge/crop-steps.jsonl).
    """

    first: tuple[int, int] = CHROMA_420
    after_full_frame: tuple[int, int] = CHROMA_420

    @classmethod
    def of(cls, pixel_format: str | None) -> SourceChroma:
        """The steps for a source of the probed ``pixel_format`` that FFmpeg neither turns nor flips.

        A format with no probed name gets NO_ROUNDING: the cover chain of each
        segment's first regional branch stays the one of before 2026-10-14. (Every
        later branch crops 4:2:0 whatever the source, as it did then.)
        """
        if not _known_format(pixel_format):
            return cls(NO_ROUNDING, NO_ROUNDING)
        if pixel_format in _CONVERTED_BEFORE_CROP:
            return cls(CHROMA_420, CHROMA_420)
        first = _own_chroma_step(pixel_format)
        kept = first == CHROMA_420 or pixel_format in _KEPT_THROUGH_GBLUR
        return cls(first, first if kept else CHROMA_420)

    @classmethod
    def of_stream(cls, video_stream: dict) -> SourceChroma:
        """The steps for the probed video stream (``pix_fmt`` and its side data).

        The export leaves FFmpeg's autorotate on: a stream whose display matrix
        FFmpeg applies (display_transform) reaches the graph through transpose,
        hflip, vflip or rotate, and its first regional branch then crops 4:2:0
        whatever the format (docs/evidence/cover-edge/crop-steps-transformed.jsonl).
        A display matrix the probe does not show plainly, or an applied one on a
        stream of unknown format, gets NO_ROUNDING.
        """
        pixel_format = video_stream.get("pix_fmt")
        transformed = display_transform(video_stream)
        if transformed is None or (transformed and not _known_format(pixel_format)):
            return cls(NO_ROUNDING, NO_ROUNDING)
        if transformed:
            return cls(CHROMA_420, CHROMA_420)
        return cls.of(pixel_format)


def _known_format(pixel_format: str | None) -> bool:
    return bool(pixel_format) and pixel_format != "unknown"


def display_transform(video_stream: dict) -> bool | None:
    """Whether FFmpeg turns or flips the probed stream's pictures before the export's graph (None: the probe cannot tell).

    As fftools 9.0.1 decides it from the stream's (first) display matrix: transpose
    at 90 and 270 degrees, hflip and/or vflip at 180, rotate beyond 1 degree, vflip
    for a flipped matrix at 0, nothing otherwise (a matrix that only moves or
    scales, an angle that rounds to 0 or 1 degree). FFmpeg 9 reads no "rotate" tag.
    """
    item = next((item for item in video_stream.get("side_data_list") or []
                 if item.get("side_data_type") == "Display Matrix"), None)
    if item is None:
        return False
    entries = re.findall(r"-?\d+", _DISPLAY_MATRIX_OFFSET.sub("", str(item.get("displaymatrix", ""))))
    if len(entries) != _DISPLAY_MATRIX_ENTRIES:
        return None
    matrix = [int(entry) for entry in entries]
    theta = _ffmpeg_rotation(matrix)
    if abs(theta - 90) < 1 or abs(theta - 270) < 1:
        return True
    if abs(theta - 180) < 1:
        return matrix[0] < 0 or matrix[4] < 0
    if abs(theta) > 1:
        return True
    if abs(theta) < 1:
        return matrix[4] < 0
    return False  # 1 degree exactly, or a matrix without a scale (NaN): FFmpeg inserts nothing


def _ffmpeg_rotation(matrix: list[int]) -> float:
    """fftools' get_rotation: av_display_rotation_get rounded to whole degrees, in [-0.9, 359.1) (NaN without a scale)."""
    scale_x = math.hypot(matrix[0] / _FIXED_ONE, matrix[3] / _FIXED_ONE)
    scale_y = math.hypot(matrix[1] / _FIXED_ONE, matrix[4] / _FIXED_ONE)
    if scale_x == 0 or scale_y == 0:
        return math.nan
    rotation = -math.degrees(math.atan2(matrix[1] / _FIXED_ONE / scale_y, matrix[0] / _FIXED_ONE / scale_x))
    theta = -math.copysign(math.floor(abs(rotation) + 0.5), rotation)  # C round(): half away from zero
    return theta - 360 * math.floor(theta / 360 + 0.9 / 360)


def _own_chroma_step(pixel_format: str) -> tuple[int, int]:
    """Pixels per chroma sample of ``pixel_format`` itself; NO_ROUNDING for one without subsampled chroma."""
    planar = _PLANAR_YUV.fullmatch(pixel_format)
    if planar and planar[1] in _SUBSAMPLING:
        return _SUBSAMPLING[planar[1]]
    semi_planar = _SEMI_PLANAR.fullmatch(pixel_format)
    if semi_planar:
        return _SEMI_PLANAR_STEPS[semi_planar[1] or semi_planar[2]]
    return NO_ROUNDING


def cover_sigma(region: dict) -> int:
    """Blur strength of the cover method for one region (never below the default)."""
    side = min(int(region["width"]), int(region["height"]))
    return max(DEFAULT_SIGMA, round(side * COVER_SIGMA_FRACTION))


@dataclass(frozen=True)
class _Patch:
    """Where a branch cuts its picture and lays it back: the region, or the cover's widened one."""

    x: int
    y: int
    width: int
    height: int
    # crop exact=1: no rounding down to whole chroma samples.
    exact: bool = False
    # Sides on the frame's edge ("left", "right", "top", "bottom"): the feather never fades them.
    frame_edges: frozenset[str] = frozenset()


def _cover_window(
    x: int, y: int, width: int, height: int, frame_size: tuple[int, int] | None,
) -> tuple[int, int, int, int, tuple[int, int, int, int] | None]:
    """The cover chain's crop (margin left, margin top, width, height) around a box, and delogo's rectangle in it.

    delogo refuses a rectangle that touches the edge of its picture, so the rectangle
    keeps one pixel of the margin on every side; where the box touches the frame
    edge (no margin there) that one pixel row of the box is not filled: it is only
    blurred, and where a feather fades that side it shows the source's own pixels.
    No rectangle (None) when the box is too small to keep one.
    """
    left, top = min(COVER_MARGIN_PIXELS, x), min(COVER_MARGIN_PIXELS, y)
    if frame_size is None:
        right = bottom = 0
    else:
        right = max(0, min(COVER_MARGIN_PIXELS, int(frame_size[0]) - x - width))
        bottom = max(0, min(COVER_MARGIN_PIXELS, int(frame_size[1]) - y - height))
    crop_width, crop_height = width + left + right, height + top + bottom
    logo_x, logo_y = max(1, left), max(1, top)
    logo_width = min(crop_width - 1, left + width) - logo_x
    logo_height = min(crop_height - 1, top + height) - logo_y
    logo = (logo_x, logo_y, logo_width, logo_height) if logo_width >= 1 and logo_height >= 1 else None
    return left, top, crop_width, crop_height, logo


def _cover_patch(
    region: dict, frame_size: tuple[int, int] | None, chroma_step: tuple[int, int],
) -> _Patch:
    """Where the cover method works on ``region``: the region itself wherever FFmpeg ran it before 2026-10-14.

    crop rounds its size down to whole chroma samples (``chroma_step``) of the
    picture it crops. Where that cut off the column or row past delogo's rectangle
    (an odd size with at most one pixel of picture past the region's right or
    bottom; up to three for 4:1:0), delogo refused the whole graph on every try
    (job 141). Such a region is widened to the even pixel left of and above it and
    cropped exactly: overlay lays a patch at even x and y only, and an exact crop at
    an odd x reads a 10-bit chroma plane from the wrong bytes (FFmpeg 9.0.1). The
    patch then reaches the region's right and bottom, the frame's edge included,
    and the feather leaves a side on the frame's edge unfaded.
    """
    x, y = int(region["x"]), int(region["y"])
    width, height = int(region["width"]), int(region["height"])
    _, _, crop_width, crop_height, logo = _cover_window(x, y, width, height, frame_size)
    step_x, step_y = chroma_step
    if logo is None or (
        logo[0] + logo[2] < crop_width - crop_width % step_x
        and logo[1] + logo[3] < crop_height - crop_height % step_y
    ):
        return _Patch(x, y, width, height)
    patch_x, patch_y = x - x % 2, y - y % 2
    patch_width, patch_height = x + width - patch_x, y + height - patch_y
    edges = {"left"} if patch_x == 0 else set()
    if patch_y == 0:
        edges.add("top")
    if frame_size is not None:
        if patch_x + patch_width >= int(frame_size[0]):
            edges.add("right")
        if patch_y + patch_height >= int(frame_size[1]):
            edges.add("bottom")
    return _Patch(patch_x, patch_y, patch_width, patch_height, exact=True, frame_edges=frozenset(edges))


def _cover_chain(patch: _Patch, frame_size: tuple[int, int] | None, enable: str) -> str:
    """Crop the patch with a margin of picture, fill it with delogo, crop back to the patch."""
    left, top, crop_width, crop_height, logo = _cover_window(
        patch.x, patch.y, patch.width, patch.height, frame_size,
    )
    exact = ":exact=1" if patch.exact else ""
    chain = f"crop={crop_width}:{crop_height}:{patch.x - left}:{patch.y - top}{exact},"
    if logo is not None:
        logo_x, logo_y, logo_width, logo_height = logo
        chain += f"delogo=x={logo_x}:y={logo_y}:w={logo_width}:h={logo_height}:{enable},"
    return chain + f"crop={patch.width}:{patch.height}:{left}:{top}{exact}"


def _feather_distance(feather_mode: str, frame_edges: frozenset[str]) -> str:
    """geq's distance from a pixel to the nearest side the feather fades ("" when none is faded)."""
    if feather_mode == "vertical_only":
        groups = ((("top", "Y"), ("bottom", "H-1-Y")),)
    elif feather_mode == "all_edges":
        groups = ((("left", "X"), ("right", "W-1-X")), (("top", "Y"), ("bottom", "H-1-Y")))
    else:
        raise ValueError(f"Unsupported blur feather mode: {feather_mode}")
    parts = [_minimum([term for side, term in group if side not in frame_edges]) for group in groups]
    return _minimum([part for part in parts if part])


def _minimum(terms: list[str]) -> str:
    """geq's min of one or two terms ("" for none)."""
    if len(terms) == 2:
        return f"min({terms[0]}\\,{terms[1]})"
    return terms[0] if terms else ""


def _gblur(sigma: float, height: int, enable: str, chroma_rows_divisor: int = 2) -> str:
    """gblur of one region, never with more threads than its chroma plane has rows.

    FFmpeg's gblur gives wrong, run-dependent pixels when a plane has fewer rows
    than its threads (docs/evidence/perf-export/gblur-threads.md). crop rounds the
    height down to whole chroma rows, so a 4:2:0 branch has height // 2 chroma rows.
    For a yuv410p source the cap is height // 4 (perf plan A3): FFmpeg keeps that
    format through gblur and converts only for the overlay (FFmpeg 9.0.1), so its
    chroma plane has height // 4 rows. FFmpeg uses the smaller of this cap and its
    own thread count.
    A region with enough rows keeps its bytes. As measured on an AVX2 CPU with
    regions at least 128 pixels wide, a thin one now comes out as with one thread,
    the same on every run; the evidence file lists what was not measured.
    """
    if chroma_rows_divisor not in (2, 4):
        raise ValueError(f"Unsupported chroma rows divisor: {chroma_rows_divisor}")
    return f"gblur=sigma={sigma:g}:threads={max(1, height // chroma_rows_divisor)}:{enable}"


def blur_feather_mode(operation: dict) -> str:
    settings = operation.get("blur") or {}
    mode = str(settings.get("edge_feather_mode", "all_edges"))
    if mode not in {"all_edges", "vertical_only"}:
        raise ValueError(f"Unsupported blur feather mode: {mode}")
    return mode


def regional_blur_paths(*, feather: int, method: str) -> int:
    """Paths through one region's branch from its crop input to its overlay (the picture's own path not counted).

    Each is one more way a frame reaches the export's output (blur_layout): the
    plain blur has one, a feathered edge adds the mask, and the cover method's
    feathered edge also lays the blur on the cleaned region.
    """
    if feather <= 0:
        return 1
    return 3 if method == COVER_METHOD else 2


def regional_blur_filters(
    *, input_label: str, output_label: str, prefix: str,
    region: dict, sigma: float, feather: int, enable: str,
    feather_mode: str = "all_edges", method: str = GBLUR,
    frame_size: tuple[int, int] | None = None, chroma_rows_divisor: int = 2,
    chroma_step: tuple[int, int] = CHROMA_420,
) -> list[str]:
    """The split/crop/blur/overlay chains hiding one region while ``enable`` holds.

    ``enable`` (an FFmpeg timeline option, ``enable='...'``) gates the final overlay
    and, since perf plan A2, also the costly per-frame filters of the branch
    (delogo, gblur): outside the blur times they pass their frame through
    untouched and the overlay shows the unchanged picture, so every output frame
    is the same as when the branch ran on every frame. The feather mask is made
    once per branch (perf plan phase 2, step 2).
    """
    base = f"{prefix}base"
    crop = f"{prefix}crop"
    branch = regional_blur_branch(
        base_label=base, crop_label=crop, output_label=output_label, prefix=prefix,
        region=region, sigma=sigma, feather=feather, enable=enable,
        feather_mode=feather_mode, method=method, frame_size=frame_size,
        chroma_rows_divisor=chroma_rows_divisor, chroma_step=chroma_step,
    )
    return [f"[{input_label}]split[{base}][{crop}]", *branch]


def regional_blur_branch(
    *, base_label: str, crop_label: str, output_label: str, prefix: str,
    region: dict, sigma: float, feather: int, enable: str,
    feather_mode: str = "all_edges", method: str = GBLUR,
    frame_size: tuple[int, int] | None = None, chroma_rows_divisor: int = 2,
    chroma_step: tuple[int, int] = CHROMA_420,
) -> list[str]:
    """regional_blur_filters without its split: the region is cut from ``crop_label`` and laid on ``base_label``.

    The filters are those of regional_blur_filters, character for character;
    blur_layout chooses two labels that carry the same pixels wherever and
    whenever the branch reads.
    """
    x, y = int(region["x"]), int(region["y"])
    width, height = int(region["width"]), int(region["height"])
    if width <= 0 or height <= 0:
        raise ValueError("Blur region must have positive width and height")
    if method not in BLUR_METHODS:
        raise ValueError(f"Unsupported blur method: {method}")
    if method == COVER_METHOD:
        cut = _cover_patch(region, frame_size, chroma_step)
        region_chain = _cover_chain(cut, frame_size, enable)
        x, y, width, height = cut.x, cut.y, cut.width, cut.height
    else:
        cut = _Patch(x, y, width, height)
        region_chain = f"crop={width}:{height}:{x}:{y}"
    base = base_label
    crop = crop_label
    blurred = f"{prefix}blurred"
    filters: list[str] = []
    if feather <= 0:
        filters.append(
            f"[{crop}]{region_chain},{_gblur(sigma, height, enable, chroma_rows_divisor)}[{blurred}]"
        )
        filters.append(
            f"[{base}][{blurred}]overlay={x}:{y}:{enable}[{output_label}]"
        )
        return filters

    colour_source = f"{prefix}coloursource"
    colour = f"{prefix}colour"
    mask_source = f"{prefix}masksource"
    mask = f"{prefix}mask"
    soft = f"{prefix}soft"
    safe_feather = min(feather, max(1, min(width, height) // 3))
    # A patch on every frame edge is not faded at all.
    distance = _feather_distance(feather_mode, cut.frame_edges) or str(safe_feather)
    if method == COVER_METHOD:
        # The feathered edge fades into the cleaned region, never into the logo.
        clean = f"{prefix}clean"
        blur_source = f"{prefix}blursource"
        patch = f"{prefix}patch"
        filters.append(f"[{crop}]{region_chain},split[{clean}][{blur_source}]")
        filters.append(
            f"[{blur_source}]{_gblur(sigma, height, enable, chroma_rows_divisor)},split[{colour_source}][{mask_source}]"
        )
    else:
        filters.append(
            f"[{crop}]{region_chain},{_gblur(sigma, height, enable, chroma_rows_divisor)},"
            f"split[{colour_source}][{mask_source}]"
        )
    # Pin the overlay branch to a colour format. Without this explicit
    # conversion FFmpeg can negotiate gray upstream from the mask branch and
    # silently desaturate the complete output frame.
    filters.append(f"[{colour_source}]format=yuv420p[{colour}]")
    # The mask depends only on the region's size (X, Y, W, H), so geq makes it
    # once, from the branch's first frame (never gated: that frame may fall
    # outside the blur times; no setpts: it keeps the colour frame's time), and
    # alphamerge repeats that frame for every later one: the same pixels as a
    # mask made per frame.
    filters.append(
        f"[{mask_source}]trim=end_frame=1,format=gray,"
        f"geq=lum='255*min(1\\,{distance}/{safe_feather})'[{mask}]"
    )
    filters.append(f"[{colour}][{mask}]{MASK_REPEAT_ALPHAMERGE}[{soft}]")
    if method == COVER_METHOD:
        filters.append(f"[{clean}][{soft}]overlay=0:0[{patch}]")
        soft = patch
    filters.append(
        f"[{base}][{soft}]overlay={x}:{y}:{enable}[{output_label}]"
    )
    return filters
