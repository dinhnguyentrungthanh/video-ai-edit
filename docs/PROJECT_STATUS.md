## Completed export history V0.7.19 — 2026-09-27

- Completed cards retain a full 100% export bar instead of replacing progress with a generic completion label.
- The export card shows `100% · Đã xuất video` and the local date/time when the durable job entered `COMPLETED`.
- This uses existing job metadata and changes only Dashboard presentation.
- Verification: Dashboard JavaScript syntax and 211/211 automated tests pass.

## Live export progress V0.7.18 — 2026-09-27

- Final FFmpeg renders now emit machine-readable progress every second. Dashboard cards show true export percentage, encoding speed and an estimated remaining time.
- The denominator is the expected output duration after merged CUT intervals, not the unedited source duration.
- Analysis progress and export progress are independent. A fully analyzed video can remain at 100% analysis while its export bar advances from 0% to 100%.
- After FFmpeg reaches 100%, the card changes to `Đang kiểm tra output` while BiliFlow validates duration, video/audio streams and a full decode. It moves to `Hoàn tất` only after those checks pass.
- The `Đang chạy` tab contains separate `Đang phân tích để duyệt` and `Đang xuất video` sections, while queued work remains in `Đang chờ xử lý`.
- Progress uses a temporary sidecar under `temp/render-progress`; scheduler cleanup removes it on success, failure, pause or interruption.
- The FFmpeg protocol was validated with a real local encode. Python, JavaScript and 211/211 automated tests pass.

## Control Center lifecycle tabs V0.7.17 — 2026-09-27

- Dashboard videos are grouped into three live tabs: `Đang chờ xử lý`, `Đang chạy`, and `Hoàn tất`, each with its own count.
- Every card now separates scene-analysis progress, deterministic structure audit, Visual AI Audit, and export lifecycle instead of mixing them into one status column.
- Export state is explicit: not reached, waiting for review, ready, queued, rendering, verifying, failed, or exported successfully.
- Completed renders move to `Hoàn tất`; a rerun follows the existing job state back through waiting and active processing before returning to completion.
- Detector selection for reruns is collapsed until needed, reducing visual noise while preserving every existing action.
- This is a frontend-only change. No API, queue, detector, scheduler, review, or rendering behavior changed.
- Verification: live server HTML contains all lifecycle views, extracted JavaScript passes Node syntax validation, and 208/208 automated tests pass.

## Final-render filter compaction V0.7.16 — 2026-09-27

- Final rendering removes short logo blurs whose time range and region are already covered by an equivalent persistent blur; the review provenance and edit plan remain unchanged.
- Identical full-frame blur decisions share one FFmpeg filter with a merged multi-range enable expression.
- Troy job #39 now renders with one persistent XEMBZ.NET overlay and one full-frame blur filter instead of roughly 66 serial filters. This eliminates the filter-thread explosion that prevented the first frame from being produced.
- Immediate pause on Windows now terminates the full stage process tree and deletes only the matching incomplete render file inside `output`, preventing orphaned FFmpeg workers and stale-partial retry failures.
- Live validation: job #39 attempt 3 is actively writing output after restart; verification suite passes 207/207 tests.

## Persistent OCR region consensus V0.7.15 — 2026-09-27

- Repeated corner overlays no longer use an unconditional min/max union. The dominant size and position consensus rejects an occasional OCR box joined to nearby movie text.
- Existing full-film text-logo items can be tightened from spatially consistent visual OCR evidence without rerunning the source video or any model.
- Troy job #39 supplied 51 matching visual OCR regions. Its XEMBZ.NET proposal changed from `x=110, y=146, 626x84` to `x=105, y=160, 174x54`, excluding the unrelated words to its right.
- The rebuilt queue retains complete candidate coverage and deterministic local audit PASS.
- Verification: 204/204 automated tests pass.

## Long-video logo routing V0.7.14 — 2026-09-27

- Generic full-frame temporal correlation is now coverage evidence instead of a unique logo lead. This prevents slowly moving movie scenes from filling the semantic budget.
- Strong regional evidence must exceed the full-frame score by a meaningful margin and every such candidate is retained for local VLM review.
- Each five-minute bucket keeps two complementary full-frame representatives: one ident-oriented and one persistence-oriented. Missing regional evidence or a missing representative still fails deterministic coverage.
- Troy job #39 was rerun from its existing routing cache. The scan retains all 285 regional candidates and all 80 required full-frame representatives, collapses 1,889 redundant full-frame windows, and reports complete coverage.
- Existing adult and violence reports were reused. The rebuilt 294-item review queue has complete references and detector coverage; local structure audit returns PASS without using ChatGPT quota.
- Verification: 202/202 automated tests pass.

## Per-video output size policy V0.7.13 — 2026-09-27

- Review cho phép chọn riêng từng video: tối đa 3,5 GB mặc định, giới hạn GB tùy chỉnh hoặc không giới hạn dung lượng.
- Chính sách được lưu trong queue và edit plan, sau đó scheduler chuyển nguyên vẹn sang renderer. Custom/unlimited có định danh output riêng nên không đè lên bản render theo chính sách khác.
- Không giới hạn dùng libx264 CRF 20 không có VBV size ceiling; bảo vệ dung lượng trống, kiểm tra hình/tiếng, thời lượng, full decode và checksum nguồn vẫn bắt buộc.
- Queue cũ và CLI không truyền lựa chọn mới tiếp tục dùng mặc định 3,5 GB.
- Dashboard giữ bản nháp thể loại phim và profile riêng theo ID video qua mỗi lượt refresh 3 giây; lựa chọn `Phim thực tế` không còn bị option mặc định `Hoạt hình` ghi đè trước khi bắt đầu.
- Verification: 200/200 automated tests pass.

## Full-timeline logo candidate coverage V0.7.12 — 2026-09-26

- Visual-logo selection now gives novel evidence priority over repeated approved watermarks.
- Approved brands are tracked by identity and five-minute timeline bucket, retaining representative temporal evidence without spending hundreds of semantic slots on the same overlay.
- Careful scans can retain up to 420 windows; fast scans retain up to 120.
- Reports expose novel omissions and missing approved-brand time groups. Review queues and the deterministic AI Audit gate remain incomplete when either class lacks coverage.
- Region decisions now require exact-region evidence: approved brand memory, a compact structural site mark, or OCR text matching a concrete VLM brand name. A frame-level Qwen YES and a Grounding/DINO box alone remain optional evidence.
- Persistent grouping accepts only `approved_brand_memory` confirmations, excludes already-classified opening/end scenes, and partitions all identities by focus geometry. A weak 0.84 memory similarity can no longer create a full-film track.
- Opening promotion conversion is bounded to intervals ending within the first 30 seconds; a persistent overlay can never become a full-film CUT.
- Visual boxes merge only at IoU >= 0.50. This prevents a valid OCR watermark from donating BLUR to a neighbouring character, sign, or DINO box.
- Low-ad scene text stays auditable but does not block export. Required and optional candidates both count toward source-reference coverage.
- Conan Movie 20 validation retains 320/320 novel candidates and 70/70 approved-brand time groups while collapsing 900 repeated PhimOnline watermark windows. The clean queue is fully covered with two mandatory items: PhimOnline BLUR for 0–6689.578 seconds and opening ident CUT for 5–10 seconds; 387 weak/scene candidates remain optional.
- Advertising stages are byte-for-byte equivalent between advertising-only and all-model pipeline construction; adult, gore and violence routing is unchanged.
- Verification: 196/196 automated tests pass and all modified Python files compile.

