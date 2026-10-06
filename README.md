# BiliFlow

Phiên Codex mới nên bắt đầu từ [`AGENTS.md`](AGENTS.md) và [`docs/SESSION_HANDOFF.md`](docs/SESSION_HANDOFF.md). Lịch sử kỹ thuật đầy đủ nằm tại [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md).

BiliFlow là hệ thống local-first hỗ trợ quét, review, xử lý và sau này upload video lên BiliBili TV. Hệ thống hiện có scanner NSFW, OCR tiếng Việt, benchmark classifier nội dung và edit plan có bước review trước khi export.

## Nguyên tắc an toàn

- Không sửa hoặc xóa video nguồn. Chỉ có hai nhóm ngoại lệ, đều do bạn bấm và xác nhận trong Control Center (các thao tác xóa làm được trên PC và, từ 2026-10-06, qua chế độ điện thoại; “Lưu trữ” / “Khôi phục bản xuất” chỉ làm trên PC): “Xóa video gốc” xóa vĩnh viễn (không qua Thùng rác) video gốc đã xuất (đúng lần duyệt mới nhất) hoặc đã bỏ qua, còn “Xóa video” xóa khỏi BiliFlow video đã hủy (cùng video gốc nếu còn) hoặc video đã mất video gốc; “Lưu trữ” / “Khôi phục bản xuất” chuyển video gốc vào kho `archive\` (đổi tên cùng ổ, kiểm SHA-256) và trả về `input\`. Hai nút xóa không bao giờ xóa file `.mp4` trong `output\` và không xóa được video thuộc bộ nhãn vàng.
- Không tự upload hoặc publish.
- Model quét media chạy local. AI Supervisor mặc định chỉ nhận JSON/log; Visual AI Audit chỉ gửi thumbnail sau khi người dùng xác nhận riêng cho từng video, tối đa 36 ảnh và không gửi video hoặc audio nguồn.
- Không dùng API trả phí/theo lượt; model phải vượt kiểm tra giấy phép trước khi chạy.
- Kết quả AI chỉ hỗ trợ review, không chứng minh video an toàn.
- Runtime, model, cache và file tạm nằm trong thư mục project trên ổ E.

## Trạng thái hiện tại

### Control Center theo phiên

Ở phần bắt đầu/chạy lại từng video, mục **OCR** cho chọn **Chuẩn** (mặc định) hoặc **Tăng tốc (thử nghiệm)**. Tùy chọn chỉ áp dụng nhận chữ trong nhóm Quảng cáo/logo, không đổi mật độ quét hay ngưỡng phát hiện. Lựa chọn được lưu riêng theo video; bạn có thể chọn lại Chuẩn cho lượt sau. Mức tăng tốc toàn bộ luồng còn phụ thuộc video và các bước khác.

Double-click [`Start-BiliFlow.cmd`](Start-BiliFlow.cmd) để mở dashboard tập trung tại `127.0.0.1:8765`. Dashboard tự nhập sáu nguồn và lịch sử review hiện có, theo dõi video mới sau khi file ổn định 60 giây, cho xếp nhiều video, chọn profile `careful`/`fast`, dừng sau stage, dừng ngay, retry, review và chọn riêng cho từng video: giới hạn mặc định 3,5 GB, giới hạn tùy chỉnh hoặc không giới hạn dung lượng.

Đóng tab trình duyệt không dừng backend. Dùng nút **Tắt** trên dashboard hoặc [`Stop-BiliFlow.cmd`](Stop-BiliFlow.cmd) để tắt watcher, scheduler, worker và cổng web. BiliFlow không cài service và không tự chạy cùng Windows.

SQLite WAL ở `state/control-center.sqlite3` giữ trạng thái job/stage/revision/artifact/event. Khi máy hoặc ứng dụng dừng bất ngờ, stage đang chạy trở về `INTERRUPTED_RECOVERABLE`; source và stage đã hoàn tất được giữ nguyên. Một GPU worker xử lý tuần tự để phù hợp RTX 2060 6 GB, còn nhiều video vẫn có thể nằm trong queue và review web hoạt động đồng thời.

Tab **Hoàn tất** có **Xóa video gốc** (trước đây là “Dọn video gốc”) cho từng video hoặc nhiều video đã chọn (tối đa 50 video mỗi lần). Chỉ video đã xuất đúng lần duyệt mới nhất (manifest khớp, bản xuất làm đúng theo các quyết định duyệt hiện tại) hoặc đã “Bỏ qua (không xuất)” mới được đề nghị. Hộp thoại liệt kê từng video gốc, dung lượng, bản xuất, dung lượng báo cáo và log, cùng tổng dung lượng được giải phóng; nút xác nhận chỉ bật khi bạn đánh dấu “Tôi hiểu”. Sau khi kiểm SHA-256, video gốc bị **xóa vĩnh viễn** (không qua Thùng rác), cùng file `…-reviewed.mp4.manifest.json` của bản xuất và dữ liệu của video trong BiliFlow (dòng trong cơ sở dữ liệu, thư mục báo cáo của video, log). File `.mp4` đã xuất trong `output\`, bộ nhớ logo/studio và cache quét được giữ. Video biến mất khỏi danh sách và không duyệt hay xuất lại được nữa; chép lại video gốc vào `input` sẽ tạo một video mới. Khi đó, nếu xuất lại đúng các quyết định cũ, BiliFlow không ghi đè file `.mp4` cũ còn trong `output` (báo file đã tồn tại): hãy xóa hoặc đổi tên file đó trước. Video thuộc bộ nhãn vàng (`annotations\golden`) không xóa được và nút bị khóa kèm lý do.

Video **đã hủy** và video **không còn video gốc** (không tính video đang lưu trữ) có nút **Xóa video**: video bị xóa khỏi BiliFlow cùng báo cáo, log và lịch sử của nó; video đã hủy còn video gốc trong `input` thì video gốc bị xóa vĩnh viễn sau khi kiểm SHA-256. Khi có video mất gốc, đầu danh sách hiện “Có N video không còn video gốc · **Dọn video mất gốc**” để xóa tất cả trong một hộp thoại (tối đa 50 video mỗi lần). Không thao tác nào đụng tới `output\`. Video cũ đã “Dọn video gốc” vào Thùng rác mà file vẫn còn trong Thùng rác thì chưa xóa được: hãy khôi phục nó về `input` hoặc xóa nó khỏi Thùng rác trước. “Xóa video gốc”, “Xóa video” và “Dọn video mất gốc” làm được cả trên điện thoại khi bật chế độ điện thoại (từ 2026-10-06; cùng hộp xác nhận và ô “Tôi hiểu”); lưu trữ, khôi phục và kiểm tra lại Thùng rác chỉ làm trên PC. Trang Control Center mở từ trước bản này phải tải lại mới xóa được (máy chủ từ chối lệnh xóa không kèm xác nhận “Tôi hiểu”). BiliFlow không giữ nhật ký xóa.

Tab **Hoàn tất** cũng có **Lưu trữ** (từng video hoặc “Lưu trữ đã chọn”): video gốc được đổi tên vào `archive\sources\<job>\` trên cùng ổ (không nén, không giảm chất lượng), kiểm SHA-256, kèm `archive-manifest.json` ghi quyết định duyệt, edit plan và manifest bản xuất; bản xuất (MP4 và manifest) của video đã xuất được chuyển vào Thùng rác Windows. Video gốc + quyết định chỉ chiếm khoảng 57% dung lượng bản xuất. **Khôi phục bản xuất** đưa video gốc về `input\` (kiểm SHA-256), video về “Đang chờ duyệt” để bạn sửa quyết định nếu cần rồi xuất lại. Lưu trữ và Xóa video gốc loại trừ nhau; đường dẫn dài hơn 259 ký tự bị từ chối. Nút **Kiểm tra lại Thùng rác** chỉ đọc Thùng rác và ghi thêm một bản ghi kiểm tra khi Windows ghi bản ghi `$I` chậm hơn lần xác minh đầu.

Nút **Hủy** hỏi xác nhận trước (khác “Dừng” chỉ tạm dừng và “Bỏ qua (không xuất)” dành cho video đã duyệt xong) và không hủy hai lần. Video đã hủy nằm trong nhóm thu gọn **Đã hủy (N)** cuối tab “Đang chờ xử lý”; **Ẩn khỏi danh sách** chỉ đặt cờ ẩn (không xóa gì), mục **Đã ẩn (N)** có **Hiện lại**; muốn xóa hẳn video đã hủy thì dùng **Xóa video** (ở trên). Đổi tab tự cuộn tới video đầu tiên của tab.

**Logo nền tảng** (iQIYI, Youku, Tencent Video/WeTV, Mango TV, Sohu, PPTV; Bilibili được loại trừ): khi OCR đọc được tên nền tảng (kể cả các biến thể OCR như “iOlYI”) hoặc khung hình khớp một logo nền tảng đã nhớ, danh sách duyệt có thẻ chính “Logo nền tảng …” đề xuất **Làm mờ** vùng logo, thời gian bám theo điểm cắt cảnh. 6 giây cuối mỗi video luôn có thẻ “Kiểm tra đoạn kết”, giống thẻ 5 giây đầu. Trên trang duyệt, “Đây là logo nền tảng — làm mờ & nhớ” ghi nhớ logo cho các tập sau (ngược với “Đây là logo hãng phim — giữ & nhớ”). Trang **Bộ nhớ logo** (link trên đầu dashboard) cho xem ảnh từng logo đã nhớ, đổi loại hoặc xóa; mọi thay đổi đều sao lưu bộ nhớ trước vào `state\backups`, ảnh của bản ghi bị xóa được dời vào đó chứ không xóa.

AI Supervisor là kiểm tra tư vấn theo yêu cầu. Dashboard có khu vực kết nối, nút mở luồng `codex login`, kiểm tra trạng thái và cấu hình model. Cấu hình portable nằm tại `config/ai_supervisor.json`; mặc định dùng `gpt-5.6-luna` + reasoning `medium`, chỉ cho chọn model thuộc dòng GPT-5.6 và Low/Medium/High. GPT-6, API key, XHigh/Max/Ultra và fast service tier đều bị chặn để tránh dùng nhầm mức tiêu hao cao hoặc phát sinh phí API ngoài gói ChatGPT. JSON audit không gửi media. Visual AI Audit phải được xác nhận cho từng job, chỉ nhận thumbnail giới hạn trong reports, không được tự duyệt KEEP/BLUR/CUT hay khởi động render. Nếu Codex không sẵn sàng, pipeline local và review vẫn hoạt động.

- Python 3.11.16, PyTorch 2.14.0+cu126 và FFmpeg 9.0.1 nằm trên ổ E.
- CUDA đã nhận RTX 2060 6 GB.
- Model NSFW nano 16,3 MB và EasyOCR đã tải theo revision/checksum cố định.
- OCR, NSFW, gore và violence đã benchmark trên phim Conan; violence anime V5 còn được kiểm tra trên video độc lập Sintel.
- Violence anime V5 đạt recall 86,44%, precision 80,95% và balanced accuracy 84,27% trên 126 mẫu từ hai video.
- Hai baseline gore/violence bị loại vì bỏ sót cảnh máu thật trong anime; trọng số thử nghiệm đã được xóa sau khi giữ báo cáo.
- Detection không tự sửa video. Sau khi giải quyết hết hàng duyệt, nút `Hoàn tất duyệt và xuất video` khóa quyết định, tạo edit plan và chạy bản xuất đã kiểm tra ngay trong giao diện.
- Chín bộ model local hiện đạt profile miễn phí/commercial-safe. Model violence VideoMAE XD cũ đã được thay và xóa khỏi ổ E do giấy phép CC-BY-NC-4.0.
- Nhánh violence phim người thật dùng hai tầng: ViT Apache-2.0 quét nhanh để tìm ứng viên, rồi Qwen2-VL-2B Apache-2.0 xem năm frame liên tiếp để loại cảnh nói chuyện/chuyển động bị nhầm. Benchmark xác nhận nhỏ gồm 11 clip đạt recall 100%, specificity 85,71% và balanced accuracy 92,86%; số liệu này chỉ là kiểm tra kỹ thuật ban đầu.
- Nhánh logo/brand dùng quét hai tầng, crop vùng nhỏ, OCR và Qwen2-VL. Sau Florence-2, GroundingDINO Tiny có thể thêm tối đa một vùng logo bổ sung trên cảnh đã được xác nhận; nó không được dùng để tự phân loại toàn cảnh hoặc tự sửa video. Cache routing nén theo SHA-256 video và cấu hình giúp stage chạy lại không giải mã lại phần tìm ứng viên. Các quyết định logo đã duyệt tạo chữ ký perceptual nhỏ trong `state/brand-memory.json`; chữ ký chỉ tăng khả năng đưa ứng viên ra review, không mang quyết định CUT/BLUR sang video khác. Các phát hiện lặp lại cùng vị trí được gom thành từng logo xuyên phim; nhiều logo ở các vùng khác nhau vẫn là các track riêng. Florence-2 khoanh vùng pixel và ưu tiên visual grounding trước tiêu đề OCR khi chưa biết tên thương hiệu. Mọi kết quả vẫn chờ người dùng xác nhận.
- Nhiều session có thể chuẩn bị video cùng lúc. Lượt dùng GPU xếp hàng qua một mutex chung để không tràn RTX 2060 6 GB; render cuối cũng có khóa riêng. Review web và các công việc CPU vẫn hoạt động đồng thời.

### Dashboard V2 (nhánh `feat/dashboard-v2`, chưa merge)

Control Center chạy nhánh này có thêm Dashboard V2 ở `127.0.0.1:8765/dashboard-v2/`; dashboard cũ ở `/` giữ nguyên. Nút **Duyệt cảnh** trong bảng chi tiết của video mở hộp duyệt ngay trên màn đang xem (địa chỉ `#review/<id>/<màn>`). Đóng hộp bằng ×, "Đóng", Esc hoặc nút Back thì về đúng màn đó.

