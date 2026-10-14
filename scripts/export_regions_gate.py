"""Gate of session H: exports of 1 to 10 blur regions stay byte for byte as before.

job 134 (2026-10-14) hung with 25 nested regions; blur_layout now wires a
segment whose nested graph would exceed NESTED_PATH_LIMIT paths in the shared
layout. Every plan that exported before (at most 10 regions) must still give
the same file. For each plan below (self-made lavfi clips, no film), both code
trees, the baseline's src and this checkout's, run in a root of their own under
<install>/temp/<name>:
- the export filter graph (build_final_filter_graph): the same text wherever
  this checkout keeps the nested layout;
- the real render_final_output, forced to x264 (NVENC output is not
  byte-reproducible): the same mp4 SHA-256 and the same framemd5 of its decoded
  video and audio.
One render at a time, at below-normal priority (a scan or an export of the
Control Center keeps its share of the CPU). Nothing outside <install>/temp/<name>
is written but the result file.

  python scripts/export_regions_gate.py --baseline-src DIR [--install DIR] [--name N] [--result FILE] [--keep]

--baseline-src is the baseline's src folder (git archive <commit> src | tar -x -C
<folder>). --install is the install folder whose tools/ffmpeg and temp it uses
(default: the one holding this checkout; a worktree under temp has a marker of its own).
Exit 0 only when every plan passes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import psutil

CHECKOUT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CHECKOUT / "src"))

from biliflow.install_paths import install_root  # noqa: E402

WIDTH, HEIGHT, SECONDS, FPS = 320, 180, 3.0, 25
RENDER_TIMEOUT_SECONDS = 3600
SIDE_PROGRAM = """
import json, sys
from pathlib import Path
import biliflow
from biliflow.final_renderer import build_final_filter_graph, render_final_output
job = json.loads(sys.argv[1])
plan = json.loads(Path(job["plan"]).read_text(encoding="utf-8"))
graph, _ = build_final_filter_graph(
    operations=plan["approved_operations"], duration=plan["source"]["duration_seconds"],
    has_audio=True, frame_size=tuple(job["frame_size"]), chroma_rows_divisor=2)
manifest = render_final_output(
    project_root=Path(job["root"]), plan_path=Path(job["plan"]), output_path=Path(job["output"]),
    ffmpeg_path=Path(job["ffmpeg"]), ffprobe_path=Path(job["ffprobe"]))
print(json.dumps({"graph": graph, "status": manifest["status"], "code": biliflow.__file__,
                  "filter_graph": manifest["encoding"].get("filter_graph")}))
