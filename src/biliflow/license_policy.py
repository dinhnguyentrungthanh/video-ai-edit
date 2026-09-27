from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class LicensePolicyError(RuntimeError):
    """Raised when a model is not allowed by the active license policy."""


@dataclass(frozen=True)
class ModelLicenseAudit:
    directory: str
    model: str
    approval_status: str
    allowed: bool
    license_spdx: str
    source_url: str
    training_data_review_status: str
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "directory": self.directory,
            "model": self.model,
            "approval_status": self.approval_status,
            "allowed": self.allowed,
            "license_spdx": self.license_spdx,
            "source_url": self.source_url,
            "training_data_review_status": self.training_data_review_status,
            "reasons": list(self.reasons),
        }


def load_license_policy(project_root: Path) -> dict[str, Any]:
    path = project_root / "config" / "license_policy.json"
    if not path.is_file():
        raise LicensePolicyError(f"Thiếu chính sách giấy phép: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_manifest(model_path: Path) -> dict[str, Any]:
    path = model_path / "manifest.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def audit_model(project_root: Path, model_path: Path) -> ModelLicenseAudit:
    model_path = model_path.resolve()
    policy = load_license_policy(project_root)
    manifest = _load_manifest(model_path)
    inventory = policy.get("models", {}).get(model_path.name, {})
    metadata = {**manifest, **inventory}
    requirements = policy["requirements"]
    reasons: list[str] = []

    status = str(metadata.get("approval_status", "UNKNOWN"))
    license_spdx = str(metadata.get("license_spdx", "UNKNOWN"))
    source_url = str(metadata.get("source_url", ""))
    if status != "APPROVED":
        reasons.append(metadata.get("blocked_reason") or "Model chưa được duyệt cho profile hiện tại.")
    if license_spdx not in requirements["allowed_model_licenses"]:
        reasons.append(f"Giấy phép {license_spdx} không nằm trong danh sách commercial-safe.")
    if metadata.get("commercial_use_allowed") is not True:
        reasons.append("Chưa xác nhận quyền sử dụng thương mại.")
    if metadata.get("usage_cost") != requirements["usage_cost"]:
        reasons.append("Model/dịch vụ không được xác nhận là miễn phí.")
    if requirements["local_inference_only"] and metadata.get("local_inference_only") is not True:
        reasons.append("Suy luận không được xác nhận chạy hoàn toàn local.")
    if metadata.get("external_media_upload") is not False:
        reasons.append("Chưa xác nhận rằng video/ảnh không bị gửi ra dịch vụ ngoài.")
    if requirements["require_source_url"] and not source_url:
        reasons.append("Thiếu URL nguồn để kiểm toán giấy phép.")
    if requirements["require_pinned_revision"] and not manifest.get("revision"):
        reasons.append("Manifest chưa ghim revision của model.")

    return ModelLicenseAudit(
        directory=model_path.name,
        model=str(manifest.get("model") or metadata.get("engine") or model_path.name),
        approval_status=status,
        allowed=not reasons,
        license_spdx=license_spdx,
        source_url=source_url,
        training_data_review_status=str(
            metadata.get("training_data_review_status", "UNKNOWN")
        ),
        reasons=tuple(dict.fromkeys(str(reason) for reason in reasons)),
    )


def ensure_model_allowed(project_root: Path, model_path: Path) -> ModelLicenseAudit:
    audit = audit_model(project_root, model_path)
    if not audit.allowed:
        detail = "; ".join(audit.reasons)
        raise LicensePolicyError(
            f"Từ chối dùng model '{audit.directory}' theo chính sách miễn phí/bản quyền: {detail}"
        )
    return audit


def audit_project_models(project_root: Path) -> dict[str, Any]:
    model_root = project_root / "models"
    audits = [
        audit_model(project_root, path)
        for path in sorted(model_root.iterdir())
        if path.is_dir() and (path / "manifest.json").is_file()
    ]
    return {
        "policy": str((project_root / "config" / "license_policy.json").resolve()),
        "profile": load_license_policy(project_root)["profile"],
        "allowed_count": sum(item.allowed for item in audits),
        "blocked_count": sum(not item.allowed for item in audits),
        "models": [item.as_dict() for item in audits],
    }
