# Plan trang duyệt giao diện V2

Soạn ngày 2026-10-04 trên nhánh `feat/dashboard-v2`. Người dùng đã chọn hướng và trả lời các câu hỏi (mục 1).
**Đổi ngày 2026-10-04 (người dùng):** bắt đầu ngay, chưa merge. Làm tiếp trên nhánh `feat/dashboard-v2`. E6, E7 của Dashboard V2 sẽ test chung khi người dùng thử trang duyệt mới. Merge vào `main` một lần, sau khi người dùng test hết.

Tài liệu kèm:
- `docs/DASHBOARD_V2_REVIEW_INVENTORY.md`: bảng kê kỹ thuật (tiếng Anh) về trang duyệt cũ, API, bất biến, test và chỗ
  thiếu của V2. Các ký hiệu A0–A7, B0–B7 trong plan này trỏ tới đó.
- `docs/patches/M4-review-back-to-v2.patch`: code M4 cloud đã viết ở đợt 5 (fbe0f89). Đã áp ở R0.1 (b853963) và xóa file patch.
- `docs/DASHBOARD_V2_CLOUD_PLAN.md` mục 10 (quy tắc commit và bàn giao) và 15.4 (yêu cầu M4).

## 0. Tóm tắt (đọc 1 phút)

- **Làm gì:** biến hộp **"Duyệt cảnh" của bản mẫu V2** thành trang duyệt thật.
  - Giữ kiểu hộp, thẻ cảnh và các nút: Giữ / Làm mờ / Cắt / Cần xem thêm / Xóa quyết định, "Giữ tất cả", "Dùng đề xuất".
  - Thẻ hiện ảnh thật và vùng logo thật; nút gửi quyết định thật.
  - Thêm những phần trang cũ có mà bản mẫu chưa có: video, khung hình, bộ lọc, nhớ logo, hoàn tác, phím tắt, xuất video.
- **Cách hiển thị:**
  - PC: hộp gần toàn màn hình, thẻ xếp 2 cột.
  - Laptop hẹp: 1 cột như bản mẫu. Điện thoại: hộp toàn màn hình, 1 cột.
  - Xem kỹ ngay trong thẻ: bấm ▶ để phát đúng đoạn, dải khung hình dưới ảnh, nút phóng to thẻ.
- **Quay lại:** đóng hộp (×, "Đóng", Esc hoặc nút Back của trình duyệt) là về đúng màn V2 đang xem.
- **Trang cũ:** giữ nguyên (trùng byte, D2), có link "Mở trang duyệt cũ" để dự phòng. Patch M4 làm trang cũ mở từ V2
  quay lại đúng V2.
- **Cách làm:** 5 đợt cloud (R0–R4), mỗi đợt máy thật kiểm khoảng 15 phút. Người dùng thử trên dữ liệu thật sau R2 và
  sau R4.
  - Nút "Duyệt" của V2 chỉ chuyển sang hộp mới ở R4.
  - Trong lúc làm, hộp mới mở bằng nút "Duyệt (bản mới, thử)".
- **Không đổi:** API, cách xuất video, quy tắc an toàn.

## 1. Bối cảnh và quyết định

Hiện trạng:
- Bản mẫu V2 có hộp "Duyệt cảnh" (`app.js:361-365`).
  - Gồm tối đa 8 thẻ với ảnh minh họa, dòng "N / M cảnh cần quyết định cuối", "Giữ tất cả", "Dùng đề xuất".
  - Mỗi thẻ có Giữ / Làm mờ / Cắt / Cần xem thêm / Xóa quyết định; có thông báo "Chỉ xem" khi bị khóa.
  - Bấm nút chỉ đổi dữ liệu mẫu. Chữ trên hộp ghi rằng khi tích hợp, nút Duyệt cảnh mở trang review hiện có. Vì vậy bản
    thật đang mở trang cũ `/review/<id>` (`app.js:392`).
- Nút "← Quay lại Dashboard" của trang cũ về `/`. Trên PC đó là dashboard cũ; trên listener điện thoại `/` chuyển sang V2.

Quyết định của người dùng (2026-10-04):
- Trang duyệt cũ vẫn giữ. Trang duyệt V2 làm theo plan này. Ban đầu định làm sau merge; ngày 2026-10-04 người dùng đổi thành làm ngay trên `feat/dashboard-v2` rồi merge một lần.
- M4 (nút quay lại về V2) chuyển vào phase này. Cloud đã viết code M4 ở đợt 5 và cất thành patch. Trên cloud patch đã
  qua D2 trùng byte, `view` lạ → `#overview`, listener điện thoại và browser-check 18/18.
- **Q1, vị trí:** người dùng nhắc rằng V2 đã có UI duyệt. Trang duyệt mới chính là hộp "Duyệt cảnh" của bản mẫu, làm
  thật; không vẽ bố cục mới.
- **Q2:** nút "Duyệt" chuyển sang hộp mới ở đợt cuối (R4), khi đủ chức năng.
- **Q3:** giữ link dự phòng "Mở trang duyệt cũ". Gỡ link thì hỏi lại người dùng sau khi họ dùng hộp mới ổn định.
- **Q4:** sửa hết S1–S7 (mục 5). S8 lấy theo bản mẫu V2, không có trong câu hỏi; người dùng có thể bỏ.
- **Q5:** phím tắt như trang cũ, không thêm phím mới ở v1 (theo khuyến nghị; người dùng không yêu cầu khác).
- **Q6 (thay bằng 2 câu):**
  - hộp rộng, thẻ xếp 2 cột trên PC (laptop hẹp và điện thoại 1 cột);
  - xem video và khung hình ngay trong thẻ, có nút phóng to thẻ.
- **Q7:** tạm dừng cập nhật dashboard khi hộp duyệt đang mở (theo khuyến nghị; người dùng không yêu cầu khác).

## 2. Mục tiêu và phạm vi

### 2.1. Mục tiêu

Người dùng duyệt toàn bộ một video trong hộp "Duyệt cảnh" của V2: xem cảnh (ảnh, video, khung hình, vùng), chọn quyết
định, hoàn tác, làm hàng loạt, rồi xuất video. Mọi quy tắc an toàn giữ như trang cũ, và dùng tốt trên PC, laptop,
điện thoại.

### 2.2. Trong phạm vi

- Hộp "Duyệt cảnh" thật ở bản chạy với Control Center (`live.html`).
- Bản demo (`index.html`) dùng cùng hộp đó với dữ liệu giả, thay cho 8 thẻ minh họa hiện nay.
- Mọi chức năng ở danh sách tương đương (mục 4) và các sửa nhỏ S1–S8 (mục 5).
- Patch M4 và link "Mở trang duyệt cũ".
- Bố cục điện thoại và laptop.

### 2.3. Ngoài phạm vi (v1)

- **Tính năng mới:** tua từng khung, đổi tốc độ, vẽ hoặc sửa vùng làm mờ, sửa khoảng thời gian, cách sắp xếp khác, chạy
  Visual AI Audit, "Bỏ qua (không xuất)" / "Mở lại để xuất" (vẫn ở Dashboard), quản lý bộ nhớ logo.
