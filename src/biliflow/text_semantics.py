from __future__ import annotations

import json
import os
import re
import unicodedata
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


SEMANTIC_LABELS = {
    "advertisement": "quảng cáo, nội dung tài trợ hoặc lời kêu gọi mua, đăng ký hay truy cập",
    "subtitle": "phụ đề lời thoại của nhân vật trong phim",
    "credits": "danh đề, logo hãng phim hoặc thông tin sản xuất phim",
    "scene_text": "chữ tự nhiên nằm trong bối cảnh của cảnh phim",
    "other": "nội dung khác hoặc không đủ thông tin để phân loại",
}


def _fold(value: str) -> str:
    value = value.casefold().replace("đ", "d")
    value = "".join(
        character
        for character in unicodedata.normalize("NFD", value)
        if unicodedata.category(character) != "Mn"
    )
    return re.sub(r"\s+", " ", value).strip()


def load_text_policy(path: Path) -> dict:
    path = path.resolve(strict=True)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Text policy must be a JSON object")
    terms = payload.get("always_review_terms", [])
    if not isinstance(terms, list) or not all(isinstance(value, str) for value in terms):
        raise ValueError("always_review_terms must be a list of strings")
    payload["always_review_terms"] = [value.strip() for value in terms if value.strip()]
    return payload


class LocalZeroShotTextClassifier:
    """Local-only multilingual NLI classifier used after OCR tracking finishes."""

    def __init__(self, model_dir: Path, device_name: str = "cuda") -> None:
        model_dir = model_dir.resolve(strict=True)
        if not (model_dir / "manifest.json").exists():
            raise RuntimeError(f"Semantic model manifest is missing in {model_dir}")
        try:
            import torch
            from transformers import (
                AutoModelForSequenceClassification,
                AutoTokenizer,
                pipeline,
            )
        except ImportError as exc:
            raise RuntimeError("Transformers and PyTorch are required for semantic text review") from exc

        use_cuda = device_name == "cuda" and torch.cuda.is_available()
        dtype = torch.float16 if use_cuda else torch.float32
        tokenizer = AutoTokenizer.from_pretrained(
            model_dir, local_files_only=True, use_fast=True
        )
        model = AutoModelForSequenceClassification.from_pretrained(
            model_dir, local_files_only=True, dtype=dtype
        )
        model.eval()
        self._classifier = pipeline(
            "zero-shot-classification",
            model=model,
            tokenizer=tokenizer,
            device=0 if use_cuda else -1,
        )
        self.device = "cuda" if use_cuda else "cpu"

    def __call__(self, text: str) -> dict[str, float]:
        return self.classify_many([text])[0]

    def classify_many(self, texts: list[str], batch_size: int = 32) -> list[dict[str, float]]:
        if not texts:
            return []
        prepared = [text if text.strip() else "(không đọc rõ)" for text in texts]
        raw_results = self._classifier(
            prepared,
            candidate_labels=list(SEMANTIC_LABELS.values()),
            hypothesis_template="Nội dung này là {}.",
            multi_label=False,
            batch_size=batch_size,
        )
        if isinstance(raw_results, dict):
            raw_results = [raw_results]
        outputs = []
        for result in raw_results:
            by_label = {
                str(label): float(score)
                for label, score in zip(result["labels"], result["scores"], strict=True)
            }
            outputs.append(
                {
                    key: round(by_label.get(description, 0.0), 6)
                    for key, description in SEMANTIC_LABELS.items()
                }
            )
        return outputs


