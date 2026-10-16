# D, watermark tới mép phim (2026-10-16)

Nhánh `fix/watermark-edges` từ `main` `41d219d` (worktree `temp\wt-wm-edges`). Chưa gộp, chưa push. Chi tiết
tiếng Anh: `docs/evidence/watermark-edges/README.md`.

## 1. Lỗi

Job 134 rev 2 ("Chạy lại" thật trên G13): thẻ repeated_watermark ở dải phụ đề (552,443,176,31) kết thúc ở
2643,5 s, phim dài 2657,2 s.
- Chữ danh sách đoàn phim che các lần đọc cuối, nhưng watermark vẫn nằm trong khung tới hết phim.
- Bản xuất lộ watermark 14 s cuối.
- 130 (thiếu 8,7 s) và 136 (thiếu 10,7 s) giống vậy. 143 và 145 đã tới cuối phim.

## 2. Sửa (chỉ ở bước dựng danh sách, không đổi khóa cache)

**`subtitle_band.py`.** Áp dụng cho nhóm watermark có từ `WATERMARK_ROBUST_RUNS` (10) lần đọc trở lên.
- **Đầu thẻ:** khoảnh khắc đầu cách đầu vùng quét ≤ `WATERMARK_BRIDGE_SECONDS` (150 s) thì bắt đầu từ đầu
  vùng quét.
- **Cuối thẻ:** khoảnh khắc cuối cách cuối vùng quét ≤ 150 s thì kéo tới cuối vùng quét.
  - Cuối vùng quét là `scan_start + scan_duration`, không quá độ dài phim.
  - Không biết cuối vùng quét thì không kéo.
- **Lý do giống quy tắc nối 150 s:** máy quét bỏ sót, watermark không rời đi. Khoảng cách được đo như lúc
  nối, từ mép khoảnh khắc tới mép vùng quét.
- **Không đổi:**
  - Nhóm ít lần đọc giữ như cũ.
  - Các khoảnh khắc rời nhau giữ khoảng hở.
- **Thẻ ghi thêm:**
  - `read_span`: thời gian thật sự đọc được.
  - Lý do nói rõ thẻ được kéo tới đầu hay cuối vùng quét.

**`review_decision_carry.py`.** Cổng dựng lại trên hàng duyệt thật của 134 tìm ra lỗi này.
- Thẻ dài hơn có id mới. Hai thẻ KEEP nằm trong khung và thời gian của nó cũng được coi là "cùng thứ":
  - nhóm watermark 2 lần đọc;
  - một dòng phụ đề 6,5 s.
- Hai thẻ đó vẫn còn thẻ riêng nhưng "phản đối" BLUR. Thẻ dài hơn vì thế để trống, còn thẻ BLUR cũ quay
  lại bên cạnh: 26 thẻ thay vì 25.
- Sửa 1: thẻ cũ đã mang sang đúng thẻ riêng của nó thì không quyết định thẻ khác nữa. Thẻ riêng là cùng id
  và cùng khoảnh khắc, hoặc cùng id trong danh sách phụ. Sửa này chỉ có thể để trống một thẻ, không bao
  giờ tự đặt quyết định.
- Sửa 2: BLUR của thẻ nhiều khoảnh khắc được mang sang khi từng khoảnh khắc cũ nằm trong một khoảnh khắc
  mới. Đây là phát hiện của review đọc mã.

## 3. Các chỗ khác (chỉ đọc, hàng duyệt đang dùng của 77 job)

- **Thẻ chữ cố định cả phim** đã dựng từ đầu tới cuối vùng quét: 39/42 thẻ dài phủ hết hai mép. 3 thẻ còn
  lại:
  - hai nhóm visual-logo bắt đầu ở 8,5 s và 15 s (job 112, 117; video gốc đã xóa, không kiểm được);
  - một dòng chữ lặp lại chỉ một phần phim (job 152, KEEP).
- **Dòng chữ chạy:** mỗi lượt chạy là một thẻ, không phải kiểu lỗi này.
- **Kết luận:** không có bằng chứng ở chỗ khác, nên chỉ sửa repeated_watermark.

## 4. Cổng (trước = 41d219d, sau = nhánh)

- **Test:**
  - 14 test mới, 11 hỏng trên 41d219d. 3 test còn lại là chốt chặn: mép xa hơn 150 s; nhóm ít lần đọc; thẻ
    cũ không còn thẻ riêng thì vẫn phản đối.
  - 515 test của 30 module duyệt: OK.
  - Full suite (một clip tổng hợp 1 s trong `input\` của worktree, đã xóa sau đó): 3.513 test OK, 53 bỏ qua, 2.545 s.
- **Báo cáo thật mới nhất** (130 r2, 134 r2, 136 r3, 143 r3, 145 r3):
  - Thẻ watermark bắt đầu ở 0 và kết thúc đúng độ dài phim (2655,167 / 2657,236 / 2702,207 / 2668,116 /
    2561,172).
  - Số thẻ không đổi.
  - Dựng lại giữ quyết định: mất 0. BLUR cũ của 134 nằm trên thẻ dài hơn, và vẫn 25 thẻ.
- **25 job:**
  - Số thẻ bằng nhau, dải phụ đề yếu đi 0.
  - Dựng lại bằng cả hai cây (10 job còn video gốc): cùng số thẻ, cùng số quyết định. Chỉ khác ở thẻ mép của
    130, 134 và 136.
- **Xuất thử 30 s cuối của 134 (x264, EasyOCR CPU):** sau 2643,5 s, OCR đọc được watermark 0/27 mẫu (41d219d:
  27/27, 4 lần đọc ra địa chỉ).
- **Golden v2:** scorecard giống hệt (598 mục, 19/19, dương tính giả 24, bẫy 0). So với baseline: +1, mất 0,
  bẫy mới 0.
- **Khóa cache:** 10/10 bước giống 41d219d.

## 5. Người dùng

- **Sau khi gộp:** job 130, 134 và 136 nhận thẻ watermark dài hơn ở lần dựng danh sách duyệt kế tiếp.
- **134:** nếu dựng lại mà giữ quyết định, BLUR đã duyệt nằm luôn trên thẻ dài hơn.
- **Trước đó:** bản xuất của ba job này lộ watermark 9–14 s cuối.
