"""Export encoder quality, size, speed and GPU memory: x264 against NVENC (perf plan A3).

Run as ``scripts/benchmark-export-steps.ps1 ... quality --name N [--src DIR]
[--preset p5 ...] [--cq 23 ...] [--vmaf] [--keep-outputs]``: the wrapper holds the
shared GPU slot and the final-render slot, and this holds <install>/state/final-render.lock.
In the bench root N (made by ``prepare``), with the code in DIR:

1. the real render_final_output forced onto x264 (render_settings video_encoder "libx264");
2. the default export, ``"auto"`` (no render_settings): the encoder it chose and why
   (the bench root's hardware profile or one trial encode), as its manifest records;
3. for each preset and -cq: the very FFmpeg command of (1) with export_encoder's NVENC
   arguments in place of x264's;
4. for every output: its size, its FFmpeg time, the GPU memory nvidia-smi reports
   (before, and the peak while it encodes) and SSIM (with --vmaf also VMAF) against the
   export graph's own frames, made again from the source in the same FFmpeg run.

Writes results/<stamp>-quality.json: numbers and encoder arguments only, no command
lines or paths. Every render runs in the bench root under <install>/temp.
"""
from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import re
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path
from typing import Any

import benchmark_export_steps as steps

SSIM_SUMMARY = re.compile(r"SSIM Y:([0-9.]+) .*?All:([0-9.]+)")
VRAM_INTERVAL_SECONDS = 0.5
VMAF_PERCENTILES = (1, 5)
# Scoring runs after every render is timed: two FFmpeg runs at once, each with half of the CPU threads.
QUALITY_WORKERS = 2
# VMAF on at most about this many frames (libvmaf n_subsample); SSIM always reads every frame.
VMAF_FRAME_TARGET = 80_000
# Checked on videos scored in full: the same numbers from every n-th frame only.
VMAF_SAMPLING_CHECK = 3


class GpuMemory:
    """The used memory (MiB) of GPU 0 before a block and at its peak, read with nvidia-smi."""

    def __init__(self) -> None:
        self.tool = shutil.which("nvidia-smi")
        self.before: int | None = None
        self.peak: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def read(self) -> int | None:
        if self.tool is None:
            return None
        try:
            result = subprocess.run(
                [self.tool, "--query-gpu=memory.used", "--format=csv,noheader,nounits", "-i", "0"],
                capture_output=True, text=True, timeout=10,
            )
            return int(result.stdout.strip()) if result.returncode == 0 else None
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None

    def _watch(self) -> None:
        while not self._stop.wait(VRAM_INTERVAL_SECONDS):
            value = self.read()
            if value is not None:
                self.peak = value if self.peak is None else max(self.peak, value)

    def __enter__(self) -> "GpuMemory":
        self.before = self.read()
        self.peak = self.before
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def record(self) -> dict:
        added = None if self.before is None or self.peak is None else self.peak - self.before
        return {"vram_before_mib": self.before, "vram_peak_mib": self.peak, "vram_added_mib": added}


def _encoder_command(command: list[str], arguments: list[str], output: Path) -> list[str]:
    """The export command with other video encoder arguments (between -map_metadata 0 and -pix_fmt)."""
    start = command.index("-map_metadata") + 2
    end = command.index("-pix_fmt")
    return command[:start] + list(arguments) + command[end:-1] + [str(output)]


def _option(command: list[str], name: str) -> int | None:
    return int(command[command.index(name) + 1]) if name in command else None


def _vmaf_subsample(copy: dict) -> int:
    """libvmaf n_subsample for a copy: 1 up to VMAF_FRAME_TARGET frames, then every n-th frame."""
    frames = float(copy["duration_seconds"]) * float(Fraction(str(copy.get("frame_rate") or "25")))
    return max(1, math.ceil(frames / VMAF_FRAME_TARGET))


def _frame_clock(frame_rate: str) -> str:
    """Timestamps from the frame number alone, so the two inputs pair frame n with frame n.

    Pairing by the inputs' own timestamps fails on a source whose timestamps are rounded
    (24000/1001 fps in 1/90000 ticks against the export's exact 1/24000): the comparison
    then meets the neighbouring frame, and a shot change scores near zero.
    """
    rate = Fraction(frame_rate)
    return f"settb=1/{rate.numerator},setpts=N*{rate.denominator}"


