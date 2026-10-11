# Evidence moved out of `temp`

On 2026-10-11 the user asked to free disk space and to stop keeping evidence in `temp\`. The ten evidence folders
that code, tests or docs cite as `temp/<folder>/...` were emptied: `run-issues-diag`, `next`, `studio-mask-design`,
`ui-plan`, `adult-fp`, `adult-triage`, `q3-handoff`, `job40-diag`, `dashboard-v2-evidence` and `review-mockup`
(about 1.7 GB, almost all frames, clips, `.npz` feature arrays, SQLite copies, temporary project roots and browser
profiles).

## Where a cited file is now

A comment or document that cites `temp/<folder>/<path>` means one of these, in this order:

1. `docs/evidence/<folder>/<path>` (this folder, tracked): plain text with no real film or series titles, real video
   file names, local paths, real hosts, home-network addresses or personal data. 75 files, about 0.5 MB. Two
   independent review passes and a scan checked each one before it was committed.
2. `reports/evidence/<folder>/<path>` (Git-ignored, only on the main PC): 185 files, about 4.9 MB. These are text files
   that name real films or video files, hold local paths or the home network, plus the results that exist nowhere
   else: the manual G0 gore classification, the audit comparison over 1,134 review queues, a few post-implementation
   studio-mask safety results and the job 40 watermark frames (images, local only).
3. Removed: everything else (images, clips, feature arrays, database copies, temporary roots, browser profiles,
   superseded logs and scripts). It was regenerable, superseded by commits, or already summarized in the files kept
   above. The cleanup sent it to the Recycle Bin.

The paths keep the old layout, so `temp/next/anime-gore/items-ff.csv` is `reports/evidence/next/anime-gore/items-ff.csv`
and `temp/ui-plan/batch4/hooks-4a.md` is `docs/evidence/ui-plan/batch4/hooks-4a.md`.

## Code that still names `temp/...`

- `src/biliflow/adult_verification.py`, `brand_memory.py` and `shot_cuts.py` keep their `temp/...` citations
  unchanged on purpose: they are part of the scan stage-cache fingerprint (`cache_dependencies.stage_source_paths`
  for the verify_adult, logo and detector stages), and editing them, even a comment, would make those cached scans
  run again. `gore_triage.py` (whose evidence string is written into queue triage audits) and `logo_memory_admin.py`
  are not fingerprinted but are left unchanged too, so no production code changed for this cleanup. Read their
  citations with the rule above.
- `scripts/benchmark_adult_triage.py` reads its two inputs from the new places: the frame-truth spans from
  `docs/evidence/adult-fp/...` and the CPU verifier scores from `reports/evidence/adult-fp/...`. Like the rest of that
  benchmark, it runs only on the main PC, which has the local review queues and reports.
- Re-running the studies behind these files needs the local queues, reports and source videos of the main PC. The
  removed `.npz` scores of the gore study can be rebuilt with its full-film re-score script while both animation
  sources are still in `input\`.
