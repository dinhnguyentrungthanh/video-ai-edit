from __future__ import annotations

from biliflow.performance import ScanPerformance

import copy
import json
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2


VIOLENCE_CONFIRMATION_PROMPT = (
    "Do these images show people boxing, punching, or physically fighting each other? "
    "Answer with a single word: yes or no."
)

_TIMESTAMP_RE = re.compile(r"-(\d+(?:\.\d+)?)s\.(?:jpg|jpeg|png)$", re.IGNORECASE)


def _inside(root: Path, path: Path, label: str) -> Path:
    root = root.resolve(strict=True)
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"{label} must stay inside {root}")
    return resolved


def _timestamp(interval: dict) -> float:
    strongest = str(interval.get("strongest_frame", ""))
    match = _TIMESTAMP_RE.search(strongest)
    if match:
        return float(match.group(1))
    return (float(interval["start_seconds"]) + float(interval["end_seconds"])) / 2.0


def _answer_state(answer: str) -> str:
    normalized = answer.strip().casefold()
    if normalized.startswith("yes"):
        return "CONFIRMED"
    if normalized.startswith("no"):
        return "REJECTED"
    return "UNCERTAIN"


def _sample_context_frames(
    capture: cv2.VideoCapture,
    *,
    center_seconds: float,
    duration_seconds: float,
    context_seconds: float,
    frame_count: int,
    output_dir: Path,
) -> list[Path]:
    if frame_count < 1:
        raise ValueError("frame_count must be at least 1")
    if context_seconds <= 0:
        raise ValueError("context_seconds must be positive")
    if frame_count == 1:
        offsets = [0.0]
    else:
        step = context_seconds / (frame_count - 1)
        offsets = [(-context_seconds / 2.0) + step * index for index in range(frame_count)]
    frames = []
    for index, offset in enumerate(offsets, start=1):
        timestamp = min(max(0.0, center_seconds + offset), max(0.0, duration_seconds - 0.001))
        capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000.0)
        ok, frame = capture.read()
        if not ok:
            continue
        path = output_dir / f"frame-{index:02d}-{timestamp:.3f}s.jpg"
        if cv2.imwrite(str(path), frame):
            frames.append(path)
    return frames


