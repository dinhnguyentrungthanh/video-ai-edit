"""Golden Set: human event labels on fixed source segments (docs/QUALITY_PLAN.md).

Labels are evaluation data, never edit decisions: nothing here touches review
queues, jobs, brand memory or source videos. ``annotations/`` is not tracked by
git, so every label change is written atomically, appended to an append-only
history and the label file is copied to ``backups/`` whenever a store opens.

Each set lives in its own directory (``annotations/golden/<set>``) with its own
manifest, labels, history, backups and lock. A later set (v1.1) only adds
segments: it pins the manifest it extends and copies its sources verbatim, and
``load_golden_sets`` combines sets read-only at scoring time.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1
GOLDEN_SET = "v1"
KNOWN_SETS = ("v1", "v1.1")
SET_PARENT = {"v1": None, "v1.1": "v1"}  # the set whose manifest a later set extends (pinned by SHA-256)
# Fields that identify a source file; a source shared by several sets must agree on all of them.
SOURCE_IDENTITY_FIELDS = ("sha256", "size_bytes", "duration_seconds", "width", "height", "fps", "content_style")
CATEGORIES = ("visual_logo", "text", "adult", "gore", "violence")
# Scoring groups equal the Dashboard detector groups; a watermark may be caught by OCR or logo.
CATEGORY_GROUP = {"visual_logo": "advertising", "text": "advertising",
                  "adult": "adult", "gore": "gore", "violence": "violence"}
GROUPS = ("advertising", "adult", "gore", "violence")
SAFETY_CATEGORIES = ("adult", "gore", "violence")
ACTIONS = ("BLUR", "CUT", "KEEP")
SEVERITIES = ("must_catch", "should_catch", "nice_to_have")
SPLITS = ("dev", "holdout")
SEGMENT_STATUSES = ("unlabeled", "in_progress", "complete")
REJECTED = "rejected"

# Read-only production jobs whose source files form Golden Set v1.
GOLDEN_V1_SOURCES = {
    "troy": {"job_id": 39, "marker": "Troy", "content_style": "live_action"},
    "conan20": {"job_id": 38, "marker": "Movie 20", "content_style": "animation"},
    "conan21": {"job_id": 37, "marker": "Movie 21", "content_style": "animation"},
}
# (id, source, start, end, split, purpose); ends past the source duration are clamped.
GOLDEN_V1_SEGMENTS = (
    ("T1", "troy", 0.0, 300.0, "dev", "Watermark XEMBZ; mở đầu 5–25 s; chữ tường thuật 51–88 s"),
    ("T2", "troy", 300.0, 600.0, "holdout", "18+ đã BLUR toàn khung 421–460 s; watermark"),
    ("T3", "troy", 2000.0, 2300.0, "holdout", "Đối chứng: kỳ vọng chỉ có watermark"),
    ("T4", "troy", 11400.0, 11763.0, "dev", "End credits, biến thể watermark, cuối phim"),
    ("C20A", "conan20", 0.0, 240.0, "dev", "Watermark phimmoi; mở đầu 5–10 s"),
    ("C20B", "conan20", 480.0, 800.0, "holdout", "Ca hồi quy đầu nhân vật 510 s; watermark đổi vị trí 775 s"),
    ("C20C", "conan20", 1300.0, 1600.0, "dev", "Logo nhỏ 1315 s; website banner 1490 s; chữ trong cảnh"),
    ("C20D", "conan20", 3300.0, 3600.0, "holdout", "Cụm máu me mức cao"),
    ("C20E", "conan20", 6400.0, 6690.0, "dev", "Kết phim, credits"),
    ("C21A", "conan21", 0.0, 240.0, "holdout", "Mở đầu 0–15 s từng chọn CUT; watermark"),
    ("C21B", "conan21", 1300.0, 1600.0, "dev", "Máu me 1436 s từng đổi CUT → KEEP"),
    ("C21C", "conan21", 4200.0, 4500.0, "dev", "Chữ Nhật trong cảnh từng bị báo là watermark"),
    ("C21D", "conan21", 6450.0, 6739.0, "holdout", "End-card 6715 s và 6730 s"),
)

# Task C2 proposal (2026-09-30), confirmed by the user on 2026-09-30: visible slashing/stabbing in
# live action counts as violence/gore to flag for review, so T7/T8 stay. Same sources as v1; no
# segment may overlap a v1 segment.
GOLDEN_V1_1_SEGMENTS = (
    ("T5", "troy", 855.0, 1110.0, "dev", "18+ thật: lộ ngực 937–951 s, cảnh giường 977–1014 s; hôn/vuốt ve còn quần áo 884–937 s không tính"),
    ("T6", "troy", 6780.0, 7020.0, "holdout", "18+ thật: cảnh giường 6800–6861 s (rev1 BLUR); 6870–6884 s cần quyết"),
    ("T7", "troy", 3256.0, 3563.0, "dev", "Trận bãi biển: máu 3259 s (gore sót), xác bị giáo đâm 3556–3562 s; 7 mục 18+ báo nhầm"),
    ("T8", "troy", 8600.0, 8800.0, "holdout", "Hector–Achilles: vết thương có máu 8746–8760, 8774–8781 s (gore sót)"),
    ("C21E", "conan21", 2150.0, 2400.0, "dev", "Hiện trường án mạng: vệt máu trên chiếu 2248–2264 s; mục xác/máu báo nhầm"),
    ("C20F", "conan20", 4525.0, 4765.0, "holdout", "Đánh tay đôi hoạt hình 4529–4672 s, gần như không máu; bẫy bạo lực/máu me"),
)
# While True, golden_prefill.py refuses to write annotations/golden/v1.1/segments.json (dry runs
# with --output still work). False since the user chose the violence rule (2026-09-30).
GOLDEN_V1_1_PENDING_DECISION = False
SET_SEGMENTS = {"v1": GOLDEN_V1_SEGMENTS, "v1.1": GOLDEN_V1_1_SEGMENTS}


class StaleRevision(ValueError):
    """The client edited an older label revision; it must reload first."""


class FilmLogoConflict(ValueError):
    """A film-long box would enlarge existing smaller boxes; the page asks before replacing them."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_sha256(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_region(value: Any) -> dict[str, int] | None:
    """Pixel box from a review item or label; anything else (None, "FULL_FRAME") is full frame."""
    if not isinstance(value, dict) or not all(k in value for k in ("x", "y", "width", "height")):
        return None
    box = {k: int(round(float(value[k]))) for k in ("x", "y", "width", "height")}
    return box if box["width"] > 0 and box["height"] > 0 else None


