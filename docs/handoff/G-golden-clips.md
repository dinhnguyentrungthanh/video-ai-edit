# Bàn giao G – Phim vàng thành clip (Golden v2)
- Nhánh: `feat/golden-clips` (từ `origin/main` ed39653) · Cập nhật: 2026-10-13
- Chạy ở: PC (worktree `temp\wt-golden-clips`) · Trạng thái: **chờ người dùng duyệt thiết kế (Bước 1)**

## Đã xong
- Bước 0: sao lưu `annotations\golden` thành `annotations\golden-backup-20261013.zip` (22 file, 283 685 byte,
  SHA-256 `831374b3e3e28b9c4f4a76a6810809b6206131deff51fc68d06d18c43c2a7c55`). Đã giải nén ra
  `temp\golden-backup-verify-20261013` và so SHA-256 từng file: khớp cả 22. Cũng đã kiểm `annotations\golden`
  không đổi trong lúc nén.
- Bước 1: đọc code và lập thiết kế bên dưới. Chưa sửa code, chưa cắt video.

## Đang dở
- (không có)

## Bước tiếp theo
1. Người dùng duyệt thiết kế, trả lời các câu hỏi ở mục 9 và văn bản AGENTS.md ở mục 7.
2. Bước 2 theo thiết kế đã duyệt.

## Cách kiểm
- Test: `.\.venv\Scripts\python.exe -m unittest tests.test_golden_set tests.test_golden_scoring tests.test_golden_evaluate tests.test_golden_prefill tests.test_golden_label_app tests.test_job_purge tests.test_job_delete -v`
- Cổng: mục 5.

## Chờ người dùng
- Duyệt thiết kế v2 (mục 1–8) và trả lời mục 9.

