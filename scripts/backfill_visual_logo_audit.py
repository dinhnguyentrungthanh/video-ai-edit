from __future__ import annotations

import argparse
import html
import json
import subprocess
import tempfile
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add one audit frame per retained visual-logo window without rerunning AI"
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    return parser.parse_args()


def _clock(seconds: float) -> str:
    whole = max(0, int(seconds))
    return f"{whole // 3600:02d}:{(whole % 3600) // 60:02d}:{whole % 60:02d}"


def _write_audit_html(report_dir: Path, records: list[dict]) -> Path:
    cards = []
    for record in sorted(records, key=lambda item: float(item["start_seconds"])):
        confirmation = record.get("visual_logo_confirmation", {})
        state = str(confirmation.get("state", "UNKNOWN"))
        answer = html.escape(str(confirmation.get("answer", "")))
        audit_frame = html.escape(str(record.get("audit_frame", "")))
        cards.append(
            f'<article class="card" data-state="{html.escape(state)}">'
            f'<img loading="lazy" src="{audit_frame}" alt="{_clock(float(record["start_seconds"]))}">'
            f'<div><strong>{_clock(float(record["start_seconds"]))}–{_clock(float(record["end_seconds"]))}</strong> '
            f'<span class="state">{html.escape(state)}</span></div><p>{answer}</p></article>'
        )
    document = """<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Visual brand/logo exhaustive audit</title>
<style>body{font-family:system-ui;margin:20px;background:#101216;color:#eee}button{margin:0 6px 16px 0;padding:8px 12px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}.card{background:#1b1f27;padding:10px;border-radius:8px}.card img{width:100%;aspect-ratio:16/9;object-fit:contain;background:#000}.card p{font-size:12px;color:#bbb;word-break:break-word}.state{float:right;color:#f6c453}.hidden{display:none}</style></head><body>
<h1>Audit brand/logo toàn timeline</h1><p>Mỗi thẻ là một cửa sổ 5 giây. REJECTED vẫn được giữ để kiểm tra false negative; ảnh không cho phép tự động chỉnh video.</p>
<button onclick="filterCards('ALL')">Tất cả</button><button onclick="filterCards('CONFIRMED')">Confirmed</button><button onclick="filterCards('UNCERTAIN')">Uncertain</button><button onclick="filterCards('REJECTED')">Rejected</button>
<div class="grid">""" + "".join(cards) + """</div><script>function filterCards(state){document.querySelectorAll('.card').forEach(card=>card.classList.toggle('hidden',state!=='ALL'&&card.dataset.state!==state));}</script></body></html>"""
    output = report_dir / "audit.html"
    output.write_text(document, encoding="utf-8")
    return output


def main() -> int:
    args = parse_args()
    report_path = args.report.resolve(strict=True)
    ffmpeg_path = args.ffmpeg.resolve(strict=True)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    sampling = payload.get("sampling", {})

    source = Path(payload["input"]).resolve(strict=True)
    window_seconds = float(sampling["window_seconds"])
    scan_start = float(payload.get("scan_start_seconds", 0.0))
    scan_duration = float(payload["scan_duration_seconds"])
    first_timestamp = scan_start + window_seconds / 2.0
    report_dir = report_path.parent
    audit_dir = report_dir / "audit-thumbnails"
    audit_dir.mkdir(parents=True, exist_ok=True)

    records = [*payload.get("intervals", []), *payload.get("rejected_windows", [])]
    audit_complete = bool(records) and all(
        item.get("audit_frame") and (report_dir / item["audit_frame"]).is_file()
        for item in records
    )
    if not audit_complete:
        with tempfile.TemporaryDirectory(prefix="biliflow-logo-audit-", dir=report_dir) as temporary:
            pattern = Path(temporary) / "window-%04d.jpg"
            command = [
                str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{first_timestamp:.6f}", "-i", str(source),
                "-t", f"{max(0.0, scan_duration - window_seconds / 2.0):.6f}",
                "-vf", f"fps=1/{window_seconds:.9f}", "-q:v", "4", str(pattern),
            ]
            subprocess.run(command, check=True)
            extracted = sorted(Path(temporary).glob("window-*.jpg"))
            expected = int((scan_duration + window_seconds - 1e-9) // window_seconds)
            if len(extracted) < max(0, expected - 1):
                raise RuntimeError(f"Expected at least {expected - 1} audit frames, got {len(extracted)}")

            for record in records:
                index = int(round((float(record["start_seconds"]) - scan_start) / window_seconds)) + 1
                source_frame = Path(temporary) / f"window-{index:04d}.jpg"
                display_timestamp = min(
                    float(payload["duration_seconds"]) - 0.05,
                    (float(record["start_seconds"]) + float(record["end_seconds"])) / 2.0,
                )
                if not source_frame.exists():
                    subprocess.run([
                        str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-y",
                        "-ss", f"{display_timestamp:.6f}", "-i", str(source),
                        "-frames:v", "1", "-q:v", "4", str(source_frame),
                    ], check=True)
                name = f"window-{index:04d}-{display_timestamp:.3f}s.jpg"
                destination = audit_dir / name
                destination.write_bytes(source_frame.read_bytes())
                record["audit_frame"] = f"audit-thumbnails/{name}"

    referenced = {
        Path(str(item["audit_frame"])).name
        for item in [*payload.get("intervals", []), *payload.get("rejected_windows", [])]
        if item.get("audit_frame")
    }
    for candidate in audit_dir.glob("window-*.jpg"):
        if candidate.name not in referenced:
            candidate.unlink(missing_ok=True)

    payload.setdefault("safety", {})["audit_frames"] = (
        "Evidence only. Rejected windows remain non-editable unless a human creates a review decision."
    )
    temporary_report = report_path.with_suffix(report_path.suffix + ".tmp")
    temporary_report.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary_report.replace(report_path)
    audit_html = _write_audit_html(report_dir, records)
    print(json.dumps({
        "report": str(report_path),
        "audit_frames": len(referenced),
        "directory": str(audit_dir),
        "html": str(audit_html),
        "automatic_edit": False,
    }, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
