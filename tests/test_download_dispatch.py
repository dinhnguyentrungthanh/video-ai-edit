"""Which way a pasted link takes (the dispatcher by host name: ``download_sources.SourceRegistry``).

The exact host of a configured provider goes to that provider, and the transport it resolves (a file or an
HLS playlist) is fetched by BiliFlow; a lookalike host, a host listed for a provider the code does not have
and any other link keep the yt-dlp path. First the pure checks (host lists, the local config, the registry),
then the same routes through the real download queue. The site provider is the test-only ExampleSiteProvider
(the code ships none), every host is a ``.example`` name served by tests/source_fixtures.FixtureServer, and
yt-dlp is the fake of tests/fake_yt_dlp.py: nothing reaches the network or a real site.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from biliflow import download_sources
from biliflow.download_links import DownloadBatchError, LinkRejected, check_host
from biliflow.download_probe import NOTHING_READ_CODES, UNSUPPORTED_MESSAGE
from biliflow.download_source_steps import with_reader_note
from biliflow.download_sources import (
    LOCAL_CONFIG,
    PROVIDER_ID,
    SHOWN_REASON_CHARS,
    SITE_PROVIDERS,
    DirectMediaProvider,
    HostList,
    SourceRegistry,
    default_registry,
    describe_config_problems,
    read_provider_config,
)
from tests.source_fixtures import HAVE_FFMPEG, NEED_FFMPEG, Reply, make_clip, make_hls, remove_tree, temp_root
from tests.test_download_provider_worker import ProviderMixin
from tests.test_download_sources import MEDIA_URL, PAGE_URL, PLAYER_PATH, ExampleSiteProvider
from tests.test_download_worker import WorkerCase, ok_verifier, page, video

CYRILLIC_I = chr(0x0456)  # looks like the Latin "i"
SHARP_S = chr(0x00DF)  # Python's IDNA codec reads it otherwise than a browser
BACKSLASH = chr(92)
FULLWIDTH_DOT = chr(0xFF0E)  # a label separator for IDNA
MEDIA: dict[str, object] = {}
CONFIG = {"example-site": {"hosts": ["video.example"]}, "films-site": {"hosts": ["films.example"]}}
FILM_PAGE = "http://films.example/watch?v=abc"  # listed for "films-site", which no provider of the code has


def setUpModule():
    if not HAVE_FFMPEG:
        return
    base = temp_root("biliflow-dispatch-media-")
    MEDIA["base"] = base
    MEDIA["clip"] = make_clip(base / "clip.mp4", seconds=3, size="160x120").read_bytes()
    MEDIA["hls"] = make_hls(base / "hls", seconds=4, size="160x120")


def tearDownModule():
    if "base" in MEDIA:
        remove_tree(MEDIA["base"])


def check_reason(name: str) -> str:
    """Why the host check refuses ``name``."""
    try:
        check_host(name)
    except LinkRejected as error:
        return error.message
    raise AssertionError(f"{name!r} was accepted")


def scratch_dir(case: unittest.TestCase) -> Path:
    """A temporary project root for one test, removed after it."""
    path = temp_root("biliflow-dispatch-")
    case.addCleanup(remove_tree, path)
    return path


# ------------------------------------------------------------------ host lists, the config, the registry
class HostListTest(unittest.TestCase):
    def test_matches_the_exact_host_after_lower_case_and_idna_normalisation(self):
        hosts = HostList(["Video.Example.", "Bücher.example"])

        self.assertEqual(hosts.hosts, frozenset({"video.example", "xn--bcher-kva.example"}))
        for link in ["http://video.example/watch?v=1", "https://VIDEO.EXAMPLE/watch", "http://video.example./x",
                     "http://xn--bcher-kva.example/v", "http://bücher.example/v", "https://BÜCHER.example/v"]:
            with self.subTest(link=link):
                self.assertTrue(hosts.matches(link))

    def test_never_matches_a_lookalike_suffix_prefix_or_subdomain(self):
        hosts = HostList(["video.example"])
        for link in ["http://evil-video.example/", "http://video.example.evil.example/", "http://sub.video.example/",
                     "http://videoexample/", "http://video.examples/", "http://evil.example/?u=video.example",
                     "http://evil.example/video.example"]:
            with self.subTest(link=link):
                self.assertFalse(hosts.matches(link))

    def test_skips_invalid_entries_and_unreadable_links(self):
        hosts = HostList(["", "   ", "a..b", "x" * 64 + ".example", "video.example"])

        self.assertEqual(hosts.hosts, frozenset({"video.example"}))
        self.assertFalse(hosts.matches("not a link"))
        self.assertFalse(hosts.matches("http://[::1/watch"))
        self.assertFalse(HostList([]).matches("http://video.example/"))

    def test_entries_must_be_bare_host_names_so_a_wildcard_matches_nothing(self):
        hosts = HostList(["*.video.example", "video.example:443", "https://video.example/", "video.example/watch",
                          "user@video.example", "127.0.0.1", "localhost", "printer.local", "cdn.video.example"])

        self.assertEqual(hosts.hosts, frozenset({"cdn.video.example"}))
        for link in ("http://video.example/watch?v=1", "http://www.video.example/watch?v=1"):
            with self.subTest(link=link):
                self.assertFalse(hosts.matches(link))

    def test_the_host_is_read_by_the_link_check_so_a_disguised_link_matches_nothing(self):
        hosts = HostList(["video.example"])
        # urlsplit alone reads "video.example" in the first three; the link check refuses them.
        for link in ["http://evil.example\\@video.example/watch?v=1", "http://video.example:8080/watch?v=1",
                     "//video.example/watch?v=1", "http://user:secret@video.example/watch?v=1",
                     "http://video.example@evil.example/watch?v=1", "http://video.example%2eevil.example/",
                     "http://evil.example%40video.example/", f"http://v{CYRILLIC_I}deo.example/watch?v=1",
                     "ftp://video.example/clip.mp4", "http://video.example/" + "x" * 2100]:
            with self.subTest(link=link[:80]):
                self.assertFalse(hosts.matches(link))
        # The same host written another way: the link check rewrites the link to it before the download.
        for link in (f"http://video{FULLWIDTH_DOT}example/watch?v=1", "https://video.example:443/watch?v=1"):
            with self.subTest(link=link):
                self.assertTrue(hosts.matches(link))


class ProviderConfigTest(unittest.TestCase):
    def setUp(self):
        self.root = scratch_dir(self)

    def write_config(self, text: str) -> None:
        path = self.root / LOCAL_CONFIG
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_config_lives_in_the_git_ignored_local_file(self):
        self.assertEqual(LOCAL_CONFIG, Path("config") / "download_providers.local.json")

    def test_missing_config_gives_no_providers(self):
        self.assertEqual(read_provider_config(self.root)[0], {})

    def test_malformed_or_misshaped_config_gives_no_providers(self):
        for text in ['{"providers": {"example-site": ', "", "[]", '{"providers": []}', '{"providers": "video.example"}']:
            with self.subTest(text=text):
                self.write_config(text)
                self.assertEqual(read_provider_config(self.root)[0], {})

    def test_valid_config_gives_a_host_list_per_provider(self):
        self.write_config(json.dumps({"providers": {"example-site": {"hosts": ["Video.Example", "cdn.video.example"]},
                                                    "other-site": {"hosts": []}}}))

        result = read_provider_config(self.root)[0]

        self.assertEqual(set(result), {"example-site", "other-site"})
        self.assertIsInstance(result["example-site"], HostList)
        self.assertEqual(result["example-site"].hosts, frozenset({"video.example", "cdn.video.example"}))
        self.assertEqual(result["other-site"].hosts, frozenset())

    def test_entries_without_a_host_list_and_non_text_hosts_are_ignored(self):
        self.write_config(json.dumps({"providers": {"a": {"hosts": "video.example"}, "b": "video.example", "c": {},
                                                    "d": {"hosts": ["video.example", 5, None, ""]}}}))

        result = read_provider_config(self.root)[0]

        self.assertEqual(set(result), {"d"})
        self.assertEqual(result["d"].hosts, frozenset({"video.example"}))

    def test_the_config_never_names_code_to_load_or_a_command_to_run(self):
        self.write_config(json.dumps({"providers": {
            "example-site": {"hosts": ["video.example"], "module": "os", "class": "system", "command": "calc.exe",
                             "factory": "subprocess.Popen", "args": ["/c", "echo"]},
            "Example Site": {"hosts": ["a.example"]}, "../evil": {"hosts": ["b.example"]},
            "-site": {"hosts": ["c.example"]}, "x" * 41: {"hosts": ["d.example"]}}}))
        refuse = AssertionError("the provider config must not load code or run a command")

        # patch.object: patch() with a dotted name would itself look the target up with importlib.import_module.
        with patch.object(subprocess, "Popen", side_effect=refuse), patch.object(os, "system", side_effect=refuse), \
                patch.object(importlib, "import_module", side_effect=refuse):
            result = read_provider_config(self.root)[0]
            registry = default_registry(self.root)

        self.assertEqual(set(result), {"example-site"})  # an id no provider class could carry is ignored
        self.assertEqual(result["example-site"].hosts, frozenset({"video.example"}))
        self.assertEqual(registry.ids, ("direct",))

    def test_the_registry_module_has_no_way_to_load_code_or_run_a_command(self):
        text = Path(download_sources.__file__).read_text(encoding="utf-8")
        for word in ("importlib", "__import__", "exec(", "eval(", "subprocess", "os.system", "shell=True",
                     "sys.modules", "globals("):
            with self.subTest(word=word):
                self.assertNotIn(word, text)

    def test_what_the_config_ignores_is_reported_and_the_rest_still_works(self):
        self.write_config(json.dumps({"providers": {
            "example-site": {"hosts": ["video.example", "https://video.example/", "*.video.example", 5]},
            "Bad Id": {"hosts": ["a.example"]}, "no-hosts": {}, "films-site": {"hosts": ["films.example"]}}}))

        hosts, problems = read_provider_config(self.root)

        self.assertEqual(set(hosts), {"example-site", "films-site"})
        self.assertEqual(hosts["example-site"].hosts, frozenset({"video.example"}))
        self.assertEqual([entry for entry, _why in hosts["example-site"].skipped],
                         ["https://video.example/", "*.video.example"])
        self.assertEqual(len(problems), 3)
        text = " ".join(problems)
        for part in ('"https://video.example/", "*.video.example" (Tên miền trong link có ký tự không hợp lệ); '
                     '5 (không phải chuỗi)', 'id "Bad Id"', '"no-hosts"'):
            self.assertIn(part, text)
        self.assertNotIn("films-site", text)
        self.assertEqual(describe_config_problems(problems), "config/download_providers.local.json: " + text)

    def test_an_unreadable_config_is_reported_but_a_missing_one_is_not(self):
        self.assertEqual(read_provider_config(self.root), ({}, ()))
        for text in ['{"providers": {"example-site": ', "", "[]", "{}", '{"providers": []}', "[" * 100_000]:
            with self.subTest(text=text[:30]):
                self.write_config(text)
                hosts, problems = read_provider_config(self.root)
                self.assertEqual(hosts, {})
                self.assertEqual(len(problems), 1)
                self.assertIn("Không đọc được", problems[0])
        self.write_config(json.dumps({"providers": {"example-site": {"hosts": ["video.example"]}}}))
        self.assertEqual(read_provider_config(self.root)[1], ())
        self.assertIsNone(describe_config_problems(()))

    def test_long_lists_of_problems_and_entries_are_cut_short(self):
        self.write_config(json.dumps({"providers": {"example-site": {"hosts": [f"*.{n}.example" for n in range(8)]}}}))

        [problem] = read_provider_config(self.root)[1]
        text = describe_config_problems([f"P{index}." for index in range(9)])

        self.assertIn('"*.4.example" (Tên miền trong link có ký tự không hợp lệ) và 3 mục khác', problem)
        self.assertNotIn("*.5.example", problem)
        self.assertIn("P4. (và 4 lỗi khác)", text)
        self.assertNotIn("P5.", text)

    def test_a_long_entry_is_cut_short(self):
        self.write_config(json.dumps({"providers": {"example-site": {"hosts": ["x" * 100 + ".example"]}}}))

        [problem] = read_provider_config(self.root)[1]

        self.assertIn('"' + "x" * 56 + "…", problem)
        self.assertNotIn("x" * 57, problem)

    def test_a_skipped_entry_says_why_and_shows_no_more_of_a_link_than_its_host(self):
        self.write_config(json.dumps({"providers": {"example-site": {"hosts": [
            "https://user:pw@video.example/watch?sig=SECRET123", f"fa{SHARP_S}.example", "127.0.0.1",
            {"url": "https://video.example/?token=SECRET456"}, ["video.example"]]}}}))

        [problem] = read_provider_config(self.root)[1]

        self.assertIn('"https://…@video.example/…" (Tên miền trong link có ký tự không hợp lệ)', problem)
        self.assertIn(f'"fa{SHARP_S}.example" (Tên miền có ký tự mà trình duyệt đọc khác BiliFlow', problem)
        self.assertIn("xn--", problem)
        self.assertIn('"127.0.0.1" (Link phải dùng tên miền, không dùng địa chỉ IP)', problem)
        self.assertIn("{…}, […] (không phải chuỗi)", problem)
        for secret in ("SECRET123", "SECRET456", "user", "pw@", "watch"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, problem)

    def test_an_entry_without_a_scheme_is_cut_at_its_host_too(self):
        self.write_config(json.dumps({"providers": {"example-site": {"hosts": [
            "video.example/api?token=SECRET789&next=https://x.example", "video.example/watch?sig=SECRET790",
            "user:SECRET791@video.example", "video.example/"]}}}))

        [problem] = read_provider_config(self.root)[1]

        self.assertIn('"video.example/…", "video.example/…", "…@video.example", "video.example/"', problem)
        self.assertNotIn("SECRET", problem)

    def test_the_reason_is_cut_short_too(self):
        self.write_config(json.dumps({"providers": {"example-site": {"hosts": ["a." * 100 + "local"]}}}))

        [problem] = read_provider_config(self.root)[1]

        self.assertIn("a." * 100, check_reason("a." * 100 + "local"))  # the reason names the whole host
        self.assertNotIn("a." * (SHOWN_REASON_CHARS // 2), problem)
        self.assertIn("…)", problem)

    def test_a_lone_surrogate_in_the_config_still_gives_a_message_the_page_can_send(self):
        escape = BACKSLASH + "ud800"  # a JSON escape for half of a surrogate pair: no character of its own
        self.write_config('{"providers": {"bad' + escape + '": {"hosts": []}, '
                          '"example-site": {"hosts": ["' + escape + '.example"]}}}')

        text = describe_config_problems(read_provider_config(self.root)[1])

        self.assertEqual(text.encode("utf-8").decode("utf-8"), text)  # the page's JSON is UTF-8
        self.assertEqual(text.count(escape), 2)  # shown as the escape it was written as

    def test_a_config_that_cannot_be_looked_at_is_reported_not_raised(self):
        with patch.object(Path, "is_file", side_effect=PermissionError(13, "Access is denied")):
            hosts, problems = read_provider_config(self.root)
            registry = default_registry(self.root)

        self.assertEqual(hosts, {})
        self.assertEqual(len(problems), 1)
        self.assertIn("Không đọc được", problems[0])
        self.assertEqual(registry.ids, ("direct",))


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.root = scratch_dir(self)

    def configure(self, providers: dict[str, Any]) -> None:
        path = self.root / LOCAL_CONFIG
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"providers": providers}), encoding="utf-8")

    def test_configured_site_provider_is_asked_before_direct_links(self):
        self.configure({"example-site": {"hosts": ["video.example"]}})

        registry = default_registry(self.root, site_providers=(ExampleSiteProvider,))

        self.assertEqual(registry.ids, ("example-site", "direct"))
        self.assertEqual(registry.provider_for(PAGE_URL).id, "example-site")
        self.assertEqual(registry.provider_for("http://video.example/files/clip.mp4").id, "example-site")
        self.assertEqual(registry.provider_for(MEDIA_URL).id, "direct")
        self.assertIsNone(registry.provider_for("http://evil-video.example/watch?v=abc"))
        self.assertIsInstance(registry.get("example-site"), ExampleSiteProvider)
        self.assertIsNone(registry.get(None))

    def test_a_site_provider_is_only_asked_about_links_of_its_exact_hosts(self):
        class GreedySiteProvider(ExampleSiteProvider):
            def claims(self, url: str) -> bool:  # a careless provider that would take any link
                return True

        self.configure({"example-site": {"hosts": ["video.example"]}})

        registry = default_registry(self.root, site_providers=(GreedySiteProvider,))

        self.assertEqual(registry.provider_for(PAGE_URL).id, "example-site")
        self.assertEqual(registry.provider_for(MEDIA_URL).id, "direct")
        for link in ("http://other.example/watch?v=abc", "http://video.example.evil.example/watch?v=abc",
                     "http://www.video.example/watch?v=abc", "http://evil.example\\@video.example/watch?v=abc"):
            with self.subTest(link=link):
                self.assertIsNone(registry.provider_for(link))

    def test_site_provider_without_hosts_in_the_local_config_is_not_active(self):
        for providers in (None, {"another-site": {"hosts": ["video.example"]}}, {"example-site": {"hosts": "x"}}):
            with self.subTest(providers=providers):
                if providers is not None:
                    self.configure(providers)
                registry = default_registry(self.root, site_providers=(ExampleSiteProvider,))
                self.assertEqual(registry.ids, ("direct",))
                self.assertIsNone(registry.provider_for(PAGE_URL))

    def test_unknown_config_id_does_not_activate_any_shipped_site_provider(self):
        self.configure({"example-site": {"hosts": ["video.example"]}})

        registry = default_registry(self.root)

        self.assertEqual({provider.id for provider in SITE_PROVIDERS}, {"player-hls", "article-mp4", "embedded-media"})
        self.assertEqual(registry.ids, ("direct",))
        self.assertIsNone(registry.provider_for(PAGE_URL))  # the configured host is recognized, not supported
        self.assertEqual(registry.recognized_without_provider(PAGE_URL), "example-site")

    def test_hosts_listed_for_an_id_without_a_provider_are_recognized_not_supported(self):
        self.configure({"films-site": {"hosts": ["films.example"]}, "example-site": {"hosts": ["video.example"]},
                        "direct": {"hosts": ["media.example"]}})

        registry = default_registry(self.root, site_providers=(ExampleSiteProvider,))

        film_page = "http://films.example/watch?v=abc"
        self.assertEqual(registry.ids, ("example-site", "direct"))
        self.assertIsNone(registry.provider_for(film_page))  # yt-dlp reads it, like any link
        self.assertEqual(registry.recognized_without_provider(film_page), "films-site")
        self.assertEqual(registry.provider_for("http://films.example/v/clip.mp4").id, "direct")
        for link in (PAGE_URL, "http://media.example/watch?v=1", "http://films.example.evil.example/watch?v=abc",
                     "http://evil.example\\@films.example/watch?v=abc", "http://other.example/watch?v=abc"):
            with self.subTest(link=link):
                self.assertIsNone(registry.recognized_without_provider(link))
        self.assertIsNone(SourceRegistry([DirectMediaProvider()]).recognized_without_provider(film_page))

    def test_duplicate_provider_ids_are_refused(self):
        class ShadowDirect(ExampleSiteProvider):
            id = "direct"

        class Twin(ExampleSiteProvider):
            pass

        with self.assertRaises(ValueError):
            SourceRegistry([DirectMediaProvider(), DirectMediaProvider()])
        self.configure({"direct": {"hosts": ["video.example"]}})
        with self.assertRaises(ValueError):
            default_registry(self.root, site_providers=(ShadowDirect,))
        self.configure({"example-site": {"hosts": ["video.example"]}})
        with self.assertRaises(ValueError):
            default_registry(self.root, site_providers=(ExampleSiteProvider, Twin))

    def test_a_provider_class_whose_id_the_config_could_not_name_is_refused(self):
        class UpperCaseId(ExampleSiteProvider):
            id = "Example_Site"

        with self.assertRaises(ValueError):
            default_registry(self.root, site_providers=(UpperCaseId,))
        for factory in SITE_PROVIDERS:
            with self.subTest(provider=factory):
                self.assertTrue(PROVIDER_ID.fullmatch(factory.id))

    def test_the_registry_carries_what_the_config_ignored(self):
        self.configure({"example-site": {"hosts": ["video.example", "video.example:443"]}})

        registry = default_registry(self.root, site_providers=(ExampleSiteProvider,))

        self.assertEqual(len(registry.config_problems), 1)
        self.assertIn('"video.example:443"', registry.config_problems[0])
        self.assertEqual(registry.provider_for(PAGE_URL).id, "example-site")
        self.assertEqual(SourceRegistry([DirectMediaProvider()]).config_problems, ())


class ReaderNoteTest(unittest.TestCase):
    def test_the_note_joins_only_a_reason_where_yt_dlp_found_nothing(self):
        self.assertEqual(NOTHING_READ_CODES, frozenset({"UNSUPPORTED", "NO_ENTRIES", "ONLY_SHORT_ENTRIES"}))
        for code in sorted(NOTHING_READ_CODES):
            with self.subTest(code=code):
                self.assertEqual(with_reader_note(code, "Lý do.", "Ghi chú."), "Lý do. Ghi chú.")
                self.assertEqual(with_reader_note(code, None, "Ghi chú."), "Ghi chú.")
                self.assertEqual(with_reader_note(code, "Lý do.", None), "Lý do.")
        for code in ("UNAVAILABLE", "DRM", "LOGIN_REQUIRED", "NETWORK", "NO_VIDEOS", None):
            with self.subTest(code=code):
                self.assertEqual(with_reader_note(code, "Lý do.", "Ghi chú."), "Lý do.")


# ------------------------------------------------------------------ through the real worker
@unittest.skipUnless(HAVE_FFMPEG, NEED_FFMPEG)
class HostDispatchTests(ProviderMixin, WorkerCase):
    def source_registry(self):
        (self.root / LOCAL_CONFIG).write_text(json.dumps({"providers": CONFIG}), encoding="utf-8")
        return default_registry(self.root, site_providers=(ExampleSiteProvider,))

    def player(self, kind: str, src: str) -> None:
        body = json.dumps({"format": kind, "src": src}).encode("utf-8")
        self.server.route(PLAYER_PATH, Reply(body, content_type="application/json"))

    def yt_dlp_finds_nothing(self) -> None:
        self.scenario(probe={"stderr": "ERROR: Unsupported URL: http://page.example/", "exit": 1})

    def yt_dlp_reads_a_video(self) -> None:
        self.scenario(probe={"json": video("Trang")}, download={"id": "abc"})
        self.worker.verifier = ok_verifier  # the fake yt-dlp writes placeholder bytes, not a video

    def probed_by_yt_dlp(self, task) -> bool:
        return any(task["url"] in call["argv"] for call in self.calls("probe"))

    def assert_completed_by_yt_dlp(self, task) -> None:
        done = self.store.get(task["id"])
        self.assertEqual(done["state"], "COMPLETED", done)
        self.assertIsNone(done["probe"].get("provider"))
        self.assertTrue(self.probed_by_yt_dlp(task))

    # --------------------------------------------------------------- the exact host: its provider
    def test_the_exact_host_goes_to_its_provider_and_the_file_it_resolves_is_fetched_by_biliflow(self):
        self.player("file", "http://cdn.example/v/abc.mp4")
        self.server.route("/v/abc.mp4", Reply(MEDIA["clip"], content_type="video/mp4"))

        task, = self.add("http://VIDEO.example./watch?v=abc")
        self.run_all(60)

        done = self.store.get(task["id"])
        self.assertEqual(done["state"], "COMPLETED", done)
        self.assertEqual((done["probe"]["provider"], done["probe"]["transport"]), ("example-site", "http_file"))
        players = self.server.seen(PLAYER_PATH)
        self.assertEqual(len(players), 2)  # resolved at PROBING, then again for fresh links before the transfer
        for player in players:
            self.assertEqual((player.host, player.query), ("video.example", {"id": ["abc"]}))
        self.assertEqual(Path(done["output_path"]).read_bytes(), MEDIA["clip"])
        self.assertEqual(self.calls(), [])  # yt-dlp never ran

    def test_the_exact_host_whose_player_names_hls_is_joined_by_the_hls_transfer(self):
        self.player("hls", "http://cdn.example/show/index.m3u8")
        self.serve_hls(MEDIA["hls"])

        task, = self.add(PAGE_URL)
        self.run_all(60)

        done = self.store.get(task["id"])
        self.assertEqual(done["state"], "COMPLETED", done)
        self.assertEqual((done["probe"]["provider"], done["probe"]["transport"]), ("example-site", "hls"))
        self.assertEqual(done["fragments_done"], len(MEDIA["hls"].segments))
        self.assertEqual(self.server.count(PLAYER_PATH), 2)
        self.assertEqual(self.calls(), [])

    # --------------------------------------------------------------- lookalikes: never the provider
    def test_lookalike_hosts_never_reach_the_provider_and_keep_the_yt_dlp_path(self):
        self.yt_dlp_finds_nothing()
        links = ["http://video.example.evil.example/watch?v=abc", "http://evilvideo.example/watch?v=abc",
                 "http://www.video.example/watch?v=abc", f"http://v{CYRILLIC_I}deo.example/watch?v=abc"]

        tasks = self.add(*links)
        self.run_all()

        for task in tasks:
            with self.subTest(url=task["url"]):
                done = self.store.get(task["id"])
                self.assertEqual((done["state"], done["error_code"]), ("FAILED", "UNSUPPORTED"))
                self.assertTrue(done["error_message"].startswith(UNSUPPORTED_MESSAGE), done["error_message"])
                self.assertNotIn("bộ đọc nguồn", done["error_message"])  # not a configured host
                self.assertTrue(self.probed_by_yt_dlp(task))
                self.assertNotIn("SOURCE_NOT_IMPLEMENTED", self.events(task["id"]))
        self.assertEqual(self.server.count(PLAYER_PATH), 0)

    def test_a_link_hiding_another_host_behind_an_account_is_refused_before_it_is_queued(self):
        # urlsplit reads "video.example" as the host of the first link; another parser reads "evil.example".
        for link in ("http://evil.example\\@video.example/watch?v=abc", "http://video.example@evil.example/watch?v=abc"):
            with self.subTest(link=link):
                with self.assertRaises(DownloadBatchError) as caught:
                    self.add(link)
                self.assertEqual([error["code"] for error in caught.exception.errors], ["USERINFO"])
        self.assertEqual(self.store.list_tasks(), [])
        self.assertEqual(self.server.count(PLAYER_PATH), 0)

    # --------------------------------------------------------------- recognized is not supported
    def test_a_host_listed_for_a_provider_the_code_lacks_goes_to_yt_dlp_and_the_failure_says_why(self):
        self.yt_dlp_finds_nothing()

        task, = self.add(FILM_PAGE)
        self.run_all()

        done = self.store.get(task["id"])
        self.assertEqual((done["state"], done["error_code"]), ("FAILED", "UNSUPPORTED"))
        self.assertTrue(done["error_message"].startswith(UNSUPPORTED_MESSAGE), done["error_message"])
        self.assertIn('"films-site"', done["error_message"])
        self.assertIn("chưa có bộ đọc nguồn đó", done["error_message"])
        [note] = [event for event in self.store.events(task["id"]) if event["kind"] == "SOURCE_NOT_IMPLEMENTED"]
        self.assertEqual(note["level"], "WARNING")
        self.assertIn("yt-dlp", note["message"])
        self.assertTrue(self.probed_by_yt_dlp(task))
        self.assertEqual(self.server.count(PLAYER_PATH), 0)

    def test_the_note_joins_each_reason_where_yt_dlp_found_nothing_and_no_other(self):
        ads, empty, gone = (f"http://films.example/watch?v={name}" for name in ("ads", "empty", "gone"))
        self.scenario(by_url={
            ads: {"probe": {"json": page(video("Quảng cáo", 30, "ad1"), video("Trailer", 90, "tr1"))}},
            empty: {"probe": {"json": {"_type": "playlist", "title": "Page", "extractor_key": "Generic",
                                       "entries": []}}},
            gone: {"probe": {"exit": 1, "stderr": "ERROR: Video unavailable"}}})

        tasks = self.add(ads, empty, gone)
        self.run_all()

        codes = {}
        for task in tasks:
            done = self.store.get(task["id"])
            codes[task["url"]] = done["error_code"]
            with self.subTest(url=task["url"]):
                self.assertEqual(done["state"], "FAILED")
                self.assertIn("SOURCE_NOT_IMPLEMENTED", self.events(task["id"]))
                noted = '"films-site"' in (done["error_message"] or "")
                self.assertEqual(noted, done["error_code"] in NOTHING_READ_CODES, done["error_message"])
        self.assertEqual(codes, {ads: "ONLY_SHORT_ENTRIES", empty: "NO_ENTRIES", gone: "UNAVAILABLE"})

    def test_a_valid_config_puts_nothing_on_the_downloads_page(self):
        self.assertIsNone(self.worker.last_error)
        self.assertEqual(self.worker.sources.config_problems, ())

    def test_a_host_listed_without_a_provider_still_downloads_when_yt_dlp_reads_it(self):
        self.yt_dlp_reads_a_video()

        task, = self.add(FILM_PAGE)
        self.run_all()

        self.assert_completed_by_yt_dlp(task)
        self.assertIn("SOURCE_NOT_IMPLEMENTED", self.events(task["id"]))
        self.assertEqual(len(self.calls("download")), 1)

    # --------------------------------------------------------------- the fallback
    def test_an_unlisted_host_and_a_page_the_provider_declines_keep_the_yt_dlp_path(self):
        self.yt_dlp_reads_a_video()

        other, declined = self.add("http://other.example/watch?v=1", "http://video.example/about")
        self.run_all()

        for task in (other, declined):
            with self.subTest(url=task["url"]):
                self.assert_completed_by_yt_dlp(task)
                self.assertNotIn("SOURCE_NOT_IMPLEMENTED", self.events(task["id"]))
        self.assertNotIn("SOURCE_DECLINED", self.events(other["id"]))  # no provider claimed it
        self.assertIn("SOURCE_DECLINED", self.events(declined["id"]))  # the provider found no video id in it
        self.assertEqual(self.server.count(PLAYER_PATH), 0)


class ConfigProblemTests(ProviderMixin, WorkerCase):
    """A mistake in the local config shows on the downloads page (the worker's last error) at start."""

    def source_registry(self):
        providers = {"example-site": {"hosts": ["https://video.example/", "video.example"]},
                     "Bad Id": {"hosts": ["a.example"]}}
        (self.root / LOCAL_CONFIG).write_text(json.dumps({"providers": providers}), encoding="utf-8")
        return default_registry(self.root, site_providers=(ExampleSiteProvider,))

    def test_what_the_local_config_ignores_shows_on_the_downloads_page(self):
        error = self.worker.last_error

        self.assertTrue(error.startswith("config/download_providers.local.json: "), error)
        for part in ('"https://video.example/"', 'id "Bad Id"'):
            self.assertIn(part, error)
        self.assertIsNotNone(self.worker.last_error_at)
        self.assertEqual(self.worker.sources.provider_for(PAGE_URL).id, "example-site")  # the valid host works


if __name__ == "__main__":
    unittest.main()
