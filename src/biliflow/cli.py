from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from biliflow.brand_memory import rebuild_brand_memory
from biliflow.cleanup import cleanup_candidates
from biliflow.animation_safety_scanner import scan_animation_safety
from biliflow.animation_policy import DIRECT_VIOLENCE_LABELS
from biliflow.ad_candidate_pipeline import augment_grounding_regions, benchmark_ad_candidates
from biliflow.content_scanner import scan_content
from biliflow.image_benchmark import benchmark_images
from biliflow.license_policy import audit_project_models, ensure_model_allowed
from biliflow.final_renderer import approve_previews, render_final_output
from biliflow.scanner import scan_nsfw
from biliflow.review_workflow import (
    build_edit_plan,
    build_review_queue,
    record_review_decision,
    render_edit_previews,
    serve_review_ui,
)
from biliflow.storage import storage_status
from biliflow.textscan import scan_text
from biliflow.text_semantics import classify_text_report
from biliflow.violence_scanner import scan_violence
from biliflow.video_benchmark import benchmark_videos
from biliflow.vlm_confirmation import confirm_violence_report
from biliflow.visual_logo_scanner import scan_visual_logos
from biliflow.control_center import serve_control_center


def build_parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(prog="biliflow")
    parser.add_argument("--project-root", type=Path, default=root)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("storage", help="Show storage safety status")
    sub.add_parser("cleanup-preview", help="List cleanup candidates without deleting files")
    sub.add_parser("license-audit", help="Audit local models against the free commercial-safe policy")
    sub.add_parser(
        "rebuild-brand-memory",
        help="Rebuild local visual signatures from saved human review decisions",
    )

    scan = sub.add_parser("scan", help="Scan a video with the local NSFW baseline")
    scan.add_argument("--input", type=Path, required=True)
    scan.add_argument("--report-dir", type=Path, required=True)
    scan.add_argument("--model", type=Path, default=root / "models" / "nsfw_detection_2_nano")
    scan.add_argument("--ffmpeg", type=Path, default=root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe")
    scan.add_argument("--ffprobe", type=Path, default=root / "tools" / "ffmpeg" / "bin" / "ffprobe.exe")
    scan.add_argument("--sample-fps", type=float, default=2.0)
    scan.add_argument("--batch-size", type=int, default=8)
    scan.add_argument("--top-k", type=int, default=20)
    scan.add_argument("--threshold", type=float, default=0.95)
    scan.add_argument("--merge-gap-seconds", type=float, default=2.0)
    scan.add_argument("--padding-seconds", type=float, default=1.0)
    scan.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    scan.add_argument(
        "--content-style", choices=["live_action", "animation", "mixed", "unknown"], default="unknown"
    )
    scan.add_argument("--temporal-window-frames", type=int, default=5)
    scan.add_argument("--temporal-minimum-hits", type=int, default=3)

    content_scan = sub.add_parser(
        "scan-content", help="Scan a video with the local gore or violence classifier"
    )
    content_scan.add_argument("--kind", choices=["gore", "violence"], required=True)
    content_scan.add_argument("--input", type=Path, required=True)
    content_scan.add_argument("--report-dir", type=Path, required=True)
    content_scan.add_argument("--model", type=Path)
    content_scan.add_argument("--ffmpeg", type=Path, default=root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe")
    content_scan.add_argument("--ffprobe", type=Path, default=root / "tools" / "ffmpeg" / "bin" / "ffprobe.exe")
    content_scan.add_argument("--sample-fps", type=float, default=1.0)
    content_scan.add_argument("--batch-size", type=int, default=16)
    content_scan.add_argument("--top-k", type=int, default=20)
    content_scan.add_argument("--threshold", type=float)
    content_scan.add_argument("--high-threshold", type=float)
    content_scan.add_argument("--merge-gap-seconds", type=float, default=2.0)
    content_scan.add_argument("--review-merge-gap-seconds", type=float)
    content_scan.add_argument("--padding-seconds", type=float, default=1.0)
    content_scan.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    content_scan.add_argument(
        "--content-style", choices=["live_action", "animation"], default="live_action"
    )
    content_scan.add_argument("--temporal-window-frames", type=int, default=5)
    content_scan.add_argument("--temporal-minimum-hits", type=int, default=3)
    content_scan.add_argument("--clip-frames", type=int, default=16)
    content_scan.add_argument("--stride-frames", type=int, default=8)

    confirm_violence = sub.add_parser(
        "confirm-violence",
        help="Filter a violence scan with a local vision-language model before human review",
    )
    confirm_violence.add_argument("--report", type=Path, required=True)
    confirm_violence.add_argument("--output", type=Path, required=True)
    confirm_violence.add_argument(
        "--model", type=Path, default=root / "models" / "qwen2_vl_2b_instruct"
    )
    confirm_violence.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    confirm_violence.add_argument("--context-seconds", type=float, default=2.0)
    confirm_violence.add_argument("--frame-count", type=int, default=5)

    visual_logo = sub.add_parser(
        "scan-visual-logo",
        help="Find non-text brand idents, logos, and watermarks with local visual AI",
    )
    visual_logo.add_argument("--input", type=Path, required=True)
    visual_logo.add_argument("--report-dir", type=Path, required=True)
    visual_logo.add_argument(
        "--model", type=Path, default=root / "models" / "qwen2_vl_2b_instruct"
    )
    visual_logo.add_argument("--ffmpeg", type=Path, default=root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe")
    visual_logo.add_argument("--ffprobe", type=Path, default=root / "tools" / "ffmpeg" / "bin" / "ffprobe.exe")
    visual_logo.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    visual_logo.add_argument("--sample-every", type=float, default=2.0)
    visual_logo.add_argument("--boundary-sample-every", type=float, default=0.25)
    visual_logo.add_argument("--boundary-seconds", type=float, default=30.0)
    visual_logo.add_argument("--candidate-threshold", type=float, default=0.52)
    visual_logo.add_argument("--window-seconds", type=float, default=5.0)
    visual_logo.add_argument("--max-candidate-windows", type=int, default=80)
    visual_logo.add_argument("--source-sha256")
    visual_logo.add_argument("--scene-change-threshold", type=float, default=0.22)
    visual_logo.add_argument("--coverage-bucket-seconds", type=float, default=300.0)
    visual_logo.add_argument("--coverage-fallbacks-per-bucket", type=int, default=2)
    visual_logo.add_argument(
        "--exhaustive", action="store_true",
        help="Send every timeline window to local Qwen and sample the full video at least twice per second",
    )
    visual_logo.add_argument("--start-seconds", type=float, default=0.0)
    visual_logo.add_argument("--duration-seconds", type=float)

    animation_scan = sub.add_parser(
        "scan-animation-safety",
        help="Scan anime gore and direct violence in one shared model pass",
    )
    animation_scan.add_argument("--input", type=Path, required=True)
    animation_scan.add_argument("--report-dir", type=Path, required=True)
    animation_scan.add_argument(
        "--model", type=Path, default=root / "models" / "wd_vit_tagger_v3"
    )
    animation_scan.add_argument("--ffmpeg", type=Path, default=root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe")
    animation_scan.add_argument("--ffprobe", type=Path, default=root / "tools" / "ffmpeg" / "bin" / "ffprobe.exe")
    animation_scan.add_argument("--sample-fps", type=float, default=2.0)
    animation_scan.add_argument("--batch-size", type=int, default=8)
    animation_scan.add_argument("--top-k", type=int, default=20)
    animation_scan.add_argument("--adult-threshold", type=float, default=0.15)
    animation_scan.add_argument(
        "--adult-cooccurrence-threshold", type=float, default=0.05
    )
    animation_scan.add_argument(
        "--adult-minimum-cooccurring-labels", type=int, default=3
    )
    animation_scan.add_argument("--gore-high-threshold", type=float, default=0.20)
    animation_scan.add_argument("--gore-low-threshold", type=float, default=0.05)
    animation_scan.add_argument("--gore-context-threshold", type=float, default=0.02)
    animation_scan.add_argument("--gore-cooccurrence-threshold", type=float, default=0.03)
    animation_scan.add_argument("--violence-threshold", type=float, default=0.05)
    animation_scan.add_argument("--violence-high-threshold", type=float, default=0.20)
    animation_scan.add_argument("--danger-threshold", type=float, default=0.05)
    animation_scan.add_argument("--merge-gap-seconds", type=float, default=2.0)
    animation_scan.add_argument("--gore-merge-gap-seconds", type=float, default=6.0)
    animation_scan.add_argument("--padding-seconds", type=float, default=1.0)
    animation_scan.add_argument("--temporal-window-frames", type=int, default=5)
    animation_scan.add_argument("--temporal-minimum-hits", type=int, default=3)
    animation_scan.add_argument(
        "--gore-context-temporal-minimum-hits", type=int, default=4
    )
    animation_scan.add_argument("--device", choices=["cuda", "cpu"], default="cuda")

    text_scan = sub.add_parser("scan-text", help="Find and track text regions for ad review")
    text_scan.add_argument("--input", type=Path, required=True)
    text_scan.add_argument("--report-dir", type=Path, required=True)
    text_scan.add_argument("--model-dir", type=Path, default=root / "models" / "easyocr")
    text_scan.add_argument("--ffmpeg", type=Path, default=root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe")
    text_scan.add_argument("--ffprobe", type=Path, default=root / "tools" / "ffmpeg" / "bin" / "ffprobe.exe")
    text_scan.add_argument("--sample-every", type=float, default=3.0)
    text_scan.add_argument("--analysis-width", type=int, default=960)
    text_scan.add_argument("--minimum-confidence", type=float, default=0.35)
    text_scan.add_argument("--max-report-tracks", type=int, default=250)
    text_scan.add_argument("--start-seconds", type=float, default=0.0)
    text_scan.add_argument("--duration-seconds", type=float)
    text_scan.add_argument("--languages", nargs="+", default=["vi", "en"])
    text_scan.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    text_scan.add_argument(
        "--semantic-model", type=Path,
        default=root / "models" / "multilingual_minilm_text_semantics",
    )
    text_scan.add_argument(
        "--policy", type=Path, default=root / "config" / "text_review_policy.json"
    )
    text_scan.add_argument(
        "--semantic-seed", type=Path,
        default=root / "annotations" / "text_semantics_seed_v1.json",
    )
    text_scan.add_argument(
        "--skip-semantic-routing", action="store_true",
        help="Create a legacy OCR-only report without semantic classification",
    )

    text_classify = sub.add_parser(
        "classify-text", help="Classify OCR tracks as ads, subtitles, credits, or scene text"
    )
    text_classify.add_argument("--input-report", type=Path, required=True)
    text_classify.add_argument("--output-report", type=Path, required=True)
    text_classify.add_argument(
        "--model", type=Path,
        default=root / "models" / "multilingual_minilm_text_semantics",
    )
    text_classify.add_argument(
        "--policy", type=Path, default=root / "config" / "text_review_policy.json"
    )
    text_classify.add_argument(
        "--semantic-seed", type=Path,
        default=root / "annotations" / "text_semantics_seed_v1.json",
    )
    text_classify.add_argument("--device", choices=["cuda", "cpu"], default="cuda")

    ad_benchmark = sub.add_parser(
        "benchmark-ad-pipeline",
        help="Benchmark local SigLIP and GroundingDINO candidate routing without editing video",
    )
    ad_benchmark.add_argument(
        "--regression", type=Path,
        default=root / "annotations" / "review-regression-v1.json",
    )
    ad_benchmark.add_argument("--report-dir", type=Path, required=True)
    ad_benchmark.add_argument(
        "--grounding-model", type=Path,
        default=root / "models" / "grounding_dino_tiny",
    )
    ad_benchmark.add_argument(
        "--ranker-model", type=Path,
        default=root / "models" / "siglip_base_patch16_224",
    )
    ad_benchmark.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    ad_benchmark.add_argument("--batch-size", type=int, default=8)
    ad_benchmark.add_argument("--positive-limit", type=int, default=24)
    ad_benchmark.add_argument("--negative-limit", type=int, default=24)
    ad_benchmark.add_argument("--grounding-threshold", type=float, default=0.25)
    ad_benchmark.add_argument("--text-threshold", type=float, default=0.20)
    ad_benchmark.add_argument("--rank-margin-threshold", type=float, default=0.0)

    grounding_regions = sub.add_parser(
        "augment-grounding-regions",
        help="Add conservative GroundingDINO regions after semantic logo confirmation",
    )
    grounding_regions.add_argument("--report", type=Path, required=True)
    grounding_regions.add_argument("--output", type=Path, required=True)
    grounding_regions.add_argument(
        "--model", type=Path, default=root / "models" / "grounding_dino_tiny"
    )
    grounding_regions.add_argument(
        "--ffmpeg", type=Path,
        default=root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
    )
    grounding_regions.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    grounding_regions.add_argument("--threshold", type=float, default=0.25)
    grounding_regions.add_argument("--text-threshold", type=float, default=0.20)

    benchmark = sub.add_parser("benchmark-images", help="Benchmark a local image classifier")
    benchmark.add_argument("--manifest", type=Path, required=True)
    benchmark.add_argument("--report-dir", type=Path, required=True)
    benchmark.add_argument("--model", type=Path, required=True)
    benchmark.add_argument("--positive-label", action="append", required=True)
    benchmark.add_argument("--threshold", type=float, default=0.50)
    benchmark.add_argument("--batch-size", type=int, default=16)
    benchmark.add_argument("--device", choices=["cuda", "cpu"], default="cuda")

    video_benchmark = sub.add_parser("benchmark-videos", help="Benchmark a local video classifier")
    video_benchmark.add_argument("--manifest", type=Path, required=True)
    video_benchmark.add_argument("--report-dir", type=Path, required=True)
    video_benchmark.add_argument("--model", type=Path, required=True)
    video_benchmark.add_argument("--ffmpeg", type=Path, default=root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe")
    video_benchmark.add_argument("--threshold", type=float, default=0.50)
    video_benchmark.add_argument("--sample-fps", type=float, default=8.0)
    video_benchmark.add_argument("--clip-frames", type=int, default=16)
    video_benchmark.add_argument("--device", choices=["cuda", "cpu"], default="cuda")

    review = sub.add_parser("build-review", help="Build one confirmation queue from scan reports")
    review.add_argument("--report", type=Path, action="append", required=True)
    review.add_argument("--selected-detector", action="append", default=[])
    review.add_argument("--queue", type=Path, required=True)
    review.add_argument("--merge-gap-seconds", type=float, default=1.0)

    decide = sub.add_parser("review-decide", help="Record one human review decision")
    decide.add_argument("--queue", type=Path, required=True)
    decide.add_argument("--id", required=True)
    decide.add_argument("--decision", choices=["KEEP", "BLUR", "CUT", "NEEDS_MORE_CONTEXT"], required=True)
    decide.add_argument("--note")
    decide.add_argument("--region", type=int, nargs=4, metavar=("X", "Y", "WIDTH", "HEIGHT"))
    decide.add_argument("--full-frame", action="store_true")
    decide.add_argument("--start-seconds", type=float)
    decide.add_argument("--end-seconds", type=float)
    decide.add_argument(
        "--blur-edge-mode", choices=["all_edges", "vertical_only"],
        help="Feather all edges or only the top/bottom edges of a scrolling banner",
    )

    edit_plan = sub.add_parser("build-edit-plan", help="Create a locked plan from resolved decisions")
    edit_plan.add_argument("--queue", type=Path, required=True)
    edit_plan.add_argument("--output", type=Path, required=True)

    previews = sub.add_parser("render-previews", help="Render short clips for approved operations")
    previews.add_argument("--plan", type=Path, required=True)
    previews.add_argument("--output-dir", type=Path, required=True)
    previews.add_argument("--ffmpeg", type=Path, default=root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe")
    previews.add_argument("--ffprobe", type=Path, default=root / "tools" / "ffmpeg" / "bin" / "ffprobe.exe")
    previews.add_argument("--context-seconds", type=float, default=3.0)
    previews.add_argument(
        "--operation-id", action="append",
        help="Render only this operation; repeat for an explicitly sampled preview set",
    )

    review_ui = sub.add_parser("review-ui", help="Open an interactive local confirmation page")
    review_ui.add_argument("--queue", type=Path, required=True)
    review_ui.add_argument("--host", default="127.0.0.1")
    review_ui.add_argument("--port", type=int, default=8765)

    approve = sub.add_parser("approve-previews", help="Approve preview clips and unlock final render")
    approve.add_argument("--plan", type=Path, required=True)
    approve.add_argument("--manifest", type=Path, required=True)
    approve.add_argument("--actor", default="user")
    approve.add_argument(
        "--allow-sampled", action="store_true",
        help="Explicitly authorize an approved sampled preview to unlock final rendering",
    )

    final_render = sub.add_parser("render-final", help="Render and validate an approved final output")
    final_render.add_argument("--plan", type=Path, required=True)
    final_render.add_argument("--output", type=Path, required=True)
    final_render.add_argument("--ffmpeg", type=Path, default=root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe")
    final_render.add_argument("--ffprobe", type=Path, default=root / "tools" / "ffmpeg" / "bin" / "ffprobe.exe")
    final_render.add_argument("--max-output-bytes", type=int, default=3_500_000_000)
    final_render.add_argument("--target-output-bytes", type=int, default=3_300_000_000)
    final_render.add_argument(
        "--no-output-size-limit", action="store_true",
        help="Render with CRF quality control and no hard output-size ceiling",
    )

    control = sub.add_parser("control-center", help="Run the on-demand local dashboard")
    control.add_argument("--host", default="127.0.0.1")
    control.add_argument("--port", type=int, default=8765)
    control.add_argument("--stable-seconds", type=float, default=60.0)
    control.add_argument("--no-import-existing", action="store_true")
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    root = args.project_root.resolve(strict=True)
    if args.command == "storage":
        print(json.dumps(storage_status(root).as_dict(), indent=2))
        return 0
    if args.command == "cleanup-preview":
        candidates = cleanup_candidates(root)
        print(json.dumps({"mode": "DRY_RUN", "count": len(candidates), "files": candidates}, indent=2))
        return 0
    if args.command == "license-audit":
        print(json.dumps(audit_project_models(root), indent=2, ensure_ascii=False))
        return 0
    if args.command == "rebuild-brand-memory":
        print(json.dumps(rebuild_brand_memory(root), indent=2, ensure_ascii=False))
        return 0
    if args.command == "scan":
        ensure_model_allowed(root, args.model)
        payload = scan_nsfw(
            project_root=root,
            input_path=args.input,
            report_dir=args.report_dir,
            model_path=args.model,
            ffmpeg_path=args.ffmpeg,
            ffprobe_path=args.ffprobe,
            sample_fps=args.sample_fps,
            batch_size=args.batch_size,
            top_k_candidates=args.top_k,
            threshold=args.threshold,
            merge_gap_seconds=args.merge_gap_seconds,
            padding_seconds=args.padding_seconds,
            device_name=args.device,
            content_style=args.content_style,
            temporal_window_frames=args.temporal_window_frames,
            temporal_minimum_hits=args.temporal_minimum_hits,
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if args.command == "scan-text":
        ensure_model_allowed(root, args.model_dir)
        if not args.skip_semantic_routing:
            ensure_model_allowed(root, args.semantic_model)
        payload = scan_text(
            project_root=root,
            input_path=args.input,
            report_dir=args.report_dir,
            model_dir=args.model_dir,
            ffmpeg_path=args.ffmpeg,
            ffprobe_path=args.ffprobe,
            sample_every=args.sample_every,
            analysis_width=args.analysis_width,
            minimum_confidence=args.minimum_confidence,
            max_report_tracks=args.max_report_tracks,
            start_seconds=args.start_seconds,
            duration_seconds_limit=args.duration_seconds,
            languages=tuple(args.languages),
            device_name=args.device,
            semantic_model_dir=None if args.skip_semantic_routing else args.semantic_model,
            policy_path=None if args.skip_semantic_routing else args.policy,
            semantic_seed_path=None if args.skip_semantic_routing else args.semantic_seed,
        )
        print(json.dumps({
            "report": str((args.report_dir / "text-scan.json").resolve()),
            "frames_scanned": payload["frames_scanned"],
            "elapsed_seconds": payload["metrics"]["elapsed_seconds"],
            "speed_x_realtime": payload["metrics"]["video_seconds_per_processing_second"],
            "review_candidate_count": payload.get("review_candidate_count"),
            "routing_counts": payload.get("routing_counts", {}),
        }, indent=2, ensure_ascii=False))
        return 0
    if args.command == "classify-text":
        ensure_model_allowed(root, args.model)
        payload = classify_text_report(
            project_root=root,
            input_report=args.input_report,
            output_report=args.output_report,
            model_dir=args.model,
            policy_path=args.policy,
            seed_path=args.semantic_seed,
            device_name=args.device,
        )
        print(json.dumps({
            "output_report": str(args.output_report.resolve()),
            "routing_counts": payload["routing_counts"],
            "review_candidate_count": payload["review_candidate_count"],
        }, indent=2, ensure_ascii=False))
        return 0
    if args.command == "scan-animation-safety":
        ensure_model_allowed(root, args.model)
        payload = scan_animation_safety(
            project_root=root,
            input_path=args.input,
            report_dir=args.report_dir,
            model_path=args.model,
            ffmpeg_path=args.ffmpeg,
            ffprobe_path=args.ffprobe,
            sample_fps=args.sample_fps,
            batch_size=args.batch_size,
            top_k_candidates=args.top_k,
            gore_high_threshold=args.gore_high_threshold,
            gore_low_threshold=args.gore_low_threshold,
            gore_context_threshold=args.gore_context_threshold,
            gore_cooccurrence_threshold=args.gore_cooccurrence_threshold,
            violence_threshold=args.violence_threshold,
            violence_high_threshold=args.violence_high_threshold,
            danger_threshold=args.danger_threshold,
            merge_gap_seconds=args.merge_gap_seconds,
            padding_seconds=args.padding_seconds,
            temporal_window_frames=args.temporal_window_frames,
            temporal_minimum_hits=args.temporal_minimum_hits,
            gore_context_temporal_minimum_hits=(
                args.gore_context_temporal_minimum_hits
            ),
            device_name=args.device,
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if args.command == "scan-content":
        if args.kind == "violence":
            selected_model = args.model or root / "models" / "vit_base_violence_detection"
            manifest_path = selected_model / "manifest.json"
            model_manifest = (
                json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest_path.exists() else {}
            )
            video_backend = str(model_manifest.get("backend", "")).startswith(
                "transformers_video"
            )
        else:
            selected_model = None
            video_backend = False
        if args.kind == "violence" and args.content_style == "animation" and not video_backend:
            animation_model = args.model or root / "models" / "wd_vit_tagger_v3"
            ensure_model_allowed(
                root, animation_model
            )
            payload = scan_content(
                kind="violence_animation", project_root=root, input_path=args.input,
                report_dir=args.report_dir,
                model_path=animation_model,
                ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe,
                sample_fps=args.sample_fps if args.sample_fps != 1.0 else 2.0,
                batch_size=args.batch_size, top_k_candidates=args.top_k,
                threshold=args.threshold if args.threshold is not None else 0.05,
                merge_gap_seconds=args.merge_gap_seconds, padding_seconds=args.padding_seconds,
                device_name=args.device, content_style="animation",
                temporal_window_frames=args.temporal_window_frames,
                temporal_minimum_hits=args.temporal_minimum_hits,
                target_labels=DIRECT_VIOLENCE_LABELS,
            )
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        if args.kind == "violence":
            ensure_model_allowed(root, selected_model)
            payload = scan_violence(
                project_root=root,
                input_path=args.input,
                report_dir=args.report_dir,
                model_path=selected_model,
                ffmpeg_path=args.ffmpeg,
                ffprobe_path=args.ffprobe,
                sample_fps=args.sample_fps if args.sample_fps != 1.0 else 8.0,
                clip_frames=args.clip_frames,
                stride_frames=args.stride_frames,
                top_k_candidates=args.top_k,
                threshold=args.threshold if args.threshold is not None else (
                    0.61 if args.content_style == "animation" else (
                        0.28 if model_manifest.get("backend") == "timm_frame_video" else 0.50
                    )
                ),
                high_threshold=args.high_threshold if args.high_threshold is not None else (
                    0.90 if args.content_style == "animation" else None
                ),
                merge_gap_seconds=args.merge_gap_seconds,
                review_merge_gap_seconds=(
                    args.review_merge_gap_seconds
                    if args.review_merge_gap_seconds is not None
                    else (8.0 if args.content_style == "animation" else 0.0)
                ),
                padding_seconds=args.padding_seconds,
                device_name=args.device,
                content_style=args.content_style,
            )
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        if args.kind == "gore" and args.content_style == "animation":
            default_model = root / "models" / "wd_vit_tagger_v3"
            default_threshold = 0.05
        else:
            default_model = root / "models" / "image_safety_classifier_m"
            default_threshold = 0.50
        ensure_model_allowed(root, args.model or default_model)
        payload = scan_content(
            kind=args.kind,
            project_root=root,
            input_path=args.input,
            report_dir=args.report_dir,
            model_path=args.model or default_model,
            ffmpeg_path=args.ffmpeg,
            ffprobe_path=args.ffprobe,
            sample_fps=args.sample_fps,
            batch_size=args.batch_size,
            top_k_candidates=args.top_k,
            threshold=args.threshold if args.threshold is not None else default_threshold,
            merge_gap_seconds=args.merge_gap_seconds,
            padding_seconds=args.padding_seconds,
            device_name=args.device,
            content_style=args.content_style,
            temporal_window_frames=args.temporal_window_frames,
            temporal_minimum_hits=args.temporal_minimum_hits,
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if args.command == "benchmark-ad-pipeline":
        ensure_model_allowed(root, args.grounding_model)
        ensure_model_allowed(root, args.ranker_model)
        payload = benchmark_ad_candidates(
            project_root=root,
            regression_path=args.regression,
            report_dir=args.report_dir,
            grounding_model_path=args.grounding_model,
            ranker_model_path=args.ranker_model,
            device_name=args.device,
            batch_size=args.batch_size,
            positive_limit=args.positive_limit,
            negative_limit=args.negative_limit,
            grounding_threshold=args.grounding_threshold,
            text_threshold=args.text_threshold,
            rank_margin_threshold=args.rank_margin_threshold,
        )
        print(json.dumps({
            "report": str((args.report_dir / "benchmark.json").resolve()),
            "production_enabled": payload["production_enabled"],
            "sample": payload["sample"],
            "metrics": payload["metrics"],
            "acceptance_gate": payload["acceptance_gate"],
        }, indent=2, ensure_ascii=False))
        return 0
    if args.command == "augment-grounding-regions":
        ensure_model_allowed(root, args.model)
        payload = augment_grounding_regions(
            project_root=root,
            report_path=args.report,
            output_path=args.output,
            model_path=args.model,
            ffmpeg_path=args.ffmpeg,
            device_name=args.device,
            threshold=args.threshold,
            text_threshold=args.text_threshold,
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if args.command == "benchmark-images":
        ensure_model_allowed(root, args.model)
        payload = benchmark_images(
            project_root=root,
            manifest_path=args.manifest,
            report_dir=args.report_dir,
            model_path=args.model,
            positive_labels=tuple(args.positive_label),
            threshold=args.threshold,
            batch_size=args.batch_size,
            device_name=args.device,
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if args.command == "confirm-violence":
        ensure_model_allowed(root, args.model)
        payload = confirm_violence_report(
            project_root=root,
            report_path=args.report,
            output_path=args.output,
            model_path=args.model,
            device_name=args.device,
            context_seconds=args.context_seconds,
            frame_count=args.frame_count,
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if args.command == "scan-visual-logo":
        ensure_model_allowed(root, args.model)
        payload = scan_visual_logos(
            project_root=root, input_path=args.input, report_dir=args.report_dir,
            model_path=args.model, ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe,
            device_name=args.device, sample_every=args.sample_every,
            boundary_sample_every=args.boundary_sample_every,
            boundary_seconds=args.boundary_seconds,
            candidate_threshold=args.candidate_threshold,
            window_seconds=args.window_seconds,
            max_candidate_windows=args.max_candidate_windows,
            start_seconds=args.start_seconds,
            duration_seconds_limit=args.duration_seconds,
            exhaustive=args.exhaustive,
            source_sha256=args.source_sha256,
            scene_change_threshold=args.scene_change_threshold,
            coverage_bucket_seconds=args.coverage_bucket_seconds,
            coverage_fallbacks_per_bucket=args.coverage_fallbacks_per_bucket,
        )
        print(json.dumps({
            "report": str((args.report_dir / "scan.json").resolve()),
            "frames_scanned": payload["frames_scanned"],
            "candidate_windows": payload["candidate_windows"],
            "retained_intervals": len(payload["intervals"]),
            "answer_counts": payload["answer_counts"],
            "metrics": payload["metrics"],
        }, indent=2, ensure_ascii=False))
        return 0
    if args.command == "benchmark-videos":
        ensure_model_allowed(root, args.model)
        payload = benchmark_videos(
            project_root=root, manifest_path=args.manifest, report_dir=args.report_dir,
            model_path=args.model, ffmpeg_path=args.ffmpeg, threshold=args.threshold,
            sample_fps=args.sample_fps, clip_frames=args.clip_frames, device_name=args.device,
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if args.command == "build-review":
        payload = build_review_queue(
            project_root=root, report_paths=args.report, queue_path=args.queue,
            merge_gap_seconds=args.merge_gap_seconds,
            selected_detectors=args.selected_detector or None,
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if args.command == "review-decide":
        payload = record_review_decision(
            project_root=root, queue_path=args.queue, item_id=args.id,
            decision=args.decision, note=args.note,
            region=tuple(args.region) if args.region else None,
            full_frame=args.full_frame,
            start_seconds=args.start_seconds, end_seconds=args.end_seconds,
            blur_edge_mode=args.blur_edge_mode,
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if args.command == "build-edit-plan":
        payload = build_edit_plan(
            project_root=root, queue_path=args.queue, plan_path=args.output,
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if args.command == "render-previews":
        payload = render_edit_previews(
            project_root=root, plan_path=args.plan, output_dir=args.output_dir,
            ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe,
            context_seconds=args.context_seconds,
            operation_ids=tuple(args.operation_id) if args.operation_id else None,
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if args.command == "review-ui":
        serve_review_ui(
            project_root=root, queue_path=args.queue, host=args.host, port=args.port,
        )
        return 0
    if args.command == "approve-previews":
        plan, manifest = approve_previews(
            project_root=root, plan_path=args.plan,
            preview_manifest_path=args.manifest, actor=args.actor,
            allow_sampled=args.allow_sampled,
        )
        print(json.dumps({"plan": plan, "preview_manifest": manifest}, indent=2, ensure_ascii=False))
        return 0
    if args.command == "render-final":
        maximum_output_bytes = None if args.no_output_size_limit else args.max_output_bytes
        target_output_bytes = None if args.no_output_size_limit else args.target_output_bytes
        payload = render_final_output(
            project_root=root, plan_path=args.plan, output_path=args.output,
            ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe,
            max_output_bytes=maximum_output_bytes,
            target_output_bytes=target_output_bytes,
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if args.command == "control-center":
        serve_control_center(
            project_root=root, host=args.host, port=args.port,
            stable_seconds=args.stable_seconds,
            import_existing=not args.no_import_existing,
        )
        return 0
    raise AssertionError("unreachable")
