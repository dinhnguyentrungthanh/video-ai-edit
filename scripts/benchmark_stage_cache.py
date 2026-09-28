"""Compare cache reuse after an isolated UI edit using real text report files.

No decode, inference, production job or queue changes. Mutations are confined
to a temporary project; only benchmark evidence is retained under reports.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import types
from datetime import datetime
from pathlib import Path
from time import perf_counter

from biliflow.stage_cache import StageArtifactCache


def digests(directory):
    return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob("*") if p.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--baseline-ref", default="75d0481")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    report = args.report.resolve(strict=True)
    if not report.is_relative_to(project / "reports") or report.name != "text-scan.json":
        parser.error("Use an existing text-scan.json inside project reports")
    payload = json.loads(report.read_text(encoding="utf-8"))
    source = Path(payload["input"])
    source_hash = payload["input_sha256"]
    revision = subprocess.check_output([
        "git", "rev-parse", "--verify", "--end-of-options", args.baseline_ref + "^{commit}",
    ], cwd=project, text=True).strip()
    code = subprocess.check_output(["git", "show", f"{revision}:src/biliflow/stage_cache.py"], cwd=project)
    baseline = types.ModuleType("baseline_stage_cache")
    exec(compile(code, "baseline_stage_cache.py", "exec"), baseline.__dict__)
    evidence = {"baseline_commit": revision, "report": str(report), "comparisons": {}}
    with tempfile.TemporaryDirectory(prefix="cache-equivalence-", dir=project / "temp") as directory:
        root = Path(directory)
        shutil.copytree(project / "src/biliflow", root / "src/biliflow",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for relative in ("scripts/run.ps1", "scripts/env.ps1", "scripts/localize_visual_logo_report.py",
                         "config/processing_profiles.json", "config/detection_policy.yaml",
                         "config/text_review_policy.json", "config/license_policy.json",
                         "annotations/text_semantics_seed_v1.json", "pyproject.toml"):
            src = project / relative
            if src.is_file():
                dst = root / relative
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        original_root = root / "reports/jobs/original"
        original_artifact = original_root / "text/text-scan.json"
        shutil.copytree(report.parent, original_artifact.parent)
        expected = digests(original_artifact.parent)
        classes = {"baseline": baseline.StageArtifactCache, "scoped": StageArtifactCache}
        for label, cls in classes.items():
            cls(root).store(stage_name="text", source_sha256=source_hash, source_path=source,
                report_root=original_root, commands=(("scan-text", "--report-dir", str(original_artifact.parent)),),
                artifact_paths=(original_artifact,))
        ui = root / "src/biliflow/control_center.py"
        ui.write_bytes(ui.read_bytes() + b"\n# isolated benchmark UI-only edit\n")
        for label, cls in classes.items():
            destination = root / "reports/jobs" / label
            artifact = destination / "text/text-scan.json"
            started = perf_counter()
            hit = cls(root).restore(stage_name="text", source_sha256=source_hash, source_path=source,
                report_root=destination, commands=(("scan-text", "--report-dir", str(artifact.parent)),),
                artifact_paths=(artifact,))
            elapsed = perf_counter() - started
            evidence["comparisons"][label] = {
                "cache_hit_after_ui_edit": hit is not None, "lookup_and_restore_seconds": elapsed,
                "all_files_identical": digests(artifact.parent) == expected if hit else None,
                "restored_files": len(expected) if hit else 0,
            }
        if evidence["comparisons"]["baseline"]["cache_hit_after_ui_edit"]:
            raise RuntimeError("Expected baseline miss after UI change")
        if not evidence["comparisons"]["scoped"]["all_files_identical"]:
            raise RuntimeError("Scoped restore did not preserve every file")
    destination = project / "reports/benchmarks" / ("stage-cache-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "comparison.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=True, indent=2))
    print(destination)


if __name__ == "__main__":
    main()
