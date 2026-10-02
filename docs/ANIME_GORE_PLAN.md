# Kế hoạch giảm báo nhầm máu me hoạt hình (Conan), không để lọt máu thật

- **Ngày lập:** 02/10/2026.
- **Trạng thái:** đề xuất. **Chưa có luật nào nên bật.** Người dùng cần trả lời câu hỏi 1 (mục 7) trước khi viết code cho bước 3.
- **Mốc mã:** nhánh `improve/scan-performance-metrics`, commit `68a5a7e`. Phiên khác đã commit `177c412` và `68a5a7e` lúc 09:31, khi nghiên cứu này đang chạy. Nghiên cứu không tạo commit nào.
- **Phạm vi:** chỉ nhóm máu me của anime tagger (`wd_vit_tagger_v3`, `src/biliflow/animation_safety_scanner.py`) trên Conan 20 và Conan 21.
  - Golden v1 r469 + v1.1 r115. Verdict máu me trùng scorecard r108.
  - Kế hoạch này không chứng nhận gì cho nhóm 18+, nhóm bạo lực hay phim người đóng.
- **Nguồn:** `docs/ADULT_FALSE_ALARM_PLAN.md` §7 (G0–G3) và toàn bộ file trong `temp/next/anime-gore/`.
  - Phần "nhánh Không" (mục 2.3) là đo bổ sung trên CPU cho kế hoạch này. Script: `plan_whatif.py`, kết quả: `plan-whatif.json`.
  - Golden được chấm lại trong bộ nhớ, không ghi gì vào `annotations/`.

**Chính sách dùng làm sự thật:**
- Máu thật, kể cả máu vẽ trong anime, và vết thương nhìn thấy phải được báo, kể cả khi người dùng giữ cảnh ("có thật · giữ").
- Trận đánh anime không có máu không phải bạo lực máu me.
- Hiệu ứng cách điệu, vật hoặc đèn màu đỏ, cảnh tối không có máu là báo nhầm.

---

## 0. Tóm tắt một trang

**Vấn đề.**
- Golden máu me đang bắt đủ **17/17 nhãn** ở mục chính, must_catch **5/5**.
- Precision mục chính chỉ **16/32 = 0.50**; tính theo thẻ cảnh là **12/26 = 0.46**.
- Cả 16 báo nhầm đều nằm ở Conan, vì anime tagger gây ra.
- Queue Conan có **55 + 50 mục**, gộp thành **40 + 35 thẻ cảnh** máu me. Không mục nào có quyết định của người dùng.

**Ba kết luận đã đo.**
1. **Giữ RGB.** Đổi sang BGR như code upstream làm Golden tệ hơn rõ: precision mục 0.50 → 0.30, chỉ 13/17 nhãn ở mục chính, báo nhầm gần gấp đôi (mục 1.3).
2. **Theo chính sách hiện tại, không luật nào đủ an toàn để bật.** Chính sách này coi vết xước/bầm không máu là "vết thương nhìn thấy".
   - Luật tốt nhất (B1, cảnh tối không có tag injury) chỉ chuyển 3 báo nhầm Golden, với biên 0.067.
   - Luật này chỉ học được từ holdout và không chuyển giao sang dev.
   - Đây đúng là điều kiện dừng của §7: biên gần bằng 0.
3. **Nếu người dùng xác nhận vết xước/bầm không máu *không* phải vết thương cần báo,** thì có đúng một luật đủ điều kiện để đo tiếp. Đó là **C1: "tagger không thấy máu và không thấy xác"**.
   - **Luật:** `blood_family_max ≤ 0.0716 AND corpse_max ≤ 0.0104`, tính trên các khung đã xác nhận.
   - **Số mục bị chuyển:** Conan 20 **55 → 43** mục, **40 → 32** thẻ. Conan 21 **50 → 38** mục, **35 → 28** thẻ.
   - **Golden với nhãn hiện tại:** mục chính 24 (0.667), thẻ 20 (0.60). Vẫn 17/17 nhãn, must_catch 5/5.
   - **Golden sau khi relabel hai nhãn bầm C20F:** precision mục 0.4375 → 0.583, thẻ 0.423 → 0.55.
   - **Máu thật:** không bị chuyển trên Golden, không span lone-shot nào trong 13 span máu thật bị chuyển, không mục nào trong 4 mục máu thật ngoài Golden bị chuyển.
   - **Biên:** 0.17 với tầng bảo vệ của nhánh Không.
   - **Chưa đủ để bật mặc định.** Luật chỉ dựa vào một model; phần chọn ngưỡng không chuyển giao giữa hai split. Vì vậy C1 phải qua thêm bộ kiểm thứ hai (quyết định của người dùng trên queue Conan) và một phim anime thứ ba (mục 4).

**Việc nên làm ngay, ở cả hai nhánh:** bước 1. Scanner ghi thêm bằng chứng tag cho từng interval máu me (`blood_family_max`, `injury_max`, `corpse_max`, số khung đã xác nhận). Thẻ review hiện gợi ý "thấy máu / chỉ vết thương / xác".
- Bước này không chuyển mục nào và không đổi interval.
- Mọi luật về sau đều cần dữ liệu này.

**Bảng xếp hạng phương án** (số liệu đã đo trừ khi ghi "chưa đo"):
- Đếm thẻ theo đúng thứ tự production: `group_safety_review_events` chạy trước, rồi mới phân loại; một thẻ chỉ bị chuyển khi **mọi** thành viên của nó thỏa luật.
- Precision thẻ Golden đo theo cách "chuyển mục rồi gộp lại". Thứ tự production phải được chấm lại ở cổng 4.1.

