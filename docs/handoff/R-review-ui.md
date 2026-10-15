# Bàn giao R – sửa trang duyệt (đọc rõ chữ trên điện thoại)
- Nhánh: feat/review-ui-clarity (từ main a63119b) · Commit: be38171 (code), commit sau (sửa theo review, bằng chứng, bàn giao): xem `git log -2` · Worktree: `temp\wt-review-ui` · Cập nhật: 2026-10-14
- Chạy ở: PC · Trạng thái: **xong; chờ người dùng thử trên điện thoại và quyết một việc về lịch sử nhánh (mục "Chờ người dùng"), rồi điều phối chạy full suite và gộp.** Chưa push, chưa merge.

## Đã xong

Người dùng duyệt job 134 trên điện thoại và gặp 4 lỗi. Ảnh trước/sau: `docs/evidence/review-ui/` (job tự tạo, không có ảnh phim thật). Trong `docs/DASHBOARD_V2_REVIEW_PLAN.md` mục 5, thay đổi này là S11.

### 1. Khung đỏ che chữ
- Khung đỏ (`.rv-region`) và khung vàng AI (`.rv-aibox`) giờ là `outline` 2 px nằm cách vùng 2 px, kèm bóng tối mảnh để nổi trên nền sáng. Không tô, không viền đè lên chữ. Khung mượn (S9) vẫn nét đứt. Lớp trắng "Làm mờ" (`.rv-region.blurred`) giữ nguyên, vì đó là phần minh họa quyết định.
- Nhãn khung ("áp dụng …", nhãn khung AI) dời ra ngoài nét 5 px, vẫn theo `labelPlace` (R4-B1).
- Nút **"Ẩn khung / Hiện khung"** (`aria-pressed`) bỏ mọi thứ trang duyệt vẽ đè lên ảnh: khung đỏ, nhãn của nó, khung AI, lớp "Làm mờ", "Đã chọn cắt" và bộ lọc minh họa cắt/làm mờ toàn khung. Ô phóng to vẽ lại, không có nét đỏ.
  - Nút chỉ hiện khi trang có vẽ gì đó lên ảnh. Quy tắc CSS dùng `:has()`, như `theme.css` đã dùng.
  - Trạng thái ẩn giữ theo từng thẻ khi danh sách được vá; dựng lại danh sách thì về mặc định.
  - Nhãn "REVIEW n | vùng" và khung vàng do **máy quét chữ in sẵn trong ảnh** thì không bỏ được. Tooltip của nút ghi rõ điều này.

### 2. Chữ nhỏ không đọc được
- Dưới ảnh của thẻ có **vùng khoanh đỏ của chính nó** có ô phóng to vùng phát hiện: canvas vẽ từ chính `<img>` đang hiện (cùng origin, không blob:, CSP không đổi), theo `devicePixelRatio`, tối đa 4096 px mỗi cạnh.
  - Hàm hình học: `D.magnifier` (`review-detail.js`). Hàm vẽ: `Media.drawMagnifier` (`review-media.js`).
  - Mức phóng: 3 lần. Nếu 3 lần rộng hơn thẻ thì lấy mức lớn nhất từ 2 đến 3 lần còn vừa. Nếu 2 lần vẫn không vừa (dòng chữ chạy) thì giữ 3 lần và vuốt ngang; chú thích ghi "vuốt ngang để xem hết". Cao tối đa 300 px.
  - Không có ô khi vùng đã chiếm ≥ 50 % ảnh theo cả hai chiều, hoặc khi chỉ phóng được dưới 1,5 lần.
  - Quanh vùng chừa thêm chỗ, để chữ sát mép hộp OCR vẫn thấy được. Ảnh letterbox được tính đúng.
- Ô phóng to thay khung cắt 360 px cũ, vốn chỉ có ở thẻ phóng to (`cropRect`, `drawCrop` đã bỏ).
  - Ô vẽ lại khi: ảnh tải xong, đổi ảnh ở dải ảnh, vùng đổi (vá danh sách), phóng to hay thu nhỏ thẻ, đổi cỡ cửa sổ, bấm "Ẩn khung".
  - Thẻ chỉ mượn vùng của thẻ khác (S9: thẻ an toàn, "Kiểm tra đoạn kết") không có ô, vì chú thích ghi "vùng phát hiện".

