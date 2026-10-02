from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from biliflow import __version__


AI_CONFIG_PATH = Path("config/ai_supervisor.json")
ALLOWED_AI_MODELS = {
    "gpt-5.6-luna": "Nhanh, phù hợp audit JSON thường xuyên",
    "gpt-5.6-terra": "Cân bằng cho audit khó hơn",
    "gpt-5.6-sol": "Mạnh hơn cho trường hợp phức tạp",
}
ALLOWED_AI_EFFORTS = {"low", "medium", "high"}
DEFAULT_AI_CONFIG: dict[str, Any] = {
    "schema_version": 1,
    "enabled": True,
    "codex_command": "auto",
    "codex_home": "auto",
    "authentication": "chatgpt_required",
    "model": "gpt-5.6-luna",
    "reasoning_effort": "medium",
    "service_tier": "default",
    "persistent_thread": True,
    "timeout_seconds": 300,
    "send_media": False,
    "visual_audit_available": True,
    "visual_audit_requires_job_opt_in": True,
    "max_visual_images": 36,
    "max_visual_images_per_item": 3,
    "visual_batch_size": 12,
    "billing_mode": "chatgpt_included_only",
    "api_key_allowed": False,
}


AUDIT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "result": {"enum": ["PASS", "WARN", "BLOCK"]},
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}},
        "recommended_actions": {"type": "array", "items": {"type": "string"}},
        "visual_assessments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "item_id": {"type": "string"},
                    "classification": {
                        "enum": [
                            "external_brand", "movie_title", "promotional_content",
                            "film_content", "adult", "gore", "violence",
                            "false_positive", "uncertain",
                        ]
                    },
                    "suggested_decision": {
                        "enum": ["KEEP", "BLUR", "CUT", "NEEDS_MORE_CONTEXT"]
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "region_assessment": {
                        "enum": [
                            "TIGHT", "TOO_WIDE", "TOO_NARROW", "MISSING",
                            "FULL_FRAME_REQUIRED", "NOT_APPLICABLE",
                        ]
                    },
                    "reasoning": {"type": "string"},
                },
                "required": [
                    "item_id", "classification", "suggested_decision",
                    "confidence", "region_assessment", "reasoning",
                ],
            },
        },
    },
    "required": [
        "result", "summary", "findings", "recommended_actions",
        "visual_assessments",
    ],
}


def validate_ai_config(value: dict[str, Any]) -> dict[str, Any]:
    config = {**DEFAULT_AI_CONFIG, **value}
    if config.get("schema_version") != 1:
        raise ValueError("Unsupported AI Supervisor config schema")
    if config.get("model") not in ALLOWED_AI_MODELS:
        raise ValueError("AI Supervisor model must be from the GPT-5.6 family")
    if config.get("reasoning_effort") not in ALLOWED_AI_EFFORTS:
        raise ValueError("AI Supervisor reasoning effort must be low, medium, or high")
    if config.get("authentication") != "chatgpt_required":
        raise ValueError("AI Supervisor only permits ChatGPT authentication")
    if config.get("service_tier") != "default":
        raise ValueError("AI Supervisor service tier is locked to default")
    if config.get("send_media") is not False:
        raise ValueError("Persistent AI media transfer must remain disabled")
    if config.get("visual_audit_requires_job_opt_in") is not True:
        raise ValueError("Visual AI Audit must require explicit opt-in for every job")
    if config.get("billing_mode") != "chatgpt_included_only":
        raise ValueError("AI Supervisor billing must remain ChatGPT-included only")
    if config.get("api_key_allowed") is not False:
        raise ValueError("AI Supervisor API-key billing must remain disabled")
    config["visual_audit_available"] = bool(config.get("visual_audit_available", True))
    max_images = int(config.get("max_visual_images", 36))
    if not 1 <= max_images <= 50:
        raise ValueError("Visual AI Audit image limit must be between 1 and 50")
    config["max_visual_images"] = max_images
    max_per_item = int(config.get("max_visual_images_per_item", 3))
    if not 1 <= max_per_item <= 3:
        raise ValueError("Visual AI Audit allows at most three images per review item")
    config["max_visual_images_per_item"] = max_per_item
    batch_size = int(config.get("visual_batch_size", 12))
    if not 4 <= batch_size <= 16:
        raise ValueError("Visual AI Audit batch size must be between 4 and 16")
    config["visual_batch_size"] = batch_size
    timeout = int(config.get("timeout_seconds", 300))
    if not 60 <= timeout <= 900:
        raise ValueError("AI Supervisor timeout must be between 60 and 900 seconds")
    config["timeout_seconds"] = timeout
    config["enabled"] = bool(config.get("enabled", True))
    config["persistent_thread"] = bool(config.get("persistent_thread", True))
    command = str(config.get("codex_command", "auto")).strip()
    if command != "auto" and not Path(command).is_absolute():
        raise ValueError("codex_command must be auto or an absolute executable path")
    config["codex_command"] = command
    codex_home = str(config.get("codex_home", "auto")).strip()
    if codex_home != "auto" and not Path(codex_home).is_absolute():
        raise ValueError("codex_home must be auto or an absolute directory path")
    config["codex_home"] = codex_home
    return config


