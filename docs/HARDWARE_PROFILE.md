# Hồ sơ phần cứng (Session E): hợp đồng E0

- **Trạng thái:** E0 **đã được người dùng duyệt** ngày 2026-10-11, cùng câu trả lời cho năm câu hỏi (mục 10). E1 và E2 làm theo hợp đồng này.
- Ngày: 2026-10-11. Nhánh `feat/hardware-profile`, base `origin/main` `40a0125`.
- Nguồn: `docs/PERF_INSTALL_PLAN.md` mục 1–4, 9, 13 và bản đồ code đọc trên `40a0125`. Mọi trích dẫn `path:line` tính theo worktree.
- Bản này đã qua một lượt review đối kháng theo bốn góc (trích dẫn, an toàn, dòng lệnh giống từng byte, khả năng cài đặt), mỗi phát hiện đều có bước kiểm chứng. Các lỗi đã xác nhận được sửa vào thân bài.
- Chỉ dùng nhãn L, H, T. Không ghi tên phim, tên file, link hay tên miền thật, kể cả chuỗi phiên bản đầy đủ của FFmpeg (chuỗi đó chứa tên miền của nơi build).

## 0. Tóm tắt

1. **E0–E2 chỉ thêm một thư viện, một phép đo có khóa, các route chỉ-PC và một khung trên trang Cài đặt.** Trước Đợt 2, không lựa chọn nào của hồ sơ tới được stage quét:
   - các file biến lựa chọn thành dòng lệnh nằm trong danh sách cấm sửa (`job_pipeline.py:189-220`, `scheduler.py:206-254`, `:568-581`);
   - trang Cài đặt ghi rõ "Chưa áp dụng cho job – có hiệu lực sau Đợt 2".
2. **Không khóa cache quét nào đổi.** Các module mới không nằm trong closure của stage nào (`cache_dependencies.py:9-41`), và E0–E2 không sửa đầu vào chung nào của khóa (`stage_cache.py:42-99`).
3. **Hai việc: `scan` và `export`.** Mỗi job chọn một **họ** trong ba họ `standard`, `standard_ocr8`, `fast`.
   - Mỗi họ là một tổ hợp đầy đủ đã qua cổng.
   - Trên PC, cả ba họ tái tạo **từng byte** dòng lệnh hôm nay khi `logo_decode` là `nvdec`.
   - Giải mã `cpu` là một dòng lệnh mới của stage text, nên cache text tính lại. Nó xảy ra khi cặp driver và FFmpeg chưa được kiểm (mục 5.5, câu hỏi 1).
4. **Bảng cấu hình nằm trong code** (`src/biliflow/hardware_table.py`), không nằm trong `config/*`.
   - Hôm nay chỉ có một dòng GPU đã qua cổng: Turing (compute capability 7.5) + torch `2.14.0+cu126`.
   - Dòng CPU fp32 chờ E3/E4.
   - Dòng chưa qua cổng chỉ chạy trên root thử (câu hỏi 2: từ chối trên thư mục cài thật).
5. **Không bao giờ âm thầm chạy CPU.** Chỉ chạy CPU trong hai trường hợp:
   - người dùng bật "Chỉ dùng CPU";
   - máy chưa từng có CUDA. Cờ `had_cuda` chỉ đi một chiều: đã đúng một lần thì mọi lần ghi sau đều giữ đúng. Ngoài ra, danh sách card đọc được phải không có thiết bị nào mang mã hãng PCI `10DE` của NVIDIA.

   Mọi lỗi CUDA khác đều từ chối job mới, kèm lý do bằng tiếng Việt.
6. **`hw:{id}` chỉ được ghi từ Đợt 2**, trong cùng commit thêm `"hw"` vào `JOB_SETTING_NAMES` (`job_store.py:148`). Nếu ghi sớm hơn:
   - `tests/test_job_purge.py:640-649` báo lỗi;
   - "Xóa video" bỏ sót dòng `hw:{id}`.

   E0–E2 chỉ giao các hàm thuần để dựng và đọc giá trị đó.
7. **Mỗi file trạng thái chỉ có một tiến trình ghi,** và mọi lần ghi đều thay file nguyên khối (có fsync).
8. **"Đo lại" chỉ chạy khi hàng đợi đã tạm dừng và worker rảnh.**
   - Nó chạy trong tiến trình con và lấy khóa theo thứ tự cố định, trên luồng chính. Mỗi lần lấy khóa chỉ thử trong thời gian ngắn, không bao giờ chờ lâu trong khi đang giữ một khóa khác.
   - Có giới hạn thời gian cứng và dùng clip tổng hợp, không dùng video của người dùng.
   - Lệnh đo chỉ nhận đúng thư mục cài của bản code đang chạy, hoặc một thư mục trong `temp\` của nó (`job_purge.allowed_project_root`). Nhờ vậy code trong worktree không bao giờ ghi được `state\` thật.
   - Route chỉ-PC (127.0.0.1 + token), không thêm vào `/api/status`.

## 1. Phạm vi E0–E2

| Bước | Giao | Không làm |
|---|---|---|
| **E0** | Tài liệu này và file bàn giao `docs/handoff/E-hardware.md` | Code |
| **E1** | `hardware_profile.py` (schema, đọc/kiểm, `resolve`/`choose`, `speed_arguments`, `LEGACY_KNOBS`, hàm đóng băng thuần), `hardware_table.py`, `hardware_detect.py` (nhận diện và `observe()`, có lớp giao diện để test giả) cùng test | Gọi `choose()` từ scheduler, job_pipeline hay job_store; ghi `hw:{id}` |
| **E2** | `hardware_measure.py` (kiểm tham số, khóa, kiểm rảnh, clip, phép thử, lịch sử số đo, hàm ngưỡng trễ, ghi hồ sơ), `hardware_manager.py` (phía Control Center), `scripts/hardware-measure.ps1`, route, khung Cài đặt, test | Sửa `/api/status`; đổi dòng lệnh stage; sửa `AGENTS.md` (việc của C0) |
| **Đợt 2 (sau G1)** | `freeze_for_job` ghi `hw:{id}`, `JOB_SETTING_NAMES += ("hw",)`, `job_pipeline` dùng `speed_arguments`, E3 sửa đường CPU | — |

## 2. Việc (task) và núm chỉnh (knob)

### 2.1 Hai việc

- **`choose("scan", request=…, observed=…)`** trả về `ScanChoice`: thiết bị, họ và **toàn bộ** núm của mọi stage quét. `ScanChoice.stage(name)` trả về `StageKnobs` của từng stage trong 10 stage GPU (`job_pipeline.py:230-397`).
  - Một job revision chỉ có một `ScanChoice`, nên không thể trộn thiết bị giữa các stage (`docs/PERF_INSTALL_PLAN.md:151`).
- **`choose("export")`** trả về `ExportChoice`: encoder, preset, CRF, chặn dung lượng, âm thanh và cách kiểm tra sau xuất.
  - Bản 1 luôn là bản xuất đang dùng (`final_renderer.py:592-612`, `:642-656`), **không bao giờ từ chối**, và chỉ mang cảnh báo của trạng thái.
  - Đổi encoder là việc của A3 và phải được người dùng duyệt (`docs/PERF_INSTALL_PLAN.md:148`, `:270-276`).
- **Thêm sau, tăng phiên bản:** `lanes` cho A1b (VRAM dư khi NVENC chạy cùng Qwen2-VL) và `install.torch` cho C5 (chọn bản torch theo driver). Trường mới cần `profile_version` 2 và `hw` bản 2 (mục 3.4).

### 2.2 Núm của `scan` (giá trị hôm nay; dòng lệnh nằm trong khóa cache)

| Núm | Giá trị | Dòng lệnh hôm nay | Stage |
|---|---|---|---|
| `device` | `cuda`, `cpu` | `--device cuda`, 11 chỗ (`job_pipeline.py:238,267,282,299,313,327,339,352,372,384,392`) | 10 stage GPU |
| `text.detect_precision` | `fp32`, `fp16` | `--detect-precision fp16` (`:206`) | text |
| `text.recognition_batch_size` | `1`, `8` (`:164-167`) | `--recognition-batch-size` (`:194`, `:214-216`) | text |
| `text.recognition_frame_window` | `1`, `4` | `--recognition-frame-window 4` (`:195`) | text |
| `text.prewarm_logo_routing` | bool | `--prewarm-logo-routing` cùng các tham số routing (`:196-205`) | text |
| `text.logo_routing_workers` | `1`, `3` | `--logo-routing-workers 3` (`:203`) | text (tiến trình con khởi động routing) |
| `text.logo_decode` | `cpu`, `nvdec` | `--logo-decode nvdec` (`:204`) | text (tiến trình con khởi động routing) |
| `visual_logo.routing_workers` | `1`, `3` | `--routing-workers 3` (`:209`) | visual_logo |
| `animation_safety.precision` | `fp32`, `fp16` | `--precision fp16` (`:211`) | animation_safety |
| `live_safety.violence_precision` | `fp32`, `fp16` | `--violence-precision fp16` (`:212`) | live_safety |

**Ràng buộc** (`ScanKnobs.__post_init__`). Lỗi phải hiện ra lúc chọn, không phải lúc stage khởi động.

*Chép lại chỗ code đang từ chối:*
- fp16 (detect, anime, bạo lực) thì device cuda (`textscan.py:542-543`, `animation_safety_scanner.py:167-168`, `live_safety_scanner.py:92-93`);
- batch > 1 thì device cuda; window > 1 thì batch > 1 (`textscan.py:546-554`).

*Quy tắc mới.* Code hôm nay không chặn các trường hợp dưới đây: CLI nhận `--device cpu` cùng `--prewarm-logo-routing --logo-decode nvdec` (`cli.py:535-542`). Lý do của từng quy tắc:
- `device == "cpu"` thì không khởi động routing và decode cpu.
  - `--device cpu` không lấy khóa GPU (`scripts/run.ps1:15-27`), còn tiến trình con khởi động routing không đi qua `run.ps1` (`routing_prewarm.py:29-41`). NVDEC ở đó sẽ dùng GPU mà không giữ khóa.
  - Trên CPU, hai bộ giải mã chạy song song làm OCR chậm thêm 18–35% (`docs/SCAN_PERFORMANCE.md:99-103`).
- `logo_decode == "nvdec"` thì device cuda và có khởi động routing.
- Mỗi `ScanKnobs` phải đúng **một trong ba dạng**. "Dạng" là hình của dòng lệnh, khác với "họ":

  | Dạng | Giá trị cố định | Được chọn |
  |---|---|---|
  | `prewarm` | cuda; batch 8; window 4; khởi động routing; `text.logo_routing_workers` 3 và `visual_logo.routing_workers` 3; detect, anime, bạo lực đều fp16 | `logo_decode` ∈ {`nvdec`, `cpu`} |
  | `plain` | cuda; window 1; không khởi động routing; `text.logo_routing_workers` 1 và `visual_logo.routing_workers` 1 (không ghi cờ); `logo_decode` cpu (không ghi cờ); mọi precision fp32 | batch ∈ {1, 8} |
  | `cpu` | như `plain`, với batch 1 và device cpu | — |

- **Số worker hai bên phải bằng nhau** vì job_pipeline viết một hằng số cho cả hai lệnh, và test ghim điều đó (`tests/test_job_ocr_option.py:153-167`).
  - Hai giá trị nằm trong khóa cache **stage** của text và logo, vì chúng nằm trên dòng lệnh.
  - Chúng **không** nằm trong khóa cache routing, và cách giải mã cũng không (`visual_logo_scanner.py:1588-1603`).
- **Dạng `prewarm` với `logo_decode = cpu` là dòng lệnh mới:** `--logo-decode cpu` nằm đúng chỗ của `nvdec`, và cache stage text tính lại. Nó chỉ tới stage từ Đợt 2.
- Mọi dạng khác đều bị từ chối cho tới khi một phiên bản sau thêm nó, ví dụ "nhanh trước G1" với detect fp32.

**Quy tắc viết dòng lệnh** (chuẩn). E1 giao hàm thuần, Đợt 2 dùng nó.
- `speed_arguments(knobs, *, config, source_sha256) -> SpeedArguments(text, visual_logo, animation_safety, live_safety)` trả về đúng bốn bộ mà `job_pipeline.py:189-220` dựng hôm nay.
- job_pipeline chèn mỗi bộ ngay sau `"--device", <device>` của stage tương ứng (`:238-239`, `:299`, `:352-353`, `:372-373`).

Nội dung từng bộ:
- **`prewarm`, text:**
  - `--recognition-batch-size 8 --recognition-frame-window 4 --prewarm-logo-routing`;
  - sáu cặp `--logo-*` lấy từ `config`, đúng thứ tự `:197-202`;
  - `--logo-routing-workers 3 --logo-decode <d>`;
  - `--logo-source-sha256 <s>`, chỉ khi có;
  - `--detect-precision fp16`.

  **`--logo-decode` luôn được ghi rõ, kể cả khi là `cpu`.** Mặc định của CLI là `nvdec` (`cli.py:290`): bỏ cờ thì vẫn chạy NVDEC (`cli.py:537`, `routing_prewarm.py:25`).
- **`prewarm`, các stage khác:** logo `--routing-workers 3`; anime `--precision fp16`; bạo lực `--violence-precision fp16`.
- **`plain` và `cpu`:** text chỉ có `--recognition-batch-size b`, và chỉ khi b ≠ 1. Ba bộ còn lại rỗng.
- **`device`** thay chữ `"cuda"` ở đúng 11 chỗ hiện có.

**`StageKnobs` của từng stage:**

| Stage | Núm |
|---|---|
| `text` | device + sáu núm `text.*` |
| `visual_logo` | device + `routing_workers` |
| `animation_safety` | device + `precision` |
| `live_safety` | device + `violence_precision` |
| `adult`, `verify_adult`, `gore`, `violence`, `confirm_violence`, `localize_logo` | chỉ device. `scan-content` không có tùy chọn precision (`cli.py:87-111`). |

**Cố ý để ngoài hồ sơ** (vẫn là hằng số trong code):
- batch size mặc định của CLI (`cli.py:67,97,137,219,421`) và độ sâu prefetch (`scanner.py:27`, `animation_safety_scanner.py:41`, `live_safety_scanner.py:31`);
- `--decode` riêng của stage text và logo, luôn là cpu (`cli.py:195-199`, `:291-295`);
- dtype nạp model trên CUDA, luôn fp16:
  - Florence (`scripts/localize_visual_logo_report.py:215`);
  - Qwen2-VL của stage logo (`visual_logo_scanner.py:1789`) và của bước xác nhận bạo lực (`vlm_confirmation.py:177`);
  - text-semantics (`text_semantics.py:61-62`);
  - GroundingDINO (`ad_candidate_pipeline.py:389`).

Các giá trị này không nằm trên dòng lệnh. Muốn đưa chúng vào hồ sơ phải sửa `cli.py`, tức là việc của B1, và chưa ai đo xem kết quả có giữ nguyên không. E3 và B2 thêm núm mới bằng cách tăng phiên bản (mục 4.4).

### 2.3 Ba họ và yêu cầu của người dùng

Yêu cầu của từng job vẫn lưu như hôm nay: `fast_scan:{id}` và `ocr_batch_size:{id}` (`scheduler.py:200-204`).

| Yêu cầu | Họ | Dạng trên dòng 1 |
|---|---|---|
| "Tăng tốc xử lý" bật | `fast` | `prewarm` |
| tắt, OCR 8 | `standard_ocr8` | `plain`, batch 8 |
| tắt, OCR 1 | `standard` | `plain`, batch 1 |

- **Hồ sơ chỉ quyết định họ nào được phép,** không tự đổi ý người dùng.
- **Hạ họ:** thứ tự là `fast` > `standard_ocr8` > `standard`. Nếu máy không có họ được yêu cầu, job nhận họ cao nhất mà máy có, không vượt quá họ được yêu cầu, và mang cảnh báo `family_capped`. Ví dụ: máy CPU chỉ có `standard`.
- Mặc định cho job mới vẫn là `fast` (`job_pipeline.py:129`) trên máy có họ đó.
- **Từ Đợt 2:** trên trang xếp hàng, ô "Tăng tốc xử lý" bị khóa kèm lý do. Điện thoại cũng xếp và chạy lại được job (`phone_access.py:159`), nên phản hồi của `start` và `rerun` mang `{family, requested_family, warnings}` hoặc lời từ chối. Đợt 2 quyết định có thêm bản tóm tắt an toàn cho điện thoại hay không; nếu có, đó là một ngoại lệ có chủ ý của I8.

## 3. File trạng thái và schema

### 3.1 Các file

Mỗi thư mục gốc dự án có bộ file riêng, đều nằm trong `state\` (đã gitignore). Control Center thử chạy trên root tạm thì có hồ sơ riêng, không đụng tới bản thật.

| File | Chỉ ai ghi | Nội dung |
|---|---|---|
| `state\hardware-profile.json` | tiến trình đo | lần đo mới nhất, thành công hay thất bại |
| `state\hardware-profile.last-good.json` | tiến trình đo, ghi sau file chính và chỉ khi đo thành công | lần đo tốt gần nhất |
| `state\hardware-override.json` | Control Center (route, dưới khóa của manager) | công tắc "Chỉ dùng CPU" |

- **Vì sao công tắc nằm ở file riêng:**
  - nếu nằm chung với hồ sơ, "Đo lại" ghi đè hồ sơ có thể chạy đua với lúc Control Center ghi công tắc;
  - nếu để trong database, `choose()` không còn là "chỉ đọc file" (`docs/PERF_INSTALL_PLAN.md:154`), và `setting()` trả về giá trị null đã lưu thay vì giá trị mặc định (`job_store.py:398-403`).
- **Cách ghi:**
  - tạo file `.tmp` tên ngẫu nhiên bằng `O_EXCL` trong cùng thư mục, ghi, `fsync`;
  - rồi `os.replace`, thử lại khi gặp `PermissionError`;
  - chép theo `download_account_vault.py:151-159`, `:384-397`, vì chưa có hàm chung (việc của D).
- Người đọc bỏ qua `*.tmp`. Không bao giờ dùng kiểu "đọc lỗi thì trả `{}`" như `download_transfer.py:164-169`.

### 3.2 `hardware-profile.json`, `profile_version` 1

Mọi khóa đều bắt buộc. Khóa lạ làm file không hợp lệ.

| Khóa | Kiểu và giá trị cho phép |
|---|---|
| `schema` | đúng `"biliflow.hardware-profile"` |
| `profile_version` | `int` (không nhận bool), thuộc `READABLE_PROFILE_VERSIONS = {1}` |
| `content_sha256` | 64 hex chữ thường: SHA-256 của JSON chuẩn hóa (`sort_keys`, `separators=(",",":")`, `ensure_ascii=False`, UTF-8) của toàn bộ file trừ khóa này |
| `written_at` | ISO-8601 có múi giờ |
| `writer` | `{biliflow_version, mode: "detect"\|"measure", launched_by: "control-center"\|"script"\|"installer"}`. E2 chỉ ghi `measure`; `detect` và `installer` để dành cho C5, để C5 không phải tăng phiên bản |
| `attempt` | `{result: "ok"\|"failed", failure: null \| {code, step, message≤300}, started_at, finished_at, budget_seconds}`. Danh sách `code` và ý nghĩa ở mục 7.2 |
| `fingerprint` | `{sha256, gate_key, info}` (mục 3.3) |
| `detection.nvml` | `{status: "ok"\|"no_library"\|"error", error}`. Thiếu `nvml.dll` cho `no_library`, nghĩa là **không biết**, không phải "0 GPU" |
| `detection.nvidia_smi` | `{ok, driver_version, gpu_count, error}` hoặc null. Chỉ để đối chiếu với NVML (kế hoạch `:191`); không quyết định gì |
| `detection.torch_cuda` | `{ok, device_count, device_name, capability, arch_list[], total_memory_bytes, fp16_ok, seconds, error}`, lấy từ tiến trình cháu chạy torch. `arch_list` lấy từ `torch.cuda.get_arch_list()` |
| `detection.ffmpeg` | `{ok, error, path, version, sha256, size, mtime_ns, hwaccels_listed[], encoders: {<tên>: {listed, trial: "ok"\|"failed"\|"not_run", error}}, nvdec: {listed, trial, framemd5_equal_cpu, chain}}`. `path` là đường dẫn tuyệt đối của `ffmpeg.exe` đã đo. `version` chỉ giữ phần số đầu chuỗi (ví dụ `9.0.1`) |
| `detection.cpu` | `{model, vendor, physical_cores, logical_cores, features: {avx2, avx512f}}`. `model` đọc từ registry `ProcessorNameString`, cùng nguồn với quan sát nhẹ |
| `detection.ram_bytes`, `detection.disk` | RAM tổng; `{root_total_bytes, root_free_bytes}` |
| `detection.power` | `{battery_present, ac_online}` (psutil) |
| `detection.adapters` | `{ok, error, items: [{vendor_id, vendor: "nvidia"\|"intel"\|"amd"\|"other", device_id, name, driver_version}]}` (chi tiết bên dưới bảng) |
| `measurements` | tối đa 32 bản ghi `{id, kind, subject, at, fingerprint_sha256, clip, wall_seconds, cpu_seconds, value, unit, ok, error, quality: "clean"\|"contended"\|"battery"}`. Lần đo mới mang sang các bản ghi cũ **cùng dấu vân tay** từ file chính trước, thêm bản ghi của mình, rồi giữ 32 bản mới nhất. Dấu vân tay đổi thì lịch sử bắt đầu lại |
| `history` | `{had_cuda, first_cuda_at}` (mục 3.5) |

Chi tiết của `detection.adapters`:
- Nguồn là `HKLM\SYSTEM\CurrentControlSet\Enum\PCI`, lớp thiết bị hiển thị, đọc qua `winreg` trong lớp `HardwareSystem`.
- `vendor_id` lấy từ trường `VEN_` của mã thiết bị PCI, **không bao giờ** suy từ tên. Một card NVIDIA chưa có driver hiện tên "Microsoft Basic Display Adapter" nhưng vẫn mang `VEN_10DE`.
- Danh sách gồm cả thiết bị đang lỗi và thiết bị từng cắm. Điều này thiên về an toàn: thấy `10DE` thì máy không bao giờ được coi là "chưa từng có CUDA".
- Đọc lỗi cho `ok: false`. Bỏ `AdapterRAM` vì WMI cắt ở 4 GiB.

Lựa chọn **không** lưu trong file. `choose()` và route GET luôn tính lại, nên không có ảnh chụp nào lệch với bảng trong code.

### 3.3 Dấu vân tay và quan sát nhẹ

- **`gate_key`** gồm những gì quyết định dòng nào khớp:
  - `nvidia_gpus: [{name, compute_capability: "M.m", vram_mib}]`, lấy từ NVML;
  - `nvidia_driver`, dạng `"617.42"`, lấy từ NVML (WMI ghi dạng khác, `32.0.16.1742`);
  - `torch_build`, đọc bằng `importlib.metadata`, **không import torch**;
  - `ffmpeg: {version, sha256, size, mtime_ns}` của đúng file đã đo. `sha256` không đổi khi file được chép sang chỗ khác; `size`/`mtime_ns` là phần quan sát nhẹ so được.
- Lúc đo, số GPU và compute capability của NVML phải bằng của torch. Nếu khác thì lần đo thất bại với mã `nvml_torch_mismatch`.
- **`fingerprint.sha256`** là SHA-256 của `gate_key` đã chuẩn hóa, được tính lại mỗi lần đọc.
- **`info`** chỉ để đọc, không quyết định dòng nào khớp: OS, Python, `torch.version.cuda`, cuDNN.
- **Không lưu định danh máy:** không GPU UUID, serial, MachineGuid, tên máy hay SID. Hồ sơ tách hẳn khỏi mã máy của C6.

**Quan sát nhẹ.** `hardware_detect.observe(root, *, ffmpeg_exe, system=None) -> Observation`:

```python
@dataclass(frozen=True, slots=True)
class Observation:  nvml: Literal["ok", "no_library", "error"]; gpus: tuple[GpuInfo, ...]   # rỗng trừ khi nvml == "ok"
                    nvidia_driver: str | None; nvidia_pci_seen: bool | None              # None: không đọc được registry
                    torch_build: str | None; ffmpeg: FileStat | None; cpu_name: str | None
