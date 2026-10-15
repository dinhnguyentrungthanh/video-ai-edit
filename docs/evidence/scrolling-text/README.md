# Scrolling site text and web addresses without their dot (fix/ticker-subtitle, part 1, 2026-10-15)

Branch `fix/ticker-subtitle` from `main` `bd0a7b8` (session D). Code: `src/biliflow/scrolling_text.py`,
`src/biliflow/review_decision_carry.py`, `src/biliflow/review_workflow.py` (`build_review_queue`, `_text_items`).
All three are review-build code: no scan stage's cache key covers them, so old jobs need no rescan. Jobs by id only;
the source site is `EXAMPLE.COM` here, and no film name, file name, OCR text or link is in these files. The gate
results with real readings stay in `reports/evidence/ticker-d/` (Git-ignored, this PC only).

## 1. What leaked (jobs 101–103, 107, 108, 112, 118, 128, 140, 147)

The source site scrolls one line through the top band ("EXAMPLE.COM chia sẻ dữ liệu phim …") in about 15–20 s. The
text scan reads the frame every 3 s, so one crossing came out as several short tracks: an entry piece at the right
edge (one reading, LIKELY_SCENE_TEXT), wide pieces, exit pieces at the left edge, often with the dot lost
("…CCOM", "EXAMPLE COM"). Each piece was judged alone:

- job 107: the BLUR card started at the second reading (2928 s); the line was on screen from about 2922 s;
- job 108: the wide piece had no dotted domain and ad 0.05, so its card was optional with a KEEP suggestion, never
  answered; nothing was blurred;
- the other jobs: one piece in the main list, the entry and exit pieces only as optional cards or none.

## 2. The rule (`scrolling_text.py`)

- **Ticker.** Tracks whose box lies in the top or bottom band (centre within 25 % of the height from the edge),
  overlap vertically by half, and whose readings are stretches of one line (the longest shared run aligns them; the
  shared stretch of at least 6 letters matches at 0.75) link into one group. A group counts when its longest reading
  has 12 letters and either it touched one frame edge and later the other (entry piece at the right edge, exit piece
  at the left: `right_to_left`, or the reverse), or a reading on a piece spanning half the frame is an earlier one
  moved on (6 letters lost at one end, 6 gained at the other, 12 shared). Groups that meet in time join (job 107's
  two halves). Short or unlinked readings of the band inside the group's time join it.
- **Card.** One track for the whole band (full width, from the highest to the lowest box), routing
  `REVIEW_SCROLLING_TEXT`, suggested BLUR, every piece cited. Time: one read interval before the first reading (two
  when that reading is not an entry piece at the edge), to the scan's end of the last piece (last reading + one
  interval), one interval more when the last reading still covered more than 30 % of the width.
- **Web address.** `web_address_tokens`: `name.tld`, `www`, `http(s)://`, and in capitals `NAME. COM`, `NAME ,COM`,
  `NAMECOM`, `NAME COM` (COM, NET, ORG, VN, TV), with a stop list of ordinary words (SITCOM, TELECOM, INTERNET,
  CABINET …). A general rule, written for no site. A track with one is a main-list card suggesting BLUR (routing
  `REVIEW_WEB_ADDRESS` unless it already has an ad routing), never only optional.
- **Rebuild keeps decisions.** `build_review_queue(..., keep_decisions=True)` carries the previous queue's decisions
  (`review_decision_carry.py`): same id exactly; else the same thing (category, 80 % of the shorter time, boxes
  75 % of the smaller): BLUR always, its time and box widened to cover the old card too; KEEP/CUT/NEEDS_MORE_CONTEXT
  only to a card the old one covered for 80 %; disagreeing old cards leave the new card open
  (`decision_conflict`); every decided card carried nowhere comes back unchanged. The audit log gets a
  `REBUILD_KEEP_DECISIONS` entry. Off by default; nothing in the Control Center calls it yet (section 7).

## 3. Web addresses read without their dot: how often the rule fires (item 3)

All 118 text reports of the install (20,629 readings): 1,125 hits on 98 distinct tokens, every one a reading of a
real site name (a source site or a platform); 448 of them found only by the new dot-less or spaced forms. No hit on
ordinary film text, subtitles or a proper name. The unit tests keep the negatives: "Anh ăn cơm chưa?", "NẤU CƠM
ĐI", "Xem TV đi em", "Hà Nội, VN", sitcom, TELECOM, ANIMAL PLANET, INTERNET, CABINET, COMPANY, NETWORK, VTV3, HTV7,
SCTV, "A NETFLIX SERIES", "3.14", "Ừ. Com".

## 4. Tickers found in every report

On all text reports of the install the rule finds 24 groups, all real crossings of the source site's line: top
band, right to left, with a web address, 18–30 s each (jobs 87, 88, 101–103, 107, 108, 111–114, 118, 122–124,
126–129, 138–140, 142, 147). Three false groups seen on the way were removed by the rule before this commit, and
each is now a unit test: end credits whose reading only grows ("Studio", then "Estudio de Doblaje"; job 128 at
3813 s, job 108 at 3657 s) and two subtitles sharing 8 letters (job 136 at 129 s).

