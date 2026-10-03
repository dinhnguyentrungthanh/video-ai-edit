# Unreleased — export identity from the render, reuse only a proven export, HTTP request limits — 2026-10-03

Status: branch `fix/export-identity-http` from `main` 23aa1e4, built in the worktree `temp/wt-export-fix` (plan `temp/ui-plan/export-fix/plan.md`). Committed on the branch at the user's request on 2026-10-03; not integrated: the Control Center still runs `main`.

- Export identity (review finding: an export was reused after a blur change). `review_export_paths` hashed only the id, decision, start, end and region of every item. A new blur edge mode (`decision_blur_edge_mode`) or detected intervals (`temporal_policy = discrete_detected_intervals`) kept the same output name, so finalize reused the old export. The name now hashes what the render applies: the render fields of the edit-plan operations (`export_identity.render_identity(approved_operations(queue))`) plus a non-default size policy. A note, the reasons or a KEEP item's region still keep the name. An unfinished review keeps the old decision hash. Exports made before keep their old name and are still found: `output_candidates` gives the review's own name, then the legacy one.
- Reuse only a proven export (review finding: a file at the export path became "Hoàn tất"; reproduced with an 11-byte file).
  - Finalize, the standalone review UI and the startup import reuse an existing file only when its manifest proves it (`export_identity.manifest_problem`). The manifest must say COMPLETED, name the same unmodified source and this file, record a full decode validation and the same size, and list the same operations. These are the checks "Dọn video gốc" already used; they are now shared.
  - A file at the path a render would write that no manifest proves is refused with “Thư mục output đã có file … BiliFlow không ghi đè …”. The refusal comes before the queue is written. That file is never overwritten or deleted: the renderer refuses an existing output when it starts and, since this branch, again when the render takes the export name.
  - "Dọn video gốc" and "Lưu trữ" check the same candidates with unchanged reasons and order. Only the advice of the "không khớp quyết định duyệt hiện tại" reason changes, to "mở “Duyệt cảnh” và xuất lại": a new export no longer collides with the old file.
  - The startup import (first start or `-RefreshExisting` only): a COMPLETED manifest completes a job only when it sits beside its file and proves it. For a reviewed job, the file must also be one of the review's candidates with the same operations.
- HTTP request limits (review finding: negative Content-Length, no body timeout, token on the LAN).
  - `http_guards.content_length`: a Content-Length that is not a plain non-negative number answers 400. A negative one used to make the handler wait until the client closed the connection.
  - Both servers close a request that stops arriving after 20 s (408 for a body). Video streaming lifts the timeout while it streams, so a paused player is not cut.
  - `control_entry`, `biliflow control-center|review-ui`, `serve_control_center`, `ControlCenter.serve` and `serve_review_ui` refuse any `--host` but an IPv4 loopback address or `localhost`, written exactly (usage error, exit code 2, or ValueError), before anything starts. `localhost` binds 127.0.0.1 with no name lookup. `::1` is refused: the servers listen on IPv4 only and could not bind it. `Start-BiliFlow.ps1` already passes 127.0.0.1.
  - Not changed: `golden_label_app`, which is meant to be opened from a phone on the LAN.
- After the code review (approve, 4 LOW) and the security review (1 MEDIUM, 5 LOW, no CRITICAL or HIGH), then a re-review of those fixes (code: approve; security: fixes hold, 3 LOW and 2 INFO left, fixed below):
  - Links and the source are never the export (MEDIUM).
    - `manifest_problem` requires a plain file, so a symbolic link, junction or other reparse point is refused. Before, a symlink to the source with a manifest written for it passed, and "Dọn video gốc" would have sent the only real copy to the Recycle Bin.
    - The export must also not be the source file itself, which catches a hard link of the source or the source reached through a junction.
    - A hard link of the export elsewhere (for an upload, say) is still accepted.
  - Manifests are compared as written, never resolved. `output.path` is matched with `abspath` and `normcase`, and the cleanup's early edit-plan check runs the same lexical check before `resolve()`. Resolving a UNC or device path from a manifest would make Windows contact that machine, and a link loop raised RuntimeError.
  - Hostile files no longer raise.
    - In finalize, the review UI and cleanup, a NUL in `output.path`, JSON nested too deep or a link loop counts as "not proven". It used to raise: the cleanup preview answered 500, and the standalone page dropped its connection.
    - The startup import skips a malformed manifest or review queue: a source without a usable `path`, a text duration, a non-text status, or a non-list `reports`. It also no longer resolves the export path, so an output/ reached through a junction imports.
    - On `main`, such a file in output/ or reports/ stopped the Control Center from starting.
  - A request body nested too deep answers 400. On this branch it had become an empty reply.
  - Only a hex source hash reaches the export name. A crafted `source.sha256` such as `..\..\x` in a review queue moved the export path out of output/.
  - The renderer:
    - It refuses a file or link at the export path as given, before resolving it and before FFmpeg runs. A broken link used to pass `exists()`, and the render then followed it.
    - It hashes the render and the source before the file takes the export name.
    - `os.rename` never replaces a file that appeared there during the render.
    - A source that changed during the render used to leave an export with no manifest; now it leaves nothing.
    - A stop or crash leaves only the partial, which the scheduler removes. The only exception is the instant between the rename and the manifest write; an export left without its manifest there is refused, never overwritten.
  - Finalize and the standalone UI also refuse a broken link at the export path ("Thư mục output đã có file …").
  - The standalone review UI reports "IDLE" (not exported) for a COMPLETED job file whose export left output/, as its export button would render it again.
  - Tests:
    - The finalize test now changes one manifest field at a time, and the standalone 408 path has a test.
    - The link tests also use the link's own size, so only the link check can refuse it.
    - Silencing the decode, operations, link or "source itself" check makes the intended tests fail.
  - Accepted, not changed:
    - The 20 s limit applies to each read, not to the whole request. A local program that trickles bytes can still hold a thread.
    - A paused video stream keeps its thread and file handle until the tab is closed, as before.
    - `allow_reuse_address` of both servers is unchanged.
    - Manifest reads have no size cap.
- Verification (worktree, `PYTHONPATH=src`; evidence in `temp/ui-plan/export-fix/`):
  - Full suite: 1192 tests OK (skipped=25), `full-suite-4.log`. Every new test failed before its fix.
  - Real FFmpeg in a throw-away root (`e2e.py`, a synthetic 40 s clip, the default size policy): 11/11 checks, re-run after each renderer change.
    - A real manifest proves the export.
    - Another edge mode gets another name, and the old export is neither reused nor touched.
    - A fake file is neither taken nor overwritten.
  - Real project, read-only (`realdata_check.py` on a backup-API copy of the database under temp/):
    - `assess_job` for the 25 COMPLETED/SKIPPED jobs and the export checks for the 24 COMPLETED jobs are identical with `main` and this branch: 21 proven, #37/#38 moved, #4 stale.
    - `existing_review_export` proves the same 21 exports, under their legacy names.
  - Side finding, pre-existing on `main` and not changed here: with the default size limit, an export whose output lasts less than about 24 s fails in libx264 (maxrate/bufsize out of range). It is flagged as a separate task.

# Unreleased — dashboard batch 4: platform logos (iQIYI) → BLUR + logo memory page, archive/restore, UI fixes — 2026-10-03

Status: implemented in the worktree `temp/wt-batch4` (plans `temp/ui-plan/batch4/plan-4a.md` and `plan-4cd.md`), reviewed (code + security) and integrated into the main tree on 2026-10-03 at about 17:05, and committed as 6a8a59c on top of batch 3 (d90c8f3) at the user's request. The user restarted the Control Center on this code at 18:01:50 (migration backup `state/backups/control-center-before-source-archive-20261003-180150.sqlite3`; all 30 jobs intact) and found the new dashboard fine.

- 4a — platform-logo detection (user decisions 2026-10-03: the iQIYI ident → BLUR the logo region, never CUT; the licence card 国家广播电视总局 stays KEEP; Tập 10–30 are not re-exported now):
  - Why iQIYI was missed on Nhất Âu Xuân Tập 10–30 (jobs 40–60): Qwen looked only at the frame at 10.000 s, when the ident had barely started, and answered NO; OCR read "iOlYI"/"iOIYI" but no platform rule existed; on Tập 10/11/15 the CUT suggestion was withheld as a studio ident and the card was remembered as a studio logo; the forced 0–5 s card of jobs 51/60 was quarantined to advisory by a garbage VLM label; there was no forced ending card; 10/21 text reports had lost the tracks of their last 34–116 s to report truncation.
  - New `platform_names.py`: platform names with OCR confusables (iQIYI/爱奇艺, Youku/优酷, Tencent Video/腾讯视频/WeTV, Mango TV/芒果TV, Sohu/搜狐视频, PPTV), Bilibili excluded, whole-token rules. A read-only pass over 58 real text reports (12 888 tracks) gives 29 hits, all iQIYI idents, no false positive. `textscan.py` keeps platform-name tracks when it truncates a report; this changes the text stage cache key once, so OCR reruns on the next scan of a video (≈100 s per episode). No other stage key changes.
  - New `platform_logos.py` and `platform_cards.py` (build-review only): the head and tail of the source are decoded on the CPU; a platform hit becomes a MAIN card “Logo nền tảng <name>” with suggested BLUR, a region measured from the logo pixels (fallback: the padded OCR box) and an interval snapped to the cut to black and the end of the logo. Platform names never count as ad evidence for a CUT.
  - Forced ending card `ending_boundary` for the last 6.0 s (the measured iQIYI end ident lasts 4.04–4.24 s). Forced opening/ending cards and platform cards are never quarantined, revalidated away or dropped by the title/in-film guards (fixes jobs 51/60). A forced card whose every preview matches a remembered studio logo still moves to advisory with KEEP (the licence card; `FORCED_BOUNDARY_STUDIO_MOVE`).
  - Platform-logo memory: `state/studio-logo-memory.json` (schema 2) gains `memory_class: "platform_logo"` records (decision BLUR, `blur_region`, `logo_frame_times`); older code ignores them. A frame match in the first or last 30 s gives a BLUR card. The review page has “Đây là logo nền tảng — làm mờ & nhớ” (flag `remember_platform_logo`), names and AI lines for the new cards, and the archived-source lock.
  - “Bộ nhớ logo” page (`logo_memory_admin.py`; GET `/logo-memory`, `/api/logo-memory`, `/api/logo-memory/frame`; POST `/api/logo-memory/class|delete` with the session token and the memory SHA-256, 409 when it changed): frames per record, change class, delete. Every write backs the memory up first and moves frames into `state/backups` instead of deleting them; the review path no longer deletes remembered frames either (security review M1).
  - `scripts/platform_logo_convert.py` (`convert` / `seed`, dry run by default) and `scripts/platform_logo_e2e.py` (scan of a COPY in a temp root; cleanup removes its junctions first and never leaves `temp/`).
- 4c — dashboard:
  - Switching tab scrolls to the first video of the tab, up or down, also from an empty tab.
  - “Hủy” asks first (and explains “Dừng” and “Bỏ qua (không xuất)”), sends one request, and the server refuses a second cancel (409 `already_cancelled`) or a cancel of a finished job (409 `not_cancellable`) without writing an event (job 2 had 5 JOB_CANCELLED events, job 5 had 3). CANCELLED still revives through “Chạy lại kiểm tra” or a review decision, as before.
  - Cancelled videos sit in the closed fold “Đã hủy (N)” at the end of “Đang chờ xử lý”. “Ẩn khỏi danh sách” only sets `jobs.hidden_at` (cleared when the job leaves CANCELLED); the fold “Đã ẩn (N)” has “Hiện lại”. Nothing is deleted.
  - Recycle Bin verification waits up to ≈3.15 s for the `$I` record (the unverified jobs 43/47 were a 10 ms race). “Kiểm tra lại Thùng rác” only reads the bin and appends a `recycle_checks` row plus SOURCE_RECYCLE_VERIFIED or SOURCE_RECYCLE_STILL_UNVERIFIED; the `source_cleanups` row is never edited.
  - Every Control Center response carries `X-Frame-Options: SAMEORIGIN` and `Content-Security-Policy: frame-ancestors 'self'`.
