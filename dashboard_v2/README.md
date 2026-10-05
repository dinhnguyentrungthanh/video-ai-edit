# BiliFlow Dashboard V2

Có hai trang dùng chung giao diện (`app.js`):

- **Demo** (`index.html`): dữ liệu mẫu trong bộ nhớ (`mock-data.js` + `demo-store.js`), CSP `connect-src 'none'`, không kết nối Control Center.
- **Bản live** (`live.html`): `adapter.js` đọc/ghi Control Center cùng origin, CSP `connect-src 'self'`. Control Center phục vụ trang này ở `/dashboard-v2/` (Pha 3). Dashboard cũ ở `/` giữ nguyên. Máy thật kiểm từng đợt theo `docs/DASHBOARD_V2_CLOUD_PLAN.md` và `docs/DASHBOARD_V2_REVIEW_PLAN.md`.

Trang **Tải video** ở cả hai bản đều là mô phỏng: không downloader, không gọi mạng.

**Mở trên điện thoại / laptop trong Wi-Fi nhà** (đợt 2): chạy `Start-BiliFlow-Phone.cmd` ở gốc repo; xem `docs/DASHBOARD_V2_PHONE.md`. Khung "Mở trên điện thoại" nằm ở Cài đặt của bản live trên PC.

## Mở demo

Chạy `Start-Demo.cmd` trong thư mục này rồi mở **http://127.0.0.1:8794/**.
Nếu demo đã được mở trong phiên Codex thì chỉ cần truy cập địa chỉ trên.
Server chỉ nghe trên loopback, chỉ phục vụ các file giao diện có trong danh sách cho phép,
không có API, proxy, thao tác xử lý video hay phụ thuộc cần cài thêm.
Để dừng server chạy từ launcher: Ctrl+C trong cửa sổ launcher.

## Nội dung

- Hai chế độ sáng/tối, chuyển bằng nút ở góc trên bên phải. Lựa chọn được nhớ riêng trong trình duyệt; đặt lại dữ liệu mẫu không đổi giao diện.
- Trang **Tải video**: YouTube / Phimmoi (mẫu), nhiều link mỗi lần, danh sách tiến độ riêng, chờ/đang tải/kiểm tra/hoàn tất/lỗi, lọc, tải đồng thời 1–3, tạm dừng/hủy/thử lại và nhật ký mẫu. Phimmoi dùng `phimmoi.example`; không phải tên miền tải thật. Không chạy downloader, command, PowerShell hoặc API tải thật. Hướng dẫn adapter command/PowerShell ở tài liệu tích hợp.
- Tổng quan tách Đang quét cảnh, Chờ bạn duyệt, Sẵn sàng xuất, Đang xuất video và Hoàn tất; số đếm là video. Video chờ quét/chờ xuất hiển thị riêng, không cộng vào số đang chạy. Bấm mỗi ô lọc đúng nhóm và xóa từ khóa tìm kiếm cũ.
- Chi tiết video ưu tiên tiến độ và 1–2 thao tác chính; mục **Thao tác khác** giữ các chức năng còn lại cùng điều kiện khóa và xác nhận.
- Tổng quan, danh sách video với tìm kiếm / bộ lọc / phân trang.
- Hàng đợi FIFO chung cho quét và xuất, tiến độ worker và tài nguyên máy.
- Bảng chi tiết, thiết lập nhóm quét / OCR / chế độ, tiếp tục / dừng / hủy / thử lại / chạy lại.
- **Duyệt cảnh** (hộp duyệt, `review*.js`, kế hoạch `docs/DASHBOARD_V2_REVIEW_PLAN.md`): nút "Duyệt cảnh" mở hộp trên màn đang xem (`#review/<id>/<màn>`); ảnh, video, dải khung, timeline, phóng to, quyết định, nút vùng, nhớ logo, phím tắt, hoàn tác, hàng loạt, xuất video. Bản demo dùng queue giả trong bộ nhớ; bản live dùng route review hiện có. NEEDS_MORE_CONTEXT chặn xuất. Trang duyệt cũ mở bằng link trong hộp.
- Xuất với ba lựa chọn dung lượng, bỏ qua / mở lại.
- Dọn nguồn / lưu trữ / khôi phục / kiểm tra lại Thùng rác **chỉ bằng dữ liệu mẫu**.
- Quản lý bộ nhớ logo, AI Supervisor, lệnh tắt mô phỏng.
- Cài đặt → Tình huống kiểm thử demo: kiểm tra mất kết nối, đang xuất, Thùng rác đầy, hệ thống bận và xung đột 409.

