"""Which file in output/ is the export of a review's current decisions.

Neutral module (standard library only): review_workflow, control_center,
job_import and source_cleanup import it without an import cycle.

An export is named by a hash of what its render applies: the render fields of
the edit-plan operations (``render_identity``) and a non-default size policy.
Exports made before that hash existed carry an older hash of the review
decisions, which left out fields the render uses (the blur edge mode, the
detected intervals of a discrete item); they are still found under that
legacy name (``output_candidates``), and only their manifest can tell whether
they render the current decisions.

The hash also holds each operation's id, which the render never uses. A new
revision of the review ("Chạy lại", "Dựng lại danh sách duyệt") names its
cards, and so its operations, anew, so an export of the same render made from
an earlier revision has another name; it is still found (``other_exports``)
when its manifest's operations, ids aside, are the current ones and its name is
the one those operations give under the current source and size policy
(``other_export_problem``). Exports keep the names they were given.

An existing file is the export only when its manifest proves it
(``manifest_problem``): a complete, decode-validated render of the same,
unmodified source with the same operations, lying at that path as a plain
file (never a link). A file without such a manifest is never reused, and the
renderer never overwrites it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

# The fields of an edit-plan operation that follow from the review decisions.
# Of its "blur" settings only edge_feather_mode is a decision; sigma, method and
# region_policy follow the code (and the region) and edge_feather_pixels follows the
# region. So an export rendered before the cover method (blur_filter.COVER_METHOD,
# 2026-10-04) still counts as rendered from the current decisions: the user re-exports
# a video to hide its logos with the cover.
RENDER_FIELDS = ("type", "start_seconds", "end_seconds", "region_source_pixels")
# The operation id also names the export (the names exports were given), but the
# render never uses it and a new revision changes it: same_render leaves it out.
NAME_FIELDS = ("id", *RENDER_FIELDS)
# Marks the operations hash apart from the legacy decision hash it replaced.
IDENTITY_VERSION = 2

# Why an existing file is not a proven export (manifest_problem).
PROBLEM_MANIFEST = "manifest"  # unreadable, or not about this file and source
PROBLEM_DECODE = "decode"  # never passed the full decode validation
PROBLEM_CHANGED = "changed"  # the file's size differs from the manifest
PROBLEM_OLDER = "older"  # rendered from other operations
PROBLEM_NO_OPERATIONS = "no_operations"  # an early manifest that lists no operations
# A file other_exports found under a name its manifest's operations, the source
# and the size policy do not give (renamed, or rendered under another policy).
PROBLEM_NAME = "name"

# A symbolic link, junction or other reparse point (os.stat_result.st_file_attributes).
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
# The source hash that names an export; anything else (a crafted queue) is "nohash".
_SOURCE_HASH = re.compile(r"[0-9a-fA-F]{8,64}")


def render_identity(operations: Any) -> list[dict[str, Any]] | None:
    """The decision fields of edit-plan operations and their ids, in order; None when malformed.

    It names the export (export_paths). Two identities render the same when
    they are equal ids aside (same_render): the same cut and blur intervals,
    regions and blur edge modes, in the same order.
    """
    if not isinstance(operations, list) or not all(isinstance(op, dict) for op in operations):
        return None
    identity = []
    for operation in operations:
        value = {field: operation.get(field) for field in NAME_FIELDS}
        blur = operation.get("blur")
        value["edge_feather_mode"] = (
            str((blur if isinstance(blur, dict) else {}).get("edge_feather_mode", "all_edges"))
            if operation.get("type") == "blur" else None
        )
        identity.append(value)
    return identity


def same_render(left: list | None, right: list | None) -> bool:
    """Two render identities apply the same operations in the same order, ids aside.

    The order counts: the renderer keeps it for blurs that start together, and a
    region next to or over an earlier one reads that region's blurred picture.
    """
    if left is None or right is None:
        return False
    return _without_ids(left) == _without_ids(right)


def _without_ids(identity: list) -> list:
    return [{field: value for field, value in operation.items() if field != "id"} for operation in identity]


def aware_datetime(value: Any) -> datetime | None:
    """A timezone-aware ISO timestamp, else None (naive or unparseable)."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        return None
    return moment


def _size_policy(queue: dict) -> dict | None:
    """The queue's size policy when it changes the render (any mode but the default)."""
    policy = queue.get("export_size_policy")
    if isinstance(policy, dict) and policy.get("mode") not in {None, "default"}:
        return policy
    return None


