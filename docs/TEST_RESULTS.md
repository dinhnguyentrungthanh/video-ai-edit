# Test results

## Final render video thực tế V7 — 2026-09-24

- Phân tích regression xác nhận V6 làm mất các biên CUT thủ công từ V4 khi tạo queue mới: khoảng `5–6s` làm sót intro và ba khoảng hở cuối làm sót logo. V7 lưu biên phát hiện gốc cùng biên người dùng chỉnh để audit.
- CUT thực tế sau hợp nhất: `0–10,5s`, `2875–2880s`, `3569,5–3722,175s`; output dự kiến 3.554,000 giây.
- Blur dùng vùng `0,48,1280,72`, sigma 28, feather 3. Mặt nạ `vertical_only` phủ alpha đầy đủ ở mép trái/phải và chỉ feather theo trục dọc.
- Output: `output/Tieng-Yeu-Nay-Anh-Dich-Duoc-Khong-Tap-1-reviewed-v7.mp4`, manifest `COMPLETED`, 629.702.558 byte, 3.554,012 giây, 1 video stream và 1 audio stream.
- Encode CPU hoàn tất trong 434,590 giây, tương đương 8,178 giây video/giây xử lý. Toàn file giải mã không lỗi; checksum nguồn trước/sau giống nhau; giới hạn 3,5 GB đạt.
- 68/68 unit test pass và `pip check` không có dependency hỏng.

## Final render video thực tế V6 — 2026-09-24

- Queue brand/logo V6 đã được người dùng giải quyết đủ 70/70 mục: 59 `KEEP`, 10 `CUT`, 1 `BLUR`, 0 pending.
- Blur không dùng chiều cao cố định. Hợp OCR của banner này tạo vùng `0,48,1280,80`; feather thích ứng là 3 px. Preview V4 xác nhận chữ không đọc được, không lộ viền ở đầu/giữa/cuối và màu phim được giữ nguyên.
- Phát hiện và sửa regression mất màu do nhánh mask xám; nhánh overlay được khóa `yuv420p` và có unit test kiểm tra filter graph.
- Output: `output/Tieng-Yeu-Nay-Anh-Dich-Duoc-Khong-Tap-1-reviewed-v6.mp4`, manifest `COMPLETED`, 631.425.474 byte, 3.573,031 giây so với 3.573,000 giây dự kiến, 1 video stream và 1 audio stream.
- Encode CPU hoàn tất trong 472,302 giây, tương đương 7,565 giây video/giây xử lý. Toàn file giải mã không lỗi; checksum nguồn trước/sau giống nhau; giới hạn 3,5 GB đạt.
- 67/67 unit test pass; `pip check` không có dependency hỏng; license audit có 8 model được phép và 0 model bị chặn.

## Brand/logo exhaustive và localization — 2026-09-23

- Quét video thực tế dài 3.722,175 giây ở chế độ exhaustive: 1.980 frame, đủ 745/745 cửa sổ 5 giây, không có cửa sổ bị bỏ do giới hạn candidate.
- Qwen2-VL-2B giữ 100 cửa sổ `CONFIRMED` và 34 `UNCERTAIN`; 611 cửa sổ `REJECTED` vẫn được ghi trong JSON để audit. Cả 745 cửa sổ có ảnh audit, tổng 42.111.328 byte, và trang HTML có bộ lọc trạng thái. Thời gian scan AI 326,592 giây, tốc độ 11,397× thời gian thực.
- Florence-2-base khoanh được ít nhất một vùng ở 130/134 mục giữ lại trong 397,663 giây. Benchmark riêng 7 ảnh bắt 4/4 mẫu dương, gồm logo N; phrase grounding cũng báo vùng ở 3/3 ảnh âm nên Florence chỉ được dùng để xác định tọa độ, không quyết định có sửa hay không.
- Phi-3.5 Vision ONNX INT4 thử trên 9 ảnh đạt recall 100% nhưng specificity 0% và trung bình 25,977 giây/ảnh. Model/runtime thử nghiệm đã bị xóa sau khi giữ báo cáo.
- V6 tăng mật độ lên 2 frame/giây trên toàn timeline: 7.563 frame, đủ 745 cửa sổ, 0 cửa sổ bị bỏ; hoàn thành trong 395,023 giây, tốc độ 9,423× thời gian thực. Prompt chặt giữ 10 `CONFIRMED`, 17 `UNCERTAIN`, loại 718 nhưng vẫn lưu ảnh audit cho tất cả; ảnh audit V6 chiếm 8.719.428 byte.
- Florence xử lý 27/27 mục V6 trong 100,615 giây và đều có ít nhất một vùng. Queue cuối lấy hợp OCR + visual V5 + visual V6 còn 70 mục chưa duyệt: 56 visual logo và 14 text; 69 mục có vùng gợi ý. Queue chưa tạo edit plan, preview hoặc output.
- Qwen2-VL-2B còn nhầm cả hai chiều giữa branding ngoài phim và bảng hiệu/tiêu đề nằm trong phim. Vì vậy mục tiêu hiện tại là báo cáo recall-first có con người duyệt; chưa có bằng chứng để công bố độ chính xác production hoặc cho phép auto-edit.
- Toàn bộ 63/63 unit test pass; `pip check` không có dependency hỏng; license audit có 8 model được phép và 0 model bị chặn trong thư mục model hoạt động.

