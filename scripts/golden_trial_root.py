"""A throw-away BiliFlow root for Golden clip trials (docs/handoff/G-golden-clips.md).

The localize_logo stage reads only a video inside <project root>/input, and Golden clips must never
go in the real input (the watcher would make jobs of them). The stages keep every path inside their
root after resolving it, so the trial root is a root of its own, like a checkout (it has its own
install marker), under <install>/temp:
  - a copy of the clip set in its own input/golden/<set> (clips and label files, SHA-256 checked);
  - copies of src, scripts, config and the files the stages read, and a read-only snapshot of the
    state they read (brand and studio-logo memory, the job database): nothing writes to the real state;
  - models, tools, runtime and the Hugging Face cache as folders of hard links to the install's files
    (no copy, no link to resolve outside the root; a junction inside them is made again inside the
    trial root), and a junction to the install's .venv (which runs the install's runtime anyway);
  - its own reports folder: harvest() moves each trial's folders into the install's reports/benchmarks
    and reports/jobs, where whole-film trials keep theirs.
remove() takes every link down without entering it (os.rmdir removes a junction, never its target;
deleting a hard link leaves the install's file) and then deletes the trial root. Windows only.
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
MARKER = "biliflow-install.json"
JUNCTIONS = (".venv",)
LINKED_TREES = ("models", "tools", "runtime", "cache/huggingface")
COPIES = (MARKER, "src", "scripts", "config", "pyproject.toml", "requirements.lock.txt",
          "annotations/text_semantics_seed_v1.json", "annotations/review-regression-v1.json",
          "state/brand-memory.json", "state/studio-logo-memory.json", "state/studio-logo-frames")
DATABASE = "state/control-center.sqlite3"
EMPTY = ("input", "work", "previews", "logs", "temp", "output", "reports/benchmarks", "reports/jobs")
HARVESTED = ("reports/benchmarks", "reports/jobs")
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


def _link_tree(source: Path, target: Path) -> None:
    """Rebuild ``source`` under ``target``: real folders, a hard link per file, and each junction of the
    tree made again to the same place inside ``target`` (a link leaving the tree is refused)."""
    source_resolved, junctions = source.resolve(strict=True), []
    _link_files(source, target, source_resolved, target, junctions)
    for pointed, destination in junctions:  # after the folders they point to exist
        _junction(pointed, destination)


def _link_files(source: Path, target: Path, top: Path, top_target: Path, junctions: list) -> None:
    target.mkdir(parents=True)
    with os.scandir(source) as entries:
        for entry in entries:
            path, destination = Path(entry.path), target / entry.name
            if _is_link(path):
                pointed = Path(os.readlink(path).removeprefix("\\\\?\\")).resolve(strict=True)
                if pointed != top and top not in pointed.parents:
                    raise RuntimeError(f"{path} links outside {top}")
                junctions.append((top_target / pointed.relative_to(top), destination))
            elif entry.is_dir(follow_symlinks=False):
                _link_files(path, destination, top, top_target, junctions)
            elif os.lstat(path).st_file_attributes & stat.FILE_ATTRIBUTE_READONLY:
                shutil.copyfile(path, destination)  # a hard link would share the read-only flag
            else:
                os.link(path, destination)


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
        for name in JUNCTIONS:
            if (install / name).is_dir():
                _junction(install / name, root / name)
        for name in LINKED_TREES:
            if (install / name).is_dir():
                _link_tree(install / name, root / name)
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
            (root / name).mkdir(parents=True, exist_ok=True)
        _copy_set(Path(golden_root), set_name, root / "input" / "golden" / set_name)
    except BaseException:
        remove(root, install)
        raise
    return root


def harvest(root: Path, install: Path) -> list[Path]:
    """Move every trial folder of the trial root's reports into the install's (same volume: a rename)."""
    install = Path(install).resolve(strict=True)
    _check_place(install, Path(root))
    moved = []
    for name in HARVESTED:
        for folder in sorted((Path(root) / name).iterdir()):
            target = install / name / folder.name
            if target.exists() or _is_link(folder):
                raise RuntimeError(f"cannot move {folder} to {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            folder.rename(target)
            moved.append(target)
    return moved


def _links(folder: Path) -> list[Path]:
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
    """Take every link down without entering it, then delete the trial root."""
    install = Path(install).resolve(strict=True)
    root = Path(root)
    _check_place(install, root)
    if not root.exists():
        return
    for link in _links(root):
        if os.lstat(link).st_file_attributes & stat.FILE_ATTRIBUTE_DIRECTORY:
            os.rmdir(link)  # removes the junction itself, never the folder it points to
        else:
            os.unlink(link)
    left = _links(root)
    if left:
        raise RuntimeError(f"links left in the trial root, nothing deleted: {[str(p) for p in left]}")
    shutil.rmtree(root)
