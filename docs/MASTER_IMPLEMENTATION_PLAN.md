# BiliFlow — Master implementation plan

Ngày cập nhật: 2026-09-24

## Trạng thái triển khai đã duyệt

Các mục 1–12 của đợt Control Center đã được triển khai trong V0.5.0: launcher/stopper, SQLite migration, import nguồn cũ, dashboard, scheduler/worker, pause-cancel-retry, restart recovery, review theo job, export bền vững, input watcher, profile `careful`/`fast` và AI Supervisor theo yêu cầu. Người dùng sẽ kiểm thử thực tế trước khi mở phạm vi tiếp theo.

Các phần upload BiliBili, Telegram/Tailscale và archive/retention tự động vẫn ở trạng thái kế hoạch; chưa có code nào tự upload, publish, gửi Telegram hoặc dọn dữ liệu dài hạn.

AI Supervisor được bổ sung cấu hình portable ở `config/ai_supervisor.json` trong V0.5.1 và hạ trần model trong V0.5.2. Dashboard quản lý đăng nhập ChatGPT và khóa model/effort theo project; mặc định dùng `gpt-5.6-luna`/`medium`, chỉ cho phép dòng GPT-5.6 với reasoning tối đa `high`, không chấp nhận API key.

## 1. Mục tiêu cuối

```text
Mở BiliFlow theo phiên
  -> thêm một hoặc nhiều video vào input
  -> preflight và nhận diện metadata
  -> AI local quét nội dung
  -> AI audit các ngoại lệ khi cần
  -> người dùng review KEEP/BLUR/CUT
  -> render và kiểm tra output <= 3,5 GB
  -> upload BiliBili ở chế độ có kiểm soát
  -> xác minh upload/playback
  -> thông báo Telegram
  -> archive/nén report và dọn dữ liệu theo policy
  -> tắt BiliFlow hoàn toàn
```

Nguyên tắc cố định:

- Giữ nguyên video nguồn.
- Model media chạy local trên Windows; không tự gửi toàn bộ video lên cloud.
- Không API AI trả phí theo lượt trong pipeline media.
- Mọi sửa video phải có quyết định người dùng hoặc preset đã được người dùng phê duyệt rõ.
- Không tự publish công khai trong POC upload.
- Không lưu mật khẩu, cookie, bot token hoặc API key dạng plaintext trong source/log.
- Output không vượt 3.500.000.000 byte và phải qua kiểm tra hình, tiếng, thời lượng, full decode, checksum nguồn.
- Runtime, model, cache, database, report và media nằm trên ổ E.

## 2. Những phần đã hoàn thành

### Phase 0 — môi trường

- Windows, CPU, RAM, RTX 2060 6 GB, ổ đĩa và driver đã được đánh giá.
- Python, PyTorch CUDA và FFmpeg portable nằm trong project ổ E.
- Storage safety có warning/pause/hard-stop.
- NVENC hiện chưa dùng vì driver/build chưa tương thích; CPU libx264 là đường production hiện tại.

### Phase 1 — local AI baseline

- Tách riêng `adult`, `gore`, `violence` cho animation và live action.
- OCR tiếng Việt + semantic routing cho advertisement/subtitle/credits/scene text.
- Qwen2-VL xác nhận violence và visual logo.
- Florence-2 định vị vùng logo sau semantic gate.
- License audit khóa model theo revision/checksum và profile miễn phí/commercial-safe.
- Violence animation đạt recall 86,44%, precision 80,95% trên 126 mẫu.
- Adult/gore đạt baseline POC hỗ trợ review; vẫn cần mở rộng field set.

### Phase 2 — review, edit và export

- Queue hợp nhất nhiều detector.
- Quyết định KEEP/BLUR/CUT/NEEDS_MORE_CONTEXT có audit log.
- Blur tự tính theo từng video, hỗ trợ banner chạy ngang và thời gian bù.
- Edit plan khóa theo checksum và quyết định.
- Final render giữ audio, kiểm tra giới hạn 3,5 GB và full decode.
- Một nút finalize trong review UI có thể tạo plan và xuất sau khi queue được giải quyết.
- 139/139 unit test đạt; 8/8 model media local được policy cho phép.