- 4d — archive and restore (user decision: keep the source + review decisions, drop the export; restoring means exporting again):
  - “Lưu trữ” (one or several COMPLETED/SKIPPED videos that pass the same checks as “Dọn video gốc”): the source is renamed into `archive/sources/<job_key>/` on the same volume and its SHA-256 verified; for an exported video the export MP4 and its manifest then go to the Recycle Bin (capacity pre-check; the source is renamed back on any failure). `archive-manifest.json` (numbered, never overwritten) records the source SHA/size/path, queue and revision, a decisions snapshot, the edit plan and the export manifest; it gets its final name only when the archive settles ARCHIVED.
  - “Khôi phục bản xuất”: a SHA-checked rename back to the original input path (refused when that path is taken); an exported job returns to READY_TO_EXPORT/WAITING_REVIEW, a skipped one stays SKIPPED; the watcher links the file to the same job.
  - New table `source_archives` with phases and SOURCE_ARCHIVE* events, a startup reconcile (unreadable or missing files stay locked with an ERROR event), one shared lock for cleanup, archive, restore and bin re-check, and archived jobs locked like cleaned ones. `send_export_to_recycle_bin` accepts only `<name>-reviewed.mp4[.manifest.json]` directly in `output\` (no alternate data stream, no subfolder).
  - Migration: the first open adds `jobs.hidden_at`, `recycle_checks` and `source_archives` with one backup `state/backups/control-center-before-source-archive-<ts>.sqlite3`; the schema version is unchanged.
  - AGENTS.md: the source-video rule lists both user-triggered exceptions; agents never trigger them.
- Verification (evidence `temp/ui-plan/batch4/`):
  - Worktree full suite 1130 tests OK (skipped=25); main-tree full suite after integration **1130 tests OK** (skipped=2).
  - End-to-end scan of a COPY of the Tập 17 export in a temp root (GPU, 252.7 s): iQIYI [8.00, 13.00] BLUR, region 469,179 318×168 (7.8 %); iQIYI [2699.68, 2703.68] BLUR, region 373,203 542×108 (8.6 %); forced ending card; the licence card moved to advisory KEEP by its studio record; without memory both iQIYI cards still come from OCR; 12/12 checks in each of 3 scenarios; production files unchanged (SHA-256 before/after).
  - Mocks at 1280 px and 375 px: after a tab switch the list starts right under the sticky bars (measured), no horizontal overflow; archive → restore on a throw-away root; the real “Bộ nhớ logo” listing served read-only.
  - Reviews: python-reviewer on 4a (1 high, 6 medium fixed), code-reviewer on 4c/4d (5 fixed), security-reviewer (0 critical/high; 1 medium and 5 low fixed with failing-first tests, 1 informational left).
  - Real memory (user-approved plan, Control Center stopped): the 3 iQIYI studio records were converted to `platform_logo` BLUR (backup `state/backups/studio-logo-memory-20261003-170730.json`) and an iQIYI end-ident record was seeded from the Tập 17 export, window 2699.68–2703.68 s, 79 logo frames (backup `…-20261003-170743.json`). The 3 licence-card records stay `studio_logo` KEEP. Memory: 7 records.
  - After integration: the user restarted the Control Center at 18:01:50 and asked for the commits (see Status).

# Unreleased — dashboard batch 3: “Dọn video gốc” to the Windows Recycle Bin — 2026-10-03

Status: implemented in the worktree `temp/wt-batch3`, through gates G1-G4, and integrated into the main tree on 2026-10-03; committed as d90c8f3. The user restarted the Control Center on it at 12:19:55 (migration backup `state/backups/control-center-before-source-cleanup-20261003-121955.sqlite3`) and cleaned the sources of jobs 40–60 (Nhất Âu Xuân Tập 10–30) at 12:21; all 21 files are in the Recycle Bin. Jobs 43 and 47 were reported SOURCE_RECYCLE_UNVERIFIED because the check ran 10 ms before Windows wrote `$I` (fixed in batch 4; their rows can be re-checked with “Kiểm tra lại Thùng rác”).

- What the user sees (tab "Hoàn tất"; user decision 2026-10-02 replacing compression):
  - Each eligible card has "Chọn để dọn" and "Dọn video gốc". A toolbar shows "Dọn video gốc: N video dọn được · đã chọn N (…)" with "Chọn tất cả video dọn được" (at most 50), "Bỏ chọn" and "Dọn video gốc đã chọn (N)". The selection is kept in memory only and survives the 3 s refresh.
  - The confirmation dialog lists each source, its size, the export and its time (or "Đã bỏ qua (không xuất)"), the total, and the drive's Recycle Bin usage before and after. Videos that cannot be cleaned are listed under "Không thể dọn:" with the reason. "Hủy" sends nothing. "Chuyển N video vào Thùng rác" sends one request, together with the preview id. While it runs, Esc and "Hủy" cannot close it (a close forced by the browser reopens it).
  - Cleaned cards show "Đã dọn video gốc · <size> · lúc <time> (đang ở Thùng rác)". "Chạy lại kiểm tra" is disabled, with the note "Chép lại video gốc vào input để chạy lại (đúng tên: …)". On a skipped video, "Mở lại để xuất" is disabled. Other cards in "Hoàn tất" give the reason ("Chưa dọn được: …", "Không còn video gốc trong input").
  - The review page of a cleaned video is view-only: decision and export buttons are locked, the status says the source is in the Recycle Bin and which file name to copy back, and the frame strip uses the preview images stored in the report. Frame and video requests answer 410.
- Eligibility, checked on the server without hashing for the card hint and the preview, then re-checked with SHA-256 right before each move:
  - An exported video (state COMPLETED): the output must be the export of the active review revision, and its manifest must be COMPLETED, name the same source SHA-256, mark the source unmodified and pass full-decode validation. The output size must match the manifest. The operations recorded in the manifest must be the ones the current decisions render (cut/blur intervals, regions, blur edge mode); a manifest without operations must be newer than every review decision. The edit plan, when it still exists, must point at the active queue. At cleanup time the output SHA-256 must also match the manifest.
  - A skipped video (state SKIPPED): the skip record must still match the active queue, revision and decisions.
  - The source must be a regular file under `input/` with the scanned size: no link, path of at most 259 characters, no wildcard. The job must not be running, queued, or still holding an unfinished export request.
  - Jobs 37 and 38 are not offered: their recorded export belongs to the current review, but the file is no longer in `output/`, so the card says "Chưa dọn được: Không thấy bản xuất trong thư mục output (đã bị dời hoặc đổi tên?)". They become cleanable once that file is back in `output/`, or after a new export. Jobs 1-6, including job 4, have no source in `input/` and are never offered.
- Recycle Bin safety (`recycle_bin.py`, stdlib ctypes/winreg, no new dependency):
  - Before every file, BiliFlow checks that the drive is fixed, the drive's Recycle Bin is configured with "delete immediately" off and no Windows policy disables it, and the bin can take the file. It refuses when used + selected > limit − 64 MiB (user decision 3; E: limit 49 741 MiB = 52 157 218 816 B).
  - The move uses SHFileOperationW with FOF_ALLOWUNDO on its own COM thread. FOF_WANTNUKEWARNING is also set, so if Windows ever wanted to delete permanently it would have to ask first instead of doing it silently. A call that does not answer within 60 s is reported, its row stays `PENDING`, and further cleanups are blocked until it ends.
  - The move is verified through the bin's `$I`/`$R` record. If the file left `input/` but no record is found, a warning event is written.
  - It refuses every folder other than `input/` of the installation. The only exception is the test folder, and only with the opt-in test variable. There is no default recycler: only a started Control Center binds the real one.
- Records and crash safety: a new table `source_cleanups` (one `PENDING` row is written before each shell call, then `RECYCLED`, `FAILED` or `RESTORED`). Events: SOURCE_RECYCLED, SOURCE_RECYCLE_UNVERIFIED, SOURCE_CLEANUP_FAILED, SOURCE_CLEANUP_PENDING. Each video is independent, so one failure does not stop the others. A `PENDING` row locks the job. At every start the Control Center settles `PENDING` rows: a file still present becomes FAILED, a file that left becomes RECYCLED. The first open of an existing database adds the table and backs it up once, to `state/backups/control-center-before-source-cleanup-<ts>.sqlite3`. The schema version is unchanged and the table is not in the base schema script, so older code still opens the database (checked on a copy of the live database, see Verification).
- A cleaned job is locked everywhere: start, rerun, resume, retry, stop, pause, cancel, export, skip, unskip and every review edit are refused with a Vietnamese reason. Nothing is written by a refused request.
- Restore: the input watcher accepts the source again only when the same file (SHA-256) is back at the same path with the same size. This happens once the file is stable (60 s), for example after "Khôi phục" from the Recycle Bin. The card then shows "Đã khôi phục video gốc (SHA-256 khớp) lúc …" and the job can be rerun or cleaned again. The same file under another name only adds an event naming the right file name. A different file at the old path is never used for the old job and becomes a new video that needs "Bắt đầu". Restart import skips cleaned paths and cleaned videos. A file that vanishes during a watcher scan no longer aborts the scan with WATCHER_ERROR.
- Fix (a), the standalone review page (`review-ui`): it no longer exports a video that has a Control Center job; it tells the user to export from the Dashboard instead. It fails closed when `state/control-center.sqlite3` cannot be read. It also refuses decision edits while that video's export is queued or running, while a paused, failed or interrupted export of it still holds a render request (Tiếp tục/Thử lại would render the old plan; Hủy retires it), after its source was cleaned, and while it is skipped. Its finalize also refuses a missing source before writing anything. Intended behaviour change.
- Fix (b), stale export requests: cancel, a changed review decision, a skip, and a finalize that finds the export already present now retire the leftover render stage (CANCELLED, event EXPORT_REQUEST_RETIRED; `render:{id}` is kept as history). "Tiếp tục" on a video that is "Đang chờ duyệt", "Sẵn sàng xuất" or "Hoàn tất", or on a cancelled export, is refused instead of silently rendering the old plan. Intended behaviour change for an old Dashboard tab.
- Batch 1-2 leftovers: after "Bắt đầu" and "Chạy lại kiểm tra" the notice gives the queue place ("Đã xếp #… vào hàng đợi quét cảnh (lượt p/n)."). Export cards show "Xuất video tạm dừng" and "Đã hủy xuất video". The node harness now models a focused input and checks that the list is not rebuilt while the user types.
- Cross-review fixes (G2): an export re-confirmed through the finalize shortcut after a re-recorded decision stays cleanable (the check compares the manifest's operations instead of decision timestamps), and the refusal for an export rendered from other decisions now says to move the old file out of `output/` first. A missing export of the current review has its own reason. `/api/shutdown` keeps the process alive until a running cleanup has settled and the store is closed. A skipped card whose source was cleaned no longer says the source is kept. Lead follow-ups: a rerun whose new revision has the same items and decisions keeps its export cleanable (with recorded operations the edit-plan check is skipped; it still applies to manifests without operations), and the review page of a cleaned video no longer ends with "Bấm “Xuất video” ở trên để xuất".
- Shared guards and messages move to the new neutral module `export_guards.py`, used by both servers. Lock order: review queue IO, then the scheduler's job lock. No lock is held while hashing or calling the shell.
- Verification (evidence `temp/ui-plan/batch3/`):
  - G1: worktree full suite 963 tests OK; cache-key check CLEAN for all 10 cacheable stages (no touched or new module in any key, no all_source fallback; `cache_check.log`). Migration on a copy of the live database (`migration/migration-evidence.json`): 20/20 checks, every table's row count unchanged (jobs 30, job_revisions 53, stages 178, artifacts 333, events 834, settings 102), `source_cleanups` and its index created, exactly one backup labelled `source-cleanup`, the second open a no-op, schema version 1. The live database was opened read-only only.
  - G2: two read-only reviewers found 7 issues (0 high, 2 medium, 5 low); all fixed with a failing-first test each (mutation check 6/6), full suite 970 OK; the recheck verified the fixes and left one low residual, fixed by the lead (above).
  - G3: static mocks at desktop width and 375x812 (no request reached the live Control Center): toolbar, card buttons, the dialog for 1 and 18 videos, "Hủy" sent no POST, confirming sent exactly one POST with the preview id, cleaned-card line with the disabled rerun and copy-back note, sticky tabs on mobile, and a view-only review page (no decision POST).
  - G4: the single real test moved a 1 KB file it created under `temp/wt-batch3/temp/recycle-bin-test` to the Recycle Bin and verified its `$I` record (2026-10-03 11:57; E: bin 7 → 8 items, +1 024 B). Its marker `ran-once.json` is also copied to `temp/recycle-bin-test/` so the test stays skipped in the main tree.
  - G5: integrated into the main tree (19 files changed, 7 new). Main-tree full suite: 972 tests OK (skipped=1, the real-bin test). `input/` still holds its 24 videos.
  - After integration: the user restarted the Control Center at 12:19:55 and cleaned jobs 40–60 at 12:21 (see Status).

# Unreleased — dashboard batch 2: "Bỏ qua (không xuất)", stage tabs, card export button — 2026-10-03

- "Bỏ qua (không xuất)" (user request; scope decided by the user):
  - Shown for a reviewed video with 0 main cards (advisory cards do not count) or with every main decision Giữ nguyên.
  - Moves the video to "Hoàn tất" as "Đã bỏ qua — không xuất" (state SKIPPED, record skip:{id}). Video, reports, queue and decisions are untouched.
  - "Mở lại để xuất" returns it to review; export is refused until it is reopened; "Chạy lại kiểm tra" clears the skip.
  - Changing a decision to Làm mờ or Cắt brings the video back to "Sẵn sàng xuất".
- Stage tabs, with counts and sub-groups; each state lands in exactly one tab and polling never jumps tabs:
  - "Đang chờ xử lý"
  - "Đang chờ chạy cảnh để duyệt"
  - "Đang chạy cảnh"
  - "Đang chờ duyệt": "Cần duyệt cảnh" / "Đã duyệt xong — chờ xuất hoặc bỏ qua"
  - "Đang chạy xuất video"
  - "Hoàn tất": "Đã xuất video" / "Đã bỏ qua"
- "Xuất video" on the card:
  - The size choice, confirmation and gate come from one shared module (export_dialog.py), so the card and the review page behave identically.
  - The panel closes on OK and shows a notice; a double click sends one request.
  - The button is disabled with the reason when review is unfinished or the source is missing (jobs 1, 2, 5).
  - The server refuses a second export while one is queued or rendering, a skipped video, and a missing source, before writing anything.
- Fixes:
  - A review decision no longer pulls a queued or rendering export back to "Sẵn sàng xuất"; decisions are refused while an export is queued.
  - A "Dừng ngay" or cancel during a very fast stage is no longer requeued.
  - One scheduler lock covers start, rerun, resume, retry, export, skip and unskip.
- Verification: static-mock checks at desktop and 375x812 with fake jobs (0-card, all-KEEP, BLUR, skipped, queued export, rendering, missing source); state, guard and race scenarios on temp roots; node syntax check of every inline script on both pages (a duplicate declaration that would have blanked the review page was caught and fixed); no file in a scan stage cache key. Two reviews (1 high, 3 medium fixed) and a re-check. 795/795 tests.

# Unreleased — dashboard batch 1: sticky tabs, click-order queue, restart safety, export panel — 2026-10-03

- Sticky job tabs: the tab bar stays under the header while the list scrolls (desktop and phone width).
- The queue runs in click order. "Bắt đầu", "Chạy lại kiểm tra" and "Xuất video" take the next place; "Tiếp tục" and "Thử lại bước lỗi" keep the old place (user decision). Scans and exports share one queue and one worker. Cards show "Chờ chạy cảnh" / "Chờ xuất video" and "Thứ tự chờ: #N". The jobs table gains queue_seq/queued_at through an idempotent migration that backs up state/control-center.sqlite3 once (state/backups/control-center-before-queue-order-<ts>.sqlite3); schema version unchanged; older code still opens the database.
- Restart safety (pre-existing bugs):
  - Restarting the Control Center no longer silently turns queued reruns and exports (and PAUSED/FAILED/CANCELLED/COMPLETED jobs) back into "Sẵn sàng xuất"/"Chờ duyệt".
  - A job interrupted during BUILDING_REVIEW, or right after its last stage, is recovered.
  - A pause or cancel that lands between picking and starting a job now wins.
  - "Dừng sau bước" on a waiting job takes it out of the queue.
  - Rerun is refused while the job is queued or running.
  - "Tiếp tục" cannot finish a job the worker is still finishing.
- Review page: after "Hoàn tất duyệt và xuất video" and OK, the export panel closes and a header notice shows progress and result. Cancel keeps the panel; a double click sends one request; a failed request reopens the panel with the error.
- Verification: a migration on a copy of the live database (row counts unchanged, one backup, second open a no-op, old code compatible); FIFO, restart and race scenarios on temp roots; static-mock UI checks at desktop and 375x812; no touched file in a scan stage cache key. Two reviews (1 medium finding fixed) and a re-check. 757/757 tests.

# Unreleased — dashboard keeps the chosen detector scope, honest structure audit, no brand-memory cache churn — 2026-10-02

- Detector picker (jobs 46/47 were started with all four groups although the user chose advertising only): the 3 s refresh rebuilt the job list under the user's clicks, a stale status poll could re-show a just-started job as "Chờ thiết lập" with every group checked, the next setup card slid into the clicked slot, drafts lived only in memory and the default is ALL. Fixed in the dashboard: ordered status polls, no rebuild while the user interacts, per-job drafts in localStorage (cleared only by that job's own start/rerun), setup cards in id order, a confirmation "Bắt đầu #… với các nhóm: …?" when the scope differs from the previous start (or includes 18+/máu me/bạo lực with no history), double-start protection; the server refuses Start for a job that is no longer waiting for setup. Default scope stays ALL (policy unchanged).
- Structure audit: a detector-only coverage shortfall (e.g. the visual-logo budget of 175-193 windows against 459-531 candidates on Nhất Âu Xuân) is now a WARN with exact numbers instead of a blocking "lỗi toàn vẹn"; missing or incomplete references still BLOCK; Visual AI Audit is no longer forced to BLOCK by it. Stored audits keep their old result until re-audited (the dashboard marks them "quy tắc cũ"). The underlying logo budget gap is a separate, measured follow-up.
- Brand memory: a decision that changes no record (text, scene-level KEEP/CUT, a repeated identical logo decision) no longer rewrites state/brand-memory.json, so the visual-logo stage cache key stays valid across reviews; real changes still write.
- Verification: static-mock and node reproductions of the picker races before/after, real queues re-audited read-only (all Nhất Âu Xuân BLOCK → WARN; synthetic missing refs still BLOCK), cache keys on temp roots; two reviews (1 medium: double start) and a re-check. 717/717 tests. None of the changed files is in a scan stage cache key except brand_memory.py.

# Unreleased — studio-logo memory ignores blurred watermarks and remembers the whole ident — 2026-10-02

- User decision: "Đây là logo hãng phim — giữ & nhớ" must ignore the watermark regions the user chose to blur and remember several frames of an animated ident, so an ident remembered on a watermarked episode matches clean episodes; a foreign overlay elsewhere must still break the match. Before: a record held one preview frame (Tập 10 at 0.25 s, with the Motchill watermark), so clean episodes scored pHash 0.50 / grid 195 and never matched.
- Schema-2 records (`brand_memory.py`): frames decoded at native fps over the card's window with the scanner's own pipeline (`_iter_frames` + `_jpeg`, cpu, 320 px, bilinear, q88), self-checked byte-for-byte against the card preview (else `preview_only` with a reason); masks = decided-BLUR persistent-overlay (text or visual_logo) regions of the same source overlapping the window, padded (1 %, 2 %), filled with 128 in both images, refused above 20 % of the frame; mandatory informative-frame guard (grid range > 40; without it a black iQIYI fade frame matched ~190 black cards of other films); dedup; at most 250 frames, stored as JPEGs under `state/studio-logo-frames/` (never cleaned up; removed only together with their record). Thresholds 0.95 / 20 unchanged; v1 records behave exactly as before; OCR window-text checks stay mandatory. Masks are refreshed when a watermark card's decision changes. Review page texts explain what is remembered and ignored.
- `scripts/studio_logo_upgrade.py` (dry run by default; `--apply` backs up `state/studio-logo-memory.json` and only adds fields; aborts if the memory changes while it runs). A script, not a `biliflow` subcommand, so `cli.py` (part of every scan cache key) stays unchanged. Stop the Control Center before `--apply`: older running code cannot read schema 2.
- Gate (`reports/benchmarks/studio-mask-20261002`, temp roots only): the Tập 10 0-5 s record matches the 0-5 s cards of Tập 11, 12, 14, 16, 17, 20 and the 2.25 s frames of Tập 18/19 (grid 3-4); the 10-15 s iQIYI record matches every 10-15 s card; in queue builds Tập 12, 14, 16, 17, 20 move to "Ứng viên phụ" while Tập 11 and 15 stay required on unconfirmed OCR text; 0 of 7,803 other-film cards match; Golden Troy/Conan records match 0 Nhất Âu Xuân cards; synthetic URL/banner/box overlays outside the mask never match. Golden queues without memory identical. Two reviews (3 medium findings fixed: upgrade race with a concurrent forget, masks from another queue of the same source, upgraded card summary) and a re-check. 700/700 tests.
- Known by design: content entirely inside an ignored watermark region passes the picture check; text there is still caught by the OCR window check, a graphic is not. `brand_memory.py` is part of the visual-logo stage cache key, so the next run of each video recomputes the logo stages once.

# Unreleased — honest whole-scene logo cards ("Kiểm tra đoạn mở đầu") — 2026-10-02

- Bug (user report, Nhất Âu Xuân Tập 12): the forced 0-5 s opening card (kept once per video so external intros are never silently missed) showed a "LOGO" pill and no red box, although Qwen had answered NO, Florence found no box and OCR read nothing; the queue kept only the rewritten state "UNCERTAIN", the reason was English and hidden, and "điểm 1.000" is a geometric score. Display-only fix, no change to detection, routing, ids, suggestions, regions or edit plans: model_evidence now keeps the raw answer (`vlm_answer`, `vlm_scene`, `promoted_from_rejected_boundary`); full-scene cards keep their located boxes as `evidence_regions` (linked with `covered_by` to the whole-video watermark card that owns them); the review page names the card "Kiểm tra đoạn mở đầu" / "Logo toàn khung (chưa khoanh vùng)", states what the AI answered (or that a brand-memory match is not an AI answer), draws located boxes as dashed amber reference boxes (red keeps meaning "will be blurred"), offers "▶ Phát đoạn này" with the strip for whole-scene logo cards, translates scanner labels/reasons, and shows why a studio-logo memory did not match (`studio_logo_compared`, e.g. Tập 12: best 65 % vs 95 %) plus a warning when the frame to remember carries a watermark.
- Verification (`reports/benchmarks/opening-card-ui-20261002`): Golden Troy/Conan 20/Conan 21 and 45 job queues (47 in memory mode) rebuilt with both codes are identical apart from the display-only fields (ids, order, main/advisory membership, suggestions, regions, priorities; generated HTML byte-identical in plain mode); state/ memory files hashed unchanged. Two independent reviews (4 medium findings fixed: label naming, brand-memory match shown as an AI answer, PROMO_FULL_FRAME verdict, display after the watermark is approved) and a re-check. 674/674 tests.
- Queues built before this change show "Thẻ cũ: chưa lưu câu trả lời gốc của AI" plus the opening explanation until rebuilt; the Control Center serves the new page after a restart.
- Incident: a diagnostic on 2026-10-02 13:51 recorded decisions on a scratch copy of the Tập 10 queue with the real project root, which deleted the user's 12:29 studio-logo record (same source and item id); the user re-created an identical record at 14:08. Brand memory lost nothing.

# Unreleased — site watermarks read as scene text are reviewed as one whole-video card — 2026-10-02

- Bug (job 40 "Nhất Âu Xuân - Tập 10", advertising only): the export left the top-left "Motchillv.ph" watermark and the faint bottom line "PHIM ĐƯỢC CẬP NHẬT NHANH NHẤT TẠI MOTCHILLV.PH" visible. OCR read the watermark in 865 of 869 frames, but the semantic router called it short scene text (ad 0.16), and its 25 visual-logo confirmations were moved to "Ứng viên phụ" as uncorroborated; the semi-transparent line was only read in pieces (~5% of frames), so only three 3-6 s cards reached the queue. On Tập 18/19 the uncertain whole-film corner track merged with bottom lines into one 839x483 box (the Tập 18 export was blurred over most of the frame). Pre-existing: the 2026-09-29 code builds the same queue.
- Build-review (`promote_fixed_text_overlays`): one persistent OCR track read in >= 50% of the scanned time, or >= 8 tracks reading pieces of one line at one place over >= 30% of the scan (>= 3 long readings), becomes one `persistent_overlay` card for its own box, covering the whole scan when the text spans >= 50% of it. BLUR is only suggested with ad evidence (ad routing, a web address such as "Motchillv.ph", a visual-logo confirmation of the same box, or the site name of another such watermark); otherwise the card has no suggestion. Text items whose boxes are more than three line heights apart are never merged into one union box. Scan reports, scan stages and stage-cache keys are unchanged.
- Verification (`reports/benchmarks/fixed-overlay-20261002`, `reports/benchmarks/job40-watermark-diag`): Golden queues of Troy, Conan 20 and Conan 21 rebuilt with both codes are identical (Golden scores unchanged); of 36 job queues 33 are identical and only the three watermark episodes change (Tập 10: 5 -> 4 items, Tập 18/19: the giant box becomes two compact cards); frames rendered with the new plan for Tập 10 at 700/1384/2000 s show both marks blurred. A survey of 173 text reports finds only these marks, the "XEM8Z.NET" watermark in Troy's end-credit benchmark clips, and the Shin title overlay (one card, no suggestion). 655/655 tests.

# Unreleased — exact-output speedups: violence confirmation, source hashing, R3, logo VLM — 2026-10-02

- `confirm-violence`: one producer thread extracts the frames and runs the processor for the next intervals (at most 2 ahead, in the original order); the main thread only runs the model and decodes the answer; a lock keeps the tokenizer single-threaded. Full Troy: 424 s against 651 s in the quiet f54cc09 trial with the same code and output (-35%); interleaved A/B on 24 intervals -23%.
- `scan` (adult), `scan-live-safety` and `scan-animation-safety` hash the source on a background thread from the start of the scan and wait for it before writing (same digest, the "file unchanged" check stays). Measured overlap cost ~3 s against ~19-27 s of serial hashing per full film.
- R3 shot completion decodes two merged edge windows at a time; cuts are collected in window order and the first failing window raises as before. Troy shot completion 95.6 -> 42.4 s, 197 cuts as before.
- Visual-logo VLM loop: a producer thread writes each window's JPEGs and prepares the logo and boundary-scene prompts; the retry prompt (depends on the first answer) is still prepared on the main thread. Troy logo stage 127.5 -> 113.5 s, Conan 20 143.5 -> 128.0 s.
- `IteratorPrefetch` without a subprocess now waits for the producer's current item when the loop is left early, instead of giving up after 5 s while the producer could still use the video reader or temporary folder.
- Not kept: moving the gore transform off the main thread (identical output, no gain: the stage is limited by decoding); Florence + GroundingDINO in one process (identity not provable without changing the CLI and stage-cache keys).
- Verification: HEAD vs working tree on Troy and Conan 20 excerpts, then every touched stage re-run on the FULL films with the HEAD baseline trials' exact commands (`scripts/benchmark_stage_equivalence.py`, `reports/benchmarks/exact-speedups/stages-20261002-075009`): every scan report and all 1,347 + 695 JPEGs identical (ignoring metrics, timestamps and elapsed times); review queues and dry edit plans rebuilt from both sides identical with the HEAD build code and with the working-tree build code (Troy 118/335 and 92/335 items/advisory, Conan 20 69/393 and 53/393). Estimated saving per all-groups fast scan: Troy ~5-5.5 min, Conan 20 ~20 s. 643/643 tests.

# Unreleased — one card per fight/blood scene, studio idents — 2026-10-01/02

- Violence and gore moments closer than 20 s (span <= 300 s) form one scene card; 18+ is never merged and categories never mix. A decision applies only to the card's detected moments (edit plan = union of the members' own edits; gaps never edited). A card keeps a suggestion only when every member had that same one, so bulk "accept suggestions" never cuts an unsuggested moment. Cards per film: Troy 118 -> 92, Conan 20 69 -> 53, Conan 21 66 -> 51; Golden v1 + v1.1: no label status change, must_catch unchanged, no trap hits (`reports/benchmarks/review-load-fix2-20261001-222343`); golden scoring now scores scene cards on their moments.
- Review page: scene cards show "Trận đánh/Cảnh máu · N khoảnh khắc", a moment timeline, per-moment chips with play buttons, "▶ Phát lần lượt" (skips gaps), a strip drawn only inside moments, and the "applies only to these moments" line.
- Opening studio idents (Warner Bros., Toho) no longer pre-select CUT when the window's OCR shows no website/phone/brand/ad text (the card stays in the main list and lists any text read); "Đây là logo hãng phim — giữ & nhớ" stores a strict signature in a separate `state/studio-logo-memory.json`, and later cards matching a confirmed ident (every preview, region-aware check, no new text) move to "Ứng viên phụ". Unconfirmed logos always stay main. Three independent review rounds (overlay-ad and unknown-text cases fixed).

# Unreleased — fewer 18+ false alarms (conservative triage) and nudity shot completion on — 2026-10-01

- New `verify_adult` GPU stage (live-action jobs scanned at the calibrated 2 fps): the already-approved `image_safety_classifier_m` (MIT) scores its NSFW head on every adult interval into `adult/scan-verified.json` (never drops an interval; failure = no verification). Build-review then moves weak 18+ candidates of live-action jobs to "Ứng viên phụ" (advisory; never deleted, never marked safe; decided items never move): credits whose every interval nano labels "hentai" (unless the verifier is confident), and items with fewer than k seeds AND verifier max below t. Default level "conservative" (k=2, t=0.7, chosen so implied nudity stays flagged per the user's rule); "balanced" (k=5) is implemented but off until a second live-action film passes gate 4.6. Animation and mixed jobs are unchanged.
- Gates (docs/ADULT_FALSE_ALARM_PLAN.md §4, pre-registered; `reports/benchmarks/adult-triage-20261001-131834`): Troy rev 4 moves 41 of 86 user-KEEP false alarms (474.5 s of 983 s review), 0 of 7 BLUR, 0 of 11 lone clear-nude shots; Golden 18+ main precision 6/28 -> 6/16 with 7/7 labels (incl. implied nudity) in the main list; other groups and Conan unchanged; GPU verifier within 4.9e-5 of the CPU measurement; frames of every moved item checked: no nudity. Cost: verify_adult ~73-82 s on Troy.
- Nudity shot completion R3 now on for live-action adult scans (fast and standard). Jobs queued before this change keep their stored stage list and read `adult/scan.json`. 588/588 tests; license audit 9 allowed, 0 blocked.

# Unreleased — review page focus mode, review evidence, Golden "real but keep" — 2026-10-01

- Review page (Control Center `/review/<id>` and the standalone review UI) rebuilt as a focus mode from a user-approved mockup: one item at a time with auto-advance (toggle), a list of all filtered items to re-open any item (decided ones too), ← Trước / Sau → / ↶ Hoàn tác, keys 1–4, ←/→, Space, Z. Safety items (18+/máu me/bạo lực) show a single shared player with "▶ Phát đoạn này", a timeline of detector windows and a strip of frames across the item ("Rõ nhất" highlighted; click to seek); technical details are collapsed. Logo/text items keep their region previews and region decisions. Measured on a copy of Troy rev 4 (159 items, headless Chrome): first render 14 ms, decision → next item p95 24 ms, polling never re-renders the focus card, video not requested until played.
- Review evidence backend (`review_evidence.py`): strip frames extracted lazily with CPU FFmpeg into the bounded cache `cache/review-frames` (14 days / 1 GiB, never under `reports/`, never sent to Visual AI); read-only Range streaming of the job's own source; per-job media key. On Troy the 15:28–15:50 card now shows 15:37.5/15:39.5/15:41.5 inside the nudity the user missed with the old one-image card.
- Security: the Control Center and the standalone review UI accept only Host 127.0.0.1/localhost/[::1] (blocks DNS rebinding of the token page, `/api/session` and 18+ thumbnails under `/media/`). One queue I/O lock serializes review reads/writes in the Control Center (a fast-reviewer race that could lose decisions was found by review and fixed).
- Golden labels: safety questions now separate "Máy sai — cảnh bình thường" from "Có thật — vẫn giữ nguyên" (label KEEP + `content_present`, a positive for detection, never a trap); clearer logo question ("Thứ nằm TRONG khung đỏ…"); `LabelStore.reopen_suggestions` and a `reopen` audit action. With the user's approval 27 v1.1 questions were asked again (26 gore/violence answered "Sai" only to keep the scenes, now "Có thật — giữ"); v1 scoring is byte-identical. 551/551 tests.

# Unreleased — Q3: watermark region fix, nudity shot completion (off), Golden Set v1.1 — 2026-10-01

- Conan 21 watermark blur box: `refine_persistent_logo_regions` shrank the PhimOnline.net text track to an OCR fragment ("Online.net") because the ~40 whole-mark reads were treated as too big to count, and reconcile then let that fragment absorb the user-approved brand-memory box. Fuller reads (up to 2x the box) now mark a text-line fragment and the refined box keeps both the full mark and the fragment; padded reads still allow legitimate tightening; an approved brand-memory box is never replaced by an OCR fragment; folding is order-independent. Rebuilt golden trial queues: Troy and Conan 20 identical, Conan 21 only swaps the watermark owner to 1565,52 282x46 (all glyphs covered, checked on frames), 66 main items as before. Golden Set (labels revision 469, Conan 21 label boxes tightened with the user's approval): advertising region OK 9/13 -> 13/13, no other metric changed, detector gate clean (`reports/benchmarks/golden-q3b-region-final-20261001`). Two independent skeptic reviews (one round of fixes).
- Nudity shot completion "R3" (`scanner.complete_nsfw_shot_context`, new `shot_cuts.py`, `scan --shot-completion`), **off by default** and not wired into jobs: after sequence completion an interval edge may move to the nearest hard cut at most 8 s away when that shot has >= 5 samples >= 0.95 and >= 40% >= 0.70; thresholds unchanged, never creates intervals. On Troy it starts the bed scene at the 977.12 cut (+5.9 s of nudity, 0 s of clothed intimacy); whole film +8.7 s. Needs a second film (Golden v1.1 T6) before the user decides to enable it.
- Golden Set v1.1 (separate label set scored together with v1; v1 untouched): `load_golden_sets`, per-set scorecards (`scorecard-v1.json` reproduces the baseline), `--set` for prefill/evaluate/label server, manifest copying v1 sources after re-hashing. Segments T5/T6 (Troy 18+), T7/T8 (Troy battles; user rule: visible slashing/stabbing is flagged for review), C21E (anime blood), C20F (bloodless anime fight): 24.9 min, 86 hints. Launchers `Golden-Label-v1.1.cmd` / `Golden-Label-Phone-v1.1.cmd`; `Golden-Label-Stop.cmd` clears every set's phone link. 477/477 tests.
- Troy #39 re-scanned with all detector groups (revision 4) and reviewed by the user. A read-only check of the 20 high-priority 18+ items the user kept found one with real nudity (15:37–15:42), which the user then changed to BLUR; the review card had shown only one preview frame and no video (docs/REVIEW_EVIDENCE_PLAN.md).

# Unreleased — Golden Set v1 label audit and baseline scorecard — 2026-09-30

- The user finished labelling all 13 segments (revision 245, 111 labels) but had answered many questions with the site watermark in mind. Every answer was checked from framed video evidence by independent agents, each suspected mistake re-checked by a skeptic (`reports/benchmarks/golden-v1/audit-20260930`): 46/46 "Sai" answers correct; 59 "Đúng" answers wrong (studio logos and credits, in-scene text, seals and signs, a character's head, normal scenes as gore, bloodless cartoon fights); 31 hand-drawn duplicates of the already-labelled watermark. After the user reviewed the contact sheets and approved, `scripts/golden_audit_corrections.py` applied the plan through `LabelStore` (backup, history, actor `claude-audit`, dry run first, pinned revision): revision 457, 19 labels, 13/13 segments complete. New `LabelStore.cover_suggestion` records a question as covered by an existing label; deleted label ids are never reused (`retired_event_ids`).
- Baseline scorecard on the corrected labels (`reports/benchmarks/golden-baseline-v1-20260930-225627`, fast-scan all-groups queues of Troy, Conan 20 and a new Conan 21 golden trial, 835 s, original data preserved): recall 13/13 advertising, 1/1 adult, 5/5 gore, no violence positives; main-item precision 65% advertising, 11% adult, 24% gore, 0% violence. Real region defect: on Conan 21 the OCR-consensus refinement shrinks the watermark blur box and leaves "Phim" of PhimOnline.net visible.
- Troy 18+ around minute 16 (user report): the active Troy revision (#39 rev 3) scanned advertising only, so the Dashboard shows no adult item; when the adult detector runs it covers the nude parts (937.5–950.75, 983–1014) but not the clothed kiss/caress lead-in (884–928) nor 977–983 (scores reproduced exactly on CPU; `reports/benchmarks/golden-v1/troy-adult-check-20260930`). That scene is outside Golden Set v1. 412/412 tests (docs/QUALITY_PLAN.md §17).

# Unreleased — Golden Set labeling from a phone — 2026-09-30

- `Golden-Label-Phone.cmd` (`golden_label_server.py --phone`) opens the easy labeling page to a phone on the same home Wi-Fi: the server binds only the PC's private LAN address and requires a random 8-character access code for every request, including video and frames; the printed link sets an HttpOnly, SameSite=Strict cookie, and writes still need the session token. The app refuses a non-local address without a code. `Golden-Label-Stop.cmd` (`scripts/golden-label-stop.ps1`) stops every running labeling page and removes the saved link. Windows' one-time firewall prompt is left to the user. Verified on a copy of the real labels (401 without the code, 303 + cookie with it, page/video Range/frames/writes 200); 409/409 tests (docs/QUALITY_PLAN.md §16).

# Unreleased — faster live-action safety stages (Phase H-live) — 2026-09-30

- Troy with all detector groups spent ~52 of ~67 minutes in live-action safety (adult 631 s, gore+violence 1,848 s, VLM violence confirmation ~600 s), mostly because FFmpeg's 1080p decode and per-frame CPU preprocessing competed for the CPU while the GPU waited. `scan` (adult) now prepares the image-processor batch and `scan-live-safety` reads, unpacks and transforms the next violence window on a producer thread (`BatchPrefetch`, new `IteratorPrefetch`), in exactly the serial order. Full Troy: adult/gore/violence reports and 139 + 22 + 578 JPEGs byte-identical to the committed scanners; adult 596 -> 361 s, gore+violence 1,813 -> 1,136 s.
- Add opt-in `scan-live-safety --violence-precision fp16` (default fp32): only the violence ViT forward runs under float16 autocast. Full Troy: the stage takes 581 s instead of 1,136 s; of 558 candidate intervals, 552 differ only in max score, one strongest frame moves 1 s and one start moves 2 s; the VLM confirmation keeps the same 88 intervals and every review item (159 primary, 294 advisory) is identical. After the user's approval it is part of "Tăng tốc xử lý" for the shared gore/violence stage (`FAST_SCAN_VIOLENCE_PRECISION`); standard mode, adult, the violence-only path and the VLM confirmation are unchanged (docs/CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md §18). Full Troy pipeline with all groups in fast scan: ~67 min -> 41m47s; its review queue (159 primary, 294 advisory) equals the verified fp32 and fp16 queues.
- `scripts/benchmark_live_safety.py` (+ `.ps1`): excerpt and full-film equivalence against the committed scanners, VLM confirmation for both precisions and a review-level gate. Long runs now start as independent processes so a closing session cannot stop them. 395/395 tests.

# Unreleased — faster animation safety stage (Phase H) and review fixes — 2026-09-30

- The animation safety stage was 56% of an all-groups anime scan (Conan Movie 20: 915 of 1,642 s) and 98% model time. `BatchPrefetch` (in `frame_prefetch.py`) now reads frames and runs the CPU transform of batch n+1 while the GPU runs batch n, with exactly the serial batches. Full films: reports and 126 + 132 JPEGs byte-identical to the committed scanner; Conan 21 800.7 -> 670.9 s (-16%).
- Add opt-in `scan-animation-safety --precision fp16` (default fp32): only the tagger forward runs under float16 autocast, logits return to float32 before the sigmoid, thresholds unchanged. Full Conan 20 and 21, same text/logo reports on both sides: every primary and advisory review item identical (69/69 + 393/393, 67/67 + 61/61), every adult/gore/violence interval identical; a few frame-level gore hits differ and two Conan 20 gore interval thumbnails move by one frame (0.5 s). Stage 671 -> 208 s per film. After user approval it is part of "Tăng tốc xử lý" (`FAST_SCAN_ANIMATION_PRECISION`); standard mode stays fp32 and the stage cache keeps the two apart (docs/CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md §17). Full Conan Movie 20 pipeline with all groups in fast scan: 1,641.7 -> 950.1 s (27m22s -> 15m50s), 69/69 primary and 393/393 advisory items identical.
- `scripts/benchmark_animation_safety.py` (+ `.ps1` with the GPU mutex): excerpt and full-film equivalence against the committed scanner, review-level comparison and pre-registered gates; resumable only with unchanged code. `benchmark_full_advertising.py` review-diff logic extracted into reusable helpers (behavior checked against earlier outputs).
- A multi-agent review of all uncommitted work (16 confirmed issues, all fixed with tests) changed, before any Golden Set measurement: box/full-frame/unknown-location matching rules (a film-long watermark no longer catches whole-frame CUT labels or hits whole-frame KEEP traps), region quality from the best-covering item, fail-closed detector gates (missing timing; new false positives in a group without baseline items), transactional label writes with a single-writer lock, stale suggestions carried forward instead of `--force`, streamed evaluation output and UTF-8 console output. 387/387 tests.

# Unreleased — Golden Set v1 labeling and evaluation tooling (quality plan Q0–Q1) — 2026-09-29

- Add the approved quality plan `docs/QUALITY_PLAN.md`: a human-labelled Golden Set measures recall/precision per detector group, then gates detector changes and non-bit-identical speed work. Segments, labeling rules, matching rules and gates were fixed from real review data before any measurement (§12).
- `annotations/golden/v1/segments.json`: 13 segments (64 min; 7 dev / 6 holdout) from the three remaining sources (Troy #39, Conan Movie 20 #38, Conan Movie 21 #37); sizes and SHA-256 re-verified against the Control Center records. `annotations/` is not in git, so labels are written atomically with an append-only `label-history.jsonl` and a backup copy on every server start.
- `scripts/golden_prefill.py`: `manifest`; `collect` turns every revision of jobs 37/38/39 (including the user's earlier KEEP/BLUR/CUT decisions) and the latest isolated benchmark queues into 272 deduplicated labeling suggestions; `dense` adds per-segment `scan-text` at 1 s and `scan-visual-logo --exhaustive` with a benchmark-only routing cache (27 min for 13 segments; 132 more suggestions, 404 in total). Dense text hints keep every ad-like track; credits/title cards are otherwise dropped, and other tracks are kept when routed REVIEW_*/LOW_AD*, persistent or with ad_probability >= 0.10 (non-persistent scene text/subtitles below 0.10 are dropped). Suggestions that labels already handled are carried forward (stale) when the inputs change; golden evaluation trials never feed the hints.
- `scripts/golden-label.ps1` / `golden_label_server.py`: local labeling page on `127.0.0.1:8766` (Host check, session token for writes). Streams the manifest sources read-only with HTTP Range, extracts suggestion frames with FFmpeg into `reports/benchmarks/golden-v1/frames`, draws regions in source pixels, accepts/rejects/marks suggestions ambiguous, and only lets a segment be marked "watched completely" when every suggestion is resolved.
- `scripts/evaluate_golden.py` + `biliflow.golden_scoring`: `score` (complete segments only, groups inside each queue's detection scope), `run` (isolated full-film trials: Troy advertising, Conan 20/21 all groups, fast scan), `compare --gate detector|speed` with the pre-registered gates. Matching: union coverage ≥ 80% of the label, box compatibility ≥ 30% of the smaller box, region OK when the blur box hides ≥ 85% of the labelled box and is ≤ 4× larger. `benchmark_full_advertising.py` now also allows job 37 and a `golden` trial label.
- Verified: 361/361 tests; manifest hashes match; scorer smoke run on real Troy/Conan queues with throw-away labels in `temp/` (`reports/benchmarks/golden-smoke-20260929-214612`); labeling page exercised in the browser against a temporary label copy (7.6 GB Troy seeks in ~20 ms, region drawing maps to exact source pixels, history records every change). No production job, queue, decision, source or brand memory changed. No real labels exist yet: labeling (≈2.5–3.5 h) is the user's next step.

# Unreleased — GroundingDINO empty-frame fix and FP16 text detection in fast scan — 2026-09-29

- Fix a crash in `augment-grounding-regions`: with transformers 5.17, a frame without any GroundingDINO detection above threshold yields one empty label and zero boxes, which failed `zip(strict=True)` and stopped the localization stage (found by a Conan Movie 20 benchmark; not caused by the speed work). Such frames now contribute no regions; other mismatches still fail. Regression tests added.
- Add opt-in `scan-text --detect-precision fp16` (default fp32): only the CRAFT detector forward runs under float16 autocast; score maps return to float32 before the unchanged post-processing; recognition stays fp32. Not bit-identical; after the validation below and user approval it is part of "Tăng tốc xử lý" (fast_scan). Standard mode stays fp32.
- Review-level validation (user-authorized full pipelines, isolated caches): Troy 16m35s -> 14m27s (OCR 668 -> 540 s) and Conan Movie 20 OCR 380 -> 322 s; logo branches identical; all primary and advisory review items identical (6/294 and 2/393); differences limited to a few credits/scene-text tracks outside the review candidates and one renumbered track reference. The Conan 20 watermark-vs-head regression case is unchanged. With all detector groups on Conan 20, adult/gore/violence reports and images, the logo branch and all 69 primary / 393 advisory items are identical. 335/335 tests pass.

# Unreleased — background source hashing and parallel localization frames — 2026-09-29

- Hash the source on a background thread (`BackgroundSha256`) in the OCR stage and, when the job's checksum is supplied, in the visual-logo stage. The logo stage keys its cache with the recorded checksum and must verify the file before writing the routing cache or `scan.json`; a mismatch still fails without artifacts.
- Extract localization frames with the same FFmpeg commands in parallel: Florence prefetches up to 4 frames while the GPU works; GroundingDINO runs up to 4 extractions at once. Stage rerun on the same `scan.json`: identical `scan-florence.json`/`scan-localized.json` (timing fields only) and 112/112 identical frames; 220.1 -> 189.1 s.
- Tried and removed: running the routing warm-up at BELOW_NORMAL priority did not speed up OCR (contention is GPU/memory) and cut routing's slack before OCR ends from ~186 to ~77 s.
- Full Troy advertising pipeline (fast scan, isolated caches): 17m45s -> 16m35s (-69.9 s); OCR 685.2 -> 668.2 s, logo 133.5 -> 116.0 s, localization 220.1 -> 185.0 s. Outputs identical (reports, 858 JPEGs, review 6/294, Structure Audit PASS); production data and caches untouched. 326/326 tests pass.

# Unreleased — warm logo routing during OCR ("Tăng tốc xử lý") — 2026-09-29

- "Tăng tốc xử lý" now also warms the visual-logo routing cache while the OCR stage runs: `scan-text --prewarm-logo-routing` starts `scan-visual-logo --routing-only` (same routing code and cache key, no VLM, no report files) as a child process with the logo stage's own routing settings, NVDEC decoding and 3 workers. A failed warm-up only makes the logo stage compute routing itself; OCR failure or interruption kills the child's whole process tree. Standard mode is unchanged.
- Add `nvdec` decode backend (`--decode`, default `cpu`) with GPU-side frame dropping; allowed only for 8-bit yuv420p H.264, else falls back to software before the first frame. Verified identical framemd5 (pts + RGB24) on 11,322 frames: full Troy OCR/logo/boundary grids and six Conan 20/21 excerpts. NVDEC for OCR alone is 4.6% slower (not used there).
- User-authorized full Troy advertising A/B, same machine and day, isolated caches: 22m33s -> 17m45s (-288 s, -21.3%). OCR 596 -> 685 s (shares the machine with routing), logo 512 -> 133 s (routing cache hit). Text and logo reports identical, localized report differs in 3 timing fields, 858/858 JPEGs, review 6/294 equal, Structure Audit PASS; production job, queue, source, brand memory and caches untouched. 319/319 tests pass.

# Unreleased — parallel logo routing and per-job "Tăng tốc xử lý" — 2026-09-29

- Add `RoutingPool`: visual-logo CPU routing (regional features + brand-memory match) computed in spawned worker processes with bounded in-flight frames and results consumed strictly in frame order. Both functions are pure, so outputs are identical; `scan-visual-logo --routing-workers 1..8`, default 1 (serial path unchanged). Workers use one OpenCV thread each.
- Measured on original sources: in-memory routing 2.25x with 4 workers (exact); real routing with FFmpeg decode -24% on two 10-minute excerpts; full Troy routing 430.7 -> 355.1s median (-17.6%), identical 6,121 frames, 2,333 windows, 80 selected windows and VLM JPEG hashes. FFmpeg decode is now the floor (NVDEC and FFmpeg thread options were slower). 3 workers equal 4 on excerpts.
- Add per-job Dashboard option "Tăng tốc xử lý" (`fast_scan`): OCR batch 8 + frame window 4 and 3 logo routing workers. ON by default for jobs without a stored choice (user decision after the full-film A/B); untick to use the standard path. Persisted like the OCR option across start/rerun/resume/restart; validated before state changes; legacy OCR batch 8 keeps its meaning. Distinct from the "Nhanh" profile, which lowers sampling.
- User-authorized full Troy advertising pipeline, isolated caches, same session: standard 1532.3s (25m32s) vs Tăng tốc xử lý 1376.6s (22m57s), -10.2%. OCR 702.8 -> 613.2s, logo 574.1 -> 518.2s. Text and logo reports identical, localized report differs only in 3 timing fields, 858/858 JPEGs identical, review 6 primary / 294 advisory proposals equal, candidate coverage complete, Structure Audit PASS; production job #39, queue, source and brand memory preserved.
- Measured but not adopted: overlapping logo routing with the OCR stage saves only ~2.4-2.9 min because both stages decode the full 1080p film and saturate the 6-core CPU (OCR +18-35% slower). 301/301 tests pass. No merge or push.

# Unreleased — opt-in cross-frame OCR recognition batches — 2026-09-28

- Add experimental `scan-text --recognition-frame-window 2..8` (requires `--recognition-batch-size > 1`). Detection still runs once per frame with the serial API/parameters; crops from up to N consecutive frames share a recognition call only when their exact serial padded width matches. Frames are committed to tracking one by one, in order, with their own image. Window is bounded by frame count and a 32 MiB frame+crop byte budget (early flush; an oversized frame is recognized whole). Default stays 1; Dashboard/job commands, sampling, thresholds, models and export are unchanged.
- Occupancy on 7 original-source excerpts (150 frames, 496 crops): recognition calls 496 serial / 330 batch-8-per-frame / 172 window 4.
- In-memory ABBA (2 rounds) on the same frames: OCR detect+recognize median 27.167 -> 21.646s (-20.3%), recognition -54%; every excerpt faster (-4.4% to -35.6%). Text, points, acceptance and sort order identical; max confidence delta 3.6e-6.
- Real `scan_text` + text semantics + advertising review queue per excerpt, A/B/B/A: all reports (except confidence/metrics), preview JPEG hashes and review projections match; candidate coverage complete; max confidence delta 2e-6. Median scan excluding source hashing 35.839 -> 30.191s (-15.8%); text-heavy openings -23% to -28%, low-text excerpts within noise (-3.1% / +3.2%). Full-film gain is NOT measured.
- Real CUDA + FFmpeg cancellation during a shared 8-crop batch: 3/3 stops in 0.34-0.36s, no child or complete report left, GPU inference works afterwards.
- User-authorized full Troy OCR stage A/B/B/A (`ocr-cross-frame-downstream-20260928-231957`): serial 729.5 / 775.6s vs cross-frame 629.0 / 633.5s; median 752.6 -> 631.2s (-16.1%; conservative best-A vs worst-B -13.2%). All runs: 3,921 frames, 250 tracks, 250 identical previews, 4 primary / 12 advisory text review items; tracks also match production job #39's serial report (except scores). Max score delta 2.1e-5. OCR stage only, not the whole advertising pipeline.
- Rejected phase B experiments, both output-equivalent: batched CRAFT detection (no speedup, CUDA allocated 458 -> 1262 MiB) and cuDNN autotuning (no change). Rejected code retained only as evidence. 291/291 tests pass. No production job, review decision, source or brand memory changed; no merge or push.

# Unreleased — complete cold advertising measurement — 2026-09-28

- Complete the user-authorized original Troy advertising run with serial OCR and fresh isolated routing/GroundingDINO caches: total 29m46s. Preserve production data and decisions; no safety detectors, Visual AI or export.
- Verify unchanged OCR/logo detections, localized geometry, review proposals and all 858 JPEGs; 6 primary / 294 advisory items, full supplied-candidate coverage, Structure Audit PASS.
- Observe CPU routing 612.124 -> 517.638s against the recent pre-optimization cold run. Do not claim an end-to-end speedup: the older complete 27m29s run used different DINO cache conditions, and this run's OCR is slower. No production code/default change in this measurement.

# Unreleased — exact CPU logo-distance calculation — 2026-09-28

- Reduce RGB background-distance computation overhead while preserving pixel distances and every threshold comparison exactly. Split logo CPU timing into feature extraction and brand-memory matching. No model, sampling, thread-count, review or export behavior changes.
- Verify 270 original-source frames across six Troy/Conan excerpts against immutable baseline code: identical features/geometry, median CPU routing 13.482 -> 10.718s (-20.50%). This is a CPU-stage measurement, not end-to-end video speed.
- Verify actual cold routing on 165 additional samples from dense opening/ordinary middle sections: same 18 windows, selected candidates and VLM JPEG inputs. Preserve source/review/brand memory. Pass 278/278 tests.
- Profile serial OCR detection/recognition without changing it. Leave batch 8 optional. Retain rejected previous-edge-cache experiment only as evidence; no full-video rerun, merge or push.

# Unreleased — lossless visual-logo routing cache — 2026-09-28

- Fix a pre-existing cold/warm mismatch found in the full Troy advertising trial: keep every routing frame's features and JPEG bytes, rather than reducing to two before regional candidate selection. The existing two-image VLM evidence selection still happens after routing.
- Version routing-cache identity/serialization as v2 and reject legacy lossy caches without deleting reports or decisions. Preserve model inputs, thresholds, sampling and existing cache cleanup limits.
- Reproduce the middle-frame loss in a regression test, then pass all 276 tests. Corrected full Troy advertising results retain 199/199 regional leads, 116 logo intervals, identical review proposals and all baseline preview hashes. Structure Audit passes; original review/source/brand memory are preserved.
- Verify a real warm v2 run matches the cold report and 496 scanner JPEGs. Same-video logo stage: 812.780s cold / 181.821s warm. Full-film OCR batch 8 (765.102s) offers no material demonstrated gain against historical serial (770.734s); keep batching optional and serial as default. No safety scan, export, merge or push.

# Unreleased — per-job experimental OCR mode — 2026-09-28

- Add an OCR selector to Dashboard start/rerun controls: standard (default batch 1) or experimental faster recognition (batch 8). Preserve drafts across refreshes; persist the selected value through rerun, resume and restart, and display the current mode on the job card.
- Exclude `reports/benchmarks` and report directories marked `.biliflow-benchmark` from automatic Dashboard import, so isolated trials cannot replace active review revisions. Existing production reports/decisions are retained.
- Validate exact integer modes before state changes. Only the advertising text stage receives the opt-in CLI flag; other detector commands, sampling, thresholds and export remain unchanged. Stage-cache command identity separates the two OCR modes.
- Pass 274/274 tests, including executed Dashboard JavaScript, invalid-value rejection, persistence, scope and cache separation.
- Complete the user-authorized full advertising trial for Troy under `reports/jobs/troy-ocr8-full-20260928-182704`, with separate state/logs under the matching `reports/benchmarks` directory. OCR equivalence passes; its pre-existing logo-cache mismatch is fixed and verified in the follow-up documented above. No production review decisions, Visual AI or export are changed/run.

# Unreleased — original-source Troy OCR pilot — 2026-09-28

- Validate opt-in batch 8 directly on Troy source 48–138s, with four warm serial/8/8/serial runs. Retain 42 tracks, native-resolution regions, primary/advisory review mappings and identical preview hashes; maximum confidence delta 0.000001.
- Median total OCR scan including full-source hashing/decode improves 7.51% (33.257 -> 30.760s); OCR model work improves 21.55%. This does not measure complete advertising or all-detector throughput.
- Retain isolated benchmark evidence and pass 268/268 tests. No runtime source, Dashboard default, source media, existing review decision or export behavior changes.

# Unreleased — OCR boundary and cancellation validation — 2026-09-28

- Add standalone synthetic OCR stress benchmark with lossless fixture hashes, actual effective-threshold diagnostics and isolated report/review comparisons. Keep the production launcher unchanged to avoid unrelated cache invalidation.
- Validate 48 generated frames / 183 OCR regions: unchanged text, accepted regions, preview hashes and review mappings; max confidence difference 0.000005918. Moving text, degraded text and low-threshold top banners are covered in this synthetic corpus.
- Exercise actual CUDA workers and FFmpeg children with the production process-tree stop method: three clean stops in 0.154–0.164s, followed by successful GPU inference. This does not certify the Dashboard UI or OOM recovery.
- Pass 268/268 tests. Serial default remains unchanged; no production source, saved decision, threshold or export changes.

# Unreleased — contiguous OCR equivalence benchmark — 2026-09-28

- Add bounded `benchmark-ocr-contiguous` with GPU locking, lossless sampled-frame verification, report/preview/review comparisons and a failing exit status if equivalence or supplied-candidate coverage fails.
- Validate six 90-second Troy/Conan windows, 180 unique frames and 24 measured runs. All accepted report content, preview hashes and review projections agree; maximum confidence delta 0.000006.
- Measured summed median OCR-to-report time on warm sampled fixtures improves 15.56%, excluding original-source hashing/decode and other stages. PyTorch peak allocated memory is equal on this corpus. Default batching remains 1 pending further validation.
- Add comparator regressions and interruption propagation coverage; 265/265 tests pass. No production queue, saved decision, detector threshold, export behavior or source video changed.

# Unreleased — opt-in same-width OCR recognition batches — 2026-09-28

- Add experimental `scan-text --recognition-batch-size 2|4|8` for CUDA vi/en. Default remains 1 in CLI, Python and Dashboard; sampling, detection, thresholds, model and export behavior are unchanged.
- Group only crops with the exact padded width used by serial EasyOCR, restore original order, preserve contrast retries and bound batch count/input area. Ordinary EasyOCR batching changes padding and was rejected after observed text changes.
- Across 39 original-source frames from Troy and Conan 20/21 (109 text boxes), same-width batches retain text and accepted regions; score differences reach 0.000001101. Recognition-only time falls 19.0% across the measured fixtures.
- Reversed-order OCR/tracking/semantic/report benchmarks on lossless fixture sequences are 3.7–6.3% faster, with the same tracks, geometry, classification and preview hashes. Reports differ slightly in confidence values, so exact numerical equivalence is not claimed.
- Pass 260/260 tests. This remains opt-in pending longer contiguous-video review/coverage validation; no production rerun, default activation, merge or push.

# Unreleased — bounded OCR prefetch experiment — 2026-09-28

- Add an opt-in raw-frame queue (depth 0..4, 32 MiB raw-buffer budget with serial fallback), preserving bytes, sampling, frame order, OCR parameters and review behavior. Default remains serial (`prefetch_frames=0`).
- Ensure queue backpressure, propagation of reader failures and early-exit cleanup of the FFmpeg reader; normal OCR also closes its pipes.
- Add a mutex-protected short OCR A/B benchmark with warm models and reversed run order. Troy 00:00–00:30 and 06:58–07:28 match every detection payload and preview hash across ten runs.
- Measured OCR wall time excluding source hashing changes by less than 1%, inconsistently. Do not enable prefetch in Dashboard/production profiles or claim new-video acceleration from this result.
- Pass 252/252 tests. Evidence: `reports/benchmarks/frame-prefetch-20260928-161812/`.

# Unreleased — dependency-scoped stage cache — 2026-09-28

- Reuse scan artifacts after unrelated dashboard/review changes by tracking the selected scanner's transitive local imports, including lazy imports. Keep CLI/launchers, shared policy/config and model identities conservative; unresolved/dynamic imports fall back to the full source tree.
- Include upstream report contents, OCR semantic seeds and logo brand memory in cache identity. Recompute fingerprints while the dashboard remains open instead of retaining its first fingerprint indefinitely.
- Refuse to cache a completed stage under a different dependency identity if code/config/input reports changed while it ran.
- Advance cache schema to v2; v1 entries are not trusted or migrated. Existing reports, review decisions and outputs are retained, and cache cleanup limits stay unchanged.
- Verify real Troy OCR artifacts in an isolated project: after a UI-only edit, baseline misses while the scoped cache restores 252 byte-identical files in 1.826s. This measures cache reuse, not new-video inference or an end-to-end rerun. Pass 247/247 tests.

# Unreleased — scan timing instrumentation — 2026-09-28

- Add bounded exclusive host-wall timings across text, visual-logo, adult and safety scanners plus localization/confirmation; preserve model inputs, thresholds and export behavior.
- Add a read-only per-job timing report that distinguishes absent telemetry, historical cache profiles and shared scan timings.
- Add a GPU-slot-aware short equivalence benchmark; no production job or review queue is created.
- Pass 236/236 tests and a real 12-second Troy CUDA equivalence smoke: text, adult and shared live-action safety detection payloads match the baseline, with 54 identical preview hashes. Full-film speed improvement is not yet measured.
- Continue on `improve/scan-performance-metrics`; implementation sequence and evidence are in `docs/SCAN_PERFORMANCE.md`.

# 0.7.24 - 2026-09-27

- Map approved brand-memory candidates by learned logo geometry and five-minute time bucket instead of treating every reviewed memory record as a separate physical track.
- Require `0.94` similarity before a brand-memory match can bypass semantic confirmation; weaker matches return to regional/full-frame candidate routing.
- Preserve every concrete regional lead, two complementary full-frame representatives per time bucket, and one representative for every approved geometry track per time bucket.
- Fix distributed candidate selection so it cannot append past the configured semantic-model budget.
- Validate the new routing against Troy's cached 2,330 windows: all 181 regional leads, all 80 required full-frame representatives, and all 39 approved geometry/time groups are covered within the existing 420-window budget.

# 0.7.23 - 2026-09-27

- Promote sustained adult findings labelled `porn` or `hentai` to high-priority review when confidence is at least `0.99` for four seconds or longer; this changes review ordering only and never creates or approves an edit.
- Add a safe priority refresh for an existing queue so detector output and saved review decisions remain untouched after the ranking upgrade.
- Verify the reported Troy sequence: explicit content is detected at `15:28.5–15:50.5` and `16:23–17:07`; the gap is a legitimate banquet cutaway rather than a missed continuation.

# 0.7.22 - 2026-09-27

- Refresh an already-open Review page when a rerun publishes a new queue revision, so newly detected adult, violence and advertising findings cannot remain hidden behind the previous in-memory queue.
- Keep the active review filter while refreshing, reload resource/export state for the new revision, and continue storing every user decision immediately before any refresh can occur.
- Confirm the completed Troy rerun contains the reported explicit scene as an `adult` review item covering `06:54.5–07:45.5` in both the detector report and the live dashboard queue.

# 0.7.21 - 2026-09-27

- Preserve the open or closed state of each video's `Chạy lại kiểm tra` panel across the Dashboard's three-second refresh cycle.
- Keep detector checkbox drafts visible while the panel is open and clear the saved panel state only after a rerun is successfully queued.
- Sort sibling stage-artifact roots deterministically so shared gore/violence cache snapshots cannot miss only because set iteration returned a different directory order.
- Keep detector commands, review data and media processing unchanged; the cache fix only makes an existing exact-match restore deterministic.

# 0.7.20 - 2026-09-27

- Complete live-action adult scenes around high-confidence NSFW seeds instead of exposing only isolated frames above the global threshold.
- Keep `0.95` as the only threshold that can create a review item; use `0.70` evidence only to extend an existing item by at most eight seconds, so moderate standalone frames do not add false findings.
- Merge review gaps up to three seconds inside one adult sequence while leaving longer gaps and unrelated candidates separate.
- Pin the sequence policy in processing profiles and adult-stage commands, which prevents an older cached adult report from satisfying a rerun after this detector upgrade.
- Recheck Troy source scores: the fragmented `07:01.5–07:40.5` detections now form one review interval covering `06:54.5–07:45.5`; the separate `07:55–07:58` candidate remains independent.
- Keep advertising/logo, gore, violence, review decisions and rendering unchanged; pass 223/223 automated tests.

# 0.7.19 - 2026-09-27

- Keep the export progress bar visible at 100% on completed video cards.
- Show `100% · Đã xuất video` together with the localized completion timestamp from the durable job record.
- Keep this presentation-only: no renderer, scheduler, detector, review or output behavior changed.
- Pass JavaScript syntax validation and 211/211 automated tests.

# 0.7.18 - 2026-09-27

- Add live FFmpeg export telemetry without changing encoding or edit decisions: output percentage, speed and estimated time remaining.
- Calculate export percentage against the expected post-CUT duration so edited videos reach 100% accurately.
- Show a separate export progress bar while the existing analysis bar remains dedicated to scan/review preparation.
- Treat FFmpeg completion as a visible `Đang kiểm tra output` phase until duration, streams and full decode validation finish.
- Divide the `Đang chạy` tab into `Đang phân tích để duyệt` and `Đang xuất video` sections.
- Store telemetry only in a small temporary file and remove it after success, failure or interruption.
- Verify the installed FFmpeg `-progress` protocol with a real two-second encode; pass 211/211 automated tests.

# 0.7.17 - 2026-09-27

- Reorganize the Control Center video list into `Đang chờ xử lý`, `Đang chạy`, and `Hoàn tất` tabs with live counts.
- Give every video separate, readable status cards for scene analysis, local structure audit, Visual AI Audit, and final export.
- Distinguish `Chờ xuất video`, `Đang xuất video`, `Đang kiểm tra output`, and `Đã xuất video` from the generic internal job state.
- Move rerun detector choices into a compact disclosure panel so routine actions and status remain easy to scan.
- Keep the dashboard change frontend-only: API payloads, job states, scheduler, detectors, review decisions, renderer, and existing outputs are unchanged.
- Verify the live server HTML and JavaScript syntax; pass 208/208 automated tests.

# 0.7.16 - 2026-09-27

- Remove short regional blur operations already covered by an equivalent persistent blur before building the FFmpeg graph.
- Combine identical full-frame blur operations into one filter with multiple time ranges instead of creating one filter per review decision.
- Reduce Troy job #39 from roughly 66 serial blur filters to one persistent regional overlay and one time-ranged full-frame blur while preserving all approved decisions.
- Stop the complete Windows stage process tree during an immediate pause, then remove only that render's incomplete `.partial` output so a retry cannot leave an orphaned FFmpeg process or fail on stale output.
- Verify live recovery of Troy job #39: attempt 3 entered `RENDERING` and the partial output grew continuously after the optimized restart.
- Pass 207/207 automated tests.

# 0.7.15 - 2026-09-27

- Prevent one OCR observation that joins a fixed watermark with nearby scene text from expanding a full-film blur region.
- Build repeated corner-overlay regions from the dominant size and position consensus while preserving small localization variations.
- Refine existing persistent text-logo proposals from repeated, spatially consistent visual OCR regions, so completed text scans do not need to run again.
- Tighten Troy job #39 XEMBZ.NET from `x=110, y=146, 626x84` to `x=105, y=160, 174x54` using 51 corroborating OCR regions; local structure audit remains PASS.
- Pass 204/204 automated tests.

# 0.7.14 - 2026-09-27

- Separate concrete regional logo leads from generic full-frame temporal hits during semantic candidate selection.
- Require every strong regional lead to reach the local VLM, while representing ordinary full-frame hits with complementary ident-like and persistence-like samples in every five-minute bucket.
- Treat weak coarse-tile advantages as full-frame coverage so low-motion movie footage cannot exhaust the careful scan budget.
- Keep detector coverage strict: missing regional leads or missing time-bucket representatives still produce an incomplete manifest and a local structure BLOCK.
- Revalidate Troy job #39 without rerunning adult or violence detection: 285/285 regional candidates and 80/80 required full-frame representatives are covered; 1,889 redundant full-frame hits are collapsed; local structure audit now passes.
- Pass 202/202 automated tests.

# 0.7.13 - 2026-09-27

- Add a per-video export-size selector to Review: the existing 3.5 GB default, a custom GB ceiling, or unlimited output size.
- Persist the selected policy in the review queue and edit plan, and pass it through the durable scheduler to the final renderer.
- Give custom and unlimited exports distinct output identities so changing a video's size policy cannot collide with an existing render.
- In unlimited mode, use CRF 20 without a VBV size ceiling while retaining disk-reserve, A/V, duration, full-decode and source-checksum validation.
- Keep old queues and commands on the 3.5 GB default.
- Preserve each new video's content-style and speed-profile drafts while the dashboard refreshes every three seconds, so `Phim thực tế` no longer resets to `Hoạt hình` before Start.
- Pass 200/200 automated tests.

# 0.7.12 - 2026-09-26

- Show the exact decision scope on every Review card: continuous track, grouped detection window, single interval, or optional candidate.
- State explicitly that a decision never propagates to every visually similar logo or advertisement unless those occurrences were already consolidated into the displayed track.
- Explain that optional candidates are uncorroborated findings outside the main decisions; selecting one promotes only its displayed interval into the edit plan.
- Warn on single-purpose scans that fewer cards reflect skipped detector groups and do not certify those groups as safe.
- Prevent a repeated approved watermark from consuming the semantic candidate budget for a full-length video.
- Select every novel visual-logo window first, then retain at least one representative for each approved brand in every five-minute timeline bucket.
- Raise the careful profile ceiling to 420 windows and the fast profile ceiling to 120 while collapsing repeated approved-brand evidence.
- Record novel-window and approved-brand time-group coverage separately in every visual-logo report.
- Propagate detector omissions into the review queue and deterministic AI Audit gate; an incomplete scan can no longer be reported as a complete edit plan.
- Require the exact localized region to provide its own brand proof. Frame-level Qwen confirmation no longer lets Florence or GroundingDINO boxes inherit a BLUR action.
- Allow only confirmed approved-brand memory to create a persistent timeline; weak memory similarity and opening promotions cannot be stretched to the end of a movie.
- Keep separate visual boxes separate below 50% IoU so OCR proof cannot leak into a nearby DINO region; compact OCR noise and ordinary words no longer qualify as site marks.
- Route low-ad scene text and uncorroborated regional candidates to the optional audit list while counting them as represented coverage.
- Verify Conan Movie 20 end to end: 320/320 novel windows and 70/70 approved-brand time groups are retained, 900 repeated watermark windows are collapsed, coverage is complete, and the mandatory queue contains only the full-timeline PhimOnline blur plus the 5–10 second opening ident CUT.
- Confirm that advertising-only and all-model runs build identical advertising stages.
- Pass 196/196 automated tests.

# 0.7.11 - 2026-09-26

- Distinguish required review items from optional evidence directly in the Review UI.
- Show the optional-candidate count in the summary and on a prominent amber `Ứng viên phụ (N)` button placed next to `Chưa duyệt`.
- Rename `Tất cả` to `Tất cả mục chính` and explain that optional candidates do not block export.
- Verify the review and Control Center test suites and parse-check the rendered Review JavaScript.

# 0.7.10 - 2026-09-26

- Separate frame-level logo confirmation from region-level proof, preventing a real corner watermark from turning an unrelated face, object, or OCR fragment into a required logo review.
- Route single-source, unclassified regional proposals to optional audit candidates. They remain visible and can be promoted by the reviewer, but do not block export.
- Route non-overlay OCR fragments below 20% advertisement probability to optional audit candidates while persistent overlays and policy matches remain mandatory review items.
- Keep full compatibility with existing scan reports, so completed jobs gain the new routing without rerunning local models or Visual AI Audit.
- Rebuild Conan Movie 20 as review revision 2 from cached reports: required items fall from 19 to 4, the approved full-timeline PhimOnline blur is preserved, and all weak evidence remains available among 61 optional candidates.
- Pass 174/174 automated tests.

# 0.7.9 - 2026-09-26

- Let every video select any combination of advertising/logo, adult, gore, and violence detection, with an explicit all-groups option.
- Build only the selected pipeline stages and reports. Advertising-only jobs skip all safety models; single-purpose live-action jobs skip unrelated models.
- Preserve selected detector groups per job and allow a rerun to choose a different scope while keeping earlier revisions.
- Record selected and skipped groups in every new review queue and show both on the dashboard/review page, so an unscanned category is never presented as safe.
- Run deterministic JSON structure checks locally when the review queue is built. This uses no Codex session, model, media upload, or ChatGPT quota; Visual AI remains explicit opt-in.
- Keep the animation adult/gore/violence categories on one shared inference pass when any safety category is selected, preserving quality and avoiding duplicate decoding.
- Pass 169/169 automated tests and parse-check the rendered JavaScript for both Control Center and Review UI.

# 0.7.8 - 2026-09-26

- Reuse one persistent AI Supervisor thread for JSON Audit, Visual AI Audit, every visual batch, and subsequent videos.
- Serialize audit turns so concurrent video jobs cannot write into the shared thread at the same time.
- Keep the shared thread when the configured model or reasoning effort changes; create a replacement only when the saved thread cannot be resumed.
- Show shared-session state in Control Center.

# 0.7.7 - 2026-09-26

- Tighten an approved brand-memory box from foreground contrast in the current full-resolution frame, with conservative no-op gates for noisy or ambiguous backgrounds.
- Ensure a consolidated end-card uses a primary thumbnail captured inside its refined interval.
- Require explicit full-frame boundary semantics before a final sequence can become a CUT suggestion; a corner watermark over black frames is insufficient.
- Cross-check localized visual-logo regions against low-ad OCR scene text so an unrelated frame-level watermark cannot turn a book, sign, subtitle, or interface label into a blur suggestion.
- Attach source-frame dimensions to text review items and clip their proposed rectangles to valid frame bounds.
- Verify the Conan regression queue at 79/79 covered candidates with no missing references: the persistent PhimOnline watermark is tightened from 308x69 to 282x46, `SCRAP BOOK` stays as in-film text, and no false full-scene end-card CUT remains.
- Pass 161/161 automated tests and the local license audit with 9 allowed models and 0 blocked models.

# 0.7.6 - 2026-09-26

- Return the relative region of the winning brand-memory signature instead of the last record inspected.
- Add a multi-record regression proving that a correct top-corner watermark match cannot inherit another record's geometry.

# 0.7.5 - 2026-09-26

- Preserve every unresolved review item when rebuilding the same source queue after a detector upgrade.
- Mark preserved candidates with migration provenance so algorithmic suppression never substitutes for a human decision.

# 0.7.4 - 2026-09-26

- Route high-confidence brand-memory matches to the exact learned relative region instead of relocalizing every text fragment in the frame.
- Reject regional pHash matches whose position, scale, or aspect ratio conflicts with the approved signature.
- Exclude full-frame CUT/scene decisions from the regional brand-memory library.
- Prevent an approved brand-memory region from being relabeled as a movie title and suppress unrelated Grounding DINO fallbacks.

# 0.7.3 - 2026-09-26

- Clarify that logo classification applies only to the red target region, while persistent watermarks elsewhere in the same frame are handled by their own review item.
- Stop unrelated overlapping regional blurs from appearing as coverage for the current target.
- Warn before a reviewer overrides a high-confidence Visual AI decision with a conflicting edit.

# 0.7.2 - 2026-09-26

- Absorb short OCR fragments that are fully contained in a confirmed persistent watermark region and timeline; preserve their evidence under the main review item.
- Conan revision 2 now has 93 pending items after removing one redundant NPt watermark fragment. No decision was applied; integrity blockers and deterministic quality findings remain empty.
- Full test suite passes: 148/148.

# 0.7.1 - 2026-09-26

- Scope AI Audit state and artifacts to the active queue revision so reruns never display or overwrite an older audit.
- Generate deterministic region-aware review IDs and block duplicate IDs in the deterministic audit gate.
- Treat branded end cards as one full-scene CUT proposal even when a region model also proposes BLUR.
- Include source frame size and target-region guidance in Visual AI evidence to avoid classifying a logo outside the reviewed box.
- Suppress bulk suggestions when high-confidence Visual AI conflicts with the local proposal; the item remains pending for human review.
- Migrated Conan revision 2 without applying decisions: 94/94 unique IDs, 35 Visual AI assessments retained, final end card proposed as CUT.
- All 148 tests pass.

# 0.7.0 - 2026-09-26

- Thêm benchmark độc lập SigLIP + GroundingDINO Tiny trên quyết định review thực tế.
- Thêm cache dùng chung theo hash ảnh/model/cấu hình; không sửa queue hay video production.
- Thêm cổng chất lượng recall/specificity trước khi cho phép tích hợp vào pipeline chính.
- A/B PaddleOCR với EasyOCR trên 34 ảnh thật; giữ EasyOCR vì cùng recall vùng BLUR nhưng nhanh hơn khoảng 43% theo thời gian mỗi ảnh.
- Chỉ duyệt model local, miễn phí và Apache-2.0 theo license policy hiện hành.

# Changelog

## 0.6.1 — 2026-09-25

- Thêm Visual AI Audit theo quyền riêng của từng job. Dashboard chỉ gửi thumbnail sau hộp xác nhận; tối đa 36 ảnh trong reports, không gửi video hoặc âm thanh nguồn.
- Codex App Server nhận localImage trực tiếp và trả phân loại có cấu trúc: logo thương hiệu, tiêu đề phim, quảng bá, nội dung phim, adult, gore, violence, false positive hoặc uncertain; kèm độ tin cậy, đề xuất xử lý và đánh giá vùng blur.
- Nhận xét Visual AI được gắn vào từng thẻ review và có thể trở thành đề xuất để người dùng nhận hàng loạt. AI không tự đặt KEEP/BLUR/CUT và không khởi động render.
- Khóa chi phí giữ chatgpt_required, chatgpt_included_only, api_key_allowed=false, GPT-5.6 Luna/Medium mặc định và giới hạn tối đa 50 thumbnail.
- Visual audit chia thành các batch 12 ảnh và hợp nhất theo item ID để tránh giới hạn ảnh mỗi lượt; prompt nhận manifest queue rút gọn nên không phụ thuộc vào việc model tự mở JSON.
- Test thực tế Conan cho thấy low-detail không được GPT-5.6 Luna nhận ổn định; mọi thumbnail nay dùng high-detail. Lượt cuối nhận đủ 36/36 đánh giá: 24 KEEP, 1 CUT, 1 BLUR, 10 NEEDS_MORE_CONTEXT và 0 quyết định tự động.
- Smoke test ảnh tổng hợp bằng GPT-5.6 Luna/Medium nhận đúng logo ngoài, đề xuất BLUR với độ tin cậy 0,99 và vùng TIGHT. Toàn bộ 137 test đạt; license audit giữ 8 model được phép, 0 model bị chặn.

## 0.6.0 — 2026-09-25

- Launcher Control Center dùng entrypoint nhẹ, không nạp trước Torch và toàn bộ scanner AI. Warm start dùng database hiện có, chặn double-click bằng launcher mutex và giữ cửa sổ lỗi để không còn trạng thái bấm Start nhưng tưởng như không chạy. Có thể dùng `Start-BiliFlow.cmd -RefreshExisting` khi cần nhập lại toàn bộ lịch sử.

- Thêm bộ nhớ logo cục bộ từ quyết định review: 27 chữ ký được dựng từ 15 queue cũ (7 BLUR, 20 CUT). Chữ ký chỉ route candidate; không tự sửa và không chuyển timestamp/vùng edit sang video mới.
- Thêm cache routing logo nén gzip theo SHA-256 video, cấu hình scanner, phiên bản thuật toán và revision bộ nhớ. Stage lỗi sau routing có thể chạy lại mà không giải mã lại phần tìm ứng viên.
- Quét thích ứng giữ fallback theo từng đoạn 5 phút và route thêm chuyển cảnh có tín hiệu visual để giảm nguy cơ bỏ sót logo dưới ngưỡng heuristic.
- Gom được nhiều persistent logo ở các vùng khác nhau thành các track riêng; review không còn gộp các candidate kề nhau nếu vùng Florence không chồng nhau.
- Florence ưu tiên visual grounding trước OCR title khi thương hiệu chưa xác định. Kiểm chứng NewGates tạo đúng vùng `130,104,159,67` và `97,13,240,224` trên hai cửa sổ liên tiếp.
- Cache logo cũ hơn 30 ngày được liệt kê trong cleanup dry-run; `state/brand-memory.json` được giữ vì nhỏ và không chứa video.
- Dashboard tự làm mới session token sau khi backend restart, báo rõ kết quả lưu/kiểm tra AI, phân biệt `AI: chờ queue` với lỗi kết nối, xác nhận khi backend đã tắt và hỗ trợ `Chạy lại từ đầu` bằng report revision riêng.
- Chế độ `codex_command: auto` tự tìm Codex Desktop trong LocalAppData và truyền `CODEX_HOME` từ hồ sơ Windows, nên mở dashboard bằng double-click vẫn nhận đúng đăng nhập ChatGPT mà không lưu token vào project.
- Báo cáo OCR mới ghi SHA-256 của video nguồn; AI Supervisor có thể đối chiếu đồng nhất nguồn trên toàn bộ detector trước khi đưa queue ra review.
- OCR gom các lần đọc chập chờn nhưng cùng chữ và cùng góc; chỉ đề xuất `persistent_overlay` khi có thêm bằng chứng quảng cáo/thương hiệu. Chuỗi lặp lại có điểm quảng cáo thấp được giữ như tiêu đề phim.
- Queue đối chiếu vùng visual-logo với OCR tiêu đề: vùng chồng khớp được đề xuất KEEP, ngăn xác nhận logo thật ở một góc làm hệ thống blur nhầm tên phim ở góc khác.
- Tách các proposal Florence trong cùng frame thành item review độc lập và thêm phân loại theo vùng. Bộ nhớ logo ghi cả chữ ký âm từ quyết định KEEP, nhưng chỉ áp dụng lên vùng crop; nó không được phép loại cả frame khi nơi khác vẫn có logo.
- Review khoanh đỏ vùng đang được phân loại, hiển thị crop phóng to và tọa độ trước khi cho xác nhận `Vùng khoanh đỏ là tiêu đề phim` hoặc `Vùng khoanh đỏ là logo thương hiệu`. Phép chiếu vùng xử lý cả preview 16:9 và ảnh detector vuông có letterbox.
- Thêm `Xem tất cả ứng viên` với ảnh audit giới hạn dung lượng. Các cửa sổ VLM đã loại không chặn xuất; nếu người dùng chọn một ứng viên, nó mới được chuyển thành item chính thức. Giao diện có hai thao tác ngữ nghĩa `Đây là tiêu đề phim` và `Đây là logo thương hiệu`.
- Review phân biệt `Làm mờ logo`, `Làm mờ toàn cảnh` và `Cắt cả cảnh`; thẻ cảnh cũng báo khi thời gian đó đã được một quyết định blur ở mục khác che phủ.
- Kiểm chứng local 15 giây: 3/3 cửa sổ được giữ, 2 cửa sổ khớp bộ nhớ, cache hit ổn định ở hai report độc lập; Florence định vị 3/3. Kiểm chứng thêm frame 25 và 209 của `#874-B2` xác nhận logo trái được BLUR, title phải được KEEP. Review nay cho phép xem và đổi quyết định vùng logo liên kết ngay trong thẻ cảnh chưa duyệt. Cửa sổ đầu/cuối được phân loại theo ngữ cảnh để gộp quảng bá toàn khung thành CUT; vùng BLUR logo được hiệu chỉnh giữa grounding và OCR; AI Supervisor chặn các CUT đầu video thiếu và vùng logo quá nhỏ hoặc quá rộng. Queue dài có candidate coverage manifest, tách OCR không liên quan khỏi watermark dài và gom OCR/visual của cùng watermark thành một quyết định. Toàn bộ 134 test đạt và license audit giữ 8 model được phép, 0 model bị chặn.

