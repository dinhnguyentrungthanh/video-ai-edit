# BiliFlow session handoff

Updated: 2026-10-05 (Asia/Bangkok)

This is the short, authoritative starting point for a new Codex account or chat. It complements the detailed history in `PROJECT_STATUS.md` and `CHANGELOG.md`.

## Repository state

- Project root: `E:\DungChung\BiliFlow`
- Branch `feat/dashboard-v2` (from `main` f6996bb, pushed 2026-10-03) holds the Dashboard V2 prototype and its integration plan.
  - 2026-10-05: it also holds the V2 review dialog (batches R0–R4 of `docs/DASHBOARD_V2_REVIEW_PLAN.md`) and the local commit `0334f8c` (stronger logo cover in exports). See "Current work — 2026-10-05" below.
  - Per the plan's log, the main folder ran `a7d8f18` of this branch (detached) on 2026-10-05, with the Control Center on that code. Check with `git status` and `git log` before any claim.
  - A session working on this branch, including a Claude cloud session, starts with `docs/DASHBOARD_V2_REVIEW_PLAN.md` (the review dialog) and `docs/DASHBOARD_V2_CLOUD_PLAN.md` (the V2 integration and phone mode).
  - `main` does not have V2. The Control Center runs V2 only while the main folder is on this branch.
- Branch `feat/delete-flow` (from `55c6e62` of `feat/dashboard-v2`, worktree `temp/wt-delete-flow`, local commits only) holds the permanent delete flow. See "Current work — 2026-10-05 permanent delete flow" below.
- Branch `feat/video-download` (from `feat/dashboard-v2` a7d8f18, worktree `temp\wt-video-download`, 2026-10-05) holds the real video download feature, D0–D5 done (see "Current work — 2026-10-05 real video download" below). A session on this branch starts at `docs/VIDEO_DOWNLOAD_PLAN.md` and asks the user before downloading any tool or package. Dashboard V2 merges into `main` on its own.
- Branch `test/download-delete` (worktree `temp\wt-download-delete`, 2026-10-06) is `feat/delete-flow` with `feat/video-download` merged in, for the user's real-machine test of both. See "Current work — 2026-10-06 test branch" below.
- Active branch: `main`. On 2026-10-03 the user asked to merge `improve/scan-performance-metrics` (everything since `7f5a9fb`: the scan-performance work, detector/review fixes and dashboard batches 1-4) into `main` and run it there. `main` was fast-forwarded to the branch tip (the commit that carries this note) and the working tree, which the Control Center runs from, was switched to `main` with no file change. The branch is kept.
- Pushed at the user's request on 2026-10-03: `origin/main` (GitHub `dinhnguyentrungthanh/video-ai-edit`) moved `9155cd7..23aa1e4`. Local `main` has moved on since (the merge below); push again only when the user asks.
- Merged at the user's request on 2026-10-03 at about 21:40, with no job running: branch `fix/export-identity-http` (worktree `temp/wt-export-fix`) was fast-forwarded into `main`. It brings export identity from the render, reuse of only a proven export, HTTP request limits and the short-export rate cap (see Current work). `origin/main` was pushed later and is at f6996bb (checked 2026-10-04). The running Control Center keeps the code it started with until it is restarted (ask the user first).
- Latest code milestones: `fix/export-identity-http` (2a37496 export identity, proven-export reuse and HTTP limits; 732b02b short-export rate cap), 1197 tests OK. Before it: dashboard batch 3 `d90c8f3` and batch 4 `6a8a59c` (docs `bb29219`); 1130 tests OK. Earlier on the branch: `2c72280` reduces CPU RGB-distance overhead with bit-exact output; 278/278 tests. Six source excerpts show 20.50% lower CPU routing time, not whole-video scan time. Actual cold-routing/VLM-input checks also pass. Prior `6231f58` fixes lossy visual-logo cache; `bf5bc35` adds opt-in OCR controls with serial default. Prefetch stays OFF. See `docs/SCAN_PERFORMANCE.md`.
- Runtime source version: `src/biliflow/__init__.py` reports `0.7.24`
- Packaging metadata in `pyproject.toml` still reports `0.7.19`; use the runtime source version for dashboard diagnosis and align the package metadata during a later release housekeeping change.
- Confirm working-tree state with Git.
- Start new work on a new branch from `main`; merge or push only when the user asks.

Since 2026-10-03 local `main` also holds everything from `improve/scan-performance-metrics`. The earlier improvement sequence on local `main` is:

1. `b0088f8 Complete live-action adult scene coverage`
2. `ff5c7d4 Keep rerun controls open during refresh`
3. `0318a22 Refresh review after queue revisions`
4. `5dce1d2 Prioritize sustained explicit scenes`
5. `0b4ff3c Map logo candidates by geometry track`

Always confirm this section with `git status` and `git log` because it becomes stale after new work.

## Current work — 2026-10-06 test branch `test/download-delete` ("Tải video" and the permanent delete; local, not pushed)

