# Bàn giao A1c – bản xuất sang làn xuất khi phim mới còn chờ preflight
- Nhánh: perf/a1c-preflight-lane (từ main dc9d294) · Worktree: `temp\wt-a1c` · Cập nhật: 2026-10-15
- Chạy ở: PC · Trạng thái: **code và test xong; cổng đơn vị, test A1b/scheduler và khóa cache đều đạt. Phần kiểm thật trên Control Center thử chưa chạy** (người dùng chọn "Để sau"; nguồn clip đã chọn: video gốc của job 142). Chưa push, chưa merge.

## Vấn đề (điều phối thấy trên máy thật, 2026-10-14 20:42)
- Trong A1b, `_select_main` / `_export_waits` chỉ để bản xuất chờ làn xuất khi **chính** bước kế tiếp của phim chờ quét chạy trên GPU (`if not runs_on_gpu(next_scan[2]): return False`).
- Phim chưa bắt đầu quét luôn chờ ở `preflight` (CPU, không có tiến trình). Vì vậy bản xuất chiếm làn chính, và lượt quét phải chờ hết mọi bản xuất trong hàng.
- Ca thật: job 131 chờ quét sau 2 bản xuất (khoảng 15 phút), trong khi VRAM còn trống khoảng 5 GB và hồ sơ phần cứng đã xác nhận NVENC.

## Đã xong

### Thay đổi (`src/biliflow/scheduler.py`)
- Hằng số mới `SHORT_CPU_STAGES = frozenset({"preflight"})`: các bước CPU đủ ngắn để làn chính chạy trong lúc bản xuất chờ làn xuất.
- Hàm mới `JobScheduler._first_gpu_stage(scan)`:
  - Bước chờ chạy trên GPU thì trả về chính nó (như A1b).
  - Bước chờ là bước CPU ngắn thì xét các bước còn chạy của job theo đúng thứ tự `next_pending_stage` phát ra (`ordinal`, trạng thái PENDING hoặc FAILED_RETRYABLE) và trả về bước GPU đầu tiên. Chỉ được đi qua các bước CPU ngắn.
  - Gặp một bước CPU khác, một bước không còn định nghĩa, hoặc hết bước GPU thì trả về `None`: bản xuất chạy trước như cũ.
- `_export_waits` gọi `_first_gpu_stage` thay cho `runs_on_gpu(next_scan[2])`. Mọi kiểm tra đứng trước nó giữ nguyên thứ tự: làn xuất bận, chế độ, luồng làn xuất đã chết, bản xuất bị trả về, bản xuất đã bị từ chối.
- Diễn biến khi song song được:
  1. Ở ranh giới bước, bản xuất đứng đầu hàng còn phim mới chờ ở `preflight`. Bản xuất được xét VRAM như A1b (`_lane_decision`, NVML đọc lúc đó, đỉnh `SCAN_PEAK_MIB` của bước GPU sắp chạy). Nếu được, làn chính chạy `preflight`.
  2. `preflight` không có tiến trình nên `_gpu_stage` vẫn trống: làn xuất không nhận gì.
  3. Ở ranh giới sau `preflight`, bước kế tiếp là bước GPU (ví dụ `text`): bản xuất được xét lại (đọc VRAM mới) và làn chính chạy bước GPU.
  4. Khi tiến trình GPU bắt đầu, làn xuất nhận bản xuất (NVENC, sự kiện `EXPORT_LANE_PARALLEL` ghi `scan_stage` là bước GPU đó). Bản xuất thứ hai trong hàng chạy tiếp cạnh cùng bước GPU khi làn xuất rảnh.
- Khi không song song được (VRAM thiếu, NVML không đọc được trên máy chỉ có CPU, hồ sơ chưa đo hoặc NVENC lỗi, kế hoạch ép x264, khung chưa đo): `_turn_down` ghi `EXPORT_LANE_SEQUENTIAL` **một lần** cho lệnh xuất đó, và bản xuất chạy trước ở làn chính như cũ.
- Không đổi: "Tắt" và lúc chưa có số đo VRAM (`_parallel_allowed` sai, làn chính là worker đơn, không đọc NVML, không ghi sự kiện); chỉ NVENC "auto" ở làn xuất, renderer vẫn không chạy x264 ở đó; đường trả bản xuất về làn chính; bộ canh treo của H ở làn xuất; detector, ngưỡng, kết quả quét, byte của bản xuất; `export_lanes.py`, `final_renderer.py`.

