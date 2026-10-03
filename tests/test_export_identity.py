"""Export identity: an export is named by what its render applies, and an existing
file is reused only when its manifest proves it is that render (never overwritten)."""

import hashlib
import json
import os
import re
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from biliflow.control_center import ControlCenter
from biliflow.export_guards import EXPORT_PATH_TAKEN_MESSAGE
from biliflow.export_identity import (
    PROBLEM_CHANGED,
    PROBLEM_DECODE,
    PROBLEM_MANIFEST,
    PROBLEM_NO_OPERATIONS,
    PROBLEM_OLDER,
    export_manifest_path,
    legacy_export_paths,
    manifest_problem,
    output_candidates,
)
from biliflow.final_renderer import normalize_output_size_policy
from biliflow.job_store import JobStore
from biliflow.review_workflow import (
    approved_operations,
    existing_review_export,
    queue_render_identity,
    review_export_paths,
)
from biliflow.scheduler import JobScheduler


SHA = "ab" * 32
REGION = {"x": 10, "y": 20, "width": 200, "height": 96}
DISCRETE = {
    "temporal_policy": "discrete_detected_intervals",
    "detected_intervals": [
        {"start_seconds": 3.0, "end_seconds": 4.0},
        {"start_seconds": 8.0, "end_seconds": 9.0},
    ],
}


def blur(item_id="review-a", **fields):
    """A BLUR decision with every field the edit plan reads."""
    value = {
        "id": item_id, "category": "advertising", "decision": "BLUR",
        "start_seconds": 3.0, "end_seconds": 9.0, "reasons": ["logo"], "evidence": [],
        "decision_region_source_pixels": dict(REGION),
    }
    value.update(fields)
    return value


def keep(item_id="review-k", **fields):
    value = {"id": item_id, "category": "adult", "decision": "KEEP", "start_seconds": 20.0, "end_seconds": 25.0}
    value.update(fields)
    return value


def make_queue(*items, source="E:/input/Tập 9.mp4", sha=SHA, policy=None):
    value = {
        "status": "READY_FOR_EDIT_PLAN",
        "source": {"path": source, "sha256": sha, "duration_seconds": 60.0},
        "items": list(items),
    }
    if policy is not None:
        value["export_size_policy"] = policy
    return value


def paths_of_main(root, queue):
    """review_export_paths of main 23aa1e4 (the decision hash): the name of every export made before."""
    source = Path(str(queue["source"]["path"]))
    source_hash = str(queue["source"].get("sha256") or "nohash")[:8]
    decisions = [
        {
            "id": item.get("id"), "decision": item.get("decision"),
            "start": item.get("start_seconds"), "end": item.get("end_seconds"),
            "region": item.get("decision_region_source_pixels"),
        }
        for item in queue.get("items", [])
    ]
    export_policy = queue.get("export_size_policy")
    identity = decisions
    if isinstance(export_policy, dict) and export_policy.get("mode") not in {None, "default"}:
        identity = {"decisions": decisions, "export_size_policy": export_policy}
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:8]
    slug = re.sub(r"[^\w.-]+", "-", source.stem, flags=re.UNICODE).strip("-._")
    key = f"{(slug or 'video')[:80]}-{source_hash}-{digest}"
    return (
        root / "work" / f"{key}-edit-plan.json",
        root / "output" / f"{key}-reviewed.mp4",
        root / "work" / f"{key}-export-job.json",
    )


