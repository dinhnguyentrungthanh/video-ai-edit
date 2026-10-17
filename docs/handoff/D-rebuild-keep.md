# D, nút "Dựng lại danh sách duyệt (giữ quyết định)" (2026-10-17)

Nhánh `feat/rebuild-keep-decisions` từ `fix/review-preview-blurs` `b9b7e23` (worktree `temp\wt-rebuild-keep`).
Chưa gộp, chưa push. Việc (c) của điều phối, mục 7 trong `docs/handoff/D-ticker.md`.

## 1. Vì sao

"Chạy lại" tạo revision mới: các bước quét lấy lại từ cache, nhưng danh sách duyệt mới không mang quyết định cũ. Sau
mỗi lần sửa bộ dựng danh sách, người dùng phải duyệt lại 25–30 thẻ mỗi tập.
`build_review_queue(keep_decisions=True)` (`review_decision_carry`) đã giữ được quyết định khi dựng lại, nhưng chỉ
dựng tại chỗ và chưa có nút.

## 2. Kế hoạch (trước khi viết mã)

### Ngữ nghĩa: (A) revision mới
Nút chạy đúng đường của "Chạy lại": cùng phạm vi quét, revision mới, các bước quét lấy từ cache nếu khóa không đổi.
Chỉ khác ở bước cuối `build_review`: danh sách mới mang quyết định của revision đang dùng, theo luật của
`review_decision_carry`.

Lý do chọn (A) chứ không (B) dựng tại chỗ:
- Revision cũ (thư mục báo cáo và `review-queue.json`) giữ nguyên để đối chiếu; (B) ghi đè danh sách đang dùng.
- Dùng lại toàn bộ cơ chế của bộ lập lịch: hàng đợi, Dừng/Tiếp tục, lỗi, khôi phục sau khi tắt máy, cache. (B) phải
  chạy ngoài bộ lập lịch, với khóa và trạng thái riêng.
- Danh sách mới dựng sạch như "Chạy lại". Thẻ cũ chưa quyết định không quay lại; đây là giới hạn của cách dựng tại chỗ
  ghi trong `D-ticker.md` mục 4.
- Giá phải trả: preflight băm lại video gốc, và cache chép lại kết quả quét vào thư mục revision mới (vài phút, không
  dùng GPU khi khóa không đổi). Nếu mã quét đã đổi thì bước đó quét lại thật, như "Chạy lại".

### Đường đi
1. Control Center (`/api/jobs/<id>/rebuild-review`), trong khóa thao tác job:
   - kiểm trạng thái (mục 3);
   - đọc danh sách đang dùng (phải đọc được và đúng video nguồn);
   - tạo thư mục revision mới `reports/jobs/<job_key>-run-<giờ>`;
   - ghi `decision-carry-request.json` vào thư mục đó (revision và SHA-256 của danh sách cũ);
   - rồi mới gọi `scheduler.rerun(job_id, pipeline_key=…)`.
   Rerun bị từ chối thì xóa yêu cầu và thư mục vừa tạo.
2. Bước `build_review` vẫn chạy lệnh `build-review` như cũ (`cli.py` nằm trong khóa cache quét nên không sửa).
   `build_review_queue` thấy yêu cầu cạnh `queue_path` thì:
   - đọc danh sách cũ;
   - kiểm SHA-256, cùng video nguồn và nằm trong `reports`;
   - mang quyết định bằng `carry_review_decisions`;
   - ghi `decision_carry` (thêm revision cũ) và một dòng `REBUILD_KEEP_DECISIONS` vào `audit_log`.
   Yêu cầu sai hay danh sách cũ đã đổi thì bước dừng lỗi, không bao giờ dựng danh sách trống thay vào.
3. Bộ lập lịch kích hoạt revision mới như mọi lần. Có đủ quyết định thì Sẵn sàng xuất, không thì Chờ duyệt.

### Đụng `scheduler.py` (session A1d đang sửa file này)
Chỉ thêm tham số `pipeline_key` (mặc định None) vào `Scheduler.rerun`: 1 tham số và 1 dòng.
- Lý do: tên thư mục revision mới phải biết trước khi job vào hàng đợi, để yêu cầu mang quyết định nằm sẵn ở đó trước
  mọi bước.
- Cách không đụng file thì phải ghi yêu cầu sau khi job đã vào hàng, nghĩa là đua với bộ lập lịch.
- Khi gộp với A1d, xung đột nếu có chỉ ở chữ ký `rerun`.

### Trạng thái được phép
- **Được:** Chờ duyệt (WAITING_REVIEW), Sẵn sàng xuất (READY_TO_EXPORT), Hoàn tất (COMPLETED), và job phải có danh
  sách đang dùng.
- **Từ chối:**
  - đang chạy, trong hàng đợi, có lệnh xuất chờ hoặc đang xuất;
  - nguồn bị khóa vì đã dọn hoặc đang lưu trữ (`source_archives` PENDING/ARCHIVED/RESTORING);
  - không còn video gốc;
  - Tạm dừng, Lỗi, Gián đoạn hoặc Đã hủy.
- **Job COMPLETED** quay về duyệt. Bản xuất cũ vẫn ở `output`. "Lưu trữ" và "Xóa video gốc" tự từ chối vì chỉ nhận job
  COMPLETED hoặc SKIPPED (`source_cleanup.assess_job`), cho tới khi xuất lại. Nếu quyết định cho ra đúng các thao tác
  cũ, "Xuất video" dùng lại bản xuất cũ, vì manifest của nó chứng minh.
- **Job SKIPPED (đề xuất):** từ chối, kèm hướng dẫn "Bấm “Mở lại để xuất” trước rồi dựng lại". Dấu Bỏ qua là một quyết
  định của người dùng, nên nút không lặng lẽ xóa nó như "Chạy lại".

### Kết quả cho người dùng
- Hộp duyệt cảnh của revision dựng lại hiện một dòng tóm tắt (từ `decision_carry`):
  - số quyết định giữ trên thẻ mới;
  - số quyết định không mang sang thẻ mới được nên giữ nguyên trên thẻ cũ, kèm lý do: quyết định cũ khác nhau cho
    cùng một thẻ mới; KEEP/CẮT không chuyển sang thẻ cho thấy nhiều hơn; làm mờ theo khoảnh khắc nằm ngoài thẻ mới;
    không còn thẻ mới nào trùng;
  - số thẻ chờ duyệt.
- Lý do đếm trong `review_decision_carry` (chỉ thêm vào phần tóm tắt, cách mang không đổi).

### Không đổi
Detector, ngưỡng, khóa cache quét (`cli.py`, `run.ps1`, `env.ps1` không đụng), cách mang quyết định, quyết định đã có,
bản xuất.
