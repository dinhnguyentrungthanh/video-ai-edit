# Preview blurs: visual check on real review queues (session D)

These two scripts check the drawing of other cards' blurs (`docs/handoff/D-preview-blurs.md`) on the review queues
of real jobs. The real Control Center is never called: the check runs on a test Control Center with a temporary root
under `<install>\temp` and its own port.

1. `copy_jobs.py make` copies the active review queue of each job, and the preview images that queue names, into a
   new temporary root. It reads `state\control-center.sqlite3` read-only and writes nothing into the install. Each
   copied job's report folder is renamed `job-<id>`, and its source points to a missing file, so the cards show
   their report previews. The scheduler of that root is paused.
2. `copy_jobs.py serve` serves that root on a port other than 8765.
3. `shoot.cjs` opens each copied job in Edge (Playwright, GET requests only) and picks two cards:
   - a dialogue card (kept text, red box in the lower half) that a blurred watermark card covers in time;
   - the narrowest blurred fixed-text card at the top.

   It shoots each card's picture with the magnifier and the note under it, at 390 × 844 (light, dark, "Ẩn khung")
   and at 1440 × 900. It records whether every blurred box is painted, whether the labels stay inside the image and
   apart, what the magnifier shows, whether the page sent any POST, and the SHA-256 of each queue before and after.

```
python -I docs/evidence/preview-blurs/copy_jobs.py make --src src --root <install>/temp/d-fixture --jobs 145 134
python -I docs/evidence/preview-blurs/copy_jobs.py serve --src src --root <install>/temp/d-fixture --port 8799
node docs/evidence/preview-blurs/shoot.cjs http://127.0.0.1:8799 <install>/temp/d-fixture <install>/reports/evidence/preview-blurs
```

Copies and screenshots show real frames and texts:
- the copies never leave `<install>\temp`;
- the screenshots and `shoot-result.json` stay in `<install>\reports\evidence\preview-blurs\`, which is not in Git.

Jobs are named by number only.