- **Đổi server:** không thêm route, không đổi API, không đổi payload. Server chỉ được sửa ở 2 chỗ: whitelist file V2
  (`DASHBOARD_V2_FILES`) và patch M4.
- **Trang cũ:** không sửa `/review/<id>` không tham số (D2), không sửa `/`, `_interactive_html` hay `export_dialog.py`.
- `serve_review_ui` (trang duyệt chạy riêng, A0).
- Gỡ trang cũ: quyết định riêng sau này (Q3).

## 3. Ràng buộc bất biến (cloud phải giữ ở mọi đợt)

1. **AGENTS.md:**
   - chỉ con người duyệt KEEP/BLUR/CUT/NEEDS_MORE_CONTEXT; phát hiện chỉ tạo ứng viên;
   - không tự xuất hay đăng; không đụng video gốc;
   - agent không bao giờ gọi các route dọn, lưu trữ hay Thùng rác;
   - test chỉ dùng root tạm và dữ liệu giả.
2. **Payload POST giống trang cũ từng trường:**
   - `decision` `{id, decision, full_frame, note, remember_studio_logo?|remember_platform_logo?}`;
   - `clear` `{id}`;
   - `bulk-keep` / `bulk-accept` `{filter}`;
   - `finalize` gửi qua hộp "Xuất video đã duyệt" của V2: `{size_mode, max_output_gb?}`, như V2 đang gửi (B4).

   Không thêm trường nào. Cờ nhớ logo chỉ gửi khi là `true` (A3, B2).
3. **Không thêm route nào:**
   - `PHONE_ALLOWED_POSTS` và danh sách endpoint trong `contracts.js` giữ nguyên;
   - các test `T/test_dashboard_v2_contract.py` và `T/test_dashboard_v2_phone_hardening.py:221` vẫn đạt mà không sửa.
4. **Không đổi trang cũ:**
   - `/` và `/review/<id>` không tham số trùng byte với fixture D2;
   - `/review/<id>?from=v2&view=…` chỉ đổi đích của nút quay lại (patch M4).
5. **Không đổi file trong fingerprint cache:** lệnh A4 cho 10 stage `none`. Không thêm thư viện hay gói npm chạy trong
   trang; V2 vẫn là JS thuần.
6. **CSP của V2 giữ nguyên:**
   - không có script hoặc handler inline (dùng `data-action`);
   - không dùng `blob:`;
   - ảnh và video là URL cùng origin (`<img src>`, `<video src>`, B7).

   Nếu thấy cần đổi CSP thì hỏi người dùng trước.
7. **Chỉ `adapter.js` được gọi `fetch(`** (`verify.cjs:164-169`). File mới phải có trong:
   - `DASHBOARD_V2_FILES`;
   - `live.html` và `index.html` (phần body hai file trùng nhau);
   - `serve.py`.

   Ngoài ra `node --check` phải đạt, và fixture demo giữ đủ 15 job.
8. **Mọi chuỗi lấy từ queue đi qua `esc()`** (tên file, chữ OCR, nhãn, ghi chú). Tham số trên địa chỉ: số job chỉ gồm chữ
   số, màn V2 nằm trong danh sách cho phép; không phản chiếu chuỗi nào khác.
9. **Điện thoại:**
   - tối đa 2 ảnh tải cùng lúc;
   - mọi lệnh ghi đi qua danh sách POST cho phép hiện có;
   - thao tác chỉ-PC vẫn chỉ-PC.
10. **Ngưỡng detector và cách xuất video không đổi.** Mỗi file nguồn dưới 800 dòng; file nào quá thì tách ra.
11. **Repo công khai:** dữ liệu mẫu, fixture và tài liệu không chứa tên video thật, đường dẫn hay thông tin máy của người dùng.
12. **Giữ ngôn ngữ thiết kế của hộp bản mẫu:**
    - thẻ cảnh, nút và cách đánh dấu nút đã chọn (`.selected`);
    - các chữ "N / M cảnh cần quyết định cuối", "Giữ tất cả", "Dùng đề xuất", "Xóa quyết định";
    - thông báo "Chỉ xem".

    Phần thêm dùng thành phần V2 sẵn có: chip lọc (`filter-tab`), notice, toast, dialog, nút `small secondary`.
13. **Quy tắc commit và bàn giao** như mục 10 của cloud plan:
    - commit kiểu `type: mô tả`, không có dòng attribution;
    - không merge `main`; push lên nhánh của phase rồi dừng sau mỗi đợt;
    - ghi cột Cloud, Bằng chứng và nhật ký ở file này.

## 4. Danh sách tương đương với trang cũ (dùng để nghiệm thu)

