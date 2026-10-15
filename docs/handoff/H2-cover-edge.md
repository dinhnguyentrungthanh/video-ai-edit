# Bàn giao H2 – vùng phủ (delogo) chạm mép phải/dưới khung không còn làm hỏng bản xuất (job 141)
- Nhánh: fix/cover-edge (từ fix/export-many-regions 11a059c) · Commit: 5cb52c4 (sửa), 20584f3 (sửa theo review:
  bước chroma đo được của từng định dạng điểm ảnh), e8b52d9 (sửa theo review của 20584f3: nguồn FFmpeg xoay/lật,
  bản xem trước, uyyvyy411), commit tài liệu kèm bàn giao này (xem `git log -1`) · Worktree: `temp\wt-cover-edge` ·
  Cập nhật: 2026-10-15
- Chạy ở: PC · Trạng thái: **xong; mọi cổng đều đạt trên e8b52d9. Chờ điều phối gộp (G9, cùng R) và chạy full
  suite.** Chưa push, chưa merge.
- Bằng chứng: `docs/evidence/cover-edge/README.md` (số đo, bảng định dạng, cổng, job 141, giới hạn).

## Đã xong

### 1. Nguyên nhân (job 141)
- Cách phủ (`delogo_blur_v1`) cắt vùng kèm tối đa 8 px hình xung quanh, lấp vùng bằng delogo, cắt lại đúng vùng, làm
  mờ rồi đặt lại. delogo từ chối hình chữ nhật chạm mép hình nó nhận, nên chuỗi giữ nó cách mép 1 px.
- crop của FFmpeg làm tròn kích thước (và góc) xuống theo mẫu chroma của hình nó cắt. Job 141: vùng x=583 y=35
  w=697 h=49 chạm mép phải khung 1280 × 534 yuv420p; chuỗi xin `crop=705:65:575:27`, FFmpeg cắt 704 × 64, nên
  `delogo=x=8 w=696` chạm mép và cả đồ thị bị từ chối lúc dựng ("Logo area is outside of the frame", mã −22),
  trước khung đầu tiên. Vì vậy "Thử lại" không bao giờ qua.

### 2. Cách sửa (5cb52c4, `src/biliflow/blur_filter.py`)
- Chỗ nào hình chữ nhật delogo vừa với ảnh FFmpeg sẽ cắt ra thì **giữ nguyên chuỗi cũ từng ký tự** (mọi kế hoạch
  đang xuất được giữ nguyên byte).
- Chỗ FFmpeg từng từ chối: cắt và đặt vùng từ điểm ảnh chẵn bên trái và bên trên nó (thêm tối đa 1 px ở hai phía đó,
  nằm trong `blur_layout.EDGE_PIXELS`), cả hai crop dùng `exact=1` (không làm tròn; góc chẵn để chroma 10-bit
  đúng), overlay đặt đúng chỗ cắt. Miếng vá tới hết mép phải/dưới của vùng, nên vùng chạm mép khung được làm mờ tới
  cột/hàng cuối. Phía nào của miếng vá nằm trên mép khung thì feather không làm mờ dần ở phía đó.

### 3. Review và sửa theo review (20584f3)
- Review của 5cb52c4 tìm ra: `chroma_step_of` coi mọi định dạng trừ YUV phẳng/gray/RGB phẳng là 4:2:0. Nguồn rgb24
  thật ra cắt theo định dạng của chính nó, nên một vùng đang xuất được (crop lẻ cách mép 1 px trên nguồn rgb24) sẽ
  bị đổi sang crop chính xác: khác byte. Đúng là lỗi; 5cb52c4 làm khác đồ thị của kế hoạch cổng
  `edge-rgb24-right-1px-odd`, 20584f3 thì không.
