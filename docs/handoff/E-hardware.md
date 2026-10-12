# Bàn giao E – Hồ sơ phần cứng
- Nhánh: `feat/hardware-profile` (từ `origin/main` `40a0125`, không theo dõi nhánh từ xa nào) · Commit cuối: code E2 `aa73419`, sau đó là commit tài liệu ghi các duyệt ngày 2026-10-12 (xem `git log -1`) · Cập nhật: 2026-10-12 07:15
- Chạy ở: PC (`E:\DungChung\BiliFlow\temp\wt-hardware-profile`) · Trạng thái: **chờ người dùng**: E0–E2 xong và đã duyệt, trừ lần đo thật ở khung GPU 3 (chờ người dùng báo Control Center thật đã tắt); sau đó xong, chờ gộp G1

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

- **E2 xong (2026-10-12), trừ lần đo thật.** Module (mục 4.1 của `docs/HARDWARE_PROFILE.md`; không module nào nằm trong closure stage, khóa cache của 10 stage giống hệt `40a0125`):
  - lệnh đo: `hardware_measure.py` (các bước 2–9 của mục 6.3), `hardware_locks.py` (mutex, file khóa, thứ tự toàn cục), `hardware_idle.py` (kiểm rảnh, `mode=ro`), `hardware_tools.py` (tiến trình con, file hủy, ngân sách), `hardware_trials.py` (clip, encoder, `nvdec_codecs`, routing logo); `scripts/hardware-measure.ps1` (chỉ ASCII); `hardware_profile.py` chuyển `__main__` sang `hardware_measure.main`;
  - phía Control Center: `hardware_manager.py` (`can_remeasure`, "Đo lại", giới hạn 15 phút rồi file hủy, 60 s ân hạn, `taskkill /T /F`; hai công tắc; lần đo của Control Center trước) và `hardware_status.py` (JSON của GET);
  - nối dây: `control_center.py` (`_hardware(center)` import trễ, GET và ba POST, `close()` khi dừng), `phone_access.py` (`PC_ONLY_HARDWARE`, ba POST chỉ-PC);
  - trang: `dashboard_v2/hardware-view.js` (chỉ nạp trong `live.html`), `adapter.js`, `app.js` (795 dòng), `contracts.js`, các file kiểm và server giả.
  - **Test:** 363 test `test_hardware_*` qua (cộng các test dashboard, phone, route, `test_cache_dependencies`, `test_job_purge`); `verify.cjs` 50/50, `verify-adapter.cjs` 42/42, `node --check` mọi file; cổng khóa cache `ALL_EQUAL True`.
  - **Cả bộ test** (`TEMP=TMP=E:\DungChung\BiliFlow\temp`, clip tổng hợp 1 s trong `input\` của worktree, đã xóa): 3024 test trong 2863 s, 35 bỏ qua, **3 thất bại, không cái nào do E2**:
    - `test_download_account_e2e…test_phone_views_accounts_but_drives_episodes_and_groups` và `test_download_account_e2e_flows…test_session_expiry_mid_group_…`: trang live chưa tải xong (`ERR_CONNECTION_REFUSED` ở `review-core.js`) khi máy đang bận; chạy riêng thì qua cả hai;
    - `test_download_account_sources.SessionTest.test_cookies_rotated_before_a_cancel_are_kept`: test hủy sau đúng 4 s và cần trang đã đổi cookie trước đó; chạy riêng ba lần thì qua 2, hỏng 1 trên worktree, và **đúng như vậy trên code gốc `40a0125`** (giải nén vào `temp`, đã xóa). Test này phụ thuộc thời gian khởi động Edge, không liên quan E2.
  - **Review:** hai agent review chỉ đọc (manager; lệnh đo). Không phát hiện mức nghiêm trọng, cao hay trung bình. Bảy phát hiện thấp, đã sửa và có test:
    - ân hạn 60 s đếm từ lúc ghi file hủy, không từ mốc 15 phút (đồng hồ nhảy không làm mất ân hạn);
    - `close()` chạy đúng lúc `start()` tạo tiến trình vẫn ghi file hủy; manager tạo sau `stop()` đóng ngay;
    - Ctrl+C hay lỗi bất kỳ khi chờ một lệnh con thì diệt lệnh con trước khi nhả khóa;
    - last-good không ghi được sau khi file chính đã ghi: giữ bản cũ, ghi log, thoát 0 (mã 70 luôn nghĩa "không ghi gì");
    - thiếu `ffmpeg.exe` không còn bị từ chối (64): lần đo vẫn chạy và ghi `detection.ffmpeg.ok` sai, đúng bản E0 đã duyệt;
    - hai test chưa đủ chặt (file khóa được nhả sau 124 và sau hủy; kiểm rảnh lần hai diễn ra khi đã giữ mutex);
    - trang hiện `previous_done` (lần đo của Control Center trước đã xong, không có mã thoát) như thông tin, không như lỗi.
  - **Tài liệu:** mục 3.6, 4.1, 6.1, 6.3, 8, 9 chỉnh theo code; ba chỗ lệch so với bản E0 đã duyệt ghi ở mục 11 (chờ duyệt, xem "Chờ người dùng").
  - **Control Center thử** (root tạm `<worktree>\temp\hw-cc-20261012`, đã xóa sau khi kiểm; cổng 8798, `--no-import-existing`, đã dừng bằng PID):
    - không có hồ sơ: "Chưa đo", "Đo lại" bị khóa với "Hãy tạm dừng hàng đợi trước";
    - hồ sơ mẫu khớp máy này (RTX 2060, driver 617.42, torch 2.14.0+cu126, `ffmpeg.exe` của thư mục cài chỉ đọc `stat`): "Sẵn sàng", dòng `nvidia-sm75-torch2.14.0+cu126`, đủ ba họ, quan sát nhẹ thật;
    - "Chỉ dùng CPU" bật rồi tắt qua hộp xác nhận: file công tắc ghi đúng, sự kiện `HARDWARE_CPU_ONLY_CHANGED` (INFO); khi bật: "CPU chưa sẵn sàng" (trước E3);
    - file công tắc hỏng: "Công tắc hỏng", mọi họ bị từ chối; bấm công tắc thì ghi lại cả file, sự kiện có `replaced_invalid: true`;
    - hồ sơ của card Ampere trên máy RTX 2060: "Card đã đổi", nêu cả hai card;
    - không bấm "Đo lại" (lần đo thật chỉ ở khung GPU 3); không đụng `state\` thật.

- **Người dùng duyệt ngày 2026-10-12** (ghi ở mục 10–11 của `docs/HARDWARE_PROFILE.md`):
  1. ba chỗ E2 lệch bản E0 (mục 11);
  2. bốn cách hiểu 1–4 của E về phương án D (mục 10). Ý 5 (lần đo thất bại vì card không có mã torch hay driver dưới mức tối thiểu cho `ARCH_UNSUPPORTED`/`DRIVER_TOO_OLD`) được duyệt sau đó cùng ngày, qua session điều phối lúc gộp G1;
  3. hai câu về nhãn và lời từ chối: giữ như đã làm.
  - Người dùng thêm cổng cho lần đo thật: RTX 2060 không bị `VRAM_TOO_SMALL` hay lời từ chối nào; ghi lại VRAM mà driver báo (mục 6.4).
  - `AGENTS.md`: session điều phối thêm các route `/api/hardware-profile*` và lệnh đo lúc gộp G1; E không sửa.

## Đang dở
- **Lần đo thật ở khung GPU 3** (mục 6.4 của `docs/HARDWARE_PROFILE.md`): chưa chạy. Chờ người dùng báo **Control Center thật đã tắt**.

## Bước tiếp theo
1. **Lần đo thật**, chỉ sau lời báo của người dùng:
   - chép `ffmpeg.exe` và `ffprobe.exe` của thư mục cài vào `<worktree>\tools\ffmpeg\bin` (đã gitignore);
   - chạy lệnh của mục 6.4 với `-ProjectRoot` là một root tạm trong `temp\` của worktree và `-GuardRoot E:\DungChung\BiliFlow`;
   - kiểm cổng của lần đo thật (mục "Cách kiểm" dưới đây);
   - báo cáo chỉ ghi tóm tắt hồ sơ (trạng thái, dòng, kết quả phép thử, VRAM, thời gian); ghi vào file này và commit.
2. **G1:** người dùng duyệt gộp; session điều phối thêm route và lệnh đo vào `AGENTS.md` lúc gộp.

## Cách kiểm
- **Lệnh test:** `E:\DungChung\BiliFlow\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_hardware_*.py" -v`, với `PYTHONPATH=<worktree>\src`.
- **Trước khi bàn giao:** chạy cả bộ, với một clip tổng hợp 1 s trong `input\` của worktree; xóa clip sau đó. Đặt `TEMP` và `TMP` là `E:\DungChung\BiliFlow\temp` (như `env.ps1`), nếu không ba module E2E tài khoản nguồn từ chối chạy.
- **Cổng của E1–E2:**
  - không khóa cache stage nào đổi: `stage_source_paths` của 10 stage giống hệt ở `40a0125` và HEAD; `tests.test_cache_dependencies` qua;
  - dòng lệnh không đổi: test so nguyên bộ argv (mục 8 ý 4 của tài liệu), và các test hiện có ghim dòng lệnh vẫn qua;
  - import sạch: `control_center` không nạp `hardware_*` lúc import;
  - không có `f"hw:{job_id}"` trong `src`;
  - các route chỉ-PC bị từ chối khi gọi từ điện thoại.
- **Cổng của lần đo thật** (người dùng thêm ngày 2026-10-12; mục 6.4):
  - lệnh đo thoát 0, `attempt.result` là `ok`;
  - `resolve()` trên root tạm với quan sát nhẹ thật cho `ready` ở dòng `nvidia-sm75-torch2.14.0+cu126`: RTX 2060 **không bị `VRAM_TOO_SMALL` hay lời từ chối nào**;
  - ghi lại VRAM mà driver báo: `vram_mib` của NVML trong `fingerprint.gate_key.nvidia_gpus` và của `nvidia-smi` trong `detection.nvidia_smi.gpus`;
  - ngưỡng 6144 MiB chỉ áp trên đường chưa qua cổng, nhưng nếu driver báo dưới 6144 MiB thì RTX 2060 sẽ bị từ chối khi đổi sang torch hay driver chưa qua cổng: khi đó dừng và báo người dùng, không tự đổi ngưỡng.

## Chờ người dùng
- **Lần đo thật E2:** người dùng tắt Control Center thật ở khung GPU 3 rồi báo E (agent không bao giờ POST `/api/scheduler` và không tự tắt Control Center).
- **G1:** duyệt gộp nhánh.
- Trước Đợt 2 (không thuộc E0–E2): chạy lại cổng framemd5 cả phim L ở khung GPU 3 (câu hỏi 1, phương án b; người dùng đã cho phép đọc bản L cho lần chạy đó).

## Lưu ý cho người tiếp quản
- **Không sửa:**
  - danh sách của người dùng: `scheduler.py`, `job_store.py`, `job_pipeline.py`, module quét, `cli.py`, `__main__.py`, `__init__.py`, `run.ps1`, `env.ps1`, `stage_cache.py`, `cache_dependencies.py`, `pyproject.toml`, `config/*`, `models\`;
  - thêm theo đề xuất:
    - `nvdec.py`, `final_renderer.py` và `resource_lock.py` (của A);
    - `job_purge.py` và `recycle_bin.py` (của C2; chỉ import);
    - `AGENTS.md`: session điều phối thêm route `/api/hardware-profile*` và lệnh đo lúc gộp G1 (người dùng, 2026-10-12);
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
- **"Đo lại" trên Control Center thử là một lần đo thật:** nó lấy mutex GPU và mutex xuất **thật** (tên toàn cục, không theo root), và khi chạy bằng `.venv` của thư mục cài thì còn canh cả thư mục cài (mục 6.3). Vì vậy không bao giờ bấm "Đo lại" trên Control Center thử ngoài khung GPU 3.
- **Test không bao giờ giữ tên mutex thật** (`Local\BiliFlowGpuInference`, `Local\BiliFlowFinalRender`). Dùng `Local\BiliFlowTest-<uuid>`, truyền bằng tham số hàm, không bao giờ qua dòng lệnh hay biến môi trường.
- **Control Center thử:**
  - lệnh: `python -m biliflow.control_entry --project-root <worktree>\temp\hw-cc-* --port 8798 --no-import-existing`, giống `Start-BiliFlow.ps1`. Không chạy qua `cli.py`, vì như vậy sẽ kéo torch vào;
  - dừng bằng PID đang nghe ở cổng 8798, **không** bằng `Stop-BiliFlow.ps1` (script đó dừng bản thật).
- **Cho session điều phối, lúc gộp G1** (người dùng giao ngày 2026-10-12; E không sửa `AGENTS.md`):
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
  - **tài liệu:** hai trích dẫn sai trong `PERF_INSTALL_PLAN.md` và byte 0x08 ở `SCAN_PERFORMANCE.md:140-143`;
  - **tải video (tài khoản nguồn):** `test_download_account_sources.SessionTest.test_cookies_rotated_before_a_cancel_are_kept` chập chờn cả trên `40a0125` (hủy cố định sau 4 s, trước khi Edge kịp tải trang khi máy bận).