## Final render video thực tế V4 — 2026-09-23

- Output: `output/Tieng-Yeu-Nay-Anh-Dich-Duoc-Khong-Tap-1-reviewed-v4.mp4`.
- Trạng thái manifest: `COMPLETED`; 630.135.013 byte, dài 3.559,017 giây so với 3.559,000 giây dự kiến, 1 video stream và 1 audio stream.
- Giới hạn 3,5 GB: đạt. Checksum nguồn trước/sau dựng giống nhau; nguồn không bị sửa.
- Thời gian encode 540,352 giây, khoảng 6,586× thời gian thực. Bản V4 đã hoàn thành trước khi queue brand/logo V6 được tạo; queue V6 không làm thay đổi file output này.

## Phase 1A technical smoke test — 2026-09-20

- Python: 3.11.16, project-local.
- PyTorch: 2.14.0+cu126.
- CUDA available: true.
- GPU: NVIDIA GeForce RTX 2060, 6 GiB VRAM.
- FFmpeg/FFprobe: 9.0.1 essentials portable.
- Model: `Falconsai/nsfw_image_detection` revision `96cb0d0342c7afb80cab76ecc58b265fa44da256`.
- Model local inference: passed; synthetic image peak CUDA allocation 448.8 MiB.
- Unit tests: 2 passed.

Video tổng hợp 10 giây, 640×360, 30 fps, có AAC audio:

- Quét: 1 frame/giây, batch 4, CUDA.
- Frame đã quét: 10/10.
- Thời gian inference pipeline: 0.506 giây.
- Tỷ lệ kỹ thuật trên mẫu tổng hợp: 19.748 giây video/giây xử lý.
- Peak process RAM: 1,031,319,552 bytes.
- Peak CUDA allocation: 383,784,448 bytes.
- JSON report: passed.
- HTML report: passed.
- Thumbnail và interval grouping với ngưỡng ép: passed.
- Final smoke test sau khi chốt code: 3/3 frame, status `COMPLETED`, runtime metadata đúng và video test tạm đã được xóa.
- Footprint sau khi dọn cache cài đặt: khoảng 4.874 GiB.
- Cache cài đặt có thể tải lại đã dọn: khoảng 4.1 GiB.
- Thư mục `temp` sạch tại thời điểm bàn giao.

Các số trên chỉ là smoke test kỹ thuật, không phải benchmark hiệu năng hoặc độ chính xác trên video thực tế.

## HEVC archive probe

- CPU `libx265`: passed trên video tổng hợp 3 giây, đầu ra HEVC 640×360, 90 frame.
- `hevc_nvenc`: blocked. FFmpeg build yêu cầu NVENC API 13.1; driver 576.80 cung cấp API 13.0 và báo cần driver 610.00 trở lên.
- Không cập nhật driver trong Phase 1A. Phase 3 sẽ benchmark CPU x265 và chỉ xem xét NVENC sau khi có phương án driver/build được duyệt.

## Pending

- Benchmark video mục tiêu thực tế của người dùng.
- Precision, recall, false positive và false negative.
- Tốc độ theo độ phân giải và nội dung thật.
- Kiểm tra dừng scanner giữa một video dài.

## OCR/ad-review pipeline — 2026-09-20

