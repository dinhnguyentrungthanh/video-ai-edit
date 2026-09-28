"""Local OCR crop-batching experiment on fixed, lossless video frame fixtures.

Does not change production scanners, queues, source media or model parameters.
Measures recognition separately from fixed detector proposals. Scores and text
are compared to serial baseline, including the scanner's acceptance decisions.
"""
from __future__ import annotations

import argparse
import json
import hashlib
import importlib.metadata
import statistics
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from time import perf_counter

from biliflow.license_policy import ensure_model_allowed
from biliflow.ocr_batch_experiment import recognize_same_width
from biliflow.textscan import _accept_detection, _box_from_points, _clean_text


def plain(value):
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "item"):
        return value.item()
    return value


def accepted(rows, shape):
    result = []
    for points, text, confidence in rows:
        box = _box_from_points(points)
        if box[2] - box[0] < 8 or box[3] - box[1] < 6:
            continue
        text = _clean_text(str(text))
        if _accept_detection(confidence=float(confidence), text=text, box=box,
                             width=shape[1], height=shape[0], minimum_confidence=0.35):
            result.append((box, text))
    return result


def compare(before, after, shape):
    # Standard EasyOCR batching also reorders free/horizontal boxes. Report
    # order changes separately; match detections by their exact coordinates.
    before, after = plain(before), plain(after)
    key = lambda row: json.dumps(row[0])
    left, right = sorted(before, key=key), sorted(after, key=key)
    same_boxes = [r[0] for r in left] == [r[0] for r in right]
    changes = []
    if same_boxes:
        for a, b in zip(left, right, strict=True):
            if a[1] != b[1]:
                changes.append({"box": a[0], "before": a[1:], "after": b[1:]})
    return {"exact_equal": before == after, "boxes_equal": same_boxes,
            "text_changes": changes,
            "max_score_delta": max((abs(a[2] - b[2]) for a, b in zip(left, right)), default=0) if same_boxes else None,
            "accepted_boxes_text_equal": sorted(accepted(before, shape)) == sorted(accepted(after, shape))}


