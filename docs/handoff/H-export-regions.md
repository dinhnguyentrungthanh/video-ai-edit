# Bàn giao H – xuất nhiều vùng làm mờ không còn treo, và bộ canh treo FFmpeg
- Nhánh: fix/export-many-regions (từ main a63119b) · Commit: 49fe768 (code), 2922a0a (sửa theo review), commit tài liệu kèm bàn giao này (xem `git log -1`) · Worktree: `temp\wt-export-regions` · Cập nhật: 2026-10-14
- Chạy ở: PC · Trạng thái: **xong; mọi cổng đều đạt. Chờ điều phối chạy full suite và gộp.** Chưa push, chưa merge.
- Bằng chứng: `docs/evidence/export-regions/README.md` (số đo, ngưỡng canh treo và giới hạn của số đo).

## Đã xong

### 1. Nguyên nhân treo (job 134)
- Đồ thị cũ lồng mỗi vùng làm mờ vào vùng trước nó (split → crop/delogo → gblur → mask → alphamerge → overlay, rồi
  vùng sau split chính overlay đó). Công việc mỗi khung của FFmpeg 9.0.1 theo số đường từ đầu vào tới đầu ra của
  đoạn, và lồng nhau nhân số đường lên ×2/×3/×4 mỗi vùng.
- Job 134: một CUT mở đầu 10 s, rồi một đoạn giữ lại có 25 vùng (37 thao tác làm mờ): 4^25 ≈ 1,1 × 10^15 đường.
  FFmpeg không bao giờ ra được khung nào; heartbeat của bước render vẫn tươi nên hàng đợi kẹt mãi.

### 2. Cách nối mới (`src/biliflow/blur_layout.py`, `blur_filter.regional_blur_branch`)
- Đoạn có ≤ 1024 đường lồng nhau (`NESTED_PATH_LIMIT`, 4^5, nhiều nhất từng xuất trước job 134) **giữ nguyên đồ thị
  cũ từng ký tự**.
- Đoạn lớn hơn dùng cách nối "shared": mỗi phiên bản hình có một `split=N` nuôi đường chính và mọi nhánh đọc nó.
  Mỗi nhánh đọc hình sớm nhất đã chứa mọi thay đổi của các vùng trước có thể chạm vào chỗ nó đọc trong lúc cả hai
  cùng bật (thời gian chạm nhau, sai số 0,001 s; hộp nới 4 px cho làm tròn chroma và overlay ở vị trí lẻ, cộng 8 px
  lề đọc của cách phủ; làm mờ toàn khung chạm mọi thứ). Không nhánh sau nào đọc hình trước overlay vùng đầu tiên
  (định dạng điểm ảnh). Số đường cộng lại thay vì nhân: đoạn của job 134 còn 292 đường, đọc đúng hoàn toàn.
- Đoạn có **hơn 10 vùng** (`EXACT_REGION_LIMIT`) có ngân sách đường (4096, hoặc chi phí sàn nếu lớn hơn). Vượt
  ngân sách thì một nhánh đọc hình cũ hơn: vùng đó **vẫn mờ toàn bộ**, chỉ khác điểm ảnh ở chỗ chồng lên vùng trước
  đang bật (mờ của hình chưa có vùng trước). Manifest đếm số vùng này:
  `encoding.filter_graph = {layout, paths, regions_reading_older_picture}`. Kế hoạch ≤ 10 vùng luôn đọc đúng.
- Thử đọc đúng hoàn toàn (không ngân sách) với kế hoạch test 60 vùng: không ra khung nào trong 60 s, bộ canh treo
  dừng nó. Nên ngân sách là cần thiết, không chỉ để nhanh.

### 3. Bộ canh treo (`src/biliflow/render_watchdog.py`, `final_renderer.py`, `scheduler.py`)
- `run_watched` thay `subprocess.run` cho lệnh render. FFmpeg được coi là đang chạy khi khối cuối của file
  `-progress` đổi (frame, out_time, size) **hoặc** số byte đã đọc tăng (psutil). Khoảng cách hai lần kiểm > 30 s
  (máy ngủ, tiến trình bị đóng băng) chỉ tính là một lần kiểm.
