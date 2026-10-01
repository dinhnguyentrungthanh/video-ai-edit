"""Apply a user-approved Golden Set label audit (docs/QUALITY_PLAN.md §17).

The plan (corrections.json) lists, for each label, why it goes away:
- wrong_answer: the user answered "Đúng" but the frames show normal film content -> the label is
  deleted and its question is recorded as "Sai" (rejected), as if answered so on the easy page.
- watermark_duplicate: a hand-drawn box on the film-long watermark that already has its own label
  -> deleted and the question marked "covered" by that watermark label.
- bad_box: a label with an unusable box whose content is already labelled -> deleted.
- rebox: only the box of a label changes (id, links and other fields kept), e.g. a padded watermark box.
- tighten_watermark: loose boxes around a watermark are replaced by one label with the box the user
  confirmed for that watermark elsewhere; their questions are marked "covered" by it.
- reopen: questions of one segment ("suggestion_ids") are asked again, e.g. 18+/gore/violence answered
  "Sai" only because the user keeps the scene (LabelStore.reopen_suggestions: the label created from
  each question is deleted and its id retired, the answer cleared). The segment stays in progress
  until the user answers again on the labeling page.
- update_label: change only the listed fields of an existing label (severity, notes, expected_action,
  content_present), e.g. implied nudity that must be flagged but is not a must-catch.
- answer_question: a question answered "Sai" becomes a label with the given answer (the user's rule
  says the detector was right to flag it); the label keeps the question link.

Every change goes through LabelStore (backup on open, append-only history, actor "claude-audit").
The plan pins the labels revision it was built from (and may name its "golden_set"); anything else
aborts. --set picks the label set (default v1). Without --apply the plan is applied to a throw-away
copy only. The labeling page of that set must be stopped (single-writer lock).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from biliflow.golden_set import KNOWN_SETS, LabelStore, now_iso, validate_event

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_ROOT = ROOT / "annotations/golden"
BENCHMARKS = ROOT / "reports/benchmarks"
LABELS = GOLDEN_ROOT / "v1"  # default set; kept for callers of the v1-only script
SUGGESTIONS = BENCHMARKS / "golden-v1/prefill/suggestions.json"


def set_paths(name: str) -> tuple[Path, Path]:
    """(labels directory, prefill suggestions) of one Golden Set."""
    if name not in KNOWN_SETS:
        raise SystemExit(f"unknown Golden Set {name!r}; choose one of {', '.join(KNOWN_SETS)}")
    return GOLDEN_ROOT / name, BENCHMARKS / f"golden-{name}/prefill/suggestions.json"


def _rebox(store: LabelStore, event_id: str, region: dict) -> None:
    """Change only a label's box, keeping its id, links (watermark decision, covered questions) and fields."""
    with store.lock:
        doc = store._draft(store.doc["revision"])
        event = next(e for e in doc["events"] if e["id"] == event_id)
        LabelStore._require_editable(doc, event["segment_id"])
        before = dict(event)
        validate_event(dict(event, region_source_pixels=region), store.manifest)  # same rules as any label
        event.update(region_source_pixels=dict(region), updated_at=now_iso())
        store._commit(doc, "rebox_event", before, dict(event))


