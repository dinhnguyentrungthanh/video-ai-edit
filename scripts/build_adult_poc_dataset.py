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


COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "BiliFlow/0.2 local content-safety benchmark"

LIVE_ACTION = [
    "File:Anterior view of an adult female with digital manipulation.jpg",
    "File:Anterior view of human female pelvic region.jpg",
    "File:Anterior view of human female.jpg",
    "File:Female nude rear white background.png",
    "File:Front view of a woman.jpg",
    "File:Human body of female.png",
    "File:Human Female Frontal Anatomy.png",
    "File:Human female.jpg",
    "File:Posterior view of human female, retouched.jpg",
    "File:Posterior view of human female.jpg",
]

ANIMATION = [
    "File:Daishuki hold.jpg",
    "File:Futanari.png",
    "File:Hadako-tan.png",
    "File:Hentai - yuuree-redraw.jpg",
    "File:Netorare.png",
    "File:Tentacles by Precipiation24.webp",
    "File:Tentacles! by Acefishy.png",
    "File:Hentai - yuuree.jpg",
    "File:Hentai censorship.jpg",
    "File:Hentai - yuuree-redraw-no-halo.jpg",
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(title: str, fallback: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
    return (stem[:80] or fallback) + ".jpg"


def _open(request: urllib.request.Request, timeout: int):
    for attempt in range(6):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == 5:
                raise
            delay = int(exc.headers.get("Retry-After", 2 ** (attempt + 1)))
            time.sleep(min(delay, 30))
    raise AssertionError("unreachable")


def _image_infos(titles: list[str]) -> dict[str, dict]:
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
            "title": page["title"],
            "download_url": info.get("thumburl", info["url"]),
            "source": info["descriptionurl"],
            "license": metadata.get("LicenseShortName", {}).get("value", "unknown"),
        }
    return output


def _download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with _open(request, 120) as response, target.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    destination = root / "benchmarks" / "adult-poc" / "positive"
    destination.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    infos = _image_infos(LIVE_ACTION + ANIMATION)
    for style, titles in (("live_action", LIVE_ACTION), ("animation", ANIMATION)):
        style_dir = destination / style
        style_dir.mkdir(parents=True, exist_ok=True)
        for index, title in enumerate(titles, start=1):
            info = infos[title]
            target = style_dir / _safe_name(info["title"], f"{style}-{index:03d}")
            if not target.exists():
                _download(info["download_url"], target)
                time.sleep(0.5)
            rows.append(
                {
                    "sample_id": f"{style}-pos-{index:03d}",
                    "image_path": target.relative_to(root).as_posix(),
                    "style": style,
                    "label": 1,
                    "source": info["source"],
                    "license": info["license"],
                    "sha256": _sha256(target),
                    "notes": f"Wikimedia Commons category-curated positive; {info['title']}",
                }
            )

    negative_manifest = root / "annotations" / "adult_negative_benchmark.csv"
    with negative_manifest.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            image_path = root / row["image_path"]
            rows.append({**row, "license": "local test input", "sha256": _sha256(image_path)})

    output_manifest = root / "annotations" / "adult_poc_benchmark.csv"
    columns = ["sample_id", "image_path", "style", "label", "source", "license", "sha256", "notes"]
    with output_manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "manifest": str(output_manifest),
        "samples": len(rows),
        "positive": sum(int(row["label"]) for row in rows),
        "negative": sum(int(row["label"]) == 0 for row in rows),
        "bytes": sum((root / row["image_path"]).stat().st_size for row in rows if int(row["label"]) == 1),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
