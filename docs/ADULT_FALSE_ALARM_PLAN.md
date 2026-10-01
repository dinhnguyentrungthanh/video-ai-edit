# Kế hoạch giảm báo nhầm 18+ (sau đó đến máu me) mà không bỏ sót cảnh khỏa thân thật

Ngày lập: 01/10/2026. Trạng thái: **đề xuất, cần người dùng trả lời mục 9 trước khi viết code**.
Nhánh `improve/scan-performance-metrics`, mốc `f54cc09` cộng working tree hiện tại (18 file đã sửa, chưa commit).
Phạm vi số liệu: nhóm 18+ của Troy job 39 revision 4 (nsfw-nano, ngưỡng 0.95, context 0.70, 2 fps, có sequence completion, không có temporal confirmation) và Golden v1 r469 + v1.1 r108. Tài liệu này không chứng nhận gì cho nhóm quảng cáo hay bạo lực.

Nguồn số liệu: `temp/adult-fp/taxonomy` (task A), `task-b-signals` (task B), `task-c-verifier` (task C), và `temp/adult-fp/plan` (phần tổng hợp này, chỉ đọc lại kết quả đã có, không chạy model).

---

## 0. Tóm tắt một trang

- **Vấn đề.** Ở nhóm 18+ của Golden, precision mục chính chỉ đạt **5/28 (17.9%)**. Ở Troy rev 4 là **7/93 (7.5%)**: người dùng KEEP 86 mục, tổng cộng **983 s** phải duyệt, và không mục nào trong số đó có khỏa thân thật.
- **Không sửa được bằng ngưỡng.** nsfw-nano gắn nhãn "porn" cho gần như mọi seed: 185/186 khung khỏa thân và 108/118 khung không khỏa thân. Median p(porn) gần như bằng nhau (0.94 so với 0.95). Mọi luật dựa trên p(porn) hay max score đều làm mất cảnh khỏa thân thật. **Giữ nguyên 0.95/0.70.**
- **Cách làm.** Thêm một bước phân loại *sau* detector. Bước này **chuyển** mục yếu sang "Ứng viên phụ" (danh sách *Xem tất cả ứng viên*), **không xóa** mục nào. Một mục chỉ bị chuyển khi **hai tín hiệu độc lập cùng yếu**:
  1. nano có ít khung seed;
  2. đầu **NSFW** của `image_safety_classifier_m` chấm thấp. Model này dùng giấy phép MIT, đã APPROVED và đã cài sẵn; hiện chỉ đầu NSFL được dùng cho máu me.
- **Phát hiện mới trong lần tổng hợp này.** Nếu dùng verifier *một mình* (max NSFW ≥ 0.8) thì trượt phép thử "cảnh lẻ":
  - Cảnh 2:00:10.5–2:00:25.5 (khỏa thân một phần, ngực được tay che) chỉ đạt NSFW max 0.692 nên sẽ bị chuyển nếu đứng riêng.
  - Verifier một mình cũng chuyển mục BLUR 1:54:29.5 của rev 4.
  - Khi kết hợp với số seed, cả hai trục đều có biên an toàn. Cảnh khỏa thân yếu nhất theo seed (2 seed) có NSFW 0.92, còn cảnh yếu nhất theo NSFW (0.692) có 28 seed.

| Bước | Luật (chuyển sang ứng viên phụ khi…) | Troy: số báo nhầm bị chuyển (trên 86) | Giây duyệt bớt (trên 983) | Mục chính Troy | Golden 18+ precision | BLUR / cảnh lẻ bị mất |
|---|---|---|---|---|---|---|
| Hiện tại | — | 0 | 0 | 93 (7.5%) | 5/28 (17.9%) | — |
| 1 | **R1**: job live_action và mục toàn seed "hentai" (credits) | 7 | 276.5 (28%) | 86 (8.1%) | 5/23 (21.7%) | 0 / 0 |
| 2 | R1 + (**< 2 seed VÀ NSFW max < 0.8**) | 44 | 513.0 (52%) | 49 (14.3%) | 5/16 (31.3%) | 0 / 0 |
| 3 (mục tiêu) | R1 + (**< 5 seed VÀ NSFW max < 0.8**) | 65 | 697.5 (71%) | 28 (25.0%) | 5/9 (55.6%) | 0 / 0 |
| 4 (chỉ sau phim thứ hai) | R1 + (< 8 seed VÀ NSFW max < 0.8) | 73 | 778.5 (79%) | 20 (35.0%) | 5/7 (71.4%) | 0 / 0 |

Ở mọi bước, Golden vẫn bắt đủ 6/6 nhãn 18+ trong mục chính. Chi phí GPU của bước 1 bằng 0. Bước 2–4 cần thêm một lượt chấm verifier: Troy có khoảng 2,525 khung, đo trên CPU thì riêng giải mã mất 76 s. Mức này nằm trong cổng thời gian +5% (khoảng 215 s trên 4,292 s).

