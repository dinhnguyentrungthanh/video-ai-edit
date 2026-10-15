"""Session R evidence: a self-made review job on a test Control Center (no real film, site, link or path).

    make  python make_fixture.py make --src <tree>/src --root <install>/temp/<new folder>
    serve python make_fixture.py serve --src <tree>/src --root <same folder> --port <port, never 8765>

make draws a 30 s 1280x536 video: a scene, changing subtitles, a faint TRANG.EXAMPLE inside the subtitle band, a
ticker crossing the top and LOGO.EXAMPLE in a corner. It writes the text scan of that video the way textscan writes
one (tracks with their zone, their OCR lines and a preview at the analysis size with "REVIEW <n> | <zone>" burned in
by textscan._annotate), builds review-queue.json with build_review_queue of that tree and registers the job as
WAITING_REVIEW with the queue paused. serve runs the ControlCenter of that tree on the root and port, the review
frames through <install>/tools/ffmpeg. Run make with the main tree for "before" and with the branch for "after".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

# The install folder: the outermost folder above this script with the install marker (a worktree in its temp has one too).
INSTALL = [p for p in Path(__file__).resolve().parents if (p / "biliflow-install.json").is_file()][-1]
FFMPEG = INSTALL / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
SIZE, ANALYSIS, SECONDS, FPS = (1280, 536), (960, 402), 30, 4
JOB_KEY, SOURCE = "review-ui-demo", "review-ui-demo.mp4"
SUBTITLES = ["Ngày mai chúng ta lên đường sớm.", "Anh có nghe thấy tôi nói không?", "Tôi biết rồi, đừng nhắc nữa.",
             "Cửa hàng mở lúc bảy giờ sáng.", "Được, hẹn gặp lại ở bến xe.", "Trời sắp mưa rồi, về thôi."]
TICKER = "Phim cập nhật nhanh nhất tại TRANG.EXAMPLE · Phim chỉ đăng trên trang này · Ủng hộ TRANG.EXAMPLE"
TICKER_START, TICKER_SPEED, TICKER_Y = 3.0, 202.0, 10
MARK, MARK_Y, LOGO, LOGO_XY = "TRANG.EXAMPLE", 418, "LOGO.EXAMPLE", (1010, 56)


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    try:
        return ImageFont.truetype("arialbd.ttf" if bold else "arial.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def _ticker_x(t: float, width: int) -> float:
    return SIZE[0] - TICKER_SPEED * (t - TICKER_START) if t >= TICKER_START else SIZE[0] + width


def frame(t: float):
    """One frame of the self-made video at t seconds."""
    from PIL import Image, ImageDraw
    w, h = SIZE
    image = Image.new("RGB", SIZE)
    draw = ImageDraw.Draw(image)
    for y in range(h):  # evening sky to a dark floor
        k = y / h
        draw.line([(0, y), (w, y)], fill=(int(40 + 60 * k), int(70 + 50 * (1 - k)), int(110 - 40 * k)))
    draw.rectangle((120, 120, 420, 380), fill=(205, 190, 150))  # a lit window
    draw.line((270, 120, 270, 380), fill=(90, 70, 50), width=6)
    shift = 12 * ((t % 8) / 8 - 0.5)
    draw.ellipse((700 + shift, 150, 820 + shift, 280), fill=(60, 45, 40))  # a person
    draw.rounded_rectangle((650 + shift, 270, 870 + shift, 536), radius=60, fill=(45, 55, 70))
    overlay = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    layer = ImageDraw.Draw(overlay)
    ticker_font = _font(24, bold=True)
    width = int(layer.textlength(TICKER, font=ticker_font))
    x = _ticker_x(t, width)
    if x < w and x + width > 0:
        layer.rectangle((0, TICKER_Y - 4, w, TICKER_Y + 30), fill=(0, 0, 0, 120))
        layer.text((x, TICKER_Y), TICKER, font=ticker_font, fill=(255, 235, 120, 255))
    layer.text(LOGO_XY, LOGO, font=_font(30, bold=True), fill=(255, 255, 255, 215))
    mark_font = _font(18, bold=True)
    layer.text(((w - layer.textlength(MARK, font=mark_font)) / 2, MARK_Y), MARK, font=mark_font, fill=(255, 255, 255, 70))
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(image)
    line = SUBTITLES[int(t // 5) % len(SUBTITLES)]
    sub_font = _font(30, bold=True)
    draw.text(((w - draw.textlength(line, font=sub_font)) / 2, 452), line, font=sub_font, fill="white",
              stroke_width=3, stroke_fill="black")
    return image


def _box(left: float, top: float, right: float, bottom: float) -> list[int]:
    """A source-pixel box at the analysis size, as textscan stores union_box."""
    sx, sy = ANALYSIS[0] / SIZE[0], ANALYSIS[1] / SIZE[1]
    return [max(0, round(left * sx)), max(0, round(top * sy)), min(ANALYSIS[0], round(right * sx)), min(ANALYSIS[1], round(bottom * sy))]


def tracks() -> list[dict]:
    """The text scan's tracks: (track, start, end, preview time, box, OCR lines, zone); previews in track order."""
    from PIL import ImageDraw
    draw = ImageDraw.Draw(frame(0))
    width, logo_w = (int(draw.textlength(text, font=_font(size, bold=True))) for text, size in ((TICKER, 24), (LOGO, 30)))

    def ticker_box(t):
        x = _ticker_x(t, width)
        return _box(max(0, x), TICKER_Y - 2, min(SIZE[0], x + width), TICKER_Y + 28)
    mark_w = 150
    rows = [
        (4, 0.0, 30.0, 12.0, _box(LOGO_XY[0] - 6, LOGO_XY[1] - 2, LOGO_XY[0] + logo_w + 6, LOGO_XY[1] + 36),
         ["LOGO.EXAMPLE", "LOGOEXAMPLE", "L0GO.EXAMPLE"], "top-right", "high"),
        # The ticker read three times while it crosses: its head entering, the whole line, its tail.
        (21, 4.0, 7.0, 5.0, ticker_box(5.0), ["Phim cập nhật nh"], None, "medium"),
        (22, 7.0, 11.0, 8.8, ticker_box(8.8), [TICKER.replace(" · ", ". ")], None, "medium"),
        (23, 11.0, 14.0, 13.0, ticker_box(13.0), ["Ủng hộ TRANG.EXA"], None, "medium"),
        # A faint site name inside the subtitle band, read with the subtitles around it.
        (58, 0.0, 30.0, 17.0, _box((SIZE[0] - mark_w) / 2 - 6, MARK_Y - 2, (SIZE[0] + mark_w) / 2 + 6, MARK_Y + 22),
         ["TRANG.EXAMPLE", "TRANGEXAMPLE", "TRANG EXAMPLE", "TRAN6.EXAMPLE", SUBTITLES[0], SUBTITLES[1], "Được.", SUBTITLES[2]],
         "subtitle", "medium"),
    ]
    from biliflow import textscan
    out = []
    for track_id, start, end, at, box, texts, zone, priority in rows:
        zone = zone or textscan._zone(tuple(box), *ANALYSIS)
        out.append({"track_id": track_id, "start_seconds": start, "end_seconds": end, "recommended_blur_end_seconds": end,
                    "review_candidate": True, "union_box": box, "sample_text": texts, "zone": zone, "review_priority": priority,
                    "reason": "Chữ lặp lại nhưng có thể thuộc nội dung phim", "preview": f"text-previews/track-{track_id:05d}.jpg",
                    "_at": at})
    return out


