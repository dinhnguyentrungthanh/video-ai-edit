# Bàn giao C – Bộ cài, phần code thuần
- Nhánh: `feat/installer` (từ `origin/main` `40a0125`) · Commit cuối: xem `git log -1` · Cập nhật: 2026-10-11 (PC)
- Chạy ở: PC (tiếp quản ngày 2026-10-11; session cloud C đã dừng, từ giờ chỉ session C trên PC commit vào nhánh này) · Trạng thái: Phase 1 xong; chờ người dùng (sửa script lock, xem "Bước tiếp theo" 1) và chờ G1
- **feat/installer sẵn sàng cho G1 tại commit `c0d2bc3`** (code + test; commit bàn giao này nằm ngay trên và chỉ sửa file này; G1 gộp đầu nhánh). Còn mở: `config/models.lock.json` chưa commit (6 dòng `!`) và 1 test chỉ fail trên Windows, lỗi nằm ở test chứ không ở code (mục "PC tiếp quản" bên dưới). G1 gộp được C2, C6-lõi, C1-script ngay; lock vào sau khi sửa.

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
- **C1 (phần cloud):**
  - `scripts/models_lock.py` mới, **chỉ đọc**: với 9 model production (`easyocr`, `nsfw_detection_2_nano`, `image_safety_classifier_m`, `wd_vit_tagger_v3`, `multilingual_minilm_text_semantics`, `vit_base_violence_detection`, `qwen2_vl_2b_instruct`, `florence_2_base`, `grounding_dino_tiny`) đọc `models\<thư mục>\manifest.json` (repo, revision), hash SHA-256 từng file trên đĩa (bỏ `manifest.json`, `.cache`), giữ nguyên văn manifest (để bản cài mới ghi đúng từng byte), đối chiếu với danh sách files trong manifest. EasyOCR: lấy URL gói nén từ `easyocr/config.py` của `.venv` bằng `ast` (không import). Chỉ ghi `--out` (từ chối nếu nằm trong `models\`, không ghi đè nếu thiếu `--replace`); không tải gì. Vấn đề (file lệch manifest, revision không phải 40 hex, đường dẫn cục bộ trong manifest, model không APPROVED, liên kết) ghi vào `problems`, mã thoát 1.
  - `scripts/download_models.py`: mặc định **tải theo lock** (`config/models.lock.json`): đúng revision và danh sách file; tải tiếp từ `.part` khi rớt mạng (Range, thử lại 5 lần 2–30 s; máy chủ không hỗ trợ Range thì tải lại từ đầu); kiểm kích thước + SHA-256 trước khi đổi tên; manifest ghi cuối, đúng từng byte trong lock; chỉ https, chuyển hướng sang http bị chặn. Thư mục model đã có file khác lock hay file lạ → từ chối, không ghi đè (không bao giờ sửa model sẵn có). Thêm `qwen2-vl-2b` và `easyocr` (EasyOCR: tải gói zip của bản phát hành, lấy đúng một file, kiểm SHA-256 của `.pth`). Cần `biliflow-install.json` ở gốc. Cách tải cũ (revision mới nhất) giữ lại sau cờ `--latest`, chỉ để thử trong thư mục dev.
  - Test: `tests/test_models_lock.py` 11 test OK (mạng giả, không chạm `models\` thật hay mạng).
  - **Chưa làm** (cần PC hoặc người dùng): lock có hash cho `.venv` cu126 và bản CPU; lock riêng và cách dựng lại `runtime\florence-python`; quyết định đóng gói `annotations/text_semantics_seed_v1.json` (kiểm tên thật trước).
- **PC tiếp quản (2026-10-11):** worktree `temp\wt-installer` (`git worktree add temp\wt-installer -b feat/installer origin/feat/installer`), đầu nhánh `c0d2bc3`, commit cuối không phải `wip:`. `origin/main` đã có thêm 3 commit chỉ sửa tài liệu sau điểm tách `40a0125` (`7d8b8bc`, `2d8240f`, `34df52c`), không đụng code.
  - **C1, chạy `scripts/models_lock.py` trên PC** (`.venv` của thư mục cài, `--root E:\DungChung\BiliFlow --out temp\wt-installer\config\models.lock.json`):
    - Chạy 23 s (file model đã nằm trong cache đĩa), mã thoát 1, khóa đủ 9 model, `not_locked` trống.
    - `models\` không đổi: 137 file, đường dẫn, kích thước và mtime trước và sau giống hệt.
    - 6 dòng `!`. **Không dòng nào là file model lệch manifest:**
      1. `easyocr/.gitkeep`: "trên đĩa có nhưng manifest không ghi". File giữ chỗ 1 byte, không phải model; `models\` không nằm trong Git.
      2. `craft_mlt_25k.pth`: "kích thước hoặc SHA-256 khác manifest". **Báo nhầm:** manifest EasyOCR ghi SHA-256 bằng chữ hoa và kích thước dưới khóa `size_bytes`, còn script so phân biệt hoa/thường và đọc khóa `bytes`. So không phân biệt hoa/thường thì SHA-256 giống hệt; kích thước cũng giống.
      3. `latin_g2.pth`: như dòng 2, báo nhầm.
      4. `.gitkeep`: "không thấy trong easyocr/config.py". Hệ quả của dòng 1.
      5. `latin_g2.pth`: "không thấy trong easyocr/config.py". **Lỗi script:** `parse_easyocr_config` gọi `ast.literal_eval` trên cả dict `recognition_models`. Dòng 101 trong `config.py` của EasyOCR 1.7.2 dùng `''.join(separator_char) + …`, nên `literal_eval` hỏng và cả dict bị bỏ; chỉ đọc được `detection_models`. Vì vậy lock thiếu URL của `latin_g2.pth` và `download_models.py` không tải được file này.
      6. `florence_2_base/__pycache__/processing_florence2.cpython-311.pyc`: bytecode do Python tự sinh (2026-09-23) khi nạp code `trust_remote_code` của Florence, không phải file model.
    - **Lock chưa commit:** người dùng quyết định còn dòng `!` thì chưa commit.
      - File đã sinh vẫn nằm trong worktree, **chưa được Git theo dõi**: `config/models.lock.json` (30.863 byte; không có đường dẫn cục bộ, tên phim hay link).
      - Không `git add` file này trước khi sửa script và chạy lại ("Bước tiếp theo" 1).
  - **Test trên Windows:**
    - Môi trường: Python 3.11.16, `.venv` của thư mục cài, `PYTHONPATH` = `src` của worktree; đã kiểm `biliflow` được nạp từ worktree.
    - 8 module: `test_install_paths`, `test_activation`, `test_models_lock`, `test_recycle_bin`, `test_job_delete`, `test_source_cleanup`, `test_download_tools`, `test_performance_report`. Kết quả: **216 test, 1 fail, 3 skip**, 21 s.
    - **So với `origin/main` `34df52c`** (worktree tách rời, chạy cùng 5 module đã có ở đó):
      - main: 156 test OK, 1 skip; nhánh này: cùng 156 test OK, cùng 1 skip (test Thùng rác thật, chỉ chạy khi có `BILIFLOW_TEST_RECYCLE_BIN=1`).
      - **Không có lỗi có sẵn nào, và không có lỗi mới trong 5 module đó.**
      - 24 fail + 26 error thấy trên Linux đều thuộc test chỉ chạy được trên Windows; trên PC đều qua.
    - **Test mới:**
      - `test_install_paths`: 13 OK.
      - `test_activation`: 36 test, 34 OK + 2 skip. `test_a_linked_activation_folder_is_refused` bị bỏ vì tạo symlink cần quyền thêm trên Windows; `test_reading_the_identity_needs_windows` chỉ chạy ngoài Windows. `test_this_pc_has_at_least_two_known_parts` (đọc định danh thật, chỉ đọc) OK.
      - `test_models_lock`: 11 test, 10 OK + 1 fail (dưới đây).
    - **1 fail mới, chỉ trên Windows, lỗi ở test chứ không ở script:** `test_models_lock.ModelsLockTest.test_a_hugging_face_model_is_locked_from_its_manifest_and_files` (`tests/test_models_lock.py:114`).
      - Fixture ghi `manifest.json` bằng `write_text`; trên Windows `\n` thành `\r\n`.
      - Test so `manifest_text` với `read_text`, vốn đổi `\r\n` về `\n`.
      - Script giữ nguyên văn từng byte (`\r\n`), đúng như thiết kế.
      - Cách sửa: fixture ghi bằng `write_bytes(json.dumps(...).encode("utf-8"))`, hoặc test so với `read_bytes().decode("utf-8")`. Chưa sửa (chờ người dùng).

## Đang dở
- (trống). `config/models.lock.json` có trong worktree nhưng chưa được Git theo dõi; xem "PC tiếp quản" ở trên.

## Bước tiếp theo
1. **Khi người dùng đồng ý** (họ quyết: còn dòng `!` thì chưa commit lock):
   - Sửa `scripts/models_lock.py`:
     - so SHA-256 không phân biệt hoa/thường, và đọc kích thước từ cả `bytes` lẫn `size_bytes`;
     - bỏ `.gitkeep`, `__pycache__/` và `*.pyc` khỏi danh sách file;
     - đọc `easyocr/config.py` theo từng mục: đi qua cây AST, lấy `filename`/`url`/`md5sum` là hằng ở từng dict con, bỏ qua giá trị không phải hằng như `characters`.
   - Kiểm `download_models.py` cũng bỏ qua `__pycache__` khi so thư mục model đã có.
   - Sửa test chỉ fail trên Windows (ở trên) và thêm test cho 3 trường hợp này.
   - Chạy lại script với `--replace` (chỉ đọc). Không còn `!` thì commit script + lock; còn `!` thì liệt kê từng dòng cho người dùng, chưa commit.
2. G1 áp dụng C0 đã duyệt (mục "Đề xuất C0" bên dưới, chép nguyên văn).
3. Sau G1 (chờ prompt Phase 2 của người dùng): lock có hash cho `.venv` (cu126, CPU), lock và cách dựng lại `runtime\florence-python`, C4, bản nháp C5. C3 thuộc B1, C6-nối thuộc Phase 3.

## Cách kiểm
- PC, cả 8 module của C (chạy trong `temp\wt-installer`):
  `$env:PYTHONPATH = "E:\DungChung\BiliFlow\temp\wt-installer\src"; ..\..\.venv\Scripts\python.exe -m unittest tests.test_install_paths tests.test_activation tests.test_models_lock tests.test_recycle_bin tests.test_job_delete tests.test_source_cleanup tests.test_download_tools tests.test_performance_report -v`
- C0: đã duyệt; G1 kiểm `AGENTS.md`, `LICENSE`, `THIRD_PARTY_NOTICES.md` khớp từng chữ với mục "Đề xuất C0".
- C6-lõi: `python -m unittest tests.test_activation -v` (cần `pycryptodomex`, có trong `.venv`). Trên PC, `test_this_pc_has_at_least_two_known_parts` đọc định danh thật (chỉ đọc).
- C1: `python -m unittest tests.test_models_lock -v`; cổng: chạy `models_lock.py` không đổi gì trong `models\` (so `dir /s` hoặc SHA trước/sau nếu muốn).
- C2: `python -m unittest tests.test_install_paths -v` (PC: `.venv` của thư mục cài, `PYTHONPATH` trỏ vào `src` của worktree); cổng: khóa cache mọi stage không đổi.

## Chờ người dùng
- ~~Duyệt C0~~: **đã duyệt 2026-10-11** (chi tiết ở mục "Đề xuất C0"). G1 áp dụng.
- Repo phải Private trước bản build đầu tiên (C7).
- Đồng ý sửa `scripts/models_lock.py` và test chỉ fail trên Windows ("Bước tiếp theo" 1). Lock chỉ được commit khi không còn dòng `!`.
- Quyết định `annotations/text_semantics_seed_v1.json` có đóng gói không (C1).
- C6: người dùng tự chạy `keygen\issue_key.py init --private-key <ngoài Git và ngoài BiliFlow>` trên PC, sao lưu file + mật khẩu, rồi đưa khóa công khai (hex) vào `RELEASE_PUBLIC_KEY_HEX` (lúc C6-nối). Agent không chạy bước này.

## Lưu ý cho người tiếp quản
- PC chạy Python 3.11 (`pyproject.toml`: `>=3.11,<3.12`): không dùng cú pháp 3.12+.
- Không sửa: `scheduler.py`, `final_renderer.py`, `job_pipeline.py`, module quét, `control_center.py`, `cli.py`, `__main__.py`, `__init__.py`, `run.ps1`, `env.ps1`, `stage_cache.py`, `cache_dependencies.py`, `pyproject.toml`, `config/*` (trừ file lock mới), `models\`.
- Không bao giờ tạo hay đưa khóa bí mật thật vào repo.
- C6-nối (Đợt 3): server chỉ để kích hoạt trước `ControlCenter()`, chặn CLI, trang kích hoạt, tạm dừng scheduler ở ranh giới stage khi key tháng hết hạn, giờ internet (tùy chọn) qua client mạng đã kiểm của trình tải; cập nhật vai trò pycryptodomex trong `config/download_tools.json` (thuộc `config/*`, chưa sửa).
- C5 (bộ cài) phải ghi `biliflow-install.json` ở gốc thư mục cài **trước** khi chạy bất kỳ code BiliFlow nào; C4 phải giữ gói `biliflow` trong 3 cấp dưới gốc cài (`MAX_LEVELS_ABOVE_PACKAGE`).
- `download_models.py` đổi cách gọi: `--model X` giờ tải theo lock; cách cũ cần `--latest`. Không chạy nó trên `models\` của PC.
- Môi trường cloud: Python 3.13 hệ thống, không có `.venv`; `pycryptodomex==3.23.0` (đúng bản trong lock) cài vào venv tạm của session để chạy test C6.

---

## Đề xuất C0

**Đã duyệt 2026-10-11** (người dùng trả lời trên PC). Điều phối G1 chép nguyên văn các khối dưới đây, trong một commit riêng:
- **A1, A2, A4:** đồng ý nguyên văn.
- **A3:** đồng ý, có thêm một gạch đầu dòng cuối (đã chèn vào khối A3).
- **B (`LICENSE`):** đồng ý. Tên chủ sở hữu: **Endmin** (đã điền vào khối B).
  - Văn bản C0 người dùng dán còn chỗ giữ chỗ `<TÊN CỦA BẠN…>` chưa điền. Tên lấy từ câu trả lời riêng cho câu hỏi "tên chủ sở hữu".
  - G1 nên hỏi lại người dùng một câu trước khi tạo `LICENSE`.
- **C (`THIRD_PARTY_NOTICES.md`):** đồng ý, kèm cách xử lý FFmpeg người dùng chọn (đã sửa vào khối C):
  - bộ cài **tải FFmpeg lúc cài** từ trang phát hành chính thức của bên build, ghim phiên bản và SHA-256;
  - `ffmpeg -version` xác định bên build;
  - link mã nguồn của bên build thay chỗ `<source URL>`.
  - Đã chạy trên PC (chỉ đọc) `tools\ffmpeg\bin\ffmpeg.exe -version`:
    - bản `9.0.1-essentials_build-www.gyan.dev`, build tĩnh 64-bit của gyan.dev;
    - cấu hình có `--enable-gpl --enable-version3`, không có `--enable-nonfree`;
    - `tools\ffmpeg\README.txt` của bản build ghi "License: GPL v3" và "Source Code: https://github.com/FFmpeg/FFmpeg/commit/bf1b838f2a", và liệt kê phiên bản thư viện ngoài ở mục "release-essentials external libraries' versions" (ví dụ x264 v0.165.3223, x265 4.3-6-g9ddc216).

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
>   - Agents only write the public key into RELEASE_PUBLIC_KEY_HEX when the user gives it to them; they never generate or choose it.

**A4. Repo Private.** Thêm vào mục "Never auto-publish or upload":
> - The repository must be Private before GitHub Actions makes the first installer build. Builds are stored as workflow artifacts; only the user creates a Release.

### B. File `LICENSE` mới (độc quyền)

Tên chủ sở hữu đã điền (người dùng, 2026-10-11). Văn bản đã duyệt:

```text
BiliFlow
Copyright (c) 2026 Endmin. All rights reserved.

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
| FFmpeg | GPL-3.0-or-later (build configured with GPL codecs such as x264/x265) | Downloaded at install time from the builder's official release page, version and SHA-256 pinned; see "FFmpeg" below |

Model weights keep the licenses in the table at the top of this file. For each Apache-2.0 model, the installer copies the upstream `LICENSE` and, when the model repository has one, its `NOTICE` file next to the weights. For each MIT model, it copies the upstream copyright notice and license text.

## FFmpeg

BiliFlow runs `ffmpeg.exe` and `ffprobe.exe` as separate programs; it does not link FFmpeg. The installer does not ship FFmpeg: at install time it downloads the FFmpeg build below from the builder's official release page and checks its pinned version and SHA-256 before use. The build is configured with `--enable-gpl --enable-version3` and without `--enable-nonfree`; the installer refuses a nonfree build.

| Build | Builder | License | Source code |
|---|---|---|---|
| FFmpeg 9.0.1 `essentials_build`, 64-bit static | gyan.dev | GPL-3.0-or-later | FFmpeg: https://github.com/FFmpeg/FFmpeg/commit/bf1b838f2a ; the versions of the external libraries in the build (x264, x265 and others) are listed in the build's `README.txt` |

The build's `LICENSE` and `README.txt` are kept next to it in `<install>\tools\ffmpeg\`. FFmpeg is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 3 or later.
```

Ghi chú cho G1 và C5 về FFmpeg:
- Bảng ghi đúng bản đang có trên PC (2026-10-11).
- Khi C5 ghim phiên bản và SHA-256 của gói tải, nếu bản ghim khác bản này thì cập nhật cả dòng bảng và link mã nguồn theo `README.txt` của bản đó ("Source Code: …").

Còn phải quyết khi làm C5 (không chặn C0):
- ~~FFmpeg: kèm nguồn hay tải lúc cài~~ → đã chọn: tải lúc cài từ trang phát hành chính thức của bên build, ghim phiên bản + SHA-256 (người dùng, 2026-10-11).
- `config/download_tools.json:71-76`: thêm vai trò "kiểm chữ ký key kích hoạt (Ed25519)" cho pycryptodomex. File này thuộc `config/*`, đi theo đợt B1 hoặc lúc nối C6, không sửa trong Phase 1.
