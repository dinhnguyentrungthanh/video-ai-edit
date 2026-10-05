# Kế hoạch tính năng Tải video (tải thật)

Cập nhật: 2026-10-05. Nhánh `feat/video-download` (tách từ `feat/dashboard-v2` a7d8f18), worktree `E:\DungChung\BiliFlow\temp\wt-video-download`.

Trạng thái: **D0–D5 xong** (2026-10-05), còn người dùng test trên máy thật: công cụ đã cài và khóa phiên bản, kiểm tra giấy phép đạt; backend tải (hàng đợi, yt-dlp, kiểm tra, chuyển vào input, phục hồi, dọn) có test với yt-dlp giả; API `/api/downloads…` và `/api/storage-summary` nối vào Control Center (PC và điện thoại) có test route; trang `#downloads` thật trong Dashboard V2 (PC và điện thoại); tải thật một video YouTube công khai trên Control Center thử (root tạm), xem "Kết quả D4" ở mục 8. Sau D4 người dùng bỏ danh sách nguồn: nhận link từ trang nào cũng được, bước thăm dò quyết định (mục 4.1, "D4b"). D5: CHANGELOG, PROJECT_STATUS, SESSION_HANDOFF, `AGENTS.md` (agent không tự bắt đầu tải trên Control Center thật). Tiếp theo: người dùng test trên máy thật (chuyển thư mục chính và khởi động lại Control Center khi người dùng đồng ý, không có job chạy). Phiên làm tính năng báo người dùng bằng tiếng Việt sau mỗi bước.

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
| Nguồn | ~~chỉ trang trong danh sách cho phép, chọn bằng combobox~~. Đổi sau D4 (D4b): không chọn nguồn, nhận link từ trang nào cũng được; yt-dlp thăm dò trang trước khi tải, trang không có video đọc được báo "Chưa hỗ trợ" (mục 4.1) |
| Dọn dữ liệu | 3 lớp (mục 7) |
| Nơi làm | máy thật, nhánh riêng; Dashboard V2 merge main độc lập |

## 2. Ranh giới (không làm)

- Không viết extractor, không bắt luồng, không gỡ quảng cáo riêng cho trình phát của một trang. Trang chỉ cho yt-dlp thấy quảng cáo hay trailer thì **không hỗ trợ**: lượt đó báo rõ lý do, không tải gì.
- Không vượt DRM, không dùng cookie, tài khoản hay `--cookies-from-browser`, không giả trình duyệt (curl_cffi) để lách chặn.
- Không tự quét, tự xuất hay tự đăng sau khi tải. Watcher nhận file như khi người dùng tự chép vào.
- Không tự cập nhật yt-dlp. Cập nhật là lệnh người dùng bấm, khóa phiên bản, chạy lại `license-audit`.
- Repo công khai: tên miền trong code, test và tài liệu chỉ là mục ví dụ (`*.example`). Tên miền thật của trang người dùng dùng, link và tên video thật không vào repo, log commit hay tài liệu.

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
| `src/biliflow/download_links.py` | kiểm tra lô link (mục 4.2). Trước D4b là `download_sources.py` kèm danh sách nguồn `config/download_sources*.json` (đã bỏ) |
| `src/biliflow/download_probe.py` | đọc kết quả thăm dò, chọn mục cần tải theo cách yt-dlp đọc trang (mục 4.1, 4.4) |
| `src/biliflow/download_store.py` | SQLite `state/downloads.sqlite3`: bảng `download_tasks`, `download_events`, `download_settings` |
| `src/biliflow/download_runner.py` | gọi yt-dlp (thăm dò, tải), đọc tiến độ, dừng cả cây tiến trình (psutil) |
| `src/biliflow/download_worker.py` | hàng đợi FIFO 1–3 slot, máy trạng thái, chờ chỗ trống, kiểm tra file, chuyển vào `input`, phục hồi, dọn |
| `src/biliflow/storage_summary.py` | số liệu mục "Dung lượng" (chỉ đọc) |
| `dashboard_v2/*` | nối trang `#downloads` vào API thật (contracts, adapter, app); bản mô phỏng chỉ giữ cho chế độ demo |

Worker chạy trong tiến trình Control Center, tách khỏi scheduler GPU (quét/xuất).

### 4.1 Trang nào tải được (D4b, thay cho danh sách nguồn)

