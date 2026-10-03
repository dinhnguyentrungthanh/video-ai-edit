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
  - 409: `ActionConflict`, có `error.code`;
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
| A1 | `node dashboard_v2/verify.cjs` đạt | [x] | [ ] | `node dashboard_v2/verify.cjs` → `{"passed":25,"failed":0}` (Pha 0); sau Pha 2: 28/28 |
| A2 | `node --check` cho mọi file `.js` / `.cjs` trong `dashboard_v2/` | [x] | [ ] | `node --check` đạt cả 5 file (app, contracts, download-demo, mock-data, verify.cjs) |
| A3 | Các test Python chạy được trên Linux: `tests.test_http_guards`, `tests.test_export_identity`, `tests.test_control_center` (cần node). Ghi rõ module nào chạy được, module nào `[-]` và vì sao | [x] | [ ] | `tests.test_export_identity` 28/28 OK. `tests.test_control_center` 52/53: 1 lỗi `test_status_exposes_the_workers_queue_order` do thiếu PowerShell (`job_pipeline._powershell`) → `[-]` riêng test này. `tests.test_http_guards` `[-]`: import `cli.py` cần torch/timm. Chạy thêm: `test_source_cleanup_http` 20 OK, `test_source_archive_http` 8 OK, `test_export_dialog` 5 OK, `test_skip_export` 26/30 (4 lỗi cùng nguyên nhân PowerShell, `[-]`) |
| A4 | Fingerprint cache: `stage_cache._tree_fingerprint(root, stage)` của cả 10 stage trong `CACHEABLE_STAGES` phải **giống hệt** giữa commit gốc f6996bb và đầu nhánh. Lệnh mẫu ở cuối mục này | [x] | [ ] | Lệnh mẫu (worktree f6996bb vs đầu nhánh 65803f7): 10 stage, `fingerprint changed for: none` |

### B. Mapping (Pha 1, chỉ đọc)

| ID | Hạng mục | Cloud | Máy thật | Bằng chứng |
| --- | --- | --- | --- | --- |
| B1 | Bảng mapping trong guide: chức năng cũ → vị trí trên V2 → endpoint / payload / schema thực tế (đã đọc code) → điều kiện khóa / xác nhận → tình trạng (khớp / thiếu / mô phỏng) | [x] | — | Guide mục 8.2 (18 dòng, đọc từ `do_GET/do_POST/status()/finalize()`); 13 chênh lệch schema ở mục 8.1. Chỉ Tải video và tình huống kiểm thử là mô phỏng |
| B2 | Đối chiếu `contracts.js → endpoints` với route thật trong `control_center.py`: GET và POST, kể cả `/api/jobs/{id}/...`, `/api/logo-memory/...`, `/review/...`. Liệt kê mọi chênh lệch | [x] | — | `python -m unittest tests.test_dashboard_v2_contract` → 6 test OK: 52/52 endpoint trong `contracts.js` có route thật (16 GET, 14 POST pattern), đúng method. Không thiếu/thừa route. Chênh lệch về schema (không phải route): guide 8.1 |
| B3 | Mọi state của job (`job_store.py`) rơi vào đúng một nhóm/tab của V2; không state nào bị bỏ sót | [x] | [ ] | Cùng file test: 20 state × 4 `queue_kind` (kể cả lạ) → mỗi state đúng một nhóm và **trùng** `jobTab()` của dashboard cũ (trích trực tiếp từ `_dashboard_html()`); `labels` phủ đủ 20 state. QUEUED + kind lạ → `waiting` |
| B4 | Guide đã cập nhật theo mục 5 (finalize 400, 408, export identity, …) | [x] | — | Guide: mục 8.1 #2–#6, mục 8.3, sửa đoạn cuối mục 7 và mục 4 (409 `code` ở cấp trên). Đối chiếu code + test hiện có: `tests.test_control_center` (403/408/400/409), `tests.test_export_identity` 28 OK (finalize ValueError → 400, không ghi đè file không manifest) |

### C. Frontend (Pha 2, không đụng backend)

