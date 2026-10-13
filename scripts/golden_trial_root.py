"""A throw-away BiliFlow root for Golden clip trials (docs/handoff/G-golden-clips.md).

The localize_logo stage reads only a video inside <project root>/input, and Golden clips must never
go in the real input (the watcher would make jobs of them). A trial root under <install>/temp holds:
  - a copy of the clip set in its own input/golden/<set> (clips and label files, SHA-256 checked);
  - copies of scripts, config and the files the stages read, and a read-only snapshot of the state
    they read (brand and studio-logo memory, the job database), so nothing writes to the real state;
  - junctions to the install's code, models, tools, runtime, caches and reports, so the stages run the
    same code on the same models and write their reports where whole-film trials write theirs.
remove() takes the junctions down first (os.rmdir removes a junction, never its target) and deletes
nothing while any link is left. Windows only (junctions).
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import stat
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

PREFIX = "golden-trial-root-"
LINKS = ("src", "models", "tools", "runtime", "cache", "reports", ".venv")
COPIES = ("scripts", "config", "pyproject.toml", "requirements.lock.txt",
          "annotations/text_semantics_seed_v1.json", "annotations/review-regression-v1.json",
          "state/brand-memory.json", "state/studio-logo-memory.json", "state/studio-logo-frames")
DATABASE = "state/control-center.sqlite3"
EMPTY = ("input", "work", "previews", "logs", "temp", "output")
IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc")


def _is_link(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    return bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT) or stat.S_ISLNK(info.st_mode)


def _junction(target: Path, link: Path) -> None:
    import _winapi
    _winapi.CreateJunction(str(target), str(link))


def _snapshot(database: Path, target: Path) -> None:
    """Copy a SQLite database through a read-only connection (WAL included)."""
    source = sqlite3.connect(f"file:{quote(database.as_posix())}?mode=ro", uri=True)
    try:
        copy = sqlite3.connect(target)
        try:
            source.backup(copy)
        finally:
            copy.close()
    finally:
        source.close()


def _copy_set(golden_root: Path, set_name: str, target: Path) -> None:
    from biliflow.golden_set import file_sha256, read_json
    source = golden_root / set_name
    (target / "media").mkdir(parents=True)
    names = [path.name for path in source.iterdir() if path.is_file()]
    names += [value["path"] for value in read_json(source / "segments.json")["sources"].values()]
    for name in names:
        shutil.copy2(source / name, target / name)
        if file_sha256(source / name) != file_sha256(target / name):
            raise RuntimeError(f"copy of {name} differs from the Golden set")


def _check_place(install: Path, root: Path) -> None:
    if root.parent != install / "temp" or not root.name.startswith(PREFIX):
        raise ValueError(f"{root} is not a Golden trial root in {install / 'temp'}")


def create(install: Path, golden_root: Path, set_name: str) -> Path:
    """Make <install>/temp/golden-trial-root-<time>; the clip set is at <root>/input/golden/<set_name>."""
    install = Path(install).resolve(strict=True)
    root = install / "temp" / (PREFIX + datetime.now().strftime("%Y%m%d-%H%M%S"))
    root.mkdir()
    try:
        for name in LINKS:
            if (install / name).is_dir():
                _junction(install / name, root / name)
        for name in COPIES:
            source, target = install / name, root / name
            if source.is_dir():
                shutil.copytree(source, target, ignore=IGNORED)
            elif source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        if (install / DATABASE).is_file():
            _snapshot(install / DATABASE, root / DATABASE)
        for name in EMPTY:
            (root / name).mkdir(exist_ok=True)
        _copy_set(Path(golden_root), set_name, root / "input" / "golden" / set_name)
    except BaseException:
        remove(root, install)
        raise
    return root


def _left_links(folder: Path) -> list[Path]:
    """Every link or junction under ``folder``, without entering any of them."""
    found, pending = [], [folder]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                path = Path(entry.path)
                if _is_link(path):
                    found.append(path)
                elif entry.is_dir(follow_symlinks=False):
                    pending.append(path)
    return found


def remove(root: Path, install: Path) -> None:
    """Take the junctions down, then delete the trial root; refuses while any link is left."""
    install = Path(install).resolve(strict=True)
    root = Path(root)
    _check_place(install, root)
    if not root.exists():
        return
    for name in LINKS:
        if _is_link(root / name):
            os.rmdir(root / name)  # removes the junction itself, never the folder it points to
    left = _left_links(root)
    if left:
        raise RuntimeError(f"links left in the trial root, nothing deleted: {[str(p) for p in left]}")
    shutil.rmtree(root)
