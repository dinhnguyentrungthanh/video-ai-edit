from __future__ import annotations

import csv
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


USER_AGENT = "BiliFlow/0.2 local content-safety benchmark"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"

LIVE_POSITIVES = [
    "File:Buring hand.jpg",
    "File:Burns foot.jpg",
    "File:Clinical photo of necrotic ulceration.png",
    "File:Exit wounds (121495316).jpg",
    "File:Ferida 1.jpg",
    "File:Ferida 2.jpg",
    "File:Ferida 4.jpg",
    "File:Ferida 5.jpg",
    "File:Schwertverletzung.JPG",
    "File:Vulnus incisum.jpg",
]

# The previous animation set came from an artwork aggregator without dependable
# per-file reuse grants. It is intentionally unavailable for future downloads.
ANIMATION_POSITIVES: list[tuple[int, str]] = []


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open(request: urllib.request.Request, timeout: int):
    for attempt in range(6):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == 5:
                raise
            time.sleep(min(int(exc.headers.get("Retry-After", 2 ** (attempt + 1))), 30))
    raise AssertionError("unreachable")


def _download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with _open(request, 120) as response, target.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)


def _commons_infos(titles: list[str]) -> dict[str, dict]:
    query = urllib.parse.urlencode(
        {
            "action": "query",
            "titles": "|".join(titles),
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "iiurlwidth": 768,
            "format": "json",
            "formatversion": 2,
        }
    )
    request = urllib.request.Request(f"{COMMONS_API}?{query}", headers={"User-Agent": USER_AGENT})
    with _open(request, 60) as response:
        pages = json.load(response)["query"]["pages"]
    output = {}
    for page in pages:
        info = page["imageinfo"][0]
        metadata = info.get("extmetadata", {})
        output[page["title"]] = {
            "url": info.get("thumburl", info["url"]),
            "source": info["descriptionurl"],
            "license": metadata.get("LicenseShortName", {}).get("value", "unknown"),
        }
    return output


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")[:80] + ".jpg"


def _row(root: Path, sample_id: str, path: Path, style: str, label: int, source: str, license_name: str, notes: str) -> dict:
    return {
        "sample_id": sample_id,
        "image_path": path.relative_to(root).as_posix(),
        "style": style,
        "label": label,
        "source": source,
        "license": license_name,
        "sha256": _sha256(path),
        "notes": notes,
    }


def main() -> int:
    if not ANIMATION_POSITIVES:
        raise SystemExit(
            "Dataset build disabled: replace the former animation artwork with "
            "per-file licensed or public-domain sources before rebuilding."
        )
    root = Path(__file__).resolve().parents[1]
    destination = root / "benchmarks" / "gore-poc" / "positive"
    live_dir = destination / "live_action"
    animation_dir = destination / "animation"
    live_dir.mkdir(parents=True, exist_ok=True)
    animation_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []

    infos = _commons_infos(LIVE_POSITIVES)
    for index, title in enumerate(LIVE_POSITIVES, start=1):
        target = live_dir / _safe_name(title)
        if not target.exists():
            _download(infos[title]["url"], target)
            time.sleep(0.5)
        rows.append(
            _row(
                root, f"gore-live-pos-{index:03d}", target, "live_action", 1,
                infos[title]["source"], infos[title]["license"],
                f"Wikimedia Commons Wounds category; {title}",
            )
        )

    for index, (post_id, url) in enumerate(ANIMATION_POSITIVES, start=1):
        target = animation_dir / f"danbooru-{post_id}.jpg"
        if not target.exists():
            _download(url, target)
            time.sleep(0.5)
        rows.append(
            _row(
                root, f"gore-animation-pos-{index:03d}", target, "animation", 1,
                f"https://danbooru.donmai.us/posts/{post_id}", "source artwork; benchmark only",
                "Danbooru rating:g with guro and/or blood tag",
            )
        )

    gore_candidates = sorted((root / "reports" / "conan-gore-full" / "candidates").glob("*.jpg"))
    excluded_timestamps = {287, 2652, 2664, 2681}
    animation_negatives = [
        path for path in gore_candidates
        if int(path.name.split("-")[1]) not in excluded_timestamps
    ][:20]
    live_negatives = sorted(
        (root / "reports" / "tears-of-steel-blind-review" / "candidates").glob("*.jpg")
    )[:20]
    if len(animation_negatives) != 20 or len(live_negatives) != 20:
        raise RuntimeError("Expected 20 hard negatives for each style")

    for style, paths, source in (
        ("animation", animation_negatives, "Conan verified non-gore candidate"),
        ("live_action", live_negatives, "Tears of Steel verified non-gore frame"),
    ):
        for index, path in enumerate(paths, start=1):
            rows.append(
                _row(
                    root, f"gore-{style}-neg-{index:03d}", path, style, 0,
                    source, "local test input", "hard negative from previous scan",
                )
            )

    manifest = root / "annotations" / "gore_poc_benchmark.csv"
    columns = ["sample_id", "image_path", "style", "label", "source", "license", "sha256", "notes"]
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    print(
        json.dumps(
            {
                "manifest": str(manifest),
                "samples": len(rows),
                "positive": sum(int(row["label"]) for row in rows),
                "negative": sum(int(row["label"]) == 0 for row in rows),
                "downloaded_positive_bytes": sum(
                    (root / row["image_path"]).stat().st_size for row in rows if int(row["label"]) == 1
                ),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
