# Bàn giao F – sửa lỗi nhỏ
- Nhánh: fix/small-bugs (từ main ed39653) · Commit cuối: code fe9bf89, rồi commit bàn giao này (xem `git log -1`) · Worktree: `temp\wt-fixes` · Cập nhật: 2026-10-13
- Chạy ở: PC · Trạng thái: **xong, chờ gộp** (điều phối chạy full suite trên bản gộp G6)

## Đã xong

### 1. Hủy xuất bị lọt (commit a113c7d)
- **Nguyên nhân** (A-export.md:362, nay đã tái hiện chắc chắn): `_execute` ghi stage RUNNING không điều kiện sau
  `claim_queued`, còn `cancel`/`pause_now` đọc `_active` **trước** khi ghi trạng thái. Có ba khoảng hở:
  1. Hủy rơi giữa `claim_queued` và lúc ghi RUNNING: Hủy đặt job CANCELLED và render CANCELLED, rồi worker ghi đè
     RUNNING, chạy render tới cuối, và `_after_success` ghi COMPLETED.
  2. Hủy rơi sau khi ghi RUNNING nhưng trước `Popen`: Hủy thấy stage RUNNING nhưng `_active` còn rỗng, nên không dừng
     được gì; tiến trình vẫn được khởi chạy.
  3. Hủy đọc `_active` (rỗng), worker khởi chạy, rồi Hủy mới ghi CANCELLED: tiến trình chạy tiếp vì Hủy dùng giá trị
     `_active` cũ.
  Dừng ngay (`pause_now`) có cùng ba khoảng hở.
- **Cách sửa** (`scheduler.py`, `job_store.py`; không đổi thứ tự hàng đợi hay luật "xuất trước"):
  - `JobStore.start_stage`: ghi RUNNING bằng một UPDATE có điều kiện (stage còn PENDING/FAILED_RETRYABLE và job còn ở
    trạng thái `claim_queued` đã đặt). Không ghi được thì `_execute` dừng, stage giữ đúng như Hủy/Dừng để lại
    (CANCELLED, hoặc PENDING để Tiếp tục). "Dừng sau bước này" (AFTER_STAGE) vẫn cho stage đã nhận chạy.
  - Trước mỗi `Popen`, `_execute` đọc trạng thái job và khởi chạy tiến trình trong **cùng một vùng `_lock`**; job đã
    PAUSED/CANCELLED thì không khởi chạy (đi qua nhánh lỗi sẵn có, không ghi FAILED).
  - `cancel` và `pause_now` đọc `_active` dưới `_lock` **sau** khi ghi trạng thái. Vì vậy hoặc worker thấy lệnh dừng,
    hoặc lệnh dừng thấy tiến trình và dừng nó.
  - Hằng `STOPPED_BY_USER_STATES` thay hai chỗ viết tay `{"PAUSED", "CANCELLED"}`.
- **Test** (`tests/test_scheduler.py::StopBeforeLaunchTests`, `tests/test_job_store.py`):
  - `test_a_stop_between_the_claim_and_the_running_stage_never_starts_the_export`: hook vào `claim_queued`, bấm Hủy
    (và Dừng ngay) ngay sau khi claim; kiểm `Popen` không được gọi, job CANCELLED/PAUSED, render CANCELLED/PENDING,
    không có STAGE_STARTED; job đã Dừng vẫn Tiếp tục được như một lệnh xuất.
  - `test_a_stop_after_the_running_stage_but_before_the_process_never_starts_it`: hook vào `_stage_cache.restore`
    (sau khi ghi RUNNING, trước `Popen`).
  - `test_a_cancel_racing_the_launch_still_stops_the_export`: hai luồng; Hủy ghi trạng thái đúng lúc worker khởi chạy
    tiến trình; kiểm tiến trình bị dừng.
  - `test_start_stage_only_while_the_claim_still_holds`: điều kiện của `start_stage`.
  - **Đỏ trên main:** chạy với `src` xuất từ ed39653 (`git archive`, thư mục tạm `temp\f-main-src`): 5 failure,
    1 error (`Popen` được gọi; "the cancelled render kept running"; `start_stage` chưa có). **Xanh trên nhánh:**
    `test_scheduler` + `test_job_store` 97 OK; thêm `test_job_import`, `test_skip_export`, `test_export_guards`,
    `test_control_center`, `test_export_identity`, `test_job_ocr_option`: 273 OK (1 skip).

