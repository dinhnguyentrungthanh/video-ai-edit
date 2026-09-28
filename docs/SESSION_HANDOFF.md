# BiliFlow session handoff

Updated: 2026-09-28 (Asia/Bangkok)

This is the short, authoritative starting point for a new Codex account or chat. It complements the detailed history in `PROJECT_STATUS.md` and `CHANGELOG.md`.

## Repository state

- Project root: `E:\DungChung\BiliFlow`
- Active branch: `improve/scan-performance-metrics` (new user request; remain here).
- Base local `main`: `7f5a9fb`; ten commits ahead of `origin/main` when this branch was created. Previous detector improvements are already on local `main`.
- Latest code milestone: `6231f58` fixes lossy visual-logo routing cache serialization; 276/276 tests. `bf5bc35` adds per-job standard/experimental OCR controls with preserved serial default. Full Troy advertising equivalence and corrected cold/warm logo verification now pass. No material full-film OCR acceleration was demonstrated. Prefetch stays OFF. See `docs/SCAN_PERFORMANCE.md`.
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

## Last verified production-style run

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

Next: profile CPU logo feature extraction versus brand-memory matching and OCR
detection versus recognition on bounded excerpts. The cold logo stage spends
612.124s in CPU routing, while full OCR batch 8 takes 765.102s against historical
serial 770.734s. Do not enable batching by default or promise a 5–10 minute scan.
Keep sampling/models/thresholds fixed; benchmark any optimization before adoption.

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

1. **Human-check the new Troy advertising queue.** Confirm the full-timeline XEMBZ.NET region is tight and that the two opening CUT proposals are correct. This is a review task, not another detector change.
2. **Run one fresh all-detector Troy regression when requested.** Confirm the V0.7.20 adult interval and V0.7.23 high-priority intervals appear in the current Review UI. The latest verified V0.7.24 run was advertising-only.
3. **Benchmark across Troy, Conan, and Shin.** Record recall-oriented logo coverage, false review groups, stage durations, and output correctness. Do not claim general logo accuracy from Troy alone.
4. **Reduce scan time without lowering coverage.** Phase timing instrumentation is complete on the improvement branch. Collect these timings on the next authorized run; older reports cannot supply them. The last advertising-only Troy rerun took roughly 27 minutes: OCR about 13 minutes and visual-logo routing/localization about 14 minutes. The requested 5-10 minute full-video target is not yet achieved.
5. **Performance work after measurement.** Dependency-scoped exact stage cache is implemented. OCR prefetch remains off because its benefit was negligible. Opt-in same-width OCR passes sampled/synthetic/native-source comparisons plus actual GPU worker cancellation. The native Troy pilot improves total OCR scan time 7.51%, not full pipeline time. Keep serial default; a controlled full advertising opt-in comparison remains to be requested/integrated. There is no OOM/exhaustion guarantee. Inspect repeated source hashing/shared decoding next without weakening content identity. Keep source SHA/config/model revisions and existing bounded cleanup policy. Do not reduce sampling density or model thresholds without an A/B regression.
6. **Optional export acceleration comes later.** NVENC is currently blocked by the installed driver/FFmpeg API mismatch. Smart Render is unsafe for timelines with persistent blur unless continuity and full output validation are proven. CPU libx264 remains the production path.
7. **Merge/push only after user authorization.** The previous improvement branch was merged into local `main`; the new `improve/scan-performance-metrics` branch is separate and unmerged. Local commits are not yet on the remote.

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
