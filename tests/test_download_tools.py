import contextlib
import hashlib
import io
import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.download_tools import (
    DownloadToolsError,
    audit_download_tools,
    binary_path,
    license_terms,
    load_download_tools_policy,
    main,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FAKE_BINARY = b"fake deno binary"


def write_policy(root: Path, **overrides) -> dict:
    policy = {
        "version": 1,
        "allowed_licenses": ["MIT", "Unlicense", "BSD-3-Clause"],
        "python_packages": {
            "sample-downloader": {
                "version": "1.2.3",
                "license_spdx": "Unlicense AND MIT",
                "source_url": "https://example.test/sample-downloader",
            },
        },
        "binaries": {
            "runtime": {
                "path": "tools/runtime/runtime.exe",
                "version": "9.9.9",
                "sha256": hashlib.sha256(FAKE_BINARY).hexdigest(),
                "license_spdx": "MIT",
                "source_url": "https://example.test/runtime",
            },
        },
        "forbidden_python_packages": {"gpl-tagger": "GPL"},
    }
    policy.update(overrides)
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "download_tools.json").write_text(json.dumps(policy), encoding="utf-8")
    return policy


def write_binary(root: Path, content: bytes = FAKE_BINARY) -> Path:
    path = root / "tools" / "runtime" / "runtime.exe"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def installed(**versions):
    return lambda name: versions.get(name)


class LicenseTermsTests(unittest.TestCase):
    def test_splits_and_or_expressions_and_parentheses(self):
        self.assertEqual(license_terms("Unlicense AND MIT AND ISC"), ("Unlicense", "MIT", "ISC"))
        self.assertEqual(license_terms("(MIT OR Apache-2.0)"), ("MIT", "Apache-2.0"))

    def test_empty_expression_has_no_terms(self):
        self.assertEqual(license_terms(""), ())


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_pinned_free_tools_pass(self):
        write_policy(self.root)
        write_binary(self.root)
        report = audit_download_tools(self.root, version_of=installed(**{"sample-downloader": "1.2.3"}))
        self.assertTrue(report["allowed"], report)
        self.assertEqual(report["blocked_count"], 0)

    def test_version_drift_blocks(self):
        write_policy(self.root)
        write_binary(self.root)
        report = audit_download_tools(self.root, version_of=installed(**{"sample-downloader": "1.2.4"}))
        self.assertFalse(report["allowed"])
        package = report["python_packages"][0]
        self.assertFalse(package["allowed"])
        self.assertEqual(package["installed_version"], "1.2.4")
        self.assertTrue(any("1.2.3" in reason for reason in package["reasons"]))

    def test_missing_package_blocks(self):
        write_policy(self.root)
        write_binary(self.root)
        report = audit_download_tools(self.root, version_of=installed())
        self.assertFalse(report["allowed"])
        self.assertIsNone(report["python_packages"][0]["installed_version"])

    def test_license_outside_the_allowed_list_blocks(self):
        policy = write_policy(self.root)
        policy["python_packages"]["sample-downloader"]["license_spdx"] = "MIT AND GPL-3.0-or-later"
        write_policy(self.root, python_packages=policy["python_packages"])
        write_binary(self.root)
        report = audit_download_tools(self.root, version_of=installed(**{"sample-downloader": "1.2.3"}))
        self.assertFalse(report["allowed"])
        self.assertTrue(any("GPL-3.0-or-later" in reason for reason in report["python_packages"][0]["reasons"]))

    def test_missing_source_url_blocks(self):
        policy = write_policy(self.root)
        policy["binaries"]["runtime"]["source_url"] = ""
        write_policy(self.root, binaries=policy["binaries"])
        write_binary(self.root)
        report = audit_download_tools(self.root, version_of=installed(**{"sample-downloader": "1.2.3"}))
        self.assertFalse(report["binaries"][0]["allowed"])

    def test_forbidden_package_present_blocks(self):
        write_policy(self.root)
        write_binary(self.root)
        report = audit_download_tools(
            self.root, version_of=installed(**{"sample-downloader": "1.2.3", "gpl-tagger": "0.1"})
        )
        self.assertFalse(report["allowed"])
        forbidden = report["forbidden_python_packages"][0]
        self.assertEqual(forbidden["name"], "gpl-tagger")
        self.assertTrue(forbidden["installed"])

    def test_missing_binary_blocks(self):
        write_policy(self.root)
        report = audit_download_tools(self.root, version_of=installed(**{"sample-downloader": "1.2.3"}))
        self.assertFalse(report["allowed"])
        self.assertFalse(report["binaries"][0]["present"])

    def test_changed_binary_blocks(self):
        write_policy(self.root)
        write_binary(self.root, b"another build")
        report = audit_download_tools(self.root, version_of=installed(**{"sample-downloader": "1.2.3"}))
        self.assertFalse(report["allowed"])
        self.assertTrue(report["binaries"][0]["present"])
        self.assertNotEqual(report["binaries"][0]["sha256"], report["binaries"][0]["expected_sha256"])

    def test_tools_root_lets_a_worktree_audit_the_installed_binaries(self):
        write_policy(self.root)
        with TemporaryDirectory() as install:
            write_binary(Path(install))
            report = audit_download_tools(
                self.root,
                tools_root=Path(install),
                version_of=installed(**{"sample-downloader": "1.2.3"}),
            )
        self.assertTrue(report["allowed"], report)

    def test_binary_path_stays_inside_tools_root(self):
        policy = write_policy(self.root)
        policy["binaries"]["runtime"]["path"] = "../outside.exe"
        write_policy(self.root, binaries=policy["binaries"])
        with self.assertRaises(DownloadToolsError):
            binary_path(self.root, "runtime")
        with self.assertRaises(DownloadToolsError):
            binary_path(self.root, "unknown")

    def test_binary_path_resolves_under_the_tools_root(self):
        write_policy(self.root)
        self.assertEqual(binary_path(self.root, "runtime"), self.root / "tools" / "runtime" / "runtime.exe")

    def test_missing_policy_raises(self):
        with self.assertRaises(DownloadToolsError):
            load_download_tools_policy(self.root)


