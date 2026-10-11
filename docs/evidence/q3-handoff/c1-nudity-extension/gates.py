"""Shot-extension rules with a shot-level evidence gate, on truth (720-1200) and film-wide.

Gate: the whole shot that the extension would reach into (shot start .. next cut after
the anchor, at most 20 s past it) must hold enough adult evidence:
  frac70  share of its 2 fps samples scoring >= 0.70 (the existing context threshold)
  seeds   number of its samples scoring >= 0.95 (the existing seed threshold)
Scores: exact production scores (golden CSV for 720-1200, region-scores.csv elsewhere).
"""
import csv
import json
import sys
from bisect import bisect_left, bisect_right

import sim

REACH = 20.0


def load_all_scores():
    scores = dict(sim.load_scores())
    try:
        with open(sim.HERE / "region-scores.csv", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                scores.setdefault(float(row["time_seconds"]), float(row["score"]))
    except FileNotFoundError:
        pass
    times = sorted(scores)
    return times, [scores[t] for t in times]


class Gate:
    def __init__(self, times, values, shots, frac70=0.0, seeds=0):
        self.times, self.values, self.shots = times, values, shots
        self.frac70, self.seeds = frac70, seeds
        self.log = []

    def stats(self, lo, hi):
        i, j = bisect_left(self.times, lo), bisect_left(self.times, hi)
        v = self.values[i:j]
        expected = int(round((hi - lo) * 2))
        return v, expected

    def ok(self, shot_lo, shot_hi):
        v, expected = self.stats(shot_lo, shot_hi)
        if len(v) < 0.9 * expected or not v:
            self.log.append(("unscored", shot_lo, shot_hi))
            return None
        f70 = sum(x >= 0.70 for x in v) / len(v)
        n95 = sum(x >= 0.95 for x in v)
        passed = f70 >= self.frac70 and n95 >= self.seeds
        self.log.append((passed, round(shot_lo, 2), round(shot_hi, 2), round(f70, 2), n95))
        return passed


def extend(interval, shots, gate, cap, side, anchor_kind):
    a = float(interval["start_seconds"]); b = float(interval["end_seconds"])
    ctx = interval["sequence_context"]
    if anchor_kind == "seed":
        anchor_s = float(ctx["detector_start_seconds"]) + 1.0
        anchor_e = float(ctx["detector_end_seconds"]) - 1.0
    else:
        anchor_s, anchor_e = a + 1.0, b - 1.0
    na, nb, unscored = a, b, False
    if side in ("start", "both"):
        status, s = shots.shot_start(anchor_s, cap + (anchor_s - a))
        if status == "ok" and s < a and a - s <= cap + 1e-9:
            st, e = shots.shot_end(anchor_s, REACH)
            shot_hi = e if st == "ok" else anchor_s + REACH
            verdict = gate.ok(s, shot_hi)
            unscored |= verdict is None
            if verdict:
                na = s
    if side in ("end", "both"):
        status, e = shots.shot_end(anchor_e, cap + (b - anchor_e))
        if status == "ok" and e > b and e - b <= cap + 1e-9:
            st, s = shots.shot_start(anchor_e, REACH)
            shot_lo = s if st == "ok" else anchor_e - REACH
            verdict = gate.ok(shot_lo, e)
            unscored |= verdict is None
            if verdict:
                nb = e
    return na, nb, unscored


def run(intervals, shots, gate, cap, side, anchor_kind):
    base = sim.union([(float(i["start_seconds"]), float(i["end_seconds"])) for i in intervals])
    new, unscored = [], 0
    for interval in intervals:
        na, nb, u = extend(interval, shots, gate, cap, side, anchor_kind)
        unscored += u
        new.append((na, nb))
    ext = sim.union(new)
    added = sim.subtract(ext, base)
    return base, ext, added, unscored


if __name__ == "__main__":
    scan = json.loads(sim.SCAN.read_text(encoding="utf-8"))
    intervals = scan["intervals"]
    truth_iv = [i for i in intervals if float(i["end_seconds"]) > 700 and float(i["start_seconds"]) < 1220]
    shots = sim.FeatureShots()
    times, values = load_all_scores()
    gates = [(0.0, 0), (0.5, 0), (0.4, 3), (0.4, 5), (0.3, 5), (0.5, 3), (0.0, 8)]
    rows = []
    for anchor_kind in ("support", "seed"):
        for side in ("start", "both"):
            for cap in (6, 8, 10, 12, 15):
                for frac70, seeds in gates:
                    g1 = Gate(times, values, shots, frac70, seeds)
                    b0, ext, added, _ = run(truth_iv, shots, g1, cap, side, anchor_kind)
                    m = sim.truth_metrics(b0, ext, added)
                    g2 = Gate(times, values, shots, frac70, seeds)
                    fb, fext, fadded, unscored = run(intervals, shots, g2, cap, side, anchor_kind)
                    outside = [(x, y) for x, y in fadded if y <= 720 or x >= 1200]
                    row = {
                        "anchor": anchor_kind, "side": side, "cap": cap, "frac70": frac70, "seeds": seeds,
                        "truth": m, "film_added": round(sim.length(fadded), 1),
                        "film_added_outside_720_1200": round(sim.length(outside), 1),
                        "film_extended_spans": len(fadded), "film_unscored_edges": unscored,
                        "film_spans_outside": [(round(x, 2), round(y, 2)) for x, y in outside],
                    }
                    rows.append(row)
                    print(f"{anchor_kind:7s} {side:5s} cap {cap:2d} gate f70>={frac70:.1f} seeds>={seeds} | truth +{m['added_total']:5.2f}s "
                          f"nude {m['added_in_nudity']:5.2f} clothedK {m['added_in_clothed_intimacy_keep']:5.2f} "
                          f"implied {m['added_in_implied_uncertain']:5.2f} otherK {m['added_in_other_keep']:5.2f} miss {m['miss_977_983_covered']:.2f} "
                          f"| film +{row['film_added']:6.1f}s outside {row['film_added_outside_720_1200']:6.1f}s spans {len(fadded):3d} unscored {unscored}")
    (sim.HERE / "results-gates.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
