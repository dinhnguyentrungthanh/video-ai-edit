"""G2: explainable two-signal rules that move anime gore false alarms to the optional list.

Gates fixed before looking at results (docs/ADULT_FALSE_ALARM_PLAN.md §7):
  * no Golden gore label may leave the main list (17/17 caught_main, must_catch 5/5) - checked with exact
    golden_scoring on items AND on scene cards;
  * protection tier A: no Golden 'useful' item and no item whose frames show real blood (G0 blood_visible) moves;
    tier B (policy-strict): additionally no wound-without-blood and no corpse item moves (visible wounds are flagged);
  * lone-shot: the rule must keep every real-blood span when that span is reduced to its single strongest
    blood shot, re-confirmed in isolation, with no neighbouring seeds (lone-shots.csv);
  * a rule needs two signals from different families that both fire; margin must be > 0.
Thresholds sit at the midpoint between the outermost moved item and the nearest protected value
(items or lone shots), i.e. at the centre of the valid gap.
Writes g2-rules-ff.csv (all valid rules), g2-picked-ff.json (exact Golden on picked rules), g2-moved-ff.csv.
"""
from __future__ import annotations
import csv, itertools, json, math
from collections import Counter
from pathlib import Path
import numpy as np

HERE = Path(__file__).parent
rows = list(csv.DictReader((HERE / "items-ff.csv").open(encoding="utf-8")))
lone = list(csv.DictReader((HERE / "lone-shots.csv").open(encoding="utf-8")))
FPLIKE = {"red_object", "red_light", "dark_scene", "stylised_effect", "other"}

SIGNALS = [  # feature, direction, family, plain-language meaning
    ("blood_family_max", "le", "gore_tags", "blood-family tags weak"),
    ("blood_max", "le", "gore_tags", "'blood' tag weak"),
    ("gore_max", "le", "gore_tags", "gore union weak"),
    ("blood_minus_injury", "le", "gore_tags", "injury outweighs blood"),
    ("own_seeds", "le", "gore_tags", "no own strong-blood frame"),
    ("corpse_max", "le", "corpse", "corpse tag weak"),
    ("injury_max", "le", "injury", "injury tag weak"),
    ("injury_max", "ge", "injury", "injury tag present"),
    ("pink_blood_max", "ge", "pink", "pink_blood (stylised) tag present"),
    ("general_mean", "ge", "rating", "rating 'general' high"),
    ("sensitive_mean", "le", "rating", "rating 'sensitive' low"),
    ("questionable_mean", "le", "rating", "rating 'questionable' low"),
    ("no_humans_max", "ge", "no_humans", "no humans in frame"),
    ("no_humans_mean", "ge", "no_humans", "mostly no humans"),
    ("luma_median", "le", "brightness", "dark frames"),
    ("luma_median", "ge", "brightness", "bright frames"),
    ("dark_frac_median", "ge", "brightness", "mostly dark pixels"),
    ("red_frac_median", "ge", "colour", "red-dominant pixels"),
    ("confirmed", "le", "run_length", "short confirmed run"),
    ("confirmed_high", "le", "run_length", "few high-score confirmed frames"),
    ("longest_confirmed_run", "le", "run_length", "short longest run"),
    ("dist_strong_blood", "ge", "seeds", "no strong-blood item within N s"),
    ("fire_max", "ge", "scene_tags", "fire tag"), ("embers_max", "ge", "scene_tags", "embers tag"),
    ("night_max", "ge", "scene_tags", "night tag"), ("dark_max", "ge", "scene_tags", "dark tag"),
    ("sunset_max", "ge", "scene_tags", "sunset tag"), ("card_max", "ge", "scene_tags", "card tag"),
    ("light_particles_max", "ge", "scene_tags", "light particles tag"), ("red_theme_max", "ge", "scene_tags", "red theme tag"),
    ("scratches_max", "ge", "scene_tags", "scratches tag"), ("bruise_max", "ge", "scene_tags", "bruise tag"),
]
MIN_TAG = 0.05  # a 'tag present' signal needs the tag at least weakly present
TAG_GE = {s[0] for s in SIGNALS if s[2] in ("scene_tags", "no_humans", "pink") and s[1] == "ge"}

