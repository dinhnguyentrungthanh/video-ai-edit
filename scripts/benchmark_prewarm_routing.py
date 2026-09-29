"""E3b prototype: warm the visual-logo routing cache while the OCR stage runs.

Runs the exact `scan-visual-logo` CLI arguments of the logo stage, redirects the
routing cache into BILIFLOW_BENCHMARK_CACHE (under reports/benchmarks) and stops
right after the routing cache is written, before candidate selection and before
the VLM loads. The later logo stage then reads that cache. Benchmark-only; the
production pipeline is unchanged. Guarded for spawned RoutingPool workers.
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

from biliflow import visual_logo_scanner
from biliflow.cli import main


class RoutingCacheWritten(Exception):
    pass


def run() -> int:
    root = Path(__file__).resolve().parents[1]
    cache = Path(os.environ["BILIFLOW_BENCHMARK_CACHE"]).resolve()
    if not cache.is_relative_to(root / "reports/benchmarks"):
        raise ValueError("Benchmark cache must stay under reports/benchmarks")
    if sys.argv[1:2] != ["scan-visual-logo"]:
        raise ValueError("Only scan-visual-logo arguments are accepted")
    old_path = visual_logo_scanner._routing_cache_path
    written = []

    def redirect(*args, **kwargs):
        return cache / old_path(*args, **kwargs).relative_to(root / "cache")

    def stop_after_routing(*args, **kwargs):
        # Called only after the scanner has written the routing cache.
        raise RoutingCacheWritten()

    original_write = visual_logo_scanner._write_routing_cache

    def record_write(path, **kwargs):
        original_write(path, **kwargs)
        written.append(str(path))

    with patch.object(visual_logo_scanner, "_routing_cache_path", side_effect=redirect), \
            patch.object(visual_logo_scanner, "_write_routing_cache", side_effect=record_write), \
            patch.object(visual_logo_scanner, "select_candidate_windows", side_effect=stop_after_routing):
        try:
            main()
        except RoutingCacheWritten:
            if len(written) != 1:
                raise RuntimeError("Routing finished without writing exactly one cache file")
            print(f"PREWARM_CACHE_WRITTEN {written[0]}", flush=True)
            return 0
    raise RuntimeError("Scanner did not stop after routing (cache hit or unexpected flow)")


if __name__ == "__main__":
    raise SystemExit(run())