```

- Bên gọi tự tính rồi truyền vào `resolve()`/`choose()`, nên hai hàm này vẫn chỉ đọc file.
- `observe()` không chạy subprocess và không import torch:
  - NVML được import trễ bên trong hàm;
  - `nvidia_pci_seen` và `cpu_name` đọc từ cùng các khóa registry mà phần nhận diện dùng;
  - `ffmpeg` là `stat` của đúng file ghi trong `detection.ffmpeg.path`.
- **Khi nào chạy:**
  - khi mở trang Cài đặt (cache 30 s);
  - từ Đợt 2, lúc xếp job;
  - **không bao giờ** trong `/api/status`. `_resources()` vẫn đọc NVML như hôm nay (`control_center.py:580-602`), và phần đó không đổi.

Cùng một bảng so sánh áp cho file chính và last-good:

| Quan sát khác hồ sơ ở | Ý nghĩa |
|---|---|
| `nvml` không phải `ok`, trong khi hồ sơ có GPU | `CUDA_UNAVAILABLE` (driver hỏng hay đã gỡ) |
| danh sách GPU (số lượng, tên, compute capability, VRAM), so với `gate_key.nvidia_gpus` | `STALE_GPU`: từ chối job mới tới khi Đo lại |
| `torch_build` | `STALE_TORCH`: từ chối |
| `nvidia_driver` | Ngoài khoảng của dòng thì dòng không khớp. Trong khoảng thì cảnh báo `driver_changed`. NVDEC xử lý theo mục 5.5 |
| `ffmpeg` (`size`, `mtime_ns`), so với `gate_key.ffmpeg` | Cảnh báo `ffmpeg_changed`. Phép thử encoder và NVDEC cũ mất giá trị: decode cpu, xuất x264 |
| `cpu_name`, so với `detection.cpu.model` | Cảnh báo `machine_changed` ("nên Đo lại"; có thể là thư mục được chép sang máy khác) |

### 3.4 Kiểm tra mỗi lần đọc (`read_profile`, chỉ stdlib, không ném lỗi vì nội dung)

1. File lớn hơn 256 KiB, đọc lỗi hay JSON hỏng đều cho `INVALID`. Thiếu file cho `MISSING`.
2. Đọc bằng `utf-8-sig` (PowerShell 5.1 ghi BOM). Khóa trùng (`object_pairs_hook`) và `NaN`/`Infinity` (`parse_constant`) đều cho `INVALID`.
3. Kiểm `schema` và `profile_version`. Phiên bản lạ cho `INVALID`: không cố đọc phần còn lại, không chuyển đổi. Trường mới chỉ được thêm cùng `profile_version` mới. Vì vậy một bản code cũ (ví dụ sau khi quay lui) gặp file của bản mới sẽ báo `INVALID` ("hồ sơ do bản mới hơn ghi; Đo lại") thay vì đoán.
4. `content_sha256` và `fingerprint.sha256` phải khớp.
5. Từng trường:
   - đúng kiểu (`type(x) is int`);
   - enum đóng (danh sách trong code E1);
   - giới hạn độ dài chuỗi;
   - thời gian có múi giờ.
6. Kiểm chéo:
   - `result == "ok"` thì `failure` là null;
   - `torch_cuda.ok` thì `device_count ≥ 1`;
   - `nvml.status == "ok"` và `torch_cuda.ok` thì `device_count == len(nvidia_gpus)`;
   - `torch_cuda.ok` thì `history.had_cuda`.
7. Cache trong tiến trình theo `(mtime_ns, size)` của ba file, theo mẫu `control_center.py:511-540`.

### 3.5 "Từng có CUDA" và bản tốt gần nhất

**`had_cuda` chỉ đi một chiều.** Tiến trình đo tính nó như sau:
- `prev` là OR của file chính và last-good, xét từng file:
  - đọc được: `history.had_cuda` OR `torch_cuda.ok`;
  - có file mà không đọc được: **coi là đúng**, vì không biết thì không được đoán là chưa có;
  - thiếu file: sai.
- `had_cuda` = `prev` OR (`torch_cuda.ok` của lần này).

**Kết quả lần đo** (mã lỗi ở mục 7.2):

| Lần này | `had_cuda` | Thêm điều kiện | Ghi |
|---|---|---|---|
| CUDA dùng được | (đúng) | NVML ok, khớp torch | `ok`; file chính, rồi last-good |
| CUDA không dùng được | đúng | — | `failed`, `cuda_unavailable` (hoặc mã `torch_*`); **last-good giữ nguyên** |
| CUDA không dùng được | sai | danh sách card không đọc được | `failed`, `adapters_unreadable` |
| CUDA không dùng được | sai | thấy `10DE`, hoặc NVML thấy GPU | `failed`, `cuda_unavailable` |
| CUDA không dùng được | sai | NVML `error` (không phải `no_library`) | `failed`, `nvml_failed` |
| CUDA không dùng được | sai | danh sách đọc được, không có `10DE`, NVML `no_library` hoặc 0 GPU | `ok`: máy chỉ có CPU, `had_cuda` sai |

Hệ quả:
- Gỡ driver NVIDIA rồi bấm "Đo lại" cho ra bản thất bại `cuda_unavailable`, không bao giờ cho máy CPU.
- Một máy CPU có hồ sơ bị hỏng sẽ phải bật "Chỉ dùng CPU". Đây là giá phải trả để không bao giờ đoán.

**Last-good:**
- Last-good chỉ thay file chính khi file chính thiếu, hỏng hay khác phiên bản.
- Khi đó phải có quan sát nhẹ, và quan sát đó phải qua đúng bảng so sánh của mục 3.3. Trạng thái là `READY_LAST_GOOD`, kèm cảnh báo.
- Last-good **không** thay cho một lần đo thất bại đã ghi lại, vì thất bại đó là bằng chứng thật, và cũng không thay cho một dấu vân tay đã cũ.

File hỏng không bao giờ bị tự xóa. Lần "Đo lại" sau ghi đè nó, và đó là thao tác của người dùng.

### 3.6 `hardware-override.json`

```json
{"schema": "biliflow.hardware-override", "version": 1, "cpu_only": true,
 "changed_at": "2026-10-12T09:00:00+07:00", "source": "dashboard-pc"}
