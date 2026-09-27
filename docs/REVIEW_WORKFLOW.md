# Luồng xác nhận và tạo edit plan

## Trạng thái

Scanner chỉ tạo candidate. Không lệnh nào trong luồng review được phép sửa video nguồn hoặc tạo bản xuất toàn phim.

Luồng gồm sáu bước bắt buộc:

1. `build-review` hợp nhất các báo cáo của cùng một video, gộp candidate trùng loại và tạo JSON/HTML.
2. `review-decide` ghi từng quyết định `KEEP`, `BLUR`, `CUT` hoặc `NEEDS_MORE_CONTEXT`.
3. `build-edit-plan` chỉ chạy khi không còn mục chưa duyệt hoặc cần thêm ngữ cảnh.
4. `render-previews` chỉ tạo clip ngắn để kiểm tra. Kế hoạch vẫn đặt `final_export_allowed: false`.
5. `approve-previews` ghi nhận người duyệt và chỉ lúc này mới mở quyền xuất cuối.
6. `render-final` dựng vào file tạm, kiểm tra dung lượng, thời lượng, hình, tiếng và checksum nguồn rồi mới đổi tên thành output chính thức.

Người dùng thông thường mở giao diện bấm trực tiếp:

```powershell
.\scripts\run.ps1 review-ui --queue .\reports\video-review\review-queue.json
```

Trang `http://127.0.0.1:8765/` lưu lựa chọn ngay vào queue. Các nút tiếng Việt tương ứng với bốn quyết định và có nút `Bỏ chọn` để sửa nhầm.

Nút `Giữ nguyên tất cả đang lọc` chỉ áp dụng `KEEP` cho các mục chưa duyệt đang hiển thị và luôn hỏi lại số lượng trước khi lưu. Không có bulk `BLUR` hoặc `CUT`.

Phần tài nguyên trên giao diện cho biết dung lượng video nguồn, ảnh/report, ổ E còn trống và thời lượng/dung lượng preview ước tính. Trong lúc review, hệ thống chỉ đọc ảnh và không chạy AI hay FFmpeg. Kế hoạch duyệt từ xa nằm tại [`REMOTE_REVIEW_PLAN.md`](REMOTE_REVIEW_PLAN.md).

## Lệnh sử dụng

```powershell
.\scripts\run.ps1 build-review `
  --report .\reports\video-adult\scan.json `
  --report .\reports\video-gore\scan.json `
  --report .\reports\video-violence\scan.json `
  --queue .\reports\video-review\review-queue.json

.\scripts\run.ps1 review-decide `
  --queue .\reports\video-review\review-queue.json `
  --id review-xxxxxxxxxxxx --decision KEEP --note "Không cần sửa"

.\scripts\run.ps1 review-decide `
  --queue .\reports\video-review\review-queue.json `
  --id review-yyyyyyyyyyyy --decision BLUR --region 320 20 1280 130 `
  --note "Quảng cáo đã xác nhận"

.\scripts\run.ps1 build-edit-plan `
  --queue .\reports\video-review\review-queue.json `
  --output .\work\video-edit-plan.json

.\scripts\run.ps1 render-previews `
  --plan .\work\video-edit-plan.json `
  --output-dir .\previews\video-edit-review

.\scripts\run.ps1 approve-previews `
  --plan .\work\video-edit-plan.json `
  --manifest .\previews\video-edit-review\preview-manifest.json `
  --actor user

.\scripts\run.ps1 render-final `
  --plan .\work\video-edit-plan.json `
  --output .\output\video-reviewed.mp4

# Hoặc bỏ trần dung lượng; các kiểm tra kỹ thuật khác vẫn giữ nguyên
.\scripts\run.ps1 render-final `
  --plan .\work\video-edit-plan.json `
  --output .\output\video-reviewed-unlimited.mp4 `
  --no-output-size-limit
```

`BLUR` cần một vùng pixel cụ thể. Làm mờ toàn khung chỉ được chấp nhận khi truyền `--full-frame`. `CUT` tạo preview nối vài giây trước và sau đoạn bị bỏ để kiểm tra hình và âm thanh. Các file preview chịu chính sách retention và được xóa sau khi không còn cần thiết.

Vùng blur chữ được tính từ hợp các bounding box OCR theo thời gian của từng video; chiều cao không phải hằng số toàn hệ thống. Hộp một hàng chữ có thể thu phần đệm dưới theo tỷ lệ. Banner chạy hết bề ngang dùng `vertical_only`: alpha phủ kín trái/phải và chỉ feather trên/dưới. Kết quả được khóa trong edit plan sau review. `review-decide --start-seconds ... --end-seconds ...` lưu biên phát hiện gốc và biên người dùng sửa để các CUT liên tục không bị mất dấu. Mặc định manifest preview phải chứa đủ mọi operation. Chỉ khi người dùng chủ động yêu cầu kiểm tra mẫu mới dùng `render-previews --operation-id ...` và `approve-previews --allow-sampled`; approval sẽ ghi `scope: SAMPLED` cùng danh sách operation đã xem.

## Điều kiện an toàn

- Tất cả báo cáo trong một queue phải trỏ tới cùng video, cùng thời lượng và cùng checksum nếu báo cáo có checksum.
- Queue và báo cáo phải nằm trong `reports`; edit plan, preview và dữ liệu xử lý phải nằm trong project trên ổ E.
- Edit plan bị chặn khi còn `null` hoặc `NEEDS_MORE_CONTEXT`.
- Preview giữ âm thanh, không sửa file nguồn và không mở quyền final export.
- Chỉ preview có trạng thái `APPROVED` mới mở quyền final export.
- Bản xuất mặc định nhắm khoảng 3,3 GB và có giới hạn cứng 3.500.000.000 byte. Review UI cho phép từng video đổi sang trần GB tùy chỉnh hoặc không giới hạn. Chế độ không giới hạn bỏ riêng size ceiling; kiểm tra dung lượng trống và mọi validation khác vẫn bắt buộc.
- Bản xuất phải có hình, tiếng, thời lượng đúng với tổng đoạn đã cắt và checksum nguồn không đổi.
