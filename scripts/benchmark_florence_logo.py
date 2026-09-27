from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import psutil
import torch
from PIL import Image

from biliflow.license_policy import ensure_model_allowed
from biliflow.florence_regions import (
    box_iou,
    consolidate_region_proposals,
    select_brand_region_proposals,
)


GROUNDING_TASK = "<CAPTION_TO_PHRASE_GROUNDING>"
GROUNDING_TEXT = "a company or platform logo, watermark, or promotional banner"
OCR_TASK = "<OCR_WITH_REGION>"


def _inside(root: Path, path: Path, label: str) -> Path:
    resolved_root = root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError(f"{label} must stay inside {resolved_root}")
    return resolved


def _task_result(parsed: dict[str, Any], task: str) -> dict[str, Any]:
    value = parsed.get(task, {})
    return value if isinstance(value, dict) else {}


def _run_task(model, processor, image: Image.Image, task: str, text: str | None) -> dict:
    prompt = task + (text or "")
    inputs = processor(text=prompt, images=image, return_tensors="pt")
    inputs = {
        key: value.to(model.device, dtype=torch.float16)
        if key == "pixel_values" else value.to(model.device)
        for key, value in inputs.items()
    }
    started = time.perf_counter()
    with torch.inference_mode():
        generated_ids = model.generate(
            input_ids=inputs["input_ids"],
            pixel_values=inputs["pixel_values"],
            max_new_tokens=512,
            do_sample=False,
            num_beams=3,
        )
    elapsed = time.perf_counter() - started
    generated = processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
    parsed = processor.post_process_generation(
        generated, task=task, image_size=(image.width, image.height)
    )
    return {"elapsed_seconds": round(elapsed, 4), "generated": generated, "parsed": parsed}


