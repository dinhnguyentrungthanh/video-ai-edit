# Bàn giao B – quét nhanh hơn (đợt B1)

- **Nhánh:** `perf/scan-b1` (tách từ `origin/main` e534c12).
- **Worktree:** `temp\wt-scan-speed`.
- **Commit:**
  - a282e47: bước 1;
  - 9f3f7f0: code B1;
  - 9b2d637: cổng và số đo;
  - commit chứa file này: sửa lỗi đường dẫn dài.
- **Cập nhật:** 2026-10-12.
- **Chạy ở:** PC.
- **Trạng thái:** bước 2 xong, kèm phần sửa lỗi đường dẫn dài. Chờ điều phối gộp ở G2 cùng E3. Chưa push, chưa merge.

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
- **Sửa lỗi đường dẫn dài trong B1** (duyệt sau bước 2, cách 1):
  - đường dẫn của mục cache (kể cả thư mục `.tmp`) vượt giới hạn thì bỏ qua lưu cache cho mục đó;
  - ghi cảnh báo rõ vào log của stage, không im lặng;
  - stage và job vẫn hoàn tất bình thường;
  - không đổi khóa hay bố cục cache ngoài thay đổi B1 đã có;
  - người dùng cho sửa `scheduler.py` cho việc này (đã báo trước).
- **Lúc làm phần sửa này:** Control Center thật đang xuất video. Không chạy cổng quét, không đo, không dùng GPU, không chạy full suite; chỉ chạy test liên quan.

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
- **Số đo từng stage** (wall, giây). Cả hai bản đều tắt cache, xem phần "Lỗi đường dẫn dài". Mỗi ô ghi vòng 1 / vòng 2:

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

### Sửa lỗi đường dẫn dài (commit chứa file này)

