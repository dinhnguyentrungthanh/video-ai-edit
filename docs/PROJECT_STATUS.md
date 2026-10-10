## Source accounts for the video downloader (2026-10-10/11) — in `main` since 2026-10-11 (`10ed81e`, not pushed)

- **Release, 2026-10-11:** accepted (the user's run 6 on the integration build, Codex's closed review) and committed from the integration build's final snapshot on `release/source-accounts-20261011`, at the user's request to bring it into `main` and restart the main copy (no push). Checks before the commit: the ticket, reuse and API tests 88 OK; Codex's regression `download_account_post_test_review_tests.py` 3/3, run unchanged (SHA-256 the same before and after); JS gates 38/37/22/55/32. The code is the one Codex accepted (no code change since), so the integration build's E2E/crossing 12/12 and the feature worktree's full suite stand. Deployed on 2026-10-11: `main` fast-forwarded `99dff60` → `10ed81e`; the accepted account config copied (Git-ignored; 1 source, 0 problems, no overlap with the provider config, no wildcard); the main copy, already stopped, started at 00:20:49 (PID 29832, `127.0.0.1:8765`); GET checks passed (served Dashboard V2 identical to main, the source NOT_CONNECTED until the user signs in, phone mode off as before); the data is unchanged apart from four new empty download tables. Details in `docs/SESSION_HANDOFF.md`.
- **Integration test build, 2026-10-10:** `main` `99dff60` plus the branch's changes in the separate checkout `temp\wt-source-accounts-integration-test` (no commit, merge or push; the main copy untouched). Only the three status docs conflicted (both sides added a block at the top); main's gore C1 changes are kept. `AGENTS.md` records the user's choice of 2026-10-10 to keep exception A in the Network rule. Checks on this build: JS gates 38/37/22/54/32; reader checks 24 OK; Codex's two scripts 2/2 each; full suite 2,554 tests, OK (2,528 pass + 26 skip, 0 failures, 0 errors). A test Control Center runs on `http://127.0.0.1:8797/` with its own root for the user's sign-in (details in `SESSION_HANDOFF.md`). Deploying the main copy is a separate request.
- Integration build, the user's first real runs (2026-10-10, this checkout only): a ticket run failed BROWSER_FAILED with no detail; fixed in this build. A failed hidden run now names its step and error kind in fixed words. A ticket is kept when the browser state cannot be read after it. Hidden runs skip images and fonts: the list read went from 46 s to 16 s. A resumed account file is continued across tickets (the file host's nginx ETag differs per ticket; before, every Tiếp tục restarted the file from byte 0) only after every byte of the part was compared with the new link's answer from byte 0: Codex's review (P1) replaced the first check of the last 1 MiB, which could join an old head to a new tail; the cost is receiving the part again. A task fetching a fresh link shows "Đang lấy link tải mới từ nguồn". A state whose IndexedDB cannot be read keeps the new cookies and only the storage that loaded in the run (a state that also held the ticket page's origin no longer loaded: SESSION_LOAD_FAILED, INDEXEDDB). A saved state that does not restore is loaded again without IndexedDB, then with its cookies alone, and the run's log line names the IndexedDB failure in fixed words. A hidden browser that fails while a fresh link is fetched now ends the task INTERRUPTED with its part (Tiếp tục continues it), not FAILED. Past the 1-hour mark the account panel shows "Hết phiên" by the page's clock, never "Đã kết nối", and keeps it for that session when the page clock goes back (P3). Details in `SESSION_HANDOFF.md`.
- **Integration build, ticket reuse (plan §9.22), 2026-10-10 night:** Codex approved the feature worktree's §9.22 work, and its delta is copied here file by file (19 code and test files, each identical to the feature worktree; none of this build's own changes overwritten; docs merged by hand; personal absolute paths in the docs replaced by file names).
  - Behavior: the probe's ticket serves the first transfer (one ticket instead of two). A short Dừng/Tiếp tục reuses the link kept in memory after one 1 MiB check; the answer's validator decides as for any link (same mark: Range/If-Range; another mark: the whole part is compared; no mark: the file starts again). The link lives only in the process memory (100 entries, 10 minutes idle, bound to the attempt, the file, the root and Windows account, the source and the session generation); it is dropped on Hủy, Xóa, Thử lại, the task's end, a new sign-in, Ngắt kết nối, cleanup and shutdown, and a restart loses it. Refused links (401/403/404) take one new ticket; network errors stay INTERRUPTED with no ticket. NO_INPUT_DIR ends INTERRUPTED and keeps the checked file (before: FAILED); for a source-account file Tiếp tục moves it with no request, while a yt-dlp task runs yt-dlp again. Strict transfers log validator kinds in fixed words. Details: plan §9.22.
  - Checks on this build: the ticket, reuse and API tests 88 OK; Codex's regression `download_account_post_test_review_tests.py` 3/3, run unchanged (SHA-256 the same before and after); JS gates 38/37/22/55/32; `git diff --check` clean; E2E and crossing with real Edge (`BILIFLOW_REQUIRE_E2E=1`, project FFmpeg) 12/12, 0 skip, within a run of the first seven `test_download*` modules (117 tests OK). At the user's request the rest of that run was stopped at the next module: every other download file of this build is byte-identical to the feature worktree too (four account files differ only in exception A's docstrings; three gore files are main's), where the full suite passed (2,658 tests, 0 failures/errors), and Codex's review asked for no new full suite.
  - Test instance 8797 restarted at 23:38 on this build (server PID 5868, launcher 5832), with an empty `input\` in its root; the user signs in again (the session had expired).
  - Run 6, the user's test (2026-10-10 23:44–23:48) passed: one ticket for the probe and the first transfer (four before); two Dừng/Tiếp tục with no new ticket; the same link's ETag changed between requests about a minute apart, so each resume compared the whole part first (the safe path) before continuing; COMPLETED with a 3,012,561,637-byte MKV in the test root's `input\`; picture and sound checked by the user. Ready for Codex's review.

- Worktree `temp\wt-download-source-accounts` (base `e8aea11`). M0–M4 done per `docs/SOURCE_ACCOUNTS_PLAN.md`: encrypted per-Windows-account sessions, the sign-in window library, the account provider (episode lists, one ticket per chosen file), and in M4 the queue side: WAITING_LOGIN without a slot, stored episode previews, episode groups (up to 500, created under the 100-task cap), PC-only sign-in routes. M5 (2026-10-09, plan §9.17) adds the Dashboard V2 side: the source-account panel (sign-in buttons on the PC only), the episode dialog and episode-group progress; Codex closed M5 after the §9.18 fix. M6 (plan §9.19) checks the integration with fixtures. M7 (plan §9.20) is closed in the worktree on 2026-10-10: a reader and a verifier for the first real source, accepted with BiliFlow's own session on a test instance (sign-in, list, one ticket and a 1 MiB probe of it), and the reader's ticket path turned on by the user's decision after Codex's review. It waits for Codex's review before anything goes into `main`.
- 2026-10-08: Codex's M4 review found three P2 issues; all fixed (plan §9.15): group Cancel reaches every unfinished episode, group intents survive interruptions and restarts, and the stop never closes SQLite while a thread can use it.
- Checks on the final code: Codex regressions M4 3/3, M3 4/4, M2b 2/2; full suite on the final code (unittest discover, the 1 s synthetic clip in the worktree's `input\`, 60-minute outer watchdog): 2,380 tests in 1,187 s, 2,352 OK, 26 skipped, 2 errors. Both errors are in `test_download_account_hang` (real headless Edge, from M3, untouched here): a run that does not hang must end within its 8 s deadline, and Edge started too slowly under full-suite load; that module alone passed 8/8 (113 s). Skips: 11 project FFmpeg absent in the worktree, 5 Golden ident previews, 4 studio-logo memory, 1 opt-in real Recycle Bin, 1 Control Center test database, 1 v1 labels revision, 1 safety classifier, 1 Playwright for the phone browser check, 1 real film data; none in the download tests; node gates 38/37/22/32.
- Phase A before M5 (plan §9.16):
  - Both hang-test errors were test bugs (the 8 s deadline of a deliberate hang also covered real Edge start-up and close). Fixed in the tests only; `SpaceTests` now waits for the real message.
  - Results: changed modules 37 OK; Codex 3/3, 4/4, 2/2.
  - First full suite: 2,380 tests, 1,206 s, 2,353 OK, 26 skipped, 1 error in the unchanged `SharedSpaceTests` (same message race); stopped for Codex.
  - Follow-up (assigned by a later prompt): `SharedSpaceTests` waits for the state and its message (Codex's script 3/3 before, 3/3 passes after). Final code: Codex 3/3, 4/4, 2/2; **full suite 2,380 tests, 1,193.9 s, 2,354 OK, 0 failures/errors, 26 skipped. Gate passed.**
- M5 checks on the final code: node gates 38/37/22/29 (new)/32; Dashboard V2 and group Python tests 157 tests: 156 pass + 1 skip; real headless Edge against the fake server 78/78 (PC light/dark, phone 390/375); full suite on the final code (unittest discover, the 1 s synthetic clip in the worktree's `input\`): 2,382 tests in 1205.9 s, 2,356 OK, 0 failures, 0 errors, 26 skipped with reasons. Three read-only reviews: no CRITICAL or HIGH; open for Codex: `accounts.problem_text` host names reach phone clients. Waiting for Codex's review.
- M5 review fix (plan §9.18, 2026-10-09): Codex's one P2 is fixed. A failed `/api/phone-mode` no longer counts as the PC: account buttons only after an answer with `remote: false`; unknown shows "Kiểm tra lại" (GET only) and at most 5 automatic retries. Codex keeps `problem_text`. Checks: node gates 38/37/22/42/32; Codex's script `--expect-pass` passes; headless Edge against the fake server 59/59; focused Python 157 tests: 156 pass + 1 skip; full suite 2,382 tests in 1142.9 s, 2,356 OK, 0 failures, 0 errors, 26 skipped with reasons. Codex re-reviewed and closed M5.
- M6 (plan §9.19, 2026-10-09): integration checks with fixtures only (no real source, session or Control Center). An integration matrix maps each M6 and §9.10 requirement to its tests and level (E2E, real wiring, unit, fake UI). A new harness drives Dashboard V2 in headless Edge through the real Control Center handler, download service, worker, store, account runtime and account provider on a fixture HTTPS site (E1–E7, C1). Seven production fixes, each with a test that failed first: the version-restart count of ticket refreshes; the FILE_RETRIES bound (whole-file answers cut low and high in turn never ended a run, on `main` too; a 200 of a really new version sets the progress mark back once per run); a resumed range answered with another 2xx (203…) is no longer appended (BAD_RESPONSE); an episode list lost to a dispatch race; the waiting count of other Windows accounts; a left probe temp folder; "Tải các tập còn lại" checks and variant labels. Two rounds of adversarial checks of the two later transfer fixes. Round 1 found the 200-of-a-new-version regression and the other-2xx append (both MEDIUM) and two LOW test and docstring gaps; all fixed. Round 2 (regression, termination, tests and docs): termination not refuted (exhaustive model check, 18/18 configurations without a cycle); two LOW (the plain path's probe validator, fixed; a second version change in one run gets no further reset, documented); mutation testing of the new lines: 21 mutants, 13 caught at first, 19 after the added tests, 2 documented survivors (Last-Modified at the call site, a shared bound that differs only when a file changes twice in one run). The round-2 judge step was stopped to save time once all three lenses had reported; its processes and temp folders were cleaned. A race in an M6 test (events read right after a state) was fixed with `wait_event` and checked with every event written 1 s late. E2E and crossing (`BILIFLOW_REQUIRE_E2E=1`): 12 tests, 12 pass + 0 skip (594 s after the last fixes); node gates 38/37/22/54/32; Codex's `download_account_m5_mode_review.py --expect-pass` passes on a byte-identical copy; the 12 file-download modules 402 pass; full suite on the final code (unittest discover, `BILIFLOW_REQUIRE_E2E=1`, the 1 s synthetic clip in the worktree's `input\`): 2,433 tests in 1,792.2 s: 2,407 pass + 26 skip, 0 failures, 0 errors (same skip reasons as before; none in the download or account tests). M6 does not prove a real source (M7). Waiting for Codex's review before M7.
- M7 (plan §9.20, 2026-10-09/10, **closed in the worktree on 2026-10-10**, see the end of this item): adapter `release-forms` for the first real source, from a survey the user confirmed step by step (real hosts and ids only in a Git-ignored survey outside the repository). Coded and fixture-tested: `FilmPageReader` (seasons/episodes from the page's labelled numbers, key `s<season>:e<episode>`, file id = variant), `NotificationsVerifier` (portal page other than the sign-in page + a fresh notifications answer with the exact schema), read-only registries, `get_ticket` opening exactly the chosen closed season with a re-read and a single-match trigger before one click. `reads_tickets = False` until a real ticket page is seen, so no ticket is spent. 27 new tests, written after the code; 4/4 hand mutants caught. Tests: all `test_download_account*` with `BILIFLOW_REQUIRE_E2E=1`, 460 tests in 1,461 s: 459 OK, 1 failure (E6 still asserted an empty verifier registry; fixed, rerun 1/1 OK); the full suite was not rerun in this round. One real ticket page was seen (one ticket, in the user's Chrome by the user's choice; structure only). After Codex's M7 review:
  - P2 is fixed: the film id is re-read right before the ticket click. Codex's script, unchanged, went from 2/2 FAIL to 2/2 PASS.
  - Ticket provenance: a ticket page that names no file id is taken only through this click's own request and checked navigation chain.
  - `ticket_page` is implemented: ready only for an unlocked `success` with a link; `blocked` is a challenge; `error` is a failure.
  - Exception A (the one exact ad script the film page's gate needs, ticket runs only) is implemented in manual permission mode with the user approving each edit. It is a trial Codex chose, not a risk the user accepted.
  - `reads_tickets` stays False: the real ticket page's logic is not yet known.
  - The private config draft has portal and tickets only, because the bare file host was not observed.

  - Tests on the final code:
    - Hand mutation: 16/16 mutants caught.
    - Read-only review: 0 CRITICAL/HIGH; the MEDIUM finding was fixed; 3 LOW are documented.
    - Focused tests: 252 OK. Codex's script: 2/2 PASS.
    - Full suite: 2,499 tests; 2,471 pass, 26 skip, 1 failure, 1 error. The failure is an E2E dashboard timeout under load; the error is a Windows file lock in an unrelated test. Both passed when rerun alone.

  Blockers for real acceptance: the file host, the ticket page's logic, a test instance, the user's sign-in in BiliFlow's window, one ticket and a probe ≤ 1 MiB.

  Final verify (2026-10-10, plan §9.20):
  - The first request of a ticket must use the entry's method (POST for release-forms). Codex's two scripts pass 2/2 each, unchanged.
  - New acceptance probe `biliflow.download_account_probe` with 19 tests: test roots only, at most one ticket, ≤ 1 MiB, never a transfer.
  - Accepted on the real source with BiliFlow's own session, without a ticket: the user's sign-in in BiliFlow's window; the gate with exception A and an actionable trial click (no POST); the series list (2 × 6) and a one-file film that stopped before the click. No media bytes were received.
  - Still blocked: the bare file host (unknown, not guessed) and the ticket page's contract, so `reads_tickets` stays False. Decisions for the user/Codex are in plan §9.20. A proposed `AGENTS.md` Network text for exception A is there too; `AGENTS.md` is unchanged.
  - Tests: focused and E2E (provenance, listing, release_forms, sources, page_script, probe, browser, config, login, E2E, E2E flows, crossing; `BILIFLOW_REQUIRE_E2E=1`) before the review fixes: 281 tests in 1,306.5 s, OK, 0 skip; after the review fixes, the changed modules (probe, provenance, listing, release_forms, sources): 145 tests in 536.6 s, OK; Codex's two scripts on the final code: 2/2 each (SHA-256 unchanged); targeted mutants of the review fixes: 3/3 caught; Full suite on the final code, one run (`unittest discover`, `BILIFLOW_REQUIRE_E2E=1`, the 1 s synthetic clip in the worktree's `input\`), 2026-10-10 09:44–10:18: 2,523 tests in 2,068.4 s, OK: 2,497 pass + 26 skip, 0 failures, 0 errors (same skip reasons as before; none in the download or account tests; log `m7\logs\full-2.log` in the session scratchpad). This replaces the earlier run with 1 failure and 1 error; neither came back.

  One ticket observed (2026-10-10 afternoon, plan §9.20 "Quan sát một vé"):
  - New observation mode `biliflow.download_account_observe`, for test roots only. It does one click and watches the ticket page, never taking a link or starting a transfer. It saves the bare file host only on request and probes that same ticket within 1 MiB.
  - New production check: the clicked control must submit exactly the entry's request and method.
  - Accepted on the real source with BiliFlow's own session:
    - 1 ticket POST, READY;
    - the ticket host signs itself in from the portal's session through 6 redirects in the click's chain;
    - an 8 s countdown, with the advert check passed;
    - link on a third host (saved only in the test root's private config);
    - probe: 206, strong ETag, Matroska HEVC/AAC, **1,048,576 media bytes**.
  - The page's own claims about expiry and IP are recorded as text only.
  - **`reads_tickets` stays False** in this round (turned on later that day, below): the agent's change to turn it on was refused by the session's safety classifier. This waits for the user's or Codex's decision.
  - Tests: Codex's two scripts 2/2 each, unchanged (SHA-256 the same); observe/release_forms/probe 88 OK; full suite on the final code, one run (`unittest discover`, `BILIFLOW_REQUIRE_E2E=1`), 2026-10-10 11:55–12:32: 2,551 tests in 2,207.4 s, OK: 2,525 pass + 26 skip, 0 failures, 0 errors (same skip reasons; none in the download or account tests). These results are those of the code with the reader off.

  Reader turned on (2026-10-10 evening, plan §9.20 "Bật reader và đóng M7"):
  - The user agreed after Codex's review. `FilmPageReader.reads_tickets = True` was its own edit for the user to approve by hand; it was not refused this time, and nothing was worked around. The docstring states the observed contract and what was not seen. Every check stays.
  - Tests: `RegistryTest` (the reader is on, the registries are read-only, an adapter without a reader is still unsupported). The two tests for a reader without tickets use `ListOnlyReader` instead of the production object. New `ProductionReaderTest` runs the registered reader through the sign-in chain and the countdown: one POST, and the file host gets no cookie. Mutants on a copy: 2/2 caught.
  - No new ticket or real source.
  - Tests on the final code: the 11 affected modules (`BILIFLOW_REQUIRE_E2E=1`) 245 tests in 1,310.5 s, OK, 0 skip; Codex's two scripts 2/2 each, unchanged (SHA-256 the same); full suite, one run, 2026-10-10 13:30–14:07: 2,553 tests in 2,216.8 s, OK: 2,527 pass + 26 skip, 0 failures, 0 errors (same skip reasons; none in the download or account tests).

  **M7 is closed in the worktree. Codex's review comes before `main`; keeping exception A and the Network text at the merge is a separate decision.**
- Not merged, not committed, real Control Center untouched.

## Anime gore rule C1 on (2026-10-07, `main`, pushed)

- Pushed at the user's request ("push lên đi bạn"): `origin/main` `d1296a7..47a1956`, then this record.
- The user turned C1 on after gates 4.1–4.7.
- Gate 4.7 ran on an official-channel episode of the same series: 3 real-blood cards, none moved, lowest blood 0.305.
- C1 applies only to new "Quét nhanh" animation scans. Moved cards stay in "Ứng viên phụ", which does not block an export. Details in CHANGELOG.

## Anime gore evidence and card hint (2026-10-07) — in `main` (`927006c`, pushed with C1 on)

- The 2026-10-02 Gore C1 patch (`docs/ANIME_GORE_PLAN.md` steps 1 and 3) is re-merged on `main` `d1296a7` in the worktree `temp\wt-gore-c1`. Animation gore intervals record tag evidence, and gore cards carry it with a one-line hint. The hint is shown on Dashboard V2 only; the classic page stays byte-identical. Rule C1 is in the code but off.
- Scan caches: `cli.py` and `intervals.py` are not touched, so only the `animation_safety`, `gore` and `violence` stages get a new cache key. Those stages run for animation videos with a safety group and for live-action videos that select only one of gore and violence. OCR, logo, 18+ and `live_safety` (live action with both groups) caches stay valid.
- Checks: gore tests 19 OK; focused tests 236 OK; node gates 32/38/37/22; offline C1 gates passed with the same numbers as on 2026-10-02 (`temp\gore-c1\gates-d1296a7`). Full suite 1947 OK (35 skipped). Details in CHANGELOG.
- GPU rescan of both Golden animation films, 2026-10-07: `reports\benchmarks\gore-triage-20261007-gpu`. Gates 4.4 and 4.5 passed: every interval is unchanged, and the real fp16 evidence is within 1.9e-4 of the re-score. At the C1 thresholds the results equal the offline run.
- User check sheet, 2026-10-07 evening:
  - Gate 4.6 passed: all 14 cards C1 moves are false alarms.
  - Gate 4.7 was tried on an open 3D film (`reports\benchmarks\gore-triage-film3-20261007`). C1 moved nothing there and no real blood sat near the line, but the film had only one real-blood card, so it does not qualify.
  - C1 stays off. Open: a 2D anime film for gate 4.7, then the decision to turn C1 on.
- At the user's request it stays out of the main folder while jobs run. Merging, committing and the Control Center restart (Stop, then Start) wait for the user. Turning C1 on needs plan gates 4.6 and 4.7.

## Tailscale managed by BiliFlow (2026-10-07) — in `main` and pushed; the user's real tests passed

- The user's choice: BiliFlow downloads, checks, installs (into `C:\Program Files\Tailscale` since the security review of 2026-10-07; auto-update, unattended), signs in and drives Tailscale from Dashboard V2 → Cài đặt → Tailscale; it starts the service when needed; the phone may extend the 8 hours while open over Tailscale. Plan `docs/TAILSCALE_PLAN.md` (T1–T5 done), details in CHANGELOG.
- Tests: manager and phone Tailscale 57 OK; V2/phone/contract 123 OK; node gates 37/37; full suite 1646 tests (after the security fixes and the Program Files change), 0 failures, 28 known input-video errors. Security review: no CRITICAL; fixes applied; the HIGH about the SYSTEM service in a folder every local account can modify was settled by the user: Tailscale goes to `C:\Program Files\Tailscale`.
- 2026-10-07 about 07:40 the user asked to go ahead (no job was running) and to commit: committed `e949c52`, then `main` (`75aae1a`) merged in (conflicts only in the docs). Next: put the main folder on this merge, start the real Control Center; the user presses "Cài và cấu hình Tailscale", signs in, installs the phone app, opens the mode over Tailscale and tests 4G and "Gia hạn" on the phone. Tailscale is not installed on the PC yet. The merge into `main` and the push wait for the user's test and request.
- 2026-10-07 08:03–08:08 the user's real test on the real Control Center (`a2b5398`, main folder detached there, started 07:54): install (UAC), sign-in, "Mở cho điện thoại ngoài nhà" and the phone's sign-in over Tailscale worked. "Gia hạn" worked but looked like it did nothing (it set 8 hours from the press). Changed at the user's choice: presses add up, at most 24 hours (committed `d003db3`; the main folder and the real Control Center run it since 08:35; the user's re-test passed).
- 2026-10-07: over Tailscale a device of the PC's own Tailscale account opens without the code (the user's choice "Bỏ mã nếu cùng tài khoản"; `tailscale whois`; security review fixed, see CHANGELOG). Committed `d570295`; the real Control Center runs it since 09:58 (stopped, then started). The user's test passed at 10:31 ("vào được rồi mà không cần mã"; the event log shows the phone signed in by `tailscale_account`). At the user's request (decided before the test: merge and push once this is tested) `main` was fast-forwarded to the branch and pushed.

## Phone mode over Tailscale (2026-10-06) — in `main` with the section above

- The user's choice: reach the phone mode from outside the home Wi-Fi through Tailscale, with the same actions as on the home Wi-Fi (permanent deletes included).
- Done in worktree `temp\wt-phone-tailscale`: `network` wifi/tailscale in `phone_access.py`, `POST /api/phone-mode`, the V2 Cài đặt panel, `Start-BiliFlow-Tailscale.cmd`, `docs/DASHBOARD_V2_PHONE.md` section 8. Details in CHANGELOG.
- Tests: `test_phone_tailscale.py` 18 OK; phone/hardening/frontend 70 OK; full suite 1607 tests, 1544 OK, 35 skipped, 28 errors; all 28 are `StopIteration` in `test_job_pipeline` / `test_job_ocr_option`, which need a real `input\*.mp4` that the worktree does not have (they do not touch the phone code).
- Superseded on 2026-10-07: BiliFlow now installs Tailscale and adds the firewall rule itself (section above).

## Source providers in `main`; real Control Center restarted; the user's real test passed — 2026-10-06

- `main` = `a3b69f0` and the record commit `bd4d019` (fast-forward from `84a5db1` at the user's request; pushed: `origin/main` `84a5db1..bd4d019`). The real Control Center runs it since 21:52:47 (PID 55128, phone mode off).
- Active download readers: `player-hls`, `article-mp4`, `embedded-media` (exact hosts from the Git-ignored `config\download_providers.local.json`, 2 each) and `direct`. Every other link still goes to yt-dlp. `embedded-media` uses Playwright 1.63.0 with headless Edge.
- Verified: `test_download*` 453 OK, `test_dashboard_v2*` 95 OK (1 skipped), `test_control_center*` 55 OK, Node gates 36/33/22/31 all passed, tool audit 0 blocked, model license audit 9 allowed and 0 blocked. The full suite of the same tree: 1858 OK (26 skipped) with a synthetic clip.
- The user's real test on the real Control Center passed (about 22:27): task #4 retried and four new links, five videos into `input\` as jobs 70–74 (43–48 minutes). Job 70 is task #4's episode (311,090,096 bytes, the size of the earlier probe). The user removed the rows afterwards; at 22:45 the jobs were in review, scan and export as usual.
- Backup of the main folder's superseded draft documents, and of `state\*.sqlite3` before the restart: `temp\main-folder-backup-20261006-214658`.

## Headless embedded-player adapter — 2026-10-06 (uncommitted)

- The user approved adding Playwright. Installed/pinned Playwright 1.63.0, pyee 13.0.1 and greenlet 3.5.6; uses the existing Microsoft Edge, with no browser download. Dependency licenses are recorded in download_tools.json (Apache-2.0, MIT, MIT AND PSF-2.0); tool audit has zero blockers and the equivalent standard model license audit has 9 allowed, zero blocked.
- Native `embedded-media` is registered alongside `player-hls`, `article-mp4`, and `direct`. Exact real hosts are enabled in the Git-ignored local config. It runs Edge headless in a fresh profile under the task directory, closes the browser and removes the profile on completion/error/stop; it never uses a user's profile or calls an outside extraction script.
- Page requests are intercepted and fulfilled via SafeHttp only; no browser route continues unchecked. Service workers, WebSockets, popups and browser downloads are blocked. Cookies/auth headers are not forwarded. Media itself is aborted in the browser and fetched by the existing native transfers after identifying the designated player subtree. Internal browser URLs do not make HTTP requests. Signed links remain private.
- Verified real HTTPS source probe: the user-provided episode source has H.264/AAC, duration 2634.19 seconds and size 311090096 bytes; resolving again produces the same identity. No browser profile remained. This was probe-only under an isolated temporary root, not a full episode download or production API write.
- Five synthetic browser tests passed: nested movie iframe versus ads, actual headless launch, no cookies forwarded, private-address refusal, stop during page read/profile cleanup, missing library error, and the real download worker publishing a verified synthetic MP4. Full downloader regression: 453 tests OK (7 conditional skips); includes the five browser tests. git diff --check clean; local host config is Git-ignored.
- Worktree remains `temp\wt-download-source-providers`, branch `feat/download-source-providers`, uncommitted. No merge, push or production Control Center restart. Earlier notes about pending browser approval are historical and superseded here.

## Native page adapters — 2026-10-06 (uncommitted)

- Worktree `temp\wt-download-source-providers`, branch `feat/download-source-providers`. The existing common downloader is preserved. No merge, push, or restart of the production Control Center.
- `player-hls` reads only the designated player iframe, its episode JSON and the public literal URL transform. It resolves a fresh HLS link on every probe/download/resume. PNG cover removal is opt-in for this adapter, bounded and CRC checked; ordinary HLS still rejects PNG responses. Every stored TS packet is validated before ordered FFmpeg remux.
- `article-mp4` reads the page's public article API through bounded checked POST. It ignores main/trailer and advertising sources, unwraps the outer media parameter exactly once, requires video/audio and at least 600 seconds, and asks for a version when several exist. The chosen version is resolved and probed before downloading. A moov index at the end of an MP4 is read through bounded byte ranges; ffprobe receives only a local sample.
- Both adapters run inside the backend and reuse queue, progress, cancellation, resume, verification and publishing. They call no external extraction/download script. Exact real hosts are enabled only in the Git-ignored local config. Signed media URLs are not stored in public identities.
- Verified with synthetic fixtures and the downloader regression tests. Real HTTPS probes of the two user-provided sources succeeded: fresh identities matched, an HLS sample of three segments (12.02 seconds) passed video/audio/head/tail validation, and the API source reported H.264/AAC and about 125 minutes. These checks used an isolated temp root and did not download the complete films or submit tasks to production.
- Final checks: all 448 downloader tests OK (7 conditional skips), including 14 native-adapter tests; dashboard download gate 22/22; `git diff --check` clean. Local host config is confirmed Git-ignored and the active registry is `player-hls`, `article-mp4`, `direct`.
- Browser-based page extraction remains pending approval to add Playwright to BiliFlow (not currently installed). The two HTTP adapters add no package or tool dependency.
- The common-only scope notes below describe the earlier implementation phase; the native adapters above supersede their statements that no site provider exists.

## "Tải video": direct MP4/HLS links and the provider interface (2026-10-06): branch `feat/download-source-providers`, not committed

- Worktree `temp\wt-download-source-providers`, made from `main` `6dc2210` with the uncommitted scope documents. Nothing is committed, merged or pushed. The running Control Center still runs `main` and was not restarted.
- **Works, tested only with self-made fixtures.** These links are downloaded by BiliFlow itself through the existing queue, progress, Stop, Resume, Cancel and Retry:
  - the generic provider `direct`, for links to a file (`.mp4`, `.m4v`, `.mov`, `.mkv`, `.webm`, `.ts`);
  - VOD MPEG-TS HLS playlists (`.m3u8`). Segments download in parallel and are joined in order with the project FFmpeg, without re-encoding.

  Picture and sound are checked at the probe, and `verify_video` runs before `input`. Signed links are resolved again on every resume, with an identity check.
- **Not done.**
  - No site-specific provider (`SITE_PROVIDERS = ()`).
  - The readers for the three film sites of the reference document were declined.
  - DRM and live HLS are refused.
  - AES-128, fMP4/CMAF and other HLS forms go to yt-dlp, as do all other links.
  - No real link and no real TLS were tested.
- **Checks.**
  - Full suite: 1783 tests. The only errors are the 28 known `input\*.mp4` errors of a worktree.
  - `test_download*`: 380 OK, 196 of them new.
  - `test_dashboard_v2*`: 93 OK.
  - Node gates: 35 / 30 / 22 / 31.
  - Manual run on a test Control Center (temporary root, port 8797, fixture links):
    - the MP4 arrived byte-identical;
    - a stopped and resumed HLS download is byte-identical to an uninterrupted one;
    - Cancel left nothing behind;
    - no-audio, DRM and live were refused with their reasons.
- **Dashboard test run (2026-10-06).**
  - `tests/try_source_downloads.py` serves the fixtures over HTTPS (a throwaway test CA) and HTTP, with slow MP4/HLS links to press Dừng or Hủy. It needs no environment variable.
  - A test Control Center was started for the user on port 8797, on a temporary root.
  - Fixed: the progress of a direct file only moved in 256 KiB steps (`read1` now).
  - HTTPS is tested locally: `tests/test_download_https.py`, 9 tests.
  - `test_download*`: 393 OK.
  - Full suite: only the 28 known errors, plus one Windows file-lock error while a test was deleting its temp files (that module passes alone).
  - The code review approved; its LOW note is fixed.
- **The user's test, the full-suite errors and the dispatcher (2026-10-06).**
  - The user downloaded `demo.mp4` and `slow.mp4` on the 8797 test dashboard. Both completed and played with picture and sound. The user did not report trying Stop, Resume or Cancel.
  - Full suite after every fix of this round:
    - in the worktree, 1837 tests and 28 errors, all of them the `input\*.mp4` `StopIteration` errors. `main` `6dc2210` has the same 28 in the same environment.
    - in an isolated copy with a synthetic clip in `input\`, 1837 tests, OK (26 skipped).
  - The earlier `WinError 32` is a race in an unchanged detector test: the `BackgroundSha256` thread still holds the file. It is not fixed here; see CHANGELOG.
  - Dispatcher (`SourceRegistry`), completed rather than duplicated:
    - one host parser (`check_link`, plus `check_host` for the config);
    - the registry gates site providers by exact host;
    - the config can only switch on providers of the code, and its mistakes show on the downloads page, with their reasons and never more of a link than its host;
    - a host listed without a provider is only recognized: it goes to yt-dlp, with a note.
  - Review fixes, over four rounds (nothing above LOW after the first):
    - Hosts that Python's IDNA reads otherwise than a browser are refused, with a hint to use the `xn--` form: `ß`, `ς`, ZWJ/ZWNJ, U+1806 and characters added after Unicode 3.2. IP shorthands are refused too.
    - The Referer goes out as a browser sends it by default, on every request and redirect.
    - A lone surrogate in the config no longer breaks `/api/downloads`.
    - A race in the fake yt-dlp's call log is fixed.
  - New worker-level tests use `.example` hosts. Mutation check: 17 of 17 caught.
  - Providers registered and working: only `direct`.
- **Next.**
  - The user reviews the branch and decides whether to commit it.
  - Optional: the user tries Stop, Resume and Cancel on the test dashboard, then one HTTPS MP4/HLS link of their own on the test Control Center (`--probe` first).
  - The user decides on the User-Agent (it is now the same browser string yt-dlp sends).
  - A real check with an allowed direct link runs only on a test Control Center, with a link the user gives.
  - A separate decision: the `BackgroundSha256` race (detector code).
  - Site providers are a separate later step: none is written.

## Downloader provider scope expanded (2026-10-06) — docs only

- At the user's request, the project no longer forbids site-specific source resolution for public page/player data. AGENTS.md and VIDEO_DOWNLOAD_PLAN.md now describe the allowed provider scope.
- No provider/runtime implementation changed, no real download was started and the Control Center was not restarted.
- DRM/paywall circumvention, user cookies/logins, challenge bypass and browser impersonation to bypass blocks remain excluded. Public-repository hygiene, isolated download tests and source-video protections are unchanged.

## Dashboard V2 is the dashboard; the classic one is off for now (2026-10-06) — in `main`, pushed

- At the user's request, `/` on the PC opens V2 (`303` to `/dashboard-v2/`), as on the phone. The classic page stays in the code behind `CLASSIC_DASHBOARD` in `control_center.py`, for a rollback (set `True`, restart).
- V2 already makes every API call the classic page makes, so nothing is lost. V2 no longer calls itself a preview.
- The phone-mode notice (H3) moved with it: while the mode is on, V2 on the PC shows "Đang mở cho điện thoại: <link>" at the top of every page except Cài đặt, never with the code and never on the phone.
- Code review (agent): no CRITICAL or HIGH; the MEDIUM (that notice) and three LOW notes are fixed.
- Real machine: the Control Center was restarted at 20:37:51 with `Stop-BiliFlow` + `Start-BiliFlow` (`Start-BiliFlow.cmd` alone reuses a running one). `/` opens V2, and the user confirmed it. The phone mode is off after the restart until the user turns it on. The scan cache is not affected.
- Tests: 8 focused tests RED then GREEN; only one test pins the switch (with it on, 1 of 96 related tests fails); node gates 36 / 33 / 21 / 31; headless Chrome on a test Control Center 6 of 6; full suite 1589 tests OK (26 skipped), with the usual temporary synthetic clip.

## Dashboard V2 list no longer blinks on every refresh, U4 (2026-10-06) — in `main`, pushed

- The user saw the video list blink on the phone every few seconds in every tab, and asked for a check of the look and of the refresh cost.
- Cause: every 3 s poll that changed anything rebuilt all of `#main` (on the overview the CPU/RAM numbers always change), so each row and its lazy-loaded poster were re-created. The drawer was rebuilt the same way, and the poll had no guard.
- Fix, static files only (`dashboard_v2/app.js`, `adapter.js`, `download-live.js`):
  - a poll patches the page in place, using the patch of "Tải video", now shared;
  - the drawer is patched too; the menu is written only on change; row posters load at once; a poll no longer scrolls the page;
  - one `/api/status` poll at a time, none while the tab is hidden, a fresh one when it shows again;
  - a GET with no answer after 15 s is cut (offline banner, next poll retries); a POST never is.
