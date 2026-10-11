# Bàn giao C – Bộ cài, phần code thuần
- Nhánh: `feat/installer` (từ `origin/main` `40a0125`) · Commit cuối: xem `git log -1` · Cập nhật: 2026-10-11
- Chạy ở: Cloud · Trạng thái: đang làm (C0 xong, chờ người dùng duyệt; tiếp C2)

## Đã xong
- **C0:** soạn đề xuất sửa `AGENTS.md`, `LICENSE`, `THIRD_PARTY_NOTICES.md` (mục "Đề xuất C0" bên dưới). **Chưa sửa** `AGENTS.md`, chưa tạo `LICENSE`, chưa sửa `THIRD_PARTY_NOTICES.md`. Không có test (chỉ tài liệu).

## Đang dở
- (trống)

## Bước tiếp theo
1. C2: `install_root()` bằng file đánh dấu.
2. C6-lõi: `activation.py`, công cụ cấp key ngoài gói, cặp khóa test.
3. C1: script chỉ đọc tạo `config/models.lock.json`, cập nhật `scripts/download_models.py` theo lock.

## Cách kiểm
- C0: người dùng đọc mục "Đề xuất C0".

## Chờ người dùng
- Duyệt C0 (ba khối A, B, C bên dưới). Sau khi duyệt, session nào áp dụng thì chép nguyên văn; điền tên chủ sở hữu vào `LICENSE`.
- Repo phải Private trước bản build đầu tiên (C7).

## Lưu ý cho người tiếp quản
- PC chạy Python 3.11 (`pyproject.toml`: `>=3.11,<3.12`): không dùng cú pháp 3.12+.
- Không sửa: `scheduler.py`, `final_renderer.py`, `job_pipeline.py`, module quét, `control_center.py`, `cli.py`, `__main__.py`, `__init__.py`, `run.ps1`, `env.ps1`, `stage_cache.py`, `cache_dependencies.py`, `pyproject.toml`, `config/*` (trừ file lock mới), `models\`.
- Không bao giờ tạo hay đưa khóa bí mật thật vào repo.

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
