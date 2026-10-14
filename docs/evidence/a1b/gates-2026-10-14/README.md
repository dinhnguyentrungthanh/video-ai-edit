# A1b: cổng 1, 2, 3, 6 trên Control Center thử (2026-10-14)

Bằng chứng cho các cổng của A1b. `gates.json` do `scripts/a1b_gates.py report` ghi ra. Code chạy là commit 962b7f2 (đã ghi số đo VRAM); code gốc để so là 23c9e00.

## Cách chạy

- **Root và cổng.** Root tạm `<install>\temp\a1b-gates-20261014-125846`, có marker riêng; Control Center thử ở cổng 8792. Script chỉ điều khiển server nào mà `state\control-center.json` của root ghi đúng tiến trình con, cổng và token của nó.
- **Control Center thật.** Người dùng tạm dừng hàng đợi thật suốt các bước dùng GPU. Hàng đợi thật trống và không chạy gì: mọi bước đều ghi `real_control_center_busy` rỗng hoặc `false`. Control Center thật chỉ bị đọc `GET /api/status`.
- **Clip.** Cắt bằng stream copy 10 phút (từ giây 600) từ hai video của người dùng:
  - X1: 1920x1080, 605,7 s;
  - Y1: 1280x640, 604,6 s.

  Các bản X2–X6 và Y2–Y3 là bản remux của X1/Y1, chỉ khác một thẻ metadata: cùng gói dữ liệu nên cùng khung hình, nhưng khác SHA-256 nên mỗi bản có job và lượt quét riêng (cache theo SHA không dùng chung). Tên và đường dẫn video không được ghi vào đây.
- **Duyệt.** Làm trên root thử: nhận gợi ý của bộ phát hiện, còn lại KEEP.
  - X1, X2: 1 BLUR, 13 KEEP.
  - Y1–Y3: 6 KEEP.
- **Hồ sơ phần cứng.** Làn xuất chỉ mở khi hồ sơ phần cứng xác nhận NVENC. Root thử ban đầu không có hồ sơ, nên script đã chạy "Đo lại" trên Control Center thử: kết quả `done`, NVENC `ok`.
- **Thiết lập quét và xuất.** Quét: careful, đủ bốn nhóm, quét nhanh, OCR 1; X quét kiểu live_action, Y kiểu mixed. Xuất: chính sách dung lượng mặc định.

## Cổng 1: x264 giống hệt từng byte giữa tuần tự và A1b — ĐẠT

Bốn bản xuất x264 từ cùng một kế hoạch đã duyệt của X1 (bản sao lấy ngay khi bấm Xuất ở lượt `off`):

| bản | cách tạo |
|---|---|
| `sequential_a3` | NVENC lỗi thật (`CUDA_VISIBLE_DEVICES=-1`: `cuInit ... CUDA_ERROR_NO_DEVICE`), A3 encode lại bằng x264 ngay |
| `a1b_handback` | Lệnh xuất như làn xuất chạy (`BILIFLOW_EXPORT_LANE=parallel`), NVENC lỗi y như trên: dừng với mã 2 sau 4,4 s, không ghi file, để lại bản ghi trả về `encoder_error`. Lệnh xuất như làn chính chạy sau đó encode x264 theo bản ghi (`fallback.deferred: true`, `lane: main`) và xóa bản ghi |
| `forced_x264` | kế hoạch chọn `libx264` |
| `baseline_x264` | như trên, nhưng chạy code gốc 23c9e00 (manifest không có `lane`) |

Cả bốn đều ra 259.550.176 byte, cùng SHA-256 mp4 `8eeb90256a1b4ab4…` và cùng framemd5 `07bf91543ec86869…`. Mỗi bản x264 mất 115–127 s.

**Phạm vi:**
- Cổng này kiểm phần renderer đúng như mỗi làn gọi nó.
- Chuỗi trả về bên trong bộ lập lịch chỉ được kiểm bằng unit test với tiến trình giả (`tests/test_scheduler.py`). Chuỗi đó gồm: `requeue_running_stage`, sự kiện `EXPORT_DEFERRED`, và việc làn chính chờ job quét xong. Lý do: trong lượt thật NVENC của làn xuất không lỗi, và không có cách làm NVENC lỗi riêng cho tiến trình xuất của Control Center mà không sửa code.
- Chỉ thử lỗi NVENC lúc mở encoder. Lỗi giữa chừng, lỗi vượt dung lượng và `not_nvenc` không được thử thật.

## Cổng 2: quét cạnh bản xuất cho kết quả như quét một mình — ĐẠT

Mỗi cặp so mọi file trong `reports/jobs/<job>` cùng cache phân luồng logo (`cache/visual-logo/<sha>`).

