# Cover regions at the right and bottom frame edge (fix/cover-edge, 2026-10-14/15)

Branch `fix/cover-edge` from `fix/export-many-regions` `11a059c`. Commits: `5cb52c4` (the fix), `20584f3` (review: the
crop step of each source pixel format, as measured), `e8b52d9` (review of `20584f3`: sources FFmpeg turns, review
previews, uyyvyy411). Code: `src/biliflow/blur_filter.py`, `src/biliflow/blur_layout.py`,
`src/biliflow/final_renderer.py`, `src/biliflow/review_workflow.py` (`render_edit_previews`); the gate and
`scripts/benchmark_export_steps.py` build the same graph. All measurements on this PC with the install's FFmpeg 9.0.1;
jobs by id only, no film names, file names or links in these files.

## 1. Why job 141's export failed on every try

- The cover method (`delogo_blur_v1`) crops the region with up to 8 px of picture around it, fills the region with
  delogo, crops back to the region, blurs it and lays it back. delogo refuses a rectangle that touches the edge of
  its picture, so the chain keeps it one pixel inside the crop.
- FFmpeg's crop rounds its size (and its corner) down to whole chroma samples of the picture it crops. Job 141's
  region x=583 y=35 w=697 h=49 touches the right edge of its 1280 × 534 yuv420p frame: the chain asked
  `crop=705:65:575:27`, got 704 × 64, and `delogo=x=8:y=8:w=696:h=49` ended on the cropped picture's last column.
  FFmpeg refused the graph while setting it up ("Logo area is outside of the frame", exit −22), before any frame,
  so "Thử lại" could never pass.

## 2. What FFmpeg 9.0.1 does (measured)

Probes of 1–3 frames, at most 1280 × 534, at below-normal priority.

- **Rounding.** crop rounds width and x to the picture's horizontal chroma step, height and y to its vertical one
  (4:2:0: 2 × 2; 4:2:2: 2 × 1; 4:4:4, gray, RGB: none; 4:4:0: 1 × 2; 4:1:1: 4 × 1; 4:1:0: 4 × 4).
  `tests/test_cover_edge.py::CropRoundingModelTests` keeps checking it on FFmpeg itself.
- `crop … :exact=1` does no rounding. At an odd x it garbles the chroma of a 10- or 16-bit subsampled picture (it
  steps the chroma plane by an odd number of bytes); at an even x it is exact. 8-bit formats are fine either way.
- delogo accepts its rectangle only when x ≥ 1, y ≥ 1, x + w ≤ W − 1 and y + h ≤ H − 1 of the picture it gets, for
  every pixel format, checked once while the graph is set up (`enable` does not help).
- With today's margins this gives: a cover chain is refused exactly when `max(margin, 1) ≤ crop size mod step` on
  the right or bottom (margin: picture past the region, at most 8, 0 without a frame size). For 4:2:0: an odd crop
  size with 0 or 1 pixel past the region; for 4:1:0 up to 3 pixels. 384 of 384 runs matched this rule.
- overlay lays a patch at x and y rounded down to even (debug log `x:257 xi:256`), for every source format: the
  main picture is 4:2:0.
- **Which picture each branch crops** ([`crop-steps.jsonl`](crop-steps.jsonl), [`measure_crop_steps.py`](measure_crop_steps.py):
  every pixel format FFmpeg can make, through the export's own graph with `-pix_fmt yuv420p` like the export, the
  first crop's size read from FFmpeg's log; earlier, 2,518 crops of `-print_graphs_file` for 9 source formats, nested
  and shared layouts alike):
  - a kept segment's **first regional branch** crops the **source's own format**, except packed 4:2:2 (yuyv422,
    uyvy422, yvyu422, y210le, y212le, y216le), uyyvyy411 and monow/monob, which are converted to 4:2:0 before the
    crop;
  - when a **full-frame blur comes before that branch** in the segment's graph, it crops the source's own format
    only if gblur takes the format (49 formats: planar YUV, gray and planar RGB in little-endian, not every depth;
    `blur_filter._KEPT_THROUGH_GBLUR`); every other format (RGB, big-endian, semi-planar, packed 4:4:4, pal8,
    yuv440p10le …) reaches it as 4:2:0;
  - **every later branch** crops the first regional overlay's output: **4:2:0** whatever the source (overlay's
    default format is yuv420);
  - the renderer orders a segment's blurs by start, the longer first on ties (`final_renderer._prune_redundant_blurs`):
    a full-frame blur comes before a region when it starts earlier;
  - a full-frame blur in one kept segment changes nothing in the next one (each segment reads the source anew).
