# Architecture

Luồng mục tiêu: input → metadata validation → local AI scan → user review → approved FFmpeg edit plan → output validation → separately approved BiliBili upload → platform verification → HEVC archive → retention management.

Visual-logo routing dùng cache riêng theo SHA-256 nguồn và cấu hình; không dùng timestamp hoặc vùng edit của video khác. `state/brand-memory.json` lưu perceptual hash từ quyết định logo đã duyệt để tăng recall trên nguồn mới. Memory match chỉ tạo candidate và vẫn phải qua review. GPU inference và final render tiếp tục chạy qua các khóa tài nguyên riêng.

SQLite sẽ là nguồn trạng thái chính ở các phase sau. Phase 1A chưa kết nối Telegram hoặc BiliBili.