| Hạng | Phương án | Dùng khi | Mục bớt C20 / C21 (gốc 55 / 50) | Thẻ bớt C20 / C21 (gốc 40 / 35) | Golden mục chính (precision) | Golden thẻ (precision) | Biên khớp (IDR) | Máu thật bị chuyển | Kết luận |
|---|---|---|---|---|---|---|---|---|---|
| 1 | **R0:** giữ RGB, không luật | luôn | 0 / 0 | 0 / 0 | 32 (0.50) | 26 (0.46) | — | 0 | Trạng thái hiện tại |
| 2 | **R1:** ghi bằng chứng tag và hiện gợi ý trên thẻ | cả hai nhánh | 0 / 0 | 0 / 0 | không đổi | không đổi | — | 0 | **Làm ngay** (bước 1–2) |
| 3 | **C1:** không có tag máu và không có tag xác | chỉ nhánh "Không" và sau khi relabel | **−12 / −12** | **−8 / −7** | 24 (0.667; 0.583 sau relabel) | 20 (0.60; 0.55 sau relabel) | 0.17 (nhánh Không); 0.023 nếu chưa relabel | 0 / 0 / 0 | **Ứng viên duy nhất.** Mặc định tắt, bật khi qua cổng mục 4 |
| 4 | **C1b:** bản chặt của C1 (`blood_minus_injury ≤ −0.0471 AND corpse_max ≤ 0.0004`) | nhánh "Không", nếu muốn biên ≥ 0.25 | −3 / −6 | −1 / −4 | 27 (0.519 sau relabel) | 23 (0.478 sau relabel) | 0.25 | 0 / 0 / 0 | Dự phòng nếu C1 trượt cổng 4.6 hoặc 4.7 |
| 5 | **Q1:** Qwen2-VL-2B trả lời "có máu nhìn thấy không?" làm tín hiệu thứ hai | cả hai nhánh | chưa đo | chưa đo; trần lý thuyết là 25 thẻ đỏ/tối/cách điệu còn lại sau C1 (14 / 11) | — | — | — | phải bằng 0 | **Chỉ đo** (bước 5), không bật |
| — | B1: tag "dark" ≥ 0.30 và injury < 0.023 | nhánh "Có" | −6 / −3 | −4 / −3 | 29 (0.552) | 23 (0.522) | 0.067 | 0 | **Không bật.** Biên < 0.1; dev không có báo nhầm nào cho luật này |
| — | B5: tag "night" và không có người | nhánh "Có" | −2 / 0 | −1 / 0 | 31 (0.516) | 25 (0.48)¹ | 0.65 | 0 | **Không bật.** Lợi quá nhỏ |
| — | A4: blood ≈ injury và ≤ 4 khung | — | −6 / −5 | −4 / −4 | 26 (0.615) | 22 (0.545) | 0.13 (tầng A); 0.0 (nhánh Không) | 0 | **Không bật.** Kém C1; chuyển `1146488859a3` (mục lớp xác) |
| — | A1–A3; R1/R3 của lần chạy trước | — | — | — | — | — | 0.008–0.023; R1/R3 = 0 | R1/R3 chuyển lone shot gs-C21E-0006 | **Không bật** |
| — | Đổi tagger sang BGR | — | **+20 / +13** | **+7 / +4** | 43 (0.30) | 26 (0.31) | — | 2 nhãn trượt, 2 nhãn partial | **Loại** |

¹ Ở thứ tự production, thẻ Golden báo nhầm `conan20-card39` (credits trước đài phun nước ánh hồng) *không* được chuyển, vì thành viên `e112d14ccbb8` của thẻ không thỏa luật. Thẻ duy nhất được chuyển là cảnh xác tàu trên mặt nước ban đêm, nằm ngoài Golden.

---

## 1. Số liệu hiện tại

### 1.1. Golden (scorecard `reports/benchmarks/golden-baseline-v1v11-20261001-095204`, chấm lại ở r469/r115)

| | Nhãn ở mục chính | must_catch | Mục chính | Hữu ích | Báo nhầm | Precision | dev | holdout |
|---|---|---|---|---|---|---|---|---|
| Mục | 17/17 | 5/5 | 32 | 16 | 16 | 0.50 | 8/18 | 8/14 |
| Thẻ cảnh | 17/17 | 5/5 | 26 | 12 | 14 | 0.46 | 6/14 | 6/12 |

- **Mục hữu ích:** 15 mục Conan và 1 mục Troy. Trong đó 11 mục mang 12 nhãn "có thật · giữ".
  - Theo nguyên nhân: máu thật 9, xác không máu 5, vết bầm 2.
- **Báo nhầm:** vết xước không máu 8, vật màu đỏ 5, cảnh tối 2, ánh sáng đỏ 1.
- **Thẻ Golden:**
  - thẻ có máu thật: 7, đều hữu ích;
  - thẻ chỉ có nguyên nhân đỏ/tối/cách điệu: 8, đều báo nhầm;
  - thẻ vết thương: 6 báo nhầm, 1 hữu ích;
  - thẻ xác: 3, đều hữu ích.

### 1.2. Queue Conan (105 mục, chưa mục nào có quyết định)

| | Conan 20 | Conan 21 | Tổng |
|---|---|---|---|
| Mục máu me | 55 (532.0 s) | 50 (373.0 s) | 105 |
| Thẻ cảnh máu me | 40 | 35 | 75 |
| Thẻ chỉ đỏ/tối/cách điệu (`fp_like_only`) | 15 | 12 | 27 |
| Thẻ vết thương | 13 | 14 | 27 |
| Thẻ xác | 6 | 5 | 11 |
| Thẻ máu thật | 6 | 4 | 10 |

Nguyên nhân kích hoạt theo mục (G0, đọc khung 640x360 từ nguồn):