- **`stage_cache.py`:**
  - `store()` gọi `_check_entry_paths()` trước khi ghi bất cứ thứ gì. Hàm này kiểm mọi thư mục và file sẽ ghi dưới thư mục `.tmp` của mục (dạng dài nhất của mục), cùng `manifest.json`;
  - giới hạn: thư mục 247 ký tự, file 259 ký tự. Hai số này đo trên máy này với LongPathsEnabled=0: thư mục 248 ký tự lỗi WinError 206, file 260 ký tự lỗi;
  - vượt giới hạn thì ném `StageCachePathTooLong` (lớp con của OSError, mang `path` và `limit`) và không tạo thư mục nào;
  - bỏ qua phần kiểm khi tiến trình được dùng đường dẫn dài (`RtlAreLongPathsEnabled()`), khi chạy ngoài Windows, hoặc khi đường dẫn có tiền tố `\\?\`.
- **`scheduler.py`:**
  - bắt đúng `StageCachePathTooLong` quanh lệnh `store()`;
  - ghi một dòng `WARNING: Stage <tên> finished but was not saved to the stage cache: …` cùng đường dẫn vào log của stage;
  - thêm sự kiện `STAGE_CACHE_SKIPPED` mức `WARN`, payload `{stage, path_characters, limit}`;
  - không chạy prune. Stage vẫn COMPLETED;
  - lỗi khác của `store()` vẫn làm stage hỏng như trước.
- **Khóa cache:**
  - không đổi đầu vào hay bố cục: vẫn là thay đổi một lần của B1;
  - `stage_cache.py` nằm trong khóa của mọi stage, nhưng B1 chưa vào `main`, nên so với `main` khóa vẫn chỉ đổi một lần;
  - `scheduler.py` không nằm trong khóa của stage nào (đã kiểm bằng `stage_source_paths`).
- **Test:**
  - `test_stage_cache`:
    - giới hạn đặt thấp hơn 1 ký tự so với đường dẫn dài nhất trong `.tmp`, cho thư mục và cho file: ném lỗi, đúng `path`, không có gì trong `cache\stage-results`;
    - đúng bằng giới hạn: lưu được, không còn `.tmp`;
    - long paths bật thì không giới hạn.
  - `test_scheduler`:
    - root tạm dài 140 ký tự: stage `adult` chạy tiến trình thật, rồi COMPLETED, job không FAILED;
    - log có dòng WARNING, có một sự kiện `STAGE_CACHE_SKIPPED` mức `WARN`, không có STAGE_CACHE_STORED hay STAGE_FAILED;
    - `cache\stage-results` trống;
    - tắt phần kiểm (`_check_entry_paths` làm rỗng) thì test này hỏng: stage không COMPLETED.

### Lỗi đường dẫn dài (phát hiện sau cổng, đã sửa ở trên)

Lỗi này có từ trước B1; B1 không đổi đường dẫn nào. Phần dưới mô tả tình trạng trước khi sửa.

- Máy đang để `LongPathsEnabled=0`; tôi chỉ đọc giá trị này, không đổi.
- Đường dẫn dài nhất trong `cache\stage-results` ở thư mục cài là 253 ký tự. Lúc lưu, thư mục `.tmp` làm nó thành 257.
- Nếu thư mục cài dài hơn `E:\DungChung\BiliFlow` (21 ký tự) khoảng 3 ký tự, `StageArtifactCache.store()` ném OSError.
- Scheduler không bắt riêng lỗi này. Stage đã quét xong vẫn thành FAILED_RETRYABLE và job thành FAILED; chạy lại cũng hỏng như cũ.
- Việc này liên quan C, vì bộ cài cho người dùng chọn thư mục.
- Người dùng chọn bỏ qua cache kèm cảnh báo. Hai hướng khác (tiền tố `\\?\`, rút ngắn tên mục cache) không làm.
- **Sau khi sửa:** ở thư mục cài dài, các stage chạy được nhưng không dùng cache cho mục quá dài. Mỗi lần như vậy log và sự kiện ghi rõ, và stage đó sẽ chạy lại thay vì lấy từ cache.

## Đang dở

- (không có)

## Bước tiếp theo

1. Điều phối:
   - chạy full suite trên nhánh gộp;
   - gộp `perf/scan-b1` ở G2 cùng E3. Khóa cache đổi một lần, mọi stage quét lại một lần.
2. Mục 3 (Florence và DINO chung một tiến trình) để sau. Chưa bắt đầu B2–B4.

## Cách kiểm

- **Test liên quan** (`.venv` của thư mục cài, `PYTHONPATH` = `src` của worktree):
  - lệnh: `python -m unittest tests.test_stage_cache tests.test_cache_dependencies tests.test_cli tests.test_run_script_and_version tests.test_batch_prefetch tests.test_gore_triage tests.test_routing_prewarm tests.test_scene_cards tests.test_shot_cuts tests.test_job_pipeline tests.test_scheduler tests.test_review_workflow tests.test_install_paths tests.test_performance_report tests.test_control_center`;
  - kết quả: 426 test OK, bỏ qua 10 (2026-10-12, sau cổng, trên 9f3f7f0).
- **Sau phần sửa đường dẫn dài:**
  - lệnh: `python -m unittest tests.test_stage_cache tests.test_scheduler tests.test_cache_dependencies tests.test_job_ocr_option tests.test_job_pipeline`;
  - kết quả: 125 test OK;
  - lúc đó Control Center thật đang xuất video, nên không chạy bộ 15 module ở trên hay full suite.
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

# Đợt OCR (stage text)

- **Nhánh:** `perf/scan-ocr`, tách từ `origin/main` 9ae104c (B1 đã gộp và push). Worktree: `temp\wt-scan-speed`.
- **Trạng thái:** bước 1 (đo, chưa sửa code) **xong** ngày 2026-10-13. Danh sách đã trình, **đang chờ người dùng duyệt**; chưa sửa code.
  - Bước 1 từng tạm dừng theo yêu cầu người dùng ngày 2026-10-12, khoảng 23:05, vì máy có ứng dụng khác làm nhiễu số đo.
  - Người dùng cho đo tiếp ngày 2026-10-13. Phần đo lại chạy từ 06:57 đến 08:11.

## Cách đo

- **Root tạm:** `temp\b-ocr\root`, giữ lại để đo tiếp (7,5 GB). Gồm code của 9ae104c, bản sao models, tools, HF cache, và nguồn job 125 và 114 (đã kiểm SHA-256).
- **Kết quả:** nằm ở `temp\b-ocr\results\<job>-<biến thể>`.
- **Script đo:** `temp\b-ocr\harness\`, không commit:
  - `ocr_probe.py setup|run <job> <biến thể>|cleanup`;
  - `ocr_probe_inner.py`;
  - `ocr_analyze.py`.
- **Lệnh chạy:** đúng argv production của stage text ở chế độ "Tăng tốc xử lý": batch 8, cửa sổ 4 khung, detect fp16, kèm tiến trình con làm nóng routing logo bằng NVDEC.
- **Điều kiện chạy:**
  - chỉ chạy khi DB thật (`mode=ro`) không có stage RUNNING nào, kể cả xuất;
  - giữ mutex `Local\BiliFlowGpuInference`;
  - ghi mẫu GPU bằng `nvidia-smi` và mẫu CPU của cây tiến trình;
  - kể từ lần chạy `125-probe-gpupre` trở đi, ghi thêm CPU của các tiến trình khác trên máy để nhận ra nhiễu.
- **Bộ đo (`probe`):** hook lúc import, không sửa file nào; có `cuda.synchronize` quanh forward. Bộ đo làm chậm khoảng 6 s mỗi lần chạy.

## Số đo đến lúc dừng

Mỗi lần chạy stage text, tính bằng giây. Job 125 (L, 889 khung), job 114 (L, 1.113 khung).

| Biến thể | 125 wall | 114 wall | Kết quả so với plain-a |
|---|---|---|---|
| plain-a / plain-b | 96,3 / 97,3 | 138,1 / 175,0 (\*) | giống hệt |
| prefetch 2 khung | 96,2 | 133,7 | giống hệt; không nhanh hơn |
| cửa sổ 8 khung | 98,2 | 135,9 | **không giống hệt** (`tracks[].max_confidence`); không nhanh hơn |
| NVDEC cho OCR | 106,7 | 163,2 | giống hệt; chậm hơn 10–18 % |
| không làm nóng routing (chỉ tham khảo) | 82,0 | 139,8 | giống hệt |

(\*) Lần chạy bị nhiễu: các ứng dụng khác của người dùng chiếm CPU.

**Tách thời gian** (lần `probe`; 125 / 114):

- **detect:** tổng 63,0 / 94,8. Trong đó:
  - forward GPU 37,1 / 56,6;
  - normalize trên CPU 12,7 / 17,5;
  - resize/pad trên CPU 3,4 / 4,6;
  - hậu xử lý 2,0 / 5,4;
  - phần còn lại khoảng 7,7 / 10,3 (chuyển dữ liệu, chép mảng).
- **recognize:** 12,3 / 17,1 (forward GPU 8,7 / 12,2).
- **Mô hình ngữ nghĩa:** nạp 7,2 / 5,8; phân loại khoảng 0,1.
- **`model_load`:** 11,7 / 8,2 (import cộng `Reader` 3,0–3,4).
- **Chờ khung:** 1,9 / 2,9.
- **Chờ tiến trình làm nóng routing:** 0.
- **Còn lại:** tracking và preview, mỗi phần khoảng 0,3.
- **Máy:**
  - GPU bận trung bình chỉ 42–48 %, không lúc nào chạm 90 %;
  - FFmpeg giải mã bằng CPU tốn 174–262 giây CPU, tiến trình làm nóng routing 125–160;
  - CPU 6 nhân bị chia giữa các tiến trình này.
- **Khung giống hệt khung trước:** không có ở cả hai job. Job 114 có 5 khung trùng với một khung nào đó trước đó.
- **Khung không có chữ:** 713/889 và 967/1.113.

**Thí nghiệm** (chỉ trong script đo, không sửa code BiliFlow):

- **`gpupre`:** dựng input của CRAFT trên GPU từ khung uint8. Resize cùng cỡ là bản chép (đã kiểm), đệm bằng số 0; phép trừ và chia làm ở float64 rồi làm tròn về float32.
  - Job 125: **889/889 khung có detect giống hệt** (cùng hash hộp chữ).
  - detect_total giảm từ 65,4 xuống 54,8 s. Nhưng lần chạy này bị nhiễu (CPU của tiến trình làm nóng routing tăng từ khoảng 138 lên 189 s), nên wall 116,8 s không dùng được.
- **Đo lại ngày 2026-10-13:**
  - các biến thể chạy xen kẽ theo vòng, thứ tự đảo giữa các vòng;
  - lấy trung vị;
  - mọi lần chạy đều có `contaminated False`.

| Biến thể | 125 trung vị (khoảng) | 114 trung vị (khoảng) | Giống hệt plain-a |
|---|---|---|---|
| plain (5 vòng) | 94,9 (92,0–116,1) | 127,0 (124,8–135,9) | 5/5, 5/5 |
| `gpupre` (5 vòng) | **81,4** (79,4–97,4) | **112,8** (108,8–113,9) | **5/5, 5/5** |
| `semimport` (2 vòng) | 92,3 (90,0–94,6) | 128,8 (125,2–132,4) | 2/2, 2/2 |
| `both2` = gpupre + semimport (2 vòng) | 80,3 (79,1–81,6) | 111,9 (110,8–113,1) | 2/2, 2/2 |
| `sembg` (3 vòng) | 92,8 | 123,1 | **0/3, 1/3** |
| `both` = gpupre + sembg (3 vòng) | 76,2; một lần **lỗi** | 105,0 | **0/2, 0/3** |

- **`gpupre` cho detect giống hệt từng khung:**
  - job 125: 889/889 khung, cùng điểm ảnh, cùng hash hộp chữ, cùng số crop;
  - job 114: 1.113/1.113 khung, so `probe` với `probe-gpupre`.
- **VRAM tối đa của `gpupre` tăng 20–24 MiB:** khoảng 1.075 so với 1.055 MiB ở job 125, 1.117 so với 1.093 MiB ở job 114.
- **`sembg` không an toàn:** nạp mô hình ngữ nghĩa ở luồng nền.
  - Lần `125-both-r1` lỗi `cudnn_batch_norm` (Float/Half), vì `from_pretrained(dtype=float16)` đổi kiểu số mặc định của torch cho cả tiến trình trong lúc OCR đang chạy.
  - Các lần không lỗi vẫn ra `semantic_scores` và `ad_probability` khác bản gốc. Lần `125-sembg-r3` đổi cả `routing` và `routing_counts`.
- **`semimport`:** chỉ import `transformers` ở luồng nền.
  - Thời gian nạp ngữ nghĩa giảm từ 5,85 xuống 3,70 s, kết quả giống hệt.
  - Nhưng wall chỉ khác 1–2 s, nằm trong nhiễu.

**Danh sách trình người dùng (2026-10-13):**

1. **`gpupre`: dựng input của CRAFT trên GPU.**
   - **Tiết kiệm:** stage text giảm 11–14 % (−13,5 s ở job 125, −14,2 s ở job 114); cả quét quảng cáo khoảng 5–6 %.
   - **Giống hệt:** có.
   - **Khóa cache:** chỉ đổi khóa của stage text. Code sẽ nằm trong `ocr_precision.py` hoặc `ocr_batch_experiment.py`; chỉ `textscan` import hai file này, còn `cli.py` là ranh giới.
   - Ngoài trường hợp đã đo, mọi trường hợp khác dùng lại `test_net` gốc: CPU, khung không phải uint8 3 kênh, resize khác cỡ, `estimate_num_chars`.
2. **`semimport`: import `transformers` trong lúc OCR chạy.** Tiết kiệm 1–2 s, trong nhiễu. Giống hệt. Chỉ đổi khóa của stage text. Đề xuất: **bỏ qua**, vì thêm một luồng mà lợi rất ít.
3. **Chạy chồng CPU và GPU (pipeline detect/recognize):**
   - chưa đo; ước đoán 5–10 %;
   - phải sửa vòng lặp OCR nhiều hơn, và phải chứng minh giống hệt;
   - chỉ đo nếu người dùng muốn.
- **Đã loại:**
  - `sembg` và `both`: không giống hệt, có lần lỗi;
  - cửa sổ 8 khung: không giống hệt;
  - NVDEC: chậm hơn 10–18 %;
  - prefetch: không nhanh hơn;
  - bỏ qua khung trùng: không có khung nào giống hệt khung trước;
  - bỏ làm nóng routing: chỉ dời việc sang stage visual_logo.

## Đang dở

- **Chờ người dùng duyệt danh sách trên.** Bước 2 chỉ làm các mục được duyệt.
- **Cổng kiểm tra:** `text-scan.json` và `review-queue.json` giống hệt bản trước, trừ các trường thời gian và hiệu năng đã liệt kê ở B1.
- **Root tạm `temp\b-ocr`:** giữ lại để làm cổng kiểm tra, xóa khi xong đợt.