def _checked_root(root: Path, new: bool) -> Path:
    root = root.resolve()
    temp = (INSTALL / "temp").resolve()
    if temp not in root.parents:
        raise SystemExit(f"refused: the root must be a folder under {temp}")
    if new and root.exists() and any(root.iterdir()):
        raise SystemExit("refused: the root must be a new, empty folder")
    return root


def make(root: Path) -> None:
    from biliflow import textscan
    from biliflow.job_store import JobStore
    from biliflow.review_workflow import build_review_queue
    root = _checked_root(root, new=True)
    for name in ("input", "output", "state", f"reports/jobs/{JOB_KEY}/text/text-previews", "work/frames"):
        (root / name).mkdir(parents=True, exist_ok=True)
    for index in range(SECONDS * FPS):
        frame(index / FPS).save(root / "work" / "frames" / f"f{index:04d}.png")
    source = root / "input" / SOURCE
    subprocess.run([str(FFMPEG), "-v", "error", "-y", "-framerate", str(FPS), "-i", str(root / "work/frames/f%04d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25", "-g", "25", str(source)], check=True)
    report_dir = root / "reports" / "jobs" / JOB_KEY / "text"
    rows = tracks()
    for row in rows:
        textscan._annotate(frame(row.pop("_at")).resize(ANALYSIS), row).save(report_dir / row["preview"], quality=90)
    report = report_dir / "text-scan.json"
    report.write_text(json.dumps({"status": "REVIEW_REQUIRED", "input": str(source), "duration_seconds": SECONDS,
                                  "source_size": list(SIZE), "analysis_size": list(ANALYSIS), "sample_every_seconds": 1.0,
                                  "tracks": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    queue_path = root / "reports" / "jobs" / JOB_KEY / "review-queue.json"
    queue = build_review_queue(project_root=root, report_paths=[report], queue_path=queue_path)
    store = JobStore(root / "state" / "control-center.sqlite3")
    try:
        stat, digest = source.stat(), hashlib.sha256(source.read_bytes()).hexdigest()
        job = store.upsert_job(job_key=JOB_KEY, source_path=source, source_sha256=digest, source_size_bytes=stat.st_size,
                               source_mtime_ns=stat.st_mtime_ns, content_style="live_action", state="WAITING_REVIEW")
        store.update_job(int(job["id"]), active_queue_path=f"reports/jobs/{JOB_KEY}/review-queue.json", active_revision=1, progress=1.0)
        store.set_setting("scheduler_paused", True)  # nothing of this root is ever scanned or exported
    finally:
        store.close()
    print(json.dumps({"job": int(job["id"]), "items": len(queue["items"]), "advisory": len(queue["advisory_items"]),
                      "queue_sha256": hashlib.sha256(queue_path.read_bytes()).hexdigest()}))


def serve(root: Path, port: int) -> None:
    from biliflow.control_center import ControlCenter
    from biliflow.review_evidence import ReviewFrameCache
    if port == 8765:
        raise SystemExit("refused: 8765 is the real Control Center")
    root = _checked_root(root, new=False)
    center = ControlCenter(root, port=port, stable_seconds=3600.0, import_existing=False)
    center.frame_cache = ReviewFrameCache(center.root, FFMPEG)
    center.serve()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["make", "serve"])
    parser.add_argument("--src", required=True, type=Path, help="the src folder of the tree to run")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    sys.path.insert(0, str(args.src.resolve()))
    if args.command == "make":
        make(args.root)
    else:
        serve(args.root, args.port)


if __name__ == "__main__":
    main()