def confirm_violence_report(
    *,
    project_root: Path,
    report_path: Path,
    output_path: Path,
    model_path: Path,
    device_name: str = "cuda",
    context_seconds: float = 2.0,
    frame_count: int = 5,
) -> dict:
    root = project_root.resolve(strict=True)
    reports_root = (root / "reports").resolve(strict=True)
    models_root = (root / "models").resolve(strict=True)
    report_path = _inside(reports_root, report_path, "Report path").resolve(strict=True)
    output_path = _inside(reports_root, output_path, "Output path")
    model_path = _inside(models_root, model_path, "Model path").resolve(strict=True)
    if output_path.parent != report_path.parent:
        raise ValueError("Confirmed report must stay beside its source report so previews remain valid")

    source = json.loads(report_path.read_text(encoding="utf-8"))
    if source.get("scan_type") != "violence":
        raise ValueError("Only violence scan reports can be confirmed")
    if source.get("status") not in {"COMPLETED", "REVIEW_REQUIRED"}:
        raise ValueError("Source report is not complete")
    video_path = Path(str(source["input"])).resolve(strict=True)
    intervals = list(source.get("intervals", []))
    manifest_path = model_path / "manifest.json"
    model_manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {"model": model_path.name}
    )

    performance = ScanPerformance()
    started = time.perf_counter()
    retained = []
    rejected = []
    answers = {"CONFIRMED": 0, "REJECTED": 0, "UNCERTAIN": 0}
    peak_cuda_memory = 0

    if intervals:
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor

        if device_name == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is not available")
        dtype = torch.float16 if device_name == "cuda" else torch.float32
        with performance.measure('model_load'):
            processor = AutoProcessor.from_pretrained(
                model_path,
                local_files_only=True,
                min_pixels=224 * 224,
                max_pixels=384 * 384,
            )
            model = AutoModelForImageTextToText.from_pretrained(
                model_path, local_files_only=True, dtype=dtype
            ).to(device_name)
            model.eval()

        if device_name == "cuda":
            torch.cuda.reset_peak_memory_stats()

        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            raise RuntimeError(f"Could not open source video: {video_path}")
        duration = float(source["duration_seconds"])
        try:
            with tempfile.TemporaryDirectory(prefix="biliflow-vlm-confirm-") as temporary:
                temporary_root = Path(temporary)
                for index, raw_interval in enumerate(intervals):
                    interval = copy.deepcopy(raw_interval)
                    frame_dir = temporary_root / f"interval-{index:05d}"
                    frame_dir.mkdir()
                    center = _timestamp(interval)
                    frames = performance.call("frame_extract", _sample_context_frames,
                        capture,
                        center_seconds=center,
                        duration_seconds=duration,
                        context_seconds=context_seconds,
                        frame_count=frame_count,
                        output_dir=frame_dir,
                    )
                    if not frames:
                        answer = "NO_FRAMES"
                        state = "UNCERTAIN"
                    else:
                        messages = [{
                            "role": "user",
                            "content": (
                                [{"type": "image", "path": str(frame)} for frame in frames]
                                + [{"type": "text", "text": VIOLENCE_CONFIRMATION_PROMPT}]
                            ),
                        }]
                        with performance.measure('model_step'):
                            inputs = processor.apply_chat_template(
                                messages,
                                add_generation_prompt=True,
                                tokenize=True,
                                return_dict=True,
                                return_tensors="pt",
                            ).to(model.device)
                            with torch.inference_mode():
                                generated = model.generate(
                                    **inputs, do_sample=False, max_new_tokens=8
                                )
                            answer = processor.decode(
                                generated[0][inputs["input_ids"].shape[-1]:],
                                skip_special_tokens=True,
                            ).strip()

                        state = _answer_state(answer)
                    answers[state] += 1
                    interval["vlm_confirmation"] = {
                        "state": state,
                        "answer": answer,
                        "center_seconds": round(center, 3),
                        "frames_sampled": len(frames),
                    }
                    if state == "REJECTED":
                        rejected.append(interval)
                    else:
                        retained.append(interval)
                    print(
                        f"{index + 1}/{len(intervals)} {center:.3f}s {state}: {answer}",
                        flush=True,
                    )
        finally:
            capture.release()
        if device_name == "cuda":
            peak_cuda_memory = int(torch.cuda.max_memory_allocated())

    payload = copy.deepcopy(source)
    payload["status"] = "REVIEW_REQUIRED" if retained else "COMPLETED"
    payload["created_at"] = datetime.now(timezone.utc).astimezone().isoformat()
    payload["intervals"] = retained
    payload["priority_counts"] = {
        "high": sum(str(item.get("priority", "")).casefold() == "high" for item in retained),
        "context": sum(str(item.get("priority", "")).casefold() != "high" for item in retained),
    }
    payload["retained_interval_thumbnail_count"] = sum(
        bool(item.get("strongest_frame")) for item in retained
    )
    payload["confirmation"] = {
        "method": "local_vlm_second_stage",
        "source_report": report_path.name,
        "model": model_manifest,
        "prompt": VIOLENCE_CONFIRMATION_PROMPT,
        "context_seconds": context_seconds,
        "frame_count": frame_count,
        "candidate_intervals": len(intervals),
        "retained_intervals": len(retained),
        "rejected_intervals": len(rejected),
        "answer_counts": answers,
        "rejected": [
            {
                "start_seconds": item.get("start_seconds"),
                "end_seconds": item.get("end_seconds"),
                "max_score": item.get("max_score"),
                "vlm_confirmation": item.get("vlm_confirmation"),
            }
            for item in rejected
        ],
    }
    payload.setdefault("metrics", {})["confirmation_elapsed_seconds"] = round(
        time.perf_counter() - started, 3
    )
    payload["metrics"]["confirmation_peak_cuda_memory_bytes"] = peak_cuda_memory
    payload["metrics"]["confirmation_performance"] = performance.snapshot()
    payload["safety"] = {
        "automatic_edit": False,
        "note": "VLM confirmation only filters review candidates; every retained interval still requires human review.",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary_output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary_output.replace(output_path)
    return payload
