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
- **S9 (người dùng chọn 2026-10-04, sau khi thử U-R2).** Thẻ chỉ mượn khung đỏ của một thẻ logo khác (`regionOwner(x)`
  khác chính thẻ đó, ví dụ "Kiểm tra đoạn kết" mượn vùng của "Logo nền tảng iQIYI") không có 2 nút vùng. Thay vào đó là
  dòng "Khung đỏ là vùng logo của thẻ “<tên thẻ chủ>” (<thời gian áp dụng>) — đang: <quyết định của thẻ chủ>" và nút
  "Đi tới thẻ logo"; khung đỏ trên ảnh của thẻ mượn vẽ nét đứt kèm thời gian áp dụng. Chỉ thẻ sở hữu vùng còn nút vùng.
  Trang cũ hiện nút vùng trên cả thẻ mượn; khi hai thẻ nằm cạnh nhau, nút của hai thẻ đổi cùng một quyết định nên
  trông như bị "nhảy chung" (R2-B2). Payload không đổi; chỉ bớt nút trên thẻ mượn.

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
| R0.1 | Patch M4 áp dụng, test M4 + D2 + browser-check đạt | [x] | [x] | b853963: `git apply --check` rồi `git apply` sạch (4 file: `control_center.py` `_review_back_to_v2`, `app.js`, `browser-check.cjs`, test M4); file patch đã xóa. `Batch5` 5/5 (gồm M4: `/review/1` trùng SHA-256 D2, `?from=v2&view=videos` → `#videos` và chỉ đổi đúng nút, `view` lạ → `#overview`, listener điện thoại `#queue` vẫn có `phone-review`), `test_dashboard_v2_route` 9/9, browser-check 20/20 **Máy thật** (2026-10-04, `7d7bc7b`, Windows): full suite 1277 OK, 25 skip; gồm test M4 trên handler thật với root tạm và D2 trùng byte. Máy không có Playwright nên browser-check dựa vào kết quả cloud. |
| R0.2 | Hộp riêng, hash, Back/Esc/Đóng về đúng màn, 2 cột / 1 cột / toàn màn hình, link trang cũ, nút "thử" | [x] | [x] | 0e43fa1: `<dialog id="review-dialog">` ngoài `#main` (`review.js`, `review.css`), regex hash đúng 6.1. `browser-check-review.cjs` ở 1440/1024/390 px × sáng/tối (6 lượt): mở từ nút "Duyệt (bản mới, thử)" → `#review/101/videos`; Esc, "Đóng", Back đều về `#videos`; 2 cột ở 1440 (hộp ≥ 90% bề ngang, ≤ 1280 px), 1 cột ở 1024 và 390, ở 390 hộp đúng 390×844; không tràn ngang; link `/review/101?from=v2&view=videos`. Mở thẳng `#review/101/queue` → màn Hàng đợi rồi hộp, đóng → `#queue`; `#review/abc` → `#overview`. Nút thử chỉ có ở job duyệt được (101 có; 102 đang quét, 106 chờ thiết lập không có), nút "Duyệt" cũ giữ nguyên. Bản demo: không có link trang cũ (không có Control Center) **Máy thật** (2026-10-04, `7d7bc7b`, Windows, bản demo `serve.py` (chỉ file tĩnh) trong trình duyệt của app): nút "Duyệt (bản mới, thử)" có trong drawer của job chờ duyệt, nút "Duyệt cảnh" cũ vẫn còn; 1440 px: hộp 1280×868, thẻ 2 cột; 1024 px: 1 cột; 375 px: hộp toàn màn hình, 1 cột; không tràn ngang. Esc, Back, "Đóng" đều đóng hộp và về `#videos`. Mở thẳng `#review/101/videos` thì hộp mở; job không có → "Không tìm thấy video."; màn lạ có mã chèn → về `#overview`, không chạy gì. Bản demo thay link trang cũ bằng dòng "Trang duyệt cũ chỉ có khi chạy cùng Control Center"; link ở bản live xem khi chạy trên Control Center thật. |
| R0.3 | Adapter GET/URL/query; demo store và queue giả | [x] | [x] | 0e43fa1: `store.review(id)` → `queue/session/mediaKey/resources/exportState/evidence/probeVideo/frameUrl/videoUrl/mediaUrl`; `contracts.request(id, job, body, query)` mã hóa query, danh sách endpoint không đổi (SHA-256 ghim trong test). `probeVideo` gửi `Range: bytes=0-0` ở chế độ `raw` (transport không đọc body). Live store có `pause()/resume()`. `verify-adapter.cjs` 21/21 (thêm 4: GET đúng path + query mã hóa, session một lượt, token không ra ngoài adapter, id job chỉ chữ số; probe raw; query của `request`; pause/resume). Demo store cùng giao diện trên queue giả `BFMock.reviewQueue` (đúng dạng `review_workflow.py`, đủ loại: cảnh nhiều khoảnh khắc, logo có/không vùng, chữ, logo nền tảng, đoạn mở đầu, Visual AI, NEEDS_MORE_CONTEXT, ứng viên phụ, thẻ không ảnh), vẫn 15 job (`verify.cjs` 28/28). Job 101 có 30 mục (25 đã quyết định, 1 Cần xem thêm, 4 chưa duyệt) để hộp và dòng Dashboard cùng "còn 5". Lệnh ghi (`decide`…) chưa có: R2 **Máy thật** (2026-10-04, `7d7bc7b`, Windows): `verify-adapter.cjs` 20 test về adapter và review đạt ổn định (test tạm dừng poll thuộc R0.5). |
| R0.4 | `review-core.js` khớp hàm trang cũ trên bảng ca mẫu | [x] | [x] | 0e43fa1: `verify-review.cjs` 12/12 lấy nguyên văn 28 hàm/hằng của `_interactive_html("test-token")` (qua Python) rồi so trên 6 queue giả (demo 30 mục, 30 mục trộn, 500 mục + 20 ứng viên phụ, ca biên, đã duyệt hết, rỗng; > 580 mục): chip và "Lọc khác", nhãn, chữ khóa; từng mục: `catName`, `actionName`, `statusOf`, `isScene`, `hasPlayer`, `studioEligible`, `platformEligible`…; danh sách, `pickFocus`, `nextUndecided`, `step` cho cả 11 bộ lọc (kèm sticky); counts/status; `bulkFilters`; `regionOwner`. Chỗ khác có chủ ý được kiểm riêng: S1 (số hàng loạt theo server; `tests/test_dashboard_v2_review.py` chạy hàm `bulk_keep`/`bulk_accept` thật trên root tạm và khớp từng bộ lọc), S2, S6 (QUEUED quét không khóa; VERIFYING khóa), S7 (`source.sha256`), S8 (SKIPPED chỉ xem). File không có DOM, fetch, timer **Máy thật** (2026-10-04, `7d7bc7b`, Windows): `verify-review.cjs` 12/12. Worktree không có `.venv` nên chạy với `BILIFLOW_PYTHON` là Python của thư mục chính. 500 mục, 11 bộ lọc: 0,19 ms mỗi lượt. |
| R0.5 | Thẻ chỉ xem có ảnh thật, lọc, nạp dần, poll chỉ vá, tạm dừng poll dashboard | [x] | [!] | 0e43fa1: thẻ kiểu `.scene` với ảnh xem trước của báo cáo (`/media/...`, `<img src>`), khung đỏ vùng (theo `regionOwner`, tỉ lệ theo `source_frame_size`), tên, thời gian, nhóm, dòng vùng, trạng thái, đề xuất; chip + "Lọc khác", "N / M cảnh cần quyết định cuối" + thanh, cảnh báo phạm vi, "Chỉ xem · lý do" khi khóa, thông báo quét lại khi queue 404. browser-check-review: lọc 18+ chỉ còn thẻ 18+, "Ứng viên phụ" 3 thẻ; poll đổi version → cùng node, đổi trạng thái; đổi identity → dựng lại; không có `/api/status` trong 6,6 s khi hộp mở (queue vẫn poll), đóng hộp → 1 lần làm mới ngay. "Rõ nhất", dải khung, video, phím tắt: R1 **Máy thật** (2026-10-04, `7d7bc7b`, Windows): test "Live store: pause() … resume() refreshes at once" của `verify-adapter.cjs` chập chờn: 1/3 lần khi chạy riêng, 2/2 lần khi máy bận, báo `5 !== 4`. Khi test hỏng, timer không dừng nên node treo chứ không thoát. Giao sửa ở R0-T1. Hành vi thật không sai: sau `resume()` thì poll 20 ms vẫn chạy, nên trong 5 ms có thể có thêm một nhịp. Trên bản demo `serve.py` (chỉ file tĩnh) trong trình duyệt của app: 4 thẻ chỉ xem, ảnh tải được, có chip lọc; "Xuất video", "Giữ tất cả", "Dùng đề xuất", "Hoàn tác" đều mờ. Ảnh thật của báo cáo trên Control Center thật chưa xem được vì cần khởi động lại Control Center; để tới R2. Sau R1 (2026-10-04): R0-T1 đã sửa và đạt (bảng R1). Vẫn `[!]` vì lỗi R1-B1 của bộ nạp ảnh (ghi trên mục 7.3). |
| R0.6 | Whitelist, CSP không đổi, đo 500 mục | [x] | [x] | 0e43fa1: 5 file mới trong `DASHBOARD_V2_FILES`, `live.html`, `index.html` (body hai trang chỉ khác như trước) và `serve.py`; `tests/test_dashboard_v2_review.py` 7/7: file mới phục vụ ở PC, ở listener điện thoại 401 khi chưa có cookie và 200 khi có; CSP header và meta đúng nguyên văn; không chuỗi `blob:`, `createObjectURL`, script/handler inline; chỉ `adapter.js` có `fetch(`; mỗi file mới < 800 dòng; D2 trùng byte; `PHONE_ALLOWED_POSTS` và endpoint không đổi. 500 mục giả: mở hộp có thẻ 215–352 ms, chuyển "Tất cả" 98–250 ms, 24 thẻ đầu rồi +24 khi cuộn, tối đa 2 ảnh cùng lúc (đo ở server giả và trong trang), chỉ thẻ gần màn hình mới tải ảnh; dựng danh sách 11 bộ lọc trong node 0,46 ms. Không POST, không request `blob:`, không lỗi console/CSP **Máy thật** (2026-10-04, `7d7bc7b`, Windows): full suite có test whitelist, CSP và listener điện thoại cho 5 file mới; A4: 42 đường dẫn được băm, `none`; đo 500 mục ở R0.4. Không file JS mới nào gọi `fetch(`, dùng `blob:` hay POST; file dài nhất 207 dòng. |

