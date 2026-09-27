# BiliFlow Control Center — audit và kế hoạch triển khai

Ngày đánh giá: 2026-09-24

## 1. Kết luận kiểm tra hiện trạng

BiliFlow V0.4.0 đã có pipeline local hoàn chỉnh từ scan đến review và export, nhưng chưa có bộ điều phối nhiều video. Review web hiện tại chỉ mở một `review-queue.json` trong lúc process `review-ui` còn chạy. Không có service chạy nền, SQLite, input watcher, checkpoint toàn pipeline, pause/cancel theo job hoặc tự phục hồi sau Windows restart.

Kiểm tra hiện tại:

- 83/83 unit test đạt; `pip check` sạch.
- 8/8 model được policy `free-commercial-safe-local` cho phép; 0 model bị chặn.
- Không có web listener tại 8765, 8766 hoặc Codex App Server 4500.
- Dữ liệu trên ổ E: input khoảng 1,44 GiB; reports 0,17 GiB; previews 0,21 GiB; output 1,75 GiB; models 5,80 GiB.
- Cleanup vẫn ở chế độ dry-run; chỉ có hai ảnh temp cũ là ứng viên dọn.
- Sáu nguồn regression còn đủ trong `input`; dataset có 206 quyết định review.

## 2. Mức sẵn sàng của detection

### Quảng cáo chữ

Trạng thái: dùng được trong production có human review.

- EasyOCR + semantic model đã bắt được `i999.ai`, `NGUONC.COM`, Netflix title/dubbing và banner tiếng Việt trên video thật.
- Track được nối theo cả vị trí và nội dung, giảm việc gộp nhầm nhiều phụ đề thành một quảng cáo dài.
- Blur lấy bounding box riêng của từng video, có tail/look-ahead và `vertical_only` cho banner chạy hết chiều ngang.
- Vẫn có thể bỏ sót chữ cách điệu, chữ quá nhỏ, opacity thấp hoặc xuất hiện ngắn hơn chu kỳ lấy mẫu.

### Logo, watermark và brand ident

Trạng thái: pilot tốt, chưa đủ bằng chứng để tự xóa không cần người duyệt.

- Crop vùng chồng lấn giúp bắt logo nhỏ; temporal grouping gom logo cố định thành một candidate xuyên phim.
- Video `#874-B2` bắt NewGates Anime từ 6,000–429,312 giây và end-card 429,312–445,312 giây.
- Florence-2 đã thu persistent logo về một vùng `97,13,240,224`, không kéo sang title bên phải.
- Benchmark Florence hiện chỉ có 4 positive và 3 negative. Localization recall là 4/4 nhưng Florence raw grounding cũng báo vùng trên 3/3 negative; Qwen gate đã ngăn việc gọi region cho các negative này.
- Qwen vẫn có thể nhầm logo trong nội dung phim với logo ngoài phim hoặc bỏ logo mới chưa từng gặp. Cần thêm ít nhất 5–10 video đa nguồn và ground truth theo cửa sổ timeline trước khi cho phép auto-BLUR/CUT.

### 18+

Trạng thái: candidate generator, human review bắt buộc.

- Animation policy V4: 43 mẫu, recall 100%, specificity 96,97%, precision 90,91%; mẫu còn nhỏ và mới tập trung một miền dữ liệu.
- Live action baseline: bắt 9/10 positive và 2/2 negative; negative set quá nhỏ để kết luận production.
- Chưa đạt field gate 30 positive + 60 hard negative cho mỗi phong cách từ ít nhất hai video độc lập.

### Blood/gore

Trạng thái: đạt POC 80–85%, chưa đạt field gate.

- Live action: recall 80%, specificity 100%, precision 100% trên 10 positive + 20 negative.
- Animation policy hiện dùng hai tầng và temporal gate: recall 80%, specificity 80%, balanced accuracy 80% trên 10 positive + 20 negative.
- Có nguy cơ bỏ máu ít, vết thương nhỏ hoặc cảnh rất ngắn; có nguy cơ báo dư cảnh đỏ/tối/người nằm.

### Violence

Trạng thái: nhánh mạnh nhất trong ba nhóm an toàn, vẫn cần review.

- Animation ensemble: 126 mẫu, recall 86,44%, precision 80,95%, specificity 82,09%, balanced accuracy 84,27%.
- Holdout animation: recall 85,71%, specificity 83,33%.
- Live-action ViT integrated: recall 90%, specificity 70% trên 10 positive + 20 negative; Qwen confirm nhỏ 11 clip đạt recall 100%, specificity 85,71%.
- Trên `#874-B2`, shared scan quét 890 frame trong 50,477 giây, giữ 0 adult, 0 gore và 1 violence. Candidate violence vẫn cần người xem vì model ưu tiên recall.

