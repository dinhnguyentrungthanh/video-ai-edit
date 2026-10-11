# Kế hoạch: xuất và quét nhanh hơn, tự nhận diện phần cứng, bộ cài, kích hoạt

- Ngày lập: 2026-10-11 (Asia/Bangkok).
- Bản 3: đã sửa theo hai lượt review đối chiếu với code. Thêm lịch chi tiết (mục 9), prompt Phase 1 (mục 12) và quy trình bàn giao (mục 13). Người dùng đồng ý đưa vào `main` ngày 2026-10-11.
- Base: `main` `d477c70`.
- Nhánh tài liệu: `docs/perf-install-plan`.
- Repo từng công khai, nên tài liệu, code, test và commit **không ghi tên phim, tên file nguồn, link hay tên miền thật**. Tên report và tên bản xuất lấy từ tên file nguồn, nên cũng không được dán vào.
- Khi báo kết quả, mọi session chỉ ghi số job, revision và nhãn:
  - **L**: phim người đóng dài dùng làm tham chiếu;
  - **H**: phim hoạt hình dùng làm tham chiếu;
  - **T**: một tập phim bộ khoảng 45 phút.

Đọc theo thứ tự: `AGENTS.md`, mục 1–3 của file này, rồi mục của session mình.

## 1. Quyết định của người dùng (2026-10-10 / 2026-10-11)

1. **Xuất video và quét nhanh hơn,** nhưng chất lượng và kết quả duyệt giữ nguyên.
2. **Không hardcode cấu hình.** Xuất và quét tự nhận diện cấu hình máy và chọn cách chạy tốt nhất, giống bộ cài.
3. **Chạy được trên máy không có card đồ họa,** chậm hơn là chấp nhận được.
4. **Chưa có máy test riêng.** Đường chạy CPU được kiểm trên PC chính bằng công tắc "Chỉ dùng CPU". Card AMD và đồ họa Intel tích hợp làm khi có máy test.
5. **Bộ cài `.exe`:**
   - hỏi thư mục cài và dựng cấu trúc thư mục giống bản hiện tại;
   - bảo vệ code;
   - **tải model lúc cài**;
   - build trên GitHub Actions;
   - **không mua chữ ký số**: hướng dẫn cài ghi rõ bấm "Thông tin thêm → Vẫn chạy".
6. **Kích hoạt** bằng key gắn với mã máy:
   - **key theo tháng** và **key vĩnh viễn**, cấp theo bộ;
   - miễn phí, không có máy chủ;
   - **không có key thì không làm gì cả.**
7. **Driver NVIDIA đã cập nhật** lên Studio 617.42. FFmpeg trong `tools\ffmpeg` có `h264_nvenc`/`hevc_nvenc` và encode thử được.
8. **Phụ đề và dịch:** tạm dừng.
9. **Mỗi session đo trước rồi mới sửa,** và báo số đo trước/sau trên máy thật.
10. **Repo:** người dùng sẽ chuyển sang Private. **Bắt buộc xong trước khi GitHub Actions tạo bản build đầu tiên** (mục 7, C7).
11. **Credit cloud có hạn** (khoảng 43 USD lúc lập kế hoạch). Việc cần Windows, GPU hay phần cứng thật chạy trên PC. Cloud chỉ làm code thuần và luôn bàn giao để PC tiếp quản được (mục 13).

## 2. Hiện trạng đã kiểm trên code (`main` d477c70)

### 2.1 Xuất video

- **Một worker cho cả quét lẫn xuất.**
  - `scheduler.py:141-145` chỉ giữ một `_process`, `_active`, `_log_handle` và `_executing_job_id`.
  - Lệnh xuất được đưa xuống cuối hàng: `queue_render` dùng `reseq` khi job không ở QUEUED (`scheduler.py:553-563`).
  - `priority` sắp xếp `queued_jobs` trước `queue_seq` (`job_store.py:487`), nhưng giao diện không có chỗ đặt.
- **Lệnh FFmpeg** (`final_renderer.py:592-614`):
  - `libx264 -preset veryfast -crf 20`, có trần `-maxrate`/`-bufsize`. `-crf` lấn át `-b:v`.
  - Âm thanh AAC 384 kb/s.
  - Không dùng `-hwaccel`.
  - Các chuỗi encoder và `"gpu_used": false` trong manifest đang viết cứng (`:689-701`).
- **Số đo cũ:** 6,586× thời gian thực trên một tập 3.559 s (`docs/TEST_RESULTS.md:37`).
  - Con số đó là `elapsed` của riêng lệnh FFmpeg, gồm cả bộ lọc và encode (`final_renderer.py:614-616`). Nó không gồm bước kiểm tra sau xuất.
  - Trên PC (2026-10-11), riêng encode x264 trên một đoạn 2 phút **ít chuyển động** đạt 36×.
  - Khoảng cách giữa hai con số nằm ở bộ lọc, ở nội dung video, hoặc ở cả hai. A0 phải đo trên **cùng một video**.
- **Bộ lọc làm mờ một vùng:** `enable=` chỉ đặt ở `overlay` cuối (`blur_filter.py:97`, `:140`). Nhánh crop → delogo → gblur → geq → alphamerge có thể chạy trên mọi khung của cả đoạn. Chưa đo.
- **Sau khi encode:**
  - giải mã toàn bộ bằng CPU để kiểm tra (`:642-656`);
  - hash SHA-256 bản xuất, video gốc trước và sau khi render (`:533`, `:660-661`);
  - `+faststart`.
- **Bằng chứng để xóa hay lưu trữ video gốc:** `export_identity.manifest_problem` (`export_identity.py:188-233`) chỉ kiểm trạng thái, hash, `full_decode_validation_passed` và các thao tác render. **Encoder không nằm trong bằng chứng này.**

### 2.2 Quét

| Phim | Phạm vi quét | Thời gian (RTX 2060, "Tăng tốc xử lý" bật) |
|---|---|---|
| L | chỉ quảng cáo | 866,6 s |
| L | cả 4 nhóm | 2.507 s (đo trước một số tối ưu sau đó) |
| H | cả 4 nhóm | 950,1 s |

- **Đã làm hoặc đã loại có số đo:** `docs/SCAN_PERFORMANCE.md`, `docs/CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md`. Không đề xuất lại các hướng đã loại.
- **Hướng còn lại:**
  - **L3:** giải mã bằng NVDEC cho stage 18+ và `live_safety`. Số "GPU khoảng 5%, CPU 99,7%" chỉ đo trên stage 18+, trước L1/L2, và gồm cả bộ xử lý ảnh trên CPU (`CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md:782`).
  - **L4:** gộp lượt giải mã của 18+ vào `live_safety`. Tài liệu ước tính 5–10 phút trên L.
  - **Khởi động stage:** `cli.py:8-43` nạp mọi module quét ở đầu file. Chưa đo.
  - **Hash video gốc:** preflight hash tuần tự (khoảng 17 s trên L), sau đó các stage còn hash lại.
- **NVDEC:**
  - chỉ được chứng minh giống hệt pixel cho chuỗi OCR và routing, với h264/yuv420p;
  - `nvdec.py:40-60` chỉ kiểm danh sách cho phép và giải mã thử một khung;
  - chuỗi 18+ (448 px, scaler mặc định, có pad; `scanner.py:436-445`) và `live_safety` (256 px, split/hstack; `live_safety_scanner.py:148-160`) **chưa có phép thử giống hệt nào**.
- **Mọi stage chạy lần lượt trên một worker.** Cờ `uses_gpu` không được đọc.

### 2.3 Cấu hình đang viết cứng

- `job_pipeline.py:129-155`:
  - `FAST_SCAN_OCR_BATCH_SIZE = 8`, `FAST_SCAN_OCR_FRAME_WINDOW = 4`, `FAST_SCAN_LOGO_ROUTING_WORKERS = 3`;
  - các precision `fp16`;
  - `--logo-decode nvdec`.
- `--device cuda` viết cứng ở 11 chỗ (`job_pipeline.py:238-392`).
- Encoder và tham số encode viết cứng trong `final_renderer.py`.
- Đường dẫn FFmpeg viết cứng ở khoảng 39 chỗ trong `src`, trong đó 20 chỗ nằm ở `cli.py` và `stage_cache.py`.
- **Mức giống nhau của từng tham số:**

