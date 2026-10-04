# Dashboard V2 review page: inventory of the classic page, the server API and the V2 gaps

Reference for `docs/DASHBOARD_V2_REVIEW_PLAN.md`. Collected on 2026-10-04 by two read-only code
explorations of branch `feat/dashboard-v2` at `18d3c18` (nothing was run or modified). Batch 5 (`c91746f`)
only added lines inside the phone handler of `control_center.py` (after line 2155) and in `phone_access.py`;
the line numbers below for those two regions were re-checked at `c91746f`. Everything else is from `18d3c18`
and can drift by a few lines; look up the quoted names when a line does not match.

Aliases: RW = `src/biliflow/review_workflow.py`, CC = `src/biliflow/control_center.py`,
ED = `src/biliflow/export_dialog.py`, RE = `src/biliflow/review_evidence.py`,
EG = `src/biliflow/export_guards.py`, PA = `src/biliflow/phone_access.py`,
LM = `src/biliflow/logo_memory_admin.py`, FR = `src/biliflow/final_renderer.py`,
BM = `src/biliflow/brand_memory.py`, SC = `src/biliflow/scheduler.py`, JS = `src/biliflow/job_store.py`,
V2 = `dashboard_v2/`, T = `tests/`.

## Part A. The classic review page (client)

### A0. Scope facts

- `_interactive_html(token)` is RW:4074-4441: CSS 4086-4175, markup 4175-4183, one inline `<script>` 4184-4435.
  Placeholders filled at 4436-4441: `__EXPORT_DIALOG_JS__` (ED:31-48: size choice, validation, confirm text,
  `EXPORT_GATE_MESSAGE`), `__EXPORT_SIZE_OPTIONS__` (ED:23), `__EXPORT_CUSTOM_GB__` (ED:19) and the token
  (JSON-encoded, `<` escaped).
- Routes: PC CC:1862-1866; phone CC:2283-2288 (wraps the same HTML with `_review_page_for_phone`, CC:193).
  The `'/api/` rewrite only changes `const API='/api/'` (RW:4185); `/media/...` and `location.href='/'` stay
  absolute.
- The page's CSP is only `frame-ancestors 'self'` (CC:1682-1687), so its inline script and `blob:` images work.
  `DASHBOARD_V2_CSP` (CC:123-127) forbids both.
- Byte lock: `T/fixtures/dashboard_v2_classic_pages.json` (127,875 bytes for `/review/1`, token "test-token").
- The standalone `serve_review_ui` (RW:4448-4699) serves the same HTML with `API='/api/'`, without media key,
  evidence, frame or video routes (stills only; its finalize returns 202). It is out of scope for V2.
- Not on this page: a "Thêm" chip (the closest is `select#more-filter` "Lọc khác…", RW:4177); remember-logo
  checkboxes (they are buttons; the only checkbox is `#auto-next`); "Bỏ qua (không xuất)" / "Mở lại để xuất"
  (Dashboard only, CC:546, 643-644; POST `/api/jobs/{id}/skip|unskip`); a Visual AI Audit trigger; frame
  stepping, zoom, loop, speed, fullscreen or native video controls; a sort selector (always by time, RW:4229).

### A1. Layout

