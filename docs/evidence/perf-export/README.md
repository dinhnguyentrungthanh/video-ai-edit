# Export timings and the A2 gate (perf plan A0, A2), 2026-10-11

Result files of `scripts/benchmark_export_steps.py`, copied from `<install>/temp/perf-export/<bench>/results/`. The only
edit: `machine.ffmpeg` keeps just the FFmpeg version number (the banner of the first files also named the build vendor's
site; the script now records the number only). The files name jobs only by id and labels L/H/T (plan, top); paths are
bench-relative (`<bench>/...`), and no film, file name or link is in them. The video copies, renders and framemd5 files
stay local and are not kept.

Benches (all copies in a temporary root; the install's `input\`, `output\` and database were only read):

| Bench | What | Blur operations |
|---|---|---|
| `T71` | whole copy of job 71 (T, 2,594 s, 1280×534, 25 fps) | job 71's approved operations, unchanged |
| `T71-seg300` | its first 300 s, stream-copied | the same, clipped to 0–300 s (3 of 4) |
| `L39-1080-seg300` | job 39 (L, 1920×1080), 3,600–3,900 s, stream-copied | job 71's operations fitted to it (regions ×1.5, cover sigma recomputed; whole-length ones stay whole-length, short ones keep their distance from the start or the end) |
| `T74` | whole copy of job 74 (T, 2,880 s, 1280×534, 25 fps) | job 74's approved operations, unchanged |
| `smoke-synth` | synthetic 20 s testsrc2 + sine at 1280×534 | job 71's operations fitted to it |
| `T71-thin60` | job 71's first 60 s, stream-copied (phase 2) | 8 hand-written regions 8–24 rows high and one cut (`T71-thin60/thin-regions.ops.json`: source pixels, the copy's clock) |

All benches render with the default export size policy, which is also the one jobs 71 and 74 were exported with (their
bench renders are byte-identical to the real exports).

Files:
- `<bench>/<stamp>-measure.json`: step timings with the old code (`origin/main` `7d8b8bc`); `render` is the real
  `render_final_output` with each FFmpeg, ffprobe and SHA-256 call timed; the other steps reuse its exact FFmpeg command.
  - The three A0 files (`T71` 10:48, `T71-seg300` 11:22, `L39-1080-seg300` 11:27) ran while this session's other work
    (tests, reviews, commits) also used the CPU: the same work took 18–29 % longer than in the later gate runs, when
    nothing else of this session ran (L39 render 177.5 s against 148.0 and 144.9 s; T71 decode check 28.6 s against 24.1 s; T71 filters 664.6 s with
    `-f null` against 515.9 s for the heavier framemd5). Read them as shares and orders of magnitude, not as clean times.
  - `T71/20261011-134700-measure.json` (only a `render` step) re-measures the old code while nothing else of this
    session ran, for A2's before/after: 741.8 s, decode check 24.3 s (as in the gate runs), same output bytes.
- `<bench>/<stamp>-gate.json`: A2 gate, old code (baseline) against this branch (candidate): framemd5 of `[vout]` and
  `[aout]` of the export graph and the SHA-256 of the real x264 mp4 of each side. These four ran one after the other
  while nothing else of this session ran (the real Control Center was off). `passed` is true only when both are identical (with `--baseline-twice`, also between the two baseline
  runs). `T71` takes its baseline mp4 SHA-256 from the A0 render (`--baseline-mp4-sha256`), which is byte-identical to
  job 71's real export; `T74`'s baseline render is byte-identical to job 74's real export.
- `code` records the code a file ran. Files written after the A0 runs have `src_sha256` (every `.py` under `src`, path
  and bytes) and, for a tracked checkout, its commit and changed files. The baseline is a frozen copy of `src` at
  `40a0125` (identical to `7d8b8bc`) in the main checkout's ignored `temp\`: it belongs to no commit, so its record is
  `tracked: false`.
  - The three A0 files have no `src_sha256`. `T71-seg300` and `L39-1080-seg300` ran the baseline copy; their `code`
    shows the main checkout's commit, which is not the code they ran.
  - `T71` (A0) ran this worktree's `src` as it was at 10:48 (`40a0125`, before any change) for its render, filters,
    encode, decode, faststart and hash steps. Its last step, `cli_import` (about 11:21:30–11:22:18), started new
    interpreters after the A1a edits to `scheduler.py` and `control_center.py`, so that step imported the A1a code.

## Phase 2 (branch `perf/export-speed-p2`)

