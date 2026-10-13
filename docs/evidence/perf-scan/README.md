# Scan stage start-up (perf plan B1), 2026-10-12

`b1/20261012-gate.json` holds the B1 gate results:

- the run summaries;
- the field comparison of the four scan reports;
- the `prune()` gate;
- the import timings;
- the cache-key cost.

It names jobs only by id and label (L = live action). It has no paths, no film or file names and no links.

The gate copies and reports stayed local and were removed afterwards:

- the temporary root under `<install>\temp`;
- the source copies, models, ffmpeg and the HF cache;
- the copy of `cache\stage-results`.

## What was run

- **Revisions.** `base` = `e534c12` (main before B1), `b1` = `9f3f7f0` on `perf/scan-b1`. Each run installed that revision's
  `src`, `scripts`, `config`, `pyproject.toml` and the install marker into the temporary root. Before each run it reset
  `reports`, `logs`, `temp`, `state` and the scan caches.
- **Scan.** Jobs 125 (L, 44 min) and 114 (L, 56 min), from SHA-256-checked copies of their sources. The settings were
  the same as the real jobs: advertising only, live action, careful profile, `ocr_recognition_batch_size=1`, fast scan.
  The scan ran through the Control Center's own path (`JobScheduler._select` / `_execute`, the same `run.ps1` stage
  processes and the GPU mutex). It ran only while the real Control Center had no scan running.
- **Stage cache off in both variants.** The temporary root is 18 characters deeper than the install. With
  `LongPathsEnabled=0`, a cache store there passes the 259-character path limit. The gain from B1 item 8 (`prune()`)
  is therefore measured on its own (`prune_gate`).
- **Order.** Two rounds, the second in reverse order: round 1 ran base 125, b1 125, b1 114, base 114; round 2 ran
  b1 125, base 125, base 114, b1 114. Round 1's base 125 was the first scan after the setup, with cold file caches.
- **Quality rule.** `text/text-scan.json`, `visual-logo/scan.json`, `visual-logo/scan-florence.json` and
  `review-queue.json` must be equal, except for the time and performance fields listed in
  `quality_gate.ignored_fields`. No key may be missing and no list may change length. Every pair passed, including
  the cross-round pairs (base r1 against base r2, b1 r1 against b1 r2).
- **`prune_gate`.** It ran on a copy of the install's `cache\stage-results` (241 entries, 0.92 GB) under `temp`, never
  on the install. The new `prune()` removed the same entries as the old one in every case. The cases cover age and
  size limits, plus one with `bytes` stripped from 7 manifests: there the new code walks only those entries.
- **`outside_model_work_seconds`.** The stage wall time minus the counted model-work phases: model step, frame-pipe
  and prefetch waits, tracking, preview writes and frame extraction. What is left is process start, imports, model
  load, the cache key and reporting.

# OCR input on the GPU (B, OCR round), 2026-10-13

`ocr/20261013-gate.json` holds the gate of `perf/scan-ocr` 5072564, which builds EasyOCR's CRAFT input on the GPU in
fast scan. It has the runs, the report comparisons and the job 125 repeats. It names jobs only by id and label
(L = live action, H = animation), with no paths, film or file names, or links.

- **Revisions.** `base` = `d5413af` (`origin/main` 6df5c63 merged into the branch), `gpupre` = `5072564`.
- **Sources.** Jobs 125 (L) and 114 (L), from SHA-256-checked copies of their sources. Job 38 (H, 1080p): a 5-minute
  stream copy from 450 s, the window of the real report with the most text tracks.
- **Scan.** The same as the B1 gate: the Control Center's scheduler path, careful, fast scan, advertising, stage cache
  off. The job 38 copy ran as animation. Every run happened while the real Control Center had no running stage, export
  included (`contaminated: false`).
- **Quality rule.** The same as B1: the four reports must be equal, except for the B1 time and performance fields. The
  text previews must also be byte-identical. Every pair passed, including base↔base and gpupre↔gpupre across repeats.
- **Speed.** Job 125, three runs of each:
  - text stage median 92.0 s → 79.9 s;
  - advertising scan median 203.8 s → 190.4 s.
  - The first gpupre runs of 125 and 114 had a 15.8–19.4 s semantic step. It did not come back in the repeats, and the
    real reports show 3.5–7.5 s, so it is counted as noise.
- **Also here.**
  - `ocr/20261013-step1-measurements.json` holds the step 1 text-stage runs: the harness-only variants, their medians
    and their identity against `plain-a`.
  - `ocr/20261013-overlap-trial.json` holds the trial that overlapped detection with recognition (`a84e42a`, never
    merged). It was identical on all three sources, but only 2.2–3.5 % faster, below the 5 % rule, so it was dropped.
- **Not kept.** The temporary root, the source copies, the clip and the reports were removed at the end of the round.