### Test (`tests/test_scheduler.py::ExportLaneTests`)
- `scan_stages` của bộ test nay dựng được pipeline có `preflight` ở đầu (`preflight`), bước CPU có tiến trình trước các bước quét (`cpu_before`) và `build_review` ở cuối (`review`). Các test A1b cũ giữ nguyên pipeline cũ.
- 14 test mới:
  - hai luồng thật: phim mới chờ `preflight` sau hai bản xuất. Trong lúc `preflight` chạy không có tiến trình nào; sau đó `scan_one` ở làn chính, hai bản xuất lần lượt ở làn xuất cạnh nó, cả hai có `EXPORT_LANE_PARALLEL` với `scan_stage = scan_one`, thứ tự `a:scan_one, render:b, render:c, a:scan_two`;
  - ở `preflight` bản xuất được xét cho bước GPU đầu tiên, rồi xét lại ở bước GPU;
  - bị từ chối ở `preflight` (VRAM thiếu kèm số liệu, không đọc được NVML, NVENC lỗi, hồ sơ chưa đo, ép x264): bản xuất chạy trước, một sự kiện;
  - VRAM thiếu ở bước GPU sau `preflight`: bản xuất chạy trước, có sự kiện;
  - "Tắt" và chưa có số đo VRAM: bản xuất chạy trước, không đọc NVML, không sự kiện;
  - phim không còn bước GPU (quét trên CPU, các bước GPU đã xong, chỉ còn `build_review`): như cũ;
  - bước CPU có tiến trình trước bước GPU: như cũ (chỉ đi qua bước CPU ngắn);
  - phim đang giữa chừng ở bước GPU: xét ở chính bước đó như A1b;
  - bản xuất bị trả về: chờ đúng job quét đã chạy cạnh nó, rồi chạy x264 ở làn chính **trước** phim mới đang chờ `preflight`;
  - bộ canh treo của H dừng bản xuất ở làn xuất cạnh phim mới, không trả về;
  - (sau review) phim rời hàng ở `preflight` (preflight lỗi, Dừng ngay, Hủy): bản xuất chạy ngay; các bước GPU đều lấy từ cache: bản xuất chạy ở `build_review`; hàng FAILED_RETRYABLE được tính như bước chờ, bước không còn định nghĩa thì dừng xét; luồng làn xuất đã chết thì không chờ ở `preflight`.
- Trên code của dc9d294, các test mới về hành vi mới đều hỏng (RED đã chạy); các test giữ hành vi cũ đạt cả hai bên.

### Cổng đã đạt
- **Test đơn vị mới và cũ của scheduler, làn xuất:** `tests.test_scheduler tests.test_export_lanes tests.test_export_lane_routes`, 115 test OK (trước 4 test bổ sung sau review; 4 test đó chạy riêng đều OK).
- **Bộ tập trung 28 module của A1b** (xem "Cách kiểm"), chạy trên code cuối cùng: 741 test OK, 5 bỏ qua, 333 giây (2026-10-15).
- **Khóa cache quét không đổi so với dc9d294:** CLEAN. Script so từng bước trong `CACHEABLE_STAGES`: danh sách file (`stage_source_paths`) và SHA-256 từng file, cộng các file cố định của khóa (config, `run.ps1`, `env.ps1`, `pyproject.toml`). 10 bước, 34 file, 7 file cố định; `scheduler.py` không thuộc khóa nào.
- **Review độc lập** (một agent, chỉ đọc): không có CRITICAL hay HIGH. Nó xác nhận không điều kiện an toàn nào của A1b bị vượt và không có chờ vô hạn. MEDIUM: thiếu test cho các ca phim rời hàng, cache hit, FAILED_RETRYABLE, bước mất định nghĩa, luồng làn xuất chết; đã thêm 4 test (14 ca). LOW không sửa: xem "Lưu ý".

