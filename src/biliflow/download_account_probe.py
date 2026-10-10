"""The acceptance probe of a source account (M7; docs/SOURCE_ACCOUNTS_PLAN.md section 9.20): one resolve of one
pasted film page with the session the user signed in on a test root, and never a file transfer.

``probe(root, url, …)`` runs the account provider's own ``resolve`` (download_account_sources: the film list, at
most one ticket of the chosen file, then ``resolve_file``'s probe of its link) exactly once, with:

- a test root only: a folder inside the install's ``temp/`` (never the install root, whose sessions are the
  user's). The root's Control Center lock (``state/control-center.lock``) is held for the whole probe, so it
  never runs beside a Control Center of that root (the source's run lock only covers one process);
- one ticket at most: ``ticket_attempts`` 1 (an expired or refused ticket is never asked for again), and
  ``state/account-probe/ticket-asked.json`` is written right before the run's first click (the session
  browser's ``before_click``; when it cannot be written, nothing is clicked). A killed probe that clicked
  leaves it, one that did not click leaves none. A root with the marker is refused (TICKET_ALREADY_ASKED) unless
  the caller passes ``another_ticket``;
- the downloader's cookie-free SafeHttp behind ``BudgetHttp``: the file's probe through ``ctx.http`` has one
  budget of body bytes (at most 1 MiB) for all its responses. A read never asks the socket for more than is
  left; the response is closed as soon as its reader has enough or the budget is spent (a server that ignores
  Range included); a request after the budget is refused (PROBE_BUDGET). Headers and the hidden browser's
  page traffic (portal and ticket pages, never media) are not counted;
- one deadline for the whole probe (at most 15 minutes; the hidden run keeps its own inside it) and a cancel
  (Ctrl+C on the command line);
- the production page reader of the source's adapter: a reader whose ``reads_tickets`` is False stops before
  any click (TICKET_UNSUPPORTED).

The transfer is never reached: this module never calls ``FileTransfer`` or the worker, and the ``ResolvedSource``
that resolve returns is only described. The report holds codes, counts and header facts: never a link, a ticket,
a cookie, a token, a title, a file name or a host. Bytes "read" are those BiliFlow took from the response; the
system may have buffered a little more in flight before the close.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import signal
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from email.utils import parsedate_tz
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import urlsplit

from biliflow.control_center import SingleInstanceLock
from biliflow.download_account_config import read_account_config
from biliflow.download_account_listing import FileSelection, checked_id
from biliflow.download_account_pages import SourcePageReader
from biliflow.download_account_sources import AccountSourceProvider, SourceNeedsEpisodes
from biliflow.download_accounts import AccountManager
from biliflow.download_http import CHUNK_BYTES, Cancelled, HttpError, SafeHttp
from biliflow.download_page_sources import SourceNeedsChoice
from biliflow.download_provider_config import HostList
from biliflow.download_runner import ProcessControl
from biliflow.download_source_types import ResolveContext, ResolvedSource, SourceError, SourceLoginRequired
from biliflow.recycle_bin import INSTALL_ROOT

PROBE_BUDGET_BYTES = 1024 * 1024  # the media the acceptance may receive in all its probes
PROBE_SECONDS = 330.0  # the whole probe: one hidden run (180 s at most, its grace included) and the file's probe
MAX_PROBE_SECONDS = 900.0
MARKER = Path("state") / "account-probe" / "ticket-asked.json"
WORK = Path("temp") / "account-probe"
_CONTENT_RANGE = re.compile(r"bytes\s+((?:[0-9]{1,20}-[0-9]{1,20}|\*)/(?:[0-9]{1,20}|\*))", re.IGNORECASE)


class ProbeRefused(Exception):
    """The probe did not start (nothing was read): ``code`` says why."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ResponseFacts:
    """What one answer of the probe showed (never its URL, host or a header value that could identify it)."""

    role: str  # the source's host role of the answer ("files", "tickets", "portal") or "other"
    status: int | None
    error: str | None = None  # the HttpError code of a refused answer
    content_type: str = ""
    content_length: int | None = None
    content_range: str | None = None  # "<start>-<end>/<total>", numbers only
    accept_ranges: str = ""
    etag: str | None = None  # "strong" or "weak", never the value
    last_modified: bool = False
    set_cookie: bool = False
    bytes_read: int = 0
    finished: bool = False  # the body ended before its reader stopped
    closed_early: bool = False  # closed before the end of the body the server announced or was sending


