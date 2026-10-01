# BiliFlow — Bàn giao tối ưu thời gian quét cho Claude

Ngày lập: **28/09/2026**, múi giờ Asia/Bangkok.

**Đây là kế hoạch triển khai và tiêu chí nghiệm thu, chưa phải tính năng đã hoàn thành.** Tài liệu được tạo theo yêu cầu của người dùng để Claude có thể tiếp tục công việc. Lượt lập tài liệu này không sửa runtime, không chạy model/video, không thay hàng duyệt, không merge/push.

## 1. Mục tiêu và phạm vi

Giảm thời gian quét video mới, giữ nguyên mật độ lấy mẫu, model, preprocessing, ngưỡng nhận diện, phạm vi quảng cáo/logo, 18+, máu me và bạo lực theo các nhóm người dùng chọn. Tối ưu phải được đo và so sánh đầu ra; không đánh đổi độ bao phủ để có con số thời gian đẹp hơn.

Ưu tiên tiếp theo là **thử ghép các crop OCR có cùng chiều rộng xử lý từ một nhóm nhỏ frame liên tiếp**, thay vì chỉ ghép crop trong từng frame. Đây mới là giả thuyết có thể tăng hiệu suất GPU; chưa có bằng chứng nó sẽ nhanh hơn trên cả phim.

Trong đợt đầu:

- Chỉ tạo thử nghiệm có giới hạn, đo và kiểm tra tương đương trước khi tích hợp.
- Giữ đường chuẩn OCR batch 1 và cách chạy batch 8 hiện có.
- Không sửa detector 18+, máu me, bạo lực hoặc renderer cùng đợt này.
- Không thêm yêu cầu duyệt, bỏ Audit AI, thay quy trình review hay tự xuất video.
- Không thay sampling, độ phân giải, precision, model hoặc threshold để tăng tốc.
- Không hứa quét đủ mọi nhóm trong 5–10 phút. Số liệu hiện tại chưa chứng minh khả năng đó.

Tương đương baseline là điều kiện bảo toàn hành vi, **không phải chứng minh baseline bắt đúng tất cả nội dung thực tế**. Chất lượng nhận diện tuyệt đối cần bộ nhãn chuẩn riêng.

## 2. Việc phải làm trước khi sửa code

Project: `E:\DungChung\BiliFlow`.

Đọc lần lượt:

1. `AGENTS.md`.
2. `docs/SESSION_HANDOFF.md`.
3. Phần mới nhất của `docs/PROJECT_STATUS.md`.
4. `README.md`.
5. Phần mới nhất của `CHANGELOG.md`.
6. `docs/SCAN_PERFORMANCE.md` và tài liệu này.

Sau đó chạy:

```powershell
Set-Location -LiteralPath 'E:\DungChung\BiliFlow'
git status --short --branch
git log -10 --oneline --decorate
```

Tại thời điểm lập tài liệu:

| Mục | Giá trị đã xác nhận |
| --- | --- |
| Nhánh | `improve/scan-performance-metrics` |
| HEAD trước khi thêm tài liệu | `ac3646b` |
| Runtime optimization mới nhất | `2c72280` |
| Base local main | `7f5a9fb` theo SESSION_HANDOFF; kiểm tra lại nếu cần |
| Working tree trước tài liệu | Sạch |
| Test code gần nhất | 278/278 unittest đạt |
| OCR mặc định | Batch 1 |
| Batch 8 | Thử nghiệm, opt-in theo video |
| Frame prefetch | Mặc định 0, chưa bật |

Ở nguyên nhánh hiện tại. Không reset các sửa đổi của người khác, không merge/main/push khi chưa được yêu cầu. Nếu HEAD đã thay đổi, đọc diff và cập nhật baseline trước khi đo.

### Những dữ liệu phải bảo toàn

- Video nguồn: không sửa/xóa/chuyển.
- `reports`, `state`, queue/revision, quyết định review, brand-memory và bằng chứng regression.
- Model/runtime/cache/temp/báo cáo mới đều ở trong project trên ổ E.
- Chỉ model local miễn phí với giấy phép thương mại phù hợp. Công việc OCR này không cần model mới hoặc API bên ngoài.
- Detector chỉ tạo ứng viên; việc KEEP/BLUR/CUT/export vẫn do người dùng duyệt theo flow hiện tại.
- Không dùng kết quả chỉ quét quảng cáo để nói đã kiểm tra các nhóm 18+/máu me/bạo lực.

## 3. Bằng chứng hiện tại: đang chậm ở đâu?

Lượt đo đầy đủ gần nhất: `troy-rgb-cold-full-20260928-194937`.

Nguồn Troy dài **196 phút 02,72 giây**, 1920×1080, 7.609.490.930 byte; profile `careful`, loại `live_action`, scope **advertising only**, OCR batch 1. Không chạy Visual AI, các nhóm safety hoặc export.

| Bước | Thời gian (giây) |
| --- | ---: |
| Preflight nguồn | 20,709 |
| OCR/text semantics | 821,828 |
| Visual logo routing + confirmation | 686,890 |
| Florence + GroundingDINO localization | 248,265 |
| Tạo review + audit cấu trúc cục bộ | 8,710 |
| Tổng wall time bộ điều phối | **1.786,436 — 29 phút 46 giây** |

Chi tiết đáng chú ý:

| Thành phần | Giây | Ý nghĩa |
| --- | ---: | --- |
| OCR `model_step` | 732,225 | Ưu tiên phân tích detect/recognize và thử batching |
| OCR chờ pipe frame | 14,609 | Quá nhỏ để kỳ vọng read-ahead giảm nhiều phút |
| Logo feature extraction | 493,007 | Còn dư địa tính toán CPU |
| Logo brand-memory matching | 24,631 | Nhỏ hơn nhiều so với extraction |
| Logo chờ pipe frame | 2,246 | Không phải bottleneck chính trong lượt này |

Không cộng các mục thành phần lần nữa vào tổng stage. Chúng nằm bên trong stage tương ứng.

Đo 40 frame thuộc bốn đoạn ngắn với OCR serial: detect 4,577 giây, recognize 5,726 giây, tổng `readtext` 10,472 giây. Instrumentation giữ nguyên predictions. **Không suy ra tỷ lệ detect/recognize cho toàn phim từ 40 frame này.**

### Kết quả tương đương đã kiểm chứng

- OCR: 3.921 frame; các track, text, vùng và thời gian được giữ như baseline.
- Logo: 6.121 frame, 199/199 regional leads, 80/80 đại diện toàn khung bắt buộc, 39/39 nhóm geometry/time, 116 intervals.
- 858/858 JPEG khớp byte.
- Review: 6 mục chính + 294 advisory; 123 ứng viên nguồn được ánh xạ đầy đủ; Structure Audit PASS.
- Raw logo khớp; localized report chỉ khác năm trường telemetry được ghi rõ trong comparison.
- Job gốc #39/revision 3, queue hash, source stat và brand-memory hash không đổi.

### Cách hiểu đúng hiệu năng

CPU logo giảm quan sát được từ 612,124 xuống 517,638 giây, khoảng 15,44%, so với một lượt cold trước đó. Thử ngắn đảo thứ tự baseline/optimized/optimized/baseline cho giảm 20,50% CPU routing và đầu ra chính xác.

Tuy nhiên tổng gần nhất 29m46 **chưa nhanh hơn tổng lịch sử khoảng 27m29**. Lượt cũ DINO tái dùng 87 kết quả, tính mới 25; lượt gần nhất tính mới 112. OCR cũng chậm hơn. Điều kiện cache/load khác nhau nên không thể kết luận tốc độ toàn luồng đã tăng hay đổ mọi dao động cho một nguyên nhân chưa đo.

## 4. Những gì đã thử: không làm lại thiếu mục đích

| Thử nghiệm | Kết luận |
| --- | --- |
| Ordinary EasyOCR batch >1 | Đã làm thay đổi padding/text; không dùng trực tiếp |
| Same-width batch trong một frame | Một số đoạn ngắn nhanh hơn và giữ đầu ra; full-film chưa có lợi ích đáng kể |
| Full Troy OCR batch 8 | 765,102 giây so với serial lịch sử 770,734; chưa đủ chứng minh cải thiện thực tế |
| Prefetch frame | Dưới 1%, không nhất quán; giữ OFF |
| Cache cạnh của frame trước | Chỉ 1,88% CPU; đã loại khỏi runtime |
| Đổi số thread OpenCV | Có lợi trên mẫu nhỏ nhưng chưa áp dụng toàn cục |
| RGB distance CPU tương đương bit | Đã áp dụng và kiểm chứng; không hoàn tác vô tình |
| Logo routing cache v2 | Đã sửa mất frame trung gian; phải giữ nguyên tính đầy đủ |

Lưu ý lỗi cache cũ: v1 giữ ít frame trước bước routing, làm mất regional evidence dù Structure Audit vẫn PASS. Do đó **PASS audit cấu trúc không thay thế phép đối chiếu detector A/B**. Không tái áp dụng kiểu giữ chỉ frame mạnh nhất/cuối cùng trước khi hoàn tất mọi quyết định cần toàn bộ bằng chứng.

## 5. Bản đồ code cần đọc

Các đường dẫn dưới đây tương đối từ project root.

| File/khu vực | Vai trò và điểm cần giữ |
| --- | --- |
| `src/biliflow/ocr_batch_experiment.py` | `recognize_same_width`, `SameWidthReader`, giới hạn pixel batch |
| `src/biliflow/textscan.py` | `scan_text`, gọi OCR rồi tracking theo thứ tự thời gian; preview lấy từ đúng frame |
| `src/biliflow/frame_prefetch.py` | Cơ chế pipe/backpressure/cleanup hiện có; không tự bật |
| `src/biliflow/visual_logo_scanner.py` | RGB optimization, feature extraction, lossless routing cache v2 |
| `src/biliflow/job_pipeline.py` | Lệnh stage, mode OCR theo job, scope các nhóm model |
| `scripts/run.ps1` | GPU mutex `Local\BiliFlowGpuInference`; tránh sửa chỉ để thêm benchmark |
| `tests/test_ocr_batch_experiment.py` | Width/order/count/error/interrupt/wide crop/default/CLI |
| `tests/test_textscan.py` | Semantics/tracking/acceptance liên quan OCR |
| `tests/test_job_ocr_option.py` | Lựa chọn OCR, scope, persistence/UI |
| `tests/test_ocr_contiguous_benchmark.py` | So sánh kết quả benchmark |
| `tests/test_ocr_stress_benchmark.py` | Stress/threshold/đầu ra benchmark |
| `tests/test_stage_cache.py`, `tests/test_cache_dependencies.py` | Phân biệt cấu hình và dependency cache |

Tên module/file mới trong kế hoạch bên dưới là đề xuất, không phải API đã tồn tại. Khi triển khai tìm definition/call site thực tế bằng `rg`, không dựa vào số dòng có thể đã cũ.

### Hành vi OCR hiện tại

`SameWidthReader.readtext` chỉ chấp nhận đúng bộ option:

```python
dict(detail=1, paragraph=False, batch_size=1, workers=0, decoder="greedy")
```

Adapter gọi `reformat_input`, detect từng frame; với mỗi box dùng `get_image_list` riêng để có padded width như serial. Sau đó chỉ gom crop có cùng width **trong frame đó**, trả lại đúng thứ tự crop.

