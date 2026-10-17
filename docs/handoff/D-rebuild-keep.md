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

## 3. Đã làm

Commit `0b983f4` (backend), `1f09e1d` (Dashboard V2), `3dbfeee` (sửa theo review mã, mục 4).

### Backend
- `src/biliflow/review_rebuild.py` (mới):
  - `rebuild_refusal`: thứ tự kiểm là SKIPPED, có lệnh xuất chờ/đang xuất, Visual AI Audit đang chạy (nó ghi lại danh
    sách đang dùng khi xong), đang chạy hoặc trong hàng đợi, trạng thái khác ba trạng thái được phép, không có danh sách
    đang dùng, không còn video gốc. Khóa nguồn (đã dọn, lưu trữ) do
    `ControlCenter._refuse_source_lock` kiểm trước, như các thao tác khác.
  - `carry_request`: đọc danh sách đang dùng, kiểm cùng video nguồn (đường dẫn, và SHA-256 khi cả hai có), ghi lại
    revision, đường dẫn tương đối và SHA-256 của danh sách cũ, số quyết định.
  - `new_revision_key`: `<job_key>-run-<giờ>`, thêm `-2`, `-3`… nếu thư mục đã có.
  - `write_carry_request`: tạo thư mục mới (không bao giờ dùng lại thư mục có sẵn), ghi qua file tạm rồi `os.replace`.
    `discard_carry_request` xóa yêu cầu và thư mục khi rerun từ chối.
  - `read_carry_request`: không có file thì None (dựng sạch như cũ). Từ chối (ValueError, trước khi ghi danh sách mới):
    file là link hoặc không phải file, JSON hỏng, sai phiên bản, `from_queue` ra ngoài `reports`, trùng danh sách mới
    hoặc không tên `review-queue.json`, danh sách cũ đọc không được, SHA-256 khác ("danh sách duyệt cũ đã đổi sau khi
    bấm dựng lại"), danh sách cũ hỏng.
- `review_workflow.build_review_queue`: đọc yêu cầu cạnh `queue_path`; danh sách cũ phải cùng video nguồn
  (`QUEUE_MISMATCH`). Thẻ cũ chưa quyết định không quay lại (dựng sạch); quyết định cũ đi qua `carry_review_decisions`
  như `keep_decisions`. `decision_carry` thêm `from_revision`, `from_queue`; `audit_log` của danh sách mới là
  `audit_log` cũ cộng một dòng `REBUILD_KEEP_DECISIONS`. `decision_carry` ghi cả `from_sha256`: lần dựng sau trên chính
  danh sách đã dựng từ yêu cầu này (`carry_applied`) lấy quyết định từ danh sách đó, nên quyết định người dùng đã đổi
  sau đó không bao giờ bị quyết định của revision cũ đè lại.
- `review_decision_carry`: phần tóm tắt thêm `restored_reasons` (`conflict`, `not_wider`, `moments_outside`,
  `no_match`) cho mỗi quyết định trả về nguyên trên thẻ cũ. Cách mang không đổi.
- `control_center.py`: route `POST /api/jobs/<id>/rebuild-review` (thân bị bỏ qua) đi qua cùng kiểm tra Host, token,
  Origin và CSRF như các route job khác. `rebuild_review` chạy trong `_REVIEW_QUEUE_IO` và `job_action_lock`, ghi sự kiện
  `REVIEW_REBUILD_QUEUED` và trả `rebuild: {from_revision, from_queue, decisions, pipeline_key}`. Rerun lỗi sau khi đã
  xếp job vào hàng với revision mới thì yêu cầu được giữ lại cho bước dựng của revision đó.
- `scheduler.py`: chỉ thêm `pipeline_key: str | None = None` vào `rerun` và dòng
  `pipeline_key = pipeline_key or f"{job['job_key']}-run-{stamp}"`. Lúc gộp với A1d, xung đột nếu có chỉ ở đây.
- `phone_access.py`: điện thoại được gửi `rebuild-review` như `rerun`.

### Dashboard V2
- `contracts.js`: thao tác `rebuild`, nhãn "Dựng lại danh sách duyệt (giữ quyết định)", ngay sau "Chạy lại kiểm tra"
  cho video Chờ duyệt, Sẵn sàng xuất, Hoàn tất (có danh sách đang dùng). Tắt khi thiếu video gốc, đang lưu trữ/đã dọn hoặc
  có lệnh xuất hoặc Visual AI Audit đang chạy; video Đã bỏ qua thấy nút tắt với lý do "bấm “Mở lại để xuất” trước".
  Có trên điện thoại như "Chạy lại".
- Hộp xác nhận (`REBUILD_CONFIRM`) liệt kê: quyết định cũ được giữ, chỉ phải duyệt thẻ mới hoặc thẻ đổi; bản xuất cũ vẫn
  ở `output`, không bị xóa; phải xuất lại, tới lúc đó "Lưu trữ" và "Xóa video gốc" chưa dùng được; video vào hàng đợi
  như "Chạy lại", bước quét dùng lại kết quả đã lưu khi mã quét không đổi.
- Sau khi gửi, toast nói số quyết định giữ và revision cũ. Hộp duyệt cảnh của danh sách dựng lại có dòng `.rv-carry`
  (`review-core.js` `carryNote`): số quyết định mang sang thẻ mới trên tổng, số giữ nguyên trên thẻ cũ kèm lý do, số
  thẻ chờ duyệt (và số thẻ để trống vì quyết định cũ trái nhau), và câu "Không có quyết định nào được tự đặt".
- `app.js` còn 796 dòng (trần 800): hộp xác nhận dùng lại `simpleConfirm`, chỉ thêm cách hiện danh sách.

## 4. Kiểm

- Python (gốc tạm trong `temp`):
  - `tests/test_review_rebuild.py` (13): bảng trạng thái được/không được; yêu cầu ghi và đọc lại, tên trùng giây,
    thư mục có sẵn, danh sách cũ đổi, ra ngoài `reports`, sai phiên bản; danh sách mới giữ mọi quyết định cũ và không tự
    đặt quyết định nào; yêu cầu hỏng dừng bước trước khi ghi; không có yêu cầu thì dựng sạch; job đã xuất quay về duyệt,
    "Xóa video gốc" và "Lưu trữ" từ chối lúc chờ và sau khi dựng, bản xuất cũ còn nguyên; mọi lần bị từ chối không ghi gì
    (kể cả khi rerun từ chối thì yêu cầu bị thu lại); route điện thoại giống `rerun`; AI Audit đang chạy thì từ chối;
    lần dựng sau trên danh sách đã dựng giữ quyết định đổi sau đó (test này hỏng khi bỏ phần sửa); rerun lỗi sau khi
    đã xếp hàng thì yêu cầu còn.
  - `tests/test_review_decision_carry.py` (15, thêm lý do), `tests/test_control_center.py` (route thật qua HTTP với token;
    403 khi thiếu token hoặc Host lạ).
  - Sau `3dbfeee`, chạy cùng nhau: `test_review_rebuild`, `test_review_decision_carry`, `test_control_center`,
    `test_scheduler`, `test_source_cleanup`, `test_source_archive`, `test_review_workflow`, `test_dashboard_v2_review`,
    `-frontend`, `-contract`, `-phone`: 432 test đạt. Các test Dashboard và điện thoại khác cũng đạt; chữ ký endpoint
    trong `test_dashboard_v2_review` cập nhật cho một endpoint mới.
  - Full suite (3.528 test, 42 phút): đạt, trừ 28 test cần video trong thư mục `input` của worktree
    (`test_job_pipeline`, `test_job_ocr_option`; đạt 35/35 khi đặt một clip tổng hợp 2 s vào đó, clip đã xóa) và 1 test
    đo thời gian của `test_hardware_manager` (`LimitTests`, chạy riêng đạt 3/3).
- JS: `verify.cjs` 51, `verify-adapter.cjs` 42, `verify-review.cjs` 46 (thêm `carryNote` và đối chiếu tên lý do với
  `RESTORED_REASONS` của Python), `browser-check-rebuild.cjs` 6 (mới: nút, hộp xác nhận, POST, toast, video bỏ qua, dòng
  tóm tắt ở 1440 và 390 px), `browser-check-review` 27, `-layout` 13, `-covers` 9, `-write` 19, `-bulk` 13.
  `browser-check.cjs` đạt 8 rồi hết giờ ở R4.3 (dòng 270), giống hệt trên `b9b7e23`: lỗi có sẵn, không do nhánh này.
- Khóa cache quét: 10/10 stage giống `41169fc` (`build_code_manifest`, cây `git archive 41169fc` trong `temp`, đã xóa).
- Review mã (agent, chỉ đọc, `b9b7e23..1f09e1d`): không có CRITICAL hay HIGH. Đã sửa trong `3dbfeee`:
  - MEDIUM: AI Audit đang chạy ghi lại danh sách cũ, làm hỏng SHA-256 và bước dựng lỗi mãi;
  - MEDIUM: yêu cầu nằm lại trong thư mục, một lần dựng tại chỗ sau đó sẽ đè quyết định mới bằng quyết định cũ;
  - LOW: rerun lỗi sau khi đã xếp hàng thì yêu cầu bị xóa.
  Agent cũng kiểm: không có đường vượt thư mục, không XSS, thứ tự khóa đúng, route đi qua cùng kiểm tra Host/token.

## 5. Kiểm thật trên Control Center thử

Script `docs/evidence/rebuild-keep/rebuild_check.py`, bằng chứng `docs/evidence/rebuild-keep/check-2026-10-17/`
(`result.json`, `result-again.json`, README). Ảnh chụp có khung hình và tên phim thật nên ở
`reports\evidence\rebuild-keep\check-2026-10-17\` (không vào Git).
- Gốc tạm `temp\rk-check-<giờ>`, Control Center thử cổng 8797. Chép job 145 (đã xuất, 27 quyết định: KEEP 24, BLUR 2,
  CẮT 1): video gốc, thư mục báo cáo, bản xuất cùng manifest, cache quét. Thư mục cài chỉ được đọc; không route nào của
  Control Center thật được gọi. Hàng đợi thật trống suốt lượt (đọc database ở chế độ chỉ đọc).
- Trước khi bấm: COMPLETED, revision 3, "Xóa video gốc" và "Lưu trữ" đều nhận video.
- Bấm (mã `3dbfeee`): QUEUED, yêu cầu được ghi; bấm lần hai bị từ chối ("đang trong hàng đợi"); cả hai thao tác trên từ
  chối ("Chỉ dọn/lưu trữ được video đã xuất hoặc đã bỏ qua").
- Sau 295 s: revision 4, Chờ duyệt.
  - 27/27 quyết định mang sang thẻ mới, 0 giữ nguyên trên thẻ cũ, 0 xung đột.
  - 26 thẻ có quyết định (KEEP 23, BLUR 2, CẮT 1), mọi quyết định đều có nguồn cũ theo dấu của luật mang.
  - 1 thẻ chờ duyệt: thẻ chữ cố định toàn phim mới, thay thẻ cũ cùng loại mà người dùng đã KEEP. Thẻ mới cho thấy nhiều
    hơn nên luật không mang KEEP sang. KEEP cũ đó trùng với 20 thẻ hội thoại, và các thẻ này vẫn có KEEP của riêng chúng,
    nên nó được đếm là "đã mang".
  - Danh sách cũ, video gốc và bản xuất cũ không đổi. "Xóa video gốc" và "Lưu trữ" vẫn từ chối.
  - Hộp duyệt hiện: "Đã mang 27 / 27 quyết định của revision 3 sang thẻ mới. Còn 1 thẻ chờ duyệt (mới hoặc đổi). Không có
    quyết định nào được tự đặt." (1440 và 390 px). Ngăn chi tiết và hộp xác nhận chụp ở cả hai cỡ, bấm "Hủy", 0 POST.
- Không xuất lại: còn 1 thẻ cần người duyệt, và tác tử không tự đặt quyết định. Nếu quyết định cho ra đúng các thao tác
  cũ, "Xuất video" dùng lại bản xuất cũ (đã có từ trước, `existing_review_export`).
- Cache:
  - Khóa cache quét gồm đường dẫn tuyệt đối của `scripts\run.ps1` của root, nên root thử không bao giờ dùng được mục
    cache của thư mục cài: lượt đầu quét lại `text` trên GPU (đúng thiết kế, không phải lỗi).
  - Bấm lần hai trên cùng root (`again`, revision 4 → 5, 143 s): `text` lấy từ cache (`STAGE_CACHE_HIT`), 26/26 quyết
    định mang sang, vẫn 1 thẻ chờ.
  - `visual_logo` và `localize_logo` chạy GPU ở cả hai lượt: khóa của chúng gồm `state\brand-memory.json`, đã đổi lúc
    22:58 ngày 16/10, sau lần quét cuối của mọi job. Trên root thử, đường dẫn mục cache của hai bước này dài 255–257 ký
    tự, quá giới hạn 247, nên chúng không được lưu (`STAGE_CACHE_SKIPPED`). Trên thư mục cài, đường dẫn ngắn hơn 30 ký tự.
  - Bản checkout của thư mục chính có `config\license_policy.json` xuống dòng CRLF và `scripts\env.ps1` LF, ngược với
    `.gitattributes`. Worktree mới lấy theo `.gitattributes` nên hai file khác byte. Khóa cache hash từng byte, nên script
    lấy hai file này từ thư mục cài khi chỉ khác xuống dòng; khác nội dung thì từ chối. Nếu một lần checkout nào ghi lại
    hai file này trong thư mục chính, mọi khóa cache quét sẽ đổi.
- Root thử đã xóa bằng `remove` (gỡ link trước, không đi vào bên trong).

## 6. Giới hạn

- Dựng lại trên thư mục cài chạy lại mọi bước có khóa đổi. Hiện bộ nhớ thương hiệu đã đổi sau lần quét cuối của mọi job,
  nên `visual_logo` và `localize_logo` sẽ quét lại bằng GPU (khoảng 2–3 phút mỗi tập 40 phút); `text` lấy từ cache.
- Thẻ cũ chưa quyết định không quay lại (dựng sạch như "Chạy lại").
- Nếu danh sách cũ bị đổi sau khi bấm (không còn đường nào trong ứng dụng làm vậy sau khi chặn AI Audit), bước dựng dừng
  lỗi, job ở Lỗi không có danh sách đang dùng; quyết định vẫn nằm trong file cũ nhưng "Thử lại" không cứu được. Chưa làm
  đường nối lại revision cũ.
- Job SKIPPED phải "Mở lại để xuất" trước.

## 7. Bước tiếp theo

1. Điều phối gộp vào G15 cùng A1d. Xung đột có thể có ở `scheduler.py` (chữ ký `rerun`).
2. Sau khi gộp và khởi động lại Control Center thật: người dùng thử nút trên một tập đã xuất, nhìn dòng tóm tắt trong
   hộp duyệt, duyệt thẻ chờ rồi xuất lại.
