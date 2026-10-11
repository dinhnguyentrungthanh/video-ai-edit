# Bàn giao C – Bộ cài, phần code thuần
- Nhánh: `feat/installer` (từ `origin/main` `40a0125`) · Commit cuối: xem `git log -1` · Cập nhật: 2026-10-11
- Chạy ở: Cloud · Trạng thái: đang làm (C0 chờ người dùng duyệt; C2, C6-lõi xong; tiếp C1)

## Đã xong
- **C0:** soạn đề xuất sửa `AGENTS.md`, `LICENSE`, `THIRD_PARTY_NOTICES.md` (mục "Đề xuất C0" bên dưới). **Chưa sửa** `AGENTS.md`, chưa tạo `LICENSE`, chưa sửa `THIRD_PARTY_NOTICES.md`. Không có test (chỉ tài liệu).

- **C2:** `src/biliflow/install_paths.py` mới: `install_root()` tìm file đánh dấu `biliflow-install.json` (`{"biliflow_install": 1, "layout": "source"}`, file thường, không phải liên kết, ≤ 4 KiB, giá trị phải đúng số nguyên 1) ở thư mục gần nhất trong 3 cấp trên thư mục gói (`src`, gốc checkout, thêm 1 cấp cho bố cục bản build). Không biến môi trường, không tham số. Không thấy thì `InstallRootNotFound` (không đoán). File đánh dấu được commit ở gốc repo, nên mỗi checkout/worktree vẫn là gốc của chính nó như `parents[2]` trước đây; worktree trong `temp\` mất file đánh dấu thì báo lỗi, không mượn gốc của thư mục cài chính (vì gốc đó cách 4 cấp).
  - `recycle_bin.INSTALL_ROOT = install_root()` (nên `job_purge.INSTALL_ROOT`, `allowed_project_root`, kho phiên, trình quản lý tài khoản, bước thử tải đều theo); mặc định `--project-root` của `download_tools audit` và `--root` của `performance_report` cũng dùng `install_root()`. **Không sửa** `cli.py`, `control_center.py` (`parents[2]` ở đó đi theo B1/C-nối), `scripts/download_models.py` (để C1).
  - Test: `tests/test_install_paths.py` 13 test OK (gốc checkout = `parents[2]` như cũ; worktree tự là gốc; worktree mất file đánh dấu không mượn gốc chính; giới hạn 3 cấp; file đánh dấu sai/thư mục/liên kết bị từ chối; biến môi trường không đổi được gốc; `allowed_project_root` giữ nghĩa; không stage nào trong `CACHEABLE_STAGES` đọc các file đã sửa).
  - Khóa cache: so `stage_source_paths` + SHA-256 từng file của cả 10 stage giữa `origin/main` và nhánh này: **giống hệt**. File đánh dấu không nằm trong danh sách `_tree_fingerprint`.
  - Test liên quan trên Linux: `test_recycle_bin`, `test_job_delete`, `test_source_cleanup`, `test_download_tools`, `test_performance_report`: 156 test, 24 fail + 26 error **giống hệt trên `origin/main`** (test chỉ chạy trên Windows: ctypes/Thùng rác/ACL). PC chạy lại ở G1.
- **C6-lõi:** `src/biliflow/activation.py` mới, **chưa nối** vào Control Center hay CLI.
  - Mã máy: 3 HMAC-SHA256 có muối (8 byte mỗi phần) của MachineGuid (registry, `KEY_WOW64_64KEY`), UUID bo mạch (bảng SMBIOS qua `GetSystemFirmwareTable('RSMB')`, kiểu 1; UUID giả như toàn 00/FF, `03000200-0400-0500-0006-000700080009` coi là không biết) và serial ổ đĩa hệ thống (`GetVolumeInformationW` trên ổ chứa Windows; cài lại Windows thì đổi, nên chỉ còn khớp 1/3). Phần không đọc được = 8 byte 0, không bao giờ khớp. Khớp ≥ 2/3. Mã máy dạng base32 48 ký tự (8 nhóm 6) có checksum 5 byte bắt lỗi gõ; chấp nhận chữ thường, khoảng trắng, 0/1/8 thay O/I/B.
  - Key: 43 byte dữ liệu (phiên bản, loại, số tháng, mã bộ uint32, ngày cấp, ngày hết hạn = ngày đầu tiên không dùng được, mã key 8 byte ngẫu nhiên, 3 hash mã máy) + chữ ký Ed25519 64 byte; base32 172 ký tự, nhóm 4; mã hóa duy nhất (bit thừa phải là 0). Không có tên khách. Lưu `state/activation/key.json`.
  - Khóa tin cậy: chỉ `RELEASE_PUBLIC_KEY_HEX` (**đang để trống** → mọi kiểm tra trả `NOT_CONFIGURED` cho tới khi người dùng tạo cặp khóa thật trên PC và đặt khóa công khai vào đây); khóa test bị từ chối kể cả khi đặt vào đó (`TEST_PUBLIC_KEY_SHA256`). Test chọn khóa test bằng cách patch `_trusted_public_key`.
  - Đồng hồ: `state/activation/clock.json` giữ thời điểm muộn nhất từng thấy, có HMAC cục bộ suy từ định danh của máy. Chậm hơn > 48 h → `CLOCK_BACK`; trong 48 h thì kiểm hạn theo thời điểm muộn hơn. Key tháng mất/sửa file đồng hồ → `NEEDS_RECHECK` (nhập lại key; ngày cấp key là mốc dưới). Key vĩnh viễn tự ghi lại file đồng hồ. Ghi tối đa mỗi 10 phút, không bao giờ lùi; lỗi ghi trong `check()` không làm hỏng kết quả.
  - Trạng thái: `ACTIVE`, `NOT_CONFIGURED`, `NO_KEY`, `INVALID`, `WRONG_MACHINE`, `EXPIRED`, `CLOCK_BACK`, `NEEDS_RECHECK`, `UNAVAILABLE` (thông báo tiếng Việt trong `MESSAGES`).
  - Công cụ cấp key `keygen/issue_key.py` (ngoài gói, bộ cài không được chép thư mục `keygen/`): `init` (tạo cặp khóa, mã hóa bằng mật khẩu ≥ 12 ký tự, không ghi đè, in khóa công khai), `issue` (cấp cả bộ: `--machine` lặp lại, `--set`, `--kind perpetual|month:N`, `--customer`; ghi sổ CSV), `reissue` (giữ bộ, loại, hạn gốc, mã key mới; tối đa 3 lần mỗi key gốc trừ khi `--force`; key gốc hết hạn thì từ chối), `verify`. Từ chối khóa bí mật và sổ nằm trong checkout Git hay thư mục cài BiliFlow (có `.git` hoặc `biliflow-install.json` ở thư mục cha).
  - Cặp khóa **TEST ONLY**: `tests/fixtures/activation/` (có README).
  - Test: `tests/test_activation.py` 36 test, OK trên Python 3.11 và 3.13 (1 skip: đọc định danh thật chỉ chạy trên Windows; PC chạy ở G1). Bắt được và đã sửa: mật khẩu có dấu tiếng Việt làm pycryptodomex lỗi (giờ mã hóa UTF-8).
  - Khóa cache: `activation.py` không thuộc stage nào (thêm vào `CacheKeyTest`).

## Đang dở
- (trống)

## Bước tiếp theo
1. C1: script chỉ đọc tạo `config/models.lock.json`, cập nhật `scripts/download_models.py` theo lock.

## Cách kiểm
- C0: người dùng đọc mục "Đề xuất C0".
- C6-lõi: `python -m unittest tests.test_activation -v` (cần `pycryptodomex`, có trong `.venv`). Trên PC, `test_this_pc_has_at_least_two_known_parts` đọc định danh thật (chỉ đọc).
- C2: `python -m unittest tests.test_install_paths -v` (PC: `.venv` của thư mục cài, `PYTHONPATH` trỏ vào `src` của worktree); cổng: khóa cache mọi stage không đổi.

## Chờ người dùng
- Duyệt C0 (ba khối A, B, C bên dưới). Sau khi duyệt, session nào áp dụng thì chép nguyên văn; điền tên chủ sở hữu vào `LICENSE`.
- Repo phải Private trước bản build đầu tiên (C7).
- C6: người dùng tự chạy `keygen\issue_key.py init --private-key <ngoài Git và ngoài BiliFlow>` trên PC, sao lưu file + mật khẩu, rồi đưa khóa công khai (hex) vào `RELEASE_PUBLIC_KEY_HEX` (lúc C6-nối). Agent không chạy bước này.

## Lưu ý cho người tiếp quản
- PC chạy Python 3.11 (`pyproject.toml`: `>=3.11,<3.12`): không dùng cú pháp 3.12+.
- Không sửa: `scheduler.py`, `final_renderer.py`, `job_pipeline.py`, module quét, `control_center.py`, `cli.py`, `__main__.py`, `__init__.py`, `run.ps1`, `env.ps1`, `stage_cache.py`, `cache_dependencies.py`, `pyproject.toml`, `config/*` (trừ file lock mới), `models\`.
- Không bao giờ tạo hay đưa khóa bí mật thật vào repo.
- C6-nối (Đợt 3): server chỉ để kích hoạt trước `ControlCenter()`, chặn CLI, trang kích hoạt, tạm dừng scheduler ở ranh giới stage khi key tháng hết hạn, giờ internet (tùy chọn) qua client mạng đã kiểm của trình tải; cập nhật vai trò pycryptodomex trong `config/download_tools.json` (thuộc `config/*`, chưa sửa).
- C5 (bộ cài) phải ghi `biliflow-install.json` ở gốc thư mục cài **trước** khi chạy bất kỳ code BiliFlow nào; C4 phải giữ gói `biliflow` trong 3 cấp dưới gốc cài (`MAX_LEVELS_ABOVE_PACKAGE`).
- Môi trường cloud: Python 3.13 hệ thống, không có `.venv`; `pycryptodomex==3.23.0` (đúng bản trong lock) cài vào venv tạm của session để chạy test C6.

---

## Đề xuất C0

### A. Sửa `AGENTS.md` (văn bản tiếng Anh để chép nguyên văn)

**A1. Thư mục cài thay cho `E:\DungChung\BiliFlow`.** `<install>` là thư mục có file đánh dấu (C2). Trên PC hiện tại `<install>` vẫn là `E:\DungChung\BiliFlow`.

- Dòng 17 (nhóm xóa/lưu trữ), thay `under \`E:\DungChung\BiliFlow\temp\`` bằng:
  > under `<install>\temp` (on this PC `E:\DungChung\BiliFlow\temp`)
- Dòng 19 (trình tải), thay `a temporary project root under \`E:\DungChung\BiliFlow\temp\`` bằng:
  > a temporary project root under `<install>\temp`
- Mục tài khoản nguồn, câu "Tests use fakes and temporary roots under `E:\DungChung\BiliFlow\temp`." thay bằng:
  > Tests use fakes and temporary roots under `<install>\temp`.
- Dòng 44, thay cả câu đầu bằng:
  > Keep runtime, models, caches, temporary files, reports, and outputs inside the install folder `<install>`: the folder that holds the install marker `biliflow-install.json` (`biliflow.install_paths.install_root()`; on this PC `E:\DungChung\BiliFlow`). Do not move project data out of it, to drive C or elsewhere. An install folder is never under `%ProgramFiles%` (the installer refuses it). The one exception is the Tailscale program in `%ProgramFiles%\Tailscale` (see the Tailscale invariant). `archive\` holds user-archived source videos; treat it like `input\`.
- Dòng 41 (Tailscale), thay đoạn "its SYSTEM service must not run from the BiliFlow tree, which every local account can modify, so this program is the one runtime outside `E:\DungChung\BiliFlow`" bằng:
  > its SYSTEM service must not run from the BiliFlow install folder, where local accounts can modify files (every file in a source checkout such as this PC's; the data folders of an installed copy), so this program is the one runtime outside `<install>`

**A2. Công cụ cần duyệt trước khi tải.** Thêm vào "Project invariants", ngay sau dòng 44:
> - Downloading or installing a build or install tool needs the user's approval first, every time: Nuitka, Inno Setup, any JavaScript obfuscator, uv, and the CPU build of torch included. Name the package, version, license and the folder inside `<install>` before asking.

**A3. Kích hoạt.** Thêm một mục mới vào "Project invariants":
> - Activation (`docs/PERF_INSTALL_PLAN.md` section 7, C6). A key is an Ed25519 signature over the machine code, set id, kind (`month:N` or `perpetual`), issue date, expiry and key id; it never carries a customer name. Keys live in `<install>\state\activation\`, which the "do not silently delete `state`" rule covers.
>   - The issuing private key exists only on the user's PC, outside Git and outside every BiliFlow folder, and the user keeps its backup. Agents never create, read, copy, print or move a real issuing key, never run the key-issuing tool (`keygen/`, never shipped by the installer) with a real key, and never issue, extend or reissue a key.
>   - The customer ledger (who got which key) is kept outside Git and outside every BiliFlow folder. Agents never read or write it.
>   - The only key pair in the repository is the test pair in `tests/fixtures/activation/`, marked TEST ONLY. A release build carries its public key as a compile-time constant and never accepts the test key; only a test or source run started by the test suite may select the test key.
>   - Agents never call the activation routes, "Đo lại" or "Chỉ dùng CPU" on the user's real Control Center; they use a test Control Center with a temporary root and its own port.

**A4. Repo Private.** Thêm vào mục "Never auto-publish or upload":
> - The repository must be Private before GitHub Actions makes the first installer build. Builds are stored as workflow artifacts; only the user creates a Release.

### B. File `LICENSE` mới (độc quyền)

Điền `<tên chủ sở hữu>` trước khi tạo file. Văn bản đề xuất:

```text
BiliFlow
Copyright (c) 2026 <tên chủ sở hữu>. All rights reserved.

This software and its source code, compiled modules, installer, documentation
and associated files (the "Software") are proprietary and confidential.

No license is granted to copy, modify, merge, publish, distribute, sublicense,
sell, reverse engineer, decompile or disassemble the Software, in whole or in
part, except as expressly permitted in a separate written agreement or by a
valid activation key issued by the copyright holder for one machine. An
activation key permits use of the installed Software on the machine it was
issued for, for the period it states, and nothing else.

Third-party components shipped with or downloaded by the Software are
licensed under their own terms, listed in THIRD_PARTY_NOTICES.md. Nothing in
this license limits rights granted by those terms.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED. IN NO EVENT SHALL THE COPYRIGHT HOLDER BE LIABLE FOR ANY CLAIM,
DAMAGES OR OTHER LIABILITY ARISING FROM THE USE OF THE SOFTWARE.
```

Ghi chú:
- Code đã từng công khai trên GitHub. `LICENSE` không thu hồi được các bản đã bị sao; nó chỉ nói rõ không có giấy phép nào được cấp (vốn là mặc định khi repo không có `LICENSE`).
- FFmpeg (GPLv3) chạy như **chương trình riêng** (`ffmpeg.exe` qua subprocess), không liên kết vào code BiliFlow, nên BiliFlow giữ được giấy phép độc quyền; nghĩa vụ GPLv3 chỉ áp cho chính FFmpeg (mục C).

### C. Sửa `THIRD_PARTY_NOTICES.md`

Đổi tiêu đề thành `# Third-party notices` và thêm hai mục sau vào cuối file (văn bản để chép nguyên văn; phiên bản lấy từ lock khi build):

```markdown
## Components shipped by the installer

The installer downloads or ships the components below. Each keeps its own license; their full license texts and NOTICE files are copied into `<install>\licenses\` at install time.

| Component | License | Notes |
|---|---|---|
| CPython runtime (`runtime\python`) | PSF-2.0 (bundled OpenSSL Apache-2.0, others per its notices) | python-build-standalone build installed by uv |
| PyTorch, torchvision (cu126 or CPU build) | BSD-3-Clause | The CUDA build includes NVIDIA CUDA runtime libraries under the NVIDIA CUDA Toolkit EULA (redistributable runtime components) |
| Playwright for Python (with its bundled Node.js driver) | Apache-2.0 (Node.js: MIT and its own notices) | Uses the Microsoft Edge already installed in Windows; no browser is shipped |
| pycryptodomex | BSD-2-Clause AND public domain | Also verifies activation keys (Ed25519) |
| Nuitka runtime parts in compiled modules | Apache-2.0 | Build tool; only its runtime helpers end up in the compiled modules |
| Inno Setup (installer engine) | Inno Setup License | Free, including commercial use; origin must not be misrepresented |
| Deno | MIT | |
| FFmpeg | GPL-3.0-or-later (build configured with GPL codecs such as x264/x265) | See "FFmpeg" below |

Model weights keep the licenses in the table at the top of this file. For each Apache-2.0 model, the installer copies the upstream `LICENSE` and, when the model repository has one, its `NOTICE` file next to the weights. For each MIT model, it copies the upstream copyright notice and license text.

## FFmpeg

BiliFlow runs `ffmpeg.exe` and `ffprobe.exe` as separate programs; it does not link FFmpeg. The FFmpeg build is configured with `--enable-gpl` and without `--enable-nonfree`; the installer refuses a nonfree build. The corresponding source code of the exact FFmpeg build (including the x264/x265 sources and build scripts) is offered at <source URL or included archive> for at least three years from the date of each installer release. FFmpeg is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 3 or later.
```

Còn phải quyết khi làm C5 (không chặn C0):
- FFmpeg: kèm nguồn tương ứng trong bộ cài, hay tải FFmpeg lúc cài từ bản build có SHA ghim sẵn (khi đó người phân phối là nơi build FFmpeg, bộ cài chỉ trỏ tới).
- `config/download_tools.json:71-76`: thêm vai trò "kiểm chữ ký key kích hoạt (Ed25519)" cho pycryptodomex. File này thuộc `config/*`, đi theo đợt B1 hoặc lúc nối C6, không sửa trong Phase 1.
