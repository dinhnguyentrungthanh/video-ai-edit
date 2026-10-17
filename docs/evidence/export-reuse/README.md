# Reuse of an export after a rebuild: check on a test Control Center (session D)

`reuse_check.py` checks `docs/handoff/D-export-reuse.md` on a copy of one job. The real Control Center is never
called and the install folder is only read: everything runs on a test root under `<install>\temp` with its own port.
It uses the helpers of `docs/evidence/rebuild-keep/rebuild_check.py`.

```
python -I docs/evidence/export-reuse/reuse_check.py run --job 143 --port 8798 --out docs/evidence/export-reuse/check-2026-10-17
python -I docs/evidence/export-reuse/reuse_check.py previews --root <install>/temp/er-check-<time> --port 8798 --out docs/evidence/export-reuse/check-2026-10-17
python -I docs/evidence/export-reuse/reuse_check.py import --root <install>/temp/er-check-<time> --out docs/evidence/export-reuse/check-2026-10-17
python -I docs/evidence/export-reuse/reuse_check.py remove --root <install>/temp/er-check-<time>
```

- `run` copies the job as it was before the user's rebuild: the newest revision whose export has that revision's own
  name (job 143: revision 3, the export `…-aa426512-24b2b5b2-reviewed.mp4`), its report folder, the export with its
  manifest, the source video (a copy, never a link) and the source's stage cache entries. On the test Control Center it
  presses "Dựng lại danh sách duyệt (giữ quyết định)", waits for the new list (the test queue runs only while the real
  queue is idle), compares the new operations with the export's manifest, then presses "Xuất video" and reads the
  "Xóa video gốc" and "Lưu trữ" previews (GET only).
- `previews` copies this tree's `src` into a root that `run` left and reads both previews again (for a later commit).
- `import` puts that job back to READY_TO_EXPORT, as job 143 is on the real Control Center now, and runs what a Control
  Center start runs (`job_import.import_existing_project`) on the test root.
- `remove` deletes the root: links first, never entered.

## check-2026-10-17 (job 143)

- `result.json` (code `ef837e9`):
  - before the press: COMPLETED, revision 3, both previews take the video;
  - the rebuild (344 s, `text` scanned again on the GPU: a test root never uses the install's cache entries) gives
    revision 4, READY_TO_EXPORT, 22/22 decisions carried, 0 cards waiting, 0 conflicts;
  - its 3 operations render the same as the export's manifest, under other ids, so the review's own name is another
    one (no file there);
  - "Xuất video": HTTP 200, `COMPLETED` in 0.03 s, the output is the earlier export, no render request, no render stage,
    `output\` unchanged since the copy and `work\` unchanged by the export; both previews take the video;
  - the real queue was idle throughout.
- `result-previews.json` (code `f8ddc38`, after the code review fix): COMPLETED, both previews take the video.
- `result-import.json` (code `f8ddc38`): from READY_TO_EXPORT, the startup import completes the job and records the
  earlier export as its final output.

The result files hold counts, states, booleans and timings; no name, path or picture.
