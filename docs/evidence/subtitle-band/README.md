# The site watermark in the subtitle band, and subtitle lines read as ads (fix/ticker-subtitle, part 2, 2026-10-15)

Branch `fix/ticker-subtitle` (session D), after part 1 (`docs/evidence/scrolling-text/`). Code:
`src/biliflow/subtitle_band.py` (new), `src/biliflow/ad_text_patterns.py` (the ad-token patterns moved unchanged out
of `review_workflow`), `src/biliflow/review_workflow.py` (the text branch of `build_review_queue`, `_text_items`,
`_advisory_text_items`, `_merge_items`, `reconcile_persistent_overlay_items`), `src/biliflow/scrolling_text.py`
(`promote_web_address_tracks` leaves a track that already names its address). Review-build code only: no scan
stage's cache key covers it, so old jobs need no rescan. Jobs by id only; the site is `EXAMPLE.COM` here. Results
with real readings stay in `reports/evidence/ticker-d/` (Git-ignored, this PC only).

## 1. What happened (jobs 130, 134, 136, 143, 145)

The source site's address sits just under the subtitles, about 130 × 27 analysis pixels in the middle of the frame.
The text scan reads it in runs with gaps (a subtitle line covers it, OCR misreads a letter: the name comes out in
several spellings, with or without its dot), so one film gave 47–64 tracks of it. Before part 1 most of them were
scene text or optional; part 1 made each one a main card suggesting BLUR (134: 1 → 51 BLUR suggestions in the bottom
band). Cards close in time then merged with the subtitle cards between them into boxes over the dialogue (job 143:
the user blurred boxes up to 715 × 87 source pixels). The user's exports still show the address: in the first 100 s
of job 134 OCR reads it at 33 of 180 samples. Single dialogue lines scored as uncertain ad text also filled the main
list.

## 2. The rule (`subtitle_band.py`)

- **Repeated watermark.** Tracks with a web address (`web_address_tokens`, part 1) or an address the user blurred
  before (`ad_text_memory`), not a fixed overlay, title overlay or scrolling line, are compared by their place
  (IoU ≥ 0.5 with the group's per-edge median box when the track joins) and their folded address (one contains the
  other, or 0.75 similar). A group of at least two becomes one track: `candidate_type` `repeated_watermark`,
  `temporal_policy` `discrete_detected_intervals`, suggested BLUR, every track cited, up to six previews spread over
  the runs. Its moments: from one read interval before each run to its blur end; runs that touch join, and in a
  group of 10 runs or more, runs at most 150 s apart join (section 4). Its box: the union of the runs' boxes; from 10
  runs on, each edge's 10th/90th percentile, so one OCR box grown over the line above does not widen every moment.
  One BLUR edits each moment in that box. The card is never merged with another card (`_merge_items`) nor absorbed
  by a fixed overlay's card (`reconcile_persistent_overlay_items`; in job 130 it was before this rule). A general
  rule: nothing names a site.
