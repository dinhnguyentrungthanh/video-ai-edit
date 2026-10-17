# Bàn giao A1d – ngân sách VRAM của làn xuất theo bước đang chạy
- Nhánh: perf/export-lane-a1d (từ main 41169fc) · Worktree: `temp\wt-a1d` · Cập nhật: 2026-10-17
- Chạy ở: PC · Trạng thái: **xong: code, test, khóa cache (CLEAN 10/10) và kiểm thật GPU trên Control Center thử ngày 2026-10-17 (mọi kiểm tra đạt, `docs/evidence/a1d/2026-10-17/`).** Chưa push, chưa merge.

## Vấn đề
- A1b tính mọi bước quét ở mức đỉnh của Qwen2-VL (`SCAN_PEAK_MIB` 4943 MiB): `6144 − max(đang dùng, nền + 4943) − NVENC 170 ≥ 256`.
- Windows và các app giữ khoảng 950–1250 MiB, nên phần còn lại luôn âm (event 2630, 2638: −180, −201). Làn xuất không bao giờ mở; ví dụ job 139 chờ ở `localize_logo` (chỉ cần khoảng 1,3 GB) gần 29 phút trong lúc ba bản xuất chạy lần lượt.

## Đã xong

### Ngân sách theo bước (`src/biliflow/export_lanes.py`)
- `STAGE_PEAK_MIB`: đỉnh VRAM thêm vào của từng bước GPU, lấy từ chính lần đo của A1b (`docs/evidence/a1b/vram-2026-10-14/run1`, `run2`, `summary.json`) và lần quét cả phim của A1d (`docs/evidence/a1d/2026-10-17/check.json`): lấy số lớn nhất của mọi tiến trình bước đó **không chạy chồng bản xuất**, ở cả ba nguồn.

  | Bước | MiB | Khung clip lớn nhất đã đo |
  |---|---|---|
  | adult | 258 | 1920x1080 |
  | verify_adult | 479 | 1920x1080 |
  | live_safety | 717 | 1920x1080 |
  | animation_safety | 754 | 1280x640 (chỉ clip mixed) |
  | text | 833 | 1920x1080 |
  | localize_logo | 1309 | 1920x1080 |
  | visual_logo | 4691 | 1920x1080 (4690 ở clip 1280x640, 4688 ở 1080p, 4691 ở cả phim 1280x720) |
  | confirm_violence | 4943 | 1920x1080 (4851 lượt 1, 4943 lượt 2) |

  - Bốn số người dùng nêu (visual_logo 4688–4690, localize_logo 1309, text ≤ 833, confirm_violence 4851–4943) khớp với nguồn; lấy số lớn nhất của mỗi khoảng. Bốn bước còn lại có số đo trong cùng lần đo nên cũng được dùng.
  - Lần quét cả phim ngày 2026-10-17 đo `visual_logo` 4691 (cao hơn 1 MiB), nên số này được nâng lên 4691; `text` 831 và `localize_logo` 1309 không vượt.
  - `gore` và `violence` riêng lẻ (chỉ chọn một trong hai detector) chưa có số đo: giữ 4943.
  - Test `RecordedNumbersTests` đọc lại hai `summary.json` và `check.json` của A1d, rồi kiểm bảng này đúng từng số, nên không có số nào đoán.
- `scan_budget(stage, profile, fast_scan, frame)`: dùng đỉnh của bước **chỉ khi** job quét giống lúc đo (profile `careful`, "Quét nhanh" bật: OCR batch 8, fp16, logo routing bằng NVDEC) **và** khung video gốc nằm gọn trong khung clip đã đo (ngang hoặc dọc). Mọi trường hợp khác giữ 4943, và ghi lý do (`scan_peak_basis`): `stage_not_measured`, `scan_settings`, `frame_unknown`, `frame_not_measured`.
  - Các job thật gần đây đều là `careful`, quét nhanh bật, khung 1280x534/536, nên đều dùng được đỉnh của bước.
- `vram_decision(..., scan_peak_mib=…)`: công thức của A1b giữ nguyên, chỉ thay 4943 bằng đỉnh của bước làn xuất chạy cạnh:
  `tổng − max(đang dùng, nền + đỉnh của bước) − NVENC ≥ 256`.
