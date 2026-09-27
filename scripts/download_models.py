from __future__ import annotations

import argparse
import hashlib
import json
import os
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Download approved BiliFlow models")
    parser.add_argument("--model", choices=sorted(MODELS), required=True)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    cache_root = args.project_root / "cache" / "huggingface"
    temp_root = args.project_root / "temp"
    cache_root.mkdir(parents=True, exist_ok=True)
    temp_root.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(cache_root)
    os.environ["HF_HUB_CACHE"] = str(cache_root / "hub")
    os.environ["HF_XET_CACHE"] = str(cache_root / "xet")
    os.environ["TEMP"] = str(temp_root)
    os.environ["TMP"] = str(temp_root)
    from huggingface_hub import HfApi, snapshot_download

    spec = MODELS[args.model]
    license_metadata = LICENSE_METADATA[args.model]
    if license_metadata["approval_status"] != "APPROVED":
        reason = license_metadata.get("blocked_reason", "license/data audit is incomplete")
        raise SystemExit(
            f"Download denied for {args.model}: {license_metadata['approval_status']} ({reason})"
        )
    target = args.project_root / "models" / spec["directory"]
    target.mkdir(parents=True, exist_ok=True)

    info = HfApi().model_info(spec["repo_id"])
    expected_license = spec.get("expected_hf_license")
    reported_license = str(getattr(info.card_data, "license", "") or "").casefold()
    if expected_license and reported_license != expected_license:
        raise SystemExit(
            f"Download denied for {args.model}: expected Hugging Face license "
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
        "model": args.model,
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


if __name__ == "__main__":
    raise SystemExit(main())
