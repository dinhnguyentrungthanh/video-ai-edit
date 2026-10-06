# Plan xóa vĩnh viễn video gốc và dọn dữ liệu video

Soạn ngày 2026-10-05 trên nhánh `feat/delete-flow` (tách từ `feat/dashboard-v2` ở `55c6e62`). Người dùng đề xuất luồng
này và đã trả lời 4 câu hỏi (mục 1). Chưa merge, chưa push; người dùng test xong mới merge.

## 0. Tóm tắt

- **"Dọn video gốc" đổi thành "Xóa video gốc"** (video đã xuất hoặc đã bỏ qua, mục "Hoàn tất"):
  - xóa hẳn video gốc trong `input` (không qua Thùng rác);
  - xóa manifest của bản xuất (`output\…-reviewed.mp4.manifest.json`); file `.mp4` giữ nguyên;
  - xóa video đó khỏi BiliFlow: dòng trong DB, thư mục báo cáo của nó trong `reports\jobs`, log của nó.
- **"Xóa video"** cho video đã hủy (xóa luôn video gốc nếu còn trong `input`) và cho video đã mất video gốc. Thao
  tác này chỉ xóa video khỏi BiliFlow và không đụng tới `output`.
- **"Dọn video mất gốc"**: một nút liệt kê mọi video đã mất video gốc để xóa một lần.
- **Luôn giữ:**
  - bản xuất `.mp4`;
  - bộ nhớ logo/studio;
  - bộ nhãn vàng: 3 video #37, #38, #39 bị khóa, không xóa được;
  - thư mục benchmark/regression, kho lưu trữ, model, cache.
- Làm trên PC và, từ 2026-10-06, cả qua chế độ điện thoại (mục 10). Mỗi lần xóa đều có hộp xác nhận liệt kê từng
  video, cảnh báo xóa vĩnh viễn và ô "Tôi hiểu".

## 1. Quyết định của người dùng (2026-10-05)

- Đề xuất:
  > "giờ xóa là xóa luôn video gốc xóa hẳn khỏi máy không còn trong thùng rác và file manifest.json vì dữ lại cũng
  > rất là thừa"
  >
  > "cũng dọn dẹp dữ liệu trong DB và những chỗ liên quan đến những thằng đã bị mất video gốc ngoại trừ thư mục ouput
  > là k được đụng vào"
  >
  > "những video có trạng thái hủy cũng nên có nút xóa luôn để sạch video"
- Câu trả lời cho 4 câu hỏi:
  1. Manifest bị xóa là manifest của bản xuất (`…-reviewed.mp4.manifest.json`), `.mp4` giữ: "đúng rồi bạn, vì nếu
     xoá video còn giữ lại file đó thì cũng thừa đâu có xuất được".
  2. Không cần nhật ký các lần xóa: "không cần bạn".
  3. Nút "Xóa" của video đã hủy xóa luôn video gốc trong `input`: "đúng rồi bạn".
  4. Dọn một lần các video đã mất video gốc: "được bạn".

## 2. Ba thao tác

| | Xóa video gốc | Xóa video (đã hủy) | Xóa video (mất gốc) / Dọn video mất gốc |
|---|---|---|---|
| Áp dụng cho | Đã xuất (bản xuất qua kiểm tra manifest) hoặc đã bỏ qua (bản ghi bỏ qua khớp) | Trạng thái Đã hủy | Video gốc không còn ở đường dẫn đã ghi, không ở kho lưu trữ |
| Video gốc trong `input` | Xóa vĩnh viễn (kiểm SHA-256 trước) | Xóa vĩnh viễn nếu còn (kiểm SHA-256 trước) | Đã mất, không làm gì |
| `output` | Xóa đúng manifest của bản xuất; giữ `.mp4` | Không đụng | Không đụng |
| Dữ liệu video trong BiliFlow | Xóa | Xóa | Xóa |

"Dữ liệu video trong BiliFlow" gồm:

- **Các dòng của video trong `state/control-center.sqlite3`:**
  - `jobs`, `job_revisions`, `stages`, `artifacts`, `events`;
  - `source_cleanups`, `source_archives`, `recycle_checks`;
  - các cài đặt riêng `pipeline_key:`, `detector_groups:`, `ocr_batch_size:`, `fast_scan:`, `render:`, `skip:` + id.
- **Dòng theo dõi file của watcher:** dòng này không bị xóa, chỉ được đặt lại như "Dọn video gốc" cũ đã làm. Nhờ vậy,
  chép lại video vào `input` thì BiliFlow coi đó là video mới.