**Được che (không so):**
- giờ hệ thống: khóa `*_at`, khóa có chữ `elapsed`, `wall` hoặc `clock`, và `at`;
- trong khối `metrics`, chỉ thời gian chạy và bộ nhớ: khóa có chữ `seconds`, `second`, `ram` hoặc `memory`, ví dụ `read_seconds`, `peak_process_ram_bytes`, `peak_cuda_memory_bytes`;
- `job_id`, `pid`, `peak_cuda_bytes` và `source_report_sha256` (hash của một báo cáo có chứa `created_at`);
- tên riêng của job: khóa job, SHA, đường dẫn, tên và kích thước file, cùng khóa, đường dẫn và kích thước của cache phân luồng (nội dung cache vẫn được so riêng);
- id mục duyệt: là hash có lẫn đường dẫn, nên được đánh số lại theo thứ tự xuất hiện;
- một file log chạy (`text/logo-routing-prewarm.log`), không so.

**Vẫn được so:** mọi thời điểm trong video, điểm số, khung, quyết định gợi ý, số đếm trong `metrics` (`crops`, `recognition_calls`, `calls`, `failed_calls`…) và bộ đếm của cache grounding.

Trước mỗi lượt quét dùng để so ở cặp X, cache grounding (đánh địa chỉ theo nội dung khung, dùng chung giữa các job) được dời sang chỗ khác. Nhờ vậy mỗi lượt tự tính hộp của nó: `computed 14, cache_hits 0` ở cả hai phía.

| cặp | một mình | cạnh bản xuất | bản xuất bắt đầu cạnh | bước chạy cạnh bản xuất | kết quả |
|---|---|---|---|---|---|
| auto | Y1 (lượt `off`) | Y2 + xuất X2 1080p | animation_safety | animation_safety, adult, live_safety | 309/309 file giống hệt |
| c1 | X6 (`ref`) | X3 + xuất Y1 | adult | adult, verify_adult, live_safety, confirm_violence | 250/250 |
| c2 | X6 | X4 + xuất Y2 | confirm_violence | confirm_violence, text, visual_logo | 250/250 |
| c3 | X6 | X5 + xuất Y3 | visual_logo | visual_logo, localize_logo | 250/250 |

- Cả tám bước dùng GPU đều đã chạy cạnh một bản xuất NVENC ít nhất một lần. Ở mọi cặp, cache phân luồng logo cũng giống hệt.
- Hai cặp tham chiếu (cả hai phía đều quét một mình) cũng giống hệt:
  - X1/X2: 250/250, quét trước khi có hồ sơ. Ở cặp này bộ đếm grounding được che, vì X2 lấy hộp từ cache X1 vừa ghi.
  - Y1/Y3: 309/309.
- Clip Y không có việc grounding nào (`computed 0`); phần grounding được kiểm ở các cặp X.

## Cổng 3: bản NVENC xuất song song qua kiểm tra sau xuất — ĐẠT

Bốn bản xuất chạy ở làn song song: X2, Y1, Y2, Y3.
- Mỗi lượt có sự kiện `EXPORT_LANE_PARALLEL`, với VRAM còn dư (`left_mib`) 421 MiB cho khung 1080p và 495 MiB cho khung 1280x640.
- Manifest ghi `h264_nvenc`, `lane: parallel`, `cpu_full_decode`, không encode lại.
- Cả bốn đều đạt đúng kiểm tra của "Xóa video gốc" (`source_cleanup.assess_job`: `eligible`).

Bản tuần tự X1 (`lane: main`) cũng đạt.

## Cổng 6: thời gian — song song nhanh hơn 56,4 s (16,8 %)

Cùng một khối việc: quét một clip 1280x640 (mixed) và xuất một clip 1080p (NVENC).

| | tổng | bước xuất | các bước quét | chồng nhau |
|---|---|---|---|---|
| Tắt (tuần tự) | 335,2 s | 82,2 s | 251,0 s | 0 s |
| Tự động | 278,8 s | 100,4 s | 278,1 s | 99,4 s |

- Khi chồng nhau, bước xuất chậm hơn khoảng 22 % và các bước quét chậm hơn khoảng 11 %, nhưng tổng thời gian vẫn ngắn hơn.
- Các lượt phủ cho thêm một điểm tham khảo, không phải cặp đo chính thức: quét X một mình (`ref`) mất 266,9 s, còn quét X cạnh một bản xuất Y mất 281,1–281,5 s (bản xuất Y chạy 95–116 s cạnh nó).
- VRAM cao nhất trong mọi lượt là 5658/6144 MiB.
- Chỉ có một cặp đo, trên một máy (RTX 2060 6 GB). Chưa đo các tỉ lệ khác giữa việc quét và việc xuất.