### Script kiểm thật `scripts/a1c_check.py` (chưa chạy)
- Dựng root tạm `temp\a1c-check-<giờ>` như A1b (`a1b_vram_measure.make_root`, thêm tiền tố `a1c-check-`), Control Center thử ở cổng 8793, clip cắt bằng stream copy (video gốc chỉ được đọc).
- **Không gọi route nào của Control Center thật.** Trước và trong mỗi bước dùng GPU, script đọc database thật ở chế độ `mode=ro`. Nó chỉ chạy khi hàng đợi thật vừa trống vừa đang tạm dừng (không job nào QUEUED hay đang chạy). Mọi thay đổi trong lúc chạy được ghi lại.
- Script tự hạ mức ưu tiên xuống dưới bình thường. Control Center thử, các bước và mọi FFmpeg kế thừa mức đó; lượt chạy ghi mức ưu tiên của từng FFmpeg nó thấy.
- Clip: E1 (bản xuất), S1 (phim), S2 (S1 đổi một thẻ metadata: cùng khung, khác SHA-256). Quét chỉ nhóm quảng cáo (`preflight` rồi `text` trên GPU), live action.
- Lượt `run`: tạm dừng hàng đợi **thử**, bấm xuất E1 (auto) và bắt đầu quét S1, rồi mở lại. Khi đó bước chờ đầu tiên là bản xuất và phim chờ ở `preflight`. Lượt `alone`: quét S2 một mình. Cache grounding được dời đi trước mỗi lượt để mỗi lượt tự tính hộp.
- `report` ghi `check.json` (không tên, không đường dẫn, không chữ detector đọc được) với các kiểm tra:
  - `EXPORT_LANE_PARALLEL` đúng một lần, cạnh bước GPU đầu tiên của S1, không có `EXPORT_LANE_SEQUENTIAL` hay `EXPORT_DEFERRED`;
  - `preflight` của S1 xong trước khi bản xuất bắt đầu, và bước GPU đầu tiên bắt đầu trước bản xuất;
  - bản xuất chỉ ở làn `export`; hai làn chạy cùng lúc (`active_lanes`);
  - kết quả quét S1 giống hệt S2 (`a1b_gates.compare_scans`);
  - bản xuất `h264_nvenc`, `lane: parallel`, không fallback, qua `source_cleanup.assess_job`;
  - mọi FFmpeg dưới bình thường; hàng đợi thật trống và tạm dừng suốt các bước.

## Đang dở
- Phần kiểm thật dùng GPU (chờ người dùng chọn giờ).

## Bước tiếp theo
1. Khi người dùng chọn giờ: người dùng bấm "Tạm dừng hàng đợi" trên Control Center thật (hàng đợi phải trống). Agent không tự bấm.
2. Chạy, từ `temp\wt-a1c`, với python của `.venv` thư mục cài. `<video>` là video gốc của job 142, tra đường dẫn bằng cách đọc database thật ở chế độ `mode=ro`; không ghi đường dẫn đó vào repo:
   ```
   $py = "E:\DungChung\BiliFlow\.venv\Scripts\python.exe"
   & $py scripts\a1c_check.py setup --install E:\DungChung\BiliFlow --code . --source <video>
   & $py scripts\a1c_check.py profile --install E:\DungChung\BiliFlow --root <root setup in ra>
   & $py scripts\a1c_check.py ready   --install E:\DungChung\BiliFlow --root <root>
   & $py scripts\a1c_check.py run     --install E:\DungChung\BiliFlow --root <root>
   & $py scripts\a1c_check.py alone   --install E:\DungChung\BiliFlow --root <root>
   & $py scripts\a1c_check.py report  --install E:\DungChung\BiliFlow --root <root> --evidence docs\evidence\a1c\<ngày>
   & $py scripts\a1c_check.py remove  --install E:\DungChung\BiliFlow --root <root>
   ```
   Ước tính 30–40 phút GPU. Xong thì báo người dùng mở lại hàng đợi thật.
