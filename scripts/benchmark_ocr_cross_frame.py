"""Bounded cross-frame OCR recognition experiment; never touches production jobs.

Subcommands:
  occupancy  Serial detection only; records exact padded widths and simulates
             how many recognition calls same-width grouping needs per window.
  abba       Serial readtext (A) vs CrossFrameReader (B) on identical in-memory
             frames, A/B/B/A order; compares raw predictions and acceptance.
  downstream Real scan_text + advertising review queue per excerpt, serial vs
             cross-frame, A/B/B/A; compares reports, confidences, previews, review.
  cancel     Stops real cross-frame CUDA/FFmpeg workers with the scheduler's
             process-tree method; checks no child or complete report remains.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import subprocess
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from time import perf_counter

import numpy as np

from biliflow.license_policy import ensure_model_allowed
from biliflow.ocr_batch_experiment import MAX_BATCH_PIXELS
from biliflow.probe import duration_seconds, probe_video
from biliflow.textscan import _read_exact, _video_size

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = ROOT / "tools/ffmpeg/bin/ffmpeg.exe"
FFPROBE = ROOT / "tools/ffmpeg/bin/ffprobe.exe"
DEFAULT_SEGMENTS = [
    ("*Troy*", 0, 60), ("*Troy*", 48, 90), ("*Troy*", 418, 60), ("*Troy*", 940, 60),
    ("*Movie 21*", 4200, 60), ("*Movie 21*", 6630, 60), ("*Movie 20*", 240, 60),
]


def resolve_source(pattern: str) -> Path:
    sources = list((ROOT / "input").glob(pattern + ".mp4"))
    if len(sources) != 1:
        raise ValueError(f"Expected exactly one source for {pattern}, found {len(sources)}")
    return sources[0].resolve(strict=True)


def iter_scan_text_frames(source: Path, start: float, duration: float, sample_every: float,
                          analysis_width: int = 960):
    """Decode with the exact FFmpeg command used by textscan.scan_text."""
    probe = probe_video(FFPROBE, source)
    width, height = _video_size(probe)
    analysis_height = max(2, round(height * analysis_width / width / 2) * 2)
    scan_duration = min(duration_seconds(probe) - start, duration)
    frame_bytes = analysis_width * analysis_height * 3
    command = [
        str(FFMPEG), "-hide_banner", "-loglevel", "error",
        "-ss", str(start), "-i", str(source), "-t", str(scan_duration),
        "-vf", f"fps={1.0 / sample_every},scale={analysis_width}:{analysis_height}:flags=bilinear",
        "-an", "-sn", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    index = 0
    try:
        while True:
            data = _read_exact(process.stdout, frame_bytes)
            if not data:
                break
            if len(data) != frame_bytes:
                raise RuntimeError(f"Incomplete raw frame: {len(data)} of {frame_bytes} bytes")
            yield start + index * sample_every, np.frombuffer(data, dtype=np.uint8).reshape(
                (analysis_height, analysis_width, 3))
            index += 1
        if process.wait():
            raise RuntimeError(process.stderr.read().decode("utf-8", errors="replace")[-2000:])
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def load_reader():
    ensure_model_allowed(ROOT, ROOT / "models/easyocr")
    import easyocr
    return easyocr.Reader(["vi", "en"], gpu=True, download_enabled=False,
                          model_storage_directory=str(ROOT / "models/easyocr"),
                          user_network_directory=str(ROOT / "models/easyocr"))


def frame_widths(reader, frame) -> list[int]:
    """Exact per-crop padded widths the serial recognize() path would use."""
    from easyocr.config import imgH
    from easyocr.utils import get_image_list, reformat_input
    color, grey = reformat_input(frame)
    horizontal, free = reader.detect(color, reformat=False)
    widths = []
    for h_box, f_box in [(b, None) for b in horizontal[0]] + [(None, b) for b in free[0]]:
        crops, width = get_image_list([h_box] if h_box is not None else [],
                                      [f_box] if f_box is not None else [], grey, model_height=imgH)
        widths.extend([int(width)] * len(crops))
    return widths


def simulated_calls(frames: list[list[int]], window: int, batch_size: int = 8, image_height: int = 64):
    calls = 0
    batched_crops = 0
    for offset in range(0, len(frames), window):
        buckets = Counter(w for widths in frames[offset:offset + window] for w in widths)
        for width, count in buckets.items():
            effective = min(batch_size, max(1, MAX_BATCH_PIXELS // (image_height * width)))
            calls += math.ceil(count / effective)
            if effective > 1 and count > 1:
                batched_crops += count
    return calls, batched_crops


def occupancy(args):
    out = ROOT / "reports/benchmarks" / ("ocr-cross-frame-occupancy-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=False)
    print(out, flush=True)
    reader = load_reader()
    segments = []
    all_frames: list[list[int]] = []
    for pattern, start, duration in DEFAULT_SEGMENTS:
        source = resolve_source(pattern)
        before = source.stat()
        rows = []
        started = perf_counter()
        for timestamp, frame in iter_scan_text_frames(source, start, duration, args.sample_every):
            rows.append({"timestamp": round(timestamp, 3),
                         "rgb_sha256": hashlib.sha256(frame.tobytes()).hexdigest(),
                         "widths": frame_widths(reader, frame)})
        after = source.stat()
        widths = [r["widths"] for r in rows]
        all_frames.extend(widths)
        segments.append({
            "source": source.name, "start": start, "duration": duration, "frames": len(rows),
            "crops": sum(map(len, widths)), "frames_without_text": sum(1 for w in widths if not w),
            "width_histogram": dict(sorted(Counter(w for ws in widths for w in ws).items())),
            "calls": {f"serial": sum(map(len, widths)),
                      **{f"window_{n}": simulated_calls(widths, n)[0] for n in (1, 2, 4, 8)}},
            "source_stat_unchanged": (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
            "detect_seconds": perf_counter() - started, "rows": rows,
        })
        print(json.dumps({k: v for k, v in segments[-1].items() if k != "rows"}), flush=True)
    total = {"frames": len(all_frames), "crops": sum(map(len, all_frames)),
             "calls_serial": sum(map(len, all_frames))}
    for n in (1, 2, 4, 8):
        calls = sum(simulated_calls([r["widths"] for r in s["rows"]], n)[0] for s in segments)
        total[f"calls_window_{n}"] = calls
    import torch
    import easyocr
    result = {"scope": "Serial detection only; exact padded recognition widths; no recognition/report",
              "sample_every": args.sample_every, "analysis_width": 960,
              "max_batch_pixels": MAX_BATCH_PIXELS, "batch_size": 8,
              "environment": {"python": platform.python_version(), "torch": torch.__version__,
                              "cuda": torch.version.cuda, "easyocr": easyocr.__version__,
                              "gpu": torch.cuda.get_device_name(0)},
              "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "total": total, "segments": segments}
    (out / "occupancy.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(total, indent=2), flush=True)


def serializable(prediction):
    points, text, confidence = prediction
    return [[[int(x), int(y)] for x, y in points], str(text), float(confidence)]


def accepted(predictions, width, height, minimum_confidence=0.35):
    """Replicates scan_text's filter/acceptance and its confidence sort order."""
    from biliflow.textscan import _accept_detection, _box_from_points, _clean_text
    rows = []
    for points, text, confidence in predictions:
        box = _box_from_points(points)
        if box[2] - box[0] < 8 or box[3] - box[1] < 6:
            continue
        cleaned = _clean_text(str(text))
        if _accept_detection(confidence=float(confidence), text=cleaned, box=box, width=width,
                             height=height, minimum_confidence=minimum_confidence):
            rows.append({"box": list(box), "text": cleaned, "confidence": float(confidence)})
    return [(row["box"], row["text"]) for row in sorted(rows, key=lambda r: r["confidence"], reverse=True)]