- **A source with a display matrix** ([`crop-steps-transformed.jsonl`](crop-steps-transformed.jsonl),
  `--mode transformed`: every format on a 160 × 160 clip carrying a display matrix of 90°, 180°, 270°, 30°, 0.4°,
  1°, −1°, 2° or 89.6°, an hflip or a vflip). The export leaves FFmpeg's autorotate on. fftools rounds the
  matrix's angle to whole degrees (`get_rotation`, in [−0.9, 359.1)) and puts transpose (90, 270), hflip and/or
  vflip (180), rotate (beyond 1) or vflip (a flipped matrix at 0) in front of the graph; then the first regional
  branch crops **4:2:0** in every measured format, alone and after a full-frame blur. Where it puts nothing
  (0.4°; −1° reads as 1; a matrix that only moves or scales the picture), the branch crops the source's own
  format as without a matrix. 1° reads as 359 and is rotated. Every measured run matched that rule. ffprobe's
  `rotation` field is no guide (0 for a vflip and for ±1°), nor is a `rotate` tag, which FFmpeg 9 does not read.
- **A review preview** ([`crop-steps-preview.jsonl`](crop-steps-preview.jsonl), `--mode preview`: the command of
  `review_workflow.render_edit_previews`). `-ss`/`-t` before `-i` make FFmpeg put a trim in front of the graph, and
  its branch crops **4:2:0** in every measured format (232 of 232).

| First branch: alone → after a full-frame blur | Formats (220 measured) |
|---|---|
| 1 × 1 → 1 × 1 | 31: yuv444p, yuvj444p, yuva444p, gray, gbrp, gbrap and their little-endian 9–16-bit and f32 forms |
| 2 × 1 → 2 × 1 | 12: yuv422p, yuvj422p, yuva422p and their little-endian 9–16-bit forms |
| 1 × 2 → 1 × 2 | yuv440p, yuvj440p, yuv440p12le |
| 4 × 1 → 4 × 1; 4 × 4 → 4 × 4 | yuv411p, yuvj411p; yuv410p |
| 2 × 2 → 2 × 2 | 36: 27 formats of 4:2:0 (planar, nv12/nv21, p010/p012/p016) and the 9 converted before the crop (packed 4:2:2, uyyvyy411, monow, monob) |
| 1 × 1 → 2 × 2 | 114: packed RGB, big-endian 4:4:4/gray/GBR, nv24/nv42, p41x, packed 4:4:4, msb forms, pal8 (from paletteuse), half-float and f32 RGB/gray/ya (from raw bytes) … |
| 2 × 1 → 2 × 2 | 18: big-endian 4:2:2, nv16, nv20, p21x |
| 1 × 2 → 2 × 2 | yuv440p10le, yuv440p10be, yuv440p12be |

Formats swscale cannot make came from raw bytes (`rawvideo`, 17 of the 220, uyyvyy411 among them). Not measured
(47): 16 hardware formats; 12 bayer formats, on which FFmpeg itself fails at the first crop alone (a swscale
assertion; after a full-frame blur they crop 2 × 2, as the model says); and 19 that FFmpeg cannot convert to the
export's yuv420p output (exit −40: bgr4, rgb4, big-endian packed 10/12/16-bit, 32-bit float and integer RGB/gray),
so no export of them runs at all.

