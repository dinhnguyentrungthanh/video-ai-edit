from contextlib import closing

import hashlib
import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.performance_report import summarize_job


class PerformanceReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "state").mkdir()
        self.database = self.root / "state/control-center.sqlite3"
        with closing(sqlite3.connect(self.database)) as c, c:
            c.executescript("""
                CREATE TABLE jobs(id, state, active_queue_path, duration_seconds, profile);
                INSERT INTO jobs VALUES(1,'WAITING_REVIEW','reports/run/review-queue.json',60,'careful');
                CREATE TABLE stages(job_id,name,ordinal,state,attempt,started_at,completed_at);
                INSERT INTO stages VALUES(1,'text',1,'COMPLETED',2,
                    '2026-09-28T01:00:00+07:00','2026-09-28T01:00:10+07:00');
                CREATE TABLE events(job_id,event_type,payload_json,created_at);
                CREATE TABLE artifacts(job_id,stage_name,path);
                INSERT INTO artifacts VALUES(1,'text','reports/run/text-scan.json');
                INSERT INTO artifacts VALUES(1,'text','reports/old/text-scan.json');
            """)
        directory = self.root / "reports/run"
        directory.mkdir(parents=True)
        (directory / "text-scan.json").write_text(json.dumps({
            "metrics": {"performance": {"total_wall_seconds": 100}},
        }), encoding="utf-8")

    def add_cache_event(self, timestamp):
        with closing(sqlite3.connect(self.database)) as c, c:
            c.execute("INSERT INTO events VALUES(?,?,?,?)", (
                1, "STAGE_CACHE_HIT", '{"stage":"text"}', timestamp,
            ))

    def test_read_only_and_old_cache_hit_does_not_mark_current_attempt(self):
        self.add_cache_event("2026-09-28T00:00:00+07:00")
        before = hashlib.sha256(self.database.read_bytes()).digest()
        result = summarize_job(self.root, 1)
        self.assertEqual(before, hashlib.sha256(self.database.read_bytes()).digest())
        self.assertEqual(result["completed_stage_wall_seconds"], 10)
        self.assertFalse(result["stages"][0]["cache_hit"])
        self.assertEqual(len(result["stages"][0]["profiles"]), 1)

    def test_cache_report_timings_are_labelled_historical(self):
        self.add_cache_event("2026-09-28T01:00:05+07:00")
        result = summarize_job(self.root, 1)
        stage = result["stages"][0]
        self.assertTrue(stage["cache_hit"])
        self.assertTrue(stage["profiles"][0]["historical_on_cache_hit"])
        # Do not add old scan's 100 seconds to this attempt's 10 seconds.
        self.assertEqual(result["completed_stage_wall_seconds"], 10)

    def test_old_report_and_unfinished_stage_are_not_zero_duration(self):
        (self.root / "reports/run/text-scan.json").write_text('{}', encoding="utf-8")
        with closing(sqlite3.connect(self.database)) as c, c:
            c.execute("UPDATE stages SET completed_at=NULL, state='RUNNING'")
        stage = summarize_job(self.root, 1)["stages"][0]
        self.assertIsNone(stage["wall_seconds"])
        self.assertIsNone(stage["profiles"][0]["performance"])

    def test_unknown_job_fails_explicitly(self):
        with self.assertRaisesRegex(ValueError, 'Unknown job'):
            summarize_job(self.root, 99)

    def test_failed_stage_time_is_visible_but_not_completed_total(self):
        with closing(sqlite3.connect(self.database)) as c, c:
            c.execute("UPDATE stages SET state='FAILED'")
        result = summarize_job(self.root, 1)
        self.assertEqual(result["stages"][0]["wall_seconds"], 10)
        self.assertEqual(result["completed_stage_wall_seconds"], 0)