## 0.5.2 - 2026-09-25

- Đổi mặc định AI Supervisor sang `gpt-5.6-luna` + reasoning `medium` theo giới hạn sử dụng Plus.
- Chỉ cho phép model thuộc dòng GPT-5.6; reasoning tối đa `high`. GPT-6 và XHigh/Max/Ultra bị chặn ở cả cấu hình lẫn API dashboard.

## 0.5.1 - 2026-09-25

- Thêm bảng kết nối AI Supervisor ngay trên dashboard: trạng thái Codex, nút mở luồng đăng nhập ChatGPT, kiểm tra lại và link tài liệu OpenAI chính thức.
- Thêm `config/ai_supervisor.json` để cấu hình đi cùng project khi chuyển máy.
- Khóa mặc định Supervisor ở `gpt-6-luna` + reasoning `low`; dashboard chỉ cho chọn Luna/Sol và Low/Medium, service tier luôn `default`.
- Chặn API key, Astra, High/XHigh và truyền media. Mỗi thread/turn App Server đều nhận lại model/effort đã khóa, không phụ thuộc model mặc định của máy.
- Đổi model/effort sẽ bỏ thread ID cũ để lượt kế tiếp tạo thread đúng cấu hình mới.
- Kiểm thử dashboard thật xác nhận Codex được nhận diện, đăng nhập ChatGPT sẵn có, Luna/Low được lưu và yêu cầu `high` bị từ chối HTTP 400. Bộ test đạt 99/99.

