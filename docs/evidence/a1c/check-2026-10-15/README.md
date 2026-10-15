# A1c – kiểm thật trên Control Center thử (2026-10-15)

`check.json` do `scripts/a1c_check.py report` ghi, trên code `566ff4f` của nhánh `perf/a1c-preflight-lane`. Không có tên phim, đường dẫn hay chữ mà detector đọc được.

## Cách chạy
- Root tạm `temp\a1c-check-20261015-132858`, Control Center thử ở cổng 8793; root đã xóa sau khi ghi bằng chứng.
- Clip cắt bằng stream copy từ video gốc của job 142 (chỉ đọc), 1280x534, khoảng 6 phút:
  - E1: bản xuất;
  - S1: phim chờ quét;
  - S2: S1 đổi một thẻ metadata (cùng khung, khác SHA-256).
- Quét live action, chỉ nhóm quảng cáo: `preflight` rồi `text`, `visual_logo`, `localize_logo` trên GPU, rồi `build_review`.
- Control Center thật: người dùng bấm "Tạm dừng hàng đợi" (hàng đợi trống). Script không gọi route nào của nó, chỉ đọc database ở chế độ `mode=ro` mỗi 2 giây. Suốt bốn bước, hàng đợi thật vẫn trống và tạm dừng.
- Các bước:
  - `profile`: "Đo lại" của Control Center thử, NVENC `ok`;
  - `ready`: quét E1 và duyệt. Ba mục đều KEEP, nên bản xuất là encode lại không che;
  - `run`: tạm dừng hàng đợi thử, bấm xuất E1 (auto) và bắt đầu quét S1, rồi mở lại. Bước chờ đầu tiên là bản xuất, còn phim chờ ở `preflight`, đúng ca của job 131;
  - `alone`: quét S2 một mình.
- Script chạy ở mức ưu tiên dưới bình thường; Control Center thử và mọi tiến trình con kế thừa mức đó.

## Kết quả: đạt cả 9 kiểm tra
| Kiểm tra | Kết quả |
|---|---|
| `EXPORT_LANE_PARALLEL` đúng một lần, cạnh bước GPU đầu tiên của S1 (`text`), không có `EXPORT_LANE_SEQUENTIAL` hay `EXPORT_DEFERRED` | đạt |
| `preflight` của S1 xong trước khi bản xuất bắt đầu | đạt (0,0–0,1 s; bản xuất bắt đầu ở 0,5 s) |
| `text` của S1 bắt đầu trước bản xuất | đạt (0,1 s) |
| Bản xuất chỉ chạy ở làn `export` | đạt |
| Hai làn chạy cùng lúc (`active_lanes`) | đạt: 60,4 s chồng nhau (cạnh `text` 28,0 s, cạnh `visual_logo` 32,4 s) |
| Kết quả quét S1 giống hệt S2 quét một mình | đạt: 69/69 file giống hệt; cache routing giống hệt; mỗi lượt tự tính hộp grounding (`computed` 1, `cache_hits` 0) |
| Bản xuất qua kiểm tra sau xuất ("Xóa video gốc") | đạt: `h264_nvenc`, `lane: parallel`, `cpu_full_decode`, không fallback, `eligible` |
| Mọi FFmpeg dưới mức bình thường | đạt (6 FFmpeg mỗi lượt, đều `below_normal`) |
| Hàng đợi thật trống và tạm dừng suốt các bước | đạt (không ghi nhận thay đổi nào) |

## Số liệu
- VRAM lúc mở làn (RTX 2060, 6144 MiB): đang dùng 409 MiB, nền 409, đỉnh bước quét tính 4943, NVENC 170, biên 256, còn dư 622 MiB. Đỉnh thật trong lượt `run` là 5253 MiB; lượt `alone` 5080 MiB.
- Thời gian: lượt `run` 123,0 s (quét S1 cộng xuất E1 61,6 s ở làn xuất); lượt `alone` 112,5 s (chỉ quét).
- Trước A1c, cùng ca này sẽ chạy bản xuất trước ở làn chính rồi mới quét.

## Khi so kết quả quét
- Bỏ qua (thay bằng `<run>`): giờ hệ thống; thời gian và bộ nhớ trong `metrics`; `peak_cuda_bytes`; khóa, đường dẫn và kích thước của cache routing (nội dung cache vẫn được so); tên riêng của job (job key, SHA-256, tên file). Danh sách đủ ở `details.scan_comparison.ignored_keys`.
- Vẫn so: thời điểm trong video, điểm số, khung và số đếm.
- Không so: 1 file log của lượt chạy.