- **Ngưỡng:** `stall_limit = (600 s + 4 s × số giây của CUT dài nhất) × (2 ở làn xuất song song của A1b)`.
  - Dựa trên số đo `docs/evidence/export-regions/quiet-stretches.jsonl`: bản xuất 1080p, 60 vùng, CUT 30 s và
    20 s. Máy rảnh: lâu nhất 1,0 s không tiến và không đọc. CPU 100 % (thêm 4 lượt x264 1080p, chậm 20,6 lần):
    lâu nhất 98,3 s. 600 s ≈ 6 lần con số đó. Thêm 4 s mỗi giây CUT cho đoạn FFmpeg giải mã CUT mà không ra khung.
    ×2 ở làn song song là biên an toàn, **không phải số đo**.
  - **Giới hạn của số đo:** một máy, mỗi điều kiện một lần; nguồn tổng hợp (testsrc2), 70 s đầu ra, không phải
    phim 2 giờ; tải là x264 trên CPU, không phải lượt quét GPU thật; không đo NVENC, không đo làn song song; không
    đo bước +faststart của file nhiều GB (bước đó vẫn đọc nên vẫn tính là chạy); không đo máy ngủ.
- Khi dừng: kill FFmpeg (chờ nó thoát tối đa 30 s), ghi `<progress>.stalled.json` trước (schema 1: message, lane,
  idle_seconds, encoded_seconds, stderr_tail = 4000 ký tự cuối stderr của FFmpeg, cũng in vào nhật ký bước), rồi
  mới xóa file partial; lỗi ở hai bước này không che được lỗi treo. Sau đó raise `RenderStalled`. **Không** thử lại x264 sau NVENC, **không** ghi bản ghi hoãn xuất
  (export deferral) ở làn song song.
- Scheduler (`_run_process`): khi bước render thoát lỗi, bản ghi treo (nếu có) thắng mọi thứ khác, kể cả bản ghi
  hoãn: bước FAILED với thông báo tiếng Việt, ví dụ "Xuất video bị dừng: FFmpeg không tiến triển trong 10 phút
  (chưa xuất được khung hình nào). BiliFlow đã dừng FFmpeg để hàng đợi chạy tiếp; …". Bản ghi bị xóa khi
  `queue_render`, khi dọn bước bị gián đoạn và khi render bắt đầu.

### 4. Cổng

| Cổng | Kết quả |
|---|---|
| Unit test bộ nối (`tests/test_blur_layout.py`, 13): đồ thị đúng dạng, đếm đường trên chính đồ thị, phụ thuộc, lề, sai số thời gian, toàn khung, ≤ 10 vùng đọc đúng, ngân sách, dạng job 134, 300 đoạn ngẫu nhiên (có bộ kiểm độc lập: đúng những vùng manifest đếm là thiếu một thay đổi trước đó, và không vùng nào khi ≤ 10 vùng) | OK |
| Tích hợp lavfi 30 và 60 vùng (`tests/test_export_regions.py`): xong trong vài giây, mọi vùng giữ < 5 % chi tiết nguồn khi bật, vùng không bị mờ khi tắt; hai cách nối cho cùng framemd5 trên yuv420p, yuv420p10le, yuv444p | OK (≈ 20 s) |
| x264 byte + framemd5, 1–10 vùng, main a63119b vs nhánh (`scripts/export_regions_gate.py` → `gate-1-10-regions.json`) | **12/12 giống từng byte**, 7 kế hoạch trong đó dùng cách nối mới; chạy hai lần (trước và sau khi sửa theo review), file giữ lần chạy trên 2922a0a (mỗi bên được kiểm là nạp đúng mã của mình) |
| 150 kế hoạch ngẫu nhiên × 4 định dạng nguồn, framemd5 lồng vs shared | giống hệt ở mọi trường hợp cả hai chạy được (3 kế hoạch yuv410p mà đồ thị lồng tự lỗi EINVAL; trong thực tế chúng vẫn dùng đồ thị lồng như main) |
| Job 134 thật, 60 s đầu, x264, trong `temp\h-job134` (`job-134-first-60s.json`) | COMPLETED trong 49,5 s, qua kiểm giải mã toàn bộ; 292 đường, 0 vùng đọc hình cũ; vùng logo cả phim và thẻ phụ đề mờ đúng chỗ, đúng lúc |
| Bộ canh treo (`tests/test_render_watchdog.py`, 20): dừng FFmpeg thật trong đồ thị 32 vùng lồng; để yên bản render thật chạy theo thời gian thực quá ngưỡng; đọc nguồn tính là chạy; khoảng ngủ máy; file progress đọc hụt một lần không tính là tiến; FFmpeg không chịu thoát sau kill; stderr UTF-8 | OK |
| Làn song song (`tests/test_scheduler.py::ExportLaneTests`, `tests/test_final_renderer.py`): treo ở làn song song → FAILED, không trả về làn chính, không bản ghi hoãn; bản ghi treo thắng bản ghi hoãn; ngưỡng làn song song gấp đôi | OK |
| Khóa cache quét (`tests/test_cache_dependencies.py`) | không đổi (thêm 3 file mới vào nhóm control plane) |
| Test tập trung trên 2922a0a: render_watchdog 20, blur_layout 13, export_regions 2, final_renderer 54, scheduler 88, blur_filter 21, cache_dependencies 14, export_guards 25, export_identity 28 | 265 OK. Một lần chạy trước đó có 1 lỗi chập chờn ở fixture hai làn có sẵn của A1b (`wait_for` 10 s chờ lượt quét bắt đầu, lúc CPU 99 % vì Control Center đang quét); chạy lại OK |

