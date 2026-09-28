"""Isolated synthetic OCR boundary, moving-text and real GPU cancellation checks.

Synthetic text is labeled and retained; this is equivalence evidence, not a
substitute for recall measurements on annotated movies. Never edits live jobs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime

from benchmark_ocr_batch import compare, plain
from benchmark_ocr_contiguous import normalize, image_hashes, queue_projection, md5_frames
from biliflow.license_policy import ensure_model_allowed
from biliflow.review_workflow import build_review_queue
from biliflow.scheduler import JobScheduler
from biliflow.textscan import scan_text, _accept_detection, _box_from_points, _clean_text


def acceptance_margin(row):
    """Use the scanner's effective cutoff, not proximity to an unused threshold."""
    points, text, score = row
    box = _box_from_points(points)
    if box[2] - box[0] < 8 or box[3] - box[1] < 6:
        return None
    options = dict(text=_clean_text(text), box=box, width=960, height=540, minimum_confidence=.35)
    cutoff = .1 if _accept_detection(confidence=.1, **options) else .35
    if not _accept_detection(confidence=cutoff, **options):
        return None
    return {"cutoff": cutoff, "score": float(score), "margin": abs(float(score) - cutoff)}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def verify_fixture_pixels(root, output):
    from PIL import Image
    checks = {}
    for family in ("moving", "boundary", "top-banner"):
        expected = []
        for frame in sorted((output / family).glob("*.png")):
            with Image.open(frame) as image:
                expected.append(hashlib.md5(image.convert("RGB").tobytes()).hexdigest())
        actual = md5_frames(root / "tools/ffmpeg/bin/ffmpeg.exe",
            ["-i", str(output / f"{family}.mkv")], "fps=1/3,scale=960:540:flags=bilinear,format=rgb24")
        checks[family] = {"source": expected, "decoded": actual, "equal": expected == actual and len(expected) == 16}
    write_json(output / "fixture-pixel-hashes.json", checks)
    if not all(check["equal"] for check in checks.values()):
        raise RuntimeError("Synthetic fixture decoding changed or dropped frame pixels")


def load_reader(root):
    import easyocr
    ensure_model_allowed(root, root / "models/easyocr")
    return easyocr.Reader(["vi", "en"], gpu=True, download_enabled=False,
        model_storage_directory=str(root / "models/easyocr"),
        user_network_directory=str(root / "models/easyocr"))


def scan_args(root, clip):
    return dict(project_root=root, input_path=clip, model_dir=root / "models/easyocr",
        ffmpeg_path=root / "tools/ffmpeg/bin/ffmpeg.exe",
        ffprobe_path=root / "tools/ffmpeg/bin/ffprobe.exe",
        policy_path=root / "config/text_review_policy.json")


def cancel_worker(root, directory):
    import torch
    reader = load_reader(root)
    marker = directory / "gpu-ready.json"
    def ready(module, args, output):
        if not marker.exists():
            torch.cuda.synchronize()
            write_json(marker, {"pid": os.getpid(), "cuda_allocated": torch.cuda.memory_allocated()})
    reader.recognizer.register_forward_hook(ready)
    scan_text(**scan_args(root, directory.parent / "moving.mkv"),
        reader=reader, report_dir=directory / "interrupted-report", recognition_batch_size=8)


def cancel_trial(root, output, number):
    import psutil
    directory = output / f"cancel-{number}"
    directory.mkdir()
    with (directory / "worker.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
            "--cancel-worker", str(directory)], cwd=root, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            marker = directory / "gpu-ready.json"
            deadline = time.monotonic() + 90
            while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.05)
            if not marker.exists() or process.poll() is not None:
                raise RuntimeError("GPU worker did not reach active recognition; inspect worker.log")
            children = psutil.Process(process.pid).children(recursive=True)
            if not any(p.name().lower().startswith("ffmpeg") for p in children):
                raise RuntimeError("No active FFmpeg child at cancellation checkpoint")
            began = time.perf_counter()
            # Exactly the production immediate-stop process-tree method, on our
            # own fresh worker only. No scheduler/store or existing PID is used.
            JobScheduler._terminate_process(None, process)
            elapsed = time.perf_counter() - began
            _, alive = psutil.wait_procs(children, timeout=5)
            result = {"worker_pid": process.pid, "child_pids": [p.pid for p in children],
                "stop_seconds": elapsed, "exit_code": process.returncode,
                "all_children_exited": not alive,
                "no_complete_report": not (directory / "interrupted-report/text-scan.json").exists()}
            write_json(directory / "result.json", result)
            if alive or not result["no_complete_report"]:
                raise RuntimeError("Cancellation left a child or a completed partial report")
            return result
        finally:
            if process.poll() is None:
                JobScheduler._terminate_process(None, process)