- EasyOCR 1.7.2 và các thư viện xử lý ảnh đã cài trong `.venv` trên ổ E.
- Pipeline lấy mẫu video, nhận bounding box OCR, nối vùng qua thời gian và phân loại vị trí đã hoàn thành.
- Báo cáo có giới hạn mặc định 250 track để tránh tăng dữ liệu không giới hạn.
- Unit tests: 5/5 passed, gồm phân loại vùng phụ đề và ưu tiên chữ cố định ở góc.
- Model EasyOCR Việt/Anh đã tải vào ổ E: 98.558.471 byte, khoảng 94 MiB (không tính `.gitkeep` và manifest).
- Test 5 phút đầu video Conan: 100 frame ở chu kỳ 3 giây, hoàn thành trong 22,254 giây, đạt 13,481× thời gian thực, peak RAM 1.130.397.696 byte.
- Phát hiện đúng banner cờ bạc `i999.ai` khoảng 00:03:06–00:03:30, gồm nội dung tiếng Việt về game bài, bắn cá và rút tiền.
- Tạo preview blur V5 30 giây từ 00:03:03–00:03:33: H.264 1920×1038, có âm thanh, 14.993.670 byte; encode trong khoảng 7,5 giây.
- V5 dùng blur mạnh trên crop và alpha feather 12 px quanh bốn cạnh. Kiểm tra trực quan: quảng cáo không còn đọc được, màu phim giữ nguyên, mép blur chuyển tiếp mềm và tiêu đề chính không bị che.
- Kiểm tra 1 frame/giây xác nhận quảng cáo xuất hiện khoảng 00:03:06–00:03:31. V5 chỉ bật blur trong khoảng này, tránh làm mờ sớm hoặc kéo dài sau quảng cáo.
- Sau khi thay thế, V5 và toàn bộ preview thử/trung gian đã được xóa để tránh phình dữ liệu.
- Review thực tế phát hiện V5 ngắt blur sớm tại frame khoảng 00:28 của preview trong khi banner vẫn còn.
- V6 giữ blur từ 00:03:06 đến hết preview 00:03:33. Frame 00:28 đã kiểm tra trực quan và không còn đọc được quảng cáo.
- Chỉ giữ video V6 (14.962.726 byte) và ảnh xác nhận frame 00:28.
- Quy tắc cộng đệm cố định được thay bằng xác nhận look-ahead: quét dày 0,5 giây/frame, chờ ít nhất 3 giây liên tục không còn banner, rồi đặt điểm tắt blur về 0,5 giây sau frame vắng banner đầu tiên. Cách này tránh cả lộ quảng cáo lẫn blur dư cảnh sạch kéo dài.
- Quét refine 50 giây ở 0,5 giây/frame: 100 frame, hoàn thành trong 30,082 giây; OCR thấy banner đến khoảng 00:03:31,0 và không thấy lại trong phần còn lại của cửa sổ quét đến 00:03:50.
- V8 dài 35 giây, blur đến khoảng 00:03:32. Kiểm tra theo số frame: mốc 0:28 của preview vẫn blur; mốc 0:29,5 đã sạch và không còn blur.
- V6/V7 và ảnh trung gian đã xóa; chỉ giữ V8 (16.651.720 byte) và ảnh so sánh endpoint.
- Chế độ tự động blur vẫn tắt; báo cáo bắt buộc review trước.

## Real-file baseline — Tears of Steel 720p — 2026-09-20

Nguồn thử là phim mở Tears of Steel tải từ máy chủ Blender. Đây là benchmark kỹ thuật trên một video thực, chưa phải bộ ground truth cho NSFW.

- File: `tears_of_steel_720p.mov`.
- SHA-256: `efa9062d9cdb7a338e40ad530dfdf234806743f29ae6a1a136b97ece4e588e8f`.
- Dung lượng: 372,178,639 bytes.
- Video: H.264, 1280×534, 24 fps.
- Audio: MP3, 44.1 kHz, stereo.
- Thời lượng: 734.166667 giây.
- Sampling: 1 frame/giây; batch size 8; threshold 0.70.
- Frame đã quét: 734.
- Thời gian scan nội bộ: 12.270 giây.
- Tốc độ: 59.836× thời gian thực.
- Peak process RAM: 960,962,560 bytes, khoảng 916.4 MiB.
- Peak CUDA allocation: 414,660,096 bytes, khoảng 395.5 MiB.
- Interval bị đánh dấu: 2.
- `00:00:09–00:00:11`, max score 0.999020: false positive do cấu trúc công nghiệp và ánh sáng hồng/đỏ.
- `00:00:12–00:00:14`, max score 0.854660: false positive do ánh sáng công nghiệp màu hồng/cam.
- Tỷ lệ cảnh báo trên mẫu này: khoảng 9.8 interval/giờ video.