## 3. The fix

- `blur_filter.SourceChroma.of(pix_fmt)` (`20584f3`): the step of a segment's first regional branch, alone
  (`first`) and after a full-frame blur (`after_full_frame`), as in the table. A source without a probed format
  (`None`, empty, `unknown`) gets no rounding: its first branch's cover chain stays the one of `11a059c`.
- `blur_filter.SourceChroma.of_stream(video_stream)` and `display_transform` (`e8b52d9`): the probed stream's first
  display matrix decided as fftools decides it (above); a matrix FFmpeg applies gives 4:2:0, any other stream
  `of(pix_fmt)`. A matrix the probe does not print as 9 numbers, or an applied one on a stream of unknown format,
  gets no rounding.
- `blur_layout.branch_chroma_steps`: the first regional branch of a segment uses `after_full_frame` when a full-frame
  blur comes before it in the graph, `first` otherwise; every later branch 4:2:0 (both layouts). `final_renderer`
  passes `SourceChroma.of_stream` of the probed video stream; the review previews crop with `CHROMA_420`
  (section 2).
- `blur_filter._cover_patch` (`5cb52c4`): where delogo's rectangle fits the crop FFmpeg will make, the chain is the
  old one, character for character. Where FFmpeg refused it, the region is cut and laid from the even pixel left of
  and above it (at most 1 px more on those sides, inside `blur_layout.EDGE_PIXELS`), both crops get `exact=1` (so
  nothing is rounded off, and the corner is even for 10-bit chroma), and the overlay lands where the patch was cut.
  The patch reaches the region's right and bottom, so a region touching the frame's edge is blurred up to its last
  column or row. A side of such a patch on the frame's edge is not faded by the feather (there is no picture
  beyond it to fade into; faded, its outermost pixels would show the source, which delogo cannot clean).
- Review of `5cb52c4` (fixed in `20584f3`): it counted every format but planar YUV, gray and planar RGB as 4:2:0.
  An rgb24 source crops its own format, so a region that exported on `11a059c` (an odd crop 1 px from the edge,
  gate plan `edge-rgb24-right-1px-odd`) would have been cropped exactly: other bytes. Graph text of the 23 gate
  plans: `5cb52c4` differs from `11a059c` on that plan; `20584f3` on none.
- Review of `20584f3` (fixed in `e8b52d9`), each finding checked on FFmpeg:
  - a source FFmpeg turns crops 4:2:0, but `20584f3` rounded it by its own format: a rotated yuv411p region that ran
    would have been cropped exactly (other bytes; gate plan `edge-411-rotated-180`), and a rotated yuv422p region
    refused at 4:2:0 kept the refused chain (`fixed-rotated-422-bottom`);
  - the review previews put a trim in front of the graph and crop 4:2:0, but `20584f3` rounded them by the source's
    format, so a preview of a 4:4:4 source could be refused (`PreviewExportTests`);
  - uyyvyy411 is converted to 4:2:0 before the crop (measured from raw bytes);
  - the first draft of this fix counted every matrix that is not the identity (and a `rotate` tag) as a turn: a
    4:4:4 source whose MP4 track matrix only moves or scales the picture, or that rotates it by less than 1°, would
    have been cropped exactly where FFmpeg ran the old chain (other bytes). `display_transform` now follows fftools.

## 4. Gates (on `e8b52d9`)

