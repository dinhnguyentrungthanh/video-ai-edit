"""Watermark-edge gate (fix/watermark-edges, 2026-10-16): a site watermark read in many runs reaches the
scan's edges.

  jobs    the active revision of each job (default: the 25 jobs of the subtitle-band gate; jobs 130, 134,
          136, 143 and 145 with their real reruns of 2026-10-16), from the stored reports.
  golden  the Golden Set v2 sources with item 6's rescanned text reports (``--scans``: the --work folder
          of ``../text-consensus/rescan.py scan``), scored and compared.

Each job queue is built fresh by ``--before-src`` (41d219d) and by this checkout, with the user's
memories (copied read-only), and rebuilt by both with keep_decisions=True over a copy of the
revision's queue (when the source is still there; the decision carry changed too). Per build: the repeated-watermark cards (time, runs,
the time read, suggestion, decision), the subtitle-band ad tracks a main-list card cites (``weakened``:
cited before and not after) and the card counts. Per job: the cards whose time or region changed, and
for the rebuild the decisions kept and the card each old watermark decision went to. Writes
``<--work>/summary.json``: job ids, counts, seconds and regions only (no OCR text, no names).

  .venv\\Scripts\\python.exe docs\\evidence\\watermark-edges\\edge_gate.py --part jobs --work temp\\wm-e1 --before-src temp\\wm-main\\src
"""
from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("text_consensus_dialogue_gate", HERE.parent / "text-consensus" / "dialogue_gate.py")
dialogue_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dialogue_gate)
gate, rescan, limit_gate = dialogue_gate.gate, dialogue_gate.rescan, dialogue_gate.limit_gate
INSTALL, FFMPEG, TREE = dialogue_gate.INSTALL, dialogue_gate.FFMPEG, dialogue_gate.TREE

sys.path.insert(0, str(TREE / "src"))
from biliflow.subtitle_band import WATERMARK_ROBUST_RUNS as ROBUST_RUNS  # noqa: E402  (this checkout's)
EDGE_TOLERANCE = 0.01  # seconds


def _region(item: dict) -> tuple | None:
    region = item.get("suggested_region_source_pixels")
    return tuple(region[key] for key in ("x", "y", "width", "height")) if isinstance(region, dict) else None


def watermarks(queue: dict) -> list[dict]:
    duration = float((queue.get("source") or {}).get("duration_seconds") or 0.0)
    rows = []
    for item in queue.get("items") or []:
        if item.get("candidate_type") != "repeated_watermark":
            continue
        details = item.get("repeated_watermark") or {}
        start, end = float(item["start_seconds"]), float(item["end_seconds"])
        rows.append({
            "start": start, "end": end, "runs": details.get("sightings"), "read_span": details.get("read_span"),
            "band": dialogue_gate._in_band(item), "suggested": item.get("suggested_decision"),
            "decision": item.get("decision"), "region": item.get("suggested_region_source_pixels"),
            "reaches_start": start <= EDGE_TOLERANCE, "reaches_end": duration - end <= EDGE_TOLERANCE,
        })
    return sorted(rows, key=lambda row: row["start"])


def summarize(queue: dict, ad_tracks: list[str]) -> dict:
    summary = gate.summarize(queue, ad_tracks)
    summary["watermarks"] = watermarks(queue)
    summary["duration_seconds"] = (queue.get("source") or {}).get("duration_seconds")
    return summary


def _card_key(item: dict) -> tuple:
    return (str(item.get("category")), str(item.get("candidate_type")), _region(item),
            round(float(item["start_seconds"]), 3), round(float(item["end_seconds"]), 3))


def changed_cards(before: dict, after: dict) -> dict:
    """Cards of one build and not the other, by category, type, region and time."""
    first = Counter(_card_key(item) for item in before.get("items") or [])
    second = Counter(_card_key(item) for item in after.get("items") or [])
    row = lambda key: {"category": key[0], "type": key[1], "region": key[2], "start": key[3], "end": key[4]}
    return {"before_only": [row(key) for key in sorted((first - second).elements(), key=str)],
            "after_only": [row(key) for key in sorted((second - first).elements(), key=str)]}


def watermark_decisions(old: dict, rebuilt: dict) -> list[dict]:
    """Where each decided watermark card of the user's queue went in the rebuild."""
    items = rebuilt.get("items") or []
    rows = []
    for card in old.get("items") or []:
        if card.get("candidate_type") != "repeated_watermark" or card.get("decision") is None:
            continue
        targets = [item for item in items
                   if item.get("id") == card.get("id") or card.get("id") in (item.get("decision_carried_from") or [])]
        rows.append({"old": {"start": card["start_seconds"], "end": card["end_seconds"], "decision": card["decision"],
                             "runs": (card.get("repeated_watermark") or {}).get("sightings")},
                     "new": [{"start": item["start_seconds"], "end": item["end_seconds"], "decision": item.get("decision"),
                              "type": item.get("candidate_type"), "preserved": item.get("migration_status")}
                             for item in targets]})
    return rows


