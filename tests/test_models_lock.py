"""The model lock (scripts/models_lock.py, read-only) and the locked download of
scripts/download_models.py for a new install: a fake network, made-up model files and temporary
roots under the install's temp/. Nothing here touches the real models\\ or the network."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import shutil
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMP_PARENT = REPO_ROOT / "temp"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


models_lock = _load("models_lock")
download_models = _load("download_models")
REVISION = "0123456789abcdef0123456789abcdef01234567"
# Shaped like EasyOCR 1.7.2's config.py: one entry builds its characters with ''.join(...), which
# must not hide the other entries; an entry whose url is computed is not read.
EASYOCR_CONFIG = '''
import os
BASE_PATH = os.path.dirname(__file__)
separator_char = []
detection_models = {
    'craft': {'filename': 'craft_mlt_25k.pth', 'url': 'https://files.example/craft.zip', 'md5sum': 'aa'},
}
recognition_models = {
    'gen1': {
        'th_g1': {'filename': 'thai.pth', 'url': 'https://files.example/thai.zip', 'md5sum': 'cc',
                  'characters': ''.join(separator_char) + 'abc'},
        'local_g1': {'filename': 'local.pth', 'url': BASE_PATH + '/local.zip'},
    },
    'gen2': {'latin_g2': {'filename': 'latin_g2.pth', 'url': 'https://files.example/latin.zip', 'md5sum': 'bb'}},
}
'''


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, status: int = 200, headers: dict | None = None, drop_after: int | None = None):
        super().__init__(data)
        self.status, self.headers, self._drop = status, headers or {}, drop_after

    def read(self, size=-1):
        if self._drop is not None and self.tell() >= self._drop:
            raise ConnectionResetError("dropped")
        if self._drop is not None:
            size = min(size if size > 0 else self._drop, self._drop - self.tell())
        return super().read(size)


class FakeOpener:
    def __init__(self, files: dict[str, bytes], *, ranges: bool = True, drops: dict[str, int] | None = None):
        self.files, self.ranges, self.drops, self.requests = files, ranges, dict(drops or {}), []

    def open(self, request, timeout=None):
        url, start = request.full_url, 0
        header = request.get_header("Range")
        self.requests.append((url, header))
        data = self.files[url]
        if header and self.ranges:
            start = int(header.split("=")[1].rstrip("-"))
            body = data[start:]
            return FakeResponse(body, 206, {"Content-Range": f"bytes {start}-{len(data) - 1}/{len(data)}"},
                                self.drops.pop(url, None))
        return FakeResponse(data, 200, {}, self.drops.pop(url, None))


class Root(unittest.TestCase):
    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        self._temp = TemporaryDirectory(dir=TEMP_PARENT)
        self.root = Path(self._temp.name).resolve()
        (self.root / "config").mkdir()
        shutil.copy2(REPO_ROOT / "config" / "license_policy.json", self.root / "config" / "license_policy.json")
        (self.root / "biliflow-install.json").write_text('{"biliflow_install": 1}', encoding="utf-8")

    def tearDown(self):
        self._temp.cleanup()

    def model(self, directory: str, files: dict[str, bytes], **manifest) -> Path:
        folder = self.root / "models" / directory
        for name, data in files.items():
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(data)
        listed = [{"name": name, "bytes": len(data), "sha256": sha(data)} for name, data in files.items()]
        body = {"files": listed, **manifest}
        (folder / "manifest.json").write_text(json.dumps(body, indent=2), encoding="utf-8")
        return folder


class ModelsLockTest(Root):
    def test_a_hugging_face_model_is_locked_from_its_manifest_and_files(self):
        folder = self.model("florence_2_base", {"config.json": b"{}", "modeling_florence2.py": b"code"},
                            repo_id="microsoft/Florence-2-base", revision=REVISION)
        (folder / ".cache" / "huggingface").mkdir(parents=True)
        (folder / ".cache" / "huggingface" / "x.lock").write_bytes(b"")
        before = {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()}
        lock = models_lock.build_lock(self.root, easyocr_files={})
        entry = lock["models"]["florence_2_base"]
        self.assertEqual(entry["problems"], [])
        self.assertEqual((entry["source"], entry["repo_id"], entry["revision"], entry["download_key"]),
                         ("huggingface", "microsoft/Florence-2-base", REVISION, "florence-logo"))
        self.assertEqual([item["name"] for item in entry["files"]], ["config.json", "modeling_florence2.py"])
        # Byte for byte: on Windows write_text gave the manifest CRLF line ends, and the lock keeps them.
        self.assertEqual(entry["manifest_text"], (folder / "manifest.json").read_bytes().decode("utf-8"))
        self.assertEqual(entry["manifest_sha256"], sha((folder / "manifest.json").read_bytes()))
        self.assertEqual({path: path.read_bytes() for path in folder.rglob("*") if path.is_file()}, before)
        self.assertIn("không có thư mục model", lock["models"]["qwen2_vl_2b_instruct"]["problems"])

    def test_differences_and_local_paths_are_problems(self):
        folder = self.model("grounding_dino_tiny", {"config.json": b"{}", "model.safetensors": b"w"},
                            repo_id="IDEA-Research/grounding-dino-tiny", revision="main",
                            note="copied from E:\\somewhere")
        (folder / "extra.bin").write_bytes(b"x")
        (folder / "config.json").write_bytes(b"{ }")
        problems = models_lock.build_lock(self.root, easyocr_files={})["models"]["grounding_dino_tiny"]["problems"]
        self.assertTrue(any("extra.bin" in problem for problem in problems))
        self.assertTrue(any(problem.startswith("config.json") for problem in problems))
        self.assertTrue(any("đường dẫn cục bộ" in problem for problem in problems))
        self.assertTrue(any("revision" in problem for problem in problems))

    def test_easyocr_files_get_their_release_urls(self):
        files = models_lock.parse_easyocr_config(EASYOCR_CONFIG)
        self.assertEqual(files["latin_g2.pth"]["url"], "https://files.example/latin.zip")
        self.assertEqual(files["thai.pth"], {"url": "https://files.example/thai.zip", "md5sum": "cc"})
        self.assertNotIn("local.pth", files)
        self.model("easyocr", {"craft_mlt_25k.pth": b"c", "latin_g2.pth": b"l"}, revision="1.7.2")
        entry = models_lock.build_lock(self.root, easyocr_files=files)["models"]["easyocr"]
        self.assertEqual(entry["problems"], [])
        self.assertEqual([(item["archive_member"], item["md5_upstream"]) for item in entry["files"]],
                         [("craft_mlt_25k.pth", "aa"), ("latin_g2.pth", "bb")])

    def test_an_easyocr_style_manifest_is_compared_without_case_and_kept_byte_for_byte(self):
        weights = b"craft"
        folder = self.root / "models" / "easyocr"
        folder.mkdir(parents=True)
        (folder / "craft_mlt_25k.pth").write_bytes(weights)
        manifest = {"revision": "easyocr-1.7.2", "files": [
            {"name": "craft_mlt_25k.pth", "size_bytes": len(weights), "sha256": sha(weights).upper()}]}
        raw = json.dumps(manifest, indent=2).replace("\n", "\r\n").encode("utf-8")
        (folder / "manifest.json").write_bytes(raw)
        files = {"craft_mlt_25k.pth": {"url": "https://files.example/craft.zip", "md5sum": "aa"}}
        entry = models_lock.build_lock(self.root, easyocr_files=files)["models"]["easyocr"]
        self.assertEqual(entry["problems"], [])
        self.assertEqual(entry["files"][0]["sha256"], sha(weights))
        self.assertEqual((entry["manifest_text"].encode("utf-8"), entry["manifest_sha256"]), (raw, sha(raw)))
        manifest["files"][0]["size_bytes"] = len(weights) + 1
        (folder / "manifest.json").write_bytes(json.dumps(manifest).encode("utf-8"))
        entry = models_lock.build_lock(self.root, easyocr_files=files)["models"]["easyocr"]
        self.assertEqual(entry["problems"], ["craft_mlt_25k.pth: kích thước hoặc SHA-256 khác manifest"])

    def test_files_that_are_not_part_of_a_model_stay_out_of_the_lock(self):
        florence = self.model("florence_2_base", {"processing_florence2.py": b"code"},
                              repo_id="microsoft/Florence-2-base", revision=REVISION)
        bytecode = florence / "__pycache__" / "processing_florence2.cpython-311.pyc"
        bytecode.parent.mkdir()
        bytecode.write_bytes(b"bytecode")
        (florence / "stray.pyc").write_bytes(b"bytecode")
        easyocr = self.model("easyocr", {"craft_mlt_25k.pth": b"c"}, revision="1.7.2")
        (easyocr / ".gitkeep").write_bytes(b"\n")
        files = {"craft_mlt_25k.pth": {"url": "https://files.example/craft.zip", "md5sum": "aa"}}
        models = models_lock.build_lock(self.root, easyocr_files=files)["models"]
        for directory, names in (("florence_2_base", ["processing_florence2.py"]),
                                 ("easyocr", ["craft_mlt_25k.pth"])):
            with self.subTest(directory):
                self.assertEqual(models[directory]["problems"], [])
                self.assertEqual([item["name"] for item in models[directory]["files"]], names)
        self.assertEqual(bytecode.read_bytes(), b"bytecode")

    @patch("sys.stderr")
    def test_the_lock_is_never_written_into_models_or_over_a_file(self, _stderr):
        self.model("easyocr", {"craft_mlt_25k.pth": b"c"}, revision="1.7.2")
        inside = self.root / "models" / "models.lock.json"
        self.assertEqual(models_lock.main(["--root", str(self.root), "--out", str(inside)]), 2)
        self.assertFalse(inside.exists())
        out = self.root / "out" / "models.lock.json"
        out.parent.mkdir()
        out.write_text("keep", encoding="utf-8")
        self.assertEqual(models_lock.main(["--root", str(self.root), "--out", str(out)]), 2)
        self.assertEqual(out.read_text(encoding="utf-8"), "keep")
        with patch("sys.stdout"):
            # The other eight models are missing here, so the lock is written with problems.
            self.assertEqual(models_lock.main(["--root", str(self.root), "--out", str(out), "--replace"]), 1)
        self.assertEqual(sorted(json.loads(out.read_text(encoding="utf-8"))["models"]),
                         sorted(models_lock.PRODUCTION_MODELS))


class LockedDownloadTest(Root):
    def lock_for(self, directory: str, key: str, contents: dict[str, bytes], **entry) -> dict:
        manifest = json.dumps({"model": key, "revision": REVISION}, indent=2)
        base = {
            "download_key": key, "problems": [], "source": "huggingface",
            "repo_id": download_models.MODELS[key].get("repo_id"), "revision": REVISION,
            "files": [{"name": name, "bytes": len(data), "sha256": sha(data)} for name, data in contents.items()],
            "manifest_text": manifest, "manifest_sha256": sha(manifest.encode()),
        }
        base.update(entry)
        return {"version": 1, "models": {directory: base}}

    def url(self, key: str, name: str) -> str:
        return download_models.HF_FILE_URL.format(repo=download_models.MODELS[key]["repo_id"], revision=REVISION,
                                                  name=name)

    def install(self, key, lock, opener):
        return download_models.install_locked(self.root, key, lock, opener=opener, sleep=lambda _: None,
                                              report=lambda _: None)

    def test_a_dropped_connection_resumes_and_every_file_is_checked(self):
        files = {"config.json": b"{}" * 10, "model.safetensors": bytes(range(256)) * 40}
        lock = self.lock_for("qwen2_vl_2b_instruct", "qwen2-vl-2b", files)
        weights = self.url("qwen2-vl-2b", "model.safetensors")
        opener = FakeOpener({self.url("qwen2-vl-2b", n): d for n, d in files.items()}, drops={weights: 3000})
        self.assertEqual(self.install("qwen2-vl-2b", lock, opener), "downloaded")
        folder = self.root / "models" / "qwen2_vl_2b_instruct"
        self.assertEqual((folder / "model.safetensors").read_bytes(), files["model.safetensors"])
        self.assertEqual((folder / "manifest.json").read_text(encoding="utf-8"),
                         lock["models"]["qwen2_vl_2b_instruct"]["manifest_text"])
        self.assertIn((weights, "bytes=3000-"), opener.requests)
        self.assertEqual(sorted(path.name for path in folder.iterdir()),
                         ["config.json", "manifest.json", "model.safetensors"])
        again = FakeOpener({})
        self.assertEqual(self.install("qwen2-vl-2b", lock, again), "ready")
        self.assertEqual(again.requests, [])

    def test_a_server_without_ranges_starts_over(self):
        files = {"model.safetensors": b"abcdefgh" * 100}
        lock = self.lock_for("florence_2_base", "florence-logo", files)
        url = self.url("florence-logo", "model.safetensors")
        part = self.root / "models" / "florence_2_base" / "model.safetensors.part"
        part.parent.mkdir(parents=True)
        part.write_bytes(b"abcdefgh" * 10)
        self.install("florence-logo", lock, FakeOpener({url: files["model.safetensors"]}, ranges=False))
        self.assertEqual((part.parent / "model.safetensors").read_bytes(), files["model.safetensors"])

    def test_a_wrong_hash_is_never_placed(self):
        lock = self.lock_for("florence_2_base", "florence-logo", {"config.json": b"right"})
        opener = FakeOpener({self.url("florence-logo", "config.json"): b"wrong"})
        with self.assertRaisesRegex(download_models.DownloadRefused, "SHA-256"):
            self.install("florence-logo", lock, opener)
        folder = self.root / "models" / "florence_2_base"
        self.assertEqual(list(folder.iterdir()), [])

    def test_an_existing_model_that_differs_is_left_alone(self):
        lock = self.lock_for("florence_2_base", "florence-logo", {"config.json": b"right"})
        folder = self.root / "models" / "florence_2_base"
        folder.mkdir(parents=True)
        (folder / "config.json").write_bytes(b"other")
        with self.assertRaisesRegex(download_models.DownloadRefused, "không ghi đè"):
            self.install("florence-logo", lock, FakeOpener({}))
        (folder / "config.json").write_bytes(b"right")
        (folder / "stray.bin").write_bytes(b"x")
        with self.assertRaisesRegex(download_models.DownloadRefused, "file lạ"):
            self.install("florence-logo", lock, FakeOpener({}))
        self.assertEqual((folder / "config.json").read_bytes(), b"right")

    def test_a_bad_lock_is_refused_before_any_request(self):
        files = {"config.json": b"{}"}
        cases = {
            "problems": {"problems": ["x"]},
            "revision": {"revision": "main"},
            "repo": {"repo_id": "someone/else"},
            "manifest": {"manifest_sha256": "0" * 64},
            "name": {"files": [{"name": "../escape", "bytes": 1, "sha256": "0" * 64}]},
            "source": {"source": "ftp"},
        }
        for label, change in cases.items():
            with self.subTest(label):
                opener = FakeOpener({})
                with self.assertRaises(download_models.DownloadRefused):
                    self.install("florence-logo", self.lock_for("florence_2_base", "florence-logo", files, **change),
                                 opener)
                self.assertEqual(opener.requests, [])
        with self.assertRaises(download_models.DownloadRefused):
            self.install("phi35-vision-int4", {"version": 1, "models": {}}, FakeOpener({}))

    def test_easyocr_comes_from_its_release_archive(self):
        weights = b"pth" * 300
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("latin_g2.pth", weights)
        manifest = json.dumps({"revision": "1.7.2"})
        lock = {"version": 1, "models": {"easyocr": {
            "download_key": "easyocr", "problems": [], "source": "easyocr-release", "revision": "1.7.2",
            "files": [{"name": "latin_g2.pth", "bytes": len(weights), "sha256": sha(weights),
                       "url": "https://files.example/latin.zip", "archive_member": "latin_g2.pth"}],
            "manifest_text": manifest, "manifest_sha256": sha(manifest.encode()),
        }}}
        self.install("easyocr", lock, FakeOpener({"https://files.example/latin.zip": archive.getvalue()}))
        folder = self.root / "models" / "easyocr"
        self.assertEqual((folder / "latin_g2.pth").read_bytes(), weights)
        self.assertEqual(list((self.root / "cache" / "model-downloads").iterdir()), [])

    def test_only_https_and_only_an_install_root(self):
        with self.assertRaises(download_models.DownloadRefused):
            download_models.fetch("http://files.example/a", self.root / "a.part", 1, opener=FakeOpener({}))
        (self.root / "biliflow-install.json").unlink()
        with self.assertRaises(SystemExit):
            download_models.main(["--all", "--project-root", str(self.root)])


if __name__ == "__main__":
    unittest.main()
