from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import torch
import numpy as np
from PIL import Image

from biliflow.performance import ScanPerformance
from biliflow.brand_memory import (
    approved_brand_memory_region,
    load_brand_memory,
    match_region_memory,
    tighten_brand_foreground_region,
)
from biliflow.florence_regions import (
    consolidate_region_proposals,
    looks_like_site_mark,
    prioritize_brand_region_proposals,
    select_brand_region_proposals,
    select_compact_overlay_proposals,
    select_focus_region_proposals,
)
from biliflow.visual_logo_scanner import localization_focus_box
from biliflow.license_policy import ensure_model_allowed


GROUNDING_TASK = "<CAPTION_TO_PHRASE_GROUNDING>"
GROUNDING_TEXT = "a company logo, platform logo, studio logo, watermark, or promotional banner"
OCR_TASK = "<OCR_WITH_REGION>"


def _inside(root: Path, path: Path, label: str, *, must_exist: bool = True) -> Path:
    resolved_root = root.resolve(strict=True)
    resolved = path.resolve(strict=must_exist)
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError(f"{label} must stay inside {resolved_root}")
    return resolved


def _timestamp(interval: dict[str, Any]) -> float:
    if interval.get("strongest_timestamp_seconds") is not None:
        return float(interval["strongest_timestamp_seconds"])
    match = re.search(r"-(\d+(?:\.\d+)?)s\.jpg$", str(interval.get("strongest_frame", "")))
    if not match:
        return (float(interval["start_seconds"]) + float(interval["end_seconds"])) / 2
    return float(match.group(1))


def _confirmed_brand(interval: dict[str, Any]) -> str | None:
    generic_labels = {
        "visual brand/logo candidate", "persistent external logo / watermark",
        "branded end card / channel promotion", "opening boundary review",
        "unknown brand", "unknown", "watermark", "logo", "brand logo",
        "company logo", "platform logo", "studio logo", "channel watermark",
        "promotional banner", "website banner", "sponsor logo",
    }
    label = str(interval.get("predicted_label", "")).strip()
    if label and label.casefold() not in generic_labels:
        return label
    answer = str(interval.get("visual_logo_confirmation", {}).get("answer", ""))
    parts = re.split(r"\s*[|:]\s*", answer, maxsplit=1)
    if len(parts) == 2 and parts[1].strip().casefold() not in generic_labels:
        return parts[1].strip()
    return None


def _label_matches_brand(labels: list[str] | tuple[str, ...], brand: str | None) -> bool:
    brand_token = re.sub(r"[^a-z0-9]+", "", (brand or "").casefold())
    if not brand_token:
        return False
    for raw_label in labels:
        label_token = re.sub(r"[^a-z0-9]+", "", str(raw_label).casefold())
        if not label_token:
            continue
        if (
            brand_token in label_token
            or (len(label_token) >= 4 and label_token in brand_token)
            or (len(label_token) == 1 and label_token == brand_token[0])
        ):
            return True
    return False


def _extract_frame(ffmpeg: Path, video: Path, timestamp: float) -> Image.Image:
    command = [
        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-ss", f"{timestamp:.3f}",
        "-i", str(video), "-frames:v", "1", "-an", "-sn", "-f", "image2pipe",
        "-vcodec", "png", "pipe:1",
    ]
    completed = subprocess.run(command, check=True, capture_output=True)
    return Image.open(io.BytesIO(completed.stdout)).convert("RGB")


def _run_task(model, processor, image: Image.Image, task: str, text: str | None) -> dict:
    prompt = task + (text or "")
    inputs = processor(text=prompt, images=image, return_tensors="pt")
    inputs = {
        key: value.to(model.device, dtype=torch.float16)
        if key == "pixel_values" else value.to(model.device)
        for key, value in inputs.items()
    }
    with torch.inference_mode():
        generated_ids = model.generate(
            input_ids=inputs["input_ids"], pixel_values=inputs["pixel_values"],
            max_new_tokens=512, do_sample=False, num_beams=3,
        )
    generated = processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
    return processor.post_process_generation(
        generated, task=task, image_size=(image.width, image.height)
    )


def _task_value(parsed: dict, task: str) -> dict:
    value = parsed.get(task, {})
    return value if isinstance(value, dict) else {}


def _blur_region(box: tuple[float, float, float, float], size: tuple[int, int]) -> list[int]:
    width, height = size
    padding = max(6, round(min(width, height) * 0.012))
    x1 = max(0, round(box[0]) - padding)
    y1 = max(0, round(box[1]) - padding)
    x2 = min(width, round(box[2]) + padding)
    y2 = min(height, round(box[3]) + padding)
    return [x1, y1, max(1, x2 - x1), max(1, y2 - y1)]