## Visible optional-candidate review V0.7.11 — 2026-09-26

- Review now displays separate counts for required items and optional candidates.
- A highlighted `Ứng viên phụ (N)` filter sits beside `Chưa duyệt`; it exposes all weak OCR and regional-logo evidence retained for high-recall inspection.
- `Tất cả mục chính` refers only to blocking review decisions, removing ambiguity about the much larger optional evidence set.
- The Conan Movie 20 revision 3 UI reports 4 required items and 57 optional candidates.

## Region evidence isolation V0.7.10 — 2026-09-26

- Frame-level Qwen confirmation now means only that branding exists somewhere in the frame. A Grounding, GroundingDINO, or OCR rectangle needs its own regional corroboration before it can block export.
- Single-source unknown rectangles remain in `Xem tất cả ứng viên`, where a reviewer can promote a missed brand without treating faces and film objects as confirmed logos.
- OCR below 20% advertisement probability that is not a persistent overlay also moves to the optional candidate view. Persistent overlays and user policy matches still require review.
- Legacy reports receive the same routing at queue-build time, avoiding a full model rerun.
- Conan Movie 20 revision 2 contains 4 required items (3 pending after preserving the approved watermark blur) and 61 optional candidates, down from 19 required items with no evidence deletion.
- Verification: 174/174 automated tests pass.

## Per-video detector scope and local structure audit V0.7.9 — 2026-09-26

- Each video can run advertising/logo, adult, gore, violence, all groups, or any combination. The registry can accept future detector groups without changing job storage.
- Scheduler stages and review reports follow the saved scope. Reruns can choose a different scope and create a separate report revision.
- New queues explicitly record selected and skipped groups. Review UI states what was not scanned rather than treating an omitted detector as a negative result.
- Structural JSON validation runs locally after queue creation and consumes no ChatGPT quota. Visual AI remains the only Codex-backed audit and still requires per-video confirmation.
- Animation safety categories share one inference pass, so selecting multiple safety outputs does not decode the video three times.
- Verification: 169/169 automated tests pass; rendered Control Center and Review JavaScript both pass Node syntax validation.

## Shared AI Supervisor session V0.7.8 — 2026-09-26

- JSON and visual audits now reuse one persistent Codex thread across batches and videos.
- Audit execution is serialized around the shared thread; each turn still carries its own queue manifest, allowed item IDs, and bounded thumbnails.
- Returned visual assessments are filtered to item IDs attached to the current turn, preventing earlier video results from being applied to a new queue.
- A new thread is created only when no saved thread exists or Codex cannot resume it. Control Center reports whether the shared session has been initialized.

## Region precision and scene-text guard V0.7.7 — 2026-09-26

- Full-resolution foreground refinement tightens approved watermark boxes without changing human review authority.
- End-card evidence is selected from inside the refined time interval.
- End-card CUT grouping now requires an explicit full-frame promotion signal; a persistent corner watermark alone remains a regional candidate.
- Low-ad, non-overlay OCR evidence protects only the overlapping in-film text region; approved brand-memory regions retain priority.
- Text queue items now include source-frame size and clipped source coordinates.
- Conan regression: 79/79 candidates covered, no missing references, PhimOnline tightened from 308x69 to 282x46, in-film `SCRAP BOOK` kept, and no false full-scene end-card CUT.
- Historical brand-memory thumbnails from four source videos remain contained after refinement; the smallest retained area is 60.8% of the reviewed box, while ambiguous regions remain unchanged.
- Verification: 161/161 automated tests pass; license audit reports 9 allowed local models and 0 blocked models.

## Winning-signature geometry V0.7.6 — 2026-09-26

- Brand-memory routing now carries the geometry belonging to the actual winning signature.
- This fixes correct PhimOnline appearance matches receiving an unrelated record's region before localization.

## Pending-item migration safety V0.7.5 — 2026-09-26

- Rebuilding an existing queue cannot silently remove candidates that still await a human decision.
- Detector improvements affect new routing, while unresolved legacy candidates remain visible with migration provenance until reviewed.

## Region-bound brand memory V0.7.4 — 2026-09-26

- Approved logos now carry their learned relative position through scanning and localization, so nearby scene text cannot inherit a frame-level logo match.
- Regional memory matching checks geometry as well as appearance, and full-frame scene decisions no longer contaminate the logo library.
- Known logo regions bypass unrelated OCR/Grounding proposals while remaining subject to human review before editing.

## Target-region review safety V0.7.3 — 2026-09-26

- Review cards now state that only the red rectangle is being classified and separately identify a previously approved persistent watermark elsewhere in the frame.
- A high-confidence Visual AI conflict requires explicit confirmation before the opposite human edit is saved.
- Unrelated overlapping region decisions are no longer presented as protection for the current candidate.

# Project status — 2026-09-24

## Contained watermark OCR deduplication V0.7.2 — 2026-09-26

- Short OCR fragments proposed for BLUR are absorbed when their region and interval are fully contained by a confirmed persistent watermark. Movie titles and text outside that region remain independent review items.
- Conan item `review-05067ca7cccb` (`NPt`) was proven to be a fragment inside the already tight full-timeline PhimOnline watermark and was merged into the main item with its source evidence preserved.
- Conan queue changed from 94 to 93 pending items with zero automatic decisions, zero duplicate IDs, zero integrity blockers and zero deterministic quality findings. 148/148 tests pass.

## Review identity and Visual AI safeguards V0.7.1 — 2026-09-26

- AI Audit is scoped to the active queue revision; rerunning a video no longer shows an older audit or lets an older worker write into the new queue.
- Review IDs now include candidate type, review kind, classification and source-pixel region. Duplicate IDs are a deterministic integrity blocker.
- Opening promotions and branded end cards are each presented as one full-scene CUT candidate; region BLUR cannot override that policy.
- Visual AI receives source-frame dimensions and explicit target-region guidance. A watermark elsewhere in the thumbnail must not classify the reviewed region as a logo.
- High-confidence disagreement between local and Visual AI suggestions clears the bulk suggestion and requires a human choice. AI still makes no edit decision.
- Conan revision 2 was migrated in place with zero automatic decisions: 94/94 unique IDs, 35 Visual AI assessments retained and the closing end-card changed to a CUT proposal.
- Regression, compile and full test suite pass: 148/148.