- Checks on a test Control Center at 390×844 (details in CHANGELOG):
  - per 5 polls on the overview: 25 nodes and 8 posters re-created before, none after; layout + style 48 ms before, 4–7 ms after;
  - 17 of 17 behaviour checks passed (drawer, search box, selection boxes, download link box, hidden tab, offline banner, drawer actions changing, no JS error).
- Code review (agent): no CRITICAL; the HIGH (a hung poll could stop polling) and the MEDIUM (`browser-check.cjs` still asserted rebuilds) are fixed, and so are the LOW notes.
- Tests: node gates 35 / 33 / 21 / 31; `tests.test_dashboard_v2_*` and `tests.test_download_*` 277 OK.
- The user checked the phone against the real Control Center: OK. At their request `fix/v2-list-flicker` was committed, `main` was fast-forwarded to it, and `main` was pushed to `origin`. That push also published the 98 earlier local commits of `main`.

## `main` has Dashboard V2, "Tải video", the permanent delete and the phone deletes (2026-10-06) — local, not pushed

- The user could not test the phone yet. At their request, the phone deletes were checked, then `main` was fast-forwarded from `f6996bb` to the tip of `test/download-delete`. The main folder runs `main`. `origin/main` is still `f6996bb`.
- Checks (details in CHANGELOG):
  - end-to-end on a test Control Center through the real phone listener at 375 px: all four delete actions worked and archive was refused;
  - logic review: no CRITICAL, HIGH or MEDIUM finding;
  - performance: fine at today's size. The SHA-256 check takes about 5 s per exported episode and previews take milliseconds. It gets slower from about 200–500 jobs;
  - full suite: 1587 tests OK.
