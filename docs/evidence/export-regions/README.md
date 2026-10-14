# Exports with many blur regions and the render watch (session H, 2026-10-14)

Branch `fix/export-many-regions` from `main` `a63119b`. Code: `src/biliflow/blur_layout.py`,
`src/biliflow/render_watchdog.py`, `src/biliflow/final_renderer.py`, `src/biliflow/scheduler.py`. All measurements on
this PC with the install's FFmpeg 9.0.1; no film names, file names or links in these files (jobs by id only).

## 1. Why job 134's export hung

- Until now each regional blur split the picture the blur before it had made ("nested" layout): split → crop
  (+ delogo for the cover method) → gblur → mask → alphamerge → overlay, then the next region splits that overlay.
  FFmpeg's work per frame follows the number of paths from the segment's input to its output, and nesting multiplies
  them per region: ×2 for gblur without a feather, ×3 with one, ×4 for the cover method with a feathered edge.
- Job 134 (revision 1): one 10 s opening CUT, then one kept segment with 25 regions (37 blur operations):
  4^25 ≈ 1.1 × 10^15 paths. FFmpeg read 2.2 MB of the source and then spun one core without ever writing a frame.
- Repro before this session (coordinator): job 134's graph cut down to 10 regions ran and 11 did not; synthetic
  nested chains ran up to 12 and hung at 24; a flat layout of 50 regions ran.
- The stall was invisible to the queue: the render stage's heartbeat only says that its process is alive.

## 2. The shared layout (`blur_layout.py`)

- A segment whose nested graph has at most `NESTED_PATH_LIMIT` = 1024 paths (4^5, the most any export had before
  job 134) keeps today's graph character for character.