def compare(reference, candidate, sizes):
    frames = mismatched_frames = points_or_text = acceptance = count = 0
    max_delta = 0.0
    deltas = []
    for (ref_seg, cand_seg, (width, height)) in zip(reference, candidate, sizes, strict=True):
        if len(ref_seg) != len(cand_seg):
            raise RuntimeError("Frame count differs between passes")
        for ref, cand in zip(ref_seg, cand_seg):
            frames += 1
            bad = False
            if len(ref) != len(cand):
                count += 1
                bad = True
            else:
                for r, c in zip(ref, cand):
                    if r[0] != c[0] or r[1] != c[1]:
                        points_or_text += 1
                        bad = True
                    delta = abs(r[2] - c[2])
                    deltas.append(delta)
                    max_delta = max(max_delta, delta)
            if accepted(ref, width, height) != accepted(cand, width, height):
                acceptance += 1
                bad = True
            mismatched_frames += bad
    return {"frames": frames, "mismatched_frames": mismatched_frames,
            "prediction_count_mismatches": count, "points_or_text_mismatches": points_or_text,
            "acceptance_or_order_mismatches": acceptance, "max_confidence_delta": max_delta,
            "nonzero_confidence_deltas": sum(1 for d in deltas if d), "predictions": len(deltas),
            "equivalent": mismatched_frames == 0}


