# Tailscale managed by BiliFlow — plan (2026-10-06)

Branch `feat/phone-tailscale`, worktree `temp\wt-phone-tailscale`. Builds on the phone mode over Tailscale (CHANGELOG, `docs/DASHBOARD_V2_PHONE.md` section 8).

## User decisions (2026-10-06)

1. Reach the phone mode from outside the home Wi-Fi through **Tailscale**; over Tailscale the phone does everything it does on the home Wi-Fi (permanent deletes included).
2. BiliFlow sets Tailscale up itself from **Dashboard V2 → Cài đặt**: download, install, configure, sign-in, connect, and manage it in the background ("chạy ẩn").
3. ~~Install the program under `E:\DungChung\BiliFlow\runtime\tailscale`~~. **Changed on 2026-10-07** after the security review (every local account can modify `E:\DungChung\BiliFlow`, so a SYSTEM service there could be replaced): the user chose the default **`C:\Program Files\Tailscale`**; the installer cache, logs and temp files stay under the BiliFlow root. If the Tailscale service is not running when needed, BiliFlow starts it: from the panel, and first thing when "Mở cho điện thoại ngoài nhà" is pressed.
4. Tailscale **updates itself** (`TS_INSTALLUPDATES=always`).
5. Tailscale **runs unattended** (before a Windows sign-in; `TS_UNATTENDEDMODE=always`).
6. **"Gia hạn thêm 8 giờ" also from the phone**, only while the phone mode is open over Tailscale. On/off stay PC-only. **Changed on 2026-10-07** after the user's test (the auto-off time hardly moved, because a press set 8 hours from the press): each press adds 8 hours to the time left, at most 24 hours from now (the user's choice).

## What BiliFlow cannot do (said to the user)

- Skip the Windows administrator prompt (UAC): it appears when installing and when the service must be started or the firewall rule created. One prompt per action; the install action also configures the service and the firewall in the same prompt.
- Install or sign in the Tailscale app on the phone.
- Embed Tailscale without installing it: a userspace node would deliver phone traffic as 127.0.0.1 and defeat every PC-only check. Rejected.

## Design

### Files

- `src/biliflow/tailscale_manager.py` (new): finding the CLI, status, sign-in, connect/disconnect, the download and its checks, background tasks. Standard library only.
- `src/biliflow/windows_elevation.py` (new): `run_elevated(exe, args)` through `ShellExecuteExW` with the `runas` verb (ctypes); waits and returns the exit code; ERROR_CANCELLED (1223) means the user declined.
- `scripts/tailscale-setup.ps1` (new, ASCII, CRLF): the only code that runs elevated. Its parameters are fixed on its command line when Windows starts it (`-Action install|start|firewall -ResultId <16 hex>`, and for an install `-MsiName <name> -Sha256 <hex>`; changed 2026-10-07 after the security review: no request file another process could rewrite during the UAC wait). It computes every path from its own root (`Split-Path $PSScriptRoot -Parent`), refuses junctions and symlinks, and writes `temp\tailscale\result-<id>.json` with CreateNew. Actions:
  - `install`: hold `cache\tailscale\<name>` open (others may only read) from the checks until msiexec ends; re-check its SHA-256 and Authenticode signature (Valid, signer `O=Tailscale Inc.`); `msiexec /i <msi> /quiet /norestart TS_UNATTENDEDMODE=always TS_INSTALLUPDATES=always TS_NOLAUNCH=1 /L*v <log>` (default folder `%ProgramFiles%\Tailscale`); start the service; then the firewall rule.
  - `start`: `Start-Service Tailscale`.
  - `firewall`: remove and re-create `BiliFlow phone mode Tailscale (Python, TCP 8767)`: inbound, allow, the BiliFlow Python (`runtime\python\cpython-3.11.<newest>-windows-x86_64-none\python.exe`), TCP 8767, profile Private, remote `100.64.0.0/10`.
- `control_center.py`: `_tailscale_answer()` routes (like `_download_answer`); `_phone_access` passes the manager's address lookup.
- `phone_access.py`: the CLI is found by the manager (service ImagePath, then `%ProgramFiles%\Tailscale`; never PATH or the BiliFlow tree). `POST /api/phone-mode/extend` on the phone listener when the network is Tailscale.
- `dashboard_v2/app.js`, `contracts.js`, `adapter.js`: the "Tailscale" panel, the phone's extend button.

### Finding Tailscale