| ID | Nhóm | Hộp "Duyệt cảnh" mới phải có (chi tiết ở inventory) |
| --- | --- | --- |
| P1 | Đầu hộp | "Duyệt cảnh · #id · tên video", "N / M cảnh cần quyết định cuối" + thanh tiến độ, nút đóng (A1) |
| P2 | Xuất video | nút "Xuất video" trong hộp, mở hộp "Xuất video đã duyệt" của V2 (3 chế độ dung lượng như `export_dialog.py`) kèm dòng tài nguyên (dung lượng nguồn/báo cáo, ổ trống, ước tính) như trang cũ (A1, A2 "Export") |
| P3 | Bộ lọc | chip Chưa duyệt (N), 18+, Máu me, Bạo lực, Quảng cáo, Tất cả và "Lọc khác" (Ưu tiên cao, Visual AI, Logo, Chữ, Ứng viên phụ (N)); quy tắc `visible()`; bắt đầu ở Chưa duyệt, hoặc Tất cả nếu không còn mục chưa duyệt (A4) |
| P4 | Cảnh báo phạm vi quét | notice "Không quét trong lượt này: …" khi có nhóm detector bị bỏ qua (A5) |
| P5 | Thẻ cảnh | sắp theo bắt đầu, kết thúc, id; tên, thời gian, nhóm, dòng vùng, trạng thái; thẻ vừa duyệt vẫn hiện trong "Chưa duyệt" (sticky) (A1, A4) |
| P6 | Xem kỹ trong thẻ | ảnh chính (khung "Rõ nhất" hoặc ảnh xem trước); ▶ phát đúng đoạn (cảnh nhiều khoảnh khắc: chip khoảnh khắc, phát lần lượt); timeline bấm để tua; dải ≤ 8 khung; khung đỏ vùng, ảnh cắt 360 px, khung vàng của AI + chú thích; "Chi tiết kỹ thuật"; nút phóng to thẻ (A1, A4 Media) |
| P7 | Quyết định | Giữ / Làm mờ / Cắt / Cần xem thêm / Xóa quyết định; nút đã chọn được đánh dấu, bấm lại thì gửi lại; mục không có vùng thì nút là "Làm mờ cả cảnh" và có xác nhận; xác nhận khi Visual AI ≥ 0,9 khác ý; báo lỗi BLUR chưa có vùng; nút vùng (tiêu đề phim giữ / logo làm mờ); nút nhớ logo hãng phim / nền tảng đúng điều kiện (A2, A4) |
| P8 | Chọn thẻ | thẻ đang chọn có viền nổi; sau khi quyết định thì tự chọn và cuộn tới thẻ chưa duyệt kế tiếp (bật/tắt "Tự chuyển cảnh", lưu `biliflow.review.autoNext`) (A2, A4) |
| P9 | Phím tắt | 1–4 cho thẻ đang chọn; ←/→ chọn thẻ trước/sau (dừng ở đầu/cuối); Space phát/dừng video của thẻ; Z hoàn tác; Esc thu nhỏ thẻ đang phóng to rồi mới đóng hộp. Bỏ qua khi đang gõ, khi giữ Ctrl/Alt/Meta, khi hộp xác nhận đang mở (A2) |
| P10 | Hoàn tác | nút "↶ Hoàn tác"; tối đa 100 bước; ứng viên phụ không hoàn tác được (có thông báo); hàng loạt không hoàn tác; xóa khi đổi lượt quét (A2) |
| P11 | Hàng loạt | "Giữ tất cả" / "Dùng đề xuất" áp dụng cho các cảnh đang lọc; Quảng cáo gửi 2 lệnh (visual_logo rồi text); Visual AI và Ứng viên phụ báo "không hỗ trợ"; xác nhận kèm số mục; khóa hộp khi chạy (A2) |
| P12 | Luồng xuất | chờ các lệnh ghi xong → chặn nếu chưa đủ quyết định → hộp xuất V2 → gửi `finalize` (không tự thử lại) → toast; xếp lệnh xong thì đóng hộp duyệt, tiến độ xuất hiện ở dòng video như hiện nay (A2) |
| P13 | Khóa | đang chờ/đang xuất, nguồn đã dọn, nguồn đang lưu trữ, đã Bỏ qua (S8): thông báo "Chỉ xem" với đúng lý do, nút quyết định tắt; nút Xuất tắt khi chưa READY_FOR_EDIT_PLAN (A4 bảng Locks) |
| P14 | Đồng bộ | poll queue 3 s khi tab hiện; đổi lượt quét → dựng lại; chỉ đổi phiên bản → vá trạng thái và nút, không dựng lại thẻ đang phát video; lệnh ghi tuần tự, lạc quan, thử lại 300/900 ms, lỗi cuối → báo rồi đồng bộ lại; 403 → lấy phiên mới (A3, A4) |
| P15 | Hiệu năng | thẻ nạp dần; ảnh chỉ tải cho thẻ gần màn hình, tối đa 2 cùng lúc; tải trước bằng chứng của thẻ chưa duyệt kế tiếp; chỉ một `<video>`; nhả video khi đóng hộp hoặc ẩn tab (A4) |
| P16 | Chữ mang quy tắc | giữ nguyên nghĩa các câu ở A5; giới hạn dung lượng và câu xác nhận xuất giống `export_dialog.py` |
| P17 | Điện thoại | hộp toàn màn hình, 1 cột, nút xuống dòng như bản mẫu, chip cuộn ngang, chữ ≥ 12 px, vùng chạm ≥ 44 px, ẩn gợi ý phím trên màn cảm ứng (A6) |

## 5. Sửa nhỏ so với trang cũ (người dùng đồng ý S1–S7, Q4)

Các điểm dưới đây là chỗ trang cũ hiển thị chưa đúng (A7). Sửa ở hộp mới không đổi quy tắc an toàn và không đổi server:

- **S1.** Số mục trong hộp xác nhận hàng loạt tính giống server: theo `category`, chỉ mục chưa duyệt của danh sách chính;
  "Dùng đề xuất" bỏ qua mục BLUR chưa có vùng. Trang cũ có thể báo lệch ở bộ lọc Chữ và Logo.
- **S2.** Khi chỉ còn mục "Cần xem thêm", hộp báo "Còn N mục Cần xem thêm — chọn quyết định cuối trước khi xuất".
  Trang cũ báo "Đã duyệt đủ" trong khi nút Xuất vẫn khóa.
- **S3.** Trạng thái xuất được tải lại sau khi sửa quyết định (gộp 1,5 s), không chỉ lúc QUEUED/RENDERING.
- **S4.** Nhãn "Ổ E còn trống" đổi thành "Ổ đĩa còn trống".
- **S5.** Hộp xác nhận và thông báo theo kiểu V2, chữ giữ nguyên. Enter xác nhận, Esc hủy; trong lúc hộp mở, phím tắt tạm tắt.
- **S6.** Phân biệt "đang xuất" với "đang chờ quét" bằng hàm `inFlight` có sẵn (`contracts.js:30`). Hàm này đã khớp với
  server: `render_request` từ `/api/status`, RENDERING/VERIFYING, hoặc QUEUED với `queue_kind === 'export'`. Trang cũ
  khóa cả khi job chỉ đang chờ quét (QUEUED).
- **S7.** Nhận biết lượt quét mới bằng `source.sha256`. Trang cũ đọc nhầm `source.input_sha256` nên phần sha luôn rỗng.
- **S8 (theo bản mẫu V2, người dùng có thể bỏ).** Video đã "Bỏ qua (không xuất)" mở hộp duyệt ở chế độ chỉ xem, kèm câu
  "Bấm “Mở lại để xuất” ở Dashboard để sửa". Đây là cách `readonlyReview` của bản mẫu đang làm (`app.js:360`). Trang cũ
  cho sửa, và sửa có thể làm mất trạng thái bỏ qua (B2).

## 6. Thiết kế

### 6.1. Hộp "Duyệt cảnh" và địa chỉ

- **Hộp riêng:** dùng `<dialog id="review-dialog">`, tách khỏi hộp chung `#modal`. Nhờ vậy hộp xác nhận và hộp "Xuất video
  đã duyệt" mở chồng lên trên mà không thay nội dung hộp duyệt. Hộp nằm ngoài `#main`, nên poll của dashboard không dựng
  lại nó (B6 gap 12).
- **Địa chỉ:**
  - mở hộp thì đặt hash `#review/<id>/<view>`; regex
    `^review\/(\d{1,9})(?:\/(overview|downloads|videos|queue|logos|settings))?$`;
  - thiếu `view` thì dùng `overview`; hash sai thì về `#overview`;
  - mở thẳng địa chỉ này (tải lại trang, gửi link) thì mở màn `<view>` rồi mở hộp;
  - đóng hộp (×, "Đóng", Esc) thì về `#<view>`; nút Back của trình duyệt cũng đóng hộp.
- **Link "Mở trang duyệt cũ"** (chữ nhỏ ở đầu hộp) trỏ tới `/review/<id>?from=v2&view=<view>`; nhờ patch M4, trang cũ
  quay lại đúng V2.
- **Poll:** khi hộp mở, tạm dừng `/api/status` (Q7); đóng hộp thì làm mới ngay và bật lại. Hộp có poll queue riêng.
- **Bản demo:** hộp thay `reviewModal` / `reviewMarkup` / `sceneList` / `decideScene` hiện nay. Demo store cung cấp cùng
  các hàm của adapter (6.7) trên queue giả.

