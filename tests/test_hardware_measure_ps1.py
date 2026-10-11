"""scripts/hardware-measure.ps1, run without admin rights and only on paths that refuse (exit 64).

docs/HARDWARE_PROFILE.md 6.3 and 9 ("E2, đo"): a missing or relative ProjectRoot, a root
allowed_project_root refuses (Python refuses it before any lock), a GuardRoot that is not a folder,
a -Python that is not a .venv\\Scripts\\python.exe, a bad Stamp. No call here can start a
measurement: every one has at least one refused argument.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow import recycle_bin
from biliflow.hardware_system import WindowsSystem

CODE = Path(__file__).resolve().parents[1]
SCRIPT = CODE / "scripts" / "hardware-measure.ps1"
TEMP_PARENT = recycle_bin.INSTALL_ROOT / "temp"
STAMP = "20261011-100000-ab12"
USAGE = 64


def venv_python() -> Path | None:
    """This interpreter when it is some <folder>\\.venv\\Scripts\\python.exe (the install's venv)."""
    path = Path(sys.executable)
    if path.name.lower() == "python.exe" and path.parent.name.lower() == "scripts" \
            and path.parent.parent.name.lower() == ".venv":
        return path
    return None


class ScriptTextTests(unittest.TestCase):
    def test_the_script_is_ascii_only(self):
        data = SCRIPT.read_bytes()
        self.assertTrue(all(byte < 128 for byte in data), "Windows PowerShell 5.1 reads the script as ANSI")

    def test_the_script_holds_no_lock_and_never_isolates_python(self):
        text = SCRIPT.read_text(encoding="ascii")
        code_lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
        body = "\n".join(code_lines)
        for forbidden in ("Mutex", "WaitOne", "final-render.lock", "run.ps1", " -I ", " -E ", "'-I'", "'-E'"):
            self.assertNotIn(forbidden, body)
        self.assertIn(". (Join-Path $PSScriptRoot 'env.ps1')", body)
        self.assertIn("'-m', 'biliflow.hardware_profile', 'measure'", body)
        self.assertIn("exit $LASTEXITCODE", body)


@unittest.skipUnless(sys.platform == "win32", "Windows PowerShell")
class ScriptRefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        temp = TemporaryDirectory(dir=TEMP_PARENT)
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / "root"
        self.root.mkdir()
        python = venv_python()
        if python is None:
            self.skipTest("needs the .venv python.exe of the install")
        self.python = python

    def run_script(self, *args: str) -> subprocess.CompletedProcess:
        folder = WindowsSystem().system_directory()
        powershell = Path(folder) / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        return subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                               "-File", str(SCRIPT), *args], capture_output=True, timeout=120,
                              stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def assert_refused(self, *args: str, code: int = USAGE) -> None:
        result = self.run_script(*args)
        self.assertEqual(result.returncode, code, result.stderr.decode("utf-8", "replace")[-600:])
        self.assertEqual([path.name for path in self.root.iterdir()], [], "nothing is created in the root")

    def test_a_missing_or_relative_project_root_is_refused(self):
        for args in ((), ("-Python", str(self.python)),
                     ("-ProjectRoot", "temp\\root", "-Python", str(self.python)),
                     ("-ProjectRoot", "\\root", "-Python", str(self.python)),
                     ("-ProjectRoot", "C:root", "-Python", str(self.python)),
                     ("-ProjectRoot", f"{self.root}\\", "-Python", str(self.python))):
            with self.subTest(args=args):
                self.assert_refused(*args)

    def test_a_root_outside_this_checkout_is_refused_by_python_before_any_lock(self):
        refused = CODE / "docs"  # exists; this checkout's allowed_project_root refuses it
        result = self.run_script("-ProjectRoot", str(refused), "-GuardRoot", str(refused),
                                 "-Python", str(self.python), "-Stamp", STAMP)
        self.assertEqual(result.returncode, USAGE, result.stderr.decode("utf-8", "replace")[-600:])
        self.assertIn("--project-root", result.stderr.decode("utf-8", "replace"))
        self.assertFalse((refused / "temp").exists())
        self.assertFalse((refused / "state").exists())

    def test_a_guard_root_that_is_not_a_folder_is_refused(self):
        file_path = self.base / "file.txt"
        file_path.write_text("x", encoding="ascii")
        for guard in (str(self.base / "missing"), str(file_path), "relative", f"{self.base}\\"):
            with self.subTest(guard=guard):
                self.assert_refused("-ProjectRoot", str(self.root), "-GuardRoot", guard, "-Python", str(self.python))

    def test_python_must_be_an_existing_venv_python(self):
        fake_scripts = self.base / "elsewhere" / "Scripts"
        fake_scripts.mkdir(parents=True)
        (fake_scripts / "python.exe").write_bytes(b"")  # never run: refused for its folder
        missing = self.base / "other" / ".venv" / "Scripts" / "python.exe"
        for python in ("", str(SCRIPT), str(fake_scripts / "python.exe"), str(missing), "python.exe"):
            with self.subTest(python=python):
                args = ("-ProjectRoot", str(self.root)) + (("-Python", python) if python else ())
                self.assert_refused(*args)

    def test_a_bad_stamp_or_wait_or_launcher_is_refused(self):
        for name, value in (("-Stamp", "20261011-100000-AB12"), ("-Stamp", "2026-10-11"),
                            ("-Stamp", "20261011-100000-ab12x"), ("-WaitMinutes", "x"), ("-WaitMinutes", "-1"),
                            ("-LaunchedBy", "installer")):
            with self.subTest(option=name, value=value):
                self.assert_refused("-ProjectRoot", str(self.root), "-Python", str(self.python), name, value)

    def test_unexpected_arguments_are_refused(self):
        for extra in (("-GuardRoot",), ("-Mutex", "Local\\BiliFlowTest-x"), ("positional",),
                      ("-GuardRoot", str(self.root), "-LockFile", str(self.root))):
            with self.subTest(extra=extra):
                self.assert_refused("-ProjectRoot", str(self.root), "-Python", str(self.python), *extra)


if __name__ == "__main__":
    unittest.main()