def load_ai_config(root: Path) -> dict[str, Any]:
    path = root.resolve(strict=True) / AI_CONFIG_PATH
    if not path.exists():
        return dict(DEFAULT_AI_CONFIG)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("AI Supervisor config must be a JSON object")
    return validate_ai_config(payload)


def save_ai_config(root: Path, updates: dict[str, Any]) -> dict[str, Any]:
    root = root.resolve(strict=True)
    current = load_ai_config(root)
    allowed = {"enabled", "model", "reasoning_effort", "persistent_thread"}
    unknown = set(updates) - allowed
    if unknown:
        raise ValueError(f"Unsupported AI Supervisor settings: {sorted(unknown)}")
    config = validate_ai_config({**current, **updates})
    path = root / AI_CONFIG_PATH
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return config


def resolve_codex_command(root: Path, config: dict[str, Any] | None = None) -> str | None:
    config = config or load_ai_config(root)
    value = str(config.get("codex_command", "auto"))
    if value == "auto":
        command = shutil.which("codex")
        if command:
            return command
        if os.name == "nt":
            local_roots: list[Path] = []
            local_app_data = os.environ.get("LOCALAPPDATA")
            if local_app_data:
                local_roots.append(Path(local_app_data))
            local_roots.append(Path.home() / "AppData" / "Local")
            candidates: list[Path] = []
            for local_root in dict.fromkeys(local_roots):
                bin_root = local_root / "OpenAI" / "Codex" / "bin"
                candidates.append(bin_root / "codex.exe")
                if bin_root.is_dir():
                    candidates.extend(bin_root.glob("*/codex.exe"))
            existing = [
                candidate.resolve() for candidate in candidates
                if candidate.exists() and candidate.is_file()
            ]
            if existing:
                return str(max(existing, key=lambda path: path.stat().st_mtime_ns))
        return None
    path = Path(value)
    return str(path) if path.exists() and path.is_file() else None


def resolve_codex_home(config: dict[str, Any]) -> Path | None:
    value = str(config.get("codex_home", "auto"))
    if value != "auto":
        return Path(value)
    candidates: list[Path] = []
    environment_home = os.environ.get("CODEX_HOME")
    if environment_home:
        candidates.append(Path(environment_home))
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        candidates.append(Path(user_profile) / ".codex")
    candidates.append(Path.home() / ".codex")
    for candidate in dict.fromkeys(candidates):
        if candidate.exists() and candidate.is_dir():
            return candidate.resolve()
    return None


def codex_subprocess_environment(config: dict[str, Any]) -> dict[str, str]:
    environment = os.environ.copy()
    codex_home = resolve_codex_home(config)
    if codex_home is not None:
        environment["CODEX_HOME"] = str(codex_home)
    return environment


