"""Golden Set v2 (docs/handoff/G-golden-clips.md): the v1 + v1.1 segments of job 39 (L) on clips, those of
jobs 37 and 38 (H) on their original films (the user's choice of 2026-10-13: H clips are off the sampling
grid and lose whole-film context). Only drop-clips and migrate --replace delete, after their checks.

  plan        Show the clips of job 39 and of the unscored benchmark window, with their grid offsets.
  cut         Re-hash the films once, then stream-copy each clip into <staging>/v2/media (an existing
              clip is kept after its SHA-256 check, never overwritten) and record <staging>/v2/clips.json.
  verify      Gate (a): decode every clip and the same span of its film (from the keyframe before the
              clip); every frame must be bit-identical at clip pts + origin pts.
  drop-clips  With --confirm, delete the staging clips that are no longer planned (after their SHA-256
              check) and their records; without it only prints them.
  migrate     Write <staging>/v2/segments.json, events.json and label-history.jsonl from v1 + v1.1
              (only read), the clips and the kept films; the labeling page's old suggestions of clip
              segments move into clip time. --replace rewrites an earlier migration's files, only while
              nobody has labelled v2 yet and after a verified backup zip in annotations/.
  gate-score  Gate (b1): score v1 + v1.1 with the film queues of a scorecard and v2 with the same
              queues (moved into clip time for clips); every metric, label and item must be identical.
  gate-diff   Gate (b2): one code version on the films (v1 + v1.1 scorecard) and on the clips (v2
              scorecard); lists every label and item count that changed, for the user to approve.
  activate    For the user only: rename <staging>/v2 to annotations/golden/v2 and move v1 and v1.1 to
              annotations/golden-retired after a dated backup zip; without --confirm it only prints.
Films, v1 and v1.1 are only read. Writes: <staging>, reports/evidence/golden-v2,
reports/benchmarks/golden-v2/prefill, docs/evidence/golden-v2 and, on activate, annotations.
--root is the BiliFlow folder of the films and labels (default: this checkout).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from fractions import Fraction
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
if str(CODE / "src") not in sys.path:
    sys.path.insert(0, str(CODE / "src"))

from biliflow import golden_clips as gc  # noqa: E402
from biliflow.golden_set import (  # noqa: E402
    file_sha256, is_clip, load_golden_sets, read_json, source_file, validate_event, write_json_atomic,
)

SOURCE_SETS = ("v1", "v1.1")
# Unscored benchmark window (the user's choice, 2026-10-13): job 39, 5400-5550 s, used by older
# live-safety and exact-speedup excerpts.
BENCH_WINDOWS = ((39, 5400.0, 5550.0),)
# Jobs whose segments go on clips; the other Golden films stay whole v2 sources (keys j<job>).
CLIP_JOBS = (39,)
EVIDENCE = "golden-v2"


def paths(args) -> dict[str, Path]:
    root = Path(args.root or CODE).resolve()
    staging = Path(args.staging).resolve() if args.staging else root / "annotations/golden-v2-staging"
    return {"root": root, "golden": root / "annotations/golden", "staging": staging, "set": staging / "v2",
            "clips": staging / "v2/clips.json", "ffmpeg": root / "tools/ffmpeg/bin/ffmpeg.exe",
            "ffprobe": root / "tools/ffmpeg/bin/ffprobe.exe", "profiles": root / "config/processing_profiles.json",
            "evidence": root / "reports/evidence" / EVIDENCE, "docs_evidence": CODE / "docs/evidence" / EVIDENCE}


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _films(p) -> tuple[dict, dict, dict]:
    return load_golden_sets(p["golden"], list(SOURCE_SETS))


def _windows(manifest: dict) -> list[dict]:
    rows = [{"source": s["source"], "job_id": manifest["sources"][s["source"]]["job_id"], "start": s["start_seconds"],
             "end": s["end_seconds"], "segment_id": s["id"], "scored": True} for s in manifest["segments"]]
    by_job = {source["job_id"]: key for key, source in manifest["sources"].items()}
    rows += [{"source": by_job[job], "job_id": job, "start": start, "end": end, "segment_id": None, "scored": False}
             for job, start, end in BENCH_WINDOWS]
    return [row for row in rows if row["job_id"] in CLIP_JOBS]


def _kept_films(manifest: dict) -> dict[str, str]:
    """v1/v1.1 source key -> v2 key of every Golden film that stays whole."""
    return {key: f"j{source['job_id']}" for key, source in manifest["sources"].items()
            if source["job_id"] not in CLIP_JOBS}


def _plan(p, full_hash: bool = False) -> tuple[list[dict], dict, dict]:
    manifest, _, _ = _films(p)
    periods = gc.sampling_periods(read_json(p["profiles"]))
    films, indexes = {}, {}
    for key in [k for k, source in manifest["sources"].items() if source["job_id"] in CLIP_JOBS]:
        if full_hash:
            print(f"verifying the SHA-256 of {key}...", flush=True)
        films[key] = source_file(p["root"], manifest, key, full_hash=full_hash)
        indexes[key] = gc.probe_keyframes(p["ffprobe"], films[key])
    durations = {key: manifest["sources"][key]["duration_seconds"] for key in films}
    return gc.plan_clips(_windows(manifest), indexes, durations, periods), films, indexes


def _clip_json(clip: dict) -> dict:
    return {key: (str(value) if isinstance(value, Fraction) else value) for key, value in clip.items()}


def _clip_from_json(row: dict) -> dict:
    clip = dict(row)
    base = Fraction(row["time_base"])
    clip["origin_seconds"] = row["origin_pts"] * base
    clip["end_seconds"] = None if row["end_pts"] is None else row["end_pts"] * base
    return clip


def _describe(clip: dict) -> str:
    end = "end of film" if clip["end_seconds"] is None else f"{float(clip['end_seconds']):.3f}"
    names = ",".join(w["segment_id"] or f"bench {w['start']:.0f}-{w['end']:.0f}" for w in clip["windows"])
    grid = "on grid" if clip["on_grid"] else "off grid " + json.dumps(clip["grid_offsets_seconds"])
    return f"{clip['key']:10} {names:12} {float(clip['origin_seconds']):10.3f} -> {end:>11}  {grid}"


def cmd_plan(args) -> int:
    clips, _, _ = _plan(paths(args))
    for clip in clips:
        print(_describe(clip), flush=True)
    return 0


def cmd_cut(args) -> int:
    p = paths(args)
    clips, films, indexes = _plan(p, full_hash=True)
    record = read_json(p["clips"]) if p["clips"].exists() else {"created_at": gc.now_iso(), "clips": {}}
    record["plan"] = [_clip_json(clip) for clip in clips]
    for clip in clips:
        target = p["set"] / "media" / f"{clip['key']}.mp4"
        info = record["clips"].get(clip["key"])
        if target.exists():
            if not info or file_sha256(target) != info["sha256"]:
                raise SystemExit(f"{target} exists without a matching record; not touching it")
            print(f"{clip['key']}: cut earlier, SHA-256 checked", flush=True)
            continue
        print(f"{clip['key']}: cutting", flush=True)
        gc.cut_clip(p["ffmpeg"], films[clip["source"]], indexes[clip["source"]], clip["origin_pts"],
                    clip["end_pts"], target)
        probe = gc.probe_clip(p["ffprobe"], target)
        if not _starts_at_zero(probe):  # an earlier audio packet would shift the video and the sampling grid
            target.unlink()  # this run's own file in the staging folder
            gc.cut_clip(p["ffmpeg"], films[clip["source"]], indexes[clip["source"]], clip["origin_pts"],
                        clip["end_pts"], target, audio=False)
            probe = gc.probe_clip(p["ffprobe"], target)
            if not _starts_at_zero(probe):
                raise SystemExit(f"{clip['key']}: video does not start at 0 ({probe})")
        record["clips"][clip["key"]] = dict(probe, path=f"media/{clip['key']}.mp4", sha256=file_sha256(target),
                                            size_bytes=target.stat().st_size, cut_at=gc.now_iso())
        write_json_atomic(p["clips"], record)
    write_json_atomic(p["clips"], record)  # the current plan, also when every clip was cut earlier
    total = sum(record["clips"][clip["key"]]["size_bytes"] for clip in clips)
    print(f"{len(clips)} clips, {total / 1e9:.2f} GB in {p['set'] / 'media'}", flush=True)
    return 0


def _starts_at_zero(probe: dict) -> bool:
    return float(probe["format_start_time"]) == 0.0 and float(probe["video_start_time"]) == 0.0


def _cut_clips(p) -> tuple[list[dict], dict]:
    record = read_json(p["clips"])
    clips = [_clip_from_json(row) for row in record["plan"]]
    missing = [clip["key"] for clip in clips if clip["key"] not in record["clips"]]
    if missing:
        raise SystemExit(f"Clips not cut yet: {', '.join(missing)}")
    return clips, record["clips"]


def cmd_drop_clips(args) -> int:
    """Delete the staging clips that are no longer planned (the user's choice: H stays on its films)."""
    p = paths(args)
    record = read_json(p["clips"])
    planned = {row["key"] for row in record["plan"]}
    dropped = [key for key in record["clips"] if key not in planned]
    for key in dropped:
        info = record["clips"][key]
        path = p["set"] / info["path"]
        if path.parent != p["set"] / "media" or path.name != f"{key}.mp4":
            raise SystemExit(f"{path} is not a staging clip")
        if path.exists() and file_sha256(path) != info["sha256"]:
            raise SystemExit(f"{path} differs from its record; not touching it")
        print(f"{'DELETE' if args.confirm else 'would delete'} {path} ({info['size_bytes'] / 1e6:.0f} MB)", flush=True)
    if not args.confirm or not dropped:
        return 0
    for key in dropped:
        info = record["clips"].pop(key)
        (p["set"] / info["path"]).unlink(missing_ok=True)
        record.setdefault("dropped", []).append({"key": key, "sha256": info["sha256"], "size_bytes": info["size_bytes"],
                                                 "dropped_at": gc.now_iso()})
        write_json_atomic(p["clips"], record)
    print(f"dropped {len(dropped)} clips; {len(record['clips'])} left", flush=True)
    return 0


def _write_evidence(p, name: str, full: dict, summary: dict) -> None:
    for folder, payload in ((p["evidence"], full), (p["docs_evidence"], summary)):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {p['evidence'] / name} and {p['docs_evidence'] / name}", flush=True)


def cmd_verify(args) -> int:
    p = paths(args)
    clips, infos = _cut_clips(p)
    manifest, _, _ = _films(p)
    films = {key: source_file(p["root"], manifest, key) for key in {clip["source"] for clip in clips}}
    indexes = {key: gc.probe_keyframes(p["ffprobe"], path) for key, path in films.items()}

    def check(clip: dict) -> tuple[str, dict]:
        clip_path = p["set"] / infos[clip["key"]]["path"]
        if file_sha256(clip_path) != infos[clip["key"]]["sha256"]:
            return clip["key"], {"ok": False, "error": "clip SHA-256 changed"}
        result = gc.verify_clip(p["ffmpeg"], films[clip["source"]], clip_path, indexes[clip["source"]],
                                clip["origin_pts"], clip["end_pts"])
        print(f"{clip['key']}: {'OK' if result['ok'] else 'FAIL'} {result}", flush=True)
        return clip["key"], result

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = dict(pool.map(check, clips))
    rows = {clip["key"]: dict(results[clip["key"]], job_id=clip["job_id"], scored=clip["scored"],
                              segments=[w["segment_id"] for w in clip["windows"] if w["segment_id"]],
                              on_sampling_grid=clip["on_grid"], grid_offsets_seconds=clip["grid_offsets_seconds"])
            for clip in clips}
    passed = all(row["ok"] for row in rows.values())
    payload = {"gate": "a", "checked_at": gc.now_iso(), "passed": passed, "clips": rows}
    _write_evidence(p, f"gate-a-{_stamp()}.json", payload, payload)
    print("GATE A " + ("PASSED" if passed else "FAILED"), flush=True)
    return 0 if passed else 1


def _approximate(p, parts: dict) -> set[str]:
    """Dense logo windows of the sets' prefill suggestions (as evaluate_golden reads them)."""
    found: set[str] = set()
    for name, part in parts.items():
        found |= set(part["manifest"].get("approximate_suggestions") or ())
        path = p["root"] / f"reports/benchmarks/golden-{name}/prefill/suggestions.json"
        if path.exists():
            found |= {s["id"] for s in read_json(path)["suggestions"]
                      if s.get("tier") == "dense" and s.get("candidate_type") == "dense_logo_window"}
    return found


def _tree_hashes(folder: Path) -> dict[str, str]:
    return {path.relative_to(folder).as_posix(): file_sha256(path) for path in sorted(folder.rglob("*")) if path.is_file()}


def _backup_unlabelled(p, files: list[Path]) -> None:
    """Before --replace: only an untouched migration (revision 0, one history line) is replaced, after a
    dated backup zip in annotations/ that is read back and checked file by file."""
    labels = read_json(p["set"] / "events.json") if (p["set"] / "events.json").exists() else {}
    history_path = p["set"] / "label-history.jsonl"
    history = history_path.read_text(encoding="utf-8").splitlines() if history_path.exists() else []
    if labels.get("revision", 0) != 0 or len(history) > 1 or \
            any(json.loads(line).get("actor") != "migration" for line in history):
        raise SystemExit("v2 has labels or history beyond the migration; not replacing them")
    extra = [path for path in (p["clips"],) if path.exists()]
    target = p["root"] / "annotations" / f"golden-v2-staging-backup-{_stamp()}.zip"
    names = {}
    with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED) as archive:
        for path in [*files, *extra]:
            name = path.relative_to(p["root"]).as_posix()
            archive.write(path, name)
            names[name] = file_sha256(path)
    with zipfile.ZipFile(target) as archive:
        for name, digest in names.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise SystemExit(f"backup {target} does not hold {name} intact")
    print(f"backup {target} ({len(names)} files, checked)", flush=True)