## GroundingDINO region fallback V0.7.0 — 2026-09-26

- Thêm POC độc lập trên 48 ảnh từ quyết định review thật: 24 BLUR/CUT và 24 KEEP. Report chuẩn ở `benchmarks/ad-pipeline-regression-v3/benchmark.json`; benchmark không sửa queue hoặc video.
- SigLIP Apache-2.0 chỉ đạt top-half recall 41,67% và bị khóa khỏi production. Không dùng model này để bỏ qua candidate.
- PySceneDetect không được thêm vì visual scanner hiện đã route theo scene-change và coverage bucket trong cùng lượt giải mã; thêm một detector cảnh riêng sẽ lặp công việc mà benchmark này chưa chứng minh được lợi ích.
- GroundingDINO Tiny Apache-2.0 không đạt specificity để phân loại toàn cảnh, nhưng đạt region recall 100% ở IoU >= 0,05 trên 8 mục BLUR có vùng chuẩn. Riêng logo NewGates đạt IoU xấp xỉ 0,94.
- Pipeline mới giữ scanner/Qwen làm semantic gate, rồi chạy GroundingDINO tối đa một vùng bổ sung trên frame gốc. Vùng chỉ được đề xuất BLUR và luôn cần người dùng duyệt; model không tự CUT/BLUR/export.
- POC trên report Conan chỉ xử lý hai interval cần fallback trong 3,046 giây, thêm đúng một vùng logo xuyên phim `x=1546, y=36, width=318, height=79`; peak CUDA khoảng 1,09 GB.
- PaddleOCR 3.7.0 được A/B trong runtime riêng: cấu hình video 960 px đạt 6/6 vùng BLUR nhưng mất 0,569 giây/ảnh, trong khi EasyOCR hiện tại cũng đạt 6/6 và chỉ mất 0,325 giây/ảnh. PaddleOCR không được đưa vào production; runtime, model và pip cache thử nghiệm khoảng 1,08 GB đã được xóa, chỉ giữ report nhỏ.
- Cache model và kết quả nằm trên ổ E. GroundingDINO khoảng 658 MiB được giữ với revision và SHA-256 trong manifest; SigLIP khoảng 778 MiB đã bị xóa sau khi trượt cổng chất lượng. License audit hiện 9 model đã cài đều allowed; toàn bộ 144 test đạt.

## Visual AI Audit V0.6.1 — 2026-09-25

- Dashboard có hai chế độ riêng: AI kiểm tra JSON không gửi media và Visual AI Audit yêu cầu xác nhận cho từng video.
- Visual audit chọn tối đa 36 thumbnail có sẵn trong reports, ưu tiên logo/chữ rồi mới đến adult/gore/violence; mỗi mục tối đa ba ảnh. Đường dẫn ngoài reports, file trên 8 MiB, video và audio đều bị loại.
- GPT-5.6 Luna/Medium trả phân loại, độ tin cậy, đề xuất KEEP/BLUR/CUT/NEEDS_MORE_CONTEXT và chất lượng vùng blur. Kết quả chỉ được ghi thành nhận xét/đề xuất; quyết định vẫn do người dùng thực hiện.
- Billing bị khóa ở đăng nhập ChatGPT và hạn mức có sẵn; API key không được phép. Hết hạn mức thì lượt audit thất bại/chờ reset, BiliFlow không có chức năng mua credit.
- Smoke test bằng ảnh tổng hợp xác nhận App Server nhận localImage: phân loại external_brand, BLUR, confidence 0,99, vùng TIGHT. 139/139 test đạt; license audit 8 allowed, 0 blocked.
- Kiểm chứng thực tế trên job Conan #37 dùng ba batch, 12 ảnh mỗi batch và high-detail cho mọi nhóm. Kết quả 36/36 structured assessments, không còn ảnh bị bỏ qua: 24 KEEP, 1 CUT, 1 BLUR, 10 NEEDS_MORE_CONTEXT; 0 quyết định được AI tự áp dụng.

## Brand memory và adaptive routing V0.6.0 — 2026-09-25

- `state/brand-memory.json` hiện có 28 chữ ký perceptual: 27 chữ ký thương hiệu đã duyệt (7 BLUR, 20 CUT) và 1 chữ ký âm cho tiêu đề phim đã KEEP. File không chứa video và luôn ghi `automatic_edit: false`.
- Cache routing visual-logo nằm dưới `cache/visual-logo/<source-sha256>/`, nén gzip và khóa theo checksum nguồn, cấu hình, thuật toán cùng revision bộ nhớ. Lần retry sau lỗi kiểm chứng đã báo cache hit và không quét lại routing.
- Scanner giữ fallback phân bố theo timeline, route chuyển cảnh có tín hiệu và có thể dùng khớp bộ nhớ ở độ tin cậy cao thay Qwen cho cửa sổ đó; tất cả vẫn đi vào review.
- Hai report độc lập trên 15 giây đầu `#874-B2` đều giữ 3/3 cửa sổ; mỗi report có 2 memory match. Florence định vị 3/3, gồm NewGates `130,104,159,67` và visual grounding `97,13,240,224`.
- Review tách intro và logo góc thành hai mục vì vùng không chồng nhau; hai cửa sổ NewGates liền nhau được gom thành một mục `5–15s`.
- 139/139 unit tests đạt; license audit: 8 allowed, 0 blocked. Báo cáo OCR mới luôn kèm SHA-256 nguồn để AI audit đối chiếu với queue và các detector còn lại.
- Queue dài nay giữ manifest đối chiếu từng candidate nguồn với mục review sau gộp. OCR và visual-logo của cùng watermark cố định được gom thành một quyết định toàn timeline; opening promotion toàn khung được đề xuất CUT trước review. Job Conan Movie 21 xác nhận 88/88 candidate được biểu diễn, AI Audit PASS.
- AI BLOCK chỉ được giữ khi validator local chứng minh lỗi checksum, report, timeline hoặc candidate coverage. BLOCK thuần suy đoán từ model được hạ xuống WARN để không chặn nhầm review; nhận xét vẫn được giữ cho người dùng xem.
- Cửa sổ đầu/cuối video nay được Qwen phân loại theo ngữ cảnh toàn cảnh để gộp trọn quảng bá thành CUT. Vùng BLUR logo được cân bằng giữa hộp grounding và OCR: giữ đủ biểu tượng đồ họa, thu phần đệm dư. AI Supervisor có thêm kiểm tra tất định để cảnh báo CUT đầu video bị thiếu và vùng logo bị quá nhỏ hoặc quá rộng.
- Logo/chữ cố định bị OCR bắt gián đoạn được gom theo nội dung, vị trí và độ phủ timeline. Chỉ chuỗi có thêm bằng chứng quảng cáo mới được đề xuất blur; chuỗi điểm quảng cáo thấp được bảo vệ như tiêu đề phim.
- Review có lựa chọn blur logo, blur toàn cảnh, cắt cảnh và hiển thị quyết định blur chồng thời gian. Vùng visual-logo trùng OCR tiêu đề được đề xuất KEEP.
- Florence tạo một mục review riêng cho từng vùng trong cùng frame; kết luận toàn cảnh không còn tự động áp dụng cho mọi chữ/logo. Review có thêm `Xem tất cả ứng viên`; candidate bị VLM loại không chặn export nhưng có thể được đưa vào queue chính bằng quyết định của người dùng.
- Job `#874-B2` giữ nguyên 7 mục chính và toàn bộ quyết định đã có, đồng thời có 26 ảnh audit tùy chọn (khoảng 4,7 MB). Kiểm chứng frame 25 giây: NewGates bên trái vẫn BLUR còn tiêu đề phim bên phải KEEP. Frame 209 giây nằm trong track NewGates BLUR `6,25–429,062s`; thẻ violence vẫn chờ người dùng quyết định độc lập.

