"""The "Bộ nhớ logo" page and API (batch 4a, 2026-10-03): list, frames, re-class, delete."""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import cv2
import numpy as np

from biliflow import logo_memory_admin as admin
from biliflow.brand_memory import (
    STUDIO_LOGO_MEMORY_PATH,
    load_studio_logo_memory,
    perceptual_hash,
    prepare_studio_logo_frames,
    remember_studio_logo,
    studio_logo_grid,
)
from biliflow.export_guards import REVIEW_QUEUE_IO
from biliflow.platform_memory import convert_logo_memory_class

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.environ.get("BILIFLOW_TEST_DATA_ROOT") or ROOT)
SIZE = (134, 320)


def _ident_frame(shade):
    frame = np.zeros(SIZE + (3,), dtype=np.uint8)
    frame[49:83:2, 129:187] = (90, 230 - 8 * shade, 120)
    frame[49:83, 129:132] = (240, 240, 240)
    return frame


def _licence_frame(shade):
    frame = np.full(SIZE + (3,), 60 + shade, dtype=np.uint8)
    cv2.putText(frame, "LICENCE 2026", (40, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    return frame


def _jpeg(frame):
    ok, encoded = cv2.imencode(".jpg", cv2.cvtColor(frame, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])
    assert ok
    return encoded.tobytes()


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _js_function(page, name):
    match = re.search(rf"(?:async )?function {re.escape(name)}\(", page)
    start = page.index("{", match.end())
    depth = 0
    for index in range(start, len(page)):
        depth += {"{": 1, "}": -1}.get(page[index], 0)
        if depth == 0:
            return page[match.start():index + 1]
    raise AssertionError(name)


class LogoMemoryAdminTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root / "reports").mkdir()
        self.ident = self.remember("review-ident", _ident_frame)
        self.licence = self.remember("review-licence", _licence_frame)

    def tearDown(self):
        self.temporary.cleanup()

    def remember(self, item_id, draw, *, sha="film"):
        frames = [(round(10.0 + index * 0.04, 3), draw(index)) for index in range(8)]
        queue = {"source": {"sha256": sha, "path": str(self.root / "missing.mp4")}, "items": []}
        item = {"id": item_id, "category": "visual_logo", "candidate_type": "opening_boundary", "decision": "KEEP",
                "start_seconds": 10.0, "end_seconds": 10.32, "preview_images": [],
                "suggested_region_source_pixels": None, "labels": ["logo"]}
        window = {"frames": [(moment, _jpeg(frame)) for moment, frame in frames], "frames_source": "source_video",
                  "reason": None, "pipeline": {"analysis_size": [SIZE[1], SIZE[0]], "fps": 25.0}}
        prepared = prepare_studio_logo_frames(self.root, queue, item, window, ignored_regions=[])
        signature = {"preview": "x", "phash": perceptual_hash(frames[0][1]), "grid": studio_logo_grid(frames[0][1])}
        return remember_studio_logo(self.root, queue, item, [signature], window_text={"covered": True, "texts": []},
                                    frames=prepared)

    @property
    def memory(self):
        return self.root / STUDIO_LOGO_MEMORY_PATH

    def jobs_database(self, rows):
        database = self.root / "state" / "control-center.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("create table jobs (id integer primary key, source_path text, source_sha256 text)")
            connection.executemany("insert into jobs values (?,?,?)", rows)
        connection.close()

    def test_list_shows_both_classes_with_frames_and_proposals(self):
        seeded = self.remember("review-other", _ident_frame, sha="export")
        convert_logo_memory_class(self.root, [seeded["key"]], to="platform_logo", platform="iqiyi", apply=True)
        self.jobs_database([(7, r"E:\BiliFlow\input\Tập 17.mp4", "film")])
        before = _sha(self.memory)
        listing = admin.list_logo_memory(self.root)
        self.assertEqual(listing["memory_sha256"], before)
        self.assertEqual(_sha(self.memory), before, "listing writes nothing")
        ident, licence, platform = listing["records"]
        self.assertEqual((ident["key"], ident["memory_class"], ident["decision"]),
                         ("film:review-ident", "studio_logo", "KEEP"))
        self.assertEqual(ident["episode"], {"job_id": 7, "name": "Tập 17.mp4"})
        self.assertEqual((ident["frames"], ident["logo_frames"], ident["convertible"]), (8, 8, True))
        self.assertEqual(len(ident["proposed_blur_region"]["box"]), 4)
        self.assertEqual(len(ident["frame_urls"]), 6)
        self.assertTrue(ident["frame_urls"][0].startswith("/api/logo-memory/frame?key=film%3Areview-ident&i="))
        self.assertEqual((licence["convertible"], licence["refusal"], licence["logo_frames"]),
                         (False, "no_logo_frames", 0))
        self.assertTrue(licence["refusal_text"].startswith("Không thấy khung nào có logo trên nền tối"))
        self.assertEqual((platform["memory_class"], platform["decision"], platform["platform"]),
                         ("platform_logo", "BLUR", {"key": "iqiyi", "name": "iQIYI"}))
        self.assertIsNone(platform["episode"], "an export's sha is no job")
        self.assertEqual((platform["logo_frames"], platform["convertible"]), (8, True))
        self.assertEqual(platform["converted_from"]["memory_class"], "studio_logo")
        self.assertEqual(listing["backups"][0]["path"].split("/")[:2], ["state", "backups"])
        self.assertTrue(listing["backups"][0]["path"].endswith(".json"))

    def test_frame_returns_only_the_records_own_jpegs(self):
        stored = self.ident["stored_frames"]
        self.assertEqual(admin.logo_memory_frame(self.root, self.ident["key"], 1),
                         (self.root / stored[1]["image"]).read_bytes())
        status, body, kind = admin.handle_get(self.root, "/api/logo-memory/frame", "key=film%3Areview-ident&i=0")
        self.assertEqual((status, kind), (200, "image/jpeg"))
        self.assertEqual(body[:2], b"\xff\xd8")
        for query, expected in (("key=film%3Areview-ident&i=99", 404), ("key=film%3Anone&i=0", 404),
                                ("key=film%3Areview-ident&i=x", 400), ("i=0", 400)):
            with self.subTest(query=query):
                status, body, kind = admin.handle_get(self.root, "/api/logo-memory/frame", query)
                self.assertEqual((status, kind), (expected, "application/json; charset=utf-8"))
                self.assertIn("error", json.loads(body))
        # A record pointing outside state/studio-logo-frames is never served.
        memory = json.loads(self.memory.read_text(encoding="utf-8"))
        (self.root / "secret.jpg").write_bytes(b"\xff\xd8secret")
        memory["records"][0]["stored_frames"][0]["image"] = "state/studio-logo-frames/../../secret.jpg"
        memory["records"][0]["stored_frames"][1]["image"] = str(self.root / "secret.jpg")
        memory["records"][0]["stored_frames"][2]["image"] = "state/studio-logo-memory.json"
        self.memory.write_text(json.dumps(memory), encoding="utf-8")
        for index in (0, 1, 2):
            with self.subTest(index=index):
                with self.assertRaises(admin.FrameOutsideMemory):
                    admin.logo_memory_frame(self.root, self.ident["key"], index)
                status, _, _ = admin.handle_get(self.root, "/api/logo-memory/frame", f"key=film%3Areview-ident&i={index}")
                self.assertEqual(status, 403)

    def test_a_locked_frame_file_is_a_server_error_not_a_refusal(self):
        original = Path.read_bytes

        def locked(path):
            if path.suffix == ".jpg":
                raise PermissionError(13, "The process cannot access the file")
            return original(path)

        with mock.patch.object(Path, "read_bytes", locked):
            status, body, _ = admin.handle_get(self.root, "/api/logo-memory/frame", "key=film%3Areview-ident&i=0")
        self.assertEqual(status, 500)
        self.assertIn("Không đọc được bộ nhớ logo", json.loads(body)["error"])

    def test_broken_frame_files_never_break_the_listing(self):
        (self.root / self.ident["stored_frames"][0]["image"]).write_bytes(b"")
        status, body, _ = admin.handle_get(self.root, "/api/logo-memory", "")
        self.assertEqual(status, 200)
        [ident] = [record for record in json.loads(body)["records"] if record["key"] == self.ident["key"]]
        self.assertTrue(ident["convertible"], "the other seven frames still show the logo")

    def test_unexpected_errors_become_json_500(self):
        with mock.patch.object(admin, "list_logo_memory", side_effect=TypeError("bad record")):
            status, body, kind = admin.handle_get(self.root, "/api/logo-memory", "")
        self.assertEqual((status, kind), (500, admin.JSON_TYPE))
        self.assertIn("bad record", json.loads(body)["error"])
        with mock.patch.object(admin, "delete_logo_memory", side_effect=AttributeError("odd")):
            status, payload = admin.handle_post(self.root, "/api/logo-memory/delete",
                                                {"key": self.ident["key"], "expected_sha256": "s"})
        self.assertEqual(status, 500)
        self.assertIn("odd", payload["error"])

    def test_class_change_backs_up_then_flips_and_back(self):
        snapshot = self.memory.read_bytes()
        result = admin.set_logo_memory_class(self.root, self.ident["key"], "platform_logo", platform="iqiyi",
                                             expected_sha256=hashlib.sha256(snapshot).hexdigest())
        self.assertEqual(result["status"], "converted")
        self.assertEqual((self.root / result["backup"]).read_bytes(), snapshot)
        self.assertEqual(result["memory_sha256"], _sha(self.memory))
        self.assertEqual(result["record"]["memory_class"], "platform_logo")
        record = load_studio_logo_memory(self.root)["records"][0]
        self.assertEqual((record["memory_class"], record["decision"]), ("platform_logo", "BLUR"))
        self.assertEqual(record["converted_from"]["by"], "logo-memory-page")
        again = admin.set_logo_memory_class(self.root, self.ident["key"], "platform_logo", platform="iqiyi",
                                            expected_sha256=result["memory_sha256"])
        self.assertEqual((again["status"], again["backup"]), ("already", None))
        back = admin.set_logo_memory_class(self.root, self.ident["key"], "studio_logo",
                                           expected_sha256=result["memory_sha256"])
        self.assertEqual(back["status"], "converted")
        self.assertEqual(load_studio_logo_memory(self.root)["records"][0]["memory_class"], "studio_logo")
        sha = _sha(self.memory)
        with self.assertRaisesRegex(ValueError, "Không thấy khung nào có logo"):
            admin.set_logo_memory_class(self.root, self.licence["key"], "platform_logo", platform="iqiyi",
                                        expected_sha256=sha)
        with self.assertRaisesRegex(ValueError, "platform"):
            admin.set_logo_memory_class(self.root, self.ident["key"], "platform_logo", platform="netflix",
                                        expected_sha256=sha)
        with self.assertRaisesRegex(ValueError, "Loại bộ nhớ"):
            admin.set_logo_memory_class(self.root, self.ident["key"], "brand", expected_sha256=sha)
        self.assertEqual(_sha(self.memory), sha, "refusals write nothing")

    def test_stale_sha_is_a_conflict_and_writes_nothing(self):
        before = self.memory.read_bytes()
        folder = self.root / self.ident["frames_folder"]
        stale = "0" * 64
        status, payload = admin.handle_post(self.root, "/api/logo-memory/class", {
            "key": self.ident["key"], "memory_class": "platform_logo", "platform": "iqiyi", "expected_sha256": stale})
        self.assertEqual((status, payload["code"]), (409, "memory_changed"))
        status, payload = admin.handle_post(self.root, "/api/logo-memory/delete",
                                            {"key": self.ident["key"], "expected_sha256": stale})
        self.assertEqual((status, payload["code"]), (409, "memory_changed"))
        self.assertEqual(self.memory.read_bytes(), before)
        self.assertTrue(folder.is_dir())
        self.assertFalse((self.root / "state" / "backups").exists())

    def test_delete_moves_frames_to_backups(self):
        snapshot = self.memory.read_bytes()
        folder = self.root / self.ident["frames_folder"]
        files = sorted(path.name for path in folder.iterdir())
        status, result = admin.handle_post(self.root, "/api/logo-memory/delete", {
            "key": self.ident["key"], "expected_sha256": hashlib.sha256(snapshot).hexdigest()})
        self.assertEqual(status, 200, result)
        self.assertEqual([record["key"] for record in load_studio_logo_memory(self.root)["records"]],
                         [self.licence["key"]])
        self.assertEqual((self.root / result["backup"]).read_bytes(), snapshot)
        moved = self.root / result["frames_backup"]
        self.assertRegex(moved.parent.name, r"^studio-logo-frames-\d{8}-\d{6}(-\d+)?$")
        self.assertEqual(moved.parent.parent, self.root / "state" / "backups")
        self.assertEqual(sorted(path.name for path in moved.iterdir()), files, "frames are moved, not deleted")
        self.assertFalse(folder.exists())
        self.assertTrue((self.root / self.licence["frames_folder"]).is_dir())
        self.assertEqual(result["memory_sha256"], _sha(self.memory))
        status, payload = admin.handle_post(self.root, "/api/logo-memory/delete", {
            "key": self.ident["key"], "expected_sha256": result["memory_sha256"]})
        self.assertEqual(status, 404, payload)
        self.assertIsNone(result["warning"])

    def test_delete_writes_the_memory_before_moving_frames(self):
        snapshot = self.memory.read_bytes()
        folder = self.root / self.ident["frames_folder"]
        files = sorted(path.name for path in folder.iterdir())
        body = {"key": self.ident["key"], "expected_sha256": hashlib.sha256(snapshot).hexdigest()}
        # The memory write fails: nothing has moved, the record and its frames stay together.
        with mock.patch.object(admin, "_write_studio_logo_memory", side_effect=PermissionError(13, "in use")):
            status, payload = admin.handle_post(self.root, "/api/logo-memory/delete", body)
        self.assertEqual(status, 500, payload)
        self.assertEqual(self.memory.read_bytes(), snapshot)
        self.assertEqual(sorted(path.name for path in folder.iterdir()), files)
        self.assertEqual(list((self.root / "state" / "backups").glob("studio-logo-frames-*")), [])
        # The frames folder is in use: the record is gone, its frames stay whole where they were.
        with mock.patch("biliflow.platform_memory.os.rename", side_effect=PermissionError(13, "in use")):
            status, result = admin.handle_post(self.root, "/api/logo-memory/delete", body)
        self.assertEqual(status, 200, result)
        self.assertEqual([record["key"] for record in load_studio_logo_memory(self.root)["records"]],
                         [self.licence["key"]])
        self.assertIsNone(result["frames_backup"])
        self.assertEqual(result["frames_left"], self.ident["frames_folder"])
        self.assertIn("Chưa chuyển được thư mục ảnh khung hình", result["warning"])
        self.assertEqual(sorted(path.name for path in folder.iterdir()), files)
        self.assertEqual(list((self.root / "state" / "backups").glob("studio-logo-frames-*")), [],
                         "no empty frames backup is left behind")

    def assert_waits_for_the_lock(self, call):
        held, released, finished = threading.Event(), threading.Event(), threading.Event()

        def hold():
            with REVIEW_QUEUE_IO:
                held.set()
                released.wait(10)

        holder = threading.Thread(target=hold)
        holder.start()
        held.wait(10)
        worker = threading.Thread(target=lambda: (call(), finished.set()))
        worker.start()
        try:
            self.assertFalse(finished.wait(0.3), "waits while a review decision holds the lock")
        finally:
            released.set()
            holder.join(10)
            worker.join(10)
        self.assertTrue(finished.is_set())

    def test_writes_hold_the_review_queue_lock(self):
        sha = _sha(self.memory)
        self.assert_waits_for_the_lock(lambda: admin.set_logo_memory_class(
            self.root, self.ident["key"], "platform_logo", platform="iqiyi", expected_sha256=sha))

    def test_reads_hold_the_review_queue_lock(self):
        # An open reader makes a writer's replace or rename fail on Windows.
        self.assert_waits_for_the_lock(lambda: admin.list_logo_memory(self.root))
        self.assert_waits_for_the_lock(lambda: admin.logo_memory_frame(self.root, self.ident["key"], 0))

    def test_dispatch_routes_and_ignores_other_paths(self):
        status, page, kind = admin.handle_get(self.root, "/logo-memory", "")
        self.assertEqual((status, kind), (200, "text/html; charset=utf-8"))
        text = page.decode("utf-8")
        self.assertIn("<title>Bộ nhớ logo", text)
        for fragment in ("'/api/logo-memory'", "'/api/logo-memory/class'", "'/api/logo-memory/delete'",
                         "'/api/session'", "X-BiliFlow-Token", '"iqiyi":"iQIYI"'):
            self.assertIn(fragment, text)
        status, body, kind = admin.handle_get(self.root, "/api/logo-memory", "")
        self.assertEqual((status, json.loads(body)["memory_sha256"]), (200, _sha(self.memory)))
        self.assertIsNone(admin.handle_get(self.root, "/api/status", ""))
        self.assertIsNone(admin.handle_post(self.root, "/api/scheduler", {}))
        for body in ({"expected_sha256": "x"}, {"key": self.ident["key"]}, {"key": self.ident["key"],
                                                                          "expected_sha256": 5}):
            with self.subTest(body=body):
                status, payload = admin.handle_post(self.root, "/api/logo-memory/delete", body)
                self.assertEqual(status, 400, payload)
        empty = self.root / "empty"
        empty.mkdir()
        status, body, _ = admin.handle_get(empty, "/api/logo-memory", "")
        self.assertEqual(json.loads(body), {"memory_sha256": None, "records": [], "backups": []})
        broken = self.root / "broken"
        (broken / "state").mkdir(parents=True)
        (broken / STUDIO_LOGO_MEMORY_PATH).write_text("{", encoding="utf-8")
        status, body, _ = admin.handle_get(broken, "/api/logo-memory", "")
        self.assertEqual(status, 500)
        self.assertIn("Không đọc được bộ nhớ logo", json.loads(body)["error"])

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_page_script_runs_in_node(self):
        page = admin.logo_memory_page()
        script = re.search(r"<script>(.*?)</script>", page, re.S).group(1)
        checked = subprocess.run([shutil.which("node"), "--check", "-"], input=script, capture_output=True,
                                 text=True, encoding="utf-8", timeout=60)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        listing = admin.list_logo_memory(self.root)
        names = ("recordHtml", "className", "clock", "baseName", "boxText", "platformOptions", "backupsHtml")
        source = (
            re.search(r"const PLATFORM_NAMES=[^;]*;", page).group(0)
            + "const esc=s=>String(s??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));"
            + "\n".join(_js_function(page, name) for name in names)
            + f";const listing={json.dumps(listing)};"
            "const saved=[{path:'state/backups/studio-logo-memory-20261003-120000.json',kind:'memory',"
            "modified_at:'2026-10-03T12:00:00+07:00'},{path:'state/backups/studio-logo-frames-20261003-120001',"
            "kind:'frames',modified_at:'2026-10-03T12:00:01+07:00'}];"
            "console.log(JSON.stringify([...listing.records.map(recordHtml),backupsHtml(saved),backupsHtml([])]));"
        )
        result = subprocess.run([shutil.which("node"), "-e", source], capture_output=True, text=True,
                                encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        ident, licence, backups, no_backups = json.loads(result.stdout)
        self.assertIn("Logo hãng phim", ident)
        self.assertIn('data-act="platform"', ident)
        self.assertIn("Đổi thành logo nền tảng (làm mờ)", ident)
        self.assertIn('<option value="iqiyi" selected>iQIYI</option>', ident)
        self.assertIn("8 khung có logo trên nền tối", ident)
        self.assertEqual(ident.count("<img "), 6)
        self.assertIn('data-act="delete"', ident)
        self.assertNotIn('data-act="platform"', licence)
        self.assertIn("Không đổi được thành logo nền tảng: Không thấy khung nào có logo trên nền tối", licence)
        self.assertIn("Chưa có bản sao lưu nào", no_backups)
        self.assertIn("<li>state/backups/studio-logo-memory-20261003-120000.json · 2026-10-03T12:00:00+07:00</li>",
                      backups)
        self.assertIn("studio-logo-frames-20261003-120001 · ảnh khung hình đã chuyển ra khi xóa", backups)


class RealLogoMemoryListingTests(unittest.TestCase):
    """Read-only: the project's memory listed through the page API; nothing is written."""

    def test_real_memory_listing_is_read_only(self):
        memory = DATA_ROOT / STUDIO_LOGO_MEMORY_PATH
        if not memory.is_file():
            self.skipTest("the project's studio-logo memory is not available")
        before = _sha(memory)
        frames = DATA_ROOT / "state" / "studio-logo-frames"
        listing_before = sorted(str(path) for path in frames.rglob("*"))
        status, body, _ = admin.handle_get(DATA_ROOT, "/api/logo-memory", "")
        self.assertEqual(status, 200)
        listing = json.loads(body)
        self.assertEqual(listing["memory_sha256"], before)
        by_item = {record["key"].split(":", 1)[1]: record for record in listing["records"]}
        for item_id in ("review-dd6d551760b5", "review-571a51c78be4", "review-bfbb9423ac85"):
            record = by_item.get(item_id)
            if record is None or record["memory_class"] != "studio_logo":
                continue
            self.assertTrue(record["convertible"], item_id)
            self.assertGreaterEqual(record["logo_frames"], 55)
        for item_id in ("review-9445dc481911", "review-f2cfc08075db", "review-fa81506bc042"):
            if item_id in by_item:
                self.assertEqual(by_item[item_id]["refusal"], "no_logo_frames")
        first = listing["records"][0]
        query = first["frame_urls"][0].split("?", 1)[1]
        status, jpeg, kind = admin.handle_get(DATA_ROOT, "/api/logo-memory/frame", query)
        self.assertEqual((status, kind, jpeg[:2]), (200, "image/jpeg", b"\xff\xd8"))
        self.assertEqual(_sha(memory), before)
        self.assertEqual(sorted(str(path) for path in frames.rglob("*")), listing_before)


if __name__ == "__main__":
    unittest.main()
