# BiliFlow session handoff

Updated: 2026-09-28 (Asia/Bangkok)

This is the short, authoritative starting point for a new Codex account or chat. It complements the detailed history in `PROJECT_STATUS.md` and `CHANGELOG.md`.

## Repository state

- Project root: `E:\DungChung\BiliFlow`
- Active branch: `main`
- Local `main` was fast-forwarded through `ef45b32` after 228/228 tests passed. Before this status refresh it was eight commits ahead of `origin/main`.
- Latest verified feature commit: `0b4ff3c Map logo candidates by geometry track`
- Runtime source version: `src/biliflow/__init__.py` reports `0.7.24`
- Packaging metadata in `pyproject.toml` still reports `0.7.19`; use the runtime source version for dashboard diagnosis and align the package metadata during a later release housekeeping change.
- Working tree was clean when this handoff was written.
- The user approved the merge after testing. Continue new work directly on `main`; do not push unless the user asks.

The improvement sequence now present on local `main` is:

1. `b0088f8 Complete live-action adult scene coverage`
2. `ff5c7d4 Keep rerun controls open during refresh`
3. `0318a22 Refresh review after queue revisions`
4. `5dce1d2 Prioritize sustained explicit scenes`
5. `0b4ff3c Map logo candidates by geometry track`

Always confirm this section with `git status` and `git log` because it becomes stale after new work.

## Last verified production-style run

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
4. **Reduce scan time without lowering coverage.** Instrument per-stage wall time first. The last advertising-only Troy rerun took roughly 27 minutes: OCR about 13 minutes and visual-logo routing/localization about 14 minutes. The requested 5-10 minute full-video target is not yet achieved.
5. **Performance work after measurement.** Reuse a bounded shared decode/frame broker across compatible OCR and visual stages, retain cache keys based on source SHA/config/model revision, and auto-clean old cache through the existing storage policy. Do not reduce sampling density or model thresholds without an A/B regression.
6. **Optional export acceleration comes later.** NVENC is currently blocked by the installed driver/FFmpeg API mismatch. Smart Render is unsafe for timelines with persistent blur unless continuity and full output validation are proven. CPU libx264 remains the production path.
7. **Push only after user authorization.** The improvement branch has already been fast-forwarded into local `main`; the local commits are not yet on the remote.

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
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

Start or stop the dashboard with `Start-BiliFlow.cmd` and `Stop-BiliFlow.cmd`. Do not assume that closing the browser tab stops the backend.

## Prompt to paste into a new chat

```text
Làm việc trong E:\DungChung\BiliFlow. Trước khi sửa, hãy đọc AGENTS.md và docs/SESSION_HANDOFF.md, sau đó kiểm tra git status và 8 commit gần nhất. Tiếp tục trực tiếp trên nhánh main hiện tại và chưa push nếu tôi chưa yêu cầu. Hãy đối chiếu mọi kết luận với report/job thực tế, giữ nguyên nguyên tắc video nguồn bất biến, model local miễn phí/commercial-safe và mọi edit phải qua người duyệt. Sau khi nắm trạng thái, tóm tắt ngắn: việc đã hoàn tất, bằng chứng kiểm chứng mới nhất, việc còn lại theo ưu tiên và bước tiếp theo bạn sẽ làm.
```