class LocalEmbeddingTextClassifier:
    """Small supervised head over local multilingual embeddings.

    The head is rebuilt from a versioned seed set at startup. This makes the
    classifier auditable and lets future confirmed review examples improve it
    without adding brand names or phrases to application code.
    """

    def __init__(
        self, model_dir: Path, seed_path: Path, device_name: str = "cuda"
    ) -> None:
        model_dir = model_dir.resolve(strict=True)
        seed_path = seed_path.resolve(strict=True)
        if not (model_dir / "manifest.json").exists():
            raise RuntimeError(f"Semantic model manifest is missing in {model_dir}")
        seed = json.loads(seed_path.read_text(encoding="utf-8"))
        train = seed.get("train") if isinstance(seed, dict) else None
        if not isinstance(train, dict) or not train:
            raise ValueError("Semantic seed must contain a non-empty train object")
        try:
            import torch
            import torch.nn.functional as functional
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError("Transformers and PyTorch are required for semantic text review") from exc

        use_cuda = device_name == "cuda" and torch.cuda.is_available()
        dtype = torch.float16 if use_cuda else torch.float32
        self._torch = torch
        self._functional = functional
        self._tokenizer = AutoTokenizer.from_pretrained(
            model_dir, local_files_only=True, use_fast=True
        )
        self._model = AutoModelForSequenceClassification.from_pretrained(
            model_dir, local_files_only=True, dtype=dtype
        ).to("cuda" if use_cuda else "cpu")
        self._model.eval()
        self.device = "cuda" if use_cuda else "cpu"
        self.labels = list(train)
        if set(self.labels) != {"advertisement", "subtitle", "credits", "scene_text"}:
            raise ValueError("Semantic seed labels must be advertisement/subtitle/credits/scene_text")
        train_texts = []
        train_targets = []
        for label_index, label in enumerate(self.labels):
            examples = train[label]
            if not isinstance(examples, list) or not examples:
                raise ValueError(f"Semantic seed label has no examples: {label}")
            for example in examples:
                train_texts.append(str(example))
                train_targets.append(label_index)
        embeddings = self._embed(train_texts)
        torch.manual_seed(7)
        self._head = torch.nn.Linear(embeddings.shape[1], len(self.labels))
        targets = torch.tensor(train_targets)
        optimizer = torch.optim.AdamW(
            self._head.parameters(), lr=0.03, weight_decay=0.08
        )
        for _ in range(800):
            optimizer.zero_grad()
            loss = functional.cross_entropy(self._head(embeddings), targets)
            loss.backward()
            optimizer.step()
        self._head.eval()
        self.method = "multilingual_embedding_linear_head"
        self.seed_path = seed_path

    def _embed(self, texts: list[str]):
        torch = self._torch
        batches = []
        for offset in range(0, len(texts), 32):
            values = [text if text.strip() else "(không đọc rõ)" for text in texts[offset:offset + 32]]
            tokens = self._tokenizer(
                values,
                padding=True,
                truncation=True,
                max_length=128,
                return_tensors="pt",
            ).to(self._model.device)
            with torch.inference_mode():
                hidden = self._model.base_model(**tokens).last_hidden_state
                mask = tokens["attention_mask"].unsqueeze(-1).to(hidden.dtype)
                pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1)
            batches.append(
                self._functional.normalize(pooled.float(), dim=1).cpu()
            )
        return torch.cat(batches)

    def __call__(self, text: str) -> dict[str, float]:
        return self.classify_many([text])[0]

    def classify_many(self, texts: list[str], batch_size: int = 32) -> list[dict[str, float]]:
        del batch_size
        if not texts:
            return []
        embeddings = self._embed(texts)
        with self._torch.inference_mode():
            probabilities = self._torch.softmax(self._head(embeddings), dim=1)
        rows = []
        for values in probabilities:
            row = {
                label: round(float(score), 6)
                for label, score in zip(self.labels, values, strict=True)
            }
            row["other"] = 0.0
            rows.append(row)
        return rows


def _representative_text(track: dict) -> str:
    unique = []
    for raw in track.get("sample_text", []):
        text = re.sub(r"\s+", " ", str(raw)).strip()
        if text and text not in unique:
            unique.append(text)
    selected = sorted(unique, key=len, reverse=True)[:5]
    return " | ".join(selected)