def cmd_migrate(args) -> int:
    p = paths(args)
    clips, infos = _cut_clips(p)
    targets = [p["set"] / name for name in ("segments.json", "events.json", "label-history.jsonl")]
    suggestions_target = p["root"] / "reports/benchmarks/golden-v2/prefill/suggestions.json"
    existing = [path for path in [*targets, suggestions_target] if path.exists()]
    if existing and not args.replace:
        raise SystemExit("v2 labels or suggestions exist already; not overwriting them (--replace)")
    if existing:
        _backup_unlabelled(p, existing)
    before = _tree_hashes(p["golden"])
    films_manifest, _, parts = _films(p)
    periods = gc.sampling_periods(read_json(p["profiles"]))
    manifest, labels, history = gc.build_v2(parts, clips, infos, periods, _approximate(p, parts),
                                            films=_kept_films(films_manifest))
    for event in labels["events"]:
        validate_event(event, manifest)  # raises on any label the shift broke
    write_json_atomic(targets[0], manifest)
    write_json_atomic(targets[1], labels)
    targets[2].write_text(json.dumps(history, ensure_ascii=False) + "\n", encoding="utf-8")
    load_golden_sets(p["staging"], ["v2"])  # the written set loads like any other
    offsets = {s["id"]: next(c["origin_seconds"] for c in clips if c["key"] == s["source"])
               for s in manifest["segments"] if is_clip(manifest["sources"][s["source"]])}
    payloads = {}
    for name, part in parts.items():
        path = p["root"] / f"reports/benchmarks/golden-{name}/prefill/suggestions.json"
        if path.exists() and read_json(path).get("manifest_sha256") == part["manifest_sha256"]:
            payloads[name] = read_json(path)
    write_json_atomic(suggestions_target, gc.migrate_suggestions(payloads, offsets, labels["manifest_sha256"]))
    if _tree_hashes(p["golden"]) != before:
        raise SystemExit("annotations/golden changed during the migration; check it before going on")
    print(f"v2: {len(clips)} clips, {len(manifest['sources']) - len(clips)} films, {len(manifest['segments'])} segments, "
          f"{len(labels['events'])} labels "
          f"in {p['set']}; manifest {labels['manifest_sha256'][:12]}", flush=True)
    return 0