- `stage_beside_export(...)`: làn chính chỉ bắt đầu một bước GPU trong lúc làn xuất đang chạy bản xuất khi
  `tổng − (đang dùng bây giờ + NVENC của bản xuất + đỉnh của bước) ≥ 256`.
  - "Đang dùng bây giờ" đã gồm bản xuất (nếu NVENC đã cấp phát), nhưng NVENC vẫn được cộng thêm đủ một lần, vì có thể NVENC chưa cấp phát xong. Trên máy này, việc tính hai lần không đổi kết quả: bước nhẹ còn dư hàng GB, `visual_logo` vẫn không vừa.
  - Không đọc được VRAM hoặc thiếu số đo: bước GPU chờ.

### Làn chính chờ bản xuất (`src/biliflow/scheduler.py`)
- `_beside_lane_export`: khi làn xuất đang bận, bước kế tiếp của làn chính:
  - bước CPU (`preflight`, `build_review`…): chạy luôn;
  - bước GPU vừa ngân sách: chạy cạnh bản xuất;
  - bước GPU nặng hơn: **chờ bản xuất xong**. Job quét ghi một sự kiện `STAGE_WAITS_FOR_EXPORT` (INFO, tiếng Việt, kèm số MiB và số job của bản xuất) mỗi lượt chờ.
  - Vẫn áp dụng khi người dùng chuyển "Xuất song song" sang "Tắt" lúc bản xuất đang chạy ở làn phụ.
- **Giữ chỗ cho làn xuất (`_lane_export`).** Làn xuất ghi bản xuất nó nhận, cùng NVENC cần, ngay trong khóa nó kiểm bước GPU còn chạy. `_lane_busy(EXPORT_LANE)` tính cả bản ghi này. Nhờ vậy, nếu bước quét kết thúc đúng lúc làn xuất vừa chọn mà chưa kịp nhận, làn chính vẫn thấy làn xuất bận và không bắt đầu bước nặng. Bản ghi xóa khi lượt của làn xuất kết thúc (`_run`, `finally`).
- **Thiếu VRAM lần đầu cạnh một bước chưa phải kết luận cuối.** Trước đây, bản xuất bị làn xuất từ chối một lần thì chạy tuần tự. Nay:
  - lần đầu thiếu VRAM cạnh một bước (thường là `visual_logo`): không ghi sự kiện, không xét lại cạnh chính bước đó (`_short_beside`);
  - ở ranh giới bước kế tiếp, làn chính xét lại bản xuất cho bước GPU sắp chạy (ví dụ `localize_logo`) với VRAM đọc mới;
  - thiếu VRAM lần nữa (ở ranh giới hay cạnh bước sau) thì chốt: `EXPORT_LANE_SEQUENTIAL` một lần, bản xuất chạy ở làn chính như cũ;
  - mọi lý do khác (ép x264, hồ sơ chưa xác nhận NVENC, khung chưa đo…) vẫn chốt ngay như A1b.
  - Không có vòng lặp chờ mãi: mỗi bản xuất chỉ được thêm đúng một lần xét.
- Sự kiện `EXPORT_LANE_PARALLEL` và `EXPORT_LANE_SEQUENTIAL` ghi thêm `scan_peak_stage`, `scan_peak_mib` (số đã dùng), `scan_peak_basis`.

### Không đổi
- Chỉ NVENC ở làn xuất; renderer không chạy x264 ở đó; đường trả bản xuất về làn chính; bộ canh treo.
- Biên 256 MiB; `NVENC_NEED_MIB`, `SCAN_PEAK_MIB`; vẫn cần hồ sơ phần cứng xác nhận NVENC; khung bản xuất phải ≤ 1920x1080.
- "Tắt" và lúc chưa có số đo: không đọc NVML, không sự kiện làn, như worker đơn (trừ khi một bản xuất đã chạy ở làn phụ trước khi tắt).
- Detector, ngưỡng, kết quả quét, byte của bản xuất. Khóa cache quét 10/10 bước giống 41169fc.