## AI Supervisor portable config V0.5.2 — 2026-09-25

- Dashboard hiển thị trạng thái cài đặt/đăng nhập Codex và có nút mở `codex login` trong trình duyệt.
- `config/ai_supervisor.json` đi cùng project; mặc định `gpt-5.6-luna`/`medium`, ChatGPT auth bắt buộc, service tier `default`, gửi media tắt. Chỉ dòng GPT-5.6 được phép; reasoning cao nhất là `high`.
- API chỉ nhận model thuộc dòng GPT-5.6 và Low/Medium/High; API key, GPT-6 và mức trên High bị chặn. App Server nhận model/effort ở từng thread và turn.
- Smoke test thật nhận đúng đăng nhập ChatGPT; cấu hình hiện dùng GPT-5.6 Luna/Medium và shutdown sạch.

## Control Center V0.5.0 — 2026-09-25

- Đã hoàn thành phạm vi 1–12 để kiểm thử thực tế: Start/Stop theo phiên, SQLite WAL, import lịch sử, dashboard, scheduler, pause/cancel/retry, recovery, review theo job, export bền vững, watcher, hai profile và AI Supervisor theo yêu cầu.
- Import thật nhận 6 nguồn và 14 revision review; không sửa video/report cũ. Tập 1 được suy luận đúng là `live_action` từ toàn bộ lịch sử thay vì revision logo mới nhất.
- Smoke test cổng 8766 đạt dashboard/review/API, single-instance và shutdown sạch; không chạy scan/render nặng trong đợt kiểm thử kỹ thuật này.
- Một GPU worker xử lý tuần tự, nhưng dashboard nhận nhiều job cùng lúc; review và web vẫn chạy song song. Mọi sửa media tiếp tục cần quyết định người dùng.
- BiliBili upload, Telegram/Tailscale và archive/retention tự động chưa triển khai theo phạm vi đã chốt.

## Nâng cấp luồng nhanh V0.4.0 — 2026-09-24

- Sáu video nguồn dùng cho regression vẫn còn trong `input`; việc người dùng chuyển sáu bản đã xuất ra khỏi `output` không ảnh hưởng queue/report. Dataset `annotations/review-regression-v1.json` có 206 quyết định đã chuẩn hóa: 14 BLUR, 29 CUT, 163 KEEP.
- Logo nhỏ được quét bằng crop vùng chồng lấn rồi theo dõi qua timeline. Trên video `#874-B2`, hệ thống tự gom NewGates Anime thành một mục `6,000–429,312s`, đề xuất BLUR vùng `x=97, y=13, width=240, height=224`; end-card `429,312–445,312s` thành một mục CUT riêng.
- Hàng kiểm chứng `reports/shin-874-b2-fbf0cf7b-review-v2/review-queue.json` có 8 mục. Ba candidate visual không còn bị gộp nhầm: opening `0–5s`, persistent overlay `6–429,312s`, branded end-card `429,312–445,312s`.
- Review UI hỗ trợ nhận hàng loạt các đề xuất đang lọc. Khi mọi mục đã được giải quyết, một nút duy nhất khóa queue, tạo edit plan và khởi chạy output nền; không cần thêm vòng xác nhận qua chat. Output vẫn bị chặn trên 3,5 GB và phải qua full-decode validation.
- Nhiều session được phép chạy song song ở mức job. Tác vụ CUDA xếp hàng qua `Local\\BiliFlowGpuInference`; final render xếp hàng qua mutex và file lock riêng. Cách này giữ UI/CPU job đồng thời nhưng tránh hai model cùng chiếm RTX 2060 6 GB.
- Quét visual-logo tối ưu dùng 40 cửa sổ trên video 445,312 giây và hoàn tất trong 158,520 giây; báo cáo cuối còn 3 mục visual thay vì hàng chục cửa sổ lặp lại.
- Bộ kiểm tra tự động hiện đạt 97/97.

## Job mới: Tiếng Yêu Này Anh Dịch Được Không — Tập 2 (2026-09-24)