---

## 1. Mục tiêu và số liệu hiện tại

### 1.1. Chính sách 18+ của người dùng (dùng làm sự thật)

- **Là 18+:** khỏa thân (ngực trần, mông trần, bộ phận sinh dục) hoặc hành vi tình dục.
- **Không phải 18+:** hôn hay vuốt ve khi còn mặc quần áo, đàn ông cởi trần, trang phục hở, credits.
- **Chưa chốt:** "khỏa thân ngụ ý", tức đắp chăn hoặc khung hình chỉ tới vai. Xem câu hỏi 1, mục 9.

### 1.2. Troy job 39 rev 4 (người dùng đã duyệt đủ 4 nhóm)

- **93 mục 18+:** 7 BLUR và 86 KEEP. Con số "8 BLUR" trước đây tính cả 12:26–12:37: mục này từng BLUR, sau đó bị xóa quyết định, rồi chuyển KEEP.
- **Lịch sử sửa quyết định:** 15:28.5–15:50.5 đi từ KEEP sang BLUR (đã sửa); 54:17.5–54:28 đi từ CUT sang KEEP.
- **7 mục BLUR:** 6 mục có khỏa thân thật. Mục còn lại (1:54:29.5–1:54:43.5) là người phụ nữ đắp chăn, chỉ lộ vai và chân, tức khỏa thân ngụ ý.
- **Sự thật theo khung:** 11 đoạn khỏa thân rõ, tổng khoảng 169 s, lấy từ `task-b-signals/frame-truth-spans.csv`.

Bảng dưới là phân loại 86 báo nhầm (task A):

| Nguyên nhân | Mục | % mục | % giây |
|---|---|---|---|
| Đàn ông cởi trần / áo giáp (trận đánh) | 41 | 47.7 | 43.5 |
| Cận cảnh da (mặt, tay, chân; có 2 mục em bé, 1 mục đùi dính máu) | 14 | 16.3 | 8.2 |
| Cặp đôi mặc quần áo / hôn | 9 | 10.5 | 6.6 |
| Cảnh tối | 8 | 9.3 | 7.2 |
| Credits / chữ trên nền đen (nhãn `hentai`) | 7 | 8.1 | 28.1 |
| Trang phục hở / múa | 6 | 7.0 | 5.6 |
| Cặp đôi cởi đồ nhưng khung chỉ tới vai | 1 | 1.2 | 0.8 |

### 1.3. Golden v1+v1.1 (scorecard `golden-baseline-v1v11-20261001-095204`)

| Tập | Nhãn 18+ bắt được | Precision mục chính |
|---|---|---|
| all | 6/6 (must_catch 6/6) | 5/28 (17.9%) |
| dev (T1, T4, T5, T7) | 3/3 | 3/19 (15.8%) |
| holdout (T2, T6, T8) | 3/3 | 2/9 (22.2%) |

- **23 báo nhầm**, chia theo nguyên nhân:
  - cởi trần / giáp: 11;
  - credits (T4): 5 mục, chiếm 51% số giây;
  - cận cảnh da: 4;
  - hôn: 1;
  - trang phục hở: 1;
  - đắp chăn: 1.
- **5 mục hữu ích:** 4 mục khỏa thân thật, cộng gs-T5-0004 (khỏa thân ngụ ý).
- **Tất cả 28 mục 18+ chính đều từ Troy** và khớp 1-1 với các mục của rev 4.

### 1.4. Hai nhãn mâu thuẫn (người dùng phải chốt; agent không sửa annotation)

| Khoảng | Rev 4 | Golden | Nội dung khung | Verifier NSFW max | Seed |
|---|---|---|---|---|---|
| 1:54:29.5–1:54:43.5 (6869.5–6883.5 s, T6 holdout) | BLUR | false_positive | đắp chăn tối màu, lộ vai và chân, không thấy khỏa thân | 0.334 | 17 |
| 17:16.5–17:24 (1036.5–1044 s, gs-T5-0004, T5 dev) | KEEP | useful (BLUR) | cặp đôi cởi đồ, khung chỉ tới vai | 0.818 | 2 |

### 1.5. 18+ hoạt hình (Conan, dùng anime tagger)

- **5 mục, chưa ai duyệt, không nằm trong đoạn Golden nào.** Theo đọc khung, cả 5 đều là báo nhầm:
  - 3 mục là khung tối hoặc khói;
  - 1 mục là dáng cúi người, vẫn mặc quần áo;
  - 1 mục là điện thoại đặt trên bàn.
- **Chưa có ví dụ dương tính.** Không có ví dụ khỏa thân hoạt hình nào để kiểm, nên **chưa có luật hoạt hình nào kiểm chứng được** (xem mục 8).

### 1.6. Ghi chú dữ liệu cho phần cài đặt (đã kiểm trong lần tổng hợp này)