## 0.5.0 - 2026-09-25

- Thêm Control Center chạy theo phiên bằng `Start-BiliFlow.cmd` và `Stop-BiliFlow.cmd`; có single-instance lock, không cài service và đóng tab không làm dừng worker.
- Thêm SQLite WAL lưu job, stage, revision, artifact, event, setting và trạng thái input watcher; nhập 6 video cùng 14 review revision cũ mà không sửa report nguồn.
- Thêm scheduler một worker phù hợp RTX 2060 6 GB, hỗ trợ nhiều video trong queue, pause, stop-after-stage, cancel, retry và phục hồi stage sau khi ứng dụng/máy dừng bất ngờ.
- Thêm dashboard đọc tài nguyên CPU/RAM/GPU/ổ E, trang review riêng theo job và luồng xuất bền vững; render không còn phụ thuộc daemon thread của tab review.
- Thêm input watcher chờ file ổn định 60 giây và SHA-256 chống trùng; video mới dừng ở `NEEDS_METADATA` để người dùng chọn hoạt hình/phim thật/hỗn hợp.
- Thêm profile `careful` và `fast` cho mật độ safety, OCR và logo scan. Cả hai vẫn tạo queue cho người dùng duyệt trước khi sửa.
- Thêm AI Supervisor theo yêu cầu qua Codex App Server, dùng persistent thread và JSON schema; kết quả chỉ tư vấn, không tự BLUR/CUT/render và mặc định không gửi media.
- Giữ giới hạn output cứng 3.500.000.000 byte, source bất biến và toàn bộ gate kiểm tra final hiện có.
- Smoke test dashboard trên cổng phụ đạt: 6 job, 14 revision, review queue 8 mục, single-instance và shutdown không để listener/process. Bộ test tự động đạt 97/97.
- Upload BiliBili, Telegram/Tailscale và archive/retention automation được giữ ngoài phạm vi bản này theo quyết định của người dùng.

