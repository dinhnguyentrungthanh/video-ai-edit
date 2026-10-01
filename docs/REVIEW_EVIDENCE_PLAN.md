# Ô duyệt cho thấy nội dung thật — kế hoạch (01/10/2026)

Chưa thi công; chờ người dùng duyệt (QUALITY_PLAN.md §18 d).

## Vì sao

Troy job #39 revision 4, mục 18+ `review-935e63a78271` 928.5–950.5 s: ô duyệt chỉ có một ảnh (`preview_images` = khung mạnh nhất 944.5 s, lưng trần) và trang duyệt không có video. Lộ ngực thật ở 937.5–942.25 s nên người dùng chọn KEEP rồi phải sửa lại. Báo cáo quét hiện chỉ giữ ảnh khung mạnh nhất của mỗi đoạn (`intervals.compact_interval_thumbnails`) và không lưu điểm từng khung, nên dải ảnh phải trích lại từ video nguồn (chỉ đọc).

## Pha A — không đổi detector, schema queue hay quyết định; dùng được ngay cho Troy rev 4

1. **Mô-đun mới `src/biliflow/review_evidence.py`** (hàm thuần + cache, test không cần server):
   - `item_evidence(root, queue, item_id)`: đọc `source_candidate_refs` (`<report>#interval:N`, chỉ report có trong `queue["reports"]` và nằm dưới `reports/`), lấy ngưỡng, `sample_fps`, `max_score`, `sample_count`, cửa sổ detector, giây của khung mạnh nhất; trả về dải khung, thông tin hạt (seed), khung mạnh nhất, phần nối thêm 0.70, khả năng phát video.
   - Chọn khung: 8–16 khung chia đều phần đã phát hiện (bám lưới 1/`sample_fps`), mọi hạt đã biết, luôn có khung mạnh nhất, tối đa 24. Troy: 929.5, 931.5 … 949.5 + 944.5 → có 937.5/939.5/941.5 nằm trong đoạn lộ ngực.
   - `ReviewFrameCache`: FFmpeg CPU (`-ss T -i src -frames:v 1 -vf scale=640:-2`), ưu tiên thấp, tối đa 2 tiến trình, khóa theo file, ghi nguyên tử, timeout 30 s.
   - `parse_range` / `stream_file` (206/416, khối 1 MiB, dừng khi client ngắt).
2. **Cache `cache/review-frames/<sha256[:16]>/<ms>-w640.jpg`** — ngoài `reports/` để dọn có giới hạn (thêm vào `cleanup.FILE_CACHE_POLICIES`: 14 ngày, 1 GiB) và để Visual AI Audit/brand memory không bao giờ lấy các ảnh này.
3. **Route Control Center** (`/api/jobs/<id>/review/evidence|frame|video`): nguồn video chỉ lấy từ job (không từ request), khớp sha/kích thước/mtime; `frame` chỉ nhận giây có trong dải do server tính; khóa `media_key = HMAC(token, job)`; kiểm tra Host (127.0.0.1/localhost) chống DNS rebinding; mã lỗi rõ ràng. Tách `Handler` thành `_handler_class()` để test được.
4. **Giao diện duyệt** (`review_workflow._interactive_html`): với 18+/máu me/bạo lực thêm dòng tóm tắt ("16 khung ≥ 0,95 trong 15:37.0–15:50.5 …"), dải ảnh có mốc giờ (hạt viền đỏ, khung mạnh nhất có huy hiệu; bấm để tua), một trình phát chung với "▶ Phát đoạn này", "Tới lúc mạnh nhất", "Tới khung ≥ ngưỡng đầu tiên"; tải lười khi cuộn tới.
5. **Bảo mật/riêng tư:** chỉ 127.0.0.1, không tải gì từ ngoài; ảnh dải không vào `preview_images` nên Visual AI Audit gửi y như cũ (có test khóa). Nên làm luôn: kiểm tra Host cho mọi route Control Center (hiện `/api/session`, `/review/<id>` và `/media/` (ảnh 18+) đọc được qua DNS rebinding).

## Pha B (cần duyệt, có hiệu lực từ lần quét sau)

Ghi `detector_samples` (giây + điểm từng khung trúng) vào interval trong `intervals.group_hits`/`merge_intervals`, không chép vào queue item. Dòng tóm tắt khi đó nêu chính xác các giây ≥ ngưỡng. Khóa cache bước an toàn đổi nên lần quét lại Troy sau tốn ~20 phút GPU.

## Pha C

Bật cho logo/chữ quảng cáo (vẽ khung đỏ trên dải ảnh).

## Kiểm chứng trên Troy rev 4 mà không đổi quyết định

Ghi sha256 queue, số quyết định (151 KEEP / 8 BLUR), stat nguồn, danh sách thư mục report trước; gọi `item_evidence` offline; mở `/review/39`, lọc 18+, mở ô 15:28.5–15:50.5, xem dải và phát đoạn (không bấm nút quyết định); sau đó mọi thứ trên phải y nguyên, file mới chỉ ở `cache/review-frames`. Test tập trung + bộ đầy đủ.

## Ước lượng

Pha A ~1–1.5 ngày công agent (mô-đun ~300 dòng, Control Center ~80, UI ~50, test ~300); Pha B ~0.5 ngày + quét lại; Pha C 0.5–1 ngày. Rủi ro: định dạng không phát được trong trình duyệt (HEVC/MKV → chỉ dải ảnh), tải CPU khi trích khung (đã giới hạn), token đổi khi khởi động lại (trang tự lấy lại).