### 6.2. Bố cục

- **PC (≥ 1100 px):** hộp rộng `min(1280px, 96vw)`, cao gần toàn màn hình; thẻ xếp 2 cột (Q6).
- **Laptop hẹp (821–1099 px):** hộp gần toàn màn hình, thẻ 1 cột như bản mẫu.
- **Điện thoại (≤ 820 px):** hộp toàn màn hình, 1 cột; nút trên thẻ xuống dòng như bản mẫu.

```
PC:
+---------------------------------------------------------------+
| Duyệt cảnh · #101 · <tên video>   [Mở trang duyệt cũ]      [x] |
| 5 / 40 cảnh cần quyết định cuối  ======-----   [Xuất video]   |
| [Chưa duyệt 5] [18+] [Máu me] [Bạo lực] [Quảng cáo] [Tất cả]  |
| [Lọc khác v]   [Giữ tất cả] [Dùng đề xuất]  [↶ Hoàn tác] Đã lưu |
| +---------------------------+ +---------------------------+   |
| | ảnh cảnh  [▶] [⤢]         | | ảnh cảnh  [▶] [⤢]         |   |
| | [khung][khung][khung]...  | | [khung][khung][khung]...  |   |
| | Logo góc trái             | | Cảnh bạo lực              |   |
| | 00:00–00:12 · Quảng cáo   | | 01:20–01:31 · Bạo lực     |   |
| | Giữ Làm mờ Cắt Cần xem    | | Giữ Làm mờ Cắt Cần xem    |   |
| | thêm  Xóa quyết định      | | thêm  Xóa quyết định      |   |
| +---------------------------+ +---------------------------+   |
|                                     [Tự chuyển cảnh] [Đóng]   |
+---------------------------------------------------------------+
```

### 6.3. Thẻ cảnh

- **Ảnh chính:**
  - khung "Rõ nhất" của bằng chứng, hoặc ảnh xem trước của báo cáo (`/media/...`);
  - vùng gợi ý vẽ khung đỏ;
  - khi chọn Làm mờ (có vùng), vùng đó hiện mờ bằng lớp `backdrop-filter`, giống `.watermark.blurred` của bản mẫu;
  - Làm mờ cả cảnh thì cả ảnh mờ; Cắt thì ảnh tối đi kèm chữ "Đã chọn cắt";
  - phần mờ và tối chỉ để minh họa, không phải bản xuất.
- **▶ Phát:** một `<video>` dùng chung được chuyển vào thẻ đang phát; phát đúng đoạn và dừng ở cuối đoạn. Cảnh nhiều
  khoảnh khắc có chip khoảnh khắc và "▶ Phát lần lượt N khoảnh khắc".
- **Dưới ảnh:** timeline mảnh (bấm để tua) và dải tối đa 8 khung; bấm khung thì xem khung đó.
- **⤢ Phóng to:** thẻ chiếm cả bề ngang hộp (ảnh và video lớn, thêm ảnh cắt 360 px và khung vàng của AI); bấm lại hoặc
  Esc thì thu nhỏ.
- **Nội dung:**
  - tên cảnh, "thời gian · nhóm · vùng", trạng thái;
  - nhận xét Visual AI nếu có;
  - với mục ứng viên phụ: "Ứng viên kiểm tra thêm — chưa thuộc quyết định chính";
  - "Chi tiết kỹ thuật" thu gọn.
- **Nút:**
  - Giữ / Làm mờ (hoặc "Làm mờ cả cảnh") / Cắt / Cần xem thêm / Xóa quyết định;
  - trên thẻ đủ điều kiện có thêm: nút vùng, "Đây là logo hãng phim — giữ & nhớ", "Đây là logo nền tảng — làm mờ & nhớ".

### 6.4. Đầu và chân hộp

- **Đầu hộp:**
  - tên và link trang cũ;
  - tiến độ "N / M cảnh cần quyết định cuối" + thanh, nút "Xuất video";
  - chip lọc và "Lọc khác", cảnh báo phạm vi quét;
  - "Giữ tất cả" / "Dùng đề xuất" (áp dụng cho các cảnh đang lọc), "↶ Hoàn tác", "Đang lưu… / Đã lưu";
  - thông báo "Chỉ xem" khi bị khóa (P13).
- **Chân hộp:** "Tự chuyển cảnh" và "Đóng", như bản mẫu.
- Trên điện thoại, đầu hộp gọn lại: chip cuộn ngang; các nút hàng loạt và hoàn tác vào một hàng có thể cuộn.

### 6.5. Nạp dần và hiệu năng

- **Thẻ:** dựng 24 thẻ đầu, thêm 24 thẻ khi cuộn gần cuối (`IntersectionObserver`). Đổi bộ lọc thì dựng lại từ đầu.
- **Ảnh:**
  - chỉ thẻ gần màn hình mới tải ảnh;
  - bộ nạp tối đa 2 ảnh cùng lúc, ưu tiên thẻ đang chọn;
  - tải trước bằng chứng của thẻ chưa duyệt kế tiếp sau 600 ms.
- **Giữ ảnh:** giữ phần tử `<img>` đã tải của các thẻ đang dựng; thẻ bị bỏ khi đổi bộ lọc thì nhả ảnh.
- **Queue lớn:** đo ở R0 với 500 mục giả. Nếu chậm thì báo người dùng trước khi làm cách khác.

### 6.6. Ảnh và video dưới CSP V2 (không dùng `blob:`)

- **Khung hình:** dùng `<img src="frameUrl(...)">` thay vì fetch → blob như trang cũ. Ảnh xem trước dùng
  `<img src="/media/...">`.
- **Ảnh lỗi:** có thể do khóa media hết hạn khi Control Center khởi động lại. Xử lý: lấy `session()` 1 lần, dựng lại URL,
  thử tối đa 2 lần, rồi hiện ô "Không tải được ảnh".
- **Ảnh cắt 360 px:** vẽ lên canvas từ ảnh cùng origin; không cần đổi CSP.
- **Video:**
  - chỉ một `<video>`, `preload="none"`, `src` đặt lúc phát lần đầu;
  - lỗi → `probeVideo` để lấy lý do (404/409/410/415, lỗi giải mã);
  - 403 → khóa mới, thử tối đa 2 lần;
  - tạm dừng khi ẩn tab; nhả (`pause`, bỏ `src`, `load()`) khi đóng hộp hoặc `pagehide`.
- **Điện thoại:** server trả HTTP/1.0 (mỗi yêu cầu một kết nối) và giới hạn kết nối chưa có cookie mỗi thiết bị (A6), nên
  giới hạn 2 ảnh là bắt buộc. Stream video trên điện thoại có timeout ghi 60 s; trình duyệt tự gửi lại Range.

### 6.7. Adapter

- `store.review(jobId)` trả về một đối tượng gồm 3 nhóm hàm.
- **Hàm GET:** `queue()`, `session()`, `resources()`, `exportState()`, `evidence(itemId)`. Riêng `probeVideo(key)` gửi
  `Range: bytes=0-0` và chỉ lấy mã trạng thái; nó cần một chế độ `raw` vì transport hiện parse JSON (B0).
