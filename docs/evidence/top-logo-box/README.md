# The fixed site logo under the top line is blurred with it (fix/top-logo-box, 2026-10-15)

Branch `fix/top-logo-box` from `de91261` (session D, after parts 1 and 2 in
`docs/evidence/scrolling-text/` and `docs/evidence/subtitle-band/`). Code:
`src/biliflow/fixed_text_logo.py` (new) and one call in `build_review_queue`
(`src/biliflow/review_workflow.py`), before `reconcile_persistent_overlay_items`. Review-build
code only: no scan stage's cache key covers it, so old jobs need no rescan. Jobs by id
only; the site is `EXAMPLE.COM` here. Results with real readings stay in
`temp\top-logo\` (Git-ignored, this PC only).

## 1. What happened (job 145)

The source site's name is a fixed logo at the top right of the whole film, OCR box about
x 863–1149, y 53–86 (source 1280 × 536). The site's scrolling line runs just above it, about
y 21–57. The user's export blurred the line and left the logo readable.

- The text scan read the logo in every sample: track 2, 607 readings from 0 to 2562 s, all
  of its kept readings spell the logo's name. Its own box was 305 × 53 analysis px (the size
  kept in `visual_features`), twice as tall as the line.
- The scan then joined 12 top-right tracks that read the same name at the same place
  (`textscan._promote_repeated_corner_overlays`). The 11 others are gone from the report;
  most likely pieces of the scrolling line, which holds the same name. Their consensus box
  (`_consensus_overlay_box`: the boxes close to the members' median) is the line's box,
  [432, 18, 960, 43] analysis px, and track 2's taller box was dropped as an outlier.
- So the film-long card suggested (576, 24, 704, 30) on `main` (the user approved it) and
  (0, 21, 1280, 32) on `fix/ticker-subtitle`, where part 1 joined the line's cards. The top
  band was not cut and the review build dropped nothing: the scan's report no longer holds
  the logo's box.
- The visual-logo scan read the same name (Florence OCR) in the logo's own box at
  175–180 s, region (857, 47, 298, 45). Its card covered those 5 s only.

On all 118 text-scan reports of the install, no other grouped track lost its strongest
member's box like this: one title overlay of job 136 lost width (not a review candidate),
the others differ by 8 px or less. Keeping the logo's box at scan time would change the text
stage's cache key, so it is not done here; the coordinator decides.

## 2. The rule (`fixed_text_logo.py`)

A visual-logo card shows the same mark as a fixed-text card (`persistent_overlay` or
`repeated_watermark`, category text) when all of these hold:

- **The same text.** One of its OCR readings (its labels without scanner labels, grounding
  prompts such as "promotional banner" and model tokens such as `</s>`, at least 6 letters)
  is 0.75 similar to, or contains, a reading the text card made on its own. A name found only
  inside a longer line of the text card does not count.
- **The same place.** Its box touches the text card's box: at least half of its width in
  the card's columns, at most 8 px or half its height above or below, at most 2.5 times as
  tall. A box already 95 % inside the card's box needs nothing (the reconciliation joins it
  as before).
- **The same time.** Its window overlaps one of the text card's intervals.
- It is a plain visual brand candidate or a persistent visual logo (never an opening,
  ending, end card or title card), its region came with OCR, and neither card has a decision.

Then (a) the text card's suggested region becomes the union of both boxes (audit:
`fixed_text_logo.previous_region`, `logo_regions`, `logo_cards`, and a reason line), and (b)
the visual-logo card takes the text card's time (its own window stays in
`fixed_text_logo.previous_interval`). The existing reconciliation then joins the logo card
into the film-long card as supporting evidence, so one BLUR covers the line and the logo
for the whole film. A carried BLUR on a rebuild covers it too: `_widen_blur` unions the old
boxes with the new suggested region.

After a read-only review the rule takes part only with a text card suggesting BLUR and a
plain visual brand candidate not suggesting KEEP. A persistent visual logo card owns its
watermark in the reconciliation: inside the text card's new box it would have absorbed the
text card and blurred only its own box, losing the line's blur (a unit test holds that
case). A text card suggesting KEEP or nothing would have absorbed the logo's BLUR.

## 3. Where the rule acts (`reach.py`, every job with a review queue)

72 queues built fresh: four jobs change, all among the 25 of the jobs gate.

| Job | Card | Region before → after (source px) | Logo box(es) |
|---|---|---|---|
| 145 | top-line fixed text, 0–2561 s | (0,21,1280,32) → (0,21,1280,71) | (857,47,298,45) |
| 70 | top-left fixed text, whole film | (29,35,216,52) → (29,35,216,55) | (38,41,205,49) |
| 72 | top-left fixed text, whole film | (40,35,216,51) → (38,35,218,54) | three of (38,41,~205,48) |
| 72 | bottom fixed text line, whole film | (381,489,515,33) → (379,489,549,35) | (379,491,549,33) |
| 130 | subtitle-band watermark (part 2), 0–2247.5 s | (552,444,176,30) → (550,444,180,34) | (550,444,180,34) |

Outside job 145 the visual box adds 2–4 px around a mark the text card already covered,
or the width the visual scan saw of the same bottom line (job 72). In Golden Set v2 one
queue (job 39 clips) changes the same way: its fixed watermark card grows from the tight
OCR box (156 × 32) to the visual box (176 × 54).

## 4. Gates

| Gate | Result |
|---|---|
| Unit tests `tests/test_fixed_text_logo.py` (11): the line card covers the logo and the logo card takes its time (inputs unchanged), scanner and grounding labels are no reading, a name inside a longer line, another name, a far box, a much taller box, no OCR source, a box already inside, end and title cards, a KEEP or no suggestion on either side, a persistent visual logo (and the reconciliation keeping the line's blur with one), decided cards, no time overlap, a watermark's separate moments; and a queue shaped like job 145 (one BLUR blurs the logo for the whole film) | OK. The queue test fails on `de91261` (region (576,24,704,30)); the reconciliation test fails with persistent visual logos allowed |
| 24 review, text and logo test modules (`test_review_workflow`, `test_review_decision_carry`, `test_subtitle_band`, `test_scrolling_text`, `test_platform_logos`, `test_visual_logo_scanner` …) | 485 OK, 13 skipped |
| Jobs gate (`../scrolling-text/gate_rebuild.py jobs`, the 25 jobs of parts 1 and 2, `de91261` → branch) | cards equal except job 145: 8 → 7 (its visual-logo card joins the film-long card); suggested BLUR in the bottom band equal everywhere; **subtitle-band ad tracks in the main list never fewer, weakened 0 in all 25 jobs**; a rebuild keeping decisions loses 0 everywhere, and in jobs 145 and 130 the user's carried BLUR covers the logo box |
| Window export of job 145 (`export_logo.py`: the user's operations "before", one BLUR on the new card "after"; `render_final_output`, libx264; OCR every 0.5 s) | all four exports COMPLETED, full decode passed. The logo's box readable in 130–230 s: source 200 of 200, before 190, **after 0**; in 400–500 s: 200, 200, **0**. The line's rows in 400–500 s: 165, 47, 0. In the user's export the logo is blurred only at 175–180 s (their BLUR on the visual-logo card) |
| Golden Set v2 (`gate_rebuild.py golden`) | against `de91261`: newly caught 0, newly missed 0, new trap hits 0, every metric identical (advertising 19/19 in the main list, regions 19/19, suggestions 19/19, false positives 24); against the v2 tag: +1 caught (part 1), 0 missed, 0 trap hits |
| Scan cache keys (`build_code_manifest` of clean archives of both trees) | unchanged for all 10 stages |

The window export ran on the tree before the review fix; job 145's card is the same on the
final tree (time, intervals, region and edge mode compared).

## 5. Limits

- Without a visual-logo card that read the name, a fixed logo whose box the scan's
  consensus dropped stays outside the card's region. Only job 145 shows that drop today;
  keeping the box at scan time needs a text-stage change and a rescan.
- OCR readings in lower-case letters only (no dot, no digit) look like grounding prompts and
  are not used as readings.

## Files

- `export_logo.py`: the window exports of job 145 and the OCR check of the logo's box.
- `reach.py`: the fresh build of every job with a review queue and the cards the rule changes.
- `gate-summary.json`: counts, seconds and source-pixel boxes per job (no text, no paths).
- The jobs and golden gates are `../scrolling-text/gate_rebuild.py` (it now also lists the
  fixed-text cards that took in a logo, and whether a carried BLUR covers their logo boxes).
