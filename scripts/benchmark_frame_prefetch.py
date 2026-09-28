"""Short OCR A/B for bounded prefetch, with warm models and reversed run order."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
import types
from datetime import datetime
from pathlib import Path
from time import perf_counter

from biliflow.license_policy import ensure_model_allowed
from biliflow.text_semantics import LocalEmbeddingTextClassifier
from biliflow.textscan import scan_text


def normalized(value):
    if isinstance(value, dict):
        return {k: normalized(v) for k, v in value.items() if k not in {"metrics", "created_at", "runtime"}}
    if isinstance(value, list):
        return [normalized(v) for v in value]
    return value


def images(directory):
    return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob("*.jpg")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--start", type=float, nargs="+", default=[0, 418])
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--baseline-ref", default="8a2caf3")
    args = parser.parse_args()
    if not 0 < args.seconds <= 30 or any(t < 0 for t in args.start) or len(args.start) > 3:
        parser.error("Use up to three nonnegative starts and excerpts of at most 30 seconds")
    root = Path(__file__).resolve().parents[1]
    source = args.input.resolve(strict=True)
    if not source.is_relative_to(root / "input"):
        parser.error("Source must be in project input")
    for name in ("easyocr", "multilingual_minilm_text_semantics"):
        ensure_model_allowed(root, root / "models" / name)
    revision = subprocess.check_output(["git", "rev-parse", "--verify", "--end-of-options",
                                        args.baseline_ref + "^{commit}"], cwd=root, text=True).strip()
    code = subprocess.check_output(["git", "show", f"{revision}:src/biliflow/textscan.py"], cwd=root)
    old = types.ModuleType("prefetch_baseline_textscan")
    sys.modules[old.__name__] = old
    exec(compile(code, "baseline_textscan.py", "exec"), old.__dict__)
    import easyocr
    reader = easyocr.Reader(["vi", "en"], gpu=True,
                            model_storage_directory=str(root / "models/easyocr"),
                            user_network_directory=str(root / "models/easyocr"), download_enabled=False)
    classifier = LocalEmbeddingTextClassifier(root / "models/multilingual_minilm_text_semantics",
                                               root / "annotations/text_semantics_seed_v1.json", "cuda")
    output = root / "reports/benchmarks" / ("frame-prefetch-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True, exist_ok=False)
    common = dict(project_root=root, input_path=source, model_dir=root / "models/easyocr",
                  ffmpeg_path=root / "tools/ffmpeg/bin/ffmpeg.exe",
                  ffprobe_path=root / "tools/ffmpeg/bin/ffprobe.exe",
                  policy_path=root / "config/text_review_policy.json",
                  semantic_classifier=classifier, reader=reader)
    # Keep warmup report as evidence; exclude it from timings and comparisons.
    old.scan_text(**common, report_dir=output / "warmup", duration_seconds_limit=3)
    evidence = {"baseline_commit": revision, "source": str(source), "scope": "OCR only; warm models",
                "seconds_per_excerpt": args.seconds, "runs": [], "summaries": []}
    for start in args.start:
        reference = None
        reference_images = None
        group = []
        # Baseline-old, prefetch-new, prefetch-new, serial-new, baseline-old.
        for index, mode in enumerate(("baseline", "prefetch", "prefetch", "serial", "baseline")):
            destination = output / f"{start:g}" / f"{index}-{mode}"
            print(f"OCR {start:g}s / {mode}", flush=True)
            kwargs = dict(common, report_dir=destination, start_seconds=start, duration_seconds_limit=args.seconds)
            began = perf_counter()
            report = old.scan_text(**kwargs) if mode == "baseline" else scan_text(
                **kwargs, prefetch_frames=2 if mode == "prefetch" else 0)
            elapsed = perf_counter() - began
            if reference is None:
                reference, reference_images = normalized(report), images(destination)
            row = {"start": start, "mode": mode, "wall_seconds": elapsed,
                   "frames": report["frames_scanned"],
                   "payload_equal": normalized(report) == reference,
                   "jpeg_hashes_equal": images(destination) == reference_images,
                   "jpeg_count": len(reference_images), "performance": report["metrics"]["performance"]}
            group.append(row)
            evidence["runs"].append(row)
            (output / "comparison.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
            if not row["payload_equal"] or not row["jpeg_hashes_equal"]:
                raise RuntimeError(f"OCR equivalence failed; evidence: {output}")
        # Scan elapsed excludes hashing/report serialization; whole-call includes both.
        def median(mode, field):
            return statistics.median(field(r) for r in group if r["mode"] == mode)
        evidence["summaries"].append({
            "start": start,
            "baseline_wall_median": median("baseline", lambda r: r["wall_seconds"]),
            "prefetch_wall_median": median("prefetch", lambda r: r["wall_seconds"]),
            "baseline_wall_without_hash_median": median("baseline", lambda r: r["wall_seconds"] - r["performance"]["phases"]["source_hash"]["wall_seconds"]),
            "prefetch_wall_without_hash_median": median("prefetch", lambda r: r["wall_seconds"] - r["performance"]["phases"]["source_hash"]["wall_seconds"]),
            "baseline_pipe_wait_median": median("baseline", lambda r: r["performance"]["phases"]["frame_pipe_wait"]["wall_seconds"]),
            "prefetch_pipe_wait_median": median("prefetch", lambda r: r["performance"]["phases"]["frame_pipe_wait"]["wall_seconds"]),
        })
        (output / "comparison.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence["summaries"], indent=2))
    print(output)


if __name__ == "__main__":
    main()
