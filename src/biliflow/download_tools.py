"""Pinned tools of the video download feature and their license audit.

The records live in ``config/download_tools.json``, apart from
``config/license_policy.json``: that file, ``pyproject.toml`` and ``cli.py`` are
part of the scan cache fingerprint (``stage_cache``), so changing them would
invalidate every cached scan.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

POLICY_RELATIVE = Path("config") / "download_tools.json"
_LICENSE_SPLIT = re.compile(r"\s+(?:AND|OR|WITH)\s+")
_HASH_CHUNK = 1024 * 1024


class DownloadToolsError(RuntimeError):
    """Raised when the download tool policy is missing or inconsistent."""


def installed_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def load_download_tools_policy(project_root: Path) -> dict[str, Any]:
    path = project_root / POLICY_RELATIVE
    if not path.is_file():
        raise DownloadToolsError(f"Thiếu danh sách công cụ tải: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def license_terms(expression: str) -> tuple[str, ...]:
    cleaned = expression.replace("(", " ").replace(")", " ").strip()
    if not cleaned:
        return ()
    return tuple(term for term in _LICENSE_SPLIT.split(cleaned) if term)


def binary_path(project_root: Path, name: str, *, tools_root: Path | None = None) -> Path:
    """Path of a pinned binary; it must stay inside ``<tools_root>/tools``."""
    entry = load_download_tools_policy(project_root).get("binaries", {}).get(name)
    if not entry:
        raise DownloadToolsError(f"Công cụ '{name}' không có trong danh sách công cụ tải.")
    base = tools_root or project_root
    candidate = base / Path(entry["path"])
    tools_dir = (base / "tools").resolve()
    if tools_dir not in candidate.resolve().parents:
        raise DownloadToolsError(f"Đường dẫn của '{name}' phải nằm trong {tools_dir}.")
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_reasons(entry: dict[str, Any], allowed_licenses: set[str]) -> list[str]:
    reasons: list[str] = []
    terms = license_terms(str(entry.get("license_spdx", "")))
    if not terms:
        reasons.append("Thiếu giấy phép.")
    for term in terms:
        if term not in allowed_licenses:
            reasons.append(f"Giấy phép {term} không nằm trong danh sách cho phép.")
    if not str(entry.get("source_url", "")).startswith("https://"):
        reasons.append("Thiếu URL nguồn để kiểm toán giấy phép.")
    if not entry.get("version"):
        reasons.append("Chưa khóa phiên bản.")
    return reasons


def _audit_package(name: str, entry: dict[str, Any], allowed: set[str],
                   version_of: Callable[[str], str | None]) -> dict[str, Any]:
    reasons = _record_reasons(entry, allowed)
    found = version_of(name)
    if found is None:
        reasons.append("Chưa cài trong môi trường Python đang chạy.")
    elif found != entry.get("version"):
        reasons.append(f"Đang cài {found}, danh sách khóa {entry.get('version')}.")
    return {
        "name": name,
        "version": entry.get("version"),
        "installed_version": found,
        "license_spdx": entry.get("license_spdx"),
        "source_url": entry.get("source_url"),
        "allowed": not reasons,
        "reasons": reasons,
    }


def _audit_binary(project_root: Path, name: str, entry: dict[str, Any], allowed: set[str],
                  tools_root: Path | None) -> dict[str, Any]:
    reasons = _record_reasons(entry, allowed)
    expected = str(entry.get("sha256", "")).lower()
    if not expected:
        reasons.append("Chưa ghi SHA-256.")
    actual = None
    try:
        path = binary_path(project_root, name, tools_root=tools_root)
    except DownloadToolsError as error:
        path = None
        reasons.append(str(error))
    present = bool(path and path.is_file())
    if path and not present:
        reasons.append(f"Không thấy {path}.")
    if present:
        actual = _sha256(path)
        if expected and actual != expected:
            reasons.append("SHA-256 khác bản đã duyệt.")
    return {
        "name": name,
        "path": str(path) if path else None,
        "version": entry.get("version"),
        "present": present,
        "sha256": actual,
        "expected_sha256": expected,
        "license_spdx": entry.get("license_spdx"),
        "source_url": entry.get("source_url"),
        "allowed": not reasons,
        "reasons": reasons,
    }


def audit_download_tools(
    project_root: Path,
    *,
    tools_root: Path | None = None,
    version_of: Callable[[str], str | None] = installed_version,
) -> dict[str, Any]:
    """Check pins, licenses, the binary checksums and the forbidden packages.

    ``tools_root`` is the install root that holds ``tools/``; a worktree or a
    temporary root passes the real install root here.
    """
    policy = load_download_tools_policy(project_root)
    allowed = set(policy.get("allowed_licenses", []))
    packages = [
        _audit_package(name, entry, allowed, version_of)
        for name, entry in sorted(policy.get("python_packages", {}).items())
    ]
    binaries = [
        _audit_binary(project_root, name, entry, allowed, tools_root)
        for name, entry in sorted(policy.get("binaries", {}).items())
    ]
    forbidden = []
    for name, reason in sorted(policy.get("forbidden_python_packages", {}).items()):
        found = version_of(name)
        forbidden.append({"name": name, "reason": reason, "installed": found is not None,
                          "installed_version": found, "allowed": found is None})
    items = [*packages, *binaries, *forbidden]
    blocked = sum(not item["allowed"] for item in items)
    return {
        "policy": str((project_root / POLICY_RELATIVE).resolve()),
        "python": sys.executable,
        "allowed": blocked == 0,
        "blocked_count": blocked,
        "python_packages": packages,
        "binaries": binaries,
        "forbidden_python_packages": forbidden,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m biliflow.download_tools")
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit", help="Kiểm tra phiên bản, giấy phép và SHA-256 của công cụ tải")
    audit.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    audit.add_argument("--tools-root", type=Path, default=None,
                       help="Thư mục cài chứa tools\\ (mặc định: project root)")
    args = parser.parse_args(argv)
    report = audit_download_tools(args.project_root.resolve(), tools_root=args.tools_root)
    # The Windows locale here is cp1252; Vietnamese reasons need UTF-8 when piped.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
