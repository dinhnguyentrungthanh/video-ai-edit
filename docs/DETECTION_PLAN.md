# Kế hoạch detection theo loại nội dung và phong cách phim

## Nguyên tắc bắt buộc

- Tách riêng ba nhóm: `adult`, `blood_gore`, `violence`.
- Mỗi nhóm chạy và đánh giá riêng cho `live_action` và `animation`.
- Model chỉ tạo candidate và timestamp; không tự blur, cắt hoặc export.
- Mọi candidate phải có quyết định của người dùng: `KEEP`, `BLUR`, `CUT` hoặc `NEEDS_MORE_CONTEXT`.
- Video nguồn không bị sửa. Chỉ tạo thumbnail giới hạn và JSON/HTML trong giai đoạn benchmark.

## Ma trận sáu nhánh

| Nội dung | Phim thực tế | Phim hoạt hình/anime | Hướng model |
|---|---|---|---|
| 18+ | Classifier ảnh/video huấn luyện trên ảnh người thật | Classifier có nhãn drawing/anime, hentai và explicit | Hai model, hai threshold riêng |
| Máu me/gore | NSFL/gore classifier và candidate theo vùng | Anime tagger đa nhãn cho blood, injury, gore; có thể fine-tune sau benchmark | Không dùng màu đỏ đơn thuần để kết luận |
| Bạo lực | Video classifier theo chuỗi frame | Video classifier temporal benchmark lại trên anime; nếu domain gap lớn thì fine-tune | Không dùng classifier một frame làm quyết định cuối |

## Chọn tuyến xử lý

Mỗi job có trường `content_style`:

- `live_action`: chỉ chạy ba nhánh phim thực tế.
- `animation`: chỉ chạy ba nhánh hoạt hình/anime.
- `mixed`: chạy cả hai và gộp candidate.
- `unknown`: hệ thống có thể đề xuất loại phim, nhưng phải hỏi người dùng nếu không chắc chắn.

Không tự suy đoán metadata khi độ tin cậy thấp.

## Model candidate cho vòng benchmark kế tiếp

| Nhánh | Candidate đầu tiên | Dung lượng ước tính | Lý do thử | Rủi ro chính |
|---|---|---:|---|---|
| 18+ phim thực tế | `viddexa/nsfw-detection-2-nano` | 16,3 MB | POC bắt đúng 9/10 mẫu dương và không báo sai 2/2 hard negative phim thật | Tập âm tính phim thật còn quá nhỏ |
| 18+ hoạt hình | `viddexa/nsfw-detection-2-nano` + temporal gate | 16,3 MB | POC bắt đúng 8/10 ở ngưỡng 0,95; nhẹ và có nhãn drawing/hentai riêng | Còn 4/20 hard negative ở ngưỡng 0,95; cần xác nhận nhiều frame |
| Gore phim thực tế | `OwenElliott/image-safety-classifier-m` | 45,3 MB | POC đạt recall 80%, precision 100%, balanced accuracy 90% | NSFL trong tập huấn luyện ít, có thể vẫn miss gore |
| Gore hoạt hình | `SmilingWolf/wd-vit-tagger-v3` + temporal gate | 378,7 MB | POC đạt recall 90%, specificity 80%, balanced accuracy 85% | Precision 69,2%; luôn cần review thủ công |
| Violence phim thực tế | `KingTechnician/videomae-small-finetuned-kinetics-xd-violence-binary` | 87,6 MB | POC đạt recall 80%, precision 88,9%, balanced accuracy 87,5% | Tập POC mới có một phim; cần field validation |
| Violence hoạt hình | VideoMAE-small temporal + WD direct-action | 87,6 MB + tagger dùng chung gore | V5 trên 126 mẫu/two video: recall 86,44%, precision 80,95%, balanced accuracy 84,27% | Nhãn Conan còn provisional; mọi candidate cần review |

Model 18+ nano đã tải và khóa revision/checksum. Model Falconsai cũ bị loại sau benchmark vì báo sai 22/22 hard negative; báo cáo được giữ và trọng số được xóa. Các candidate còn lại chỉ được tải từng model sau khi nhánh tương ứng có đủ mẫu POC, rồi xóa trọng số ngay nếu model không đạt.

## Kết quả POC 18+ ngày 2026-09-20

