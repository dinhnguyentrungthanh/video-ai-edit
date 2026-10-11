"""G2: exact evaluation of named candidate rules (picked from g2-rules-ff.csv plus the previous run's R1/R3).

For each rule: exact Golden scoring (items and scene cards), labels lost, Golden FP moved, queue items moved by
G0 class / film / priority, scene cards (triage before grouping and card-level triage), joint margin with the
nearest protected item, per-condition raw gaps, lone-shot verdict for every positive span, dev-only check.
Writes g2-picked-ff.json, g2-moved-ff.csv, g2-lone-shot-ff.csv.
"""
from __future__ import annotations
import csv, json, math
from collections import Counter
from pathlib import Path
import numpy as np
import ff_common as F
from biliflow.review_workflow import group_safety_review_events

HERE = Path(__file__).parent
rows = list(csv.DictReader((HERE / "items-ff.csv").open(encoding="utf-8")))
lone = list(csv.DictReader((HERE / "lone-shots.csv").open(encoding="utf-8")))
FPLIKE = {"red_object", "red_light", "dark_scene", "stylised_effect", "other"}
feats = sorted({k for k in rows[0] if k not in ("item_id", "film", "priority", "labels", "golden_verdict", "golden_split",
                                                 "golden_segment", "golden_labels", "trigger", "visually_dark")})
X = {f: np.array([float(r[f]) for r in rows]) for f in feats}
L = {f: np.array([float(r[f]) if r.get(f, "") != "" else np.nan for r in lone]) for f in feats if f in lone[0]}
IDR = {f: max(1e-6, float(np.percentile(X[f], 90) - np.percentile(X[f], 10))) for f in feats}
trig = np.array([r["trigger"] for r in rows]); verdict = np.array([r["golden_verdict"] for r in rows])
split = np.array([r["golden_split"] for r in rows]); ids = [r["item_id"] for r in rows]
PROTECT = {"A": (verdict == "useful") | (trig == "blood_visible"),
           "B": (verdict == "useful") | np.isin(trig, ["blood_visible", "wound_no_blood", "corpse_no_blood"])}
lone_blood = np.array([r["real_blood"] == "1" for r in lone])

RULES = {  # name: (tier, [(feature, dir, thr)], note)
    "A1_injury_over_blood_and_safe_rating": ("A", [("blood_minus_injury", "le", -0.03916), ("questionable_mean", "le", 0.00204)],
                                             "injury tag exceeds every blood tag AND rating 'questionable' ~ 0"),
    "A2_no_blood_no_corpse": ("A", [("blood_family_max", "le", 0.07158), ("corpse_max", "le", 0.02874)],
                              "blood-family tags < 0.07 AND corpse tag < 0.03"),
    "A3_injury_over_blood_and_bright": ("A", [("blood_minus_injury", "le", 0.02979), ("luma_median", "ge", 78.23)],
                                        "blood barely above injury AND frames not dark"),
    "A4_injury_over_blood_and_short_run": ("A", [("blood_minus_injury", "le", 0.02409), ("confirmed", "le", 4)],
                                           "blood barely above injury AND <= 4 confirmed frames (best tier-A gain at margin >= 0.1)"),
    "prev_R1_weak_blood_isolated": ("A", [("blood_family_max", "le", 0.20), ("dist_strong_blood", "ge", 120.0)],
                                    "previous run (2026-10-01) R1: seed-based"),
    "prev_R3_R1_plus_corpse_guard": ("A", [("blood_family_max", "le", 0.20), ("dist_strong_blood", "ge", 120.0),
                                           ("corpse_max", "le", 0.05)], "previous run R3: seed-based + corpse guard"),
    "B1_dark_tag_no_injury": ("B", [("injury_max", "le", 0.02315), ("dark_max", ">=", 0.29992)],
                              "tag 'dark' >= 0.30 AND injury tag < 0.023 (best Golden gain under tier B)"),
    "B4_stylised_pink_blood": ("B", [("pink_blood_max", "ge", 0.23731), ("confirmed_high", "le", 4)],
                               "stylised pink_blood tag >= 0.24 AND <= 4 high-score confirmed frames (one item)"),
    "B5_night_no_humans": ("B", [("no_humans_mean", "ge", 0.37791), ("night_max", "ge", 0.53821)],
                           "tag 'night' >= 0.54 AND mostly no humans (no_humans mean >= 0.38)"),
    "prev_B3_red_pixels_short_run": ("B", [("red_frac_median", "ge", 0.06625), ("longest_confirmed_run", "le", 5)],
                                     "earlier candidate of this run; after the frame check it moves wound / possible-body items"),
}