def validate_region(value: Any, width: int, height: int) -> dict[str, int] | None:
    if value is None:
        return None
    box = normalize_region(value)
    if box is None or box["x"] < 0 or box["y"] < 0:
        raise ValueError("Vùng phải có x, y ≥ 0 và rộng/cao > 0")
    if box["x"] + box["width"] > width or box["y"] + box["height"] > height:
        raise ValueError(f"Vùng vượt khung {width}×{height}")
    return box


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    name = manifest.get("golden_set")
    if manifest.get("schema_version") != SCHEMA_VERSION or name not in KNOWN_SETS:
        raise ValueError(f"Manifest không phải Golden Set đã biết (golden_set={name!r}; "
                         f"chỉ nhận {', '.join(KNOWN_SETS)})")
    parent = SET_PARENT[name]
    if manifest.get("extends") != parent:
        raise ValueError(f"Manifest Golden Set {name} phải " +
                         (f"mở rộng {parent}" if parent else "không mở rộng bộ nào") +
                         f" (extends={manifest.get('extends')!r})")
    if parent and not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("extends_manifest_sha256") or "")):
        raise ValueError(f"Manifest Golden Set {name} thiếu SHA-256 của manifest {parent} mà nó mở rộng")
    sources = manifest.get("sources") or {}
    seen: set[str] = set()
    for segment in manifest.get("segments") or []:
        source = sources.get(segment.get("source"))
        if source is None:
            raise ValueError(f"Đoạn {segment.get('id')} trỏ tới nguồn không có trong manifest")
        if segment["id"] in seen:
            raise ValueError(f"Trùng ID đoạn {segment['id']}")
        seen.add(segment["id"])
        if segment.get("split") not in SPLITS:
            raise ValueError(f"Đoạn {segment['id']} có split không hợp lệ")
        if not 0 <= segment["start_seconds"] < segment["end_seconds"] <= source["duration_seconds"] + 1e-6:
            raise ValueError(f"Đoạn {segment['id']} nằm ngoài thời lượng nguồn")
    if not seen:
        raise ValueError("Manifest không có đoạn nào")
    return manifest


