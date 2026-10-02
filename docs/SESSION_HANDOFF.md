# BiliFlow session handoff

Updated: 2026-10-02 (Asia/Bangkok)

This is the short, authoritative starting point for a new Codex account or chat. It complements the detailed history in `PROJECT_STATUS.md` and `CHANGELOG.md`.

## Repository state

- Project root: `E:\DungChung\BiliFlow`
- Active branch: `improve/scan-performance-metrics` (new user request; remain here).
- Base local `main`: `7f5a9fb`; ten commits ahead of `origin/main` when this branch was created. Previous detector improvements are already on local `main`.
- Latest code milestone: `2c72280` reduces CPU RGB-distance overhead with bit-exact output; 278/278 tests. Six source excerpts show 20.50% lower CPU routing time, not whole-video scan time. Actual cold-routing/VLM-input checks also pass. Prior `6231f58` fixes lossy visual-logo cache; `bf5bc35` adds opt-in OCR controls with serial default. Prefetch stays OFF. See `docs/SCAN_PERFORMANCE.md`.
- Runtime source version: `src/biliflow/__init__.py` reports `0.7.24`
- Packaging metadata in `pyproject.toml` still reports `0.7.19`; use the runtime source version for dashboard diagnosis and align the package metadata during a later release housekeeping change.
- Confirm working-tree state with Git; performance work is isolated from `main`.
- The earlier merge was approved, but this new improvement branch is not approved for merge/push. Do not merge or push unless the user asks.

The improvement sequence now present on local `main` is:

1. `b0088f8 Complete live-action adult scene coverage`
2. `ff5c7d4 Keep rerun controls open during refresh`
3. `0318a22 Refresh review after queue revisions`
4. `5dce1d2 Prioritize sustained explicit scenes`
5. `0b4ff3c Map logo candidates by geometry track`

Always confirm this section with `git status` and `git log` because it becomes stale after new work.

## Current work — 2026-10-03 dashboard batch 1 (uncommitted, integrated in the main tree)