def fires(V, f, d, t):
    return V[f] <= t if d == "le" else V[f] >= t


def norm(conds):
    return [(f, "ge" if d in ("ge", ">=") else "le", t) for f, d, t in conds]


def mask(V, conds):
    m = np.ones(len(next(iter(V.values()))), bool)
    for f, d, t in conds:
        m &= fires(V, f, d, t)
    return m


def gap_plus(V, f, d, t):
    g = (V[f] - t) if d == "le" else (t - V[f])
    return np.maximum(g, 0.0)


def joint_margin(conds, prot):
    """Distance (inter-decile ranges) of every protected item / real-blood lone shot from being moved:
    all conditions must fire, so an item's distance is its largest normalised gap. Report the nearest."""
    di = np.max([gap_plus(X, f, d, t) / IDR[f] for f, d, t in conds], axis=0)
    dl = np.max([gap_plus(L, f, d, t) / IDR[f] for f, d, t in conds], axis=0)
    cands = [(float(v), ids[i]) for i, v in enumerate(di) if prot[i]] + \
            [(float(v), lone[i]["span_id"] + "@lone-shot") for i, v in enumerate(dl) if lone_blood[i]]
    v, who = min(cands)
    return v, who


def per_condition(conds, prot):
    out = []
    for k, (f, d, t) in enumerate(conds):
        others = np.ones(len(rows), bool)
        lothers = np.ones(len(lone), bool)
        for j, (f2, d2, t2) in enumerate(conds):
            if j != k:
                others &= fires(X, f2, d2, t2); lothers &= fires(L, f2, d2, t2)
        rel = prot & others & ~fires(X, f, d, t)
        lrel = lone_blood & lothers & ~fires(L, f, d, t)
        vals = [(abs(float(X[f][i]) - t), ids[i], float(X[f][i])) for i in np.nonzero(rel)[0]] + \
               [(abs(float(L[f][i]) - t), lone[i]["span_id"] + "@lone-shot", float(L[f][i])) for i in np.nonzero(lrel)[0]]
        moved = mask(X, conds)
        edge = (X[f][moved].max() if d == "le" else X[f][moved].min()) if moved.any() else None
        row = {"condition": f"{f} {'<=' if d == 'le' else '>='} {t:g}", "protected_kept_only_by_this": len(vals),
               "outermost_moved_value": None if edge is None else round(float(edge), 5)}
        if vals:
            g, who, v = min(vals)
            row.update(nearest_protected=who, its_value=round(v, 5), raw_gap=round(g, 5), gap_in_IDR=round(g / IDR[f], 4),
                       gap_relative_to_threshold=round(g / abs(t), 3) if t else None)
        out.append(row)
    return out


def cards_after(moved_ids):
    """Scene cards per film when moved items leave the main list before grouping (adult-triage-style placement)."""
    q = F.queues("cards", moved=moved_ids)
    return F.gore_cards_per_film(q)