- Recognizer Latin, thực nghiệm CUDA vi/en.
- Python/CLI hiện có batch 1/2/4/8; Dashboard chỉ 1/8.
- `MAX_BATCH_PIXELS = 8 * 64 * 512`; đây là diện tích tensor input, **không phải trần VRAM tổng**.
- Crop rất rộng vẫn serial, không resize nhỏ để ép vừa batch.
- Tham số decoder/beam/contrast/filter/workers phải giữ như code và EasyOCR đang cài.

`scan_text` hiện xử lý theo thứ tự: đọc RGB → tính timestamp → OCR → acceptance → tracking → `track.add(..., frame_image)` → đóng ảnh → tăng progress. Khi nhóm nhiều frame, phải giữ chính xác thứ tự và ảnh tương ứng ở bước tracking.

## 6. Giai đoạn A — Thử recognition batching xuyên frame có giới hạn

### A1. Đo khả năng ghép trước khi viết nhiều code

Dùng một manifest các đoạn ngắn cố định; ghi số box/frame và phân bố **exact padded width**. Đo bao nhiêu crop có thể ghép trong nhóm 2/4/8 frame và bao nhiêu phải chạy serial. Không kết luận occupancy thấp chỉ từ suy đoán.

Nếu các width hầu như khác nhau, đừng nới width bucket/resize để ép batch. Ghi kết quả và dừng hướng này nếu overhead không bù được lợi ích.

### A2. Thiết kế thử nghiệm

Đường baseline là `easyocr.Reader.readtext` serial nguyên trạng. Đường thử nghiệm làm:

1. Đọc tối đa N frame liên tiếp theo chính sampling grid hiện tại, khởi đầu N=4, so thêm N=2/8 nếu cần.
2. Gắn `frame_index`, timestamp nguồn, RGB và hash. Giới hạn nhóm không có nghĩa giảm số frame.
3. Reformat/detect **từng frame bằng cùng API/tham số serial**. Chưa batch detection trong giai đoạn này.
4. Chuẩn bị mỗi crop như serial; lưu frame index, crop index, points, exact padded width, crop pixels và cấu hình reader.
5. Ghép crop từ các frame trong nhóm nếu width và mọi thuộc tính xử lý tương thích. Không trộn recognizer/device/dtype/decoder/ngôn ngữ khác nhau.
6. Chạy recognize theo batch count và diện tích input đã giới hạn. Giữ logic contrast retry và thứ tự trả về; kiểm tra bằng code EasyOCR local, không tự viết lại theo trí nhớ.
7. Khôi phục kết quả theo `(frame_index, crop_index)`; phát từng frame đủ kết quả theo đúng thứ tự gốc.
8. Chạy acceptance, tracking, tạo preview và semantics như trước trên chính frame đó.
9. Cuối nguồn flush nhóm còn thiếu N frame. Frame không có text vẫn phải đi qua vòng tracking/progress.

Pseudocode minh họa, chưa phải chữ ký API bắt buộc:

```python
for group in bounded_frame_groups(source, limit=frame_window, byte_limit=buffer_budget):
    prepared = [prepare_with_serial_detection(frame) for frame in group]
    predictions = recognize_compatible_width_buckets(prepared)
    validate_complete_frame_crop_mapping(prepared, predictions)
    for frame in group:  # thứ tự gốc, gồm cả frame không có text
        consume_with_existing_tracking(frame, predictions[frame.index])
```

### A3. Ràng buộc bộ nhớ và lỗi

- Giới hạn cả frame count, byte lưu RGB/gray/crop, crop count và diện tích inference; tránh buffer vô hạn khi frame nhiều chữ.
- Có thể bắt đầu với budget buffer 32 MiB như một **giá trị thử nghiệm**, nhưng phải hạch toán array/view/PIL/copy thực tế. Không tuyên bố đây là trần RAM toàn process.
- Khi sắp chạm budget, flush sớm; trường hợp một frame tự vượt budget thì xử lý serial đầy đủ, không cắt bớt box.
- Crop giữ NumPy view có thể giữ sống cả ảnh cha; kiểm tra ownership để số đo buffer không đánh lừa.
- `decoded_frames` và `committed_frames` nên phân biệt trong telemetry; progress hoàn thành dựa trên frame đã xử lý đủ, không báo 100% khi còn pending inference.
- Thiếu prediction, lỗi decode, lỗi model phải thất bại rõ; không tạo report hoàn tất thiếu frame.
- Cancel/KeyboardInterrupt không được biến thành fallback tiếp tục quét. Đóng pipe/process con và giải phóng group.
- Đợt đầu không bắt mọi exception rồi âm thầm retry. Nếu muốn fallback OOM sau này, phải có thiết kế riêng bảo đảm không duplicate/mất crop, không publish kết quả nửa chừng, có log và test.
- Không chạy nhiều model GPU cùng lúc để che thời gian chờ; vẫn dùng mutex hiện tại.

### A4. Tích hợp sau khi thử nghiệm đạt

Ban đầu đặt adapter/harness riêng, không sửa default Dashboard. Nếu đạt gate, tích hợp đường opt-in nhỏ nhất vào `scan_text`; giữ đường serial độc lập và dễ đối chiếu.

Nếu thêm `recognition_frame_window`, mặc định 1. Không lặng lẽ đổi ý nghĩa batch 8 hiện có thành cross-frame. Cấu hình mới phải có validation, report metadata, command/cache identity và persistence theo job nếu đưa lên Dashboard.

Tách refactor phục vụ iterator khỏi thay đổi thuật toán khi có thể; kiểm tra refactor với frame window 1 trước. Không sửa review/render chỉ để thích ứng với output khác.

## 7. Giai đoạn B/C — Chỉ làm khi số đo cho thấy đáng làm

### B. Detection nhiều frame

Nếu A chứng minh recognize không còn là bottleneck và detector tốn nhiều thời gian, mới thử batch text detection. Đây là thí nghiệm riêng vì resize/padding/postprocessing có thể đổi box và recall.

Giữ độ phân giải input, canvas/magnification, normalization, detector thresholds, coordinate transform và ordering. Chạy lại toàn bộ so sánh box/text/tracks/review. Không ghép A+B cùng một benchmark đầu tiên vì sẽ khó tìm nguyên nhân sai khác.

Nếu thư viện local không hỗ trợ đường tương đương đủ rõ, dừng thay vì thay detector hoặc giảm chất lượng để đạt tốc độ.

### C. CPU logo

Dựa trên profiler, thử giảm allocation/chuyển đổi lặp hoặc tính toán chung **chỉ khi inputs tương đương**. Giữ tất cả focus tiles, frame, feature values, window selection và model evidence.

Không đổi global thread count, bỏ vùng, nới ngưỡng hoặc dùng frame gần giống thay frame thật trong cùng đợt. Edge reuse đã chỉ lợi 1,88%; không đưa lại nếu không có giả thuyết và số đo mới.

Shared decode/cache có thể là hướng sau, nhưng không phải ưu tiên ngay từ số đo pipe wait. OCR/logo/safety dùng sampling/geometry khác nhau; muốn chia sẻ phải chứng minh từng consumer nhận đúng pixels và timestamp, có backpressure và cleanup. Cache reuse chủ yếu giúp chạy lại cùng video; không mô tả nó là giảm inference cho mọi video mới.

## 8. Kế hoạch benchmark và điều kiện đạt

### 8.1. Đóng băng baseline

- Dùng HEAD xác nhận ngay trước thử nghiệm, hiện là `ac3646b` (runtime chứa `2c72280`).
- Không so OCR mới với bản trước tối ưu CPU rồi gán tất cả lợi ích cho OCR.
- Ghi Git commit/diff, model revision/hash, Python/EasyOCR/PyTorch/CUDA, GPU/driver, profile/scope, sampling, geometry, thread settings và config.
- Dùng namespace report/cache/SQLite thử nghiệm riêng; không ghi vào job production.
- Harness có thể nạp module baseline từ bản frozen trong thư mục benchmark; nếu dependency thay đổi, đóng băng đủ dependency, không chỉ copy một file rồi tưởng đó là baseline độc lập.

### 8.2. Bộ đoạn nhỏ ban đầu

Chọn manifest hữu hạn, có thể dùng lại các đoạn sau nếu file còn nguyên. Đây là fixture benchmark, không phải quy tắc production theo tên phim/thời điểm.

| Nguồn | Bắt đầu giây | Thời lượng gợi ý | Mục đích |
| --- | ---: | ---: | --- |
| Troy | 0 | 60 | Opening, nhiều/số lượng chữ thay đổi |
| Troy | 48 | 90 | Native OCR pilot đã có bằng chứng |
| Troy | 418 | 60 | Đoạn hình tối/ít chữ |
| Troy | 940 | 60 | Nội dung khác để tránh chỉ tối ưu opening |
| Conan 21 | 4200 | 60 | Chữ trong phim và watermark |
| Conan 21 | 6630 | 60 | Credit/end-card |
| Conan 20 | 240 | 60 | Nguồn khác, animation |

Không nhất thiết chạy tất cả ngay vòng đầu. Bắt đầu 3–4 đoạn, mở rộng khi thấy lợi ích. Giữ cùng danh sách frame và cùng FFmpeg filtering giữa A/B. Với fixture lossless, xác nhận RGB hash và ghi rõ đã loại hashing/decode gốc ra khỏi timing.

Bổ sung test kiểm soát: frame rỗng, nhiều box, free-form box, width xen kẽ, crop cực rộng, crop gần threshold, banner chuyển động, nhóm cuối không đầy, scene boundary, cancel và lỗi giữa group. Dùng acceptance function thật vì confidence thấp có thể vẫn được nhận tùy vùng.

### 8.3. So sánh nhiều tầng, không chỉ đếm số mục

| Tầng | Phải kiểm tra |
| --- | --- |
| Input | Hash nguồn/fixture, số frame, RGB hash, thứ tự/timestamp, kích thước |
| Detection | Mọi raw box/points, crop mapping, padded width, text, số prediction |
| Confidence | Ghi sai khác max/phân bố; không xóa score trước khi xem tác động |
| Acceptance | Mọi nhận/loại và thứ tự sau sort confidence; không chỉ số lượng |
| Tracking | Track identity/observations, geometry, first/last time, semantics, retention |
| Preview | Hash ảnh và liên kết đúng nguồn/frame/region |
| Review | Primary/advisory, source refs, actions đề xuất, vùng, phạm vi áp dụng |
| Mapping | Đủ mọi ứng viên nguồn, không mất/hợp sai track, structural audit |
| Bảo toàn | Source stat/hash, queue/revision/job production, brand-memory không đổi |

GPU batching có thể khác confidence rất nhỏ dù width giống nhau. Không tự chọn tolerance lớn để PASS. Phải lượng hóa, kiểm tra threshold crossings, sort order/tie breaking và mọi kết quả downstream. Nếu có sai text/box/acceptance/track/review thì fail gate bảo toàn; điều tra trước khi tiếp tục.

Comparator cũ bỏ confidence/runtime khi normalize không đủ cho thay đổi mới. Lưu raw predictions và thêm so sánh score riêng. Chỉ bỏ đúng trường telemetry đã liệt kê; không bỏ một dictionary lớn để che khác biệt.

### 8.4. Đo tốc độ công bằng