Mọi trạng thái thử nghiệm được giữ trong bộ nhớ của tab; tải lại trang sẽ đặt lại.
Ảnh SVG và cảnh duyệt là minh họa tự tạo, không trích xuất video thật.

## Cấu trúc và kiểm tra

| File | Vai trò |
| --- | --- |
| `index.html`, `styles.css`, `theme.css` | Khung trang, bố cục và hai theme; theme.css tải sau styles.css |
| `contracts.js` | Trạng thái, điều kiện thao tác, danh mục đường dẫn API để đối chiếu |
| `mock-data.js` | Fixture tổng hợp, dùng các trường của API hiện tại (chỉ bản demo) |
| `demo-store.js` | DemoStore: trạng thái mẫu trong bộ nhớ, cùng giao diện với live store (chỉ bản demo) |
| `adapter.js` | ControlCenterAdapter: nơi **duy nhất** gọi HTTP; token, 403 làm mới một lần, không lặp lệnh ghi, chống response cũ; live store và chuẩn hóa snapshot (chỉ bản live) |
| `live.html` | Trang live cho `/dashboard-v2/`; cùng phần body với `index.html` |
| `download-demo.js` | Hai nguồn mẫu, kiểm tra batch và state machine hàng đợi tải; không có transport hoặc downloader |
| `app.js` | Giao diện; chỉ đọc snapshot của store và gọi `store.dispatch` |
| `review-core.js`, `review-detail.js` | Hộp duyệt: logic thuần (lọc, khóa, payload, so với trang cũ) và chữ chi tiết; chạy được trong node |
| `review-media.js`, `review-cards.js`, `review.js`, `review.css` | Hộp duyệt: bộ nạp ảnh (tối đa 2) và video dùng chung, thẻ, điều khiển hộp, bố cục PC / laptop / điện thoại |
| `serve.py` | Server static hạn chế phạm vi, cổng riêng |
| `verify.cjs` | Kiểm tra hợp đồng, điều kiện khóa, demo không có transport |
| `verify-adapter.cjs` | Test adapter bằng transport giả (endpoint/body, 403/408/409/400/500, double click, response cũ) |
| `verify-review.cjs` | Hộp duyệt so với hàm của trang duyệt cũ (lọc, dải khung, quyết định, hoàn tác, hàng loạt; chỗ khác có chủ ý S1–S9) |
| `browser-check.cjs` | Tùy chọn: Chromium headless qua Playwright, với API giả lập trong tiến trình (draft, khóa, Tab/Escape, 1280/375) |
| `review-fake-server.cjs` | Control Center giả cho các browser-check của hộp duyệt (queue giả, khung, clip VP8 tạo bằng ffmpeg trong `temp/`) |
| `browser-check-review*.cjs` | Hộp duyệt trên Chromium: xem (`-review`), quyết định (`-write`), hàng loạt và xuất (`-bulk`), bố cục điện thoại / laptop (`-layout`, đo bằng `review-layout-audit.cjs`); `-phone` chỉ chạy qua `tests.test_dashboard_v2_review.ReviewR4PhoneListener` (listener điện thoại thật trên root tạm) |

Chạy kiểm tra từ thư mục project:

```
node dashboard_v2/verify.cjs
node dashboard_v2/verify-adapter.cjs
node dashboard_v2/verify-review.cjs
node dashboard_v2/browser-check.cjs   # cần Playwright; không có thì in SKIP
node dashboard_v2/browser-check-review.cjs
node dashboard_v2/browser-check-review-write.cjs
node dashboard_v2/browser-check-review-bulk.cjs
node dashboard_v2/browser-check-review-layout.cjs
```

`tests/test_dashboard_v2_frontend.py` gọi hai gate đầu trong bộ unittest; `tests/test_dashboard_v2_review.py` gọi `verify-review.cjs` và chạy `browser-check-review-phone.cjs` qua listener điện thoại thật (bỏ qua khi thiếu node, ffmpeg hoặc Playwright).
Không chạy scan, export, cleanup hay archive trên dữ liệu thật để kiểm tra demo.

Hướng dẫn mapping, tích hợp, kiểm thử và rollback:
[`docs/DASHBOARD_V2_UPDATE_GUIDE.md`](../docs/DASHBOARD_V2_UPDATE_GUIDE.md).
