from __future__ import annotations

from pathlib import Path


def group_hits(
    hits: list[dict],
    merge_gap_seconds: float,
    padding_seconds: float,
    duration_seconds: float,
) -> list[dict]:
    if not hits:
        return []
    ordered = sorted(hits, key=lambda item: item["timestamp_seconds"])
    groups: list[list[dict]] = [[ordered[0]]]
    for hit in ordered[1:]:
        previous = groups[-1][-1]
        if hit["timestamp_seconds"] - previous["timestamp_seconds"] <= merge_gap_seconds:
            groups[-1].append(hit)
        else:
            groups.append([hit])

    intervals = []
    for group in groups:
        start = max(0.0, group[0]["timestamp_seconds"] - padding_seconds)
        end = min(duration_seconds, group[-1]["timestamp_seconds"] + padding_seconds)
        strongest = max(group, key=lambda item: item["score"])
        intervals.append(
            {
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "max_score": round(strongest["score"], 6),
                "strongest_frame": strongest.get("thumbnail"),
                "sample_count": len(group),
                **(
                    {"predicted_label": strongest["predicted_label"]}
                    if strongest.get("predicted_label")
                    else {}
                ),
                **(
                    {"reason": strongest["reason"]}
                    if strongest.get("reason")
                    else {}
                ),
            }
        )
    return intervals


def compact_interval_thumbnails(
    report_dir: Path, hits: list[dict], intervals: list[dict]
) -> int:
    """Keep only the strongest hit thumbnail referenced by each interval."""
    retained = {
        str(interval["strongest_frame"])
        for interval in intervals
        if interval.get("strongest_frame")
    }
    for hit in hits:
        relative = hit.get("thumbnail")
        if relative and str(relative) not in retained:
            (report_dir / str(relative)).unlink(missing_ok=True)
    return len(retained)


def merge_intervals(intervals: list[dict], maximum_gap_seconds: float) -> list[dict]:
    """Merge nearby review intervals without dropping their strongest evidence."""
    if maximum_gap_seconds < 0:
        raise ValueError("maximum_gap_seconds cannot be negative")
    if not intervals:
        return []
    groups: list[list[dict]] = [[intervals[0]]]
    for interval in intervals[1:]:
        if (
            interval["start_seconds"] - groups[-1][-1]["end_seconds"]
            <= maximum_gap_seconds
        ):
            groups[-1].append(interval)
        else:
            groups.append([interval])
    merged = []
    for group in groups:
        strongest = max(group, key=lambda item: item["max_score"])
        merged.append(
            {
                "start_seconds": group[0]["start_seconds"],
                "end_seconds": group[-1]["end_seconds"],
                "max_score": strongest["max_score"],
                "strongest_frame": strongest.get("strongest_frame"),
                "sample_count": sum(item["sample_count"] for item in group),
                **(
                    {"predicted_label": strongest["predicted_label"]}
                    if strongest.get("predicted_label") else {}
                ),
                **(
                    {"reason": strongest["reason"]}
                    if strongest.get("reason") else {}
                ),
            }
        )
    return merged