- Warmup giống nhau; ít nhất hai vòng ABBA trên các đoạn chọn để thấy dao động. A=serial chuẩn, B=đường mới.
- Ghi riêng model load, detect, crop preparation, recognition, tracking/semantics/report, source hashing, decode, GPU-slot wait và total wall time.
- Nếu đo GPU bằng CUDA events/synchronize, dùng nhất quán hai đường; không thêm synchronize từng crop làm méo pipeline.
- Ghi median/range, trường hợp nhanh/chậm riêng, hiệu suất ghép thực tế và peak RAM/CUDA allocated/reserved. Không coi CUDA allocated là tổng VRAM.
- Không tăng số lần chạy vô hạn cho tới khi có kết quả đẹp. Nếu chỉ khoảng 1–2% hoặc dao động lấn lợi ích, dừng/giữ experimental.
- Gate đề xuất trước tích hợp: lợi ích OCR stage lặp lại khoảng 5% trở lên trên tập đoạn, không có regression đáng kể trên kiểu nội dung khác, cùng lúc đạt mọi gate đầu ra. Đây là tiêu chí kế hoạch, không phải mức đã đo hay cam kết toàn phim.

### 8.5. A/B đầy đủ sau khi thử ngắn đạt

Chỉ chạy lượt dài khi thực sự cần và có yêu cầu của người dùng; yêu cầu lập tài liệu hiện tại không cho phép tự chạy lại nhiều giờ.

- Hai đường cùng source/config/brand-memory snapshot, cùng model load policy và điều kiện cache.
- Tách cold result-cache và warm result-cache thành hai phép đo. Không xóa cache production; dùng namespace mới cho cả A và B.
- Ghi cụ thể routing hits, DINO hits/computed và stage reuse. Cold result-cache không có nghĩa cold cache hệ điều hành/model file.
- Không copy OCR từ lượt trước rồi gọi đó là total scan mới.
- Baseline và optimized phải là hai lượt hoàn chỉnh thực đo, không cộng timing từ nhiều trial khác nhau.
- Lượt advertising-only chỉ chứng minh scope đó. Nếu thay common scheduler/decode/model infrastructure, cần bounded real-video kiểm tra cả adult/gore/violence và cả content styles liên quan trước khi kết luận không ảnh hưởng toàn bộ.

## 9. Test, resource và production isolation

Sau khi sửa detector/scan orchestration, chạy test liên quan rồi full suite:

```powershell
Set-Location -LiteralPath 'E:\DungChung\BiliFlow'
. .\scripts\env.ps1
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_ocr_batch_experiment.py' -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_textscan.py' -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -v
```

Chạy thêm test cho file mới và scheduler/cache/UI nếu đã chạm vào chúng. Không chạy model/dependency license audit chỉ vì viết tài liệu; nếu đổi manifest/model/dependency/policy thì chạy `scripts/run.ps1 license-audit` theo AGENTS.

Các test mới quan trọng:

- Map frame/crop đúng khi width buckets trả kết quả không theo thứ tự frame.
- Frame rỗng và empty final flush vẫn cập nhật tracking đúng.
- Byte/count limit gây early flush nhưng không mất hoặc nhân đôi frame/crop.
- Wide crop không bị scale/clip để vừa memory cap.
- Missing prediction, incomplete FFmpeg frame, EOF/error, KeyboardInterrupt propagate đúng.
- Ảnh preview thuộc chính frame được dùng để cập nhật track; không dùng ảnh cuối group cho cả group.
- Confidence perturbation làm đổi sort/acceptance phải được comparator phát hiện.
- Window=1 giữ đường cũ; CPU/unsupported recognizer/options xử lý rõ ràng.
- Nếu expose config: serial/batch8 cũ không đổi command, scope quảng cáo riêng không thay safety commands, cache không dùng nhầm mode, resume/rerun giữ đúng setting.
- Cancel thật với CUDA + FFmpeg child ở harness riêng; không đánh đồng mock cancel với kiểm chứng process thực.

GPU benchmark dùng `Local\BiliFlowGpuInference` như launcher chính; nếu cần PowerShell launcher mới thì tạo riêng, không sửa `run.ps1` chỉ để thay cache fingerprint. Kiểm tra job đang chạy trước khi giành tài nguyên.

Mọi queue benchmark đặt dưới `reports/benchmarks/` hoặc tạo `.biliflow-benchmark` trong report root **trước khi tạo queue**. Không auto-import/activate benchmark queue vào Dashboard. JobStore/SQLite thử nghiệm tách biệt; DB production chỉ đọc nếu cần snapshot.

Không mở đồng thời nhiều session inference để cố tăng throughput RTX 2060 6 GB. Trước mắt tối ưu công việc bên trong một worker. Sau đo nếu muốn concurrency CPU/GPU phải là đề xuất riêng có benchmark contention và memory.

## 10. Bằng chứng và harness có sẵn

Tất cả đường dẫn dưới đây nằm trong project. `reports/benchmarks` là dữ liệu local, có thể không được Git track; Claude trên máy khác cần được cung cấp riêng nếu muốn đọc. Nếu thiếu thì báo thiếu, không giả định đã xác minh.

| Đường dẫn | Dùng để |
| --- | --- |
| `reports/benchmarks/troy-rgb-cold-full-20260928-194937/SUMMARY.md` | Báo cáo full gần nhất bằng tiếng Việt |
| Cùng thư mục: `trial.json`, `analysis.json`, `comparison.json` | Commands, timings, integrity, output differences |
| `reports/benchmarks/run_troy_rgb_full.py` | Cách chạy real stages trong state/cache riêng; không chạy ngay |
| `reports/benchmarks/cold-ad-stage.ps1`, `cold_ad_stage.py` | Mutex và redirect result cache riêng |
| `reports/benchmarks/compare_troy_ocr8_full.py` | Comparator cũ; phải bổ sung raw confidence checks khi cần |
| `reports/benchmarks/profile_ocr_phases.py` | Đo detect/recognize serial không sửa method behavior |
| `reports/benchmarks/ocr-phases-20260928-192818/profile.json` | 40-frame phase breakdown |
| `reports/benchmarks/logo-rgb-distance-20260928-193550/` | CPU ABBA + full suite 278/278 |
| `reports/benchmarks/logo-routing-equivalence-20260928-193742/` | Actual routing control-flow trước VLM |
| `reports/benchmarks/ocr-native-20260928-181450/` | Original-source OCR pilot, geometry native |
| `reports/benchmarks/ocr-contiguous-20260928-172310/` | Nhiều đoạn nối tiếp và lossless fixture |
| `reports/benchmarks/ocr-stress-20260928-175841/` | Moving/near-threshold text và GPU cancellation |

Không chạy lại harness lịch sử một cách mù quáng: có harness chứa trial key cố định hoặc tham chiếu prototype đã loại. Đọc trước, tạo run ID mới; không overwrite bằng chứng cũ.

### Nguồn Troy và dữ liệu production cần tránh ghi đè

```text
Source:
E:\DungChung\BiliFlow\input\TM2.Troy.2004.Directors.Cut.1080p.BluRay.DD5.1.x264-CtrlHD.mp4

Source SHA-256 đã ghi nhận:
f43cf94aadffb8c127c18fb23a51c58de2bdafcb2f05b1e91bd84be726fb19e9

Production review root đã xác minh trong lượt đo gần nhất:
reports/jobs/tm2-troy-2004-directors-cut-1080p-bluray-dd5-1-x-f43cf94a-run-20260927-234709/

Job #39, revision 3, WAITING_REVIEW tại thời điểm snapshot.
```

Trạng thái live có thể đổi sau khi người dùng thao tác; kiểm tra hiện trạng trước mọi công việc. Không khôi phục queue/state về snapshot lịch sử chỉ để khớp tài liệu.

## 11. Trình tự giao hàng và điều kiện dừng

1. Xác nhận baseline/invariants; tạo manifest và metric crop-width occupancy.
2. Viết prototype cross-frame recognition và focused tests; không thay production default.
3. Chạy bounded ABBA, lưu raw outputs, memory/timing và diff nhiều tầng.
4. Nếu không nhanh rõ hoặc output khác: báo nguyên nhân, giữ baseline, không tiếp tục tích hợp chỉ vì đã viết code.
5. Nếu đạt: tích hợp opt-in tối thiểu, kiểm tra window=1/serial, lifecycle và cache identity, chạy full unittest.
6. Cập nhật tài liệu trạng thái, chỉ rõ phần đã đạt và giới hạn; trình kết quả trước khi chạy A/B cả phim.
7. Sau A/B đầy đủ đạt mới đề xuất bật rộng hơn. Không tự merge/push hay đổi default.

Báo cáo mỗi milestone phải có:

- Commit baseline/optimized, scope và điều kiện cache/model load.
- Thời gian từng phase + tổng, median/range, không chỉ phần trăm model nhanh hơn.
- Số frame/box/track/review, diff score và output, hashes ảnh.
- RAM/VRAM, batch occupancy, cancel/error behavior, tests.
- Những gì chưa kiểm chứng; quyết định tiếp tục/loại thử nghiệm.
- Xác nhận source/review/state/brand-memory được bảo toàn.

Khi milestone được kiểm chứng, cập nhật `CHANGELOG.md`, `docs/PROJECT_STATUS.md`, `docs/SESSION_HANDOFF.md` và `docs/SCAN_PERFORMANCE.md` nếu có số đo mới. Lưu code benchmark có ích tái lập vào source phù hợp; không commit video/model/cache/DB hoặc báo cáo media lớn.

## 12. Prompt có thể gửi cho Claude

```text
Tiếp tục tối ưu BiliFlow tại E:\DungChung\BiliFlow.

Trước khi sửa, đọc AGENTS.md và các tài liệu theo thứ tự nó yêu cầu, sau đó đọc
docs/CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md. Kiểm tra Git status/log và ở nguyên
nhánh improve/scan-performance-metrics; không merge/push.

Triển khai giai đoạn A theo tài liệu: thử OCR recognition batching xuyên một
nhóm nhỏ frame, chỉ gom crop có cùng exact serial padded width. Trước hết đo
khả năng ghép và tạo thử nghiệm có giới hạn. Giữ baseline serial, toàn bộ frame,
timestamp, preprocessing, model, ngưỡng, tracking và review/export như hiện tại.
Không bật default hoặc thay ý nghĩa batch 8 cũ khi chưa qua các gate.

Kiểm chứng bằng bounded A/B đảo thứ tự và so sánh raw predictions, confidence,
acceptance, tracks, regions/times, JPEG hashes và review mappings; không chỉ dựa
vào Structure Audit PASS. Chạy focused tests và toàn bộ unittest nếu sửa scanner.
Preserve nguồn, report, state, quyết định review và brand-memory. Benchmark dùng
state/cache/queue riêng và GPU mutex hiện có; mọi tài nguyên mới ở ổ E.

Nếu chỉ lợi 1–2% hoặc có sai khác đầu ra ảnh hưởng review thì dừng hướng đó và
báo bằng chứng, không đánh đổi độ bao phủ. Chưa chạy lại cả phim dài, Visual AI,
export, model/API mới hay merge/push. Báo ngắn baseline và bước sẽ làm rồi tiến
hành thí nghiệm ngắn; cuối cùng cung cấp kết quả, diff và đề xuất bước tiếp theo.
```

Prompt trên là hướng dẫn người dùng có thể gửi để giao việc triển khai. Việc tạo tài liệu này tự nó không khởi động các thí nghiệm được mô tả.

## 13. Cập nhật 29/09/2026 — kết quả A–D và kế hoạch giai đoạn E

### 13.1. Trạng thái sau commit `c025486`

- A (recognition xuyên frame): OCR cả phim 752,6 → 631,2 giây (trung vị A/B/B/A), đầu ra giống hệt.
- B (batch detection, cuDNN autotune): loại, không nhanh hơn.
- C (`RoutingPool`, routing logo song song): routing cả phim 430,7 → 355,1 giây, đầu ra giống hệt.
- D (tùy chọn "Tăng tốc xử lý" = A + C, **bật mặc định** theo quyết định người dùng): pipeline quảng cáo cả phim 25m32s → **22m57s**; report, 858 JPEG, review 6/294, Structure Audit giống hệt/PASS.
- Chạy chồng routing với OCR (giải mã phần mềm cho cả hai): chỉ lợi 2,4–2,9 phút vì CPU bão hòa; chưa áp dụng.

