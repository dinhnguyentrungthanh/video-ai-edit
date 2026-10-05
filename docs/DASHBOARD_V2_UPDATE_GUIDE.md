# Dashboard V2 — hướng dẫn mapping, tích hợp và rollback

Ngày: 2026-10-03. Đối chiếu ban đầu với Control Center 0.7.24, commit `23aa1e4`; **đã đối chiếu lại với code hiện tại (`f6996bb`, gồm merge 2a37496/732b02b) ở mục 8**. Khi mục 8 khác phần cũ của guide thì theo mục 8.

## 1. Trạng thái bàn giao

**Cập nhật 2026-10-03 (cloud, Pha 0–3):** đã có adapter, bản live `live.html` và route xem thử `/dashboard-v2/`, test bằng transport/API giả và handler thật với dữ liệu tạm. **Chưa test trên Control Center và dữ liệu thật của máy người dùng, nên chưa được coi là đã tích hợp.** Bản demo độc lập bên dưới vẫn giữ nguyên.

**Bản demo: prototype độc lập, có tương tác bằng dữ liệu mẫu.**

- Mã demo: `E:\DungChung\BiliFlow\dashboard_v2\`.
- Demo: `http://127.0.0.1:8794/`. Dashboard thật vẫn ở cổng riêng `8765`.
- `index.html` khóa kết nối bằng `connect-src 'none'`; JS không có fetch, XHR, WebSocket hay bridge đến API thật.
- `serve.py` chỉ phục vụ danh sách asset cho phép. POST trả 405; đường dẫn API / file ngoài demo trả 404.
- Dữ liệu được tạo trong `mock-data.js`, giữ trong bộ nhớ tab. Reload đặt lại. Không đọc input, report, queue, state hay bộ nhớ logo thật.
- Các hình SVG tự tạo và cảnh review chỉ là minh họa. Không chạy model, giải mã video, xuất preview hoặc render thật.
- Chỉ mở demo để đánh giá thiết kế ở giai đoạn này. Việc tích hợp là bước sau, cần người dùng yêu cầu.

### Điều chỉnh theo phản hồi giao diện

- Nền sáng, điểm nhấn xanh dương, chữ/nút lớn hơn. `theme.css` phải được phục vụ và tải sau `styles.css`; server demo đã thêm file này vào danh sách cho phép.
- Chi tiết ưu tiên tiến độ và tối đa hai thao tác chính. **Thao tác khác** chứa toàn bộ thao tác còn lại từ `BFContracts.operations`; không bỏ endpoint, đổi payload hoặc nới điều kiện khóa. Xuất đứng trước Duyệt cảnh ở video sẵn sàng xuất.
- **Kết quả kiểm tra & xuất video** chứa audit/xuất; **Video gốc & thông tin kỹ thuật** chứa đường dẫn và SHA-256. Tên hiển thị của bước/profile/nội dung được dịch, giá trị backend giữ nguyên.
- Nền phía sau Chi tiết giữ độ trong suốt khi rê chuột. CSS phải áp dụng cả `.drawer-backdrop:hover:not(:disabled)` để tránh rule hover của button làm nền thành màu đặc. Phím Tab bỏ qua nút nằm trong mục đang gấp.
- Khi kiểm thử tích hợp: mở Đang xử lý và rê chuột lên nền; mở Thao tác khác và kiểm tra xác nhận Hủy; kiểm tra Xuất/Duyệt vẫn có đủ điều kiện khóa; kiểm tra màn hình 375 px có thẻ tiến độ/tài nguyên xếp dọc và bảng chi tiết không tràn ngang.
- Kết quả prototype sau điều chỉnh: 9 kiểm tra Browser, 18 kiểm tra contract, source production 64/64 hash không đổi. Evidence nằm ở `temp/dashboard-v2-evidence/light-*`.

### Tệp và trách nhiệm

| Tệp | Trách nhiệm | Khi tích hợp |
| --- | --- | --- |
| `index.html` | Khung trang, sidebar, modal, CSP demo | Phục vụ từ route riêng; sửa CSP có chủ đích |
| `styles.css` | Bố cục, responsive, focus | Giữ độc lập với CSS dashboard và review cũ |
| `theme.css` | Theme sáng/tối, cỡ chữ, màu trạng thái, bố cục chi tiết rút gọn | Tải **sau** styles.css trên route V2; không nạp vào dashboard/review cũ |
| `contracts.js` | Danh mục endpoint, nhóm trạng thái, payload quét / xuất, điều kiện UI | Đối chiếu lại với backend tại thời điểm tích hợp |
| `mock-data.js` | Fixture tổng hợp | Không đưa vào build kết nối dữ liệu thật |
| `download-demo.js` | Hai nguồn mẫu, kiểm tra batch, state machine lượt tải và giới hạn đồng thời | Chỉ là UI; không thể coi là adapter tải thật hoặc chứng nhận tên miền được hỗ trợ |
| `app.js` | Các view và logic thay đổi fixture | Tách lớp dữ liệu demo khỏi presenter; thay bằng adapter đã kiểm thử |
| `serve.py`, `Start-Demo.cmd` | Server demo static | Không dùng làm proxy hoặc server production |
| `verify.cjs` | Kiểm tra hợp đồng, khóa quan trọng và không có transport | Giữ như gate cho prototype; bổ sung gate adapter riêng |
| `demo-store.js` | (Pha 2) DemoStore: fixture + mutation trong bộ nhớ, cùng giao diện với live store | Chỉ nạp ở `index.html` |
| `adapter.js` | (Pha 2) ControlCenterAdapter + live store + chuẩn hóa snapshot; nơi duy nhất có HTTP | Chỉ nạp ở `live.html` |
| `live.html` | (Pha 2) Trang live, `connect-src 'self'`, `data-mode="live"` | Phục vụ ở `/dashboard-v2/` (Pha 3) |
| `verify-adapter.cjs`, `browser-check.cjs` | (Pha 2) Gate adapter bằng transport giả; kiểm tra trình duyệt tùy chọn với API giả | Chạy trước khi đổi adapter/presenter |

## 2. Mapping giao diện

### Tổng quan và nhóm danh sách

Các ô tổng quan **đếm video**, riêng ghi chú “cảnh cần quyết định” mới đếm cảnh. Mỗi video có một trạng thái; các ô đang chạy không cộng video đang chờ đến lượt. Bấm ô xóa từ khóa tìm kiếm cũ, lọc đúng tập được đếm và hiển thị nhãn “Đang lọc”. Bấm tab danh sách hoặc “Xem tất cả” để thoát bộ lọc này.

| Ô tổng quan | Trạng thái được đếm / lọc | Bộ lọc UI |
| --- | --- | --- |
| Đang quét cảnh | Các trạng thái quét/chuẩn bị/khoanh vùng/tạo review/AI audit từ `BFContracts.tab(job) === 'scanning'` | `scan_active` |
| Chờ bạn duyệt | WAITING_REVIEW | `review_pending` |
| Sẵn sàng xuất | READY_TO_EXPORT; nút xuất vẫn phải qua các gate source/queue/render hiện có | `review_ready` |
| Đang xuất video | RENDERING hoặc VERIFYING | `export_active` |
| Hoàn tất | COMPLETED hoặc SKIPPED; ghi chú phân biệt đã xuất và bỏ qua | `completed` hiện có |

- `QUEUED` + `queue_kind=scan/export` chỉ vào số **chờ quét/chờ xuất**, không vào hai ô đang chạy. Unknown queue kind không được đoán là scan hoặc export.
- Danh sách vẫn giữ nguyên sáu bucket backend và tab Tất cả. Chỉ đổi nhãn `review` thành **Cần duyệt / đã duyệt** (gộp WAITING_REVIEW và READY_TO_EXPORT), `export` thành **Chờ / đang xuất** (gộp queued export và đang render/verify), để số tổng hợp không bị hiểu nhầm là số đang chạy.
- Bốn ID UI mới chỉ lọc trên frontend qua `BFContracts.overviewMatch`; không phải state backend hay endpoint mới. Không gửi chúng trong payload quét/xuất.
- Thẻ tiến trình chính ghi **ĐANG QUÉT CẢNH** hoặc **ĐANG XUẤT VIDEO**, tên bước thật; VERIFYING hiện “Kiểm tra bản xuất”, không bị gộp vào quét cảnh.

