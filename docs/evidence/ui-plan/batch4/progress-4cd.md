# Batch 4c/4d progress (agent "4c+4d", worktree wt-batch4)

## 2026-10-03 — milestone 4c done (focused tests green)
- recycle_bin: `VERIFY_RETRY_DELAYS`, patchable `_sleep`, `find_recycle_record_patiently`; `_conclude` uses it.
- job_store: `jobs.hidden_at` (cleared by every state writer when the job leaves CANCELLED), `recycle_checks`
  (append-only) and `source_archives` tables, one `source-archive` backup in the elif chain, archive store
  methods, `source_lock`, `set_job_hidden`, `add_recycle_check`, `recycle_check_summary`.
- export_guards: `ActionConflict`, archived messages; scheduler: `_refuse_locked`, cancel guard (409
  `already_cancelled` / `not_cancellable`, conditional write, no duplicate event). L1 kept (no reopen).
- source_cleanup: `SOURCE_FILE_LOCK`, `CleanupConflict(ActionConflict)`, `precheck` public, archive-aware
  `assess_job`, `check_fields`, `recheck_recycle_record` (lowercase kinds `source_cleanup`/`archive_export`).
- control_center: `/api/jobs/{id}/hide|unhide`, `/api/source-recycle-check`, 409 for every `ActionConflict`;
  dashboard: scroll-to-list on tab click, `cancelJob` (L3 confirm, one POST), folds "Đã hủy"/"Đã ẩn"
  (closed, open state kept across polls), hidden rows with "Hiện lại", "Kiểm tra lại Thùng rác".
- Tests: test_recycle_bin (+4), test_job_store (+6, live read-only copy extended), test_scheduler (+3),
  test_source_cleanup (+2), test_control_center (+3: markup, `scroll` and `cancelled` node scenarios).
- Focused runs: test_control_center 44 OK; recycle_bin+job_store+scheduler+source_cleanup(+http)+
  export_guards+skip_export+job_import 258 OK (skipped=2).

## 2026-10-03 — milestone 4d done (focused tests green; full suite next)
- New `source_archive.py` (eligibility, preview, execute; 719 lines) + `source_archive_files.py` (manifest
  snapshot, no-overwrite rename/write) + `source_archive_restore.py` (restore, startup reconcile). Split so
  `source_archive.py` stays under 800 lines.
- recycle_bin: `send_export_to_recycle_bin` (output/ only, `-reviewed.mp4` + manifest names only) with
  export wording; export_guards/job_import/scheduler/control_center honour `source_lock` 'archived'.
- control_center: `/api/source-archive/preview`, `/api/source-archive`, `/api/source-archive/restore`,
  status `archive` hint / `source_archive` / `source_archived`, review export payload (L6), startup reconcile;
  dashboard: shared "Chọn" box, "Lưu trữ đã chọn (N)", archive dialog, "Đã lưu trữ" badge + closed fold,
  "Khôi phục bản xuất" (confirm), export bin re-check, locked rerun / "Mở lại để xuất".
