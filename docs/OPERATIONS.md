# Operations

Mọi lệnh phải chạy qua `scripts/run.ps1` để cache và file tạm được chuyển về project. Phase 1A chạy thủ công, không tạo Windows Service và không tự khởi động cùng Windows.

`Ctrl+C` yêu cầu scanner dừng FFmpeg và ghi báo cáo `INTERRUPTED` khi có thể.

Sau khi model đã được tải, scanner nạp model bằng chế độ local-only. Quét video không gửi frame đến Hugging Face hoặc dịch vụ AI cloud.

Archive HEVC hiện có thể dùng CPU `libx265`. NVENC cần được đánh giá lại ở Phase 3 vì FFmpeg build hiện tại yêu cầu NVENC API 13.1 trong khi driver 576.80 cung cấp API 13.0.
