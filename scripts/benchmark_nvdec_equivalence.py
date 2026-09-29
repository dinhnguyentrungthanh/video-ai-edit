"""Phase E1: prove NVDEC decoding yields the exact frames the scanners use today.

For each case the software command (exactly as textscan / visual_logo_scanner
build it) and the NVDEC variant (frames dropped on the GPU before download) are
run with FFmpeg's framemd5 muxer; every line (stream, dts, pts, duration, size,
MD5 of RGB24 pixels) must match. Software and NVDEC run concurrently per case
since they use different engines; results do not depend on timing.
Read-only for sources; writes only under reports/benchmarks.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from time import perf_counter

from biliflow.probe import duration_seconds, probe_video

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = ROOT / "tools/ffmpeg/bin/ffmpeg.exe"
FFPROBE = ROOT / "tools/ffmpeg/bin/ffprobe.exe"
NVDEC_INPUT = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]


def source(pattern):
    found = list((ROOT / "input").glob(pattern + ".mp4"))
    if len(found) != 1:
        raise ValueError(f"Expected one source for {pattern}")
    return found[0].resolve(strict=True)


def grids(src, start, duration):
    """(name, -ss text, -t text, fps, W, H) in each consumer's own formatting."""
    probe = probe_video(FFPROBE, src)
    width, height = next((s["width"], s["height"]) for s in probe["streams"] if s.get("codec_type") == "video")
    full = duration_seconds(probe)
    span = min(full - start, duration if duration is not None else full)
    ocr_h = max(2, round(height * 960 / width / 2) * 2)
    logo_h = max(2, round(height * 320 / width / 2) * 2)
    rows = [("ocr", str(float(start)), str(span), 1.0 / 3.0, 960, ocr_h),
            ("logo", f"{start:.3f}", f"{span:.3f}", 1.0 / 2.0, 320, logo_h)]
    if duration is None:  # boundary ranges exactly as scan_visual_logos derives them
        for label, left, right in (("boundary-start", 0.0, min(full, 30.0)), ("boundary-end", max(0.0, full - 30.0), full)):
            rows.append((label, f"{left:.3f}", f"{right - left:.3f}", 1.0 / 0.25, 320, logo_h))
    return rows


def command(src, ss, t, fps, width, height, nvdec, out):
    chain = f"fps={fps},scale={width}:{height}:flags=bilinear"
    if nvdec:
        chain = f"fps={fps},hwdownload,format=nv12,format=yuv420p,scale={width}:{height}:flags=bilinear"
    return [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", *(NVDEC_INPUT if nvdec else []),
            "-ss", ss, "-i", str(src), "-t", t, "-vf", chain, "-an", "-sn",
            "-pix_fmt", "rgb24", "-f", "framemd5", str(out)]


def lines(path):
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-full-film", action="store_true", help="only the bounded Conan/Troy excerpts")
    args = parser.parse_args()
    out = ROOT / "reports/benchmarks" / ("nvdec-equivalence-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=False)
    print(out, flush=True)
    cases = [] if args.skip_full_film else [("*Troy*", 0.0, None)]
    for pattern in ("*Movie 20*", "*Movie 21*"):
        full = duration_seconds(probe_video(FFPROBE, source(pattern)))
        cases += [(pattern, 0.0, 120.0), (pattern, round(full / 2), 120.0), (pattern, max(0.0, round(full) - 90), None)]
    cases.append(("*Troy*", 5000.0, 300.0))
    results = []
    for pattern, start, duration in cases:
        src = source(pattern)
        stat = (src.stat().st_size, src.stat().st_mtime_ns)
        for name, ss, t, fps, width, height in grids(src, start, duration):
            tag = f"{src.stem[:12]}-{int(start)}-{name}".replace(" ", "_")
            files = {kind: out / f"{tag}-{kind}.framemd5" for kind in ("cpu", "nvdec")}
            began = perf_counter()
            procs = {kind: subprocess.Popen(command(src, ss, t, fps, width, height, kind == "nvdec", files[kind]),
                                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                     for kind in files}
            errors = {}
            for kind, proc in procs.items():
                _, err = proc.communicate()
                errors[kind] = err.decode("utf-8", "replace")[-500:] if proc.returncode else ""
            a, b = (lines(files[k]) if files[k].exists() else [] for k in ("cpu", "nvdec"))
            first_diff = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), None)
            row = {"source": src.name, "start": start, "duration": duration, "grid": name, "ss": ss, "t": t,
                   "fps": fps, "size": [width, height], "frames": [len(a), len(b)],
                   "identical": bool(a) and a == b, "first_difference_index": first_diff,
                   "errors": errors, "seconds": perf_counter() - began,
                   "source_stat_unchanged": stat == (src.stat().st_size, src.stat().st_mtime_ns)}
            results.append(row)
            print(json.dumps({k: row[k] for k in ("source", "start", "grid", "frames", "identical", "seconds")},
                             ensure_ascii=False), flush=True)
    summary = {"scope": "Frame-by-frame framemd5 (pts, size, RGB24 MD5) of current software decode vs NVDEC "
                        "with GPU-side fps before hwdownload; the scanners themselves were not run",
               "ffmpeg": subprocess.check_output([str(FFMPEG), "-hide_banner", "-version"], text=True).splitlines()[0],
               "driver": subprocess.check_output(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
                                                 text=True).strip(),
               "cases": results, "frames_compared": sum(r["frames"][0] for r in results),
               "all_identical": all(r["identical"] and r["source_stat_unchanged"] for r in results)}
    (out / "equivalence.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("frames_compared", "all_identical", "driver")}, ensure_ascii=False))
    if not summary["all_identical"]:
        raise SystemExit("NVDEC frames differ from software decode; do not enable")


if __name__ == "__main__":
    main()
