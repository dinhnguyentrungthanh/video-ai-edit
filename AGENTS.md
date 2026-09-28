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
- Never modify or delete source videos. Never auto-publish or upload.
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