- job_store (IN_PROCESS_STATES, claim_queued, mark_queued, queued_jobs, queue_seq/queued_at migration), scheduler (FIFO _select, start/rerun/export reseq, resume keeps place, _executing_job_id guard), job_import (no overwrite of settled jobs), control_center (sticky tabs, queue badges, status queue_position/queue_kind), review_workflow (export panel closes on confirm, #export-notice). Evidence: temp/ui-plan/batch1/.
- Done 2026-10-03 00:18: Control Center restarted with batch 1; jobs table migrated (queue_seq/queued_at), backup state/backups/control-center-before-queue-order-20261003-001806.sqlite3 (30 jobs). Next: batch 2 and batch 3 per docs/UI_QUEUE_PLAN.md.

## Current work — 2026-10-02 run-affecting fixes (uncommitted, integrated in the main tree)

- `control_center.py` (picker drafts/localStorage, ordered polls, interaction-deferred render, start confirmation, setup-card order), `scheduler.start_job` guard, `codex_supervisor.queue_coverage_findings` (detector shortfall = WARN), `brand_memory.remember_review_item` no-op writes skipped; tests in test_control_center (node harness tests/fixtures/dashboard_harness.js), test_scheduler, test_codex_supervisor, test_brand_memory. Evidence and repros: `temp/run-issues-diag/` (after-fix/).
- Open: restart the Control Center when the queue is idle; stored structure-audit.json files of jobs 40-50 still say BLOCK until re-audited; the visual-logo candidate budget (18 windows per 5-min bucket) leaves 125-290 regional leads unchecked per Nhất Âu Xuân episode — needs a measured real-video run before any change.

## Current work — 2026-10-02 studio-logo memory v2 (uncommitted, integrated in the main tree)

- `brand_memory.py` (schema 2: frames, masks, guard, refresh, upgrade), `review_workflow.py` (remember/refresh hooks, page texts), `scripts/studio_logo_upgrade.py`, tests in `tests/test_scene_cards.py`. Evidence `reports/benchmarks/studio-mask-20261002`, design `temp/studio-mask-design/design.json`.
- Done 2026-10-02 17:29 at the user's request: Control Center stopped, `scripts/studio_logo_upgrade.py --apply` upgraded the 3 records (111/68/111 frames; backup `state/backups/studio-logo-memory-20261002-172902.json`, frames in `state/studio-logo-frames/`), Control Center restarted with the new code.
- Open: the candidate queue's own blurred watermark regions are not masked; a remembered card's summary in another queue of the same source can go stale (record itself correct); OCR noise on animated idents keeps some episodes required (Tập 11, 15).

## Current work — 2026-10-02 opening card UI (uncommitted, integrated in the main tree)

- `review_workflow.py` (`_raw_vlm_evidence`, `evidence_regions`, `link_full_scene_logo_evidence`, page helpers `aiVerdictHtml`/`evidenceMediaHtml`/`sceneLogo`/studio notes), `brand_memory.compare_studio_logo`; tests `tests/test_opening_card_display.py`. Worktree `temp/wt-opening` holds the same code and is reused for the next step.
- Next (approved by the user): studio-logo memory masks the regions of persistent-overlay cards decided BLUR and stores several frames per remembered ident; must be measured for false moves (Troy/Conan idents, overlays outside the mask still break a match) before integration.
- Never call decision or edit-plan writers with the real project root in diagnostics (they rewrite state/brand-memory.json and state/studio-logo-memory.json).

## Current work — 2026-10-02 watermark fix (uncommitted, integrated in the main tree)

- `review_workflow.py`: `promote_fixed_text_overlays` (whole-film text track / recurring pieces of one line -> one `persistent_overlay` card), `corroborate_fixed_text_overlays`, `_text_regions_adjacent` merge guard; tests `tests/test_fixed_text_overlays.py`. Evidence: `reports/benchmarks/fixed-overlay-20261002`, `reports/benchmarks/job40-watermark-diag`, frames in `temp/job40-diag/frames`.
- Jobs 40/45/48/49 (Nhất Âu Xuân Tập 10/15/18/19) were re-queued through the Control Center API at the user's request; the old Tập 10 and Tập 18 exports in `output/` are wrong (unblurred / over-blurred) and were left for the user.
- Pending: integrate `temp/gore-c1.patch` after the series is redone (it changes `cli.py` and so every scan cache key); remove worktrees `temp/wt-gore` and `temp/wt-overlay` afterwards.

## Current work — 2026-10-02 task B exact-output speedups (uncommitted)

- Changed: `vlm_confirmation.py` (T1), `scanner.py` / `live_safety_scanner.py` / `animation_safety_scanner.py` (T5a background hash), `shot_cuts.py` (T5b), `visual_logo_scanner.py` (T5d), `frame_prefetch.py` (shutdown fix); tests `test_vlm_confirmation`, `test_batch_prefetch`, `test_shot_cuts`, `test_exact_speedups`, `test_visual_logo_vlm_prefetch`; harnesses `scripts/benchmark_exact_speedups.py` (+ `.ps1`) and `scripts/benchmark_stage_equivalence.py`.
- Verified: full-film stage-level equivalence against the HEAD trials `troy-allgroups-full-speedbase-20261001-203927` and `conan20-allgroups-full-speedbase-20261001-215759` (`reports/benchmarks/exact-speedups/stages-20261002-075009/summary.json`, all_identical true; src diff sha256 18adb60b… unchanged during the run). 643/643 tests. Decisions and numbers: `reports/benchmarks/exact-speedups/decisions-20261002.json`.
- Another agent edits `review_workflow.py`, `brand_memory.py`, `cli.py` and review-page code in parallel; compare review queues only by rebuilding both sides with one build code (the stage harness does this).
- Code edits invalidate stage caches; the Control Center runs the old code until restarted. Nothing committed; commit only when the user asks.

## Current work — 2026-10-01 afternoon (uncommitted)

- 18+ triage (conservative, live action) + verify_adult stage + R3 on: implemented, skeptic-reviewed, gates passed
  (`reports/benchmarks/adult-triage-20261001-131834`). Review page focus mode + review evidence + Host check done.
  Golden v1 r469, v1.1 r115 (implied-nudity rule). Baseline v1+v1.1: `reports/benchmarks/golden-baseline-v1v11-20261001-095204`.
- The user's Control Center still runs the old code until restarted (Stop-BiliFlow.cmd / Start-BiliFlow.cmd).
  Code edits invalidate stage caches: the next scan of each film recomputes every stage.
- Next proposals: carry reviewed decisions forward on a rerun (today only unresolved items are preserved), then
  anime gore false alarms (plan §7). Nothing committed since f54cc09; commit only when the user asks.

## Current work — 2026-10-01 (uncommitted)

- Done and skeptic-approved: Conan 21 region fix (region 13/13, `reports/benchmarks/golden-q3b-region-final-20261001`),
  R3 nudity shot completion (off by default), Golden Set v1.1 tooling. v1.1 manifest + hints built
  (`annotations/golden/v1.1/segments.json`, `reports/benchmarks/golden-v1.1/prefill/suggestions.json`);
  the user labels it with `Golden-Label-v1.1.cmd` / `Golden-Label-Phone-v1.1.cmd`.
- In progress: review-evidence backend + Control Center Host-check fix (spec `temp/q3-handoff/review-evidence-backend-spec.md`,
  plan `docs/REVIEW_EVIDENCE_PLAN.md`). UI waits for the user's feedback on the mockup `temp/review-mockup/`.
- Troy #39 rev 4 reviewed (151 KEEP / 8 BLUR), READY_TO_EXPORT; 16:17-16:23 not covered until R3 is enabled.

## PAUSED 2026-10-01 00:15 — resume here when the user says "tiếp tục"

The user shut the PC down for the night. Everything was paused cleanly: review workflows stopped, Control
Center stopped, no BiliFlow process left, full suite 474/474 OK on the paused tree. Nothing committed.

Uncommitted work waiting for an independent skeptic review (author reports in `temp/q3-handoff/`):
1. Conan 21 watermark region fix v2 (`src/biliflow/review_workflow.py`, `tests/test_review_workflow.py`):
   `region-fix-v2-author.md`; first review findings in `temp/q3-handoff/review-b/` and `b-region-report.md`.
   After review: score the rebuilt queues against `reports/benchmarks/golden-baseline-v1-r469-20260930-235857`
   (labels revision 469; expect advertising region 13/13, nothing worse).
2. Nudity shot completion R3, off by default (`scanner.py`, new `shot_cuts.py`, `cli.py --shot-completion`,
   `tests/test_shot_cuts.py`): `r3-shot-completion-author.md`, design `c1-report.md`. Not wired into jobs;
   needs validation on a second film (Golden v1.1 T6) before the user decides to enable it.
3. Golden Set v1.1 tooling, option A (`golden_set.py`, `golden_scoring.py`, `golden_label_app.py`,
   `evaluate_golden.py`, `golden_prefill.py`, `golden_label_server.py`, tests): `golden-v11-tooling-author.md`,
   proposal `c2-proposal.md`. The user decided battles with visible slashing/stabbing count (flag for review),
   so keep T7/T8; clear `GOLDEN_V1_1_PENDING_DECISION`, then build the v1.1 manifest and hints (collect after
   Troy revision 4) for the user to label (~40 min).

Troy #39 revision 4 was reviewed by the user (151 KEEP, 8 BLUR incl. 15:28-15:50 after the hint); 16:17-16:23 is still
not covered until R3 is enabled and Troy re-scanned. Review-card gap (QUALITY_PLAN §18 d): one preview frame, no video.
Previously pending: review Troy #39 revision 4 on the Dashboard (159 items; minutes 14-18: clothed = KEEP,
15:28-15:50 and 16:23-17:07 = BLUR/CUT); label v1.1; say when to commit.