def segments_by_id(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {segment["id"]: segment for segment in manifest["segments"]}


def file_sha256(path: Path, chunk_bytes: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_bytes), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_file(root: Path, manifest: dict[str, Any], key: str, full_hash: bool = False) -> Path:
    """Path of a manifest source after checking its size (and SHA-256 when ``full_hash``)."""
    source = manifest["sources"][key]
    path = (root / source["path"]).resolve(strict=True)
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Nguồn {key} nằm ngoài thư mục BiliFlow")
    if path.stat().st_size != source["size_bytes"]:
        raise ValueError(f"Nguồn {key} đã đổi kích thước; từ chối dùng")
    if full_hash and file_sha256(path) != source["sha256"]:
        raise ValueError(f"Nguồn {key} đã đổi nội dung (SHA-256 khác manifest); từ chối dùng")
    return path


def empty_labels(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION, "golden_set": manifest["golden_set"],
        "manifest_sha256": canonical_sha256(manifest), "revision": 0, "updated_at": now_iso(),
        "segments": {segment["id"]: {"status": "unlabeled", "completed_at": None}
                     for segment in manifest["segments"]},
        "events": [], "suggestion_resolutions": {},
    }


def is_present_keep(event: dict[str, Any]) -> bool:
    """A safety label whose content is really there and that the user keeps on export.

    The detector is right to flag it (review is wanted), so scoring treats it as a positive,
    never as a trap; only plain KEEP labels (no ``content_present``) are known traps.
    """
    return event.get("expected_action") == "KEEP" and event.get("content_present") is True


def validate_event(event: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    """Normalized label; ``content_present`` is kept only on KEEP safety labels (absent elsewhere).

    ``content_present`` must be a boolean when given. True is only meaningful for 18+/gore/violence:
    with KEEP it marks "really there, flag it for review, the user keeps it" (default severity
    should_catch); with BLUR/CUT it is implied and not stored. False (or null) means not given.
    """
    segment = segments_by_id(manifest).get(event.get("segment_id"))
    if segment is None:
        raise ValueError("Nhãn phải thuộc một đoạn trong manifest")
    source = manifest["sources"][segment["source"]]
    category = event.get("category")
    if category not in CATEGORIES:
        raise ValueError("Nhóm nội dung không hợp lệ")
    action = event.get("expected_action")
    if action not in ACTIONS:
        raise ValueError("Hành động phải là BLUR, CUT hoặc KEEP")
    start, end = float(event.get("start_seconds")), float(event.get("end_seconds"))
    if not 0 <= start < end <= source["duration_seconds"] + 1e-6:
        raise ValueError("Thời gian bắt đầu phải nhỏ hơn kết thúc và nằm trong video")
    if end <= segment["start_seconds"] or start >= segment["end_seconds"]:
        raise ValueError("Nhãn phải giao với đoạn đang gán")
    region = validate_region(event.get("region_source_pixels"), source["width"], source["height"])
    ambiguous = bool(event.get("ambiguous"))
    if action == "BLUR" and category in ("visual_logo", "text") and region is None and not ambiguous:
        raise ValueError("Logo/chữ cần BLUR phải có vùng")  # ambiguous labels are never scored for recall
    present = event.get("content_present")
    if present is not None and not isinstance(present, bool):
        raise ValueError("content_present phải là true hoặc false")
    if present and category not in SAFETY_CATEGORIES:
        raise ValueError("Chỉ nhãn 18+, máu me hoặc bạo lực mới đánh dấu 'có thật · giữ'")
    present_keep = bool(present) and action == "KEEP"  # BLUR/CUT already mean the content is there
    severity = event.get("severity")
    if present_keep:
        severity = severity if severity in SEVERITIES else "should_catch"
    elif action == "KEEP":
        severity = severity if severity in SEVERITIES else None
    elif severity not in SEVERITIES:
        raise ValueError("Mức độ phải là must_catch, should_catch hoặc nice_to_have")
    normalized = {
        "id": event.get("id"), "segment_id": segment["id"], "category": category,
        "start_seconds": round(start, 3), "end_seconds": round(end, 3),
        "region_source_pixels": region, "expected_action": action, "severity": severity,
        "ambiguous": ambiguous, "notes": str(event.get("notes") or "").strip(),
        "from_suggestion": event.get("from_suggestion") or None,
    }
    if present_keep:  # only stored when set, so earlier labels keep their exact shape
        normalized["content_present"] = True
    return normalized


# --- several sets scored together (read-only) -------------------------------------------------

def check_disjoint_segments(rows: Iterable[tuple[str, dict[str, Any]]]) -> None:
    """Refuse duplicate segment ids and segments overlapping in time on one source.

    ``rows`` are (set name, segment) pairs from one set or several; segments that only touch
    (one ends where the next starts) do not overlap.
    """
    owner: dict[str, str] = {}
    placed: list[tuple[str, dict[str, Any]]] = []
    for name, segment in rows:
        if segment["id"] in owner:
            raise ValueError(f"Trùng ID đoạn {segment['id']} ({owner[segment['id']]} và {name})")
        owner[segment["id"]] = name
        for other_name, other in placed:
            if other["source"] == segment["source"] and _overlap(
                    segment["start_seconds"], segment["end_seconds"], other["start_seconds"], other["end_seconds"]) > 0:
                raise ValueError(f"Đoạn {segment['id']} ({name}) chồng thời gian với đoạn {other['id']} "
                                 f"({other_name}) trên nguồn {segment['source']}")
        placed.append((name, segment))


def check_shared_sources(manifests: Iterable[tuple[str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """Sources of several manifests merged by key; a shared key must describe the same file."""
    merged: dict[str, dict[str, Any]] = {}
    owner: dict[str, str] = {}
    for name, manifest in manifests:
        for key, source in manifest["sources"].items():
            if key in merged:
                differ = [field for field in SOURCE_IDENTITY_FIELDS if merged[key].get(field) != source.get(field)]
                if differ:
                    raise ValueError(f"Nguồn {key} khác nhau giữa bộ {owner[key]} và {name} ({', '.join(differ)})")
                continue
            twin = next((k for k, s in merged.items() if s.get("sha256") == source.get("sha256")), None)
            if twin is not None:
                raise ValueError(f"Nguồn {key} ({name}) và {twin} ({owner[twin]}) là cùng một tệp dưới hai tên")
            merged[key], owner[key] = source, name
    return merged


def load_golden_sets(root: Path, names: Iterable[str], labels_paths: dict[str, Path] | None = None,
                     ) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    """Read one or more sets under ``root`` (annotations/golden) for scoring; never writes or locks.

    Returns (manifest, labels, parts). With one set, manifest and labels are that set's own
    documents, so its scorecard keeps the set's manifest pin. With several, the combined manifest
    has ``golden_set: "v1+v1.1"``, ``sets: {name: pin}`` and a ``set`` on every segment; the
    combined labels merge the segment states and events, with ``revision`` per set.
    ``parts[name]`` holds that set's own manifest, labels, pin and labels path. A set without an
    events.json yet (nothing labelled) is read as all segments unlabelled.

    Refuses unknown or repeated sets, labels pinned to another manifest, an extension whose pinned
    parent manifest changed, shared sources that differ, duplicate segment ids, segments that
    overlap on one source (within or across sets, parents included) and colliding event ids.
    """
    overrides = {name: Path(path) for name, path in (labels_paths or {}).items()}
    chosen = list(names)
    unknown = [name for name in chosen if name not in KNOWN_SETS]
    if unknown or not chosen:
        raise ValueError(f"Bộ nhãn không hợp lệ: {', '.join(map(str, unknown)) or 'chưa chọn bộ nào'}; "
                         f"chỉ nhận {', '.join(KNOWN_SETS)}")
    if len(set(chosen)) != len(chosen):
        raise ValueError("Một bộ nhãn được chọn hai lần")
    extra = sorted(set(overrides) - set(chosen))
    if extra:
        raise ValueError(f"Có tệp nhãn cho bộ không được chọn: {', '.join(extra)}")
    chosen.sort(key=KNOWN_SETS.index)
    manifests: dict[str, dict[str, Any]] = {}

    def manifest_of(name: str) -> dict[str, Any]:
        if name not in manifests:
            path = root / name / "segments.json"
            if not path.exists():
                raise ValueError(f"Chưa có manifest {path}")
            manifest = validate_manifest(read_json(path))
            if manifest["golden_set"] != name:
                raise ValueError(f"{path} là manifest của bộ {manifest['golden_set']}, không phải {name}")
            manifests[name] = manifest
        return manifests[name]

    parts: dict[str, dict[str, Any]] = {}
    for name in chosen:
        manifest = manifest_of(name)
        pin = canonical_sha256(manifest)
        path = overrides.get(name) or root / name / "events.json"
        missing = not path.exists()
        if missing and name in overrides:
            raise ValueError(f"Không có tệp nhãn {path}")
        labels = empty_labels(manifest) if missing else read_json(path)
        if labels.get("manifest_sha256") != pin:
            raise ValueError(f"{path} belongs to a different manifest (bộ {name}, pin {pin[:12]})")
        if labels.get("golden_set", name) != name:
            raise ValueError(f"{path} là nhãn của bộ {labels.get('golden_set')}, không phải {name}")
        own = {segment["id"] for segment in manifest["segments"]}
        stray = {str(event.get("segment_id")) for event in labels["events"] if event.get("segment_id") not in own}
        stray |= set(labels["segments"]) - own
        if stray:
            raise ValueError(f"{path} có nhãn hoặc đoạn không thuộc manifest {name}: {', '.join(sorted(stray))}")
        parts[name] = {"manifest": manifest, "labels": labels, "manifest_sha256": pin,
                       "labels_path": None if missing else path, "labels_missing": missing}
    involved = list(chosen)  # parents are checked even when not scored, so an extension never drifts
    for name in chosen:
        parent = SET_PARENT[name]
        if parent is None:
            continue
        pinned = manifests[name]["extends_manifest_sha256"]
        if canonical_sha256(manifest_of(parent)) != pinned:
            raise ValueError(f"Bộ {name} được tạo cho một manifest {parent} khác (pin {pinned[:12]}); "
                             "không chấm chung")
        if parent not in involved:
            involved.append(parent)
    involved.sort(key=KNOWN_SETS.index)
    sources = check_shared_sources((name, manifests[name]) for name in involved)
    check_disjoint_segments((name, segment) for name in involved for segment in manifests[name]["segments"])
    event_owner: dict[str, str] = {}
    for name in chosen:
        for event in parts[name]["labels"]["events"]:
            if event["id"] in event_owner:
                raise ValueError(f"Trùng ID nhãn {event['id']} ({event_owner[event['id']]} và {name})")
            event_owner[event["id"]] = name
    if len(chosen) == 1:
        return parts[chosen[0]]["manifest"], parts[chosen[0]]["labels"], parts
    used = {key for name in chosen for key in manifests[name]["sources"]}
    manifest = {"schema_version": SCHEMA_VERSION, "golden_set": "+".join(chosen),
                "sets": {name: parts[name]["manifest_sha256"] for name in chosen},
                "sources": {key: dict(source) for key, source in sources.items() if key in used},
                "segments": [dict(segment, set=name) for name in chosen for segment in manifests[name]["segments"]]}
    labels = {"schema_version": SCHEMA_VERSION, "golden_set": manifest["golden_set"],
              "manifest_sha256": canonical_sha256(manifest),
              "revision": {name: parts[name]["labels"].get("revision") for name in chosen},
              "segments": {key: value for name in chosen for key, value in parts[name]["labels"]["segments"].items()},
              "events": [event for name in chosen for event in parts[name]["labels"]["events"]],
              "suggestion_resolutions": {key: value for name in chosen for key, value in
                                         (parts[name]["labels"].get("suggestion_resolutions") or {}).items()}}
    return manifest, labels, parts


class LabelStore:
    """Single-writer label file with revision checks, atomic writes and history.

    Each change is built on a copy of the document, written atomically, and only
    then becomes the in-memory state, so a failed write never shows up as saved.
    An exclusive lock file keeps a second labeling server from writing the same
    events.json; call close() (or use ``with``) to release it.
    """

    def __init__(self, directory: Path, manifest: dict[str, Any],
                 suggestions: Iterable[dict[str, Any]] = (), actor: str = "user"):
        self.directory = directory
        self.manifest = validate_manifest(manifest)
        self.path = directory / "events.json"
        self.history_path = directory / "label-history.jsonl"
        self.actor = actor
        self.lock = threading.Lock()
        self.suggestions = {s["id"]: s for s in suggestions}
        directory.mkdir(parents=True, exist_ok=True)
        self._lock_handle = _acquire_file_lock(directory / "events.lock")
        try:
            if self.path.exists():
                self.doc = read_json(self.path)
                if self.doc.get("manifest_sha256") != canonical_sha256(manifest):
                    raise ValueError("events.json được tạo cho manifest khác; không mở để tránh lệch nhãn")
                backup = directory / "backups" / f"events-{datetime.now():%Y%m%d-%H%M%S-%f}.json"
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.path, backup)
            else:
                self.doc = empty_labels(manifest)
                write_json_atomic(self.path, self.doc)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self._lock_handle is not None:
            _release_file_lock(self._lock_handle)
            self._lock_handle = None

    def __enter__(self) -> "LabelStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def state(self) -> dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.doc))

    def _draft(self, expected_revision: int) -> dict[str, Any]:
        if int(expected_revision) != self.doc["revision"]:
            raise StaleRevision("Nhãn đã thay đổi ở nơi khác; tải lại trang rồi thử lại")
        return json.loads(json.dumps(self.doc))

    def _commit(self, doc: dict[str, Any], action: str, before: Any, after: Any) -> None:
        doc["revision"] += 1
        doc["updated_at"] = now_iso()
        write_json_atomic(self.path, doc)
        self.doc = doc  # disk and memory agree from here on
        entry = {"at": doc["updated_at"], "actor": self.actor, "action": action,
                 "revision": doc["revision"], "before": before, "after": after}
        try:
            with self.history_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as error:
            raise RuntimeError(f"Đã lưu nhãn (revision {doc['revision']}) nhưng không ghi được lịch sử: {error}") from error

    @staticmethod
    def _next_id(doc: dict[str, Any], segment_id: str) -> str:
        """First free id; ids of deleted labels are never handed out again (history stays unambiguous)."""
        used = {event["id"] for event in doc["events"]} | set(doc.get("retired_event_ids", ()))
        number = 1
        while f"gs-{segment_id}-{number:04d}" in used:
            number += 1
        return f"gs-{segment_id}-{number:04d}"

    @staticmethod
    def _require_editable(doc: dict[str, Any], segment_id: str) -> None:
        if doc["segments"][segment_id]["status"] == "complete":
            raise ValueError("Đoạn đã đánh dấu xem hết; mở lại đoạn trước khi sửa")

    def upsert_event(self, payload: dict[str, Any], expected_revision: int) -> dict[str, Any]:
        with self.lock:
            doc = self._draft(expected_revision)
            event = validate_event(payload, self.manifest)
            self._require_editable(doc, event["segment_id"])
            events = doc["events"]
            index = next((i for i, e in enumerate(events) if e["id"] == event["id"]), None)
            before = events[index] if index is not None else None
            if before is not None and before["segment_id"] != event["segment_id"]:
                raise ValueError("Không đổi đoạn của nhãn đã có")
            suggestion = event["from_suggestion"]
            linked = suggestion is not None and (before is None or before.get("from_suggestion") != suggestion)
            if linked:  # a new link: the suggestion must exist and must not already back another label
                if suggestion not in self.suggestions:
                    raise ValueError("Gợi ý không tồn tại")
                owner = (doc["suggestion_resolutions"].get(suggestion) or {}).get("event_id")
                if owner and owner != event["id"]:
                    raise ValueError(f"Gợi ý này đã thành nhãn {owner}; sửa nhãn đó thay vì tạo nhãn mới")
            stamp = now_iso()
            event["id"] = event["id"] if before is not None else self._next_id(doc, event["segment_id"])
            event["labeled_by"] = before["labeled_by"] if before else self.actor
            event["labeled_at"] = before["labeled_at"] if before else stamp
            event["updated_at"] = stamp
            if index is None:
                events.append(event)
            else:
                events[index] = event
            if before is not None and before.get("from_suggestion") not in (None, suggestion):
                doc["suggestion_resolutions"].pop(before["from_suggestion"], None)  # old link released
            if suggestion is not None and suggestion in self.suggestions:
                doc["suggestion_resolutions"][suggestion] = {
                    "resolution": "ambiguous" if event["ambiguous"] else "accepted",
                    "event_id": event["id"], "segment_id": event["segment_id"], "resolved_at": stamp,
                    "suggestion": self.suggestions[suggestion]}
            doc["segments"][event["segment_id"]]["status"] = "in_progress"
            self._commit(doc, "upsert_event", before, event)
            return event

    def delete_event(self, event_id: str, expected_revision: int) -> None:
        with self.lock:
            doc = self._draft(expected_revision)
            events = doc["events"]
            index = next((i for i, e in enumerate(events) if e["id"] == event_id), None)
            if index is None:
                raise KeyError(event_id)
            before = events[index]
            self._require_editable(doc, before["segment_id"])
            del events[index]
            doc.setdefault("retired_event_ids", []).append(event_id)
            resolutions = doc["suggestion_resolutions"]
            for key in [k for k, v in resolutions.items() if v.get("event_id") == event_id]:
                del resolutions[key]  # suggestion becomes unresolved again
            self._commit(doc, "delete_event", before, None)

    def resolve_suggestion(self, suggestion_id: str, resolution: str | None, expected_revision: int) -> None:
        """Reject a suggestion, or clear a rejection or a "covered" mark (None; e.g. easy-mode undo).

        Accepting goes through upsert_event.
        """
        with self.lock:
            doc = self._draft(expected_revision)
            suggestion = self.suggestions.get(suggestion_id)
            if suggestion is None:
                raise KeyError(suggestion_id)
            self._require_editable(doc, suggestion["segment_id"])
            resolutions = doc["suggestion_resolutions"]
            before = resolutions.get(suggestion_id)
            covered_only = before is not None and before.get("resolution") == "covered" and resolution is None
            if before is not None and before.get("event_id") and not covered_only:
                raise ValueError("Gợi ý đã thành nhãn; xóa nhãn đó nếu muốn đổi")
            if resolution == REJECTED:
                after = {"resolution": REJECTED, "event_id": None, "segment_id": suggestion["segment_id"],
                         "resolved_at": now_iso(), "suggestion": suggestion}
                resolutions[suggestion_id] = after
            elif resolution is None:
                resolutions.pop(suggestion_id, None)
                after = None
            else:
                raise ValueError("Chỉ có thể đánh dấu gợi ý là sai hoặc bỏ đánh dấu")
            doc["segments"][suggestion["segment_id"]]["status"] = "in_progress"
            self._commit(doc, "resolve_suggestion", before, after)

    def cover_suggestion(self, suggestion_id: str, event_id: str, expected_revision: int) -> None:
        """Mark a suggestion as handled by an existing label (e.g. the film-long watermark), without a new label.

        Like the watermark confirmation, deleting that label reopens the suggestion.
        """
        with self.lock:
            doc = self._draft(expected_revision)
            suggestion = self.suggestions.get(suggestion_id)
            if suggestion is None:
                raise KeyError(suggestion_id)
            self._require_editable(doc, suggestion["segment_id"])
            before = doc["suggestion_resolutions"].get(suggestion_id)
            if before is not None and before.get("event_id") and before.get("resolution") != "covered":
                raise ValueError("Gợi ý đã thành nhãn; xóa nhãn đó nếu muốn đổi")
            event = next((e for e in doc["events"] if e["id"] == event_id), None)
            if event is None or event["segment_id"] != suggestion["segment_id"]:
                raise ValueError("Nhãn che phủ phải nằm trong cùng đoạn")
            if event["expected_action"] not in ("BLUR", "CUT") or event.get("ambiguous") or \
                    CATEGORY_GROUP[event["category"]] != suggestion["group"]:
                raise ValueError("Nhãn che phủ phải là nhãn làm mờ/cắt chắc chắn cùng nhóm")
            if _overlap(event["start_seconds"], event["end_seconds"],
                        suggestion["start_seconds"], suggestion["end_seconds"]) <= 0:
                raise ValueError("Nhãn che phủ không trùng thời gian với gợi ý")
            after = {"resolution": "covered", "event_id": event_id, "segment_id": suggestion["segment_id"],
                     "resolved_at": now_iso(), "suggestion": suggestion}
            doc["suggestion_resolutions"][suggestion_id] = after
            doc["segments"][suggestion["segment_id"]]["status"] = "in_progress"
            self._commit(doc, "cover_suggestion", before, after)

    def reject_remaining(self, segment_id: str, advisory_only: bool, expected_revision: int) -> int:
        """Mark every still-unresolved suggestion of a segment as wrong (after watching it)."""
        with self.lock:
            doc = self._draft(expected_revision)
            if segment_id not in doc["segments"]:
                raise ValueError("Đoạn không hợp lệ")
            self._require_editable(doc, segment_id)
            chosen = [key for key in self._unresolved(doc, segment_id)
                      if not advisory_only or self.suggestions[key].get("advisory")]
            if not chosen:
                return 0
            stamp = now_iso()
            for key in chosen:
                doc["suggestion_resolutions"][key] = {
                    "resolution": REJECTED, "event_id": None, "segment_id": segment_id, "resolved_at": stamp,
                    "suggestion": self.suggestions[key], "bulk": True}
            doc["segments"][segment_id]["status"] = "in_progress"
            self._commit(doc, "reject_remaining", None,
                         {"segment_id": segment_id, "advisory_only": advisory_only, "suggestions": chosen})
            return len(chosen)

    def confirm_watermark(self, candidate: dict[str, Any], expected_revision: int,
                          note: str = "Watermark xác nhận một lần cho cả phim (chế độ dễ)",
                          decision: str = "confirmed") -> list[dict[str, Any]]:
        """Easy mode: one confirmation labels a film-long watermark in every open segment of that film.

        Each created event is a normal BLUR label clipped to its segment; the watermark
        suggestions of that segment are marked "covered" by it (deleting the event reopens them).
        Completed segments are left untouched.
        """
        with self.lock:
            doc = self._draft(expected_revision)
            decisions = doc.setdefault("watermark_decisions", {})
            if candidate["id"] in decisions:
                raise ValueError("Watermark này đã được trả lời")
            stamp = now_iso()
            created = []
            for segment in self.manifest["segments"]:
                if segment["source"] != candidate["source"] or doc["segments"][segment["id"]]["status"] == "complete":
                    continue
                start = max(float(candidate["start_seconds"]), segment["start_seconds"])
                end = min(float(candidate["end_seconds"]), segment["end_seconds"])
                if end <= start or self._has_watermark_label(doc, segment["id"], candidate, start, end):
                    continue
                event = validate_event({
                    "segment_id": segment["id"], "category": "visual_logo", "start_seconds": start, "end_seconds": end,
                    "region_source_pixels": candidate["region_source_pixels"], "expected_action": "BLUR",
                    "severity": "must_catch", "notes": note,
                }, self.manifest)
                event.update(id=self._next_id(doc, segment["id"]), labeled_by=self.actor, labeled_at=stamp,
                             updated_at=stamp, watermark_candidate=candidate["id"])
                doc["events"].append(event)
                created.append(event)
                for key in candidate["suggestion_ids"]:
                    member = self.suggestions.get(key)
                    if member and member["segment_id"] == segment["id"] and key not in doc["suggestion_resolutions"]:
                        doc["suggestion_resolutions"][key] = {
                            "resolution": "covered", "event_id": event["id"], "segment_id": segment["id"],
                            "resolved_at": stamp, "suggestion": member}
                doc["segments"][segment["id"]]["status"] = "in_progress"
            decisions[candidate["id"]] = {"decision": decision, "at": stamp,
                                          "region_source_pixels": candidate["region_source_pixels"],
                                          "events": [e["id"] for e in created]}
            self._commit(doc, "confirm_watermark", None, {"candidate": candidate["id"], "events": created})
            return created

    @staticmethod
    def _has_watermark_label(doc: dict[str, Any], segment_id: str, candidate: dict[str, Any],
                             start: float, end: float) -> bool:
        """An existing advertising BLUR label on the same box that covers most of the span (no duplicates)."""
        for event in doc["events"]:
            if event["segment_id"] != segment_id or CATEGORY_GROUP[event["category"]] != "advertising":
                continue
            if event["expected_action"] != "BLUR" or event.get("ambiguous") or not event.get("region_source_pixels"):
                continue
            if not regions_compatible(event["region_source_pixels"], candidate["region_source_pixels"]):
                continue
            if _overlap(event["start_seconds"], event["end_seconds"], start, end) >= 0.8 * (end - start):
                return True
        return False

    def add_film_logo(self, source: str, region: Any, start: float, end: float, expected_revision: int,
                      category: str = "visual_logo", replace: bool = False) -> list[dict[str, Any]]:
        """Easy mode: a film-long logo/ad text boxed once, labelled in every open segment it spans.

        Per segment: an existing advertising BLUR label whose box already hides the new box is kept
        (nothing to add); a smaller existing box inside the new box is a region correction and is only
        enlarged when ``replace`` is true, otherwise FilmLogoConflict("enlarge") is raised so the page
        can ask; anything else gets a new label. Nothing is written when nothing would change.
        """
        if source not in self.manifest["sources"]:
            raise ValueError("Phim không có trong bộ nhãn")
        if category not in ("visual_logo", "text"):
            raise ValueError("Loại phải là logo hoặc chữ quảng cáo")
        info = self.manifest["sources"][source]
        box = validate_region(region, info["width"], info["height"])
        if box is None or box["width"] < 4 or box["height"] < 4:
            raise ValueError("Hãy khoanh vùng logo trên video")
        start, end = max(0.0, float(start)), min(float(end), info["duration_seconds"])
        if end - start < 1.0:
            raise ValueError("Khoảng thời gian của logo quá ngắn")
        with self.lock:
            doc = self._draft(expected_revision)
            stamp = now_iso()
            created, enlarged, conflicts = [], [], []
            note = ("Chữ quảng cáo" if category == "text" else "Logo") + " suốt phim do người dùng thêm"
            for segment in self.manifest["segments"]:
                if segment["source"] != source or doc["segments"][segment["id"]]["status"] == "complete":
                    continue
                s0, s1 = max(start, segment["start_seconds"]), min(end, segment["end_seconds"])
                if s1 <= s0:
                    continue
                covering, inside = False, []
                for event in doc["events"]:
                    if (event["segment_id"] != segment["id"] or CATEGORY_GROUP[event["category"]] != "advertising"
                            or event["expected_action"] != "BLUR" or event.get("ambiguous")
                            or not event.get("region_source_pixels")
                            or _overlap(event["start_seconds"], event["end_seconds"], s0, s1) < 0.8 * (s1 - s0)):
                        continue
                    old = event["region_source_pixels"]
                    shared = box_intersection(old, box)
                    if shared >= 0.85 * box_area(box):
                        covering = True
                    elif shared >= 0.85 * box_area(old):
                        inside.append(event)
                if covering:
                    continue
                if inside:
                    if not replace:
                        conflicts.append(segment["id"])
                        continue
                    for event in inside:
                        before = dict(event)
                        event.update(region_source_pixels=box, category=category, updated_at=stamp)
                        enlarged.append({"before": before, "after": dict(event)})
                    continue
                event = validate_event({
                    "segment_id": segment["id"], "category": category, "start_seconds": s0, "end_seconds": s1,
                    "region_source_pixels": box, "expected_action": "BLUR", "severity": "must_catch", "notes": note,
                }, self.manifest)
                event.update(id=self._next_id(doc, segment["id"]), labeled_by=self.actor, labeled_at=stamp,
                             updated_at=stamp)
                doc["events"].append(event)
                doc["segments"][segment["id"]]["status"] = "in_progress"
                created.append(event)
            if conflicts and not created:
                raise FilmLogoConflict(
                    "enlarge", f"Các đoạn {', '.join(conflicts)} đã có khung (xanh) nhỏ hơn nằm trong vùng bạn khoanh")
            if not created and not enlarged:
                raise ValueError("Vùng này đã được đánh dấu (khung xanh) ở mọi đoạn trong khoảng đã chọn — không cần thêm")
            decisions = doc.setdefault("watermark_decisions", {})
            key = f"user-{len(decisions) + 1:03d}-" + canonical_sha256([source, box, start, end])[:8]
            decisions[key] = {"decision": "user_added", "at": stamp, "region_source_pixels": box,
                              "category": category, "events": [e["id"] for e in created],
                              "enlarged": [x["after"]["id"] for x in enlarged]}
            self._commit(doc, "add_film_logo", None, {"id": key, "created": created, "enlarged": enlarged,
                                                      "skipped_conflicts": conflicts})
            return created + [x["after"] for x in enlarged]

    def reset_segment(self, segment_id: str, expected_revision: int) -> int:
        """Easy mode "redo this segment": drop its answers but keep film-long logo/watermark labels.

        Removed labels and resolutions stay in label-history.jsonl, so the redo is recoverable.
        """
        with self.lock:
            doc = self._draft(expected_revision)
            if segment_id not in doc["segments"]:
                raise ValueError("Đoạn không hợp lệ")
            segment = segments_by_id(self.manifest)[segment_id]
            span = segment["end_seconds"] - segment["start_seconds"]

            def film_long(e: dict[str, Any]) -> bool:  # a logo/watermark label covering the whole segment
                return (CATEGORY_GROUP[e["category"]] == "advertising" and e["expected_action"] == "BLUR"
                        and not e.get("ambiguous") and bool(e.get("region_source_pixels"))
                        and _overlap(e["start_seconds"], e["end_seconds"], segment["start_seconds"],
                                     segment["end_seconds"]) >= 0.95 * span)

            keep_ids = {e["id"] for e in doc["events"] if e["segment_id"] == segment_id
                        and (e.get("watermark_candidate") or "suốt phim" in str(e.get("notes") or "")
                             or "cả phim" in str(e.get("notes") or "") or film_long(e))}
            removed = [e for e in doc["events"] if e["segment_id"] == segment_id and e["id"] not in keep_ids]
            doc["events"] = [e for e in doc["events"] if not (e["segment_id"] == segment_id and e["id"] not in keep_ids)]
            doc.setdefault("retired_event_ids", []).extend(e["id"] for e in removed)
            cleared = {k: v for k, v in doc["suggestion_resolutions"].items()
                       if v.get("segment_id") == segment_id and v.get("resolution") != "covered"}
            for key in cleared:
                del doc["suggestion_resolutions"][key]
            doc["segments"][segment_id] = {"status": "in_progress", "completed_at": None}
            self._commit(doc, "reset_segment", {"segment_id": segment_id, "events": removed, "resolutions": cleared},
                         {"segment_id": segment_id, "kept": sorted(keep_ids)})
            return len(removed)

    def reopen_suggestions(self, suggestion_ids: Iterable[str], expected_revision: int) -> dict[str, Any]:
        """Ask answered questions again, e.g. a safety question answered "Sai" only to keep the scene.

        For each suggestion the label created from it (``from_suggestion``) is deleted and its id
        retired, and its resolution is cleared; questions covered by a deleted label reopen as well,
        as with delete_event. Affected segments, complete ones included, become in_progress, and the
        ids are listed in ``reopened_suggestions`` so the easy page asks them even when it would
        otherwise hide them (low-value advisories). Refused as a whole, writing nothing, when an id
        is unknown or stale, is still unanswered, or was answered by a film-long watermark/logo
        label ("covered": delete that label instead). One history entry keeps everything removed.
        """
        ids = list(dict.fromkeys(str(key) for key in suggestion_ids))
        if not ids:
            raise ValueError("Chưa chọn câu hỏi nào để hỏi lại")
        with self.lock:
            doc = self._draft(expected_revision)
            resolutions = doc["suggestion_resolutions"]
            for key in ids:
                suggestion = self.suggestions.get(key)
                if suggestion is None:
                    raise KeyError(key)
                if suggestion.get("stale"):
                    raise ValueError(f"Gợi ý {key} không còn trong lượt quét hiện tại; không hỏi lại được")
                resolution = resolutions.get(key)
                if resolution is None and not any(e.get("from_suggestion") == key for e in doc["events"]):
                    raise ValueError(f"Câu hỏi {key} đang chờ trả lời; không cần mở lại")
                if resolution is not None and resolution.get("resolution") == "covered":
                    raise ValueError(f"Câu hỏi {key} được trả lời bằng nhãn {resolution.get('event_id')} "
                                     "(watermark/logo suốt phim); xóa nhãn đó nếu muốn hỏi lại")
            chosen = set(ids)
            removed = [e for e in doc["events"] if e.get("from_suggestion") in chosen]
            removed_ids = {e["id"] for e in removed}
            doc["events"] = [e for e in doc["events"] if e["id"] not in removed_ids]
            doc.setdefault("retired_event_ids", []).extend(e["id"] for e in removed)
            cleared = {key: value for key, value in resolutions.items()
                       if key in chosen or value.get("event_id") in removed_ids}
            for key in cleared:
                del resolutions[key]
            touched = ({self.suggestions[key]["segment_id"] for key in ids} | {e["segment_id"] for e in removed}
                       | {v["segment_id"] for v in cleared.values() if v.get("segment_id") in doc["segments"]})
            before_status = {sid: dict(doc["segments"][sid]) for sid in sorted(touched)}
            after_status = {sid: {"status": "in_progress", "completed_at": None} for sid in sorted(touched)}
            doc["segments"].update(json.loads(json.dumps(after_status)))
            listed = doc.setdefault("reopened_suggestions", [])
            listed.extend(key for key in ids if key not in listed)
            result = {"suggestions": ids, "deleted_events": [e["id"] for e in removed],
                      "cleared": sorted(cleared), "segments": sorted(touched)}
            self._commit(doc, "reopen_suggestions",
                         {"events": removed, "resolutions": cleared, "segments": before_status},
                         dict(result, segments=after_status))
            return result

    def decline_watermark(self, candidate_id: str, expected_revision: int) -> None:
        """Easy mode: not a watermark; its suggestions are then asked one by one."""
        with self.lock:
            doc = self._draft(expected_revision)
            decisions = doc.setdefault("watermark_decisions", {})
            if candidate_id in decisions:
                raise ValueError("Watermark này đã được trả lời")
            decisions[candidate_id] = {"decision": "declined", "at": now_iso()}
            self._commit(doc, "decline_watermark", None, {"candidate": candidate_id})

    def _unresolved(self, doc: dict[str, Any], segment_id: str) -> list[str]:
        resolved = doc["suggestion_resolutions"]
        return [key for key, s in self.suggestions.items()
                if s["segment_id"] == segment_id and key not in resolved]

    def unresolved(self, segment_id: str) -> list[str]:
        with self.lock:
            return self._unresolved(self.doc, segment_id)

    def set_segment_status(self, segment_id: str, status: str, expected_revision: int) -> None:
        with self.lock:
            doc = self._draft(expected_revision)
            if segment_id not in doc["segments"] or status not in SEGMENT_STATUSES:
                raise ValueError("Đoạn hoặc trạng thái không hợp lệ")
            pending = self._unresolved(doc, segment_id)
            if status == "complete" and pending:
                raise ValueError(f"Còn {len(pending)} gợi ý chưa xử lý trong đoạn")
            before = dict(doc["segments"][segment_id])
            after = {"status": status, "completed_at": now_iso() if status == "complete" else None}
            doc["segments"][segment_id] = after
            self._commit(doc, "set_segment_status", {segment_id: before}, {segment_id: after})


def _acquire_file_lock(path: Path):
    handle = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise ValueError("Bộ nhãn đang được mở ở cửa sổ/tiến trình khác; đóng nó trước") from None
    return handle


def _release_file_lock(handle) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


# --- review items -> labeling suggestions -------------------------------------------------

def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def box_intersection(a: dict[str, int], b: dict[str, int]) -> int:
    width = min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"])
    height = min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"])
    return max(0, width) * max(0, height)


def box_area(box: dict[str, int]) -> int:
    return box["width"] * box["height"]


def regions_compatible(a: dict[str, int] | None, b: dict[str, int] | None, minimum: float = 0.30) -> bool:
    """A full-frame side (None) is compatible with anything; boxes must share ``minimum`` of the smaller."""
    if a is None or b is None:
        return True
    return box_intersection(a, b) >= minimum * min(box_area(a), box_area(b))


def queue_suggestions(queue: dict[str, Any], manifest: dict[str, Any], origin: str,
                      tier: str = "review") -> list[dict[str, Any]]:
    """Clip one review queue's main and advisory items to the manifest segments of its source."""
    source_key = next((key for key, s in manifest["sources"].items()
                       if s["sha256"] == (queue.get("source") or {}).get("sha256")), None)
    if source_key is None:
        return []
    rows = [(item, False) for item in queue.get("items") or []]
    rows += [(item, True) for item in queue.get("advisory_items") or []]
    output = []
    for segment in manifest["segments"]:
        if segment["source"] != source_key:
            continue
        for item, advisory in rows:
            category = item.get("category")
            if category not in CATEGORY_GROUP:
                continue
            start, end = float(item["start_seconds"]), float(item["end_seconds"])
            if _overlap(start, end, segment["start_seconds"], segment["end_seconds"]) <= 0:
                continue
            decision = item.get("decision")
            prior = []
            if decision in ("KEEP", "BLUR", "CUT", "NEEDS_MORE_CONTEXT"):
                prior.append({"decision": decision, "origin": origin, "item_id": item.get("id"),
                              "region_source_pixels": normalize_region(item.get("decision_region_source_pixels")),
                              "decided_at": item.get("decided_at")})
            output.append({
                "segment_id": segment["id"], "category": category, "group": CATEGORY_GROUP[category],
                "start_seconds": round(max(start, segment["start_seconds"]), 3),
                "end_seconds": round(min(end, segment["end_seconds"]), 3),
                "item_start_seconds": round(start, 3), "item_end_seconds": round(end, 3),
                "region_source_pixels": normalize_region(item.get("suggested_region_source_pixels")),
                "suggested_action": item.get("suggested_decision"), "advisory": advisory,
                "priority": item.get("priority"), "candidate_type": item.get("candidate_type"),
                "labels": [str(x) for x in (item.get("labels") or [])[:5]], "tier": tier,
                "origins": [origin], "prior_decisions": prior,
            })
    return output


def carry_forward_resolved(merged: list[dict[str, Any]], resolutions: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep every suggestion that labels already handled, even if new inputs no longer produce it.

    The stored snapshot is re-added under its old id and marked stale, so events that
    reference it stay editable and completed segments stay complete.
    """
    known = {row["id"] for row in merged}
    stale = []
    for key, resolution in sorted(resolutions.items()):
        snapshot = resolution.get("suggestion")
        if key not in known and isinstance(snapshot, dict) and snapshot.get("id") == key:
            stale.append(dict(snapshot, stale=True))
    return merged + stale


def merge_suggestions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Greedy, deterministic merge of duplicates: same segment and group, ≥50% time and box overlap.

    Tiers are never merged, so adding a dense scan later keeps review-suggestion ids stable.
    """
    ordered = sorted(rows, key=lambda r: (r["segment_id"], r["tier"], r["group"], r["start_seconds"],
                                          -(r["end_seconds"] - r["start_seconds"]), r["category"],
                                          json.dumps(r["region_source_pixels"], sort_keys=True),
                                          r["origins"][0]))
    clusters: list[dict[str, Any]] = []
    for row in ordered:
        for cluster in clusters:
            if (cluster["segment_id"], cluster["tier"], cluster["group"]) != (row["segment_id"], row["tier"], row["group"]):
                continue
            shorter = min(cluster["end_seconds"] - cluster["start_seconds"], row["end_seconds"] - row["start_seconds"])
            shared = _overlap(cluster["start_seconds"], cluster["end_seconds"], row["start_seconds"], row["end_seconds"])
            a, b = cluster["region_source_pixels"], row["region_source_pixels"]
            same_region = (a is None and b is None) or (a is not None and b is not None and regions_compatible(a, b, 0.5))
            if shorter > 0 and shared >= 0.5 * shorter and same_region:
                cluster["merged_count"] += 1
                cluster["origins"] = sorted(set(cluster["origins"]) | set(row["origins"]))
                cluster["prior_decisions"] += row["prior_decisions"]
                cluster["advisory"] = cluster["advisory"] and row["advisory"]
                break
        else:
            clusters.append(dict(row, merged_count=1))
    for cluster in clusters:
        key = [cluster[k] for k in ("segment_id", "tier", "group", "category", "start_seconds", "end_seconds")]
        cluster["id"] = "sug-" + canonical_sha256(key + [cluster["region_source_pixels"]])[:12]
        latest = sorted(cluster["prior_decisions"], key=lambda p: str(p.get("decided_at") or ""))
        cluster["prior_decision"] = latest[-1]["decision"] if latest else None
    return clusters


# --- easy labeling mode ---------------------------------------------------------------------

WATERMARK_MIN_SECONDS = 60.0     # a persistent overlay shorter than this is asked per segment
WATERMARK_SAME_BOX_SHARE = 0.80  # boxes are one watermark when they share 80% of the smaller box


def _is_watermark_hint(suggestion: dict[str, Any]) -> bool:
    if suggestion["group"] != "advertising" or not suggestion.get("region_source_pixels"):
        return False
    return suggestion.get("candidate_type") in ("persistent_overlay", "REVIEW_PERSISTENT_OVERLAY") or any(
        "Persistent external logo" in str(label) for label in suggestion.get("labels") or ())


def watermark_candidates(manifest: dict[str, Any], suggestions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Film-long watermarks proposed by the detectors, grouped per source and box, longest first."""
    source_of = {segment["id"]: segment["source"] for segment in manifest["segments"]}
    rows = []
    for suggestion in suggestions:
        if not _is_watermark_hint(suggestion):
            continue
        start = float(suggestion.get("item_start_seconds", suggestion["start_seconds"]))
        end = float(suggestion.get("item_end_seconds", suggestion["end_seconds"]))
        rows.append((source_of[suggestion["segment_id"]], end - start, start, end, suggestion))
    clusters: list[dict[str, Any]] = []
    for source, _, start, end, suggestion in sorted(rows, key=lambda r: (r[0], -r[1], r[2], r[4]["id"])):
        box = suggestion["region_source_pixels"]
        for cluster in clusters:
            if cluster["source"] == source and regions_compatible(cluster["_box"], box, WATERMARK_SAME_BOX_SHARE):
                cluster["start_seconds"] = min(cluster["start_seconds"], start)
                cluster["end_seconds"] = max(cluster["end_seconds"], end)
                cluster["suggestion_ids"].append(suggestion["id"])
                cluster["_members"].append(suggestion)
                break
        else:
            clusters.append({"source": source, "start_seconds": start, "end_seconds": end, "_box": box,
                             "suggestion_ids": [suggestion["id"]], "_members": [suggestion]})
    output = []
    for cluster in clusters:
        if cluster["end_seconds"] - cluster["start_seconds"] < WATERMARK_MIN_SECONDS:
            continue
        decided = sorted((p for m in cluster["_members"] for p in m.get("prior_decisions") or ()
                          if p.get("decision") == "BLUR" and p.get("region_source_pixels")),
                         key=lambda p: str(p.get("decided_at") or ""))
        box = decided[-1]["region_source_pixels"] if decided else cluster["_box"]  # the user's latest box first
        labels = sorted({str(label) for m in cluster["_members"] for label in m.get("labels") or ()
                         if "Persistent" not in str(label) and "candidate" not in str(label)})
        output.append({"id": "wm-" + canonical_sha256([cluster["source"], cluster["_box"]])[:10],
                       "source": cluster["source"], "region_source_pixels": box,
                       "start_seconds": round(cluster["start_seconds"], 3), "end_seconds": round(cluster["end_seconds"], 3),
                       "suggestion_ids": sorted(cluster["suggestion_ids"]), "labels": labels[:4],
                       "prior_blur": bool(decided)})
    return sorted(output, key=lambda c: (c["source"], c["start_seconds"], c["id"]))


def easy_cards(suggestions: Iterable[dict[str, Any]], doc: dict[str, Any],
               candidates: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Suggestions worth a question per segment: primary items, or advisories the user once blurred/cut.

    Members of a pending or confirmed watermark candidate are handled in step 1; members of a
    declined candidate are asked individually. Low-value advisories stay hidden and are marked
    wrong when the segment is finished. Questions reopened with LabelStore.reopen_suggestions are
    always asked again.
    """
    decisions = doc.get("watermark_decisions") or {}
    in_step_one = {key for c in candidates if (decisions.get(c["id"]) or {}).get("decision") != "declined"
                   for key in c["suggestion_ids"]}
    reopened = set(doc.get("reopened_suggestions") or ())
    cards: dict[str, list[str]] = {}
    for suggestion in sorted(suggestions, key=lambda s: (s["segment_id"], s["start_seconds"], s["id"])):
        worth = not suggestion.get("advisory") or suggestion.get("prior_decision") in ("BLUR", "CUT")
        asked = suggestion["id"] in reopened or (worth and suggestion["id"] not in in_step_one)
        if asked and not suggestion.get("stale"):
            cards.setdefault(suggestion["segment_id"], []).append(suggestion["id"])
    return cards
