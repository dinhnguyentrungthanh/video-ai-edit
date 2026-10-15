# Bàn giao D – logo trang nguồn cố định ở góc trên phải lọt bản xuất (job 145)
- Nhánh: fix/top-logo-box (từ de91261, đỉnh fix/ticker-subtitle) · Worktree: `temp\wt-top-logo` · Cập nhật: 2026-10-15
- Chạy ở: PC · Trạng thái: **xong, mọi cổng đạt; chờ điều phối gộp (sau G11) và chạy full suite.** Chưa push,
  chưa merge.
- Bằng chứng: `docs/evidence/top-logo-box/README.md`, `gate-summary.json`. Kết quả có chữ OCR thật chỉ nằm trong
  `temp\top-logo\` (không vào Git).

## 1. Vì sao lọt (trả lời câu hỏi của điều phối)
- **Dải trên không bị cắt; hộp OCR của logo bị bỏ ngay ở bước quét chữ.** Bước dựng danh sách không bỏ gì: báo cáo
  quét đã không còn hộp đó.
- Job 145: tên trang là một logo cố định ở góc trên phải suốt phim (hộp OCR khoảng x 863–1149, y 53–86 trên khung
  1280×536), ngay dưới dòng chữ chạy của trang (y khoảng 21–57).
- Quét chữ đọc logo ở mọi mẫu (track 2, 607 lần, 0–2562 s). Hộp riêng của track là 305×53 điểm ảnh phân tích, cao
  gấp đôi dòng chữ chạy.
- Bước quét gộp 12 track góc trên phải cùng chữ, cùng chỗ thành một (`textscan._promote_repeated_corner_overlays`).
  11 track kia nhiều khả năng là mẩu của dòng chữ chạy, vì dòng đó cũng có tên trang.
- Khung chung `_consensus_overlay_box` chỉ giữ các hộp gần trung vị. Kết quả là khung của dòng chữ chạy
  [432,18,960,43]; hộp cao hơn của logo bị coi là lệch và bị bỏ.
- Vì vậy thẻ cả phim đề xuất (576,24,704,30) trên main, đúng vùng người dùng đã duyệt, và (0,21,1280,32) trên
  fix/ticker-subtitle. Thẻ visual_logo đúng chỗ (857,47,298,45) chỉ có 175–180 s.
- Trên 118 báo cáo quét chữ của máy, không track gộp nào khác mất hộp kiểu này.
- **Sửa ngay ở bước quét thì phải đổi khóa cache bước chữ (`textscan.py`), nên không làm** (điều kiện c). Nếu muốn,
  điều phối quyết định; các job cũ khi đó phải quét lại bước chữ.

## 2. Cách sửa: chỉ ở bước dựng danh sách duyệt
Code: `src/biliflow/fixed_text_logo.py`, một lời gọi trong `build_review_queue` ngay trước
`reconcile_persistent_overlay_items`.

Một thẻ visual_logo được coi là cùng dấu với một thẻ chữ cố định (`persistent_overlay` hoặc `repeated_watermark`)
khi đủ cả bốn điều kiện:
- **Cùng chữ:** một chữ OCR của thẻ visual_logo (từ 6 chữ cái trở lên; đã bỏ nhãn của máy quét, nhãn grounding
  như "promotional banner" và mã `</s>`) giống ≥ 0,75 hoặc chứa một chữ mà thẻ chữ đọc riêng. Tên chỉ nằm trong
  một câu dài thì không tính.
- **Cùng chỗ:** hộp của nó chạm hộp thẻ chữ: ít nhất nửa bề rộng nằm trong các cột của thẻ, cách không quá 8 px
  hoặc nửa chiều cao của nó, cao không quá 2,5 lần thẻ chữ. Hộp đã nằm ≥ 95 % trong thẻ chữ thì không cần làm gì.
- **Cùng lúc:** thời gian của nó chồng một khoảng của thẻ chữ.
- **Đúng loại thẻ:**
  - thẻ chữ đề xuất BLUR;
  - thẻ visual_logo là thẻ thương hiệu thường (không phải logo cố định, mở đầu, kết, đoạn kết hay tiêu đề), không
    đề xuất KEEP, và vùng có nguồn OCR;
  - cả hai thẻ chưa có quyết định.

Khi đủ điều kiện:
- **(a)** Vùng đề xuất của thẻ chữ thành hợp của hai hộp. Ghi lại trong `fixed_text_logo` (vùng cũ, các hộp logo)
  kèm một dòng lý do.
- **(b)** Thẻ visual_logo lấy thời gian của thẻ chữ; khoảng cũ giữ trong `previous_interval`.
- Bước gộp có sẵn sau đó nhập thẻ logo vào thẻ cả phim. Kết quả: **một BLUR làm mờ cả dòng chữ chạy lẫn logo suốt
  phim.**

Các giới hạn về loại thẻ có từ một lượt review đọc mã:
- Một logo cố định (visual) nằm trong hộp mới sẽ nuốt thẻ chữ và chỉ làm mờ hộp của nó, làm mất phần làm mờ dòng
  chữ chạy.
- Một thẻ chữ đề xuất KEEP hoặc không đề xuất gì sẽ nuốt đề xuất BLUR của logo.

Không đổi: khóa cache quét (cả 10 bước), các ngưỡng, thẻ đã có quyết định, việc bắt quảng cáo.

## 3. Cổng (chi tiết trong `docs/evidence/top-logo-box/README.md`)
- **Test tái hiện** `tests/test_fixed_text_logo.py` (11 test): đều OK. Test danh sách dựng theo dạng job 145 hỏng
  trên de91261 (vùng (576,24,704,30)). Ngoài ra 24 module test duyệt/chữ/logo: 485 OK.
- **Cổng 25 job** (de91261 → nhánh):
  - số thẻ chỉ đổi ở job 145 (8 → 7, thẻ logo nhập vào thẻ cả phim);
  - thẻ đề xuất BLUR ở dải dưới không đổi;
  - **dải phụ đề yếu đi 0 ở cả 25 job**;
  - dựng lại giữ quyết định mất 0; ở job 145 và 130, BLUR cũ của người dùng mang sang phủ cả hộp logo.
- **Phạm vi trên cả 72 job:** quy tắc chỉ chạm 4 job.
  - Job 145: vùng (0,21,1280,32) → (0,21,1280,71).
  - Job 70, 72, 130: nới 2–4 px quanh một dấu thẻ chữ đã phủ.
  - Job 72: dòng chữ dưới rộng thêm 34 px theo khung visual-logo đọc cùng chữ.
- **Golden v2:** so với de91261 bắt thêm 0, mất 0, bẫy mới 0, mọi chỉ số y hệt (quảng cáo 19/19, dương tính giả
  24). Một danh sách golden (clip job 39) đổi theo cùng quy tắc: khung watermark 156×32 → 176×54.
- **Xuất thử job 145** (x264), OCR mỗi 0,5 s trên hộp logo; cả bốn bản xuất xong và giải mã đủ:

  | Cửa sổ | Gốc | Bản người dùng đã xuất | Sau khi sửa |
  |---|---|---|---|
  | 130–230 s | 200/200 | 190 | **0** |
  | 400–500 s | 200 | 200 | **0** |

  - Dòng chữ chạy (400–500 s): gốc 165, bản đã xuất 47, sau khi sửa 0.
  - Bản người dùng đã xuất chỉ làm mờ logo ở 175–180 s, đúng chỗ họ đã BLUR thẻ visual_logo.

## 4. Cách người dùng áp dụng cho job 145
Chỉ áp dụng khi nhánh đã vào main và Control Center đã khởi động lại.
- Không cần quét lại.
- **"Chạy lại":** danh sách mới có một thẻ chữ cố định 0–2561 s, vùng (0,21,1280,71) gồm cả dòng chữ chạy lẫn logo.
  Bấm BLUR trên thẻ đó, rồi "Xuất video" lại.
- **Nút "Dựng lại danh sách duyệt (giữ quyết định)"** (chỉ khi điều phối đồng ý có nút này): BLUR cũ trên thẻ chữ cố
  định được mở rộng tới vùng mới (gồm logo); không cần duyệt lại thẻ đó.
- Job 70 và 72 không còn video gốc; job 130 chỉ thêm vài điểm ảnh, không cần làm lại.

## 5. Còn để ngỏ
- Không có thẻ visual_logo đọc cùng chữ thì một logo bị khung chung bỏ sót vẫn nằm ngoài vùng. Hôm nay chỉ job 145
  gặp; muốn sửa tận gốc phải sửa bước quét chữ (đổi khóa cache, quét lại).
- Chữ OCR toàn chữ thường, không dấu chấm, không chữ số trông giống nhãn grounding nên không được dùng làm chữ đọc.