~~Danh sách nguồn (`config/download_sources.json`, `.local.json`, combobox): mỗi nguồn có `domains`, `min_duration_seconds`, `allow_multi_entry`.~~ Người dùng bỏ ngày 2026-10-05 sau D4, chọn "Mọi trang, thăm dò":
- Nhận link `http`/`https` công khai từ trang nào cũng được; vẫn chặn như mục 4.2.
- Bước thăm dò (`yt-dlp --dump-single-json --skip-download`, không tải gì) là cách nhận biết trang tải được hay không. Trang yt-dlp không đọc được video (`Unsupported URL`, hoặc không có mục nào) thành FAILED `UNSUPPORTED` / `NO_ENTRIES`, lời nhắn "Trang này chưa được hỗ trợ: …", nhãn "Chưa hỗ trợ" trên trang. DRM, cần đăng nhập, đang phát trực tiếp: báo lý do như trước.
- Luật chọn mục theo bộ đọc yt-dlp dùng (`extractor_key` của kết quả thăm dò):
  - trang có bộ đọc riêng (YouTube, Bilibili, …): chỉ video trong link, độ dài nào cũng được; link ra nhiều video (danh sách phát, kênh) thì FAILED `MULTIPLE_ENTRIES`;
  - trang đọc bằng bộ đọc chung (`Generic`): bỏ mục ngắn hơn 10 phút (`GENERIC_MIN_DURATION_SECONDS`, để bỏ quảng cáo), chọn như mục 4.4; chỉ có video ngắn thì FAILED `ONLY_SHORT_ENTRIES`. Video không rõ thời lượng (link file trực tiếp) vẫn được tải.
- Thăm dò đọc tối đa 10 mục (`--playlist-end 10`, `MAX_PROBE_ENTRIES`): link danh sách phát hay kênh dán nhầm không bị đọc từng video tới hết 300 giây. Trang đọc chung có phim ở mục thứ 11 trở đi thì không thấy phim đó (chấp nhận).
- Link danh sách phát hay kênh trống trên trang có bộ đọc riêng: FAILED `NO_VIDEOS` ("Link không có video nào"), không gọi là "chưa hỗ trợ".
- Đổi lại: không còn danh sách cho phép nên yt-dlp có thể theo chuyển hướng của một trang lạ, hay đọc link nhúng trong trang (iframe, `<video src>`). Kiểm tra DNS chỉ chặn được ở link đầu (mục 10).
- Thêm sau review D4b (security-reviewer, code-reviewer, 2026-10-05):
  - Lời báo lỗi của yt-dlp bỏ link và địa chỉ IP (`<link>`, `<địa chỉ>`) trước khi lưu và trước khi xét mã lỗi: trang lạ không dò được cổng hay máy trong mạng qua lời báo, và chữ trong link (`login`, `premium`…) không làm "Unsupported URL" thành "cần đăng nhập". Luật `UNSUPPORTED` xét đầu tiên.
  - Chốt dung lượng khi đang tải (`SizeGuard`): lượt tải dừng với `TOO_LARGE` khi số byte yt-dlp báo vượt phần chỗ trống dành cho nó ((còn trống − phần lượt khác cần − phần giữ lại) / 2,2), hoặc thư mục của lượt vượt gấp 2,2 lần số đó (đọc mỗi 2 giây, cho cả trình tải không báo tiến độ). Dung lượng trang báo không bao giờ là giới hạn.
  - Tên mục từ trang tối đa 300 ký tự; ffprobe/ffmpeg kiểm tra file với `-protocol_whitelist file`.
  - Cơ sở dữ liệu tải tạo trước D4b (còn cột `source_id`) được bỏ cột đó khi mở (chỉ có ở root thử); `.gitignore` vẫn bỏ qua `config/download_sources.local.json` để bản cũ có tên miền thật không bị commit.
- Thử thật (2026-10-05): link trang phim tham khảo của người dùng → "Trang này chưa được hỗ trợ"; link YouTube thử → READY, 1080p H.264.

### 4.2 Kiểm tra link (backend, không tin frontend)

- Chỉ `https`/`http`; không userinfo; cổng mặc định; host chuẩn hóa IDNA, chữ thường, phải có dấu chấm (từ chối `localhost`, tên máy trong mạng nội bộ trước cả khi hỏi DNS).
- Không nhận IP literal, kể cả dạng chỉ thành địa chỉ sau IDNA (`１.１.１.１`, `1.2.3.4.`).
- Tên chỉ có trong mạng nội bộ (`*.localhost`, `*.local`, `*.internal`, `*.lan`, `*.home.arpa`, `*.localdomain`) bị từ chối trước khi hỏi DNS (`LOCAL_HOST`).
- Phân giải DNS: từ chối loopback, private, link-local, multicast. Giới hạn đã biết: yt-dlp tự theo redirect nên không chặn hết được (từ D4b không còn danh sách cho phép làm lớp chặn chính, mục 10).
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

Lệnh: `python -m yt_dlp --ignore-config --no-playlist --dump-single-json --skip-download --playlist-end 10 --no-warnings [--js-runtimes deno:<path>] -- <url>`