## Current work — Golden Set v1 audited and scored (uncommitted when written)

- Labels: revision 457 after the user-approved audit (QUALITY_PLAN.md §17; plan and evidence in
  `reports/benchmarks/golden-v1/audit-20260930`, applied with `scripts/golden_audit_corrections.py`).
- Baseline scorecard: `reports/benchmarks/golden-baseline-v1-20260930-225627` (Conan 21 trial
  `conan21-allgroups-full-golden-20260930-222720`). Use it as the reference for `compare --gate`.
- Open findings: Conan 21 watermark region refinement cuts off "Phim"; Troy #39 active revision is
  advertising-only (no 18+ scan) yet READY_TO_EXPORT; adult detector misses clothed kissing and
  977–983 s (`reports/benchmarks/golden-v1/troy-adult-check-20260930`). Troy 870–1110 s is not in v1.
- The user asked to commit only when told to; nothing since f54cc09 is committed.

## Current work — Phase H-live (live-action safety speed), uncommitted when written

Plan and results: docs/CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md §18. L1 exact prefetch is in `scan` and
`scan-live-safety` (full Troy byte-identical). L2 `scan-live-safety --violence-precision fp16` passed its
review-level gate on full Troy (`reports/benchmarks/live-safety-h/full-20260930-144244`) and, after user
approval, is wired into "Tăng tốc xử lý" (`FAST_SCAN_VIOLENCE_PRECISION`). Long benchmarks are launched as independent processes
(WMI `Win32_Process.Create`) because a background shell dies with the session.

## Current work — Phase H (animation safety speed), uncommitted when written

The user asked to keep improving while Golden Set labeling waits for the PC. Phase H
(docs/CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md §17): H1 exact batch prefetch is in the scanner;
H2 `scan-animation-safety --precision fp16` passed its full-film gates on Conan 20/21
(`reports/benchmarks/anime-safety-h/full-20260929-231545`) and, after user approval, is wired into
"Tăng tốc xử lý" (H3, `FAST_SCAN_ANIMATION_PRECISION`); 387/387 tests. The user asked not to commit until the quality track has real
results.

## Current work — quality track (Golden Set v1), uncommitted when written