- Fixed before the merge: the phone-mode risk wording, a phone hint for long deletes, a stale help text and fallback about downloads, and the API table of `docs/DASHBOARD_V2_UPDATE_GUIDE.md`.
- Next:
  - the user tests the deletes from the phone when they can;
  - push `main` only when asked;
  - the later performance items, the stale `browser-check.cjs` download check and the security hardening ideas wait for a request.

## Phone deletes (2026-10-06) — branch `test/download-delete`, local, not pushed

- The user's real-machine test of the merge `a6c8b8b` passed. They then asked that four actions also work from the Dashboard V2 phone mode:
  - "Dọn video mất gốc";
  - "Xóa video gốc";
  - "Hủy" then "Xóa video" for a video waiting for setup.
- `/api/source-cleanup` and `/api/job-delete` are now phone routes. Cookie, token, Origin, `preview_id` and `confirm_permanent` are still required; archive, restore and the bin check stay PC only. See CHANGELOG and `docs/DELETE_FLOW_PLAN.md` section 10.
- Risk: plain HTTP on the home Wi-Fi. A stolen phone session could "Hủy" any unfinished video and then "Xóa video" it.
  - The security review found no CRITICAL or HIGH issue. The user was given the choice to limit phone deletes to cancelled videos without a review queue, and chose to keep the PC behaviour.
  - The risk is written in `docs/DASHBOARD_V2_PHONE.md`. Code review approved; its LOW notes are fixed.
- Checks: full suite 1587 tests, no failure. The only errors are the 28 known `input\*.mp4` errors, which pass with a synthetic clip. Node gates: 35 / 30 / 21 / 31.
- Next: the user approved restarting the Control Center on this commit, which the phone change needs (only with no job or download running). Then the user tests the deletes from the phone. Push or merge only when the user asks.

## Test branch `test/download-delete` (2026-10-06): "Tải video" and the permanent delete, for one real-machine test

- `feat/delete-flow` (`0ed2f96`) with `feat/video-download` (`caedb8b`) merged in; neither feature branch was changed. Six files conflicted and keep both sides: routes, phone rules, the V2 page, the endpoints pin and these docs. See CHANGELOG.
- Checks: full suite 1579 tests, no failure, only the 28 known `input\*.mp4` errors of the worktree (they pass with a temporary synthetic clip); node gates `verify` 35, `verify-adapter` 30, `verify-download` 21, `verify-review` 31.
- Next: the user's real-machine test of both features (download a link; cancel and "Xóa video" a downloaded video; "Dọn video mất gốc"; "Xóa video gốc"). Push or merge only when the user asks.

## Permanent delete flow (2026-10-05) — branch `feat/delete-flow`, local commits, not pushed, not merged