def codex_connection_status(root: Path) -> dict[str, Any]:
    config = load_ai_config(root)
    command = resolve_codex_command(root, config)
    result: dict[str, Any] = {
        "config": config,
        "models": ALLOWED_AI_MODELS,
        "efforts": sorted(ALLOWED_AI_EFFORTS),
        "installed": command is not None,
        "command": command,
        "authenticated": False,
        "authentication_method": "none",
        "ready": False,
        "message": "Không tìm thấy Codex trên máy",
        "login_docs": "https://learn.chatgpt.com/docs/auth",
    }
    if not command:
        return result
    try:
        completed = subprocess.run(
            [command, "login", "status"], cwd=root, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=12,
            env=codex_subprocess_environment(config),
        )
        output = (completed.stdout + "\n" + completed.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as error:
        result["message"] = f"Không kiểm tra được đăng nhập Codex: {error}"
        return result
    lowered = output.casefold()
    if completed.returncode == 0 and "chatgpt" in lowered:
        result.update({
            "authenticated": True, "authentication_method": "chatgpt",
            "ready": bool(config["enabled"]),
            "message": "Đã kết nối bằng tài khoản ChatGPT",
        })
    elif completed.returncode == 0 and ("api key" in lowered or "api_key" in lowered):
        result.update({
            "authenticated": True, "authentication_method": "api_key",
            "message": "Đang đăng nhập bằng API key; BiliFlow chặn để tránh phí API",
        })
    elif completed.returncode == 0:
        result["message"] = "Codex có đăng nhập nhưng không xác định được phương thức; hãy đăng nhập lại bằng ChatGPT"
    else:
        result["message"] = "Chưa đăng nhập Codex bằng ChatGPT"
    if not config["enabled"]:
        result["ready"] = False
        result["message"] = "AI Supervisor đang bị tắt trong cấu hình"
    return result


def start_codex_login(root: Path, log_path: Path) -> tuple[subprocess.Popen, Any]:
    config = load_ai_config(root)
    command = resolve_codex_command(root, config)
    if not command:
        raise RuntimeError("Không tìm thấy Codex. Hãy cài ứng dụng Codex trước")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("ab")
    kwargs: dict[str, Any] = {
        "cwd": root, "stdout": handle, "stderr": subprocess.STDOUT,
        "env": codex_subprocess_environment(config),
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    try:
        process = subprocess.Popen([command, "login"], **kwargs)
    except Exception:
        handle.close()
        raise
    return process, handle


class AppServerClient:
    """Small newline JSON-RPC client for the official `codex app-server` stdio API."""

    def __init__(self, root: Path, popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen):
        config = load_ai_config(root)
        command = resolve_codex_command(root, config)
        if not command:
            raise RuntimeError("Codex CLI is not installed or is not on PATH")
        self.process = popen_factory(
            [command, "app-server", "--stdio"], cwd=root,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env=codex_subprocess_environment(config),
        )
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        self._next_id = 1

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    self._messages.put(value)
            except ValueError:
                continue

    def send(self, method: str, params: dict[str, Any] | None = None, *, request: bool = True) -> int | None:
        assert self.process.stdin is not None
        message: dict[str, Any] = {"method": method}
        identifier = None
        if request:
            identifier = self._next_id
            self._next_id += 1
            message["id"] = identifier
        if params is not None:
            message["params"] = params
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        return identifier

    def wait_response(self, identifier: int, timeout: float = 30.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        deferred: list[dict[str, Any]] = []
        try:
            while time.monotonic() < deadline:
                value = self._messages.get(timeout=max(0.01, deadline - time.monotonic()))
                if value.get("id") == identifier:
                    if "error" in value:
                        raise RuntimeError(f"Codex App Server error: {value['error']}")
                    return value.get("result", {})
                deferred.append(value)
        finally:
            for value in deferred:
                self._messages.put(value)
        raise TimeoutError("Timed out waiting for Codex App Server")

    def next_message(self, timeout: float) -> dict[str, Any]:
        return self._messages.get(timeout=timeout)

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()


def _extract_thread_id(result: dict[str, Any]) -> str:
    thread = result.get("thread", result)
    value = thread.get("id") if isinstance(thread, dict) else None
    if not value:
        raise RuntimeError("Codex App Server did not return a thread id")
    return str(value)


def _region_intersection_over_smaller(first: dict, second: dict) -> float:
    ax1, ay1 = int(first["x"]), int(first["y"])
    ax2, ay2 = ax1 + int(first["width"]), ay1 + int(first["height"])
    bx1, by1 = int(second["x"]), int(second["y"])
    bx2, by2 = bx1 + int(second["width"]), by1 + int(second["height"])
    intersection = max(0, min(ax2, bx2) - max(ax1, bx1)) * max(
        0, min(ay2, by2) - max(ay1, by1)
    )
    smaller = min(
        max(1, int(first["width"]) * int(first["height"])),
        max(1, int(second["width"]) * int(second["height"])),
    )
    return intersection / smaller


def _region_union(first: dict, second: dict) -> dict:
    left = min(int(first["x"]), int(second["x"]))
    top = min(int(first["y"]), int(second["y"]))
    right = max(
        int(first["x"]) + int(first["width"]),
        int(second["x"]) + int(second["width"]),
    )
    bottom = max(
        int(first["y"]) + int(first["height"]),
        int(second["y"]) + int(second["height"]),
    )
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def queue_quality_findings(queue_payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Run deterministic edit-quality checks before accepting an AI PASS."""
    items = list(queue_payload.get("items") or [])
    findings: list[str] = []
    actions: list[str] = []
    promo_terms = ("quảng bá", "promotional", "promotion", "advertisement", "branded intro")
    early = [item for item in items if float(item.get("start_seconds", 0)) < 30]
    for item in early:
        text = " ".join(
            str(value) for value in [*(item.get("labels") or []), *(item.get("reasons") or [])]
        ).casefold()
        is_promo = item.get("candidate_type") == "opening_promotion" or any(
            term in text for term in promo_terms
        )
        # An opening studio ident has no pre-selected CUT by design (R3a/R3b, 2026-10-01):
        # keeping it, or confirming it as a studio logo, is a valid human decision.
        studio_ident_kept = item.get("decision") == "KEEP" and bool(
            item.get("opening_ident") or item.get("suggestion_withheld")
            or item.get("studio_logo_memory") or item.get("studio_logo_match")
        )
        if is_promo and item.get("decision") not in {None, "CUT"} and not studio_ident_kept:
            findings.append(
                f"Opening promotion {item.get('start_seconds')}–{item.get('end_seconds')}s is {item.get('decision')} instead of CUT."
            )
            actions.append("Review the complete opening promotion and prefer one CUT interval.")
        if not is_promo or item.get("decision") != "CUT":
            continue
        adjacent = [
            other for other in early
            if other is not item
            and 0 <= float(other.get("start_seconds", 0)) - float(item.get("end_seconds", 0)) <= 1.0
            and other.get("category") == "visual_logo"
            and other.get("decision") == "BLUR"
        ]
        if adjacent:
            next_item = adjacent[0]
            findings.append(
                f"A CUT opening promotion ends at {item.get('end_seconds')}s but an adjacent branded window "
                f"{next_item.get('start_seconds')}–{next_item.get('end_seconds')}s is only BLUR; the promotion may remain visible."
            )
            actions.append("Inspect the opening boundary and extend CUT through all full-frame promotional material.")

    for item in items:
        decision_region = item.get("decision_region_source_pixels")
        suggested_region = item.get("suggested_region_source_pixels")
        if (
            item.get("candidate_type") != "persistent_overlay"
            or item.get("decision") != "BLUR"
            or not isinstance(decision_region, dict)
            or not isinstance(suggested_region, dict)
        ):
            continue
        suggested_area = int(suggested_region["width"]) * int(suggested_region["height"])
        decision_area = int(decision_region["width"]) * int(decision_region["height"])
        tighter = []
        for other in items:
            other_region = other.get("suggested_region_source_pixels")
            sources = set(other.get("model_evidence", {}).get("region_sources") or [])
            trusted_tight_region = (
                "ocr" in sources
                or other.get("region_classification") == "external_brand"
            )
            if (
                other is item or not isinstance(other_region, dict) or not trusted_tight_region
                or float(other.get("start_seconds", 0)) >= float(item.get("end_seconds", 0))
                or float(other.get("end_seconds", 0)) <= float(item.get("start_seconds", 0))
            ):
                continue
            other_area = int(other_region["width"]) * int(other_region["height"])
            if (
                other_area <= suggested_area * 0.60
                and _region_intersection_over_smaller(suggested_region, other_region) >= 0.70
            ):
                tighter.append(other_region)
        if len(tighter) >= 2:
            tight_union = tighter[0]
            for candidate in tighter[1:]:
                tight_union = _region_union(tight_union, candidate)
            tight_area = int(tight_union["width"]) * int(tight_union["height"])
            tight_ratio = tight_area / max(1, suggested_area)
            decision_ratio = decision_area / max(1, suggested_area)
            if tight_ratio < 0.45 and decision_ratio < 0.55:
                findings.append(
                    f"Persistent blur {item.get('start_seconds')}–{item.get('end_seconds')}s is too small: "
                    "OCR appears to cover only text inside a larger grounded graphical logo."
                )
                actions.append("Restore the full grounded emblem and trim only its outer padding.")
            elif tight_ratio < 0.45 and decision_ratio > 0.90:
                findings.append(
                    f"Persistent blur {item.get('start_seconds')}–{item.get('end_seconds')}s still uses the "
                    "untrimmed grounding box even though repeated OCR confirms the logo center."
                )
                actions.append("Trim grounding-box padding while retaining the complete graphical emblem.")
            elif tight_ratio >= 0.45 and decision_area > tight_area * 1.50:
                findings.append(
                    f"Persistent blur {item.get('start_seconds')}–{item.get('end_seconds')}s uses an oversized region "
                    f"despite {len(tighter)} repeated tight OCR observations."
                )
                actions.append("Refine the persistent blur from repeated tight OCR boxes before export.")
    return list(dict.fromkeys(findings)), list(dict.fromkeys(actions))


def queue_integrity_blockers(
    root: Path, queue_payload: dict[str, Any],
) -> list[str]:
    """Return only machine-verifiable reasons that may block human review."""
    blockers: list[str] = []
    source = queue_payload.get("source") or {}
    source_sha = str(source.get("sha256") or "")
    source_duration = float(source.get("duration_seconds") or 0.0)
    coverage = queue_payload.get("candidate_coverage")
    if isinstance(coverage, dict):
        if (
            not coverage.get("complete")
            or coverage.get("missing_refs")
            or any(not item.get("complete") for item in coverage.get("reports") or [])
        ):
            blockers.append("Candidate coverage manifest is incomplete.")
    item_ids = [str(item.get("id") or "") for item in queue_payload.get("items") or []]
    duplicate_ids = sorted({value for value in item_ids if value and item_ids.count(value) > 1})
    if duplicate_ids:
        blockers.append(
            "Review queue contains duplicate item IDs: " + ", ".join(duplicate_ids)
        )
    for item in queue_payload.get("items") or []:
        start = float(item.get("start_seconds") or 0.0)
        end = float(item.get("end_seconds") or 0.0)
        if start < 0 or end <= start or (source_duration > 0 and end > source_duration + 0.001):
            blockers.append(f"Review item {item.get('id')} has an invalid timeline interval.")
    for relative in queue_payload.get("reports") or []:
        path = (root / str(relative)).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            blockers.append(f"Report path escapes the project root: {relative}")
            continue
        if not path.is_file():
            blockers.append(f"Report is missing: {relative}")
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            blockers.append(f"Report is unreadable: {relative}")
            continue
        if report.get("status") not in {"COMPLETED", "REVIEW_REQUIRED"}:
            blockers.append(f"Report has a non-reviewable status: {relative}")
        if report.get("error"):
            blockers.append(f"Report contains an unresolved error: {relative}")
        report_sha = str(report.get("input_sha256") or "")
        if source_sha and report_sha and report_sha != source_sha:
            blockers.append(f"Report checksum does not match the queue source: {relative}")
        try:
            report_duration = float(report.get("duration_seconds"))
        except (TypeError, ValueError):
            blockers.append(f"Report duration is missing or invalid: {relative}")
        else:
            if source_duration > 0 and abs(report_duration - source_duration) > 0.1:
                blockers.append(f"Report duration does not match the queue source: {relative}")
    return list(dict.fromkeys(blockers))



_VISUAL_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def collect_visual_evidence(
    root: Path, queue_payload: dict[str, Any], *, max_images: int,
    max_images_per_item: int,
) -> list[dict[str, Any]]:
    """Select bounded review thumbnails; never opens or attaches the source video."""
    root = root.resolve(strict=True)
    reports_root = (root / "reports").resolve(strict=True)
    priority = {"visual_logo": 0, "text": 1, "adult": 2, "gore": 2, "violence": 2}

    prepared: list[tuple[dict[str, Any], list[Path]]] = []
    ordered = sorted(
        list(queue_payload.get("items") or []),
        key=lambda item: (
            priority.get(str(item.get("category")), 3),
            0 if item.get("priority") == "high" else 1,
            float(item.get("start_seconds") or 0),
        ),
    )
    for item in ordered:
        paths: list[Path] = []
        for value in list(item.get("preview_images") or [])[:max_images_per_item]:
            candidate = (root / str(value)).resolve()
            try:
                candidate.relative_to(reports_root)
            except ValueError:
                continue
            if (
                candidate.is_file()
                and candidate.suffix.casefold() in _VISUAL_IMAGE_EXTENSIONS
                and candidate.stat().st_size <= 8 * 1024 * 1024
            ):
                paths.append(candidate)
        if paths:
            prepared.append((item, paths))

    selected: list[dict[str, Any]] = []
    # Round-robin keeps broad timeline/category coverage before adding extra
    # context frames for persistent logos and promotional segments.
    for image_index in range(max_images_per_item):
        for item, paths in prepared:
            if len(selected) >= max_images:
                return selected
            if image_index >= len(paths):
                continue
            selected.append({
                "item_id": str(item.get("id")),
                "category": str(item.get("category") or ""),
                "candidate_type": item.get("candidate_type"),
                "start_seconds": float(item.get("start_seconds") or 0),
                "end_seconds": float(item.get("end_seconds") or 0),
                "suggested_region_source_pixels": item.get(
                    "suggested_region_source_pixels"
                ),
                "source_frame_size": item.get("source_frame_size"),
                "review_kind": item.get("review_kind"),
                "path": paths[image_index],
                # GPT-5.6 Luna/App Server on this host did not expose low-detail
                # safety thumbnails to the model. High detail is required for
                # every category so an assessment always has inspectable pixels.
                "detail": "high",
            })
    return selected


def _visual_turn_input(
    prompt: str, evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for index, item in enumerate(evidence, 1):
        marker = {
            key: value for key, value in item.items()
            if key not in {"path", "detail"}
        }
        values.append({
            "type": "text",
            "text": f"Visual evidence {index}: "
                    + json.dumps(marker, ensure_ascii=False),
        })
        values.append({
            "type": "localImage",
            "path": str(item["path"]),
            "detail": item["detail"],
        })
    return values


def run_local_queue_audit(
    *, root: Path, job: dict[str, Any], queue_path: Path,
) -> dict[str, Any]:
    """Run deterministic queue checks without Codex, media, or account quota."""
    root = root.resolve(strict=True)
    queue_path = queue_path.resolve(strict=True)
    queue_payload = json.loads(queue_path.read_text(encoding="utf-8"))
    blockers = queue_integrity_blockers(root, queue_payload)
    findings, actions = queue_quality_findings(queue_payload)
    if blockers:
        result = "BLOCK"
        summary = "Kiểm tra cấu trúc cục bộ phát hiện lỗi toàn vẹn cần sửa."
    elif findings:
        result = "WARN"
        summary = "Kiểm tra cấu trúc cục bộ phát hiện điểm chất lượng cần xem lại."
    else:
        result = "PASS"
        summary = "Cấu trúc queue, nguồn và độ bao phủ detector đều hợp lệ."
    return {
        "schema_version": 1,
        "result": result,
        "summary": summary,
        "findings": list(dict.fromkeys([*blockers, *findings])),
        "recommended_actions": list(dict.fromkeys(actions)),
        "job_key": job["job_key"],
        "queue": queue_path.relative_to(root).as_posix(),
        "detection_scope": queue_payload.get("detection_scope"),
        "candidate_coverage": queue_payload.get("candidate_coverage"),
        "authority": "DETERMINISTIC_LOCAL",
        "execution": {
            "uses_codex": False,
            "uses_model": False,
            "sends_media": False,
            "uses_chatgpt_quota": False,
        },
    }

def run_ai_audit(*, root: Path, job: dict[str, Any], queue_path: Path,
                 thread_id: str | None = None,
                 cancel_event: threading.Event | None = None,
                 timeout: float | None = None,
                 visual_opt_in: bool = False,
                 visual_batch_offset: int = 0,
                 visual_batch_limit: int | None = None,
                 connection_checker: Callable[[Path], dict[str, Any]] = codex_connection_status,
                 client_factory: Callable[[Path], AppServerClient] = AppServerClient) -> dict[str, Any]:
    """Ask Codex to audit reports and optional job-approved thumbnails.

    The result is advisory. It cannot approve edits or start rendering.
    """
    root = root.resolve(strict=True)
    config = load_ai_config(root)
    if not config["enabled"]:
        raise RuntimeError("AI Supervisor is disabled in config/ai_supervisor.json")
    connection = connection_checker(root)
    if not connection["ready"]:
        raise RuntimeError(connection["message"])
    timeout = float(timeout or config["timeout_seconds"])
    queue_path = queue_path.resolve(strict=True)
    queue_payload = json.loads(queue_path.read_text(encoding="utf-8"))
    report_paths = [str(value) for value in queue_payload.get("reports", [])]
    visual_evidence: list[dict[str, Any]] = []
    if visual_opt_in:
        if not config.get("visual_audit_available", True):
            raise RuntimeError("Visual AI Audit is disabled in project configuration")
        all_visual_evidence = collect_visual_evidence(
            root, queue_payload,
            max_images=int(config["max_visual_images"]),
            max_images_per_item=int(config["max_visual_images_per_item"]),
        )
        batch_offset = max(0, int(visual_batch_offset))
        batch_limit = int(
            visual_batch_limit or config.get("visual_batch_size", 12)
        )
        visual_evidence = all_visual_evidence[
            batch_offset:batch_offset + batch_limit
        ]
        if not visual_evidence:
            raise RuntimeError("No safe review thumbnails are available for Visual AI Audit")
    local_findings, local_actions = queue_quality_findings(queue_payload)
    integrity_blockers = queue_integrity_blockers(root, queue_payload)
    attached_ids = {item["item_id"] for item in visual_evidence}
    context_items = [
        {
            key: item.get(key)
            for key in (
                "id", "category", "candidate_type", "priority",
                "start_seconds", "end_seconds", "labels", "reasons",
                "suggested_decision", "suggested_region_source_pixels",
                "source_frame_size", "review_kind", "region_classification",
            )
        }
        for item in list(queue_payload.get("items") or [])
        if not attached_ids or str(item.get("id")) in attached_ids
    ]
    report_manifest = []
    for relative in report_paths:
        report_path = (root / relative).resolve()
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            report = {}
        report_manifest.append({
            "path": relative,
            "status": report.get("status"),
            "input_sha256": report.get("input_sha256"),
            "duration_seconds": report.get("duration_seconds"),
            "error": report.get("error"),
        })
    compact_context = {
        "source": queue_payload.get("source"),
        "counts": queue_payload.get("counts"),
        "candidate_coverage": queue_payload.get("candidate_coverage"),
        "detection_scope": queue_payload.get("detection_scope"),
        "reports": report_manifest,
        "items_in_this_audit": context_items,
    }
    visual_guidance = (
        "Inspect only the explicitly attached review thumbnails. Never open the source video or audio. "
        "Return one visual_assessments entry per unique attached item_id. Distinguish external brand/logo "
        "from a movie title, legitimate film content, full-frame promotion, violence, gore, adult content, "
        "and false positives. When suggested_region_source_pixels and source_frame_size are present, "
        "evaluate the object inside that target rectangle. A logo elsewhere in the same full-frame thumbnail "
        "does not make the target region a logo; use outside content only as context. Evaluate whether the "
        "target blur region is tight, too wide, too narrow, or missing. Do not invent objects outside the "
        "visible evidence. "
        if visual_evidence else
        "No images are attached. Leave visual_assessments empty and do not open media files. "
    )
    prompt = (
        "Audit one BiliFlow review queue using the local JSON files listed below and only the explicitly "
        "attached review thumbnails, if any. "
        + visual_guidance
        + "Do not modify files and do not make human review decisions. Check report completeness, "
        "source/checksum consistency, suspicious timeline gaps, unresolved detector errors, and "
        "whether the queue is safe to present for human review. This is a PRE-REVIEW audit: null human "
        "decisions are expected and must not cause BLOCK by themselves. A pending opening promotion is safe "
        "to present when it is explicitly classified as opening_promotion and has suggested_decision CUT, "
        "or when it is an opening studio ident (opening_ident true, suggestion_withheld) that deliberately "
        "has no pre-selected decision; KEEP is a valid decision for such an ident. "
        "The human still makes the actual decision. Use candidate_coverage in the queue to distinguish proven "
        "cross-detector deduplication from missing candidates: when complete is true, missing_refs is empty, "
        "and every report entry is complete, do not infer missing coverage merely because queue_item_count is "
        "smaller than source_candidate_count. Audit edit quality as well as structure: "
        "full-frame opening/closing promotions should be CUT rather than regionally blurred, and a persistent "
        "logo blur must cover the complete emblem without excessive padding; repeated OCR can be only one text "
        "fragment inside a graphical logo. Treat the deterministic preflight "
        "findings below as mandatory WARN findings unless the JSON proves they are resolved. Return the required JSON only.\n\n"
        f"Job: {job['job_key']}\nQueue: {queue_path.relative_to(root).as_posix()}\n"
        f"Reports: {json.dumps(report_paths, ensure_ascii=False)}\n"
        f"Compact queue manifest: {json.dumps(compact_context, ensure_ascii=False)}\n"
        f"Deterministic integrity blockers: {json.dumps(integrity_blockers, ensure_ascii=False)}\n"
        f"Deterministic preflight findings: {json.dumps(local_findings, ensure_ascii=False)}"
    )
    client = client_factory(root)
    try:
        request_id = client.send("initialize", {
            "clientInfo": {"name": "biliflow-control-center", "version": __version__},
            "capabilities": {"experimentalApi": False},
        })
        assert request_id is not None
        client.wait_response(request_id, timeout=30)
        client.send("initialized", request=False)
        supervisor_instructions = (
            "You are a read-only quality auditor. Never call tools that change files. "
            "You may inspect only localImage inputs explicitly attached to the current turn. "
            "Never open the source video, audio, or any other media path. Every edit remains "
            "subject to explicit human approval."
        )
        # Reuse one persistent supervisor thread for JSON and visual audits.  Every
        # turn still carries a complete queue manifest and visual item allow-list;
        # returned assessments are filtered to the current turn's attached IDs.
        active_thread_id = thread_id
        if active_thread_id:
            try:
                request_id = client.send("thread/resume", {
                    "threadId": active_thread_id, "cwd": str(root),
                    "model": config["model"], "serviceTier": config["service_tier"],
                    "approvalPolicy": "never", "sandbox": "read-only",
                    "developerInstructions": supervisor_instructions,
                    "excludeTurns": True,
                })
                assert request_id is not None
                active_thread_id = _extract_thread_id(
                    client.wait_response(request_id, timeout=30)
                )
            except Exception:
                active_thread_id = None
        if not active_thread_id:
            request_id = client.send("thread/start", {
                "cwd": str(root), "approvalPolicy": "never", "sandbox": "read-only",
                "ephemeral": not config["persistent_thread"],
                "model": config["model"], "serviceTier": config["service_tier"],
                "developerInstructions": supervisor_instructions,
            })
            assert request_id is not None
            active_thread_id = _extract_thread_id(client.wait_response(request_id, timeout=30))
        request_id = client.send("turn/start", {
            "threadId": active_thread_id,
            "input": _visual_turn_input(prompt, visual_evidence),
            "outputSchema": AUDIT_SCHEMA,
            "model": config["model"],
            "effort": config["reasoning_effort"],
            "serviceTierForTurn": config["service_tier"],
        })
        assert request_id is not None
        client.wait_response(request_id, timeout=30)
        deadline = time.monotonic() + timeout
        fragments: list[str] = []
        while time.monotonic() < deadline:
            if cancel_event and cancel_event.is_set():
                raise RuntimeError("AI Supervisor audit cancelled during shutdown")
            try:
                message = client.next_message(timeout=min(1.0, max(0.1, deadline - time.monotonic())))
            except queue.Empty:
                continue
            method = message.get("method")
            params = message.get("params") or {}
            if method == "item/agentMessage/delta":
                fragments.append(str(params.get("delta", "")))
            elif method == "item/completed":
                item = params.get("item") or {}
                if item.get("type") == "agentMessage" and item.get("text"):
                    fragments = [str(item["text"])]
            elif method == "turn/completed":
                turn = params.get("turn") or {}
                if turn.get("status") not in {None, "completed"}:
                    raise RuntimeError(f"Codex audit ended with status {turn.get('status')}")
                break
        else:
            raise TimeoutError("AI Supervisor audit timed out")
        text = "".join(fragments).strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].lstrip()
        payload = json.loads(text)
        payload.setdefault("visual_assessments", [])
        allowed_visual_ids = {item["item_id"] for item in visual_evidence}
        payload["visual_assessments"] = [
            item for item in list(payload.get("visual_assessments") or [])
            if isinstance(item, dict) and item.get("item_id") in allowed_visual_ids
        ]
        if payload.get("result") not in {"PASS", "WARN", "BLOCK"}:
            raise ValueError("AI Supervisor returned an invalid result")
        if integrity_blockers:
            payload["result"] = "BLOCK"
            payload["findings"] = list(dict.fromkeys(
                integrity_blockers + list(payload.get("findings") or [])
            ))
            payload["summary"] = (
                "Deterministic queue integrity validation found blocking issues. "
                + str(payload.get("summary") or "")
            ).strip()
        elif payload["result"] == "BLOCK":
            # The hosted audit is advisory even when job-approved thumbnails
            # are attached. A BLOCK without a reproducible local integrity failure must not
            # prevent the user from reaching the human-review screen.
            payload["result"] = "WARN"
            payload["findings"] = list(dict.fromkeys([
                "AI raised a qualitative concern without a deterministic queue-integrity failure; it remains advisory.",
                *list(payload.get("findings") or []),
            ]))
            payload["summary"] = (
                "AI reported a qualitative concern, but deterministic integrity checks passed. "
                + str(payload.get("summary") or "")
            ).strip()
        if local_findings:
            payload["findings"] = list(dict.fromkeys(local_findings + list(payload.get("findings") or [])))
            payload["recommended_actions"] = list(dict.fromkeys(
                local_actions + list(payload.get("recommended_actions") or [])
            ))
            if payload["result"] == "PASS":
                payload["result"] = "WARN"
            payload["summary"] = (
                "Deterministic edit-quality preflight found unresolved issues. "
                + str(payload.get("summary") or "")
            ).strip()
        payload.update({
            "schema_version": 1,
            "job_key": job["job_key"],
            "queue": queue_path.relative_to(root).as_posix(),
            "privacy": (
                f"Explicit per-job opt-in; {len(visual_evidence)} bounded review thumbnails sent; "
                "source video and audio were not sent"
                if visual_evidence else
                "JSON paths and report text only; no media sent"
            ),
            "visual_audit": {
                "enabled_for_this_job": bool(visual_evidence),
                "image_count": len(visual_evidence),
                "maximum_images": int(config["max_visual_images"]),
                "batch_offset": int(visual_batch_offset),
                "batch_limit": int(
                    visual_batch_limit or config.get("visual_batch_size", 12)
                ),
                "source_video_sent": False,
                "audio_sent": False,
                "requires_human_approval": True,
                "billing_mode": config["billing_mode"],
            },
            "authority": "ADVISORY_ONLY",
            "deterministic_gate": {
                "status": "BLOCK" if integrity_blockers else "PASS",
                "blockers": integrity_blockers,
            },
            "model": config["model"],
            "reasoning_effort": config["reasoning_effort"],
            "service_tier": config["service_tier"],
            "session_policy": "shared_ai_supervisor_thread",
            "_thread_id": active_thread_id,
        })
        return payload
    finally:
        client.close()
