import hashlib
import io
import json
import os
import subprocess
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow.codex_supervisor import collect_visual_evidence
from biliflow.review_evidence import (
    CHUNK_BYTES,
    MAX_STRIP_FRAMES,
    ReviewFrameCache,
    item_evidence,
    parse_range,
    read_json_cached,
    stream_file,
    strip_time,
)
from biliflow.review_workflow import review_resource_status

SHA = "f43cf94aadffb8c127c18fb23a51c58de2bdafcb2f05b1e91bd84be726fb19e9"
JOB = "reports/jobs/troy-run"
REPORT = f"{JOB}/adult/scan.json"
THUMBNAIL = "thumbnails/frame-00001889-944.500s.jpg"
ITEM = "review-935e63a78271"
# Seconds >= 0.95 in the independent Troy CSV (adult-scores-720-1200.csv).
TROY_SEEDS = [
    (938.0, 0.997338), (938.5, 0.996097), (939.0, 0.992761), (939.5, 0.988466),
    (940.0, 0.977749), (940.5, 0.998832), (941.0, 0.995762), (941.5, 0.995475),
    (943.0, 0.958694), (943.5, 0.997203), (944.5, 0.999486), (945.0, 0.975221),
    (946.5, 0.992955), (947.0, 0.999386), (947.5, 0.984713), (949.5, 0.984152),
]


def troy_interval(**extra):
    value = {
        "start_seconds": 928.5, "end_seconds": 950.5, "max_score": 0.999486,
        "strongest_frame": THUMBNAIL, "sample_count": 16, "predicted_label": "porn",
        "sequence_context": {
            "applied": True, "detector_start_seconds": 937.0,
            "detector_end_seconds": 950.5, "supporting_sample_count": 2,
            "context_threshold": 0.7, "maximum_extension_seconds": 8.0,
        },
    }
    value.update(extra)
    return value


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def build_fixture(root: Path, *, intervals=None, items=None, reports=None, source_name="movie.mp4"):
    """Troy-shaped legacy report (interval 12) plus a queue that references it."""
    source = root / "input" / source_name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"not really a video" * 64)
    if intervals is None:
        intervals = [
            {"start_seconds": 10.0 * index, "end_seconds": 10.0 * index + 1, "max_score": 0.96,
             "strongest_frame": f"thumbnails/frame-{index:08d}-{10.0 * index:.3f}s.jpg", "sample_count": 1}
            for index in range(12)
        ] + [troy_interval()]
    write_json(root / REPORT, {
        "scan_type": "nsfw", "sample_fps": 2.0, "threshold": 0.95, "intervals": intervals,
    })
    thumbnail = root / JOB / "adult" / THUMBNAIL
    thumbnail.parent.mkdir(parents=True, exist_ok=True)
    thumbnail.write_bytes(b"\xff\xd8strongest\xff\xd9")
    if items is None:
        items = [{
            "id": ITEM, "category": "adult", "start_seconds": 928.5, "end_seconds": 950.5,
            "max_score": 0.999486, "priority": "high",
            "preview_images": [f"{JOB}/adult/{THUMBNAIL}"],
            "source_candidate_refs": [f"{REPORT}#interval:12"],
            "detected_intervals": [{"start_seconds": 928.5, "end_seconds": 950.5}],
            "decision": None,
        }]
    queue = {
        "schema_version": 1, "status": "REVIEW_REQUIRED",
        "source": {"path": str(source.resolve()), "sha256": SHA, "duration_seconds": 11762.72},
        "reports": [REPORT] if reports is None else reports,
        "items": items, "advisory_items": [],
    }
    queue_path = root / JOB / "review-queue.json"
    write_json(queue_path, queue)
    return queue, queue_path, source


def gaps(evidence):
    times = [evidence["start"], *(frame["t"] for frame in evidence["frames"]), evidence["end"]]
    return [right - left for left, right in zip(times, times[1:])]


class ItemEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_troy_legacy_strip_covers_the_exposed_part(self):
        queue, _, _ = build_fixture(self.root)
        evidence = item_evidence(self.root, queue, ITEM)
        times = [frame["t"] for frame in evidence["frames"]]
        self.assertEqual(times, [929.5, 931.5, 933.5, 935.5, 937.5, 939.5, 941.5, 943.5,
                                 944.5, 945.5, 947.5, 949.5])
        self.assertGreaterEqual(len(times), 8)
        self.assertLessEqual(max(gaps(evidence)), 2.75)
        self.assertTrue(any(937.5 <= value <= 941.5 for value in times))
        self.assertTrue(all((value * 2).is_integer() for value in times))  # 1/sample_fps grid
        self.assertEqual(evidence["strongest"], {"t": 944.5, "score": 0.999486})
        self.assertEqual(
            [frame for frame in evidence["frames"] if frame["kind"] != "context"],
            [{"t": 944.5, "kind": "strongest", "score": 0.999486}],
        )
        self.assertEqual(evidence["seeds"], {
            "known": False, "count": 16, "threshold": 0.95, "samples": None,
            "windows": [{"start": 937.0, "end": 950.5, "count": 16}],
        })
        self.assertEqual(evidence["context"]["extended"], [{"start": 928.5, "end": 937.0}])
        self.assertTrue(evidence["context"]["applied"])
        self.assertEqual(evidence["context"]["threshold"], 0.7)
        self.assertEqual(evidence["context"]["supporting_sample_count"], 2)
        self.assertEqual(evidence["sample_fps"], 2.0)
        self.assertEqual(evidence["video"], {"available": True, "mime": "video/mp4", "reason": None})
        self.assertEqual((evidence["item_id"], evidence["category"], evidence["start"], evidence["end"]),
                         (ITEM, "adult", 928.5, 950.5))

    def test_detector_samples_put_every_seed_in_the_strip(self):
        samples = [{"timestamp_seconds": t, "score": s} for t, s in TROY_SEEDS]
        intervals = [troy_interval(detector_samples=samples)]
        items = [{
            "id": ITEM, "category": "adult", "start_seconds": 928.5, "end_seconds": 950.5,
            "source_candidate_refs": [f"{REPORT}#interval:0"],
            "detected_intervals": [{"start_seconds": 928.5, "end_seconds": 950.5}],
        }]
        queue, _, _ = build_fixture(self.root, intervals=intervals, items=items)
        evidence = item_evidence(self.root, queue, ITEM)
        frames = {frame["t"]: frame for frame in evidence["frames"]}
        for seconds, score in TROY_SEEDS:
            self.assertIn(seconds, frames)
            self.assertEqual(frames[seconds]["score"], score)
            self.assertIn(frames[seconds]["kind"], {"seed", "strongest"})
        self.assertEqual(frames[944.5]["kind"], "strongest")
        self.assertTrue(evidence["seeds"]["known"])
        self.assertEqual(evidence["seeds"]["count"], 16)
        self.assertEqual(len(evidence["seeds"]["samples"]), 16)
        self.assertLessEqual(len(evidence["frames"]), MAX_STRIP_FRAMES)
        seeds = [seconds for seconds, _ in TROY_SEEDS]
        for frame in evidence["frames"]:
            if frame["kind"] == "context":
                self.assertTrue(all(abs(frame["t"] - seed) > 0.4 for seed in seeds))
                self.assertNotIn("score", frame)

    def test_thinning_above_24_keeps_first_last_and_strongest(self):
        samples = [{"t": 100.0 + 0.5 * index, "score": 0.96} for index in range(60)]
        samples[37]["score"] = 0.999
        strongest = samples[37]["t"]
        intervals = [{
            "start_seconds": 99.0, "end_seconds": 131.0, "max_score": 0.999,
            "strongest_frame": f"thumbnails/frame-00000274-{strongest:.3f}s.jpg",
            "sample_count": 60, "detector_samples": samples,
        }]
        items = [{
            "id": ITEM, "category": "adult", "start_seconds": 99.0, "end_seconds": 131.0,
            "source_candidate_refs": [f"{REPORT}#interval:0"],
            "detected_intervals": [{"start_seconds": 99.0, "end_seconds": 131.0}],
        }]
        queue, _, _ = build_fixture(self.root, intervals=intervals, items=items)
        evidence = item_evidence(self.root, queue, ITEM)
        times = [frame["t"] for frame in evidence["frames"]]
        self.assertEqual(len(times), MAX_STRIP_FRAMES)
        self.assertIn(100.0, times)
        self.assertIn(129.5, times)
        self.assertIn(strongest, times)
        self.assertEqual(evidence["strongest"]["t"], strongest)
        kept_seeds = [frame["t"] for frame in evidence["frames"] if frame["kind"] != "context"]
        self.assertGreater(len(kept_seeds), 3)
        self.assertLessEqual(max(b - a for a, b in zip(kept_seeds, kept_seeds[1:])), 3.0)
        self.assertEqual(evidence["seeds"]["count"], 60)  # the summary still reports every seed

    def test_grouped_item_gets_no_frames_in_gaps(self):
        intervals = [
            {"start_seconds": 100.0, "end_seconds": 104.0, "max_score": 0.97,
             "strongest_frame": "thumbnails/frame-00000203-101.500s.jpg", "sample_count": 3},
            {"start_seconds": 200.0, "end_seconds": 206.0, "max_score": 0.99,
             "strongest_frame": "thumbnails/frame-00000406-203.000s.jpg", "sample_count": 5},
        ]
        windows = [(100.0, 104.0), (200.0, 206.0)]
        items = [{
            "id": ITEM, "category": "adult", "start_seconds": 100.0, "end_seconds": 206.0,
            "source_candidate_refs": [f"{REPORT}#interval:0", f"{REPORT}#interval:1"],
            "detected_intervals": [{"start_seconds": a, "end_seconds": b} for a, b in windows],
        }]
        queue, _, _ = build_fixture(self.root, intervals=intervals, items=items)
        evidence = item_evidence(self.root, queue, ITEM)
        for frame in evidence["frames"]:
            self.assertTrue(any(a <= frame["t"] <= b for a, b in windows), frame)
        self.assertGreaterEqual(sum(frame["kind"] == "context" for frame in evidence["frames"]), 6)
        self.assertEqual(evidence["strongest"], {"t": 203.0, "score": 0.99})
        self.assertIn({"t": 101.5, "kind": "seed", "score": 0.97}, evidence["frames"])
        self.assertEqual(evidence["seeds"]["count"], 8)
        self.assertEqual(len(evidence["seeds"]["windows"]), 2)

    def test_untrusted_references_are_ignored(self):
        outside = self.root / "cache" / "evil.json"
        write_json(outside, {"sample_fps": 2.0, "threshold": 0.5, "intervals": [troy_interval()]})
        unlisted = f"{JOB}/adult/other.json"
        write_json(self.root / unlisted, {"sample_fps": 2.0, "threshold": 0.5, "intervals": [troy_interval()]})
        dotted = f"{JOB}/adult/../adult/scan.json"
        items = [{
            "id": ITEM, "category": "adult", "start_seconds": 928.5, "end_seconds": 950.5,
            "source_candidate_refs": [
                f"{unlisted}#interval:0", f"{dotted}#interval:12", "cache/evil.json#interval:0",
                f"{REPORT}#track:3", f"{REPORT}#interval:99",
            ],
            "detected_intervals": [{"start_seconds": 928.5, "end_seconds": 950.5}],
        }]
        queue, _, _ = build_fixture(self.root, items=items,
                                    reports=[REPORT, dotted, "cache/evil.json"])
        evidence = item_evidence(self.root, queue, ITEM)
        self.assertEqual(evidence["ignored_ref_count"], 5)
        self.assertEqual(evidence["refs"], [])
        self.assertIsNone(evidence["strongest"])
        self.assertEqual(evidence["seeds"]["count"], 0)
        self.assertTrue(all(frame["kind"] == "context" for frame in evidence["frames"]))
        self.assertGreaterEqual(len(evidence["frames"]), 8)

    def test_advisory_items_resolve_and_unknown_items_raise(self):
        queue, _, _ = build_fixture(self.root)
        queue["advisory_items"] = [dict(queue["items"][0], id="advisory-1")]
        self.assertEqual(item_evidence(self.root, queue, "advisory-1")["strongest"]["t"], 944.5)
        with self.assertRaises(KeyError):
            item_evidence(self.root, queue, "missing")

    def test_frame_time_must_belong_to_the_strip(self):
        queue, _, _ = build_fixture(self.root)
        evidence = item_evidence(self.root, queue, ITEM)
        self.assertEqual(strip_time(evidence, "939.5"), 939.5)
        self.assertEqual(strip_time(evidence, 939.5009), 939.5)
        for value in (940.0, 939.502, "abc", "nan", None):
            with self.assertRaises(ValueError):
                strip_time(evidence, value)

    def test_unsupported_container_is_reported(self):
        queue, _, _ = build_fixture(self.root, source_name="movie.mkv")
        video = item_evidence(self.root, queue, ITEM)["video"]
        self.assertEqual(video, {"available": False, "mime": None, "reason": "unsupported_container"})

    def test_reports_are_cached_by_mtime(self):
        queue, _, _ = build_fixture(self.root)
        first = read_json_cached(self.root / REPORT)
        self.assertIs(read_json_cached(self.root / REPORT), first)
        payload = json.loads((self.root / REPORT).read_text(encoding="utf-8"))
        payload["threshold"] = 0.9
        write_json(self.root / REPORT, payload)
        stat = (self.root / REPORT).stat()
        os.utime(self.root / REPORT, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
        self.assertEqual(read_json_cached(self.root / REPORT)["threshold"], 0.9)
        self.assertEqual(item_evidence(self.root, queue, ITEM)["seeds"]["threshold"], 0.9)

    def test_resource_status_declares_cpu_only_evidence_preview(self):
        _, queue_path, _ = build_fixture(self.root)
        status = review_resource_status(project_root=self.root, queue_path=queue_path)
        self.assertEqual(status["evidence_preview"], {
            "on_demand_ffmpeg_frames": True, "uses_gpu": False, "encodes_video": False,
        })


def fake_ffmpeg(payload=b"\xff\xd8frame\xff\xd9", calls=None):
    def run(command, **kwargs):
        if calls is not None:
            calls.append((list(command), kwargs))
        Path(command[-1]).write_bytes(payload)
        return subprocess.CompletedProcess(command, 0, b"", b"")
    return run


class ReviewFrameCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.queue, self.queue_path, self.source = build_fixture(self.root)
        self.cache = ReviewFrameCache(self.root, self.root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe")

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self):
        files = [self.queue_path, self.root / REPORT, self.source,
                 self.root / JOB / "adult" / THUMBNAIL]
        return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}

    def files(self):
        return {path.relative_to(self.root).as_posix() for path in self.root.rglob("*") if path.is_file()}

    def test_command_is_read_only_cpu_only_and_cached(self):
        calls = []
        before = self.snapshot()
        files_before = self.files()
        with patch("biliflow.review_evidence.subprocess.run", side_effect=fake_ffmpeg(calls=calls)):
            target = self.cache.frame(self.source, SHA, 939.5)
            again = self.cache.frame(self.source, SHA, 939.5)
        self.assertEqual(target, again)
        self.assertEqual(len(calls), 1)  # second call is a cache hit
        self.assertEqual(
            target, self.root / "cache" / "review-frames" / SHA[:16] / "0000939500-w640.jpg",
        )
        self.assertEqual(target.read_bytes(), b"\xff\xd8frame\xff\xd9")
        command, kwargs = calls[0]
        joined = " ".join(command).casefold()
        for forbidden in ("hwaccel", "cuda", "nvenc", "cuvid", "qsv", "d3d11va", "dxva"):
            self.assertNotIn(forbidden, joined)
        self.assertEqual(command[command.index("-i") + 1], str(self.source))
        self.assertEqual(command[command.index("-ss") + 1], "939.500")
        self.assertLess(command.index("-ss"), command.index("-i"))
        for flag in ("-nostdin", "-an", "-sn", "-dn"):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("-map") + 1], "0:v:0")
        self.assertEqual(command[command.index("-frames:v") + 1], "1")
        self.assertEqual(command[command.index("-vf") + 1], "scale=640:-2")
        self.assertEqual(command[command.index("-threads") + 1], "2")
        output = Path(command[-1])
        self.assertEqual(output.parent, target.parent)
        self.assertTrue(output.name.endswith(".tmp.jpg"))
        self.assertFalse(output.exists())  # atomically replaced
        self.assertEqual(kwargs["timeout"], 30.0)
        self.assertTrue(kwargs["check"])
        if os.name == "nt":
            self.assertTrue(kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)
            self.assertTrue(kwargs["creationflags"] & subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.files() - files_before, {target.relative_to(self.root).as_posix()})

    def test_failed_extraction_leaves_no_temporary_file(self):
        def failing(command, **kwargs):
            Path(command[-1]).write_bytes(b"partial")
            raise subprocess.CalledProcessError(1, command, b"", b"decode error")

        for side_effect in (
            failing,
            subprocess.TimeoutExpired("ffmpeg", 30),
            fake_ffmpeg(payload=b""),
        ):
            with self.subTest(side_effect=side_effect):
                with patch("biliflow.review_evidence.subprocess.run", side_effect=side_effect):
                    with self.assertRaises(RuntimeError):
                        self.cache.frame(self.source, SHA, 941.5)
                directory = self.root / "cache" / "review-frames" / SHA[:16]
                self.assertEqual(list(directory.iterdir()) if directory.exists() else [], [])

    def test_invalid_inputs_are_rejected_before_ffmpeg(self):
        with patch("biliflow.review_evidence.subprocess.run") as run:
            for sha, seconds in (("../../x", 1.0), ("", 1.0), (SHA, -1.0), (SHA, "abc")):
                with self.assertRaises(ValueError):
                    self.cache.frame(self.source, sha, seconds)
        run.assert_not_called()

    def test_visual_ai_evidence_is_unchanged_and_never_uses_strip_frames(self):
        before = collect_visual_evidence(self.root, self.queue, max_images=36, max_images_per_item=3)
        with patch("biliflow.review_evidence.subprocess.run", side_effect=fake_ffmpeg()):
            evidence = item_evidence(self.root, self.queue, ITEM)
            for frame in evidence["frames"]:
                self.cache.frame(self.source, SHA, frame["t"])
        after = collect_visual_evidence(self.root, self.queue, max_images=36, max_images_per_item=3)
        self.assertEqual(before, after)
        self.assertEqual([entry["path"] for entry in after],
                         [(self.root / JOB / "adult" / THUMBNAIL).resolve()])
        strip_dir = (self.root / "cache" / "review-frames").resolve()
        self.assertEqual(len(list(strip_dir.rglob("*.jpg"))), len(evidence["frames"]))
        # Even a queue pointing at a strip frame cannot route it to Visual AI.
        poisoned = json.loads(json.dumps(self.queue))
        poisoned["items"][0]["preview_images"] = [
            path.relative_to(self.root).as_posix() for path in strip_dir.rglob("*.jpg")
        ]
        self.assertEqual(
            collect_visual_evidence(self.root, poisoned, max_images=36, max_images_per_item=24), [],
        )