def _quality(
    ffmpeg: Path, source: Path, encoded: Path, graph: str, results: Path, tag: str, vmaf: bool,
    *, threads: int, subsample: int, frame_rate: str,
) -> dict:
    """SSIM (and VMAF) of ``encoded`` against the export graph's frames, made again from ``source``."""
    lines = [graph]
    if "[aout]" in graph:
        lines.append("[aout]anullsink")
    clock = _frame_clock(frame_rate)
    lines += [f"[1:v:0]{clock},format=yuv420p[dist]", f"[vout]{clock},format=yuv420p[ref]"]
    log = f"{tag}.vmaf.json"  # relative: FFmpeg runs in results/ (a drive colon would need escaping)
    if vmaf:
        lines += ["[dist]split[d1][d2]", "[ref]split[r1][r2]", "[d1][r1]ssim[s]",
                  f"[d2][r2]libvmaf=n_threads={threads}:n_subsample={subsample}"
                  f":log_fmt=json:log_path={log}[m]"]
        maps = ["-map", "[s]", "-map", "[m]"]
    else:
        lines.append("[dist][ref]ssim[s]")
        maps = ["-map", "[s]"]
    graph_file = results / f"{tag}.quality-graph.txt"
    graph_file.write_bytes(";".join(lines).encode("utf-8"))
    command = [str(ffmpeg), "-hide_banner", "-nostats", "-loglevel", "info", "-i", str(source),
               "-i", str(encoded), "-/filter_complex", str(graph_file), *maps, "-f", "null", "-"]
    try:
        seconds, result = steps._run_timed(command, cwd=results)
    finally:
        graph_file.unlink(missing_ok=True)
    match = SSIM_SUMMARY.search(result.stderr)
    if match is None:
        raise RuntimeError("FFmpeg printed no SSIM summary")
    facts = {"ssim_y": float(match.group(1)), "ssim_all": float(match.group(2)), "quality_seconds": round(seconds, 3)}
    if vmaf:
        log_path = results / log
        document = json.loads(log_path.read_text(encoding="utf-8"))
        log_path.unlink()
        pooled = document["pooled_metrics"]["vmaf"]
        # The hard scenes: one frame's minimum is noisy, the low percentiles are not.
        scores = [frame["metrics"]["vmaf"] for frame in document["frames"]]
        frames = sorted(scores)
        facts.update({"vmaf_mean": pooled["mean"], "vmaf_min": pooled["min"],
                      "vmaf_harmonic_mean": pooled["harmonic_mean"],
                      **{f"vmaf_p{point}": _percentile(frames, point) for point in VMAF_PERCENTILES}})
        if subsample == 1:
            # What sampling every n-th frame (as on the longest videos) would have given here.
            sampled = sorted(scores[::VMAF_SAMPLING_CHECK])
            facts[f"vmaf_every{VMAF_SAMPLING_CHECK}"] = {
                "mean": round(sum(sampled) / len(sampled), 6), "min": round(sampled[0], 6),
                **{f"p{point}": _percentile(sampled, point) for point in VMAF_PERCENTILES},
            }
    return facts