- Đã đo trên FFmpeg 9.0.1 **mọi định dạng điểm ảnh FFmpeg tạo được** (nay 220 định dạng: pal8 tạo bằng paletteuse,
  17 định dạng swscale không tạo được thì dựng từ byte thô), qua chính đồ thị xuất với `-pix_fmt yuv420p` như bản
  xuất, đứng một mình và sau một lớp làm mờ toàn khung (`docs/evidence/cover-edge/crop-steps.jsonl`,
  `measure_crop_steps.py`):
  - nhánh vùng đầu tiên của mỗi đoạn giữ lại cắt theo định dạng của nguồn (trừ 4:2:2 đóng gói, uyyvyy411 và
    monow/monob: đổi sang 4:2:0 trước crop);
  - có lớp làm mờ toàn khung đứng trước nó trong đồ thị thì chỉ 49 định dạng gblur nhận giữ nguyên; mọi định dạng
    khác (RGB, big-endian, bán phẳng, 4:4:4 đóng gói, pal8, yuv440p10le …) tới nhánh đó là 4:2:0;
  - mọi nhánh sau đó cắt 4:2:0; renderer xếp các lớp mờ theo thời điểm bắt đầu (dài hơn đứng trước khi bằng nhau).
- Code: `blur_filter.SourceChroma.of(pix_fmt)` (bước khi đứng một mình và sau làm mờ toàn khung, theo đúng bảng đo;
  định dạng không rõ → không làm tròn ở nhánh đầu, tức giữ chuỗi cũ); `blur_layout.branch_chroma_steps` dùng bước
  sau làm mờ toàn khung khi lớp đó đứng trước nhánh đầu tiên.

### 4. Review của 20584f3 và sửa theo review (e8b52d9)
- Review độc lập (3 hướng: mô hình, giữ byte, test/script; mỗi phát hiện được một người khác thử bác bỏ trên FFmpeg
  thật) xác nhận:
  - **Nguồn có ma trận hiển thị** (xoay/lật): bản xuất để autorotate của FFmpeg bật, FFmpeg chèn transpose, hflip,
    vflip hoặc rotate trước đồ thị, và nhánh đầu cắt **4:2:0** ở mọi định dạng. 20584f3 lại làm tròn theo định dạng
    nguồn: vùng trên nguồn yuv411p xoay 180° vốn xuất được sẽ bị đổi chuỗi (khác byte), vùng trên nguồn yuv422p xoay
    bị FFmpeg từ chối thì vẫn bị từ chối.
  - **Bản xem trước của review** (`-ss`/`-t` trước `-i` chèn trim trước đồ thị) cắt 4:2:0 ở mọi định dạng; 20584f3
    làm tròn theo định dạng nguồn nên bản xem trước của nguồn 4:4:4 có thể bị từ chối.
  - uyyvyy411 bị đổi sang 4:2:0 trước crop (đo từ byte thô).
- Bản nháp đầu của sửa này coi mọi ma trận khác ma trận đơn vị (và thẻ `rotate`) là xoay. Review thứ hai tìm ra lỗi
  giữ byte: FFmpeg không xoay ma trận chỉ dời hoặc chỉ phóng hình, góc làm tròn ra 0° hay +1°, và FFmpeg 9 không đọc
  thẻ `rotate`; nguồn 4:4:4 như vậy sẽ bị đổi chuỗi dù 11a059c xuất được. Đã sửa trước khi commit.
- Code (e8b52d9):
  - `blur_filter.display_transform` quyết định như fftools 9.0.1 (`get_rotation`: góc làm tròn tới độ, trong
    [−0,9; 359,1); transpose ở 90/270, hflip/vflip ở 180, rotate khi > 1°, vflip cho ma trận lật ở 0°). Đo trên
    FFmpeg với mọi định dạng và 11 ma trận (90°, 180°, 270°, 30°, 0,4°, 1°, −1°, 2°, 89,6°, hflip, vflip): 2.526/2.526
    lượt khớp. Xoay 1° đọc thành 359° nên bị xoay, −1° thì không. Trường `rotation` của ffprobe không dùng được (ghi 0
    cho vflip và cho ±1°).
  - `SourceChroma.of_stream(video_stream)`: ma trận FFmpeg áp dụng → 4:2:0; còn lại `of(pix_fmt)`; ma trận không
    đọc được 9 số, hoặc nguồn bị xoay mà không rõ định dạng → không làm tròn (chuỗi của 11a059c).
  - `final_renderer` truyền `SourceChroma.of_stream` của luồng video đã probe; bản xem trước dùng `CHROMA_420`.
  - `scripts/export_regions_gate.py` dựng đồ thị từ probe của nguồn đã chép như `render_final_output`, làm được clip
    `<định dạng>@rotate-180` và thêm 2 kế hoạch xoay; `scripts/benchmark_export_steps.py` ghi ma trận hiển thị của
    bản chép (không ghi thẻ nào khác). Cả hai lùi về `SourceChroma.of` cho cây không có `of_stream` (20584f3).
  - `measure_crop_steps.py` có 3 chế độ (export, transformed, preview); 3 bảng đo nằm trong
    `docs/evidence/cover-edge/`.

