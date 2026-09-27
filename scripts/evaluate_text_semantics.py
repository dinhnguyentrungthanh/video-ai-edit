from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from biliflow.text_semantics import LocalZeroShotTextClassifier


CASES = [
    ("advertisement", "NGUONC.COM chia sẻ dữ liệu phim, hoạt hình miễn phí - cập nhật nhanh"),
    ("advertisement", "Bộ phim này do i999.ai tài trợ. Nổ hũ, game bài, rút tiền 24/7"),
    ("subtitle", "Ngày mai chúng ta sẽ gặp lại ở nhà ga."),
    ("subtitle", "Anh có thực sự hiểu điều em muốn nói không?"),
    ("credits", "Executive Producer - Production Manager - Director of Photography"),
    ("scene_text", "Cửa hàng tiện lợi, giá 25.000 đồng"),
    ("credits", "NETFLIX SERIES"),
    ("scene_text", "Cấm đỗ xe"),
]

LABEL_SETS = {
    "vi_current": {
        "advertisement": "quảng cáo, nội dung tài trợ hoặc lời kêu gọi mua, đăng ký hay truy cập",
        "subtitle": "phụ đề lời thoại của nhân vật trong phim",
        "credits": "danh đề, logo hãng phim hoặc thông tin sản xuất phim",
        "scene_text": "chữ tự nhiên nằm trong bối cảnh của cảnh phim",
        "other": "nội dung khác hoặc không đủ thông tin để phân loại",
    },
    "en_concrete": {
        "advertisement": "an advertisement asking viewers to visit, buy, register, gamble, or use a service",
        "subtitle": "a sentence spoken in dialogue between movie characters",
        "credits": "a movie title, studio logo, or film production credit",
        "scene_text": "a sign, label, or price physically visible inside the filmed scene",
        "other": "unrelated or ambiguous text",
    },
    "en_short": {
        "advertisement": "commercial advertising",
        "subtitle": "movie dialogue subtitle",
        "credits": "film production credits",
        "scene_text": "text on an object in the scene",
        "other": "other text",
    },
}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "models"
        / "multilingual_minilm_text_semantics",
    )
    parser.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    parser.add_argument(
        "--seed",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "annotations"
        / "text_semantics_seed_v1.json",
    )
    args = parser.parse_args()
    classifier = LocalZeroShotTextClassifier(args.model, args.device)
    configurations = {}
    texts = [text for _, text in CASES]
    for name, labels in LABEL_SETS.items():
        raw = classifier._classifier(
            texts,
            candidate_labels=list(labels.values()),
            hypothesis_template="This text is {}.",
            multi_label=False,
            batch_size=32,
        )
        rows = []
        reverse = {description: key for key, description in labels.items()}
        for (expected, text), output in zip(CASES, raw, strict=True):
            scores = {
                reverse[label]: round(float(score), 6)
                for label, score in zip(output["labels"], output["scores"], strict=True)
            }
            predicted = max(scores, key=scores.get)
            rows.append({
                "text": text,
                "expected": expected,
                "predicted": predicted,
                "correct": predicted == expected,
                "scores": scores,
            })
        configurations[name] = {
            "correct": sum(row["correct"] for row in rows),
            "total": len(rows),
            "rows": rows,
        }
    import torch
    import torch.nn.functional as functional

    seed = json.loads(args.seed.read_text(encoding="utf-8"))
    labels = list(seed["train"])
    train_texts = [text for label in labels for text in seed["train"][label]]
    train_labels = [label for label in labels for _ in seed["train"][label]]
    holdout_texts = [row["text"] for row in seed["holdout"]]
    tokenizer = classifier._classifier.tokenizer
    model = classifier._classifier.model

    def embed(values):
        batches = []
        for offset in range(0, len(values), 32):
            tokens = tokenizer(
                values[offset:offset + 32], padding=True, truncation=True,
                max_length=128, return_tensors="pt",
            ).to(model.device)
            with torch.inference_mode():
                hidden = model.base_model(**tokens).last_hidden_state
                mask = tokens["attention_mask"].unsqueeze(-1).to(hidden.dtype)
                pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1)
            batches.append(functional.normalize(pooled.float(), dim=1).cpu())
        return torch.cat(batches)

    train_embeddings = embed(train_texts)
    centroids = torch.stack([
        functional.normalize(
            train_embeddings[[value == label for value in train_labels]].mean(0), dim=0
        )
        for label in labels
    ])
    holdout_embeddings = embed(holdout_texts)
    similarities = holdout_embeddings @ centroids.T
    embedding_rows = []
    for row, values in zip(seed["holdout"], similarities, strict=True):
        scores = {label: round(float(value), 6) for label, value in zip(labels, values)}
        predicted = max(scores, key=scores.get)
        embedding_rows.append({
            **row,
            "predicted": predicted,
            "correct": predicted == row["label"],
            "scores": scores,
        })
    torch.manual_seed(7)
    head = torch.nn.Linear(train_embeddings.shape[1], len(labels))
    targets = torch.tensor([labels.index(label) for label in train_labels])
    optimizer = torch.optim.AdamW(head.parameters(), lr=0.03, weight_decay=0.08)
    for _ in range(800):
        optimizer.zero_grad()
        loss = functional.cross_entropy(head(train_embeddings), targets)
        loss.backward()
        optimizer.step()
    with torch.inference_mode():
        probabilities = torch.softmax(head(holdout_embeddings), dim=1)
    linear_rows = []
    for row, values in zip(seed["holdout"], probabilities, strict=True):
        scores = {label: round(float(value), 6) for label, value in zip(labels, values)}
        predicted = max(scores, key=scores.get)
        linear_rows.append({
            **row,
            "predicted": predicted,
            "correct": predicted == row["label"],
            "scores": scores,
        })
    result = {
        "embedding_linear_head": {
            "correct": sum(row["correct"] for row in linear_rows),
            "total": len(linear_rows),
            "rows": linear_rows,
        },
        "embedding_centroid": {
            "correct": sum(row["correct"] for row in embedding_rows),
            "total": len(embedding_rows),
            "rows": embedding_rows,
        },
        "zero_shot_configurations": configurations,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
