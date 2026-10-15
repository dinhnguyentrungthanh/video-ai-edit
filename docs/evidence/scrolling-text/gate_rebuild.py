"""fix/ticker-subtitle gate, parts 1 and 2: review queues rebuilt from copies of real scan reports.

    jobs    For each job id: its report folder and review queue copied into --work, then
            built fresh by the baseline tree and by the candidate tree, and rebuilt by the
            candidate with keep_decisions=True over the copy of the user's queue. A job
            whose source is gone gets an empty placeholder source (fresh builds only).
            Part 2 adds, per build: the repeated-watermark cards, the subtitle lines moved
            to the optional list, and which subtitle-band tracks holding ad evidence (a web
            address, phone number, @handle or policy hit, by the candidate tree's own
            functions) a main-list card cites; ``subtitle_band_ads.weakened`` lists every
            such track the baseline's main list cited and the candidate's does not.
    golden  The Golden Set v2 baseline's nine trial folders copied into --work, each queue
            built fresh by both trees (benchmark settings: no studio-logo memory), scored
            with evaluate_golden.py score, then compared with compare --gate detector. A
            report whose clip copy was in a removed temp root reads the same clip in
            annotations/golden/v2/media (the report's own input_sha256 names it).

Reads, never writes: the real control-center.sqlite3 (read-only URI), the report
folders, state/studio-logo-memory.json and state/studio-logo-frames (copied), the
sources (FFmpeg reads short windows for platform and ending cards) and
annotations/golden (labels, read by the scorer). Writes only under --work, which must
be a new folder in <install>/temp. Each build runs in its own Python process with one
code tree on PYTHONPATH. Prints job ids, counts and seconds; no film names, file names,
OCR text or links.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

INSTALL = Path(r"E:\DungChung\BiliFlow")
FFMPEG = INSTALL / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
GOLDEN_BASELINE = INSTALL / "reports" / "benchmarks" / "golden-v2-baseline-20261013-153857"

BUILD_PROGRAM = r"""
import json, sys
from pathlib import Path
import psutil
psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
import biliflow
from biliflow.review_workflow import build_review_queue
job = json.loads(sys.argv[1])
options = dict(job["options"])
if job.get("keep_decisions"):
    options["keep_decisions"] = True
queue = build_review_queue(project_root=Path(job["root"]), report_paths=[Path(p) for p in job["reports"]],
                           queue_path=Path(job["queue"]), ffmpeg_path=Path(job["ffmpeg"]), **options)
print(json.dumps({"code": biliflow.__file__, "status": queue["status"]}))
"""


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def _environment(tree: Path, temp: str | None = None) -> dict:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("BILIFLOW_")}
    environment.update(PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    if temp:
        environment.update(TEMP=temp, TMP=temp)
    return environment


def run_build(tree: Path, job: dict) -> dict:
    done = subprocess.run([sys.executable, "-c", BUILD_PROGRAM, json.dumps(job)], env=_environment(tree, job["root"]),
                          capture_output=True, text=True, encoding="utf-8", timeout=3600)
    if done.returncode:
        raise RuntimeError(done.stderr[-1500:])
    result = json.loads(done.stdout.strip().splitlines()[-1])
    if tree not in Path(result.pop("code")).resolve().parents:
        raise RuntimeError(f"biliflow was not imported from {tree}")
    return result


# --- Summaries (job ids, counts and seconds only) -------------------------------------


def _frame_height(item: dict) -> int:
    size = item.get("source_frame_size")
    return int(size[1]) if isinstance(size, list) and len(size) == 2 else 0


def _band(item: dict, region_key: str = "suggested_region_source_pixels") -> str | None:
    region, height = item.get(region_key), _frame_height(item)
    if not isinstance(region, dict) or not height:
        return None
    centre = (region["y"] + region["height"] / 2) / height
    return "top" if centre < 0.30 else "bottom" if centre > 0.70 else "middle"


def _scrolling_card(item: dict) -> dict:
    details = item["scrolling_text"]
    return {
        "start": item["start_seconds"], "end": item["end_seconds"],
        "first_read": details["first_read_seconds"], "last_read": details["last_read_seconds"],
        "pieces": details["track_count"], "band": details["band"], "direction": details.get("direction"),
        "web_address": bool(details.get("web_address")),
        "suggested": item.get("suggested_decision"), "decision": item.get("decision"),
        "carried_from": len(item.get("decision_carried_from") or []),
    }


def _watermark_card(item: dict) -> dict:
    details = item.get("repeated_watermark") or {}
    return {
        "start": item["start_seconds"], "end": item["end_seconds"],
        "moments": len(item.get("detected_intervals") or []), "sightings": details.get("sightings"),
        "region": item.get("suggested_region_source_pixels"), "band": _band(item),
        "suggested": item.get("suggested_decision"), "decision": item.get("decision"),
        "carried_from": len(item.get("decision_carried_from") or []),
    }


def _cited_tracks(items: list[dict]) -> set[str]:
    """``<report>#track:<id>`` of every track a card cites, also through absorbed supporting cards."""
    refs: set[str] = set()
    for item in items:
        refs.update(str(ref) for ref in item.get("source_candidate_refs") or [])
        for support in item.get("supporting_detections") or []:
            refs.update(str(ref) for ref in support.get("source_candidate_refs") or [])
    return {ref.replace("\\", "/") for ref in refs if "#track:" in ref}


