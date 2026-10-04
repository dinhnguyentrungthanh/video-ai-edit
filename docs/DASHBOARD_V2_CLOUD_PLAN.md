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

1. **E9 đã thay đổi:** nhánh nay có thêm file V2 mới (`adapter.js`, `demo-store.js`, `live.html`, `verify-adapter.cjs`, `browser-check.cjs`) và đã sửa `app.js`, `index.html`, `verify.cjs`, `serve.py`. Bản `dashboard_v2/` chưa track trong `E:\DungChung\BiliFlow` **không còn giống** bản trên nhánh. Trước khi checkout/merge, hãy dời bản đó ra ngoài repo (không xóa, nếu bạn muốn giữ). Bạn đồng ý không?
2. **Nơi đọc asset:** route đọc `dashboard_v2/` cạnh mã nguồn (`src/biliflow/../../dashboard_v2`, đúng khi `PYTHONPATH=src` như `scripts/env.ps1`), không đọc từ project root/dữ liệu. Thiếu thư mục thì trả 404, không lỗi. Giữ như vậy?
3. **`render_request` ở bản live** được suy ra từ `current_stage === "render"` + state PAUSED/FAILED/INTERRUPTED_RECOVERABLE, chỉ để khóa nút (backend vẫn quyết định; 409/400 được hiển thị). Guide cũ dặn “không invent render request”. Chấp nhận cách suy ra này, hay muốn bỏ (chỉ dựa vào 409)?
4. **Trang Tải video:** đang để hiện ở bản live với badge “MÔ PHỎNG” và khung cảnh báo, không ẩn sau cờ. Muốn ẩn hẳn khỏi bản live không?
5. **Fixture trang cũ** (`tests/fixtures/dashboard_v2_classic_pages.json`) khóa SHA-256 của `/` và `/review/1` theo f6996bb. Nếu sau này `main` cố ý sửa dashboard cũ hay trang duyệt, test này sẽ báo và cần cập nhật fixture. Chấp nhận?
6. **403 vì Host sai** (không phải token) cũng làm adapter lấy token mới và gửi lại đúng một lần trước khi báo lỗi. Hai lần 403 là vô hại vì backend từ chối trước khi chạy gì. Giữ, hay muốn phân biệt theo nội dung lỗi?

## 9. Nhật ký cloud (mục mới nhất ở trên cùng)

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
| 2026-10-03 | (commit docs này, sau 4c76a34) | Đính chính tài liệu cho khớp code: mục 5 của kế hoạch (409 `code` ở cấp ngoài, không phải `error.code`); guide mục 4 và mục 2 (danh sách job lấy từ `/api/status`, không từ `/api/jobs`) | Chỉ sửa docs, không chạy test | Như dòng dưới |
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
| 2026-10-04 | c616bef (chưa commit bảng này) | Kéo nhánh về `temp/wt-dashboard-v2`; điền cột Máy thật ở mục 7. Bản xem thử chỉ đọc trên handler thật: root tạm `temp\v2-preview-*`, 11 job giả, cổng 8796, chặn mọi POST, không chạy scheduler/watcher, không đọc dữ liệu thật | Full suite 1215 OK (25 skip); verify 28/28; verify-adapter 15/15; fingerprint 10 stage không đổi; V2 trùng 6 nhóm với `/`; 0 lỗi console/CSP; 375 px không tràn. **Tìm được 2 chỗ V2 hiển thị sai hoặc thiếu so với `/`** (dưới bảng) | Sửa 2 chỗ dưới bảng. Câu hỏi mục 8 chờ người dùng trả lời. C4, C5, C6 (Tab/Escape), C7 và E2–E9 cần Control Center thật chạy code nhánh, nghĩa là phải khởi động lại; việc này chỉ làm khi người dùng đồng ý và không có job nào chạy |

Máy thật tìm được hai lỗi, chưa sửa (để phiên cloud sau). Cả hai chỉ là lỗi hiển thị: nút thao tác vẫn bị khóa đúng (`contracts.js` → `locked`) và backend vẫn quyết định.

1. **Video gốc không còn trong input nhưng V2 ghi "Có trong input".**
   - Vị trí: `dashboard_v2/app.js:221`, mục "Video gốc & thông tin kỹ thuật". Điều kiện chỉ xét `archived` rồi `cleaned`, mọi trường hợp còn lại đều in "Có trong input"; không xét `source_present === false`.
   - Dòng trong danh sách cũng không báo thiếu video gốc.
   - Dashboard cũ báo "Không còn video gốc trong input" hoặc "Thiếu video gốc".
   - Tái hiện: bản xem thử, job #10 (`source_present: false`).
2. **Thiếu thông báo "lần trước không thành công".**
   - Dashboard cũ hiện "Lần dọn trước không thành công: …" và "Lần lưu trữ trước không thành công: …" khi dòng `source_cleanup` / `source_archive` mới nhất có `state: FAILED`, kèm `error`.
   - V2 không hiện thông báo này.

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
| G1 | Thiếu video gốc hiển thị đúng ở chi tiết, danh sách và trạng thái chính | [ ] | [ ] | |
| G2 | "Lần dọn / lưu trữ trước không thành công" hiển thị như dashboard cũ | [ ] | [ ] | |
| G3 | `/api/status` có `render_request` từ backend; V2 không còn tự suy ra | [ ] | [ ] | |
| P1 | Listener điện thoại: thiếu cookie thì không vào được route nào; sai mã quá 10 lần thì khóa; đúng mã thì nhận cookie | [ ] | [ ] | |
| P2 | Host sai → 403; POST thiếu token → 403; thao tác chỉ-PC → 403 qua listener điện thoại nhưng vẫn chạy qua `127.0.0.1` | [ ] | [ ] | |
| P3 | Endpoint bật/tắt chỉ nhận từ `127.0.0.1`; tắt thì listener đóng và mã hết hiệu lực; mỗi lần bật có mã mới | [ ] | [ ] | |
| P4 | Chỉ nghe địa chỉ IPv4 riêng; từ chối `0.0.0.0` và địa chỉ công cộng | [ ] | [ ] | |
| P5 | `127.0.0.1:8765` không đổi: D2 trùng byte, test Control Center cũ đạt, A4/D6 không đổi | [ ] | [ ] | |
| P6 | File khởi động chạy đúng khi Control Center chưa chạy, đã chạy, hoặc đang chạy bản cũ; in link và mã | [ ] | [ ] | |
| P7 | Điện thoại thật trong Wi-Fi nhà: mở link, nhập mã, dùng V2, tạm dừng/tiếp tục hàng đợi; thao tác chỉ-PC bị từ chối | — | [ ] | |
| P8 | Laptop trong Wi-Fi nhà: như P7 | — | [ ] | |
| P9 | Trang duyệt cũ ở 375 px: ghi nhận cách hiển thị và đề xuất, không sửa | [ ] | [ ] | |