- AGENTS.md: source-video bullet rewritten for both actions (L1, no reopen) + `archive\` storage sentence.
- Tests: test_source_archive (26), test_source_archive_http (8), test_source_cleanup (+2),
  test_control_center (+2: markup, `archive` node scenario), test_recycle_bin (+2), test_export_guards (+2),
  test_cache_dependencies (3 modules added to CONTROL_PLANE).
- Focused runs: source_archive 26 OK; source_archive_http 8 OK; control_center 46 OK; source_cleanup 43 OK;
  cache_dependencies 13 OK; control_center+cleanup_http+skip_export+job_import+export_guards+recycle_bin+
  source_cleanup+job_store+scheduler 306 OK (skipped=2) before the dashboard step.

## 2026-10-03 — full suite and mock done
- Full suite (`python -m unittest discover -s tests -p "test_*.py"`, worktree src): Ran 1088 tests, OK
  (skipped=25). Baseline was 972 OK (skipped=21); the 4 extra skips are the other agent's live-memory tests
  (test_logo_memory_admin ×1, test_platform_memory ×3: project studio-logo memory not available). No failures.
- Mock `temp\ui-plan\batch4\mock_server.py` (port 8793, real handler on `mock-root`, fake recycler/bin/finder,
  real recycle_bin entry points replaced by raisers, 8765 refused) + `mock-README.md`; `--self-test` 15 checks OK.
  Visual check in the browser: toolbar, "Đã lưu trữ" fold, archived cards, archive dialog. Server stopped
  (the leftover python child on 8793 was stopped by PID too).

## 2026-10-03 — read-only code review (ecc:code-reviewer): 0 CRITICAL, 0 HIGH; fixes applied
- M1: the bin is read before REVIEW_QUEUE_IO/job_action_lock (`_export_facts`); only the capacity
  comparison stays under the locks (the recycler re-checks capacity before its shell call anyway).
- M2a: a recycler refusal (touches nothing) now always renames the source back (FAILED) instead of parking
  the row PENDING when the export left output/ on its own (plan: "refused/failed → rename source back").
- M2b: any manifest-write error (e.g. a lone surrogate, UnicodeEncodeError) settles FAILED (nothing moved).
- L3: manifest written to its own `.tmp` (fsync) before the rename, given its final name right after; a
  rename that fails removes only that `.tmp`, so retries leave no orphan manifests. A publish failure rolls
  the source back. Post-move rollbacks still keep their manifest (next try → next free numbered name).
- L5: an unreadable archived copy is no longer reported as a SHA mismatch (archive and restore).
- `_move_and_recycle` split into `_place_source` / `_verify_copy` / `_recycle_export`; restore hash check in
  `_check_copy`. source_archive.py 771 lines.
- Not changed (reported): L4 shutdown wait vs long hash (reconcile covers it), L6 sizes, L7 cleanup busy text
  (pinned by the batch-3 HTTP test), the batch-3 cleanup still reads the bin under the locks.
- Tests: test_source_archive 32 OK (+6 new, 2 extended; all new ones RED first). Focused 12 modules: 363 OK
  (skipped=2). Mock `--self-test` OK, no `.tmp` left in mock-root/archive.

## 2026-10-03 — final full suite
- `python -m unittest discover -s tests -p "test_*.py"` (worktree src): Ran 1112 tests in 137.1s, OK
  (skipped=25), no failures (log: scratch\full-suite-2.log). Final report sent to the lead.

## 2026-10-03 — security-review fixes L1–L5 (coordinator request; all new tests RED first)
- L1: `source_archive_files.presence()` (present/absent/other/unreadable; only FileNotFoundError or
  NotADirectoryError = absent). The startup reconcile never settles on a guess: an unreadable
  source/archive/export, or a source in neither place, keeps the row PENDING (RESTORING for a restore) with
  an ERROR event and places in the payload. Both places → FAILED as before.
- L2: a manifest gets its final name only in `settle_archived` (ARCHIVED); every rollback (`roll_back`) and
  the reconcile "interrupted" path remove the row's own `.tmp`; reconcile names the `.tmp` when it settles.
  A naming failure after a good archive is a warning (the complete `.tmp` stays). Numbering is unchanged.
- L3: `path_refusal` refuses a colon after the drive (alternate data streams; sources and exports);
  `export_path_refusal` (name rule + directly in output/, `EXPORT_FOLDER_MESSAGE`) used by
  `send_export_to_recycle_bin` and the archive eligibility check.
- L4: `Handler.end_headers` adds `X-Frame-Options: SAMEORIGIN` and `Content-Security-Policy:
  frame-ancestors 'self'` to every response (send_bytes/json, stream_file, send_error).
- L5: `find_recycle_record(..., until=None)`; the recheck passes `until` = the row's own settle time
  (`finished_at` / `archived_at`) + `RECHECK_UNTIL_SLACK_SECONDS` (60). A next-row bound never applies (only
  the latest row is recheckable) and is always later than this one.
- AGENTS.md: manifest kept only for a successful archive (.tmp before the move, removed on rollback), export
  directly in output\ (no subfolder/stream), unreadable/nowhere rows stay locked with an ERROR event.
- Tests: +8 (recycle_bin 2, source_cleanup 1, control_center 1, source_archive 4) and 9 updated.
  Changed modules 186 OK (skipped=1); focused 12 modules 373 OK (skipped=2); full suite Ran 1130, OK
  (skipped=25) (scratch\full-suite-3.log). Mock self-test OK.