def main() -> int:
    performance = ScanPerformance()
    parser = argparse.ArgumentParser(
        description="Add Florence region proposals after Qwen logo confirmation"
    )
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    args = parser.parse_args()

    root = args.project_root.resolve(strict=True)
    report_path = _inside(root / "reports", root / args.report, "Report")
    output_path = _inside(root / "reports", root / args.output, "Output", must_exist=False)
    model_path = _inside(root / "models", root / "models" / "florence_2_base", "Model")
    ffmpeg = _inside(root / "tools", root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe", "FFmpeg")
    ensure_model_allowed(root, model_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("scan_type") != "visual_logo":
        raise ValueError("Input must be a visual-logo scan report")
    video = _inside(root / "input", Path(report["input"]), "Input video")
    memory_records = load_brand_memory(root).get("records", [])
    intervals = [
        interval for interval in report.get("intervals", [])
        if interval.get("visual_logo_confirmation", {}).get("state")
        in {"CONFIRMED", "UNCERTAIN"}
    ]
    if args.limit is not None:
        if args.limit <= 0:
            raise ValueError("limit must be positive")
        intervals = intervals[:args.limit]

    os.environ["HF_HOME"] = str(root / "cache" / "huggingface")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TEMP"] = str(root / "temp")
    os.environ["TMP"] = str(root / "temp")
    sys.path.insert(0, str((root / "runtime" / "florence-python").resolve(strict=True)))
    import transformers
    from transformers import AutoModelForCausalLM, AutoProcessor

    if transformers.__version__ != "4.49.0":
        raise RuntimeError(f"Florence runtime mismatch: {transformers.__version__}")
    if args.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable")
    with performance.measure('model_load'):
        processor = AutoProcessor.from_pretrained(
            model_path, local_files_only=True, trust_remote_code=True
        )
        dtype = torch.float16 if args.device == "cuda" else torch.float32
        model = AutoModelForCausalLM.from_pretrained(
            model_path, torch_dtype=dtype, local_files_only=True, trust_remote_code=True
        ).to(args.device).eval()

    started = time.perf_counter()
    localized = 0
    for index, interval in enumerate(intervals, start=1):
        timestamp = _timestamp(interval)
        image = performance.call('frame_extract', _extract_frame, ffmpeg, video, timestamp)
        confirmation = interval.get("visual_logo_confirmation", {})
        memory_match = confirmation.get("features", {}).get("brand_memory")
        learned_region = approved_brand_memory_region(
            memory_match, (image.width, image.height)
        )
        if learned_region is not None:
            original_region = dict(learned_region)
            learned_region = tighten_brand_foreground_region(
                np.asarray(image), learned_region
            )
            x, y = learned_region["x"], learned_region["y"]
            width, height = learned_region["width"], learned_region["height"]
            interval["region_localization"] = {
                "model": "approved human brand memory",
                "routing": "approved_brand_memory_region",
                "timestamp_seconds": timestamp,
                "confirmed_brand": None,
                "frame_size": [image.width, image.height],
                "proposal_count": 1,
                "proposals": [{
                    "box_xyxy": [x, y, x + width, y + height],
                    "blur_region_px": [x, y, width, height],
                    "sources": ["brand_memory"],
                    "labels": list(memory_match.get("labels") or []),
                    "region_classification": "external_brand",
                    "classification_reason": (
                        "High-confidence approved signature matched at its learned region"
                    ),
                    "suggested_decision": "BLUR",
                    "memory_match": memory_match,
                    "region_refinement": {
                        "method": "foreground_inside_approved_memory",
                        "original_region": original_region,
                        "refined_region": learned_region,
                        "changed": learned_region != original_region,
                    },
                }],
                "requires_human_review": True,
                "automatic_edit": False,
            }
            interval["suggested_decision"] = "BLUR"
            localized += 1
            print(
                f"Brand-memory region {index}/{len(intervals)} at {timestamp:.3f}s: 1",
                flush=True,
            )
            continue
        grounding = _task_value(
            performance.call('model_step', _run_task, model, processor, image, GROUNDING_TASK, GROUNDING_TEXT),
            GROUNDING_TASK,
        )
        ocr = _task_value(performance.call('model_step', _run_task, model, processor, image, OCR_TASK, None), OCR_TASK)
        proposals = consolidate_region_proposals(
            grounding_boxes=grounding.get("bboxes", []),
            grounding_labels=grounding.get("labels", []),
            ocr_quads=ocr.get("quad_boxes", []),
            ocr_labels=ocr.get("labels", []),
            image_size=(image.width, image.height),
        )
        confirmed_brand = _confirmed_brand(interval)
        selected = select_brand_region_proposals(proposals, confirmed_brand)
        focus_routing = "none"
        narrowed = localization_focus_box(
            confirmation.get("features", {}).get("focus_region"),
            (image.width, image.height),
        )
        if narrowed is not None:
            # The router committed to a corner. Keep only boxes centred in the
            # narrow strip a watermark can occupy; a box on a face fails this.
            focused = select_focus_region_proposals(selected, narrowed)
            if focused:
                selected, focus_routing = focused, "narrow_corner_strip"
            else:
                # The router can point at the wrong tile while a real mark sits
                # elsewhere. Fall back on compactness rather than on the whole
                # frame, so oversized frame-level boxes still cannot survive.
                selected = select_compact_overlay_proposals(
                    selected, (image.width, image.height)
                )
                focus_routing = "compact_overlay_fallback"
        else:
            seed = interval.get("region_seed_analysis") or confirmation.get(
                "features", {}
            ).get("focus_box_analysis")
            analysis_size = report.get("analysis_size") or [
                320, max(2, round(image.height * 320 / image.width / 2) * 2),
            ]
            if isinstance(seed, list) and len(seed) == 4 and len(analysis_size) == 2:
                scale_x = image.width / float(analysis_size[0])
                scale_y = image.height / float(analysis_size[1])
                seed_focus = select_focus_region_proposals(selected, [
                    seed[0] * scale_x, seed[1] * scale_y,
                    seed[2] * scale_x, seed[3] * scale_y,
                ])
                if seed_focus:
                    selected = seed_focus
                    focus_routing = "analysis_seed_box"
                else:
                    selected = select_compact_overlay_proposals(
                        selected, (image.width, image.height)
                    )
                    focus_routing = "compact_overlay_fallback"
        selected = prioritize_brand_region_proposals(selected, confirmed_brand)
        proposal_payloads = []
        frame_rgb = np.asarray(image)
        for proposal in selected:
            x1, y1, x2, y2 = proposal.box
            relative_box = [
                x1 / image.width, y1 / image.height,
                max(1.0, x2 - x1) / image.width,
                max(1.0, y2 - y1) / image.height,
            ]
            memory_match = match_region_memory(
                frame_rgb, relative_box, memory_records, minimum_similarity=0.90
            )
            classification = "unknown"
            suggestion = None
            classification_reason = "No approved region signature matched"
            if memory_match and memory_match.get("memory_class") == "non_brand":
                classification = "approved_non_brand"
                suggestion = "KEEP"
                classification_reason = "Matched a region previously approved as valid film content"
            elif memory_match and memory_match.get("memory_class") == "brand":
                classification = "external_brand"
                suggestion = "BLUR"
                classification_reason = "Matched a region previously approved as external branding"
            elif (
                "ocr" in proposal.sources
                and any(looks_like_site_mark(label) for label in proposal.labels)
                and bool(select_compact_overlay_proposals(
                    [proposal], (image.width, image.height)
                ))
            ):
                classification = "external_brand_candidate"
                suggestion = "BLUR"
                classification_reason = "OCR read a compact peripheral site/watermark mark in this region"
            elif (
                confirmed_brand
                and "ocr" in proposal.sources
                and _label_matches_brand(proposal.labels, confirmed_brand)
            ):
                classification = "external_brand_candidate"
                suggestion = "BLUR"
                classification_reason = "OCR text in this exact region matches the semantically confirmed brand"
            proposal_payloads.append({
                "box_xyxy": [round(value, 2) for value in proposal.box],
                "blur_region_px": _blur_region(proposal.box, (image.width, image.height)),
                "sources": list(proposal.sources),
                "labels": list(proposal.labels),
                "region_classification": classification,
                "classification_reason": classification_reason,
                "suggested_decision": suggestion,
                "memory_match": memory_match,
            })
        interval["region_localization"] = {
            "model": "microsoft/Florence-2-base",
            "timestamp_seconds": timestamp,
            "confirmed_brand": confirmed_brand,
            "frame_size": [image.width, image.height],
            "focus_routing": focus_routing,
            "proposal_count": len(selected),
            "proposals": proposal_payloads,
            "requires_human_review": True,
            "automatic_edit": False,
        }
        if (
            interval.get("candidate_type") == "persistent_overlay"
            and any(
                proposal.get("suggested_decision") == "BLUR"
                for proposal in proposal_payloads
            )
        ):
            interval["suggested_decision"] = "BLUR"
        elif interval.get("candidate_type") == "persistent_overlay":
            interval.pop("suggested_decision", None)
        localized += bool(selected)
        print(f"Florence region {index}/{len(intervals)} at {timestamp:.3f}s: {len(selected)}", flush=True)

    report["region_localization_summary"] = {
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "routing": "visual candidate -> Qwen confirmation -> Florence region proposal",
        "retained_intervals_processed": len(intervals),
        "intervals_with_region": localized,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "automatic_edit": False,
        "requires_human_review": True,
    }
    report.setdefault("metrics", {})["localization_performance"] = performance.snapshot()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output_path)
    print(json.dumps(report["region_localization_summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
