import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.download_sources import (
    MAX_BATCH_LINKS,
    DownloadBatchError,
    DownloadSourceError,
    host_matches,
    load_sources,
    validate_batch,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_IP = "93.184.215.14"


def public_resolver(host, port):
    return [PUBLIC_IP]


def write_sources(root: Path, sources, *, local=None):
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "download_sources.json").write_text(
        json.dumps({"version": 1, "sources": sources}), encoding="utf-8",
    )
    if local is not None:
        (root / "config" / "download_sources.local.json").write_text(
            local if isinstance(local, str) else json.dumps({"version": 1, "sources": local}),
            encoding="utf-8",
        )


def source(id_="clips", domains=("clips.example",), **extra):
    return {"id": id_, "label": id_.title(), "domains": list(domains),
            "min_duration_seconds": 0, "allow_multi_entry": False, **extra}


class CatalogTests(unittest.TestCase):
    def test_repository_catalog_lists_only_youtube_and_bilibili(self):
        catalog = load_sources(REPO_ROOT)
        ids = [item.id for item in catalog.sources if not item.local]
        self.assertEqual(ids, ["youtube", "bilibili"])
        domains = {domain for item in catalog.sources if not item.local for domain in item.domains}
        self.assertEqual(domains, {"youtube.com", "youtu.be", "bilibili.com", "b23.tv"})

    def test_the_local_example_uses_reserved_example_domains_only(self):
        payload = json.loads(
            (REPO_ROOT / "config" / "download_sources.local.example.json").read_text(encoding="utf-8")
        )
        for item in payload["sources"]:
            for domain in item["domains"]:
                self.assertTrue(domain.endswith(".example"), domain)

    def test_local_sources_are_added_and_marked(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_sources(root, [source()], local=[source("movies", ("movies.example",),
                                                          min_duration_seconds=600,
                                                          allow_multi_entry=True)])
            catalog = load_sources(root)
        self.assertEqual([item.id for item in catalog.sources], ["clips", "movies"])
        movies = catalog.get("movies")
        self.assertTrue(movies.local)
        self.assertEqual(movies.min_duration_seconds, 600)
        self.assertTrue(movies.allow_multi_entry)
        self.assertEqual(catalog.warnings, ())

    def test_a_broken_local_file_is_skipped_with_a_warning(self):
        for local in ("{not json", [source("clips")], [source("bad id!")],
                      [source("movies", ("http://movies.example/path",))]):
            with self.subTest(local=local), TemporaryDirectory() as directory:
                root = Path(directory)
                write_sources(root, [source()], local=local)
                catalog = load_sources(root)
                self.assertEqual([item.id for item in catalog.sources], ["clips"])
                self.assertEqual(len(catalog.warnings), 1)

    def test_a_broken_repository_file_raises(self):
        for sources in ([source(domains=())], [source(domains=("127.0.0.1",))],
                        [source(min_duration_seconds=-1)], [source(), source()]):
            with self.subTest(sources=sources), TemporaryDirectory() as directory:
                root = Path(directory)
                write_sources(root, sources)
                with self.assertRaises(DownloadSourceError):
                    load_sources(root)

    def test_domains_are_normalized(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_sources(root, [source(domains=("Clips.Example.", "bücher.example"))])
            catalog = load_sources(root)
        self.assertEqual(catalog.get("clips").domains, ("clips.example", "xn--bcher-kva.example"))


class HostMatchTests(unittest.TestCase):
    def test_exact_host_and_subdomains_match_but_lookalikes_do_not(self):
        domains = ("youtube.com", "youtu.be")
        self.assertTrue(host_matches("youtube.com", domains))
        self.assertTrue(host_matches("m.youtube.com", domains))
        self.assertTrue(host_matches("youtu.be", domains))
        self.assertFalse(host_matches("notyoutube.com", domains))
        self.assertFalse(host_matches("youtube.com.evil.example", domains))
        self.assertFalse(host_matches("com", domains))


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        root = Path(self.directory.name)
        write_sources(root, [source("clips", ("clips.example", "c.example"))])
        self.source = load_sources(root).get("clips")

    def tearDown(self):
        self.directory.cleanup()

    def validate(self, urls, resolver=public_resolver):
        return validate_batch(self.source, urls, resolver=resolver)

    def assert_rejected(self, urls, code, resolver=public_resolver):
        with self.assertRaises(DownloadBatchError) as caught:
            self.validate(urls, resolver)
        self.assertIn(code, [error["code"] for error in caught.exception.errors])
        return caught.exception

    def test_valid_links_are_normalized(self):
        urls = self.validate([
            "  https://CLIPS.example/watch?v=abc#t=10  ",
            "",
            "http://www.c.example:80/v/1",
            "https://clips.example",
        ])
        self.assertEqual(urls, [
            "https://clips.example/watch?v=abc",
            "http://www.c.example/v/1",
            "https://clips.example/",
        ])

    def test_one_bad_link_rejects_the_whole_batch(self):
        error = self.assert_rejected(
            ["https://clips.example/a", "https://other.example/b"], "HOST_NOT_ALLOWED",
        )
        self.assertEqual([item["line"] for item in error.errors], [2])

    def test_scheme_userinfo_port_and_ip_literals_are_rejected(self):
        cases = {
            "ftp://clips.example/a": "BAD_SCHEME",
            "javascript:alert(1)": "BAD_SCHEME",
            "https://user:pw@clips.example/a": "USERINFO",
            "https://user@clips.example/a": "USERINFO",
            "https://clips.example:8443/a": "BAD_PORT",
            "https://clips.example:99999/a": "BAD_PORT",
            "https://127.0.0.1/a": "IP_LITERAL",
            "https://[::1]/a": "IP_LITERAL",
            "https:///a": "NO_HOST",
            "https://clips.example/a b": "BAD_CHARACTERS",
            "https://clips.example/" + "a" * 2100: "URL_TOO_LONG",
            "--exec=calc": "BAD_SCHEME",
        }
        for url, code in cases.items():
            with self.subTest(url=url[:60]):
                self.assert_rejected([url], code)

    def test_hosts_another_parser_would_read_differently_are_rejected(self):
        # urlsplit keeps "\" and "%2f" inside the host; urllib3 and requests would end the host there.
        for url in ("https://evil.example\\.clips.example/x.mp4", "https://evil.example%2f.clips.example/",
                    "http://192.168.1.1\\.clips.example/", "https://a%40b.clips.example/"):
            with self.subTest(url=url):
                self.assert_rejected([url], "NO_HOST")

    def test_malformed_links_get_their_own_line_error(self):
        error = self.assert_rejected(
            ["https://clips.example/a", "https://[::1/x", "https://a／b.clips.example/"], "BAD_URL",
        )
        self.assertEqual([(item["line"], item["code"]) for item in error.errors], [(2, "BAD_URL"), (3, "BAD_URL")])

    def test_hosts_resolving_to_internal_addresses_are_rejected(self):
        for address in ("127.0.0.1", "10.1.2.3", "192.168.1.5", "169.254.10.1", "100.64.0.1",
                        "224.0.0.251", "0.0.0.0", "::1", "fe80::1%12", "fd00::1", "::ffff:10.0.0.1"):
            with self.subTest(address=address):
                self.assert_rejected(["https://clips.example/a"], "PRIVATE_ADDRESS",
                                     resolver=lambda host, port, address=address: [PUBLIC_IP, address])

    def test_unresolvable_hosts_are_rejected(self):
        def failing(host, port):
            raise OSError("no such host")
        self.assert_rejected(["https://clips.example/a"], "DNS_FAILED", resolver=failing)
        self.assert_rejected(["https://clips.example/a"], "DNS_FAILED", resolver=lambda host, port: [])

    def test_duplicates_inside_a_batch_are_rejected(self):
        error = self.assert_rejected(
            ["https://clips.example/a#x", "https://CLIPS.example/a"], "DUPLICATE_IN_BATCH",
        )
        self.assertEqual(error.errors[0]["line"], 2)

    def test_empty_and_oversized_batches_are_rejected(self):
        self.assert_rejected(["", "   "], "EMPTY_BATCH")
        self.assert_rejected([f"https://clips.example/{index}" for index in range(MAX_BATCH_LINKS + 1)],
                             "TOO_MANY_LINKS")

    def test_dns_is_asked_once_per_host(self):
        calls = []

        def counting(host, port):
            calls.append(host)
            return [PUBLIC_IP]
        self.validate(["https://clips.example/a", "https://clips.example/b"], counting)
        self.assertEqual(calls, ["clips.example"])


if __name__ == "__main__":
    unittest.main()
