"""Export step timings and the blur-gating gate (docs/PERF_INSTALL_PLAN.md, A0 and A2).

Everything runs in a bench root under <install>/temp/perf-export/<name>. The source
is copied there (a whole-file copy, or a stream-copied segment); the install's
input/, output/ and state/control-center.sqlite3 are only read: the job's export
manifest gives the approved operations it was rendered with. Results name jobs only
by id: no titles, file names or paths. Run it through
scripts/benchmark-export-steps.ps1, which holds the shared GPU slot and the
final-render slot; measure and gate also hold <install>/state/final-render.lock.

  prepare  --name N --job J [--segment START SECONDS] [--ops-from-job M]
           [--synthetic WxH:SECONDS:FPS]
           Copy job J's source (or cut a segment of it, or make a synthetic clip)
           into the bench root and write work/plan.template.json from job J's
           approved operations clipped to the copy, or from job M's operations
           fitted to it (--ops-from-job: whole-length blurs stay whole-length,
           short ones keep their distance from the start or the end, regions
           scale with the frame width).
  measure  --name N [--src DIR] [--steps ...]
           Time each export step with the code in DIR (default: this checkout's
           src). "render" is the real render_final_output with its FFmpeg, ffprobe
           and SHA-256 calls timed one by one; the other steps reuse the exact
           FFmpeg command it ran.
  gate     --name N --baseline-src DIR --candidate-src DIR [--framemd5-only]
           For both code trees: framemd5 of [vout] (and [aout]) of the export
           filter graph, then the real render_final_output mp4. Exit 0 only when
           both framemd5 files and both mp4 SHA-256 are identical.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

CHECKOUT = Path(__file__).resolve().parents[1]
STEPS = (
    "render", "filters_null", "encode_only", "decode_source", "faststart",
    "output_sha256", "cli_import", "variants",
)
DEFAULT_STEPS = (
    "render", "filters_null", "encode_only", "decode_source", "faststart",
    "output_sha256", "cli_import",
)
VARIANTS = ("no_blur", "long_only", "short_only", "no_feather", "gblur_method")
# A blur covering at least this share of the source counts as whole-length.
LONG_BLUR_SHARE = 0.9
CHUNK = 1024 * 1024


# --------------------------------------------------------------------------- helpers

def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _tools(install_root: Path) -> tuple[Path, Path]:
    tools = install_root / "tools" / "ffmpeg" / "bin"
    return (tools / "ffmpeg.exe").resolve(strict=True), (tools / "ffprobe.exe").resolve(strict=True)


def _bench_dir(install_root: Path, name: str) -> Path:
    if not name or any(part in name for part in ("/", "\\", "..", ":")):
        raise SystemExit(f"Invalid bench name: {name!r}")
    temp = (install_root / "temp").resolve(strict=True)
    bench = (temp / "perf-export" / name).resolve()
    if temp not in bench.parents:
        raise SystemExit("The bench root must stay inside <install>/temp")
    return bench


def _run_timed(command: list[str], **kwargs: Any) -> tuple[float, subprocess.CompletedProcess]:
    started = time.perf_counter()
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    elapsed = time.perf_counter() - started
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {result.stderr.strip()[-2000:]}")
    return elapsed, result


def _import_biliflow(src: Path):
    """Import biliflow from ``src`` (a checkout's src folder) in this process."""
    src = src.resolve(strict=True)
    sys.path.insert(0, str(src))
    for module in [name for name in sys.modules if name == "biliflow" or name.startswith("biliflow.")]:
        del sys.modules[module]
    renderer = importlib.import_module("biliflow.final_renderer")
    if Path(renderer.__file__).resolve().parents[1] != src:
        raise SystemExit(f"biliflow was imported from {renderer.__file__}, not from {src}")
    return renderer


def _git_state(src: Path) -> dict:
    def git(*args: str) -> str:
        result = subprocess.run(["git", "-C", str(src), *args], capture_output=True, text=True)
        return result.stdout.strip()
    return {
        "head": git("rev-parse", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty_files": [line[3:] for line in git("status", "--porcelain", "--", ".").splitlines()],
    }


def _machine(ffmpeg: Path) -> dict:
    version = subprocess.run([str(ffmpeg), "-hide_banner", "-version"], capture_output=True, text=True)
    return {
        "cpu": platform.processor(), "logical_cpus": os.cpu_count(),
        "platform": platform.platform(), "python": platform.python_version(),
        "ffmpeg": version.stdout.splitlines()[0] if version.stdout else None,
    }


@contextlib.contextmanager
def _install_render_lock(install_root: Path) -> Iterator[None]:
    """Hold <install>/state/final-render.lock so no real export overlaps a measurement."""
    lock = importlib.import_module("biliflow.resource_lock")
    print("waiting for <install>/state/final-render.lock ...", flush=True)
    with lock.project_resource_lock(install_root, "final-render"):
        print("final-render lock held", flush=True)
        yield


# --------------------------------------------------------------------------- prepare

def _job_row(install_root: Path, job_id: int) -> tuple[Path, float, Any]:
    """(source path, duration, render setting) of a job; read-only."""
    database = install_root / "state" / "control-center.sqlite3"
    with contextlib.closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        row = connection.execute("SELECT source_path,duration_seconds FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise SystemExit(f"Unknown job: {job_id}")
        render = connection.execute(
            "SELECT value_json FROM settings WHERE key=?", (f"render:{job_id}",)
        ).fetchone()
    return Path(row[0]), float(row[1]), json.loads(render[0]) if render else None


def _job_operations(install_root: Path, job_id: int) -> tuple[Path, list[dict], dict]:
    """(source path, approved operations, facts) of a job's completed export; read-only."""
    source, duration, render = _job_row(install_root, job_id)
    if not render:
        raise SystemExit(f"Job {job_id} has no export")
    output = install_root / render["output"]
    manifest = _read_json(output.with_suffix(output.suffix + ".manifest.json"))
    if manifest.get("status") != "COMPLETED":
        raise SystemExit(f"Job {job_id} has no completed export manifest")
    facts = {
        "job_id": job_id, "source_duration_seconds": duration,
        "export_elapsed_seconds": manifest["encoding"].get("elapsed_seconds"),
        "export_decode_validation_seconds": manifest["encoding"].get("full_decode_validation_seconds"),
        "export_video_seconds_per_processing_second": manifest["encoding"].get("video_seconds_per_processing_second"),
    }
    return source, list(manifest.get("operations") or []), facts


def _probe(ffprobe: Path, path: Path) -> dict:
    result = subprocess.run(
        [str(ffprobe), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    return json.loads(result.stdout)


def _stream_facts(probe: dict) -> dict:
    video = next(item for item in probe["streams"] if item.get("codec_type") == "video")
    audio = next((item for item in probe["streams"] if item.get("codec_type") == "audio"), None)
    return {
        "duration_seconds": float(probe["format"]["duration"]),
        "bytes": int(probe["format"]["size"]),
        "width": int(video["width"]), "height": int(video["height"]),
        "video_codec": video.get("codec_name"), "pix_fmt": video.get("pix_fmt"),
        "frame_rate": video.get("r_frame_rate"), "video_bit_rate": video.get("bit_rate"),
        "audio_codec": audio.get("codec_name") if audio else None,
    }


def _clip_operations(operations: list[dict], start: float, duration: float) -> list[dict]:
    """The operations of [start, start + duration) of a source, on the segment's clock."""
    clipped = []
    for item in operations:
        new_start = max(0.0, float(item["start_seconds"]) - start)
        new_end = min(duration, float(item["end_seconds"]) - start)
        if new_end - new_start > 1e-3:
            clipped.append({**item, "start_seconds": round(new_start, 6), "end_seconds": round(new_end, 6)})
    return clipped


def _fit_operations(
    operations: list[dict], template_duration: float, template_width: int,
    duration: float, width: int, height: int, cover_sigma: Callable[[dict], int],
) -> list[dict]:
    """Job M's operations moved onto another video (see --ops-from-job)."""
    scale = width / template_width
    fitted = []
    for item in operations:
        start, end = float(item["start_seconds"]), float(item["end_seconds"])
        if end - start >= LONG_BLUR_SHARE * template_duration:
            start, end = 0.0, duration
        elif (start + end) / 2 > template_duration / 2:
            start, end = start + duration - template_duration, end + duration - template_duration
        start, end = max(0.0, start), min(duration, end)
        if end - start <= 1e-3:
            continue
        copy = {**item, "start_seconds": round(start, 6), "end_seconds": round(end, 6)}
        region = item.get("region_source_pixels")
        if isinstance(region, dict):
            region_width = min(width, max(2, round(int(region["width"]) * scale)))
            region_height = min(height, max(2, round(int(region["height"]) * scale)))
            copy["region_source_pixels"] = {
                "x": min(width - region_width, round(int(region["x"]) * scale)),
                "y": min(height - region_height, round(int(region["y"]) * scale)),
                "width": region_width, "height": region_height,
            }
            blur = dict(item.get("blur") or {})
            if blur.get("method") == "delogo_blur_v1":
                blur["sigma"] = cover_sigma(copy["region_source_pixels"])
            copy["blur"] = blur
        fitted.append(copy)
    return fitted


def _operation_summary(operations: list[dict]) -> list[dict]:
    summary = []
    for item in operations:
        region = item.get("region_source_pixels")
        blur = item.get("blur") or {}
        summary.append({
            "type": item["type"],
            "start_seconds": round(float(item["start_seconds"]), 3),
            "end_seconds": round(float(item["end_seconds"]), 3),
            "region": region if not isinstance(region, dict) else {
                key: int(region[key]) for key in ("x", "y", "width", "height")
            },
            "blur": {key: blur.get(key) for key in ("sigma", "edge_feather_pixels", "edge_feather_mode", "method")},
        })
    return summary


def command_prepare(args: argparse.Namespace) -> int:
    install_root = args.install_root.resolve(strict=True)
    bench = _bench_dir(install_root, args.name)
    if bench.exists():
        raise SystemExit(f"Bench root already exists (remove it first): {bench}")
    ffmpeg, ffprobe = _tools(install_root)
    renderer = _import_biliflow(args.src)
    cover_sigma = importlib.import_module("biliflow.blur_filter").cover_sigma
    for folder in ("input", "work", "output", "state", "results"):
        (bench / folder).mkdir(parents=True)
    if args.ops_from_job is None:
        source, operations, job_facts = _job_operations(install_root, args.job)
    else:
        source, operations, job_facts = _job_row(install_root, args.job)[0], [], None
    origin: dict[str, Any] = {"job_id": args.job, "segment": None, "synthetic": None}
    if args.synthetic:
        size, seconds, fps = args.synthetic.split(":")
        copy = bench / "input" / "source.mp4"
        subprocess.run([
            str(ffmpeg), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc2=s={size}:r={fps}:d={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:d={seconds}",
            "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", str(copy),
        ], check=True)
        origin["synthetic"] = args.synthetic
    elif args.segment:
        start, seconds = (float(value) for value in args.segment)
        copy = bench / "input" / f"source{source.suffix.lower()}"
        subprocess.run([
            str(ffmpeg), "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(source),
            "-t", f"{seconds:.3f}", "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
            "-avoid_negative_ts", "make_zero", str(copy),
        ], check=True)
        origin["segment"] = [start, seconds]
    else:
        copy = bench / "input" / f"source{source.suffix.lower()}"
        shutil.copyfile(source, copy)
    facts = _stream_facts(_probe(ffprobe, copy))
    duration = facts["duration_seconds"]
    if args.ops_from_job is not None:
        template_source, template_operations, template_facts = _job_operations(install_root, args.ops_from_job)
        template_width = _stream_facts(_probe(ffprobe, template_source))["width"]
        operations = _fit_operations(
            template_operations, template_facts["source_duration_seconds"], template_width,
            duration, facts["width"], facts["height"], cover_sigma,
        )
        origin["ops_from_job"] = args.ops_from_job
    elif args.segment:
        operations = _clip_operations(operations, origin["segment"][0], duration)
    # A whole-file copy keeps the job's approved operations exactly as exported.
    plan = {
        "status": "READY_FOR_FINAL_RENDER", "final_export_allowed": True,
        "source": {"path": str(copy), "sha256": _sha256(copy), "duration_seconds": duration},
        "approved_operations": operations,
    }
    _write_json(bench / "work" / "plan.template.json", plan)
    prepared = {
        "schema_version": 1, "created_at": _now(), "origin": origin,
        "source_job_export": job_facts if not args.synthetic and not args.segment else None,
        "copy": {**facts, "file": copy.relative_to(bench).as_posix()},
        "operations": _operation_summary(operations),
        "graph_frame_size": [facts["width"], facts["height"]],
        "renderer_module": Path(renderer.__file__).relative_to(args.src.resolve().parent).as_posix(),
    }
    _write_json(bench / "prepared.json", prepared)
    print(json.dumps(prepared, ensure_ascii=False, indent=2))
    return 0


# --------------------------------------------------------------------------- instrumented render

class RenderProbe:
    """Times every FFmpeg/ffprobe process, probe and SHA-256 of one render_final_output call."""

    def __init__(self, renderer) -> None:
        self.renderer = renderer
        self.events: list[dict] = []
        self.render_command: list[str] | None = None

    @contextlib.contextmanager
    def installed(self) -> Iterator["RenderProbe"]:
        renderer = self.renderer
        original_run = renderer.subprocess.run
        original_sha = renderer._sha256
        original_probe = renderer.probe_video
        probe = self

        def run(command, *args, **kwargs):
            # subprocess is one module: ffprobe calls (timed by probe_video below)
            # and this script's own commands pass through here too.
            if Path(str(command[0])).stem.lower() != "ffmpeg":
                return original_run(command, *args, **kwargs)
            started = time.perf_counter()
            try:
                return original_run(command, *args, **kwargs)
            finally:
                elapsed = time.perf_counter() - started
                kind = "ffmpeg_decode_validation" if "-filter_complex" not in command else "ffmpeg_render"
                if kind == "ffmpeg_render":
                    probe.render_command = list(command)
                probe.events.append({"step": kind, "seconds": round(elapsed, 3)})

        def sha(path):
            started = time.perf_counter()
            try:
                return original_sha(path)
            finally:
                probe.events.append({"step": "sha256", "seconds": round(time.perf_counter() - started, 3),
                                     "bytes": Path(path).stat().st_size if Path(path).exists() else None})

        def probe_video(ffprobe, video):
            started = time.perf_counter()
            try:
                return original_probe(ffprobe, video)
            finally:
                probe.events.append({"step": "ffprobe", "seconds": round(time.perf_counter() - started, 3)})

        renderer.subprocess.run = run
        renderer._sha256 = sha
        renderer.probe_video = probe_video
        try:
            yield self
        finally:
            renderer.subprocess.run = original_run
            renderer._sha256 = original_sha
            renderer.probe_video = original_probe


def _render_once(renderer, bench: Path, ffmpeg: Path, ffprobe: Path, tag: str) -> dict:
    """One real render_final_output in the bench root; returns timings and the manifest facts."""
    plan = _read_json(bench / "work" / "plan.template.json")
    plan_path = bench / "work" / f"plan-{tag}.json"
    output_path = bench / "output" / f"render-{tag}.mp4"
    for stale in (plan_path, output_path, output_path.with_suffix(".mp4.manifest.json")):
        stale.unlink(missing_ok=True)
    _write_json(plan_path, plan)
    probe = RenderProbe(renderer)
    started = time.perf_counter()
    with probe.installed():
        manifest = renderer.render_final_output(
            project_root=bench, plan_path=plan_path, output_path=output_path,
            ffmpeg_path=ffmpeg, ffprobe_path=ffprobe,
        )
    total = time.perf_counter() - started
    # render_final_output hashes the source, then the output, then the source again.
    sha_labels = iter(("sha256_source_before", "sha256_output", "sha256_source_after"))
    named = [
        {**event, "step": next(sha_labels) if event["step"] == "sha256" else event["step"]}
        for event in probe.events
    ]
    return {
        "tag": tag, "total_seconds": round(total, 3), "events": named,
        "command": probe.render_command,
        "output_sha256": manifest["output"]["sha256"], "output_bytes": manifest["output"]["bytes"],
        "output_duration_seconds": manifest["output"]["duration_seconds"],
        "manifest_elapsed_seconds": manifest["encoding"]["elapsed_seconds"],
        "manifest_decode_validation_seconds": manifest["encoding"]["full_decode_validation_seconds"],
        "manifest_video_seconds_per_processing_second": manifest["encoding"]["video_seconds_per_processing_second"],
        "output_path": output_path,
    }


# --------------------------------------------------------------------------- command variants

def _graph_index(command: list[str]) -> int:
    return command.index("-filter_complex") + 1


def _null_command(command: list[str]) -> list[str]:
    """The render command up to its maps, with FFmpeg's null muxer instead of encode + mp4."""
    cut = command.index("-map_metadata")
    return command[:cut] + ["-f", "null", "-"]


def _with_graph(command: list[str], graph: str) -> list[str]:
    copy = list(command)
    copy[_graph_index(copy)] = graph
    return copy


def _with_output(command: list[str], output: Path) -> list[str]:
    return command[:-1] + [str(output)]


def _without_progress(command: list[str]) -> list[str]:
    copy = list(command)
    if "-progress" in copy:
        index = copy.index("-progress")
        del copy[index:index + 2]
    return copy


def _variant_operations(name: str, operations: list[dict], duration: float) -> list[dict]:
    def long(item: dict) -> bool:
        return float(item["end_seconds"]) - float(item["start_seconds"]) >= LONG_BLUR_SHARE * duration
    if name == "no_blur":
        return [item for item in operations if item["type"] != "blur"]
    if name == "long_only":
        return [item for item in operations if item["type"] != "blur" or long(item)]
    if name == "short_only":
        return [item for item in operations if item["type"] != "blur" or not long(item)]
    if name == "no_feather":
        return [
            {**item, "blur": {**(item.get("blur") or {}), "edge_feather_pixels": 0}} if item["type"] == "blur" else item
            for item in operations
        ]
    if name == "gblur_method":
        return [
            {**item, "blur": {**(item.get("blur") or {}), "method": "gblur"}} if item["type"] == "blur" else item
            for item in operations
        ]
    raise ValueError(name)


def _graph(renderer, plan: dict, operations: list[dict], frame_size: tuple[int, int], has_audio: bool) -> str:
    graph, _ = renderer.build_final_filter_graph(
        operations=operations, duration=float(plan["source"]["duration_seconds"]),
        has_audio=has_audio, frame_size=frame_size,
    )
    return graph


# --------------------------------------------------------------------------- measure

def command_measure(args: argparse.Namespace) -> int:
    install_root = args.install_root.resolve(strict=True)
    bench = _bench_dir(install_root, args.name)
    prepared = _read_json(bench / "prepared.json")
    ffmpeg, ffprobe = _tools(install_root)
    src = args.src.resolve(strict=True)
    renderer = _import_biliflow(src)
    steps = tuple(args.steps or DEFAULT_STEPS)
    plan = _read_json(bench / "work" / "plan.template.json")
    source = Path(plan["source"]["path"])
    has_audio = prepared["copy"]["audio_codec"] is not None
    frame_size = tuple(prepared["graph_frame_size"])
    results: dict[str, Any] = {
        "schema_version": 1, "name": args.name, "started_at": _now(), "steps": {},
        "code": _git_state(src), "machine": _machine(ffmpeg),
        "copy": prepared["copy"], "origin": prepared["origin"],
        "operations": prepared["operations"],
    }
    tag = _stamp()
    with _install_render_lock(install_root):
        command = None
        render_output = None
        if "render" in steps:
            print("render ...", flush=True)
            render = _render_once(renderer, bench, ffmpeg, ffprobe, f"measure-{tag}")
            render_output = render.pop("output_path")
            command = render["command"]
            results["steps"]["render"] = {key: value for key, value in render.items() if key != "command"}
            print(json.dumps(results["steps"]["render"], indent=2), flush=True)
        if command is None:
            command = _recorded_command(renderer, bench, ffmpeg, ffprobe, tag)
        results["render_command_shape"] = _command_shape(command, bench)
        if "filters_null" in steps:
            print("filters_null ...", flush=True)
            seconds, _ = _run_timed(_without_progress(_null_command(command)))
            results["steps"]["filters_null"] = {"seconds": round(seconds, 3)}
        if "encode_only" in steps:
            print("encode_only ...", flush=True)
            output = bench / "output" / f"encode-only-{tag}.mp4"
            duration = float(plan["source"]["duration_seconds"])
            graph = _graph(renderer, plan, _variant_operations("no_blur", plan["approved_operations"], duration),
                           frame_size, has_audio)
            seconds, _ = _run_timed(_without_progress(_with_output(_with_graph(command, graph), output)))
            results["steps"]["encode_only"] = {"seconds": round(seconds, 3), "output_bytes": output.stat().st_size}
            output.unlink()
        if "decode_source" in steps:
            print("decode_source ...", flush=True)
            decode = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-i", str(source), "-map", "0:v:0"]
            if has_audio:
                decode += ["-map", "0:a:0"]
            seconds, _ = _run_timed(decode + ["-f", "null", "-"])
            results["steps"]["decode_source"] = {"seconds": round(seconds, 3)}
        if render_output is not None and "faststart" in steps:
            print("faststart ...", flush=True)
            timings = {}
            for label, flags in (("remux_plain", []), ("remux_faststart", ["-movflags", "+faststart"])):
                output = bench / "output" / f"{label}-{tag}.mp4"
                seconds, _ = _run_timed([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-i",
                                         str(render_output), "-map", "0", "-c", "copy", *flags, str(output)])
                timings[label] = round(seconds, 3)
                output.unlink()
            timings["faststart_extra_seconds"] = round(timings["remux_faststart"] - timings["remux_plain"], 3)
            results["steps"]["faststart"] = timings
        if render_output is not None and "output_sha256" in steps:
            started = time.perf_counter()
            _sha256(render_output)
            results["steps"]["output_sha256"] = {"seconds": round(time.perf_counter() - started, 3),
                                                 "bytes": render_output.stat().st_size}
        if "cli_import" in steps:
            print("cli_import ...", flush=True)
            environment = {**os.environ, "PYTHONPATH": str(src)}
            runs = []
            for _ in range(3):
                seconds, _ = _run_timed([sys.executable, "-c", "import biliflow.cli"], env=environment)
                runs.append(round(seconds, 3))
            results["steps"]["cli_import"] = {"seconds_per_run": runs}
        if "variants" in steps:
            duration = float(plan["source"]["duration_seconds"])
            variants = {}
            for variant in VARIANTS:
                print(f"variant {variant} ...", flush=True)
                operations = _variant_operations(variant, plan["approved_operations"], duration)
                graph = _graph(renderer, plan, operations, frame_size, has_audio)
                seconds, _ = _run_timed(_without_progress(_null_command(_with_graph(command, graph))))
                variants[variant] = {"seconds": round(seconds, 3),
                                     "blur_operations": sum(item["type"] == "blur" for item in operations)}
            results["steps"]["variants"] = variants
        if render_output is not None and not args.keep_outputs:
            render_output.unlink(missing_ok=True)
    results["finished_at"] = _now()
    path = bench / "results" / f"{tag}-measure.json"
    _write_json(path, results)
    print(json.dumps(results["steps"], indent=2))
    print(f"results: {path}")
    return 0


class _StopBeforeFfmpeg(Exception):
    pass


def _recorded_command(renderer, bench: Path, ffmpeg: Path, ffprobe: Path, tag: str) -> list[str]:
    """The exact render command render_final_output would run, without running it."""
    probe = RenderProbe(renderer)
    original = renderer.subprocess.run

    def stop(command, *args, **kwargs):
        if "-filter_complex" in command:
            probe.render_command = list(command)
            raise _StopBeforeFfmpeg()
        return original(command, *args, **kwargs)

    plan = _read_json(bench / "work" / "plan.template.json")
    plan_path = bench / "work" / f"plan-record-{tag}.json"
    output_path = bench / "output" / f"record-{tag}.mp4"
    _write_json(plan_path, plan)
    renderer.subprocess.run = stop
    try:
        renderer.render_final_output(
            project_root=bench, plan_path=plan_path, output_path=output_path,
            ffmpeg_path=ffmpeg, ffprobe_path=ffprobe,
        )
    except _StopBeforeFfmpeg:
        pass
    finally:
        renderer.subprocess.run = original
        plan_path.unlink(missing_ok=True)
    if probe.render_command is None:
        raise RuntimeError("render_final_output did not reach its FFmpeg render")
    return probe.render_command


def _command_shape(command: list[str], bench: Path) -> list[str]:
    """The render command with bench paths shortened and the graph replaced by its length."""
    shape = []
    for index, part in enumerate(command):
        if index and command[index - 1] == "-filter_complex":
            shape.append(f"<graph {len(part)} chars, {part.count(';') + 1} chains>")
        elif str(bench) in part:
            shape.append("<bench>/" + Path(part).relative_to(bench).as_posix())
        elif index == 0:
            shape.append("ffmpeg")
        else:
            shape.append(part)
    return shape


# --------------------------------------------------------------------------- gate

def command_gate_side(args: argparse.Namespace) -> int:
    """One side of the gate, in its own process so each side imports its own code."""
    install_root = args.install_root.resolve(strict=True)
    bench = _bench_dir(install_root, args.name)
    ffmpeg, ffprobe = _tools(install_root)
    src = args.src.resolve(strict=True)
    renderer = _import_biliflow(src)
    prepared = _read_json(bench / "prepared.json")
    plan = _read_json(bench / "work" / "plan.template.json")
    has_audio = prepared["copy"]["audio_codec"] is not None
    graph = _graph(renderer, plan, plan["approved_operations"], tuple(prepared["graph_frame_size"]), has_audio)
    framemd5 = bench / "results" / f"{args.tag}.framemd5"
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-i", plan["source"]["path"],
               "-filter_complex", graph, "-map", "[vout]"]
    if has_audio:
        command += ["-map", "[aout]"]
    # -pix_fmt as in the export, so [vout] negotiates the same pixel format.
    seconds, _ = _run_timed(command + ["-pix_fmt", "yuv420p", "-f", "framemd5", "-y", str(framemd5)])
    result: dict[str, Any] = {
        "tag": args.tag, "code": _git_state(src), "graph_chars": len(graph),
        "graph_sha256": hashlib.sha256(graph.encode("utf-8")).hexdigest(),
        "framemd5_seconds": round(seconds, 3), "framemd5_sha256": _sha256(framemd5),
        "framemd5_frames": sum(1 for line in framemd5.read_text(encoding="utf-8").splitlines()
                               if line and not line.startswith("#")),
    }
    (bench / "results" / f"{args.tag}.graph.txt").write_text(graph, encoding="utf-8")
    if not args.framemd5_only:
        render = _render_once(renderer, bench, ffmpeg, ffprobe, args.tag)
        output = render.pop("output_path")
        render.pop("command")
        result["render"] = render
        if not args.keep_outputs:
            output.unlink(missing_ok=True)
    _write_json(bench / "results" / f"{args.tag}.gate-side.json", result)
    return 0


def command_gate(args: argparse.Namespace) -> int:
    install_root = args.install_root.resolve(strict=True)
    bench = _bench_dir(install_root, args.name)
    prepared = _read_json(bench / "prepared.json")
    # The parent only needs the lock helper; take it from the candidate tree.
    _import_biliflow(args.candidate_src)
    tag = _stamp()
    sides = [("baseline", args.baseline_src), ("candidate", args.candidate_src)]
    if args.baseline_twice:
        sides.insert(1, ("baseline-repeat", args.baseline_src))
    with _install_render_lock(install_root):
        for label, src in sides:
            print(f"gate side {label} ...", flush=True)
            child = [sys.executable, str(Path(__file__).resolve()), "--install-root", str(install_root),
                     "_gate-side", "--name", args.name, "--src", str(src.resolve(strict=True)),
                     "--tag", f"{tag}-{label}"]
            # A baseline mp4 hash already proven (an earlier render of the same plan
            # and code) spares the baseline render; its framemd5 still runs.
            if args.framemd5_only or (label == "baseline" and args.baseline_mp4_sha256):
                child.append("--framemd5-only")
            if args.keep_outputs:
                child.append("--keep-outputs")
            subprocess.run(child, check=True)
    results = {label: _read_json(bench / "results" / f"{tag}-{label}.gate-side.json") for label, _ in sides}
    if args.baseline_mp4_sha256 and not args.framemd5_only:
        results["baseline"]["render"] = {"output_sha256": args.baseline_mp4_sha256, "given": True}
    baseline, candidate = results["baseline"], results["candidate"]

    def framemd5(label: str) -> bytes:
        return (bench / "results" / f"{tag}-{label}.framemd5").read_bytes()

    framemd5_identical = framemd5("baseline") == framemd5("candidate")
    mp4_identical = None
    if not args.framemd5_only:
        mp4_identical = baseline["render"]["output_sha256"] == candidate["render"]["output_sha256"]
    summary = {
        "schema_version": 1, "name": args.name, "tag": tag, "finished_at": _now(),
        "machine": _machine(_tools(install_root)[0]), "origin": prepared["origin"],
        "copy": prepared["copy"], "operations": prepared["operations"],
        "graph_changed": baseline["graph_sha256"] != candidate["graph_sha256"],
        "framemd5_identical": framemd5_identical, "mp4_sha256_identical": mp4_identical,
        "sides": results,
    }
    if args.baseline_twice:
        summary["baseline_repeat_framemd5_identical"] = framemd5("baseline") == framemd5("baseline-repeat")
        if not args.framemd5_only:
            summary["baseline_repeat_mp4_identical"] = (
                results["baseline-repeat"]["render"]["output_sha256"] == baseline["render"]["output_sha256"])
    passed = framemd5_identical and mp4_identical is not False
    summary["passed"] = passed
    path = bench / "results" / f"{tag}-gate.json"
    _write_json(path, summary)
    print(json.dumps({key: summary[key] for key in (
        "graph_changed", "framemd5_identical", "mp4_sha256_identical", "passed")}, indent=2))
    for label, side in results.items():
        print(label, "framemd5_seconds", side["framemd5_seconds"],
              "render_ffmpeg_seconds", (side.get("render") or {}).get("manifest_elapsed_seconds"))
    print(f"results: {path}")
    return 0 if passed else 1


# --------------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--install-root", type=Path, default=CHECKOUT)
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare")
    prepare.add_argument("--name", required=True)
    prepare.add_argument("--job", type=int, required=True)
    prepare.add_argument("--segment", type=float, nargs=2, metavar=("START", "SECONDS"))
    prepare.add_argument("--ops-from-job", type=int)
    prepare.add_argument("--synthetic", metavar="WxH:SECONDS:FPS")
    prepare.add_argument("--src", type=Path, default=CHECKOUT / "src")

    measure = sub.add_parser("measure")
    measure.add_argument("--name", required=True)
    measure.add_argument("--src", type=Path, default=CHECKOUT / "src")
    measure.add_argument("--steps", nargs="+", choices=STEPS)
    measure.add_argument("--keep-outputs", action="store_true")

    gate = sub.add_parser("gate")
    gate.add_argument("--name", required=True)
    gate.add_argument("--baseline-src", type=Path, required=True)
    gate.add_argument("--candidate-src", type=Path, required=True)
    gate.add_argument("--framemd5-only", action="store_true")
    gate.add_argument("--baseline-twice", action="store_true")
    gate.add_argument("--baseline-mp4-sha256", metavar="HEX",
                      help="SHA-256 of a baseline render of this bench already made (skips that render)")
    gate.add_argument("--keep-outputs", action="store_true")

    side = sub.add_parser("_gate-side")
    side.add_argument("--name", required=True)
    side.add_argument("--src", type=Path, required=True)
    side.add_argument("--tag", required=True)
    side.add_argument("--framemd5-only", action="store_true")
    side.add_argument("--keep-outputs", action="store_true")

    args = parser.parse_args(argv)
    handlers = {
        "prepare": command_prepare, "measure": command_measure,
        "gate": command_gate, "_gate-side": command_gate_side,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
