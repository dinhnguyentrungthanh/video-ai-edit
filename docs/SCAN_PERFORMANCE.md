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

## Phase 3 experiment — bounded OCR prefetch

`FramePrefetch` can read original FFmpeg raw bytes ahead on one CPU thread.
Depth is limited to 0..4; the 32 MiB raw-buffer budget reserves queue frames plus
three transient/consumer buffers, falling back to serial for oversized frames.
This budget excludes FFmpeg/model/image-processing RAM. A full queue blocks the
producer; it never replaces or drops frames. EOF/partial-frame bytes and reader
errors reach the original scanner checks. Early exit terminates the owned FFmpeg
process before joining the thread, avoiding a blocked pipe-reader lock.

The Python OCR function accepts `prefetch_frames`, default **0**. No production
profile/CLI scan default enables it. Only the benchmark opts into depth 2.
`metrics.frame_prefetch` records requested/effective depth and budget. Pipe-wait
timing in prefetch mode measures consumer queue wait, not the producer's CPU work.

Run the bounded benchmark with the normal GPU mutex:

```powershell
.\scripts\run.ps1 benchmark-frame-prefetch --input "E:\DungChung\BiliFlow\input\VIDEO.mp4"
```

The script permits at most three excerpts of 30 seconds each and writes separate
evidence under reports. The default samples are 0–30s and 418–448s. It loads local
OCR/semantic models once, warms up, then runs baseline / prefetch / prefetch /
current-serial / baseline. It compares detection payloads (excluding runtime
metadata) and all JPEG hashes, without transcode or changes to the source/queue.

Troy experiment against baseline `8a2caf3`:

| Excerpt start | Baseline median excluding SHA | Prefetch median excluding SHA | Exact payload/JPEG matches |
| --- | ---: | ---: | --- |
| 0s | 2.886s | 2.892s | All five runs |
| 418s | 2.333s | 2.325s | All five runs |

Each run processes ten original samples at the unchanged three-second OCR
interval. The opening excerpt has 13 preview JPEGs; the later excerpt has one.
Hashing the entire 7.609 GB source takes about 20–22 seconds per short invocation,
so whole-call medians are 23.256/24.026s and 22.731/22.728s respectively. This cost
must not be mistaken for OCR inference time or extrapolated per 30-second window
in a full-film scan: the production scanner hashes once per invocation.

**Decision: keep prefetch disabled by default.** The sub-1% changes after excluding
SHA are inconsistent and do not demonstrate a useful gain. These samples prove
OCR equivalence only, not general detector accuracy, safety-group coverage or
full-film throughput. Broader prefetch activation is not justified.

Evidence: `reports/benchmarks/frame-prefetch-20260928-161812/comparison.json`
and `analysis.json`. Full unittest: **252/252**; log at
`reports/benchmarks/prefetch-unittest.log`. Tests cover ordering, partial frames,
EOF, producer exceptions, bounded backpressure, memory fallback and cancellation
of a real child process blocked on a pipe.

## Phase 3 experiment — recognition batches with unchanged crop padding

Inspection of the installed EasyOCR implementation found that ordinary
`readtext(batch_size=8)` changes the padding width: it uses a common frame-wide
maximum instead of each crop's serial width. A/B confirmed changed text, so this
path is rejected even though the sampled changes were below acceptance thresholds.

The alternative in `ocr_batch_experiment.py` uses EasyOCR's unchanged crop
extraction and recognition functions, groups only identical serial padded widths,
and restores original output order. It retains character filtering, greedy
decoding, contrast retry and all existing thresholds. Batch count is at most 8;
input area is limited to `8 * 64 * 512` pixels per batch, with oversized crops
running alone. This bounds batching growth, not total VRAM/activation memory.

It is explicitly opt-in through Python `recognition_batch_size` and CLI
`--recognition-batch-size`; choices are 1, 2, 4, 8. **Default remains 1**. The
experimental path requires CUDA and the current vi/en Latin model. No Dashboard
profile enables it. Errors propagate normally; there is no skipped-frame or
silent drop fallback.

### Measured results

- Original source frames: Troy and Conan 20/21, two disjoint timestamp sets,
  **39 frames / 109 text boxes**. Extraction is single-frame 960px bilinear;
  lossless PNG fixtures are retained. These are fixed-frame equivalence tests,
  not annotated ground truth and not contiguous full-video sampling.
