"""Offline cut detector over the per-frame features written by shots2.py / sweep2.py.

A frame is a cut when
  (a) d_hist >= HIST_ABS                      (large colour-histogram jump), or
  (b) d_hsv >= HSV_MIN and d_hsv >= RATIO * mean(d_hsv of the +-3 neighbouring
      non-duplicate frames)                    (PySceneDetect-style adaptive content jump).
Duplicate frames (d_gray < 0.3, the 24->25 fps pulldown repeats) are excluded
from the neighbour means so the frame after a repeat is not a false cut.
"""
import csv
import glob
from pathlib import Path

HERE = Path(__file__).resolve().parent
DUP = 0.3


def load(files=None):
    files = files or sorted(glob.glob(str(HERE / "sweep2" / "feat-*.csv")))
    frames = {}
    for name in files:
        with open(name, encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        for row in rows[1:]:  # first frame of a window has no predecessor
            frames[float(row["t"])] = (float(row["d_gray"]), float(row["d_hsv"]), float(row["d_hist"]))
    times = sorted(frames)
    return times, [frames[t] for t in times]


def coverage(times):
    cover = []
    for t in times:
        if cover and t - cover[-1][1] <= 0.1:
            cover[-1][1] = t
        else:
            cover.append([t, t])
    # the first frame of each run was dropped; extend runs back one frame
    return [(a - 0.04, b) for a, b in cover]


def detect(times, feats, hist_abs=0.12, hsv_min=8.0, ratio=3.0, window=3, min_gap=0.2):
    cuts = []
    n = len(times)
    for i in range(n):
        g, c, h = feats[i]
        if g < DUP:
            continue
        neigh = []
        for j in range(max(0, i - window - 2), min(n, i + window + 3)):
            if j == i or abs(times[j] - times[i]) > window * 0.04 + 0.041:
                continue
            if feats[j][0] < DUP:
                continue
            neigh.append(feats[j][1])
        base = sum(neigh) / len(neigh) if neigh else 0.0
        is_cut = h >= hist_abs or (c >= hsv_min and c >= ratio * max(base, 1e-6))
        if is_cut and (not cuts or times[i] - cuts[-1] >= min_gap):
            cuts.append(times[i])
    return cuts


if __name__ == "__main__":
    import sys
    lo, hi = float(sys.argv[1]), float(sys.argv[2])
    times, feats = load()
    sel = [(t, f) for t, f in zip(times, feats) if lo <= t <= hi]
    for params in ({}, {"hist_abs": 0.08}, {"ratio": 2.5, "hsv_min": 6.0}, {"hist_abs": 0.2, "hsv_min": 12.0, "ratio": 4.0}):
        cuts = detect([t for t, _ in sel], [f for _, f in sel], **params)
        print(params, len(cuts))
        print("  ", [round(c, 2) for c in cuts])
