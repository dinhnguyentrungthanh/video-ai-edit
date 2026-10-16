# A site watermark read in many runs reaches the film's edges (2026-10-16)

Branch `fix/watermark-edges` from `main` `41d219d`. Review build only: no scan stage's cache key changes.

## Why

On job 134's real "Chạy lại" (revision 2, built on G13), the subtitle-band watermark card ended at
2,643.5 s of a 2,657.2 s film.
- In the source frames at 2,645, 2,650 and 2,655 s, the mark sits fully inside the card's box and is
  clearly visible: the end credits (the last scene) cover it from about 2,640 s, so OCR stopped reading
  it, but it never left.
- The export showed it for the last 14 s. The review page cannot extend a card.
- Jobs 130 (2,646.5 / 2,655.2 s) and 136 (2,691.5 / 2,702.2 s) were the same; 143 and 145 already
  reached the end.

## The rule (`src/biliflow/subtitle_band.py`)

A `repeated_watermark` group read in `WATERMARK_ROBUST_RUNS` (10) runs or more:
- a first moment `WATERMARK_BRIDGE_SECONDS` (150 s) or closer to the scan's start starts at the scan's
  start;
- a last moment that close to the scan's end reaches the end: `scan_start + scan_duration`, or the
  film's duration when the scan reaches past it. With neither known, the end is not reached.

This is the reason behind joining runs up to 150 s apart: the scan missed the mark, the mark did not
leave. The distance is measured the same way, between the moment's edge and the scan's edge.

Also:
- separate moments keep their gaps;
- a group of fewer runs keeps its time;
- the card records `repeated_watermark.read_span`, the time it was read in;
- the reason says which edge the card reached.

## The decision carry (`src/biliflow/review_decision_carry.py`)

The gate's rebuild of job 134's real queue (keep decisions) found that the longer card got a new id and
lost the user's BLUR.
- Two other decided cards matched it as "the same thing" (time and box overlap over the smaller card):
  - the user's KEEP on a 2-run watermark group in a larger box;
  - a KEEP on a 6.5 s subtitle line whose box holds the mark.
- Both still had their own unchanged cards, but they disagreed with the BLUR. The longer card stayed
  open, and the old BLUR card came back beside it: 26 cards instead of 25.

Two changes:
- **An old card whose own card is still there decides only that card.** "Its own card" means the same
  id (and the same moments for a main card), or the same id in the optional list. Such a card no longer
  votes on other new cards. This can only leave a card open, never invent a decision.
- **A BLUR of separate moments is carried** when each old moment lies inside a new one. Before, the old
  card's whole span had to lie inside a single new moment (a code-review finding: a grown last moment
  left the old card beside the new one).

## Other edges (read-only, the active queues of 77 jobs)

- **Film-long fixed-text cards** (`whole_film_text`, `recurring_fixed_text` with `whole_film`) are built
  from the scan's start to its end already: 39 of 42 long persistent-overlay cards span edge to edge.
  The 3 others:
  - two visual-logo groups that start at 8.5 s and 15 s (jobs 112 and 117; their sources are deleted, so
    nothing can be checked);
  - one recurring line over part of a film (job 152, 15–1,239.5 s, KEEP by the user).
- **Scrolling lines** get one card per crossing; a crossing that leaves the frame is not this case.
- **No evidence** of the same early stop elsewhere, so only `repeated_watermark` changes.

## Gates (`edge_gate.py`, `edge-gate-summary.json`; before = 41d219d, after = this branch)

*Tests:*
- 14 new tests: 11 in `tests/test_watermark_edges.py` and 3 in `tests/test_review_decision_carry.py`.
- 11 of them fail on 41d219d. The 3 that pass are guards: an edge more than 150 s away, a group of fewer
  runs, and old cards without their own card still disagree.
- 515 tests of 30 review modules pass.
- Full suite: 3,513 tests OK, 53 skipped, with a 1 s synthetic clip in the worktree's `input\`
  (removed after).