### 3. Không biết máy đọc được gì
- **"Máy đọc: “…”"** hiện trên thẻ chữ và thẻ logo, ngay dưới dòng thời gian. Hàm: `D.readings`.
  - Các cách đọc gần giống nhau (theo cặp ký tự; dòng ngắn thì xét chuỗi con) gộp thành một nhóm, kèm "N cách đọc gần giống".
  - Thứ tự: nhóm có nhiều cách đọc nhất, rồi lần lượt nhóm khác những dòng đã hiện nhất. Dòng ngắn được tính nhẹ hơn, nên "Được." hay "Ừ." không chen lên trước.
  - Tối đa 3 dòng, mỗi dòng 70 ký tự; phần còn lại ghi "+N dòng khác trong Chi tiết kỹ thuật".
  - Thẻ logo bỏ nhãn của máy quét (tiếng Anh, chữ thường) và tên nền tảng; bỏ phần "OCR nội dung phim: " ở đầu. Thẻ an toàn không có dòng này.
  - Mọi chuỗi đều qua `esc()`.
- **Tên vùng:** dưới ảnh xem trước của máy quét chữ có câu như "Nhãn “REVIEW 58 | subtitle” trên ảnh do máy quét chữ in: “subtitle” chỉ là tên vùng (vùng phụ đề), không có nghĩa đây là phụ đề." Hàm: `D.zoneNote`; tên vùng tiếng Việt ở `ZONE_NAMES`.
  - Câu đổi theo ảnh đang hiện (chạm ảnh khác ở dải ảnh).
  - Hàng đợi dựng từ nay biết đúng vùng của từng ảnh (`text_preview_zones`).
  - Hàng đợi cũ (như job 134) chưa có trường này: câu chung giải thích phần sau dấu `|` là tên vùng.

### 4. Ảnh đầu của thẻ dòng chữ chạy
- `review_workflow.py` thêm `_text_preview_tracks` và `order_text_previews`. Chúng chạy cuối `build_review_queue`, sau mọi bước quyết định. Thẻ chữ có nhiều ảnh, mà tất cả đều là ảnh xem trước của máy quét chữ, được xếp ảnh theo dòng OCR dài nhất của track (bằng nhau thì giữ thứ tự cũ).
  - Mỗi ảnh xem trước được ghi kèm tên vùng của nó (`text_preview_zones`, chỉ để hiển thị).
  - Không đổi đề xuất, quyết định, vùng làm mờ, id hay số thẻ. Không sửa thẻ đầu vào.
  - Thẻ cũ giữ thứ tự cũ cho tới khi hàng đợi được dựng lại.
- `review_workflow.py` không thuộc khóa cache quét. Khóa cache của cả 10 bước quét giống hệt main (`stage_cache._tree_fingerprint`).

### Sửa theo review
Review gồm 4 góc nhìn, mỗi phát hiện được kiểm lại theo hướng bác bỏ. Các lỗi xác nhận đều đã sửa:
- Test mới có chuỗi OCR đọc sai từ watermark thật của job 134. Đã thay bằng `TRANGEXAMPLECOM`. Chuỗi cũ vẫn còn trong be38171: xem "Chờ người dùng".
- "Ẩn khung" không ẩn "Đã chọn cắt": hai quy tắc CSS cùng độ ưu tiên, quy tắc sau thắng.
- Ô phóng to hiện cả trên thẻ chỉ mượn vùng, với chú thích "vùng phát hiện".
- Ô phóng to vừa khít thẻ thì rộng hơn khung chứa 2 px.
- Bấm "Ẩn khung" bằng phím trên thẻ chưa chọn thì mất focus.
- Nút "Ẩn khung" hiện cả ở thẻ không có gì vẽ đè; tooltip hứa "ảnh gốc".

