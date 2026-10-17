# D, ảnh xem trước hiện vùng các thẻ khác đã làm mờ (2026-10-17)

Nhánh `fix/review-preview-blurs` từ `main` `41169fc` (worktree `temp\wt-preview-blurs`). Commit: xem `git log 41169fc..`.
Chưa gộp, chưa push. Trong `docs/DASHBOARD_V2_REVIEW_PLAN.md` mục 5, thay đổi này là S12.

Chỉ sửa Dashboard V2 (`dashboard_v2/`) và tài liệu.
- Không đổi `review_workflow.py`, các bước quét, quyết định hay bản xuất.
- Khóa cache: 10/10 bước giống `41169fc` (`build_code_manifest`, so cây nhánh với `git archive 41169fc`).
- Trang cũ không đổi (D2).

## 1. Lỗi

Trên ảnh xem trước của một thẻ, vùng mà thẻ khác đã làm mờ (watermark ở dải phụ đề, chữ cố định góc trên, logo) vẫn
hiện rõ. Người dùng không biết bản xuất sẽ làm mờ chỗ đó, và dễ đọc khung đỏ là chỗ bị làm mờ (job 134).

## 2. Đã làm

### Vùng nào được vẽ (`review-detail.js`: `blurCovers`, `coversAt`)
- Vẽ vùng của thẻ KHÁC, giống `review_workflow.approved_operations`:
  - thẻ đã quyết định BLUR có vùng riêng: vùng đó, từ đầu đến cuối thẻ;
  - thẻ `discrete_detected_intervals`: theo từng khoảnh khắc.
- Không vẽ:
  - thẻ KEEP hoặc CUT;
  - BLUR toàn khung (ghi chú đếm riêng "N đoạn trùng thời gian đã được duyệt làm mờ toàn cảnh");
  - thẻ ứng viên (`advisory`), vì bản xuất bỏ qua chúng cho đến khi được quyết định;
  - chính thẻ đó;
  - thẻ logo mà thẻ này mượn khung đỏ (S9: khung đó đã vẽ nét đứt đỏ).
- Theo thời điểm của ảnh (`thumbTime`, sai số ±0,05 s):
  - Ảnh có thời điểm (khung lấy từ video; ảnh báo cáo có giây trong tên `…-100.500s.png`): vẽ vùng có một khoảnh khắc
    chứa thời điểm đó.
  - Ảnh không có thời điểm (ảnh xem trước của máy quét chữ `track-N.jpg`): chỉ vẽ vùng có một khoảnh khắc chứa trọn
    thẻ (thẻ rời rạc: chứa trọn từng khoảnh khắc của nó). Vùng chỉ trùng một phần thì không vẽ; câu dưới ảnh ghi
    "Còn N vùng … không vẽ trên ảnh này".
- Đổi ảnh (chạm ảnh ở dải ảnh khi video không phát được) thì phần vẽ theo ảnh mới; lớp giữ thời điểm ở `data-t`.

### Vẽ như bản xuất (`review-cards.js`, `review-media.js`, `review.css`)
- Vùng đã làm mờ (`applied`):
  - phần ảnh trong vùng bị làm mờ;
  - viền nét đứt xanh da trời (`#38bdf8`), khác hẳn khung đỏ;
  - nhãn "đã làm mờ ở thẻ watermark (toàn phim)", hoặc ngắn hơn khi chật: "đã làm mờ · watermark", "đã làm mờ".
  - Loại thẻ trong nhãn: watermark, dòng chữ chạy, chữ cố định, logo cố định, logo, chữ.
- Cách làm mờ: một canvas cắt vùng từ chính `<img>` đang hiện, thu nhỏ còn vài ô (một phần ba cạnh ngắn của vùng,
  ít nhất 1/160 bề ngang ảnh) rồi kéo lại có làm mịn.
  - Không dùng CSS `filter`/`backdrop-filter`: R4-B3 cấm backdrop-filter, vì nó làm Chrome nhấp nháy.
  - Không dùng `<img>` thứ hai: ảnh duyệt là `no-store`, nên sẽ phải tải lại.
  - Chromium không vẽ được một phần của SVG không có kích thước riêng (ảnh demo). Chỉ khi đó mới cắt từ bản canvas
    của cả ảnh (tối đa 1920 px mỗi cạnh).
  - Độ mờ chỉ gần giống bản xuất, không trùng từng điểm ảnh.
