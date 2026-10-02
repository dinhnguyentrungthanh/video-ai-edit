# BiliFlow — Kế hoạch chất lượng: đo được, rồi mới nhanh hơn, mạnh hơn, chính xác hơn

Ngày lập: 29/09/2026, sửa lần 1 cùng ngày sau tự rà soát (mục 11). Trạng thái: **đã duyệt 29/09/2026** — bắt đầu với 3 nguồn hiện có, video mới kiểm chứng sau. Kế hoạch thi công Q0–Q1 và các điều chỉnh theo dữ liệu thật (trước khi có bất kỳ số đo nào) ở mục 12. Mốc code: `f54cc09` trên nhánh `improve/scan-performance-metrics`.

## 0. Tóm tắt một trang

Mọi cải tiến tốc độ từ 25m32s xuống 14m27s (Troy, quét quảng cáo) đều được chứng minh bằng một tiêu chí duy nhất: *kết quả giống lượt trước*. Tiêu chí đó bảo vệ được hành vi hiện có nhưng **không nói gì về việc BiliFlow bắt đúng và đủ bao nhiêu phần trăm nội dung thật**. Nếu bản trước bỏ sót một watermark, bản sau bỏ sót y hệt và mọi phép thử vẫn PASS.

Kế hoạch này xây **Bộ nhãn chuẩn (Golden Set)** từ nhãn do người gán trên các đoạn phim thật, rồi dùng nó làm thước đo cố định cho ba việc:

| Mục tiêu | Thước đo | Ý nghĩa với người dùng |
| --- | --- | --- |
| **Chính xác hơn** | Recall và precision theo từng nhóm (logo/watermark, quảng cáo chữ, 18+, máu me, bạo lực), độ chính xác vùng blur | Ít nội dung lọt ra video xuất; ít mục báo nhầm |
| **Mạnh hơn** | Tỉ lệ bắt được trên từng loại nội dung khó (hoạt hình, banner chạy, chèn ngắn, logo mờ) | Detector tốt đều trên nhiều thể loại, không chỉ Troy |
| **Nhanh hơn** | Mỗi thay đổi tốc độ *không giống hệt từng bit* (TensorRT, batch VLM, lấy mẫu thích ứng) được chấp nhận khi điểm Golden Set không giảm | Mở khóa các hướng tăng tốc còn lại mà không cần duyệt tay từng lần |

Chi phí người dùng: **khoảng 2,5–3,5 giờ gán nhãn** một lần (phải xem hết từng đoạn mới tìm được cái bị sót) cho 13 đoạn (~64 phút video), có trang gán nhãn điền sẵn gợi ý, làm trên máy tính, chia nhiều buổi được. Sau đó mọi đánh giá chạy tự động.

Nguyên tắc không đổi: detector chỉ tạo ứng viên; con người duyệt; không tự xuất video; nhãn là dữ liệu đánh giá, không phải quyết định chỉnh sửa.

## 1. Hiện trạng đo lường

| Đã có | Đo được gì | Thiếu gì |
| --- | --- | --- |
| `annotations/review-regression-v1.json`: 206 quyết định duyệt cuối (KEEP 163, CUT 29, BLUR 14) trên 6 nguồn; logo 97, chữ 42, bạo lực 23, 18+ 23, máu me 21 | Detector **đề xuất đúng hay sai** trên những gì nó đã bắt (precision) | **Không đo được bỏ sót** (recall): nhãn chỉ tồn tại cho mục detector đã tạo |
| `annotations/*_poc_benchmark.csv`, `violence_*_benchmark*.csv`, Sintel | Điểm ảnh/clip cho model an toàn ở mức thành phần | Không nối được với pipeline thật (sampling, tracking, review) |
| `annotations/content_benchmark.csv` | Có schema tốt (start/end, category, label, severity, split, review_decision) | **0 dòng** |
| So sánh "giống baseline" (`benchmark_full_advertising.py`, `review-diff`) | Bảo toàn hành vi khi tối ưu | Không biết baseline đúng bao nhiêu |
| Ca hồi quy Conan 20 (watermark phimmoi đúng, không blur đầu nhân vật) | Một lỗi cụ thể không quay lại | Chỉ 1 ca |

Kết luận: mảnh ghép còn thiếu là **nhãn đầy đủ cho một đoạn video** ("trong 5 phút này có đúng những gì"), không phải thêm điểm ảnh rời rạc.

## 2. Nguyên tắc

1. **Nhãn là sự thật do người quyết**, ghi cùng thời điểm, người gán và ghi chú; không sinh nhãn tự động rồi coi là chuẩn.
2. **Cổng ghi trước khi đo** (như đã làm với FP16): tiêu chí đạt/không đạt nằm trong tài liệu trước khi chạy đánh giá.
3. **Tách bộ phát triển và bộ kiểm tra**: điều chỉnh ngưỡng chỉ trên `dev`; báo cáo cuối trên `holdout` chưa dùng để chỉnh.
4. **Cách ly**: đánh giá chạy với state/cache/report riêng dưới `reports/benchmarks/`, không ghi vào job, queue, brand-memory production.
5. **Không đổi ngưỡng, sampling, model để "đẹp số"** nếu chưa có bằng chứng trên Golden Set và chưa ghi rõ đánh đổi.
6. Mọi model vẫn local, miễn phí, giấy phép thương mại; không API trả phí.

## 3. Thiết kế Golden Set v1

### 3.1. Nguồn và đoạn