- **Đếm seed lấy được từ dữ liệu sẵn có.** Với cả 93/93 mục của rev 4, tổng `sample_count` của các interval trong `source_candidate_refs` **bằng đúng** số seed mà task B đo lại. Vì vậy số seed tính được ngay trong `build-review` từ `adult/scan.json`, không cần quét lại.
- **Credits nhận ra được từ nhãn interval.** Cả 7 mục credits có `predicted_label = hentai` ở mọi interval, và mọi seed của chúng đều là hentai. Không mục nào khác có interval mang nhãn hentai; mục 2:00:10.5 chỉ có 1/83 seed hentai.
- **Có thể chỉ chấm verifier trên khung nano ≥ 0.70** (`plan/gated-variant.json`). Kết quả gần như không đổi: luật k=5, t=0.8 chuyển 66 thay vì 65 mục, không mất BLUR nào và không bỏ cảnh lẻ nào. Trên các mục Troy chỉ có 1,417 khung như vậy, so với 2,475 khung tổng.

---

## 2. Các phương án, xếp theo thứ tự nên làm

"Cảnh lẻ" nghĩa là: nếu mỗi đoạn khỏa thân rõ đứng một mình thành một mục, luật có chuyển nó đi không. Có 11 đoạn như vậy. Mọi luật bên dưới đều chỉ **chuyển sang ứng viên phụ**.

| # | Phương án | Troy: báo nhầm bị chuyển | Mất BLUR rev 4 | Cảnh lẻ bị chuyển | Golden 18+ (all · dev · holdout) | Biên an toàn trên Troy | Chi phí | Kết luận |
|---|---|---|---|---|---|---|---|---|
| A | **R1 – credits:** job `live_action` và mọi interval mang nhãn `hentai` | 7/86 (276.5 s) | 0 | 0 | 5/23 · 3/14 · 2/9 | Mục thật có ít hentai nhất: 1/83 seed | 0 GPU, chỉ ở `build-review` | **Làm ngay** |
| B | **R2 có bảo vệ:** < 2 seed VÀ NSFW max < 0.8 | 44 khi cộng A (513 s) | 0 | 0 | (cùng A) 5/16 · 3/9 · 2/7 | Cảnh thật ít seed nhất có 2 seed và NSFW 0.92 (biên 0.12) | 1 lượt verifier | Làm sau A; thay cho R2 "trần" (biên 0) |
| C | **Hai tín hiệu:** < 5 seed VÀ NSFW max < 0.8 | 65 khi cộng A (697.5 s) | 0 | 0 | (cùng A) 5/9 · 3/5 · 2/4 | NSFW: 0.92 với 0.8 (biên 0.12). Seed: cảnh NSFW thấp nhất có 28 seed so với ngưỡng 5 | như B | **Mục tiêu.** Bật mặc định sau khi qua cổng 4.1–4.5 |
| D | < 8 seed VÀ NSFW max < 0.8 | 73 khi cộng A (778.5 s) | 0 | 0 | (cùng A) 5/7 · 3/4 · 2/3 | NSFW biên 0.12. Cảnh thật có < 8 seed (7, 7, 5, 2) đều có NSFW ≥ 0.92 | như B | Chỉ bật khi phim thứ hai qua cổng 4.6 |
| — | Verifier một mình (NSFW max ≥ 0.8) | 76 | 1 (1:54:29.5) | **1** (2:00:10.5, NSFW 0.692) | 5/6 | âm | như B | **Không dùng** |
| — | ≥ 3 seed, ≥ 2 seed liên tiếp, cổng temporal, chỉ giữ mục "high" | 50–67 | 0 | **1–2** (2:01:05.5 có 2 seed; 7:21.5) | 4/7–4/15, mất gs-T5-0004 | âm | 0 | **Không dùng** |
| — | Temporal confirmation đang có (cắt interval) | 61 | 0 mục | mất 25.75 s khỏa thân rõ | 4/11 | âm | 0 | **Không dùng** |
| — | p(porn) max ≥ 0.98, hoặc ≥ 3 khung có p(porn) ≥ 0.97 | 68 / 79 | 1 (2:01:05–2:01:20) | 2–4 | 4/5 | âm | 0 | **Không dùng** |
| — | Đa số seed là "sexy" | 9 | 1 (1:54:29.5) | 0 | 5/26 | — | 0 | Chỉ dùng nếu người dùng chốt "đắp chăn không phải 18+"; lợi ích nhỏ |
| — | Qwen2-VL-2B, câu hỏi khỏa thân chặt | — | — | — | — | Trả lời "có" với cởi trần, hôn, vai trần | khoảng 1.16 s / mục trên GPU | Không dùng làm cổng; nhiều nhất chỉ là ghi chú phụ |
| — | Florence-2 với từ khóa "naked" | — | — | bỏ sót cảnh ngực trần | — | — | 7–11 s/khung (CPU) | Không dùng |