| Tham số | Kết quả khi đổi giá trị |
|---|---|
| Số worker routing (`RoutingPool`) | Giống hệt |
| OCR batch 8 + cửa sổ 4 | **Không** giống hệt từng byte: điểm lệch tối đa 2,1e-5 (`SCAN_PERFORMANCE.md:129-131`). Chỉ giống ở mức danh sách duyệt |
| Precision fp16 | Chỉ giống ở mức danh sách duyệt |

  Mọi cổng kiểm tra đều mới chạy trên **một** card RTX 2060.

### 2.4 Chạy trên CPU

- **CLI đã có `--device {cuda,cpu}`.** Nhưng:
  - Cờ fp16 báo lỗi trên CPU (`textscan.py:541-542`, `animation_safety_scanner.py:167`, `live_safety_scanner.py:92`).
  - `autocast` viết cứng `"cuda"` (`ocr_precision.py:31`, `animation_safety_scanner.py:272`, `live_safety_scanner.py:230`).
  - **Lỗi:** `scripts/localize_visual_logo_report.py:134-135` luôn ép float16, nên Florence lỗi trên CPU.
  - **Trên CUDA, Florence, Qwen2-VL và text-semantics luôn nạp trọng số fp16** (`vlm_confirmation.py:177`, `text_semantics.py:62`, `:136`, `visual_logo_scanner.py:1789`, `ad_candidate_pipeline.py:295`, `:389`). Vì vậy CPU fp32 khác GPU ở cả `confirm_violence` và `localize_logo`, không chỉ ở ba cờ fp16.
  - **Luật máu anime C1** chỉ chạy khi report là fp16 (`gore_triage.py:114`). Trên CPU, C1 không di chuyển thẻ nào.
- **Ước tính, chưa đo:** Florence trên CPU mất 7–11 s mỗi khung. Quét cả 4 nhóm trên CPU có thể mất nhiều giờ.

### 2.5 Cài đặt và đóng gói

- **Thư mục gốc lấy theo vị trí file:**
  - `parents[2]` ở `cli.py:47`, `recycle_bin.py:60` (`INSTALL_ROOT`), `download_tools.py:180`, `performance_report.py:100`, `scripts/download_models.py:330`;
  - `control_center.py:125` là `DASHBOARD_V2_DIR`;
  - `job_purge.py:36` dùng lại `recycle_bin.INSTALL_ROOT`.
- **`job_purge.allowed_project_root` (`:103-111`) là chốt an toàn.** Nó chặn các hàm xóa, và chặn cả kho phiên tài khoản nguồn (`download_account_vault.py:180`), trình quản lý tài khoản (`download_accounts.py:230`) và bước thử tải (`download_account_probe.py:210`).
- **Chưa có script cài đặt.**
  - Python nằm trong `runtime\python`, cài qua uv. `.venv` lấy từ `requirements.lock.txt`, **không có hash**, dùng `-e .`, và chỉ có bản cu126.
  - `runtime\florence-python` là **thư mục package được thêm vào `sys.path`** của Python trong `.venv` (`localize_visual_logo_report.py:203-208`). Repo **không có cách dựng lại** thư mục này.
  - FFmpeg 9.0.1 là bản build **GPLv3**. Ngoài ra còn Deno.
- **Model không ghim phiên bản lúc tải:**
  - `download_models.py:354` lấy revision mới nhất và ghi lại `manifest.json` cùng `downloaded_at` (`:384-394`);
  - Qwen2-VL-2B không có script tải;
  - EasyOCR không kiểm checksum.