### 5. Cổng (trên e8b52d9, `src` sạch)

| Cổng | Kết quả |
|---|---|
| x264 byte + framemd5 so với 11a059c (`scripts/export_regions_gate.py` → `gate-cover-edge.json`): 12 kế hoạch của H + 12 kế hoạch gần mép vốn chạy được (gồm rgb24 crop lẻ cách mép 1 px, 10-bit 4:2:2 sau làm mờ toàn khung, yuv410p, 10-bit, shared, yuv411p xoay 180°) | **24/24 giống từng byte**, cùng framemd5, cùng chữ đồ thị (8 kế hoạch dùng cách nối shared) |
| Kế hoạch 11a059c từ chối (cùng cổng, 10 ca: dạng job 141, cách mép 1 px, cao lẻ ở mép dưới, góc không feather, nhánh sau trên yuv444p, yuv410p cách đáy 2 px, 10-bit, shared, rgb24 sau làm mờ toàn khung, yuv422p xoay 180°) | **10/10**: 11a059c bị delogo từ chối; nhánh xuất COMPLETED, crop chính xác; trong vùng giữ ≤ 0,0013 chi tiết nguồn, cột/hàng cuối khung ≤ 0,0094; ô đối chứng giữ 0,89–0,94. Hai ca xoay phân biệt được 20584f3 với e8b52d9 |
| `tests/test_cover_edge.py` (31 test): mô hình so với cả 3 bảng đo (220 định dạng; 2.526 lượt có ma trận hiển thị; 232 bản xem trước); `of_stream`; FFmpeg thật trên yuv420p/422p/444p/410p × mép phải/dưới × rộng/cao lẻ/chẵn × lề 0/1/2 px × có/không feather × nhánh đầu/nhánh sau × nested/shared, kèm chạy đồ thị của 11a059c ở từng ca; rgb24, yuv440p10le, yuv422p10le, nv16 một mình và sau làm mờ toàn khung; clip xoay 180° và lật; MP4 có ma trận chỉ dời/phóng và NUT có thẻ rotate; bản xem trước thật của nguồn 4:4:4; vùng job 141 trên khung 1280 × 534; chroma 10-bit; định dạng không rõ | OK. Chạy trên mã của 20584f3 thì các test mới hỏng đúng ở từng phát hiện của review |
| Test tập trung: cover_edge, blur_filter, blur_layout, final_renderer, export_regions, cache_dependencies, render_graph_file, export_guards, export_identity, render_watchdog, export_encoder, export_lanes, review_workflow, skip_export | 382 test OK |
| Khóa cache quét (`cache_keys.py`, 10 bước, e8b52d9 so với 11a059c) | không đổi |

