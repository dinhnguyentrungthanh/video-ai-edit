"""File names of the episodes of a group (docs/SOURCE_ACCOUNTS_PLAN.md 9.14): ``NNN - <film> - <episode code>``.

- ``NNN``: the episode's place in its group (download_account_listing order), zero-padded to the same width for
  the whole group (at least three digits), so a sort by name keeps the group's order whatever order the
  episodes finished in.
- The episode code comes from the numbers the source gave, never a guess: ``S01E05`` (season and episode
  number), ``E05`` (no season number), ``SP03`` / ``S01SP03`` (a special with a number), else the episode's own
  label (``SP <label>`` for a special).
- The whole name keeps the downloader's limit (download_files.MAX_NAME_LENGTH, extension included). Only the
  film part is cut, so the prefix and the code always stay; room is kept for the " (N)" of a taken name.
- The usual rules of ``sanitize_name`` (forbidden characters, reserved device names) apply to each part; a
  file is never overwritten (``group_target`` takes the first free name, case-insensitively as NTFS).
"""
from __future__ import annotations

import re
from pathlib import Path

from biliflow.download_files import MAX_NAME_LENGTH, sanitize_name

MIN_WIDTH = 3
MAX_CODE_CHARS = 40
MAX_MARKERS = 10000
SEPARATOR = " - "
_TAKEN_MARKER_RESERVE = len(f" ({MAX_MARKERS - 1})")


def ordinal_width(total: int) -> int:
    """Digits of the group's ordinal prefix: wide enough for ``total``, at least three."""
    return max(MIN_WIDTH, len(str(max(int(total), 1))))


def episode_code(*, season_number: int | None, episode_number: int | None, special: bool, label: str) -> str:
    """The code of one episode from the source's own numbers (see the module docstring)."""
    season = f"S{season_number:02d}" if season_number is not None else ""
    if episode_number is not None:
        return f"{season}{'SP' if special else 'E'}{episode_number:02d}"
    text = _part(label, MAX_CODE_CHARS) or "?"
    return f"SP {text}" if special else text


def _part(text: str, limit: int) -> str:
    cleaned = sanitize_name(text or "", fallback=None)
    return cleaned[:limit].rstrip(". ")


def group_stem(ordinal: int, width: int, film: str, code: str, *, suffix: str, reserve: int = 0) -> str:
    """``NNN - <film> - <code>`` within the name limit (``suffix`` and ``reserve`` characters left free); only the
    film is cut. A film part that cleans to nothing is left out (``NNN - <code>``)."""
    prefix = f"{int(ordinal):0{int(width)}d}"
    code_part = _part(code, MAX_CODE_CHARS) or "?"
    room = MAX_NAME_LENGTH - len(suffix) - reserve - len(prefix) - len(code_part) - 2 * len(SEPARATOR)
    film_part = _part(film, max(room, 0)) if room > 0 else ""
    parts = [prefix, film_part, code_part] if film_part else [prefix, code_part]
    return re.sub(r"\s+", " ", SEPARATOR.join(parts)).rstrip(". ")


def planned_stem(ordinal: int, width: int, film: str, code: str, suffix: str = ".mp4") -> str:
    """The name ``group_target`` gives when it is free (what a page shows before the file exists)."""
    return group_stem(ordinal, width, film, code, suffix=suffix, reserve=_TAKEN_MARKER_RESERVE)


def group_target(directory: Path, ordinal: int, width: int, film: str, code: str, suffix: str) -> Path:
    """The first free ``<stem><suffix>``, ``<stem> (2)<suffix>``… in ``directory``; never an existing file."""
    taken = {entry.name.casefold() for entry in directory.iterdir()} if directory.is_dir() else set()
    stem = planned_stem(ordinal, width, film, code, suffix)
    for number in range(1, MAX_MARKERS):
        marker = "" if number == 1 else f" ({number})"
        name = f"{stem}{marker}{suffix}"
        if name.casefold() not in taken:
            return directory / name
    raise RuntimeError(f"Không còn tên trống cho tập {ordinal} trong {directory}.")