| Nguyên nhân | Số mục |
|---|---|
| Vết thương không máu | 42 |
| Cảnh tối | 18 |
| Xác | 14 |
| Vật màu đỏ | 12 |
| Máu thật | 12 |
| Ánh sáng đỏ | 3 |
| Cách điệu | 2 |
| Khác | 2 |

**Mục cần lưu ý:**
- Năm mục "ánh sáng đỏ" thực ra là vết xước trên mặt dưới ánh lửa.
- `cb1c14ac71a7` có thể là một thi thể trôi trên sông (chưa chắc).
- `1146488859a3` bị xếp vào lớp xác, nhưng khung hình giống người ngủ gục trên bàn.

### 1.3. G1: thứ tự kênh màu

- **Production:** đưa ảnh RGB vào model, pad đen.
- **Code upstream** (`wdv3-timm`, HF Space): đưa ảnh BGR vào model, pad trắng.
- **Chạy lại bằng RGB:** khớp production tuyệt đối. Mọi interval gore, adult và violence của hai phim đều trùng, lệch điểm 0.0; cả 105 điểm mục đều trùng.

| | RGB (production) | BGR (upstream) |
|---|---|---|
| Mục: số mục chính / báo nhầm / precision | 32 / 16 / 0.50 | 43 / 30 / 0.30 |
| Mục: nhãn ở mục chính | 17/17 | 13/17 (2 trượt, 2 partial) |
| Thẻ: precision / nhãn ở mục chính | 0.46 / 17/17 | 0.31 / 12/17 |
| Số mục máu me C20 / C21 | 55 / 50 | 75 / 63 |
| Số thẻ máu me C20 / C21 | 40 / 35 | 47 / 39 |
| Số interval adult / violence (hai phim) | 5 / 21 | 1 / 37 |

**Quyết định:** giữ RGB. Chưa thử pad trắng và chưa thử đưa thẳng ảnh 448 px vào model. Hai thử nghiệm này đều là thay đổi detector của cả ba nhóm hoạt hình, nên không nằm trong kế hoạch này.

---

## 2. Chi tiết các phương án

### 2.1. R1: ghi bằng chứng tag và hiện gợi ý trên thẻ (làm ngay, không chuyển mục nào)

- **Bằng chứng ghi thêm.** Scanner ghi thêm cho từng interval máu me:
  - `blood_family_max`: max trên các khung đã xác nhận của hợp xác suất 16 tag `blood*` và `pool_of_blood`;
  - `injury_max`;
  - `corpse_max`;
  - `confirmed_frames`.

  Các giá trị này đã có sẵn trong tensor xác suất của `process_batch`, nên không tốn thêm GPU.
- **Gợi ý trên thẻ.** Thẻ review hiện một dòng gợi ý:
  - "Thấy máu" khi `blood_family_max ≥ 0.5`;
  - "Chỉ thấy vết thương" khi injury lớn hơn mọi tag máu;
  - "Có thể là xác";
  - "Máu yếu".

  Ngưỡng hiển thị chỉ dùng để trình bày, không dùng để phân loại.
- **Lợi ích.** Người duyệt nhận ra ngay thẻ nào đáng xem kỹ. Thêm nữa, các luật ở bước 3 và 5 có dữ liệu production để chạy và để kiểm.
- **Rủi ro.** Không có, *với điều kiện* interval giữ nguyên tuyệt đối (cổng 4.5).

### 2.2. Vì sao chính sách hiện tại (nhánh "Có") không có luật nào

**Hai tầng bảo vệ đã đo:**
- **Tầng A:** bảo vệ mục hữu ích Golden và mọi mục có máu thật.
- **Tầng B:** như tầng A, thêm các mục vết thương không máu và các mục xác. Đây là tầng mà chính sách hiện tại đòi hỏi.

**Cách đo biên:**
- Biên của một mục được bảo vệ (hoặc một span lone-shot máu thật) là quãng mà các ngưỡng phải dịch thêm để luật chuyển được mục đó. Mỗi quãng chia cho IDR của tín hiệu, tức khoảng p10–p90 trên 105 mục. Vì luật chỉ chuyển khi mọi điều kiện cùng thỏa, mục được tính theo quãng lớn nhất trong các điều kiện.
- Biên khớp là giá trị nhỏ nhất trên mọi mục được bảo vệ và mọi span lone-shot máu thật.

**Kết quả ở tầng B:**
- Biên ≥ 0.1 chỉ chuyển được tối đa **1** báo nhầm Golden.
- B1 chuyển được 3 báo nhầm, nhưng biên chỉ 0.067. Khoảng cách thô:
  - tag injury: 0.021, so với `37a573cdc71b` (người nằm trên xà trong đoạn hồi tưởng nhiễu);
  - tag dark: 0.056, so với lone shot gs-C20E-0005 (vết máu dưới credits, must_catch, dark = 0.244).
- Cả ba báo nhầm B1 chuyển được đều thuộc holdout. Dev không có ví dụ nào, nên luật không kiểm chéo được.

**Kiểm chéo hai split, tầng A:**
- Luật chọn trên dev làm rơi gs-C20F-0009/0010.
- Luật chọn trên holdout làm rơi 5 nhãn xác C21E và 2 span lone-shot máu thật.

**Phép thử lone shot:** 3 trong 13 span máu thật (gs-C20E-0005, gs-C21A-0006 và gs-C20F-0006, dài 1–2 khung) không được detector xác nhận nếu đứng riêng. Chúng chỉ được bắt nhờ các khung lân cận cũng có điểm.

**Kết luận:** dừng theo §7 ("chỉ tìm được luật có biên bằng 0"). Không bật B1, B4, B5.

### 2.3. C1: chỉ khi người dùng trả lời "Không"