### Chuyển sáng/tối

- Nút ở topbar đặt `data-theme=light/dark` trên phần tử html; cả hai chế độ dùng cùng DOM và cùng điều kiện thao tác. Chế độ tối giữ cỡ chữ/khoảng cách của bản sáng.
- `app.js` chỉ lưu sở thích giao diện ở key `biliflow-v2-theme` trong localStorage. Không lưu dữ liệu video, quyết định review hoặc token vào key này. Nếu trình duyệt chặn storage, nút vẫn hoạt động trong tab; tải lại trở về sáng.
- Tải lại nhớ theme; đặt lại dữ liệu mẫu chỉ reset fixture. Khi tích hợp, giữ key tách biệt và thứ tự stylesheet. Không gọi API hay khởi động worker khi chuyển theme.
- Kiểm tra cả topbar, danh sách, bảng chi tiết, backdrop hover và hộp xác nhận trong hai theme; màn hình 375 px phải bấm được nút mà không tràn ngang.

### Trang Tải video — yêu cầu mới, chỉ làm mẫu

- Route `#downloads`, mục **Tải video** trong sidebar. Form gồm nguồn tải, textarea nhiều link (mỗi dòng một link cùng nguồn), nút thêm và nút thử 3 video mẫu. Danh sách bên cạnh có bộ đếm, bộ lọc và tiến độ riêng từng video; có thể thêm nguồn khác khi lượt trước vẫn đang chạy. Thư mục đích dự kiến `E:\DungChung\BiliFlow\input\`.
- Hai lựa chọn theo yêu cầu người dùng: YouTube và Phimmoi (mẫu). `phimmoi.example` là địa chỉ minh họa, **không phải tên miền Phimmoi thật**. Thêm tùy chọn trong combobox không có nghĩa hệ thống hỗ trợ tải trang đó.
- `downloadQueue` và `downloadDraft` giữ riêng trong bộ nhớ tab. Mỗi lượt có `id`, `domain`, `url`, `name`, `state`, `progress`, `totalBytes`, `logs`, `error`. QUEUED → DOWNLOADING → VERIFYING → COMPLETED; tạm dừng riêng → PAUSED; hủy → CANCELLED; giả lập lỗi → FAILED; thử lại giữ ID, đặt tiến độ về 0 và xếp cuối hàng đợi. Resume riêng chỉ bật khi có slot. `download-demo.js` chứa state machine thuần, `app.js` dùng timer mô phỏng 1 giây, 320 MB/lượt. Không gửi HTTP, tạo MP4, thêm job quét hay đổi hàng đợi GPU.
- Tải đồng thời chọn 1/2/3 (mặc định 2), độc lập với quy tắc một GPU của quét/xuất. Hạ giới hạn không ngắt các lượt đang chạy, chỉ hạn chế lượt mới. Tạm dừng tất cả giữ tiến độ và ngừng nhận lượt mới; tiếp tục khôi phục. Giới hạn demo 20 link/lần, 100 lượt/tab; batch có link sai/trùng bị từ chối toàn bộ, không thêm một phần. Log đang mở, vị trí cuộn và ô nhập được giữ qua cập nhật tiến độ. Dữ liệu giữ khi đổi trang; reload hoặc Đặt lại dữ liệu mẫu xóa danh sách.
- Kiểm tra frontend: HTTPS, host đúng nguồn đã chọn (YouTube có alias youtu.be), link video cụ thể, không tài khoản/cổng tùy chỉnh. Đây chỉ là validation form; **không thay thế kiểm tra URL ở backend** khi có tải thật.
- Trang này là bổ sung mới, **chưa có mapping đến API tải video hiện tại được xác nhận**. Không thêm endpoint tưởng tượng vào `BFContracts.endpoints`, không trỏ nút tải thử vào API thật.
- Khi người dùng yêu cầu tích hợp tải thật: xác định downloader/giấy phép/nguồn được hỗ trợ và tên miền thật trước; bỏ timer mô phỏng. Chốt contract tạo tác vụ + đọc tiến độ + dừng + lỗi với adapter riêng, gồm ID ổn định, trạng thái, downloaded/total bytes và đường dẫn kết quả. Ghi `.part` dưới ổ E; chỉ công bố file hoàn chỉnh vào input sau khi kiểm tra, không ghi đè file có sẵn. Watcher nhập video mới theo cơ chế hiện có; quét/duyệt/xuất vẫn cần các bước và xác nhận hiện có.
- Backend tải thật phải kiểm tra lại host, redirect, địa chỉ nội bộ và phạm vi đường dẫn đầu ra; không truyền URL người dùng qua câu lệnh shell ghép chuỗi. Những yêu cầu này là gate cho bước tích hợp, chưa phải chức năng của prototype.

#### Adapter command / PowerShell trong bước tích hợp sau

1. Browser chỉ tạo yêu cầu tải có cấu trúc. Backend dùng ID tác vụ ổn định để quản lý từng tiến trình. Không đưa ô terminal hoặc cho người dùng nhập câu lệnh trực tiếp trong UI. Đây là thiết kế tương lai, chưa có endpoint downloader đã xác nhận.
2. Backend chạy executable/script được cấu hình với danh sách đối số; giữ URL là một đối số dữ liệu, không ghép thành `cmd /c`, `Invoke-Expression` hoặc chuỗi `-Command`. Nếu dùng script PowerShell, script có tham số rõ ràng và được gọi ẩn, không bật cửa sổ terminal cho mỗi video.
3. Ưu tiên output tiến độ JSON Lines có `task_id`, `attempt_id`, `sequence`, `stage`, `downloaded_bytes`, `total_bytes`, `speed_bytes_per_second`, `eta_seconds`, `message`, `exit_code`. Nếu downloader chỉ có stdout/stderr dạng text, adapter backend phân tích thành schema này; UI không phụ thuộc định dạng dòng lệnh. Dữ liệu log chỉ hiển thị dạng text và phải che cookie/token/header bí mật.
4. Khi chưa biết total bytes, dùng thanh tiến độ không xác định và hiển thị số bytes đã tải; không bịa % hoặc ETA. Tiến độ tải phải tách với bước kiểm tra tệp. Chỉ COMPLETED khi tiến trình trả thành công và tệp được kiểm tra/công bố vào input; 100% tải không đồng nghĩa video đã vào input. Lỗi hiển thị theo từng lượt, không ngắt cả danh sách.
5. Contract backend cần snapshot danh sách và event tiến độ có thứ tự, retry/cancel idempotent, trạng thái persist trên E, phục hồi sau reload/backend restart và kết quả tải có đường dẫn E đã kiểm tra. Bỏ event cũ từ attempt trước khi retry; giữ ID và phiên thử để tránh tiến trình cũ ghi đè tiến độ mới.
6. Nút hủy thật dùng CANCELLING cho đến khi backend xác nhận cả cây tiến trình đã dừng, rồi mới CANCELLED; không chỉ đổi badge ở frontend. Tạm dừng/resume chỉ bật khi downloader hỗ trợ và backend xác nhận. Nếu không hỗ trợ, dùng dừng nhận lượt mới/hủy/thử lại và giải thích rõ; không giả lập khả năng resume của công cụ thật.
7. Giới hạn concurrent download cần do backend thực thi, tách worker quét/xuất, giới hạn tài nguyên thực tế và giữ FIFO. Tệp `.part`, log, state đều trên E; không ghi đè video đã có. Chỉ đưa tệp hoàn chỉnh vào input sau kiểm tra. Không tự bắt đầu quét hoặc xuất từ event tải hoàn tất; giữ các gate người dùng hiện có.

### Cập nhật khi chuyển từ quét sang xuất

- Trong prototype, mỗi lần `state.jobs` hoặc `state.active` đổi và `render()` chạy, các ô tổng quan/danh sách/thẻ tiến trình được tính lại. Tình huống **Đang kiểm tra bản xuất** trong Cài đặt đã được kiểm tra: quét giảm về 0, xuất tăng lên 1, thẻ chính đổi thành ĐANG XUẤT VIDEO với bước Kiểm tra bản xuất.
- Prototype không theo dõi worker thật. Khi tích hợp, adapter phải nhận snapshot mới từ GET `/api/status` (danh sách job lấy từ `jobs[]` của nó; `/api/jobs` thiếu trường, xem mục 8.1 #1) theo cơ chế refresh hiện có, cập nhật cả jobs và active rồi render từ cùng snapshot. Không tăng/giảm bộ đếm thủ công hoặc giữ tên bước quét cũ khi active job chuyển sang render/verify.
- Luồng đúng: quét → chờ duyệt → sẵn sàng xuất → chờ xuất → đang xuất/kiểm tra → hoàn tất. Chuyển sang xuất cần lệnh người dùng; không tự bỏ qua bước duyệt hoặc tự xuất sau khi tải.

### Mapping các chức năng đã có

| Dashboard / chức năng hiện có | Vị trí V2 | Nguồn khi tích hợp |
| --- | --- | --- |
| Worker và CPU / RAM / GPU / ổ đĩa | Tổng quan → tiến trình & tài nguyên, sidebar ổ E | GET `/api/status` |
| Sáu tab theo giai đoạn | Bộ lọc trên danh sách, thêm Tất cả | `jobTab` hiện có → `BFContracts.tab` |
| Đếm job, hàng đợi FIFO, vị trí quét / xuất | KPI, badge lượt, trang Hàng đợi | `queue_position`, `queue_kind`, `queue.length` |
| Thông tin job / trạng thái / lỗi | Danh sách và bảng Chi tiết | `jobs[]`; GET `/api/jobs/{id}` cho lịch sử sâu |
| Thiết lập nội dung, profile, detector, OCR, tăng tốc | Thiết lập & bắt đầu trong Chi tiết / dòng video | POST start |
| Tiếp tục, dừng sau bước, dừng ngay, retry, hủy | Chi tiết → Thao tác chính / Thao tác khác | Các endpoint job tương ứng; giữ nguyên điều kiện khóa và xác nhận |
| Chạy lại theo phạm vi trong revision mới | Chi tiết → Thao tác khác → Chạy lại kiểm tra | POST rerun |
| Duyệt cảnh | Nút Duyệt cảnh | Từ R4: mở **hộp duyệt V2** `#review/{id}/{màn}` (`docs/DASHBOARD_V2_REVIEW_PLAN.md`); trang review hiện có `/review/{id}` giữ nguyên, mở bằng link "Mở trang duyệt cũ" trong hộp |
| Cấu trúc cục bộ và Visual AI Audit | Chi tiết → Kết quả kiểm tra & xuất video | `structure_audit`, `ai_audit`; mục gấp không thay đổi kết quả |
| Audit JSON / Visual AI | Chi tiết → Thao tác khác → Visual AI Audit → chọn dữ liệu | POST ai-audit; Visual cần opt-in |
| Xuất từ dashboard và từ review | Dòng video / Chi tiết → Xuất video | Cùng POST review/finalize và chính sách dung lượng |
| Bỏ qua, mở lại | Chi tiết → Thao tác khác → Bỏ qua / Mở lại để xuất | POST skip / unskip |
| Đã hủy, đã ẩn, hiện lại | Nhóm gấp cuối danh sách | `hidden_at`, POST hide / unhide |
| Dọn một / nhiều nguồn | Hoàn tất → chọn tối đa 50 → Dọn video gốc; Chi tiết | Preview mới rồi POST cleanup |
| Lưu trữ một / nhiều video | Hoàn tất → Lưu trữ; Chi tiết | Preview mới rồi POST archive |
| Khôi phục bản xuất | Nhóm Đã lưu trữ → Chi tiết | POST source-archive/restore |
| Kiểm tra lại Thùng rác | Chi tiết video chưa xác minh | POST source-recycle-check, **id của row**, không phải job |
| Bộ nhớ logo: xem khung hình / đổi loại / xóa | Trang Bộ nhớ logo | API logo-memory |
| Cấu hình / kết nối / đăng nhập AI | Cài đặt → AI Supervisor | API ai |
| Tạm dừng scheduler, tắt sau bước / ngay | Header và Cài đặt | API scheduler / shutdown |

