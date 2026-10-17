# Rebuild keeping decisions: check on a test Control Center (session D)

`rebuild_check.py` checks "Dựng lại danh sách duyệt (giữ quyết định)" (`docs/handoff/D-rebuild-keep.md`) on a copy of
one exported job. The real Control Center is never called and the install folder is only read; everything runs on a
test Control Center with a temporary root under `<install>\temp` and its own port.

```
python -I docs/evidence/rebuild-keep/rebuild_check.py run --job 145 --port 8797 --out docs/evidence/rebuild-keep/check-2026-10-17 --shots <install>/reports/evidence/rebuild-keep/check-2026-10-17
python -I docs/evidence/rebuild-keep/rebuild_check.py again --root <install>/temp/rk-check-<time> --port 8797 --out docs/evidence/rebuild-keep/check-2026-10-17
python -I docs/evidence/rebuild-keep/rebuild_check.py remove --root <install>/temp/rk-check-<time>
```

- `run` copies the job (source video, report folder, export and manifest, stage cache entries) into a new root, presses
  the button and waits for the new list. It resumes the test queue only while the real queue is idle (read from its
  database, mode=ro). It then shoots the review dialog, the job drawer and the confirmation in headless Edge (GET only)
  and re-exports when no card waits.
- `again` presses the button once more on the job that `run` left in review. The stage cache key holds the absolute path
  of the root's `scripts\run.ps1`, so a test root never uses the install's entries. The second press shows the scan steps
  the first press ran coming back from the root's own cache.
- `remove` deletes the root: links first, never entered.

## check-2026-10-17 (code `3dbfeee`, job 145)

- `result.json` (first press, revision 3 → 4, 295 s):
  - before the press, "Xóa video gốc" and "Lưu trữ" accepted the exported video; while queued and after the rebuild both
    refuse it, and a second press is refused;
  - 27/27 old decisions carried, 0 restored unchanged, 0 conflicts; no decision without an old source; 1 card waits;
  - the old list, the source and the old export are unchanged;
  - the dialog's note: "Đã mang 27 / 27 quyết định của revision 3 sang thẻ mới. Còn 1 thẻ chờ duyệt (mới hoặc đổi).
    Không có quyết định nào được tự đặt.";
  - no re-export, because a card waits for a person.
- `result-again.json` (second press on the same root, revision 4 → 5, 143 s): `text` restored from the root's cache
  (`STAGE_CACHE_HIT`); 26/26 decisions carried; 1 card waits.
- Both runs: the real queue stayed idle; `visual_logo` and `localize_logo` ran on the GPU. Their key includes the brand
  memory, which changed after every job's last scan, and their entries are too long to store under the test root.

The result files hold counts, states, stage names and the dialog's texts; no name, path or picture. The screenshots show
real frames and titles, so they stay in `<install>\reports\evidence\rebuild-keep\check-2026-10-17\`, which is not in Git.
