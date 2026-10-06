import random
import unittest
from urllib.parse import urlsplit

from biliflow.download_links import (
    MAX_BATCH_LINKS,
    DownloadBatchError,
    LinkRejected,
    check_host,
    check_link,
    validate_batch,
)

PUBLIC_IP = "93.184.215.14"
BACKSLASH = chr(92)
FULLWIDTH_DOT, IDEOGRAPHIC_DOT = chr(0xFF0E), chr(0x3002)  # label separators for IDNA
FULLWIDTH_127 = "".join(map(chr, (0xFF11, 0xFF12, 0xFF17)))
SHARP_S, CAPITAL_SHARP_S, FINAL_SIGMA = chr(0x00DF), chr(0x1E9E), chr(0x03C2)
ZERO_WIDTH_NON_JOINER, ZERO_WIDTH_JOINER = chr(0x200C), chr(0x200D)
MONGOLIAN_TODO_SOFT_HYPHEN, MONGOLIAN_FVS4 = chr(0x1806), chr(0x180F)  # Python drops the first, keeps the second
VARIATION_SELECTOR_17 = chr(0xE0100)  # added in Unicode 4.0: a browser drops it, Python's codec keeps it
CHEROKEE_A, CHEROKEE_SMALL_A = chr(0x13A0), chr(0xAB70)  # a browser folds to the capital, Python to the small
CYRILLIC_ROUNDED_VE = chr(0x1C80)  # a browser folds it to "в"; Python's codec does not know it


def public_resolver(host, port):
    return [PUBLIC_IP]


class BatchTests(unittest.TestCase):
    def validate(self, urls, resolver=public_resolver):
        return validate_batch(urls, resolver=resolver)

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

    def test_any_public_site_is_accepted_the_probe_decides_later(self):
        urls = self.validate(["https://clips.example/a", "https://phim.example/phim/tap-1",
                              "https://Bücher.example/v"])
        self.assertEqual(urls, ["https://clips.example/a", "https://phim.example/phim/tap-1",
                                "https://xn--bcher-kva.example/v"])

    def test_one_bad_link_rejects_the_whole_batch(self):
        error = self.assert_rejected(
            ["https://clips.example/a", "https://user@other.example/b"], "USERINFO",
        )
        self.assertEqual([item["line"] for item in error.errors], [2])

    def test_addresses_that_only_appear_after_idna_are_ip_literals(self):
        for url in ("http://１.１.１.１/v", "http://1.2.3.4./v", "http://127。0。0。1/v"):
            with self.subTest(url=url):
                self.assert_rejected([url], "IP_LITERAL")

    def test_names_that_only_exist_inside_a_network_are_rejected_before_dns(self):
        asked = []
        for url in ("https://a.localhost/v", "https://nas.local/v", "https://x.internal/v", "https://pc.lan/v",
                    "https://r.home.arpa/v", "https://h.localdomain/v", "https://NAS.Local./v"):
            with self.subTest(url=url):
                self.assert_rejected([url], "LOCAL_HOST", resolver=lambda host, port: asked.append(host) or [PUBLIC_IP])
        self.assertEqual(asked, [])
        self.assertEqual(self.validate(["https://local.example/v"]), ["https://local.example/v"])

    def test_a_host_without_a_dot_is_rejected_before_dns(self):
        asked = []
        for url in ("https://localhost/a", "http://router/admin", "https://intranet./v"):
            with self.subTest(url=url):
                self.assert_rejected([url], "NO_HOST", resolver=lambda host, port: asked.append(host) or [PUBLIC_IP])
        self.assertEqual(asked, [])

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


