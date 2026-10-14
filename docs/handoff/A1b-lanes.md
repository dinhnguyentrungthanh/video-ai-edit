# Bàn giao A1b – xuất song song với quét, tự chọn theo máy
- Nhánh: perf/export-a1b (từ main 23c9e00) · Commit cuối: xem `git log -1` (bước 1 a079975, bước 2 là commit số đo) · Worktree: `temp\wt-a1b` · Cập nhật: 2026-10-14
- Chạy ở: PC · Trạng thái: **code, test và số đo VRAM xong (bước 2); đang chạy các cổng 1, 2, 3, 6 trên Control Center thử.** Với số đo đã ghi, "Tự động" mở làn xuất trên máy này khi đủ điều kiện.

## Đã xong

### Bước 1: hai làn trong bộ lập lịch, công tắc "Xuất song song", bản xuất trả về làn chính
- **Hai làn** (`scheduler.py`):
  - Làn chính `main` chạy mọi bước quét và mọi bản xuất như A1a: xuất trước, mỗi loại theo thứ tự bấm.
  - Làn `export` chỉ chạy **một** bản xuất NVENC, bên cạnh một bước quét **trên GPU** của làn chính.
  - Trạng thái theo làn (dict, dưới `_lock`): `_process`, `_active`, `_log_handle`, `_executing`, `_threads`.
  - Mỗi làn có một luồng (`biliflow-scheduler`, `biliflow-scheduler-export`).
  - `_wake.set()` đánh thức cả hai làn; mỗi làn chờ và xóa cờ của riêng nó (`_LaneWake`), nên làn này không nuốt mất tín hiệu của làn kia.
  - Luồng của làn xuất mà chết vì lỗi thì làn chính không giữ bản xuất chờ làn đó nữa.
- **Dừng đúng làn:**
  - `pause_now` và `cancel` ghi trạng thái trước, rồi đọc (dưới `_lock`) những làn đang chạy job đó (`_lanes_running`) và chỉ dừng các làn đó (`_terminate_lane`).
  - `_terminate_active` dừng mọi làn; chỉ dùng khi shutdown quá hạn.
  - Nhánh lỗi chỉ đóng log của làn mình (`_release_lane(lane)`).
  - Giữ nguyên các lệnh ghi có điều kiện của F (`claim_queued`, `start_stage`, kiểm `STOPPED_BY_USER_STATES` dưới `_lock` trước `Popen`) ở cả hai làn.
- **`is_busy`:** đúng với mọi làn. Nó gồm cả khoảng ghi sổ sau khi tiến trình đã thoát (`_executing`), nên Xóa và Lưu trữ vẫn bị khóa trong lúc đó.
- **`shutdown`:**
  - Đặt `_draining` để không làn nào bắt đầu bước mới.
  - "Tắt ngay": Dừng ngay từng job đang chạy.
  - "Tắt sau bước hiện tại": đánh dấu AFTER_STAGE mọi job đang chạy và chờ **cả hai** làn tới ranh giới bước.
- **`scheduler_paused`** ("Tạm dừng hàng đợi"): cả hai làn không bắt đầu bước mới; bước đang chạy ở làn nào vẫn chạy tiếp.
- **`status()`:**
  - `active` giữ nguyên dạng một dict cho trang cũ và "Đo lại". Nó là bước của làn chính nếu có, nếu không thì của làn xuất.
  - Thêm `active_lanes`: danh sách `{lane, job_id, stage, pid}`, làn chính trước.
  - Thêm `parallel_export`: `auto` hoặc `off`.
- **Khi nào mở làn xuất** (`export_lanes.py`, mới, thuộc control plane, ngoài khóa cache). Chế độ `auto` mở làn chỉ khi **tất cả** điều sau đúng lúc bắt đầu:
  1. Bước của làn chính chạy trên GPU (`runs_on_gpu`: `uses_gpu`, có lệnh, không có `--device cpu`).
  2. Bản xuất xin `auto` (không bị ép x264) và hồ sơ phần cứng xác nhận NVENC cho đúng FFmpeg của root (`profile_nvenc_trial == "ok"`, không encode thử).
  3. Khung của video gốc thuộc cỡ đã đo VRAM: cạnh dài ≤ 1920, số điểm ảnh ≤ mức đã đo (ffprobe, có cache).
  4. NVML (pynvml, trong tiến trình, không gọi nvidia-smi hay torch) cho thấy đủ chỗ:
     `total − max(used_now, baseline + SCAN_PEAK_MIB) − need ≥ SAFETY_MARGIN_MIB`.
     `baseline` là VRAM đang dùng ngay trước khi tiến trình GPU của làn chính bắt đầu.
  5. Bản xuất chưa bị trả về lần nào trong lệnh xuất này.

  Các số `NVENC_NEED_MIB`, `SCAN_PEAK_MIB` và `SAFETY_MARGIN_MIB` được đo ở bước 2. Nếu một số là `None` (`export_lanes.measured()` sai), chế độ "Tự động" **đúng là worker đơn của trước**: không đọc NVML, không ghi sự kiện làn, không có làn xuất.
