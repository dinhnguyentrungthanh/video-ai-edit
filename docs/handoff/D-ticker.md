# Bàn giao D – dòng chữ chạy của trang nguồn và địa chỉ web mất dấu chấm (phần 1)
- Nhánh: fix/ticker-subtitle (từ main bd0a7b8) · Worktree: `temp\wt-detect` · Cập nhật: 2026-10-15
- Chạy ở: PC · Trạng thái: **phần 1 xong (commit 220d25f, e6daeb5 mục 5, 16a4469 và c230029 sửa theo một lượt
  review đọc mã), mọi cổng đạt, chạy lại trên c230029; chờ điều phối gộp và chạy full suite.** Chưa push, chưa merge.
  Phần 2 (watermark trong dải phụ đề, job 134/143) làm tiếp trên cùng nhánh.
- Bằng chứng: `docs/evidence/scrolling-text/README.md` (quy tắc, số đo, cổng). Kết quả có chữ OCR thật chỉ nằm trong
  `reports\evidence\ticker-d\` (không vào Git).

## Đã xong (phần 1)

### 1. Vì sao lọt
- Trang nguồn cho một dòng chữ chạy ngang dải trên trong khoảng 15–20 s. Quét chữ đọc 3 s một lần nên một lượt chạy
  thành nhiều track ngắn (mảnh vào ở mép phải chỉ một lần đọc, mảnh rộng, mảnh ra ở mép trái), tên miền hay mất dấu
  chấm ("…CCOM", "EXAMPLE COM"). Mỗi mảnh bị xét riêng: job 107 làm mờ từ lần đọc thứ hai (2928 s, dòng chữ có từ
  khoảng 2922 s); job 108 chỉ là ứng viên phụ đề xuất KEEP (chưa ai trả lời), không làm mờ gì.

### 2. Cách sửa (chỉ ở bước dựng danh sách duyệt; không đổi khóa cache quét nào)
- `src/biliflow/scrolling_text.py` (mới):
  - `promote_scrolling_text` gộp các mảnh của một lượt chạy (cùng dải trên/dưới, chữ nối nhau, đi từ mép này sang
    mép kia hoặc trượt) thành một track phủ cả dải, từ một lần đọc trước lần thấy đầu (hai lần nếu lần đầu không ở
    mép) tới sau lần thấy cuối, đề xuất BLUR, nằm ở danh sách chính;
  - `web_address_tokens` nhận địa chỉ web cả khi mất dấu chấm hoặc thêm khoảng trắng (chữ hoa `NAMECOM`, `NAME COM`,
    `NAME. COM`, `NAME,COM` với COM/NET/ORG/VN/TV; `name.tld`, `www`, `http`). Quy tắc chung, không viết riêng cho
    trang nào;
  - `promote_web_address_tracks`: track có địa chỉ web luôn vào danh sách chính, đề xuất BLUR.
- `src/biliflow/review_decision_carry.py` (mới) + `build_review_queue(..., keep_decisions=True)`: dựng lại danh sách
  mà giữ mọi quyết định cũ; BLUR cũ được nới theo thẻ dài hơn (job 107: 2928 → 2922 s); KEEP/CUT/CẦN THÊM NGỮ CẢNH
  chỉ chuyển sang thẻ không cho thấy gì hơn thẻ cũ (thời gian và khung nằm trong thẻ đã duyệt): KEEP một mảnh không
  thành KEEP cả dải, CUT không lan ra thời gian chưa ai xem; quyết định không khớp thẻ mới nào thì được giữ nguyên
  thẻ cũ. Danh sách cũ không đọc được, hoặc của video nguồn khác, thì dừng chứ không ghi đè. Mặc định tắt.
- `src/biliflow/ad_text_memory.py` (mới, mục 5): BLUR trên thẻ có địa chỉ web (hay dòng chữ chạy) ghi địa chỉ đó
  (chữ thường, bỏ dấu, bỏ chấm và khoảng trắng) vào `state/ad-text-memory.json` trước khi lưu quyết định; không ghi
  được thì từ chối BLUR, không ghi gì. Không gì xóa một địa chỉ đã nhớ. Lần dựng sau (khi bật bộ nhớ của người dùng,
  như Control Center) đề xuất BLUR, ở danh sách chính, cho track có địa chỉ đã nhớ, kể cả dạng quy tắc không đọc
  được ("examplecom", "EXAMPLE - COM").
- `src/biliflow/review_workflow.py`: gọi các hàm trên khi dựng thẻ chữ và khi ghi quyết định; thẻ gộp ghi mọi
  track và ảnh xem trước.

### 3. Cổng (chi tiết: `docs/evidence/scrolling-text/README.md`)
- Test đơn vị `tests/test_scrolling_text.py` 24 OK; test clip tự làm `tests/test_scrolling_text_clip.py` (OCR thật,
  CPU) OK, trên bd0a7b8 thì hỏng (nhiều thẻ một lượt).
- Dựng lại trên bản chép của job 107, 108, 128, 140, 147 (root tạm): mỗi lượt một thẻ chính đề xuất BLUR, job 107 phủ
  2922–2943,5 s (gồm 2924 s); giữ đủ mọi quyết định cũ (mất 0).
- Số thẻ (bd0a7b8 → nhánh): 107 7 → 7, 108 5 → 6, 128 4 → 4, 140 3 → 3, 147 8 → 8.
- Golden v2: bắt thêm 1 (T3 quảng cáo, must-catch: partial → caught), mất 0, bẫy mới 0, dương tính giả danh sách
  chính 24 → 24.
- Khóa cache quét: không đổi (10 stage).
- Xuất thử cửa sổ job 107, 108 (root tạm, x264): bản xuất cũ còn đọc được dòng chữ (107: 2925–2927,5 s; 108: gần cả
  lượt), bản mới OCR không đọc được chữ nào.
- Dải dưới (job có BLUR ở dải dưới: 130, 134, 136, 143, 145 còn gốc; 62–66, 70–72, 99, 100, 101–103, 112, 118 mất
  gốc): số thẻ đề xuất BLUR ở dải dưới không giảm ở job nào (134: 1 → 51, 143: 0 → 45). Số thẻ tăng ở 4 job có
  watermark trang nguồn trong dải phụ đề: 134 42 → 56, 136 38 → 48, 143 64 → 66, 145 50 → 53. Thẻ thêm là các lần
  thấy watermark có địa chỉ web mà bản cũ không hiện ở đâu cả (134: 13, 136: 8, 143: 2, 145: 3) hoặc chỉ là ứng viên
  phụ (134: 1, 136: 2). Phần 2 sẽ gộp các lần thấy của một watermark thành một thẻ. Dựng lại giữ quyết định: mất 0;
  thẻ mới chờ duyệt 0, 14, 2, 2, 3.
- Chạy lại trên c230029 (sau mục 5 và các sửa theo review): test `test_scrolling_text` 25, `test_ad_text_memory` 12,
  `test_review_decision_carry` 11, clip OCR thật, cùng 13 module duyệt khác đều OK; cổng 25 job cho đúng các số trên
  (thẻ dòng chữ chạy giữ nguyên thời gian và số mảnh), mất 0 ở cả mười job còn gốc, thẻ mới chờ duyệt: 108 1 (thẻ dòng
  chữ chạy, trước đây chưa ai trả lời), 134 14, 136 2, 143 2, 145 3; golden v2: bắt thêm 1, mất 0, bẫy mới 0; khóa
  cache quét không đổi. Quy tắc địa chỉ web thu hẹp (bỏ "SMART TV", "Saigon, VN", "Wait.Me"…) giữ đủ 1.125 lần bắt
  thật, không thêm lần nào.

### 4. Chưa làm / cần quyết định
- **Mục 5 (học từ BLUR của người dùng):** xong ở e6daeb5 (mục 2 ở trên). Bộ nhớ chỉ thêm, không tự xóa; muốn quên
  một địa chỉ thì hiện phải sửa tay `state/ad-text-memory.json` (chưa có nút).
- **Mục 6 (giới hạn 250 track):** không đổi. Giới hạn giữ mọi ứng viên duyệt (mọi định tuyến quảng cáo), chỉ bỏ track
  không phải ứng viên muộn nhất. Job 107 bỏ 115 (đều LIKELY_CREDITS từ 3954 s), job 108 bỏ 237 (235 credits, 2 scene
  text, từ 3666 s); không track quảng cáo nào bị bỏ. Sửa thì đổi khóa cache stage text: chờ điều phối và người dùng.
- **Mục 7 (job cũ):** chưa có nút dựng lại danh sách duyệt. "Chạy lại" tạo revision mới (quét lấy lại từ cache nếu
  còn) nhưng danh sách mới không có quyết định nào. Đề xuất nút "Dựng lại danh sách duyệt (giữ quyết định)" gọi
  `build_review_queue(keep_decisions=True)` trên revision đang dùng, đưa job đã xuất về duyệt rồi xuất lại: cần điều
  phối đồng ý trước.

## Cách người dùng áp dụng cho job cũ (khi nhánh đã vào main và Control Center khởi động lại)
- Job còn video gốc (107, 108, 128, 140, 147): hiện chỉ có "Chạy lại" (duyệt lại toàn bộ thẻ). Nếu điều phối đồng ý
  nút "Dựng lại danh sách duyệt", người dùng bấm nút đó, chỉ duyệt thẻ mới (job 108: thẻ dòng chữ chạy 402–423,5 s),
  rồi "Xuất video" lại.
- Job đã mất gốc (101–103, 112, 118): không xuất lại được.