## Test
- Viết trước (RED trên 41169fc: lỗi import `_LaneExport`, `scan_budget`…), rồi code cho đạt.
- `tests/test_export_lanes.py` (7 test mới): đỉnh theo bước, mọi trường hợp giữ 4943, công thức cổng làn chính (đúng biên, khác chương trình tăng VRAM), không đọc được VRAM, số đo khớp bằng chứng.
- `tests/test_scheduler.py`:
  - phần dựng sẵn của `ExportLaneTests` tách thành `LaneFixture` (không đổi test cũ);
  - `StageBudgetLaneTests` (17 test, số như RTX 2060: 6144, nền 1200, NVENC 244, biên 256): bản xuất cạnh `text` (A1b từ chối); `visual_logo` chờ bản xuất rồi mới chạy, một sự kiện; `localize_logo` chạy ngay cạnh bản xuất; ca job 139 (bản xuất xếp lúc `visual_logo` chạy sang cạnh `localize_logo`); cổng đọc VRAM bây giờ; không đọc được VRAM; bước CPU không chờ; "Tắt" khi bản xuất đang chạy; giữ chỗ chống chạy đua giữa hai làn; xét lại một lần rồi chốt (ở ranh giới và cạnh bước sau); yêu cầu xuất mới xét lại từ đầu; job ngoài điều kiện đo giữ 4943; thiếu VRAM lần đầu rồi không còn bước GPU nào thì vẫn ghi lý do khi bản xuất chạy tuần tự; hủy hoặc trả về bản xuất thì bước đang chờ chạy tiếp; ffprobe lỗi được nhớ, không chạy lại mỗi giây.
- Kết quả (src của worktree, `.venv` của thư mục chính):
  - `tests.test_scheduler tests.test_export_lanes tests.test_export_lane_routes tests.test_job_import`: 163 test OK (sau các sửa của review, và lại sau khi nâng `visual_logo` lên 4691).
  - Bộ tập trung 28 module của A1b/A1c: 761 test OK, 5 bỏ qua, 332 giây (trên 13b59ee, trước các sửa nhỏ của review); chạy lại trên bản cuối (sau kiểm thật GPU và số 4691): **765 test OK, 5 bỏ qua, 344 giây**.

## Review độc lập
- Một agent review chỉ đọc (13b59ee): **APPROVE, không có CRITICAL hay HIGH.** Nó kiểm lại: không đường nào để bước GPU vượt ngân sách bắt đầu khi làn xuất đang giữ bản xuất, kể cả chạy đua giữa hai luồng; thứ tự khóa không đổi; bản ghi `_lane_export` luôn được xóa (hủy, tạm dừng, trả về, lỗi, tắt máy); không chờ mãi; "Tắt" và lúc chưa đo như cũ; khóa cache không đổi; bảng `STAGE_PEAK_MIB` tính lại từ bằng chứng khớp từng số.
- Đã sửa theo review:
  - LOW: thiếu VRAM lần đầu rồi không có bước GPU nào để xét lại thì bản xuất chạy tuần tự mà không có sự kiện. Nay `EXPORT_LANE_SEQUENTIAL` được ghi (lý do và số của lần thiếu đó) khi làn chính bắt đầu bản xuất.
  - LOW: chỉ nhớ khóa cuối của sự kiện chờ; nay xóa khi lượt của làn xuất kết thúc, và sửa chú thích kiểu.
  - LOW: video không ffprobe được thì mỗi giây chạy ffprobe một lần trong lúc chờ; nay nhớ cả kết quả lỗi (theo đường dẫn, cỡ, mtime).
  - LOW: thêm test hủy và trả về bản xuất khi bước nặng đang chờ.
- MEDIUM (đã đóng ngày 2026-10-17 bằng bước `measure`, xem mục kiểm thật): số đo theo bước chỉ lấy từ hai clip 10 phút. Báo cáo quét thật (chỉ đọc, job 100–152) cho thấy:
  - `visual_logo`: đỉnh CUDA trong báo cáo 4262–4271 MiB với phim 36–74 phút, gần như không đổi;
  - grounding (lệnh thứ hai của `localize_logo`, bước có 1309 MiB): đỉnh CUDA 835–1042 MiB tùy phim, nên 1309 có thể thấp hơn phim nặng nhất;
  - `text` không ghi bộ nhớ trong báo cáo.
  
  Vì vậy kiểm thật có thêm bước `measure`: quét cả phim của job 146 (phim có đỉnh grounding cao nhất, 1042 MiB) một mình và đo đỉnh từng tiến trình như A1b. Kết quả: `text` 831, `visual_logo` 4691 (vượt 1 MiB, đã nâng số lên), `localize_logo` 1309 (grounding, đỉnh CUDA 1042 đúng như báo cáo thật).
- Không sửa: `scheduler.py` dài 1593 dòng từ trước (nay khoảng 1720), vượt mức 800 dòng; tách phần làn xuất ra module riêng là một việc tái cấu trúc khác, không làm trong A1d.
- Khóa cache quét: script so `cache_dependencies.stage_source_paths` và SHA-256 từng file của mọi bước trong `stage_cache.CACHEABLE_STAGES`, cộng 9 file cố định của `_tree_fingerprint`, giữa worktree và `git archive 41169fc`: **CLEAN**, 10 bước, 36 file, 7 file cố định có mặt; `scheduler.py` và `export_lanes.py` không thuộc khóa nào.

