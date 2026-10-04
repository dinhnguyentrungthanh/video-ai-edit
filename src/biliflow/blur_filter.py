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


def _cover_chain(region: dict, frame_size: tuple[int, int] | None) -> str:
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
        chain += f"delogo=x={logo_x}:y={logo_y}:w={logo_width}:h={logo_height},"
    return chain + f"crop={width}:{height}:{left}:{top}"


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
    x, y = int(region["x"]), int(region["y"])
    width, height = int(region["width"]), int(region["height"])
    if width <= 0 or height <= 0:
        raise ValueError("Blur region must have positive width and height")
    if method not in BLUR_METHODS:
        raise ValueError(f"Unsupported blur method: {method}")
    region_chain = (
        _cover_chain(region, frame_size) if method == COVER_METHOD
        else f"crop={width}:{height}:{x}:{y}"
    )
    base = f"{prefix}base"
    crop = f"{prefix}crop"
    blurred = f"{prefix}blurred"
    filters = [f"[{input_label}]split[{base}][{crop}]"]
    if feather <= 0:
        filters.append(
            f"[{crop}]{region_chain},gblur=sigma={sigma:g}[{blurred}]"
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
            f"[{blur_source}]gblur=sigma={sigma:g},split[{colour_source}][{mask_source}]"
        )
    else:
        filters.append(
            f"[{crop}]{region_chain},gblur=sigma={sigma:g},"
            f"split[{colour_source}][{mask_source}]"
        )
    # Pin the overlay branch to a colour format. Without this explicit
    # conversion FFmpeg can negotiate gray upstream from the mask branch and
    # silently desaturate the complete output frame.
    filters.append(f"[{colour_source}]format=yuv420p[{colour}]")
    filters.append(
        f"[{mask_source}]format=gray,"
        f"geq=lum='255*min(1\\,{distance}/{safe_feather})'[{mask}]"
    )
    filters.append(f"[{colour}][{mask}]alphamerge[{soft}]")
    if method == COVER_METHOD:
        filters.append(f"[{clean}][{soft}]overlay=0:0[{patch}]")
        soft = patch
    filters.append(
        f"[{base}][{soft}]overlay={x}:{y}:{enable}[{output_label}]"
    )
    return filters