**Lưu ý phương pháp: biên an toàn có giới hạn.**
- Ngưỡng k và t được chọn sau khi đã nhìn toàn bộ Troy, kể cả T2, T6, T8. Vì vậy holdout Golden **không còn sạch** với các luật này.
- Bằng chứng độc lập duy nhất sẽ là một phim live-action thứ hai (cổng 4.6).
- Biên 0.12 trên trục NSFW được đo trên 11 đoạn của một phim.

**Phương án dự phòng chưa cài.** Không tải về khi chưa có yêu cầu. Giấy phép đã kiểm qua Hugging Face/GitHub API ngày 01/10/2026; tốc độ chỉ là ước lượng.

| Model | Giấy phép | Kích thước | Ghi chú |
|---|---|---|---|
| Marqo/nsfw-image-detection-384 | Apache-2.0 | khoảng 22 MB | Phương án thay thế verifier nếu C/D trượt ở phim thứ hai |
| google/siglip2-base-patch16-224 | Apache-2.0 | khoảng 0.75 GB fp16 | Zero-shot. SigLIP v1 đã trượt bài kiểm logo của dự án |
| InternVL3-1B, SmolVLM2-2.2B, moondream2 (ghim revision) | Apache-2.0 | 1–4.5 GB | VLM nhỏ. SmolVLM từng thử và bị xóa ở dự án |
| *Bị loại* | NudeNet (AGPL-3.0, ngoài danh sách cho phép); Falconsai (đã loại, báo 22/22 hard negative); Qwen2.5-VL-3B (phi thương mại) | | |

---

## 3. Chi phí và giấy phép

| Hạng mục | Thời gian chạy | GPU | Giấy phép |
|---|---|---|---|
| R1, đếm seed, áp luật trong `build-review` | vài ms | 0 | — |
| Verifier V-c: stage `verify-adult` riêng, giải mã lại các khoảng 18+ ở 2 fps (**khuyến nghị làm trước**) | Troy: 2,525 khung. Đo trên CPU 4 luồng: giải mã 76 s, model khoảng 30 ms/khung. Trên GPU *ước lượng* < 10 s cho phần model | Thấp: weights 45 MB. VRAM *ước lượng* < 300 MB, chưa đo | `image_safety_classifier_m`: MIT, APPROVED. `manifest.json` đã khai `target_labels: ["NSFL","NSFW"]` nên không cần đổi manifest |
| Verifier V-a: chạy trong `scan_nsfw`, chỉ trên khung nano ≥ 0.70 (tối ưu về sau) | Không phải giải mã thêm. Troy có 1,417 khung trong các mục; số khung toàn phim chưa đo | như trên | như trên |
| V-b: dùng lại p(NSFW) từ lượt quét máu me của `live_safety` | 0 model | 0 | Chưa kiểm: khung pad 256 thay vì 448, gắn hai nhóm detector với nhau, và chỉ có khi job chọn máu me. **Không làm bây giờ** |

Cổng thời gian của `compare --gate detector` là +5%, tức khoảng 215 s trên 4,292 s pipeline Golden. V-c phải lọt trong ngân sách này; nếu không thì chuyển sang V-a.

---

## 4. Các cổng phải qua (ghi trước khi đo, không sửa sau khi đã thấy kết quả)

### 4.1. Golden v1 và v1.1, chấm từng bộ

Chạy `scripts/evaluate_golden.py compare --gate detector` với scorecard của từng bộ (`scorecard-v1.json`, `scorecard-v1.1.json`). Cổng này đã có sẵn ba điều kiện: không giảm recall must_catch, precision không giảm quá 2 điểm, không trúng bẫy mới. Kế hoạch thêm các điều kiện sau:

- 18+: **6/6 nhãn được bắt ở mục chính**, `advisory_only = 0`, `partial = 0`, must_catch 6/6.
- Precision 18+ mục chính (gộp hai bộ) phải tăng đúng như số đã đo trước:
  - bước 1: ≥ 5/23;
  - bước 2: ≥ 5/16;
  - bước 3: ≥ 5/9.
- Bốn nhóm còn lại (quảng cáo, máu me, bạo lực, logo/chữ) phải giữ **nguyên danh sách mục** trong cả `items` và `advisory_items`, so theo id và khoảng thời gian.
- "Nhiễu advisory" của 18+ sẽ tăng. Đây là hệ quả chủ định nên chỉ báo cáo, không tính là trượt.

### 4.2. Kiểm riêng holdout và đoạn credits

- **T6 (holdout):**
  - 1:53:16 (hành vi tình dục, có khỏa thân) phải nằm ở mục chính;
  - mục mâu thuẫn 1:54:29.5 phải giữ ở mục chính, cho tới khi người dùng chốt câu hỏi 1. Luật C giữ mục này vì nó có 17 seed.
