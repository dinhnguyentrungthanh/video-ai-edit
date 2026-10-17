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
   sách hiện tại (`other_export_problem`). Manifest không ghi size policy (chỉ ghi `maximum_bytes`), nên chỉ tên file
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

## 3. Đã làm

Commit `e70af07` (kế hoạch), `ef837e9` (mã và test).

- `export_identity.py`:
  - `RENDER_FIELDS` bỏ `id`; `NAME_FIELDS` = `id` + `RENDER_FIELDS`. `render_identity` vẫn lấy `NAME_FIELDS`, nên tên
    file của mọi bản xuất (cũ và mới) không đổi.
  - `same_render(left, right)`: hai identity bằng nhau khi bỏ `id`, so theo thứ tự. `manifest_problem` dùng hàm này.
  - `other_exports(root, queue, render)`: các file thường trong `output\` có tên
    `<slug>-<sha8>-<8 hex thường>-reviewed.mp4` của chính video nguồn, xếp theo tên.
    - Bỏ qua tên v2 và tên legacy của danh sách hiện tại.
    - Nguồn không có SHA-256 hợp lệ thì trả rỗng.
    - Chỉ đọc tên, không đọc file, không ghi gì.
  - `other_export_problem(...)`: `manifest_problem` cộng điều kiện tên. Tên tính lại từ operation của manifest, với nguồn
    và size policy của danh sách hiện tại, phải trùng tên file (so `normcase(abspath)`); sai thì trả mã mới
    `PROBLEM_NAME`.
  - `verified_export`: thử tên của mình, rồi tên legacy, rồi mới đến `other_exports`. Lỗi đọc của một file chỉ bỏ qua
    file đó, như trước.
- `source_cleanup._exported_facts`:
  - thử các bản theo tên như cũ;
  - không bản nào được chứng minh thì thử `_other_export_facts`;
  - vẫn không có thì báo đúng lý do cũ (lý do của file đầu tiên, hoặc MOVED, STALE, NO_OUTPUT).

  `_parse_queue_facts` ghi thêm `export_queue` (nguồn và size policy) để dò. "Lưu trữ" dùng `assess_job` nên cũng nhận
  bản đó, và chuyển đúng file đó cùng manifest vào Thùng rác.
- `job_import`:
  - `other_exports` của danh sách đang dùng được thêm vào nơi tìm;
  - một file chỉ có trong danh sách dò phải qua `other_export_problem`;
  - một job READY_TO_EXPORT có bản xuất render y hệt về COMPLETED khi Control Center khởi động.
- `review_workflow.existing_review_export`: chỉ sửa docstring. `control_center.finalize` không đổi.
- Không sửa `scheduler.py`, `export_lanes.py`, renderer hay detector.

## 4. Test và review mã

- Test mới:
  - `test_export_identity`:
    - lớp `OtherRevisionExportTests`, 9 test:
      - cùng render khác id: dùng lại, và `output\` không đổi;
      - đổi vùng, start, end, edge mode, CUT, detected intervals, thêm một blur, thứ tự hay size policy: không dùng lại;
      - policy khác mặc định: chỉ dùng lại dưới đúng policy đó;
      - mười kiểu manifest không chứng minh;
      - file bị đổi tên;
      - chỉ tên của chính nguồn được dò;
      - tên của mình và tên legacy vẫn được thử trước;
      - tên legacy của revision khác không được dò;
      - so manifest bỏ id nhưng giữ thứ tự;
    - `FinalizeReuseTests`, thêm 2 test: dựng lại cùng render thì COMPLETED, không render, `output\` không đổi; khác
      render thì vẫn xếp lượt render.
  - `test_source_cleanup`, thêm 2 test:
    - dựng lại cùng render: xóa gốc nhận bản cũ, kể cả khi ở tên riêng của danh sách mới có một file không manifest, và có
      một file đặt tên như bản xuất nhưng manifest hỏng;
    - "Xóa video gốc" xóa đúng manifest của bản cũ; file `.mp4` ở lại;
    - khác edge mode, start hay policy, hoặc bản cũ bị dời đi: từ chối với lý do như main.
  - `test_source_archive`, thêm 1 test: "Lưu trữ" nhận và chuyển đúng bản cũ cùng manifest vào Thùng rác (giả).
  - `test_job_import`, thêm 3 test:
    - cùng render: COMPLETED, artifact là bản cũ;
    - khác render: vẫn READY_TO_EXPORT;
    - bản xuất dưới policy khác hay bị đổi tên: không về COMPLETED. Thử đột biến bỏ phép kiểm tên thì test này hỏng.
  - `test_review_rebuild`, thêm 1 test cả luồng: dựng lại, "Xuất video" về COMPLETED không render, xóa gốc và lưu trữ
    nhận bản cũ. Bản quét giả của fixture không có track và `input_sha256`, nên test đổi id thẻ và đặt SHA-256 như một
    bản dựng thật.
- Lúc RED (trước khi sửa mã): các test xóa gốc, lưu trữ, import và luồng dựng lại đều hỏng đúng chỗ.
- Chạy cùng nhau, đạt 650 test (1 bỏ qua có sẵn): export_identity, final_renderer, export_encoder, source_cleanup (+http),
  source_archive (+http), job_import, job_delete, review_rebuild, review_decision_carry, control_center, scheduler,
  review_workflow, export_guards, export_lanes.
- Khóa cache quét: 10/10 stage giống `376e352` (`build_code_manifest`, so với cây `git archive 376e352` trong `temp`).
  Không file nào bị sửa nằm trong khóa của 10 stage.
- Review mã (agent, chỉ đọc, `376e352..ef837e9`):
  - không có CRITICAL, HIGH hay MEDIUM;
  - agent tự kiểm: tên mới không đổi, renderer không đọc `id`, mọi kiểm tra của manifest giữ nguyên, không vượt thư mục,
    lý do từ chối giữ nguyên.
  - Ba điểm LOW đã xử lý:
    - L1: `source_cleanup` dò bằng một danh sách rút gọn (không có thẻ), nên có thể liệt kê tên legacy của chính danh sách
      đó như một bản "khác" (vô hại: tên legacy không bao giờ qua điều kiện tên). Đã sửa: bỏ các tên của danh sách trước
      khi dò.
    - L2: danh sách dựng lại hay chạy lại không mang `export_size_policy`. Ghi ở mục 6.
    - L3: thiếu test cho phép kiểm tên khi import, cho manifest hỏng lúc dò, và cho tên riêng có file hỏng. Đã thêm.

## 5. Kiểm thật trên Control Center thử

Script `docs/evidence/export-reuse/reuse_check.py`, bằng chứng `docs/evidence/export-reuse/check-2026-10-17/`
(`result.json`, `result-previews.json`, `result-import.json`, README). Không có ảnh chụp.

- Gốc tạm `temp\er-check-<giờ>`, Control Center thử cổng 8798.
  - Chép job 143 như trước khi người dùng bấm: revision 3, thư mục báo cáo của nó, bản xuất `…-aa426512-24b2b5b2-…`
    cùng manifest, bản sao (không link) video gốc, các mục cache quét.
  - Thư mục cài chỉ được đọc (database ở chế độ chỉ đọc). Không route nào của Control Center thật được gọi, `output\`
    thật không bị động tới. Hàng đợi thật trống suốt lượt.
- Trước khi bấm: COMPLETED, revision 3; "Xóa video gốc" và "Lưu trữ" đều nhận video.
- Bấm "Dựng lại danh sách duyệt" (mã `ef837e9`). Sau 344 s:
  - revision 4, READY_TO_EXPORT; 22/22 quyết định mang sang, 0 thẻ chờ, 0 xung đột;
  - đúng như máy thật: `text` quét lại trên GPU, vì root thử không dùng được cache của thư mục cài;
  - 3 operation render y hệt manifest của bản xuất, id khác. Tên riêng của danh sách mới khác tên bản xuất, và không có
    file nào ở tên đó.
- Bấm "Xuất video":
  - HTTP 200, `COMPLETED` sau 0,03 s; bản xuất là bản cũ;
  - không có yêu cầu render, không có stage render;
  - `output\` không đổi so với lúc chép, `work\` không đổi vì lần xuất;
  - "Xóa video gốc" và "Lưu trữ" nhận video.
- Mã `f8ddc38` (sau phần sửa theo review), trên cùng gốc:
  - `previews`: COMPLETED, cả hai thao tác nhận video;
  - `import`: đưa job về READY_TO_EXPORT (như job 143 thật bây giờ) rồi chạy bước import lúc khởi động. Job về
    COMPLETED, bản cũ được ghi làm bản xuất.
- Gốc thử đã xóa bằng `remove`.

## 6. Giới hạn

- Bản xuất đặt tên legacy (hash của quyết định) của một revision khác không được dò: tên đó không tính lại được từ
  manifest. Bản legacy của chính danh sách hiện tại vẫn tìm theo tên như trước.
- Danh sách mới của "Chạy lại" và "Dựng lại danh sách duyệt" không mang `export_size_policy`. Với một bản xuất dưới
  policy khác mặc định (không giới hạn, hay tùy chỉnh GB):
  - "Xóa video gốc", "Lưu trữ" và bước import tính tên dưới policy mặc định, nên từ chối (đóng an toàn);
  - "Xuất video" vẫn dùng lại bản đó nếu người dùng chọn lại đúng policy cũ trong hộp xuất.
- So theo thứ tự: nếu một lần dựng đổi thứ tự hai thẻ cùng ưu tiên, cùng giờ bắt đầu, cùng nhóm, thì phải xuất lại.
- Một bản xuất được dùng lại không đổi tên. Tên của nó vẫn mang id của revision cũ, còn artifact `final_output` cũ vẫn
  trỏ đúng file.

## 7. Bước tiếp theo

1. Điều phối gộp nhánh. Thử `merge-tree` sau cùng ở mục báo cáo.
2. Sau khi gộp và khởi động lại Control Center thật:
   - job 143 (đang READY_TO_EXPORT) sẽ về COMPLETED ngay lúc khởi động, nhờ bước import;
   - nếu chưa về, bấm "Xuất video": phải xong ngay, không render;
   - rồi "Lưu trữ" hay "Xóa video gốc" nhận bản `…-24b2b5b2-…`.
