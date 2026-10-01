import contextlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from biliflow.golden_label_app import EASY_PAGE, PAGE, GoldenLabelApp, parse_range
from biliflow.golden_set import canonical_sha256, write_json_atomic

SHA = "a" * 64


class RangeTest(unittest.TestCase):
    def test_parse_range(self):
        self.assertIsNone(parse_range(None, 100))
        self.assertEqual(parse_range("bytes=10-19", 100), (10, 19))
        self.assertEqual(parse_range("bytes=90-", 100), (90, 99))
        self.assertEqual(parse_range("bytes=-5", 100), (95, 99))
        self.assertEqual(parse_range("bytes=95-200", 100), (95, 99))
        for bad in ("bytes=100-", "bytes=-", "items=1-2", "bytes=5-2"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_range(bad, 100)


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        video = root / "input" / "clip.mp4"
        video.parent.mkdir()
        self.video_bytes = bytes(range(256)) * 40
        video.write_bytes(self.video_bytes)
        (root / "secret.txt").write_text("private", encoding="utf-8")
        labels = root / "annotations/golden/v1"
        manifest = {"schema_version": 1, "golden_set": "v1",
                    "sources": {"src": {"path": "input/clip.mp4", "size_bytes": len(self.video_bytes), "sha256": SHA,
                                        "duration_seconds": 100.0, "width": 1920, "height": 1080, "fps": 25.0}},
                    "segments": [{"id": "S1", "source": "src", "start_seconds": 0.0, "end_seconds": 50.0,
                                  "split": "dev", "purpose": "test"}]}
        write_json_atomic(labels / "segments.json", manifest)
        suggestion = {"id": "sug-1", "segment_id": "S1", "category": "adult", "group": "adult", "start_seconds": 1.0,
                      "end_seconds": 2.0, "region_source_pixels": None, "advisory": True}
        write_json_atomic(root / "suggestions.json", {"manifest_sha256": canonical_sha256(manifest),
                                                      "suggestions": [suggestion]})
        self.app = GoldenLabelApp(root, labels_dir=labels, suggestions_path=root / "suggestions.json",
                                  frames_dir=root / "frames", ffmpeg=root / "missing-ffmpeg.exe", port=0)
        self.app.start_background()
        self.base = f"http://127.0.0.1:{self.app.port}"

    def tearDown(self):
        self.app.stop()
        self.temp.cleanup()

    def request(self, path, body=None, token=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data, headers=headers or {},
                                         method="POST" if body is not None else "GET")
        if token:
            request.add_header("X-Golden-Token", token)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read(), error.headers

    def test_video_range_and_no_other_files(self):
        status, body, headers = self.request("/video/src", headers={"Range": "bytes=100-199"})
        self.assertEqual((status, body, headers["Content-Range"]), (206, self.video_bytes[100:200],
                                                                    f"bytes 100-199/{len(self.video_bytes)}"))
        status, body, _ = self.request("/video/src")
        self.assertEqual((status, len(body)), (200, len(self.video_bytes)))
        self.assertEqual(self.request("/video/src", headers={"Range": "bytes=999999-"})[0], 416)
        self.assertEqual(self.request("/video/..%2Fsecret.txt")[0], 404)
        self.assertEqual(self.request("/video/secret")[0], 404)
        self.assertEqual(self.request("/frame/src?t=500")[0], 400)

    def test_writes_need_token_and_current_revision(self):
        event = {"segment_id": "S1", "category": "adult", "start_seconds": 1, "end_seconds": 2,
                 "expected_action": "CUT", "severity": "must_catch", "from_suggestion": "sug-1"}
        self.assertEqual(self.request("/api/events", {"event": event, "revision": 0})[0], 403)
        token = json.loads(self.request("/api/session")[1])["token"]
        status, body, _ = self.request("/api/events", {"event": event, "revision": 0}, token)
        self.assertEqual(status, 200)
        state = json.loads(body)["state"]
        self.assertEqual((state["labels"]["revision"], state["unresolved"]["S1"]), (1, 0))
        self.assertNotIn("path", state["sources"]["src"])
        self.assertEqual(self.request("/api/events", {"event": event, "revision": 0}, token)[0], 409)
        self.assertEqual(self.request("/api/segments/S1/status", {"status": "complete", "revision": 1}, token)[0], 200)
        self.assertEqual(self.request("/api/events", {"event": event, "revision": 2}, token)[0], 400)

    def test_foreign_host_header_is_refused(self):
        self.assertEqual(self.request("/api/state", headers={"Host": "evil.example:80"})[0], 403)

    def test_easy_page_is_default_and_full_page_remains(self):
        status, body, _ = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("chế độ dễ", body.decode("utf-8"))
        status, body, _ = self.request("/full")
        self.assertEqual(status, 200)
        self.assertIn("Gán nhãn Golden Set", body.decode("utf-8"))

    def test_state_carries_easy_plan(self):
        state = json.loads(self.request("/api/state")[1])
        self.assertEqual(state["easy"]["watermarks"], [])
        self.assertEqual(state["easy"]["cards"], {})  # the only suggestion is a low-value advisory


if __name__ == "__main__":
    unittest.main()


class PhoneModeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        video = root / "input" / "clip.mp4"
        video.parent.mkdir()
        video.write_bytes(b"0123456789" * 10)
        labels = root / "annotations/golden/v1"
        write_json_atomic(labels / "segments.json", {
            "schema_version": 1, "golden_set": "v1",
            "sources": {"src": {"path": "input/clip.mp4", "size_bytes": 100, "sha256": SHA, "duration_seconds": 100.0,
                                "width": 1920, "height": 1080, "fps": 25.0}},
            "segments": [{"id": "S1", "source": "src", "start_seconds": 0.0, "end_seconds": 50.0, "split": "dev",
                          "purpose": "test"}]})
        self.app = GoldenLabelApp(root, labels_dir=labels, suggestions_path=root / "none.json",
                                  frames_dir=root / "frames", ffmpeg=root / "ffmpeg.exe", port=0, access_code="secret42")
        self.app.start_background()
        self.base = f"http://127.0.0.1:{self.app.port}"

    def tearDown(self):
        self.app.stop()
        self.temp.cleanup()

    def get(self, path, cookie=None):
        request = urllib.request.Request(self.base + path)
        if cookie:
            request.add_header("Cookie", cookie)
        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(request) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read(), error.headers

    def test_every_request_needs_the_code(self):
        status, body, _ = self.get("/")
        self.assertEqual(status, 401)
        self.assertIn("Mã truy cập", body.decode("utf-8"))
        for path in ("/api/state", "/api/session", "/video/src", "/frame/src?t=1"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path)[0], 401)
        self.assertEqual(self.get("/?code=wrong")[0], 401)

    def test_link_with_code_sets_cookie_and_cookie_opens_everything(self):
        status, _, headers = self.get("/?code=secret42")
        self.assertEqual((status, headers["Location"]), (303, "/"))
        self.assertIn("golden_access=secret42", headers["Set-Cookie"])
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        cookie = "golden_access=secret42"
        self.assertEqual(self.get("/", cookie)[0], 200)
        self.assertEqual(self.get("/video/src", cookie)[0], 200)
        token = json.loads(self.get("/api/session", cookie)[1])["token"]
        request = urllib.request.Request(self.base + "/api/segments/S1/status", data=b'{"status":"in_progress","revision":0}',
                                         headers={"X-Golden-Token": token}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as caught:  # token alone is not enough
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 403)
        request.add_header("Cookie", cookie)
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.status, 200)

    def test_code_typed_on_a_phone_keyboard(self):
        self.assertEqual(self.get("/?code=%20SECRET42")[0], 303)  # auto-capitalised, stray space
        self.assertEqual(self.get("/?code=m%C3%A3")[0], 401)  # non-ASCII text is just a wrong code
        self.assertEqual(self.get("/api/state", "golden_access=mã")[0], 401)

    def test_lan_binding_without_code_is_refused(self):
        with self.assertRaises(ValueError):
            GoldenLabelApp(Path(self.temp.name), labels_dir=Path(self.temp.name) / "annotations/golden/v1",
                           suggestions_path=Path(self.temp.name) / "none.json", frames_dir=Path(self.temp.name),
                           ffmpeg=Path(self.temp.name), host="192.168.1.100", port=0)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def small_manifest(name="v1"):
    value = {"schema_version": 1, "golden_set": name,
             "sources": {"src": {"path": "input/clip.mp4", "size_bytes": 100, "sha256": SHA, "duration_seconds": 100.0,
                                 "width": 1920, "height": 1080, "fps": 25.0}},
             "segments": [{"id": "S1" if name == "v1" else "N1", "source": "src", "start_seconds": 0.0,
                           "end_seconds": 50.0, "split": "dev", "purpose": "test"}]}
    if name != "v1":
        value.update(extends="v1", extends_manifest_sha256="d" * 64)
    return value