- Thẻ chỉ ĐỀ XUẤT BLUR mà chưa quyết định (`pending`): **có vẽ**, nhưng khác hẳn: viền chấm tím (`#c084fc`), không làm
  mờ gì, nhãn "đề xuất làm mờ ở thẻ …, chưa duyệt" (ngắn: "đề xuất · …, chưa duyệt", "đề xuất, chưa duyệt").
  - Lý do vẽ: người duyệt thẻ dòng thoại cần biết vùng watermark bên dưới có thể bị làm mờ, nếu thẻ watermark được
    duyệt như đề xuất.
  - Lý do không làm mờ: chưa ai quyết định, và bản xuất lúc này chưa làm mờ chỗ đó; ảnh phải cho thấy đúng hiện trạng.
- Nhãn:
  - Đặt cho thẻ điện thoại (rộng 360 px, thẻ hẹp nhất), ước lượng 6,5 px mỗi ký tự.
  - Vùng đã làm mờ: thử nhãn trong vùng trước (ảnh trong đó đã mờ), ở cả ba độ dài, rồi mới ra ngoài. Vùng đề xuất thì
    ra ngoài trước.
  - Không bao giờ đè lên nhãn khác hay lên khung đỏ của thẻ. Không còn chỗ thì không có nhãn; câu dưới ảnh vẫn nói đủ.
- Dưới ảnh có câu tóm tắt (`D.coverLegend`), ví dụ "Vùng mờ viền xanh: 2 vùng thẻ khác đã làm mờ suốt đoạn này — bản
  xuất cũng làm mờ."
- Ô phóng to (session R) vẽ cùng các vùng đó, ở cùng tỷ lệ: làm mờ, nét đứt xanh hoặc chấm tím, nằm dưới nét đỏ.
  Thẻ dòng thoại có watermark sát bên dưới nay thấy watermark bị làm mờ ngay trong ô phóng to.
- Khi một thẻ khác đổi quyết định, danh sách được vá tại chỗ (cùng nút thẻ): lớp vẽ đổi, canvas vẽ lại, ô phóng to vẽ lại.

### Ghi chú vùng chồng ("Chi tiết kỹ thuật", `D.coverageNote`)
- Có ở MỌI thẻ có vùng chồng, không chỉ thẻ an toàn. Thay `overlapCoverage` cũ.
- Tính mọi loại thẻ có vùng: `repeated_watermark`, `scrolling_text`, chữ và logo cố định, logo hình.
- Mỗi dòng ghi:
  - loại thẻ và chữ máy đọc;
  - tọa độ;
  - "cùng vùng khung đỏ của thẻ này" (nếu có);
  - đã làm mờ hay chỉ đề xuất;
  - khoảng thời gian (toàn phim, N khoảnh khắc, hoặc mm:ss–mm:ss);
  - trùng thẻ này ở đâu.
- Tối đa 6 dòng, rồi "và N vùng khác".
- Thẻ không có vùng riêng có thêm câu "quyết định bên dưới áp dụng cho cả đoạn …".

### Ẩn phần vẽ
- "Ẩn khung" (session R) ẩn phần vẽ, câu tóm tắt và các vùng trong ô phóng to; "Hiện khung" đưa lại.
- Ẩn trong lúc video phát: khung video đổi liên tục, còn phần vẽ chỉ đúng với ảnh tĩnh.
- Ẩn trên thẻ đã chọn CẮT hoặc làm mờ toàn khung: cả đoạn đã bị cắt hay làm mờ.
- Chế độ điện thoại dùng cùng trang và cùng file. Đã kiểm ở 390 × 844 cảm ứng, sáng và tối.

## 3. Giới hạn (cố ý)
- Ảnh nhỏ ở dải ảnh không có phần vẽ: chỉ cao vài chục px. Chạm vào thì ảnh lên khung chính, kèm phần vẽ (khi video
  không phát được; thẻ phát được thì chạm là tua video).