**Máy thật tìm được sau R0 (giao cùng R1):**

- **R0-T1. Test tạm dừng/tiếp tục poll của adapter chập chờn, và treo khi hỏng.**
  - Ở `verify-adapter.cjs`, test "Live store: pause() … resume() refreshes at once":
    - sau `resume()` test chờ 5 ms rồi đòi đúng `before + 1` lần gọi `/api/status`;
    - nhưng poll 20 ms vẫn chạy, nên đôi khi có thêm một nhịp. Trên Windows hay gặp hơn vì timer thô (khoảng 15 ms).
  - Khi assert hỏng thì `store.stop()` không chạy, `setInterval` giữ node sống, nên `node verify-adapter.cjs` treo chứ không báo lỗi.
  - Sửa (chỉ test): đếm khi không còn timer chạy (dừng interval trước khi `resume()`, hoặc dùng interval dài và gọi tay); `store.stop()` luôn chạy trong `finally`; runner gọi `process.exit(1)` khi có test hỏng để timer sót lại không làm treo gate. Không đổi `adapter.js` nếu hành vi đã đúng.
- **Ghi nhận cho R4 (chưa sửa):** ở 375 px, hàng chip lọc và hàng nút trong hộp cuộn ngang mà không có dấu hiệu cuộn (giống P9 của trang cũ).

### 7.2. R1: xem kỹ trong thẻ

- **R1.1** ▶ phát đúng đoạn bằng `<video>` dùng chung; cảnh nhiều khoảnh khắc (chip, phát lần lượt); báo khi không có video.
- **R1.2** Timeline mảnh dưới ảnh, bấm để tua (với cảnh thì nhảy tới đầu khoảnh khắc kế tiếp).
- **R1.3** Dải tối đa 8 khung ("Rõ nhất" trước, ô chờ khi đang tải).
- **R1.4** Bằng chứng: ảnh cắt 360 px, khung vàng của AI và chú thích, "Chi tiết kỹ thuật".
- **R1.5** ⤢ phóng to thẻ; Esc thu nhỏ trước khi đóng hộp.
- **R1.6** Lỗi video, nguồn đã dọn hoặc lưu trữ (chỉ ảnh xem trước), khóa media mới khi lỗi, nhả video.

