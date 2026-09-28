from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from biliflow.cache_dependencies import stage_source_paths


CACHE_SCHEMA_VERSION = 2
DEFAULT_MAX_CACHE_BYTES = 10 * 1024**3
DEFAULT_MAX_CACHE_AGE = timedelta(days=14)
CACHEABLE_STAGES = frozenset({
    "animation_safety",
    "live_safety",
    "adult",
    "gore",
    "violence",
    "confirm_violence",
    "text",
    "visual_logo",
    "localize_logo",
})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _tree_fingerprint(root: Path, stage_name: str = "") -> str:
    """Fingerprint code/config and model identities without hashing large weights."""
    records: list[dict[str, Any]] = []
    for relative in (
        *(path.relative_to(root).as_posix() for path in stage_source_paths(root, stage_name)),
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
            stat = path.stat()
            item: dict[str, Any] = {
                "path": path.relative_to(root).as_posix(),
                "size": stat.st_size,
            }
            # Source and small configuration files are content hashed. Large model
            # weights are represented by their manifest plus stable file metadata.
            if stat.st_size <= 8 * 1024 * 1024:
                item["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                item["mtime_ns"] = stat.st_mtime_ns
            records.append(item)
    if stage_name in {"visual_logo", "localize_logo"}:
        memory = root / "state" / "brand-memory.json"
        records.append({"brand_memory": hashlib.sha256(memory.read_bytes()).hexdigest()
                        if memory.is_file() else None})
    for name in ("ffmpeg", "ffprobe"):
        executable = root / "tools" / "ffmpeg" / "bin" / (name + ".exe")
        if executable.is_file():
            stat = executable.stat()
            records.append({"tool": name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    models = root / "models"
    if models.exists():
        for path in sorted(item for item in models.rglob("*") if item.is_file()):
            stat = path.stat()
            item = {
                "path": path.relative_to(root).as_posix(),
                "size": stat.st_size,
            }
            if path.name in {"manifest.json", "config.json"} or stat.st_size <= 128 * 1024:
                item["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                item["mtime_ns"] = stat.st_mtime_ns
            records.append(item)
    return _json_digest(records)


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
        return stage_name in CACHEABLE_STAGES and bool(tuple(artifact_paths))

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
                size = sum(
                    path.stat().st_size for path in entry.rglob("*") if path.is_file()
                )
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