*The latest real reports* (active revisions: 130 r2, 134 r2, 136 r3, 143 r3, 145 r3, all rerun on
2026-10-16):

| Job | Band watermark card before → after | Film | Cards | Rebuild (keep decisions) |
|---|---|---|---|---|
| 130 r2 | 0–2,646.5 → 0–2,655.167 | 2,655.167 | 30 → 30 | 30; nothing decided yet |
| 134 r2 | 0–2,643.5 → 0–2,657.236 | 2,657.236 | 25 → 25 | 25; 0 of 24 lost; the BLUR is on the longer card |
| 136 r3 | 0–2,691.5 → 0–2,702.207 | 2,702.207 | 26 → 26 | 26; nothing decided yet |
| 143 r3 | 0–2,668.116 (unchanged) | 2,668.116 | 22 → 22 | 22; 0 of 22 lost |
| 145 r3 | 0–2,561.172 (unchanged) | 2,561.172 | 27 → 27 | 27; 0 of 27 lost |

- Only the edge cards change: the watermark card in 130, 134 and 136, plus 130's visual-logo card of the
  same mark, which takes the watermark card's time (`fixed_text_logo`).
- Groups of fewer runs keep their time: 134's 2-run group (186–2,589.5 s, KEEP) and 130's (561–1,431.5 s).

*The 25 jobs of the subtitle-band gate* (stored reports, active revisions):
- cards equal;
- subtitle-band ad tracks cited by a main card: weakened 0;
- changes only in 130, 134 and 136.

*Rebuilds by both trees* (the 10 jobs whose source is still there):
- the same card counts and the same decision counts;
- changes only in the edge cards of 130 (2), 134 (1) and 136 (1);
- 0 decisions lost by either.
- Job 108's rebuild has one card more than the user's queue with both trees. That is older code, not
  this branch.

*Tail export of job 134* (`../text-consensus/export_tail.py --job 134 --before-queue … --queue …`):
- The last 30 s (2,627.2–2,657.1 s), one BLUR on each band watermark card of the 41d219d build and of
  this branch's build, real `render_final_output` (libx264).
- EasyOCR (CPU) reads the cards' padded box every 0.5 s: 60 samples.

| Samples | Source: readable / web address | 41d219d export | This branch's export |
|---|---|---|---|
| 2,643.5 s to the end (27) | 26 / 3 | 27 / 4 | **0 / 0** |
| 2,627.2–2,643.5 s, both cards blur (33) | 21 / 5 | 7 / 0 | 7 / 0 (the same readings) |

The 7 readings before 2,643.5 s are equal in both exports and none is an address. They are other text
(the end credits) in the padded union box, which includes the 2-run group's larger box.

*Golden Set v2* (item 6's rescanned text reports, no memories):
- the scorecards before and after are identical: 598 items, advertising 19/19, false positives 24,
  advisory 487, traps 0;
- against the stored v2 baseline: +1 caught (G11), 0 missed, 0 traps;
- `compare` exits 1 only for its time gate.

*Cache keys:* all 10 stages equal to 41d219d.

## Limits

- **Unread time is blurred.** Up to 150 s of time the scan never read gets the mark's blur at each edge,
  on the mark's own box only. A mark that really leaves earlier than that (fewer than 150 s before the
  end) gets a blur on an empty box. The card is still suggested only and the user decides.
- **The 10-run threshold.** A mark read in fewer than 10 runs keeps its time. It may still stop short of
  an edge.
- **A conflict can still leave the card open.** A decided card in the newly covered head or tail that
  has no card of its own any more still disagrees with the watermark's BLUR. The card then stays open
  and the user decides it again; nothing is lost.

## Tools

- `edge_gate.py`: the job and golden gates above.
- `../text-consensus/export_tail.py`: new `--job`, `--queue` and `--before-queue`. Its defaults (job 145,
  `limit_gate.py`'s queue, the user's own queue) stay item 6's.