## 0.4.0 - 2026-09-24

- Nâng quét logo nhỏ bằng crop vùng chồng lấn và Qwen2-VL local; kiểm chứng thực tế đã gom logo NewGates Anime xuyên phim thành một candidate `6,000–429,312s` thay vì bỏ sót.
- Thêm theo dõi thời gian cho logo cố định và end-card: cảnh mở đầu, watermark xuyên phim và brand cuối phim giữ thành các quyết định độc lập, không còn bị gộp thành một mục kéo dài toàn video.
- Florence-2 giới hạn đề xuất theo vùng seed của detector; candidate NewGates chỉ còn một vùng `97,13,240,224`, không kéo sang tiêu đề phim bên phải.
- Giảm scan logo chất lượng cao của video kiểm chứng 445 giây từ 80 xuống 40 cửa sổ VLM, tối đa ba ảnh bằng chứng mỗi cửa sổ; lượt cuối hoàn tất trong 158,520 giây và còn ba mục logo cần xem.
- Giao diện review hiển thị đề xuất `BLUR/CUT`, cho nhận tất cả đề xuất theo bộ lọc, và có một nút `Hoàn tất duyệt và xuất video`. Nút này tạo plan, cấp quyền từ queue đã giải quyết, render nền và báo trạng thái ngay trên trang.
- Thêm khóa tài nguyên liên process: nhiều session có thể xếp hàng công việc GPU, gồm cả Florence qua `run.ps1 localize-visual-logo`, và final render mà không tranh VRAM hoặc ghi đè output.
- Final render giải mã toàn bộ file trước khi công nhận hoàn tất, tiếp tục giữ giới hạn cứng 3,5 GB và kiểm tra checksum nguồn.
- Tạo `annotations/review-regression-v1.json` từ sáu video đã review: 206 ví dụ gồm 14 BLUR, 29 CUT và 163 KEEP. Việc di chuyển sáu output cũ không ảnh hưởng dataset vì nguồn vẫn còn trong `input`.
- Sửa regression khiến các candidate logo kề nhau nhưng khác loại/hành động bị gộp mất đề xuất. Hàng kiểm chứng nay có đúng 8 mục, gồm persistent logo đề xuất BLUR và end-card đề xuất CUT.
- Bộ kiểm tra tự động đạt 83/83.

