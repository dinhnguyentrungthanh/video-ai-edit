# Chính sách miễn phí và bản quyền

BiliFlow dùng profile `free-commercial-safe-local` làm mặc định.

Một model chỉ được chạy khi đồng thời đáp ứng các điều kiện sau:

- không thu phí theo lượt, token hoặc thời gian xử lý;
- suy luận hoàn toàn trên máy và không gửi video, ảnh hay OCR ra dịch vụ ngoài;
- có URL nguồn, revision/checksum cố định và giấy phép được ghi trong manifest;
- giấy phép trọng số cho phép sử dụng thương mại;
- trạng thái trong `config/license_policy.json` là `APPROVED`.

Lệnh kiểm tra:

```powershell
.\scripts\run.ps1 license-audit
```

Scanner gọi cùng bộ kiểm tra trước khi nạp model. Model lạ, manifest thiếu thông tin, dịch vụ có phí, model chỉ cho nghiên cứu/phi thương mại hoặc model cần upload dữ liệu ra ngoài đều bị từ chối mặc định.

## Kiểm toán hiện tại

| Thư mục model | Giấy phép trọng số | Trạng thái |
|---|---|---|
| `easyocr` | Apache-2.0 | APPROVED |
| `nsfw_detection_2_nano` | Apache-2.0 | APPROVED |
| `image_safety_classifier_m` | MIT | APPROVED |
| `wd_vit_tagger_v3` | Apache-2.0 | APPROVED |
| `multilingual_minilm_text_semantics` | MIT | APPROVED |
| `vit_base_violence_detection` | Apache-2.0 | APPROVED |
| `qwen2_vl_2b_instruct` | Apache-2.0 | APPROVED |
| `florence_2_base` | MIT | APPROVED |

`APPROVED` xác nhận quyền sử dụng trọng số theo giấy phép công bố và cấu hình local miễn phí. Một số tác giả không công bố đầy đủ toàn bộ nguồn dữ liệu huấn luyện; trường `training_data_review_status` giữ phần này hiển thị thay vì che giấu sự không chắc chắn.

Model bạo lực VideoMAE XD cũ chỉ cho sử dụng phi thương mại nên đã bị khóa rồi xóa khỏi ổ E sau khi giữ báo cáo audit. Nhánh phim người thật hiện dùng ViT Apache-2.0 để tạo candidate và Qwen2-VL-2B Apache-2.0 để xác nhận; cả hai có revision/checksum cố định và chạy local.

Phi-3.5 Vision ONNX INT4 có giấy phép MIT nhưng bị khóa theo quality gate sau benchmark logo đạt specificity 0%. Trọng số và runtime thử nghiệm đã được xóa; chỉ còn báo cáo nhỏ để tránh lặp lại hướng không đạt. Qwen2.5-VL-3B không được tải vì file LICENSE chính thức giới hạn sử dụng phi thương mại.

## Dữ liệu nguồn

Giấy phép model không cấp quyền cho phim, nhạc, phụ đề, logo hoặc ảnh đầu vào. Trước khi upload/publish, người dùng phải xác nhận mình sở hữu nội dung hoặc có giấy phép/quyền cho phép chỉnh sửa và đăng. Phân tích và export local không tự tạo ra quyền đăng tải.

Script benchmark gore cũ từng lấy ảnh từ nguồn không có quyền tái sử dụng rõ theo từng file đã bị vô hiệu hóa. Báo cáo cũ được giữ làm audit, không dùng để phân phối hoặc xây lại tập production.