- Review của agent (không có CRITICAL, HIGH hay MEDIUM):
  - Đã sửa:
    - canvas cả ảnh mỗi lần vẽ, nay chỉ dùng khi là SVG;
    - vùng bắt đầu ngoài mép trái hoặc trên, nay chỉ giữ phần trong khung.
  - Giữ nguyên:
    - Lần vá đầu của một thẻ mà ảnh chưa tải xong dựng lại lớp vẽ rỗng một lần, cùng kiểu `setHtml` có sẵn.
    - Bóng tối mảnh của viền: cùng kiểu với khung đỏ và khung AI.
  - Đo: `blurCovers` + `coversAt` cho cả 500 thẻ của một hàng 500 thẻ (42 vùng làm mờ dài) mất 85 ms trong Node, sau
    khi xét thời gian trước khi dựng vùng. Mỗi lần vá chỉ tính các thẻ đang dựng.
- `browser-check.cjs` (trang V2 chung): 8 mục đạt rồi treo ở R4.3 ("Duyệt cảnh", `waitForFunction`, dòng 270). Trên
  `41169fc` cũng y như vậy, nên không liên quan việc này.

## 4. Cổng
- `node dashboard_v2/verify-review.cjs`: 45/45. Session D có 7 mục, gồm:
  - chồng theo thời gian và theo khoảnh khắc;
  - thẻ không chồng thì không vẽ;
  - "Ẩn khung" ẩn phần vẽ;
  - ghi chú tính đủ loại thẻ;
  - vẽ canvas và ô phóng to trên canvas giả.
- `browser-check-review-covers.cjs` (mới): 9/9, trên Edge với Control Center giả. Kiểm:
  - 390 px sáng và tối, 1440 px;
  - nhãn nằm trong ảnh và không đè nhau;
  - ô phóng to;
  - "Ẩn khung";
  - ảnh báo cáo theo khoảnh khắc;
  - BLUR ở thẻ khác vá tại chỗ;
  - ẩn khi video phát;
  - chỉ một POST (quyết định của test), không lỗi console hay CSP.
- Không lỗi:
  - `browser-check-review.cjs` 27/27, `-write` 19/19, `-bulk` 13/13, `-layout` 13/13;
  - `-phone` (qua `tests.test_dashboard_v2_review.ReviewR4PhoneListener`);
  - `verify.cjs` 50/50, `verify-adapter.cjs` 42/42, `verify-download.cjs` 23/23, `verify-download-accounts.cjs` 55/55.
- Python, 130 test, OK, không bỏ qua test nào (có `NODE_PATH` thì `ReviewR4PhoneListener` chạy trình duyệt):
  - `tests.test_dashboard_v2_review`, `_frontend`, `_phone`, `_phone_hardening`, `_route`, `_contract`, `_status`,
    `_downloads`;
  - `tests.test_opening_card_display`, `tests.test_review_text_previews`.
- Playwright: dùng `playwright-core` có sẵn trong `.venv` qua một shim ở `temp\d-pw`, chạy Edge của máy
  (`NODE_PATH=temp/d-pw/node_modules`). Không tải gì.

## 5. Kiểm bằng mắt trên dữ liệu thật (Control Center thử)
- Cách làm: `docs/evidence/preview-blurs/README.md`.
  - `copy_jobs.py` chép hàng duyệt và ảnh xem trước của job 145 (27 thẻ, 256 ảnh) và job 134 (25 thẻ, 245 ảnh; đã lưu
    trữ, báo cáo còn trong `reports`) vào gốc tạm `temp\d-fixture`.
  - DB thật chỉ được đọc (mode=ro). Cổng 8799. Không gọi Control Center thật.
- `shoot.cjs` (chỉ GET) chụp thẻ dòng thoại có watermark bên dưới và thẻ chữ cố định góc trên của mỗi job, ở 390 px
  (sáng, tối, "Ẩn khung") và 1440 px, kèm cả hộp duyệt ở 1440 px. Kết quả:
  - 16 ảnh, 0 POST, 0 lỗi; hàng duyệt giữ nguyên SHA-256;
  - mọi vùng làm mờ đã vẽ; nhãn trong ảnh và không đè nhau;
  - ô phóng to của thẻ dòng thoại có viền xanh của watermark (0 khi "Ẩn khung").
- Ảnh và `shoot-result.json`: `<install>\reports\evidence\preview-blurs\` (ngoài Git, có hình phim thật).

## 6. Người dùng
- Mở một job có watermark ở dải phụ đề trên điện thoại.
- Xem thẻ dòng thoại: vùng watermark mờ, viền xanh nét đứt, kể cả trong ô phóng to.
- Bấm "Ẩn khung" và "Hiện khung".
- Bấm BLUR ở thẻ watermark rồi quay lại thẻ dòng thoại.