## 0.3.4 - 2026-09-24

- Người dùng duyệt đủ ba preview Tập 2; xuất file mới `output/tieng-yeu-tap-2-reviewed-v1.mp4` 577.014.939 byte, 3791,500 giây, 1 H.264 + 1 AAC. Giải mã toàn bộ không lỗi, checksum nguồn không đổi, đã đối chiếu frame đầu/vùng BLUR/cuối và lưu `final-qa-notes.md`.
- Tập 2: người dùng chốt CUT liên tục 3801,5 giây tới hết; queue giữ biên phát hiện gốc và edit plan gộp outro thành một operation. Đã dựng đủ ba preview (CUT intro, BLUR banner, CUT outro), kiểm tra frame/luồng/giải mã và checksum nguồn. Chờ duyệt preview trước khi xuất full.
- Mở job độc lập cho Tập 2: quét OCR/semantic, logo exhaustive, 18+, gore và violence bằng model local đã duyệt; queue 45 mục còn chờ người dùng, chưa có edit plan hoặc output.
- Queue tự gợi ý `vertical_only` khi hợp bounding box OCR của banner phủ kín chiều ngang nguồn, kể cả khi người dùng chọn BLUR trong giao diện; phép đo vùng vẫn theo từng video.
- Thêm audit 10 khoảng hở trong chuỗi logo/credit Tập 2 bằng ảnh exhaustive để hỗ trợ kiểm tra CUT liên tục; không có CUT tự động.
- Khi tạo edit plan, các CUT chồng nhau hoặc liền kề được gộp thành một operation preview; plan vẫn lưu ID candidate và biên phát hiện gốc của từng mục. Bộ kiểm tra hiện đạt 70/70.