- **`annotations/` bị gitignore,** nhưng bước quét chữ cần `annotations/text_semantics_seed_v1.json` (`cli.py:305`, `textscan.py:785`).
- **Đầu vào của khóa cache quét** (`stage_cache.py:44-96`, `:153-198`; `cache_dependencies.py:24-95`):
  - các file `.py` của stage cùng `__init__.py`, `__main__.py`, `cli.py`, `stage_cache.py`, `cache_dependencies.py` và `scripts/localize_visual_logo_report.py`;
  - `config/processing_profiles.json`, `config/detection_policy.yaml`, `config/text_review_policy.json`, `config/license_policy.json`;
  - `scripts/run.ps1`, `scripts/env.ps1`, `annotations/text_semantics_seed_v1.json`, `pyproject.toml`, `uv.lock`;
  - **mọi file trong `models\`** (manifest và config được hash);
  - kích thước và mtime của `ffmpeg.exe`/`ffprobe.exe`;
  - toàn bộ dòng lệnh của stage.
  
  Nếu không còn file `.py` (bản đã biên dịch), phần code **bị bỏ khỏi khóa mà không báo gì**. Khi đó lên bản mới vẫn dùng lại kết quả cũ.
- **Tài khoản nguồn phim:** phiên được mã hóa bằng DPAPI theo tài khoản Windows, nằm trong `state\source-accounts\<SID>\`, và kho phiên tự tạo ACL riêng. Phiên **không chép sang máy khác được**.
- **Không có file LICENSE.** `pycryptodomex 3.23.0` có sẵn trong lock (đi kèm yt-dlp) và có Ed25519.

## 3. Nguyên tắc chung

1. **`AGENTS.md` là luật cao nhất.** Không bao giờ gọi các route bị cấm trên Control Center thật. Các route PC-only mới của kế hoạch này (Đo lại, Chỉ dùng CPU, kích hoạt) cũng được thêm vào danh sách "agent không bao giờ gọi" (C0).
2. **Repo:** không ghi tên phim, tên file nguồn, link hay tên miền thật. Báo kết quả bằng số job, revision và nhãn L/H/T.
3. **Giữ nguyên chất lượng:**
   - Không đổi model, mật độ quét hay ngưỡng.
   - Cách chạy mới chỉ bật sau khi qua cổng:
     - **giống hệt từng byte** (report, JPEG, danh sách duyệt) cho thay đổi tuyên bố là "chính xác";
     - hoặc **danh sách duyệt giống hệt trên bộ Golden** cho thay đổi về số học (fp16, CPU fp32, OpenVINO).
   - Không dùng nén int8.
   - Đổi encoder hay cách kiểm tra bản xuất phải được **người dùng duyệt**.
4. **Không hardcode cấu hình:**
   - Mọi lựa chọn tốc độ lấy từ **hồ sơ phần cứng** (mục 4) và chỉ chọn trong **bảng cấu hình đã qua cổng**.
   - Lựa chọn được **đóng băng theo từng job** lúc xếp hàng (`configure_and_queue`), giống cách lưu `fast_scan:{id}`. "Đo lại" giữa chừng không trộn thiết bị trong một revision.
   - `device` và `precision` **luôn nằm trên dòng lệnh**, nên luôn có trong khóa cache. Một stage **không bao giờ tự quay về CPU** khi CUDA lỗi.
   - Tham số đã chứng minh cho kết quả giống hệt (số worker, cách giải mã khi đã qua cổng pixel) được **bỏ khỏi khóa cache** trong đợt B1.
   - `choose()` chỉ đọc file. Tiến trình Control Center không nạp torch và không gọi `nvidia-smi` trong các vòng lặp hỏi trạng thái.
5. **Khóa cache:** mọi sửa đổi trên các đầu vào chung ở mục 2.5 (file khởi động, `run.ps1`, `env.ps1`, `config/*`, `pyproject.toml`, `uv.lock`, `__init__.py`, đường dẫn và file trong `models\`, `ffmpeg.exe`) đều làm toàn bộ cache quét bị tính lại. Vì vậy:
   - mọi sửa đổi đó **gom vào một đợt duy nhất, B1**, và người dùng duyệt trước;
   - **không tải lại hay sửa gì trong `models\` trên PC.** C1 chỉ đọc;
   - sửa module quét (E3, B2–B4) làm cache của các stage đó bị tính lại. Phải báo trước.
6. **Đo trước.** Đo trên bản sao hoặc trong root tạm dưới `E:\DungChung\BiliFlow\temp`, hoặc trên một **Control Center thử** (root tạm, cổng riêng). Không ghi vào `input\`, `output\`, `reports\` hay `archive\` thật.
7. **Chỉ có một GPU.** Các lần đo xếp lịch lần lượt, tránh lúc Control Center thật đang chạy job. Mọi phép đo dùng GPU lấy khóa `Local\BiliFlowGpuInference`; phép đo xuất lấy thêm `state/final-render.lock`.
8. **Chuẩn bị worktree trên PC:**
   - `git worktree add temp\wt-<tên> -b <nhánh> main`.
   - Chạy test bằng `.venv` của thư mục cài, với `PYTHONPATH` trỏ vào `src` của worktree.
   - Full suite cần một clip tổng hợp 1 s trong `input\` của worktree; xóa sau khi xong.
   - `performance_report` phải có `--root E:\DungChung\BiliFlow`.
   - Đo pipeline bằng harness với root tạm, như các benchmark trước.
9. **Không push hay merge** nếu người dùng chưa bảo. **Chỉ người dùng khởi động lại Control Center**, và chỉ khi không có job.
10. **Gộp nhánh theo các điểm gộp đã định (mục 9).** Mỗi session ghi `CHANGELOG.md`, `docs/PROJECT_STATUS.md`, `docs/SESSION_HANDOFF.md` trong nhánh của mình. Xung đột được giải lúc gộp.

## 4. Session E: hồ sơ phần cứng và đường chạy CPU

- **Ở đâu:** PC (mục 9.3), vì phải nhận diện phần cứng thật. Phép đo E4 chạy trên một Control Center thử.
- **Nhánh / worktree:** `feat/hardware-profile` / `temp\wt-hardware-profile`.
- **Thời gian (ước tính):** E0–E4 khoảng 2 tuần; E5 thêm 2–4 tuần.

### E0. Hợp đồng giao diện (người dùng duyệt)

- **Schema `state\hardware-profile.json`:**
  - `profile_version`, dấu vân tay phần cứng và driver, các phát hiện, lựa chọn kèm lý do và lần đo;
  - kiểm tra hợp lệ mỗi khi đọc.
- **API:**
  - `hardware_profile.choose(task)` chỉ đọc file;
  - `freeze_for_job(job_id)` lưu lựa chọn theo job, dạng setting `hw:{id}`.
- **Bảng cấu hình đã qua cổng:**
  - mỗi dòng là một tổ hợp đầy đủ, khóa theo kiến trúc GPU, phiên bản torch và khoảng driver;
  - OCR batch chỉ được là 1 hoặc 8 (`job_pipeline.py:164`).

### E1. Nhận diện (chạy trong tiến trình con)

- **Phát hiện:**
  - card, VRAM, driver (`nvidia-smi`);
  - CUDA dùng được với torch hay không;
  - FFmpeg: `-hwaccels`, các encoder, và encode/decode thử thật;
  - CPU (số nhân thật/luồng, tập lệnh), RAM, đĩa trống.
- **Lỗi khi nhận diện:**
  - Máy **từng có CUDA** mà lần này lỗi: giữ hồ sơ tốt gần nhất, hoặc từ chối xếp job mới kèm cảnh báo. **Không** âm thầm chuyển sang CPU.
  - Máy **chưa từng có CUDA:** dùng CPU fp32 và x264.
- Mọi lệnh gọi riêng của Windows nằm sau một lớp giao diện để test bằng dữ liệu giả.

### E2. Chọn cấu hình

- **Đo nhanh** chạy bằng `python -m biliflow.hardware_profile`, gói trong một file `.ps1` có khóa riêng, giống các file `benchmark-*.ps1`. Nó **không** thêm lệnh vào `run.ps1`.
  - Đo khi chưa khởi động scheduler, hoặc khi scheduler đang rảnh và tạm dừng.
  - Lấy khóa GPU và khóa render.
  - Đo trên clip mẫu đi kèm, không phải video của người dùng.
- **Cách giải mã và số worker:** **không** chọn chỉ dựa trên một lần đo nhanh đứng riêng. NVDEC từng chậm hơn khi CPU rảnh (`SCAN_PERFORMANCE.md:93-95`). Dùng bảng số đo thật và có ngưỡng trễ (hysteresis) để không đổi qua đổi lại.
- **Trang Cài đặt:** hiện hồ sơ; có "Đo lại" và "Chỉ dùng CPU", **chỉ trên PC** (127.0.0.1 + token).
- E0–E2 **không sửa `scheduler.py`**. Việc đóng băng lựa chọn theo job làm sau điểm gộp G1 (mục 9).

### E3. Sửa đường chạy CPU

- Bỏ cờ fp16 khi chạy CPU; `autocast` theo thiết bị thật; sửa lỗi ép float16 của Florence; không lấy khóa GPU khi chạy CPU.
- **Luật C1 trên CPU, người dùng chọn trước khi làm E4:**
  - đo lại ngưỡng cho fp32;
  - hoặc giữ nguyên và ghi chú "C1 chưa hiệu chỉnh cho CPU" trên thẻ. Việc này chạm `review_workflow.py`, nên làm ở đợt có quyền sửa file đó.

### E4. Cổng CPU fp32 (Control Center thử trên PC)

- So danh sách duyệt của CPU fp32 với **bản GPU chạy fp32 thường**, không so với fp16, trên **các đoạn Golden** của L, H và một T.
- Đo tốc độ và RAM đỉnh.
- **Giới hạn:** chạy bản torch CUDA trên CPU của PC không chứng nhận được bản torch CPU, cũng không chứng nhận các loại CPU khác. Bản torch CPU sẽ được kiểm lại khi có bộ cài (C5).

### E5. Đợt sau: OpenVINO trên CPU

- Chuyển từng model và qua cổng Golden. Florence-2 chưa xác nhận được.
- File model OpenVINO **không đặt vào `models\`** cho tới khi B1 đã giới hạn khóa cache theo đúng model mà stage dùng.

## 5. Session A: xuất video nhanh hơn

- **Ở đâu:** PC.
- **Nhánh / worktree:** `perf/export-speed` / `temp\wt-export-speed`.
- **Thời gian (ước tính):** khoảng 1–1,5 tuần.

### A0. Đo từng bước

- `performance_report --root E:\DungChung\BiliFlow --job-id <N>` (chỉ đọc).
- Trên **cùng một** T và một đoạn 1080p, làm trên bản sao trong root tạm, đo:
  - bộ lọc + encode (lệnh hiện tại);
  - riêng encode (không bộ lọc);
  - bộ lọc với `-f null`;
  - kiểm tra sau xuất; hash; `+faststart`.
- Kiểm bộ lọc nào nhận `enable` bằng `ffmpeg -filters` (cột T).

### A1a. Ưu tiên bản xuất, chưa chạy song song (ít rủi ro, làm trước)

- `_select` lấy lệnh xuất đang chờ trước, ở ranh giới stage kế tiếp, dùng `priority` có sẵn.
- Một worker như cũ. Mọi chốt chặn giữ nguyên.

### A1b. Chạy song song nhiều làn (sau A3, chỉ khi hồ sơ cho phép)

**Phải thay toàn bộ trạng thái đang giả định chỉ có một worker:**
- `_process`/`_active`/`_log_handle` thành dict theo làn; `_executing_job_ids` thành set;
- `pause_now`/`cancel`/`_terminate_active` chỉ dừng đúng làn;
- nhánh xử lý lỗi (`:824`) không đóng log của làn khác;
- `is_busy` (đang được dùng cho chốt xóa và lưu trữ ở `source_cleanup.py:493`, `job_delete.py:154`, `control_center.py:1176,1464,1494,1578`) phải đúng với mọi làn;
- `shutdown` chờ và dừng mọi làn; `_select`/`queue_order` có bộ lọc theo làn; `_wake` dùng chung;
- `status()["active"]` thành danh sách, giữ trường cũ để tương thích (`control_center.py:936`, `dashboard_v2/adapter.js:317`, `app.js:91`);
- định nghĩa `scheduler_paused` dừng những gì.

**Test:** hủy hoặc tạm dừng làn A khi làn B đang chạy; `is_busy` trong khoảng ghi sổ.

**Điều kiện:** chỉ bật khi đã đo VRAM của NVENC lúc Qwen2-VL đang chạy. Máy chỉ có CPU thì không chạy song song.

### A2. Bộ lọc làm mờ

- Đặt `enable` cho các bộ lọc xử lý từng khung (gblur, geq, delogo) trong nhánh làm mờ.
- **Không** tách đoạn tại biên làm mờ: `trim` loại trừ điểm cuối còn `between` thì gồm cả điểm cuối, và `concat` đặt lại thời gian khung, nên kết quả không giống hệt.
- **Cổng:** `framemd5` của `[vout]` **và** SHA-256 của file mp4 x264 giống hệt, trên clip tổng hợp và trên bản sao video thật.

### A3. Encoder theo hồ sơ

- **Lựa chọn encoder** lưu trong cài đặt render hoặc edit plan, **không thêm cờ vào `cli.py`**.
- **NVENC:** `-rc vbr -cq <N>` với `-maxrate`/`-bufsize`. Nếu bản xuất vượt kiểm tra dung lượng (`final_renderer.py:619`) thì **tự encode lại bằng x264**.
- **Manifest ghi thêm** `encoding.encoder` và `validation_method`. Giữ nguyên `source_hash_before/after` và `sha256_after_render`.
- Encoder không nằm trong bằng chứng xóa/lưu trữ (mục 2.1), nên **NVENC chỉ chạy trong root tạm cho tới khi người dùng duyệt**. Khi duyệt, ghi rõ vào `AGENTS.md` rằng bản xuất NVENC được dùng làm bằng chứng cho "Xóa video gốc" và "Lưu trữ".
- **Cổng:** SSIM (và VMAF nếu có) cùng dung lượng file trên 3 tập, so với x264. x264 vẫn là mặc định cho tới khi người dùng duyệt.

### A4. Kiểm tra sau xuất (chỉ khi A0 cho thấy đáng làm)

- Đổi cách kiểm tra (ví dụ giải mã bằng NVDEC) là **đổi nghĩa của "bản xuất qua kiểm tra manifest"** trong `AGENTS.md`. Cần người dùng duyệt và sửa `AGENTS.md`.
- Giải mã bằng CPU vẫn là chuẩn.

### Kỳ vọng (ước tính, A0 xác nhận)

- Một T từ khoảng 6–8 phút còn khoảng 1,5–3 phút.
- Không còn chờ sau việc quét.

## 6. Session B: quét nhanh hơn

- **Ở đâu:** PC. Bắt đầu sau điểm gộp G1.
- **Nhánh / worktree:** `perf/scan-decode` / `temp\wt-scan-speed`.
- **Thời gian (ước tính):** khoảng 2 tuần.

### B0. Đo nền mới

- Phim người đóng quét cả 4 nhóm (đoạn của L) và H.
- Thời gian khởi động từng stage (`python -X importtime`, PowerShell).

### B1. Đợt sửa khóa cache duy nhất (người dùng duyệt danh sách và thời điểm)

Danh sách chốt sau B0 và sau khi thiết kế xong B3. Dự kiến gồm:
1. Nạp thư viện khi cần trong `cli.py`.
2. Thay `parents[2]` ở `cli.py:47` bằng `install_root()` (lấy từ C2).
3. Phần chạm khóa cache của C3: `stage_cache.py`, `cache_dependencies.py`.
4. Từ D: đường dẫn FFmpeg trong `cli.py`/`stage_cache.py`, số phiên bản trong `__init__.py`, `pyproject.toml`.
5. Cờ CLI mới mà B2/B3 cần.
6. Thay đổi `STAGE_MODULES` của B3.
7. Bỏ các tham số đã chứng minh cho kết quả giống hệt khỏi khóa cache.
8. Đề xuất: khóa cache chỉ tính **model mà stage dùng**, không tính cả thư mục `models\`.

### B2. NVDEC cho stage 18+ và `live_safety`

- Trước hết viết **phép thử giống hệt pixel cho từng video** trên đúng chuỗi xử lý của stage: 448 px có pad, và 256 px split/hstack.
- **Cổng:** report và JPEG giống hệt từng byte (`scripts/benchmark_stage_equivalence.py`).

### B3. Gộp lượt giải mã 18+ vào `live_safety`

- Chỉ khi job chọn cả 18+ lẫn máu/bạo lực.
- Đổi tên stage thì **job đang trong hàng cần đường cũ**, giống `scheduler.py:568-570`. Nếu không, `_runnable` trả `None` và job bị kẹt.
- Cổng như B2.

### B4. Hash một lần

Preflight hash xong thì truyền kết quả cho các stage sau.

### B5. Tùy chọn

Cho stage CPU của video sau chạy chồng với stage GPU của video trước, theo hồ sơ phần cứng. Chỉ làm khi đo có lợi và kết quả không đổi.

### Kỳ vọng (ước tính)

- Chỉ quét quảng cáo: nhanh hơn khoảng 10%.
- Phim người đóng quét cả 4 nhóm: nhanh hơn khoảng 20–30%.

## 7. Session C: bộ cài, biên dịch, kích hoạt

- **Ở đâu:** cloud viết; GitHub Actions build; kiểm thật trên PC.
- **Nhánh:** `feat/installer`.
- **Thời gian (ước tính):** khoảng 4 tuần.

### C0. Đề xuất sửa `AGENTS.md` và tài liệu (người dùng duyệt)

1. **"Giữ runtime trong `E:\DungChung\BiliFlow`"** đổi thành "trong thư mục cài". Áp dụng cho:
   - các câu về root tạm của test (`<install>\temp`);
   - câu "không chuyển dữ liệu về ổ C";
   - giả định trong mục Tailscale rằng mọi tài khoản cục bộ đều sửa được cây thư mục BiliFlow.
2. **Công cụ cần người dùng duyệt trước khi tải:** Nuitka, Inno Setup, công cụ làm rối JS, uv, torch bản CPU.
3. **Thêm quy tắc mới:**
   - agent không bao giờ tạo, đọc hay in khóa bí mật cấp key; không chạy công cụ cấp key; không cấp key;
   - sổ khách hàng nằm ngoài Git và ngoài cây thư mục BiliFlow;
   - các route kích hoạt, Đo lại, Chỉ dùng CPU thuộc danh sách "agent không bao giờ gọi" trên Control Center thật.
4. **`LICENSE`** (độc quyền) và `THIRD_PARTY_NOTICES.md`:
   - FFmpeg GPLv3 và nguồn tương ứng;
   - Playwright, torch/CUDA, Nuitka, pycryptodomex, Inno Setup;
   - file NOTICE của các model Apache-2.0.
5. **Repo phải Private** trước bản build đầu tiên.

### C1. Khóa phiên bản (chỉ đọc trên PC)

- `config/models.lock.json`: revision và SHA-256 từng file của 9 model. Lấy bằng một **script chỉ đọc** chạy trên PC. **Không tải lại model trên PC.**
- Cập nhật `download_models.py`: tải theo lock, tải tiếp khi rớt mạng, kiểm SHA-256, thêm Qwen2-VL-2B, thêm checksum cho EasyOCR. Script này chỉ dùng cho bản cài mới.
- **Lock có hash** cho `.venv` bản cu126 và bản CPU; **lock riêng cho `florence-python`**, kèm cách dựng lại.
- `annotations/text_semantics_seed_v1.json`: quyết định có đóng gói không. Kiểm trước xem có tên thật nào trong đó không.

### C2. `install_root()`

- Tìm **file đánh dấu** ở gốc cây thư mục cài. Không dùng biến môi trường, không dùng tham số.
- Giữ nguyên nghĩa của `allowed_project_root`, vì nó chặn cả hàm xóa, kho phiên, trình quản lý tài khoản và bước thử tải.
- **Chưa sửa `cli.py`.** Thay đổi ở đó đi theo đợt B1.
- Kiểm bằng `cache_dependencies.stage_source_paths` (trước và sau khi sửa) rằng **không stage nào đổi khóa cache**.

### C3. Khóa cache cho bản build (đi theo B1, phải xong trước C4)

- Bản build: khóa theo mã build và hash từng module, tính sẵn lúc build.
- **Từ chối an toàn:** khi không có cả `.py` lẫn manifest build thì `cacheable()` trả False.
- Bản chạy từ mã nguồn giữ cách cũ.

### C-spike. Thử Nuitka sớm (Phase 1, trên PC, không gộp vào `main`)

- **Mục đích:** biết sớm những gì vướng khi biên dịch.
- **Cần người dùng duyệt** trước khi tải Nuitka.
- **Cách làm:**
  - biên dịch gói `biliflow` sang một **thư mục tạm** dưới `temp\`;
  - chạy `python -m biliflow --help`, `python -m biliflow.performance_report --help`, một stage quét ngắn và `render-final` trên clip tổng hợp trong root tạm;
  - so kết quả với bản chạy từ mã nguồn.
- **Không sửa** code của repo. Mọi vướng mắc ghi vào file bàn giao (mục 13) để C4 dùng.

### C4. Biên dịch Nuitka (chỉ chế độ module)

- Biên dịch gói `biliflow` thành `.pyd`. **Giữ `__main__.py` dạng mã nguồn** để `python -m biliflow` vẫn chạy (`run.ps1:62`, `routing_prewarm.py:36`). **Làm thử nghiệm điểm này trước tiên.**
- `python.exe` của runtime vẫn giữ, nên những thứ sau vẫn đúng:
  - `sys.executable -m yt_dlp` (`download_runner.py:288`);
  - ProcessPool `spawn` (`visual_logo_scanner.py:1016`);
  - quy tắc tường lửa gắn với `python.exe` (`tailscale-setup.ps1:46-60`).
  
  File `.exe` chỉ là lớp khởi động mỏng.
- **Florence** dùng `trust_remote_code`: ghim và hash các file `.py` đi kèm model.
- **JS:** làm rối mã phải giữ CSP `script-src 'self'` và danh sách file cố định (`control_center.py:125-143`).
- **Test** chạy trên cây mã nguồn, vì một số test đọc file nguồn.
- **Cổng:** chạy bản biên dịch và bản mã nguồn trên một clip ngắn, kết quả phải giống hệt.

### C5. Trình cài đặt (Inno Setup)

1. **Thư mục cài:**
   - **Từ chối** Program Files.
   - Phân quyền: file chương trình chỉ đọc/chạy cho Users; thư mục dữ liệu cho phép người dùng sửa.
   - Test việc kho phiên tự tạo thư mục bằng một **tài khoản thường**.
2. **Tạo cấu trúc thư mục**, trừ `state\source-accounts`, vì kho phiên tự tạo thư mục đó với ACL riêng.
3. **Chọn torch** theo phiên bản driver từ `nvidia-smi`. Cài xong thì kiểm `torch.cuda`; không dùng được thì chuyển sang bản CPU.
4. **Kiểm VC++ runtime.**
5. **Tải torch và model:** hiện tiến độ, tải tiếp khi rớt mạng, kiểm SHA-256.
6. **FFmpeg:**
   - từ chối bản build `--enable-nonfree`;
   - với GPLv3: kèm mã nguồn tương ứng, hoặc tải FFmpeg lúc cài với SHA ghim sẵn.
7. **Cài Playwright,** dùng Edge có sẵn trong Windows; cài Deno.
8. **Tạo shortcut.**
9. **Gỡ cài:**
   - **không bao giờ xóa** `input`, `archive`, `output`, `state`, `reports`, `annotations`;
   - chỉ xóa chương trình, runtime, model, cache, temp;
   - **không có tùy chọn "xóa dữ liệu".**
10. **Lên bản mới:**
    - chỉ dừng Control Center khi không có job;
    - các dòng lưu trữ đang dở được xử lý ở lần khởi động sau;
    - schema database chỉ đi lên (`job_store.py:303`), nên **không hạ bản được**;
    - đổi model hoặc torch thì làm qua lock.

### C6. Kích hoạt

**Chia làm hai phần:**
- **C6-lõi (Phase 1):** chỉ là code mới, có test, chưa nối vào Control Center:
  - module mới `activation.py`: mã máy, định dạng key, kiểm chữ ký, kiểm hạn, chống chỉnh lùi đồng hồ;
  - công cụ cấp key dạng script, nằm ngoài gói cài;
  - cặp khóa test chỉ dành cho test.
- **C6-nối (Đợt 3):** server chỉ để kích hoạt, chặn CLI, trang kích hoạt.

- **Mức bảo vệ:** chỉ để **răn đe**, không chống được người có chuyên môn. `.venv` là CPython thường, sửa được.
  - Kiểm chữ ký **bên trong module đã biên dịch**, và kiểm hash các file mã hóa so với manifest build.
  - **Khóa công khai của bản phát hành là hằng số lúc biên dịch.** Bản phát hành không bao giờ chọn được khóa test.
- **Mã máy:** ba hash có muối:
  - MachineGuid;
  - UUID bo mạch, đọc bằng `GetSystemFirmwareTable` (không dùng `wmic`), và bỏ qua UUID giả;
  - serial ổ hệ thống.
  
  Khớp 2/3 là được.
  - VM nhân bản sẽ khớp cả 3. Cài lại Windows chỉ còn khớp 1/3: cấp lại theo chính sách cấp lại trong công cụ.
- **Key:**
  - gồm mã máy, mã bộ, loại (`month:N` hoặc `perpetual`), ngày cấp, ngày hết hạn, mã key;
  - **không chứa tên khách**, tên khách chỉ nằm trong sổ;
  - ký Ed25519, mã hóa base32, khoảng 176 ký tự;
  - lưu ở `state\activation\`.
- **Không có key hoặc key không hợp lệ:**
  - Chạy **server chỉ để kích hoạt**, trước khi dựng `ControlCenter()`. Như vậy không migrate database, không reconcile, không nhập report, không chạy watcher, trình tải hay tài khoản nguồn, và **không đụng tới dữ liệu**.
  - Chỉ mở `/healthz`, GET `/api/phone-mode`, POST `/api/shutdown` (để `Start/Stop-BiliFlow` vẫn chạy) và route kích hoạt (chỉ PC: 127.0.0.1 + token).
  - Không bật chế độ điện thoại hay Tailscale.
  - CLI của bản build cũng kiểm key.
- **Key tháng hết hạn trong lúc chạy:** tạm dừng scheduler ở ranh giới stage kế tiếp. **Không bao giờ làm hỏng một stage.**
- **Chống chỉnh lùi đồng hồ:**
  - lưu thời điểm muộn nhất từng thấy, có chữ ký cục bộ; cho phép lệch ±48 h;
  - mất file này thì key tháng phải kiểm lại;
  - giờ trên internet là tùy chọn, lấy qua client mạng đã kiểm của trình tải.
- **Công cụ cấp key** chạy trên PC của người dùng. **Khóa bí mật chỉ nằm ở đó và phải sao lưu.** Sổ khách hàng nằm ngoài Git.
- **Bản chạy từ mã nguồn** (thư mục dev trên PC) cũng cần key: người dùng tự cấp cho mình một key vĩnh viễn. Test dùng cặp khóa test, và chỉ bản test mới chọn được khóa đó.
- Cập nhật vai trò của pycryptodomex trong `config/download_tools.json:71-76`.

### C7. GitHub Actions (sau khi repo đã Private)

- Đẩy tag thì tự build: biên dịch, Inno Setup, rồi lưu bộ cài thành **artifact** của workflow. Người dùng tự tạo Release, vì `AGENTS.md:18` cấm tự phát hành.
- **Cài thử tự động** trên Windows sạch: cài, kích hoạt bằng key test, chạy clip ngắn với model nhỏ.
- **Máy build không kiểm được:** CUDA, NVENC/NVDEC, Edge có giao diện, Thùng rác thật, ACL với tài khoản thường (máy build chạy quyền admin), SmartScreen. Những thứ này kiểm trên PC.
- **Bảo mật:** token phạm vi hẹp, bảo vệ tag, ghim action theo SHA. Khóa bí mật cấp key không bao giờ lên CI.

### C8. Hướng dẫn cài

- SmartScreen: bấm "Thông tin thêm → Vẫn chạy". Diệt virus báo nhầm: gửi file cho Microsoft kiểm tra.
- Phiên tài khoản nguồn phải đăng nhập lại trên máy mới.

## 8. Session D: dọn code (làm cuối)

- **Ở đâu:** cloud.
- **Nhánh:** `refactor/shared-helpers`.
- **Thời gian (ước tính):** khoảng 1 tuần.

**Việc:**
1. Module dùng chung cho:
   - ghi JSON an toàn: file tạm riêng + `fsync`, theo `source_archive_files.py`;
   - SHA-256;
   - đọc JSON;
   - lấy giờ hiện tại.
2. `tool_path()` cho FFmpeg.
3. Thống nhất định dạng giờ. **Người dùng chọn**, vì chữ trên thẻ đổi.
4. `RoutingPool` dùng `spawn`. Harness Node gọi `process.exit`.

**Phần chạm khóa cache** (đường dẫn FFmpeg trong `cli.py`/`stage_cache.py`, số phiên bản trong `__init__.py`, `pyproject.toml`) **đi theo đợt B1**.

## 9. Lịch, song song, điểm gộp, ai sửa file nào

### 9.1 Đường găng

Chuỗi dài nhất quyết định ngày ra bản cài đầu tiên:

> C1/C2 → **B1** (đợt sửa khóa cache duy nhất) → C3 → C4 biên dịch → C5 bộ cài → C7 build tự động → người dùng kiểm

Để rút ngắn:
1. **B0 (chỉ đo) chạy ngay ở Phase 1**, để có thể chốt B1 vào tuần 2.
2. **C-spike (thử Nuitka) chạy ngay ở Phase 1**, để lộ vướng mắc sớm.
3. **C6-lõi viết ngay ở Phase 1**, vì đó là code mới, không đụng file của session khác.
4. Mọi việc không nằm trên đường găng (A, E, B2–B4) chạy song song với nó.

### 9.2 Lịch theo tuần (ước tính)

| Tuần | PC | Cloud (khi còn credit) | Người dùng |
|---|---|---|---|
| **1 (Phase 1)** | **A:** A0 → A1a → A2 · **E:** E0 → E1–E2 · **B0:** chỉ đo, chạy lúc rảnh hoặc ban đêm · **C-spike** | **C:** C0 đề xuất · C1 (script) · C2 · C6-lõi | Repo Private · duyệt E0, C0 · chạy script chỉ đọc của C1 · duyệt tải Nuitka |
| | **Điểm gộp G1:** E0–E2, A1a, A2, C1, C2, C6-lõi vào `main` | | Duyệt G1 |
| **2** | **A3:** NVENC trong thư mục thử + SSIM · **B:** thiết kế B3 → **B1** · **E3** + đóng băng hồ sơ theo job | **C:** C4 dây chuyền biên dịch, bản nháp C5 | Chọn cách xử lý C1 trên CPU · duyệt danh sách B1 · duyệt NVENC · khởi động lại Control Center khi rảnh |
| | **Điểm gộp G2:** B1, E3, A3 (nếu duyệt) | | Duyệt G2 |
| **3** | **B:** B2–B4 · **E4:** cổng CPU (Control Center thử, ban đêm) · A1b nếu đủ điều kiện | **C:** C6-nối, C5, C7, C8 | |
| **4** | Người dùng cài thử bản build (thư mục mới, thêm một tài khoản Windows thường) | Sửa lỗi từ lần cài thử · **D** | Kiểm bộ cài |
| | **Điểm gộp G3** | | |
| **5–6** | Dự phòng: sửa lỗi, đo lại, bản cài đầu tiên chính thức | | |

**Khi nào thấy kết quả:**
- **Cuối tuần 1:** bản xuất không còn chờ sau việc quét; bộ lọc làm mờ nhanh hơn (nếu A0 xác nhận đó là chỗ nghẽn).
- **Tuần 2:** có NVENC sau khi duyệt; máy biết tự chọn cấu hình.
- **Tuần 3:** quét nhanh hơn.
- **Tuần 4–6:** bản cài có kích hoạt.

### 9.3 Phase 1: 5 session, cái nào chạy ở đâu

| Session | Chạy ở | Nhánh | Worktree trên PC | Bắt đầu | Dùng GPU |
|---|---|---|---|---|---|
| **A** xuất video | PC | `perf/export-speed` | `temp\wt-export-speed` | Ngày 1 | A0 (khung giờ GPU 1) |
| **E** hồ sơ phần cứng | **PC** (nhận diện phần cứng thật, không tốn credit cloud) | `feat/hardware-profile` | `temp\wt-hardware-profile` | Ngày 1 | Đo nhanh E2 (khung giờ GPU 3) |
| **C** bộ cài, phần code thuần | **Cloud**; PC tiếp quản khi hết credit (mục 13) | `feat/installer` | `temp\wt-installer` (khi tiếp quản) | Ngày 1 | Không |
| **B0** đo nền quét | PC | `perf/scan-baseline`, chỉ có file bàn giao, không sửa code | `temp\wt-scan-baseline` | Ngày 2–3 | Có (khung giờ GPU 2, ban đêm) |
| **C-spike** thử Nuitka | PC | `spike/nuitka`, chỉ có file bàn giao; script build để trong `temp\` | `temp\wt-nuitka-spike` | Ngày 2–3, sau khi duyệt tải Nuitka | Chỉ khi chạy clip thử |

- **Vì sao E chạy trên PC:** E phải nhận diện card, driver, NVENC/NVDEC thật. Cloud không có những thứ này, và chạy trên PC còn tiết kiệm credit cloud.
- **Vì sao C chạy trên cloud:** C0, C2 và C6-lõi là code thuần với test giả. Đây là phần duy nhất của Phase 1 hợp với cloud. Nếu hết credit, PC tiếp quản theo mục 13.

### 9.4 Khung giờ GPU trên PC (không chạy chồng)

1. **Khung 1, A0:** đo xuất. Chủ yếu dùng CPU, NVENC chỉ chạy trong thư mục thử.
2. **Khung 2, B0:** đo quét. Dùng GPU nặng; chạy ban đêm hoặc khi Control Center thật không có job.
3. **Khung 3, E2:** đo nhanh hồ sơ phần cứng, vài phút.
4. **C-spike:** chỉ dùng GPU khi chạy clip thử. Xếp vào lúc không có khung nào ở trên.

Mỗi phép đo lấy khóa GPU hoặc khóa render (mục 3.7). Session nào thấy khóa đang bị giữ thì **chờ, không chen ngang**.

### 9.5 Điểm gộp

- **Ai gộp:** một session **trên PC** ("Điều phối gộp", prompt ở mục 12). Session này gộp lần lượt các nhánh vào `main`, chạy cả bộ test, rồi push `main` khi người dùng đồng ý.
- **Thư mục chính trên PC** là bản đang chạy của Control Center. Chỉ cập nhật thư mục này khi **không có job**, và người dùng tự khởi động lại.
- **Thứ tự gộp ở G1:** C2 → C1 → C6-lõi → E0–E2 → A1a → A2. Nhánh nào chưa đạt cổng thì để sang G2, không chờ.

### 9.6 Ai sửa file nào

| File | Đợt 1 | Đợt 2 | Đợt 3 |
|---|---|---|---|
| `hardware_profile.py` (mới), `.ps1` đo nhanh | E | E | E |
| `scheduler.py`, `job_store.py` | A (A1a) | E (đóng băng hồ sơ) | A (A1b) → B (B3, giữ đường cũ) |
| `final_renderer.py`, `blur_filter.py` | A | A | A |
| `job_pipeline.py`, module quét, `nvdec.py` | — | E (E3) | B |
| `cli.py`, `__main__.py`, `__init__.py`, `run.ps1`, `env.ps1`, `stage_cache.py`, `cache_dependencies.py`, `pyproject.toml`, `config/*` | — | **chỉ B1** | — |
| `recycle_bin.py`, `job_purge.py` (phần root) | C (C2) | — | — |
| `control_center.py`, `dashboard_v2/` | E (trang hồ sơ) → A (hàng đợi), lần lượt | — | C (server kích hoạt) |
| `review_workflow.py` | — | E (ghi chú C1, nếu chọn) | — |
| `models\` trên PC | **không ai sửa** | **không ai sửa** | **không ai sửa** |

### 9.7 Thời gian

**Tối thiểu khoảng 5–6 tuần** cho bản cài đầu tiên (ước tính). Bản 1 ước tính 4 tuần; review cho thấy cần thêm:
- server chỉ để kích hoạt;
- khóa cache an toàn cho bản build;
- tách trạng thái theo làn trong scheduler;
- phép thử pixel cho từng chuỗi giải mã.

## 10. Rủi ro và câu hỏi mở

1. **Kích hoạt chỉ để răn đe.** Người có chuyên môn vẫn bẻ được.
2. **Luật C1 trên CPU:** đo lại cho fp32, hay ghi chú "chưa hiệu chỉnh"? (E3)
3. **Florence-2 trên OpenVINO** chưa xác nhận được.
4. **Bản Nuitka không ký số** dễ bị phần mềm diệt virus báo nhầm.
5. **Lịch sử repo đã công khai** thì không thu hồi được.
6. **Mất khóa bí mật** thì không cấp key mới được.
7. **Hạn mức phút GitHub Actions** sau khi repo chuyển sang Private.
8. **NVENC và việc chạy song song** chưa có số đo VRAM khi Qwen2-VL đang chạy.
9. **Mọi cổng hiện chỉ chạy trên một card RTX 2060.**
10. **Cài lại Windows hoặc dùng VM nhân bản** ảnh hưởng tới mã máy (cần chính sách cấp lại).

## 11. Bảng trạng thái

| Mốc | Trạng thái |
|---|---|
| Kế hoạch bản 3 vào `main` | [x] 2026-10-11 |
| Repo chuyển sang Private | [ ] |
| Phase 1 bắt đầu: A, E, C (cloud), B0, C-spike | [ ] |
| B0 đo nền quét (`docs/handoff/B0-scan-baseline.md`) | [ ] |
| C-spike thử Nuitka (`docs/handoff/C-spike-nuitka.md`) | [ ] |
| C6-lõi thư viện kích hoạt + công cụ cấp key | [ ] |
| C0: sửa `AGENTS.md` (route mới, quy tắc khóa bí mật, thư mục cài) được duyệt | [ ] |
| E0 hợp đồng hồ sơ phần cứng được duyệt | [ ] |
| E1–E2 nhận diện, bảng cấu hình, đo nhanh | [ ] |
| A0 đo từng bước xuất | [ ] |
| A1a ưu tiên bản xuất | [ ] |
| A2 bộ lọc làm mờ (framemd5 + SHA-256 giống hệt) | [ ] |
| C1 khóa phiên bản (chỉ đọc) · C2 `install_root()` | [ ] |
| **Điểm gộp G1** | [ ] |
| Người dùng chọn cách xử lý C1 trên CPU | [ ] |
| E3 + đóng băng hồ sơ theo job | [ ] |
| A3 NVENC trong root tạm; người dùng duyệt; `AGENTS.md` cập nhật | [ ] |
| A4 (nếu làm) được duyệt; `AGENTS.md` cập nhật | [ ] |
| **B1 đợt sửa khóa cache duy nhất** (người dùng duyệt) | [ ] |
| **Điểm gộp G2** | [ ] |
| B2 · B3 · B4 · (B5) | [ ] |
| E4 cổng CPU fp32 | [ ] |
| A1b chạy song song nhiều làn | [ ] |
| C3 · C4 · C5 · C6 · C7 · C8 | [ ] |
| **Điểm gộp G3** | [ ] |
| D dọn code | [ ] |
| Bản cài đầu tiên được người dùng kiểm | [ ] |

## 12. Prompt dán cho từng session

**Cách dùng:**
- Prompt **[PC]** dán vào Claude Code trên máy Windows: Remote Control hoặc phiên chạy local trong `E:\DungChung\BiliFlow`.
- Prompt **[Cloud]** dán vào một session cloud mới của repo `dinhnguyentrungthanh/video-ai-edit`.
- **Mọi session đều theo quy trình bàn giao ở mục 13.**

### 12.1 Phase 1

```text
[A – PC] Xuất video nhanh hơn. Làm việc trong E:\DungChung\BiliFlow. Đọc AGENTS.md, rồi docs/PERF_INSTALL_PLAN.md mục 1–3, 5, 9, 13.
Chuẩn bị: git fetch origin; git worktree add temp\wt-export-speed -b perf/export-speed origin/main; làm theo mục 3.8. Không làm trong thư mục chính.
Việc: A0 (đo từng bước trong root tạm, dùng khung giờ GPU 1, báo số cho tôi) → A1a → A2 (cổng framemd5 + SHA-256 giống hệt). Chưa làm A3/A4.
Không sửa: cli.py, __main__.py, __init__.py, run.ps1, env.ps1, stage_cache.py, cache_dependencies.py, pyproject.toml, config/*, models\. Không ghi vào input\, output\, reports\, archive\ thật. Không gọi route bị cấm trên Control Center thật.
Chỉ ghi số job/revision/nhãn L-H-T, không tên phim hay tên file nguồn.
Bàn giao: sau mỗi bước chạy test liên quan, commit trong worktree, cập nhật docs/handoff/A-export.md (mẫu mục 13). Chưa push hay merge. Xong A2 thì báo tôi và chờ điểm gộp G1.
```

```text
[E – PC] Hồ sơ phần cứng. Làm việc trong E:\DungChung\BiliFlow. Đọc AGENTS.md, rồi docs/PERF_INSTALL_PLAN.md mục 1–4, 9, 13.
Chuẩn bị: git fetch origin; git worktree add temp\wt-hardware-profile -b feat/hardware-profile origin/main; làm theo mục 3.8.
Việc: E0 (schema + API + bảng cấu hình đã qua cổng): trình tôi duyệt trước khi code. Sau đó E1 (nhận diện trong tiến trình con, có lớp giao diện để test giả) → E2 (đo nhanh qua file .ps1 riêng có khóa, chỉ chạy ở khung giờ GPU 3 khi Control Center thật rảnh; trang Cài đặt với "Đo lại", "Chỉ dùng CPU", chỉ PC).
Không sửa: scheduler.py, job_store.py, job_pipeline.py, module quét, cli.py, __main__.py, __init__.py, run.ps1, env.ps1, stage_cache.py, cache_dependencies.py, pyproject.toml, config/*, models\. Không gọi route mới trên Control Center thật; kiểm trên Control Center thử (root tạm, cổng riêng).
Chỉ ghi nhãn L-H-T, không tên phim.
Bàn giao: sau mỗi bước chạy test, commit trong worktree, cập nhật docs/handoff/E-hardware.md. Chưa push hay merge. Xong E2 thì báo tôi và chờ G1.
```

```text
[C – Cloud] Bộ cài, phần code thuần. Repo dinhnguyentrungthanh/video-ai-edit. Đọc AGENTS.md, rồi docs/PERF_INSTALL_PLAN.md mục 1–3, 7, 9, 13.
Nhánh feat/installer từ origin/main.
Việc theo thứ tự:
(1) C0: soạn đề xuất sửa AGENTS.md, LICENSE, THIRD_PARTY_NOTICES vào docs/handoff/C-installer.md để tôi duyệt; chưa sửa AGENTS.md.
(2) C2: install_root() bằng file đánh dấu, giữ nguyên nghĩa allowed_project_root, có test; không sửa cli.py.
(3) C6-lõi: activation.py (mã máy 3 hash có muối, key Ed25519 qua pycryptodomex, kiểm hạn, chống chỉnh lùi đồng hồ), công cụ cấp key dạng script ngoài gói, cặp khóa test; chưa nối vào Control Center.
(4) C1: script chỉ đọc để tôi chạy trên PC (lấy revision từ models\*\manifest.json), cập nhật download_models.py theo lock; không chạy tải.
Không sửa: scheduler.py, final_renderer.py, job_pipeline.py, module quét, control_center.py, cli.py, __main__.py, __init__.py, run.ps1, env.ps1, stage_cache.py, cache_dependencies.py, pyproject.toml, config/* (trừ file lock mới), models\. Không bao giờ tạo hay đưa khóa bí mật thật vào repo.
Tiết kiệm credit: không chạy workflow nhiều agent, chỉ chạy test liên quan (cả bộ test để PC chạy ở G1), trả lời ngắn.
Bàn giao: sau MỖI bước chạy test, commit, cập nhật docs/handoff/C-installer.md, rồi push nhánh feat/installer (tôi cho phép push nhánh này, không bao giờ push main). Khi sắp hết credit: dừng việc mới, commit "wip:" kèm file bàn giao, rồi push.
```

```text
[B0 – PC] Đo nền quét, không sửa code. Làm việc trong E:\DungChung\BiliFlow. Đọc AGENTS.md, rồi docs/PERF_INSTALL_PLAN.md mục 1–3, 6 (B0), 9.4, 13.
Chuẩn bị: git fetch origin; git worktree add temp\wt-scan-baseline -b perf/scan-baseline origin/main.
Việc:
- performance_report --root E:\DungChung\BiliFlow cho 2–3 job gần đây (chỉ đọc);
- đo một đoạn của L (cả 4 nhóm) và H trong root tạm bằng harness benchmark sẵn có, chỉ ở khung giờ GPU 2 (ban đêm hoặc khi Control Center thật không có job), lấy khóa GPU;
- đo thời gian khởi động từng stage (python -X importtime, PowerShell);
- phác thiết kế B3 và danh sách dự kiến cho B1.
Ghi mọi số đo vào docs/handoff/B0-scan-baseline.md, chỉ dùng số job và nhãn L-H-T. Commit trong worktree; chưa push hay merge; không sửa code.
```

```text
[C-spike – PC] Thử Nuitka, không sửa code repo. Làm việc trong E:\DungChung\BiliFlow. Đọc AGENTS.md, rồi docs/PERF_INSTALL_PLAN.md mục 2.5, 7 (C-spike, C4), 13.
Trước khi tải Nuitka: hỏi tôi duyệt (tên gói, phiên bản, giấy phép, nơi cài trong E:\DungChung\BiliFlow).
Chuẩn bị: git fetch origin; git worktree add temp\wt-nuitka-spike -b spike/nuitka origin/main.
Việc: biên dịch gói biliflow (chế độ module, giữ __main__.py dạng mã nguồn) ra thư mục tạm trong temp\; thử python -m biliflow --help, performance_report --help, một stage quét ngắn và render-final trên clip tổng hợp trong root tạm; so với bản chạy từ mã nguồn. Ghi mọi vướng mắc (đọc file .py, subprocess, spawn, Florence trust_remote_code, khóa cache) vào docs/handoff/C-spike-nuitka.md.
Script build để trong temp\. Commit chỉ file bàn giao; chưa push hay merge.
```

### 12.2 Tiếp quản session cloud trên PC (khi cloud hết credit)

```text
[Tiếp quản <X> – PC] Session cloud <X> đã dừng. Làm việc trong E:\DungChung\BiliFlow.
git fetch origin <nhánh>; git worktree add temp\wt-<tên> <nhánh>  (nếu nhánh local chưa có: git worktree add temp\wt-<tên> -b <nhánh> origin/<nhánh>).
Đọc AGENTS.md, docs/PERF_INSTALL_PLAN.md (mục 1–3, mục của session <X>, 9, 13) và docs/handoff/<file bàn giao>.md.
Kiểm trạng thái: git log, git status; chạy test của các file đã sửa. Nếu commit cuối là "wip:" thì đọc kỹ phần "Đang dở".
Tiếp tục từ "Bước tiếp theo", giữ nguyên mọi quy tắc của session <X>. Commit trong worktree; chưa push hay merge nếu tôi chưa bảo.
```

Ví dụ cho C: `<X>` = C, `<nhánh>` = `feat/installer`, `<tên>` = `installer`, file bàn giao = `C-installer.md`.

### 12.3 Điểm gộp G1 (PC, khi các session Phase 1 báo xong)

```text
[Điều phối G1 – PC] Làm việc trong E:\DungChung\BiliFlow. Đọc AGENTS.md, docs/PERF_INSTALL_PLAN.md mục 9.5, 11, 13, và mọi docs/handoff/*.md trên các nhánh feat/installer (git fetch origin), feat/hardware-profile, perf/export-speed, perf/scan-baseline, spike/nuitka.
Tạo worktree temp\wt-merge-g1 từ origin/main. Gộp theo thứ tự mục 9.5, chỉ những nhánh đã đạt cổng (perf/scan-baseline và spike/nuitka chỉ có tài liệu). Sau mỗi nhánh chạy test liên quan; cuối cùng chạy cả bộ test (clip tổng hợp 1 s trong input\ của worktree, xóa sau).
Cập nhật bảng trạng thái mục 11, CHANGELOG.md, docs/PROJECT_STATUS.md, docs/SESSION_HANDOFF.md.
Báo kết quả cho tôi. Chỉ push main, và chỉ cập nhật thư mục chính, khi tôi đồng ý và Control Center thật không có job.
```

### 12.4 Các đợt sau (dùng khi tới lượt)

```text
[B – PC] Sau G1. Worktree temp\wt-scan-speed, nhánh perf/scan-decode từ origin/main. Đọc AGENTS.md, docs/PERF_INSTALL_PLAN.md mục 1–3, 6, 9, 13 và docs/handoff/B0-scan-baseline.md. Trình tôi danh sách B1 trước khi sửa; mọi thay đổi qua cổng giống hệt; không sửa models\. Bàn giao theo mục 13 (docs/handoff/B-scan.md). Chưa push hay merge.
```
```text
[D – Cloud hoặc PC] Sau G3. Nhánh refactor/shared-helpers. Đọc AGENTS.md, docs/PERF_INSTALL_PLAN.md mục 1–3, 8, 9, 13. Hỏi tôi định dạng giờ trước khi đổi. Không sửa cli.py, stage_cache.py, __init__.py, pyproject.toml (đã đi theo B1). Bàn giao theo mục 13 (docs/handoff/D-refactor.md). Cloud: push nhánh của mình sau mỗi bước; không bao giờ push main.
```

## 13. Bàn giao: để không mất việc khi session dừng (nhất là cloud hết credit)

### 13.1 Quy tắc

1. **Mỗi session có một file bàn giao** `docs/handoff/<mã>-<tên>.md` trên nhánh của mình:
   - `A-export.md`, `E-hardware.md`, `C-installer.md`, `B0-scan-baseline.md`, `C-spike-nuitka.md`;
   - sau này thêm `B-scan.md`, `D-refactor.md`.
2. **Điểm lưu sau MỖI bước nhỏ** (ví dụ xong C2, xong E1), và với cloud thì **ít nhất mỗi 1–2 giờ làm việc**:
   - chạy test của các file đã sửa;
   - commit;
   - cập nhật file bàn giao.
3. **Session cloud push nhánh của mình ở mỗi điểm lưu.** Người dùng cho phép việc này trong prompt, **chỉ cho nhánh đó và không bao giờ cho `main`**. Session trên PC commit trong worktree; các worktree dùng chung `.git` nên session điều phối thấy được mà không cần push.
4. **Sắp hết credit** (người dùng báo, hoặc session thấy cảnh báo hạn mức):
   - không bắt đầu bước mới;
   - commit phần đang dở thành `wip: <mô tả>`, ghi rõ phần "Đang dở";
   - push.
5. **Tiết kiệm credit cloud:**
   - không chạy workflow nhiều agent; tối đa một agent review ở cuối phase, hoặc để PC review lúc gộp;
   - chỉ chạy test liên quan. Cả bộ test do PC chạy ở điểm gộp;
   - không đọc đi đọc lại file lớn; trả lời ngắn.
6. **Việc cần Windows, GPU, video thật hay người dùng duyệt** luôn giao cho PC, không để cloud chờ.

### 13.2 Mẫu file bàn giao

```markdown
# Bàn giao <mã session> – <tên>
- Nhánh: <nhánh> · Commit cuối: <hash> · Cập nhật: <ngày giờ>
- Chạy ở: PC / Cloud · Trạng thái: đang làm / chờ người dùng / xong, chờ gộp

## Đã xong
- <bước>: <kết quả, test đã chạy, số đo (chỉ số job + nhãn L-H-T)>

## Đang dở
- <file>: <đang làm gì, còn thiếu gì> (để trống nếu không có)

## Bước tiếp theo
1. <việc cụ thể kế tiếp>

## Cách kiểm
- Lệnh test: <lệnh>
- Cổng của bước này: <tiêu chí>

## Chờ người dùng
- <quyết định hoặc duyệt đang chờ>

## Lưu ý cho người tiếp quản
- <bẫy, giả định, file không được sửa>
```
