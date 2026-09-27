from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from biliflow.review_regression import build_review_regression_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Build regression data from completed review queues")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--queue", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-source-hash", action="store_true")
    args = parser.parse_args()
    root = args.project_root.resolve(strict=True)
    output = (root / args.output).resolve()
    annotations = (root / "annotations").resolve(strict=True)
    if output != annotations and annotations not in output.parents:
        raise ValueError("Output must stay inside annotations")
    payload = build_review_regression_manifest(
        project_root=root,
        queue_paths=[(root / value).resolve(strict=True) for value in args.queue],
        verify_sources=not args.skip_source_hash,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(json.dumps({
        "output": output.relative_to(root).as_posix(),
        "source_count": payload["source_count"],
        "example_count": payload["example_count"],
        "decision_counts": payload["decision_counts"],
        "category_counts": payload["category_counts"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
