from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import psutil

from biliflow.license_policy import ensure_model_allowed


PROMPT = (
    "Classify this single movie frame. EXTERNAL means branding added outside the story: "
    "a platform, distributor, studio or company logo/ident; watermark or ad overlay; "
    "standalone sponsor banner; branded company credit or end card. SCENE means normal "
    "movie content: dialogue subtitle, movie/episode/scene title, person credit, or a "
    "sign, billboard, package, screen or branded object physically photographed inside "
    "the story. Reply with exactly EXTERNAL, SCENE, or UNCERTAIN."
)


def _inside(root: Path, path: Path, label: str) -> Path:
    resolved_root = root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError(f"{label} must stay inside {resolved_root}")
    return resolved


def _classify(answer: str) -> str:
    folded = answer.strip().casefold()
    if folded.startswith("external"):
        return "EXTERNAL"
    if folded.startswith("scene"):
        return "SCENE"
    return "UNCERTAIN"


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Phi-3.5 Vision INT4 logo semantics")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", type=Path, default=Path("benchmarks/phi35-logo-v1/manifest.json"))
    parser.add_argument("--output", type=Path, default=Path("benchmarks/phi35-logo-v1/result.json"))
    args = parser.parse_args()

    root = args.project_root.resolve(strict=True)
    model_root = _inside(root / "models", root / "models" / "phi_3_5_vision_int4", "Model")
    model_path = _inside(
        model_root, model_root / "gpu" / "gpu-int4-rtn-block-32", "Model runtime"
    )
    manifest_path = _inside(root, root / args.manifest, "Benchmark manifest")
    output_path = (root / args.output).resolve()
    if output_path != root and root not in output_path.parents:
        raise ValueError("Output must stay inside the project")
    ensure_model_allowed(root, model_root)

    runtime = _inside(root / "runtime", root / "runtime" / "phi35-onnx", "Runtime")
    sys.path.insert(0, str(runtime))
    dll_handles = []
    if hasattr(os, "add_dll_directory"):
        dll_handles.append(os.add_dll_directory(str(runtime / "onnxruntime" / "capi")))
        torch_lib = root / ".venv" / "Lib" / "site-packages" / "torch" / "lib"
        if torch_lib.is_dir():
            os.environ["PATH"] = str(torch_lib) + os.pathsep + os.environ.get("PATH", "")
            dll_handles.append(os.add_dll_directory(str(torch_lib)))
    import onnxruntime_genai as og

    config = og.Config(str(model_path))
    config.clear_providers()
    config.append_provider("cuda")
    started = time.perf_counter()
    model = og.Model(config)
    processor = model.create_multimodal_processor()
    stream = processor.create_stream()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    process = psutil.Process()
    peak_rss = process.memory_info().rss
    results = []
    for sample in manifest["samples"]:
        image_path = _inside(root, root / sample["path"], "Benchmark image")
        images = og.Images.open(str(image_path))
        prompt = f"<|user|>\n<|image_1|>\n{PROMPT}<|end|>\n<|assistant|>\n"
        inputs = processor(prompt, images=images)
        params = og.GeneratorParams(model)
        params.set_search_options(max_length=3072, do_sample=False)
        generator = og.Generator(model, params)
        generator.set_inputs(inputs)
        pieces = []
        sample_started = time.perf_counter()
        while not generator.is_done():
            generator.generate_next_token()
            if generator.is_done():
                break
            pieces.append(stream.decode(generator.get_next_tokens()[0]))
        answer = "".join(pieces).strip()
        predicted = _classify(answer)
        results.append({
            **sample,
            "predicted": predicted,
            "answer": answer,
            "correct": predicted == sample["expected"],
            "elapsed_seconds": round(time.perf_counter() - sample_started, 3),
        })
        peak_rss = max(peak_rss, process.memory_info().rss)
        print(f"{sample['id']}: {sample['expected']} -> {predicted} ({answer})", flush=True)

    positives = [item for item in results if item["expected"] == "EXTERNAL"]
    negatives = [item for item in results if item["expected"] == "SCENE"]
    true_positive = sum(item["predicted"] == "EXTERNAL" for item in positives)
    true_negative = sum(item["predicted"] == "SCENE" for item in negatives)
    elapsed = time.perf_counter() - started
    payload = {
        "schema_version": 1,
        "status": "COMPLETED",
        "model": json.loads((model_root / "manifest.json").read_text(encoding="utf-8")),
        "prompt": PROMPT,
        "metrics": {
            "accuracy": round(sum(item["correct"] for item in results) / len(results), 4),
            "external_recall": round(true_positive / len(positives), 4),
            "scene_specificity": round(true_negative / len(negatives), 4),
            "balanced_accuracy": round(
                ((true_positive / len(positives)) + (true_negative / len(negatives))) / 2, 4
            ),
            "sample_count": len(results),
        },
        "resources": {
            "onnxruntime_genai_version": og.__version__,
            "elapsed_seconds": round(elapsed, 3),
            "seconds_per_image": round(elapsed / len(results), 3),
            "peak_process_ram_bytes": peak_rss,
        },
        "safety": {
            "local_inference_only": True,
            "automatic_edit": False,
            "result_requires_human_review": True,
        },
        "samples": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"metrics": payload["metrics"], "resources": payload["resources"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