Ba điểm bị bác:
- Thứ tự ảnh mới ảnh hưởng tới AI duyệt ảnh: có đường đi, nhưng không sinh kết quả sai.
- Bộ nhớ canvas: trường hợp nặng nhất có thật trên máy là khoảng 40 thẻ có vùng, tức khoảng 40–80 MB.
- "Máy đọc" trên thẻ logo hình có tên thương hiệu do mô hình đoán: đúng thiết kế, thẻ cũ cũng ghi như vậy.

### Test và cổng đã chạy
- **Python** (`PYTHONPATH=<worktree>\src`, có node và ffmpeg): 285 test đạt, không bỏ qua test nào. Gồm `test_review_text_previews` (mới, 5 test), `test_review_workflow`, `test_review_evidence`, `test_review_diff`, `test_review_regression`, `test_fixed_text_overlays`, `test_platform_logos`, `test_platform_logo_e2e`, mọi `test_dashboard_v2_*` (có `ReviewR4PhoneListener`, chạy thật qua listener điện thoại). Chưa chạy full suite: điều phối chạy lúc gộp.
- **Node:**
  - `node --check` 31 file đạt.
  - Script kiểm: `verify` 50, `verify-adapter` 42, `verify-review` 37 (5 mục "Session R" mới), `verify-download` 23, `verify-download-accounts` 55.
  - Browser check: `browser-check-review` 27, `-write` 19, `-bulk` 13, `-layout` 13.
