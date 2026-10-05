# Kế hoạch tính năng Tải video (tải thật)

Cập nhật: 2026-10-05. Nhánh `feat/video-download` (tách từ `feat/dashboard-v2` a7d8f18), worktree `E:\DungChung\BiliFlow\temp\wt-video-download`.

Trạng thái: **D0 xong** (2026-10-05): công cụ đã cài và khóa phiên bản, kiểm tra giấy phép đạt. Tiếp theo D1. Phiên làm tính năng báo người dùng bằng tiếng Việt sau mỗi bước.

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
2. **Bộ nhớ đệm:** thêm `cache/yt-dlp` và `cache/deno` vào `cleanup.FILE_CACHE_POLICIES` (1 GB, 30 ngày). Kiểm tra Deno vẫn chạy sau khi cache bị dọn.
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
