from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    root = Path(__file__).resolve().parents[1]

    adult_source = root / "annotations" / "adult_poc_benchmark.csv"
    with adult_source.open("r", encoding="utf-8-sig", newline="") as handle:
        adult_rows = list(csv.DictReader(handle))
    known_hashes = {row["sha256"] for row in adult_rows}
    adult_report_dir = root / "reports" / "field-conan-adult-animation"
    adult_report = json.loads(
        (adult_report_dir / "scan.json").read_text(encoding="utf-8")
    )
    adult_destination = (
        root / "benchmarks" / "adult-poc" / "negative" / "animation-field-conan-v2"
    )
    adult_destination.mkdir(parents=True, exist_ok=True)
    added_adult = 0
    for interval in adult_report["intervals"]:
        source = adult_report_dir / interval["strongest_frame"]
        digest = sha256(source)
        if digest in known_hashes:
            continue
        timestamp = float(interval["start_seconds"])
        target = adult_destination / f"conan-{timestamp:08.1f}s.jpg"
        shutil.copy2(source, target)
        added_adult += 1
        known_hashes.add(digest)
        adult_rows.append(
            {
                "sample_id": f"anime-field-neg-{added_adult:03d}",
                "image_path": target.relative_to(root).as_posix(),
                "style": "animation",
                "label": "0",
                "source": "Conan full-film field scan",
                "license": "local test input",
                "sha256": digest,
                "notes": (
                    "verified non-explicit interval strongest frame; "
                    f'{interval["start_seconds"]}-{interval["end_seconds"]}s'
                ),
            }
        )
    adult_v2 = root / "annotations" / "adult_poc_benchmark_v2.csv"
    with adult_v2.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(adult_rows[0]))
        writer.writeheader()
        writer.writerows(adult_rows)

    violence_labels = {
        161.0: "NEGATIVE",
        1395.5: "POSITIVE",
        1480.5: "NEGATIVE",
        1856.0: "POSITIVE",
        2666.5: "NEGATIVE",
        2762.0: "NEGATIVE",
        2933.0: "POSITIVE",
        2977.0: "POSITIVE",
        3007.0: "POSITIVE",
        3091.0: "NEEDS_MORE_CONTEXT",
        3169.0: "POSITIVE",
        3178.5: "NEEDS_MORE_CONTEXT",
        4359.5: "NEEDS_MORE_CONTEXT",
        4867.5: "POSITIVE",
        4888.5: "NEEDS_MORE_CONTEXT",
        4897.0: "NEEDS_MORE_CONTEXT",
        4905.5: "NEGATIVE",
        5020.0: "NEGATIVE",
        5844.5: "NEGATIVE",
    }
    violence_report_dir = (
        root / "reports" / "field-conan-animation-safety-v3" / "violence"
    )
    violence_report = json.loads(
        (violence_report_dir / "scan.json").read_text(encoding="utf-8")
    )
    violence_destination = root / "benchmarks" / "violence-field-audit-v3"
    violence_destination.mkdir(parents=True, exist_ok=True)
    violence_rows = []
    for index, interval in enumerate(violence_report["intervals"], start=1):
        start = float(interval["start_seconds"])
        source = violence_report_dir / interval["strongest_frame"]
        target = violence_destination / f"candidate-{index:03d}-{start:08.1f}s.jpg"
        shutil.copy2(source, target)
        label = violence_labels[start]
        violence_rows.append(
            {
                "sample_id": f"violence-field-{index:03d}",
                "image_path": target.relative_to(root).as_posix(),
                "start_seconds": interval["start_seconds"],
                "end_seconds": interval["end_seconds"],
                "score": interval["max_score"],
                "predicted_label": interval.get("predicted_label", ""),
                "review_label": label,
                "review_source": "provisional visual field audit",
                "sha256": sha256(target),
            }
        )
    violence_manifest = root / "annotations" / "violence_field_audit_v3.csv"
    with violence_manifest.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(violence_rows[0]))
        writer.writeheader()
        writer.writerows(violence_rows)

    print(
        json.dumps(
            {
                "adult_manifest": str(adult_v2),
                "adult_samples": len(adult_rows),
                "adult_field_negatives_added": added_adult,
                "violence_manifest": str(violence_manifest),
                "violence_labels": {
                    label: sum(row["review_label"] == label for row in violence_rows)
                    for label in ("POSITIVE", "NEGATIVE", "NEEDS_MORE_CONTEXT")
                },
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
