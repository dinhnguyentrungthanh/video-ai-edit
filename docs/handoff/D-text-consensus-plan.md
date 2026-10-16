# Kế hoạch D – giữ hộp logo trong khung chung của bước quét chữ (job 145)
- Nhánh: fix/text-consensus-logo (từ main 419377f) · Worktree: `temp\wt-text-consensus` · 2026-10-16
- Trạng thái: **điều phối duyệt phương án A ngày 2026-10-16 (ca17c07); sau cổng, người dùng chọn làm mục 6 trên cùng
  nhánh (đã xong).** Kết quả: `docs/handoff/D-text-consensus.md`.
- Số đo thô (không vào Git): `temp\text-consensus\` (probe OCR, bản dựng thử).

## 1. Báo ngắn: vì sao "Chạy lại" không lấy visual_logo từ cache, và vì sao kết quả khác
- **Lỡ cache.** Khóa bước `visual_logo` (và `localize_logo`) có SHA-256 của `state\brand-memory.json`
  (`stage_cache.py`, dòng 210–213).
  - File đó đổi lúc 2026-10-15 23:25 (263 → 275 bản ghi, từ quyết định của người dùng trên phim khác), sau lần quét
    đầu của job 145 (11:27).
  - Code của bước không đổi từ 10-14.
  - Bộ đệm định tuyến riêng của máy quét (`cache\visual-logo\<sha>\routing-*.json.gz`) cũng khóa theo bản bộ nhớ
    thương hiệu: lần trước trúng, lần này trượt (60 s → 140 s).
  - Bước `text` không có bộ nhớ thương hiệu trong khóa nên trúng cache.
- **Kết quả khác.** Máy quét chỉ hỏi Qwen2-VL 175 trên 512 cửa sổ ứng viên (bỏ 337).
  - Với 12 bản ghi mới: 5 cửa sổ khớp thương hiệu đã duyệt (4 nhóm thời gian, 4 bị gộp), định tuyến theo cảnh 27 → 22.
    Vì vậy 175 cửa sổ được chọn đổi.
  - Lần 1: cửa sổ 175–180 s được chọn, mô hình trả lời không chắc → khoảng → OCR đọc ra chữ logo → thẻ visual_logo.
  - Lần 2: cửa sổ đó nằm trong phần bị bỏ, không được hỏi, cũng không bị từ chối.
  - Không phải do mô hình ngẫu nhiên. Mọi cách sửa ở bước dựng mà dựa vào một thẻ visual_logo của logo cố định đều
    mong manh. Bước chữ thì ổn định.

## 2. Nguyên nhân gốc (đo lại hôm nay)
- Probe OCR trên CPU, 9 khung của video gốc job 145 (0–2400 s, khung phân tích 960×402): lần nào logo cũng ở
  [644,38,862,68] (218×30).
- Dòng chữ chạy ở y khoảng 18–43.
- Track 2 (607 lần đọc, cả phim) là logo. Hộp min/max của nó là 305×53, vì vài lần đọc dính dòng chữ chạy.
  11 track kia là mẩu dòng chữ chạy, cao khoảng 25.
- `_consensus_overlay_box` cho mỗi track một phiếu ngang nhau:
  - trung vị chiều cao 25, nên hộp 53 (> 1,8 × 25) bị loại;
  - khung chung còn [432,18,960,43], tức chỉ là dòng chữ chạy.
  - Một track đọc 607 lần cả phim bị đối xử như một mẩu đọc một lần.
- Bước dựng rồi biến khung đó thành thẻ (0,21,1280,32): không phủ logo (logo ở x 865–1146, y 55–84 trên khung
  1280×536).

## 3. Cách sửa đề xuất (quy tắc chung, chỉ trong `textscan.py`)
**Phương án A, "thành viên neo" (đề xuất):**
1. `_summarize_track` thêm `footprint_box`: khung chung của chính các lần đọc của track (`_consensus_overlay_box`
   trên hộp từng lần đọc; dưới 4 lần đọc thì bằng `union_box`). Lần đọc lệch của một track dài bị bỏ ở đây.
2. `_promote_repeated_corner_overlays` vẫn tính khung chung như nay. Một thành viên là **neo** khi tự nó đã là chữ
   cố định: ít nhất 10 lần đọc và tự kéo dài ít nhất `minimum_span` của nhóm (12–60 s).
3. Hộp `footprint_box` của một neo được thêm vào khung khi đủ ba điều kiện:
   - chưa nằm ≥ 95 % trong khung chung;
   - chạm khung chung (chồng lên, hoặc cách không quá nửa chiều cao của nó);
   - cao không quá 2,5 lần khung chung.

   Khi đó `union_box` = hợp(khung chung, footprint). Ghi lại `overlay_box_sources` (khung chung cũ; mỗi neo: track,
   footprint, số lần đọc).
4. Neo nằm xa khung chung: giữ như nay, chỉ ghi vào `overlay_box_sources` để đo. Hôm nay chưa thấy ca nào.

Bảo đảm:
- Hộp không bao giờ nhỏ hơn hôm nay.
- Ca đang được bảo vệ vẫn bị bỏ như cũ: một lần đọc dính chữ cảnh làm hộp rộng ra (test
  `test_repeated_overlay_ignores_one_ocr_box_joined_to_scene_text`). Lý do: track một lần đọc không thể là neo, còn
  lần đọc lệch của neo bị footprint của chính nó bỏ.
- Không đổi ngưỡng nào, không đổi cách gộp nhóm, không đổi định tuyến hay điểm quảng cáo.

**Thử trên bản sao (root tạm, chỉ dựng danh sách, revision 2 của job 145, không có thẻ visual_logo 175–180):**
- Hiện tại: thẻ chữ cố định 0–2561 s (0,21,1280,32), không phủ logo.
- Với `union_box` [432,18,960,68] theo quy tắc trên: thẻ (0,21,1280,63), phủ logo.
- Không cần thẻ visual_logo, không cần sửa bước dựng. Thẻ vẫn đề xuất BLUR, cùng thời gian, 7 thẻ như cũ.
- Đáy thẻ (84) trùng đáy chữ logo đo trên ảnh (84): bước dựng tinh chỉnh theo điểm ảnh ổn định. Cổng xuất thử sẽ
  kiểm OCR có còn đọc được logo không.

**Các phương án đã cân và bỏ:**
- **B, cân phiếu theo số lần đọc:** logo thắng, dòng chữ chạy bị loại khỏi khung. Phần chữ chạy yếu đi.
- **C, hợp min/max mọi thành viên:** mở lại lỗi cũ: một lần đọc rộng thành dải mờ cả phim.
- **D, luôn tách neo thành track riêng:**
  - Track logo một mình có điểm quảng cáo 0,21 ("credits"). Điểm 0,70 của nhóm đến từ mẩu dòng chữ chạy, nên logo
    có thể mất đề xuất BLUR.
  - Track tách ra còn trùng `track_id` với track gộp (track mạnh nhất chính là neo).
  - Để sau, nếu đo thấy neo nằm xa.

## 4. Khóa cache và quét lại
- **Khóa:** chỉ `textscan.py` (và test) đổi, nên chỉ khóa bước `text` đổi.
  - 9 bước cache còn lại không đổi: `textscan.py` chỉ nằm trong `stage_source_paths(root, "text")`, đã kiểm cả 10
    bước.
  - Không bước nào nhận `text-scan.json` qua `--report` (đầu vào nằm trong khóa). `build_review` không có cache.
- **Phim phải quét lại:** sau khi gộp và khởi động lại Control Center, mọi phim bấm "Chạy lại" và mọi phim mới quét
  lại bước chữ. Không có quét tự động.
- **Phim có kết quả đổi:**
  - Trong 32 track gộp của 125 báo cáo quét chữ trên máy, chỉ track 2 của job 145 có thành viên chính lồi ra khỏi
    khung chung.
  - Đây là ước lượng theo cỡ hộp của thành viên mạnh nhất; cổng đo chính xác trên các phim quét lại.
  - Chỉ job 145 cần "Chạy lại" rồi duyệt lại. Chưa có nút giữ quyết định (mục 7).
- **Thời gian trên máy này (GPU):**
  - Bước text: 87–178 s, trung vị khoảng 125 s, cho phim 43–47 phút (job 145: 121 s).
  - Phim 112 phút: khoảng 3–4 phút.
  - "Chạy lại" thường quét lại cả visual_logo (khoảng 2,5 phút) vì bộ nhớ thương hiệu hay đổi (mục 1).

## 5. Mục 6 (giới hạn 250 track): có gộp vào cùng lần đổi khóa không
- **Đề xuất: không gộp mù; đo trước trong cùng lượt GPU của cổng.**
  - Quét các phim của cổng không giới hạn track, rồi mô phỏng giới hạn 250 ngoại tuyến.
  - Đếm thẻ mới mà các track bị bỏ sẽ tạo: dòng chữ chạy, địa chỉ web, watermark ở dải phụ đề.
  - Nếu có mất thật: thêm mục 6 vào cùng nhánh trước khi gộp, để main chỉ đổi khóa text một lần và phim cũ chỉ quét
    lại một lần.
  - Nếu không: giữ nguyên.
- **Công:**
  - Đo: không thêm GPU, khoảng 30 phút phân tích.
  - Làm mục 6: khoảng 2–3 giờ cộng cổng. Cách làm dự kiến: giữ track tham khảo ở dải trên/dưới và các góc vượt
    giới hạn; ảnh xem trước chỉ cho 250 track đầu.
- **Rủi ro của mục 6:**
  - thêm thẻ từ chữ cảnh muộn trong dải (bẫy mới);
  - báo cáo và cache lớn hơn;
  - trang duyệt dài hơn.
  - D-ticker đã thấy: job 107/108 chỉ bỏ credits và chữ cảnh cuối phim, không track quảng cáo nào.
- Chi phí thật của việc đổi khóa hai lần không phải thời gian quét (khoảng 2 phút mỗi phim) mà là phải duyệt lại
  phim sau "Chạy lại".

## 6. Việc 2 (chỉ sau khi điều phối duyệt), TDD
1. **Test đỏ (`tests/test_textscan.py`):**
   - (a) nhóm dạng job 145: 11 mẩu dòng chữ chạy cộng một neo 600 lần đọc ngay dưới → `union_box` chứa footprint
     của neo;
   - (b) ca một lần đọc dính chữ cảnh giữ nguyên [53,79,141,108];
   - (c) neo đã nằm trong khung → không đổi;
   - (d) neo xa → không đổi, có ghi lại;
   - (e) `_summarize_track`: footprint bỏ lần đọc lệch.
2. **Code** trong `textscan.py`.
3. **Clip tự làm** (ffmpeg drawtext, OCR thật trên CPU, không GPU): dòng "EXAMPLE.COM …" chạy ở dải trên và
   "Example.com" cố định ngay dưới suốt clip. Sau khi quét, `union_box` chứa logo; dựng danh sách, thẻ BLUR phủ logo.
4. **GPU** (hỏi người dùng trước, chỉ chạy khi hàng đợi thật trống; lúc lập kế hoạch cả 72 job đều COMPLETED):
   - quét bước chữ trên root tạm cho job 145, 130, 134, 136, 143 và golden v2 (phim 37, 38, clip của job 39);
   - khoảng 20–25 phút GPU;
   - lưu đầu vào của bước gộp, để baseline (hàm của main) và nhánh so trên cùng một kết quả OCR, không nhiễu GPU.
5. **Cổng:**
   - job 145: thẻ phủ logo;
   - xuất thử một cửa sổ khoảng 100 s: OCR không còn đọc được logo;
   - golden: mất 0, bẫy mới 0;
   - dải phụ đề yếu đi 0 ở 130/134/136/143/145;
   - khóa cache của 10 bước: chỉ text đổi;
   - test tập trung. Full suite do điều phối chạy.
6. **Tài liệu:** CHANGELOG, PROJECT_STATUS, SESSION_HANDOFF, bàn giao, bằng chứng `docs/evidence/text-consensus/`
   (chỉ số đếm).

Ước lượng: 3–4 giờ cộng khoảng 25 phút GPU. Với job 145, khi nhánh đã vào main và Control Center đã khởi động lại:
bấm "Chạy lại", BLUR thẻ chữ cố định (0,21,1280,63), duyệt lại các thẻ khác, rồi xuất lại.
