"""Tailscale managed by BiliFlow (docs/TAILSCALE_PLAN.md): finding it, status, the installer, tasks.

Nothing here reaches the network, the real `tailscale` CLI, the Windows service, the firewall or a UAC
prompt: the downloader, the subprocess runner, the elevation and the registry lookup are all fakes.
The real elevated script runs only on its refusal paths (no admin right needed, nothing changes).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from biliflow import tailscale_manager as tm
from biliflow import windows_elevation

ROOT = Path(__file__).resolve().parents[1]
TEMP = ROOT / "temp"
SIGNED = '{"status":"Valid","subject":"CN=Tailscale Inc., O=Tailscale Inc., L=Toronto, S=Ontario, C=CA"}'
MSI = "tailscale-setup-1.102.4-amd64.msi"
MSI_BYTES = b"fake msi" * 1000
MSI_SHA = hashlib.sha256(MSI_BYTES).hexdigest()


def done(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, stdout=out, stderr=err)


def status_json(backend="Running", ip="100.101.102.103", auth=""):
    return json.dumps({
        "BackendState": backend, "Version": "1.102.4", "AuthURL": auth,
        "Self": {"HostName": "my-pc", "TailscaleIPs": [ip, "fd7a:115c:a1e0::1"], "UserID": 7},
        "User": {"7": {"LoginName": "user@example.com"}},
        "CurrentTailnet": {"Name": "user@example.com"},
        "Peer": {"a": {"HostName": "zphone", "OS": "android", "Online": True, "TailscaleIPs": ["100.64.0.9"]},
                 "b": {"HostName": "Laptop", "OS": "windows", "Online": False}},
    })


class FakeSystem:
    """subprocess.run stand-in: sc.exe, PowerShell (signature, firewall) and the tailscale CLI."""

    def __init__(self, service="running", backend="Running", signature=SIGNED, firewall="True"):
        self.service, self.backend, self.signature, self.firewall = service, backend, signature, firewall
        self.calls: list[list[str]] = []
        self.cli_results: dict[str, subprocess.CompletedProcess] = {}

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        assert kwargs.get("timeout"), "every call has a timeout"
        assert not kwargs.get("shell"), "never through a shell"
        exe = Path(command[0]).name.lower()
        if exe == "sc.exe":
            if self.service == "missing":
                return done(1060, "[SC] OpenService FAILED 1060:")
            state = {"running": "4  RUNNING", "stopped": "1  STOPPED"}[self.service]
            return done(0, f"SERVICE_NAME: Tailscale\n        TYPE               : 10  WIN32_OWN_PROCESS\n"
                           f"        STATE              : {state}\n")
        if exe == "powershell.exe":
            script = command[-1]
            if "Get-AuthenticodeSignature" in script:
                assert kwargs["env"]["BILIFLOW_SIGNED_FILE"].endswith(MSI)
                return done(0, self.signature)
            if "Get-NetFirewallRule" in script:
                return done(0, self.firewall)
        if exe == "tailscale.exe":
            args = " ".join(command[1:])
            if args == "status --json":
                return done(0, status_json(self.backend))
            if args == "up":
                self.backend = "Running"
            return self.cli_results.get(args, done(0))
        raise AssertionError(f"unexpected command {command}")


class Base(unittest.TestCase):
    def setUp(self):
        TEMP.mkdir(exist_ok=True)
        self.temp = TemporaryDirectory(dir=TEMP)
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        shutil.copy(ROOT / "scripts" / "tailscale-setup.ps1", self.root / "scripts" / "tailscale-setup.ps1")
        self.system = FakeSystem()
        self.events = []
        self.elevations = []
        self.program_files = self.root / "pf"  # stands in for %ProgramFiles%
        self.cli_path = tm.program_files_dir(str(self.program_files)) / "tailscale.exe"

    def tearDown(self):
        self.temp.cleanup()

    def installed(self):
        self.cli_path.parent.mkdir(parents=True, exist_ok=True)
        self.cli_path.write_bytes(b"")

    @staticmethod
    def elevated_args(params):
        """{"-Action": …, "-MsiName": …, "-Sha256": …, "-ResultId": …} from the elevated command line."""
        tokens = params.rsplit('"', 1)[-1].split()
        return dict(zip(tokens[0::2], tokens[1::2]))

    def write_result(self, params, **result):
        result_id = self.elevated_args(params)["-ResultId"]
        (tm.work_dir(self.root) / f"result-{result_id}.json").write_text(json.dumps(result), encoding="utf-8")

    def elevate_ok(self, exe, params, *, timeout):
        """Stands in for UAC + tailscale-setup.ps1: reads its command line, writes a result."""
        self.commands = getattr(self, "commands", []) + [(exe, params)]
        args = self.elevated_args(params)
        data = {"action": args["-Action"]}
        if "-MsiName" in args:
            data.update(msi_name=args["-MsiName"], sha256=args["-Sha256"])
        self.elevations.append(data)
        if data["action"] == "install":
            self.installed()
        if data["action"] == "start":
            self.system.service = "running"
        self.write_result(params, ok=True, action=data["action"], steps=[], error=None)
        return 0

    def manager(self, **kw):
        options = dict(run=self.system, fetch=self.fetch, elevate=self.elevate_ok, image_path=lambda: None,
                       program_files=str(self.program_files), on_event=lambda *event: self.events.append(event))
        options.update(kw)
        manager = tm.TailscaleManager(self.root, **options)
        self.addCleanup(manager.close)
        return manager

    fetched: list
    index = {"MSIs": {"amd64": MSI, "arm64": "x"}, "MSIsVersion": "1.102.4"}

    def fetch(self, url, *, max_bytes, dest=None, on_progress=None):
        self.fetched = getattr(self, "fetched", []) + [url]
        if url == tm.PKGS_INDEX_URL:
            return json.dumps(self.index).encode()
        if url.endswith(".sha256"):
            return f"{MSI_SHA}\n".encode()
        if url.endswith(".msi"):
            dest.write_bytes(MSI_BYTES)
            if on_progress:
                on_progress(len(MSI_BYTES), len(MSI_BYTES))
            return b""
        raise AssertionError(url)

    def wait_task(self, manager, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            task = manager.status()["task"]
            if task and not task["running"]:
                return task
            time.sleep(0.05)
        self.fail("the task did not finish")


class FindingTests(Base):
    def test_the_service_folder_comes_first_then_program_files_never_the_biliflow_tree_or_path(self):
        service_dir = self.root / "moved"
        service_dir.mkdir()
        image = f'"{service_dir}\\tailscaled.exe"'

        def find():
            return tm.find_cli(image_path=lambda: image, program_files=str(self.program_files))
        planted = self.root / "runtime" / "tailscale" / "tailscale.exe"  # every local account can write here
        planted.parent.mkdir(parents=True)
        planted.write_bytes(b"")
        self.assertIsNone(find())
        self.installed()
        self.assertEqual(find(), str(self.cli_path))
        self.assertEqual(self.cli_path, self.program_files / "Tailscale" / "tailscale.exe")
        (service_dir / "tailscale.exe").write_bytes(b"")
        self.assertEqual(find(), str(service_dir / "tailscale.exe"))
        source = Path(tm.__file__).read_text(encoding="utf-8")
        self.assertNotIn("shutil.which", source)
        self.assertIsNone(tm.find_cli(image_path=lambda: None, program_files=str(self.root / "x")))

    def test_the_panel_shows_where_tailscale_is_or_will_be(self):
        manager = self.manager()
        self.assertEqual(manager.status()["install_dir"], str(self.program_files / "Tailscale"))
        self.installed()
        manager = self.manager()
        self.assertEqual(manager.status()["install_dir"], str(self.cli_path.parent))

    def test_image_paths_quoted_unquoted_and_with_arguments(self):
        for text, folder in (('"C:\\A B\\tailscaled.exe" -port 41641', "C:\\A B"),
                             ("E:\\DungChung\\BiliFlow\\runtime\\tailscale\\tailscaled.exe",
                              "E:\\DungChung\\BiliFlow\\runtime\\tailscale"),
                             (None, None), ("", None)):
            with self.subTest(text=text):
                self.assertEqual(str(tm.image_dir(text)) if tm.image_dir(text) else None, folder)

    def test_service_state(self):
        for state in ("running", "stopped", "missing"):
            self.system.service = state
            self.assertEqual(tm.service_state(self.system), state)
        self.assertEqual(tm.service_state(lambda *a, **k: done(0, "nothing useful")), "unknown")

        def broken(*_a, **_k):
            raise OSError("no sc.exe")
        self.assertEqual(tm.service_state(broken), "unknown")

    def test_status_fields_hide_other_devices_addresses(self):
        value = tm.parse_status(json.loads(status_json(auth="https://login.tailscale.com/a/abc123")))
        self.assertEqual((value["backend"], value["ip"], value["hostname"], value["user"]),
                         ("Running", "100.101.102.103", "my-pc", "user@example.com"))
        self.assertEqual(value["peers"], [{"name": "zphone", "os": "android", "online": True},
                                          {"name": "Laptop", "os": "windows", "online": False}])
        self.assertNotIn("100.64.0.9", json.dumps(value))
        self.assertEqual(value["auth_url"], "https://login.tailscale.com/a/abc123")
        evil = tm.parse_status(json.loads(status_json(auth="https://login.tailscale.com.evil.example/a/x")))
        self.assertIsNone(evil["auth_url"])
        self.assertIsNone(tm.parse_status(json.loads(status_json(ip="192.168.1.5")))["ip"])
        self.assertEqual(tm.parse_status({})["backend"], "Unknown")

    def test_firewall_rule_state(self):
        for out, state in (("True", "enabled"), ("False", "disabled"), ("missing", "missing"), ("?", "unknown")):
            self.system.firewall = out
            self.assertEqual(tm.firewall_rule_state(self.system), state)


class DownloadTests(Base):
    def test_only_https_tailscale_hosts(self):
        for url in ("https://pkgs.tailscale.com/stable/x.msi", "https://dl.tailscale.com/x", "https://tailscale.com/"):
            tm.require_tailscale_url(url)
        for url in ("http://pkgs.tailscale.com/x", "https://pkgs.tailscale.com.evil.example/x",
                    "https://eviltailscale.com/x", "file:///C:/x.msi", "https://example.com/x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                tm.require_tailscale_url(url)
        handler = tm._TailscaleOnlyRedirect()
        with self.assertRaises(ValueError):
            handler.redirect_request(mock.Mock(), None, 302, "Found", {}, "https://example.com/x.msi")

    def fake_opener(self, body: bytes, *, length=None, url="https://pkgs.tailscale.com/stable/x"):
        response = mock.MagicMock()
        stream = io.BytesIO(body)
        response.read.side_effect = lambda size=-1: stream.read(size)
        response.headers = {"Content-Length": str(length if length is not None else len(body))}
        response.geturl.return_value = url
        response.__enter__.return_value = response
        opener = mock.Mock()
        opener.open.return_value = response
        return mock.patch.object(tm.urllib.request, "build_opener", return_value=opener)

    def test_streams_to_dest_and_cleans_up_a_cut_or_oversized_download(self):
        dest = self.root / "x.msi"
        progress = []
        with self.fake_opener(b"a" * 200_000):
            tm.https_get("https://pkgs.tailscale.com/stable/x.msi", max_bytes=300_000, dest=dest,
                         on_progress=lambda d, t: progress.append((d, t)))
        self.assertEqual(dest.read_bytes(), b"a" * 200_000)
        self.assertEqual(progress[-1], (200_000, 200_000))
        with self.fake_opener(b"a" * 10, length=10**9), self.assertRaises(ValueError):
            tm.https_get("https://pkgs.tailscale.com/stable/y.msi", max_bytes=1000, dest=self.root / "y.msi")
        with self.fake_opener(b"a" * 5000, length=0), self.assertRaises(ValueError):
            tm.https_get("https://pkgs.tailscale.com/stable/z.msi", max_bytes=1000, dest=self.root / "z.msi")
        self.assertEqual(sorted(p.name for p in self.root.iterdir() if p.is_file()), ["x.msi"], "no .part left")
        with self.fake_opener(b"{}", url="https://example.com/moved"), self.assertRaises(ValueError):
            tm.https_get("https://pkgs.tailscale.com/stable/?mode=json", max_bytes=1000)

    def test_a_trickling_download_ends_at_the_deadline(self):
        with self.fake_opener(b"a" * 200_000), mock.patch.object(tm, "DOWNLOAD_DEADLINE_SECONDS", -1), \
                self.assertRaises(ValueError) as caught:
            tm.https_get("https://pkgs.tailscale.com/stable/x.msi", max_bytes=300_000, dest=self.root / "x.msi")
        self.assertEqual(str(caught.exception), tm.TOO_SLOW_MESSAGE)
        self.assertEqual([p.name for p in self.root.iterdir() if p.is_file()], [], "no .part left")

    def test_system32_comes_from_windows_not_the_environment(self):
        real = tm._system32("cmd.exe")
        with mock.patch.dict(os.environ, {"SystemRoot": r"D:\elsewhere", "windir": r"D:\elsewhere"}):
            self.assertEqual(tm._system32("cmd.exe"), real)
        self.assertTrue(Path(real).is_file())

    def test_signature_must_be_valid_and_tailscale(self):
        path = self.root / MSI
        tm.require_tailscale_signature(path, self.system)
        for signature in ('{"status":"NotSigned","subject":""}',
                          '{"status":"Valid","subject":"CN=Someone Else, O=Evil Inc."}',
                          '{"status":"Valid","subject":"CN=x, O=Tailscale Inc.Fake"}', "not json"):
            self.system.signature = signature
            with self.subTest(signature=signature), self.assertRaises(ValueError):
                tm.require_tailscale_signature(path, self.system)


class InstallTests(Base):
    def test_install_downloads_checks_then_elevates_once(self):
        manager = self.manager()
        manager.start("install")
        task = self.wait_task(manager)
        self.assertTrue(task["ok"], task)
        self.assertEqual(len(self.elevations), 1)
        self.assertEqual(self.elevations[0], {"action": "install", "msi_name": MSI, "sha256": MSI_SHA})
        # Everything the elevated script acts on is on its command line (no request file to rewrite).
        exe, params = self.commands[0]
        self.assertEqual(exe, tm.POWERSHELL)
        script = tm.setup_script(self.root)
        self.assertRegex(params, "^" + re.escape(f'-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden '
                                                  f'-File "{script}" -Action install -MsiName {MSI} -Sha256 {MSI_SHA} '
                                                  f'-ResultId ') + "[0-9a-f]{16}$")
        self.assertEqual((tm.cache_dir(self.root) / MSI).read_bytes(), MSI_BYTES)
        self.assertEqual(list(tm.work_dir(self.root).iterdir()), [], "the result is removed")
        self.assertTrue(manager.status()["installed"])
        self.assertEqual(self.events[-1][0], "TAILSCALE_TASK")
        self.assertTrue(self.events[-1][2]["ok"])
        # Installed again later: the cached MSI with the right hash is not downloaded again.
        self.fetched = []
        manager.start("install")
        self.assertTrue(self.wait_task(manager)["ok"])
        self.assertEqual([url.rsplit("/", 1)[-1] for url in self.fetched], ["?mode=json", MSI + ".sha256"])

    def test_a_bad_hash_or_signature_never_reaches_the_uac_prompt(self):
        cases = {"hash": dict(sha="0" * 64), "signature": dict(signature='{"status":"HashMismatch","subject":""}')}
        for name, case in cases.items():
            with self.subTest(case=name):
                self.elevations.clear()
                for leftover in tm.cache_dir(self.root).glob("*") if tm.cache_dir(self.root).exists() else []:
                    leftover.unlink()
                self.system.signature = case.get("signature", SIGNED)
                original = self.fetch

                def fetch(url, **kw):
                    if url.endswith(".sha256") and "sha" in case:
                        return case["sha"].encode()
                    return original(url, **kw)
                manager = self.manager(fetch=fetch)
                manager.start("install")
                task = self.wait_task(manager)
                self.assertFalse(task["ok"])
                self.assertEqual(self.elevations, [])
                self.assertIn("SHA-256" if name == "hash" else "chữ ký số", task["error"])

    def test_a_bad_index_or_declined_prompt_ends_with_a_reason(self):
        self.index = {"MSIs": {"amd64": "../evil.msi"}}
        manager = self.manager()
        manager.start("install")
        self.assertEqual(self.wait_task(manager)["error"], tm.INDEX_MESSAGE)
        self.index = {"MSIs": {"amd64": MSI}}

        def decline(*_a, **_k):
            raise windows_elevation.ElevationDeclined()
        manager = self.manager(elevate=decline)
        manager.start("install")
        self.assertEqual(self.wait_task(manager)["error"], tm.DECLINED_MESSAGE)
        self.assertEqual(list(tm.work_dir(self.root).iterdir()), [])

    def test_a_failed_elevated_step_and_a_missing_result(self):
        def failing(exe, params, *, timeout):
            self.write_result(params, ok=False, error="msiexec failed with exit code 1603")
            return 1
        manager = self.manager(elevate=failing)
        manager.start("firewall")
        self.assertIn("1603", self.wait_task(manager)["error"])
        manager = self.manager(elevate=lambda *a, **k: 5)
        manager.start("firewall")
        self.assertEqual(self.wait_task(manager)["error"], tm.NO_RESULT_MESSAGE.format(code=5))

        def ok_but_exit_1(exe, params, *, timeout):
            self.write_result(params, ok=True, error=None)  # e.g. planted by another process
            return 1
        manager = self.manager(elevate=ok_but_exit_1)
        manager.start("firewall")
        self.assertEqual(self.wait_task(manager)["error"], tm.STEP_MESSAGE.format(detail="mã thoát 1"))

    def test_bad_arguments_and_linked_folders_never_reach_the_uac_prompt(self):
        manager = self.manager()
        for action, fields in (("format", {}), ("install", dict(msi_name="..\\evil.msi", sha256=MSI_SHA)),
                               ("install", dict(msi_name=MSI, sha256="0" * 63)),
                               ("install", dict(msi_name=MSI + " -Action firewall", sha256=MSI_SHA))):
            with self.subTest(action=action, fields=fields), self.assertRaises(ValueError):
                manager._elevated(action, **fields)
        target = self.root / "elsewhere"
        target.mkdir()
        link = tm.work_dir(self.root)
        link.parent.mkdir(parents=True, exist_ok=True)
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        if made.returncode:
            self.skipTest("cannot create a junction here")
        try:
            with self.assertRaises(ValueError) as caught:
                manager._elevated("firewall")
            self.assertEqual(str(caught.exception), tm.WORK_DIR_MESSAGE)
            self.assertEqual(self.elevations, [])
            self.assertEqual(list(target.iterdir()), [])
        finally:
            os.rmdir(link)  # removes the junction only, before the temporary folder is cleaned

    def test_one_task_at_a_time_and_unknown_actions(self):
        release = threading.Event()

        def slow(*args, **kw):
            release.wait(5)
            return self.elevate_ok(*args, **kw)
        manager = self.manager(elevate=slow)
        manager.start("firewall")
        with self.assertRaises(ValueError) as caught:
            manager.start("install")
        self.assertEqual(str(caught.exception), tm.BUSY_MESSAGE)
        release.set()
        self.assertTrue(self.wait_task(manager)["ok"])
        for action in ("format-c", "", None, "INSTALL"):
            with self.subTest(action=action), self.assertRaises(ValueError):
                manager.start(action)


class FakeLogin:
    """`tailscale login`: prints the link, then exits once the test says the user signed in."""

    def __init__(self, system, signed_in):
        self.system, self.signed_in = system, signed_in
        self.stdout = iter(["To authenticate, visit:\n", "\n", "\thttps://login.tailscale.com/a/abc123\n"])
        self.terminated = False

    def poll(self):
        if self.signed_in.is_set():
            self.system.backend = "Running"
            return 0
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True


class TaskTests(Base):
    def test_login_shows_the_link_then_finishes_when_signed_in(self):
        self.installed()
        self.system.backend = "NeedsLogin"
        signed_in = threading.Event()
        launched = []

        def popen(command, **kw):
            launched.append(command)
            return FakeLogin(self.system, signed_in)
        manager = self.manager(popen=popen)
        manager.start("login")
        deadline = time.monotonic() + 5
        while manager.status()["auth_url"] is None and time.monotonic() < deadline:
            time.sleep(0.05)
        status = manager.status()
        self.assertEqual(status["auth_url"], "https://login.tailscale.com/a/abc123")
        self.assertEqual(status["task"]["stage"], "waiting_login")
        signed_in.set()
        task = self.wait_task(manager)
        self.assertTrue(task["ok"], task)
        self.assertIn("user@example.com", task["message"])
        self.assertEqual(launched, [[str(self.cli_path), "login"]])
        self.assertIsNone(manager.status()["auth_url"])

    def test_up_down_logout_and_not_signed_in(self):
        self.installed()
        self.system.backend = "NeedsLogin"
        manager = self.manager()
        manager.start("up")
        self.assertEqual(self.wait_task(manager)["error"], tm.NEEDS_LOGIN_MESSAGE)
        self.system.backend = "Stopped"
        manager.start("up")
        self.assertTrue(self.wait_task(manager)["ok"])
        self.assertIn([str(self.cli_path), "up"], self.system.calls)
        for action, args in (("down", "down"), ("logout", "logout")):
            manager.start(action)
            self.assertTrue(self.wait_task(manager)["ok"])
            self.assertIn([str(self.cli_path), args], self.system.calls)
        self.system.cli_results["down"] = done(1, err="access denied")
        manager.start("down")
        self.assertIn("access denied", self.wait_task(manager)["error"])

    def test_not_installed(self):
        manager = self.manager()
        status = manager.status()
        self.assertEqual((status["installed"], status["service"]), (False, "missing"))
        for action in ("login", "up", "down", "remote-on", "start-service"):
            manager.start(action)
            self.assertEqual(self.wait_task(manager)["error"], tm.NOT_INSTALLED_MESSAGE)

    def test_remote_on_starts_the_service_connects_then_opens_the_phone_mode(self):
        self.installed()
        self.system.service, self.system.backend = "stopped", "Stopped"
        opened = []
        manager = self.manager(phone_enable=lambda: opened.append(1) or {"network": "tailscale",
                                                                           "url": "http://100.101.102.103:8767/"})
        manager.start("remote-on")
        task = self.wait_task(manager)
        self.assertTrue(task["ok"], task)
        self.assertEqual([e["action"] for e in self.elevations], ["start"])
        self.assertEqual(opened, [1])
        self.assertIn("100.101.102.103:8767", task["message"])
        manager = self.manager(phone_enable=lambda: {"network": "wifi", "url": "http://192.168.1.5:8767/"})
        manager.start("remote-on")
        self.assertEqual(self.wait_task(manager)["error"], tm.PHONE_WIFI_MESSAGE)

    def test_status_reads_the_firewall_rule_at_most_every_30_seconds(self):
        self.installed()
        manager = self.manager()
        for _ in range(3):
            self.assertEqual(manager.status()["firewall_rule"], "enabled")
        firewall_calls = [c for c in self.system.calls if "Get-NetFirewallRule" in c[-1]]
        self.assertEqual(len(firewall_calls), 1)
        self.assertEqual(manager.status()["ip"], "100.101.102.103")

    def test_status_reads_tailscale_at_most_every_2_seconds_and_again_after_a_task(self):
        self.installed()
        manager = self.manager()
        reads = lambda: len([c for c in self.system.calls if Path(c[0]).name == "sc.exe"])  # noqa: E731
        for _ in range(5):
            manager.status()
        self.assertEqual(reads(), 1, "a page (or a hostile <img>) asking again and again reads once")
        self.system.backend = "Stopped"
        self.assertEqual(manager.status()["backend"], "Running", "cached")
        manager.start("down")
        self.wait_task(manager)
        self.assertEqual(manager.status()["backend"], "Stopped", "a task end reads Tailscale again")
        with mock.patch.object(tm, "STATUS_CACHE_SECONDS", 0):
            before = reads()
            manager.status()
            self.assertEqual(reads(), before + 1)

    def test_a_failed_sign_in_never_puts_the_link_in_its_error(self):
        self.installed()
        self.system.backend = "NeedsLogin"
        exited = threading.Event()

        class FailingLogin(FakeLogin):
            def poll(self):
                return 1 if exited.is_set() or self.terminated else None
        manager = self.manager(popen=lambda command, **kw: FailingLogin(self.system, threading.Event()))
        manager.start("login")
        deadline = time.monotonic() + 5
        while manager.status()["auth_url"] is None and time.monotonic() < deadline:
            time.sleep(0.05)
        exited.set()
        task = self.wait_task(manager)
        self.assertFalse(task["ok"])
        self.assertIn("<link>", task["error"])
        self.assertNotIn("abc123", task["error"])
        self.assertNotIn("abc123", json.dumps(self.events, ensure_ascii=False))


@unittest.skipUnless(sys.platform == "win32", "Windows PowerShell")
class ElevatedScriptRefusals(Base):
    """The real tailscale-setup.ps1, run WITHOUT admin rights, only on paths that refuse before any change."""

    def run_script(self, *args: str) -> int:
        script = self.root / "scripts" / "tailscale-setup.ps1"
        return subprocess.run([tm.POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                               "-File", str(script), *args], capture_output=True, timeout=60,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode

    def result(self, result_id: str):
        return json.loads((tm.work_dir(self.root) / f"result-{result_id}.json").read_text(encoding="utf-8-sig"))

    def junction(self, link: Path, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        link.parent.mkdir(parents=True, exist_ok=True)
        if subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True).returncode:
            self.skipTest("cannot create a junction here")

    def test_bad_arguments_and_a_linked_work_folder_leave_no_result(self):
        work = tm.work_dir(self.root)
        work.mkdir(parents=True)
        for args in (["-Action", "format", "-ResultId", "0123456789abcdef"],
                     ["-Action", "firewall", "-ResultId", "x"],
                     ["-Action", "firewall", "-ResultId", "..\\..\\0123456789abcdef"],
                     ["-Action", "firewall"]):
            with self.subTest(args=args):
                self.assertNotEqual(self.run_script(*args), 0)
                self.assertEqual(list(work.iterdir()), [])
        work.rmdir()
        elsewhere = self.root / "elsewhere"
        self.junction(work, elsewhere)
        try:
            self.assertEqual(self.run_script("-Action", "firewall", "-ResultId", "0123456789abcdef"), 2)
            self.assertEqual(list(elsewhere.iterdir()), [], "nothing written through the junction")
        finally:
            os.rmdir(work)

    def test_bad_installers_are_refused_before_msiexec(self):
        tm.work_dir(self.root).mkdir(parents=True)
        cache = tm.cache_dir(self.root)
        cache.mkdir(parents=True)
        (cache / MSI).write_bytes(MSI_BYTES)
        cases = [
            ("other.msi", MSI_SHA, "Not a Tailscale MSI name"),
            ("..\\" + MSI, MSI_SHA, "Not a Tailscale MSI name"),
            (MSI, "xyz", "No expected SHA-256"),
            ("tailscale-setup-9.9.9-amd64.msi", MSI_SHA, "missing"),
            (MSI, "0" * 64, "does not match"),
            (MSI, MSI_SHA, "not validly signed"),
        ]
        for number, (name, sha, expected) in enumerate(cases):
            result_id = f"{number:016x}"
            with self.subTest(expected=expected, name=name):
                self.assertEqual(self.run_script("-Action", "install", "-MsiName", name, "-Sha256", sha,
                                                 "-ResultId", result_id), 1)
                result = self.result(result_id)
                self.assertFalse(result["ok"])
                self.assertIn(expected, result["error"])
        self.assertFalse((self.root / "logs" / "tailscale").exists(), "msiexec was never reached")
        self.assertEqual((cache / MSI).read_bytes(), MSI_BYTES)
        # A result is never written over an existing file (exit 3, the planted file stays as it was).
        (tm.work_dir(self.root) / "result-ffffffffffffffff.json").write_text("planted", encoding="utf-8")
        self.assertEqual(self.run_script("-Action", "install", "-MsiName", "other.msi", "-Sha256", MSI_SHA,
                                         "-ResultId", "ffffffffffffffff"), 3)
        self.assertEqual((tm.work_dir(self.root) / "result-ffffffffffffffff.json").read_text(encoding="utf-8"),
                         "planted")

    def test_a_linked_cache_folder_is_refused(self):
        tm.work_dir(self.root).mkdir(parents=True)
        elsewhere = self.root / "elsewhere"
        self.junction(tm.cache_dir(self.root), elsewhere)
        (elsewhere / MSI).write_bytes(MSI_BYTES)
        try:
            self.assertEqual(self.run_script("-Action", "install", "-MsiName", MSI, "-Sha256", MSI_SHA,
                                             "-ResultId", "0123456789abcdef"), 1)
            self.assertIn("Refusing a link", self.result("0123456789abcdef")["error"])
        finally:
            os.rmdir(tm.cache_dir(self.root))


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class ElevationTests(unittest.TestCase):
    def test_the_structure_matches_windows_and_an_exit_code_comes_back(self):
        import ctypes
        self.assertEqual(ctypes.sizeof(windows_elevation.SHELLEXECUTEINFOW), 112 if sys.maxsize > 2**32 else 60)
        cmd = str(Path(tm._system32("cmd.exe")))
        # The "open" verb: the same wait/exit-code path without a UAC prompt.
        self.assertEqual(windows_elevation.run_elevated(cmd, "/c exit 7", verb="open", timeout=30), 7)


if __name__ == "__main__":
    unittest.main()
