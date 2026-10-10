"""The source accounts of the Control Center (docs/SOURCE_ACCOUNTS_PLAN.md 9.14): their runtime and routes.

``AccountRuntime`` lives inside the downloader's ``DownloadService``. It reads the project root's own account
config; without any source it makes no manager and never touches the session vault. With sources it makes
the root's ``AccountManager`` (the Windows account this process runs as) and its ``LoginCoordinator``, and
the downloader's source registry gets that manager. Lifecycle:

- ``start`` (before the worker dispatches): settle sign-ins a restart interrupted and check the saved sessions
  (``manager.recover``), and delete the temporary browser profiles a killed run left
  (``clear_browser_profiles``). No window is ever opened at start, while a status is read, when a session is
  missing or when a task is retried: only the user's Đăng nhập opens one.
- ``stop``: refuse new sign-ins, cancel the open windows and wait for them (bounded); ``close``: close the
  manager once nothing uses it (the service decides, after the worker stopped).

Routes (PC only: the Control Center handler checks Host and token like every POST, then that the request came
over 127.0.0.1; the phone listener refuses them before reading a body, ``phone_access.PC_ONLY_PATTERNS``):

    POST /api/download-accounts/<source_id>/login          202 {source, login: "STARTED"}
    POST /api/download-accounts/<source_id>/cancel-login   200 {cancelled, source}
    POST /api/download-accounts/<source_id>/disconnect     200 {source}

Only a source id of the config is accepted (anything else: 404 ACCOUNT_UNKNOWN); the body is ignored, so a
request can never bring a sign-in URL, a cookie, a password, a module or a command. Errors: 503
ACCOUNT_NOT_READY (no manager), 409 LOGIN_UNSUPPORTED (no sign-in check for the source's adapter yet; no
window opens), 409 LOGIN_BUSY / ACCOUNT_BUSY, 503 ACCOUNT_STATE_ERROR (the database). The status of every
source is part of ``GET /api/downloads`` (``accounts``, see ``status``).
"""
from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from biliflow.download_account_config import AccountConfig, describe_account_problems, read_account_config
from biliflow.download_account_login import LOGIN_VERIFIERS, LoginCoordinator, LoginOutcome, LoginUnsupported
from biliflow.download_accounts import MESSAGES, AccountError, AccountManager, AccountUnknown

ACCOUNT_ACTION = re.compile(r"/api/download-accounts/([a-z0-9][a-z0-9-]{0,39})/(login|cancel-login|disconnect)")
POST_ROUTES = (ACCOUNT_ACTION.pattern,)
NOT_READY = "Tài khoản nguồn phim chưa sẵn sàng trong bản này"
MAX_HANG_FRAMES = 8

Response = tuple[int, Any]


def owns(path: str) -> bool:
    return path == "/api/download-accounts" or path.startswith("/api/download-accounts/")


