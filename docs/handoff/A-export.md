# Bàn giao A – Xuất video nhanh hơn
- Nhánh: `perf/export-speed` (worktree `temp\wt-export-speed`, tách từ `origin/main` `7d8b8bc`) · Commit cuối: (điền khi commit) · Cập nhật: 2026-10-11
- Chạy ở: PC · Trạng thái: đang làm (A0 xong phần T71, A1a xong, A2 đang qua cổng)

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
- Số thô nằm trong `docs/evidence/perf-export/`.
- Control Center thật đang tắt suốt lúc đo.

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
| `import biliflow.cli` (mỗi stage render một lần) | 32,0 lần đầu; 7,4 và 7,3 các lần sau | |

Kết luận cho T71:
- **Bộ lọc làm mờ chiếm khoảng 517 s trong 900 s render (57 %).** Đây là chỗ cần sửa trước (A2), sau đó là encoder (A3).
- **Kiểm tra sau xuất, hash và `+faststart` cộng lại khoảng 31 s (3 %).** A4 chưa đáng làm.
- **Bản xuất thật chậm hơn lần đo 30 %** (1.173,5 s so với 899,6 s), dù cùng code và cùng byte đầu ra. Phần chênh là do tải máy lúc xuất, không do code.
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
- Review chỉ đọc: không có CRITICAL, HIGH hay MEDIUM. Các điểm LOW đã sửa.

## Đang dở
- A2 (commit `wip:`): đặt `enable` cho delogo, gblur và geq trong `blur_filter.py`. Test đơn vị và cổng tổng hợp (6 ca, framemd5 và SHA-256 mp4 giống hệt) đã qua.
- Cổng trên video thật chưa có kết quả. Phiên dừng vì hết hạn mức. Nó có thể đã được khởi động nền bằng script `run-gates.ps1` trong scratchpad của phiên. Log ở `temp\perf-export\<tên>\gate-a2.log`, kết quả ở `temp\perf-export\<tên>\results\*-gate.json`. Nếu chưa có kết quả, chạy lại theo "Cách kiểm".

## Bước tiếp theo
1. Đọc hoặc chạy cổng A2 cho `smoke-synth`, `L39-1080-seg300` (`--baseline-twice`), `T74` và `T71` (`--baseline-mp4-sha256 437c60cc8dda369ef1baf1bc9222e8953cdf975cab72506df19e97994c3a83a5`). Baseline: `temp\perf-export\baseline-40a0125\src`. Candidate: `src` của worktree. Nếu cổng qua, đổi commit `wip:` thành commit A2 thật, chép `*-gate.json` vào `docs/evidence/perf-export/` và ghi số trước/sau.
2. Full suite, cập nhật `CHANGELOG.md`, `docs/PROJECT_STATUS.md`, `docs/SESSION_HANDOFF.md`, báo người dùng, chờ G1.

## Cách kiểm
- Lệnh test (trong worktree, PowerShell):
  `$env:PYTHONPATH="$PWD\src;$PWD"; $env:TEMP='E:\DungChung\BiliFlow\temp'; $env:TMP=$env:TEMP; $env:BILIFLOW_FFMPEG='E:\DungChung\BiliFlow\tools\ffmpeg\bin\ffmpeg.exe'; E:\DungChung\BiliFlow\.venv\Scripts\python.exe -m unittest tests.test_scheduler tests.test_control_center tests.test_blur_filter tests.test_final_renderer -v`
- Đo: `powershell -File scripts\benchmark-export-steps.ps1 -InstallRoot E:\DungChung\BiliFlow measure --name <tên> [--src <src>]`.
- Cổng A2: `... gate --name <tên> --baseline-src <src cũ> --candidate-src <src mới>`. Mã thoát 0 chỉ khi framemd5 của `[vout]`/`[aout]` và SHA-256 mp4 x264 giống hệt.

## Chờ người dùng
- Điểm gộp G1.

## Lưu ý cho người tiếp quản
- Không sửa: `cli.py`, `__main__.py`, `__init__.py`, `run.ps1`, `env.ps1`, `stage_cache.py`, `cache_dependencies.py`, `pyproject.toml`, `config/*`, `models\`. Các file nhánh này đã sửa nằm ngoài dấu vân tay cache quét: đã kiểm bằng `stage_source_paths` cho cả 10 stage có cache.
- `control_center.py` và `dashboard_v2/` cũng do session E sửa (mục 9.6: E trước, A sau). Lúc gộp G1, dòng Cài đặt ở `dashboard_v2/app.js` (`1 GPU · xuất trước, mỗi loại theo thứ tự bấm`) có thể đụng phần của E.
- Kế hoạch ghi "dùng `priority` có sẵn". Ở đây `priority` vẫn là khóa sắp xếp đầu tiên, còn "lệnh xuất trước" được tính lúc chọn, không ghi vào cột. Lý do: lệnh xuất bị tạm dừng, thử lại hay hủy thì tự về đúng loại, không cần sửa cột.
- Lỗi có từ trước, không do A1a (theo đọc code, chưa tái hiện): nếu Hủy rơi vào khoảng vài mili giây giữa `claim_queued` và lúc `_execute` ghi stage RUNNING, một lệnh xuất đã hủy vẫn có thể chạy xong. A1a không đổi khoảng này. Chưa sửa.
- Thư mục `temp\perf-export\` (bản sao video, kết quả thô) là dữ liệu tạm cục bộ. Xóa được sau G1. Bằng chứng cần giữ đã chép vào `docs/evidence/perf-export/`.