def _has_source_hash(queue: dict) -> bool:
    return _SOURCE_HASH.fullmatch(str(queue["source"].get("sha256") or "")) is not None


def _name_prefix(queue: dict) -> str:
    """``<slug>-<source hash>``: how every export name of the queue's source begins."""
    source = Path(str(queue["source"]["path"]))
    # Only hex reaches the name: a crafted hash cannot move the paths out of output/ or work/.
    source_hash = str(queue["source"]["sha256"])[:8] if _has_source_hash(queue) else "nohash"
    slug = re.sub(r"[^\w.-]+", "-", source.stem, flags=re.UNICODE).strip("-._")
    return f"{(slug or 'video')[:80]}-{source_hash}"


def _paths(root: Path, queue: dict, identity: object) -> tuple[Path, Path, Path]:
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:8]
    key = f"{_name_prefix(queue)}-{digest}"
    return (
        root / "work" / f"{key}-edit-plan.json",
        root / "output" / f"{key}-reviewed.mp4",
        root / "work" / f"{key}-export-job.json",
    )


def legacy_export_paths(root: Path, queue: dict) -> tuple[Path, Path, Path]:
    """The paths exports had before the operations hash (a hash of the review decisions)."""
    decisions = [
        {
            "id": item.get("id"), "decision": item.get("decision"),
            "start": item.get("start_seconds"), "end": item.get("end_seconds"),
            "region": item.get("decision_region_source_pixels"),
        }
        for item in queue.get("items", [])
    ]
    policy = _size_policy(queue)
    identity: object = decisions if policy is None else {
        "decisions": decisions, "export_size_policy": policy,
    }
    return _paths(root, queue, identity)


def export_paths(root: Path, queue: dict, render: list | None) -> tuple[Path, Path, Path]:
    """Plan, output and job-state paths of an export of ``queue``.

    ``render`` is the render_identity of the queue's edit-plan operations, or
    None while they cannot be built (an unfinished review, which has no
    export): the legacy decision hash names those.
    """
    if render is None:
        return legacy_export_paths(root, queue)
    identity: dict[str, Any] = {"export_identity": IDENTITY_VERSION, "operations": render}
    policy = _size_policy(queue)
    if policy is not None:
        identity["export_size_policy"] = policy
    return _paths(root, queue, identity)


def output_candidates(root: Path, queue: dict, render: list | None) -> list[Path]:
    """Where the export of ``queue`` may be: its own path, then its legacy name."""
    current = export_paths(root, queue, render)[1]
    legacy = legacy_export_paths(root, queue)[1]
    return [current] if legacy == current else [current, legacy]


def other_exports(root: Path, queue: dict, render: list | None) -> list[Path]:
    """The other files in output/ named as an export of the queue's source, by name.

    An earlier revision named its export by its own operation ids. Only a
    source with a hash is looked at, and only names: other_export_problem
    tells what each file is. Never the queue's own names (output_candidates).
    """
    if not _has_source_hash(queue):
        return []
    pattern = re.compile(re.escape(_name_prefix(queue)) + r"-[0-9a-f]{8}-reviewed\.mp4")
    own = {os.path.normcase(str(path)) for path in output_candidates(root, queue, render)}
    folder = root / "output"
    try:
        with os.scandir(folder) as entries:
            names = sorted(entry.name for entry in entries if pattern.fullmatch(entry.name) and entry.is_file())
    except OSError:
        return []
    return [folder / name for name in names if os.path.normcase(str(folder / name)) not in own]


def export_manifest_path(output: Path) -> Path:
    return output.with_suffix(output.suffix + ".manifest.json")


