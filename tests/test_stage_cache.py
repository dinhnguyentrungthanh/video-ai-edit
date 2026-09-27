import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.stage_cache import StageArtifactCache


class StageArtifactCacheTests(unittest.TestCase):
    def _root(self, directory: str) -> Path:
        root = Path(directory)
        (root / "src" / "biliflow").mkdir(parents=True)
        (root / "src" / "biliflow" / "worker.py").write_text(
            "VERSION = 1\n", encoding="utf-8"
        )
        (root / "config").mkdir()
        (root / "config" / "processing_profiles.json").write_text(
            '{"careful": {"sample": 2}}', encoding="utf-8"
        )
        (root / "scripts").mkdir()
        (root / "scripts" / "run.ps1").write_text("python -m biliflow", encoding="utf-8")
        (root / "reports" / "jobs").mkdir(parents=True)
        (root / "input").mkdir()
        return root

    def test_exact_stage_snapshot_restores_all_evidence_into_new_revision(self):
        with TemporaryDirectory() as directory:
            root = self._root(directory)
            source = root / "input" / "movie.mp4"
            source.write_bytes(b"video")
            sha = "1" * 64
            old_report = root / "reports" / "jobs" / "run-1"
            old_stage = old_report / "adult"
            (old_stage / "thumbnails").mkdir(parents=True)
            (old_stage / "thumbnails" / "frame.jpg").write_bytes(b"jpeg")
            artifact = old_stage / "scan.json"
            artifact.write_text(
                json.dumps({
                    "status": "COMPLETED",
                    "input_sha256": sha,
                    "intervals": [{"thumbnail": "thumbnails/frame.jpg"}],
                }),
                encoding="utf-8",
            )
            cache = StageArtifactCache(root)
            stored = cache.store(
                stage_name="adult", source_sha256=sha, source_path=source,
                report_root=old_report,
                commands=(("run", "--input", str(source), "--report-dir", str(old_stage)),),
                artifact_paths=(artifact,),
            )
            self.assertIsNotNone(stored)

            new_report = root / "reports" / "jobs" / "run-2"
            new_artifact = new_report / "adult" / "scan.json"
            hit = cache.restore(
                stage_name="adult", source_sha256=sha, source_path=source,
                report_root=new_report,
                commands=(("run", "--input", str(source), "--report-dir", str(new_artifact.parent)),),
                artifact_paths=(new_artifact,),
            )
            self.assertIsNotNone(hit)
            self.assertTrue(new_artifact.is_file())
            self.assertEqual(
                (new_artifact.parent / "thumbnails" / "frame.jpg").read_bytes(),
                b"jpeg",
            )

    def test_changed_command_does_not_reuse_old_stage(self):
        with TemporaryDirectory() as directory:
            root = self._root(directory)
            source = root / "input" / "movie.mp4"
            source.write_bytes(b"video")
            sha = "2" * 64
            old_report = root / "reports" / "jobs" / "run-1"
            artifact = old_report / "adult" / "scan.json"
            artifact.parent.mkdir(parents=True)
            artifact.write_text(
                json.dumps({"status": "COMPLETED", "input_sha256": sha}),
                encoding="utf-8",
            )
            cache = StageArtifactCache(root)
            cache.store(
                stage_name="adult", source_sha256=sha, source_path=source,
                report_root=old_report,
                commands=(("run", "--sample-fps", "2", "--report-dir", str(artifact.parent)),),
                artifact_paths=(artifact,),
            )
            new_report = root / "reports" / "jobs" / "run-2"
            new_artifact = new_report / "adult" / "scan.json"
            hit = cache.restore(
                stage_name="adult", source_sha256=sha, source_path=source,
                report_root=new_report,
                commands=(("run", "--sample-fps", "1", "--report-dir", str(new_artifact.parent)),),
                artifact_paths=(new_artifact,),
            )
            self.assertIsNone(hit)
            self.assertFalse(new_artifact.exists())

    def test_sibling_stage_roots_do_not_replace_unrelated_completed_stage(self):
        with TemporaryDirectory() as directory:
            root = self._root(directory)
            source = root / "input" / "movie.mp4"
            source.write_bytes(b"video")
            sha = "4" * 64
            old_report = root / "reports" / "jobs" / "run-1"
            gore = old_report / "gore" / "scan.json"
            violence = old_report / "violence" / "scan.json"
            for artifact in (gore, violence):
                artifact.parent.mkdir(parents=True)
                artifact.write_text(
                    json.dumps({"status": "COMPLETED", "input_sha256": sha}),
                    encoding="utf-8",
                )
            cache = StageArtifactCache(root)
            cache.store(
                stage_name="live_safety", source_sha256=sha, source_path=source,
                report_root=old_report,
                commands=(("shared", "--report-dir", str(old_report)),),
                artifact_paths=(gore, violence),
            )

            new_report = root / "reports" / "jobs" / "run-2"
            adult = new_report / "adult" / "scan.json"
            adult.parent.mkdir(parents=True)
            adult.write_text('{"keep": true}', encoding="utf-8")
            new_gore = new_report / "gore" / "scan.json"
            new_violence = new_report / "violence" / "scan.json"
            hit = cache.restore(
                stage_name="live_safety", source_sha256=sha, source_path=source,
                report_root=new_report,
                commands=(("shared", "--report-dir", str(new_report)),),
                artifact_paths=(new_gore, new_violence),
            )
            self.assertIsNotNone(hit)
            self.assertTrue(adult.is_file())
            self.assertTrue(new_gore.is_file())
            self.assertTrue(new_violence.is_file())

    def test_prune_removes_expired_entries(self):
        with TemporaryDirectory() as directory:
            root = self._root(directory)
            cache = StageArtifactCache(root)
            manifest = (
                root / "cache" / "stage-results" / ("3" * 64)
                / "adult" / "key" / "manifest.json"
            )
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{}", encoding="utf-8")
            old = datetime.now(timezone.utc) - timedelta(days=3)
            os.utime(manifest, (old.timestamp(), old.timestamp()))
            result = cache.prune(max_age=timedelta(days=1))
            self.assertEqual(result["removed_entries"], 1)
            self.assertFalse(manifest.parent.exists())


if __name__ == "__main__":
    unittest.main()