- Made at the user's request: `git worktree add temp\wt-download-delete -b test/download-delete feat/delete-flow`, then a merge of `feat/video-download` (`caedb8b`). Neither feature branch was changed; their own sections below still describe them.
- Six files conflicted (`CHANGELOG.md`, `dashboard_v2/app.js`, `docs/PROJECT_STATUS.md`, `docs/SESSION_HANDOFF.md`, `src/biliflow/control_center.py`, `tests/test_dashboard_v2_review.py`); both sides were kept. See CHANGELOG for the details and the checks.
- The rules of both sections below apply: agents never POST to `/api/downloads*` or to the delete, cleanup, archive or restore routes on the real Control Center.
- Next: the user tests both features on the real machine with the main folder detached at this branch's merge commit (moved only with the user's consent and no job running), then decides about push and merge.

## Current work — 2026-10-05 permanent delete flow (branch `feat/delete-flow`, worktree `temp/wt-delete-flow`; local commits, not pushed)

- Plan: `docs/DELETE_FLOW_PLAN.md` (the user's decisions, what is deleted and kept, protections, API, UI, tests, phases, real-machine numbers). `AGENTS.md` has the new source-video invariant.
- The branch starts at `55c6e62` (`feat/dashboard-v2`). Phases D0 `22216af`, D1 `b3be8ca` and D2 `6c79aeb`, then D3 (Dashboard V2), D4 (classic page `/`) and D5 (docs, full suite) committed on the local machine. See CHANGELOG for the behavior.
- Agents never call `/api/source-cleanup`, `/api/job-delete`, `execute_cleanup`, `execute_delete`, `purge_job` or `delete_input_file` on user files; tests use temporary roots under `temp` only.
- Next:
  1. The user's test (plan section 8):
     - move the main folder to this branch and restart the Control Center (ask first, no job running);
     - "Dọn video mất gốc" for the 27 lost jobs;
     - "Xóa video gốc" for one exported video the user picks.
  2. Push or merge only when the user asks. `feat/dashboard-v2` still waits for the end of U-R4 and its own merge.

## Current work — 2026-10-05 real video download (branch `feat/video-download`, local; not pushed, not merged)

- Plan, decisions, results and log: `docs/VIDEO_DOWNLOAD_PLAN.md`. D0–D5 are done: D0 `6a6fdfd`, D1 `918bc2c`, D2 `a046a93`, D3 `cc7812b`, D4 `f378619`, D4b `6f4fbee`, and D5, the docs.
- What it does:
  - The V2 page `#downloads` takes links from any public site, on the PC or from the phone.
  - yt-dlp probes each page first. "Chưa hỗ trợ" means it cannot read a video there.
  - The download is checked, then moves into `input\` under a unique name; the watcher picks it up; no auto scan.
  - No cookies, logins, DRM work-arounds or site-specific code.
- Rules for agents (also in `AGENTS.md`):
  - Never start, resume or retry a download on the user's real Control Center, and never POST to `/api/downloads*` there.
  - Real checks use a test Control Center: `python -m biliflow --project-root <temp root> control-center --port 8797 --no-import-existing`, with a copied `config\` and `tools\` and links the user gave.
  - No real links, titles or non-example domains in the repository.
  - Ask before downloading any tool or package.
- The scan cache key files are untouched (`pyproject.toml`, `config/license_policy.json`, `scripts/env.ps1`, `cli.py`); tool pins live in `config/download_tools.json`.
- Next:
  1. Done 2026-10-06: the user's test on the real machine passed (main folder detached at `9781c70`, Control Center started 2026-10-05 23:43:59; one public YouTube video downloaded into `input`; a movie-site link read "Chưa hỗ trợ"; "Xóa khỏi danh sách" removes only the download row). The branch includes `feat/dashboard-v2` up to `55c6e62`.
  2. Next: combine with `feat/delete-flow` (D5 `0ed2f96`) on a test branch so the user tests both at once. Six files conflict: `CHANGELOG.md`, `dashboard_v2/app.js`, `docs/PROJECT_STATUS.md`, `docs/SESSION_HANDOFF.md`, `src/biliflow/control_center.py`, `tests/test_dashboard_v2_review.py`.
  3. Merge into `main` only when the user asks.

## Current work — 2026-10-05 Dashboard V2 review dialog R4 and the stronger logo cover (branch `feat/dashboard-v2`, pushed; not merged)

- Plan and checklist: `docs/DASHBOARD_V2_REVIEW_PLAN.md`. Section 7 has the batches and their tables, section 11 the log (newest first).
- Done on the cloud and checked on the local machine: R0 (view-only dialog), R1 (media), R2 (decisions), R3 (bulk actions and export, R2-B1, R2-B2). The user tried R2 on a real job (U-R2): pass.
- R4, done on the cloud on 2026-10-05:
  - "Duyệt cảnh" in the V2 drawer opens the review dialog (live and demo). The temporary "Duyệt (bản mới, thử)" button and the prototype review box are gone. The classic page stays byte-identical, one link away.
  - Phone and laptop layout (P17): 44 px touch targets on a phone and on any touch screen; text ≥ 12 px; a fading chip row; the tools row wraps; a full-screen dialog when the phone is held sideways.
  - A check through the real phone listener on a temporary root (`tests.test_dashboard_v2_review.ReviewR4PhoneListener`, Playwright).
  - R3-N1 blocked: one Esc closes only the dialog on top. The reason is under the R4 table of the plan.
  - The local check of R4 (`4c7ac3c`) passed R4.3, R4.4 and R3-N1, skipped R4.2 (no Playwright there) and found R4-B1 (a box label cut by the card image). The cloud fixed R4-B1 in `fcfe616`. The local recheck (`ce9f865`) found R4-B2 (two labels over each other on a zoomed card); the local machine fixed it in `4046090` at the user's request and measured again: pass. The two browser-checks it touched need Playwright and were not run there; the cloud runs them next time. The main folder moved to `0200c23` and the Control Center started for the user's acceptance test U-R4 (section 8.3). During it the user found R4-B3 (V2 dialogs blinking on hover and scroll on their PC); the local machine fixed it in `cbe215e` and `35127e6` (no `backdrop-filter` anywhere in V2; the main folder followed, static files only) and the user confirmed. Then the user passed step 7 (phone) and step 8 (narrow window) except R4-B4 (in a narrow window the scrollbar dragged the page behind the review dialog), fixed in `e3b894f`, and asked for R4-U1 (rows waiting for or in their export show only ⋯) and R4-U2 ("Xuất lại" in "Hoàn tất" with a warning and a guide), made in `73ccc51`. Next: the user tries these three, then the merge to main when the user asks (the user said they will then run the main folder on `main`).
  - The main folder runs a detached commit of this branch (`a7d8f18` on 2026-10-05). Once, when a new session opened, it was switched to `main` (most likely by the desktop app's branch picker) and V2 answered "Không tìm thấy"; with the user's consent it went back. Check `git -C E:\DungChung\BiliFlow status` before any claim.
  - Branch `feat/video-download` (worktree `temp/wt-video-download`, from `a7d8f18`) holds the real video download feature (plan `docs/VIDEO_DOWNLOAD_PLAN.md` there); another session builds it.
- Stronger logo cover in exports (`0334f8c`, made on the local machine, see CHANGELOG):
  - A new export hides a reviewed regional logo with FFmpeg `delogo`, then a blur that grows with the region.
  - The export identity is unchanged: an older export still counts as proven, and "Dọn video gốc" / "Lưu trữ" / "Xuất video" treat it as before.
  - For an already exported video to get the new cover, the user moves the old export (`output\…-reviewed.mp4` and its `.manifest.json`) to the Recycle Bin themselves, then exports again. Agents never move or recycle exports.
  - The running Control Center uses the new cover only after the main folder moves to a commit that contains `0334f8c` and the Control Center restarts (ask the user first).
- Next:
  1. The user tries R4-B4, R4-U1 and R4-U2 on the PC (the main folder moves to the new commit, static files only, when the user agrees and no job runs), which ends U-R4 (plan section 8.3).
  2. The cloud runs the browser-checks that need Playwright (R4-B2, R4-B3, R4-B4, R4-U1, R4-U2).
  3. Merge into `main` only when the user asks; the user then runs the main folder on `main`.

## Previous work — 2026-10-03 Dashboard V2 prototype (branch `feat/dashboard-v2`, pushed; integration planned for a cloud session)

- Latest requested revision: multi-download UI at `#downloads`. Each task has independent ID/state/progress/sample log; multiline input validates an entire batch before adding (max 20/batch, 100/tab, duplicate URL rejection). FIFO with default 2 download slots, adjustable 1–3; lowering slots lets current tasks finish. Global pause, per-task pause/cancel/retry/failure simulation and filters. Resume needs a free slot. Keep downloads separate from production scan/export queues; no shell/downloader has been run or integrated.
- Verified: 25 contract checks and 16 Browser checks, 375px light/dark and desktop; 64 production hashes unchanged. Evidence `multi-download-ui-checks.json` and `multi-download-*.png`. Command/PowerShell integration sequence and event/cancel/resume contracts are now documented in the V2 guide; they are future design, not existing API claims. Current demo open light with three paused sample tasks; click Tiếp tục tất cả to watch it. No demo or production restart.
- New requested page **Tải video**, route `#downloads`: only YouTube + Phimmoi mock (user explicitly requested a sample). Phimmoi host is reserved `phimmoi.example`, not a real service address. `download-demo.js` validates URL input, `app.js` animates in-memory progress; no external transport/downloaded files/processing jobs. Navigation preserves draft/task; reset/reload clears them. Do not wire the simulation timer to production or claim existing downloader endpoints.
- Latest download checks: 22 contract and 11 Browser checks passed, server syntax and asset HTTP 200/no-store; 64 production hashes unchanged. Evidence `download-ui-checks.json` and `download-*.png`. Scan/export counters and active-card title were checked again against a simulated VERIFYING snapshot. User's question: state changes recalculate the UI; live worker updates require future status/jobs adapter integration as documented.
- Static demo on 8794 restarted alone for new asset whitelist; PID is stored in `temp/dashboard-v2-evidence/server.pid`. Existing Control Center remains untouched. Download page is open for review; every media action remains a simulation.
- Latest feedback complete: light/dark toggle in topbar, stored as `biliflow-v2-theme` (theme only). Five overview cards now distinguish scanning / human review / ready export / active rendering-or-verification / completion; queued videos are separate. Summary filters are frontend-only and clear old search. Existing six bucket IDs are preserved; review/export labels explicitly describe their combined stages. Do not reintroduce the ambiguous mixed “Đang xử lý” card.
- Latest checks: 20 contract, 13 focused Browser checks passed; 64/64 production source hashes unchanged. Evidence `theme-summary-checks.json`, `dark-overview.png`, `dark-mobile.png`, `light-summary-v2.png`. Demo remains at port 8794 and is not connected to the real backend. Theme changes require no server restart.
- User requested a separate, more usable dashboard with hardcoded data and mapping to every current function. Production code audit fixes are deferred. New demo files: `dashboard_v2/`; guide: `docs/DASHBOARD_V2_UPDATE_GUIDE.md`.
- Demo on `127.0.0.1:8794`, with synthetic in-memory data only, no production transport. It covers overview/list/details, queue, setup/rerun/export/review illustrations, skip, hide, source cleanup/archive/restore illustrations, logo memory and AI controls. The production dashboard and full review page remain as before.
- Latest user feedback applied: light blue/white theme in `theme.css` (loaded after `styles.css`), larger text/buttons, one or two main drawer actions with secondary actions folded, readable stage labels, folded audit/technical sections. Fixed opaque grey hover backdrop; mobile hero cards stack vertically. Grouping preserves every operation ID and confirmation; keyboard focus excludes controls inside closed folds.
- Feedback revision verified: 9 focused Browser checks (`light-ui-checks.json`), contract checks 18/18 rerun, demo server syntax and theme HTTP 200/no-store; all 64 production source hashes still unchanged. Updated mapping guide for new action locations and stylesheet order. Only static demo server on port 8794 restarted.
- Verified: 18 contract checks, 22 Browser interaction checks, 6 HTTP isolation checks; desktop 1280 and mobile 375; 64 production Python source hashes unchanged. Evidence, server PID and screenshots in `temp/dashboard-v2-evidence/`.
- The independent static demo was started hidden with Python `-B`; its launcher is `dashboard_v2/Start-Demo.cmd`. No real cleanup/archive/export actions were triggered; no Control Center restart.
- Next: collect user feedback. Do not wire mock mutations to live APIs. Any future integration follows the guide and preserves token, source/export guards, FIFO, current review workflow and rollback route.
- 2026-10-03: committed on `feat/dashboard-v2` and pushed at the user's request, for a Claude cloud session.
  - Its plan and checklist: `docs/DASHBOARD_V2_CLOUD_PLAN.md`.
    - It does cloud-safe work first: mapping, the adapter with a fake transport, and an opt-in `/dashboard-v2` route.
    - It marks what it tested and leaves live-data checks to the local machine.
  - Before merging into `main`, move the untracked `dashboard_v2/` copy to `temp\` and drop the uncommitted V2 notes in the main tree.
    - They are byte-identical to the branch's first commit e044a18, which is pushed, so nothing is lost.
    - The user agreed on 2026-10-04.
- 2026-10-03: the cloud session finished phases 0–3 (up to c616bef):
  - the adapter and live store;
  - an opt-in `/dashboard-v2/` route with a file whitelist and its own CSP;
  - `/` and `/review/{id}` byte-identical to f6996bb.
- 2026-10-04: checked on the local machine in worktree `temp/wt-dashboard-v2`.
  - Results:
    - full suite: 1215 tests OK, 25 skipped;
    - `verify.cjs` 28/28 and `verify-adapter.cjs` 15/15;
    - stage-cache fingerprints unchanged for all 10 stages.
  - A read-only preview found two display gaps against `/`:
    - a missing source shown as present;
    - no "previous cleanup/archive failed" notice.
    - The preview ran the real handler on a temporary root with synthetic jobs and blocked every POST.
  - The user answered the cloud's six questions and asked for batch 2:
    - fix both gaps;
    - send `render_request` from the backend;
    - add a phone/laptop mode for the home Wi-Fi, modelled on Golden Label's `--phone`.
  - All of it is in `docs/DASHBOARD_V2_CLOUD_PLAN.md` sections 11 and 12. Not merged; Control Center not restarted.
- 2026-10-04, later: batch 2 pulled (up to 949f935).
  - Local check:
    - full suite: 1236 tests OK after fixing two test bugs that only show on Windows;
    - V2 gates pass;
    - cache fingerprints unchanged.
  - A security review found phone mode safe on the home Wi-Fi and listed S1–S6 to harden.
  - The user decided:
    - Visual AI Audit is PC-only;
    - batch 3 on the cloud fixes S1–S6;
    - then the user tests everything, and only after that is the branch merged into `main`.
  - Not merged. Plan: `docs/DASHBOARD_V2_CLOUD_PLAN.md` sections 11–13.

## Current work — 2026-10-03 export identity, proven-export reuse, HTTP request limits, short-export rate cap (branch `fix/export-identity-http`, merged into `main`; Control Center restart pending)

- Request (user, 2026-10-03): plan, fix and test three review findings on a new branch from `main` (not on `main`).
  - (1) Export identity from the render operations, with a legacy fallback.
  - (2) Never reuse an existing output without verifying its manifest.
  - (3) Reject a bad Content-Length, add a request timeout without breaking video streaming, and make the servers refuse a non-loopback `--host`.
- Where: the worktree `temp/wt-export-fix` (its gitignored `input/placeholder.mp4` only feeds `test_job_pipeline`/`test_job_ocr_option`). Plan and logs are in `temp/ui-plan/export-fix/`.
- Files:
  - New: `src/biliflow/export_identity.py`, `src/biliflow/http_guards.py`, `tests/test_export_identity.py`, `tests/test_http_guards.py`.
  - Changed: `review_workflow.py`, `control_center.py`, `source_cleanup.py`, `job_import.py`, `final_renderer.py`, `export_guards.py`, `control_entry.py`, `cli.py` and their tests (plus `tests/test_final_renderer.py`).
  - Two old tests encoded the bug and now encode the fix: `test_finalize_shortcut_retires_the_old_request` (a manifest-less file was taken for the export) and the source-cleanup edge-mode test (another edge mode kept the same name).
- Reviews (code and security, read-only agents), two rounds, each followed by a fix pass. See CHANGELOG for the list.
  - Fixed:
    - links and the source itself are never the export (MEDIUM);
    - manifest paths are compared as written, never resolved;
    - hostile manifests and queues no longer raise, and the startup import skips them;
    - deep JSON bodies → 400;
    - hex-only source hash in the name;
    - the renderer refuses a link at the export path, hashes before the rename and never replaces;
    - strict `--host`, with `localhost` → 127.0.0.1.
  - Accepted: the timeout applies per read; the stream exemption; `golden_label_app` and `allow_reuse_address` unchanged; no size cap on manifest reads.
- Compatibility: exports made before keep their legacy decision-hash name.
  - finalize, the standalone review UI, the startup import, "Dọn video gốc" and "Lưu trữ" look at the review's own name first, then the legacy name, and accept a file only when its manifest proves it.
- Verification:
  - Full suite: 1192 OK (skipped=25).
  - Real FFmpeg check in a temp root (`temp/ui-plan/export-fix/e2e.py`): 11/11.
  - Mutation checks: silencing any one manifest check fails the intended tests.
  - Read-only on the real project (`realdata_check.py`): all 21 exports that still have a file are proven under their legacy names, and the cleanup assessment and export checks on a database copy are identical between `main` and this branch (25 and 24 jobs).
- Short exports, fixed after 2a37496 at the user's request (2026-10-03):
  - The bug: with a size limit, an output shorter than about 24 s at the default limit failed in FFmpeg. So did an output under about 11 minutes at a 100 GB custom limit. The cause was `-maxrate`, or `-bufsize` (twice the rate), going above 2,147,483,647.
  - The fix: `final_renderer.MAX_VIDEO_MAXRATE` = 1,073,741,823 caps the rate. Every render FFmpeg accepted before keeps its exact command.
  - Tests: new `RenderCompletionTests` cover over-range rates, the exact old rates, the boundary and audio. Every mutation of the cap fails a test.
  - Code review (read-only agent): approve. Comparing 4,524 cases, every command FFmpeg accepted before is identical.
  - Real FFmpeg check: `temp/ui-plan/export-fix/shortclip_check.py`, 20/20.
  - Full suite: 1197 OK (skipped=25).
  - Noted, not changed: a custom limit larger than the free disk space is refused before rendering, and short exports are marked H.264 level 6.2, the same as before the fix.
- Merged into `main` at the user's request (2026-10-03, about 21:40; fast-forward, no job running, not pushed).
  - The main tree's uncommitted Dashboard V2 work from another session was kept as uncommitted changes: `dashboard_v2/`, `docs/DASHBOARD_V2_UPDATE_GUIDE.md`, and its notes in CHANGELOG, PROJECT_STATUS and SESSION_HANDOFF.
- Side effect found after the merge: the `--host` check changed `cli.py`, which every scan stage's cache fingerprint hashes. Cached scan results therefore no longer match, and the next rescan of an already-scanned video recomputes every stage (see item 10, Gore C1). To be handled later, as the user asked.
- Next: a Control Center restart, so that it runs this code. Ask the user first, and restart only with no job running. Until then, do not start a new scan or export: stages run as separate processes and would load the new code under the old Control Center.

## Current work — 2026-10-03 dashboard batch 4: platform logos → BLUR, logo memory page, archive/restore, UI fixes (committed 6a8a59c; integrated into the main tree ~17:05; the user restarted the Control Center on it at 18:01:50)

- Plans and evidence: `temp/ui-plan/batch4/` (`plan.md` = user decisions, `plan-4a.md`, `plan-4cd.md`, `progress-4a.md`, `progress-4cd.md`, `hooks-4a.md`, `e2e-4a/`, `mock_server.py` + `mock-README.md` on port 8793, `full-suite-*.log`, `pre-integration-backup/` = the 23 main-tree files as they were before the copy). Worktree `temp/wt-batch4` (detached c0a20cc; batch 3 staged in its index as baseline tree 1f523d1, batch 4 unstaged) and `temp/wt-batch3` can be removed now that both batches are committed.
- New modules: `platform_names.py`, `platform_logos.py`, `platform_cards.py`, `platform_memory.py`, `logo_memory_admin.py`, `source_archive.py`, `source_archive_files.py`, `source_archive_restore.py`; scripts `platform_logo_convert.py`, `platform_logo_e2e.py`. Changed: `textscan.py` (platform tracks survive truncation → text cache key changes once), `review_workflow.py` (platform/ending cards, forced-card protection, safe remember/forget, platform choice, archived lock), `control_center.py` (routes, dashboard, logo-memory hooks, framing headers), `job_store.py` (`hidden_at`, `recycle_checks`, `source_archives`, one `source-archive` backup), `recycle_bin.py` (verify retry, `send_export_to_recycle_bin`), `source_cleanup.py` (shared lock, archive-aware assessment, bin re-check), `scheduler.py` (cancel guard, archived locks), `export_guards.py`, `job_import.py`, AGENTS.md (two user-triggered exceptions).
- Verified: worktree 1130 OK (skipped=25); main tree 1130 OK (skipped=2); e2e scan of a COPY of the Tập 17 export: iQIYI [8.00, 13.00] and [2699.68, 2703.68] as MAIN BLUR cards, 12/12 checks × 3 scenarios, production unchanged; mocks at 1280 px and 375 px; security review 0 critical/high (1 medium + 5 low fixed).
- Real state changed in this session (Control Center stopped, user-approved plan): `state/studio-logo-memory.json` — 3 iQIYI records → `platform_logo` BLUR, 1 iQIYI end-ident record seeded from the Tập 17 export; licence-card records unchanged; backups `state/backups/studio-logo-memory-20261003-170730.json` and `…-170743.json`. Nothing else in `state/`, `input/`, `output/` or `archive/` was touched by the agent.
- Done: the user restarted the Control Center at 18:01:50 (log `logs/control-center/control-center-20261003-180150.out.log`; migration backup `state/backups/control-center-before-source-archive-20261003-180150.sqlite3`; `jobs.hidden_at`, `recycle_checks` and `source_archives` present; 30 jobs intact: 24 COMPLETED, 1 SKIPPED, 5 CANCELLED) and found the new dashboard fine. Committed at the user's request: d90c8f3 (batch 3 code = baseline tree 1f523d1 without its docs), 6a8a59c (batch 4 code), then the docs of both batches. Checked read-only the same day: the 18+/violence cards of jobs 46, 47, 52 and 55 come from scans started with all four detector groups by the pre-236fb06 dashboard (picker race; 52/55 were started at 20:21 on the process started at 17:29); all 9 of their main cards are KEEP, so the exports are unaffected; no scan has been queued since the fix went live (last JOB_QUEUED: #60 at 2026-10-02 20:22:51). Next: the first new Nhất Âu Xuân episode should show the two iQIYI BLUR cards and the “Kiểm tra đoạn kết” card. Jobs 43/47 can be re-checked with “Kiểm tra lại Thùng rác”. Decided by the user 2026-10-03: keep the all-groups default for a new job (no "remember the last scope"); the start confirmation already catches a scope that differs from the last start.

## Previous work — 2026-10-03 dashboard batch 3: Dọn video gốc (committed d90c8f3; integrated into the main tree after gates G1-G4; the user restarted the Control Center at 12:19:55 and cleaned jobs 40–60 at 12:21)

- Scope: the rest of docs/UI_QUEUE_PLAN.md. This covers steps 8-9 ("Dọn video gốc" to the Windows Recycle Bin), the batch-3 part of step 10, the two open items left by batch 2, and the batch 1-2 leftovers. Plan `temp/ui-plan/batch3/plan.md`, contract `temp/ui-plan/batch3/contract.md`, evidence and logs `temp/ui-plan/batch3/` (per-track focused/full-suite logs, `ta3-mutation/`, `tb1-mock-dashboard.html`).
- Files:
  - New: `src/biliflow/export_guards.py` (the shared queue lock `REVIEW_QUEUE_IO`, every refusal message, the shared export/edit guards and a read-only Control Center DB reader), `src/biliflow/recycle_bin.py` (pre-checks, SHFileOperationW on a COM STA thread with a 60 s timeout, `$I`/`$R` verification) and `src/biliflow/source_cleanup.py` (eligibility, preview, execute, reconcile). Tests: `tests/test_export_guards.py`, `tests/test_recycle_bin.py`, `tests/test_source_cleanup.py`, `tests/test_source_cleanup_http.py`.
  - Changed: `job_store.py` (table `source_cleanups` with one backup `control-center-before-source-cleanup-<ts>.sqlite3`, `retire_stage`, `render_request`, `reset_watched_file`). `scheduler.py` (cleaned-source locks, `retire_render_request`, `resume` refusals, InputWatcher restore and per-file FileNotFoundError). `job_import.py` (skips cleaned paths/SHA-256). `control_center.py` (guards from export_guards, cleaned-source refusals, 410 media, status keys `source_cleanup`/`source_cleaned`/`cleanup`/`source_cleanup_running`, GET `/api/source-cleanup/preview`, POST `/api/source-cleanup` with 409 codes, reconcile at every start, the Hoàn tất toolbar/checkboxes/dialog, start/rerun queue notices). `review_workflow.py` (standalone review-ui guards, view-only page for a cleaned source). Also `tests/fixtures/dashboard_harness.js` and the tests of these modules.
- Closes batch 2's open items:
  - (a) The standalone review server (`serve_review_ui`) now fails closed. It does not export any video with a Control Center job, nor when the DB is unreadable. It refuses decision edits while the video's export is in flight or still requested (paused, failed or interrupted, until Hủy retires it), after its source was cleaned, and while it is skipped.
  - (b) A paused, cancelled, superseded or already-exported export no longer keeps a live PENDING render. Cancel, a changed decision, a skip and the finalize shortcut retire the stage (EXPORT_REQUEST_RETIRED), and "Tiếp tục" refuses a settled job or an old export. `render:{id}` stays as history.
- G2 fix pass (evidence `temp/ui-plan/batch3/fix/`: focused log, mutation summary, read-only identity probe, Esc mock): cleanup eligibility of an export now compares the manifest's `operations` with `review_workflow.approved_operations(current queue)` (decision fields only); review-ui also refuses edits while a paused/failed/interrupted export still holds a render request; `serve()` waits for an API `stop()`; Esc cannot close the cleanup dialog mid-POST; skipped+cleaned card text. Deferred: the finalize shortcut still marks COMPLETED when only the blur edge mode changed (review_export_paths does not hash it); cleanup now refuses such an export with a "move the old file out of output/ first" reason. (Resolved on branch `fix/export-identity-http`, 2026-10-03: the name hashes the render operations, and only a manifest-proven export is reused.)
- Defaults applied without asking (from the plan): after cleanup the review page is view-only (no decision edits, no export) and the job stays in "Hoàn tất". A different file at the old path becomes a new job that needs "Bắt đầu".
- Safety: no default recycler anywhere. Every test module that can reach cleanup patches `recycle_bin._shell_delete` to raise. `send_to_recycle_bin` refuses every folder except `<install>\input` (plus `<install>\temp\recycle-bin-test` only with BILIFLOW_TEST_RECYCLE_BIN=1). The workers' rules forbid any POST to 8765 and any access to `input/`; the G2 reviewers re-check this.
- Gates done (evidence `temp/ui-plan/batch3/`):
  - G1: worktree full suite 963 OK; cache-key check CLEAN; migration on a copy of the live DB 20/20 (row counts unchanged, one `source-cleanup` backup, second open a no-op, schema version 1).
  - G2: 7 findings (0 high, 2 medium, 5 low), all fixed; 970 OK. Lead follow-ups after the recheck: a rerun with the same decisions keeps its export cleanable, and the cleaned review page no longer tells the user to export.
  - G3: static mocks at desktop and 375x812 (no live POST).
  - G4: the single real Recycle Bin test passed on 2026-10-03 11:57 (1 KB file it created; `$I` verified; E: bin 7 → 8 items). Marker `temp/recycle-bin-test/ran-once.json`; never run it again without the user's consent.
  - G5: integrated into the main tree on 2026-10-03; main-tree full suite 972 OK (skipped=1). The running Control Center (started 06:59) still serves the batch 2 code until restarted.
- User steps after integration:
  1. Approve a Control Center restart while the queue is idle (Stop-BiliFlow.cmd, then Start-BiliFlow.cmd; no RefreshExisting).
  2. Clean one exported episode yourself: Dashboard → "Hoàn tất" → #59 Nhất Âu Xuân Tập 29 → "Dọn video gốc" → read the dialog → "Chuyển 1 video vào Thùng rác". The agent only checks read-only before and after.
  3. Optional: restore it from the Recycle Bin and check that the card shows "Đã khôi phục video gốc (SHA-256 khớp)" after about 60 s.
- Keep in mind: items C.8/C.9 below need some Nhất Âu Xuân sources (C.9's example is Tập 14 37:05, and the studio-logo upgrade reads sources). A cleaned video stays restorable from the Recycle Bin until the user empties it.

## Current work — 2026-10-03 dashboard batch 2 (committed 1b6ad90 (+c0a20cc docs), integrated in the main tree)

- control_center (skip/unskip endpoints, jobTab stage tabs, card export panel, finalize guards, review_summary cache, source_present), scheduler (job_action_lock, SKIPPED handling, is_busy, _after_success no requeue after pause/cancel), job_store (update_job_if), review_workflow (shared export dialog, SKIPPED text), new export_dialog.py; tests test_skip_export, test_export_dialog (incl. node --check of every inline script). Evidence: temp/ui-plan/batch2/.
- Done 2026-10-03 06:59: Control Center started with batch 2 (it was already stopped); 30 jobs intact.
- Open (low), closed by batch 3 once integrated: the standalone review server (serve_review_ui) has its own finalize without the new guards; a paused/cancelled export keeps a PENDING render stage and render:{id} until the next finalize or rerun.

## Current work — 2026-10-03 dashboard batch 1 (committed 27dc000, integrated in the main tree)

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

Updated 2026-10-03 (after commits 53cd9a6..c0a20cc and the uncommitted batch 3 in temp/wt-batch3). The user's requests are quoted in Vietnamese.

### A. Pending user steps
1. Done: the Control Center was restarted with batch 1 (2026-10-03 00:18) and with batch 2 (06:59), so it serves 236fb06 and both batches. Next restart: batch 3, after integration and only with the user's approval (see "Current work — dashboard batch 3").
2. Done: Nhất Âu Xuân Tập 14 (job 44) was exported. A read-only GET of /api/status on 2026-10-03 shows it COMPLETED.
3. Optional: re-run the local structure audit for jobs 40-50. Their stored structure-audit.json still says BLOCK, which the new dashboard labels "quy tắc cũ".
4. After the batch 3 restart: clean one exported episode yourself (suggested job 59, Tập 29), and optionally restore it from the Recycle Bin.

### B. Dashboard / workflow requests from the user (2026-10-02)
Status: items 4-7 and 7c-7d are done, in batches 1-2 (commits 27dc000, 1b6ad90, docs c0a20cc). Item 7b is implemented in batch 3 and integrated into the main tree after gates G1-G4 (committed d90c8f3; the user restarted the Control Center at 12:19:55 and cleaned jobs 40–60 at 12:21). Only 7b (c), an export near the source bitrate, is still open. Details: docs/UI_QUEUE_PLAN.md, "Trạng thái thi công". The original requests are kept below for reference.

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
     - It never touches reports, state, decisions, brand/studio memory or outputs. (As built: it only adds its own `source_cleanups` rows and SOURCE_* events to state and resets the watcher row of the moved file; see AGENTS.md.)
     - It records an event with path, size and SHA-256.
     - User decision (2026-10-02): move to the Windows **Recycle Bin**, not a permanent delete ("chuyển vào thùng rác thôi"). Space is freed when the user empties the bin; tell them so in the dialog. Use the Windows shell API (SHFileOperation/IFileOperation with FOF_ALLOWUNDO) or Microsoft.VisualBasic FileSystem.DeleteFile(..., SendToRecycleBin) through PowerShell. Add no new dependency without a license check.
     - Afterwards the job shows "Đã dọn video gốc". "Chạy lại kiểm tra" is disabled with the message "Chép lại video gốc vào input để chạy lại". A re-added file is accepted only if its SHA-256 matches.
     - Discovery must not recreate a job for a cleaned file.
     - Agents never run the cleanup themselves.
   - Implemented in batch 3 (see "Current work — 2026-10-03 dashboard batch 3"), with stricter eligibility than these notes. The export must belong to the active review revision and have been rendered from the current decisions (the manifest's operations), and a skip record must still match the review. The bin capacity is blocked rather than warned. AGENTS.md carries the exception in the same change.
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
10. **Gore C1** (temp/wt-gore, off by default). Checked on 2026-10-03, after the `fix/export-identity-http` merge; the user said to handle it later.
    - What it is: steps 1-3 of `docs/ANIME_GORE_PLAN.md`.
      - The animation safety scanner records tag evidence for each gore interval.
      - Every gore card shows a hint: blood, injury only, or corpse.
      - Rule C1 (no blood tag and no corpse tag) may move an undecided ANIMATION gore card to "Ứng viên phụ". It never deletes a card, and `GORE_TRIAGE_LEVEL = "off"` by default.
      - Plan figures: Conan 20 goes from 40 to 32 cards, Conan 21 from 35 to 28, and no real blood is moved.
    - Where it is: uncommitted in the worktree `temp/wt-gore` (base 68a5a7e, written 2026-10-02 12:03-12:31), and saved byte-identical as `temp/gore-c1.patch`. It touches 7 files, +1078/-8 lines, of which 554 are tests. None of it is in `main`.
    - Against `main` 738b944 (`git apply --check`):
      - It still applies to animation_policy, animation_safety_scanner, intervals, cli.py, the new gore_triage.py and its tests.
      - It no longer applies to review_workflow.py. That file gained +1160/-146 lines over 7 commits since 68a5a7e, the export-identity merge among them, so the patch's 243-line part there needs a manual re-merge.
    - Scan caches: integrating it was held back because it edits cli.py, and every stage's cache fingerprint hashes cli.py (`cache_dependencies.stage_source_paths`).
      - The 2026-10-03 merge already edited cli.py (the `--host` check). Every cached scan stage therefore no longer matches: up to 99 entries for 22 videos, about 487 MB. The next rescan of those videos recomputes all their stages.
      - Results, exports and new videos are not affected.
      - Integrating Gore C1 before many new scans therefore costs little extra.
      - Moving the `--host` check out of cli.py would restore the old fingerprints wherever nothing else changed; the servers keep their own loopback check. Measure this before doing it.
    - Enabling it still needs the user's Conan gore decisions (the second test set) and a third anime film (plan §4.6-4.7 and §7).

### D. Later
11. Carry reviewed decisions across a rerun (stash@{0}, paused 2026-10-01).
12. 18+ "balanced" triage level (needs a second live-action film).
13. Speed: shared decode / T2-T3, only after measurement.
14. Merge into `main` or push only with the user's explicit authorization. Commits 53cd9a6..c0a20cc are local on `improve/scan-performance-metrics`.

## Safety and product constraints

- Source videos are immutable. Exceptions, all user-triggered on the PC after the user confirms them: “Xóa video gốc” deletes an exported or skipped input video for good (from `feat/delete-flow`; before it, “Dọn video gốc” moved it to the Windows Recycle Bin), “Xóa video” deletes a cancelled job's input video, and “Lưu trữ” / “Khôi phục bản xuất” rename it into `archive\` and back. Agents never run them (see AGENTS.md).
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
Làm việc trong E:\DungChung\BiliFlow. Trước khi sửa, hãy đọc AGENTS.md, docs/SESSION_HANDOFF.md và docs/SCAN_PERFORMANCE.md, sau đó kiểm tra git status và 8 commit gần nhất. Tiếp tục trên nhánh improve/scan-performance-metrics; chưa merge/push nếu tôi chưa yêu cầu. Hãy đối chiếu mọi kết luận với report/job thực tế, giữ nguyên nguyên tắc video nguồn bất biến (trừ nút “Dọn video gốc” do tôi bấm, chỉ chuyển vào Thùng rác), model local miễn phí/commercial-safe và mọi edit phải qua người duyệt. Sau khi nắm trạng thái, tóm tắt ngắn: việc đã hoàn tất, bằng chứng kiểm chứng mới nhất, việc còn lại theo ưu tiên và bước tiếp theo bạn sẽ làm.
```
