# Dashboard V2: kế hoạch tích hợp cho Claude cloud session

- Ngày lập: 2026-10-03.
- Nhánh: `feat/dashboard-v2`, tách từ `main` f6996bb.
- Người dùng yêu cầu tách Dashboard V2 sang nhánh riêng, đẩy lên GitHub và giao phần tích hợp cho một Claude cloud session.
- Máy thật (Windows, ổ `E:`) sẽ kéo nhánh về để test những phần cloud không test được.

## 1. Bối cảnh ngắn

- **BiliFlow** là công cụ local-first:
  - quét video và tạo các thẻ cảnh cần duyệt (quảng cáo/logo, 18+, máu me, bạo lực);
  - người dùng tự duyệt KEEP / BLUR / CUT / NEEDS_MORE_CONTEXT rồi mới xuất video.
- **Control Center** (`src/biliflow/control_center.py`) chỉ nghe trên 127.0.0.1 và phục vụ:
  - dashboard hiện tại ở `/` (HTML từ `_dashboard_html()`);
  - trang duyệt ở `/review/{id}`;
  - API JSON ở `/api/...`.
- **Dashboard V2** (`dashboard_v2/`): người dùng thấy dashboard cũ khó dùng nên nhờ Codex thiết kế lại.
  - Đây mới là UI/UX chạy trên dữ liệu mẫu trong bộ nhớ, với server tĩnh riêng (`serve.py`, cổng 8794).
  - Chưa nối API thật.
- **`docs/DASHBOARD_V2_UPDATE_GUIDE.md`** là tài liệu mapping của Codex: endpoint, payload, các khóa bắt buộc, trình tự tích hợp và rollback.
  - File này **không thay** guide đó.
  - File này giao việc, đặt thứ tự ưu tiên và quy định cách đánh dấu kết quả.

Đọc theo thứ tự:
1. `AGENTS.md`: các bất biến của dự án, luôn áp dụng.
2. File này.
3. `docs/DASHBOARD_V2_UPDATE_GUIDE.md`.
4. `dashboard_v2/README.md`.
5. Code liên quan (xem mục 4).

`docs/SESSION_HANDOFF.md` và `docs/PROJECT_STATUS.md` chỉ để nắm bối cảnh chung.

## 2. Môi trường cloud: làm được gì, không làm được gì