### Trang review

Cập nhật 2026-10-05 (R4): hộp mẫu cũ đã được thay bằng hộp duyệt V2 (`review*.js`), làm theo `docs/DASHBOARD_V2_REVIEW_PLAN.md` (tương đương trang cũ P1–P17, chỗ khác có chủ ý S1–S9).
Hộp dùng đúng các route review hiện có (queue, session, evidence, frame, video, decision, clear, bulk-keep, bulk-accept, finalize) với payload như trang cũ; không thêm route.
Trang review hiện có `/review/{id}` giữ nguyên (trùng byte) và mở bằng link trong hộp.

## 3. Hợp đồng dữ liệu

| Trường backend | Cách dùng |
| --- | --- |
| `id`, `job_key`, `source_sha256` | Định danh job và draft; không nhận diện bằng tên phim |
| `source_path`, `source_size_bytes`, `duration_seconds` | Tên file / đường dẫn / dung lượng / thời lượng |
| `active_revision`, `active_queue_path` | Revision hiện tại, review có sẵn hay chưa |
| `state`, `current_stage`, `progress` | Trạng thái và tiến độ quét; progress nằm trong 0–1 |
| `queue_kind`, `queue_position` | Loại scan / export và thứ tự backend; không tự suy ra từ tên stage |
| `render_progress.state`, `render_progress.percent` | Tiến độ bản xuất, percent nằm trong 0–100; VERIFYING vẫn đang kiểm tra |
| `review_summary.status` | READY_FOR_EDIT_PLAN mới đủ điều kiện duyệt để xuất |
| `review_summary.main_items`, `pending`, `decisions` | Đếm cảnh chính; unresolved = cảnh chính trừ KEEP + BLUR + CUT |
| `review_summary.advisory_items` | Ứng viên phụ, không cộng vào cảnh chính cần quyết định |
| `review_summary.skip_eligible` | Backend chứng nhận bỏ qua; không tính theo số card đang nhìn thấy |
| `review_summary.export_size_policy` | Điền lựa chọn xuất đã lưu, giống `exportPolicyChoice` hiện có |
| `source_present`, `source_cleaned`, `source_archived` | Khóa thao tác khi nguồn thiếu, đã dọn hoặc đang lưu trữ |
| `source_cleanup`, `source_archive` | Trạng thái nguồn và kiểm tra Thùng rác; dùng row `id` khi recheck |
| `cleanup.eligible/reason`, `archive.eligible/reason` | Backend chứng nhận điều kiện file; trạng thái COMPLETED chưa đủ |
| `source_cleanup_running` | Khóa chung dọn / lưu trữ / khôi phục / recheck |
| `structure_audit`, `ai_audit`, `error` | Giữ kết quả và lý do lỗi; `outdated_rule` cần ghi “quy tắc cũ” |
| `resources.memory.*`, `resources.disk.*`, `resources.gpu.*` | API dùng byte; GPU có thể null, phải hiển thị N/A |
| `detector_options` | ID / tên / mô tả nhóm từ backend, không hardcode danh mục mới ở production |

