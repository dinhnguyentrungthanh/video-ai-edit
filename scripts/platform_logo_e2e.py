"""End-to-end check of the platform-logo cards (batch 4a) on a COPY of a real export.

Never writes production state. Everything happens under
``<production>/temp/platform-logo-e2e-<ts>/root`` (T): a copy of the export
(input/t17.mp4), copies of the config files and the OCR semantic seed, empty
reports/state/cache/tmp, and junctions ``models``, ``tools`` and
``runtime/florence-python`` to the production folders (read-only use). ``--memory-copy`` copies the studio-logo
memory and its frames into T and converts the three iQIYI records there.
``prepare`` records fingerprints of the production memory files, the Tập 17
queue, the export and the listings behind the junctions; ``check`` compares them.

    python scripts/platform_logo_e2e.py prepare --production-root E:/DungChung/BiliFlow \\
        --export "output/Nhất-Âu-Xuân---Tập-17-bae453af-259f7b43-reviewed.mp4" --memory-copy
    python scripts/platform_logo_e2e.py run --root <T> [--from-stage localize_logo]   # OCR … build-review
    python scripts/platform_logo_e2e.py check --root <T> [--queue-name review-queue.json] [--evidence DIR]
    python scripts/platform_logo_e2e.py seed-ending --root <T> --start 2699.68 --end 2703.68
    python scripts/platform_logo_e2e.py rebuild --root <T> --queue-name review-queue-seeded.json [--no-memory]
    python scripts/platform_logo_e2e.py cleanup --root <T>        # junctions first, then the folder
                                                                  # (only <install>/temp/platform-logo-e2e-*)

``run`` refuses while a Control Center listens on 127.0.0.1:8765 or another
BiliFlow GPU command runs; every GPU stage holds the ``Local\\BiliFlowGpuInference``
mutex like scripts/run.ps1. The stages come from ``job_pipeline.pipeline_stages``
(advertising only, fast scan) with the logo-routing prewarm dropped (its child
would use the production root) and explicit model/tool paths under T.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT / "src"))

from biliflow.job_pipeline import pipeline_stages, safe_job_key  # noqa: E402
from biliflow.platform_memory import convert_logo_memory_class, seed_platform_logo  # noqa: E402

IQIYI_RECORDS = ("review-dd6d551760b5", "review-571a51c78be4", "review-bfbb9423ac85")
CONFIG_FILES = ("processing_profiles.json", "detection_policy.yaml", "text_review_policy.json",
                "license_policy.json")
SEMANTIC_SEED = Path("annotations") / "text_semantics_seed_v1.json"
FINGERPRINTED = ("state/studio-logo-memory.json", "state/brand-memory.json",
                 "reports/jobs/nhất-âu-xuân-tập-17-bae453af/review-queue.json")
GPU_MUTEX = "Local\\BiliFlowGpuInference"
GPU_COMMANDS = ("scan", "scan-text", "scan-content", "scan-animation-safety", "scan-live-safety",
                "scan-visual-logo", "confirm-violence", "verify-adult", "localize-visual-logo",
                "augment-grounding-regions", "control-center")
CONTROL_CENTER = ("127.0.0.1", 8765)
# Read-only links into the production tree (the Florence localizer imports its own
# transformers from runtime/florence-python).
JUNCTIONS = ("models", "tools", "runtime/florence-python")
META = "e2e-meta.json"
COPY_NAME = "t17.mp4"
# The measured Tập 17 export (1280x534): logo pixels that every BLUR region must cover.
OPENING_LOGO = (516, 194, 746, 326)
ENDING_LOGO = (414, 220, 870, 292)


def _install_root(code_root: Path = CODE_ROOT) -> Path:
    """The install whose temp folder holds the e2e runs; a worktree lives in <install>/temp/<name>."""
    return code_root.parent.parent if code_root.parent.name.lower() == "temp" else code_root


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _listing(folder: Path) -> str:
    """Hash of every name, size and mtime below ``folder`` (detects writes through a junction)."""
    rows = []
    for current, directories, files in os.walk(folder):
        directories.sort()
        for name in sorted(files):
            path = Path(current) / name
            info = path.stat()
            rows.append(f"{path.relative_to(folder).as_posix()}|{info.st_size}|{info.st_mtime_ns}")
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def _fingerprints(production: Path, export: Path) -> dict[str, str | None]:
    values = {name: _sha256(production / name) for name in FINGERPRINTED}
    values[export.relative_to(production).as_posix()] = _sha256(export)
    for name in JUNCTIONS:
        values[f"{name}/ (listing)"] = _listing(production / name)
    return values


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _meta(root: Path) -> dict[str, Any]:
    return json.loads((root.parent / META).read_text(encoding="utf-8"))


def _reparse_point(path: Path) -> bool:
    try:
        return bool(os.lstat(path).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except FileNotFoundError:
        return False


# ------------------------------------------------------------------ prepare


def prepare(args: argparse.Namespace) -> int:
    production = args.production_root.resolve(strict=True)
    export = (production / args.export).resolve(strict=True)
    if (production / "output").resolve() not in export.parents:
        raise SystemExit("--export must be a file inside the production output folder")
    base = production / "temp" / f"platform-logo-e2e-{datetime.now():%Y%m%d-%H%M%S}"
    root = base / "root"
    for name in ("input", "reports", "state", "cache", "tmp", "config", "annotations", "runtime"):
        (root / name).mkdir(parents=True)
    started = time.perf_counter()
    fingerprints = _fingerprints(production, export)
    copy = root / "input" / COPY_NAME
    shutil.copyfile(export, copy)
    for name in CONFIG_FILES:
        shutil.copyfile(production / "config" / name, root / "config" / name)
    shutil.copyfile(production / SEMANTIC_SEED, root / SEMANTIC_SEED)
    _ensure_links(root, production)
    meta: dict[str, Any] = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "production_root": str(production), "code_root": str(CODE_ROOT), "export": str(export),
        "copy": str(copy), "copy_sha256": _sha256(copy), "fingerprints": fingerprints,
        "memory_copy": bool(args.memory_copy), "converted": None, "seeded": None,
    }
    if args.memory_copy:
        shutil.copyfile(production / "state" / "studio-logo-memory.json", root / "state" / "studio-logo-memory.json")
        shutil.copytree(production / "state" / "studio-logo-frames", root / "state" / "studio-logo-frames")
        records = json.loads((root / "state" / "studio-logo-memory.json").read_text(encoding="utf-8"))["records"]
        keys = [record["key"] for record in records
                if str(record.get("key", "")).rsplit(":", 1)[-1] in IQIYI_RECORDS]
        meta["converted"] = convert_logo_memory_class(root, keys, to="platform_logo", platform="iqiyi",
                                                      apply=True, actor="platform-logo-e2e")
    meta["prepare_seconds"] = round(time.perf_counter() - started, 1)
    _write_json(base / META, meta)
    print(root)
    return 0


def _ensure_links(root: Path, production: Path) -> None:
    import _winapi  # no shell: "cmd /c mklink" splits an unquoted "&" in a path

    for name in JUNCTIONS:
        link = root / name
        if not _reparse_point(link):
            link.parent.mkdir(parents=True, exist_ok=True)
            _winapi.CreateJunction(str((production / name).resolve(strict=True)), str(link))


# ---------------------------------------------------------------------- run


def _preflight() -> None:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(CONTROL_CENTER) == 0:
            raise SystemExit("SKIP: a Control Center listens on 127.0.0.1:8765")
    import psutil

    for process in psutil.process_iter(["pid", "cmdline"]):
        if process.info["pid"] == os.getpid():
            continue
        arguments = [str(value) for value in (process.info.get("cmdline") or [])]
        text = " ".join(arguments).casefold()
        if "biliflow" in text and any(value in GPU_COMMANDS for value in arguments):
            raise SystemExit(f"SKIP: another BiliFlow GPU command runs (pid {process.info['pid']})")


@contextlib.contextmanager
def _gpu_slot(needed: bool) -> Iterator[float]:
    """Hold the GPU mutex of scripts/run.ps1 while a GPU stage runs; yields the wait in seconds."""
    if not needed:
        yield 0.0
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel32.ReleaseMutex.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel32.CreateMutexW(None, False, GPU_MUTEX)
    if not handle:
        raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
    started = time.perf_counter()
    result = kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
    if result not in (0, 0x80):  # WAIT_OBJECT_0, WAIT_ABANDONED
        kernel32.CloseHandle(handle)
        raise OSError(result, "WaitForSingleObject failed")
    try:
        yield time.perf_counter() - started
    finally:
        kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)


def _without_logo_prewarm(arguments: list[str]) -> list[str]:
    kept, skip = [], False
    for value in arguments:
        if skip:
            skip = False
        elif value == "--prewarm-logo-routing":
            continue
        elif value.startswith("--logo-"):
            skip = True
        else:
            kept.append(value)
    return kept


def _explicit_paths(root: Path, command: str) -> list[str]:
    ffmpeg = root / "tools" / "ffmpeg" / "bin"
    tools = ["--ffmpeg", str(ffmpeg / "ffmpeg.exe")]
    if command == "scan-text":
        return ["--model-dir", str(root / "models" / "easyocr"),
                "--semantic-model", str(root / "models" / "multilingual_minilm_text_semantics"),
                "--semantic-seed", str(root / SEMANTIC_SEED),
                "--policy", str(root / "config" / "text_review_policy.json"),
                *tools, "--ffprobe", str(ffmpeg / "ffprobe.exe")]
    if command == "scan-visual-logo":
        return ["--model", str(root / "models" / "qwen2_vl_2b_instruct"), *tools,
                "--ffprobe", str(ffmpeg / "ffprobe.exe")]
    if command == "augment-grounding-regions":
        return ["--model", str(root / "models" / "grounding_dino_tiny"), *tools]
    return []


def _child_command(root: Path, argv: tuple[str, ...]) -> tuple[str, list[str]]:
    index = next(position for position, value in enumerate(argv) if value.endswith("run.ps1"))
    command, *arguments = argv[index + 1:]
    arguments = _without_logo_prewarm(list(arguments))
    if command == "localize-visual-logo":
        return command, [sys.executable, str(CODE_ROOT / "scripts" / "localize_visual_logo_report.py"),
                         "--project-root", str(root), *arguments]
    return command, [sys.executable, "-m", "biliflow", "--project-root", str(root), command, *arguments,
                     *_explicit_paths(root, command)]


def _environment(root: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("BILIFLOW_BENCHMARK_CACHE", None)
    environment.update(
        PYTHONPATH=str(CODE_ROOT / "src"), TEMP=str(root / "tmp"), TMP=str(root / "tmp"),
        HF_HOME=str(root / "cache" / "huggingface"), HF_HUB_CACHE=str(root / "cache" / "huggingface" / "hub"),
        TORCH_HOME=str(root / "cache" / "torch"), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
        HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1", PYTHONIOENCODING="utf-8",
    )
    return environment


def _execute(root: Path, name: str, argv: list[str], uses_gpu: bool, log: Path) -> dict[str, Any]:
    with _gpu_slot(uses_gpu) as waited:
        started = time.perf_counter()
        completed = subprocess.run(argv, cwd=CODE_ROOT, env=_environment(root), capture_output=True,
                                   text=True, encoding="utf-8", errors="replace")
        elapsed = time.perf_counter() - started
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(f"$ {' '.join(argv)}\n\n{completed.stdout}\n--- stderr ---\n{completed.stderr}",
                   encoding="utf-8")
    return {"stage": name, "returncode": completed.returncode, "seconds": round(elapsed, 1),
            "gpu_wait_seconds": round(waited, 1), "log": str(log)}


def _job(root: Path) -> tuple[str, Path, str]:
    copy = root / "input" / COPY_NAME
    sha256 = _meta(root)["copy_sha256"]
    return safe_job_key(copy, sha256), copy, sha256


def run(args: argparse.Namespace) -> int:
    root = args.root.resolve(strict=True)
    _preflight()
    _ensure_links(root, Path(_meta(root)["production_root"]))
    job_key, copy, sha256 = _job(root)
    stages = pipeline_stages(root=root, job_key=job_key, source=copy, content_style="live_action",
                             profile="careful", source_sha256=sha256, detector_groups=["advertising"],
                             fast_scan=True)
    names = [stage.name for stage in stages]
    if args.from_stage and args.from_stage not in names:
        raise SystemExit(f"--from-stage must be one of {', '.join(names)}")
    first = names.index(args.from_stage) if args.from_stage else 0
    previous = root.parent / "run.json"
    results = json.loads(previous.read_text(encoding="utf-8"))["stages"] if first and previous.is_file() else []
    results = [value for value in results if value["stage"].split(":")[0] in names[:first]]
    started = time.perf_counter()
    for stage in stages[first:]:
        for number, step in enumerate(stage.commands):
            if not step.argv:
                continue
            command, argv = _child_command(root, step.argv)
            result = _execute(root, f"{stage.name}:{command}", argv, stage.uses_gpu,
                              root.parent / "logs" / f"{stage.name}-{number}.log")
            result["missing_artifacts"] = [str(path) for path in step.expected_artifacts if not path.is_file()]
            results.append(result)
            print(json.dumps(result, ensure_ascii=False))
            if result["returncode"] or result["missing_artifacts"]:
                _write_json(root.parent / "run.json", {"job_key": job_key, "stages": results, "failed": True})
                return 1
    summary = {"job_key": job_key, "stages": results, "failed": False,
               "total_seconds": round(sum(value["seconds"] for value in results), 1),
               "this_invocation_seconds": round(time.perf_counter() - started, 1),
               "queue": str(root / "reports" / "jobs" / job_key / "review-queue.json")}
    _write_json(root.parent / "run.json", summary)
    return 0


def rebuild(args: argparse.Namespace) -> int:
    root = args.root.resolve(strict=True)
    job_key, _, _ = _job(root)
    base = root / "reports" / "jobs" / job_key
    argv = [sys.executable, "-m", "biliflow", "--project-root", str(root), "build-review",
            "--report", str(base / "text" / "text-scan.json"),
            "--report", str(base / "visual-logo" / "scan-localized.json"),
            "--selected-detector", "advertising", "--content-style", "live_action",
            "--queue", str(base / args.queue_name)]
    if args.no_memory:
        argv.append("--no-studio-logo-memory")
    result = _execute(root, "build_review", argv, False, root.parent / "logs" / f"rebuild-{args.queue_name}.log")
    _write_json(root.parent / f"rebuild-{Path(args.queue_name).stem}.json", result)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["returncode"] else 0


def seed_ending(args: argparse.Namespace) -> int:
    root = args.root.resolve(strict=True)
    tools = root / "tools" / "ffmpeg" / "bin"
    report = seed_platform_logo(root, video=root / "input" / COPY_NAME, start=args.start, end=args.end,
                                platform="iqiyi", apply=True, ffmpeg_path=tools / "ffmpeg.exe",
                                ffprobe_path=tools / "ffprobe.exe", actor="platform-logo-e2e")
    meta = _meta(root)
    meta["seeded"] = report
    _write_json(root.parent / META, meta)
    print(json.dumps({key: report.get(key) for key in ("status", "reason", "logo_frames", "stored_frames",
                                                        "box_share", "key")}, ensure_ascii=False))
    return 0 if report.get("status") == "seeded" else 1


# -------------------------------------------------------------------- check


def _covers(region: dict | None, box: tuple[int, int, int, int]) -> bool:
    if not isinstance(region, dict):
        return False
    return (region["x"] <= box[0] and region["y"] <= box[1]
            and region["x"] + region["width"] >= box[2] and region["y"] + region["height"] >= box[3])


def _card(item: dict[str, Any], frame: list[int] | None) -> dict[str, Any]:
    region = item.get("suggested_region_source_pixels")
    share = (round(region["width"] * region["height"] / (frame[0] * frame[1]), 4)
             if isinstance(region, dict) and frame else None)
    platform = item.get("platform_logo") or {}
    return {
        "id": item.get("id"), "type": item.get("candidate_type"), "interval": [item.get("start_seconds"),
                                                                                item.get("end_seconds")],
        "suggested": item.get("suggested_decision"), "region": region, "region_share": share,
        "labels": (item.get("labels") or [])[:3], "reason": str((item.get("reasons") or [""])[0])[:240],
        "detections": [value.get("kind") for value in platform.get("detections") or []],
        "snap": platform.get("snap"), "region_method": (platform.get("region") or {}).get("method"),
        "link": item.get("platform_logo_link"), "previews": item.get("preview_images"),
    }


def _evaluate(queue: dict[str, Any]) -> list[dict[str, Any]]:
    duration = float((queue.get("source") or {}).get("duration_seconds") or 0.0)
    items, advisory = queue.get("items") or [], queue.get("advisory_items") or []
    platform = [item for item in items if item.get("candidate_type") == "platform_logo"]
    opening = next((item for item in platform if item["start_seconds"] < 60), None)
    ending = next((item for item in platform if item["start_seconds"] > duration - 60), None)
    checks: list[dict[str, Any]] = []

    def expect(name: str, ok: object, detail: object = None) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    expect("opening iQIYI card ≈[8.0, 13.0] covering [10.0, 12.9]",
           opening and 7.4 <= opening["start_seconds"] <= 10.0 and opening["end_seconds"] >= 12.9,
           opening and [opening["start_seconds"], opening["end_seconds"]])
    expect("opening suggestion BLUR", opening and opening.get("suggested_decision") == "BLUR")
    expect(f"opening region covers {OPENING_LOGO}",
           opening and _covers(opening.get("suggested_region_source_pixels"), OPENING_LOGO),
           opening and opening.get("suggested_region_source_pixels"))
    expect("ending iQIYI card ≈[2699.7, D] covering [2700, D-0.2]",
           ending and 2699.0 <= ending["start_seconds"] <= 2700.0 and ending["end_seconds"] >= duration - 0.2,
           ending and [ending["start_seconds"], ending["end_seconds"], duration])
    expect("ending suggestion BLUR", ending and ending.get("suggested_decision") == "BLUR")
    expect(f"ending region covers {ENDING_LOGO}",
           ending and _covers(ending.get("suggested_region_source_pixels"), ENDING_LOGO),
           ending and ending.get("suggested_region_source_pixels"))
    # The first 5 s always get a whole-scene card: the forced opening_boundary, or the
    # scanner's own opening_promotion when Qwen called that window promotional.
    for name, kinds, low, high in (("opening 0-5 s card", ("opening_boundary", "opening_promotion"), 0.0, 5.0),
                                   ("ending_boundary card", ("ending_boundary",), duration - 6.0, duration)):
        def near(item: dict[str, Any]) -> bool:
            return (item.get("candidate_type") in kinds and abs(item["start_seconds"] - low) <= 0.6
                    and abs(item["end_seconds"] - high) <= 0.6)
        main = [item for item in items if near(item)]
        optional = [item for item in advisory if near(item)]
        found = (main or optional or [None])[0]
        expect(f"{name} present ≈[{low:.2f}, {high:.2f}] (main or advisory)", found,
               found and {"list": "main" if main else "advisory", "type": found.get("candidate_type"),
                          "interval": [found["start_seconds"], found["end_seconds"]],
                          "suggested": found.get("suggested_decision"),
                          "moved_by_studio_memory": bool(found.get("studio_logo_match"))})
        expect(f"{name} never suggests CUT or BLUR",
               found and found.get("suggested_decision") in (None, "KEEP"), found and found.get("suggested_decision"))
    expect("no platform card suggests CUT", all(item.get("suggested_decision") != "CUT" for item in platform))
    return checks


def check(args: argparse.Namespace) -> int:
    root = args.root.resolve(strict=True)
    meta = _meta(root)
    job_key, _, _ = _job(root)
    queue_path = root / "reports" / "jobs" / job_key / args.queue_name
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    frame = next((item.get("source_frame_size") for item in queue.get("items") or []
                  if item.get("candidate_type") == "platform_logo"), None)
    production = Path(meta["production_root"])
    now = _fingerprints(production, Path(meta["export"]))
    checks = _evaluate(queue)
    checks.append({"name": "production state, Tập 17 queue, export and linked folders unchanged",
                   "ok": now == meta["fingerprints"],
                   "detail": {name: value == meta["fingerprints"].get(name) for name, value in now.items()}})
    interesting = {"platform_logo", "opening_boundary", "opening_promotion", "ending_boundary"}
    report = {
        "queue": str(queue_path), "duration": (queue.get("source") or {}).get("duration_seconds"),
        "memory_copy": meta.get("memory_copy"), "seeded": bool(meta.get("seeded")),
        "checks": checks, "passed": all(value["ok"] for value in checks),
        "cards": [_card(item, frame) for item in queue.get("items") or []
                  if item.get("candidate_type") in interesting],
        "advisory_cards": [_card(item, frame) for item in queue.get("advisory_items") or []
                           if item.get("candidate_type") in interesting or item.get("covered_by")],
        "main_items": len(queue.get("items") or []), "advisory_items": len(queue.get("advisory_items") or []),
        "platform_logos": queue.get("platform_logos"),
        "studio_logo_memory": queue.get("studio_logo_memory"),
    }
    for name in ("run.json", f"rebuild-{Path(args.queue_name).stem}.json"):
        if (root.parent / name).is_file():
            report[name] = json.loads((root.parent / name).read_text(encoding="utf-8"))
    output = root.parent / f"check-{Path(args.queue_name).stem}.json"
    _write_json(output, report)
    if args.evidence:
        _write_json(args.evidence / output.name, report)
        _write_json(args.evidence / META, {key: value for key, value in meta.items() if key != "converted"}
                    | {"converted": [{k: v for k, v in entry.items() if k != "blur_region"}
                                     for entry in (meta.get("converted") or {}).get("records", [])]})
    for value in checks:
        print(("PASS " if value["ok"] else "FAIL ") + value["name"])
    return 0 if report["passed"] else 1


# ------------------------------------------------------------------ cleanup


def _same_path(first: Path | str, second: Path | str) -> bool:
    return os.path.normcase(os.path.normpath(str(first))) == os.path.normcase(os.path.normpath(str(second)))


def _cleanup_base(root: Path, install: Path) -> Path:
    """The e2e folder ``prepare`` made for ``install``, or SystemExit (nothing touched).

    Directly inside ``<install>/temp``, lexically and resolved; neither it nor
    that temp folder may be a reparse point (a junction would make the tree
    removed elsewhere than it reads); its e2e-meta.json must name ``install``.
    """
    temp = install / "temp"
    base = root.parent
    if not base.name.startswith("platform-logo-e2e-") or root.name != "root":
        raise SystemExit("refusing: not a platform-logo e2e folder")
    if not _same_path(base.parent, temp):
        raise SystemExit(f"refusing: {base} is not directly inside {temp}")
    for path in (temp, base):
        if _reparse_point(path):
            raise SystemExit(f"refusing: {path} is a reparse point")
    try:
        resolved = base.resolve(strict=True)
        resolved_temp = temp.resolve(strict=True)
    except OSError as error:
        raise SystemExit(f"refusing: {base} cannot be resolved ({error})") from error
    if not _same_path(resolved.parent, resolved_temp):
        raise SystemExit(f"refusing: {base} does not resolve inside {temp}")
    if not (base / META).is_file():
        raise SystemExit(f"refusing: {base} has no {META} (made by prepare)")
    try:
        recorded = json.loads((base / META).read_text(encoding="utf-8")).get("production_root")
    except (OSError, ValueError, AttributeError) as error:
        raise SystemExit(f"refusing: {META} is unreadable ({error})") from error
    if not recorded or not _same_path(recorded, install):
        raise SystemExit(f"refusing: {META} names another install ({recorded})")
    return base


def cleanup(args: argparse.Namespace) -> int:
    # Lexical paths, like Windows itself; nothing here follows a link.
    root = Path(os.path.normpath(args.root.absolute()))
    base = _cleanup_base(root, Path(os.path.normpath(args.production_root.absolute())))
    for name in JUNCTIONS:
        link = root / name
        if link.exists() or _reparse_point(link):
            if not _reparse_point(link):
                raise SystemExit(f"refusing: {link} is a real folder, not a junction")
            os.rmdir(link)  # removes the junction only, never the production folder behind it
    pending = [base]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                path = Path(entry.path)
                if _reparse_point(path):
                    raise SystemExit(f"refusing: reparse point left at {path}")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
    shutil.rmtree(base)
    print(f"removed {base}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    step = commands.add_parser("prepare")
    step.add_argument("--production-root", type=Path, default=_install_root())
    step.add_argument("--export", type=Path, required=True)
    step.add_argument("--memory-copy", action="store_true")
    for name in ("run", "check", "rebuild", "seed-ending", "cleanup"):
        step = commands.add_parser(name)
        step.add_argument("--root", type=Path, required=True)
        if name == "cleanup":
            step.add_argument("--production-root", type=Path, default=_install_root(),
                              help="The install whose temp folder holds the e2e folder (default: this one)")
        if name in ("check", "rebuild"):
            step.add_argument("--queue-name", default="review-queue.json")
        if name == "check":
            step.add_argument("--evidence", type=Path)
        if name == "rebuild":
            step.add_argument("--no-memory", action="store_true")
        if name == "run":
            step.add_argument("--from-stage", help="Resume at this pipeline stage (earlier artifacts kept)")
        if name == "seed-ending":
            step.add_argument("--start", type=float, required=True)
            step.add_argument("--end", type=float, required=True)
    args = parser.parse_args(argv)
    handlers = {"prepare": prepare, "run": run, "check": check, "rebuild": rebuild,
                "seed-ending": seed_ending, "cleanup": cleanup}
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
