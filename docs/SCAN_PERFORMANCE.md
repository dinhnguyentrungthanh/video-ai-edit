# Scan performance work — 2026-09-28

## Current milestone

Phase 1 (measurement and equivalence checks) is implemented on
`improve/scan-performance-metrics`, based on local `main` at `7f5a9fb`.
This milestone does not claim faster scans. Detector sampling, input tensors,
thresholds, review decisions, model selection and export behavior are unchanged.

Collectors record bounded aggregate timings, not a growing per-frame trace:
model load, frame-pipe wait, model work, source hashing, preview writes, OCR
tracking/semantics and logo routing. Localization, GroundingDINO augmentation
and violence confirmation use separate metric keys so the original scan profile
is not overwritten. The existing GPU mutex logs acquisition wait time.

Timing is exclusive host wall time. Nested operations are not double-counted.
No CUDA synchronization was added; these numbers are not GPU kernel timings.
Pipe wait measures the consumer's wait/copy, not total concurrent FFmpeg decode.
Snapshots cover collector creation through snapshot, excluding later work and
final serialization. Shared gore/violence reports repeat the same snapshot;
never sum them as independent scans.

## Evidence

- Full unittest suite: **236/236 passed**; log:
  `reports/benchmarks/scan-timing-20260928-154948/unittest.log`.
- CUDA equivalence smoke: a temporary 12-second Troy excerpt, source seconds
  **418–430**, compared against scanner modules from commit
  `7f5a9fbca04e96a6ae7e220679c3e2e165629130`.
- OCR/text semantics, adult and shared live-action gore/violence returned equal
  detection payloads after excluding telemetry/runtime metadata. All **54 JPEG
  preview hashes matched** (1 text, 21 adult, 32 shared safety).
- Evidence: `reports/benchmarks/scan-timing-20260928-154948/comparison.json`.
- This is a transcode-based short equivalence smoke, not an accuracy benchmark,
  an end-to-end job test, or proof of full-film speedup. Florence/Qwen/DINO and
  animation instrumentation still require real-video A/B coverage in a future
  authorized benchmark. The sequential old/new order is not suitable for speed
  comparisons because initialization and OS/model caches differ.
- No production queue, review decision, brand memory, input or output was changed.

The read-only summary of existing advertising-only Troy job **39**, run
`20260927-234709`, gives:

| Completed stage | Wall seconds |
| --- | ---: |
| Preflight | 19.865 |
| Text | 770.734 |
| Visual logo | 642.611 |
| Localization | 207.277 |
| Build review | 8.139 |
| Total | 1648.627 |

This old run has no internal phase telemetry; missing values are reported as
unknown, never zero. It skipped adult, gore and violence and cannot benchmark
an all-detector run.

## Commands

Read current job timing without starting or rerunning anything:

```powershell
Set-Location E:\DungChung\BiliFlow
. .\scripts\env.ps1
.\.venv\Scripts\python.exe -m biliflow.performance_report --job-id 39
```

The reader opens SQLite read-only, restricts profiles to the current run and
marks cache-restored timings as historical. The stage total uses completed
stages only. Shared profiles are displayed but never added to that total.

Short equivalence test (only when a sample run is wanted):

```powershell
.\scripts\run.ps1 benchmark-scan-timing --input "E:\DungChung\BiliFlow\input\VIDEO.mp4" --baseline-ref 7f5a9fb --start 418 --seconds 12
```

The script limits excerpts to 30 seconds, checks model licenses, uses the normal
GPU mutex through `run.ps1`, writes evidence under `reports/benchmarks`, and
removes its temporary excerpt under `temp`. It does not enqueue a production job.
Preserve benchmark evidence; no automatic cleanup of reports was added.

## Phase 2 — dependency-scoped exact stage reuse

Implemented after phase 1 on the same branch:

- Follow eager and lazy local Python imports from each known scanner entrypoint.
  Unresolved imports, syntax errors or dynamic imports fall back to all source.
  CLI/entrypoint, cache implementation and launcher contents remain in the key.
  `STAGE_MODULES` in `cache_dependencies.py` must be maintained when CLI dispatch
  gains new stage implementations. This is a dependency map, not video-specific
  detection logic.
