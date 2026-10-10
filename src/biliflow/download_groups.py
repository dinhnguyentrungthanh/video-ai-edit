"""Episode previews and episode groups of a source account (docs/SOURCE_ACCOUNTS_PLAN.md 9.14).

A pasted film page whose source lists several episodes ends its probe in NEEDS_CHOICE of the "episodes" kind
(download_account_tasks). Its public list (``Listing.public()``: ids, labels, numbers, order and sizes; never a
page link, a ticket, a signed URL or a session) is kept in ``download_previews`` with the user's draft, so a
refresh or a restart loses nothing.

"Tải N tập" (``create_group``) checks the request against that stored list and, in one transaction, writes the
group and every chosen episode as a member in list order, then closes the page task as EXPANDED. Members become
download tasks only while the list has room under ``MAX_UNFINISHED_TASKS`` (``fill``, called by the dispatcher):
one task per episode file, in group order; the rest waits as PENDING (no slot, no ticket) and survives restarts.

Duplicates are refused at every level: the request key (a repeated or concurrent "Tải N tập"), one group per
page task, one member per episode file in a group, one task per member (a unique index), and an episode file
already in an open task or waiting in another group (``ITEMS_EXIST``, listed, never dropped silently).

``DownloadGroups`` shares the store's connection and lock (one writer of ``state/downloads.sqlite3``).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from biliflow.download_account_listing import (
    MAX_EPISODES,
    MODES,
    REASONS,
    SPECIALS_KEY,
    Episode,
    FileSelection,
    Listing,
    Season,
    SelectionPlan,
    Variant,
    checked_id,
    plan_selection,
)
from biliflow.download_episode_names import episode_code, ordinal_width, planned_stem
from biliflow.download_store import MAX_UNFINISHED_TASKS, RELEASED_LINK_STATES, SLOT_STATES, DownloadStore

MAX_GROUP_EPISODES = 500
MAX_KIND_CHARS = 200
MAX_PREVIEW_BYTES = 4 * 1024 * 1024  # a stored list is bounded by the listing's own limits; this is a backstop
REQUEST_KEY = re.compile(r"[A-Za-z0-9_-]{8,64}")
PENDING, HELD, CREATED, CANCELLED = "PENDING", "HELD", "CREATED", "CANCELLED"
GROUP_ACTIVE, GROUP_CANCELLED = "ACTIVE", "CANCELLED"
INTENT_STOP, INTENT_RESUME = "STOP", "RESUME"
_IN_CHUNK = 400
_SELECTION_KEYS = frozenset({"mode", "episodes", "variant_kind", "variants"})


class GroupError(Exception):
    """A refused preview, draft or group request: ``code``, a Vietnamese ``message``, the HTTP ``status`` and a
    bounded, JSON-safe ``detail`` (ids and labels of the stored list only)."""

    def __init__(self, code: str, message: str, status: int = 400, detail: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.detail = dict(detail or {})

    def public(self) -> dict[str, Any]:
        return {"error": self.message, "code": self.code, **self.detail}


@dataclass(frozen=True)
class Created:
    """What ``create_group`` did: the group row, whether this was a replay of the same request, and the chosen
    episodes left out because they were already in the list (``skip_existing``)."""
    group: dict[str, Any]
    replay: bool = False
    existing: list[dict[str, Any]] = field(default_factory=list)


# The stored list -----------------------------------------------------------------------------------------------

def listing_from_public(public: Mapping[str, Any]) -> Listing:
    """The ``Listing`` behind a stored ``Listing.public()`` (no navigation: it can plan, never fetch). Its
    fingerprint must come out the same, so a stored list that was changed is refused (BAD_PREVIEW)."""
    try:
        source, film = checked_id(public["source"]), checked_id(public["film"])
        kind = public["kind"]
        if source is None or film is None or kind not in ("film", "series"):
            raise ValueError("ids")
        seasons: list[Season] = []
        episodes: list[Episode] = []
        for group in public["groups"]:
            key = group["key"]
            if not (key == "" or key == SPECIALS_KEY or checked_id(key) == key):
                raise ValueError("season")
            seasons.append(Season(key, _number(group["number"]), str(group["label"]), bool(group["special"])))
            for item in group["episodes"]:
                variants = tuple(_variant(value) for value in item["variants"])
                if checked_id(item["key"]) != item["key"] or not variants:
                    raise ValueError("episode")
                episodes.append(Episode(item["key"], key, _number(item["number"]), str(item["label"]),
                                        int(item["order"]), bool(item["special"]), None, variants))
        episodes.sort(key=lambda episode: episode.position)
        listing = Listing(source, film, str(public["title"]), kind, tuple(seasons), tuple(episodes),
                          bool(public["complete"]), tuple(str(reason) for reason in public["reasons"]))
    except (KeyError, TypeError, ValueError, AttributeError):
        raise GroupError("BAD_PREVIEW", "Danh sách tập đã lưu không đọc được; bấm Hủy rồi dán lại trang phim.",
                         409) from None
    if listing.public()["fingerprint"] != public.get("fingerprint"):
        raise GroupError("BAD_PREVIEW", "Danh sách tập đã lưu không khớp dấu của nó; bấm Hủy rồi dán lại trang phim.",
                         409)
    return listing


def _number(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("number")
    return value


def _variant(value: Mapping[str, Any]) -> Variant:
    variant_id = checked_id(value["id"])
    if variant_id != value["id"]:
        raise ValueError("variant")
    size = value.get("size")
    variant = Variant(variant_id, str(value["label"]), value.get("quality"), value.get("audio"),
                      size if isinstance(size, int) and not isinstance(size, bool) else None)
    if variant.kind != value.get("kind"):
        raise ValueError("kind")
    return variant


# The user's choice ---------------------------------------------------------------------------------------------

def parse_selection(value: Any) -> dict[str, Any]:
    """The shape of a choice (``mode`` all/pick, ``episodes``, ``variant_kind`` or ``variants``); ids are checked
    against the stored list by ``check_selection``."""
    def bad(why: str) -> GroupError:
        return GroupError("BAD_SELECTION", f"Lựa chọn tập không hợp lệ: {why}.")

    if not isinstance(value, Mapping) or set(value) - _SELECTION_KEYS:
        raise bad("cần mode, episodes và variant_kind hoặc variants")
    mode = value.get("mode")
    if mode not in MODES:
        raise bad('mode phải là "all" hoặc "pick"')
    episodes = value.get("episodes", [])
    if (not isinstance(episodes, list) or len(episodes) > MAX_EPISODES
            or any(not isinstance(item, str) or checked_id(item) != item for item in episodes)
            or len(set(episodes)) != len(episodes)):
        raise bad("episodes phải là danh sách mã tập không trùng")
    if mode == "all" and episodes:
        raise bad('mode "all" không kèm danh sách tập')
    kind, variants = value.get("variant_kind"), value.get("variants")
    if kind is not None and (not isinstance(kind, str) or not kind or len(kind) > MAX_KIND_CHARS):
        raise bad("variant_kind không hợp lệ")
    if variants is not None and (
            not isinstance(variants, Mapping) or len(variants) > MAX_EPISODES
            or any(not isinstance(key, str) or checked_id(key) != key or not isinstance(item, str)
                   or checked_id(item) != item for key, item in variants.items())):
        raise bad("variants phải là {mã tập: mã bản}")
    if kind is not None and variants is not None:
        raise bad("chỉ chọn variant_kind hoặc variants")
    return {"mode": mode, "episodes": list(episodes), "variant_kind": kind,
            "variants": dict(variants) if variants is not None else None}


def check_selection(listing: Listing, selection: Mapping[str, Any]) -> SelectionPlan:
    """The plan of a parsed choice; every id must belong to the stored list (BAD_SELECTION otherwise)."""
    known = {episode.key: episode for episode in listing.episodes}
    unknown = [key for key in selection["episodes"] if key not in known]
    variants = selection["variants"]
    if variants is not None:
        unknown += [key for key, variant in variants.items()
                    if key not in known or known[key].variant(variant) is None]
    kind = selection["variant_kind"]
    if kind is not None and not any(variant.kind == kind for episode in listing.episodes
                                    for variant in episode.variants):
        unknown.append(kind[:40])
    if unknown:
        raise GroupError("BAD_SELECTION", "Có mã tập hoặc mã bản không thuộc danh sách đã lưu.",
                         detail={"unknown": unknown[:20]})
    try:
        return plan_selection(listing, mode=selection["mode"], episodes=selection["episodes"],
                              variant_kind=kind, variants=variants)
    except ValueError as error:
        message = {"Choose a variant": "Chọn một bản (chất lượng, âm thanh) cho các tập.",
                   "Pick at least one episode of this list": "Chọn ít nhất một tập."}.get(str(error), str(error))
        raise GroupError("BAD_SELECTION", message) from None


def plan_summary(listing: Listing, plan: SelectionPlan) -> dict[str, Any]:
    """What "Tải N tập" would start, for the page: counts, the label, the scope note, missing/ambiguous."""
    labels = {episode.key: episode.label for episode in listing.episodes}
    return {"mode": plan.mode, "count": plan.count, "complete": plan.complete, "confirm_label": plan.confirm_label,
            "note": plan.note or None, "max": MAX_GROUP_EPISODES, "too_large": plan.count > MAX_GROUP_EPISODES,
            "missing": [{"episode": key, "label": labels.get(key, key)} for key in plan.missing],
            "ambiguous": [{"episode": key, "label": labels.get(key, key)} for key in plan.ambiguous]}


def request_hash(parent_id: int, selection: Mapping[str, Any], fingerprint: str, skip_existing: bool) -> str:
    body = {"parent": parent_id, "selection": selection, "fingerprint": fingerprint, "skip": bool(skip_existing)}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _chunks(values: Sequence[Any]) -> Iterable[Sequence[Any]]:
    for start in range(0, len(values), _IN_CHUNK):
        yield values[start:start + _IN_CHUNK]


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class DownloadGroups:
    """Previews and groups on the store's own connection and lock (see the module docstring)."""

    def __init__(self, store: DownloadStore):
        self.store = store

    @property
    def _db(self) -> sqlite3.Connection:
        return self.store._connection  # noqa: SLF001 - one SQLite writer for the downloader's tables

    @property
    def _lock(self) -> Any:
        return self.store._lock  # noqa: SLF001

    def _now(self) -> str:
        return self.store._now()  # noqa: SLF001

    # Previews --------------------------------------------------------------------------------------------

    def save_preview(self, task_id: int, source_id: str, listing: Mapping[str, Any]) -> None:
        """Keep the public list of a page task; a draft made for the same list (same fingerprint) is kept. The
        few fields the snapshot shows are kept apart, so a poll never reads the list itself."""
        text = json.dumps(listing, ensure_ascii=False)
        if len(text.encode("utf-8")) > MAX_PREVIEW_BYTES:
            raise GroupError("PREVIEW_TOO_LARGE", "Danh sách tập quá lớn để lưu.", 413)
        summary = json.dumps(_preview_summary(listing), ensure_ascii=False)
        fingerprint = str(listing.get("fingerprint") or "")
        now = self._now()
        with self._lock, self._db:
            old = self._db.execute("SELECT fingerprint, draft_json, revision FROM download_previews WHERE task_id = ?",
                                   (task_id,)).fetchone()
            draft = old["draft_json"] if old is not None and old["fingerprint"] == fingerprint else None
            revision = int(old["revision"]) + 1 if old is not None else 0
            self._db.execute(
                "INSERT OR REPLACE INTO download_previews (task_id, source_id, listing_json, fingerprint, draft_json, "
                "revision, summary_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, source_id, text, fingerprint, draft, revision, summary, now, now))

    def preview(self, task_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM download_previews WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        return {"source_id": row["source_id"], "listing": json.loads(row["listing_json"]),
                "fingerprint": row["fingerprint"], "revision": int(row["revision"]),
                "draft": json.loads(row["draft_json"]) if row["draft_json"] else None,
                "updated_at": row["updated_at"]}

    def preview_summaries(self) -> dict[int, dict[str, Any]]:
        """Per page task: what the snapshot shows (no episode list, and the list itself is not read)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT p.task_id, p.summary_json, p.fingerprint, p.draft_json IS NOT NULL AS has_draft, p.revision, "
                "CASE WHEN p.summary_json IS NULL THEN p.listing_json END AS listing_json FROM download_previews p "
                "JOIN download_tasks t ON t.id = p.task_id WHERE t.state = 'NEEDS_CHOICE'").fetchall()
        out = {}
        for row in rows:
            summary = (json.loads(row["summary_json"]) if row["summary_json"]
                       else _preview_summary(json.loads(row["listing_json"])))
            out[int(row["task_id"])] = {**summary, "fingerprint": row["fingerprint"], "revision": int(row["revision"]),
                                        "has_draft": bool(row["has_draft"])}
        return out

    def save_draft(self, task_id: int, selection: Mapping[str, Any], fingerprint: Any, revision: Any) -> int:
        """Keep the user's unconfirmed choice; ``revision`` must be the stored one (else STALE_DRAFT)."""
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise GroupError("BAD_SELECTION", "Thiếu revision của bản nháp.")
        text = json.dumps(selection, sort_keys=True, ensure_ascii=False)
        with self._lock, self._db:
            row = self._db.execute("SELECT fingerprint, revision FROM download_previews WHERE task_id = ?",
                                   (task_id,)).fetchone()
            if row is None:
                raise GroupError("NOT_WAITING", "Lượt này không chờ chọn tập.", 409)
            if row["fingerprint"] != fingerprint:
                raise GroupError("STALE_PREVIEW", "Danh sách tập đã đổi; mở lại danh sách rồi chọn lại.", 409)
            if int(row["revision"]) != revision:
                raise GroupError("STALE_DRAFT", "Lựa chọn vừa được sửa ở nơi khác; tải lại danh sách.", 409,
                                 {"revision": int(row["revision"])})
            cursor = self._db.execute(
                "UPDATE download_previews SET draft_json = ?, revision = revision + 1, updated_at = ? "
                "WHERE task_id = ? AND revision = ?", (text, self._now(), task_id, revision))
            if cursor.rowcount != 1:
                raise GroupError("STALE_DRAFT", "Lựa chọn vừa được sửa ở nơi khác; tải lại danh sách.", 409)
        return revision + 1

    # Creating a group -------------------------------------------------------------------------------------

    def create_group(self, parent_id: int, *, selection: Mapping[str, Any], fingerprint: Any, request_key: Any,
                     confirm_scope: bool, skip_existing: bool) -> Created:
        """"Tải N tập" (see the module docstring). Raises GroupError; nothing is written unless all of it is."""
        if not isinstance(request_key, str) or not REQUEST_KEY.fullmatch(request_key):
            raise GroupError("BAD_REQUEST_KEY", "Thiếu khóa yêu cầu (idempotency_key: 8–64 chữ, số, _ hoặc -).")
        digest = request_hash(parent_id, selection, str(fingerprint), skip_existing)
        with self._lock:
            replay = self._replay(request_key, digest)
            if replay is not None:
                return replay
            parent, preview = self._waiting_parent(parent_id)
            if preview["fingerprint"] != fingerprint:
                raise GroupError("STALE_PREVIEW", "Danh sách tập đã đổi; mở lại danh sách rồi chọn lại.", 409)
            listing = listing_from_public(preview["listing"])
            plan = self._checked_plan(listing, selection, confirm_scope)
            items, existing = self._new_items(listing, plan.items, skip_existing)
            group = self._write_group(parent, preview, listing, plan, items, existing, selection, request_key, digest)
        return Created(group, existing=existing)

    def _replay(self, request_key: str, digest: str) -> Created | None:
        row = self._db.execute("SELECT * FROM download_groups WHERE request_key = ?", (request_key,)).fetchone()
        if row is None:
            return None
        if row["request_hash"] != digest:
            raise GroupError("IDEMPOTENCY_CONFLICT", "Khóa yêu cầu này đã dùng cho một lựa chọn khác.", 409)
        return Created(dict(row), replay=True, existing=json.loads(row["existing_json"] or "[]"))

    def _waiting_parent(self, parent_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
        parent = self.store.get(parent_id)
        if parent is None:
            raise GroupError("NOT_FOUND", "Không thấy lượt tải.", 404)
        probe = parent.get("probe") or {}
        preview = self.preview(parent_id)
        if parent["state"] != "NEEDS_CHOICE" or probe.get("choice_kind") != "episodes" or preview is None:
            row = self._db.execute("SELECT id FROM download_groups WHERE parent_task_id = ?", (parent_id,)).fetchone()
            raise GroupError("NOT_WAITING", "Lượt này không còn chờ chọn tập.", 409,
                             {"group_id": int(row["id"]) if row else None, "state": parent["state"]})
        return parent, preview

    @staticmethod
    def _checked_plan(listing: Listing, selection: Mapping[str, Any], confirm_scope: bool) -> SelectionPlan:
        plan = check_selection(listing, selection)
        summary = plan_summary(listing, plan)
        if plan.missing:
            raise GroupError("VARIANT_MISSING", "Có tập không có bản đã chọn; chọn bản khác hoặc bỏ các tập đó.",
                             detail={"episodes": summary["missing"][:50], "episode_count": len(plan.missing)})
        if plan.ambiguous:
            raise GroupError("VARIANT_AMBIGUOUS", "Có tập có hai file cùng bản đã chọn; chọn bản riêng cho tập đó.",
                             detail={"episodes": summary["ambiguous"][:50], "episode_count": len(plan.ambiguous)})
        if not plan.items:
            raise GroupError("BAD_SELECTION", "Chọn ít nhất một tập.")
        if plan.count > MAX_GROUP_EPISODES:
            raise GroupError("GROUP_TOO_LARGE", f"Một nhóm tối đa {MAX_GROUP_EPISODES} tập; đang chọn {plan.count}. "
                             "Chọn bớt tập (Chọn tập) rồi tải phần còn lại sau.", detail={"count": plan.count})
        if not plan.complete and confirm_scope is not True:
            raise GroupError("SCOPE_NOT_CONFIRMED", plan.note, detail={"count": plan.count,
                             "confirm_label": plan.confirm_label})
        return plan

    def _new_items(self, listing: Listing, items: Sequence[FileSelection],
                   skip_existing: bool) -> tuple[list[FileSelection], list[dict[str, Any]]]:
        """The chosen files without those already in an open task or waiting in a group; ITEMS_EXIST unless
        ``skip_existing`` (then they are listed in the group, never dropped silently)."""
        keys = [item.key for item in items]
        found: dict[str, dict[str, Any]] = {}
        released = tuple(sorted(RELEASED_LINK_STATES))
        for chunk in _chunks(keys):
            marks = ", ".join("?" for _ in chunk)
            for row in self._db.execute(
                    f"SELECT id, item_key, state FROM download_tasks WHERE item_key IN ({marks}) AND state NOT IN "
                    f"({', '.join('?' for _ in released)}) ORDER BY id", (*chunk, *released)):
                found.setdefault(row["item_key"], {"task_id": int(row["id"]), "state": row["state"]})
            for row in self._db.execute(
                    f"SELECT item_key, group_id, status FROM download_group_members WHERE item_key IN ({marks}) "
                    "AND status IN (?, ?)", (*chunk, PENDING, HELD)):
                found.setdefault(row["item_key"], {"group_id": int(row["group_id"]), "state": row["status"]})
        labels = {episode.key: episode.label for episode in listing.episodes}
        existing = [{"episode": item.episode, "label": labels.get(item.episode, item.episode), **found[item.key]}
                    for item in items if item.key in found]
        if existing and not skip_existing:
            raise GroupError("ITEMS_EXIST", "Có tập đã nằm trong danh sách tải; bỏ chọn các tập đó hoặc tải phần còn "
                             "lại.", 409, {"existing": existing[:50], "existing_count": len(existing)})
        new = [item for item in items if item.key not in found]
        if not new:
            raise GroupError("ITEMS_EXIST", "Mọi tập đã chọn đều đã nằm trong danh sách tải.", 409,
                             {"existing": existing[:50], "existing_count": len(existing)})
        return new, existing

    def _write_group(self, parent: dict[str, Any], preview: dict[str, Any], listing: Listing, plan: SelectionPlan,
                     items: list[FileSelection], existing: list[dict[str, Any]], selection: Mapping[str, Any],
                     request_key: str, digest: str) -> dict[str, Any]:
        probe = parent.get("probe") or {}
        seasons = {season.key: season for season in listing.seasons}
        now = self._now()
        with self._db:
            cursor = self._db.execute(
                "INSERT INTO download_groups (parent_task_id, source_id, source_label, account_owner, film, title, "
                "url, fingerprint, mode, complete, reasons_json, note, total, width, request_key, request_hash, state, "
                "existing_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?)",
                (parent["id"], preview["source_id"], str(probe.get("source_label") or preview["source_id"]),
                 parent.get("account_owner"), listing.film, listing.title, parent["url"], preview["fingerprint"],
                 selection["mode"], int(listing.complete), json.dumps(list(listing.reasons)), plan.note or None,
                 len(items), ordinal_width(len(items)), request_key, digest, GROUP_ACTIVE,
                 json.dumps(existing, ensure_ascii=False) if existing else None, now, now))
            group_id = int(cursor.lastrowid)
            for ordinal, item in enumerate(items, start=1):
                found = listing.find(item)
                if found is None:  # the plan was made from this listing: a bug, never a partial group
                    raise GroupError("BAD_SELECTION", "Lựa chọn không khớp danh sách.", 409)
                episode, variant = found
                season = seasons.get(episode.season)
                season_number = season.number if season is not None and not season.special else None
                code = episode_code(season_number=season_number, episode_number=episode.number,
                                    special=episode.special, label=episode.label)
                self._db.execute(
                    "INSERT INTO download_group_members (group_id, ordinal, item_key, selection_json, season_number, "
                    "season_label, episode_number, episode_label, special, variant_label, code, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (group_id, ordinal, item.key, json.dumps(item.public()), season_number,
                     season.label if season is not None else None, episode.number, episode.label,
                     int(episode.special), variant.label, code, PENDING))
            moved = self._db.execute(
                "UPDATE download_tasks SET state = 'EXPANDED', group_id = ?, updated_at = ?, state_since = ?, "
                "finished_at = ?, error_code = NULL, error_message = ? WHERE id = ? AND state = 'NEEDS_CHOICE'",
                (group_id, now, now, now, f"Đã tách thành nhóm {len(items)} tập.", parent["id"]))
            if moved.rowcount != 1:
                raise GroupError("NOT_WAITING", "Lượt này vừa đổi trạng thái; tải lại danh sách.", 409)
            self._db.execute("DELETE FROM download_previews WHERE task_id = ?", (parent["id"],))
        return self.group(group_id) or {}

    # Members becoming tasks --------------------------------------------------------------------------------

    def fill(self, limit: int = MAX_UNFINISHED_TASKS) -> list[dict[str, Any]]:
        """Turn PENDING members into QUEUED tasks, in (group, ordinal) order, while the list has room under
        ``limit``; each member once (compare-and-set + a unique index). The new tasks."""
        with self._lock:
            room = limit - self.store.unfinished_count()
            if room <= 0:
                return []
            rows = self._db.execute(
                "SELECT m.id, m.item_key, m.selection_json, m.episode_label, m.variant_label, g.id AS gid, g.url, "
                "g.source_id, g.source_label, g.account_owner, g.title FROM download_group_members m "
                "JOIN download_groups g ON g.id = m.group_id WHERE m.status = ? AND g.state = ? "
                "ORDER BY m.group_id, m.ordinal LIMIT ?", (PENDING, GROUP_ACTIVE, room)).fetchall()
            if not rows:
                return []
            now = self._now()
            created = []
            with self._db:
                for row in rows:
                    claimed = self._db.execute("UPDATE download_group_members SET status = ? WHERE id = ? AND status = ?",
                                               (CREATED, row["id"], PENDING))
                    if claimed.rowcount != 1:
                        continue
                    probe = {"provider": row["source_id"], "source_label": row["source_label"], "ready": False,
                             "account_file": json.loads(row["selection_json"])}
                    title = " · ".join(part for part in (row["title"], row["episode_label"], row["variant_label"]) if part)
                    cursor = self._db.execute(
                        "INSERT INTO download_tasks (url, state, created_at, updated_at, queued_at, state_since, "
                        "group_id, member_id, item_key, account_owner, probe_json, original_title) "
                        "VALUES (?, 'QUEUED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (row["url"], now, now, now, now, row["gid"], row["id"], row["item_key"], row["account_owner"],
                         json.dumps(probe, ensure_ascii=False), title[:300]))
                    task_id = int(cursor.lastrowid)
                    self._db.execute("UPDATE download_group_members SET task_id = ? WHERE id = ?", (task_id, row["id"]))
                    created.append(task_id)
        return [task for task in (self.store.get(task_id) for task_id in created) if task is not None]

    # Reading ---------------------------------------------------------------------------------------------------

    def group(self, group_id: int) -> dict[str, Any] | None:
        with self._lock:
            return _row(self._db.execute("SELECT * FROM download_groups WHERE id = ?", (group_id,)).fetchone())

    def group_of_parent(self, parent_id: int) -> dict[str, Any] | None:
        with self._lock:
            return _row(self._db.execute("SELECT * FROM download_groups WHERE parent_task_id = ?",
                                         (parent_id,)).fetchone())

    def members(self, group_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT m.*, t.state AS task_state, t.downloaded_bytes, t.total_bytes, t.estimated_bytes, t.output_size, "
                "t.desired_name, t.error_code, t.error_message, t.login_source, t.login_reason "
                "FROM download_group_members m LEFT JOIN download_tasks t ON t.id = m.task_id "
                "WHERE m.group_id = ? ORDER BY m.ordinal", (group_id,)).fetchall()
        return [dict(row) for row in rows]

    def member(self, member_id: int | None) -> dict[str, Any] | None:
        if member_id is None:
            return None
        with self._lock:
            return _row(self._db.execute(
                "SELECT m.*, g.title, g.width, g.total, g.state AS group_state, g.source_id, g.source_label "
                "FROM download_group_members m "
                "JOIN download_groups g ON g.id = m.group_id WHERE m.id = ?", (member_id,)).fetchone())

    def naming(self, task: Mapping[str, Any]) -> dict[str, Any] | None:
        """The group place of an episode task (ordinal, width, film part, code), or None for any other task. A
        renamed episode keeps its prefix and code: the new name replaces the film part only."""
        member = self.member(task.get("member_id"))
        if member is None:
            return None
        return {"ordinal": int(member["ordinal"]), "width": int(member["width"]),
                "film": task.get("desired_name") or member["title"], "code": member["code"],
                "total": int(member["total"]), "group_id": int(member["group_id"])}

    def names_by_task(self) -> dict[int, dict[str, Any]]:
        """For the snapshot: the group place and planned name of every episode task."""
        with self._lock:
            rows = self._db.execute(
                "SELECT m.task_id, m.ordinal, m.code, m.group_id, g.width, g.total, g.title, t.desired_name "
                "FROM download_group_members m JOIN download_groups g ON g.id = m.group_id "
                "JOIN download_tasks t ON t.id = m.task_id").fetchall()
        return {int(row["task_id"]): {
            "group_id": int(row["group_id"]), "ordinal": int(row["ordinal"]), "total": int(row["total"]),
            "code": row["code"], "planned_name": planned_stem(int(row["ordinal"]), int(row["width"]),
                                                              row["desired_name"] or row["title"], row["code"])}
            for row in rows}

    def _counts(self, group_id: int | None = None) -> dict[int, list[dict[str, Any]]]:
        """Per group: its members counted by (status, task state, last state) in SQL, with the sizes the percent
        needs (``summarize``), so a poll reads a few rows per group, never every member."""
        where, values = ("WHERE m.group_id = ?", (group_id,)) if group_id is not None else ("", ())
        with self._lock:
            rows = self._db.execute(
                "WITH sized AS (SELECT m.group_id, m.status, m.last_state, t.state AS task_state, t.downloaded_bytes, "
                "CASE WHEN t.state = 'COMPLETED' THEN NULLIF(t.output_size, 0) "
                "ELSE COALESCE(NULLIF(t.total_bytes, 0), NULLIF(t.estimated_bytes, 0)) END AS size "
                f"FROM download_group_members m LEFT JOIN download_tasks t ON t.id = m.task_id {where}) "
                "SELECT group_id, status, task_state, last_state, COUNT(*) AS n, SUM(size IS NULL) AS unsized, "
                "COALESCE(SUM(size), 0) AS size_sum, COALESCE(SUM(CASE WHEN size IS NULL THEN 0 "
                "WHEN task_state = 'COMPLETED' THEN size ELSE MIN(COALESCE(downloaded_bytes, 0), size) END), 0) "
                "AS done_sum FROM sized GROUP BY group_id, status, task_state, last_state", values).fetchall()
        by_group: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            by_group.setdefault(int(row["group_id"]), []).append(dict(row))
        return by_group

    def summaries(self) -> list[dict[str, Any]]:
        """Every group with its counts (the snapshot; the episodes left out as already listed are counted only,
        their list is in ``summary``)."""
        with self._lock:
            groups = [dict(row) for row in self._db.execute("SELECT * FROM download_groups ORDER BY id")]
        counts = self._counts()
        return [summarize(group, counts.get(int(group["id"]), []), with_existing=False) for group in groups]

    def summary(self, group_id: int) -> dict[str, Any] | None:
        group = self.group(group_id)
        return summarize(group, self._counts(group_id).get(group_id, [])) if group is not None else None

    def item_taken(self, item_key: str | None, task_id: int) -> bool:
        """Another open task, or a member waiting in a group, already has this episode file (a retried episode
        of a cancelled group must not download it twice)."""
        if not item_key:
            return False
        released = tuple(sorted(RELEASED_LINK_STATES))
        with self._lock:
            task = self._db.execute(
                f"SELECT 1 FROM download_tasks WHERE item_key = ? AND id != ? AND state NOT IN "
                f"({', '.join('?' for _ in released)}) LIMIT 1", (item_key, task_id, *released)).fetchone()
            member = self._db.execute(
                "SELECT 1 FROM download_group_members WHERE item_key = ? AND status IN (?, ?) LIMIT 1",
                (item_key, PENDING, HELD)).fetchone()
        return task is not None or member is not None

    def tasks_of(self, group_id: int) -> list[dict[str, Any]]:
        """The group's existing tasks, in group order."""
        with self._lock:
            ids = [int(row["task_id"]) for row in self._db.execute(
                "SELECT task_id FROM download_group_members WHERE group_id = ? AND task_id IS NOT NULL "
                "ORDER BY ordinal", (group_id,))]
        return [task for task in (self.store.get(task_id) for task_id in ids) if task is not None]

    # Group actions: the members not created yet, and the stored intent for the episode tasks ------------------
    #
    # A group action is stored before any episode task is touched, in the same transaction as its members' change:
    # Hủy nhóm as the group's CANCELLED state (every unfinished episode of a cancelled group is cancelled, now or
    # after an interruption), Dừng / Tiếp tục nhóm as the ``intent`` of each member whose task it applies to now
    # (STOPPABLE / RESUMABLE, from the worker). The worker applies what is left (``_settle_groups``) before any
    # dispatch or sign-in wake; an episode's own action, or its group's next one, replaces its intent.

    def _set_members(self, group_id: int, old: Sequence[str], new: str, intent: str,
                     intent_states: Iterable[str]) -> int:
        marks, states = ", ".join("?" for _ in old), tuple(intent_states)
        with self._lock, self._db:
            cursor = self._db.execute(
                f"UPDATE download_group_members SET status = ? WHERE group_id = ? AND status IN ({marks})",
                (new, group_id, *old))
            self._db.execute("UPDATE download_group_members SET intent = NULL WHERE group_id = ?", (group_id,))
            if states:
                self._db.execute(
                    f"UPDATE download_group_members SET intent = ? WHERE group_id = ? AND task_id IN (SELECT id FROM "
                    f"download_tasks WHERE group_id = ? AND state IN ({', '.join('?' for _ in states)}))",
                    (intent, group_id, group_id, *states))
            return int(cursor.rowcount)

    def hold(self, group_id: int, intent_states: Iterable[str] = ()) -> int:
        """Dừng nhóm: the members not created yet wait (HELD) instead of becoming tasks; the episode tasks in
        ``intent_states`` keep the stop to apply."""
        return self._set_members(group_id, (PENDING,), HELD, INTENT_STOP, intent_states)

    def release(self, group_id: int, intent_states: Iterable[str] = ()) -> int:
        """Tiếp tục nhóm: the held members may become tasks again; the episode tasks in ``intent_states`` keep the
        resume to apply."""
        return self._set_members(group_id, (HELD,), PENDING, INTENT_RESUME, intent_states)

    def cancel(self, group_id: int) -> int:
        """Hủy nhóm, first step, one transaction: the group CANCELLED (the stored intent for every episode task it
        has) and no member is created any more; a stored Dừng / Tiếp tục gives way to it."""
        with self._lock, self._db:
            self._db.execute("UPDATE download_groups SET state = ?, updated_at = ? WHERE id = ?",
                             (GROUP_CANCELLED, self._now(), group_id))
            self._db.execute("UPDATE download_group_members SET intent = NULL WHERE group_id = ?", (group_id,))
            cursor = self._db.execute(
                "UPDATE download_group_members SET status = ? WHERE group_id = ? AND status IN (?, ?)",
                (CANCELLED, group_id, PENDING, HELD))
            return int(cursor.rowcount)

    def intents(self, group_id: int | None = None) -> list[tuple[int, int, str]]:
        """(member id, task id, intent) of the stored Dừng / Tiếp tục not applied yet, in group order."""
        where, values = (" AND group_id = ?", (group_id,)) if group_id is not None else ("", ())
        with self._lock:  # the partial index: a pass reads only the members that still have an intent
            rows = self._db.execute(
                "SELECT id, task_id, intent, group_id, ordinal FROM download_group_members WHERE intent IS NOT NULL "
                f"AND task_id IS NOT NULL{where}", values).fetchall()
        rows.sort(key=lambda row: (row["group_id"], row["ordinal"]))
        return [(int(row["id"]), int(row["task_id"]), str(row["intent"])) for row in rows]

    def clear_intent(self, member_id: int | None, intent: str | None = None) -> None:
        """Drop a member's stored intent (only ``intent`` when given: a newer one stays)."""
        if member_id is None:
            return
        with self._lock, self._db:
            if intent is None:
                self._db.execute("UPDATE download_group_members SET intent = NULL WHERE id = ? AND intent IS NOT "
                                 "NULL", (member_id,))
            else:
                self._db.execute("UPDATE download_group_members SET intent = NULL WHERE id = ? AND intent = ?",
                                 (member_id, intent))

    def clear_intents(self, group_id: int) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE download_group_members SET intent = NULL WHERE group_id = ? AND intent IS NOT "
                             "NULL", (group_id,))

    def is_cancelled(self, group_id: int | None) -> bool:
        if group_id is None:
            return False
        with self._lock:
            return self._db.execute("SELECT 1 FROM download_groups WHERE id = ? AND state = ?",
                                    (group_id, GROUP_CANCELLED)).fetchone() is not None

    def cancelled_group_ids(self) -> set[int]:
        with self._lock:
            return {int(row["id"]) for row in self._db.execute("SELECT id FROM download_groups WHERE state = ?",
                                                               (GROUP_CANCELLED,))}

    def cancelled_tasks(self, states: Iterable[str], group_id: int | None = None) -> list[dict[str, Any]]:
        """The episode tasks of cancelled groups still in ``states`` (what Hủy nhóm has yet to reach)."""
        wanted = tuple(states)
        where, values = (" AND g.id = ?", (group_id,)) if group_id is not None else ("", ())
        with self._lock:
            ids = [int(row["id"]) for row in self._db.execute(
                "SELECT t.id FROM download_tasks t JOIN download_groups g ON g.id = t.group_id WHERE g.state = ? "
                f"AND t.state IN ({', '.join('?' for _ in wanted)}){where} ORDER BY t.id",
                (GROUP_CANCELLED, *wanted, *values))]
        return [task for task in (self.store.get(task_id) for task_id in ids) if task is not None]

    def delete_group(self, group_id: int, parent_id: int | None) -> None:
        """The group's own rows (its members cascade) and its EXPANDED page task, in one transaction, so a page
        task is never left without its group; its episode tasks are removed by the worker first."""
        with self._lock, self._db:
            self._db.execute("DELETE FROM download_groups WHERE id = ?", (group_id,))
            if parent_id is not None:
                self._db.execute("DELETE FROM download_tasks WHERE id = ? AND state = 'EXPANDED'", (parent_id,))

    def drop_left_previews(self) -> int:
        """The stored lists of page tasks that no longer wait for a choice (cancelled, or removed with their
        group): the list is needed only while its page waits in NEEDS_CHOICE. A page still PROBING keeps it: its
        probe stores the list just before the task moves to NEEDS_CHOICE, and a dispatch pass may run between."""
        with self._lock, self._db:
            cursor = self._db.execute(
                "DELETE FROM download_previews WHERE task_id IN (SELECT p.task_id FROM download_previews p "
                "JOIN download_tasks t ON t.id = p.task_id WHERE t.state NOT IN ('NEEDS_CHOICE', 'PROBING'))")
            return int(cursor.rowcount)