1. `HKLM\SYSTEM\CurrentControlSet\Services\Tailscale\ImagePath` → its folder → `tailscale.exe` (follows an auto-update that moved it).
2. `%ProgramFiles%\Tailscale\tailscale.exe`. (`<root>\runtime\tailscale` is never searched: every local account can write there.)

Service state from `sc.exe query Tailscale` (RUNNING / STOPPED / missing).

### Status (`GET /api/tailscale`, PC only)

`installed`, `cli`, `version`, `service` (running/stopped/missing), `backend` (`NeedsLogin`/`Stopped`/`Running`/`Starting`/…), `ip`, `hostname`, `tailnet`, `user` (login name), `peers` (host name, OS, online; no IPs of other devices), `firewall_rule` (exists/enabled), `task` (kind, stage, progress, message, error), `auth_url` while a sign-in waits. Read with `tailscale status --json` (10 s timeout). The status call never starts anything.

### Actions (`POST /api/tailscale/<action>`, 127.0.0.1 + token only, one task at a time)

| Action | What it does | UAC |
| --- | --- | --- |
| `install` | Read `https://pkgs.tailscale.com/stable/?mode=json`, take `MSIs.amd64`; download it and `<msi>.sha256` to `cache\tailscale\` (HTTPS, redirects only to `*.tailscale.com`, size cap, `.part` then rename); check SHA-256 and Authenticode; run `tailscale-setup.ps1 install` elevated. | 1 |
| `start-service` | `tailscale-setup.ps1 start` elevated. | 1 |
| `firewall` | `tailscale-setup.ps1 firewall` elevated. | 1 |
| `login` | `tailscale login`; read the `https://login.tailscale.com/…` link from its output (or `AuthURL` in the status); the page opens it in a new tab; the process waits up to 10 min. | 0 |
| `up` / `down` | `tailscale up` / `tailscale down`. | 0 |
| `logout` | `tailscale logout`. | 0 |
| `remote-on` | Start the service if stopped (UAC), `tailscale up` if stopped, wait for `Running`, then turn the phone mode on over Tailscale. Refuses with a clear reason when not signed in. | 0–1 |

All are refused on the phone listener (`PC_ONLY_POSTS`), and `GET /api/tailscale` is refused there too.

### Phone extend (decision 6)

`POST /api/phone-mode/extend` is in `PHONE_ALLOWED_POSTS`; the phone handler accepts it only when `phone.network == "tailscale"` (403 otherwise). Same `extend()` as the PC: adds 8 hours to the time left, at most 24 hours from now (decision 6, changed 2026-10-07), same code, a `PHONE_MODE_EXTENDED` event with the device IP. The phone's `GET /api/phone-mode` adds `expires_at`.

### Safety

- Agents never press install, start-service, firewall, login, up, down, logout or remote-on on the user's real Control Center, never download the real installer and never change the firewall. Tests fake the network, the CLI, the signature check and the elevation.
- The elevated script takes no free text and no request file: a fixed action name, a result id, and for an install an MSI name (strict pattern) and a SHA-256, all on its command line.
- The MSI is checked twice (before the prompt and inside the elevated script) against the published SHA-256 and the Tailscale Inc. signature.
- Downloads stay under `E:\DungChung\BiliFlow\cache\tailscale`; results under `temp\tailscale`; the msiexec log under `logs\tailscale`.

## Batches

- [x] **T1** manager: locate, status parse, service state, login/up/down/logout runners + tests (fakes).
- [x] **T2** installer: version JSON, download + SHA-256 + Authenticode, elevation, `tailscale-setup.ps1`, task state + tests.
- [x] **T3** HTTP routes, PC-only rules, `remote-on`, phone extend + tests (`tests/test_phone_tailscale.py`: routes, wiring, stop).
- [x] **T4** V2 panel and phone extend button + node gates (verify.cjs 37, verify-adapter.cjs 37). Checked on two test Control Centers (temporary roots, ports 8795/8796): the real manager on this PC (Tailscale not installed: only "Cài và cấu hình Tailscale"; its dialog cancelled, no POST) and a fake manager for the sign-in, download and connected states. `browser-check.cjs` was updated but skips here (no Playwright).
- [x] **T5** docs (guide section 8, AGENTS.md, README, CHANGELOG, status, handoff), security review, full suite.
- [ ] **User test** (first moved to 2026-10-08; on 2026-10-07 about 07:40 the user asked to go ahead, with no job running): restart the real Control Center from this code (consent, no job running); the user presses "Cài và cấu hình Tailscale", signs in, installs the phone app, presses "Mở cho điện thoại ngoài nhà", tries 4G and "Gia hạn" on the phone.