def make_fixtures(root, output, families=("moving", "boundary", "top-banner")):
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/arial.ttf"
    labels = []
    rng = np.random.default_rng(8701)
    for family in families:
        folder = output / family
        folder.mkdir()
        for index in range(16):
            image = Image.new("RGB", (960, 540), (35, 40, 45))
            draw = ImageDraw.Draw(image)
            entries = []
            if family == "moving":
                # Boundary clipping, appearing/disappearing text and two
                # independent stationary distractors, not a movie-specific rule.
                x = 1000 - index * 100
                text = "QUANG CAO - TRUY CAP DEMO.EXAMPLE"
                font = ImageFont.truetype(str(font_path), 24)
                if index not in (8, 9):
                    box = draw.textbbox((x, 30), text, font=font)
                    draw.text((x, 30), text, font=font, fill=(245, 230, 70))
                    entries.append({"text": text, "box_unclipped": box})
                for text, pos in (("PHIM THU NGHIEM", (350, 290)), ("DEMO.EXAMPLE", (700, 12))):
                    font = ImageFont.truetype(str(font_path), 16)
                    entries.append({"text": text, "box_unclipped": draw.textbbox(pos, text, font=font)})
                    draw.text(pos, text, font=font, fill=(220, 220, 220))
            elif family == "boundary":
                size = (10, 12, 16, 20)[index % 4]
                font = ImageFont.truetype(str(font_path), size)
                for line in range(8):
                    text = f"DEMO{line}.EXAMPLE"
                    pos = (40 + line % 2 * 440, 15 + line // 2 * 130)
                    fill = 55 + line * 24
                    entries.append({"text": text, "box_unclipped": draw.textbbox(pos, text, font=font)})
                    draw.text(pos, text, font=font, fill=(fill, fill, fill))
                image = image.filter(ImageFilter.GaussianBlur((index // 4) * .65))
                noise = rng.normal(0, (index // 4) * 3, (540, 960, 1))
                image = Image.fromarray(np.uint8(np.clip(np.asarray(image).astype(float) + noise, 0, 255)))
            else:
                font = ImageFont.truetype(str(font_path), 12 + index % 4 * 2)
                for line in range(3):
                    text = "QUANG CAO TRUY CAP DEMO EXAMPLE"
                    pos = (25, 8 + line * 38)
                    fill = 95 + line * 65
                    entries.append({"text": text, "box_unclipped": draw.textbbox(pos, text, font=font)})
                    draw.text(pos, text, font=font, fill=(fill, fill, fill))
                image = image.filter(ImageFilter.GaussianBlur(.5 + index // 4 * .5))
            name = f"{index:03d}.png"
            image.save(folder / name)
            labels.append({"family": family, "frame": f"{family}/{name}", "index": index,
                           "labels": entries, "scope": "rendered text; no claim degraded/clipped labels are readable"})
        subprocess.run([str(root / "tools/ffmpeg/bin/ffmpeg.exe"), "-hide_banner", "-loglevel", "error",
            "-framerate", "1/3", "-i", str(folder / "%03d.png"), "-c:v", "ffv1", "-pix_fmt", "bgr0",
            str(output / f"{family}.mkv")], check=True)
    write_json(output / "fixture-labels.json", labels)
    return labels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cancel-worker", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.cancel_worker:
        directory = args.cancel_worker.resolve(strict=True)
        if not directory.is_relative_to(root / "reports/benchmarks"):
            raise ValueError("Worker evidence must be under benchmark reports")
        cancel_worker(root, directory)
        return
    output = root / "reports/benchmarks" / ("ocr-stress-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True, exist_ok=False)
    labels = make_fixtures(root, output)
    verify_fixture_pixels(root, output)
    evidence = {"scope": "Synthetic OCR equivalence and isolated GPU cancellation only",
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "cancel": [], "frames": [], "pipelines": []}
    for trial in range(3):
        evidence["cancel"].append(cancel_trial(root, output, trial))
        write_json(output / "comparison.json", evidence)
        print(f"GPU cancellation {trial}: {evidence['cancel'][-1]}", flush=True)
    import torch
    import numpy as np
    from PIL import Image
    from easyocr.utils import reformat_input
    from biliflow.ocr_batch_experiment import recognize_same_width
    from biliflow.text_semantics import LocalEmbeddingTextClassifier
    reader = load_reader(root)
    for item in labels:
        pixels = np.array(Image.open(output / item["frame"]).convert("RGB"))
        color, grey = reformat_input(pixels)
        horizontal, free = reader.detect(color, reformat=False)
        serial = reader.recognize(grey, horizontal[0], free[0], decoder="greedy", batch_size=1,
            workers=0, detail=1, paragraph=False, reformat=False)
        batch = recognize_same_width(reader, grey, horizontal[0], free[0], batch_size=8)
        evidence["frames"].append({"frame": item["frame"], "serial": plain(serial),
            "batch": plain(batch), "comparison": compare(serial, batch, grey.shape)})
        write_json(output / "comparison.json", evidence)
    ensure_model_allowed(root, root / "models/multilingual_minilm_text_semantics")
    classifier = LocalEmbeddingTextClassifier(root / "models/multilingual_minilm_text_semantics",
        root / "annotations/text_semantics_seed_v1.json", "cuda")
    for family in ("moving", "boundary", "top-banner"):
        reports, queues, hashes = [], [], []
        for batch in (1, 8):
            directory = output / f"{family}-batch-{batch}"
            report = scan_text(**scan_args(root, output / f"{family}.mkv"), reader=reader,
                semantic_classifier=classifier, report_dir=directory, recognition_batch_size=batch)
            queue = build_review_queue(project_root=root, report_paths=[directory / "text-scan.json"],
                queue_path=directory / "review-queue.json", selected_detectors=["advertising"])
            reports.append(report); queues.append(queue); hashes.append(image_hashes(directory))
        evidence["pipelines"].append({"family": family,
            "reports_equal_except_confidence": normalize(reports[0]) == normalize(reports[1]),
            "queues_equal": queue_projection(queues[0]) == queue_projection(queues[1]),
            "previews_equal": hashes[0] == hashes[1], "frames": [r["frames_scanned"] for r in reports],
            "tracks": [len(r["tracks"]) for r in reports],
            "coverage_complete": all(q["candidate_coverage"]["complete"] for q in queues)})
    scores = [float(row[2]) for frame in evidence["frames"] for row in frame["serial"]]
    margins = [{"frame": frame["frame"], **margin}
        for frame in evidence["frames"] for row in frame["serial"]
        if (margin := acceptance_margin(row)) is not None]
    evidence["acceptance_margins"] = sorted(margins, key=lambda item: item["margin"])
    evidence["summary"] = {"gpu": torch.cuda.get_device_name(), "raw_boxes": len(scores),
        "near_effective_0_35": sum(m["cutoff"] == .35 and m["margin"] <= .05 for m in margins),
        "near_effective_0_10": sum(m["cutoff"] == .1 and m["margin"] <= .03 for m in margins),
        "accepted_equal_all": all(f["comparison"]["accepted_boxes_text_equal"] for f in evidence["frames"]),
        "text_change_frames": sum(bool(f["comparison"]["text_changes"]) for f in evidence["frames"]),
        "max_score_delta": max(f["comparison"]["max_score_delta"] or 0 for f in evidence["frames"])}
    write_json(output / "comparison.json", evidence)
    print(json.dumps({"summary": evidence["summary"], "pipelines": evidence["pipelines"]}, indent=2))
    print(output, flush=True)
    if (not evidence["summary"]["accepted_equal_all"] or evidence["summary"]["text_change_frames"]
        or not evidence["summary"]["near_effective_0_35"] or not evidence["summary"]["near_effective_0_10"]
        or not all(p["reports_equal_except_confidence"] and p["queues_equal"] and p["previews_equal"]
                   and p["coverage_complete"] and p["frames"] == [16, 16] for p in evidence["pipelines"])):
        raise RuntimeError("Stress equivalence gate failed; preserve evidence and keep serial default")


if __name__ == "__main__":
    main()