def cmd_gate_score(args) -> int:
    from biliflow.golden_scoring import score_sets
    p = paths(args)
    film_manifest, film_labels, film_parts = _films(p)
    clip_manifest, clip_labels, clip_parts = load_golden_sets(p["staging"], ["v2"])
    card = read_json(Path(args.scorecard))
    film_queues, provenance = {}, {}
    for key, row in card["queues"].items():
        path = p["root"] / row["path"]
        if file_sha256(path) != row["sha256"]:
            raise SystemExit(f"{path} changed since the scorecard was made")
        film_queues[key] = read_json(path)
        # Job id and queue hash only: the public evidence never names a film or its files.
        provenance[f"job {film_manifest['sources'][key]['job_id']}"] = {"queue_sha256": row["sha256"]}
    film_card, _ = score_sets(film_manifest, film_labels, film_parts, film_queues, None, _approximate(p, film_parts))
    by_sha = {source["sha256"]: key for key, source in film_manifest["sources"].items()}
    clip_queues, offsets = {}, {}
    for key, source in clip_manifest["sources"].items():
        if source.get("scored") is False:
            continue
        if not is_clip(source):  # a kept film scores its own queue unchanged
            clip_queues[key] = film_queues[by_sha[source["sha256"]]]
            offsets.update({s["id"]: Fraction(0) for s in clip_manifest["segments"] if s["source"] == key})
            continue
        origin = source["origin"]
        offset = origin["origin_pts"] * Fraction(origin["time_base"])
        clip_queues[key] = gc.shift_queue(film_queues[by_sha[origin["sha256"]]], offset,
                                          source["duration_seconds"], source["sha256"])
        offsets.update({s["id"]: offset for s in clip_manifest["segments"] if s["source"] == key})
    clip_card, _ = score_sets(clip_manifest, clip_labels, clip_parts, clip_queues, None, _approximate(p, clip_parts))
    problems = gc.compare_shifted_cards(film_card, clip_card, offsets)
    passed = not problems
    summary = {"gate": "b1", "checked_at": gc.now_iso(), "passed": passed, "film_queues": provenance,
               "labels": len(film_card["labels"]), "items": len(film_card["items"]),
               "scored_segments": sum(1 for s in film_card["segments"] if s["scored_groups"]),
               "metrics_all": {group: film_card["metrics"][group]["all"] for group in film_card["metrics"]},
               "problems": problems[:200], "problem_count": len(problems)}
    full = dict(summary, scorecard=str(Path(args.scorecard)), film_card=film_card, clip_card=clip_card)
    _write_evidence(p, f"gate-b1-{_stamp()}.json", full, summary)
    print(f"labels {summary['labels']}, items {summary['items']}, problems {len(problems)}", flush=True)
    for problem in problems[:20]:
        print("  " + problem, flush=True)
    print("GATE B1 " + ("PASSED" if passed else "FAILED"), flush=True)
    return 0 if passed else 1