- **Thư mục báo cáo của chính video đó:**
  - chỉ các thư mục nằm ngay trong `reports\jobs` có tên là `<job_key>` hoặc `<job_key>-run-…`;
  - không xóa thư mục mà một video khác còn trỏ tới;
  - không xóa thư mục có dấu `.biliflow-benchmark` (bản chạy benchmark);
  - không xóa thư mục là liên kết (symlink/junction).
- **Log của video:** `logs\control-center\job-<id>-*.log`.

Hai thứ còn lại **không xóa**, để chính sách dọn có giới hạn hiện có lo:

- `work\…-edit-plan.json`, khoảng 5 MB, tự xóa sau 7 ngày;
- cache theo SHA-256: `cache\stage-results`, `cache\review-frames`.

## 3. Những thứ được bảo vệ

- **Bộ nhãn vàng** (`annotations\golden\*\segments.json`): video có `job_id` hoặc `sha256` nằm trong danh sách nguồn
  thì không xóa được và nút bị khóa kèm lý do. Hôm nay là #37, #38, #39.
  - `scripts\golden_prefill.py` đọc revision của chính các video này, và chấm detector cần quét lại video gốc của chúng.
  - Không đọc được file `segments.json`: khóa mọi thao tác xóa và nói rõ file nào hỏng.