| ID | Hạng mục | Cloud | Máy thật | Bằng chứng |
| --- | --- | --- | --- | --- |
| C1 | Tách DemoStore khỏi phần render; render chỉ nhận snapshot đã chuẩn hóa | [x] | — | `demo-store.js` (fixture + mutation) và `BFAdapter.createLiveStore` có cùng giao diện `snapshot/subscribe/dispatch/preview/fileAction/logoAction`; `app.js` chỉ đọc `store.snapshot()`. `node dashboard_v2/verify.cjs` → 28/28 (thêm: demo không nạp adapter, live không nạp fixture, chỉ adapter.js có fetch, DemoStore 409 không ghi) |
| C2 | `ControlCenterAdapter` là nơi duy nhất gọi HTTP. Lấy token qua GET `/api/session`; POST gửi `Content-Type: application/json` và `X-BiliFlow-Token` | [x] | [ ] | `dashboard_v2/adapter.js`; verify.cjs kiểm không file nào khác có fetch/XHR/WebSocket. `node dashboard_v2/verify-adapter.cjs` test 1: token qua GET `/api/session`, header đúng, token không nằm trong URL, dùng lại token |
| C3 | Test adapter bằng transport giả: đúng endpoint/body cho từng thao tác ở guide mục 4; 403 làm mới token **một** lần; 409 không replay; 400/408 hiện lý do; response cũ không ghi đè response mới | [x] | — | `node dashboard_v2/verify-adapter.cjs` → 15/15: 26 thao tác guide mục 4 đúng path/body; 403 → làm mới token đúng 1 lần, 403 lần 2 hiện lỗi (2 POST); 409 giữ `code`/`preview`, 1 POST; 400 finalize hiện nguyên văn; 408/500/mất kết nối không gửi lại; GET 403 không làm mới token; `/api/status` cũ trả về sau → bỏ; bấm đúp 1 POST |
| C4 | Draft (detector, OCR, metadata, chính sách xuất) không mất khi polling hoặc khi mở/đóng Chi tiết | [x] | [ ] | `node dashboard_v2/browser-check.cjs` (Chromium headless + API giả): draft quét (bỏ nhóm gore, OCR 8) và xuất (custom 2,5 GB) còn sau polling và sau khi đóng/mở lại; POST finalize đúng body. Draft gắn với `job_key + source_sha256 + revision`. Drawer giữ mục đang mở và focus khi polling; form AI chưa lưu không bị dựng lại. Lỗi tìm được và đã sửa trong commit này: focus trong drawer nhảy sang `summary` khác sau polling |
| C5 | Các khóa: NEEDS_MORE_CONTEXT hoặc pending chặn xuất; nguồn missing/cleaned/archived; export đang chạy; hủy dialog không POST; bấm đúp chỉ gửi một POST | [x] | [ ] | browser-check: NEEDS_MORE_CONTEXT còn 1 → nút Xuất disabled; Hủy dialog → 0 POST; bấm đúp Xác nhận → 1 POST; 409 hiện lý do trong dialog, không gửi lại. Khóa nguồn/xuất đang chạy: verify.cjs (missing source, render request, archived/cleaned, khóa file chung). Adapter tự chặn POST trùng khi đang bay |
| C6 | Desktop 1280 và mobile 375 không tràn ngang; Tab/Escape đúng trong modal và drawer. Chỉ làm nếu cloud có trình duyệt headless, nếu không thì `[-]` | [x] | [ ] | browser-check (Playwright 1.56, Chromium headless): 1280 không tràn; 375 px không tràn ở overview/videos/queue/downloads/logos/settings và drawer; Tab 25 lần vẫn trong drawer; Escape đóng modal trước rồi mới đóng drawer. Lỗi tìm được và đã sửa: hero đọc `gpu.name` → trang trắng khi `gpu` null; VRAM cố định của fixture |
| C7 | Trang Tải video vẫn là mô phỏng: có nhãn rõ ở bản live (hoặc ẩn sau cờ), không gọi mạng | [x] | [ ] | Bản live có badge “MÔ PHỎNG” và khung `#download-simulation`; browser-check: thêm 3 link mẫu → 0 POST, 0 request ra ngoài origin. Không có downloader/endpoint |

### D. Route `/dashboard-v2` (Pha 3)

