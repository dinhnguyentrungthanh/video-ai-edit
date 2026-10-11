# Kế hoạch: định danh bản xuất, tái sử dụng an toàn, siết HTTP

Nhánh `fix/export-identity-http` (tách từ `main` 23aa1e4), làm trong worktree
`temp/wt-export-fix` để Control Center đang chạy từ `main` không bị ảnh hưởng.

## Hiện trạng (đã đọc code và kiểm chứng)

1. `review_workflow.review_export_paths` băm `id/decision/start/end/region` của mọi item.
   Nó bỏ sót những trường mà bản render thật sự dùng:
   - `decision_blur_edge_mode` (kiểu làm mờ mép);
   - `temporal_policy = discrete_detected_intervals` + `detected_intervals` (làm mờ từng đoạn phát hiện thay vì cả khoảng).

   Vì vậy đổi những trường này vẫn ra cùng tên file `output/...-reviewed.mp4`.
2. `ControlCenter.finalize` (control_center.py:1114) và review-ui độc lập (`export_status`, `start_export`)
   chỉ kiểm tra `output_path.exists()`. Một file bất kỳ trùng tên, kể cả file giả 11 byte không có manifest,
   cũng thành "Hoàn tất" mà không render.
3. "Dọn video gốc" và "Lưu trữ" đã có lớp chặn riêng: so operations trong manifest với quyết định hiện tại
   (`REASON_OUTPUT_OLDER`). Vì vậy chưa từng có nguy cơ mất video gốc. Lỗi chỉ ở chỗ báo "Hoàn tất" sai.
4. `job_import` (chỉ chạy khi khởi động lần đầu hoặc `-RefreshExisting`) chấp nhận manifest COMPLETED mà
   không kiểm tra operations, số byte hay kết quả giải mã.
5. HTTP:
   - `Content-Length` âm làm `rfile.read(-1)` chờ tới khi client đóng kết nối, có thể treo luồng mãi.
   - Không có timeout đọc request.
   - `control_entry --host 0.0.0.0` mở Control Center ra mạng LAN, và `/api/session` trả token cho bất kỳ ai
     giả header `Host: 127.0.0.1`.
6. 25 bản xuất thật trong `output/` đều mang tên theo công thức cũ. Manifest của chúng có đủ `operations`,
   đã qua giải mã toàn bộ (`full_decode_validation_passed`) và số byte khớp, nên phải tiếp tục được nhận ra.

## Sửa 1: định danh bản xuất theo thao tác render thật

- Thêm module trung lập mới `src/biliflow/export_identity.py`, chỉ dùng thư viện chuẩn để không tạo vòng
  import. Module chứa:
  - `RENDER_FIELDS` và `render_identity()`, chuyển nguyên văn từ `source_cleanup`.
  - Hàm dựng 3 đường dẫn plan/output/job:
    - Có render identity: khóa mới = sha256 của `{"export_identity": 2, "operations": render_identity(approved_operations(queue)), "export_size_policy": <chỉ khi khác mặc định>}`.
    - Hàng đợi chưa duyệt xong, chưa dựng được operations: giữ công thức cũ. Lúc đó cũng chưa thể có
      bản xuất hợp lệ.
  - `legacy_*`: công thức cũ, chỉ dùng để nhận ra bản xuất làm trước bản sửa này.
  - `output_candidates()`: trả về [đường dẫn mới, đường dẫn cũ] (bỏ trùng).
- `review_workflow.review_export_paths` giữ nguyên tên và chữ ký (các nơi gọi và test không phải đổi), bên
  trong gọi module mới. Thêm `queue_render_identity(queue)` và `existing_review_export(root, queue)`.
- Kết quả:
  - Đổi kiểu làm mờ mép, hoặc đổi giữa làm mờ cả khoảng và từng đoạn, sẽ ra tên file mới và render lại.
  - Đổi ghi chú, hoặc đổi vùng của item KEEP, không đổi bản render nên vẫn giữ tên cũ.

## Sửa 2: chỉ dùng lại file đã xuất khi manifest chứng minh được

- Thêm `export_identity.manifest_problem(root, output, manifest, source_sha256, render)`, trích từ các điều kiện
  đã review kỹ của `source_cleanup._exported_facts`. Hàm trả `None` khi file hợp lệ, ngược lại trả mã lý do:
  `manifest` / `decode` / `changed` / `older` / `no_operations`. Điều kiện để hợp lệ:
  - manifest `COMPLETED`, cùng SHA-256 video gốc, `modified=false`;
  - mô tả đúng file này;
  - đã qua giải mã toàn bộ;
  - số byte khớp;
  - operations khớp quyết định hiện tại.