Chỉ **ba nguồn còn tồn tại** trong `input/`: Troy (live action, job #39), Conan Movie 20 (#38) và Conan Movie 21 (#37), đều H.264 1920×1080 + AAC nên trình duyệt phát trực tiếp được. Nguồn Shin (job #1–4), Tiếng Yêu (#5–6) và Conan 3 **không còn file gốc**. SHA-256 nguồn ghi vào manifest trước khi gán nhãn.

Danh sách đoạn chốt theo **quyết định duyệt thật** đã có trong các revision cũ (mục 12.1), cộng đoạn đối chứng:

| ID | Nguồn | Đoạn (giây) | Vì sao chọn (dữ liệu thật) | Tập |
| --- | --- | --- | --- | --- |
| T1 | Troy | 0–300 | Watermark XEMBZ từ 0 s; mở đầu 5–25 s (đề xuất CUT); chữ tường thuật 51–88 s (bẫy báo nhầm) | dev |
| T2 | Troy | 300–600 | 18+ bạn đã BLUR toàn khung 421–460 s; watermark | holdout |
| T3 | Troy | 2000–2300 | **Đối chứng**: kỳ vọng chỉ có watermark | holdout |
| T4 | Troy | 11400–11763 | End credits, biến thể watermark, cuối phim | dev |
| C20A | Conan 20 | 0–240 | Watermark phimmoi; mở đầu 5–10 s (bạn chọn KEEP) | dev |
| C20B | Conan 20 | 480–800 | **Ca hồi quy** đầu nhân vật 510 s; watermark đổi vị trí từ 775 s; chữ OCR 642 s | holdout |
| C20C | Conan 20 | 1300–1600 | Ứng viên logo nhỏ 1315 s, "website banner" 1490 s, chữ trong cảnh 1500 s | dev |
| C20D | Conan 20 | 3300–3600 | Cụm máu me mức cao (4 mục) | holdout |
| C20E | Conan 20 | 6400–6690 | Kết phim, credits | dev |
| C21A | Conan 21 | 0–240 | Mở đầu 0–15 s bạn từng chọn CUT; watermark; máu me bạn chọn KEEP | holdout |
| C21B | Conan 21 | 1300–1600 | Mục máu me 1436 s bạn đổi CUT → KEEP (ca khó) | dev |
| C21C | Conan 21 | 4200–4500 | Chữ Nhật trong cảnh (karuta) từng bị báo là watermark | dev |
| C21D | Conan 21 | 6450–6738 | End-card 6715 s (đề xuất BLUR), end-card 6730 s (bạn chọn KEEP) | holdout |

Tổng 3841 s ≈ **64 phút**: 7 đoạn `dev`, 6 đoạn `holdout`. Mọi nhóm nội dung được gán nhãn trên mọi đoạn (mục 3.2); nhóm nào được chấm tùy lượt đánh giá có chạy nhóm detector đó hay không.

**Giới hạn cần biết:** 3 nguồn, 2 cùng loạt Conan; đoạn được chọn một phần dựa trên chỗ detector từng báo (có thiên lệch), T3 là đối chứng. Video mới (loại khác) sẽ được thêm thành các đoạn tiếp theo ở v1.1 sau khi kiểm chứng xong bộ này.

### 3.2. Đơn vị nhãn: **sự kiện**, không phải frame

Mỗi nhãn là một khoảng thời gian có ý nghĩa với người duyệt:

```json
{
  "id": "gs-troy-0001",
  "source_sha256": "f43cf94a…",
  "segment": 1,
  "category": "visual_logo | text_ad | adult | gore | violence",
  "start_seconds": 0.0,
  "end_seconds": 11762.7,
  "region_source_pixels": {"x": 118, "y": 173, "width": 150, "height": 31},
  "expected_action": "BLUR | CUT | KEEP",
  "severity": "must_catch | should_catch | nice_to_have",
  "ambiguous": false,
  "notes": "watermark XEMBZ góc trên trái, xuất hiện suốt phim",
  "labeled_by": "user", "labeled_at": "2026-09-30T…"
}
```

- **Gán mọi nhóm trên mọi đoạn**: mọi thứ bạn sẽ BLUR hoặc CUT, dù nhóm detector đó có chạy hay không. Phần không có nhãn trong một đoạn đã "xem hết" được hiểu là KEEP.
- Nhãn `KEEP` chỉ dùng cho **bẫy đã biết** (ví dụ đầu nhân vật 510 s Conan 20): mục review nào đề xuất BLUR/CUT trúng bẫy bị ghi thành lỗi có tên.
- Mỗi đoạn có trạng thái `chưa gán | đang gán | đã xem hết`; **chỉ đoạn "đã xem hết" mới được chấm** (recall cần xem trọn đoạn).
- Nhóm chấm điểm: `visual_logo` và `text` cùng thuộc nhóm **quảng cáo** (watermark có thể được bắt bằng OCR hoặc logo); `adult`, `gore`, `violence` là ba nhóm riêng — khớp với nhóm detector trên Dashboard.
- `must_catch`: lọt ra video là lỗi nghiêm trọng (watermark, quảng cáo chèn, cảnh 18+ rõ). `nice_to_have`: chữ credits, mục tham khảo.
- `ambiguous: true` khi chính người gán cũng do dự; mục này **không tính** vào recall/precision, chỉ báo cáo riêng.
- Vùng chỉ bắt buộc với logo/watermark/chữ cần BLUR.
- Sự kiện kéo dài ra ngoài đoạn (ví dụ watermark suốt phim) được **cắt theo biên đoạn** khi chấm điểm; nhãn vẫn ghi khoảng thật.
- Lưu ở `annotations/golden/v1/segments.json` (manifest đoạn + hash nguồn) và `annotations/golden/v1/events.json`. Không chứa media.
- Đã có schema CSV `annotations/content_benchmark.csv` (0 dòng) cho benchmark thành phần; Golden Set dùng JSON vì cần vùng pixel và mức nghiêm trọng, và có bộ chuyển đổi sang CSV khi cần.

### 3.3. Cách gán nhãn nhanh (không bắt đầu từ trang trắng)

1. Chạy **quét dày** trên từng đoạn với cache riêng: profile `careful`, logo `--exhaustive` (mọi cửa sổ qua VLM), chữ lấy mẫu 1 s; nhóm an toàn lấy từ kết quả cả phim sẵn có (mục 12.4). Mục đích là **tối đa ứng viên**, chấp nhận nhiều nhiễu, để gợi ý phủ càng nhiều càng tốt.
2. Trang gán nhãn cục bộ (`scripts/golden_label_server.py`, mở trong trình duyệt như Dashboard) hiển thị từng ứng viên với thumbnail/thời gian/vùng; người dùng bấm **Đúng / Sai / Sửa vùng / Mơ hồ**, và có nút **Thêm sự kiện bị sót** với thanh tua thời gian + khung frame (dùng FFmpeg trích frame như localization).
3. Với đoạn đã có quyết định duyệt (Troy #39, Conan 20 #38, Conan 21 #37 — mọi revision), điền sẵn quyết định cũ làm gợi ý; người dùng chỉ xác nhận.
4. Mỗi nhãn ghi kèm ai/khi nào; xuất ra JSON ở mục 3.2. Hash nguồn ghi vào manifest để phát hiện file đổi.

Thời gian ước tính: xem hết ~64 phút video + xác nhận gợi ý + thêm cái bị sót ≈ **2,5–3,5 giờ**, chia nhiều buổi. Trang gán nhãn chạy trên máy tính (localhost như Dashboard), không mở ra mạng ngoài, nên không làm được qua điện thoại từ xa.

## 4. Chỉ số và cách tính

Khớp giữa nhãn và mục review theo **nhóm + thời gian + vùng** (cả nhãn và mục đều được cắt theo biên đoạn trước khi so):

- **Loại mục review:** có hộp → *vùng*; mục an toàn (18+/máu me/bạo lực) không hộp → *toàn khung*; mục quảng cáo không hộp → *toàn khung* nếu đề xuất CUT hoặc là mở đầu/end-card, còn lại (logo bị VLM từ chối) → *không rõ vị trí*, không khớp nhãn nào.
- **Tương thích vùng:** hộp–hộp: phần giao ≥ 30% diện tích hộp nhỏ hơn. Nhãn toàn khung (không vẽ hộp) chỉ khớp mục toàn khung hoặc hộp phủ ≥ 50% khung hình. Nhãn có hộp khớp mục an toàn toàn khung, nhưng **không** khớp mục quảng cáo toàn khung (một đề xuất CUT cả cảnh không phải bằng chứng cho logo ở góc). Vì vậy watermark suốt phim không thể "bắt hộ" nhãn CUT mở đầu hay chạm bẫy KEEP toàn khung.
- **Bắt được (recall):** hợp thời gian của mọi mục cùng nhóm, tương thích vùng, phủ **≥ 80%** thời lượng nhãn. Phủ 0–80% ghi là **bắt một phần** (không tính là bắt được, liệt kê riêng); 0% là **bỏ sót**.
- **Vùng đạt** (nhãn BLUR có vùng, đã bắt được): mục phủ nhãn tốt nhất **theo thời gian** (ưu tiên mục chính) phải che **≥ 85%** hộp nhãn và không rộng quá **4 lần** hộp nhãn. Hộp nhãn vẽ **sát** logo/chữ. Mục không có vùng thì vùng không đạt.
- Mục `advisory` tính là **đã bắt** nhưng ghi riêng "chỉ bắt ở advisory" (người duyệt dễ bỏ qua).
- **Mục hữu ích (precision):** mục chính có ≥ 50% thời lượng (đã cắt theo đoạn) nằm trong các nhãn BLUR/CUT cùng nhóm và tương thích vùng. Mục **chỉ** chạm nhãn `ambiguous` (không chạm nhãn BLUR/CUT nào) không tính vào mẫu số; mục chạm cả hai mà không đủ 50% vẫn là báo nhầm.

Báo cáo (`reports/benchmarks/golden-<date>/scorecard.md` + `.json`):

| Chỉ số | Định nghĩa | Vì sao quan trọng |
| --- | --- | --- |
| Recall `must_catch` theo nhóm | nhãn must_catch được khớp / tổng | Rủi ro lọt nội dung khi xuất |
| Recall tổng theo nhóm | như trên, mọi severity | Sức bắt chung |
| Precision mục chính | số mục chính khớp một nhãn có hành động BLUR hoặc CUT / tổng mục chính | Gánh nặng duyệt tay |
| Tỉ lệ nhiễu advisory | số advisory không khớp nhãn nào / tổng advisory | Mục có thể hạ ưu tiên hiển thị |
| Vùng đạt | nhãn BLUR có vùng đã bắt được và vùng đạt / nhãn BLUR có vùng đã bắt được | Chất lượng blur |
| Trúng bẫy | nhãn KEEP bị mục đề xuất BLUR/CUT chạm vào (liệt kê tên) | Hồi quy đã biết |
| Đề xuất đúng hành động | nhãn đã bắt mà mục phủ tốt nhất đề xuất đúng BLUR/CUT / nhãn đã bắt | Người duyệt đỡ phải sửa |
| Tải duyệt | mục chính + advisory trên mỗi giờ phim | Thời gian của người dùng |
| Thời gian pipeline | như hiện nay (per stage) | Giữ tốc độ khi tăng chất lượng |
| Danh sách bỏ sót | từng nhãn không khớp, kèm thumbnail | Nguồn việc cho cải tiến |

Điểm tổng hợp không dùng một con số duy nhất; **cổng luôn đặt trên recall `must_catch` trước**, rồi precision.

## 5. Bộ đánh giá tự động

`scripts/evaluate_golden.py` (mới):

1. Đọc manifest; kiểm tra hash nguồn; từ chối chạy nếu file đổi.
2. **Chạy cả phim, chấm theo đoạn**: chạy pipeline thật cho cả ba nguồn qua harness hiện có (state/cache riêng, `.biliflow-benchmark`), đúng profile và `fast_scan` như production; Conan bật thêm nhóm an toàn. Chỉ các mục review rơi vào đoạn có nhãn mới được chấm. Lý do: các scanner an toàn không có `--start/--duration`, và quét riêng một đoạn làm thay đổi tracking/nhận diện overlay lâu dài (ví dụ watermark suốt phim), nên kết quả không giống production.
3. Ghép mục review với nhãn, tính bảng ở mục 4, xuất scorecard Markdown tiếng Việt và JSON, kèm thumbnail cho từng bỏ sót/báo nhầm.
4. So với scorecard mốc gần nhất; in bảng **khác biệt** (nhãn nào mới bắt được / mới bị sót).
5. Mã thoát ≠ 0 nếu vi phạm cổng ở mục 7 — dùng được như test hồi quy trước khi commit thay đổi detector.

Ước tính một lượt đánh giá đầy đủ (dùng làm cổng): Troy quảng cáo ~15 phút + Conan 20 và Conan 21 đủ nhóm ~27 phút mỗi phim ≈ **70 phút máy**. Nhãn 18+ của Troy chỉ được chấm khi lượt đánh giá bật nhóm 18+ cho Troy (tùy chọn, tốn thêm máy); mặc định scorecard ghi "chưa chấm". Chế độ nhanh chỉ để thử nghiệm trong lúc phát triển: chỉ chạy stage chữ/logo trên từng đoạn bằng `--start/--duration` (~10 phút); kết quả chế độ nhanh **không dùng làm cổng** vì khác hành vi production.

## 6. Lộ trình

| Giai đoạn | Việc | Đầu ra | Ai |
| --- | --- | --- | --- |
| **Q0** (0,5 ngày) | Duyệt kế hoạch; chốt danh sách đoạn; kiểm tra nguồn còn tồn tại | Manifest v1 với hash | Người dùng + Claude |
| **Q1** (1 ngày máy + 2,5–3,5 giờ người) | Quét dày điền sẵn; trang gán nhãn; gán nhãn | `annotations/golden/v1/*.json` | Claude dựng, người dùng gán |
| **Q2** (0,5 ngày) | `evaluate_golden.py`; chạy mốc trên code hiện tại | **Scorecard mốc** — lần đầu biết recall/precision thật | Claude |
| **Q3** (lặp) | Sửa detector theo danh sách bỏ sót, ưu tiên `must_catch`; mỗi sửa một PR nhỏ có scorecard trước/sau | Recall tăng, precision không giảm | Claude, người dùng duyệt |
| **Q4** (song song) | Mở lại các hướng tốc độ không giống hệt từng bit: TensorRT CRAFT, batch VLM/Florence, lấy mẫu thích ứng theo cảnh; mỗi hướng qua cổng Golden | Nhanh hơn với chất lượng chứng minh | Claude |
| **Q5** (liên tục) | Mỗi quyết định duyệt thật trên Dashboard được `build_review_regression` gom thành regression v2; chạy Golden trước mỗi commit detector | Không lỗi quay lại | Tự động |

Ứng viên Q3 dựa trên những gì đã thấy (chỉ là giả thuyết cho tới khi có scorecard):

- Watermark mờ/nhỏ trên hoạt hình sau chuyển cảnh (routing dựa cạnh + màu nền).
- Quảng cáo chèn ngắn (< 5 s) rơi giữa hai mẫu lấy 2–3 s; lấy mẫu dày quanh điểm chuyển cảnh mà không tăng tổng số frame nhiều.
- Chữ banner chạy ngang bị tách track (`_text_continuity`).
- Nhiễu advisory: Troy có 294 advisory cho 6 mục chính; xem nhóm nào chưa từng được người duyệt chuyển thành BLUR/CUT để hạ ưu tiên hiển thị (không xóa).

## 7. Cổng (ghi trước, không nới sau khi thấy số)

- **Q2 chỉ là mốc**: không có cổng; ghi số thật, kể cả xấu.
- **Mỗi thay đổi detector (Q3)**: recall `must_catch` mỗi nhóm không giảm; precision mục chính không giảm quá 2 điểm; không thêm bỏ sót `must_catch` mới trên `holdout`; ca hồi quy Conan 20 giữ nguyên; thời gian pipeline không tăng quá 5% trừ khi được duyệt.
- **Mỗi thay đổi tốc độ (Q4)**: recall và precision mọi nhóm bằng mốc trong sai số ±1 mục trên `holdout`; danh sách bỏ sót `must_catch` không đổi; báo cáo mọi mục khác đi để người dùng xem như đã làm với FP16.
- **Dừng hướng nào** khi hai lần thử liên tiếp không qua cổng; ghi bằng chứng, giữ code chỉ trong `reports/benchmarks`.

## 8. Rủi ro và cách xử lý

| Rủi ro | Xử lý |
| --- | --- |
| Nhãn thiên vị theo gợi ý của detector (chỉ xác nhận cái đã bắt) | Quét dày `exhaustive` để gợi ý rộng hơn production; bắt buộc bước "tua xem lại đoạn" với nút thêm sự kiện; đoạn âm tính để đo báo nhầm |
| Bộ quá nhỏ, số liệu dao động | Báo số đếm tuyệt đối kèm phần trăm; cổng theo "không thêm bỏ sót" thay vì phần trăm nhỏ; mở rộng v2 từ quyết định duyệt thật |
| Nguồn video thay đổi/mất | Hash trong manifest; evaluate từ chối chạy nếu lệch |
| Chỉnh ngưỡng quá khớp bộ dev | Holdout tách riêng, chỉ báo cáo, không dùng để chỉnh |
| Tốn máy | Lượt cổng ~70 phút máy; chế độ nhanh ~10 phút khi phát triển; state riêng, mutex GPU hiện có; không chạy khi Dashboard đang xử lý job |
| Bộ nhãn ít đa dạng (3 nguồn, 2 cùng loạt Conan) | Ghi rõ giới hạn trong mọi scorecard; mời người dùng bổ sung 1–2 nguồn tiêu biểu; v2 mở rộng từ quyết định duyệt thật |
| An toàn live action chưa có nhãn | Ngoài phạm vi v1; mọi báo cáo ghi rõ; v2 khi có nguồn live action chứa nội dung an toàn |
| Nhãn không nhất quán (một người gán, mệt) | Chia nhiều buổi; gán lại một đoạn sau vài ngày (~10 phút) để ước lượng độ nhất quán; mục do dự đánh dấu `ambiguous` |
| Nhãn nhạy cảm (18+/máu me) | Chỉ lưu thời gian/vùng/ghi chú, không lưu ảnh trong `annotations/`; thumbnail chỉ nằm trong `reports/benchmarks` cục bộ |

## 9. Việc người dùng cần làm

1. Duyệt danh sách đoạn ở 3.1 (thêm/bớt nguồn bạn quan tâm; ưu tiên loại video bạn xử lý nhiều nhất).
2. Dành 2,5–3,5 giờ gán nhãn trên máy tính (có thể chia nhiều buổi); nếu muốn bộ nhãn đại diện hơn, chép thêm 1–2 video tiêu biểu vào `input/` trước khi bắt đầu.
3. Xem scorecard mốc và chọn thứ tự ưu tiên cho Q3.

## 10. Việc Claude làm ngay sau khi được duyệt

1. Kiểm tra nguồn và hash; tạo `annotations/golden/v1/segments.json`.
2. Viết `scripts/golden_prefill.py` (quét dày, cache riêng) và `scripts/golden_label_server.py` (trang gán nhãn cục bộ), kèm test.
3. Viết `scripts/evaluate_golden.py` + scorecard, kèm test trên dữ liệu giả.
4. Chạy quét dày cho 13 đoạn (ước tính 40–60 phút máy), báo khi trang gán nhãn sẵn sàng.
5. Sau khi có nhãn: chạy scorecard mốc, trình bày kết quả và đề xuất Q3 theo số liệu thật.

Mọi mã và tài liệu đi kèm sẽ được cập nhật vào `CHANGELOG.md`, `docs/PROJECT_STATUS.md`, `docs/SESSION_HANDOFF.md` sau mỗi mốc được kiểm chứng; không commit/merge/push khi chưa được yêu cầu.

## 11. Tự rà soát lần 1 (29/09/2026) — những gì đã sửa

1. **Nguồn không tồn tại:** Shin, Tiếng Yêu và nguồn gốc của clip Conan 3 không còn trong máy; danh sách đoạn cũ dựa vào chúng. Đã thay bằng 12 đoạn chỉ từ Troy, Conan 20, Conan 21 và ghi rõ giới hạn đa dạng.
2. **Không quét được từng đoạn cho nhóm an toàn:** `scan-animation-safety`, `scan-live-safety`, `scan-content`, `scan` không có `--start/--duration`. Đổi sang "chạy cả phim, chấm theo đoạn" cho lượt cổng; chế độ nhanh theo đoạn chỉ dùng khi phát triển.
3. **Ước lượng thời gian quá lạc quan:** muốn đo bỏ sót phải xem hết từng đoạn; sửa thành 2,5–3,5 giờ gán nhãn và ~70 phút máy cho mỗi lượt cổng.
4. **Định nghĩa precision mơ hồ:** viết lại; thêm tỉ lệ nhiễu advisory.
5. **Phạm vi an toàn live action:** ghi rõ nằm ngoài v1; thêm quy tắc cắt sự kiện dài theo biên đoạn, rủi ro nhãn không nhất quán, và việc trang gán nhãn chỉ dùng được trên máy tính.

## 12. Kế hoạch thi công Q0–Q1 (sau khi duyệt, 29/09/2026)

### 12.1. Dữ liệu thật tìm thấy khi rà soát (trước khi đo)

- **Troy #39 revision 1** (quảng cáo + 18+ + bạo lực): 294 mục do bạn quyết định trên Dashboard (`control_center_user`) — 66 BLUR, trong đó 11 cảnh 18+ BLUR toàn khung (421–460 s, 983–1004 s, 3257 s, 6800–6861 s, 7266 s) và watermark XEMBZ ở nhiều cửa sổ. Revision 3 hiện tại chỉ chạy quảng cáo: 6 mục chính, chưa duyệt; hộp watermark mới (118,173,150×31) chặt hơn hộp bạn đã duyệt trước đây (105,160,174×54).
- **Conan 21 #37 revision 2–3** (đủ nhóm): bạn giữ KEEP gần như mọi mục máu me/bạo lực (79/80 ở revision 3); từng chọn CUT cho mở đầu 0–15 s và máu me 1436 s rồi đổi lại. Nghĩa là trên hoạt hình này, nhóm an toàn chủ yếu tạo **gánh nặng duyệt** — Golden Set sẽ đo được điều đó.
- **Conan 20 #38**: các revision chỉ chạy quảng cáo; có lượt benchmark đủ nhóm ngày 29/09 (69 mục chính, 393 advisory) để làm gợi ý.
- `annotations/` và `reports/` nằm trong `.gitignore`: nhãn **không** vào git, nên phải có sao lưu cục bộ (12.3).

Điều chỉnh so với bản đã duyệt (chưa có số đo nào, nên không vi phạm nguyên tắc "cổng ghi trước"): danh sách đoạn 3.1 chốt theo dữ liệu trên (13 đoạn, ~64 phút); gán mọi nhóm trên mọi đoạn; quy tắc khớp dùng **độ phủ hợp thời gian ≥ 80%** thay cho IoU ≥ 0,3 (IoU phạt oan mục dài phủ sự kiện ngắn và không phản ánh việc blur có che hết khoảng thời gian hay không); vùng đạt dùng độ che ≥ 85% + không rộng quá 4 lần thay cho IoU ≥ 0,5 (hộp watermark chặt vs hộp rộng cho IoU 0,49 dù cả hai đều che chữ).

### 12.2. Thành phần mã

| Tệp | Vai trò |
| --- | --- |
| `src/biliflow/golden_set.py` | Schema manifest/nhãn, kiểm tra hợp lệ, kho nhãn (ghi nguyên tử, lịch sử append-only, sao lưu), chuyển mục review thành gợi ý |
| `src/biliflow/golden_scoring.py` | Cắt theo đoạn, khớp nhãn–mục, chỉ số mục 4, so sánh hai scorecard, cổng mục 7 |
| `src/biliflow/golden_label_app.py` | Máy chủ gán nhãn `127.0.0.1:8766` + trang HTML tiếng Việt |
| `scripts/golden_prefill.py` | `manifest` (kiểm tra nguồn + SHA-256), `collect` (gợi ý từ review cũ), `dense` (quét dày, cache riêng) |
| `scripts/golden_label_server.py` + `scripts/golden-label.ps1` | Khởi động trang gán nhãn |
| `scripts/evaluate_golden.py` | `score` (chấm một bộ review queue), `run` (chạy cả phim qua harness rồi chấm), `compare` (khác biệt + cổng) |
| `scripts/benchmark_full_advertising.py` | Thêm Conan 21 (#37) vào danh sách job được phép chạy thử; thêm nhãn lượt `golden` |

### 12.3. Dữ liệu và an toàn

- `annotations/golden/v1/segments.json`: 13 đoạn + đường dẫn, kích thước, SHA-256 nguồn. Tạo một lần; lệnh `manifest` từ chối ghi đè nếu nội dung khác.
- `annotations/golden/v1/events.json`: nhãn, trạng thái từng đoạn, cách xử lý từng gợi ý; có số `revision` tăng dần để chặn ghi đè từ tab cũ.
- `annotations/golden/v1/label-history.jsonl`: mỗi thay đổi (thêm/sửa/xóa nhãn, đổi trạng thái đoạn) ghi kèm bản trước và sau; xóa nhãn trên trang vẫn còn dấu vết ở đây.
- `annotations/golden/v1/backups/events-<thời điểm>.json`: bản sao mỗi lần mở máy chủ (vài KB mỗi bản, không tự xóa).
- Gợi ý, ảnh frame, kết quả quét dày: `reports/benchmarks/golden-v1/` (ảnh 18+ chỉ nằm đây, không vào `annotations/`).
- Một khóa `events.lock` chặn tiến trình thứ hai ghi cùng bộ nhãn; mỗi thay đổi được dựng trên bản sao và chỉ thành trạng thái hiện tại sau khi ghi đĩa thành công.
- Máy chủ chỉ đọc 3 file nguồn trong manifest (phát video bằng HTTP Range), không ghi vào `input/`, job, queue, brand memory hay SQLite production. Chỉ nghe `127.0.0.1`; mọi lệnh ghi cần token phiên như Dashboard.

### 12.4. Gợi ý điền sẵn (`golden_prefill.py`)

1. `manifest`: đối chiếu đường dẫn/kích thước/SHA-256 với SQLite (chỉ đọc) và tính lại SHA-256 từ file.
2. `collect` (không tốn GPU): lấy mục chính + advisory từ **mọi revision** của job #37/#38/#39 và các lượt benchmark đủ nhóm gần nhất; cắt theo đoạn; gộp mục trùng (cùng nhóm, trùng thời gian ≥ 50%, trùng vùng ≥ 50%); giữ quyết định cũ của bạn làm gợi ý mặc định ("trước đây bạn chọn BLUR").
3. `dense` (GPU, ~40–60 phút, cache riêng qua `BILIFLOW_BENCHMARK_CACHE`): trên từng đoạn chạy `scan-text` lấy mẫu 1 s và `scan-visual-logo --exhaustive`; mọi cửa sổ logo được VLM xác nhận thành gợi ý mức thấp; track chữ thành gợi ý khi có dấu hiệu quảng cáo (policy, ứng viên review, nhãn ngữ nghĩa "advertisement"), hoặc khi không phải credits/tiêu đề và được định tuyến REVIEW_*/LOW_AD*, cố định, hay có xác suất quảng cáo ≥ 0,10. Credits cuộn/tiêu đề và chữ trong cảnh/phụ đề không cố định dưới 0,10 bị bỏ để tránh hàng trăm gợi ý tên người (T4 từ 136 xuống 12). Nhóm an toàn không có `--start/--duration` nên dùng kết quả cả phim sẵn có; bạn vẫn phải xem hết đoạn để tìm cảnh bị sót.

### 12.5. Trang gán nhãn

- Danh sách 13 đoạn (trạng thái, số nhãn, số gợi ý chưa xử lý); trình phát video nhảy đúng đầu đoạn, tốc độ 1×/1,5×/2×, phím tắt (Space, ←/→ 1 s, Shift ±5 s, `[`/`]` đặt đầu/cuối).
- Dải thời gian của đoạn hiển thị nhãn và gợi ý; bấm để nhảy tới.
- Mỗi gợi ý: ảnh frame có khung vùng, thời gian, nguồn, quyết định cũ; nút **Đúng (BLUR/CUT)**, **Sai**, **Mơ hồ**, **Sửa rồi lưu**.
- **Thêm sự kiện bị sót**: đặt đầu/cuối từ vị trí đang phát, chọn nhóm/hành động/mức độ, kéo chuột trên khung hình đang dừng để vẽ vùng (tự đổi sang pixel gốc 1920×1080), ghi chú.
- Nút **"Đã xem hết đoạn"** chỉ bật khi mọi gợi ý của đoạn đã xử lý; lưu thời điểm hoàn tất.

### 12.6. Bộ chấm (`evaluate_golden.py`)

- `score --queue <review-queue.json>...`: nhận review queue của từng nguồn (nhận diện nguồn bằng SHA-256), chỉ chấm đoạn "đã xem hết", chỉ chấm nhóm nằm trong `detection_scope` của queue; xuất `scorecard.json` + `scorecard.md` tiếng Việt và danh sách bỏ sót/báo nhầm kèm ảnh frame vào `reports/benchmarks/golden-<thời điểm>/`.
- `run`: chạy harness cho Troy (quảng cáo) và Conan 20/21 (đủ nhóm) đúng cấu hình production (`fast_scan` bật), state/cache riêng, rồi `score`.
- `compare --baseline --candidate [--gate detector|speed]`: bảng khác biệt; mã thoát ≠ 0 khi vi phạm cổng mục 7.

### 12.7. Kiểm thử và tiêu chí xong Q1

- Test đơn vị (unittest, dữ liệu giả): schema và kiểm tra nhãn; kho nhãn (ghi nguyên tử, lịch sử, chặn revision cũ, không cho "đã xem hết" khi còn gợi ý chưa xử lý); gợi ý từ queue giả (cắt đoạn, gộp, giữ quyết định cũ); quy tắc khớp (phủ hợp, bắt một phần, tương thích vùng, vùng đạt, bẫy, `ambiguous`, nhóm ngoài phạm vi); chỉ số và cổng; máy chủ (403 khi thiếu token, Range trả 206 đúng byte, không phục vụ file ngoài manifest).
- Chạy thử thật: `manifest` khớp SHA-256 cả 3 nguồn; `collect` ra gợi ý cho cả 13 đoạn; `dense` chạy xong; mở trang gán nhãn, phát video và lưu/xóa một nhãn thử trong bản sao tạm rồi bỏ bản sao; `score` chạy được trên queue benchmark sẵn có với bộ nhãn giả (không lưu vào `annotations/`).
- Toàn bộ test suite xanh; cập nhật `CHANGELOG.md`, `docs/PROJECT_STATUS.md`, `docs/SESSION_HANDOFF.md`. Không commit tới khi bạn yêu cầu.

### 12.8. Rủi ro riêng của Q1

| Rủi ro | Xử lý |
| --- | --- |
| Mất nhãn (không có git) | Ghi nguyên tử + lịch sử append-only + sao lưu mỗi lần mở máy chủ |
| Video 7,6 GB phát chậm/tua lâu | HTTP Range theo khối; nếu trình duyệt tua quá chậm thì tạo bản xem trước 720p cho từng đoạn (chỉ để xem, thời gian quy đổi theo đầu đoạn) |
| Tọa độ vùng lệch do trình duyệt co giãn | Quy đổi theo `videoWidth/videoHeight` thực; test quy đổi; hiển thị lại vùng đã lưu trên frame gốc do FFmpeg trích |
| Quét dày chiếm GPU khi bạn đang dùng Dashboard | Dùng mutex GPU sẵn có; chạy khi không có job; có thể bỏ qua `dense` (gợi ý từ review cũ vẫn đủ để bắt đầu) |

## 13. Hướng dẫn gán nhãn (dành cho người dùng, trên máy tính)

1. Chạy `.\scripts\golden-label.ps1` (hoặc `.\.venv\Scripts\python.exe scripts\golden_label_server.py`); trình duyệt mở http://127.0.0.1:8766. Không cần tắt Dashboard. Đóng cửa sổ lệnh (Ctrl+C) là dừng; nhãn đã lưu ngay sau mỗi lần bấm.
2. Chọn một đoạn bên trái. Xem **trọn đoạn** (có thể 1,5× hoặc 2×; video tự dừng ở cuối đoạn).
3. Với mỗi gợi ý bên phải: **Đúng…** → kiểm tra thời gian/vùng/mức độ → **Lưu nhãn**; **Sai** nếu không cần làm gì; **Mơ hồ** nếu chính bạn không chắc. Gợi ý ghi "trước đây: BLUR/CUT/KEEP" là quyết định cũ của bạn.
4. Thấy nội dung cần BLUR/CUT mà không có gợi ý: dừng video, `[` đặt đầu, `]` đặt cuối, chọn nhóm/hành động/mức độ, bấm **✎ Vẽ vùng** (phím R) rồi kéo chuột **sát** logo/chữ, **Lưu nhãn**.
5. Quy tắc:
   - Watermark xuất hiện suốt đoạn: một nhãn phủ cả đoạn (đầu–cuối đoạn), vùng sát chữ watermark. Watermark đổi vị trí → mỗi vị trí một nhãn.
   - Mức độ: `must_catch` nếu lọt ra video là lỗi (watermark, quảng cáo, cảnh 18+ rõ); `should_catch` nếu nên bắt; `nice_to_have` cho thứ tùy chọn.
   - `KEEP` chỉ dùng cho **bẫy đã biết** (vùng detector hay báo nhầm mà tuyệt đối không được blur, ví dụ đầu nhân vật Conan 20 lúc 510 s). Những thứ bình thường không cần nhãn.
   - Máu me/bạo lực hoạt hình bạn vẫn giữ (KEEP) thì **không** gán nhãn.
6. Hết gợi ý chờ và đã xem trọn đoạn → **✔ Đã xem hết đoạn**. Chỉ đoạn này mới được chấm. Muốn sửa lại: **↺ Mở lại đoạn**.
7. Làm nhiều buổi được. Mọi thay đổi có lịch sử ở `annotations/golden/v1/label-history.jsonl` và bản sao ở `annotations/golden/v1/backups/`.

## 14. Rà soát nhiều agent (29/09/2026, trước khi có số đo)

Năm agent rà soát độc lập (mỗi phát hiện được một agent khác cố bác bỏ): 16 lỗi xác nhận, 2 lỗi chưa chắc, 5 bị bác bỏ. Đã sửa hết trước khi có nhãn nào, nên không vi phạm nguyên tắc "cổng ghi trước":

- **Chấm điểm:** quy tắc loại mục/vùng ở mục 4 (lỗi nghiêm trọng nhất: watermark suốt phim từng bắt hộ mọi nhãn CUT toàn khung và chạm mọi bẫy toàn khung); vùng đạt tính theo mục phủ tốt nhất; mục chạm cả nhãn dương lẫn nhãn mơ hồ vẫn là báo nhầm; cổng detector **thất bại** khi thiếu số thời gian hoặc khi nhóm chưa có mục chính ở mốc mà bản mới thêm báo nhầm (trước đây bị bỏ qua âm thầm).
- **Kho nhãn/trang:** ghi kiểu giao dịch, khóa một người ghi, không cho một gợi ý thành hai nhãn, nhãn từ gợi ý đã biến mất vẫn sửa được, "Mơ hồ" cho logo không cần vùng, đổi đoạn khi video chưa nạp xong vẫn nhảy đúng đoạn.
- **Gợi ý:** gợi ý đã xử lý được giữ lại (đánh dấu cũ) khi đầu vào đổi thay cho yêu cầu `--force`; lượt đánh giá Golden không bao giờ làm đầu vào gợi ý; bộ lọc chữ quét dày được ghi đúng như code.
- **Script:** `evaluate_golden.py run` hiện tiến độ và nguyên nhân lỗi; các script in tiếng Việt không còn lỗi mã hóa khi ghi log.

## 15. Chế độ dễ của trang gán nhãn (30/09/2026)

Người dùng thấy trang đầy đủ khó dùng ("không biết chọn như nào"). Trang mặc định (`Golden-Label.cmd` → http://127.0.0.1:8766) giờ là chế độ hỏi–đáp; trang cũ ở `/full`.

1. **Watermark, mỗi phim một câu:** các gợi ý watermark suốt phim được gom theo phim và khung (khung trùng ≥ 80% là một); khung ưu tiên là khung người dùng duyệt gần nhất. "Đúng" tạo nhãn BLUR must_catch cho mọi đoạn còn mở của phim đó (bỏ qua đoạn đã có nhãn watermark tương đương), các gợi ý watermark của đoạn được đánh dấu "covered"; "Không phải" đưa các gợi ý đó về hỏi từng cái.
2. **Từng câu hỏi:** chỉ mục chính và advisory từng được người dùng BLUR/CUT (136 câu thay vì 404); cùng bốn lựa chọn như Dashboard: Để nguyên / Làm mờ (logo/chữ theo khung gợi ý, an toàn toàn khung) / Cắt bỏ / Không chắc (nhãn `ambiguous`). Mức độ mặc định must_catch; có hoàn tác.
3. **Xem nhanh 2×:** nút ⚑ đánh dấu đầu/cuối chỗ bị sót, chọn loại bằng nút lớn; logo/chữ làm mờ yêu cầu khoanh vùng. "Xong đoạn" đánh dấu Sai các gợi ý ẩn còn lại rồi khóa đoạn.

Nhãn do chế độ dễ tạo là nhãn bình thường (cùng schema, lịch sử, sao lưu). 7 nhãn T1 người dùng gán trên trang cũ lúc 17:51–18:03 được giữ nguyên.

## 16. Gán nhãn bằng điện thoại (30/09/2026)

Để người dùng gán nhãn khi máy tính đang có người khác dùng.

- **Mở:** bấm đúp `Golden-Label-Phone.cmd` ở thư mục gốc. Cửa sổ đen in ra link dạng `http://<IP máy trong Wi-Fi nhà>:8766/?code=<mã 8 ký tự>` (cũng lưu tạm ở `reports/benchmarks/golden-v1/phone-link.txt`). Điện thoại phải dùng cùng Wi-Fi.
- **Bảo vệ:** chỉ mở ra mạng nội bộ khi có mã ngẫu nhiên (tạo mới mỗi lần chạy). Mọi yêu cầu, kể cả trang, video và ảnh khung (có thể chứa cảnh 18+), đều cần cookie `golden_access` (HttpOnly, SameSite=Strict) do link có mã cấp; thiếu mã thì trang hiện ô nhập mã, API trả 401. Thao tác ghi vẫn cần thêm session token. Không gắn được địa chỉ ngoài `127.0.0.1` nếu không có mã. Máy chủ chỉ nghe trên địa chỉ Wi-Fi nhà, không mở ra Internet.
- **Tường lửa:** lần đầu Windows có thể hỏi cho Python qua tường lửa. Người dùng tự bấm "Allow access"; BiliFlow không tự đổi cài đặt tường lửa.
- **Tắt hẳn:** đóng cửa sổ đen hoặc bấm đúp `Golden-Label-Stop.cmd`, lệnh này dừng mọi trang gán nhãn đang chạy (cả chế độ máy tính lẫn điện thoại) và xóa file link. Nhãn đã được lưu ngay sau mỗi lần bấm, nên tắt lúc nào cũng không mất.
- Mỗi lúc chỉ chạy được một trang gán nhãn (khóa `events.lock`), nên muốn đổi giữa máy tính và điện thoại thì tắt trước rồi mở lại.

## 17. Kiểm tra nhãn và bảng điểm gốc (30/09/2026)

**Kiểm tra nhãn.** Người dùng làm xong 13 đoạn (revision 245, 111 nhãn) nhưng cho biết đã tập trung vào logo quảng cáo nên có thể chọn nhầm. 114 câu trả lời (68 nhãn không phải watermark + 46 câu "Sai" bấm tay) được chấm từ ảnh trích video có vẽ khung (`reports/benchmarks/golden-v1/audit-20260930`): mỗi nhóm ~10 câu một agent chấm, mọi chỗ nghi sai được agent thứ hai cố bác bằng thêm khung hình (20 agent). Kết quả:

- 46/46 câu "Sai" đúng.
- 31 nhãn khoanh tay trùng watermark PhimOnline.net đã có nhãn cả đoạn (khung che 56–100% logo, rộng 0.66–2.7×): nội dung đúng nhưng trùng và làm lệch chỉ số vùng.
- 59 nhãn "Đúng" sai: logo hãng phim/credits (Warner Bros, Toho, credits cuối Troy bị chọn 18+), chữ và hình trong phim (con dấu FBI/CIA, băng KEEP OUT, biển quầy, bảng phi tiêu, 4 khung trên đầu nhân vật), "máu me" ở cảnh thường (gói snack, chữ FRAGILE trên thùng), đánh nhau hoạt hình không máu.
- Giữ nguyên: 5 nhãn máu me có máu thật (Conan 20 kho hàng, án mạng mở đầu Conan 21), 1 nhãn 18+ (Troy cảnh trong lều), watermark.
- T1: hai khung XEMBZ.NET rộng (160×44, 174×54) được thay bằng khung người dùng đã xác nhận ở T2–T4 (118,173 150×31), khung này ôm vừa logo và dòng chữ phụ.

Người dùng xem hai ảnh tổng hợp và đồng ý sửa. `scripts/golden_audit_corrections.py` áp dụng kế hoạch `corrections.json` qua `LabelStore` (sao lưu, lịch sử, actor `claude-audit`, chạy thử trên bản sao trước, từ chối nếu revision khác 245): nhãn sai bị xóa và câu hỏi ghi "Sai"; nhãn trùng bị xóa và câu hỏi ghi "covered" bởi nhãn watermark (`LabelStore.cover_suggestion`). Mã nhãn đã xóa không bao giờ được cấp lại (`retired_event_ids`). Kết quả: revision 457, 19 nhãn, 13/13 đoạn xong.

**Bảng điểm gốc** (`reports/benchmarks/golden-baseline-v1-20260930-225627`, queue fast-scan tất cả nhóm: Troy `troy-allgroups-full-fast-20260930-162534`, Conan 20 `conan20-allgroups-full-fast-20260930-073627`, Conan 21 `conan21-allgroups-full-golden-20260930-222720`; 64 phút, pipeline 4 292 s):

| Nhóm | Nhãn | Recall must_catch | Precision mục chính | Vùng đạt |
| --- | --- | --- | --- | --- |
| Quảng cáo | 13 | 13/13 | 13/20 (65%) | 9/13 |
| 18+ | 1 | 1/1 | 1/9 (11%) | — |
| Máu me | 5 | 5/5 | 5/21 (24%) | — |
| Bạo lực | 0 | — | 0/6 (0%) | — |

- Không sót nhãn nào; vấn đề chính là báo nhầm (credits/logo hãng phim thành logo quảng cáo hoặc 18+, cảnh thường thành máu me, đánh nhau hoạt hình thành bạo lực).
- Lỗi vùng thật: ở Conan 21 bước `repeated_visual_ocr_consensus` thu khung watermark từ 1544,38 376×67 xuống 1653,46 208×66, bỏ ngoài chữ "Phim" (ảnh `c21-watermark-region.png`); blur theo gợi ý này để lộ một phần watermark.
- Giới hạn: 18+ chỉ có 1 nhãn, bạo lực không có nhãn dương nào trong 13 đoạn; cần thêm đoạn (v1.1) trước khi kết luận recall các nhóm an toàn.

**Troy 18+ quanh phút 16 (người dùng báo "bắt thiếu").** Kiểm tra song song, độc lập (`reports/benchmarks/golden-v1/troy-adult-check-20260930`): một agent định vị cảnh từ khung hình 1–4 fps, một agent tái tạo đúng điểm của detector 18+ (nsfw-nano, 2 fps, CPU; khớp chính xác các interval production).

- Revision đang hoạt động của job Troy (#39, rev 3 `run-20260927-234709`) chỉ quét **quảng cáo**, bỏ 18+/máu me/bạo lực, và đang READY_TO_EXPORT: Dashboard không có mục 18+ nào. Đây là lý do chính người dùng không thấy cảnh này.
- Khi detector 18+ chạy (rev 2, trial tất cả nhóm) các phần khỏa thân được bắt: 937.5–950.75 (lộ ngực) nằm trong mục high 928.5–950.5; cảnh giường 977–1014 được bắt từ 983 (mục high 983–1027). Rev 2 chưa được duyệt; ở rev 1 người dùng đã chọn KEEP cho 937–950.5 và 1006.5–1027, BLUR chỉ 983.5–986 và 990.5–1004.5.
- Chỗ detector thật sự hụt: phần dạo đầu có quần áo (vuốt ve 884–915, hôn 915–928, điểm < 0.70 trừ 895.5–900) và 6 s đầu cảnh giường 977–983 (điểm 0.1–0.65). Mô hình nsfw-nano chấm khỏa thân/khiêu dâm nên coi hôn có quần áo là an toàn; `sequence_completion` chỉ nối ≤ 8 s qua điểm ≥ 0.70 nên không lấp được.
- Đoạn 870–1110 s không nằm trong 13 đoạn của Golden Set v1, nên recall 18+ của bảng điểm gốc chưa đo cảnh này. Đề xuất v1.1: thêm đoạn này (và các cảnh 18+ khác) với quy ước người dùng chọn cho cảnh hôn/dạo đầu.

## 18. Kế hoạch Q3 (30/09/2026, người dùng duyệt thứ tự a → b → c)

**Quy ước mới của người dùng:** cảnh hôn/vuốt ve còn quần áo **không** tính 18+ (KEEP). 18+ = khỏa thân hoặc hoạt động tình dục.

**a. Quét lại Troy đủ 4 nhóm (đang chạy).** Job #39 rerun qua Control Center (`/api/jobs/39/rerun`, detectors advertising+adult+gore+violence, fast_scan, OCR batch 1 như cũ). Job rời trạng thái READY_TO_EXPORT nên bản chỉ-quảng-cáo không thể bị xuất nhầm. Người dùng duyệt revision mới trên Dashboard; không tự xuất.

**b. Khung watermark Conan 21 hụt chữ "Phim".** `review_workflow.refine_persistent_logo_regions`: với mục `text` persistent_overlay, cụm khung OCR lặp lại chiếm ưu thế (chỉ "Online.net") thay khung gốc khi diện tích < 75%. Thiết kế sửa: khung tinh chỉnh không được bỏ ngoài phần chữ/logo mà bằng chứng của chính mục đó thấy (vd. hợp các cụm OCR cùng hàng, hoặc giữ khung gốc khi cụm chỉ phủ một phần chiều ngang). Cổng: (1) test hồi quy; (2) dựng lại queue từ report sẵn có của 3 trial golden (chỉ bước build_review, CPU): Conan 21 vùng đạt 4/4, Conan 20 và Troy không đổi mục nào ngoài vùng watermark; (3) golden `compare --gate detector` không vi phạm; (4) bộ test đầy đủ.

**c1. 18+ theo cảnh (chỉ khỏa thân).** Troy 977–983 s: 6 s đầu cảnh giường điểm 0.1–0.65 nên `sequence_completion` (≤ 8 s qua điểm ≥ 0.70) không nối. Hướng: khi có hạt ≥ 0.95, mở rộng tới ranh giới shot/cảnh (phát hiện cắt cảnh) thay vì chỉ theo điểm, có giới hạn thời lượng. Đo offline trước bằng điểm từng khung (CSV) và ground truth cảnh; rồi đo cả phim sau khi GPU rảnh: phủ thêm bao nhiêu giây đúng/sai (credits, cảnh thường). Không đổi ngưỡng 0.95/0.70.

**c2. Golden Set v1.1.** Thêm đoạn có 18+/bạo lực dương thật (Troy 870–1110 s và các cảnh khác) cùng "hard negative" (credits); v1 giữ nguyên, v1.1 có manifest riêng hoặc di trú nhãn có kiểm tra. Người dùng gán nhãn theo quy ước mới.

**Song song:** GPU chỉ chạy (a). Trong lúc đó các agent làm việc CPU: một agent sửa (b) trong cây làm việc (chỉ `review_workflow.py` + test), một agent thiết kế/đo offline (c1) trong scratchpad, một agent đề xuất đoạn cho (c2) (chỉ đọc). Kết quả (b) được một agent khác rà soát đối kháng trước khi báo người dùng. Không commit cho tới khi người dùng yêu cầu.

**Quyết định của người dùng (30/09, tối):**
- Bạo lực/máu me: cảnh chém, đâm thấy rõ trong phim người thật **được tính** (nhãn dương, máy phải báo để người duyệt quyết định BLUR/CUT). Giữ T7/T8 trong v1.1; đánh nhau hoạt hình không máu vẫn KEEP.
- Nhãn watermark Conan 21 (C21A–D) được thu từ 1553,44 308×69 (có đệm) về 1565,52 282×46, khung che đủ mọi chữ "PhimOnline.net" (kiểm tra 700/1400/2200/3000/4300/5600/6000 s; `audit-20260930/c21-tight-box.png`), giống nhãn Conan 20. Áp dụng qua `corrections-c21-rebox.json` (hành động `rebox`: chỉ đổi khung, giữ mã nhãn, liên kết watermark và lịch sử). Nhãn revision 469; mốc so sánh mới `reports/benchmarks/golden-baseline-v1-r469-20260930-235857` (vùng quảng cáo 9/13 vì code hiện tại để lộ chữ "Phim").
- Troy #39 đã quét lại đủ 4 nhóm (revision 4, `run-20260930-230655`, 159 mục chờ duyệt, WAITING_REVIEW).

**d. Ô duyệt phải cho thấy nội dung thật (người dùng báo 01/10).** Troy rev 4, mục 18+ 928.5–950.5 (15:28–15:50): ô duyệt chỉ có một ảnh `preview_images` = khung mạnh nhất 944.5 s (ôm, lưng trần), không thấy lúc lộ ngực 937.5–941.5, và trang duyệt Dashboard không có video. Người dùng chọn KEEP vì không thấy gì, sau đó tự sửa thành BLUR khi được báo. Cải tiến: (1) dải nhiều khung trải đều cả đoạn, ưu tiên mọi khung có điểm ≥ ngưỡng, có mốc thời gian; (2) trình phát video ngay trong ô duyệt (stream nguồn chỉ-đọc bằng HTTP Range như trang gán nhãn), nút "phát đoạn này" và "nhảy tới chỗ điểm cao nhất"; (3) ghi rõ "N khung ≥ 0.95 ở các giây …". Áp dụng cho 18+/máu me/bạo lực trước, quảng cáo sau. Không đổi detector hay ngưỡng.

## 19. Golden Set v1.1 và bảng điểm gốc v1 + v1.1 (01/10/2026)

- v1.1 (6 đoạn, 24.9 phút) do người dùng gán; 26 câu máu me/bạo lực trả lời "Sai" chỉ để giữ cảnh đã được hỏi lại với nút mới "Có thật — vẫn giữ nguyên" (nhãn KEEP + `content_present`, là nhãn dương cho phát hiện); câu logo C20F có khung đỏ trên đầu nhân vật được đổi thành "Sai" theo quy ước của người dùng (logo PhimOnline đã có nhãn riêng). Nhãn v1.1 revision 108, 37 nhãn.
- Bảng điểm gốc v1 + v1.1 với code hiện tại (`reports/benchmarks/golden-baseline-v1v11-20261001-095204`; Conan 21 dùng queue đã sửa vùng):

| Nhóm | Nhãn | Bắt được | Precision mục chính | Vùng đạt |
| --- | --- | --- | --- | --- |
| Quảng cáo | 19 | 19/19 | 19/26 (73%) | 19/19 |
| 18+ | 6 | 6/6 | 5/28 (18%) | — |
| Máu me | 17 | 17/17 | 16/32 (50%) | — |
| Bạo lực | 14 | 14/14 | 14/20 (70%) | — |

- Không sót nhãn nào, nhưng phần lớn nhãn sinh từ gợi ý của chính detector, nên chỗ sót chỉ lộ ra qua cờ "bị sót" của người dùng (T6 6791–6863) và kiểm tra khung hình (Troy 977–983). Mục tiêu tiếp theo là giảm báo nhầm, ưu tiên 18+ (23/28 mục chính là báo nhầm) rồi máu me.

## 20. Kế hoạch giảm báo nhầm 18+ (01/10/2026)

Chi tiết, số liệu, cổng và câu hỏi cho người dùng: `docs/ADULT_FALSE_ALARM_PLAN.md` (bằng chứng trong `temp/adult-fp/`). Chưa thi công; chờ người dùng trả lời mục 9 của kế hoạch.

**Quyết định của người dùng (01/10):** (1) khỏa thân ngụ ý (đắp chăn, khung tới vai, che kín chỗ nhạy cảm) vẫn phải được báo để người dùng xác nhận, lộ rõ là chắc chắn 18+ → dùng t = 0.7; nhãn v1.1 r115: gs-T5-0004 và gs-T6-0004 (6869.5–6883.5) là nhãn dương should_catch (`corrections-implied-nudity.json`). (2) Đồng ý chuyển sang Ứng viên phụ (không xóa) và bộ kiểm thứ hai NSFW của `image_safety_classifier_m`. (3) Chỉ bật mức bảo thủ (credits + k = 2, t = 0.7) cho phim người thật; mức cân bằng (k = 5) cài sẵn nhưng tắt cho tới khi có phim người thật thứ hai qua cổng 4.6. (4) Chưa có phim thứ hai. (5) Bật R3 (mở rộng 18+ theo cảnh) cho phim người thật.

**Thi công xong (01/10):** mức bảo thủ bật mặc định cho phim người thật, R3 bật; cổng 4.1–4.5 đạt (`reports/benchmarks/adult-triage-20261001-131834`, rà soát độc lập từng khung của mọi mục bị chuyển). Còn mở: cổng 4.6 (phim người thật thứ hai) trước khi bật "balanced"; mục 18:20 (1100–1102.5, cặp đôi cởi đồ chỉ thấy vai, 2 seed, NSFW 0.618) cần người dùng xác nhận là "ngụ ý" trước khi tính tới "balanced". Đề xuất tiếp: giữ quyết định đã duyệt khi quét lại (hiện chỉ giữ mục chưa duyệt), để quét lại Troy lấp 16:17–16:23 không phải duyệt lại từ đầu.

## 21. Giữ quyết định khi quét lại (01/10/2026, người dùng chọn làm trước)

Hiện trạng: khi quét lại một video (revision mới), `build_review_queue` chỉ giữ các mục **chưa duyệt** của queue cũ (`preserve_unresolved_review_items`); mọi quyết định đã duyệt bị bỏ, nên Troy rev 4 có 159 mục chờ duyệt dù rev 1 đã duyệt. Mục tiêu: quét lại để lấp 16:17–16:23 mà người dùng chỉ phải duyệt phần thật sự mới hoặc thay đổi.

Nguyên tắc (giữ bất biến "người duyệt mọi quyết định"):
- Chỉ mang quyết định sang khi mục mới **cùng nội dung** với mục cũ đã duyệt: cùng nhóm/loại, khoảng thời gian gần như trùng (sai lệch nhỏ cố định), cùng vùng (với logo/chữ). Mục mang sang ghi rõ nguồn (revision, thời điểm, người quyết định) và vẫn mở lại để sửa được.
- Mục **thay đổi** (khoảng dài ra/ngắn lại, vùng khác) **không** tự áp quyết định: để chờ duyệt, hiện "Trước đây bạn chọn …" để người dùng xác nhận bằng một phím. Không bao giờ tự làm mờ/cắt phần nội dung mới.
- Mục mới hoàn toàn: chờ duyệt như bình thường. Quyết định cũ không còn mục tương ứng: liệt kê trong khối kiểm toán, không áp dụng.
- Cổng: dựng lại queue Troy (R3 + lọc 18+) với queue rev 4 đã duyệt làm queue cũ trên **bản sao**: số mục mang sang, số mục "cần xác nhận" (gồm 16:17–17:07), không mục nào bị tự áp quyết định khi khoảng thay đổi; test đầy đủ; xuất video từ queue có quyết định mang sang vẫn đúng.

**Tạm dừng (01/10, 18:25):** người dùng ưu tiên video mới (quét nhanh hơn, ít mục duyệt hơn) hơn việc quét lại. Code đang làm dở của §21 được cất: `git stash` "WIP: carry reviewed decisions on rerun" và `temp/q3-handoff/carry-decisions-wip.patch`. Còn mở khi làm tiếp: lỗi nặng của vòng soát 3 (gợi ý "Trước đây bạn chọn" bị che khi quét lại hai lần không duyệt ở giữa), hai lỗi vừa (gợi ý không vào danh sách mồ côi; track watermark hấp thụ thẻ mới vẫn mang quyết định cũ); hướng sửa: bốn bất biến I1–I4 và test ngẫu nhiên (xem prompt trong workflow `carry-decisions-invariants`).

## 22. Ưu tiên mới: video mới quét nhanh hơn, duyệt ít hơn (01/10/2026)

Đang phân tích song song (chỉ đọc): tải duyệt theo nguồn gốc trên Troy/Conan 20/Conan 21, thời gian từng bước của pipeline, nguyên nhân báo nhầm máu me hoạt hình; sau đó lập kế hoạch có số liệu để người dùng duyệt.

## 23. Thẻ cảnh (R1) và logo hãng phim (R3a/R3b): chốt an toàn sau vòng soát (01/10/2026)

- **R3b không được làm sót quảng cáo.** pHash 64 bit toàn khung gần như không thấy lớp phủ nhỏ (URL ở góc khung Toho vẫn 1.000, banner 10–15 % đáy khung WB 0.969). Một thẻ logo chỉ sang Ứng viên phụ khi đủ cả ba: (1) mọi ảnh xem trước khớp logo đã xác nhận bằng pHash ≥ 0.95 **và** lưới màu 32×18 lệch ≤ 20 mỗi ô (cùng ident giữa Conan 20/21 và mọi lần quét Troy lệch ≤ 6, nén lại JPEG q40 ≤ 14, chữ 5 px ở góc 23–26, mọi URL/banner/khung thử ≥ 36); ảnh không đọc được hoặc thứ 9 trở đi đều phải khớp; (2) có bản quét chữ phủ đoạn đó và không có dấu hiệu quảng cáo; (3) mọi dòng OCR trong đoạn là dòng mà logo đã xác nhận từng hiện (lưu khi bấm "giữ & nhớ"; chịu lỗi OCR nhỏ, không chấp nhận chữ thêm như "PHIMMOI", "BZNET"). Thiếu một điều kiện → thẻ ở danh sách chính, trang duyệt ghi "Hình giống logo hãng phim đã nhớ nhưng có chữ lạ: …".
- **Bộ nhớ logo hãng phim chỉ dùng cho queue thật**: `build_review_queue` mặc định tắt; lệnh `build-review` bật, trừ khi chạy trong thử nghiệm benchmark (`BILIFLOW_BENCHMARK_CACHE` hoặc thư mục có `.biliflow-benchmark`) hoặc có `--no-studio-logo-memory`. Golden/benchmark không phụ thuộc `state/studio-logo-memory.json`; `evaluate_golden.py` ghi lại và cảnh báo nếu queue có thẻ bị chuyển bởi bộ nhớ này.
- **Luật chấm Golden cho thẻ cảnh** (bổ sung, không nới): thẻ có `scene_card` được chấm theo các khoảnh khắc đã phát hiện (đúng phần quyết định sẽ sửa), không theo cả khoảng đầu–cuối; nhãn nằm trong khoảng trống không được tính là bắt. Queue không có thẻ cảnh chấm y hệt trước (thẻ chấm lại của control giống từng byte ở các trường nhãn/mục/số đo). Thẻ điểm có thẻ cảnh ghi thêm `rules.scene_card_extent`.
- Cổng chạy lại: `reports/benchmarks/review-load-fix-20261001-215846` (v1 r469 + v1.1 r115; control = HEAD 16d203e). `compare --gate detector` đạt cả hai bộ (v1.1 máu me 6/7 → 7/7). Ba nhãn should_catch (gs-T7-0008, gs-T8-0002, gs-T8-0006) hiện "bắt một phần" ở thẻ cảnh nhưng "bắt được" ở control: chỉ vì control được tính cả khoảng trống < 6 s không bao giờ bị sửa; tính theo thời gian thực sự bị sửa, cả control lẫn bản mới đều 47 % / 69 % / 75–79 % (không mất giây nào). Số thẻ: Troy 118 → 92 (91 khi đã nhớ logo WB), Conan 20 69 → 53 (52), Conan 21 66 → 51 (50); edit plan của mọi thẻ cảnh đúng bằng các khoảnh khắc của nó.

## 23. Báo nhầm máu me hoạt hình (02/10/2026)

Phân tích xong, chưa thi công: `docs/ANIME_GORE_PLAN.md` (bằng chứng `temp/next/anime-gore/`). Giữ RGB (BGR tệ hơn). Theo chính sách hiện tại (vết xước/bầm không máu = vết thương phải báo) không luật nào đủ biên an toàn; nếu người dùng xác nhận xước/bầm không máu không cần báo, luật C1 (không tag máu, không tag xác) bớt khoảng 8 + 7 thẻ ở Conan 20/21 mà không chuyển máu thật nào, nhưng vẫn cần bộ kiểm thứ hai và một phim anime thứ ba trước khi bật. Chờ người dùng dùng thử giao diện mới rồi lập kế hoạch tiếp.

**Quyết định của người dùng (02/10):** vết xước/vết bầm không có máu **không** cần máy báo (chỉ máu thật, kể cả máu vẽ trong anime). Hệ quả: nhánh "Không" của `docs/ANIME_GORE_PLAN.md` áp dụng (luật C1); hai nhãn bầm gs-C20F-0009/0010 ("có thật · giữ") mâu thuẫn với quy ước này và cần đổi thành "Máy sai" trước khi đo C1. Làm trong kế hoạch tiếp theo, sau khi người dùng dùng thử giao diện mới.

## 24. Watermark trang web và thẻ "Kiểm tra đoạn mở đầu" (02/10/2026)

- **Watermark trang web (đã sửa, đã tích hợp):** chữ cố định một chỗ ≥ 50 % thời gian quét, hoặc ≥ 8 lần đọc các mảnh của cùng một dòng chữ ở một chỗ, thành một thẻ "toàn video" cho đúng vùng đó; chỉ gợi ý làm mờ khi có bằng chứng quảng cáo. Chi tiết và kiểm chứng: CHANGELOG 02/10.
- **Thẻ 0:00–0:05 "Opening boundary review"** (luật giữ 5 giây đầu một lần, có từ khi bỏ sót logo Netflix): ở Nhất Âu Xuân tập 11/12/14/17/20, AI trả lời KHÔNG, không có vùng, không có chữ; giao diện lại gọi là "LOGO" và giấu câu trả lời. Chưa từng bắt được intro thật trong dữ liệu hiện có.
- **Quyết định của người dùng (02/10):** (1) thẻ mở đầu khi AI trả lời KHÔNG **vẫn ở danh sách chính**; chỉ sửa giao diện cho trung thực (tên "Kiểm tra đoạn mở đầu", câu trả lời của AI, nút phát đoạn, khung vàng tham khảo cho vùng AI định vị). (2) Bộ nhớ "logo hãng phim" **bỏ qua vùng watermark đã chọn làm mờ** và **nhớ nhiều khung** của logo động, để một logo nhớ từ tập có watermark khớp được tập sạch; lớp phủ lạ ở chỗ khác vẫn phải làm hỏng việc khớp. Phải đo không chuyển nhầm trên Troy/Conan/Nhất Âu Xuân trước khi bật.
- **Đã làm (02/10):** giao diện thẻ mở đầu trung thực (đã tích hợp); bộ nhớ logo hãng phim bản 2 (bỏ qua vùng watermark đã làm mờ, nhớ mọi khung có thông tin trong đoạn, chặn khung đen/mờ dần; 0/7.803 thẻ phim khác khớp nhầm). Giới hạn theo đúng quyết định: nội dung nằm trọn trong vùng watermark đã làm mờ không làm hỏng việc khớp ảnh; chữ ở đó vẫn bị chặn bởi kiểm tra OCR, hình đồ họa thì không.