- Trong hộp: ảnh và video của từng cảnh, dải khung, timeline, phóng to thẻ, các nút Giữ / Làm mờ / Cắt / Cần xem thêm / Xóa quyết định, nút vùng logo, nhớ logo, phím 1–4, hoàn tác, "Giữ tất cả", "Dùng đề xuất" và "Xuất video". Hộp chỉ gửi quyết định khi bạn bấm, không bao giờ tự xuất.
- Trang duyệt cũ `/review/<id>` không đổi, mở bằng link "Mở trang duyệt cũ" trong hộp.
- PC: thẻ 2 cột. Laptop hẹp: 1 cột. Điện thoại: hộp toàn màn hình, nút cao ít nhất 44 px, chữ ít nhất 12 px, hàng bộ lọc cuộn ngang và mờ dần ở mép còn bộ lọc.
- Mở trên điện thoại hoặc laptop trong Wi-Fi nhà: [`docs/DASHBOARD_V2_PHONE.md`](docs/DASHBOARD_V2_PHONE.md). Kế hoạch và kết quả kiểm từng đợt: [`docs/DASHBOARD_V2_REVIEW_PLAN.md`](docs/DASHBOARD_V2_REVIEW_PLAN.md).

## Kiểm tra chi phí và giấy phép

```powershell
.\scripts\run.ps1 license-audit
.\scripts\run.ps1 rebuild-brand-memory
```