### 13.2. Thời gian hiện tại (chế độ Tăng tốc xử lý, 22m57s)

| Stage | Giây | Thành phần chính |
| --- | ---: | --- |
| Preflight | 18,5 | Hash nguồn |
| OCR | 613,2 | `model_step` 539,8 (GPU-bound; detect CRAFT ~110 ms/frame), hash 18,8, tracking 16,5, chờ pipe 12,9 |
| Logo | 518,2 | **chờ pipe FFmpeg 342,2** (routing bị giới hạn bởi giải mã), VLM 96,0 + nạp 8,7, hash 19,2, chờ worker chỉ 4,5 |
| Localization | 219,7 | Florence/region localization 143,8, GroundingDINO 39,3, còn lại nạp model/IO |
| Review + audit | 7,1 | |

### 13.3. Phát hiện mới: chi phí CPU của giải mã 1080p

Đo trên Troy 600–1200 s (10 phút), đúng bộ lọc hiện tại:

| Cách giải mã | Wall | CPU của FFmpeg | Pixel so với hiện tại |
| --- | ---: | ---: | --- |
| Phần mềm (hiện tại) | 15,2 s | ~141 s | — |
| NVDEC, `fps` lọc trên GPU rồi mới `hwdownload` | 19,1 s | ~5,3 s | giống hệt (framemd5: 300 frame lưới logo, 200 frame lưới OCR) |

Lệnh NVDEC đã thử: `-hwaccel cuda -hwaccel_output_format cuda ... -vf "fps=…,hwdownload,format=nv12,format=yuv420p,scale=W:H:flags=bilinear"`. Lần thử trước (`-hwaccel cuda` không giữ frame trên GPU) chậm vì tải mọi frame 1080p về RAM.

Ý nghĩa: mỗi lần giải mã cả phim bằng phần mềm tốn ~2.750 giây-CPU (khoảng 9 luồng bận liên tục). OCR và logo mỗi bên giải mã riêng, nên khi chạy chồng CPU 6 nhân bão hòa. NVDEC giảm ~96% CPU nhưng wall chậm hơn ~26% (giới hạn engine giải mã ~790 frame/s).

### 13.4. Giả thuyết giai đoạn E

1. Stage OCR là GPU-bound; giải mã bằng NVDEC không làm stage chậm hơn (NVDEC là engine riêng, 375 s < 540 s model) và trả lại CPU cho EasyOCR/tracking.
2. Khi OCR không còn dùng CPU để giải mã, có thể **tính routing logo (giải mã phần mềm + 3 worker) song song trong lúc OCR chạy**, ghi vào routing cache. Stage logo sau đó gặp cache nóng, chỉ còn hash + VLM (~130 s thay vì ~518 s). Lợi ích tối đa lý thuyết ~5–6 phút (tổng ~17–18 phút); con số thật phải đo.
3. Stage logo tự nó không nên chuyển sang NVDEC (wall chậm hơn khi routing đã bị giới hạn bởi giải mã), trừ khi đo cho thấy khác.

Không làm: giảm độ phân giải, bỏ frame, `-skip_frame`, `-flags2 +fast` (đổi pixel), đổi model/threshold/precision.

### 13.5. Các bước, cổng kiểm tra và điều kiện dừng

**E1 — Chứng minh pixel giống hệt (chỉ đo, không sửa code production)**

- framemd5 phần mềm và NVDEC trên **cả phim Troy** cho lưới OCR (fps 1/3, 960×540) và lưới logo (fps 0,5, 320×180), cộng hai đoạn biên 30 s ở 0,25 s.
- Conan 20 và Conan 21 (profile Main, 23,976 fps): các đoạn có `-ss 0`, giữa phim và sát cuối file; cùng lưới như trên.
- Cổng: 100% frame giống hệt, cùng số frame, cùng timestamp. Chỉ một frame khác → loại NVDEC cho lưới đó và ghi lại bằng chứng.

**E2 — Stage OCR với NVDEC (A/B/B/A trên cả phim, chế độ Tăng tốc xử lý)**

- Thêm tùy chọn thử nghiệm `scan-text --decode cpu|nvdec` (mặc định `cpu`), chỉ đổi lệnh FFmpeg.
- Đo wall của stage, `model_step`, CPU hệ thống, VRAM; so report/preview/review như các lượt trước.
- Cổng: đầu ra giống hệt; stage OCR không chậm hơn quá 2%. Nếu chậm hơn → giữ `cpu` cho OCR và dừng E3.

**E3 — Đo lại chạy chồng khi OCR dùng NVDEC**

- OCR (NVDEC) chạy cùng lúc với routing cả phim (giải mã phần mềm, 3 worker), hai biến thể: ưu tiên thường và BELOW_NORMAL; thêm biến thể routing cũng dùng NVDEC để kiểm tra giới hạn engine.
- Chỉ số: OCR chậm đi bao nhiêu, routing có xong trước OCR không, tổng thời gian so với chạy nối tiếp.
- Cổng: OCR chậm đi ≤ 10% và tổng (OCR ∥ routing) + stage logo cache nóng nhỏ hơn cách hiện tại ≥ 3 phút. Không đạt → dừng, chỉ giữ E2 nếu E2 có lợi.

**E4 — Thiết kế tích hợp (chỉ khi E3 đạt)**

- Tách phần routing của `scan_visual_logos` thành hàm dùng chung (không đổi logic); thêm lệnh CPU-only `prewarm-visual-logo-routing` gọi đúng hàm đó rồi ghi routing cache (atomic như hiện tại).
- Stage OCR (khi `fast_scan`) khởi chạy prewarm như process con với BELOW_NORMAL, dùng đúng tham số logo của profile; khi OCR xong thì chờ prewarm rồi mới kết thúc stage. Nhờ vậy không có hai process cùng ghi một cache key (file `.tmp` có tên cố định).
- Prewarm lỗi → ghi log, không làm hỏng stage OCR; stage logo tự tính cold như cũ. Brand memory đổi giữa chừng → cache key khác → stage logo tính cold. Pause/cancel → scheduler giết cả cây process như hiện tại.
- NVDEC không dùng được (codec/driver/lỗi trước frame đầu) → chạy lại toàn bộ bằng `cpu` từ đầu. Lỗi giữa chừng → stage thất bại rõ ràng; không bao giờ ghép frame từ hai bộ giải mã.
- Không sửa `run.ps1`; lệnh mới không nằm trong danh sách GPU mutex vì chỉ dùng CPU.

**E5 — Test**

- Unit: lựa chọn backend và fallback, lệnh FFmpeg, prewarm lỗi không làm hỏng OCR, cancel giết cả process con, cache key giữ nguyên, lệnh standard (tắt Tăng tốc xử lý) không đổi.
- Test thật: cancel CUDA + FFmpeg (NVDEC) + prewarm; full suite.

**E6 — A/B cả pipeline**

- Chế độ hiện tại so với E, state/cache riêng, cùng phiên. Đầu ra phải giống hệt (report, JPEG, review, Structure Audit). Cập nhật tài liệu, hỏi người dùng trước khi commit.

### 13.6. Rủi ro đã biết

- NVDEC có thể khác pixel trên một số luồng (interlaced/PAFF, lỗi bitstream, driver mới) → E1 kiểm từng frame; giữ harness để kiểm lại khi đổi driver/FFmpeg.
- Tranh chấp GPU giữa NVDEC và CUDA khi OCR chạy → E2 đo trực tiếp.
- Laptop nóng lên khi CPU/GPU cùng bận lâu → chạy theo thứ tự đối xứng, ghi lại xung nhịp/nhiệt GPU.
- Process chạy ẩn trong stage OCR khó quan sát trên Dashboard → ghi log riêng, telemetry trong report.
- Worker `RoutingPool` là process spawn: mọi entry point phải là `python -m biliflow` hoặc script có guard `__main__`.

### 13.7. Ngoài phạm vi E

- Localization (~220 s): phân tích riêng sau (giai đoạn F), vì liên quan model Florence/GroundingDINO.
- Detect CRAFT (~430 s) là GPU-bound ở độ phân giải hiện tại; không đổi độ phân giải/precision.
- Dùng chung một lần giải mã cho OCR và logo (một FFmpeg nhiều đầu ra): chỉ xét lại nếu NVDEC thất bại ở E1/E2.

### 13.8. Kết quả E1/E2 và điều chỉnh kế hoạch (29/09/2026)

- **E1 đạt:** 11.322/11.322 frame giống hệt (pts, kích thước, MD5 RGB24): cả phim Troy (lưới OCR 3.921, lưới logo 5.881, hai đoạn biên 240), sáu đoạn Conan 20/21 (đầu/giữa/cuối, biên 0,25 s) và Troy 5000–5300 s. Driver 576.80. Bằng chứng: `reports/benchmarks/nvdec-equivalence-20260929-122204/`.
- **E2 không đạt cổng ≤ 2%:** OCR cả phim ở chế độ Tăng tốc xử lý, A/B/B/A: CPU 586,8/585,1 s, NVDEC 613,0/613,0 s (+4,6%); `model_step` +5,2% vì NVDEC và CUDA chia sẻ GPU. Đầu ra giống hệt (chênh confidence 0; track khớp report production). Bằng chứng: `ocr-cross-frame-downstream-20260929-123524/`. → **Không dùng NVDEC cho OCR chạy một mình.**
- **Điều chỉnh E3 (trước khi đo):** mục tiêu thật là tổng thời gian khi routing chạy chồng với OCR. Đo các biến thể, mỗi biến thể một lượt, có lượt OCR đơn lẻ (CPU) ở đầu và cuối làm mốc:
  - V1: OCR giải mã NVDEC + routing giải mã CPU (3 worker, ưu tiên thường).
  - V3: OCR giải mã CPU + routing giải mã NVDEC (3 worker, ưu tiên thường) — CPU chỉ còn một lần giải mã; OCR chịu tranh chấp NVDEC/CUDA.
  - Cổng E3 giữ nguyên: OCR chậm đi ≤ 10% và tiết kiệm ≥ 3 phút so với chạy nối tiếp (OCR đơn lẻ + stage logo hiện tại). Không đạt → dừng giai đoạn E, giữ trạng thái `c025486`, ghi bằng chứng.
- Để đo V3, thêm tùy chọn thử nghiệm `scan-visual-logo --decode cpu|nvdec` (mặc định `cpu`, cùng chính sách fallback như OCR); chưa đưa vào pipeline.

### 13.9. Kết quả E3 và quyết định dừng (29/09/2026)

Stage OCR thật (CLI, chế độ Tăng tốc xử lý) chạy một mình hoặc cùng lúc với routing cả phim (3 worker). Bằng chứng: `reports/benchmarks/stage-overlap-20260929-131800/`, `-132756/`, `-133946/`, `-135110/`.

| Lượt | OCR (s) | OCR chậm đi | Routing xong (s) | CPU hệ thống |
| --- | ---: | ---: | ---: | ---: |
| OCR một mình (đầu) | 595,0 | — | — | 68% |
| V1: OCR NVDEC ∥ routing CPU | 708,2 | +18,8% | 437,8 | 71% |
| V3: OCR CPU ∥ routing NVDEC | 683,3 | +14,6% | 483,5 | 72% |
| OCR một mình (cuối) | 597,7 | — | — | 69% |

