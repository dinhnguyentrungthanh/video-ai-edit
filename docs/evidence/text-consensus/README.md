# The text scan keeps a fixed logo's box in its group (fix/text-consensus-logo, 2026-10-16)

Branch `fix/text-consensus-logo` from `main` `419377f` (session D; plan
`docs/handoff/D-text-consensus-plan.md`, approved by the coordinator on 2026-10-16). Code:
`src/biliflow/textscan.py` only. **The text stage's cache key changes** (the 9 other stages keep
theirs): after the merge, "Chạy lại" scans the text stage again (about 2 minutes for a 45-minute
film on this PC). Jobs by id only; the site is `EXAMPLE.COM` here. Results with real readings
stay in `temp\text-consensus\` (Git-ignored, this PC only).

## 1. Why job 145's logo still leaked after G12

- **Root cause (scan).** The scan joins repeated corner tracks of one text into one track. Its
  box (`_consensus_overlay_box`) gives every member one vote. In job 145, 11 short pieces of the
  site's scrolling line outvoted the logo, which was read in 607 samples just below the line. The
  group box [432, 18, 960, 43] (960 × 402 analysis frame) covered the line only.
- **G12's review-build fix needed a visual-logo card.** That card read the logo's text at 175–180 s.
  The user's "Chạy lại" on 2026-10-16 (revision 2) took the text stage from the cache but ran the
  visual-logo stage again, because its key holds `state\brand-memory.json`, which had changed
  (263 → 275 records). That stage asks the model about 175 of 512 candidate windows, and the new
  brand records changed which ones; the 175–180 s window was not asked. So there was no card to
  join, the film-long card stayed (0, 21, 1280, 32) and the export showed the logo.

## 2. The rule (`textscan.py`)

- `_summarize_track` adds `footprint_box`: the consensus of the track's own reads (its box
  without a read far off its usual size; up to 3 reads, its min/max box).
- `_promote_repeated_corner_overlays` still takes the consensus of the members' boxes. A member
  read at least 10 times (`OVERLAY_ANCHOR_MINIMUM_READINGS`) over at least the group's minimum
  span is an **anchor**: a fixed overlay by itself. Its footprint is added to the group box when
  all of these hold:
  - it is not already 95 % inside the consensus box;
  - it touches the box (overlaps it, or a gap of at most half its own height);
  - it is at most 2.5 times as tall.
- The box never shrinks. `overlay_box_sources` keeps the consensus box and every anchor that
  sticks out (track, footprint, reads, joined or not).
- Unchanged: thresholds, how tracks join a group, routing, ad scores, the 250-track cap. A read
  joined to scene text still drops out: a one-read track is never an anchor.

## 3. Gates (`gate-summary.json`)

**Tests.**
- `tests/test_textscan.py`: 10 new tests. The job-145-shaped group gives [432, 18, 960, 43] on
  `main`.
- `tests/test_fixed_logo_scan.py`: scripted OCR shaped on job 145's real reads, through
  `scan_text` and `build_review_queue`, 3 tests. On `main` 2 fail: the box drops the logo and no
  BLUR card covers it.
- `tests/test_fixed_logo_clip.py`: real OCR on a self-made clip, a scrolling `EXAMPLE.COM …` line
  with `Example.com` fixed just below it, 2 tests. It passes on `main` too: real OCR there never
  joins the logo with the line, so the logo stays its own track and the review build blurs it.
- 97 tests of the modules that use the text scan pass.

**Rescans** (`rescan.py`, GPU, with the user's go and the real queue empty):
- **Sources:** jobs 145, 130, 134, 136 and 143, and the Golden Set v2 sources (films 37 and 38,
  seven clips of job 39); about 19 minutes in all.
- **Arguments:** the production fast-scan arguments with no track cap.
- **Same reads as before:** the track counts equal the earlier reports (job 145: 856; golden: 60,
  468, 392 and 2,658).
- **One scan per source, three variants built offline:**
  - baseline: the old box;
  - candidate: the cap of 250;
  - uncapped: item 6.
- **The baseline is checked** against `main`'s own grouping run on the saved grouping input: the
  same box for every group.

**Group boxes that change**, over all 14 rescanned reports: only job 145's top-right group,
[432, 18, 960, 43] → [432, 18, 960, 77]. Its anchors are tracks 2, 554, 606 and 839: the logo,
whose track broke three times.

**Job 145's queue**, built from the rescanned text report and revision 2's visual-logo reports
(no 175–180 s card):
- one text card suggesting BLUR, 0–2561 s, region (0, 21, 1280, 74), covers the logo (x 865–1146,
  y 55–84), still 7 cards;
- with revision 1's visual-logo reports: the same card and region, and the 175–180 s visual card
  merges into it;
- `fixed_text_logo` (G12) joins nothing: no duplicate region, no extra widening.

**Subtitle band:** ad tracks cited by a main card weakened 0 in jobs 130, 134, 136, 143 and 145.
The baseline and candidate queues have the same counts in all five.

**Golden Set v2:**
- old box vs new box on the same reads: caught 0, missed 0, new traps 0; ads 19/19, false
  positives 24/24;
- against the stored v2 baseline (2026-10-13): +1 caught, 0 missed, 0 new traps, the catch G11
  already brought;
- `compare` exits 1 only for its +5 % time gate: score-only scorecards carry no pipeline time.

**Window exports of job 145** (`export_logo.py`, x264; OCR every 0.5 s on the logo's box):

| Window | Logo in the source | Logo in the user's export (revision 2) | Logo after one BLUR on the new card |
|---|---|---|---|
| 130–230 s | 199 / 200 | 198 | **0** |
| 400–500 s | 200 / 200 | 200 | **0** |

The scrolling line (400–500 s) reads 165 in the source, 0 in the user's export and 0 after.

**Cache keys** (`stage_cache.build_code_manifest` on clean archives of `main` and the branch): only
`text` differs (`src/biliflow/textscan.py`). `adult`, `animation_safety`, `confirm_violence`,
`gore`, `live_safety`, `localize_logo`, `verify_adult`, `violence` and `visual_logo` are equal.

## 4. Item 6 (the 250-track cap): measured, not changed

Uncapped vs the cap of 250, from the same scans:

- **What the cap drops** (580–694 tracks per film in this series): reference tracks, mostly in
  the subtitle zone (credits, scene text, subtitle lines, low-ad uncertain lines) and the middle
  of the frame. It drops the latest ones first.
- **What is lost: the end of the subtitle-band watermark.** The watermark card under the
  subtitles ends earlier with the cap:

  | Job | Card ends with the cap | Card ends without | Film length |
  |---|---|---|---|
  | 130 | 2,247 s | 2,646 s | 2,655 s |
  | 134 | 2,427 s | 2,643 s | 2,657 s |
  | 136 | 2,436 s | 2,691 s | 2,702 s |
  | 143 | 2,361 s | 2,668 s | 2,668 s |
  | 145 | 2,313 s | 2,561 s | 2,561 s |

  The sightings in a film's last minutes are among the dropped references.
- **On the user's exports:**
  - Job 145's export (revision 2) still shows the mark after 2,313 s: OCR reads it in 8 of 24
    samples from 2,320 to 2,550 s (10 in the source).
  - Job 130's export reads it in 0 of 24 (6 in the source); its older queue blurred that area
    longer.
- **Golden v2 without the cap:** caught 19/19, missed 0, new traps 0, false positives 24 → 22.
- **Queues without the cap:**
  - main cards 130: 8 → 7, 134: 6 → 8, 136: 9 → 9, 143: 22 → 7, 145: 7 → 8;
  - subtitle-band ad tracks in a film: 47–66 → 86–107.

Whether to drop or change the cap on this branch, before the merge (one text-key change), is the
user's decision.

## 5. Limits (from a read-only code review)

- **The footprint keeps a read up to 1.8 times the usual size.** Job 145's logo track keeps a
  few reads joined to the line, so the box reaches y 77 (analysis) where the logo ends at 68.
  The result is a little more blur below the logo, never less.
- **The height margin is thin.** On job 145 the footprint is 2.12 times the consensus height,
  against the limit of 2.5. On a film whose line pieces are thinner (about 20 px), the same logo
  would stay out: it would be recorded as `joined: false` in `overlay_box_sources`, and the
  review build does not show that.
- **The width is not bounded.** Only the height is capped. Over the 14 rescanned reports only job
  145 changed.

## 6. Tools

- `rescan.py scan`: GPU; writes only into a new folder under `<install>\temp`.
- `rescan.py gate`: CPU; queues, golden scores, counts.
- `export_logo.py`: CPU; the window exports.

They read the real reports, queues and sources, and the database read-only. They never call a
Control Center route.