class PageTitleTest(unittest.TestCase):
    def test_pages_are_titled_with_the_manifests_set(self):
        for name in ("v1", "v1.1"):
            with self.subTest(set=name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                (root / "input").mkdir()
                (root / "input" / "clip.mp4").write_bytes(b"0123456789" * 10)
                labels = root / "annotations/golden" / name
                write_json_atomic(labels / "segments.json", small_manifest(name))
                app = GoldenLabelApp(root, labels_dir=labels, suggestions_path=root / "none.json",
                                     frames_dir=root / "frames", ffmpeg=root / "ffmpeg.exe", port=0)
                try:
                    for easy in (True, False):
                        page = app.page(easy)
                        self.assertIn(f"Gán nhãn Golden Set {name}", page)
                        self.assertIn(f"<title>Gán nhãn Golden Set {name}", page)
                        self.assertNotIn("__GOLDEN_SET__", page)
                    self.assertIn("chế độ dễ", app.page(True))
                    stored = json.loads((labels / "events.json").read_text(encoding="utf-8"))
                    self.assertEqual(stored["golden_set"], name)
                finally:
                    app.stop()


class ServerSetRoutingTest(unittest.TestCase):
    """scripts/golden_label_server.py --set picks the label directory, hints, frames and phone link."""

    def setUp(self):
        import sys
        scripts = str(Path(__file__).resolve().parents[1] / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import golden_label_server
        self.server = golden_label_server
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.calls = []
        test = self

        class FakeApp:
            def __init__(self, root, **kwargs):
                test.calls.append(dict(kwargs, root=root))

            def serve(self):
                test.link_seen = [p for p in (test.root / "reports/benchmarks").rglob("phone-link.txt")]

        self.patches = [patch.object(golden_label_server, "ROOT", self.root),
                        patch.object(golden_label_server, "GoldenLabelApp", FakeApp),
                        patch.object(golden_label_server, "lan_address", lambda: "192.168.1.50"),
                        patch("webbrowser.open")]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def run_server(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.server.main(list(argv))

    def test_set_selects_every_path(self):
        for name in ("v1", "v1.1"):
            write_json_atomic(self.root / "annotations/golden" / name / "segments.json", small_manifest(name))
        self.run_server("--set", "v1.1", "--no-browser")
        self.run_server("--no-browser")
        bench = self.root / "reports/benchmarks"
        self.assertEqual([(c["labels_dir"], c["suggestions_path"], c["frames_dir"]) for c in self.calls], [
            (self.root / "annotations/golden/v1.1", bench / "golden-v1.1/prefill/suggestions.json",
             bench / "golden-v1.1/frames"),
            (self.root / "annotations/golden/v1", bench / "golden-v1/prefill/suggestions.json",
             bench / "golden-v1/frames")])
        self.assertEqual({c["host"] for c in self.calls}, {"127.0.0.1"})

    def test_phone_link_file_belongs_to_the_set_and_dies_with_the_server(self):
        write_json_atomic(self.root / "annotations/golden/v1.1/segments.json", small_manifest("v1.1"))
        self.run_server("--set", "v1.1", "--phone")
        link = self.root / "reports/benchmarks/golden-v1.1/phone-link.txt"
        self.assertEqual(self.link_seen, [link])
        self.assertFalse(link.exists())
        self.assertEqual((self.calls[0]["host"], len(self.calls[0]["access_code"])), ("192.168.1.50", 8))

    def test_a_set_without_manifest_or_an_unknown_set_is_refused(self):
        for argv in (["--set", "v1.1"], ["--set", "v2"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                self.run_server(*argv)
        self.assertEqual(self.calls, [])
        self.assertFalse((self.root / "annotations").exists())  # nothing is created for a missing set


# --- "có thật · giữ" on the easy page (2026-10-01) -------------------------------------------------

def page_script(page: str) -> str:
    scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
    assert len(scripts) == 1
    return scripts[0]


class EasyPageWordingTest(unittest.TestCase):
    """Safety questions ask "is it really there" first; keeping real content is not "Máy sai"."""

    def test_safety_questions_offer_five_answers(self):
        for text in ("1 · ✘ Máy sai — cảnh bình thường", "2 · ✔ Có thật — vẫn giữ nguyên", "3 · ✔ Có thật — làm mờ",
                     "4 · ✔ Có thật — cắt bỏ", "5 · Không chắc"):
            self.assertIn(text, EASY_PAGE)
        self.assertIn("Máy báo có <b>${NAMES[s.category]}</b> ở đoạn này. Có thật không, và bạn sẽ làm gì khi xuất video?",
                      EASY_PAGE)
        self.assertIn("adult:'cảnh 18+',gore:'máu me',violence:'bạo lực'", EASY_PAGE)
        self.assertIn("Chọn 'Máy sai' chỉ khi đoạn này KHÔNG có nội dung đó. "
                      "Có thật mà bạn muốn giữ thì chọn 'Có thật — vẫn giữ nguyên'.", EASY_PAGE)
        # the "present" answer is a KEEP label that says the content is there
        self.assertIn("if(kind==='present')return {...base,expected_action:'KEEP',content_present:true,"
                      "severity:'should_catch',region_source_pixels:null};", EASY_PAGE)
        self.assertIn("notes:'chế độ dễ'", EASY_PAGE)

    def test_logo_questions_keep_four_answers_with_clearer_wording(self):
        for text in ("1 · ✘ Sai — để nguyên", "2 · ✔ Đúng — làm mờ", "3 · ✔ Đúng — cắt bỏ", "4 · Không chắc",
                     "Thứ nằm TRONG khung đỏ có phải logo/chữ quảng cáo không?",
                     "Trong cảnh này máy thấy logo/chữ quảng cáo — đúng không?",
                     "Logo/watermark đã có khung xanh thì không cần khoanh lại."):
            self.assertIn(text, EASY_PAGE)
        self.assertNotIn("Máy nói đúng không?", EASY_PAGE)

    def test_flag_form_and_label_list(self):
        self.assertIn("flagged('gore','present')\">Máu me · Có thật — vẫn giữ nguyên", EASY_PAGE)
        self.assertIn("flagged('violence','present')\">Bạo lực · Có thật — vẫn giữ nguyên", EASY_PAGE)
        self.assertNotIn("flagged('adult','present')", EASY_PAGE)
        self.assertIn("e.content_present?'có thật · giữ':'giữ'", EASY_PAGE)
        # green boxes still only show BLUR/CUT labels with a box
        self.assertIn("e.region_source_pixels&&!e.ambiguous&&e.expected_action!=='KEEP'", EASY_PAGE)
        self.assertIn("KEEP · có thật", PAGE)  # the full page keeps the flag when a label is edited there

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_scripts_parse_and_number_keys_follow_the_buttons(self):
        with tempfile.TemporaryDirectory() as temp:
            for name, page in (("full", PAGE), ("easy", EASY_PAGE)):
                path = Path(temp) / f"{name}.js"
                path.write_text(page_script(page), encoding="utf-8")
                checked = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
                self.assertEqual(checked.returncode, 0, checked.stderr)
            script = page_script(EASY_PAGE)
            constants = [line for line in script.splitlines()
                         if line.startswith(("const SAFETY=", "const LOGO_CHOICES=", "const SAFETY_CHOICES=",
                                             "const choices="))]
            self.assertEqual(len(constants), 4)
            probe = Path(temp) / "keys.js"
            probe.write_text("\n".join(constants) + "\nconsole.log(JSON.stringify(['violence','gore','adult','text',"
                             "'visual_logo'].map(c=>choices({category:c}).map(o=>o[0]))))", encoding="utf-8")
            result = subprocess.run(["node", str(probe)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            safety = ["keep", "present", "blur", "cut", "unsure"]
            logo = ["keep", "blur", "cut", "unsure"]
            self.assertEqual(json.loads(result.stdout), [safety, safety, safety, logo, logo])
        # keys 1..n pick the n-th button of the current question
        self.assertIn("/^[1-9]$/.test(k)){const opts=choices(sug(pendingCards(seg)[0]));"
                      "if(+k<=opts.length)answer(opts[+k-1][0])}", EASY_PAGE)


class SafetyAnswerServerTest(unittest.TestCase):
    """The easy page's new answers round-trip through the server and LabelStore."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / "input").mkdir()
        (root / "input" / "clip.mp4").write_bytes(b"0123456789" * 10)
        labels = root / "annotations/golden/v1"
        manifest = small_manifest()
        write_json_atomic(labels / "segments.json", manifest)
        rows = [{"id": "sug-fight", "segment_id": "S1", "category": "violence", "group": "violence",
                 "start_seconds": 5.0, "end_seconds": 9.0, "region_source_pixels": None, "advisory": False},
                {"id": "sug-logo", "segment_id": "S1", "category": "visual_logo", "group": "advertising",
                 "start_seconds": 20.0, "end_seconds": 25.0, "region_source_pixels": None, "advisory": False}]
        write_json_atomic(root / "suggestions.json", {"manifest_sha256": canonical_sha256(manifest), "suggestions": rows})
        self.app = GoldenLabelApp(root, labels_dir=labels, suggestions_path=root / "suggestions.json",
                                  frames_dir=root / "frames", ffmpeg=root / "ffmpeg.exe", port=0)
        self.app.start_background()
        self.base = f"http://127.0.0.1:{self.app.port}"
        self.token = json.loads(urllib.request.urlopen(self.base + "/api/session").read())["token"]

    def tearDown(self):
        self.app.stop()
        self.temp.cleanup()

    def post(self, path, body):
        request = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                         headers={"X-Golden-Token": self.token})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_present_answer_and_already_green_logo(self):
        state = json.loads(urllib.request.urlopen(self.base + "/api/state").read())
        self.assertEqual(state["easy"]["cards"], {"S1": ["sug-fight", "sug-logo"]})
        present = {"segment_id": "S1", "category": "violence", "start_seconds": 5.0, "end_seconds": 9.0,
                   "severity": "should_catch", "from_suggestion": "sug-fight", "notes": "chế độ dễ",
                   "expected_action": "KEEP", "content_present": True, "region_source_pixels": None}
        status, body = self.post("/api/events", {"event": present, "revision": 0})
        self.assertEqual(status, 200)
        saved = body["state"]["labels"]["events"][0]
        self.assertEqual((saved["expected_action"], saved["content_present"], saved["severity"]),
                         ("KEEP", True, "should_catch"))
        self.assertEqual(body["state"]["labels"]["suggestion_resolutions"]["sug-fight"]["resolution"], "accepted")
        status, body = self.post("/api/events", {"event": dict(present, category="visual_logo", from_suggestion=None,
                                                               region_source_pixels={"x": 1, "y": 1, "width": 50,
                                                                                     "height": 20}),
                                                 "revision": 1})
        self.assertEqual(status, 400)  # "có thật · giữ" is only for 18+/gore/violence
        watermark = {"segment_id": "S1", "category": "visual_logo", "start_seconds": 0.0, "end_seconds": 50.0,
                     "severity": "must_catch", "expected_action": "BLUR",
                     "region_source_pixels": {"x": 1500, "y": 40, "width": 300, "height": 60}}
        status, body = self.post("/api/events", {"event": watermark, "revision": 1})
        green = body["result"]["id"]
        status, body = self.post("/api/suggestions/sug-logo/cover", {"event_id": green, "revision": 2})
        self.assertEqual(status, 200)
        self.assertEqual(body["state"]["labels"]["suggestion_resolutions"]["sug-logo"]["resolution"], "covered")
        self.assertEqual(len(body["state"]["labels"]["events"]), 2)  # no duplicate box was added
        status, body = self.post("/api/suggestions/sug-logo/resolve", {"resolution": None, "revision": 3})  # undo
        self.assertEqual(status, 200)
        self.assertNotIn("sug-logo", body["state"]["labels"]["suggestion_resolutions"])
        status, _ = self.post("/api/suggestions/sug-fight/cover", {"event_id": green, "revision": 4})
        self.assertEqual(status, 400)  # an answered violence question is not covered by a logo label


class CoverButtonRuleTest(unittest.TestCase):
    def test_cover_button_needs_a_green_box_where_the_question_points(self):
        from biliflow.golden_label_app import EASY_PAGE
        self.assertIn("function coverCandidates(ev)", EASY_PAGE)
        self.assertIn("const greens=pendingSave.from_suggestion?coverCandidates(pendingSave):[]", EASY_PAGE)
        self.assertIn("if(!r)return g.length===1?g:[];", EASY_PAGE)