- Đầu ra: report OCR và snapshot routing (window, feature, JPEG, lựa chọn VLM) giống mốc ở mọi lượt; nguồn, brand-memory, queue production không đổi.
- Cổng E3 không đạt ở điều kiện "OCR chậm đi ≤ 10%". Ước tính (chưa đo) V3 + stage logo cache nóng giảm tổng ~4 phút so với cách hiện tại, nhưng không nới cổng sau khi đã thấy kết quả.
- Theo kế hoạch: **dừng giai đoạn E**, giữ `c025486` làm trạng thái production. Tùy chọn thử nghiệm `--decode cpu|nvdec` (mặc định `cpu`) cho `scan-text`/`scan-visual-logo` và harness liên quan đang ở working tree, chưa commit; chỉ giữ lại nếu người dùng quyết định làm E4 với cổng mới.
- Quyết định cần người dùng: chấp nhận OCR chậm ~15% để đổi lấy ~4 phút tổng (cần làm E4: lệnh prewarm routing, process con ưu tiên thấp trong stage OCR, kiểm thử cancel/lỗi, A/B cả pipeline), hoặc dừng tại đây.

### 13.10. Quyết định người dùng và cổng mới cho E3b (29/09/2026, ghi TRƯỚC khi đo)

Người dùng đồng ý đánh đổi vì chạy chồng không đổi đầu ra (chỉ đổi thời điểm chạy). Cổng "OCR chậm ≤ 10%" được thay bằng tiêu chí trên **tổng pipeline**, đặt trước khi đo:

- **E3b (prototype, không sửa pipeline production):** harness chạy pipeline quảng cáo cả phim; trong stage OCR, một process chạy song song chính là lệnh `scan-visual-logo` của stage logo (cùng argv + `--decode nvdec`) nhưng dừng ngay sau khi ghi routing cache vào namespace benchmark; stage logo sau đó phải gặp cache hit. So với một lượt pipeline hiện tại (`c025486`, Tăng tốc xử lý) chạy trước trong cùng phiên.
- **Cổng:** tổng thời gian giảm **≥ 3 phút (180 s)**; report OCR/logo/localized, toàn bộ JPEG, đề xuất review và Structure Audit giống lượt hiện tại (chỉ khác telemetry đã liệt kê); stage logo xác nhận cache hit; dữ liệu production bảo toàn.
- Đạt → E4 (tích hợp thật + test pause/cancel/lỗi + đo lại cả pipeline bằng code tích hợp). Không đạt → dừng E, gỡ tùy chọn `--decode` khỏi `src`, giữ bằng chứng.

### 13.11. Kết quả E3b — ĐẠT (29/09/2026)

| Stage | Hiện tại `troy-full-fast-20260929-141826` | Prototype `troy-full-overlap-20260929-144100` |
| --- | ---: | ---: |
| Preflight | 18,2 | 18,8 |
| OCR (+ routing làm nóng song song) | 596,1 | 681,5 |
| Logo | 511,7 | 133,0 (routing cache hit) |
| Localization | 219,6 | 219,6 |
| Review + audit | 7,4 | 7,3 |
| **Tổng** | **1.353,0 s (22m33s)** | **1.060,2 s (17m40s), −292,8 s (−21,6%)** |

Report OCR/logo: 0 khác biệt; localized: 3 trường thời gian; 858/858 JPEG; review 6/294 trùng; coverage đủ; Structure Audit PASS; dữ liệu production bảo toàn. Routing làm nóng (NVDEC, 3 worker) xong trước khi OCR kết thúc.

### 13.12. Thiết kế E4 (tích hợp thật)

1. `scan_visual_logos(routing_only=True)` / `scan-visual-logo --routing-only`: chạy đúng đoạn routing hiện có (cùng cache key, cùng code), trả về ngay sau khi ghi routing cache hoặc gặp cache hit; không nạp VLM, không ghi gì vào report dir.
2. `scan-text --prewarm-logo-routing` kèm tham số logo của profile (`--logo-sample-every`, `--logo-boundary-sample-every`, `--logo-boundary-seconds`, `--logo-scene-change-threshold`, `--logo-coverage-bucket-seconds`, `--logo-coverage-fallbacks-per-bucket`, `--logo-routing-workers`, `--logo-source-sha256`): CLI khởi chạy `python -m biliflow scan-visual-logo --routing-only --decode nvdec ...` làm process con trước khi quét OCR; OCR xong thì chờ process con. Hàm `scan_text` không đổi.
3. Process con lỗi → cảnh báo, stage OCR vẫn thành công, stage logo tự tính cold. OCR lỗi/bị ngắt → kết thúc process con rồi báo lỗi. Pause/cancel của scheduler giết cả cây process.
4. `pipeline_stages(fast_scan=True)` thêm các cờ trên cho stage OCR với **chính các giá trị** truyền cho stage logo; test so khớp tham số hai stage để cache key không lệch. Tham số lệch chỉ làm stage logo tính cold (vẫn đúng, chỉ chậm hơn).
5. Test: routing-only không nạp model/không ghi report; lỗi/ngắt của process con; khớp tham số; lệnh standard không đổi; full suite. Sau đó E6: A/B cả pipeline bằng code tích hợp, so với lượt hiện tại.

### 13.13. Kết quả E4/E6 — ĐẠT (29/09/2026)

Tích hợp theo 13.12; 319/319 test. A/B cả pipeline bằng code tích hợp (`troy-full-fast-20260929-150440`) so với lượt hiện tại cùng ngày (`troy-full-fast-20260929-141826`): **22m33s → 17m45s (−288 s, −21,3%)**; OCR 596,1 → 685,2 s, logo 511,7 → 133,5 s (cache hit), localization 219,6 → 220,1 s. Report OCR/logo giống hệt, localized chỉ khác 3 trường thời gian, 858/858 JPEG, review 6/294 trùng, Structure Audit PASS; job/queue/nguồn/brand-memory production bảo toàn; không file nào trong `cache/` production thay đổi (harness chuyển process làm nóng sang namespace benchmark qua `scripts/benchmark-text-stage.ps1`).

## 14. Giai đoạn F — cắt thời gian chờ còn lại, đầu ra giống hệt (kế hoạch 29/09/2026)

Mốc: `12a59a3`, pipeline quảng cáo Troy 17m45s (`troy-full-fast-20260929-150440`). Phân bổ đo được:

| Stage (s) | Thành phần |
| --- | --- |
| OCR 685,2 | GPU-bound; chậm hơn 89 s vì routing làm nóng chạy cùng (routing xong ở ~480 s, còn ~200 s dư) |
| Logo 133,5 | hash nguồn 19,5; nạp VLM 8,7; VLM 94,6 (390 lượt) |
| Localization 220,1 | Florence: trích frame 27,2 (116 lần FFmpeg tuần tự, PNG) + suy luận 116,0 + nạp 2,6; DINO: trích lại frame 19,0 (112 lần, JPEG) + nạp/suy luận 39,4; ~12 s khởi động process |

Chỉ làm các thay đổi **không đổi đầu vào của model** (giống hệt theo cấu trúc) và vẫn đo xác nhận:

- **F1 — Routing làm nóng chạy ưu tiên thấp (BELOW_NORMAL, process con kế thừa).** Routing có ~200 s dư nên nhường CPU cho OCR. Kỳ vọng OCR 685 → 620–650 s. Rủi ro: routing xong muộn hơn OCR → stage OCR chờ; stage logo vẫn gặp cache.
- **F2 — Băm nguồn chạy nền, xác minh trước khi ghi.** OCR: băm song song trong lúc quét, chờ kết quả trước khi tạo report. Logo (khi có `--source-sha256` kỳ vọng): dùng hash kỳ vọng để tra cache ngay, băm nền; bắt buộc xác minh khớp **trước khi ghi routing cache và trước khi ghi `scan.json`**, sai → lỗi, không có artifact. Kỳ vọng −35…−40 s. Không đổi preflight (nơi tạo danh tính job).
- **F3 — Trích frame song song cho localization**, cùng đúng lệnh FFmpeg: Florence prefetch tối đa 4 frame trước (chạy trong lúc GPU suy luận); DINO chạy các lệnh trích frame với tối đa 4 FFmpeg cùng lúc. Kỳ vọng −35…−40 s.
- Không làm trong F: batch Florence/VLM (beam search nhạy số học, cần phép thử riêng), gộp process Florence+DINO (~12 s), đổi JPEG/PNG của đầu vào model.

Kiểm chứng:

1. Unit test cho từng thay đổi (thứ tự frame, lỗi/hủy, hash sai → không artifact, priority).
2. F3 đo riêng: chạy lại stage localization (cả hai lệnh) trên `scan.json` của `troy-full-fast-20260929-150440`, so file `scan-localized.json` và toàn bộ frame/JPEG với bản gốc — phải giống hệt trừ telemetry thời gian.
3. A/B cả pipeline F so với `12a59a3` cùng phiên: đầu ra giống hệt như các lượt trước; báo thời gian từng stage để quy công cho F1/F2/F3.

Cổng: đầu ra giống hệt; tổng nhanh hơn ≥ 60 s. Mục nào tự nó làm chậm stage của mình → bỏ mục đó.

Track chất lượng (đề xuất riêng, cần người dùng tham gia gán nhãn): bộ nhãn chuẩn để đo recall/precision; ca Conan Movie 20 nhận nhầm watermark với đầu nhân vật.

### 14.1. Kết quả giai đoạn F — ĐẠT (29/09/2026)

| Mục | Kết quả đo | Quyết định |
| --- | --- | --- |
| F1 routing ưu tiên thấp | OCR `model_step` 613 → 618 s (không nhanh hơn); routing 499 → 596 s, khoảng dư trước khi OCR xong 186 → 77 s | **Loại** (tranh chấp là GPU/băng thông, không phải CPU) |
| F2 băm nền | OCR: hash 18,8 → 0 s chờ; logo 133,5 → 116,0 s | Giữ |
| F3 trích frame song song | Florence chờ frame 27,2 → 0,4 s; DINO 19,0 → 7,2 s; localization 220,1 → 185,0 s; đầu ra và 112 frame giống hệt | Giữ |

Cấu hình cuối (F2 + F3), `troy-full-fast-20260929-164440`: **16m35s** so với 17m45s (−69,9 s ≥ cổng 60 s); report giống hệt, 858/858 JPEG, review 6/294, Structure Audit PASS; production và cache production không đổi. 326/326 test.

## 15. Phép thử G0 — detect CRAFT ở FP16 (kế hoạch 29/09/2026, ghi trước khi đo)

Mốc: `6a50df8` (16m35s). Detect CRAFT là phần lớn nhất còn lại (~430 s, GPU-bound). Phép thử chỉ đo, không sửa code production:

- Cùng 150 frame (7 đoạn Troy/Conan) của harness `abba`. A = EasyOCR nguyên trạng (FP32). H = cùng reader, chỉ forward của CRAFT chạy trong `torch.autocast(float16)`, đầu ra ép về FP32 trước hậu xử lý; recognition giữ FP32. Thứ tự A/H/H/A, warmup riêng.
- Đo: thời gian detect, toàn bộ dự đoán thô (box, text, confidence), nhận/loại và thứ tự sau lọc.
- Quyết định:
  - Giống hệt trên cả 150 frame và detect nhanh hơn ≥ 20% → mở rộng thành phép thử cả phim như giai đoạn A; chỉ tích hợp nếu cả phim cũng giống hệt.
  - Có khác biệt → ghi số lượng/loại khác biệt; **không tích hợp** cho tới khi có bộ nhãn chuẩn để chứng minh chất lượng không giảm (track chất lượng).
  - Không nhanh hơn ≥ 20% → loại.

