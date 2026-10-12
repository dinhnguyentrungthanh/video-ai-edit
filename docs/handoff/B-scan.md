# Bàn giao B – quét nhanh hơn (đợt B1)

- **Nhánh:** `perf/scan-b1` (tách từ `origin/main` e534c12).
- **Worktree:** `temp\wt-scan-speed`.
- **Commit:**
  - a282e47: bước 1;
  - 9f3f7f0: code B1;
  - commit chứa file này: cổng và bàn giao.
- **Cập nhật:** 2026-10-12.
- **Chạy ở:** PC.
- **Trạng thái:** bước 2 xong, chờ điều phối gộp ở G2 cùng E3. Chưa push, chưa merge.

## Quyết định của người dùng (2026-10-12)

- **Phạm vi:**
  - làm mục 1, 8, 4, 5, 10, 7;
  - mục 6 theo phương án (b): phiên bản torch, CUDA của torch, tên GPU, compute capability, không có driver;
  - không làm mục 2, 3, 9 (mục 3 để sau).
- **Chất lượng giữ nguyên:**
  - không đổi model, ngưỡng, số khung lấy mẫu, độ phân giải, fp16/fp32, batch hay bất kỳ tham số phát hiện nào;
  - trên bản sao của job 125 và 114, bốn report phải giống hệt bản trước B1, trừ trường thời gian và hiệu năng;
  - khác dù chỉ một phát hiện, một khung hay một vùng: dừng và báo người dùng.
- **Mục 8 có cổng riêng:**
  - chạy trên bản sao `cache\stage-results`;
  - mục nào manifest thiếu `bytes` thì duyệt file của riêng mục đó;
  - không bao giờ gọi `prune` trên thư mục cài.
- **Khóa cache:** chỉ đổi một lần, trong đợt này. Nó vào `main` ở G2, cùng E3.

## Đã xong

### Bước 2: code (9f3f7f0)

- **Mục 1 (nạp thư viện khi cần):**
  - `cli.py` chỉ nạp module nhẹ; mỗi nhánh lệnh tự import scanner của nó;
  - `CONTENT_STYLES` chuyển sang `job_pipeline.py`, và `review_workflow` import nó từ đó;
  - `import biliflow.cli` giảm từ 7,3 s xuống 0,13 s và không còn nạp torch, timm, easyocr, transformers hay cv2. Một test subprocess kiểm việc này;
  - các test patch `biliflow.cli.<hàm>` giờ patch đúng module gốc: `test_cli`, `test_batch_prefetch`, `test_gore_triage`, `test_routing_prewarm`, `test_scene_cards`, `test_shot_cuts` và `scripts/benchmark_exact_speedups.py`.
- **Mục 8:**
  - `prune()` lấy cỡ của mỗi mục từ `bytes` trong manifest, cộng cỡ của chính manifest;
  - manifest thiếu `bytes` hoặc `bytes` sai kiểu: chỉ duyệt file của mục đó.