- Baseline/same-width recognition time, sum of per-frame medians: first corpus
  1.2213/0.9324s, second 0.8345/0.7321s; combined reduction **19.03%**.
- Same-width batches preserve all text and accepted box/text pairs in the corpus.
  Maximum confidence difference **1.100739e-6**. Serial repeat runs are exact;
  batched floating-point results are not numerically identical.
- Ordinary batch=8 changes text in four repeated runs of each corpus (eight
  changed runs, not necessarily eight distinct frames) and shifts confidence by
  up to 0.0633. It is not integrated.
- Warm reversed-order whole OCR calls on lossless fixture sequences include
  detection, recognition, tracking, semantics, source hashing and report writes:
  15-frame sequence **3.294 → 3.085s (6.33%)**; 24-frame sequence
  **3.813 → 3.672s (3.70%)**. These small test clips use one fixture per second;
  production sampling defaults are unchanged. No original source was transcoded.
- Both sequences retain track count (14 and 5), regions, times, classifications
  and identical JPEG hashes. Exact report comparison fails only on small OCR
  confidence changes after runtime metadata is excluded.
- This does not validate a production queue, Structure/Visual Audit, render,
  near-threshold cases in unseen movies, or safety detectors. Do not extrapolate
  these percentages to the complete 27-minute advertising pipeline.

Evidence roots:

- `reports/benchmarks/ocr-batch-20260928-165521/comparison.json`
- `reports/benchmarks/ocr-batch-20260928-165521/review-170011/comparison.json`
- `reports/benchmarks/ocr-batch-20260928-170046/comparison.json`
- `reports/benchmarks/ocr-batch-20260928-170046/review-170317/comparison.json`
- Final tests: **260/260**, second root's `unittest.log`.

Reproduce bounded tests, with the usual GPU mutex:

```powershell
.\scripts\run.ps1 benchmark-ocr-batch --input "E:\DungChung\BiliFlow\input\VIDEO.mp4"
.\scripts\run.ps1 benchmark-ocr-batch --review-fixtures "reports/benchmarks/ocr-batch-RUN"
```

A separate short opt-in scan can use `scan-text --recognition-batch-size 8` with
`--start-seconds`, `--duration-seconds` and a new report directory. Preserve the
default batch=1 baseline for comparison; do not replace a live job's reports.

### Contiguous sampling and isolated review validation

`benchmark-ocr-contiguous` accepts a JSON plan containing `segments`, each with
`source` (absolute input path), `start` and `seconds`. Starts and lengths must be
multiples of 3 seconds, at most six excerpts, each at most 120 seconds. It uses
the same GPU mutex as production inference:

```powershell
.\scripts\run.ps1 benchmark-ocr-contiguous --plan reports/benchmarks/ocr-contiguous-plan-20260928.json
```

Evidence: `reports/benchmarks/ocr-contiguous-20260928-172310/`. Six 90-second
windows cover Conan 20 at 240/600s, Conan 21 at 4140/6630s and Troy at 0/11640s.
There are 180 unique sampled frames, representing 9 minutes of source timeline;
this is not a scan of every video frame. RGB frame hashes verify lossless
sampling fixtures against the original decode/scale output before inference.
The fixtures are 960px FFV1 clips already sampled at 1/3 fps. They are removed
after comparison; reports, previews and hashes remain. Isolated queues refer to
those temporary sources and are evidence only, not production export inputs.

Each excerpt has an untimed warmup and serial/8/8/serial runs. All 24 measured
runs preserve accepted report fields except confidence/runtime, all JPEG hashes,
and review projections including geometry, dimensions, blur edge mode, actions,
intervals, labels, reasons and source-candidate references. All supplied OCR
candidates remain represented; 273 reference tracks across the six excerpts.
Maximum confidence difference: **0.000006**. This comparison preserves existing
false positives too; it does not measure ground-truth recognition accuracy.