def classify_text_track(
    track: dict,
    *,
    semantic_scores: dict[str, float],
    analysis_size: list[int] | tuple[int, int],
    video_duration: float,
    policy: dict,
) -> dict:
    result = deepcopy(track)
    scores = {key: float(semantic_scores.get(key, 0.0)) for key in SEMANTIC_LABELS}
    text = _representative_text(track)
    folded = _fold(text)
    policy_hits = [
        term
        for term in policy.get("always_review_terms", [])
        if _fold(term) and _fold(term) in folded
    ]

    width, height = (analysis_size if len(analysis_size) == 2 else (1, 1))
    box = track.get("union_box") or [0, 0, 0, 0]
    box_width_ratio = max(0.0, float(box[2]) - float(box[0])) / max(1.0, float(width))
    box_height_ratio = max(0.0, float(box[3]) - float(box[1])) / max(1.0, float(height))
    start = float(track.get("start_seconds", 0.0))
    end = float(track.get("end_seconds", start))
    duration = max(0.0, end - start)
    progress = start / video_duration if video_duration > 0 else 0.0
    zone = str(track.get("zone", ""))
    persistent = bool(track.get("persistent"))
    overlay_signal = persistent and (
        zone.startswith(("top-", "bottom-"))
        or zone.endswith(("-left", "-right"))
        or box_width_ratio >= 0.30
    )
    short_subtitle_shape = (
        zone == "subtitle" and duration <= 12.0 and box_height_ratio <= 0.18
    )
    end_credit_roll = progress >= 0.90 and len(text) >= 3

    ad_score = scores["advertisement"]
    subtitle_score = scores["subtitle"]
    credits_score = scores["credits"]
    scene_score = scores["scene_text"]
    strongest_non_ad = max(subtitle_score, credits_score, scene_score)
    ad_likely = ad_score >= 0.45 and ad_score >= strongest_non_ad - 0.03
    subtitle_likely = (
        short_subtitle_shape
        and subtitle_score >= 0.52
        and subtitle_score >= ad_score + 0.12
    )
    credits_likely = (
        progress >= 0.85
        and credits_score >= 0.52
        and credits_score >= ad_score + 0.12
    )
    scene_likely = (
        not overlay_signal
        and scene_score >= 0.55
        and scene_score >= ad_score + 0.15
    )
    compact_text_length = len(re.sub(r"[^0-9a-z]+", "", _fold(text)))
    compact_non_ad_visual = (
        progress < 0.90
        and ad_score < 0.20
        and max(credits_score, scene_score) >= 0.65
        and (
            not persistent
            or box_width_ratio < 0.30
            or compact_text_length <= 4
        )
    )

    if policy_hits:
        candidate = True
        route = "REVIEW_POLICY_OVERRIDE"
        priority = "high"
        reason = "Khớp quy tắc review do người dùng cấu hình; vẫn cần xác nhận trước khi sửa"
    elif end_credit_roll and not (ad_score >= 0.85 and overlay_signal):
        candidate = False
        route = "LIKELY_CREDITS"
        priority = "low"
        reason = "Mật độ chữ và vị trí cuối video nghiêng về credit roll"
    elif compact_non_ad_visual:
        candidate = False
        route = "LIKELY_SCENE_TEXT"
        priority = "low"
        reason = "Chữ ngắn/nhỏ trong cảnh và điểm quảng cáo thấp"
    elif ad_likely:
        candidate = True
        route = "REVIEW_AD_LIKELY"
        priority = "high" if ad_score >= 0.62 or overlay_signal else "medium"
        reason = "Model ngữ nghĩa nghiêng về quảng cáo; cần người dùng xác nhận"
    elif subtitle_likely:
        candidate = False
        route = "LIKELY_SUBTITLE"
        priority = "low"
        reason = "Ngữ nghĩa, vị trí và thời lượng cùng nghiêng về phụ đề"
    elif credits_likely:
        candidate = False
        route = "LIKELY_CREDITS"
        priority = "low"
        reason = "Ngữ nghĩa và vị trí cuối video nghiêng về danh đề/thông tin sản xuất"
    elif scene_likely:
        candidate = False
        route = "LIKELY_SCENE_TEXT"
        priority = "low"
        reason = "Model nghiêng về chữ nằm trong bối cảnh phim"
    elif not overlay_signal and ad_score < 0.20:
        candidate = False
        route = "LOW_AD_UNCERTAIN"
        priority = "low"
        reason = (
            "Điểm quảng cáo thấp và chữ không bám cố định; giữ trong ứng viên audit "
            "thay vì bắt buộc duyệt"
        )
    elif overlay_signal:
        candidate = True
        route = "REVIEW_UNCERTAIN_OVERLAY"
        priority = "medium"
        reason = "Chữ bám cố định như lớp phủ nhưng ngữ nghĩa chưa đủ chắc; cần xem thủ công"
    else:
        candidate = True
        route = "REVIEW_UNCERTAIN"
        priority = "low"
        reason = "Chưa đủ bằng chứng để loại khỏi hàng đợi; cần xem thủ công"

    result.update(
        {
            "semantic_scores": {key: round(value, 6) for key, value in scores.items()},
            "semantic_top_label": max(scores, key=scores.get),
            "ad_probability": round(ad_score, 6),
            "review_candidate": candidate,
            "routing": route,
            "review_priority": priority,
            "reason": reason,
            "policy_hits": policy_hits,
            "visual_features": {
                "overlay_signal": overlay_signal,
                "short_subtitle_shape": short_subtitle_shape,
                "box_width_ratio": round(box_width_ratio, 4),
                "box_height_ratio": round(box_height_ratio, 4),
                "video_progress": round(progress, 4),
                "end_credit_roll": end_credit_roll,
                "compact_non_ad_visual": compact_non_ad_visual,
            },
        }
    )
    return result