### 2. Test chập chờn `SessionTest.test_cookies_rotated_before_a_cancel_are_kept` (commit 8196f3d)
- **Nguyên nhân:** lệnh hủy hẹn cố định sau 4 s. Cookie xoay khi trang phim trả về (Set-Cookie); máy bận thì Edge chưa
  tải xong trang phim lúc 4 s, nên cookie chưa xoay và vault giữ cookie cũ.
- **Cách sửa (chỉ test):** hủy khi thấy tín hiệu thật: trang vé (`/t/s1e1--v1080`) được gọi với cookie đã xoay. Lúc
  đó trang phim đã tải và lượt chạy đang chờ vé 30 s. Hạn tối đa 45 s (dưới hạn 60 s của lượt chạy); test kiểm thêm là
  tín hiệu đã xuất hiện. Các điều test kiểm giữ nguyên.
- **Bằng chứng:** script nháp làm trang phim trả chậm 5 s (giả lập máy bận): bản cũ FAIL (vault giữ cookie
  `BF-CANARY-…`), bản mới OK.

### 3. `net::ERR_NO_BUFFER_SPACE` trong test E2E tài khoản nguồn (commit fe9bf89)
- **Nguyên nhân** (lỗi thật ở code server, không phải rò socket):
  - Control Center, cổng điện thoại và review UI độc lập đều nghe với backlog mặc định của `socketserver` là 5.
  - Dashboard V2 khi tải mở khoảng 20 kết nối cùng lúc (server trả HTTP/1.0, mỗi request một kết nối), cộng các
    lượt poll.
  - Khi luồng server nhận kết nối chậm (tiến trình bận, tranh GIL), hàng chờ accept đầy và Windows từ chối phần
    còn lại. Edge báo `ERR_CONNECTION_REFUSED` sau khoảng 2 s thử lại SYN, hoặc hiếm hơn, `ERR_NO_BUFFER_SPACE`
    ngay lập tức (`connect()` trả WSAENOBUFS 10055).
  - Mất một script thì Dashboard V2 rơi về chế độ demo; mất một lượt poll thì có lỗi console, và
    `assert_page_clean` đánh trượt test.
- **Bằng chứng** (chi tiết trong `docs/evidence/f-fixes/err-no-buffer-space.md`, chỉ số tổng hợp, không có
  host/IP/đường dẫn):
  - Nhánh trước khi sửa: `test_download_account_e2e` chạy riêng 10 lần, đạt 7/10. Ba lần trượt đều vì
    `ERR_NO_BUFFER_SPACE`: điện thoại 390 px (`/api/status`, `/api/downloads`) và PC (`/api/downloads`).
  - Net-log của Edge: 249 ms sau khi Edge khởi động, trang mở khoảng 20 kết nối tới server PC thử trong vài ms, và
    một `connect()` trả 10055 ngay. Trang mất script nên rơi về demo.
  - Đã loại trừ cạn cổng: mẫu 1 giây cho thấy tối đa khoảng 1.170 mục TCP (TIME_WAIT khoảng 1.030), lúc lỗi khoảng
    283; dải cổng động 16.384, và không có sự kiện Tcpip 4231/4266. Không có socket rò, và bộ nhớ còn dư.
  - Tái hiện độc lập không cần BiliFlow (`docs/evidence/f-fixes/edge_burst.py`), Edge headless tải trang nhiều
    script:
    - backlog 5: 3 `ERR_NO_BUFFER_SPACE` trên 19.290 request lúc máy rảnh; khi có hai luồng Python bận thì
      1.000/1.230 `ERR_CONNECTION_REFUSED`;
    - backlog 128: 0 lỗi trên 16.800 request lúc rảnh và 0/1.230 lúc bận.