**Nhánh "Không":** vết xước/bầm không máu *không* phải vết thương cần báo. Khi đó:
- **Tầng bảo vệ C:**
  - mục hữu ích Golden, trừ hai mục bầm C20F (`1872f0ba6046`, `25dc3a115a17`);
  - mọi mục máu thật;
  - mọi mục xác;
  - mọi span lone-shot máu thật.
- **Golden mô phỏng:** gs-C20F-0009/0010 được đổi thành bẫy KEEP (`content_present=False`), đúng như khi người dùng tự relabel bằng app gán nhãn.
- **Đường biên trên tầng C**, với luật không dựa vào seed lân cận:
  - biên ≥ 0.1: tối đa 8 báo nhầm Golden;
  - biên ≥ 0.25: 5;
  - biên ≥ 0.5: 2.

**Luật C1:** `blood_family_max ≤ 0.07158 AND corpse_max ≤ 0.01041`. Cả hai giá trị là max trên các khung đã qua temporal confirmation của mục.
- **Ý nghĩa:** detector kích hoạt chỉ vì tag injury. Tagger không thấy máu (dưới 0.07) và không thấy xác.
- **Nguồn gốc:** dạng luật rút ra từ chính sách; chỉ hai ngưỡng là học từ dữ liệu. Hai ngưỡng được ghi cố định ở đây, trước khi chạy cổng.

**Mục và thẻ bị chuyển:**

| | Conan 20 | Conan 21 |
|---|---|---|
| Mục bị chuyển | 12 (63.5 s / 532 s) | 12 (50.5 s / 373 s) |
| Nguyên nhân | 10 xước, 1 vật đỏ (gói snack), 1 hành lang tối | 11 xước, 1 hành lang sáng |
| Mục còn ở danh sách chính | 43 | 38 |
| Thẻ bị chuyển (thứ tự production) | 8 (7 vết thương, 1 đỏ/tối) | 7 (6 vết thương, 1 đỏ/tối) |
| Thẻ bị chặn vì có thành viên không thỏa luật | 2 | 3 |
| Thẻ còn lại | 32 (14 đỏ/tối, 6 vết thương, 6 xác, 6 máu thật) | 28 (11 đỏ/tối, 8 vết thương, 5 xác, 4 máu thật) |

- Mức ưu tiên của 24 mục bị chuyển: 23 `context`, 1 `high` (`fee07d624ddb`, báo nhầm Golden).

**Golden:**

| | Mục chính | Precision mục | Thẻ chính | Precision thẻ | Nhãn ở mục chính | must_catch |
|---|---|---|---|---|---|---|
| Hiện tại, nhãn r469/r115 | 32 → **24** | 0.50 → **0.667** | 26 → **20** | 0.46 → **0.60** | 17/17 | 5/5 |
| Sau khi relabel C20F-0009/0010 (mô phỏng) | 32 → **24** | 0.4375 → **0.583** | 26 → **20** | 0.423 → **0.55** | 15/15 | 5/5 |

- Báo nhầm Golden bị chuyển: 8 (dev 7, holdout 1).
- Hai mục bầm C20F *không* bị chuyển, vì `blood_family_max` của chúng là 0.21 và 0.088. Sau relabel, chúng vẫn nằm ở danh sách chính dưới dạng báo nhầm.

**Biên:**
- **Tầng C:** 0.17. Mục gần nhất là `1146488859a3`: `corpse_max` 0.030 so với ngưỡng 0.0104.
- **Riêng máu thật:** 0.188. Gần nhất là lone shot gs-C21E-0006 (vết máu khô lớn): `blood_family_max` 0.138, `corpse_max` 0.032.
- **Điều kiện tag máu:** biên 0.86 so với mục máu thật gần nhất chỉ được giữ nhờ điều kiện này (`00de33606c91`, 0.675).
- **Nếu chưa relabel:** biên chỉ còn 0.023, vì mục gs-C20F-0010 (`25dc3a115a17`, `blood_family_max` 0.0877). C1 vẫn qua cổng nhãn (không chuyển mục này), nhưng trượt cổng biên. **Vì vậy relabel là bắt buộc.**
- **Ở tầng B (nhánh "Có"):** C1 chuyển 21 mục vết thương, nên **không hợp lệ**.

**Các điểm yếu, ghi rõ:**
- **Kiểm chéo trên tầng C vẫn không chuyển giao.**
  - Chọn luật trên dev, kiểm trên holdout: luật có biên 0.16 nhưng chuyển 0 báo nhầm holdout.
  - Chọn luật trên holdout, kiểm trên dev: luật chuyển must_catch gs-C20E-0005 (`c1d214468655`), 2 nhãn xác C21E và 2 span lone-shot máu thật. Nguyên nhân là holdout không có báo nhầm kiểu vết xước.

  C1 không phải luật mà fold nào chọn ra. Nó chỉ đứng được vì dạng luật lấy từ chính sách. Do đó **bắt buộc** phải có bộ kiểm 4.6 và 4.7.
- **C1 dựa vào việc tagger thấy ít nhất một vệt máu yếu (≥ 0.07).** gs-C21E-0006 cho thấy tagger có thể chấm máu khô thật chỉ 0.05–0.14 trên từng khung. Một vệt máu như vậy, nếu không có xác bên cạnh, có thể bị chuyển ở phim khác.
- **Phép thử lone shot phụ thuộc ngưỡng cắt cảnh** `SHOT_HIST_CUT = 0.20` và `SHOT_LUMA_CUT = 20`, chọn bằng mắt. Cổng 4.3 thêm phép thử độ nhạy cho hai ngưỡng này.

### 2.4. C1b: bản chặt, biên 0.25