Mỗi lệnh scan/benchmark kiểm tra cùng policy trước khi nạp model. Chi tiết và giới hạn quyền đối với video nguồn nằm tại [`docs/LICENSE_POLICY.md`](docs/LICENSE_POLICY.md); nguồn và giấy phép model nằm tại [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

## Chạy scanner

```powershell
.\scripts\run.ps1 storage
.\scripts\run.ps1 cleanup-preview
.\scripts\run.ps1 scan --input .\input\sample.mp4 --report-dir .\reports\sample
.\scripts\run.ps1 scan-content --kind gore --input .\input\sample.mp4 --report-dir .\reports\sample-gore
.\scripts\run.ps1 scan-content --kind violence --content-style animation --input .\input\sample.mp4 --report-dir .\reports\sample-violence
.\scripts\run.ps1 confirm-violence --report .\reports\sample-violence\scan.json --output .\reports\sample-violence\scan-confirmed.json --device cuda
.\scripts\run.ps1 scan-visual-logo --input .\input\sample.mp4 --report-dir .\reports\sample-visual-logo --device cuda
.\scripts\run.ps1 localize-visual-logo --report .\reports\sample-visual-logo\scan.json --output .\reports\sample-visual-logo\scan-florence.json --device cuda
.\scripts\run.ps1 augment-grounding-regions --report .\reports\sample-visual-logo\scan-florence.json --output .\reports\sample-visual-logo\scan-localized.json --device cuda
.\scripts\run.ps1 scan-visual-logo --input .\input\sample.mp4 --report-dir .\reports\sample-visual-logo-exhaustive --device cuda --exhaustive
.\scripts\run.ps1 benchmark-images --manifest .\annotations\adult_poc_benchmark.csv --report-dir .\reports\adult-poc --model .\models\nsfw_detection_2_nano --positive-label hentai --positive-label porn --positive-label sexy --threshold 0.95
```

Kết quả scan gồm `scan.json`, `review.html` và thumbnail của các frame vượt ngưỡng.
Scanner cũng lưu 20 frame có điểm cao nhất để hỗ trợ blind review khi chưa có timestamp hoặc ground truth.

## Rà soát chữ và quảng cáo

```powershell
.\scripts\run.ps1 scan-text --input ".\input\video.mp4" --report-dir .\reports\text-review
```

Lệnh tạo `text-scan.json`, `text-review.html` và ảnh đánh dấu từng vùng nghi vấn. Sau OCR, model semantic local phân loại `advertisement`, `subtitle`, `credits` và `scene_text`, rồi kết hợp vị trí, độ bền và tính liên tục của chữ. Hai câu ở cùng vị trí chỉ được nối khi nội dung cũng liên tục, tránh biến nhiều phụ đề khác nhau thành một banner dài. Trường hợp chưa chắc chắn vẫn được đưa ra review; model không tự sửa video nguồn.

Quảng cáo có chữ vẫn đi qua OCR/semantic. Logo không có chữ đi qua scanner hình ảnh riêng và Qwen2-VL xác nhận; crop chồng lấn giúp logo nhỏ như NewGates Anime chiếm đủ diện tích để model nhìn thấy. Hệ thống theo dõi vị trí qua thời gian, rồi Florence-2 chỉ khoanh đúng logo thay vì cả tiêu đề bên cạnh. Kết quả tạo candidate và đề xuất thao tác, không tự blur/cắt. Regression hiện tại bắt logo N của Netflix mà prompt không chứa tên Netflix.

Scanner luôn giữ hai fallback mạnh nhất trong mỗi đoạn 5 phút và thêm ứng viên ở chuyển cảnh đáng chú ý, nên logo không vượt heuristic ban đầu vẫn có cơ hội được Qwen kiểm tra. Cache chỉ dùng lại khi checksum video, thuật toán, mật độ lấy mẫu và revision bộ nhớ logo trùng hoàn toàn. Cache được nén gzip và trở thành ứng viên dọn dry-run sau 30 ngày.

Khi ưu tiên không bỏ sót, dùng `--exhaustive` để lấy mẫu toàn timeline tối thiểu 2 frame/giây và đưa mọi cửa sổ 5 giây qua tầng visual local. Mỗi cửa sổ, kể cả cửa sổ Qwen loại, có một ảnh trong `audit-thumbnails` và được liệt kê ở `audit.html` để kiểm tra false negative. Chế độ này có thể giữ dư tiêu đề phim và bảng hiệu nằm trong bối cảnh; các mục đó phải chọn `Giữ nguyên`. Florence-2 chạy sau đó để gợi ý vùng pixel cho logo/brand đã giữ lại. Qwen và Florence không được tự cho phép chỉnh sửa.

Với banner lớn, vùng blur được tính từ hợp bounding box OCR theo thời gian của chính video. Vị trí và chiều cao không dùng một hằng số chung; kết quả được khóa vào edit plan để render lặp lại ổn định. Mép dưới được thu theo tỷ lệ đệm OCR, còn chiều ngang bám theo đường di chuyển thực tế của chữ. Banner chạy hết khung dùng blur kín hai đầu trái/phải và chỉ feather trên/dưới, tránh nhấp nháy khi chữ đi vào hoặc ra. Video nguồn luôn được giữ nguyên.

EasyOCR được lưu trong `models\easyocr`; model semantic khoảng 450 MB nằm trong `models\multilingual_minilm_text_semantics`, đều trên ổ E. Bộ phân loại semantic học từ tập ví dụ có phiên bản ở `annotations\text_semantics_seed_v1.json`, nên có thể cải thiện bằng quyết định review thực tế thay vì thêm chuỗi từ khóa vào mã. Quy tắc riêng như “mọi chữ Netflix đều cần xem” nằm trong `config\text_review_policy.json` và chỉ ép đưa ra xác nhận. Scanner tắt tải model tự động; nếu model chưa có, lệnh sẽ dừng.

FFmpeg 9.0.1 hiện quét video và encode CPU được. HEVC/NVENC của build này yêu cầu driver mới hơn driver 576.80 đang có, vì vậy chưa dùng NVENC cho archive.

## Detection và xác nhận

Detection được tách thành sáu nhánh: 18+, blood/gore và violence cho phim thực tế và phim hoạt hình/anime. Cấu hình bắt buộc xác nhận trước khi sửa nằm tại `config/detection_policy.yaml`; kế hoạch benchmark nằm tại `docs/DETECTION_PLAN.md`.

Model chỉ tạo candidate. Người dùng phải chọn `KEEP`, `BLUR`, `CUT` hoặc `NEEDS_MORE_CONTEXT`; không có blur/cut/export tự động trong giai đoạn benchmark.

Với phim người thật, dùng `scan-content` trước rồi chạy `confirm-violence` trên báo cáo vừa tạo. Lớp xác nhận chạy hoàn toàn local, chỉ loại ứng viên trả lời rõ `no`; câu trả lời không chắc chắn vẫn được giữ để người dùng xem.

## Xác nhận và preview

```powershell
.\scripts\run.ps1 build-review --report .\reports\sample-gore\scan.json --report .\reports\sample-violence\scan.json --queue .\reports\sample-review\review-queue.json
.\scripts\run.ps1 review-ui --queue .\reports\sample-review\review-queue.json
.\scripts\run.ps1 review-decide --queue .\reports\sample-review\review-queue.json --id review-xxxxxxxxxxxx --decision KEEP
.\scripts\run.ps1 build-edit-plan --queue .\reports\sample-review\review-queue.json --output .\work\sample-edit-plan.json
.\scripts\run.ps1 render-previews --plan .\work\sample-edit-plan.json --output-dir .\previews\sample-edit-review
.\scripts\run.ps1 approve-previews --plan .\work\sample-edit-plan.json --manifest .\previews\sample-edit-review\preview-manifest.json --actor user
.\scripts\run.ps1 render-final --plan .\work\sample-edit-plan.json --output .\output\sample-reviewed.mp4
```

Trong giao diện mới, các mục tin cậy có nhãn `Đề xuất: BLUR/CUT` và có thể được nhận cùng lúc bằng `Duyệt tất cả đề xuất đang lọc`. Bạn xử lý các mục còn lại rồi nhấn `Hoàn tất duyệt và xuất video`; không cần quay lại chat để duyệt thêm một lần. Trạng thái render được cập nhật ngay trên trang.

Trang `review-ui` chạy riêng không xuất video nào đã có job trong Control Center, và cũng không xuất khi không đọc được `state/control-center.sqlite3`. Với video thuộc Control Center, trang này cũng không cho đổi quyết định khi video đang chờ xuất, đang xuất hoặc còn lệnh xuất tạm dừng/lỗi chưa hủy, đã dọn video gốc, hoặc đã được đánh dấu bỏ qua. Hãy xuất và duyệt các video đó trên Dashboard để giữ đúng hàng đợi, trạng thái bỏ qua và việc dọn video gốc.

Vùng logo được duyệt **Làm mờ** trong bản xuất mới được xóa bằng FFmpeg `delogo` rồi làm mờ (độ mờ tăng theo cỡ vùng), nên logo sáng trên nền tối không còn đọc được. Bản xuất làm trước đó vẫn hợp lệ. Muốn video đã xuất có cách che mới, bạn tự chuyển file xuất cũ (`.mp4` và `.manifest.json` trong `output\`) vào Thùng rác rồi xuất lại.

Edit plan chỉ được tạo khi toàn bộ candidate đã được giải quyết. Mặc định bản xuất đặt mục tiêu khoảng 3,3 GB và bị từ chối nếu vượt 3,5 GB. Mỗi video có thể chọn trần GB khác hoặc chế độ không giới hạn; mọi chế độ đều phải giải mã toàn bộ thành công trước khi đổi tên từ file tạm thành output chính thức. Video nguồn không bị sửa. Các lệnh preview thủ công bên trên vẫn được giữ cho trường hợp cần kiểm tra kỹ một operation. Chi tiết nằm tại [`docs/REVIEW_WORKFLOW.md`](docs/REVIEW_WORKFLOW.md).

Các nguồn đã quét được nhận diện bằng SHA-256. Di chuyển file thành phẩm ra khỏi `output` không làm mất queue, report hoặc dữ liệu regression; chỉ cần gửi lại đường dẫn khi chính video nguồn trong `input` đã bị chuyển đi.

Mặc định phải render và duyệt đủ mọi operation. Khi người dùng chủ động yêu cầu kiểm tra một mẫu đại diện, có thể thêm `--operation-id op-...` vào `render-previews`; bước duyệt phải thêm `--allow-sampled` để ghi rõ phạm vi mẫu trong manifest. Không có cờ này thì preview thiếu operation sẽ bị từ chối.