BUCKETS = ("pending", "held", "queued", "waiting_login", "running", "completed", "failed", "stopped", "interrupted",
           "cancelled", "expired", "removed", "attention")
_UNFINISHED_BUCKETS = ("pending", "held", "queued", "waiting_login", "running", "attention")
_STATE_BUCKETS = {"QUEUED": "queued", "WAITING_LOGIN": "waiting_login", "COMPLETED": "completed", "FAILED": "failed",
                  "STOPPED": "stopped", "INTERRUPTED": "interrupted", "CANCELLED": "cancelled", "EXPIRED": "expired"}


def _preview_summary(listing: Mapping[str, Any]) -> dict[str, Any]:
    return {name: listing.get(name) for name in ("title", "kind", "episode_count", "complete", "message")}


def member_bucket(status: str, state: str | None, last_state: str | None) -> str:
    """Where one member counts in its group's summary. A task the user (or the 30-day clean-up) removed counts by
    its last state; one removed before any state was kept counts as "removed"."""
    if status in (PENDING, HELD, CANCELLED):
        return {PENDING: "pending", HELD: "held", CANCELLED: "cancelled"}[status]
    current = state or last_state
    if current is None:
        return "removed"
    if current in SLOT_STATES:
        return "running" if state is not None else "removed"
    return _STATE_BUCKETS.get(current, "attention" if state is not None else "removed")