def subtitle_band_ad_tracks(root: Path, reports: list[Path], candidate: Path) -> list[str]:
    """``<report>#track:<id>`` of every subtitle-zone track holding ad evidence."""
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
    from biliflow.ad_text_patterns import ad_text_tokens
    from biliflow.scrolling_text import web_address_tokens
    found = []
    for report in reports:
        payload = read(report)
        relative = report.relative_to(root).as_posix()
        for index, track in enumerate(payload.get("tracks") or []):
            texts = [str(text) for text in track.get("sample_text") or []]
            if track.get("zone") == "subtitle" and (
                list(track.get("policy_hits") or [])
                or any(web_address_tokens(text) or ad_text_tokens(text) or "@" in text for text in texts)
            ):
                found.append(f"{relative}#track:{track.get('track_id', index)}")
    return found


def summarize(queue: dict, ad_tracks: list[str] | None = None) -> dict:
    items, advisory = queue.get("items") or [], queue.get("advisory_items") or []
    suggested_blur = [item for item in items if item.get("suggested_decision") == "BLUR"]
    scrolling = [item for item in items if item.get("candidate_type") == "scrolling_text"]
    watermarks = [item for item in items if item.get("candidate_type") == "repeated_watermark"]
    main_refs = _cited_tracks(items)
    band_ads = {
        "tracks": len(ad_tracks or []),
        "main_list": sorted(ref for ref in ad_tracks or [] if ref in main_refs),
    }
    return {
        "repeated_watermark_cards": [_watermark_card(item) for item in sorted(watermarks, key=lambda v: v["start_seconds"])],
        "subtitle_line_cards": sum(1 for item in advisory if isinstance(item.get("subtitle_line"), dict)),
        "subtitle_band_ads": band_ads,
        "items": len(items),
        "advisory_items": len(advisory),
        "pending": sum(1 for item in items if item.get("decision") is None),
        "decided": {decision: sum(1 for item in items if item.get("decision") == decision)
                    for decision in ("KEEP", "BLUR", "CUT", "NEEDS_MORE_CONTEXT")},
        "text_items": sum(1 for item in items if item.get("category") == "text"),
        "suggested_blur": len(suggested_blur),
        "suggested_blur_bottom_band": sum(1 for item in suggested_blur if _band(item) == "bottom"),
        "bottom_band_items": sum(1 for item in items if _band(item) == "bottom"),
        "web_address_items": sum(1 for item in items if isinstance(item.get("web_address"), dict)),
        "advisory_text_items": sum(1 for item in advisory if item.get("category") == "text"),
        "scrolling_cards": [_scrolling_card(item) for item in sorted(scrolling, key=lambda v: v["start_seconds"])],
    }


def decisions_kept(old: dict, new: dict) -> dict:
    """Every decided card of the user's queue is in the rebuilt one, by its id or carried."""
    new_items = new.get("items") or []
    by_id = {item.get("id"): item for item in new_items}
    lost = []
    for item in old.get("items") or []:
        decision = item.get("decision")
        if decision is None:
            continue
        same = by_id.get(item.get("id"))
        carried = [card for card in new_items if item.get("id") in (card.get("decision_carried_from") or [])]
        if (same is not None and same.get("decision") == decision) or any(
            card.get("decision") == decision for card in carried
        ):
            continue
        lost.append({"start": item.get("start_seconds"), "end": item.get("end_seconds"),
                     "category": item.get("category"), "decision": decision})
    return {"old_decided": sum(1 for item in old.get("items") or [] if item.get("decision") is not None),
            "lost": lost}


def old_blur_cards_in_band(queue: dict, band: str) -> list[dict]:
    return [
        {"start": item["start_seconds"], "end": item["end_seconds"]}
        for item in queue.get("items") or []
        if item.get("decision") == "BLUR" and item.get("category") == "text"
        and _band(item, "decision_region_source_pixels") == band
    ]


