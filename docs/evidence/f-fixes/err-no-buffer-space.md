# `net::ERR_NO_BUFFER_SPACE` in the account E2E tests (session F, item 3), 2026-10-13

Cause: the local servers listened with socketserver's default backlog of 5. A page load opens about 20
connections at once, so Windows refused the rest of that burst whenever the server thread was slow to accept.
Fix: `fe9bf89` (`http_guards.LocalHTTPServer`, backlog 128).

The numbers below are aggregates only. There are no hosts, addresses (other than loopback), paths or titles.

## What failed

- **Before the fix.** The branch at `74b2052` (main `ed39653` plus F items 1, 2 and 4) ran
  `tests.test_download_account_e2e` alone 10 times: 7 passed and 3 failed. Each failure was a console error
  `Failed to load resource: net::ERR_NO_BUFFER_SPACE`, which the page check (`assert_page_clean`) counts:
  - `/api/status` on the 390 px phone page;
  - `/api/downloads` on the 390 px phone page;
  - `/api/downloads` on the PC page.
- **With Edge net-logs** (`--log-net-log`, added by a wrapper outside the repository): 1 of 7 runs failed. Here a
  script of the first dashboard load was lost. The page fell back to demo mode, and `#dl-accounts` never showed.
- **What the net-log shows:**
  - 249 ms after Edge started, it opened about 20 TCP connections to the test PC server on 127.0.0.1 within a
    few milliseconds;
  - the server answers HTTP/1.0, so each request has its own connection;
  - one `connect()` returned `os_error 10055` (WSAENOBUFS) at once; Chromium maps it to `ERR_NO_BUFFER_SPACE`
    (-176).

## What it was not

- **Port exhaustion.** While the runs failed, 1-second samples of the system TCP table showed at most about 1,170
  entries (about 1,030 in TIME_WAIT). At the moment of the logged failure there were about 283 entries. The
  dynamic port range is 16,384 ports, 60 of them excluded, and Windows logged no Tcpip 4231 or 4266 event during the
  runs.
- **Leaks.** The test processes held no growing number of sockets. The handle growth seen earlier was Python lock
  semaphores.
- **Memory.** About 6 GB was free.

## Reproduction without BiliFlow (`edge_burst.py`)

`edge_burst.py` launches headless Edge with the E2E fixture's flags. Each load is a page with N scripts served by a
`ThreadingHTTPServer` (HTTP/1.0). "Busy" adds two CPU-bound Python threads (GIL contention, like a loaded test
process).

| Backlog | Shape (scripts x loads) | Busy | Requests | Failed |
|---|---|---|---|---|
| 5 | 40 x 30 | no | 1,230 | 0 |
| 5 | 40 x 30 | yes | 1,230 | 1,000 `ERR_CONNECTION_REFUSED` |
| 5 | 6 x 100 | yes | 700 | 60 `ERR_CONNECTION_REFUSED` |
| 5 | 20 x 60 | no | 1,260 | 1 `ERR_NO_BUFFER_SPACE` |
| 5 | 4 runs of 20 x 200 | no | 16,800 | 2 `ERR_NO_BUFFER_SPACE` |
| 128 | 4 runs of 20 x 200 | no | 16,800 | 0 |
| 128 | 40 x 30 | yes | 1,230 | 0 |

With a backlog of 5, Windows refuses a connection when the accept queue is full:
- usually `WSAECONNREFUSED` (10061) after about 2 s of SYN retries;
- rarely `WSAENOBUFS` (10055) at once.

Both are lost requests for the page. With 128, no request was lost, even with the busy threads.

## The test (`tests/test_http_guards.py::ListenBacklogTests`)

It opens 40 connections at once to each server while the server accepts none of them. The servers are the
Control Center through `ControlCenter.serve()`, the standalone review UI through `serve_review_ui()`, and the phone
listener `_PhoneServer`.

- On main: 5 were queued and 35 refused with 10061, on each server.
- After the fix: all 40 were queued.

## After the fix (`fe9bf89`): a rarer error remains

Each module was run alone, several times in a row:

| Module | Before | After |
|---|---|---|
| `tests.test_http_guards` | – | 10/10 |
| `tests.test_download_account_e2e` | 7/10 | 10/10 |
| `tests.test_download_account_e2e_flows` | – | 2 of 3 runs passed; the series was stopped there |

The failed run was `DashboardFlowsTest.test_session_expiry_mid_group_waits_then_resumes_in_order_after_a_new_sign_in`:
`Failed to load resource: net::ERR_NO_BUFFER_SPACE @ /dashboard-v2/theme.css`. It happened on the PC page, at a page
load, on the fixture server that now listens with a backlog of 128.

So `connect()` from Edge to 127.0.0.1 still, rarely, returns WSAENOBUFS at once. It was not found below the socket API:

- **Not port exhaustion.** In that run the system TCP table had at most 1,271 entries (1,151 in TIME_WAIT).
- **Not reproduced outside the E2E runs:**
  - 60,000 connections from Python, sent as bursts of 20 to an HTTP/1.0 server with a backlog of 128, from a
    separate process, with and without Chromium's socket options (TCP_NODELAY, a 45 s keep-alive, WSAEventSelect),
    had no error;
  - 120 freshly launched headless Edge instances, each loading a page with 20 scripts (2,520 requests), had no
    error.
- **Not in the logs.** Every failure with a known time was at a page load in a recently launched Edge. The Windows
  logs that could say more, TCPIP/Operational and Winsock-AFD/Operational, are off; turning them on needs
  administrator rights. The firewall and WFP logs show nothing at those times.

On 2026-10-13 the user chose to keep `fe9bf89` and stop the search here. The 10-run gate of
`test_download_account_e2e_flows` is therefore not met.

The options left open were:
- an elevated Windows trace while the E2E runs repeat;
- HTTP/1.1 keep-alive on the local servers, so a page load opens about 6 connections instead of about 20;
- test pages without Edge's own background traffic to the internet (an untested idea).
