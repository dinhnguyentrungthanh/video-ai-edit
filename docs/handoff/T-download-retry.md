# Bàn giao T – tải phim hàng loạt bị lỗi mạng
- Nhánh: fix/download-network-retry (từ origin/main 9ae104c) · Commit code: 5e882af · Worktree: `temp\wt-download-retry` ·
  Cập nhật: 2026-10-13
- Chạy ở: PC · Trạng thái: bước 2 xong, chờ điều phối chạy full suite và gộp (không push, không merge)

## Đã xong
- Bước 1 (commit e4e275f): nguyên nhân và thiết kế. Người dùng duyệt cả 4 khuyến nghị ngày 2026-10-13:
  tự thử lại cả ở bước tải; WAITING_RETRY giữ nguyên qua khởi động lại; giãn 5 s, áp dụng cả cho yt-dlp; nút hàng loạt
  gồm cả lượt INTERRUPTED (Tiếp tục).
- Bước 2: cài theo thiết kế; chi tiết ở `docs/VIDEO_DOWNLOAD_PLAN.md` mục 4.15, tóm tắt ở `CHANGELOG.md`.
  - Mới: `src/biliflow/download_retry.py`, gồm `NetworkPolicy`, `is_transient`, `retry_delay`, `HostPacer` và mixin
    `DownloadRetrySteps` (`_retry_later`, `_wake_retries`, giãn nhịp, `retry_network`).
  - Sửa:
    - `download_http.py`: `parse_retry_after`, `HttpError.retry_after` cho 429/503.
    - `download_runner.DownloadOutcome`: thêm `status`, `retry_after`.
    - `download_sources.py`: truyền hai trường đó.
    - `download_store.py`: trạng thái WAITING_RETRY, cột `retry_count`/`retry_at`/`retry_phase`.
    - `download_worker.py`: mixin, `network_policy`, dispatch, `_settle`, `stop`/`resume`.
    - `download_source_steps.py`: thăm dò, `_interrupt`, lấy lại nguồn có giãn nhịp.
    - `download_groups.py`: WAITING_RETRY tính là "chờ tải".
    - `download_api.py`: `NetworkPolicy()` cho Control Center, route `POST /api/downloads/retry-network`, snapshot
      `settings.auto_retries` và `network_failures`, trường `retry_*`.
    - `phone_access.py`: cho phép route trên điện thoại.
    - `dashboard_v2`: `download-core.js`, `download-view.js`, `download-live.js`, `contracts.js`,
      `download-fake-server.cjs`, `verify-download.cjs`.
    - test: `source_fixtures.py` (reply `drop`/`stall`), `fake_yt_dlp.py` (ghi `at`), `try_source_downloads.py`
      (Control Center thử có `NetworkPolicy()`), `test_download_api.py`, `test_dashboard_v2_downloads.py` (gate 23).
  - Test đã chạy (PYTHONPATH = `src` của worktree):
    - `tests.test_download_retry`: 30 OK.
    - `discover -p "test_download_*.py"`: 1179 test, các lỗi đều đã xử lý:
      - `test_download_api` do snapshot có thêm `auto_retries`: đã sửa test.
      - `test_download_account_api` lỗi một lần vì máy bận; chạy lại riêng thì OK.
      - 3 module E2E cần TEMP/TMP = `E:\DungChung\BiliFlow\temp`. Chạy lại với biến đó thì lộ ra 2 chỗ cần sửa:
        - `GatedReply` (`account_e2e_fixtures.py`) không có `stall`/`drop`: `FixtureServer._send` nay đọc hai trường
          đó bằng `getattr`;
        - danh sách route cố định: thêm route mới vào `EXPECTED_POST_ROUTES` (`test_download_account_e2e_flows.py`),
          vào `PHONE_POSTS_LITERAL` và hash endpoint (`test_dashboard_v2_review.py`, 92 endpoint).
        Sau khi sửa: 11/12 test E2E qua trong lượt chạy đó, còn `RouteInventoryTest` và `test_dashboard_v2_review` qua
        khi chạy lại.
    - Thử đột biến, cả 6 đều làm test hỏng: bỏ đánh thức lượt chờ; bỏ giãn nhịp ở dispatch; coi 500 là tạm thời; bỏ
      giãn nhịp lúc lấy lại nguồn; bỏ Retry-After; không thử lại ở bước tải.
    - `test_dashboard_v2_downloads`, `_contract`, `_phone_hardening`, `_phone`, `_frontend`, `_route`: OK.
    - `node dashboard_v2/verify-download.cjs`: 23/23.

## Đang dở
- (không)

## Bước tiếp theo
1. Điều phối: full suite và gộp. Không có file quét, cache, xuất hay `hardware_*` nào bị sửa (khóa cache quét không đổi).
2. Nếu người dùng muốn kiểm thật: Control Center thử (`tests/try_source_downloads.py`, root tạm trong `temp\`, cổng riêng)
   với link người dùng đưa; không bao giờ trên Control Center thật.

## Cách kiểm
- Lệnh test: `$env:PYTHONPATH="<worktree>\src"; .\.venv\Scripts\python.exe -m unittest tests.test_download_retry -v`;
  `node dashboard_v2/verify-download.cjs`; module E2E nguồn tài khoản cần `TEMP`/`TMP` trong `<install>\temp`.
- Cổng:
  - lỗi tạm thời tự thử lại đúng lịch (đồng hồ giả) và không giữ slot;
  - lỗi lâu dài không thử lại;
  - Retry-After được tôn trọng; hẹn quá 15 phút thì kết thúc;
  - mỗi host một lượt tìm nguồn, cách nhau ≥ gap;
  - nút hàng loạt chỉ đụng lượt lỗi mạng;
  - `network_policy=None` giữ hành vi cũ.

## Chờ người dùng
- Không có quyết định nào đang chờ. Kiểm thật chỉ khi người dùng đưa link và đồng ý chạy trên Control Center thử.

## Lưu ý cho người tiếp quản
- Không bao giờ thêm, tiếp tục, thử lại lượt tải hay POST `/api/downloads*` trên Control Center thật.
- Repo công khai: chỉ dùng host1/host2, `*.example`; không ghi host thật, link hay tên phim.
- `DownloadWorker` mặc định không tự thử lại; chỉ `DownloadService` và Control Center thử truyền `NetworkPolicy()`. Test cũ giữ
  nguyên hành vi (FAILED/INTERRUPTED ngay).
- Chạy test trong worktree phải đặt `PYTHONPATH` = `src` của worktree, nếu không `.venv` nạp code của thư mục chính.