- Sửa regression kế thừa quyết định: queue V7 lưu cả biên candidate gốc và biên người dùng điều chỉnh, thay vì làm mất các đoạn CUT liên tục khi tái tạo plan từ báo cáo mới.
- Khôi phục CUT intro liên tục `0–10,5s`; không còn khoảng hở 5–6 giây làm sót `A NETFLIX SERIES`.
- Khôi phục CUT cuối liên tục `3569,5–3722,175s`; loại toàn bộ Imaginus, Netflix và logo sau phần credit thay vì để hở giữa các candidate rời rạc.
- Vùng blur chữ dùng trim đáy thích ứng 10% của hộp OCR: banner này từ 80 px còn 72 px, giữ nguyên mép trên và feather 3 px.
- Thêm `vertical_only` cho banner chạy hết chiều ngang: blur phủ kín mép trái/phải và chỉ feather trên/dưới, tránh nhấp nháy giữa chữ thật và blur khi chữ vào/ra khung.
- Xuất V7 thành công: 629.702.558 byte, dài 3.554,012 giây, 1 luồng hình + 1 luồng tiếng, giải mã toàn bộ không lỗi, dưới 3,5 GB và checksum nguồn không đổi.
- Tăng bộ kiểm tra tự động lên 68 bài.

## 0.3.3 - 2026-09-24

- Hoàn tất review queue brand/logo V6: 70/70 mục đã quyết định thành 59 `KEEP`, 10 `CUT` và 1 `BLUR`; không còn mục chờ hoặc cần thêm ngữ cảnh.
- Thay dải blur cao cố định bằng hợp các bounding box OCR của chính video. Chiều cao, vị trí và feather được tính theo từng vùng chữ rồi khóa vào edit plan của video; không tái sử dụng tọa độ giữa các video.
- Vùng banner thực tế được tính thành `x=0, y=48, width=1280, height=80`, thấp hơn dải 105 px cũ. Feather 3 px che kín viền chữ nhưng vẫn giữ vùng tác động gọn.
- Sửa lỗi nhánh mặt nạ feather khiến FFmpeg có thể thương lượng định dạng xám và làm mất màu toàn khung; nhánh overlay nay được khóa ở `yuv420p`.
- Thêm preview theo operation bằng `render-previews --operation-id`. Phê duyệt mẫu chỉ mở khóa render khi dùng rõ `approve-previews --allow-sampled`; mặc định vẫn yêu cầu đủ mọi preview.
- Xuất V6 thành công: 631.425.474 byte, dài 3.573,031 giây, có hình và tiếng, dưới 3,5 GB, giải mã toàn bộ không lỗi và checksum nguồn không đổi.
- Tăng bộ kiểm tra tự động lên 67 bài; `pip check` sạch và license audit có 8 model được phép, 0 model bị chặn.

## 0.3.2 - 2026-09-23

- Thêm chế độ `scan-visual-logo --exhaustive`: quét đủ mọi cửa sổ 5 giây thay vì giới hạn 80 candidate, vẫn chạy local và không tự chỉnh video.
- Full scan video 62 phút kiểm tra 745/745 cửa sổ trong 326,592 giây; giữ 134 cửa sổ theo hướng ưu tiên recall và gộp với OCR thành queue 73 mục chờ duyệt.
- Lưu ảnh audit cho cả 745 cửa sổ và tạo `audit.html` có bộ lọc Confirmed/Uncertain/Rejected; 611 cửa sổ Qwen loại vẫn có bằng chứng để kiểm tra false negative mà không xuất lại video.
- V6 tăng mật độ toàn timeline từ 1 frame/2 giây lên 2 frame/giây: 7.563 frame, 745/745 cửa sổ, 395,023 giây. Prompt chặt giữ 27 cửa sổ nhưng bỏ sót chữ Netflix mà OCR đã bắt, nên kết quả cuối lấy hợp OCR + V5 rộng + V6 chặt và không dùng Qwen làm cổng duy nhất.
- Queue hợp nhất V6 còn 70 mục chưa duyệt (56 visual, 14 text), 69 mục có vùng gợi ý. Loại câu hướng dẫn Qwen lặp lại khỏi tên thương hiệu; mọi cửa sổ V6 vẫn có ảnh audit.
- Thêm Florence-2-base MIT làm tầng khoanh vùng sau xác nhận ngữ nghĩa. Benchmark 7 ảnh bắt 4/4 mẫu dương gồm logo N; không dùng Florence làm classifier vì phrase grounding báo vùng trên 3/3 cảnh âm.
- Khoanh vùng 134 candidate thực tế trong 397,663 giây; 130 mục có ít nhất một vùng, mọi vùng chỉ là gợi ý và vẫn cần người dùng chọn KEEP/BLUR/CUT.
- Thêm vùng Florence vào queue review để BLUR có thể dùng tọa độ nguồn; không tự duyệt hoặc tự áp dụng vùng.
- Thử Phi-3.5 Vision INT4 MIT trên 9 ảnh: recall 100% nhưng specificity 0%, 25,977 giây/ảnh. Model và runtime khoảng 2,8 GB đã bị xóa, giữ benchmark và khóa tải lại theo quality gate.
- Xác nhận Qwen2-VL-2B chưa phân biệt ổn định branding ngoài phim với bảng hiệu nằm trong cảnh; nhánh logo hiện là hệ thống báo cáo recall-first có human review, chưa phải bộ tự động xóa logo.

## 0.3.1 - 2026-09-23

- Thêm lệnh `scan-visual-logo` cho brand ident, logo và watermark không có chữ bằng candidate hình học/temporal kết hợp Qwen2-VL local.
- Quét dày 30 giây đầu/cuối, quét thưa toàn phim và phân bổ tối đa 80 cửa sổ đồng đều theo từng đoạn 5 phút để tránh thiên lệch timeline.
- Regression 16 giây bắt đúng logo N và `A NETFLIX SERIES` mà prompt không chứa tên Netflix, đồng thời loại hai cửa sổ cảnh phim bình thường.
- Full scan 62 phút xử lý 1.980 frame và 80 cửa sổ trong 99,297 giây; giữ logo intro, logo/nhãn trong cảnh cần review và logo credit cuối phim.
- Mọi kết quả vẫn cần xác nhận; các candidate đã nằm hoàn toàn trong vùng CUT có thể kế thừa quyết định cũ, candidate mới không được tự sửa.
- Tăng bộ kiểm tra tự động lên 48 bài.

