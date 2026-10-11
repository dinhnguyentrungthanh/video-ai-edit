"""Read-only inventory of the installed models for config/models.lock.json (plan section 7, C1).

Run on the PC, from the installer worktree, against the install folder:

    .\\.venv\\Scripts\\python.exe temp\\wt-installer\\scripts\\models_lock.py --root E:\\DungChung\\BiliFlow ^
        --out temp\\wt-installer\\config\\models.lock.json

It only reads: every file under ``<root>\\models\\<model>`` is opened read-only and hashed, nothing
there is written, moved or deleted, and nothing is downloaded. The only file it writes is ``--out``
(refused inside ``models\\``; never overwritten unless ``--replace``). The lock records, per model,
the source (Hugging Face repo and revision from ``manifest.json``, or the EasyOCR release file),
the SHA-256 and size of every file and the manifest text itself, so a new install gets the same
bytes. Anything that needs a look (a file the manifest does not list, a size or hash that differs
from it, a local path inside a manifest, a model not APPROVED) goes into ``problems`` and the exit
code is 1; the lock is still written so it can be reviewed.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path


LOCK_VERSION = 1
# The 9 production models: install directory -> key in scripts/download_models.py MODELS.
PRODUCTION_MODELS = {
    "easyocr": "easyocr",
    "nsfw_detection_2_nano": "nsfw-nano",
    "image_safety_classifier_m": "safety",
    "wd_vit_tagger_v3": "anime-gore-tagger",
    "multilingual_minilm_text_semantics": "text-semantics",
    "vit_base_violence_detection": "violence-vit",
    "qwen2_vl_2b_instruct": "qwen2-vl-2b",
    "florence_2_base": "florence-logo",
    "grounding_dino_tiny": "grounding-dino-tiny",
}
_REVISION = re.compile(r"[0-9a-f]{40}")
_LOCAL_PATH = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|\\\\[A-Za-z0-9]")
_CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_easyocr_config(text: str) -> dict[str, dict[str, str]]:
    """``filename -> {url, md5sum}`` from easyocr/config.py, read with ``ast`` (never imported)."""
    found: dict[str, dict[str, str]] = {}

    def visit(value):
        if isinstance(value, dict):
            if isinstance(value.get("filename"), str) and isinstance(value.get("url"), str):
                found[value["filename"]] = {"url": value["url"], "md5sum": str(value.get("md5sum", ""))}
            for item in value.values():
                visit(item)

    for node in ast.parse(text).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in {"detection_models", "recognition_models"}
                for target in node.targets):
            try:
                visit(ast.literal_eval(node.value))
            except ValueError:
                continue
    return found


def easyocr_release_files() -> dict[str, dict[str, str]]:
    spec = importlib.util.find_spec("easyocr")
    locations = list(spec.submodule_search_locations or []) if spec else []
    for location in locations:
        config = Path(location) / "config.py"
        if config.is_file():
            return parse_easyocr_config(config.read_text(encoding="utf-8"))
    return {}


def _files_on_disk(folder: Path, problems: list[str]) -> list[dict]:
    files = []
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder)
        if relative.as_posix() == "manifest.json" or any(part.startswith(".cache") for part in relative.parts):
            continue
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            problems.append(f"{relative.as_posix()}: là liên kết, không khóa")
            continue
        if stat.S_ISREG(info.st_mode):
            files.append({"name": relative.as_posix(), "bytes": info.st_size, "sha256": sha256_file(path)})
    return files


def _compare_with_manifest(files: list[dict], listed: object, problems: list[str]) -> None:
    if not isinstance(listed, list):
        problems.append("manifest không có danh sách files để đối chiếu")
        return
    on_disk = {item["name"]: item for item in files}
    named = {}
    for item in listed:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            named[item["name"]] = item
    for name in sorted(set(named) - set(on_disk)):
        problems.append(f"{name}: manifest có nhưng trên đĩa không có")
    for name in sorted(set(on_disk) - set(named)):
        problems.append(f"{name}: trên đĩa có nhưng manifest không ghi")
    for name in sorted(set(named) & set(on_disk)):
        expected, actual = named[name], on_disk[name]
        if (expected.get("sha256") not in (None, actual["sha256"])
                or expected.get("bytes") not in (None, actual["bytes"])):
            problems.append(f"{name}: kích thước hoặc SHA-256 khác manifest")


def lock_model(root: Path, directory: str, policy: dict, easyocr_files: dict[str, dict[str, str]]) -> dict:
    folder = root / "models" / directory
    problems: list[str] = []
    entry: dict = {"download_key": PRODUCTION_MODELS[directory], "problems": problems}
    approval = policy.get("models", {}).get(directory, {})
    entry["license_spdx"] = approval.get("license_spdx")
    if approval.get("approval_status") != "APPROVED":
        problems.append("config/license_policy.json không ghi APPROVED cho model này")
    if not folder.is_dir():
        problems.append("không có thư mục model")
        return entry
    manifest_path = folder / "manifest.json"
    try:
        raw = manifest_path.read_bytes()
        manifest_text = raw.decode("utf-8")
        manifest = json.loads(manifest_text.lstrip("\ufeff"))
        if not isinstance(manifest, dict):
            raise ValueError("not an object")
    except (OSError, UnicodeDecodeError, ValueError):
        problems.append("không đọc được manifest.json")
        manifest_text, manifest = None, {}
    if manifest_text is not None:
        # Kept verbatim (BOM and line ends included) so a new install writes the same bytes.
        entry["manifest_text"] = manifest_text
        entry["manifest_sha256"] = hashlib.sha256(raw).hexdigest()
        if _LOCAL_PATH.search(manifest_text):
            problems.append("manifest.json có đường dẫn cục bộ; xem lại trước khi commit")
    revision = str(manifest.get("revision") or "")
    entry["revision"] = revision or None
    files = _files_on_disk(folder, problems)
    entry["files"] = files
    if not files:
        problems.append("thư mục model không có file")
    _compare_with_manifest(files, manifest.get("files"), problems)
    if directory == "easyocr":
        entry["source"] = "easyocr-release"
        for item in files:
            release = easyocr_files.get(Path(item["name"]).name)
            if release is None:
                problems.append(f"{item['name']}: không thấy trong easyocr/config.py")
                continue
            item["url"] = release["url"]
            item["archive_member"] = Path(item["name"]).name
            item["md5_upstream"] = release["md5sum"]
            if not release["url"].startswith("https://"):
                problems.append(f"{item['name']}: URL không phải https")
    else:
        entry["source"] = "huggingface"
        entry["repo_id"] = manifest.get("repo_id")
        if not isinstance(entry["repo_id"], str) or "/" not in entry["repo_id"]:
            problems.append("manifest không có repo_id")
        if not _REVISION.fullmatch(revision):
            problems.append("manifest không ghim revision 40 ký tự hex")
    return entry


def build_lock(root: Path, easyocr_files: dict[str, dict[str, str]] | None = None) -> dict:
    policy = json.loads((root / "config" / "license_policy.json").read_text(encoding="utf-8"))
    if easyocr_files is None:
        easyocr_files = easyocr_release_files()
    models = {directory: lock_model(root, directory, policy, easyocr_files) for directory in PRODUCTION_MODELS}
    models_dir = root / "models"
    present = {path.name for path in models_dir.iterdir() if path.is_dir()} if models_dir.is_dir() else set()
    return {
        "version": LOCK_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator": "scripts/models_lock.py",
        "models": models,
        "not_locked": sorted(present - set(PRODUCTION_MODELS)),
    }


def _inside(child: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath([os.path.normcase(child), os.path.normcase(parent)]) == os.path.normcase(parent)
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lập config/models.lock.json từ model đã cài (chỉ đọc)")
    parser.add_argument("--root", type=Path, required=True, help="Thư mục cài có models\\")
    parser.add_argument("--out", type=Path, required=True, help="File lock sẽ ghi (không nằm trong models\\)")
    parser.add_argument("--replace", action="store_true", help="Cho ghi đè file lock đã có")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    out = args.out.resolve()
    if _inside(out, root / "models"):
        print("Lỗi: không ghi gì vào models\\.", file=sys.stderr)
        return 2
    if out.exists() and not args.replace:
        print(f"Lỗi: {out} đã có; thêm --replace nếu muốn ghi lại.", file=sys.stderr)
        return 2
    lock = build_lock(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(lock, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    problems = 0
    for directory, entry in lock["models"].items():
        size = sum(item["bytes"] for item in entry.get("files", []))
        print(f"{directory}: {len(entry.get('files', []))} file, {size / 1048576:.1f} MiB, "
              f"revision {entry.get('revision') or '-'}")
        for problem in entry["problems"]:
            problems += 1
            print(f"  ! {problem}")
    if lock["not_locked"]:
        print("Không khóa (không thuộc 9 model production):", ", ".join(lock["not_locked"]))
    print(f"Đã ghi {out}; {problems} vấn đề cần xem.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
