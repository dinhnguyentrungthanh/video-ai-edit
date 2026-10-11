# Bàn giao E – Hồ sơ phần cứng
- Nhánh: `feat/hardware-profile` (từ `origin/main` `40a0125`, không theo dõi nhánh từ xa nào) · Commit cuối: xem `git log -1` · Cập nhật: 2026-10-11
- Chạy ở: PC (`E:\DungChung\BiliFlow\temp\wt-hardware-profile`) · Trạng thái: **E0 đã duyệt (2026-10-11); tiếp theo E1**

## Đã xong
- **Chuẩn bị:**
  - `git fetch origin` (thư mục chính đứng sau `origin/main` 4 commit);
  - `git worktree add temp\wt-hardware-profile -b feat/hardware-profile origin/main`;
  - `git branch --unset-upstream`, để `git push` không bao giờ nhắm vào `main`.
- **E0: bản đề xuất hợp đồng ở `docs/HARDWARE_PROFILE.md`.** Gồm:
  - schema `state\hardware-profile.json`;
  - API `choose`/`resolve`/`speed_arguments`/đóng băng;
  - bảng cấu hình đã qua cổng;
  - thứ tự quyết định và mã lỗi;
  - route, trang Cài đặt và "Đo lại";
  - file, test và câu hỏi.
- **Cách làm:**
  - sáu agent đọc code (chỉ đọc), ba thiết kế độc lập, một bản gộp;
  - một lượt review đối kháng theo bốn góc (trích dẫn, an toàn, dòng lệnh giống từng byte, khả năng cài đặt), mỗi phát hiện có một bước kiểm chứng riêng;
  - kết quả: 66 phát hiện, 61 được xác nhận và đã sửa vào tài liệu, 5 bị bác.
- **Sửa chính sau review:**
  - `had_cuda` chỉ đi một chiều. Gỡ driver rồi đo lại không bao giờ biến máy thành "máy CPU";
  - danh sách card lấy mã hãng PCI (`VEN_10DE`), không suy từ tên;
  - quy tắc viết dòng lệnh (`speed_arguments`, `--logo-decode` luôn ghi rõ) và `LEGACY_KNOBS` cho job cũ;
  - thứ tự quyết định của `resolve()`, kèm bảng mã lỗi;
  - lệnh đo chỉ nhận root mà `allowed_project_root` cho phép;
  - bỏ chuỗi phiên bản FFmpeg chứa tên miền: hồ sơ chỉ giữ số phiên bản và SHA-256;
  - câu hỏi 2 đổi đề xuất thành "từ chối", vì kế hoạch chỉ bật cách chạy đã qua cổng.
- **Người dùng duyệt E0 ngày 2026-10-11:** "Duyệt E0. Theo đề xuất: 1 a+b, 2 từ chối, 3 từ chối, 4 tự tạm dừng, 5 từ chối." Quyết định và hệ quả ghi ở mục 10 của `docs/HARDWARE_PROFILE.md`.
- Chưa có code và chưa chạy test: chưa có gì để test.

## Đang dở
- (trống)

## Bước tiếp theo
1. **E1:**
   - `hardware_profile.py`: schema, `resolve`/`choose`, `speed_arguments`, `LEGACY_KNOBS`, `freeze_value`/`parse_frozen`;
   - `hardware_table.py`: hash `ffmpeg.exe` cho `nvdec_verified`, đọc ở thư mục cài, chỉ đọc;
   - `hardware_detect.py`: `detect()`, `observe()`;
   - test (giả lập, không GPU), rồi commit.
2. **E2:**
   - `hardware_measure.py`, `hardware_manager.py`, `scripts/hardware-measure.ps1`;
   - route, `phone_access`, `dashboard_v2/hardware-view.js` (chỉ nạp trong `live.html`);
   - test;
   - Control Center thử ở cổng 8798;
   - một lần đo thật ở khung GPU 3, khi người dùng đã tạm dừng hàng đợi thật.

   Xong thì commit, báo người dùng và chờ G1.

