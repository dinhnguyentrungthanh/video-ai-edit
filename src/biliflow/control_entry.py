from __future__ import annotations

import argparse
from pathlib import Path

from biliflow.control_center import serve_control_center
from biliflow.http_guards import loopback_host_argument


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="biliflow-control-center")
    parser.add_argument("--project-root", type=Path, required=True)
    # Loopback only: on a LAN address any machine could read the session token.
    parser.add_argument("--host", default="127.0.0.1", type=loopback_host_argument)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--stable-seconds", type=float, default=60.0)
    parser.add_argument("--no-import-existing", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    serve_control_center(
        project_root=args.project_root,
        host=args.host,
        port=args.port,
        stable_seconds=args.stable_seconds,
        import_existing=not args.no_import_existing,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