Các trường `name`, `duration` (chuỗi), `palette` trong fixture phục vụ trình bày demo.
Adapter thật phải derive tên từ `source_path`, định dạng `duration_seconds` và lấy poster qua media hợp lệ.
Fixture có `gpu.name` để minh họa; API hiện tại không cung cấp tên GPU. Không giả định trường này luôn có.
Demo dùng boolean `render_request` để thử khóa. Adapter không được tự invent render request thật; dùng trạng thái / thông tin backend và xử lý từ chối 409. **Đợt 2 (G3):** `/api/status` → `jobs[].render_request` do backend tính (`store.render_request(job_id) is not None`: stage `render` ở PENDING/RUNNING/FAILED_RETRYABLE/FAILED); adapter dùng nguyên giá trị đó.

### Nhóm trạng thái phải giữ

- completed: COMPLETED, SKIPPED.
- export: RENDERING, VERIFYING, QUEUED với queue_kind=export.
- scan_queue: QUEUED với queue_kind=scan.
- review: WAITING_REVIEW, READY_TO_EXPORT.
- scanning: PREFLIGHT, SCANNING_SAFETY, SCANNING_TEXT, SCANNING_LOGO, LOCALIZING_REGIONS, BUILDING_REVIEW, AI_AUDITING và SCANNING_*.
- waiting: còn lại, kể cả QUEUED không biết loại. Không đoán nó là scan.
- Chỉ CANCELLED với hidden_at mới vào nhóm Đã ẩn; loại ra khỏi đếm các tab.
- Lưu trữ / dọn là trạng thái nguồn bổ sung. Không invent job state ARCHIVED / RECYCLED.

## 4. Endpoint và payload phải giữ nguyên

`contracts.js → endpoints` là danh mục máy đọc được để đối chiếu đường dẫn. Danh mục này **không thực hiện HTTP**.

### Đọc dữ liệu