## 0.3.0 - 2026-09-23

- Thêm lớp xác nhận violence thứ hai bằng Qwen2-VL-2B Apache-2.0, chạy local trên năm frame theo thứ tự thời gian và không tự chỉnh sửa video.
- Benchmark nhỏ 11 clip có nhãn rõ đạt recall 100%, specificity 85,71% và balanced accuracy 92,86%; giữ một false positive để người dùng quyết định.
- Quét video thực tế 62 phút: ViT tạo 167 interval, Qwen loại 166 và giữ 1 interval 52:27,875–52:35,875 để review trong 192,943 giây; VRAM đỉnh khoảng 4,62 GB.
- Tạo queue V2 gồm 12 mục thay cho 38 mục cũ: 4 text/logo có chữ, 7 adult, 1 violence và 0 gore; chưa tạo edit plan hay output.
- Sau review, bổ sung logo N thuần hình ảnh của Netflix tại 00:01,0–00:05,0; mở rộng thao tác cắt intro liên tục thành 00:00–00:10,5 và tạo lại queue/edit plan/preview V3.
- Xóa SmolVLM thử nghiệm không đạt cùng file tạm, thu hồi hơn 2,03 GB; giữ model, benchmark nén và báo cáo hợp lệ trên ổ E.
- Tăng bộ kiểm tra tự động lên 43 bài.

## 0.2.9 - 2026-09-23

- Thay model VideoMAE violence CC-BY-NC bằng ViT Apache-2.0 đã ghim revision/checksum và chạy hoàn toàn local.
- Thêm backend cửa sổ 16 frame, lấy trung bình 5 frame mạnh nhất để giảm nhiễu từ một frame đơn lẻ.
- Benchmark tích hợp 60 clip đạt recall 95%, specificity 72,5% và balanced accuracy 83,75%; riêng phim người thật đạt recall 90% và balanced accuracy 80%.
- Smoke scan clip 2,208 giây chạy trong 0,446 giây, dùng khoảng 478 MB VRAM và tạo đúng interval review; không tự edit.
- Loại Aleris vì balanced accuracy chỉ 65%; xóa Aleris, VideoMAE cũ và ONNX Runtime thử nghiệm sau khi giữ báo cáo.
- Ghi rõ quảng cáo/logo có chữ đã hoạt động trên video thực tế; logo thuần hình ảnh chưa hoàn tất.
- Tăng bộ kiểm tra tự động lên 40 bài.

## 0.2.8 - 2026-09-23

- Thêm policy `free-commercial-safe-local`: không API trả phí/theo lượt, không upload media ra AI ngoài, chỉ chạy model local có nguồn, revision và giấy phép được duyệt.
- Thêm lệnh `license-audit` và chặn model lạ/thiếu manifest trước mọi scan hoặc benchmark.
- Ghi metadata giấy phép, nguồn, chi phí và trạng thái dữ liệu huấn luyện vào toàn bộ manifest model hiện có.
- Khóa VideoMAE XD violence vì giấy phép CC-BY-NC-4.0; giữ file/báo cáo cũ cho audit nhưng cấm lượt production mới.
- Vô hiệu hóa script tải lại ảnh animation từ nguồn artwork không có quyền tái sử dụng rõ theo từng file.
- Thêm thông báo third-party và quy định xác nhận quyền với video/nhạc/phụ đề trước khi upload.
- Tăng bộ kiểm tra tự động lên 37 bài.

## 0.2.7 - 2026-09-23

- Thay quyết định OCR dựa trên danh sách từ khóa bằng ensemble local gồm nhận dạng chữ, tính liên tục của nội dung, vị trí, độ bền theo thời gian và classifier embedding đa ngôn ngữ.
- Thêm model `multilingual-MiniLMv2-L6-mnli-xnli` đã ghim revision/checksum, khoảng 450,2 MB trên ổ E; sau tải chạy offline và không gửi chữ/video ra ngoài.
- Thêm tập seed có phiên bản và đầu phân loại tuyến tính có thể học tiếp từ dữ liệu review; holdout khởi đầu nhỏ đạt 14/16, chỉ dùng làm kiểm tra kỹ thuật chứ chưa phải số đo production.
- Chỉ nối hai OCR track khi cả vị trí lẫn nội dung chữ liên tục, tránh ghép nhiều câu phụ đề khác nhau thành một banner kéo dài.
- Tách `advertisement`, `subtitle`, `credits` và `scene_text`; giữ các trường hợp chưa chắc chắn trong review và không tự blur/xóa.
- Chuyển quy tắc Netflix thành policy người dùng minh bạch trong `config/text_review_policy.json`; policy chỉ ép review, không tự tạo edit.
- Quét lại video thực tế: 1.241 frame trong 369,466 giây, tốc độ 10,074× thời gian thực, RAM đỉnh 1,28 GB. V7 giữ đủ Netflix/NGUONC và còn 28 track review trước khi gộp theo thời gian.
- Loại ký tự OCR rỗng/đơn lẻ, nhận biết credit roll cuối phim và chữ nhỏ trong cảnh để giảm false positive; thêm progress log và luôn giữ ảnh cho candidate ở các lượt quét mới.
- Tăng bộ kiểm tra tự động lên 34 bài.

## 0.2.6 - 2026-09-22

- Sửa đọc metadata video có tên tiếng Việt trên Windows bằng UTF-8.
- Ánh xạ báo cáo kỹ thuật `nsfw` thành nhóm `adult` để nút lọc 18+ trong giao diện hoạt động đúng.
- Bổ sung từ khóa quảng cáo Việt thường gặp và tên miền `.ai` cho OCR.
- Giữ lại dòng chữ dài ở mép trên từ ngưỡng tin cậy 0,10 trong khi phụ đề và chữ thông thường vẫn dùng ngưỡng 0,35.
- Xác nhận OCR V2 bắt đúng `NETFLIX SERIES` ở 00:06–00:10 và banner `NGUONC.COM` ở 03:33–04:02; thêm đệm thời gian cùng vùng che trước khi đưa vào review.
- Áp dụng quy tắc người dùng: mọi chữ/logo Netflix ở phần cuối cũng được coi là quảng cáo; thêm dòng bản quyền cuộn 59:29,5–59:45,5 và logo `NETFLIX | DUBBING` 59:50,5–hết video vào review.
- Quét video người thật đầu tiên dài 62 phút qua bốn nhánh adult, gore, violence và text; giữ bước xác nhận trước mọi chỉnh sửa.

## 0.2.5 - 2026-09-22

- Thêm bước phê duyệt preview riêng trước khi mở quyền dựng video hoàn chỉnh.
- Thêm bộ dựng cuối có CUT, BLUR, giữ âm thanh 5.1 và kiểm tra checksum nguồn trước/sau khi dựng.
- Đặt mục tiêu dung lượng 3,3 GB và giới hạn cứng 3,5 GB cho mọi bản xuất; dựng qua file tạm và chỉ công nhận khi vượt đủ kiểm tra.
- Xuất bản thử Sintel dài 886,064 giây, dung lượng 173.784.506 byte trong 53,364 giây; giải mã toàn bộ không lỗi và nguồn không đổi.
- Tăng bộ kiểm tra tự động lên 25 bài.

## 0.2.4 - 2026-09-22

- Thêm bulk KEEP theo bộ lọc có xác nhận và cấm bulk BLUR/CUT.
- Hiển thị dung lượng nguồn/report, ổ E còn trống, ước tính preview và mức tải máy ngay trong giao diện review.
- Thêm actor/transport cùng audit log giới hạn làm nền cho review từ xa.
- Chốt thiết kế Telegram long polling với allowlist và thumbnail opt-in; chưa truyền dữ liệu ra ngoài.
- Hoàn tất queue Sintel thử nghiệm với 37 KEEP, 1 CUT và 1 BLUR; tạo hai preview có audio trong khi giữ nguyên checksum nguồn và khóa full export.

## 0.2.3 - 2026-09-22

- Thêm review workflow hợp nhất report, ghi quyết định người dùng và khóa edit plan khi còn candidate chưa giải quyết.
- Thêm preview ngắn cho BLUR/CUT có âm thanh, xác minh checksum nguồn không đổi và giữ `final_export_allowed: false`.
- Tạo queue Sintel thực tế gồm 39 mục sau khi gộp candidate violence trùng giữa hai model.
- Sửa lệnh scan NSFW dùng đúng tập tham số và sửa ánh xạ video cho preview blur toàn khung.
- Thêm giao diện review tương tác chạy trên localhost, lưu quyết định ngay và hỗ trợ lọc candidate cùng hoàn tác lựa chọn.

## 0.2.2 - 2026-09-21

- Thay benchmark violence anime cũ bằng 126 mẫu direct-violence từ hai video, gồm 59 positive và 67 negative.
- Chọn ensemble VideoMAE temporal ngưỡng 0,61 với WD direct-action ngưỡng 0,20; đạt recall 86,44%, precision 80,95% và balanced accuracy 84,27%.
- Holdout Sintel đạt recall 85,71% và balanced accuracy 84,52%.
- Quét toàn bộ Sintel dài 14 phút 48 giây trong 71,745 giây bằng VideoMAE, bắt cả hai cụm giao chiến và gom còn 20 interval review.
- Thêm mức ưu tiên high/context, khoảng gom review 8 giây và routing model video đúng cho lệnh violence animation.
- Thêm công cụ tạo storyboard, benchmark Sintel/Conan và báo cáo ensemble tái lập được; không tạo full output.

## 0.2.0 - 2026-09-20

- Thêm pipeline OCR để phát hiện và theo dõi vùng chữ qua thời gian.
- Phân vùng phụ đề, trung tâm, cạnh và góc để ưu tiên review quảng cáo cố định.
- Thêm báo cáo JSON/HTML và preview có khung đánh dấu; chưa tự blur khi chưa được duyệt.
- Giới hạn số preview trong báo cáo và nén frame tạm trong RAM để tránh phình dữ liệu khi quét phim dài.
- Thêm EasyOCR và model Việt/Anh vào môi trường project trên ổ E.
- Xác nhận OCR phát hiện banner quảng cáo tiếng Việt trong video Conan và tạo preview blur 30 giây.
- Tối ưu blur bằng crop cục bộ kết hợp alpha feather; giữ nguyên màu và dọn các preview thử không đạt.
- Căn chính xác thời gian banner ở 00:03:06–00:03:31 để blur không xuất hiện trên các frame sạch.
- Thêm xác nhận endpoint bằng look-ahead 3 giây ở mật độ 0,5 giây/frame; tắt blur tại frame vắng banner đầu tiên sau khi xác nhận nội dung không quay lại.
- Thêm đệm 0,5 giây sau frame sạch đầu tiên để bù sai số timestamp/trình phát; xác minh V8 tại frame 0:28 và 0:29,5.
- Hoàn tất OCR toàn bộ video Conan 99 phút và loại các track chữ thuộc nội dung phim.
- Hoàn tất full-film NSFW scan; review 23 interval và xác định không có cảnh 18+ thật trong các candidate.
- Thêm scanner benchmark gore/violence với model local, top-candidate report và bảo vệ video nguồn.
- Loại hai baseline gore/violence vì cùng bỏ sót cảnh máu thật tại 01:01:39; xóa khoảng 388 MB trọng số thử nghiệm sau khi giữ báo cáo/checksum.
- Tạo `reports/conan-edit-plan.json` và hai clip review ngắn cho các cảnh nhạy cảm còn cần quyết định.
- Ghi nhận người dùng xác nhận cả hai cảnh nhạy cảm là false positive; giữ nguyên nội dung và xóa hai clip review.
- Hủy bước full export trong giai đoạn detection; chuyển gate sang benchmark có ground truth riêng cho 18+, gore và violence.
- Tách detection thành sáu nhánh theo nội dung và phong cách `live_action`/`animation`.
- Thêm policy bắt buộc người dùng xác nhận `KEEP`, `BLUR`, `CUT` hoặc `NEEDS_MORE_CONTEXT` trước mọi chỉnh sửa.
- Thêm schema CSV cho tập benchmark và quy định không full export trong giai đoạn thử model.

## 0.1.0 - 2026-09-20

- Khởi tạo cấu trúc Phase 1A.
- Thêm scanner NSFW baseline, báo cáo HTML/JSON và storage dry-run.
- Thêm chính sách giữ toàn bộ runtime, model và cache trên ổ E.
- Xác minh PyTorch CUDA trên RTX 2060 và smoke test scanner end-to-end.
- Ghi nhận FFmpeg 9.0.1 không tương thích NVENC API của driver 576.80; CPU x265 hoạt động.
- Thêm score distribution và top-20 candidate frames cho blind review video chưa có annotation.
# 0.2.1 - 2026-09-20

- Added a reproducible image benchmark command with per-style precision, recall, specificity, speed, RAM/VRAM and bounded error thumbnails.
- Added a 42-sample adult-content POC manifest with checksums and source/license metadata.
- Selected the 16.3 MB multi-class NSFW nano model as the experimental baseline at a 0.95 candidate threshold.
- Added a 3-of-5 temporal confirmation rule for animation and retained mandatory user confirmation before edits.
- Rejected the Falconsai baseline after it flagged all 22 hard negatives.
- Changed the current milestone to coverage-first: reach 80–85% for adult, blood/gore and violence before deeper optimization from real-video errors.
- Added a 60-sample blood/gore POC with 20 positives and 40 hard negatives split evenly across live action and animation.
- Selected the 45.3 MB NSFL classifier for live action (90% balanced accuracy) and the 378.7 MB anime tagger for animation (85% balanced accuracy).
- Added 23-tag probabilistic gore scoring plus 3-of-5 temporal confirmation for animation video scans.
- Verified the animation gore path on a 35-second clip: 70 frames in 4.532 seconds and zero confirmed false intervals.
- Added a 60-clip violence POC with 10 positives and 20 hard negatives per content style.
- Selected the 87.6 MB VideoMAE-small model for live action at 80% recall and 87.5% balanced accuracy.
- Reused the pinned anime tagger for animation violence at 80% recall and 87.5% balanced accuracy, with 3-of-5 temporal confirmation.
- Rejected and deleted the 345 MB surveillance-oriented VideoMAE after retaining its report and checksums.
- Deleted the 372 MB temporary Tears of Steel source and review contact sheets after producing the bounded 30.3 MB clip benchmark.
- Completed 2 fps field scans for adult, blood/gore and violence across the full 99-minute Conan video without exporting or editing the source.
- Measured field review load at 7.85, 41.64 and 74.83 intervals per hour respectively; identified fire dominance in violence and excessive low-score gore candidates.
- Recorded the next optimization pass: one shared anime tagger inference, danger/violence separation, two-tier gore filtering and one retained thumbnail per interval.
- Added a combined anime safety scan that produces gore and direct-violence reports from one WD tagger pass.
- Separated fire/explosion danger telemetry from direct violence triggers.
- Added two-tier gore filtering with stricter 4-of-5 temporal confirmation for weak contextual hits.
- Reduced Conan field review load to 19.92 gore and 11.47 violence intervals per hour while retaining the strongest injury candidate.
- Compacted reports to one strongest thumbnail per interval plus bounded top candidates, reducing the two anime reports from about 13.17 MB to under 1 MB.
- Marked the legacy anime violence POC as label-mismatched because its positives are injury/fire rather than confirmed direct violence.
- Added 13 verified adult-anime field false positives to a checksum-backed regression manifest.
- Replaced the anime adult nano path with explicit WD tags in the shared inference pass.
- Added adult label co-occurrence gating; the 43-sample anime regression reached 100% recall and 98.48% balanced accuracy.
- Added high/context priority tiers for direct anime violence; the provisional high-priority precision audit reached 87.5%.
- Completed V4 full-field shared inference in 754.749 seconds with adult, gore and violence reports from one model pass.
