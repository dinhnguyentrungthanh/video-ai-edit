# A1b: đo VRAM của NVENC cạnh bước quét (2026-10-14)

Số đo cho ba hằng số của `src/biliflow/export_lanes.py` (`NVENC_NEED_MIB`,
`SCAN_PEAK_MIB`, `SAFETY_MARGIN_MIB`). Người dùng cho phép đo ngay vào ngày 2026-10-14.

## Cách đo

- Script: `scripts/a1b_vram_measure.py run` (mã của commit a079975).
- Control Center thử: root tạm `<install>\temp\a1b-measure-<giờ>`, có marker cài đặt
  riêng, cổng 8791, `--no-import-existing`. Control Center thật (cổng 8765) chỉ được đọc
  `GET /api/status` mỗi 2 giây; nó rảnh suốt cả hai lượt (`real_control_center_busy` rỗng).
- Hai clip 10 phút được cắt bằng stream copy từ hai video của người dùng (bắt đầu ở giây
  600), nằm trong root tạm: một clip 1920x1080, một clip 1280x640. Tên và đường dẫn video
  không được ghi vào đây.
- NVML (GPU 0, "used") lấy mẫu 10 lần/giây; `samples-1s.csv` là mẫu theo từng giây.
- Các pha: rảnh 15 giây; mỗi clip xuất riêng (NVENC, p5 -cq 25, không có gì khác chạy);
  quét clip 1080p một mình (live_action, cả bốn nhóm phát hiện, careful, quét nhanh, OCR 1);
  quét clip nhỏ (mixed) và xuất hai clip cạnh lúc Qwen2-VL đã nạp xong (mức VRAM đứng yên).
- Máy: NVIDIA GeForce RTX 2060, 6144 MiB, driver 617.42.

`run1`: các bản xuất cạnh bước quét bị từ chối ("Final render is not approved") vì bản xuất
riêng trước đó đã đánh dấu kế hoạch là đã xuất. Lỗi của script, không phải của BiliFlow;
`run2` dùng một kế hoạch cho mỗi lần xuất. Số đo của các bước quét trong `run1` vẫn dùng được.

## Kết quả

| | run1 | run2 |
|---|---|---|
| VRAM lúc rảnh (chương trình khác) | 596 MiB | 499 MiB (490 lúc quét) |
| NVENC 1920x1080, xuất riêng | 244 MiB, 64,6 s | 244 MiB, 64,0 s |
| NVENC 1280x640, xuất riêng | 169 MiB, 73,4 s | 169 MiB, 72,9 s |
| NVENC 1920x1080 cạnh confirm_violence | (lỗi script) | 244 MiB, 77,4 s, đỉnh 5678/6144 MiB |
| NVENC 1280x640 cạnh visual_logo | (lỗi script) | 170 MiB, 84,5 s, đỉnh 5350/6144 MiB |

Đỉnh của từng bước quét so với VRAM ngay trước khi bước đó bắt đầu (không chồng bản xuất):

| bước | run1 | run2 |
|---|---|---|
| confirm_violence (Qwen2-VL) | 4851 | 4943 |
| visual_logo (Qwen2-VL) | 4690 | 4688 |
| localize_logo | 1309 | 1308 |
| text | 833 | 832 |
| animation_safety | 754 | 754 |
| live_safety | 717 | 717 |
| verify_adult | 479 | 479 |
| adult | 258 | 258 |

Mọi bản xuất NVENC của `run2` trả 0 với `h264_nvenc`, không encode lại. Cả hai job quét
kết thúc ở WAITING_REVIEW. Khi chồng bản xuất, các bước của job 2 chậm hơn `run1` khoảng
15-29 % (confirm_violence 48,7 → 57,9 s; text 35,5 → 44,3 s; visual_logo 37,4 → 48,3 s), và
bản xuất chậm hơn 16-21 %. Thời gian tổng của song song và tuần tự được đo ở cổng 6.

## Hằng số đã ghi

- `NVENC_NEED_MIB = ((1280 * 640, 170), (1920 * 1080, 244))`: số lớn nhất đo được cho
  mỗi cỡ khung. Khung lớn hơn 1920x1080 (hoặc cạnh dài hơn 1920) không mở luồng thứ hai.
- `SCAN_PEAK_MIB = 4943`: đỉnh lớn nhất của một bước quét; mọi bước được tính bằng nó.
- `SAFETY_MARGIN_MIB = 256`: VRAM của chương trình khác dao động 106 MiB giữa hai lượt;
  người dùng chọn 256 MiB.

Với các số này, máy này mở luồng thứ hai khi VRAM đang dùng ngay trước bước quét không quá
701 MiB (khung 1080p) hoặc 775 MiB (khung 1280x640):
`6144 - (nền + 4943) - nhu cầu NVENC >= 256`.