- A larger one gets the shared layout: one `split=N` per picture version feeds the main line and every branch that
  reads it. A branch reads the earliest picture that already holds every change an earlier blur can make where the
  branch reads, while both are on (time ranges meet within 0.001 s; boxes padded by 4 px for chroma rounding and odd
  overlay positions, plus the cover method's 8 px read margin; a full-frame blur touches everything). No later branch
  reads a picture from before the first regional overlay (its pixel format may still be the source's). The paths
  then add up instead of multiplying: job 134's segment needs 292.
- A segment with more than `EXACT_REGION_LIMIT` = 10 regions has a path budget (`SHARED_PATH_BUDGET` = 4096, or the
  cost of reading the floor picture if that is larger). Past it a branch reads an older picture. See section 4.

### Proof that the frames do not change

| Check | Result |
|---|---|
| Graph text, every plan whose segments stay ≤ 1024 paths (`tests/test_blur_layout.py`, gate below) | identical to `a63119b` |
| Frames nested vs shared forced on the same plan, 150 random plans of 1–10 regions (overlaps crowded in one corner, full-frame blurs, CUTs, feathers 0/2/4, both methods, both feather modes) × 4 source formats (`layout_equivalence.py`, framemd5) | identical in every case both layouts can render. The 3 differences are yuv410p plans where the **nested** graph itself fails in FFmpeg (EINVAL); with 4–20 paths they keep the nested layout in production, as on `a63119b`. |
| `tests/test_export_regions.py::LayoutEquivalenceTests` (2 overlapping plans × yuv420p, yuv420p10le, yuv444p) | identical framemd5 |
| **Byte gate** `scripts/export_regions_gate.py` → [`gate-1-10-regions.json`](gate-1-10-regions.json): 12 plans of 1–10 regions on self-made clips (static noise over testsrc2, 320×180, 3 s, with audio; yuv420p and one yuv420p10le), real `render_final_output` of `a63119b` and of the branch (`2922a0a`, each side checked to import its own code tree), x264 | **all 12 pass**: same mp4 SHA-256 and same framemd5. 7 of them use the shared layout (up to 442,368 paths, 0 older reads). Run twice (before and after the review fixes), both passed; the file holds the run on `2922a0a`. Its `ffmpeg` field was cut to the version number after the run (the banner also named the build vendor's site; the script now records the number only). |

Speed in the gate (3 s clips, one render at a time beside a running scan): the job-134-shaped plan of 10 regions
(786,432 nested paths) took 23.7 s and 34.5 s on `a63119b` in the two runs, 3.8 s and 3.9 s on the branch (86,028
paths); the others are within a second or two.

## 3. Many regions now export

- `tests/test_export_regions.py::ManyRegionsExportTests`: 30 and 60 regions (two film-long strips, subtitle-band
  cards overlapping in place and time, regions with two time ranges, both methods, two CUTs), 640×360 12 s, real
  `render_final_output` with x264: each finishes in a few seconds, passes the full-decode check, and every region
  keeps less than 5 % of the source's fine detail in the middle of each of its time ranges; a region is untouched
  outside its times.
- 1080p (`measure_quiet_stretches.py`, [`quiet-stretches.jsonl`](quiet-stretches.jsonl)): 60 cover regions with
  feathered edges, two CUTs (30 s and 20 s), 70 s of output rendered in 51.6 s on an idle PC.
- The same 60-region test plan with exact reads only (no budget) did not write a frame within 60 s and was stopped by
  the watch: past 10 regions the budget is needed, not just a speed-up.

### How "blurred" is measured

`retained detail` = Σ(s·e) / Σ(s²) over the region, 4 px inside its edge, where s and e are the source's and the
export's pixels less the mean of their 3×3 neighbourhood. It is the share of the source's fine detail still in the
export: 0.945 for a region left alone (x264 loses a little), 0.002 for one cover-method region exported alone and
0.013 for one plain-gblur region exported alone (both in the nested layout, as on `a63119b`); a quarter of a region
left sharp would give about 0.24. The tests require < 0.05. Two simpler measures were tried and rejected: the spread
of the pixel values (the cover method fills a region with a smooth gradient between its edges, which spreads as
widely as the source) and the mean step between neighbours (a blurred edge keeps its height, spread over many small
steps). The correlation was rejected too: an almost flat export can follow the source's one sharp edge with its last
trace of curvature (0.35 correlation while keeping 1.2 % of the detail).

## 4. Pixel differences from the old method

- Plans of at most 10 regions per segment: none (section 2).
- Segments of more than 10 regions never exported before (11 nested regions already hung). Where the budget makes a
  branch read an older picture, that region is still blurred all over, but it blurs a picture without the earlier
  overlapping blur(s) that are on at the same time. A blur spreads what it reads across the region, so its pixels
  may differ from the exact layout anywhere inside its own region (never outside it). One case to know: a plain
  gblur region over a cover-method region (a logo) shows the logo blurred there instead of covered. The export
  manifest counts these regions in `encoding.filter_graph.regions_reading_older_picture`; the random-segment test
  checks, apart from `blur_layout`'s own code, that exactly these regions miss an earlier change.
- Job 134: 0 such regions (its 25 regions read exactly with 292 paths).

## 4a. Known limits

- A segment of at most 10 regions always reads exactly. When its regions form one chain (each overlapping the one
  before while both are on), exact reads save no paths: the gate's 10-region chain has 442,368 paths either way
  (3.7–4.8 s on `a63119b`, 3.6–5.1 s on the branch for 75 frames at 320×180), and a chain of 10 cover regions with feathered
  edges would keep its 4^10 paths. Such an export is as slow as before; the watch never stops it, since its frames
  keep coming.
- `NESTED_PATH_LIMIT` and the budget hold per kept segment; a plan with many CUTs adds its segments' paths up, as
  before.
- A stall caused by NVENC itself (a stuck driver) also fails the stage; "Thử lại bước lỗi" runs the same encoder
  choice again.
- The CUT allowance makes the watch slower to stop a real hang in a plan with a long CUT (a 20 min CUT: 90 min).
- The standalone review UI's export does not remove an old stall record of its output; its next render does.

## 5. Job 134, first 60 s ([`job-134-first-60s.json`](job-134-first-60s.json))

- In `<install>/temp/h-job134`: job 134's `review-queue.json` copied there, `build_edit_plan` and the authorization
  there, then the real `render_final_output` (x264) with job 134's whole filter graph. Only the output was stopped
  after 60 s (`-t 60`, expected duration 60 s), and x264 was held to 2 threads at below-normal priority because a scan
  of the Control Center ran beside it. The source film was only read; nothing was written to the real `output\`,
  `reports\` or `state\`; the temp root was deleted afterwards.
- Result: COMPLETED in 49.5 s, full-decode check passed, layout shared, 292 paths, 0 older reads; stall limit for this
  plan 640 s.
- Placement: the three regions on in the first minute. The film-long logo region keeps 0.0 of the source's detail at
  25 s and 55 s; a subtitle-band card keeps −0.001 and 0.002 while on and 0.966 half a second after it ends; the
  film-long top strip lies on a flat band (detail energy 2.4 per pixel), which cannot show either way.

## 6. The render watch (`render_watchdog.py`) and its threshold

FFmpeg counts as moving while the latest complete block of its `-progress` file changes (frame, out_time, size) or
the bytes it has read grow (psutil). A gap of more than 30 s between two polls (sleep, a frozen process) counts as one
poll. After `stall_limit` seconds without either, FFmpeg is killed and the render stage fails with a Vietnamese
message; there is no NVENC→x264 retry and, in A1b's second lane, no export deferral (the scheduler would hand the
export to the main lane, which would stall again).

`stall_limit = (600 s + 4 s × the plan's longest CUT in seconds) × (2 in A1b's second lane)`.

### The measurement it rests on

`measure_quiet_stretches.py`, results in [`quiet-stretches.jsonl`](quiet-stretches.jsonl): the 1080p export above
(lavfi testsrc2 1920×1080 25 fps 120 s with audio; CUTs 0–30 s and 60–80 s; 60 cover regions; the renderer's x264
arguments: veryfast, crf 20, maxrate, +faststart, `-progress`), sampled every 0.5 s.

| Condition | Render time | Longest stretch without progress | without reads | without either |
|---|---|---|---|---|
| idle PC | 51.6 s | 4.0 s (in the 30 s opening CUT) | 38.3 s | **1.0 s** |
| CPU at 100 %: 4 more x264 1080p encodes (preset medium) beside it | 1065.6 s (20.6× slower, 0.066× real time) | 146.2 s | 705.6 s | **98.3 s** |

- 600 s is about 6× the worst stretch measured under that load (98.3 s).
- The CUT allowance covers the stretch where FFmpeg decodes a CUT without a new frame. On the idle PC the longest
  stretch without progress (4.0 s) was in the 30 s opening CUT; under the load it was 146.2 s, but where it fell was
  not recorded (if it was that CUT: 4.9 s per second of CUT), and the reads kept moving through it. The watch needs
  only one of the two signals; the 4 s per second of the longest CUT, on top of the 600 s, is room for a long CUT
  decoded while the read counter shows nothing new (read ahead, or psutil unable to read it).
- The ×2 for A1b's second lane is a margin for an export sharing the PC with a scan, not a measurement.

### Limits of that measurement

- One PC, one run per condition.
- A synthetic source (testsrc2), not a real film; 70 s of output, not a 2 h film.
- The load was CPU encodes, not a real GPU scan; the second lane (NVENC beside a scan) was not measured, nor NVENC
  at all.
- The `+faststart` rewrite of a multi-GB output (after the last frame: reads and writes, no progress) was not
  measured at that size; it counts as moving through its reads, and if psutil cannot read the counters it must finish
  within the limit.
- Sleep and suspension are handled by the gap rule but were not measured.
- The watch was checked to stop a real hang: `tests/test_render_watchdog.py` stops real FFmpeg in a graph of 32
  nested regions (the shape of job 134) and leaves alone a real render that runs past its limit in real time.