feats = sorted({s[0] for s in SIGNALS})
X = {f: np.array([float(r[f]) for r in rows]) for f in feats}
L = {f: np.array([float(r[f]) for r in lone]) for f in feats}
IDR = {f: max(1e-6, float(np.percentile(X[f], 90) - np.percentile(X[f], 10))) for f in feats}
trig = np.array([r["trigger"] for r in rows]); verdict = np.array([r["golden_verdict"] for r in rows])
split = np.array([r["golden_split"] for r in rows])
gold_fp = verdict == "false_positive"; gold_useful = verdict == "useful"
PROTECT = {"A": gold_useful | (trig == "blood_visible"),
           "B": gold_useful | np.isin(trig, ["blood_visible", "wound_no_blood", "corpse_no_blood"])}
lone_blood = np.array([r["real_blood"] == "1" for r in lone])
lone_pos_other = ~lone_blood  # Golden 'có thật' spans without drawn blood (corpse, bruises)
lone_split = np.array([r.get("split", "") for r in lone])
TRAIN = {"all": (np.ones(len(rows), bool), np.ones(len(lone), bool)),
         "dev": ((split == "dev") | (split == ""), (lone_split == "dev") | (lone_split == "")),
         "holdout": ((split == "holdout") | (split == ""), (lone_split == "holdout") | (lone_split == ""))}


def fires(V, f, d, t):
    return V[f] <= t if d == "le" else V[f] >= t


def candidates(f, d):
    v = np.unique(X[f])
    if f in TAG_GE and d == "ge":
        v = v[v >= MIN_TAG]
    return v


def gap_place(f, d, moved_vals, protected_vals):
    """Threshold at the centre of the gap between the outermost moved value and the nearest protected value."""
    if d == "le":
        edge = moved_vals.max()
        beyond = protected_vals[protected_vals > edge]
        if not beyond.size:
            return edge, math.inf
        thr = (edge + beyond.min()) / 2
        return thr, (beyond.min() - thr) / IDR[f]
    edge = moved_vals.min()
    beyond = protected_vals[protected_vals < edge]
    if not beyond.size:
        return max(edge, MIN_TAG) if f in TAG_GE else edge, math.inf
    thr = (edge + beyond.max()) / 2
    if f in TAG_GE:
        thr = max(thr, MIN_TAG)
    return thr, (thr - beyond.max()) / IDR[f]


def evaluate(sa, sb, ta, tb, prot, lb):
    """Centre both thresholds; margins are measured on protected items and real-blood lone shots that are
    kept by ONE signal only (the other fires), which is where a threshold actually protects something."""
    A = fires(X, sa[0], sa[1], ta); B = fires(X, sb[0], sb[1], tb)
    m = A & B
    LA = fires(L, sa[0], sa[1], ta); LB = fires(L, sb[0], sb[1], tb)
    out = []
    for (s, t, mine, other, Lother) in ((sa, ta, A, B, LB), (sb, tb, B, A, LA)):
        pv = np.concatenate([X[s[0]][prot & other], L[s[0]][lb & Lother]])
        thr, margin = gap_place(s[0], s[1], X[s[0]][m], pv)
        out.append((thr, margin))
    (ta2, _), (tb2, _) = out
    m2 = fires(X, sa[0], sa[1], ta2) & fires(X, sb[0], sb[1], tb2)
    lm2 = fires(L, sa[0], sa[1], ta2) & fires(L, sb[0], sb[1], tb2)
    if (m2 & prot).any() or (lm2 & lb).any():
        ta2, tb2 = ta, tb   # centring both at once broke a gate: keep the tight thresholds
        m2 = m
        lm2 = fires(L, sa[0], sa[1], ta) & fires(L, sb[0], sb[1], tb)
    ma = margin_at(sa, ta2, fires(X, sb[0], sb[1], tb2), fires(L, sb[0], sb[1], tb2), prot, lb)
    mb = margin_at(sb, tb2, fires(X, sa[0], sa[1], ta2), fires(L, sa[0], sa[1], ta2), prot, lb)
    jm, who = joint_margin(sa, ta2, sb, tb2, prot, lb)
    return ta2, tb2, m2, lm2, ma, mb, jm, who


def gap_plus(V, s, t):
    """Normalised distance a value must travel to make signal s fire (0 when it already fires)."""
    g = (V[s[0]] - t) if s[1] == "le" else (t - V[s[0]])
    return np.maximum(g, 0.0) / IDR[s[0]]