class BudgetHttp:
    """The probe's HTTP client: ``http`` (a cookie-free SafeHttp) with one byte budget for every response."""

    def __init__(self, http: SafeHttp, budget: int, role: Callable[[str], str]):
        if not 0 < budget <= PROBE_BUDGET_BYTES:
            raise ValueError(f"budget must be 1 to {PROBE_BUDGET_BYTES} bytes")
        self.http = http
        self.budget = budget
        self.used = 0
        self.role = role
        self.responses: list[ResponseFacts] = []

    @property
    def left(self) -> int:
        return self.budget - self.used

    def open(self, url: str, control: Any, *, headers: Mapping[str, str] | None = None, method: str = "GET",
             body: bytes | None = None) -> _BudgetResponse:
        if self.left <= 0:
            raise HttpError("PROBE_BUDGET", "Đã nhận đủ ngân sách probe; không mở thêm request.")
        try:
            response = self.http.open(url, control, headers=headers, method=method, body=body)
        except HttpError as error:
            if not isinstance(error, Cancelled):
                self.responses.append(ResponseFacts(self.role(url), error.status, error.code))
            raise
        tag = response.header("ETag")
        facts = ResponseFacts(
            role=self.role(response.url), status=response.status,
            content_type=response.header("Content-Type").split(";")[0].strip()[:60],
            content_length=response.content_length, content_range=_numbers(response.header("Content-Range")),
            accept_ranges=response.header("Accept-Ranges")[:20],
            etag=("weak" if tag.startswith("W/") else "strong") if tag else None,
            last_modified=bool(response.header("Last-Modified")), set_cookie=bool(response.header("Set-Cookie")))
        self.responses.append(facts)
        return _BudgetResponse(response, self, facts)


class _BudgetResponse:
    """A response of ``BudgetHttp``: SafeHttp's reads, each asking the socket for no more than is left."""

    def __init__(self, response: Any, owner: BudgetHttp, facts: ResponseFacts):
        self._response, self._owner, self._facts = response, owner, facts
        self.status, self.headers = response.status, response.headers
        self.url, self.host, self.content_length = response.url, response.host, response.content_length
        self._closed = False

    def header(self, name: str) -> str:
        return self._response.header(name)

    def __enter__(self) -> _BudgetResponse:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        facts = self._facts
        facts.closed_early = not facts.finished and (self.content_length is None
                                                     or facts.bytes_read < self.content_length)
        self._response.close()

    def _read(self, want: int) -> bytes:
        """One read of at most ``want`` bytes and never more than the budget left; b"" at the end of either."""
        want = min(want, self._owner.left)
        if want <= 0 or self._closed:
            return b""
        data = next(self._response.chunks(want), b"")
        if not data:
            self._facts.finished = True
            return b""
        self._owner.used += len(data)
        self._facts.bytes_read += len(data)
        return data

    def chunks(self, size: int = CHUNK_BYTES) -> Iterator[bytes]:
        while data := self._read(size):
            yield data
        self.close()  # the end of the body or of the budget: nothing more is read

    def read_some(self, limit: int) -> bytes:
        body = bytearray()
        while len(body) < limit and (data := self._read(min(CHUNK_BYTES, limit - len(body)))):
            body += data
        self.close()  # enough for its reader: the rest is never read
        return bytes(body)


def _numbers(content_range: str) -> str | None:
    """The numbers of a Content-Range ("0-1048575/2010000000", "*/5000"), "bad" for another shape, or None."""
    value = content_range.strip()
    if not value:
        return None
    found = _CONTENT_RANGE.fullmatch(value)
    return found.group(1) if found else "bad"


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def checked_test_root(root: Path) -> Path:
    """``root`` when it is a folder strictly inside the install's temp/ (a test root); else ProbeRefused."""
    try:
        resolved = root.resolve(strict=True)
        temp = (INSTALL_ROOT / "temp").resolve()
    except (OSError, RuntimeError):
        raise ProbeRefused("TEST_ROOT_ONLY", "Root thử không tồn tại.") from None
    if not resolved.is_dir() or resolved == temp or temp not in resolved.parents:
        raise ProbeRefused("TEST_ROOT_ONLY", f"Probe chỉ chạy trên một root thử trong {temp}.")
    return resolved


