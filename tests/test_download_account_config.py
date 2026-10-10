"""The source-account config (download_account_config): schema, adapters, exact hosts, https sign-in link,
host conflicts, and messages that never show a link's path or query. Every host is a ``.example`` name and
every root is a temporary folder under the install's temp/."""
from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow import recycle_bin
from biliflow.download_account_config import (
    ACCOUNT_ADAPTERS,
    ACCOUNT_CONFIG,
    describe_account_problems,
    read_account_config,
)
from biliflow.download_sources import LOCAL_CONFIG, read_provider_config

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMP_PARENT = recycle_bin.INSTALL_ROOT / "temp"
EXAMPLE = REPO_ROOT / "config" / "download_accounts.example.json"


def entry(**changes):
    base = {"adapter": "ticket-files", "label": "Nguồn thử", "login_url": "https://portal.example/login",
            "hosts": {"portal": ["portal.example"], "tickets": ["tickets.example"], "files": ["files.example"]}}
    base.update(changes)
    return base


def without(key):
    return {name: value for name, value in entry().items() if name != key}


class AccountConfigTest(unittest.TestCase):
    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        self._temp = TemporaryDirectory(dir=TEMP_PARENT)
        self.root = Path(self._temp.name)
        (self.root / "config").mkdir()

    def tearDown(self):
        self._temp.cleanup()

    def write(self, data, path=ACCOUNT_CONFIG):
        text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        (self.root / path).write_text(text, encoding="utf-8")

    def read(self, sources):
        self.write({"sources": sources})
        return read_account_config(self.root)

    def test_the_example_file_is_valid_and_names_example_hosts_only(self):
        shutil.copyfile(EXAMPLE, self.root / ACCOUNT_CONFIG)
        config = read_account_config(self.root)
        self.assertEqual(config.problems, ())
        self.assertEqual(list(config.sources), ["demo-portal"])
        source = config.sources["demo-portal"]
        self.assertTrue(source.all_hosts)
        self.assertTrue(all(host.endswith(".example") for host in source.all_hosts))
        self.assertTrue(source.login_url.startswith("https://portal.example/"))

    def test_no_file_means_no_source_and_no_problem(self):
        config = read_account_config(self.root)
        self.assertEqual((dict(config.sources), config.problems), ({}, ()))

    def test_a_file_that_is_not_json_or_has_the_wrong_shape_is_reported(self):
        for text in ("{not json", "[]", '{"sources": []}', '{"other": {}}'):
            with self.subTest(text=text):
                self.write(text)
                config = read_account_config(self.root)
                self.assertEqual(dict(config.sources), {})
                self.assertEqual(len(config.problems), 1)

    def test_a_valid_source_is_normalized(self):
        config = self.read({"alpha": entry(label="  Nguồn A  ", hosts={
            "portal": ["Portal.Example", "portal.example"], "files": ["FILES.example."]})})
        self.assertEqual(config.problems, ())
        source = config.sources["alpha"]
        self.assertEqual(source.label, "Nguồn A")
        self.assertEqual(dict(source.hosts), {"portal": ("portal.example",), "tickets": (),
                                              "files": ("files.example",)})
        self.assertEqual(source.adapter, ACCOUNT_ADAPTERS["ticket-files"])
        self.assertNotIn("portal.example", repr(source))  # the hosts and the sign-in link stay out of logs

    def test_a_page_script_is_its_own_role_outside_the_sources_hosts(self):
        config = self.read({"alpha": entry(page_script="https://ads.example/js/gate.js")})
        self.assertEqual(config.problems, ())
        source = config.sources["alpha"]
        self.assertEqual(source.page_script, "https://ads.example/js/gate.js")
        self.assertNotIn("ads.example", source.all_hosts)  # never matched, kept in the vault or fetched as the source
        self.assertNotIn("ads.example", repr(source))
        self.assertIsNone(self.read({"alpha": entry()}).sources["alpha"].page_script)

    def test_the_label_defaults_to_the_id_and_comment_keys_are_allowed(self):
        config = self.read({"_note": "a comment", "alpha": {**without("label"), "_why": "a comment"}})
        self.assertEqual(config.problems, ())
        self.assertEqual(config.sources["alpha"].label, "alpha")

    def test_each_wrong_entry_is_left_out_as_a_whole_with_a_reason(self):
        cases = {
            "id with capitals": ("Alpha", entry()),
            "id of a public provider": ("direct", entry()),
            "another public provider id": ("player-hls", entry()),
            "Windows device name nul": ("nul", entry()),
            "Windows device name com1": ("com1", entry()),
            "Windows device name lpt0": ("lpt0", entry()),
            "not an object": ("alpha", ["x"]),
            "unknown key": ("alpha", entry(command="calc.exe")),
            "a Windows account in the config": ("alpha", entry(account_sid="S-1-5-18")),
            "unknown adapter": ("alpha", entry(adapter="other")),
            "missing adapter": ("alpha", without("adapter")),
            "label too long": ("alpha", entry(label="x" * 61)),
            "label with a control character": ("alpha", entry(label="a‮b")),
            "missing hosts": ("alpha", without("hosts")),
            "unknown host role": ("alpha", entry(hosts={"portal": ["portal.example"], "cdn": ["c.example"]})),
            "empty portal": ("alpha", entry(hosts={"portal": [], "files": ["files.example"]})),
            "wildcard host": ("alpha", entry(hosts={"portal": ["portal.example", "*.files.example"]})),
            "host with a port": ("alpha", entry(hosts={"portal": ["portal.example"], "files": ["files.example:443"]})),
            "IP host": ("alpha", entry(hosts={"portal": ["portal.example"], "files": ["203.0.113.5"]})),
            "URL as host": ("alpha", entry(hosts={"portal": ["portal.example"], "files": ["https://files.example/"]})),
            "local host": ("alpha", entry(hosts={"portal": ["portal.example"], "files": ["nas.local"]})),
            "host not a string": ("alpha", entry(hosts={"portal": ["portal.example", 5]})),
            "file host that is the portal": ("alpha", entry(hosts={"portal": ["portal.example"],
                                                                   "files": ["portal.example"]})),
            "file host that is a ticket host": ("alpha", entry(hosts={"portal": ["portal.example"], "tickets": [
                "t.example"], "files": ["t.example", "files.example"]})),
            "http sign-in link": ("alpha", entry(login_url="http://portal.example/login")),
            "sign-in link on another host": ("alpha", entry(login_url="https://files.example/login")),
            "sign-in link with an account": ("alpha", entry(login_url="https://user:pw@portal.example/login")),
            "sign-in link with a fragment": ("alpha", entry(login_url="https://portal.example/login#t=1")),
            "sign-in link with a port": ("alpha", entry(login_url="https://portal.example:8443/login")),
            "missing sign-in link": ("alpha", without("login_url")),
            "page script on the portal": ("alpha", entry(page_script="https://portal.example/gate.js")),
            "page script on a file host": ("alpha", entry(page_script="https://files.example/gate.js")),
            "http page script": ("alpha", entry(page_script="http://ads.example/gate.js")),
            "page script with a query": ("alpha", entry(page_script="https://ads.example/gate.js?_=1")),
            "page script with a fragment": ("alpha", entry(page_script="https://ads.example/gate.js#x")),
            "page script with an account": ("alpha", entry(page_script="https://u@ads.example/gate.js")),
            "page script with a port": ("alpha", entry(page_script="https://ads.example:8443/gate.js")),
            "page script without a path": ("alpha", entry(page_script="https://ads.example/")),
            "page script on a local host": ("alpha", entry(page_script="https://nas.local/gate.js")),
            "page script not a string": ("alpha", entry(page_script=["https://ads.example/gate.js"])),
        }
        for name, (source_id, value) in cases.items():
            with self.subTest(name):
                config = self.read({source_id: value})
                self.assertEqual(dict(config.sources), {})
                self.assertEqual(len(config.problems), 1)
                self.assertTrue(config.problems[0].startswith("Bỏ qua nguồn"))

    def test_reasons_never_show_the_path_or_query_of_a_sign_in_link(self):
        config = self.read({"alpha": entry(login_url="http://portal.example/login?token=CANARY-VALUE-123")})
        self.assertEqual(len(config.problems), 1)
        text = config.problems[0] + (describe_account_problems(config.problems) or "")
        self.assertNotIn("CANARY-VALUE-123", text)
        self.assertNotIn("/login", text)
        self.assertIn("portal.example", text)

    def test_a_host_of_two_sources_leaves_both_out(self):
        config = self.read({"alpha": entry(), "beta": entry(hosts={"portal": ["portal.example"]}),
                            "gamma": entry(login_url="https://other.example/", hosts={"portal": ["other.example"]})})
        self.assertEqual(list(config.sources), ["gamma"])
        self.assertEqual(len(config.problems), 2)
        self.assertTrue(all("nhiều nguồn tài khoản" in problem for problem in config.problems))

    def test_a_host_of_a_public_provider_leaves_the_source_out_and_keeps_the_provider(self):
        self.write({"providers": {"player-hls": {"hosts": ["files.example"]}}}, path=LOCAL_CONFIG)
        config = self.read({"alpha": entry()})
        self.assertEqual(dict(config.sources), {})
        self.assertIn("download_providers.local.json", config.problems[0])
        providers, problems = read_provider_config(self.root)
        self.assertEqual(problems, ())
        self.assertEqual(providers["player-hls"].hosts, frozenset({"files.example"}))

    def test_describe_account_problems(self):
        self.assertIsNone(describe_account_problems(()))
        line = describe_account_problems([f"p{index}" for index in range(7)])
        self.assertTrue(line.startswith("config/download_accounts.local.json: p0"))
        self.assertIn("và 2 lỗi khác", line)

    def test_the_local_config_is_git_ignored(self):
        lines = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("config/download_accounts.local.json", lines)


if __name__ == "__main__":
    unittest.main()