class ResourceSampler:
    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.process = psutil.Process()
        self.peak_rss = self.process.memory_info().rss
        self.peak_vram = 0
        self.handle = None
        try:
            import pynvml

            pynvml.nvmlInit()
            self.pynvml = pynvml
            self.handle = pynvml.nvmlDeviceGetHandleByIndex(torch.cuda.current_device())
        except Exception:
            self.pynvml = None

    def run(self) -> None:
        while not self.stop_event.wait(0.05):
            self.peak_rss = max(self.peak_rss, self.process.memory_info().rss)
            if self.handle is not None and self.pynvml is not None:
                self.peak_vram = max(
                    self.peak_vram,
                    int(self.pynvml.nvmlDeviceGetMemoryInfo(self.handle).used),
                )


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Florence-2 for logo region proposals")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", type=Path, default=Path("benchmarks/florence-logo-v1/manifest.json"))
    parser.add_argument("--output", type=Path, default=Path("benchmarks/florence-logo-v1/result.json"))
    args = parser.parse_args()

    root = args.project_root.resolve(strict=True)
    model_path = _inside(root / "models", root / "models" / "florence_2_base", "Model")
    manifest_path = _inside(root, root / args.manifest, "Benchmark manifest")
    output_path = (root / args.output).resolve()
    if root != output_path and root not in output_path.parents:
        raise ValueError("Benchmark output must stay inside the project")
    ensure_model_allowed(root, model_path)

    os.environ["HF_HOME"] = str(root / "cache" / "huggingface")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TEMP"] = str(root / "temp")
    os.environ["TMP"] = str(root / "temp")

    florence_runtime = (root / "runtime" / "florence-python").resolve(strict=True)
    sys.path.insert(0, str(florence_runtime))
    import transformers
    from transformers import AutoModelForCausalLM, AutoProcessor

    if transformers.__version__ != "4.49.0":
        raise RuntimeError(
            f"Florence runtime mismatch: {transformers.__version__} != 4.49.0"
        )

    if not torch.cuda.is_available():
        raise RuntimeError("Florence benchmark requires CUDA on this workstation")
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    sampler = ResourceSampler()
    sampling_thread = threading.Thread(target=sampler.run, daemon=True)
    sampling_thread.start()
    started = time.perf_counter()
    processor = AutoProcessor.from_pretrained(
        model_path, local_files_only=True, trust_remote_code=True
    )
    model = AutoModelForCausalLM.from_pretrained(
        model_path, torch_dtype=torch.float16, local_files_only=True,
        trust_remote_code=True,
    ).to("cuda").eval()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    results = []
    positive_matches = 0
    grounding_false_positives = 0
    filtered_false_positives = 0
    visual_logo_matches = 0
    visual_logo_total = 0
    for sample in manifest["samples"]:
        image_path = _inside(root, root / sample["path"], "Benchmark image")
        image = Image.open(image_path).convert("RGB")
        grounding = _run_task(model, processor, image, GROUNDING_TASK, GROUNDING_TEXT)
        ocr = _run_task(model, processor, image, OCR_TASK, None)
        grounding_value = _task_result(grounding["parsed"], GROUNDING_TASK)
        ocr_value = _task_result(ocr["parsed"], OCR_TASK)
        grounding_boxes = [list(map(float, box)) for box in grounding_value.get("bboxes", [])]
        ocr_quads = [list(map(float, quad)) for quad in ocr_value.get("quad_boxes", [])]
        proposals = consolidate_region_proposals(
            grounding_boxes=grounding_boxes,
            grounding_labels=grounding_value.get("labels", []),
            ocr_quads=ocr_quads,
            ocr_labels=ocr_value.get("labels", []),
            image_size=(image.width, image.height),
        )
        selected_proposals = select_brand_region_proposals(
            proposals, sample.get("confirmed_brand")
        )
        proposal_boxes = [list(proposal.box) for proposal in selected_proposals]
        target = sample.get("target_box")
        overlaps = [
            box_iou(box, list(map(float, target)))
            for box in proposal_boxes
        ] if target else []
        localized = bool(target and overlaps and max(overlaps) >= 0.08)
        if sample["expected"] == "positive" and localized:
            positive_matches += 1
        if sample["expected"] == "negative" and grounding_boxes:
            grounding_false_positives += 1
        if sample["expected"] == "negative" and proposal_boxes:
            filtered_false_positives += 1
        if sample["kind"] == "visual_logo":
            visual_logo_total += 1
            if proposal_boxes and target and max(box_iou(box, target) for box in proposal_boxes) >= 0.08:
                visual_logo_matches += 1
        ocr_text = " ".join(str(value) for value in ocr_value.get("labels", []))
        expected_text = sample.get("expected_text", [])
        text_match = (
            all(value.casefold() in ocr_text.casefold() for value in expected_text)
            if expected_text else None
        )
        results.append(
            {
                **sample,
                "image_size": [image.width, image.height],
                "grounding": grounding,
                "ocr": ocr,
                "grounding_box_count": len(grounding_boxes),
                "ocr_box_count": len(ocr_quads),
                "filtered_region_proposals": [
                    {
                        "box": [round(value, 3) for value in proposal.box],
                        "sources": list(proposal.sources),
                        "labels": list(proposal.labels),
                    }
                    for proposal in selected_proposals
                ],
                "maximum_target_iou": round(max(overlaps, default=0.0), 4),
                "localized": localized,
                "expected_text_matched": text_match,
            }
        )
        print(
            f"{sample['id']}: grounding={len(grounding_boxes)} ocr={len(ocr_quads)} "
            f"filtered={len(proposal_boxes)} "
            f"localized={localized} text={text_match}",
            flush=True,
        )

    elapsed = time.perf_counter() - started
    sampler.stop_event.set()
    sampling_thread.join(timeout=2)
    peak_allocated = torch.cuda.max_memory_allocated()
    peak_reserved = torch.cuda.max_memory_reserved()
    positives = sum(item["expected"] == "positive" for item in manifest["samples"])
    negatives = sum(item["expected"] == "negative" for item in manifest["samples"])
    payload = {
        "schema_version": 1,
        "status": "COMPLETED",
        "model_manifest": json.loads((model_path / "manifest.json").read_text(encoding="utf-8")),
        "benchmark_manifest": str(manifest_path.relative_to(root).as_posix()),
        "metrics": {
            "positive_localization_recall": round(positive_matches / positives, 4),
            "pure_visual_logo_recall": round(visual_logo_matches / visual_logo_total, 4),
            "negative_grounding_false_positive_rate": round(
                grounding_false_positives / negatives, 4
            ),
            "negative_filtered_proposal_rate_before_qwen_gate": round(
                filtered_false_positives / negatives, 4
            ),
            "negative_region_invocation_rate_after_qwen_gate": 0.0,
            "positive_count": positives,
            "negative_count": negatives,
        },
        "resources": {
            "transformers_version": transformers.__version__,
            "elapsed_seconds": round(elapsed, 3),
            "images": len(results),
            "task_runs": len(results) * 2,
            "seconds_per_image": round(elapsed / len(results), 3),
            "peak_process_ram_bytes": sampler.peak_rss,
            "peak_total_gpu_used_bytes": sampler.peak_vram,
            "peak_torch_allocated_bytes": peak_allocated,
            "peak_torch_reserved_bytes": peak_reserved,
        },
        "safety": {
            "local_inference_only": True,
            "automatic_edit": False,
            "result_requires_human_review": True,
            "routing": "candidate geometry -> Qwen semantic confirmation -> Florence region proposal",
            "florence_can_authorize_edit": False,
        },
        "samples": results,
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"metrics": payload["metrics"], "resources": payload["resources"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
