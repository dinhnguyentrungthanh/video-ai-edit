"""Dựng lại danh sách duyệt (giữ quyết định) (docs/handoff/D-rebuild-keep.md): a new revision whose review
build carries the decisions of the queue in use; nothing else changes."""
import hashlib
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow import source_archive, source_cleanup
from biliflow.control_center import ControlCenter
from biliflow.phone_access import post_policy
from biliflow.review_rebuild import (
    CARRY_REQUEST_NAME,
    carry_request,
    discard_carry_request,
    new_revision_key,
    read_carry_request,
    rebuild_refusal,
    write_carry_request,
)
from biliflow.review_workflow import build_review_queue
from tests.test_source_cleanup import NOW, CleanupFixture, item, iso
from tests.test_source_cleanup import setUpModule as cleanup_setup
from tests.test_source_cleanup import tearDownModule as cleanup_teardown



def card(item_id, decision, decided_at=None):
    """A card with the fields every built queue gives it."""
    return {**item(item_id, decision, decided_at), "priority": "normal", "labels": [], "preview_images": []}


KEEP = card("keep", "KEEP", iso(NOW))
CUT = card("cut", "CUT", iso(NOW))
OPEN = card("open", None)


def setUpModule():
    cleanup_setup()  # no real Recycle Bin call from any test here


def tearDownModule():
    cleanup_teardown()


def _text_report(folder: Path, source: Path) -> Path:
    """A text scan with no track: the rebuilt queue has no card of its own to carry onto."""
    report = folder / "text" / "text-scan.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({
        "status": "REVIEW_REQUIRED", "input": str(source), "duration_seconds": 60.0, "sample_every_seconds": 3.0,
        "source_size": [1280, 640], "analysis_size": [960, 480], "tracks": [],
    }), encoding="utf-8")
    return report


def _decided(queue: dict) -> dict:
    return {str(x["id"]): x["decision"] for x in queue["items"] if x.get("decision") is not None}


class RefusalAndRequestTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.source = self.root / "input" / "film.mp4"
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b"film")
        self.queue = self.root / "reports" / "jobs" / "film" / "review-queue.json"
        self.queue.parent.mkdir(parents=True)
        self.queue.write_text(json.dumps({"source": {"path": str(self.source), "sha256": "a" * 64},
                                          "items": [KEEP, CUT, OPEN]}), encoding="utf-8")
        self.job = {"id": 7, "job_key": "film", "state": "WAITING_REVIEW", "source_path": str(self.source),
                    "source_sha256": "a" * 64, "active_queue_path": "reports/jobs/film/review-queue.json",
                    "active_revision": 3}

    def refusal(self, *, in_flight=False, busy=False, in_process=False, source_present=True, **job):
        return rebuild_refusal({**self.job, **job}, in_flight=in_flight, busy=busy, in_process=in_process,
                               source_present=source_present)

    def test_only_a_reviewed_or_exported_job_with_its_queue_and_source_is_rebuilt(self):
        for state in ("WAITING_REVIEW", "READY_TO_EXPORT", "COMPLETED"):
            self.assertIsNone(self.refusal(state=state), state)
        cases = {
            "SKIPPED": ({"state": "SKIPPED"}, "Mở lại để xuất"),
            "export": ({"in_flight": True}, "chờ xuất hoặc đang xuất"),
            "queued": ({"state": "QUEUED"}, "trong hàng đợi"),
            "scanning": ({"state": "SCANNING_TEXT", "in_process": True}, "đang được xử lý"),
            "busy": ({"busy": True}, "đang được xử lý"),
            "paused": ({"state": "PAUSED"}, "Tạm dừng"),
            "failed": ({"state": "FAILED"}, "Lỗi"),
            "cancelled": ({"state": "CANCELLED"}, "Đã hủy"),
            "no queue": ({"active_queue_path": None}, "Chạy lại kiểm tra"),
            "no source": ({"source_present": False}, "video gốc"),
        }
        for name, (values, words) in cases.items():
            with self.subTest(case=name):
                self.assertIn(words, self.refusal(**values) or "")

    def test_the_request_names_the_queue_in_use_and_is_read_back_only_while_it_is_unchanged(self):
        request = carry_request(self.root, self.job, self.queue, now=datetime(2026, 10, 17, 9, 0))
        self.assertEqual({key: request[key] for key in ("from_queue", "from_revision", "decisions", "job_id")},
                         {"from_queue": "reports/jobs/film/review-queue.json", "from_revision": 3,
                          "decisions": 2, "job_id": 7})
        self.assertEqual(request["from_sha256"], hashlib.sha256(self.queue.read_bytes()).hexdigest())
        key = new_revision_key(self.root, "film", now=datetime(2026, 10, 17, 9, 0))
        path = write_carry_request(self.root, key, request)
        new_queue = path.parent / "review-queue.json"
        read, queue = read_carry_request(self.root, new_queue)
        self.assertEqual((read, _decided(queue)), (request, {"keep": "KEEP", "cut": "CUT"}))
        self.assertIsNone(read_carry_request(self.root, self.queue))  # no request beside the old queue
        # A second folder in the same second gets its own name; write never reuses a folder.
        self.assertEqual(new_revision_key(self.root, "film", now=datetime(2026, 10, 17, 9, 0)), key + "-2")
        with self.assertRaises(FileExistsError):
            write_carry_request(self.root, key, request)
        # The old queue changed since the click: refused, never guessed.
        self.queue.write_text(self.queue.read_text(encoding="utf-8") + " ", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "đã đổi sau khi bấm dựng lại"):
            read_carry_request(self.root, new_queue)
        for broken, words in (({**request, "from_queue": "input/film.mp4"}, "ngoài reports"),
                              ({**request, "version": 2}, "phiên bản")):
            path.write_text(json.dumps(broken), encoding="utf-8")
            with self.subTest(words=words), self.assertRaisesRegex(ValueError, words):
                read_carry_request(self.root, new_queue)
        discard_carry_request(path)
        self.assertFalse(path.parent.exists())

    def test_a_queue_of_another_source_gives_no_request(self):
        with self.assertRaisesRegex(ValueError, "không khớp video nguồn"):
            carry_request(self.root, {**self.job, "source_sha256": "b" * 64}, self.queue)


class BuildWithRequestTests(unittest.TestCase):
    """build_review_queue itself, as the pipeline's build-review runs it in the new revision."""

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / "input").mkdir()
        self.source = self.root / "input" / "film.mp4"
        self.source.write_bytes(b"film")
        old = self.root / "reports" / "jobs" / "film"
        old.mkdir(parents=True)
        self.old_queue = old / "review-queue.json"
        self.old_queue.write_text(json.dumps({
            "source": {"path": str(self.source), "sha256": None},
            "items": [KEEP, CUT, OPEN], "audit_log": [{"action": "DECIDE", "id": "keep"}],
        }), encoding="utf-8")
        job = {"id": 1, "source_path": str(self.source), "source_sha256": "a" * 64, "active_revision": 1}
        self.request = carry_request(self.root, job, self.old_queue)
        self.folder = write_carry_request(self.root, "film-run-20261017-090000", self.request).parent
        self.report = _text_report(self.folder, self.source)
        self.new_queue = self.folder / "review-queue.json"

    def build(self):
        return build_review_queue(project_root=self.root, report_paths=[self.report], queue_path=self.new_queue)

    def test_the_new_queue_keeps_every_old_decision_and_invents_none(self):
        before = self.old_queue.read_bytes()
        payload = self.build()
        self.assertEqual(_decided(payload), {"keep": "KEEP", "cut": "CUT"})
        # Each decision is an old one, never a new card's own: a fresh build of these reports has none.
        self.assertTrue(all(x.get("migration_status") or x.get("decision_carried_from")
                            for x in payload["items"] if x.get("decision")))
        self.assertNotIn("open", [x["id"] for x in payload["items"]])  # an old open card stays behind
        carry = payload["decision_carry"]
        self.assertEqual({key: carry[key] for key in ("previous_decisions", "restored_unchanged", "from_revision",
                                                      "from_queue")},
                         {"previous_decisions": 2, "restored_unchanged": 2, "from_revision": 1,
                          "from_queue": "reports/jobs/film/review-queue.json"})
        self.assertEqual(carry["restored_reasons"]["no_match"], 2)
        self.assertEqual([entry["action"] for entry in payload["audit_log"]], ["DECIDE", "REBUILD_KEEP_DECISIONS"])
        self.assertEqual(self.old_queue.read_bytes(), before)  # the old revision is never written

    def test_an_unusable_request_stops_the_build_before_a_queue_is_written(self):
        self.old_queue.write_text(self.old_queue.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "đã đổi"):
            self.build()
        self.assertFalse(self.new_queue.exists())
        other = self.root / "input" / "other.mp4"
        other.write_bytes(b"other")
        _text_report(self.folder, other)
        self.old_queue.write_text(json.dumps({"source": {"path": str(self.source)}, "items": [KEEP]}),
                                  encoding="utf-8")
        request = {**self.request, "from_sha256": hashlib.sha256(self.old_queue.read_bytes()).hexdigest()}
        (self.folder / CARRY_REQUEST_NAME).write_text(json.dumps(request), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "không khớp video nguồn"):
            self.build()
        self.assertFalse(self.new_queue.exists())

    def test_without_a_request_the_build_starts_fresh_as_before(self):
        (self.folder / CARRY_REQUEST_NAME).unlink()
        payload = self.build()
        self.assertEqual(_decided(payload), {})
        self.assertNotIn("decision_carry", payload)


