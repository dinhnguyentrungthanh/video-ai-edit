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

Possible fix (not done; changes output only where it is wrong today, so it needs the user's decision and its own
gate): cap each regional gblur's threads at its smallest plane's rows (`gblur=sigma=S:threads=<height // 2>` for
yuv420p). A region whose chroma rows reach the default thread count is unchanged by the cap (FFmpeg uses the smaller
of the two), so regions that are correct today keep their bytes; in the probes every thread count up to the row count
gave the same result, so the output would also stop depending on the machine's CPU count. Also seen: geq (the feather
mask) and delogo on an 8-row crop were stable; an overlay case was unstable only because of the gblur inside it.