- Keep the existing conservative model/config fingerprint; all model identities
  still invalidate cache, even if only a different model changed. Do not narrow
  model/config scope without tracing its use first.
- Add previously missing dependencies: OCR semantic seed, localizer script,
  environment launcher, tool identity, upstream `--report` content, explicit text
  policy/seed arguments, and brand memory for logo stages. Internal outputs of a
  multi-command stage are not mistaken for upstream inputs.
- Recompute fingerprints on lookup and store. A running dashboard no longer
  retains a stale first fingerprint. Scheduler captures the pre-execution key
  and skips cache storage if dependencies changed while the process ran.
- Cache schema v2 rejects old v1 entries; the next requested run may compute an
  affected stage once to populate v2. No automatic rerun or cache migration is
  performed. Existing 10 GiB / 14-day stage-cache pruning policy is unchanged.

Verified with `scripts/benchmark_stage_cache.py` and a temporary project under E:

- Source evidence: the existing job #39 Troy OCR report listed above.
- Baseline `75d0481`: cache miss after a UI-only edit.
- New cache: hit, **252 files identical by SHA-256**, lookup/copy **1.826s**.
- Results: `reports/benchmarks/stage-cache-20260928-160614/comparison.json`.
- Full unittest: **247/247 passed**, including invalidation for command/source,
  transitive and lazy imports, model/config/seed changes, brand memory, upstream
  reports, mid-execution dependency changes, and scheduler subprocess avoidance.
- This benchmark copies actual report files into a temporary project; it does
  not load models, decode video, alter the production job or run an all-detector
  regression. Its timing excludes real-project model identity enumeration and
  should not be presented as the duration of a production rerun.
- A read-only fingerprint probe on the real project took 0.903s for the first
  text call, then 0.050s adult and 0.096s logo (OS cache warm). These are diagnostic
  samples, not a sustained throughput benchmark.

Reproduce only the isolated artifact-cache comparison:

```powershell
. .\scripts\env.ps1
.\.venv\Scripts\python.exe scripts/benchmark_stage_cache.py --report "reports/jobs/RUN/text/text-scan.json"
```

The Dashboard must restart when idle to load scheduler changes. No automatic
restart was performed. This phase improves reuse on exact reruns after unrelated
code edits; it does not accelerate the first scan of a new video.

## Remaining implementation sequence

1. **Remove additional demonstrated duplicate work.** Dependency-scoped stage
   reuse is complete. Inspect hashing and repeated frame extraction next, without
   replacing source-content verification with filename/mtime guesses. Cache reuse must require
   the same source SHA, model/revision, preprocessing, scope and configuration.
   Never reuse results between different source videos by filename or appearance.
2. **Bounded prefetch, then OCR batching experiments.** Preserve exact timestamps,
   dimensions, frame order and end-of-stream handling. Cap RAM/VRAM, support
   cancellation and keep a serial fallback. Batching changes numerical execution;
   enable it only after model-output and coverage comparisons pass.
3. **Extend shared decode only across compatible consumers.** Existing live-action
   gore/violence sharing remains intact. A broker must preserve each model's
   sampling schedule, image size and transforms, and must not let a slow consumer
   drop frames or another detector's findings.
4. **Benchmark before enabling optimizations.** Use short advertising, adult,
   gore/violence and negative examples, including live action and animation.
   Then validate Troy, Conan and Shin when a long rerun is requested. Compare
   sample timestamps, candidates, intervals/regions, audit completeness, peak
   resources and cold/warm wall time separately. Run focused and full unittest
   suites after detector/scheduler/review changes.

Do not reduce sampling, lower resolution, remove audit stages or loosen thresholds
to meet a speed target. No 5–10 minute full-film guarantee is supported yet.
Export acceleration remains a separate, later experiment. Do not merge this
branch or push until the user requests it.