## Cách kiểm
- **Lệnh test:** `E:\DungChung\BiliFlow\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_hardware_*.py" -v`, với `PYTHONPATH=<worktree>\src`.
- **Trước khi bàn giao:** chạy cả bộ, với một clip tổng hợp 1 s trong `input\` của worktree; xóa clip sau đó.
- **Cổng của E1–E2:**
  - không khóa cache stage nào đổi: `stage_source_paths` của 10 stage giống hệt ở `40a0125` và HEAD; `tests.test_cache_dependencies` qua;
  - dòng lệnh không đổi: test so nguyên bộ argv (mục 8 ý 4 của tài liệu), và các test hiện có ghim dòng lệnh vẫn qua;
  - import sạch: `control_center` không nạp `hardware_*` lúc import;
  - không có `f"hw:{job_id}"` trong `src`;
  - các route chỉ-PC bị từ chối khi gọi từ điện thoại.

## Chờ người dùng
- Ở bước E2: chọn giờ cho khung GPU 3 và tạm dừng hàng đợi thật (agent không bao giờ POST `/api/scheduler`).
- Trước Đợt 2 (không thuộc E0–E2): chạy lại cổng framemd5 cả phim L ở khung GPU 3 (câu hỏi 1, phương án b; người dùng đã cho phép đọc bản L cho lần chạy đó).

## Lưu ý cho người tiếp quản
- **Không sửa:**
  - danh sách của người dùng: `scheduler.py`, `job_store.py`, `job_pipeline.py`, module quét, `cli.py`, `__main__.py`, `__init__.py`, `run.ps1`, `env.ps1`, `stage_cache.py`, `cache_dependencies.py`, `pyproject.toml`, `config/*`, `models\`;
  - thêm theo đề xuất:
    - `nvdec.py`, `final_renderer.py` và `resource_lock.py` (của A);
    - `job_purge.py` và `recycle_bin.py` (của C2; chỉ import);
    - `AGENTS.md` (của C0);
    - `dashboard_v2/index.html` và `serve.py`.
- **`hw:{id}` chưa được ghi.** `JOB_SETTING_NAMES` (`job_store.py:148`) là tuple cố định, và `tests/test_job_purge.py:640-649` dò mọi `f"<name>:{job_id}"` trong `src`. Không bao giờ né test đó.
- **Worktree không có** `.venv`, `tools`, `state`, `models`.
  - Dùng `.venv` của thư mục cài.
  - Với `.ps1`, truyền `-Python` và `-ProjectRoot`.
  - Trước lần đo thật, chép `ffmpeg.exe` và `ffprobe.exe` của thư mục cài vào `<worktree>\tools\ffmpeg\bin` (đã gitignore).
- **Test không bao giờ giữ tên mutex thật** (`Local\BiliFlowGpuInference`, `Local\BiliFlowFinalRender`). Dùng `Local\BiliFlowTest-<uuid>`, truyền bằng tham số hàm, không bao giờ qua dòng lệnh hay biến môi trường.
- **Control Center thử:**
  - lệnh: `python -m biliflow.control_entry --project-root <worktree>\temp\hw-cc-* --port 8798 --no-import-existing`, giống `Start-BiliFlow.ps1`. Không chạy qua `cli.py`, vì như vậy sẽ kéo torch vào;
  - dừng bằng PID đang nghe ở cổng 8798, **không** bằng `Stop-BiliFlow.ps1` (script đó dừng bản thật).
- **Cho C0, trước G1:**
  - đưa các route `/api/hardware-profile*` vào danh sách "agent không bao giờ gọi";
  - ghi rõ: agent không bao giờ chạy `scripts/hardware-measure.ps1` hay `python -m biliflow.hardware_profile measure` với `-ProjectRoot` là thư mục cài.
- **Phát hiện cho session khác** (chi tiết ở mục 11 của `docs/HARDWARE_PROFILE.md`):
  - **E3:** ở họ `standard` (OCR 1, fp32), stage text âm thầm chạy CPU khi CUDA lỗi. `standard_ocr8` và `fast` thì báo lỗi rõ;
  - **B1:**
    - khóa cache thiếu GPU, driver và torch;
    - số worker và cách giải mã đã nằm ngoài khóa cache routing;
    - `run.ps1` không bắt `AbandonedMutexException`;
    - `uv.lock` không có ở thư mục cài;
    - phiên bản 0.7.24 và 0.7.19 lệch nhau;
  - **E4:** cache ảnh DINO/SigLIP không tính thiết bị;
  - **tài liệu:** hai trích dẫn sai trong `PERF_INSTALL_PLAN.md` và byte 0x08 ở `SCAN_PERFORMANCE.md:140-143`.
