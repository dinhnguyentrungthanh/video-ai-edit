"""Where the BiliFlow install folder is: the folder that holds the install marker.

The marker ``biliflow-install.json`` sits at the root of every install: committed
at the root of a source checkout (so each worktree is its own root, as before)
and written by the installer. Neither an environment variable nor a parameter
can move the root. The search looks only a few levels above the package, so a
checkout under ``<install>\\temp`` that lost its marker never borrows the
install's root; it fails instead.
"""
from __future__ import annotations

import json
import os
import stat
from functools import lru_cache
from pathlib import Path


MARKER_NAME = "biliflow-install.json"
MARKER_KEY = "biliflow_install"
MARKER_VERSION = 1
MAX_MARKER_BYTES = 4096
# Folders checked above the package folder: src, the checkout root, and one more
# for a build layout. The install root of a checkout is parents[2] of a module.
MAX_LEVELS_ABOVE_PACKAGE = 3
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
NOT_FOUND_MESSAGE = (
    "Không tìm thấy file đánh dấu thư mục cài BiliFlow ({name}) ở gốc thư mục cài; "
    "BiliFlow không chạy khi không biết thư mục cài."
)


class InstallRootNotFound(RuntimeError):
    pass


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
    )


def is_marker(path: Path) -> bool:
    """A regular file (not a link) holding ``{"biliflow_install": 1, ...}``."""
    try:
        info = os.lstat(path)
        if _is_link(info) or not stat.S_ISREG(info.st_mode) or info.st_size > MAX_MARKER_BYTES:
            return False
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    version = data.get(MARKER_KEY) if isinstance(data, dict) else None
    return type(version) is int and version == MARKER_VERSION


def find_install_root(module_file: Path) -> Path | None:
    """The nearest folder above ``module_file``'s package that holds a valid marker."""
    try:
        parents = Path(module_file).resolve().parents
    except (OSError, RuntimeError):
        return None
    for level in range(1, MAX_LEVELS_ABOVE_PACKAGE + 1):
        if level >= len(parents):
            break
        # ``resolve()`` already followed every link of the path itself.
        folder = parents[level]
        if is_marker(folder / MARKER_NAME):
            return folder
    return None


@lru_cache(maxsize=1)
def install_root() -> Path:
    """The install folder of the running code; raises when no marker is found."""
    found = find_install_root(Path(__file__))
    if found is None:
        raise InstallRootNotFound(NOT_FOUND_MESSAGE.format(name=MARKER_NAME))
    return found
