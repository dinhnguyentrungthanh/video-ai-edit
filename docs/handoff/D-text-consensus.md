# Bàn giao D – bước quét chữ giữ hộp của logo cố định trong nhóm (job 145) và giữ dấu hiệu quảng cáo quá giới hạn 250 track (mục 6); thẻ chuỗi thoại không đề xuất BLUR (việc 2)
- Nhánh: fix/text-consensus-logo (từ main 419377f) · Worktree: `temp\wt-text-consensus` · Cập nhật: 2026-10-16
- Kế hoạch đã được điều phối duyệt: `docs/handoff/D-text-consensus-plan.md` (phương án A). Bằng chứng:
  `docs/evidence/text-consensus/README.md`, `gate-summary.json`. Số đo có chữ OCR thật chỉ nằm trong
  `temp\text-consensus\` (không vào Git).
- Trạng thái: **phương án A (ca17c07), mục 6 (7fa0e0e) và việc 2 (commit sau) xong, mọi cổng đạt; chờ điều phối
  gộp.** Chưa push, chưa merge.
- Khóa cache bước `text` đổi một lần (phương án A và mục 6); việc 2 chỉ ở bước dựng, không đổi khóa nào.

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

## 3b. Việc 2: thẻ chữ cố định cả phim trong dải phụ đề (đã làm theo TDD, commit sau 7fa0e0e)
**Vì sao:** ở job 130, 134, 136 và 145, các câu thoại đọc nối nhau dưới phim gộp thành một nhóm chữ lặp lại, rồi
thành một thẻ chữ cố định cả phim phủ phụ đề.
- Thẻ đề xuất BLUR ở 130, 134 và 136, vì một câu thoại bị mô hình chấm điểm quảng cáo cao.
- 130 và 136 đã xuất với cả dải thoại bị mờ suốt phim. Người dùng xác nhận đó là không cố ý.
- Watermark dưới phụ đề đã có thẻ repeated_watermark riêng. Điều phối kiểm: watermark nằm trọn trong thẻ đó ở 15/15
  mẫu.

**Đo trước khi làm** (trên mọi báo cáo):
- 16 thẻ `recurring_fixed_text` cả phim trong dải phụ đề.
- 10 thẻ thật (job 62–66, 70–72, 99, 100): độ đồng thuận 0,993–1,0.
- 6 chuỗi thoại (130 rev1; 134 rev1; 136 rev1 và rev2; 145 rev1 và rev2): độ đồng thuận 0,25–0,375.

**Quy tắc** (`review_workflow.py`, chỉ ở bước dựng, không đổi khóa cache). Một thẻ `recurring_fixed_text` cả phim không
đề xuất gì khi đủ cả ba điều kiện:
1. tâm khung nằm trong dải phụ đề (y ≥ 0,70 H, x trong 0,18–0,82 W, trên khung phân tích);
2. không thành viên nào đọc ra:
   - địa chỉ web (kể cả mất dấu chấm);
   - số điện thoại hay @tên;
   - tên miền khác (đuôi như ".ph");
   - tên nền tảng;
3. độ đồng thuận < 0,60. Đây là tỉ lệ lớn nhất số thành viên có cách đọc dài nhất là mẩu (giống ≥ 0,75, như bước
   gộp) của cùng một cách đọc dài (từ 12 chữ) trong nhóm.

Cách thẻ đó hoạt động:
- **Vẫn ở danh sách chính**, ghi `subtitle_dialogue: {line_consensus}` và lý do: "giống chuỗi phụ đề thoại; logo dưới
  phụ đề (nếu có) đã có thẻ watermark riêng — chưa đề xuất làm mờ, bạn tự quyết". Dấu hiệu yếu vẫn được liệt kê.
- **Ngoại lệ:** thành viên nào gọi tên trang của một watermark khác thì thẻ giữ cách cũ.
- **Không cho thẻ đó che gì:**
  - Nó chỉ nuốt thẻ chữ thường: không đề xuất, điểm quảng cáo < 0,40, và không có `ad_text_evidence`.
    - `ad_text_evidence` là: trúng chính sách, hoặc đọc ra địa chỉ, số điện thoại, "@", tên miền hay tên nền tảng.
    - Dấu này được ghi trên mục chữ và giữ lại khi gộp thẻ.
  - Mọi thẻ khác trong khung vẫn là thẻ riêng, kể cả thẻ visual-logo.
  - Nó không gộp theo thời gian với thẻ khác.
  - Xác nhận visual-logo trong khung của nó không thành bằng chứng của nó.

**Review đọc mã** (ecc:code-reviewer), hai lỗi đã sửa kèm test:
- **HIGH:** bản đầu để thẻ đó nuốt thẻ quảng cáo không có đề xuất riêng (số điện thoại, @tên, trúng chính sách). Khi
  đó các thẻ ấy không còn được thấy.
- **MEDIUM:** điều kiện 2 thiếu các tên miền ngoài danh sách địa chỉ và tên nền tảng.

**Cổng** (`dialogue_gate.py`, `dialogue-gate-summary.json`; trước = 7fa0e0e, sau = nhánh):
- **Test:**
  - `test_subtitle_dialogue_overlay`: 13 test mới; 7 test hỏng trên 7fa0e0e.
  - 6 test còn lại giữ BLUR khi:
    - có địa chỉ, địa chỉ mất dấu chấm, số điện thoại, @tên, tên miền ".ph" hay tên nền tảng;
    - chuỗi ở trên khung hình;
    - chuỗi không kéo cả phim;
    - có thành viên gọi tên trang của watermark;
    - một dòng đọc thành mẩu trong dải.
  - 513 test của 31 module duyệt: OK.
  - Full suite: xem phần 3c.
- **Dựng lại từ báo cáo quét lại của mục 6, mọi revision:**

  | Job, revision | Thẻ thoại trước → sau (đồng thuận) | Watermark dải phụ đề BLUR tới / độ dài phim | Số thẻ |
  |---|---|---|---|
  | 130 r1 | BLUR → không (0,375) | 2646,5 / 2655,2 | 8 → 30 |
  | 134 r1 | BLUR → không (0,13) | 2643,5 / 2657,2 | 7 → 25 |
  | 136 r1, r2 | BLUR → không (0,333; thêm một thẻ chuỗi thứ hai) | 2691,5 / 2702,2 | 9 → 26 |
  | 143 r1, r2 | không có thẻ trong dải | 2668,1 / 2668,1 | 22 → 22 |
  | 145 r1, r2 | không → không (0,273) | 2561,2 / 2561,2 | 7 → 27 |

  - **Thẻ thêm** đều là thẻ trước đây nằm trong thẻ thoại.
    - Phần lớn là câu thoại bị mô hình chấm điểm quảng cáo ≥ 0,40: không có địa chỉ, số điện thoại, @ hay chính sách,
      không có đề xuất. Có 19 ở 130, 18 ở 134, 16 ở 136, 19 ở 145.
    - Trong số đó, ở 130, có một lần đọc tên trang không có dấu chấm (điểm 0,47).
    - Còn lại:
      - 130: một dòng địa chỉ web (1767–1785,5 s, BLUR), thẻ visual-logo của dấu trang (BLUR, cùng khung và thời gian
        với thẻ watermark), một câu thoại mà OCR đọc một chữ thành "@", và một thẻ chữ cố định;
      - 136: thẻ chuỗi thứ hai;
      - 145: một câu thoại mà OCR đọc một chữ thành "@" (với 7fa0e0e nó cũng nằm trong thẻ của dải).
  - Dải phụ đề yếu đi (thẻ chính trích dẫn): 0 ở cả 8.
  - Cách đếm chặt hơn (thẻ đề xuất BLUR trích dẫn): ít đi 1 ở 130. Đó là câu "@" (831–834 s), giờ là thẻ riêng,
    không có đề xuất.
  - Dựng lại giữ quyết định: mất 0 ở cả 8 (đã quyết 9, 42, 38, 9, 64, 22, 50, 7).
    - Quyết định BLUR cũ của người dùng trên thẻ thoại của 130 và 136 r2 vẫn còn: được mang sang, hoặc thẻ cũ được
      giữ.
    - Người dùng cần đổi thành KEEP. Bản dựng mới (fresh) không có quyết định nào trên thẻ đó.
- **25 job của cổng dải phụ đề (báo cáo đã lưu, revision đang dùng):**
  - 10 thẻ thật giữ nguyên đề xuất: BLUR ở 62, 63, 65, 66, 70, 71, 72, 99, 100; không đề xuất ở 64.
  - Thẻ thoại của 130, 134, 136 và 145 không còn đề xuất.
  - Số thẻ bằng nhau, trừ 130 (8 → 30), 134 (6 → 24), 136 r2 (9 → 26), 145 r2 (7 → 27); các thẻ thêm cùng loại như
    trên.
  - Dải phụ đề yếu đi: 0. Cách đếm chặt: vẫn chỉ câu "@" đó của 130.
  - Dựng lại giữ quyết định: mất 0 ở 10 job còn video gốc.
- **Golden v2** (báo cáo quét lại của mục 6, không dùng bộ nhớ):
  - scorecard trước và sau giống hệt: 598 mục chấm; quảng cáo 19/19; dương tính giả 24; thẻ tư vấn 487; bẫy 0;
  - so với baseline v2: +1 (G11), mất 0, bẫy mới 0;
  - `compare` thoát 1 chỉ vì cổng thời gian.
- **Khóa cache:** cả 10 bước giống 7fa0e0e.

**Đánh đổi (người dùng quyết):** vì thẻ không đề xuất gì thì không được che gì, mỗi phim trong bốn phim này có thêm
16–20 thẻ không đề xuất, gần như toàn là câu thoại.
- Nếu cho thẻ thoại nuốt cả các câu chỉ có điểm quảng cáo, số thẻ về lại 8–11.
- Nhưng làm vậy sẽ che lần đọc tên trang không dấu chấm của 130.
- Nó cũng che mọi dòng quảng cáo thật không có địa chỉ, số điện thoại, @ hay chính sách. Với 7fa0e0e các dòng đó nằm
  dưới BLUR của thẻ thoại ở 130, 134 và 136.

Đó là làm yếu dải phụ đề, nên chưa làm.

**Giới hạn:**
- Dải phụ đề và ngưỡng 0,60 mới đo trên một kiểu phụ đề: 4 phim có chuỗi thoại, 10 phim có dòng của trang.
  - Một dòng của trang bị đọc thành những mẩu rất khác nhau có thể xuống dưới 0,60.
  - Khi đó nó vẫn là thẻ, chỉ không có đề xuất.
- Quảng cáo chỉ đọc ra chữ giống thoại không nhận BLUR từ thẻ thoại. Nó vẫn là thẻ riêng của nó.

## 3c. Full suite (việc 2)
- `unittest discover` (BILIFLOW_TEST_INSTALL_ROOT là thư mục cài đặt), trên mã cuối, với một clip tổng hợp 1 s trong
  `input\` của worktree (đã xóa sau đó): 3.499 test trong 2.583 s, OK, 53 bỏ qua.

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

Việc 2: thẻ chuỗi thoại (dải phụ đề, suốt phim) của 130, 134, 136 và 145 không còn đề xuất BLUR.
- Hãy chọn KEEP cho thẻ đó, và BLUR cho thẻ watermark cùng các thẻ địa chỉ trong dải.
- Bản xuất của 130 và 136 (rev2) đang làm mờ cả dải thoại suốt phim. Nếu dựng lại mà giữ quyết định, quyết định BLUR
  cũ trên thẻ thoại vẫn còn: hãy đổi thành KEEP rồi xuất lại.