- Nguồn `input/Tiếng Yêu Này Anh Dịch Được Không - Tập 2.mp4` là job độc lập, 344.101.373 byte, SHA-256 `3148f4832c4a9da9877a00eb761371b18f59291c871a44b54a375f2f66b11e37`, 4.084,287 giây, H.264 1280×720 23,976 fps và AAC stereo. Checksum trước và sau quét không đổi. Không chuyển timestamp, vùng blur hay quyết định từ Tập 1.
- Queue đang dùng: `reports/tieng-yeu-tap-2-review-v1/review-queue.json`. Người dùng đã giải quyết 45/45 mục: 33 `KEEP`, 11 `CUT`, 1 `BLUR`. Người dùng xác nhận CUT liên tục 3801,5–4084,287 giây; biên phát hiện gốc 3935–3945 giây vẫn nằm trong queue và edit plan.
- OCR/semantic quét 1.361 frame toàn phim, giữ 13 text track; lượt dày 0,5 giây quanh banner quét thêm 90 frame. Mục banner sau hợp nhất là 213–243,5 giây; vùng OCR của chính Tập 2 gợi ý `x=0, y=44, width=1280, height=78`, `vertical_only` nếu người dùng chọn BLUR.
- Visual logo exhaustive quét 8.287 frame và đủ 817/817 cửa sổ. Qwen giữ 7 confirmed + 19 uncertain; 791 rejected vẫn có ảnh tại `reports/tieng-yeu-tap-2-visual-logo-exhaustive-v1/audit.html`. Florence đề xuất vùng cho 26/26 mục, không tự quyết định sửa.
- Adult live action quét 8.168 frame, giữ 23 interval thô. Gore ngưỡng chuẩn 0,5 không giữ interval; lượt nhạy 0,2 giữ khoảng 2237–2240 giây để người dùng xem. Violence ViT quét 32.671 frame/4.082 cửa sổ, tạo 152 interval thô; Qwen giữ 1 candidate 281,875–284,875 giây.
- `reports/tieng-yeu-tap-2-review-v1/cut-gap-audit.html` ghi 10 khoảng hở. Các khoảng 185–210, 350–370 và 375–385 giây là cảnh phim; chuỗi credit/brand cuối nay được bao trọn bằng CUT liên tục 3801,5–4084,287 giây theo quyết định người dùng.
- Edit plan `work/tieng-yeu-tap-2-edit-plan-v1.json` có đúng ba operation: CUT 0–10 giây, BLUR 213–243,5 giây vùng `0,44,1280,78` với `vertical_only`, CUT 3801,5–4084,287 giây. CUT cuối gộp chín candidate CUT liên quan và giữ từng biên phát hiện gốc. Thời lượng dự kiến sau CUT là 3791,5 giây.
- Người dùng đã duyệt đủ ba preview tại `previews/tieng-yeu-tap-2-edit-v1/`; manifest `APPROVED`, `scope: ALL`. Output mới `output/tieng-yeu-tap-2-reviewed-v1.mp4` đã hoàn tất: 577.014.939 byte, 3791,500 giây, H.264 1280×720 + AAC, SHA-256 `DC59BA171267D7F0E68AF0544231E4BA07A290F10295943243A62F3583BC66C8`. Giải mã toàn bộ hình/tiếng không lỗi, dưới 3,5 GB; checksum nguồn không đổi. Đã trích và kiểm tra frame đầu, quanh BLUR và cuối; xem `reports/tieng-yeu-tap-2-review-v1/final-qa-notes.md`. Tổng tài nguyên Tập 2 tạo khoảng 603,46 MB, chưa xóa preview hay audit.

## Trạng thái bàn giao chuẩn cho session mới — cập nhật 2026-09-24

Đây là nguồn sự thật hiện tại cho video `Tiếng Yêu Này Anh Dịch Được Không - Tập 1.mp4`. V1–V6 chỉ là lịch sử đối chiếu; mọi việc tiếp theo phải dựa trên V7.

- Queue chuẩn: `reports/tieng-yeu-tap-1-brand-review-v7/review-queue.json`; đã giải quyết 70/70 mục và lưu audit cho các biên thời gian được người dùng chỉnh.
- Edit plan chuẩn: `work/tieng-yeu-tap-1-edit-plan-v7.json`; intro CUT liên tục `0–10,5s`, đoạn cuối CUT liên tục `3569,5–3722,175s`, cùng CUT giữa phim `2875–2880s`.
- Preview blur chuẩn: `previews/tieng-yeu-tap-1-edit-v7-blur-v3/preview-manifest.json`; đã duyệt theo mẫu sau khi kiểm tra lúc chữ vào, ở giữa và ra khỏi khung.
- Vùng banner riêng của video là `x=0, y=48, width=1280, height=72`, sigma 28, feather 3, `edge_feather_mode: vertical_only`. Hai mép ngang phủ kín, chỉ feather trên/dưới.
- Full V7: `output/Tieng-Yeu-Nay-Anh-Dich-Duoc-Khong-Tap-1-reviewed-v7.mp4`; manifest cạnh file có trạng thái `COMPLETED`.
- V7 có 629.702.558 byte, dài 3.554,012 giây so với 3.554,000 giây dự kiến, 1 luồng hình và 1 luồng tiếng. Toàn file giải mã không lỗi, dưới 3,5 GB và checksum nguồn trước/sau không đổi.
- Khung đầu, ba mốc blur và khung cuối đã được lấy trực tiếp từ full V7 để xác nhận ba lỗi người dùng báo đã được sửa.

## Kết luận hiện tại

POC nhận diện quảng cáo tiếng Việt và blur V8 đã hoàn tất. Ba nhóm 18+, blood/gore và violence đều đã có baseline hỗ trợ review trong vùng mục tiêu 80–85% ở các tập hiện có. Violence phim người thật nay dùng ViT tạo candidate và Qwen2-VL-2B xác nhận; violence anime V5 vẫn đạt recall 86,44%, precision 80,95% và balanced accuracy 84,27% trên 126 mẫu.

Policy yêu cầu mọi lượt chạy phải miễn phí, local và commercial-safe. VideoMAE XD violence cũ đã được thay bằng ViT và Qwen2-VL-2B, đều Apache-2.0. Benchmark xác nhận nhỏ 11 clip đạt recall 100%, specificity 85,71% và balanced accuracy 92,86%; cần tiếp tục mở rộng dữ liệu vì mẫu hiện còn ít.

Video độc lập Sintel dài 14 phút 48 giây đã được quét, review và dùng để kiểm chứng toàn bộ luồng xử lý. Queue 39 mục đã được giải quyết thành 37 `KEEP`, 1 `CUT` và 1 `BLUR`; hai preview được người dùng duyệt trước khi dựng. Bản cuối dài 886,064 giây, dung lượng 173.784.506 byte, giữ hình H.264 và tiếng AAC 5.1, giải mã toàn bộ không lỗi và checksum nguồn không đổi. Video mẫu, preview và output thử đã được xóa sau khi hoàn tất kiểm chứng; báo cáo nhỏ được giữ lại để cải tiến detector.

## Trạng thái theo roadmap gốc

| Phase | Trạng thái | Đã có | Còn thiếu để hoàn thành |
|---|---|---|---|
| 0. Environment Assessment | Hoàn thành | Windows/GPU/RAM/disk/driver/tool assessment; kiến trúc, chi phí và kế hoạch | Chỉ cần đánh giá lại khi phần cứng/driver thay đổi |
| 1. Local AI Proof of Concept | Baseline kỹ thuật hoàn thành | OCR; 18+ benchmark; gore benchmark; violence anime V5; ViT violence Apache-2.0; quét độc lập Sintel; quét brand/logo exhaustive V0.3.2 và Florence-2 khoanh vùng | Dùng quyết định review thật để giảm báo dư mà không làm mất recall |
| 2. Video Processing | Hoàn thành POC end-to-end | Hàng đợi review chung; bốn quyết định; khóa edit plan; preview; phê duyệt; render CUT/BLUR có audio; kiểm tra nguồn và giới hạn 3,5 GB | Mở rộng dần từ lỗi video thực tế, không chặn phase kế tiếp |
| 3. BiliBili Upload POC | Chưa bắt đầu | Chưa có | Xác minh đúng Creator Center, upload draft, poster/metadata, success detection và retry |
| 4. Telegram Integration | Chưa bắt đầu | Chưa có | Bot allowlist, secret store, status/approve/stop/retry và thông báo |
| 5. End-to-End Automation | Đã có luồng review→export một cổng | Review UI nhận đề xuất, finalize một nút, background export và job-state riêng theo source/decision hash | Input watcher, metadata/upload BiliBili và Telegram |
| 6. Reliability & Optimization | Nền móng hoạt động | Storage thresholds, cleanup dry-run, local-only, source preservation, khóa GPU/render liên process, output cô lập và full-decode validation | Resume sau Windows restart, retention sau khi upload thành công và archive verification |

