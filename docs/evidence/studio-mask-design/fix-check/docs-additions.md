Text to port into the main tree's docs when the worktree is merged (the main tree has uncommitted
edits to these files, so the worktree copies were not edited to avoid overwriting them).

## docs/SESSION_HANDOFF.md (studio-logo memory schema 2)

- Before running `.\scripts\run.ps1 studio-logo-upgrade --apply` or clicking "Đây là logo hãng phim — giữ & nhớ"
  on the merged code, STOP the Control Center and restart it on the merged code. The old code reads only
  `schema_version: 1`; every write of the new code (remember, forget, mask refresh, upgrade) writes 2, after which an
  old Control Center raises "Unsupported studio-logo memory schema": its queue builds route nothing from memory and
  its "giữ & nhớ" is refused.
- `studio-logo-upgrade` is a dry run by default. Run `--apply` with the Control Center stopped: the command does not
  hold the Control Center's queue lock and decodes for seconds per record. It now aborts without writing anything
  (`aborted: memory_changed_during_upgrade`, exit code 1) when state/studio-logo-memory.json changed while it ran;
  re-run it. The backup in state/backups/ holds exactly the bytes it replaced.
- The real memory now has 3 records (Tập 10 A/B and Tập 16 review-f2cfc08075db, added 16:28). The dry run marks all
  3 upgradable; Tập 16 gets 111 frames with no ignored region (no watermark of Tập 16 is decided BLUR).
- After the upgrade, a remembered card keeps its schema-1 summary ("Chỉ nhớ 1 ảnh xem trước…") until the next
  decision on a watermark card of that queue refreshes it (the upgrade never writes review queues).
- A different decision on a remembered card removes the record AND its frame folder
  state/studio-logo-frames/<sha16>-<item_id> (the user's own withdrawal). Nothing else deletes that folder; the
  cleanup policy never lists it. If the folder is missing when a watermark decision changes the mask, the record
  keeps no frames (`frames_missing`) and stops matching until the logo is remembered again.

## docs/QUALITY_PLAN.md (R3b, schema 2 paragraph)

- Schema 2 (người dùng quyết định 02/10): bỏ qua vùng của các thẻ watermark cố định đã chọn Làm mờ (cùng nguồn), nhớ
  mọi khung của ident. Lớp phủ nằm ngoài các vùng đó vẫn làm hỏng khớp. Nội dung nằm hoàn toàn TRONG vùng bỏ qua thì
  qua được phép so ảnh: chữ ở đó vẫn bị kiểm tra OCR trong đoạn bắt buộc; hình không phải chữ ở đó (logo, mã QR,
  banner ảnh) OCR không thấy — chỉ còn đề xuất vùng của chính bộ quét logo (`full_frame_logo_ad_evidence`) có thể bắt,
  và không bắt nếu tập đó có thẻ watermark riêng phủ vùng đó. Đây là phạm vi người dùng đã chấp nhận (ở tập sạch, các
  vùng đó là hình phim bình thường).
- Vùng bỏ qua được gộp theo mã thẻ watermark: quyết định trong một hàng duyệt (kể cả bản quét lại cùng nguồn) chỉ đổi
  vùng của các thẻ watermark trong hàng đó; vùng do hàng khác quyết định được giữ. Giới hạn 20 % áp cho phần hợp.
