"""Episode previews, groups and names of a source account (download_groups, download_episode_names; M4).

The store and the groups only (no worker, no browser): made-up lists of tests/account_queue_fixtures.py in a
temporary root under the install's temp/. The worker and the routes are in test_download_account_queue.py.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.download_account_listing import FileSelection
from biliflow.download_episode_names import (
    episode_code,
    group_stem,
    group_target,
    ordinal_width,
    planned_stem,
)
from biliflow.download_files import MAX_NAME_LENGTH
from biliflow.download_groups import (
    MAX_GROUP_EPISODES,
    DownloadGroups,
    GroupError,
    check_selection,
    listing_from_public,
    parse_selection,
    summarize,
)
from biliflow.download_store import MAX_UNFINISHED_TASKS, DownloadStore
from tests.account_queue_fixtures import TITLE, entry, page_url, series
from tests.test_download_accounts import TEMP_PARENT

KIND_1080 = "1080p|vietsub"
KIND_720 = "720p|vietsub"


def all_of(kind: str = KIND_1080) -> dict:
    return {"mode": "all", "variant_kind": kind}


class NamingTest(unittest.TestCase):
    def test_the_prefix_has_at_least_three_digits_and_the_width_of_the_whole_group(self):
        self.assertEqual([ordinal_width(n) for n in (1, 12, 999, 1000, 0)], [3, 3, 3, 4, 3])
        self.assertTrue(planned_stem(2, 3, "Phim", "E02").startswith("002 - "))
        names = sorted(planned_stem(n, 3, "Phim", f"E{n:02d}") for n in (10, 2, 1))
        self.assertEqual([name[:3] for name in names], ["001", "002", "010"])  # 2 before 10 by name

    def test_episode_codes_come_from_the_source_numbers_never_a_guess(self):
        self.assertEqual(episode_code(season_number=1, episode_number=5, special=False, label="Tập 5"), "S01E05")
        self.assertEqual(episode_code(season_number=None, episode_number=12, special=False, label="x"), "E12")
        self.assertEqual(episode_code(season_number=None, episode_number=3, special=True, label="x"), "SP03")
        self.assertEqual(episode_code(season_number=2, episode_number=3, special=True, label="x"), "S02SP03")
        self.assertEqual(episode_code(season_number=1, episode_number=None, special=False, label="Tập cuối"),
                         "Tập cuối")
        self.assertEqual(episode_code(season_number=None, episode_number=None, special=True, label="OVA: mở đầu"),
                         "SP OVA mở đầu")
        self.assertEqual(episode_code(season_number=None, episode_number=None, special=False, label="///"), "?")

    def test_a_long_film_name_is_cut_and_the_prefix_and_code_always_stay(self):
        long_film = "Tên phim rất dài " * 30
        stem = group_stem(7, 3, long_film, "S01E07", suffix=".mkv")
        self.assertTrue(stem.startswith("007 - Tên phim"))
        self.assertTrue(stem.endswith(" - S01E07"))
        self.assertLessEqual(len(stem + ".mkv"), MAX_NAME_LENGTH)
        planned = planned_stem(7, 3, long_film, "S01E07", ".mkv")
        self.assertLessEqual(len(planned + " (9999).mkv"), MAX_NAME_LENGTH)  # room kept for a taken name
        self.assertEqual(group_stem(1, 3, "<>:|?*", "E01", suffix=".mp4"), "001 - E01")

    def test_a_taken_name_gets_the_next_number_and_nothing_is_overwritten_mkv_stays(self):
        with TemporaryDirectory(dir=TEMP_PARENT) as name:
            directory = Path(name)
            first = group_target(directory, 1, 3, "Phim", "S01E01", ".mkv")
            self.assertEqual(first.name, "001 - Phim - S01E01.mkv")
            first.write_bytes(b"x")
            (directory / "001 - phim - s01e01 (2).MKV").write_bytes(b"y")  # case-insensitive, as NTFS
            third = group_target(directory, 1, 3, "Phim", "S01E01", ".mkv")
            self.assertEqual(third.name, "001 - Phim - S01E01 (3).mkv")
            self.assertEqual(first.read_bytes(), b"x")


class GroupCase(unittest.TestCase):
    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        self._temp = TemporaryDirectory(dir=TEMP_PARENT, prefix="groups-")
        self.root = Path(self._temp.name)
        self.path = self.root / "state" / "downloads.sqlite3"
        self.store = DownloadStore(self.path)
        self.groups = DownloadGroups(self.store)

    def tearDown(self):
        self.store.close()
        self._temp.cleanup()

    def reopen(self):
        self.store.close()
        self.store = DownloadStore(self.path)
        self.groups = DownloadGroups(self.store)

    def waiting_page(self, listing, url=None) -> dict:
        """A pasted page whose probe found a series: NEEDS_CHOICE of the episodes kind with its stored list."""
        task, = self.store.add_tasks([url or page_url(film=listing.film)])
        self.store.transition(task["id"], {"QUEUED"}, "PROBING")
        public = listing.public()
        self.groups.save_preview(task["id"], "alpha", public)
        probe = {"provider": "alpha", "source_label": "Nguồn alpha", "choice_kind": "episodes", "ready": False,
                 "fingerprint": public["fingerprint"]}
        return self.store.transition(task["id"], {"PROBING"}, "NEEDS_CHOICE", probe=probe)

    def create(self, task, selection=None, *, key="key-00000001", fingerprint=None, scope=False, skip=False):
        fingerprint = fingerprint or self.groups.preview(task["id"])["fingerprint"]
        return self.groups.create_group(task["id"], selection=parse_selection(selection or all_of()),
                                        fingerprint=fingerprint, request_key=key, confirm_scope=scope,
                                        skip_existing=skip)

    def error(self, call) -> GroupError:
        with self.assertRaises(GroupError) as caught:
            call()
        return caught.exception

    def fill_list(self, count: int) -> list[dict]:
        urls = [f"https://clips.example/v/{index}" for index in range(count)]
        return self.store.add_tasks(urls)


class StoredListTest(GroupCase):
    def test_the_stored_list_rebuilds_the_same_listing_and_a_changed_one_is_refused(self):
        listing = series(4, seasons=((1, 2), (2, 2)))
        public = listing.public()
        rebuilt = listing_from_public(public).public()  # it plans; how many pages were read is not kept
        self.assertEqual({key: rebuilt[key] for key in public if key not in ("pages", "skipped")},
                         {key: public[key] for key in public if key not in ("pages", "skipped")})
        changed = json.loads(json.dumps(public))
        changed["groups"][0]["episodes"][0]["label"] = "Tập khác"
        self.assertEqual(self.error(lambda: listing_from_public(changed)).code, "BAD_PREVIEW")
        self.assertEqual(self.error(lambda: listing_from_public({"source": "alpha"})).code, "BAD_PREVIEW")

    def test_a_selection_is_checked_strictly(self):
        for bad in (None, {"mode": "some"}, {"mode": "all", "episodes": ["s1e1"]},
                    {"mode": "pick", "episodes": ["s1e1", "s1e1"]}, {"mode": "pick", "episodes": ["../x"]},
                    {"mode": "all", "variant_kind": KIND_1080, "variants": {}}, {"mode": "all", "url": "x"},
                    {"mode": "pick", "episodes": "s1e1"}, {"mode": "all", "variant_kind": ""}):
            with self.subTest(bad=bad):
                self.assertEqual(self.error(lambda: parse_selection(bad)).code, "BAD_SELECTION")
        listing = series(3)
        unknown = self.error(lambda: check_selection(listing, parse_selection(
            {"mode": "pick", "episodes": ["s1e1", "s9e9"], "variant_kind": KIND_1080})))
        self.assertEqual((unknown.code, unknown.detail["unknown"]), ("BAD_SELECTION", ["s9e9"]))
        fake_variant = self.error(lambda: check_selection(listing, parse_selection(
            {"mode": "pick", "episodes": ["s1e1"], "variants": {"s1e1": "v4k"}})))
        self.assertEqual(fake_variant.detail["unknown"], ["s1e1"])
        no_variant = self.error(lambda: check_selection(listing, parse_selection({"mode": "all"})))
        self.assertIn("Chọn một bản", no_variant.message)

    def test_a_draft_survives_restart_and_a_stale_revision_or_list_is_refused(self):
        task = self.waiting_page(series(3))
        preview = self.groups.preview(task["id"])
        selection = parse_selection({"mode": "pick", "episodes": ["s1e2"], "variant_kind": KIND_720})
        revision = self.groups.save_draft(task["id"], selection, preview["fingerprint"], preview["revision"])
        self.assertEqual(revision, preview["revision"] + 1)
        stale = self.error(lambda: self.groups.save_draft(task["id"], selection, preview["fingerprint"], 0))
        self.assertEqual((stale.code, stale.status, stale.detail["revision"]), ("STALE_DRAFT", 409, revision))
        other = self.error(lambda: self.groups.save_draft(task["id"], selection, "0" * 64, revision))
        self.assertEqual(other.code, "STALE_PREVIEW")
        self.reopen()
        kept = self.groups.preview(task["id"])
        self.assertEqual((kept["draft"], kept["revision"]), (selection, revision))
        # The same list read again keeps the draft; another list drops it.
        self.groups.save_preview(task["id"], "alpha", series(3).public())
        self.assertEqual(self.groups.preview(task["id"])["draft"], selection)
        self.groups.save_preview(task["id"], "alpha", series(4).public())
        self.assertIsNone(self.groups.preview(task["id"])["draft"])


class CreateGroupTest(GroupCase):
    def test_all_episodes_become_one_group_in_list_order_and_the_page_is_expanded(self):
        task = self.waiting_page(series(12))
        created = self.create(task)
        group = created.group
        self.assertFalse(created.replay)
        self.assertEqual((group["total"], group["width"], group["state"], group["title"]), (12, 3, "ACTIVE", TITLE))
        members = self.groups.members(group["id"])
        self.assertEqual([m["ordinal"] for m in members], list(range(1, 13)))
        self.assertEqual([m["code"] for m in members[:3]], ["S01E01", "S01E02", "S01E03"])
        self.assertEqual({json.loads(m["selection_json"])["variant"] for m in members}, {"v1080"})
        self.assertEqual({m["status"] for m in members}, {"PENDING"})
        parent = self.store.get(task["id"])
        self.assertEqual((parent["state"], parent["group_id"]), ("EXPANDED", group["id"]))
        self.assertIsNone(self.groups.preview(task["id"]))  # the list went into the group
        self.assertEqual(self.store.unfinished_count(), 0)  # EXPANDED holds no place in the list
        self.assertEqual(self.store.list_tasks()[0]["state"], "EXPANDED")

    def test_two_seasons_and_specials_keep_the_listing_order_and_their_codes(self):
        extra = (entry("sp1", "v1080", season="s1", season_number=1, number=None, label="OVA", special=True,
                       after=1),
                 entry("sp2", "v1080", season=None, season_number=None, number=2, label="Ngoại truyện",
                       special=True))
        listing = series(seasons=((1, 2), (2, 2)), variants=(("v1080", "1080p"),), extra=extra)
        created = self.create(self.waiting_page(listing), {"mode": "all"})
        codes = [m["code"] for m in self.groups.members(created.group["id"])]
        self.assertEqual(codes, ["S01E01", "SP OVA", "S01E02", "S02E01", "S02E02", "SP02"])

    def test_one_variant_per_episode_never_another_one_silently(self):
        extra = (entry("s1e4", "v1080", number=4, quality="1080p"), entry("s1e4", "v1080b", number=4,
                                                                          quality="1080p"))
        listing = series(3, extra=extra)
        task = self.waiting_page(listing)
        ambiguous = self.error(lambda: self.create(task))
        self.assertEqual((ambiguous.code, ambiguous.detail["episodes"]), ("VARIANT_AMBIGUOUS",
                         [{"episode": "s1e4", "label": "Tập 4"}]))
        missing_listing = series(3, extra=(entry("s1e4", "v480", number=4, quality="480p"),))
        task2 = self.waiting_page(missing_listing, url=page_url(film="f2"))
        missing = self.error(lambda: self.create(task2))
        self.assertEqual((missing.code, missing.detail["episode_count"]), ("VARIANT_MISSING", 1))
        # Nothing was written: no group, the pages still wait for a choice.
        self.assertEqual(self.groups.summaries(), [])
        self.assertEqual({self.store.get(t["id"])["state"] for t in (task, task2)}, {"NEEDS_CHOICE"})
        # Per-episode variants settle it: one member per episode, each the file of the variant chosen for it.
        variants = {f"s1e{n}": "v1080" for n in range(1, 4)} | {"s1e4": "v1080b"}
        created = self.create(task, {"mode": "all", "variants": variants})
        self.assertEqual(created.group["total"], 4)
        members = self.groups.members(created.group["id"])
        chosen = [(selection["episode"], selection["variant"])
                  for selection in (json.loads(member["selection_json"]) for member in members)]
        self.assertEqual(chosen, [("s1e1", "v1080"), ("s1e2", "v1080"), ("s1e3", "v1080"), ("s1e4", "v1080b")])
        self.assertEqual([member["item_key"] for member in members],
                         [FileSelection("alpha", "f1", episode, variant).key for episode, variant in chosen])

    def test_an_incomplete_list_needs_the_seen_scope_confirmed(self):
        task = self.waiting_page(series(5, complete=False))
        refused = self.error(lambda: self.create(task))
        self.assertEqual((refused.code, refused.detail["confirm_label"]), ("SCOPE_NOT_CONFIRMED",
                                                                          "Tải 5 tập đã thấy"))
        self.assertIn("chỉ gồm 5 tập", refused.message)
        created = self.create(task, scope=True)
        summary = self.groups.summary(created.group["id"])
        self.assertFalse(summary["complete"])
        self.assertIn("chỉ gồm 5 tập", summary["note"])

    def test_a_stale_fingerprint_is_refused(self):
        task = self.waiting_page(series(3))
        self.assertEqual(self.error(lambda: self.create(task, fingerprint="f" * 64)).code, "STALE_PREVIEW")

    def test_a_group_above_500_episodes_is_refused_clearly(self):
        task = self.waiting_page(series(MAX_GROUP_EPISODES + 1, variants=(("v1080", "1080p"),)))
        refused = self.error(lambda: self.create(task, {"mode": "all"}))
        self.assertEqual((refused.code, refused.detail["count"]), ("GROUP_TOO_LARGE", 501))
        picked = [f"s1e{n}" for n in range(1, MAX_GROUP_EPISODES + 1)]
        created = self.create(task, {"mode": "pick", "episodes": picked})
        self.assertEqual(created.group["total"], MAX_GROUP_EPISODES)
        self.assertEqual(len(self.groups.members(created.group["id"])), MAX_GROUP_EPISODES)

    def test_a_repeated_request_returns_the_same_group_and_a_reused_key_is_refused(self):
        task = self.waiting_page(series(3))
        fingerprint = self.groups.preview(task["id"])["fingerprint"]
        first = self.create(task, fingerprint=fingerprint)
        again = self.create(task, fingerprint=fingerprint)
        self.assertTrue(again.replay)
        self.assertEqual(again.group["id"], first.group["id"])
        conflict = self.error(lambda: self.create(task, all_of(KIND_720), fingerprint=fingerprint))
        self.assertEqual(conflict.code, "IDEMPOTENCY_CONFLICT")
        other_key = self.error(lambda: self.create(task, key="key-00000002", fingerprint=fingerprint))
        self.assertEqual((other_key.code, other_key.detail["group_id"]), ("NOT_WAITING", first.group["id"]))
        bad_key = self.error(lambda: self.create(task, key="short", fingerprint=fingerprint))
        self.assertEqual(bad_key.code, "BAD_REQUEST_KEY")
        self.assertEqual(len(self.groups.summaries()), 1)

    def test_concurrent_confirms_make_one_group(self):
        task = self.waiting_page(series(20))
        fingerprint = self.groups.preview(task["id"])["fingerprint"]
        results, start = [], threading.Barrier(8)

        def confirm(index):
            start.wait()
            try:
                results.append(self.create(task, key=f"key-{index % 2:08d}", fingerprint=fingerprint).group["id"])
            except GroupError as error:
                results.append(error.code)

        threads = [threading.Thread(target=confirm, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        groups = self.groups.summaries()
        self.assertEqual(len(groups), 1)
        self.assertEqual(set(results) - {"NOT_WAITING"}, {groups[0]["id"]})
        self.assertEqual(len(self.groups.members(groups[0]["id"])), 20)

    def test_episodes_already_in_the_list_are_named_and_only_skipped_on_request(self):
        first = self.waiting_page(series(5))
        self.create(first, {"mode": "pick", "episodes": ["s1e1", "s1e2"], "variant_kind": KIND_1080})
        second = self.waiting_page(series(5), url=page_url(film="f1") + "?again=1")
        refused = self.error(lambda: self.create(second, key="key-00000002"))
        self.assertEqual((refused.code, refused.detail["existing_count"]), ("ITEMS_EXIST", 2))
        self.assertEqual([item["episode"] for item in refused.detail["existing"]], ["s1e1", "s1e2"])
        created = self.create(second, key="key-00000003", skip=True)
        self.assertEqual(created.group["total"], 3)
        self.assertEqual([item["episode"] for item in created.existing], ["s1e1", "s1e2"])
        self.assertEqual(len(self.groups.summary(created.group["id"])["existing"]), 2)
        listed = next(item for item in self.groups.summaries() if item["id"] == created.group["id"])
        self.assertEqual((listed["existing"], listed["existing_count"]), (None, 2))  # the snapshot only counts them
        # Another variant of the same episode is another file: not a duplicate.
        third = self.waiting_page(series(5), url=page_url(film="f1") + "?third=1")
        self.assertEqual(self.create(third, all_of(KIND_720), key="key-00000004").group["total"], 5)

    def assert_a_failed_write_is_rolled_back(self, failing: str, expected: list[tuple[str, object]]) -> None:
        """R49: ``failing`` is a TEMP trigger that aborts one statement of the group write (``_write_group``).
        TEMP triggers on the store's own connection (they live only on that connection; no production code is
        patched) also record each row the write made before it failed, which must be ``expected`` (table, value)
        in order: a partial write. Nothing of it stays: no group, no member, the page task and its stored list
        and draft exactly as they were (still NEEDS_CHOICE), and nothing is filled. The same request (same key)
        then makes the whole group."""
        task = self.waiting_page(series(12))
        preview = self.groups.preview(task["id"])
        draft = parse_selection({"mode": "pick", "episodes": ["s1e2"], "variant_kind": KIND_720})
        revision = self.groups.save_draft(task["id"], draft, preview["fingerprint"], preview["revision"])
        page, kept = self.store.get(task["id"]), self.groups.preview(task["id"])
        self.assertEqual((page["state"], page["group_id"], kept["fingerprint"], kept["revision"], kept["draft"]),
                         ("NEEDS_CHOICE", None, preview["fingerprint"], revision, draft))
        db = self.groups._db  # noqa: SLF001 - the connection DownloadGroups writes with
        written: list[tuple[str, object]] = []
        db.create_function("test_written", 2, lambda table, value: written.append((table, value)))
        db.executescript(
            "CREATE TEMP TRIGGER test_group_in AFTER INSERT ON download_groups "
            "BEGIN SELECT test_written('group', NEW.id); END;"
            "CREATE TEMP TRIGGER test_member_in AFTER INSERT ON download_group_members "
            "BEGIN SELECT test_written('member', NEW.ordinal); END;"
            "CREATE TEMP TRIGGER test_task_moved AFTER UPDATE OF state ON download_tasks "
            "BEGIN SELECT test_written('task', NEW.state); END;"
            f"{failing}")

        with self.assertRaises(sqlite3.Error):
            self.create(task)

        self.assertEqual(([table for table, _value in written[:1]], written[1:]), (["group"], expected))
        self.assertFalse(db.in_transaction)
        counted = db.execute("SELECT (SELECT COUNT(*) FROM download_groups), "
                             "(SELECT COUNT(*) FROM download_group_members)").fetchone()
        self.assertEqual(tuple(counted), (0, 0))
        self.assertEqual(self.groups.summaries(), [])
        self.assertEqual(self.store.get(task["id"]), page)  # every field of the page task, its state included
        self.assertEqual(self.groups.preview(task["id"]), kept)  # the same list, fingerprint, revision and draft
        self.assertEqual(self.groups.fill(), [])
        self.assertEqual([item["id"] for item in self.store.list_tasks()], [task["id"]])
        self.assertEqual(self.store.unfinished_count(), 1)  # the page still holds its place in the list

        db.executescript("DROP TRIGGER test_failing; DROP TRIGGER test_task_moved; DROP TRIGGER test_member_in; "
                         "DROP TRIGGER test_group_in;")
        created = self.create(task)

        self.assertFalse(created.replay)
        self.assertEqual(created.group["total"], 12)
        self.reopen()  # what was committed
        self.assertEqual([m["ordinal"] for m in self.groups.members(created.group["id"])], list(range(1, 13)))
        page = self.store.get(task["id"])
        self.assertEqual((page["state"], page["group_id"]), ("EXPANDED", created.group["id"]))
        self.assertIsNone(self.groups.preview(task["id"]))
        self.assertEqual(len(self.groups.fill()), 12)

    def test_a_failure_inside_the_group_write_rolls_everything_back(self):
        """The write fails after the group row and six members are in: the 7th member is aborted."""
        self.assert_a_failed_write_is_rolled_back(
            "CREATE TEMP TRIGGER test_failing BEFORE INSERT ON download_group_members WHEN NEW.ordinal = 7 "
            "BEGIN SELECT RAISE(ABORT, 'test: the member write failed'); END;",
            [("member", ordinal) for ordinal in range(1, 7)])

    def test_a_failure_moving_the_page_to_expanded_rolls_everything_back(self):
        """The write fails at the page's UPDATE … EXPANDED, after the group row and all twelve members."""
        self.assert_a_failed_write_is_rolled_back(
            "CREATE TEMP TRIGGER test_failing BEFORE UPDATE OF state ON download_tasks WHEN NEW.state = 'EXPANDED' "
            "BEGIN SELECT RAISE(ABORT, 'test: the page update failed'); END;",
            [("member", ordinal) for ordinal in range(1, 13)])

    def test_a_failure_dropping_the_stored_list_rolls_everything_back(self):
        """The write fails at its last statement, the DELETE FROM download_previews, after the group row, all
        twelve members and the page's move to EXPANDED: that move is undone too."""
        self.assert_a_failed_write_is_rolled_back(
            "CREATE TEMP TRIGGER test_failing BEFORE DELETE ON download_previews "
            "BEGIN SELECT RAISE(ABORT, 'test: the preview delete failed'); END;",
            [("member", ordinal) for ordinal in range(1, 13)] + [("task", "EXPANDED")])