- **Ở ranh giới bước, làn chính đoán trước:**
  - Bản xuất đầu hàng đợi sẽ chạy song song được → làn chính lấy bước quét GPU kế tiếp, và làn xuất nhận bản xuất ngay khi tiến trình quét bắt đầu.
  - Không được → bản xuất chạy ở làn chính trước, như A1a.
  - Mỗi lệnh xuất chỉ bị từ chối một lần. Sự kiện `EXPORT_LANE_SEQUENTIAL` ghi lý do bằng tiếng Việt cùng số liệu; khi làn mở thì ghi `EXPORT_LANE_PARALLEL`.
  - Một lệnh "Xuất video" mới bắt đầu sạch: xóa bản ghi trả về và lời từ chối cũ.
- **Không bao giờ chạy x264 song song với quét** (`final_renderer.py`):
  - Làn xuất truyền biến `BILIFLOW_EXPORT_LANE=parallel` cho riêng tiến trình `render-final` của nó. `cli.py` và `run.ps1` không đổi; biến này chỉ thu hẹp những gì renderer được làm.
  - Với biến đó, renderer chỉ chạy NVENC theo hồ sơ (`parallel_lane_choice`). Bản xuất không phải NVENC thì dừng trước khi đọc video gốc (`not_nvenc`).
  - NVENC tự lỗi (ví dụ thiếu VRAM; vẫn chờ khoảng ân hạn dừng của A3) hoặc vượt giới hạn dung lượng → ghi `temp\render-progress\<id>.deferred.json` rồi thoát mã 2, **không chạy x264**.
- **Bản xuất trả về làn chính:**
  - Bộ lập lịch đưa stage render từ RUNNING về PENDING bằng một UPDATE có điều kiện (`JobStore.requeue_running_stage`), nên không ghi đè Hủy hay Dừng ngay đến trước. Job về QUEUED (hoặc PAUSED nếu đang "Dừng sau bước này") và giữ chỗ. Sự kiện `EXPORT_DEFERRED` ở mức WARNING.
  - Làn chính giữ bản xuất này cho tới khi **job quét đã chạy bên cạnh nó không còn bước nào chờ**, rồi chạy nó trước job quét kế tiếp ("sau khi lượt quét xong, không chen ngang").
  - Lúc đó renderer đọc bản ghi và encode bằng x264 đúng như A3: cùng lệnh x264 từng byte với bản ép x264. Manifest ghi `fallback.deferred: true`, `encoding.lane` (`main` hoặc `parallel`), `encoder_choice` của lần NVENC, và thời gian gồm cả lần NVENC. Xong thì xóa bản ghi.
- **Công tắc:**
  - Cài đặt → Hệ thống cục bộ → "Xuất song song: Tự động / Tắt" (`POST /api/scheduler/parallel-export`, đúng `{"mode": "auto"|"off"}`; chỉ PC: 127.0.0.1 kèm token; điện thoại bị từ chối trước khi đọc body).
  - "Tắt" giống hệt hành vi cũ: không đọc VRAM, không ghi sự kiện làn, không có làn xuất. Bản xuất đang chạy song song (nếu có) vẫn chạy xong.
  - Dashboard: dòng trạng thái đầu trang đổi chữ; thẻ "đang chạy" ghi "đang xuất song song #id"; hợp đồng `parallelExport` có trong `contracts.js` và bản demo.
- **Test đã chạy** (src của worktree, `.venv` của thư mục chính chỉ để chạy Python; không chạy full suite):
  - Mới, `tests/test_scheduler.py::ExportLaneTests` (18 test, chạy bằng chính hai luồng của bộ lập lịch với tiến trình giả). Cổng 4:
    - hủy hoặc tạm dừng làn A khi làn B đang chạy (đủ bốn trường hợp);
    - `is_busy` trong khoảng ghi sổ của từng làn;
    - shutdown ngay và shutdown chờ khi hai làn cùng chạy;
    - công tắc Tắt thì tuần tự;
    - bản xuất trả về chạy x264 ở làn chính sau job quét và trước job quét sau;
    - trả về không ghi đè Hủy hoặc Dừng ngay;
    - mọi lý do từ chối;
    - chưa đo VRAM thì "Tự động" giống hệt worker đơn;
    - luồng làn xuất đã chết thì không ai chờ nó;
    - tín hiệu đánh thức theo làn;
    - bước quét trên CPU;
    - biến môi trường chỉ có ở tiến trình của làn xuất.
  - Mới, `tests/test_final_renderer.py` (8 test về làn song song): NVENC lỗi hoặc quá cỡ thì trả về mà không chạy x264; ép x264 hoặc hồ sơ không xác nhận thì dừng trước mọi lệnh; làn chính chạy lại đúng lệnh x264 của A3; bản ghi của bản xuất khác bị bỏ qua.
  - Mới: `tests/test_export_lanes.py` (12 test), `tests/test_export_lane_routes.py` (5 test), `tests/test_job_store.py::test_requeue_running_stage_only_moves_a_running_stage`.
  - Bộ tập trung (28 module, xem "Cách kiểm"), chạy lại sau các sửa của review: 715 test OK, 5 bỏ qua, 295 giây.