def abba(args):
    import psutil
    import torch
    import easyocr
    from biliflow.ocr_batch_experiment import CrossFrameReader, VALIDATED_READTEXT_OPTIONS
    out = ROOT / "reports/benchmarks" / ("ocr-cross-frame-abba-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=False)
    print(out, flush=True)
    load_started = perf_counter()
    reader = load_reader()
    torch.cuda.synchronize()
    model_load_seconds = perf_counter() - load_started

    segments = []
    for pattern, start, duration in DEFAULT_SEGMENTS[:args.segments]:
        source = resolve_source(pattern)
        before = source.stat()
        frames = [frame.copy() for _, frame in iter_scan_text_frames(source, start, duration, 3.0)]
        segments.append({"source": source, "start": start, "duration": duration, "frames": frames,
                         "stat": (before.st_size, before.st_mtime_ns),
                         "rgb_sha256": [hashlib.sha256(f.tobytes()).hexdigest() for f in frames]})
    sizes = [(s["frames"][0].shape[1], s["frames"][0].shape[0]) for s in segments]

    timing = {"detect": 0.0, "recognize": 0.0}
    original_detect, original_recognize = reader.detect, reader.recognize

    def timed(name, method):
        def wrapped(*a, **k):
            started = perf_counter()
            try:
                return method(*a, **k)
            finally:
                timing[name] += perf_counter() - started
        return wrapped
    reader.detect = timed("detect", original_detect)
    reader.recognize = timed("recognize", original_recognize)
    process = psutil.Process()

    def run_pass(label, config):
        config = dict(config or {})
        # cuDNN autotuning keeps fp32, shapes and all preprocessing; only the
        # convolution kernel choice changes, so outputs are still compared.
        torch.backends.cudnn.benchmark = bool(config.pop("cudnn_benchmark", False))
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
        predictions, per_segment, stats = [], [], []
        peak_rss = process.memory_info().rss
        for segment in segments:
            timing.update(detect=0.0, recognize=0.0)
            started = perf_counter()
            if not config:
                rows = [[serializable(p) for p in reader.readtext(f, **VALIDATED_READTEXT_OPTIONS)]
                        for f in segment["frames"]]
                recognize_seconds = timing["recognize"]
                reader_stats = None
            else:
                cross = CrossFrameReader(reader, byte_budget=args.byte_budget_mib * 1024 * 1024, **config)
                rows = [[serializable(p) for p in preds]
                        for _, preds in cross.iter_readtext(segment["frames"], **VALIDATED_READTEXT_OPTIONS)]
                recognize_seconds = cross.stats.recognize_seconds
                reader_stats = cross.stats.as_dict()
            torch.cuda.synchronize()
            wall = perf_counter() - started
            peak_rss = max(peak_rss, process.memory_info().rss)
            predictions.append(rows)
            per_segment.append({"wall_seconds": wall, "detect_seconds": timing["detect"],
                                "recognize_seconds": recognize_seconds})
            stats.append(reader_stats)
        result = {"label": label, "config": config,
                  "wall_seconds": sum(s["wall_seconds"] for s in per_segment),
                  "detect_seconds": sum(s["detect_seconds"] for s in per_segment),
                  "recognize_seconds": sum(s["recognize_seconds"] for s in per_segment),
                  "per_segment": per_segment, "cross_frame_stats": stats,
                  "cuda_max_allocated": torch.cuda.max_memory_allocated(),
                  "cuda_max_reserved": torch.cuda.max_memory_reserved(), "peak_rss": peak_rss}
        print(json.dumps({k: v for k, v in result.items() if k not in {"per_segment", "cross_frame_stats"}}),
              flush=True)
        return result, predictions

    # Warmup both paths identically on the first segment; results discarded.
    warm = segments[0]["frames"][:8]
    for f in warm:
        reader.readtext(f, **VALIDATED_READTEXT_OPTIONS)
    # A serial; B recognition across frames; C/G = A/B with cuDNN autotuning.
    # Batched detection (former D/E) was rejected: no speedup, 2.7x CUDA memory;
    # its code lives in reports/benchmarks/ocr-cross-frame-abba-20260929-000902/.
    modes = {"A": None, "B": dict(batch_size=8, frame_window=args.window),
             "C": dict(cudnn_benchmark=True),
             "G": dict(batch_size=8, frame_window=args.window, cudnn_benchmark=True)}
    order = list(args.order) * args.rounds
    if order[0] != "A" or set(order) - set(modes):
        raise ValueError("--order must start with A and use only " + ", ".join(modes))
    for label in dict.fromkeys(order):
        config = dict(modes[label] or {})
        autotune = config.pop("cudnn_benchmark", False)
        torch.backends.cudnn.benchmark = autotune
        # Autotuning runs once per new tensor shape; warm every shape it will see.
        frames = [f for s in segments for f in s["frames"]] if autotune else warm
        if config:
            list(CrossFrameReader(reader, **config).iter_readtext(frames, **VALIDATED_READTEXT_OPTIONS))
        elif autotune:
            for f in frames:
                reader.readtext(f, **VALIDATED_READTEXT_OPTIONS)
    torch.backends.cudnn.benchmark = False

    passes, outputs = [], {}
    for index, label in enumerate(order):
        result, preds = run_pass(label, modes[label])
        passes.append(result)
        outputs.setdefault(label, preds)
        if index:
            result["comparison_to_first_A"] = compare(outputs["A"], preds, sizes)
    extra = []
    for window in args.extra_windows:
        result, preds = run_pass(f"W{window}", dict(batch_size=8, frame_window=window))
        result["comparison_to_first_A"] = compare(outputs["A"], preds, sizes)
        extra.append(result)
    reader.detect, reader.recognize = original_detect, original_recognize
    torch.backends.cudnn.benchmark = False

    def summary(label):
        rows = [p for p in passes if p["label"] == label]
        pick = lambda key: sorted(p[key] for p in rows)
        return {key: {"median": statistics.median(pick(key)), "min": min(pick(key)), "max": max(pick(key))}
                for key in ("wall_seconds", "detect_seconds", "recognize_seconds")}
    by_mode = {label: summary(label) for label in dict.fromkeys(order)}
    a = by_mode["A"]
    stat_unchanged = all((s["source"].stat().st_size, s["source"].stat().st_mtime_ns) == s["stat"]
                         for s in segments)
    comparisons = [p["comparison_to_first_A"] for p in passes + extra if p.get("comparison_to_first_A")]
    result = {
        "scope": ("OCR detect+recognize only on in-memory scan_text frames (decode/hashing excluded); "
                  "no tracking/semantics/report/review. Acceptance replicates scan_text filter and sort."),
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "git_dirty": bool(subprocess.check_output(["git", "status", "--porcelain", "--", "src"], cwd=ROOT, text=True).strip()),
        "environment": {"python": platform.python_version(), "torch": torch.__version__, "cuda": torch.version.cuda,
                        "easyocr": easyocr.__version__, "gpu": torch.cuda.get_device_name(0)},
        "model_load_seconds": model_load_seconds, "window": args.window, "batch_size": 8,
        "byte_budget_mib": args.byte_budget_mib, "order": order,
        "segments": [{"source": s["source"].name, "start": s["start"], "duration": s["duration"],
                      "frames": len(s["frames"]), "rgb_sha256": s["rgb_sha256"]} for s in segments],
        "summary": {**by_mode, "change_percent_vs_A": {
            label: {key: (m[key]["median"] / a[key]["median"] - 1) * 100
                    for key in ("wall_seconds", "detect_seconds", "recognize_seconds")}
            for label, m in by_mode.items() if label != "A"}},
        "all_equivalent": all(c["equivalent"] for c in comparisons),
        "source_stat_unchanged": stat_unchanged, "passes": passes, "extra_windows": extra,
    }
    (out / "abba.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (out / "reference_predictions.json").write_text(json.dumps(outputs["A"]), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("summary", "all_equivalent", "source_stat_unchanged")}, indent=2))
    print(json.dumps([{"label": p["label"], **(p.get("comparison_to_first_A") or {})} for p in passes + extra], indent=1))
    if not result["all_equivalent"]:
        raise SystemExit("Cross-frame OCR output differs from serial baseline")


def confidence_values(value, path=""):
    """Every confidence-like number in a report, keyed by its JSON path."""
    found = {}
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"metrics", "created_at"}:
                continue
            child = f"{path}/{key}"
            if key in {"confidence", "max_confidence", "mean_confidence"} and isinstance(item, (int, float)):
                found[child] = float(item)
            else:
                found.update(confidence_values(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.update(confidence_values(item, f"{path}[{index}]"))
    return found


def downstream(args):
    """Full scan_text + review queue on original sources: serial (A) vs cross-frame (B)."""
    import sys
    import torch
    sys.path.insert(0, str(ROOT / "scripts"))
    from benchmark_ocr_contiguous import image_hashes, normalize, queue_projection
    from biliflow.review_workflow import build_review_queue
    from biliflow.text_semantics import LocalEmbeddingTextClassifier
    from biliflow.textscan import scan_text
    out = ROOT / "reports/benchmarks" / ("ocr-cross-frame-downstream-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=False)
    (out / ".biliflow-benchmark").write_text("isolated cross-frame OCR benchmark\n", encoding="utf-8")
    print(out, flush=True)
    ensure_model_allowed(ROOT, ROOT / "models/multilingual_minilm_text_semantics")
    started = perf_counter()
    reader = load_reader()
    classifier = LocalEmbeddingTextClassifier(ROOT / "models/multilingual_minilm_text_semantics",
                                              ROOT / "annotations/text_semantics_seed_v1.json", "cuda")
    torch.cuda.synchronize()
    model_load_seconds = perf_counter() - started
    if args.full_source:
        segments = [(resolve_source(args.full_source), 0, None)]
    else:
        segments = [(resolve_source(p), s, d) for p, s, d in DEFAULT_SEGMENTS[:args.segments]]
    production = None
    if args.production_report:
        production = json.loads((ROOT / args.production_report).resolve(strict=True).read_text(encoding="utf-8"))
    stats_before = {str(src): (src.stat().st_size, src.stat().st_mtime_ns) for src, _, _ in segments}
    modes = {"A": dict(recognition_batch_size=1), "B": dict(recognition_batch_size=8, recognition_frame_window=args.window)}

    def run_segment(directory, source, start, duration, mode):
        common = dict(project_root=ROOT, input_path=source, model_dir=ROOT / "models/easyocr",
                      ffmpeg_path=FFMPEG, ffprobe_path=FFPROBE, reader=reader, semantic_classifier=classifier,
                      policy_path=ROOT / "config/text_review_policy.json", start_seconds=start,
                      duration_seconds_limit=duration, sample_every=3.0)
        torch.cuda.synchronize()
        began = perf_counter()
        report = scan_text(**common, report_dir=directory, **modes[mode])
        torch.cuda.synchronize()
        seconds = perf_counter() - began
        queue = build_review_queue(project_root=ROOT, report_paths=[directory / "text-scan.json"],
                                   queue_path=directory / "review-queue.json", selected_detectors=["advertising"])
        phases = report["metrics"]["performance"]["phases"]
        return report, queue, {
            "scan_seconds": seconds, "source_hash_seconds": phases["source_hash"]["wall_seconds"],
            "scan_without_hash_seconds": seconds - phases["source_hash"]["wall_seconds"],
            "model_step_seconds": phases["model_step"]["wall_seconds"],
            "frame_pipe_wait_seconds": phases["frame_pipe_wait"]["wall_seconds"],
            "tracking_seconds": phases["tracking"]["wall_seconds"],
            "frames": report["frames_scanned"], "tracks": len(report["tracks"]),
            "cross_frame": report["metrics"]["cross_frame_recognition"],
            "candidate_coverage_complete": queue["candidate_coverage"]["complete"],
            "review_items": len(queue["items"]), "advisory_items": len(queue.get("advisory_items", []))}

    # Warmup both modes on a short excerpt; excluded from results.
    for mode in modes:
        run_segment(out / f"warmup-{mode}", segments[0][0], segments[0][1], 9, mode)
    order = ["A", "B", "B", "A"] * args.rounds
    reference = {}
    runs = []
    for index, mode in enumerate(order):
        torch.cuda.reset_peak_memory_stats()
        rows = []
        for seg_index, (source, start, duration) in enumerate(segments):
            directory = out / f"run-{index}-{mode}" / f"segment-{seg_index}"
            report, queue, row = run_segment(directory, source, start, duration, mode)
            hashes = image_hashes(directory)
            confidences = confidence_values(report)
            if seg_index not in reference:
                reference[seg_index] = (report, queue, hashes, confidences)
            ref_report, ref_queue, ref_hashes, ref_conf = reference[seg_index]
            deltas = [abs(confidences[k] - ref_conf[k]) for k in ref_conf if k in confidences]
            row.update({
                "segment": seg_index,
                "report_equal_except_confidence_and_metrics": normalize(report) == normalize(ref_report),
                "confidence_paths_equal": set(confidences) == set(ref_conf),
                "max_confidence_delta": max(deltas, default=0.0),
                "review_projection_equal": queue_projection(queue) == queue_projection(ref_queue),
                "preview_hashes_equal": hashes == ref_hashes, "preview_count": len(hashes),
            })
            if production is not None:
                # Read-only sanity check against the production serial report.
                row["tracks_equal_to_production"] = normalize(report["tracks"]) == normalize(production["tracks"])
                row["frames_equal_to_production"] = report["frames_scanned"] == production["frames_scanned"]
            row["passed"] = all(row[k] for k in ("report_equal_except_confidence_and_metrics", "confidence_paths_equal",
                "review_projection_equal", "preview_hashes_equal", "candidate_coverage_complete"))
            rows.append(row)
        run = {"index": index, "mode": mode, "segments": rows,
               "totals": {k: sum(r[k] for r in rows) for k in ("scan_seconds", "source_hash_seconds",
                          "scan_without_hash_seconds", "model_step_seconds", "frame_pipe_wait_seconds", "tracking_seconds")},
               "cuda_max_allocated": torch.cuda.max_memory_allocated(),
               "cuda_max_reserved": torch.cuda.max_memory_reserved(),
               "passed": all(r["passed"] for r in rows)}
        runs.append(run)
        print(json.dumps({"index": index, "mode": mode, **run["totals"], "passed": run["passed"],
                          "max_confidence_delta": max(r["max_confidence_delta"] for r in rows)}), flush=True)
    medians = {mode: {k: statistics.median(r["totals"][k] for r in runs if r["mode"] == mode)
                      for k in runs[0]["totals"]} for mode in modes}
    stats_after = {str(src): (src.stat().st_size, src.stat().st_mtime_ns) for src, _, _ in segments}
    result = {
        "scope": "scan_text (OCR + text semantics + previews) and advertising review queue per excerpt; "
                 "no visual logo, safety detectors, Visual AI or export",
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "git_diff_src_sha256": hashlib.sha256(subprocess.check_output(["git", "diff", "--", "src"], cwd=ROOT)).hexdigest(),
        "model_load_seconds": model_load_seconds, "gpu": torch.cuda.get_device_name(0),
        "modes": modes, "order": order,
        "segments": [{"source": s.name, "start": st, "duration": d} for s, st, d in segments],
        "medians": medians,
        "change_percent": {k: (medians["B"][k] / medians["A"][k] - 1) * 100 for k in medians["A"] if medians["A"][k]},
        "all_passed": all(r["passed"] for r in runs),
        "source_stat_unchanged": stats_before == stats_after, "runs": runs,
    }
    (out / "downstream.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("medians", "change_percent", "all_passed", "source_stat_unchanged")}, indent=2))
    if not (result["all_passed"] and result["source_stat_unchanged"]):
        raise SystemExit("Downstream equivalence failed; keep serial default")


def cancel_worker(directory: Path):
    """Child process: cross-frame scan_text on Troy 48s; marks the first shared batch."""
    import os
    import torch
    from biliflow.textscan import scan_text
    reader = load_reader()
    marker = directory / "gpu-ready.json"

    def ready(module, inputs, output):
        if not marker.exists() and inputs[0].shape[0] > 1:
            torch.cuda.synchronize()
            marker.write_text(json.dumps({"pid": os.getpid(), "recognition_batch_crops": int(inputs[0].shape[0]),
                                          "cuda_allocated": torch.cuda.memory_allocated()}), encoding="utf-8")
    reader.recognizer.register_forward_hook(ready)
    scan_text(project_root=ROOT, input_path=resolve_source("*Troy*"), model_dir=ROOT / "models/easyocr",
              ffmpeg_path=FFMPEG, ffprobe_path=FFPROBE, reader=reader, start_seconds=48,
              duration_seconds_limit=90, sample_every=3.0, report_dir=directory / "interrupted-report",
              recognition_batch_size=8, recognition_frame_window=4)


def cancel(args):
    """Stop real CUDA + FFmpeg workers with the production process-tree method."""
    import os
    import sys
    import time
    import psutil
    from biliflow.scheduler import JobScheduler
    out = ROOT / "reports/benchmarks" / ("ocr-cross-frame-cancel-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=False)
    print(out, flush=True)
    results = []
    for number in range(args.trials):
        directory = out / f"cancel-{number}"
        directory.mkdir()
        with (directory / "worker.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "cancel-worker", str(directory)],
                                       cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            try:
                marker = directory / "gpu-ready.json"
                deadline = time.monotonic() + 120
                while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.05)
                if not marker.exists() or process.poll() is not None:
                    raise RuntimeError("Worker did not reach a shared cross-frame batch; inspect worker.log")
                children = psutil.Process(process.pid).children(recursive=True)
                if not any(p.name().lower().startswith("ffmpeg") for p in children):
                    raise RuntimeError("No active FFmpeg child at cancellation checkpoint")
                began = time.perf_counter()
                JobScheduler._terminate_process(None, process)
                elapsed = time.perf_counter() - began
                _, alive = psutil.wait_procs(children, timeout=5)
                result = {"worker_pid": process.pid, "child_pids": [p.pid for p in children],
                          "marker": json.loads(marker.read_text(encoding="utf-8")),
                          "stop_seconds": elapsed, "exit_code": process.returncode,
                          "all_children_exited": not alive,
                          "no_complete_report": not (directory / "interrupted-report/text-scan.json").exists()}
            finally:
                if process.poll() is None:
                    JobScheduler._terminate_process(None, process)
        results.append(result)
        print(json.dumps(result), flush=True)
    # GPU must still serve inference after the stopped workers.
    reader = load_reader()
    frame = next(iter_scan_text_frames(resolve_source("*Troy*"), 48, 3, 3.0))[1]
    after = len(reader.readtext(frame, detail=1, paragraph=False, batch_size=1, workers=0, decoder="greedy"))
    evidence = {"scope": "Real CUDA OCR worker + FFmpeg child stopped during cross-frame recognition",
                "trials": results, "post_cancel_inference_boxes": after,
                "passed": all(r["all_children_exited"] and r["no_complete_report"] for r in results)}
    (out / "cancel.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps({k: evidence[k] for k in ("post_cancel_inference_boxes", "passed")}), flush=True)
    if not evidence["passed"]:
        raise SystemExit("Cancellation left a child process or a completed partial report")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    occ = sub.add_parser("occupancy")
    occ.add_argument("--sample-every", type=float, default=3.0)
    ab = sub.add_parser("abba")
    ab.add_argument("--window", type=int, default=4)
    ab.add_argument("--order", default="ABBA", help="pass order per round; A first; modes A/B/D/E")
    ab.add_argument("--rounds", type=int, default=2, choices=(1, 2, 3))
    ab.add_argument("--segments", type=int, default=len(DEFAULT_SEGMENTS), choices=range(1, len(DEFAULT_SEGMENTS) + 1))
    ab.add_argument("--extra-windows", type=int, nargs="*", default=[1, 2, 8])
    ab.add_argument("--byte-budget-mib", type=int, default=32)
    down = sub.add_parser("downstream")
    down.add_argument("--window", type=int, default=4)
    down.add_argument("--rounds", type=int, default=1, choices=(1, 2))
    down.add_argument("--segments", type=int, default=len(DEFAULT_SEGMENTS), choices=range(1, len(DEFAULT_SEGMENTS) + 1))
    down.add_argument("--full-source", help="input glob (e.g. '*Troy*') to scan the whole film instead of excerpts")
    down.add_argument("--production-report", help="project-relative serial text-scan.json for a read-only check")
    stop = sub.add_parser("cancel")
    stop.add_argument("--trials", type=int, default=3, choices=(1, 2, 3))
    worker = sub.add_parser("cancel-worker")
    worker.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.command == "cancel-worker":
        directory = args.directory.resolve(strict=True)
        if not directory.is_relative_to(ROOT / "reports/benchmarks"):
            raise ValueError("Worker evidence must be under benchmark reports")
        cancel_worker(directory)
        return
    {"occupancy": occupancy, "abba": abba, "downstream": downstream, "cancel": cancel}[args.command](args)


if __name__ == "__main__":
    main()