- **Báo cáo không thuộc `reports\jobs\<job_key>…`:** benchmark, POC, regression, và thư mục báo cáo cũ ở cấp trên của
  6 video nhập từ lịch sử (#1–#6). Xóa #1–#6 chỉ xóa dòng DB và log, còn báo cáo cũ giữ làm bằng chứng.
- **`output`:** không bao giờ xóa `.mp4`. Chỉ "Xóa video gốc" xóa đúng một file manifest của bản xuất đã được kiểm
  (tên kết thúc `-reviewed.mp4.manifest.json`, nằm ngay trong `output`).
- **Bộ nhớ logo/studio** (`state\brand-memory.json`, `state\studio-logo-memory.json`, `state\studio-logo-frames`):
  - không đổi;
  - việc so khớp dùng phash và khung đã lưu trong `state`, không đọc ảnh trong báo cáo đã xóa;
  - riêng đường dẫn ảnh xem trước cũ trong bản ghi có thể trỏ tới thư mục đã xóa; chỉ để tham khảo.
- **`archive\`:** video đang lưu trữ không phải "mất gốc". Muốn dùng lại thì bấm "Khôi phục bản xuất".
- **Video cũ đã "Dọn video gốc" vào Thùng rác mà file vẫn còn trong Thùng rác:** không cho xóa, kèm lý do. Khôi phục
  về `input` hoặc xóa khỏi Thùng rác trước. Hôm nay không có video nào như vậy (21 video đã rời Thùng rác).
- **Video đang bận:** đang chạy, đang chờ, còn lệnh xuất, đang chạy AI Audit hay một thao tác video gốc khác thì bị từ
  chối.
- **Lớp bảo vệ thêm:** hàm xóa chỉ chạy khi thư mục gốc là thư mục cài BiliFlow hoặc nằm trong `temp` của nó (test).
  Code trong một worktree vì thế không thể xóa file của thư mục chính.
  - `input`, `output`, `reports`, `reports\jobs`, `logs`, `logs\control-center` là liên kết (symlink/junction): không
    xóa gì qua đó (review D1, 2026-10-05). Hôm nay máy thật không có liên kết nào trong thư mục cài.
  - Video có dòng "Lưu trữ" PENDING/ARCHIVED/RESTORING hoặc dòng dọn cũ PENDING: hàm xóa dữ liệu từ chối luôn, dù nơi
    gọi đã kiểm.
  - Xóa dòng DB chạy trong một giao dịch; lỗi bất kỳ (không chỉ lỗi SQLite) đều hoàn tác hết.
  - AI Audit chỉ bắt đầu dưới cùng khóa `job_action_lock`: lệnh xóa thấy audit đang chạy, hoặc audit thấy video đã
    bị xóa.
- **Lưu ý cho người dùng (không chặn):**
  - Sau khi xóa manifest, file `.mp4` cũ trong `output` không còn gắn với video nào. Nếu sau này chép lại đúng video
    gốc đó và xuất lại cùng quyết định, BiliFlow sẽ không ghi đè file `.mp4` cũ (báo file đã tồn tại); hãy xóa hoặc đổi
    tên file `.mp4` cũ trước.
  - `rebuild-brand-memory` dựng lại bộ nhớ logo từ các danh sách duyệt còn lại; quyết định của video đã xóa không còn
    để dựng lại (bộ nhớ hiện có thì không đổi).

## 4. Thứ tự an toàn

Mỗi video chạy lần lượt các bước sau:

1. **Kiểm SHA-256, không giữ khóa:** băm video gốc (và bản xuất nếu có) rồi so với số đã ghi; so cả kích thước và
   thời điểm sửa trước/sau.
2. **Làm trong khóa `REVIEW_QUEUE_IO` rồi `job_action_lock`** (đúng thứ tự khóa sẵn có):
   1. kiểm lại toàn bộ điều kiện từ dữ liệu mới đọc;
   2. xóa video gốc (nếu có);
   3. xóa manifest (chỉ với "Xóa video gốc");
   4. xóa thư mục báo cáo và log;
   5. cuối cùng xóa các dòng DB trong một giao dịch.
3. **Khi một bước lỗi:**
   - **Xóa video gốc thất bại** (file đang mở, đổi kích thước…): không thay đổi gì.
   - **Xóa báo cáo/log thất bại:** video gốc đã mất nhưng dòng DB còn giữ. Video hiện là "mất gốc"; bấm "Dọn video mất
     gốc" để làm lại (các bước đều chạy lại được).
   - **Tắt máy hay crash giữa chừng:** kết quả như dòng trên. Không có trạng thái treo.
4. Một lần chỉ một thao tác với file gốc (`SOURCE_FILE_LOCK`, chung với Lưu trữ / Khôi phục / Kiểm tra lại Thùng rác).
   Lúc tắt, BiliFlow chờ thao tác đang chạy xong (`wait_idle`).

## 5. API (PC; điện thoại từ 2026-10-06)

- **`GET /api/source-cleanup/preview?ids=…`** (giữ tên):
  - liệt kê video xóa được và không xóa được, kèm lý do;
  - mỗi video có dung lượng video gốc và dung lượng báo cáo sẽ xóa;
  - không còn thông tin Thùng rác.
- **`POST /api/source-cleanup`** `{job_ids, preview_id, confirm_permanent: true}`: xóa video gốc, manifest và dữ liệu
  như mục 2. Kết quả từng video là `DELETED`, `PARTIAL`, `FAILED` hoặc `NOT_RUN`.
- **`GET /api/job-delete/preview?ids=…`** và **`POST /api/job-delete`** `{job_ids, preview_id, confirm_permanent: true}`:
  "Xóa video" cho video đã hủy và video mất gốc.
- **400** khi thiếu `confirm_permanent: true` (đúng giá trị `true`): trang mở từ trước bản này (hộp thoại cũ còn hứa
  "vào Thùng rác") không xóa được, người dùng phải tải lại trang và đánh dấu "Tôi hiểu".
- **409** khi danh sách đổi (`preview_changed`, kèm danh sách mới) hoặc khi đang có thao tác khác (`busy`).
- **`/api/status`:** mỗi video có thêm hai trường:
  - `protected`: lý do khóa vì bộ nhãn vàng, hoặc null;
  - `delete`: gợi ý "Xóa video" gồm `{eligible, kind: CANCELLED|LOST, reason, size_bytes}`, chỉ có với video đã hủy
    hoặc mất gốc.
- Hai route POST mới lúc đầu nằm trong `PC_ONLY_POSTS`. Từ 2026-10-06 (mục 10) chúng nằm trong
  `PHONE_ALLOWED_POSTS`: điện thoại gửi đúng yêu cầu như PC.

## 6. Giao diện

- **Dashboard V2:**
  - nút "Dọn video gốc" đổi thành "Xóa video gốc" (cả nút chọn nhiều);
  - "Xóa video" có trong menu ⋯ của video đã hủy và video mất gốc;
  - khi có video mất gốc, đầu danh sách hiện "Có N video không còn video gốc · Dọn video mất gốc";
  - hộp xác nhận liệt kê từng video, phần sẽ xóa, phần giữ lại và tổng dung lượng được giải phóng;
  - nút xác nhận chỉ bật sau khi tick "Tôi hiểu: …";
  - video thuộc bộ nhãn vàng có nút bị khóa kèm lý do.
- **Trang Control Center cũ (`/`):** hộp "Dọn video gốc" đổi thành "Xóa video gốc" (đúng hành vi mới, có ô "Tôi hiểu");
  có nút "Xóa video" cho video đã hủy và video mất gốc. Trang duyệt cũ `/review/<id>` giữ trùng byte (D2).
- **Không đổi:** "Lưu trữ" và "Khôi phục bản xuất" (bản xuất vẫn vào Thùng rác như cũ); "Kiểm tra lại Thùng rác" vẫn
  có cho bản ghi cũ.

## 7. Test (chỉ thư mục tạm trong `temp`, không bao giờ đụng dữ liệu thật)

- **`tests/test_job_purge.py`:**
  - bộ nhãn vàng (đọc được, hỏng, không có);
  - chọn thư mục báo cáo (theo tên, theo bản ghi, benchmark, liên kết, thư mục của video khác);
  - xóa dòng DB (mọi bảng, khóa ngoại, watcher, video khác không đổi);
  - xem trước và thực hiện "Xóa video" (đã hủy còn/mất gốc, SHA lệch, bận, đang lưu trữ, vẫn còn trong Thùng rác,
    lỗi giữa chừng → PARTIAL, tắt máy → NOT_RUN).
- **`tests/test_source_cleanup*.py`:** viết lại phần thực hiện theo hành vi xóa vĩnh viễn. Phần đọc bản ghi cũ (gợi ý,
  đối soát lúc khởi động, kiểm tra lại Thùng rác) giữ nguyên.
- **Route HTTP:** gồm cả chặn trên điện thoại; trường mới trong `/api/status`.
- **Kiểm tra JS:**
  - `verify*.cjs`;
  - harness trang cũ;
  - các mã băm và danh sách route đã ghim (cập nhật có chủ đích).
- Cả bộ test chạy với `TEMP`/`TMP` trỏ vào `E:\DungChung\BiliFlow\temp`.

## 8. Các đợt làm

- **D0:** plan này và đổi `AGENTS.md`. Xong: `22216af`.
- **D1:** backend gồm `job_purge.py`, `JobStore.purge_job` và "Xóa video gốc" (`source_cleanup.py`). Xong: `b3be8ca`.
- **D2:** "Xóa video" và "Dọn video mất gốc", gồm route, `/api/status` và chặn điện thoại. Xong: `6c79aeb` (kèm
  các sửa của lần review D1 và `confirm_permanent`).
- **D3:** Dashboard V2. Xong, commit chung với D4. Đã thử bản demo trên trình duyệt (dữ liệu giả): hộp xác nhận,
  ô "Tôi hiểu", "Dọn video mất gốc". Máy này không có Playwright nên các browser-check để lần chạy trên cloud.
- **D4:** trang Control Center cũ. Xong, commit chung với D3.
- **D5:** tài liệu (README, CHANGELOG, PROJECT_STATUS, SESSION_HANDOFF), chạy cả bộ test, commit trên máy (chưa push).
  Xong: cả bộ test 1383 OK, 26 bỏ qua.
- **Còn thiếu (trang cũ):** video đã hủy đang ẩn (mục "Đã ẩn") chưa có nút "Xóa video" ở trang cũ; hiện lại video đó
  trước, hoặc dùng Dashboard V2.
- **Người dùng test:**
  1. chuyển thư mục chính sang nhánh này và khởi động lại Control Center (hỏi trước);
  2. "Dọn video mất gốc" 27 video;
  3. "Xóa video gốc" một video đã xuất mà người dùng chọn.

## 9. Số liệu đo trên máy thật (2026-10-05, chỉ đọc)

- **Dung lượng:** `input` 14 GB, `output` 15 GB, `reports` 5,0 GB. Phần của 38 video trong Control Center chỉ khoảng
  0,7 GB; phần còn lại là benchmark, POC và regression.
- **Mất gốc: 27 video.**
  - #1, #2, #5, #6: đã hủy.
  - #3, #4: đã xuất.
  - #40–#60: đã "Dọn video gốc" vào Thùng rác, nay không còn trong Thùng rác.
- **Đã hủy mà còn video gốc:** chỉ #39 (khoảng 7,1 GB), thuộc bộ nhãn vàng nên khóa.
- **Chỉ ở `reports\jobs`:** 23 thư mục không thuộc video nào, đều có dấu `.biliflow-benchmark`. Giữ nguyên.
- **Cache quét (`cache\stage-results`):** không bị mất. Các file sửa không nằm trong vân tay cache của bước quét nào;
  đã kiểm bằng `stage_source_paths`.

## 10. Xóa qua điện thoại (quyết định của người dùng, 2026-10-06)

- **Yêu cầu:** người dùng đã test xong trên PC. Họ muốn làm được trên điện thoại bốn thao tác:
  - "Dọn video mất gốc";
  - "Xóa video gốc";
  - "Hủy" cho video đang chờ thiết lập;
  - "Xóa video" cho video vừa hủy đó.
- **Server** (`phone_access.py`):
  - `/api/source-cleanup` và `/api/job-delete` chuyển từ `PC_ONLY_POSTS` sang `PHONE_ALLOWED_POSTS`. Nút "Hủy"
    (`/api/jobs/<id>/cancel`) vốn đã làm được trên điện thoại.
  - Điện thoại vẫn cần cookie mã, token phiên và Origin của listener. Mỗi lệnh xóa vẫn cần `preview_id` của hộp
    xem trước và `confirm_permanent: true`.
  - Lưu trữ, khôi phục bản xuất và kiểm tra lại Thùng rác vẫn chỉ làm trên PC. Lời nhắn chung `PC_ONLY_SOURCE`
    nay là "Chỉ làm trên PC: lưu trữ, khôi phục bản xuất và kiểm tra lại Thùng rác không làm qua điện thoại."
- **Dashboard V2:**
  - `pcOnlyOps` = archive, restore, recheck.
  - Trên điện thoại, tab Hoàn tất cho chọn video để "Xóa video gốc"; nút "Lưu trữ" bị khóa và nói lý do.
  - "Dọn video mất gốc" và "Xóa video" bật như trên PC.
  - Khung "Đang mở qua điện thoại" nói rõ ba thao tác xóa làm được ở đó và là xóa vĩnh viễn.
  - Trang Control Center cũ không mở trên điện thoại nên không đổi.
- **Rủi ro, đã báo người dùng:** kết nối điện thoại là HTTP không mã hóa trong Wi-Fi nhà. Ai bắt được gói tin trong
  cùng Wi-Fi có thể lấy cookie và token phiên rồi gửi lệnh xóa vĩnh viễn.
  - Vì "Hủy" cũng làm được trên điện thoại, người đó có thể hủy **bất kỳ** video nào chưa xong (kể cả video đang
    duyệt dở) rồi "Xóa video". Video gốc, quyết định duyệt và báo cáo của video đó sẽ mất.
  - Giảm thiểu:
    - chế độ điện thoại mặc định tắt và tự tắt sau 8 giờ;
    - bộ nhãn vàng luôn bị khóa;
    - bản xuất `.mp4` trong `output` không bao giờ bị xóa.
- **Review (2026-10-06):**
  - Review bảo mật: không có lỗi CRITICAL hay HIGH. Mọi lớp chặn của điện thoại (Host, cookie, token, Origin,
    allowlist khớp đúng, `preview_id`, `confirm_permanent`) chặt hơn trên PC.
  - Rủi ro MEDIUM ở trên được đưa cho người dùng chọn: (1) giữ như PC, hoặc (2) trên điện thoại chỉ xóa video đã hủy
    chưa có hàng duyệt.
  - **Người dùng chọn (1)**, chấp nhận rủi ro.
  - Các đề xuất chưa làm, để sau nếu người dùng muốn:
    - gắn cookie với IP và ghi sự kiện khi một IP mới dùng cookie;
    - mã PIN riêng cho lệnh xóa;
    - tường lửa chỉ mở cho IP của điện thoại.
  - Review code: APPROVE. Bốn ghi chú LOW đã sửa: câu cảnh báo trong khung bật chế độ điện thoại, chữ trên điện
    thoại, các dòng tài liệu cũ, và bước điện thoại của `browser-check.cjs`.
- **Test** (thư mục tạm trong `temp`, listener điện thoại thật trên 127.0.0.1):
  - `tests/test_job_delete_http.py` `PhoneListenerTests`: Hủy rồi Xóa video một video chờ thiết lập; Dọn video
    mất gốc (output giữ nguyên); thiếu cookie, token, Origin hay xác nhận thì không xóa gì.
  - `tests/test_source_cleanup_http.py` `PhoneListenerTests`:
    - Xóa video gốc từ điện thoại;
    - Host hoặc Origin sai bị từ chối;
    - route gần giống (thêm `/`, `/preview`, chữ hoa) bị 403 `pc_only`;
    - lưu trữ, khôi phục và kiểm tra lại Thùng rác vẫn bị 403 `pc_only`.
  - Cập nhật các test ghim danh sách route điện thoại và `verify*.cjs`.