Each step is gated against the step before it, both as frozen copies of `src` in `<install>\temp\perf-export\`
(`tracked: false`; `src_sha256` identifies them): `a2-05653c9` (`git archive 05653c9 src`), `p2-step1`, `p2-step2`, `p2-step3`.
- Step 1, the gblur thread cap (`gblur-threads.md`):
  - `T71-thin60/<stamp>-stability.json`: `stability` runs one code twice (render and framemd5) and adds a framemd5 with
    one filter thread; `passed` only when both runs give the same framemd5 and mp4 SHA-256 and the framemd5 equals the
    one-thread one. `15:03:40` is the A2 code (fails, as expected), `15:04:45` the fixed code (passes).
  - `<bench>/<stamp>-gate.json` stamped 15:06–15:33 on 2026-10-11 (`smoke-synth` 15:06:19, `L39-1080-seg300` 15:07:08,
    `T74` 15:14:26, `T71` 15:32:53): A2 code (baseline, framemd5 only; its mp4 SHA-256 is the one of the A2 gate,
    `--baseline-mp4-sha256`) against step 1. Regions there are 30 rows or more, so the cap changes the graph but no
    byte: all four passed.
  - These ran while research agents of the same session also used the CPU: their times are not comparable.
- Step 2, the feather mask made once:
  - `<bench>/<stamp>-gate.json` stamped from 18:40 on 2026-10-11 (`smoke-synth` 18:40:39, `T71-thin60` 18:41:35,
    `L39-1080-seg300` 18:42:37, `T74` 18:51:36, `T71` 01:01:17 on 2026-10-12): step 1 code (baseline) against step 2. Both
    sides render, so each file is also a timing pair. All passed: framemd5 and mp4 SHA-256 identical.
  - The PC was not idle while they ran: the A2 gate rendered T74 in 330.5 s, here the same bytes took 441.9 s. Compare
    only the two sides of one file.
  - `T71` ran while the user's video editor used the CPU (the Control Center ran nothing): its render of the same
    bytes took 1,000.6 s against 438.5 s in step 1's gate, and its step 2 framemd5 was slower than step 1's. It shows
    only that no byte changed.
- Step 3, the graph through a file (frozen `p2-step3`, harness `harness-p3`):
  - `<bench>/<stamp>-gate.json` stamped 01:55–01:56 on 2026-10-12 (`smoke-synth` 01:55:57, `T71-thin60` 01:56:05,
    `T74` 01:56:26): step 2 code (baseline, framemd5 only; `--baseline-mp4-sha256` gives the step 2 mp4) against step 3.
  - Each side's `graph_option` names how its graph reached FFmpeg. The candidate render's is `-/filter_complex`, and
    `render_graphs_match` is true: the file FFmpeg read holds the graph measured. All three passed: framemd5 and mp4
    SHA-256 are identical, and the graph text is unchanged (`graph_changed: false`).
- A3, the x264 path after merging G1 (frozen `a3` = `f4e80c2`, harness `harness-a3`):
  - `<bench>/<stamp>-gate.json` stamped 08:54 on 2026-10-12 (`smoke-synth` 08:54:48, `T74` 08:54:58): `9f0581a`
    (frozen `p2-step3`, baseline) against A3 with no `render_settings`. Both sides render.
  - Both passed: framemd5 and mp4 SHA-256 identical, and the mp4s are those of A2 and steps 2 and 3 (`smoke-synth`
    `1aa9f941…`, `T74` `3f2de702…`). The candidate's `encoding` records `libx264` and `nvenc_check: not_asked`.
- A3, NVENC against x264 (`<bench>/<stamp>-quality.json`, command `quality`, code `a3` = `f4e80c2`):
  - `T71-seg300` (09:11) and `L39-1080-seg300` (09:35): sweep of p4/p5/p7 and `-cq` 19–27 (harness `harness-a3`).
  - `T71` (10:26), `T74` (11:17): whole episodes, x264, "auto" (p5 `-cq` 23 through the renderer) and p5 `-cq` 25/27.
    T74 was scored with two FFmpeg runs at a time and also records `vmaf_every3` (the same numbers from every
    third frame only).
  - `H37` (12:03): the whole film (job 37, 1920×1080, 24000/1001). Its sizes, render times and `fallback` are
    right; its **SSIM and VMAF are not**: the scoring paired frames by timestamp, and this source's timestamps are
    rounded to 1/90000 against the export's exact 1/24000, so many frames met their neighbour (VMAF percentiles 0
    for every encoder). Fixed in `42282f8` (pairing by frame number); T71 and T74 (25/1) were not affected.
  - `H37-seg1200` (14:47): 20 minutes of that film (from 2,400 s, the job's blur region), scored with the fix, every
    frame. Its `size_policy` was set so the video maxrate is the whole film's (3,416,495 bit/s).
  - Every render ran alone; scoring ran after all renders of a bench. VRAM is nvidia-smi's used memory (GPU 0).
- A3 after the user's approval (p5 `-cq` 25; exports default to "auto"; frozen `a3c`, harness `harness-a3c`):
  - `smoke-synth/20261012-154941-gate.json`: `9f0581a` (frozen `p2-step3`, baseline) against the approved code. Both sides
    render, and the harness forces x264 on both (`render_settings` `video_encoder: "libx264"`; the baseline ignores it).
  - Passed: framemd5 and mp4 SHA-256 identical (`1aa9f941…`, as before). The candidate's `encoding` records `libx264`,
    `requested: libx264` and `nvenc_check: not_asked`.