### Phase 2B — cải tiến logo và hiệu suất

- Crop vùng chồng lấn để bắt logo nhỏ.
- Temporal grouping cho persistent overlay và branded end-card.
- NewGates Anime đã được bắt từ 6,000–429,312 giây; end-card 429,312–445,312 giây.
- Regression dataset có 206 quyết định từ sáu nguồn.
- Mutex ngăn hai GPU job hoặc hai final render tranh tài nguyên.

## 3. Mức sẵn sàng hiện tại

| Nhánh | Trạng thái | Quyền tự động hiện tại |
|---|---|---|
| Quảng cáo chữ | Production có human review | Đề xuất BLUR/CUT, người dùng duyệt |
| Logo/watermark | Pilot tốt; cần thêm 5–10 video đa nguồn | Không tự sửa |
| 18+ animation | Benchmark nhỏ rất khả quan | Chỉ tạo candidate |
| 18+ live action | Thiếu hard negative | Chỉ tạo candidate |
| Gore animation/live | Đạt POC 80–85%, chưa đủ field gate | Chỉ tạo candidate |
| Violence animation | Baseline tốt nhất, hai video/126 mẫu | Chỉ tạo candidate |
| Violence live action | ViT + Qwen giảm báo sai, tập xác nhận còn nhỏ | Chỉ tạo candidate |
| Blur/cut/render | Đã kiểm chứng trên output thực tế | Chạy sau review |
| Upload BiliBili | Chưa bắt đầu | Không upload |
| Telegram | Chưa bắt đầu | Không kết nối |

## 4. Trải nghiệm người dùng cuối

### Khởi động

Người dùng double-click `Start-BiliFlow.cmd`:

1. kiểm tra single-instance lock;
2. khởi động backend local `127.0.0.1:8765`;
3. mở SQLite và chạy recovery audit;
4. khởi động scheduler ở trạng thái đã lưu;
5. mở dashboard trong trình duyệt.

BiliFlow không cài Windows service và không tự chạy cùng Windows.

### Tắt

- `Tắt an toàn sau bước hiện tại`: hoàn thành stage, lưu trạng thái và đóng.
- `Dừng ngay và tắt`: cancel worker, quarantine partial, lưu checkpoint và đóng.
- `Stop-BiliFlow.cmd`: đường dự phòng khi dashboard không truy cập được.
- Đóng tab trình duyệt không tự tắt backend để tránh làm hỏng job đang chạy.
- Sau shutdown không còn watcher, scheduler, worker, FFmpeg, AI Supervisor hoặc listener 8765.

## 5. Pipeline của một video

### Bước 1 — discover và preflight

- Chờ file ổn định ít nhất 60 giây và mở đọc được.
- FFprobe codec, resolution, fps, audio, duration.
- SHA-256 để chống job trùng.
- Kiểm tra dung lượng ổ, model/license, tool và output name.
- Xác định metadata/content style; nếu không chắc chuyển `NEEDS_METADATA`.

### Bước 2 — safety scan

- Animation: shared WD inference cho adult/gore/direct-action; temporal violence ensemble khi cần.
- Live action: NSFW/gore classifier và ViT violence; Qwen xác nhận candidate violence.
- Giữ strongest evidence và top-K giới hạn.
- Không sửa video trong bước scan.

### Bước 3 — text/ad scan

- OCR vi/en, tracking theo vị trí và tính liên tục nội dung.
- Semantic routing advertisement/subtitle/credits/scene text.
- Refine đầu/cuối và cửa sổ nghi vấn.
- Tạo vùng blur và thời gian bù riêng của video.

### Bước 4 — visual logo scan

- Boundary sampling dày 0,25 giây ở đầu/cuối.
- Sampling toàn timeline, crop vùng chồng lấn và Qwen confirmation.
- Group persistent watermark và end-card.
- Florence định vị pixel sau Qwen gate.

### Bước 5 — coverage/AI audit