### Kết luận chất lượng

Hệ thống hiện phù hợp để **tìm candidate và giảm thời gian xem tay**, không phù hợp để tuyên bố video sạch hoặc tự sửa toàn bộ mà không duyệt. Quảng cáo chữ/blur đã trưởng thành nhất về edit; violence có số đo tốt nhất; logo đã xử lý được lỗi NewGates nhưng benchmark đa thương hiệu còn thiếu; 18+ và gore cần mở rộng field set.

## 3. Chế độ vận hành: chạy theo phiên, không chạy 24/7

BiliFlow Control Center không cài Windows service và không tự khởi động cùng máy. Người dùng mở bằng một launcher, ví dụ `Start-BiliFlow.cmd` hoặc `Start-BiliFlow.ps1`. Launcher khởi động backend local, scheduler và trình duyệt. Khi người dùng chọn `Tắt BiliFlow`, toàn bộ thành phần được shutdown có kiểm soát và không còn process nền.

Một file HTML đứng riêng không thể chạy Python, FFmpeg hoặc model AI do giới hạn bảo mật của trình duyệt. HTML sẽ là giao diện do backend Python local phục vụ; launcher là điểm bắt đầu/kết thúc toàn phiên.

```text
Double-click Start-BiliFlow.cmd
  -> kiểm tra không có instance trùng
  -> khởi động backend tại 127.0.0.1:8765
  -> khởi động scheduler ở trạng thái người dùng đã lưu
  -> mở trình duyệt

Nút "Tắt BiliFlow" hoặc Stop-BiliFlow.cmd
  -> ngừng phát stage mới
  -> dừng worker theo chế độ đã chọn
  -> ghi checkpoint/job state
  -> đóng AI Supervisor nếu đang mở
  -> đóng web server
  -> giải phóng port, CPU, RAM và VRAM
```

Đóng riêng tab trình duyệt không nên tự terminate backend vì có thể là thao tác nhầm trong lúc render. Việc tắt toàn bộ phải đi qua nút `Tắt BiliFlow`, `Stop-BiliFlow.cmd`, hoặc đóng cửa sổ launcher và xác nhận chế độ shutdown.

## 4. Kiến trúc Control Center

```text
Browser local / VPN riêng
          |
          v
Control Center web (nhẹ, chạy lâu dài)
          |
          +---- SQLite trên ổ E
          |       jobs, stages, artifacts, decisions, events
          |
          +---- Scheduler
          |       1 GPU slot, 1 render slot, CPU jobs giới hạn
          |
          +---- Workers theo process
          |       scan -> localize -> build review -> export
          |
          +---- Review UI theo từng job
          |
          +---- AI Supervisor theo yêu cầu
                  audit report/logic, không tự quyết định edit
```

### Thành phần tồn tại trong một phiên BiliFlow

Chỉ hai phần tồn tại trong lúc người dùng đã mở BiliFlow:

1. Web service: phục vụ dashboard/API, đọc SQLite và thumbnail; gần như không dùng GPU.
2. Scheduler: ngủ khi không có việc; thức khi có job mới hoặc retry đến hạn.

Khi BiliFlow chưa được mở hoặc đã shutdown, cả web service lẫn scheduler đều không chạy. Model AI, FFmpeg và Codex AI Supervisor cũng không chạy liên tục trong phiên: worker chỉ khởi động khi đến bước tương ứng, giải phóng model/VRAM sau khi hoàn tất, rồi kết thúc process.

### Input watcher

- Theo dõi `input` nhưng không xử lý ngay khi file vừa xuất hiện.
- Chờ kích thước và thời gian sửa không đổi tối thiểu 60 giây, đồng thời mở file đọc được.
- Tính SHA-256 và dùng hash làm khóa chống job trùng.
- Video mới mặc định ở `DISCOVERED`; tùy cấu hình mới tự chuyển sang `QUEUED`.
- Metadata hoặc content style không chắc chắn chuyển sang `NEEDS_METADATA`, không tự đoán.

## 5. State machine cho từng video

```text
DISCOVERED
  -> NEEDS_METADATA | QUEUED
  -> PREFLIGHT
  -> SCANNING_SAFETY
  -> SCANNING_TEXT
  -> SCANNING_LOGO
  -> LOCALIZING_REGIONS
  -> AI_AUDIT_QUEUED (chỉ khi rule kích hoạt)
  -> WAITING_REVIEW
  -> READY_TO_EXPORT
  -> RENDER_QUEUED
  -> RENDERING
  -> VERIFYING
  -> COMPLETED
```

