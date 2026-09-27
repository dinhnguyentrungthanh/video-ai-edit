# 0.7.21 - 2026-09-27

- Preserve the open or closed state of each video's `Chạy lại kiểm tra` panel across the Dashboard's three-second refresh cycle.
- Keep detector checkbox drafts visible while the panel is open and clear the saved panel state only after a rerun is successfully queued.
- Sort sibling stage-artifact roots deterministically so shared gore/violence cache snapshots cannot miss only because set iteration returned a different directory order.
- Keep detector commands, review data and media processing unchanged; the cache fix only makes an existing exact-match restore deterministic.

# 0.7.20 - 2026-09-27

- Complete live-action adult scenes around high-confidence NSFW seeds instead of exposing only isolated frames above the global threshold.
- Keep `0.95` as the only threshold that can create a review item; use `0.70` evidence only to extend an existing item by at most eight seconds, so moderate standalone frames do not add false findings.
- Merge review gaps up to three seconds inside one adult sequence while leaving longer gaps and unrelated candidates separate.
- Pin the sequence policy in processing profiles and adult-stage commands, which prevents an older cached adult report from satisfying a rerun after this detector upgrade.
- Recheck Troy source scores: the fragmented `07:01.5–07:40.5` detections now form one review interval covering `06:54.5–07:45.5`; the separate `07:55–07:58` candidate remains independent.
- Keep advertising/logo, gore, violence, review decisions and rendering unchanged; pass 223/223 automated tests.

# 0.7.19 - 2026-09-27

- Keep the export progress bar visible at 100% on completed video cards.
- Show `100% · Đã xuất video` together with the localized completion timestamp from the durable job record.
- Keep this presentation-only: no renderer, scheduler, detector, review or output behavior changed.
- Pass JavaScript syntax validation and 211/211 automated tests.

# 0.7.18 - 2026-09-27

- Add live FFmpeg export telemetry without changing encoding or edit decisions: output percentage, speed and estimated time remaining.
- Calculate export percentage against the expected post-CUT duration so edited videos reach 100% accurately.
- Show a separate export progress bar while the existing analysis bar remains dedicated to scan/review preparation.
- Treat FFmpeg completion as a visible `Đang kiểm tra output` phase until duration, streams and full decode validation finish.
- Divide the `Đang chạy` tab into `Đang phân tích để duyệt` and `Đang xuất video` sections.
- Store telemetry only in a small temporary file and remove it after success, failure or interruption.
- Verify the installed FFmpeg `-progress` protocol with a real two-second encode; pass 211/211 automated tests.

# 0.7.17 - 2026-09-27

- Reorganize the Control Center video list into `Đang chờ xử lý`, `Đang chạy`, and `Hoàn tất` tabs with live counts.
- Give every video separate, readable status cards for scene analysis, local structure audit, Visual AI Audit, and final export.
- Distinguish `Chờ xuất video`, `Đang xuất video`, `Đang kiểm tra output`, and `Đã xuất video` from the generic internal job state.
- Move rerun detector choices into a compact disclosure panel so routine actions and status remain easy to scan.
- Keep the dashboard change frontend-only: API payloads, job states, scheduler, detectors, review decisions, renderer, and existing outputs are unchanged.
- Verify the live server HTML and JavaScript syntax; pass 208/208 automated tests.

# 0.7.16 - 2026-09-27

- Remove short regional blur operations already covered by an equivalent persistent blur before building the FFmpeg graph.
- Combine identical full-frame blur operations into one filter with multiple time ranges instead of creating one filter per review decision.
- Reduce Troy job #39 from roughly 66 serial blur filters to one persistent regional overlay and one time-ranged full-frame blur while preserving all approved decisions.
- Stop the complete Windows stage process tree during an immediate pause, then remove only that render's incomplete `.partial` output so a retry cannot leave an orphaned FFmpeg process or fail on stale output.
- Verify live recovery of Troy job #39: attempt 3 entered `RENDERING` and the partial output grew continuously after the optimized restart.
- Pass 207/207 automated tests.

# 0.7.15 - 2026-09-27

- Prevent one OCR observation that joins a fixed watermark with nearby scene text from expanding a full-film blur region.
- Build repeated corner-overlay regions from the dominant size and position consensus while preserving small localization variations.
- Refine existing persistent text-logo proposals from repeated, spatially consistent visual OCR regions, so completed text scans do not need to run again.
- Tighten Troy job #39 XEMBZ.NET from `x=110, y=146, 626x84` to `x=105, y=160, 174x54` using 51 corroborating OCR regions; local structure audit remains PASS.
- Pass 204/204 automated tests.

# 0.7.14 - 2026-09-27

- Separate concrete regional logo leads from generic full-frame temporal hits during semantic candidate selection.
- Require every strong regional lead to reach the local VLM, while representing ordinary full-frame hits with complementary ident-like and persistence-like samples in every five-minute bucket.
- Treat weak coarse-tile advantages as full-frame coverage so low-motion movie footage cannot exhaust the careful scan budget.
- Keep detector coverage strict: missing regional leads or missing time-bucket representatives still produce an incomplete manifest and a local structure BLOCK.
- Revalidate Troy job #39 without rerunning adult or violence detection: 285/285 regional candidates and 80/80 required full-frame representatives are covered; 1,889 redundant full-frame hits are collapsed; local structure audit now passes.
- Pass 202/202 automated tests.

# 0.7.13 - 2026-09-27

- Add a per-video export-size selector to Review: the existing 3.5 GB default, a custom GB ceiling, or unlimited output size.
- Persist the selected policy in the review queue and edit plan, and pass it through the durable scheduler to the final renderer.
- Give custom and unlimited exports distinct output identities so changing a video's size policy cannot collide with an existing render.
- In unlimited mode, use CRF 20 without a VBV size ceiling while retaining disk-reserve, A/V, duration, full-decode and source-checksum validation.
- Keep old queues and commands on the 3.5 GB default.
- Preserve each new video's content-style and speed-profile drafts while the dashboard refreshes every three seconds, so `Phim thực tế` no longer resets to `Hoạt hình` before Start.
- Pass 200/200 automated tests.

# 0.7.12 - 2026-09-26