### 15.1. Kết quả G0 (29/09/2026)

`reports/benchmarks/ocr-cross-frame-abba-20260929-173102/`: detect CRAFT FP32 14,891/15,057 s, FP16 autocast 9,177/9,125 s → **−39%**; CUDA allocated 458 → 372 MiB. Recognition không đổi.

Đầu ra: 147/150 frame giống hệt; 3 frame khác, lặp lại y hệt giữa hai lượt:

- Troy 24 s: box chữ "A" đơn lẻ lệch cạnh 2 px (vốn bị loại vì < 2 ký tự).
- Troy 75 s: FP32 có box rác "PaiT1a" (confidence 0,02, vốn bị loại); FP16 không có.
- Troy 117 s: watermark "XEMBZ NET" lệch 2 px, confidence 0,887 → 0,652; vẫn được nhận ở cả hai.

Theo quy tắc ghi trước khi đo: có khác biệt → **không tích hợp** khi chưa có cách chứng minh chất lượng không giảm. Ước tính nếu áp dụng: detect cả phim ~430 → ~260 s, tổng pipeline ~16m35s → ~13m50s (chưa đo). Cần người dùng chọn tiêu chí: bộ nhãn chuẩn (chặt) hoặc so sánh ở mức kết quả review trên cả phim có người duyệt khác biệt (thực dụng).

## 16. G1 — FP16 detect CRAFT, xác nhận ở mức review (kế hoạch 29/09/2026, người dùng chọn "Cách 1")

1. **Tùy chọn opt-in** `scan-text --detect-precision fp32|fp16` (mặc định `fp32`): FP16 chỉ bọc forward của CRAFT trong `torch.autocast(float16)` và trả bản đồ điểm về float32 trước hậu xử lý; recognition, ngưỡng, sampling, độ phân giải giữ nguyên. Chưa đưa vào pipeline/Dashboard.
2. **A/B cả pipeline cùng phiên**: lượt mốc = chế độ Tăng tốc xử lý hiện tại (`6a50df8`, FP32); lượt thử = như trên + `--detect-precision fp16` chỉ cho stage OCR (harness thêm cờ, không sửa pipeline).
3. **So sánh**:
   - Report logo, localized và mọi JPEG của nhánh logo phải giống hệt (OCR không ảnh hưởng nhánh này).
   - OCR/review: ghép mục review giữa hai lượt theo loại và thời gian; liệt kê mục mất/thêm, đổi khoảng thời gian, đổi đề xuất, vùng lệch bao nhiêu pixel; tạo trang HTML tiếng Việt kèm ảnh preview hai bên để người dùng duyệt.
4. **Cổng**: không mục review chính nào bị mất hoặc đổi đề xuất KEEP/BLUR/CUT; mọi khác biệt còn lại được liệt kê đầy đủ. **Chỉ tích hợp vào "Tăng tốc xử lý" khi người dùng duyệt danh sách khác biệt.** Tổng thời gian phải nhanh hơn ≥ 60 s.
5. Sau đó (nếu được duyệt): tích hợp, test, cập nhật tài liệu, hỏi commit. Bộ nhãn chuẩn (Cách 2) làm tiếp theo làm nền đo lâu dài.

### 16.1. Kết quả G1 (29/09/2026) — chờ người dùng duyệt

Lượt FP16 `troy-full-fp16-20260929-173943` so với FP32 `troy-full-fast-20260929-164440` (cùng code `6a50df8` + tùy chọn opt-in, cùng ngày):

| Stage | FP32 (s) | FP16 detect (s) |
| --- | ---: | ---: |
| Preflight | 18,5 | 18,1 |
| OCR (+ routing làm nóng) | 668,2 | 540,0 |
| Logo | 116,0 | 117,1 |
| Localization | 185,0 | 183,9 |
| Review + audit | 7,2 | 7,3 |
| **Tổng** | **994,9 (16m35s)** | **866,6 (14m27s), −128,3 s (−12,9%)** |

- Nhánh logo (scan, localized, mọi JPEG logo) giống hệt; 6/6 mục chính và 294/294 advisory giống hệt về loại, thời gian, vùng, đề xuất, nhãn (`review-diff.json`/`review-diff.html`).
- Khác biệt duy nhất trong hàng đợi: một tham chiếu nguồn `track:2211` → `track:2212` (đánh số lại vì có thêm track credits).
- Report OCR (45 đường dẫn JSON): mục watermark chỉ khác số thứ tự track/tên ảnh preview; hai track credits ở 11178 s đổi thứ tự; một track credits ở 11256 s lệch 2 px; thêm 2 track credits ngoài giới hạn report. Không track nào là ứng viên review.
- Cổng kỹ thuật đạt (không mục chính nào mất/đổi đề xuất; nhanh hơn ≥ 60 s). Chờ người dùng duyệt trước khi đưa vào "Tăng tốc xử lý".

### 16.2. G1b — xác minh thêm trên hoạt hình trước khi áp dụng (kế hoạch, ghi trước khi đo)