def summarize(group: Mapping[str, Any], counted: Iterable[Mapping[str, Any]], *,
              with_existing: bool = True) -> dict[str, Any]:
    """A group's counts from ``DownloadGroups._counts`` rows (status, task state, last state, ``n`` members,
    ``unsized`` of them, ``size_sum`` and ``done_sum`` bytes): N/total done, what waits, runs or failed; a percent
    only when every member still in the group has a known size (never an invented one). ``finished``: nothing
    waits or runs any more."""
    counts = dict.fromkeys(BUCKETS, 0)
    done_bytes = size_bytes = 0
    sized = True
    for row in counted:
        bucket = member_bucket(row["status"], row.get("task_state"), row.get("last_state"))
        counts[bucket] += int(row["n"])
        if bucket in ("cancelled", "expired", "removed"):
            continue
        if row["unsized"]:
            sized = False
        size_bytes += int(row["size_sum"] or 0)
        done_bytes += int(row["done_sum"] or 0)
    total = int(group["total"])
    existing = json.loads(group.get("existing_json") or "[]")
    live = total - counts["cancelled"] - counts["expired"] - counts["removed"]
    percent = None
    if sized and live > 0 and size_bytes > 0:
        percent = 100 if counts["completed"] == live else min(99, int(100 * done_bytes / size_bytes))
    return {"id": int(group["id"]), "parent_task_id": group.get("parent_task_id"), "source_id": group["source_id"],
            "source_label": group["source_label"], "title": group["title"], "state": group["state"],
            "mode": group["mode"], "complete": bool(group["complete"]), "note": group.get("note"),
            "reasons": [REASONS.get(reason, reason) for reason in json.loads(group.get("reasons_json") or "[]")],
            "total": total, "done": counts["completed"], "counts": counts, "percent": percent,
            "finished": not any(counts[name] for name in _UNFINISHED_BUCKETS),
            "existing_count": len(existing), "existing": existing if with_existing else None,
            "created_at": group["created_at"]}