def review_fixture_benchmark(root, fixtures, reader):
    """Exercise tracking/semantics/previews without repeatedly hashing full films."""
    from biliflow.textscan import scan_text
    from biliflow.text_semantics import LocalEmbeddingTextClassifier
    from PIL import Image
    import numpy as np
    fixture_root = fixtures.resolve(strict=True)
    if not fixture_root.is_relative_to(root / "reports/benchmarks"):
        raise ValueError("Fixtures must be local benchmark evidence")
    manifest = json.loads((fixture_root / "comparison.json").read_text(encoding="utf-8"))
    frames = []
    for item in manifest["frames"]:
        frame = (fixture_root / item["frame"]).resolve(strict=True)
        if not frame.is_relative_to(fixture_root):
            raise ValueError("Frame outside evidence directory")
        frames.append(frame)
    if not 1 <= len(frames) <= 24:
        raise ValueError("Use 1..24 frame fixtures")
    model = root / "models/multilingual_minilm_text_semantics"
    ensure_model_allowed(root, model)
    classifier = LocalEmbeddingTextClassifier(model, root / "annotations/text_semantics_seed_v1.json", "cuda")
    output = fixture_root / ("review-" + datetime.now().strftime("%H%M%S"))
    output.mkdir()
    def normalize(value, omit_confidence=False):
        if isinstance(value, dict):
            return {k: normalize(v, omit_confidence) for k, v in value.items()
                    if k not in {"metrics", "created_at", "runtime"}
                    and not (omit_confidence and k in {"confidence", "max_confidence"})}
        if isinstance(value, list):
            return [normalize(v, omit_confidence) for v in value]
        return value
    def jpeg_hashes(directory):
        return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in directory.rglob("*.jpg")}
    # Lossless short fixture sequence, not a transcode of a production video.
    with tempfile.TemporaryDirectory(prefix="ocr-batch-review-", dir=root / "temp") as temporary:
        clip = Path(temporary) / "fixtures.mkv"
        with Image.open(frames[0]) as image:
            width, height = image.size
        chunks = []
        for frame in frames:
            with Image.open(frame) as image:
                if image.size != (width, height):
                    raise ValueError("Fixture dimensions must match")
                chunks.append(np.array(image.convert("RGB")).tobytes())
        subprocess.run([str(root / "tools/ffmpeg/bin/ffmpeg.exe"), "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", f"{width}x{height}",
            "-framerate", "1", "-i", "pipe:0", "-an", "-c:v", "ffv1", str(clip)],
            input=b"".join(chunks), check=True)
        common = dict(project_root=root, input_path=clip, model_dir=root / "models/easyocr",
            ffmpeg_path=root / "tools/ffmpeg/bin/ffmpeg.exe", ffprobe_path=root / "tools/ffmpeg/bin/ffprobe.exe",
            sample_every=1, semantic_classifier=classifier, policy_path=root / "config/text_review_policy.json")
        reference = scan_text(**common, report_dir=output / "warmup", reader=reader)
        runs = []
        for index, mode in enumerate(("serial", "same_width8", "same_width8", "serial")):
            destination = output / f"{index}-{mode}"
            started = perf_counter()
            report = scan_text(**common, report_dir=destination,
                               reader=reader, recognition_batch_size=1 if mode == "serial" else 8)
            wall = perf_counter() - started
            runs.append({
                "mode": mode, "wall_seconds": wall,
                "ocr_seconds": report["metrics"]["performance"]["phases"]["model_step"]["wall_seconds"],
                "exact_report_equal": normalize(reference) == normalize(report),
                "report_equal_except_confidences": normalize(reference, True) == normalize(report, True),
                "preview_hashes_equal": jpeg_hashes(output / "warmup") == jpeg_hashes(destination),
                "tracks": len(report["tracks"]),
            })
        result = {
            "scope": "OCR tracking/semantics on a lossless fixture sequence; not production queue or audit",
            "frames": len(frames),
            "runs": runs,
            "summary": {mode: {field: statistics.median(r[field] for r in runs if r["mode"] == mode)
                               for field in ("wall_seconds", "ocr_seconds")}
                        for mode in ("serial", "same_width8")},
        }
        (output / "comparison.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", default=[])
    parser.add_argument("--review-fixtures", type=Path)
    parser.add_argument("--timestamps", type=float, nargs="+", default=[6, 69, 420, 1550, 4200])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sources = [p.resolve(strict=True) for p in args.input]
    if bool(sources) == bool(args.review_fixtures):
        parser.error("Provide input videos OR --review-fixtures")
    if len(sources) > 3 or len(args.timestamps) > 8 or any(t < 0 for t in args.timestamps):
        parser.error("At most 3 videos x 8 nonnegative timestamps")
    if any(not p.is_relative_to(root / "input") for p in sources):
        parser.error("Sources must be in project input")
    model = root / "models/easyocr"
    ensure_model_allowed(root, model)
    import easyocr
    import numpy as np
    import torch
    from PIL import Image
    from easyocr.utils import reformat_input
    reader = easyocr.Reader(["vi", "en"], gpu=True, model_storage_directory=str(model),
                            user_network_directory=str(model), download_enabled=False)
    if args.review_fixtures:
        review_fixture_benchmark(root, args.review_fixtures, reader)
        return
    output = root / "reports/benchmarks" / ("ocr-batch-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True, exist_ok=False)
    evidence = {"scope": "fixed detector boxes; Latin vi/en recognition only",
                "environment": {"easyocr": importlib.metadata.version("easyocr"),
                                "torch": torch.__version__, "gpu": torch.cuda.get_device_name(),
                                "experiment_sha256": hashlib.sha256((root / "src/biliflow/ocr_batch_experiment.py").read_bytes()).hexdigest()},
                "frames": [], "summary": {}}
    for source_index, source in enumerate(sources):
        for timestamp in args.timestamps:
            frame = output / f"source-{source_index}-{timestamp:g}.png"
            subprocess.run([str(root / "tools/ffmpeg/bin/ffmpeg.exe"), "-hide_banner", "-loglevel", "error",
                "-ss", str(timestamp), "-i", str(source), "-vf", "scale=960:-2:flags=bilinear",
                "-frames:v", "1", "-threads", "1", str(frame)], check=True)
            if not frame.exists():
                raise RuntimeError(f"No frame at {source}:{timestamp}")
            with Image.open(frame) as image:
                pixels = np.array(image.convert("RGB"))
            img, grey = reformat_input(pixels)
            horizontal, free = reader.detect(img, reformat=False)
            horizontal, free = horizontal[0], free[0]
            def infer(mode):
                if mode == "same_width8":
                    return recognize_same_width(reader, grey, horizontal, free, batch_size=8)
                return reader.recognize(grey, horizontal, free, decoder="greedy",
                    batch_size=1 if mode == "serial" else 8, workers=0, detail=1, paragraph=False, reformat=False)
            reference = infer("serial")
            result = {"source": str(source), "timestamp": timestamp, "frame": frame.name,
                      "boxes": len(reference), "reference": plain(reference), "runs": []}
            for mode in ("serial", "standard8", "same_width8", "same_width8", "standard8", "serial"):
                torch.cuda.synchronize()
                torch.cuda.reset_peak_memory_stats()
                start = perf_counter()
                prediction = infer(mode)
                torch.cuda.synchronize()
                elapsed = perf_counter() - start
                result["runs"].append({"mode": mode, "seconds": elapsed,
                    "peak_cuda_allocated_bytes": torch.cuda.max_memory_allocated(),
                    "comparison": compare(reference, prediction, grey.shape), "output": plain(prediction)})
            evidence["frames"].append(result)
            (output / "comparison.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"source {source_index} @ {timestamp:g}s: {len(reference)} boxes", flush=True)
    for mode in ("serial", "standard8", "same_width8"):
        rows = [r for f in evidence["frames"] for r in f["runs"] if r["mode"] == mode]
        evidence["summary"][mode] = {
            "sum_frame_median_seconds": sum(statistics.median(r["seconds"] for r in f["runs"] if r["mode"] == mode) for f in evidence["frames"]),
            "exact_equal_all": all(r["comparison"]["exact_equal"] for r in rows),
            "accepted_boxes_text_equal_all": all(r["comparison"]["accepted_boxes_text_equal"] for r in rows),
            "text_change_runs": sum(bool(r["comparison"]["text_changes"]) for r in rows),
            "max_score_delta": max(r["comparison"]["max_score_delta"] or 0 for r in rows),
        }
    (output / "comparison.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence["summary"], indent=2))
    print(output)


if __name__ == "__main__":
    main()