"""


def box(x: int, y: int, width: int, height: int) -> dict:
    return {"x": x, "y": y, "width": width, "height": height}


def blur(start: float, end: float, region: dict | str, *, method: str = "delogo_blur_v1",
         feather: int = 2, sigma: float = 28, name: str) -> dict:
    return {
        "id": name, "type": "blur", "start_seconds": start, "end_seconds": end,
        "region_source_pixels": region,
        "blur": {"sigma": sigma, "edge_feather_pixels": feather, "edge_feather_mode": "all_edges",
                 "method": method},
    }


def cut(start: float, end: float, name: str) -> dict:
    return {"id": name, "type": "cut", "start_seconds": start, "end_seconds": end}


def cards(count: int, *, y: int = 140) -> list[dict]:
    """Subtitle-band cards as in job 134: neighbours overlap in place and time, some come back."""
    items = []
    for k in range(count):
        start = round(0.1 + (k * 0.31) % 2.4, 2)
        region = box(8 + 34 * k, y + 6 * (k % 2), 44, 22)
        items.append(blur(start, min(SECONDS, start + 0.9), region,
                          method="gblur" if k % 4 == 3 else "delogo_blur_v1", name=f"card-{k}"))
        if k % 3 == 0:
            items.append(blur(min(2.6, start + 1.6), min(SECONDS, start + 2.0), region, name=f"card-{k}-again"))
    return items


def chain(count: int, *, methods: tuple[str, ...] = ("delogo_blur_v1",)) -> list[dict]:
    """Each region overlaps the one before it, all on together."""
    return [blur(0.2 + 0.05 * k, 2.8, box(10 + 22 * k, 30 + 8 * (k % 3), 60, 40),
                 method=methods[k % len(methods)], name=f"chain-{k}") for k in range(count)]


STRIP = blur(0.0, SECONDS, box(0, 4, WIDTH, 18), name="strip")
LOGO = blur(0.0, SECONDS, box(220, 2, 90, 24), name="logo")

PLANS: dict[str, tuple[str, list[dict]]] = {
    "1-gblur-no-feather": ("yuv420p", [blur(0.5, 2.5, box(20, 20, 80, 40), method="gblur", feather=0, name="r")]),
    "1-cover-with-cut": ("yuv420p", [cut(1.0, 1.4, "c"), blur(0.0, SECONDS, box(200, 10, 100, 30), name="r")]),
    "2-overlapping-with-cut": ("yuv420p", [
        cut(1.8, 2.1, "c"), blur(0.0, SECONDS, box(10, 10, 120, 40), name="a"),
        blur(0.5, 2.6, box(60, 30, 90, 50), name="b")]),
    "3-full-frame-between": ("yuv420p", [
        STRIP, blur(1.0, 1.3, "FULL_FRAME", name="full"),
        blur(1.2, 2.5, box(100, 60, 80, 40), method="gblur", feather=4, name="r")]),
    "5-cover-nested-limit": ("yuv420p", [STRIP, LOGO, *cards(3)]),
    # 22 pixels apart: no region reads what another changes (the cover method reads 8 around).
    "6-cover-apart": ("yuv420p", [
        blur(0.2 + 0.3 * k, 2.8, box(6 + 52 * k, 70, 30, 30), name=f"apart-{k}") for k in range(6)]),
    # 8 pixels apart: each reads the edge of the one before.
    "6-cover-close": ("yuv420p", [
        blur(0.2 + 0.3 * k, 2.8, box(6 + 52 * k, 70, 44, 30), name=f"close-{k}") for k in range(6)]),
    "6-cover-chain": ("yuv420p", chain(6)),
    "9-mixed-two-cuts": ("yuv420p", [cut(0.0, 0.5, "open"), cut(2.0, 2.3, "mid"), STRIP, LOGO, *cards(6)]),
    "9-mixed-ten-bit": ("yuv420p10le", [cut(0.0, 0.5, "open"), cut(2.0, 2.3, "mid"), STRIP, LOGO, *cards(6)]),
    "10-chain-mixed-methods": ("yuv420p", chain(10, methods=("delogo_blur_v1", "delogo_blur_v1", "gblur"))),
    "10-job-134-shape": ("yuv420p", [cut(0.0, 0.6, "open"), STRIP, LOGO, *cards(7)]),
}


def region_count(operations: list[dict]) -> int:
    """Blurs as the renderer groups them: one region with several time ranges counts once."""
    keys = {json.dumps([item["region_source_pixels"], item["blur"]], sort_keys=True)
            for item in operations if item["type"] == "blur"}
    return len(keys)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_clip(ffmpeg: Path, target: Path, pixel_format: str) -> None:
    """Static noise over testsrc2: fine detail in every region, so a changed blur shows in the bytes."""
    subprocess.run(
        [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc2=s={WIDTH}x{HEIGHT}:r={FPS}:d={SECONDS:g},noise=alls=40:allf=u",
         "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:d={SECONDS:g}",
         "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-pix_fmt", pixel_format, "-c:a", "aac", str(target)],
        capture_output=True, timeout=300, check=True,
    )


def framemd5(ffmpeg: Path, video: Path) -> str:
    result = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-map", "0:v:0", "-map", "0:a:0?", "-f", "framemd5", "-"],
        capture_output=True, timeout=600, check=True,
    )
    return hashlib.sha256(result.stdout).hexdigest()


def run_side(side_src: Path, root: Path, clip: Path, operations: list[dict],
             ffmpeg: Path, ffprobe: Path) -> dict:
    """One code tree's graph and export of one plan, in a root of its own."""
    for folder in ("input", "work", "output", "state"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    source = root / "input" / clip.name
    shutil.copyfile(clip, source)
    plan = root / "work" / "plan.json"
    plan.write_text(json.dumps({
        "status": "READY_FOR_FINAL_RENDER", "final_export_allowed": True,
        "source": {"path": str(source), "duration_seconds": SECONDS, "sha256": sha256(source)},
        "approved_operations": operations,
        "render_settings": {"video_encoder": "libx264"},
    }), encoding="utf-8")
    output = root / "output" / "export.mp4"
    job = {"plan": str(plan), "root": str(root), "output": str(output), "ffmpeg": str(ffmpeg),
           "ffprobe": str(ffprobe), "frame_size": [WIDTH, HEIGHT]}
    # No BILIFLOW_* setting of the caller (an export lane, a test key) reaches either side.
    environment = {key: value for key, value in os.environ.items() if not key.startswith("BILIFLOW_")}
    environment.update({"PYTHONPATH": str(side_src), "TEMP": str(root), "TMP": str(root)})
    started = time.monotonic()
    # Run from the side's own root: `python -c` puts its working folder first on sys.path.
    result = subprocess.run([sys.executable, "-c", SIDE_PROGRAM, json.dumps(job)], env=environment, cwd=root,
                            capture_output=True, text=True, timeout=RENDER_TIMEOUT_SECONDS)
    seconds = round(time.monotonic() - started, 1)
    if result.returncode:
        return {"error": result.stderr.strip()[-1500:], "seconds": seconds}
    side = json.loads(result.stdout.strip().splitlines()[-1])
    if side_src not in Path(side.pop("code")).resolve().parents:
        return {"error": f"biliflow was not imported from {side_src}", "seconds": seconds}
    side.update(seconds=seconds, mp4_sha256=sha256(output), framemd5_sha256=framemd5(ffmpeg, output))
    return side


def gate_plan(name: str, pixel_format: str, operations: list[dict], *, baseline: Path, candidate: Path,
              bench: Path, clips: dict[str, Path], ffmpeg: Path, ffprobe: Path) -> dict:
    sides = {label: run_side(src, bench / name / label, clips[pixel_format], operations, ffmpeg, ffprobe)
             for label, src in (("baseline", baseline), ("candidate", candidate))}
    record = {"plan": name, "source_pixel_format": pixel_format, "regions": region_count(operations),
              "operations": len(operations)}
    errors = {label: side["error"] for label, side in sides.items() if "error" in side}
    if errors:
        return {**record, "passed": False, "errors": errors,
                "seconds": {label: side["seconds"] for label, side in sides.items()}}
    wiring = sides["candidate"]["filter_graph"] or {}
    same_graph = sides["baseline"]["graph"] == sides["candidate"]["graph"]
    record.update(
        candidate_filter_graph=wiring,
        graph_text_identical=same_graph,
        mp4_sha256={label: side["mp4_sha256"] for label, side in sides.items()},
        framemd5_sha256={label: side["framemd5_sha256"] for label, side in sides.items()},
        seconds={label: side["seconds"] for label, side in sides.items()},
    )
    checks = [
        sides["baseline"]["mp4_sha256"] == sides["candidate"]["mp4_sha256"],
        sides["baseline"]["framemd5_sha256"] == sides["candidate"]["framemd5_sha256"],
        wiring.get("regions_reading_older_picture") == 0,
        # The nested layout keeps the old graph character for character.
        same_graph or wiring.get("layout") != "nested",
    ]
    record["passed"] = all(checks)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baseline-src", type=Path, required=True)
    parser.add_argument("--install", type=Path, default=None)
    parser.add_argument("--name", default="h-regions-gate")
    parser.add_argument("--result", type=Path,
                        default=CHECKOUT / "docs" / "evidence" / "export-regions" / "gate-1-10-regions.json")
    parser.add_argument("--only", nargs="*", choices=list(PLANS), help="plan names (default: all)")
    parser.add_argument("--keep", action="store_true", help="keep the bench folder")
    args = parser.parse_args()
    psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)  # inherited by every FFmpeg below
    if not args.name or any(part in args.name for part in ("/", "\\", "..", ":")):
        raise SystemExit(f"Invalid bench name: {args.name!r}")
    install = (args.install or install_root()).resolve(strict=True)
    bench = (install / "temp" / args.name).resolve()
    if (install / "temp").resolve() not in bench.parents:
        raise SystemExit("The bench must stay inside <install>/temp")
    tools = install / "tools" / "ffmpeg" / "bin"
    ffmpeg, ffprobe = (tools / "ffmpeg.exe").resolve(strict=True), (tools / "ffprobe.exe").resolve(strict=True)
    baseline = args.baseline_src.resolve(strict=True)
    candidate = (CHECKOUT / "src").resolve(strict=True)
    if baseline == candidate:
        raise SystemExit("The baseline must be another code tree than this checkout's src")
    if bench.exists():
        raise SystemExit(f"{bench} exists (a run kept with --keep?): remove it first")
    names = args.only or list(PLANS)
    bench.mkdir(parents=True)
    records = []
    try:
        clips = {}
        for pixel_format in sorted({PLANS[name][0] for name in names}):
            clips[pixel_format] = bench / f"clip-{pixel_format}.mp4"
            make_clip(ffmpeg, clips[pixel_format], pixel_format)
        for name in names:
            pixel_format, operations = PLANS[name]
            record = gate_plan(name, pixel_format, operations, baseline=baseline, candidate=candidate,
                               bench=bench, clips=clips, ffmpeg=ffmpeg, ffprobe=ffprobe)
            records.append(record)
            print(json.dumps({key: record.get(key) for key in (
                "plan", "regions", "passed", "graph_text_identical", "candidate_filter_graph", "seconds",
                "errors")}), flush=True)
    finally:
        if not args.keep:
            shutil.rmtree(bench, ignore_errors=True)
    banner = subprocess.run([str(ffmpeg), "-version"], capture_output=True, text=True).stdout.split()
    version = banner[2].split("-")[0]  # the number only: the banner also names the build vendor's site
    commit = subprocess.run(["git", "-C", str(CHECKOUT), "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "-C", str(CHECKOUT), "status", "--porcelain", "src"],
                                capture_output=True, text=True).stdout.strip())
    result = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "candidate": {"commit": commit, "src_has_uncommitted_changes": dirty},
        "baseline": "git archive of the baseline commit's src (docs/evidence/export-regions/README.md)",
        "ffmpeg": version,
        "clip": f"lavfi testsrc2 + static noise, {WIDTH}x{HEIGHT}, {SECONDS:g} s, {FPS} fps, with audio",
        "encoder": "libx264 (render_settings video_encoder)",
        "plans": records,
        "passed": bool(records) and all(record["passed"] for record in records),
    }
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("passed" if result["passed"] else "FAILED", args.result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