- **T2 (holdout):** 6:54.5 phải ở mục chính.
- **T8 (holdout):** chỉ được phép chuyển mục báo nhầm.
- **T4 (dev, credits):** cả 5 mục phải chuyển sang ứng viên phụ, và T4 không được có nhãn 18+ nào bị ảnh hưởng.
- **gs-T5-0004:** phải ở mục chính (NSFW 0.818, biên chỉ 0.018 so với t = 0.8) cho tới khi người dùng chốt câu hỏi 1.

### 4.3. Bộ kiểm thứ hai: quyết định của người dùng trên Troy rev 4

- **Cách dựng lại queue.** Dựng queue mới vào `reports/benchmarks/adult-triage-<thời điểm>/`, từ chính các report của job 39. Không ghi vào `reports/jobs`.
- **Yêu cầu 1.** Cả 7 mục BLUR phải ở mục chính.
- **Yêu cầu 2.** Phép thử cảnh lẻ không được chuyển đoạn nào trong 11 đoạn khỏa thân rõ. Áp luật cho từng đoạn, dùng số seed và NSFW max *trong chính đoạn đó*.
- **Yêu cầu 3.** Số báo nhầm bị chuyển phải bằng số dự kiến ±1: bước 1 là 7, bước 2 là 44, bước 3 là 65.
- **Yêu cầu 4.** Không mục nào đã có `decision` bị đổi chỗ.

### 4.4. Verifier trong production cho cùng điểm với lúc đo

Chấm lại 2,525 khung Troy bằng production (GPU, fp32). NSFW max của mỗi mục phải lệch không quá **±0.01** so với `task-c-verifier/safety_m_items.json`, với cùng chuỗi tiền xử lý: `fps=2`, pad 448, rồi `timm create_transform` theo `config.json`.

### 4.5. Không làm hỏng phần còn lại

- Chạy đủ bộ test: `.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v`.
- Chạy `.\scripts\run.ps1 license-audit`. Manifest không đổi, nhưng tài liệu giấy phép có thêm mô tả cách dùng.
- Thời gian pipeline không tăng quá 5%.

### 4.6. Phim live-action thứ hai, bắt buộc trước khi bật bước 4

