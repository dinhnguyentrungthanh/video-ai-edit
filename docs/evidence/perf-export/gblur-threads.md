# FFmpeg gblur is wrong when a plane has fewer rows than its threads (found 2026-10-11)

Found while self-testing the A2 gate on a 320×180 synthetic bench whose fitted regions were 8 and 15 rows high: two runs
of the old code gave different framemd5. Pre-existing, not caused by A2 (A2 runs the same gblur inside the blur times).

Probes (FFmpeg 9.0.1 of the install, this PC: 6 cores, 12 logical CPUs, 13 filter threads by default), each on a
4 s testsrc2 clip, `crop=128:<h>:96:100,...,gblur=sigma=28`:

| Case | Result |
|---|---|
| yuv420p, heights 9–25, default threads, 5 runs | 4–5 different framemd5 of 5 |
| yuv420p, heights 26, 27, 30, 60 | 1 of 5 (stable) |
| same graphs with `-filter_complex_threads 1` or `gblur=...:threads=1` | 1 of 5 (stable) |
| gray, height 8, `threads=` 1, 2, 3, 4, 6, 7, 8 | one identical result |
| gray, height 8, `threads=` 9, 12, 13 | different results, changing from run to run for 12 and 13 |
| gray, height 12, `threads=` 1 … 12 / 13 | identical / different |
| default threads against `threads=1`, 4 runs | gray h 8: up to 178 of 255 off, every frame; gray h 14–40: identical; yuv420p h 16 and 24: luma identical, chroma up to 171 and 159 off, every frame; h 26: identical |

So with more filter threads than a plane has rows, gblur's output is wrong and varies; with at most that many threads
every thread count gives the same (correct) result. In yuv420p the chroma planes have half the rows: a blur region
under 2 × (filter threads) rows gets wrong colour, under 1 × also wrong brightness.

Exports so far (read-only scan of the 22 completed export manifests): no blur region under 26 rows; the smallest are
30 rows (5 regions), then 33 and 34. They are fine on this PC. On a machine with 16 or more filter threads (about 15+
logical CPUs) a 30-row region's chroma (15 rows) would be wrong.

Also seen: geq (the feather mask) and delogo on an 8-row crop were stable; an overlay case was unstable only because of
the gblur inside it.

## Fix (perf phase 2, step 1; the user approved it on 2026-10-11)

Each regional gblur gets `threads=max(1, height // 2)`: the rows of its chroma plane (the branch is yuv420p, and crop
rounds an odd height down). FFmpeg uses the smaller of this cap and its own filter thread count, and every count up to
the row count gives the same pixels, so:
- a region whose chroma rows reach the default thread count (26 rows or more on this PC) is unchanged;
- a thinner region now comes out as with one thread: right, and the same on every run and every machine.

The full-frame gblur keeps FFmpeg's default (a whole frame has hundreds of rows).

Gates (`scripts/benchmark_export_steps.py`, files in `<bench>/*-stability.json` and `<bench>/*-gate.json`):
- `T71-thin60`: the first 60 s of job 71 with 8 hand-written regions 8, 14, 15 and 24 rows high (gblur and cover,
  feather 0, 2, 3 and 4, `all_edges` and `vertical_only`, whole-length and short) and one cut
  (`thin-regions.ops.json`). `stability` renders the same code twice and adds a framemd5 with one filter thread:
  - A2 code (`05653c9`): the two runs differ in framemd5 and in the mp4 SHA-256, and neither matches one thread:
    failed, as expected;
  - fixed code: both runs and the one-thread run give the same framemd5 (`3546c609…`), and both mp4 are identical
    (`821bec86…`): passed. The one-thread framemd5 of the A2 code is the same `3546c609…`: the fix gives exactly
    the one-thread pixels.
- `smoke-synth`, `L39-1080-seg300`, `T74` and `T71` (regions of 30 rows or more): `gate` A2 code against the fix,
  framemd5 identical and mp4 SHA-256 identical to the A2 renders (for `T74` and `T71` also the real exports of jobs
  74 and 71): regions that were right keep their bytes.

Root cause (FFmpeg n9.0.1 source, read by a research agent of this session and confirmed by its probes; its second,
independent check did not run, so treat the source line details as unverified): gblur's last
pass (`postscale`) splits the plane by rows into `min(width × height, threads)` jobs, so with more threads than rows
some jobs get no row. The C version then does nothing, but the x86 assembly loop tests its length after its body and
scales one vector (8 floats with AVX2) of the next job's rows a second time, without synchronisation: those pixels go
almost to black, or not, depending on timing. The cap leaves no job empty. A second assembly fault, not reached by
the cap: with AVX2, a plane 8 pixels wide or less (a yuv420p region 16 pixels wide or less) whose slices reach 8 rows
reads past its row; thin regions get slices of about 2 rows (correct), and such narrow regions of 26 rows or more
behave as before.
