# Bàn giao T – tải phim hàng loạt bị lỗi mạng
- Nhánh: fix/download-network-retry (từ origin/main 9ae104c) · Worktree: `temp\wt-download-retry` · Cập nhật: 2026-10-13
- Chạy ở: PC · Trạng thái: chờ người dùng duyệt thiết kế (bước 1 xong, chưa sửa code)

## Đã xong
- Bước 1: đọc `state\downloads.sqlite3` (mode=ro) và code tìm nguồn/tải; nguyên nhân và thiết kế ở dưới.

## Nguyên nhân (đã xác nhận trên dữ liệu và code)
- Sự kiện 2026-10-11 09:43–09:46 (UTC trong DB), lượt 27–40, slots = 3: mọi lượt là trang của host1, provider
  `embedded-media` ("Nguồn trong trình phát trang"). 13 lượt lỗi `RemoteDisconnected` từ host2 (host của trình phát,
  khác host1), lượt 35 quá thời gian chờ từ host3. Các link được dán cách nhau ~8–10 s nên 3 lượt thăm dò chạy cùng lúc.
  Lượt 41–45 (09:48–09:51) dán thưa hơn, mỗi lượt tìm nguồn ~10 s nên gần như chạy một mình: đều xong.
- Mỗi lượt thăm dò của `embedded-media` mở một Edge ẩn; mọi yêu cầu của trang đi qua `SafeHttp`, mỗi yêu cầu là một kết nối
  TCP + TLS mới (không keep-alive). Ba trang cùng nạp một lúc thành một đợt kết nối dồn dập tới host2; host2 đóng kết nối.
  (Không đo được luật của máy chủ; dấu hiệu: chỉ lỗi khi dồn, cùng host, chạy thưa thì được.)
- Không có tầng nào thử lại:
  - `browser_source` giữ lỗi NETWORK của yêu cầu trong `bridge.failure`, trình phát không có nguồn, rồi ném lỗi đó;
  - `_probe_source` (`download_source_steps.py`) đổi mọi `SourceError`/`HttpError` thành FAILED; yt-dlp cũng vậy
    (`classify_error` → NETWORK → FAILED);
  - `SafeHttp.open` không thử lại; `with_retries` chỉ dùng cho đoạn HLS;
  - ở bước tải, NETWORK/SERVER_BUSY/DNS_FAILED thành INTERRUPTED, chờ người dùng bấm Tiếp tục;
  - 429/5xx là SERVER_BUSY nhưng `Retry-After` không được đọc; trong Edge ẩn SERVER_BUSY làm hỏng ngay.

## Thiết kế đề xuất (chờ duyệt)
### (1) Tự thử lại có giãn cách
- Lỗi tạm thời: `NETWORK` (RemoteDisconnected, reset, timeout, ngắt giữa chừng), `DNS_FAILED`, HTTP 429/502/503/504.
  Lỗi NETWORK của yt-dlp (thăm dò) cũng vậy. Không thử lại: 404/410/451, 401/403/407, đăng nhập (vẫn WAITING_LOGIN),
  TLS, luật mạng, DRM, file bị xóa, hết đĩa, SOURCE_CHANGED, PLAYER_CHANGED, 500, lỗi nội bộ.
- Lịch: 20 s, 60 s, 180 s, mỗi lần nhân ngẫu nhiên 0,8–1,2. `Retry-After` (giây hoặc ngày giờ HTTP) của 429/503: chờ
  max(lịch, Retry-After); quá 15 phút thì thôi tự thử, kết thúc như hôm nay kèm "máy chủ hẹn sau N phút".
- Trạng thái mới `WAITING_RETRY`: không giữ slot, không luồng, không trình duyệt (giống WAITING_LOGIN). Cột mới
  `retry_count`, `retry_at` (thêm bằng `ALTER TABLE ADD COLUMN`). Đến giờ thì về QUEUED, cuối hàng.
- Ở bước thăm dò: thử lại thăm dò. Ở bước tải: thử lại như Tiếp tục (giữ phần đã tải, cùng attempt).
  Mỗi bước tối đa 3 lần; qua bước thì đếm lại. Hết 3 lần: như hôm nay (thăm dò → FAILED, tải → INTERRUPTED) kèm
  "đã tự thử lại 3 lần".