| Method / đường dẫn | Vai trò / điều kiện |
| --- | --- |
| GET /api/session | Lấy token cho phiên; không ghi token vào URL, log hay localStorage |
| GET /api/status, /api/jobs | Tổng quan và danh sách. **Đính chính (mục 8.1 #1):** chỉ `/api/status` → `jobs[]` có đủ trường hàng đợi, review, dọn/lưu trữ; `/api/jobs` là hàng thô, không dùng để vẽ |
| GET /api/jobs/{id} | job, stages, revisions, artifacts, events |
| GET /healthz | Kiểm tra backend còn chạy sau lệnh tắt |
| GET /api/ai | Kết nối / cấu hình / trạng thái phiên AI |
| GET /review/{id} | Trang review hiện có |
| GET /api/jobs/{id}/review/queue, session, resources, export | Tài nguyên review; giữ schema và quyền hiện tại |
| GET /api/jobs/{id}/review/evidence, frame, video | Giữ query hiện có, media_key HMAC theo job; không dùng token phiên làm khóa media |
| GET /media/{path} | Chỉ dùng URL media được server cung cấp và cho phép |
| GET /logo-memory, /api/logo-memory | Trang cũ / danh sách bộ nhớ và memory_sha256 |
| GET /api/logo-memory/frame?key=…&i=… | Khung hình của record; encode key đúng cách |
| GET /api/source-cleanup/preview?ids=1,2 | Preview read-only mới ngay trước mỗi lần dọn |
| GET /api/source-archive/preview?ids=1,2 | Preview read-only mới ngay trước mỗi lần lưu trữ |

### Thay đổi trạng thái

| POST đường dẫn | JSON body | Lưu ý |
| --- | --- | --- |
| /api/jobs/{id}/start | {content_style, profile, detectors, ocr_recognition_batch_size, fast_scan} | Ít nhất một detector; animation/live_action/mixed; careful/fast |
| /api/jobs/{id}/rerun | {detectors, ocr_recognition_batch_size, fast_scan} | Revision mới, giữ report và quyết định cũ |
| /api/jobs/{id}/resume, pause, stop-after-stage, cancel, retry, skip, unskip, hide, unhide | {} | Tên endpoint không đổi; backend kiểm tra state |
| /api/jobs/{id}/ai-audit | {visual: boolean} | false = JSON; true cần đồng ý gửi tối đa 36 thumbnail, không video/audio |
| /api/jobs/{id}/review/finalize | {size_mode:"default"} hoặc {size_mode:"custom",max_output_gb:number} hoặc {size_mode:"unlimited"} | Mặc định 3,5 GB; custom 0,05–1.000 GB; kiểm tra quyết định và source |
| /api/jobs/{id}/review/decision | {id,decision,note?,full_frame?,remember_studio_logo?,remember_platform_logo?} | Giữ contract của review hiện có; người dùng phải duyệt |
| /api/jobs/{id}/review/clear | {id} | Xóa quyết định |
| /api/jobs/{id}/review/bulk-keep, bulk-accept | {filter} | Bộ lọc phải là giá trị trang review hiện có dùng; cần xác nhận |
| /api/scheduler | {paused:boolean} | Giữ thứ tự FIFO |
| /api/shutdown | {mode:"after_stage"} hoặc {mode:"immediate"} | Nhận 202/STOPPING chưa chứng minh backend đã tắt |
| /api/ai/config | {enabled,model,reasoning_effort} | Allowlist hiện có: gpt-5.6-luna/terra/sol, low/medium/high |
| /api/ai/check, /api/ai/login | {} | Dùng luồng ChatGPT hiện có, không thêm API trả phí |
| /api/source-cleanup | {job_ids:[…],preview_id} | Tối đa 50, preview vừa duyệt; chỉ người dùng thực hiện trên file thật |
| /api/source-archive | {job_ids:[…],preview_id} | Tương tự cleanup; giữ SHA-256, rollback và khóa backend |
| /api/source-archive/restore | {job_id} | Chỉ ARCHIVED; đường dẫn input phải trống và hash khớp |
| /api/source-recycle-check | {kind:"source_cleanup" hoặc "archive_export",id:rowId} | id của bản ghi dọn/lưu trữ, không phải job id |
| /api/logo-memory/class | {key,memory_class,platform?,expected_sha256} | platform_logo hoặc studio_logo; hash toàn bộ memory được đọc trước đó |
| /api/logo-memory/delete | {key,expected_sha256} | Backend sao lưu memory và ảnh trước; không xóa frame trực tiếp |

Detector ID hiện tại: advertising, adult, gore, violence. OCR batch: 1 mặc định, 8 thử nghiệm.
Profile fast giảm mật độ; fast_scan tăng tốc xử lý mà giữ mật độ. Không gộp hai tùy chọn.
Platform key hiện tại: iqiyi, youku, tencent_video, mango_tv, sohu, pptv.

### Transport khi viết adapter thật

1. Chạy UI và API cùng origin. Không thêm CORS rộng, proxy sang máy khác hoặc nghe 0.0.0.0.
2. Lấy /api/session; POST có Content-Type: application/json và X-BiliFlow-Token.
3. 403: refresh token và thử lại **một lần** theo luồng hiện tại. Không lặp vô hạn.
4. 409: body là `{error, code, preview?}` (`code` ở **cấp trên cùng**, không nằm trong `error`; `preview` có khi preview đổi). Giữ lý do và `code`, tải trạng thái hoặc preview mới rồi yêu cầu người dùng xác nhận lại. Không tự replay.
5. File preview trả eligible/ineligible/recycle_bin/blocked/preview_id: hiển thị toàn bộ danh sách và lý do loại, không chỉ tổng đếm.
6. Khi có nhiều request polling, dùng sequence / abort để response cũ không ghi đè response mới.
7. Không rebuild toàn bộ form đang nhập mỗi lần polling. Giữ draft theo job_key + source identity; xóa draft khi revision/nguồn đổi.
8. Escape dữ liệu hiển thị, encode query, kiểm tra ID số nguyên dương. Không dùng path hoặc HTML từ server như mã thực thi.
9. Không gọi API ghi để thăm dò khả năng hoặc kiểm tra giao diện.

## 5. Các khóa bắt buộc

- NEEDS_MORE_CONTEXT không phải quyết định cuối; pending và trạng thái chưa ready chặn xuất.
- Bulk keep/accept chỉ áp dụng mục chưa có quyết định, không ghi đè KEEP/BLUR/CUT/NEEDS_MORE_CONTEXT đã được duyệt. Cần xem thêm phải giải quyết riêng.
- Lệnh xuất đang chạy hoặc còn được yêu cầu khóa sửa quyết định, rerun và gửi export trùng. Backend là nguồn thẩm quyền.
- Không đánh dấu COMPLETED chỉ vì nhận QUEUED, hay tiến độ render 100%; VERIFYING vẫn là kiểm tra.
- Skip chỉ khi backend xác nhận queue không có cảnh chính hoặc mọi cảnh chính KEEP.
- Cleanup / archive chỉ khi backend xác nhận bản xuất đúng revision, quyết định và manifest, hoặc skip record còn khớp.
- Nguồn cleaned/archived/PENDING/RESTORING bị khóa. Restore thành công không có nghĩa đã xuất lại thành công.
- Không tự chỉnh source_cleanups/source_archives/recycle_checks, watcher hoặc SQLite để mở khóa.
- Không xác nhận bin capacity bằng fixture; adapter dùng preview backend và từ chối khi capacity không biết / vượt limit − 64 MiB.
- File actions có thể thành công một phần; hiển thị từng result. Không tự xóa selection của mục thất bại.
- Recheck chỉ append kết quả xác minh, không sửa lịch sử dọn/lưu trữ.
- Xóa/đổi logo có expected_sha256; memory_changed phải tải lại trước khi duyệt lại.
- Audit cần đồng ý Visual riêng từng video. Không gửi source/audio, không tự áp dụng đề xuất AI.

## 6. Trình tự tích hợp sau khi người dùng yêu cầu

### A. Chuẩn bị

1. Đọc AGENTS.md và handoff mới nhất; xác minh Git và version đang chạy. Contract này có thể cũ nếu backend vừa thay đổi.
2. Tạo nhánh làm việc theo hướng dẫn repository; không tự merge, commit hoặc push.
3. Lưu hash / bản sao mã frontend cần thay trong temp trên E. Không sửa dữ liệu thật.
4. Kiểm tra worker / queue qua GET, thống nhất thời điểm restart nếu cần. Không tự tắt worker đang làm video.

### B. Tạo adapter, giữ route cũ

1. Tách state / mutation của app.js thành DemoStore; các hàm render chỉ nhận normalized snapshot.
2. Viết ControlCenterAdapter có loadStatus/loadJob/loadAI/loadMemory và dispatch request. Chỉ adapter được làm HTTP.
3. Adapter lấy schema thật ở mục 3, không mang fixture, preview_id giả hoặc state simulation vào bản live.
4. Phục vụ V2 tại route opt-in riêng, ví dụ /dashboard-v2; giữ / và /review/{id} như hiện tại.
5. Whitelist asset route; không mở thư mục project hoặc thư mục state/report qua static directory listing.
6. CSP bản live cần connect-src 'self' thay cho 'none' chỉ ở route live, đồng thời giữ frame-ancestors, token và same-origin.
7. Duyệt cảnh mở hộp duyệt V2 (từ R4); trang review hiện có vẫn mở được bằng link trong hộp. Hộp mẫu cũ đã bị bỏ.
8. Tái sử dụng export_dialog.py cho chính sách, validation, xác nhận và exportPolicyChoice; không tạo gate preview mới ngoài workflow hiện tại.
9. Tải poster / media qua URL backend hợp lệ, lazy load ảnh cần nhìn. Không dựng full preview chỉ để trang overview.
10. Xóa scenario selector, mock login/shutdown/memory/file operations khỏi build live.

### C. Các gate kiểm tra

- [ ] Prototype verify.cjs qua; các JS qua node --check.
- [ ] Adapter contract tests cho endpoint / body / token, response stale, 403 một retry, 409 không replay.
- [ ] Mọi state vào đúng một trong sáu tab; queue scan/export dùng thứ tự backend.
- [ ] Draft detector/OCR/metadata và export policy không mất khi polling hoặc mở/đóng Chi tiết.
- [ ] Quét chỉ logo vẫn ghi ba nhóm chưa kiểm tra; default mới vẫn đủ bốn nhóm.
- [ ] NEEDS_MORE_CONTEXT, source missing/cleaned/archived và export in flight đều khóa đúng.
- [ ] Hủy dialog không POST; double click confirm chỉ gửi một POST; lỗi mở lại dialog có lý do.
- [ ] Dọn/lưu trữ thử trên fixture trong temp với fake recycler; tuyệt đối không trên video thật.
- [ ] Preview stale, bin unknown/full, lock busy, per-file partial results, restore refused đều hiển thị đúng.
- [ ] Memory hash conflict, studio KEEP / platform BLUR và audit opt-in đúng.
- [ ] Desktop 1280, mobile 375; search, sticky filters, modal, drawer, Tab/Escape và không tràn ngang.
- [ ] Source Python hashes / cache stage identity không đổi bởi thay frontend. Nếu sửa control_center để thêm route, xác minh cache dependency không đổi.
- [ ] Chạy focused tests của các module thật có sửa; full suite nếu sửa scheduler/review/renderer/detector theo AGENTS.md.
- [ ] Người dùng xem route V2 và yêu cầu chuyển mặc định trước khi đổi /.

### D. Chuyển mặc định và rollback

1. Chỉ đổi route / sau khi người dùng duyệt và gates qua; giữ route /dashboard-classic hoặc cờ chọn UI.
2. Restart Control Center theo luồng hiện có ở thời điểm đã thống nhất, không sửa SQLite hay reset job.
3. Kiểm tra read-only: version, số job, queue positions, revision và nguồn; mở một review đúng job.
4. Nếu có lỗi UI: đưa route mặc định về classic / tắt cờ V2, giữ dữ liệu và backend nguyên trạng.
5. Không rollback bằng cách restore database cũ sau khi người dùng đã duyệt thêm; không xóa report/decisions.
6. Không coi bỏ V2 là lý do chạy lại scan hoặc invalidating detector cache.

## 7. Kiểm tra demo và giới hạn bằng chứng

Các bằng chứng kiểm thử / ảnh / log server nằm ở `temp/dashboard-v2-evidence/` trên E.
Node verify kiểm tra hợp đồng, schema fixture, export / source locks, validation và không có network transport.
Browser kiểm tra tương tác của prototype. **Không chứng minh backend live đã được tích hợp hay thay đổi.**
Không cần chạy model, full suite detector hoặc video thật để đánh giá thay đổi này.

Các lỗi backend đã được trao đổi ở bước review code trước đó (export identity, finalize dùng lại file không có manifest, kiểm tra đầu vào HTTP)
**đã được sửa trên `main`** (merge 2a37496 và 732b02b, ngày 2026-10-03). Prototype V2 không phải nơi sửa chúng; V2 chỉ hiển thị đúng lý do backend trả về
(xem mục 8). Dữ liệu và quyết định xuất vẫn do backend quyết định, V2 không tự suy ra tên hay đường dẫn bản xuất.


## 8. Đối chiếu với code hiện tại (Pha 1, cloud, 2026-10-03)

Đọc từ `src/biliflow/control_center.py` (`do_GET`, `do_POST`, `status()`, `finalize()`), `logo_memory_admin.py`, `job_store.py`.
Tự động kiểm tra bằng `tests/test_dashboard_v2_contract.py` (route và nhóm trạng thái). Không có thay đổi backend.

### 8.1. Chênh lệch cần adapter xử lý

| # | Điều thực tế trong code | Hệ quả cho V2 |
| --- | --- | --- |
| 1 | `GET /api/jobs` trả **hàng thô của store**, không có `queue_position`, `queue_kind`, `review_summary`, `cleanup`, `archive`, `source_present`, `source_cleanup`… Chỉ `GET /api/status` → `jobs[]` được bổ sung các trường này (`status()`, dòng 602–629). | Adapter lấy danh sách chính từ `/api/status`, không dùng `/api/jobs` để vẽ. `/api/jobs` chỉ để đối chiếu. |
| 2 | 409 trả `{"error": str, "code": str, "preview"?: {...}}`; `code` cấp trên cùng. Logo memory 409 là `{"error", "code":"memory_changed"}`. | Đọc `body.code`, không phải `body.error.code`. |
| 3 | `POST …/review/finalize` thành công trả `{status:"COMPLETED"\|"QUEUED", output, export_size_policy, plan?}`. `COMPLETED` nghĩa là backend **chứng minh được** bản xuất của lần duyệt này đã có (manifest), không render lại. `QUEUED` chỉ là đã xếp hàng. | Không đánh dấu hoàn tất theo `QUEUED`; luôn tải lại `/api/status` sau finalize. |
| 4 | Finalize **400** khi đường dẫn xuất đã có file/liên kết mà manifest không chứng minh (`EXPORT_PATH_TAKEN_MESSAGE`, “… BiliFlow không ghi đè …”), hoặc queue chưa `READY_FOR_EDIT_PLAN`, hoặc nguồn bị khóa/thiếu, hoặc `size_mode` sai. | Hiện nguyên văn `error`, không tự thử lại, không đổi tên file. |
| 5 | `do_POST` thứ tự: Host sai → 403; thiếu/sai token → 403 (body bị bỏ để socket không bị reset); body quá 64 KiB, Content-Length sai, JSON không phải object hoặc lồng quá sâu → 400; body ngừng gửi quá 20 s → **408** `{"error": …}` rồi đóng kết nối (client có thể chỉ thấy kết nối đóng, không có body); đường dẫn lạ → 404; `ActionConflict` → 409; `KeyError/TypeError/ValueError` → 400; còn lại → 500. | Adapter phân biệt: 403 (làm mới token đúng một lần), 408 và lỗi mạng (“kết nối bị ngắt”, **không** tự gửi lại lệnh ghi), 409, 400, 500, 404. |
| 6 | `GET` lỗi: `KeyError/ValueError/FileNotFoundError` → 404; preview với danh sách id sai → 400; còn lại → 500. Host sai → 403. Riêng media: `k` sai → 403 (khóa media, **không phải** token phiên); logo frame ngoài thư mục → 403. | Không làm mới token khi **GET** trả 403. GET chỉ thử lại khi người dùng bấm. |
| 7 | `render_progress` chỉ có khi `state === "RENDERING"`: `{state: "STARTING"\|"RENDERING"\|"VERIFYING", percent 0–100, speed_text?, eta_seconds?}`. `state === "VERIFYING"` cũng là trạng thái job hợp lệ. | Cả hai cách vẫn vào nhóm “xuất”; thẻ chính “Kiểm tra bản xuất” khi một trong hai là VERIFYING. |
| 7b | (Đợt 2, G3) `jobs[].render_request` (bool) có trong `/api/status`: `store.render_request(job_id) is not None`, cùng định nghĩa backend dùng để chặn xuất/chạy lại/sửa quyết định. Chỉ thêm trường. | Adapter dùng nguyên giá trị; không suy ra từ `current_stage`. |
| 8 | `active` là `{job_id, stage, pid}` hoặc `null` (không phải mảng). `queue` là `{length, paused}`; thứ tự theo từng job nằm ở `jobs[].queue_position/queue_kind`. `queue_kind` chỉ `scan` hoặc `export`, và chỉ có khi job đang chờ chạy được. | Không dựng danh sách queue từ nơi khác. Kiểu lạ → nhóm “Chờ xử lý”. |
| 9 | `resources`: `cpu_percent`, `memory{percent,used_bytes,total_bytes}`, `disk{percent,free_bytes,total_bytes}`, `gpu` = `null` hoặc `{memory_used_bytes,memory_total_bytes,utilization_percent,temperature_c}`. **Không có tên GPU.** | Hiển thị N/A khi `gpu` null; không bịa `gpu.name` (fixture demo có, live không). |
| 10 | `GET /api/logo-memory` trả `{memory_sha256, records[], backups}`; mỗi record có `key, memory_class, decision, platform, labels, episode, frames, frame_urls, convertible, refusal_text…` (không có `name`/`color` như fixture). `frame_urls` đã mã hóa sẵn. | Adapter dựng tên hiển thị từ record; dùng `memory_sha256` của **lần tải gần nhất** làm `expected_sha256`; 409 `memory_changed` → tải lại. |
| 11 | Review media: `evidence` cần `item`; `frame` cần `item`, `t`, `k`; `video` cần `k`. `k` lấy từ `GET /api/jobs/{id}/review/session` (cùng `token`). | Hộp duyệt V2 dựng URL qua `adapter.js` (`item`, `t`, `k` mã hóa, `k` từ `review/session`; 403 → lấy phiên mới một lần cho cả hộp). |
| 12 | `POST /api/shutdown` trả **202** `{status:"STOPPING", mode}` rồi mới tắt; `/api/scheduler` trả `{paused}`; `/api/jobs/{id}/ai-audit` trả `{status:"QUEUED", visual_opt_in}`. | Không coi 202 là đã tắt; kiểm tra `/healthz` (guide mục 4). |
| 13 | Job state thực tế (20 giá trị): DISCOVERED, NEEDS_METADATA, QUEUED, PREFLIGHT, SCANNING_SAFETY, SCANNING_TEXT, SCANNING_LOGO, LOCALIZING_REGIONS, BUILDING_REVIEW, AI_AUDITING, WAITING_REVIEW, READY_TO_EXPORT, RENDERING, VERIFYING, PAUSED, FAILED, INTERRUPTED_RECOVERABLE, CANCELLED, COMPLETED, SKIPPED. `review_summary` chỉ có ở WAITING_REVIEW/READY_TO_EXPORT/SKIPPED có `active_queue_path`. | Test kiểm tra mỗi state vào đúng một nhóm và trùng `jobTab` của dashboard cũ. |

### 8.2. Mapping chức năng đã đối chiếu

Tình trạng: **khớp** (endpoint, body và khóa đã khớp code), **một phần** (khớp nhưng thiếu tầng giao diện/adapter), **mô phỏng** (chỉ có trong demo).

| Chức năng cũ | Vị trí V2 | Endpoint / payload thực tế | Khóa / xác nhận | Tình trạng |
| --- | --- | --- | --- | --- |
| Tổng quan, hàng đợi, tài nguyên | Tổng quan, Hàng đợi, sidebar | GET `/api/status` (jobs, queue, active, resources, storage, detector_options, source_cleanup_running, scheduler_paused) | — | khớp (mục 8.1 #1, #7–#9) |
| Thiết lập & bắt đầu | Chi tiết → Thiết lập | POST `/api/jobs/{id}/start` `{content_style, profile, detectors[], ocr_recognition_batch_size, fast_scan}`; `detectors` không phải mảng → 400 | nguồn không bị khóa; ≥1 nhóm hợp lệ | khớp |
| Tiếp tục / dừng sau bước / dừng ngay / thử lại / hủy | Chi tiết → Thao tác chính/khác | POST `…/resume`, `stop-after-stage`, `pause`, `retry`, `cancel` `{}` | backend kiểm tra state; hủy cần xác nhận UI | khớp |
| Chạy lại | Thao tác khác | POST `…/rerun` `{detectors?, ocr_recognition_batch_size, fast_scan}` | không khi xuất đang chờ/chạy | khớp |
| Ẩn / hiện lại job đã hủy | Nhóm gấp “Đã ẩn” | POST `…/hide`, `…/unhide` `{}` | chỉ CANCELLED | khớp |
| Bỏ qua / mở lại | Thao tác khác | POST `…/skip`, `…/unskip` `{}` | `review_summary.skip_eligible` do backend | khớp |
| Visual AI Audit | Thao tác khác | POST `…/ai-audit` `{visual: bool}` → `{status:"QUEUED", visual_opt_in}` | Visual cần đồng ý riêng từng video | khớp |
| Duyệt cảnh | Nút Duyệt cảnh | mở hộp duyệt V2 `#review/{id}/{màn}` (R4); trang cũ GET `/review/{id}` giữ nguyên, có link trong hộp | — | khớp; bản demo dùng queue giả trong bộ nhớ |
| Quyết định / xóa / bulk | Hộp duyệt V2 (và trang review cũ) | POST `…/review/decision|clear|bulk-keep|bulk-accept`, body như trang cũ | `ensure_review_editable` | khớp (từ R2/R3) |
| Xuất video | Dòng video / Chi tiết | POST `…/review/finalize` `{size_mode, max_output_gb?}` → 200 `{status, output, export_size_policy}` hoặc 400/409 (mục 8.1 #3–#4) | READY_TO_EXPORT, queue READY_FOR_EDIT_PLAN, không còn cảnh chờ, nguồn không khóa, không có lệnh xuất | khớp |
| Dọn video gốc | Hoàn tất → Dọn | GET `/api/source-cleanup/preview?ids=` → `{preview_id, eligible[], ineligible[], recycle_bin, blocked}`; POST `/api/source-cleanup` `{job_ids[], preview_id}` | tối đa 50, preview mới; 409 `preview_changed/bin_unavailable/bin_capacity/busy` | khớp; **thao tác thật chỉ người dùng bấm** (AGENTS.md) |
| Lưu trữ / Khôi phục | Hoàn tất → Lưu trữ; Đã lưu trữ | GET `/api/source-archive/preview?ids=`; POST `/api/source-archive` `{job_ids, preview_id}`; POST `/api/source-archive/restore` `{job_id}` | như trên; restore khi đường dẫn input trống và hash khớp | khớp; chỉ người dùng bấm |
| Kiểm tra lại Thùng rác | Chi tiết video chưa xác minh | POST `/api/source-recycle-check` `{kind:"source_cleanup"\|"archive_export", id: <id của row>}` | `source_cleanup_running` | khớp |
| Bộ nhớ logo | Trang Bộ nhớ logo | GET `/api/logo-memory`, GET `…/frame?key=&i=`, POST `…/class` `{key, memory_class, platform?, expected_sha256}`, POST `…/delete` `{key, expected_sha256}` | 409 `memory_changed` → tải lại | khớp (schema ở mục 8.1 #10); demo dùng fixture khác schema |
| AI Supervisor | Cài đặt | GET `/api/ai`; POST `/api/ai/config` `{enabled, model, reasoning_effort}`, `/api/ai/check`, `/api/ai/login` | allowlist model ở backend | khớp |
| Tạm dừng hàng đợi / tắt | Header, Cài đặt | POST `/api/scheduler` `{paused}`; POST `/api/shutdown` `{mode}` | 202 chưa chứng minh đã tắt | khớp |
| Tải video | Trang Tải video | **không có endpoint** | — | **mô phỏng**, không có downloader |
| Tình huống kiểm thử, đăng nhập/tắt/bộ nhớ giả | Cài đặt → Tình huống | — | — | **mô phỏng**, bỏ khỏi bản live |

### 8.3. Quy tắc adapter đã chốt (theo code hiện tại)

- Lệnh ghi **không** tự lặp lại. Ngoại lệ duy nhất: POST nhận 403 → `GET /api/session` lấy token mới rồi gửi lại **đúng một lần**; lần 403 thứ hai hiện lỗi.
- 408, lỗi mạng, 5xx: hiện lỗi, **không** gửi lại (lệnh có thể đã chạy); yêu cầu tải lại trạng thái.
- 409: hiện `error` + `code`; nếu có `preview` thì dùng làm preview mới; không replay.
- 400: hiện nguyên văn `error`.
- GET chỉ thử lại khi người dùng bấm; 403 của GET không làm mới token.
- Mọi request đi qua một adapter duy nhất; token chỉ ở bộ nhớ, không ghi URL, localStorage hay log.
- Trường hiển thị adapter tự thêm (`normalizeJob`): `name` = tên file của `source_path`, `duration` từ `duration_seconds`, `palette` (ảnh minh họa), `output_path` = `cleanup.output_name`/`archive.output_name` do backend trả (không tự tính), `render_request` = giá trị backend gửi trong `/api/status` (đợt 2, G3; không còn suy ra từ `current_stage`; backend vẫn chặn và 409/400 được hiển thị).
- Polling: `/api/status` mỗi 3 s (như dashboard cũ); `/api/ai` khi mở trang và khi đang đăng nhập; `/api/logo-memory` khi mở trang Bộ nhớ logo và sau mỗi thao tác logo.

### 8.4. Route xem thử `/dashboard-v2/` (Pha 3)

- `GET /dashboard-v2` → 301 đến `/dashboard-v2/` (asset dùng đường dẫn tương đối; CSP `base-uri 'none'` cấm `<base>`).
- `GET /dashboard-v2/` → `dashboard_v2/live.html`; `GET /dashboard-v2/<file>` chỉ cho 12 file trong `DASHBOARD_V2_FILES` (`control_center.py`). Mọi đường dẫn khác → 404 JSON, không liệt kê thư mục, không phục vụ `index.html`, fixture hay script kiểm tra.
- Trang có header CSP riêng `DASHBOARD_V2_CSP` (`connect-src 'self'`, `frame-ancestors 'self'`), cộng header chung `frame-ancestors 'self'` + `X-Frame-Options: SAMEORIGIN` của mọi response.
- `/`, `/review/{id}`, mọi `/api/...`, token, mã lỗi giữ nguyên (test so byte với f6996bb).
- Rollback: không mở `/dashboard-v2/` là đủ; route không ghi dữ liệu. Gỡ hẳn: bỏ nhánh `elif` và các hằng `DASHBOARD_V2_*` trong `control_center.py` (không ảnh hưởng cache quét).

### 8.5. Chế độ điện thoại (đợt 2, mục 12.3 của kế hoạch)

Hướng dẫn cho người dùng: `docs/DASHBOARD_V2_PHONE.md`. Code: `src/biliflow/phone_access.py` (chỉ `control_center.py` import, ngoài fingerprint cache) và `_phone_handler_class` trong `control_center.py`.

| Đường dẫn | Listener | Vai trò |
| --- | --- | --- |
| GET `/api/phone-mode` | `127.0.0.1:8765` | `{remote:false, enabled, address, port, url, code, locked, failed_attempts, max_failed_attempts, default_port}` |
| POST `/api/phone-mode` `{enabled: bool, port?}` | `127.0.0.1:8765` + token | Bật (mã mới; đang bật thì giữ mã) / tắt (đóng listener, mã và cookie hết hiệu lực). Không khởi động lại Control Center |
| GET `/api/phone-mode` | điện thoại (đã có cookie) | `{remote:true, enabled:true, pc_only:[…]}`, không có mã |
| GET `/`, `/dashboard-v2/`, `/phone-login` không cookie | điện thoại | Trang nhập mã (401). `?code=` trong link bị bỏ qua (câu 12: luôn nhập tay), không cấp cookie, không tính lần sai |
| POST `/phone-login` (form `code=`) | điện thoại | Đúng → trang nối (meta refresh tới V2) + `Set-Cookie: biliflow_phone=<HMAC>; HttpOnly; SameSite=Strict; Path=/`; sai → 401 (còn N lần); lần sai thứ 10 → 403 khóa nhập mã. Khi đang khóa: chỉ xét khóa mở đặc biệt `UNLOCK_KEY`. Đúng → 401 “Đã gỡ khóa” (đặt lại số lần sai, **không** cấp cookie, vẫn cần mã 8 ký tự); sai → 403 (còn N lần); 5 lần sai hoặc đã gỡ 3 lần → khóa mở bị khóa tới lần bật sau |
| Mọi đường khác không cookie | điện thoại | 401 JSON |
| `/` có cookie | điện thoại | 303 → `/dashboard-v2/` |
| `/review/{id}` có cookie | điện thoại | Trang duyệt cũ y như PC + `REVIEW_PHONE_STYLE` (trước `</head>`) + `REVIEW_PHONE_SCRIPT` (trước `</body>`): mũi tên chip, ẩn “phím N” trên cảm ứng, nút quyết định `position:fixed` ở đáy (≤ 820 px), chữ ≥ 12 px. Trên `127.0.0.1:8765` không chèn gì |
| POST | điện thoại | **Đợt 3 (H2): danh sách cho phép** `PHONE_ALLOWED_POSTS` (regex, full match): `/api/scheduler`, `/api/ai/check`, `/api/jobs/{id}/(start\|resume\|pause\|stop-after-stage\|cancel\|retry\|rerun\|skip\|unskip\|hide\|unhide)`, `/api/jobs/{id}/ai-audit` (chỉ `visual: false`), `/api/jobs/{id}/review/(decision\|clear\|bulk-keep\|bulk-accept\|finalize)`. Mọi POST khác → 403 `{error: "Chỉ làm trên PC: …", code: "pc_only"}` trước khi đọc body (lý do riêng cho các route trong `PC_ONLY_POSTS`, còn lại `PC_ONLY_DEFAULT`). `ai-audit` với `visual: true` → 403 `PC_ONLY_VISUAL_AUDIT`. `tests/test_dashboard_v2_phone_hardening.py` đọc mọi route của `do_POST` và báo lỗi khi route chưa phân loại |

- Listener chỉ bind địa chỉ IPv4 riêng (10/8, 172.16/12, 192.168/16) tìm bằng `lan_address()` như Golden Label; cổng mặc định 8767 (1024–65535, khác 8765). Không bao giờ `0.0.0.0`.
- Host phải đúng `<ip>:<cổng>`; POST cần cookie, token phiên, và Origin (nếu có) đúng `http://<ip>:<cổng>`.
- Trang nhập mã: CSP `default-src 'none'; form-action 'self'; frame-ancestors 'none'`, `Referrer-Policy: same-origin` (`no-referrer` làm form gửi `Origin: null`).
- Adapter: `loadPhone()`; `state.remote` khóa các thao tác `C.pcOnlyOps` (cleanup/archive/restore/recheck) và các nút tắt / AI / bộ nhớ logo trong giao diện. 403 có `code: "pc_only"` không làm mới token và không gửi lại.

### 8.6. Làm chắc chế độ điện thoại (đợt 3, mục 13 của kế hoạch)

- **H1:** cả hai listener: `urlparse` lỗi → 400 "Đường dẫn không hợp lệ", không traceback. Listener điện thoại: tối đa `MAX_CONNECTIONS` = 32 kết nối (vượt thì đóng ngay); kết nối chưa có cookie timeout `GATE_TIMEOUT_SECONDS` = 5 s, có cookie thì 20 s như PC (stream video vẫn bỏ timeout); `handle_error` một dòng, tối đa 1 dòng mỗi 10 s kèm số dòng bị bỏ.
- **H2:** danh sách cho phép (8.5). Từ chối trả lời trước, rồi chỉ bỏ phần body còn lại trong tối đa 1 s.
- **H3:** tự tắt sau `AUTO_OFF_SECONDS` = 8 giờ, và khi `lan_address()` khác địa chỉ đang nghe (kiểm mỗi `ADDRESS_CHECK_SECONDS` = 60 s; lỗi dò cũng tính là đổi). GET `/api/phone-mode` (PC) có `enabled_at`, `expires_at`, `last_disabled_reason` (`user` / `expired` / `address_changed` / `stopped`), `last_disabled_reason_text`, `last_disabled_at`. `/` trên PC có thêm `<div id="phone-mode-notice">` **chỉ khi chế độ đang bật** (tắt thì trùng byte fixture D2); `/review/{id}` trên PC luôn trùng byte. `Start-BiliFlow.cmd` in `NOTE: phone mode is ON: …` khi dùng lại Control Center đang bật chế độ này.
- **H4:** event của Control Center (`job_id` NULL): `PHONE_MODE_ENABLED`, `PHONE_MODE_DISABLED` (`reason`), `PHONE_LOGIN`, `PHONE_CODE_WRONG`, `PHONE_CODE_LOCKED` (WARN), `PHONE_UNLOCKED`, `PHONE_UNLOCK_WRONG`, `PHONE_UNLOCK_LOCKED` (WARN). Payload chỉ có `ip`, bộ đếm, lý do, địa chỉ/cổng của listener; không bao giờ có mã, chữ đã gõ hay cookie. GET `/api/phone-mode` (PC) có `events`: 10 event gần nhất (trong bộ nhớ), mới nhất trước; listener điện thoại không trả trường này.
- **H5:** launcher: lỗi không có HTTP status → GET `/api/phone-mode` → báo "IS on" (in link, mã) / "NOT on" / "unknown".
- **Câu 14:** khi tạo `PhoneAccess`, `restore_history(store.events(None, limit=500))` đọc lại tối đa 20 event `PHONE_*` gần nhất (đánh dấu `restored: true`) và lý do tắt gần nhất: từ event `PHONE_MODE_DISABLED` mới nhất; nếu event mới nhất là `PHONE_MODE_ENABLED` thì lý do là `stopped`.
- **Câu 15:** POST `/api/phone-mode` `{extend: true}` (PC, token): giờ tắt = bây giờ + 8 giờ, giữ mã, event `PHONE_MODE_EXTENDED`; đang tắt → 400. Qua listener điện thoại → 403 `pc_only`.
- **H6:** `PhoneAccess.try_code(text, ip=…)` trả `(outcome, set_cookie_header | None)` trong một lần giữ khóa; `set_cookie_header()` đã bỏ.

### 8.7. Đợt 4 (mục 14 của kế hoạch)

- **L1:** `_PhoneServer` giới hạn `MAX_CONNECTIONS_PER_IP` = 6 kết nối mỗi IP (trong tổng 32); vượt thì đóng ngay kết nối mới của IP đó. `PhoneHandler.setup()` đặt một `threading.Timer(GATE_TIMEOUT_SECONDS)` tính từ lúc nhận kết nối, gọi `request.shutdown(SHUT_RDWR)`; hủy trong `opened()` (có cookie) và `finish()`. Kết nối bị từ chối vì giới hạn ghi event `PHONE_CONNECTIONS_LIMITED` (`ip`, `limit`: `per_ip`/`total`), tối đa 1 event mỗi `LIMITED_EVENT_INTERVAL_SECONDS` = 60 s.
- **L2:** `PHONE_LOGIN` chỉ ghi một lần cho mỗi IP trong một lần bật.
- **L3:** `_PhoneServer` giữ các socket đã nhận; `disable()` đóng listener rồi `close_connections()` (shutdown mọi socket đang mở, kể cả video đang phát). Listener `127.0.0.1:8765` không bị đụng.
- **L4:** hướng dẫn Firewall: không bấm Cancel; rule `-Program <python.exe trong runtime\python\cpython-3.11.*> -Protocol TCP -LocalPort 8767 -Profile Private -RemoteAddress LocalSubnet`; dọn rule Block/Public cũ. Dòng in của launcher (ASCII) có "KHONG bam Cancel".