- Không đổi bộ dò, quyết định duyệt, ngưỡng hay cách chọn encoder. NVENC chỉ cần qua kiểm sau xuất (không đổi).
- Job 134 (thật) đang PAUSED ở bước render. Sau khi gộp và khởi động lại Control Center, người dùng bấm "Tiếp tục"
  là bản xuất chạy với đồ thị mới. Agent không bấm.

### 5. Review độc lập (2 agent, chỉ đọc)
- Không có lỗi CRITICAL/HIGH; không tìm ra kế hoạch ≤ 10 vùng nào mà cách nối mới đổi điểm ảnh.
- Đã sửa (2922a0a): thứ tự ghi bản ghi treo/xóa partial; giữ stderr của FFmpeg; chờ kill có hạn; đọc stderr UTF-8;
  file progress đọc hụt; test treo thật 32 vùng; test bản render khỏe chạy quá ngưỡng; nới thời gian test; gate kiểm
  mỗi bên nạp đúng mã; docstring số đo mỗi đường.
- Ghi lại thay vì sửa: khi > 10 vùng và một vùng đọc hình cũ, điểm ảnh có thể khác **trong cả vùng đó** (không chỉ
  chỗ chồng), và một vùng gblur thường đè lên vùng phủ (logo) sẽ cho thấy logo bị mờ chứ không bị phủ ở chỗ chồng.
  Đổi luật ngân sách để tránh việc này có thể làm số đường nhân lên lại (nguyên nhân treo).

### 6. Giới hạn đã biết
- Đoạn ≤ 10 vùng xếp thành một chuỗi chồng nhau thì đọc đúng vẫn không bớt được đường: chậm như trước (bản xuất vẫn
  ra khung đều nên bộ canh treo không dừng nó).
- Ngưỡng và ngân sách tính theo từng đoạn; kế hoạch nhiều CUT cộng số đường các đoạn như trước.
- Treo do chính NVENC (driver kẹt) cũng làm bước FAILED; "Thử lại bước lỗi" chạy lại cùng lựa chọn encoder.
- CUT dài làm bộ canh treo dừng một ca treo thật chậm hơn (CUT 20 phút: 90 phút).
- Bản xuất trong review UI độc lập không xóa bản ghi treo cũ của nó; lần render sau sẽ xóa.

### 7. Kiểm tra trên máy đang chạy job thật
- Mọi lượt chạy có FFmpeg đều ở mức ưu tiên dưới bình thường. Bản xuất job 134 dùng x264 hai luồng vì Control Center
  đang quét. Không chạy thêm bài thử tải nào sau yêu cầu của người dùng (CPU 100 %, bản xuất thật tụt từ 7x xuống
  1,3x lúc 4 tiến trình tải chạy). `temp\h-quiet` và `temp\h-job134` đã xóa; không commit file nào từ đó.

## File đã đổi
- Mới: `src/biliflow/blur_layout.py`, `src/biliflow/render_watchdog.py`, `scripts/export_regions_gate.py`,
  `tests/test_blur_layout.py`, `tests/test_render_watchdog.py`, `tests/test_export_regions.py`,
  `docs/evidence/export-regions/` (README, 2 kết quả JSON, số đo, 2 script đo).
- Sửa: `src/biliflow/blur_filter.py` (tách `regional_blur_branch`, `regional_blur_paths`),
  `src/biliflow/final_renderer.py`, `src/biliflow/scheduler.py`, `scripts/benchmark_export_steps.py` (đo cả
  `run_watched`), `tests/test_final_renderer.py`, `tests/test_scheduler.py`, `tests/test_blur_filter.py`,
  `tests/test_cache_dependencies.py`.

## Việc tiếp theo
- Điều phối: chạy full suite trên bản gộp, rồi gộp.
- Chưa đo: NVENC và làn song song với bộ canh treo trên máy thật; một bản xuất phim dài thật có hơn 10 vùng.