| Check | Result |
|---|---|
| Graph text: every cover chain FFmpeg ran on `11a059c` (the chain of `11a059c` copied into the test; 4 frame sizes incl. odd and none, 6 steps, regions 0–4 px from the right and bottom edge, odd and even sizes and corners) | identical (`CoverChainTextTests`, > 400 cases) |
| **Byte gate** `scripts/export_regions_gate.py` → [`gate-cover-edge.json`](gate-cover-edge.json) (2026-10-15, `e8b52d9`, clean `src`): session H's 12 plans plus 12 near-edge plans that ran (even and 2-px-odd at the right edge, bottom and corner, gblur at an odd corner, an odd region at the edge of a yuv444p source, an odd-height one at the bottom of a yuv422p source, yuv410p (FFV1 clip) on whole chroma samples, 10-bit, the shared layout, an odd crop 1 px from the edge of an rgb24 source (PNG clip), a 10-bit 4:2:2 region after a full-frame blur, and a crop of 38 columns 1 px from the edge of a yuv411p source turned by 180° (FFV1 clip with a display matrix)); each side's graph built from a probe of its copied source, as `render_final_output` builds it; real `render_final_output` of `11a059c` and of the branch, x264 | **24/24**: same mp4 SHA-256, same framemd5, same graph text (8 in the shared layout) |
| Plans `11a059c` refused, same gate (`fixed`): job 141's shape, 1 px from the right edge, odd height at the bottom, a corner without feather, a later branch on a yuv444p source, yuv410p 2 px from the bottom, 10-bit, the shared layout, an rgb24 source after a full-frame blur, a yuv422p source turned by 180° 1 px above the bottom | **10/10**: `11a059c` refused by delogo; the branch exports (COMPLETED), crops exactly, keeps 0.0013 or less of the source's fine detail inside each region and 0.0094 or less on the frame's last column or row, while an untouched box keeps 0.89–0.94. (A fixed plan the baseline exports after all must keep its bytes; none did.) The two turned plans tell `20584f3` from `e8b52d9`: on `20584f3` the yuv411p one gets other graph text and the yuv422p one stays refused. |
| `tests/test_cover_edge.py` (31 tests): the model against all three tables (220 formats; 2,526 runs with a display matrix; 232 previews); `of_stream` (identity, moved, scaled, small-angle and turned matrices, rotate tags, unreadable matrices); real FFmpeg on the export's own graph for yuv420p, yuv422p, yuv444p, yuv410p × right and bottom edge × odd and even size and corner × 0, 1 and 2 px from the edge × feather 0 and 2 × first and later branch × nested and shared, `11a059c`'s graph run beside it in every case (it ran exactly where the model says); rgb24, yuv440p10le, yuv422p10le and nv16 alone and after a full-frame blur; a full-frame blur in one kept segment only; job 141's region on a 1280 × 534 frame; 10-bit chroma equal to 8-bit; clips turned by 180° and flipped (yuv411p keeps the chain that ran, yuv422p refused before now exports blurred); an MP4 whose track matrix only moves or scales and a NUT with a rotate tag (the source's own step); a real preview of a 4:4:4 source (exports; the chain of `20584f3` is refused); unknown formats | OK. Every refused region now runs, is blurred inside and on the frame's last column/row, and no pixel changes outside the region and the even pixel left of and above it. Run on the code of `20584f3`, the new tests fail on every finding of its review. |
| Focused suites: cover_edge, blur_filter, blur_layout, final_renderer, export_regions, cache_dependencies, render_graph_file, export_guards, export_identity, render_watchdog, export_encoder, export_lanes, review_workflow, skip_export | 382 tests OK |
| Scan cache keys ([`cache_keys.py`](cache_keys.py)): the files each of the 10 scan stages hashes, `e8b52d9` against `11a059c` | none of them changed; same bytes for every stage |

How "blurred" is measured: `docs/evidence/export-regions/README.md`, section 3 (the share of the source's fine
detail the export keeps). On a line of pixels at the frame's edge the neighbourhood is the two neighbours along the
line.

## 5. Job 141, 2140–2170 s ([`job-141-2140-2170.json`](job-141-2140-2170.json))

