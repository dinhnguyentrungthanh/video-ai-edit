from __future__ import annotations

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


def cover_sigma(region: dict) -> int:
    """Blur strength of the cover method for one region (never below the default)."""
    side = min(int(region["width"]), int(region["height"]))
    return max(DEFAULT_SIGMA, round(side * COVER_SIGMA_FRACTION))


def _cover_chain(region: dict, frame_size: tuple[int, int] | None, enable: str) -> str:
    """Crop the region with a margin of picture, fill it with delogo, crop back to the region.

    delogo refuses a rectangle that touches the image edge, so the rectangle keeps one
    pixel of the margin on every side; where the region touches the frame edge (no
    margin there) that one pixel row of the region stays and is only blurred.
    """
    x, y = int(region["x"]), int(region["y"])
    width, height = int(region["width"]), int(region["height"])
    left, top = min(COVER_MARGIN_PIXELS, x), min(COVER_MARGIN_PIXELS, y)
    if frame_size is None:
        right = bottom = 0
    else:
        right = max(0, min(COVER_MARGIN_PIXELS, int(frame_size[0]) - x - width))
        bottom = max(0, min(COVER_MARGIN_PIXELS, int(frame_size[1]) - y - height))
    crop_width, crop_height = width + left + right, height + top + bottom
    chain = f"crop={crop_width}:{crop_height}:{x - left}:{y - top},"
    logo_x, logo_y = max(1, left), max(1, top)
    logo_width = min(crop_width - 1, left + width) - logo_x
    logo_height = min(crop_height - 1, top + height) - logo_y
    if logo_width >= 1 and logo_height >= 1:
        chain += f"delogo=x={logo_x}:y={logo_y}:w={logo_width}:h={logo_height}:{enable},"
    return chain + f"crop={width}:{height}:{left}:{top}"


def _gblur(sigma: float, height: int, enable: str) -> str:
    """gblur of one region, never with more threads than its chroma plane has rows.

    FFmpeg's gblur gives wrong, run-dependent pixels when a plane has fewer rows
    than its threads (docs/evidence/perf-export/gblur-threads.md). The branch is
    yuv420p and crop rounds an odd height down, so the chroma plane has
    height // 2 rows. FFmpeg uses the smaller of this cap and its own thread count,
    and every count up to the row count gives the same pixels: a region with enough
    rows keeps its bytes, a thin one comes out right and the same on every machine.
    """
    return f"gblur=sigma={sigma:g}:threads={max(1, height // 2)}:{enable}"


def blur_feather_mode(operation: dict) -> str:
    settings = operation.get("blur") or {}
    mode = str(settings.get("edge_feather_mode", "all_edges"))
    if mode not in {"all_edges", "vertical_only"}:
        raise ValueError(f"Unsupported blur feather mode: {mode}")
    return mode


def regional_blur_filters(
    *, input_label: str, output_label: str, prefix: str,
    region: dict, sigma: float, feather: int, enable: str,
    feather_mode: str = "all_edges", method: str = GBLUR,
    frame_size: tuple[int, int] | None = None,
) -> list[str]:
    """The split/crop/blur/overlay chains hiding one region while ``enable`` holds.

    ``enable`` (an FFmpeg timeline option, ``enable='...'``) gates the final overlay
    and, since perf plan A2, also the costly per-frame filters of the branch
    (delogo, gblur): outside the blur times they pass their frame through
    untouched and the overlay shows the unchanged picture, so every output frame
    is the same as when the branch ran on every frame. The feather mask is made
    once per branch (perf plan phase 2, step 2).
    """
    x, y = int(region["x"]), int(region["y"])
    width, height = int(region["width"]), int(region["height"])
    if width <= 0 or height <= 0:
        raise ValueError("Blur region must have positive width and height")
    if method not in BLUR_METHODS:
        raise ValueError(f"Unsupported blur method: {method}")
    region_chain = (
        _cover_chain(region, frame_size, enable) if method == COVER_METHOD
        else f"crop={width}:{height}:{x}:{y}"
    )
    base = f"{prefix}base"
    crop = f"{prefix}crop"
    blurred = f"{prefix}blurred"
    filters = [f"[{input_label}]split[{base}][{crop}]"]
    if feather <= 0:
        filters.append(
            f"[{crop}]{region_chain},{_gblur(sigma, height, enable)}[{blurred}]"
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
    if feather_mode == "vertical_only":
        distance = "min(Y\\,H-1-Y)"
    elif feather_mode == "all_edges":
        distance = "min(min(X\\,W-1-X)\\,min(Y\\,H-1-Y))"
    else:
        raise ValueError(f"Unsupported blur feather mode: {feather_mode}")
    if method == COVER_METHOD:
        # The feathered edge fades into the cleaned region, never into the logo.
        clean = f"{prefix}clean"
        blur_source = f"{prefix}blursource"
        patch = f"{prefix}patch"
        filters.append(f"[{crop}]{region_chain},split[{clean}][{blur_source}]")
        filters.append(
            f"[{blur_source}]{_gblur(sigma, height, enable)},split[{colour_source}][{mask_source}]"
        )
    else:
        filters.append(
            f"[{crop}]{region_chain},{_gblur(sigma, height, enable)},"
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