def card_level(conds):
    """Card-level triage: group all gore items into scene cards first; a card moves only when every member fires."""
    m = dict(zip(ids, mask(X, conds)))
    out = {}
    for film in F.FILMS:
        cards = group_safety_review_events([dict(i) for i in F.BASE[film]["items"] if i["category"] == "gore"])
        mem = []
        for c in cards:
            members = [i["id"] for i in F.BASE[film]["items"] if i["category"] == "gore" and any(
                ref in (c.get("source_candidate_refs") or []) for ref in (i.get("source_candidate_refs") or []))]
            mem.append(members)
        moved_cards = [c for c, ms in zip(cards, mem) if ms and all(m.get(x, False) for x in ms)]
        out[film] = {"cards": len(cards), "cards_moved": len(moved_cards),
                     "items_in_moved_cards": sum(len(ms) for c, ms in zip(cards, mem) if c in moved_cards),
                     "multi_member_cards_blocked_by_one_member": sum(1 for ms in mem if len(ms) > 1 and any(m.get(x) for x in ms)
                                                                     and not all(m.get(x) for x in ms))}
    return out


def main():
    base_items = F.golden("items"); base_cards = F.golden("cards")
    out = {"labels_revision": F.LABELS["revision"],
           "baseline": {"items": F.gore_view(base_items), "cards": F.gore_view(base_cards),
                        "gore_items_per_film": {f: sum(1 for i in F.BASE[f]["items"] if i["category"] == "gore") for f in F.FILMS},
                        "gore_cards_per_film": F.gore_cards_per_film(F.queues("cards"))},
           "protection": {"A": "Golden useful (incl. 'có thật · giữ') + every item whose frames show real blood (G0 blood_visible)",
                          "B": "A + wound-without-blood + corpse-without-blood items (policy: visible wounds are flagged)"},
           "lone_shot_definition": "span reduced to the single shot holding its strongest blood-family frame (shot cuts: "
                                   "histogram distance >= 0.20 or luma jump >= 20 between 2 fps frames), temporal "
                                   "confirmation recomputed with hits outside the shot removed, no neighbouring seeds "
                                   "(dist_strong_blood = 9999); features over confirmed frames of the shot, else all its frames",
           "margin_definition": "joint margin = min over protected items and real-blood lone shots of max over conditions of "
                                "(distance to firing / inter-decile range of the feature over the 105 items)",
           "rules": {}}
    moved_rows, lone_rows = [], []
    for name, (tier, conds, note) in RULES.items():
        conds = norm(conds); prot = PROTECT[tier]
        m = mask(X, conds); lm = mask(L, conds)
        moved_ids = {ids[i] for i in np.nonzero(m)[0]}
        ci = F.golden("items", moved=moved_ids); cc = F.golden("cards", moved=moved_ids)
        jm, who = joint_margin(conds, prot)
        dev_prot = prot & ((split == "dev") | (split == ""))
        rec = {
            "tier": tier, "note": note,
            "rule": " AND ".join(f"{f} {'<=' if d == 'le' else '>='} {t:g}" for f, d, t in conds),
            "golden_items_after": F.gore_view(ci), "golden_cards_after": F.gore_view(cc),
            "labels_not_main_items": F.gore_labels_not_main(ci), "labels_not_main_cards": F.gore_labels_not_main(cc),
            "labels_lost": len(set(F.gore_labels_not_main(ci)) | set(F.gore_labels_not_main(cc))),
            "golden_fp_moved": sorted(ids[i] for i in np.nonzero(m & (verdict == "false_positive"))[0]),
            "golden_useful_moved": sorted(ids[i] for i in np.nonzero(m & (verdict == "useful"))[0]),
            "protected_moved_tierA": int((m & PROTECT["A"]).sum()), "protected_moved_tierB": int((m & PROTECT["B"]).sum()),
            "queue_items_moved": int(m.sum()),
            "queue_items_moved_by_trigger": dict(Counter(trig[m]).most_common()),
            "queue_items_moved_by_film": dict(Counter(r["film"] for r, x in zip(rows, m) if x)),
            "queue_items_moved_by_priority": dict(Counter(r["priority"] for r, x in zip(rows, m) if x)),
            "gore_items_per_film_after": {f: sum(1 for r, x in zip(rows, m) if r["film"] == f and not x) for f in F.FILMS},
            "gore_cards_per_film_after_triage_before_grouping": cards_after(moved_ids),
            "card_level_triage": card_level(conds),
            "joint_margin_IDR": round(jm, 4) if math.isfinite(jm) else "inf", "nearest_protected": who,
            "per_condition": per_condition(conds, prot),
            "lone_shot_real_blood_moved": [lone[i]["span_id"] for i in np.nonzero(lm & lone_blood)[0]],
            "lone_shot_cothat_nonblood_moved": [lone[i]["span_id"] for i in np.nonzero(lm & ~lone_blood)[0]],
            "uses_neighbour_seeds": any(f == "dist_strong_blood" for f, _, _ in conds),
            "dev_only_check": {"valid_with_dev_labels_only": not bool((m & dev_prot).any()),
                               "holdout_useful_moved": int((m & (verdict == "useful") & (split == "holdout")).sum()),
                               "holdout_fp_moved": int((m & (verdict == "false_positive") & (split == "holdout")).sum()),
                               "dev_fp_moved": int((m & (verdict == "false_positive") & (split == "dev")).sum())},
        }
        out["rules"][name] = rec
        for r, x in zip(rows, m):
            if x:
                moved_rows.append({"rule": name, "tier": tier, "item_id": r["item_id"], "film": r["film"], "start": r["start"],
                                   "end": r["end"], "priority": r["priority"], "labels": r["labels"],
                                   "golden_verdict": r["golden_verdict"], "trigger": r["trigger"],
                                   **{f: r[f] for f, _, _ in conds}})
        for i, s in enumerate(lone):
            lone_rows.append({"rule": name, "span_id": s["span_id"], "kind": s["kind"], "trigger": s["trigger"],
                              "real_blood": s["real_blood"], "shot": f"{float(s['shot_start']):.1f}-{float(s['shot_end']):.1f}",
                              "shot_frames": s["shot_frames"], "detector_confirms_alone": s["detector_confirms_alone"],
                              "moved_by_rule": int(lm[i]), **{f: s[f] for f, _, _ in conds}})
        a, c = rec["golden_items_after"]["all"], rec["golden_cards_after"]["all"]
        print(f"\n{name} [{tier}] {rec['rule']}")
        print(f"  items: main {a['main_items']} useful {a['useful_main']} fp {a['false_positive_main']} prec {a['precision_main']} "
              f"caught_main {a['caught_main']}/{a['labels']} must {a['must_caught']}/{a['must_catch']} adv {a['advisory_items']}")
        print(f"  cards: main {c['main_items']} useful {c['useful_main']} fp {c['false_positive_main']} prec {c['precision_main']} "
              f"caught_main {c['caught_main']}/{c['labels']} must {c['must_caught']}/{c['must_catch']}")
        print(f"  lost {rec['labels_lost']} | moved {rec['queue_items_moved']} {rec['queue_items_moved_by_trigger']} | "
              f"cards after {rec['gore_cards_per_film_after_triage_before_grouping']} | card-level {rec['card_level_triage']}")
        print(f"  joint margin {rec['joint_margin_IDR']} ({who}) | lone real-blood moved {rec['lone_shot_real_blood_moved']} | "
              f"lone có-thật non-blood moved {rec['lone_shot_cothat_nonblood_moved']} | dev-only {rec['dev_only_check']}")
        for pc in rec["per_condition"]:
            print("   ", pc)
    (HERE / "g2-picked-ff.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    for name, data in (("g2-moved-ff.csv", moved_rows), ("g2-lone-shot-ff.csv", lone_rows)):
        keys = list(dict.fromkeys(k for r in data for k in r))
        with (HERE / name).open("w", encoding="utf-8", newline="") as h:
            w = csv.DictWriter(h, fieldnames=keys); w.writeheader(); w.writerows(data)


if __name__ == "__main__":
    main()
