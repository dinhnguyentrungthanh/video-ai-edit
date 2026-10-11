# Batch 4a progress (platform-logo detection + memory)

## Milestone 1 — platform names + text-scan keep (done)
- New `src/biliflow/platform_names.py` (stdlib): `PLATFORMS`, `EXCLUDED`, `match_platform_text`,
  `match_platform_texts`. Latin candidates = tokens, whitespace chunks without punctuation (so
  `v.qq.com` matches), adjacent token pairs.
- `textscan.py`: `_annotate_platform_names` (after overlay promotion; non-semantic filter keeps
  platform tracks) and `_limit_report_tracks` (candidates + platform + earliest others).
- Tests: tests/test_platform_logos.py (3 name tests), tests/test_textscan.py
  `test_platform_tracks_survive_report_truncation` → 15 tests OK.
- Read-only check over the 58 real text reports (12 888 tracks): 29 matches, all iQIYI idents
  (`iOly '`×14, `iOlYI`×10, `iOIYI`×3, `iQIYI`×2), no other platform, no false positive.

## Milestone 2 — A1 forced-card protection (done)
- review_workflow.py: `FORCED_BOUNDARY_TYPES`, `GATE_PROTECTED_TYPES` (+ platform_logo),
  `FORCED_BOUNDARY_STUDIO_MOVE = True`; `_scan_items` treats forced cards as whole-scene (boxes →
  `evidence_regions`, suggestion None); quarantine, revalidation, title/in-film guards and the
  reconcile support set skip protected types; R3b route keeps moving fully matched forced cards.
- Tests: jobs 51/60 regression, gate test (test_review_workflow), A1 studio test (test_scene_cards).

## Milestone 3 — probe, platform cards, ending card, links (done)
- New `platform_logos.py` (hits, SourceProbe decode, ident_span, logo_region, memory runs; 729 lines)
  and `platform_cards.py` (build_platform_cards, ensure_forced_ending_card, link_platform_cards).
- build_review_queue: platform + ending cards after the quarantine; links after the final ids;
  `_fixed_text_ad_evidence` gains "tên nền tảng video: <name>"; platform cards own evidence boxes.
- Read-only probe of the Tập 17 export: opening [8.00, 13.00] region (470,179,317,168);
  ending [2699.68, 2703.68] region (374,203,541,108); decode 0.2–0.3 s per 16 s window.
- Tests: test_platform_logos 16 (incl. 2 with a generated FFmpeg clip: span 3.00–6.52 from frames,
  previews pass the studio window self-check), ending-card tests, overlay evidence, cache scope.

## Milestone 4 — platform memory, convert + seed (done)
- New `platform_memory.py`: `prepare_platform_logo_frames`, `remember_platform_logo` (same store,
  `memory_class:"platform_logo"`, BLUR, `platform`, `blur_region`, `logo_frame_times`),
  `convert_logo_memory_class` (dry run default; backup + byte check before write) and
  `seed_platform_logo` (`seeded_from{path,sha256,start,end}`).
- `record_review_decision(..., remember_platform_logo=True)` (BLUR only; refuses without logo frames
  and writes nothing); any other decision forgets the record (either class).
- New `scripts/platform_logo_convert.py` (`convert --key … --platform`, `seed --video --start --end
  --platform`, `--apply` opt-in).
- Tests: tests/test_platform_memory.py 12 (3 real-data read-only, skip without data) + 2 in
  test_scene_cards → OK.
- Real-root dry runs (evidence e2e-4a/dry-runs/): memory sha256 5ab0fdfd… and the
  frames/backups listing hash unchanged before/after. Convert: 3 iQIYI records convertible
  (62–65 logo frames, box ≈ 478,179,313,168 @1280×534), licence refused `no_logo_frames`.
  Seed on the Tập 17 export 2699.68–2703.68: seedable, 100 decoded / 89 stored / 79 logo frames,
  box 374,203,541,108 (= the measured ending region). Nothing applied.

## Milestone 5 — review page (done)
- `_interactive_html`: names (`Logo nền tảng <tên>`, `Kiểm tra đoạn kết`, KIND_NAMES), ending card in
  the shared player, `endingVerdictHtml` (6 s wording = `ENDING_REASON` + tail Qwen answers),
  `platformVerdictHtml` (OCR / remembered-logo line), link line for whole-scene cards and `covered_by`
  advisory duplicates, button «Đây là logo nền tảng — làm mờ & nhớ» (`data-act="platform"`, posts
  `remember_platform_logo:true`, no region needed), undo/clear carry the flag, export lock covers it.