def joint_margin(sa, ta, sb, tb, prot, lb):
    """Smallest joint threshold shift (in inter-decile ranges) that would move a protected item or a real-blood
    lone shot: an item moves only when BOTH signals fire, so its distance is the larger of its two gaps."""
    di = np.maximum(gap_plus(X, sa, ta), gap_plus(X, sb, tb))[prot]
    dl = np.maximum(gap_plus(L, sa, ta), gap_plus(L, sb, tb))[lb]
    allv = np.concatenate([di, dl])
    if not allv.size:
        return math.inf, ""
    k = int(np.argmin(allv))
    who = ([r["item_id"] for r, p in zip(rows, prot) if p] + [r["span_id"] + "@lone" for r, b in zip(lone, lb) if b])[k]
    return float(allv[k]), who


def margin_at(s, t, other_items, other_lone, prot, lb):
    """Distance (in inter-decile ranges) from threshold t to the nearest protected value kept only by s."""
    pv = np.concatenate([X[s[0]][prot & other_items], L[s[0]][lb & other_lone]])
    if not pv.size:
        return math.inf
    gaps = (pv - t) if s[1] == "le" else (t - pv)
    return float(gaps.min()) / IDR[s[0]]


def sym(d):
    return "<=" if d == "le" else ">="


def search(tier, train="all"):
    """Selection sees only the training split: its protected items, its real-blood lone shots, its Golden FP.
    Items and lone shots outside Golden (non-Golden real blood) are in every training view."""
    tm, tl = TRAIN[train]
    prot = PROTECT[tier] & tm
    lb = lone_blood & tl
    fp_train = gold_fp & tm
    results = []
    for sa, sb in itertools.combinations(SIGNALS, 2):
        if sa[0] == sb[0] or sa[2] == sb[2]:
            continue
        best = {}
        for ta in candidates(sa[0], sa[1]):
            A = fires(X, sa[0], sa[1], ta); LA = fires(L, sa[0], sa[1], ta)
            if not (A & fp_train).any():
                continue
            for tb in candidates(sb[0], sb[1]):
                m = A & fires(X, sb[0], sb[1], tb)
                if (m & prot).any() or not (m & fp_train).any():
                    continue
                if (LA & fires(L, sb[0], sb[1], tb) & lb).any():
                    continue
                key = int((m & fp_train).sum())
                # among equal FP gains keep the smallest moved set (most conservative)
                if key not in best or m.sum() < best[key][2].sum():
                    best[key] = (ta, tb, m)
        for key, (ta, tb, m) in best.items():
            ta2, tb2, m2, lm2, ma, mb, jm, who = evaluate(sa, sb, ta, tb, prot, lb)
            results.append({
                "tier": tier, "train": train,
                "golden_fp_moved_train": int((m2 & fp_train).sum()),
                "protected_moved_all_tierA": int((m2 & PROTECT["A"]).sum()),
                "protected_moved_all_tierB": int((m2 & PROTECT["B"]).sum()),
                "rule": f"{sa[0]} {sym(sa[1])} {ta2:.4g} AND {sb[0]} {sym(sb[1])} {tb2:.4g}",
                "signal_a": sa[3], "signal_b": sb[3], "families": f"{sa[2]}+{sb[2]}",
                "feature_a": sa[0], "dir_a": sa[1], "thr_a": round(float(ta2), 5),
                "feature_b": sb[0], "dir_b": sb[1], "thr_b": round(float(tb2), 5),
                "golden_fp_moved": int((m2 & gold_fp).sum()),
                "golden_fp_moved_dev": int((m2 & gold_fp & (split == "dev")).sum()),
                "golden_fp_moved_holdout": int((m2 & gold_fp & (split == "holdout")).sum()),
                "golden_useful_moved": int((m2 & gold_useful).sum()),
                "queue_moved": int(m2.sum()),
                "moved_fplike": int((m2 & np.isin(trig, list(FPLIKE))).sum()),
                "moved_wound": int((m2 & (trig == "wound_no_blood")).sum()),
                "moved_corpse": int((m2 & (trig == "corpse_no_blood")).sum()),
                "moved_blood_visible": int((m2 & (trig == "blood_visible")).sum()),
                "lone_real_blood_moved": int((lm2 & lone_blood).sum()),
                "lone_real_blood_moved_ids": " ".join(r["span_id"] for r, x in zip(lone, lm2 & lone_blood) if x),
                "lone_cothat_nonblood_moved": int((lm2 & lone_pos_other).sum()),
                "margin_a": round(ma, 4) if math.isfinite(ma) else "inf",
                "margin_b": round(mb, 4) if math.isfinite(mb) else "inf",
                "margin": round(jm, 4) if math.isfinite(jm) else "inf",
                "nearest_protected": who,
                "uses_seeds": int("seeds" in (sa[2], sb[2])),
                "moved_ids": " ".join(r["item_id"][7:] for r, x in zip(rows, m2) if x),
            })
    return results


