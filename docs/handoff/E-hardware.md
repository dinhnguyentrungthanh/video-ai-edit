# Bàn giao E – Hồ sơ phần cứng
- Nhánh: `feat/hardware-profile` (từ `origin/main` `40a0125`, không theo dõi nhánh từ xa nào) · Commit cuối: xem `git log -1` · Cập nhật: 2026-10-11
- Chạy ở: PC (`E:\DungChung\BiliFlow\temp\wt-hardware-profile`) · Trạng thái: **E0 đã duyệt, phương án D đã chọn, E1 xong (2026-10-11); tiếp theo E2**

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
- **E0 được người dùng duyệt ngày 2026-10-11.**
  - Lời duyệt: "Duyệt E0. Theo đề xuất: 1 a+b, 2 từ chối, 3 từ chối, 4 tự tạm dừng, 5 từ chối."
  - Commit: `958395c` (bản đề xuất), `e0549df` (ghi quyết định). File: `docs/HARDWARE_PROFILE.md`; quyết định và hệ quả ở mục 10.
  - Các đề xuất người dùng đã đồng ý:
    1. NVDEC trên driver 617.42: phép tự kiểm framemd5 của E2 làm cổng cho từng driver; trước Đợt 2, chạy lại cổng framemd5 cả phim L ở khung GPU 3 (người dùng đã cho phép đọc bản L cho lần đó).
    2. Dòng cấu hình chưa qua cổng: từ chối trên thư mục cài thật; chỉ chạy trên root thử.
    3. Chưa có hồ sơ khi Đợt 2 bật: từ chối job mới tới lần "Đo lại" đầu.
    4. "Đo lại": người dùng tự tạm dừng hàng đợi; nút bị khóa kèm lý do, không có nút tự tạm dừng.
    5. Máy nhiều GPU NVIDIA: từ chối.
  - Kèm theo hợp đồng:
    - `hw:{id}` chỉ ghi từ Đợt 2;
    - các route mới và lệnh đo vào danh sách "agent không bao giờ gọi/chạy" qua C0;
    - công tắc "Chỉ dùng CPU" ghi ngay trong E2, có hiệu lực cho job mới từ Đợt 2.
- **Đối chiếu E0 với kế hoạch (người dùng hỏi ngày 2026-10-11).** Mọi điểm khớp, trừ một: card đời chưa đo. Người dùng quyết định **phương án D ngày 2026-10-11** (sửa từ C). Nguyên văn và hệ quả ở mục 10 của `docs/HARDWARE_PROFILE.md`; quy tắc ở mục 5.6. Tóm tắt:
  - **Card chưa đo mà torch có mã:** không từ chối, kể cả trên thư mục cài thật.
    - Chạy chế độ chuẩn: họ `standard`, OCR batch 1, không fp16, tắt TF32 cho matmul và cuDNN.
    - Nhãn "Chưa qua cổng trên đời card này" hiện ở hồ sơ, trang Cài đặt và thẻ duyệt.
  - **Chế độ nhanh fp16** (họ Turing) trên card chưa đo: chỉ khi người dùng bật một công tắc riêng cho root trên PC (mặc định tắt). Hộp xác nhận nêu ba rủi ro.
  - **Từ chối kèm lý do cụ thể:** torch không có mã cho đời card (`ARCH_UNSUPPORTED`); driver thấp hơn mức NVIDIA ghi cho CUDA của bản torch (`DRIVER_TOO_OLD`, cu126 cần ≥ 561.17).
  - **Số worker, số làn:** chưa tăng; để sau B1 và A1b.
  - **Các lệch ở mục 11 của E0:** người dùng đồng ý cả.
  - **TF32 trên torch 2.14:** tắt bằng `torch.backends.fp32_precision = "ieee"`.
    - Đã thử: không trộn với `allow_tf32` cũ, vì sau đó đọc `cudnn.allow_tf32` sẽ ném lỗi.
    - Cần cờ `cli.py` của Đợt 2 (B1). Trước đó `speed_arguments` báo lỗi rõ.
- **Đọc kế hoạch mới trên `main`** (`7d8b8bc`, `2d8240f`, `34df52c`, chưa merge):
  - nhận diện theo khả năng: `nvidia-smi … compute_cap` để đối chiếu;
  - encode/decode thử thật cho H.264, HEVC, AV1;
  - mục 12.6: E3 làm trên nhánh xếp chồng `feat/hardware-profile-e3` sau khi E2 đạt cổng.

  Hợp đồng đã cập nhật theo các điểm này.