**Cloud không có:**
- GPU, model AI, video;
- ổ `E:\` và runtime Windows (`.venv`, `runtime\`);
- Control Center đang chạy;
- database thật (`state/`), report và output thật.

Mọi đường dẫn `E:\DungChung\BiliFlow\...` trong tài liệu là của máy người dùng.

Vì vậy cloud chỉ làm phần **code và kiểm thử không cần dữ liệu thật**:
- **Node:**
  - `node dashboard_v2/verify.cjs`;
  - `node --check`;
  - test adapter bằng transport giả.
- **Python:**
  - Chỉ cài gói nhẹ khi cần để import, ví dụ `psutil`, `pyyaml`, `pillow`, `numpy`. **Không** cài torch, easyocr hay transformers, và không tải model.
  - Chạy bằng `python -m unittest <module>`. Lệnh trong `AGENTS.md` dùng `.venv\Scripts\python.exe` của Windows; trên cloud dùng `python`.
- **Các import riêng của Windows** (`msvcrt`, `winreg`) đều nằm trong hàm, nên `control_center.py` import được trên Linux.
  - Test nào chạm khóa file, Thùng rác hay model thì đánh dấu `[-]` kèm lý do.
- **File tạm** để trong `temp/` của repo (đã gitignore), không commit.

## 3. Nguyên tắc: test trước những gì không đụng logic dữ liệu

"Logic dữ liệu" gồm các phần sau, và cloud **không sửa** phần nào trong đó:
- scheduler và hàng đợi;
- `job_store` (SQLite);
- quyết định duyệt;
- renderer và xuất video, export identity, manifest;
- dọn nguồn, lưu trữ, khôi phục, Thùng rác;
- detector, threshold, model;
- bộ nhớ logo/brand;
- cache quét.

Cloud chỉ thêm lớp giao diện và một route xem riêng.

Thứ tự làm. Xong mỗi pha thì: test, đánh dấu bảng ở mục 7, commit, push, rồi mới sang pha sau.
1. **Pha 0, nền:** chạy các test hiện có ở mức môi trường cho phép, ghi lại cái gì chạy được.
2. **Pha 1, mapping:** chỉ đọc code, hoàn thiện bảng mapping và cập nhật guide.
3. **Pha 2, frontend:** không đụng backend.
   - Tách DemoStore.
   - Viết `ControlCenterAdapter`.
   - Test bằng transport giả.
4. **Pha 3, route xem thử `/dashboard-v2`:** sửa tối thiểu `control_center.py`.
   - Chỉ phục vụ file tĩnh của V2, kèm whitelist và CSP.
   - `/`, `/review/{id}` và mọi API giữ nguyên.
5. **Pha 4, máy thật:** cloud không làm, để nguyên các ô `[ ]` cho máy thật.

**Trang Tải video:**
- Chưa có downloader hay endpoint thật.
- Giữ ở dạng mô phỏng, có nhãn "mô phỏng" rõ trong route live (hoặc ẩn sau một cờ).
- Không viết downloader, không chạy lệnh shell từ trình duyệt.

## 4. Được sửa / cấm sửa

**Được sửa:**
- `dashboard_v2/**`;
- `docs/DASHBOARD_V2_UPDATE_GUIDE.md` và file này;
- test mới cho V2: `tests/test_dashboard_v2*.py` và file mới trong `tests/fixtures/`;
- `src/biliflow/control_center.py`, **chỉ** để thêm:
  - route GET `/dashboard-v2` và các asset của nó;
  - whitelist và CSP cho route đó.

  Không đổi route cũ, payload, mã lỗi, token hay logic thao tác.

**Cấm sửa.** Nếu thấy cần sửa một trong các file dưới đây, hãy dừng lại và ghi vào mục 8 "Câu hỏi cho người dùng".
- `src/biliflow/cli.py`, `__init__.py`, `__main__.py`, `stage_cache.py`, `cache_dependencies.py`.
  - Mọi thay đổi ở đây làm **mất toàn bộ cache quét** của người dùng (xem `docs/SESSION_HANDOFF.md`, mục 10).
- Module quét/detector.
- Các module backend: `scheduler.py`, `job_store.py`, `job_pipeline.py`, `review_workflow.py`, `review_evidence.py`, `export_dialog.py`, `export_guards.py`, `export_identity.py`, `final_renderer.py`, `source_cleanup*.py`, `source_archive*.py`, `recycle_bin.py`, `brand_memory.py`, `logo_memory_admin.py`, `http_guards.py`.
- `config/` và `scripts/`.
- Dashboard cũ (`_dashboard_html()`): giữ nguyên để có đường rollback.

## 5. Backend đã đổi sau khi Codex viết guide

Merge ngày 2026-10-03 (2a37496, 732b02b) đến sau khi guide được viết, nên khi xác minh mapping phải theo code hiện tại:

- **`POST /api/jobs/{id}/review/finalize`:** nếu đường dẫn xuất đã có file mà manifest không chứng minh đó là bản xuất của lần duyệt này, backend trả **400** `{"error": "Thư mục output đã có file … BiliFlow không ghi đè …"}`.
  - V2 hiện nguyên văn lý do và không tự thử lại.
- **Tên bản xuất:** tính từ các thao tác render (`export_identity.py`).
  - Chỉ file có manifest chứng minh mới được coi là đã xuất.
  - V2 không tự suy ra tên hay đường dẫn bản xuất; dùng dữ liệu backend trả về.
- **HTTP:**
  - `Content-Length` không phải số nguyên không âm → 400;
  - body JSON lồng quá sâu → 400;
  - request ngừng gửi quá 20 s → **408**;
  - server chỉ nghe `127.0.0.1` / `localhost`;
  - stream video không bị cắt.
- **Mã lỗi của `do_POST`:**
  - 403: phiên hoặc token không hợp lệ;
  - 409: `ActionConflict`; body là `{error, code, preview?}`, `code` nằm ở cấp ngoài cùng, **không** phải `error.code` (đính chính sau Pha 1, xem guide mục 8.1 #2);
  - 400: dữ liệu sai (`KeyError` / `TypeError` / `ValueError`);
  - 500: lỗi khác.
- **Quy tắc thử lại của adapter:**
  - Lệnh ghi **không** tự lặp lại. Ngoại lệ duy nhất là 403: làm mới token một lần như guide quy định.
  - GET chỉ thử lại khi người dùng bấm.
- **Xuất video ngắn** đã chạy được (giới hạn bitrate nội bộ); không ảnh hưởng UI.
- **Đoạn cuối guide** ("prototype V2 không tuyên bố sửa các lỗi export identity, finalize shortcut hay HTTP input validation"): các lỗi đó nay đã được sửa trên `main`. Cập nhật đoạn này cho đúng.

## 6. Cách đánh dấu kết quả (bắt buộc)

Bảng ở mục 7 có hai cột kết quả: **Cloud** và **Máy thật**.

| Ký hiệu | Nghĩa |
| --- | --- |
| `[x]` | Đã test và đạt. Ghi lệnh hoặc cách test và kết quả ngắn ở cột Bằng chứng. |
| `[!]` | Đã test và lỗi. Ghi lỗi; nếu đã sửa thì ghi commit sửa và test lại. |
| `[-]` | Không test được ở môi trường này. Ghi lý do; máy thật phải test. |
| `[ ]` | Chưa làm. |
| `—` | Không áp dụng cho cột này. |

- Cloud chỉ điền cột **Cloud**.
- Cột **Máy thật** để nguyên `[ ]`. Máy thật sẽ kéo nhánh về, test và điền.
- Hạng mục nào cloud làm được thì phải test và đánh `[x]` trước khi làm hạng mục phụ thuộc vào nó.
- Mỗi lần push: cập nhật bảng và thêm một dòng vào mục 9 "Nhật ký cloud".
- Không tuyên bố "đã tích hợp" khi phần máy thật chưa test.

## 7. Checklist

### A. Nền (Pha 0)

| ID | Hạng mục | Cloud | Máy thật | Bằng chứng |
| --- | --- | --- | --- | --- |
| A1 | `node dashboard_v2/verify.cjs` đạt | [x] | [x] | `node dashboard_v2/verify.cjs` → `{"passed":25,"failed":0}` (Pha 0); sau Pha 2: 28/28. **Máy thật** (Windows, node v24.21.0, c616bef): 28/28 |
| A2 | `node --check` cho mọi file `.js` / `.cjs` trong `dashboard_v2/` | [x] | [x] | `node --check` đạt cả 5 file (app, contracts, download-demo, mock-data, verify.cjs). **Máy thật:** đạt cả 9 file hiện có (6 `.js`, 3 `.cjs`) |
| A3 | Các test Python chạy được trên Linux: `tests.test_http_guards`, `tests.test_export_identity`, `tests.test_control_center` (cần node). Ghi rõ module nào chạy được, module nào `[-]` và vì sao | [x] | [x] | `tests.test_export_identity` 28/28 OK. `tests.test_control_center` 52/53: 1 lỗi `test_status_exposes_the_workers_queue_order` do thiếu PowerShell (`job_pipeline._powershell`) → `[-]` riêng test này. `tests.test_http_guards` `[-]`: import `cli.py` cần torch/timm. Chạy thêm: `test_source_cleanup_http` 20 OK, `test_source_archive_http` 8 OK, `test_export_dialog` 5 OK, `test_skip_export` 26/30 (4 lỗi cùng nguyên nhân PowerShell, `[-]`). **Máy thật** (Windows): cả ba module đạt hết, kể cả các test cloud phải để `[-]`: `test_http_guards` 8/8, `test_export_identity` 28/28, `test_control_center` 53/53 (gồm `test_status_exposes_the_workers_queue_order`), `test_skip_export` 30/30 |
| A4 | Fingerprint cache: `stage_cache._tree_fingerprint(root, stage)` của cả 10 stage trong `CACHEABLE_STAGES` phải **giống hệt** giữa commit gốc f6996bb và đầu nhánh. Lệnh mẫu ở cuối mục này | [x] | [x] | Lệnh mẫu (worktree f6996bb vs đầu nhánh 65803f7): 10 stage, `fingerprint changed for: none`. **Máy thật:** worktree tạm f6996bb vs c616bef: 10 stage giống hệt; `control_center.py` không nằm trong 33 file được băm (`temp/ui-plan/dashboard-v2-check/fingerprint-a4-d6.txt`) |

### B. Mapping (Pha 1, chỉ đọc)

| ID | Hạng mục | Cloud | Máy thật | Bằng chứng |
| --- | --- | --- | --- | --- |
| B1 | Bảng mapping trong guide: chức năng cũ → vị trí trên V2 → endpoint / payload / schema thực tế (đã đọc code) → điều kiện khóa / xác nhận → tình trạng (khớp / thiếu / mô phỏng) | [x] | — | Guide mục 8.2 (18 dòng, đọc từ `do_GET/do_POST/status()/finalize()`); 13 chênh lệch schema ở mục 8.1. Chỉ Tải video và tình huống kiểm thử là mô phỏng |
| B2 | Đối chiếu `contracts.js → endpoints` với route thật trong `control_center.py`: GET và POST, kể cả `/api/jobs/{id}/...`, `/api/logo-memory/...`, `/review/...`. Liệt kê mọi chênh lệch | [x] | — | `python -m unittest tests.test_dashboard_v2_contract` → 6 test OK: 52/52 endpoint trong `contracts.js` có route thật (16 GET, 14 POST pattern), đúng method. Không thiếu/thừa route. Chênh lệch về schema (không phải route): guide 8.1 |
| B3 | Mọi state của job (`job_store.py`) rơi vào đúng một nhóm/tab của V2; không state nào bị bỏ sót | [x] | [x] | Cùng file test: 20 state × 4 `queue_kind` (kể cả lạ) → mỗi state đúng một nhóm và **trùng** `jobTab()` của dashboard cũ (trích trực tiếp từ `_dashboard_html()`); `labels` phủ đủ 20 state. QUEUED + kind lạ → `waiting`. **Máy thật:** test đạt trên Windows. Bản xem thử (handler thật, root tạm, 11 job giả, POST bị chặn): 6 nhóm của `/` và `/dashboard-v2/` trùng số (chờ xử lý 4, chờ quét 1, đang quét 1, chờ duyệt 3, đang xuất 1, hoàn tất 1) |
| B4 | Guide đã cập nhật theo mục 5 (finalize 400, 408, export identity, …) | [x] | — | Guide: mục 8.1 #2–#6, mục 8.3, sửa đoạn cuối mục 7 và mục 4 (409 `code` ở cấp trên). Đối chiếu code + test hiện có: `tests.test_control_center` (403/408/400/409), `tests.test_export_identity` 28 OK (finalize ValueError → 400, không ghi đè file không manifest) |

### C. Frontend (Pha 2, không đụng backend)

| ID | Hạng mục | Cloud | Máy thật | Bằng chứng |
| --- | --- | --- | --- | --- |
| C1 | Tách DemoStore khỏi phần render; render chỉ nhận snapshot đã chuẩn hóa | [x] | — | `demo-store.js` (fixture + mutation) và `BFAdapter.createLiveStore` có cùng giao diện `snapshot/subscribe/dispatch/preview/fileAction/logoAction`; `app.js` chỉ đọc `store.snapshot()`. `node dashboard_v2/verify.cjs` → 28/28 (thêm: demo không nạp adapter, live không nạp fixture, chỉ adapter.js có fetch, DemoStore 409 không ghi) |
| C2 | `ControlCenterAdapter` là nơi duy nhất gọi HTTP. Lấy token qua GET `/api/session`; POST gửi `Content-Type: application/json` và `X-BiliFlow-Token` | [x] | [x] | `dashboard_v2/adapter.js`; verify.cjs kiểm không file nào khác có fetch/XHR/WebSocket. `node dashboard_v2/verify-adapter.cjs` test 1: token qua GET `/api/session`, header đúng, token không nằm trong URL, dùng lại token. **Máy thật:** verify-adapter 15/15. Bản xem thử: chỉ GET cùng origin (`/api/status` khoảng 3 s một lần, `/api/ai`, asset), 0 POST, 0 request ra ngoài |
| C3 | Test adapter bằng transport giả: đúng endpoint/body cho từng thao tác ở guide mục 4; 403 làm mới token **một** lần; 409 không replay; 400/408 hiện lý do; response cũ không ghi đè response mới | [x] | — | `node dashboard_v2/verify-adapter.cjs` → 15/15: 26 thao tác guide mục 4 đúng path/body; 403 → làm mới token đúng 1 lần, 403 lần 2 hiện lỗi (2 POST); 409 giữ `code`/`preview`, 1 POST; 400 finalize hiện nguyên văn; 408/500/mất kết nối không gửi lại; GET 403 không làm mới token; `/api/status` cũ trả về sau → bỏ; bấm đúp 1 POST |
| C4 | Draft (detector, OCR, metadata, chính sách xuất) không mất khi polling hoặc khi mở/đóng Chi tiết | [x] | [ ] | `node dashboard_v2/browser-check.cjs` (Chromium headless + API giả): draft quét (bỏ nhóm gore, OCR 8) và xuất (custom 2,5 GB) còn sau polling và sau khi đóng/mở lại; POST finalize đúng body. Draft gắn với `job_key + source_sha256 + revision`. Drawer giữ mục đang mở và focus khi polling; form AI chưa lưu không bị dựng lại. Lỗi tìm được và đã sửa trong commit này: focus trong drawer nhảy sang `summary` khác sau polling. **Máy thật:** chưa test vì máy không có Playwright để chạy `browser-check.cjs`; sẽ thử tay ở E3–E6 |
| C5 | Các khóa: NEEDS_MORE_CONTEXT hoặc pending chặn xuất; nguồn missing/cleaned/archived; export đang chạy; hủy dialog không POST; bấm đúp chỉ gửi một POST | [x] | [ ] | browser-check: NEEDS_MORE_CONTEXT còn 1 → nút Xuất disabled; Hủy dialog → 0 POST; bấm đúp Xác nhận → 1 POST; 409 hiện lý do trong dialog, không gửi lại. Khóa nguồn/xuất đang chạy: verify.cjs (missing source, render request, archived/cleaned, khóa file chung). Adapter tự chặn POST trùng khi đang bay. **Máy thật:** chưa test vì bản xem thử chặn mọi POST; thử ở E6 trên Control Center thật |
| C6 | Desktop 1280 và mobile 375 không tràn ngang; Tab/Escape đúng trong modal và drawer. Chỉ làm nếu cloud có trình duyệt headless, nếu không thì `[-]` | [x] | [ ] | browser-check (Playwright 1.56, Chromium headless): 1280 không tràn; 375 px không tràn ở overview/videos/queue/downloads/logos/settings và drawer; Tab 25 lần vẫn trong drawer; Escape đóng modal trước rồi mới đóng drawer. Lỗi tìm được và đã sửa: hero đọc `gpu.name` → trang trắng khi `gpu` null; VRAM cố định của fixture. **Máy thật (một phần, nên vẫn `[ ]`):** bản xem thử 1280 px hiển thị đúng; 375 px trang Tổng quan không tràn (`scrollWidth` 375); chưa thử Tab/Escape và các trang khác |
| C7 | Trang Tải video vẫn là mô phỏng: có nhãn rõ ở bản live (hoặc ẩn sau cờ), không gọi mạng | [x] | [ ] | Bản live có badge “MÔ PHỎNG” và khung `#download-simulation`; browser-check: thêm 3 link mẫu → 0 POST, 0 request ra ngoài origin. Không có downloader/endpoint |

### D. Route `/dashboard-v2` (Pha 3)

| ID | Hạng mục | Cloud | Máy thật | Bằng chứng |
| --- | --- | --- | --- | --- |
| D1 | GET `/dashboard-v2` và asset theo whitelist; asset ngoài whitelist trả 404; không liệt kê thư mục | [x] | [x] | `python -m unittest tests.test_dashboard_v2_route` → 9 OK. `/dashboard-v2` → 301 `/dashboard-v2/` (asset tương đối cần dấu `/`, CSP `base-uri 'none'` cấm `<base>`); `/dashboard-v2/` = `live.html`; 12 asset whitelist đúng byte; 18 đường dẫn ngoài whitelist (index.html, mock-data, demo-store, serve.py, `..`, `%2e%2e`, thư mục `assets/`, NUL, chữ hoa…) → 404 JSON, không HTML. Asset đọc từ `<code>/dashboard_v2`, không đọc gì trong project root. Thêm: Chromium mở `/dashboard-v2/` trên **handler thật** (root tạm, 4 job SQLite tạm): 4 dòng job, 0 lỗi JS, 0 vi phạm CSP, 0 POST (script `temp/real_handler_*`, không commit). **Máy thật:** `test_dashboard_v2_route` 9/9. Bản xem thử (`temp/ui-plan/dashboard-v2-check/preview_server.py`, không commit): `live.html` và đủ 12 asset whitelist trả 200 |
| D2 | `/` (dashboard cũ) và `/review/{id}` trả nội dung **như trước**, có test so sánh | [x] | [x] | Cùng file test: body `/` và `/review/1` có SHA-256 và độ dài **trùng** bản gốc f6996bb (`tests/fixtures/dashboard_v2_classic_pages.json`, tính từ worktree f6996bb); header CSP vẫn chỉ `frame-ancestors 'self'`. **Máy thật:** đạt trên Windows dù `core.autocrlf=true` (hai trang sinh từ chuỗi Python, không phụ thuộc kiểu xuống dòng của file) |
| D3 | CSP của route V2 có `connect-src 'self'`; giữ `frame-ancestors` và chống framing như route cũ | [x] | [x] | Trang V2 có thêm header CSP đầy đủ (`connect-src 'self'`, `frame-ancestors 'self'`, không `*`); header chung `frame-ancestors 'self'` và `X-Frame-Options: SAMEORIGIN` vẫn có trên trang và asset; `Cache-Control: no-store`, `nosniff`, `Referrer-Policy: no-referrer`. **Máy thật:** test đạt; bản xem thử: console 0 lỗi, 0 vi phạm CSP |
| D4 | Test Python với thư mục gốc tạm, theo mẫu `tests/test_control_center.py`: route trả 200, POST thiếu token vẫn 403 | [x] | [x] | Cùng file test: route 200; POST `/api/scheduler` thiếu token → 403 và setting không đổi; có token → 200; Host lạ → 403; POST vào `/dashboard-v2/` → 403. **Máy thật:** test đạt trên Windows |
| D5 | Chạy lại các test cũ liên quan Control Center (`tests.test_control_center`, `tests.test_source_cleanup_http`, …): không test nào đổi kết quả | [x] | [x] | Sau khi sửa: test_control_center 52/53 (1 lỗi PowerShell, như Pha 0); test_source_cleanup_http 20 OK; test_source_archive_http 8 OK; test_skip_export 26/30 (4 lỗi PowerShell, như Pha 0); test_export_identity 28 OK; test_export_dialog 5 OK; test_source_archive 37 OK; test_logo_memory_admin 14 OK (1 skip); test_review_workflow 111 OK. test_source_cleanup 47/49: 2 lỗi đường dẫn Windows (UNC, `\`) trên Linux, **giống hệt** khi chạy ở worktree f6996bb → `[-]` cho 2 test này. **Máy thật:** full suite 1215 test OK (25 skip). Mọi test cloud phải để `[-]` đều chạy và đạt trên Windows: `test_control_center` 53/53, `test_skip_export` 30/30, `test_source_cleanup` 49/49 |
| D6 | Lặp lại A4 sau khi sửa `control_center.py`: fingerprint cache không đổi | [x] | [x] | Lệnh mẫu A4 sau khi sửa: 10 stage, `fingerprint changed for: none`. `control_center.py` và `dashboard_v2/` không nằm trong 33 file nguồn của `stage_source_paths`. **Máy thật:** như A4 (c616bef đã gồm bản sửa `control_center.py`) |

### E. Máy thật (cloud không làm)

| ID | Hạng mục | Cloud | Máy thật | Bằng chứng |
| --- | --- | --- | --- | --- |
| E1 | Kéo nhánh về một worktree. Chạy full suite trên Windows (`.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`) và `node dashboard_v2/verify.cjs` | — | [x] | 2026-10-04, c616bef, worktree `temp/wt-dashboard-v2`, `.venv` của thư mục chính và `PYTHONPATH=src`: 1215 test OK (25 skip, 159 s); verify.cjs 28/28; verify-adapter 15/15. Worktree cần một `input/placeholder.mp4` rỗng (đã gitignore) cho 28 test của `test_job_pipeline` và `test_job_ocr_option`, vì chúng lấy một `.mp4` trong `input/`. Log: `temp/ui-plan/dashboard-v2-check/full-suite-2.log` |
| E2 | Khởi động lại Control Center trên code nhánh, chỉ khi người dùng đồng ý và không có job nào chạy | — | [ ] | |
| E3 | So `/dashboard-v2` với `/` (chỉ GET): số video từng nhóm, hàng đợi, tiến độ, revision và lỗi của toàn bộ job thật | — | [ ] | Chưa làm trên dữ liệu thật. Trên dữ liệu giả, 6 nhóm đã trùng nhưng có 2 chỗ hiển thị lệch (mục 11) |
| E4 | Mở trang duyệt từ V2 đúng job; ảnh, khung hình và video hiện đúng | — | [ ] | |
| E5 | Thao tác an toàn do người dùng bấm: ẩn/hiện một job đã hủy; tạm dừng/tiếp tục hàng đợi khi rảnh | — | [ ] | |
| E6 | Xuất một video do người dùng bấm từ V2: kiểm ba lựa chọn dung lượng trong dialog, xuất thật một lần | — | [ ] | |
| E7 | Dọn / lưu trữ / khôi phục: chỉ xem preview trong V2; thao tác thật chỉ người dùng tự bấm (theo `AGENTS.md`) | — | [ ] | |
| E8 | Rollback: `/` vẫn là dashboard cũ; tắt V2 không đổi dữ liệu | — | [ ] | |
| E9 | Trước khi merge vào `main`: bỏ bản `dashboard_v2/` chưa track và các ghi chú V2 chưa commit trong thư mục chính `E:\DungChung\BiliFlow`. Chúng giống hệt bản trên nhánh; nếu không bỏ, git sẽ từ chối merge | — | [ ] | |

Lệnh mẫu cho A4 và D6, chạy từ gốc repo:

```bash
git worktree add ../bf-base f6996bb
python - <<'EOF'
import sys
from pathlib import Path
sys.path.insert(0, "src")
from biliflow.stage_cache import CACHEABLE_STAGES, _tree_fingerprint
base, head = Path("../bf-base").resolve(), Path(".").resolve()
changed = [s for s in sorted(CACHEABLE_STAGES) if _tree_fingerprint(base, s) != _tree_fingerprint(head, s)]
print("fingerprint changed for:", changed or "none")
EOF
git worktree remove ../bf-base
```

## 8. Câu hỏi cho người dùng (cloud ghi thêm vào đây)

**Người dùng đã trả lời câu 1–6 ngày 2026-10-04:** xem mục 12.1. Câu hỏi mới của đợt 2 đánh số tiếp từ 7.

Câu hỏi đợt 2 (cloud, 2026-10-04). **Người dùng đã trả lời câu 7–12** (xem 12.6). Câu 10 đã làm sau khi giải thích lại bằng ảnh.

7. **Nhớ logo khi duyệt qua điện thoại.** Lệnh duyệt cảnh (`review/decision`) có tùy chọn `remember_studio_logo` / `remember_platform_logo`, ghi **thêm** vào bộ nhớ logo. Hiện nó vẫn chạy qua điện thoại, vì là một phần của duyệt cảnh, không phải sửa/xóa bộ nhớ. Giữ, hay muốn chặn riêng tùy chọn nhớ logo khi duyệt qua điện thoại?
8. **Chọn địa chỉ Wi-Fi.** Giống Golden Label, listener nghe địa chỉ của card mạng đi ra mạng nhà (route mặc định). Nếu PC bật VPN hoặc có card ảo (Hyper-V/WSL) mang địa chỉ 10.x/172.x, địa chỉ chọn được có thể là của VPN, tức là mở cho mạng VPN đó. Giữ cách tự chọn (cửa sổ in rõ địa chỉ), hay muốn cho chọn/khóa địa chỉ cụ thể?
9. **Khóa nhập mã.** Bất kỳ ai trong Wi-Fi nhà cũng có thể nhập sai 10 lần để khóa nhập mã; khi đó bạn phải tắt rồi bật lại trên PC. Điện thoại đã vào trước đó vẫn dùng tiếp. Chấp nhận?
10. **Trang duyệt cũ ở 375 px (P9).** Trang dùng được, không tràn ngang. Đề xuất cho đợt sau (cần sửa `/review/{id}` và cập nhật fixture D2 trong cùng commit):
    - (a) thêm dấu hiệu cuộn cho hàng chip bộ lọc;
    - (b) ẩn nhãn “phím 1–4” trên màn hình cảm ứng;
    - (c) cho 4 nút quyết định dính đáy màn hình khi cuộn qua video;
    - (d) chữ nhỏ nhất 10 px nên tăng lên ≥ 12 px.

    Làm mục nào?
11. **`/` trên điện thoại** chuyển thẳng sang `/dashboard-v2/`; dashboard cũ chỉ mở trên PC. Nút “Quay lại Dashboard” của trang duyệt cũ trên điện thoại vì vậy cũng về V2. Giữ?
12. **Mã trong link.** Theo mẫu Golden Label, link in ra có `?code=…`, nên mã nằm trong lịch sử trình duyệt của điện thoại cho tới lần bật sau. Giữ, hay chỉ in link không mã và luôn nhập mã tay?

Câu hỏi đợt 3 (cloud, 2026-10-04):

13. **Fixture D2 không phải đổi.** Dòng báo "Đang mở cho điện thoại" ở `/` chỉ thêm khi chế độ điện thoại **đang bật**. Khi tắt (mặc định), `/` vẫn trùng byte với fixture f6996bb, nên fixture không cập nhật; test mới kiểm cả hai trạng thái. Mục 13 có dặn cập nhật fixture; giữ cách này?
14. **Nhật ký trên khung PC chỉ của lần chạy hiện tại.** Khung "Mở trên điện thoại" hiện 10 event gần nhất từ bộ nhớ của Control Center đang chạy. Event vẫn được lưu lâu dài trong nhật ký sự kiện (`PHONE_*`), nhưng sau khi khởi động lại thì khung trống cho tới event đầu tiên. Có cần đọc lại từ nhật ký khi khởi động không?
15. **Tự tắt 8 giờ không gia hạn.** Đang dùng điện thoại vẫn bị tắt đúng 8 giờ sau khi bật; muốn dùng tiếp thì bật lại (có mã mới). Giữ, hay muốn có nút "Gia hạn thêm 8 giờ" trên PC?
16. **Đổi địa chỉ khi bật/tắt VPN.** Việc kiểm địa chỉ mỗi phút dùng cùng cách dò như lúc bật. Bật hoặc tắt VPN có thể đổi địa chỉ dò được, và chế độ điện thoại sẽ tự tắt (lý do "đổi địa chỉ"). Chấp nhận?

1. **E9 đã thay đổi:** nhánh nay có thêm file V2 mới (`adapter.js`, `demo-store.js`, `live.html`, `verify-adapter.cjs`, `browser-check.cjs`) và đã sửa `app.js`, `index.html`, `verify.cjs`, `serve.py`. Bản `dashboard_v2/` chưa track trong `E:\DungChung\BiliFlow` **không còn giống** bản trên nhánh. Trước khi checkout/merge, hãy dời bản đó ra ngoài repo (không xóa, nếu bạn muốn giữ). Bạn đồng ý không?
2. **Nơi đọc asset:** route đọc `dashboard_v2/` cạnh mã nguồn (`src/biliflow/../../dashboard_v2`, đúng khi `PYTHONPATH=src` như `scripts/env.ps1`), không đọc từ project root/dữ liệu. Thiếu thư mục thì trả 404, không lỗi. Giữ như vậy?
3. **`render_request` ở bản live** được suy ra từ `current_stage === "render"` + state PAUSED/FAILED/INTERRUPTED_RECOVERABLE, chỉ để khóa nút (backend vẫn quyết định; 409/400 được hiển thị). Guide cũ dặn “không invent render request”. Chấp nhận cách suy ra này, hay muốn bỏ (chỉ dựa vào 409)?
4. **Trang Tải video:** đang để hiện ở bản live với badge “MÔ PHỎNG” và khung cảnh báo, không ẩn sau cờ. Muốn ẩn hẳn khỏi bản live không?
5. **Fixture trang cũ** (`tests/fixtures/dashboard_v2_classic_pages.json`) khóa SHA-256 của `/` và `/review/1` theo f6996bb. Nếu sau này `main` cố ý sửa dashboard cũ hay trang duyệt, test này sẽ báo và cần cập nhật fixture. Chấp nhận?
6. **403 vì Host sai** (không phải token) cũng làm adapter lấy token mới và gửi lại đúng một lần trước khi báo lỗi. Hai lần 403 là vô hại vì backend từ chối trước khi chạy gì. Giữ, hay muốn phân biệt theo nội dung lỗi?

## 9. Nhật ký cloud (mục mới nhất ở trên cùng)

**Tóm tắt phiên cloud đợt 3, 2026-10-04 (H1–H6 xong trên cloud; chưa test trên máy thật):**

- **Đã khớp (đã test trên cloud):**
  - đường dẫn hỏng → 400 không traceback; giới hạn 32 kết nối; 5 s khi chưa có cookie; log lỗi một dòng;
  - danh sách POST cho phép có test phân loại đủ mọi route; Visual AI Audit chỉ trên PC;
  - tự tắt sau 8 giờ / đổi địa chỉ, có lý do tắt; dòng báo ở `/` khi đang bật;
  - event `PHONE_*` có IP, không mã/cookie; khung PC hiện 10 event;
  - `try_code` trả cookie trong một lần giữ khóa; hướng dẫn rule Firewall riêng;
  - `127.0.0.1:8765` không đổi ngoài dòng báo; fingerprint 10 stage không đổi.
- **Còn thiếu / chưa kiểm:** H5 và dòng báo của launcher (không có PowerShell); toàn bộ cột Máy thật ở 12.4 và 13.3; P6–P9 và E2–E8 trên điện thoại, laptop, dữ liệu thật; câu 13–16.
- **Vẫn là mô phỏng:** trang Tải video; bản demo `index.html`.

| Ngày | Commit | Việc đã làm | Test đã chạy | Còn lại |
| --- | --- | --- | --- | --- |
| 2026-10-04 | a7514df, (commit docs này) | Đợt 3: H1–H6 (mục 13.1) trong `phone_access.py`, `control_center.py`, `Start-BiliFlow.ps1`, V2 (khung PC, khóa Visual audit), hướng dẫn; test mới `tests/test_dashboard_v2_phone_hardening.py`; điền 13.3, câu 13–16 | test_dashboard_v2_* 57 OK (contract 10, frontend 3, route 9, status 2, phone 15, hardening 18); verify 28/28; verify-adapter 17/17; browser-check 17/17; các test Control Center như đợt trước (6 lỗi PowerShell); Chromium thật: đăng nhập điện thoại, trang duyệt 375 px; A4 `none` | Máy thật kiểm đợt 3; người dùng test toàn bộ rồi mới merge |

Nhật ký đợt 2:

**Tóm tắt phiên cloud đợt 2, 2026-10-04 (12.2 và 12.3 xong trên cloud; chưa test trên máy thật):**

- **Đã khớp (đã test trên cloud):**
  - G1/G2: dòng trạng thái video gốc trùng chữ với dashboard cũ (16 trường hợp);
  - G3: `render_request` do backend gửi;
  - chế độ điện thoại P1–P5: cookie mã, khóa sau 10 lần sai, Host/Origin/token, 10 thao tác chỉ-PC → 403, bật/tắt chỉ từ `127.0.0.1`, mã mới mỗi lần bật, chỉ bind IPv4 riêng;
  - `127.0.0.1:8765` không đổi (D2 trùng byte); fingerprint 10 stage không đổi;
  - V2 có khung "Mở trên điện thoại" và dùng được ở 375 px qua listener điện thoại;
  - đăng nhập trong Chromium thật chạy ở cả hai cách.
- **Còn thiếu / chưa kiểm:**
  - P6: file khởi động (cloud không có PowerShell);
  - P7/P8: điện thoại và laptop thật trong Wi-Fi nhà;
  - Windows Firewall và địa chỉ Wi-Fi thực tế;
  - câu 7–12 ở mục 8.
- **Vẫn là mô phỏng:** trang Tải video; bản demo `index.html`.

| Ngày | Commit | Việc đã làm | Test đã chạy | Còn lại |
| --- | --- | --- | --- | --- |
| 2026-10-04 | (commit này, sau cdcfa5e) | Câu 10: trang duyệt cũ qua listener điện thoại có mũi tên chip, ẩn “phím N” trên cảm ứng, nút quyết định cố định ở đáy, chữ ≥ 12 px; PC không đổi | test_dashboard_v2_phone 15 OK (thêm test trang duyệt điện thoại = trang PC + 2 khối chèn); route 9 OK; review_workflow 111 OK; verify 28/28; browser-check 17/17; Chromium 375 px cảm ứng: mũi tên ẩn khi cuộn hết, nút đáy ở y = 740/740, không chữ < 12 px, trang PC không có khối chèn; A4 `none` | P6–P8 và cột Máy thật (P9: xem trang duyệt trên điện thoại thật) |
| 2026-10-04 | cdcfa5e | Câu 10–12: câu 11 giữ; câu 12 bỏ đăng nhập bằng `?code=` (chỉ nhập tay), file khởi động và khung PC in link không mã; câu 10 giải thích lại | test_dashboard_v2_phone 14 OK; verify 28/28; verify-adapter 17/17; browser-check 17/17; contract/route/status/frontend OK; Chromium thật: link `?code=` không vào được, gõ mã vào được | Câu 10; P6–P8 và cột Máy thật |
| 2026-10-04 | a2cc434 | Câu 7–9 của người dùng (12.6): giữ nhớ logo khi duyệt qua điện thoại, tự chọn địa chỉ; thêm khóa mở đặc biệt `2007` kiểu chỉ-mở-khóa, giới hạn 5 lần sai và 3 lần gỡ mỗi lần bật; khung PC hiện lượt gỡ còn lại; hướng dẫn và guide cập nhật | test_dashboard_v2_phone 13 OK; verify 28/28; verify-adapter 17/17; browser-check 17/17; contract/route/status/frontend OK; Chromium đăng nhập thật OK; A4 `none` | Câu 10–12; P6–P8 và cột Máy thật |
| 2026-10-04 | ef9ad52, a80ab59, 4f0c349 | Đợt 2: G1–G3 (12.2); chế độ điện thoại (12.3): `src/biliflow/phone_access.py` (chỉ `control_center.py` import), listener điện thoại + `/api/phone-mode`, khung V2, `Start-BiliFlow-Phone.cmd` + `Start-BiliFlow.ps1 -Phone`, hướng dẫn `docs/DASHBOARD_V2_PHONE.md`; điền 12.4, câu 7–12 | verify 28/28; verify-adapter 17/17; browser-check 17/17; test_dashboard_v2_* 33 OK (contract 10, frontend 3, route 9, status 2, phone 9); các test Control Center như đợt 1; A4 `none`; Chromium trên handler thật (đăng nhập điện thoại, trang duyệt 375 px) | P6–P8 và cột Máy thật của 12.4; câu 7–12 |

Nhật ký đợt 1:

**Tóm tắt phiên cloud 2026-10-03 (Pha 0–3 xong; Pha 4 chưa làm, V2 chưa được coi là đã tích hợp):**

- **Đã khớp (đã test trên cloud):**
  - 52/52 endpoint của `contracts.js` có route thật, đúng method;
  - 20 state job vào đúng một nhóm, trùng dashboard cũ;
  - adapter: token, 403 làm mới một lần, không lặp lệnh ghi, 409 `code`/`preview`, 400/408/500, chống response cũ, bấm đúp một POST;
  - presenter chỉ đọc snapshot; draft quét/xuất/AI giữ qua polling;
  - route `/dashboard-v2/` có whitelist và CSP; `/` và `/review/{id}` trùng byte với f6996bb; fingerprint cache không đổi.
- **Còn thiếu / chưa kiểm:**
  - mọi thứ trên dữ liệu và Control Center thật (nhóm E, cột Máy thật);
  - full suite trên Windows (torch, PowerShell, đường dẫn Windows);
  - hiệu năng polling 3 s với số job thật.
- **Vẫn là mô phỏng:**
  - trang Tải video (cả hai bản);
  - trong bản demo (`index.html`): toàn bộ dữ liệu, cảnh duyệt minh họa, tình huống kiểm thử.
  - Bản live không có cảnh duyệt; nút Duyệt cảnh mở `/review/{id}` cũ.

| Ngày | Commit | Việc đã làm | Test đã chạy | Còn lại |
| --- | --- | --- | --- | --- |
| 2026-10-03 | c616bef | Đính chính tài liệu cho khớp code: mục 5 của kế hoạch (409 `code` ở cấp ngoài, không phải `error.code`); guide mục 4 và mục 2 (danh sách job lấy từ `/api/status`, không từ `/api/jobs`) | Chỉ sửa docs, không chạy test | Như dòng dưới |
| 2026-10-03 | 4c76a34 | Điền hash commit vào nhật ký | — | Như dòng dưới |
| 2026-10-03 | 86af3ad | Route `/dashboard-v2/` trong `control_center.py` (+53 dòng: whitelist 12 asset, CSP riêng, 301 khi thiếu `/`), `tests/test_dashboard_v2_route.py`, fixture hash trang cũ; tóm tắt phiên, câu hỏi mục 8 | test_dashboard_v2_route 9 OK; D5: các test Control Center như Pha 0; D6 không đổi; Chromium trên handler thật OK | Pha 4 (máy thật): toàn bộ cột Máy thật, nhóm E |
| 2026-10-03 | 4cb9c40 | Tách DemoStore, viết ControlCenterAdapter + live store, `live.html`, gate `verify-adapter.cjs`, `browser-check.cjs` (API giả), `tests/test_dashboard_v2_frontend.py`; sửa 2 lỗi do browser-check tìm ra (focus drawer, `gpu` null) | verify.cjs 28/28; verify-adapter 15/15; browser-check 14/14; test_dashboard_v2_frontend + contract 9 OK | Pha 3 (route `/dashboard-v2/`). Lần push đầu của pha 0–1 bị 403 (quyền GitHub); push lại cùng pha 2 thành công |
| 2026-10-03 | ff1ccdb | Pha 0: chạy nền A1–A4. Pha 1: đối chiếu route/schema/state với code, thêm `tests/test_dashboard_v2_contract.py`, guide mục 8, sửa guide mục 4 và 7 | verify.cjs 25/25; node --check; test_dashboard_v2_contract 6 OK; test_export_identity 28 OK; test_control_center 52/53 (1 lỗi thiếu PowerShell); A4 không đổi | Pha 2 (frontend), Pha 3 (route) |

## 10. Quy tắc commit và bàn giao

- **Push:** chỉ push lên `feat/dashboard-v2`; người dùng đã cho phép riêng nhánh này.
  - Không push hay merge vào `main`.
  - Không force-push, không viết lại lịch sử.
- **Commit:**
  - Theo dạng `feat:` / `fix:` / `docs:` / `test:`.
  - **Không** thêm dòng ghi công (`Co-Authored-By`, "Generated with …").
- **Cuối mỗi phiên cần có:**
  - bảng ở mục 7 đã cập nhật;
  - nhật ký ở mục 9;
  - câu hỏi ở mục 8;
  - một đoạn tóm tắt ở đầu mục 9: phần đã khớp, phần còn thiếu, phần vẫn là mô phỏng.

## 11. Nhật ký máy thật (mục mới nhất ở trên cùng)

| Ngày | Commit | Việc đã làm | Kết quả | Còn lại |
| --- | --- | --- | --- | --- |
| 2026-10-04 | 949f935 (đợt 2) | Kéo đợt 2 về. Sửa 2 lỗi chỉ có trong test và chỉ lộ ra trên Windows (dưới bảng). Một agent riêng review bảo mật chế độ điện thoại; nó chỉ đọc code và không mở cổng nào | Full suite 1236 OK (25 skip) sau khi sửa test; verify 28/28; verify-adapter 17/17; fingerprint 10 stage `none`; không file cấm nào bị sửa. Review bảo mật: không có lỗi nghiêm trọng hay cao; 2 trung bình, 4 thấp (S1–S6 dưới bảng) | P6–P9 trên máy thật; sửa S1–S6 |
| 2026-10-04 | c616bef (bảng này commit ở 6df60d3) | Kéo nhánh về `temp/wt-dashboard-v2`; điền cột Máy thật ở mục 7. Bản xem thử chỉ đọc trên handler thật: root tạm `temp\v2-preview-*`, 11 job giả, cổng 8796, chặn mọi POST, không chạy scheduler/watcher, không đọc dữ liệu thật | Full suite 1215 OK (25 skip); verify 28/28; verify-adapter 15/15; fingerprint 10 stage không đổi; V2 trùng 6 nhóm với `/`; 0 lỗi console/CSP; 375 px không tràn. **Tìm được 2 chỗ V2 hiển thị sai hoặc thiếu so với `/`** (dưới bảng) | Sửa 2 chỗ dưới bảng. Câu hỏi mục 8 chờ người dùng trả lời. C4, C5, C6 (Tab/Escape), C7 và E2–E9 cần Control Center thật chạy code nhánh, nghĩa là phải khởi động lại; việc này chỉ làm khi người dùng đồng ý và không có job nào chạy |

Máy thật tìm được hai lỗi, chưa sửa (để phiên cloud sau). Cả hai chỉ là lỗi hiển thị: nút thao tác vẫn bị khóa đúng (`contracts.js` → `locked`) và backend vẫn quyết định.

1. **Video gốc không còn trong input nhưng V2 ghi "Có trong input".**
   - Vị trí: `dashboard_v2/app.js:221`, mục "Video gốc & thông tin kỹ thuật". Điều kiện chỉ xét `archived` rồi `cleaned`, mọi trường hợp còn lại đều in "Có trong input"; không xét `source_present === false`.
   - Dòng trong danh sách cũng không báo thiếu video gốc.
   - Dashboard cũ báo "Không còn video gốc trong input" hoặc "Thiếu video gốc".
   - Tái hiện: bản xem thử, job #10 (`source_present: false`).
2. **Thiếu thông báo "lần trước không thành công".**
   - Dashboard cũ hiện "Lần dọn trước không thành công: …" và "Lần lưu trữ trước không thành công: …" khi dòng `source_cleanup` / `source_archive` mới nhất có `state: FAILED`, kèm `error`.
   - V2 không hiện thông báo này.

**Đợt 2: máy thật sửa 2 lỗi trong test.** Cả hai nằm ở test, không ở code chạy:

1. **Đọc output của node sai mã hóa.**
   - `tests/test_dashboard_v2_contract.py` và `tests/test_dashboard_v2_frontend.py` đọc output của node mà không khai báo `encoding="utf-8"`.
   - Trên Windows, Python giải mã bằng cp1252 nên chữ tiếng Việt bị vỡ: 3 test contract lỗi, có cả test so 16 trường hợp G1/G2.
   - Đã thêm `encoding="utf-8"` ở cả 3 lời gọi.
2. **Test P4 phụ thuộc mạng của máy chạy test.**
   - `test_p4_only_a_private_ipv4_address_is_ever_bound` gọi `PhoneAccess().enable(address=None)`. Với `address=None`, code tự dò địa chỉ; trên PC có Wi-Fi thật, nó dò ra 192.168.x hợp lệ, nên test sai.
   - Đã gán `lan` giả cho lời gọi này.
   - Không test nào bind vào địa chỉ Wi-Fi thật: các test dùng 127.0.0.1, và test bật qua HTTP đã gán `_lan` thành 127.0.0.1 nên bị từ chối trước khi bind.

**Review bảo mật chế độ điện thoại (máy thật, 2026-10-04).** Kết luận: dùng được trong Wi-Fi nhà theo thiết kế.
- Không tìm được đường vượt cổng cookie: 30 GET và 8 POST không có cookie đều nhận 401.
- Danh sách chỉ-PC hiện đủ.
- Mã không đoán được: tối đa khoảng 40 lần thử mỗi lần bật.
- Mã không nằm trong URL, file trạng thái, log hay event.

Nên sửa ở đợt sau:

- **S1 (trung bình). Thiết bị trong Wi-Fi không cần mã vẫn làm phình log và giữ thread.**
  - Nguyên nhân 1: `urllib.parse.urlparse(self.path)` ở `do_GET`/`do_POST` của listener điện thoại chạy sau bước kiểm Host, trước bước kiểm cookie, và không nằm trong `try`. Request có đường dẫn hỏng (vd. `GET http://[x/`) gây `ValueError`, rồi `handle_error` ghi khoảng 1,5 KB traceback vào `logs\control-center\*.err.log` cho mỗi request.
  - Nguyên nhân 2: listener không giới hạn số kết nối, nên kết nối gửi rất chậm sẽ giữ thread của chính tiến trình chạy scheduler.
  - Sửa:
    - bắt lỗi `urlparse` và trả 400 (làm cả ở listener PC);
    - `handle_error` của listener điện thoại chỉ ghi một dòng, có giới hạn tần suất;
    - giới hạn khoảng 32 kết nối đồng thời;
    - đặt hạn chót ngắn (khoảng 5 s) cho tới khi có cookie.
- **S2 (trung bình). Danh sách chỉ-PC là danh sách cấm theo đường dẫn chính xác**, nên route POST thêm sau này sẽ mặc định mở cho điện thoại.
  - Sửa: đổi sang danh sách cho phép (có regex), mặc định 403. Thêm test liệt kê mọi route của `do_POST` và báo lỗi khi có route chưa phân loại.
  - Người dùng cần quyết: `POST /api/jobs/{id}/ai-audit` với `visual: true` gửi tối đa 36 ảnh thumbnail cho Codex. Có cho làm qua điện thoại không?
  - Hướng dẫn cần nói rõ: duyệt cảnh qua điện thoại có thể thêm hoặc bỏ bản ghi logo đã nhớ (câu 7). Chỉ trang Bộ nhớ logo mới là chỉ-PC.
- **S3 (thấp). Vòng đời chế độ điện thoại.**
  - Hiện tại: không tự tắt; không kiểm lại khi địa chỉ Wi-Fi của PC đổi; dashboard cũ và `Start-BiliFlow.cmd` không báo chế độ điện thoại đang bật.
  - Đề xuất: tự tắt sau khoảng 8 giờ hoặc khi địa chỉ đổi; báo trạng thái ở dashboard cũ và ở launcher.
- **S4 (thấp). Không ghi event** khi bật/tắt, nhập sai, khóa hay gỡ khóa.
  - Đề xuất: ghi event, không ghi mã, kèm IP thiết bị.
- **S5 (thấp). Launcher có thể báo lỗi sai trạng thái.** Khi gặp lỗi, launcher báo lỗi mà không kiểm trạng thái thật, nên chế độ có thể đã bật trong khi cửa sổ báo lỗi.
  - Đề xuất: sau lỗi không phải HTTP, gọi GET `/api/phone-mode` rồi báo đúng trạng thái.
- **S6 (thấp). Các chỗ nhỏ:**
  - `try_code` và `set_cookie_header` lấy khóa hai lần; nên cho `try_code` trả luôn cookie.
  - Một cookie dùng chung cho mọi thiết bị trong một lần bật.
  - Hướng dẫn Firewall nên ưu tiên một rule riêng cho TCP 8767, mạng Private, LocalSubnet, thay vì cho python.exe qua mọi cổng.

## 12. Đợt 2 (giao ngày 2026-10-04): việc cho phiên cloud tiếp theo

Đọc mục 11 trước. Mọi quy tắc ở mục 2, 4, 6, 10 và `AGENTS.md` vẫn giữ nguyên.

Thứ tự làm: 12.2 trước, 12.3 sau. Bảng kết quả ở 12.4.

### 12.1. Quyết định của người dùng cho câu hỏi 1–6 (mục 8)

| # | Quyết định | Việc cần làm |
| --- | --- | --- |
| 1 | Đồng ý | Cloud không làm gì. Lúc merge, máy thật dời bản `dashboard_v2/` cũ sang `temp\` và trả 3 file ghi chú về như `main` (E9). |
| 2 | Giữ cách đọc asset cạnh mã nguồn | Không đổi. |
| 3 | Chọn (c): backend tự gửi `render_request` | Làm G3. Bỏ cách V2 tự suy ra. |
| 4 | Giữ trang Tải video ở bản live, có nhãn MÔ PHỎNG | Không đổi. |
| 5 | Chấp nhận fixture trang cũ | Khi cố ý sửa `/` hoặc trang duyệt thì cập nhật fixture trong cùng commit. |
| 6 | Giữ: 403 vẫn lấy token mới và gửi lại một lần | Không đổi. |

### 12.2. Sửa hiển thị và `render_request` (làm trước)

- **G1. Thiếu video gốc.** Khi job có `source_present === false`:
  - Mục "Video gốc & thông tin kỹ thuật" (`dashboard_v2/app.js:221`) phải ghi "Không còn video gốc trong input", không ghi "Có trong input".
  - Dòng trong danh sách và trạng thái chính cũng phải báo thiếu video gốc, cùng câu chữ và điều kiện như dashboard cũ.
    - Lấy từ `_dashboard_html()`: `SOURCE_MISSING_MESSAGE`; "Thiếu video gốc" cho READY_TO_EXPORT; "Không còn video gốc trong input".
- **G2. Lần dọn / lưu trữ trước không thành công.**
  - Hiện "Lần dọn trước không thành công: {error}" và "Lần lưu trữ trước không thành công: {error}".
  - Điều kiện giống dashboard cũ: dòng `source_cleanup` / `source_archive` mới nhất có `state: FAILED` và video gốc còn.
- **G3. Backend gửi `render_request`.**
  - Trong `status()` của `control_center.py`, thêm cho mỗi job `render_request = self.store.render_request(job_id) is not None`.
    - Đây là đúng định nghĩa backend dùng để chặn: stage `render` ở PENDING, RUNNING, FAILED_RETRYABLE hoặc FAILED.
  - Chỉ **thêm** trường, không đổi trường cũ. Không sửa `job_store.py`.
  - Adapter dùng giá trị backend, bỏ phép suy ra từ `current_stage`. Cập nhật guide mục 8.1 và 8.3.
  - Test Python: `/api/status` trả `render_request` đúng cho job có và không có lệnh xuất treo. Test JS: adapter giữ nguyên giá trị backend.
- Sau G1–G3, chạy lại: verify.cjs, verify-adapter.cjs, các test Python liên quan, D2 (trang cũ trùng byte), A4/D6 (fingerprint).

### 12.3. Mở BiliFlow trên điện thoại hoặc laptop trong Wi-Fi nhà

**Mục tiêu.** Có một file khởi động trên PC, đề xuất tên `Start-BiliFlow-Phone.cmd`. Chạy file đó thì điện thoại hoặc laptop **trong cùng Wi-Fi nhà** mở được V2 bằng trình duyệt.

**Làm theo mẫu Golden Label đã có**, không tự nghĩ cơ chế mới:
- Đường đi: `Golden-Label-Phone.cmd` → `scripts/golden-label.ps1 --phone` → `scripts/golden_label_server.py` và `src/biliflow/golden_label_app.py`.
- Mẫu này:
  - tìm địa chỉ Wi-Fi bằng `lan_address()`;
  - tạo mã truy cập ngẫu nhiên 8 ký tự;
  - lưu mã trong cookie HttpOnly, SameSite=Strict, so sánh hằng thời gian, và bắt mã ở **mọi** request (trang, API, video, khung hình);
  - in link `http://<ip>:<cổng>/?code=<mã>`; mã mất hiệu lực khi tắt.

**Yêu cầu:**
- **Không đổi đường cũ.** `127.0.0.1:8765` giữ nguyên hành vi, và D2 vẫn phải trùng byte.
- **Một listener thêm, tắt sẵn.**
  - Chế độ điện thoại là một listener thêm, mặc định tắt.
  - Nó chỉ nghe đúng địa chỉ IPv4 riêng của PC (10/8, 172.16/12, 192.168/16), ở một cổng riêng. Đề xuất 8767, vì 8766 là của Golden Label.
  - Không bao giờ nghe `0.0.0.0` hay địa chỉ công cộng.
- **Bật/tắt khi đang chạy, không khởi động lại Control Center.**
  - Endpoint bật/tắt chỉ nhận request từ listener `127.0.0.1`, có token phiên.
  - File khởi động mở Control Center như `Start-BiliFlow.cmd` nếu chưa chạy, gọi endpoint bật, rồi in link và mã.
  - Nếu Control Center đang chạy bản cũ không có endpoint này, file khởi động báo rõ và **không** tự khởi động lại.
  - V2 trên PC có khung "Mở trên điện thoại": trạng thái, link, mã và nút Tắt.
  - Mã đổi mỗi lần bật. Tắt chế độ hoặc dừng Control Center thì mã hết hiệu lực.
- **Bảo vệ:**
  - Mọi request vào listener điện thoại đều cần cookie mã, trừ trang nhập mã.
  - Nhập sai mã quá 10 lần thì khóa nhập mã cho tới lần bật sau.
  - Host phải đúng `<ip>:<cổng>` của listener, để chống DNS rebinding.
  - POST vẫn cần token phiên như hiện tại.
  - Các thao tác sau **chỉ làm trên PC**; qua listener điện thoại thì trả 403 kèm lý do:
    - dọn, lưu trữ, khôi phục video gốc và kiểm tra lại Thùng rác;
    - tắt Control Center;
    - cấu hình và đăng nhập AI;
    - sửa hoặc xóa bộ nhớ logo;
    - bật/tắt chế độ điện thoại.
- **Không được sửa:**
  - `cli.py`, `__init__.py`, `__main__.py`, `stage_cache.py`, `cache_dependencies.py`;
  - `scripts/run.ps1`, `scripts/env.ps1`;
  - các file đang có trong `config/`;
  - `pyproject.toml`, `uv.lock`.
  - Các file này nằm trong fingerprint cache: sửa là mất toàn bộ cache quét. Cũng không thêm thư viện mới.
  - Code mới đặt trong `control_center.py`, hoặc trong module mới mà chỉ `control_center.py` import. File khởi động mới đặt ở gốc repo, hoặc là file mới trong `scripts/`.
  - Chạy lại A4/D6 để chứng minh fingerprint không đổi.
- **Windows Firewall:** không tự sửa. Ghi hướng dẫn: lần đầu Windows hỏi cho Python qua tường lửa thì chọn Private networks.
- **HTTP không mã hóa:** chỉ dùng trong Wi-Fi nhà, không dùng Wi-Fi công cộng. Ghi rõ trong hướng dẫn và trong khung "Mở trên điện thoại".
- **Giao diện:**
  - V2 phải dùng được ở 375 px qua listener điện thoại.
  - Trang duyệt cũ `/review/{id}` **không sửa** trong đợt này. Chỉ đánh giá cách nó hiển thị ở 375 px và ghi đề xuất vào mục 8.
- **Ngoài phạm vi:** truy cập từ ngoài nhà qua internet. Nếu cần thì hỏi người dùng ở mục 8.

### 12.4. Checklist đợt 2

| ID | Hạng mục | Cloud | Máy thật | Bằng chứng |
| --- | --- | --- | --- | --- |
| G1 | Thiếu video gốc hiển thị đúng ở chi tiết, danh sách và trạng thái chính | [x] | [x] | `ef9ad52`. `sourceLineInfo`/`archiveLineInfo` của dashboard cũ chép nguyên văn vào `contracts.js` (`C.sourceLine`). `tests.test_dashboard_v2_contract`: 16 trường hợp so với hàm cũ trích từ `_dashboard_html()` → trùng chữ và tone; `SOURCE_MISSING_MESSAGE` trùng `export_guards`. browser-check: job `source_present:false` → dòng danh sách “Không còn video gốc trong input”, Chi tiết có câu đó và “Video gốc không còn trong input; không thể xuất.”, không còn “Có trong input”, nút Xuất khóa. **Máy thật** (Windows, 2026-10-04): `test_dashboard_v2_contract` 10/10 sau khi sửa cách test đọc output của node (mục 11); `verify.cjs` 28/28 |
| G2 | "Lần dọn / lưu trữ trước không thành công" hiển thị như dashboard cũ | [x] | [x] | `ef9ad52`. Cùng test so với hàm cũ (cleanup FAILED, archive FAILED, có/không còn nguồn). browser-check: danh sách và Chi tiết hiện “Lần dọn trước không thành công: Thùng rác không phản hồi”. **Máy thật:** cùng test, đạt |
| G3 | `/api/status` có `render_request` từ backend; V2 không còn tự suy ra | [x] | [x] | `ef9ad52`. `status()` thêm `render_request = self.store.render_request(job_id) is not None` (chỉ thêm trường). `tests.test_dashboard_v2_status` 2 OK (PENDING/FAILED_RETRYABLE → true; không có/COMPLETED → false; trường cũ còn đủ). verify-adapter: giữ nguyên giá trị backend, job PAUSED+`current_stage=render` không có trường → false. **Máy thật:** `test_dashboard_v2_status` 2/2; `verify-adapter.cjs` 17/17 |
| P1 | Listener điện thoại: thiếu cookie thì không vào được route nào; sai mã quá 10 lần thì khóa; đúng mã thì nhận cookie | [x] | [x] | `a80ab59`, `tests.test_dashboard_v2_phone` (9 OK, listener chạy trên 127.0.0.1, root tạm): 14 route → 401, không lộ token/mã; `?code=` hoặc form → cookie `HttpOnly; SameSite=Strict; Path=/`, giá trị là HMAC (không chứa mã); lần sai thứ 10 khóa, kể cả mã đúng cũng bị từ chối cho tới lần bật sau; cookie của lần bật trước hết hiệu lực. Câu 9: khóa mở `2007` chỉ gỡ khóa (không cấp cookie, vẫn cần mã), 5 lần sai hoặc 3 lần gỡ thì khóa luôn tới lần bật sau, an toàn khi gửi song song (13 test OK). Chromium thật: link mở từ trang khác và form nhập mã đều vào được V2 (trang nối bằng meta refresh để cookie Strict được gửi). Lỗi tìm được và đã sửa trước commit: `Referrer-Policy: no-referrer` làm form gửi `Origin: null` → đổi sang `same-origin`. **Máy thật:** `test_dashboard_v2_phone` 15/15 trên Windows (listener chạy trên 127.0.0.1) |
| P2 | Host sai → 403; POST thiếu token → 403; thao tác chỉ-PC → 403 qua listener điện thoại nhưng vẫn chạy qua `127.0.0.1` | [x] | [x] | Cùng file test: 5 Host sai → 403; POST thiếu token → 403, setting không đổi; Origin lạ → 403; có cookie + token + Origin đúng → tạm dừng hàng đợi chạy. 10 thao tác chỉ-PC → 403 `code: pc_only`, lý do “Chỉ làm trên PC…”; Control Center không tắt, chế độ điện thoại không tự tắt. 7 thao tác (trừ shutdown, đăng nhập AI, phone-mode) gửi qua `127.0.0.1` → không bị chặn pc_only. V2: nút chỉ-PC bị khóa kèm lý do (browser-check, contract test); adapter không làm mới token khi 403 `pc_only`. **Máy thật:** cùng file test, đạt |
| P3 | Endpoint bật/tắt chỉ nhận từ `127.0.0.1`; tắt thì listener đóng và mã hết hiệu lực; mỗi lần bật có mã mới | [x] | [x] | Cùng file test: POST `/api/phone-mode` thiếu token → 403, `enabled` không phải bool → 400; qua listener điện thoại → 403 pc_only; GET trên điện thoại chỉ trả `{remote:true}` (không mã); tắt → cổng đóng (kết nối bị từ chối), mã cũ 401; bật lại → mã mới; bật khi đang bật giữ mã; `stop()` đóng listener. browser-check: khung PC Bật → link + mã, Tắt → mất mã, Bật lại → mã khác. **Máy thật:** cùng file test, đạt |
| P4 | Chỉ nghe địa chỉ IPv4 riêng; từ chối `0.0.0.0` và địa chỉ công cộng | [x] | [x] | Cùng file test: chấp nhận 10/8, 172.16/12, 192.168/16; từ chối 15 địa chỉ (`0.0.0.0`, `127.0.0.1`, công cộng, 172.32.x, 100.64.x, 169.254.x, IPv6, `localhost`, viết sai) **trước khi bind**; cổng phải 1024–65535, khác 8765; qua HTTP, `lan_address` ra loopback → 400. Thực tế trên Windows cần máy thật xác nhận địa chỉ Wi-Fi được chọn (câu 8). **Máy thật:** đạt sau khi sửa test (mục 11). Máy này tự dò ra địa chỉ 192.168.1.x, là IPv4 riêng hợp lệ; bind thật trên Wi-Fi thử ở P7 |
| P5 | `127.0.0.1:8765` không đổi: D2 trùng byte, test Control Center cũ đạt, A4/D6 không đổi | [x] | [x] | `tests.test_dashboard_v2_route` 9 OK, P5 trong file phone (`/`, `/review/1` trùng SHA-256 f6996bb khi chế độ điện thoại đang bật). test_control_center 52/53, test_skip_export 26/30 (lỗi PowerShell như cũ); source_cleanup_http 20, source_archive_http 8, export_identity 28, export_dialog 5, logo_memory_admin 14, review_workflow 111, golden_label_app 20 OK. A4/D6: 10 stage `none`. Chỉ thêm route `/api/phone-mode` vào listener PC. **Máy thật:** full suite 1236 test OK (25 skip); `test_control_center` 53/53; `test_dashboard_v2_route` 9/9; fingerprint 10 stage `none` (`temp/ui-plan/dashboard-v2-check/fingerprint-batch2.txt`) |
| P6 | File khởi động chạy đúng khi Control Center chưa chạy, đã chạy, hoặc đang chạy bản cũ; in link và mã | [-] | [ ] | Cloud không có PowerShell. Đã viết `Start-BiliFlow-Phone.cmd` → `scripts/Start-BiliFlow.ps1 -Phone` (giữ ASCII, CRLF). Ba nhánh: đang khởi động / đang chạy / mở mới đều gọi `Enable-PhoneMode`; bản cũ trả 404 → báo, không khởi động lại. `Start-BiliFlow.cmd` không đổi hành vi. Máy thật phải chạy cả ba trường hợp |
| P7 | Điện thoại thật trong Wi-Fi nhà: mở link, nhập mã, dùng V2, tạm dừng/tiếp tục hàng đợi; thao tác chỉ-PC bị từ chối | — | [ ] | |
| P8 | Laptop trong Wi-Fi nhà: như P7 | — | [ ] | |
| P9 | Trang duyệt cũ ở 375 px: ghi nhận cách hiển thị và đề xuất, không sửa | [x] | [ ] | Chromium 375 px trên handler thật, root tạm, 1 cảnh 18+: trang không tràn ngang (scrollWidth 375); 4 nút quyết định 150×67 px, dễ bấm; hàng chip bộ lọc cuộn ngang (3 chip và ô “Thêm” nằm ngoài màn hình, không có dấu hiệu cuộn); chữ nhỏ nhất 10 px; nhãn “phím 1–4” vô nghĩa trên điện thoại; nút quyết định nằm dưới video, phải cuộn. Không sửa. Đề xuất: câu 10 mục 8 |

### 12.6. Quyết định của người dùng cho câu 7–12 (2026-10-04)

| # | Quyết định | Đã làm |
| --- | --- | --- |
| 7 | Duyệt cảnh qua điện thoại vẫn được nhớ logo, giống trên laptop/PC | Không đổi code; `review/decision` có `remember_*_logo` vẫn chạy qua điện thoại |
| 8 | Tự chọn địa chỉ Wi-Fi | Không đổi (`lan_address()` như Golden Label) |
| 10 | Sau khi xem ảnh giải thích: làm cả (a) mũi tên mép phải, (b) ẩn “phím N”, (c) nút quyết định dính đáy, (d) chữ ≥ 12 px | Chèn `REVIEW_PHONE_STYLE`/`REVIEW_PHONE_SCRIPT` (`control_center.py`) vào `/review/{id}` **chỉ khi phục vụ qua listener điện thoại**; không sửa `review_workflow.py`; `127.0.0.1:8765` vẫn trùng byte (D2). (a) nút “›” cuộn hàng chip, tự ẩn khi tới cuối; (b) ẩn “phím N” trên màn hình cảm ứng, vẫn hiện “✓ đã chọn”; (c) 4 nút cố định ở đáy màn hình ≤ 820 px, cuối trang có đệm để không che nội dung; (d) nhãn khung hình/chip lên 12 px. Dùng `fixed` thay `sticky` vì khung `#focus` có `overflow:hidden` và cột nút nằm dưới video |
| 11 | Trên điện thoại, `/` chuyển thẳng sang V2 | Không đổi (đã làm như vậy) |
| 12 | Nhập mã tay, không để mã trong link | Bỏ đăng nhập bằng `?code=`: link in ra và khung PC chỉ có `http://<ip>:8767/` + mã; `?code=` bị bỏ qua (không cookie, không tính lần sai); status không còn trường `link`. Test mới + Chromium thật: link có `?code=` chỉ hiện ô nhập mã |
| 9 | Đồng ý khóa sau 10 lần sai, **thêm khóa mở đặc biệt `2007`**; người dùng chọn kiểu “chỉ mở khóa” | `phone_access.UNLOCK_KEY = "2007"`, chỉ có tác dụng khi đang khóa: gỡ khóa và đặt lại số lần sai, không cấp cookie, vẫn phải nhập mã 8 ký tự. Sai 5 lần (`MAX_UNLOCK_ATTEMPTS`) hoặc đã gỡ 3 lần trong một lần bật (`MAX_UNLOCKS`, thêm để khóa trong mã nguồn không mở đường dò mã vô hạn) thì khóa mở bị khóa tới lần bật sau. Trang không bao giờ hiện khóa; ô nhập đổi nhãn thành “Khóa mở đặc biệt”. Khung PC cho biết còn bao nhiêu lượt gỡ |

### 12.5. Tự review bảo mật chế độ điện thoại (cloud, trước khi push)

| Mặt | Kiểm tra | Kết luận |
| --- | --- | --- |
| Xác thực | Mã 8 ký tự từ 31 ký tự (≈ 8,5·10¹¹ tổ hợp), `secrets`; so sánh hằng thời gian; đếm lần sai trong khóa luồng (không vượt được bằng request song song); khóa sau 10 lần sai. Khóa mở `2007` (câu 9) có trong mã nguồn nên coi như công khai: nó chỉ gỡ khóa, không cấp cookie; tối đa 5 lần sai và 3 lần gỡ mỗi lần bật, nên tổng số lần thử mã 8 ký tự mỗi lần bật ≤ 40 (test song song 40 luồng: bộ đếm đúng). Cookie là HMAC-SHA256 của mã với khóa ngẫu nhiên 32 byte tạo lại mỗi lần bật, nên tắt/bật làm mọi cookie cũ vô hiệu | Đạt. Rủi ro còn lại: người trong Wi-Fi có thể cố tình khóa nhập mã (câu 9) |
| Rò mã / token | Mã chỉ trả ở GET `/api/phone-mode` của `127.0.0.1` (đọc được chỉ từ PC; Host check chặn rebinding). Điện thoại chỉ nhận `{remote:true}`. Token phiên chỉ trả sau khi có cookie. Cookie không chứa mã. Server không ghi log request. Mã chỉ được gõ vào form (câu 12), không bao giờ nằm trong URL, nên không vào lịch sử trình duyệt. Mã và cookie vẫn đi qua Wi-Fi dạng HTTP | Đạt trong phạm vi Wi-Fi nhà; HTTP được cảnh báo ở hướng dẫn, ở khung V2 và trang nhập mã |
| DNS rebinding / CSRF | Host phải trùng đúng `ip:cổng`; POST cần cookie SameSite=Strict, token phiên ở header và Origin đúng; trang nhập mã `frame-ancestors 'none'`; mọi response vẫn có `X-Frame-Options: SAMEORIGIN` | Đạt |
| Thao tác chỉ-PC | Chặn ở listener điện thoại **trước** mọi xử lý (403 `pc_only`), không dựa vào giao diện; bật/tắt chế độ chỉ ở listener `127.0.0.1` (kiểm cả `client_address` và cờ listener). Test gửi đủ 10 đường dẫn qua điện thoại → 403, qua PC → không bị chặn | Đạt. Riêng nhớ logo khi duyệt vẫn chạy qua điện thoại (câu 7) |
| Bind | Chỉ IPv4 riêng, kiểm **trước** khi bind; không `0.0.0.0`; `allow_reuse_address = False`; cổng 1024–65535, khác 8765 | Đạt. Địa chỉ tự chọn có thể là của VPN (câu 8) |
| Vòng đời | `stop()` và nhánh Ctrl+C của `serve()` đóng listener; thread daemon; timeout 20 s như listener PC | Đạt |

### 12.7. Quyết định sau review bảo mật của máy thật (2026-10-04)

| Việc | Quyết định |
| --- | --- |
| Visual AI Audit (`POST /api/jobs/{id}/ai-audit` với `visual: true`, gửi tối đa 36 ảnh cho Codex) | Chỉ làm trên PC. AI Audit không gửi ảnh (`visual: false`) vẫn làm được qua điện thoại. |
| S1–S6 (mục 11) | Sửa hết ở đợt 3 (mục 13). |
| Thứ tự | Đợt 3 làm trên cloud → máy thật kéo về kiểm → người dùng test toàn bộ trên điện thoại, laptop và dữ liệu thật → rồi mới merge vào `main`. **Hiện chưa merge.** |

## 13. Đợt 3 (giao ngày 2026-10-04): làm chắc chế độ điện thoại

Đọc trước: mục 11 (S1–S6, kèm vị trí trong code) và 12.7.

Mọi ràng buộc của 12.3 vẫn giữ nguyên:
- không sửa file nằm trong fingerprint cache, không thêm thư viện;
- `127.0.0.1:8765` giữ nguyên hành vi, ngoại trừ dòng báo ở H3;
- không tự sửa Windows Firewall;
- chế độ điện thoại mặc định tắt.

### 13.1. Việc cần làm

- **H1 (S1). Chặn quấy cửa khi chưa có mã.**
  - Bắt lỗi `urllib.parse.urlparse(self.path)` ở `do_GET`/`do_POST` của cả listener điện thoại lẫn listener PC: đường dẫn hỏng trả 400, không in traceback.
  - `handle_error` của listener điện thoại chỉ ghi một dòng ngắn, có giới hạn tần suất (vd. tối đa 1 dòng mỗi 10 s, kèm số lần đã bỏ qua).
  - Listener điện thoại nhận tối đa khoảng 32 kết nối cùng lúc (vd. `BoundedSemaphore` trong `process_request`). Vượt giới hạn thì đóng ngay kết nối mới.
  - Kết nối chưa có cookie chỉ được giữ khoảng 5 s. Qua bước kiểm cookie thì dùng timeout như listener PC; stream video vẫn không bị cắt như hiện nay.
- **H2 (S2). Đổi danh sách chỉ-PC thành danh sách cho phép.**
  - Listener điện thoại chỉ nhận các POST có trong danh sách cho phép. Danh sách hỗ trợ regex cho `/api/jobs/{id}/...` và `/api/jobs/{id}/review/...`.
  - Mọi POST khác trả 403 `pc_only` kèm lý do, trước khi đọc body hay chạy bất cứ gì.
  - `ai-audit` với `visual: true` trả 403 qua điện thoại, lý do: "Chỉ làm trên PC: Visual AI Audit gửi ảnh ra ngoài máy". Với `visual: false` thì vẫn chạy. V2 trên điện thoại khóa nút Visual AI Audit kèm lý do.
  - Thêm test liệt kê mọi route của `do_POST` (đọc từ code, như `tests/test_dashboard_v2_contract.py` đang làm). Test báo lỗi khi có route chưa được xếp vào "cho phép qua điện thoại" hoặc "chỉ PC".
  - `docs/DASHBOARD_V2_PHONE.md` phải nói rõ: duyệt cảnh qua điện thoại có thể thêm hoặc bỏ bản ghi logo đã nhớ (câu 7); chỉ trang Bộ nhớ logo là chỉ-PC.
- **H3 (S3). Cửa phụ tự đóng và luôn được báo.**
  - Tự tắt sau 8 giờ kể từ lúc bật (hằng số trong `phone_access.py`). Khung "Mở trên điện thoại" hiện giờ sẽ tắt.
  - Kiểm định kỳ, vd. mỗi 60 s: nếu `lan_address()` không còn trùng địa chỉ đang nghe thì tự tắt.
  - GET `/api/phone-mode` trên PC cho biết lý do tắt gần nhất: người dùng tắt, hết giờ, đổi địa chỉ, hoặc dừng Control Center.
  - Dashboard cũ `/` hiện dòng "Đang mở cho điện thoại: http://…:8767/ (tắt trong Dashboard V2 → Cài đặt)" khi chế độ đang bật.
    - Đây là sửa `/` có chủ đích: cập nhật fixture D2 trong cùng commit (người dùng đã đồng ý ở câu 5).
    - `/review/{id}` trên PC vẫn phải trùng byte.
  - `Start-BiliFlow.cmd` (không có `-Phone`) in một dòng báo khi dùng lại Control Center đang chạy mà chế độ điện thoại đang bật.
- **H4 (S4). Ghi event.**
  - Ghi event của Control Center khi: bật, tắt (kèm lý do), nhập sai mã, bị khóa, gỡ khóa bằng khóa mở, khóa mở bị khóa.
  - Mỗi event kèm IP thiết bị, nhưng **không bao giờ ghi mã hay cookie**.
  - Khung trên PC hiện khoảng 10 event gần nhất.
- **H5 (S5). Launcher báo đúng trạng thái.** Sau một lỗi không phải lỗi HTTP (vd. hết thời gian chờ), `Enable-PhoneMode` gọi GET `/api/phone-mode` rồi báo đúng tình trạng: đã bật (in link và mã) hay chưa bật.
- **H6 (S6). Chỉnh nhỏ.**
  - `try_code` trả luôn cookie trong cùng một lần giữ khóa, bỏ khe hở giữa `try_code` và `set_cookie_header`.
  - Hướng dẫn Firewall: ưu tiên một rule riêng cho TCP 8767, mạng Private, chỉ nhận từ LocalSubnet. Ghi sẵn lệnh PowerShell để **người dùng tự chạy** bằng quyền admin; BiliFlow không tự tạo rule. Giữ cách cho phép Python làm phương án dự phòng.
  - Cookie dùng chung cho mọi thiết bị trong một lần bật: chỉ ghi chú trong hướng dẫn (muốn đuổi thiết bị thì tắt rồi bật lại). Không bắt buộc sửa.

### 13.2. Test

- Viết test Python mới cho H1–H6: listener chạy trên 127.0.0.1 với root tạm, theo mẫu `tests/test_dashboard_v2_phone.py`.
- **Không test nào được bind vào địa chỉ Wi-Fi thật:** luôn gán `lan` giả (xem lỗi test P4 ở mục 11).
- Khi đọc output của node hay subprocess, luôn dùng `encoding="utf-8"` (lỗi Windows ở mục 11).
- Chạy lại:
  - `node dashboard_v2/verify.cjs`, `verify-adapter.cjs`, `browser-check.cjs`;
  - `tests.test_dashboard_v2_*` và các test Control Center liên quan;
  - lệnh A4: cả 10 stage phải ra `none`.

### 13.3. Checklist đợt 3

| ID | Hạng mục | Cloud | Máy thật | Bằng chứng |
| --- | --- | --- | --- | --- |
| H1 | Đường dẫn hỏng → 400, không traceback; log lỗi có giới hạn; tối đa khoảng 32 kết nối; kết nối chưa có cookie bị đóng sau khoảng 5 s | [x] | [ ] | `a7514df`, `tests.test_dashboard_v2_phone_hardening` (18 OK, listener 127.0.0.1 + `lan` giả): `GET/POST http://[x/` → 400 “Đường dẫn không hợp lệ” ở cả listener điện thoại và PC, stderr không có `Traceback`; `handle_error` 6 lỗi liên tiếp → 2 dòng (`ValueError from <ip>`, “(4 more skipped)”), không lộ dữ liệu request; giới hạn kết nối (thử với 2): kết nối thứ 3 bị đóng ngay < 1 s; kết nối im lặng bị đóng sau khoảng `GATE_TIMEOUT_SECONDS` (test đặt 1 s) rồi slot được trả lại; có cookie thì body đến sau 1,6 s vẫn được nhận (timeout 20 s). Hằng số 32 và 5 s kiểm trong test |
| H2 | Listener điện thoại chỉ nhận POST trong danh sách cho phép; test phân loại đủ mọi route; Visual AI Audit → 403 qua điện thoại, vẫn chạy trên PC | [x] | [ ] | Cùng file: đọc mọi route `do_POST` từ code (+ 2 route bộ nhớ logo), bung `(a\|b)` và `(\d+)` → mọi đường dẫn đều được phân loại rõ (`post_policy(...)[2]`); route lạ → 403 `PC_ONLY_DEFAULT`, trả lời < 3 s dù thiếu body (không chờ body). `ai-audit` `visual:true` qua điện thoại → 403 “Chỉ làm trên PC: Visual AI Audit gửi ảnh ra ngoài máy”, `start_ai_audit` không chạy; `visual:false` → 200 và chạy; trên PC `visual:true` vẫn chạy. browser-check: lựa chọn Visual bị khóa trên điện thoại, JSON vẫn chọn được. Hướng dẫn ghi rõ duyệt cảnh có thể thêm hoặc bỏ logo đã nhớ |
| H3 | Tự tắt sau 8 giờ và khi địa chỉ đổi; có lý do tắt; dòng báo ở `/` (fixture D2 cập nhật, `/review/{id}` trên PC vẫn trùng byte) và ở `Start-BiliFlow.cmd` | [x] | [ ] | Cùng file: tự tắt sau thời hạn (test 0,6 s) → lý do `expired` (“hết 8 giờ”), cổng đóng, dòng báo ở `/` biến mất; `lan` giả đổi → `address_changed` (payload có địa chỉ mới); lỗi dò → `address_changed`; tắt qua API → `user`; `stop()` → `stopped`; watchdog cũ không đóng listener mới. Dòng báo `/` chỉ có khi đang bật, không chứa mã; `/` khi tắt và `/review/1` luôn trùng byte fixture f6996bb (câu 13). Launcher `Show-PhoneNotice` có trong script; chưa chạy được (không có PowerShell) |
| H4 | Event bật/tắt/sai mã/khóa/gỡ khóa có IP, không chứa mã hay cookie; khung PC hiện các event gần nhất | [x] | [ ] | Cùng file: luồng đủ 8 loại event `PHONE_*` lưu vào store; mọi event của thiết bị có `ip`; dump toàn bộ event không chứa mã, giá trị cookie, chữ đã gõ hay khóa mở; event khóa ở mức WARN; `/api/phone-mode` trên PC trả ≤ 10 event, mới nhất trước, không có mã; listener điện thoại không trả `events`. browser-check: khung PC hiện “Nhật ký gần đây” |
| H5 | Launcher báo đúng trạng thái sau lỗi không phải HTTP | [-] | [ ] | Cloud không có PowerShell. `Enable-PhoneMode`: lỗi không có HTTP status → `Get-PhoneStatus` → “IS on” (in link và mã) / “NOT on” / “unknown”. Máy thật cần thử, ví dụ chặn tạm hoặc làm chậm request |
| H6 | `try_code` trả cookie trong một lần giữ khóa; hướng dẫn Firewall có rule riêng cho cổng 8767 | [x] | [ ] | `try_code(text, ip=…)` trả `(outcome, cookie)` trong một lần giữ khóa; `set_cookie_header` đã bỏ (test kiểm). Hướng dẫn có `New-NetFirewallRule … -LocalPort 8767 -Profile Private -RemoteAddress LocalSubnet` và `Remove-NetFirewallRule`, cách dự phòng cho Python, cảnh báo rule Block khi đã bấm Cancel, và ghi chú một mã cho mọi thiết bị |

**Sau đợt 3:**
1. Máy thật kéo về kiểm như đợt 2.
2. Người dùng test toàn bộ trên điện thoại, laptop và dữ liệu thật: P6–P9 ở 12.4 và E2–E8 ở mục 7.
3. Chỉ merge vào `main` (kèm E9) khi người dùng đã test xong và yêu cầu merge.