| ID | Hạng mục | Cloud | Máy thật | Bằng chứng |
| --- | --- | --- | --- | --- |
| R1.1 | Phát đúng đoạn trong thẻ, cảnh nhiều khoảnh khắc | [x] | [x] | 1191690: một `<video>` dùng chung (`review-media.js` `createPlayer`, `preload="none"`, `src` đặt lúc phát lần đầu, chuyển vào khung ảnh của thẻ đang phát), logic phát như trang cũ (`playRange`, `momentGuard`, `playSequence`). browser-check-review ở 1440/1024/390 px × sáng/tối (6 lượt): ▶ phát `gore` 0:08–0:20 trong đúng thẻ, chỉ có 1 `<video>`; nút thành "❚❚ Dừng" khi đang phát. Cảnh 3 khoảnh khắc: 3 chip, "▶ Phát lần lượt 3 khoảnh khắc" chạy đúng thứ tự 1→2→3 rồi dừng. Không có video → lý do của trang cũ thay cho ▶ (`videoReason`, so khớp trong verify-review); bản demo không có khóa media nên chỉ hiện ảnh. Clip thử: VP8 70 s do ffmpeg tạo trong `temp/` **Máy thật** (2026-10-04, `ea2adcc`, Windows, bản xem thử chỉ đọc trên handler thật: root tạm, clip VP8 70 s do ffmpeg của bản cài tạo trong root tạm, queue giả từ `BFMock.reviewQueue` với ảnh xem trước là khung thật của clip, mọi POST bị chặn; script cục bộ `temp/ui-plan/dashboard-v2-check/review_preview_server.py`, không trong git): thẻ có 3 khoảnh khắc (1,4–2,2 / 2,7–3,5 / 4,1–4,9 s), "Phát lần lượt" phát đủ rồi dừng ở 4,87 s; "Phát đoạn này" ở thẻ 0:12–0:24 đang chạy ở 13,8 s; cả hộp chỉ có một `<video>`, video trả 206. Ở 375 px vẫn phát được. |
| R1.2 | Timeline và tua | [x] | [x] | 1191690: markup timeline giống hệt `renderTimeline` của trang cũ (đoạn, khoảnh khắc, cửa sổ máy dò, vạch, điểm "Rõ nhất"; verify-review so trên mọi mục mẫu × 5 loại bằng chứng), đầu phát đỏ chạy theo video. Bấm 50% → video dừng ở 0:14,7 (±0,6 s) và nhãn "0:14 / đoạn 0:08–0:20" (6 lượt); với cảnh, bấm vào khoảng trống → nhảy tới đầu khoảnh khắc kế tiếp, chip 2 sáng (`seekTarget`, đúng công thức của trang cũ) **Máy thật** (2026-10-04, `ea2adcc`, Windows, cùng bản xem thử): bấm giữa timeline của thẻ đầu thì video về thẻ đó, ở 3,4 s (trong đoạn của thẻ). |
| R1.3 | Dải khung | [x] | [x] | 1191690: tối đa 8 khung theo `pickStrip`/`pickSceneStrip`/`stripFrames` của trang cũ (verify-review: 1270 dải khớp, có/không khóa, 5 loại bằng chứng), "Rõ nhất" đứng đầu, nhãn thời gian và số khoảnh khắc; 8 ô chờ khi bằng chứng đang tải (server giả chậm 1,5 s); bấm một khung → video dừng đúng thời điểm đó (±0,3 s). Bằng chứng chỉ tải cho thẻ đang chọn, thẻ chưa duyệt kế tiếp sau 600 ms; ảnh khung qua bộ nạp tối đa 2 **Máy thật** (2026-10-04, `ea2adcc`, Windows, cùng bản xem thử): dải 8 khung lấy từ máy chủ thật (ffmpeg), có số khoảnh khắc trên từng khung; ở 375 px dải xuống 2 hàng, không tràn. Không thử được "Rõ nhất" vì queue giả không có điểm nên máy chủ trả `strongest: null`; phần này dựa vào cloud và xem lại trên dữ liệu thật ở U-R2. |
| R1.4 | Bằng chứng và chi tiết kỹ thuật | [x] | [x] | 1191690: `review-detail.js` (mới, trong whitelist) chép chữ của trang cũ; verify-review: "Chi tiết kỹ thuật" bằng nguyên văn `techDetails(x, ev, false)` của trang cũ trên mọi mục mẫu, cùng `decisionScope`, `overlapCoverage`, nhãn/lý do, Visual AI, bằng chứng máy dò, dòng AI; khung vàng/đỏ và chú thích khớp `evidenceMediaHtml`. browser-check-review: thẻ phóng to có ảnh cắt 360 px vẽ lên canvas từ ảnh cùng origin (vùng khoanh đỏ), thẻ logo không vùng có 2 khung vàng, chú thích "Khung vàng: vùng AI định vị, chỉ để tham khảo — không phải vùng sẽ làm mờ · watermark đã có thẻ riêng" và nhãn "watermark — đã có thẻ riêng"; "Chi tiết kỹ thuật" mở vẫn mở sau poll **Máy thật** (2026-10-04, `ea2adcc`, Windows, cùng bản xem thử, bộ lọc "Tất cả"): có dòng "Visual AI 93%: Làm mờ toàn cảnh", khung AI (`rv-aibox`, có khung đã được che); "Chi tiết kỹ thuật" mở ra có loại, ưu tiên, điểm, thời gian và phạm vi áp dụng; mục không có ảnh ghi "Không có ảnh xem trước cho mục này.". Ảnh cắt 360 px chưa thấy trên queue giả, phần này dựa vào cloud. |
| R1.5 | Phóng to thẻ | [x] | [x] | 1191690: ⤢ cho thẻ chiếm cả bề ngang (ở 1440 px bằng bề rộng lưới thẻ), một thẻ phóng to mỗi lúc; Esc lần 1 chỉ thu nhỏ (hộp vẫn mở), Esc lần 2 mới đóng hộp và về `#videos`; không tràn ngang khi phóng to (6 lượt) **Máy thật** (2026-10-04, `ea2adcc`, Windows, cùng bản xem thử): ⤢ phóng to thẻ (1219×1137 ở 1440 px); Esc lần đầu thu nhỏ (603×624), hộp vẫn mở; Esc lần hai đóng hộp và về `#videos`. |
| R1.6 | Lỗi video, nguồn đã dọn/lưu trữ, khóa mới, nhả video | [x] | [x] | 1191690: lỗi video → dò bằng `Range: bytes=0-0` rồi đổi mã 404/409/410/415/lỗi giải mã sang lý do của trang cũ (`probeReason`); server giả trả 415 → "Trình duyệt không phát được định dạng video này…" thay cho ▶. Bằng chứng báo video không dùng được → lý do "Video nguồn đã thay đổi sau khi quét…". Khóa media đổi (Control Center khởi động lại): 403 → đúng 1 lần lấy phiên mới cho cả hộp, video phát với khóa mới, khung bị từ chối với khóa cũ chỉ dựng lại URL với khóa hiện tại (giống `fetchFrame`); không khung nào lỗi. Job 113 (nguồn đã dọn): không ▶, lý do "Video gốc đã được dọn vào Thùng rác…", không request bằng chứng, khung hay video. Nhả video (`pause`, bỏ `src`, `load()`, gỡ khỏi trang) khi đóng hộp, khi đổi lượt quét và khi `pagehide`; tạm dừng khi ẩn tab. Poll khi đang phát: cùng `<video>`, cùng thẻ, vẫn phát. Sửa trong lúc test: một lệnh phát đang chờ không bị bỏ khi khóa đổi; khung không gọi lấy phiên mới nhiều lần **Máy thật** (2026-10-04, `ea2adcc`, Windows, cùng bản xem thử): đóng hộp thì `<video>` dừng, bị gỡ khỏi trang và bỏ `src`. Job thiếu video gốc chỉ hiện ảnh xem trước (4/4 ảnh tải được) với dòng "Không tìm thấy video nguồn", không có nút phát, không tạo `<video>`. Ở 1024 px 1 cột, 375 px toàn màn hình, không tràn; chế độ tối đọc rõ. Toàn bộ request là GET; khi hộp mở, dashboard ngừng poll `/api/status`. |
| R0-T1 | Test tạm dừng/tiếp tục poll không chập chờn; test hỏng thì node thoát, không treo | [x] | [x] | fb6b273, chỉ `verify-adapter.cjs` (không đổi `adapter.js`): dừng interval trước `resume()` rồi mới đếm, `resume()` lần 2 không gọi thêm; `store.stop()` trong `finally`; runner ghi tên test hỏng rồi `process.exit(1)` sau khi ghi xong lỗi. 5 lần liên tiếp và 24 lần chạy song song (giả máy bận) đều 21/21; thử cố ý làm hỏng và bỏ `stop()` → node thoát ngay với mã 1, không treo **Máy thật** (2026-10-04, `ea2adcc`, Windows): `verify-adapter.cjs` chạy 8 lần lúc máy bận vì full suite: 21/21 cả 8 lần, không còn `5 !== 4`. Giả lập test này hỏng và timer bị bỏ sót (preload trong scratchpad, không sửa repo): node thoát ngay với mã 1 và in tên test. |

**Máy thật tìm được sau R1 (giao cùng R2):**

- **R1-B1. Bộ nạp ảnh mất một chỗ khi một ô ảnh được `watch()` lại lúc ảnh đầu còn đang tải.**
  - Trong hộp: thẻ được `watch` khi vẽ, rồi `watchCard` chạy lại khi bằng chứng về. Mục cũ bị hủy, nhưng mục mới ghi đè `img.onload`/`onerror` của cùng thẻ `<img>`, nên `settle()` của lần tải đầu không bao giờ chạy.
  - Kết quả: `active` còn 1 dù không có ảnh nào đang tải. Từ đó chỉ tải được từng ảnh một thay vì 2, cho tới khi tải lại trang; `reset()` khi đóng hộp không trả lại chỗ. Trên bản xem thử: `BFReviewStats.activeImages` = 1, không có ô `.loading` nào và không ảnh nào đang tải.
  - Tái hiện trong trang: `L = BFReviewMedia.createImageLoader({max: 2})`; `L.watch(a, u, 10)` rồi lại `L.watch(a, u, 10)`; khi ảnh tải xong thì `L.active()` vẫn là 1 (mong đợi 0).
  - Sửa: mỗi lần tải đã bắt đầu phải trả chỗ đúng một lần, kể cả khi mục bị hủy hoặc ô được `watch` lại (ví dụ giữ mục đang tải nếu URL không đổi, hoặc dùng listener hay `Image()` riêng cho từng lần tải). Thêm test: `watch` hai lần lúc đang tải thì `active()` về 0 sau khi ảnh xong; vẫn tối đa 2 ảnh cùng lúc.