```

- Thiếu file nghĩa là `cpu_only = false`.
- File hỏng cho `OVERRIDE_INVALID` và **từ chối** job mới. Công tắc không bao giờ bị đoán theo hướng nào.
- **E2 ghi ngay khi người dùng bật hay tắt.**
  - Hộp xác nhận nói rõ: công tắc có hiệu lực cho **job mới từ Đợt 2**.
  - Trang Cài đặt luôn hiện trạng thái của công tắc.
  - Khi Đợt 2 vào mà công tắc đang bật, trang hiện một thông báo một lần.

### 3.7 Ví dụ: PC thật (dạng rút gọn)

Số liệu GPU, CPU, RAM, ổ đĩa và torch đọc bằng lệnh chỉ-đọc ngày 2026-10-11. Thời gian, hash, đường dẫn và kết quả phép thử là **minh họa**, vì E2 chưa chạy.

```json
{
  "schema": "biliflow.hardware-profile", "profile_version": 1, "content_sha256": "<64 hex>",
  "written_at": "2026-10-12T22:10:05+07:00",
  "writer": {"biliflow_version": "0.7.24", "mode": "measure", "launched_by": "script"},
  "attempt": {"result": "ok", "failure": null, "started_at": "…", "finished_at": "…", "budget_seconds": 600},
  "fingerprint": {
    "sha256": "<64 hex>",
    "gate_key": {
      "nvidia_gpus": [{"name": "NVIDIA GeForce RTX 2060", "compute_capability": "7.5", "vram_mib": 6144}],
      "nvidia_driver": "617.42", "torch_build": "2.14.0+cu126",
      "ffmpeg": {"version": "9.0.1", "sha256": "<64 hex>", "size": "<int>", "mtime_ns": "<int>"}
    },
    "info": {"os": "Windows 10.0.26300", "python": "3.11.16", "torch_cuda": "12.6"}
  },
  "detection": {
    "nvml": {"status": "ok", "error": null},
    "nvidia_smi": {"ok": true, "driver_version": "617.42", "gpu_count": 1, "error": null},
    "torch_cuda": {"ok": true, "device_count": 1, "device_name": "NVIDIA GeForce RTX 2060", "capability": "7.5",
                   "arch_list": ["<…>", "sm_75", "<…>"], "total_memory_bytes": "<int>", "fp16_ok": true,
                   "seconds": "<float>", "error": null},
    "ffmpeg": {"ok": true, "error": null, "path": "<thư mục code>\\tools\\ffmpeg\\bin\\ffmpeg.exe", "version": "9.0.1",
               "sha256": "<64 hex>", "size": "<int>", "mtime_ns": "<int>",
               "hwaccels_listed": ["cuda", "vaapi", "dxva2", "qsv", "d3d11va", "d3d12va", "amf"],
               "encoders": {"libx264": {"listed": true, "trial": "ok", "error": null},
                            "h264_nvenc": {"listed": true, "trial": "ok", "error": null},
                            "hevc_nvenc": {"listed": true, "trial": "ok", "error": null},
                            "h264_qsv": {"listed": true, "trial": "not_run", "error": null}},
               "nvdec": {"listed": true, "trial": "ok", "framemd5_equal_cpu": true, "chain": "logo_routing"}},
    "cpu": {"model": "Intel(R) Core(TM) i7-10750H CPU @ 2.60GHz", "vendor": "GenuineIntel",
            "physical_cores": 6, "logical_cores": 12, "features": {"avx2": true, "avx512f": false}},
    "ram_bytes": 16988123136, "disk": {"root_total_bytes": 1000203087872, "root_free_bytes": 610769174528},
    "power": {"battery_present": true, "ac_online": true},
    "adapters": {"ok": true, "error": null, "items": [
      {"vendor_id": "10de", "vendor": "nvidia", "device_id": "<4 hex>", "name": "NVIDIA GeForce RTX 2060", "driver_version": "<…>"},
      {"vendor_id": "8086", "vendor": "intel", "device_id": "<4 hex>", "name": "Intel(R) UHD Graphics", "driver_version": "<…>"}]}
  },
  "measurements": [{"id": "m-20261012T220820-1", "kind": "decode", "subject": "nvdec:logo_routing", "…": "…"}],
  "history": {"had_cuda": true, "first_cuda_at": "2026-10-12T22:10:05+07:00"}
}
```

**Máy chỉ có CPU** (minh họa):
- `nvml.status: "no_library"`, `nvidia_gpus: []`, `nvidia_driver: null`;
- `torch_cuda.ok: false` (`error: "cuda_not_available"`);
- `adapters.ok: true`, không có `10de`;
- `history.had_cuda: false`.

Kết quả:
- `choose("scan")` đi nhánh CPU với `source = "no_cuda_machine"`. Trước E3 nó cho `CPU_ROW_NOT_READY` (mục 5.3);
- `choose("export")` cho x264.

## 4. API Python

### 4.1 Module

Các module được tách theo hai tiêu chí: chạm tới khóa cache tới đâu, và mức độ hay đổi.

| Module | Nội dung | Import ở cấp module | Không bao giờ bị import bởi |
|---|---|---|---|
| `hardware_profile.py` | kiểu, hằng số, `read_profile`, `read_override`/`write_override`, `resolve`, `choose`, `speed_arguments`, `LEGACY_KNOBS`, `freeze_value`/`parse_frozen` (thuần), ghi nguyên khối. Khối `__main__` chỉ là một dòng chuyển sang `hardware_measure.main` (`docs/PERF_INSTALL_PLAN.md:201`) | chỉ stdlib. `hardware_table` và `job_purge` được import trễ bên trong `resolve()` | bất kỳ module nào trong closure của stage |
| `hardware_table.py` | `TABLE_VERSION`, `Match`, `Row`, `ROWS`, bằng chứng | `hardware_profile` (lấy kiểu) | như trên |
| `hardware_detect.py` | E1: Protocol `HardwareSystem`, `WindowsSystem`, `detect()`, `observe()`, tiến trình cháu `torch-probe` (torch chỉ được import **bên trong** tiến trình đó) | `hardware_profile`, `nvdec` (chỉ đọc) | như trên |
| `hardware_measure.py` | E2: `main()`; kiểm tham số, kiểm rảnh, khóa, clip, phép thử, lịch sử số đo, hàm ngưỡng trễ, ghi hồ sơ | `hardware_profile`, `hardware_detect`, `nvdec.input_arguments`/`sampling_filter` (đúng chuỗi lọc thật) | như trên |
| `hardware_manager.py` | phía Control Center: `status()` có cache, `start("remeasure")`, `set_cpu_only()`, `close()`, `popen` thay được | `hardware_profile`; `hardware_detect.observe` được import trễ | như trên |

- **Không làm gì lúc import** (không torch, subprocess, I/O hay luồng). Lý do: `cli.py:42` import `control_center` ngay đầu file, nên mọi tiến trình stage đều import theo. Vì vậy `control_center` chỉ import các module này **trễ**, bên trong `_hardware(center)`.
- **Tránh import vòng:** kiểu nằm ở `hardware_profile`, `hardware_table` import kiểu, còn `hardware_profile` chỉ import `hardware_table` bên trong `resolve()`.

### 4.2 Kiểu và hàm chính

```python
Device = Literal["cuda", "cpu"]; Precision = Literal["fp32", "fp16"]; Decode = Literal["cpu", "nvdec"]
Family = Literal["standard", "standard_ocr8", "fast"]; Task = Literal["scan", "export"]
Form = Literal["prewarm", "plain", "cpu"]

@dataclass(frozen=True, slots=True)
class ScanRequest:  fast_scan: bool; ocr_batch_size: Literal[1, 8]            # yêu cầu của người dùng

@dataclass(frozen=True, slots=True)
class TextKnobs:    detect_precision: Precision; recognition_batch_size: Literal[1, 8]
                    recognition_frame_window: Literal[1, 4]; prewarm_logo_routing: bool
                    logo_routing_workers: int; logo_decode: Decode

@dataclass(frozen=True, slots=True)
class ScanKnobs:    device: Device; text: TextKnobs; visual_logo_routing_workers: int
                    animation_precision: Precision; violence_precision: Precision
                    # __post_init__: mục 2.2; form -> Form
                    def stage(self, name: str) -> StageKnobs: ...   # mục 2.2; tên lạ thì ValueError

@dataclass(frozen=True, slots=True)
class StageKnobs:   stage: str; device: Device; text: TextKnobs | None; routing_workers: int | None
                    precision: Precision | None; violence_precision: Precision | None

@dataclass(frozen=True, slots=True)
class SpeedArguments: text: tuple[object, ...]; visual_logo: tuple[object, ...]
                      animation_safety: tuple[object, ...]; live_safety: tuple[object, ...]

@dataclass(frozen=True, slots=True)
class ExportKnobs:  encoder: Literal["libx264"]; preset: Literal["veryfast"]; crf: Literal[20]
                    size_guard: Literal["vbv"]; audio_bitrate: Literal[384000]
                    validate: Literal["cpu_full_decode"]

class Status(StrEnum):   # resolve() trả về; thứ tự quyết định ở mục 7.1
    READY, READY_LAST_GOOD, READY_UNGATED, NEEDS_MEASUREMENT, INVALID, CUDA_UNAVAILABLE,
    STALE_GPU, STALE_TORCH, NO_GATED_ROW, MULTI_GPU_UNSUPPORTED, OVERRIDE_INVALID, CPU_ROW_NOT_READY

@dataclass(frozen=True, slots=True)
class Reason:       code: ReasonCode; text: str        # ReasonCode: danh sách đóng, bên dưới

@dataclass(frozen=True, slots=True)
class ScanChoice:   task: Literal["scan"]; family: Family; requested_family: Family; device: Device
                    knobs: ScanKnobs; row_id: str; row_status: Literal["gated", "ungated"]
                    table_version: int; source: Literal["profile", "last_good", "cpu_only", "no_cuda_machine"]
                    profile_sha256: str | None; fingerprint_sha256: str | None; observed_driver: str | None
                    reasons: tuple[Reason, ...]; warnings: tuple[Reason, ...]
                    def stage(self, name: str) -> StageKnobs: ...

@dataclass(frozen=True, slots=True)
class ExportChoice: task: Literal["export"]; row_id: Literal["export-x264"]; row_status: Literal["gated"]
                    table_version: int; knobs: ExportKnobs; warnings: tuple[Reason, ...]

class HardwareRefusal(ValueError):   # status hoặc code, message (tiếng Việt)

LEGACY_KNOBS: Mapping[Family, ScanKnobs]   # đúng dòng lệnh hôm nay (fast: nvdec); không phụ thuộc bảng, hồ sơ, công tắc

def read_profile(root: Path, name: Literal["main", "last_good"]) -> ProfileRead: ...   # không ném lỗi vì nội dung
def read_override(root: Path) -> OverrideRead: ...
def write_override(root: Path, *, cpu_only: bool, now: datetime) -> None: ...   # chỉ manager của Control Center gọi
def resolve(root: Path, *, observed: Observation | None) -> Resolution: ...      # không bao giờ ném lỗi
def choose(task: Task, *, root: Path, request: ScanRequest | None = None,
           observed: Observation | None = None) -> ScanChoice | ExportChoice: ...
def speed_arguments(knobs: ScanKnobs, *, config: Mapping[str, object],
                    source_sha256: str | None) -> SpeedArguments: ...
```

- **`resolve()`** không bao giờ ném lỗi; trang Cài đặt dùng nó để hiện trạng thái.
- **`choose("scan")`:**
  - ném `HardwareRefusal` khi trạng thái không phải `READY*`;
  - cũng ném khi `observed is None` (mã `not_observed`).
- **`choose("export")`** không bao giờ ném lỗi trong bản 1.
- **Root thử** được tính **bên trong** `resolve()`, không do bên gọi truyền vào: root khác thư mục cài của bản code, và `job_purge.allowed_project_root(root)` đúng (`job_purge.py:103-111`).
  - Mọi lỗi đều cho "không phải root thử".
  - C2 sẽ thay hàm này bằng `install_root()`, và hàm đó cũng phải cho kết quả an toàn khi lỗi.
- **`ReasonCode`** (danh sách đóng của bản 1):
  - `row_match`, `family_capped`, `last_good_used`, `ungated_row`, `not_observed`;
  - `driver_changed`, `ffmpeg_changed`, `ffmpeg_unavailable`, `machine_changed`;
  - `nvdec_unverified`, `nvdec_selftest_failed`;
  - `cpu_only_recorded`;
  - `c1_off_fp32`, `fp16_anime_careful_only`, `fp16_violence_l_only`, `no_golden_gate`.

### 4.3 Giá trị đóng băng `hw:{id}` (hợp đồng; chỉ ghi từ Đợt 2)

Một khối JSON trong bảng `settings` (`job_store.py:277-281`):

```json
{"schema": "biliflow.hw-choice", "version": 1, "frozen_at": "…", "source": "profile",
 "row_id": "nvidia-sm75-torch2.14.0+cu126", "row_status": "gated", "table_version": 1,
 "family": "fast", "requested": {"fast_scan": true, "ocr_batch_size": 1}, "device": "cuda",
 "knobs": {"text": {"detect_precision": "fp16", "recognition_batch_size": 8, "recognition_frame_window": 4,
                    "prewarm_logo_routing": true, "logo_routing_workers": 3, "logo_decode": "nvdec"},
           "visual_logo": {"routing_workers": 3}, "animation_safety": {"precision": "fp16"},
           "live_safety": {"violence_precision": "fp16"}},
 "profile_sha256": "<hex>", "fingerprint_sha256": "<hex>", "observed_driver": "617.42", "warnings": []}
