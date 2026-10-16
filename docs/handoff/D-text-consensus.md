# Bàn giao D – bước quét chữ giữ hộp của logo cố định trong nhóm (job 145)
- Nhánh: fix/text-consensus-logo (từ main 419377f) · Worktree: `temp\wt-text-consensus` · Cập nhật: 2026-10-16
- Kế hoạch đã được điều phối duyệt: `docs/handoff/D-text-consensus-plan.md` (phương án A). Bằng chứng:
  `docs/evidence/text-consensus/README.md`, `gate-summary.json`. Số đo có chữ OCR thật chỉ nằm trong
  `temp\text-consensus\` (không vào Git).
- Trạng thái: **xong, mọi cổng đạt; chờ điều phối chạy full suite và gộp.** Chưa push, chưa merge.

## 1. Đã sửa (chỉ `src/biliflow/textscan.py`)
- **Footprint của track:** mỗi track có `footprint_box`, là khung chung của chính các lần đọc của nó.
- **Neo:** trong một nhóm góc, thành viên được đọc ít nhất 10 lần và tự kéo dài ít nhất `minimum_span` của nhóm là
  "neo".
- **Khi nào footprint của neo được hợp vào khung chung:**
  - chưa nằm ≥ 95 % trong khung chung;
  - chạm khung chung;
  - cao không quá 2,5 lần khung chung.
- **Ghi lại:** `overlay_box_sources` giữ khung cũ và các neo.
- Hộp không bao giờ nhỏ hơn trước. Không đổi ngưỡng, cách gộp nhóm, định tuyến hay giới hạn 250.
- **Khóa cache:** chỉ khóa bước `text` đổi; 9 bước còn lại giữ nguyên (đã so trên bản sạch của main và nhánh).

## 2. Cổng
- **Test:**
  - `test_textscan`: 10 test mới;
  - `test_fixed_logo_scan`: OCR giả lập theo dạng đọc thật của job 145; hỏng 2/3 trên main, đạt trên nhánh;
  - `test_fixed_logo_clip`: OCR thật trên clip tự làm; đạt trên cả main lẫn nhánh, vì OCR thật trên clip không gộp
    logo với dòng chữ chạy;
  - 97 test của các module dùng bước quét chữ: OK.
- **Quét lại trên GPU:**
  - người dùng đồng ý, hàng đợi thật trống, khoảng 19 phút;
  - job 145, 130, 134, 136, 143 và nguồn golden v2;
  - số track trùng báo cáo cũ (145: 856);
  - hàm gộp của main chạy trên cùng đầu vào cho đúng khung baseline.
- **Khung chung đổi:** trên cả 14 báo cáo quét lại, chỉ nhóm góc trên phải của job 145 đổi, từ [432,18,960,43]
  thành [432,18,960,77]. Các neo là track 2, 554, 606, 839 (logo, track bị đứt ba lần).
- **Danh sách của job 145:** dựng từ text-scan.json vừa quét lại, cùng báo cáo visual_logo của revision 2 (không có
  thẻ 175–180).
  - Một thẻ chữ đề xuất BLUR, 0–2561 s, vùng (0,21,1280,74), phủ logo. Vẫn 7 thẻ.
  - Với báo cáo visual_logo của revision 1: cùng thẻ, cùng vùng; thẻ visual 175–180 nhập vào thẻ đó.
  - `fixed_text_logo` (G12) không gộp gì, không nhân đôi vùng, không nới thêm.
- **Dải phụ đề yếu đi:** 0 ở 130/134/136/143/145.
- **Golden v2:** khung cũ so với khung mới mất 0, bẫy mới 0, bắt thêm 0 (19/19, dương tính giả 24/24). So với
  baseline v2 lưu ngày 10-13: +1, chính là phần G11 đã bắt thêm.
- **Xuất thử job 145 (x264):** OCR đọc được logo:

  | Cửa sổ | Gốc | Bản người dùng đã xuất | Sau một BLUR trên thẻ mới |
  |---|---|---|---|
  | 130–230 s | 199 | 198 | **0** |
  | 400–500 s | 200 | 200 | **0** |

  Dòng chữ chạy (400–500 s): sau khi sửa vẫn 0.

## 3. Mục 6 (giới hạn 250 track): chỉ đo, chưa đổi; người dùng quyết
- **Giới hạn bỏ gì:** 580–694 track tham khảo mỗi phim của bộ này, phần lớn ở vùng phụ đề, bỏ từ cuối phim trước.
- **Vì vậy:** thẻ watermark dưới phụ đề dừng sớm 200–400 s so với cuối phim:
  - 130: 2247 so với 2646 s;
  - 134: 2427 / 2643;
  - 136: 2436 / 2691;
  - 143: 2361 / 2668;
  - 145: 2313 / 2561.
- **Trên bản xuất thật:** bản của job 145 (revision 2) còn đọc được watermark ở 8/24 mẫu sau 2313 s (gốc 10/24).
  Bản của job 130 thì 0/24, vì danh sách cũ của nó làm mờ lâu hơn.
- **Golden v2 khi bỏ giới hạn:** 19/19, mất 0, bẫy mới 0, dương tính giả 24 → 22.
- **Nếu làm mục 6:** làm trên cùng nhánh trước khi gộp, để khóa bước chữ chỉ đổi một lần.
  - Công: khoảng 2–3 giờ cộng cổng.
  - Rủi ro: báo cáo lớn hơn và thêm thẻ.

## 4. Giới hạn (từ một lượt review đọc mã, không có lỗi nặng)
- **Footprint giữ cả lần đọc lớn tới 1,8 lần cỡ thường.** Vì vậy hộp của job 145 xuống tới y 77 trên khung phân
  tích, trong khi logo hết ở 68. Chỉ mờ thừa một chút bên dưới, không lọt.
- **Biên chiều cao mỏng:** job 145 là 2,12 so với giới hạn 2,5. Ở một phim có mẩu dòng chữ chạy mỏng hơn (khoảng
  20 px), cùng logo đó sẽ không được hợp. Khi đó nó chỉ được ghi `joined: false`, và bước dựng không hiện ra.
  Điều phối quyết có nới không.
- **Bề rộng chưa có giới hạn.** Trên 14 báo cáo quét lại chỉ job 145 đổi.

## 5. Người dùng áp dụng cho job 145
Sau khi nhánh vào main và Control Center khởi động lại:
- "Chạy lại" job 145 (bước chữ quét lại, khoảng 2 phút);
- BLUR thẻ chữ cố định (0,21,1280,74);
- duyệt lại các thẻ khác;
- "Xuất video" lại.

Bản đã xuất hôm nay còn lộ logo, và còn lộ watermark dưới phụ đề ở khoảng 4 phút cuối.