- **Ghi nhận thêm cho R4 (chưa sửa):** ở 375 px, hàng chip khoảnh khắc trong thẻ cũng bị cắt ở mép phải mà không có dấu hiệu cuộn, giống hàng chip lọc.
- **Ghi chú:** khi trình duyệt bị ẩn, IntersectionObserver không chạy nên các thẻ chưa chọn không nạp ảnh. Đó là đúng thiết kế (ảnh chỉ nạp khi thẻ gần màn hình), không phải lỗi.

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
| R2.1 | Các nút quyết định, xác nhận, lỗi BLUR, minh họa trên ảnh | [x] | [x] | 539e6a6: Giữ / Làm mờ (mục không có vùng: "Làm mờ cả cảnh") / Cắt / Cần xem thêm / Xóa quyết định; nút đã chọn có `.selected` và `aria-pressed`, bấm lại thì gửi lại; dòng "Đã chọn: …"; minh họa trên ảnh: vùng đỏ mờ (`backdrop-filter`), cả ảnh mờ, Cắt làm tối ảnh kèm "Đã chọn cắt". Xác nhận làm mờ cả cảnh và xác nhận khi Visual AI ≥ 0,9 khác ý hiện trong hộp V2 với chữ của trang cũ (S5: Enter xác nhận, Esc chỉ hủy hộp xác nhận, phím tắt tạm tắt); lỗi BLUR chưa có vùng như trang cũ. verify-review: 2820 ca (mọi mục mẫu × 4 quyết định × 5 kiểu nút × 3 cách trả lời) cho cùng câu hỏi, lỗi, body POST, trạng thái mục, số đếm và mục hoàn tác như `decide()` / `clearDecision()` của trang cũ (2413 body giống hệt). browser-check-review-write: 14 lựa chọn bằng nút thật, hộp xác nhận và phím trên V2 gửi đúng 14 body mà trang cũ thật (`_interactive_html`) gửi tới cùng server giả. Test Python: 12 body gửi vào route thật trên root tạm, queue trả về khớp với trạng thái hộp hiển thị; 3 body bị từ chối (nhớ logo không có ảnh trên root tạm, BLUR không vùng) trả 400 và queue trùng byte. R2-K (khác có chủ ý, theo P7 và 6.3): nút "Làm mờ" và phím 2 của mục có vùng đỏ làm mờ vùng đó, body bằng `decide(id, 'BLUR', false)` của trang cũ, không hỏi làm mờ cả khung **Máy thật** (2026-10-04, `d152ef3`, Windows, bản xem thử trên handler thật: root tạm, clip VP8 do ffmpeg của bản cài tạo trong root tạm, queue giả từ `BFMock.reviewQueue`, thêm job đang xuất và job đã Bỏ qua; chỉ POST decision/clear của các job này đi vào route thật và chỉ ghi vào root tạm, mọi POST khác 403; script cục bộ `temp/ui-plan/dashboard-v2-check/review_preview_server.py`, không trong git): "Làm mờ cả cảnh" trên cảnh 18+ 3 khoảnh khắc khi thẻ đang phóng to → hộp xác nhận chữ trang cũ ("Làm mờ toàn bộ khung hình trong 3 khoảnh khắc (tổng 0:02)? …"), focus ở "Xác nhận"; Esc thật chỉ đóng hộp xác nhận (hộp duyệt vẫn mở, thẻ vẫn phóng to, server không nhận gì); Enter → 1 POST `{"id":"adult-12-0000","decision":"BLUR","full_frame":true,"note":null}`, queue ghi `FULL_FRAME`, nút có `.selected`, ảnh mờ cả khung, "Đã lưu", tự chọn thẻ chưa duyệt kế tiếp. Chọn Cắt trên thẻ có Visual AI 93% khác ý → hộp "Visual AI tin cậy 93% đề xuất “Làm mờ toàn cảnh” … Bạn vẫn muốn chọn “Cắt cả cảnh” …?", Hủy thì không gửi. R2-K: thẻ chữ có vùng riêng có nút "Làm mờ"; phím 2 → `{"decision":"BLUR","full_frame":false}`, không hỏi làm mờ cả khung. Cắt: ảnh tối kèm "Đã chọn cắt". 1440 px 2 cột, 1024 px 1 cột, 375 px hộp 375×812; không tràn ngang (hàng chip và hàng công cụ cuộn ngang, xem ghi nhận R4 dưới bảng); chế độ tối: chữ trên thẻ và nút có tương phản 7,5–11,8. Sau khi thử: 24/24 thẻ hiện đúng quyết định của queue trong root tạm, 11 lệnh ghi, không lệnh nào cho job bị khóa |
| R2.2 | Nút vùng | [x] | [x] | 539e6a6: "Đây là tiêu đề/nội dung phim — giữ lại" / "Đây là logo thương hiệu — làm mờ" trên thẻ quảng cáo; thẻ an toàn có chúng trong "Chi tiết kỹ thuật" như trang cũ. Quyết định cho thẻ sở hữu vùng, kèm ghi chú của trang cũ; tiêu đề, ghi chú và nút đã chọn bằng `regionControlsHtml` trên mọi mục mẫu (verify-review). Body so với trang cũ thật ở bước 5–6 của browser-check; test Python gửi cả hai vào route thật **Máy thật** (2026-10-04, `d152ef3`, Windows, cùng bản xem thử): nút "Đây là tiêu đề/nội dung phim — giữ lại" trên thẻ Máu me (vùng của thẻ chữ đang làm mờ) → 1 POST cho thẻ sở hữu `{"id":"text-12-0006","decision":"KEEP","full_frame":false,"note":"Đã xác nhận vùng khoanh đỏ là tiêu đề hoặc nội dung hợp lệ của phim"}` (ghi chú giống trang cũ từng chữ), queue ghi KEEP kèm ghi chú. Sau đó vùng của thẻ Máu me chuyển sang thẻ logo đang làm mờ kế tiếp: đúng `regionOwner` của trang cũ (chỉ thẻ logo BLUR làm chủ vùng), không phải lỗi |
| R2.3 | Nhớ logo hãng phim / nền tảng | [x] | [x] | 539e6a6: "Đây là logo hãng phim — giữ & nhớ" khi `studioEligible`, "Đây là logo nền tảng — làm mờ & nhớ" khi `platformEligible`; đã nhớ thì nút thành "✓ Đã nhớ…" và bấm lại không gửi gì. Chữ ghi chú (so khớp, chữ sẽ nhớ, số khung, watermark) bằng `studioHtml` / `platformHtml` của trang cũ (11 biến thể × thẻ đủ điều kiện × watermark đã/chưa làm mờ). Cờ nhớ chỉ gửi khi là `true` (verify-review, browser-check, test Python) **Máy thật** (2026-10-04, `d152ef3`, Windows, cùng bản xem thử): "Đây là logo hãng phim — giữ & nhớ" trên thẻ kiểm tra đoạn mở đầu → `{"id":"visual_logo-12-0008","decision":"KEEP","full_frame":false,"note":null,"remember_studio_logo":true}`; server nhớ được (1 bản ghi `studio_logo` trong `state/studio-logo-memory.json` của root tạm; file của bản cài không đổi, sửa lần cuối 2026-10-03); nút thành "✓ Đã nhớ là logo hãng phim (giữ nguyên)", bấm lại không gửi gì. "Đây là logo nền tảng — làm mờ & nhớ" trên thẻ logo toàn khung → server 400; toast "Chưa lưu được lựa chọn … Chi tiết: Không thấy hình logo trên nền tối trong thẻ này để ghi nhớ logo nền tảng (no_frames); chưa ghi …" (chữ của server nguyên văn), gửi đúng 1 lần, queue của mục không đổi, thẻ về trạng thái đã lưu và được chọn lại |
| R2.4 | Hàng ghi tuần tự, thử lại, 403, đồng bộ lại | [x] | [!] | 539e6a6: `adapter.js` `review(id).write()`, một chuỗi tuần tự cho mỗi job, không dùng `once()`. verify-adapter thêm 6 test: 2 quyết định trên 2 thẻ → 2 POST đúng thứ tự, lệnh 2 chỉ đi khi lệnh 1 đã trả lời; thử lại sau 300 rồi 900 ms (đo bằng timer thật); 400 không gửi lại và giữ chữ của server; 403 → lấy phiên mới, gửi lại 1 lần; chuỗi thuộc job nên hộp mở lại thấy lệnh đang chờ và chờ nó xong. Hộp: "Đang lưu…/Đã lưu"; queue của server chỉ áp dụng cho lệnh cuối; không poll khi đang ghi (không có GET queue nào trong 3,6 s hai lệnh đang chờ) và không gọi `/api/status`. Lỗi cuối → toast "Chưa lưu được … (đã thử 3 lần) … Chi tiết: …", tải lại queue, chọn lại thẻ, chuyển "Tất cả" khi bộ lọc ẩn thẻ. Đóng hộp khi đang ghi → lệnh vẫn chạy, lỗi hiện ở toast của dashboard; mở lại → "Đang chờ lưu xong…" rồi queue của server; mở job khác → toast "#101 · …". Hộp xác nhận đang mở khi lệnh trước trả queue vẫn gửi đúng lựa chọn (test hỏng trên bản trước khi sửa). Mất kết nối → nút quyết định và Hoàn tác tắt kèm thông báo, bật lại khi queue trả lời **Máy thật** (2026-10-04, `d152ef3`, Windows, cùng bản xem thử): ghi chậm 2,5 s: bấm Giữ → "Đang lưu…"; đóng hộp ngay (về `#videos`) → lệnh vẫn tới server và được lưu; mở lại ngay → "Đang chờ lưu xong các lựa chọn trước…" rồi thẻ hiện "Giữ" đã chọn đúng queue. Lệnh ghi lỗi lúc mất kết nối (server xem thử đóng mọi kết nối) → thông báo "Mất kết nối với Control Center…", mọi nút quyết định và Hoàn tác tắt; có kết nối lại thì thông báo tắt và nút bật lại. Vẫn `[!]` vì R2-B1: thẻ vừa lỗi vẫn hiện quyết định chưa lưu sau khi có kết nối lại |
| R2.5 | Hoàn tác, tự chuyển cảnh, sticky | [x] | [x] | 539e6a6: "↶ Hoàn tác" và phím Z, tối đa 100 bước, tiêu đề nút như trang cũ, ứng viên phụ báo không hoàn tác được, xóa khi đổi lượt quét; verify-review: 130 thao tác ngẫu nhiên rồi 105 lần hoàn tác giống hệt `undo()` của trang cũ. "Tự chuyển cảnh" lưu `biliflow.review.autoNext` (chung với trang cũ): tắt thì không chuyển, vẫn tắt sau khi tải lại; bật thì tự chọn và cuộn tới thẻ chưa duyệt kế tiếp. Thẻ vừa duyệt vẫn ở "Chưa duyệt" (sticky); poll cũng giữ lại thẻ được duyệt ở nơi khác **Máy thật** (2026-10-04, `d152ef3`, Windows, cùng bản xem thử): phím 3 → Cắt, thẻ vẫn ở "Chưa duyệt" (sticky), tự chọn thẻ chưa duyệt kế tiếp; nút Hoàn tác có tiêu đề "Hoàn tác lựa chọn cho 18+ 0:57–1:01 (phím Z)"; Z → `{"id":"adult-12-0020"}` gửi tới `clear`, thẻ về chưa duyệt và được chọn lại, Hoàn tác tắt khi hết bước. Tắt "Tự chuyển cảnh" → `biliflow.review.autoNext` = `0`, tải lại trang vẫn tắt, quyết định xong thì thẻ đứng yên. Bấm lại nút đã chọn → gửi lại cùng body; "Xóa quyết định" → `clear {id}` |
| R2.6 | Phím tắt | [x] | [x] | 539e6a6: 1–4 cho thẻ đang chọn, ←/→ chọn thẻ trước/sau (dừng ở đầu/cuối, thẻ được cuộn vào tầm nhìn), Space phát/dừng, Z hoàn tác; bỏ qua khi đang gõ, khi giữ Ctrl/Alt/Meta, khi hộp xác nhận mở; phím giữ chỉ lặp ←/→ (như `onKeyDown`). verify-review so `keyDecision` (chỉ khác R2-K). browser-check: phím 1 + Enter, 2, ←/→, Z ở 6 lượt; phím 3 khi hộp xác nhận mở không gửi gì. Không thêm phím mới **Máy thật** (2026-10-04, `d152ef3`, Windows, cùng bản xem thử): phím thật 2, 3, Z, Esc, Enter, ←, Space chạy như mô tả ở R2.1 và R2.5; ← chọn thẻ trước và dừng ở thẻ đầu; Space phát video của thẻ đang chọn (đoạn 0:35–0:43, đang chạy ở 36,9 s), Space lần nữa thì dừng; cả hộp vẫn chỉ có một `<video>` |
| R2.7 | Khóa, S6, S8 | [x] | [x] | 539e6a6: khi khóa, mọi nút quyết định tắt và có "Chỉ xem · lý do": job 114 (chờ xuất), 113 (nguồn đã dọn), 110 (đang lưu trữ), 109 (đã Bỏ qua, S8); bấm phím không gửi gì, toast báo lý do. S6: job chờ quét không khóa, chờ xuất thì khóa. verify-review vẫn so `decisionsLocked` và liệt kê S5, S6, S8, R2-K **Máy thật** (2026-10-04, `d152ef3`, Windows, cùng bản xem thử): job đang xuất (RENDERING) có "Chỉ xem · Video đang chờ xuất hoặc đang xuất; hủy lệnh xuất trước khi đổi quyết định."; job đã Bỏ qua có "Chỉ xem · Video đã được đánh dấu bỏ qua (không xuất). Bấm “Mở lại để xuất” ở Dashboard để sửa."; 10/10 nút quyết định và Hoàn tác tắt; phím 1 chỉ hiện toast lý do, server không nhận lệnh nào cho hai job này |
| R1-B1 | Bộ nạp ảnh không mất chỗ khi một ô được `watch` lại lúc đang tải; `active()` về 0 | [x] | [x] | b8b458f: mỗi lần tải đã bắt đầu trả chỗ đúng một lần (listener riêng cho từng lần: load, error, hoặc khi chính `<img>` đó bắt đầu tải URL khác), kể cả khi mục đã bị hủy; `watch` lại cùng URL thì giữ lần tải đang chạy. verify-review: đúng ca được báo (`L.watch(a,u,10)` hai lần → `active()` 1 rồi 0, chỉ 1 request), URL khác khi đang tải, mục đã hủy (`forget`, `reset`), 9 ô có thử lại → tối đa 2, rồi về 0; test này hỏng trên bản cũ. browser-check-review-write: trên Chromium thật `active()` về 0, tối đa 2 ảnh cùng lúc (server giả cũng thấy ≤ 2) **Máy thật** (2026-10-04, `d152ef3`, Windows, cùng bản xem thử): trong hộp `BFReviewStats.maxImages` = 2 và `activeImages` = 0 sau khi tải xong 20 ảnh, không ô nào kẹt ở `.loading` (trước khi sửa: `activeImages` kẹt ở 1) |
| U-R2 | Người dùng thử trên job thật (8.3, bước 1–4) | — | [x] | **Máy thật** (2026-10-04, thư mục chính `536025d`, Control Center người dùng tự bật): người dùng thử bước 1–4 trên job thật và báo đạt (mở hộp mới bằng "Duyệt (bản mới, thử)", đúng tên video, số cảnh, ảnh; duyệt, hoàn tác, xóa quyết định, so với trang cũ; đóng rồi mở lại). Tìm được R2-B2 (nút vùng trên thẻ mượn khung đỏ "nhảy chung"), người dùng chọn cách sửa S9; dữ liệu của job đọc lại qua GET đúng ý người dùng. Sau đó người dùng xuất thử video bằng nút "Xuất video" của Dashboard V2 (nút xuất trong hộp mới còn tắt tới R3) |