- **Cổng giao diện** trên Control Center thử, root tạm trong `temp\`, cổng 8797 (code main) và 8798 (nhánh); xem `docs/evidence/review-ui/README.md`. Ở 390 × 844 sáng, 390 × 844 tối và 1440 × 900:
  - Chữ trong khung thấy rõ (nét ngoài vùng, không tô).
  - Ô phóng to ×3 đọc được; dòng chữ chạy cao 42 px, vuốt ngang.
  - "Máy đọc" đúng chữ; câu tên vùng đúng.
  - Ảnh đầu của dòng chữ chạy là lúc chữ hiện đủ.
  - Trang không cuộn ngang; chỉ có GET.
- **Quyết định, đề xuất, vùng không đổi:**
  - Fixture dựng bằng code main và code nhánh cho cùng 3 thẻ (cùng id, đề xuất, vùng); chỉ khác thứ tự ảnh và `text_preview_zones`.
  - Trên bản chép của job 134 (chỉ trong `temp\`, không đưa vào repo): 42 thẻ và 187 thẻ phụ giống hệt, trừ hai trường hiển thị. 3 thẻ đổi thứ tự ảnh, 54 thẻ có tên vùng.
  - SHA-256 của `review-queue.json` giữ nguyên suốt lúc trang mở.
- **Trang cũ `/review/<id>` (D2):** không đổi. `verify-review` so từng byte script của trang cũ.

## Đang dở
- (không có)

## Bước tiếp theo
1. Người dùng trả lời câu hỏi về be38171 (mục dưới).
2. Người dùng thử trên điện thoại với job thật, sau khi gộp và khởi động lại Control Center.
   - Job 134 có hàng đợi cũ. Thấy ngay: khung nét, nút "Ẩn khung", ô phóng to, "Máy đọc" và câu giải thích chung về nhãn vùng.
   - Thứ tự ảnh mới và câu ghi đúng tên vùng chỉ có ở hàng đợi dựng sau khi gộp.
3. Điều phối chạy full suite rồi gộp.
4. Đề xuất cho sau (ngoài phạm vi R, cần một vòng sửa phần phát hiện):
   - Máy quét chữ lưu chữ theo từng khung hình, để chọn đúng khung có dòng dài nhất làm ảnh xem trước.
   - Ảnh xem trước không in nhãn đè lên chữ sát mép trên.

## Cách kiểm
- Python: `$env:PYTHONPATH="<worktree>\src"; .\.venv\Scripts\python.exe -m unittest tests.test_review_text_previews tests.test_review_workflow tests.test_dashboard_v2_review -v`. Lưu ý: venv cài `biliflow` dạng editable trỏ vào thư mục chính, nên thiếu `PYTHONPATH` thì test chạy code của thư mục chính.
- Node (trong worktree; `BILIFLOW_PYTHON` trỏ venv, `NODE_PATH` có `playwright`): `node dashboard_v2/verify-review.cjs`, `node dashboard_v2/browser-check-review.cjs`, `-write`, `-bulk`, `-layout`. `browser-check-review-phone.cjs` chạy qua `tests.test_dashboard_v2_review.ReviewR4PhoneListener`.
- Ảnh và cổng giao diện: các lệnh trong `docs/evidence/review-ui/README.md`. `make_fixture.py` từ chối root ngoài `<install>\temp` và cổng 8765.
- Cổng của bước này: các mục ở "Test và cổng đã chạy".

## Chờ người dùng
- **be38171 có một chuỗi OCR đọc sai từ watermark thật của job 134** (trong test mới). Bản làm việc đã thay bằng chuỗi `.example`. AGENTS.md cấm tự viết lại lịch sử, nên chuỗi cũ vẫn nằm trong be38171. Có hai cách để nó không lên repo công khai:
  - người dùng cho phép gộp hai commit của nhánh thành một (squash) trước khi gộp;
  - điều phối gộp nhánh bằng squash.
  Nếu gộp kiểu thường, chuỗi đó sẽ có trong lịch sử của main.
- Thử trên điện thoại (bước tiếp theo 2).

## Lưu ý cho người tiếp quản
- **Không sửa:** phần phát hiện và ngưỡng; file thuộc khóa cache quét (`textscan.py`…); bộ lập lịch; phần xuất; `AGENTS.md`, `CHANGELOG.md`, `docs/PROJECT_STATUS.md`, `docs/SESSION_HANDOFF.md`; `annotations\`.
- **Lỗi có sẵn trên main, ngoài trang duyệt:** `browser-check.cjs` vẫn hỏng trên main a63119b vì hai chỗ.
  - Check "Downloads stay a labelled simulation" chờ `#download-simulation`, phần tử này không còn từ khi trang "Tải video" chạy thật.
  - Check R4.3 thỉnh thoảng hỏng vì tranh thời gian: Esc được bấm trong 400 ms sau Esc của hộp `#modal`, mà R3-N1 (`UPPER_MS`) bỏ qua Esc như vậy. Đo được 2/6 lượt hỏng trên main và 1/6 trên nhánh.
  - Đã tạo một việc riêng để sửa; session R không sửa file đó.
- **Đã làm chắc hai check của trang duyệt** vốn hỏng ngẫu nhiên vì yêu cầu còn đang chạy:
  - "Demo page: no API request" giờ chỉ đếm yêu cầu của trang demo;
  - check điện thoại chờ lượt `/api/status` mà nút × gọi xong rồi mới đóng trình duyệt.
  - `review-fake-server.cjs` trả `/api/tailscale` như `browser-check.cjs`; trước đó mọi browser check của trang duyệt hỏng ở bước "no console error" vì lỗi 404.
- **Kích cỡ file:** `review.js` 799 dòng, sát mức 800. Muốn thêm gì nữa thì tách một phần (ví dụ phần ô phóng to) ra file riêng.
- **Thư mục tạm của session R** (chỉ trong `temp\`, xóa được sau khi gộp):
  - `wt-r-main` (worktree tách rời ở a63119b, dùng để so trước/sau);
  - `r-fixture-before`, `r-fixture-after` (root của hai Control Center thử ở cổng 8797/8798; dừng tiến trình trước khi xóa);
  - `r-shots`;
  - `r-sample\job134` (bản chép chỉ đọc của hàng đợi và ảnh xem trước job 134; chứa tên thật, không bao giờ đưa vào repo);
  - `r-pw` (shim playwright-core cho Edge của máy);
  - các file `r-*.log` và `r-strip-check.cjs`.