class FillTest(GroupCase):
    def test_members_become_tasks_only_while_the_list_has_room(self):
        others = self.fill_list(90)
        created = self.create(self.waiting_page(series(25)))
        group_id = created.group["id"]
        new = self.groups.fill()
        self.assertEqual(len(new), 10)
        self.assertEqual(self.store.unfinished_count(), MAX_UNFINISHED_TASKS)
        self.assertEqual([t["member_id"] for t in new], [m["id"] for m in self.groups.members(group_id)[:10]])
        self.assertEqual(self.groups.fill(), [])  # no room: nothing, and nothing twice
        task = new[0]
        self.assertEqual((task["state"], task["group_id"], task["probe"]["account_file"]["variant"]),
                         ("QUEUED", group_id, "v1080"))
        self.assertTrue(task["item_key"].startswith("acct-"))
        self.assertIn("Tập 1", task["original_title"])
        # Five plain tasks finish: five more members come in, in order; the rest still waits without a task.
        for other in others[:5]:
            self.store.transition(other["id"], {"QUEUED"}, "CANCELLED")
        more = self.groups.fill()
        self.assertEqual([self.groups.member(t["member_id"])["ordinal"] for t in more], [11, 12, 13, 14, 15])
        summary = self.groups.summary(group_id)
        self.assertEqual((summary["counts"]["queued"], summary["counts"]["pending"]), (15, 10))

    def test_waiting_for_login_and_for_a_choice_count_as_unfinished(self):
        tasks = self.fill_list(98)
        self.store.transition(tasks[0]["id"], {"QUEUED"}, "WAITING_LOGIN")
        self.waiting_page(series(2))  # NEEDS_CHOICE: the 99th
        self.assertEqual(self.store.unfinished_count(), 99)
        self.store.transition(tasks[1]["id"], {"QUEUED"}, "EXPIRED")
        self.assertEqual(self.store.unfinished_count(), 98)

    def test_pending_members_survive_a_restart_and_are_never_created_twice(self):
        self.fill_list(95)
        created = self.create(self.waiting_page(series(12)))
        self.assertEqual(len(self.groups.fill()), 5)  # 95 plain tasks; the EXPANDED page holds no place
        self.reopen()
        self.assertEqual(self.groups.fill(), [])
        summary = self.groups.summary(created.group["id"])
        self.assertEqual((summary["counts"]["queued"], summary["counts"]["pending"]), (5, 7))
        ids = [m["task_id"] for m in self.groups.members(created.group["id"]) if m["task_id"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_hold_release_and_cancel_handle_the_members_without_a_task(self):
        self.fill_list(98)
        created = self.create(self.waiting_page(series(6)))
        group_id = created.group["id"]
        self.assertEqual(len(self.groups.fill()), 2)
        self.assertEqual(self.groups.hold(group_id), 4)
        self.store.transition(self.store.list_tasks()[0]["id"], {"QUEUED"}, "CANCELLED")
        self.assertEqual(self.groups.fill(), [])  # held members wait
        self.assertEqual(self.groups.release(group_id), 4)
        self.assertEqual(len(self.groups.fill()), 1)
        self.assertEqual(self.groups.cancel(group_id), 3)
        self.store.transition(self.store.list_tasks()[1]["id"], {"QUEUED"}, "CANCELLED")
        self.assertEqual(self.groups.fill(), [])  # a cancelled group creates nothing more
        summary = self.groups.summary(group_id)
        self.assertEqual((summary["state"], summary["counts"]["cancelled"], summary["counts"]["queued"]),
                         ("CANCELLED", 3, 3))

    def test_the_summary_counts_done_and_shows_a_percent_only_with_known_sizes(self):
        created = self.create(self.waiting_page(series(3)))
        group_id = created.group["id"]
        first, second, third = self.groups.fill()
        self.store.transition(first["id"], {"QUEUED"}, "COMPLETED", output_size=1000)
        self.store.update_fields(second["id"], total_bytes=1000, downloaded_bytes=500)
        summary = self.groups.summary(group_id)
        self.assertEqual((summary["done"], summary["total"], summary["percent"]), (1, 3, None))  # third unsized
        self.store.update_fields(third["id"], estimated_bytes=1000)
        self.assertEqual(self.groups.summary(group_id)["percent"], 50)
        self.assertFalse(self.groups.summary(group_id)["finished"])
        # A removed task keeps counting by its last state.
        self.store.delete_task(first["id"])
        summary = self.groups.summary(group_id)
        self.assertEqual((summary["done"], summary["counts"]["completed"]), (1, 1))

    def test_names_of_the_snapshot_and_a_retry_guard(self):
        created = self.create(self.waiting_page(series(12)))
        tasks = self.groups.fill()
        names = self.groups.names_by_task()
        self.assertEqual(names[tasks[9]["id"]]["planned_name"], f"010 - {TITLE} - S01E10")
        self.store.update_fields(tasks[9]["id"], desired_name="Tên mới")
        self.assertEqual(self.groups.names_by_task()[tasks[9]["id"]]["planned_name"], "010 - Tên mới - S01E10")
        self.assertEqual(self.groups.naming(self.store.get(tasks[0]["id"]))["code"], "S01E01")
        self.assertIsNone(self.groups.naming({"member_id": None}))
        # A cancelled episode whose file is open in another task is taken; alone it is free again.
        self.store.transition(tasks[0]["id"], {"QUEUED"}, "CANCELLED")
        self.assertFalse(self.groups.item_taken(tasks[0]["item_key"], tasks[0]["id"]))
        self.assertTrue(self.groups.item_taken(tasks[1]["item_key"], tasks[0]["id"]))
        self.assertEqual(created.group["total"], 12)

    def test_a_pasted_page_link_is_blocked_only_while_its_own_task_is_open(self):
        task = self.waiting_page(series(3))
        with self.assertRaises(Exception) as caught:
            self.store.add_tasks([task["url"]])
        self.assertIn("DUPLICATE_EXISTING", json.dumps(caught.exception.errors))
        self.create(task)
        self.groups.fill()
        self.assertEqual(len(self.store.add_tasks([task["url"]])), 1)  # the page again (episodes do not block it)

    def test_summarize_is_bounded_to_the_members_given(self):
        group = {"id": 1, "parent_task_id": None, "source_id": "alpha", "source_label": "Nguồn alpha",
                 "title": "x", "state": "ACTIVE", "mode": "all", "complete": 1, "note": None, "reasons_json": "[]",
                 "total": 2, "existing_json": None, "created_at": "t"}
        counted = [{"status": "CREATED", "task_state": "FAILED", "last_state": None, "n": 1, "unsized": 0,
                    "size_sum": 10, "done_sum": 0},
                   {"status": "CREATED", "task_state": None, "last_state": None, "n": 1, "unsized": 1,
                    "size_sum": 0, "done_sum": 0}]
        summary = summarize(group, counted, with_existing=False)
        self.assertEqual((summary["counts"]["failed"], summary["counts"]["removed"], summary["finished"]),
                         (1, 1, True))
        self.assertEqual((summary["percent"], summary["existing"], summary["existing_count"]), (0, None, 0))
        self.assertEqual(FileSelection("alpha", "f1", "s1e1", "v1").key,
                         FileSelection("alpha", "f1", "s1e1", "v1").key)


if __name__ == "__main__":
    unittest.main()