- **Hàm dựng URL** (chỉ trả chuỗi, không fetch): `frameUrl(item, t, key)`, `videoUrl(key)`, `mediaUrl(path)`. Mọi tham
  số qua `encodeURIComponent`; số job kiểm là chữ số. `contracts.js` `request()` nhận thêm query string.
- **Lệnh ghi** (`decide`, `clear`, `bulkKeep`, `bulkAccept`):
  - một chuỗi tuần tự cho mỗi job, giống `writeChain` của trang cũ;
  - **không dùng `once()`**, vì nó gộp 2 quyết định nhanh trên 2 thẻ khác nhau thành 1 POST (B0);
  - thử lại sau 300 ms rồi 900 ms khi lỗi mạng hoặc ≥ 500;
  - 403 → `session()` rồi gửi lại 1 lần;
  - trả về queue của server; người gọi chỉ áp dụng khi đó là lệnh cuối còn chờ;
  - không gọi `/api/status` sau mỗi quyết định.
- **Xuất:** đi qua `exportModal` và `mutate('finalize')` hiện có (`app.js:312-319`), không tự thử lại.
- **Demo store:** có cùng các hàm, chạy trong bộ nhớ trên queue giả đúng dạng thật. Queue giả có khoảng 30 mục mỗi job đủ
  loại: cảnh nhiều khoảnh khắc, logo có và không có vùng, chữ, ứng viên phụ, NEEDS_MORE_CONTEXT. Ảnh dùng poster SVG sẵn
  có; không có video.

### 6.8. Đồng bộ

- **Queue:**
  - poll 3 s chỉ khi tab hiện;
  - identity thay đổi (lượt quét mới, S7) → xóa cache, sticky, hoàn tác và dựng lại;
  - chỉ version thay đổi → vá trạng thái, nút và đầu hộp của các thẻ đang dựng, không dựng lại thẻ đang phát video.
- **Trạng thái xuất:** poll khi QUEUED/RENDERING và sau khi sửa quyết định (S3).
- **Tài nguyên:** tải lại 1,5 s sau lệnh ghi.
- **Lượt quét lại:** queue trả 404 (`rerun` làm mất queue đang dùng). Hộp báo "Video đang được quét lại" và chỉ còn nút đóng.

### 6.9. Lỗi và mất kết nối

- V2 đã có trạng thái offline. Khi mất mạng: nút quyết định tắt; lệnh ghi đang chờ thì thử lại, rồi báo như P14.
- Lỗi 400 của server (chữ tiếng Việt) hiện nguyên văn trong toast và không gửi lại.
- Lệnh cuối thất bại → tải lại queue, chọn lại thẻ vừa lỗi (chuyển sang "Tất cả" nếu cần), như `resync()` của trang cũ.

### 6.10. File

| File | Mới/Sửa | Nội dung |
| --- | --- | --- |
| `dashboard_v2/review-core.js` | Mới | Logic thuần, không DOM, không fetch: `visible`, sắp xếp, chọn thẻ, thẻ kế tiếp, ánh xạ và đếm hàng loạt, kiểm quyết định, xây payload, quy tắc khóa (S6, S8), identity/version (S7), chữ quy tắc. Chạy được trong node |
| `dashboard_v2/review-cards.js` | Mới | Dựng và vá thẻ, đầu hộp, nạp dần |
| `dashboard_v2/review-media.js` | Mới | Video dùng chung, timeline, dải khung, vùng và ảnh cắt, phóng to, bộ nạp ảnh tối đa 2 |
| `dashboard_v2/review.js` | Mới | Điều khiển hộp: mở/đóng theo hash, sự kiện (`data-action`), phím tắt, hàng ghi, poll, khóa, nối sang hộp xuất |
| `dashboard_v2/review.css` | Mới | Bố cục hộp và thẻ, dùng token màu của `theme.css`, giữ kiểu thẻ của bản mẫu |
| `dashboard_v2/verify-review.cjs` | Mới | Test node (mục 8.1) |
| `dashboard_v2/adapter.js`, `contracts.js` | Sửa | 6.7 |
| `dashboard_v2/app.js` | Sửa | Route hash, mở hộp, nút "Duyệt (bản mới, thử)" (R0) rồi chuyển nút Duyệt (R4), tạm dừng poll; bỏ code hộp mẫu cũ |
| `dashboard_v2/demo-store.js`, `mock-data.js` | Sửa | Review giả (6.7), vẫn 15 job |
| `live.html`, `index.html`, `serve.py` | Sửa | Thẻ script/link cho file mới; body hai trang trùng nhau |
| `src/biliflow/control_center.py` | Sửa | Chỉ `DASHBOARD_V2_FILES` (thêm file mới) và patch M4 |
| `dashboard_v2/browser-check.cjs`, `tests/test_dashboard_v2_review.py` | Sửa/Mới | Mục 8 |

## 7. Các đợt

Mỗi đợt cloud làm xong thì push lên nhánh của phase rồi dừng. Máy thật kéo về kiểm (mục 8.2) và ghi cột Máy thật.
Nhánh: `feat/dashboard-v2`, làm tiếp, chưa merge (người dùng quyết định 2026-10-04). Dashboard V2 xong ở `fb67b9e`, chỉ còn E6, E7. Nếu cần merge riêng Dashboard V2 thì chỉ merge tới commit đó.

### 7.1. R0: nền móng, hộp chỉ xem với ảnh thật, M4

- **R0.1** Áp patch M4: `git apply --check docs/patches/M4-review-back-to-v2.patch` rồi `git apply`. Sau đó chạy lại
  test M4, D2 và browser-check.
- **R0.2** Hộp `#review-dialog` và địa chỉ (6.1), bố cục 3 cỡ màn hình (6.2), link "Mở trang duyệt cũ". Thêm nút tạm
  "Duyệt (bản mới, thử)" trong drawer của job, chỉ khi job duyệt được. Nút "Duyệt" cũ giữ nguyên.
- **R0.3** Adapter: hàm GET, hàm dựng URL, query; demo store và queue giả (6.7).
- **R0.4** `review-core.js`: lọc, sắp xếp, chọn thẻ, thẻ kế tiếp, identity/version, quy tắc khóa.
- **R0.5** Thẻ chỉ xem:
  - ảnh chính qua bộ nạp ảnh tối đa 2 và khung đỏ vùng;
  - tên, thời gian, nhóm, trạng thái;
  - chip, "Lọc khác", tiến độ, cảnh báo phạm vi;
  - nạp dần; poll chỉ vá; tạm dừng poll dashboard.
- **R0.6** Whitelist file mới, CSP không đổi; đo 500 mục giả.

