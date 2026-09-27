# BiliFlow review and scan performance benchmark

Benchmark date: 2026-09-27. The review benchmark rebuilds queues from existing
detector reports; it does not rerun models or edit source videos.

## Review workload

| Video | Required cards before | Required cards after | Reduction | Source references represented | Missing references |
| --- | ---: | ---: | ---: | ---: | ---: |
| Troy | 294 | 177 | 39.8% | 353 -> 353 | 0 |
| Conan Movie 21 | 90 | 67 | 25.6% | 98 -> 98 | 0 |
| Shin #874-B2 | 11 | 6 | 45.5% | 11 -> 11 | 0 |

The reduction comes from one decision per persistent overlay track and bounded
groups of nearby adult/gore/violence detections. Safety groups retain their
original discrete intervals, so an edit never fills the gaps between detections.

## Shared live-action decode

The legacy gore scan decodes 256x256 RGB at 2 fps and the violence scan decodes
the same geometry at 8 fps. The shared scanner uses one FFmpeg decode with two
independent filter branches. The 2 fps branch is repeated to 8 fps only for
transport, then every fourth frame is routed to gore. This preserves the exact
legacy pixels instead of resizing an already-scaled frame.

Pixel alignment was checked on the first 60 seconds of Troy, Conan Movie 20 and
Conan Movie 21. Both branches matched the legacy streams byte for byte.

A 30-second Troy model benchmark produced:

| Check | Gore | Violence |
| --- | --- | --- |
| Frames scanned | 60 = 60 | 240 = 240 |
| Score summary | exact | exact |
| Ranked candidates and scores | exact | exact |
| Review intervals | exact | exact |
| Saved JPEG hashes | exact (20/20) | exact (23/23) |

The two legacy scan processes took about 25.7 seconds wall time in total; the
shared process took about 22.2 seconds, a 13.6% reduction on this short clip.
The measured scan loop decreased from 5.151 seconds combined to 4.132 seconds
(19.8%). The shared process peaked at 522,827,776 CUDA bytes and 1,551,986,688
RAM bytes on the RTX 2060 machine.

Adult scanning remains separate because it uses a distinct 448x448 frame path.
Keeping it separate avoids changing its input pixels or model scores.

## Exact rerun cache

Model-stage snapshots are reusable only when all of these identities match:

- source SHA-256;
- normalized stage command and sample settings;
- scanner/config source fingerprint;
- local model manifests and weight file identity;
- expected artifact layout.

Each cache hit copies the complete report and evidence images into the new report
revision. The review queue therefore does not depend on the cache surviving.
Stage snapshots are capped at 10 GiB and 14 days. Visual-logo routing cache is
capped at 4 GiB/30 days, and ad-candidate cache at 2 GiB/30 days. Cleanup runs
after a cacheable stage completes.

## Optional final-render experiments

The bundled FFmpeg advertises `h264_nvenc` and `hevc_nvenc`, but an actual Troy
render could not open the encoder. FFmpeg requires NVENC API 13.1 (NVIDIA driver
610 or newer) while the installed driver exposes API 13.0. BiliFlow therefore
keeps CPU `libx264` as the only selectable production encoder on this machine;
it does not expose a UI option that would fail after review.

Smart Render is not enabled for reviewed videos with blur operations. A regional
or full-frame blur must process the affected pixels, and stream-copy cuts can
move boundaries to keyframes. The existing accurate filter graph and full-decode
output validation remain the production path. A future Smart Render mode must
prove frame-accurate cut boundaries and automatically fall back to the current
renderer before it can be exposed.