def gate_revision(work: Path, trees: dict[str, Path], row: dict) -> dict:
    old = gate.read(row["queue"])
    result = {"job": row["job"], "revision": row["revision"], "user_queue_items": len(old.get("items") or [])}
    queues = {}
    for side, tree in trees.items():
        root = work / f"job-{row['job']}-r{row['revision']}" / side
        gate.prepare_root(root, with_memory=True)
        memory = INSTALL / "state" / "ad-text-memory.json"
        if memory.is_file():
            shutil.copyfile(memory, root / "state" / "ad-text-memory.json")
        queue_copy, reports, has_source = gate.copy_job(root, row["job"], row)
        fresh = queue_copy.parent / "gate-fresh" / "review-queue.json"
        job = {"root": str(root), "reports": [str(p) for p in reports], "queue": str(fresh), "ffmpeg": str(FFMPEG),
               "options": gate.build_options(old, memory=True)}
        gate.run_build(tree, job)
        ad_tracks = gate.subtitle_band_ad_tracks(root, reports, TREE / "src")
        queues[side] = gate.read(fresh)
        result[side] = summarize(queues[side], ad_tracks)
        if has_source:
            gate.run_build(tree, {**job, "queue": str(queue_copy), "keep_decisions": True})
            rebuilt = gate.read(queue_copy)
            queues[f"rebuild_{side}"] = rebuilt
            key = "rebuild" if side == "after" else "rebuild_before"
            result[key] = summarize(rebuilt, ad_tracks)
            result[key]["decisions_kept"] = gate.decisions_kept(old, rebuilt)
            result[key]["watermark_decisions"] = watermark_decisions(old, rebuilt)
        result["source_present"] = has_source
    result["changed"] = changed_cards(queues["before"], queues["after"])
    if "rebuild_after" in queues:
        # The decision carry changed too: the rebuilds of both trees, card by card and decision by decision.
        result["rebuild_changed"] = changed_cards(queues["rebuild_before"], queues["rebuild_after"])
        result["rebuild_decisions"] = {
            side: dict(sorted(Counter(str(item.get("decision")) for item in queues[f"rebuild_{side}"]["items"]).items()))
            for side in ("before", "after")
        }
    before, after = result["before"]["subtitle_band_ads"], result["after"]["subtitle_band_ads"]
    result["weakened"] = sorted(set(before["main_list"]) - set(after["main_list"]))
    robust = [card for card in result["after"]["watermarks"] if card["band"] and (card["runs"] or 0) >= ROBUST_RUNS]
    # A flag to look at, not a gate by itself: a robust mark may start more than 150 s in.
    result["robust_band_watermarks_reach_edges"] = all(card["reaches_start"] and card["reaches_end"] for card in robust)
    return result


def _line(result: dict) -> str:
    before, after, rebuild = result["before"], result["after"], result.get("rebuild")
    marks = lambda side: [(card["start"], card["end"], card["runs"], card["suggested"]) for card in side["watermarks"]]
    return (f"job {result['job']} r{result['revision']}: items {before['items']} -> {after['items']}, watermarks "
            f"{marks(before)} -> {marks(after)}, film {after['duration_seconds']}, changed "
            f"{len(result['changed']['before_only'])}/{len(result['changed']['after_only'])}, band ads main "
            f"{len(before['subtitle_band_ads']['main_list'])} -> {len(after['subtitle_band_ads']['main_list'])} "
            f"(weakened {len(result['weakened'])})"
            + (f", rebuild items {result['rebuild_before']['items']} -> {rebuild['items']} (user queue "
               f"{result['user_queue_items']}), lost {len(rebuild['decisions_kept']['lost'])} of "
               f"{rebuild['decisions_kept']['old_decided']}, decisions {result['rebuild_decisions']['before']} -> "
               f"{result['rebuild_decisions']['after']}, rebuild changed {len(result['rebuild_changed']['before_only'])}/"
               f"{len(result['rebuild_changed']['after_only'])}, watermark decisions "
               f"{[(row['old']['decision'], [(new['decision'], new['end']) for new in row['new']]) for row in rebuild['watermark_decisions']]}"
               if rebuild else ", no source"))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--part", required=True, choices=("jobs", "golden"))
    parser.add_argument("--scans", help="the --work folder of ../text-consensus/rescan.py scan (golden)")
    parser.add_argument("--work", required=True, help="new folder under <install>/temp")
    parser.add_argument("--before-src", required=True, help="src folder that builds the \"before\" queues")
    parser.add_argument("--job", type=int, action="append", help="limit the jobs part to these job ids")
    args = parser.parse_args()
    trees = {"before": Path(args.before_src).resolve(strict=True), "after": TREE / "src"}
    work = rescan._new_folder(args.work)
    summary: dict = {"part": args.part, "trees": {key: str(value) for key, value in trees.items()}, "jobs": []}
    if args.part == "jobs":
        for row in dialogue_gate.revisions(args.job or dialogue_gate.STORED_JOBS, active_only=True):
            summary["jobs"].append(gate_revision(work, trees, row))
            print(_line(summary["jobs"][-1]), flush=True)
    else:
        scans = Path(args.scans).resolve(strict=True) / "scans"
        reports = {scan.name: limit_gate.after_report(gate.read(scan / "reports" / "scan" / "text" / "text-scan.json"))
                   for scan in sorted(scans.iterdir()) if scan.name.startswith("golden-")}
        previews = {label: scans / label / "reports" / "scan" / "text" / "text-previews" for label in reports}
        golden = {label: {"before": report, "after": report} for label, report in reports.items()}
        summary["golden"] = limit_gate.gate_golden(work, golden, previews, trees)
    gate.write(work / "summary.json", summary)


if __name__ == "__main__":
    main()