- Show the exact decision scope on every Review card: continuous track, grouped detection window, single interval, or optional candidate.
- State explicitly that a decision never propagates to every visually similar logo or advertisement unless those occurrences were already consolidated into the displayed track.
- Explain that optional candidates are uncorroborated findings outside the main decisions; selecting one promotes only its displayed interval into the edit plan.
- Warn on single-purpose scans that fewer cards reflect skipped detector groups and do not certify those groups as safe.
- Prevent a repeated approved watermark from consuming the semantic candidate budget for a full-length video.
- Select every novel visual-logo window first, then retain at least one representative for each approved brand in every five-minute timeline bucket.
- Raise the careful profile ceiling to 420 windows and the fast profile ceiling to 120 while collapsing repeated approved-brand evidence.
- Record novel-window and approved-brand time-group coverage separately in every visual-logo report.
- Propagate detector omissions into the review queue and deterministic AI Audit gate; an incomplete scan can no longer be reported as a complete edit plan.
- Require the exact localized region to provide its own brand proof. Frame-level Qwen confirmation no longer lets Florence or GroundingDINO boxes inherit a BLUR action.
- Allow only confirmed approved-brand memory to create a persistent timeline; weak memory similarity and opening promotions cannot be stretched to the end of a movie.
- Keep separate visual boxes separate below 50% IoU so OCR proof cannot leak into a nearby DINO region; compact OCR noise and ordinary words no longer qualify as site marks.
- Route low-ad scene text and uncorroborated regional candidates to the optional audit list while counting them as represented coverage.
- Verify Conan Movie 20 end to end: 320/320 novel windows and 70/70 approved-brand time groups are retained, 900 repeated watermark windows are collapsed, coverage is complete, and the mandatory queue contains only the full-timeline PhimOnline blur plus the 5–10 second opening ident CUT.
- Confirm that advertising-only and all-model runs build identical advertising stages.
- Pass 196/196 automated tests.

# 0.7.11 - 2026-09-26

- Distinguish required review items from optional evidence directly in the Review UI.
- Show the optional-candidate count in the summary and on a prominent amber `Ứng viên phụ (N)` button placed next to `Chưa duyệt`.
- Rename `Tất cả` to `Tất cả mục chính` and explain that optional candidates do not block export.
- Verify the review and Control Center test suites and parse-check the rendered Review JavaScript.

# 0.7.10 - 2026-09-26

- Separate frame-level logo confirmation from region-level proof, preventing a real corner watermark from turning an unrelated face, object, or OCR fragment into a required logo review.
- Route single-source, unclassified regional proposals to optional audit candidates. They remain visible and can be promoted by the reviewer, but do not block export.
- Route non-overlay OCR fragments below 20% advertisement probability to optional audit candidates while persistent overlays and policy matches remain mandatory review items.
- Keep full compatibility with existing scan reports, so completed jobs gain the new routing without rerunning local models or Visual AI Audit.
- Rebuild Conan Movie 20 as review revision 2 from cached reports: required items fall from 19 to 4, the approved full-timeline PhimOnline blur is preserved, and all weak evidence remains available among 61 optional candidates.
- Pass 174/174 automated tests.

# 0.7.9 - 2026-09-26

- Let every video select any combination of advertising/logo, adult, gore, and violence detection, with an explicit all-groups option.
- Build only the selected pipeline stages and reports. Advertising-only jobs skip all safety models; single-purpose live-action jobs skip unrelated models.
- Preserve selected detector groups per job and allow a rerun to choose a different scope while keeping earlier revisions.
- Record selected and skipped groups in every new review queue and show both on the dashboard/review page, so an unscanned category is never presented as safe.
- Run deterministic JSON structure checks locally when the review queue is built. This uses no Codex session, model, media upload, or ChatGPT quota; Visual AI remains explicit opt-in.
- Keep the animation adult/gore/violence categories on one shared inference pass when any safety category is selected, preserving quality and avoiding duplicate decoding.
- Pass 169/169 automated tests and parse-check the rendered JavaScript for both Control Center and Review UI.

# 0.7.8 - 2026-09-26

- Reuse one persistent AI Supervisor thread for JSON Audit, Visual AI Audit, every visual batch, and subsequent videos.
- Serialize audit turns so concurrent video jobs cannot write into the shared thread at the same time.
- Keep the shared thread when the configured model or reasoning effort changes; create a replacement only when the saved thread cannot be resumed.
- Show shared-session state in Control Center.

# 0.7.7 - 2026-09-26

- Tighten an approved brand-memory box from foreground contrast in the current full-resolution frame, with conservative no-op gates for noisy or ambiguous backgrounds.
- Ensure a consolidated end-card uses a primary thumbnail captured inside its refined interval.
- Require explicit full-frame boundary semantics before a final sequence can become a CUT suggestion; a corner watermark over black frames is insufficient.
- Cross-check localized visual-logo regions against low-ad OCR scene text so an unrelated frame-level watermark cannot turn a book, sign, subtitle, or interface label into a blur suggestion.
- Attach source-frame dimensions to text review items and clip their proposed rectangles to valid frame bounds.
- Verify the Conan regression queue at 79/79 covered candidates with no missing references: the persistent PhimOnline watermark is tightened from 308x69 to 282x46, `SCRAP BOOK` stays as in-film text, and no false full-scene end-card CUT remains.
- Pass 161/161 automated tests and the local license audit with 9 allowed models and 0 blocked models.

# 0.7.6 - 2026-09-26

- Return the relative region of the winning brand-memory signature instead of the last record inspected.
- Add a multi-record regression proving that a correct top-corner watermark match cannot inherit another record's geometry.

# 0.7.5 - 2026-09-26

- Preserve every unresolved review item when rebuilding the same source queue after a detector upgrade.
- Mark preserved candidates with migration provenance so algorithmic suppression never substitutes for a human decision.

# 0.7.4 - 2026-09-26

- Route high-confidence brand-memory matches to the exact learned relative region instead of relocalizing every text fragment in the frame.
- Reject regional pHash matches whose position, scale, or aspect ratio conflicts with the approved signature.
- Exclude full-frame CUT/scene decisions from the regional brand-memory library.
- Prevent an approved brand-memory region from being relabeled as a movie title and suppress unrelated Grounding DINO fallbacks.

# 0.7.3 - 2026-09-26

- Clarify that logo classification applies only to the red target region, while persistent watermarks elsewhere in the same frame are handled by their own review item.
- Stop unrelated overlapping regional blurs from appearing as coverage for the current target.
- Warn before a reviewer overrides a high-confidence Visual AI decision with a conflicting edit.

# 0.7.2 - 2026-09-26