class ControlCenterRebuildTests(CleanupFixture):
    """The route's method on a real JobStore and JobScheduler (never started) in a temp root."""

    def setUp(self):
        super().setUp()
        center = ControlCenter.__new__(ControlCenter)
        center.root, center.store, center.scheduler = self.root, self.store, self.scheduler
        self.center = center

    def finish_build(self, job_id):
        """What the pipeline does next: the scan reports land (from the cache), build-review runs, the
        scheduler activates the revision."""
        job = self.store.get_job(job_id)
        folder = self.root / "reports" / "jobs" / self.store.setting(f"pipeline_key:{job_id}")
        report = _text_report(folder, Path(job["source_path"]))
        build_review_queue(project_root=self.root, report_paths=[report], queue_path=folder / "review-queue.json")
        self.scheduler._after_success(job_id, "build_review", None)
        return self.store.get_job(job_id)

    def report_folders(self):
        return sorted(path.name for path in (self.root / "reports" / "jobs").iterdir())

    def test_a_rebuild_queues_a_new_revision_with_the_request_and_keeps_the_old_one(self):
        job_id = self.make_job("film", state="WAITING_REVIEW", items=[KEEP, CUT, OPEN], status="REVIEW_REQUIRED")
        old_bytes = self.queue_file(job_id).read_bytes()
        result = self.center.rebuild_review(job_id)
        key = self.store.setting(f"pipeline_key:{job_id}")
        self.assertEqual((result["state"], result["rebuild"]["decisions"], result["rebuild"]["pipeline_key"]),
                         ("QUEUED", 2, key))
        self.assertTrue(key.startswith("film-run-"))
        request = json.loads((self.root / "reports" / "jobs" / key / CARRY_REQUEST_NAME).read_text(encoding="utf-8"))
        self.assertEqual((request["from_queue"], request["from_revision"]), ("reports/jobs/film/review-queue.json", 1))
        self.assertIsNone(self.store.get_job(job_id)["active_queue_path"])  # as "Chạy lại"
        self.assertEqual((self.root / "reports" / "jobs" / "film" / "review-queue.json").read_bytes(), old_bytes)
        self.assertEqual(len(self.events(job_id, "REVIEW_REBUILD_QUEUED")), 1)
        with self.assertRaisesRegex(ValueError, "trong hàng đợi"):  # a second click while it waits
            self.center.rebuild_review(job_id)
        job = self.finish_build(job_id)
        # The fixture's revision 1 has no job_revisions row, so the store numbers the new one from 1.
        self.assertEqual(job["active_queue_path"], f"reports/jobs/{key}/review-queue.json")
        self.assertIsNotNone(job["active_revision"])
        self.assertEqual(_decided(self.queue(job_id)), {"keep": "KEEP", "cut": "CUT"})
        self.assertIn(job["state"], {"WAITING_REVIEW", "READY_TO_EXPORT"})

    def test_an_exported_job_returns_to_review_and_its_source_cannot_be_deleted_or_archived_until_exported_again(self):
        decided = iso(NOW - timedelta(hours=1))
        job_id = self.make_exported_job("film", items=[card("a", "KEEP", decided), card("b", "KEEP", decided)])
        output = self.output_of(job_id)
        output_bytes = output.read_bytes()

        def assessments():
            job = self.store.get_job(job_id)
            return (source_cleanup.assess_job(self.root, self.store, self.scheduler, job, fresh=True),
                    source_archive.assess_archive(self.root, self.store, self.scheduler, job, fresh=True))

        cleanup, archive = assessments()
        self.assertTrue(cleanup.eligible, cleanup.reason)
        self.assertTrue(archive.eligible, archive.reason)
        self.center.rebuild_review(job_id)
        for when in ("queued", "rebuilt"):
            if when == "rebuilt":
                job = self.finish_build(job_id)
                self.assertIn(job["state"], {"WAITING_REVIEW", "READY_TO_EXPORT"})
            cleanup, archive = assessments()
            with self.subTest(when=when):
                self.assertFalse(cleanup.eligible)
                self.assertFalse(archive.eligible)
        self.assertEqual(_decided(self.queue(job_id)), {"a": "KEEP", "b": "KEEP"})
        self.assertEqual(output.read_bytes(), output_bytes)  # the old export stays in output

    def test_a_refused_rebuild_writes_nothing(self):
        cases = {
            "skipped": lambda: self.make_skipped_job("skipped"),
            "queued": lambda: self.make_job("queued", state="QUEUED", items=[KEEP]),
            "rendering": lambda: self.make_job("rendering", state="RENDERING", items=[KEEP]),
            "paused": lambda: self.make_job("paused", state="PAUSED", items=[KEEP]),
            "failed": lambda: self.make_job("failed", state="FAILED", items=[KEEP]),
            "cancelled": lambda: self.make_job("cancelled", state="CANCELLED", items=[KEEP]),
        }
        for name, make in cases.items():
            with self.subTest(case=name):
                job_id = make()
                self.assert_refused(job_id)
        missing = self.make_job("missing", state="WAITING_REVIEW", items=[KEEP])
        self.source_of(missing).unlink()
        self.assert_refused(missing, "video gốc")
        cleaned = self.make_job("cleaned", state="COMPLETED", items=[KEEP])
        self.add_row(cleaned, state="PENDING")
        self.assert_refused(cleaned)
        no_style = self.make_job("nostyle", state="WAITING_REVIEW", items=[KEEP])
        self.store.update_job(no_style, content_style="unknown")
        self.assert_refused(no_style, "loại nội dung")  # refused by rerun itself: the request is taken back

    def assert_refused(self, job_id, words=None):
        before = (self.store.get_job(job_id), self.report_folders(), self.store.setting(f"pipeline_key:{job_id}"))
        with self.assertRaises(ValueError) as caught:
            self.center.rebuild_review(job_id)
        if words:
            self.assertIn(words, str(caught.exception))
        after = (self.store.get_job(job_id), self.report_folders(), self.store.setting(f"pipeline_key:{job_id}"))
        self.assertEqual(after, before)
        self.assertEqual(self.events(job_id, "REVIEW_REBUILD_QUEUED"), [])

    def test_the_route_works_from_the_phone_as_rerun_does(self):
        self.assertEqual(post_policy("/api/jobs/5/rebuild-review"), post_policy("/api/jobs/5/rerun"))
        self.assertTrue(post_policy("/api/jobs/5/rebuild-review")[0])


if __name__ == "__main__":
    unittest.main()