Chưa tính precision/recall chính thức vì video chưa có annotation đầy đủ và không chứa bộ cảnh NSFW dương tính đã xác nhận. Không tăng threshold chỉ dựa trên mẫu âm tính này, vì có thể làm tăng false negative trên dữ liệu thật.

Ước lượng từ lần đo này cho video 45 phút: khoảng 45 giây scan nội bộ; tổng thời gian thực dụng dự kiến khoảng 1–2 phút khi tính khởi động model, đọc file và checksum. Đây là ngoại suy từ mẫu 720p, cần xác nhận lại trên tập phim thực tế 1080p.

## Full-film Conan benchmark — 2026-09-20

Nguồn: video Conan 1920×1038, 5.965,299 giây, SHA-256 `b6a896aca3796b325b279a65c6edf44110a48a4cda8be4b9b89b1c41c56ed6b0`.

### OCR tiếng Việt

- 1.988 frame ở chu kỳ 3 giây; 307,775 giây; 19,382× thời gian thực; peak RAM 1.139.302.400 byte.
- 213 track được giữ để review. Chỉ nhóm 00:03:06–00:03:30 chứa quảng cáo tiếng Việt thật (`i999.ai`, game bài, bắn cá, rút tiền).
- Track ưu tiên cao tại 01:30:57 là chữ trên cửa kính trong cảnh phim, không phải quảng cáo.
- Refine 0,5 giây xác nhận điểm tắt blur 00:03:32 theo look-ahead 3 giây và tail 0,5 giây.

### NSFW

- 5.964 frame ở 1 fps; 125,121 giây; 47,676× thời gian thực.
- Peak RAM 1.018.720.256 byte; peak CUDA 414.660.096 byte.
- 23 interval vượt ngưỡng 0,70. Kiểm tra toàn bộ frame mạnh nhất không thấy nội dung 18+ thật; cảnh báo chủ yếu do da, tay, mặt và góc cận trong anime.
- Hai cụm 00:44:21–00:44:43 và 01:01:35–01:01:40 sau đó được người dùng xác nhận đều là false positive, không phải cảnh máu me hoặc 18+.

### Blood/gore baseline — rejected

- Model: `OwenElliott/image-safety-classifier-m`, revision `a5ce9eec1ac11773ca9ff44f45b1bb6591631562`.
- 5.964 frame ở 1 fps; 121,076 giây; 49,269× thời gian thực; peak RAM 1.269.391.360 byte; peak CUDA 286.056.960 byte.
- Điểm NSFL cao nhất 0,301 thuộc title Conan màu đỏ. Cảnh máu thật tại 01:01:39 chỉ đạt 0,036759 và bị phân loại SFW.
- Kết luận: false negative rõ ràng trên anime; không dùng cho auto-edit.

### Violence baseline — rejected

- Model: `jaranohaal/vit-base-violence-detection`, revision `31931091dfd4ea08a30c42be0db8e1488263cbd5`.
- Đã nạp theo đúng timm ViT từ notebook huấn luyện; toàn bộ key trọng số khớp. Cách nạp Transformers trực tiếp không tương thích checkpoint này và đã bị loại bỏ.
- 5.964 frame ở 1 fps; 117,625 giây; 50,714× thời gian thực; peak RAM 1.312.350.208 byte; peak CUDA 478.205.440 byte.
- Ngưỡng 0,50 tạo 169 interval, 331 frame và 514 giây cần review. Nhiều điểm cao nhất là cảnh nói chuyện, xe cộ, ánh đèn hoặc chuyển động bình thường.
- Cảnh máu thật tại 01:01:39 chỉ đạt 0,063632 và bị phân loại non-violence.
- Kết luận: precision thấp và có false negative quan trọng trên anime; không dùng cho auto-edit.

Hai model bị loại đã được xóa khỏi `models` sau benchmark, thu hồi khoảng 388 MB. Hai clip review false positive cũng đã xóa. Báo cáo JSON/HTML, ảnh candidate, revision và checksum được giữ để tránh thử lại vô ích. Không full export; giai đoạn hiện tại chỉ tạo kết quả scan và benchmark.
