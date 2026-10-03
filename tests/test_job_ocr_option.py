import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from biliflow.control_center import _dashboard_html
from biliflow.job_pipeline import pipeline_stages, normalize_fast_scan, normalize_ocr_batch_size
from biliflow.job_store import JobStore
from biliflow.scheduler import JobScheduler
from biliflow.stage_cache import StageArtifactCache

ROOT = Path(__file__).resolve().parents[1]


def _run_node(source):
    """Run a script file: the dashboard script is longer than a Windows command line."""
    with TemporaryDirectory() as directory:
        path = Path(directory) / "check.js"
        path.write_text(source, encoding="utf-8")
        return subprocess.run([shutil.which("node"), str(path)], capture_output=True, text=True)


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
            scheduler.rerun(job_id, detector_groups=["advertising"], ocr_recognition_batch_size=8,
                            fast_scan=False)
            store.close()
            store = JobStore(db)
            try:
                scheduler = JobScheduler(root, store)
                scheduler.resume(job_id)
                self.assertEqual(scheduler.ocr_batch_size(job_id), 8)
                self.assertEqual(scheduler._definitions(store.get_job(job_id))["text"].commands[0].argv[-1], "8")
                store.update_job(job_id, state="WAITING_REVIEW")  # that scan finished; reruns refuse a queued job
                scheduler.rerun(job_id)
                self.assertEqual(scheduler.ocr_batch_size(job_id), 8)
                store.update_job(job_id, state="WAITING_REVIEW")  # that scan finished; reruns refuse a queued job
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
        result = _run_node(script + checks)
        self.assertEqual(result.returncode, 0, result.stderr)