Từ chối với mã lỗi rõ:
- `live_status` là `is_live`, `is_upcoming` hoặc `post_live`;
- định dạng có DRM;
- lỗi cần đăng nhập hoặc giới hạn tuổi;
- trang yt-dlp không hỗ trợ.

Nhiều mục (trang có quảng cáo hoặc trailer):
1. Bỏ mục ngắn hơn ngưỡng (D4b: 10 phút với trang đọc chung, 0 với trang có bộ đọc riêng; mục 4.1).
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
- ~~`config/download_sources.json`: chỉ YouTube và Bilibili; `config/download_sources.local.json` cho trang người dùng tự thêm.~~ Bỏ ở D4b (mục 4.1).
- `download_links.py` (trước D4b: `download_sources.py`): kiểm tra lô link (mục 4.2). DNS nhận hàm phân giải giả trong test.
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

Gọi lại cùng thao tác khi đã ở trạng thái đích thì không lỗi (idempotent). Trang có bộ đọc riêng (YouTube, Bilibili…) mà thăm dò ra nhiều mục thì FAILED `MULTIPLE_ENTRIES`. Trang đọc chung chỉ có video ngắn hơn 10 phút thì FAILED `ONLY_SHORT_ENTRIES` (D4b, mục 4.1; trước đó theo `allow_multi_entry` và `min_duration_seconds` của nguồn).

Bổ sung sau review D1 (python-reviewer và security-reviewer, 2026-10-05):
- Kiểm tra lại link (DNS không trỏ vào mạng nội bộ; trước D4b thêm: nguồn còn trong danh sách) ngay trước thăm dò và trước khi tải, kể cả lượt tiếp tục hay thử lại.
- Tên miền: sau IDNA, mỗi nhãn chỉ gồm `a-z 0-9 - _`. Link có `\` hay `%` trong tên miền bị từ chối (`NO_HOST`), vì urllib3/requests đọc tên miền khác `urlsplit`. Link hỏng dạng (`https://[::1/`) báo lỗi riêng dòng đó (`BAD_URL`), không làm hỏng cả lô.
- Lỗi ghi cơ sở dữ liệu giữa chừng: runner luôn dừng cả cây yt-dlp; luồng của lượt luôn nhả slot. Lượt còn ở trạng thái chạy mà không có luồng thì `dispatch` xử lý ở vòng sau (`_reconcile`): WAITING_SPACE về QUEUED, CANCELLING thử xóa lại, PUBLISHING kiểm tra như lúc khởi động, còn lại INTERRUPTED.
- Ghi trạng thái cuối và nhả slot trong cùng một lần giữ khóa; lượt có luồng cũ chưa thoát thì `dispatch` chưa chạy lại (không chạy đôi).
- CANCELLING chỉ thành CANCELLED khi thư mục tạm đã thật sự bị xóa; file còn bị giữ thì thử lại sau mỗi 30 giây. Thử lại và "Dọn file tạm" không làm khi file tạm cũ chưa xóa được.
- Chuyển vào `input` bị chương trình khác giữ file (PermissionError): thử lại 5 lần cách 2 giây, sau đó INTERRUPTED `PUBLISH_BLOCKED`, giữ file đã kiểm tra; bấm Tiếp tục để thử lại.
- Chỗ trống: trừ phần các lượt đang tải còn cần (ước tính × 2,2 trừ phần đã ghi); kiểm tra dưới khóa để hai lượt không cùng tính một chỗ trống.
- File cuối không phải mp4 (hiếm, do `--remux-video mp4`) giữ đuôi thật khi vào `input`.

Phục hồi (bổ sung 4.9): lưu `pid` và thời điểm tạo của tiến trình yt-dlp. Khi khởi động, tiến trình còn sống, đúng `pid` và đúng thời điểm tạo, thì dừng cả cây trước khi đổi trạng thái. WAITING_SPACE về QUEUED. CANCELLING thì xóa file tạm rồi thành CANCELLED.

## 5. API

Mọi POST dùng `X-BiliFlow-Token` và kiểm tra Host như các route hiện có (trên điện thoại thêm cookie mã truy cập, Origin cùng nguồn và danh sách route cho phép).

