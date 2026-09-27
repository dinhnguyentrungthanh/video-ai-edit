from __future__ import annotations


def blur_parameters(operation: dict) -> tuple[float, int]:
    settings = operation.get("blur") or {}
    sigma = max(1.0, float(settings.get("sigma", 28)))
    feather = max(0, int(settings.get("edge_feather_pixels", 0)))
    return sigma, feather


def blur_feather_mode(operation: dict) -> str:
    settings = operation.get("blur") or {}
    mode = str(settings.get("edge_feather_mode", "all_edges"))
    if mode not in {"all_edges", "vertical_only"}:
        raise ValueError(f"Unsupported blur feather mode: {mode}")
    return mode


def regional_blur_filters(
    *, input_label: str, output_label: str, prefix: str,
    region: dict, sigma: float, feather: int, enable: str,
    feather_mode: str = "all_edges",
) -> list[str]:
    x, y = int(region["x"]), int(region["y"])
    width, height = int(region["width"]), int(region["height"])
    if width <= 0 or height <= 0:
        raise ValueError("Blur region must have positive width and height")
    base = f"{prefix}base"
    crop = f"{prefix}crop"
    blurred = f"{prefix}blurred"
    filters = [f"[{input_label}]split[{base}][{crop}]"]
    if feather <= 0:
        filters.append(
            f"[{crop}]crop={width}:{height}:{x}:{y},gblur=sigma={sigma:g}[{blurred}]"
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
    filters.append(
        f"[{crop}]crop={width}:{height}:{x}:{y},gblur=sigma={sigma:g},"
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
    filters.append(
        f"[{base}][{soft}]overlay={x}:{y}:{enable}[{output_label}]"
    )
    return filters
