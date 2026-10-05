# Kế hoạch tính năng Tải video (tải thật)

Cập nhật: 2026-10-05. Nhánh `feat/video-download` (tách từ `feat/dashboard-v2` a7d8f18), worktree `E:\DungChung\BiliFlow\temp\wt-video-download`.

Trạng thái: **D0, D1 xong** (2026-10-05): công cụ đã cài và khóa phiên bản, kiểm tra giấy phép đạt; backend tải (hàng đợi, yt-dlp, kiểm tra, chuyển vào input, phục hồi, dọn) có test với yt-dlp giả. Tiếp theo D2. Phiên làm tính năng báo người dùng bằng tiếng Việt sau mỗi bước.

Trang `#downloads` của Dashboard V2 hiện chỉ là mô phỏng (`dashboard_v2/download-demo.js`, timer trong `app.js`). Các yêu cầu tích hợp ở `docs/DASHBOARD_V2_UPDATE_GUIDE.md` (mục "Trang Tải video" và "Adapter command / PowerShell") vẫn áp dụng; kế hoạch này chốt các điểm còn để ngỏ ở đó.

## 1. Người dùng đã chốt (2026-10-05)

| Điểm | Quyết định |
|---|---|
| Công cụ | yt-dlp (gói PyPI) |
| Chất lượng | tốt nhất, tối đa 1080p; cùng độ phân giải thì ưu tiên H.264 + AAC |
| Playlist | chỉ tải đúng video trong link (`--no-playlist`) |
| Phụ đề | không tải |
| Tải xong | kiểm tra đạt thì tự chuyển vào `input\`; có ô đổi tên, sửa được tới lúc chuyển |
| Điện thoại | dán link và thao tác tải được (điện thoại là remote, file về máy tính) |
| Nguồn | chỉ trang trong danh sách cho phép, chọn bằng combobox |
| Dọn dữ liệu | 3 lớp (mục 7) |
| Nơi làm | máy thật, nhánh riêng; Dashboard V2 merge main độc lập |

## 2. Ranh giới (không làm)

- Không viết extractor, không bắt luồng, không gỡ quảng cáo riêng cho trình phát của một trang. Trang chỉ cho yt-dlp thấy quảng cáo hay trailer thì **không hỗ trợ**: lượt đó báo rõ lý do, không tải gì.
- Không vượt DRM, không dùng cookie, tài khoản hay `--cookies-from-browser`, không giả trình duyệt (curl_cffi) để lách chặn.
- Không tự quét, tự xuất hay tự đăng sau khi tải. Watcher nhận file như khi người dùng tự chép vào.
- Không tự cập nhật yt-dlp. Cập nhật là lệnh người dùng bấm, khóa phiên bản, chạy lại `license-audit`.
- Repo công khai: chỉ YouTube, Bilibili và mục ví dụ (`*.example`) nằm trong repo. Tên miền thật của trang người dùng tự thêm, link và tên video thật không vào repo, log commit hay tài liệu.

## 3. Công cụ và giấy phép (D0)

- `yt-dlp` từ PyPI vào `.venv`, khóa phiên bản (bản mới nhất lúc lập kế hoạch: 2026.08.19). Wheel PyPI chỉ chứa mã Unlicense. **Không** dùng `yt-dlp.exe`: bản PyInstaller gộp mã GPLv3+.
- `yt-dlp-ejs` (Unlicense, kèm MIT/ISC) và Deno (MIT, `deno-x86_64-pc-windows-msvc.zip`, khoảng 43 MB, bản mới nhất lúc lập kế hoạch: v2.9.7) đặt ở `tools\deno\`. YouTube cần hai thứ này để giải chữ ký.
- Gói mạng: certifi (MPL-2.0), brotli (MIT), websockets (BSD-3), requests (Apache-2.0), pycryptodomex (BSD-2). **Không** cài `mutagen` (GPLv2+) và `curl_cffi`.
- ~~Thêm vào `pyproject.toml`, ghi vào `config/license_policy.json`, kiểm bằng `license-audit`~~. Đổi ngày 2026-10-05, người dùng chọn "Giữ cache quét" (xem 3.1):
  - phiên bản, giấy phép, nguồn, SHA-256 của Deno và gói bị cấm (`mutagen`, `curl_cffi`) ghi ở `config/download_tools.json`;
  - bản khóa ở `requirements.lock.txt` (test so hai nơi phải trùng);
  - lệnh kiểm tra: `python -m biliflow.download_tools audit` (thoát 1 khi có mục bị chặn; `--tools-root` cho worktree hay root tạm trỏ về thư mục cài có `tools\`);
  - `license-audit` (model) giữ nguyên. Gộp hai lệnh và thêm vào `pyproject.toml` khi có đợt dọn phiên bản (đợt đó vốn đổi khóa cache).
- Mọi đường dẫn trên ổ E:
  - `--cache-dir cache\yt-dlp`, `DENO_DIR=cache\deno`, `DENO_NO_UPDATE_CHECK=1`, `TEMP`/`TMP` dưới `temp\`: runner đặt cho từng tiến trình con, **không** sửa `scripts/env.ps1`;
  - `--ignore-config` và `--no-plugin-dirs` để không đọc cấu hình hay plugin yt-dlp trên ổ C;
  - `PYTHONUTF8=1` cho tiến trình con (máy đang ở locale cp1252, tên video tiếng Việt hay tiếng Trung sẽ lỗi nếu không đặt).
- Tải công cụ hay gói về máy cần người dùng đồng ý trước, mỗi lần.

### 3.1 Không đổi khóa cache quét

Khóa của `cache\stage-results` (574 MB lúc làm D0) tính từ nội dung các file dưới đây. Sửa một file là mọi kết quả quét cũ mất cache một lần khi thư mục chính chạy code mới. Tính năng tải **không sửa** các file này:
- `pyproject.toml`, `scripts/run.ps1`, `scripts/env.ps1`;
- `config/license_policy.json`, `config/processing_profiles.json`, `config/detection_policy.yaml`, `config/text_review_policy.json`;
- `src/biliflow/__init__.py`, `__main__.py`, `cli.py`, `stage_cache.py`, `cache_dependencies.py`, `license_policy.py`;
- mọi module nằm trong chuỗi import của một bước quét, trong đó có `storage.py`, `probe.py`, `report.py`, `source_hash.py`, `performance.py`, `intervals.py` (chỉ import, không sửa);
- `tools\ffmpeg\bin\*.exe` (kích thước và thời gian sửa), `models\`.

Module mới (`download_*.py`, `storage_summary.py`) không nằm trong chuỗi import nào của bước quét; test `test_download_modules_and_config_stay_out_of_the_scan_cache_key` giữ điều này. `control_center.py`, `phone_access.py`, `cleanup.py` cũng nằm ngoài khóa.

## 4. Kiến trúc

Module mới, giữ dưới 800 dòng mỗi file. `control_center.py` (2385 dòng) chỉ thêm phần nối route.

| File | Việc |
|---|---|
| `src/biliflow/download_sources.py` | đọc `config/download_sources.json` (trong repo) + `config/download_sources.local.json` (gitignore, người dùng tự thêm); kiểm tra link |
| `src/biliflow/download_store.py` | SQLite `state/downloads.sqlite3`: bảng `download_tasks`, `download_events`, `download_settings` |
| `src/biliflow/download_runner.py` | gọi yt-dlp (thăm dò, tải), đọc tiến độ, dừng cả cây tiến trình (psutil) |
| `src/biliflow/download_worker.py` | hàng đợi FIFO 1–3 slot, máy trạng thái, chờ chỗ trống, kiểm tra file, chuyển vào `input`, phục hồi, dọn |
| `src/biliflow/storage_summary.py` | số liệu mục "Dung lượng" (chỉ đọc) |
| `dashboard_v2/*` | nối trang `#downloads` vào API thật (contracts, adapter, app); bản mô phỏng chỉ giữ cho chế độ demo |

Worker chạy trong tiến trình Control Center, tách khỏi scheduler GPU (quét/xuất).

### 4.1 Cấu hình nguồn

Mỗi mục có:
- `id`, `label`;
- `domains`: host chính xác hoặc host con của tên miền liệt kê, ví dụ `youtube.com`, `youtu.be`, `bilibili.com`, `b23.tv`;
- `min_duration_seconds`: YouTube và Bilibili 0, trang phim ví dụ 600;
- `allow_multi_entry`;
- `notes`.

Trang mới chỉ được thêm sau bước "thăm dò" ở D4.

### 4.2 Kiểm tra link (backend, không tin frontend)

- Chỉ `https`/`http`; không userinfo; cổng mặc định; host chuẩn hóa IDNA, chữ thường, khớp `domains` của nguồn đã chọn.
- Không nhận IP literal.
- Phân giải DNS: từ chối loopback, private, link-local, multicast. Giới hạn đã biết: yt-dlp tự theo redirect nên không chặn hết được; danh sách cho phép là lớp chặn chính.
- Mỗi lô tối đa 20 link, tổng tối đa 100 lượt chưa xong. Một link sai hoặc trùng thì từ chối cả lô.
- Link không bao giờ ghép vào chuỗi lệnh: danh sách đối số, `--` trước URL, không `shell=True`, `CREATE_NO_WINDOW`.

### 4.3 Máy trạng thái của một lượt

`QUEUED → PROBING → (NEEDS_CHOICE) → (WAITING_SPACE) → DOWNLOADING → VERIFYING → PUBLISHING → COMPLETED`

Nhánh phụ:
- `STOPPED`: người dùng dừng, giữ file tạm.
- `FAILED`.
- `CANCELLED`: xóa file tạm.
- `INTERRUPTED`: Control Center khởi động lại giữa chừng.
- `EXPIRED`: file tạm đã dọn sau 7 ngày.

`retry` giữ ID, tăng `attempt`, bỏ sự kiện của attempt cũ. Mọi thao tác đều idempotent. Hủy chuyển sang `CANCELLING` cho tới khi cả cây tiến trình đã dừng.

### 4.4 Thăm dò (PROBING)

Lệnh: `python -m yt_dlp --ignore-config --no-playlist --dump-single-json --skip-download --no-warnings [--js-runtimes deno:<path>] -- <url>`

Từ chối với mã lỗi rõ:
- `live_status` là `is_live`, `is_upcoming` hoặc `post_live`;
- định dạng có DRM;
- lỗi cần đăng nhập hoặc giới hạn tuổi;
- trang yt-dlp không hỗ trợ.

Nhiều mục (trang có quảng cáo hoặc trailer):
1. Bỏ mục ngắn hơn `min_duration_seconds`.
2. Còn một mục thì chọn mục đó.
3. Còn nhiều mục thì chọn mục dài nhất nếu dài ít nhất 2 lần mục kế tiếp. Không thì vào `NEEDS_CHOICE`: hiện danh sách tên và thời lượng cho người dùng chọn.
4. Không còn mục nào thì vào `FAILED`, mã `ONLY_SHORT_ENTRIES`: "Không tìm thấy phim, chỉ thấy N video ngắn (…), có thể là quảng cáo".

Lưu tên gốc, thời lượng, dung lượng ước tính (`filesize`, `filesize_approx` hoặc bitrate × thời lượng) và mục đã chọn.

### 4.5 Chỗ trống (WAITING_SPACE)

- Dùng `storage.storage_status`: hiện giữ 20% ổ hoặc ít nhất 100 GB.
- Cần trống = ước tính × 2,2 (file hình + tiếng + bản ghép) cộng mức giữ lại.
- Thiếu chỗ thì chờ, kiểm tra lại mỗi 60 giây, không báo lỗi.
- Không ước tính được dung lượng thì dùng `--max-filesize` theo chỗ còn lại.

### 4.6 Tải (DOWNLOADING)

Thư mục: `temp\downloads\<task_id>\` (chỉ lượt đó dùng).

Đối số chính:
- `-S "res:1080,vcodec:h264,acodec:aac"`, `--merge-output-format mp4`;
- `--no-playlist` (hoặc `--playlist-items <n>` khi đã chọn mục);
- `--paths home:<dir> --paths temp:<dir>\frag`, `-o "%(id)s.%(ext)s"`;
- `--continue`, `--newline`, `--no-mtime`;
- `--progress-template` với tiền tố riêng (`BFPROG`): `downloaded_bytes`, `total_bytes`, `total_bytes_estimate`, `speed`, `eta`;
- `--ffmpeg-location tools\ffmpeg\bin`, `--cache-dir cache\yt-dlp`;
- `--no-write-subs --no-write-auto-subs --no-embed-subs --no-write-thumbnail`;
- `--socket-timeout 30 --retries 10 --fragment-retries 10`.

Tiến độ:
- Không có `total_bytes` thì hiện số byte đã tải, không phần trăm, không ETA.
- Log chỉ lưu tối đa 200 dòng mỗi lượt và che chuỗi giống token, cookie hay header.

Dừng và tiếp tục:
- `stop` dừng cây tiến trình, giữ file tạm.
- `resume` chạy lại với `--continue`; tải nối tiếp phụ thuộc trang nguồn.

### 4.7 Kiểm tra (VERIFYING)

- ffprobe: ít nhất 1 luồng hình và 1 luồng tiếng.
- Thời lượng lệch không quá max(2 s, 1%) so với lúc thăm dò.
- ffmpeg `-v error` giải mã 5 giây đầu và 5 giây cuối không lỗi.
- Phần mở rộng nằm trong `job_import.VIDEO_EXTENSIONS`.

### 4.8 Chuyển vào input (PUBLISHING)

Tên file:
- Tên do người dùng đặt (mặc định là tên gốc), chuẩn hóa NFC.
- Bỏ ký tự Windows cấm (`\ / : * ? " < > |` và ký tự điều khiển), bỏ dấu chấm hay khoảng trắng cuối, tránh tên dành riêng (CON, NUL, COM1, …).
- Tối đa 150 ký tự, đuôi `.mp4`.
- Trùng thì thêm " (2)", " (3)"…

Di chuyển: `os.rename` từ `temp\downloads\…` sang `input\` cùng ổ E. Trên Windows `os.rename` báo lỗi khi đích đã tồn tại, nên **không bao giờ ghi đè**. Không dùng `os.replace`.

Sau khi chuyển:
- Tên khóa lại. `rename` sau mốc này bị từ chối.
- Ghi `output_path` và SHA-256 (tính trước khi chuyển) vào lượt.
- Watcher nhận file sau `stable_seconds` (60 s).

### 4.9 Phục hồi khi khởi động

- `PROBING`, `DOWNLOADING`, `VERIFYING` chuyển thành `INTERRUPTED`. Không tự chạy lại; người dùng bấm Thử lại.
- `PUBLISHING`: file đích có, đúng kích thước và SHA-256 thì `COMPLETED`; file tạm còn thì `INTERRUPTED`; còn lại `FAILED` kèm sự kiện lỗi, không đoán.

### 4.10 Thiết kế chi tiết D1 (chốt 2026-10-05, trước khi viết code)

File (mỗi file dưới 800 dòng):
- `config/download_sources.json`: chỉ YouTube và Bilibili. Mẫu cho trang người dùng tự thêm: `config/download_sources.local.example.json` (tên miền `*.example`). Bản thật `config/download_sources.local.json` nằm trong `.gitignore`. Trùng `id` giữa hai file thì bỏ file local và báo cảnh báo.
- `download_sources.py`: đọc, kiểm tra cấu hình, kiểm tra lô link (mục 4.2). DNS nhận hàm phân giải giả trong test.
- `download_store.py`: SQLite `state/downloads.sqlite3` (WAL, khóa luồng như `JobStore`). Bảng `download_tasks`, `download_events` (có `attempt`), `download_log` (tối đa 200 dòng mỗi lượt), `download_settings`. Chuyển trạng thái kiểu so-rồi-đổi: chỉ đổi khi trạng thái hiện tại nằm trong tập cho phép. Cập nhật tiến độ kèm `attempt`, nên tiến trình của attempt cũ không ghi đè được.
- `download_runner.py`: dựng lệnh (danh sách đối số, `--` trước URL), thăm dò, tải, đọc dòng `BFPROG`, che token trong log, phân loại lỗi, dừng cả cây tiến trình bằng psutil. Lệnh yt-dlp nhận tiền tố từ ngoài: thật là `python -P -m yt_dlp` (`-P`: thư mục lượt, nơi yt-dlp ghi file, không vào `sys.path`), test là `python tests/fake_yt_dlp.py`.
- `download_files.py`: chuẩn hóa tên, chọn tên không trùng, xóa an toàn chỉ trong `temp\downloads\`, kiểm tra file bằng ffprobe/ffmpeg, SHA-256.
- `download_worker.py`: hàng đợi, máy trạng thái, chờ chỗ trống, chuyển vào `input`, thao tác người dùng.
- `download_upkeep.py`: phục hồi khi khởi động, dọn file tạm, quét mỗi giờ (tách khỏi `download_worker.py` cho dưới 800 dòng; `DownloadWorker` kế thừa).
- `cleanup.py`: thêm `DOWNLOAD_CACHE_POLICIES` (`cache/yt-dlp`, `cache/deno`: 1 GB, 30 ngày) và `prune_download_caches`. Worker gọi hàm này **chỉ khi không có lượt nào chạy**. Không thêm vào `FILE_CACHE_POLICIES`: hàm đó chạy sau mỗi bước quét, lúc Deno có thể đang giữ file cache. File đang bị khóa thì bỏ qua, không làm hỏng lượt dọn.

Bổ sung cho đối số tải (4.6): `-f "bv*+ba/b"`, `--remux-video mp4` (file cuối luôn là mp4), `--restrict-filenames`, `--no-plugin-dirs`, `--no-cookies`, `--no-cookies-from-browser`, `--no-update`, `--print-to-file after_move:%(filepath)s <thư mục lượt>\final-path.txt` (không dùng `--print` vì nó bật chế độ im lặng). Thăm dò dùng cùng `-f` và `-S` để ước tính dung lượng đúng định dạng sẽ tải.

Trạng thái giữ slot: PROBING, WAITING_SPACE, DOWNLOADING, VERIFYING, PUBLISHING, CANCELLING. NEEDS_CHOICE nhả slot. "Chưa xong" (giới hạn 100) là mọi trạng thái trừ COMPLETED, CANCELLED, EXPIRED. Link trùng với lượt chưa xong hoặc đã COMPLETED thì từ chối cả lô; muốn tải lại thì xóa dòng cũ.

| Thao tác | Trạng thái được phép | Kết quả |
|---|---|---|
| stop | QUEUED, PROBING, WAITING_SPACE, DOWNLOADING | STOPPED, giữ file tạm |
| resume | STOPPED, INTERRUPTED | QUEUED, giữ file tạm, tải với `--continue` |
| cancel | mọi trạng thái trừ PUBLISHING, COMPLETED, EXPIRED | CANCELLING → CANCELLED, xóa file tạm |
| retry | FAILED, INTERRUPTED, STOPPED, CANCELLED, EXPIRED | attempt + 1, bỏ sự kiện cũ, xóa file tạm, thăm dò lại |
| remove | COMPLETED, CANCELLED, FAILED, STOPPED, INTERRUPTED, EXPIRED | xóa dòng và file tạm, không đụng `input` |
| rename | mọi trạng thái trừ PUBLISHING, COMPLETED | đổi tên sẽ dùng khi chuyển |
| choose | NEEDS_CHOICE | QUEUED với mục đã chọn |
| cleanup-temp | STOPPED, FAILED, INTERRUPTED | xóa file tạm → EXPIRED |

Gọi lại cùng thao tác khi đã ở trạng thái đích thì không lỗi (idempotent). Nguồn có `allow_multi_entry: false` (YouTube, Bilibili) mà thăm dò ra nhiều mục thì FAILED `MULTIPLE_ENTRIES`. Một video duy nhất ngắn hơn `min_duration_seconds` thì FAILED `ONLY_SHORT_ENTRIES`.

Bổ sung sau review D1 (python-reviewer và security-reviewer, 2026-10-05):
- Kiểm tra lại link (nguồn còn trong danh sách, DNS không trỏ vào mạng nội bộ) ngay trước thăm dò và trước khi tải, kể cả lượt tiếp tục hay thử lại.
- Tên miền: sau IDNA, mỗi nhãn chỉ gồm `a-z 0-9 - _`. Link có `\` hay `%` trong tên miền bị từ chối (`NO_HOST`), vì urllib3/requests đọc tên miền khác `urlsplit`. Link hỏng dạng (`https://[::1/`) báo lỗi riêng dòng đó (`BAD_URL`), không làm hỏng cả lô.
- Lỗi ghi cơ sở dữ liệu giữa chừng: runner luôn dừng cả cây yt-dlp; luồng của lượt luôn nhả slot. Lượt còn ở trạng thái chạy mà không có luồng thì `dispatch` xử lý ở vòng sau (`_reconcile`): WAITING_SPACE về QUEUED, CANCELLING thử xóa lại, PUBLISHING kiểm tra như lúc khởi động, còn lại INTERRUPTED.
- Ghi trạng thái cuối và nhả slot trong cùng một lần giữ khóa; lượt có luồng cũ chưa thoát thì `dispatch` chưa chạy lại (không chạy đôi).
- CANCELLING chỉ thành CANCELLED khi thư mục tạm đã thật sự bị xóa; file còn bị giữ thì thử lại sau mỗi 30 giây. Thử lại và "Dọn file tạm" không làm khi file tạm cũ chưa xóa được.
- Chuyển vào `input` bị chương trình khác giữ file (PermissionError): thử lại 5 lần cách 2 giây, sau đó INTERRUPTED `PUBLISH_BLOCKED`, giữ file đã kiểm tra; bấm Tiếp tục để thử lại.
- Chỗ trống: trừ phần các lượt đang tải còn cần (ước tính × 2,2 trừ phần đã ghi); kiểm tra dưới khóa để hai lượt không cùng tính một chỗ trống.
- File cuối không phải mp4 (hiếm, do `--remux-video mp4`) giữ đuôi thật khi vào `input`.

Phục hồi (bổ sung 4.9): lưu `pid` và thời điểm tạo của tiến trình yt-dlp. Khi khởi động, tiến trình còn sống, đúng `pid` và đúng thời điểm tạo, thì dừng cả cây trước khi đổi trạng thái. WAITING_SPACE về QUEUED. CANCELLING thì xóa file tạm rồi thành CANCELLED.

## 5. API

Mọi POST dùng `X-BiliFlow-Token` và kiểm tra Origin như các route hiện có.

| Lệnh | Việc |
|---|---|
| `GET /api/downloads` | snapshot: lượt, cài đặt, nguồn (id, label, domains), số liệu chỗ trống |
| `POST /api/downloads` | `{source_id, urls[], rights_confirmed: true}`; trả về các lượt mới hoặc lỗi cho cả lô |
| `POST /api/downloads/<id>/rename` | `{name}`; chỉ trước PUBLISHING |
| `POST /api/downloads/<id>/choose` | `{entry_index}`; chỉ ở NEEDS_CHOICE |
| `POST /api/downloads/<id>/(stop\|resume\|cancel\|retry\|remove)` | `remove` chỉ cho lượt đã kết thúc; xóa dòng và file tạm của lượt, không đụng file trong `input` |
| `POST /api/downloads/settings` | `{slots: 1..3}`; hạ số slot không ngắt lượt đang chạy |
| `POST /api/downloads/cleanup-temp` | dọn file tạm của lượt STOPPED, FAILED, INTERRUPTED (sau xác nhận ở UI) |
| `GET /api/storage-summary` | mục "Dung lượng", chỉ đọc, tính nền và cache 5 phút |

Điện thoại:
- Thêm các route POST của `/api/downloads…` vào `phone_access.PHONE_ALLOWED_POSTS`, có test.
- `source-cleanup`, `source-archive*` và `source-recycle-check` vẫn chỉ cho PC.

## 6. Giao diện (`#downloads`)

- Thay timer mô phỏng bằng adapter gọi API; chế độ demo giữ bản mô phỏng.
- Combobox nguồn lấy từ API. Bỏ mục `phimmoi.example` ở chế độ thật.
- Bắt buộc tick "Tôi có quyền tải và chỉnh sửa video này" (cùng ý với `publishing.confirmation_text` của license policy).
- Mỗi dòng:
  - tên (ô đổi tên tới lúc chuyển vào input), nguồn, trạng thái;
  - tiến độ, tốc độ, còn lại;
  - lỗi, log gập;
  - nút theo trạng thái;
  - danh sách mục để chọn khi `NEEDS_CHOICE`.
- Bộ đếm, bộ lọc, chọn 1–3 slot, nút "Dọn file tạm" (có xác nhận, ghi rõ dung lượng).
- Mục "Dung lượng":
  - kích thước `input`, `output`, `reports`, `cache`, `temp`;
  - phần `input` thuộc video đã xuất xong và dọn được;
  - Thùng rác ổ E (`SHQueryRecycleBinW`, chỉ đọc);
  - ổ còn trống và mức giữ lại;
  - liên kết tới danh sách video dọn được (dùng các nút "Dọn video gốc" / "Lưu trữ" sẵn có, chỉ PC).
- 375 px không tràn ngang, hai theme. Không dùng `backdrop-filter` (R4-B3).

## 7. Dọn dữ liệu

1. **Tự dọn phần của tính năng tải:**
   - Thư mục tạm của lượt bị xóa khi COMPLETED, CANCELLED hoặc remove.
   - STOPPED, FAILED, INTERRUPTED quá 7 ngày chuyển thành EXPIRED và xóa file tạm. Thử lại sẽ tải từ đầu.
   - Lượt COMPLETED, CANCELLED, EXPIRED quá 30 ngày bị xóa khỏi danh sách.
   - Log tối đa 200 dòng mỗi lượt.
   - Thư mục mồ côi trong `temp\downloads\` (không có lượt) quá 24 giờ thì xóa.
   - Chỉ xóa bên trong `temp\downloads\`; kiểm tra đường dẫn đã resolve nằm trong thư mục này.
   - Quét dọn lúc khởi động và mỗi giờ.
2. **Bộ nhớ đệm:** `cache/yt-dlp` và `cache/deno` (1 GB, 30 ngày) nằm trong `cleanup.DOWNLOAD_CACHE_POLICIES`, dọn bằng `prune_download_caches` khi không có lượt nào chạy (mục 4.10), không nằm trong `FILE_CACHE_POLICIES`. Kiểm tra Deno vẫn chạy sau khi cache bị dọn.
3. **Chặn khi ổ gần đầy:** WAITING_SPACE (mục 4.5).
4. **Video gốc và bản xuất:**
   - Không tự xóa.
   - Mục "Dung lượng" chỉ hiển thị và dẫn tới các thao tác người dùng đã có.
   - Không tự làm trống Thùng rác.
   - Quy tắc trong `AGENTS.md` về "Dọn video gốc", "Lưu trữ" và Thùng rác giữ nguyên.

## 8. Các bước và cổng

| Bước | Việc | Cổng |
|---|---|---|
| D0 | Cài công cụ (hỏi trước), giấy phép, đường dẫn cache trên E, `.gitignore` cho `config/download_sources.local.json` | `license-audit` đạt; `python -m yt_dlp --version` và `deno --version` chạy từ E; không có gì ghi sang ổ C |
| D1 | `download_sources`, `download_store`, `download_runner`, `download_worker` | test đơn vị với **yt-dlp giả** (script trong `tests/`, in dòng tiến độ, tạo mp4 nhỏ bằng ffmpeg `testsrc`), không mạng |
| D2 | API, nối route, điện thoại | test route: token, Origin, lô sai, trùng, giới hạn, `PHONE_ALLOWED_POSTS`, từ chối route dọn/lưu trữ trên điện thoại |
| D3 | Giao diện `#downloads` thật, mục "Dung lượng" | `verify*.cjs`, `node --check`; kiểm tra 375/390/1440 px, sáng/tối (Chrome chạy ẩn qua CDP vì máy không có Playwright) |
| D4 | Full suite; tải thật trên Control Center thử: root tạm dưới `temp\`, cổng khác (ví dụ 8797), `input` của root tạm | 1–2 link ngắn công khai (YouTube, Bilibili) do người dùng đưa: tải, kiểm tra, vào `input` tạm, watcher nhận. Link trang phim: **chỉ thăm dò**, báo trường hợp (một mục / nhiều mục / chỉ quảng cáo) trước khi thêm nguồn |
| D5 | CHANGELOG, PROJECT_STATUS, SESSION_HANDOFF, AGENTS.md (agent không tự bắt đầu tải trên Control Center thật); người dùng test trên máy thật | chuyển thư mục chính và khởi động lại Control Center chỉ khi người dùng đồng ý và không có job chạy |

Test D1/D2 phải gồm:
- link sai, host ngoài danh sách, IP nội bộ, userinfo;
- trùng trong lô;
- live, DRM, cần đăng nhập (bằng mã lỗi giả);
- nhiều mục: chọn đúng mục dài, NEEDS_CHOICE, chỉ mục ngắn;
- WAITING_SPACE rồi chạy tiếp;
- tiến độ không có total;
- dừng, tiếp tục, hủy (cây tiến trình đã chết, file tạm đã xóa);
- thử lại (attempt mới, sự kiện cũ bị bỏ);
- kiểm tra file hỏng (thiếu tiếng, sai thời lượng, giải mã lỗi);
- tên: ký tự cấm, tên dành riêng, quá dài, trùng tên;
- không ghi đè; đổi tên bị khóa sau PUBLISHING;
- phục hồi sau khởi động cho từng trạng thái;
- dọn 7 ngày, 30 ngày, thư mục mồ côi, không xóa ra ngoài `temp\downloads\`;
- log che token.

## 9. Dừng lại và hỏi người dùng khi

- Một trang cần cookie, đăng nhập hoặc giả trình duyệt mới tải được.
- `license-audit` không đạt, hoặc một phụ thuộc kéo theo giấy phép ngoài danh sách.
- Video tải về có codec mà bước quét hay xuất của BiliFlow không giải mã được (ví dụ AV1). Khi đó cân nhắc ép H.264 khi có, hoặc chuyển mã: là quyết định của người dùng.
- Cần đụng `input\`, `output\`, `archive\` thật, hoặc POST vào Control Center thật.
- yt-dlp cần cập nhật mới chạy được.

## 10. Rủi ro đã biết

- Trang nguồn đổi cách chạy (nhất là YouTube): cần cập nhật yt-dlp theo lệnh người dùng.
- yt-dlp theo redirect nội bộ: kiểm tra IP chỉ chặn được ở link đầu; danh sách cho phép là lớp chặn chính.
- Ước tính dung lượng có thể sai: `--max-filesize` và lần kiểm tra chỗ trống trước khi chuyển vào input là lớp chặn sau.
- Tải nối tiếp sau khi dừng không phải trang nào cũng hỗ trợ: UI không hứa nối tiếp.

## 11. Nhật ký

| Ngày | Commit | Việc | Kết quả | Còn lại |
| --- | --- | --- | --- | --- |
| 2026-10-05 | a59c6f2 | Người dùng chốt yêu cầu, ranh giới và cách làm; tạo nhánh và worktree; ghi kế hoạch | Chưa cài, chưa code | D0 ở một phiên local mới |
| 2026-10-05 | (commit này) | D0. Người dùng đồng ý: 8 gói PyPI vào `.venv` chính (yt-dlp 2026.8.19, yt-dlp-ejs 0.8.0, requests 2.34.2, urllib3 2.7.0, charset-normalizer 3.5.0, brotli 1.2.0, websockets 17.0.1, pycryptodomex 3.23.0; bản theo extra `pin` của yt-dlp; tải bằng `tools\uv` với cache ở `cache\uv`; dry-run trước: chỉ thêm, không đổi gói nào); Deno 2.9.7 chép từ bản WinGet có sẵn (SHA-256 `e020f3e2…` trùng `deno-x86_64-pc-windows-msvc.sha256sum` của bản phát hành), không tải. Người dùng chọn "Giữ cache quét" (3.1): `config/download_tools.json`, `download_tools.py` + lệnh `audit`, `requirements.lock.txt`, `.gitignore` cho `config/download_sources.local.json`, `THIRD_PARTY_NOTICES.md` | Máy thật: `python -m yt_dlp --version` → 2026.08.19, `deno --version` → 2.9.7, `deno eval` chạy, chạy từ `E:\…\tools\deno`; yt-dlp `-v` thấy `yt_dlp_ejs-0.8.0`, `JS runtimes: deno-2.9.7`, ffmpeg 9.0.1 của dự án, không có plugin. Cache Deno ghi vào `E:\…\cache\deno`; ổ C không có thư mục yt-dlp/Deno mới (so trước và sau). `download_tools audit --tools-root E:\DungChung\BiliFlow`: `allowed: true`, 0 chặn, `mutagen` và `curl_cffi` không có. `license-audit` (model): 9 cho phép, 0 chặn. Test `test_download_tools` + `test_license_policy` OK | D1 |
| 2026-10-05 | (commit này) | D1: `download_sources`, `download_store`, `download_probe`, `download_runner`, `download_files`, `download_worker` + `download_upkeep`, `cleanup.DOWNLOAD_CACHE_POLICIES`, yt-dlp giả `tests/fake_yt_dlp.py`. Hai agent review (python, security): 1 HIGH, 6 MEDIUM, 9 LOW; đã sửa theo mục "Bổ sung sau review D1" (4.10); còn để lại: `create_time` không đọc được thì lưu 0 và lần khởi động sau không dừng tiến trình đó (an toàn: không bao giờ dừng nhầm pid) | 155 test D1 OK (yt-dlp giả, không mạng, root tạm). Full suite 1466 test: chỉ 28 lỗi có sẵn của `test_job_pipeline`/`test_job_ocr_option` vì worktree không có `input/*.mp4` (không liên quan). `python -P -m yt_dlp --version` chạy; `json.py` trong thư mục làm việc không che được module chuẩn khi có `-P` | D2 |