- Kiểm tra coverage timeline, cửa sổ lỗi, detector mâu thuẫn, candidate count bất thường.
- AI Supervisor chỉ được gọi khi người dùng yêu cầu.
- AI kiểm tra JSON không gửi media. Visual AI Audit yêu cầu xác nhận riêng cho từng job, chọn tối đa 36 thumbnail trong reports và không gửi video/âm thanh nguồn.
- Visual AI chỉ tạo nhận xét và đề xuất có cấu trúc; không tự đặt quyết định review hoặc khởi động render.

### Bước 6 — review

- Một queue canonical cho một source revision.
- Filter logo/text/adult/gore/violence.
- Bulk accept suggestion theo filter; vẫn sửa lại từng mục được.
- Queue còn null hoặc NEEDS_MORE_CONTEXT thì không xuất.

### Bước 7 — render và QA

- Tạo edit plan từ queue đã khóa.
- Render vào `.partial` bằng CPU libx264.
- Kiểm tra bytes, duration, video/audio stream, full decode và source checksum.
- Chỉ rename thành output chính thức sau khi tất cả kiểm tra đạt.

### Bước 8 — upload và post-processing

- Chỉ bắt đầu sau khi Upload POC được phê duyệt.
- Upload draft, poster và metadata.
- Xác minh upload completion và playback.
- Publish công khai là một gate riêng.
- Sau xác minh mới archive/nén và áp retention.

## 6. Control Center

### Database

SQLite trên `state/control-center.sqlite3`, WAL mode:

- `jobs`: source hash, metadata, state, priority, profile.
- `stages`: attempt, progress, heartbeat, checkpoint, error.
- `artifacts`: report/queue/plan/output path, checksum và revision.
- Quyết định và actor/transport tiếp tục nằm trong queue JSON có audit log; database giữ revision và artifact trỏ tới queue canonical.
- `events`: log có giới hạn và không chứa secret.
- `settings`: scheduler, resource và retention policy.

### Trang tổng quan

- Queue video và trạng thái.
- Stage hiện tại, tiến độ, ETA.
- GPU/CPU/RAM/nhiệt độ/ổ E.
- Worker đang giữ GPU/render slot.
- Start all, pause dispatch, stop-after-stage và shutdown.

### Trang video

- Metadata, source checksum và model revision.
- Timeline coverage và candidate theo loại.
- Start, pause, stop-after-stage, cancel, retry và open review.
- Artifact/output/history.

## 7. State machine và điều khiển

```text
DISCOVERED
 -> NEEDS_METADATA | QUEUED
 -> PREFLIGHT
 -> SCANNING_SAFETY
 -> SCANNING_TEXT
 -> SCANNING_LOGO
 -> LOCALIZING_REGIONS
 -> AI_AUDIT_QUEUED (optional)
 -> WAITING_REVIEW
 -> READY_TO_EXPORT
 -> RENDER_QUEUED
 -> RENDERING
 -> VERIFYING
 -> READY_TO_UPLOAD
 -> UPLOADING
 -> VERIFYING_UPLOAD
 -> COMPLETED
```

Phụ: `PAUSE_REQUESTED`, `PAUSED`, `STOP_AFTER_STAGE`, `CANCEL_REQUESTED`, `CANCELLED`, `FAILED_RETRYABLE`, `FAILED_FINAL`, `INTERRUPTED_RECOVERABLE`.

- Pause scheduler: không phát stage mới.
- Stop-after-stage: stage hiện tại hoàn tất rồi pause.
- Pause now: dừng process group; giữ mọi stage đã hoàn tất và chạy lại riêng stage đang dở khi resume.
- Cancel job: dừng một video, giữ source/report hợp lệ.
- Emergency stop: dừng toàn bộ process group và recovery ở lần mở sau.
- Render bị dừng sẽ bỏ `.partial` và render lại từ đầu.

## 8. Đa video và tài nguyên

- Một GPU worker vì RTX 2060 có 6 GB VRAM.
- Một final render.
- Review và web chạy đồng thời, không giữ VRAM.
- Nhiều job được queue; mỗi job có source hash/revision riêng.
- Profile `careful` mặc định; `fast` chỉ bật cho nguồn đã có regression tốt.
- Thermal guard ngăn phát stage GPU mới khi vượt ngưỡng.
- Storage guard ngăn job mới khi dưới reserve.

## 9. AI Supervisor

