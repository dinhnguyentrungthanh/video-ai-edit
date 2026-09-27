"""Show visual evidence for short gaps between logo candidates before CUT review."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--logo-report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--intro-end", type=float, default=420.0)
    parser.add_argument("--outro-start", type=float, default=3880.0)
    parser.add_argument("--max-gap", type=float, default=30.0)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    reports = (root / "reports").resolve(strict=True)
    queue_path = args.queue.resolve(strict=True)
    logo_path = args.logo_report.resolve(strict=True)
    output_dir = args.output_dir.resolve()
    for path in (queue_path, logo_path, output_dir):
        if path != reports and reports not in path.parents:
            raise ValueError(f"Audit path must stay in reports: {path}")
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    logo = json.loads(logo_path.read_text(encoding="utf-8"))
    if queue["source"]["sha256"].lower() != logo["input_sha256"].lower():
        raise ValueError("Queue and logo report have different source checksums")
    source = Path(queue["source"]["path"]).resolve(strict=True)
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual_sha = digest.hexdigest()
    if actual_sha.lower() != queue["source"]["sha256"].lower():
        raise ValueError("Source checksum changed")

    candidates = sorted(
        (item for item in queue["items"] if item["category"] == "visual_logo"),
        key=lambda item: item["start_seconds"],
    )
    audit_windows = sorted(
        logo.get("intervals", []) + logo.get("rejected_windows", []),
        key=lambda item: item["start_seconds"],
    )
    gaps = []
    pairs = [
        (left, right, float(left["end_seconds"]), float(right["start_seconds"]))
        for left, right in zip(candidates, candidates[1:])
    ]
    if candidates and float(candidates[-1]["end_seconds"]) >= args.outro_start:
        pairs.append((
            candidates[-1], None, float(candidates[-1]["end_seconds"]),
            float(queue["source"]["duration_seconds"]),
        ))
    for left, right, start, end in pairs:
        if not 0 < end - start <= args.max_gap:
            continue
        group = "intro" if end <= args.intro_end else "outro" if start >= args.outro_start else None
        if group is None:
            continue
        windows = [
            window for window in audit_windows
            if window["start_seconds"] < end and window["end_seconds"] > start
        ]
        selected = []
        for window in windows:
            frame = window.get("audit_frame")
            if not frame:
                continue
            image_path = (logo_path.parent / frame).resolve(strict=True)
            selected.append({
                "time": window["start_seconds"],
                "frame": image_path.relative_to(root).as_posix(),
                "href": Path(os.path.relpath(image_path, output_dir)).as_posix(),
            })
        gaps.append({
            "group": group,
            "start_seconds": start,
            "end_seconds": end,
            "left_candidate_id": left["id"],
            "right_candidate_id": right["id"] if right else "END_OF_SOURCE",
            "audit_frames": selected,
            "decision": "PENDING_HUMAN_REVIEW",
        })

    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_sha256": actual_sha,
        "queue": queue_path.relative_to(root).as_posix(),
        "logo_report": logo_path.relative_to(root).as_posix(),
        "automatic_cut": False,
        "gaps": gaps,
    }
    (output_dir / "cut-gap-audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    cards = []
    for gap in gaps:
        frames = "".join(
            f'<a href="{html.escape(frame["href"])}">'
            f'<img src="{html.escape(frame["href"])}" '
            f'alt="frame {frame["time"]}"></a>'
            for frame in gap["audit_frames"]
        )
        cards.append(
            f'<section><h2>{gap["group"]}: {gap["start_seconds"]:.3f}–'
            f'{gap["end_seconds"]:.3f}s</h2><p>{gap["left_candidate_id"]} → '
            f'{gap["right_candidate_id"]}. Cần kiểm tra trước khi chọn CUT liên tục.</p>'
            f'<div>{frames}</div></section>'
        )
    document = (
        '<!doctype html><html lang="vi"><meta charset="utf-8">'
        '<title>BiliFlow CUT gap audit</title><style>'
        'body{font:16px system-ui;background:#111;color:#eee;margin:24px}'
        'section{border:1px solid #555;padding:16px;margin:16px 0}'
        'img{height:160px;margin:4px}</style>'
        '<h1>Khoảng hở giữa candidate logo</h1>'
        '<p>Ảnh lấy từ audit exhaustive của chính video. Chưa có CUT tự động.</p>'
        + "".join(cards) + '</html>'
    )
    (output_dir / "cut-gap-audit.html").write_text(document, encoding="utf-8")
    print(json.dumps({"gap_count": len(gaps), "audit_frames": sum(len(g["audit_frames"]) for g in gaps)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
