"""Add every window frame and the blurred watermark regions to remembered studio logos.

Dry run by default. Stop the Control Center before --apply: a remember or forget
during the run aborts it, and a Control Center started from older code cannot read
the schema-2 memory file. --apply backs up state/studio-logo-memory.json first and
only adds fields (never removes a record or its old signatures).

A standalone script rather than a ``biliflow`` subcommand: ``cli.py`` is part of
every scan stage's cache key, so a new subcommand would make every rerun recompute
its scans.

    .venv/Scripts/python.exe scripts/studio_logo_upgrade.py [--apply]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biliflow.brand_memory import upgrade_studio_logo_memory  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply", action="store_true",
        help="Back up state/studio-logo-memory.json, then add the schema-2 fields (never removes anything)",
    )
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--ffmpeg", type=Path, default=None)
    parser.add_argument("--ffprobe", type=Path, default=None)
    args = parser.parse_args()
    report = upgrade_studio_logo_memory(
        args.project_root.resolve(strict=True), apply=args.apply,
        ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if report.get("aborted") else 0


if __name__ == "__main__":
    raise SystemExit(main())