- **Cổng 5 (khóa cache quét không đổi so với main):** đạt. Script so từng bước trong `CACHEABLE_STAGES`: danh sách file tính bằng code của nhánh và bằng code của 23c9e00 (`git archive`) giống nhau; blob git của cả 43 file trong khóa (cộng các file cố định như `run.ps1`, `env.ps1`, `pyproject.toml`) không đổi. Kết quả: 10 bước, 43 file, CLEAN. `test_cache_dependencies` liệt kê `export_lanes.py`, `final_renderer.py`, `export_encoder.py` và `phone_access.py` là control plane.

- **Review độc lập** (một agent review code, chỉ đọc):
  - Không có lỗi CRITICAL hay HIGH. Agent đã kiểm thứ tự khóa (`job_action_lock` trước `_lock`), các lệnh ghi có điều kiện của F ở cả hai làn, không có đường nào cho x264 chạy ở làn xuất, không có hai bản render chồng nhau, và trả về so với Dừng ngay/Hủy theo mọi thứ tự.
  - Ba điểm LOW đã sửa trong bước này: luồng làn xuất chết thì làn chính không chờ nó; tín hiệu đánh thức theo làn; chưa đo thì "Tự động" giống hệt worker đơn.

### Bước 2: đo VRAM và ghi số đo
- Người dùng cho phép đo ngay (2026-10-14) và chọn biên an toàn 256 MiB.
- Script `scripts/a1b_vram_measure.py`:
  - `run` dựng một root tạm `temp\a1b-measure-<giờ>` có marker riêng. `.venv` là junction; models, tools, runtime và cache mô hình là hard link; src, scripts và config chép từ worktree.
  - Nó cắt hai clip 10 phút bằng stream copy (1920x1080 và 1280x640) từ video người dùng đưa qua tham số, rồi chạy Control Center thử ở cổng 8791 (`--no-import-existing`).
  - Nó lấy mẫu NVML 10 lần/giây và đọc `GET /api/status` của Control Center thật mỗi 2 giây. Nó từ chối chạy nếu Control Center thật đang bận hoặc nếu cổng là 8765.
  - `remove` xóa root tạm: gỡ junction và link mà không đi vào bên trong, chỉ nhận thư mục `a1b-measure-*` nằm trong `temp`.
- Bằng chứng: `docs/evidence/a1b/vram-2026-10-14/` (README tiếng Việt, `run1`, `run2`: `summary.json`, `samples-1s.csv`, `run.log`; không có tên video).
- Kết quả trên RTX 2060 6144 MiB, driver 617.42; Control Center thật rảnh suốt hai lượt:
  - NVENC cần 244 MiB với khung 1080p và 169-170 MiB với khung 1280x640, khi xuất một mình cũng như cạnh Qwen2-VL.
  - Đỉnh lớn nhất của một bước quét là confirm_violence, 4851-4943 MiB trên nền (visual_logo 4688-4690).
  - Cạnh confirm_violence, đỉnh tổng là 5678/6144 MiB.
  - Mọi bản NVENC cạnh bước quét đều thành công, không encode lại, và job quét vẫn về WAITING_REVIEW.
- Hằng số đã ghi: `NVENC_NEED_MIB = ((1280 * 640, 170), (1920 * 1080, 244))`, `SCAN_PEAK_MIB = 4943`, `SAFETY_MARGIN_MIB = 256`. Máy này mở làn khi VRAM nền không quá 701 MiB (1080p) hoặc 775 MiB (1280x640); nền đo được là 490-596 MiB.
- Test không phải sửa: các test của làn đều tự đặt số của chúng (`patch.multiple`), còn `UnmeasuredTests` đặt `None`. Bộ tập trung 28 module chạy lại sau khi ghi số đo.

## Đang dở
- Cổng 1, 2, 3, 6 trên Control Center thử (người dùng chọn "Chạy ngay").