Scripts: [`job141_prepare.py`](job141_prepare.py) (job 141's `review-queue.json` copied into
`<install>/temp/ce-job141`, `build_edit_plan` and the authorization there), [`job141_check.py`](job141_check.py),
[`job141_raw.py`](job141_raw.py). The source film was only read; nothing was written to the real `output\`,
`reports\` or `state\`; the temp root was deleted afterwards. Job 141's plan has one operation: this region, cover
method, feather 2, on 2151.0–2157.5 s; no CUT. Source yuv420p without a display matrix, so `20584f3` builds the
same graph as `5cb52c4`, and `e8b52d9` the same whole-film graph as `20584f3` (text compared on the probed film).

- The whole film's graph through FFmpeg on the source for 1 s: `11a059c` refused by delogo; the branch runs
  (`crop=706:66:574:26:exact=1,delogo=x=8:y=8:w=697:h=50,…,crop=698:50:8:8:exact=1`, overlay at 582:34).
- The real `render_final_output` on a lossless copy of 2140–2170 s (x264 qp 0, yuv420p) with the blur moved by
  −2140 s: `11a059c` fails; the branch COMPLETED (6.2 s on `5cb52c4`, 5.4 s on `20584f3`), full-decode check passed.
  The export of `20584f3` (two runs) is byte-identical to the one of `5cb52c4`.
- The export against the copy, while the blur is on (2152.0, 2154.25, 2156.5 s): the region keeps 0.0 of the
  source's detail, and so do the frame's columns 1272–1279 (rows 36–81) where the source has detail there (2154.25 s:
  column 1279 has 119 units of detail per pixel, keeps −0.003). 3 s after the blur the region keeps 0.98.
- The graph's own output (no encoder) against the copy: the changed pixels fill exactly x 582–1279, y 34–83 (the
  region and its even pixel left of and above it, up to the frame's last column); 0 changed pixels elsewhere. Frame
  column 1279 changed on 46 of 46 rows at 2152.0 and 2154.25 s (35 of 46 at 2156.5 s, where the source is flat there).

## 6. Known limits

Plans that ran keep their bytes, so these behaviours of `11a059c` stay for them:

- A feathered region that touches the frame's edge keeps the frame's outermost column or row as the source's
  pixels (the feather fades to 0 at the patch's border, and delogo cannot clean it).
- A region with an odd x or y or an odd size (cover and plain gblur) is cut from the corner rounded down and laid
  at x and y rounded down to even: its last one or two columns or rows are not blurred, at the frame's edge too
  (job 141's region as plain gblur would leave columns 1278–1279 and rows 82–83). For a yuv410p source the cut and
  the overlay differ by up to 2 px.
- Fixing these for every region would change the bytes of plans that ran: a decision for the user.

In the fix itself:

- A refused region whose side is not on the frame's edge still fades there over the feather; where that band meets
  the frame's edge (the first and last 1–2 rows of the frame's last column) the outermost pixels fade into the
  source's, which delogo cannot clean.
- The step after a full-frame blur was measured with the export's output format (yuv420p). The 62 formats FFmpeg
  cannot make a picture of (hardware, bayer, half float …) follow the same rules unmeasured; exports decode in
  software, and none of them is what a software decoder gives for a usual video file.
- Not measured: NVENC exports (same graph), sources with an odd frame size in a real export, hardware-decoded sources
  (exports use none).
- A display orientation carried in the H.264/HEVC bitstream (an SEI) is applied by FFmpeg but not shown by
  `ffprobe -show_streams`: such a source that is not 4:2:0 keeps its own step, so a region FFmpeg refuses there is
  refused as on `11a059c` (no changed bytes; seen by the review on a self-made clip).
- A source turned by 90° or 270° reaches the graph with width and height swapped, but the renderer and the
  previews take the probe's coded size (`final_renderer._frame_size`), as before this branch: out of its scope,
  left as a separate task.
- The review previews (CLI `render-previews`) crop with `CHROMA_420`, but still pass no `chroma_rows_divisor`
  (as before).