## Đối chiếu với các lần từ chối thật
- `docs/evidence/a1d/replay-2026-10-15.md`: năm event `EXPORT_LANE_SEQUENTIAL` (2236–2641) tính lại bằng luật mới. Một lần vẫn tuần tự (cạnh `visual_logo`, đúng), hai lần được xét lại rồi chạy cạnh `localize_logo`, hai lần mở làn ngay. Với job 139, `localize_logo` bắt đầu ngay thay vì chờ khoảng 29 phút.

## Kiểm thật trên Control Center thử
- Script `scripts/a1d_check.py` (root tạm `temp\a1d-check-<giờ>`, cổng 8794, clip cắt bằng stream copy, video gốc chỉ được đọc; không gọi route nào của Control Center thật, chỉ đọc database của nó ở `mode=ro` và từ chối chạy nếu hàng đợi thật không trống và tạm dừng).
- Video: video gốc của job 146 (1280x720, chỉ đọc). Clip E1 và S1 6 phút, F1 là cả phim remux với một thẻ metadata khác (cùng khung, khác SHA-256).
- Lượt `run`: bản xuất E1 xếp trước phim S1 đang chờ `preflight`; lấy mẫu VRAM bằng nvidia-smi (0,2 s) và NVML (0,1 s).
- Lượt `measure`: quét F1 một mình (cache grounding dời đi trước), đỉnh từng tiến trình so với VRAM trong giây trước khi nó bắt đầu, cộng đỉnh CUDA trong báo cáo của `visual_logo` và grounding; kiểm tra mới `whole_film_within_the_recorded_peaks`.
- Kiểm tra: bản xuất cạnh `text` với đỉnh 833 của chính bước đó; chỉ ở làn xuất; hai làn cùng lúc; `visual_logo` chờ bản xuất (một sự kiện, bắt đầu sau khi bản xuất xong) hoặc vừa ngân sách và không có sự kiện; đỉnh VRAM dưới tổng; không có `STAGE_FAILED` hay log hết bộ nhớ; bản xuất `h264_nvenc`, `lane: parallel`, không fallback, qua kiểm tra "Xóa video gốc"; mọi FFmpeg dưới mức bình thường; hàng đợi thật trống và tạm dừng suốt lượt.
- **Đã chạy ngày 2026-10-17, 12:02–12:14, mọi kiểm tra đạt** (`docs/evidence/a1d/2026-10-17/README.md`, `check.json`, `vram-1s.csv`). Hàng đợi thật trống và tạm dừng suốt lượt; gốc thử đã gỡ.
  - `run`: bản xuất E1 mở ở làn xuất cạnh `text` với đỉnh 833 của chính bước đó (còn 4541 MiB); hai làn chạy cùng lúc 59,3 s. Ở ranh giới, `visual_logo` vừa ngân sách (`6144 − (694 + 244 + 4691) = 515`) nên chạy cạnh bản xuất 29,6 s, không có sự kiện chờ. **Đỉnh VRAM 5134 MiB (nvidia-smi), 5322 MiB (NVML)** trên 6144. Không OOM, không `STAGE_FAILED`. Bản xuất `h264_nvenc`, `lane: parallel`, không fallback, qua kiểm tra "Xóa video gốc"; mọi FFmpeg dưới mức bình thường.
  - Hôm nay nền VRAM chỉ 526 MiB (các ngày trước 950–1250), nên luật A1b cũng sẽ mở làn ở lượt này (còn 431). Ca chờ khi `visual_logo` không vừa được kiểm bằng test đơn vị và đối chiếu ở `replay-2026-10-15.md`.
  - `measure`: cả phim (2807 s) quét trong 240,8 s, `WAITING_REVIEW`.
- Sửa nhỏ trong script lúc chạy: `store.events(limit=…)` tối đa 1000 (trước ghi 2000, báo lỗi ở bước `report`); `check.json` ghi thêm `settings` của lượt quét để test đọc lại.

## Đang dở
- Không còn.

## Bước tiếp theo
1. Điều phối: full suite trên nhánh, rồi gộp theo lời người dùng. Agent của A1d không push, không merge.
2. Sau khi gộp và khởi động lại Control Center thật, A1d có hiệu lực ngay ("Tự động" và hồ sơ đã đo trên máy thật).