def write_export(root, output, queue, *, sha=SHA, change=None, content=b"rendered video " * 64):
    """A file at ``output`` with the manifest the renderer writes for ``queue``'s operations."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(content)
    manifest = {
        "schema_version": 1, "status": "COMPLETED", "created_at": "2026-10-03T12:00:00+07:00",
        "edit_plan": "work/earlier-edit-plan.json",
        "source": {
            "path": queue["source"]["path"], "sha256": sha, "duration_seconds": 60.0,
            "sha256_after_render": sha, "modified": False,
        },
        "output": {
            "path": output.relative_to(root).as_posix(), "bytes": output.stat().st_size,
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        },
        "encoding": {"full_decode_validation_passed": True},
        "operations": approved_operations(queue),
    }
    if change is not None:
        change(manifest)
    output.with_suffix(output.suffix + ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


class TempRoot(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()


class ExportIdentityTests(TempRoot):
    def test_the_blur_edge_mode_gets_its_own_export(self):
        exported = make_queue(blur(), keep())
        vertical = make_queue(blur(decision_blur_edge_mode="vertical_only"), keep())
        # The name of main never saw the edge mode, so finalize reused the old export.
        self.assertEqual(paths_of_main(self.root, exported), paths_of_main(self.root, vertical))
        first, second = review_export_paths(self.root, exported), review_export_paths(self.root, vertical)
        for before, after in zip(first, second):
            self.assertNotEqual(before, after)
        # "all_edges" is what the render applies when no mode was chosen.
        explicit = make_queue(blur(decision_blur_edge_mode="all_edges"), keep())
        self.assertEqual(review_export_paths(self.root, explicit), first)

    def test_detected_intervals_get_their_own_export(self):
        whole, parts = make_queue(blur()), make_queue(blur(**DISCRETE))
        self.assertEqual(paths_of_main(self.root, whole), paths_of_main(self.root, parts))
        self.assertEqual(len(approved_operations(parts)), 2)
        self.assertNotEqual(review_export_paths(self.root, whole)[1], review_export_paths(self.root, parts)[1])

    def test_only_what_the_render_applies_names_the_export(self):
        base = review_export_paths(self.root, make_queue(blur(), keep()))
        for changed in (
            make_queue(blur(decision_note="logo kênh"), keep()),
            make_queue(blur(reasons=["another reason"]), keep()),
            make_queue(blur(), keep(decision_region_source_pixels=dict(REGION), start_seconds=21.0)),
        ):
            with self.subTest(unchanged=changed["items"]):
                self.assertEqual(review_export_paths(self.root, changed), base)
        for changed in (
            make_queue(blur(start_seconds=3.5), keep()),
            make_queue(blur(decision_region_source_pixels=dict(REGION, width=180)), keep()),
            make_queue(blur(decision="CUT"), keep()),
            make_queue(blur(), blur("review-k", start_seconds=20.0, end_seconds=25.0)),
        ):
            with self.subTest(changed=changed["items"]):
                self.assertNotEqual(review_export_paths(self.root, changed)[1], base[1])

    def test_a_non_default_size_policy_still_names_the_export(self):
        queue = make_queue(blur())
        plain = review_export_paths(self.root, queue)
        default = make_queue(blur(), policy=normalize_output_size_policy("default", None))
        unlimited = make_queue(blur(), policy=normalize_output_size_policy("unlimited", None))
        self.assertEqual(review_export_paths(self.root, default), plain)
        self.assertNotEqual(review_export_paths(self.root, unlimited)[1], plain[1])

    def test_an_unfinished_review_keeps_the_decision_hash(self):
        for queue in (
            make_queue(blur(decision=None)),
            make_queue(blur(decision="NEEDS_MORE_CONTEXT"), keep()),
            {"source": {"path": "E:/input/x.mp4", "sha256": SHA},
             "items": [{"id": "a", "decision": "CUT", "start_seconds": 0, "end_seconds": 5}]},
        ):
            with self.subTest(items=queue["items"]):
                self.assertIsNone(queue_render_identity(queue))
                self.assertEqual(review_export_paths(self.root, queue), paths_of_main(self.root, queue))
                self.assertEqual(output_candidates(self.root, queue, None), [paths_of_main(self.root, queue)[1]])

    def test_a_source_hash_that_is_not_hex_stays_out_of_the_name(self):
        # A crafted review queue must not move the export path out of output/.
        for sha in ("..\\..\\..\\x", "../../x/../y", "ab/cd\\ef", "abc", None):
            queue = make_queue(blur(), sha=sha)
            for paths in (review_export_paths(self.root, queue), legacy_export_paths(self.root, queue)):
                with self.subTest(sha=sha, paths=paths[1].name):
                    self.assertEqual(paths[1].parent, self.root / "output")
                    self.assertEqual({paths[0].parent, paths[2].parent}, {self.root / "work"})
                    self.assertIn("-nohash-", paths[1].name)

    def test_legacy_paths_are_the_names_main_gave(self):
        for queue in (
            make_queue(blur(), keep()),
            make_queue(blur(**DISCRETE), policy=normalize_output_size_policy("unlimited", None)),
            make_queue(keep(), source="E:/input/Nhất Âu Xuân - Tập 10.mp4"),
        ):
            with self.subTest(items=queue["items"]):
                self.assertEqual(legacy_export_paths(self.root, queue), paths_of_main(self.root, queue))
                self.assertEqual(
                    output_candidates(self.root, queue, queue_render_identity(queue)),
                    [review_export_paths(self.root, queue)[1], paths_of_main(self.root, queue)[1]],
                )


class ManifestProblemTests(TempRoot):
    def setUp(self):
        super().setUp()
        self.queue = make_queue(blur(), keep())
        self.output = review_export_paths(self.root, self.queue)[1]
        self.manifest = write_export(self.root, self.output, self.queue)
        self.render = queue_render_identity(self.queue)

    def problem(self, change=None, *, sha=SHA, render=..., compare_render=True):
        manifest = json.loads(json.dumps(self.manifest))
        if change is not None:
            change(manifest)
        return manifest_problem(
            self.root, self.output, manifest, source_sha256=sha,
            render=self.render if render is ... else render, compare_render=compare_render,
        )

    def test_a_complete_render_of_the_review_passes(self):
        self.assertIsNone(self.problem())
        self.assertIsNone(self.problem(sha=SHA.upper()))

    def test_a_manifest_that_does_not_prove_this_file_and_source(self):
        cases = {
            "status": lambda m: m.update(status="FAILED"),
            "source sha": lambda m: m["source"].update(sha256="cd" * 32),
            "modified": lambda m: m["source"].update(modified=True),
            "changed during render": lambda m: m["source"].update(sha256_after_render="cd" * 32),
            "another file": lambda m: m["output"].update(path="output/other-reviewed.mp4"),
            "bytes type": lambda m: m["output"].update(bytes=True),
            "output sha": lambda m: m["output"].update(sha256="xyz"),
            "naive time": lambda m: m.update(created_at="2026-10-03T12:00:00"),
            "no source": lambda m: m.pop("source"),
            "operations": lambda m: m.update(operations="x"),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                self.assertEqual(self.problem(change), PROBLEM_MANIFEST)
        self.assertEqual(self.problem(sha=""), PROBLEM_MANIFEST)
        for manifest in (None, ["not", "a", "manifest"]):
            with self.subTest(manifest=manifest):
                self.assertEqual(manifest_problem(
                    self.root, self.output, manifest, source_sha256=SHA, render=self.render,
                ), PROBLEM_MANIFEST)

    def test_decode_size_and_operations(self):
        self.assertEqual(self.problem(lambda m: m.pop("encoding")), PROBLEM_DECODE)
        self.assertEqual(
            self.problem(lambda m: m["encoding"].update(full_decode_validation_passed="yes")), PROBLEM_DECODE,
        )
        self.assertEqual(self.problem(lambda m: m["output"].update(bytes=m["output"]["bytes"] + 1)), PROBLEM_CHANGED)
        self.assertEqual(self.problem(lambda m: m.pop("operations")), PROBLEM_NO_OPERATIONS)
        vertical = queue_render_identity(make_queue(blur(decision_blur_edge_mode="vertical_only"), keep()))
        self.assertEqual(self.problem(render=vertical), PROBLEM_OLDER)
        self.assertEqual(self.problem(render=None), PROBLEM_OLDER)
        # Without the render comparison the file still has to match its manifest.
        self.assertIsNone(self.problem(render=vertical, compare_render=False))
        self.output.write_bytes(b"not a video")
        self.assertEqual(self.problem(), PROBLEM_CHANGED)
        self.assertEqual(self.problem(compare_render=False), PROBLEM_CHANGED)

    def test_a_link_at_the_export_path_is_not_the_export(self):
        # A link to the source with a manifest that fits its bytes: "Dọn video gốc"
        # would send the only real copy to the bin and keep a dangling link as the export.
        target = self.root / "input" / "Tập 9.mp4"
        target.parent.mkdir(parents=True)
        target.write_bytes(self.output.read_bytes())
        self.output.unlink()
        try:
            self.output.symlink_to(target)
        except OSError as error:
            self.skipTest(f"cannot create a symbolic link here: {error}")
        self.assertEqual(self.problem(), PROBLEM_CHANGED)
        self.assertEqual(self.problem(compare_render=False), PROBLEM_CHANGED)
        # The link's own size (0 on Windows) in the manifest: only the link check refuses it.
        link_size = os.lstat(self.output).st_size
        self.assertEqual(self.problem(lambda m: m["output"].update(bytes=link_size)), PROBLEM_CHANGED)

    def test_any_reparse_point_is_not_the_export(self):
        original = os.lstat
        info = original(self.output)
        flagged = SimpleNamespace(st_mode=info.st_mode, st_size=info.st_size, st_nlink=1, st_file_attributes=0x400)

        def lstat(path, *args, **kwargs):
            if os.path.normcase(os.fspath(path)) == os.path.normcase(os.fspath(self.output)):
                return flagged
            return original(path, *args, **kwargs)

        with patch("biliflow.export_identity.os.lstat", side_effect=lstat):
            self.assertEqual(self.problem(), PROBLEM_CHANGED)

    def test_a_hard_link_keeps_the_data_and_is_still_the_export(self):
        # Recycling the source never loses the export's data (e.g. a copy linked for an upload).
        os.link(self.output, self.root / "upload-copy.mp4")
        self.assertIsNone(self.problem())

    def test_a_path_the_system_cannot_resolve_proves_nothing(self):
        self.assertEqual(
            self.problem(lambda m: m["output"].update(path="output/bad\0name-reviewed.mp4")), PROBLEM_MANIFEST,
        )

    def test_a_described_path_is_compared_as_written_never_resolved(self):
        # Resolving a UNC or device path makes Windows contact that machine (SMB).
        with patch.object(Path, "resolve", side_effect=AssertionError("resolve must not run")):
            for path in ("\\\\host\\share\\x-reviewed.mp4", "//host/share/x-reviewed.mp4",
                         "\\\\?\\UNC\\host\\share\\x-reviewed.mp4", "\\\\.\\pipe\\x"):
                with self.subTest(path=path):
                    self.assertEqual(self.problem(lambda m: m["output"].update(path=path)), PROBLEM_MANIFEST)
            self.assertIsNone(self.problem())

    def test_a_described_path_through_a_symlink_loop_proves_nothing(self):
        loop = self.root / "output" / "loop"
        try:
            loop.symlink_to(loop, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"cannot create a symbolic link here: {error}")
        self.assertEqual(
            self.problem(lambda m: m["output"].update(path="output/loop/x-reviewed.mp4")), PROBLEM_MANIFEST,
        )

    def test_the_source_itself_is_never_its_export(self):
        # A hard link of the source (or the source through a junction) at the export path.
        source = self.root / "input" / "Tập 9.mp4"
        source.parent.mkdir(parents=True)
        os.link(self.output, source)
        self.assertEqual(manifest_problem(
            self.root, self.output, self.manifest, source_sha256=SHA, render=self.render, source_path=source,
        ), PROBLEM_CHANGED)
        # A copy linked for an upload is not the source: still the export.
        other = self.root / "input" / "Tập 10.mp4"
        other.write_bytes(b"another video")
        self.assertIsNone(manifest_problem(
            self.root, self.output, self.manifest, source_sha256=SHA, render=self.render, source_path=other,
        ))


class ExistingExportTests(TempRoot):
    def test_a_file_without_a_manifest_is_not_an_export(self):
        queue = make_queue(blur())
        output = review_export_paths(self.root, queue)[1]
        output.parent.mkdir(parents=True)
        output.write_bytes(b"not a video")
        self.assertIsNone(existing_review_export(self.root, queue))

    def test_its_own_export_first_then_the_one_main_named(self):
        queue = make_queue(blur(), keep())
        legacy = paths_of_main(self.root, queue)[1]
        write_export(self.root, legacy, queue)
        found, manifest = existing_review_export(self.root, queue)
        self.assertEqual((found, manifest["output"]["path"]), (legacy, legacy.relative_to(self.root).as_posix()))
        current = review_export_paths(self.root, queue)[1]
        write_export(self.root, current, queue)
        self.assertEqual(existing_review_export(self.root, queue)[0], current)
        # A broken file at its own name does not hide a proven export under the old one.
        current.write_bytes(b"not a video")
        self.assertEqual(existing_review_export(self.root, queue)[0], legacy)

    def test_an_export_of_other_operations_under_the_old_name_is_not_reused(self):
        exported = make_queue(blur(), keep())
        legacy = paths_of_main(self.root, exported)[1]
        write_export(self.root, legacy, exported)
        for changed in (
            make_queue(blur(decision_blur_edge_mode="vertical_only"), keep()),
            make_queue(blur(**DISCRETE), keep()),
        ):
            with self.subTest(items=changed["items"]):
                self.assertEqual(paths_of_main(self.root, changed)[1], legacy)
                self.assertIsNone(existing_review_export(self.root, changed))

    def test_an_unfinished_review_has_no_export(self):
        finished = make_queue(blur(), keep())
        unfinished = make_queue(blur(), keep(decision=None))
        write_export(self.root, paths_of_main(self.root, unfinished)[1], finished)
        self.assertIsNone(existing_review_export(self.root, unfinished))

    def test_a_hostile_manifest_is_never_an_export(self):
        # No error escapes: finalize, the review page and the startup import go on without it.
        queue = make_queue(blur())
        output = review_export_paths(self.root, queue)[1]
        manifest = write_export(self.root, output, queue)
        manifest["output"]["path"] = "output/bad\0name-reviewed.mp4"
        for name, text in (
            ("NUL in the path", json.dumps(manifest)),
            ("nested too deep", "[" * 100_000),
            ("not JSON", "{"),
        ):
            with self.subTest(case=name):
                export_manifest_path(output).write_text(text, encoding="utf-8")
                self.assertIsNone(existing_review_export(self.root, queue))

    def test_a_hard_link_of_the_source_is_not_its_export(self):
        source = self.root / "input" / "Tập 9.mp4"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"source video " * 64)
        queue = make_queue(blur(), source=str(source))
        output = review_export_paths(self.root, queue)[1]
        output.parent.mkdir(parents=True)
        os.link(source, output)
        write_export(self.root, output, queue, content=source.read_bytes())
        self.assertIsNone(existing_review_export(self.root, queue))


class FinalizeReuseTests(TempRoot):
    """ControlCenter.finalize on a temp root with a real JobStore and JobScheduler."""

    def setUp(self):
        super().setUp()
        for name in ("input", "reports/jobs/tap9", "output", "work", "state", "logs"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.store = JobStore(self.root / "state" / "control-center.sqlite3")
        self.addCleanup(self.store.close)
        center = ControlCenter.__new__(ControlCenter)
        center.root = self.root
        center.store = self.store
        center._stopping = threading.Event()
        center.scheduler = JobScheduler(self.root, self.store)
        self.center = center
        patcher = patch("biliflow.scheduler.pipeline_stages", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.source = self.root / "input" / "Tập 9.mp4"
        self.source.write_bytes(b"source video " * 64)
        self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.queue_path = self.root / "reports" / "jobs" / "tap9" / "review-queue.json"
        self.set_items(blur(), keep())
        stat = self.source.stat()
        job = self.store.upsert_job(
            job_key="tap9", source_path=self.source, source_sha256=self.sha,
            source_size_bytes=stat.st_size, source_mtime_ns=stat.st_mtime_ns,
            content_style="animation", state="READY_TO_EXPORT",
        )
        self.job_id = int(job["id"])
        self.store.update_job(self.job_id, active_queue_path="reports/jobs/tap9/review-queue.json", active_revision=1)

    def set_items(self, *items):
        self.queue_path.write_text(json.dumps(make_queue(*items, source=str(self.source), sha=self.sha)),
                                   encoding="utf-8")

    def queue(self):
        """The queue as finalize sees it (with the default size policy it records)."""
        value = json.loads(self.queue_path.read_text(encoding="utf-8"))
        value["export_size_policy"] = normalize_output_size_policy("default", None)
        return value

    def finalize(self):
        with (
            patch("biliflow.control_center.build_edit_plan", return_value={"status": "READY_FOR_PREVIEW"}),
            patch("biliflow.control_center.authorize_final_from_resolved_review"),
        ):
            return self.center.finalize(self.job_id)

    def relative(self, path):
        return path.relative_to(self.root).as_posix()

    def test_a_file_the_manifest_does_not_prove_is_never_taken_for_the_export(self):
        output = review_export_paths(self.root, self.queue())[1]
        broken = {
            "no manifest": None,
            "decode not passed": lambda m: m["encoding"].update(full_decode_validation_passed=False),
            "other operations": lambda m: m.update(operations=[]),
        }
        for name, change in broken.items():
            with self.subTest(case=name):
                if change is None:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(b"not a video")
                else:
                    # The manifest describes these very bytes: only the named field is wrong.
                    write_export(self.root, output, self.queue(), sha=self.sha, change=change,
                                 content=b"not a video")
                before = self.queue_path.read_bytes()
                with self.assertRaises(ValueError) as caught:
                    self.finalize()
                self.assertEqual(str(caught.exception), EXPORT_PATH_TAKEN_MESSAGE.format(name=output.name))
                self.assertEqual(self.store.get_job(self.job_id)["state"], "READY_TO_EXPORT")
                self.assertIsNone(self.store.render_request(self.job_id))
                self.assertEqual(self.queue_path.read_bytes(), before)
                self.assertEqual(output.read_bytes(), b"not a video")

    def test_a_link_left_at_the_export_path_is_refused_before_any_render(self):
        output = review_export_paths(self.root, self.queue())[1]
        try:
            output.symlink_to(self.root / "missing-target.mp4")
        except OSError as error:
            self.skipTest(f"cannot create a symbolic link here: {error}")
        with self.assertRaises(ValueError) as caught:
            self.finalize()
        self.assertEqual(str(caught.exception), EXPORT_PATH_TAKEN_MESSAGE.format(name=output.name))
        self.assertIsNone(self.store.render_request(self.job_id))
        self.assertTrue(os.path.islink(output))

    def test_a_proven_export_is_reused_without_a_render(self):
        output = review_export_paths(self.root, self.queue())[1]
        write_export(self.root, output, self.queue(), sha=self.sha)
        result = self.finalize()
        self.assertEqual((result["status"], result["output"]), ("COMPLETED", self.relative(output)))
        self.assertEqual(self.store.get_job(self.job_id)["state"], "COMPLETED")
        self.assertIsNone(self.store.render_request(self.job_id))

    def test_an_export_named_by_main_is_still_reused(self):
        legacy = paths_of_main(self.root, self.queue())[1]
        self.assertNotEqual(legacy, review_export_paths(self.root, self.queue())[1])
        write_export(self.root, legacy, self.queue(), sha=self.sha)
        result = self.finalize()
        self.assertEqual((result["status"], result["output"]), ("COMPLETED", self.relative(legacy)))
        self.assertIsNone(self.store.render_request(self.job_id))

    def test_a_render_change_main_did_not_see_renders_a_new_export(self):
        for name, changed in (
            ("edge mode", blur(decision_blur_edge_mode="vertical_only")),
            ("detected intervals", blur(**DISCRETE)),
        ):
            with self.subTest(change=name):
                self.set_items(blur(), keep())
                self.store.update_job(self.job_id, state="READY_TO_EXPORT", current_stage=None)
                legacy = paths_of_main(self.root, self.queue())[1]
                write_export(self.root, legacy, self.queue(), sha=self.sha)
                manifest = legacy.with_suffix(".mp4.manifest.json")
                before = (legacy.read_bytes(), manifest.read_bytes())
                self.set_items(changed, keep())
                self.assertEqual(paths_of_main(self.root, self.queue())[1], legacy)
                result = self.finalize()
                expected = review_export_paths(self.root, self.queue())[1]
                self.assertNotIn(expected, (legacy, review_export_paths(self.root, make_queue(
                    blur(), keep(), source=str(self.source), sha=self.sha))[1]))
                self.assertEqual((result["status"], result["output"]), ("QUEUED", self.relative(expected)))
                self.assertEqual(self.store.setting(f"render:{self.job_id}")["output"], self.relative(expected))
                self.assertEqual((legacy.read_bytes(), manifest.read_bytes()), before)
                self.center.scheduler.cancel(self.job_id)


if __name__ == "__main__":
    unittest.main()