def probe(root: Path, url: str, *, chosen: FileSelection | None = None, budget: int = PROBE_BUDGET_BYTES,
          seconds: float = PROBE_SECONDS, another_ticket: bool = False, ffprobe: Path | None = None,
          http: SafeHttp | None = None, manager: AccountManager | None = None,
          reader: SourcePageReader | None = None, run_options: Mapping[str, Any] | None = None,
          control: ProcessControl | None = None) -> dict[str, Any]:
    """One resolve of ``url`` on the test ``root`` (module docstring); returns the report. ``chosen``: the file
    to ask a ticket for (its source is the link's). ``http``, ``manager``, ``reader`` and ``run_options`` are the
    tests' injection; the command line passes none."""
    if not (math.isfinite(seconds) and 0 < seconds <= MAX_PROBE_SECONDS):
        raise ValueError(f"seconds must be more than 0 and at most {MAX_PROBE_SECONDS:.0f}")
    root = checked_test_root(root)
    if manager is not None and os.path.normcase(str(manager.root.resolve())) != os.path.normcase(str(root)):
        raise ValueError("The account manager must be the test root's")
    try:
        lock = SingleInstanceLock(root / "state" / "control-center.lock")
    except RuntimeError:
        raise ProbeRefused("CONTROL_CENTER_RUNNING", "Control Center của root thử đang chạy; dừng nó trước khi "
                           "probe.") from None
    own_manager = None
    try:
        if (root / MARKER).exists() and not another_ticket:
            raise ProbeRefused("TICKET_ALREADY_ASKED", "Root thử này đã có một lượt probe đã bấm lấy vé; không "
                               "lấy thêm vé khi chưa được yêu cầu rõ.")
        config = manager.config if manager is not None else read_account_config(root)
        if config.problems:
            raise ProbeRefused("CONFIG_INVALID", f"Config nguồn của root thử có {len(config.problems)} lỗi.")
        source = next((item for item in config.sources.values() if HostList(item.all_hosts).matches(url)), None)
        if source is None:
            raise ProbeRefused("NOT_A_SOURCE", "Link không thuộc nguồn nào trong config của root thử.")
        if manager is None:
            manager = own_manager = AccountManager(root, config)
        clicks: list[str] = []

        def before_click() -> None:  # synchronous, before the browser clicks: a ticket may be asked for from here
            if not clicks:
                _mark(root, "CLICKED")
            clicks.append("click")

        options = dict(run_options or {})
        options["browser_options"] = {**dict(options.get("browser_options") or {}), "before_click": before_click}
        provider = AccountSourceProvider(source, manager, reader=reader, run_options=options, ticket_attempts=1)
        roles = {host: role for role, hosts in source.hosts.items() for host in hosts}
        client = BudgetHttp(http or SafeHttp(), budget, lambda link: roles.get(_host(link), "other"))
        if chosen is not None:
            chosen = replace(chosen, source=source.id)
        report = _run(root, provider, url, chosen, client, seconds, ffprobe, control or ProcessControl())
        report["clicks"] = len(clicks)
        if clicks:
            _mark(root, report["outcome"])
        report["ticket_marker"] = (root / MARKER).exists()
        return report
    finally:
        if own_manager is not None:
            own_manager.close()
        lock.close()


def _run(root: Path, provider: AccountSourceProvider, url: str, chosen: FileSelection | None, client: BudgetHttp,
         seconds: float, ffprobe: Path | None, control: ProcessControl) -> dict[str, Any]:
    (root / WORK).mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="probe-", dir=root / WORK))
    warnings: list[str] = []
    context = ResolveContext(http=client, control=control, task_dir=work, ffprobe=ffprobe,  # type: ignore[arg-type]
                             previous={"account_file": chosen.public()} if chosen is not None else None,
                             notice=lambda code, _message: warnings.append(code))
    timer = threading.Timer(seconds, control.request, ("cancel",))
    timer.daemon = True
    started = time.monotonic()
    timer.start()
    try:
        report = _outcome(provider, url, context)
    finally:
        timer.cancel()
        shutil.rmtree(work, ignore_errors=True)
    elapsed = time.monotonic() - started
    if report["outcome"] == "CANCELLED" and elapsed >= seconds:
        report["outcome"] = "DEADLINE"
    report.update(seconds=round(elapsed, 1), warnings=sorted(set(warnings)),
                  budget={"limit": client.budget, "used": client.used},
                  responses=[asdict(item) for item in client.responses])
    return report