## Các bài test đã hoàn tất

- PyTorch CUDA nhận RTX 2060 6 GB; local NSFW inference chạy được.
- FFmpeg/FFprobe portable chạy trên ổ E; CPU H.264/x265 hoạt động.
- NVENC của FFmpeg hiện tại không tương thích driver hiện tại; chưa cập nhật driver.
- NSFW baseline trên Tears of Steel 720p: 734 frame, 12,270 giây, 59,836× real-time; hai cảnh báo đều là false positive do ánh sáng đỏ/hồng.
- OCR toàn phim Conan: 1.988 frame ở chu kỳ 3 giây, 307,775 giây, 19,382× real-time; chỉ một dải quảng cáo thật ở 00:03:06–00:03:32.
- NSFW toàn phim Conan: 5.964 frame, 125,121 giây, 47,676× real-time; 23 đoạn vượt ngưỡng đều không phải nội dung 18+ sau khi xem ảnh mạnh nhất.
- Gore toàn phim Conan: 5.964 frame, 121,076 giây, 49,269× real-time. Model bỏ sót cảnh máu thật tại 01:01:39 và ưu tiên nhầm title đỏ; baseline bị loại.
- Violence toàn phim Conan: 5.964 frame, 117,625 giây, 50,714× real-time. Model tạo 169 interval/514 giây ở ngưỡng 0,5 nhưng vẫn bỏ sót cảnh máu thật; baseline bị loại.
- OCR Conan 5 phút: 100 frame, 22,254 giây, 13,481× real-time; tìm đúng banner `i999.ai`.
- Refine endpoint: 100 frame trong cửa sổ 50 giây ở chu kỳ 0,5 giây, 30,082 giây; banner cuối cùng khoảng 00:03:31.
- V8 dùng look-ahead 3 giây và tail 0,5 giây. Frame preview 0:28 còn blur; frame 0:29,5 đã sạch và không blur.
- Benchmark 18+ POC: nano bắt đúng 17/20 dương tính, đúng 12/22 âm tính ở ngưỡng 0,50. Ở ngưỡng 0,95, nhánh anime đạt recall 80% và specificity 80%; nhánh phim thật bắt 9/10 dương, đúng 2/2 âm.
- Gore phim thật: 8/10 dương và 20/20 hard negative; recall 80%, precision 100%, balanced accuracy 90%.
- Gore hoạt hình: 9/10 dương và 16/20 hard negative; recall 90%, precision 69,2%, balanced accuracy 85%.
- Smoke test gore hoạt hình 35 giây: 70 frame trong 4,532 giây (7,724× thời gian thực); temporal gate loại 2 hit rời rạc, không tạo interval sai.
- Violence phim thật: VideoMAE-small đúng 8/10 dương và 19/20 âm; recall 80%, precision 88,9%, balanced accuracy 87,5%.
- Violence anime: tagger anime đúng 8/10 dương và 19/20 âm; recall 80%, precision 88,9%, balanced accuracy 87,5%.
- Scanner bạo lực phim thật chạy khoảng 6,1× thời gian thực, dùng khoảng 140 MB VRAM; nhánh anime dùng khoảng 512–639 MB VRAM tùy batch.
- Falconsai bị loại vì báo sai 22/22 hard negative; model nano dùng khoảng 248 MB VRAM trong benchmark song song.
- Unit tests hiện tại: 97/97 passed, gồm policy detection, semantic OCR routing, text continuity, review queue, bulk suggestion, visual-logo regional/temporal grouping, Florence focus region, khóa tài nguyên, regression dataset, SQLite/job import, scheduler, AI Supervisor protocol, finalize một cổng, full-decode final render và license gate.

## Field scan Conan 99 phút — 2026-09-20

| Nhánh | Tốc độ | Raw/confirmed hit | Interval | Tải review | Kết luận |
|---|---:|---:|---:|---:|---|
| 18+ anime | 33,68× real-time | 103 / 74 | 13 | 7,85/giờ | Tải duyệt đạt, nhưng 13 ảnh đại diện đều là cảnh người nằm/chạm, ánh đỏ, chim hoặc cảnh sinh hoạt; cần hard-negative mining |
| Blood/gore anime | 7,85× real-time | 620 / 464 | 69 | 41,64/giờ | Có candidate đáng xem nhưng lẫn nhiều người nằm, cảnh tối và màu đỏ; vượt gate 20/giờ |
| Violence anime | 7,81× real-time | 1.310 / 1.143 | 124 | 74,83/giờ | Nhãn `fire` lấn át top candidate; đang trộn nguy hiểm môi trường với bạo lực trực tiếp |

Ba báo cáo chiếm khoảng 15,33 MB và lưu 1.741 thumbnail; riêng violence lưu 1.143 thumbnail/9,33 MB. Dữ liệu không lớn cho một phim nhưng sẽ phình tuyến tính khi chạy lâu dài. Báo cáo mới phải chỉ giữ một ảnh mạnh nhất cho mỗi interval cùng top-K giới hạn; báo cáo cũ được nén hoặc xóa theo retention sau khi upload thành công.

Mô phỏng từ `max_score` của interval blood/gore cho thấy ngưỡng 0,20 còn 32 interval, tương đương 19,3/giờ, và vẫn giữ candidate mạnh nhất 4.901,5–4.909 giây. Đây mới là phương án hiệu chỉnh cần regression test, chưa phải threshold chính thức.

## Baseline anime V3 sau cải tiến

