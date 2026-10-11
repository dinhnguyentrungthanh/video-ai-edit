# Batch 4a — Control Center hooks for the lead (A2)

Agent 4a did not edit `src/biliflow/control_center.py`. Insert the five snippets below. They wire up:

- the "Bộ nhớ logo" page (`src/biliflow/logo_memory_admin.py`);
- the platform-logo review choice.

Line numbers are from the worktree on 2026-10-03 ~15:35 and drift while `control_center.py` changes; the anchors of snippets 2–5 are exact text (re-checked at 15:35).

`logo_memory_admin` has its own error handling:

- `handle_get` returns `(status, body, content_type)`, or `None` for any other path;
- `handle_post` returns `(status, payload)`, or `None`;
- it never raises for a bad request (400), a missing record or frame (404), a path outside `state/studio-logo-frames` (403), or a stale memory sha (409, `code: "memory_changed"`); any other error comes back as JSON 500.

Every read and write takes `export_guards.REVIEW_QUEUE_IO` itself (a listing takes it once per record, ~0.2 s each). It is an RLock, so it is safe if the caller already holds it. A delete writes the memory first, then moves the frames folder in one rename; if that folder is in use, the answer is still 200 with `frames_left` and a Vietnamese `warning`, which the page shows.

## 1. Import (line 19)

Add `logo_memory_admin` to the `from biliflow import …` line, right after `__version__`. At 15:35 the line was:
```python
from biliflow import __version__, recycle_bin, source_archive, source_archive_restore, source_cleanup
```
Replace with:
```python
from biliflow import __version__, logo_memory_admin, recycle_bin, source_archive, source_archive_restore, source_cleanup
```

## 2. `do_GET`: page, listing, frames (before the final 404, ~line 1667)

Anchor (end of the `elif` chain in `do_GET`):
```python
                else:
                    self.send_json(404, {"error": "Không tìm thấy"})
            except (KeyError, ValueError, FileNotFoundError) as error:
```
Insert directly **before** that `else:`:
```python
                elif (logo_memory := logo_memory_admin.handle_get(center.root, path, parsed.query)) is not None:
                    # "Bộ nhớ logo" (batch 4a): GET /logo-memory, /api/logo-memory,
                    # /api/logo-memory/frame?key=&i= — read-only; errors come back as JSON.
                    self.send_bytes(*logo_memory)
```
`parsed` and `path` are the variables already defined at the top of `do_GET`.

## 3. `do_POST`: class change and delete (before the final 404, ~line 1794)

Anchor (end of the `elif` chain in `do_POST`, after `authorized()` and `body = self.body()`):
```python
                else:
                    self.send_json(404, {"error": "Không tìm thấy"}); return
                self.send_json(200, result)
```
Insert directly **before** that `else:`:
```python
                elif (logo_memory := logo_memory_admin.handle_post(center.root, path, body)) is not None:
                    # "Bộ nhớ logo" (batch 4a): POST /api/logo-memory/class {key, memory_class,
                    # platform?, expected_sha256} and /api/logo-memory/delete {key, expected_sha256}.
                    # Host and token were checked above; 409 = memory changed since the page loaded.
                    status, result = logo_memory
                    if status != 200:
                        self.send_json(status, result); return
```

## 4. Review decision route passes the platform flag (~line 1768)

Anchor:
```python
                                remember_studio_logo=body.get("remember_studio_logo") is True)
```
Replace with:
```python
                                remember_studio_logo=body.get("remember_studio_logo") is True,
                                remember_platform_logo=body.get("remember_platform_logo") is True)
```
The standalone review server in `review_workflow.py` already passes it. Without this line, the button «Đây là logo nền tảng — làm mờ & nhớ» on a full-frame card (for example the forced ending card) gets 400 "Mục này chưa có vùng được định vị…". On a platform card it saves a plain BLUR without remembering.

## 5. Dashboard header link (line 348)

Anchor:
```html
<header><h1>BiliFlow Control Center</h1><span class="pill" id="worker">Đang tải</span><span class="pill" id="resource"></span></header>
```
Replace with:
```html
<header><h1>BiliFlow Control Center</h1><span class="pill" id="worker">Đang tải</span><span class="pill" id="resource"></span><a class="pill" href="/logo-memory" title="Logo hãng phim (giữ) và logo nền tảng (làm mờ) đã nhớ">Bộ nhớ logo</a></header>
```

## HTTP test (lead)

Suggested shape: `tests/test_control_center.py`, or the style of `tests/test_source_cleanup_http.py`. Use a stub `ControlCenter.__new__` on a temporary root and `_handler_class(center)` on port 0.

1. GET `/logo-memory`. Expect 200 `text/html`, with `<title>Bộ nhớ logo`.
2. GET `/api/logo-memory`. Expect 200 JSON `{memory_sha256: null, records: [], backups: []}` on an empty root.
3. POST `/api/logo-memory/delete` without the token. Expect 403 "Phiên Control Center không hợp lệ" (drained body).
4. POST with the token and `expected_sha256: "0"*64` on a root with one record (use `brand_memory.remember_studio_logo`). Expect 409 `code: "memory_changed"`, and the memory bytes are unchanged.
5. POST `/api/jobs/<id>/review/decision` with `{"id": …, "decision": "BLUR", "remember_platform_logo": true}`. Expect `record_review_decision` to be called with `remember_platform_logo=True` (patch it, as the existing decision-route tests do for `remember_studio_logo`).

## Where the pieces live

| Piece | Location |
|---|---|
| Page, API and module | `src/biliflow/logo_memory_admin.py` |
| Tests | `tests/test_logo_memory_admin.py` (14 tests, 1 real-data read-only) |
| Review choice | `review_workflow.record_review_decision(..., remember_platform_logo=True)` |
| Review page button | `data-act="platform"`, posts `remember_platform_logo:true` |
| CLI (dry run by default) | `scripts/platform_logo_convert.py` (`convert` / `seed`) |

- Never run the CLI with `--apply` on the real root before the user agrees.
- Dry-run evidence: `temp/ui-plan/batch4/e2e-4a/dry-runs/`.
