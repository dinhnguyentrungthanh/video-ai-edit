"""Benchmark-only cache namespace for logo/grounding stages; CLI arguments unchanged.

Redirects the visual-logo routing cache and the GroundingDINO result cache into
BILIFLOW_BENCHMARK_CACHE (under reports/benchmarks) so a trial runs cold without
reading, writing or deleting production caches. Guarded for spawned workers.
"""
import os
from pathlib import Path
from unittest.mock import patch

from biliflow import ad_candidate_pipeline, visual_logo_scanner
from biliflow.cli import main


def run() -> int:
    root = Path(__file__).resolve().parents[1]
    cache = Path(os.environ["BILIFLOW_BENCHMARK_CACHE"]).resolve()
    if not cache.is_relative_to(root / "reports/benchmarks"):
        raise ValueError("Benchmark cache must stay under reports/benchmarks")
    old_logo_path = visual_logo_scanner._routing_cache_path
    old_ad_path = ad_candidate_pipeline._cache_path

    def redirect(function, *args, **kwargs):
        return cache / function(*args, **kwargs).relative_to(root / "cache")

    with patch.object(visual_logo_scanner, "_routing_cache_path",
                      side_effect=lambda *a, **k: redirect(old_logo_path, *a, **k)), \
            patch.object(ad_candidate_pipeline, "_cache_path",
                         side_effect=lambda *a, **k: redirect(old_ad_path, *a, **k)):
        return main()


if __name__ == "__main__":
    raise SystemExit(run())