- **Cách sửa** (code, không đụng file nào thuộc khóa cache; nới không điều test nào):
  - `http_guards.LocalHTTPServer` (`ThreadingHTTPServer` với `request_queue_size = LISTEN_BACKLOG = 128`, bằng mặc
    định của `socket.listen()`);
  - `ControlCenter.serve()`, `serve_review_ui()` và `_PhoneServer` dùng lớp này. Giới hạn kết nối của cổng
    điện thoại (32 tổng, 8 mỗi IP) giữ nguyên;
  - hai server PC của test E2E (`account_e2e_fixtures.py`, `test_download_account_e2e_flows.py`) dùng cùng lớp với
    bản thật;
  - các test vá `ThreadingHTTPServer` theo tên đổi sang vá `LocalHTTPServer` (`test_http_guards.py`,
    `test_review_workflow.py`, `test_source_cleanup_http.py`).
- **Test** `tests/test_http_guards.py::ListenBacklogTests::test_a_burst_of_connections_waits_while_the_server_is_busy`:
  mở 40 kết nối cùng lúc vào từng server (Control Center qua `serve()`, review UI qua `serve_review_ui()`, cổng
  điện thoại) khi server chưa accept.
  - **Đỏ trên main** (src của ed39653): mỗi server chỉ xếp hàng 5, 35 kết nối bị từ chối với 10061.
  - **Xanh trên nhánh:** cả 40 đều được xếp hàng.
- **Phần còn lại, chưa tìm ra nguyên nhân** (người dùng chọn ngày 2026-10-13: dừng ở fe9bf89):
  - Sau khi sửa, `test_download_account_e2e_flows` vẫn trượt 1 trong 3 lần chạy (chuỗi dừng ở đó). Lần trượt là
    `test_session_expiry_mid_group_waits_then_resumes_in_order_after_a_new_sign_in`:
    `ERR_NO_BUFFER_SPACE @ /dashboard-v2/theme.css` ở trang PC, lúc trang vừa tải, trên server fixture đã có
    backlog 128.
  - Nghĩa là `connect()` của Edge tới 127.0.0.1 thỉnh thoảng vẫn trả WSAENOBUFS ngay, khoảng 1 lần trên
    20.000–30.000 kết nối. Đã loại trừ:
    - cạn cổng: tối đa 1.271 mục TCP;
    - rò socket;
    - thiếu bộ nhớ;
    - hàng chờ accept đầy.
  - Không tái hiện được bên ngoài bộ test:
    - 60.000 kết nối Python (bắn từng loạt 20, có và không có tùy chọn socket của Chromium) không lỗi;
    - 120 lần mở Edge mới, mỗi lần tải 20 script, không lỗi.
  - Mọi lần lỗi biết giờ đều rơi vào lúc tải trang ở một Edge vừa mở. Log Windows có thể nói thêm
    (TCPIP/Operational, Winsock-AFD/Operational) đang tắt và cần quyền admin để bật.
  - Ảnh hưởng khi dùng thật:
    - mất một lượt poll thì 2 s sau trang tự lấy lại;
    - mất một script lúc mở trang thì trang hiện bản demo, tải lại (F5) là hết.
  - Hướng làm tiếp nếu cần (chưa làm, cần người dùng chọn):
    1. ghi trace Windows bằng quyền admin trong lúc chạy E2E lặp lại;
    2. HTTP/1.1 keep-alive cho các server cục bộ, để một lần tải trang mở khoảng 6 kết nối thay vì khoảng 20.
       Thay đổi lớn: luồng giữ theo kết nối, giới hạn 8 kết nối mỗi IP của cổng điện thoại;
    3. Edge của trang test không có lưu lượng nền ra Internet. Hiện fixture chỉ truyền `--no-proxy-server`, nên
       Edge test vẫn kết nối dịch vụ Microsoft và QUIC. Đây là giả thuyết, chưa kiểm.