- Tập nhỏ gồm 20 mẫu dương tính có nguồn/giấy phép Wikimedia Commons và 22 hard negative từ Conan/Tears of Steel.
- Nano tại ngưỡng 0,50: recall 85%, precision 62,96%, specificity 54,55%; 17/20 dương tính đúng và 10/22 báo sai.
- Nano tại ngưỡng 0,95: hoạt hình recall 80%, precision 66,67%, specificity 80%; phim thật vẫn bắt 9/10 mẫu dương và 2/2 mẫu âm.
- Falconsai tại ngưỡng 0,50: recall 80% nhưng specificity 0%; model bị loại vì báo sai toàn bộ 22 hard negative.
- Đây là POC để chọn hướng, chưa đạt gate sử dụng thực tế vì thiếu 30 dương + 60 âm cho mỗi nhánh và thiếu ít nhất hai video độc lập.
- Với anime, candidate phải qua điều kiện 3/5 frame ở 2 fps rồi mới xuất hiện trong danh sách review. Điều kiện này chỉ giảm nhiễu; mọi candidate vẫn cần người dùng xác nhận.

## Benchmark có ground truth

Mỗi nhánh cần một tập kiểm thử độc lập:

- POC ban đầu: tối thiểu 10 mẫu dương tính và 20 hard negative.
- Gate sử dụng thực tế: tối thiểu 30 mẫu dương tính và 60 hard negative từ ít nhất hai video khác nhau.
- Mẫu dương tính phải có timestamp bắt đầu/kết thúc và mức độ `low`, `medium`, `high`.
- Hard negative gồm da người, màu đỏ, cảnh nằm/ngã, vũ khí không sử dụng, ánh sáng mạnh, cận mặt và chuyển cảnh nhanh.
- Tập hiệu chỉnh threshold và tập đánh giá cuối phải tách nhau.

## Kết quả POC blood/gore ngày 2026-09-20

- Tập gồm 20 mẫu dương có nguồn rõ ràng và 40 hard negative: mỗi phong cách có 10 dương + 20 âm.
- Phim thật dùng `OwenElliott/image-safety-classifier-m`, ngưỡng 0,50: đúng 8/10 dương và 20/20 âm; recall 80%, precision 100%, balanced accuracy 90%.
- Hoạt hình dùng `SmilingWolf/wd-vit-tagger-v3`, hợp nhất xác suất của 23 tag máu/thương tích, ngưỡng 0,05: đúng 9/10 dương và 16/20 âm; recall 90%, precision 69,2%, balanced accuracy 85%.
- Nhánh hoạt hình áp dụng thêm xác nhận 3/5 frame ở 2 fps. Smoke test 35 giây quét 70 frame trong 4,532 giây; 2 frame đơn lẻ vượt ngưỡng đều bị temporal gate loại, còn 0 interval.
- Hai nhánh đã đạt mục tiêu độ phủ 80–85% của POC và được đóng băng tạm thời. Precision hoạt hình hơi dưới mục tiêu 70% đúng một mẫu; review bắt buộc và temporal gate kiểm soát rủi ro này.
- Tập POC dương chỉ chiếm khoảng 3,1 MB. Báo cáo lưu giới hạn thumbnail; không tạo full export.

## Acceptance criteria cho từng nhánh

- Mốc dùng thử là balanced accuracy và recall từ 80% trở lên; mục tiêu tốt là 85%.
- Không tiếp tục tinh chỉnh một nhánh sau khi đã nằm trong vùng 80–85% nếu hai nhánh còn lại chưa có baseline.
- Precision mục tiêu từ 70% trở lên ở chế độ review.
- Tải review mục tiêu không quá 20 candidate cho mỗi giờ video.
- Candidate phải có thumbnail, timestamp, score, model revision và lý do gắn cờ.
- Trong POC chỉ cho phép tối đa một mẫu `high` bị bỏ sót; mẫu đó phải được ghi vào regression set cho vòng cải tiến sau.
- Nếu chưa đạt 80%, nhánh giữ trạng thái `EXPERIMENTAL` và không được nối vào quy trình thực tế.
- Dù đạt 80–85%, hệ thống vẫn chỉ tạo candidate và luôn yêu cầu người dùng xác nhận trước khi sửa.

