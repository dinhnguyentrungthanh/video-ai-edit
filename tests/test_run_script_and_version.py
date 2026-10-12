import re
import tomllib
import unittest
from pathlib import Path

import biliflow

ROOT = Path(__file__).resolve().parents[1]


class VersionSourceTests(unittest.TestCase):
    def test_pyproject_reads_the_package_version(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertNotIn("version", project["project"])
        self.assertIn("version", project["project"]["dynamic"])
        self.assertEqual(project["tool"]["setuptools"]["dynamic"]["version"],
                         {"attr": "biliflow.__version__"})
        self.assertRegex(biliflow.__version__, r"^\d+\.\d+\.\d+$")


class RunScriptMutexTests(unittest.TestCase):
    def test_an_abandoned_resource_mutex_is_taken_not_fatal(self):
        # The real behaviour was checked by hand with a uniquely named mutex: the wait
        # throws AbandonedMutexException yet owns the mutex. Tests never touch the GPU
        # or export mutex of a running Control Center.
        text = (ROOT / "scripts" / "run.ps1").read_text(encoding="utf-8")
        self.assertRegex(text, re.compile(
            r"try \{\s*\$HasMutex = \$ResourceMutex\.WaitOne\(\)\s*\}\s*"
            r"catch \[Threading\.AbandonedMutexException\] \{[^}]*\$HasMutex = \$true\s*\}"))
        self.assertIn("$ResourceMutex.ReleaseMutex()", text)


if __name__ == "__main__":
    unittest.main()