- **Luật:** `blood_minus_injury ≤ −0.0471 AND corpse_max ≤ 0.0004`, tức tag injury vượt mọi tag máu rõ rệt và gần như không có tag xác.
- **Mục bị chuyển:** Conan 20 3 (13.0 s), Conan 21 6 (24.0 s), tất cả là vết xước.
- **Thẻ bị chuyển:** Conan 20 1, Conan 21 4. Có 3 thẻ bị chặn.
- **Golden:** 5 báo nhầm bị chuyển. Sau relabel: mục 27 (0.519), thẻ 23 (0.478).
- **Khi nào dùng:** chỉ khi C1 trượt cổng 4.6 hoặc 4.7 vì một mục có máu yếu.

### 2.5. Q1: đo Qwen2-VL-2B như tín hiệu độc lập (chỉ đo)

- **Lý do.** Sau C1 vẫn còn **25 thẻ đỏ/tối/cách điệu** (14 + 11). Tagger "thấy máu" ở những thẻ này. Không luật nào dùng tag chuyển được chúng với biên dương ở tầng B.
- **Cần gì.** Một nhóm tín hiệu thứ hai độc lập với tagger.
- **Model.** `qwen2_vl_2b_instruct`: Apache-2.0, APPROVED, đã cài, đang dùng để xác nhận bạo lực phim người đóng. Không tải thêm gì.
- **Cách đo** (chỉ dưới `temp/next/anime-gore/q1/`, trong GPU slot):
  - Hỏi một câu chặt, ví dụ "Is there visible blood in this image? Answer yes or no", trên khung mạnh nhất (theo blood family) của:
    - 105 mục;
    - 13 span lone-shot máu thật (khung mạnh nhất của chính shot đó).
  - Ghi xác suất token "yes".
- **Chi phí.** Ở 18+, đo được khoảng 1.16 s/mục trên GPU. Ước tính khoảng 2–3 phút cho cả hai phim, chưa tính nạp model.
- **Điều kiện đạt (ghi trước).**
  - **Bắt buộc:** "yes" trên 12/12 mục máu thật và 13/13 span lone-shot máu thật, gồm gs-C21E-0006 (máu khô), gs-C20E-0005 (credits) và ba shot 1–2 khung.
  - **Hữu ích:** "no" trên ≥ 50% trong 27 thẻ `fp_like_only`.
  - **Dùng thế nào:** nếu đạt, chỉ được dùng làm điều kiện thứ hai trong một luật hai tín hiệu (tag yếu **và** Qwen nói "no"). Luật đó phải đo lại toàn bộ mục 4.
- **Dừng khi:** Qwen nói "no" với bất kỳ máu thật nào ở ngưỡng cần dùng. Khi đó ghi kết quả và không làm tiếp. Ở 18+, Qwen đã trả lời "có" quá dễ, nên khả năng trượt là đáng kể.

---

## 3. Rủi ro với máu thật

| Kiểm tra | C1 | C1b | B1 | R1 |
|---|---|---|---|---|
| Nhãn máu me Golden rời mục chính (mục và thẻ) | 0 | 0 | 0 | 0 |
| must_catch | 5/5 | 5/5 | 5/5 | 5/5 |
| Span lone-shot máu thật bị chuyển (trên 13) | 0 | 0 | 0 | 0 |
| Mục máu thật ngoài Golden bị chuyển (trên 4: `00de33606c91`, `f6d3db73706d`, `cba0be08bcd6`, `8e5c3baf98d9`) | 0 | 0 | 0 | 0 |
| Mục lớp xác bị chuyển | 0 | 0 | 0 | 0 |
| Biên tới máu thật gần nhất | 0.188 (gs-C21E-0006 lone) | ≥ 0.25 | 0.089 theo tag dark (gs-C20E-0005 lone) | — |

Rủi ro còn lại chưa đo được: một phim khác có máu khô hoặc máu nhỏ mà tagger chấm dưới 0.07, đi kèm tag injury. Cổng 4.7 (phim anime thứ ba) có mục đích kiểm đúng trường hợp này.

---

## 4. Các cổng phải qua (ghi trước khi đo, không sửa sau khi đã thấy kết quả)

### 4.1. Golden v1 và v1.1, chấm từng bộ

- **Cổng chung.** Chạy `.\.venv\Scripts\python.exe scripts\evaluate_golden.py compare --baseline <baseline> --candidate <candidate> --gate detector` cho từng bộ v1 và v1.1. Cổng có sẵn ba điều kiện: không giảm recall must_catch, precision không giảm quá 2 điểm, không trúng bẫy mới.
- **Máu me, đo trên cả mục và thẻ:**
  - mọi nhãn `content_present` hoặc BLUR/CUT đều được bắt ở mục chính;
  - `advisory_only = 0`, `partial = 0`, must_catch 5/5.
- **Thẻ phải chấm theo đúng thứ tự production:** gộp thẻ trước, phân loại sau; thẻ chỉ bị chuyển khi mọi thành viên thỏa luật. Không dùng cách "chuyển mục rồi gộp lại" của nghiên cứu.
- **Precision phải đạt đúng số đã đo trước** (sai lệch tối đa ±1 mục):
  - C1, nhãn hiện tại: mục ≥ 16/24, thẻ ≥ 12/20;
  - C1, sau relabel: mục ≥ 14/24, thẻ ≥ 11/20. Số chính xác lấy theo revision nhãn mới của người dùng, và phải chấm lại baseline với revision đó trước.
- **Các nhóm còn lại** (18+, bạo lực, quảng cáo, logo/chữ) giữ **nguyên danh sách mục** trong cả `items` và `advisory_items`, so theo id và khoảng thời gian.

### 4.2. Nhãn và quyết định của người dùng

- Không mục nào có `decision` bị chuyển.
- Mục giữ lại từ queue cũ phải được phân loại lại với cùng luật.
- Mọi `source_candidate_refs` vẫn có mặt ở `items` hoặc `advisory_items` (kiểm coverage). Id không trùng.