Các tỷ lệ trên chỉ có ý nghĩa sau khi đủ số mẫu; không suy ra từ một video âm tính.

## Kết quả POC violence ngày 2026-09-20

- Tập gồm 60 clip dài 2,2 giây: mỗi phong cách có 10 dương + 20 hard negative. Tổng clip khoảng 30,3 MB; audio bị bỏ để giảm dung lượng.
- Phim thật dùng VideoMAE-small 16 frame ở 8 fps, ngưỡng 0,50: đúng 8/10 dương và 19/20 âm; recall 80%, precision 88,9%, balanced accuracy 87,5%.
- Anime dùng tagger đã có cho gore với 20 tag hành động/nguy hiểm, ngưỡng 0,05: đúng 8/10 dương và 19/20 âm; recall 80%, precision 88,9%, balanced accuracy 87,5%.
- Tuyến anime khi quét video yêu cầu 3/5 frame dương ở 2 fps. Smoke test trên clip lửa/nguy hiểm xác nhận 4/4 frame và tạo đúng một interval review.
- VideoMAE 345 MB chỉ đạt phim thật recall 40%, anime balanced accuracy 60%; model bị loại và trọng số đã xóa sau khi giữ báo cáo/checksum.
- Cả hai nhánh violence đã đạt mốc POC. Kết quả vẫn chưa phải field gate vì dữ liệu dương hiện tập trung ở một phim cho mỗi phong cách.

## Quy trình xác nhận trước khi sửa

```text
DETECTED
  -> REVIEW_REQUIRED
  -> KEEP | BLUR | CUT | NEEDS_MORE_CONTEXT
  -> APPROVED_EDIT_PLAN
  -> PROCESS (chỉ khi người dùng yêu cầu)
```

Quy tắc vận hành:

1. Báo cáo mặc định chỉ có ảnh và timestamp.
2. Clip ngắn chỉ được tạo khi ảnh không đủ ngữ cảnh hoặc người dùng yêu cầu.
3. `KEEP` loại candidate khỏi edit plan nhưng giữ annotation để cải thiện model.
4. `BLUR` và `CUT` phải lưu người xác nhận, thời điểm xác nhận và phạm vi thời gian cuối.
5. Thay đổi threshold hoặc model làm mất hiệu lực phê duyệt cũ nếu candidate/timestamp thay đổi.
6. Không full export trong giai đoạn benchmark.

## Kết quả field scan Conan 99 phút ngày 2026-09-20

- Adult anime: 13 interval, 7,85 candidate/giờ, 177,105 giây xử lý. Tải review đạt mục tiêu nhưng ảnh đại diện đều là hard negative; cần đưa các cảnh người nằm/chạm, ánh đỏ và vật thể giống da vào regression set.
- Blood/gore anime: 69 interval, 41,64 candidate/giờ, 759,803 giây xử lý. Ngưỡng interval 0,20 trong mô phỏng còn 32 interval, tương đương 19,3/giờ, nhưng chỉ được áp dụng sau khi kiểm tra recall trên regression set.
- Violence anime: 124 interval, 74,83 candidate/giờ, 764,166 giây xử lý. Top candidate gần như toàn `fire`; `fire` và `explosion` phải chuyển sang nhóm danger và không được tự kích hoạt kết luận violence.
- Hai nhánh anime blood/gore và violence dùng cùng WD tagger nhưng đang quét hai lần. Pipeline kế tiếp phải suy luận một lần rồi tách hai score để giảm thời gian.
- Một phim tạo 1.741 thumbnail trong ba báo cáo. Định dạng báo cáo kế tiếp chỉ giữ strongest frame mỗi interval, top-K giới hạn và lý do gắn cờ.
- Field scan không tạo edit plan hoặc output. Candidate vẫn phải qua `KEEP`, `BLUR`, `CUT` hoặc `NEEDS_MORE_CONTEXT`.

### Kết quả V3 sau cải tiến

