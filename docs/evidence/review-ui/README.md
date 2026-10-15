# Session R: ảnh trước/sau của trang duyệt (2026-10-14)

Ảnh trong `shots/` chụp một job tự tạo, không dùng phim thật, tên trang web thật hay đường dẫn máy thật. Job có ba thẻ chữ giống các thẻ của job 134 mà người dùng duyệt trên điện thoại:

- **Logo góc.** Chữ "LOGO.EXAMPLE" cố định ở góc trên bên phải.
- **Tên trang mờ.** Chữ "TRANG.EXAMPLE" rất mờ nằm trong vùng phụ đề, máy quét đọc lẫn với phụ đề. Nhãn máy quét in lên ảnh là "REVIEW 58 | subtitle".
- **Dòng chữ chạy.** Dòng chữ chạy ngang ở mép trên, máy quét đọc ba lần: lúc đầu dòng mới vào, lúc hiện đủ, lúc chỉ còn đuôi.

## Cách dựng lại

1. Dựng root tạm (thư mục mới trong `<install>\temp`).
   - Dùng code main để có ảnh "trước", code của nhánh để có ảnh "sau".
   - Video 30 giây do script tự vẽ.
   - `text-scan.json` có đủ track, ảnh xem trước in nhãn bằng chính `textscan._annotate`.
   - `review-queue.json` dựng bằng `build_review_queue` của cây code đó.
   - Job 1 ở trạng thái WAITING_REVIEW, hàng đợi tạm dừng.

   ```
   python -I docs/evidence/review-ui/make_fixture.py make --src <cây main>/src --root <install>/temp/r-fixture-before
   python -I docs/evidence/review-ui/make_fixture.py make --src <nhánh>/src --root <install>/temp/r-fixture-after
   ```

2. Chạy hai Control Center thử, mỗi cái trên root và cổng riêng (không bao giờ 8765):

   ```
   python -I docs/evidence/review-ui/make_fixture.py serve --src <cây main>/src --root <install>/temp/r-fixture-before --port 8797
   python -I docs/evidence/review-ui/make_fixture.py serve --src <nhánh>/src --root <install>/temp/r-fixture-after --port 8798
   ```

3. Chụp và kiểm (Playwright, Edge chạy ẩn; trang chỉ gửi GET):

   ```
   node docs/evidence/review-ui/shoot.cjs http://127.0.0.1:8797 <root before> http://127.0.0.1:8798 <root after> <thư mục ảnh>
   ```

## Kết quả (`shots/shoot-result.json`)

- **Thẻ, quyết định, đề xuất, vùng.** Hai hàng đợi có cùng 3 thẻ (cùng id), cùng đề xuất, cùng vùng khoanh đỏ, cùng số thẻ. Chỉ khác thứ tự ảnh xem trước của thẻ dòng chữ chạy và trường hiển thị `text_preview_zones`. `source` khác nhau vì đường dẫn root khác nhau.
- **review-queue.json không đổi khi mở trang.** SHA-256 của cả hai file giữ nguyên suốt lúc trang mở, ở cả 390 × 844 (sáng, tối) và 1440 px.
- **Kiểm trên trang "sau"** (390 × 844 sáng, 390 × 844 tối, 1440 × 900 sáng):
  - Khung đỏ là nét 2 px nằm ngoài vùng: không tô, không viền đè lên chữ.
  - Ô phóng to được vẽ, ×3 trên cả ba thẻ ở điện thoại. Dòng chữ chạy rộng hơn thẻ nên vuốt ngang được, ô cao 42 px.
  - "Máy đọc" ghi đúng chữ máy đọc được.
  - Câu dưới ảnh giải thích "subtitle" là tên vùng (vùng phụ đề).
  - Ảnh đầu của thẻ dòng chữ chạy là lúc dòng hiện đủ (track 22).
  - Trang không cuộn ngang. Không có lỗi trang, không có yêu cầu nào khác GET.

## Ảnh

| Ảnh | Thấy gì |
|-----|---------|
| `before-390-light-band.jpg` / `after-390-light-band.jpg` | Trước: khung đỏ tô che chữ "TRANG.EXAMPLE". Sau: khung chỉ là nét ngoài vùng, ô phóng to ×3, câu giải thích nhãn "subtitle". |
| `after-390-light-band-bare.jpg` | Sau khi bấm "Ẩn khung": ảnh không còn khung đỏ, nhãn khung hay lớp phủ, ô phóng to vẽ lại không có nét đỏ. Nút đổi thành "Hiện khung". Nét vàng trên ảnh là của máy quét. |
| `before-390-light-ticker.jpg` / `after-390-light-ticker.jpg` | Trước: ảnh đầu chụp lúc dòng chữ mới vào, bị khung tô che. Sau: ảnh đầu là lúc dòng hiện đủ; ô phóng to ×3 vuốt ngang. |
| `*-390-light-band-read.jpg`, `*-390-light-ticker-read.jpg` | Phần dưới ảnh. Sau: "Máy đọc: “TRANG.EXAMPLE” 4 cách đọc gần giống / “Ngày mai chúng ta lên đường sớm.”" và dòng chữ chạy rút gọn ở 70 ký tự. |
| `*-390-light-logo.jpg` | Logo góc: khung nét, ô phóng to ×3. |
| `*-390-dark-band.jpg` | Như trên, giao diện tối. |
| `*-1440-light-dialog.jpg` | Hộp duyệt ở 1440 px, hai cột. |

## Giới hạn đã biết

- **Nhãn của máy quét.** Nhãn "REVIEW n | vùng" và khung vàng do máy quét chữ in thẳng vào ảnh xem trước. Trang duyệt không xóa được, chỉ giải thích. Với dòng chữ sát mép trên, nhãn này đè một phần chữ, cả trong ô phóng to. Dải ảnh của thẻ chữ là các ảnh xem trước đó (cùng nhãn), không phải khung hình sạch lấy từ video. Chạm một ảnh khác ở dải thì ảnh lớn và ô phóng to đổi theo ảnh đó (đã thử trên Control Center thử).
- **Ảnh xem trước của một track.** Ảnh này là khung máy quét tin nhất, không chắc là lúc chữ hiện đủ nhất. Thứ tự mới xếp theo dòng OCR dài nhất của từng track. Ở job 134, ảnh đầu của thẻ dòng chữ chạy (track 713) hiện đủ dòng. Ảnh thứ hai (track 759) chỉ có "ủng hộ." dù track đó từng đọc được dòng 50 ký tự. Muốn ảnh khớp đúng lúc chữ dài nhất thì máy quét phải lưu chữ theo từng khung hình, tức là một vòng sửa phần phát hiện, ngoài phạm vi session R.
