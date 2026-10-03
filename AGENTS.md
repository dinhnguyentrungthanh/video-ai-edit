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
- Never modify or delete source videos. The single exception is the user-triggered "Dọn video gốc" action in the Control Center: after the user confirms the listed files in the dashboard dialog, it moves an input video to the Windows Recycle Bin (never a permanent delete; it refuses when the bin capacity could be exceeded). It applies only to a job whose active review revision has a completed export that passes its manifest check and was rendered from the current review decisions, or that the user marked "Bỏ qua (không xuất)" with a skip record that still matches the review. It never deletes or rewrites reports, review decisions, brand/studio memory or outputs; in `state/control-center.sqlite3` it only adds its own `source_cleanups` rows and SOURCE_* events and resets the watcher row of the moved file (these records lock the cleaned job; never "repair" them). Agents must never trigger this action, call /api/source-cleanup, execute_cleanup or send_to_recycle_bin on user files; tests may only recycle temporary files they created themselves under `E:\DungChung\BiliFlow\temp`, and only with the opt-in BILIFLOW_TEST_RECYCLE_BIN=1 test (`tests.test_recycle_bin.RealRecycleBinTest`), which runs at most once and is then skipped because of its marker `temp\recycle-bin-test\ran-once.json`.
- Never auto-publish or upload.
- Detection only creates review candidates. A human must approve KEEP, BLUR, CUT, or NEEDS_MORE_CONTEXT before export.
- Do not silently delete `reports`, `state`, review decisions, regression evidence, or brand memory. Generated caches may only be cleaned through the existing bounded cleanup policy.
- Keep runtime, models, caches, temporary files, reports, and outputs under `E:\DungChung\BiliFlow`; do not move project data back to drive C.
- Preserve the current detector thresholds and export behavior unless a measured regression justifies a change.
- Stay on the current branch. Do not merge to `main`, push, or rewrite history unless the user explicitly requests it.

## Verification

- Run the focused tests for the files changed, then the full suite for detector, scheduler, review, or renderer changes.
- The standard full test command is `.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v`. The project test files use `unittest`; do not assume `pytest` is installed.
- Run `.\scripts\run.ps1 license-audit` when model manifests, dependencies, or license policy change.
- Real-video validation must report the exact job/revision and detector scope. A single-purpose scan never certifies skipped detector groups.
- Update `CHANGELOG.md`, `docs/PROJECT_STATUS.md`, and `docs/SESSION_HANDOFF.md` after a verified milestone.