**Máy thật tìm được sau R2 (giao cùng R3):**

- **R2-B1. Lệnh ghi lỗi lúc mất kết nối: có kết nối lại thì thẻ vẫn hiện quyết định chưa lưu.**
  - Tái hiện trên bản xem thử: server không trả lời → bấm phím 1 trên thẻ chưa duyệt → hộp áp dụng tại chỗ ("Giữ" được đánh dấu, số chưa duyệt giảm), lệnh ghi lỗi sau khi thử lại 300/900 ms, toast, tải lại queue cũng lỗi → server trả lời lại → poll chạy 3 lần, thông báo mất kết nối tắt, nút bật lại, nhưng thẻ vẫn "Giữ" và đầu hộp vẫn "Còn 1 mục chưa duyệt" trong khi server chưa có quyết định cho thẻ đó (còn 2). Chỉ đúng lại khi mở lại hộp, tải lại trang hoặc khi queue của server đổi vì lý do khác.
  - Nguyên nhân: `apply()` (không `force`) bỏ qua khi `version === s.version`, mà `s.version` là phiên bản của server ở lần áp dụng trước, không đổi theo lựa chọn tại chỗ. Trang cũ so `queueVersion(latest)` với `queueVersion(queue)` của queue tại chỗ (đã đổi `counts.pending` theo lựa chọn), nên lần poll đầu sau khi có kết nối áp dụng queue của server và thẻ trở về trạng thái đã lưu. `resync()` lỗi thì không còn gì đánh dấu là cần đồng bộ lại.
  - Sửa: như trang cũ, so phiên bản của server với `R.queueVersion(s.queue)` của queue tại chỗ; hoặc giữ cờ "cần đồng bộ" khi `resync()` lỗi để lần tải queue thành công kế tiếp áp dụng bắt buộc. Test: ghi lỗi mạng, tải lại queue cũng lỗi, có kết nối lại → poll kế tiếp đưa thẻ và số đếm về đúng queue của server.
  - Mức độ: chỉ sai hiển thị; server không ghi gì sai và lệnh xuất vẫn kiểm theo server, nhưng người dùng có thể tưởng thẻ đã lưu.
- **Ghi nhận thêm cho R4 (chưa sửa):** ở 375 px, hàng công cụ "Giữ tất cả / Dùng đề xuất / ↶ Hoàn tác" rộng hơn khung 21 px (cuộn ngang, không có dấu hiệu cuộn), giống hàng chip; vùng chạm dưới 44 px: nút tên thẻ (cao 21 px), nút vùng (38 px) (P17).
- **Ghi chú:** khi tab bị ẩn, hộp không poll (đúng thiết kế, như trang cũ), nên mất kết nối chỉ được báo khi một lệnh ghi lỗi hoặc khi tab hiện lại.

**Người dùng tìm được khi thử U-R2 (giao cùng R3):**

