# BiliFlow agent entry point

Before changing this repository, read these files in order:

1. `docs/SESSION_HANDOFF.md`
2. `docs/PROJECT_STATUS.md`
3. `README.md`
4. The latest entries in `CHANGELOG.md`

Then run `git status --short --branch` and `git log -8 --oneline --decorate`.
The handoff file records the active branch, the last verified real-video run, and the remaining work. Treat live dashboard state as ephemeral and verify it through the local API or artifacts before making claims.

## Project invariants

- BiliFlow is local-first. Do not introduce paid per-call APIs or models without an explicit user request.
- Keep every production model commercially usable and recorded in the license policy.
- Never modify or delete source videos. The only exceptions are two user-triggered Control Center actions run after the user confirms the listed files in the dashboard dialog: (1) "Dọn video gốc" moves an input video to the Windows Recycle Bin (never a permanent delete; it refuses when the bin capacity could be exceeded). It applies only to a job whose active review revision has a completed export that passes its manifest check and was rendered from the current review decisions, or that the user marked "Bỏ qua (không xuất)" with a skip record that still matches the review. It never deletes or rewrites reports, review decisions, brand/studio memory or outputs; in `state/control-center.sqlite3` it only adds its own `source_cleanups` rows and SOURCE_* events and resets the watcher row of the moved file (these records lock the cleaned job; never "repair" them). A Recycle Bin record found later is never written into that row: "Kiểm tra lại Thùng rác" only reads the bin and appends a `recycle_checks` row and a SOURCE_RECYCLE_VERIFIED or SOURCE_RECYCLE_STILL_UNVERIFIED event. (2) "Lưu trữ" and "Khôi phục bản xuất". For a job whose export passes the same checks as "Dọn video gốc", or that was skipped with a matching skip record, "Lưu trữ" renames the unchanged source from `<install>\input` into `<install>\archive\sources\<job_key>\` on the same volume, verifies its SHA-256 and then, for an exported job only, moves that job's current export (`output\…-reviewed.mp4` and its `.manifest.json`, directly in `output\`, never a subfolder or an alternate data stream) to the Windows Recycle Bin (never a permanent delete; refused when the bin capacity could be exceeded; if the export cannot be moved, the source is renamed back). Only an archive that succeeded keeps `archive-manifest.json` (or the next free numbered name) beside the archived file: it is prepared as a `.tmp` before the move and a rollback removes only that `.tmp`. "Khôi phục bản xuất" renames the archived file back to its original input path only when that path is free and the SHA-256 matches; an exported job then returns to review (READY_TO_EXPORT or WAITING_REVIEW) to be exported again, a skipped job stays SKIPPED. An interrupted archive or restore is settled at the next start without calling the Recycle Bin; a row whose files cannot be found or read is never settled on a guess: it keeps the job locked and logs an ERROR event. Neither action deletes or rewrites reports, review decisions, brand/studio memory, other outputs or anything in `archive\` (except its own unfinished `.tmp` manifest); in the database they only add `source_archives` rows (a latest PENDING, ARCHIVED or RESTORING row locks the job; never "repair" them), SOURCE_ARCHIVED and SOURCE_ARCHIVE_* events, the watcher row of the moved file and, on a restore, the job state above. Agents must never trigger these actions, call /api/source-cleanup, /api/source-archive*, /api/source-recycle-check, execute_cleanup, execute_archive, restore_archive, send_to_recycle_bin or send_export_to_recycle_bin on user files, or move or delete anything in `<install>\archive`. Tests may only move or recycle temporary files they created under `E:\DungChung\BiliFlow\temp`; archive tests use a temporary project root there with a fake recycler, never `<install>\input`, `output` or `archive`. The only real-bin test remains the opt-in BILIFLOW_TEST_RECYCLE_BIN=1 test (`tests.test_recycle_bin.RealRecycleBinTest`), which runs at most once and is then skipped because of its marker `temp\recycle-bin-test\ran-once.json`; there is no real-bin test for exports.
- Never auto-publish or upload.
- Detection only creates review candidates. A human must approve KEEP, BLUR, CUT, or NEEDS_MORE_CONTEXT before export.
- Do not silently delete `reports`, `state`, review decisions, regression evidence, or brand memory. Generated caches may only be cleaned through the existing bounded cleanup policy.
- Keep runtime, models, caches, temporary files, reports, and outputs under `E:\DungChung\BiliFlow`; do not move project data back to drive C. `archive\` holds user-archived source videos; treat it like `input\`.
- Preserve the current detector thresholds and export behavior unless a measured regression justifies a change.
- Stay on the current branch. Do not merge to `main`, push, or rewrite history unless the user explicitly requests it.

## Verification

- Run the focused tests for the files changed, then the full suite for detector, scheduler, review, or renderer changes.
- The standard full test command is `.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v`. The project test files use `unittest`; do not assume `pytest` is installed.
- Run `.\scripts\run.ps1 license-audit` when model manifests, dependencies, or license policy change.
- Real-video validation must report the exact job/revision and detector scope. A single-purpose scan never certifies skipped detector groups.
- Update `CHANGELOG.md`, `docs/PROJECT_STATUS.md`, and `docs/SESSION_HANDOFF.md` after a verified milestone.