- Một lượt WD tagger dùng chung cho gore và violence mất 767,539 giây, thay cho 1.523,969 giây của hai lượt cũ: nhanh hơn gần 2 lần cho phần tagger và tiết kiệm khoảng 49,6% thời gian.
- Gore dùng hai tầng điểm 0,20/0,05, context 0,02, temporal 3/5 cho điểm cao và 4/5 cho context. POC đạt recall 80%, specificity 80%, balanced accuracy 80%.
- Gore field còn 33 interval sau khi gom các candidate cách nhau tối đa 6 giây, tương đương 19,92/giờ. Candidate 4.901,5–4.909,5 giây vẫn được giữ.
- Violence chỉ dùng tag hành vi trực tiếp; `fire`/`explosion` được ghi thành danger và không kích hoạt violence. Field còn 19 interval, tương đương 11,47/giờ; top candidate là punching, fighting, kicking và strangling.
- Báo cáo V3 giữ 33 ảnh interval gore, 19 ảnh interval violence và tối đa 20 top candidate mỗi nhánh. Dung lượng dưới 1 MB thay vì khoảng 13,17 MB của hai báo cáo anime cũ.
- Ở V3, bộ positive violence anime cũ gồm cảnh injury và fire danger nên kết quả 80% khi đó bị hạ trạng thái. Thiếu sót này đã được xử lý bằng benchmark direct-violence V5 ở phần dưới.

## Baseline anime V4 — shared adult/gore/violence

- V4 tạo cả ba báo cáo adult, gore và violence từ một lượt WD tagger 754,749 giây; thêm adult score không làm chậm V3.
- Adult nano trên manifest mở rộng chỉ đạt recall 80%, specificity 63,64%, balanced accuracy 71,82% cho anime và bị thay thế.
- Adult WD policy dùng 20 nhãn giải phẫu/hành vi cụ thể, bỏ nhãn `explicit` tổng quát, ngưỡng union 0,15 và yêu cầu ít nhất 3 nhãn đạt 0,05. Trên 10 positive + 33 negative anime: recall 100%, specificity 96,97%, precision 90,91%, balanced accuracy 98,48%.
- Field V4 trước điều kiện co-occurrence tạo 7 interval adult, 4,22/giờ và đều là false positive khi xem ảnh. Sau policy cuối, chỉ 1/7 strongest frame còn vượt điều kiện trước temporal gate; lần quét kế tiếp sẽ đo exact interval count mà không cần thêm một lượt full scan trong vòng này.
- Gore giữ 33 interval, 19,92/giờ. Violence giữ 19 interval, gồm 8 high priority và 11 context priority.
- Audit violence provisional: 7/8 high-priority interval là hành vi trực tiếp rõ ràng; 11 context interval gồm 6 false positive đã quyết định và 5 `NEEDS_MORE_CONTEXT`. Đây là precision audit, không đo recall.

## Baseline anime V5 — temporal ensemble và video độc lập

- Thay benchmark violence anime cũ bằng 112 clip Sintel có hành vi trực tiếp và 14 clip Conan đã review: tổng 126 mẫu, gồm 59 dương và 67 âm từ hai video.
- Policy cuối lấy hợp của VideoMAE temporal ở ngưỡng 0,61 và WD direct-action ở ngưỡng 0,20. VideoMAE bắt chuyển động kéo dài; WD bù các cảnh anime mà model video bỏ sót.
- Toàn bộ tập đạt recall 86,44%, precision 80,95%, specificity 82,09% và balanced accuracy 84,27%.
- Riêng holdout Sintel chưa dùng để chọn ngưỡng đạt recall 85,71%, precision 82,76% và balanced accuracy 84,52%.
- Full scan Sintel tạo 20 interval sau khi gom khoảng cách review 8 giây, gồm 7 high và 13 context; báo cáo khoảng 231 KB, không tạo output.
- 14 nhãn Conan là field review provisional; chúng đủ kiểm tra chéo phong cách ở vòng này nhưng sẽ tiếp tục được thay bằng nhãn độc lập khi có thêm video thực tế.

## Những việc chưa được xem là đã test xong

- POC 18+ đã có số đo hai chiều nhưng chưa đủ gate chính thức: mỗi nhánh vẫn thiếu 30 mẫu dương, 60 hard negative và video độc lập thứ hai.
- Blood/gore đã đạt gate POC nhưng chưa đạt field gate 30 dương + 60 âm mỗi phong cách.
- Violence anime đã đạt baseline hai video với 59 dương + 67 âm. Nhãn Conan vẫn là provisional nên chưa coi đây là chứng nhận production tự động.
- Quảng cáo chữ, logo có chữ và logo thuần hình ảnh đã có nhánh phát hiện local V0.3.2. V5 dùng prompt rộng giữ 134 cửa sổ; V6 quét dày 2 frame/giây với prompt chặt giữ 27 cửa sổ. Cả hai đều quét đủ 745/745 cửa sổ và giữ ảnh audit cho cả kết quả bị loại. Queue cuối lấy hợp OCR + V5 + V6 còn 70 mục. Đây là danh sách recall-first để con người duyệt, không phải 70 quảng cáo đã được xác nhận.
- Qwen2-VL-2B vẫn có cả false positive và false negative khi phân biệt logo ngoài phim với bảng hiệu trong bối cảnh. Phi-3.5 Vision INT4 thử trên 9 ảnh bắt đủ mẫu dương nhưng specificity 0%, nên trọng số/runtime đã bị xóa. Không model nào trong hai model này được phép tự quyết định sửa video.
- Đã có bản output POC và kiểm tra duration, audio stream, giải mã toàn bộ, điểm cắt, vùng blur, dung lượng cùng checksum nguồn. Chưa kiểm thử trên video đầu vào thực tế dung lượng lớn gần 3,5 GB.
- Chưa thao tác BiliBili hoặc Telegram. SQLite queue và phục hồi stage đã có trong V0.5.0; vẫn cần kiểm thử thực tế khi chủ động dừng một scan dài.

## Dung lượng hiện tại

Gói POC 18+ thêm khoảng 8,5 MB ảnh và 16,3 MB model. Gore thêm khoảng 3,1 MB ảnh, model phim thật 45,3 MB và model hoạt hình 378,7 MB. Violence dùng model ViT khoảng 343,2 MB và Qwen2-VL-2B khoảng 4,43 GB; nhánh anime dùng chung tagger gore. VideoMAE CC-BY-NC, Aleris, VideoMAE surveillance và SmolVLM thử nghiệm đã bị xóa sau khi giữ kết luận cần thiết. Ngày 2026-09-23 đã dọn thêm hơn 2,03 GB model/file tạm không đạt; benchmark Qwen nén còn dưới 5 MB.

Video thực tế `Tiếng Yêu Này Anh Dịch Được Không - Tập 1.mp4` dài 3.722,175 giây, 720p và 368,3 MB đã được thêm sau đợt dọn. Adult có 7 interval, gore không có interval vượt ngưỡng. ViT tạo 167 violence candidate; Qwen xác nhận lại trong 192,943 giây, loại 166 và giữ một interval 52:27,875–52:35,875 để người dùng xem. OCR giữ `NETFLIX SERIES`, `NGUONC.COM`, dòng bản quyền Netflix và `NETFLIX | DUBBING`. Người dùng phát hiện thêm logo N thuần hình ảnh tại 00:01,0–00:05,0. Queue V4 đã gộp intro Netflix thành CUT 00:00–00:10,5 và gộp toàn bộ phần Netflix cuối thành CUT liên tục 59:29,5–hết video. Preview V4 đã được duyệt và full render V4 đã hoàn thành, vượt đủ kiểm tra kỹ thuật.