```

**Giao trong E1 (hàm thuần, chưa ghi gì):**
- `freeze_value(choice, request, now) -> dict`.
- `parse_frozen(raw, request) -> FrozenScan | FrozenInvalid` không bao giờ ném lỗi, vì nó nằm trên đường hỏi trạng thái.
  - `None` (thiếu dòng, hoặc giá trị null đã lưu) nghĩa là **job cũ**. Kết quả là `LEGACY_KNOBS[họ]`, với họ suy từ `request`, nên dòng lệnh đúng như hôm nay, kể cả `--logo-decode nvdec`. Không dùng bảng, hồ sơ, công tắc hay mục 5.5.
  - `request` của job cũ chỉ được dựng qua `JobScheduler.fast_scan()`/`ocr_batch_size()` (`scheduler.py:200-204`), không bao giờ từ giá trị mặc định của tham số `pipeline_stages` (`fast_scan=False`, `job_pipeline.py:175`).
  - Giá trị khác null thì thắng: `request` không được dùng nữa.
  - Giá trị hỏng, khóa lạ hay phiên bản lạ cho `FrozenInvalid`. Job không chạy được, và thẻ hiện "Cấu hình phần cứng của job hỏng – Chạy lại". Không bao giờ tự đóng băng lại.

**Đợt 2** (cùng commit với `JOB_SETTING_NAMES += ("hw",)` và lời gọi trong scheduler):

```python
def freeze_for_job(store: SettingsStore, job_id: int, *, root: Path, request: ScanRequest,
                   mode: Literal["first", "rerun", "resume"], observed: Observation | None,
                   now: Callable[[], datetime]) -> FrozenScan: ...