def _code_versions(p, card: dict) -> set[tuple]:
    """(git HEAD, src diff hash) of every trial a scorecard was made from (the install folder's code)."""
    versions = set()
    for name in (card.get("timing") or {}).get("trials", {}):
        trial = read_json(p["root"] / "reports/benchmarks" / name / "trial.json")
        versions.add((trial.get("git_head"), trial.get("git_diff_src_sha256"), tuple(trial.get("detector_groups") or ())))
    return versions


def cmd_gate_diff(args) -> int:
    p = paths(args)
    film_path, clip_path = Path(args.film_scorecard), Path(args.clip_scorecard)
    film_card, clip_card = read_json(film_path), read_json(clip_path)
    if set(film_card.get("sets") or {}) != set(SOURCE_SETS) or set(clip_card.get("sets") or {}) != {"v2"}:
        raise SystemExit("--film-scorecard must score v1 + v1.1 and --clip-scorecard v2")
    versions = _code_versions(p, film_card) | _code_versions(p, clip_card)
    if len(versions) != 1:
        raise SystemExit(f"the trials ran different code or detector groups: {sorted(versions, key=str)}")
    (head, src_diff, groups), = versions
    result = gc.run_differences(film_card, clip_card)
    summary = {"gate": "b2", "checked_at": gc.now_iso(), "code": {"git_head": head, "git_diff_src_sha256": src_diff,
                                                               "detector_groups": list(groups)},
               "film_scorecard_sha256": file_sha256(film_path), "clip_scorecard_sha256": file_sha256(clip_path),
               "labels": len(result["labels"]), "changed_labels": len(result["changed"]), **result}
    full = dict(summary, film_scorecard=str(film_path), clip_scorecard=str(clip_path))
    _write_evidence(p, f"gate-b2-{_stamp()}.json", full, summary)
    print(f"labels {summary['labels']}, changed {summary['changed_labels']}, segments with other item counts "
          f"{len(result['items_changed'])}", flush=True)
    for row in result["labels"]:
        if not row["same"]:
            print(f"  {row['id']} {row['group']}/{row['category']}: film {row['film']} -> clip {row['clip']}", flush=True)
    return 0