Nhánh brand/logo exhaustive V5 quét 1.980 frame và toàn bộ 745 cửa sổ trong 326,592 giây. V6 tăng lên 7.563 frame ở chu kỳ 0,5 giây và hoàn thành 745 cửa sổ trong 395,023 giây, tốc độ 9,423× thời gian thực, RAM đỉnh khoảng 828 MB và CUDA khoảng 4,50 GB. V6 giữ 10 `CONFIRMED`, 17 `UNCERTAIN` và 718 `REJECTED`; prompt chặt đã loại được nhiều cảnh phim nhưng cũng bỏ chữ Netflix 5–10 giây mà OCR bắt được. Vì vậy không dùng V6 thay V5 mà lấy hợp cả hai với OCR.

Trang `reports/tieng-yeu-tap-1-visual-logo-exhaustive-v6/audit.html` có ảnh cho đủ 745 cửa sổ và lọc được Confirmed/Uncertain/Rejected; ảnh audit V6 chiếm khoảng 8,72 MB. Florence khoanh vùng 27/27 mục V6 trong 100,615 giây. Queue hợp nhất `reports/tieng-yeu-tap-1-brand-review-v6/review-queue.json` còn 70 mục, gồm 56 visual và 14 text; 69 mục có vùng gợi ý. Chưa có chỉnh sửa mới nào được áp dụng.

Ngày 2026-09-23, nhánh text được nâng lên semantic V7. Mã không còn quyết định quảng cáo bằng danh sách từ khóa: EasyOCR cung cấp chữ, tracker yêu cầu liên tục cả vị trí lẫn nội dung, model embedding local phân biệt quảng cáo/phụ đề/danh đề/chữ trong cảnh, sau đó temporal/geometry router xử lý lớp phủ và credit roll. Quy tắc Netflix được tách thành policy người dùng chỉ ép review. Model thêm 450.156.032 byte trên ổ E và chạy offline. Tập holdout khởi đầu nhỏ đạt 14/16; đây là smoke benchmark, chưa đại diện độ chính xác production.

Full scan V5/V7 trên cùng video xử lý 1.241 frame trong 369,466 giây, 10,074× thời gian thực và RAM đỉnh 1.277.317.120 byte. V7 vẫn giữ `NETFLIX SERIES`, toàn bộ Netflix cuối phim và banner `NGUONC.COM`; 160+ credit/chữ trong cảnh được tách khỏi candidate. Queue V2 dùng bốn text track có preview rõ và kết quả violence đã xác nhận; queue V1 38 mục được giữ làm đối chiếu vì chưa có quyết định nào cần chuyển.

## Thứ tự thực hiện tiếp theo

1. Duyệt 70 mục trong `reports/tieng-yeu-tap-1-brand-review-v6/review-queue.json`: `KEEP` cho tiêu đề/bảng hiệu thuộc nội dung phim; `BLUR` hoặc `CUT` cho logo hãng, watermark, tài trợ, quảng cáo và credit ngoài nội dung muốn giữ. Dùng `reports/tieng-yeu-tap-1-visual-logo-exhaustive-v6/audit.html` để rà thêm các cửa sổ Qwen loại khi cần.
2. Dùng các quyết định đã duyệt làm regression và thư viện tham chiếu logo động; không hardcode tên thương hiệu vào mã. Sau khi queue không còn mục chờ, mới tạo edit plan và preview mới.
3. Chỉ xuất bản video mới sau khi người dùng duyệt toàn bộ preview; tiếp tục áp giới hạn 3,5 GB và kiểm tra checksum nguồn.
4. Trước upload, ghi xác nhận quyền đối với video/nhạc/phụ đề/logo; sau đó bắt đầu Phase 3 bằng bản nháp BiliBili, chưa publish công khai.
5. Sau upload thành công mới nén archive, áp dụng retention và triển khai Telegram allowlist để duyệt từ xa.

## Review workflow V1 — 2026-09-22

- Đã thêm lệnh hợp nhất nhiều báo cáo của cùng video thành một queue JSON/HTML và gộp candidate trùng trong cùng nhóm.
- Mỗi mục có ID ổn định, timestamp, mức ưu tiên, score, nhãn, ảnh và đường dẫn bằng chứng.
- `build-edit-plan` bị chặn khi còn mục chưa duyệt hoặc `NEEDS_MORE_CONTEXT`.
- `BLUR` bắt buộc có vùng pixel hoặc xác nhận rõ full-frame; `CUT` và `BLUR` chỉ tạo preview ngắn ở gate hiện tại.
- Smoke test bằng video tự tạo xác nhận preview blur và cut đều có một luồng hình, một luồng tiếng; checksum nguồn không đổi; không tạo final output. Toàn bộ file smoke test đã được xóa.
- Queue Sintel thực tế có 39 mục: 18 gore và 21 violence sau khi gộp hai model. Hiện tất cả đang chờ quyết định, chưa có edit plan.
- Đã thêm giao diện cục bộ có nút `Giữ nguyên`, `Làm mờ`, `Cắt bỏ`, `Cần xem thêm` và `Bỏ chọn`; lựa chọn lưu ngay vào queue, có bộ lọc chưa duyệt/ưu tiên cao/gore/violence và thanh tiến độ.
- Đã thêm bulk `Giữ nguyên tất cả đang lọc` có bước hỏi lại; bulk blur/cut bị cấm. Giao diện hiển thị dung lượng nguồn/report, dung lượng trống và ước tính preview, đồng thời nêu rõ review không chạy AI/FFmpeg.
- Queue đã có trường `actor`/`transport` và audit log giới hạn để cùng một lõi quyết định có thể dùng cho Telegram. Thiết kế ưu tiên Telegram long polling, allowlist và chỉ gửi thumbnail khi người dùng bật truyền dữ liệu ra ngoài.
- Queue Sintel đã được giải quyết theo kế hoạch thử nghiệm: 37 KEEP, 1 CUT và 1 full-frame BLUR. Hai preview 6,0 giây và 9,5 giây đã được duyệt; bản cuối được dựng trong 53,364 giây, nhỏ hơn giới hạn 3,5 GB và vượt kiểm tra hình/tiếng/thời lượng/checksum.

## Acceptance gate kế tiếp

Gate Video Processing POC đã đạt. Với yêu cầu mới “chỉ giữ nội dung phim”, gate đang hoạt động là giải quyết queue brand/logo V6 rồi duyệt preview mới. BiliBili Upload POC ở chế độ draft chỉ bắt đầu sau gate này.