```

- **`first`** (`start_job`, `scheduler.py:275`) và **`rerun`** (revision mới, `:313-325`) chọn lại từ đầu, và cần `observed` khác None.
- **`resume`** (`:369-373`, kể cả `retry`, `scheduler.py:478-501`) giữ nguyên giá trị đã đóng băng. Job cũ chưa có giá trị thì nhận đúng bản "job cũ" ở trên.
- **Kiểm trước, ghi sau** (`:306-312`): nếu bị từ chối thì không thiết lập nào bị ghi.
- Đợt 2 chỉ hiện lưu ý fp16 bạo lực, fp16 anime và C1 khi stage tương ứng có trong danh sách stage của job.
- Bản xuất **không** nằm trong `hw:{id}`. A3 đóng băng lựa chọn xuất vào `render:{id}`, khóa đã có sẵn trong `JOB_SETTING_NAMES`.

### 4.4 Bất biến (mỗi điều có test)

| # | Bất biến |
|---|---|
| I1 | `choose()`/`resolve()` chỉ đọc ba file, bảng trong code và `job_purge.allowed_project_root`, cộng với `Observation` do bên gọi truyền vào. Không subprocess, torch, NVML hay database. Import `biliflow.hardware_profile` thì `torch`, `pynvml`, `psutil` không có trong `sys.modules` và không có luồng mới (mẫu `tests/test_recycle_bin.py:239-250`). |
| I2 | Control Center không bao giờ import torch hay chạy nvidia-smi. Không code `hardware_*` nào (quan sát nhẹ, `resolve`, manager) chạy trong `/api/status`. Phần đọc NVML sẵn có trong `_resources()` giữ nguyên. Import `biliflow.control_center` không nạp module `hardware_*` nào. |
| I3 | Núm chỉ tới stage qua dòng lệnh, và dòng lệnh nằm trong khóa cache (`stage_cache.py:184-200`). Không stage nào đọc `state\hardware-*.json`, vì file đó không nằm trong khóa nào (`:77-80`). Không module nào trong closure stage import `hardware_*`. |
| I4 | Không âm thầm chạy CPU: `device == "cpu"` chỉ khi công tắc bật (có hiệu lực từ Đợt 2), hoặc máy chưa từng có CUDA theo mục 3.5 (`had_cuda` sai ở mọi file đọc được, không file nào hỏng, danh sách card đọc được và không có `10DE`). NVDEC tự quay về giải mã CPU kèm lý do (`nvdec.py:40-60`) vẫn được phép: kết quả giống hệt, thiết bị tính toán không đổi. |
| I5 | Một revision chỉ có một dòng, một họ, một thiết bị. "Đo lại" hay bật/tắt công tắc không đổi job đã đóng băng. Job không có `hw:{id}` nhận `LEGACY_KNOBS`, tức **đúng** dòng lệnh hôm nay. |
| I6 | Mỗi file một tiến trình ghi; ghi bằng cách thay nguyên khối. |
| I7 | Phép đo chỉ chạy khi có đủ khóa và hàng đợi rảnh (mục 6.3). Nó chỉ ghi `state\hardware-profile*.json`, `logs\…` và `temp\hardware-measure-<stamp>\` của chính root mình; không bao giờ `models\` hay `tools\`. Với guard root khác, nó chỉ làm đúng những việc liệt kê ở mục 6.4. |
| I8 | Route mới chỉ-PC. Không có trong `/api/status`. |
| I9 | Không có code nào dựng khóa `hw:` trong E0–E2. Đợt 2 viết đúng chuỗi `f"hw:{job_id}"`, để regex của `tests/test_job_purge.py:640-649` vẫn bắt được. Không bao giờ né test đó. |
| I10 | Tên mutex và file khóa của phép đo không đổi được qua dòng lệnh hay biến môi trường. Test chỉ thay chúng bằng tham số hàm trong cùng tiến trình. |
| I11 | Lệnh đo từ chối (thoát 64) mọi `ProjectRoot` mà `allowed_project_root` không nhận, trước khi lấy khóa, chạy tiến trình con hay ghi bất cứ gì. |

## 5. Bảng cấu hình đã qua cổng

### 5.1 Nơi đặt và định dạng

- Đặt trong `src/biliflow/hardware_table.py`, dạng hằng số Python:
  - không đặt trong `config/*`, vì bốn file trong đó là đầu vào khóa cache và dành cho B1 (`stage_cache.py:45-56`);
  - không đặt trong file JSON trong `src/`, vì C4 sẽ phải đóng gói thêm; hằng số thì biên dịch được với Nuitka.
- Module không nằm trong closure stage nào, nên sửa bảng không đổi khóa. Khóa chỉ đổi khi dòng lệnh của **job mới đóng băng** khác đi, và đó đúng là điều mong muốn.
- **`row_id` không bao giờ đổi.** Tổ hợp khác thì đặt `row_id` mới; dòng thôi dùng vẫn giữ lại, ghi `retired`, để truy nguồn. Mỗi lần sửa bảng tăng `TABLE_VERSION`.

```python
@dataclass(frozen=True, slots=True)
class Match:   vendor: Literal["nvidia", "none"]; compute_capability: str | None   # khớp chính xác, ví dụ "7.5"
               min_vram_mib: int | None; gpu_count: int | None; torch_builds: frozenset[str]
               driver_min: str | None; driver_max: str | None                    # so số "major.minor"
@dataclass(frozen=True, slots=True)
class Row:     row_id: str; status: Literal["gated", "ungated", "retired"]; match: Match
               families: Mapping[Family, ScanKnobs]; default_family: Family
               nvdec_verified: frozenset[tuple[str, str]]     # (driver, SHA-256 của ffmpeg.exe) đã qua cổng pixel
               free_fields: Mapping[str, tuple[object, ...]]   # núm giống hệt được chọn theo thời gian (mục 5.5)
               runnable_after: str | None                      # ví dụ "E3"
               evidence: tuple[str, ...]; caveats: tuple[Reason, ...]
```

- Dòng GPU (`vendor="nvidia"`) chỉ được xét ở bước 8 của mục 7.1.
- Dòng CPU (`vendor="none"`) chỉ được xét ở nhánh CPU (bước 9), và nhánh đó chỉ tới được từ công tắc hoặc từ "máy chưa từng có CUDA". Công tắc và lịch sử không phải phần cứng, nên chúng nằm trong code của `resolve()`, không nằm trong `Match`.

### 5.2 Khóa khớp

1. **Hãng và số thiết bị:** hơn một GPU NVIDIA cho `MULTI_GPU_UNSUPPORTED` (câu hỏi 5).
   - Các stage tính toán chỉ nhận `--device cuda|cpu` (`cli.py:72`, `:104`, `:145`), không có `cuda:N`, nên torch luôn dùng thiết bị mặc định.
   - Thứ tự thiết bị của torch có thể khác NVML. Bảng theo dõi trên dashboard cũng đọc thiết bị 0 (`control_center.py:591`).
2. **Compute capability khớp chính xác.** Lấy từ NVML; tiến trình torch phải báo cùng giá trị.
3. **VRAM tối thiểu.**
4. **Bản torch khớp chính xác.**
5. **Khoảng driver cho phần tính toán:** `driver_min` là driver thấp nhất từng qua cổng; `driver_max` để mở.
   - Đây là suy luận, chưa đo: bản torch cu126 mang sẵn cuDNN 9 và cuBLAS 12, nên kernel fp16 cho sm_75 đến từ wheel chứ không từ driver.
6. **NVDEC do driver làm,** nên có tập `(driver, ffmpeg)` đã kiểm riêng (mục 5.5).

### 5.3 Các dòng có cơ sở hôm nay (`TABLE_VERSION = 1`)

Mọi cổng đều chạy trên một RTX 2060 6 GB, hồ sơ quét `careful`, torch `2.14.0+cu126`.

**Dòng 1: `nvidia-sm75-torch2.14.0+cu126`, `gated`.**
- Khớp: NVIDIA, compute capability 7.5, VRAM ≥ 6144 MiB, đúng 1 GPU, torch `2.14.0+cu126`, driver ≥ 576.80.
- `default_family = fast`, `free_fields = {}`.

| Họ | Giá trị (nguồn dòng lệnh hôm nay) | Bằng chứng cổng |
|---|---|---|
| `standard` | dạng `plain`, batch 1 (`job_pipeline.py:213-220`) | Đường tham chiếu của cổng OCR-8 và của phép so chế độ nhanh với chế độ thường; các cổng fp16 thì so với fp32 của chế độ nhanh (`docs/CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md:640`). Trên L, chỉ xét quảng cáo, standard và fast ra cùng kết quả (`docs/SCAN_PERFORMANCE.md:57-77`) |
| `standard_ocr8` | dạng `plain`, batch 8 (`:214-216`) | Đoạn trích giống hệt, chỉ khác độ tin cậy tối đa 6e-6 (`docs/SCAN_PERFORMANCE.md:531-547`); trên cả phim L, track và ảnh xem trước giống hệt (`:634-641`) |
| `fast` | dạng `prewarm`, `logo_decode` theo mục 5.5 (`job_pipeline.py:130-144`, `:193-212`) | OCR 8 + window 4 (`docs/SCAN_PERFORMANCE.md:115-133`, `:146-154`); worker giống hệt do cách dựng, cộng cả phim L (`:79-91`); NVDEC giống hệt pixel (`docs/CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md:469-473`, `:520`); chạy chồng với khởi động routing (`:549-575`); CRAFT fp16 trên L và H (`:644-696`); anime fp16 trên H và một phim hoạt hình thứ hai (`:706-758`); bạo lực fp16 chỉ trên L (`:815-826`) |

`nvdec_verified = {("576.80", "<SHA-256 của ffmpeg.exe đã dùng trong cổng>")}`.
- E1 điền hash này bằng cách đọc file ở `tools\ffmpeg\bin` của thư mục cài, chỉ đọc. Mtime của file có trước cổng NVDEC.
- Không chuỗi phiên bản đầy đủ nào được đưa vào repo.

Lưu ý mang theo, hiện trên trang Cài đặt và trong cảnh báo của job:
- họ fp32 (`standard`, `standard_ocr8`) **tắt luật máu anime C1**, vì C1 chỉ hiệu chỉnh cho report fp16 ở 2 fps (`gore_triage.py:65`, `:114-115`);
- anime fp16 mới chỉ đo trên hồ sơ `careful` (`docs/CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md:758`). Hồ sơ `fast` vẫn dùng fp16 như hôm nay, để giữ nguyên hành vi;
- bạo lực fp16 mới chỉ chứng minh trên L (`:826`);
- chưa núm tốc độ nào qua bộ Golden (`docs/QUALITY_PLAN.md:173`, `:337`).

**Dòng 2: `cpu-fp32-torch2.14.0+cu126`, `ungated`, `runnable_after = "E3"`.**
- Khớp: `vendor="none"`, torch `2.14.0+cu126`, xét ở nhánh CPU. Bản torch CPU của bộ cài sẽ có dòng riêng sau C5 (`docs/PERF_INSTALL_PLAN.md:221`).
- Chỉ có họ `standard`, ở dạng `cpu`.
- **Trước E3 không chạy được:** Florence đưa đầu vào ở float16 (`scripts/localize_visual_logo_report.py:135`), trong khi trên CPU model được nạp fp32 (`:215`). Vì vậy `runnable_after` khác None cho `CPU_ROW_NOT_READY` trên **mọi** root.
  - Phần `autocast` viết cứng `"cuda"` không chặn dòng này: nó chỉ chạy khi precision là fp16 (`ocr_precision.py:42-43`).
- E3 gỡ chặn bằng cách sửa bảng (`runnable_after = None`, tăng `TABLE_VERSION`). Dòng thành `gated` sau E4: cổng Golden của L, H, T so với GPU fp32 (`docs/PERF_INSTALL_PLAN.md:217-221`).

**Xuất: `export-x264`, `gated`.**
- libx264 veryfast CRF 20, chặn VBV, AAC 384 kb/s, kiểm tra bằng giải mã toàn bộ trên CPU (`final_renderer.py:592-612`, `:642-656`).
- Các thử nghiệm NVENC chỉ được **ghi lại** trong phần nhận diện. A3 thêm dòng khi người dùng duyệt.

**Không có dòng:**
- kiến trúc GPU khác, card AMD hay Intel, nhiều GPU;
- đường fp32 tham chiếu trên một card chưa đo (câu hỏi 2: từ chối; `arch_list` vẫn được ghi để một dòng sau này dùng được);
- fp16 trên CPU;
- NVDEC cho 18+ và `live_safety` (cổng B2);
- NVENC/QSV;
- tổ hợp "nhanh trước G1" (detect fp32): đã qua cổng nhưng giao diện không chọn được.

### 5.4 Máy không khớp dòng nào

- Card NVIDIA, bản torch hay driver chưa đo: `NO_GATED_ROW`. Lời từ chối ghi rõ khóa nào không khớp.
- Không bao giờ tự chuyển sang CPU.
- **Máy CPU:** dùng dòng 2. Trước E3 cho `CPU_ROW_NOT_READY`; từ E3 tới E4, dòng chỉ chạy trên root thử (câu hỏi 2: từ chối trên thư mục cài thật).

### 5.5 NVDEC và ngưỡng trễ

- **Núm số học** (fp16, OCR 8 + window 4) không bao giờ tự đổi. Chỉ sửa bảng trong code mới đổi được chúng.
- **Cách giải mã không bao giờ chọn theo thời gian đo.** Đo nhanh khi máy rảnh luôn thiên về CPU:
  - NVDEC chạy một mình chậm hơn 26% thời gian thực nhưng tốn ít hơn 96% CPU (`docs/CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md:446-457`);
  - nó chỉ thắng khi chạy chồng với OCR. Cả việc chạy chồng (khởi động routing bằng NVDEC, so với không khởi động) lợi 288 s trên L (`:575`).
- **Quy tắc giải mã** cho họ `fast`, với `d` là driver trong quan sát và `f` là `gate_key.ffmpeg.sha256` của hồ sơ:

  ```
  logo_decode = "nvdec"  khi  không có cảnh báo ffmpeg_changed  và  (
        (d, f) ∈ row.nvdec_verified
     hoặc (detection.ffmpeg.nvdec.trial == "ok"  và  framemd5_equal_cpu
           và  gate_key.nvidia_driver của hồ sơ == d) )
  logo_decode = "cpu"    trong mọi trường hợp khác, kèm cảnh báo
                         nvdec_unverified | nvdec_selftest_failed | ffmpeg_changed
  ```

  - Nhánh thứ hai là phép tự kiểm của E2, được dùng làm cổng cho từng driver (câu hỏi 1, phương án a).
  - Trước khi Đợt 2 bật, cổng framemd5 cả phim L chạy lại ở khung GPU 3 (phương án b). Qua thì cặp `(driver, SHA-256 của ffmpeg.exe)` được thêm vào `nvdec_verified`, kèm `TABLE_VERSION` mới.
  - Phép tự kiểm framemd5 của E2 (mục 6.3) mà **thất bại** thì hạ ngay về `cpu`.
  - Kết quả hai cách giải mã giống hệt, nên không cần ngưỡng trễ.
- **Ngưỡng trễ cho núm giống hệt chọn theo thời gian** (`free_fields`):
  - Bảng 1 chưa có núm nào như vậy: số worker nằm trong khóa cache stage tới B1, còn cách giải mã theo quy tắc ở trên.
  - E2 vẫn cài hàm thuần `hysteresis_choice(history, field, current, candidates)` và giữ lịch sử số đo (mục 3.2), đúng kế hoạch (`docs/PERF_INSTALL_PLAN.md:206`). Trong E2, chỉ test gọi hàm này; B1 nối nó vào khi thêm núm tự do đầu tiên.
- **Quy tắc:**
  - chỉ đổi khi **2 lần đo liên tiếp** đều cho thấy giá trị mới nhanh hơn ít nhất **10%**, và lợi ít nhất **60 s** trên một phim tham chiếu;
  - "phim tham chiếu" là hằng số `REFERENCE_FILM_SECONDS = 9000` (2,5 giờ). Đây là quy ước cho một phim dài, không phải số đo. Lợi trên clip được quy đổi bằng `(t_cũ − t_mới) / clip_seconds × 9000`;
  - hai lần đo đó phải `clean`, cắm điện, cùng dấu vân tay;
  - đổi ngược lại cũng cần đúng mức đó;
  - dấu vân tay đổi thì đếm lại từ đầu, vì lịch sử bắt đầu lại.
- Con số này dựa trên nhiễu đã ghi, tối đa khoảng 6% (`docs/SCAN_PERFORMANCE.md:14`, `:152-154`), và trên cách các cổng trước dùng ít nhất 5% hoặc 60 s.
- Một lần đổi chỉ áp dụng cho job đóng băng sau đó. Trang Cài đặt ghi "Job mới sẽ dùng X (trước: Y) – cache stage liên quan sẽ tính lại".

## 6. Route, trang Cài đặt và "Đo lại"

### 6.1 Route (dòng route viết thẳng trong `control_center.py`, logic nằm sau `_hardware(center)`)

| Route | Chặn | Kết quả |
|---|---|---|
| `GET /api/hardware-profile` | Kiểm `loopback_client()` ngay trong route; sai thì 403 `{error: PC_ONLY_HARDWARE, code: "pc_only"}`. Bắt buộc, vì cổng điện thoại không có danh sách GET được phép (`control_center.py:2585-2623`). Mẫu ở `:2098-2103` | JSON ở dưới |
| `POST /api/hardware-profile/remeasure` | token + `loopback_client()` | Bỏ qua body. Trả 409 `busy` / `not_paused` / `not_idle`, hoặc bắt đầu việc đo |
| `POST /api/hardware-profile/cpu-only` | token + `loopback_client()` | Body đúng `{"enabled": bool}` (`type(...) is bool`, không thêm khóa nào), sai thì 400. Ghi `hardware-override.json` và thêm sự kiện `HARDWARE_CPU_ONLY_CHANGED` |

**GET, dạng trả về:**

```json
{"status": "ready", "applies_to_jobs": false,
 "row": {"id": "nvidia-sm75-torch2.14.0+cu126", "status": "gated", "table_version": 1},
 "families": {"fast": {"family": "fast", "capped": false, "logo_decode": "nvdec", "refusal": null},
              "standard_ocr8": {"family": "standard_ocr8", "capped": false, "refusal": null},
              "standard": {"family": "standard", "capped": false, "refusal": null}},
 "reasons": [{"code": "row_match", "text": "RTX 2060 (7.5), torch 2.14.0+cu126, driver 617.42 ≥ 576.80"}],
 "warnings": [{"code": "c1_off_fp32", "text": "…"}],
 "cpu_only": {"value": false, "valid": true},
 "profile": {"present": true, "source": "main", "written_at": "…", "result": "ok", "failure": null,
             "power": {"battery_present": true, "ac_online": true}},
 "detection": {"gpu": "NVIDIA GeForce RTX 2060", "compute_capability": "7.5", "vram_mib": 6144,
               "driver": "617.42", "torch": "2.14.0+cu126", "cpu": "…", "ram_gib": 15.8,
               "ffmpeg": {"version": "9.0.1", "encoders": {"libx264": "ok", "h264_nvenc": "ok", "hevc_nvenc": "ok"},
                          "nvdec": {"trial": "ok", "framemd5_equal_cpu": true}}},
 "task": null,
 "can_remeasure": {"ok": false, "reason": "not_paused", "text": "Hãy tạm dừng hàng đợi trước"}}
```

- **Các biến thể của `task`:**
  - khi đang đo: `{"running": true, "step": "torch_probe", "index": 3, "total": 9, "started_at": "…"}`;
  - khi đã xong: `{"running": false, "exit": 0, "outcome": "done", "message": "…", "finished_at": "…"}`.
- **`NEEDS_MEASUREMENT`:** `profile.present` là false, `detection` là null, và mỗi họ là `{"family": null, "refusal": "needs_measurement"}`.
- **Quan sát nhẹ lỗi:** phần đó là null, kèm cảnh báo `not_observed`.
- **Đường dẫn và chuỗi lỗi đi qua bộ làm sạch:** tiền tố của root, thư mục cài, thư mục code và hồ sơ người dùng được thay bằng `<root>`, `<install>`, `<code>`, `<user>`. Không có token.
- **Control Center cũ** trả 404 cho route này. `adapter.js` đổi nó thành `{unavailable: true}`, như Tailscale (`dashboard_v2/verify-adapter.cjs:299-303`).

**Cách khớp route và các chỗ phải sửa theo:**
- Route khớp bằng `path == "/api/hardware-profile"` và `re.fullmatch(r"/api/hardware-profile/(remeasure|cpu-only)", path)`. Các test đọc source thấy được chúng, và GET với POST không trùng đường dẫn (`tests/test_dashboard_v2_contract.py:76-86`).
- `phone_access.py`:
  - thêm lý do riêng `PC_ONLY_HARDWARE` (không dùng lại `PC_ONLY_SOURCE`; `tests/test_dashboard_v2_contract.py:212-231`);
  - thêm hai đường POST vào `PC_ONLY_POSTS` (mẫu Tailscale `phone_access.py:136-138`).
- **`can_remeasure`** đúng khi đủ cả bốn điều:
  - `scheduler_paused` là true;
  - `scheduler.active is None`;
  - không job nào `scheduler.is_busy(id)`. Dùng hàm công khai `scheduler.py:157-163`, không đọc `_executing_job_id`, vì A1b sẽ đổi nó thành tập hợp;
  - không job nào ở `IN_PROCESS_STATES` (`job_store.py:16-19`, gồm cả RENDERING).
- Các route mới phải vào danh sách "agent không bao giờ gọi" của `AGENTS.md` qua C0 trước khi G1 gộp chúng. Danh sách đó cũng phải có chính lệnh đo (mục 10, phần ghi nhận). E không tự sửa `AGENTS.md`.

### 6.2 Trang Cài đặt (chỉ PC)

- **File mới `dashboard_v2/hardware-view.js`, chỉ nạp trong `live.html`,** giống các module tải video (`tests/test_dashboard_v2_downloads.py:147-156`).
  - Thẻ `<script defer>` nằm trong `<head>` (dòng 8). Phần thân của `live.html` và `index.html` vẫn phải giống nhau (`dashboard_v2/verify.cjs:191-192`).
  - `index.html` (bản demo) và `dashboard_v2/serve.py` không đổi. Khi không có module, `app.js` hiện "Chỉ có ở bản live" trong bản demo.
- **Giao diện của module:** `window.BFHardware.create({store, toast, showModal, esc})` trả về `{panel(state), onAction(el), watch(on), onSnapshot()}`, theo mẫu `BFDownloadLive.create` (`app.js:19`).
- **`app.js` chỉ thêm đúng các dòng sau**, để vẫn dưới 800 dòng (hôm nay 793):
  - dòng `create`;
  - lời gọi `panel` trong `settingsView()` (`app.js:322-324`);
  - một nhánh `else if(action==='hw')` chuyển cho `onAction` (mẫu `:681`);
  - `watch(view==='settings')` trong `route()` (mẫu `:740`);
  - lời gọi `onSnapshot` (mẫu `:761`);
  - sửa danh sách việc chỉ-PC viết cứng trong khung điện thoại (`:241`).
- **Chỉ `adapter.js` gọi mạng:**
  - `loadHardware()` và `hardwareAction()`, từ chối khi `snap.device !== 'pc'` (mẫu `accountAction`, `adapter.js:542-545`);
  - hỏi lại như `watchTailscale` (`:485-489`): theo mỗi nhịp khi đang mở Cài đặt trên PC; khi đã đóng thì chỉ trong lúc có việc đo đang chạy.
- **Khung hiển thị:**
  - huy hiệu trạng thái; GPU, VRAM, driver, torch; kết quả thử FFmpeg; CPU, RAM;
  - dòng và họ, kèm lý do và cảnh báo (C1, driver NVDEC, "chưa qua cổng");
  - lần đo gần nhất và nguồn điện lúc đo.
- **Nút "Đo lại":** bị khóa kèm `can_remeasure.text`. Hộp xác nhận ghi: dùng GPU vài phút; hàng đợi phải tạm dừng, và stage đang chạy phải xong.
- **Công tắc "Chỉ dùng CPU":** hộp xác nhận ghi:
  - chậm hơn nhiều;
  - tắt luật C1;
  - đường CPU chưa qua cổng (E4);
  - **có hiệu lực cho job mới từ Đợt 2**, chưa áp dụng trước đó;
  - job đã xếp giữ thiết bị đã đóng băng.
- **Trên điện thoại:** trả `''` (như `tailscalePanel`, `app.js:284-321`).
- Dòng viết cứng "Worker: 1 GPU · hàng đợi FIFO" (`app.js:324`) để nguyên; nó thuộc A.

### 6.3 "Đo lại" chạy thế nào

**Đường chạy.** Control Center (qua `hardware_manager`) và người chạy tay dùng chung một lệnh:

```
powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File <thư mục code>\scripts\hardware-measure.ps1 ^
  -ProjectRoot <root> -GuardRoot <root> [-GuardRoot <thư mục cài thật>] -Python <python.exe>
  [-FfmpegDir <thư mục>] [-Stamp <yyyyMMdd-HHmmss-xxxx>] [-WaitMinutes N]
```

- **`.ps1`** chỉ gồm ký tự ASCII. Nó làm ba việc rồi trả lại mã thoát:
  - dot-source `env.ps1` (không sửa);
  - kiểm `-Python` là `.venv\Scripts\python.exe`;
  - chạy `python -m biliflow.hardware_profile measure …` (`docs/PERF_INSTALL_PLAN.md:201`).

  Toàn bộ khóa nằm trong tiến trình Python: chỉ một cách làm, test được, và dùng chung cho đường chạy của Control Center. Đây là cách hiểu của "`.ps1` có khóa riêng" (`docs/PERF_INSTALL_PLAN.md:202`). Script không bao giờ thêm lệnh vào `run.ps1`.
- **`-FfmpegDir`** mặc định là `<thư mục code>\tools\ffmpeg\bin`.
  - Thư mục phải chứa `ffmpeg.exe` và `ffprobe.exe`. Chúng chỉ được đọc và chạy tại chỗ, không bao giờ bị ghi.
  - Hồ sơ ghi đường dẫn tuyệt đối của `ffmpeg.exe` đó, và quan sát nhẹ `stat` đúng file ấy.
- **Thư mục chạy** `<ProjectRoot>\temp\hardware-measure-<stamp>\` chứa:
  - clip;
  - `progress.json` (`{step, index, total, started_at}`), thay nguyên khối ở mỗi bước;
  - file hủy `cancel`.

  `-Stamp` do manager chọn. Người chạy tay bỏ trống thì lệnh tự tạo.
- **Control Center:**
  - gọi script từ thư mục code (`Path(__file__).parents[2]`), không từ `<root>\scripts`;
  - ghi log vào `<root>\logs\control-center\hardware-measure-<stamp>.log`;
  - mỗi lúc chỉ một việc;
  - Control Center thử cũng canh thư mục cài thật, nếu `python.exe` thuộc `.venv` của thư mục cài và ở đó có `state\control-center.sqlite3`.

**Các bước trong Python** (mọi bước khóa chạy trên **luồng chính**):
1. **Kiểm tham số** (sai thì thoát 64, khi chưa lấy khóa, chưa chạy gì và chưa ghi gì):
   - `ProjectRoot` là đường dẫn tuyệt đối, tồn tại, và `allowed_project_root(ProjectRoot)` đúng (I11);
   - mỗi `GuardRoot` là đường dẫn tuyệt đối tới một thư mục;
   - `FfmpegDir` hợp lệ;
   - `Stamp` đúng mẫu `^\d{8}-\d{6}-[0-9a-f]{4}$`.

   Dòng lệnh không có tùy chọn nào cho tên mutex hay file khóa (I10).
2. **Khóa riêng:** mutex `Local\BiliFlowHardwareProfile`, chờ 0 s. Bị giữ thì thoát 75.
3. **Kiểm rảnh,** không HTTP, không thử `control-center.lock`. Với từng guard root:
   - **Control Center đang chạy** khi `state\control-center.json` tồn tại và PID trong đó còn sống.
     - File đó chứa cả token phiên (`control_center.py:1834-1837`). Một hàm riêng chỉ trả về số PID, không bao giờ ghi log nội dung hay chuỗi lỗi của nó.
   - **Đang chạy:** đọc `state\control-center.sqlite3` bằng URI `mode=ro`, **không bao giờ qua JobStore** (hàm khởi tạo của nó migrate và ghi, `job_store.py:329-392`). Cần đủ ba điều:
     - `scheduler_paused` true;
     - không job nào ở `IN_PROCESS_STATES`;
     - không stage nào RUNNING.

     Với `downloads.sqlite3`, chỉ đếm số việc ở `SLOT_STATES`, vì bảng đó còn chứa link và tên. Có việc tải đang chạy thì không từ chối, chỉ đánh dấu số đo là `contended`.
   - **Không chạy:** **không mở database nào.**
     - Không có scheduler nào chạy. Stage chạy tay và bản xuất đã được mutex và file khóa che.
     - Lý do không mở: hai database dùng WAL (`job_store.py:189`, `download_store.py:134`), và một kết nối chỉ-đọc vẫn có thể tạo `-wal`/`-shm` trong `state\` thật.
     - Cách này cũng tránh trường hợp dòng IN_PROCESS còn sót của một Control Center bị sập chặn phép đo mãi.
   - **`--wait-minutes N`:** kiểm lại mỗi 30 s và **không giữ khóa nào** trong lúc chờ ("chờ, không chen ngang", `docs/PERF_INSTALL_PLAN.md:548`).
4. **Khóa theo thứ tự toàn cục:**
   - riêng → `Local\BiliFlowGpuInference` → `Local\BiliFlowFinalRender` → `<guard root>\state\final-render.lock`;
   - mỗi khóa thử tối đa 2 s; lấy đủ hoặc trả hết. `WAIT_ABANDONED` tính là lấy được, có ghi lại;
   - lỗi thì nhả theo thứ tự ngược và thoát 75.

   Ghi chú về các khóa:
   - **Cần cả hai mutex xuất:** bản xuất của Control Center lấy `Local\BiliFlowFinalRender` trước rồi mới tới file khóa (`scripts/run.ps1:25-36`, `final_renderer.py:735`); bản xuất trên trang duyệt chỉ lấy file khóa (`review_workflow.py:4722-4737`).
   - **File khóa:**
     - mở bằng `O_RDWR`, **không `O_CREAT`**, rồi `msvcrt.locking(LK_NBLCK, 1)` ở vị trí 0, đúng như `resource_lock.py:33-34`;
     - không ghi gì vào file;
     - thiếu file thì bỏ qua, vì `project_resource_lock` sẽ tạo file hoặc ghi `b"0"` vào state thật (`resource_lock.py:17-25`).
   - **Không bao giờ chờ lâu trong khi giữ khóa,** nên không thể kẹt với `WaitOne()` không hạn giờ của `run.ps1` (`:36`).
   - **Mutex của Windows thuộc về luồng đã lấy nó.**
     - Mọi lần lấy và nhả đều ở luồng chính.
     - Giới hạn thời gian được giữ bằng timeout của tiến trình con và bằng cách kiểm file hủy mỗi giây trên luồng chính. Không dùng timer, signal handler hay `atexit` để nhả khóa.
     - Tiến trình thoát ngay sau khi nhả.
   - **Quy tắc cho A3:** nếu một ngày bản xuất lấy khóa GPU, nó phải lấy trước `FinalRender`.
5. **Kiểm rảnh lại sau khi đã có khóa.** Nếu hết rảnh thì nhả và thoát 75.
6. **Nhận diện (E1):**
   - NVML; `nvidia-smi` lấy từ System32 (timeout 10 s) chỉ để đối chiếu;
   - CPU, RAM, ổ đĩa, nguồn điện và adapter qua `HardwareSystem`;
   - tiến trình cháu torch (120 s): `cuda.is_available`, tên, compute capability, `get_arch_list()`, bộ nhớ, một phép nhân ma trận nhỏ fp32/fp16 có `synchronize`; in đúng một dòng JSON;
   - `ffmpeg -version/-hwaccels/-encoders` và SHA-256 của `ffmpeg.exe`.
7. **Clip tổng hợp:**
   - lavfi `testsrc2` + `noise`, 1920×1080, 30 fps, 20 s, h264/yuv420p, `-threads 1`, nên lần nào tạo cũng giống nhau, và hash được ghi lại;
   - nằm trong thư mục chạy, không bao giờ trong `input\` (watcher sẽ nhập nó);
   - không có clip đi kèm repo, vì `*.mp4` bị gitignore (`.gitignore:45`).
8. **Phép thử** (mỗi phép có timeout riêng; giữa các bước và trong lúc chờ đều kiểm file hủy):
   - mã hóa 5 s bằng libx264 (veryfast CRF 20), `h264_nvenc`, `hevc_nvenc`. QSV, AMF và AV1 chỉ ghi là "có trong danh sách";
   - giải mã chuỗi routing logo bằng CPU và bằng NVDEC, qua `nvdec.input_arguments`/`sampling_filter` (`nvdec.py:25-33`); so framemd5 và đo thời gian. Đây là **phép tự kiểm rẻ** cho từng driver, không thay cổng cả phim.
   - Lỗi FFmpeg (thiếu file, phép thử thất bại) chỉ được ghi trong `detection.ffmpeg`. Lần đo vẫn `ok`; hệ quả là decode cpu, kèm cảnh báo `ffmpeg_unavailable`.
9. **Ghi kết quả:**
   - nhả khóa GPU và khóa xuất;
   - ghi file chính theo bảng ở mục 3.5, rồi last-good nếu thành công. Mọi chuỗi lỗi đi qua bộ làm sạch (mục 6.1) và bị cắt ở 300 ký tự;
   - nhả khóa riêng; xóa thư mục chạy.
   - Nếu dấu hiệu rảnh đổi trong lúc chạy (job vào trạng thái chạy, tải xuống hoạt động, rút điện), số đo mang `contended` hoặc `battery`. Kết quả nhận diện vẫn có giá trị.

**Mã thoát:**

| Mã | Nghĩa | Ghi hồ sơ? |
|---|---|---|
| 0 | xong, lần đo `ok` | file chính và last-good |
| 10 | xong, lần đo `failed` (mục 7.2) | chỉ file chính; last-good giữ nguyên |
| 64 | sai tham số, hoặc root không được phép | không; chưa lấy khóa |
| 70 | lỗi nội bộ | không |
| 75 | bận, không rảnh, hoặc đã hủy | không |
| 124 | quá giờ | không |
| khác | ví dụ PowerShell lỗi gán tham số (thoát 1), hay tiến trình bị diệt | manager báo "Lỗi không rõ (mã N)", rồi đọc lại file để biết có gì đã được ghi |

**Manager và lúc Control Center dừng:**
- Manager cho tối đa 15 phút: giới hạn 10 phút cộng thời gian ân hạn. Quá hạn thì ghi file hủy, chờ thêm, và **chỉ khi đó** mới diệt cả cây tiến trình.
- Khi Control Center dừng (`serve()` lẫn `stop()`, cạnh `self.tailscale.close()`, `control_center.py:1852-1853`, `:1888-1889`), `center.hardware.close()` ghi file hủy và **không bao giờ diệt** lần đo. Lần đo tự nhả khóa rồi thoát.
- Lần khởi động sau, manager thấy một lần đo đang chạy nhờ thử mutex riêng (mở, chờ 0 s, nhả ngay nếu lấy được), và hiện "Đang đo (từ lần chạy trước)".

**Còn lại:**
- Nếu ai đó bỏ tạm dừng giữa chừng (điện thoại làm được, `phone_access.py:157`), stage GPU kế tiếp chờ trong `run.ps1`, lâu nhất bằng thời gian đo.
- Bị diệt cứng khi đang giữ mutex có thể làm một `run.ps1` đang chờ bỏ qua lệnh của nó (`scripts/run.ps1:31-36`, lỗi có từ trước, xem mục 11). Vì vậy dừng mềm luôn đi trước.

### 6.4 Kiểm thật E2 trên PC (agent)

- **Chuẩn bị:** chép `ffmpeg.exe` và `ffprobe.exe` từ `E:\DungChung\BiliFlow\tools\ffmpeg\bin` sang `<worktree>\tools\ffmpeg\bin`.
  - `tools/` đã bị gitignore. File chỉ được đọc ở thư mục cài.
  - SHA-256 giữ nguyên, nên quy tắc NVDEC ở mục 5.5 vẫn so được.
- **Lệnh, chỉ ở khung giờ GPU 3:** `-Python E:\DungChung\BiliFlow\.venv\Scripts\python.exe -ProjectRoot <worktree>\temp\hw-measure-<stamp> -GuardRoot <worktree>\temp\hw-measure-<stamp> -GuardRoot E:\DungChung\BiliFlow`.
  - Code của worktree chỉ nhận `ProjectRoot` là worktree hoặc một thư mục trong `temp\` của nó (I11), nên chỉ ghi vào root tạm.
- **Những gì lần đo chạm tới ở thư mục cài thật** (đầy đủ):
  - `state\control-center.json`: chỉ đọc số PID;
  - `state\control-center.sqlite3` và `state\downloads.sqlite3`: chỉ đọc (`mode=ro`), và **chỉ khi Control Center thật đang chạy**. Khi đó kết nối của chính nó giữ `-wal`/`-shm`, nên không file mới nào được tạo;
  - mutex `Local\BiliFlowGpuInference` và `Local\BiliFlowFinalRender` (đối tượng của kernel, không phải file);
  - khóa 1 byte trên `state\final-render.lock` có sẵn: mở không `O_CREAT`, không ghi.

  Ngoài ra không gì khác.
- **Cần bạn tạm dừng hàng đợi thật, hoặc tắt Control Center thật.** Agent không bao giờ POST `/api/scheduler`. Nếu hàng đợi chưa dừng, script thoát 75.
- `state\hardware-profile.json` thật chỉ được ghi khi **bạn** bấm "Đo lại" trên Control Center thật, sau khi gộp G1.
- **Control Center thử:**
  - `python -m biliflow.control_entry --project-root <worktree>\temp\hw-cc-<stamp> --port 8798 --no-import-existing`, với `PYTHONPATH` là `src` của worktree, giống `scripts/Start-BiliFlow.ps1:152`. Không chạy qua `python -m biliflow … control-center`: `cli.py` import các module quét, mà các module đó import torch (`cli.py:17`, `:24`);
  - chép `config\` trừ `*.local.*`;
  - dừng bằng PID đang nghe ở 8798, **không bao giờ** bằng `Stop-BiliFlow.ps1`, vì script đó dừng Control Center thật (`scripts/Stop-BiliFlow.ps1:6-18`);
  - dùng cổng 8798 để tránh 8797, cổng mà A hay B0 có thể đang dùng.
  - Bấm "Đo lại" trên Control Center thử cũng là một lần đo thật trên GPU. Vì vậy cũng chỉ làm ở khung GPU 3, khi hàng đợi thật đã dừng.

## 7. Trạng thái và trường hợp lỗi

"Job mới" và "job đã đóng băng" mô tả hành vi từ Đợt 2. Trong E0–E2, trang Cài đặt hiện đúng các trạng thái này nhưng chưa có gì bị chặn.

### 7.1 Thứ tự quyết định của `resolve(root, observed)`

Bước đầu tiên khớp thì quyết định. Các cảnh báo ở bước 10 cộng thêm vào trạng thái `READY*`.

0. **Root thử:** tính theo mục 4.2. Lỗi bất kỳ cho "không phải root thử".
1. **Công tắc hỏng** → `OVERRIDE_INVALID`.
2. **Công tắc bật** → nhánh CPU (bước 9), nguồn `cpu_only`. Bước này không cần hồ sơ.
   - Trong E0–E2, trang hiện kết quả của nhánh CPU, kèm `cpu_only_recorded` ("có hiệu lực từ Đợt 2").
3. **Chọn hồ sơ:**
   - file chính đọc được (thành công hay thất bại) → dùng nó;
   - file chính thiếu, hỏng hay khác phiên bản, last-good đọc được **và có quan sát** → ứng viên last-good. Nó chỉ thành `READY_LAST_GOOD` nếu qua các bước 5–8;
   - cả hai thiếu → `NEEDS_MEASUREMENT`;
   - còn lại (có file hỏng, hoặc chỉ có last-good mà không có quan sát) → `INVALID`.
4. **Hồ sơ là bản thất bại** → `CUDA_UNAVAILABLE`, lời nhắn theo mã lỗi (mục 7.2).
5. **Máy chưa từng có CUDA.** Cần đủ các điều sau:
   - hồ sơ `ok`;
   - `had_cuda` sai ở mọi file đọc được, và không file nào hỏng;
   - `adapters.ok` và không có `10de`;
   - NVML không thấy GPU.

   Khi đó:
   - quan sát thấy NVIDIA (NVML có GPU, hoặc `nvidia_pci_seen` khác `False`) → `STALE_GPU` ("máy có card NVIDIA: Đo lại");
   - quan sát None → giữ kết quả, kèm `not_observed` (`choose()` từ chối);
   - còn lại → nhánh CPU (bước 9), nguồn `no_cuda_machine`.
6. **Nhiều GPU** (trong hồ sơ hoặc trong quan sát) → `MULTI_GPU_UNSUPPORTED`.
7. **So quan sát với `gate_key`** (bảng mục 3.3):
   - NVML không chạy → `CUDA_UNAVAILABLE`;
   - danh sách GPU khác → `STALE_GPU`;
   - torch khác → `STALE_TORCH`;
   - quan sát None: không kiểm được, kèm `not_observed`.
8. **Khớp dòng GPU** (mục 5.2; driver lấy từ quan sát, không có thì từ hồ sơ):
   - dòng `gated` → `READY` (hoặc `READY_LAST_GOOD`);
   - không dòng nào → `NO_GATED_ROW`, ghi rõ khóa không khớp.
9. **Nhánh CPU:**
   - dòng CPU phải khớp torch, lấy từ quan sát; quan sát None → `not_observed`;
   - `runnable_after` khác None → `CPU_ROW_NOT_READY` trên mọi root;
   - dòng `ungated`: root thử → `READY_UNGATED`; thư mục cài → `NO_GATED_ROW` (câu hỏi 2: từ chối);
   - dòng `gated` → `READY`.
10. **Cảnh báo:** `driver_changed`, `ffmpeg_changed`, `machine_changed`, NVDEC (mục 5.5), `last_good_used`, và lưu ý của dòng.

### 7.2 Mã lỗi của lần đo

| `failure.code` | Khi nào | Trạng thái | Lời nhắn (rút gọn) |
|---|---|---|---|
| `cuda_unavailable` | torch không dùng được CUDA, trong khi máy từng có CUDA hoặc đang thấy card NVIDIA | `CUDA_UNAVAILABLE` | "Không dùng được CUDA. Khởi động lại hoặc cài lại driver NVIDIA rồi Đo lại; hoặc bật Chỉ dùng CPU." |
| `torch_probe_timeout`, `torch_probe_crashed` | tiến trình torch quá 120 s; hoặc thoát lỗi, in rác | `CUDA_UNAVAILABLE` | như trên, kèm bước bị lỗi |
| `nvml_failed` | NVML lỗi (không phải thiếu thư viện) | `CUDA_UNAVAILABLE` | "NVML lỗi; kiểm tra driver NVIDIA." |
| `nvml_torch_mismatch` | số GPU hay compute capability của NVML và torch khác nhau | `CUDA_UNAVAILABLE` | "NVML và torch báo khác nhau." |
| `adapters_unreadable` | không có CUDA, và không đọc được danh sách card | `CUDA_UNAVAILABLE` | "Không xác định được máy có card NVIDIA hay không. Bật Chỉ dùng CPU nếu máy không có." |

Lỗi FFmpeg và lỗi phép thử encoder không làm lần đo thất bại (mục 6.3, bước 8).

### 7.3 Bảng tình huống

| Tình huống | Trạng thái | Job mới | Job đã đóng băng |
|---|---|---|---|
| Khớp dòng đã qua cổng, quan sát khớp | `READY` | đóng băng | chạy |
| File chính thiếu hoặc hỏng; last-good hợp lệ và khớp | `READY_LAST_GOOD` + cảnh báo | đóng băng theo last-good | chạy |
| Chưa từng đo (không file, không last-good) | `NEEDS_MEASUREMENT` | từ chối tới lần "Đo lại" đầu (câu hỏi 3) | chạy |
| Hỏng, không có last-good dùng được | `INVALID` | từ chối | chạy |
| Lần đo cuối thất bại (mục 7.2), kể cả sau khi gỡ driver | `CUDA_UNAVAILABLE` | từ chối, **không bao giờ CPU** | `standard_ocr8` và `fast` báo lỗi khi bắt đầu; `standard` (OCR 1, fp32) âm thầm chạy CPU ở stage text cho tới E3 (mục 11) |
| Chưa từng có CUDA, không card NVIDIA | nhánh CPU | `CPU_ROW_NOT_READY` tới E3; từ E3 tới E4 chỉ trên root thử | — |
| Đổi card, hoặc máy CPU nay có card NVIDIA | `STALE_GPU` | từ chối tới khi Đo lại | giữ nguyên dòng lệnh. Khóa cache không chứa GPU, nên kết quả cache cũ vẫn được dùng (B1) |
| Đổi bản torch | `STALE_TORCH` | từ chối tới khi Đo lại | như trên |
| Cập nhật driver, vẫn trong khoảng | `READY` + `driver_changed` | đóng băng; NVDEC theo mục 5.5 | chạy |
| Thay FFmpeg | `READY` + `ffmpeg_changed` | đóng băng; decode cpu, xuất x264 | chạy |
| Hơn một GPU NVIDIA | `MULTI_GPU_UNSUPPORTED` | từ chối, "chưa hỗ trợ nhiều GPU" (câu hỏi 5) | — |
| File công tắc hỏng | `OVERRIDE_INVALID` | từ chối | chạy |
| Công tắc bật khi đã có job CUDA trong hàng | — | job mới chạy CPU | **giữ thiết bị đã đóng băng** (muốn chuyển thì "Chạy lại") |
| Giá trị `hw:{id}` hỏng | — | — | không chạy được, không bao giờ tự đóng băng lại |
| Phép đo quá giờ, bị hủy hay bận | không ghi gì | không đổi | không đổi |

## 8. File và bằng chứng không đổi khóa cache

**Tạo:**
- `src/biliflow/hardware_profile.py`, `hardware_table.py`, `hardware_detect.py`, `hardware_measure.py`, `hardware_manager.py`;
- `scripts/hardware-measure.ps1`;
- `dashboard_v2/hardware-view.js`;
- `tests/test_hardware_profile.py`, `test_hardware_table.py`, `test_hardware_detect.py`, `test_hardware_measure.py`, `test_hardware_manager.py`, `test_hardware_routes.py`;
- `docs/handoff/E-hardware.md`.

**Sửa nhỏ:**
- `control_center.py`: `_hardware(center)` import trễ, một nhánh GET, một nhánh POST, `center.hardware.close()` ở `serve()`/`stop()`, `DASHBOARD_V2_FILES`;
- `phone_access.py`: lý do `PC_ONLY_HARDWARE`, hai dòng `PC_ONLY_POSTS`;
- `dashboard_v2/`:
  - `live.html` (một thẻ `<script>` trong `<head>`), `app.js` (các dòng ở mục 6.2), `adapter.js`, `contracts.js`;
  - `verify.cjs`, `verify-adapter.cjs`;
  - `browser-check.cjs`: ASSETS, mẫu trả lời cho `GET /api/hardware-profile` (READY, NEEDS_MEASUREMENT, đang đo), và hai đường POST trong danh sách `PC_ONLY` của nó;
  - danh sách ASSETS trong `download-fake-server.cjs` và `review-fake-server.cjs`. Hai server này trả 404 cho route mới, và trang hiện "không có" như với Control Center cũ;
- test: `tests/test_dashboard_v2_review.py:84` (`ENDPOINTS_SHA256`), `tests/test_dashboard_v2_phone.py` (bảng route chỉ-PC), `tests/test_cache_dependencies.py:161-167` (thêm `hardware_*.py` vào `CONTROL_PLANE`);
- `CHANGELOG.md`, `docs/PROJECT_STATUS.md`, `docs/SESSION_HANDOFF.md`.

**Ngoại lệ có chủ ý về độ dài file:** `control_center.py` (2.689 dòng) và `phone_access.py` (825 dòng) đã vượt trần 800 dòng từ trước. E2 chỉ thêm vào mỗi file vài dòng nối dây; mọi logic nằm trong `hardware_manager.py`.

**Không đụng:**
- danh sách của bạn: `scheduler.py`, `job_store.py`, `job_pipeline.py`, mọi module quét, `cli.py`, `__main__.py`, `__init__.py`, `run.ps1`, `env.ps1`, `stage_cache.py`, `cache_dependencies.py`, `pyproject.toml`, `config/*`, `models\`;
- thêm vào đó:
  - `nvdec.py`, `routing_prewarm.py`, `textscan.py`, `text_semantics.py`;
  - `final_renderer.py`, `blur_filter.py`, `export_identity.py`, `resource_lock.py`: của A, hoặc A đang import;
  - `review_workflow.py`;
  - `job_purge.py` và `recycle_bin.py`: của C2 (chỉ import);
  - `AGENTS.md`: của C0;
  - `dashboard_v2/index.html`, `dashboard_v2/serve.py`, HTML trang cũ (đã ghim SHA-256, `tests/test_dashboard_v2_route.py:139-153`);
  - `tools\` của thư mục cài (chỉ đọc và chép ra);
  - mọi thứ trong `input\`, `output\`, `reports\`, `archive\` thật.

**Bằng chứng khóa cache không đổi:**
1. Khóa gồm schema, stage, hash nguồn, dấu vân tay code/config/model, hash report đầu vào, dòng lệnh và đường dẫn artifact (`stage_cache.py:184-200`). E0–E2 không đổi dòng lệnh (không sửa `job_pipeline.py` hay `scheduler.py`) và không sửa đầu vào cố định nào (`:45-56`, `:77-98`).
2. Closure code bắt đầu từ `STAGE_MODULES` (`cache_dependencies.py:9-21`) và coi `cli.py` là ranh giới chỉ hash, không đi theo import (`:35-41`). Vì vậy chuỗi `cli → control_center → hardware_*` không bao giờ được theo. `hardware_*` được import module trong closure (`nvdec`), nhưng chiều ngược lại bị cấm và có test.
3. Test:
   - `stage_source_paths` của cả 10 stage không chứa `hardware_*` và không rơi vào "hash toàn bộ source";
   - file bàn giao ghi tập đường dẫn của từng stage ở `40a0125` và ở HEAD; hai tập phải bằng nhau.
4. **Dòng lệnh, so nguyên bộ:**
   - Dựng `pipeline_stages` hôm nay trên root tạm, cho mọi tổ hợp: fast T/F × OCR 1/8 × có/không `source_sha256` × các kiểu nội dung và nhóm detector đủ phủ 10 stage.
   - Với mỗi tổ hợp, argv của từng stage phải **bằng nguyên bộ** argv của bản `standard` OCR 1 (các bộ rỗng) sau khi chèn `speed_arguments(LEGACY_KNOBS[họ], …)` vào ngay sau `"--device", "cuda"`.
   - Họ của dòng 1 với `logo_decode = nvdec` phải bằng `LEGACY_KNOBS`.
   - Bản `logo_decode = cpu` chỉ khác đúng một phần tử (`nvdec` → `cpu`).
   - Đợt 2 chạy lại đúng test này sau khi nối dây.

## 9. Kế hoạch test

- **Cách chạy:** `E:\DungChung\BiliFlow\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_hardware_*.py" -v`, với `PYTHONPATH` là `src` của worktree. Sau đó chạy cả bộ theo `docs/PERF_INSTALL_PLAN.md` mục 3, ý 8 (`:161-164`): một clip tổng hợp 1 s trong `input\` của worktree, xóa sau.
- **E1, thuần:**
  - **schema:** từng lỗi kiểu, enum, khóa lạ, khóa trùng, NaN, BOM, file quá lớn, phiên bản lạ, sai hash, vi phạm kiểm chéo;
  - **thứ tự quyết định:** một bảng dữ liệu test ghi trạng thái mong đợi cho từng tổ hợp. Tổ hợp gồm:
    - file chính {thiếu, hỏng, ok-cuda, ok-không-NVIDIA, ok-không-NVIDIA mà `had_cuda` đúng, thất bại-cuda};
    - last-good {thiếu, hỏng, cuda, không-NVIDIA};
    - công tắc {thiếu, tắt, bật, hỏng};
    - quan sát {None, khớp, mất GPU, đổi GPU, đổi driver trong khoảng, driver ngoài khoảng, NVML `no_library`, NVML `error`, thấy `10DE` mà không có NVML, đổi torch, đổi FFmpeg, 2 GPU}.

    Mọi tổ hợp đều khẳng định: `device == "cpu"` kéo theo công tắc bật, hoặc máy chưa từng có CUDA theo mục 3.5;
  - **các ca bắt buộc:**
    - đo lại sau khi gỡ driver (lần trước ok-cuda) cho `failed`/`cuda_unavailable`, last-good giữ nguyên, kết quả là từ chối, không bao giờ CPU;
    - card NVIDIA chưa có driver, tên "Microsoft Basic Display Adapter", mang `VEN_10DE`;
    - đọc danh sách card quá giờ;
    - `observed=None` không bao giờ cho `READY_LAST_GOOD`;
    - root thử chỉ do `resolve()` tính, không truyền vào được;
  - **bảng:**
    - `row_id` không trùng;
    - mỗi họ thỏa `__post_init__` và đúng một dạng;
    - dòng CPU không fp16 và batch 1;
  - **dòng lệnh:** test so nguyên bộ ở mục 8 ý 4;
  - **`parse_frozen`:**
    - `None` cho `LEGACY_KNOBS` (fast kèm nvdec), dù bảng, hồ sơ hay công tắc nói gì;
    - giá trị rác, khóa lạ cho `FrozenInvalid`, không ném lỗi;
  - **ghi nguyên khối:** bỏ qua `.tmp` còn sót; thử lại khi gặp `PermissionError`;
  - **import sạch,** chạy trong subprocess:
    - import `hardware_profile`/`hardware_table` không kéo torch, pynvml, psutil và không tạo luồng;
    - import `control_center` không kéo torch và không nạp `hardware_*`;
  - **chặn lùi:** `tests/test_job_purge.py:640-649` qua mà không sửa gì; `hardware_*.py` không có `set_setting(`.
- **E1, nhận diện (giả lập):**
  - `FakeSystem` khẳng định mọi lệnh có timeout, không qua shell, có `CREATE_NO_WINDOW` (mẫu `tests/test_tailscale_manager.py:61-94`);
  - các ca:
    - không có thư viện NVML;
    - NVML ok nhưng tiến trình torch báo `cuda_not_available`, quá giờ, sập hay in rác;
    - compute capability hay số GPU của NVML và torch khác nhau;
    - 2 GPU; chỉ có iGPU Intel;
    - thiếu ffmpeg; encoder có trong danh sách nhưng thử thất bại;
    - `nvidia-smi` thiếu hay báo khác NVML: chỉ ghi lại, không đổi kết quả;
  - `observe()` không chạy subprocess;
  - test thật trên Windows (`skipUnless(win32)`) chỉ đọc NVML, registry và psutil. Tiến trình torch không bao giờ chạy trong unit test, vì nó tạo CUDA context.
- **E2, đo (giả lập, database tạm, chạy trong tiến trình):**
  - **khóa:**
    - tên khóa được truyền bằng tham số hàm, dạng `Local\BiliFlowTest-<uuid>`;
    - các ca: bận; bị bỏ rơi; nhả theo thứ tự ngược khi chỉ lấy được một phần; file khóa không bao giờ bị tạo; kiểm lại sau khi lấy khóa;
    - nhả từ luồng khác bị từ chối;
    - argparse không có tùy chọn nào cho mutex hay file khóa;
  - **kiểm rảnh trên SQLite tạm:**
    - tạm dừng hay không, job đang chạy, stage RUNNING, việc tải;
    - không có Control Center thì không mở database (kiểm bằng cách thay hàm kết nối);
    - hàm đọc PID không làm lộ token;
  - **bộ làm sạch** thay tiền tố đường dẫn;
  - **lịch sử số đo:** mang sang bản ghi cùng dấu vân tay, giữ 32 bản, bắt đầu lại khi dấu vân tay đổi;
  - **hàm ngưỡng trễ:**
    - không đổi dưới 10% hay dưới 60 s trên phim tham chiếu;
    - không đổi sau chỉ một lần;
    - bỏ số đo `contended` và `battery`;
    - dòng 2060 (`free_fields` rỗng) không bao giờ đổi;
  - **các đường thoát:** quá giờ thì nhả khóa; file hủy; mã thoát; không ghi gì với 64/70/75/124; thoát 64 trước mọi khóa với root ngoài `allowed_project_root`;
  - **`.ps1`** chạy không quyền admin, chỉ trên các đường từ chối:
    - thiếu `ProjectRoot`, hay đường dẫn tương đối;
    - root không được phép;
    - `GuardRoot` không phải thư mục;
    - `Python` không phải `.venv\Scripts\python.exe`;
    - `Stamp` sai mẫu.

    Kiểm file chỉ có ASCII.
- **E2, manager:** `can_remeasure`; mỗi lúc một việc; quá 15 phút thì ghi file hủy trước rồi mới diệt; `close()` chỉ ghi file hủy; lần khởi động sau thấy lần đo đang chạy; ghi công tắc dưới khóa.
- **E2, route** (khung `tests/test_phone_tailscale.py:193-243`, cổng 0, `center.hardware` giả):
  - PC GET 200; điện thoại GET 403 `pc_only`; POST thiếu token 403; POST từ điện thoại 403 trước khi đọc body;
  - 409 `not_paused`/`not_idle`/`busy`; body `cpu-only` sai kiểu bị từ chối;
  - route đã được phân loại (`tests/test_dashboard_v2_phone_hardening.py:223-230`);
  - `/api/status` không bao giờ gọi manager hay `observe()`: hai hàm đó được thay bằng bản ném lỗi khi bị gọi.
- **Dashboard:**
  - `node --check`;
  - `verify.cjs` (nhánh PC, điện thoại, demo; phần thân hai trang vẫn giống nhau); `verify-adapter.cjs` (404 → `unavailable`);
  - không `fetch(` ngoài `adapter.js`;
  - `browser-check` ở 375 px không tràn ở #settings, với mẫu trả lời READY và NEEDS_MEASUREMENT.
- **Kiểm thật:**
  - Control Center thử ở cổng 8798: các hồ sơ mẫu đẩy trang Cài đặt qua từng trạng thái;
  - một lần đo thật ở khung GPU 3 (mục 6.4). Báo cáo chỉ ghi tóm tắt hồ sơ.

## 10. Câu hỏi và quyết định

**Quyết định của người dùng (2026-10-11):** "Duyệt E0. Theo đề xuất: 1 a+b, 2 từ chối, 3 từ chối, 4 tự tạm dừng, 5 từ chối."

| # | Quyết định | Hệ quả trong hợp đồng |
|---|---|---|
| 1 | (a) + (b) | Phép tự kiểm của E2 là cổng cho từng driver (mục 5.5). Trước khi Đợt 2 bật, cổng framemd5 cả phim L chạy lại ở khung GPU 3; câu trả lời này cũng là lời cho phép đọc bản L cho lần chạy đó |
| 2 | Từ chối | Thư mục cài thật không dùng dòng chưa qua cổng nào (`NO_GATED_ROW`). Dòng CPU chỉ chạy trên root thử tới E4 (mục 5.4, 7.1 bước 9) |
| 3 | Từ chối | `NEEDS_MEASUREMENT` từ chối job mới tới lần "Đo lại" đầu (mục 7.3) |
| 4 | Tự tạm dừng | Nút "Đo lại" bị khóa kèm lý do tới khi hàng đợi đã tạm dừng và rảnh (`can_remeasure`, mục 6.1). Không có nút tự tạm dừng |
| 5 | Từ chối | Hơn một GPU NVIDIA cho `MULTI_GPU_UNSUPPORTED` (mục 5.2, 7.3) |

Các câu hỏi và phương án ban đầu được giữ dưới đây để truy nguồn.

1. **NVDEC trên driver 617.42.**
   - Bối cảnh:
     - tiến trình con khởi động routing đang giải mã bằng NVDEC trong bản thật (`job_pipeline.py:204`);
     - khóa cache routing không tính cách giải mã (`visual_logo_scanner.py:1588-1603`);
     - cổng pixel chỉ mới chạy trên driver 576.80 (`docs/CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md:520`); máy đã chạy 617.42 từ khi cập nhật mà chưa kiểm lại.
   - Chọn:
     - (a) chấp nhận phép tự kiểm framemd5 trên clip tổng hợp của E2 làm cổng cho từng driver. Thất bại thì hạ về cpu; kết quả giống hệt;
     - (b) chạy lại cổng framemd5 cả phim L ở khung GPU 3 trước Đợt 2 (cần bạn cho phép đọc bản L);
     - (c) giải mã cpu cho tới khi kiểm lại. Kết quả giống hệt, nhưng dòng lệnh stage text đổi, nên cache text tính lại. Thời gian mất thêm chưa đo trên code hiện tại:
       - cả việc chạy chồng bằng NVDEC lợi 288 s trên L;
       - một phép đo cũ của việc chạy chồng với giải mã CPU lợi khoảng 144–174 s (`docs/SCAN_PERFORMANCE.md:99-103`);
       - vậy ước chừng mất thêm 110–150 s trên L.
   - **Đề xuất:** (a) ngay trong E2, cộng (b) trước khi Đợt 2 bật.
2. **Dòng chưa qua cổng trên thư mục cài thật:** card, torch hay driver chưa đo (đường fp32 tham chiếu trên GPU), và đường CPU sau E3 nhưng trước E4.
   - **Đề xuất (đã đổi sau review): từ chối.**
     - Kế hoạch đã duyệt chỉ bật cách chạy mới sau khi qua cổng, và xếp "CPU fp32" vào nhóm thay đổi số học cần cổng Golden (`docs/PERF_INSTALL_PLAN.md:143-146`). Lựa chọn chỉ lấy từ bảng đã qua cổng (`:150`). "Cho phép" trái với hai điều đó.
     - Một ứng viên bị bỏ sót trên đường chưa qua cổng không bao giờ hiện trong danh sách duyệt, nên nhãn "chưa qua cổng" trên thẻ không giúp người duyệt bắt được nó.
     - Trên Ampere trở lên, cuDNN mặc định dùng TF32 cho phép tích chập "fp32", nên kết quả có thể lệch đường tham chiếu trên 2060 (kiến thức chung, chưa kiểm).
     - Hệ quả: bảng 1 không có dòng GPU chưa qua cổng; dòng CPU chỉ chạy trên root thử tới E4.
   - Hoặc: cho phép, nhưng chỉ qua một công tắc riêng cho từng root trên PC, có hộp xác nhận giải thích rằng chỗ bị bỏ sót sẽ không ai thấy. E2 không làm công tắc này.
3. **Chưa có hồ sơ khi Đợt 2 bật.**
   - **Đề xuất:** từ chối job mới tới khi bạn bấm "Đo lại" một lần. Bấm ngay sau khi khởi động lại; cần tạm dừng hàng đợi.
   - Hoặc: giữ đúng hành vi hôm nay tới lần đo đầu.
4. **"Đo lại" khi hàng đợi đang chạy.**
   - **Đề xuất:** bạn tự tạm dừng trước; nút bị khóa kèm lý do, và tạm dừng vẫn là thao tác của bạn.
   - Hoặc: nút tự tạm dừng hàng đợi.
5. **Máy có hơn một GPU NVIDIA.**
   - **Đề xuất:** từ chối ("chưa hỗ trợ nhiều GPU"), vì CLI không chọn được thiết bị và thứ tự của torch có thể khác NVML.
   - Hoặc: dùng thiết bị 0 kèm cảnh báo.

**Ghi nhận (không cần chọn):**
- `hw:{id}` chỉ được ghi từ Đợt 2, theo đúng danh sách cấm sửa của bạn (`job_store.py`).
- Các route mới vào danh sách "agent không bao giờ gọi" qua C0, trước khi G1 gộp chúng. Cùng mục đó: agent không bao giờ chạy `scripts/hardware-measure.ps1` hay `python -m biliflow.hardware_profile measure` với `-ProjectRoot` là thư mục cài. Code trong worktree đã tự từ chối điều này; code của chính thư mục cài thì cho phép, để bạn đo.
- Công tắc "Chỉ dùng CPU" được ghi ngay trong E2, và có hiệu lực cho job mới từ Đợt 2. Hộp xác nhận nói rõ điều đó.

## 11. Chỗ kế hoạch và code lệch nhau, và phát hiện cho session khác

**Kế hoạch so với code:**

| Kế hoạch | Thực tế | Cách xử lý |
|---|---|---|
| `freeze_for_job` thuộc API E0 (`PERF_INSTALL_PLAN.md:183`) | Chưa ghi được trước khi `job_store.py:148` đổi (`tests/test_job_purge.py:640-649`) | E1 giao phần thuần; phần ghi ở Đợt 2 (khớp `:208`, `:561`) |
| Nhận diện bằng `nvidia-smi` (`:191`) | NVML đã là phụ thuộc và đang chạy trong tiến trình (`control_center.py:589-599`) | NVML quyết định; `nvidia-smi` lấy từ System32 chỉ để đối chiếu |
| "Clip mẫu đi kèm" (`:205`) | Repo không có clip; `*.mp4` bị gitignore | Clip lavfi cố định, tạo mỗi lần đo |
| Đo xuất chỉ lấy `state/final-render.lock` (`:160`) | Bản xuất của Control Center lấy `Local\BiliFlowFinalRender` trước (`scripts/run.ps1:25-26`) | Lấy cả hai mutex và file khóa, theo thứ tự cố định |
| "Lựa chọn kèm lý do" lưu trong hồ sơ (`:179`) | Lựa chọn phụ thuộc bảng trong code | Không lưu; `choose()` và GET luôn tính lại |
| Ngưỡng trễ cho cách giải mã và số worker (`:206`) | Bảng 1 không có núm nào chọn theo thời gian: giải mã theo cổng pixel, worker cố định trong khóa cache | E2 cài hàm và lịch sử số đo; chưa có gì gọi hàm đó tới B1 |
| `SCAN_PERFORMANCE.md:129-131` cho số 2,1e-5 (`:95`) | Số đó nằm ở `:152`; dòng 129-131 là 3,6e-6 | Sửa trích dẫn ở đợt tài liệu |
| `SCAN_PERFORMANCE.md:93-95` "NVDEC chậm khi CPU rảnh" (`:206`) | Đoạn đó tả bản NVDEC cũ; số hiện tại ở `CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md:446-457` | Kết luận không đổi |
| "device và precision luôn trên dòng lệnh" (`:152`) | fp32 hôm nay là mặc định ngầm của CLI | Giữ ngầm để dòng lệnh giống từng byte; B1 quyết định |

**Phát hiện cho session khác** (E không sửa; đã ghi vào file bàn giao):
- **E3:** ở họ `standard` (OCR 1, fp32), stage text âm thầm chạy EasyOCR (`textscan.py:604-607`) và text-semantics (`text_semantics.py:61-74`) trên CPU khi CUDA lỗi, mà report vẫn ghi `device=cuda` (`textscan.py:864`).
  - `standard_ocr8` và `fast` thì báo lỗi rõ (`textscan.py:614-617`, `ocr_precision.py:44-45`).
  - Phải sửa trước khi Đợt 2 bật.
- **B1:**
  - khóa cache không có GPU, driver hay torch (`stage_cache.py:42-99`), và `uv.lock` liệt kê ở `:55` không có ở thư mục cài. Vì vậy đổi card hay torch vẫn dùng lại kết quả fp16 cũ;
  - số worker và cách giải mã đã nằm ngoài khóa cache routing (`visual_logo_scanner.py:1588-1603`);
  - `run.ps1` không bắt `AbandonedMutexException` (`:31-36`);
  - `__init__.py` ghi 0.7.24 còn `pyproject.toml` ghi 0.7.19.
- **E4:** cache ảnh của DINO/SigLIP không tính thiết bị và dtype (`ad_candidate_pipeline.py:217-232`), nên cổng CPU phải dùng root mới.
- **Tài liệu:** `docs/SCAN_PERFORMANCE.md:140-143` chứa byte 0x08 ở chỗ `\b` bị nuốt.