def _mark(root: Path, outcome: str) -> None:
    """The marker of a probe that clicked (a ticket may have been asked for)."""
    marker = root / MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "outcome": outcome}), encoding="utf-8")


def _outcome(provider: AccountSourceProvider, url: str, context: ResolveContext) -> dict[str, Any]:
    try:
        resolved = provider.resolve(url, context)
    except SourceNeedsEpisodes as needs:
        return {"outcome": "NEEDS_EPISODES", "listing": _listing_counts(needs.listing)}
    except SourceNeedsChoice as needs:
        return {"outcome": "NEEDS_CHOICE", "choices": len(needs.choices)}
    except SourceLoginRequired as needs:
        return {"outcome": needs.code, "reason": needs.reason}
    except Cancelled:
        return {"outcome": "CANCELLED"}
    except HttpError as error:
        return {"outcome": error.code, "status": error.status}
    except SourceError as error:
        return {"outcome": error.code}
    return {"outcome": "RESOLVED", "file": _file_facts(resolved)}


def _listing_counts(listing: Mapping[str, Any]) -> dict[str, Any]:
    groups = listing.get("groups") or []
    return {"kind": listing.get("kind"), "complete": listing.get("complete"),
            "reasons": list(listing.get("reasons") or []), "seasons": len(groups),
            "episodes_per_season": [len(group.get("episodes") or []) for group in groups],
            "episodes": listing.get("episode_count"), "files": listing.get("file_count")}


def _file_facts(resolved: ResolvedSource) -> dict[str, Any]:
    plan = resolved.plan
    validator = plan.validator
    if validator is None:
        kind = None
    else:  # download_media_file.other_version: a quoted value, or any value that is not a date, is an entity tag
        kind = "etag" if validator.startswith('"') or parsedate_tz(validator) is None else "last-modified"
    return {"container": plan.container, "total": plan.total, "ranges": plan.ranges, "validator": kind,
            "strict_versions": plan.strict_versions,
            "streams_checked": resolved.video_codec is not None or resolved.audio_codec is not None,
            "video_codec": resolved.video_codec, "audio_codec": resolved.audio_codec,
            "width": resolved.width, "height": resolved.height, "duration_seconds": resolved.duration_seconds}


def _selection(value: str) -> FileSelection:
    """``FILM/EPISODE/VARIANT`` of the command line, each an id as the source gives it (the source is the
    link's)."""
    parts = value.split("/")
    if len(parts) != 3 or any(checked_id(part) != part for part in parts):
        raise argparse.ArgumentTypeError("--file is FILM/EPISODE/VARIANT, each a source id")
    return FileSelection("", *parts)


def _seconds(value: str) -> float:
    seconds = float(value)
    if not (math.isfinite(seconds) and 0 < seconds <= MAX_PROBE_SECONDS):
        raise argparse.ArgumentTypeError(f"--seconds is more than 0 and at most {MAX_PROBE_SECONDS:.0f}")
    return seconds


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Vietnamese messages through a pipe of another code page
    parser = argparse.ArgumentParser(prog="python -m biliflow.download_account_probe",
                                     description="M7 acceptance probe of a source account (a test root only).")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--file", type=_selection, help="FILM/EPISODE/VARIANT of the file to ask a ticket for")
    parser.add_argument("--budget", type=int, default=PROBE_BUDGET_BYTES)
    parser.add_argument("--seconds", type=_seconds, default=PROBE_SECONDS)
    parser.add_argument("--ffprobe", type=Path, default=os.environ.get("BILIFLOW_FFPROBE") or None)
    parser.add_argument("--another-ticket", action="store_true")
    args = parser.parse_args(argv)
    if not 0 < args.budget <= PROBE_BUDGET_BYTES:
        parser.error(f"--budget must be 1 to {PROBE_BUDGET_BYTES}")
    control = ProcessControl()
    previous = signal.signal(signal.SIGINT, lambda *_: control.request("cancel"))  # the run ends as a cancel
    try:
        report = probe(args.root, args.url, chosen=args.file, budget=args.budget, seconds=args.seconds,
                       another_ticket=args.another_ticket, ffprobe=args.ffprobe, control=control)
    except ProbeRefused as refused:
        print(json.dumps({"refused": refused.code, "message": refused.message}, ensure_ascii=False))
        return 2
    finally:
        signal.signal(signal.SIGINT, previous)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["outcome"] == "RESOLVED" else 1


if __name__ == "__main__":
    sys.exit(main())
