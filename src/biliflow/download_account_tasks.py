"""Source accounts in the download queue (docs/SOURCE_ACCOUNTS_PLAN.md 9.14); a mixin of DownloadWorker.

WAITING_LOGIN: a task of an account source waits for the user's sign-in there, without a slot or a thread,
its downloaded part kept. Only ``SourceLoginRequired`` (no session, one past the TTL, one the source refused)
sends it there, never a file server's HTTP 401. The dispatcher checks the session from the account's row
before it takes a slot (``_park_for_login``), and a probe or a download that meets the error moves its task
there too (download_source_steps). A sign-in that lands (the coordinator's callback, and a check every
``LOGIN_RECHECK_SECONDS`` that covers restarts) sends back to the queue only the waiting tasks of that source
and Windows account whose failed session is older than the usable one, keeping their place (``queued_at``),
choice and group. Stopped or cancelled tasks are not waiting any more, so they are never woken. A task added
under another Windows account never uses this account's session: it waits (OTHER_ACCOUNT) for its own.

Episodes: a series page waits in NEEDS_CHOICE of the "episodes" kind with its stored list (download_groups);
``episodes``, ``save_episode_draft`` and ``confirm_episodes`` are the "Chọn tập" / "Tải N tập" actions. A
group's actions use the per-task actions on its tasks and handle the members not created yet first. Dừng,
Tiếp tục and Hủy nhóm are stored before any episode task is touched (download_groups); ``_settle_groups`` applies
what an interrupted action left, before every dispatch and after recovery, so a cancelled group never starts,
wakes or retries an episode (its unfinished episodes are cancelled; finished files stay in input).
Nothing here opens a sign-in window, runs a browser or asks for a ticket.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Mapping

from biliflow.download_account_tickets import TicketCache
from biliflow.download_accounts import MESSAGES as ACCOUNT_MESSAGES
from biliflow.download_groups import (
    GROUP_CANCELLED,
    INTENT_RESUME,
    INTENT_STOP,
    MAX_GROUP_EPISODES,
    DownloadGroups,
    GroupError,
    check_selection,
    listing_from_public,
    parse_selection,
    plan_summary,
)
from biliflow.download_links import DownloadBatchError
from biliflow.download_source_types import SourceLoginRequired
from biliflow.download_store import FINAL_STATES

LOGIN_RECHECK_SECONDS = 30.0
GROUP_WAIT_SECONDS = 20.0  # one wait for every running task a group action stopped or cancelled
GROUP_RETRYABLE = frozenset({"FAILED", "EXPIRED"})  # "Thử lại" of a group: the episodes that cannot continue
GROUP_ACTIONS = ("stop", "resume", "cancel", "retry", "remove")
INTENT_ACTIONS = {INTENT_STOP: "stop", INTENT_RESUME: "resume"}
GROUP_CANCELLED_MESSAGE = "Nhóm này đã hủy; dán lại trang phim để chọn tập."
LOGIN_HINT = "Bấm Đăng nhập ở trang Tải video trên PC; BiliFlow không tự mở cửa sổ đăng nhập."
OTHER_ACCOUNT = ("Lượt này được thêm khi BiliFlow chạy bằng tài khoản Windows khác; chỉ tài khoản đó dùng được "
                 "phiên đăng nhập của nó.")


def login_message(label: str, reason: str) -> str:
    """The waiting task's message: which source, why, and what the user can do (never a link or a cookie)."""
    if reason == "OTHER_ACCOUNT":
        return f"Chờ tài khoản Windows đã thêm lượt này ({label}): {OTHER_ACCOUNT}"
    why = ACCOUNT_MESSAGES.get(reason) or "Cần đăng nhập nguồn này."
    return f"Chờ đăng nhập {label}: {why} {LOGIN_HINT}"