- Dừng/Hủy được trong lúc chờ. Control Center khởi động lại: lượt WAITING_RETRY vẫn chờ tới `retry_at`.
- Nguồn tài khoản: WAITING_RETRY giữ link như INTERRUPTED (`KEEPS_TICKET`).
- Sự kiện `AUTO_RETRY_SCHEDULED` (WARNING, lý do đã che) và `AUTO_RETRY` (INFO). Giao diện: "Chờ thử lại (lần n/3)",
  màu vàng, đếm ngược.

### (2) Giãn nhịp tìm nguồn theo host
- Khóa theo host của link trang (`check_link`): mỗi host chỉ một lượt tìm nguồn một lúc, lần bắt đầu sau cách lần trước
  ít nhất 5 s. Tìm nguồn = PROBING (provider và yt-dlp) và lần lấy lại nguồn trước khi tải của provider.
- `dispatch` không lấy slot cho lượt cần thăm dò khi host đang bận; lượt của host khác hoặc lượt đã thăm dò xong được
  chạy trước. Lần lấy lại nguồn trong bước tải chờ khóa trong luồng (ngắn, Dừng/Hủy cắt được).
- Truyền file (HTTP, HLS, yt-dlp tải) vẫn theo slots như cũ.

### (3) Nút "Thử lại các lượt lỗi mạng"
- `POST /api/downloads/retry-network` (cùng quyền như nút Thử lại từng lượt). FAILED với mã tạm thời → Thử lại
  (attempt + 1, từ đầu); INTERRUPTED với mã tạm thời → Tiếp tục (giữ phần đã tải). Lượt trong nhóm đã hủy hoặc tập đã có
  lượt khác thì bỏ qua và báo số. Trả về số lượt đã thử lại, tiếp tục, bỏ qua.
- Nút chỉ hiện khi có ít nhất một lượt như vậy, kèm số lượt. Nhờ (2) các lượt chạy lần lượt theo host.

### File sẽ sửa
- Mới `download_retry.py` (luật lỗi tạm thời, lịch, Retry-After, khóa host, thao tác hàng loạt) để
  `download_worker.py` (845 dòng) không dài thêm.
- `download_http.py` (Retry-After vào `HttpError`), `download_store.py` (trạng thái, cột), `download_worker.py`,
  `download_source_steps.py`, `download_embedded_source.py` (giữ Retry-After), `download_api.py`, `dashboard_v2`
  (download-core.js, download-view.js, fake server), tài liệu.
- Không đụng file xuất, quét, cache, `hardware_*`, `models\`.

## Đang dở
- (không)

## Bước tiếp theo
1. Người dùng duyệt thiết kế (và các câu hỏi dưới).
2. Bước 2: test trước (fake provider + `tests/source_fixtures.py` giả ngắt kết nối, timeout, 429 có Retry-After; fake
   yt-dlp lỗi mạng rồi được), rồi code; commit.

## Cách kiểm
- Lệnh test: `.\.venv\Scripts\python.exe -m unittest tests.test_download_retry -v` và các test download liên quan;
  `node dashboard_v2/verify-download.cjs`. Full suite để điều phối chạy.
- Cổng: lỗi tạm thời tự thử lại đúng lịch và không giữ slot; lỗi lâu dài không thử lại; mỗi host một lượt tìm nguồn;
  nút hàng loạt chỉ đụng lượt lỗi mạng.

## Chờ người dùng
- Duyệt thiết kế; các câu hỏi trong tin nhắn bước 1.

## Lưu ý cho người tiếp quản
- Không bao giờ thêm, tiếp tục, thử lại lượt tải hay POST `/api/downloads*` trên Control Center thật. Kiểm thật chỉ trên
  Control Center thử (root tạm trong `temp\`, cổng riêng) với link người dùng đưa.
- Repo công khai: chỉ dùng host1/host2, `*.example`; không ghi host thật, link hay tên phim.