- A3: `source_archived` locks the page like `source_cleaned` (decisionsLocked, refuse alert, renderExport
  status/summary/foot titles) and `reviewDoneHint()` says “Video gốc đang ở kho lưu trữ; bấm “Khôi phục
  bản xuất” trên Dashboard để xuất lại.”
- Tests: test_opening_card_display +2, test_scene_cards +1, test_review_workflow +1 (A3) and 4 exact-string
  assertions updated for the archived lock. 14/14 page mutations killed. 198 tests OK in the 4 page modules.

## Milestone 6 — "Bộ nhớ logo" page/API + hooks (done)
- New `src/biliflow/logo_memory_admin.py`: `list_logo_memory`, `logo_memory_frame` (only JPEGs inside
  state/studio-logo-frames; 403 otherwise), `set_logo_memory_class`, `delete_logo_memory` (memory backup +
  frames folder MOVED to state/backups/studio-logo-frames-<ts>/), all writes under REVIEW_QUEUE_IO with
  expected_sha256 (409 when stale); `handle_get` / `handle_post` dispatch; inline page (`/logo-memory`).
- platform_memory: `memory_snapshot` / `memory_unchanged` public, `convert_logo_memory_class(...,
  expected_sha256=)`, `MEMORY_CHANGED_MESSAGE`.
- Tests: tests/test_logo_memory_admin.py 9 (8 synthetic + 1 real read-only listing; sha 5ab0fdfd… unchanged).
- Hooks for the lead: temp/ui-plan/batch4/hooks-4a.md (import, do_GET elif, do_POST elif, decision flag,
  header link, HTTP test sketch).

## Milestone 7 — e2e A5 on a copy of the Tập 17 export (done)
- New `scripts/platform_logo_e2e.py` (prepare / run / check / rebuild / seed-ending / cleanup) + tests/test_platform_logo_e2e.py (3).
  Temp root T = temp/platform-logo-e2e-20261003-151459/root (copy of the export, sha b02de6a3…, D = 2703.778;
  memory COPY with the 3 iQIYI records converted; junctions models / tools / runtime/florence-python).
  Port 8765 free and no other BiliFlow GPU process before `run`; GPU mutex taken per GPU stage.
- Stages: text 99.6 s, visual_logo 106.4 s (cold logo routing), localize 23.6 s, augment 11.3 s, build-review
  11.8 s → 252.7 s. Platform probe: 4 decodes / 1180 frames with memory, 2 / 868 without.
- Main list (seeded run, after the fix): iQIYI [8.00, 13.00] BLUR (469,179,318,168) 7.8 %, snap dark_run,
  OCR + 4 memory runs; iQIYI [2699.68, 2703.68] BLUR (373,203,542,108) 8.6 %, OCR + seeded run;
  ending_boundary [2697.778, D] KEEP linked to the outro card (coverage 0.667). Advisory: opening_promotion
  [0, 5] KEEP (moved by the licence studio record, A1), rejected window [10, 15], uncorroborated region
  [2700, D] covered_by the outro card. No-memory run: same two platform cards (OCR only) and the 0–5 s card
  stays in the main list with no suggestion. 12/12 checks PASS in memory-copy, no-memory and seeded-ending
  (evidence e2e-4a/<scenario>/check-*.json + e2e-meta.json).
- Found + fixed: the seeded OUTRO record matched one opening frame (11.4–11.6 s, 0.969) and its box was unioned
  into the opening region (13.3 %). `_platform_card` now uses remembered boxes only when no logo pixels were
  measured (regression tests `PlatformCardRegionTests`, 2). Seeded rebuild after the fix: opening back to
  7.8 % `logo_pixels`. (memory-copy evidence predates the fix: 322 px wide, `logo_pixels+platform_memory`.)
- Production fingerprints (studio memory 5ab0fdfd…, brand memory, Tập 17 queue, export, models/tools/runtime
  listings) unchanged in every check. Cleanup removed the junctions first, then T; production folders intact.

