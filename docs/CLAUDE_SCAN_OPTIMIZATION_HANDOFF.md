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