## Cách kiểm
- Lệnh test (trong worktree; các module liên quan):
  ```
  $env:PYTHONPATH = "E:\DungChung\BiliFlow\temp\wt-a1d\src"
  E:\DungChung\BiliFlow\.venv\Scripts\python.exe -m unittest tests.test_scheduler tests.test_job_store tests.test_final_renderer tests.test_export_encoder tests.test_render_graph_file tests.test_control_center tests.test_cache_dependencies tests.test_stage_cache tests.test_dashboard_v2_review tests.test_dashboard_v2_contract tests.test_dashboard_v2_phone tests.test_dashboard_v2_phone_hardening tests.test_dashboard_v2_route tests.test_dashboard_v2_frontend tests.test_dashboard_v2_status tests.test_dashboard_v2_downloads tests.test_hardware_manager tests.test_hardware_routes tests.test_job_import tests.test_skip_export tests.test_export_guards tests.test_job_delete tests.test_source_cleanup tests.test_source_archive tests.test_job_purge tests.test_export_lanes tests.test_export_lane_routes tests.test_phone_tailscale
  ```
- Khóa cache: so `stage_source_paths` và SHA-256 từng file của mọi bước trong `CACHEABLE_STAGES`, cộng các file cố định của `_tree_fingerprint`, giữa worktree và `git archive 41169fc`. Script nằm trong scratchpad của session; viết lại theo mô tả này nếu cần.
- Kiểm thật (chỉ khi người dùng cho phép và hàng đợi thật trống, tạm dừng), từ worktree:
  ```
  $py = "E:\DungChung\BiliFlow\.venv\Scripts\python.exe"
  & $py scripts\a1d_check.py setup   --install E:\DungChung\BiliFlow --code . --source <video ≤1080p> --film
  & $py scripts\a1d_check.py profile --install E:\DungChung\BiliFlow --root <root setup in ra>
  & $py scripts\a1d_check.py ready   --install E:\DungChung\BiliFlow --root <root>
  & $py scripts\a1d_check.py run     --install E:\DungChung\BiliFlow --root <root>
  & $py scripts\a1d_check.py measure --install E:\DungChung\BiliFlow --root <root>
  & $py scripts\a1d_check.py report  --install E:\DungChung\BiliFlow --root <root> --evidence docs\evidence\a1d\<ngày>
  & $py scripts\a1d_check.py remove  --install E:\DungChung\BiliFlow --root <root>
  ```

## Chờ người dùng
- Bấm "Tiếp tục hàng đợi" trên Control Center thật (đã tạm dừng cho lượt đo lúc 12:02).
- Quyết định gộp nhánh vào main.

## Lưu ý cho người tiếp quản
- **Số đo gắn với code và máy lúc đo.** Đổi model, batch hay độ chính xác của một bước (ví dụ nhánh gpupre của OCR) thì phải đo lại bước đó (`scripts/a1b_vram_measure.py`) và cập nhật `STAGE_PEAK_MIB`; test `RecordedNumbersTests` sẽ hỏng nếu bảng không khớp bằng chứng mới. Chỉ đo một máy (RTX 2060 6 GB, driver 617.42), như A1b.
- Khung video gốc lớn hơn khung đã đo (ví dụ 4K) giữ 4943 như A1b; số này cũng chỉ đo ở ≤ 1080p.
- Trong lúc làn chính chờ bản xuất, nó đọc NVML mỗi lần nhìn lại hàng đợi (mỗi giây). Bước GPU nặng không chen vào giữa chừng: nó chỉ bắt đầu khi công thức cho phép, hoặc khi bản xuất xong.
- Làn chính chờ chứ không lấy job khác: thứ tự A1a giữ nguyên (các bước quét theo thứ tự bấm).
- `queue_order()` trên dashboard vẫn là thứ tự A1a.
- **Không sửa:** `AGENTS.md`, `CHANGELOG.md`, `docs/PROJECT_STATUS.md`, `docs/SESSION_HANDOFF.md`, `final_renderer.py`, mọi file thuộc khóa cache quét, thiết lập encode, ngưỡng phát hiện, `annotations\`, các file của session D (`dashboard_v2/review*.js`, `review_workflow.py`, `review_decision_carry.py`).
- Thư mục chính đang chạy Control Center thật: không sửa file ở đó, không gọi route nào của nó, không đo phần cứng với root là thư mục cài.