## Milestone 8 — code review fixes (done)
Review (ecc:python-reviewer): no CRITICAL, 1 HIGH, 6 MEDIUM, LOWs. Each fix has a test that failed first.
- HIGH `_groups`: two remembered-logo runs with no OCR reading crashed build-review (`min()` of an empty list).
  Group spans now come from readings and runs; runs are sorted. Tests: `PlatformGroupTests` (2).
- `platform_names`: "b站" excluded every text with the letter b (Latin form "b"); excluded Latin forms now need
  4+ letters. Also: bare "芒果" (the fruit) no longer means Mango TV; "v.qq.com" matches exactly only ("ZOO COM").
  Read-only recheck of 212 text + 237 visual real reports: 29 + 6 hits, none changed.
- Review page: `endingVerdictHtml` read `w.start`/`w.end`; the card has `start_seconds`/`end_seconds` (fixture fixed,
  plus a test that renders a card built by `ensure_forced_ending_card`).
- "Bộ nhớ logo": reads take REVIEW_QUEUE_IO (memory read; per record in the listing; frame reads); delete writes
  the memory first, then moves the frames folder in one `os.rename` (in use → 200 + `frames_left` + `warning`, no
  empty backup folder); `FrameOutsideMemory` (403) instead of reusing PermissionError (a locked file is now 500);
  any other error → JSON 500. `load_frame_image` catches `cv2.error` (empty JPEG).
- `platform_memory`: frames sorted by time only (two frames at one time crashed); repeated `--key` converts once;
  memory re-checked after the backup, before the write. Convert script: exit 1 when nothing is convertible or the
  memory is missing, exit 2 + JSON error for a missing file.
- `_memory_windows`: 30–60 s videos are scanned whole (the tail was skipped). `_decodable`: at 50/60 fps the
  probe window shrinks around the readings so the 1200-frame decode cap never cuts its end (25/30 fps unchanged).
- e2e script: cleanup needs `e2e-meta.json` and normalises the path; junctions via `_winapi.CreateJunction`.
- Removed unused `relative_regions`. Not changed: a negative VLM answer naming a platform still counts (it names
  the ident; without logo pixels or an OCR box the card is skipped as `no_region`).
- Focused: 291 tests OK (1 pre-existing real-data skip). Full suite: 1106 tests OK, skipped=25.

## Milestone 9 — security review fixes M1 + L6 (done)
- M1 `platform_memory`: `remember_platform_logo` = snapshot → `backup_memory` → re-check → new frames into a
  fresh folder (`in_fresh_folder`: own name, else -2, -3…) → re-check + write memory (new frames removed again on
  a change or failure) → replaced record's frames moved with `move_frames_to_backups` (one `os.rename`; was
  `logo_memory_admin._move_frames`, now shared). New `forget_remembered_logo` (either class): backup → re-check →
  write → move frames; no-op without a record. `MemoryChanged` on a concurrent change, nothing written. Seeds use
  the fresh-folder writer too. No rmtree left in platform_memory / logo_memory_admin.
- `review_workflow`: both remember actions run before the decision is saved, retried up to 3× on a fresh read
  (`MemoryChanged` → ValueError "Bộ nhớ logo vừa thay đổi…", queue untouched); "giữ & nhớ" first forgets the
  card's old record safely and writes brand_memory's frames into a fresh folder, so `remember_studio_logo` never
  deletes; other decisions and clear use `_forget_remembered_logo_quietly` (same 3 retries, never fails the
  decision). brand_memory.py untouched; item flags unchanged.
- L6 e2e cleanup: base must be directly inside `<install>/temp` lexically and resolved, neither it nor temp a
  reparse point, e2e-meta.json naming that install; `--production-root` (default: the install of this code,
  a worktree's main tree) on cleanup and prepare.
- Tests: test_platform_memory +4 (`SafeRememberForgetTests`), test_scene_cards +2 and 1 extended, test_platform_logo_e2e
  +2 (guard, install root) and helpers. Focused + test_control_center + test_cache_dependencies: 348 run, only the
  other agent's new `test_every_response_forbids_framing_by_another_site` fails (X-Frame-Options not implemented
  yet). Full suite: 1128 run, failures=22, errors=3, skipped=25 — all in test_source_archive (11),
  test_recycle_bin (4), test_source_cleanup (1), test_control_center framing (9 subtests), the other agent's
  in-progress files. Real memory sha 5ab0fdfd… unchanged.