Các trạng thái phụ: `PAUSE_REQUESTED`, `PAUSED`, `STOP_AFTER_STAGE`, `CANCEL_REQUESTED`, `CANCELLED`, `FAILED_RETRYABLE`, `FAILED_FINAL`, `INTERRUPTED_RECOVERABLE`.

## 6. Cơ chế chạy, dừng và khôi phục

### Tắt toàn bộ BiliFlow

Nút tắt toàn bộ đưa ra hai lựa chọn rõ ràng:

1. `Tắt an toàn sau bước hiện tại`: ngừng phát stage mới, chờ stage đang chạy hoàn tất, ghi trạng thái rồi đóng toàn bộ ứng dụng.
2. `Dừng ngay và tắt`: gửi cancel tới worker, quarantine artifact partial, ghi checkpoint nếu có rồi đóng ứng dụng.

Nếu đang render, lựa chọn dừng ngay loại file `.partial`; edit plan và quyết định review vẫn được giữ để render lại khi mở BiliFlow lần sau.

### Pause scheduler toàn hệ thống

- Không phát job/stage mới.
- Worker đang chạy tiếp tục đến checkpoint an toàn.
- Web và review vẫn truy cập được.

### Dừng sau bước hiện tại

- Job hoàn thành scanner hoặc localizer đang chạy, ghi report/checksum rồi chuyển `PAUSED`.
- Đây là lựa chọn mặc định vì không làm mất lượt GPU dài vừa chạy.

### Pause ngay một video

- Scanner nhận cancel token tại ranh giới batch/window, ghi cursor và thống kê partial nếu stage hỗ trợ resume.
- Nếu stage chưa hỗ trợ resume, artifact partial bị đánh dấu `INCOMPLETE`; khi tiếp tục sẽ chạy lại riêng stage đó, không chạy lại các stage đã hoàn tất.
- Review đang chờ không tiêu thụ tài nguyên nên pause chỉ đổi trạng thái.

### Dừng khi đang render

FFmpeg final encode không nên nối tiếp từ giữa file. `Pause ngay` sẽ:

1. gửi terminate có thời gian chờ;
2. xóa hoặc quarantine file `.partial`;
3. giữ nguyên edit plan đã duyệt;
4. khi resume, render lại final từ đầu.

### Cancel job

- Dừng worker của riêng job đó; không ảnh hưởng video khác.
- Giữ source, quyết định review, report hợp lệ và audit log.
- Không tự xóa output đã hoàn tất.

### Emergency stop

- Dừng phát job mới và terminate toàn bộ worker group.
- Chỉ dùng khi máy quá nóng hoặc hệ điều hành sắp tắt.
- Lần khởi động sau chuyển các job đang chạy thành `INTERRUPTED_RECOVERABLE`.

### Khôi phục sau restart

- Mỗi worker gửi heartbeat 5–10 giây.
- Khi Control Center khởi động, job có heartbeat cũ được audit artifact và checksum.
- Stage có output hoàn chỉnh được bỏ qua; stage partial chạy lại hoặc resume từ checkpoint.
- Job không bao giờ được đánh `COMPLETED` chỉ dựa trên trạng thái DB; phải có manifest và artifact hợp lệ.

## 7. Điều phối tài nguyên

- RTX 2060 6 GB: một GPU worker tại một thời điểm.
- Final render: một job tại một thời điểm.
- CPU/background: giới hạn theo nhiệt độ và RAM; mặc định một scan CPU phụ hoặc một web service.
- Review UI không giữ model trong VRAM.
- Scheduler kiểm tra ổ E trước mỗi stage và không nhận job mới khi dưới ngưỡng policy.
- Thêm thermal guard: không phát stage GPU mới khi GPU vượt ngưỡng cấu hình; job đang chạy được phép kết thúc batch hiện tại.

## 8. Màn hình web

### Tổng quan

- Hàng đợi theo trạng thái, tiến độ %, stage hiện tại, thời gian ước tính.
- GPU/CPU/RAM/nhiệt độ, dung lượng ổ E và worker đang giữ resource slot.
- Nút `Xử lý tất cả`, `Tạm dừng nhận job`, `Dừng sau bước hiện tại` và `Tắt BiliFlow`.

### Trang video

- Metadata, checksum, content style, model revision và timeline coverage.
- Nút `Bắt đầu`, `Pause`, `Dừng sau bước`, `Cancel`, `Retry stage`, `Mở review`.
- Log theo event có giới hạn; không hiển thị secret.
- Danh sách artifact và link output.

### Review

- Tái sử dụng bốn quyết định KEEP/BLUR/CUT/NEEDS_MORE_CONTEXT.
- Bulk accept chỉ nhận đề xuất đang lọc.
- Không mở `Hoàn tất và xuất` khi còn unresolved.

