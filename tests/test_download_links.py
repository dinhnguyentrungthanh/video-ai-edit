import unittest

from biliflow.download_links import MAX_BATCH_LINKS, DownloadBatchError, validate_batch

PUBLIC_IP = "93.184.215.14"


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


if __name__ == "__main__":
    unittest.main()