- Shared inference quét 11.928 frame trong 767,539 giây, nhanh hơn gần 2 lần so với chạy gore và violence riêng.
- Gore POC sau policy hai tầng: recall 80%, specificity 80%, balanced accuracy 80%. Field: 33 interval, 19,92/giờ; giữ candidate 4.901,5–4.909,5 giây.
- Violence field: 19 interval, 11,47/giờ. `fire` và `explosion` chỉ còn trong danger telemetry, không kích hoạt violence.
- Thumbnail được compact còn một ảnh mạnh nhất mỗi interval cùng top-20 giới hạn cho từng nhánh; báo cáo V3 dưới 1 MB.
- Benchmark violence anime cũ không hợp lệ cho định nghĩa direct violence vì positive set là injury/fire. V5 ở phần dưới đã thay nó bằng clip đánh, đá, đâm, bóp cổ và tấn công đã xác nhận.

### Kết quả V4

- Adult anime chuyển từ nano sang WD tagger dùng chung. Policy cuối bỏ nhãn `explicit` tổng quát, dùng các nhãn giải phẫu/hành vi cụ thể, union threshold 0,15 và tối thiểu 3 nhãn riêng đạt 0,05.
- Adult regression 43 mẫu anime: recall 100%, specificity 96,97%, precision 90,91%, balanced accuracy 98,48%.
- V4 full field chạy 754,749 giây. Trước điều kiện co-occurrence cuối, adult có 7 interval; cả 7 là false positive khi xem strongest frame. Policy cuối giữ 1/7 strongest frame trước temporal gate và sẽ được đo exact ở job anime kế tiếp.
- Gore giữ 33 interval, 19,92/giờ. Violence giữ 8 high-priority interval và 11 context interval.
- Provisional violence precision audit: high priority đạt 7/8 đúng; context gồm 6 false positive đã quyết định và 5 cần thêm ngữ cảnh. Chưa có recall vì legacy positives không đúng định nghĩa.

### Kết quả V5

- Benchmark direct violence mới có 126 clip từ Sintel và Conan, gồm 59 positive và 67 hard negative. Sintel được chia calibration/holdout theo hai cụm hành vi khác nhau.
- VideoMAE temporal dùng ngưỡng 0,61; WD direct-action dùng ngưỡng 0,20. Candidate được giữ khi một trong hai nhánh kích hoạt.
- Toàn bộ tập đạt recall 86,44%, precision 80,95%, specificity 82,09% và balanced accuracy 84,27%.
- Holdout Sintel đạt recall 85,71%, precision 82,76%, specificity 83,33% và balanced accuracy 84,52%.
- Full scan video độc lập Sintel dài 888,064 giây mất 71,745 giây cho VideoMAE, tương đương 12,38× thời gian thực. Sau khi gom các đoạn cách nhau tối đa 8 giây còn 20 interval, gồm 7 high và 13 context.
- Báo cáo full scan khoảng 231 KB và không tạo video output. Bộ storyboard dùng để gán nhãn được coi là dữ liệu tạm và xóa sau khi benchmark đã khóa.

## Thứ tự triển khai

1. Giữ 18+ nano ở baseline hiện tại; chưa dành thêm thời gian tối ưu riêng.
2. Blood/gore phim thật và hoạt hình đã đạt vùng 80–85%; đóng băng tạm thời.
3. Violence phim thật và hoạt hình đã đạt vùng 80–85%; đóng băng tạm thời.
4. Field scan đầu tiên đã hoàn tất; dùng lỗi thực tế làm regression set cho vòng hai.
5. Shared inference, giới hạn thumbnail, danger separation và gore hai tầng đã hoàn tất ở V3.
6. Adult hard negative và shared WD policy đã hoàn tất; xác minh exact field count trong anime job kế tiếp.
7. Benchmark violence anime V5 và quét video độc lập đã hoàn tất; đóng băng policy ở baseline hỗ trợ review.
8. Chuyển sang luồng xác nhận candidate và tạo edit plan; chỉ encode sau khi người dùng duyệt.

## Chiến lược độ phủ trước

Mục tiêu của vòng hiện tại là có ba detector đủ dùng để hỗ trợ review, không tối ưu một detector đến mức gần hoàn hảo. Khi một nhóm đạt vùng 80–85% trên POC, nhóm đó được đóng băng tạm thời và nguồn lực chuyển sang nhóm kế tiếp. Sau khi `adult`, `blood_gore` và `violence` đều có baseline, kết quả trên video thực tế sẽ quyết định nhánh nào cần cải tiến tiếp.