- **R2-B2. Nút vùng trên thẻ mượn khung đỏ "nhảy chung" với thẻ logo (người dùng chọn cách sửa S9).**
  - Người dùng thử job thật: thẻ "Kiểm tra đoạn kết" (48:34–48:40, không có vùng riêng) và thẻ "Logo nền tảng iQIYI" (48:35–48:39, vùng khoanh đỏ áp dụng 48:36.0–48:40.0) nằm cạnh nhau. Bấm "Đây là tiêu đề/nội dung phim — giữ lại" ở thẻ trái rồi "Đây là logo thương hiệu — làm mờ" ở thẻ phải → nút của cả hai thẻ cùng đổi theo, không chọn riêng được.
  - Không phải lỗi ghi: thẻ trái chỉ mượn vùng của thẻ iQIYI (`regionOwner`, giống trang cũ), nên nút vùng ở cả hai thẻ đều gửi quyết định cho thẻ iQIYI. Dữ liệu cuối cùng đúng ý người dùng (đoạn kết Giữ, logo iQIYI Làm mờ vùng). Ảnh của thẻ trái là khung 48:34.6, trước khi logo xuất hiện, nên khung đỏ trông như đè lên chữ tiêu đề phim dù vùng chỉ làm mờ từ 48:36.0.
  - Sửa theo S9 (mục 5): thẻ mượn vùng không có nút vùng (cả trong "Chi tiết kỹ thuật" của thẻ an toàn), có dòng ghi thẻ chủ, thời gian áp dụng, quyết định hiện tại và nút "Đi tới thẻ logo" (chọn và cuộn tới thẻ chủ, chuyển sang "Tất cả" nếu bộ lọc đang ẩn nó); khung đỏ trên thẻ mượn vẽ nét đứt kèm thời gian áp dụng. Thẻ sở hữu vùng giữ nguyên nút vùng. Test: verify-review liệt kê S9 là chỗ khác có chủ ý; thẻ mượn không có nút vùng, nút "Đi tới thẻ logo" chọn đúng thẻ chủ; browser-check cảnh đoạn kết + logo nền tảng cạnh nhau.

### 7.4. R3: hàng loạt và xuất video

Ghi chú cho cloud: trong lúc làm R3, máy thật đã đẩy một commit đổi cách che logo khi xuất (mục 11, dòng 2026-10-04 "Ngoài các đợt R"). Commit đó không đụng `dashboard_v2/`; cloud chỉ cần kéo về (merge, không rebase lại lịch sử đã đẩy) trước khi đẩy.

- **R3.1** "Giữ tất cả" / "Dùng đề xuất" cho các cảnh đang lọc, ánh xạ bộ lọc, xác nhận có số mục (S1), khóa hộp khi
  chạy (P11).
- **R3.2** Nút "Xuất video" trong hộp, luồng P12.
  - Mở hộp "Xuất video đã duyệt" của V2 chồng lên hộp duyệt, có thêm dòng tài nguyên.
  - Xếp lệnh xong thì đóng hộp duyệt.
- **R3.3** Giới hạn dung lượng và câu xác nhận của hộp xuất khớp `export_dialog.py` (test so chuỗi và giới hạn).
- **R3.4** S2 và S3.