| Excerpt | Serial median | Batch 8 median | Reduction |
| --- | ---: | ---: | ---: |
| Conan 20, 240–330s | 4.645s | 4.640s | 0.12% |
| Conan 20, 600–690s | 5.735s | 5.358s | 6.57% |
| Conan 21, 4140–4230s | 6.337s | 5.397s | 14.84% |
| Conan 21, 6630–6720s | 5.158s | 4.867s | 5.65% |
| Troy, 0–90s | 11.630s | 9.101s | 21.74% |
| Troy, 11640–11730s | 15.542s | 12.053s | 22.45% |

Summed medians: **49.047 -> 41.415s (15.56% reduction)**. This measures warm
OCR-to-report calls on sampled fixtures; it excludes original-source decoding
and hashing, cold loading, review construction, other detectors, audit and export.
Do not extrapolate it to a complete film's elapsed time.

RTX 2060 PyTorch peak allocated memory is 697,112,064 bytes (664.82 MiB) in both
modes; peak reserved is 828,375,040 bytes (790 MiB). Sampled process RAM peaks at
2,065,948,672 bytes (about 1.924 GiB). Allocator counters exclude other GPU
allocations and RAM is sampled; none is a hard resource cap. Tests confirm a
batch interruption propagates without returning partial predictions; existing
raw-reader tests cover blocked-pipe cleanup. Real GPU stop-button/cancellation
latency is not measured here. Full suite: **265/265**, `unittest.log` in the root.

### Synthetic moving text, threshold and cancellation follow-up

Run independently with `.\scripts\benchmark-ocr-stress.ps1`. This runner takes
the existing GPU mutex without editing `scripts/run.ps1`, whose content is part
of production stage-cache identity. No downloads or external API calls occur.

Evidence: `reports/benchmarks/ocr-stress-20260928-175841/`. The 48 generated,
labeled frames contain moving/clipped/disappearing banners, independent static
text, blur/noise/low contrast and long top-banner text. Source/decode RGB hashes
match for all 48 frames. Synthetic clips and PNGs remain as reproducible test
evidence (about 12 MB), not production media. The earlier 32-frame pilot remains
at `ocr-stress-20260928-175620/`; do not silently delete either evidence root.

- All **183 raw regions** retain text and accepted-box/text pairs; maximum
  confidence change is **0.000005918**.
- Acceptance diagnostics use the scanner's actual text/geometry rules, not just
  proximity to a numeric threshold that may not apply. There are 2 eligible
  examples within 0.05 of 0.35 and 3 within 0.03 of the 0.10 banner threshold.
  Closest margins are 0.006515 and 0.008519. Arbitrarily close boundary cases are
  not proven safe; finite equivalence tests cannot guarantee all unseen inputs.
- Three separate OCR-to-review pipelines retain report content except score/
  runtime noise, JPEG hashes, regions/actions and reference mappings. Track
  counts: **9, 12, 4**. All supplied OCR candidates are represented. Labels record
  rendered text, including clipped/degraded examples; this is not a claim that
  every label is readable or correctly recognized by the baseline detector.
- Three workers reached a real CUDA recognition checkpoint, then the production
  scheduler's process-tree stop method terminated them and their FFmpeg children
  in **0.154–0.164s**. No final partial scan report appeared. Subsequent GPU
  inference completed successfully. Only benchmark-created processes were stopped;
  no live scheduler/store was instantiated or modified. This is not an end-to-end
  Dashboard pause/UI test and does not simulate OOM or driver failure.
- **268/268 unittest passes**, including effective-threshold diagnostic checks;
  `unittest.log` is retained in the evidence directory.

These follow-ups complete the bounded synthetic moving-text/threshold and normal
GPU cancellation checks. They do not establish full-film recall or speed. Default
remains serial; the next batching step is a bounded original-source pilot for
source-resolution geometry and elapsed timing, rather than more synthetic loops.
Resource exhaustion is still unvalidated. No production job, audit or export ran.

## Remaining implementation sequence

1. **Remove additional demonstrated duplicate work.** Dependency-scoped stage
   reuse is complete. Inspect hashing and repeated frame extraction next, without
   replacing source-content verification with filename/mtime guesses. Cache reuse must require
   the same source SHA, model/revision, preprocessing, scope and configuration.
   Never reuse results between different source videos by filename or appearance.
2. **Validate opt-in OCR recognition batching.** Same-width batches show modest
   gains on fixture pipelines but need the gate above before default activation.
   Prefetch remains off. Preserve exact timestamps,
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
