# Kế hoạch: xuất và quét nhanh hơn, tự nhận diện phần cứng, bộ cài, kích hoạt

- Ngày lập: 2026-10-11 (Asia/Bangkok). Bản nháp, chờ review trước khi giao việc.
- Base: `main` `d477c70`.
- Nhánh tài liệu: `docs/perf-install-plan`.
- Repo đang công khai: tài liệu này không ghi tên phim, link hay tên miền thật. Phim tham chiếu được gọi theo nhãn:
  - **L196**: phim người đóng dài khoảng 196 phút.
  - **H110**: phim hoạt hình dài khoảng 110 phút.
  - **T45**: một tập phim bộ khoảng 45 phút.

Đọc theo thứ tự: `AGENTS.md`, file này, rồi mục của session mình (mục 4–8).

## 1. Quyết định của người dùng (2026-10-10 / 2026-10-11)

1. **Xuất video nhanh hơn và quét nhanh hơn**, nhưng chất lượng và kết quả duyệt giữ nguyên.
2. **Không hardcode cấu hình.** Xuất và quét phải tự nhận diện cấu hình máy và chọn cách chạy tốt nhất, giống như bộ cài tự nhận diện máy.
3. **Chạy được trên máy không có card đồ họa.** Phải chấp nhận vì nhiều máy không có card. Chất lượng giữ nguyên, chậm hơn là chấp nhận được.
4. **Máy test:** chưa có máy riêng. Đường chạy CPU được kiểm trên PC chính bằng công tắc "Chỉ dùng CPU". Card AMD và đồ họa Intel tích hợp làm sau, khi có máy test.
5. **Bộ cài `.exe`:**
   - hỏi thư mục cài và dựng cấu trúc thư mục giống bản hiện tại;
   - code phải được bảo vệ để không bị lấy cắp;
   - model được **tải lúc cài**;
   - build ở nơi nhanh và tiện nhất: GitHub Actions (Windows);
   - **không mua chữ ký số**: hướng dẫn cài ghi rõ bấm "Thông tin thêm → Vẫn chạy".
6. **Kích hoạt** bằng key gắn với mã máy:
   - có **key theo tháng** và **key vĩnh viễn**, cấp theo bộ;
   - miễn phí, không có máy chủ;
   - **không có key thì không làm gì cả**: chỉ hiện trang kích hoạt.
7. **Driver NVIDIA đã cập nhật** lên Studio 617.42. Bản FFmpeg trong `tools\ffmpeg` có `h264_nvenc`/`hevc_nvenc`, và lệnh encode thử bằng `h264_nvenc` chạy được.
8. **Phụ đề và dịch:** tạm dừng, làm sau. Nhánh `feat/read-audio-add-text` chưa có commit.
9. **Mỗi session đo trước rồi mới sửa,** và báo số đo trước/sau trên máy thật.

## 2. Hiện trạng đã kiểm trên code (`main` d477c70)

### 2.1 Xuất video

- **Một hàng đợi và một worker cho cả quét lẫn xuất** (`scheduler.py:121`, `:681`, `:703-716`).
  - Lệnh xuất bị đưa xuống cuối hàng (`queue_render` dùng `reseq=True`, `scheduler.py:553-563`).
  - Vì vậy bản xuất phải chờ mọi stage còn lại của mọi video quét xếp trước nó.
- **Encode bằng CPU.**
  - `libx264 -preset veryfast -crf 20`, có giới hạn dung lượng qua `-maxrate`/`-bufsize`.
  - Âm thanh luôn encode lại AAC 384 kb/s.
  - Không dùng `-hwaccel`. Manifest ghi `"gpu_used": false` (`final_renderer.py:592-614`, `:701`).
- **Bộ lọc làm mờ một vùng nhỏ:** `enable=` chỉ đặt ở `overlay` cuối (`blur_filter.py:97`, `:140`). Nhánh crop → delogo → gblur → geq → alphamerge có thể đang chạy trên mọi khung của cả đoạn, không chỉ trong khoảng làm mờ. Đây là suy luận, **chưa đo**.
- **Sau khi encode:**
  - giải mã lại toàn bộ file bằng CPU để kiểm tra (`final_renderer.py:642-656`);
  - hash SHA-256 bản xuất và video gốc;
  - `+faststart` ghi lại file một lần nữa.
- **Số đo cũ:** 6,6–8,2× thời gian thực, trên video 1280 px (`docs/TEST_RESULTS.md`).
- **Thử trên máy bạn (2026-10-11):** riêng bước encode x264 trên một đoạn 2 phút ít chuyển động đạt 36×. Khoảng cách lớn so với 6,6–8,2× là lý do phải đo từng bước trước (A0).