- Absorb short OCR fragments that are fully contained in a confirmed persistent watermark region and timeline; preserve their evidence under the main review item.
- Conan revision 2 now has 93 pending items after removing one redundant NPt watermark fragment. No decision was applied; integrity blockers and deterministic quality findings remain empty.
- Full test suite passes: 148/148.

# 0.7.1 - 2026-09-26

- Scope AI Audit state and artifacts to the active queue revision so reruns never display or overwrite an older audit.
- Generate deterministic region-aware review IDs and block duplicate IDs in the deterministic audit gate.
- Treat branded end cards as one full-scene CUT proposal even when a region model also proposes BLUR.
- Include source frame size and target-region guidance in Visual AI evidence to avoid classifying a logo outside the reviewed box.
- Suppress bulk suggestions when high-confidence Visual AI conflicts with the local proposal; the item remains pending for human review.
- Migrated Conan revision 2 without applying decisions: 94/94 unique IDs, 35 Visual AI assessments retained, final end card proposed as CUT.
- All 148 tests pass.

# 0.7.0 - 2026-09-26

- Thêm benchmark độc lập SigLIP + GroundingDINO Tiny trên quyết định review thực tế.
- Thêm cache dùng chung theo hash ảnh/model/cấu hình; không sửa queue hay video production.
- Thêm cổng chất lượng recall/specificity trước khi cho phép tích hợp vào pipeline chính.
- A/B PaddleOCR với EasyOCR trên 34 ảnh thật; giữ EasyOCR vì cùng recall vùng BLUR nhưng nhanh hơn khoảng 43% theo thời gian mỗi ảnh.
- Chỉ duyệt model local, miễn phí và Apache-2.0 theo license policy hiện hành.

# Changelog

## 0.6.1 — 2026-09-25

- Thêm Visual AI Audit theo quyền riêng của từng job. Dashboard chỉ gửi thumbnail sau hộp xác nhận; tối đa 36 ảnh trong reports, không gửi video hoặc âm thanh nguồn.
- Codex App Server nhận localImage trực tiếp và trả phân loại có cấu trúc: logo thương hiệu, tiêu đề phim, quảng bá, nội dung phim, adult, gore, violence, false positive hoặc uncertain; kèm độ tin cậy, đề xuất xử lý và đánh giá vùng blur.
- Nhận xét Visual AI được gắn vào từng thẻ review và có thể trở thành đề xuất để người dùng nhận hàng loạt. AI không tự đặt KEEP/BLUR/CUT và không khởi động render.
- Khóa chi phí giữ chatgpt_required, chatgpt_included_only, api_key_allowed=false, GPT-5.6 Luna/Medium mặc định và giới hạn tối đa 50 thumbnail.
- Visual audit chia thành các batch 12 ảnh và hợp nhất theo item ID để tránh giới hạn ảnh mỗi lượt; prompt nhận manifest queue rút gọn nên không phụ thuộc vào việc model tự mở JSON.
- Test thực tế Conan cho thấy low-detail không được GPT-5.6 Luna nhận ổn định; mọi thumbnail nay dùng high-detail. Lượt cuối nhận đủ 36/36 đánh giá: 24 KEEP, 1 CUT, 1 BLUR, 10 NEEDS_MORE_CONTEXT và 0 quyết định tự động.
- Smoke test ảnh tổng hợp bằng GPT-5.6 Luna/Medium nhận đúng logo ngoài, đề xuất BLUR với độ tin cậy 0,99 và vùng TIGHT. Toàn bộ 137 test đạt; license audit giữ 8 model được phép, 0 model bị chặn.

## 0.6.0 — 2026-09-25

- Launcher Control Center dùng entrypoint nhẹ, không nạp trước Torch và toàn bộ scanner AI. Warm start dùng database hiện có, chặn double-click bằng launcher mutex và giữ cửa sổ lỗi để không còn trạng thái bấm Start nhưng tưởng như không chạy. Có thể dùng `Start-BiliFlow.cmd -RefreshExisting` khi cần nhập lại toàn bộ lịch sử.

- Thêm bộ nhớ logo cục bộ từ quyết định review: 27 chữ ký được dựng từ 15 queue cũ (7 BLUR, 20 CUT). Chữ ký chỉ route candidate; không tự sửa và không chuyển timestamp/vùng edit sang video mới.
- Thêm cache routing logo nén gzip theo SHA-256 video, cấu hình scanner, phiên bản thuật toán và revision bộ nhớ. Stage lỗi sau routing có thể chạy lại mà không giải mã lại phần tìm ứng viên.
- Quét thích ứng giữ fallback theo từng đoạn 5 phút và route thêm chuyển cảnh có tín hiệu visual để giảm nguy cơ bỏ sót logo dưới ngưỡng heuristic.
- Gom được nhiều persistent logo ở các vùng khác nhau thành các track riêng; review không còn gộp các candidate kề nhau nếu vùng Florence không chồng nhau.
- Florence ưu tiên visual grounding trước OCR title khi thương hiệu chưa xác định. Kiểm chứng NewGates tạo đúng vùng `130,104,159,67` và `97,13,240,224` trên hai cửa sổ liên tiếp.
- Cache logo cũ hơn 30 ngày được liệt kê trong cleanup dry-run; `state/brand-memory.json` được giữ vì nhỏ và không chứa video.
- Dashboard tự làm mới session token sau khi backend restart, báo rõ kết quả lưu/kiểm tra AI, phân biệt `AI: chờ queue` với lỗi kết nối, xác nhận khi backend đã tắt và hỗ trợ `Chạy lại từ đầu` bằng report revision riêng.
- Chế độ `codex_command: auto` tự tìm Codex Desktop trong LocalAppData và truyền `CODEX_HOME` từ hồ sơ Windows, nên mở dashboard bằng double-click vẫn nhận đúng đăng nhập ChatGPT mà không lưu token vào project.
- Báo cáo OCR mới ghi SHA-256 của video nguồn; AI Supervisor có thể đối chiếu đồng nhất nguồn trên toàn bộ detector trước khi đưa queue ra review.
- OCR gom các lần đọc chập chờn nhưng cùng chữ và cùng góc; chỉ đề xuất `persistent_overlay` khi có thêm bằng chứng quảng cáo/thương hiệu. Chuỗi lặp lại có điểm quảng cáo thấp được giữ như tiêu đề phim.
- Queue đối chiếu vùng visual-logo với OCR tiêu đề: vùng chồng khớp được đề xuất KEEP, ngăn xác nhận logo thật ở một góc làm hệ thống blur nhầm tên phim ở góc khác.
- Tách các proposal Florence trong cùng frame thành item review độc lập và thêm phân loại theo vùng. Bộ nhớ logo ghi cả chữ ký âm từ quyết định KEEP, nhưng chỉ áp dụng lên vùng crop; nó không được phép loại cả frame khi nơi khác vẫn có logo.
- Review khoanh đỏ vùng đang được phân loại, hiển thị crop phóng to và tọa độ trước khi cho xác nhận `Vùng khoanh đỏ là tiêu đề phim` hoặc `Vùng khoanh đỏ là logo thương hiệu`. Phép chiếu vùng xử lý cả preview 16:9 và ảnh detector vuông có letterbox.
- Thêm `Xem tất cả ứng viên` với ảnh audit giới hạn dung lượng. Các cửa sổ VLM đã loại không chặn xuất; nếu người dùng chọn một ứng viên, nó mới được chuyển thành item chính thức. Giao diện có hai thao tác ngữ nghĩa `Đây là tiêu đề phim` và `Đây là logo thương hiệu`.
- Review phân biệt `Làm mờ logo`, `Làm mờ toàn cảnh` và `Cắt cả cảnh`; thẻ cảnh cũng báo khi thời gian đó đã được một quyết định blur ở mục khác che phủ.
- Kiểm chứng local 15 giây: 3/3 cửa sổ được giữ, 2 cửa sổ khớp bộ nhớ, cache hit ổn định ở hai report độc lập; Florence định vị 3/3. Kiểm chứng thêm frame 25 và 209 của `#874-B2` xác nhận logo trái được BLUR, title phải được KEEP. Review nay cho phép xem và đổi quyết định vùng logo liên kết ngay trong thẻ cảnh chưa duyệt. Cửa sổ đầu/cuối được phân loại theo ngữ cảnh để gộp quảng bá toàn khung thành CUT; vùng BLUR logo được hiệu chỉnh giữa grounding và OCR; AI Supervisor chặn các CUT đầu video thiếu và vùng logo quá nhỏ hoặc quá rộng. Queue dài có candidate coverage manifest, tách OCR không liên quan khỏi watermark dài và gom OCR/visual của cùng watermark thành một quyết định. Toàn bộ 134 test đạt và license audit giữ 8 model được phép, 0 model bị chặn.