class FakeHandler:
    def __init__(self, range_header=None, *, fail_after=None):
        self.headers = {"Range": range_header} if range_header else {}
        self.status = None
        self.sent_headers = {}
        self.wfile = io.BytesIO()
        self.writes = 0
        self.fail_after = fail_after
        original = self.wfile.write

        def write(data):
            if self.fail_after is not None and self.writes >= self.fail_after:
                raise ConnectionResetError("client went away")
            self.writes += 1
            return original(data)

        self.wfile.write = write

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.sent_headers[name] = value

    def end_headers(self):
        pass


class RangeStreamingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "movie.mp4"
        self.data = bytes(range(256)) * 4
        self.path.write_bytes(self.data)

    def tearDown(self):
        self.temp.cleanup()

    def test_parse_range(self):
        self.assertIsNone(parse_range(None, 100))
        self.assertEqual(parse_range("bytes=0-9", 100), (0, 9))
        self.assertEqual(parse_range("bytes=90-", 100), (90, 99))
        self.assertEqual(parse_range("bytes=-10", 100), (90, 99))
        self.assertEqual(parse_range("bytes=50-1000", 100), (50, 99))
        for header in ("bytes=100-", "bytes=9-3", "bytes=-", "items=0-1", "bytes=0-1,4-5"):
            with self.assertRaises(ValueError):
                parse_range(header, 100)

    def test_partial_content_is_exact(self):
        handler = FakeHandler("bytes=5-14")
        self.assertEqual(stream_file(handler, self.path, "video/mp4"), 10)
        self.assertEqual(handler.status, 206)
        self.assertEqual(handler.wfile.getvalue(), self.data[5:15])
        self.assertEqual(handler.sent_headers["Content-Range"], f"bytes 5-14/{len(self.data)}")
        self.assertEqual(handler.sent_headers["Content-Length"], "10")
        self.assertEqual(handler.sent_headers["Accept-Ranges"], "bytes")
        self.assertEqual(handler.sent_headers["Cache-Control"], "no-store")
        self.assertEqual(handler.sent_headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(handler.sent_headers["Content-Type"], "video/mp4")

    def test_whole_file_and_unsatisfiable_range(self):
        handler = FakeHandler()
        stream_file(handler, self.path, "video/mp4")
        self.assertEqual((handler.status, handler.wfile.getvalue()), (200, self.data))
        handler = FakeHandler(f"bytes={len(self.data)}-")
        stream_file(handler, self.path, "video/mp4")
        self.assertEqual(handler.status, 416)
        self.assertEqual(handler.sent_headers["Content-Range"], f"bytes */{len(self.data)}")
        self.assertEqual(handler.wfile.getvalue(), b"")

    def test_large_files_stream_in_chunks_and_stop_on_disconnect(self):
        big = Path(self.temp.name) / "big.mp4"
        big.write_bytes(b"x" * (2 * CHUNK_BYTES + 17))
        handler = FakeHandler()
        self.assertEqual(stream_file(handler, big, "video/mp4"), 2 * CHUNK_BYTES + 17)
        self.assertEqual(handler.writes, 3)
        handler = FakeHandler(fail_after=1)
        self.assertEqual(stream_file(handler, big, "video/mp4"), CHUNK_BYTES)
        stop = threading.Event()
        stop.set()
        handler = FakeHandler()
        self.assertEqual(stream_file(handler, big, "video/mp4", stop), 0)
        self.assertEqual(self.path.read_bytes(), self.data)


if __name__ == "__main__":
    unittest.main()


class HardeningTests(unittest.TestCase):
    def test_a_reference_that_no_longer_touches_its_item_is_ignored(self):
        from biliflow import review_evidence as evidence
        with TemporaryDirectory() as temp:
            root = Path(temp)
            report = root / "reports/jobs/j/adult/scan.json"
            report.parent.mkdir(parents=True)
            report.write_text(json.dumps({"threshold": 0.95, "sample_fps": 2.0, "intervals": [
                {"start_seconds": 600.0, "end_seconds": 605.0, "max_score": 0.99, "sample_count": 3,
                 "strongest_frame": "thumbnails/frame-00001204-602.000s.jpg"}]}), encoding="utf-8")
            queue = {"reports": ["reports/jobs/j/adult/scan.json"], "items": [{
                "id": "x", "category": "adult", "start_seconds": 740.0, "end_seconds": 745.0,
                "source_candidate_refs": ["reports/jobs/j/adult/scan.json#interval:0"]}]}
            result = evidence.item_evidence(root, queue, "x")
            self.assertIsNone(result["strongest"])
            self.assertEqual(result["ignored_ref_count"], 1)

    def test_frame_locks_do_not_accumulate(self):
        from biliflow import review_evidence as evidence
        with TemporaryDirectory() as temp:
            root = Path(temp)
            cache = evidence.ReviewFrameCache(root, root / "ffmpeg.exe")

            def fake_run(command, **_kwargs):
                Path(command[-1]).write_bytes(b"jpg")
            with patch("biliflow.review_evidence.subprocess.run", side_effect=fake_run):
                for seconds in (1.0, 2.0, 3.0, 2.0):
                    cache.frame(root / "video.mp4", "a" * 64, seconds)
            self.assertEqual(evidence._TARGET_LOCKS, {})
