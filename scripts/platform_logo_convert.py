"""Turn remembered studio logos into platform logos (BLUR), or seed one from a video window.

Dry run by default: it prints what would change and writes nothing. --apply
first copies state/studio-logo-memory.json to state/backups/ and writes only
when the memory still has the bytes the plan was made from. Stop the Control
Center before --apply; a running Control Center reads platform records only
after a restart.

A standalone script rather than a ``biliflow`` subcommand: ``cli.py`` is part of
every scan stage's cache key, so a new subcommand would make every rerun recompute
its scans.

    .venv/Scripts/python.exe scripts/platform_logo_convert.py convert --key <sha>:<item> --platform iqiyi [--apply]
    .venv/Scripts/python.exe scripts/platform_logo_convert.py convert --key <sha>:<item> --to studio_logo [--apply]
    .venv/Scripts/python.exe scripts/platform_logo_convert.py seed --video <file> --start 2699.68 --end 2703.68 \\
        --platform iqiyi [--apply]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biliflow.platform_memory import convert_logo_memory_class, seed_platform_logo  # noqa: E402
from biliflow.platform_names import PLATFORMS  # noqa: E402

USABLE = {"convertible", "converted", "already"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project-root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    convert = commands.add_parser("convert", help="Change the class of remembered logos")
    convert.add_argument("--key", action="append", required=True, help="Record key <source sha>:<item id>")
    convert.add_argument("--to", choices=("platform_logo", "studio_logo"), default="platform_logo")
    convert.add_argument("--platform", choices=sorted(PLATFORMS), default=None)
    convert.add_argument("--apply", action="store_true", help="Back up the memory, then write")
    seed = commands.add_parser("seed", help="Remember a platform logo from a video window")
    seed.add_argument("--video", type=Path, required=True)
    seed.add_argument("--start", type=float, required=True)
    seed.add_argument("--end", type=float, required=True)
    seed.add_argument("--platform", choices=sorted(PLATFORMS), required=True)
    seed.add_argument("--ffmpeg", type=Path, default=None)
    seed.add_argument("--ffprobe", type=Path, default=None)
    seed.add_argument("--apply", action="store_true", help="Back up the memory, then write")
    args = parser.parse_args(argv)
    try:
        root = args.project_root.resolve(strict=True)
        if args.command == "convert":
            report = convert_logo_memory_class(root, args.key, to=args.to, platform=args.platform, apply=args.apply)
        else:
            report = seed_platform_logo(
                root, video=args.video, start=args.start, end=args.end, platform=args.platform,
                apply=args.apply, ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe,
            )
    except (ValueError, FileNotFoundError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return _exit_code(report)


def _exit_code(report: dict) -> int:
    """0 when something was (or would be) converted or seeded, or already is; 1 otherwise."""
    if report.get("error") or report.get("aborted") or report.get("status") in {"aborted", "refused"}:
        return 1
    records = report.get("records")
    if records is not None and not any(entry["status"] in USABLE for entry in records):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