## 0.5.2 - 2026-09-25

- Đổi mặc định AI Supervisor sang `gpt-5.6-luna` + reasoning `medium` theo giới hạn sử dụng Plus.
- Chỉ cho phép model thuộc dòng GPT-5.6; reasoning tối đa `high`. GPT-6 và XHigh/Max/Ultra bị chặn ở cả cấu hình lẫn API dashboard.

## 0.5.1 - 2026-09-25

- Thêm bảng kết nối AI Supervisor ngay trên dashboard: trạng thái Codex, nút mở luồng đăng nhập ChatGPT, kiểm tra lại và link tài liệu OpenAI chính thức.
- Thêm `config/ai_supervisor.json` để cấu hình đi cùng project khi chuyển máy.
- Khóa mặc định Supervisor ở `gpt-6-luna` + reasoning `low`; dashboard chỉ cho chọn Luna/Sol và Low/Medium, service tier luôn `default`.
- Chặn API key, Astra, High/XHigh và truyền media. Mỗi thread/turn App Server đều nhận lại model/effort đã khóa, không phụ thuộc model mặc định của máy.
- Đổi model/effort sẽ bỏ thread ID cũ để lượt kế tiếp tạo thread đúng cấu hình mới.
- Kiểm thử dashboard thật xác nhận Codex được nhận diện, đăng nhập ChatGPT sẵn có, Luna/Low được lưu và yêu cầu `high` bị từ chối HTTP 400. Bộ test đạt 99/99.

## 0.5.0 - 2026-09-25

- Thêm Control Center chạy theo phiên bằng `Start-BiliFlow.cmd` và `Stop-BiliFlow.cmd`; có single-instance lock, không cài service và đóng tab không làm dừng worker.
- Thêm SQLite WAL lưu job, stage, revision, artifact, event, setting và trạng thái input watcher; nhập 6 video cùng 14 review revision cũ mà không sửa report nguồn.
- Thêm scheduler một worker phù hợp RTX 2060 6 GB, hỗ trợ nhiều video trong queue, pause, stop-after-stage, cancel, retry và phục hồi stage sau khi ứng dụng/máy dừng bất ngờ.
- Thêm dashboard đọc tài nguyên CPU/RAM/GPU/ổ E, trang review riêng theo job và luồng xuất bền vững; render không còn phụ thuộc daemon thread của tab review.
- Thêm input watcher chờ file ổn định 60 giây và SHA-256 chống trùng; video mới dừng ở `NEEDS_METADATA` để người dùng chọn hoạt hình/phim thật/hỗn hợp.
- Thêm profile `careful` và `fast` cho mật độ safety, OCR và logo scan. Cả hai vẫn tạo queue cho người dùng duyệt trước khi sửa.
- Thêm AI Supervisor theo yêu cầu qua Codex App Server, dùng persistent thread và JSON schema; kết quả chỉ tư vấn, không tự BLUR/CUT/render và mặc định không gửi media.
- Giữ giới hạn output cứng 3.500.000.000 byte, source bất biến và toàn bộ gate kiểm tra final hiện có.
- Smoke test dashboard trên cổng phụ đạt: 6 job, 14 revision, review queue 8 mục, single-instance và shutdown không để listener/process. Bộ test tự động đạt 97/97.
- Upload BiliBili, Telegram/Tailscale và archive/retention automation được giữ ngoài phạm vi bản này theo quyết định của người dùng.

## 0.4.0 - 2026-09-24