def apply(labels_dir: Path, plan: dict, suggestions_path: Path | None = None) -> dict:
    manifest = json.loads((labels_dir / "segments.json").read_text(encoding="utf-8"))
    if plan.get("golden_set") and plan["golden_set"] != manifest.get("golden_set"):
        raise SystemExit(f"the plan is for Golden Set {plan['golden_set']}, these labels are "
                         f"{manifest.get('golden_set')}; pass the matching --set")
    suggestions = json.loads((suggestions_path or SUGGESTIONS).read_text(encoding="utf-8"))["suggestions"]
    store = LabelStore(labels_dir, manifest, suggestions, actor="claude-audit")
    try:
        if store.doc["revision"] != plan["labels_revision"]:
            raise SystemExit(f"labels are at revision {store.doc['revision']}, the plan was built on "
                             f"{plan['labels_revision']}; rebuild the audit instead of applying a stale plan")
        by_segment = defaultdict(list)
        for change in plan["changes"]:
            by_segment[change["segment_id"]].append(change)
        resolutions = store.doc["suggestion_resolutions"]
        answered = set(resolutions) | {e.get("from_suggestion") for e in store.doc["events"]}
        planned_reopen: set[str] = set()
        for segment_id, changes in by_segment.items():  # check reopen lists before anything is written
            for change in changes:
                if change["action"] != "reopen":
                    continue
                stray = [key for key in change["suggestion_ids"]
                         if (store.suggestions.get(key) or {}).get("segment_id") != segment_id]
                if not change["suggestion_ids"] or stray:
                    raise SystemExit(f"reopen in {segment_id}: no questions, or questions of another segment "
                                     f"or unknown: {', '.join(stray)}")
                odd = [key for key in change["suggestion_ids"] if key not in answered
                       or (resolutions.get(key) or {}).get("resolution") == "covered"
                       or (store.suggestions.get(key) or {}).get("stale")]
                if odd:
                    raise SystemExit(f"reopen in {segment_id}: not answered yet, stale, or answered by a film-long "
                                     f"watermark/logo label: {', '.join(odd)}")
                repeated = [key for key in change["suggestion_ids"] if key in planned_reopen]
                if repeated:
                    raise SystemExit(f"reopen in {segment_id}: listed more than once: {', '.join(repeated)}")
                planned_reopen.update(change["suggestion_ids"])
        done = defaultdict(int)
        reopened: list[str] = []
        for segment_id, changes in by_segment.items():
            was_complete = store.doc["segments"][segment_id]["status"] == "complete"
            if was_complete:
                store.set_segment_status(segment_id, "in_progress", store.doc["revision"])
            for change in changes:
                if change["action"] == "reopen":
                    result = store.reopen_suggestions(change["suggestion_ids"], store.doc["revision"])
                    reopened += result["suggestions"]
                    done[change["action"]] += 1
                    continue
                if change["action"] == "update_label":
                    event = next(e for e in store.doc["events"] if e["id"] == change["event_id"])
                    unknown = set(change["fields"]) - {"severity", "notes", "expected_action", "content_present"}
                    if unknown:
                        raise SystemExit(f"update_label: fields not allowed: {', '.join(sorted(unknown))}")
                    store.upsert_event(dict(event, **change["fields"]), store.doc["revision"])
                    done[change["action"]] += 1
                    continue
                if change["action"] == "answer_question":
                    key = change["suggestion_id"]
                    question = store.suggestions[key]
                    if (store.doc["suggestion_resolutions"].get(key) or {}).get("resolution") != "rejected":
                        raise SystemExit(f"answer_question: {key} was not answered 'Sai'")
                    store.resolve_suggestion(key, None, store.doc["revision"])
                    store.upsert_event({
                        "segment_id": segment_id, "category": question["category"],
                        "start_seconds": question["start_seconds"], "end_seconds": question["end_seconds"],
                        "region_source_pixels": None, "from_suggestion": key, **change["answer"],
                    }, store.doc["revision"])
                    done[change["action"]] += 1
                    continue
                if change["action"] == "rebox":
                    _rebox(store, change["event_id"], change["region_source_pixels"])
                    done[change["action"]] += 1
                    continue
                if change["action"] == "tighten_watermark":
                    questions = [e.get("from_suggestion") for e in store.doc["events"] if e["id"] in change["event_ids"]]
                    for event_id in change["event_ids"]:
                        store.delete_event(event_id, store.doc["revision"])
                    new = store.upsert_event(change["new_event"], store.doc["revision"])
                    for question in filter(None, questions):
                        store.cover_suggestion(question, new["id"], store.doc["revision"])
                    done[change["action"]] += 1
                    continue
                store.delete_event(change["event_id"], store.doc["revision"])
                question = change.get("from_suggestion")
                if question and question in store.suggestions:
                    if change["action"] == "watermark_duplicate":
                        store.cover_suggestion(question, change["covered_by"], store.doc["revision"])
                    elif change["action"] == "wrong_answer":
                        store.resolve_suggestion(question, "rejected", store.doc["revision"])
                done[change["action"]] += 1
            reopening = any(change["action"] == "reopen" for change in changes)
            if was_complete and not reopening:  # reopened questions wait for the user's new answer
                store.set_segment_status(segment_id, "complete", store.doc["revision"])
        result = {"golden_set": manifest.get("golden_set"), "revision": store.doc["revision"],
                  "events": len(store.doc["events"]), "applied": dict(done),
                  "segments": {k: v["status"] for k, v in store.doc["segments"].items()}}
        if reopened:
            result["reopened_questions"] = len(reopened)
            result["unanswered"] = {segment_id: len(store.unresolved(segment_id))
                                    for segment_id in by_segment if store.unresolved(segment_id)}
        return result
    finally:
        store.close()


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):  # tests capture stdout in a StringIO
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--set", default="v1", choices=KNOWN_SETS, help="Golden Set to correct (default v1)")
    parser.add_argument("--apply", action="store_true", help="change the real labels (otherwise a throw-away copy)")
    parser.add_argument("--copy-to", type=Path, help="keep the corrected copy here (dry run only)")
    args = parser.parse_args(argv)
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    labels, suggestions = set_paths(args.set)
    if args.apply:
        print(json.dumps(apply(labels, plan, suggestions), ensure_ascii=False, indent=1))
        return
    (ROOT / "temp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / "temp") as temp:
        copy = Path(temp) / "labels"
        copy.mkdir()
        for name in ("segments.json", "events.json", "label-history.jsonl"):
            if (labels / name).exists():
                shutil.copy2(labels / name, copy / name)
        result = apply(copy, plan, suggestions)
        print(json.dumps(dict(result, dry_run=True), ensure_ascii=False, indent=1))
        if args.copy_to:
            shutil.rmtree(args.copy_to, ignore_errors=True)
            shutil.copytree(copy, args.copy_to, ignore=shutil.ignore_patterns("events.lock", "backups"))


if __name__ == "__main__":
    main()