def mval(r):
    return 99.0 if r["margin"] == "inf" else float(r["margin"])


if __name__ == "__main__":
    allres = []
    for tier in ("A", "B"):
        for train in ("all", "dev", "holdout"):
            allres += search(tier, train)
    allres.sort(key=lambda r: (r["tier"], r["train"], -r["golden_fp_moved"], -mval(r)))
    with (HERE / "g2-rules-ff.csv").open("w", encoding="utf-8", newline="") as h:
        w = csv.DictWriter(h, fieldnames=list(allres[0].keys())); w.writeheader(); w.writerows(allres)
    for tier in ("A", "B"):
        sub = [r for r in allres if r["tier"] == tier and r["train"] == "all" and mval(r) > 0]
        print(f"\n== tier {tier}: {len(sub)} valid rules with margin > 0 (no seeds: {sum(1 for r in sub if not r['uses_seeds'])})")
        for r in sorted(sub, key=lambda r: (-r["golden_fp_moved"], -mval(r)))[:30]:
            print(r["golden_fp_moved"], f"dev{r['golden_fp_moved_dev']}/ho{r['golden_fp_moved_holdout']}", r["margin"],
                  r["queue_moved"], f"fp-like {r['moved_fplike']} wound {r['moved_wound']} corpse {r['moved_corpse']}",
                  f"lone-cothat {r['lone_cothat_nonblood_moved']}", "S" if r["uses_seeds"] else "-", "|", r["rule"])

    # cross-split check of the selection PROCEDURE: pick the top rule on one split (max train FP, then joint margin,
    # margin > 0), then look at the other split's labels.  Variants: any rule / seedless / seedless + margin >= 0.1.
    folds = []
    for tier in ("A", "B"):
        for train, test in (("dev", "holdout"), ("holdout", "dev")):
            for variant, ok in (("any", lambda r: True), ("seedless", lambda r: not r["uses_seeds"]),
                                ("seedless_margin>=0.1", lambda r: not r["uses_seeds"] and mval(r) >= 0.1)):
                cands = [r for r in allres if r["tier"] == tier and r["train"] == train and mval(r) > 0 and ok(r)]
                if not cands:
                    continue
                top = max(cands, key=lambda r: (r["golden_fp_moved_train"], mval(r), -r["queue_moved"]))
                mid = set(top["moved_ids"].split())
                moved = np.array([r["item_id"][7:] in mid for r in rows])
                lm = fires(L, top["feature_a"], top["dir_a"], top["thr_a"]) & fires(L, top["feature_b"], top["dir_b"], top["thr_b"])
                folds.append({"tier": tier, "train": train, "test": test, "variant": variant, "rule": top["rule"],
                              "train_fp_moved": top["golden_fp_moved_train"], "train_margin": top["margin"],
                              "test_fp_moved": int((moved & gold_fp & (split == test)).sum()),
                              "test_fp_total": int((gold_fp & (split == test)).sum()),
                              "test_useful_moved": int((moved & gold_useful & (split == test)).sum()),
                              "test_useful_moved_ids": " ".join(r["item_id"] for r, x in zip(rows, moved & gold_useful & (split == test)) if x),
                              "test_lone_real_blood_moved": int((lm & lone_blood & (lone_split == test)).sum()),
                              "test_lone_cothat_moved": int((lm & lone_pos_other & (lone_split == test)).sum()),
                              "protected_moved_same_tier_all_items": int((moved & PROTECT[tier]).sum())})
    with (HERE / "g2-folds-ff.csv").open("w", encoding="utf-8", newline="") as h:
        w = csv.DictWriter(h, fieldnames=list(dict.fromkeys(k for f in folds for k in f))); w.writeheader(); w.writerows(folds)
    print("\n== cross-split selection check")
    for f in folds:
        print(f)
