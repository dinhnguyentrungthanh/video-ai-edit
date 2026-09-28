"""Short equivalence check for scan instrumentation against a local Git revision.

Run via the project environment. This writes isolated reports, never a job queue.
Timing is diagnostic only: sequential runs have different warm-up/cache costs.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib
import json
import subprocess
import sys
import tempfile
import types
from datetime import datetime
from pathlib import Path

from biliflow.license_policy import ensure_model_allowed


def normalize(value):
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()
                if key not in {"metrics", "created_at", "runtime"}}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    return value


def images(directory):
    return {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*.jpg")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--baseline-ref", default="main")
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--seconds", type=float, default=12)
    args = parser.parse_args()
    if not 0 < args.seconds <= 30 or args.start < 0:
        parser.error("Use a nonnegative start and a clip of at most 30 seconds")
    root = Path(__file__).resolve().parents[1]
    source = args.input.resolve(strict=True)
    if not source.is_relative_to(root / "input"):
        parser.error("Input must be in the project input directory")
    revision = subprocess.check_output([
        "git", "rev-parse", "--verify", "--end-of-options", args.baseline_ref + "^{commit}",
    ], cwd=root, text=True).strip()
    models = root / "models"
    for name in ("easyocr", "multilingual_minilm_text_semantics", "nsfw_detection_2_nano",
                 "image_safety_classifier_m", "vit_base_violence_detection"):
        ensure_model_allowed(root, models / name)
    destination = root / "reports" / "benchmarks" / ("scan-timing-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    destination.mkdir(parents=True, exist_ok=False)
    ffmpeg = root / "tools/ffmpeg/bin/ffmpeg.exe"
    ffprobe = root / "tools/ffmpeg/bin/ffprobe.exe"
    results = {"baseline_commit": revision, "source": str(source),
               "clip_start": args.start, "clip_seconds": args.seconds, "comparisons": {}}
    with tempfile.TemporaryDirectory(prefix="scan-timing-", dir=root / "temp") as directory:
        clip = Path(directory) / "sample.mp4"
        subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-ss", str(args.start),
                        "-i", str(source), "-t", str(args.seconds), "-an", "-c:v", "libx264",
                        "-preset", "veryfast", "-crf", "18", str(clip)], check=True)
        common = dict(project_root=root, input_path=clip, ffmpeg_path=ffmpeg, ffprobe_path=ffprobe)
        specs = [
            ("textscan", "scan_text", dict(model_dir=models / "easyocr",
                 semantic_model_dir=models / "multilingual_minilm_text_semantics",
                 semantic_seed_path=root / "annotations/text_semantics_seed_v1.json",
                 policy_path=root / "config/text_review_policy.json")),
            ("scanner", "scan_nsfw", dict(model_path=models / "nsfw_detection_2_nano",
                 sample_fps=2, batch_size=8, top_k_candidates=20, threshold=0.95,
                 merge_gap_seconds=2, padding_seconds=1, device_name="cuda", content_style="live_action")),
            ("live_safety_scanner", "scan_live_safety", dict(
                 gore_model_path=models / "image_safety_classifier_m",
                 violence_model_path=models / "vit_base_violence_detection")),
        ]
        for module_name, function_name, kwargs in specs:
            code = subprocess.check_output(["git", "show", f"{revision}:src/biliflow/{module_name}.py"], cwd=root).decode("utf-8")
            old = types.ModuleType("timing_baseline_" + module_name)
            sys.modules[old.__name__] = old
            exec(compile(code, f"{revision}:{module_name}", "exec"), old.__dict__)
            current = importlib.import_module("biliflow." + module_name)
            payloads = []
            for label, module in (("baseline", old), ("instrumented", current)):
                print(f"Running {module_name}: {label}", flush=True)
                output = destination / module_name / label
                payloads.append(getattr(module, function_name)(**common, **kwargs, report_dir=output))
                gc.collect()
                import torch
                torch.cuda.empty_cache()
            same_payload = normalize(payloads[0]) == normalize(payloads[1])
            before_images = images(destination / module_name / "baseline")
            after_images = images(destination / module_name / "instrumented")
            results["comparisons"][module_name] = {
                "detection_payload_equal": same_payload,
                "jpeg_hashes_equal": before_images == after_images,
                "jpeg_count": len(after_images),
            }
            (destination / "comparison.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
            if not same_payload or before_images != after_images:
                raise RuntimeError(f"Equivalence failed for {module_name}; see {destination}")
    print(str(destination / "comparison.json"), flush=True)


if __name__ == "__main__":
    main()