- `source_cleanup._exported_facts` gọi chung hàm này, giữ nguyên thứ tự lý do và thông điệp. Nhánh manifest
  cũ không có operations vẫn kiểm tra bằng thời điểm và edit plan như trước. Nó thử ứng viên mới rồi đến ứng
  viên cũ, nên bản xuất cũ vẫn dọn hoặc lưu trữ được.
- `ControlCenter.finalize`:
  - Có bản xuất đã xác minh (mới hoặc cũ): "Hoàn tất", không render. Hành vi cũ, nhưng nay có kiểm chứng.
  - Đường dẫn sắp ghi đã có file chưa xác minh: từ chối, kèm thông điệp tiếng Việt rõ ràng
    "dời file đó ra khỏi output rồi xuất lại". Không ghi đè, không xóa. Kiểm tra này chạy trước mọi thao
    tác ghi, nên queue giữ nguyên từng byte.
  - Còn lại: render vào đường dẫn mới. File cũ không bị đụng tới vì khác tên.
- Review-ui độc lập (`export_status`, `start_export`) dùng cùng logic.
- `job_import`:
  - Job đã duyệt: chỉ nhận manifest nằm ở một trong hai ứng viên và qua `manifest_problem`.
  - Job mới hoặc chưa duyệt: vẫn nhận bản xuất của cùng video gốc như trước, nhưng manifest phải chứng minh
    file (byte, giải mã). Không so operations, giữ ngữ nghĩa cũ của lần import đầu.
- Cập nhật lời nhắc `REASON_OUTPUT_OLDER`: giờ chỉ cần xuất lại, không phải tự dời file cũ.

## Sửa 3: siết HTTP

- Module mới `src/biliflow/http_guards.py` (thư viện chuẩn):
  - `body_length(headers)`: không có header thì là 0; chữ, số âm hay `+5` đều báo `ValueError` và trả HTTP 400.
  - `REQUEST_TIMEOUT_SECONDS = 20`.
  - `is_loopback_host()` / `require_loopback_host()`.
- Control Center và review-ui độc lập:
  - Handler đặt `timeout` để socket tự đóng khi client gửi header/body quá chậm (hết giờ trả 408 hoặc đóng
    kết nối).
  - Riêng khi stream video, gỡ timeout trong lúc gửi rồi đặt lại. Trình phát đang tạm dừng không đọc tiếp
    vẫn không bị cắt, đúng như hiện nay.
- `control_entry`, `serve_control_center`, `ControlCenter.serve` và `serve_review_ui` từ chối `--host` không
  phải loopback (`127.x`, `localhost`, `::1`). Lệnh dừng ngay, trước khi mở database hay import.
  `Start-BiliFlow.ps1` vẫn dùng `127.0.0.1` nên không bị ảnh hưởng.
- Không đổi: `golden_label_app` (cố ý mở LAN cho điện thoại). Sẽ ghi lại thành việc để làm sau.

## Test (viết trước, phải thấy FAIL rồi mới sửa)

1. Đổi `decision_blur_edge_mode` thì đổi đường dẫn output. Đổi cả khoảng ↔ từng đoạn phát hiện (cùng
   start/end) cũng đổi. Đổi ghi chú hay vùng của item KEEP thì giữ nguyên.
2. Hàng đợi chưa duyệt xong vẫn cho đường dẫn (công thức cũ), không ném lỗi.
3. `manifest_problem`: từng mã lý do, và trường hợp hợp lệ.
4. `finalize` với file giả 11 byte, không manifest: không "Hoàn tất", không xếp render, queue giữ nguyên
   từng byte.
5. `finalize`:
   - bản xuất cũ (tên công thức cũ) có operations khớp: "Hoàn tất" với đúng file đó;
   - operations khác (đổi kiểu mép): render vào đường dẫn mới, file cũ còn nguyên.
6. Dọn video gốc / Lưu trữ:
   - bản xuất tên cũ, operations khớp: vẫn đủ điều kiện;
   - đổi kiểu mép: bị từ chối (cũ: `OUTPUT_OLDER`; bản xuất ở tên mới: `OUTPUT_STALE`).
7. `job_import`: file giả hoặc manifest thiếu thông tin thì không COMPLETED; bản xuất tên cũ hợp lệ vẫn
   COMPLETED.
8. Review-ui: file giả thì `export_status` là IDLE, còn `start_export` từ chối.
9. HTTP:
   - `Content-Length: -1` và `abc`: 400 ngay, không treo;
   - body gửi dở: kết nối bị đóng sau timeout;
   - stream video 32 MB với client nghỉ lâu hơn timeout: vẫn nhận đủ byte.