## Lưu ý cho người tiếp quản
- `annotations\` bị gitignore: nhãn ở đó là bản duy nhất. Không xóa, không ghi đè nhãn, `events.json` hay `label-history.jsonl`.
- Không xóa, di chuyển hay sửa video trong `input\`, `output\`, `archive\`. Không gọi route hay hàm xóa, lưu trữ.
  Control Center thật chỉ được đọc (`mode=ro`).
- Không sửa: `final_renderer.py`, `blur_filter.py`, `export_encoder.py`, `cli.py`, `stage_cache.py`,
  `cache_dependencies.py`, `job_pipeline.py`, module quét, `scheduler.py`, `hardware_*.py`, `models\`.
- Repo công khai: chỉ ghi số job và nhãn L-H-T, không tên phim, không tên file nguồn.

---

# Thiết kế Golden v2

## 1. Hiện trạng (đã kiểm trên code)

- **Số đoạn:** v1 có 13 đoạn (3 842 s), v1.1 thêm 6 đoạn (T5–T8, C21E, C20F; 1 492 s). Cả 19 đoạn đều
  "complete": v1 có 19 nhãn (revision 469), v1.1 có 36 nhãn (revision 121). Tổng cộng 5 332 s, khoảng 89 phút.
  v2 phải gồm **cả 19 đoạn**. Nếu chỉ lấy 13 đoạn, 36 nhãn của v1.1 sẽ mất nguồn.
- **Nguồn:** job 39 (L, 25 fps, keyframe đúng mỗi 1 s tại giây chẵn), job 38 và 37 (H, 23,976 fps,
  keyframe cố định mỗi 250 khung = 10,427 s). Cả ba đều H.264 + AAC trong MP4, start_time 0.
- **Cách chấm** (`evaluate_golden.py run`): mỗi phim chạy **cả phim** qua `benchmark_full_advertising.py`
  (profile careful, fast_scan, đủ 4 nhóm). Sau đó `golden_scoring.score` cắt các mục duyệt theo khung thời
  gian của từng đoạn, tính theo **giây của phim gốc**. Lý do đã ghi ở QUALITY_PLAN §5 và §11: quét riêng một
  đoạn cho kết quả khác production.
- **Cách lấy mẫu khung:** các scanner chạy bộ lọc `fps=` của ffmpeg từ đầu file và gán
  `timestamp = frame_index / sample_fps`. Lưới lấy mẫu vì thế **neo ở giây 0 của file**. Các chu kỳ của profile
  careful là 0,125 / 0,25 / 0,5 / 2 / 3 s (BSCNN là 6 s), cộng thêm "bucket" phủ logo 300 s cũng neo ở giây 0.
- **Ngữ cảnh cả phim** mà một clip không giữ được:
  - cửa sổ biên 30 s ở đầu và cuối video (`logo_boundary_seconds`, lấy mẫu dày 0,25 s);
  - thẻ "5 giây đầu" và opening_promotion (bắt đầu trước giây 30);
  - thẻ đoạn kết 6 s;
  - trần 420 cửa sổ logo cho mỗi phim và bucket 300 s;
  - top-K = 20 ứng viên an toàn cho mỗi lần quét;
  - watermark kéo dài suốt phim.

## 2. Kết luận chính: điều gì giữ được, điều gì không

- **Giữ được, bit-exact:** mọi khung giải mã của từng đoạn (cổng a). Cắt bằng stream copy từ keyframe thì
  khung giải mã từ clip giống hệt khung giải mã từ phim gốc.
- **L (job 39): giữ được cả lưới lấy mẫu.** Gốc clip đặt tại bội của 6 s. Vì keyframe nằm ở mọi giây chẵn
  nên luôn chọn được, và mọi scanner lấy đúng các khung như khi chạy cả phim, cùng mốc giờ.
- **H (job 38, 37): không giữ được lưới, trừ clip bắt đầu ở giây 0** (C20A+C20B, C21A). Một keyframe giữ đúng
  mọi lưới phải nằm ở bội của 6 006 s, hoặc 300 300 s nếu tính cả bucket. Với các clip khác, điểm lấy mẫu lệch
  dưới một chu kỳ, nên một phần khung lấy mẫu là khung kề bên (cách 41,7 ms). Muốn khác đi thì phải sửa scanner
  để nhận gốc thời gian, mà scanner nằm trong danh sách không được sửa, hoặc phải mã hóa lại (khung không còn
  giống hệt).
- **Vì vậy cổng (b) "cùng điểm khi chạy detector trên clip" không bảo đảm được** (ngữ cảnh cả phim ở mục 1,
  cộng thêm lệch lưới của H). Đề xuất tách (b) thành b1 và b2 (mục 5).

## 3. Cách cắt

- **Stream copy** (`-c copy`, không mã hóa lại), mỗi clip bắt đầu ở một keyframe IDR. Script kiểm keyframe
  bằng ffprobe và từ chối nếu không đúng keyframe.
- **Lề:**
  - trước đoạn ≥ 35 s (cửa sổ biên 30 s + thẻ 5 s), lùi tới keyframe;
  - sau đoạn ≥ 36 s (cửa sổ biên 30 s + thẻ kết 6 s), hoặc tới hết phim;
  - đoạn bắt đầu ở 0 hoặc kết thúc ở cuối phim thì giữ đúng mép phim, nên ngữ cảnh mở đầu và kết thúc như phim gốc;
  - hai đoạn có clip chồng hoặc gần nhau thì gộp vào một clip (T1+T2, C20A+C20B).
- **Gốc clip:**
  - L: bội lớn nhất của 6 s ≤ (đầu đoạn − 35 s);
  - H: keyframe lớn nhất ≤ (đầu đoạn − 35 s);
  - ghi `origin_seconds` chính xác theo pts của keyframe (time base gốc) và độ lệch lưới đạt được.
- **Âm thanh:** giữ AAC (copy) để trang gán nhãn phát có tiếng, nhưng bắt buộc video start_time = format
  start_time = 0 (ffprobe). Nếu không đạt thì bỏ âm thanh, vì ffmpeg trừ start_time sớm nhất và sẽ làm lệch lưới.
- **Kết quả dự kiến: 17 clip, khoảng 3,1 GB** (ước theo bitrate trung bình, ±20 %), thay cho 11,3 GB phim gốc,
  tức giải phóng khoảng 8 GB.

| Job | Clip (đoạn) | Gốc → hết (s) | Ước MB |
| --- | --- | --- | --- |
| 39 L | T1+T2 · T5 · T3 · T7 · T6 · T8 · T4 | 0→636 · 816→1146 · 1962→2336 · 3216→3599 · 6744→7056 · 8562→8836 · 11364→hết | ≈1 750 |
| 38 H | C20A+C20B · C20C · C20D · C20F · C20E | 0→836 · 1261,7→1636 · 3263,7→3636 · 4483,6→4801 · 6360,5→hết | ≈750 |
| 37 H | C21A · C21B · C21E · C21C · C21D | 0→276 · 1261,7→1636 · 2106,3→2436 · 4160,4→4536 · 6412,7→hết | ≈550 |

## 4. Nơi để và manifest v2

- **Khi làm (trước khi gộp):** dựng ở `annotations\golden-v2-staging\` (vẫn gitignore). Glob
  `annotations/golden/*/segments.json` không thấy thư mục này, nên Control Center thật đang chạy code cũ không
  bị ảnh hưởng (code cũ đọc nguồn không có job_id thành "không đọc được" và khóa mọi lệnh xóa).
- **Sau khi gộp và khởi động lại Control Center, khi người dùng bảo:**
  - đổi tên thư mục staging thành `annotations\golden\v2\` (clip ở `media\`; không bao giờ ở `input\`, vì
    watcher sẽ tạo job);
  - chuyển `v1` và `v1.1` sang `annotations\golden-retired\`.
  - Cả hai là phép đổi tên trên cùng ổ. Script kiểm SHA-256 từng file trước và sau, có sao lưu zip mới.
- **`segments.json` v2:**
  - `golden_set: "v2"`, không `extends`. `derived_from` ghi, cho v1 và v1.1: pin manifest, revision nhãn,
    SHA-256 `events.json` và dấu vân nhãn.
  - Mỗi nguồn là một clip:
    - `kind: "clip"`, `path`, `sha256`, `size_bytes`, `duration_seconds`, `fps`, `width`, `height`, `content_style`;
    - `origin`: `job_id`, `sha256` phim gốc, `origin_seconds`, `origin_pts` và `time_base`, `grid_offsets`
      (độ lệch theo từng chu kỳ lấy mẫu), `keyframe` true.
  - Khóa nguồn và tên file trung tính, ví dụ `j39-0816` / `j39-0816.mp4`: không tên phim, không tên file gốc.
  - Đoạn giữ nguyên ID (T1…, C20A…), nên nhãn và lịch sử cũ vẫn tra ngược được. Giờ đoạn tính theo clip
    (= giờ gốc − origin).
- **`events.json` v2:** hợp các nhãn của v1 r469 và v1.1 r121.
  - Giữ ID nhãn, vùng (cùng độ phân giải), hành động và mức độ. Giờ dời theo origin, cắt về phạm vi clip
    (phần trong đoạn không đổi).
  - `suggestion_resolutions`, `watermark_decisions`, `retired_event_ids` và `reopened_suggestions` được chép;
    giờ trong ảnh chụp gợi ý cũng dời theo.
  - `label-history.jsonl` của v2 mở đầu bằng một dòng "migrate" ghi pin nguồn.
  - Tập gợi ý "dense_logo_window" (vùng thô, không chấm chất lượng vùng) ghi vào manifest v2, không phụ thuộc
    `reports\`.
- **v1 và v1.1 giữ nguyên từng byte.** Code vẫn đọc được chúng (kể cả ở `golden-retired`), nhưng không bao giờ
  chấm chung với v2.

## 5. Cổng

- **(a) Khung bit-exact, CPU, khoảng 15 phút, chạy được bất cứ lúc nào (chỉ đọc phim gốc):**
  - với mỗi clip, `framemd5` mọi khung giải mã từ clip (`-fps_mode passthrough`), so với `framemd5` phim gốc
    giải mã cùng khoảng (`-copyts`);
  - đòi: cùng số khung; pts clip + origin_pts = pts gốc (đúng từng tick); md5 giống hệt trên toàn bộ đoạn (nên
    cả lề);
  - thêm: origin là bội của mọi chu kỳ lấy mẫu với L, và với clip H bắt đầu ở 0; độ lệch lưới còn lại của các
    clip H khác được ghi ra.
- **(b1) Cùng điểm trên cùng đầu ra detector, CPU, vài giây, phải giống hệt:**
  - lấy các review queue cả phim của bảng điểm gốc v1+v1.1 có sẵn trong `reports\benchmarks`;
  - dời từng mục sang giờ clip (cả `detected_intervals` của thẻ cảnh), cắt theo clip, gắn SHA-256 clip;
  - chấm v2;
  - so với thẻ v1+v1.1 trên từng nhãn (trạng thái, độ phủ, vùng, đồng ý gợi ý, bẫy) và từng chỉ số. Phải giống hệt.
  - Cổng này chứng minh manifest, nhãn đã dời giờ và code đọc v2 đúng.
- **(b2) Mốc chuẩn mới trên clip, GPU, chỉ khi Control Center thật không chạy job nào, kể cả xuất (kiểm
  bằng `mode=ro`):**
  - `evaluate_golden.py run --set v2`: 17 lượt harness trên 17 clip. Ước khoảng 1 giờ: thời gian quét khoảng
    22 % cả phim, cộng tải model cho mỗi clip;
  - kèm một lượt **cả phim lần cuối** trên cùng code (khoảng 1 giờ) để có thẻ so sánh cùng code. Sau khi xóa
    phim gốc thì không chạy lại được nữa;
  - báo cáo khác biệt từng nhãn giữa clip và cả phim, kèm nguyên nhân (biên, lệch lưới H, top-K, watermark).
    **Người dùng chấp nhận** thì thẻ clip thành mốc chuẩn v2;
  - từ đó mọi cổng detector, tốc độ và E4 so v2 với v2.

## 6. Gỡ khóa job 37–39

- `job_purge.GoldenIndex` chỉ đọc `annotations/golden/*/segments.json`. Khi v1 và v1.1 đã sang
  `golden-retired`, job 37–39 tự hết khóa.
- `_read_golden` phải nhận nguồn `kind: "clip"`: chỉ khóa theo SHA-256 của clip, bỏ qua `origin`, không đòi
  `job_id`. Thiếu bước này thì v2 bị đọc thành "không đọc được" và **khóa mọi lệnh xóa**.
- Thứ tự:
  1. code mới (đọc v2, `_read_golden`) gộp vào main và Control Center khởi động lại;
  2. kích hoạt thư mục (mục 4);
  3. kiểm bằng GET chỉ đọc rằng job 37–39 không còn `protected`.
- Người dùng tự xóa hoặc lưu trữ phim gốc.

## 7. Văn bản đề xuất cho AGENTS.md (chờ duyệt)

Thay câu `Jobs of a golden set (...) are never deleted, and nothing is deleted while such a file cannot be read.` bằng:

> Jobs of an active golden set (`annotations\golden\*\segments.json`, by job id or SHA-256) are never deleted, and nothing is deleted while such a file cannot be read. A golden source that is a clip (golden set v2) names no job and protects only its own SHA-256; the retired sets in `annotations\golden-retired\` protect nothing.

Thêm mục mới sau mục "Do not silently delete `reports`, `state`…":

> - Golden set (`docs/QUALITY_PLAN.md`, `docs/handoff/G-golden-clips.md`). Since <ngày kích hoạt> the active set is v2: stream-copied clips of the 19 labelled segments of jobs 37, 38 and 39, kept with their labels in `annotations\golden\v2\` (clips in `media\`, never in `input\`, where the watcher would make a job of them). Times are relative to each clip; the manifest records each clip's origin in its original film. v1 and v1.1 were moved unchanged to `annotations\golden-retired\` after v2 passed its gates (bit-identical decoded frames of every segment; identical scores for the same detector output). `annotations\` is not in Git and holds the only copy of the labels: agents never delete, move, rewrite or overwrite anything in `annotations\golden*` (labels, `events.json`, `label-history.jsonl`, backups, clips) except through the label page or a user-approved migration that first writes a dated, verified backup zip in `annotations\`. Golden clips and `annotations\golden*` are never packaged in an installer, build or release. Detector gates on the golden set compare v2 runs with v2 runs; whole-film behaviour (film-long watermarks, the 30 s opening and ending windows, per-film caps) is not covered by v2. Whether the original films of jobs 37–39 are deleted or archived is the user's own decision in the Control Center.

## 8. Script, benchmark và cổng đang cần phim gốc

| Nơi | Cần gì | Chuyển sang clip |
| --- | --- | --- |
| `evaluate_golden.py run` | chạy cả phim job 39/38/37 (`RUNS`) | lặp theo nguồn clip của manifest; mặc định chỉ chấm bộ đang hoạt động (v2) |
| `evaluate_golden.py` `_frames` | trích ảnh từ nguồn | dùng `source_file` của v2 (clip) |
| `benchmark_full_advertising.py` | job thật 37–39 trong DB (`AUTHORIZED_JOBS`) | thêm `--golden-clip <khóa>`: job thử trỏ vào clip đã kiểm SHA-256; giữ chế độ job cho job khác |
| `golden_prefill.py` | queue và nguồn của job 37–39 | gợi ý v2 lấy từ queue các lượt clip (b2); lệnh `segments` của v1/v1.1 không chạy lại |
| `golden_label_app.py`, `golden_label_server.py` | phát video nguồn | phát clip v2 (manifest tự trỏ) |
| `golden_audit_corrections.py` | một bộ nhãn | thêm v2 vào `KNOWN_SETS`, đường dẫn gợi ý theo bộ |
| `benchmark_live_safety.py`, `benchmark_exact_speedups.py`, `benchmark_animation_safety.py` | cửa sổ 150–300 s trên phim gốc, cộng lượt cả phim cũ | cửa sổ nằm trong clip thì ánh xạ (job 39 400 s, job 38 0 s và 3300 s, job 37 0 s); job 39 5400 s **nằm ngoài mọi clip** (mục 9); lượt cả phim cũ chỉ còn là bằng chứng lịch sử |
| `benchmark_adult_triage.py` | chỉ queue đã lưu | không cần phim gốc |
| Cổng E4 "Golden gate of L, H, T" (`hardware_table.py`, PERF_INSTALL_PLAN §4) và cổng "danh sách duyệt giống hệt trên bộ Golden" (fp16, CPU fp32, OpenVINO) | so hai cấu hình trên cùng đầu vào | chạy trên clip v2: rẻ hơn khoảng 4 lần. Chỉ sửa chữ trong docs; không sửa `hardware_*.py` |
| Cổng detector/tốc độ QUALITY_PLAN §7 (`compare --gate`) | thẻ mốc cả phim | mốc chuẩn v2 (b2); cổng +5 % thời gian đo trên clip |
| `job_purge`, `job_delete`, `source_cleanup`, `control_center` | khóa theo GoldenIndex | mục 6 |

**Bộ cài:** không đóng gói `annotations\golden*` và clip. Nhánh bộ cài (C) chưa có script đóng gói. Ghi vào
`docs/handoff/C-installer.md` lúc gộp: `annotations\` nằm ngoài payload (danh sách cho phép), trừ khi người dùng
quyết riêng `text_semantics_seed_v1.json`. Thêm một test chặn đường dẫn `annotations/golden` khi có danh sách
payload.

## 9. Câu hỏi cho người dùng

1. v2 gồm cả 19 đoạn (v1 + v1.1), không phải 13 đoạn: đồng ý?
2. Cổng (b): chấp nhận tách thành b1 (giống hệt, bắt buộc) và b2 (mốc chuẩn mới, người dùng duyệt khác biệt)?
3. Chạy một lượt cả phim lần cuối trên code hiện tại (khoảng 1 giờ GPU), trước khi gỡ khóa?
4. Cửa sổ job 39 ở 5400 s (dùng bởi 2 benchmark cũ) không nằm trong đoạn nào: thêm một clip phụ 150 s (khoảng
   100 MB, không phải đoạn chấm điểm) hay bỏ?
5. Dựng ở `annotations\golden-v2-staging\` rồi mới đổi tên sang `annotations\golden\v2\` khi kích hoạt: đồng ý?