| Region | Selectors and content | Line |
|---|---|---|
| Header (sticky; static at ≤820 px) | `header#top > .top` (`.has-notice` while a notice shows); `h1.title#title` "Duyệt nội dung · {phim}" (also `document.title`); `#count` "{done}/{total} xong"; `.progress > #progress` | 4176, 4240 |
| Export dropdown | `details.export#export-section` (`.ready`): summary "Xuất video" + `#export-summary`; body: `#summary`, `section#resources` (4 cards + note), `section#export-panel` with `select#output-size-mode`, `input#custom-output-gb` (0.05-1000, default 3.5), `button#finalize`, `#export-status` | 4176, 4243-4247 |
| Export notice | `span.export-notice#export-notice` (`.running/.ok/.error`, `role=status`) | 4248-4250 |
| Back | `button.back` "← Quay lại Dashboard" → `location.href='/'` (the page's only link to `/`) | 4176 |
| Filter chips | `div.chips#chips[role=toolbar]`: pending "Chưa duyệt (N)", adult "18+", gore "Máu me", violence "Bạo lực", ads "Quảng cáo", all "Tất cả"; `select#more-filter`: high "Ưu tiên cao", visual_ai "Visual AI", visual_logo "Logo", text "Chữ", candidates "Ứng viên phụ (N)" | 4177, 4238 |
| Scope warning | `.warn-line#scope-warning` when detector groups were skipped | 4178, 4240 |
| Scene list | `aside.list#list`: title + count; `.rows#rows` of `button.row[data-id]` (dot, name + small span, `.st` pill Giữ / Làm mờ / Cắt / Cần xem / Chưa duyệt); `.list-foot` with the two bulk buttons | 4179, 4255-4256 |
| Mobile list sheet (≤820 px) | `.list.open`, `#sheet-backdrop`, `#sheet-close`, `button#mobile-list` "Mục N · Danh sách để chọn lại ▾" | 4180, 4266 |
| Nav bar | `#prev` "← Trước", `#next` "Sau →", `#undo` "↶ Hoàn tác", `#save-state` ("Đang lưu…" / "Đã lưu"), `#auto-next` | 4181 |
| Focus card | `section.card#focus` (`--cat` colour): left `.media`, right `.side#side` | 4182 |
| Player | `.player#player` (`.covered/.playing/.no-video`): `video#video` (no controls, `preload=none`), `img.poster#poster`, `#poster-loading`, `#play-btn`, `#ptime`, `#pnote` | 4182 |
| Timeline | `.timeline#timeline`: `.seg/.mo/.gapline/.win` bars, `.hit` ticks, `.peak` dot, `.head#thead` playhead | 4280 |
| Frame strip | `.strip#strip`: ≤8 `button.thumb` (`.peak` "Rõ nhất", `.hit`, `.on`, moment badge, time); 8 `.ghost` cells while loading | 4277 |
| Non-video evidence | `.media-region#media-region`: ≤3 previews; `.region-frame` red box; `.region-crop` 360 px crop; `.evidence-frame` yellow dashed AI boxes (reference only) and red approved-blur boxes + legend | 4281-4285 |
| Side panel | Safety card (`safetySide`): pill, title, sub, hint, `.focus-box` (play, `.moments .mchip`), `.applies`, `.decide`, `.chosen`. Ad/logo/text card (`adSide`): `.ai-verdict`, `.coverage`, `.decision-block .region-decide`, two `.studio-wrap`. Both end with `details.tech` "Chi tiết kỹ thuật" | 4296, 4298, 4319 |
| Empty / next hint | `.empty#empty`, `p.next#next-note` | 4183, 4268 |

- A player shows only for `hasPlayer(x)`: safety items, or full-frame logo "scene logo" cards (RW:4222-4223).
  `#media-region` shows for every non-safety item.
- A "scene" is a safety item with more than 1 detected moment (RW:4212-4213): moment chips, per-moment timeline
  bars, a scene-restricted strip and an "applies" line.
- Responsive rules at ≤820 px: RW:4170-4174.

### A2. User actions

Keyboard (`onKeyDown`, RW:4406), acting on the focus item:

| Key | Action |
|---|---|
| `1` | KEEP "Giữ nguyên" |
| `2` | BLUR with `full_frame`, always behind a confirm |
| `3` | CUT |
| `4` | NEEDS_MORE_CONTEXT |
| `←` / `→` | Previous / next item in the current list (clamped, no wrap; key-repeat allowed) |
| `Space` | Play / pause (not when focus is on a button, summary, link or form field, RW:4405) |
| `Z` | Undo |
| `Esc` | Close the mobile sheet and the export panel |

Ignored with Ctrl/Meta/Alt, when `defaultPrevented`, or while typing (RW:4404). Key-repeat is ignored except for
the arrows. Clicked buttons are blurred so Space does not re-press them (RW:4427). No keys for clear, filters,
bulk, export, seek or frame step.

Pointer and touch: chips and `#more-filter` → `setFilter` (RW:4265, 4412-4413); row click → `selectItem`
(RW:4263, 4414); `#prev/#next/#undo` (RW:4421-4423); `#auto-next` persisted in
`localStorage['biliflow.review.autoNext']` (RW:4192, 4424); sheet open/close (RW:4425); side-panel delegation on
`button[data-act]`: decide, seq ("▶ Phát lần lượt N khoảnh khắc"), moment, play ("▶ Phát đoạn này"), studio,
platform, region, clear (RW:4415); strip thumb seeks the paused video (RW:4417); timeline click seeks (2-98 % of
the width; scenes snap to the next moment start, RW:4418); player / poster / `#play-btn` toggle play
(RW:4419-4420); export `<details>` closes on an outside click (RW:4428); `details.tech` open state remembered
(RW:4416); `#output-size-mode` toggles the custom GB field (RW:4252).

Decision semantics (`decideButtons` RW:4292, `decide` RW:4399):
- Four buttons "Giữ nguyên" (1), "Làm mờ cả cảnh" (2, `data-full`), "Cắt cảnh" (3), "Cần xem thêm" (4). The
  selected one shows "✓ đã chọn" + `aria-pressed`; clicking it again re-sends (no toggle-off).
- Clear only via "Bỏ chọn" (RW:4294) → `clearDecision` (RW:4400).
- Region buttons (RW:4326): "Đây là tiêu đề/nội dung phim — giữ lại" (KEEP) and "Đây là logo thương hiệu — làm
  mờ" (BLUR), deciding on the red-region owner card with a canned Vietnamese `note`.
- Studio button "Đây là logo hãng phim — giữ & nhớ" (RW:4309): KEEP + `remember_studio_logo:true`, only for
  `studioEligible` (visual_logo, no suggested region, not a persistent overlay, RW:4220); ignored if remembered.
- Platform button "Đây là logo nền tảng — làm mờ & nhớ" (RW:4312): BLUR + `remember_platform_logo:true`; eligible
  for platform-logo or studio-eligible cards (RW:4310); ignored if remembered.
- Un-remember = another decision or "Bỏ chọn"; the server forgets the record (RW:3525-3526, 3891).
- NEEDS_MORE_CONTEXT counts as decided in the counters but makes the queue status NEEDS_MORE_CONTEXT, which blocks
  export (RW:4233; server RW:2693).
- Deciding an advisory ("Ứng viên phụ") item promotes it into the main list server-side (RW:3432-3440); not
  undoable (RW:4401-4402).

Undo (RW:4379, 4401): stack of ≤100 entries (previous decision, region, note, studio/platform flags), re-POSTing
`decision` or `clear`; cleared when the queue identity changes; bulk actions are not undoable.

Bulk (`.list-foot`, RW:4408-4410): "Giữ nguyên tất cả đang lọc" → `bulk-keep`; "Duyệt tất cả đề xuất đang lọc" →
`bulk-accept`. Filter map: pending, all, high, adult, gore, violence, text, visual_logo map to themselves; `ads`
sends two POSTs (`visual_logo` then `text`); `visual_ai` and `candidates` alert "Bộ lọc này không hỗ trợ thao tác
hàng loạt.". Flow: `confirm` with the count → `runBlocking` (`busy`, `body.saving`) → `applyQueueUpdate`.

Export (`finalizeExport`, RW:4411): await `writeChain` → if status ≠ READY_FOR_EDIT_PLAN `alert(EXPORT_GATE_MESSAGE)`
→ validate the size → `confirm(exportConfirmText)` → close the panel, notice "Đang gửi lệnh xuất video…" → POST
`finalize` → on failure a red notice and the panel reopens. Sizes (ED:12-16): default 3.5 GB, custom 0.05-1000 GB,
unlimited.

Back / skip / reopen: back → `/` (on the phone listener `/` answers 303 to `/dashboard-v2/`, CC:2275-2282). Skip and
reopen are not on this page. Editing a COMPLETED review moves the job back to READY_TO_EXPORT or WAITING_REVIEW
(CC:1215-1218); editing a SKIPPED review may supersede the skip (CC:1201-1214).

### A3. API calls of the page

Paths relative to `/api/jobs/{id}/review/`. GETs use `cache:'no-store'`; POSTs send JSON and `X-BiliFlow-Token`.

| Call | Payload → fields used | When |
|---|---|---|
| GET `queue` | `items[]`, `advisory_items[]`, `status`, `counts`, `created_at`, `updated_at`, `source.{path,sha256,duration_seconds}`, `reports`, `detection_scope.{selected,skipped}`, `visual_ai_audit.assessment_count`, `export_size_policy.{mode,maximum_output_gb}` | load (RW:4236); every 3000 ms (RW:4336, 4433); after a failed write (RW:4386) |
| GET `resources` | `source_bytes`, `report_bytes`, `disk_free_bytes`, `estimated_preview_seconds`, `estimated_preview_megabytes_range` | load; identity change; 1500 ms after writes (RW:4385) |
| GET `export` | `status` (job state), `output`, `error`, `render_progress.{state,percent,eta_seconds}`, `source_cleaned`, `source_archived`, `source_name`, `source_cleanup.finished_at`, `source_archive.archived_at` (CC:1879-1898) | load; identity change; every 3000 ms only while QUEUED / RENDERING (RW:4434) |
| GET `session` | `token`, `media_key` | load; any 403, single-flight (RW:4376) |
| GET `evidence?item=` | `frames[{t,kind,score}]` (≤24), `detected_intervals`, `seeds`, `strongest`, `sample_fps`, `context`, `ignored_ref_count`, `video.{available,reason}` | focus on a hasPlayer item; prefetch of the next undecided |
| GET `frame?item=&t=&k=` | 640 px JPEG, kept as a Blob URL | strip frames |
| GET `video?k=` | `<video src>`; Range 200/206/416 with `Accept-Ranges` (RE:599-638) | first play or seek |
| GET `/media/<encodeURIComponent(preview_image)>` | report previews (posters, fallback strip, region media) | absolute path |
| POST `decision` | `{id, decision, full_frame, note, remember_studio_logo?\|remember_platform_logo?}` → full queue | decide, region, studio, platform, undo |
| POST `clear` | `{id}` → queue | clear, undo |
| POST `bulk-keep`, `bulk-accept` | `{filter}` → queue | bulk |
| POST `finalize` | `{size_mode, max_output_gb?, description}` (from the shared export dialog JS) → `{status:'QUEUED'\|'COMPLETED', output, plan?, export_size_policy}` | export |

Errors: `readJson` (RW:4203) throws `data.error` or "Máy chủ trả về lỗi N"; a network failure throws an offline
error (RW:4204). `postJson` (RW:4391) refreshes the session once on 403; `finalizeExport` uses raw `fetch` (no
retry, "Không gửi được lệnh xuất: …"). Writes (RW:4387-4390) go through a serial `writeChain`, are optimistic,
retry transient errors (no status or ≥500) after 300 ms then 900 ms, and on final failure `alert(writeFailureMessage)`
then `resync()` (reload the queue and reselect the failed item, switching to `all` if needed). Review rule
violations are 400; a bad token or Host is 403; review writes never return 409 (409 only on media when the source
changed, CC:1129, 1140). Media: 410 source cleaned, 404 archived or missing, 415 container (CC:1114-1116, 1186).
No revision or ETag on writes (last writer wins; polling merges). Frame and video need a valid `k` (else 403); a
frame `t` must match an evidence frame within 1 ms (RE:481-489).

### A4. Client state and rules

- Filter starts at `pending`, or `all` if nothing is pending (RW:4236). The list is `queue.items.filter(visible)`
  sorted by start, end, id (RW:4228-4230); `candidates` uses `advisory_items`. `visible()`: pending = no decision or
  sticky; high = priority high; visual_ai = has `ai_visual_audit`; visual_logo = `isLogoItem`; ads = `isAdItem`;
  text = category text except `review_kind` logo_overlay; otherwise `category == filter`.
- `pickFocus`: first undecided in the list, else the first (RW:4261). After a decision with `autoNext` on, focus
  moves to `nextUndecided` (cyclic, skips decided; RW:4262, 4383). `sticky` keeps just-decided rows visible in the
  pending view until a filter or identity change. `step()` clamps (RW:4264).
- `decide` validation (RW:4399): early return when busy or locked; "Không tìm thấy mục này…" if missing; Visual AI
  conflict confirm when `ai_visual_audit.confidence ≥ 0.9` and its suggestion differs (both in KEEP/BLUR/CUT); BLUR
  with `needsFullFrame` always confirms (scene wording, RW:4392); BLUR without `suggested_region_source_pixels` and
  without full-frame or platform throws "Mục này chưa có vùng được định vị; hãy chọn Làm mờ cả cảnh."; then
  pushUndo → applyLocalDecision → afterLocalChange → enqueueWrite. The server's full queue replaces the local one
  only when that write is the last pending one (RW:4390).
- Locks (client mirror of `ensure_review_editable` CC:1229-1245 and finalize guards CC:1247-1280):

| Condition | Effect |
|---|---|
| Export QUEUED / RENDERING (RW:4397) | `body.export-locked` dims decision, region, clear, studio, platform, bulk; edits alert the lock message; `#finalize` disabled |
| `source_cleaned` / `source_archived` | same lock with its own message; only report previews (RW:4276) |
| SKIPPED | `#finalize` disabled, text points to the Dashboard; edits allowed |
| COMPLETED, FAILED | not locked |
| Not READY_FOR_EDIT_PLAN | `#finalize` disabled (also while `exportRequestInFlight`) |
| Bulk running | `body.saving` blocks pointer events; decide/clear/undo return early |
| No `mediaKey` or video unavailable | `.no-video`, reason text instead of play |
| Prev, next, undo | disabled at the list ends or on an empty stack |
| Tab hidden | queue polling skipped, video paused |

- Tokens: the page token (`X-BiliFlow-Token`) is refreshed from `session`; `mediaKey` = HMAC(token, job) (CC:1075-1080)
  sent as `k`; a 403 refreshes the session with up to 2 retries (frames RW:4342, video RW:4375).
- Identity: `queueIdentity` = `created_at` + source sha + `reports` (RW:4234); `queueVersion` = identity +
  `updated_at` + status + `counts.total` + `counts.pending` (RW:4235). Identity change (new scan revision) clears the
  evidence cache, sticky, undo and frame blobs, refetches export and resources, full render (RW:4337). Version change
  only patches list pills, header, side panel, and the focus only if its start or end moved (RW:4335, 4337).
- Media: poster until the first `seeked` / `playing` for the right item; `ensureVideo` sets `src` lazily; playback
  stops at the range end, scenes guarded by `momentGuard` (RW:4362), sequence mode jumps between moments;
  `videoFailed` (RW:4375) probes with `Range: bytes=0-0` and maps 404/409/410/415 (and 2xx with media error 3/4 to
  `decode_error`); `pagehide` releases the video (RW:4430).
- Performance: no virtualisation; list rebuilt with `innerHTML`, rows use `content-visibility:auto` (RW:4111);
  `updateListStatuses` patches pills unless the order changed; `setText` / `setHtml` diff helpers (RW:4201-4202);
  `renderSide` skips identical HTML; frames 2 concurrent with urgent and later queues (RW:4343), peak first;
  `keepFrames` keeps blobs for focus, previous and next-undecided only (RW:4345); prefetch of the next undecided after
  600 ms (RW:4346); single-flight for evidence, session and queue refresh; `ResizeObserver` sets `--hh` (RW:4431);
  `window.reviewStats` counters (RW:4191). Server: ffmpeg frame cache `cache/review-frames`, 2 ffmpeg slots (RE:495).

### A5. User-facing texts that carry rules

- Export gate and confirm: "Vẫn còn mục chưa có quyết định cuối cùng." (ED:9); "Khóa các lựa chọn hiện tại và bắt
  đầu xuất video hoàn chỉnh (tối đa 3,5 GB)?" (ED:43); "Giới hạn tùy chỉnh phải từ 0,05 đến 1.000 GB." (ED:41).
- Locks (RW:4393-4395): "Video đang chờ xuất hoặc đang xuất; hủy lệnh xuất trước khi đổi quyết định."; "Video gốc đã
  được dọn vào Thùng rác; trang duyệt chỉ để xem…"; "Video gốc đang ở kho lưu trữ… Bấm “Khôi phục bản xuất” trên
  Dashboard…".
- Skipped (RW:4246): "Video đã được đánh dấu bỏ qua (không xuất). Bấm “Mở lại để xuất” ở Dashboard…".
- Progress (RW:4241-4242, 4254): "Bấm “Xuất video” ở trên để xuất."; "Còn N mục chưa duyệt… mục kế tiếp tự hiện sau
  khi chọn"; "Hãy chọn Giữ nguyên, Làm mờ cả cảnh, Cắt cảnh hoặc Cần xem thêm.".
- Scope (RW:4240): "Không quét trong lượt này: … Ít mục hơn không có nghĩa các nhóm này đã an toàn.".
- Decision scope (RW:4218, 4329): "QUYẾT ĐỊNH TOÀN VIDEO", "TOÀN KHOẢNG XUẤT HIỆN", "CHỈ ĐOẠN HIỆN TẠI", "Ứng viên kiểm
  tra thêm — chưa thuộc quyết định chính", "Quyết định chỉ áp dụng cho N khoảnh khắc này… khoảng trống giữa chúng giữ
  nguyên.".
- Confirms (RW:4399, 4409-4410): "Bạn có xác nhận làm mờ toàn bộ khung hình trong đoạn này?"; "Visual AI tin cậy 93%
  đề xuất…"; "Giữ nguyên N mục chưa duyệt đang hiển thị? Thao tác này không blur hoặc cắt video."; "Áp dụng N đề xuất
  đang hiển thị? Bạn vẫn có thể bỏ chọn từng mục trước khi xuất.".
- Mandatory boundary review (RW:4299-4300): "BiliFlow luôn đưa 5 giây đầu video…", "6 giây cuối video…".
- Remember-logo notes: studio (RW:4309, similarity ≥95 % moves later matches to "Ứng viên phụ"); platform (RW:4313,
  "vẫn chờ bạn duyệt").
- Write failure (RW:4389) and offline (RW:4204) messages; advisory undo (RW:4402); video texts (RW:4288, 4375).

### A6. Phone additions (REVIEW_PHONE_STYLE / REVIEW_PHONE_SCRIPT, CC:138-199, phone listener only)

- Touch screens: "phím N" hints hidden on unselected decision buttons; "✓ đã chọn" stays.
- ≤820 px: chips wrapped in `.chips-wrap` with a `›` scroll button (aria-label "Xem thêm bộ lọc", hides at the end);
  `.decide` fixed at the bottom (safe-area padding) with `body.paddingBottom` kept equal to its height + 12 px; font
  floor 12 px for thumbs, moment chips and the export chevron.
- Transport: page and all GETs need the access cookie; writes need the token, same-origin `Origin` and a path in
  `PHONE_ALLOWED_POSTS` (PA:106), which includes decision, clear, bulk-keep, bulk-accept and finalize.
- Connections: at most 8 connections per device that have not yet shown the cookie (`MAX_CONNECTIONS_PER_IP`, PA:53,
  batch 5 M1) and 32 in total (PA:49); responses are HTTP/1.0, one connection per request.

### A7. Gotchas

- Only job states QUEUED / RENDERING count as "export active" client-side (RW:4397); a QUEUED scan looks the same;
  the server distinguishes them (CC:1220-1227).
- Bulk counts come from the client `visible()` predicate while the server filters by raw `category` (RW:3917-3930):
  counts can differ for `text` and `visual_logo`; `bulk-accept` also skips BLUR items without a region (RW:3987).
- When NEEDS_MORE_CONTEXT is all that is left, the page says "Đã duyệt đủ. Bạn có thể xuất video." (RW:4254) while
  `#finalize` is disabled.
- `export` is re-polled only while QUEUED / RENDERING, so its text can go stale after edits.
- "Ổ E còn trống" is a hard-coded label (RW:4245).
- `queueIdentity` (RW:4234) reads `source.input_sha256`, but the queue writes `source.sha256` (RW:3294), so the sha
  part is always empty and the identity is effectively `created_at` + `reports`. A new scan revision still changes
  `created_at`. V2 should read `source.sha256`.

## Part B. Server API, invariants, tests and V2 gaps

### B0. Key findings

1. No new server route is needed: the existing review routes cover a full V2 review page. A new POST would have to be
   classified in PA (`T/test_dashboard_v2_phone_hardening.py:221`) and listed in `contracts.js`
   (`T/test_dashboard_v2_contract.py:59-84`).
2. Every review refusal is HTTP 400 `{error}` (export in flight, source cleaned/archived, skipped, queue not ready,
   bad input). 409 is only raised by hide/unhide, cancel, cleanup/archive/restore/recheck and logo memory. 403 = bad
   token; 500 = generic (for example WinError on a locked queue file).
3. The phone listener allows every review POST, including the `remember_*_logo` flags. PC-only: logo-memory
   class/delete and the source operations. HTTP/1.0 responses and the per-device limit (A6) mean V2 must throttle
   image loading (the classic page runs at most 2 frame fetches at once, RW:4343).
4. `/` and `/review/{id}` are byte-locked by SHA fixtures (`T/test_dashboard_v2_route.py:122`,
   `T/test_dashboard_v2_phone.py:402,422`): do not edit `_interactive_html` or `export_dialog.py`.
5. Adapter blockers: no review GET helpers; `once()` keys on method + path only (`V2/adapter.js:109-114,153-158`), so
   two quick decisions on different items collapse into one POST (the second caller silently gets the first
   promise); every live `dispatch` ends with a full `/api/status` refresh (`adapter.js:282`); the transport
   JSON-parses bodies (`adapter.js:40-47`), so JPEG and video must be plain `<img>` / `<video>` URLs; the V2 CSP
   forbids `blob:` images.

### B1. Endpoints

All GETs check only the Host header (CC:1824) plus the cookie on the phone listener; no token is needed for GET.
POST pipeline (CC:1935-2110): Host check → token (403) → body must be a JSON object of at most 64 KiB (400) → route →
error mapping (409/400/500). On the phone listener a body shorter than its `Content-Length` is a 400 "Request body
was cut" (batch 5 M3, `PhoneHandler.checked_body`).

| Route | Response and validation | Notes |
|---|---|---|
| GET `/review/{id}` (CC:1862) | classic HTML | `?from=v2` is ignored today |
| GET `.../review/queue` (CC:1869) | whole queue JSON (RW:3290-3360): `status` (READY_FOR_EDIT_PLAN, REVIEW_REQUIRED, NEEDS_MORE_CONTEXT), `counts`, `items`, `advisory_items`, `source{path,sha256,duration_seconds}`, `detection_scope`, `audit_log`, `export_size_policy`, `updated_at` | read under `_REVIEW_QUEUE_IO`, uncached; 404 when the job has no `active_queue_path` (SC:325; `rerun` nulls it) or the file is unreadable |
| GET `.../review/session` (CC:1872) | `{token, media_key}`; key = HMAC(token, "review-media:{id}") (CC:1075-1087) | key changes on Control Center restart |
| GET `.../review/resources` (CC:1874; RW:4029) | sizes, disk, preview estimates | 404 once the source file is gone (tolerate it) |
| GET `.../review/export` (CC:1879) | `{status: job.state, output, error, render_progress, source_cleaned, source_name, source_cleanup, source_archived, source_archive}` | |
| GET `.../review/evidence?item=` (CC:1754; RE:374) | `frames` (≤24), `seeds`, `strongest`, `detected_intervals`, `context`, `video{available,mime,reason}` | no key; 400 without `item`; 404 unknown job or item; reasons unsupported_container, source_cleaned, source_changed, source_missing (archived maps to missing, CC:1156-1164) |
| GET `.../review/frame?item=&t=&k=` (CC:1759; RE:481-575) | 640 px JPEG | 403 without valid `k`; 400 unless `t` is a strip time; 404/409/410; 500 on ffmpeg failure; 2 ffmpeg at a time; cache `cache/review-frames/` (the only file written) |
| GET `.../review/video?k=` (CC:1767; RE:599) | Range 200/206/416 | mp4/m4v/mov/webm else 415; 410 cleaned; 404 archived or missing; 409 changed; phone streams get a 60 s write timeout (batch 5 M1) |
| GET `/media/{path}` (CC:1900) | any file under `reports/` | no key |
| GET `/logo-memory`, `/api/logo-memory`, `/api/logo-memory/frame?key=&i=` (CC:1924; LM:310) | read-only memory list | not needed by the review page |
| POST `.../review/decision` (CC:2049-2064; RW:3388-3578) | `{id, decision, note?, full_frame?, remember_studio_logo?, remember_platform_logo?}`; flags count only when JSON `true` (CC:2062) | full queue back |
| POST `.../review/clear` (RW:3873) | `{id}` | clears and forgets a remembered logo |
| POST `.../review/bulk-keep`, `bulk-accept` (RW:3910, 3958) | `{filter}` ∈ pending, high, all, gore, violence, adult, text, visual_logo | undecided main items only; bulk-accept applies `suggested_decision`, skips BLUR without region; audit BULK_KEEP / BULK_ACCEPT_SUGGESTIONS |
| POST `.../review/finalize` (CC:1247-1311) | `{size_mode: default\|custom\|unlimited, max_output_gb}`; custom 0.05-1000 (FR:36-78) | see B3 |
| POST `/api/jobs/{id}/skip`, `/unskip` (CC:1321-1403) | `{}` | Dashboard actions, not on the review page |
| POST `/api/logo-memory/class`, `/delete` (LM:338-367) | | PC-only; 409 `memory_changed` |
| POST `/api/source-*` | | PC-only; agents must never call them (AGENTS.md) |

An unlisted POST is PC-only by default and refused before its body is used (`post_policy` PA:161; CC:2329-2333).

### B2. Decision validation and side effects (RW:3421-3578)

- `decision` upper-cased ∈ KEEP, BLUR, CUT, NEEDS_MORE_CONTEXT; both remember flags at once is an error;
  `remember_studio_logo` needs KEEP on a `studio_logo_eligible` card (BM:660); `remember_platform_logo` needs BLUR on a
  `platform_logo_eligible` card (platform_memory.py:188); BLUR needs `full_frame:true` or a
  `suggested_region_source_pixels`; an advisory id is promoted (RW:3430-3443); the route does not accept region,
  interval or `blur_edge_mode` fields (CC:2057-2063); the response is the full queue.
- Side effects: the queue file is rewritten atomically with a re-rendered `.html` and an `audit_log` entry (actor
  `control_center_user`); `state/brand-memory.json` via `remember_review_item` / `forget_review_item` (BM:411-486);
  remember flags write `state/studio-logo-memory.json` and `state/studio-logo-frames/` before the queue is saved
  (RW:3561-3568); forgetting backs up and moves frames to `state/backups/` (RW:3696-3708); persistent-overlay decisions
  re-sign the studio masks (RW:3575, 3719); `sync_queue_state` (CC:1190-1218) moves the job to READY_TO_EXPORT or
  WAITING_REVIEW (also from COMPLETED), may supersede a skip (JOB_SKIP_SUPERSEDED) and retires an old render request.
  `docs/DASHBOARD_V2_PHONE.md:98` tells the user that reviewing on the phone can add or remove remembered logos.

### B3. Finalize sequence (CC:1247-1311)

1. `export_state_refusal`, then `queue.status == READY_FOR_EDIT_PLAN`, then the source lock and source-missing check.
2. `EXPORT_PATH_TAKEN` check, then `queue.export_size_policy` is written.
3. If a manifest-proven export of the current decisions exists → job COMPLETED, no render, returns
   `{status:'COMPLETED', output, export_size_policy}`.
4. Otherwise `work/<key>-edit-plan.json` is built and authorized; `queue_render` sets render PENDING, the `render:{id}`
   setting, job QUEUED and an EXPORT_QUEUED event; returns `{status:'QUEUED', plan, output, export_size_policy}`.

Concurrency: lock order `REVIEW_QUEUE_IO` (EG:40) then `scheduler.job_action_lock` (SC:138); queue and evidence GETs
take `REVIEW_QUEUE_IO`; no revision check on POST (last writer wins; a rebuilt queue returns 400 "Unknown or duplicate
review item").

### B4. Invariants and where they are enforced

| Invariant | Code |
|---|---|
| A human approves KEEP/BLUR/CUT before export; undecided or NEEDS_MORE_CONTEXT blocks | `_queue_status` RW:2693; `approved_operations` RW:4713-4720; finalize CC:1263-1264; `authorize_final_from_resolved_review` FR:456-497; renderer refuses without READY_FOR_FINAL_RENDER (FR:527-528) |
| Detection and AI never decide | `safety.automatic_edit False` RW:3356; Visual AI is ADVISORY_ONLY (RW:3770-3871); only a human `bulk-accept` applies suggestions |
| Export behaviour unchanged | `normalize_output_size_policy` FR:36-78; naming and reuse by manifest proof (`export_identity.py:130`, CC:1274-1279); V2 sends only `size_mode` and `max_output_gb` |
| Nothing exported or published automatically | `ControlCenter.finalize` is the only `queue_render` caller (CC:1302); no upload code |
| Remember-logo semantics | B2; refused before any write when the memory is unreadable (RW:3453-3459, 3642-3648); nothing deleted |
| Source cleanup/archive lock | `JS.source_lock` (JS:1230); `ensure_review_editable` CC:1229-1245; finalize CC:1265-1271; media 410/404 |
| Read-only states | in flight `render_in_flight` EG:189, `_export_in_flight` CC:1220; cleaned or archived; SKIPPED is not read-only on the server |
| Source video untouched | `review_source` is read-only; frames go to the cache dir |

### B5. Tests for parity

| File | What it asserts |
|---|---|
| `T/test_control_center.py` | review routes and media; per-job media key (1631); evidence read-only (1640); video Range 206/416 (1659); key required (1677); 404/409/415 (1691-1731); frame only at strip times (1731); remember flags only when `True` (1808, 1830); logo-memory token and stale sha 409 (1852); 40 rapid decisions vs concurrent reads (1883); export route shape (1612); `API` constant scoped to the job (1600) |
| `T/test_skip_export.py` | skip/unskip rules; `sync_queue_state` transitions (410-458); edits refused while an export is queued or running (616); paused export retired (662); finalize guards before any write (475); cleaned source refuses and media 410 (809-886) |
| `T/test_review_workflow.py` | queue build and edit plan; BLUR needs a region or full frame (1424); intervals (1474); bulk keep/accept (1539-1610); classic page contracts in node: filters and decisions (2116), keyboard (2138), polling (2152), lazy evidence and media release (2173), write retries (2208), media-key refresh (2231), advisory undo (2267), export lock and cleaned/archived read-only (3028-3340) |
| `T/test_export_guards.py`, `test_export_identity.py`, `test_export_dialog.py` | guard order; export naming by operations and manifest proof; shared dialog JS and unchanged review markup |
| `T/test_logo_memory_admin.py`, `test_brand_memory.py`, `test_platform_memory.py`, `test_platform_logos.py`, `test_review_evidence.py` | backup-first writes, 409, locks; byte-identical memory when unchanged; strip and range logic |
| `T/test_dashboard_v2_*.py` | whitelist and CSP; contract endpoints are real routes; node gates (`verify.cjs`, `verify-adapter.cjs`, `node --check`); every POST classified; PC-only table; phone review page |

### B6. The V2 demo review UI and its gaps

Decision (user, 2026-10-04): the real V2 review page is built on this demo dialog design (wider, two columns on PC,
inline video in each card); see `docs/DASHBOARD_V2_REVIEW_PLAN.md`. The gaps below are what it must add.

- Demo: "Duyệt cảnh" → `jobAction` (`V2/app.js:390-398`) → `reviewModal` (365), a `<dialog>` with
  `reviewMarkup` (361-364): intro, read-only `.notice`, `.review-heading` "N / M cảnh cần quyết định cuối" with
  "Giữ tất cả" and "Dùng đề xuất", a `.review-scenes` grid of ≤8 `article.scene` (`.scene-art` with fake watermark,
  `.blurred`, `.retained`; `.scene-content`; `.scene-actions` with 4 decisions and "Xóa quyết định"). Decisions mutate
  `sceneCache` and call `store.recordReview` (`V2/demo-store.js:142-150`). CSS in `styles.css` 9, 13, 15 and
  `theme.css` 122, 181-184, 272. Live mode navigates to `/review/{id}` (app.js:392; asserted by
  `browser-check.cjs:222`); `createLiveStore` has no `recordReview`.
- Demo data: scenes `{id:'scene-<job>-<i>', start, end, title, group, decision, region|null}` derived from
  `review_summary` counts (`V2/mock-data.js:43-46`); the fixture uses `status:'WAITING_REVIEW'`, a job state, while
  real queue statuses are READY_FOR_EDIT_PLAN, REVIEW_REQUIRED and NEEDS_MORE_CONTEXT.
- `contracts.js` already maps queue, reviewSession, resources, reviewExport, evidence, frame, video, media, decision,
  clear, bulkKeep, bulkAccept, finalize (14-25); `request()` (135-140) builds only the path (no query string);
  `pcOnlyOps` (47) covers only the source operations. `verify-adapter.cjs:47-82` never exercises decision, clear,
  bulkKeep or bulkAccept.
- V2 router: `route()` (app.js:482) accepts the hash views overview, downloads, videos, queue, logos, settings.
- Gaps: real items have ~40 fields (category, priority, suggested_decision, suggested_region_source_pixels,
  preview_images, candidate_type, temporal_policy, detected_intervals, advisory…) and queues hold hundreds of items;
  no media; no full-frame BLUR semantics; no remember-logo actions; no filters, undo, auto-next or keyboard; bulk uses a
  fixed `'all'` filter; SKIPPED treated as read-only in the demo (the server allows edits); generic lock texts; no write
  serialization, retry, token or media-key refresh, polling or offline handling; `mutate()` allows edits without gating
  (app.js:277); `dispatch` refreshes the whole status after each call; no export or resources panel or scope warning;
  the `#main` re-render on each snapshot (app.js:490-505) would destroy a `<video>`.

### B7. Serving, CSP and phone for a new view

- `DASHBOARD_V2_FILES` (CC:113-117) has 12 entries; only `live.html` gets the CSP header (CC:1818);
  `DASHBOARD_V2_TYPES` (CC:118-121) covers .html, .css, .js, .svg. Only whitelisted names are served (deep paths such
  as `/dashboard-v2/review/7` are 404), so the review view should use hash routing.
- CSP: `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src
  'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'` (also as a meta tag in
  `live.html:5` without `frame-ancestors`). Same-origin `<video src=".../video?k=…">` and `<img src=".../frame?…">` /
  `/media/...` work; `blob:` does not. No inline script or handlers (use `data-action` delegation); escape every queue
  string with `esc()`.
- Phone listener (CC:2264-2352): Host + cookie gate; without the cookie, `/`, `/dashboard-v2`, `/dashboard-v2/` and
  `/phone-login` show the login page (CC:2124) and other paths get JSON 401; with the cookie `/dashboard-v2/*` is served
  normally; POSTs need the token, same-origin `Origin` and `PHONE_ALLOWED_POSTS`.
- New files must go into `DASHBOARD_V2_FILES`, `live.html` and `index.html` (whose bodies must stay identical,
  `verify.cjs:155-163`) and `V2/serve.py:11`; `T/test_dashboard_v2_route.py:84` requires every `src`/`href` in
  `live.html` to be whitelisted; `node --check` runs on `V2/*.*js`; only `adapter.js` may call `fetch(`
  (`verify.cjs:164-169`); `verify.cjs:134-144` pins the demo fixtures at 15 jobs.