def classify_text_report(
    *,
    project_root: Path,
    input_report: Path,
    output_report: Path,
    model_dir: Path,
    policy_path: Path,
    seed_path: Path | None = None,
    device_name: str = "cuda",
    classifier: Callable[[str], dict[str, float]] | None = None,
) -> dict:
    root = project_root.resolve(strict=True)
    reports_root = (root / "reports").resolve(strict=True)
    models_root = (root / "models").resolve(strict=True)
    input_report = input_report.resolve(strict=True)
    output_report = output_report.resolve()
    model_dir = model_dir.resolve()
    if reports_root not in input_report.parents:
        raise ValueError("Input text report must stay inside the project reports directory")
    if reports_root not in output_report.parents:
        raise ValueError("Output text report must stay inside the project reports directory")
    if model_dir != models_root and models_root not in model_dir.parents:
        raise ValueError("Semantic model must stay inside the project models directory")

    payload = json.loads(input_report.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("tracks"), list):
        raise ValueError("Input report is not an OCR text report")
    policy = load_text_policy(policy_path)
    if classifier is None:
        if seed_path is None:
            raise ValueError("seed_path is required for the embedding text classifier")
        semantic_classifier = LocalEmbeddingTextClassifier(
            model_dir, seed_path, device_name
        )
    else:
        semantic_classifier = classifier

    texts = [_representative_text(track) for track in payload["tracks"]]
    if hasattr(semantic_classifier, "classify_many"):
        score_rows = semantic_classifier.classify_many(texts)
    else:
        score_rows = [semantic_classifier(text) for text in texts]
    enriched_tracks = []
    for track, scores in zip(payload["tracks"], score_rows, strict=True):
        enriched = classify_text_track(
            track,
            semantic_scores=scores,
            analysis_size=payload.get("analysis_size") or [1, 1],
            video_duration=float(payload.get("duration_seconds", 0.0)),
            policy=policy,
        )
        preview = enriched.get("preview")
        if preview:
            original = (input_report.parent / str(preview)).resolve()
            enriched["preview"] = Path(
                os.path.relpath(original, output_report.parent)
            ).as_posix()
        enriched_tracks.append(enriched)

    result = deepcopy(payload)
    result.update(
        {
            "schema_version": 2,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_text_report": input_report.relative_to(root).as_posix(),
            "semantic_model": model_dir.relative_to(root).as_posix(),
            "semantic_device": getattr(semantic_classifier, "device", device_name),
            "semantic_method": getattr(semantic_classifier, "method", "custom"),
            "semantic_seed": (
                Path(seed_path).resolve().relative_to(root).as_posix()
                if seed_path is not None
                else None
            ),
            "text_policy": policy,
            "tracks": enriched_tracks,
            "routing_counts": {
                route: sum(track["routing"] == route for track in enriched_tracks)
                for route in sorted({track["routing"] for track in enriched_tracks})
            },
            "review_candidate_count": sum(
                bool(track["review_candidate"]) for track in enriched_tracks
            ),
            "safety": {
                "automatic_blur": False,
                "automatic_delete": False,
                "note": "Semantic routing only prioritizes review. Every edit still requires explicit approval.",
            },
        }
    )
    output_report.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_report.with_suffix(output_report.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(output_report)
    return result