- **E1 xong (2026-10-11), theo phương án D.** Module (không module nào nằm trong closure stage; khóa cache của 10 stage giống hệt `40a0125`):
  - `hardware_schema.py`: lớp file, schema mục 3.2–3.6, `classify_attempt`, bộ làm sạch chuỗi lỗi;
  - `hardware_profile.py`: kiểu, `choose`, `speed_arguments`, `LEGACY_KNOBS`, `freeze_value`/`parse_frozen`;
  - `hardware_resolve.py`: `resolve()` tách riêng (thứ tự quyết định mục 7.1, đường chưa qua cổng mục 5.6, bước 4 đổi lần đo lỗi của card không có mã torch thành `arch_unsupported`/`driver_too_old`);
  - `hardware_table.py`: dòng 1 Turing `gated`, dòng CPU `ungated` (`runnable_after = "E3"`), `nvidia-untested` và `nvidia-untested-fast`, `CUDA_DRIVER_MIN = {"cu126": "561.17"}`;
  - `hardware_detect.py` và `hardware_system.py` (lớp truy cập Windows): `detect()`, `observe()`, tiến trình cháu `torch-probe`.
  - **Test:** 191 test `test_hardware_*` (gồm phép so nguyên bộ argv trên `pipeline_stages` thật, lưới `resolve()` 256 root × 12 quan sát, hai test đầu-cuối detect → hồ sơ → resolve cho RTX 2060 và cho một card Ampere giả), cộng `test_cache_dependencies` và `test_job_purge`: qua. Tiến trình torch thật chưa chạy lần nào (theo luật); lần chạy đầu là phép đo E2.
  - **Cả bộ test** (clip tổng hợp 1 s trong `input\` của worktree, đã xóa): 2838 test, 35 bỏ qua. Ba module E2E tài khoản nguồn lỗi ở `setUpModule` vì TEMP/TMP của shell nằm ngoài thư mục cài (lỗi môi trường, không do E1). Chạy lại với `TEMP=TMP=E:\DungChung\BiliFlow\temp`: 12 test qua.
  - **Cách làm:** workflow (cài song song, review bốn góc mỗi nhóm, mỗi phát hiện một bước kiểm chứng, sửa, tích hợp): 42 phát hiện, 20 được xác nhận và đã sửa; tài liệu được chỉnh theo code (mục 3.2, 4.1, 4.2, 6.3, 7.1, 8, 9).

## Đang dở
- (trống)

## Bước tiếp theo
1. **E2:**
   - `hardware_measure.py`, `hardware_manager.py`, `scripts/hardware-measure.ps1`;
   - route, `phone_access`, `dashboard_v2/hardware-view.js` (chỉ nạp trong `live.html`);
   - test;
   - Control Center thử ở cổng 8798;
   - một lần đo thật ở khung GPU 3, khi người dùng đã tạm dừng hàng đợi thật.

   Xong thì commit, báo người dùng và chờ G1.

## Cách kiểm
- **Lệnh test:** `E:\DungChung\BiliFlow\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_hardware_*.py" -v`, với `PYTHONPATH=<worktree>\src`.
- **Trước khi bàn giao:** chạy cả bộ, với một clip tổng hợp 1 s trong `input\` của worktree; xóa clip sau đó. Đặt `TEMP` và `TMP` là `E:\DungChung\BiliFlow\temp` (như `env.ps1`), nếu không ba module E2E tài khoản nguồn từ chối chạy.
- **Cổng của E1–E2:**
  - không khóa cache stage nào đổi: `stage_source_paths` của 10 stage giống hệt ở `40a0125` và HEAD; `tests.test_cache_dependencies` qua;
  - dòng lệnh không đổi: test so nguyên bộ argv (mục 8 ý 4 của tài liệu), và các test hiện có ghim dòng lệnh vẫn qua;
  - import sạch: `control_center` không nạp `hardware_*` lúc import;
  - không có `f"hw:{job_id}"` trong `src`;
  - các route chỉ-PC bị từ chối khi gọi từ điện thoại.

## Chờ người dùng
- **Xác nhận bốn cách hiểu của E về phương án D** (mục 10 của `docs/HARDWARE_PROFILE.md`; đã làm theo, đổi lại được dễ):
  1. D áp cho cả bản torch và driver chưa qua cổng trên card Turing;
  2. thêm từ chối `VRAM_TOO_SMALL` khi VRAM nhỏ hơn 6144 MiB;
  3. khi công tắc có hiệu lực, cả ba họ Turing đều tắt TF32;
  4. công tắc gắn với card lúc bật.
- **Hai câu nhỏ về nhãn và lời từ chối** (đã làm theo hợp đồng, đổi được):
  - card Turing chỉ khác bản torch hay driver cũng mang nhãn "Chưa qua cổng trên đời card này (sm_75)", vì mục 5.6 ghi "mọi lựa chọn trên đường này";
  - lần đo lỗi với bản torch chưa có trong `CUDA_DRIVER_MIN` giữ `cuda_unavailable` ở bước 4; bước 8 thì từ chối `driver_too_old` ("chưa biết driver tối thiểu").
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
- **Cho E2, từ bước tích hợp E1:**
  - manager gọi `observe()` với đúng `detection.ffmpeg.path` của hồ sơ, nếu không `ffmpeg_changed` sẽ báo sai;
  - `warnings` của GET ghép từ `Resolution.warnings`, `Resolution.caveats` và `FamilyOption.warnings`, bỏ trùng (`untested_fast_on` có ở cả hai chỗ đầu; `c1_off_fp32` chỉ nằm trong họ);
  - ghi chú chỉ-đọc cho E2 nằm trong scratchpad của session (`e2\read-*.md`), không nằm trong repo; session khác phải đọc lại code.
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