def cmd_activate(args) -> int:
    p = paths(args)
    retired = p["root"] / "annotations/golden-retired"
    moves = [(p["set"], p["golden"] / "v2")] + [(p["golden"] / name, retired / name) for name in SOURCE_SETS]
    problems = [f"missing {src}" for src, _ in moves if not (src / "segments.json").exists()]
    problems += [f"exists already {dst}" for _, dst in moves if dst.exists()]
    if not problems:
        load_golden_sets(p["staging"], ["v2"])
    for src, dst in moves:
        print(f"{'MOVE' if args.confirm else 'would move'} {src} -> {dst}", flush=True)
    if problems:
        raise SystemExit("; ".join(problems))
    if not args.confirm:
        print("dry run: nothing changed (pass --confirm only when the user asks)", flush=True)
        return 0
    backup = p["root"] / f"annotations/golden-backup-{datetime.now():%Y%m%d}-activate.zip"
    labels_only = [path for src, _ in moves for path in sorted(src.rglob("*"))
                   if path.is_file() and path.suffix != ".mp4"]
    with zipfile.ZipFile(backup, "x", zipfile.ZIP_DEFLATED) as archive:
        for path in labels_only:
            archive.write(path, path.relative_to(p["root"] / "annotations").as_posix())
    with zipfile.ZipFile(backup) as archive:
        if archive.testzip() is not None or any(
                hashlib.sha256(archive.read(path.relative_to(p["root"] / "annotations").as_posix())).hexdigest()
                != file_sha256(path) for path in labels_only):
            raise SystemExit(f"backup {backup} does not match; nothing moved")
    hashes = {src: _tree_hashes(src) for src, _ in moves}
    retired.mkdir(exist_ok=True)
    for src, dst in moves:
        src.rename(dst)
        if _tree_hashes(dst) != hashes[src]:
            raise SystemExit(f"{dst} differs from {src} after the move; stop and check")
    print(f"activated v2; backup {backup}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", help="BiliFlow folder of the films and labels (default: this checkout)")
    parser.add_argument("--staging", help="staging folder (default <root>/annotations/golden-v2-staging)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan")
    sub.add_parser("cut")
    verify = sub.add_parser("verify")
    verify.add_argument("--workers", type=int, default=2)
    drop = sub.add_parser("drop-clips")
    drop.add_argument("--confirm", action="store_true", help="really delete the unplanned staging clips")
    migrate = sub.add_parser("migrate")
    migrate.add_argument("--replace", action="store_true", help="rewrite an unlabelled earlier migration")
    gate = sub.add_parser("gate-score")
    gate.add_argument("--scorecard", required=True, help="scorecard.json of v1 + v1.1 whose film queues are reused")
    diff = sub.add_parser("gate-diff")
    diff.add_argument("--film-scorecard", required=True, help="scorecard.json of v1 + v1.1 (whole-film trials)")
    diff.add_argument("--clip-scorecard", required=True, help="scorecard.json of v2 (clip trials, same code)")
    activate = sub.add_parser("activate")
    activate.add_argument("--confirm", action="store_true", help="really move the folders (the user's call)")
    args = parser.parse_args(argv)
    return {"plan": cmd_plan, "cut": cmd_cut, "verify": cmd_verify, "drop-clips": cmd_drop_clips,
            "migrate": cmd_migrate,
            "gate-score": cmd_gate_score, "gate-diff": cmd_gate_diff,
            "activate": cmd_activate}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
