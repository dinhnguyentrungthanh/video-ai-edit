"""Benchmark-only OCR stage runner: its routing-prewarm child uses the benchmark cache.

`scan-text --prewarm-logo-routing` starts `python -m biliflow scan-visual-logo
--routing-only`; here that child is started through benchmark_cold_ad_stage.py
instead, so the warmed routing cache lands in BILIFLOW_BENCHMARK_CACHE and no
production cache is read or written. All other arguments are unchanged.
"""
from pathlib import Path
from unittest.mock import patch

from biliflow import routing_prewarm
from biliflow.cli import main

COLD_STAGE = Path(__file__).resolve().parent / "benchmark_cold_ad_stage.py"


def run() -> int:
    original = routing_prewarm.prewarm_command

    def through_cold_stage(**kwargs):
        argv = original(**kwargs)
        if argv[1:3] != ["-m", "biliflow"]:
            raise RuntimeError("Unexpected prewarm command shape")
        return [argv[0], str(COLD_STAGE), *argv[3:]]

    with patch.object(routing_prewarm, "prewarm_command", side_effect=through_cold_stage):
        return main()


if __name__ == "__main__":
    raise SystemExit(run())