class CheckHostTests(unittest.TestCase):
    """A provider's host list is read by the same rules as the host of a pasted link."""

    def assert_host_refused(self, name, code):
        with self.assertRaises(LinkRejected) as caught:
            check_host(name)
        self.assertEqual(caught.exception.code, code)
        with self.assertRaises(DownloadBatchError) as batch:
            validate_batch([f"https://{name}/watch"], resolver=public_resolver)
        self.assertEqual([error["code"] for error in batch.exception.errors], [code])

    def test_a_bare_host_name_is_normalized_like_the_host_of_a_link(self):
        for name, expected in (("Video.Example.", "video.example"), ("Bücher.example", "xn--bcher-kva.example"),
                               (f"video{FULLWIDTH_DOT}example", "video.example"),
                               ("Tiếng-Việt.example", "xn--ting-vit-e50d7c.example"),
                               ("中文.example", "xn--fiq228c.example"),
                               ("cdn.video.example", "cdn.video.example")):
            with self.subTest(name=name):
                self.assertEqual(check_host(name), expected)
                self.assertEqual(validate_batch([f"https://{name}/watch"], resolver=public_resolver),
                                 [f"https://{expected}/watch"])

    def test_anything_but_a_bare_public_host_name_is_refused(self):
        cases = {"": "NO_HOST", "*.video.example": "NO_HOST", "video.example:443": "NO_HOST",
                 "https://video.example/": "NO_HOST", "video.example/watch": "NO_HOST",
                 "user@video.example": "NO_HOST", f"evil.example{BACKSLASH}video.example": "NO_HOST",
                 "video%2eexample": "NO_HOST", "a..b": "NO_HOST", "[::1]": "NO_HOST", "localhost": "NO_HOST",
                 "127.0.0.1": "IP_LITERAL", f"{FULLWIDTH_127}.0.0.1": "IP_LITERAL", "printer.local": "LOCAL_HOST"}
        for name, code in cases.items():
            with self.subTest(name=name), self.assertRaises(LinkRejected) as caught:
                check_host(name)
            self.assertEqual(caught.exception.code, code)

    def test_characters_a_browser_reads_otherwise_than_idna_2003_are_refused(self):
        # Python's codec would turn "fa<sharp s>.example" into fass.example; a browser opens xn--fa-hia.example.
        for character in (SHARP_S, CAPITAL_SHARP_S, FINAL_SIGMA, ZERO_WIDTH_JOINER, ZERO_WIDTH_NON_JOINER,
                          MONGOLIAN_TODO_SOFT_HYPHEN, MONGOLIAN_FVS4, VARIATION_SELECTOR_17, CHEROKEE_A,
                          CHEROKEE_SMALL_A, CYRILLIC_ROUNDED_VE):
            with self.subTest(character=f"U+{ord(character):04X}"):
                self.assert_host_refused(f"fa{character}.example", "NO_HOST")

    def test_a_name_refused_for_such_a_character_can_still_be_given_in_its_xn_form(self):
        with self.assertRaises(LinkRejected) as caught:
            check_host(f"fa{SHARP_S}.example")

        self.assertIn("xn--", caught.exception.message)
        self.assertEqual(check_host("xn--fa-hia.example"), "xn--fa-hia.example")  # what a browser opens

    def test_an_address_in_a_shorthand_the_resolver_reads_is_an_ip_literal(self):
        for name in ("127.1", "0x7f.1", "1.0x7f", "8.8.2056", "2130706433", "10.1.1.1."):
            with self.subTest(name=name):
                self.assert_host_refused(name, "IP_LITERAL")
        self.assertEqual(check_host("video2.example"), "video2.example")
        self.assertEqual(check_host("2056.example"), "2056.example")

    def test_the_host_of_an_accepted_link_is_what_any_reading_of_that_link_gives(self):
        """Seeded fuzz: for every generated link the check accepts, urlsplit reads the same host from the
        normalized link, the check gives the same result again, and that host is a bare ASCII name."""
        rng = random.Random(20261006)
        pieces = ["video", "example", ".", "..", "@", BACKSLASH, "%2e", "%40", ":", ":443", ":80", "/", "?", "#", "[",
                  "]", "x--", "0x", "1", SHARP_S, FULLWIDTH_DOT, IDEOGRAPHIC_DOT, "-", "_", "A", chr(9), " ", "%"]
        accepted = 0
        for _ in range(4000):
            authority = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 6)))
            tail = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 4)))
            raw = f"{rng.choice(['http', 'https', 'HTTPS'])}://{authority}.example/{tail}"
            try:
                url, host, port = check_link(raw)
            except LinkRejected:
                continue
            accepted += 1
            with self.subTest(raw=raw):
                self.assertEqual(urlsplit(url).hostname, host)
                self.assertEqual(check_link(url), (url, host, port))
                self.assertTrue(host.isascii())
                self.assertEqual(check_host(host), host)
        self.assertGreater(accepted, 100)  # the generator does reach links the check accepts


if __name__ == "__main__":
    unittest.main()
