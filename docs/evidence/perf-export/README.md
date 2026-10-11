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
(`tracked: false`; `src_sha256` identifies them): `a2-05653c9` (`git archive 05653c9 src`), `p2-step1`, `p2-step2`.
- Step 1, the gblur thread cap (`gblur-threads.md`):
  - `T71-thin60/<stamp>-stability.json`: `stability` runs one code twice (render and framemd5) and adds a framemd5 with
    one filter thread; `passed` only when both runs give the same framemd5 and mp4 SHA-256 and the framemd5 equals the
    one-thread one. `15:03:40` is the A2 code (fails, as expected), `15:04:45` the fixed code (passes).
  - `<bench>/<stamp>-gate.json` stamped 15:06–15:33 on 2026-10-11 (`smoke-synth` 15:06:19, `L39-1080-seg300` 15:07:08,
    `T74` 15:14:26, `T71` 15:32:53): A2 code (baseline, framemd5 only; its mp4 SHA-256 is the one of the A2 gate,
    `--baseline-mp4-sha256`) against step 1. Regions there are 30 rows or more, so the cap changes the graph but no
    byte: all four passed.
  - These ran while research agents of the same session also used the CPU: their times are not comparable.