| ID | Hạng mục | Cloud | Máy thật | Bằng chứng |
| --- | --- | --- | --- | --- |
| D1 | GET `/dashboard-v2` và asset theo whitelist; asset ngoài whitelist trả 404; không liệt kê thư mục | [x] | [ ] | `python -m unittest tests.test_dashboard_v2_route` → 9 OK. `/dashboard-v2` → 301 `/dashboard-v2/` (asset tương đối cần dấu `/`, CSP `base-uri 'none'` cấm `<base>`); `/dashboard-v2/` = `live.html`; 12 asset whitelist đúng byte; 18 đường dẫn ngoài whitelist (index.html, mock-data, demo-store, serve.py, `..`, `%2e%2e`, thư mục `assets/`, NUL, chữ hoa…) → 404 JSON, không HTML. Asset đọc từ `<code>/dashboard_v2`, không đọc gì trong project root. Thêm: Chromium mở `/dashboard-v2/` trên **handler thật** (root tạm, 4 job SQLite tạm): 4 dòng job, 0 lỗi JS, 0 vi phạm CSP, 0 POST (script `temp/real_handler_*`, không commit) |
| D2 | `/` (dashboard cũ) và `/review/{id}` trả nội dung **như trước**, có test so sánh | [x] | [ ] | Cùng file test: body `/` và `/review/1` có SHA-256 và độ dài **trùng** bản gốc f6996bb (`tests/fixtures/dashboard_v2_classic_pages.json`, tính từ worktree f6996bb); header CSP vẫn chỉ `frame-ancestors 'self'` |
| D3 | CSP của route V2 có `connect-src 'self'`; giữ `frame-ancestors` và chống framing như route cũ | [x] | [ ] | Trang V2 có thêm header CSP đầy đủ (`connect-src 'self'`, `frame-ancestors 'self'`, không `*`); header chung `frame-ancestors 'self'` và `X-Frame-Options: SAMEORIGIN` vẫn có trên trang và asset; `Cache-Control: no-store`, `nosniff`, `Referrer-Policy: no-referrer` |
| D4 | Test Python với thư mục gốc tạm, theo mẫu `tests/test_control_center.py`: route trả 200, POST thiếu token vẫn 403 | [x] | [ ] | Cùng file test: route 200; POST `/api/scheduler` thiếu token → 403 và setting không đổi; có token → 200; Host lạ → 403; POST vào `/dashboard-v2/` → 403 |
| D5 | Chạy lại các test cũ liên quan Control Center (`tests.test_control_center`, `tests.test_source_cleanup_http`, …): không test nào đổi kết quả | [x] | [ ] | Sau khi sửa: test_control_center 52/53 (1 lỗi PowerShell, như Pha 0); test_source_cleanup_http 20 OK; test_source_archive_http 8 OK; test_skip_export 26/30 (4 lỗi PowerShell, như Pha 0); test_export_identity 28 OK; test_export_dialog 5 OK; test_source_archive 37 OK; test_logo_memory_admin 14 OK (1 skip); test_review_workflow 111 OK. test_source_cleanup 47/49: 2 lỗi đường dẫn Windows (UNC, `\`) trên Linux, **giống hệt** khi chạy ở worktree f6996bb → `[-]` cho 2 test này |
| D6 | Lặp lại A4 sau khi sửa `control_center.py`: fingerprint cache không đổi | [x] | [ ] | Lệnh mẫu A4 sau khi sửa: 10 stage, `fingerprint changed for: none`. `control_center.py` và `dashboard_v2/` không nằm trong 33 file nguồn của `stage_source_paths` |

### E. Máy thật (cloud không làm)

| ID | Hạng mục | Cloud | Máy thật | Bằng chứng |
| --- | --- | --- | --- | --- |
| E1 | Kéo nhánh về một worktree. Chạy full suite trên Windows (`.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`) và `node dashboard_v2/verify.cjs` | — | [ ] | |
| E2 | Khởi động lại Control Center trên code nhánh, chỉ khi người dùng đồng ý và không có job nào chạy | — | [ ] | |
| E3 | So `/dashboard-v2` với `/` (chỉ GET): số video từng nhóm, hàng đợi, tiến độ, revision và lỗi của toàn bộ job thật | — | [ ] | |
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
| 2026-10-03 | (commit docs này) | Điền hash commit vào nhật ký | — | Như dòng dưới |
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