### Lịch sử

- Một canonical job cho mỗi source SHA-256; các lần quét lại là revision của job.
- 15 queue lịch sử hiện có phải được nhập thành revision, không hiển thị như 15 video độc lập.

## 9. AI Supervisor

- Chạy theo yêu cầu, không chạy 24/7 và không thay local detector.
- Kích hoạt khi coverage thiếu, detector mâu thuẫn, candidate count bất thường, stage retry nhiều lần hoặc người dùng bấm `AI kiểm tra`.
- Mặc định chỉ nhận JSON/report/log; thumbnail chỉ được chia sẻ khi người dùng bật quyền cho job.
- Ghi kết quả có schema vào `ai-audit.json`; không tự tạo quyết định BLUR/CUT.
- Nếu Codex hết hạn mức hoặc mất mạng, job vẫn đi tới review local.

## 10. Lưu trữ dài hạn

- Source và output không tự xóa.
- Temp 24 giờ, failed work 7 ngày, preview 30 ngày; trước mắt vẫn dry-run.
- Sau khi BiliBili upload và playback được xác minh: đóng gói report/queue/manifest thành archive có checksum; có thể tạo HEVC archive khi encoder/driver đã được xác minh.
- Chỉ đưa output lớn vào danh sách dọn sau khi archive qua full decode, checksum và thời gian an toàn; thao tác xóa vẫn cần policy rõ.

## 11. Lộ trình triển khai

### Phase 0 — launcher và single-instance

- `Start-BiliFlow.cmd` cho thao tác double-click.
- `Stop-BiliFlow.cmd` cho trường hợp không mở được dashboard.
- Instance lock để không mở hai backend trên cùng database/port.
- Shutdown API có token local và quy trình graceful/cancel rõ ràng.
- Xác nhận sau shutdown không còn process Python, FFmpeg, Codex App Server hoặc port 8765 của BiliFlow.

### Phase A — nền tảng job

- SQLite schema + migration.
- Import sáu source và các queue canonical hiện có.
- Source hash, revision, artifact registry và event log.
- Unit test duplicate-job và migration.

### Phase B — web dashboard chỉ đọc

- Trang tổng quan, trang video, resource telemetry.
- Không chạy worker hoặc sửa quyết định trong bước đầu.
- Xác nhận import không làm thay đổi report cũ.

### Phase C — scheduler và worker

- Process supervisor, GPU/render slot, heartbeat.
- Start/pause/stop-after-stage/cancel/retry.
- Checkpoint ở batch/window cho scanner mới; stage cũ restart riêng stage.
- Recovery test bằng cách kill worker và restart Control Center.

### Phase D — review và export

- Nhúng review hiện tại theo job.
- Chuyển export daemon thread thành worker process bền vững.
- Test đóng browser/server trong khi render rồi phục hồi đúng trạng thái.

### Phase E — watcher và profile

- Stable-file detection, metadata gate.
- Profile `careful` mặc định và `fast` cho nguồn đã có regression tốt.
- Không auto-start video mới cho tới khi người dùng bật setting.

### Phase F — AI Supervisor

- Codex App Server qua stdio, persistent thread ID và approval handling.
- Rule escalation, structured `ai-audit.json`, rate-limit/offline fallback.
- Không gửi thumbnail nếu job chưa được cấp quyền.

### Phase G — reliability/retention

- Graceful shutdown theo phiên, recovery sau lần tắt không an toàn và thermal guard. Không tạo Windows startup task.
- Archive/upload-success retention sau khi BiliBili POC hoàn tất.
- Test mất điện giả lập, ổ gần đầy, job trùng và output partial.

## 12. Acceptance criteria trước khi dùng hằng ngày

- 100% job sống sót qua restart ở ranh giới stage; không job trùng theo source hash.
- Pause/cancel một video không ảnh hưởng video khác.
- Không có hai GPU worker hoặc hai final render chạy đồng thời.
- Khi phiên đang mở nhưng idle, web service không giữ VRAM và CPU gần 0.
- Sau `Tắt BiliFlow`, không còn backend, watcher, scheduler, worker, FFmpeg, AI Supervisor hoặc listener cổng 8765.
- Đóng nhầm tab trình duyệt không làm hỏng job đang chạy; dashboard mở lại được bằng URL trong cùng phiên.
- Queue/decision cũ không bị sửa khi import.
- Output chỉ `COMPLETED` sau giới hạn 3,5 GB, A/V probe, full decode và source checksum.
- Chạy thử ít nhất 5 video qua dashboard; audit false negative quảng cáo/logo và ba nhóm safety trước khi bật auto-start watcher.