| ID | Hạng mục | Cloud | Máy thật | Bằng chứng |
| --- | --- | --- | --- | --- |
| R3.1 | Hàng loạt | [x] | [ ] | bb763ad: "Giữ tất cả" / "Dùng đề xuất" cho các cảnh đang lọc, ánh xạ `bulkFilters` của trang cũ (Quảng cáo gửi 2 lệnh `visual_logo` rồi `text`; Visual AI và Ứng viên phụ báo "Bộ lọc này không hỗ trợ thao tác hàng loạt."), câu xác nhận của trang cũ trong hộp V2 (S5) với số mục server sẽ đổi (S1), chạy sau các lệnh ghi đang chờ (chuỗi ghi của job, 6.7), khóa hộp khi chạy (nút, phím, poll; "Đang áp dụng…"), không hoàn tác (P10). Body `{filter}`. verify-review: so `bulkKeep` / `bulkAccept` của trang cũ trên 6 queue giả × 11 bộ lọc × 2 lệnh: 81 ca giống hệt, 27 ca chỉ khác số mục (S1, câu và bộ lọc như nhau), 24 ca không hỗ trợ. Test Python: gửi bulk-keep / bulk-accept vào route thật trên root tạm, số mục server đổi (`changed_count`) bằng số trong câu xác nhận ở mọi bộ lọc có mục để đổi; server cũng từ chối `visual_ai`, `candidates`, `ads` (400). verify-adapter: bulk đi sau quyết định đang chờ. browser-check-review-bulk: body giống trang cũ thật; Esc chỉ hủy xác nhận; khi chạy mọi nút tắt và phím 1 không gửi; thẻ vừa giữ vẫn ở "Chưa duyệt"; Hoàn tác tắt; bấm "Giữ tất cả" ngay sau một quyết định chậm thì số mục không tính thẻ đó và lệnh bulk chờ quyết định xong (≥ 1,15 s); bulk bị từ chối 400 hiện chữ server và mở khóa hộp |
| R3.2 | Nút Xuất video và luồng xuất | [x] | [ ] | bb763ad: "Xuất video" trong hộp (P12): chờ các lệnh ghi xong; nếu lệnh ghi cuối lỗi thì tải lại queue đã lưu trước khi kiểm; chưa đủ quyết định → "Vẫn còn mục chưa có quyết định cuối cùng."; rồi mở hộp "Xuất video đã duyệt" của V2 chồng lên hộp duyệt, có dòng tài nguyên (Video nguồn, Ảnh và report, "Ổ đĩa còn trống" theo S4, Preview dự kiến) và trạng thái job mới nhất (một lần `/api/status` trước khi mở, vì poll dashboard đang dừng); finalize chỉ gửi khi bấm "Xác nhận xuất video", qua `exportModal` và `mutate('finalize')` hiện có, không tự thử lại; xếp lệnh xong → toast của dashboard, hộp duyệt đóng, về màn trước. Nút tắt khi chưa READY_FOR_EDIT_PLAN, khi khóa, mất kết nối hoặc đang chạy. browser-check-review-bulk ở 1440/1024/390 px × sáng/tối: Esc chỉ đóng hộp xuất (hộp duyệt vẫn mở), giới hạn sai bị từ chối không gửi, chỉ có finalize sau khi xác nhận (đếm cả bài: không finalize nào khác); lệnh ghi bị từ chối ngay trước khi bấm Xuất → báo cổng chặn, không mở hộp xuất. verify-adapter: finalize gửi 1 lần với 500, lỗi mạng, 409; chuỗi ghi của hộp duyệt từ chối finalize |
| R3.3 | Khớp `export_dialog.py` | [x] | [ ] | bb763ad: `contracts.js` có `EXPORT_SIZE_OPTIONS`, `EXPORT_GATE_MESSAGE`, thuộc tính ô GB, `exportConfirmText`, `exportPolicyChoice` như `EXPORT_DIALOG_JS`; hộp xuất (cả nút Xuất của Dashboard) hiện câu "Khóa các lựa chọn hiện tại và bắt đầu xuất video hoàn chỉnh (…)?" cập nhật theo lựa chọn, hoặc chữ lỗi giới hạn. Test Python chạy `export_dialog.EXPORT_DIALOG_JS` trong node và so với V2 trên 9 lựa chọn (biên 0,05 / 1000 / 0,04 / 1000,1 / rỗng): cùng chữ lỗi, cùng câu xác nhận, body chỉ có `size_mode`, `max_output_gb`; verify-review so thêm với bản nhúng trong trang cũ. Trang cũ gửi thêm `description` trong body finalize; V2 không gửi (B4), browser-check so body hai trang và ghi rõ điểm này |
| R3.4 | S2, S3 | [x] | [ ] | bb763ad: S2: chỉ còn "Cần xem thêm" thì dòng dưới tiến độ và tiêu đề nút Xuất báo "Còn N mục Cần xem thêm — chọn quyết định cuối trước khi xuất.". S3: trạng thái xuất và tài nguyên tải lại một lần 1,5 s sau lệnh ghi cuối (browser-check: 2 quyết định → đúng 1 GET export và 1 GET resources, khoảng 1,5 s sau). Dòng trạng thái xuất như trang cũ (đã xếp hàng, đang render, hoàn tất, thất bại, đã bỏ qua, nguồn đã dọn hoặc đang lưu trữ); khi đang chờ hoặc đang xuất thì tải lại ở mỗi lần poll (≥ 2 lần trong 6,5 s) và chuyển sang "Hoàn tất: …" khi xong. verify-review liệt kê S2, S3, S4 |
| R2-B1 | Lệnh ghi lỗi lúc mất kết nối: có kết nối lại thì thẻ và số đếm về đúng queue của server | [x] | [ ] | 108f27b: lựa chọn tại chỗ đánh dấu hộp là chưa đồng bộ; lần tải queue thành công kế tiếp áp dụng bắt buộc dù server cùng phiên bản (cờ được xóa khi áp dụng queue của server). browser-check-review-write: chặn mọi request của job (lệnh ghi thử lại 300/900 ms rồi lỗi, tải lại queue cũng lỗi, hiện mất kết nối) → bỏ chặn → poll kế tiếp đưa thẻ về "Chưa duyệt" và tiến độ về "5 / 30"; server không ghi gì. Test này hỏng khi bỏ phần sửa |
| R2-B2 | S9: thẻ mượn khung đỏ không có nút vùng, có dòng ghi thẻ chủ và nút "Đi tới thẻ logo"; khung đỏ nét đứt kèm thời gian áp dụng | [x] | [ ] | 108f27b: thẻ mượn vùng (`regionOwner` khác chính thẻ) không có 2 nút vùng, kể cả trong "Chi tiết kỹ thuật" của thẻ an toàn; thay bằng "Khung đỏ là vùng logo của thẻ “…” (…–…) — đang: …" và "Đi tới thẻ logo" (chọn, cuộn tới thẻ chủ, chuyển "Tất cả" nếu bộ lọc ẩn nó; không gửi gì); khung đỏ nét đứt kèm "áp dụng …–…". Thẻ sở hữu vùng giữ nút; payload không đổi; khung đỏ được vá lại khi thẻ chủ đổi. verify-review dựng thẻ thật (`review-cards.js`) và liệt kê S9 (trang cũ hiện nút trên cả hai thẻ). browser-check-review-write ở 1440 px sáng và 390 px tối: "Kiểm tra đoạn kết" cạnh "Logo nền tảng Nền tảng mẫu": thẻ trái không có nút vùng, khung nét đứt, thẻ phải nét liền có 2 nút; nút của thẻ chủ chỉ đổi thẻ chủ (bỏ làm mờ thì thẻ trái hết khung và dòng, làm mờ lại thì có lại); "Đi tới thẻ logo" từ "Chưa duyệt" chuyển sang "Tất cả" và chọn thẻ chủ. Hệ quả: lỗi "chưa có vùng được định vị" của trang cũ không còn đường bấm nào trong V2 (vẫn so logic với trang cũ ở verify-review và test Python) |

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
| 2026-10-04 | 108f27b, bb763ad, (commit docs này) | Cloud sửa R2-B1 (cờ chưa đồng bộ: lần tải queue thành công kế tiếp áp dụng bắt buộc) và R2-B2 theo S9 (thẻ mượn khung đỏ: dòng thẻ chủ, "Đi tới thẻ logo", khung nét đứt), rồi làm R3 (mục 7.4): "Giữ tất cả" / "Dùng đề xuất" (S1, chờ lệnh ghi, khóa hộp, không hoàn tác), nút "Xuất video" trong hộp với hộp xuất V2 chồng lên và dòng tài nguyên (S4), câu và giới hạn của `export_dialog.py` (cả nút Xuất của Dashboard), S2, S3 và dòng trạng thái xuất. Không route mới, không file phục vụ mới, `PHONE_ALLOWED_POSTS` và endpoint của `contracts.js` không đổi (chỉ thêm hàm chữ của hộp xuất), CSP không đổi; finalize chỉ gửi khi bấm "Xác nhận xuất video", không tự thử lại. Test mới: `browser-check-review-bulk.cjs`; server giả có bulk, finalize và đồng bộ `review_summary`; lớp `ReviewR3Bulk` và `ReviewR3ExportDialog` trong `tests/test_dashboard_v2_review.py`. Trước khi đẩy, merge `0334f8c` của máy thật (cách che logo khi xuất; merge, không rebase) rồi chạy lại các test. Tự review bảo mật: chuỗi qua `esc()`, body bulk `{filter}` và finalize `{size_mode, max_output_gb?}`, không `confirm()`/`alert()`, không `blob:`, chỉ `adapter.js` gọi `fetch`, không route dọn/lưu trữ/Thùng rác | `node --check` mọi file; verify 29/29; verify-adapter 29/29; verify-review 28/28 (bulk 81 ca giống trang cũ + 27 ca S1); browser-check 20/20; browser-check-review 26/26; browser-check-review-write 19/19 (thêm R2-B1, S9); browser-check-review-bulk 12/12 (body bulk và finalize so với trang cũ thật; 1440/1024/390 px × sáng/tối; không finalize nào chưa xác nhận); test_dashboard_v2_* 83 OK (review 10); test Control Center liên quan như trước (test_control_center: 1 lỗi cũ thiếu PowerShell; test_skip_export: 4 lỗi cũ thiếu PowerShell; review_workflow, export_dialog, logo_memory_admin, source_cleanup_http, source_archive_http, export_identity, golden_label_app OK); A4 10 stage `none`; D2 trùng byte | Máy thật kiểm R3 (mục 8.2: bản xem thử cho phép ghi chỉ vào root tạm, finalize chỉ vào root tạm hoặc chặn); R4 khi được giao |
| 2026-10-04 | 0334f8c | Ngoài các đợt R: người dùng thấy logo nền tảng đã làm mờ vẫn đọc được, so sáu cách che trên khung thật và chọn "F. Xóa logo rồi mờ", làm ở máy thật. Bộ xuất thêm cách che `delogo_blur_v1`: lấp vùng bằng hình quanh nó (FFmpeg `delogo`) rồi làm mờ theo cỡ vùng; mọi BLUR có vùng của plan xuất mới dùng cách này; tên file xuất không đổi (xem CHANGELOG). Chỉ đổi `blur_filter.py`, `final_renderer.py`, `review_workflow.py` và test, không đụng `dashboard_v2/` | Test liên quan 140 OK; full suite 1288 OK (25 bỏ qua); xuất thử hai đoạn của một job thật (chỉ đọc nguồn): vùng logo sạch ở mọi khung, bản cũ còn thấy chữ | Người dùng xem ảnh so sánh; muốn video đã xuất có cách che mới thì tự chuyển file xuất cũ vào Thùng rác rồi xuất lại, sau khi thư mục chính chuyển sang commit này |
| 2026-10-04 | 536025d (thư mục chính) | Người dùng thử xong U-R2 (mục 8.3 bước 1–4) trên job thật, rồi xuất thử video bằng nút "Xuất video" của Dashboard V2 | U-R2 đạt; lỗi duy nhất là R2-B2 (đã ghi, cách sửa S9) | Cloud làm R3 kèm R2-B1 và R2-B2; sau R3 máy thật kiểm, người dùng thử bước 5–6 khi tới R4 (mục 8.3) |
| 2026-10-04 | 536025d (thư mục chính) | Người dùng đồng ý chuyển thư mục chính sang `536025d` rồi tự bật Control Center; bắt đầu thử U-R2 trên job thật. Nút "Duyệt cảnh" vẫn mở trang cũ (đúng kế hoạch, R4.3 mới chuyển); hộp mới mở bằng "Duyệt (bản mới, thử)" trong bảng chi tiết | Hộp mở đúng tên video, số cảnh, ảnh; tìm được R2-B2 (nút vùng trên thẻ mượn khung đỏ "nhảy chung"); người dùng chọn cách sửa S9; dữ liệu của job đúng ý người dùng | Người dùng thử tiếp U-R2 bước 2–4; cloud sửa R2-B1 và R2-B2 cùng R3 |
| 2026-10-04 | d152ef3 (R1-B1, R2) | Máy thật kéo về và kiểm nhanh: diff chỉ đổi `dashboard_v2/`, test và plan (`control_center.py` và `DASHBOARD_V2_FILES` không đổi), không có dòng attribution; full suite; `node --check` 18 file; `verify.cjs`, `verify-adapter.cjs` 5 lần lúc máy bận vì full suite, `verify-review.cjs`; A4; D2 (test trong full suite). Bản xem thử có ghi trên handler thật (chỉ decision/clear vào queue của root tạm, POST khác 403) với clip ffmpeg, queue giả, job đang xuất và job đã Bỏ qua, công tắc mất kết nối và ghi chậm; thử ở 1440/1024/375 px, sáng và tối; xong thì tắt và xóa root tạm. Các browser-check cần Playwright nên dựa vào cloud. Thư mục chính vẫn ở `652f207`; Control Center đang tắt | 1278 OK (25 bỏ qua); 29/29; 27/27 ×5; 24/24; A4 `none`; R1-B1, R2.1–R2.3 và R2.5–R2.7 đạt; 11 lệnh ghi đúng dạng trang cũ, 24/24 thẻ khớp queue; tìm được R2-B1 (R2.4 `[!]`) | Cloud sửa R2-B1 cùng R3; người dùng thử U-R2 (mục 8.3 bước 1–4): cần chuyển thư mục chính và chạy Control Center bản mới, chờ người dùng đồng ý |
| 2026-10-04 | b8b458f, 539e6a6, (commit docs này) | Cloud sửa R1-B1 rồi làm R2 (mục 7.3): quyết định trong thẻ, nút vùng, nhớ logo, minh họa trên ảnh, hộp xác nhận V2 (S5), hàng ghi tuần tự theo job trong adapter, "Đang lưu…/Đã lưu", đồng bộ lại khi lỗi, hoàn tác, "Tự chuyển cảnh", sticky, phím tắt, khóa và mất kết nối; demo store ghi trong bộ nhớ. Không route mới, không file phục vụ mới (`DASHBOARD_V2_FILES` giữ nguyên), `PHONE_ALLOWED_POSTS` và endpoint của `contracts.js` không đổi, CSP không đổi. Test mới: `browser-check-review-write.cjs` và `review-fake-server.cjs` (server giả dùng chung với `browser-check-review.cjs`, có trang cũ thật ở `/classic/<id>`), lớp `ReviewR2Writes` trong `tests/test_dashboard_v2_review.py`. Tự review bảo mật: chuỗi queue qua `esc()`, body chỉ có các trường của trang cũ, cờ nhớ chỉ khi `true`, không `confirm()`/`alert()`, không `blob:`, chỉ `adapter.js` gọi `fetch` | `node --check` mọi file; verify 29/29; verify-adapter 27/27; verify-review 24/24 (2820 ca quyết định so với trang cũ); browser-check 20/20; browser-check-review 26/26; browser-check-review-write 17/17 (14 body giống trang cũ thật; 1440/1024/390 px × sáng/tối); test_dashboard_v2_* 81 OK (review 8 gồm test route thật); test Control Center liên quan như trước (test_control_center: 1 lỗi cũ thiếu PowerShell; test_skip_export 26/30: lỗi thiếu PowerShell cũ; review_workflow, export_dialog, logo_memory_admin, source_cleanup_http, source_archive_http, export_identity, golden_label_app OK); A4 10 stage `none`; D2 trùng byte | Máy thật kiểm R2 (mục 8.2: bản xem thử cho phép ghi nhưng chỉ vào root tạm); sau đó người dùng thử U-R2 (mục 8.3 bước 1–4), cần khởi động lại Control Center |
| 2026-10-04 | ea2adcc (R1, R0-T1) | Máy thật kéo về và kiểm nhanh: đọc diff (`control_center.py` chỉ thêm `review-detail.js` vào whitelist), full suite, `node --check`, `verify.cjs`, `verify-adapter.cjs` 8 lần lúc máy bận, `verify-review.cjs`, A4. Dựng bản xem thử trên handler thật với clip ffmpeg và queue giả trong root tạm; thử ở 1440/1024/375 px, sáng và tối; xong thì tắt và xóa root tạm. Thư mục chính vẫn ở `652f207` | 1277 OK; 28/28; 21/21 ×8; 17/17; A4 `none`; R0-T1 và R1.1–R1.6 đạt; tìm được R1-B1 | Cloud làm R2 kèm R1-B1; sau R2 người dùng thử (U-R2), cần khởi động lại Control Center |
| 2026-10-04 | fb6b273, 1191690, (commit docs này) | Cloud sửa R0-T1 (chỉ test) rồi làm R1 (mục 7.2), vẫn chỉ xem: video dùng chung trong thẻ, timeline và tua, dải ≤ 8 khung, bằng chứng (ảnh cắt 360 px, khung vàng, chú thích, "Chi tiết kỹ thuật"), phóng to thẻ, lỗi video và khóa media mới, nguồn đã dọn/lưu trữ chỉ hiện ảnh, nhả video. File mới `review-detail.js` (trong whitelist, `live.html`, `index.html`, `serve.py`). Bằng chứng giả trong `mock-data.js`. browser-check-review dùng clip VP8 do ffmpeg tạo trong `temp/` (máy không có ffmpeg thì bỏ qua phần phát). Tự review bảo mật: chuỗi queue/bằng chứng qua `esc()`, URL do adapter dựng và mã hóa, không `blob:`, không `toDataURL`, không lệnh ghi | `node --check` mọi file; verify 28/28; verify-adapter 21/21; verify-review 17/17 (thêm 5 test R1 so với hàm trang cũ); browser-check 20/20; browser-check-review 26/26 ba lần liên tiếp (1440/1024/390 px × sáng/tối); test_dashboard_v2_* 80 OK (review 7 gồm file mới); test Control Center liên quan như trước (test_control_center 52/53, test_skip_export 26/30: lỗi thiếu PowerShell cũ; còn lại OK); A4 10 stage `none`; D2 trùng byte | Máy thật kiểm R1 (mục 8.2, bản xem thử với video tổng hợp trong `temp`); cloud làm R2 khi được giao |
| 2026-10-04 | 7d7bc7b (R0) | Máy thật kéo R0 về và kiểm nhanh: đọc diff, full suite, `node --check`, `verify.cjs`, `verify-adapter.cjs`, `verify-review.cjs`, A4, xem hộp mới trên bản demo `serve.py` ở 1440/1024/375 px. Thư mục chính vẫn ở `652f207`: R0 đổi `control_center.py`, nên chỉ chuyển cùng lúc khởi động lại Control Center | 1277 OK; 28/28; 12/12; A4 `none`; R0.1–R0.4 và R0.6 đạt; R0.5 `[!]` vì test chập chờn (R0-T1) | Cloud làm R1 kèm R0-T1 |
| 2026-10-04 | b853963, 0e43fa1, (commit docs này) | Cloud làm R0 (mục 7.1): áp patch M4 rồi xóa file patch; hộp `#review-dialog` chỉ xem với địa chỉ `#review/<id>/<view>`, nút tạm "Duyệt (bản mới, thử)" trong drawer; `review-core.js`, `review-cards.js`, `review-media.js`, `review.js`, `review.css`; adapter GET/URL/query, pause/resume poll; demo store và queue giả; whitelist. Test mới: `verify-review.cjs`, `browser-check-review.cjs` (file riêng để `browser-check.cjs` dưới 800 dòng), `tests/test_dashboard_v2_review.py`. Tự review bảo mật: chuỗi queue qua `esc()`, hash chỉ nhận số và màn cho phép, hộp không gửi lệnh ghi | `node --check` mọi file; verify 28/28 (15 job); verify-adapter 21/21; verify-review 12/12; browser-check 20/20; browser-check-review 14/14 (1440/1024/390 px × sáng/tối, 500 mục); test_dashboard_v2_* 80 OK (contract 10, frontend 3, route 9 gồm D2, status 2, phone 15, hardening 34, review 7); test Control Center liên quan như trước (test_control_center 52/53, test_skip_export 26/30: lỗi thiếu PowerShell cũ; source_cleanup_http, source_archive_http, export_identity, export_dialog, logo_memory_admin, review_workflow, golden_label_app OK); A4 10 stage `none` | Máy thật kiểm R0 (mục 8.2, bản xem thử với root tạm); cloud làm R1 khi được giao |
| 2026-10-04 | (commit này) | Người dùng quyết định làm trang duyệt ngay, chưa merge; E6, E7 của Dashboard V2 test chung khi thử trang duyệt mới. Máy thật kiểm patch M4 trên file đã commit ở đầu nhánh: áp sạch (`git show HEAD:docs/patches/M4-review-back-to-v2.patch` rồi `git apply --check --cached`). Trên Windows, file patch checkout ra CRLF nên `git apply --check` trên thư mục làm việc báo lỗi; đó không phải lỗi của patch | Sẵn sàng giao R0 trên `feat/dashboard-v2` | Cloud làm R0 |
| 2026-10-04 | (commit này) | Người dùng nhắc V2 đã có UI duyệt; máy thật mở bản demo (`serve.py`, chỉ file tĩnh) xem hộp "Duyệt cảnh" ở 1440 px và 375 px, rồi viết lại plan dựa trên hộp đó. Ghi các câu trả lời Q1–Q7 | Plan theo hộp "Duyệt cảnh" của bản mẫu | Merge `feat/dashboard-v2` khi người dùng test xong; giao R0 |
| 2026-10-04 | (chưa commit) | Máy thật soạn bản đầu của plan và inventory từ 2 lượt đọc code chỉ đọc ở 18d3c18, kiểm lại số dòng phần điện thoại ở c91746f; thêm patch M4 của cloud (fbe0f89) vào R0 | Bản nháp, bố cục mới; người dùng không đồng ý vì V2 đã có UI duyệt | Viết lại theo hộp "Duyệt cảnh" (dòng trên) |
