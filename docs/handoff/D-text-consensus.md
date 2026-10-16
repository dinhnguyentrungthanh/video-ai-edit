# Bàn giao D – bước quét chữ giữ hộp của logo cố định trong nhóm (job 145) và giữ dấu hiệu quảng cáo quá giới hạn 250 track (mục 6)
- Nhánh: fix/text-consensus-logo (từ main 419377f) · Worktree: `temp\wt-text-consensus` · Cập nhật: 2026-10-16
- Kế hoạch đã được điều phối duyệt: `docs/handoff/D-text-consensus-plan.md` (phương án A). Bằng chứng:
  `docs/evidence/text-consensus/README.md`, `gate-summary.json`. Số đo có chữ OCR thật chỉ nằm trong
  `temp\text-consensus\` (không vào Git).
- Trạng thái: **phương án A (ca17c07) và mục 6 (commit sau) xong, mọi cổng đạt; chờ điều phối gộp.** Việc 2 (thẻ
  chữ cố định trong dải phụ đề) chỉ đo và đề xuất (phần 3b), chờ người dùng xác nhận. Chưa push, chưa merge.
- Khóa cache bước `text` đổi một lần cho cả hai phần; 9 bước còn lại giữ nguyên.

## 1. Phương án A (ca17c07, chỉ `src/biliflow/textscan.py`)
- **Footprint của track:** mỗi track có `footprint_box`, là khung chung của chính các lần đọc của nó.
- **Neo:** trong một nhóm góc, thành viên được đọc ít nhất 10 lần và tự kéo dài ít nhất `minimum_span` của nhóm là
  "neo".
- **Khi nào footprint của neo được hợp vào khung chung:**
  - chưa nằm ≥ 95 % trong khung chung;
  - chạm khung chung;
  - cao không quá 2,5 lần khung chung.
- **Ghi lại:** `overlay_box_sources` giữ khung cũ và các neo.
- Hộp không bao giờ nhỏ hơn trước. Không đổi ngưỡng, cách gộp nhóm hay định tuyến. Giới hạn 250: xem mục 6,
  phần 3.
- **Khóa cache:** chỉ khóa bước `text` đổi; 9 bước còn lại giữ nguyên (đã so trên bản sạch của main và nhánh).

## 2. Cổng
- **Test:**
  - `test_textscan`: 10 test mới;
  - `test_fixed_logo_scan`: OCR giả lập theo dạng đọc thật của job 145; hỏng 2/3 trên main, đạt trên nhánh;
  - `test_fixed_logo_clip`: OCR thật trên clip tự làm; đạt trên cả main lẫn nhánh, vì OCR thật trên clip không gộp
    logo với dòng chữ chạy;
  - 97 test của các module dùng bước quét chữ: OK.
- **Quét lại trên GPU:**
  - người dùng đồng ý, hàng đợi thật trống, khoảng 19 phút;
  - job 145, 130, 134, 136, 143 và nguồn golden v2;
  - số track trùng báo cáo cũ (145: 856);
  - hàm gộp của main chạy trên cùng đầu vào cho đúng khung baseline.
- **Khung chung đổi:** trên cả 14 báo cáo quét lại, chỉ nhóm góc trên phải của job 145 đổi, từ [432,18,960,43]
  thành [432,18,960,77]. Các neo là track 2, 554, 606, 839 (logo, track bị đứt ba lần).
- **Danh sách của job 145:** dựng từ text-scan.json vừa quét lại, cùng báo cáo visual_logo của revision 2 (không có
  thẻ 175–180).
  - Một thẻ chữ đề xuất BLUR, 0–2561 s, vùng (0,21,1280,74), phủ logo. Vẫn 7 thẻ.
  - Với báo cáo visual_logo của revision 1: cùng thẻ, cùng vùng; thẻ visual 175–180 nhập vào thẻ đó.
  - `fixed_text_logo` (G12) không gộp gì, không nhân đôi vùng, không nới thêm.
- **Dải phụ đề yếu đi:** 0 ở 130/134/136/143/145.
- **Golden v2:** khung cũ so với khung mới mất 0, bẫy mới 0, bắt thêm 0 (19/19, dương tính giả 24/24). So với
  baseline v2 lưu ngày 10-13: +1, chính là phần G11 đã bắt thêm.
- **Xuất thử job 145 (x264):** OCR đọc được logo:

  | Cửa sổ | Gốc | Bản người dùng đã xuất | Sau một BLUR trên thẻ mới |
  |---|---|---|---|
  | 130–230 s | 199 | 198 | **0** |
  | 400–500 s | 200 | 200 | **0** |

  Dòng chữ chạy (400–500 s): sau khi sửa vẫn 0.

## 3. Mục 6 (giới hạn 250 track): đã làm theo TDD, người dùng chọn, cùng nhánh
**Vì sao:** giới hạn bỏ 580–694 track tham khảo mỗi phim của bộ này, bỏ từ cuối phim trước. Vì vậy thẻ watermark
dưới phụ đề dừng sớm 200–400 s, và bản xuất của job 145 (revision 2) còn lộ watermark sau 2313 s.

**Quy tắc ở bước quét chữ (`textscan.py`):**
- Quá giới hạn, báo cáo vẫn giữ mọi track tham khảo có dấu hiệu quảng cáo:
  - trúng chính sách, nhãn quảng cáo, hoặc điểm quảng cáo ≥ 0,40 (cùng mức của `subtitle_band`);
  - địa chỉ web, kể cả khi mất dấu chấm;
  - token chữ quảng cáo: địa chỉ, số điện thoại từ 8 chữ số, @tên.
- **Một dấu "@" đứng riêng không đủ** (một @tên thì đủ). Bản đầu có tính nó: ở job 143 nó giữ một câu thoại, câu này
  nối bảy câu khác thành một thẻ chữ cố định cả phim đề xuất BLUR dải phụ đề. Đã bỏ và quét lại GPU.
- Các track đã giữ trước vẫn giữ chỗ và thứ tự.
- Các track còn lại quá giới hạn nằm trong `omitted_tracks` (không preview), tối đa 4.000;
  `omitted_tracks_unrecorded` đếm phần dư. Báo cáo lớn nhất trên máy này có 3.344 track quá giới hạn.
- `web_address_tokens` và `fold_alnum` chuyển nguyên vẹn từ `scrolling_text.py` sang `web_address_patterns.py`.
  Khóa cache bước `text` thêm `ad_text_patterns.py` và `web_address_patterns.py` (18 → 20 tệp); 9 bước còn lại giữ
  nguyên.

**Bước dựng danh sách (không thuộc khóa cache nào):**
- **`readmit_remembered_tracks`:** khi bật bộ nhớ của người dùng, track bị bỏ mà chứa địa chỉ đã nhớ
  (`state/ad-text-memory.json`) được đưa lại vào `tracks`. Bước quét không thấy bộ nhớ. Nếu đưa bộ nhớ vào khóa, mỗi
  lần người dùng làm mờ một địa chỉ mới thì mọi phim phải quét lại. Benchmark dựng không có bộ nhớ, nên không lấy
  lại gì.
- **`FIXED_TEXT_PIECE_INSIDE = 0.60`:** track nằm ≥ 60 % trong khung của một chữ cố định cả phim thì không vào nhóm
  chữ lặp lại. Chuyện ở job 134:
  - logo của trang, đọc cả phim dưới dòng chữ chạy, bị đứt gần cuối;
  - mẩu cuối (giờ được giữ vì đọc ra địa chỉ) đọc giống dòng chữ, vào nhóm của dòng và kéo khung nhóm phủ lên logo;
  - thẻ cả phim của logo nuốt thẻ của dòng, và chỉ còn khung logo được làm mờ.
- Trên 139 báo cáo (125 đã lưu, 14 quét lại), quy tắc này đổi 4 nhóm:
  - 134: khung dòng về [0,16,960,43];
  - 143: nhóm dòng 25 → 24 thành viên, cùng khung.

**Cổng (`limit-gate-summary.json`):**
- **Test:**
  - 13 test mới: `test_textscan` 4, `test_ad_text_memory` 4, `test_text_track_limit_scan` 4,
    `test_fixed_text_overlays` 1.
  - Full suite: xem phần 3a.
- **Quét lại GPU, hai lần:**
  - người dùng đồng ý, hàng đợi thật trống, mỗi lần khoảng 16 phút;
  - 130, 134, 136, 143, 145 và golden v2, tham số production với giới hạn 250;
  - lần hai dùng quy tắc cuối;
  - mọi báo cáo trùng từng track với cùng quy tắc cắt offline từ bản quét không giới hạn buổi sáng.
- **Số track và kích thước báo cáo:**

  | Nguồn | Track | Giữ trước → sau | Ghi trong omitted | text-scan.json | Preview |
  |---|---|---|---|---|---|
  | 130 | 913 | 250 → 311 | 602 | 0,37 → 1,27 MB | 9,8 → 12,2 MB |
  | 134 | 944 | 250 → 299 | 645 | 0,37 → 1,31 MB | 9,8 → 11,8 MB |
  | 136 | 919 | 250 → 293 | 626 | 0,37 → 1,28 MB | 9,5 → 11,3 MB |
  | 143 | 830 | 250 → 292 | 538 | 0,37 → 1,16 MB | 9,6 → 11,3 MB |
  | 145 | 856 | 250 → 294 | 562 | 0,37 → 1,20 MB | 9,5 → 11,1 MB |
  | phim 37 | 392 | 250 | 142 | 0,36 → 0,55 MB | không đổi |
  | phim 38 | 468 | 250 | 218 | 0,36 → 0,65 MB | không đổi |
  | clip dày của 39 | 2.658 | 250 | 2.408 | 0,36 → 3,62 MB | không đổi |

  Sáu clip golden còn lại có 1–60 track, không đổi. Trang duyệt đọc danh sách, không đọc báo cáo: danh sách 5 job
  lớn thêm 4–6 KB (327–372 → 333–376 KB).
- **Thẻ watermark dưới phụ đề kéo tới cuối phim** (danh sách có bộ nhớ của người dùng; "trước" dựng bằng mã ca17c07):

  | Job | Hết trước | Hết sau | Độ dài phim | Số thẻ |
  |---|---|---|---|---|
  | 130 | 2247,5 | 2646,5 | 2655,2 | 8 → 8 |
  | 134 | 2427,5 | 2643,5 | 2657,2 | 6 → 7 |
  | 136 | 2436,5 | 2691,5 | 2702,2 | 9 → 9 |
  | 143 | 2361,5 | 2668,1 | 2668,1 | 22 → 22 |
  | 145 | 2313,5 | 2561,2 | 2561,2 | 7 → 7 |

  - Thẻ hết trước cuối phim thì hết ở lần thấy cuối cộng phần đuôi thường lệ (cách cuối 9–14 s).
  - Thẻ thứ 7 của job 134 có hai lần thấy địa chỉ của trang ở cùng một chỗ trong dải phụ đề:
    - lần 189–204 s trước đây bị gộp vào thẻ thoại của dải;
    - lần 2580–2589 s giờ mới được giữ.

    Thẻ đề xuất BLUR chỉ ở hai lúc đó.
- **Dải phụ đề yếu đi: 0** ở cả 5 job. Mọi thẻ BLUR của danh sách cũ vẫn được thẻ BLUR mới phủ ≥ 96,9 % khung, ở 50
  thời điểm mỗi thẻ. Khung watermark dịch 1 px; của 143 cao 31 px thay vì 32.
- Job 145 lấy lại 2 track bị bỏ có chứa địa chỉ đã nhớ; bốn job kia không có.
- **Golden v2** (không bộ nhớ):
  - trước = sau ở mọi detector: quảng cáo 19/19, dương tính giả 24/24, thẻ tư vấn 487/487, bẫy 0;
  - so với baseline v2: +1 (G11), mất 0, bẫy 0;
  - `compare` thoát 1 chỉ vì cổng thời gian.
- **Xuất thử đoạn cuối của 145** (x264, 2313–2561 s, OCR mỗi 0,5 s trên khung watermark, 496 mẫu): OCR đọc được
  watermark ở 188 mẫu của bản gốc và 188 mẫu của bản theo danh sách hiện tại của người dùng. Sau một BLUR trên thẻ
  watermark mới: **0**.
- **Khóa cache** (bản sạch của main, ca17c07 và nhánh): chỉ `text` đổi; 9 bước còn lại giữ nguyên.
- **Giới hạn:**
  - Báo cáo lớn hơn khoảng 3,4 lần (1,2–1,3 MB cho phim 45 phút); tối đa 4.000 bản ghi, khoảng 5–6 MB.
  - Track được lấy lại không có preview riêng.
  - Địa chỉ đã nhớ nằm ngoài 4.000 bản ghi thì không lấy lại được (máy này không có).
  - Một dòng quảng cáo lặp lại có số điện thoại hay @tên mà nằm ≥ 60 % trong khung một chữ cố định cả phim sẽ
    không có thẻ nhóm riêng. Các track của nó vẫn là mục riêng, và thẻ cả phim phủ ít nhất 60 % mỗi track. Trên máy này, 4/139
    nhóm đổi và nhóm nào cũng giữ đề xuất BLUR (review đọc mã: không lỗi CRITICAL/HIGH, 2 MEDIUM; MEDIUM thứ nhất
    đã thêm test).
  - Thẻ chuỗi thoại (việc 2) vẫn còn; thẻ của 134 rộng thêm 21 px vì giữ thêm mẩu thoại.

## 3a. Full suite
- `unittest discover` (BILIFLOW_TEST_INSTALL_ROOT là thư mục cài đặt): 3.485 test trong 2.571 s; 3.404 OK, 53 bỏ qua,
  28 lỗi.
- Cả 28 lỗi là `next((ROOT / "input").glob("*.mp4"))`, vì worktree không có clip trong `input\`
  (`test_job_pipeline` 17, `test_job_ocr_option` 11). Với một clip tổng hợp 1 s trong `input\` của worktree (đã xóa
  sau đó), hai module này đạt 35/35.
- Hai file test sửa sau lần chạy (tên ví dụ; giới hạn 4.000 bản ghi) đạt trên mã cuối (14 + 4), cùng 113 test của
  các module đổi.

## 3b. Việc 2: thẻ chữ cố định cả phim trong dải phụ đề (CHỈ ĐO VÀ ĐỀ XUẤT, chưa sửa)
**Đo trên mọi báo cáo:** có 16 thẻ trong dải phụ đề, đều là `recurring_fixed_text` và đều kéo cả phim.
- **10 thẻ thật** (dòng chữ hoặc watermark của trang; job 62, 63, 64, 65, 66, 70, 71, 72, 99, 100 rev 1):
  - độ đồng thuận của dòng 0,993–1,0;
  - tỉ lệ chữ khác nhau 0,41–0,55.
- **6 chuỗi thoại** (130 rev1; 134 rev1; 136 rev1 và rev2; 145 rev1 và rev2):
  - độ đồng thuận 0,25–0,375;
  - tỉ lệ chữ khác nhau 1,0;
  - không thành viên nào có địa chỉ web, số điện thoại hay @tên.
- **Đề xuất và quyết định:** 13 thẻ đề xuất BLUR, 3 không đề xuất (job 64 và cả hai của 145). Người dùng chọn
  12 BLUR và 4 KEEP (134, 136 rev1, cả hai của 145). Job 130 và 136 hiện xuất với cả dải thoại bị mờ suốt phim.

**Đề xuất quy tắc** (chỉ ở bước dựng, không đổi khóa cache):
- Trong `promote_fixed_text_overlays`, không nâng một nhóm `recurring_fixed_text` khi đủ cả ba:
  - tâm khung nằm trong dải phụ đề (tâm y ≥ 0,70 H, x trong 0,18–0,82 W);
  - không thành viên nào có địa chỉ web, số điện thoại hay @tên;
  - độ đồng thuận của dòng < 0,60.

  Các thành viên giữ track riêng và vẫn qua `subtitle_band` như mọi dòng phụ đề.
- **Ảnh hưởng đo được:**
  - bỏ 4 đề xuất BLUR trong danh sách (130, 134, 136 rev1 và rev2) và 7 trong các nhóm tính lại;
  - giữ nguyên 10 thẻ thật và 25 thẻ chữ cố định ngoài dải; dòng chạy phía trên có độ đồng thuận 0,44–0,59 nhưng có
    thành viên chứa địa chỉ web;
  - không đụng thẻ `repeated_watermark` nào.
- **Không làm yếu việc bắt quảng cáo trong dải phụ đề:**
  - quy tắc chỉ bỏ nhóm không có dấu hiệu nào;
  - watermark dưới phụ đề là thẻ `repeated_watermark`, không đi qua đường này;
  - để chắc hơn, có thể dùng luôn bộ dấu hiệu rộng hơn của mục 6 (điểm quảng cáo, chính sách, token), nhưng phải đo
    lại.
- **Không đề xuất** bắt buộc có web, số điện thoại hay @ mới được BLUR: cách đó mất 9 đề xuất thật.
- **Giới hạn của phép đo:**
  - chỉ 4 phim, một kiểu phụ đề;
  - bỏ qua bộ nhớ chữ quảng cáo;
  - các danh sách đã lưu được dựng bằng mã cũ.
- **Chờ người dùng xác nhận qua điều phối.**

## 4. Giới hạn (từ một lượt review đọc mã, không có lỗi nặng)
- **Footprint giữ cả lần đọc lớn tới 1,8 lần cỡ thường.** Vì vậy hộp của job 145 xuống tới y 77 trên khung phân
  tích, trong khi logo hết ở 68. Chỉ mờ thừa một chút bên dưới, không lọt.
- **Biên chiều cao mỏng:** job 145 là 2,12 so với giới hạn 2,5. Ở một phim có mẩu dòng chữ chạy mỏng hơn (khoảng
  20 px), cùng logo đó sẽ không được hợp. Khi đó nó chỉ được ghi `joined: false`, và bước dựng không hiện ra.
  Điều phối quyết có nới không.
- **Bề rộng chưa có giới hạn.** Trên 14 báo cáo quét lại chỉ job 145 đổi.

## 5. Người dùng áp dụng cho job 145
Sau khi nhánh vào main và Control Center khởi động lại:
- "Chạy lại" job 145 (bước chữ quét lại, khoảng 2 phút);
- BLUR thẻ chữ cố định (0,21,1280,74);
- BLUR thẻ watermark dưới phụ đề, giờ kéo tới cuối phim (0–2561 s);
- duyệt lại các thẻ khác;
- "Xuất video" lại.

Bản đã xuất hôm nay còn lộ logo, và còn lộ watermark dưới phụ đề ở khoảng 4 phút cuối. Các job 130, 134, 136 và 143
cũng có thẻ watermark dừng sớm 200–400 s: "Chạy lại" sẽ kéo thẻ tới cuối phim (với 134 có thêm một thẻ địa chỉ
trong dải phụ đề). Người dùng quyết có xuất lại không.
