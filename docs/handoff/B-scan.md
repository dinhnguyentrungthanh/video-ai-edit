# Bàn giao B – quét nhanh hơn (đợt B1)
- Nhánh: `perf/scan-b1` (từ `origin/main` e534c12) · Worktree: `temp\wt-scan-speed` · Cập nhật: 2026-10-12
- Chạy ở: PC · Trạng thái: **chờ người dùng duyệt danh sách B1** (bước 1 xong, chưa sửa code)

## Đã xong
- **Bước 1: đo khởi động** (chỉ đọc; không ghi vào `input\`, `output\`, `reports\`, `archive\` thật; không dùng GPU).
  - Nguồn số: DB thật (`mode=ro`), report của job 125 (L, 44 phút, 220 MB) và job 114 (L, 56 phút, 312 MB), quét quảng cáo, "Tăng tốc xử lý". Đo import bằng `.venv` của thư mục cài với `PYTHONPATH` = `src` của worktree.
  - **Tách thời gian stage** (wall = trước bộ đếm + bộ đếm của report + sau):

    | Stage | Job 114: wall / bộ đếm / trước / sau (s) | Job 125 (s) |
    |---|---|---|
    | text | 148,6 / 133,6 / 7,6 / 7,4 | 140,9 / 108,1 / 22,2 / 10,6 |
    | visual_logo | 99,0 / 81,9 / 8,1 / 9,0 | 71,8 / 55,6 / 7,3 / 8,9 |
    | localize_logo (2 tiến trình) | 40,5 / 12,5 + 4,6 / 23,4 (cộng cả hai) | 38,1 / 20,1 + 0,0 / 18,0 |
    | build_review | 20,2 (không có bộ đếm) | 24,3 |

    Job 125 chạy sau lúc máy nghỉ: thư viện chưa nằm trong bộ nhớ đệm của Windows nên phần "trước" của text dài hơn (22 s, so với 7,6 s).
  - **Import:** `import biliflow.cli` mất 7,3 s ở mỗi tiến trình `python -m biliflow` (`timm` 3,9 s qua `animation_safety_scanner`, `scanner` 2,1 s). Phần stage thật sự cần: text (`easyocr` + `transformers`) 5,6 s; logo và grounding (`torch` + `transformers`) 3,4–3,5 s; `review_workflow` 0,4 s. PowerShell + `env.ps1` khoảng 0,25 s.
  - **Dọn cache sau mỗi stage:** `StageArtifactCache.prune()` duyệt toàn bộ `cache\stage-results` (241 mục, 45.726 file, 0,92 GB) mất 5,5 s mỗi lần (máy nóng). Nó chạy sau text, visual_logo và localize_logo. Dấu thời gian (manifest cache → hết stage) cho 4,7–6,7 s mỗi lần, khoảng **18 s mỗi job**, và tăng theo cỡ cache (giới hạn 10 GB).
  - **Khóa cache:** `_tree_fingerprint` mất 0,07–0,15 s (0,9 s khi máy nguội), tính 3 lần mỗi stage. `models\` có 137 file, 116 file nhỏ được hash.
  - **Hash nguồn:** SHA-256 khoảng 430 MB/s, tức 0,5–0,7 s cho mỗi nguồn trên. Mỗi job quét quảng cáo hash 4 lần: preflight (đồng bộ) và 3 lần chạy nền, song song với việc quét (text, tiến trình làm nóng routing, visual_logo).
  - **Làm nóng routing logo** xong trước OCR (job 125: 38 s trước), nên không nằm trên đường găng.
  - **build_review:** khoảng 7 s import, còn 13–17 s là việc riêng của nó (cắt khung hình bằng FFmpeg cho bộ nhớ nền tảng/studio). Phần này nằm ngoài B1.

## Danh sách B1 đề xuất (chờ duyệt)
Ước tính cho một job quét quảng cáo, máy đã nóng. Mọi mục có "khóa đổi" gộp vào **một** lần đổi khóa cache duy nhất.

| # | Mục | Tiết kiệm/job | Khóa cache đổi | Đề xuất |
|---|---|---|---|---|
| 1 | Nạp thư viện khi cần trong `cli.py` | ~16 s (text 1,9; logo 3,9; grounding 3,9; build_review 6,9) | Có (`cli.py` nằm trong khóa mọi stage) | Làm |
| 8 (mới) | `prune()` đọc `bytes` trong manifest thay vì duyệt từng file | ~17–18 s, tăng theo cỡ cache | Có (`stage_cache.py`) | Làm |
| 3 | Florence và DINO chung một tiến trình | ~4–5 s (sau mục 1) | Có (lệnh của `localize_logo`) | Làm, cổng giống hệt |
| 2 | Hash nguồn một lần | 0–0,7 s (3/4 lần đã chạy nền) | Có (cờ CLI) | Không làm ở B1; để B4 |
| 4 | Khóa bỏ `__pycache__`, `*.pyc`, `.cache`, `.gitkeep`; chỉ tính model của stage | ~0 s; hết mất cache vô cớ | Có | Làm |
| 5 | C3: khóa cho bản build, từ chối an toàn | 0 | Có | Làm |
| 6 | GPU/driver/torch vào khóa | 0 | Có | **Người dùng chọn** |
| 7 | `run.ps1` bắt `AbandonedMutexException`; một nguồn số phiên bản | 0 | Có | Làm |
| 9 (thêm) | Tính khóa 1 lần thay vì 3 lần mỗi stage | ~1 s | Cần sửa `scheduler.py` | Không làm |
| 10 (kế hoạch B1 mục 2) | `cli.py:47` `parents[2]` → `install_root()` | 0 | Có | Làm (cho C4) |

Tổng mục 1 + 8 + 3: khoảng 37–39 s/job trên 275–310 s, tức **khoảng 12–14%**. Job đầu sau lúc máy nghỉ được lợi thêm (import nguội).

## Đang dở
- (không có)

## Bước tiếp theo
1. Chờ người dùng duyệt danh sách và chọn mục 6.
2. Bước 2: sửa theo danh sách → test liên quan → commit.
3. Cổng: root tạm dưới `temp\`, bản sao nguồn của job 125 và 114; so `text-scan.json`, `scan.json`, `scan-florence.json`, `review-queue.json` với bản trước B1 (bỏ trường thời gian/hiệu năng); đo lại từng stage. Lấy khóa GPU, chỉ chạy khi Control Center thật không quét.

## Cách kiểm
- Lệnh test: `.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v` (thư mục cài, `PYTHONPATH` = `src` của worktree); bước 2 chỉ chạy test liên quan, full suite để điều phối chạy lúc gộp.
- Cổng của bước 2: 4 file trên giống hệt trừ thời gian/hiệu năng; số đo từng stage thấp hơn số nền ở trên.

## Chờ người dùng
- Duyệt danh sách B1 (mục nào làm).
- Mục 6: (a) không đưa vào; (b) phiên bản torch + CUDA của torch + tên GPU + compute capability, không driver; (c) như (b) và thêm driver (mỗi lần cập nhật driver là quét lại toàn bộ).

## Lưu ý cho người tiếp quản
- Không sửa: `final_renderer.py`, `blur_filter.py`, `export_encoder.py`, `hardware_*.py`, `models\`. `scheduler.py` chỉ khi bắt buộc và báo trước (danh sách trên không cần).
- Khóa cache chỉ đổi một lần, trong B1; vào `main` ở G2 cùng E3.
- `prune()` và `prune_file_caches()` xóa file: không bao giờ gọi trên thư mục cài khi đo.
- Không ghi tên phim, tên file nguồn hay link vào repo (tên thư mục report chứa tên phim).