- **Subtitle lines.** A track of the subtitle zone read at most twice (a track whose report does not say is not),
  routed exactly `REVIEW_UNCERTAIN`, centred (box centre within 15 % of the width from the middle), not persistent,
  in a film with at least 20 subtitle-zone tracks read at most twice, and with no ad evidence, gets
  `review_candidate: false` and `subtitle_line`: it is shown among the optional cards ("Giống một dòng phụ đề; không
  chặn xuất video", suggested KEEP) instead of the main list.
- **Never weaker in the subtitle band.** Any ad evidence keeps a line's main-list card exactly as before: a web
  address (the part 1 rule and the older ad-token patterns, which also read "name . vn" or "xem tv"), a remembered
  address, a phone number, an @handle, a policy hit, "advertisement" as the text semantics' first label, an ad score
  of 0.40 or more (REVIEW_AD_LIKELY starts at 0.45), or any routing other than `REVIEW_UNCERTAIN`. The ad label and
  score were added after a read-only review: without them a centred dialogue-shaped ad without an address, scored
  just under the ad cutoff, would have moved. The unit tests hold one line of each kind.

## 3. Groups and moved lines on every text report of the install

Through the same steps as the build (fixed-overlay promotion, scrolling lines, then this rule), on all 72 text
reports:

| Group | Runs | Moments | Box (analysis px) | Lowest IoU with the final median box |
|---|---|---|---|---|
| job 130, subtitle band, the site's address | 49 | 1 (0–2247.5 s) | 414,333–546,358 | 0.68 |
| job 130, a subtitle line with an address read twice | 2 | 2 (561–1431.5 s) | 315,307–649,357 | 0.73 |
| job 134, subtitle band | 64 | 1 (0–2427.5 s) | 413,332–546,358 | 0.62 |
| job 136, subtitle band | 60 | 1 (0–2436.5 s) | 413,332–546,359 | 0.55 |
| job 143, subtitle band | 47 | 1 (0–2361.5 s) | 413,352–546,379 | 0.68 |
| job 145, subtitle band | 49 | 1 (0–2313.5 s) | 413,352–546,379 | 0.67 |

No other group forms. The top-band lines with the address in these jobs already are a film-long fixed-overlay card
suggesting BLUR (a step before this rule). At IoU 0.4 one 2-piece group of job 129 (0.48) would also join; a box
holding the mark and a subtitle line, or a wider line around it, overlaps the mark by 0.1–0.4 (unit tests hold one
at 0.36 and one at 0.40). The subtitle-line rule moves 123 tracks on the 72 reports, 119 of them in these five jobs.

## 4. Is the mark still on screen between its runs? (`gap_check.py`)

Before the 150 s rule each run was its own moment: they covered 51–63 % of each mark's span, the gaps' median was
8.5–11.5 s and the longest 68.5–131.5 s. OCR on the source, in the card's box, at the middle of each moment (control)
and 1.5 s after a moment, mid-gap and 1.5 s before the next one:

| Job | Middle of a moment readable | Gaps 0–6 s | 6–12 s | 12–30 s | 30–60 s | over 60 s |
|---|---|---|---|---|---|---|
| 134 | 44 of 63 | 10 of 25 gaps | 7 of 13 | 9 of 14 | 3 of 6 | 4 of 4 |
| 143 | 35 of 46 | 15 of 21 | 1 of 3 | 10 of 12 | 2 of 5 | 4 of 4 |
| 136 | 42 of 58 | 11 of 24 | 8 of 11 | 11 of 13 | 7 of 7 | 2 of 2 |
| 145 | 34 of 49 | 9 of 16 | 1 of 9 | 8 of 11 | 7 of 9 | 1 of 3 |
| 130 | 32 of 48 | 11 of 18 | 4 of 6 | 4 of 10 | 6 of 7 | 3 of 6 |

(gaps in which at least one sample reads the mark; the web address itself is read in gaps over 60 s in all five
films). The scan missed the mark there, it did not leave. Hence the 150 s rule for a mark read
10 times or more: the five marks become one moment each, from before the first run to after the last. A line read
twice (job 130, 870 s apart) keeps its two moments. The user asked for one card with several intervals; the card
has them where the mark really leaves (a gap over 150 s), and the window export below shows why one is right here.

## 5. Gates

| Gate | Result |
|---|---|
| Unit tests `tests/test_subtitle_band.py` (15): one group of misread runs and its moments, a box holding the mark and a subtitle, a wider line around it, other sites and places, single sightings, remembered addresses, fixed overlays and scrolling lines left alone, an outlier box, the 150 s rule, subtitle lines moved, every kind of ad evidence kept (address, phone, @, policy hit, remembered, "name . vn", ad routing, ad label, ad score 0.44), off-centre / repeated / persistent / top-band / unknown-count lines kept, a queue with one watermark card and optional lines, one BLUR editing only its moments in its box, a fixed overlay not absorbing the card | OK (the fixed-overlay test fails without the new exclusion) |
| 17 review test modules (`test_review_workflow`, `test_scene_cards`, `test_scrolling_text`, `test_ad_text_memory`, `test_review_decision_carry`, `test_dashboard_v2_review` …) | 407 OK |
| Jobs gate (`gate_rebuild.py jobs`, the 25 jobs of part 1) | cards, bd0a7b8 → branch: 134 42 → 6, 143 64 → 22, 136 38 → 9, 145 50 → 8, 130 9 → 8; the other 20 jobs exactly as after part 1. One watermark card in 134, 136, 143, 145, two in 130. Suggested BLUR in the bottom band never fewer (134 1 → 2, 136 1 → 2, 143 0 → 1, 145 0 → 1, 130 1 → 3, the others equal) |
| Ad tracks in the subtitle band (every subtitle-zone track with a web address, phone number, @handle or policy hit) | cited by a main-list card, bd0a7b8 → branch: 134 50 → 65 of 66, 143 45 → 47 of 47, 136 47 → 60 of 60, 145 47 → 50 of 50, 130 46 → 52 of 52; **weakened (cited before, not now) 0 in all 25 jobs** |
| The user's decisions on the moved lines (130, 134, 136, 143, 145) | 33 cards KEEP; 12 BLUR cards each also held the watermark, which stays in the main list in its own card; no BLUR or CUT card made only of moved lines |
| Rebuild keeping decisions (copies of the user's queues) | lost 0 everywhere. The watermark card waits for one decision in 134, 136, 143, 145: the user blurred most runs but kept at least one merged box that also held a subtitle line (134: 1, 143: 3), so
the old cards disagree (`decision_conflict`) and every old card stays as it was. In 130 the old BLURs carry onto it |
| Window export, first 100 s of jobs 143 and 134 (`export_watermark.py`: the user's own operations "before", one BLUR on the watermark card "after"; real `render_final_output`, libx264; OCR every 0.5 s on the mark's box and on the 72 source rows above it) | all four exports COMPLETED, full decode passed. Mark readable at 180 samples: 143 source 116, before 15, after 0 (web address 74 / 2 / 0); 134 source 111, before 55, after 0 (web address 59 / 33 / 0). Subtitle rows readable: 143 source 69, before 21 (the merged boxes blurred them), after 72; 134 has no dialogue in those rows in the window (1 / 1 / 1). Before the 150 s rule the same check still read the mark in 11 of 26 gap samples of job 134 |
| Golden Set v2 (`gate_rebuild.py golden`) | newly caught 1, newly missed 0, new trap hits 0 (against the baseline tree and the v2 tag); advertising: 19 of 19 caught in the main list, regions 19/19, suggestions 19/19; main-list false positives 24 → 24, optional cards 487 → 487. No golden queue has a watermark card; 3 subtitle lines move there |
| Scan cache keys (`build_code_manifest` of both trees) | unchanged for all 10 stages |

## 6. Limits

- `golden_scoring.scene_moments` treats only scene cards as separate moments; a watermark card with several moments
  would be scored over its whole span. No golden queue has one today.
- A rebuild over the same queue (`preserve_unresolved_review_items`) brings back old undecided cards it finds no
  equivalent for among the main cards, so an old undecided subtitle-line card returns to the main list (and also
  shows among the optional ones). It never removes a card; a fresh queue ("Chạy lại") has neither.

## Files

- `export_watermark.py`: the window exports and the OCR check.
- `gap_check.py`: the mark's box read in the source between its moments.
- `gate-summary.json`: card counts, seconds and OCR sample counts per job (no text, no paths).
- The jobs and golden gates are `../scrolling-text/gate_rebuild.py` (part 2 adds the watermark cards, the moved
  lines and the subtitle-band ad tracks to its summaries).
