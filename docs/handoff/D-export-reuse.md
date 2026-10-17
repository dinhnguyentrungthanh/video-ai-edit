# D, dùng lại bản xuất có cùng operation sau khi dựng lại (2026-10-17)

Nhánh `fix/export-reuse-same-ops` từ `main` `376e352` (G16), worktree `temp\wt-export-reuse`. Chưa gộp, chưa push.
Việc nhỏ điều phối giao sau khi người dùng thử (c) trên máy thật.

## 1. Lỗi

Job 143: người dùng bấm "Dựng lại danh sách duyệt (giữ quyết định)". Revision 4 giữ 22/22 quyết định, 0 thẻ chờ,
READY_TO_EXPORT. Ba operation của revision 4 render y hệt bản xuất đang có (`…-aa426512-24b2b5b2-reviewed.mp4`,
revision 3): cùng loại, thời gian, vùng, edge mode, cùng thứ tự, size policy mặc định. Chỉ `id` khác: id operation
lấy từ id thẻ (`op-<id thẻ>`), mà id thẻ là hash của bằng chứng, nằm trong thư mục revision, nên đổi sau mỗi lần dựng.

`export_identity.RENDER_FIELDS` có `"id"`, và trường này dùng cho cả hai việc:
- đặt tên: revision 4 tính ra `…-aa426512-fa3833a2-reviewed.mp4`, nên không thấy file cũ;
- so nội dung: `manifest_problem` trả `older` dù thấy file.

Hệ quả: bấm "Xuất video" thì phải mã hóa lại 5–8 phút, và trong lúc đó "Lưu trữ" và "Xóa video gốc" từ chối.

## 2. Kế hoạch (trước khi viết mã)

### So nội dung, bỏ id
`manifest_problem` so operation của manifest với operation hiện tại mà không tính `id`. Các trường còn so: type,
start, end, vùng, edge mode. Mọi kiểm tra khác giữ nguyên: COMPLETED, cùng SHA-256 nguồn, nguồn không bị sửa, đã giải
mã đủ, đúng kích thước, file thường (không link), không phải chính file nguồn, đường dẫn manifest ghi đúng file.

So **theo thứ tự**, không theo tập hợp. Lý do:
- `approved_operations` xếp operation theo `start_seconds`, sort ổn định, nên hai operation cùng giờ bắt đầu giữ thứ tự
  của thẻ.
- Renderer giữ thứ tự đó khi lọc blur trùng (`_prune_redundant_blurs`) và khi xếp chuỗi filter (`blur_layout`).
- Một vùng nằm đè hoặc sát vùng trước sẽ đọc hình đã làm mờ của vùng đó (cover lấy màu từ quanh vùng), nên đổi thứ tự
  có thể đổi pixel.
- So theo tập hợp có thể nhận một bản render khác. So theo thứ tự thì nặng nhất chỉ là xuất lại khi thứ tự đổi.
  Thứ tự thẻ đã là nội dung: ưu tiên, giờ bắt đầu, nhóm.

### Tên file giữ nguyên
Tên vẫn tính như cũ (IDENTITY_VERSION 2, có `id`). Không đổi tên của bản xuất mới, vì:
- đổi tên thì file `work\…-edit-plan.json` và `work\…-export-job.json` của một lần xuất đang chạy hay đang chờ sẽ bị
  mất dấu sau khi cập nhật;
- phần dò bên dưới vẫn cần, để tìm các bản xuất v2 hiện có.

### Dò các bản xuất khác của cùng video
`export_identity.other_exports(root, queue)` liệt kê các file trong `output\`:
- tên có dạng `<slug>-<sha8>-<8 hex>-reviewed.mp4` của chính video nguồn;
- không phải tên v2 hay tên legacy của danh sách hiện tại.

Một file như vậy được nhận khi thỏa đủ ba điều:
1. Manifest của nó qua `manifest_problem`, kể cả phép so nội dung.
2. Tên file đúng bằng tên v2 tính lại từ operation trong chính manifest đó, với video nguồn và size policy của danh
   sách hiện tại (`named_by_manifest`). Manifest không ghi size policy (chỉ ghi `maximum_bytes`), nên chỉ tên file
   chứng minh được bản đó xuất cùng policy.
3. Video nguồn có SHA-256 hợp lệ. Tên `nohash` không bao giờ được dò.

Hệ quả của điều 2: bản xuất đặt tên legacy (hash của quyết định) của một revision **khác** không được dò, vì không tính
lại được tên đó từ manifest. Bản xuất legacy của chính danh sách hiện tại vẫn tìm theo tên như trước.

### Nơi dùng (đồng nhất)
- `verified_export` (`review_workflow.existing_review_export`): dùng cho "Xuất video" của Control Center, trang duyệt
  riêng và `export_status`. Thử tên của mình, tên legacy, rồi mới đến các bản dò được.
- `source_cleanup._exported_facts`: dùng cho "Xóa video gốc", và cho "Lưu trữ" qua `assess_job`.
  - Khi không tìm thấy hay không chứng minh được bản theo tên của danh sách, mới thử các bản dò được.
  - Không có bản dò nào được chứng minh thì báo lý do từ chối cũ (MOVED, STALE, OLDER…), để lời báo không đổi.
  - Bản được nhận là bản mà thao tác xóa manifest (xóa gốc) hoặc chuyển vào Thùng rác (lưu trữ) sẽ dùng.
- `job_import`: thêm các bản dò được vào `expected_outputs`, áp cùng điều 2. Một job READY_TO_EXPORT có bản xuất render y
  hệt sẽ về COMPLETED khi Control Center khởi động, như với một bản đúng tên.
- `control_center.finalize`: không đổi. Nó gọi `existing_review_export`; khi có bản thì về COMPLETED và không render.

### Không đổi
- Khóa cache quét: 10/10 phải giống `376e352`.
- Detector, ngưỡng, thiết lập encode, byte của bản xuất mới và tên của bản xuất mới.
- Không ghi, không sửa file nào trong `output\`, kể cả manifest cũ.
- `scheduler.py` và `export_lanes.py`.

### Cổng
- Test đơn vị:
  - cùng nội dung, khác id: dùng lại;
  - đổi vùng, thời gian, edge mode, size policy hay thứ tự: không dùng lại;
  - tên v2 và tên legacy của danh sách hiện tại vẫn tìm được;
  - bản không có manifest chứng minh, hay có tên không khớp manifest: không dùng lại;
  - sau khi dựng lại mà không đổi gì: "Xuất video" về COMPLETED không render, lưu trữ và xóa gốc nhận bản đó;
  - `job_import` nhận bản đó.
- Test liên quan: export_identity, final_renderer, source_cleanup (+http), source_archive, job_import, job_delete,
  review_rebuild, control_center.
- Khóa cache quét.
- Kiểm thật trên Control Center thử (gốc tạm, cổng riêng), chép job 143:
  - danh sách duyệt revision 3, bản xuất `…-24b2b5b2-…` cùng manifest, hard link hoặc bản sao của video gốc;
  - bấm dựng lại, rồi "Xuất video";
  - phải xong ngay, không có tiến trình render, job COMPLETED, lưu trữ và xóa gốc nhận video;
  - không gọi Control Center thật.