### 6. Job 141 thật, đoạn 2140–2170 s (root tạm `temp\ce-job141`)
- Kế hoạch dựng trong root tạm từ `review-queue.json` của job 141; phim nguồn chỉ được đọc; không ghi gì vào
  `output\`, `reports\`, `state\` thật; root tạm đã xóa sau khi xong.
- Đồ thị cả phim qua FFmpeg 1 s: 11a059c bị delogo từ chối; nhánh chạy.
- `render_final_output` thật trên bản sao không mất dữ liệu của 2140–2170 s: 11a059c lỗi; nhánh COMPLETED (5,4 s
  trên 20584f3), qua kiểm giải mã toàn bộ; bản xuất của 20584f3 (chạy 2 lần) giống từng byte bản của 5cb52c4.
- Nguồn yuv420p, không có ma trận hiển thị: e8b52d9 dựng đúng đồ thị cả phim như 20584f3 (so chữ đồ thị trên probe
  của phim), nên kết quả trên vẫn đúng cho e8b52d9.
- Lúc lớp mờ bật: trong vùng còn 0,0 chi tiết nguồn, các cột 1272–1279 của khung cũng 0,0; 3 s sau khi tắt vùng giữ
  0,98. Đầu ra của đồ thị (không encoder): điểm ảnh đổi đúng x 582–1279, y 34–83, 0 điểm đổi ở chỗ khác.

### 7. Giới hạn đã biết
- Kế hoạch đang chạy được giữ nguyên byte, nên giữ cả hai hành vi cũ: vùng có feather chạm mép khung để lại cột/hàng
  ngoài cùng là điểm ảnh nguồn; vùng có x/y/kích thước lẻ (phủ và gblur thường) bị cắt từ góc làm tròn xuống và đặt
  ở x/y chẵn, nên 1–2 cột/hàng cuối không mờ. Sửa hai điều này cho mọi vùng sẽ đổi byte kế hoạch đang chạy: để người
  dùng quyết định.
- Vùng được sửa: phía không nằm trên mép khung vẫn làm mờ dần theo feather; chỗ dải đó gặp mép khung (1–2 hàng đầu và
  cuối của cột cuối) điểm ảnh ngoài cùng pha với nguồn.
- Bước sau làm mờ toàn khung đo với định dạng đầu ra của bản xuất (yuv420p). 47 định dạng không đo được: 16 định dạng
  phần cứng, 12 bayer (FFmpeg tự lỗi ở crop đầu khi đứng một mình) và 19 định dạng FFmpeg không đổi được sang yuv420p
  của bản xuất (mã −40), nên không bản xuất nào của chúng chạy được.
- Hướng hiển thị nằm trong luồng H.264/HEVC (SEI) được FFmpeg áp dụng nhưng `ffprobe -show_streams` không thấy:
  nguồn như vậy (không phải 4:2:0) giữ bước của chính nó, nên vùng FFmpeg từ chối vẫn bị từ chối như trên 11a059c
  (không đổi byte).
- Nguồn xoay 90°/270° tới đồ thị với rộng và cao đổi chỗ, nhưng renderer, bản xem trước và các bộ quét lấy kích thước
  mã hóa từ probe (`final_renderer._frame_size`), như trước nhánh này: ngoài phạm vi, đã ghi thành việc riêng.
- Chưa đo: NVENC (cùng đồ thị), nguồn kích thước lẻ trong bản xuất thật, nguồn giải mã bằng phần cứng (bản xuất không
  dùng).

### 8. Kiểm tra trên máy đang chạy job thật
- Mọi lượt FFmpeg ở mức ưu tiên dưới bình thường, mỗi lần một bản render; không chạy bài thử tải CPU nào. Không gọi
  route nào của Control Center thật, không đụng thư mục chính.
- Người dùng tạm dừng ngày 2026-10-15 lúc cổng đang chạy (11/32 kế hoạch đã qua); khi tiếp tục, cổng chạy lại từ đầu
  và qua toàn bộ, rồi chạy lại lần nữa trên e8b52d9.

## File đã đổi
- Mới: `tests/test_cover_edge.py`, `docs/evidence/cover-edge/` (README, `gate-cover-edge.json`,
  `job-141-2140-2170.json`, `crop-steps.jsonl`, `crop-steps-transformed.jsonl`, `crop-steps-preview.jsonl`,
  `measure_crop_steps.py`, `cache_keys.py`, 3 script job 141).
- Sửa: `src/biliflow/blur_filter.py`, `src/biliflow/blur_layout.py`, `src/biliflow/final_renderer.py`,
  `src/biliflow/review_workflow.py`, `scripts/export_regions_gate.py`, `scripts/benchmark_export_steps.py`,
  `tests/test_final_renderer.py`.

## Việc tiếp theo
- Điều phối: gộp cùng R thành G9, chạy full suite trên bản gộp.
- Sau khi gộp và khởi động lại Control Center (Stop rồi Start), người dùng bấm "Thử lại" ở job 141. Agent không bấm.
- Việc riêng (không thuộc nhánh này): kích thước khung của nguồn xoay 90°/270°.