Người dùng yêu cầu thêm một lượt xác minh đầy đủ. Nguồn: Conan Movie 20 (job production #38, hoạt hình, 6.690 s, H.264), chỉ đọc.

- Harness nhận `--job-id`; hai lượt cùng phiên, state/cache riêng: FP32 (Tăng tốc xử lý hiện tại) rồi FP16 detect.
- Cổng: nhánh logo giống hệt; không mục review chính nào mất hoặc đổi đề xuất KEEP/BLUR/CUT; mọi khác biệt còn lại được liệt kê; ca hồi quy Conan 20 phải giữ nguyên: watermark phimmoi vẫn BLUR tại vùng ~`1565,52 282x46` (brand memory) và không có vùng blur trên đầu nhân vật (~`1203,193 261x239`, 510–515 s) ở cả hai lượt.
- Đạt cả Troy và Conan 20 → đề xuất người dùng duyệt để đưa FP16 vào "Tăng tốc xử lý". Không đạt → giữ FP32, ghi bằng chứng.

### 16.3. Kết quả G1b trên Conan Movie 20 (29/09/2026) — đạt, chờ người dùng duyệt

- **Lỗi có sẵn được phát hiện và sửa:** lượt FP32 đầu tiên dừng ở `augment-grounding-regions` vì `transformers` 5.17 `batch_decode([])` trả về `['']`, nên frame không có detection nào có 1 nhãn nhưng 0 box (`zip(strict=True)` lỗi). Không liên quan FP16/F3; job production nào gặp frame như vậy cũng sẽ dừng ở localization. Sửa: `_grounding_boxes` trả `[]` khi không có box, mọi lệch khác vẫn lỗi; có test hồi quy. Lượt lỗi `conan20-full-fast-20260929-181745` được giữ làm bằng chứng.
- Chạy lại cùng phiên: FP32 `conan20-full-fast-20260929-183147` 658,0 s; FP16 `conan20-full-fp16-20260929-184246` 632,4 s. OCR 380,3 → 321,5 s (−58,8 s). Logo (+11,7 s) và localization (+19,5 s) chậm hơn ở lượt FP16 dù logic và đầu ra giống hệt — chỉ phần suy luận GPU chậm (VLM 115,4 → 123,2 s, Florence 75,8 → 90,4 s), tức trôi hiệu năng máy ở lượt chạy sau, làm lượt FP16 bị thiệt.
- Đầu ra: nhánh logo giống hệt (scan, localized, JPEG); 2/2 mục chính, 393/393 advisory giống hệt; ứng viên review 2 = 2; track: LIKELY_CREDITS 177 → 175, LIKELY_SCENE_TEXT 277 → 276, các nhóm REVIEW_* và LOW_AD_UNCERTAIN không đổi.
- Ca hồi quy Conan 20 giữ nguyên ở cả hai lượt: watermark phimmoi BLUR tại `1565,52 282x46`; không có vùng trên đầu nhân vật quanh 510–515 s.
- Kết luận G1/G1b: cổng đạt trên Troy (live action) và Conan 20 (hoạt hình). Đề xuất đưa `--detect-precision fp16` vào "Tăng tốc xử lý" sau khi người dùng duyệt.

### 16.4. G1c — kiểm tra các nhóm an toàn (kế hoạch, ghi trước khi đo)

Người dùng yêu cầu xác nhận FP16 không ảnh hưởng model khác. Về cấu trúc: FP16 chỉ bọc forward CRAFT trong process `scan-text`; các model an toàn chạy ở stage/process riêng và (với live action) chạy **trước** stage OCR. Để đo trực tiếp: Conan Movie 20 (#38) với đủ nhóm quảng cáo + 18+ + máu me + bạo lực (stage `animation_safety`), FP32 rồi FP16 cùng phiên. Cổng: mọi report/ảnh an toàn giống hệt, nhánh logo giống hệt, mọi mục review (kể cả mục an toàn) giống hệt về loại/thời gian/vùng/đề xuất. Live action an toàn (Troy, 4–5 stage, rất dài) không chạy lần này; dựa vào lập luận thứ tự stage và có thể chạy sau nếu người dùng muốn.

### 16.5. Kết quả G1c — đủ nhóm trên Conan Movie 20 (29/09/2026): đạt

`conan20-allgroups-full-fast-20260929-190913` (FP32, 1.669,6 s) và `conan20-allgroups-full-fp16-20260929-193703` (FP16, 1.641,7 s):

- Report an toàn `animation-safety/{adult,gore,violence}/scan.json`, report tổng và mọi ảnh an toàn: **giống hệt**. Nhánh logo (scan, localized, JPEG): giống hệt.
- Review: 69/69 mục chính (55 gore, 9 violence, 3 adult, 2 visual_logo) và 393/393 advisory giống hệt về loại, thời gian, vùng, đề xuất.
- Thời gian: stage an toàn 911,9 / 915,3 s (không dùng FP16); OCR 438,0 → 353,0 s (−85 s). Localization 147,0 → 204,9 s dù code và đầu ra giống hệt: +22 s trong các pha đo được (suy luận chậm hơn) và +36 s ngoài các pha (khởi động process/nạp) — nhiễu môi trường ở lượt chạy sau, không có cơ chế liên quan FP16 (stage chạy ở process riêng sau khi OCR đã thoát). Tổng chỉ −27,9 s trong cặp này vì nhiễu đó.
- Kết luận: FP16 detect không ảnh hưởng model an toàn, logo, VLM, Florence hay GroundingDINO; chỉ đổi vài track credits/chữ trong cảnh ngoài ứng viên review. Lợi ích OCR ổn định: −128 s (Troy), −59 s và −85 s (Conan 20).

### 16.6. Áp dụng (29/09/2026)

Người dùng duyệt. `FAST_SCAN_DETECT_PRECISION = "fp16"`: "Tăng tốc xử lý" thêm `--detect-precision fp16` vào stage OCR; lệnh sinh ra trùng khớp từng tham số với lệnh đã benchmark (`troy-full-fp16-20260929-173943`). Chế độ thường và chế độ OCR batch 8 cũ vẫn FP32. 335/335 test.

## 17. Giai đoạn H — tăng tốc stage an toàn hoạt hình (kế hoạch 29/09/2026, ghi trước khi đo)

Người dùng yêu cầu tiếp tục nâng cấp trong lúc chờ gán nhãn Golden Set (việc đó cần người dùng ngồi máy tính). Giai đoạn H không cần nhãn.

### 17.1. Hiện trạng đo được

- Conan Movie 20 đủ nhóm, chế độ Tăng tốc xử lý (`conan20-allgroups-full-fp16-20260929-193703`): tổng 1.641,7 s; **`animation_safety` 915,3 s (56%)**, OCR 353 s, logo 153 s, localization 205 s, review 10 s. Chỉ quảng cáo: 632 s. Stage an toàn là stage lớn nhất còn lại và chưa từng được tối ưu.
- Bên trong stage (metrics của report): `model_step` 886,0 s / 900,6 s (98,4%); `frame_pipe_wait` 2,9 s (FFmpeg 256×256 @ 2 fps theo kịp); 13.379 frame, 1.673 batch × 8; ≈ 66 ms/frame.
- Profile H0 (`reports/benchmarks/anime-safety-profile/`, Conan 20 3300–3450 s, 300 frame, RTX 2060 6 GB): biến đổi PIL 256→448 bicubic 4,3 ms/frame; chép sang GPU 0,4; **forward FP32 47,4**; forward FP16 autocast 14,4 (3,3×). Xác suất FP16 lệch tối đa 0,0078, p99 4·10⁻⁵. Model `wd-vit-tagger-v3` (`vit_base_patch16_224` chạy 448×448, fused attention đã bật).
- Dữ liệu duyệt thật (các revision đang dùng): 111/111 mục an toàn hoạt hình được người dùng chọn KEEP. Đây là vấn đề chất lượng/gánh nặng duyệt, thuộc track chất lượng (cần Golden Set); **giai đoạn H không đổi ngưỡng, sampling, nhóm hay model**.

### 17.2. Các bước

- **H1 — giống hệt từng bit:** chuẩn bị tensor của batch kế tiếp (biến đổi PIL + stack) trên một luồng nền trong khi GPU chạy batch hiện tại; thứ tự, nội dung batch và mọi xử lý kết quả (hit, heap, thumbnail, report) giữ nguyên trên luồng chính; lỗi/hủy dọn cả luồng lẫn FFmpeg.
  - Cổng H1: mọi `animation-safety/*/scan.json` (trừ `metrics`, `runtime`, `created_at`) và mọi JPEG giống hệt byte trên 2 đoạn trích (Conan 20 3300–3450 s, Conan 21 0–150 s) và trên cả phim; stage cả phim nhanh hơn ≥ 30 s, nếu không thì bỏ H1.
- **H2 — FP16 cho forward (không giống hệt từng bit, opt-in):** `scan-animation-safety --precision fp32|fp16`, mặc định fp32. FP16 chỉ bọc forward trong `torch.autocast(float16)`, logits về float32 trước sigmoid; ngưỡng, sampling, độ phân giải, temporal, co-occurrence giữ nguyên. Chưa đưa vào pipeline/Dashboard.
  - Đo cả phim theo cách rẻ hơn: chỉ chạy lại stage an toàn (FP32 và FP16, cùng phiên) vào thư mục riêng dưới `reports/benchmarks`, rồi `build-review` với **cùng** report chữ/logo cho cả hai bên. Stage an toàn không đọc/ghi gì của chữ/logo, nên khác biệt review chỉ đến từ FP16. Nguồn: Conan 20 và Conan 21 (đủ nhóm).
  - Cổng H2 (như G1): không mục review chính nào mất/thêm/đổi đề xuất KEEP/BLUR/CUT trên cả hai phim; mọi khác biệt còn lại (advisory, khoảng thời gian, số frame vượt ngưỡng từng nhóm) được liệt kê trong `review-diff.html`; ca hồi quy Conan 20 giữ nguyên; stage nhanh hơn ≥ 5 phút mỗi phim. **Chỉ đưa vào "Tăng tốc xử lý" khi người dùng duyệt.**
- **H3 (sau khi được duyệt):** `FAST_SCAN_ANIMATION_PRECISION = "fp16"` như §16.6; lệnh sinh ra phải trùng lệnh đã đo; test; tài liệu.
- Ngoài phạm vi: đổi ngưỡng/sampling/nhóm; tăng batch (đổi kernel, không giống hệt, GPU đã bão hòa); stage an toàn live action (`scan-live-safety`, đo sau); TensorRT.

### 17.3. Ước tính, rủi ro, điều kiện dừng

- Ước tính: H1 ≈ −1 phút; H2 ≈ −7 đến −9 phút mỗi phim hoạt hình đủ nhóm (Conan 20: ~27 → ~17–19 phút).
- Rủi ro: FP16 lệch xác suất gần các ngưỡng 0,02–0,20 → frame hit đổi → khoảng review đổi (vì vậy cổng ở mức review trên 2 phim). Bộ nhớ GPU giảm chứ không tăng. Các lượt cả phim (~15 phút GPU mỗi lượt FP32, ~5 phút FP16) làm máy nặng: chỉ chạy khi người dùng cho phép vì máy đang được dùng; đoạn trích ngắn (1–2 phút) chạy được ngay.
- Dừng: H1 lệch bất kỳ byte nào hoặc không nhanh hơn → bỏ H1. H2 làm mất/đổi đề xuất mục chính trên một trong hai phim → giữ FP32, ghi bằng chứng; xem lại khi có Golden Set (cổng recall).

### 17.4. Kết quả trên đoạn trích (29/09/2026)

Mã: `BatchPrefetch` trong `src/biliflow/frame_prefetch.py` (đọc + biến đổi batch kế tiếp trên luồng nền, độ sâu 2; phần dọn dẹp dùng chung với `FramePrefetch`), `scan-animation-safety --precision fp32|fp16` (mặc định fp32, chưa vào pipeline), `runtime.precision` và `metrics.batch_prefetch` trong report. Harness `scripts/benchmark_animation_safety.py` (+ `.ps1`, giữ mutex GPU) chạy scanner của `HEAD` (qua `git show`, không đụng working tree) và scanner hiện tại trên đoạn trích stream-copy. 370/370 test.

`reports/benchmarks/anime-safety-h/equivalence-20260929-230252` (máy đang có người dùng, GPU ~22% nền):

| Đoạn trích (150 s) | HEAD fp32 | H1 fp32 | H2 fp16 |
| --- | ---: | ---: | ---: |
| Conan 20 3300 s | 21,3 s | 17,5 s | 7,4 s |
| Conan 21 0 s | 20,2 s | 17,1 s | 6,7 s |

- H1: mọi report (trừ metrics/runtime/created_at) và 62 + 63 JPEG giống hệt từng byte.
- H2: số hit thô, hit đã xác nhận và mọi khoảng thời gian của adult/gore/violence trùng FP32 trên cả hai đoạn trích (thời gian gồm cả nạp model ~2 s và hash nguồn).
- Còn lại: xác minh cả phim Conan 20 và Conan 21 (§17.2, ~40 phút GPU) — chờ người dùng chọn lúc máy rảnh vì máy đang được dùng và số đo thời gian sẽ nhiễu.

### 17.5. Kết quả cả phim — H1 và H2 ĐẠT (29–30/09/2026)

`reports/benchmarks/anime-safety-h/full-20260929-231545` (một phiên, tuần tự, stage an toàn riêng; máy có người dùng nền):

| Lượt | Conan 21 | Conan 20 |
| --- | ---: | ---: |
| HEAD fp32 | 800,7 s | (tham chiếu byte: `conan20-allgroups-full-fp16-20260929-193703`, 915,3 s ở phiên trước) |
| H1 fp32 | 670,9 s (−129,8 s, −16,2%) | 667,4 s |
| H2 fp16 | **208,4 s** (−462,5 s so với H1; −74% so với HEAD) | **207,5 s** (−459,9 s so với H1) |

- **H1:** report (trừ metrics/runtime/created_at) và 126 + 132 JPEG giống hệt từng byte trên cả hai phim; nhanh hơn 129,8 s ≥ 30 s. `model_step` 782 → 659 s vì biến đổi CPU đã ra khỏi luồng GPU; `batch_wait` 0,3 s.
- **H2:** review dựng lại với cùng report chữ/logo: Conan 21 67/67 mục chính + 61/61 advisory, Conan 20 69/69 + 393/393 **giống hệt** (loại, thời gian, vùng, đề xuất, nhãn, ưu tiên). Mọi khoảng adult/gore/violence giống hệt. Ở mức frame: gore thô 804 → 803 (C21), 1028 → 1027 và gore xác nhận 581 → 577 (C20) — không đổi khoảng nào; hệ quả duy nhất nhìn thấy: 2 ảnh đại diện khoảng máu me của Conan 20 lùi 1 frame (471,0 → 471,5 s; 5549,0 → 5549,5 s). Hàng đợi FP32 dựng lại từ report của lượt benchmark Conan 20 trùng hàng đợi gốc của lượt đó (kiểm tra độc lập cách so).
- Ước tính quét hoạt hình đủ nhóm ở chế độ Tăng tốc xử lý: Conan 20 ~1.642 s → ~934 s (~27 → ~16 phút, −43%), nếu áp dụng H2.
- Sau lượt đo, rà soát nhiều agent (§ QUALITY_PLAN 14) sửa: `frames_scanned` khi quét bị ngắt (chỉ đường lỗi; báo cáo COMPLETED không đổi), docstring độ sâu hàng đợi, và harness (thư mục `--run-dir` tuyệt đối, lượt dở dang chuyển sang `.attempt-N`, từ chối chạy tiếp khi mã đổi, chỉ so thời gian cùng một lần chạy). 386/386 test.
- **H3 chờ người dùng duyệt:** đưa `--precision fp16` vào "Tăng tốc xử lý" cho stage an toàn hoạt hình (như §16.6). H1 đã là mặc định vì giống hệt từng bit.

### 17.6. Áp dụng H3 (30/09/2026)

Người dùng duyệt ("Tiếp tục đi bạn" sau câu hỏi duyệt H3). `FAST_SCAN_ANIMATION_PRECISION = "fp16"`: "Tăng tốc xử lý" thêm `--precision fp16` vào stage `animation_safety` (hoạt hình/mixed khi chọn nhóm an toàn); mọi tham số khác của lệnh giữ nguyên, khóa stage cache khác chế độ thường nên kết quả fp32 và fp16 không bao giờ dùng lẫn. Chế độ thường vẫn fp32. Profile `careful` (2 fps) là cấu hình đã đo; profile `fast` (1 fps) dùng cùng model và cùng phép tính, chưa đo riêng. Tooltip Dashboard cập nhật. Có hiệu lực ở lần khởi động Dashboard tiếp theo. 387/387 test.

### 17.7. Xác nhận cả pipeline sau H3 (30/09/2026)

`conan20-allgroups-full-fast-20260930-073627` (Conan Movie 20, đủ nhóm, "Tăng tốc xử lý" hiện tại qua harness; lệnh thật có `--precision fp16`) so với `conan20-allgroups-full-fp16-20260929-193703`:

| Stage | 29/09 (s) | 30/09 (s) |
| --- | ---: | ---: |
| An toàn hoạt hình | 915,3 | **249,0** |
| OCR | 353,0 | 354,1 |
| Logo | 153,4 | 162,0 |
| Localization | 204,9 | 170,2 |
| Review | 10,0 | 8,7 |
| **Tổng** | **1.641,7 (27m22s)** | **950,1 (15m50s), −691,6 s (−42%)** |

- Review: 69/69 mục chính và 393/393 advisory giống hệt; chữ giống hệt (2.230 frame, 468 track, cùng routing); JPEG logo giống hệt.
- Report logo chỉ khác `brand_memory.record_count` 74 → 75 và `revision` (người dùng duyệt watermark Troy lúc 22:44 ngày 29/09 nên brand memory có thêm một bản ghi) cùng số đo thời gian localizer; không có phát hiện nào đổi.
- Report an toàn khác như dự kiến của FP16 (vài hit mức frame, 2 ảnh đại diện lùi 1 frame; §17.5). Dữ liệu production được giữ nguyên (`preserved_original_data: true`).

## 18. Giai đoạn H-live — stage an toàn phim người đóng (kế hoạch 30/09/2026, ghi trước khi đo)

### 18.1. Hiện trạng đo được

- Troy đủ nhóm, "Tăng tốc xử lý" (`troy-allgroups-full-fast-20260930-075806`; lượt bị ngắt ở `confirm_violence` khi phiên làm việc đóng — không phải lỗi pipeline): **adult 630,8 s**, **live_safety 1.848,2 s**. Lần chạy production cũ: confirm_violence 620–714 s. Cộng quảng cáo (~870 s): Troy đủ nhóm ≈ **67 phút**, trong đó ba stage an toàn ≈ 52 phút.
- adult (`scan`, `nsfw_detection_2_nano`, 448 px @ 2 fps, 23.525 frame): `model_step` 529 s (22,5 ms/frame; gồm image processor của transformers trên CPU), `frame_pipe_wait` 49 s. GPU ~5%, CPU 99,7% (ffmpeg giải mã 1080p + tiền xử lý tranh CPU).
- live_safety (`scan-live-safety`, một lần giải mã tách hai nhánh): `violence_model_step` **1.603 s** (94.102 frame @ 8 fps, 17 ms/frame, biến đổi PIL + ViT-B/16 224 FP32 theo cửa sổ 8 frame mới), gore `model_step` 141 s, `frame_pipe_wait` 39 s.
- Profile đoạn trích không tranh CPU (`reports/benchmarks/live-safety-profile/`, Troy 400–550 s): adult processor 1,6 ms + forward 3,1 ms/frame, **fp16 chậm hơn (22 ms)**; violence biến đổi 1,5 ms + forward **9,8 ms fp32 / 3,6 ms fp16** (xác suất lệch tối đa 0,003); gore 3,6 ms/frame tổng. Giải mã đồ thị live-safety bằng CPU: 150 s video trong 4,3 s (~35× thời gian thực → ~340 s cho Troy khi chạy riêng).
- Kết luận: cả phim chậm hơn đo đơn lẻ 2–4,5 lần vì CPU bị ffmpeg chiếm; GPU còn dư.

### 18.2. Các bước (mỗi bước đo trước, giữ nếu qua cổng)

- **L1 — giống hệt từng bit:** live_safety chuẩn bị tensor của cửa sổ bạo lực kế tiếp và batch gore kế tiếp trên luồng nền (cùng frame, cùng transform, cùng ranh giới cửa sổ/batch như vòng lặp tuần tự); adult chuẩn bị đầu vào image processor của batch kế tiếp trên luồng nền. Cổng: mọi report (trừ metrics/runtime/created_at) và JPEG giống hệt byte trên đoạn trích và cả phim Troy; mỗi stage nhanh hơn ≥ 30 s trên cả phim, nếu không bỏ phần đó.
- **L2 — FP16 cho ViT bạo lực (không giống hệt, opt-in `--violence-precision fp16`):** chỉ forward trong autocast, logits về float32 trước softmax, ngưỡng/cửa sổ/top-k giữ nguyên. Gore fp16 chỉ thêm nếu đo thấy lợi; adult không dùng fp16 (đo chậm hơn). Cổng như H2: review dựng lại với cùng report chữ/logo/adult có mọi mục chính và advisory giống hệt trên cả phim Troy; mọi khác biệt liệt kê; stage nhanh hơn ≥ 5 phút. Chỉ đưa vào "Tăng tốc xử lý" khi người dùng duyệt.
- **L3 — thử NVDEC** (đã kiểm chứng khung giống hệt cho H.264 8-bit yuv420p ở OCR/logo) cho adult và/hoặc live_safety để giải phóng CPU; chỉ giữ nếu framemd5 giống hệt với đúng chuỗi filter của stage và nhanh hơn trên cả phim.
- **L4 — cân nhắc sau L1–L3:** gộp nhánh adult vào lần giải mã chung của live_safety (bỏ một lần giải mã cả phim, ~5–10 phút trên Troy). Thay đổi cấu trúc stage (cache, tiến trình Dashboard) nên sẽ lập kế hoạch riêng nếu còn đáng làm.
- **confirm_violence:** đo riêng từ report bạo lực đã có (không cần chạy lại cả pipeline) rồi mới quyết định.
- Ngoài phạm vi: đổi ngưỡng, sampling, model; bỏ nhóm.

### 18.3. Rủi ro và điều kiện dừng

- FP16 lệch xác suất gần ngưỡng bạo lực (0,28, trung bình top-k của 16 frame) → cổng ở mức review trên cả phim.
- Lượt dài chạy tách khỏi phiên làm việc (tiến trình độc lập, log ra file) để không bị ngắt như lượt 30/09.
- Dừng từng bước nếu lệch byte (L1/L3) hoặc làm mất/đổi mục chính (L2).

### 18.4. L1/L2 — mã và kết quả đoạn trích (30/09/2026)

- Mã: `IteratorPrefetch` (`frame_prefetch.py`) chạy bộ sinh đọc/tách khung + biến đổi cửa sổ bạo lực của `scan-live-safety` trên luồng nền, đúng thứ tự tuần tự (mỗi khung: cửa sổ bạo lực nếu đến lượt, rồi khung gore); adult (`scan`) dùng `BatchPrefetch` cho image processor. `scan-live-safety --violence-precision fp32|fp16` (mặc định fp32; chỉ forward ViT bạo lực trong autocast, logits về float32 trước softmax); `runtime.violence_precision` trong report. Harness `scripts/benchmark_live_safety.py` (+ `.ps1`, mutex GPU).
- `reports/benchmarks/live-safety-h/equivalence-20260930-143850` (Troy 400–550 s và 5400–5550 s, đúng tham số pipeline careful): adult và live_safety **giống hệt HEAD từng byte** (report trừ metrics/runtime/created_at; 22 + 20 và 50 + 43 JPEG). live_safety 19,8 → 16,6 / 16,3 s (−16…−18%); adult trên đoạn trích ngắn gần như không đổi (6,2 → 4,9 và 5,2 → 5,3 s — lợi ích kỳ vọng nằm ở cả phim khi CPU bị ffmpeg chiếm).
- L2 fp16: live_safety 16,6 → 9,2 s (−45%); mọi khoảng bạo lực trùng fp32, report gore giống hệt, điểm lớn nhất lệch ≤ 0,0007.
- Lượt cả phim (stage riêng, tiến trình độc lập tạo qua WMI để không bị ngắt theo phiên): adult và live_safety mã mới so byte với report HEAD của lượt 30/09 sáng; live fp16; VLM xác nhận chạy riêng cho bản fp32 và fp16 (khung "mạnh nhất" có thể đổi); review dựng lại với cùng adult/chữ/logo. Thời gian L1 so với HEAD là khác phiên (ghi rõ); L2 so cùng phiên.

### 18.5. Kết quả cả phim Troy — L1 và L2 ĐẠT (30/09/2026)

`reports/benchmarks/live-safety-h/full-20260930-144244` (tiến trình độc lập, một phiên; thời gian là `elapsed_seconds` bên trong scanner):

| Stage | HEAD (sáng 30/09) | L1 | L1 + L2 fp16 |
| --- | ---: | ---: | ---: |
| adult | 596,1 s | **360,5 s (−235,6 s, −40%)** | (không dùng fp16) |
| live_safety | 1.812,8 s | **1.136,0 s (−676,9 s, −37%)** | **581,4 s (−554,6 s so với L1; −68% so với HEAD)** |
| confirm_violence (VLM) | 620–714 s (lượt cũ) | 594,6 s | 596,8 s |

- **L1:** report adult/gore/violence (trừ metrics/runtime/created_at) và 139 + 22 + 578 JPEG **giống hệt HEAD từng byte** trên cả phim. Lợi ích lớn hơn đoạn trích vì cả phim CPU bị ffmpeg chiếm: tiền xử lý không còn chặn luồng GPU (`batch_wait` adult 242,6 s đã chồng lên giải mã; `event_wait` live 0,7 s). So với HEAD là khác phiên.
- **L2:** 558 khoảng bạo lực ứng viên: 552 chỉ khác `max_score` (≤ 5·10⁻⁵), 1 khoảng đổi khung mạnh nhất 5897,875 → 5896,875 s, 1 khoảng đổi điểm đầu 10.187,875 → 10.189,875 s. VLM xác nhận cho kết quả giống hệt ở cả hai bản (88 giữ, 470 loại, cùng tập khoảng giữ lại). Review dựng lại với cùng adult/chữ/logo: **159/159 mục chính và 294/294 advisory giống hệt**. Report gore giống hệt; ảnh bạo lực: 1 ảnh đại diện đổi tên theo khung mạnh nhất.
- Ước tính Troy đủ nhóm ở "Tăng tốc xử lý": ba stage an toàn ~3.010 s → ~1.540 s (nếu áp dụng L2); cả pipeline ~67 → ~42 phút. Stage an toàn lớn nhất còn lại là `confirm_violence` (~595 s cho 558 khoảng, VLM).
- 393/393 test. **L3 (NVDEC) chưa thử**; L1 đã là mặc định vì giống hệt từng bit. **L2 chờ người dùng duyệt** trước khi đưa `--violence-precision fp16` vào "Tăng tốc xử lý".

### 18.6. Áp dụng L2 (30/09/2026)

Người dùng hỏi đánh giá và đồng ý "nếu chất lượng giữ nguyên thì đáng update"; bằng chứng §18.5 cho thấy mục review giữ nguyên. `FAST_SCAN_VIOLENCE_PRECISION = "fp16"`: "Tăng tốc xử lý" thêm `--violence-precision fp16` vào stage `live_safety` (khi chọn cả máu me và bạo lực); adult, `confirm_violence` và đường chỉ-bạo-lực (`scan-content --kind violence`) giữ nguyên; khóa stage cache khác chế độ thường. Chế độ thường vẫn fp32. Tooltip Dashboard cập nhật. Giới hạn: mới kiểm chứng cả phim trên Troy (nguồn Tiếng Yêu không còn). Xác nhận cả pipeline: §18.7.

### 18.7. Xác nhận cả pipeline Troy sau L1 + L2 (30/09/2026)

`troy-allgroups-full-fast-20260930-162534` (Troy, đủ nhóm, "Tăng tốc xử lý" hiện tại, qua harness, tiến trình độc lập):

| Stage | Trước (s) | Sau (s) |
| --- | ---: | ---: |
| Preflight | 20,8 | 17,1 |
| adult | 630,8 | **393,2** |
| live_safety (máu me + bạo lực) | 1.848,2 | **603,3** |
| confirm_violence (VLM) | 620–714 (lượt cũ) | 651,1 |
| OCR | 540,0 | 528,6 |
| Logo | 117,1 | 117,3 |
| Localization | 183,9 | 189,0 |
| Review | 7,3 | 7,2 |
| **Tổng** | **~4.020 (~67 phút; ghép từ lượt 30/09 sáng + lượt quảng cáo 29/09)** | **2.507,0 (41m47s), ~−38%** |

- Hàng đợi review của pipeline **giống hệt** hàng đợi đã kiểm chứng ở §18.5 (cả bản fp16 lẫn fp32): 159/159 mục chính, 294/294 advisory. Report adult/gore/violence và 88 khoảng bạo lực đã xác nhận giống hệt lượt benchmark; dữ liệu production giữ nguyên.
- Stage lớn nhất còn lại của Troy: `confirm_violence` 651 s (558 lần hỏi VLM), OCR 529 s, live_safety 603 s.
