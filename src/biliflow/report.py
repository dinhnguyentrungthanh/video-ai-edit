from __future__ import annotations

import html
import json
from pathlib import Path


def _clock(seconds: float) -> str:
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def write_report(report_dir: Path, payload: dict) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "scan.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    rows = []
    for interval in payload.get("intervals", []):
        thumb = interval.get("strongest_frame")
        image = f'<img src="{html.escape(thumb)}" loading="lazy">' if thumb else ""
        rows.append(
            "<tr>"
            f"<td>{_clock(interval['start_seconds'])}</td>"
            f"<td>{_clock(interval['end_seconds'])}</td>"
            f"<td>{interval['max_score']:.3f}</td>"
            f"<td>{interval['sample_count']}</td>"
            f"<td>{html.escape(str(interval.get('predicted_label', '')))}</td>"
            f"<td>{html.escape(str(interval.get('reason', '')))}</td>"
            f"<td>{image}</td>"
            "</tr>"
        )

    candidate_rows = []
    for candidate in payload.get("top_candidates", []):
        thumb = candidate.get("thumbnail")
        image = f'<img src="{html.escape(thumb)}" loading="lazy">' if thumb else ""
        candidate_rows.append(
            "<tr>"
            f"<td>{candidate['rank']}</td>"
            f"<td>{_clock(candidate['timestamp_seconds'])}</td>"
            f"<td>{candidate['score']:.3f}</td>"
            f"<td>{image}</td>"
            "</tr>"
        )

    scan_type = payload.get("scan_type", "nsfw")
    scan_names = {
        "nsfw": "nội dung 18+",
        "adult": "nội dung 18+",
        "gore": "máu/gore",
        "violence": "bạo lực",
    }
    scan_name = scan_names.get(scan_type, scan_type)
    target_label = payload.get("target_label")
    target_note = f" · Nhãn theo dõi: <strong>{html.escape(target_label)}</strong>" if target_label else ""
    document = f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><title>BiliFlow scan report</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;max-width:1100px;margin:32px auto;padding:0 20px;background:#111;color:#eee}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #444;padding:10px;text-align:left}}
th{{background:#252525}}img{{max-width:320px;max-height:180px}}code{{color:#b7e1ff}}
.warning{{padding:12px;background:#3b2d10;border-left:4px solid #e0a526}}
</style></head><body>
<h1>BiliFlow — quét {html.escape(scan_name)}</h1>
<p class="warning">Kết quả chỉ dùng để hỗ trợ review. Không chứng minh video an toàn và không thay thế kiểm tra của người dùng.</p>
<p>Video: <code>{html.escape(payload.get('input', ''))}</code></p>
<p>Trạng thái: <strong>{html.escape(payload.get('status', ''))}</strong> · Tổng frame: {payload.get('frames_scanned', 0)}{target_note}</p>
<table><thead><tr><th>Bắt đầu</th><th>Kết thúc</th><th>Điểm cao nhất</th><th>Số mẫu</th><th>Nhãn</th><th>Lý do</th><th>Preview</th></tr></thead>
<tbody>{''.join(rows) if rows else '<tr><td colspan="7">Không có cảnh vượt ngưỡng. Vẫn cần review thủ công.</td></tr>'}</tbody></table>
<h2>Các frame có điểm cao nhất</h2>
<p>Danh sách này phục vụ blind review và hiệu chỉnh threshold; frame xuất hiện ở đây chưa được xem là vi phạm.</p>
<table><thead><tr><th>Hạng</th><th>Timestamp</th><th>Điểm</th><th>Preview</th></tr></thead>
<tbody>{''.join(candidate_rows)}</tbody></table>
</body></html>"""
    (report_dir / "review.html").write_text(document, encoding="utf-8")