Speed work is committed through `f54cc09` (fast_scan incl. FP16 CRAFT; Troy advertising 25m32s -> 14m27s).
The user then approved `docs/QUALITY_PLAN.md` and asked to start with the three existing
sources (Troy #39, Conan 20 #38, Conan 21 #37) and verify new videos later. Q0–Q1 tooling is
built and tested (361/361), not committed (commit only when the user asks):

- Manifest: `annotations/golden/v1/segments.json` (13 segments, 64 min, SHA-256 verified).
  Labels: `annotations/golden/v1/events.json` + `label-history.jsonl` + `backups/` (gitignored:
  never delete; they are user work).
- Suggestions: `python scripts/golden_prefill.py collect` -> `reports/benchmarks/golden-v1/prefill/suggestions.json`
  (404: 272 from all job revisions and latest benchmark queues + 132 from the dense per-segment
  scans in `reports/benchmarks/golden-v1/prefill/dense`, 27 min). Re-running `collect` with the same inputs
  keeps suggestion ids; it refuses to drop ids that labels reference unless `--force`.
- Labeling page: `Golden-Label.cmd` / `.\scripts\golden-label.ps1` -> http://127.0.0.1:8766 (PC only). Phone on the home Wi-Fi: `Golden-Label-Phone.cmd` (random access code, link printed and saved to `reports/benchmarks/golden-v1/phone-link.txt`); stop every labeling page with `Golden-Label-Stop.cmd` (QUALITY_PLAN.md §16). One server at a time (events.lock).
- Scoring: `python scripts/evaluate_golden.py run` (≈70 min, isolated trials, then scorecard in
  `reports/benchmarks/golden-<timestamp>/`), `score --queue ...`, `compare --gate detector|speed`.
  Rules and gates are in `docs/QUALITY_PLAN.md` §4, §7, §12 and `src/biliflow/golden_scoring.py`;
  do not loosen them after seeing numbers.
- `reports/benchmarks/golden-smoke-20260929-214612` was scored with throw-away labels from `temp/`;
  it is a pipeline check, not a measurement.

Next: the user labels all 13 segments; then run the Q2 baseline scorecard and propose Q3
fixes from the measured misses (must_catch first).

## Latest bounded experiment — cross-frame OCR recognition (uncommitted when written)

Opt-in `recognition_frame_window` (CLI `--recognition-frame-window`, 1..8, default 1,
requires batch size > 1) shares exact-width recognition batches across consecutive
frames; detection stays per frame and serial. Not wired into job pipeline/Dashboard.
Seven original-source excerpts, A/B/B/A: identical reports (except scores/metrics),
previews and review mappings; max score delta 2e-6 in reports. Scan without source
hash 35.839 -> 30.191s (-15.8%), OCR model -18.5%; text-heavy sections -23..-28%,
single-watermark sections ~0..-4%. Real CUDA/FFmpeg cancel 3/3 clean. 291/291 tests.
Full Troy OCR stage A/B/B/A (user-authorized, `ocr-cross-frame-downstream-20260928-231957`):
median 752.6 -> 631.2s (-16.1%), identical outputs, tracks equal production report.
Phase B rejected: batched detection (no gain, 2.7x CUDA memory) and cuDNN autotune
(no gain); code only under `reports/benchmarks/ocr-cross-frame-abba-20260929-000902/`.
Evidence: `reports/benchmarks/ocr-cross-frame-*`; harness `scripts/benchmark_ocr_cross_frame.py`.
Phases A-D committed on this branch at the user's request (2026-09-29); no merge/push.
Phase C done: frame-parallel logo routing (`--routing-workers`, exact; full Troy
routing -17.6%). Phase D done: per-job Dashboard option "Tăng tốc xử lý"
(`fast_scan`, ON by default since 2026-09-29) = OCR batch 8 + window 4 + 3 routing workers. Full
advertising A/B same session: 25m32s -> 22m57s (-10.2%), identical outputs,
Structure Audit PASS (`troy-full-standard-20260929-093619`, `troy-full-fast-20260929-100152`).
Stage overlap measured, not adopted (+18-35% OCR slowdown, ~2.4-2.9 min gain).
Phase E done (uncommitted until the user asks): fast_scan also warms the logo
routing cache during OCR (`scan-text --prewarm-logo-routing` -> child
`scan-visual-logo --routing-only --decode nvdec`). Full pipeline 22m33s -> 17m45s,
identical outputs (`troy-full-fast-20260929-150440`). Plan/results: §13 of
docs/CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md. Benchmark OCR-stage wrapper
`scripts/benchmark-text-stage.ps1` keeps the warm-up in the benchmark cache.
Phase E committed as `12a59a3`. Phase F (uncommitted until the user asks): background
source hashing + parallel localization frame extraction; full pipeline 17m45s -> 16m35s,
identical outputs (`troy-full-fast-20260929-164440`). Low-priority warm-up was rejected.
Phase F committed as `6a50df8`. Then (user-approved): fast_scan uses FP16 CRAFT detection
(review items identical on Troy and Conan 20 incl. all detector groups; Troy 14m27s) and
the GroundingDINO empty-frame crash is fixed. Harness supports `--job-id 38|39` and
`--detectors`; `review-diff` writes a Vietnamese HTML of review-level differences. 301/301 tests. RoutingPool entry points must be
`python -m biliflow` or `__main__`-guarded scripts (spawned workers).

## Last verified production-style run

COMPLETED user-authorized full measurement: `troy-rgb-cold-full-20260928-194937`.
No benchmark is running. Harness `reports/benchmarks/run_troy_rgb_full.py` uses
serial OCR and actual pipeline stages with isolated SQLite/review, and redirects
routing/GroundingDINO result caches into a fresh benchmark namespace. No production
cache is deleted. Source: original Troy, careful/live_action/advertising only.

- Total **1786.436s (29m46s)**: preflight 20.709, OCR 821.828, visual-logo 686.890,
  localization 248.265, build-review/local structural audit 8.710 seconds.
- CPU logo routing 517.638s vs recent pre-optimization cold 612.124s (-15.44%
  observed); total logo stage 686.890 vs 812.780s. These runs were not interleaved.
- The older complete production scan was about 27m29s, so this trial DOES NOT
  demonstrate a lower end-to-end time. Older DINO had 87 cache hits / 25 computed;
  this run has 0 hits / 112 computed. OCR also took longer. OS/model-file caching
  is uncontrolled. Do not conflate the CPU improvement with full-pipeline speed.
- OCR/raw logo results match baseline; localized differences are five documented
  telemetry fields. All 858 JPEG hashes match; review proposals remain 6 primary
  and 294 advisory, all 123 source candidates represented, Structure Audit PASS.
- Original job #39 (full row/revision), queue hash, source stat and brand-memory
  hash remain unchanged. No Visual AI, safety scan, export, merge or push.
- Read `analysis.json`, `comparison.json`, `trial.json`, and Vietnamese `SUMMARY.md`
  under `reports/benchmarks/troy-rgb-cold-full-20260928-194937/`. The corresponding
  `reports/jobs` tree is benchmark-marked and not auto-imported by the Dashboard.

Latest bounded performance verification (not a production rerun):

- `reports/benchmarks/logo-rgb-distance-20260928-193550/`: six source excerpts,
  270 frames from Troy and Conan 20/21, baseline/optimized/optimized/baseline.
  CPU features plus brand matching median 13.482 -> 10.718s (-20.50%); every
  feature/score/focus geometry equals immutable baseline `35fa7de` exactly.
- `reports/benchmarks/logo-routing-equivalence-20260928-193742/`: actual scanner
  control flow on Troy 0-30s (dense boundaries) and Conan 21 4200-4260s (ordinary
  sampling). All 165 samples, 18 windows, counts, selection and full/crop JPEG
  hashes for VLM evidence match. Source hash verification runs normally; cache
  reads/writes are intercepted only by the harness to force cold routing without
  overwriting existing caches. Stop before VLM load; no review/export is created.
- `reports/benchmarks/ocr-phases-20260928-192818/`: unchanged serial OCR over 40
  frames, four 30-second excerpts. Detection 4.577s / recognition 5.726s, total
  readtext 10.472s. Instrumented/uninstrumented predictions match. These are
  excerpt measurements, not full-film phase shares; OCR code/defaults unchanged.
- OpenCV thread-count changes were not adopted. Previous-edge reuse was also
  rejected after only 1.88% CPU benefit; its code/evidence is retained at
  `reports/benchmarks/logo-edge-reuse-20260928-193159/`, not in production.
- All 278 tests pass (`logo-rgb-distance-20260928-193550/unittest.log`). Production
  Troy queue and brand-memory hashes remain unchanged. No benchmark is running.

Completed corrective advertising trial: `troy-ocr8-full-cache2-20260928-184844`.
Reports live under `reports/jobs/` with that key; separate SQLite state/logs and
`trial.json` live under the matching `reports/benchmarks/` directory. Do not
launch a duplicate run. The first full trial `troy-ocr8-full-20260928-182704`
finished: OCR preserved all 3,921 sampled frames, retained tracks and preview
hashes, but logo cache v1 lost middle-frame regional evidence. Its faster total
time is NOT an accepted performance result. The corrective run reuses that
verified OCR, rehashes the source and reruns logo/localization/review with v2.
Original Dashboard job #39 is unchanged.
Harness `reports/benchmarks/run_troy_ocr8_full.py`; read-only comparator
`reports/benchmarks/compare_troy_ocr8_full.py`. Corrected raw logo results match
the production cold baseline: 199/199 regional leads, 116 intervals, all 608
pipeline JPEGs. Localized differences are only six documented telemetry fields.
Review proposals match (6 primary / 294 advisory); all 123 source candidates are
represented and Structure Audit passes. Original job #39/revision 3 and source,
review and brand-memory checks remain unchanged (`integrity-check.json`).
`verify_troy_logo_cache2.py` confirms the real warm v2 run matches the corrected
cold report and all 496 scanner JPEGs, including scores. See `warm-cache-check.json`;
the initial all-directory comparison is retained: it counted 112 downstream
GroundingDINO extraction images that the scanner-only warm run does not produce.
Warm logo stage is 181.821s versus cold 812.780s; this only measures same-video
routing-cache reuse, not new-video or whole-pipeline acceleration. The corrective
run reuses OCR, so its total is not a fresh end-to-end scan measurement.
No trial is still running. No Visual AI,
safety scan or export. The main Dashboard was not running at launch; next normal
start will load the new OCR selector. No merge/push authorized.

Next: focus bounded experiments on OCR/model computation (732.225s in this run)
and remaining logo feature work (493.007s), rather than frame read-ahead (OCR
14.609s / logo 2.246s pipe wait). A fair total speed claim needs matching cache
conditions and a controlled baseline/optimized run, not another unrelated historic
comparison. Do not launch additional long runs automatically. Keep OCR batch 1,
sampling/models/thresholds fixed and user decisions intact. No 5–10 minute scan
promise, merge or push is authorized.

Latest original-source OCR pilot (not a production job):
`reports/benchmarks/ocr-native-20260928-181450/` contains the comparison,
analysis, Vietnamese `SUMMARY.md`, isolated queues/previews and 268/268 test log.
Harness: `reports/benchmarks/ocr-native-pilot.py`. Source is the original Troy
file, SHA256 `f43cf94aadffb8c127c18fb23a51c58de2bdafcb2f05b1e91bd84be726fb19e9`.
At source 48–138s, 30 frames per run, native 1920x1080 geometry: all four runs
retain 42 tracks, 4 primary / 10 advisory review items and identical JPEGs.
Maximum confidence delta is 0.000001; source hashes and size/mtime agree.
Warm medians: OCR 11.089 -> 8.700s; total scan 33.257 -> 30.760s, of which
source hashing costs about 20.6s per run. Model loading (8.701s) and the 3-second
warmup are excluded from those medians. Complete visual-logo/AI audit, safety
detectors and export were not run; production Troy decisions are untouched.
The bounded native-source gate passes. Next target is a controlled opt-in
full advertising pipeline comparison when requested, or removing demonstrated
duplicate work/shared decode. Do not claim the Dashboard already uses batch 8.

Earlier synthetic stress evidence (not a production run):
`reports/benchmarks/ocr-stress-20260928-175841/`. Includes generated text labels,
48 source/decode RGB hashes, raw serial/batched predictions, isolated queues,
three real GPU cancellation results and `unittest.log` (268/268). Both effective
acceptance thresholds have nearby examples; nearest margins are 0.006515 at
0.35 and 0.008519 at 0.10, not arbitrarily close to numerical boundaries.
No text/acceptance differences; max confidence delta 0.000005918. Dedicated
`scripts/benchmark-ocr-stress.ps1` preserves the production launcher's fingerprint
and uses its GPU mutex. Three full reports/queues match except documented score
noise, with 9/12/4 tracks. Earlier 32-frame pilot is at `ocr-stress-20260928-175620`.
Do not claim unseen-film recall, all-detector coverage, OOM safety, Dashboard
button behavior or full end-to-end speed from these synthetic checks. The bounded
original-source pilot is now complete above; focus next on duplicate work/shared
decoding rather than endlessly expanding synthetic OCR tests.

Earlier contiguous OCR evidence (not a production run):
`reports/benchmarks/ocr-contiguous-20260928-172310/` contains `comparison.json`,
`analysis.json`, per-frame source/fixture hashes, isolated reports/queues/previews
and the 265/265 `unittest.log`. Six 90-second windows from Troy and Conan 20/21
retain 180 sampled RGB frames exactly. All 24 runs match baseline tracks,
regions, text/classifications and review mappings; all supplied OCR candidates
remain represented. The sum of per-window median wall times is 49.047 -> 41.415s
(15.56% less), on warm lossless sampled fixtures. This is not full-source scan
time, complete advertising coverage, or a benchmark of adult/gore/violence.
Measured PyTorch peak allocation is equal at 664.82 MiB for both paths; this is
not total GPU VRAM. Batch 8 remains opt-in. Synthetic moving banners,
near-threshold text and process-tree cancellation are now checked above;
resource exhaustion and a production-style batching pilot remain unvalidated.

Earlier OCR batching evidence (not a production run):
`reports/benchmarks/ocr-batch-20260928-165521/` and
`reports/benchmarks/ocr-batch-20260928-170046/`. Total 39 original-source frames,
109 text boxes, with unchanged text/accepted boxes for same-width batching.
Reversed-order lossless fixture pipelines are in `review-170011` and
`review-170317` respectively. Reports/previews match except confidence/runtime
metadata, with measured wall-time gains 6.33% and 3.70%. CLI supports experimental
`--recognition-batch-size 8`; default remains 1. Do not describe these as full-film
speedups or validated coverage for adult/gore/violence.

Latest experiment (not a production run):
`reports/benchmarks/frame-prefetch-20260928-161812/`. OCR samples at source
0–30s and 418–448s matched all detections/preview hashes across ten runs, but
wall time excluding source SHA changed by less than 1%, inconsistently. Keep
`prefetch_frames=0` in production. Next investigate OCR inference batching with
coverage/equivalence tests; do not broaden prefetch based on this result.

Phase 2 cache-only evidence is at
`reports/benchmarks/stage-cache-20260928-160614/`. A temporary project used copies
of the real Troy OCR artifacts: baseline cache missed after a UI edit; new cache
restored 252 identical files in 1.826s. This does not measure new-video inference.
Cache schema v2 rejects old v1 entries once; reports and decisions remain intact.
Restart the Dashboard when idle to load the updated scheduler/cache implementation.

Performance work did not rerun or replace the production job below. The new
isolated 12-second benchmark is under
`reports/benchmarks/scan-timing-20260928-154948/`: text, adult and shared live-action
gore/violence payloads match baseline `7f5a9fb`, with 54 identical JPEG hashes.
It does not validate full-film accuracy, animation, full visual-logo inference,
or a scan speedup. See `docs/SCAN_PERFORMANCE.md` for limitations and next steps.

Troy job #39 was rerun with detector scope `advertising` only after the V0.7.24 mapping fix.

- Source job key: `tm2-troy-2004-directors-cut-1080p-bluray-dd5-1-x-f43cf94a`
- Run directory: `reports/jobs/tm2-troy-2004-directors-cut-1080p-bluray-dd5-1-x-f43cf94a-run-20260927-234709`
- Final job state: `WAITING_REVIEW`
- Local Structure Audit: `PASS`
- Candidate coverage: complete
- Missing source references: none
- Source candidates: 123
- Review groups: 6, after deduplicating 117 repeated findings
- Regional visual candidates retained: 199/199
- Required full-frame representatives missing: 0
- Approved brand geometry/time groups represented: 39/39
- The XEMBZ.NET watermark is one persistent full-timeline BLUR proposal at approximately `x=118, y=173, width=150, height=31` in a 1920x1080 source.
- Opening promotions at approximately 00:05-00:15 and 00:20-00:25 are separate CUT proposals.

Evidence:

- `reports/jobs/tm2-troy-2004-directors-cut-1080p-bluray-dd5-1-x-f43cf94a-run-20260927-234709/structure-audit.json`
- `reports/jobs/tm2-troy-2004-directors-cut-1080p-bluray-dd5-1-x-f43cf94a-run-20260927-234709/review-queue.json`
- `reports/jobs/tm2-troy-2004-directors-cut-1080p-bluray-dd5-1-x-f43cf94a-run-20260927-234709/visual-logo/scan-localized.json`

This run skipped `adult`, `gore`, and `violence`. It validates advertisement/logo selection and mapping only.

## Recent fixes and what they mean

### V0.7.24: logo candidate coverage

- Approved brand-memory matches require similarity >=0.94 before they bypass semantic confirmation.
- Strong matches are grouped by learned logo geometry plus five-minute timeline bucket. Different memory records for the same physical logo no longer consume the candidate budget as separate tracks.
- Weak memory matches return to normal regional/full-frame routing.
- The selector keeps every regional lead, two complementary full-frame representatives per bucket, and one representative per approved geometry track per bucket.
- Candidate selection can no longer exceed its configured budget.
- This changes routing and coverage accounting. It does not change the base OCR, Florence, GroundingDINO, Qwen, adult, gore, violence, or render models.

### V0.7.23: sustained explicit-scene priority

- Adult findings labelled `porn` or `hentai`, score >=0.99, and duration >=4 seconds are promoted to high review priority.
- This only changes review order. It does not create or approve an edit.
- Troy evidence exists at `15:28.5-15:50.5` and `16:23-17:07`; the interval between them is a banquet cutaway.

### V0.7.22: queue revision refresh

- An open Review page polls for a new queue revision and reloads it without losing the selected filter.
- This fixes the case where a rerun completed but the browser continued showing the previous queue.

### V0.7.20: live-action adult sequence completion

- A score >=0.95 is still required to create an adult review seed.
- Nearby evidence >=0.70 may extend an existing seed by at most eight seconds on each side.
- Short gaps up to three seconds may be merged within the same explicit sequence.
- The measured Troy sequence around 06:56 is now presented as approximately `06:54.5-07:45.5`.

## Remaining work in priority order

Updated 2026-10-02 evening (after commits 53cd9a6..f997dde). The user's requests are quoted in Vietnamese.

### A. Pending user steps
1. Restart the Control Center while the queue is idle, so it serves the dashboard and structure-audit fixes of 236fb06 (the running process still has the old page).
2. Export Nhất Âu Xuân Tập 14 (job 44). It is reviewed (2 x KEEP) and READY_TO_EXPORT.
3. Optional: re-run the local structure audit for jobs 40-50. Their stored structure-audit.json still says BLOCK, which the new dashboard labels "quy tắc cũ".

### B. Dashboard / workflow requests from the user (2026-10-02, not started)
4. **Skip button for videos with nothing to review.** User request: "đối với video mà không có cảnh nào để duyệt khi chạy cảnh để duyệt thì có thêm một nút bỏ qua".
   - Today such a job goes straight to READY_TO_EXPORT (e.g. Tập 13), and the only way to finish it is to export a re-encoded copy with the same content.
   - Add "Bỏ qua (không xuất)", which marks the job done without rendering. Keep reports and never touch the source.
   - Confirmed by the user (2026-10-02): "Bỏ qua là đánh dấu xong mà không xuất video". A skipped job moves to "Hoàn tất".
5. **Sticky job tabs.** User request: "thanh menu của đang xử lý, đang chạy, hoàn tất … luôn luôn được giữ lại khi scroll". The tab bar must stay visible under the header while the list scrolls (position: sticky).
6. **Run the queue in click order.** User request: "chạy queue phải chạy theo thứ tự bấm trước bấm sau … hiện tại đang chạy từ trên xuống".
   - Today `scheduler._select` walks `JobStore.list_jobs()` (`ORDER BY priority, updated_at DESC`), so the most recently touched queued job runs first.
   - Use FIFO by queue time: a queued_at value set on JOB_QUEUED, EXPORT_QUEUED and rerun, with ties broken by id. Keep explicit priority.
   - Show each card's queue position. Check that heartbeat or progress updates do not reorder the queue.
7. **Tabs by stage.** User request: add "Đang chờ chạy cảnh để duyệt", "Đang chờ duyệt", "Đang chạy xuất video", and rename "Đang chạy" to "Đang chạy cảnh".
   - "Đang chờ chạy cảnh để duyệt" = queued for scanning.
   - "Đang chờ duyệt" = WAITING_REVIEW.
   - "Đang chạy xuất video" = export queued or RENDERING.
   - Map every job state to exactly one tab, with counts. Keep the tab bar sticky (item 5).

7b. **Finished videos move to "Hoàn tất", plus a cleanup that deletes exported inputs.**
   - Skip (item 4) and finished exports must both land in the "Hoàn tất" tab.
   - Compression is dropped. Measured 2026-10-02: zipping the 240.5 MB Tập 12 input gave 239.5 MB with deflate-9 (13 s) and 241.9 MB with LZMA (128 s). H.264 MP4 is already compressed, and zlib/lzma samples also gave 1.000.
   - User decision (2026-10-02), replacing compression: "bỏ qua việc nén -> thay nó bằng tính năng dọn dẹp các mục input đã xuất … kiểu như sẽ xóa đi". The user thereby approves changing the AGENTS.md invariant "never modify or delete source videos" for this explicit, user-triggered cleanup only. Update AGENTS.md when it is implemented.
   - Design notes:
     - A "Dọn video gốc" action in "Hoàn tất", per video and for all selected.
     - It lists each input with its size, export file and export time, and the total space freed, then asks for one confirmation.
     - It offers only jobs whose final export exists and passed its manifest check, or that the user skipped.
     - It never touches reports, state, decisions, brand/studio memory or outputs.
     - It records an event with path, size and SHA-256.
     - User decision (2026-10-02): move to the Windows **Recycle Bin**, not a permanent delete ("chuyển vào thùng rác thôi"). Space is freed when the user empties the bin; tell them so in the dialog. Use the Windows shell API (SHFileOperation/IFileOperation with FOF_ALLOWUNDO) or Microsoft.VisualBasic FileSystem.DeleteFile(..., SendToRecycleBin) through PowerShell. Add no new dependency without a license check.
     - Afterwards the job shows "Đã dọn video gốc". "Chạy lại kiểm tra" is disabled with the message "Chép lại video gốc vào input để chạy lại". A re-added file is accepted only if its SHA-256 matches.
     - Discovery must not recreate a job for a cleaned file.
     - Agents never run the cleanup themselves.
   - Still open, as a user decision: (c) export near the source bitrate. Exports are about 1.75x their source (Tập 12: 240 MB at 0.67 Mbps in, 422 MB at 1.05 Mbps out), so this would save about 180 MB per episode. Measure SSIM/VMAF first; it changes export behaviour.

7c. **"Xuất video" button on the dashboard job card.** User request (2026-10-02): "bổ sung thêm một button xuất video bên ngoài … đối với những video đã duyệt cảnh rồi, logic y chang bên trong duyệt cảnh nút xuất video".
   - For jobs whose review is complete (queue READY_FOR_EDIT_PLAN / job READY_TO_EXPORT), show "Xuất video" on the card.
   - It reuses exactly the review page's finalize path: the same size-policy choice (default / custom maximum GB), the same confirmation text, the same POST to finalize, and the same "every item decided" gate.
   - Hide or disable it while an export is queued or rendering.
7d. **Close the export dialog after confirming.** User request (2026-10-02): on the review page, "Xuất video" opens the export panel (`<details>` holding #export-panel and the button "Hoàn tất duyệt và xuất video" → finalizeExport()). After the user agrees in the confirm() dialog, close that panel automatically; today it stays open.
   - Close it right after the user confirms, and show the export progress or result in the page header or as a notice.
   - If the request fails, reopen the panel with the error.

### C. Detection quality (needs GPU measurement, about 3-4 h together; ask before running)
8. **Visual-logo candidate budget.**
   - Problem: adaptive_candidate_budget gives 18 windows per 5-minute bucket, which leaves 125-290 regional leads per Nhất Âu Xuân episode unchecked by Qwen (now a structure-audit WARN).
   - Measure a larger budget (about +40-60 s GPU per episode) on Nhất Âu Xuân plus Golden Troy/Conan.
   - This changes visual_logo output and invalidates its cache.
9. **Region-less "logo" false alarm in mid-film.**
   - Example: Tập 14 37:05, a torch scene where Qwen said YES and nothing was located. It is 1 card across all current queues.
   - Measure together with item 8, since more windows mean more chances of such false alarms.
   - Options: a second-opinion prompt, or advisory routing (the user must approve).
10. **Gore C1** (temp/wt-gore, off by default).
    - The patch no longer applies cleanly: review_workflow.py needs a re-merge.
    - Integrate only when a full scan-cache invalidation is acceptable, because it changes cli.py.
    - Enabling it needs the user's Conan gore decisions and a third anime film.

### D. Later
11. Carry reviewed decisions across a rerun (stash@{0}, paused 2026-10-01).
12. 18+ "balanced" triage level (needs a second live-action film).
13. Speed: shared decode / T2-T3, only after measurement.
14. Merge into `main` or push only with the user's explicit authorization. Commits 53cd9a6..f997dde are local on `improve/scan-performance-metrics`.

## Safety and product constraints

- Source videos are immutable.
- No automatic KEEP, BLUR, CUT, upload, or publish.
- All models must be free to run locally and commercially usable under the recorded policy.
- AI Supervisor is optional. Deterministic local Structure Audit uses no ChatGPT quota. Visual AI Audit sends only explicitly approved thumbnails and never source video/audio.
- A detector scope containing only advertising, adult, gore, or violence must clearly list skipped categories.
- Review decisions and brand memory are durable product data. Test previews and bounded caches are disposable only through their defined cleanup policy.

## Commands for a new session

```powershell
Set-Location E:\DungChung\BiliFlow
git status --short --branch
git log -8 --oneline --decorate
. .\scripts\env.ps1
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

Start or stop the dashboard with `Start-BiliFlow.cmd` and `Stop-BiliFlow.cmd`. Do not assume that closing the browser tab stops the backend.

## Prompt to paste into a new chat

```text
Làm việc trong E:\DungChung\BiliFlow. Trước khi sửa, hãy đọc AGENTS.md, docs/SESSION_HANDOFF.md và docs/SCAN_PERFORMANCE.md, sau đó kiểm tra git status và 8 commit gần nhất. Tiếp tục trên nhánh improve/scan-performance-metrics; chưa merge/push nếu tôi chưa yêu cầu. Hãy đối chiếu mọi kết luận với report/job thực tế, giữ nguyên nguyên tắc video nguồn bất biến, model local miễn phí/commercial-safe và mọi edit phải qua người duyệt. Sau khi nắm trạng thái, tóm tắt ngắn: việc đã hoàn tất, bằng chứng kiểm chứng mới nhất, việc còn lại theo ưu tiên và bước tiếp theo bạn sẽ làm.
```
