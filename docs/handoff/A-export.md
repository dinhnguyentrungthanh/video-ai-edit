# Bàn giao A – Xuất video nhanh hơn
- Nhánh: `perf/export-speed` (A0–A2, tách từ `origin/main` `7d8b8bc`, giữ nguyên ở `05653c9`), `perf/export-speed-p2` (phase 2, tách từ `05653c9`, kết thúc ở `9f0581a`, đã gộp vào `merge/g1`) và `perf/export-a3` (A3, tách từ `9f0581a`; worktree `temp\wt-export-speed` đang ở nhánh này) · Commit cuối: xem `git log -1` trong worktree · Cập nhật: 2026-10-12
- Chạy ở: PC · Trạng thái: xong A0, A1a, A2 và phase 2 (cả ba đã ở `merge/g1` = G1). A3 đang làm trên `perf/export-a3`: đã merge `merge/g1` (`6e813e0`, fast-forward), test liên quan đạt, A3.1 đã commit; tiếp theo là cổng x264 và đo NVENC. Chưa push, chưa merge. Chưa làm A1b/A4.

## Quyết định của người dùng (2026-10-12, A3)
- G1 đã làm: `merge/g1` = C + E + A + C0 (session điều phối gộp).
- A3 làm ngay sau G1, trên nhánh `perf/export-a3` tách từ `9f0581a`. Từ giờ chỉ commit vào nhánh này.
- Chờ người dùng báo "suite đạt" (full suite của `merge/g1`). Trước đó chỉ được đọc, thiết kế và viết code: không chạy test hay đo, không merge. Khi được báo: `git merge merge/g1` (không rebase), rồi mới chạy test và đo.
- Việc:
  1. chọn encoder: NVENC dùng được thì dùng, không thì x264. Hồ sơ phần cứng của E đã có kết quả NVENC thì dùng lại, không viết phép thử thứ hai; không có thì encode thử một lần. Lựa chọn nằm trong cài đặt render hoặc edit plan, không thêm cờ vào `cli.py`;
  2. NVENC `-rc vbr -cq <N>` với `-maxrate`/`-bufsize`; bản xuất vượt kiểm tra dung lượng thì tự encode lại bằng x264; manifest ghi thêm `encoding.encoder` và `validation_method`, giữ nguyên các hash của nguồn;
  3. yuv410p: nguồn yuv410p thì giới hạn số luồng gblur ở `chiều cao // 4`; nguồn 4:2:0 giữ nguyên đồ thị.
- Cổng:
  - x264 vẫn là mặc định; đường x264 giống hệt từng byte so với `9f0581a` (framemd5 + SHA-256 mp4) trên `smoke-synth` và T74;
  - NVENC chỉ chạy trong root tạm, giữ khóa GPU hoặc khóa render; khóa đang bị giữ thì chờ, không chen ngang;
  - đo SSIM (và VMAF nếu FFmpeg có), dung lượng và thời gian xuất trên 3 tập so với x264; trình bảng số đo cùng ngưỡng `-cq` đề xuất, rồi chờ người dùng duyệt;
  - đo VRAM của riêng NVENC bằng nvidia-smi; đo chung với Qwen2-VL để sau, khi làm A1b.