### 4. Tài liệu (commit 74b2052)
- `docs/SCAN_PERFORMANCE.md:140-143`: byte 0x08 (có từ c025486) trả lại thành `\b`, tức
  `.\scripts\benchmark-ocr-cross-frame.ps1 …`.
- `docs/PERF_INSTALL_PLAN.md:99`: số 2,1e-5 nằm ở `SCAN_PERFORMANCE.md:152` (dòng 129-131 là 3,6e-6 của các đoạn trích).
- `docs/PERF_INSTALL_PLAN.md:216`: số NVDEC hiện tại trích từ `CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md:446-457`
  (`SCAN_PERFORMANCE.md:93-95` tả đường NVDEC cũ); câu văn giữ nguyên.
- Quét lại cả hai file: không còn byte điều khiển nào (ngoài tab/xuống dòng), vẫn UTF-8, không CRLF. Số dòng không đổi,
  nên các trích dẫn `path:line` khác vào hai file (ví dụ trong `hardware_table.py`) vẫn đúng.

## Kết quả cổng
- **Khóa cache:** `git diff --name-only main...HEAD` (16 file) không có file nào thuộc khóa cache của 10 stage (adult,
  verify_adult, text, visual_logo, localize_logo, confirm_violence, animation_safety, live_safety, gore, violence;
  `stage_source_paths()` cộng danh sách cố định trong `stage_cache._tree_fingerprint`: 43 file).
  - Kiểm thêm so với main (bản `git archive main` đầy đủ): danh sách file trong khóa của từng stage, tính bằng code
    của mỗi bên, giống hệt nhau ở cả 10 stage; 43 file trong khóa có blob id giống main. **Khóa cache quét không
    đổi.**
- **Test mới của mục 1 đỏ trên main, xanh trên nhánh:** xem mục 1 (5 failure, 1 error trên src của ed39653).
- **Chạy riêng mỗi module 10 lần liên tiếp** (mỗi lần một tiến trình, `BILIFLOW_REQUIRE_E2E=1`, ở fe9bf89):
  - `tests.test_http_guards` (test mới của mục 3): 10/10;
  - `tests.test_download_account_e2e` (mục 3): 10/10 (trước khi sửa: 7/10);
  - `tests.test_download_account_e2e_flows` (mục 3): **chưa đạt**, 2 trong 3 lần đạt, chuỗi dừng ở đó (xem
    "Phần còn lại" của mục 3; người dùng chọn dừng ở fe9bf89);
  - `tests.test_download_account_sources` (mục 2): **8/10**. Test của mục 2
    (`SessionTest.test_cookies_rotated_before_a_cancel_are_kept`) đạt cả 10 lần. Hai lần trượt là một test khác trong
    cùng module, `TicketTest.test_ticket_page_failures_keep_their_own_codes`, ở hai lần chạy chậm hẳn (417 s và 486 s,
    các lần khác khoảng 300–330 s):
    - lần 9, trường hợp `challenge`: `TICKET_TIMEOUT` thay vì `SOURCE_CHALLENGE`;
    - lần 10, trường hợp `error`: `CLICK_FAILED` thay vì `TICKET_FAILED`.
    Xem "Phát hiện cho session khác". F không sửa test này vì nó ngoài 4 mục được giao.
- **Full suite:** người dùng bỏ bước này (2026-10-13). Điều phối chạy full suite trên bản gộp G6. Lượt full suite đang
  chạy (từ 20:13) được dừng lúc khoảng 20:22, không có kết quả.
- **Chạy một lần các module liên quan tới file đã sửa** (người dùng đổi cổng; ở fe9bf89; cùng môi trường như trên):
  `test_scheduler*`, `test_job_store*`, `test_http_guards`, `test_control_center*`, `test_review_workflow`,
  `test_source_cleanup_http`, `test_phone*`, `test_download_account_*` (31 module): **946 test, OK** (1 bỏ qua, 0 fail, 0 lỗi), 1.898,8 s, lúc 20:24–20:56.