- Nâng quét logo nhỏ bằng crop vùng chồng lấn và Qwen2-VL local; kiểm chứng thực tế đã gom logo NewGates Anime xuyên phim thành một candidate `6,000–429,312s` thay vì bỏ sót.
- Thêm theo dõi thời gian cho logo cố định và end-card: cảnh mở đầu, watermark xuyên phim và brand cuối phim giữ thành các quyết định độc lập, không còn bị gộp thành một mục kéo dài toàn video.
- Florence-2 giới hạn đề xuất theo vùng seed của detector; candidate NewGates chỉ còn một vùng `97,13,240,224`, không kéo sang tiêu đề phim bên phải.
- Giảm scan logo chất lượng cao của video kiểm chứng 445 giây từ 80 xuống 40 cửa sổ VLM, tối đa ba ảnh bằng chứng mỗi cửa sổ; lượt cuối hoàn tất trong 158,520 giây và còn ba mục logo cần xem.
- Giao diện review hiển thị đề xuất `BLUR/CUT`, cho nhận tất cả đề xuất theo bộ lọc, và có một nút `Hoàn tất duyệt và xuất video`. Nút này tạo plan, cấp quyền từ queue đã giải quyết, render nền và báo trạng thái ngay trên trang.
- Thêm khóa tài nguyên liên process: nhiều session có thể xếp hàng công việc GPU, gồm cả Florence qua `run.ps1 localize-visual-logo`, và final render mà không tranh VRAM hoặc ghi đè output.
- Final render giải mã toàn bộ file trước khi công nhận hoàn tất, tiếp tục giữ giới hạn cứng 3,5 GB và kiểm tra checksum nguồn.
- Tạo `annotations/review-regression-v1.json` từ sáu video đã review: 206 ví dụ gồm 14 BLUR, 29 CUT và 163 KEEP. Việc di chuyển sáu output cũ không ảnh hưởng dataset vì nguồn vẫn còn trong `input`.
- Sửa regression khiến các candidate logo kề nhau nhưng khác loại/hành động bị gộp mất đề xuất. Hàng kiểm chứng nay có đúng 8 mục, gồm persistent logo đề xuất BLUR và end-card đề xuất CUT.
- Bộ kiểm tra tự động đạt 83/83.

## 0.3.4 - 2026-09-24

- Người dùng duyệt đủ ba preview Tập 2; xuất file mới `output/tieng-yeu-tap-2-reviewed-v1.mp4` 577.014.939 byte, 3791,500 giây, 1 H.264 + 1 AAC. Giải mã toàn bộ không lỗi, checksum nguồn không đổi, đã đối chiếu frame đầu/vùng BLUR/cuối và lưu `final-qa-notes.md`.
- Tập 2: người dùng chốt CUT liên tục 3801,5 giây tới hết; queue giữ biên phát hiện gốc và edit plan gộp outro thành một operation. Đã dựng đủ ba preview (CUT intro, BLUR banner, CUT outro), kiểm tra frame/luồng/giải mã và checksum nguồn. Chờ duyệt preview trước khi xuất full.
- Mở job độc lập cho Tập 2: quét OCR/semantic, logo exhaustive, 18+, gore và violence bằng model local đã duyệt; queue 45 mục còn chờ người dùng, chưa có edit plan hoặc output.
- Queue tự gợi ý `vertical_only` khi hợp bounding box OCR của banner phủ kín chiều ngang nguồn, kể cả khi người dùng chọn BLUR trong giao diện; phép đo vùng vẫn theo từng video.
- Thêm audit 10 khoảng hở trong chuỗi logo/credit Tập 2 bằng ảnh exhaustive để hỗ trợ kiểm tra CUT liên tục; không có CUT tự động.
- Khi tạo edit plan, các CUT chồng nhau hoặc liền kề được gộp thành một operation preview; plan vẫn lưu ID candidate và biên phát hiện gốc của từng mục. Bộ kiểm tra hiện đạt 70/70.

- Sửa regression kế thừa quyết định: queue V7 lưu cả biên candidate gốc và biên người dùng điều chỉnh, thay vì làm mất các đoạn CUT liên tục khi tái tạo plan từ báo cáo mới.
- Khôi phục CUT intro liên tục `0–10,5s`; không còn khoảng hở 5–6 giây làm sót `A NETFLIX SERIES`.
- Khôi phục CUT cuối liên tục `3569,5–3722,175s`; loại toàn bộ Imaginus, Netflix và logo sau phần credit thay vì để hở giữa các candidate rời rạc.
- Vùng blur chữ dùng trim đáy thích ứng 10% của hộp OCR: banner này từ 80 px còn 72 px, giữ nguyên mép trên và feather 3 px.
- Thêm `vertical_only` cho banner chạy hết chiều ngang: blur phủ kín mép trái/phải và chỉ feather trên/dưới, tránh nhấp nháy giữa chữ thật và blur khi chữ vào/ra khung.
- Xuất V7 thành công: 629.702.558 byte, dài 3.554,012 giây, 1 luồng hình + 1 luồng tiếng, giải mã toàn bộ không lỗi, dưới 3,5 GB và checksum nguồn không đổi.
- Tăng bộ kiểm tra tự động lên 68 bài.

## 0.3.3 - 2026-09-24

- Hoàn tất review queue brand/logo V6: 70/70 mục đã quyết định thành 59 `KEEP`, 10 `CUT` và 1 `BLUR`; không còn mục chờ hoặc cần thêm ngữ cảnh.
- Thay dải blur cao cố định bằng hợp các bounding box OCR của chính video. Chiều cao, vị trí và feather được tính theo từng vùng chữ rồi khóa vào edit plan của video; không tái sử dụng tọa độ giữa các video.
- Vùng banner thực tế được tính thành `x=0, y=48, width=1280, height=80`, thấp hơn dải 105 px cũ. Feather 3 px che kín viền chữ nhưng vẫn giữ vùng tác động gọn.
- Sửa lỗi nhánh mặt nạ feather khiến FFmpeg có thể thương lượng định dạng xám và làm mất màu toàn khung; nhánh overlay nay được khóa ở `yuv420p`.
- Thêm preview theo operation bằng `render-previews --operation-id`. Phê duyệt mẫu chỉ mở khóa render khi dùng rõ `approve-previews --allow-sampled`; mặc định vẫn yêu cầu đủ mọi preview.
- Xuất V6 thành công: 631.425.474 byte, dài 3.573,031 giây, có hình và tiếng, dưới 3,5 GB, giải mã toàn bộ không lỗi và checksum nguồn không đổi.
- Tăng bộ kiểm tra tự động lên 67 bài; `pip check` sạch và license audit có 8 model được phép, 0 model bị chặn.

## 0.3.2 - 2026-09-23