| Lệnh | Việc |
|---|---|
| `GET /api/downloads` | snapshot: lượt, bộ đếm, cài đặt (`slots`, `max_slots`), chỗ trống, file tạm, lượt đang chạy, lỗi gần nhất của worker và thời điểm |
| `GET /api/downloads/<id>` | một lượt cùng sự kiện của attempt hiện tại và log đã che |
| `POST /api/downloads` | `{urls[], rights_confirmed: true}` (trước D4b có `source_id`); trả về các lượt mới hoặc lỗi cho cả lô |
| `POST /api/downloads/<id>/rename` | `{name}`; chỉ trước PUBLISHING |
| `POST /api/downloads/<id>/choose` | `{entry_index}`; chỉ ở NEEDS_CHOICE |
| `POST /api/downloads/<id>/(stop\|resume\|cancel\|retry\|remove)` | `remove` chỉ cho lượt đã kết thúc; xóa dòng và file tạm của lượt, không đụng file trong `input` |
| `POST /api/downloads/settings` | `{slots: 1..3}`; hạ số slot không ngắt lượt đang chạy |
| `POST /api/downloads/cleanup-temp` | `{confirm: true, ids?}`: dọn file tạm của lượt STOPPED, FAILED, INTERRUPTED (sau xác nhận ở UI). Có `ids` (các lượt hộp xác nhận đã liệt kê, lấy từ `temp.ids` của snapshot) thì chỉ dọn các lượt đó; lượt dừng hay lỗi trong lúc hộp đang mở giữ phần đã tải |
| `GET /api/storage-summary` | mục "Dung lượng", chỉ đọc, tính nền và cache 5 phút |

Điện thoại:
- Thêm các route POST của `/api/downloads…` vào `phone_access.PHONE_ALLOWED_POSTS`, có test.
- `source-cleanup`, `source-archive*` và `source-recycle-check` vẫn chỉ cho PC.

Đã làm ở D2 (2026-10-05):
- `download_api.py` (`DownloadService`): route, `public_task` chỉ trả tên file trong `input` (không trả đường dẫn đầy đủ hay thư mục tạm). Lô sai: 400 `BATCH_REJECTED` kèm lỗi từng dòng. Thao tác sai trạng thái: 409. Không thấy lượt: 404.
- `storage_summary.py`: mục "Dung lượng", tính ở luồng nền, giữ 5 phút; lỗi của một phần (Thùng rác, số dọn được) chỉ hiện ở phần đó. Số "dọn được" dùng cùng đánh giá chỉ đọc của gợi ý "Dọn video gốc" (`ControlCenter.cleanable_sources`), không hash, không hỏi Thùng rác.
- `control_center.py`: tạo dịch vụ trong `__init__` (cơ sở dữ liệu tải lỗi thì route tải trả 503, quét và duyệt vẫn chạy); chạy worker trong `serve()` sau scheduler và watcher; dừng trong `stop()` và khi `serve()` kết thúc (lượt đang tải thành INTERRUPTED, bấm Tiếp tục để tải nối).
- Thân POST tối đa 64 KB như các route khác (20 link × 2048 ký tự vẫn vừa).
- Sau review D2 (security-reviewer: 3 MEDIUM, 5 LOW, đã sửa hết):
  - Đường dẫn thư mục cài đặt trong lỗi, sự kiện, log yt-dlp, chỗ trống và lỗi worker được thay bằng `<BiliFlow>` trước khi tới trang hay điện thoại.
  - Id dài hơn 12 chữ số trả 404 (không làm tràn số của SQLite).
  - Vòng lặp worker luôn chạy, kể cả khi phục hồi lúc khởi động hay lượt dọn định kỳ lỗi; một lượt không xử lý được (ví dụ file đang bị chương trình khác giữ) chỉ ghi lỗi, không chặn các lượt khác.
  - Lỗi gần nhất của hàng tải được giữ kèm thời điểm (`worker_error`, `worker_error_at` trong snapshot) cho tới khi có lỗi mới; lượt chạy sau không xóa nó.
  - Khi tắt, mọi cây tiến trình bị dừng cùng lúc, chung một hạn chờ.
  - "Xóa" chỉ xóa dòng khi thư mục tạm của lượt đã hết; file còn bị giữ thì báo lỗi và giữ dòng.
  - Bấm làm mới "Dung lượng" trong lúc đang tính thì tính thêm một lượt sau lượt đang chạy.
  - Snapshot đọc `running` và file tạm không chờ khóa của worker.

## 6. Giao diện (`#downloads`)

- Thay timer mô phỏng bằng adapter gọi API; chế độ demo giữ bản mô phỏng.
- ~~Combobox nguồn lấy từ API.~~ D4b: không còn ô chọn nguồn (cả bản demo); dòng lỗi của trang yt-dlp không đọc được có nhãn "Chưa hỗ trợ".
- Bắt buộc tick "Tôi có quyền tải và chỉnh sửa video này" (cùng ý với `publishing.confirmation_text` của license policy).
- Mỗi dòng:
  - tên (ô đổi tên tới lúc chuyển vào input), link, trạng thái;
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

