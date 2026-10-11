"""Download approved BiliFlow models for a NEW install (docs/PERF_INSTALL_PLAN.md section 7, C1).

Default: by ``config/models.lock.json`` (made on the PC by ``scripts/models_lock.py``): exactly the
locked revision and files, each checked against its SHA-256; an interrupted file resumes from its
``.part``; the manifest is written last, byte for byte the locked one. A model folder that already
holds a file which differs from the lock, or a file the lock does not name, is refused and left as
it is: this never rewrites an existing model (never run it on the PC's own ``models\\``).
``--latest`` keeps the old unlocked download of the newest revision, for trying a model in a dev
root only.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import re
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

MODELS = {
    "nsfw": {
        "repo_id": "Falconsai/nsfw_image_detection",
        "directory": "nsfw_image_detection",
        "backend": "transformers",
        "target_labels": ["nsfw"],
        "allow_patterns": [
            "config.json",
            "preprocessor_config.json",
            "model.safetensors",
        ],
    },
    "nsfw-nano": {
        "repo_id": "viddexa/nsfw-detection-2-nano",
        "directory": "nsfw_detection_2_nano",
        "backend": "transformers",
        "target_labels": ["hentai", "porn", "sexy"],
        "allow_patterns": [
            "config.json",
            "preprocessor_config.json",
            "model.safetensors",
        ],
    },
    "safety": {
        "repo_id": "OwenElliott/image-safety-classifier-m",
        "directory": "image_safety_classifier_m",
        "backend": "timm",
        "target_labels": ["NSFL", "NSFW"],
        "allow_patterns": [
            "config.json",
            "model.safetensors",
        ],
    },
    "anime-gore-tagger": {
        "repo_id": "SmilingWolf/wd-vit-tagger-v3",
        "directory": "wd_vit_tagger_v3",
        "backend": "timm_multilabel",
        "target_labels": [
            "blood", "blood_on_face", "blood_on_clothes", "blood_on_hands",
            "blood_splatter", "blood_on_weapon", "blood_from_mouth", "guro",
            "corpse", "blood_stain", "blood_from_eyes", "pink_blood",
            "severed_head", "blood_on_knife", "blood_in_hair", "blood_on_arm",
            "pool_of_blood", "severed_limb", "decapitation", "blood_on_leg",
            "blood_on_bandages", "blood_on_ground", "injury",
        ],
        "allow_patterns": [
            "config.json",
            "model.safetensors",
            "selected_tags.csv",
        ],
    },
    "violence": {
        "repo_id": "HappyGook/videomae-violence-detector",
        "directory": "videomae_violence_detector",
        "backend": "transformers_video",
        "target_labels": ["Violent", "violent"],
        "allow_patterns": [
            "config.json",
            "preprocessor_config.json",
            "model.safetensors",
        ],
    },
    "violence-xd-small": {
        "repo_id": "KingTechnician/videomae-small-finetuned-kinetics-xd-violence-binary",
        "directory": "videomae_xd_violence_small",
        "backend": "transformers_video_multiclass",
        "target_labels": ["B1", "B2", "B4", "B5", "B6", "G"],
        "allow_patterns": [
            "config.json",
            "preprocessor_config.json",
            "model.safetensors",
        ],
    },
    "violence-vit": {
        "repo_id": "jaranohaal/vit-base-violence-detection",
        "directory": "vit_base_violence_detection",
        "backend": "timm_frame_video",
        "architecture": "vit_base_patch16_224",
        "label_names": ["non_violence", "violence"],
        "positive_index": 1,
        "aggregation": "top_k_mean",
        "aggregation_top_k": 5,
        "target_labels": ["violence"],
        "allow_patterns": [
            "config.json",
            "preprocessor_config.json",
            "model.safetensors",
            "README.md",
            "LICENSE*",
        ],
    },
    "text-semantics": {
        "repo_id": "MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli",
        "directory": "multilingual_minilm_text_semantics",
        "backend": "transformers_zero_shot",
        "target_labels": [
            "advertisement", "subtitle", "credits", "scene_text", "other",
        ],
        "allow_patterns": [
            "config.json",
            "model.safetensors",
            "sentencepiece.bpe.model",
            "special_tokens_map.json",
            "tokenizer.json",
            "tokenizer_config.json",
        ],
    },
    "florence-logo": {
        "repo_id": "microsoft/Florence-2-base",
        "directory": "florence_2_base",
        "backend": "transformers_florence2_region_proposer",
        "target_labels": ["logo", "watermark", "promotional_text"],
        "expected_hf_license": "mit",
        "allow_patterns": [
            "config.json",
            "generation_config.json",
            "preprocessor_config.json",
            "processor_config.json",
            "special_tokens_map.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "vocab.json",
            "merges.txt",
            "model.safetensors",
            "*.py",
            "README.md",
            "LICENSE*",
        ],
    },
    "grounding-dino-tiny": {
        "repo_id": "IDEA-Research/grounding-dino-tiny",
        "directory": "grounding_dino_tiny",
        "backend": "transformers_grounding_dino",
        "target_labels": ["logo", "watermark", "banner", "promotional_badge", "qr_code"],
        "expected_hf_license": "apache-2.0",
        "allow_patterns": [
            "config.json",
            "preprocessor_config.json",
            "processor_config.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "vocab.txt",
            "model.safetensors",
            "README.md",
            "LICENSE*",
        ],
    },
    "siglip-ranker": {
        "repo_id": "google/siglip-base-patch16-224",
        "directory": "siglip_base_patch16_224",
        "backend": "transformers_siglip_ranker",
        "target_labels": ["promotional_content", "legitimate_film_content"],
        "expected_hf_license": "apache-2.0",
        "allow_patterns": [
            "config.json",
            "preprocessor_config.json",
            "processor_config.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "spiece.model",
            "model.safetensors",
            "README.md",
            "LICENSE*",
        ],
    },
    "qwen2-vl-2b": {
        "repo_id": "Qwen/Qwen2-VL-2B-Instruct",
        "directory": "qwen2_vl_2b_instruct",
        "backend": "transformers_qwen2_vl",
        "target_labels": [],
        "expected_hf_license": "apache-2.0",
        "lock_only": True,
    },
    "easyocr": {
        "directory": "easyocr",
        "backend": "easyocr",
        "target_labels": [],
        "lock_only": True,
    },
    "phi35-vision-int4": {
        "repo_id": "microsoft/Phi-3.5-vision-instruct-onnx",
        "directory": "phi_3_5_vision_int4",
        "backend": "onnxruntime_genai_cuda_vision",
        "target_labels": ["external_branding", "scene_embedded_branding"],
        "expected_hf_license": "mit",
        "allow_patterns": [
            "gpu/gpu-int4-rtn-block-32/*",
            "README.md",
            "LICENSE*",
        ],
    },
}

# Downloads are denied by default. A model must have an explicit free,
# local-only and commercial-safe approval record before any weights are fetched.
LICENSE_METADATA = {
    "nsfw": {
        "approval_status": "REVIEW_REQUIRED",
        "license_spdx": "Apache-2.0",
        "commercial_use_allowed": True,
        "usage_cost": "free",
        "local_inference_only": True,
        "external_media_upload": False,
        "source_url": "https://huggingface.co/Falconsai/nsfw_image_detection",
        "training_data_review_status": "UPSTREAM_DETAILS_LIMITED",
    },
    "nsfw-nano": {
        "approval_status": "APPROVED",
        "license_spdx": "Apache-2.0",
        "commercial_use_allowed": True,
        "usage_cost": "free",
        "local_inference_only": True,
        "external_media_upload": False,
        "source_url": "https://huggingface.co/viddexa/nsfw-detection-2-nano",
        "training_data_review_status": "UPSTREAM_DETAILS_LIMITED",
    },
    "safety": {
        "approval_status": "APPROVED",
        "license_spdx": "MIT",
        "commercial_use_allowed": True,
        "usage_cost": "free",
        "local_inference_only": True,
        "external_media_upload": False,
        "source_url": "https://huggingface.co/OwenElliott/image-safety-classifier-m",
        "training_data_review_status": "UPSTREAM_REPORTS_PROPRIETARY_WEB_DATA",
    },
    "anime-gore-tagger": {
        "approval_status": "APPROVED",
        "license_spdx": "Apache-2.0",
        "commercial_use_allowed": True,
        "usage_cost": "free",
        "local_inference_only": True,
        "external_media_upload": False,
        "source_url": "https://huggingface.co/SmilingWolf/wd-vit-tagger-v3",
        "training_data_review_status": "UPSTREAM_REPORTS_DANBOORU",
    },
    "violence": {
        "approval_status": "REVIEW_REQUIRED",
        "license_spdx": "MIT",
        "commercial_use_allowed": True,
        "usage_cost": "free",
        "local_inference_only": True,
        "external_media_upload": False,
        "source_url": "https://huggingface.co/HappyGook/videomae-violence-detector",
        "training_data_review_status": "DATASET_TERMS_REQUIRE_REVIEW",
    },
    "violence-xd-small": {
        "approval_status": "BLOCKED",
        "license_spdx": "CC-BY-NC-4.0",
        "commercial_use_allowed": False,
        "usage_cost": "free",
        "local_inference_only": True,
        "external_media_upload": False,
        "source_url": "https://huggingface.co/KingTechnician/videomae-small-finetuned-kinetics-xd-violence-binary",
        "training_data_review_status": "UPSTREAM_DETAILS_LIMITED",
        "blocked_reason": "Non-commercial license is incompatible with the active policy.",
    },
    "violence-vit": {
        "approval_status": "APPROVED",
        "license_spdx": "Apache-2.0",
        "commercial_use_allowed": True,
        "usage_cost": "free",
        "local_inference_only": True,
        "external_media_upload": False,
        "source_url": "https://huggingface.co/jaranohaal/vit-base-violence-detection",
        "training_data_review_status": "UPSTREAM_REPORTS_REAL_LIFE_VIOLENCE_SITUATIONS",
    },
    "text-semantics": {
        "approval_status": "APPROVED",
        "license_spdx": "MIT",
        "commercial_use_allowed": True,
        "usage_cost": "free",
        "local_inference_only": True,
        "external_media_upload": False,
        "source_url": "https://huggingface.co/MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli",
        "training_data_review_status": "DOCUMENTED_MIXED_UPSTREAM_DATASETS",
    },
    "florence-logo": {
        "approval_status": "APPROVED",
        "license_spdx": "MIT",
        "commercial_use_allowed": True,
        "usage_cost": "free",
        "local_inference_only": True,
        "external_media_upload": False,
        "source_url": "https://huggingface.co/microsoft/Florence-2-base",
        "training_data_review_status": "DOCUMENTED_BY_UPSTREAM_MODEL_CARD",
    },
    "grounding-dino-tiny": {
        "approval_status": "APPROVED",
        "license_spdx": "Apache-2.0",
        "commercial_use_allowed": True,
        "usage_cost": "free",
        "local_inference_only": True,
        "external_media_upload": False,
        "source_url": "https://huggingface.co/IDEA-Research/grounding-dino-tiny",
        "training_data_review_status": "DOCUMENTED_BY_UPSTREAM_MODEL_CARD",
    },
    "siglip-ranker": {
        "approval_status": "APPROVED",
        "license_spdx": "Apache-2.0",
        "commercial_use_allowed": True,
        "usage_cost": "free",
        "local_inference_only": True,
        "external_media_upload": False,
        "source_url": "https://huggingface.co/google/siglip-base-patch16-224",
        "training_data_review_status": "DOCUMENTED_BY_UPSTREAM_MODEL_CARD",
    },
    "qwen2-vl-2b": {
        "approval_status": "APPROVED",
        "license_spdx": "Apache-2.0",
        "commercial_use_allowed": True,
        "usage_cost": "free",
        "local_inference_only": True,
        "external_media_upload": False,
        "source_url": "https://huggingface.co/Qwen/Qwen2-VL-2B-Instruct",
        "training_data_review_status": "DOCUMENTED_BY_UPSTREAM_MODEL_CARD",
    },
    "easyocr": {
        "approval_status": "APPROVED",
        "license_spdx": "Apache-2.0",
        "commercial_use_allowed": True,
        "usage_cost": "free",
        "local_inference_only": True,
        "external_media_upload": False,
        "source_url": "https://github.com/JaidedAI/EasyOCR",
        "training_data_review_status": "UPSTREAM_DETAILS_LIMITED",
    },
    "phi35-vision-int4": {
        "approval_status": "BLOCKED",
        "license_spdx": "MIT",
        "commercial_use_allowed": True,
        "usage_cost": "free",
        "local_inference_only": True,
        "external_media_upload": False,
        "source_url": "https://huggingface.co/microsoft/Phi-3.5-vision-instruct-onnx",
        "training_data_review_status": "DOCUMENTED_BY_UPSTREAM_MODEL_CARD",
        "blocked_reason": "Benchmark logo semantics đạt specificity 0%; quá chậm và không phân biệt được bảng hiệu trong cảnh.",
    },
}



MARKER_NAME = "biliflow-install.json"
LOCK_RELATIVE = Path("config") / "models.lock.json"
HF_FILE_URL = "https://huggingface.co/{repo}/resolve/{revision}/{name}"
CHUNK = 1024 * 1024
TIMEOUT_SECONDS = 60
RETRIES = 5
RETRY_DELAYS = (2, 4, 8, 16, 30)
MAX_MANIFEST_BYTES = 1024 * 1024


class DownloadRefused(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise urllib.error.HTTPError(newurl, code, "redirect away from https refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def default_opener():
    return urllib.request.build_opener(_HttpsOnlyRedirects())


def load_lock(path: Path) -> dict:
    lock = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(lock, dict) or lock.get("version") != 1 or not isinstance(lock.get("models"), dict):
        raise DownloadRefused(f"{path} không phải lock model phiên bản 1.")
    return lock


def locked_entry(lock: dict, key: str) -> dict:
    spec = MODELS[key]
    entry = lock["models"].get(spec["directory"])
    if not isinstance(entry, dict):
        raise DownloadRefused(f"Lock không có model {key} ({spec['directory']}).")
    if entry.get("problems"):
        raise DownloadRefused(f"Lock của {key} còn vấn đề chưa xử lý: {entry['problems']}")
    if entry.get("download_key") != key:
        raise DownloadRefused(f"Lock ghi {spec['directory']} cho {entry.get('download_key')!r}, không phải {key}.")
    files = entry.get("files")
    if not isinstance(files, list) or not files:
        raise DownloadRefused(f"Lock của {key} không có file.")
    for item in files:
        name = item.get("name") if isinstance(item, dict) else None
        if (not isinstance(name, str) or not name or name.startswith("/") or "\\" in name
                or any(part in {"", ".", ".."} for part in name.split("/"))):
            raise DownloadRefused(f"Lock của {key} có tên file không hợp lệ: {name!r}")
        if not (isinstance(item.get("sha256"), str) and len(item["sha256"]) == 64
                and isinstance(item.get("bytes"), int) and item["bytes"] >= 0):
            raise DownloadRefused(f"Lock của {key} thiếu SHA-256 hoặc kích thước của {name}.")
        url = item.get("url")
        if url is not None and not str(url).startswith("https://"):
            raise DownloadRefused(f"Lock của {key} có URL không phải https: {name}")
    text = entry.get("manifest_text")
    if not isinstance(text, str) or hashlib.sha256(text.encode("utf-8")).hexdigest() != entry.get("manifest_sha256"):
        raise DownloadRefused(f"Manifest trong lock của {key} không khớp SHA-256 của nó.")
    if entry.get("source") == "huggingface":
        if entry.get("repo_id") != spec.get("repo_id") or not re.fullmatch(r"[0-9a-f]{40}", str(entry.get("revision"))):
            raise DownloadRefused(f"Lock của {key} không khớp repo hoặc thiếu revision.")
    elif entry.get("source") == "easyocr-release":
        if any(not item.get("url") or not item.get("archive_member") for item in files):
            raise DownloadRefused(f"Lock của {key} thiếu URL hoặc tên file trong gói nén.")
    else:
        raise DownloadRefused(f"Nguồn {entry.get('source')!r} của {key} không được hỗ trợ.")
    return entry


def _check_existing(target: Path, entry: dict) -> list[dict]:
    """The locked files still missing; refuses a folder holding anything that differs."""
    expected = {item["name"]: item for item in entry["files"]}
    missing = []
    if target.exists():
        for path in sorted(target.rglob("*")):
            relative = path.relative_to(target).as_posix()
            if path.is_dir() or relative == "manifest.json" or relative.endswith(".part"):
                continue
            if relative not in expected:
                raise DownloadRefused(f"{target} có file lạ {relative}; không ghi vào thư mục này.")
    for name, item in expected.items():
        path = target / name
        if path.is_file():
            if path.stat().st_size != item["bytes"] or sha256_file(path) != item["sha256"]:
                raise DownloadRefused(f"{path} đã có nhưng khác lock; không ghi đè.")
        else:
            missing.append(item)
    manifest = target / "manifest.json"
    if manifest.exists() and manifest.read_bytes() != entry["manifest_text"].encode("utf-8"):
        raise DownloadRefused(f"{manifest} đã có nhưng khác lock; không ghi đè.")
    return missing


def fetch(url: str, part: Path, expected_bytes: int | None, *, opener=None, sleep=time.sleep,
          report=print) -> None:
    """Download ``url`` into ``part``, continuing from its current size after a dropped connection."""
    if not url.startswith("https://"):
        raise DownloadRefused(f"Chỉ tải qua https: {url}")
    opener = opener or default_opener()
    part.parent.mkdir(parents=True, exist_ok=True)
    attempt = 0
    while True:
        have = part.stat().st_size if part.exists() else 0
        if expected_bytes is not None and have > expected_bytes:
            part.unlink()
            have = 0
        if expected_bytes is not None and have == expected_bytes:
            return
        request = urllib.request.Request(url, headers={"User-Agent": "BiliFlow-installer"})
        if have:
            request.add_header("Range", f"bytes={have}-")
        try:
            with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                status = getattr(response, "status", 200)
                resumed = status == 206 and str(response.headers.get("Content-Range", "")).startswith(
                    f"bytes {have}-")
                if have and not resumed:
                    have = 0  # The server sent the whole file again.
                with part.open("ab" if have else "wb") as handle:
                    for chunk in iter(lambda: response.read(CHUNK), b""):
                        handle.write(chunk)
                        have += len(chunk)
                        if expected_bytes is not None and have > expected_bytes:
                            raise DownloadRefused(f"{url} dài hơn lock ({expected_bytes} byte).")
            if expected_bytes is None or have == expected_bytes:
                return
            raise OSError(f"kết nối đóng ở {have}/{expected_bytes} byte")
        except DownloadRefused:
            raise
        except urllib.error.HTTPError as error:
            if error.code == 416 and have:
                part.unlink()
            elif error.code < 500 and error.code != 429:
                raise DownloadRefused(f"{url}: HTTP {error.code}") from None
            reason = f"HTTP {error.code}"
        except (OSError, urllib.error.URLError, http.client.HTTPException) as error:
            reason = str(error) or type(error).__name__
        if attempt >= RETRIES:
            raise DownloadRefused(f"{url}: rớt mạng quá {RETRIES} lần ({reason}); "
                                  "chạy lại để tải tiếp.")
        report(f"  rớt mạng ({reason}); thử lại sau {RETRY_DELAYS[attempt]} s, tải tiếp từ {have} byte")
        sleep(RETRY_DELAYS[attempt])
        attempt += 1


def _verify_and_place(part: Path, final: Path, item: dict) -> None:
    if part.stat().st_size != item["bytes"] or sha256_file(part) != item["sha256"]:
        part.unlink()
        raise DownloadRefused(f"{item['name']}: SHA-256 khác lock; "
                              "đã bỏ phần tải, chạy lại để tải lại.")
    os.replace(part, final)


def _extract_member(archive: Path, member: str, part: Path, limit: int) -> None:
    with zipfile.ZipFile(archive) as bundle:
        matches = [info for info in bundle.infolist()
                   if not info.is_dir() and info.filename.replace("\\", "/").rsplit("/", 1)[-1] == member]
        if len(matches) != 1 or matches[0].file_size != limit:
            raise DownloadRefused(f"{archive.name} không có đúng một {member} đúng kích thước.")
        with bundle.open(matches[0]) as source, part.open("wb") as handle:
            written = 0
            for chunk in iter(lambda: source.read(CHUNK), b""):
                written += len(chunk)
                if written > limit:
                    raise DownloadRefused(f"{member} lớn hơn lock.")
                handle.write(chunk)


def install_locked(root: Path, key: str, lock: dict, *, opener=None, sleep=time.sleep, report=print) -> str:
    """Download one locked model into ``root/models``; returns "ready" or "downloaded"."""
    license_metadata = LICENSE_METADATA[key]
    if license_metadata["approval_status"] != "APPROVED":
        raise DownloadRefused(f"{key}: {license_metadata['approval_status']} "
                              f"({license_metadata.get('blocked_reason', 'chưa duyệt giấy phép')})")
    entry = locked_entry(lock, key)
    target = root / "models" / MODELS[key]["directory"]
    missing = _check_existing(target, entry)
    manifest = target / "manifest.json"
    if not missing and manifest.exists():
        report(f"{key}: đã đủ theo lock")
        return "ready"
    target.mkdir(parents=True, exist_ok=True)
    for index, item in enumerate(missing, 1):
        final = target / item["name"]
        final.parent.mkdir(parents=True, exist_ok=True)
        part = final.with_name(final.name + ".part")
        report(f"{key}: [{index}/{len(missing)}] {item['name']} ({item['bytes'] / 1048576:.1f} MiB)")
        if entry["source"] == "huggingface":
            url = HF_FILE_URL.format(repo=entry["repo_id"], revision=entry["revision"],
                                     name=urllib.parse.quote(item["name"]))
            fetch(url, part, item["bytes"], opener=opener, sleep=sleep, report=report)
        else:
            archive_name = urllib.parse.urlsplit(item["url"]).path.rsplit("/", 1)[-1]
            archive = root / "cache" / "model-downloads" / (archive_name + ".part")
            fetch(item["url"], archive, None, opener=opener, sleep=sleep, report=report)
            try:
                _extract_member(archive, item["archive_member"], part, item["bytes"])
            except zipfile.BadZipFile:
                archive.unlink()
                raise DownloadRefused(f"{archive_name} hỏng; đã bỏ, chạy lại để tải lại.") from None
            archive.unlink()
        _verify_and_place(part, final, item)
    temporary = manifest.with_name("manifest.json.part")
    temporary.write_bytes(entry["manifest_text"].encode("utf-8"))
    os.replace(temporary, manifest)
    report(f"{key}: xong, đã kiểm SHA-256 {len(entry['files'])} file")
    return "downloaded"


def _download_latest(project_root: Path, key: str) -> int:
    """The old unlocked path: newest revision via huggingface_hub (dev roots only)."""
    cache_root = project_root / "cache" / "huggingface"
    temp_root = project_root / "temp"
    cache_root.mkdir(parents=True, exist_ok=True)
    temp_root.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(cache_root)
    os.environ["HF_HUB_CACHE"] = str(cache_root / "hub")
    os.environ["HF_XET_CACHE"] = str(cache_root / "xet")
    os.environ["TEMP"] = str(temp_root)
    os.environ["TMP"] = str(temp_root)
    from huggingface_hub import HfApi, snapshot_download

    spec = MODELS[key]
    if spec.get("lock_only"):
        raise SystemExit(f"{key} chỉ tải theo lock.")
    license_metadata = LICENSE_METADATA[key]
    if license_metadata["approval_status"] != "APPROVED":
        reason = license_metadata.get("blocked_reason", "license/data audit is incomplete")
        raise SystemExit(
            f"Download denied for {key}: {license_metadata['approval_status']} ({reason})"
        )
    target = project_root / "models" / spec["directory"]
    target.mkdir(parents=True, exist_ok=True)

    info = HfApi().model_info(spec["repo_id"])
    expected_license = spec.get("expected_hf_license")
    reported_license = str(getattr(info.card_data, "license", "") or "").casefold()
    if expected_license and reported_license != expected_license:
        raise SystemExit(
            f"Download denied for {key}: expected Hugging Face license "
            f"{expected_license!r}, received {reported_license!r}"
        )
    snapshot_download(
        repo_id=spec["repo_id"],
        revision=info.sha,
        allow_patterns=spec["allow_patterns"],
        local_dir=target,
    )

    files = []
    for path in sorted(target.rglob("*")):
        if (
            path.is_file()
            and path.name != "manifest.json"
            and ".cache" not in path.relative_to(target).parts
        ):
            files.append(
                {
                    "name": path.relative_to(target).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )

    manifest = {
        "model": key,
        "repo_id": spec["repo_id"],
        "revision": info.sha,
        "backend": spec["backend"],
        **license_metadata,
        "target_labels": spec["target_labels"],
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }
    for optional_key in (
        "architecture", "label_names", "positive_index", "aggregation", "aggregation_top_k"
    ):
        if optional_key in spec:
            manifest[optional_key] = spec[optional_key]
    (target / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download approved BiliFlow models")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--model", choices=sorted(MODELS))
    which.add_argument("--all", action="store_true", help="Mọi model có trong lock")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--lock", type=Path, default=None, help="Mặc định: <project-root>/config/models.lock.json")
    parser.add_argument("--latest", action="store_true",
                        help="Không theo lock: tải revision mới nhất (chỉ để thử trong thư mục dev)")
    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    if not (root / MARKER_NAME).is_file():
        raise SystemExit(f"{root} không có {MARKER_NAME}; chỉ tải vào thư mục cài BiliFlow.")
    if args.latest:
        if args.all:
            raise SystemExit("--latest chỉ dùng với --model.")
        return _download_latest(root, args.model)
    try:
        lock = load_lock(args.lock or root / LOCK_RELATIVE)
        if args.all:
            directories = {spec["directory"]: key for key, spec in MODELS.items()}
            keys = [directories[name] for name in sorted(lock["models"]) if name in directories]
        else:
            keys = [args.model]
        for key in keys:
            install_locked(root, key, lock)
    except (DownloadRefused, OSError, ValueError) as error:
        print(f"Lỗi: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