- Thêm chế độ `scan-visual-logo --exhaustive`: quét đủ mọi cửa sổ 5 giây thay vì giới hạn 80 candidate, vẫn chạy local và không tự chỉnh video.
- Full scan video 62 phút kiểm tra 745/745 cửa sổ trong 326,592 giây; giữ 134 cửa sổ theo hướng ưu tiên recall và gộp với OCR thành queue 73 mục chờ duyệt.
- Lưu ảnh audit cho cả 745 cửa sổ và tạo `audit.html` có bộ lọc Confirmed/Uncertain/Rejected; 611 cửa sổ Qwen loại vẫn có bằng chứng để kiểm tra false negative mà không xuất lại video.
- V6 tăng mật độ toàn timeline từ 1 frame/2 giây lên 2 frame/giây: 7.563 frame, 745/745 cửa sổ, 395,023 giây. Prompt chặt giữ 27 cửa sổ nhưng bỏ sót chữ Netflix mà OCR đã bắt, nên kết quả cuối lấy hợp OCR + V5 rộng + V6 chặt và không dùng Qwen làm cổng duy nhất.
- Queue hợp nhất V6 còn 70 mục chưa duyệt (56 visual, 14 text), 69 mục có vùng gợi ý. Loại câu hướng dẫn Qwen lặp lại khỏi tên thương hiệu; mọi cửa sổ V6 vẫn có ảnh audit.
- Thêm Florence-2-base MIT làm tầng khoanh vùng sau xác nhận ngữ nghĩa. Benchmark 7 ảnh bắt 4/4 mẫu dương gồm logo N; không dùng Florence làm classifier vì phrase grounding báo vùng trên 3/3 cảnh âm.
- Khoanh vùng 134 candidate thực tế trong 397,663 giây; 130 mục có ít nhất một vùng, mọi vùng chỉ là gợi ý và vẫn cần người dùng chọn KEEP/BLUR/CUT.
- Thêm vùng Florence vào queue review để BLUR có thể dùng tọa độ nguồn; không tự duyệt hoặc tự áp dụng vùng.
- Thử Phi-3.5 Vision INT4 MIT trên 9 ảnh: recall 100% nhưng specificity 0%, 25,977 giây/ảnh. Model và runtime khoảng 2,8 GB đã bị xóa, giữ benchmark và khóa tải lại theo quality gate.
- Xác nhận Qwen2-VL-2B chưa phân biệt ổn định branding ngoài phim với bảng hiệu nằm trong cảnh; nhánh logo hiện là hệ thống báo cáo recall-first có human review, chưa phải bộ tự động xóa logo.

## 0.3.1 - 2026-09-23

- Thêm lệnh `scan-visual-logo` cho brand ident, logo và watermark không có chữ bằng candidate hình học/temporal kết hợp Qwen2-VL local.
- Quét dày 30 giây đầu/cuối, quét thưa toàn phim và phân bổ tối đa 80 cửa sổ đồng đều theo từng đoạn 5 phút để tránh thiên lệch timeline.
- Regression 16 giây bắt đúng logo N và `A NETFLIX SERIES` mà prompt không chứa tên Netflix, đồng thời loại hai cửa sổ cảnh phim bình thường.
- Full scan 62 phút xử lý 1.980 frame và 80 cửa sổ trong 99,297 giây; giữ logo intro, logo/nhãn trong cảnh cần review và logo credit cuối phim.
- Mọi kết quả vẫn cần xác nhận; các candidate đã nằm hoàn toàn trong vùng CUT có thể kế thừa quyết định cũ, candidate mới không được tự sửa.
- Tăng bộ kiểm tra tự động lên 48 bài.

## 0.3.0 - 2026-09-23

- Thêm lớp xác nhận violence thứ hai bằng Qwen2-VL-2B Apache-2.0, chạy local trên năm frame theo thứ tự thời gian và không tự chỉnh sửa video.
- Benchmark nhỏ 11 clip có nhãn rõ đạt recall 100%, specificity 85,71% và balanced accuracy 92,86%; giữ một false positive để người dùng quyết định.
- Quét video thực tế 62 phút: ViT tạo 167 interval, Qwen loại 166 và giữ 1 interval 52:27,875–52:35,875 để review trong 192,943 giây; VRAM đỉnh khoảng 4,62 GB.
- Tạo queue V2 gồm 12 mục thay cho 38 mục cũ: 4 text/logo có chữ, 7 adult, 1 violence và 0 gore; chưa tạo edit plan hay output.
- Sau review, bổ sung logo N thuần hình ảnh của Netflix tại 00:01,0–00:05,0; mở rộng thao tác cắt intro liên tục thành 00:00–00:10,5 và tạo lại queue/edit plan/preview V3.
- Xóa SmolVLM thử nghiệm không đạt cùng file tạm, thu hồi hơn 2,03 GB; giữ model, benchmark nén và báo cáo hợp lệ trên ổ E.
- Tăng bộ kiểm tra tự động lên 43 bài.

## 0.2.9 - 2026-09-23

- Thay model VideoMAE violence CC-BY-NC bằng ViT Apache-2.0 đã ghim revision/checksum và chạy hoàn toàn local.
- Thêm backend cửa sổ 16 frame, lấy trung bình 5 frame mạnh nhất để giảm nhiễu từ một frame đơn lẻ.
- Benchmark tích hợp 60 clip đạt recall 95%, specificity 72,5% và balanced accuracy 83,75%; riêng phim người thật đạt recall 90% và balanced accuracy 80%.
- Smoke scan clip 2,208 giây chạy trong 0,446 giây, dùng khoảng 478 MB VRAM và tạo đúng interval review; không tự edit.
- Loại Aleris vì balanced accuracy chỉ 65%; xóa Aleris, VideoMAE cũ và ONNX Runtime thử nghiệm sau khi giữ báo cáo.
- Ghi rõ quảng cáo/logo có chữ đã hoạt động trên video thực tế; logo thuần hình ảnh chưa hoàn tất.
- Tăng bộ kiểm tra tự động lên 40 bài.

## 0.2.8 - 2026-09-23

- Thêm policy `free-commercial-safe-local`: không API trả phí/theo lượt, không upload media ra AI ngoài, chỉ chạy model local có nguồn, revision và giấy phép được duyệt.
- Thêm lệnh `license-audit` và chặn model lạ/thiếu manifest trước mọi scan hoặc benchmark.
- Ghi metadata giấy phép, nguồn, chi phí và trạng thái dữ liệu huấn luyện vào toàn bộ manifest model hiện có.
- Khóa VideoMAE XD violence vì giấy phép CC-BY-NC-4.0; giữ file/báo cáo cũ cho audit nhưng cấm lượt production mới.
- Vô hiệu hóa script tải lại ảnh animation từ nguồn artwork không có quyền tái sử dụng rõ theo từng file.
- Thêm thông báo third-party và quy định xác nhận quyền với video/nhạc/phụ đề trước khi upload.
- Tăng bộ kiểm tra tự động lên 37 bài.

