# The text scan keeps a fixed logo's box in its group, and ad evidence past its track limit (fix/text-consensus-logo, 2026-10-16)

Branch `fix/text-consensus-logo` from `main` `419377f` (session D; plan
`docs/handoff/D-text-consensus-plan.md`, approved by the coordinator on 2026-10-16). Two changes
of the text stage, made on one branch so that its cache key changes once:
- the fixed logo's box (sections 1–3; `src/biliflow/textscan.py`);
- item 6, the 250-track limit (section 4, requested by the coordinator for the user after the
  first gates): `textscan.py`, the web-address patterns moved to `web_address_patterns.py`, and two
  review-build changes (`ad_text_memory.py`, `review_workflow.py`).

**The text stage's cache key changes** (the 9 other stages keep theirs): after the merge, "Chạy
lại" scans the text stage again (about 2 minutes for a 45-minute film on this PC). Jobs by id
only; the site is `EXAMPLE.COM` here. Results with real readings stay in `temp\text-consensus\`
(Git-ignored, this PC only).

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
- Unchanged: thresholds, how tracks join a group, routing, ad scores (the 250-track cap: see
  section 4). A read joined to scene text still drops out: a one-read track is never an anchor.

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

## 4. Item 6: the 250-track limit keeps ad evidence

**Why.** The text report keeps at most 250 tracks: review candidates, platform names, then the
earliest references. A film of this series has 830–944 tracks, so the limit dropped the last
580–694 references, and with them the sightings of the site's mark under the subtitles in the
film's last minutes. That card stopped 200–400 s before the end (table below), and job 145's
export (revision 2) still shows the mark there.

**The rule (text stage).**
- Past the limit, the report keeps every reference track with ad evidence:
  - a policy hit, the advertisement label, or an ad score of at least 0.40
    (`REFERENCE_AD_EVIDENCE`, `subtitle_band`'s line);
  - a web address (`web_address_tokens`, also without its dot);
  - an ad-text token: an address, a phone number of 8 or more digits, an @handle.
- The tracks kept before keep their places; the order is unchanged.
- A lone "@" is not enough (an @handle is). In the first version it was: in job 143 it kept one
  dialogue line, which linked seven others into a film-long fixed-text card suggesting BLUR over
  the subtitles (the band cards of task 2).
- Every other track past the limit stays in the report as `omitted_tracks`, without a preview,
  up to 4,000 (`MAX_OMITTED_TRACK_RECORDS`; `omitted_tracks_unrecorded` counts any beyond). The
  largest report on this PC has 3,594 tracks: 3,344 past the limit.
- `web_address_tokens` and `fold_alnum` moved unchanged from `scrolling_text.py` to
  `web_address_patterns.py`; `scrolling_text` imports them back. Both pattern modules are now in
  the text stage's cache key (an edit there rescans text); `scrolling_text.py` stays out.

**The review build (in no cache key).**
- `ad_text_memory.readmit_remembered_tracks`:
  - The scan cannot see the remembered addresses (`state/ad-text-memory.json`). Putting them in
    the text key would rescan every film each time the user blurs a new address.
  - So, with the user's memories on, the review build takes an omitted track whose folded
    reading holds a remembered address back into `tracks` as soon as it reads the report.
  - Benchmarks build without memories, so nothing comes back there.
- `promote_fixed_text_overlays`: a track lying at least 60 % inside a whole-film fixed text's
  box (`FIXED_TEXT_PIECE_INSIDE`) stays out of the recurring-text groups.
  - What happened in job 134: the site's logo, read all film long under its scrolling line, broke
    near the end. Its last piece, kept now because it reads the site's address, read like the line,
    joined the line's group and stretched the group's box over the logo.
  - The logo's film-long card then absorbed the line's card, and only the logo's box was left to blur.
  - Over 139 text reports (125 stored, 14 rescanned) the rule changes 4 groups:
    - job 134's line box returns to [0, 16, 960, 43];
    - job 143's line group has 24 members instead of 25, with the same box.

**Gates** (`limit-gate-summary.json`; `limit_gate.py`, `export_tail.py`).

*Tests (13 new):*
- `test_textscan`, 4:
  - a track with evidence is kept past the limit;
  - a plain track, a word like "SITCOM", a name without its ending and a lone "@" still drop;
  - evidence takes no track's place;
  - candidates and platform names are kept as before.
- `test_ad_text_memory`, 4: the take-back, its no-op cases, and a card with and without the
  user's memories.
- `test_text_track_limit_scan`, 4: scripted OCR through `scan_text` and `build_review_queue`, and the
  bound of 4,000 omitted records (patched to 2).
- `test_fixed_text_overlays`, 1.

*GPU rescans* (with the user's go and an empty queue): jobs 130, 134, 136, 143 and 145 and the
Golden Set v2 sources, with the production arguments and the limit of 250.
- They ran twice, about 16 minutes each; the second run used the final scan rule.
- Every report of both runs equals, track for track, the same rule applied to the morning's
  uncapped scans of the same sources.

*Tracks and report sizes* (text-scan.json as written; previews):

| Source | Tracks | Listed before → after | Omitted records | text-scan.json | Previews |
|---|---|---|---|---|---|
| 130 | 913 | 250 → 311 | 602 | 0.37 → 1.27 MB | 250 → 311 (9.8 → 12.2 MB) |
| 134 | 944 | 250 → 299 | 645 | 0.37 → 1.31 MB | 250 → 299 (9.8 → 11.8 MB) |
| 136 | 919 | 250 → 293 | 626 | 0.37 → 1.28 MB | 250 → 293 (9.5 → 11.3 MB) |
| 143 | 830 | 250 → 292 | 538 | 0.37 → 1.16 MB | 250 → 292 (9.6 → 11.3 MB) |
| 145 | 856 | 250 → 294 | 562 | 0.37 → 1.20 MB | 250 → 294 (9.5 → 11.1 MB) |
| film 37 | 392 | 250 → 250 | 142 | 0.36 → 0.55 MB | 250 (14.8 MB) |
| film 38 | 468 | 250 → 250 | 218 | 0.36 → 0.65 MB | 250 (14.8 MB) |
| dense clip of job 39 | 2,658 | 250 → 250 | 2,408 | 0.36 → 3.62 MB | 250 (11.4 MB) |

The six other golden clips have 1–60 tracks and do not change. The review page reads the queue,
not the report: the five queues grow by 4–6 KB (327–372 KB → 333–376 KB), and their card counts
follow.

*The subtitle-band watermark card now reaches the end.* Queues are built with the user's memories;
"before" is built by ca17c07's own code:

| Job | Card ends before | Card ends after | Film length | Cards before → after |
|---|---|---|---|---|
| 130 | 2,247.5 s | 2,646.5 s | 2,655.2 s | 8 → 8 |
| 134 | 2,427.5 s | 2,643.5 s | 2,657.2 s | 6 → 7 |
| 136 | 2,436.5 s | 2,691.5 s | 2,702.2 s | 9 → 9 |
| 143 | 2,361.5 s | 2,668.1 s | 2,668.1 s | 22 → 22 |
| 145 | 2,313.5 s | 2,561.2 s | 2,561.2 s | 7 → 7 |

- A card that ends before the film ends at the mark's last sighting plus the usual tail (9–14 s
  before the end).
- Job 134's seventh card holds two sightings of the site's address at one place in the subtitle
  zone. The one at 189–204 s was folded into the band's dialogue card before; the one at
  2,580–2,589 s is kept now. The card suggests BLUR on those two moments only.

*The subtitle band and the blur:*
- Subtitle band weakened: 0 in all five jobs. Every ad track a main card cited before is still
  cited after.
- Every BLUR card of the "before" queues stays covered by BLUR cards after, at 50 moments each:
  at least 96.9 % of its box. The watermark box moves by 1 px with more sightings; job 143's is
  31 px high instead of 32.
- Job 145's queue takes back its 2 omitted tracks that hold a remembered address; the other four
  jobs have none.

*Golden Set v2* (built without memories):
- Before and after are identical for every detector: advertising caught 19/19, false positives
  24/24, advisory cards 487/487, traps 0.
- Against the stored v2 baseline: +1 caught (G11), 0 missed, 0 traps. `compare` exits 1 only for
  its time gate.

*Export of job 145's end* (`export_tail.py`, x264, OCR every 0.5 s on the mark's box, 2,313–2,561 s,
496 samples): the mark is readable 188 times in the source, 188 times in the export of the user's
queue, and **0** times after one BLUR on the new watermark card.

*Cache keys* (clean trees of `main`, ca17c07 and this branch): only `text` differs.
- `textscan.py` changed; `ad_text_patterns.py` and `web_address_patterns.py` join the key
  (18 → 20 files).
- The 9 other stages are equal.

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
- **Report size (item 6).**
  - text-scan.json grows about 3.4 times on this series (1.2–1.3 MB for a 45-minute film) and 10
    times on the densest golden clip (3.6 MB for 400 s), all from `omitted_tracks`.
  - It holds at most 4,000 omitted records (about 5–6 MB).
  - Previews grow only with the evidence kept (42–61 per film here).
- **A remembered address beyond 4,000 omitted tracks** is not taken back (none on this PC). A track
  taken back has no preview of its own.
- **A piece inside a whole-film text's box joins no recurring group.**
  - A recurring ad line with a phone number or an @handle that lies at least 60 % inside such a box
    would lose its own group card. Its tracks stay items of their own, and the whole-film card covers
    at least 60 % of each.
  - On this PC 4 groups of 139 change, and each keeps its BLUR suggestion.
  - The queue does not show `omitted_tracks_unrecorded` (0 everywhere here).
- **Dialogue chains (task 2) remain.** The subtitle band's film-long fixed-text cards of jobs 130,
  134 and 136 are there before and after; this branch adds none. Job 134's grows 21 px wider
  ((276, 389, 736, 74) → (263, 389, 757, 74)): more dialogue pieces are kept.

## 6. Tools

- `rescan.py scan`: GPU; writes only into a new folder under `<install>\temp`.
  `--max-report-tracks 250` scans with the production limit (the default is none).
- `rescan.py gate`: CPU; queues, golden scores and counts for the logo's box.
- `limit_gate.py`: CPU; item 6's reports, queues, golden scores and sizes. `--before-src` builds
  the "before" queues with another tree.
- `export_logo.py`, `export_tail.py`: CPU; the window exports.

They read the real reports, queues and sources, and the database read-only. They never call a
Control Center route.