## Đang dở

## Bước tiếp theo
1. Điều phối gộp `fix/small-bugs` theo điểm gộp; ghi `CHANGELOG.md`, `docs/PROJECT_STATUS.md`,
   `docs/SESSION_HANDOFF.md` (session F không sửa các file này theo prompt).
2. Nếu muốn làm tiếp phần còn lại của mục 3: chọn một trong ba hướng ở mục 3.

## Cách kiểm
- Lệnh test (từ worktree): `E:\DungChung\BiliFlow\.venv\Scripts\python.exe -m unittest <module> -v`, với
  `PYTHONPATH=<worktree>\src;<worktree>`, `TEMP`/`TMP` là `E:\DungChung\BiliFlow\temp`, và cho các test E2E
  `BILIFLOW_REQUIRE_E2E=1`, `BILIFLOW_FFMPEG`/`BILIFLOW_FFPROBE` trỏ vào `tools\ffmpeg\bin`.
- Cổng: như "Kết quả cổng" ở trên.

## Chờ người dùng
- Mục 3: người dùng đã chọn dừng ở fe9bf89 (2026-10-13); không còn gì chờ.
- Có sửa test chập chờn `TicketTest.test_ticket_page_failures_keep_their_own_codes` không (ngoài 4 mục; cách sửa gợi ý
  ở "Phát hiện cho session khác")?

## Lưu ý cho người tiếp quản
- Đã dọn: clip tổng hợp 1 s trong `input` của worktree, thư mục tạm do test để lại trong `temp` của worktree, các bản
  `git archive` của main và profile Edge thử nghiệm trong thư mục `temp` của thư mục cài; script lấy mẫu cổng đã tắt.
- Không sửa: `AGENTS.md`, `CHANGELOG.md`, `docs/PROJECT_STATUS.md`, `docs/SESSION_HANDOFF.md`, `docs/QUALITY_PLAN.md`,
  `job_purge.py`, `annotations\`, file thuộc khóa cache quét, mã xuất, ngưỡng phát hiện.
- Test vá server của Control Center hay review UI theo tên phải vá `LocalHTTPServer` (không còn
  `ThreadingHTTPServer` trong `control_center.serve()` và `serve_review_ui()`).

## Phát hiện cho session khác
- **Test chập chờn `TicketTest.test_ticket_page_failures_keep_their_own_codes`** (cùng loại với mục 2: hạn cố định
  trong test gặp lúc máy bận):
  - Test cho cả lượt lấy vé `ticket_seconds=4`; thời gian tìm trang vé và chờ trạng thái chung một hạn đó. Máy
    chậm thì trang vé `challenge` chưa kịp hiện trạng thái trước 4 s, và lượt chạy trả `TICKET_TIMEOUT`.
  - Cú click lấy vé có hạn chờ trang 10 s (`page_seconds`, cắt theo hạn của lượt chạy). Máy chậm thì click không
    xong và trả `CLICK_FAILED`.
  - Cách sửa gợi ý (chỉ test, không nới điều test kiểm): chỉ trường hợp `other` giữ hạn 4 s, vì kết quả mong đợi
    `TICKET_NOT_OPENED` chính là hết hạn. Các trường hợp khác cho hạn rộng hơn, vì mã lỗi của chúng đến từ trạng
    thái trang chứ không từ hạn.
- `golden_label_app._ExclusiveServer` vẫn nghe với backlog 5. F không sửa vì mọi thứ về golden nằm ngoài phạm vi.
- Edge của trang test E2E (`account_e2e_fixtures.open_page`) chỉ có `--no-proxy-server`, nên tự kết nối ra dịch vụ
  Microsoft (TCP 443 và QUIC) trong lúc test. Session browser của bản thật thì chặn bằng proxy hố đen.
- Các test khác dựng `ThreadingHTTPServer` cho Control Center (backlog 5) chỉ gọi bằng urllib, không tải trang bằng
  trình duyệt, nên không gặp lỗi của mục 3.
