from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


FINAL_DECISIONS = {"KEEP", "BLUR", "CUT"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_review_regression_manifest(
    *, project_root: Path, queue_paths: list[Path], verify_sources: bool = True,
) -> dict:
    """Convert completed human queues into a versioned regression manifest."""
    root = project_root.resolve(strict=True)
    reports_root = (root / "reports").resolve(strict=True)
    sources: dict[str, dict] = {}
    examples = []
    for raw_path in queue_paths:
        queue_path = raw_path.resolve(strict=True)
        if reports_root != queue_path and reports_root not in queue_path.parents:
            raise ValueError(f"Queue must stay inside reports: {queue_path}")
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        unresolved = [
            item.get("id") for item in queue.get("items", [])
            if item.get("decision") not in FINAL_DECISIONS
        ]
        if unresolved:
            raise ValueError(f"Queue has {len(unresolved)} unresolved items: {queue_path}")
        source = Path(str(queue["source"]["path"])).resolve(strict=True)
        expected_hash = queue["source"].get("sha256")
        if verify_sources and expected_hash and _sha256(source) != expected_hash:
            raise ValueError(f"Source checksum changed: {source}")
        source_key = str(expected_hash or source)
        sources[source_key] = {
            "path": source.relative_to(root).as_posix(),
            "sha256": expected_hash,
            "duration_seconds": queue["source"]["duration_seconds"],
        }
        queue_relative = queue_path.relative_to(root).as_posix()
        for item in queue.get("items", []):
            examples.append({
                "source_key": source_key,
                "review_item_id": item["id"],
                "category": item["category"],
                "start_seconds": item["start_seconds"],
                "end_seconds": item["end_seconds"],
                "expected_decision": item["decision"],
                "labels": item.get("labels", []),
                "region_source_pixels": item.get("decision_region_source_pixels"),
                "queue": queue_relative,
                "evidence": item.get("evidence", []),
            })
    counts = {decision: 0 for decision in sorted(FINAL_DECISIONS)}
    category_counts: dict[str, int] = {}
    for example in examples:
        counts[example["expected_decision"]] += 1
        category = str(example["category"])
        category_counts[category] = category_counts.get(category, 0) + 1
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "purpose": "Regression set built from final human review decisions; never authorizes edits",
        "source_count": len(sources),
        "example_count": len(examples),
        "decision_counts": counts,
        "category_counts": category_counts,
        "sources": sources,
        "examples": examples,
        "safety": {"automatic_edit": False, "human_decisions_are_ground_truth": True},
    }