class DownloadAccountSteps:
    """Uses the worker's store, sources, lock, controls, threads, wake events and step helpers; ``groups`` and the
    login wake state come from ``_init_accounts``."""

    def _init_accounts(self) -> None:
        self.groups = DownloadGroups(self.store)
        self._login_wake = threading.Event()
        self._login_check_at = 0.0
        self.tickets = TicketCache()  # source accounts' signed links, in memory only (download_account_tickets)

    def _drop_stale_tickets(self) -> None:
        """Kept links idle past their limit, and links of a session that is no longer the source's usable one
        (expired, rejected, replaced, or a source that left the config), leave memory at the next dispatch pass,
        not only at their task's next run (download_account_tickets; ``take`` refuses them anyway)."""
        for owner, source_id in self.tickets.held():
            provider = self.sources.get(source_id)
            manager = getattr(provider, "manager", None)
            usable = (provider.usable_generation() if manager is not None and manager.owner == owner
                      and callable(getattr(provider, "usable_generation", None)) else None)
            self.tickets.drop_stale(owner, source_id, usable)

    def forget_source_tickets(self, owner: tuple[str, str], source_id: str) -> int:
        """A session change of a source (Ngắt kết nối, a new sign-in; AccountRuntime calls it): every link kept for
        its tasks goes, and a resolve that began before is never kept."""
        return self.tickets.revoke_source(owner, source_id)

    # ------------------------------------------------------------- pasting links
    def _account_provider(self, url: str) -> Any:
        provider = self.sources.provider_for(url)
        return provider if callable(getattr(provider, "login_gate", None)) else None

    def _account_owners(self, urls: list[str]) -> list[str | None]:
        """The Windows account of each link an account source claims; a link of a source the account config left
        out is refused here with its reason (ACCOUNT_SOURCE_INVALID), unless a public provider keeps its host."""
        errors = []
        for line, url in enumerate(urls, start=1):
            dropped = self.sources.dropped_account(url)
            if dropped is not None and not dropped[1]:
                errors.append({"line": line, "url": url[:200], "code": "ACCOUNT_SOURCE_INVALID",
                               "message": ("Link của nguồn tài khoản đang bị bỏ qua vì cấu hình sai, nên BiliFlow "
                                           f"không tải bằng cách khác. {dropped[0]}")[:500]})
        if errors:
            raise DownloadBatchError("Lô bị từ chối: có link của nguồn tài khoản đang bị bỏ qua.", errors)
        owners = []
        for url in urls:
            provider = self._account_provider(url)
            owners.append(provider.account_sid if provider is not None else None)
        return owners

    def _note_ignored_account(self, task: dict[str, Any]) -> None:
        dropped = self.sources.dropped_account(task["url"])
        if dropped is not None:
            self._event(task, "ACCOUNT_SOURCE_IGNORED", (f"{dropped[0]} Link này đi đường thường của bộ đọc công khai, "
                        "không dùng phiên đăng nhập.")[:500], level="WARNING")

    def _orphan_account_task(self, task: dict[str, Any]) -> bool:
        """A task that belonged to an account source no provider claims any more (the source left the config): it
        ends FAILED instead of going to yt-dlp. True when it was ended."""
        probe = task.get("probe") or {}
        if not (task.get("account_owner") or task.get("member_id") or probe.get("account_file")):
            return False
        self._fail(task["id"], {"PROBING"}, "ACCOUNT_SOURCE_GONE", "Nguồn tài khoản của link này không còn trong "
                   "cấu hình; BiliFlow không tải link này bằng cách khác.")
        return True

    # ------------------------------------------------------ waiting for a sign-in
    def wake_logins(self) -> None:
        """A sign-in ended (the coordinator's callback, any thread): check the waiting tasks at the next pass."""
        self._login_wake.set()
        self._wake.set()

    def _account_upkeep(self) -> None:
        """Each dispatch pass (the caller holds the worker lock): group members become tasks while there is room,
        and the tasks whose sign-in landed go back to the queue. An error here never stops the dispatch."""
        try:
            for task in self.groups.fill():
                self._event(task, "QUEUED", f"Đã thêm từ nhóm tập #{task['group_id']}.")
            self.groups.drop_left_previews()
        except Exception as error:  # noqa: BLE001 - the members stay PENDING; the next pass tries again
            self._note_error(f"Nhóm tập: {type(error).__name__}: {error}")
        try:
            self._drop_stale_tickets()
        except Exception as error:  # noqa: BLE001 - such links can never be taken; the next pass tries again
            self._note_error(f"Link tải đã giữ: {type(error).__name__}")
        now = time.monotonic()
        if self._login_wake.is_set() or now >= self._login_check_at:
            self._login_wake.clear()
            self._login_check_at = now + LOGIN_RECHECK_SECONDS
            try:
                self._resume_waiting_logins()
            except Exception as error:  # noqa: BLE001 - they keep waiting; the next check tries again
                self._note_error(f"Chờ đăng nhập: {type(error).__name__}: {error}")

    def _resume_waiting_logins(self) -> list[int]:
        generations: dict[str, int | None] = {}
        woken = []
        cancelled = self.groups.cancelled_group_ids()
        for task in self.store.tasks_in({"WAITING_LOGIN"}):
            if task.get("group_id") in cancelled:
                continue  # Hủy nhóm reaches it (_settle_groups); a sign-in never sends it back to the queue
            source_id = task.get("login_source")
            provider = self.sources.get(source_id)
            if not callable(getattr(provider, "usable_generation", None)):  # the source left the config
                self._fail(task["id"], {"WAITING_LOGIN"}, "ACCOUNT_SOURCE_GONE", "Nguồn tài khoản của lượt này "
                           "không còn trong cấu hình; không đăng nhập được nữa. Phần đã tải được giữ đến khi Xóa.")
                continue
            if task.get("account_owner") != provider.account_sid:
                continue  # another Windows account's task never uses this account's session
            if source_id not in generations:
                generations[source_id] = provider.usable_generation()
            generation, failed = generations[source_id], task.get("login_generation")
            if generation is None or (failed is not None and generation <= failed):
                continue
            moved = self.store.transition(task["id"], {"WAITING_LOGIN"}, "QUEUED", error_code=None,
                                          error_message=None, login_source=None, login_generation=None,
                                          login_reason=None)
            if moved:
                woken.append(moved["id"])
                self._event(moved, "LOGIN_RESUMED", "Đã có phiên đăng nhập của nguồn; lượt tải trở lại hàng đợi "
                            "(giữ nguyên thứ tự và lựa chọn).")
        return woken

    def _wait_login(self, task: dict[str, Any], allowed: set[str] | frozenset[str], source_id: str,
                    generation: int | None, reason: str, label: str) -> bool:
        message = login_message(label, reason)
        self.tickets.revoke(task["id"])  # a link is never carried across a sign-in wait
        moved = self.store.transition(task["id"], allowed, "WAITING_LOGIN", error_code="SOURCE_LOGIN_REQUIRED",
                                      error_message=message, login_source=source_id, login_generation=generation,
                                      login_reason=reason, speed=None, eta=None, pid=None, pid_created=None)
        if moved:
            self._event(moved, "WAITING_LOGIN", message, level="WARNING", payload={"source": source_id,
                                                                                 "reason": reason})
        return moved is not None

    def _login_needed(self, task: dict[str, Any], allowed: set[str], provider: Any,
                      needed: SourceLoginRequired) -> None:
        """A probe or a download met ``SourceLoginRequired``: the task waits; its part stays."""
        self._wait_login(task, allowed, needed.source_id, needed.generation, needed.reason,
                         getattr(provider, "label", needed.source_id))
        return None

    def _park_for_login(self, task: dict[str, Any]) -> bool:
        """Before a slot is taken: a task of an account source whose session cannot be used now, or that another
        Windows account added, waits in WAITING_LOGIN instead (no slot, no browser). True when it was parked."""
        try:
            provider = self._account_provider(task["url"])
            owner = provider.account_sid if provider is not None else None
            if owner is None:
                return False  # not an account link, or no manager: the probe says why
            if task.get("account_owner") is None:
                self.store.update_fields_if(task["id"], {"QUEUED"}, account_owner=owner)
            elif task["account_owner"] != owner:
                return self._wait_login(task, {"QUEUED"}, provider.id, None, "OTHER_ACCOUNT", provider.label)
            if self._finished_file(task) is not None:
                return False  # only its check and its move are left: no ticket, no session needed
            needed = provider.login_gate(task["url"])
        except Exception as error:  # noqa: BLE001 - the probe meets the same problem and reports it
            self._note_error(f"Lượt {task['id']}: {type(error).__name__}: {error}")
            return False
        if needed is None:
            return False
        return self._wait_login(task, {"QUEUED"}, needed.source_id, needed.generation, needed.reason, provider.label)

    # ------------------------------------------------------------ choosing episodes
    def _needs_episodes(self, task: dict[str, Any], provider: Any, listing: Mapping[str, Any]) -> None:
        """A series page: its public list is kept and the task waits for "Tải N tập" (its slot is freed)."""
        task_id = task["id"]
        try:
            self.groups.save_preview(task_id, provider.id, listing)
        except GroupError as error:
            return self._fail(task_id, {"PROBING"}, error.code, error.message)
        count = listing.get("episode_count") or 0
        message = f"Phim nhiều tập ({count} tập): chọn Tải tất cả hoặc Chọn tập rồi bấm Tải N tập."
        if not listing.get("complete"):
            message += f" Danh sách chưa đầy đủ: {listing.get('message') or 'chưa đọc hết'}"
        page = {"provider": provider.id, "source_label": provider.label, "choice_kind": "episodes", "ready": False,
                "page_title": str(listing.get("title") or "")[:300], "fingerprint": listing.get("fingerprint")}
        moved = self.store.transition(task_id, {"PROBING"}, "NEEDS_CHOICE", entries=None, probe=page,
                                      original_title=str(listing.get("title") or "")[:300] or None,
                                      error_message=message)
        if moved:
            self._event(moved, "NEEDS_CHOICE", message)
        return None

    def _waiting_preview(self, task_id: int) -> tuple[dict[str, Any], Any]:
        task = self._require(task_id)
        preview = self.groups.preview(task_id)
        if task["state"] != "NEEDS_CHOICE" or (task.get("probe") or {}).get("choice_kind") != "episodes" \
                or preview is None:
            group = self.groups.group_of_parent(task_id)
            raise GroupError("NOT_WAITING", "Lượt này không chờ chọn tập.", 409,
                             {"group_id": group["id"] if group else None, "state": task["state"]})
        return preview, listing_from_public(preview["listing"])

    @staticmethod
    def _plan_or_error(listing: Any, selection: Mapping[str, Any] | None) -> tuple[Any, Any]:
        if not selection:
            return None, None
        try:
            return plan_summary(listing, check_selection(listing, selection)), None
        except GroupError as error:
            return None, error.public()

    def episodes(self, task_id: int) -> dict[str, Any]:
        """The stored list of a series page, the draft and what the draft would start (no browser, no ticket)."""
        preview, listing = self._waiting_preview(task_id)
        plan, problem = self._plan_or_error(listing, preview["draft"])
        return {"task_id": task_id, "listing": preview["listing"], "fingerprint": preview["fingerprint"],
                "draft": preview["draft"], "revision": preview["revision"], "plan": plan, "plan_error": problem,
                "max_episodes": MAX_GROUP_EPISODES}

    def save_episode_draft(self, task_id: int, body: Mapping[str, Any]) -> dict[str, Any]:
        """Keep the user's unconfirmed choice (ids of the stored list only); it survives refresh and restart."""
        selection = parse_selection(body.get("selection"))
        _preview, listing = self._waiting_preview(task_id)
        plan, problem = self._plan_or_error(listing, selection)
        if problem is not None and problem.get("unknown"):
            raise GroupError("BAD_SELECTION", problem["error"], detail={"unknown": problem["unknown"]})
        revision = self.groups.save_draft(task_id, selection, body.get("fingerprint"), body.get("revision"))
        return {"task_id": task_id, "revision": revision, "draft": selection, "plan": plan, "plan_error": problem}

    def confirm_episodes(self, task_id: int, body: Mapping[str, Any]) -> dict[str, Any]:
        """"Tải N tập": the group and its members in one transaction, the page task EXPANDED, then the first
        episode tasks while the list has room. A repeat of the same request returns the same group."""
        selection = parse_selection(body.get("selection"))
        self._require(task_id)
        with self._lock:
            created = self.groups.create_group(
                task_id, selection=selection, fingerprint=body.get("fingerprint"),
                request_key=body.get("idempotency_key"), confirm_scope=body.get("confirm_scope") is True,
                skip_existing=body.get("skip_existing") is True)
            group_id = int(created.group["id"])
            if not created.replay:
                parent = self.store.get(task_id)
                if parent is not None:
                    note = f" ({len(created.existing)} tập đã có trong danh sách được bỏ qua)" if created.existing else ""
                    self._event(parent, "EXPANDED", f"Đã tách thành nhóm tập #{group_id}: "
                                f"{created.group['total']} tập{note}.")
                self._account_upkeep()
        self._wake.set()
        return {"group": self.groups.summary(group_id), "replay": created.replay, "existing": created.existing}

    # ----------------------------------------------------------------------- groups
    def _require_group(self, group_id: int) -> dict[str, Any]:
        group = self.groups.group(group_id)
        if group is None:
            raise GroupError("NOT_FOUND", "Không thấy nhóm tập.", 404)
        return group

    def group_detail(self, group_id: int) -> dict[str, Any]:
        self._require_group(group_id)
        return {"group": self.groups.summary(group_id), "members": self.groups.members(group_id)}

    def _member_probe(self, task: Mapping[str, Any]) -> dict[str, Any] | None:
        """The probe an episode task starts from (its chosen file), for "Thử lại" (never lost with the reset)."""
        member = self.groups.member(task.get("member_id"))
        if member is None:
            return None
        return {"provider": member["source_id"], "source_label": member["source_label"], "ready": False,
                "account_file": json.loads(member["selection_json"])}

    @staticmethod
    def _join(threads: list[threading.Thread]) -> None:
        deadline = time.monotonic() + GROUP_WAIT_SECONDS
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(max(0.0, deadline - time.monotonic()))

    def group_action(self, group_id: int, action: str) -> dict[str, Any]:
        """Dừng / Tiếp tục / Hủy / Thử lại / Xóa of a whole group (see the module docstring)."""
        from biliflow.download_worker import RESUMABLE, STOPPABLE

        if action not in GROUP_ACTIONS:
            raise GroupError("BAD_ACTION", "Thao tác nhóm không hợp lệ.")
        threads: list[threading.Thread] = []
        with self._lock:
            group = self._require_group(group_id)
            if action == "remove":
                return self._remove_group(group)
            if action in ("stop", "resume", "retry") and group["state"] == GROUP_CANCELLED:
                raise GroupError("GROUP_CANCELLED", GROUP_CANCELLED_MESSAGE, 409)
            # First the stored intent, with the members not created yet (one transaction): an interruption from
            # here on is finished by _settle_groups, never lost.
            if action == "stop":
                self.groups.hold(group_id, STOPPABLE)
            elif action == "resume":
                self.groups.release(group_id, RESUMABLE)
            elif action == "cancel":
                self.groups.cancel(group_id)
            for task in self.groups.tasks_of(group_id):
                thread = self._threads.get(task["id"])
                if self._group_task_action(task, action) and thread is not None:
                    threads.append(thread)
            if action in ("stop", "resume", "cancel"):
                self.groups.clear_intents(group_id)  # every episode had its turn (Thử lại stores none)
        self._join(threads)
        self._wake.set()
        return {"group": self.groups.summary(group_id)}

    def _group_task_action(self, task: dict[str, Any], action: str) -> bool:
        """The per-task action on one episode of a group; True when it was asked of a running task. Hủy nhóm
        cancels every episode the per-task Hủy can (queued, waiting, running, stopped, interrupted or failed);
        a COMPLETED file stays in input and a PUBLISHING one finishes first (_settle_groups cancels it if its
        publish fails); a CANCELLING one keeps its own clean-up and retries."""
        from biliflow.download_worker import CANCELLABLE, RESUMABLE, STOPPABLE, DownloadActionError

        state, task_id = task["state"], task["id"]
        try:
            if action == "stop" and state in STOPPABLE:
                self.stop(task_id, wait=False)
                return task_id in self._controls
            if action == "resume" and state in RESUMABLE:
                self.resume(task_id)
            elif action == "cancel" and state in CANCELLABLE and state != "CANCELLING":
                self.cancel(task_id, wait=False)
                return task_id in self._controls
            elif action == "retry" and state in GROUP_RETRYABLE:
                self.retry(task_id)  # keeps the 100 cap for a closed task (EXPIRED)
        except DownloadActionError as error:  # one episode that cannot follow never stops the others
            self._event(task, "GROUP_ACTION_SKIPPED", f"Thao tác nhóm bỏ qua tập này: {error}", level="WARNING")
        return False

    def _settle_groups(self, group_id: int | None = None) -> None:
        """Apply the stored group intents an action did not finish (an error, a crash, a restart): every episode
        of a cancelled group the per-task Hủy can reach is cancelled, and a stored Dừng / Tiếp tục reaches its
        episode. Runs under the worker lock before any dispatch or sign-in wake and after recovery; idempotent.
        A CANCELLING episode keeps the clean-up retry of _reconcile; one still running is asked to cancel once.
        Nothing here starts a task, opens a browser or asks for a ticket."""
        from biliflow.download_worker import CANCELLABLE

        with self._lock:
            try:
                cancelled, intents = self.groups.cancelled_tasks(CANCELLABLE, group_id), self.groups.intents(group_id)
            except Exception as error:  # noqa: BLE001 - other tasks still dispatch; cancelled groups stay skipped
                self._note_error(f"Nhóm tập: {type(error).__name__}: {error}")
                return
            for task in cancelled:
                try:
                    self._cancel_in_group(task)
                except Exception as error:  # noqa: BLE001 - the next pass tries again; the dispatcher skips it
                    self._note_error(f"Lượt {task['id']}: {type(error).__name__}: {error}")
            for member_id, task_id, intent in intents:
                task = self.store.get(task_id)
                try:
                    if task is not None and intent in INTENT_ACTIONS:
                        self._group_task_action(task, INTENT_ACTIONS[intent])
                    self.groups.clear_intent(member_id, intent)
                except Exception as error:  # noqa: BLE001 - the intent stays for the next pass
                    self._note_error(f"Lượt {task_id}: {type(error).__name__}: {error}")

    def _cancel_in_group(self, task: dict[str, Any]) -> None:
        if task["state"] == "CANCELLING":
            control = self._controls.get(task["id"])
            if control is not None and control.reason != "cancel":
                control.request("cancel")
            return
        self._group_task_action(task, "cancel")

    def _refuse_in_cancelled_group(self, task: Mapping[str, Any]) -> None:
        """Tiếp tục / Thử lại of one episode of a cancelled group: refused (Hủy nhóm would cancel it again, and the
        group is never reopened); the user pastes the film page again to choose episodes."""
        from biliflow.download_worker import DownloadActionError

        if self.groups.is_cancelled(task.get("group_id")):
            raise DownloadActionError(f"Tập này thuộc nhóm tập #{task['group_id']} đã hủy nên không tải tiếp được; "
                                      "dán lại trang phim rồi chọn tập để tải lại.", 409)

    def _clear_member_intent(self, task: Mapping[str, Any]) -> None:
        """An episode's own action replaces a stored group Dừng / Tiếp tục it has not met yet."""
        if task.get("member_id") is not None:
            self.groups.clear_intent(int(task["member_id"]))

    def _remove_group(self, group: dict[str, Any]) -> dict[str, Any]:
        """Xóa nhóm (the caller holds the lock): only when nothing waits or runs; files in input stay. The temp
        folders of its episodes and of its page go with their rows."""
        from biliflow.download_worker import DownloadActionError

        summary = self.groups.summary(int(group["id"]))
        tasks = self.groups.tasks_of(int(group["id"]))
        if summary is None or not summary["finished"] or any(task["state"] not in FINAL_STATES for task in tasks):
            raise DownloadActionError("Nhóm còn tập chưa tải xong hoặc đang chờ; bấm Hủy nhóm trước (tập đã tải "
                                      "xong vẫn ở trong input).")
        freed = 0
        for task in tasks:
            freed += int(self.remove(task["id"]).get("freed_bytes") or 0)
        parent = group.get("parent_task_id")
        page = self.store.get(int(parent)) if parent is not None else None
        if page is not None and page["state"] == "EXPANDED":  # the page's own temp folder (made by its probe)
            freed += self._remove_temp(page)
            if not self._temp_gone(page["id"]):  # the rows stay with the folder; Xóa nhóm tries again later
                raise DownloadActionError("Chưa xóa được file tạm của trang phim (file đang bị giữ); thử lại sau.")
        self.groups.delete_group(int(group["id"]), int(parent) if parent is not None else None)
        return {"group_id": int(group["id"]), "removed": True, "freed_bytes": freed}