class JobFastScanTests(unittest.TestCase):
    def options(self, **extra):
        source = next((ROOT / "input").glob("*.mp4"))
        return dict(root=ROOT, job_key="fast-scan-test", source=source,
                    content_style="live_action", profile="careful", **extra)

    def test_fast_scan_changes_only_text_logo_and_live_safety_commands(self):
        standard = pipeline_stages(**self.options())
        fast = pipeline_stages(**self.options(fast_scan=True))
        self.assertEqual([s.name for s in standard], [s.name for s in fast])
        cache = StageArtifactCache(ROOT)
        for a, b in zip(standard, fast):
            if a.name == "text":
                argv = b.commands[0].argv
                start = argv.index("--recognition-batch-size")
                self.assertEqual(argv[start:start + 5], ("--recognition-batch-size", "8",
                                                        "--recognition-frame-window", "4",
                                                        "--prewarm-logo-routing"))
            elif a.name == "visual_logo":
                self.assertEqual(b.commands[0].argv[-2:], ("--routing-workers", "3"))
            elif a.name == "live_safety":
                self.assertEqual(b.commands[0].argv[-2:], ("--violence-precision", "fp16"))
            else:
                self.assertEqual(a, b)
                continue
            keys = [cache.key(stage_name=a.name, source_sha256="a" * 64, source_path=Path(a.commands[0].argv[7]),
                              report_root=ROOT / "reports/jobs/fast-scan-test",
                              commands=[s.commands[0].argv], artifact_paths=s.commands[0].expected_artifacts)
                    for s in (a, b)]
            self.assertNotEqual(*keys)

    def test_prewarm_routing_arguments_equal_logo_stage_routing_arguments(self):
        # A mismatch would silently change the routing cache key (logo stage
        # recomputes); keep the two stages' routing inputs identical.
        from biliflow.routing_prewarm import LOGO_ROUTING_OPTIONS
        for profile in ("careful", "fast"):
            stages = {s.name: s for s in pipeline_stages(**self.options(fast_scan=True, source_sha256="f" * 64)
                                                         | {"profile": profile})}
            text, logo = stages["text"].commands[0].argv, stages["visual_logo"].commands[0].argv
            for text_option, logo_option in LOGO_ROUTING_OPTIONS:
                if logo_option == "--decode":
                    self.assertNotIn("--decode", logo)  # decode is not part of the cache key
                    continue
                with self.subTest(profile=profile, option=logo_option):
                    self.assertIn(logo_option, logo)
                    self.assertEqual(text[text.index(text_option) + 1], logo[logo.index(logo_option) + 1])

    def test_standard_mode_never_prewarms(self):
        stages = {s.name: s for s in pipeline_stages(**self.options())}
        self.assertNotIn("--prewarm-logo-routing", stages["text"].commands[0].argv)
        self.assertNotIn("--detect-precision", stages["text"].commands[0].argv)

    def test_fast_scan_detects_text_in_fp16(self):
        text = {s.name: s for s in pipeline_stages(**self.options(fast_scan=True))}["text"].commands[0].argv
        self.assertEqual(text[text.index("--detect-precision") + 1], "fp16")
        legacy = {s.name: s for s in pipeline_stages(**self.options(ocr_recognition_batch_size=8))}
        self.assertNotIn("--detect-precision", legacy["text"].commands[0].argv)

    def test_fast_scan_overrides_legacy_ocr_batch_without_changing_its_meaning(self):
        legacy = {s.name: s for s in pipeline_stages(**self.options(ocr_recognition_batch_size=8))}
        self.assertEqual(legacy["text"].commands[0].argv[-2:], ("--recognition-batch-size", "8"))
        self.assertNotIn("--recognition-frame-window", legacy["text"].commands[0].argv)
        self.assertNotIn("--routing-workers", legacy["visual_logo"].commands[0].argv)
        both = {s.name: s for s in pipeline_stages(**self.options(ocr_recognition_batch_size=8, fast_scan=True))}
        self.assertEqual(both["text"].commands[0].argv.count("--recognition-batch-size"), 1)

    def test_fast_scan_runs_the_anime_tagger_in_fp16_only(self):
        options = self.options() | {"content_style": "animation"}
        standard = {s.name: s for s in pipeline_stages(**options)}
        fast = {s.name: s for s in pipeline_stages(**options, fast_scan=True)}
        self.assertNotIn("--precision", standard["animation_safety"].commands[0].argv)
        argv = fast["animation_safety"].commands[0].argv
        self.assertEqual(argv[-2:], ("--precision", "fp16"))
        self.assertEqual(argv[:-2], standard["animation_safety"].commands[0].argv)  # nothing else changes
        cache = StageArtifactCache(ROOT)
        keys = [cache.key(stage_name="animation_safety", source_sha256="a" * 64, source_path=options["source"],
                          report_root=ROOT / "reports/jobs/fast-scan-test", commands=[s.commands[0].argv],
                          artifact_paths=s.commands[0].expected_artifacts)
                for s in (standard["animation_safety"], fast["animation_safety"])]
        self.assertNotEqual(*keys)  # an fp32 result is never reused for an fp16 job or vice versa

    def test_fast_scan_runs_the_live_violence_model_in_fp16_only(self):
        standard = {s.name: s for s in pipeline_stages(**self.options())}
        fast = {s.name: s for s in pipeline_stages(**self.options(fast_scan=True))}
        self.assertNotIn("--violence-precision", standard["live_safety"].commands[0].argv)
        argv = fast["live_safety"].commands[0].argv
        self.assertEqual(argv[-2:], ("--violence-precision", "fp16"))
        self.assertEqual(argv[:-2], standard["live_safety"].commands[0].argv)
        for name in ("adult", "confirm_violence"):  # adult stays fp32; VLM unchanged
            self.assertEqual(standard[name], fast[name])
        cache = StageArtifactCache(ROOT)
        keys = [cache.key(stage_name="live_safety", source_sha256="a" * 64, source_path=self.options()["source"],
                          report_root=ROOT / "reports/jobs/fast-scan-test", commands=[s.commands[0].argv],
                          artifact_paths=s.commands[0].expected_artifacts)
                for s in (standard["live_safety"], fast["live_safety"])]
        self.assertNotEqual(*keys)

    def test_violence_only_scan_keeps_its_standard_scanner(self):
        options = self.options(detector_groups=["violence"])
        standard = {s.name: s for s in pipeline_stages(**options)}
        fast = {s.name: s for s in pipeline_stages(**options, fast_scan=True)}
        self.assertNotIn("live_safety", fast)
        self.assertEqual(standard["violence"], fast["violence"])

    def test_fast_scan_without_advertising_adds_nothing(self):
        options = self.options(detector_groups=["adult"])
        self.assertEqual(pipeline_stages(**options), pipeline_stages(**options, fast_scan=True))

    def test_rejects_non_boolean_values(self):
        for value in (1, 0, "true", None, 3):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_fast_scan(value)

    def test_setting_survives_rerun_resume_restart(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            shutil.copyfile(ROOT / "config/processing_profiles.json", root / "config/processing_profiles.json")
            source = root / "movie.mp4"
            source.write_bytes(b"movie")
            db = root / "jobs.sqlite3"
            store = JobStore(db)
            job_id = store.upsert_job(job_key="movie", source_path=source, source_sha256="1" * 64,
                source_size_bytes=5, source_mtime_ns=1, content_style="live_action", state="WAITING_REVIEW")["id"]
            scheduler = JobScheduler(root, store)
            self.assertTrue(scheduler.fast_scan(job_id))  # product default
            scheduler.configure_and_queue(job_id, content_style="live_action", profile="careful",
                                          detector_groups=["advertising"])
            definitions = scheduler._definitions(store.get_job(job_id))
            self.assertIn("--recognition-frame-window", definitions["text"].commands[0].argv)
            self.assertIn("--routing-workers", definitions["visual_logo"].commands[0].argv)
            store.update_job(job_id, state="WAITING_REVIEW")
            with self.assertRaises(ValueError):
                scheduler.rerun(job_id, fast_scan="true")
            self.assertEqual(store.get_job(job_id)["state"], "WAITING_REVIEW")
            self.assertIsNone(store.setting(f"pipeline_key:{job_id}"))
            scheduler.rerun(job_id, detector_groups=["advertising"], fast_scan=False)
            self.assertFalse(scheduler.fast_scan(job_id))
            store.update_job(job_id, state="WAITING_REVIEW")  # that scan finished; reruns refuse a queued job
            scheduler.rerun(job_id, fast_scan=True)
            store.close()
            store = JobStore(db)
            try:
                scheduler = JobScheduler(root, store)
                scheduler.resume(job_id)
                self.assertTrue(scheduler.fast_scan(job_id))
                definitions = scheduler._definitions(store.get_job(job_id))
                self.assertIn("--recognition-frame-window", definitions["text"].commands[0].argv)
                self.assertIn("--routing-workers", definitions["visual_logo"].commands[0].argv)
                store.update_job(job_id, state="WAITING_REVIEW")  # that scan finished; reruns refuse a queued job
                scheduler.rerun(job_id)
                self.assertTrue(scheduler.fast_scan(job_id))
                store.update_job(job_id, state="WAITING_REVIEW")  # that scan finished; reruns refuse a queued job
                scheduler.rerun(job_id, fast_scan=False)
                definitions = scheduler._definitions(store.get_job(job_id))
                self.assertNotIn("--recognition-frame-window", definitions["text"].commands[0].argv)
                self.assertNotIn("--routing-workers", definitions["visual_logo"].commands[0].argv)
            finally:
                store.close()

    @unittest.skipUnless(shutil.which("node"), "Node is required for Dashboard JS execution")
    def test_dashboard_fast_scan_draft_and_posts(self):
        script = _dashboard_html().split("<script>", 1)[1].split("</script>", 1)[0]
        script = script[:script.rindex("(async()=>")]
        checks = r'''
const assert=require('assert');
const values={'ocr-1':{value:'1'},'fast-1':{checked:true},'style-1':{value:'live_action'},'profile-1':{value:'careful'}};
global.document={getElementById:id=>values[id]};global.confirm=()=>true;
const calls=[];global.fetch=async(url,opt)=>{calls.push({url,body:JSON.parse(opt.body)});return{ok:true,status:200,json:async()=>({})}};load=async()=>{};notify=()=>{};
selectedDetectors=()=>['advertising'];
(async()=>{
 const job={id:1,fast_scan:false};
 const on='id="fast-1" checked';
 assert(!speedPicker(job).includes(on));
 assert(speedPicker({id:1,fast_scan:true}).includes(on));
 assert(speedPicker({id:2}).includes('id="fast-2" checked'));
 speedDrafts[1]=true;assert(speedPicker(job).includes(on));
 await start(1);assert.strictEqual(calls[0].body.fast_scan,true);assert(!Object.hasOwn(speedDrafts,1));
 values['fast-1'].checked=false;await rerun(1);assert.strictEqual(calls[1].body.fast_scan,false);
 global.fetch=async()=>{throw Error('save failed')};speedDrafts[1]=true;await rerun(1);assert.equal(speedDrafts[1],true);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = _run_node(script + checks)
        self.assertEqual(result.returncode, 0, result.stderr)