10. `control_entry --host 0.0.0.0` và `192.168.x.x`: thoát mã 2, không gọi server. `127.0.0.1`, `localhost`,
    `::1`: chạy bình thường.

Mọi test dùng thư mục tạm, không đụng `input`/`output`/`archive`/`state` thật. Chạy test liên quan trước,
rồi chạy cả bộ `unittest`.

## Kiểm chứng trên dữ liệu thật (chỉ đọc)

Script trong scratchpad đọc database ở chế độ `mode=ro`, đọc queue và manifest. Với mỗi job COMPLETED, so
kết quả tìm bản xuất theo logic mới với file đang có. Kỳ vọng: cả 25 bản xuất hiện có vẫn được nhận. Script
không gọi hàm ghi nào.

## Sau khi xong

- Agent review code và review bảo mật (tối đa 2 agent song song); sửa các phát hiện mức CRITICAL/HIGH.
- Cập nhật CHANGELOG, PROJECT_STATUS, SESSION_HANDOFF.
- Muốn chạy chính thức thì phải khởi động lại Control Center trên nhánh này. Mình sẽ hỏi bạn trước.
  Commit hay merge chỉ khi bạn yêu cầu.

## Đợt sửa sau review (2026-10-03, tối)

Review code: APPROVE, 4 LOW. Review bảo mật: 1 MEDIUM, 5 LOW, không có CRITICAL/HIGH. Đã sửa (viết test trước,
thấy test đỏ rồi mới sửa):

1. MEDIUM: symlink hoặc reparse point ở đường dẫn bản xuất không bao giờ được coi là bản xuất
   (`manifest_problem` dùng `lstat`). Hard link vẫn được nhận vì dọn video gốc không làm mất dữ liệu.
2. Manifest hoặc queue "độc" (NUL trong đường dẫn, JSON lồng quá sâu, `"source": "x"`) không bao giờ ném lỗi:
   finalize, review-ui và dọn video gốc coi là "chưa chứng minh"; import lúc khởi động bỏ qua.
3. Body JSON lồng quá sâu: trả 400.
4. Hash nguồn trong tên file chỉ nhận chữ số hex, nên queue bị sửa tay không đẩy được đường dẫn ra ngoài `output/`.
5. Renderer:
   - băm file và video nguồn trước khi đổi tên;
   - `os.rename` không ghi đè file xuất hiện trong lúc render;
   - nguồn bị đổi trong lúc render thì không để lại bản xuất thiếu manifest.
6. `--host` chặt hơn: chỉ nhận IPv4 loopback hoặc `localhost` viết đúng. `localhost` bind thẳng 127.0.0.1;
   `::1` bị từ chối (server chỉ chạy IPv4).
7. Review-ui:
   - job file COMPLETED mà bản xuất đã rời `output/` thì trạng thái là IDLE;
   - thêm test cho 408;
   - test finalize đổi từng trường một.

Chấp nhận, không đổi:
- timeout tính theo mỗi lần đọc;
- stream video đang tạm dừng vẫn giữ luồng xử lý;
- `golden_label_app` và `allow_reuse_address` giữ nguyên.

Kết quả kiểm chứng:
- full suite 1180 OK (skipped=25) sau đợt 1;
- e2e ffmpeg 11/11;
- thử tắt từng kiểm tra manifest: đều có test đỏ;
- dữ liệu thật chỉ đọc (`realdata_check.py`): giống hệt main.

## Đợt sửa thứ hai (sau khi hai agent review lại)

Review code: APPROVE. Review bảo mật: các bản sửa đều giữ được; còn 3 LOW và 2 INFO, đã sửa hết:

1. Đường dẫn trong manifest được so theo đúng chữ viết (`abspath` + `normcase`), không gọi `resolve()`:
   - không bao giờ tra cứu đường dẫn UNC qua mạng;
   - symlink lặp không còn làm ném RuntimeError.
2. Bản xuất không được là chính video nguồn (hardlink của nguồn, hoặc qua junction).
3. Import khi khởi động bỏ qua queue hỏng:
   - không có `path` hoặc `path` chứa NUL;
   - `duration` là chữ;
   - `status` không phải chữ;
   - `reports` không phải list.

   Ngoài ra không `resolve()` đường dẫn bản xuất nữa, nên `output/` là junction vẫn import được.