## Bước tiếp theo
1. Cổng 1: x264 giống hệt từng byte và framemd5 giữa tuần tự và A1b (bản trả về chạy x264 ở làn chính).
2. Cổng 2: lượt quét chạy cạnh bản xuất cho kết quả giống lượt quét chạy một mình (bỏ qua các trường thời gian).
3. Cổng 3: bản NVENC chạy song song qua kiểm tra sau xuất (`manifest_problem`).
4. Cổng 6: đo thời gian xuất cộng quét, song song so với tuần tự.
5. Mỗi bước: commit, cập nhật bàn giao này, báo hash. Không push, không merge. Xóa root tạm bằng `a1b_vram_measure.py remove` khi xong.

## Cách kiểm
- Lệnh test (trong worktree; chỉ các module liên quan):
  ```
  $env:PYTHONPATH = "E:\DungChung\BiliFlow\temp\wt-a1b\src"
  E:\DungChung\BiliFlow\.venv\Scripts\python.exe -m unittest tests.test_scheduler tests.test_job_store tests.test_final_renderer tests.test_export_encoder tests.test_render_graph_file tests.test_control_center tests.test_cache_dependencies tests.test_stage_cache tests.test_dashboard_v2_review tests.test_dashboard_v2_contract tests.test_dashboard_v2_phone tests.test_dashboard_v2_phone_hardening tests.test_dashboard_v2_route tests.test_dashboard_v2_frontend tests.test_dashboard_v2_status tests.test_dashboard_v2_downloads tests.test_hardware_manager tests.test_hardware_routes tests.test_job_import tests.test_skip_export tests.test_export_guards tests.test_job_delete tests.test_source_cleanup tests.test_source_archive tests.test_job_purge tests.test_export_lanes tests.test_export_lane_routes tests.test_phone_tailscale
  ```
- Cổng 5: script `gate5.py` (so `stage_source_paths` và blob của các file trong khóa cache với một commit gốc), chạy `python -I gate5.py <worktree> 23c9e00`. Script nằm trong scratchpad của session; điều phối có thể viết lại theo mô tả ở trên.
- Các cổng 1, 2, 3, 6 cần GPU, video thật và Control Center thử.
- Đo lại VRAM (máy khác, driver khác): `python scripts\a1b_vram_measure.py run --install <install> --code <checkout> --source-1080 <video> --source-small <video> --evidence <thư mục>`, chỉ khi người dùng cho phép và Control Center thật rảnh. Sau đó chạy `remove`.

## Chờ người dùng
- (trống) Người dùng đã cho đo (2026-10-14), đã chọn "sau khi job quét đó xong" cho bản xuất bị trả về, chọn biên 256 MiB và cho chạy các cổng ngay.

## Lưu ý cho người tiếp quản
- **Không thêm trường `lanes` vào hồ sơ phần cứng.** Quyết định dựa trên NVML đọc trực tiếp lúc bắt đầu cùng các số đo cố định trong `export_lanes.py`; không tăng `profile_version`. Nếu điều phối muốn có trường `lanes`, đó là một bước riêng của Session E.
- NVML đọc GPU số 0 (giống `control_center._resources`). Máy nhiều GPU thì thứ tự NVML và CUDA có thể khác nhau; máy hiện tại chỉ có một GPU.
- `queue_order()` (vị trí trong hàng đợi trên dashboard) vẫn là thứ tự A1a. Khi làn xuất nhận một bản xuất cạnh bước quét, hai việc chạy gần như cùng lúc nên thứ tự hiển thị vẫn đúng ý. Riêng bản xuất bị trả về và đang chờ job quét xong thì vẫn hiện ở vị trí 1.
- Bản ghi `.deferred.json` của một bản xuất bị Hủy vẫn nằm trong `temp\render-progress` cho tới lần "Xuất video" kế tiếp của cùng output, hoặc tới khi bản xuất đó xong. File nhỏ và không ai đọc nếu không có lệnh xuất.
- Bản xuất đã bị trả về rồi bị Dừng ngay, khi Tiếp tục vẫn chạy x264 ở làn chính theo bản ghi (không thử NVENC lại).
- Vòng nhịp tim của `_run_process` là 1 giây, nên test luồng mất khoảng 30 giây; không phải lỗi treo.
- **Không sửa:** `AGENTS.md`, `CHANGELOG.md`, `docs/PROJECT_STATUS.md`, `docs/SESSION_HANDOFF.md`, mọi file thuộc khóa cache quét (`cli.py`, `run.ps1`, `env.ps1`, `probe.py`, `storage.py`…), thiết lập encode (NVENC p5 `-cq 25`, x264), ngưỡng phát hiện, `annotations\`.
- Thư mục chính đang chạy Control Center thật: không sửa file ở đó, không gọi API ghi của nó, không đo phần cứng với root là thư mục cài.