- The user's choice (plan `docs/DELETE_FLOW_PLAN.md`):
  - "Dọn video gốc" becomes "Xóa video gốc": the source is deleted for good after its SHA-256 check (no Recycle Bin), with the export's manifest (the `.mp4` stays) and the job's own data (DB rows, report folders, logs).
  - "Xóa video" removes a cancelled job (its source too) or a job whose source is gone; "Dọn video mất gốc" does it for all of them at once.
  - `output\` is never touched beyond that one manifest. No deletion log is kept.
- Phases:
  - D0 `22216af` (plan, AGENTS.md invariant);
  - D1 `b3be8ca` (`job_purge.py`, `JobStore.purge_job`, the new "Xóa video gốc");
  - D2 `6c79aeb` (`job_delete.py`, routes, `/api/status` hints, the D1 review fixes, `confirm_permanent`);
  - D3 (Dashboard V2) and D4 (classic page `/`) committed together;
  - D5: these docs and the full suite (1383 tests OK, 26 skipped; Playwright browser checks not run on this machine).
- Known gap: on the classic page a hidden cancelled video has no "Xóa video" button (show it again first, or use V2).
- Protections: the golden set (#37–#39 today), benchmark folders, links and junctions, busy jobs, legacy bin-held sources, and an install-root guard (a worktree cannot delete the main folder's files). Both POSTs need `confirm_permanent: true`; they were PC only until the phone change of 2026-10-06 (top section).
- Real machine (read only): 27 jobs have lost their source and can be removed by "Dọn video mất gốc"; the only cancelled job that still has its source is #39 (about 7.1 GB), which stays locked in the golden set.
- Next: the user's test (plan section 8). It needs the user's consent to move the main folder to this branch and restart the Control Center, with no job running.

## Real video download "Tải video" (2026-10-05) — branch `feat/video-download`, local, not pushed, not merged

- Plan, decisions and log: `docs/VIDEO_DOWNLOAD_PLAN.md` (D0–D5 done). The Dashboard V2 page `#downloads` downloads with yt-dlp, on the PC and from the phone.
  - Any public link is accepted. The yt-dlp probe decides; a page it cannot read shows "Chưa hỗ trợ".
  - A checked file goes into `input\`; no auto scan.
- Commits: D0 `6a6fdfd` (tools: yt-dlp 2026.08.19, yt-dlp-ejs 0.8.0, Deno 2.9.7 copied from WinGet; `config/download_tools.json`), D1 `918bc2c` (backend), D2 `a046a93` (API and Control Center routes, phone), D3 `cc7812b` (live page), D4 `f378619` (real download on a test Control Center), D4b `6f4fbee` (no source list; probe decides; review fixes), D5 (docs and the `AGENTS.md` rule).
- D4 (temporary root, port 8797, links from the user):
  - YouTube → COMPLETED, 1080p H.264 + AAC, 1.28 GB, picked up as NEEDS_METADATA.
  - The user's reference movie page → not supported ("Unsupported URL"), probe only.
- Full suite on D4b: 1488 tests, only the 28 known `input/*.mp4` errors of the worktree. The scan cache key files are untouched.
- Next: the user's test on the real machine. It needs the main folder on this branch and a Control Center restart, both only with the user's consent and no job running. `feat/dashboard-v2` has moved on since a7d8f18 (`55c6e62`, R4-B4 and R4-U1/U2); bringing those commits in is the user's call. Merge into `main` only when the user asks.

## Dashboard V2 review dialog R0–R4 and the stronger logo cover (2026-10-04/05) — branch `feat/dashboard-v2`, not merged

- The V2 review dialog replaces the prototype's review box (plan: `docs/DASHBOARD_V2_REVIEW_PLAN.md`).
  - Since R4, "Duyệt cảnh" in the detail drawer opens it over the current screen at `#review/<id>/<view>`, on the live page and the demo.
  - It covers the classic review page (P1–P17): cards, frames and the video of each card, timeline, zoom, technical details, decisions with the classic confirms, region buttons, logo memory, keys, undo, auto-next, "Giữ tất cả" / "Dùng đề xuất", and "Xuất video" through the V2 export dialog. The deliberate differences S1–S10 are listed in section 5 of the plan (S10, the gore hint on V2 cards only, since 2026-10-07).
  - The classic page `/review/<id>` is byte-identical (D2) and one link away ("Mở trang duyệt cũ").
- Batches and checks:
  - R0 view-only dialog `b853963` (M4), `0e43fa1`; R1 media `1191690` (R1-B1 `b8b458f`); R2 decisions `539e6a6`; R2-B1 and R2-B2 `108f27b`; R3 bulk actions and export `bb763ad`. The local machine checked each batch (section 8.2); the last check, on `03666a6`, passed.
  - The user tried R2 (section 8.3, steps 1–4) on a real job: pass (U-R2).
  - R4 (2026-10-05): phone and laptop layout (44 px touch targets, 12 px text, fading chip row, the tools row wraps, a full-screen dialog when held sideways), a check through the real phone listener, the "Duyệt cảnh" switch, and R3-N1 (one Esc closes only the dialog on top).
  - The local check of R4 (`4c7ac3c`) passed except R4-B1 (a box label cut by the card image), fixed in `fcfe616`. Its recheck (`ce9f865`) found R4-B2 (two labels over each other on a zoomed card), fixed on the local machine in `4046090` and measured again: pass. During the user's acceptance test U-R4 (section 8.3) the user found R4-B3 (V2 dialogs blinking on their PC), fixed on the local machine in `cbe215e` and `35127e6` (no `backdrop-filter` in V2); the user confirmed. The user then passed step 7 (phone) and step 8 (narrow window) except R4-B4 (the scrollbar of a narrow window dragged the page behind the review dialog), fixed in `e3b894f`. At the user's request, `73ccc51` adds R4-U1 (rows waiting for or in their export show only ⋯) and R4-U2 ("Xuất lại" in "Hoàn tất": a warning that it exports with the old choices and how to review again; while the export is still in `output` it only explains how to export again). Next: the user tries R4-B4, R4-U1 and R4-U2.
- Stronger logo cover in exports (`0334f8c`, made on the local machine at the user's request):
  - A new export covers a reviewed regional logo BLUR with FFmpeg `delogo` first, then a blur that grows with the region (`delogo_blur_v1`). Full-frame blurs and edit plans without a method render as before.
  - The export identity is unchanged, so an export made before still proves the current decisions. "Dọn video gốc", "Lưu trữ" and "Xuất video" treat it as before.
  - To give an already exported video the new cover, the user moves its old export (`output\…-reviewed.mp4` and its `.manifest.json`) to the Recycle Bin, then exports again. BiliFlow never does this itself.
  - The Control Center uses the new cover once the main folder runs a commit that contains `0334f8c` and the Control Center restarts (ask the user first).
- Verified on the cloud for R4: see the R4 table and the log in section 11 of the plan. The pre-existing PowerShell-only errors in `test_control_center` and `test_skip_export` are unchanged.
- Next: the rest of U-R4. Merge into `main` only when the user asks.

## Dashboard V2 prototype (2026-10-03): independent demo, not integrated

- Latest feedback: **Tải video** now supports multiline batches, adding during active downloads, separate task rows/progress, filters/counters, 1–3 download slots, pause/cancel/retry and folded sample logs. Queue is independent of the GPU processing queue. `download-demo.js` owns the pure sample state machine. Real command/PowerShell integration is documented only, not implemented; no downloader/API transport is connected.
- Multi-download verification: 25 contract checks, 16 Browser checks (batch rejection, cross-source addition, slot/FIFO, pause/failure/retry/completion, filtering/navigation, no processing-count changes, 375px light/dark and concurrency selector); 64/64 production hashes unchanged. Evidence `temp/dashboard-v2-evidence/multi-download-*`. No server restart.
- Added requested **Tải video** page (`#downloads`): YouTube / Phimmoi (mock), domain combo, URL input and timed progress with verification/completion/cancel. `phimmoi.example` is a mock address; no real downloads or backend API mapping yet. All request/task data remain in memory; navigation retains them, reset/reload clears them. Page navigation scrolls to the top.
- Download revision verification: 22 contract checks and 11 Browser checks passed; live source 64/64 hashes unchanged; demo asset HTTP/syntax verified. Also rechecked scan→export state changes: counters and active-card stage update correctly from the changed mock snapshot. New evidence: `temp/dashboard-v2-evidence/download-*`. Integration guide distinguishes simulator from future downloader contract and specifies live snapshot refresh requirements.
- Latest user feedback complete: topbar light/dark toggle persists the preference; five overview cards separate scanning, pending review, ready export, rendering/verification and completed/skipped. Counts are videos; queued scan/export counts are separate. Card clicks filter the same counted group and clear old search. List labels clarify the combined review and export buckets without changing their IDs or API mapping.
- Latest verification: 20 contract checks, 13 Browser checks; source production 64/64 unchanged. Updated guide includes precise state predicates, frontend-only filter IDs and theme persistence. Evidence: `temp/dashboard-v2-evidence/theme-summary-checks.json` and new light/dark screenshots.
- User authorized the dashboard redesign first, with hardcoded data and complete mapping to current functions; backend audit fixes remain deferred.
- New files in `dashboard_v2/`, served independently at `http://127.0.0.1:8794/`. Current Control Center/dashboard, detectors, models and source videos remain unchanged.
- Overview, video filters/search/pagination, details, FIFO queue, scan/export/review illustrations, skip/reopen, hide/unhide, source management illustrations, logo memory and AI settings are available. All changes are in-memory synthetic data; no real API transport.
- Applied user design feedback: light surfaces with blue accents, larger labels/buttons, simplified progress drawer, secondary operations under “Thao tác khác” and folded audit/technical details. Fixed generic hover style making the backdrop opaque grey. All operation identifiers, gates and confirmations retained; `theme.css` loads after `styles.css` only in V2.
- Feedback revision checks: 9 focused Browser checks pass (hover backdrop, grouped scan/export actions, cancel confirmation, closing drawer, desktop/mobile layout); contract checks 18/18 rerun; demo server syntax and theme response verified. Production source hashes still 64/64 unchanged. Evidence: `light-ui-checks.json`, `light-processing.png`, `light-list.png`, `light-mobile.png`, `light-mobile-detail.png` in the evidence directory.
- Detailed mapping, schemas, payloads, integration sequence, gates and rollback: `docs/DASHBOARD_V2_UPDATE_GUIDE.md`. The future live version must retain `/review/{id}` and its full existing review workflow.
- Verified: 18 contract checks, 22 Browser checks, 6 static-server isolation checks; 1280 px desktop and 375 px mobile; all 64 Python production hashes unchanged. Logs and screenshots: `temp/dashboard-v2-evidence/`.
- 2026-10-03: committed on branch `feat/dashboard-v2` and pushed at the user's request; not merged.
- Next: a Claude cloud session follows `docs/DASHBOARD_V2_CLOUD_PLAN.md`.
  - Its work: mapping, the adapter, an opt-in `/dashboard-v2` route, and the cloud-safe tests marked in its checklist.
  - The local machine then tests the remaining items before any merge.
- 2026-10-04: the cloud finished phases 0–3 (c616bef). Local check passed:
  - full suite: 1215 tests OK;
  - V2 gates pass;
  - stage-cache fingerprints unchanged.
- Two display gaps were found. Batch 2 is planned in `docs/DASHBOARD_V2_CLOUD_PLAN.md` §12:
  - the gap fixes;
  - `render_request` sent by the backend;
  - a phone/laptop mode on the home Wi-Fi.
- Not merged; Control Center not restarted.
- 2026-10-04, later: batch 2 pulled (949f935).
  - Local check: 1236 tests OK after two Windows-only test fixes.
  - Security review: phone mode is safe on the home Wi-Fi; S1–S6 should be hardened.
- Next: batch 3 on the cloud (`docs/DASHBOARD_V2_CLOUD_PLAN.md` §13). The user tests everything before any merge into `main`.

## Export identity, proven-export reuse, HTTP request limits, short-export rate cap (2026-10-03) — branch `fix/export-identity-http`, merged into `main`; needs a Control Center restart

- Why: a code review found three problems.
  - Export identity: changing only the blur edge mode or the detected intervals kept the export name, and finalize reused the old export.
  - Existing files: finalize, the standalone review UI and the startup import took any file at the export path for the export (an 11-byte file became "Hoàn tất").
  - HTTP: a negative Content-Length could hold a handler thread, there was no request timeout, and `--host` could expose the session token on the LAN.

  Source cleanup and archive already compared the manifest operations. The security review found that a link at the export path, with a manifest written for it, still passed; that is fixed here.
- What:
  - New `export_identity.py` names the export by the render fields of the edit-plan operations (legacy names are still found). It holds one shared manifest proof, `manifest_problem`, which also refuses links.
  - New `http_guards.py` handles Content-Length, the 20 s request timeout and binding to IPv4 loopback only.
  - Changed: `review_workflow.py`, `control_center.py`, `source_cleanup.py`, `job_import.py`, `final_renderer.py` (hash before the rename, never replace), `export_guards.py`, `control_entry.py`, `cli.py`.
- Reviews:
  - Code review: approve, 4 LOW. Security review: 1 MEDIUM (links), 5 LOW, nothing CRITICAL or HIGH.
  - Re-review of the fixes: code approve; security found the fixes hold, with 3 LOW and 2 INFO left.
  - Fixed after both rounds:
    - the MEDIUM: links and the source itself are never the export;
    - manifest paths are compared as written, never resolved, so no UNC lookup and no link-loop error;
    - hostile manifests and queues no longer raise (finalize, the review UI, cleanup, the startup import);
    - deep JSON bodies answer 400;
    - only a hex source hash reaches the export name;
    - the renderer refuses a link at the export path and never replaces a file;
    - the strict `--host`, test isolation and the standalone 408 test.
  - Accepted and documented: the timeout applies per read, a paused stream keeps its thread, `golden_label_app` and `allow_reuse_address` are unchanged, and manifest reads have no size cap.
- Verification:
  - Full suite: 1192 OK (skipped=25).
  - Real FFmpeg check in a temp root: 11/11.
  - Silencing any one manifest check fails the intended tests.
  - Real data, read-only:
    - all 21 real exports that still have a file are proven under their legacy names;
    - on a backup-API copy of the database, the "Dọn video gốc" assessment (25 jobs) and its export checks (24 COMPLETED jobs) are identical with `main` and this branch: 21 proven, 37/38 "đã bị dời", 4 "không ứng với lần duyệt mới nhất".
- Short exports (after 2a37496, user request 2026-10-03).
  - The bug: with a size limit, any output shorter than about 24 s at the default limit failed in FFmpeg, and so did any output under about 11 minutes at a 100 GB custom limit. The cause was `-maxrate`, or `-bufsize` (twice the rate), going above 2,147,483,647.
  - The fix: `MAX_VIDEO_MAXRATE` caps the rate at 1,073,741,823 bit/s, the highest whose buffer FFmpeg accepts. Every render FFmpeg accepted before keeps its exact command.
  - Verification:
    - the over-range tests were RED before the fix;
    - boundary and audio tests pin the cap: every mutation of it fails a test;
    - code review (read-only agent): approve. It compared 4,524 cases, and every command FFmpeg accepted before is identical; its 1 LOW and 2 INFO were addressed;
    - real FFmpeg 20/20 (`shortclip_check.py`): 6 s, 20 s, and 10 minutes at 100 GB were refused before and are proven after; a 30 s clip is byte-identical with and without the cap;
    - full suite 1197 OK (skipped=25).
- Merged into `main` (fast-forward from 23aa1e4) at the user's request on 2026-10-03 at about 21:40, with no job running; not pushed.
- Next: restart the Control Center so it runs this code (ask the user first; only with no job running).

## Dashboard batch 4 (2026-10-03): platform logos → BLUR, logo memory page, archive/restore, UI fixes — committed 6a8a59c, integrated; the user restarted the Control Center on it at 18:01:50

- Plans: `temp/ui-plan/batch4/plan.md` (decisions), `plan-4a.md`, `plan-4cd.md`; evidence, logs, mock (`mock_server.py`, port 8793) and e2e results in `temp/ui-plan/batch4/`. Full details: CHANGELOG.md, batch 4.
- User decisions (2026-10-03 ~13:05): the iQIYI ident (≈3–4 s at the start and the end of each Nhất Âu Xuân episode) is BLURRED, never cut; the licence card 国家广播电视总局 is kept; Tập 10–30 are not re-exported now; archive = keep the source + review decisions and drop the export, restore = export again.
- 4a, detection: OCR platform-name rule (`platform_names.py`; iQIYI incl. "iOlYI"/"iOIYI", Youku, Tencent Video/WeTV, Mango TV, Sohu, PPTV; Bilibili excluded) → MAIN card “Logo nền tảng …” with suggested BLUR, a pixel-measured region and an interval snapped to the cut (`platform_logos.py`, `platform_cards.py`, build-review only). Forced 6 s ending card. Forced and platform cards are never quarantined (fixes jobs 51/60); a forced card fully matching a remembered studio logo still goes to advisory with KEEP. Platform-logo memory (`memory_class: "platform_logo"`, BLUR) in `state/studio-logo-memory.json`; review choice “Đây là logo nền tảng — làm mờ & nhớ”; “Bộ nhớ logo” page (`logo_memory_admin.py`). Only the text stage cache key changes (OCR reruns once per video).
- 4c, dashboard: tab switch scrolls to the first video; “Hủy” confirm + 409 on a repeat or finished job; folds “Đã hủy (N)” / “Đã ẩn (N)” with `jobs.hidden_at`; Recycle Bin verification retry (≈3.15 s) and the append-only “Kiểm tra lại Thùng rác” (`recycle_checks`); anti-framing headers.
- 4d, archive/restore (`source_archive.py`, `source_archive_files.py`, `source_archive_restore.py`): source renamed into `archive/sources/<job_key>/` (same volume, SHA-256 verified, `archive-manifest.json`), export + manifest to the Recycle Bin; restore renames it back (SHA-checked) and returns the job to review. New table `source_archives`, startup reconcile, shared lock with cleanup, one `source-archive` DB backup on first open.
- Measured: worktree full suite 1130 OK (skipped=25); main tree after integration 1130 OK (skipped=2). E2E on a COPY of the Tập 17 export (GPU, 252.7 s): iQIYI [8.00, 13.00] and [2699.68, 2703.68] as BLUR cards with regions 7.8 % / 8.6 % of the frame; 12/12 checks in 3 scenarios (with memory, without memory, with the seeded end record); production files unchanged. Mocks at 1280 px and 375 px. Reviews: python (1 high + 6 medium fixed), code (5 fixed), security (0 critical/high; 1 medium + 5 low fixed).
- Real memory after the user-approved plan (Control Center stopped, backups `state/backups/studio-logo-memory-20261003-170730.json` and `…-170743.json`): 3 iQIYI records converted to `platform_logo` BLUR, 1 iQIYI end-ident record seeded from the Tập 17 export (2699.68–2703.68 s), 3 licence-card records unchanged (`studio_logo` KEEP); 7 records.
- Done after integration: the user restarted the Control Center at 18:01:50 (migration backup `state/backups/control-center-before-source-archive-20261003-180150.sqlite3`; 30 jobs intact) and found the dashboard fine; committed d90c8f3 (batch 3) and 6a8a59c (batch 4). Optional later: re-export Tập 10–30 without iQIYI via “Khôi phục bản xuất” after restoring their sources from the Recycle Bin (decision 3: not now); Qwen 3-frame sampling (deferred, needs Golden gates).

## Dashboard batch 3 (2026-10-03): Dọn video gốc vào Thùng rác — committed d90c8f3, integrated; the user restarted the Control Center at 12:19:55 and cleaned jobs 40–60 at 12:21

- Plan: `temp/ui-plan/batch3/plan.md`; frozen interface contract `temp/ui-plan/batch3/contract.md`; evidence and logs in `temp/ui-plan/batch3/`. Status in Vietnamese: docs/UI_QUEUE_PLAN.md, "Trạng thái thi công".
- Feature: "Dọn video gốc" in the "Hoàn tất" tab moves the sources of up to 50 confirmed videos to the Windows Recycle Bin. Each video is handled on its own, with a `source_cleanups` row (PENDING before the shell call, then RECYCLED or FAILED) and an event carrying path, size and SHA-256. It is never a permanent delete. Reports, review decisions, brand/studio memory and outputs are never deleted or rewritten; in `state/control-center.sqlite3` it only adds its own `source_cleanups` rows and SOURCE_* events and resets the watcher row of the moved file. Agents never run it (AGENTS.md).
- Eligibility:
  - Exported: the job is COMPLETED, and the output is the export of the active review revision. Its manifest must be COMPLETED, name the same source SHA-256 and mark the source unmodified; full-decode validation must have passed; output bytes must match. The operations recorded in the manifest must be the ones the current decisions render (cut/blur intervals, regions, blur edge mode), so re-recording a decision after the export (an undo, a misclick) keeps it cleanable; a manifest without operations falls back to "newer than every review decision". The edit plan, if present, must point at the active queue. The output SHA-256 is checked again at cleanup time.
  - Skipped: the job is SKIPPED, and the skip record still matches the active queue, revision and decisions.
  - Both: the source is a regular file under `input/` with the scanned size (no link, at most 259 characters). The job is not running, not queued, and has no unfinished export request.
  - Jobs 37 and 38 are not offered: their recorded export is the export of the current review, but the file is no longer in `output/` (read-only check, fix pass). Their cards say "Chưa dọn được: Không thấy bản xuất trong thư mục output (đã bị dời hoặc đổi tên?)". They become cleanable once that file is back in `output/`, or after a new export. Jobs 1-6, including job 4, have no source in `input/` and are never offered.
- Capacity block (user decision 3): the request is refused when used + selected > limit − 64 MiB on the source drive. The limit is MaxCapacity in MiB × 1 048 576; on E: that is 49 741 MiB = 52 157 218 816 B. The drive must also be fixed, have "delete immediately" off, and have no Windows policy that disables the bin. The dialog shows the bin before and after the move.
- Restore rule: the watcher re-accepts a cleaned source only when the same file (SHA-256) is back at the same path with the same size, after the file is stable. The job then unlocks, and the card shows "Đã khôi phục video gốc (SHA-256 khớp) lúc …". The same bytes under another name only add a SOURCE_RESTORE_WRONG_PATH event. A different file at the old path becomes a new NEEDS_METADATA job and adds SOURCE_RESTORE_REJECTED on the old job. Restart import skips cleaned paths and cleaned SHA-256 values.
- Also in this batch:
  - (a) The standalone `review-ui` refuses to export any video that has a Control Center job, and fails closed when the Control Center database cannot be read. It also refuses decision edits for that video while an export is in flight or still requested (a paused, failed or interrupted export that Tiếp tục/Thử lại would run; Hủy retires it), after its source was cleaned, or while it is skipped.
  - (b) Cancel, a changed decision, a skip, and a finalize that finds the output already present retire a leftover render stage (EXPORT_REQUEST_RETIRED). "Tiếp tục" no longer re-renders an old plan.
  - Queue-place notices after "Bắt đầu" and "Chạy lại kiểm tra", and the export labels "Xuất video tạm dừng" / "Đã hủy xuất video".
  - The watcher survives a file that vanishes mid-scan.
  - G2 cross-review fixes (evidence `temp/ui-plan/batch3/fix/`): the exported-video check compares the manifest's operations with what the current decisions render instead of decision timestamps (a re-recorded decision plus the finalize shortcut no longer blocks cleanup forever; read-only probe: jobs 40-59 still match); `/api/shutdown` keeps the process alive until a running cleanup settled and the store closed; the dialog reopens if the browser force-closes it during the POST; a cleaned skipped card no longer says the source is kept.
- Lead follow-ups after the recheck: a rerun with the same items and decisions keeps its export cleanable (operations identity skips the edit-plan check; manifests without operations still get it), and the view-only review page no longer ends with "Bấm “Xuất video” ở trên để xuất".
- Measured (evidence `temp/ui-plan/batch3/`):
  - Baseline at c0a20cc: 795 tests. In the worktree 28 tests error only because it has no `input/*.mp4`.
  - G1: worktree full suite 963 OK; cache-key check CLEAN for all 10 cacheable stages; migration on a copy of the live DB: 20/20 checks, row counts unchanged, one `source-cleanup` backup, second open a no-op, schema version 1.
  - G2: 7 findings (0 high, 2 medium, 5 low), all fixed with tests (mutation 6/6); 970 OK after the fix pass.
  - G3: static mocks at desktop and 375x812: dialog for 1 and 18 videos, cancel sends nothing, confirm sends one POST with the preview id, cleaned card and view-only review page as designed.
  - G4: the one real Recycle Bin test (1 KB file it created, 2026-10-03 11:57) passed with a verified `$I` record; E: bin 7 → 8 items, +1 024 B.
  - G5: integrated into the main tree; main-tree full suite **972 tests OK** (skipped=1, the real-bin test, now marked as run).
- Done after integration: the user restarted the Control Center at 12:19:55 (backup `state/backups/control-center-before-source-cleanup-20261003-121955.sqlite3`) and cleaned the 21 sources of jobs 40–60 at 12:21; every file has its `$I`/`$R` pair in the bin. Jobs 43 and 47 show SOURCE_RECYCLE_UNVERIFIED only because the check ran 10 ms before Windows wrote `$I` (batch 4 adds the retry and “Kiểm tra lại Thùng rác”).

## Dashboard batch 2 (2026-10-03): skip, stage tabs, card export — committed 1b6ad90 (+c0a20cc docs), integrated

- The Control Center has run batch 2 since 2026-10-03 06:59. Next: batch 3 (above).

## Dashboard batch 1 (2026-10-03): sticky tabs, click-order queue, restart safety, export panel — committed 27dc000, integrated

- Plan: docs/UI_QUEUE_PLAN.md (batch 2: skip, stage tabs, card export button; batch 3: input cleanup to the Recycle Bin). The Control Center was restarted with batch 1 on 2026-10-03 00:18. That first start migrated the jobs table, with a backup (`state/backups/control-center-before-queue-order-20261003-001806.sqlite3`).

## Run-affecting fixes (2026-10-02): detector picker, structure audit, brand-memory cache churn — uncommitted, integrated

- Dashboard keeps each job's chosen scope and confirms changed scopes; structure audit reports budget shortfalls as WARN; review decisions no longer invalidate the logo stage cache. Needs a Control Center restart (queue idle) to serve the new dashboard and audit. Next on the list: logo false alarm on a torch scene (Tập 14, rare), the visual-logo candidate budget gap, gore C1.

## Studio-logo memory v2 (2026-10-02): masked, multi-frame, uncommitted, integrated

- Remembered idents ignore the user's blurred watermark regions and keep every informative frame of the window; measured 0 false matches on 7,803 other-film cards; Nhất Âu Xuân clean episodes now match the ident remembered on Tập 10. The 3 existing records were upgraded on 2026-10-02 17:29 with the Control Center stopped (111/68/111 frames; backup in state/backups); the Control Center now runs the new code.

## Opening card UI (2026-10-02): honest whole-scene logo cards, uncommitted, integrated

- "Kiểm tra đoạn mở đầu" cards now show the AI's real answer, amber reference boxes, a playable span and studio-memory match diagnostics; display-only, queues identical otherwise (`reports/benchmarks/opening-card-ui-20261002`). User decision: these cards stay in the main list. Next: studio-logo memory that ignores watermark regions the user blurred and remembers several frames of animated idents (approved; measure false moves first). Restart the Control Center to serve the new page.

## Watermark fix (2026-10-02): whole-video cards for site watermarks, uncommitted

- Nhất Âu Xuân Tập 10/15/18/19 (site Motchill): the corner watermark and the faint "cập nhật nhanh nhất tại MOTCHILLV" line were missed (Tập 10 export unblurred) or merged into one 839x483 box (Tập 18 export blurred over most of the frame). Fixed in build-review only (`promote_fixed_text_overlays`, text merge distance guard); Golden queues identical; 655/655 tests. Integrated into the main tree on the user's instruction while jobs ran (build-review runs in its own process; scan caches unaffected).
- Reruns queued 2026-10-02 13:56 for jobs 40, 45, 48, 49 (Tập 10, 15, 18, 19). Tập 11, 16, 17, 20 have no watermark (queues unchanged by the fix). Tập 12-14 build their queues with the fix.
- The gore C1 patch (`temp/gore-c1.patch`) waits: it edits `cli.py`, which is in every scan stage's cache key, so integrating it would force full rescans. Update 2026-10-03, to be handled later:
  - The `fix/export-identity-http` merge already changed `cli.py`, so the scan caches are invalidated anyway.
  - The patch still applies except for review_workflow.py, which needs a manual re-merge.
  - See SESSION_HANDOFF, item 10.

## Task B (2026-10-02): exact-output speedups, uncommitted

- Kept: violence confirmation prefetch (T1), background source hash in adult/live/animation scans (T5a), R3 windows decoded two at a time (T5b), logo VLM processor prefetch (T5d), and an `IteratorPrefetch` shutdown fix. Dropped T4 (no gain); skipped Florence+DINO in one process.
- Proven identical on the full films: every touched stage re-run on Troy and Conan 20 with the HEAD trials' commands; scan reports, JPEGs, review queues (HEAD and working-tree build code) and dry edit plans identical (`reports/benchmarks/exact-speedups/stages-20261002-075009`).
- Estimated saving per all-groups fast scan: Troy ~5-5.5 min (confirmation 651 -> 424 s), Conan 20 ~20 s. Raw stage times against the HEAD trials are not attributable (that run was loaded); see `timing-attribution.json` there.

## Q3 (2026-10-01): watermark region, nudity shot completion, Golden Set v1.1

- Conan 21 watermark blur no longer leaves "Phim" visible; Golden region 13/13 with nothing else changed.
- Nudity shot completion R3 implemented but off by default (needs validation on Golden v1.1 T6).
- Golden Set v1.1 (6 segments, 18+/violence/blood positives and fight hard negatives) ready for labelling.
- Next: review cards that show a frame strip and an inline player (plan approved, backend in progress).

## Quality track Q2: Golden Set v1 audit and baseline — 2026-09-30

- Labels audited from video evidence and corrected with the user's approval: revision 457, 19 labels (12 watermark, 1 Troy watermark re-boxed, 1 adult, 5 gore).
- Baseline (`reports/benchmarks/golden-baseline-v1-20260930-225627`): nothing missed in the 13 segments; the weakness is false alarms (precision 65% advertising, 11% adult, 24% gore, 0% violence) and one Conan 21 watermark blur box that leaves "Phim" visible.
- Troy's active revision has no adult/gore/violence scan (advertising only) and is READY_TO_EXPORT; the adult detector misses clothed kissing and the first 6 s of the bed scene near minute 16 (outside v1). Next: v1.1 segments for 18+/violence, then precision work (docs/QUALITY_PLAN.md §17).

## Phase H-live: live-action safety stages — 2026-09-30

- L1 (exact prefetch) in `scan` and `scan-live-safety`: full Troy byte-identical; adult 596 -> 361 s, gore+violence 1,813 -> 1,136 s.
- L2 `--violence-precision fp16` measured on full Troy: stage 1,136 -> 581 s, every review item identical after VLM confirmation. Enabled in "Tăng tốc xử lý" after user approval; measured full Troy pipeline with all groups: ~67 min -> 41m47s, review queue identical (`troy-allgroups-full-fast-20260930-162534`). Evidence: `reports/benchmarks/live-safety-h/`. 393/393 tests.

## Phase H: animation safety stage — 2026-09-30

- H1 (exact batch prefetch) is in the scanner: Conan 21 stage 800.7 -> 670.9 s, byte-identical reports/images on both Conan films.
- H2 fp16 measured on full Conan 20/21 (review items and intervals identical, stage ~208 s instead of ~670 s) and, after user approval, part of "Tăng tốc xử lý" (H3). Measured full pipeline, Conan 20 all groups: 27m22s -> 15m50s (-42%), review items identical (`conan20-allgroups-full-fast-20260930-073627`).
- Multi-agent review of the Golden Set tooling and Phase H: 16 confirmed issues fixed before any labels exist (matching rules, fail-closed gates, label store safety). 386/386 tests. Evidence: `reports/benchmarks/anime-safety-h/`.

## Quality track Q0–Q1: Golden Set v1 tooling — 2026-09-29

- User approved `docs/QUALITY_PLAN.md` (start with the three existing sources; new videos later). Detailed Q0–Q1 plan and pre-measurement adjustments: §12.
- Manifest `annotations/golden/v1/segments.json`: 13 segments, 64 min (Troy T1–T4, Conan 20 C20A–E, Conan 21 C21A–D; 7 dev / 6 holdout), SHA-256 verified.
- 404 labeling suggestions (272 from earlier review queues, 132 from 27 min of dense per-segment scans).
- Tooling: `scripts/golden_prefill.py` (manifest/collect/dense), labeling page `scripts/golden-label.ps1` → http://127.0.0.1:8766, `scripts/evaluate_golden.py` (score/run/compare with gates), modules `golden_set`, `golden_scoring`, `golden_label_app`. 361/361 tests.
- Found while planning: the user's earlier decisions (Troy revision 1: 294 decisions incl. 11 adult BLURs; Conan 21: nearly all gore/violence KEEP) become labeling suggestions; `annotations/` is gitignored, so labels rely on atomic writes + history + backups.
- Next: the user labels the 13 segments (≈2.5–3.5 h on the PC), then Q2 baseline scorecard (`evaluate_golden.py run`, ≈70 min machine).

## FP16 text detection in "Tăng tốc xử lý" and GroundingDINO fix — 2026-09-29

- fast_scan (default on) now also runs CRAFT detection under float16 autocast (`--detect-precision fp16`). Not bit-identical: validated at review level on Troy (6/294 items identical), Conan Movie 20 advertising (2/393) and Conan Movie 20 with all detector groups (69/393; adult/gore/violence and logo reports byte-identical). OCR stage −59…−128 s; Troy pipeline 16m35s → 14m27s. Plan/results: §15–16 of `docs/CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md`.
- Fixed a latent crash: GroundingDINO frames without detections produced one empty label (transformers 5.17 `batch_decode([])`), stopping `augment-grounding-regions`.
- Since the start of the optimization work, Troy advertising: 25m32s → ~14m27s (−43%).

## Phase F: hidden hashing and parallel localization frames — 2026-09-29

- F2 background source hashing (OCR; logo with verified recorded checksum) and F3 parallel localization frame extraction kept; F1 (low-priority warm-up) measured and rejected. Plan and results: §14 of `docs/CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md`.
- Full pipeline 17m45s -> **16m35s** with identical outputs (`reports/benchmarks/troy-full-fast-20260929-164440/comparison.json` vs `troy-full-fast-20260929-150440`). Since the start of this optimization work: 25m32s standard -> 16m35s (-35%).
- Existing precision-style regression set: `annotations/review-regression-v1.json` (206 human decisions, 6 sources). Recall of missed content is still unmeasured; a labelled segment set is the proposed quality track. The Conan Movie 20 watermark-vs-head case was already fixed on 2026-09-26 (keep it as a regression check).

## Phase E: routing warm-up during OCR — 2026-09-29

- Plan-first phase (docs/CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md §13): E1 NVDEC pixel identity PASS (11,322 frames); E2 NVDEC-for-OCR rejected (+4.6%); E3 overlap gate "OCR ≤ 10% slower" failed (OCR +15–19%) → user accepted the trade-off with a pre-registered total-pipeline gate (≥ 3 min faster, identical outputs); E3b prototype PASS (22m33s → 17m40s).
- E4/E6 integrated in `fast_scan` (default on): OCR stage runs `scan-visual-logo --routing-only --decode nvdec --routing-workers 3` as a child; full pipeline 22m33s → **17m45s** with identical outputs (`reports/benchmarks/troy-full-fast-20260929-150440/comparison.json`; baseline `troy-full-fast-20260929-141826`).
- Next candidates (not started): background source hashing in the OCR stage (~20–40 s), localization profiling (~220 s), a labelled ground-truth set to measure detection recall/precision, and the recorded Conan Movie 20 watermark-vs-head misdetection.

## Phase C/D: parallel logo routing and "Tăng tốc xử lý" — 2026-09-29

- `RoutingPool` (src/biliflow/visual_logo_scanner.py) computes routing features in worker processes, consumed in frame order; `--routing-workers` default 1. Full Troy routing 430.7 -> 355.1s (-17.6%) with identical windows/selection/JPEGs (`reports/benchmarks/logo-routing-parallel-pipeline-20260929-081117/`). CPU-only evidence: `logo-routing-parallel-cpu-20260929-080248/`.
- Per-job `fast_scan` ("Tăng tốc xử lý" checkbox on start/rerun; ON by default via `DEFAULT_FAST_SCAN`) = OCR batch 8 + window 4 + 3 routing workers. Full advertising A/B in one session: 25m32s -> 22m57s (-10.2%), outputs identical (`reports/benchmarks/troy-full-standard-20260929-093619/`, `troy-full-fast-20260929-100152/` with `comparison.json`).
- Stage overlap (routing during OCR) measured in `stage-overlap-*` directories: ~2.4-2.9 min extra saving at the cost of 18-35% slower OCR and a pipeline change; not implemented. The remaining lever is the duplicated full-film decode (OCR and logo each decode 1080p separately).
- User decided (2026-09-29): "Tăng tốc xử lý" is the default for jobs without a stored choice; each job can still untick it. Next: plan-first shared decode (see `docs/CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md`).

## Cross-frame OCR recognition experiment — 2026-09-28

- Phase A of `docs/CLAUDE_SCAN_OPTIMIZATION_HANDOFF.md` is implemented as opt-in only: `CrossFrameReader` in `src/biliflow/ocr_batch_experiment.py`, `scan_text(recognition_frame_window=1..8)` and CLI `--recognition-frame-window`. Default 1 keeps the existing serial/batch-8 paths; job pipeline and Dashboard do not pass the new option.
- Evidence (baseline HEAD `ac3646b` + working-tree change; RTX 2060, torch 2.14.0+cu126, EasyOCR 1.7.2): `reports/benchmarks/ocr-cross-frame-occupancy-20260928-224612/`, `ocr-cross-frame-abba-20260928-224943/`, `ocr-cross-frame-downstream-20260928-225858/` (includes `unittest.log` 291/291, `SUMMARY.md`), `ocr-cross-frame-cancel-20260928-230913/`. Harness `scripts/benchmark_ocr_cross_frame.py`, launcher `scripts/benchmark-ocr-cross-frame.ps1` (GPU mutex; `run.ps1` untouched).
- Seven excerpts (Troy 0/48/418/940, Conan 21 4200/6630, Conan 20 240): same text/boxes/acceptance/tracks/previews/review mappings as serial; scores differ by at most 3.6e-6 raw / 2e-6 in reports. OCR model time -18.5% and scan-without-hash -15.8% (median, A/B/B/A). Gains concentrate in text-heavy sections; single-watermark sections gain about 4% in the controlled in-memory run and are within noise in full scans.
- Full Troy OCR stage A/B/B/A (`reports/benchmarks/ocr-cross-frame-downstream-20260928-231957/`, user-authorized): median 752.6 -> 631.2s (-16.1%, about 2 minutes); conservative -13.2%. Identical tracks/previews/review items across all runs and versus the production serial report. Whole advertising pipeline (~30 min) would drop by roughly 2 minutes; logo routing/localization are untouched.
- Phase B rejected (`ocr-cross-frame-abba-20260929-000902/`, `-001335/`): batched detection gives no speedup and 2.7x CUDA memory; cuDNN autotuning gives no change. CRAFT detection is GPU-bound at 960x544. Code kept only in that evidence directory.
- Not yet done: Dashboard/job option with per-job persistence and stage-cache identity, full-pipeline timing with the option, adult/gore/violence untouched (not rerun). User asked not to commit until all phases are done; everything above is uncommitted on this branch.

## Full advertising measurement after RGB optimization — 2026-09-28

- User-authorized Troy trial `troy-rgb-cold-full-20260928-194937` completed with serial OCR and fresh isolated routing/GroundingDINO caches. Actual pipeline stages use separate SQLite/review and benchmark import protection; production source/cache/job/review/brand memory are preserved.
- Full advertising elapsed time **1786.436s (29m46s)**: preflight 20.709s, OCR 821.828s, visual-logo 686.890s, localization 248.265s, review/local audit 8.710s. No Visual AI, adult/gore/violence or export.
- CPU logo routing is 517.638s (features 493.007 + brand matching 24.631), versus 612.124s in the recent pre-optimization cold run: 15.44% observed reduction. Logo stage falls from that run's 812.780 to 686.890s. These are separate, non-interleaved runs; bounded reversed-order evidence remains the more controlled attribution test.
- End-to-end speedup is NOT demonstrated against the older 27m29s complete production run. That run computed 25 DINO inputs and reused 87; this run computes 112 with zero hits. OCR also takes longer. Do not use a synthetic sum of stages from different runs to claim a measured full-pipeline gain.
- Baseline equivalence passes: 3,921 OCR frames, 6,121 logo frames, 199/199 regional leads, 116 logo intervals, 858 identical JPEGs. Localized output differs only in five runtime/cache telemetry fields. Review proposals remain 6 primary / 294 advisory, all 123 source candidates represented, Structure Audit PASS. Original job #39/revision and hashes remain unchanged.
- Evidence in `reports/benchmarks/troy-rgb-cold-full-20260928-194937/`: `trial.json`, `analysis.json`, `comparison.json`, `SUMMARY.md`. No benchmark remains active. Runtime source unchanged in this measurement; latest full suite remains 278/278. Next bounded target: OCR inference and remaining feature extraction, with controlled cache conditions for any future end-to-end A/B. No merge/push.

## CPU logo routing: exact RGB-distance optimization — 2026-09-28

- Commit `2c72280` replaces generic RGB-axis norm reduction with the same three squared channel terms, square root and normalization. uint8 pixels and half-integer channel medians make the intermediate squared sum exactly representable; array tests verify bit-exact distances and the unchanged 0.12 mask. No threshold, region, model, OpenCV thread count, sampling or export changes.
- Six original-source excerpts / 270 frames from Troy and Conan 20/21, with reversed baseline/optimized order, preserve every feature/score/geometry and brand match against immutable `35fa7de`. Median CPU routing 13.482 -> 10.718s (-20.50%); individual excerpt reductions 15.28–22.29%. This excludes source decode/hash, VLM/localization and review, so it is not a full-film acceleration percentage.
- Actual cold scanner routing checks on Troy 0–30s and Conan 21 4200–4260s retain all 165 samples, 18 windows, selected candidates and full/crop JPEG hashes supplied to VLM. The harness stops before model load and intercepts cache IO without deleting/overwriting production caches. Original Troy queue and brand-memory hashes remain unchanged. No all-detector scan, audit, export, merge or push.
- Split future logo telemetry into `logo_feature_extraction` and `logo_brand_memory`. Bounded serial OCR instrumentation (40 frames) measured detection 4.577s and recognition 5.726s in 10.472s readtext time; predictions match the uninstrumented calls. OCR code and batch-1 default are unchanged.
- Rejected previous-edge reuse (1.88% CPU gain) and left OpenCV threading unchanged. Experiment source/evidence retained under `reports/benchmarks`; only the measured RGB optimization is in production code. Full suite 278/278 passes.
- Evidence: `reports/benchmarks/logo-rgb-distance-20260928-193550/`, `logo-routing-equivalence-20260928-193742/`, `ocr-phases-20260928-192818/`. Next: authorized full cold advertising timing or the next new-video run, before quoting actual minutes saved. No long rerun started.

## Routing-cache regression found by full advertising comparison — 2026-09-28

- First full trial `troy-ocr8-full-20260928-182704` preserves OCR tracks, sample coverage and all 250 preview hashes. OCR takes 765.102s versus historical serial 770.734s; no material full-film OCR speedup is demonstrated.
- Cross-run comparison exposed a pre-existing logo cache v1 bug: serialization kept only strongest/latest images, but regional routing needs evidence from all frames. Warm routing lost 16 regional leads and changed which windows entered semantic confirmation. Structure Audit still passed because it checks supplied reports, not a previous detector run's coverage.
- Commit `6231f58` preserves all routing frames in order, including features and JPEG bytes, and separates v2 cache identities. Existing thresholds, sampling, models and VLM image selection stay unchanged; legacy v1 caches are rejected, not deleted. Existing 4 GiB / 30-day cache cleanup policy is retained.
- Reproduced the middle-frame regression before fixing it; focused tests and all 276 unittests pass. Corrected isolated run `troy-ocr8-full-cache2-20260928-184844` reuses verified OCR and reruns logo/localization/review. Raw logo report, 199/199 regional leads, 116 intervals and 608 pipeline JPEG hashes match the original baseline. Localized output differs only in six documented telemetry fields. Review proposals match: 6 primary / 294 advisory; 123 source candidates covered; Structure Audit PASS.
- Actual warm v2 verification passes: identical report including scores and 496/496 scanner JPEGs. Warm scan-visual-logo takes 181.821s versus current cold 812.780s, for same-video routing reuse only. The first image comparison included 112 downstream grounding extraction images absent from the scanner-only warm run; original comparison is retained and the corrected check explicitly limits itself to scanner outputs. No additional inference rerun was needed.
- Original job #39/revision 3, queue hash, source stat and brand-memory hash remain unchanged. No trial remains active; no export, merge or push occurred. Default stays serial. Next profile CPU logo routing (612.124s here) and split OCR detection/recognition timing before choosing further speed changes. Do not treat the OCR-copy corrective total as an end-to-end fresh scan.

## Per-job OCR mode and full advertising trial — 2026-09-28

- Commit `bf5bc35` exposes standard/experimental OCR on Dashboard start and rerun. Selection persists per job and across page refresh/resume/restart; unsupported values are rejected before settings/job changes. Serial mode retains its exact previous command line.
- Batch 8 changes only the advertising text command. Safety, visual-logo, localization and export commands remain unchanged. Cache keys distinguish serial/batched OCR. All 274 tests pass, including Node-executed UI behavior and scheduler persistence.
- Automatic report import now skips `reports/benchmarks` and `.biliflow-benchmark` marked report trees; the new Troy trial is marked. This also protects native-source pilot queues from becoming the active production revision on Dashboard restart.
- Full advertising Troy trial completed at `reports/jobs/troy-ocr8-full-20260928-182704`. State and logs are isolated at `reports/benchmarks/troy-ocr8-full-20260928-182704/`; harness `reports/benchmarks/run_troy_ocr8_full.py` uses the production pipeline definitions and local structure audit. Its logo cache regression and corrected follow-up are documented above. Original live job #39/queue is retained.
- Stage-report caching is bypassed for this trial; scanner-internal routing cache follows its existing policy, so any cache contribution must be reported separately. No cloud/Visual AI, safety detectors or export are authorized by this advertising-only run.
- Comparison helper: `reports/benchmarks/compare_troy_ocr8_full.py`; compare against #39 run `20260927-234709`. Corrected full-advertising equivalence is verified above, not safety-detector recall or a full-film acceleration claim. Keep serial default, remain on the improvement branch, no merge/push.

## Scan performance phase 3: native Troy OCR pilot — 2026-09-28

- User authorized a short original-source comparison. Scanned Troy source 48–138s directly, no proxy/transcode, with unchanged 3-second sampling and thresholds. Scope: OCR/text semantics only, not complete advertising or safety detectors.
- Four warm runs in serial/8/8/serial order preserve 30 frames, 42 tracks, classifications, original 1920x1080 review geometry, 4 primary and 10 advisory items, candidate mapping and all JPEG hashes. Max confidence difference 0.000001. All source hashes agree; source size/mtime remain unchanged.
- Median total OCR scan: 33.257 -> 30.760s (7.51% reduction). OCR model work: 11.089 -> 8.700s (21.55%). Whole-source hashing costs 20.674/20.546s respectively. Excluding hash, scan time improves 18.83%. Do not extend these ratios to full-film elapsed time.
- Models load once in 8.701s; an untimed 3-second native warmup precedes measurements. Isolated queue creation costs about 0.024s. No production job/decision/default changed; no Visual AI Audit or export ran.
- Evidence: `reports/benchmarks/ocr-native-20260928-181450/` (`comparison.json`, `analysis.json`, `SUMMARY.md`, per-run reports and 268/268 unittest log). Harness retained at `reports/benchmarks/ocr-native-pilot.py`. No runtime source change.
- Bounded native-source gate passes. Default remains serial; next work is controlled full-advertising opt-in validation when requested, or duplicate-work/shared-decode optimization. No merge/push.

## Scan performance phase 3: moving text, effective thresholds and GPU stop — 2026-09-28

- Added a standalone synthetic OCR stress benchmark and a dedicated PowerShell runner using the existing GPU mutex. Production `run.ps1` is unchanged, so adding this benchmark does not invalidate production stage-cache fingerprints.
- 48 generated/labeled 960x540 frames cover moving/clipped/disappearing banners, unrelated text, small/blurred/noisy text and low-confidence top banners. All fixture RGB pixels survive FFV1 decode exactly. All 183 raw OCR regions preserve recognized text and acceptance between serial and batch 8; max confidence delta 0.000005918.
- Effective threshold checks use the actual scanner acceptance function and geometry: two eligible cases within 0.05 of 0.35, three within 0.03 of 0.10. Closest margins 0.006515 and 0.008519 respectively. These are not numerical knife-edge cases or proof of unseen-film recall.
- Three isolated complete OCR/review comparisons retain all report fields except confidence/runtime, JPEG hashes, candidate mappings and queue projections, with 9/12/4 tracks. All supplied OCR candidates remain represented. Nothing is auto-approved/exported.
- Three fresh workers signal after actual CUDA recognition, then stop through the scheduler's existing process-tree terminator. Worker/FFmpeg children exit in 0.154–0.164s; no completed partial report survives. Subsequent GPU inference succeeds. This checks the backend termination method, not Dashboard button/UI state or resource exhaustion.
- Evidence: `reports/benchmarks/ocr-stress-20260928-175841/`, about 12 MB before logs; prior 32-frame pilot retained at `ocr-stress-20260928-175620/`. Full suite 268/268 passes. No source videos, production code, queues, decisions or model settings changed.
- Default remains batch 1. Next: bounded original-source batching pilot with source-resolution geometry/timing, then duplicate work/shared decode investigation. No full-film rerun, merge or push.

## Scan performance phase 3: contiguous OCR validation — 2026-09-28

- Added `benchmark-ocr-contiguous` under the existing GPU mutex. Plans allow at most six excerpts of at most 120 seconds each, restricted to input sources and the existing 3-second sampling grid. Original files and production reports/decisions are untouched.
- Tested six 90-second windows: Conan 20 at 240/600s, Conan 21 at 4140/6630s, Troy at 0/11640s. FFmpeg RGB frame hashes verify all 180 sampled frames survive the temporary FFV1 fixture exactly. Fixtures are already 960px sampled video; queue geometry is checked in that coordinate space, not original-resolution rendering.
- Warm reversed-order serial/8/8/serial runs all match baseline report content except confidence/runtime fields, JPEG hashes, queue regions/actions/intervals and source-candidate mapping. 273 reference tracks across six windows; all supplied OCR candidates represented. Tiny score differences reach 0.000006. Equivalence does not establish ground-truth recall or correctness of existing candidates.
- Sum of per-excerpt median OCR-to-report wall times: 49.047 -> 41.415s, 15.56% reduction. Individual reductions range 0.12–22.45%. This excludes original-source hashing/decode, cold model loading, other detectors and audit; no full-film acceleration claim.
- RTX 2060: measured PyTorch peak allocated memory 664.82 MiB for both paths; peak reserved 790 MiB. Sampled process RAM peak about 1.924 GiB. These are measurements, not total VRAM or hard resource bounds.
- Evidence: `reports/benchmarks/ocr-contiguous-20260928-172310/`. `analysis.json` rechecks all 24 runs using the extended queue comparator. Full suite 265/265 passes; added comparison-regression and batch-interrupt propagation tests.
- Default stays batch 1, prefetch off. Remaining activation gates: moving banners, labeled near-threshold text and real GPU cancellation/resource stress. No live job rerun, model/threshold/export change, merge or push.

## Scan performance phase 3: opt-in OCR crop batching — 2026-09-28

- Added `scan-text --recognition-batch-size 2|4|8`, restricted to CUDA vi/en. Default 1 preserves current Dashboard behavior. Raw frame prefetch remains off.
- Unlike ordinary EasyOCR batching, the experimental implementation preserves each crop's serial padded width, only combines matching widths, retains order and bounds batches by count and input area. Very wide crops stay serial. The input-area cap is not a total VRAM cap.
- Benchmarked 39 source frames / 109 boxes across Troy and Conan 20/21. Same-width batching preserves all recognized text and accepted boxes in this corpus; maximum confidence change is about 1.101e-6. Ordinary batch=8 changed text and is rejected.
- Summed frame-median recognition time improves 19.0%; reversed-order OCR-to-report wall time on two short lossless fixture sequences improves 6.33% and 3.70%. These numbers are NOT full-video pipeline speedups.
- All report fields except confidence/runtime metadata and all preview JPEG hashes agree. The sequences produce respectively 14 and 5 tracks with unchanged classifications/regions/times. This is baseline equivalence evidence, not a ground-truth accuracy/recall benchmark.
- Evidence: `reports/benchmarks/ocr-batch-20260928-165521/`, `review-170011/` beneath it, and `reports/benchmarks/ocr-batch-20260928-170046/`, `review-170317/` beneath it. Final full test log in the second root: 260/260 pass.
- Next: validate the opt-in path on longer contiguous excerpts with near-threshold/low-contrast small text, resource/cancellation checks and complete review coverage before proposing any default activation. No full source job rerun or saved decision was changed.

## Scan performance phase 3 experiment: OCR prefetch — 2026-09-28

- Added bounded CPU raw-frame prefetch for OCR as an opt-in Python parameter. Default is zero; production profiles and CLI scan defaults remain serial. No detector thresholds, sampling, models or export logic changed.
- Queue caps depth at four with a 32 MiB raw-buffer budget, retains exact bytes/order, applies backpressure instead of dropping frames, forwards read errors and stops the reader on early exit. This is not a cap on total process/model RAM.
- Real CUDA OCR A/B: Troy source seconds 0–30 and 418–448, five measured runs per excerpt with a warm shared OCR/semantic model and reversed baseline/prefetch order. Every detection payload and JPEG hash agrees; ten frames per run, 13 preview images in the opening excerpt and one in the later excerpt.
- Median wall time excluding full-source hashing: baseline/prefetch 2.886/2.892s at start 0; 2.333/2.325s at start 418. Benefit is below 1% and inconsistent, so prefetch is NOT enabled by default.
- Evidence: `reports/benchmarks/frame-prefetch-20260928-161812/comparison.json` and `analysis.json`. Full suite passes 252/252; log `reports/benchmarks/prefetch-unittest.log`.
- No production rerun, queue change, merge or push. Next: benchmark OCR inference batching/preprocessing, with exact input coverage and output comparisons; do not assume read-ahead will accelerate an entire film.

## Scan performance phase 2: exact stage reuse — 2026-09-28

- Remain on `improve/scan-performance-metrics`; no merge/push or production rerun.
- Stage cache now follows each scanner's transitive Python imports rather than invalidating every scan when the Dashboard or review UI changes. Uncertain dependencies fall back to full-source fingerprinting. Model/config scopes remain conservative.
- Added missing identity inputs: upstream report bytes, semantic training seed, localizer script and logo brand memory. Fingerprints refresh for each lookup, and store checks the identity captured before execution.
- Cache schema v2 deliberately misses v1 entries once; no old report, queue, brand memory or decision is deleted. Future exact reruns can reuse v2 snapshots.
- Real artifact test: copied the existing Troy job #39 OCR report into an isolated temporary project, changed only its UI file, then compared old/new cache implementations. Old cache misses; new cache restores all 252 files byte-for-byte in 1.826 seconds. No detector or video ran.
- Evidence: `reports/benchmarks/stage-cache-20260928-160614/comparison.json`; full suite 247/247 passes, log beside that file.
- Benefit applies to exact reruns after unrelated code edits. New-video throughput has not improved in this phase. Next is bounded prefetch, then benchmark-gated batching/shared decode.

## Scan performance phase 1 — 2026-09-28

- User requested a separate improvement branch: `improve/scan-performance-metrics`, created from `main` at `7f5a9fb`.
- Added per-phase host-wall telemetry and a read-only job timing command. No sampling, threshold, model, review or export behavior changes.
- Existing Troy advertising-only job #39 totals 1648.627 seconds across completed stages: text 770.734s, visual-logo 642.611s, localization 207.277s. Older reports have no internal phase timings.
- Short CUDA equivalence smoke compared source seconds 418–430 against baseline scanner modules: text, adult and shared gore/violence payloads match; all 54 preview hashes match.
- Full suite passes 236/236. Evidence: `reports/benchmarks/scan-timing-20260928-154948/`.
- This completes measurement scaffolding, not a measured speedup. Full visual-logo/animation A/B and full-film performance benchmarks remain. No production rerun, merge or push was performed.
- Next: eliminate measured duplicate work/cache invalidation, then bounded prefetch/OCR batch experiments and compatible shared decode. See `docs/SCAN_PERFORMANCE.md`.

## Logo geometry-track coverage V0.7.24 — 2026-09-28

- Approved brand-memory matches now require similarity `>=0.94` before bypassing semantic confirmation; weaker matches return to ordinary candidate routing.
- Candidate coverage groups strong matches by learned logo geometry and five-minute timeline bucket instead of treating every memory record as a separate physical logo.
- Selection retains every concrete regional lead, two complementary full-frame representatives per bucket, and one representative for every approved geometry track per bucket without exceeding its configured budget.
- The real Troy advertising-only rerun retained 199/199 regional leads and 39/39 approved geometry/time groups, with zero missing required full-frame representatives.
- The resulting queue covers all 123 source candidates, collapses 117 repeated findings into six review groups, has no missing references, and passes deterministic Structure Audit.
- The base OCR, Florence, GroundingDINO, Qwen, adult, gore, violence and render models were not changed.
- Verification: 228/228 automated tests pass. Real-run evidence is recorded in `docs/SESSION_HANDOFF.md`.

## Sustained explicit-scene priority V0.7.23 — 2026-09-27

- Sustained adult findings labelled `porn` or `hentai` with score `>=0.99` for at least four seconds are promoted to high review priority.
- The change only affects review ordering; it does not create a candidate, choose an edit, or modify a saved decision.
- Troy evidence at `15:28.5-15:50.5` and `16:23-17:07` is present in the detector output. The gap is a banquet cutaway.
- Existing queues can refresh priority safely while preserving all detector data and user decisions.
- Verification: 225/225 automated tests pass.

## Live review queue revision refresh V0.7.22 — 2026-09-27

- Review pages poll the active queue revision every three seconds and reload when a rerun publishes a newer revision.
- The current filter is preserved and resource/export state is reloaded; user decisions continue to be persisted before refresh.
- This prevents newly detected adult, violence, or advertising items from remaining hidden behind an older in-memory queue.
- Verification: 224/224 automated tests pass.

## Stable rerun controls V0.7.21 — 2026-09-27

- Dashboard refreshes every three seconds but now captures the open/closed state of every `Chạy lại kiểm tra` panel before rebuilding video cards.
- An open rerun panel is restored immediately after rendering, so detector choices no longer disappear while the user is selecting a scope.
- The saved UI state is removed after the rerun request succeeds. This changes no scheduler, detector, report, review or renderer behavior.
- Shared stages now sort sibling artifact roots by a stable path key. This removes a pre-existing nondeterministic cache miss for the paired gore/violence reports discovered by the full regression suite.

## Live-action adult sequence completion V0.7.20 — 2026-09-27

- A frame-by-frame Troy audit confirmed a temporal coverage defect rather than a render defect. The source adult sequence begins around `06:56`, while the old `0.95` frame gate produced five separate intervals beginning only at `07:01.5` and left short unblurred cutaways.
- The high-confidence `0.95` score remains the only signal allowed to create a required review item. Nearby `0.70+` scores may only extend that existing seed, bounded to eight seconds on either side.
- Adult intervals separated by at most three seconds after padding are presented as one continuous review interval. This covers camera cutaways inside the same sequence without merging the unrelated candidate at `07:55–07:58`.
- Applying the new policy to the measured Troy scores changes the fragmented `07:01.5–07:40.5` coverage into one `06:54.5–07:45.5` interval. An isolated moderate score still creates no item.
- The policy values are explicit adult-stage arguments in both processing profiles, so the stage-cache command fingerprint changes and a requested rerun cannot restore the old adult artifact.
- No advertising/logo, gore, violence, review-decision or renderer logic changed. Verification: 223/223 automated tests pass.

## Completed export history V0.7.19 — 2026-09-27

- Completed cards retain a full 100% export bar instead of replacing progress with a generic completion label.
- The export card shows `100% · Đã xuất video` and the local date/time when the durable job entered `COMPLETED`.
- This uses existing job metadata and changes only Dashboard presentation.
- Verification: Dashboard JavaScript syntax and 211/211 automated tests pass.

## Live export progress V0.7.18 — 2026-09-27

- Final FFmpeg renders now emit machine-readable progress every second. Dashboard cards show true export percentage, encoding speed and an estimated remaining time.
- The denominator is the expected output duration after merged CUT intervals, not the unedited source duration.
- Analysis progress and export progress are independent. A fully analyzed video can remain at 100% analysis while its export bar advances from 0% to 100%.
- After FFmpeg reaches 100%, the card changes to `Đang kiểm tra output` while BiliFlow validates duration, video/audio streams and a full decode. It moves to `Hoàn tất` only after those checks pass.
- The `Đang chạy` tab contains separate `Đang phân tích để duyệt` and `Đang xuất video` sections, while queued work remains in `Đang chờ xử lý`.
- Progress uses a temporary sidecar under `temp/render-progress`; scheduler cleanup removes it on success, failure, pause or interruption.
- The FFmpeg protocol was validated with a real local encode. Python, JavaScript and 211/211 automated tests pass.

## Control Center lifecycle tabs V0.7.17 — 2026-09-27

- Dashboard videos are grouped into three live tabs: `Đang chờ xử lý`, `Đang chạy`, and `Hoàn tất`, each with its own count.
- Every card now separates scene-analysis progress, deterministic structure audit, Visual AI Audit, and export lifecycle instead of mixing them into one status column.
- Export state is explicit: not reached, waiting for review, ready, queued, rendering, verifying, failed, or exported successfully.
- Completed renders move to `Hoàn tất`; a rerun follows the existing job state back through waiting and active processing before returning to completion.
- Detector selection for reruns is collapsed until needed, reducing visual noise while preserving every existing action.
- This is a frontend-only change. No API, queue, detector, scheduler, review, or rendering behavior changed.
- Verification: live server HTML contains all lifecycle views, extracted JavaScript passes Node syntax validation, and 208/208 automated tests pass.

## Final-render filter compaction V0.7.16 — 2026-09-27

- Final rendering removes short logo blurs whose time range and region are already covered by an equivalent persistent blur; the review provenance and edit plan remain unchanged.
- Identical full-frame blur decisions share one FFmpeg filter with a merged multi-range enable expression.
- Troy job #39 now renders with one persistent XEMBZ.NET overlay and one full-frame blur filter instead of roughly 66 serial filters. This eliminates the filter-thread explosion that prevented the first frame from being produced.
- Immediate pause on Windows now terminates the full stage process tree and deletes only the matching incomplete render file inside `output`, preventing orphaned FFmpeg workers and stale-partial retry failures.
- Live validation: job #39 attempt 3 is actively writing output after restart; verification suite passes 207/207 tests.

## Persistent OCR region consensus V0.7.15 — 2026-09-27

- Repeated corner overlays no longer use an unconditional min/max union. The dominant size and position consensus rejects an occasional OCR box joined to nearby movie text.
- Existing full-film text-logo items can be tightened from spatially consistent visual OCR evidence without rerunning the source video or any model.
- Troy job #39 supplied 51 matching visual OCR regions. Its XEMBZ.NET proposal changed from `x=110, y=146, 626x84` to `x=105, y=160, 174x54`, excluding the unrelated words to its right.
- The rebuilt queue retains complete candidate coverage and deterministic local audit PASS.
- Verification: 204/204 automated tests pass.

## Long-video logo routing V0.7.14 — 2026-09-27

- Generic full-frame temporal correlation is now coverage evidence instead of a unique logo lead. This prevents slowly moving movie scenes from filling the semantic budget.
- Strong regional evidence must exceed the full-frame score by a meaningful margin and every such candidate is retained for local VLM review.
- Each five-minute bucket keeps two complementary full-frame representatives: one ident-oriented and one persistence-oriented. Missing regional evidence or a missing representative still fails deterministic coverage.
- Troy job #39 was rerun from its existing routing cache. The scan retains all 285 regional candidates and all 80 required full-frame representatives, collapses 1,889 redundant full-frame windows, and reports complete coverage.
- Existing adult and violence reports were reused. The rebuilt 294-item review queue has complete references and detector coverage; local structure audit returns PASS without using ChatGPT quota.
- Verification: 202/202 automated tests pass.

## Per-video output size policy V0.7.13 — 2026-09-27

- Review cho phép chọn riêng từng video: tối đa 3,5 GB mặc định, giới hạn GB tùy chỉnh hoặc không giới hạn dung lượng.
- Chính sách được lưu trong queue và edit plan, sau đó scheduler chuyển nguyên vẹn sang renderer. Custom/unlimited có định danh output riêng nên không đè lên bản render theo chính sách khác.
- Không giới hạn dùng libx264 CRF 20 không có VBV size ceiling; bảo vệ dung lượng trống, kiểm tra hình/tiếng, thời lượng, full decode và checksum nguồn vẫn bắt buộc.
- Queue cũ và CLI không truyền lựa chọn mới tiếp tục dùng mặc định 3,5 GB.
- Dashboard giữ bản nháp thể loại phim và profile riêng theo ID video qua mỗi lượt refresh 3 giây; lựa chọn `Phim thực tế` không còn bị option mặc định `Hoạt hình` ghi đè trước khi bắt đầu.
- Verification: 200/200 automated tests pass.

## Full-timeline logo candidate coverage V0.7.12 — 2026-09-26

- Visual-logo selection now gives novel evidence priority over repeated approved watermarks.
- Approved brands are tracked by identity and five-minute timeline bucket, retaining representative temporal evidence without spending hundreds of semantic slots on the same overlay.
- Careful scans can retain up to 420 windows; fast scans retain up to 120.
- Reports expose novel omissions and missing approved-brand time groups. Review queues and the deterministic AI Audit gate remain incomplete when either class lacks coverage.
- Region decisions now require exact-region evidence: approved brand memory, a compact structural site mark, or OCR text matching a concrete VLM brand name. A frame-level Qwen YES and a Grounding/DINO box alone remain optional evidence.
- Persistent grouping accepts only `approved_brand_memory` confirmations, excludes already-classified opening/end scenes, and partitions all identities by focus geometry. A weak 0.84 memory similarity can no longer create a full-film track.
- Opening promotion conversion is bounded to intervals ending within the first 30 seconds; a persistent overlay can never become a full-film CUT.
- Visual boxes merge only at IoU >= 0.50. This prevents a valid OCR watermark from donating BLUR to a neighbouring character, sign, or DINO box.
- Low-ad scene text stays auditable but does not block export. Required and optional candidates both count toward source-reference coverage.
- Conan Movie 20 validation retains 320/320 novel candidates and 70/70 approved-brand time groups while collapsing 900 repeated PhimOnline watermark windows. The clean queue is fully covered with two mandatory items: PhimOnline BLUR for 0–6689.578 seconds and opening ident CUT for 5–10 seconds; 387 weak/scene candidates remain optional.
- Advertising stages are byte-for-byte equivalent between advertising-only and all-model pipeline construction; adult, gore and violence routing is unchanged.
- Verification: 196/196 automated tests pass and all modified Python files compile.

## Visible optional-candidate review V0.7.11 — 2026-09-26

- Review now displays separate counts for required items and optional candidates.
- A highlighted `Ứng viên phụ (N)` filter sits beside `Chưa duyệt`; it exposes all weak OCR and regional-logo evidence retained for high-recall inspection.
- `Tất cả mục chính` refers only to blocking review decisions, removing ambiguity about the much larger optional evidence set.
- The Conan Movie 20 revision 3 UI reports 4 required items and 57 optional candidates.

## Region evidence isolation V0.7.10 — 2026-09-26

- Frame-level Qwen confirmation now means only that branding exists somewhere in the frame. A Grounding, GroundingDINO, or OCR rectangle needs its own regional corroboration before it can block export.
- Single-source unknown rectangles remain in `Xem tất cả ứng viên`, where a reviewer can promote a missed brand without treating faces and film objects as confirmed logos.
- OCR below 20% advertisement probability that is not a persistent overlay also moves to the optional candidate view. Persistent overlays and user policy matches still require review.
- Legacy reports receive the same routing at queue-build time, avoiding a full model rerun.
- Conan Movie 20 revision 2 contains 4 required items (3 pending after preserving the approved watermark blur) and 61 optional candidates, down from 19 required items with no evidence deletion.
- Verification: 174/174 automated tests pass.

## Per-video detector scope and local structure audit V0.7.9 — 2026-09-26

- Each video can run advertising/logo, adult, gore, violence, all groups, or any combination. The registry can accept future detector groups without changing job storage.
- Scheduler stages and review reports follow the saved scope. Reruns can choose a different scope and create a separate report revision.
- New queues explicitly record selected and skipped groups. Review UI states what was not scanned rather than treating an omitted detector as a negative result.
- Structural JSON validation runs locally after queue creation and consumes no ChatGPT quota. Visual AI remains the only Codex-backed audit and still requires per-video confirmation.
- Animation safety categories share one inference pass, so selecting multiple safety outputs does not decode the video three times.
- Verification: 169/169 automated tests pass; rendered Control Center and Review JavaScript both pass Node syntax validation.

## Shared AI Supervisor session V0.7.8 — 2026-09-26

- JSON and visual audits now reuse one persistent Codex thread across batches and videos.
- Audit execution is serialized around the shared thread; each turn still carries its own queue manifest, allowed item IDs, and bounded thumbnails.
- Returned visual assessments are filtered to item IDs attached to the current turn, preventing earlier video results from being applied to a new queue.
- A new thread is created only when no saved thread exists or Codex cannot resume it. Control Center reports whether the shared session has been initialized.

## Region precision and scene-text guard V0.7.7 — 2026-09-26

- Full-resolution foreground refinement tightens approved watermark boxes without changing human review authority.
- End-card evidence is selected from inside the refined time interval.
- End-card CUT grouping now requires an explicit full-frame promotion signal; a persistent corner watermark alone remains a regional candidate.
- Low-ad, non-overlay OCR evidence protects only the overlapping in-film text region; approved brand-memory regions retain priority.
- Text queue items now include source-frame size and clipped source coordinates.
- Conan regression: 79/79 candidates covered, no missing references, PhimOnline tightened from 308x69 to 282x46, in-film `SCRAP BOOK` kept, and no false full-scene end-card CUT.
- Historical brand-memory thumbnails from four source videos remain contained after refinement; the smallest retained area is 60.8% of the reviewed box, while ambiguous regions remain unchanged.
- Verification: 161/161 automated tests pass; license audit reports 9 allowed local models and 0 blocked models.

## Winning-signature geometry V0.7.6 — 2026-09-26

- Brand-memory routing now carries the geometry belonging to the actual winning signature.
- This fixes correct PhimOnline appearance matches receiving an unrelated record's region before localization.

## Pending-item migration safety V0.7.5 — 2026-09-26

- Rebuilding an existing queue cannot silently remove candidates that still await a human decision.
- Detector improvements affect new routing, while unresolved legacy candidates remain visible with migration provenance until reviewed.

## Region-bound brand memory V0.7.4 — 2026-09-26

- Approved logos now carry their learned relative position through scanning and localization, so nearby scene text cannot inherit a frame-level logo match.
- Regional memory matching checks geometry as well as appearance, and full-frame scene decisions no longer contaminate the logo library.
- Known logo regions bypass unrelated OCR/Grounding proposals while remaining subject to human review before editing.

## Target-region review safety V0.7.3 — 2026-09-26

- Review cards now state that only the red rectangle is being classified and separately identify a previously approved persistent watermark elsewhere in the frame.
- A high-confidence Visual AI conflict requires explicit confirmation before the opposite human edit is saved.
- Unrelated overlapping region decisions are no longer presented as protection for the current candidate.

# Project status — 2026-09-24

## Contained watermark OCR deduplication V0.7.2 — 2026-09-26

- Short OCR fragments proposed for BLUR are absorbed when their region and interval are fully contained by a confirmed persistent watermark. Movie titles and text outside that region remain independent review items.
- Conan item `review-05067ca7cccb` (`NPt`) was proven to be a fragment inside the already tight full-timeline PhimOnline watermark and was merged into the main item with its source evidence preserved.
- Conan queue changed from 94 to 93 pending items with zero automatic decisions, zero duplicate IDs, zero integrity blockers and zero deterministic quality findings. 148/148 tests pass.

## Review identity and Visual AI safeguards V0.7.1 — 2026-09-26

- AI Audit is scoped to the active queue revision; rerunning a video no longer shows an older audit or lets an older worker write into the new queue.
- Review IDs now include candidate type, review kind, classification and source-pixel region. Duplicate IDs are a deterministic integrity blocker.
- Opening promotions and branded end cards are each presented as one full-scene CUT candidate; region BLUR cannot override that policy.
- Visual AI receives source-frame dimensions and explicit target-region guidance. A watermark elsewhere in the thumbnail must not classify the reviewed region as a logo.
- High-confidence disagreement between local and Visual AI suggestions clears the bulk suggestion and requires a human choice. AI still makes no edit decision.
- Conan revision 2 was migrated in place with zero automatic decisions: 94/94 unique IDs, 35 Visual AI assessments retained and the closing end-card changed to a CUT proposal.
- Regression, compile and full test suite pass: 148/148.

## GroundingDINO region fallback V0.7.0 — 2026-09-26

- Thêm POC độc lập trên 48 ảnh từ quyết định review thật: 24 BLUR/CUT và 24 KEEP. Report chuẩn ở `benchmarks/ad-pipeline-regression-v3/benchmark.json`; benchmark không sửa queue hoặc video.
- SigLIP Apache-2.0 chỉ đạt top-half recall 41,67% và bị khóa khỏi production. Không dùng model này để bỏ qua candidate.
- PySceneDetect không được thêm vì visual scanner hiện đã route theo scene-change và coverage bucket trong cùng lượt giải mã; thêm một detector cảnh riêng sẽ lặp công việc mà benchmark này chưa chứng minh được lợi ích.
- GroundingDINO Tiny Apache-2.0 không đạt specificity để phân loại toàn cảnh, nhưng đạt region recall 100% ở IoU >= 0,05 trên 8 mục BLUR có vùng chuẩn. Riêng logo NewGates đạt IoU xấp xỉ 0,94.
- Pipeline mới giữ scanner/Qwen làm semantic gate, rồi chạy GroundingDINO tối đa một vùng bổ sung trên frame gốc. Vùng chỉ được đề xuất BLUR và luôn cần người dùng duyệt; model không tự CUT/BLUR/export.
- POC trên report Conan chỉ xử lý hai interval cần fallback trong 3,046 giây, thêm đúng một vùng logo xuyên phim `x=1546, y=36, width=318, height=79`; peak CUDA khoảng 1,09 GB.
- PaddleOCR 3.7.0 được A/B trong runtime riêng: cấu hình video 960 px đạt 6/6 vùng BLUR nhưng mất 0,569 giây/ảnh, trong khi EasyOCR hiện tại cũng đạt 6/6 và chỉ mất 0,325 giây/ảnh. PaddleOCR không được đưa vào production; runtime, model và pip cache thử nghiệm khoảng 1,08 GB đã được xóa, chỉ giữ report nhỏ.
- Cache model và kết quả nằm trên ổ E. GroundingDINO khoảng 658 MiB được giữ với revision và SHA-256 trong manifest; SigLIP khoảng 778 MiB đã bị xóa sau khi trượt cổng chất lượng. License audit hiện 9 model đã cài đều allowed; toàn bộ 144 test đạt.

## Visual AI Audit V0.6.1 — 2026-09-25

- Dashboard có hai chế độ riêng: AI kiểm tra JSON không gửi media và Visual AI Audit yêu cầu xác nhận cho từng video.
- Visual audit chọn tối đa 36 thumbnail có sẵn trong reports, ưu tiên logo/chữ rồi mới đến adult/gore/violence; mỗi mục tối đa ba ảnh. Đường dẫn ngoài reports, file trên 8 MiB, video và audio đều bị loại.
- GPT-5.6 Luna/Medium trả phân loại, độ tin cậy, đề xuất KEEP/BLUR/CUT/NEEDS_MORE_CONTEXT và chất lượng vùng blur. Kết quả chỉ được ghi thành nhận xét/đề xuất; quyết định vẫn do người dùng thực hiện.
- Billing bị khóa ở đăng nhập ChatGPT và hạn mức có sẵn; API key không được phép. Hết hạn mức thì lượt audit thất bại/chờ reset, BiliFlow không có chức năng mua credit.
- Smoke test bằng ảnh tổng hợp xác nhận App Server nhận localImage: phân loại external_brand, BLUR, confidence 0,99, vùng TIGHT. 139/139 test đạt; license audit 8 allowed, 0 blocked.
- Kiểm chứng thực tế trên job Conan #37 dùng ba batch, 12 ảnh mỗi batch và high-detail cho mọi nhóm. Kết quả 36/36 structured assessments, không còn ảnh bị bỏ qua: 24 KEEP, 1 CUT, 1 BLUR, 10 NEEDS_MORE_CONTEXT; 0 quyết định được AI tự áp dụng.

## Brand memory và adaptive routing V0.6.0 — 2026-09-25

- `state/brand-memory.json` hiện có 28 chữ ký perceptual: 27 chữ ký thương hiệu đã duyệt (7 BLUR, 20 CUT) và 1 chữ ký âm cho tiêu đề phim đã KEEP. File không chứa video và luôn ghi `automatic_edit: false`.
- Cache routing visual-logo nằm dưới `cache/visual-logo/<source-sha256>/`, nén gzip và khóa theo checksum nguồn, cấu hình, thuật toán cùng revision bộ nhớ. Lần retry sau lỗi kiểm chứng đã báo cache hit và không quét lại routing.
- Scanner giữ fallback phân bố theo timeline, route chuyển cảnh có tín hiệu và có thể dùng khớp bộ nhớ ở độ tin cậy cao thay Qwen cho cửa sổ đó; tất cả vẫn đi vào review.
- Hai report độc lập trên 15 giây đầu `#874-B2` đều giữ 3/3 cửa sổ; mỗi report có 2 memory match. Florence định vị 3/3, gồm NewGates `130,104,159,67` và visual grounding `97,13,240,224`.
- Review tách intro và logo góc thành hai mục vì vùng không chồng nhau; hai cửa sổ NewGates liền nhau được gom thành một mục `5–15s`.
- 139/139 unit tests đạt; license audit: 8 allowed, 0 blocked. Báo cáo OCR mới luôn kèm SHA-256 nguồn để AI audit đối chiếu với queue và các detector còn lại.
- Queue dài nay giữ manifest đối chiếu từng candidate nguồn với mục review sau gộp. OCR và visual-logo của cùng watermark cố định được gom thành một quyết định toàn timeline; opening promotion toàn khung được đề xuất CUT trước review. Job Conan Movie 21 xác nhận 88/88 candidate được biểu diễn, AI Audit PASS.
- AI BLOCK chỉ được giữ khi validator local chứng minh lỗi checksum, report, timeline hoặc candidate coverage. BLOCK thuần suy đoán từ model được hạ xuống WARN để không chặn nhầm review; nhận xét vẫn được giữ cho người dùng xem.
- Cửa sổ đầu/cuối video nay được Qwen phân loại theo ngữ cảnh toàn cảnh để gộp trọn quảng bá thành CUT. Vùng BLUR logo được cân bằng giữa hộp grounding và OCR: giữ đủ biểu tượng đồ họa, thu phần đệm dư. AI Supervisor có thêm kiểm tra tất định để cảnh báo CUT đầu video bị thiếu và vùng logo bị quá nhỏ hoặc quá rộng.
- Logo/chữ cố định bị OCR bắt gián đoạn được gom theo nội dung, vị trí và độ phủ timeline. Chỉ chuỗi có thêm bằng chứng quảng cáo mới được đề xuất blur; chuỗi điểm quảng cáo thấp được bảo vệ như tiêu đề phim.
- Review có lựa chọn blur logo, blur toàn cảnh, cắt cảnh và hiển thị quyết định blur chồng thời gian. Vùng visual-logo trùng OCR tiêu đề được đề xuất KEEP.
- Florence tạo một mục review riêng cho từng vùng trong cùng frame; kết luận toàn cảnh không còn tự động áp dụng cho mọi chữ/logo. Review có thêm `Xem tất cả ứng viên`; candidate bị VLM loại không chặn export nhưng có thể được đưa vào queue chính bằng quyết định của người dùng.
- Job `#874-B2` giữ nguyên 7 mục chính và toàn bộ quyết định đã có, đồng thời có 26 ảnh audit tùy chọn (khoảng 4,7 MB). Kiểm chứng frame 25 giây: NewGates bên trái vẫn BLUR còn tiêu đề phim bên phải KEEP. Frame 209 giây nằm trong track NewGates BLUR `6,25–429,062s`; thẻ violence vẫn chờ người dùng quyết định độc lập.

## AI Supervisor portable config V0.5.2 — 2026-09-25

- Dashboard hiển thị trạng thái cài đặt/đăng nhập Codex và có nút mở `codex login` trong trình duyệt.
- `config/ai_supervisor.json` đi cùng project; mặc định `gpt-5.6-luna`/`medium`, ChatGPT auth bắt buộc, service tier `default`, gửi media tắt. Chỉ dòng GPT-5.6 được phép; reasoning cao nhất là `high`.
- API chỉ nhận model thuộc dòng GPT-5.6 và Low/Medium/High; API key, GPT-6 và mức trên High bị chặn. App Server nhận model/effort ở từng thread và turn.
- Smoke test thật nhận đúng đăng nhập ChatGPT; cấu hình hiện dùng GPT-5.6 Luna/Medium và shutdown sạch.

## Control Center V0.5.0 — 2026-09-25

- Đã hoàn thành phạm vi 1–12 để kiểm thử thực tế: Start/Stop theo phiên, SQLite WAL, import lịch sử, dashboard, scheduler, pause/cancel/retry, recovery, review theo job, export bền vững, watcher, hai profile và AI Supervisor theo yêu cầu.
- Import thật nhận 6 nguồn và 14 revision review; không sửa video/report cũ. Tập 1 được suy luận đúng là `live_action` từ toàn bộ lịch sử thay vì revision logo mới nhất.
- Smoke test cổng 8766 đạt dashboard/review/API, single-instance và shutdown sạch; không chạy scan/render nặng trong đợt kiểm thử kỹ thuật này.
- Một GPU worker xử lý tuần tự, nhưng dashboard nhận nhiều job cùng lúc; review và web vẫn chạy song song. Mọi sửa media tiếp tục cần quyết định người dùng.
- BiliBili upload, Telegram/Tailscale và archive/retention tự động chưa triển khai theo phạm vi đã chốt.

## Nâng cấp luồng nhanh V0.4.0 — 2026-09-24

- Sáu video nguồn dùng cho regression vẫn còn trong `input`; việc người dùng chuyển sáu bản đã xuất ra khỏi `output` không ảnh hưởng queue/report. Dataset `annotations/review-regression-v1.json` có 206 quyết định đã chuẩn hóa: 14 BLUR, 29 CUT, 163 KEEP.
- Logo nhỏ được quét bằng crop vùng chồng lấn rồi theo dõi qua timeline. Trên video `#874-B2`, hệ thống tự gom NewGates Anime thành một mục `6,000–429,312s`, đề xuất BLUR vùng `x=97, y=13, width=240, height=224`; end-card `429,312–445,312s` thành một mục CUT riêng.
- Hàng kiểm chứng `reports/shin-874-b2-fbf0cf7b-review-v2/review-queue.json` có 8 mục. Ba candidate visual không còn bị gộp nhầm: opening `0–5s`, persistent overlay `6–429,312s`, branded end-card `429,312–445,312s`.
- Review UI hỗ trợ nhận hàng loạt các đề xuất đang lọc. Khi mọi mục đã được giải quyết, một nút duy nhất khóa queue, tạo edit plan và khởi chạy output nền; không cần thêm vòng xác nhận qua chat. Output vẫn bị chặn trên 3,5 GB và phải qua full-decode validation.
- Nhiều session được phép chạy song song ở mức job. Tác vụ CUDA xếp hàng qua `Local\\BiliFlowGpuInference`; final render xếp hàng qua mutex và file lock riêng. Cách này giữ UI/CPU job đồng thời nhưng tránh hai model cùng chiếm RTX 2060 6 GB.
- Quét visual-logo tối ưu dùng 40 cửa sổ trên video 445,312 giây và hoàn tất trong 158,520 giây; báo cáo cuối còn 3 mục visual thay vì hàng chục cửa sổ lặp lại.
- Bộ kiểm tra tự động hiện đạt 97/97.

## Job mới: Tiếng Yêu Này Anh Dịch Được Không — Tập 2 (2026-09-24)

- Nguồn `input/Tiếng Yêu Này Anh Dịch Được Không - Tập 2.mp4` là job độc lập, 344.101.373 byte, SHA-256 `3148f4832c4a9da9877a00eb761371b18f59291c871a44b54a375f2f66b11e37`, 4.084,287 giây, H.264 1280×720 23,976 fps và AAC stereo. Checksum trước và sau quét không đổi. Không chuyển timestamp, vùng blur hay quyết định từ Tập 1.
- Queue đang dùng: `reports/tieng-yeu-tap-2-review-v1/review-queue.json`. Người dùng đã giải quyết 45/45 mục: 33 `KEEP`, 11 `CUT`, 1 `BLUR`. Người dùng xác nhận CUT liên tục 3801,5–4084,287 giây; biên phát hiện gốc 3935–3945 giây vẫn nằm trong queue và edit plan.
- OCR/semantic quét 1.361 frame toàn phim, giữ 13 text track; lượt dày 0,5 giây quanh banner quét thêm 90 frame. Mục banner sau hợp nhất là 213–243,5 giây; vùng OCR của chính Tập 2 gợi ý `x=0, y=44, width=1280, height=78`, `vertical_only` nếu người dùng chọn BLUR.
- Visual logo exhaustive quét 8.287 frame và đủ 817/817 cửa sổ. Qwen giữ 7 confirmed + 19 uncertain; 791 rejected vẫn có ảnh tại `reports/tieng-yeu-tap-2-visual-logo-exhaustive-v1/audit.html`. Florence đề xuất vùng cho 26/26 mục, không tự quyết định sửa.
- Adult live action quét 8.168 frame, giữ 23 interval thô. Gore ngưỡng chuẩn 0,5 không giữ interval; lượt nhạy 0,2 giữ khoảng 2237–2240 giây để người dùng xem. Violence ViT quét 32.671 frame/4.082 cửa sổ, tạo 152 interval thô; Qwen giữ 1 candidate 281,875–284,875 giây.
- `reports/tieng-yeu-tap-2-review-v1/cut-gap-audit.html` ghi 10 khoảng hở. Các khoảng 185–210, 350–370 và 375–385 giây là cảnh phim; chuỗi credit/brand cuối nay được bao trọn bằng CUT liên tục 3801,5–4084,287 giây theo quyết định người dùng.
- Edit plan `work/tieng-yeu-tap-2-edit-plan-v1.json` có đúng ba operation: CUT 0–10 giây, BLUR 213–243,5 giây vùng `0,44,1280,78` với `vertical_only`, CUT 3801,5–4084,287 giây. CUT cuối gộp chín candidate CUT liên quan và giữ từng biên phát hiện gốc. Thời lượng dự kiến sau CUT là 3791,5 giây.
- Người dùng đã duyệt đủ ba preview tại `previews/tieng-yeu-tap-2-edit-v1/`; manifest `APPROVED`, `scope: ALL`. Output mới `output/tieng-yeu-tap-2-reviewed-v1.mp4` đã hoàn tất: 577.014.939 byte, 3791,500 giây, H.264 1280×720 + AAC, SHA-256 `DC59BA171267D7F0E68AF0544231E4BA07A290F10295943243A62F3583BC66C8`. Giải mã toàn bộ hình/tiếng không lỗi, dưới 3,5 GB; checksum nguồn không đổi. Đã trích và kiểm tra frame đầu, quanh BLUR và cuối; xem `reports/tieng-yeu-tap-2-review-v1/final-qa-notes.md`. Tổng tài nguyên Tập 2 tạo khoảng 603,46 MB, chưa xóa preview hay audit.

## Trạng thái bàn giao chuẩn cho session mới — cập nhật 2026-09-24

Đây là nguồn sự thật hiện tại cho video `Tiếng Yêu Này Anh Dịch Được Không - Tập 1.mp4`. V1–V6 chỉ là lịch sử đối chiếu; mọi việc tiếp theo phải dựa trên V7.

- Queue chuẩn: `reports/tieng-yeu-tap-1-brand-review-v7/review-queue.json`; đã giải quyết 70/70 mục và lưu audit cho các biên thời gian được người dùng chỉnh.
- Edit plan chuẩn: `work/tieng-yeu-tap-1-edit-plan-v7.json`; intro CUT liên tục `0–10,5s`, đoạn cuối CUT liên tục `3569,5–3722,175s`, cùng CUT giữa phim `2875–2880s`.
- Preview blur chuẩn: `previews/tieng-yeu-tap-1-edit-v7-blur-v3/preview-manifest.json`; đã duyệt theo mẫu sau khi kiểm tra lúc chữ vào, ở giữa và ra khỏi khung.
- Vùng banner riêng của video là `x=0, y=48, width=1280, height=72`, sigma 28, feather 3, `edge_feather_mode: vertical_only`. Hai mép ngang phủ kín, chỉ feather trên/dưới.
- Full V7: `output/Tieng-Yeu-Nay-Anh-Dich-Duoc-Khong-Tap-1-reviewed-v7.mp4`; manifest cạnh file có trạng thái `COMPLETED`.
- V7 có 629.702.558 byte, dài 3.554,012 giây so với 3.554,000 giây dự kiến, 1 luồng hình và 1 luồng tiếng. Toàn file giải mã không lỗi, dưới 3,5 GB và checksum nguồn trước/sau không đổi.
- Khung đầu, ba mốc blur và khung cuối đã được lấy trực tiếp từ full V7 để xác nhận ba lỗi người dùng báo đã được sửa.

## Kết luận hiện tại

POC nhận diện quảng cáo tiếng Việt và blur V8 đã hoàn tất. Ba nhóm 18+, blood/gore và violence đều đã có baseline hỗ trợ review trong vùng mục tiêu 80–85% ở các tập hiện có. Violence phim người thật nay dùng ViT tạo candidate và Qwen2-VL-2B xác nhận; violence anime V5 vẫn đạt recall 86,44%, precision 80,95% và balanced accuracy 84,27% trên 126 mẫu.

Policy yêu cầu mọi lượt chạy phải miễn phí, local và commercial-safe. VideoMAE XD violence cũ đã được thay bằng ViT và Qwen2-VL-2B, đều Apache-2.0. Benchmark xác nhận nhỏ 11 clip đạt recall 100%, specificity 85,71% và balanced accuracy 92,86%; cần tiếp tục mở rộng dữ liệu vì mẫu hiện còn ít.

Video độc lập Sintel dài 14 phút 48 giây đã được quét, review và dùng để kiểm chứng toàn bộ luồng xử lý. Queue 39 mục đã được giải quyết thành 37 `KEEP`, 1 `CUT` và 1 `BLUR`; hai preview được người dùng duyệt trước khi dựng. Bản cuối dài 886,064 giây, dung lượng 173.784.506 byte, giữ hình H.264 và tiếng AAC 5.1, giải mã toàn bộ không lỗi và checksum nguồn không đổi. Video mẫu, preview và output thử đã được xóa sau khi hoàn tất kiểm chứng; báo cáo nhỏ được giữ lại để cải tiến detector.

## Trạng thái theo roadmap gốc

| Phase | Trạng thái | Đã có | Còn thiếu để hoàn thành |
|---|---|---|---|
| 0. Environment Assessment | Hoàn thành | Windows/GPU/RAM/disk/driver/tool assessment; kiến trúc, chi phí và kế hoạch | Chỉ cần đánh giá lại khi phần cứng/driver thay đổi |
| 1. Local AI Proof of Concept | Baseline kỹ thuật hoàn thành | OCR; 18+ benchmark; gore benchmark; violence anime V5; ViT violence Apache-2.0; quét độc lập Sintel; quét brand/logo exhaustive V0.3.2 và Florence-2 khoanh vùng | Dùng quyết định review thật để giảm báo dư mà không làm mất recall |
| 2. Video Processing | Hoàn thành POC end-to-end | Hàng đợi review chung; bốn quyết định; khóa edit plan; preview; phê duyệt; render CUT/BLUR có audio; kiểm tra nguồn và giới hạn 3,5 GB | Mở rộng dần từ lỗi video thực tế, không chặn phase kế tiếp |
| 3. BiliBili Upload POC | Chưa bắt đầu | Chưa có | Xác minh đúng Creator Center, upload draft, poster/metadata, success detection và retry |
| 4. Telegram Integration | Chưa bắt đầu | Chưa có | Bot allowlist, secret store, status/approve/stop/retry và thông báo |
| 5. End-to-End Automation | Đã có luồng review→export một cổng | Review UI nhận đề xuất, finalize một nút, background export và job-state riêng theo source/decision hash | Input watcher, metadata/upload BiliBili và Telegram |
| 6. Reliability & Optimization | Nền móng hoạt động | Storage thresholds, cleanup dry-run, local-only, source preservation, khóa GPU/render liên process, output cô lập và full-decode validation | Resume sau Windows restart, retention sau khi upload thành công và archive verification |

## Các bài test đã hoàn tất

- PyTorch CUDA nhận RTX 2060 6 GB; local NSFW inference chạy được.
- FFmpeg/FFprobe portable chạy trên ổ E; CPU H.264/x265 hoạt động.
- NVENC của FFmpeg hiện tại không tương thích driver hiện tại; chưa cập nhật driver.
- NSFW baseline trên Tears of Steel 720p: 734 frame, 12,270 giây, 59,836× real-time; hai cảnh báo đều là false positive do ánh sáng đỏ/hồng.
- OCR toàn phim Conan: 1.988 frame ở chu kỳ 3 giây, 307,775 giây, 19,382× real-time; chỉ một dải quảng cáo thật ở 00:03:06–00:03:32.
- NSFW toàn phim Conan: 5.964 frame, 125,121 giây, 47,676× real-time; 23 đoạn vượt ngưỡng đều không phải nội dung 18+ sau khi xem ảnh mạnh nhất.
- Gore toàn phim Conan: 5.964 frame, 121,076 giây, 49,269× real-time. Model bỏ sót cảnh máu thật tại 01:01:39 và ưu tiên nhầm title đỏ; baseline bị loại.
- Violence toàn phim Conan: 5.964 frame, 117,625 giây, 50,714× real-time. Model tạo 169 interval/514 giây ở ngưỡng 0,5 nhưng vẫn bỏ sót cảnh máu thật; baseline bị loại.
- OCR Conan 5 phút: 100 frame, 22,254 giây, 13,481× real-time; tìm đúng banner `i999.ai`.
- Refine endpoint: 100 frame trong cửa sổ 50 giây ở chu kỳ 0,5 giây, 30,082 giây; banner cuối cùng khoảng 00:03:31.
- V8 dùng look-ahead 3 giây và tail 0,5 giây. Frame preview 0:28 còn blur; frame 0:29,5 đã sạch và không blur.
- Benchmark 18+ POC: nano bắt đúng 17/20 dương tính, đúng 12/22 âm tính ở ngưỡng 0,50. Ở ngưỡng 0,95, nhánh anime đạt recall 80% và specificity 80%; nhánh phim thật bắt 9/10 dương, đúng 2/2 âm.
- Gore phim thật: 8/10 dương và 20/20 hard negative; recall 80%, precision 100%, balanced accuracy 90%.
- Gore hoạt hình: 9/10 dương và 16/20 hard negative; recall 90%, precision 69,2%, balanced accuracy 85%.
- Smoke test gore hoạt hình 35 giây: 70 frame trong 4,532 giây (7,724× thời gian thực); temporal gate loại 2 hit rời rạc, không tạo interval sai.
- Violence phim thật: VideoMAE-small đúng 8/10 dương và 19/20 âm; recall 80%, precision 88,9%, balanced accuracy 87,5%.
- Violence anime: tagger anime đúng 8/10 dương và 19/20 âm; recall 80%, precision 88,9%, balanced accuracy 87,5%.
- Scanner bạo lực phim thật chạy khoảng 6,1× thời gian thực, dùng khoảng 140 MB VRAM; nhánh anime dùng khoảng 512–639 MB VRAM tùy batch.
- Falconsai bị loại vì báo sai 22/22 hard negative; model nano dùng khoảng 248 MB VRAM trong benchmark song song.
- Unit tests hiện tại: 97/97 passed, gồm policy detection, semantic OCR routing, text continuity, review queue, bulk suggestion, visual-logo regional/temporal grouping, Florence focus region, khóa tài nguyên, regression dataset, SQLite/job import, scheduler, AI Supervisor protocol, finalize một cổng, full-decode final render và license gate.

## Field scan Conan 99 phút — 2026-09-20

| Nhánh | Tốc độ | Raw/confirmed hit | Interval | Tải review | Kết luận |
|---|---:|---:|---:|---:|---|
| 18+ anime | 33,68× real-time | 103 / 74 | 13 | 7,85/giờ | Tải duyệt đạt, nhưng 13 ảnh đại diện đều là cảnh người nằm/chạm, ánh đỏ, chim hoặc cảnh sinh hoạt; cần hard-negative mining |
| Blood/gore anime | 7,85× real-time | 620 / 464 | 69 | 41,64/giờ | Có candidate đáng xem nhưng lẫn nhiều người nằm, cảnh tối và màu đỏ; vượt gate 20/giờ |
| Violence anime | 7,81× real-time | 1.310 / 1.143 | 124 | 74,83/giờ | Nhãn `fire` lấn át top candidate; đang trộn nguy hiểm môi trường với bạo lực trực tiếp |

Ba báo cáo chiếm khoảng 15,33 MB và lưu 1.741 thumbnail; riêng violence lưu 1.143 thumbnail/9,33 MB. Dữ liệu không lớn cho một phim nhưng sẽ phình tuyến tính khi chạy lâu dài. Báo cáo mới phải chỉ giữ một ảnh mạnh nhất cho mỗi interval cùng top-K giới hạn; báo cáo cũ được nén hoặc xóa theo retention sau khi upload thành công.

Mô phỏng từ `max_score` của interval blood/gore cho thấy ngưỡng 0,20 còn 32 interval, tương đương 19,3/giờ, và vẫn giữ candidate mạnh nhất 4.901,5–4.909 giây. Đây mới là phương án hiệu chỉnh cần regression test, chưa phải threshold chính thức.

## Baseline anime V3 sau cải tiến

- Một lượt WD tagger dùng chung cho gore và violence mất 767,539 giây, thay cho 1.523,969 giây của hai lượt cũ: nhanh hơn gần 2 lần cho phần tagger và tiết kiệm khoảng 49,6% thời gian.
- Gore dùng hai tầng điểm 0,20/0,05, context 0,02, temporal 3/5 cho điểm cao và 4/5 cho context. POC đạt recall 80%, specificity 80%, balanced accuracy 80%.
- Gore field còn 33 interval sau khi gom các candidate cách nhau tối đa 6 giây, tương đương 19,92/giờ. Candidate 4.901,5–4.909,5 giây vẫn được giữ.
- Violence chỉ dùng tag hành vi trực tiếp; `fire`/`explosion` được ghi thành danger và không kích hoạt violence. Field còn 19 interval, tương đương 11,47/giờ; top candidate là punching, fighting, kicking và strangling.
- Báo cáo V3 giữ 33 ảnh interval gore, 19 ảnh interval violence và tối đa 20 top candidate mỗi nhánh. Dung lượng dưới 1 MB thay vì khoảng 13,17 MB của hai báo cáo anime cũ.
- Ở V3, bộ positive violence anime cũ gồm cảnh injury và fire danger nên kết quả 80% khi đó bị hạ trạng thái. Thiếu sót này đã được xử lý bằng benchmark direct-violence V5 ở phần dưới.

## Baseline anime V4 — shared adult/gore/violence

- V4 tạo cả ba báo cáo adult, gore và violence từ một lượt WD tagger 754,749 giây; thêm adult score không làm chậm V3.
- Adult nano trên manifest mở rộng chỉ đạt recall 80%, specificity 63,64%, balanced accuracy 71,82% cho anime và bị thay thế.
- Adult WD policy dùng 20 nhãn giải phẫu/hành vi cụ thể, bỏ nhãn `explicit` tổng quát, ngưỡng union 0,15 và yêu cầu ít nhất 3 nhãn đạt 0,05. Trên 10 positive + 33 negative anime: recall 100%, specificity 96,97%, precision 90,91%, balanced accuracy 98,48%.
- Field V4 trước điều kiện co-occurrence tạo 7 interval adult, 4,22/giờ và đều là false positive khi xem ảnh. Sau policy cuối, chỉ 1/7 strongest frame còn vượt điều kiện trước temporal gate; lần quét kế tiếp sẽ đo exact interval count mà không cần thêm một lượt full scan trong vòng này.
- Gore giữ 33 interval, 19,92/giờ. Violence giữ 19 interval, gồm 8 high priority và 11 context priority.
- Audit violence provisional: 7/8 high-priority interval là hành vi trực tiếp rõ ràng; 11 context interval gồm 6 false positive đã quyết định và 5 `NEEDS_MORE_CONTEXT`. Đây là precision audit, không đo recall.

## Baseline anime V5 — temporal ensemble và video độc lập

- Thay benchmark violence anime cũ bằng 112 clip Sintel có hành vi trực tiếp và 14 clip Conan đã review: tổng 126 mẫu, gồm 59 dương và 67 âm từ hai video.
- Policy cuối lấy hợp của VideoMAE temporal ở ngưỡng 0,61 và WD direct-action ở ngưỡng 0,20. VideoMAE bắt chuyển động kéo dài; WD bù các cảnh anime mà model video bỏ sót.
- Toàn bộ tập đạt recall 86,44%, precision 80,95%, specificity 82,09% và balanced accuracy 84,27%.
- Riêng holdout Sintel chưa dùng để chọn ngưỡng đạt recall 85,71%, precision 82,76% và balanced accuracy 84,52%.
- Full scan Sintel tạo 20 interval sau khi gom khoảng cách review 8 giây, gồm 7 high và 13 context; báo cáo khoảng 231 KB, không tạo output.
- 14 nhãn Conan là field review provisional; chúng đủ kiểm tra chéo phong cách ở vòng này nhưng sẽ tiếp tục được thay bằng nhãn độc lập khi có thêm video thực tế.

## Những việc chưa được xem là đã test xong

- POC 18+ đã có số đo hai chiều nhưng chưa đủ gate chính thức: mỗi nhánh vẫn thiếu 30 mẫu dương, 60 hard negative và video độc lập thứ hai.
- Blood/gore đã đạt gate POC nhưng chưa đạt field gate 30 dương + 60 âm mỗi phong cách.
- Violence anime đã đạt baseline hai video với 59 dương + 67 âm. Nhãn Conan vẫn là provisional nên chưa coi đây là chứng nhận production tự động.
- Quảng cáo chữ, logo có chữ và logo thuần hình ảnh đã có nhánh phát hiện local V0.3.2. V5 dùng prompt rộng giữ 134 cửa sổ; V6 quét dày 2 frame/giây với prompt chặt giữ 27 cửa sổ. Cả hai đều quét đủ 745/745 cửa sổ và giữ ảnh audit cho cả kết quả bị loại. Queue cuối lấy hợp OCR + V5 + V6 còn 70 mục. Đây là danh sách recall-first để con người duyệt, không phải 70 quảng cáo đã được xác nhận.
- Qwen2-VL-2B vẫn có cả false positive và false negative khi phân biệt logo ngoài phim với bảng hiệu trong bối cảnh. Phi-3.5 Vision INT4 thử trên 9 ảnh bắt đủ mẫu dương nhưng specificity 0%, nên trọng số/runtime đã bị xóa. Không model nào trong hai model này được phép tự quyết định sửa video.
- Đã có bản output POC và kiểm tra duration, audio stream, giải mã toàn bộ, điểm cắt, vùng blur, dung lượng cùng checksum nguồn. Chưa kiểm thử trên video đầu vào thực tế dung lượng lớn gần 3,5 GB.
- Chưa thao tác BiliBili hoặc Telegram. SQLite queue và phục hồi stage đã có trong V0.5.0; vẫn cần kiểm thử thực tế khi chủ động dừng một scan dài.

## Dung lượng hiện tại

Gói POC 18+ thêm khoảng 8,5 MB ảnh và 16,3 MB model. Gore thêm khoảng 3,1 MB ảnh, model phim thật 45,3 MB và model hoạt hình 378,7 MB. Violence dùng model ViT khoảng 343,2 MB và Qwen2-VL-2B khoảng 4,43 GB; nhánh anime dùng chung tagger gore. VideoMAE CC-BY-NC, Aleris, VideoMAE surveillance và SmolVLM thử nghiệm đã bị xóa sau khi giữ kết luận cần thiết. Ngày 2026-09-23 đã dọn thêm hơn 2,03 GB model/file tạm không đạt; benchmark Qwen nén còn dưới 5 MB.

Video thực tế `Tiếng Yêu Này Anh Dịch Được Không - Tập 1.mp4` dài 3.722,175 giây, 720p và 368,3 MB đã được thêm sau đợt dọn. Adult có 7 interval, gore không có interval vượt ngưỡng. ViT tạo 167 violence candidate; Qwen xác nhận lại trong 192,943 giây, loại 166 và giữ một interval 52:27,875–52:35,875 để người dùng xem. OCR giữ `NETFLIX SERIES`, `NGUONC.COM`, dòng bản quyền Netflix và `NETFLIX | DUBBING`. Người dùng phát hiện thêm logo N thuần hình ảnh tại 00:01,0–00:05,0. Queue V4 đã gộp intro Netflix thành CUT 00:00–00:10,5 và gộp toàn bộ phần Netflix cuối thành CUT liên tục 59:29,5–hết video. Preview V4 đã được duyệt và full render V4 đã hoàn thành, vượt đủ kiểm tra kỹ thuật.

Nhánh brand/logo exhaustive V5 quét 1.980 frame và toàn bộ 745 cửa sổ trong 326,592 giây. V6 tăng lên 7.563 frame ở chu kỳ 0,5 giây và hoàn thành 745 cửa sổ trong 395,023 giây, tốc độ 9,423× thời gian thực, RAM đỉnh khoảng 828 MB và CUDA khoảng 4,50 GB. V6 giữ 10 `CONFIRMED`, 17 `UNCERTAIN` và 718 `REJECTED`; prompt chặt đã loại được nhiều cảnh phim nhưng cũng bỏ chữ Netflix 5–10 giây mà OCR bắt được. Vì vậy không dùng V6 thay V5 mà lấy hợp cả hai với OCR.

Trang `reports/tieng-yeu-tap-1-visual-logo-exhaustive-v6/audit.html` có ảnh cho đủ 745 cửa sổ và lọc được Confirmed/Uncertain/Rejected; ảnh audit V6 chiếm khoảng 8,72 MB. Florence khoanh vùng 27/27 mục V6 trong 100,615 giây. Queue hợp nhất `reports/tieng-yeu-tap-1-brand-review-v6/review-queue.json` còn 70 mục, gồm 56 visual và 14 text; 69 mục có vùng gợi ý. Chưa có chỉnh sửa mới nào được áp dụng.

Ngày 2026-09-23, nhánh text được nâng lên semantic V7. Mã không còn quyết định quảng cáo bằng danh sách từ khóa: EasyOCR cung cấp chữ, tracker yêu cầu liên tục cả vị trí lẫn nội dung, model embedding local phân biệt quảng cáo/phụ đề/danh đề/chữ trong cảnh, sau đó temporal/geometry router xử lý lớp phủ và credit roll. Quy tắc Netflix được tách thành policy người dùng chỉ ép review. Model thêm 450.156.032 byte trên ổ E và chạy offline. Tập holdout khởi đầu nhỏ đạt 14/16; đây là smoke benchmark, chưa đại diện độ chính xác production.

Full scan V5/V7 trên cùng video xử lý 1.241 frame trong 369,466 giây, 10,074× thời gian thực và RAM đỉnh 1.277.317.120 byte. V7 vẫn giữ `NETFLIX SERIES`, toàn bộ Netflix cuối phim và banner `NGUONC.COM`; 160+ credit/chữ trong cảnh được tách khỏi candidate. Queue V2 dùng bốn text track có preview rõ và kết quả violence đã xác nhận; queue V1 38 mục được giữ làm đối chiếu vì chưa có quyết định nào cần chuyển.

## Thứ tự thực hiện tiếp theo

1. Duyệt 70 mục trong `reports/tieng-yeu-tap-1-brand-review-v6/review-queue.json`: `KEEP` cho tiêu đề/bảng hiệu thuộc nội dung phim; `BLUR` hoặc `CUT` cho logo hãng, watermark, tài trợ, quảng cáo và credit ngoài nội dung muốn giữ. Dùng `reports/tieng-yeu-tap-1-visual-logo-exhaustive-v6/audit.html` để rà thêm các cửa sổ Qwen loại khi cần.
2. Dùng các quyết định đã duyệt làm regression và thư viện tham chiếu logo động; không hardcode tên thương hiệu vào mã. Sau khi queue không còn mục chờ, mới tạo edit plan và preview mới.
3. Chỉ xuất bản video mới sau khi người dùng duyệt toàn bộ preview; tiếp tục áp giới hạn 3,5 GB và kiểm tra checksum nguồn.
4. Trước upload, ghi xác nhận quyền đối với video/nhạc/phụ đề/logo; sau đó bắt đầu Phase 3 bằng bản nháp BiliBili, chưa publish công khai.
5. Sau upload thành công mới nén archive, áp dụng retention và triển khai Telegram allowlist để duyệt từ xa.

## Review workflow V1 — 2026-09-22

- Đã thêm lệnh hợp nhất nhiều báo cáo của cùng video thành một queue JSON/HTML và gộp candidate trùng trong cùng nhóm.
- Mỗi mục có ID ổn định, timestamp, mức ưu tiên, score, nhãn, ảnh và đường dẫn bằng chứng.
- `build-edit-plan` bị chặn khi còn mục chưa duyệt hoặc `NEEDS_MORE_CONTEXT`.
- `BLUR` bắt buộc có vùng pixel hoặc xác nhận rõ full-frame; `CUT` và `BLUR` chỉ tạo preview ngắn ở gate hiện tại.
- Smoke test bằng video tự tạo xác nhận preview blur và cut đều có một luồng hình, một luồng tiếng; checksum nguồn không đổi; không tạo final output. Toàn bộ file smoke test đã được xóa.
- Queue Sintel thực tế có 39 mục: 18 gore và 21 violence sau khi gộp hai model. Hiện tất cả đang chờ quyết định, chưa có edit plan.
- Đã thêm giao diện cục bộ có nút `Giữ nguyên`, `Làm mờ`, `Cắt bỏ`, `Cần xem thêm` và `Bỏ chọn`; lựa chọn lưu ngay vào queue, có bộ lọc chưa duyệt/ưu tiên cao/gore/violence và thanh tiến độ.
- Đã thêm bulk `Giữ nguyên tất cả đang lọc` có bước hỏi lại; bulk blur/cut bị cấm. Giao diện hiển thị dung lượng nguồn/report, dung lượng trống và ước tính preview, đồng thời nêu rõ review không chạy AI/FFmpeg.
- Queue đã có trường `actor`/`transport` và audit log giới hạn để cùng một lõi quyết định có thể dùng cho Telegram. Thiết kế ưu tiên Telegram long polling, allowlist và chỉ gửi thumbnail khi người dùng bật truyền dữ liệu ra ngoài.
- Queue Sintel đã được giải quyết theo kế hoạch thử nghiệm: 37 KEEP, 1 CUT và 1 full-frame BLUR. Hai preview 6,0 giây và 9,5 giây đã được duyệt; bản cuối được dựng trong 53,364 giây, nhỏ hơn giới hạn 3,5 GB và vượt kiểm tra hình/tiếng/thời lượng/checksum.

## Acceptance gate kế tiếp

Gate Video Processing POC đã đạt. Với yêu cầu mới “chỉ giữ nội dung phim”, gate đang hoạt động là giải quyết queue brand/logo V6 rồi duyệt preview mới. BiliBili Upload POC ở chế độ draft chỉ bắt đầu sau gate này.