### 2.2 Quét

Số đo mới nhất, card RTX 2060, "Tăng tốc xử lý" bật:

| Phim | Phạm vi quét | Thời gian |
|---|---|---|
| L196 | chỉ quảng cáo | 866,6 s |
| L196 | cả 4 nhóm | 2.507 s (đo trước một số tối ưu sau đó) |
| H110 | cả 4 nhóm | 950,1 s |

- **Đã làm hoặc đã loại có số đo:** xem `docs/SCAN_PERFORMANCE.md` và `docs/CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md`. Không đề xuất lại các hướng đã loại.
- **Hướng còn lại, chưa làm:**
  - **L3:** giải mã bằng NVDEC cho stage 18+ và `live_safety`. Hai stage này đang nghẽn CPU (GPU khoảng 5%, CPU 99,7%).
  - **L4:** gộp lượt giải mã của 18+ vào lượt chung của `live_safety`. Tài liệu ước tính 5–10 phút trên L196.
  - **Khởi động stage:** `cli.py:8-43` nạp mọi module quét (torch, transformers, timm) ở đầu file, nên stage nào cũng tốn thời gian này, kể cả `build-review` và `render-final`. Chưa đo.
  - **Hash video gốc:** preflight hash tuần tự (khoảng 17 s trên L196), sau đó các stage còn hash lại trong nền.
- **Giải mã toàn bộ phim mỗi job:**
  - phim người đóng: 4 lần;
  - phim hoạt hình: 3 lần;
  - phim hỗn hợp: 5 lần.
  
  Chỉ luồng routing chạy kèm trong stage OCR dùng NVDEC, và chỉ với h264/yuv420p (`nvdec.py:16`).
- **Mọi stage chạy lần lượt trên một worker.** Cờ `uses_gpu` không được scheduler đọc.

### 2.3 Cấu hình đang viết cứng (yêu cầu số 2 muốn bỏ)

- `job_pipeline.py:129-155`:
  - `FAST_SCAN_OCR_BATCH_SIZE = 8`, `FAST_SCAN_OCR_FRAME_WINDOW = 4`, `FAST_SCAN_LOGO_ROUTING_WORKERS = 3`;
  - các precision `fp16`;
  - `--logo-decode nvdec`.
- `--device cuda` viết cứng ở 11 chỗ trong `job_pipeline.py:238-392`. Loader báo lỗi khi không có CUDA (ví dụ `scanner.py:421-422`).
- Encoder `libx264` và các tham số encode viết cứng trong `final_renderer.py`.
- Đường dẫn FFmpeg `tools\ffmpeg\bin\ffmpeg.exe` viết cứng ở khoảng 34 chỗ.

### 2.4 Chạy trên CPU

- **CLI đã có `--device {cuda,cpu}`**, và có nhánh float32 cho một số model. Nhưng:
  - Cờ fp16 của "Tăng tốc xử lý" báo lỗi trên CPU (`textscan.py:542-543`, `animation_safety_scanner.py:167`, `live_safety_scanner.py:92`).
  - `autocast` viết cứng `device_type="cuda"` (`ocr_precision.py:31`, `animation_safety_scanner.py:272`, `live_safety_scanner.py:230`).
  - **Lỗi:** `scripts/localize_visual_logo_report.py:134-135` luôn ép `pixel_values` sang float16, nên Florence trên CPU (float32) sẽ lỗi.
  - **Luật máu anime C1** chỉ chạy khi report là fp16 (`gore_triage.py:61-65`, `GORE_TRIAGE_SCAN_PRECISION = "fp16"`). Trên CPU fp32, C1 sẽ không di chuyển thẻ nào, tức hành vi duyệt khác máy có card.
- **NVDEC tự quay về CPU** khi không dùng được (`nvdec.py:41-60`). NVML là tùy chọn.
- **Ước tính của tôi, chưa đo:**
  - Florence trên CPU mất 7–11 s mỗi khung (`docs/ADULT_FALSE_ALARM_PLAN.md`).
  - Một phim như L196 quét cả 4 nhóm trên CPU có thể mất nhiều giờ.

### 2.5 Cài đặt và đóng gói

- **Thư mục gốc không viết cứng.** Nó lấy theo vị trí file: `Path(__file__).resolve().parents[2]` ở `cli.py:47`, `recycle_bin.py:60` (`INSTALL_ROOT`), `job_purge.py:36`, `control_center.py:125`; trong PowerShell là `Split-Path -Parent $PSScriptRoot`.
  - `INSTALL_ROOT` còn là **chốt an toàn khi xóa**: `job_purge.allowed_project_root` từ chối mọi root khác install root hoặc thư mục `temp` bên trong nó.