- Codex App Server qua stdio, tạo persistent BiliFlow Supervisor thread.
- Dùng ChatGPT-managed authentication thay vì API key trả theo lượt.
- Chỉ chạy khi được gọi, không chạy 24/7.
- Có thể đề nghị scan lại hoặc đánh dấu `NEEDS_AI_ATTENTION`.
- Không tự quyết định BLUR/CUT, render, upload hoặc publish.
- Offline/rate-limit fallback về review local.

## 10. BiliBili Upload POC

1. Xác minh đúng Creator Center của BiliBili TV, không đồng nhất với bilibili.com Trung Quốc.
2. Kiểm tra API chính thức; nếu không phù hợp mới dùng browser automation/RPA.
3. Upload một file test ở chế độ draft/unpublished.
4. Poster, title, episode, description và category lấy từ metadata đã xác nhận.
5. Theo dõi upload %, retry có backoff và nhận biết session hết hạn.
6. Không vượt CAPTCHA/xác thực và không tự publish.
7. Xác minh playback trước khi đánh upload thành công.

Secret lưu bằng Windows credential protection/DPAPI hoặc credential manager, không nằm trong SQLite/log.

## 11. Telegram và truy cập từ xa

- Telegram long polling; không mở inbound port.
- Allowlist user_id/chat_id, revision chống callback cũ/trùng.
- `/status`, `/pause`, `/resume`, `/retry`, `/stop-after-stage` ở giai đoạn đầu.
- Review thumbnail là opt-in; không gửi video nguồn.
- Không cho publish từ callback Telegram trong POC.
- Dashboard từ Mac/iPhone dùng Tailscale/VPN riêng; không public trực tiếp cổng 8765.

## 12. Storage, archive và retention

- Source/output không tự xóa.
- Temp: 24 giờ; failed work: 7 ngày; preview: 30 ngày.
- Cleanup ban đầu dry-run và cần review.
- Sau upload + playback verified: zip report/queue/plan/manifest, tạo checksum.
- HEVC archive chỉ bật sau khi encoder/driver và full-decode được kiểm chứng.
- Output lớn chỉ thành cleanup candidate sau archive verification và safe window.

## 13. Thứ tự triển khai còn lại

### Phase A — launcher và database

- Start/Stop scripts, single-instance lock, shutdown API.
- SQLite schema/migration.
- Import source và canonical queue hiện có mà không sửa report cũ.

### Phase B — dashboard read-only

- Overview, job detail, telemetry và artifacts.
- Kiểm chứng dữ liệu import và revision.

### Phase C — scheduler/workers

- Process supervisor, resource slots, heartbeat, checkpoint.
- Start/pause/stop/cancel/retry và recovery.

### Phase D — review/export integration

- Review theo job.
- Final export thành durable worker thay daemon thread.
- Test đóng browser/backend, kill worker và restart.

### Phase E — watcher và profiles

- Stable-file detection, metadata gate, careful/fast.
- Auto-start mặc định OFF; người dùng bật sau pilot.

### Phase F — AI Supervisor

- App Server, persistent thread, escalation rules và structured audit.

### Phase G — BiliBili POC

- Creator Center research, draft upload, metadata, retry và playback verification.

### Phase H — Telegram/remote

- Allowlist, secret store, notification và remote review.

### Phase I — archive/reliability

- Upload-success retention, archive verification, thermal/disk guard và recovery test.

## 14. Acceptance gates

- Dashboard shutdown để lại 0 process/listener của BiliFlow.
- Không hai GPU worker hoặc final render chạy cùng lúc.
- Duplicate source không tạo job độc lập ngoài revision được yêu cầu.
- Pause/cancel một video không ảnh hưởng video khác.
- Job sống sót restart ở ranh giới stage; stage hoàn tất không chạy lại.
- Queue cũ không bị thay đổi khi import.
- Chạy pilot ít nhất 5 video dashboard bằng profile careful và audit false negative.
- Logo đa nguồn phải có benchmark đủ trước khi cho auto-action.
- Upload POC chỉ draft; publish vẫn cần gate riêng.
- Archive không làm output trở thành cleanup candidate trước khi verification hoàn tất.