| ID | Hạng mục | Cloud | Máy thật | Bằng chứng |
| --- | --- | --- | --- | --- |
| R0.1 | Patch M4 áp dụng, test M4 + D2 + browser-check đạt | [x] | [ ] | b853963: `git apply --check` rồi `git apply` sạch (4 file: `control_center.py` `_review_back_to_v2`, `app.js`, `browser-check.cjs`, test M4); file patch đã xóa. `Batch5` 5/5 (gồm M4: `/review/1` trùng SHA-256 D2, `?from=v2&view=videos` → `#videos` và chỉ đổi đúng nút, `view` lạ → `#overview`, listener điện thoại `#queue` vẫn có `phone-review`), `test_dashboard_v2_route` 9/9, browser-check 20/20 |
| R0.2 | Hộp riêng, hash, Back/Esc/Đóng về đúng màn, 2 cột / 1 cột / toàn màn hình, link trang cũ, nút "thử" | [x] | [ ] | 0e43fa1: `<dialog id="review-dialog">` ngoài `#main` (`review.js`, `review.css`), regex hash đúng 6.1. `browser-check-review.cjs` ở 1440/1024/390 px × sáng/tối (6 lượt): mở từ nút "Duyệt (bản mới, thử)" → `#review/101/videos`; Esc, "Đóng", Back đều về `#videos`; 2 cột ở 1440 (hộp ≥ 90% bề ngang, ≤ 1280 px), 1 cột ở 1024 và 390, ở 390 hộp đúng 390×844; không tràn ngang; link `/review/101?from=v2&view=videos`. Mở thẳng `#review/101/queue` → màn Hàng đợi rồi hộp, đóng → `#queue`; `#review/abc` → `#overview`. Nút thử chỉ có ở job duyệt được (101 có; 102 đang quét, 106 chờ thiết lập không có), nút "Duyệt" cũ giữ nguyên. Bản demo: không có link trang cũ (không có Control Center) |
| R0.3 | Adapter GET/URL/query; demo store và queue giả | [x] | [ ] | 0e43fa1: `store.review(id)` → `queue/session/mediaKey/resources/exportState/evidence/probeVideo/frameUrl/videoUrl/mediaUrl`; `contracts.request(id, job, body, query)` mã hóa query, danh sách endpoint không đổi (SHA-256 ghim trong test). `probeVideo` gửi `Range: bytes=0-0` ở chế độ `raw` (transport không đọc body). Live store có `pause()/resume()`. `verify-adapter.cjs` 21/21 (thêm 4: GET đúng path + query mã hóa, session một lượt, token không ra ngoài adapter, id job chỉ chữ số; probe raw; query của `request`; pause/resume). Demo store cùng giao diện trên queue giả `BFMock.reviewQueue` (đúng dạng `review_workflow.py`, đủ loại: cảnh nhiều khoảnh khắc, logo có/không vùng, chữ, logo nền tảng, đoạn mở đầu, Visual AI, NEEDS_MORE_CONTEXT, ứng viên phụ, thẻ không ảnh), vẫn 15 job (`verify.cjs` 28/28). Job 101 có 30 mục (25 đã quyết định, 1 Cần xem thêm, 4 chưa duyệt) để hộp và dòng Dashboard cùng "còn 5". Lệnh ghi (`decide`…) chưa có: R2 |
| R0.4 | `review-core.js` khớp hàm trang cũ trên bảng ca mẫu | [x] | [ ] | 0e43fa1: `verify-review.cjs` 12/12 lấy nguyên văn 28 hàm/hằng của `_interactive_html("test-token")` (qua Python) rồi so trên 6 queue giả (demo 30 mục, 30 mục trộn, 500 mục + 20 ứng viên phụ, ca biên, đã duyệt hết, rỗng; > 580 mục): chip và "Lọc khác", nhãn, chữ khóa; từng mục: `catName`, `actionName`, `statusOf`, `isScene`, `hasPlayer`, `studioEligible`, `platformEligible`…; danh sách, `pickFocus`, `nextUndecided`, `step` cho cả 11 bộ lọc (kèm sticky); counts/status; `bulkFilters`; `regionOwner`. Chỗ khác có chủ ý được kiểm riêng: S1 (số hàng loạt theo server; `tests/test_dashboard_v2_review.py` chạy hàm `bulk_keep`/`bulk_accept` thật trên root tạm và khớp từng bộ lọc), S2, S6 (QUEUED quét không khóa; VERIFYING khóa), S7 (`source.sha256`), S8 (SKIPPED chỉ xem). File không có DOM, fetch, timer |
| R0.5 | Thẻ chỉ xem có ảnh thật, lọc, nạp dần, poll chỉ vá, tạm dừng poll dashboard | [x] | [ ] | 0e43fa1: thẻ kiểu `.scene` với ảnh xem trước của báo cáo (`/media/...`, `<img src>`), khung đỏ vùng (theo `regionOwner`, tỉ lệ theo `source_frame_size`), tên, thời gian, nhóm, dòng vùng, trạng thái, đề xuất; chip + "Lọc khác", "N / M cảnh cần quyết định cuối" + thanh, cảnh báo phạm vi, "Chỉ xem · lý do" khi khóa, thông báo quét lại khi queue 404. browser-check-review: lọc 18+ chỉ còn thẻ 18+, "Ứng viên phụ" 3 thẻ; poll đổi version → cùng node, đổi trạng thái; đổi identity → dựng lại; không có `/api/status` trong 6,6 s khi hộp mở (queue vẫn poll), đóng hộp → 1 lần làm mới ngay. "Rõ nhất", dải khung, video, phím tắt: R1 |
| R0.6 | Whitelist, CSP không đổi, đo 500 mục | [x] | [ ] | 0e43fa1: 5 file mới trong `DASHBOARD_V2_FILES`, `live.html`, `index.html` (body hai trang chỉ khác như trước) và `serve.py`; `tests/test_dashboard_v2_review.py` 7/7: file mới phục vụ ở PC, ở listener điện thoại 401 khi chưa có cookie và 200 khi có; CSP header và meta đúng nguyên văn; không chuỗi `blob:`, `createObjectURL`, script/handler inline; chỉ `adapter.js` có `fetch(`; mỗi file mới < 800 dòng; D2 trùng byte; `PHONE_ALLOWED_POSTS` và endpoint không đổi. 500 mục giả: mở hộp có thẻ 215–352 ms, chuyển "Tất cả" 98–250 ms, 24 thẻ đầu rồi +24 khi cuộn, tối đa 2 ảnh cùng lúc (đo ở server giả và trong trang), chỉ thẻ gần màn hình mới tải ảnh; dựng danh sách 11 bộ lọc trong node 0,46 ms. Không POST, không request `blob:`, không lỗi console/CSP |

### 7.2. R1: xem kỹ trong thẻ

- **R1.1** ▶ phát đúng đoạn bằng `<video>` dùng chung; cảnh nhiều khoảnh khắc (chip, phát lần lượt); báo khi không có video.
- **R1.2** Timeline mảnh dưới ảnh, bấm để tua (với cảnh thì nhảy tới đầu khoảnh khắc kế tiếp).
- **R1.3** Dải tối đa 8 khung ("Rõ nhất" trước, ô chờ khi đang tải).
- **R1.4** Bằng chứng: ảnh cắt 360 px, khung vàng của AI và chú thích, "Chi tiết kỹ thuật".
- **R1.5** ⤢ phóng to thẻ; Esc thu nhỏ trước khi đóng hộp.
- **R1.6** Lỗi video, nguồn đã dọn hoặc lưu trữ (chỉ ảnh xem trước), khóa media mới khi lỗi, nhả video.