- **Chưa có script cài đặt.**
  - Python nằm trong `runtime\python`, cài qua uv. `.venv` dùng `requirements.lock.txt` với `torch==2.14.0+cu126`.
  - Florence có runtime riêng (`runtime\florence-python`, transformers 4.49.0).
  - Ngoài ra cần FFmpeg 9.0.1 (bản build **GPLv3**) và Deno.
- **Model không ghim phiên bản lúc tải.**
  - `scripts/download_models.py:354` lấy revision mới nhất rồi mới ghi vào manifest.
  - Qwen2-VL-2B (khoảng 4,4 GB) không có script tải.
  - EasyOCR tải không kiểm checksum.
- **Khóa cache quét đọc mã nguồn `.py`:** `cache_dependencies.py` parse AST của `src/biliflow/*.py`. Code đã biên dịch thì không còn file `.py` để đọc.
- **Mỗi stage chạy qua** `powershell -File scripts\run.ps1` → `python -m biliflow`.
- **Phần chỉ chạy trên Windows:** Thùng rác, `msvcrt`, DPAPI, ACL, Edge/Playwright.
- **Tài khoản nguồn phim** (vào `main` ngày 2026-10-11): phiên đăng nhập được mã hóa bằng DPAPI theo tài khoản Windows, nằm trong `state\source-accounts\<SID>\`, với ACL riêng do kho phiên tự tạo. Phiên này **không chép sang máy khác được**: máy mới phải đăng nhập lại.
- **Repo công khai và không có file LICENSE.** `pycryptodomex 3.23.0` đã có sẵn trong `requirements.lock.txt` (đi kèm yt-dlp) và có Ed25519, nên kích hoạt không cần thêm thư viện mới.

## 3. Nguyên tắc chung cho mọi session

1. Đọc `AGENTS.md` trước. Tuân thủ các mục về xóa, lưu trữ, tải video, tài khoản nguồn và Tailscale. **Không bao giờ** POST tới các route bị cấm trên Control Center thật.
2. **Repo công khai:** không ghi tên phim, link hay tên miền thật vào code, test, tài liệu hay commit. Dùng nhãn L196, H110, T45.
3. **Giữ nguyên chất lượng:**
   - Không đổi model, mật độ quét hay ngưỡng phát hiện.
   - Một cách chạy mới chỉ được bật sau khi qua cổng kiểm tra:
     - **giống hệt từng byte** (report, JPEG, danh sách duyệt) với thay đổi tuyên bố là "chính xác";
     - hoặc **danh sách duyệt giống hệt trên bộ Golden** (`scripts/evaluate_golden.py`, `review-diff`) với thay đổi về số học (fp16, CPU fp32, OpenVINO).
   - **Không dùng nén int8.**
   - Đổi encoder (NVENC…) phải qua đo SSIM và **người dùng duyệt** trước khi làm mặc định.
4. **Không hardcode cấu hình (yêu cầu số 2):**
   - Mọi lựa chọn ảnh hưởng tốc độ đều lấy từ **hồ sơ phần cứng** (mục 4): thiết bị, precision, cách giải mã, encoder, số worker, batch, số làn chạy song song.
   - Hồ sơ chỉ được chọn trong **bảng các cấu hình đã qua cổng**. Không tự bịa cấu hình mới.
   - Hằng số hiện tại chỉ còn là giá trị dự phòng.
   - Lựa chọn thực tế phải được ghi vào report và manifest để chạy lại được đúng như cũ.
5. **Đo trước:** mỗi session bắt đầu bằng bước đo (A0, B0, E4). Đo trên bản sao hoặc trong root tạm dưới `E:\DungChung\BiliFlow\temp`, không ghi vào `input\`, `output\`, `reports\` hay `archive\` thật.
6. **Chỉ có một GPU:** các lần đo của A, B và E xếp lịch lần lượt, và tránh lúc Control Center đang chạy job.
7. **`cli.py`, `__main__.py`, `run.ps1`, `stage_cache.py` và `cache_dependencies.py` được hash vào khóa cache của mọi stage.** Sửa một trong các file này làm toàn bộ cache quét bị tính lại. Vì vậy:
   - mọi thay đổi ở đây được **gom thành một đợt duy nhất** (B1);
   - hỏi người dùng trước khi làm.
8. **Trên PC:**
   - mỗi session làm trong worktree `temp\wt-*`, **không** làm trong thư mục chính;
   - không push hay merge nếu người dùng chưa bảo;
   - chỉ người dùng khởi động lại Control Center, và chỉ khi không có job.
9. **Cloud** không có GPU, model, video hay Windows. Cloud chỉ viết code và test bằng dữ liệu giả, rồi giao phần đo cho PC.
10. **Xong một mốc thì** đánh dấu bảng trạng thái ở mục 11 và cập nhật `CHANGELOG.md`, `docs/PROJECT_STATUS.md`, `docs/SESSION_HANDOFF.md`.

## 4. Session E: hồ sơ phần cứng và đường chạy CPU (nền cho A, B, C)

- **Ở đâu:** cloud viết; đo trên PC với công tắc "Chỉ dùng CPU".
- **Nhánh:** `feat/hardware-profile`.
- **Thời gian (ước tính):** E1–E4 khoảng 1,5–2 tuần; E5 thêm 2–4 tuần.

### E0. Hợp đồng giao diện (làm đầu tiên, khoảng nửa ngày)

Viết schema của `state\hardware-profile.json` và API `hardware_profile.choose(task)`. A, B và C dùng chung API này.

- **Phần nhận diện:**
  - card: hãng, tên, VRAM, driver;
  - CUDA dùng được với torch hay không;
  - FFmpeg: `-hwaccels`, các encoder có trong bản build, và **encode/decode thử thật** (giống lệnh người dùng đã chạy);
  - CPU: số nhân thật và số luồng, tập lệnh;
  - RAM và dung lượng đĩa còn trống.
- **Phần lựa chọn:** `device`, `precision`, `decode` theo từng stage, `encoder`, `routing_workers`, `ocr_batch`, `parallel_lanes`.
- **Mỗi lựa chọn ghi kèm lý do và lần đo.** Hồ sơ có `profile_version` và dấu vân tay phần cứng/driver. Khi phần cứng, driver hoặc phiên bản BiliFlow đổi thì tự đo lại.

### E1. Nhận diện

- Module mới `hardware_profile.py`. Mọi lệnh gọi riêng của Windows (WMI, nvidia-smi, NVML, `-hwaccels`) nằm sau một lớp giao diện để test bằng dữ liệu giả.
- Có lỗi thì luôn chọn **cấu hình an toàn nhất**: CPU fp32 và x264.

### E2. Bảng cấu hình đã qua cổng, đo nhanh, lưu lựa chọn

1. Bảng ban đầu chỉ gồm những gì đã được đo và chấp nhận:
   - CUDA + fp16 cho các stage đã qua cổng duyệt; batch OCR 8 + cửa sổ 4; số worker routing (đã chứng minh cho kết quả giống hệt, nên được tự chỉnh theo số nhân);
   - CPU fp32 (thêm vào sau khi E4 đạt);
   - encoder x264 (NVENC thêm vào sau khi A3 đạt).
2. Đo nhanh khoảng 1–3 phút trên một clip mẫu đi kèm, không phải video của người dùng, để chọn trong bảng.
3. Trang Cài đặt hiện hồ sơ đang dùng, có nút **"Đo lại"** và công tắc **"Chỉ dùng CPU"**.

### E3. Sửa đường chạy CPU

- Bỏ cờ fp16 khi chạy CPU. `autocast` theo thiết bị thật.
- Sửa lỗi ép float16 của Florence.
- Bỏ khóa GPU khi chạy CPU (`run.ps1` đã bỏ qua khóa khi có `--device cpu`).
- Torch: bộ cài chọn bản CUDA hoặc bản CPU (mục 7).
- **Luật máu anime C1 trên CPU:** khi report là fp32, C1 không di chuyển thẻ nào (hành vi hiện tại).
  - Phương án 1: đo lại ngưỡng cho fp32 trên bộ Golden và các bằng chứng đã có.
  - Phương án 2: giữ nguyên, và ghi chú trên thẻ "C1 chưa hiệu chỉnh cho CPU".
  - **Người dùng chọn.**

### E4. Đo trên PC với "Chỉ dùng CPU"

- Trên đoạn trích của L196, H110 và một T45:
  - so **danh sách duyệt** của CPU fp32 với bản GPU;
  - đo tốc độ và RAM đỉnh.
- Chỉ bật CPU fp32 trong bảng sau khi đạt cổng.

### E5. Đợt sau: OpenVINO trên CPU

- Chuyển từng model và qua cổng Golden. Qwen2-VL đã được OpenVINO hỗ trợ; Florence-2 chưa xác nhận.
- Card AMD và đồ họa Intel tích hợp: chỉ làm khi có máy test.

## 5. Session A: xuất video nhanh hơn

- **Ở đâu:** PC.
- **Nhánh / worktree:** `perf/export-speed` / `temp\wt-export-speed`.
- **Thời gian (ước tính):** 3–5 ngày.

### A0. Đo từng bước

Chạy `python -m biliflow.performance_report --job-id <N>` (chỉ đọc). Sau đó tách thời gian xuất của một T45 và một đoạn 1080p, làm trên bản sao trong root tạm:

- bộ lọc (encode giả `-f null`);
- encode;
- kiểm tra sau xuất (giải mã lại toàn bộ);
- hash;
- `+faststart`.

Riêng nhánh làm mờ vùng nhỏ thì đo hai trường hợp: có và không có `enable` sớm.

### A1. Làn xuất riêng

- Bản xuất **không còn xếp sau việc quét**. Số làn và mức ưu tiên lấy từ hồ sơ phần cứng:
  - máy có card: xuất chạy song song với quét;
  - máy chỉ có CPU: chạy lần lượt hoặc hạ ưu tiên một bên, theo số đo tranh chấp CPU.
- **Giữ nguyên mọi chốt chặn:** khóa render `Local\BiliFlowFinalRender` và `state/final-render.lock`, `job_action_lock`, hủy/tạm dừng/tiếp tục, retire, finalize, và việc khóa job khi xóa hoặc lưu trữ.
- Có test cho trạng thái xuất song song với quét.

### A2. Bộ lọc làm mờ

Chỉ xử lý đúng khoảng cần làm mờ. Ví dụ: tách đoạn tại biên của khoảng làm mờ, hoặc đặt `enable`/`trim` cho cả nhánh.

**Cổng:** `framemd5` giống hệt trên clip tổng hợp và trên bản sao video thật.

### A3. Encoder chọn theo hồ sơ

- NVENC `h264_nvenc`; giới hạn dung lượng ánh xạ sang `-rc vbr` với `-maxrate`/`-bufsize`.
- Tự quay về x264 nếu thiếu hoặc encode thử thất bại.
- Ghi encoder đã dùng vào manifest. Tên bản xuất (export identity) không đổi, vì các thao tác render vẫn giống nhau.
- **Cổng:** đo SSIM (và VMAF nếu bản build có) cùng dung lượng file trên 3 tập, so với x264. **x264 vẫn là mặc định cho tới khi người dùng duyệt.**

### A4. Kiểm tra sau xuất

Chỉ đổi cách kiểm tra (ví dụ giải mã bằng NVDEC) nếu A0 cho thấy bước này chiếm phần đáng kể và người dùng chấp nhận. Giải mã bằng CPU vẫn là chuẩn.

### Kỳ vọng (ước tính, A0 sẽ xác nhận)

- Một T45 từ khoảng 6–8 phút còn khoảng 1,5–3 phút.
- Không còn chờ sau việc quét.

### Được sửa

`scheduler.py`, `job_store.py` (nếu cần), `final_renderer.py`, `blur_filter.py`, phần hiển thị hàng đợi trong `control_center.py` và `dashboard_v2/`, kèm test.

## 6. Session B: quét nhanh hơn

- **Ở đâu:** PC. Bắt đầu sau khi A1 đã vào `main` và E0–E2 xong.
- **Nhánh / worktree:** `perf/scan-decode` / `temp\wt-scan-speed`.
- **Thời gian (ước tính):** 1–2 tuần.

### B0. Đo nền mới

- Một phim người đóng quét cả 4 nhóm (đoạn trích L196) và H110.
- Đo thêm thời gian khởi động từng stage (`python -X importtime`, PowerShell).

### B1. Đợt sửa `cli.py` duy nhất

- Nạp thư viện khi cần (lazy import).
- Gom luôn mọi thay đổi CLI mà A, C, E cần: cờ thiết bị và giải mã, đường dẫn tool. **Danh sách phải được chốt trước khi sửa.**
- Hỏi người dùng trước, vì cache quét bị tính lại một lần.

### B2. NVDEC cho stage 18+ và `live_safety`

- Theo hồ sơ phần cứng và phép thử "giống hệt pixel" cho từng video. NVDEC hiện chỉ được chứng minh với h264/yuv420p.
- **Cổng:** report và JPEG giống hệt từng byte (`scripts/benchmark_stage_equivalence.py`).

### B3. Gộp lượt giải mã 18+ vào `live_safety`

Chỉ khi job chọn cả 18+ lẫn máu/bạo lực. Cổng như B2.

### B4. Hash một lần

Preflight hash xong thì truyền kết quả cho các stage sau, bớt số lần đọc toàn bộ file.

### B5. Tùy chọn

Cho stage chỉ dùng CPU của video sau chạy chồng với stage GPU của video trước, theo VRAM và số nhân trong hồ sơ. Chỉ làm khi đo có lợi và kết quả không đổi.

### Kỳ vọng (ước tính)

- Chỉ quét quảng cáo: nhanh hơn khoảng 10%.
- Phim người đóng quét cả 4 nhóm: nhanh hơn khoảng 20–30%.

## 7. Session C: bộ cài, biên dịch, kích hoạt

- **Ở đâu:** cloud viết; build và cài thử bằng GitHub Actions; kiểm thật trên PC.
- **Nhánh:** `feat/installer`.
- **Thời gian (ước tính):** 3–4 tuần.

### C0. Việc cần người dùng duyệt trước

- **Sửa `AGENTS.md`:** bản cài ở máy khác không nằm trong `E:\DungChung\BiliFlow`. Mục "giữ runtime trong `E:\DungChung\BiliFlow`" phải đổi thành "trong thư mục cài".
- **Thêm file `LICENSE`** (độc quyền) và cập nhật `THIRD_PARTY_NOTICES.md`: ghi chú GPL của FFmpeg và nơi lấy mã nguồn FFmpeg.

### C1. Khóa phiên bản model

1. Tạo `config/models.lock.json` ghi revision và SHA-256 từng file của 9 model.
2. Viết một script **chỉ đọc** để người dùng chạy trên PC, lấy revision từ các `models\*\manifest.json` hiện có.
3. Cập nhật `download_models.py`:
   - tải đúng phiên bản đã khóa, tải tiếp khi rớt mạng, kiểm SHA-256;
   - thêm Qwen2-VL-2B;
   - thêm checksum cho EasyOCR.

### C2. Một hàm `install_root()` duy nhất

- Thay các chỗ `parents[2]`.
- **Giữ nguyên nghĩa** của `recycle_bin.INSTALL_ROOT` và chốt `job_purge.allowed_project_root`: các hàm xóa vẫn chỉ nhận install root hoặc thư mục `temp` bên trong. Có test.

### C3. Khóa cache khi chạy bản build

- **Bản build:** khóa cache theo **mã build** và hash từng module, tính sẵn lúc build và ghi trong một manifest.
- **Bản chạy từ mã nguồn** (thư mục dev hiện tại): **giữ nguyên** cách cũ, nên cache trên PC không bị mất.

### C4. Biên dịch

- Biên dịch gói `biliflow` thành `.pyd` bằng Nuitka (bản miễn phí). Thư viện bên thứ ba giữ nguyên trong `.venv`.
- Script chạy lúc runtime (ví dụ `scripts/localize_visual_logo_report.py`, chạy trong runtime Florence) phải được đưa vào gói hoặc biên dịch luôn.
- Khởi động qua `.exe`; `.cmd`/`.ps1` chỉ còn là lớp mỏng.
- JS của giao diện được làm rối mã. Không thể giấu hoàn toàn vì trình duyệt phải đọc được.

### C5. Trình cài đặt (Inno Setup)

1. Hỏi thư mục cài, kiểm dung lượng đĩa. Tạo `input`, `output`, `archive`, `state`, `reports`, `models`, `tools`, `cache`, `temp`, `logs`.
2. **Không tự tạo** `state\source-accounts`. Kho phiên tự tạo thư mục này với ACL riêng, và từ chối thư mục có ACL lạ.
3. Chạy E1 để chọn bản torch CUDA hay bản CPU.
4. Tải torch và model, hiện tiến độ, tải tiếp khi rớt mạng, kiểm SHA-256.
5. Cài Playwright, dùng Edge có sẵn trong Windows. Cài FFmpeg và Deno đi kèm.
6. Tạo shortcut "Mở BiliFlow" và "Tắt BiliFlow".
7. Gỡ cài **giữ dữ liệu** (`input`, `output`, `archive`, `state`, `reports`), trừ khi người dùng chọn xóa.

**Điểm cần review:** thư mục cài nên giới hạn quyền ghi, để tài khoản Windows khác không sửa được file chương trình.

### C6. Kích hoạt

- **Mã máy:** SHA-256 của MachineGuid, UUID bo mạch (SMBIOS) và số serial ổ hệ thống. Chỉ cần khớp 2/3.
- **Key:** chuỗi để dán, gồm nội dung + chữ ký Ed25519 (`pycryptodomex`), mã hóa base32, chia nhóm.
  - Nội dung: mã máy, tên khách, mã bộ, loại (`month:N` hoặc `perpetual`), ngày cấp, ngày hết hạn, mã key.
- **Không có key hoặc key không hợp lệ:** chỉ trang kích hoạt hoạt động.
  - Không quét, không xuất, không tải, không duyệt.
  - Scheduler và trình tải không khởi động. Các route khác trả 403.
  - Dữ liệu trên đĩa không bị đụng tới.
- **Key tháng hết hạn trong lúc chạy:** stage đang chạy được chạy xong, rồi khóa. Hàng đợi giữ nguyên.
- **Chống chỉnh lùi đồng hồ:** lưu thời điểm muộn nhất từng thấy (có chữ ký cục bộ). Khi có mạng thì so thêm giờ trên internet.
- **Công cụ cấp key** chạy trên PC của người dùng:
  - ghi sổ số máy theo từng bộ;
  - **khóa bí mật chỉ nằm trên PC đó và phải sao lưu**; không bao giờ đưa vào Git hay GitHub Actions;
  - bộ cài chỉ chứa khóa công khai.
- **PC của người dùng** tự cấp cho mình một key vĩnh viễn. Test dùng cặp khóa riêng cho test.

### C7. GitHub Actions

- Đẩy tag thì tự build: biên dịch, Inno Setup, rồi đưa lên Release của repo private.
- **Cài thử tự động** trên máy Windows sạch: cài đặt, kích hoạt bằng key test, rồi chạy một clip ngắn với model nhỏ.
  - Máy build chỉ có 2 nhân, 8 GB RAM, 14 GB đĩa, nên không chạy được Qwen2-VL và không quét cả phim.
- Theo dõi hạn mức phút miễn phí. Vượt hạn mức thì build trên PC.

### C8. Hướng dẫn cài

- Cảnh báo SmartScreen: bấm "Thông tin thêm → Vẫn chạy".
- Phần mềm diệt virus báo nhầm: gửi file cho Microsoft kiểm tra (miễn phí).
- Ghi rõ: phiên đăng nhập nguồn phim phải đăng nhập lại trên máy mới.

## 8. Session D: dọn code (làm cuối)

- **Ở đâu:** cloud.
- **Nhánh:** `refactor/shared-helpers`.
- **Thời gian (ước tính):** 3–5 ngày.

**Việc:**
1. Module dùng chung cho:
   - ghi JSON an toàn: file tạm riêng cho mỗi lần ghi + `fsync`. Dùng lại cách của `source_archive_files.py`;
   - SHA-256;
   - đọc JSON;
   - lấy giờ hiện tại.
2. `tool_path()` thay khoảng 34 chỗ viết cứng đường dẫn FFmpeg.
3. Thống nhất định dạng giờ. **Người dùng chọn định dạng**, vì chữ trên thẻ sẽ đổi.
4. Một nguồn duy nhất cho số phiên bản (hiện `pyproject.toml` ghi 0.7.19, còn `__init__` ghi 0.7.24).
5. Khai báo numpy và opencv trong `pyproject.toml`.
6. `RoutingPool` dùng `spawn` để bộ test không treo trên Linux.
7. Harness Node gọi `process.exit`.

**Lưu ý:** phần chạm vào module mà module quét import sẽ làm cache bị tính lại. Phần đó gộp chung đợt với B1.

## 9. Thứ tự và ai sửa file nào

```
Đợt 1:  E0–E2 (cloud)  ║  A0–A2 (PC)  ║  C0–C2 (cloud)
Đợt 2:  A3–A4 (PC, dùng hồ sơ)  ║  E3–E4 (cloud + đo trên PC)  ║  C4–C7 (cloud + Actions)
Đợt 3:  B (PC)  →  B1 gom mọi thay đổi cli.py/cache  →  C3 → D
Sau:    E5 OpenVINO · AMD/Intel GPU · phụ đề/dịch
```

| File | E | A | B | C | D |
|---|---|---|---|---|---|
| `hardware_profile.py` (mới) | ✅ | đọc | đọc | đọc | — |
| `scheduler.py` | — | ✅ làn xuất | ✅ hash (sau A) | ✅ chặn khi chưa kích hoạt (sau A, B) | — |
| `final_renderer.py`, `blur_filter.py` | — | ✅ | — | — | sau A |
| `job_pipeline.py`, module quét, `nvdec.py` | ✅ CPU/precision | — | ✅ (sau E) | — | — |
| `cli.py`, `__main__.py`, `run.ps1`, `stage_cache.py`, `cache_dependencies.py` | — | — | ✅ **một lần** | ✅ C3 (sau B1) | — |
| `recycle_bin.py`, `job_purge.py` (chỉ phần root) | — | — | — | ✅ C2 | — |
| `control_center.py`, `dashboard_v2/` | ✅ trang hồ sơ | ✅ hàng đợi | — | ✅ trang kích hoạt | — |
| `scripts/download_models.py`, `config/models.lock.json`, bộ cài | — | — | — | ✅ | — |

**Tối thiểu khoảng 4 tuần** cho bản cài đầu tiên: có kích hoạt, code đã biên dịch, xuất và quét nhanh hơn, chạy được trên máy NVIDIA và máy chỉ có CPU.

## 10. Rủi ro và câu hỏi mở

1. **Luật máu anime C1 trên CPU:** đo lại cho fp32, hay ghi chú "chưa hiệu chỉnh" (E3)?
2. **Florence-2 trên OpenVINO** chưa xác nhận được (E5).
3. **Bản build bằng Nuitka hay bị phần mềm diệt virus báo nhầm** khi không có chữ ký số.
4. **Quyền ghi của thư mục cài:** tài khoản Windows khác có thể sửa file chương trình (C5).
5. **Lịch sử repo đã từng công khai.** Chuyển sang Private không thu hồi được những gì đã bị clone.
6. **Mất khóa bí mật** thì không cấp key mới cho các bản đã cài được nữa.
7. **Repo chuyển sang Private** thì GitHub Actions tính phút theo hạn mức miễn phí, phút Windows tính gấp đôi.
8. **Kích hoạt chặn cả trang duyệt.** Người dùng đã chọn "không key thì không làm gì".

## 11. Bảng trạng thái

| Mốc | Trạng thái |
|---|---|
| Kế hoạch được review và người dùng duyệt | [ ] |
| E0 hợp đồng giao diện hồ sơ phần cứng | [ ] |
| E1–E2 nhận diện, bảng cấu hình, đo nhanh | [ ] |
| E3 sửa đường chạy CPU | [ ] |
| E4 cổng CPU fp32 trên PC | [ ] |
| A0 đo từng bước xuất | [ ] |
| A1 làn xuất riêng | [ ] |
| A2 bộ lọc làm mờ (framemd5 giống hệt) | [ ] |
| A3 NVENC qua cổng SSIM, người dùng duyệt | [ ] |
| B0 đo nền quét | [ ] |
| B1 đợt `cli.py`/cache duy nhất (người dùng duyệt) | [ ] |
| B2–B4 NVDEC, gộp giải mã, hash một lần | [ ] |
| C0 `AGENTS.md`, `LICENSE` | [ ] |
| C1 khóa phiên bản model | [ ] |
| C2 `install_root()` | [ ] |
| C3 khóa cache cho bản build | [ ] |
| C4 biên dịch Nuitka | [ ] |
| C5 Inno Setup | [ ] |
| C6 kích hoạt + công cụ cấp key | [ ] |
| C7 GitHub Actions build + cài thử | [ ] |
| D dọn code | [ ] |
| Bản cài đầu tiên được người dùng kiểm | [ ] |

## 12. Prompt dán cho từng session

```text
[E – cloud] Repo dinhnguyentrungthanh/video-ai-edit. Đọc AGENTS.md và docs/PERF_INSTALL_PLAN.md (mục 3, 4, 9). Nhánh feat/hardware-profile từ main. Làm E0 trước và đưa hợp đồng cho tôi duyệt; sau đó E1–E3. Không sửa cli.py, run.ps1, stage_cache.py, cache_dependencies.py. Commit trên nhánh; hỏi tôi trước khi push.
```
```text
[A – PC] Làm việc trong E:\DungChung\BiliFlow. Đọc AGENTS.md và docs/PERF_INSTALL_PLAN.md (mục 3, 5, 9). Worktree temp\wt-export-speed, nhánh perf/export-speed từ main. Làm A0 (đo) trước và báo số; rồi A1–A2. A3 dùng hồ sơ phần cứng của session E. Không sửa cli.py; không ghi vào input\/output\/reports\/archive thật. Chưa push/merge.
```
```text
[C – cloud] Repo dinhnguyentrungthanh/video-ai-edit. Đọc AGENTS.md và docs/PERF_INSTALL_PLAN.md (mục 3, 7, 9). Nhánh feat/installer từ main. Làm C0 (đề xuất thay đổi AGENTS.md/LICENSE để tôi duyệt), C1, C2. C3 chỉ làm sau đợt B1. Không đưa khóa bí mật cấp key vào repo. Commit trên nhánh; hỏi tôi trước khi push.
```
```text
[B – PC] Làm việc trong E:\DungChung\BiliFlow. Đọc AGENTS.md và docs/PERF_INSTALL_PLAN.md (mục 3, 6, 9). Chỉ bắt đầu khi A1 và E0–E2 đã vào main. Worktree temp\wt-scan-speed, nhánh perf/scan-decode. B0 đo trước; hỏi tôi trước khi làm B1. Mọi thay đổi qua cổng giống hệt. Chưa push/merge.
```
```text
[D – cloud] Repo dinhnguyentrungthanh/video-ai-edit. Đọc AGENTS.md và docs/PERF_INSTALL_PLAN.md (mục 3, 8, 9). Chỉ bắt đầu khi A, B đã vào main. Nhánh refactor/shared-helpers. Hỏi tôi định dạng giờ trước khi đổi; phần chạm module quét gộp với đợt B1. Hỏi tôi trước khi push.
```
