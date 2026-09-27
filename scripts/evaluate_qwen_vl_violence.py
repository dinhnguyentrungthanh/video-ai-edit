from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

import cv2
import torch
from transformers import AutoModelForImageTextToText, AutoProcessor


PROMPT = (
    "Do these images show people boxing, punching, or physically fighting each other? "
    "Answer with a single word: yes or no."
)


def sample_frames(video: Path, directory: Path) -> list[Path]:
    capture = cv2.VideoCapture(str(video))
    count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    paths = []
    for index, fraction in enumerate((0.1, 0.3, 0.5, 0.7, 0.9), start=1):
        capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, round((count - 1) * fraction)))
        ok, frame = capture.read()
        if ok:
            path = directory / f"frame-{index}.jpg"
            cv2.imwrite(str(path), frame)
            paths.append(path)
    capture.release()
    return paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()

    processor = AutoProcessor.from_pretrained(
        args.model, local_files_only=True, min_pixels=224 * 224, max_pixels=384 * 384
    )
    model = AutoModelForImageTextToText.from_pretrained(
        args.model, local_files_only=True, dtype=torch.float16
    ).to(args.device)
    model.eval()

    clips = []
    for label_names, expected in ((("pos", "positive"), True), (("neg", "negative"), False)):
        label_dir = next((args.benchmark / name for name in label_names if (args.benchmark / name).is_dir()), None)
        if label_dir is None:
            continue
        for video in sorted(label_dir.glob("*.mp4")):
            if video.name.startswith(("source-", "sparring-source")):
                continue
            clips.append((video, expected))

    rows = []
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="biliflow-qwen-vl-") as temporary:
        temporary_root = Path(temporary)
        for clip_index, (video, expected) in enumerate(clips):
            frame_dir = temporary_root / f"clip-{clip_index:03d}"
            frame_dir.mkdir()
            frames = sample_frames(video, frame_dir)
            messages = [{
                "role": "user",
                "content": (
                    [{"type": "image", "path": str(frame)} for frame in frames]
                    + [{"type": "text", "text": PROMPT}]
                ),
            }]
            inputs = processor.apply_chat_template(
                messages,
                add_generation_prompt=True,
                tokenize=True,
                return_dict=True,
                return_tensors="pt",
            ).to(model.device)
            with torch.inference_mode():
                generated = model.generate(**inputs, do_sample=False, max_new_tokens=8)
            answer = processor.decode(
                generated[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True
            ).strip().upper()
            predicted = answer.startswith("YES")
            rows.append({
                "clip": video.as_posix(),
                "expected_violence": expected,
                "answer": answer,
                "predicted_violence": predicted,
                "correct": predicted == expected,
            })
            print(f"{clip_index + 1}/{len(clips)} {video.name}: {answer}", flush=True)

    tp = sum(r["expected_violence"] and r["predicted_violence"] for r in rows)
    fn = sum(r["expected_violence"] and not r["predicted_violence"] for r in rows)
    tn = sum(not r["expected_violence"] and not r["predicted_violence"] for r in rows)
    fp = sum(not r["expected_violence"] and r["predicted_violence"] for r in rows)
    recall = tp / (tp + fn) if tp + fn else 0.0
    specificity = tn / (tn + fp) if tn + fp else 0.0
    payload = {
        "model": "Qwen/Qwen2-VL-2B-Instruct",
        "license": "Apache-2.0",
        "prompt": PROMPT,
        "samples": len(rows),
        "confusion": {"tp": tp, "fn": fn, "tn": tn, "fp": fp},
        "metrics": {
            "recall": round(recall, 6),
            "specificity": round(specificity, 6),
            "balanced_accuracy": round((recall + specificity) / 2, 6),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "peak_cuda_memory_bytes": (
                torch.cuda.max_memory_allocated() if torch.cuda.is_available() else 0
            ),
        },
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["metrics"], indent=2))


if __name__ == "__main__":
    main()
