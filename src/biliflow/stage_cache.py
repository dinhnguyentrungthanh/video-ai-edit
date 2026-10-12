from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from biliflow.cache_dependencies import STAGE_MODELS, STAGE_MODULES, stage_source_paths


CACHE_SCHEMA_VERSION = 2
DEFAULT_MAX_CACHE_BYTES = 10 * 1024**3
DEFAULT_MAX_CACHE_AGE = timedelta(days=14)
CACHEABLE_STAGES = frozenset({
    "animation_safety",
    "live_safety",
    "adult",
    "verify_adult",
    "gore",
    "violence",
    "confirm_violence",
    "text",
    "visual_logo",
    "localize_logo",
})
# A build (no .py modules) keys its stages by this manifest at the install root.
BUILD_MANIFEST_NAME = "biliflow-build.json"
BUILD_MANIFEST_VERSION = 1
MAX_BUILD_MANIFEST_BYTES = 4 * 1024 * 1024
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_NOT_MODEL_NAMES = frozenset({".gitkeep"})
_BYTECODE_SUFFIXES = frozenset({".pyc", ".pyo"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _not_a_model_file(relative: Path) -> bool:
    """What under models\\ is not part of a model and changes without it: Hugging Face's
    ``.cache``, bytecode written when a model's own code is imported (trust_remote_code) and
    placeholders. The same filter as scripts/models_lock.py, except that a model's
    manifest.json stays in the key."""
    return (relative.name in _NOT_MODEL_NAMES
            or relative.suffix in _BYTECODE_SUFFIXES
            or any(part.startswith(".cache") or part == "__pycache__" for part in relative.parts))


def _stage_sources(root: Path, stage_name: str) -> tuple[Path, ...] | None:
    """The stage's code as source files; None when its modules are not .py files (a build)."""
    package = root / "src" / "biliflow"
    entries = [package / f"{name}.py" for name in STAGE_MODULES.get(stage_name, ("cli",))]
    if not all(path.is_file() for path in entries):
        return None
    return stage_source_paths(root, stage_name)


def _build_code_records(root: Path, stage_name: str) -> list[dict[str, Any]] | None:
    """The stage's code identity from a build's manifest (made at build time by
    build_code_manifest); None when the manifest is missing, invalid or lacks the stage."""
    path = root / BUILD_MANIFEST_NAME
    try:
        info = os.lstat(path)
        if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BUILD_MANIFEST_BYTES
                or getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT):
            return None
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or type(data.get("biliflow_build")) is not int:
        return None
    build_id, stages = data.get("build_id"), data.get("stages")
    modules = stages.get(stage_name) if isinstance(stages, dict) else None
    if (data["biliflow_build"] != BUILD_MANIFEST_VERSION
            or not isinstance(build_id, str) or not build_id.strip()
            or not isinstance(modules, dict) or not modules
            or not all(isinstance(name, str) and isinstance(digest, str)
                       and re.fullmatch(r"[0-9a-f]{64}", digest)
                       for name, digest in modules.items())):
        return None
    return [{"build_id": build_id},
            *({"module": name, "sha256": modules[name]} for name in sorted(modules))]


def build_code_manifest(source_root: Path, build_id: str) -> dict[str, Any]:
    """The content of a build's biliflow-build.json, made from the source tree when the build
    is made: the build id and, for each cached stage, the SHA-256 of every source file in its
    key. A build keys its stages by this; without .py files or this manifest nothing is cached."""
    if not build_id.strip():
        raise ValueError("build_id must not be empty")
    source_root = source_root.resolve(strict=True)
    stages = {}
    for stage in sorted(CACHEABLE_STAGES):
        sources = _stage_sources(source_root, stage)
        if sources is None:
            raise ValueError(f"Stage {stage} has no source files in {source_root}")
        stages[stage] = {
            path.relative_to(source_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sources
        }
    return {"biliflow_build": BUILD_MANIFEST_VERSION, "build_id": build_id, "stages": stages}


def _torch_versions() -> dict[str, Any]:
    """torch's version and its CUDA build, read as text from torch/version.py: the
    Control Center never imports torch."""
    try:
        spec = importlib.util.find_spec("torch")
    except (ImportError, ValueError):
        spec = None
    for location in (spec.submodule_search_locations or []) if spec else []:
        try:
            tree = ast.parse((Path(location) / "version.py").read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        values = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign):
                target, value = node.target, node.value
            else:
                continue
            if isinstance(target, ast.Name) and isinstance(value, ast.Constant):
                values[target.id] = value.value
        return {"torch": values.get("__version__"), "cuda": values.get("cuda")}
    return {"torch": None, "cuda": None}


@lru_cache(maxsize=1)
def _gpu_identity() -> tuple[tuple[str, str], ...] | str:
    """Name and compute capability of each NVIDIA GPU (never the driver), read once per
    process through NVML: the GPU cannot change while the Control Center runs. A failed
    read gives its status, so those keys never match a working GPU's."""
    from biliflow.hardware_system import WindowsSystem

    read = WindowsSystem().nvml()
    if read.status != "ok":
        return read.status
    return tuple((gpu.name, gpu.compute_capability) for gpu in read.gpus)


def _runtime_identity() -> dict[str, Any]:
    gpus = _gpu_identity()
    return {
        **_torch_versions(),
        "gpus": ([{"name": name, "compute_capability": capability} for name, capability in gpus]
                 if isinstance(gpus, tuple) else {"nvml": gpus}),
    }


def _tree_fingerprint(root: Path, stage_name: str = "") -> str:
    """Fingerprint code/config, the stage's models and the torch/GPU runtime without
    hashing large weights."""
    sources = _stage_sources(root, stage_name)
    records: list[dict[str, Any]] = (
        [] if sources is not None else list(_build_code_records(root, stage_name) or ())
    )
    for relative in (
        *(path.relative_to(root).as_posix() for path in sources or ()),
        "config/processing_profiles.json",
        "config/detection_policy.yaml",
        "config/text_review_policy.json",
        "config/license_policy.json",
        "scripts/run.ps1",
        "scripts/env.ps1",
        "annotations/text_semantics_seed_v1.json",
        "pyproject.toml",
        "uv.lock",
    ):
        candidate = root / relative
        paths = [candidate] if candidate.is_file() else (
            sorted(path for path in candidate.rglob("*") if path.is_file())
            if candidate.exists() else []
        )
        for path in paths:
            if path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.parts:
                continue
            info = path.stat()
            item: dict[str, Any] = {
                "path": path.relative_to(root).as_posix(),
                "size": info.st_size,
            }
            # Source and small configuration files are content hashed. Large model
            # weights are represented by their manifest plus stable file metadata.
            if info.st_size <= 8 * 1024 * 1024:
                item["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                item["mtime_ns"] = info.st_mtime_ns
            records.append(item)
    if stage_name in {"visual_logo", "localize_logo"}:
        memory = root / "state" / "brand-memory.json"
        records.append({"brand_memory": hashlib.sha256(memory.read_bytes()).hexdigest()
                        if memory.is_file() else None})
    for name in ("ffmpeg", "ffprobe"):
        executable = root / "tools" / "ffmpeg" / "bin" / (name + ".exe")
        if executable.is_file():
            info = executable.stat()
            records.append({"tool": name, "size": info.st_size, "mtime_ns": info.st_mtime_ns})
    models = root / "models"
    if models.exists():
        names = STAGE_MODELS.get(stage_name)
        for folder in [models] if names is None else [models / name for name in sorted(names)]:
            if not folder.is_dir():
                records.append({"model": folder.relative_to(root).as_posix(), "missing": True})
                continue
            for path in sorted(item for item in folder.rglob("*") if item.is_file()):
                if _not_a_model_file(path.relative_to(models)):
                    continue
                info = path.stat()
                item = {
                    "path": path.relative_to(root).as_posix(),
                    "size": info.st_size,
                }
                if path.name in {"manifest.json", "config.json"} or info.st_size <= 128 * 1024:
                    item["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                else:
                    item["mtime_ns"] = info.st_mtime_ns
                records.append(item)
    records.append({"runtime": _runtime_identity()})
    return _json_digest(records)


def _entry_bytes(manifest_path: Path) -> int:
    """Bytes of one cache entry: the snapshot size its manifest recorded plus the manifest.
    Walking every snapshot file instead cost ~5.5 s per stage on 45,726 files. An entry
    whose manifest has no usable size is walked as before."""
    try:
        recorded = json.loads(manifest_path.read_text(encoding="utf-8")).get("bytes")
    except (OSError, UnicodeDecodeError, ValueError, AttributeError):
        recorded = None
    if type(recorded) is int and recorded >= 0:
        return recorded + manifest_path.stat().st_size
    return sum(path.stat().st_size for path in manifest_path.parent.rglob("*") if path.is_file())


def _artifact_roots(paths: Iterable[Path]) -> tuple[Path, ...]:
    parents = sorted(
        {path.resolve().parent for path in paths},
        key=lambda path: (len(path.parts), path.as_posix().casefold()),
    )
    if not parents:
        raise ValueError("A cached stage must declare at least one artifact")
    return tuple(
        path for index, path in enumerate(parents)
        if not any(parent in path.parents for parent in parents[:index])
    )


class StageArtifactCache:
    """Content-addressed snapshots for exact scan-stage reuse.

    A hit requires the same source checksum, normalized command, source/config
    fingerprint and expected artifact layout. Snapshots are copied into each new
    report revision so review queues never depend on the cache remaining present.
    """

    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)
        self.cache_root = self.root / "cache" / "stage-results"

    def cacheable(self, stage_name: str, artifact_paths: Iterable[Path]) -> bool:
        # Refuse safely: a stage whose code is neither .py files nor in a build manifest
        # would get a key that no code change can alter.
        return (
            stage_name in CACHEABLE_STAGES and bool(tuple(artifact_paths))
            and (_stage_sources(self.root, stage_name) is not None
                 or _build_code_records(self.root, stage_name) is not None)
        )

    def _fingerprint(self, stage_name: str) -> str:
        # A dashboard can stay open across code/model/config changes. Never pin
        # its first fingerprint for the lifetime of that process.
        return _tree_fingerprint(self.root, stage_name)

    def _normalized_arguments(
        self, arguments: Iterable[str], *, source_path: Path, report_root: Path
    ) -> list[str]:
        source = str(source_path.resolve()).casefold()
        report = str(report_root.resolve()).casefold()
        normalized = []
        for value in arguments:
            raw = str(value)
            folded = raw.casefold()
            if folded == source:
                normalized.append("<SOURCE>")
            elif folded == report or folded.startswith(report + os.sep.casefold()):
                suffix = raw[len(str(report_root.resolve())):].lstrip("\\/")
                normalized.append(f"<REPORT>/{Path(suffix).as_posix()}")
            else:
                normalized.append(raw)
        return normalized

    def key(
        self,
        *,
        stage_name: str,
        source_sha256: str,
        source_path: Path,
        report_root: Path,
        commands: Iterable[Iterable[str]],
        artifact_paths: Iterable[Path],
    ) -> str:
        artifacts = tuple(Path(path).resolve() for path in artifact_paths)
        commands = tuple(tuple(str(value) for value in command) for command in commands)
        resolved_report_root = report_root.resolve()
        # Reports produced inside a multi-command stage are outputs, not external
        # dependencies. External upstream reports must match by content, not name.
        produced = set()
        upstream = []
        for command in commands:
            for index, argument in enumerate(command[:-1]):
                if argument in {"--report", "--policy", "--semantic-seed"}:
                    path = Path(command[index + 1]).resolve()
                    if path not in produced:
                        upstream.append({
                            "path": self._normalized_arguments(
                                [str(path)], source_path=source_path, report_root=report_root,
                            )[0],
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        })
            for index, argument in enumerate(command[:-1]):
                if argument == "--output":
                    produced.add(Path(command[index + 1]).resolve())
        payload = {
            "schema": CACHE_SCHEMA_VERSION,
            "stage": stage_name,
            "source_sha256": source_sha256,
            "runtime": self._fingerprint(stage_name),
            "upstream": upstream,
            "commands": [
                self._normalized_arguments(
                    command, source_path=source_path, report_root=report_root
                )
                for command in commands
            ],
            "artifacts": [
                path.relative_to(resolved_report_root).as_posix() for path in artifacts
            ],
        }
        return _json_digest(payload)

    def entry(self, source_sha256: str, stage_name: str, key: str) -> Path:
        return self.cache_root / source_sha256 / stage_name / key

    def restore(
        self,
        *,
        stage_name: str,
        source_sha256: str,
        source_path: Path,
        report_root: Path,
        commands: Iterable[Iterable[str]],
        artifact_paths: Iterable[Path],
    ) -> dict[str, Any] | None:
        artifacts = tuple(Path(path).resolve() for path in artifact_paths)
        if not self.cacheable(stage_name, artifacts):
            return None
        key = self.key(
            stage_name=stage_name,
            source_sha256=source_sha256,
            source_path=source_path,
            report_root=report_root,
            commands=commands,
            artifact_paths=artifacts,
        )
        entry = self.entry(source_sha256, stage_name, key)
        manifest_path = entry / "manifest.json"
        snapshot = entry / "snapshot"
        if not manifest_path.is_file() or not snapshot.is_dir():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if (
            manifest.get("schema_version") != CACHE_SCHEMA_VERSION
            or manifest.get("key") != key
            or manifest.get("source_sha256") != source_sha256
            or manifest.get("stage") != stage_name
        ):
            return None
        resolved_report_root = report_root.resolve()
        expected_relatives = [
            path.relative_to(resolved_report_root).as_posix() for path in artifacts
        ]
        if manifest.get("artifacts") != expected_relatives:
            return None
        expected_roots = [
            path.relative_to(resolved_report_root).as_posix()
            for path in _artifact_roots(artifacts)
        ]
        if manifest.get("roots") != expected_roots:
            return None
        for relative in expected_relatives:
            cached_artifact = snapshot / relative
            try:
                payload = json.loads(cached_artifact.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            if not isinstance(payload, dict) or payload.get("status") == "FAILED":
                return None
            artifact_sha = payload.get("input_sha256")
            if artifact_sha and artifact_sha != source_sha256:
                return None
        reports_root = (self.root / "reports" / "jobs").resolve()
        if reports_root != resolved_report_root and reports_root not in resolved_report_root.parents:
            raise ValueError("Cached stage report root must stay inside reports/jobs")
        roots = _artifact_roots(artifacts)
        for target_root in roots:
            if resolved_report_root not in target_root.parents:
                raise ValueError("Cached stage target must stay below its report root")
            relative_root = target_root.relative_to(resolved_report_root)
            cached_root = snapshot / relative_root
            if not cached_root.is_dir():
                return None
        for target_root in roots:
            relative_root = target_root.relative_to(resolved_report_root)
            if target_root.exists():
                shutil.rmtree(target_root)
            target_root.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(
                snapshot / relative_root, target_root, copy_function=shutil.copy2
            )
        for artifact in artifacts:
            if not artifact.is_file():
                for target_root in roots:
                    shutil.rmtree(target_root, ignore_errors=True)
                return None
        os.utime(manifest_path, None)
        return {"key": key, "entry": entry, "manifest": manifest}

    def store(
        self,
        *,
        stage_name: str,
        source_sha256: str,
        source_path: Path,
        report_root: Path,
        commands: Iterable[Iterable[str]],
        artifact_paths: Iterable[Path],
        expected_key: str | None = None,
    ) -> dict[str, Any] | None:
        artifacts = tuple(Path(path).resolve() for path in artifact_paths)
        if not self.cacheable(stage_name, artifacts):
            return None
        resolved_report_root = report_root.resolve()
        reports_root = (self.root / "reports" / "jobs").resolve()
        if reports_root != resolved_report_root and reports_root not in resolved_report_root.parents:
            raise ValueError("Cached stage report root must stay inside reports/jobs")
        roots = _artifact_roots(artifacts)
        if any(resolved_report_root not in target.parents for target in roots):
            raise ValueError("Cached stage source must stay below its report root")
        if any(not artifact.is_file() for artifact in artifacts):
            return None
        key = self.key(
            stage_name=stage_name,
            source_sha256=source_sha256,
            source_path=source_path,
            report_root=report_root,
            commands=commands,
            artifact_paths=artifacts,
        )
        if expected_key is not None and key != expected_key:
            # Dependencies changed while the subprocess was running. The result
            # may still be reviewed, but must not be cached under a new identity.
            return None
        entry = self.entry(source_sha256, stage_name, key)
        snapshot = entry / "snapshot"
        temporary = entry.with_name(entry.name + ".tmp")
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir(parents=True, exist_ok=True)
        snapshot = temporary / "snapshot"
        for target_root in roots:
            relative_root = target_root.relative_to(resolved_report_root)
            destination = snapshot / relative_root
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(target_root, destination, copy_function=shutil.copy2)
        bytes_count = sum(
            path.stat().st_size for path in snapshot.rglob("*") if path.is_file()
        )
        manifest = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "key": key,
            "stage": stage_name,
            "source_sha256": source_sha256,
            "created_at": _utc_now(),
            "bytes": bytes_count,
            "artifacts": [
                path.relative_to(resolved_report_root).as_posix() for path in artifacts
            ],
            "roots": [
                path.relative_to(resolved_report_root).as_posix() for path in roots
            ],
        }
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        entry.parent.mkdir(parents=True, exist_ok=True)
        if entry.exists():
            shutil.rmtree(entry)
        temporary.replace(entry)
        return {"key": key, "entry": entry, "manifest": manifest}

    def prune(
        self,
        *,
        max_bytes: int = DEFAULT_MAX_CACHE_BYTES,
        max_age: timedelta = DEFAULT_MAX_CACHE_AGE,
        now: datetime | None = None,
    ) -> dict[str, int]:
        now = now or datetime.now(timezone.utc)
        if not self.cache_root.exists():
            return {"removed_entries": 0, "removed_bytes": 0, "kept_bytes": 0}
        entries: list[tuple[float, int, Path]] = []
        for manifest_path in self.cache_root.glob("*/*/*/manifest.json"):
            entry = manifest_path.parent
            try:
                modified = manifest_path.stat().st_mtime
                size = _entry_bytes(manifest_path)
            except OSError:
                continue
            entries.append((modified, size, entry))
        removed_entries = 0
        removed_bytes = 0
        kept: list[tuple[float, int, Path]] = []
        cutoff = now - max_age
        for modified, size, entry in entries:
            timestamp = datetime.fromtimestamp(modified, timezone.utc)
            if timestamp < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                removed_entries += 1
                removed_bytes += size
            else:
                kept.append((modified, size, entry))
        total = sum(size for _, size, _ in kept)
        for _modified, size, entry in sorted(kept):
            if total <= max_bytes:
                break
            shutil.rmtree(entry, ignore_errors=True)
            removed_entries += 1
            removed_bytes += size
            total -= size
        return {
            "removed_entries": removed_entries,
            "removed_bytes": removed_bytes,
            "kept_bytes": max(0, total),
        }