## 0.2.7 - 2026-09-23

- Thay quyết định OCR dựa trên danh sách từ khóa bằng ensemble local gồm nhận dạng chữ, tính liên tục của nội dung, vị trí, độ bền theo thời gian và classifier embedding đa ngôn ngữ.
- Thêm model `multilingual-MiniLMv2-L6-mnli-xnli` đã ghim revision/checksum, khoảng 450,2 MB trên ổ E; sau tải chạy offline và không gửi chữ/video ra ngoài.
- Thêm tập seed có phiên bản và đầu phân loại tuyến tính có thể học tiếp từ dữ liệu review; holdout khởi đầu nhỏ đạt 14/16, chỉ dùng làm kiểm tra kỹ thuật chứ chưa phải số đo production.
- Chỉ nối hai OCR track khi cả vị trí lẫn nội dung chữ liên tục, tránh ghép nhiều câu phụ đề khác nhau thành một banner kéo dài.
- Tách `advertisement`, `subtitle`, `credits` và `scene_text`; giữ các trường hợp chưa chắc chắn trong review và không tự blur/xóa.
- Chuyển quy tắc Netflix thành policy người dùng minh bạch trong `config/text_review_policy.json`; policy chỉ ép review, không tự tạo edit.
- Quét lại video thực tế: 1.241 frame trong 369,466 giây, tốc độ 10,074× thời gian thực, RAM đỉnh 1,28 GB. V7 giữ đủ Netflix/NGUONC và còn 28 track review trước khi gộp theo thời gian.
- Loại ký tự OCR rỗng/đơn lẻ, nhận biết credit roll cuối phim và chữ nhỏ trong cảnh để giảm false positive; thêm progress log và luôn giữ ảnh cho candidate ở các lượt quét mới.
- Tăng bộ kiểm tra tự động lên 34 bài.

## 0.2.6 - 2026-09-22

- Sửa đọc metadata video có tên tiếng Việt trên Windows bằng UTF-8.
- Ánh xạ báo cáo kỹ thuật `nsfw` thành nhóm `adult` để nút lọc 18+ trong giao diện hoạt động đúng.
- Bổ sung từ khóa quảng cáo Việt thường gặp và tên miền `.ai` cho OCR.
- Giữ lại dòng chữ dài ở mép trên từ ngưỡng tin cậy 0,10 trong khi phụ đề và chữ thông thường vẫn dùng ngưỡng 0,35.
- Xác nhận OCR V2 bắt đúng `NETFLIX SERIES` ở 00:06–00:10 và banner `NGUONC.COM` ở 03:33–04:02; thêm đệm thời gian cùng vùng che trước khi đưa vào review.
- Áp dụng quy tắc người dùng: mọi chữ/logo Netflix ở phần cuối cũng được coi là quảng cáo; thêm dòng bản quyền cuộn 59:29,5–59:45,5 và logo `NETFLIX | DUBBING` 59:50,5–hết video vào review.
- Quét video người thật đầu tiên dài 62 phút qua bốn nhánh adult, gore, violence và text; giữ bước xác nhận trước mọi chỉnh sửa.

## 0.2.5 - 2026-09-22

- Thêm bước phê duyệt preview riêng trước khi mở quyền dựng video hoàn chỉnh.
- Thêm bộ dựng cuối có CUT, BLUR, giữ âm thanh 5.1 và kiểm tra checksum nguồn trước/sau khi dựng.
- Đặt mục tiêu dung lượng 3,3 GB và giới hạn cứng 3,5 GB cho mọi bản xuất; dựng qua file tạm và chỉ công nhận khi vượt đủ kiểm tra.
- Xuất bản thử Sintel dài 886,064 giây, dung lượng 173.784.506 byte trong 53,364 giây; giải mã toàn bộ không lỗi và nguồn không đổi.
- Tăng bộ kiểm tra tự động lên 25 bài.

## 0.2.4 - 2026-09-22

- Thêm bulk KEEP theo bộ lọc có xác nhận và cấm bulk BLUR/CUT.
- Hiển thị dung lượng nguồn/report, ổ E còn trống, ước tính preview và mức tải máy ngay trong giao diện review.
- Thêm actor/transport cùng audit log giới hạn làm nền cho review từ xa.
- Chốt thiết kế Telegram long polling với allowlist và thumbnail opt-in; chưa truyền dữ liệu ra ngoài.
- Hoàn tất queue Sintel thử nghiệm với 37 KEEP, 1 CUT và 1 BLUR; tạo hai preview có audio trong khi giữ nguyên checksum nguồn và khóa full export.

## 0.2.3 - 2026-09-22

- Thêm review workflow hợp nhất report, ghi quyết định người dùng và khóa edit plan khi còn candidate chưa giải quyết.
- Thêm preview ngắn cho BLUR/CUT có âm thanh, xác minh checksum nguồn không đổi và giữ `final_export_allowed: false`.
- Tạo queue Sintel thực tế gồm 39 mục sau khi gộp candidate violence trùng giữa hai model.
- Sửa lệnh scan NSFW dùng đúng tập tham số và sửa ánh xạ video cho preview blur toàn khung.
- Thêm giao diện review tương tác chạy trên localhost, lưu quyết định ngay và hỗ trợ lọc candidate cùng hoàn tác lựa chọn.

## 0.2.2 - 2026-09-21

- Thay benchmark violence anime cũ bằng 126 mẫu direct-violence từ hai video, gồm 59 positive và 67 negative.
- Chọn ensemble VideoMAE temporal ngưỡng 0,61 với WD direct-action ngưỡng 0,20; đạt recall 86,44%, precision 80,95% và balanced accuracy 84,27%.
- Holdout Sintel đạt recall 85,71% và balanced accuracy 84,52%.
- Quét toàn bộ Sintel dài 14 phút 48 giây trong 71,745 giây bằng VideoMAE, bắt cả hai cụm giao chiến và gom còn 20 interval review.
- Thêm mức ưu tiên high/context, khoảng gom review 8 giây và routing model video đúng cho lệnh violence animation.
- Thêm công cụ tạo storyboard, benchmark Sintel/Conan và báo cáo ensemble tái lập được; không tạo full output.

## 0.2.0 - 2026-09-20

