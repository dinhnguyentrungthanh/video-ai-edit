# Storage policy

## Ngưỡng

- Cảnh báo khi còn dưới 200 GiB.
- Pause job mới khi còn dưới 150 GiB.
- Hard stop khi còn dưới 100 GiB hoặc 20% dung lượng ổ, lấy ngưỡng lớn hơn.

## Retention ban đầu

- Video nguồn và output: không tự xóa.
- Temp: đủ điều kiện dry-run sau 24 giờ.
- Work của job lỗi: đủ điều kiện dry-run sau 7 ngày.
- Preview: đủ điều kiện dry-run sau 30 ngày.
- Cache routing logo: nén gzip ngay khi tạo và đủ điều kiện dry-run sau 30 ngày.
- `state/brand-memory.json`: giữ lâu dài vì chỉ chứa chữ ký perceptual nhỏ và quyết định nguồn; không chứa video.
- Phase 1A chỉ liệt kê ứng viên; không xóa thật.

Sau khi BiliBili publish và playback được xác minh, phase sau sẽ tạo bản HEVC archive. Bản output lớn chỉ trở thành ứng viên dọn khi archive đã được kiểm tra, nhỏ hơn đáng kể và qua thời gian an toàn.
