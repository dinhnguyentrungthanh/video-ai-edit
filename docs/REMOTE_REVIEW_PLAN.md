# Kế hoạch duyệt từ xa

## Phương án ưu tiên: Telegram bot dùng long polling

Máy BiliFlow chủ động kết nối ra Telegram để nhận thao tác; không mở cổng từ Internet vào máy. Bot gửi một thumbnail giới hạn dung lượng, timestamp, nhóm cảnh và bốn nút:

- `Giữ nguyên`
- `Làm mờ`
- `Cắt bỏ`
- `Cần xem thêm`

Callback chỉ chứa ID candidate và quyết định ngắn. Mọi quyết định đi qua cùng hàm ghi queue với giao diện local, vì vậy edit plan vẫn bị khóa cho tới khi tất cả mục được xử lý.

## Bảo vệ bắt buộc

- Chỉ chấp nhận Telegram `user_id` và `chat_id` trong allowlist.
- Token bot không ghi vào source code, report hoặc log. Khi triển khai trên Windows, lưu token mã hóa theo tài khoản chạy BiliFlow và chỉ giải mã trong bộ nhớ.
- Mỗi callback có queue ID, item ID và revision để từ chối thao tác cũ hoặc trùng.
- Ghi `actor`, `transport`, timestamp và action vào audit log giới hạn 1.000 sự kiện.
- Không gửi video nguồn. Mặc định chỉ gửi thumbnail đã nén; clip ngắn chỉ gửi khi người dùng bật rõ tùy chọn này.
- Sau khi hoàn thành job và hết retention, xóa media tạm ở máy. Telegram giữ bản đã gửi theo chính sách của Telegram, nên việc gửi ảnh phải được người dùng bật riêng.
- Bot chỉ duyệt candidate; không có lệnh render full, upload hoặc publish từ callback Telegram.

## Dung lượng và nhiệt độ

Telegram review không chạy model hoặc render. Máy chỉ đọc thumbnail nhỏ và gọi HTTPS nên tải CPU/GPU thấp. Tốn dung lượng mạng bằng tổng thumbnail được gửi. Preview video chỉ được render cục bộ sau khi queue đã hoàn tất và vẫn cần gate kiểm tra riêng.

## Các bước triển khai sau

1. Tạo bot và nhập token vào secret store cục bộ.
2. Ghi nhận `user_id`/`chat_id` của tài khoản được phép.
3. Thêm transport Telegram long polling, retry có backoff và checkpoint `update_id`.
4. Gửi thử một queue giả, kiểm tra callback, thao tác trùng và mất mạng.
5. Bật tùy chọn gửi thumbnail cho một video thật sau khi người dùng xác nhận phạm vi dữ liệu được gửi ra ngoài.

Một phương án khác là đặt giao diện web hiện tại sau VPN riêng. Cách đó giữ trải nghiệm đầy đủ hơn nhưng cần quản lý VPN và quyền truy cập. Telegram được ưu tiên cho lượt triển khai đầu vì nút callback phù hợp với bốn quyết định và không cần mở cổng inbound.