- Thêm pipeline OCR để phát hiện và theo dõi vùng chữ qua thời gian.
- Phân vùng phụ đề, trung tâm, cạnh và góc để ưu tiên review quảng cáo cố định.
- Thêm báo cáo JSON/HTML và preview có khung đánh dấu; chưa tự blur khi chưa được duyệt.
- Giới hạn số preview trong báo cáo và nén frame tạm trong RAM để tránh phình dữ liệu khi quét phim dài.
- Thêm EasyOCR và model Việt/Anh vào môi trường project trên ổ E.
- Xác nhận OCR phát hiện banner quảng cáo tiếng Việt trong video Conan và tạo preview blur 30 giây.
- Tối ưu blur bằng crop cục bộ kết hợp alpha feather; giữ nguyên màu và dọn các preview thử không đạt.
- Căn chính xác thời gian banner ở 00:03:06–00:03:31 để blur không xuất hiện trên các frame sạch.
- Thêm xác nhận endpoint bằng look-ahead 3 giây ở mật độ 0,5 giây/frame; tắt blur tại frame vắng banner đầu tiên sau khi xác nhận nội dung không quay lại.
- Thêm đệm 0,5 giây sau frame sạch đầu tiên để bù sai số timestamp/trình phát; xác minh V8 tại frame 0:28 và 0:29,5.
- Hoàn tất OCR toàn bộ video Conan 99 phút và loại các track chữ thuộc nội dung phim.
- Hoàn tất full-film NSFW scan; review 23 interval và xác định không có cảnh 18+ thật trong các candidate.
- Thêm scanner benchmark gore/violence với model local, top-candidate report và bảo vệ video nguồn.
- Loại hai baseline gore/violence vì cùng bỏ sót cảnh máu thật tại 01:01:39; xóa khoảng 388 MB trọng số thử nghiệm sau khi giữ báo cáo/checksum.
- Tạo `reports/conan-edit-plan.json` và hai clip review ngắn cho các cảnh nhạy cảm còn cần quyết định.
- Ghi nhận người dùng xác nhận cả hai cảnh nhạy cảm là false positive; giữ nguyên nội dung và xóa hai clip review.
- Hủy bước full export trong giai đoạn detection; chuyển gate sang benchmark có ground truth riêng cho 18+, gore và violence.
- Tách detection thành sáu nhánh theo nội dung và phong cách `live_action`/`animation`.
- Thêm policy bắt buộc người dùng xác nhận `KEEP`, `BLUR`, `CUT` hoặc `NEEDS_MORE_CONTEXT` trước mọi chỉnh sửa.
- Thêm schema CSV cho tập benchmark và quy định không full export trong giai đoạn thử model.

## 0.1.0 - 2026-09-20

- Khởi tạo cấu trúc Phase 1A.
- Thêm scanner NSFW baseline, báo cáo HTML/JSON và storage dry-run.
- Thêm chính sách giữ toàn bộ runtime, model và cache trên ổ E.
- Xác minh PyTorch CUDA trên RTX 2060 và smoke test scanner end-to-end.
- Ghi nhận FFmpeg 9.0.1 không tương thích NVENC API của driver 576.80; CPU x265 hoạt động.
- Thêm score distribution và top-20 candidate frames cho blind review video chưa có annotation.
# 0.2.1 - 2026-09-20

- Added a reproducible image benchmark command with per-style precision, recall, specificity, speed, RAM/VRAM and bounded error thumbnails.
- Added a 42-sample adult-content POC manifest with checksums and source/license metadata.
- Selected the 16.3 MB multi-class NSFW nano model as the experimental baseline at a 0.95 candidate threshold.
- Added a 3-of-5 temporal confirmation rule for animation and retained mandatory user confirmation before edits.
- Rejected the Falconsai baseline after it flagged all 22 hard negatives.
- Changed the current milestone to coverage-first: reach 80–85% for adult, blood/gore and violence before deeper optimization from real-video errors.
- Added a 60-sample blood/gore POC with 20 positives and 40 hard negatives split evenly across live action and animation.
- Selected the 45.3 MB NSFL classifier for live action (90% balanced accuracy) and the 378.7 MB anime tagger for animation (85% balanced accuracy).
- Added 23-tag probabilistic gore scoring plus 3-of-5 temporal confirmation for animation video scans.
- Verified the animation gore path on a 35-second clip: 70 frames in 4.532 seconds and zero confirmed false intervals.
- Added a 60-clip violence POC with 10 positives and 20 hard negatives per content style.
- Selected the 87.6 MB VideoMAE-small model for live action at 80% recall and 87.5% balanced accuracy.
- Reused the pinned anime tagger for animation violence at 80% recall and 87.5% balanced accuracy, with 3-of-5 temporal confirmation.
- Rejected and deleted the 345 MB surveillance-oriented VideoMAE after retaining its report and checksums.
- Deleted the 372 MB temporary Tears of Steel source and review contact sheets after producing the bounded 30.3 MB clip benchmark.
- Completed 2 fps field scans for adult, blood/gore and violence across the full 99-minute Conan video without exporting or editing the source.
- Measured field review load at 7.85, 41.64 and 74.83 intervals per hour respectively; identified fire dominance in violence and excessive low-score gore candidates.
- Recorded the next optimization pass: one shared anime tagger inference, danger/violence separation, two-tier gore filtering and one retained thumbnail per interval.
- Added a combined anime safety scan that produces gore and direct-violence reports from one WD tagger pass.
- Separated fire/explosion danger telemetry from direct violence triggers.
- Added two-tier gore filtering with stricter 4-of-5 temporal confirmation for weak contextual hits.
- Reduced Conan field review load to 19.92 gore and 11.47 violence intervals per hour while retaining the strongest injury candidate.
- Compacted reports to one strongest thumbnail per interval plus bounded top candidates, reducing the two anime reports from about 13.17 MB to under 1 MB.
- Marked the legacy anime violence POC as label-mismatched because its positives are injury/fire rather than confirmed direct violence.
- Added 13 verified adult-anime field false positives to a checksum-backed regression manifest.
- Replaced the anime adult nano path with explicit WD tags in the shared inference pass.
- Added adult label co-occurrence gating; the 43-sample anime regression reached 100% recall and 98.48% balanced accuracy.
- Added high/context priority tiers for direct anime violence; the provisional high-priority precision audit reached 87.5%.
- Completed V4 full-field shared inference in 754.749 seconds with adult, gore and violence reports from one model pass.