### 4.3. Lone shot và độ nhạy cắt cảnh

- Chạy lại phép thử lone shot trên đặc trưng **production** (bước 2).
- Phép thử phải cho 0 span máu thật bị chuyển ở cả ba mức `SHOT_HIST_CUT` 0.15 / 0.20 / 0.30. `SHOT_LUMA_CUT` giữ 20.
- Phép thử phải chạy cho cả 13 span máu thật và 7 span "có thật" không máu, gồm xác và (nếu chưa relabel) vết bầm.

### 4.4. Đặc trưng production khớp với lúc đo

- `blood_family_max` và `corpse_max` mà scanner ghi cho 105 mục phải lệch không quá **0.001** so với `items-ff.csv`.
- Lượt chạy lại G1 bằng RGB đã khớp production với độ lệch 0.0, nên mức sai lệch này chỉ để chừa cho fp16.
- Nếu trượt: dừng và tìm nguyên nhân, **không** chỉnh ngưỡng cho khớp.

### 4.5. Scanner không đổi interval

- Quét lại Conan 20 và Conan 21 bằng code mới, ghi vào `reports/benchmarks/gore-triage-<ts>/`.
- Đếm interval gore 60/60 và 52/52, adult 3/3 và 2/2, violence 9/9 và 12/12. `start/end/max_score/sample_count/predicted_label/reason` trùng tuyệt đối với report của job.
- Hai lượt quét chạy qua `scripts\run.ps1` trong GPU slot, khoảng 384 s và 390 s.
- Thời gian stage animation không tăng quá 1%.

### 4.6. Bộ kiểm thứ hai: quyết định của người dùng trên Conan 20/21

- **Phạm vi tối thiểu:** 24 mục C1 sẽ chuyển và 15 thẻ chứa chúng.
- **Nên có:** cả 75 thẻ.
- **Yêu cầu:**
  - không mục nào người dùng chọn BLUR/CUT hoặc đánh dấu "có thật · giữ" nằm trong nhóm bị chuyển;
  - số báo nhầm bị chuyển bằng số dự kiến ±1.

### 4.7. Phim anime thứ ba (bắt buộc trước khi bật mặc định)

