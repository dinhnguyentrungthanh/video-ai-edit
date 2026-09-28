"""Bounded, isolated OCR A/B on losslessly preserved contiguous sampling windows."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from time import perf_counter

from biliflow.license_policy import ensure_model_allowed
from biliflow.probe import probe_video, duration_seconds
from biliflow.review_workflow import build_review_queue
from biliflow.text_semantics import LocalEmbeddingTextClassifier
from biliflow.textscan import scan_text


def normalize(value):
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items()
                if k not in {"metrics", "created_at", "runtime", "confidence", "max_confidence"}}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    return value


def image_hashes(directory):
    return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob("*.jpg")}


def queue_projection(queue):
    fields = ("category", "review_kind", "candidate_type", "start_seconds", "end_seconds",
              "suggested_region_source_pixels", "labels", "priority", "suggested_decision",
              "detected_intervals", "decision", "decision_region_source_pixels",
              "source_frame_size", "suggested_blur_edge_mode", "reasons")
    return {group: [{**{k: item.get(k) for k in fields},
                    "refs": [ref.split("#", 1)[-1] for ref in item.get("source_candidate_refs", [])]}
                   for item in queue.get(group, [])] for group in ("items", "advisory_items")}


def md5_frames(ffmpeg, input_args, vf):
    output = subprocess.check_output([str(ffmpeg), "-hide_banner", "-loglevel", "error",
        *input_args, "-vf", vf, "-an", "-sn", "-pix_fmt", "rgb24", "-f", "framemd5", "pipe:1"], text=True)
    return [line.split(",")[-1].strip() for line in output.splitlines() if line and not line.startswith("#")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    segments = plan["segments"]
    if not 1 <= len(segments) <= 6:
        raise ValueError("Plan must contain 1..6 excerpts")
    for segment in segments:
        source = Path(segment["source"]).resolve(strict=True)
        if not source.is_relative_to(root / "input"):
            raise ValueError("Sources must be in project input")
        start, seconds = float(segment["start"]), float(segment["seconds"])
        if start < 0 or not 3 <= seconds <= 120 or start % 3 or seconds % 3:
            raise ValueError("Use aligned nonnegative starts and 3..120 seconds, multiples of 3")
    for name in ("easyocr", "multilingual_minilm_text_semantics"):
        ensure_model_allowed(root, root / "models" / name)
    import easyocr
    import torch
    reader = easyocr.Reader(["vi", "en"], gpu=True,
        model_storage_directory=str(root / "models/easyocr"),
        user_network_directory=str(root / "models/easyocr"), download_enabled=False)
    classifier = LocalEmbeddingTextClassifier(root / "models/multilingual_minilm_text_semantics",
        root / "annotations/text_semantics_seed_v1.json", "cuda")
    ffmpeg, ffprobe = (root / "tools/ffmpeg/bin" / name for name in ("ffmpeg.exe", "ffprobe.exe"))
    output = root / "reports/benchmarks" / ("ocr-contiguous-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True, exist_ok=False)
    evidence = {"scope": "OCR only; isolated queues; no production edits or all-detector certification",
                "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
                "gpu": torch.cuda.get_device_name(), "sampling_seconds": 3, "segments": []}
    for index, segment in enumerate(segments):
        source = Path(segment["source"]).resolve(strict=True)
        source_stat = source.stat()
        probe = probe_video(ffprobe, source)
        video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
        height = max(2, round(int(video["height"]) * 960 / int(video["width"]) / 2) * 2)
        start, seconds = float(segment["start"]), float(segment["seconds"])
        if start + seconds > duration_seconds(probe):
            raise ValueError("Excerpt exceeds source duration")
        base = output / f"segment-{index}"
        base.mkdir()
        vf = f"fps=1/3,scale=960:{height}:flags=bilinear,format=rgb24"
        source_args = ["-ss", str(start), "-i", str(source), "-t", str(seconds)]
        original_hashes = md5_frames(ffmpeg, source_args, vf)
        with tempfile.TemporaryDirectory(prefix="ocr-contiguous-", dir=root / "temp") as temporary:
            clip = Path(temporary) / "sampled.mkv"
            subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", *source_args,
                "-vf", vf + ",format=bgr0", "-an", "-sn", "-c:v", "ffv1", str(clip)], check=True)
            decoded_hashes = md5_frames(ffmpeg, ["-i", str(clip)], vf)
            if len(original_hashes) != int(seconds / 3) or original_hashes != decoded_hashes:
                raise RuntimeError("Lossless sampling fixture changed/dropped source pixels")
            (base / "frame-hashes.json").write_text(json.dumps({"source": original_hashes,
                "fixture": decoded_hashes}, indent=2), encoding="utf-8")
            common = dict(project_root=root, input_path=clip, model_dir=root / "models/easyocr",
                ffmpeg_path=ffmpeg, ffprobe_path=ffprobe, reader=reader,
                semantic_classifier=classifier, policy_path=root / "config/text_review_policy.json")
            # Warmup is not used for time comparisons.
            reference = scan_text(**common, report_dir=base / "warmup", recognition_batch_size=1)
            reference_queue = build_review_queue(project_root=root,
                report_paths=[base / "warmup/text-scan.json"], queue_path=base / "warmup/review-queue.json",
                selected_detectors=["advertising"])
            rows = []
            for run, batch in enumerate((1, 8, 8, 1)):
                target = base / f"run-{run}-batch-{batch}"
                torch.cuda.synchronize()
                torch.cuda.reset_peak_memory_stats()
                began = perf_counter()
                report = scan_text(**common, report_dir=target, recognition_batch_size=batch)
                torch.cuda.synchronize()
                elapsed = perf_counter() - began
                peak_gpu = torch.cuda.max_memory_allocated()
                peak_reserved = torch.cuda.max_memory_reserved()
                queue = build_review_queue(project_root=root, report_paths=[target / "text-scan.json"],
                    queue_path=target / "review-queue.json", selected_detectors=["advertising"])
                row = {"batch": batch, "wall_seconds": elapsed,
                    "ocr_seconds": report["metrics"]["performance"]["phases"]["model_step"]["wall_seconds"],
                    "peak_cuda_allocated": peak_gpu, "peak_cuda_reserved": peak_reserved,
                    "peak_process_ram": report["metrics"]["peak_process_ram_bytes"],
                    "frames": report["frames_scanned"], "tracks": len(report["tracks"]),
                    "report_equal_except_confidences": normalize(reference) == normalize(report),
                    "preview_hashes_equal": image_hashes(base / "warmup") == image_hashes(target),
                    "review_projection_equal": queue_projection(reference_queue) == queue_projection(queue),
                    "candidate_coverage": queue["candidate_coverage"],
                }
                row["all_checks_passed"] = (
                    row["frames"] == len(original_hashes)
                    and row["report_equal_except_confidences"]
                    and row["preview_hashes_equal"] and row["review_projection_equal"]
                    and row["candidate_coverage"]["complete"]
                )
                rows.append(row)
                print(f"Excerpt {index} batch {batch}: {elapsed:.3f}s report={row['report_equal_except_confidences']} previews={row['preview_hashes_equal']} queue={row['review_projection_equal']}", flush=True)
            result = {**segment, "source_size_bytes": source_stat.st_size,
                "source_mtime_ns": source_stat.st_mtime_ns, "frames_verified": len(original_hashes), "runs": rows,
                "medians": {str(batch): {field: statistics.median(r[field] for r in rows if r["batch"] == batch)
                    for field in ("wall_seconds", "ocr_seconds")} for batch in (1, 8)}}
            evidence["segments"].append(result)
            (output / "comparison.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        final_stat = source.stat()
        if (source_stat.st_size, source_stat.st_mtime_ns) != (final_stat.st_size, final_stat.st_mtime_ns):
            raise RuntimeError("Source changed during benchmark")
    print(output, flush=True)
    if not all(row["all_checks_passed"] for segment in evidence["segments"] for row in segment["runs"]):
        raise RuntimeError("OCR equivalence gate failed; inspect preserved comparison evidence")


if __name__ == "__main__":
    main()