4. Renderer kiểm tra đường dẫn xuất đúng như được truyền vào, trước khi resolve. Link treo bị từ chối trước khi
   chạy FFmpeg; trước đây render sẽ đi theo link đó. Finalize và review-ui cũng từ chối link treo.
5. Test link dùng kích thước của chính link, nên chỉ phần kiểm tra link mới có thể làm test đỏ.

Kết quả:
- full suite 1192 OK (skipped=25);
- e2e ffmpeg 11/11;
- thử tắt từng kiểm tra mới (link, "chính video nguồn"): đều có test đỏ;
- dữ liệu thật chỉ đọc: vẫn giống hệt main.

## Sửa lỗi xuất video ngắn (sau commit 2a37496, theo yêu cầu "commit xong sửa luon chỗ lỗi video ngắn")

Nguyên nhân: khi có giới hạn dung lượng, renderer truyền cho FFmpeg
- `-maxrate` = ngân sách dung lượng chia cho thời lượng;
- `-bufsize` = 2 × maxrate.

FFmpeg chỉ nhận mỗi giá trị tối đa 2.147.483.647, nên các trường hợp sau lỗi:
- giới hạn mặc định 3,5 GB: video ngắn hơn khoảng 24 s;
- giới hạn tùy chỉnh 100 GB: video ngắn hơn khoảng 11 phút;
- giới hạn tùy chỉnh 400 GB: video ngắn hơn khoảng 45 phút.

Cách sửa: `MAX_VIDEO_MAXRATE = (2**31 - 1) // 2 = 1.073.741.823` bit/s.
- `video_maxrate = min(ngân sách - audio, MAX_VIDEO_MAXRATE)`.
- Đây là mức cao nhất mà bufsize (gấp đôi) vẫn hợp lệ. Mọi lệnh FFmpeg từng chạy được giữ nguyên 100%; chỉ những lệnh FFmpeg từng từ chối mới đổi.
- Trần thấp hơn chỉ làm file nhỏ hơn, nên giới hạn dung lượng vẫn đúng.
- Không chọn 1 Gbit/s: mức đó sẽ đổi tham số của các clip dài 23,9–25,6 s vốn đang xuất được.

Kiểm thử:
- TDD trong `RenderCompletionTests`:
  - đọc `-maxrate`, `-bufsize` và `-b:v` từ lệnh FFmpeg;
  - clip 6 s và 20 s (mặc định; clip 20 s chạy cả bản có âm thanh) và 1 giờ ở 1000 GB: nằm trong giới hạn của FFmpeg. Các test này ĐỎ trước khi sửa, XANH sau khi sửa;
  - clip 24,5 s và 1 giờ (mặc định, có và không có âm thanh): giữ đúng tham số cũ. Test này xanh cả trước lẫn sau khi sửa, vì nó khóa lệnh cũ;
  - ranh giới: trần là mức cao nhất mà bufsize vẫn hợp lệ. Ngân sách thấp hơn trần 1 / bằng trần / cao hơn trần 1 cho ra trần−1 / trần / trần.
  - Đột biến (`mutation-shortclip.txt`): trần ±1, 1,05e9, 1,073e9, 1e9, 2**31−1, bỏ trần, trừ âm thanh sau khi chặn trần. Trường hợp nào cũng làm ít nhất 1 test đỏ.
- Review code (agent chỉ đọc): APPROVE.
  - So lệnh cũ và mới trên 4.524 trường hợp: mọi lệnh FFmpeg từng nhận đều giống hệt; các lệnh bị từ chối nay thành (trần, 2×trần).
  - 1 LOW (khóa đúng ranh giới) và 2 INFO (thêm trường hợp có âm thanh; ghi rõ test giữ tham số cũ là test khóa hành vi cũ): đã xử lý.
- FFmpeg thật (`shortclip_check.py`, `shortclip-results.txt`): 20/20.
  - Không có trần: FFmpeg từ chối clip 6 s (maxrate), clip 20 s (bufsize) và clip 10 phút ở 100 GB (bufsize), không để lại gì trong output/.
  - Có trần: cả ba xuất được, manifest chứng minh được, nằm trong giới hạn, video nguồn không đổi.
  - Clip 30 s: bản xuất giống hệt từng byte dù có hay không có trần.
- Full suite: 1197 OK (skipped=25), `full-suite-7.log`.

Ghi nhận, không sửa:
- Giới hạn tùy chỉnh lớn hơn dung lượng trống bị chặn trước khi render, vì renderer giữ chỗ bằng dung lượng mục tiêu.
- Clip ngắn được x264 gắn H.264 level 6.2, giống hệt trước khi sửa.