def _outcome(outcome: LoginOutcome) -> dict[str, Any]:
    """What the page may show of a finished sign-in: its code and fixed message, never the error's text."""
    hang = outcome.hang
    return {"code": outcome.code, "message": outcome.message, "connected": outcome.connected,
            "profile_left": outcome.profile_left, "failure": outcome.failure,
            "hang": {"phase": hang.phase, "at": list(hang.stack[:MAX_HANG_FRAMES])} if hang is not None else None,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


class AccountRuntime:
    """See the module docstring. ``manager`` and ``coordinator_options`` are the tests' injection (a manager on a
    temporary root with a fake vault, a fake launcher); production passes neither."""

    def __init__(self, root: Path, *, manager: AccountManager | None = None,
                 coordinator_options: Mapping[str, Any] | None = None):
        self.root = root
        self.manager: AccountManager | None = manager
        self.coordinator: LoginCoordinator | None = None
        self.error: str | None = None
        self.recovered: dict[str, Any] = {}
        self.last_login: dict[str, dict[str, Any]] = {}
        self._wake: Callable[[], None] | None = None
        self._forget: Callable[[tuple[str, str], str], Any] | None = None
        self._registry: Any = None
        self._waiting: Callable[[], Mapping[str, int]] | None = None
        self._guard = threading.Lock()
        self.config: AccountConfig = manager.config if manager is not None else read_account_config(root)
        if self.manager is None and self.config.sources:
            try:
                self.manager = AccountManager(root, self.config)
            except Exception as error:  # noqa: BLE001 - the links are then refused with ACCOUNT_NOT_READY
                self.error = f"{NOT_READY} ({type(error).__name__})."
        if self.manager is not None:
            self.coordinator = LoginCoordinator(self.manager, on_finish=self._finished, **(coordinator_options or {}))

    def attach(self, *, wake: Callable[[], None], registry: Any, waiting: Callable[[], Mapping[str, int]],
               forget: Callable[[tuple[str, str], str], Any] | None = None) -> None:
        """The downloader's side: wake its dispatcher after a sign-in, its providers (their warnings), the
        number of tasks waiting for each source's sign-in, and ``forget``: drop the links its tasks keep in memory
        for a source whose session changed (Ngắt kết nối, a new sign-in; download_account_tickets)."""
        self._wake, self._registry, self._waiting, self._forget = wake, registry, waiting, forget

    def _finished(self, outcome: LoginOutcome) -> None:
        """A sign-in ended (the window's thread): keep its outcome and wake the dispatcher, which reads the
        account's row itself before any task goes back to the queue. Nothing here uses the database."""
        with self._guard:
            self.last_login[outcome.source_id] = _outcome(outcome)
        if outcome.code == "CONNECTED":
            self._session_changed(outcome.source_id)
        if self._wake is not None:
            self._wake()

    def _session_changed(self, source_id: str) -> None:
        """Links kept for the source's tasks belong to the session before; the take-time generation check already
        refuses them, this only drops them at once."""
        if self._forget is None or self.manager is None:
            return
        try:
            self._forget(self.manager.owner, source_id)
        except Exception:  # noqa: BLE001 - the generation check at take time still refuses them
            pass

    # Lifecycle -----------------------------------------------------------------------------------------------

    def start(self) -> dict[str, Any]:
        """Before the worker dispatches: settle what a previous run left (no window, no browser run)."""
        if self.manager is None:
            return {}
        try:
            unsettled = self.manager.recover()
            profiles = self.manager.clear_browser_profiles()
        except Exception as error:  # noqa: BLE001 - the downloads page shows it; links still get clear errors
            self.error = f"Không khôi phục được trạng thái tài khoản nguồn ({type(error).__name__})."
            return {}
        self.recovered = {"unsettled": list(unsettled), "profiles_cleared": profiles}
        return self.recovered

    def stop(self, timeout: float = 30.0) -> None:
        """Refuse new sign-ins and close the open windows (bounded)."""
        if self.coordinator is not None:
            self.coordinator.shutdown(timeout)

    def busy(self) -> bool:
        """A sign-in window is still running (its thread may still use the manager)."""
        return self.coordinator is not None and bool(self.coordinator.active())

    def close(self) -> None:
        if self.manager is not None:
            self.manager.close()

    # Status ------------------------------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Every configured source for the downloads page: the manager's public status, whether its sign-in and
        page reader exist, an open window, the last sign-in, warnings and waiting tasks. Read only; it never
        opens a browser or a window."""
        active = set(self.coordinator.active()) if self.coordinator is not None else set()
        try:
            waiting = dict(self._waiting()) if self._waiting is not None else {}
        except sqlite3.Error:
            waiting = {}
        error = self.error
        try:
            statuses = self.manager.statuses() if self.manager is not None else [
                {"id": source.id, "label": source.label, "state": "UNAVAILABLE", "session_check":
                 source.adapter.session_check, "authenticated_at": None, "recheck_at": None, "checked_at": None,
                 "error_code": "ACCOUNT_NOT_READY", "message": self.error or NOT_READY + "."}
                for source in self.config.sources.values()]
        except (sqlite3.Error, AccountError) as failure:
            statuses, error = [], f"Không đọc được trạng thái tài khoản nguồn ({type(failure).__name__})."
        sources = []
        for item in statuses:
            source = self.config.sources[item["id"]]
            provider = self._registry.get(item["id"]) if self._registry is not None else None
            with self._guard:
                last = self.last_login.get(item["id"])
            verifiers = self.coordinator.verifiers if self.coordinator is not None else LOGIN_VERIFIERS
            sources.append({**item, "login_supported": source.adapter.id in verifiers,
                            "reader_supported": getattr(provider, "reader", None) is not None,
                            "login_running": item["id"] in active, "last_login": last,
                            "warnings": list(getattr(provider, "warnings", {}).values()),
                            "waiting_tasks": int(waiting.get(item["id"], 0))})
        return {"sources": sources, "problems": list(self.config.problems),
                "problem_text": describe_account_problems(self.config.problems), "error": error}

    def _source_status(self, source_id: str) -> dict[str, Any] | None:
        return next((item for item in self.status()["sources"] if item["id"] == source_id), None)

    # Actions (PC only) ----------------------------------------------------------------------------------------

    def handle_post(self, source_id: str, action: str) -> Response:
        if self.manager is None or self.coordinator is None:
            reason = self.error or f"{NOT_READY}: chưa có nguồn nào trong config/download_accounts.local.json."
            return 503, {"error": reason, "code": "ACCOUNT_NOT_READY"}
        if source_id not in self.config.sources:
            return 404, {"error": MESSAGES["ACCOUNT_UNKNOWN"], "code": "ACCOUNT_UNKNOWN"}
        try:
            if action == "login":
                self.coordinator.start(source_id)
                return 202, {"source": self._source_status(source_id), "login": "STARTED"}
            if action == "cancel-login":
                cancelled = self.coordinator.cancel(source_id)
                return 200, {"cancelled": cancelled, "source": self._source_status(source_id)}
            if source_id in self.coordinator.active():  # a disconnect also closes that source's open window
                self.coordinator.cancel(source_id)
            try:
                self.manager.disconnect(source_id)
            finally:  # also when removing the files failed after the row changed: drop the links all the same
                self._session_changed(source_id)
            return 200, {"source": self._source_status(source_id)}
        except LoginUnsupported as error:
            return 409, {"error": error.message, "code": error.code}
        except AccountUnknown as error:
            return 404, {"error": error.message, "code": error.code}
        except AccountError as error:  # LOGIN_BUSY, ACCOUNT_BUSY, a session that could not be removed…
            return 409, {"error": error.message, "code": error.code}
        except RuntimeError:
            return 503, {"error": "Control Center đang tắt; không mở đăng nhập mới.", "code": "SHUTTING_DOWN"}
        except sqlite3.Error:
            return 503, {"error": "Không ghi được trạng thái tài khoản nguồn (cơ sở dữ liệu bận); thử lại.",
                         "code": "ACCOUNT_STATE_ERROR"}