- Chưa làm A1b và A4. Không sửa: `scheduler.py`, `job_store.py`, `job_pipeline.py`, module quét, `hardware_*.py` (của E, chỉ đọc), `cli.py`, `__main__.py`, `__init__.py`, `run.ps1`, `env.ps1`, `stage_cache.py`, `cache_dependencies.py`, `pyproject.toml`, `config/*`, `models\`, `AGENTS.md`.
- Lịch sử commit giữ nguyên: `THIRD_PARTY_NOTICES.md` đã ghi tên bên build FFmpeg (khối C đã duyệt), nên banner trong lịch sử không lộ thêm gì.
- Bổ sung (2026-10-12, người dùng đã duyệt):
  1. NVENC tự lỗi (không phải vượt dung lượng; ví dụ GPU bận, hết VRAM, encoder trả mã lỗi) thì tự encode lại bằng x264 và ghi lý do fallback vào manifest, như khi vượt dung lượng, kèm test. Hủy hay dừng bản xuất (người dùng bấm) không được coi là lỗi NVENC;
  2. sau khi merge `merge/g1`, bỏ full suite: chỉ chạy test liên quan (`test_final_renderer`, `test_blur_filter`, file test mới, `test_scheduler`, `test_control_center`, `test_source_cleanup`, `test_skip_export`, test lưu trữ). Full suite để dành cho G2;
  3. vẫn chờ "suite đạt" rồi mới merge, chạy test hay đo;
  4. khóa GPU cho bản xuất khi NVENC thành mặc định: để sang A1b.

## Quyết định của người dùng (2026-10-11, sau khi A2 xong)
- Duyệt sửa lỗi gblur.
- Không squash commit `wip:` (`db0a139`).
- Giữ `perf/export-speed` nguyên cho G1.
- Phase 2 làm trên nhánh mới `perf/export-speed-p2`, tách từ `05653c9`, theo thứ tự:
  1. sửa gblur. Cổng: vùng đang đúng giữ nguyên byte, vùng đang sai được sửa, chạy 2 lần cùng hash;
  2. mặt nạ viền mềm tính một lần. Cổng: giống hệt từng byte;
  3. đồ thị bộ lọc truyền qua file.
- Chưa làm A3. Commit và cập nhật bàn giao sau mỗi bước. Nhánh `-p2` gộp sau G1. Chưa push hay merge.

## Đã xong

### A0 – đo từng bước (khung GPU 1)

Cách đo:
- `scripts/benchmark-export-steps.ps1` giữ `Local\BiliFlowGpuInference`, `Local\BiliFlowFinalRender` và `state\final-render.lock`, nên không chồng lên quét hay xuất thật.
- `scripts/benchmark_export_steps.py prepare` chép nguồn vào root tạm `temp\perf-export\<tên>\`. Nó lấy thao tác đã duyệt từ manifest bản xuất của job; DB và manifest chỉ được đọc.
- `measure` chạy đúng `render_final_output` của code gốc (`src` của `7d8b8bc` = `40a0125`) và đo từng lệnh FFmpeg, ffprobe và SHA-256 của nó.
- Các bước tách riêng dùng lại đúng lệnh FFmpeg đó:
  - `-f null` thay cho encode + mp4;
  - đồ thị không có vùng mờ;
  - giải mã nguồn;
  - remux có và không có `+faststart`;
  - `import biliflow.cli`;
  - các biến thể bộ lọc.
- Số thô nằm trong `docs/evidence/perf-export/` (README ở đó nói file nào chạy code nào).
- Control Center thật đang tắt suốt lúc đo.
- **Ba lượt A0 chạy cùng lúc với việc khác của chính session này** (test, review, commit). Cùng một việc, chúng chậm hơn 18–29 % so với các lượt cổng A2 chạy sau đó, khi session không làm gì khác:
  - L39 render: 177,5 s, so với 148,0 và 144,9 s;
  - T71 kiểm tra sau xuất: 28,6 s, so với 24,1 s.
- Vì vậy số A0 chỉ nên đọc như tỉ lệ và cỡ độ lớn. Số trước/sau của A2 lấy từ các lượt cổng và lượt đo lại.

`performance_report --root E:\DungChung\BiliFlow` (chỉ đọc), bản xuất thật:

| Job | Nhãn | Stage render (wall) | FFmpeg (manifest) | Tốc độ | Kiểm tra sau xuất |
|---|---|---|---|---|---|
| 71 | T | 1.231,9 s | 1.173,5 s | 2,21× | 37,1 s |
| 74 | T | 729,2 s | 691,2 s | 4,17× | 27,4 s |

**T71, bản sao đủ 2.594 s** (1280×534, 25 fps). Có 4 vùng mờ `delogo_blur_v1`: 2 vùng suốt phim (feather 2) và 2 vùng ngắn 5 s và 4 s (sigma 101 và 65, feather 4).

| Bước | Giây | Ghi chú |
|---|---|---|
| `render_final_output`, tổng | 931,6 | |
| SHA-256 nguồn trước / sau | 0,90 / 0,78 | |
| FFmpeg: bộ lọc + x264 + `+faststart` | 899,6 | 2,88× thời gian thực. SHA-256 mp4 trùng từng byte với bản xuất thật của job 71 (`437c60cc…`) |
| Kiểm tra sau xuất (giải mã toàn bộ) | 28,6 | |
| SHA-256 bản xuất | 1,58 | |
| Bộ lọc riêng (`-f null`: giải mã + bộ lọc, không encode) | 664,6 | |
| Encode riêng (cùng lệnh, bỏ mọi vùng mờ) | 382,8 | 6,78× |
| Giải mã nguồn riêng | 26,9 | |
| `+faststart` (remux có so với không) | +0,47 | |
| `import biliflow.cli` (mỗi stage render một lần) | 32,0 lần đầu; 7,4 và 7,3 các lần sau | Lượt này chạy sau khi đã sửa A1a, nên import code A1a (README bằng chứng) |

Kết luận cho T71:
- **Bộ lọc làm mờ chiếm khoảng một nửa đến 57 % thời gian render** (899,6 s render, so với 382,8 s chỉ encode). Đây là ước lượng, vì hai số này đo lúc máy bận khác nhau. Bộ lọc là chỗ cần sửa trước (A2), sau đó là encoder (A3).
- **Kiểm tra sau xuất, hash và `+faststart` cộng lại khoảng 31 s (3 %).** A4 chưa đáng làm.
- **Bản xuất thật chậm hơn lượt đo này** (1.173,5 s so với 899,6 s), dù cùng code và cùng byte đầu ra. Giả thuyết là tải máy lúc xuất khác nhau, chưa đo.
- **Phần ngoài FFmpeg trong stage render khoảng 18 s.** Đó là 1.231,9 s trừ FFmpeg, trừ kiểm tra sau xuất, trừ hash; phần lớn là khởi động CLI.
- **`ffmpeg -filters` (cột T):**
  - nhận `enable`: gblur (TS), geq (TS), delogo (T.), overlay (TS), alphamerge (T.);
  - không nhận `enable`: crop, split, format, scale, trim, concat, setpts.

Đoạn 300 s (code gốc). "Biến thể" là thời gian chỉ chạy bộ lọc (`-f null`), tính bằng giây:

| Bản sao | FFmpeg render | Bộ lọc riêng | Encode riêng | Giải mã | Không vùng mờ | Chỉ vùng suốt phim | Chỉ vùng ngắn | Bỏ feather | Đổi sang gblur |
|---|---|---|---|---|---|---|---|---|---|
| T71 0–300 s (1280×534, 3 vùng) | 78,6 (3,82×) | 71,4 | 39,5 | 3,4 | 3,6 | 22,0 | 23,2 | 24,2 | 35,1 |
| L39 1080p 3.600–3.900 s (4 vùng theo job 71, ×1,5) | 177,5 (1,69×) | 127,1 | 67,2 | 10,1 | 9,3 | 38,3 | 85,5 | 60,0 | 105,8 |

- Vùng ngắn (chỉ hiện vài giây) vẫn tốn bằng hoặc hơn vùng suốt phim, vì trước A2 nhánh của nó chạy trên mọi khung. A2 nhắm vào phần này.
- Feather (mặt nạ `geq` tính lại mỗi khung, rồi `alphamerge` và `overlay`) chiếm khoảng 2/3 chi phí bộ lọc trên T71 0–300 s. Mặt nạ không đổi giữa các khung, nên có thể làm thêm một bước sau (ngoài A2, cần người dùng duyệt).

### A1a – lệnh xuất chạy trước (commit `8b84149`)
- `_select` và `queue_order` dùng chung một thứ tự (`_waiting`):
  1. `priority` (cột có sẵn, chưa ai ghi);
  2. lệnh xuất đang chờ (stage kế tiếp là `render`) đứng trước lượt quét;
  3. trong mỗi loại, theo thứ tự bấm (`queue_seq`, rồi `id`).
- Lệnh xuất chỉ được lấy ở ranh giới stage kế tiếp. Stage đang chạy không bị cắt.
- Vẫn một worker. Mọi chốt chặn giữ nguyên (`_runnable`, tạm dừng, dừng, hủy).
- `_select` dừng ở job chạy được đầu tiên, nên một job hỏng phía sau không làm hỏng worker.
- Chữ trên dashboard cũ và V2, demo store và mock, harness node và hướng dẫn V2 giờ ghi "lệnh xuất chạy trước, mỗi loại theo thứ tự bấm".
- Test: `tests.test_scheduler` và `tests.test_control_center` (61 + 55 OK). Thêm các test cho:
  - thứ tự;
  - ranh giới stage khi một stage đang RUNNING;
  - `queue_order == _select`;
  - resume và retry giữa các lệnh xuất;
  - rerun lệnh xuất đã hủy;
  - job hỏng phía sau;
  - hàng đợi đang tạm dừng.
- Các test về thứ tự mới fail trên code cũ. Test `priority` và test rerun lệnh xuất đã hủy là chốt chặn, nên qua cả trên code cũ.
- Full suite sau đó bắt được một chỗ A1a bỏ sót: mã băm khóa cứng của trang dashboard cũ trong `tests/fixtures/dashboard_v2_classic_pages.json`.
  - Đã cập nhật. Trang mới chỉ khác bản gốc ở đúng ba dòng chữ của A1a (`scanStatus`, `exportStatus`, `jobCard`).
  - Mã băm mới: `a876dd0b…`, 101.790 byte.
- Review chỉ đọc: không có CRITICAL, HIGH hay MEDIUM. Các điểm LOW đã sửa.

### A2 – bộ lọc của nhánh làm mờ chỉ chạy trong thời gian làm mờ (commit `db0a139`, ghi `wip:` vì commit trước khi có cổng video thật; cổng qua ở commit sau)
- `blur_filter.py`: delogo, gblur và geq trong nhánh làm mờ một vùng mang cùng chuỗi `enable` với overlay cuối.
  - Ngoài thời gian làm mờ, ba bộ lọc này chuyển khung qua nguyên vẹn (timeline "generic" của FFmpeg). Overlay cuối khi đó vẫn trả khung gốc, nên mọi khung đầu ra như cũ.
  - crop, split, format, alphamerge và overlay bên trong không đổi; FFmpeg không nhận `enable` trên crop, split và format.
  - Không tách đoạn.
  - Bản xem trước trong `review_workflow.py` dùng cùng hàm, nên cũng được gate như vậy.
- Test:
  - `tests.test_blur_filter`: chuỗi bộ lọc; chỉ delogo, gblur và geq mang `enable`; `BlurGatingEquivalenceTests` gồm 6 ca tổng hợp, framemd5 và SHA-256 mp4 giống hệt giữa đồ thị cũ và mới;
  - `tests.test_final_renderer`.
- **Cổng trên video thật** (`gate`, code cũ so với nhánh này). Bốn lượt chạy nối nhau, lúc session không làm gì khác. Kết quả trong `docs/evidence/perf-export/<bản>/*-gate.json`:

| Bản sao | Dòng framemd5 | framemd5 `[vout]`+`[aout]` / SHA-256 mp4 | Bộ lọc + giải mã (framemd5), trước → sau | FFmpeg render, trước → sau | Nhanh hơn |
|---|---|---|---|---|---|
| Clip tổng hợp 20 s, 4 vùng | 1.438 | giống hệt / giống hệt; baseline chạy 2 lần cũng giống hệt | 4,0 → 2,2 s | 4,9 → 3,1 s | 1,56× |
| L39 1080p 300 s, 4 vùng | 20.426 | giống hệt / giống hệt; baseline chạy 2 lần cũng giống hệt | 122,2 → 50,6 s | 148,0 và 144,9 → 83,5 s | 1,75× |
| T74 đủ 2.880 s, 2 vùng ngắn | 133.989 | giống hệt / giống hệt; baseline trùng bản xuất thật của job 74 (`3f2de702…`) | 385,6 → 120,3 s | 631,7 → 330,5 s (4,56× → 8,71× thời gian thực) | 1,91× |
| T71 đủ 2.594 s, 2 vùng suốt phim + 2 vùng ngắn | 125.645 | giống hệt / giống hệt (`437c60cc…` = bản xuất thật của job 71) | 515,9 → 223,5 s | 741,8 → 451,4 s (3,50× → 5,75× thời gian thực) | 1,64× |

- Số "trước" của T71 là một lượt đo lại code cũ (13:47). Lượt này chạy lúc session không làm gì khác. Kiểm tra sau xuất của nó mất 24,3 s, bằng các lượt cổng.
  - Hai lượt trước của cùng code chậm hơn vì máy bận: 899,6 s (A0) và 814,6 s (chạy cùng lúc với review).
  - Cả ba lượt đều ra cùng byte `437c60cc…`.
- Kiểm tra sau xuất không đổi (T74: 25,6 s so với 25,9 s).
- Theo phút, FFmpeg render của một tập T: T74 từ 10,5 còn 5,5 phút; T71 từ 12,4 còn 7,5 phút. Kế hoạch mong 1,5–3 phút sau cả A2 lẫn A3, nên A3 (encoder) vẫn cần.
- Đồ thị dài thêm khoảng 18 % (T71: 2.710 → 3.205 ký tự).
- Review chỉ đọc theo 4 hướng (A2, test, harness, tài liệu và quyền riêng tư), mỗi phát hiện có một người phản biện. Không có lỗi làm đổi đầu ra. Năm điểm được xác nhận, đều LOW:
  1. **Độ dài dòng lệnh.** Windows giới hạn một dòng lệnh ở 32.767 ký tự, và đồ thị được truyền thẳng trên dòng lệnh. Rủi ro này có từ trước A2.
     - Một plan kiểu T71 (hai vùng che suốt phim có feather) vượt giới hạn từ khoảng 19 lần cắt sau A2, so với khoảng 22 lần trước A2.
     - Plan thật dài nhất hiện nay chỉ khoảng 10 % giới hạn.
     - Đề xuất truyền đồ thị qua file (`-/filter_complex <file>`) khi làm A3, vì A3 cũng sửa lệnh FFmpeg. Người dùng đã chọn làm việc này ở phase 2 bước 3.
  2. **Harness: `gate --framemd5-only` báo "passed".** Đã sửa: giờ ghi `passed: null` và thoát mã 2.
  3. **Harness bỏ qua chính sách dung lượng của job.** Đã sửa: `prepare` ghi `size_policy` và mọi lượt render dùng nó. Các bản cũ dùng mặc định, cũng là chính sách mà job 71 và 74 đã dùng khi xuất.
  4. **Số A0 đo lúc máy bận.** Đã ghi rõ ở trên và trong README bằng chứng.
  5. **README ghi sai code mà file A0 của T71 đã chạy.** Đã sửa.
- Tên miền của bên build FFmpeg (có trong banner phiên bản) đã được bỏ khỏi kết quả. Harness giờ chỉ ghi số phiên bản.

### Phase 2 · bước 1 – sửa lỗi gblur (nhánh `perf/export-speed-p2`)
- `blur_filter.py`: mỗi gblur của một vùng mờ mang `threads=max(1, chiều cao // 2)`, tức số dòng của mặt phẳng màu với nguồn 4:2:0 (crop làm tròn chiều cao lẻ xuống). FFmpeg dùng số nhỏ hơn giữa giới hạn này và số luồng của nó. Giới hạn này làm mọi việc của gblur đều có dòng, nên bỏ được lỗi tranh chấp trên mọi CPU.
  - Đã đo trên máy này (AVX2, 13 luồng), với vùng rộng từ 128 px: vùng cao từ 26 dòng không đổi gì, giữ nguyên byte; vùng thấp hơn giờ ra như khi chạy 1 luồng, giống nhau ở mọi lần chạy.
  - Chưa đo: CPU khác (AVX-512, chỉ SSE), vùng rộng từ 16 px trở xuống, máy có trên 26 luồng (ở đó vùng từ 26 dòng cũng đổi, từ sai thành đúng).
  - Chưa phủ: nguồn yuv410p (4:1:0, ví dụ file SVQ1 hay Indeo cũ). Không gì đổi nhánh sang 4:2:0 trước gblur, nên mặt phẳng màu có `chiều cao // 4` dòng và nguồn đó vẫn sai như trước khi sửa (không tệ hơn). Cách sửa đề xuất ở mục Chờ người dùng.
  - gblur toàn khung giữ mặc định (khung có hàng trăm dòng).
- Test:
  - `tests.test_blur_filter`: số `threads` theo chiều cao (1 đến 301 dòng, cả hai cách làm mờ, có và không có feather);
  - `ThinRegionBlurTests` (FFmpeg thật): vùng 8, 15 và 24 dòng, hai cách, feather 0 và 4: hai lần chạy mặc định đều bằng bản 1 luồng; vùng 64 dòng: đồ thị có giới hạn và không giới hạn ra cùng framemd5;
  - trên code cũ: 55 fail; sau khi sửa: `test_blur_filter` và `test_final_renderer` 34 test OK.
- Harness: `prepare --ops-file` (danh sách thao tác viết tay), `_gate-side --filter-threads`, lệnh mới `stability` (chạy một bộ code nhiều lần, cộng một framemd5 với 1 luồng bộ lọc; chỉ đạt khi mọi lần cùng framemd5, cùng SHA-256 mp4 và bằng bản 1 luồng).
- **Cổng** (kết quả trong `docs/evidence/perf-export/<bản>/`). Code A2 là bản đông cứng `git archive 05653c9 src`, code bước 1 là bản đông cứng của worktree (cả hai ở `temp\perf-export\`):

| Bản sao | Cổng | Kết quả |
|---|---|---|
| `T71-thin60`: 60 s đầu job 71, 8 vùng viết tay cao 8, 14, 15 và 24 dòng (gblur và che, feather 0/2/3/4, `all_edges` và `vertical_only`, suốt và ngắn) + 1 lần cắt | `stability`, code A2 | Hỏng, đúng như dự đoán: hai lần chạy khác nhau cả framemd5 lẫn mp4, và không lần nào bằng bản 1 luồng |
| `T71-thin60` | `stability`, code bước 1 | Đạt: hai lần chạy cùng framemd5 `3546c609…`, bằng bản 1 luồng; hai mp4 giống hệt (`821bec86…`). Bản 1 luồng của code A2 cũng là `3546c609…`: bản sửa cho đúng điểm ảnh của 1 luồng |
| clip tổng hợp 20 s | `gate` A2 → bước 1 | Giữ nguyên byte: framemd5 giống hệt, mp4 `1aa9f941…` như A2 |
| L39 1080p 300 s | `gate` A2 → bước 1 | Giữ nguyên byte: framemd5 giống hệt, mp4 `a9da0a09…` như A2 |
| T74 đủ | `gate` A2 → bước 1 | Giữ nguyên byte: 133.989 dòng framemd5 giống hệt, mp4 `3f2de702…` (= bản xuất thật của job 74) |
| T71 đủ | `gate` A2 → bước 1 | Giữ nguyên byte: 125.645 dòng framemd5 giống hệt, mp4 `437c60cc…` (= bản xuất thật của job 71) |

- Các cổng `gate` dùng `--baseline-mp4-sha256` (SHA-256 của bản render A2 đã có), nên phía A2 chỉ chạy framemd5. Cổng bước 1 chạy lúc các agent nghiên cứu của session cũng dùng CPU, nên thời gian của chúng không dùng để so.

### Phase 2 · bước 2 – mặt nạ viền mềm tính một lần
- `blur_filter.py`: mặt nạ viền mềm chỉ phụ thuộc kích thước vùng (`X`, `Y`, `W`, `H`), vậy mà geq tính lại nó ở mọi khung trong thời gian làm mờ. Giờ nhánh mặt nạ chỉ lấy khung đầu của nhánh (`trim=end_frame=1`; geq không mang `enable`, không có `setpts`), và `alphamerge` lặp khung đó cho mọi khung sau.
  - Tùy chọn lặp (`eof_action=repeat:repeatlast=1:shortest=0`, hằng `MASK_REPEAT_ALPHAMERGE`) là mặc định của FFmpeg. Code ghi rõ chúng để nếu FFmpeg đổi mặc định thì mặt nạ cũng không mất.
- Test (`tests.test_blur_filter`):
  - chuỗi lọc chính xác; một kiểm tra rằng biểu thức mặt nạ chỉ đọc `X`, `Y`, `W`, `H`;
  - `OneTimeMaskEquivalenceTests` (FFmpeg thật), so với mặt nạ tính mỗi khung: 5 ca tổng hợp (cửa sổ muộn, hai khoảng, có cắt, vùng mỏng). framemd5 và SHA-256 mp4 x264 giống hệt;
  - một ca đối chứng âm: mặt nạ không được lặp thì khung ra khác.
- **Cổng**: code bước 1 (baseline, `temp\perf-export\p2-step1\src`) với bước 2 (`p2-step2\src`). Cả hai phía đều render, nên mỗi cổng cũng là một cặp đo thời gian. Harness đông cứng ở `temp\perf-export\harness-p2`, để các sửa đổi harness của bước 3 trong worktree không chạm tới các cổng này. Kết quả trong `docs/evidence/perf-export/<bản>/`:

| Bản sao | framemd5: chỉ bộ lọc, không mã hóa (bước 1 → bước 2) | FFmpeg render (bước 1 → bước 2) | Kết quả |
|---|---|---|---|
| clip tổng hợp 20 s | 3,99 → 2,39 s | 4,49 → 4,08 s | giống hệt; mp4 `1aa9f941…` |
| `T71-thin60` | 7,32 → 4,45 s | 11,81 → 10,57 s | giống hệt; mp4 `821bec86…` |
| L39 1080p 300 s | 84,68 → 49,58 s | 122,87 → 101,92 s | giống hệt; mp4 `a9da0a09…` |
| T74 đủ | 147,37 → 116,61 s | 441,88 → 450,33 s | giống hệt 133.989 dòng; mp4 `3f2de702…` |
| T71 đủ | 390,12 → 582,24 s (không dùng để so, xem dưới) | 1.000,57 → 895,54 s (không dùng để so) | giống hệt 125.645 dòng; mp4 `437c60cc…` (= bản xuất thật của job 71) |

- Các cổng bước 2 chạy lúc máy không rảnh: cổng A2 render T74 trong 330,5 s, ở đây cùng các byte đó mất 441,9 s. Vì vậy chỉ so hai phía của cùng một cổng. Ngay cả vậy vẫn có nhiễu: render T74 của bước 2 chậm hơn 2 %, nằm trong nhiễu, vì T74 chỉ có hai vùng ngắn và thời gian render do x264 quyết định.
- Lượt T71 đầu tiên (19:13:50) bị dừng lúc 19:36 vì người dùng cần máy. Khi đó framemd5 hai phía đã giống hệt và phía bước 1 đã render ra `437c60cc…`; bản render dở của phía bước 2 đã bị xóa.
- Cổng T71 trong bảng là lượt chạy lại đủ hai phía (2026-10-12, 01:01:17–01:51:10), sau khi bản xuất thật của job 114 trên Control Center xong. Control Center không chạy gì khi đó: các job 115–124 đều tạm dừng. Phần mềm edit video của người dùng vẫn mở và dùng CPU, nên thời gian của lượt này không dùng được: render cùng các byte đó ở bước 1 mất 438,5 s, ở đây mất 1.000,6 s, và framemd5 của bước 2 còn chậm hơn bước 1. Lượt này chỉ chứng minh giữ nguyên byte.

### Phase 2 · bước 3 – đồ thị bộ lọc truyền qua file
- Lý do: trên dòng lệnh, một kế hoạch có nhiều lần cắt sẽ vượt giới hạn 32.767 ký tự của Windows (WinError 206). Đồ thị của A2 dài hơn khoảng 18 %, nên giới hạn này lùi từ khoảng 22 xuống khoảng 19 lần cắt với kế hoạch dạng T71.
- `final_renderer.py`: bản xuất ghi đồ thị vào `<root>\temp\render-progress\<id>.filtergraph` (`render_graph_path`, cạnh file tiến độ) rồi đưa cho FFmpeg bằng `-/filter_complex <file>` (FFmpeg 7 trở lên; `-filter_complex_script` đã bị bỏ ở 9.0).
  - File chứa đúng các byte UTF-8 của đồ thị. Đồ thị có ký tự NUL bị từ chối, vì FFmpeg sẽ cắt đồ thị ở đó.
  - Thư mục của file phải nằm trong `temp` của root.
  - File hay link còn sót ở đường dẫn đó bị xóa trước, rồi file mới được tạo riêng (`xb`). Không bao giờ ghi xuyên qua một link.
  - File bị xóa khi FFmpeg xong, lỗi hay ném ngoại lệ. Nếu không xóa được, kết quả của FFmpeg vẫn được trả về như bình thường; lần render sau của cùng bản xuất sẽ thay file đó.
- `scheduler.py`: dọn dẹp sau khi dừng cũng xóa file đồ thị, vì tiến trình render bị kill trước khi kịp xóa.
- Test:
  - `test_final_renderer`: đồ thị đến FFmpeg qua file trong `temp` và bị xóa sau đó; bị xóa cả khi FFmpeg lỗi; file cũ bị thay; link không bị ghi xuyên qua; thư mục dẫn ra ngoài `temp` bị từ chối trước khi FFmpeg chạy; NUL bị từ chối; xóa file thất bại không che kết quả render;
  - `test_scheduler`: sau khi dừng, file đồ thị bị xóa, còn file đồ thị của bản xuất khác vẫn giữ;
  - `test_render_graph_file` (FFmpeg thật): file cho cùng framemd5 với đồ thị trên dòng lệnh; một đồ thị dài hơn giới hạn dòng lệnh của Windows vẫn render được. Code cũ fail với WinError 206 trên đồ thị 46.001 ký tự.
  - Các module liên quan (`test_blur_filter`, `test_final_renderer`, `test_scheduler`, `test_render_graph_file`, `test_review_workflow`): 219 test OK, không bỏ qua.
- Harness: nhận cả hai cách truyền đồ thị (`-filter_complex` và `-/filter_complex`), đọc đồ thị của mỗi lượt render, ghi cách truyền (`graph_option`) và SHA-256 của nó. `gate` và `stability` kiểm rằng đồ thị FFmpeg nhận đúng là đồ thị đã đo (`render_graphs_match`).
- `blur_filter.py`: docstring của `_gblur` giờ chỉ nói điều đã đo (CPU AVX2, vùng rộng từ 128 px). Code không đổi.
- **Cổng** (2026-10-12): code bước 2 (`p2-step2\src`, chỉ chạy framemd5; SHA-256 mp4 của bước 2 đưa vào bằng `--baseline-mp4-sha256`) với bước 3 (`p2-step3\src`, render thật). Harness đông cứng `temp\perf-export\harness-p3`. Đồ thị không đổi, chỉ đổi cách FFmpeg nhận nó, nên một bản đủ thật (T74) là đủ:

| Bản sao | Kết quả |
|---|---|
| clip tổng hợp 20 s | Đạt: bản render bước 3 nhận đồ thị qua `-/filter_complex`, đồ thị khớp, framemd5 giống hệt, mp4 `1aa9f941…` |
| `T71-thin60` | Đạt: như trên, mp4 `821bec86…` |
| T74 đủ | Đạt: như trên, 133.989 dòng framemd5 giống hệt, mp4 `3f2de702…` (= bản xuất thật của job 74). FFmpeg render 394,9 s |

## Đang dở
- **A3.1, đã commit** (nhánh `perf/export-a3`, sau khi merge `merge/g1`; dùng `install_paths` của C2 và `hardware_schema` của E). Còn cổng x264, đo NVENC và duyệt:
  - `src/biliflow/export_encoder.py` (mới):
    - plan có `render_settings.video_encoder` = `"libx264"` (cũng là mặc định khi không có khóa) hoặc `"auto"`;
    - `"auto"` chỉ dùng NVENC trong root tạm: root nằm trong một thư mục tên `temp` và bản thân không có file đánh dấu thư mục cài (`biliflow-install.json`). Không dùng `install_root()`: trong worktree nó trả về worktree, và bản đông cứng không có file đánh dấu;
    - NVENC dùng được hay không: lấy từ hồ sơ của E ở root đó (`detection.ffmpeg.encoders.h264_nvenc.trial`, chỉ khi đường dẫn, kích thước và mtime của `ffmpeg.exe` khớp), không có thì encode thử 1 s (`-f null`, tối đa 60 s);
    - `video_arguments`: x264 giữ đúng tham số cũ; NVENC `-preset p5 -rc vbr -cq 23 -b:v 0` cộng `-maxrate`/`-bufsize` như x264 và `-tune hq -spatial-aq 1 -rc-lookahead 20`. Đây là điểm xuất phát, phép đo sẽ đề xuất giá trị.
  - `final_renderer.py`:
    - chọn encoder, chạy render; bản NVENC vượt giới hạn dung lượng thì xóa bản dở và encode lại bằng x264;
    - NVENC tự lỗi thì cũng xóa bản dở và encode lại bằng x264 (`export_encoder.failed_by_itself`). Cách phân biệt với một lần dừng:
      - FFmpeg phải thoát mã khác 0 và in thông báo lỗi (lệnh xuất chạy với `-loglevel error`), hoặc thoát bằng mã crash NTSTATUS (từ `0xC0000000`);
      - Tạm dừng, Hủy hay tắt Control Center giết cả cây tiến trình render (`scheduler.terminate_process_tree`, `taskkill /T /F`): FFmpeg thoát mã 1 và không in gì, nên không được tính là lỗi;
      - trước lần x264 còn chờ `STOP_GRACE_SECONDS` = 3 s: nếu `taskkill` giết FFmpeg trước tiến trình Python, lệnh dừng sẽ giết luôn Python trong lúc chờ, nên không có lần x264 nào chạy sau khi người dùng dừng (kể cả với nguồn làm FFmpeg in lỗi giải mã);
      - x264 lỗi vẫn báo lỗi như cũ, không render lại;
    - manifest thêm `encoding.encoder`, `video_arguments`, `encoder_choice` (`requested`, `nvenc_check`), `fallback` (`null`, `{from, reason: "size_limit", bytes, elapsed_seconds}` hoặc `{from, reason: "encoder_error", returncode, message, elapsed_seconds}`; `message` là tối đa 3 dòng đầu của FFmpeg, 500 ký tự: FFmpeg in nguyên nhân trước, các dòng sau chỉ báo luồng kết thúc) và `validation_method: "cpu_full_decode"`; `gpu_used` đúng với NVENC; các hash của nguồn giữ nguyên;
    - nguồn yuv410p truyền `chroma_rows_divisor=4` xuống `blur_filter`;
    - file dài 830 dòng, vượt mức mềm 800. Cố ý giữ: toàn bộ đường render nằm chung một module, và `scheduler.py` (không được sửa) import trực tiếp từ đó. Phần nhận diện lỗi và bản ghi fallback nằm ở `export_encoder.py`.
  - `blur_filter.py`: `_gblur`/`regional_blur_filters` nhận `chroma_rows_divisor` (2 hoặc 4, mặc định 2, nên đồ thị 4:2:0 không đổi).
  - Test (đạt, xem Cách kiểm):
    - `tests/test_export_encoder.py` (mới): yêu cầu, root tạm, hồ sơ, chọn encoder, tham số, nhận diện lỗi và bản ghi fallback, và một lần xuất NVENC thật trong root tạm. Test thật này giữ khóa GPU, chờ tối đa 120 s, rồi bỏ qua nếu khóa vẫn bận;
    - thêm vào `test_final_renderer` (kể cả NVENC tự lỗi, crash không thông báo, lần dừng không phải lỗi, x264 lỗi không render lại, x264 thay thế cũng lỗi) và `test_blur_filter`, kèm lớp `Yuv410pThinRegionBlurTests` (nguồn FFV1 yuv410p).
  - Harness:
    - lệnh mới `quality` (`scripts/benchmark_export_quality.py`): x264 hôm nay, `"auto"`, và lệnh x264 thay bằng tham số NVENC cho từng preset và `-cq`; đo dung lượng, thời gian, VRAM (nvidia-smi, mỗi 0,5 s), SSIM và VMAF so với chính khung của đồ thị;
    - `_render_once` nhận `render_settings` và trả về thông tin encoder, bỏ `fallback.message` (chữ của FFmpeg có thể chứa đường dẫn, không được vào evidence); lần encode thử có nhãn riêng (`ffmpeg_encoder_trial`).
- **Đã kiểm sau khi merge (2026-10-12, FFmpeg 9.0.1, clip tổng hợp trong `temp`):**
  - Khoảng hở yuv410p có thật. `-loglevel debug` cho thấy FFmpeg chèn bộ đổi định dạng sau gblur (giữa gblur và overlay), nên gblur chạy trên yuv410p. Giới hạn `// 2` cho sai 36 trên 50 khung (đúng các khung trong khoảng làm mờ) so với gblur 1 luồng; giới hạn `// 4` khớp cả 50 khung, ở mọi lần chạy.
  - Test yuv410p ban đầu so với `-filter_complex_threads 1` và trượt 12 lần, vì chính bộ đổi yuv410p sang 4:2:0 cho kết quả khác theo số luồng (một bản chép `null` không làm mờ cũng khác ở cả 50 khung). Test nay so với đồ thị đó với gblur 1 luồng, và có thêm test cho thấy `// 2` không đủ. Đổi định dạng theo số luồng là chuyện có từ trước, không do A3, và ảnh hưởng mọi bản xuất từ nguồn yuv410p (không chỉ vùng mỏng).
  - Lệnh dừng: FFmpeg đang encode NVENC bị `taskkill /T /F` thì thoát mã 1, không in gì, nên `failed_by_itself` là `False`.
  - Lỗi NVENC thật (khung 8192×8192): mã thoát `0xDFABA7BB`, dòng đầu "No capable devices found", nên `failed_by_itself` là `True`.

## Bước tiếp theo
1. Khi người dùng báo "suite đạt": `git merge merge/g1` (không rebase) vào `perf/export-a3`; chỉ chạy test liên quan, không chạy full suite (để dành cho G2): `test_final_renderer`, `test_blur_filter`, `test_export_encoder`, `test_render_graph_file`, `test_scheduler`, `test_control_center`, `test_source_cleanup`, `test_skip_export`, `test_source_archive` (test lưu trữ; trên `merge/g1` còn có `test_source_archive_http` và `test_source_cleanup_http`, chạy kèm); commit A3.1.
2. ~~Cổng x264~~ **Đạt (2026-10-12 08:54).** Đã đông cứng `temp\perf-export\a3\src` (= `f4e80c2`) và harness `harness-a3`; chạy `gate --baseline-src p2-step3\src` (= `9f0581a`) `--candidate-src a3\src` trên `smoke-synth` và T74, cả hai phía render. framemd5 và SHA-256 mp4 giống hệt, và trùng mp4 của A2/bước 2/bước 3 (`smoke-synth` `1aa9f941…`, T74 `3f2de702…`; T74 mất 975 s). JSON trong `docs/evidence/perf-export/<bench>/20261012-0854*-gate.json`.
3. Đo NVENC (`quality`, có `--vmaf`): dò preset và `-cq` trên 2 đoạn 300 s (`T71-seg300`, `L39-1080-seg300`), rồi 3 tập đủ với `-cq` đề xuất: T71, T74 (1280×534, 43 và 48 phút) và H37 (job 37 là phim hoạt hình, nhãn H; trước đây ghi nhầm là L37). Bản xuất của job 37 không còn trong `output\` (chỉ còn nguồn và edit plan đã duyệt), nên `prepare --job 37` thất bại. H37 được chuẩn bị bằng `prepare --name H37 --job 37 --ops-file temp\perf-export\H37.ops.json`; file thao tác chép từ `approved_operations` của edit plan đã duyệt (trạng thái `FINAL_RENDER_COMPLETED`; 1 vùng làm mờ suốt phim, 308×69). Bản sao: 1920×1080, 6.738 s, 23,976 khung/s, chính sách dung lượng mặc định. Không còn job 1080p nào giữ cả nguồn lẫn bản xuất; job 39 dài 11.763 s nên không dùng cả tập. Ghi VRAM. Trình bảng số đo và `-cq` đề xuất, chờ người dùng duyệt.
4. Sau khi duyệt (chưa làm): đặt giá trị đã duyệt; "auto" thành mặc định cho root thật; ghi vào `AGENTS.md` (người dùng/điều phối) rằng bản xuất NVENC được dùng làm bằng chứng cho "Xóa video gốc" và "Lưu trữ"; dòng NVENC trong bảng của E. Sau đó mới đến A1b.

## Cách kiểm
- **Test liên quan của A3** (2026-10-12, sau khi merge `merge/g1` `6e813e0`; không chạy full suite, theo quyết định của người dùng): `test_final_renderer`, `test_blur_filter`, `test_export_encoder`, `test_render_graph_file`, `test_scheduler`, `test_control_center`, `test_source_cleanup`, `test_source_cleanup_http`, `test_skip_export`, `test_source_archive`, `test_source_archive_http`: 352 test trong 128,4 s, OK, không bỏ qua (kể cả lần xuất NVENC thật trong root tạm). Lượt đầu trượt 12 subtest yuv410p vì mốc so sai (xem Đang dở); đã sửa test, không sửa code.
- **Full suite trên `perf/export-speed-p2`** (2026-10-12, code bước 3, có clip tổng hợp 1 s trong `input\` của worktree, đã xóa sau khi chạy): 2.684 test trong 2.207,7 s, OK: 0 fail, 0 lỗi, 26 bỏ qua (do môi trường, như lần trước).
  - Lượt đầu (02:10) bị ngắt lúc 02:14 khi phiên làm việc kết thúc. Lượt đó có 1 lỗi ở `test_download_account_browser` (test mở Edge thật của phần tải video; nhánh này không sửa phần đó).
  - Chạy riêng module đó: 65 test OK. Lượt full suite chạy lại (07:05, có giới hạn 70 phút) đạt như trên.
- **Full suite trên nhánh** (2026-10-11, có clip tổng hợp 1 s trong `input\` của worktree, đã xóa sau khi chạy): 2.669 test trong 2.233,8 s, 26 bỏ qua (do môi trường), 3 fail.
  - Cả 3 fail đều là mã băm khóa cứng của trang dashboard cũ, mà A1a đã cố ý đổi. Fixture đã được cập nhật.
  - Chạy lại 4 module dùng fixture này: 73 test OK, 1 bỏ qua.
- Test cổng tổng hợp của A2 giờ dùng vùng cao 64 dòng, để không chập chờn trên máy nhiều nhân (lỗi gblur ở trên). Sau khi đổi: `test_blur_filter` và `test_final_renderer` 31 test OK.
- Lệnh test (trong worktree, PowerShell):
  `$env:PYTHONPATH="$PWD\src;$PWD"; $env:TEMP='E:\DungChung\BiliFlow\temp'; $env:TMP=$env:TEMP; $env:BILIFLOW_FFMPEG='E:\DungChung\BiliFlow\tools\ffmpeg\bin\ffmpeg.exe'; E:\DungChung\BiliFlow\.venv\Scripts\python.exe -m unittest tests.test_scheduler tests.test_control_center tests.test_blur_filter tests.test_final_renderer -v`
- Đo: `powershell -File scripts\benchmark-export-steps.ps1 -InstallRoot E:\DungChung\BiliFlow measure --name <tên> [--src <src>]`.
- Cổng A2: `... gate --name <tên> --baseline-src E:\DungChung\BiliFlow\temp\perf-export\baseline-40a0125\src --candidate-src <worktree>\src [--baseline-twice] [--baseline-mp4-sha256 <hex>]`.
  - Mã thoát 0 chỉ khi framemd5 của `[vout]`/`[aout]` và SHA-256 mp4 x264 giống hệt; với `--baseline-twice` thì hai lượt baseline cũng phải giống nhau.
  - `--framemd5-only` không bao giờ được tính là qua cổng: khớp thì thoát 2.
  - Bốn lượt đã chạy dùng script `run-gates.ps1` trong scratchpad của session. Lệnh tương đương: `gate --name smoke-synth --baseline-twice`, `gate --name L39-1080-seg300 --baseline-twice`, `gate --name T74`, `gate --name T71 --baseline-mp4-sha256 437c60cc8dda369ef1baf1bc9222e8953cdf975cab72506df19e97994c3a83a5`.
- Phase 2 bước 1:
  - Bản vùng mỏng: `... prepare --name T71-thin60 --job 71 --segment 0 60 --ops-file docs\evidence\perf-export\T71-thin60\thin-regions.ops.json`, rồi `... stability --name T71-thin60 --src <src>`. Mã thoát 0 chỉ khi đạt.
  - Cổng giữ nguyên byte: `gate --baseline-src E:\DungChung\BiliFlow\temp\perf-export\a2-05653c9\src --candidate-src <bản bước 1>` với `--baseline-mp4-sha256` lấy từ bảng A2 (smoke `1aa9f94172e152b1a66bfd2e089e4ce1f4eb3a6fee5a0c93a626c8f9570f1e17`, L39 `a9da0a09da5666efb22f02524028041af8d00debcafcca65a48149df1a8f4355`, T74 `3f2de70275670aa8ef934bfc75dfee2d7a5e8db996ed37eca2de822e54c3ab43`, T71 `437c60cc…` như trên).
  - Bản đông cứng để cổng không đổi theo worktree: `temp\perf-export\a2-05653c9\src` (A2), `temp\perf-export\p2-step1\src` (bước 1).
- Phase 2 bước 2: `temp\perf-export\harness-p2\scripts\benchmark-export-steps.ps1 -InstallRoot E:\DungChung\BiliFlow gate --name <tên> --baseline-src E:\DungChung\BiliFlow\temp\perf-export\p2-step1\src --candidate-src E:\DungChung\BiliFlow\temp\perf-export\p2-step2\src`, không có `--baseline-mp4-sha256`, nên cả hai phía render. Đã chạy cho `smoke-synth`, `T71-thin60`, `L39-1080-seg300`, `T74`, `T71`.
- Phase 2 bước 3: `temp\perf-export\harness-p3\scripts\benchmark-export-steps.ps1 -InstallRoot E:\DungChung\BiliFlow gate --name <tên> --baseline-src E:\DungChung\BiliFlow\temp\perf-export\p2-step2\src --candidate-src E:\DungChung\BiliFlow\temp\perf-export\p2-step3\src --baseline-mp4-sha256 <SHA-256 của bước 2>`. Đã chạy cho `smoke-synth`, `T71-thin60`, `T74`. Ngoài mã thoát 0, kiểm `render_graph_option` của phía bước 3 là `-/filter_complex` và `render_graphs_match` là true.
- Bản đông cứng: `temp\perf-export\p2-step2\src` (bước 2), `p2-step3\src` (bước 3); harness `harness-p2` (cổng bước 2) và `harness-p3` (cổng bước 3).

## Chờ người dùng
- Báo "suite đạt" cho `merge/g1`, để A3 merge rồi chạy test và đo.
- Duyệt NVENC (SSIM/VMAF, dung lượng, thời gian trên 3 tập, ngưỡng `-cq`) khi có số đo.
- Hai câu hỏi trước đã có lời đáp (2026-10-12): NVENC tự lỗi thì quay về x264 (đã viết, xem Đang dở); khóa GPU cho bản xuất khi NVENC thành mặc định để sang A1b. Hiện render chỉ giữ khóa render, nên một lần xuất NVENC có thể chạy cùng lúc với một lần quét trên GPU (6 GB).
- **Lỗi có từ trước trong FFmpeg gblur, tìm thấy khi tự kiểm tra harness** (chi tiết và số đo trong `docs/evidence/perf-export/gblur-threads.md`). Người dùng đã duyệt sửa ngày 2026-10-11; đã sửa ở phase 2 bước 1 (ở trên).
  - Khi một mặt phẳng ảnh có ít dòng hơn số luồng bộ lọc, gblur cho điểm ảnh sai, lệch tới 171–178/255, ở mọi khung, và kết quả đổi giữa các lần chạy. Máy này có 13 luồng: vùng yuv420p thấp dưới 26 dòng bị sai màu, dưới 13 dòng sai cả độ sáng.
  - Không do A2, vì code cũ cũng vậy.
  - 22 bản xuất đã có không bị: vùng thấp nhất là 30 dòng. Nhưng trên máy có từ 16 luồng trở lên, vùng 30 dòng sẽ bị sai màu nếu chạy code chưa sửa (`perf/export-speed`, `main`).
  - Còn hở (review 2026-10-11, LOW): nguồn yuv410p chưa được phủ. Người dùng quyết ngày 2026-10-12: gộp vào A3 (giới hạn `chiều cao // 4` khi nguồn là yuv410p, kèm test). Đang làm, xem mục Đang dở.
- **Tên miền của bên build FFmpeg trong lịch sử commit** (`a90bbf4`, `db0a139`): người dùng quyết ngày 2026-10-12 giữ nguyên lịch sử, vì `THIRD_PARTY_NOTICES.md` đã ghi tên bên build theo khối C đã duyệt.

## Lưu ý cho người tiếp quản
- Không sửa: `cli.py`, `__main__.py`, `__init__.py`, `run.ps1`, `env.ps1`, `stage_cache.py`, `cache_dependencies.py`, `pyproject.toml`, `config/*`, `models\`. Các file nhánh này đã sửa nằm ngoài dấu vân tay cache quét: đã kiểm bằng `stage_source_paths` cho cả 10 stage có cache.
- `control_center.py` và `dashboard_v2/` cũng do session E sửa (mục 9.6: E trước, A sau). Lúc gộp G1, dòng Cài đặt ở `dashboard_v2/app.js` (`1 GPU · xuất trước, mỗi loại theo thứ tự bấm`) có thể đụng phần của E.
- Kế hoạch ghi "dùng `priority` có sẵn". Ở đây `priority` vẫn là khóa sắp xếp đầu tiên, còn "lệnh xuất trước" được tính lúc chọn, không ghi vào cột. Lý do: lệnh xuất bị tạm dừng, thử lại hay hủy thì tự về đúng loại, không cần sửa cột.
- Lỗi có từ trước, không do A1a (theo đọc code, chưa tái hiện): nếu Hủy rơi vào khoảng vài mili giây giữa `claim_queued` và lúc `_execute` ghi stage RUNNING, một lệnh xuất đã hủy vẫn có thể chạy xong. A1a không đổi khoảng này. Chưa sửa.
- Thư mục `temp\perf-export\` (bản sao video, kết quả thô, baseline `baseline-40a0125\src`) là dữ liệu tạm cục bộ, khoảng 0,9 GB. Xóa được sau G1. Bằng chứng cần giữ đã chép vào `docs/evidence/perf-export/`.
- Commit `db0a139` mang tiền tố `wip:` vì được commit trước khi có kết quả cổng video thật. Không sửa lịch sử: commit sau ghi kết quả cổng. Người dùng đã quyết không squash commit này (2026-10-11).
- Banner FFmpeg có tên miền của bên build nằm trong lịch sử (`a90bbf4`, `db0a139`). Người dùng quyết giữ nguyên (2026-10-12). Không squash hay viết lại lịch sử.
- x264 cho kết quả tất định trên máy này ở chế độ dung lượng mặc định (VBV): mỗi bản render lại đều trùng byte với bản xuất thật của job 71 và 74, và baseline chạy 2 lần cũng trùng. Trên máy khác, hoặc với số luồng khác, SHA-256 có thể khác. Khi đó framemd5 vẫn là bằng chứng chính.
- Với code chưa sửa gblur (A2 và trước), cổng byte chỉ dùng được khi mọi vùng mờ cao ít nhất 2 × số luồng bộ lọc (26 dòng trên máy này). Nếu không, chính code đó cho ra kết quả khác nhau giữa các lần chạy. Các vùng của 4 bản đã cổng ở A2 đều cao từ 30 dòng, và `--baseline-twice` cho thấy kết quả ổn định. Bản tự kiểm tra 320×180 (vùng 8 và 15 dòng) đã fail đúng vì lỗi này, rồi được xóa. Từ bước 1 của phase 2, code đã sửa ổn định cả với vùng mỏng (`T71-thin60`).
