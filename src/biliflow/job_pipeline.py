from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DETECTOR_GROUPS: dict[str, dict[str, str]] = {
    "advertising": {
        "label": "Quảng cáo / logo",
        "description": "OCR, logo, watermark, banner và quảng bá đầu/cuối phim",
    },
    "adult": {
        "label": "18+",
        "description": "Nội dung người lớn hoặc tình dục",
    },
    "gore": {
        "label": "Máu me",
        "description": "Máu, thương tích và gore",
    },
    "violence": {
        "label": "Bạo lực",
        "description": "Hành vi đánh nhau hoặc bạo lực trực tiếp",
    },
}
DEFAULT_DETECTOR_GROUPS = tuple(DETECTOR_GROUPS)


def normalize_detector_groups(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if values is None or "all" in values:
        return DEFAULT_DETECTOR_GROUPS
    selected = tuple(
        key for key in DEFAULT_DETECTOR_GROUPS if key in set(values)
    )
    unknown = sorted(set(values) - set(DEFAULT_DETECTOR_GROUPS))
    if unknown:
        raise ValueError(f"Unknown detector group(s): {', '.join(unknown)}")
    if not selected:
        raise ValueError("Select at least one detector group")
    return selected


@dataclass(frozen=True)
class StageCommand:
    argv: tuple[str, ...]
    expected_artifacts: tuple[Path, ...]


@dataclass(frozen=True)
class PipelineStage:
    name: str
    job_state: str
    commands: tuple[StageCommand, ...]
    uses_gpu: bool = False


def safe_job_key(source: Path, sha256: str) -> str:
    stem = source.stem.casefold()
    stem = re.sub(r"[^\w]+", "-", stem, flags=re.UNICODE).strip("-")
    if not stem:
        stem = "video"
    return f"{stem[:48]}-{sha256[:8]}"


def load_profiles(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "config" / "processing_profiles.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        key: value for key, value in payload.items()
        if key != "schema_version" and isinstance(value, dict)
    }


def _powershell() -> str:
    value = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not value:
        raise RuntimeError("PowerShell is required to run BiliFlow stages")
    return value


def run_command(root: Path, command: str, *arguments: object) -> tuple[str, ...]:
    return (
        _powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(root / "scripts" / "run.ps1"), command,
        *(str(value) for value in arguments),
    )


def _report_paths(
    root: Path, job_key: str, style: str,
    detector_groups: tuple[str, ...],
) -> list[Path]:
    base = root / "reports" / "jobs" / job_key
    reports: list[Path] = []
    if style in {"animation", "mixed"}:
        reports += [
            base / "animation-safety" / category / "scan.json"
            for category in ("adult", "gore", "violence")
            if category in detector_groups
        ]
    if style in {"live_action", "mixed"}:
        if "adult" in detector_groups:
            reports.append(base / "adult" / "scan.json")
        if "gore" in detector_groups:
            reports.append(base / "gore" / "scan.json")
        if "violence" in detector_groups:
            reports.append(base / "violence" / "scan-confirmed.json")
    if "advertising" in detector_groups:
        reports += [
            base / "text" / "text-scan.json",
            base / "visual-logo" / "scan-localized.json",
        ]
    return reports


# Fast scan changes only how work is scheduled; each setting was validated on
# a full film to produce the same tracks, previews, routing windows and review
# items as the standard path (docs/SCAN_PERFORMANCE.md).
# Product default for jobs without a stored choice (user decision 2026-09-29,
# after the full-film A/B above). pipeline_stages itself stays explicit.
DEFAULT_FAST_SCAN = True
FAST_SCAN_OCR_BATCH_SIZE = 8
FAST_SCAN_OCR_FRAME_WINDOW = 4
FAST_SCAN_LOGO_ROUTING_WORKERS = 3


def normalize_fast_scan(value: object) -> bool:
    if type(value) is not bool:
        raise ValueError("Quét nhanh phải là bật (true) hoặc tắt (false)")
    return value


def normalize_ocr_batch_size(value: object) -> int:
    if type(value) is not int or value not in (1, 8):
        raise ValueError("OCR phải là chế độ chuẩn (1) hoặc tăng tốc thử nghiệm (8)")
    return value


def pipeline_stages(
    *, root: Path, job_key: str, source: Path, content_style: str, profile: str,
    source_sha256: str | None = None,
    detector_groups: list[str] | tuple[str, ...] | None = None,
    ocr_recognition_batch_size: int = 1,
    fast_scan: bool = False,
) -> list[PipelineStage]:
    ocr_recognition_batch_size = normalize_ocr_batch_size(ocr_recognition_batch_size)
    fast_scan = normalize_fast_scan(fast_scan)
    if fast_scan:
        text_speed_arguments: tuple[object, ...] = (
            "--recognition-batch-size", FAST_SCAN_OCR_BATCH_SIZE,
            "--recognition-frame-window", FAST_SCAN_OCR_FRAME_WINDOW,
        )
        logo_speed_arguments: tuple[object, ...] = (
            "--routing-workers", FAST_SCAN_LOGO_ROUTING_WORKERS,
        )
    else:
        text_speed_arguments = (
            ("--recognition-batch-size", ocr_recognition_batch_size)
            if ocr_recognition_batch_size != 1 else ()
        )
        logo_speed_arguments = ()
    root = root.resolve(strict=True)
    source = source.resolve(strict=True)
    if content_style not in {"animation", "live_action", "mixed"}:
        raise ValueError("Content style must be confirmed before processing")
    profiles = load_profiles(root)
    if profile not in profiles:
        raise ValueError(f"Unknown profile: {profile}")
    config = profiles[profile]
    selected_detectors = normalize_detector_groups(detector_groups)
    safety_selected = any(
        value in selected_detectors for value in ("adult", "gore", "violence")
    )
    base = root / "reports" / "jobs" / job_key
    stages: list[PipelineStage] = [
        PipelineStage("preflight", "PREFLIGHT", tuple(), uses_gpu=False)
    ]

    if content_style in {"animation", "mixed"} and safety_selected:
        target = base / "animation-safety"
        stages.append(PipelineStage(
            "animation_safety", "SCANNING_SAFETY",
            (StageCommand(
                run_command(
                    root, "scan-animation-safety", "--input", source,
                    "--report-dir", target, "--sample-fps",
                    config["animation_sample_fps"], "--device", "cuda",
                ),
                tuple(
                    target / category / "scan.json"
                    for category in ("adult", "gore", "violence")
                    if category in selected_detectors
                ),
            ),),
            uses_gpu=True,
        ))
    if content_style in {"live_action", "mixed"}:
        adult = base / "adult"
        gore = base / "gore"
        violence = base / "violence"
        if "adult" in selected_detectors:
            stages.append(
            PipelineStage(
                "adult", "SCANNING_SAFETY",
                (StageCommand(
                    run_command(
                        root, "scan", "--input", source, "--report-dir", adult,
                        "--sample-fps", config["adult_sample_fps"],
                        "--review-merge-gap-seconds",
                        config["adult_review_merge_gap_seconds"],
                        "--sequence-context-threshold",
                        config["adult_sequence_context_threshold"],
                        "--sequence-context-seconds",
                        config["adult_sequence_context_seconds"],
                        "--content-style", "live_action", "--device", "cuda",
                    ),
                    (adult / "scan.json",),
                ),), uses_gpu=True,
            ))
        shared_live_safety = (
            "gore" in selected_detectors and "violence" in selected_detectors
        )
        if shared_live_safety:
            stages.append(PipelineStage(
                "live_safety", "SCANNING_SAFETY",
                (StageCommand(
                    run_command(
                        root, "scan-live-safety", "--input", source,
                        "--report-dir", base,
                        "--gore-sample-fps", config["gore_sample_fps"],
                        "--violence-sample-fps", config["violence_sample_fps"],
                        "--device", "cuda",
                    ),
                    (gore / "scan.json", violence / "scan.json"),
                ),), uses_gpu=True,
            ))
        elif "gore" in selected_detectors:
            stages.append(
            PipelineStage(
                "gore", "SCANNING_SAFETY",
                (StageCommand(
                    run_command(
                        root, "scan-content", "--kind", "gore", "--input", source,
                        "--report-dir", gore, "--sample-fps",
                        config["gore_sample_fps"], "--content-style", "live_action",
                        "--device", "cuda",
                    ),
                    (gore / "scan.json",),
                ),), uses_gpu=True,
            ))
        if "violence" in selected_detectors and not shared_live_safety:
            stages.append(
            PipelineStage(
                "violence", "SCANNING_SAFETY",
                (StageCommand(
                    run_command(
                        root, "scan-content", "--kind", "violence", "--input", source,
                        "--report-dir", violence, "--sample-fps",
                        config["violence_sample_fps"], "--content-style", "live_action",
                        "--device", "cuda",
                    ),
                    (violence / "scan.json",),
                ),), uses_gpu=True,
            ))
        if "violence" in selected_detectors:
            stages.append(
            PipelineStage(
                "confirm_violence", "SCANNING_SAFETY",
                (StageCommand(
                    run_command(
                        root, "confirm-violence", "--report", violence / "scan.json",
                        "--output", violence / "scan-confirmed.json", "--device", "cuda",
                    ),
                    (violence / "scan-confirmed.json",),
                ),), uses_gpu=True,
            ))

    if "advertising" in selected_detectors:
        text = base / "text"
        stages.append(PipelineStage(
        "text", "SCANNING_TEXT",
        (StageCommand(
            run_command(
                root, "scan-text", "--input", source, "--report-dir", text,
                "--sample-every", config["text_sample_every"], "--device", "cuda",
                *text_speed_arguments,
            ),
            (text / "text-scan.json",),
        ),), uses_gpu=True,
        ))
        logo = base / "visual-logo"
        stages.append(PipelineStage(
        "visual_logo", "SCANNING_LOGO",
        (StageCommand(
            run_command(
                root, "scan-visual-logo", "--input", source, "--report-dir", logo,
                "--sample-every", config["logo_sample_every"],
                "--boundary-sample-every", config["logo_boundary_sample_every"],
                "--boundary-seconds", config["logo_boundary_seconds"],
                "--max-candidate-windows", config["logo_max_candidate_windows"],
                "--scene-change-threshold", config["logo_scene_change_threshold"],
                "--coverage-bucket-seconds", config["logo_coverage_bucket_seconds"],
                "--coverage-fallbacks-per-bucket", config["logo_coverage_fallbacks_per_bucket"],
                *(("--source-sha256", source_sha256) if source_sha256 else ()),
                "--device", "cuda",
                *logo_speed_arguments,
            ),
            (logo / "scan.json",),
        ),), uses_gpu=True,
        ))
        stages.append(PipelineStage(
        "localize_logo", "LOCALIZING_REGIONS",
        (
            StageCommand(
                run_command(
                    root, "localize-visual-logo", "--report", logo / "scan.json",
                    "--output", logo / "scan-florence.json", "--device", "cuda",
                ),
                (logo / "scan-florence.json",),
            ),
            StageCommand(
                run_command(
                    root, "augment-grounding-regions",
                    "--report", logo / "scan-florence.json",
                    "--output", logo / "scan-localized.json", "--device", "cuda",
                ),
                (logo / "scan-localized.json",),
            ),
        ), uses_gpu=True,
        ))
    queue = base / "review-queue.json"
    review_arguments: list[object] = []
    reports = _report_paths(
        root, job_key, content_style, selected_detectors,
    )
    for report in reports:
        review_arguments += ["--report", report]
    for detector in selected_detectors:
        review_arguments += ["--selected-detector", detector]
    review_arguments += ["--queue", queue]
    stages.append(PipelineStage(
        "build_review", "BUILDING_REVIEW",
        (StageCommand(
            run_command(root, "build-review", *review_arguments), (queue,),
        ),), uses_gpu=False,
    ))
    return stages


def validate_json_artifact(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Expected stage artifact is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    if payload.get("status") == "FAILED":
        raise RuntimeError(f"Stage artifact reports failure: {path}")
    return payload
