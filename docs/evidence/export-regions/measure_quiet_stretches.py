"""Longest quiet stretch of a healthy export: no new progress block values and no new bytes read.

Usage: python measure_quiet_stretches.py <src_dir> <work_dir> <load_processes> [regions]
Makes a 1080p 25 fps 120 s clip with audio, renders it as the renderer would (x264
veryfast, size-limited rates, +faststart, -progress) with a plan of N cover+feather
regions and two CUTs (0-30 s at the start, 60-80 s), samples every 0.5 s, and prints
the longest stretch without progress, without reads, and without either.
<load_processes> CPU burners (x264 medium of a lavfi 1080p picture to null) run meanwhile.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import psutil  # noqa: E402
from biliflow.final_renderer import _final_filter_graph  # noqa: E402
from biliflow.render_watchdog import read_progress_tail  # noqa: E402

FF = os.environ["BILIFLOW_FFMPEG"]  # the install's tools/ffmpeg/bin/ffmpeg.exe
DURATION = 120.0


def clip(work: Path) -> Path:
    path = work / "src.mp4"
    if not path.exists():
        subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"testsrc2=size=1920x1080:rate=25:duration={DURATION}", "-f", "lavfi", "-i",
                        f"sine=frequency=440:sample_rate=48000:duration={DURATION}", "-c:v", "libx264",
                        "-preset", "ultrafast", "-g", "50", "-c:a", "aac", str(path)], check=True)
    return path


def plan(regions: int) -> list[dict]:
    blur = {"sigma": 40, "edge_feather_pixels": 2, "edge_feather_mode": "all_edges", "method": "delogo_blur_v1"}
    ops = [{"type": "cut", "id": "c0", "start_seconds": 0.0, "end_seconds": 30.0},
           {"type": "cut", "id": "c1", "start_seconds": 60.0, "end_seconds": 80.0}]
    for k in range(regions):
        start = (k * 1.7) % 100
        ops.append({"type": "blur", "id": f"b{k}", "start_seconds": start, "end_seconds": start + 15,
                    "region_source_pixels": {"x": 20 + 150 * (k % 12), "y": 800 + 50 * ((k // 12) % 5),
                                             "width": 120, "height": 40}, "blur": dict(blur)})
    return ops


def main() -> None:
    work = Path(sys.argv[2])
    work.mkdir(parents=True, exist_ok=True)
    load, regions = int(sys.argv[3]), int(sys.argv[4]) if len(sys.argv) > 4 else 60
    source = clip(work)
    graph, expected, wiring = _final_filter_graph(operations=plan(regions), duration=DURATION, has_audio=True,
                                                  frame_size=(1920, 1080), chroma_rows_divisor=2)
    graph_path, progress, output = work / "g.fg", work / "p.progress", work / "out.mp4"
    graph_path.write_text(graph, encoding="utf-8")
    progress.unlink(missing_ok=True)
    output.unlink(missing_ok=True)
    burners = [subprocess.Popen([FF, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                                 "testsrc2=size=1920x1080:rate=25", "-c:v", "libx264", "-preset", "medium",
                                 "-f", "null", "NUL"], stdin=subprocess.DEVNULL) for _ in range(load)]
    time.sleep(2 if load else 0)
    command = [FF, "-hide_banner", "-loglevel", "error", "-stats_period", "1", "-progress", str(progress),
               "-nostats", "-i", str(source), "-/filter_complex", str(graph_path), "-map", "[vout]",
               "-map", "[aout]", "-map_metadata", "0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-maxrate", "8000000", "-bufsize", "16000000", "-b:v", "6000000", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "384000", "-movflags", "+faststart", "-max_muxing_queue_size", "2048",
               str(output)]
    started = time.monotonic()
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stderr=subprocess.PIPE)
    handle = psutil.Process(process.pid)
    last_sig = last_read = None
    since_sig = since_read = since_any = started
    longest = {"progress": 0.0, "reads": 0.0, "either": 0.0}
    while process.poll() is None:
        time.sleep(0.5)
        now = time.monotonic()
        values = read_progress_tail(progress)
        sig = None if values is None else tuple(values.get(k) for k in ("frame", "out_time_us", "total_size"))
        try:
            reads = handle.io_counters().read_bytes
        except psutil.Error:
            reads = last_read
        moved_sig, moved_read = sig != last_sig, reads != last_read
        since_sig = now if moved_sig else since_sig
        since_read = now if moved_read else since_read
        since_any = now if (moved_sig or moved_read) else since_any
        longest["progress"] = max(longest["progress"], now - since_sig)
        longest["reads"] = max(longest["reads"], now - since_read)
        longest["either"] = max(longest["either"], now - since_any)
        last_sig, last_read = sig, reads
    elapsed = time.monotonic() - started
    for burner in burners:
        burner.kill()
    print(json.dumps({"load_processes": load, "regions": regions, "wiring": wiring, "returncode": process.returncode,
                      "stderr": process.stderr.read().decode("utf-8", "replace")[-300:],
                      "render_seconds": round(elapsed, 1), "output_seconds": expected,
                      "longest_quiet_seconds": {k: round(v, 1) for k, v in longest.items()}}))


if __name__ == "__main__":
    main()