# --- jobs ----------------------------------------------------------------------------


def job_rows(job_ids: list[int]) -> dict[int, dict]:
    database = (INSTALL / "state" / "control-center.sqlite3").as_posix()
    connection = sqlite3.connect(f"file:///{database}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            f"select id, active_queue_path, source_path from jobs where id in ({','.join('?' * len(job_ids))})",
            job_ids,
        ).fetchall()
    finally:
        connection.close()
    return {int(job_id): {"queue": INSTALL / queue, "source": Path(source)} for job_id, queue, source in rows}


def prepare_root(root: Path, with_memory: bool) -> None:
    for folder in ("input", "work", "output", "state", "reports/jobs"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    if with_memory and not (root / "state" / "studio-logo-memory.json").exists():
        shutil.copyfile(INSTALL / "state" / "studio-logo-memory.json", root / "state" / "studio-logo-memory.json")
        shutil.copytree(INSTALL / "state" / "studio-logo-frames", root / "state" / "studio-logo-frames")


def copy_job(root: Path, job_id: int, row: dict) -> tuple[Path, list[Path], bool]:
    """(queue copy, report copies, the real source exists) in ``root``."""
    real_queue = row["queue"]
    target = root / real_queue.parent.relative_to(INSTALL)
    shutil.copytree(real_queue.parent, target)
    reports = [root / Path(relative) for relative in read(real_queue).get("reports") or []]
    has_source = row["source"].is_file()
    if not has_source:
        placeholder = root / "input" / f"job-{job_id}.mp4"
        placeholder.write_bytes(b"")
        for report in reports:
            payload = read(report)
            payload["input"] = str(placeholder)
            write(report, payload)
    return target / real_queue.name, reports, has_source


def build_options(queue: dict, *, memory: bool) -> dict:
    scope = queue.get("detection_scope") or {}
    return {
        "selected_detectors": list(scope.get("selected") or []) if scope.get("explicit") else None,
        "content_style": queue.get("content_style"),
        "use_studio_logo_memory": memory,
    }


def gate_job(work: Path, trees: dict[str, Path], job_id: int, row: dict) -> dict:
    result = {"job": job_id}
    old = read(row["queue"])
    for side, tree in trees.items():
        root = work / side
        prepare_root(root, with_memory=True)
        queue_copy, reports, has_source = copy_job(root, job_id, row)
        result["source_present"] = has_source
        fresh = queue_copy.parent / "gate-fresh" / "review-queue.json"
        job = {"root": str(root), "reports": [str(p) for p in reports], "queue": str(fresh), "ffmpeg": str(FFMPEG),
               "options": build_options(old, memory=True)}
        run_build(tree, job)
        ad_tracks = subtitle_band_ad_tracks(root, reports, trees["candidate"])
        result[f"{side}_fresh"] = summarize(read(fresh), ad_tracks)
        if side == "candidate" and has_source:
            run_build(tree, {**job, "queue": str(queue_copy), "keep_decisions": True})
            rebuilt = read(queue_copy)
            result["candidate_rebuild"] = summarize(rebuilt, ad_tracks)
            result["candidate_rebuild"]["decision_carry"] = rebuilt.get("decision_carry")
            result["candidate_rebuild"]["decisions_kept"] = decisions_kept(old, rebuilt)
    base_ads = result["baseline_fresh"]["subtitle_band_ads"]
    cand_ads = result["candidate_fresh"]["subtitle_band_ads"]
    cand_ads["weakened"] = sorted(set(base_ads["main_list"]) - set(cand_ads["main_list"]))
    result["user_queue"] = summarize(old)
    result["user_queue"]["old_text_blur_top_band"] = old_blur_cards_in_band(old, "top")
    return result


def _new_work(path: str) -> Path:
    work = Path(path).resolve()
    if work.exists() or INSTALL / "temp" not in work.parents:
        raise SystemExit("--work must be a new folder under <install>/temp")
    return work


def jobs_command(args) -> None:
    work = _new_work(args.work)
    trees = {"baseline": Path(args.baseline).resolve(strict=True), "candidate": Path(args.candidate).resolve(strict=True)}
    rows = job_rows(args.job)
    results = [gate_job(work, trees, job_id, rows[job_id]) for job_id in args.job]
    write(Path(args.output), {"trees": {side: str(tree) for side, tree in trees.items()}, "jobs": results})
    for result in results:
        base, cand = result["baseline_fresh"], result["candidate_fresh"]
        rebuild = result.get("candidate_rebuild")
        print(f"job {result['job']}: items {base['items']} -> {cand['items']}, suggested BLUR "
              f"{base['suggested_blur']} -> {cand['suggested_blur']} (bottom {base['suggested_blur_bottom_band']} -> "
              f"{cand['suggested_blur_bottom_band']}), scrolling {len(cand['scrolling_cards'])}, watermark cards "
              f"{len(cand['repeated_watermark_cards'])}, subtitle lines moved {cand['subtitle_line_cards']}, "
              f"subtitle-band ad tracks in main list {len(base['subtitle_band_ads']['main_list'])} -> "
              f"{len(cand['subtitle_band_ads']['main_list'])} of {cand['subtitle_band_ads']['tracks']} "
              f"(weakened {len(cand['subtitle_band_ads']['weakened'])})"
              + (f", rebuild pending {rebuild['pending']}, lost {len(rebuild['decisions_kept']['lost'])}"
                 if rebuild else ""), flush=True)


# --- golden --------------------------------------------------------------------------


def _point_to_golden_clip(report: Path) -> None:
    """A trial's clip copy in a removed temp root: read the same clip in annotations/golden/v2/media."""
    payload = read(report)
    if Path(str(payload["input"])).is_file():
        return
    clip = INSTALL / "annotations" / "golden" / "v2" / "media" / Path(str(payload["input"])).name
    if not clip.is_file():
        raise SystemExit(f"no source for {report.relative_to(report.parents[3])}")
    payload["input"] = str(clip)
    write(report, payload)


def _golden_queues(root: Path, tree: Path) -> list[Path]:
    queues = []
    for value in read(GOLDEN_BASELINE / "scorecard-v2.json")["queues"].values():
        folder = INSTALL / Path(value["path"]).parent
        target = root / folder.relative_to(INSTALL)
        shutil.copytree(folder, target)
        original = read(folder / "review-queue.json")
        reports = [root / Path(p) for p in original.get("reports") or []]
        for report in reports:
            _point_to_golden_clip(report)
        queue = target / "gate-fresh" / "review-queue.json"
        run_build(tree, {
            "root": str(root), "reports": [str(report) for report in reports],
            "queue": str(queue), "ffmpeg": str(FFMPEG), "options": build_options(original, memory=False),
        })
        queues.append(queue)
    return queues


def golden_command(args) -> None:
    work = _new_work(args.work)
    trees = {"baseline": Path(args.baseline).resolve(strict=True), "candidate": Path(args.candidate).resolve(strict=True)}
    script = str(trees["candidate"].parent / "scripts" / "evaluate_golden.py")
    cards = {}
    for side, tree in trees.items():
        root = work / side
        prepare_root(root, with_memory=False)
        command = [sys.executable, script, "score", "--root", str(root),
                   "--golden-root", str(INSTALL / "annotations" / "golden"), "--set", "v2", "--no-frames",
                   "--tag", f"ticker-{side}"]
        for queue in _golden_queues(root, tree):
            command += ["--queue", str(queue)]
        done = subprocess.run(command, env=_environment(trees["candidate"]), capture_output=True, text=True,
                              encoding="utf-8", timeout=3600)
        if done.returncode:
            raise RuntimeError(done.stderr[-1500:])
        [card] = sorted((root / "reports" / "benchmarks").glob(f"golden-*ticker-{side}*/scorecard-v2.json"))
        cards[side] = card
        print(f"{side}: {card}", flush=True)
    for label, baseline in (("baseline_tree", cards["baseline"]), ("v2_baseline_tag", GOLDEN_BASELINE / "scorecard-v2.json")):
        done = subprocess.run([sys.executable, script, "compare", "--baseline", str(baseline),
                               "--candidate", str(cards["candidate"]), "--gate", "detector"],
                              env=_environment(trees["candidate"]), capture_output=True, text=True,
                              encoding="utf-8", timeout=600)
        print(f"== compare --gate detector against {label}: exit {done.returncode}\n{done.stdout[-6000:]}", flush=True)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("jobs", "golden"):
        command = sub.add_parser(name)
        command.add_argument("--baseline", required=True, help="src folder of the baseline tree (git archive)")
        command.add_argument("--candidate", required=True, help="src folder of the candidate tree")
        command.add_argument("--work", required=True, help="new folder under <install>/temp")
        if name == "jobs":
            command.add_argument("--job", type=int, action="append", required=True)
            command.add_argument("--output", required=True)
    args = parser.parse_args()
    (jobs_command if args.command == "jobs" else golden_command)(args)


if __name__ == "__main__":
    main()