## 5. Gates

| Gate | Result |
|---|---|
| Unit tests `tests/test_scrolling_text.py` (24): job 107/108/140 shapes, lead and tail, negatives (subtitles, a static title, a corner watermark, unrelated band text, persistent overlays, growing credits, subtitles sharing words), web-address positives and negatives, queue cards, rebuild with decisions | OK; the three negative shapes of section 4 fail on the earlier slide rule |
| Self-made clip `tests/test_scrolling_text_clip.py`: drawtext "EXAMPLE.COM …" and "EXAMPLECOM …" crossing the top band in 15 s, subtitles at the bottom, real OCR on CPU | OK: one main card per crossing, from before the entry to after the exit, full width; no scrolling card for the subtitles. On `bd0a7b8`: several cards per crossing |
| Rebuild on copies of jobs 107, 108, 128, 140, 147 (`gate_rebuild.py jobs`, temp root, below-normal priority) | each crossing is one main card suggesting BLUR (107: 2922–2943.5 s, covers 2924 s; 108: 402–423.5; 128: 2979–3003.5; 140: 321–339.5; 147: 2235–2259.5); with `keep_decisions` every old decision is kept (lost 0) and the old BLUR cards are carried onto the longer card (107 from 2928, 128 from 2985, 140 from 333, 147 from 2241 s) |
| Card counts, fresh build, `bd0a7b8` → branch | 107 7 → 7, 108 5 → 6, 128 4 → 4, 140 3 → 3, 147 8 → 8 |
| Golden Set v2 (`gate_rebuild.py golden`: the baseline's 9 trial queues rebuilt by both trees, scored, `compare --gate detector`) | newly caught 1 (T3, advertising, must-catch, holdout: partial → caught), newly missed 0, new trap hits 0; main-list false positives 24 → 24, optional cards 487 → 487. The same against the v2 tag. The tool's "+5 % pipeline time" line cannot be checked by a rebuild (no scan ran) |
| Scan cache keys (`build_code_manifest` of both trees) | unchanged for all 10 stages |
| Bottom band: the jobs with BLUR there (130, 134, 136, 143, 145 with source; 62–66, 70–72, 99, 100 and 101–103, 112, 118 without, built over an empty placeholder) | suggested BLUR in the bottom band never fewer (134: 1 → 51, 136: 1 → 43, 143: 0 → 45, 145: 0 → 45, 130: 1 → 2, the others equal). Cards equal or one fewer in 16 jobs (joined ticker pieces). More in the four jobs with the site watermark in the subtitle band: 134 42 → 56, 136 38 → 48, 143 64 → 66, 145 50 → 53. The extra cards are watermark sightings with a web address that the old build showed nowhere (scene text: 134 13, 136 8, 143 2, 145 3) or only as optional (134 1, 136 2); part 2 groups one watermark's sightings into one card. Rebuild with decisions in the five jobs with source: lost 0; new pending cards 0, 14, 2, 2, 3 |
| Window exports of jobs 107 and 108 (`export_check.py`: a lossless copy of the window around the card, the old and the new queue's operations, real `render_final_output` with libx264; job 108's ticker card decided BLUR on the copy; EasyOCR on CPU reads the band every 0.5 s, 63 samples per job) | all four exports COMPLETED, full decode passed. Job 107: the source reads the line at 34 samples (2925–2942.5 s); the old export still reads 6 samples with the web address (2925–2927.5 s, the leak); the new one reads no letter at any sample. Job 108: the old export reads the line at 30 samples (404.5–422 s, nothing was blurred); the new one reads no letter |

## 6. The 250-track cap (item 6, not changed)

`textscan._limit_report_tracks` keeps every review candidate (every ad routing) and drops the latest non-candidate
tracks past 250, without their text. Job 107: 365 tracks, 115 dropped, all LIKELY_CREDITS from 3954 s (end
credits). Job 108: 487 tracks, 237 dropped (235 LIKELY_CREDITS, 2 LIKELY_SCENE_TEXT) from 3666 s. No ad-routed
track can be dropped; a single-sighting piece of a ticker routed as scene text late in a long film could be. Keeping
web-address tracks past the cap changes the text stage (its cache key); it waits for the coordinator and the user.

## 7. Old jobs

There is no button that rebuilds a review list in place. "Chạy lại" queues a new revision: its scan stages come from
the stage cache when they are still there (the keys did not change), but its queue starts without decisions. A
"Dựng lại danh sách duyệt (giữ quyết định)" action that calls `build_review_queue(keep_decisions=True)` on the
active revision and sends an exported job back to review needs the coordinator's agreement first.

## Files

- `gate_rebuild.py`: the jobs and golden gates (writes only under a new folder in `<install>/temp`).
- `export_check.py`: the window exports and the OCR check.
- `gate-summary.json`: card counts and seconds per job (no text).
