# A1d – kiểm thật trên Control Center thử, 2026-10-17

- Code chạy trong gốc thử: `87def7f` (nhánh `perf/export-lane-a1d`). Gốc tạm `temp\a1d-check-20261017-110815`, cổng 8794, đã gỡ sau khi đo.
- Máy: RTX 2060 6144 MiB, driver 617.42. Hàng đợi thật trống và tạm dừng suốt các bước (người dùng bấm lúc 12:02:12; database thật chỉ đọc ở `mode=ro`, không gọi route nào của Control Center thật).
- Video: video gốc của job 146 (1280x720), chỉ đọc. E1 và S1 là hai clip 6 phút cắt bằng stream copy; F1 là cả phim (2807 s) remux với một thẻ metadata khác.
- Quét: nhóm quảng cáo (preflight, text, visual_logo, localize_logo, build_review), live action, `careful`, "Quét nhanh" bật.
- File: `check.json` (mọi kiểm tra và số liệu), `vram-1s.csv` (VRAM lớn nhất mỗi giây của lượt `run`, theo nvidia-smi và NVML).

## Lượt `run`: bản xuất NVENC chạy cạnh bước quét

| Mốc (s) | Làn chính (S1) | Làn xuất (E1) |
|---|---|---|
| 0,0–0,2 | preflight (CPU) | |
| 0,2–30,3 | text | render bắt đầu lúc 0,7 |
| 30,3–71,7 | visual_logo | render đến 60,4 |
| 71,7–122,4 | localize_logo, build_review | |

- Làn xuất mở cạnh `text`, tính bằng đỉnh của chính bước đó: `EXPORT_LANE_PARALLEL`, đang dùng 526 MiB, đỉnh bước 833 (`scan_peak_basis: stage`), NVENC 244, biên 256, còn 4541 MiB.
- Hôm nay nền VRAM thấp (526 MiB thay vì khoảng 950–1250 MiB như các ngày trước), nên luật A1b (4943) cũng sẽ còn 431 MiB và mở làn. Phần A1d khác A1b là lúc nền cao: xem `../replay-2026-10-15.md`.
- Ở ranh giới `text` → `visual_logo` (30,3 s), làn xuất vẫn đang xuất. VRAM đang dùng là 694 MiB (NVML), nên `6144 − (694 + 244 + 4691) = 515 ≥ 256`: `visual_logo` vừa ngân sách và chạy cạnh bản xuất 29,6 s, không có sự kiện chờ. Việc chờ khi không vừa đã được kiểm bằng test đơn vị (`StageBudgetLaneTests`).
- Hai làn chạy cùng lúc 59,3 s; cả lượt mất 123 s.
- **Đỉnh VRAM: 5134 MiB (nvidia-smi), 5322 MiB (NVML)**, trên tổng 6144: còn trống 822–1010 MiB. Không có `STAGE_FAILED`, không có log hết bộ nhớ.
- Bản xuất: `h264_nvenc`, `lane: parallel`, không fallback, kiểm tra `cpu_full_decode`, qua kiểm tra của "Xóa video gốc". 7 lần thấy FFmpeg, đều ở mức dưới bình thường.

## Lượt `measure`: quét cả phim F1 một mình

Đỉnh VRAM thêm vào của từng tiến trình, so với VRAM thấp nhất trong giây trước khi nó bắt đầu (cách đo của A1b; NVML và nvidia-smi cho cùng số):

| Bước | Đỉnh (MiB) | Số đã ghi trước đó | Đỉnh CUDA trong báo cáo |
|---|---|---|---|
| text | 831 | 833 | – |
| visual_logo | **4691** | 4690 | 4269 |
| localize_logo (định vị) | 767 | 1309 | – |
| localize_logo (grounding) | 1309 | 1309 | 1042 |

- Phim của job 146 có đỉnh grounding cao nhất trong các báo cáo thật (1042 MiB). Đỉnh của tiến trình vẫn là 1309, đúng bằng số đã ghi.
- `visual_logo` vượt 1 MiB, nên `STAGE_PEAK_MIB["visual_logo"]` được nâng lên 4691 theo số đo này. Test `RecordedNumbersTests` đọc cả `check.json` này cùng hai lần đo của A1b.
- Quét cả phim trong 240,8 s, kết thúc ở `WAITING_REVIEW`, không có bước nào lỗi.

## Kết quả kiểm tra (`check.json` → `checks`)
Tất cả `true`: export_beside_the_light_stage_at_its_own_peak, export_only_in_the_export_lane, both_lanes_at_once, heavy_stage_waited_or_fit, vram_peak_below_the_card, no_out_of_memory, scan_and_export_finished, export_manifest_valid, every_ffmpeg_below_normal, real_queue_stayed_empty_and_paused, whole_film_within_the_recorded_peaks (sau khi nâng lên 4691).
