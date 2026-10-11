# Export timings and the A2 gate (perf plan A0, A2), 2026-10-11

Result files of `scripts/benchmark_export_steps.py`, copied unchanged from `<install>/temp/perf-export/<bench>/results/`.
They name jobs only by id and labels L/H/T (plan, top); paths are bench-relative (`<bench>/...`), and no film, file
name or link is in them. The video copies, renders and framemd5 files stay local and are not kept.

Benches (all copies in a temporary root; the install's `input\`, `output\` and database were only read):

| Bench | What | Blur operations |
|---|---|---|
| `T71` | whole copy of job 71 (T, 2,594 s, 1280×534, 25 fps) | job 71's approved operations, unchanged |
| `T71-seg300` | its first 300 s, stream-copied | the same, clipped to 0–300 s (3 of 4) |
| `L39-1080-seg300` | job 39 (L, 1920×1080), 3,600–3,900 s, stream-copied | job 71's operations fitted to it (regions ×1.5, cover sigma recomputed; whole-length ones stay whole-length, short ones keep their distance from the start or the end) |
| `T74` | whole copy of job 74 (T, 2,880 s, 1280×534, 25 fps) | job 74's approved operations, unchanged |
| `smoke-synth` | synthetic 20 s testsrc2 + sine at 1280×534 | job 71's operations fitted to it |

Files:
- `<bench>/<stamp>-measure.json`: A0 step timings with the code of `origin/main` `7d8b8bc`; `render` is the real
  `render_final_output` with each FFmpeg, ffprobe and SHA-256 call timed; the other steps reuse its exact FFmpeg command.
- `<bench>/<stamp>-gate.json`: A2 gate, old code (baseline) against this branch (candidate): framemd5 of `[vout]` and
  `[aout]` of the export graph and the SHA-256 of the real x264 mp4 of each side; `passed` only when both are identical.
- `code` records the code a file ran: `src_sha256` (every `.py` under `src`, path and bytes) and, for a tracked
  checkout, its commit and changed files. The baseline is a frozen copy of `src` at `40a0125` (identical to `7d8b8bc`)
  in the main checkout's ignored `temp\`: it belongs to no commit, so its record is `tracked: false`. The A0 files of
  `T71-seg300` and `L39-1080-seg300` were written before `src_sha256` was added; they ran that baseline copy (their
  `code` shows the main checkout's commit, which is not the code they ran). `T71` ran this worktree at `40a0125`,
  before any change.