class CommandTests(unittest.TestCase):
    def run_main(self, *argv):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = main(list(argv))
        return code, json.loads(stream.getvalue())

    def test_audit_command_exit_code_follows_the_result(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_policy(root, python_packages={}, binaries={}, forbidden_python_packages={})
            code, report = self.run_main("audit", "--project-root", str(root))
            self.assertEqual(code, 0)
            self.assertTrue(report["allowed"])
            write_policy(root)
            code, report = self.run_main("audit", "--project-root", str(root))
            self.assertEqual(code, 1)
            self.assertFalse(report["allowed"])

    def test_audit_command_prints_vietnamese_through_a_cp1252_pipe(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_policy(root)
            raw = io.BytesIO()
            stream = io.TextIOWrapper(raw, encoding="cp1252")
            with contextlib.redirect_stdout(stream):
                code = main(["audit", "--project-root", str(root)])
            stream.flush()
            self.assertEqual(code, 1)
            report = json.loads(raw.getvalue().decode("utf-8"))
            self.assertIn("Chưa cài", " ".join(report["python_packages"][0]["reasons"]))


class RepositoryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_download_tools_policy(REPO_ROOT)

    def test_every_package_pin_matches_the_lock_file(self):
        pins = {}
        for line in (REPO_ROOT / "requirements.lock.txt").read_text(encoding="utf-8").splitlines():
            match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)", line.strip())
            if match:
                pins[re.sub(r"[-_.]+", "-", match.group(1)).lower()] = match.group(2)
        for name, entry in self.policy["python_packages"].items():
            with self.subTest(package=name):
                self.assertEqual(pins.get(re.sub(r"[-_.]+", "-", name).lower()), entry["version"])

    def test_policy_licenses_are_permissive_and_forbidden_packages_are_listed(self):
        allowed = set(self.policy["allowed_licenses"])
        self.assertFalse(any("GPL" in term for term in allowed))
        for section in ("python_packages", "binaries"):
            for name, entry in self.policy[section].items():
                with self.subTest(tool=name):
                    self.assertTrue(set(license_terms(entry["license_spdx"])) <= allowed)
                    self.assertTrue(entry["source_url"].startswith("https://"))
        self.assertIn("mutagen", self.policy["forbidden_python_packages"])
        self.assertIn("curl_cffi", self.policy["forbidden_python_packages"])

    def test_download_modules_and_config_stay_out_of_the_scan_cache_key(self):
        # A change to the downloader must never invalidate cached scans.
        from biliflow.cache_dependencies import stage_source_paths
        from biliflow.stage_cache import CACHEABLE_STAGES

        modules = {path.name for path in (REPO_ROOT / "src" / "biliflow").glob("download_*.py")}
        modules.add("storage_summary.py")
        self.assertIn("download_tools.py", modules)
        for stage in sorted(CACHEABLE_STAGES):
            with self.subTest(stage=stage):
                scoped = {path.name for path in stage_source_paths(REPO_ROOT, stage)}
                self.assertFalse(scoped & modules)
        fingerprint_source = (REPO_ROOT / "src" / "biliflow" / "stage_cache.py").read_text(encoding="utf-8")
        self.assertNotIn("download_tools.json", fingerprint_source)
        self.assertNotIn("download_sources", fingerprint_source)

    def test_deno_lives_under_tools(self):
        self.assertEqual(
            binary_path(REPO_ROOT, "deno"),
            REPO_ROOT / "tools" / "deno" / "deno.exe",
        )


if __name__ == "__main__":
    unittest.main()
