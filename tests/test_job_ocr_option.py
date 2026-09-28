import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from biliflow.control_center import _dashboard_html
from biliflow.job_pipeline import pipeline_stages, normalize_ocr_batch_size
from biliflow.job_store import JobStore
from biliflow.scheduler import JobScheduler
from biliflow.stage_cache import StageArtifactCache

ROOT = Path(__file__).resolve().parents[1]


class JobOcrOptionTests(unittest.TestCase):
    def test_only_text_command_changes_and_cache_separates_modes(self):
        source = next((ROOT / "input").glob("*.mp4"))
        options = dict(root=ROOT, job_key="ocr-option-test", source=source,
                       content_style="live_action", profile="careful")
        serial = pipeline_stages(**options)
        batch = pipeline_stages(**options, ocr_recognition_batch_size=8)
        self.assertEqual([s.name for s in serial], [s.name for s in batch])
        for a, b in zip(serial, batch):
            if a.name != "text":
                self.assertEqual(a, b)
            else:
                self.assertNotIn("--recognition-batch-size", a.commands[0].argv)
                self.assertEqual(b.commands[0].argv[-2:], ("--recognition-batch-size", "8"))
                cache = StageArtifactCache(ROOT)
                keys = [cache.key(stage_name="text", source_sha256="a" * 64,
                    source_path=source, report_root=ROOT / "reports/jobs/ocr-option-test",
                    commands=[s.commands[0].argv], artifact_paths=s.commands[0].expected_artifacts)
                    for s in (a, b)]
                self.assertNotEqual(*keys)

    def test_no_advertising_does_not_add_ocr_work(self):
        source = next((ROOT / "input").glob("*.mp4"))
        options = dict(root=ROOT, job_key="ocr-option-test", source=source,
                       content_style="animation", profile="careful", detector_groups=["adult"])
        self.assertEqual(pipeline_stages(**options), pipeline_stages(**options, ocr_recognition_batch_size=8))

    def test_rejects_invalid_types_and_values(self):
        for value in (True, False, "8", 8.0, 0, 4, 16, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_ocr_batch_size(value)

    def test_option_survives_resume_rerun_restart_and_keeps_old_review(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            shutil.copyfile(ROOT / "config/processing_profiles.json", root / "config/processing_profiles.json")
            source = root / "movie.mp4"
            source.write_bytes(b"movie")
            old_review = root / "old-review.json"
            old_review.write_text('{"decision":"BLUR"}', encoding="utf-8")
            db = root / "jobs.sqlite3"
            store = JobStore(db)
            job = store.upsert_job(job_key="movie", source_path=source, source_sha256="1" * 64,
                source_size_bytes=5, source_mtime_ns=1, content_style="live_action", state="WAITING_REVIEW")
            job_id = job["id"]
            scheduler = JobScheduler(root, store)
            self.assertEqual(scheduler.ocr_batch_size(job_id), 1)
            with self.assertRaises(ValueError):
                scheduler.rerun(job_id, ocr_recognition_batch_size=True)
            self.assertEqual(store.get_job(job_id)["state"], "WAITING_REVIEW")
            self.assertIsNone(store.setting(f"pipeline_key:{job_id}"))
            scheduler.rerun(job_id, detector_groups=["advertising"], ocr_recognition_batch_size=8)
            store.close()
            store = JobStore(db)
            try:
                scheduler = JobScheduler(root, store)
                scheduler.resume(job_id)
                self.assertEqual(scheduler.ocr_batch_size(job_id), 8)
                self.assertEqual(scheduler._definitions(store.get_job(job_id))["text"].commands[0].argv[-1], "8")
                scheduler.rerun(job_id)
                self.assertEqual(scheduler.ocr_batch_size(job_id), 8)
                scheduler.rerun(job_id, ocr_recognition_batch_size=1)
                self.assertNotIn("--recognition-batch-size", scheduler._definitions(store.get_job(job_id))["text"].commands[0].argv)
                self.assertEqual(old_review.read_text(encoding="utf-8"), '{"decision":"BLUR"}')
            finally:
                store.close()

    @unittest.skipUnless(shutil.which("node"), "Node is required for Dashboard JS execution")
    def test_dashboard_draft_survives_refresh_and_posts_selected_batch(self):
        script = _dashboard_html().split("<script>", 1)[1].split("</script>", 1)[0]
        # Skip page boot/network/timers, execute the actual control functions.
        script = script[:script.rindex("(async()=>")]
        checks = r'''
const assert=require('assert');
const values={'ocr-1':{value:'8'},'style-1':{value:'live_action'},'profile-1':{value:'careful'}};
global.document={getElementById:id=>values[id]};global.confirm=()=>true;
const calls=[];global.fetch=async(url,opt)=>{calls.push({url,body:JSON.parse(opt.body)});return{ok:true,status:200,json:async()=>({})}};load=async()=>{};notify=()=>{};
selectedDetectors=()=>['advertising'];
(async()=>{
 const job={id:1,ocr_recognition_batch_size:1};
 assert(ocrPicker(job).includes('value="1" selected'));
 ocrDrafts[1]=8;assert(ocrPicker(job).includes('value="8" selected'));
 await start(1);assert.equal(calls[0].body.ocr_recognition_batch_size,8);assert(!Object.hasOwn(ocrDrafts,1));
 ocrDrafts[1]=8;await rerun(1);assert.equal(calls[1].body.ocr_recognition_batch_size,8);
 global.fetch=async()=>{throw Error('save failed')};ocrDrafts[1]=8;await rerun(1);assert.equal(ocrDrafts[1],8);
 values['ocr-1'].value='1';assert.equal(selectedOcrBatch(1),1);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run([shutil.which("node"), "-e", script + checks], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