3. Viết README tiếng Việt cho bằng chứng, cập nhật file này, `CHANGELOG.md`, `docs/PROJECT_STATUS.md`, `docs/SESSION_HANDOFF.md`, rồi commit.
4. Điều phối: full suite trên nhánh, rồi gộp. Agent của A1c không push, không merge.

## Cách kiểm
- Lệnh test (trong worktree):
  ```
  $env:PYTHONPATH = "E:\DungChung\BiliFlow\temp\wt-a1c\src"
  E:\DungChung\BiliFlow\.venv\Scripts\python.exe -m unittest tests.test_scheduler tests.test_job_store tests.test_final_renderer tests.test_export_encoder tests.test_render_graph_file tests.test_control_center tests.test_cache_dependencies tests.test_stage_cache tests.test_dashboard_v2_review tests.test_dashboard_v2_contract tests.test_dashboard_v2_phone tests.test_dashboard_v2_phone_hardening tests.test_dashboard_v2_route tests.test_dashboard_v2_frontend tests.test_dashboard_v2_status tests.test_dashboard_v2_downloads tests.test_hardware_manager tests.test_hardware_routes tests.test_job_import tests.test_skip_export tests.test_export_guards tests.test_job_delete tests.test_source_cleanup tests.test_source_archive tests.test_job_purge tests.test_export_lanes tests.test_export_lane_routes tests.test_phone_tailscale
  ```
- Khóa cache: so `cache_dependencies.stage_source_paths` và SHA-256 từng file của mọi bước trong `stage_cache.CACHEABLE_STAGES`, cộng các file cố định của `stage_cache._tree_fingerprint`, giữa worktree và `git archive dc9d294`. Script nằm trong scratchpad của session; viết lại theo mô tả này nếu cần.

## Chờ người dùng
- Giờ chạy phần kiểm thật dùng GPU (người dùng chọn "Để sau" ngày 2026-10-15; nguồn clip: video gốc của job 142).

## Lưu ý cho người tiếp quản
- **"Ngắn" là giả định.** `preflight` băm SHA-256 toàn bộ video gốc (`_run_preflight`), nên trên ổ chậm hay video lớn nó có thể mất vài chục giây. Trong lúc đó bản xuất chờ và GPU rảnh. Vẫn tốt hơn trước, khi bản xuất chặn cả lượt quét. Chỉ `preflight` nằm trong `SHORT_CPU_STAGES`; thêm bước khác vào đó cần đo trước.
- Bản xuất bị từ chối (hồ sơ chưa đo, ép x264, VRAM thiếu…) trước đây im lặng khi phim mới chờ `preflight`, nay ghi `EXPORT_LANE_SEQUENTIAL` ở ranh giới đó. Kết quả vẫn như cũ: bản xuất chạy trước. VRAM thiếu tạm thời đúng lúc đó cũng từ chối cho cả lệnh xuất, như A1b ở ranh giới bước GPU.
- Bước GPU lấy từ cache không chạy tiến trình nên làn xuất không nhận gì. Bản xuất chờ qua các bước đó và chạy trước ở `build_review`, như A1b với một bước GPU lấy từ cache.
- `_first_gpu_stage` dựng lại `_definitions(job)` một lần ở ranh giới đó (đọc `processing_profiles.json` và vài setting). Việc này chỉ xảy ra khi bản xuất đứng đầu hàng và phim chờ ở `preflight`, nhỏ so với `preflight`.
- `queue_order()` trên dashboard vẫn là thứ tự A1a: trong lúc `preflight` chạy, bản xuất hiện ở vị trí 1.
- **Không sửa:** `AGENTS.md`, `export_lanes.py`, `final_renderer.py`, mọi file thuộc khóa cache quét, thiết lập encode, ngưỡng phát hiện, `annotations\`.
- Thư mục chính đang chạy Control Center thật: không sửa file ở đó, không gọi route nào của nó, không đo phần cứng với root là thư mục cài.