| ID | Hạng mục | Cloud | Máy thật | Bằng chứng |
| --- | --- | --- | --- | --- |
| R1.1 | Phát đúng đoạn trong thẻ, cảnh nhiều khoảnh khắc | [ ] | [ ] | |
| R1.2 | Timeline và tua | [ ] | [ ] | |
| R1.3 | Dải khung | [ ] | [ ] | |
| R1.4 | Bằng chứng và chi tiết kỹ thuật | [ ] | [ ] | |
| R1.5 | Phóng to thẻ | [ ] | [ ] | |
| R1.6 | Lỗi video, nguồn đã dọn/lưu trữ, khóa mới, nhả video | [ ] | [ ] | |

### 7.3. R2: quyết định (mốc thử 1 của người dùng)

- **R2.1** Giữ / Làm mờ (hoặc "Làm mờ cả cảnh") / Cắt / Cần xem thêm / Xóa quyết định.
  - Nút đã chọn được đánh dấu; ảnh hiện minh họa mờ hoặc cắt.
  - Có xác nhận làm mờ cả cảnh và xác nhận khi Visual AI khác ý; báo lỗi BLUR chưa có vùng (P7).
- **R2.2** Nút vùng: quyết định cho thẻ sở hữu vùng, kèm ghi chú như trang cũ.
- **R2.3** Nút nhớ logo hãng phim / nền tảng, đúng điều kiện; nếu đã nhớ thì bỏ qua.
- **R2.4** Hàng ghi tuần tự (6.7), "Đang lưu…/Đã lưu", đồng bộ lại khi lỗi (P14).
- **R2.5** Hoàn tác (P10), "Tự chuyển cảnh" và sticky (P8).
- **R2.6** Phím tắt (P9).
- **R2.7** Khóa và thông báo "Chỉ xem" (P13, S6, S8).

| ID | Hạng mục | Cloud | Máy thật | Bằng chứng |
| --- | --- | --- | --- | --- |
| R2.1 | Các nút quyết định, xác nhận, lỗi BLUR, minh họa trên ảnh | [ ] | [ ] | |
| R2.2 | Nút vùng | [ ] | [ ] | |
| R2.3 | Nhớ logo hãng phim / nền tảng | [ ] | [ ] | |
| R2.4 | Hàng ghi tuần tự, thử lại, 403, đồng bộ lại | [ ] | [ ] | |
| R2.5 | Hoàn tác, tự chuyển cảnh, sticky | [ ] | [ ] | |
| R2.6 | Phím tắt | [ ] | [ ] | |
| R2.7 | Khóa, S6, S8 | [ ] | [ ] | |
| U-R2 | Người dùng thử trên job thật (8.3, bước 1–4) | — | [ ] | |

### 7.4. R3: hàng loạt và xuất video

- **R3.1** "Giữ tất cả" / "Dùng đề xuất" cho các cảnh đang lọc, ánh xạ bộ lọc, xác nhận có số mục (S1), khóa hộp khi
  chạy (P11).
- **R3.2** Nút "Xuất video" trong hộp, luồng P12.
  - Mở hộp "Xuất video đã duyệt" của V2 chồng lên hộp duyệt, có thêm dòng tài nguyên.
  - Xếp lệnh xong thì đóng hộp duyệt.
- **R3.3** Giới hạn dung lượng và câu xác nhận của hộp xuất khớp `export_dialog.py` (test so chuỗi và giới hạn).
- **R3.4** S2 và S3.

| ID | Hạng mục | Cloud | Máy thật | Bằng chứng |
| --- | --- | --- | --- | --- |
| R3.1 | Hàng loạt | [ ] | [ ] | |
| R3.2 | Nút Xuất video và luồng xuất | [ ] | [ ] | |
| R3.3 | Khớp `export_dialog.py` | [ ] | [ ] | |
| R3.4 | S2, S3 | [ ] | [ ] | |

### 7.5. R4: điện thoại, chuyển nút Duyệt, nghiệm thu (mốc thử 2)

- **R4.1** Bố cục điện thoại và laptop (6.2, P17).
- **R4.2** Kiểm qua listener điện thoại:
  - cookie; tối đa 2 ảnh cùng lúc; video có Range;
  - mọi POST nằm trong `PHONE_ALLOWED_POSTS` (không thêm);
  - không lộ thao tác chỉ-PC.
- **R4.3** Nút "Duyệt" của V2 (bản chạy thật và bản demo) mở hộp mới; bỏ nút "thử"; cập nhật browser-check
  (`browser-check.cjs:222`).
- **R4.4** Tài liệu: README, `docs/DASHBOARD_V2_PHONE.md`, CHANGELOG, PROJECT_STATUS, SESSION_HANDOFF.
- **R4.5** Nghiệm thu với người dùng (8.3, đủ các bước).

| ID | Hạng mục | Cloud | Máy thật | Bằng chứng |
| --- | --- | --- | --- | --- |
| R4.1 | Bố cục điện thoại và laptop | [ ] | [ ] | |
| R4.2 | Listener điện thoại | [ ] | [ ] | |
| R4.3 | Nút Duyệt mở hộp mới, browser-check | [ ] | [ ] | |
| R4.4 | Tài liệu | [ ] | [ ] | |
| U-R4 | Người dùng nghiệm thu (8.3) | — | [ ] | |

## 8. Test

### 8.1. Cloud (mỗi đợt)

- **`verify-review.cjs`** (node):
  - Tách các hàm của trang cũ (`visible`, `isLogoItem`, `isAdItem`, `studioEligible`, `needsFullFrame`, `pickFocus`,
    `nextUndecided`, `queueVersion`, `decisionsLocked`, ánh xạ hàng loạt…) từ script `_interactive_html("test-token")`,
    cách làm giống G1 ở đợt 2.
  - So với `review-core.js` trên các queue giả: đủ loại mục, cảnh nhiều khoảnh khắc, ứng viên phụ, NEEDS_MORE_CONTEXT,
    nguồn đã dọn, SKIPPED.
  - Chỗ khác có chủ ý (S1–S8) phải được liệt kê trong test.
- **Payload:**
  - Một bảng ca mẫu → body POST của hộp mới phải bằng body trang cũ gửi.
  - Browser-check ghi lại body thật của cả hai trang trên server giả.
  - Test Python gửi các payload này vào route thật trên root tạm với queue giả và kiểm queue trả về.
- **Adapter** (`verify-adapter.cjs`):
  - 2 quyết định nhanh trên 2 thẻ → 2 POST đúng thứ tự (không gộp);
  - thử lại 300/900 ms; 403 → lấy phiên rồi gửi lại 1 lần;
  - URL builder mã hóa đúng.
