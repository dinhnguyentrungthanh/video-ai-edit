"""Warm the visual-logo routing cache while the OCR stage runs.

The OCR stage is GPU-bound and the logo routing is CPU/decoder-bound, so the
routing pass (``scan-visual-logo --routing-only``) runs as a child process of
the OCR stage. It writes the same routing cache the later logo stage reads; if
the child fails, or its settings do not produce the logo stage's cache key, the
logo stage simply computes routing itself. The child never writes reports.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

LOGO_ROUTING_OPTIONS = (
    # (scan-text option, scan-visual-logo option)
    ("--logo-sample-every", "--sample-every"),
    ("--logo-boundary-sample-every", "--boundary-sample-every"),
    ("--logo-boundary-seconds", "--boundary-seconds"),
    ("--logo-scene-change-threshold", "--scene-change-threshold"),
    ("--logo-coverage-bucket-seconds", "--coverage-bucket-seconds"),
    ("--logo-coverage-fallbacks-per-bucket", "--coverage-fallbacks-per-bucket"),
    ("--logo-routing-workers", "--routing-workers"),
    ("--logo-source-sha256", "--source-sha256"),
    ("--logo-decode", "--decode"),
)


def prewarm_command(*, input_path: Path, report_dir: Path, settings: dict[str, object],
                    python: str | None = None) -> list[str]:
    """argv for the child; ``settings`` maps scan-visual-logo options to values."""
    allowed = {logo for _, logo in LOGO_ROUTING_OPTIONS}
    unknown = set(settings) - allowed
    if unknown:
        raise ValueError(f"Unsupported routing prewarm settings: {sorted(unknown)}")
    argv = [python or sys.executable, "-m", "biliflow", "scan-visual-logo",
            "--input", str(input_path), "--report-dir", str(report_dir), "--routing-only"]
    for option in (logo for _, logo in LOGO_ROUTING_OPTIONS):
        if settings.get(option) is not None:
            argv += [option, str(settings[option])]
    return argv


class RoutingPrewarm:
    """A child process started before OCR and awaited (or stopped) after it."""

    def __init__(self, argv: list[str], *, cwd: Path, log_path: Path, popen=subprocess.Popen):
        self.argv = argv
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = log_path.open("w", encoding="utf-8")
        try:
            self._process = popen(argv, cwd=str(cwd), stdout=self._log, stderr=subprocess.STDOUT)
        except BaseException:
            self._log.close()
            raise

    def finish(self) -> int:
        try:
            return self._process.wait()
        finally:
            self._log.close()

    def stop(self, terminate=None) -> None:
        """Kill the child with its FFmpeg and RoutingPool descendants."""
        if terminate is None:
            from biliflow.scheduler import terminate_process_tree as terminate
        try:
            if self._process.poll() is None:
                terminate(self._process)
        finally:
            self._log.close()