Đã làm ở D3 (2026-10-05):
- Ba file mới, chỉ `live.html` nạp (bản demo `index.html` giữ mô phỏng cũ):
  - `download-core.js`: nhãn trạng thái, nhóm lọc, nút theo trạng thái, tiến độ, kiểm tra lô. Các tập trạng thái giống `download_worker.py` và `download_store.py`; `tests/test_dashboard_v2_downloads.py` so sánh hai bên.
  - `download-view.js`: dựng HTML. Mọi chữ đến từ trang web (tên, link, mục chọn, lỗi, log) đi qua `esc()`.
  - `download-live.js`: trạng thái trang, làm mới từng phần, xử lý nút.
- Danh sách tải lại mỗi 2 giây, chỉ khi trang `#downloads` đang mở; rời trang thì ngừng. Khi làm mới, giữ nguyên: chỗ đang gõ, vị trí cuộn, nhật ký đang mở, tên đang sửa, video đang chọn. Khung "Thêm video" không vẽ lại khi đang gõ.
- Tiến độ:
  - chỉ ghi % khi biết tổng dung lượng; 100 % chỉ khi file đã vào input;
  - lúc thăm dò, kiểm tra, chuyển vào input hoặc chưa biết tổng: thanh chạy qua lại (tắt khi máy đặt giảm chuyển động);
  - lượt không có tiến độ (chờ, cần chọn, đã hủy) không hiện thanh rỗng.
- Lô bị từ chối: hiện lỗi từng dòng, giữ nguyên link và ô xác nhận để sửa. Thêm được: xóa ô link, bỏ tick xác nhận, cuộn tới lượt mới (danh sách xếp cũ trước, mới sau).
- Xác nhận trước khi: hủy (nút "Hủy lượt tải", vì nút đóng hộp thoại đã là "Hủy"), thử lại hoặc xóa lượt còn phần đã tải, dọn file tạm (ghi số lượt và dung lượng).
- Ghi (POST) qua `BFContracts.request` và adapter (token, 403 lấy phiên mới một lần, mỗi nút một yêu cầu). Sau mỗi thao tác chỉ tải lại danh sách tải, không gọi `/api/status`.
- "Dung lượng": chỉ đọc; "Tính lại" hỏi server tính lại. Liên kết mở danh sách "Video của bạn" → "Hoàn tất" để dùng "Dọn video gốc"/"Lưu trữ" sẵn có. Trên điện thoại ghi "dọn chỉ làm trên PC".
- Kiểm tra:
  - `verify-download.cjs` (Node, không mạng);
  - `download-fake-server.cjs`: server giả trong bộ nhớ, đủ mọi trạng thái, có chế độ `--phone`; Control Center không phục vụ file này;
  - đã xem trên trình duyệt ở 375, 390 và 1440 px, sáng và tối, PC và điện thoại.
- Sửa trong lúc kiểm tra trên trình duyệt:
  - `loadStorage` của live store chép `snap` trước khi chờ, nên câu trả lời đến muộn đưa trạng thái cũ trở lại và mất cờ điện thoại; đã có test.
  - Nhãn đầu trang ("Control Center" / "Qua điện thoại") cập nhật theo chế độ.
  - Nút trên điện thoại cao tối thiểu 40 px.
  - Thanh "Tải đồng thời / Dọn file tạm" thành phần riêng; trước đó cả danh sách đứng yên khi ô chọn có focus.