def _percentile(ordered: list[float], point: float) -> float:
    """The ``point`` percentile of sorted values (nearest rank)."""
    rank = max(1, -(-len(ordered) * point // 100))
    return round(ordered[int(rank) - 1], 3)


def _render_row(label: str, render: dict, memory: GpuMemory) -> dict:
    ffmpeg_seconds = sum(event["seconds"] for event in render["events"] if event["step"] == "ffmpeg_render")
    encoding = render.get("encoding") or {}
    return {
        "label": label, "encoder": encoding.get("encoder"), "video_arguments": encoding.get("video_arguments"),
        "encoder_choice": encoding.get("encoder_choice"), "fallback": encoding.get("fallback"),
        "bytes": render["output_bytes"], "ffmpeg_seconds": round(ffmpeg_seconds, 3),
        "video_seconds_per_second": round(render["output_duration_seconds"] / ffmpeg_seconds, 3),
        **memory.record(),
    }


def command_quality(args: argparse.Namespace) -> int:
    install_root = args.install_root.resolve(strict=True)
    bench = steps._bench_dir(install_root, args.name)
    prepared = steps._read_json(bench / "prepared.json")
    source = bench / prepared["copy"]["file"]
    ffmpeg, ffprobe = steps._tools(install_root)
    renderer = steps._import_biliflow(args.src)
    encoder = importlib.import_module("biliflow.export_encoder")
    tag = steps._stamp()
    results = bench / "results"
    results.mkdir(exist_ok=True)
    rows: list[dict] = []
    outputs: list[tuple[dict, Path]] = []
    subsample = _vmaf_subsample(prepared["copy"])
    with steps._install_render_lock(install_root):
        with GpuMemory() as memory:
            x264 = steps._render_once(renderer, bench, ffmpeg, ffprobe, f"{tag}-x264")
        rows.append(_render_row("x264 (forced)", x264, memory))
        outputs.append((rows[-1], x264["output_path"]))
        with GpuMemory() as memory:
            auto = steps._render_once(renderer, bench, ffmpeg, ffprobe, f"{tag}-auto", render_settings=None)
        rows.append(_render_row('default export ("auto")', auto, memory))
        outputs.append((rows[-1], auto["output_path"]))
        maxrate, target = _option(x264["command"], "-maxrate"), _option(x264["command"], "-b:v")
        base = steps._without_progress(steps._with_graph(x264["command"], x264["graph"], bench))
        for preset in args.preset:
            for cq in args.cq:
                arguments = encoder.video_arguments(
                    encoder.H264_NVENC, maxrate=maxrate, target_bitrate=target, cq=cq, preset=preset,
                )
                output = results / f"{tag}-nvenc-{preset}-cq{cq}.mp4"
                with GpuMemory() as memory:
                    seconds, _ = steps._run_timed(_encoder_command(base, arguments, output))
                row = {
                    "label": f"h264_nvenc {preset} cq {cq}", "encoder": encoder.H264_NVENC,
                    "video_arguments": arguments, "bytes": output.stat().st_size,
                    "ffmpeg_seconds": round(seconds, 3),
                    "video_seconds_per_second": round(x264["output_duration_seconds"] / seconds, 3),
                    **memory.record(),
                }
                rows.append(row)
                outputs.append((row, output))
        # Every render is timed by now: the scoring runs side by side.
        threads = max(1, (os.cpu_count() or 1) // QUALITY_WORKERS)

        def score(index: int) -> dict:
            row, path = outputs[index]
            row.update(_quality(ffmpeg, source, path, x264["graph"], results, f"{tag}-q{index}", args.vmaf,
                                threads=threads, subsample=subsample,
                                frame_rate=str(prepared["copy"].get("frame_rate") or "25/1")))
            print(json.dumps({key: row.get(key) for key in ("label", "bytes", "ffmpeg_seconds", "ssim_all",
                                                             "vmaf_mean", "vmaf_p1", "vram_added_mib")}), flush=True)
            return row

        with ThreadPoolExecutor(max_workers=QUALITY_WORKERS) as pool:
            list(pool.map(score, range(len(outputs))))
    if not args.keep_outputs:
        for _, path in outputs:
            path.unlink(missing_ok=True)
            path.with_suffix(path.suffix + ".manifest.json").unlink(missing_ok=True)
    report = {
        "schema_version": 1, "name": args.name, "tag": tag, "finished_at": steps._now(),
        "machine": steps._machine(ffmpeg), "code": steps._git_state(args.src.resolve()),
        "origin": prepared.get("origin"),
        "copy": {key: value for key, value in prepared["copy"].items() if key != "file"},
        "size_policy": prepared.get("size_policy"), "vmaf": args.vmaf,
        "vmaf_subsample": subsample if args.vmaf else None, "quality_workers": QUALITY_WORKERS,
        "rows": rows,
    }
    path = results / f"{tag}-quality.json"
    steps._write_json(path, report)
    print(f"results: {path}", flush=True)
    return 0
