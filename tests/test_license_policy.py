import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.license_policy import (
    LicensePolicyError,
    audit_model,
    ensure_model_allowed,
)


class LicensePolicyTests(unittest.TestCase):
    def make_project(self, directory: str, *, status: str = "APPROVED", license_spdx: str = "MIT"):
        root = Path(directory)
        (root / "config").mkdir()
        (root / "models" / "sample").mkdir(parents=True)
        policy = {
            "profile": "test",
            "requirements": {
                "usage_cost": "free",
                "local_inference_only": True,
                "commercial_use_allowed": True,
                "external_media_upload_allowed": False,
                "require_pinned_revision": True,
                "require_source_url": True,
                "allowed_model_licenses": ["MIT", "Apache-2.0"],
            },
            "models": {
                "sample": {
                    "approval_status": status,
                    "license_spdx": license_spdx,
                    "commercial_use_allowed": status == "APPROVED",
                    "usage_cost": "free",
                    "local_inference_only": True,
                    "external_media_upload": False,
                    "source_url": "https://example.test/model",
                }
            },
        }
        (root / "config" / "license_policy.json").write_text(json.dumps(policy), encoding="utf-8")
        (root / "models" / "sample" / "manifest.json").write_text(
            json.dumps({"model": "sample", "revision": "abc123"}), encoding="utf-8"
        )
        return root

    def test_approved_free_local_model_is_allowed(self):
        with TemporaryDirectory() as directory:
            root = self.make_project(directory)
            audit = ensure_model_allowed(root, root / "models" / "sample")
            self.assertTrue(audit.allowed)

    def test_noncommercial_model_is_blocked(self):
        with TemporaryDirectory() as directory:
            root = self.make_project(
                directory, status="BLOCKED", license_spdx="CC-BY-NC-4.0"
            )
            audit = audit_model(root, root / "models" / "sample")
            self.assertFalse(audit.allowed)
            with self.assertRaises(LicensePolicyError):
                ensure_model_allowed(root, root / "models" / "sample")

    def test_unknown_model_is_blocked(self):
        with TemporaryDirectory() as directory:
            root = self.make_project(directory)
            unknown = root / "models" / "unknown"
            unknown.mkdir()
            (unknown / "manifest.json").write_text(
                json.dumps({"revision": "abc123"}), encoding="utf-8"
            )
            self.assertFalse(audit_model(root, unknown).allowed)


if __name__ == "__main__":
    unittest.main()