- Sau review D3 (security-reviewer: 0 HIGH, 1 MEDIUM, 8 LOW; typescript-reviewer: 3 HIGH, 6 MEDIUM, vài LOW), đã sửa:
  - Làm mới tại chỗ (`morph` trong `download-live.js`): không thay `innerHTML` nữa mà giữ nguyên node, chỉ ghi thuộc tính và chữ đã đổi. Gõ tiếng Việt (IME) trong ô đổi tên hay ô link, chọn chữ, focus (cả `<summary>`), nhật ký đang mở và vị trí cuộn trong log không bị làm mới phá. Ô link (`textarea`) không bao giờ bị ghi đè chữ, chỉ xóa có chủ ý sau khi thêm được.
  - Focus sau thao tác: nút biến mất (Dừng sau khi dừng, dòng đã xóa) thì focus về nút đầu tiên còn bật của lượt đó, hoặc bộ lọc đang chọn; đổi số luồng thì về ô chọn; sau hộp xác nhận cũng vậy.
  - "Dọn file tạm" gửi kèm `ids` của các lượt đã liệt kê (backend `cleanup_temp(ids)`).
  - Thông báo lỗi (`role="alert"`) không bị chèn lại mỗi lần làm mới (đọc lại liên tục với trình đọc màn hình).
  - Nhật ký: tải lại khi lượt đổi trạng thái hoặc sau lỗi; chỉ cho dòng đang hiện theo bộ lọc.
  - Adapter: mục "Dung lượng" đổi tên thành `storage_summary` (trùng khóa `storage` của `/api/status`), giữ `storage_error` qua mỗi lần làm mới; mỗi lúc chỉ một lượt hỏi danh sách, không hỏi khi tab bị ẩn; "Dung lượng" lỗi thì chờ bấm "Tính lại"; lỗi vẽ trang không biến một thao tác đã xong thành lỗi.
  - `app.js`: dải "Mất kết nối" / "thao tác với video gốc đang chạy" cập nhật cả trên trang Tải video; Enter trong ô đổi tên là đổi tên.
  - Nhỏ: ô chọn số luồng khóa khi đang gửi, ô link chỉ đọc khi đang gửi lô, nguồn gửi đi là nguồn ô chọn đang hiện, `role="group"` cho bộ lọc, số trong HTML qua `Number()`, lựa chọn video cũ bị bỏ khi lượt rời NEEDS_CHOICE, cuộn tới lượt mới cả trên điện thoại.
  - Chưa làm: kiểm tra `download-live.js` tự động trên trình duyệt (máy không có Playwright); đã kiểm bằng tay trên trình duyệt với `download-fake-server.cjs` (gõ qua nhiều lần làm mới, focus sau Dừng / hộp xác nhận, dọn file tạm khi có lượt dừng giữa chừng, mất và có lại kết nối).

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

### Kết quả D4 (2026-10-05)