def _is_source(output: Path, source_path: Any) -> bool:
    """``output`` is the source file itself (a hard link of it, or the same file through a junction)."""
    if not source_path:
        return False
    try:
        left, right = os.stat(output), os.stat(source_path)
    except (OSError, ValueError):
        return False
    # A file system without file ids reports 0 for every file: never "the same" then.
    return left.st_ino != 0 and (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def manifest_problem(
    root: Path, output: Path, manifest: Any, *, source_sha256: Any, render: list | None,
    compare_render: bool = True, source_path: Any = None,
) -> str | None:
    """None when ``manifest`` proves ``output`` is a complete render; else a PROBLEM_* code.

    The manifest must be COMPLETED, name ``output`` and the unmodified source
    ``source_sha256``, record a full decode validation and the file's size;
    ``output`` must be a plain file, never a link nor the source file
    ``source_path`` itself; with ``compare_render`` its operations must also
    render ``render`` (same_render: their ids may differ). Raises OSError when
    ``output`` cannot be read.
    """
    sha = str(source_sha256 or "").casefold()
    if not isinstance(manifest, dict):
        return PROBLEM_MANIFEST
    source, described = manifest.get("source"), manifest.get("output")
    matches = (
        isinstance(source, dict) and isinstance(described, dict)
        and manifest.get("status") == "COMPLETED"
        and bool(sha) and str(source.get("sha256") or "").casefold() == sha
        and source.get("modified") is False
        and ("sha256_after_render" not in source
             or str(source.get("sha256_after_render") or "").casefold() == sha)
        and isinstance(described.get("path"), str) and bool(described.get("path"))
        and isinstance(described.get("bytes"), int) and not isinstance(described.get("bytes"), bool)
        and isinstance(described.get("sha256"), str)
        and _SHA256.fullmatch(described.get("sha256") or "") is not None
        and aware_datetime(manifest.get("created_at")) is not None
    )
    if matches:
        try:
            # Compared as written, never resolved: resolving a UNC or device
            # path makes Windows contact that machine, and a link loop raises.
            # The renderer always records output/<name> under the same root.
            described_output = os.path.abspath(root / described["path"])
            matches = os.path.normcase(described_output) == os.path.normcase(os.path.abspath(output))
        except (OSError, ValueError):
            # A path the system cannot take (a NUL character, say) proves nothing.
            matches = False
    if not matches:
        return PROBLEM_MANIFEST
    encoding = manifest.get("encoding")
    if not isinstance(encoding, dict) or encoding.get("full_decode_validation_passed") is not True:
        return PROBLEM_DECODE
    info = os.lstat(output)
    if (
        not stat.S_ISREG(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
        or int(info.st_size) != described["bytes"]
        or _is_source(output, source_path)
    ):
        # The renderer wrote a plain file here: a link, the source itself or a
        # file of another size is not that render.
        return PROBLEM_CHANGED
    if not compare_render:
        return None
    if manifest.get("operations") is None:
        return PROBLEM_NO_OPERATIONS
    rendered = render_identity(manifest["operations"])
    if rendered is None:
        return PROBLEM_MANIFEST
    return None if same_render(rendered, render) else PROBLEM_OLDER


def other_export_problem(
    root: Path, output: Path, manifest: Any, queue: dict, *, source_sha256: Any, render: list | None,
    source_path: Any = None,
) -> str | None:
    """manifest_problem for a file other_exports found, which must also bear its own name.

    That name is the one the manifest's operations give under the queue's
    source and size policy: the manifest records no size policy, so only the
    name proves the file was rendered under the current one. A legacy name (a
    hash of decisions no manifest records) never passes.
    """
    problem = manifest_problem(root, output, manifest, source_sha256=source_sha256, render=render,
                               source_path=source_path)
    if problem is not None:
        return problem
    named = export_paths(root, queue, render_identity(manifest["operations"]))[1]
    if os.path.normcase(os.path.abspath(named)) != os.path.normcase(os.path.abspath(output)):
        return PROBLEM_NAME
    return None


def read_manifest(output: Path) -> Any:
    """The parsed manifest beside ``output``; None when it is missing or unreadable (or nested too deep)."""
    try:
        return json.loads(export_manifest_path(output).read_bytes().decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        return None


def verified_export(root: Path, queue: dict, render: list | None) -> tuple[Path, dict] | None:
    """(output, manifest) of the first file its manifest proves; None when none does.

    The queue's own name and its legacy name first (output_candidates), then an
    export of the same render an earlier revision named (other_exports).
    """
    source = queue.get("source") or {}
    facts = {"source_sha256": source.get("sha256"), "render": render, "source_path": source.get("path")}
    found = _first_proven(
        output_candidates(root, queue, render),
        lambda output, manifest: manifest_problem(root, output, manifest, **facts),
    )
    if found is not None:
        return found
    return _first_proven(
        other_exports(root, queue, render),
        lambda output, manifest: other_export_problem(root, output, manifest, queue, **facts),
    )


def _first_proven(outputs: list[Path], problem_of: Callable[[Path, Any], str | None]) -> tuple[Path, dict] | None:
    for output in outputs:
        try:
            if not output.is_file():
                continue
            manifest = read_manifest(output)
            problem = problem_of(output, manifest)
        except (OSError, ValueError, RuntimeError):
            # RuntimeError: a link loop on the way.
            continue
        if problem is None:
            return output, manifest
    return None