- **Phim:** người dùng cung cấp trong `input\`, không tải từ mạng. Phim cần có ít nhất 3 cảnh máu thật, trong đó ít nhất 1 cảnh máu khô hoặc máu nhỏ, và có cảnh vết xước không máu.
- **Yêu cầu:**
  - không cảnh máu thật nào bị chuyển;
  - không cảnh máu thật nào có `blood_family_max < 0.15`, tức gần ngưỡng 0.0716 trong vòng khoảng 0.1 IDR.
- **Nếu trượt:** C1 giữ ở mức "chỉ đo" hoặc lùi về C1b.

### 4.8. Không làm hỏng phần còn lại

- Chạy test tập trung cho các file đã sửa, sau đó chạy đủ bộ test: `.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v`.
- Không đổi model, manifest hay dependency, nên không cần `license-audit`. Nếu Q1 sau này vào production thì phải chạy `.\scripts\run.ps1 license-audit` và cập nhật `docs/LICENSE_POLICY.md`.
- Ngưỡng detector (`gore_high_threshold`, `gore_low_threshold`, context, co-occurrence, temporal) và cách export giữ nguyên.

---

## 5. Điều kiện dừng (gặp một điều kiện là dừng, hoàn nguyên luật, ghi lại bằng chứng)

1. Có nhãn máu me Golden nào rời mục chính, dù là mục hay thẻ, hoặc must_catch giảm.
2. Có span lone-shot máu thật hoặc mục máu thật ngoài Golden nào bị chuyển, ở bất kỳ mức cắt cảnh nào của cổng 4.3.
3. Đặc trưng production lệch quá 0.001 (cổng 4.4), hoặc scanner đổi bất kỳ interval nào (cổng 4.5).
4. Biên khớp của C1, đo lại với revision nhãn mới của người dùng, xuống dưới **0.1**.
5. Người dùng đánh dấu BLUR/CUT hoặc "có thật" cho một mục trong nhóm bị chuyển (cổng 4.6).
6. Phim thứ ba có máu thật bị chuyển, hoặc máu thật nằm sát biên (cổng 4.7).
7. Danh sách mục của nhóm khác thay đổi.
8. **Người dùng trả lời "Có"** (vết xước/bầm không máu là vết thương cần báo). Khi đó C1 và C1b bị loại. Chỉ còn R1 và phép đo Q1; dừng luật máu me cho tới khi có tín hiệu độc lập qua được mục 2.5.
9. Q1 nói "no" với bất kỳ máu thật nào. Khi đó dừng Q1.

---

## 6. Các bước triển khai (theo file)

**Bước 0. Chốt nhãn (người dùng làm).**
- Người dùng trả lời câu hỏi 1–3 ở mục 7.
- Nếu trả lời "Không", người dùng tự sửa gs-C20F-0009/0010 thành KEEP không có "có thật" bằng app gán nhãn (`Golden-Label-v1.1.cmd`). Agent không sửa `annotations/`.
- Chấm lại baseline với revision mới, rồi mới đo luật.

**Bước 1. Ghi bằng chứng tag (R1; cả hai nhánh; chỉ thêm trường, không đổi interval).**
- `src/biliflow/animation_policy.py`: thêm `GORE_BLOOD_FAMILY_LABELS` (16 tag `blood*` và `pool_of_blood`). Đây là chỗ duy nhất định nghĩa nhóm tag này.
- `src/biliflow/animation_safety_scanner.py`:
  - Trong `process_batch`, tính `_union(probabilities, blood_family_indices)`, tag `injury` và tag `corpse` cho từng khung.
  - Gắn ba giá trị vào hit gore dưới khóa `tag_evidence`.
  - Sau `group_hits`, gắn cho mỗi interval gore khóa `gore_tag_evidence = {blood_family_max, injury_max, corpse_max, confirmed_frames}`. Giá trị là max trên các hit đã xác nhận nằm trong interval.
  - Payload ghi thêm `gore_tag_evidence_policy = {version: 1, labels, aggregation: "max over temporally confirmed frames"}`.
  - **Không** đổi ngưỡng, không đổi lấy mẫu, không đổi `group_hits`.
- `src/biliflow/review_workflow.py`:
  - `_scan_items` mang `gore_tag_evidence` lên mục; khi gộp mục và gộp thẻ thì lấy max.
  - UI thẻ máu me hiện một dòng gợi ý (mục 2.1).
- Tests:
  - `tests/test_animation_policy.py`: nội dung nhóm tag.
  - Test scanner bằng xác suất giả: interval giữ nguyên start/end/max_score/sample_count khi thêm bằng chứng; bằng chứng bằng max trên đúng các hit đã xác nhận.
  - `tests/test_review_workflow.py`: bằng chứng đi qua bước gộp mục và gộp thẻ.

**Bước 2. Benchmark tái lập (GPU, trong slot).**
- `scripts/benchmark_gore_triage.py` (mới), theo khuôn `scripts/benchmark_adult_triage.py`:
  1. Quét lại animation-safety của Conan 20/21 bằng code mới, qua `scripts\run.ps1` để chờ GPU slot, vào `reports/benchmarks/gore-triage-<ts>/`. Kiểm cổng 4.5.
  2. So `gore_tag_evidence` với `temp/next/anime-gore/items-ff.csv` (cổng 4.4). Chép `items-ff.csv` và `lone-shots.csv` vào thư mục bằng chứng.
  3. Dựng lại queue Conan 20/21 ở các mức `off`, `C1b` và `C1` từ report mới, với `content_style="animation"`. Chấm Golden từng bộ, chạy `compare --gate detector` (cổng 4.1, 4.2).
  4. Chạy phép thử lone shot ở ba mức cắt cảnh (cổng 4.3).
  5. Ghi số mục, thẻ và giây bị chuyển cho từng phim, rồi so với bảng 2.3 và 2.4.
  6. Nếu queue có quyết định của người dùng thì kiểm cổng 4.6.
- Script chỉ đọc từ `reports/jobs`, `input/`, `annotations/`, `state/` và kiểm checksum trước và sau khi chạy. Ghi `summary.json` và `summary.md`; thoát mã 1 khi trượt cổng.

**Bước 3. Luật C1 (chỉ nhánh "Không", mặc định tắt).**
- `src/biliflow/gore_triage.py` (mới):
  - `GORE_TRIAGE_LEVELS = {"off": None, "strict": C1b, "no_blood_no_corpse": C1}`;
  - `GORE_TRIAGE_LEVEL = "off"`;
  - đường dẫn bằng chứng (file này, `plan-whatif.json`);
  - các ngưỡng scanner đã hiệu chỉnh (fps, high/low/context threshold) để từ chối report khác cấu hình.
- `src/biliflow/review_workflow.py`: thêm `triage_anime_gore_items(items, scan_payloads, job_content_style, level)` theo đúng khuôn `triage_adult_items`. Hàm trả về `(required, advisory, audit)` và được gọi **ngay sau** `triage_adult_items` trong `build_review_queue`, tức sau `group_safety_review_events`.
  - **Chỉ áp cho mục thỏa đủ các điều kiện:**
    - `category == "gore"`, `decision is None`;
    - job là `animation`;
    - mọi `source_candidate_refs` trỏ tới report `scan_type == "gore"`, `content_style == "animation"`, có `gore_tag_evidence` và đúng cấu hình đã hiệu chỉnh.
  - **Khi thiếu bằng chứng hoặc sai cấu hình:** không chuyển (fail-safe).
  - **Với thẻ cảnh:** chỉ chuyển khi mọi interval thành viên thỏa luật.
  - **Mục bị chuyển nhận các trường:**
    - `advisory=True`, `priority="context"`;
    - `suggested_decision=None`, để không ngụ ý mục đã được chứng minh an toàn;
    - `gore_triage={rule, blood_family_max, corpse_max, level, evidence}`;
    - lý do tiếng Việt: "Tagger không thấy máu và không thấy xác; chỉ có tag vết thương".
  - **Queue:** ghi khối `gore_triage` ở cấp gốc để kiểm toán (mức, số mục và số giây bị chuyển, lý do giữ lại).
- `build_review_queue(...)` nhận thêm `gore_triage_level`. `src/biliflow/cli.py`: lệnh `build-review` thêm `--gore-triage-level` để đo. `src/biliflow/job_pipeline.py` đã truyền `content_style`; chỉ cần kiểm lại.
- `tests/test_review_workflow.py`, các ca cần có:
  - chỉ áp cho animation và nhóm gore;
  - mục có quyết định được giữ nguyên;
  - mục giữ lại từ queue cũ được phân loại lại;
  - thẻ có một thành viên không thỏa luật thì không chuyển;
  - thiếu bằng chứng thì không chuyển;
  - coverage đủ, id không trùng.
- **Bật mặc định** (`GORE_TRIAGE_LEVEL = "no_blood_no_corpse"`) chỉ khi đã qua 4.1–4.7 và người dùng đồng ý (câu hỏi 5).

**Bước 4. Không làm.**
- Không đổi sang BGR.
- Không đổi ngưỡng gore.
- Không dùng đầu NSFL của `image_safety_classifier_m` cho hoạt hình, vì kết quả đã kém ở task C.
- Không dùng luật dựa trên seed lân cận (R1/R3 cũ trượt lone shot).

**Bước 5. Q1 (chỉ đo, độc lập với bước 3).**
- Script dưới `temp/next/anime-gore/q1/`, dùng lại loader của `src/biliflow/vlm_confirmation.py` (import, không sửa), chạy trong GPU slot.
- Ghi kết quả theo điều kiện ở mục 2.5. Chỉ khi đạt mới viết kế hoạch luật hai tín hiệu riêng.

**Bước 6. Tài liệu.**
- Sau mỗi mốc đã kiểm, cập nhật `CHANGELOG.md`, `docs/PROJECT_STATUS.md` và `docs/SESSION_HANDOFF.md`.
- Ghi chính xác job/revision, phạm vi detector (chỉ nhóm máu me của anime tagger) và revision nhãn.
- `docs/ADULT_FALSE_ALARM_PLAN.md` §7: thêm một dòng kết quả G0–G2 và dẫn tới file này.

---

## 7. Câu hỏi cho người dùng

1. **Vết xước hoặc vết bầm *không có máu* có phải "vết thương nhìn thấy" cần báo không?**
   - Ví dụ: vết xước trên mặt Curaçao (C20C), vết xước của Conan/Heiji dưới ánh lửa (C21B), Amuro bầm tím sau trận đánh (C20F).
   - Hiện Golden đang ghi hai cách ngược nhau: C20F ghi "có thật · giữ" (gs-C20F-0009/0010), còn C20C, C21B và C21C ghi báo nhầm.
   - **"Có"** → không bật luật máu me nào; chỉ làm bước 1–2 và đo Q1.
   - **"Không"** → làm bước 3 với C1, bớt khoảng 8 + 7 thẻ.
2. **Nếu trả lời "Không":** bạn tự sửa gs-C20F-0009/0010 thành KEEP không có "có thật" bằng app gán nhãn được không? Nếu chưa relabel, biên của C1 chỉ còn 0.023 và trượt cổng.
3. **Hai mục chặn biên:**
   - `1146488859a3` (Conan 20, 20:45–20:48.5): người gục trên bàn văn phòng. Đây là xác hay người ngủ?
   - `cb1c14ac71a7` (Conan 20, 45:08–45:11): bóng tối trôi trên sông lúc hoàng hôn. Có phải thi thể không?

   Cả hai hiện được bảo vệ như xác.
4. **Bạn có thể duyệt thẻ máu me của Conan 20/21 làm bộ kiểm thứ hai không?** Tối thiểu là 15 thẻ C1 sẽ chuyển; tốt nhất là cả 75 thẻ.
5. **Bạn có phim anime thứ ba trong `input\` không?** Phim cần có máu thật (ít nhất một cảnh máu khô hoặc máu nhỏ) và cảnh vết xước không máu. Không tải từ mạng. Đây là điều kiện bắt buộc trước khi bật C1 mặc định.
6. **Bạn có đồng ý cho đo Qwen2-VL-2B** (đã cài, Apache-2.0, APPROVED) làm bộ kiểm "có máu nhìn thấy không?" không? Lượt này chỉ đo, không ảnh hưởng queue.

---

## 8. Bằng chứng và cách tái lập (`temp/next/anime-gore/`)

- **Tổng hợp:** `summary.json` (G0/G1/G2).
- **G0:**
  - `g0-items.csv`: 106 mục, nguyên nhân, ghi chú khung;
  - `gore-items-final.csv`;
  - `gore-cards.csv`: 75 thẻ, lớp thẻ, luật nào chuyển thẻ;
  - `check/`: ảnh kiểm khung 640x360;
  - `sheets/`, `zoom/`.
- **G1:**
  - `g1-fullfilm.json`, `g1-items.csv`;
  - `scores/fullfilm/conan20.npz`, `conan21.npz`: điểm toàn phim RGB và BGR, đủ vector tag;
  - `g1_fullfilm.py`, `g1_analyze.py`, `gpu_slot.ps1`.
- **G2:**
  - `items-ff.csv` (đặc trưng 105 mục), `lone-shots.csv` (20 span: 13 máu thật, 7 có thật không máu);
  - `g2-rules-ff.csv`, `g2-picked-ff.json`, `g2-moved-ff.csv`, `g2-lone-shot-ff.csv`, `g2-folds-ff.csv`;
  - script `g2_features.py`, `g2_rules.py`, `g2_pick.py`, `ff_common.py`.
- **Phần đo cho kế hoạch này (CPU, chỉ đọc):**
  - `plan_whatif.py` và `plan-whatif.json`: tầng C, Golden mô phỏng relabel, đường biên, luật C1/C1b, kiểm chéo tầng C;
  - `plan_whatif.out`: log.
- **Đã thay thế, chỉ giữ để tham khảo:** `summary-prev-20261001.json`, `items.csv`, `g2-rules.csv`, `g2-picked.json`, `g2-moved-items.csv`, `g1-segments.json`, `g0-items-prev.csv`.
- **Tái lập (CPU):** `PYTHONPATH=E:\DungChung\BiliFlow\src`, `PYTHONIOENCODING=utf-8`, chạy `.venv\Scripts\python.exe temp\next\anime-gore\plan_whatif.py` (khoảng 30 s). Lệnh này đọc `items-ff.csv`, `lone-shots.csv`, queue cơ sở và nhãn Golden; không ghi ra ngoài thư mục này.
- **Bất biến giữ nguyên:**
  - không sửa video nguồn, `reports/jobs`, `state`, `annotations` hay brand memory;
  - detector chỉ tạo ứng viên; mục bị chuyển vẫn nằm ở "Xem tất cả ứng viên" với đủ interval và tham chiếu nguồn;
  - người dùng quyết định KEEP/BLUR/CUT/NEEDS_MORE_CONTEXT;
  - mọi model chạy local và có giấy phép thương mại.