Control Center thử: `python -m biliflow --project-root <temp>\vd-d4-root control-center --port 8797 --no-import-existing`. Root tạm có `config\` chép từ bản cài, `tools\deno` và `tools\ffmpeg\bin` chép vào, `input\`, `state\`, `temp\` riêng. Không POST vào Control Center thật; không đụng `input\`, `output\`, `archive\` thật.

- **YouTube, link công khai do người dùng đưa (khoảng 31 phút):** thêm qua trang `#downloads` thật trên cổng 8797 (ô xác nhận quyền do agent tích theo lời người dùng: link đưa để tải thử). Trạng thái QUEUED → DOWNLOADING → COMPLETED, 1 lượt, khoảng 80 giây. Kết quả: 1080p, H.264 + AAC, 1,28 GB, thời lượng 1849 giây. File nằm trong `input\` của root tạm, thư mục `temp\downloads\<id>` đã xóa, log che đường dẫn thành `<BiliFlow>`. Watcher tạo job 1 ở NEEDS_METADATA, không tự quét. H.264 + AAC là loại codec BiliFlow vẫn quét, nên không cần chuyển mã; chưa chạy quét trên file này.
- **Trang phim (link tham khảo của người dùng), chỉ thăm dò:** yt-dlp báo "Unsupported URL" (bộ trích chung không thấy video; trình phát có lẽ chạy bằng JavaScript hoặc iframe). Đây là trường hợp UNSUPPORTED, không phải một mục, nhiều mục hay chỉ quảng cáo. **Không thêm nguồn:** muốn tải phải viết code riêng cho trang đó, nằm ngoài ranh giới tính năng.
- **Deno:** client mặc định của yt-dlp lấy định dạng mà không cần giải thử thách JS. Khi ép client `mweb` (chỉ thăm dò), yt-dlp gọi `tools\deno\deno.exe` để giải bằng `yt-dlp-ejs` 0.8.0 (nguồn: gói Python, không tải script từ mạng). Xóa cả `cache\` của root tạm rồi chạy lại: vẫn giải được, Deno tạo lại cache trong `cache\deno`. Ổ C không có thư mục `deno` hay `yt-dlp` mới.

## 9. Dừng lại và hỏi người dùng khi

- Một trang cần cookie, đăng nhập hoặc giả trình duyệt mới tải được.
- `license-audit` không đạt, hoặc một phụ thuộc kéo theo giấy phép ngoài danh sách.
- Video tải về có codec mà bước quét hay xuất của BiliFlow không giải mã được (ví dụ AV1). Khi đó cân nhắc ép H.264 khi có, hoặc chuyển mã: là quyết định của người dùng.
- Cần đụng `input\`, `output\`, `archive\` thật, hoặc POST vào Control Center thật.
- yt-dlp cần cập nhật mới chạy được.

## 10. Rủi ro đã biết

- Trang nguồn đổi cách chạy (nhất là YouTube): cần cập nhật yt-dlp theo lệnh người dùng.
- yt-dlp theo redirect nội bộ: kiểm tra IP chỉ chặn được ở link đầu. Từ D4b không còn danh sách cho phép, nên một trang lạ có thể chuyển yt-dlp tới địa chỉ trong mạng nội bộ, hay chỉ cho nó một link nhúng ở đó (chỉ yêu cầu đọc; lời báo lỗi đã bỏ link và địa chỉ). Muốn chặn hẳn thì cần một proxy cục bộ kiểm tra từng bước chuyển (`--proxy`), chưa làm. Người dùng chấp nhận khi chọn "Mọi trang, thăm dò"; link chỉ do người dùng dán (PC hoặc điện thoại có mã truy cập).
- Ước tính dung lượng có thể sai: `--max-filesize` và lần kiểm tra chỗ trống trước khi chuyển vào input là lớp chặn sau.
- Tải nối tiếp sau khi dừng không phải trang nào cũng hỗ trợ: UI không hứa nối tiếp.

## 11. Nhật ký

| Ngày | Commit | Việc | Kết quả | Còn lại |
| --- | --- | --- | --- | --- |
| 2026-10-05 | a59c6f2 | Người dùng chốt yêu cầu, ranh giới và cách làm; tạo nhánh và worktree; ghi kế hoạch | Chưa cài, chưa code | D0 ở một phiên local mới |
| 2026-10-05 | 6a6fdfd | D0. Người dùng đồng ý: 8 gói PyPI vào `.venv` chính (yt-dlp 2026.8.19, yt-dlp-ejs 0.8.0, requests 2.34.2, urllib3 2.7.0, charset-normalizer 3.5.0, brotli 1.2.0, websockets 17.0.1, pycryptodomex 3.23.0; bản theo extra `pin` của yt-dlp; tải bằng `tools\uv` với cache ở `cache\uv`; dry-run trước: chỉ thêm, không đổi gói nào); Deno 2.9.7 chép từ bản WinGet có sẵn (SHA-256 `e020f3e2…` trùng `deno-x86_64-pc-windows-msvc.sha256sum` của bản phát hành), không tải. Người dùng chọn "Giữ cache quét" (3.1): `config/download_tools.json`, `download_tools.py` + lệnh `audit`, `requirements.lock.txt`, `.gitignore` cho `config/download_sources.local.json`, `THIRD_PARTY_NOTICES.md` | Máy thật: `python -m yt_dlp --version` → 2026.08.19, `deno --version` → 2.9.7, `deno eval` chạy, chạy từ `E:\…\tools\deno`; yt-dlp `-v` thấy `yt_dlp_ejs-0.8.0`, `JS runtimes: deno-2.9.7`, ffmpeg 9.0.1 của dự án, không có plugin. Cache Deno ghi vào `E:\…\cache\deno`; ổ C không có thư mục yt-dlp/Deno mới (so trước và sau). `download_tools audit --tools-root E:\DungChung\BiliFlow`: `allowed: true`, 0 chặn, `mutagen` và `curl_cffi` không có. `license-audit` (model): 9 cho phép, 0 chặn. Test `test_download_tools` + `test_license_policy` OK | D1 |
| 2026-10-05 | 918bc2c | D1: `download_sources`, `download_store`, `download_probe`, `download_runner`, `download_files`, `download_worker` + `download_upkeep`, `cleanup.DOWNLOAD_CACHE_POLICIES`, yt-dlp giả `tests/fake_yt_dlp.py`. Hai agent review (python, security): 1 HIGH, 6 MEDIUM, 9 LOW; đã sửa theo mục "Bổ sung sau review D1" (4.10); còn để lại: `create_time` không đọc được thì lưu 0 và lần khởi động sau không dừng tiến trình đó (an toàn: không bao giờ dừng nhầm pid) | 155 test D1 OK (yt-dlp giả, không mạng, root tạm). Full suite 1466 test: chỉ 28 lỗi có sẵn của `test_job_pipeline`/`test_job_ocr_option` vì worktree không có `input/*.mp4` (không liên quan). `python -P -m yt_dlp --version` chạy; `json.py` trong thư mục làm việc không che được module chuẩn khi có `-P` | D2 |
| 2026-10-05 | a046a93 | D2: `download_api.py` (route, `public_task`, che đường dẫn cài đặt), `storage_summary.py`, nối vào `control_center.py` (tạo trong `__init__`, chạy trong `serve()`, dừng trong `stop()`; lỗi cơ sở dữ liệu tải → 503, quét vẫn chạy), 4 mẫu route mới trong `phone_access.PHONE_ALLOWED_POSTS`. Review D2 (security-reviewer): 3 MEDIUM, 5 LOW, đã sửa hết (mục 5, "Sau review D2"). Còn để lại (LOW): bước kiểm tra bằng ffmpeg không ngắt được khi tắt (lần khởi động sau ghi INTERRUPTED); hash lại file ở `_recover_publish` trong khóa (đường hiếm) | 262 test tải, route, điện thoại, dashboard OK (2 test hợp đồng V2 chỉ đỏ khi có `contracts.js` của D3 chưa commit). Full suite trên bản D1 + đúng các file D2: 1473 test, chỉ 28 lỗi có sẵn của `test_job_pipeline`/`test_job_ocr_option` (worktree không có `input/*.mp4`). Không POST vào Control Center thật; test route chạy Control Center trên root tạm | D3 |
| 2026-10-05 | cc7812b | D3: trang `#downloads` thật trong Dashboard V2 (`download-core.js`, `download-view.js`, `download-live.js`), 13 endpoint trong `contracts.js`, adapter (`loadDownloads`, `loadStorage`, `watchDownloads`, `downloadAction`), CSS, mục "Dung lượng"; `cleanup-temp` nhận `ids`. Gate `verify-download.cjs`, server giả `download-fake-server.cjs`. Hai review (security, typescript): đã sửa (mục 6, "Sau review D3") | `verify-download.cjs` 20/20; test Dashboard V2, route, Control Center 140 OK; full suite 1478 test, chỉ 28 lỗi có sẵn của `test_job_pipeline`/`test_job_ocr_option`. Trình duyệt (khung trình duyệt của app, server giả, không Control Center thật): 375/390/1440 px, sáng/tối, PC và chế độ điện thoại, không tràn ngang; gõ qua nhiều lần làm mới, focus, hộp xác nhận, dọn file tạm khi có lượt dừng giữa chừng, mất và có lại kết nối | D4 |
| 2026-10-05 | f378619 | D4: tải thật trên Control Center thử (root tạm dưới `temp\`, cổng 8797, `--no-import-existing`), link do người dùng đưa; không đổi code. Không POST vào Control Center thật, không đụng `input\`/`output\`/`archive\` thật | 1 video YouTube công khai (khoảng 31 phút) thêm qua trang `#downloads`: COMPLETED sau 1 lượt, khoảng 80 giây, 1080p H.264 + AAC, 1,28 GB, vào `input\` của root tạm, thư mục tạm đã xóa, watcher tạo job NEEDS_METADATA. Trang phim: chỉ thăm dò, "Unsupported URL", không thêm nguồn. Deno giải thử thách JS (client `mweb`) kể cả sau khi xóa `cache\`; ổ C không có thư mục `deno`/`yt-dlp` mới. Full suite 1478 test, chỉ 28 lỗi có sẵn của `test_job_pipeline`/`test_job_ocr_option` | D5; người dùng quyết có xóa video 1,28 GB trong root tạm hay không |
| 2026-10-05 | 6f4fbee | D4b: người dùng bỏ combobox nguồn ("Mọi trang, thăm dò"): `download_sources.py` → `download_links.py`, bỏ `config/download_sources*.json` và cột `source_id`; luật chọn mục theo bộ đọc yt-dlp (mục 4.1); nhãn "Chưa hỗ trợ"; demo chỉ dùng `*.example`. Hai review (security, code): 3 MEDIUM, các LOW; đã sửa (mục 4.1, "Thêm sau review D4b"). Người dùng cho xóa root tạm của D4 (đã xóa) | Full suite 1488 test, chỉ 28 lỗi có sẵn; `verify-download.cjs` 21/21, `verify.cjs` 30/30. Thăm dò thật (root tạm, đã xóa): trang phim tham khảo → "Trang này chưa được hỗ trợ", YouTube → READY 1080p. Trình duyệt (server giả, trước bản sửa review): không còn ô chọn, lô sai báo theo dòng, "Chưa hỗ trợ", 375 px không tràn | D5 |
| 2026-10-05 | cf38d25 | D5: CHANGELOG, PROJECT_STATUS, SESSION_HANDOFF, `AGENTS.md` (agent không POST `/api/downloads*` vào Control Center thật, không tự bắt đầu tải) | Chỉ tài liệu | Người dùng test trên máy thật; `feat/dashboard-v2` đã tới `55c6e62` (3 commit sau a7d8f18), đưa vào nhánh này hay không do người dùng quyết |
| 2026-10-05 | db4bf6d | Người dùng chọn đưa 3 commit của `feat/dashboard-v2` vào (R4-B4, R4-U1, R4-U2; merge, không xung đột) | `verify.cjs` 32/32, `verify-review.cjs` 31/31, `verify-adapter.cjs` 29/29, `verify-download.cjs` 21/21; `test_dashboard_v2*` 91 OK, `test_download*` 184 OK | Người dùng test trên máy thật. `feat/delete-flow` cũng cần thư mục chính cho bước test của nó: chọn thứ tự với người dùng. Merge sau này với `feat/delete-flow` có một xung đột ở `src/biliflow/control_center.py` |