- **Mục 4 (khóa cache):**
  - khóa bỏ qua `.gitkeep`, `*.pyc`/`*.pyo`, `__pycache__` và `.cache*`, giống bộ lọc của `scripts/models_lock.py`;
  - mỗi stage chỉ tính thư mục model của nó (`cache_dependencies.STAGE_MODELS`). Thư mục thiếu được ghi `missing`; stage không có trong bảng thì tính cả `models\`;
  - test suy ra model của từng stage từ `pipeline_stages`, mặc định của argparse, các nhánh của `cli.main` và script localize. Mọi tên phải có trong `config/models.lock.json`.
- **Mục 5 (C3, khóa cho bản build):**
  - bản build không có `src\biliflow\*.py` thì dùng `biliflow-build.json`. File này do `build_code_manifest()` tạo, chứa `build_id` và, cho mỗi stage, danh sách module kèm SHA-256;
  - không có nguồn mà manifest hỏng, thiếu stage, là reparse point hoặc lớn hơn 4 MiB: `cacheable()` trả False. Stage chạy không cache, không lỗi;
  - có nguồn thì luôn dùng nguồn.
- **Mục 6(b):**
  - khóa có thêm `runtime`: `torch` và `cuda` đọc từ `torch/version.py` (không import torch), và danh sách GPU (tên, compute capability) lấy qua NVML của E (`hardware_system`, chỉ đọc);
  - NVML lỗi thì khóa ghi trạng thái lỗi đó;
  - không có driver.
- **Mục 10:** `cli.py` dùng `install_root()` thay cho `parents[2]`.
- **Mục 7:**
  - `run.ps1` bắt `AbandonedMutexException` và coi như đã giữ mutex;
  - `pyproject.toml` lấy phiên bản từ `biliflow.__version__` (0.7.24). Trước đây nó ghi riêng 0.7.19, lệch với code.

### Bước 2: cổng và số đo

Bằng chứng nằm ở `docs/evidence/perf-scan/`: `README.md` mô tả cách làm, `b1/20261012-gate.json` chứa số.

- **Cách chạy:**
  - root tạm `temp\b1-gate`, chép nguồn của job 125 (L, 44 phút) và 114 (L, 56 phút), kiểm SHA-256;
  - quét qua đúng đường của Control Center (`JobScheduler._select`/`_execute`, `run.ps1`, mutex GPU), cùng thiết lập với job thật: quảng cáo, live action, careful, OCR batch 1, "Tăng tốc xử lý";
  - chỉ chạy khi Control Center thật không quét;
  - hai vòng, vòng 2 đảo thứ tự.
- **Cổng chất lượng: đạt.**
  - So 4 file: `text-scan.json`, `scan.json`, `scan-florence.json`, `review-queue.json`.
  - Mọi cặp đều chỉ khác giá trị ở các trường được bỏ qua: base↔b1 trong cùng vòng, và chéo giữa hai vòng (base r1↔base r2, b1 r1↔b1 r2). Không thiếu khóa nào, không danh sách nào đổi độ dài.
  - Có 29 trường được bỏ qua khi so; danh sách đầy đủ ở `quality_gate.ignored_fields`:
    - `created_at`, `updated_at`;
    - `region_localization_summary/created_at`, `region_localization_summary/elapsed_seconds`;
    - `metrics/elapsed_seconds`, `metrics/peak_process_ram_bytes`, `metrics/video_seconds_per_processing_second`;
    - `metrics/cross_frame_recognition/detect_seconds`, `metrics/cross_frame_recognition/recognize_seconds`;
    - mọi `phases/*/wall_seconds`, `total_wall_seconds` và `unattributed_wall_seconds` dưới `metrics/performance`, `metrics/localization_performance` và `metrics/vlm_prefetch_performance`.
- **Cổng mục 8: đạt.**
  - Chạy trên bản sao `cache\stage-results` trong `temp` (241 mục, 0,92 GB, đường dẫn `\\?\`).
  - Cỡ ghi trong manifest khớp cỡ duyệt thực tế ở cả 241 mục.
  - Mọi trường hợp xóa đúng các mục như cách cũ. Thời gian dưới đây tính cả việc xóa file:

    | Trường hợp | Xóa / giữ | Cũ (s) | Mới (s) |
    |---|---|---|---|
    | mặc định | 9 / 232 | 3,97 | 1,70 |
    | giới hạn 300 MB | 138 / 103 | 7,69 | 4,82 |
    | tuổi 1 ngày | 228 / 13 | 10,66 | 5,25 |
    | 300 MB, 7 manifest bỏ `bytes` | 138 / 103 | 7,59 | 5,17 |

  - Không có gì để xóa (trường hợp thường gặp): 3,5 s → 0,07 s (0,66 s lúc máy nguội).
- **Số đo từng stage** (wall, giây). Cả hai bản đều tắt cache, xem phần "Phát hiện ngoài phạm vi". Mỗi ô ghi vòng 1 / vòng 2:

  | Stage | 125 trước B1 | 125 B1 | 114 trước B1 | 114 B1 |
  |---|---|---|---|---|
  | preflight | 0,5 / 0,6 | 0,5 / 0,5 | 0,7 / 0,8 | 0,7 / 0,7 |
  | text | 110,5\* / 95,4 | 94,2 / 96,2 | 136,0 / 130,9 | 132,8 / 128,8 |
  | visual_logo | 64,2 / 61,8 | 61,9 / 61,8 | 79,0 / 77,0 | 78,0 / 80,0 |
  | localize_logo | 28,4 / 20,3 | 12,2 / 21,3 | 28,4 / 36,4 | 36,5 / 30,4 |
  | build_review | 51,6 / 48,7 | 41,5 / 42,5 | 50,6 / 50,7 | 44,5 / 50,6 |
  | **Tổng** | 255,2\* / 226,8 | 210,4 / 222,4 | 294,8 / 295,9 | 292,6 / 290,6 |

  \* Đây là lần quét đầu tiên sau khi dựng root, file cache của Windows còn nguội.
- **Kết luận: lợi ít hơn ước tính ở bước 1.**
  - **Mục 1** giảm khoảng 4–10 s mỗi job. Ở vòng 2, job 125 giảm 4,4 s và job 114 giảm 5,3 s. Trung bình hai vòng: job 114 giảm 3,7 s, job 125 giảm 10,4 s nếu bỏ lần nguội.
    - Phần lớn đến từ build_review, stage không cần torch: thời gian nạp giảm từ 7,7 s xuống 0,23 s; wall giảm 6–10 s ở 3/4 lần chạy, không giảm ở 1 lần.
    - text và visual_logo gần như không đổi. Lúc load model, chúng vẫn nạp torch, transformers và module của model (Qwen2-VL 6,3 s; OCR cùng mô hình ngữ nghĩa 6,5 s). Con số này ngang với phần `cli.py` cũ nạp sẵn.
    - Ước tính ~16 s ở bước 1 đã bỏ sót phần thư viện mà stage tự cần.
    - localize_logo dao động 12–36 s ở cả hai bản, vì phần "unattributed" của Florence chiếm 4–13 s và B1 không đụng tới phần này. Không rút ra được kết luận cho stage này.
  - **Mục 8** đo riêng, vì cổng phải tắt cache. Mỗi lần prune nhanh hơn 3,4–5,4 s. Khi cache bật, prune chạy 3 lần mỗi job, nên tiết kiệm khoảng 10–16 s mỗi job với cache hiện tại (241 mục); cache càng lớn thì càng tiết kiệm nhiều.
  - **Tính khóa cache:** mỗi lần còn 0,06–0,13 s, nhanh hơn trước 0,02–0,04 s. Lần đầu lúc máy nguội: 0,89 s → 0,18 s.
  - **Tổng khi cache bật:** khoảng 14–26 s mỗi job trên 230–295 s, tức khoảng **5–9%**. Bước 1 ước 12–14%, nhưng con số đó gồm cả mục 3 (chưa làm) và ước mục 1 quá cao.
- **Đổi khóa cache:** sau G2, mọi stage quét lại một lần, vì cache cũ không còn trùng khóa.

### Phát hiện ngoài phạm vi (chưa sửa)

**Đường dẫn dài.** Lỗi này có từ trước B1; B1 không đổi đường dẫn nào.

- Máy đang để `LongPathsEnabled=0`; tôi chỉ đọc giá trị này, không đổi.
- Đường dẫn dài nhất trong `cache\stage-results` ở thư mục cài là 253 ký tự. Lúc lưu, thư mục `.tmp` làm nó thành 257.
- Nếu thư mục cài dài hơn `E:\DungChung\BiliFlow` (21 ký tự) khoảng 3 ký tự, `StageArtifactCache.store()` ném OSError.
- Scheduler không bắt riêng lỗi này. Stage đã quét xong vẫn thành FAILED_RETRYABLE và job thành FAILED; chạy lại cũng hỏng như cũ.
- Việc này liên quan C, vì bộ cài cho người dùng chọn thư mục.
- Cách sửa cần người dùng duyệt. Có thể:
  - bỏ qua cache (không báo lỗi) khi đường dẫn quá dài;
  - dùng tiền tố `\\?\`;
  - rút ngắn tên mục cache.
- Nếu sửa trong `stage_cache.py` thì phải làm **trước G2**: file này nằm trong khóa của mọi stage, sửa sau G2 là đổi khóa lần hai.

## Đang dở

- (không có)

## Bước tiếp theo

1. Người dùng quyết có sửa lỗi đường dẫn dài ngay trong B1 không (xem phần trên).
2. Điều phối:
   - chạy full suite trên nhánh gộp;
   - gộp `perf/scan-b1` ở G2 cùng E3. Khóa cache đổi một lần, mọi stage quét lại một lần.
3. Mục 3 (Florence và DINO chung một tiến trình) để sau. Chưa bắt đầu B2–B4.

## Cách kiểm

- **Test liên quan** (`.venv` của thư mục cài, `PYTHONPATH` = `src` của worktree):
  - lệnh: `python -m unittest tests.test_stage_cache tests.test_cache_dependencies tests.test_cli tests.test_run_script_and_version tests.test_batch_prefetch tests.test_gore_triage tests.test_routing_prewarm tests.test_scene_cards tests.test_shot_cuts tests.test_job_pipeline tests.test_scheduler tests.test_review_workflow tests.test_install_paths tests.test_performance_report tests.test_control_center`;
  - kết quả: 426 test OK, bỏ qua 10 (2026-10-12, sau cổng; code không đổi kể từ 9f3f7f0).
- **Full suite:** điều phối chạy lúc gộp.
- **`input\synthetic-test.mp4`:** worktree có clip lavfi 1 s này (gitignored) cho `test_job_pipeline`. Giữ lại cho full suite; xóa khi bỏ worktree.
- **Chạy lại cổng:** làm theo `docs/evidence/perf-scan/README.md`. Script của cổng nằm trong scratchpad của session, không commit.

## Bước 1: đo khởi động (giữ lại để đối chiếu)

- **Nguồn số:**
  - DB thật, mở chỉ đọc (`mode=ro`);
  - report của job 125 (L, 44 phút, 220 MB) và job 114 (L, 56 phút, 312 MB), quét quảng cáo, "Tăng tốc xử lý".
- **Tách thời gian stage** (wall = trước bộ đếm + bộ đếm của report + sau bộ đếm):

  | Stage | Job 114: wall / bộ đếm / trước / sau (s) | Job 125 (s) |
  |---|---|---|
  | text | 148,6 / 133,6 / 7,6 / 7,4 | 140,9 / 108,1 / 22,2 / 10,6 |
  | visual_logo | 99,0 / 81,9 / 8,1 / 9,0 | 71,8 / 55,6 / 7,3 / 8,9 |
  | localize_logo (2 tiến trình) | 40,5 / 12,5 + 4,6 / 23,4 (cộng cả hai) | 38,1 / 20,1 + 0,0 / 18,0 |
  | build_review | 20,2 (không có bộ đếm) | 24,3 |

- **Import:** `import biliflow.cli` mất 7,3 s ở mỗi tiến trình `python -m biliflow`.
- **`prune()`:** duyệt toàn bộ `cache\stage-results` (241 mục, 45.726 file), mất 5,5 s mỗi lần. Nó chạy sau text, visual_logo và localize_logo.
- **Tính khóa cache:** 0,07–0,15 s, tính 3 lần mỗi stage.
- **Hash nguồn:** mỗi job quét quảng cáo hash 4 lần; 3 lần trong số đó chạy nền.
- **build_review:** khoảng 7 s import; 13–17 s còn lại là cắt khung hình bằng FFmpeg (nằm ngoài B1).
- **Danh sách B1 đã đề xuất:**

  | # | Mục | Quyết định |
  |---|---|---|
  | 1 | Nạp thư viện khi cần trong `cli.py` | Làm |
  | 2 | Hash nguồn một lần | Không làm |
  | 3 | Florence và DINO chung một tiến trình | Để sau |
  | 4 | Lọc file ngoài model khỏi khóa; chỉ tính model của stage | Làm |
  | 5 | Khóa cho bản build (C3) | Làm |
  | 6 | Phiên bản torch, CUDA và GPU trong khóa | Làm, phương án (b) |
  | 7 | `AbandonedMutexException`; một nguồn số phiên bản | Làm |
  | 8 | `prune()` đọc `bytes` trong manifest | Làm |
  | 9 | Tính khóa 1 lần thay vì 3 lần mỗi stage (cần sửa `scheduler.py`) | Không làm |
  | 10 | `install_root()` trong `cli.py` | Làm |

## Lưu ý cho người tiếp quản

- **Không sửa:** `final_renderer.py`, `blur_filter.py`, `export_encoder.py`, `hardware_*.py`, `models\`. `scheduler.py` chỉ sửa khi bắt buộc, và báo người dùng trước. B1 không đụng `scheduler.py`.
- **`prune()` và `prune_file_caches()` xóa file:** không bao giờ gọi trên thư mục cài khi đo.
- **Không ghi vào repo** tên phim, tên file nguồn hay link. Tên thư mục report có chứa tên phim.