- **Python** (`tests/test_dashboard_v2_review.py`):
  - file mới nằm trong whitelist, được phục vụ cả trên listener điện thoại khi có cookie;
  - CSP không đổi, không có `blob:` và không có script inline trong file mới;
  - D2 trùng byte; `PHONE_ALLOWED_POSTS` và endpoint của `contracts.js` không đổi.
- **Gate cũ:** `node --check`, `verify.cjs` (15 job), `tests.test_dashboard_v2_*`, test Control Center liên quan, lệnh A4
  (10 stage `none`).
- **Browser-check** (Playwright trên cloud), ở 1440, 1024 và 390 px, chế độ sáng và tối:
  - 2 cột ở 1440, 1 cột ở 1024 và 390, hộp toàn màn hình ở 390;
  - 0 lỗi console/CSP; không request `blob:`; tối đa 2 ảnh cùng lúc;
  - phím tắt chạy; poll không dựng lại `<video>` đang phát;
  - Esc, "Đóng" và Back về đúng màn; không tràn ngang ở 390 px.

### 8.2. Máy thật (sau mỗi đợt, khoảng 15 phút, không review dài)

- Kéo về, full suite, node gates, A4, D2, kiểm attribution.
- **Bản xem thử** trên handler thật với root tạm `temp\v2-preview-*`:
  - video tổng hợp tạo bằng ffmpeg trong `temp`, queue giả;
  - từ R2 cho phép ghi nhưng chỉ vào root tạm; không bao giờ root thật;
  - xem hộp, thẻ, media, phím tắt, 3 cỡ màn hình trên Windows.
- **Không bao giờ POST vào Control Center thật** (`127.0.0.1:8765`) và không đọc ghi `state` thật.

### 8.3. Người dùng (dữ liệu thật; sau R2 bước 1–4, sau R4 đủ cả 8 bước)

1. Từ V2 mở hộp duyệt mới của một job: đúng tên video, đúng số cảnh, ảnh và video đúng cảnh.
2. Duyệt 3–5 cảnh bằng chuột và phím 1–4, thử Hoàn tác và Xóa quyết định. Mở trang cũ bằng link và thấy cùng các
   quyết định.
3. Một cảnh logo (nếu có): thử nút nhớ logo, rồi đổi lại quyết định.
4. Đóng rồi mở lại hộp: quyết định vẫn còn. Đóng hộp về đúng màn V2.
5. "Giữ tất cả" hoặc "Dùng đề xuất" trên một bộ lọc nhỏ.
6. Xuất một video ngắn và theo dõi tiến độ. Tên file và kết quả theo đúng quy tắc cũ.
7. Điện thoại (link + mã): duyệt vài cảnh, phát video trong thẻ, phóng to thẻ.
8. Laptop: hộp 1 cột.

## 9. Rủi ro và cách giảm

- **Poll dựng lại video:** hộp riêng ngoài `#main`, chỉ vá thẻ, kèm test trên browser-check.
- **Nhiều thẻ có ảnh và video:** nạp dần, ảnh chỉ cho thẻ gần màn hình, tối đa 2 ảnh, một `<video>` dùng chung.
- **Payload lệch trang cũ:** test so sánh payload và test trên route thật.
- **Adapter gộp lệnh ghi (`once()`):** hàng tuần tự theo job, kèm test.
- **Khóa media đổi sau khi Control Center khởi động lại:** lấy khóa mới khi lỗi.
- **Hộp chồng hộp** (xác nhận, xuất): hộp duyệt là `<dialog>` riêng; test Esc và focus.
- **File phình to:** tách file theo chức năng (6.10).
- **Repo công khai:** chỉ dữ liệu giả.

## 10. Câu hỏi cho người dùng

- Q1–Q7 đã trả lời ngày 2026-10-04 (mục 1).
- Còn một điểm người dùng có thể đổi: S8 (video đã Bỏ qua thì hộp chỉ xem, theo bản mẫu V2).

## 11. Nhật ký (mục mới nhất ở trên cùng)

| Ngày | Commit | Việc đã làm | Kết quả | Còn lại |
| --- | --- | --- | --- | --- |
| 2026-10-04 | b853963, 0e43fa1, (commit docs này) | Cloud làm R0 (mục 7.1): áp patch M4 rồi xóa file patch; hộp `#review-dialog` chỉ xem với địa chỉ `#review/<id>/<view>`, nút tạm "Duyệt (bản mới, thử)" trong drawer; `review-core.js`, `review-cards.js`, `review-media.js`, `review.js`, `review.css`; adapter GET/URL/query, pause/resume poll; demo store và queue giả; whitelist. Test mới: `verify-review.cjs`, `browser-check-review.cjs` (file riêng để `browser-check.cjs` dưới 800 dòng), `tests/test_dashboard_v2_review.py`. Tự review bảo mật: chuỗi queue qua `esc()`, hash chỉ nhận số và màn cho phép, hộp không gửi lệnh ghi | `node --check` mọi file; verify 28/28 (15 job); verify-adapter 21/21; verify-review 12/12; browser-check 20/20; browser-check-review 14/14 (1440/1024/390 px × sáng/tối, 500 mục); test_dashboard_v2_* 80 OK (contract 10, frontend 3, route 9 gồm D2, status 2, phone 15, hardening 34, review 7); test Control Center liên quan như trước (test_control_center 52/53, test_skip_export 26/30: lỗi thiếu PowerShell cũ; source_cleanup_http, source_archive_http, export_identity, export_dialog, logo_memory_admin, review_workflow, golden_label_app OK); A4 10 stage `none` | Máy thật kiểm R0 (mục 8.2, bản xem thử với root tạm); cloud làm R1 khi được giao |
| 2026-10-04 | (commit này) | Người dùng quyết định làm trang duyệt ngay, chưa merge; E6, E7 của Dashboard V2 test chung khi thử trang duyệt mới. Máy thật kiểm patch M4 trên file đã commit ở đầu nhánh: áp sạch (`git show HEAD:docs/patches/M4-review-back-to-v2.patch` rồi `git apply --check --cached`). Trên Windows, file patch checkout ra CRLF nên `git apply --check` trên thư mục làm việc báo lỗi; đó không phải lỗi của patch | Sẵn sàng giao R0 trên `feat/dashboard-v2` | Cloud làm R0 |
| 2026-10-04 | (commit này) | Người dùng nhắc V2 đã có UI duyệt; máy thật mở bản demo (`serve.py`, chỉ file tĩnh) xem hộp "Duyệt cảnh" ở 1440 px và 375 px, rồi viết lại plan dựa trên hộp đó. Ghi các câu trả lời Q1–Q7 | Plan theo hộp "Duyệt cảnh" của bản mẫu | Merge `feat/dashboard-v2` khi người dùng test xong; giao R0 |
| 2026-10-04 | (chưa commit) | Máy thật soạn bản đầu của plan và inventory từ 2 lượt đọc code chỉ đọc ở 18d3c18, kiểm lại số dòng phần điện thoại ở c91746f; thêm patch M4 của cloud (fbe0f89) vào R0 | Bản nháp, bố cục mới; người dùng không đồng ý vì V2 đã có UI duyệt | Viết lại theo hộp "Duyệt cảnh" (dòng trên) |