Phim do người dùng cung cấp và đặt trong `input\`. Phim cần có ít nhất 3 cảnh khỏa thân thật và các cảnh dễ gây nhầm: trận đánh cởi trần, hôn, credits. Cần gán nhãn sự thật theo khung cho các đoạn có khỏa thân.

- **Yêu cầu 1.** 0 cảnh khỏa thân thật bị chuyển.
- **Yêu cầu 2.** Không cảnh khỏa thân thật nào có NSFW max < t + 0.05, tức nằm sát biên.
- **Yêu cầu 3.** Precision 18+ phải tăng.
- **Hệ quả.** Nếu phim này trượt, bước 3 trở về bước 2 làm mặc định.

---

## 5. Điều kiện dừng (gặp một điều kiện là dừng, hoàn nguyên luật, ghi lại bằng chứng)

1. Có nhãn 18+ nào của Golden rơi xuống ứng viên phụ hoặc bị bỏ sót, hoặc có mục BLUR nào của Troy rev 4 (theo chính sách đã chốt) bị chuyển.
2. Phép thử cảnh lẻ chuyển bất kỳ đoạn khỏa thân rõ nào.
3. Verifier production lệch quá ±0.01 so với lúc đo trên CPU (cổng 4.4). Khi đó dừng và tìm nguyên nhân, **không** chỉnh t cho khớp.
4. Ở phim thứ hai, có cảnh khỏa thân thật nằm trong vùng bị chuyển (ít seed và NSFW thấp).
5. Thời gian pipeline tăng quá 5% mà V-a cũng không kéo về dưới mức đó được.
6. Bất kỳ nhóm nào khác thay đổi danh sách mục.
7. Người dùng trả lời rằng "khỏa thân ngụ ý" là 18+. Khi đó đổi t từ 0.8 xuống **0.7**, vì gs-T5-0004 có NSFW 0.818 và cần biên ≥ 0.1. Luật k = 5, t = 0.7 chuyển 59/86 mục (task B và bảng `plan/and-grid.json`), không bao giờ dùng luật "sexy", rồi đo lại toàn bộ mục 4.

---

## 6. Các bước triển khai (theo file)

**Bước 0. Chốt nhãn (người dùng làm).**
- Người dùng trả lời câu hỏi 1.
- Nếu cần, người dùng tự sửa gs-T5-0004 hoặc nhãn T6 bằng app gán nhãn (`Golden-Label-v1.1.cmd`) để tạo revision mới. Agent không sửa `annotations/`.
- Sau đó chấm lại baseline với revision nhãn mới, rồi mới đo các luật.

**Bước 1. R1 – credits trên live action.**
- `src/biliflow/review_workflow.py`:
  - Thêm hàm `triage_adult_items(items, scan_payloads, job_content_style, verification)`, trả về `(required, advisory)` theo đúng khuôn của `_quarantine_uncorroborated_visual_regions` và `revalidate_preserved_review_items`.
  - Gọi hàm **sau** `preserve_unresolved_review_items` và `revalidate_preserved_review_items`, **trước** `promote_strong_adult_priorities`.
  - Chỉ áp cho mục `category == "adult"` lấy từ report `scan_type == "nsfw"`, có `decision is None`, và chỉ khi `job_content_style == "live_action"`. Job `mixed` và `animation` không áp; thiếu content style cũng không áp (fail-safe).
  - Mục bị chuyển nhận các trường:
    - `advisory=True`, `priority="context"`;
    - `suggested_decision=None`, để không ngụ ý rằng mục đã được chứng minh an toàn;
    - `adult_triage={rule, n_seeds, labels, verifier_max, k, t, model, revision}`;
    - một câu lý do tiếng Việt.
  - Queue ghi thêm khối `adult_triage` ở cấp gốc (tham số và số mục bị chuyển) để kiểm toán.
- `build_review_queue(...)` nhận thêm tham số `content_style`.
- `src/biliflow/cli.py`: lệnh `build-review` thêm `--content-style`.
- `src/biliflow/job_pipeline.py`: truyền `content_style` của job vào lệnh `build-review`.
- `tests/test_review_workflow.py`, các ca cần có:
  - chỉ áp trên live_action;
  - không áp trên mixed hay animation;
  - mục đã có quyết định giữ nguyên;
  - mục chưa quyết định được giữ lại từ queue cũ thì được phân loại lại;
  - mọi `source_candidate_refs` vẫn được đại diện (kiểm coverage);
  - id không trùng.
- Tùy chọn **1b**: `scanner.py` và `intervals.py` ghi thêm `seed_label_counts` cho mỗi interval. Trường này chỉ thêm vào, phải có test chứng minh start/end/sample_count/max_score không đổi. Khi đó R1 chuyển sang dùng "đa số seed là hentai"; report cũ vẫn dùng `predicted_label`.

**Bước 2. Verifier `verify-adult` (V-c).**
- `src/biliflow/adult_verification.py` (mới):
  - Đọc `adult/scan.json`, giải mã từng interval ở 2 fps với filter giống `scan_nsfw` (pad 448).
  - Chấm đầu NSFW của `image_safety_classifier_m`, dùng lại loader timm của `content_scanner._load_classifier` và thêm lựa chọn nhãn đích "NSFW".
  - Ghi `adult/scan-verified.json`. File này là bản sao của scan, mỗi interval có thêm `adult_verification={state: SCORED|FAILED, nsfw_max, nsfw_frames, sample_fps, preprocessing, model, revision}`.
  - **Không bao giờ bỏ interval.** Kiểm `input_sha256` và việc file nguồn không bị đổi trong lúc chạy, giống `scan_nsfw`.
- `src/biliflow/cli.py`: thêm lệnh `verify-adult --report --output --device`, theo khuôn `confirm-violence`.
- `src/biliflow/job_pipeline.py`:
  - Thêm stage `verify_adult` (`uses_gpu=True`), chạy sau stage `adult` khi job là live_action hoặc mixed và có chọn adult.
  - `_report_paths` dùng `scan-verified.json` khi file tồn tại.
  - Đếm thời gian stage này vào `trial.json`.
- `tests/test_adult_verification.py` (mới): dùng model giả và khung tổng hợp; kiểm schema, kiểm không bỏ interval, kiểm trạng thái FAILED khi lỗi.
- Thêm test stage cho pipeline vào file test pipeline hiện có.
- `docs/LICENSE_POLICY.md`: ghi rằng đầu NSFW của `image_safety_classifier_m` được dùng làm verifier 18+ cho live action. Model và giấy phép giữ nguyên.

**Bước 3. Luật hai tín hiệu.** Thêm vào `triage_adult_items`:
- **Cách tính `n_seeds`.** Bằng tổng `sample_count` của các interval mà `source_candidate_refs` trỏ tới (đã kiểm khớp 93/93).
- **Cách tính `verifier_max`.** Lấy max `nsfw_max` trên các interval đó.
- **Điều kiện chuyển.** Chuyển khi `n_seeds < k` **và** `verifier_max < t`.
- **Khi thiếu verification hoặc state ≠ SCORED: không chuyển.**
- **Tham số.**
  - Hằng số nằm ở một chỗ, kèm chú thích dẫn tới bằng chứng: `ADULT_TRIAGE = {"conservative": (2, 0.8), "balanced": (5, 0.8)}`.
  - Mặc định là `conservative`. Chỉ chuyển sang `balanced` khi qua cổng 4.1–4.5 và người dùng đồng ý (câu hỏi 4).
  - k = 8 chỉ bật sau cổng 4.6.

**Bước 4. Benchmark tái lập.**
- `scripts/benchmark_adult_triage.py` (mới). Script này:
  1. dựng lại queue của Troy, Conan 20 và Conan 21 từ report có sẵn (đúng ba queue trong `scorecard.json → queues`) vào `reports/benchmarks/adult-triage-<ts>/`;
  2. chạy `verify-adult` cho report adult của Troy vào cùng thư mục;
  3. chấm bằng `golden_scoring` cho v1 và v1.1, rồi chạy `compare --gate detector` với baseline theo từng bộ, dùng `--trial` của baseline cộng thời gian stage verify vừa đo;
  4. kiểm Troy rev 4: so với quyết định của người dùng, chạy phép thử cảnh lẻ với `frame-truth-spans.csv`, chép file này vào thư mục bằng chứng.
- Script chỉ chạy GPU khi không có benchmark nào khác đang chạy.

**Bước 5. Tài liệu.** Sau mỗi mốc đã kiểm, cập nhật `CHANGELOG.md`, `docs/PROJECT_STATUS.md` và `docs/SESSION_HANDOFF.md`. Ghi chính xác job/revision và phạm vi detector (chỉ nhóm 18+), và thêm một dòng tham chiếu trong `docs/QUALITY_PLAN.md`.

**Bước 6 (sau, tùy chọn). V-a.** Nếu V-c tốn quá ngân sách thời gian, chuyển phần chấm verifier vào `scan_nsfw`, chỉ trên khung nano ≥ 0.70. Cần chứng minh interval của scanner không đổi, rồi chạy lại cổng 4.4.

---

## 7. Máu me (làm sau khi 18+ qua bước 3)

**Hiện trạng Golden:**
- 17 nhãn máu me, bắt đủ 17/17; must_catch 5/5.
- Precision mục chính **16/32 (50%)**: dev 8/18, holdout 8/14.

**Phân bố báo nhầm:**
- **Cả 16 báo nhầm đều ở hoạt hình Conan** (anime tagger): Conan 20 có 7, Conan 21 có 9.
- Nhãn của báo nhầm: blood 7, injury 8, pink_blood 1.
- Nhãn của mục hữu ích: blood 10, injury 2, corpse 3, NSFL 1 (Troy). Riêng nhãn không tách được hai bên.

**Ngoài Golden:**
- Queue Conan 20 có 55 mục máu me (29 high), Conan 21 có 50 mục (25 high). Tất cả **chưa có quyết định**.
- Troy rev 4 chỉ có 1 mục máu me (KEEP). Máu me live action hiện không phải vấn đề.

Các bước:

- **G0 – Phân loại nguyên nhân.** Làm như task A, chạy trên CPU:
  - xem 16 báo nhầm, 16 mục hữu ích Golden và 105 mục trong queue Conan;
  - dự kiến các nhóm: vật hoặc ánh sáng màu đỏ, kiểu "pink blood" cách điệu, vết thương không chảy máu, cảnh tối.
- **G1 – Kiểm thứ tự kênh màu của wd-tagger.**
  - Ví dụ upstream đổi RGB sang BGR, còn production đang đưa vào RGB.
  - Bước này chỉ đo: chấm lại các đoạn Conan của Golden trên CPU theo cả hai thứ tự, so 18+, máu me và bạo lực hoạt hình.
  - Sửa thứ tự kênh màu là thay đổi detector. Chỉ sửa khi qua cổng detector của cả ba nhóm hoạt hình.
- **G2 – Tín hiệu.**
  - Dùng vector tag của tagger: tag blood/injury/pink_blood/corpse, rating, `no_humans`, độ sáng, độ dài chuỗi khung.
  - Áp cùng nguyên tắc "hai tín hiệu yếu mới được chuyển".
  - Không dùng `image_safety_classifier_m` NSFL cho hoạt hình, vì đã kém ở task C.
- **G3 – Cổng.**
  - Máu me must_catch 5/5 và 17/17 nhãn ở mục chính;
  - precision tăng;
  - quyết định của người dùng trên Conan 20/21 (nếu có) là bộ kiểm thứ hai;
  - không đổi ngưỡng.
- **Dừng khi:** mất bất kỳ nhãn máu me nào, hoặc chỉ tìm được luật có biên bằng 0.

---

## 8. 18+ hoạt hình (Conan)

- **Chưa thêm luật nào.** Hiện có 5 mục, đều là báo nhầm theo đọc khung, và không có ví dụ dương tính.
- **Vì sao điểm cao:** điểm tổng cộng từ 20 tag khiêu dâm, nên nhiều tag yếu cộng lại. Tag mạnh nhất chỉ 0.24–0.65 nhưng điểm tổng lên tới 0.47–0.97. Ba mục là khung rất tối, hai mục có `no_humans` cao.
- **Cần trước khi làm luật:**
  - quyết định của người dùng cho 5 mục này;
  - kết quả G1 (thứ tự kênh màu);
  - nếu được, một nguồn hoạt hình có khỏa thân thật mà người dùng có sẵn.

---

## 9. Câu hỏi cho người dùng

1. **"Khỏa thân ngụ ý" có tính là 18+ không?** Ví dụ là cảnh đắp chăn, hoặc cặp đôi cởi đồ nhưng khung hình chỉ tới vai. Có hai mục cụ thể:
   - **1:54:29.5–1:54:43.5:** bạn chọn BLUR ở rev 4, nhưng Golden ghi là báo nhầm.
   - **17:16.5–17:24 (gs-T5-0004):** Golden ghi BLUR, nhưng bạn chọn KEEP ở rev 4.

   Theo chính sách bạn đã nêu thì cả hai *không* phải 18+. Nếu bạn đồng ý, hãy tự sửa gs-T5-0004 bằng app gán nhãn. BLUR ở 1:54:29 vẫn giữ được như một lựa chọn biên tập, chỉ không còn là mục bắt buộc phải bắt. Nếu bạn coi đó là 18+, kế hoạch dùng t = 0.7 (mục 5, điều kiện 7).
2. **Bạn có đồng ý cơ chế "chuyển sang Ứng viên phụ" cho 18+ không?** Mục bị chuyển **không chặn xuất video**. Nếu một mục thật bị chuyển nhầm, nó chỉ được làm mờ khi bạn mở *Xem tất cả ứng viên*. Các cổng ở mục 4 có mục đích ngăn trường hợp đó.
3. **Bạn có đồng ý dùng đầu NSFW của `image_safety_classifier_m` làm bộ kiểm thứ hai không?** Model này giấy phép MIT, đã duyệt và đã cài; không cần tải gì thêm.
4. **Mức mặc định:** bật ngay mức bảo thủ (k = 2), rồi bật mức cân bằng (k = 5) khi qua cổng trên Troy và Golden. Hay bạn muốn chờ phim thứ hai mới bật k = 5?
5. **Bạn có phim live-action thứ hai trong `input\` không?** Phim cần có cảnh khỏa thân thật và cảnh dễ nhầm (trận đánh cởi trần, hôn, credits) để làm cổng 4.6. Không tải từ mạng.
6. **Máu me hoạt hình:** pink_blood (máu cách điệu), vết thương không chảy máu và xác có cần làm mờ không? Bạn có thể duyệt 105 mục máu me của Conan 20/21 (hoặc chỉ phần trong Golden) để làm bộ kiểm thứ hai không?
7. **5 mục 18+ của Conan:** bạn duyệt KEEP hay BLUR cho từng mục để lưu làm bằng chứng?

---

## 10. Bằng chứng và cách tái lập

- **Task A:**
  - `temp/adult-fp/taxonomy/adult-false-alarm-taxonomy.csv`
  - `temp/adult-fp/taxonomy/adult-false-alarm-summary.json`
  - `temp/adult-fp/taxonomy/sheets/` (contact sheet theo mục)
- **Task B:**
  - `temp/adult-fp/task-b-signals/results.json`
  - `temp/adult-fp/task-b-signals/rule-evaluation.csv` (52 luật)
  - `temp/adult-fp/task-b-signals/troy-rev4-adult-items.csv`
  - `temp/adult-fp/task-b-signals/golden-troy-adult-items.csv`
  - `temp/adult-fp/task-b-signals/frame-truth-spans.csv`
  - `temp/adult-fp/task-b-signals/troy-frame-scores.csv`: 6,848 khung. Từ các điểm này dựng lại đúng cả 119 interval production.
- **Task C:**
  - `temp/adult-fp/task-c-verifier/safety_m_items.json` (chuỗi NSFW theo khung của mọi mục Troy và Golden)
  - `temp/adult-fp/task-c-verifier/safety_m_items.py`
  - `temp/adult-fp/task-c-verifier/per_case_summary.json`
  - `temp/adult-fp/task-c-verifier/qwen_results.jsonl`
  - `temp/adult-fp/task-c-verifier/florence_results.json`
- **Tổng hợp** (chỉ đọc các file trên, không chạy model):
  - `temp/adult-fp/plan/stack_rules.py` và `stack-results.json`: các luật xếp chồng, phép thử cảnh lẻ cho verifier.
  - `temp/adult-fp/plan/and_grid.py` và `and-grid.json`: lưới k × t của luật hai tín hiệu.
  - `temp/adult-fp/plan/gated_variant.py` và `gated-variant.json`: verifier chỉ chạy trên khung nano ≥ g.
- **Lưu ý về dữ liệu nhạy cảm:** `temp/adult-fp/task-b-signals/visual/` chứa khung hình các cảnh khỏa thân. Thư mục này chỉ để ở máy và có thể xóa sau khi kế hoạch được duyệt.
- **Bất biến giữ nguyên:**
  - ngưỡng 0.95/0.70 và sampling không đổi;
  - không sửa hay xóa video nguồn, `reports/jobs`, `state`, `annotations` hay brand memory;
  - mọi model đều chạy local và có giấy phép thương mại;
  - detector chỉ tạo ứng viên, người dùng quyết định KEEP/BLUR/CUT/NEEDS_MORE_CONTEXT.
