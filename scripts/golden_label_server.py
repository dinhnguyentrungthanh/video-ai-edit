"""Start the Golden Set labeling page (docs/QUALITY_PLAN.md §12.5, §15).

Default: this PC only, http://127.0.0.1:8766. --phone: also reachable from a phone on the same
home Wi-Fi, protected by a random access code; the link is printed and saved to
reports/benchmarks/golden-<set>/phone-link.txt. Windows may ask once to allow Python through the
firewall; the person at the PC decides. --set v1.1 labels annotations/golden/v1.1 (suggestions and
frames under reports/benchmarks/golden-v1.1); the default is v1.
"""
from __future__ import annotations

import argparse
import secrets
import socket
import sys
import webbrowser
from pathlib import Path

from biliflow.golden_label_app import GoldenLabelApp
from biliflow.golden_set import KNOWN_SETS

ROOT = Path(__file__).resolve().parents[1]
CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def set_paths(name: str, root: Path | None = None) -> dict[str, Path]:
    """Label directory, suggestions, frame cache and phone-link file of one Golden Set."""
    root = root or ROOT
    benchmarks = root / "reports/benchmarks" / f"golden-{name}"
    return {"labels_dir": root / "annotations/golden" / name,
            "suggestions_path": benchmarks / "prefill/suggestions.json",
            "frames_dir": benchmarks / "frames", "link_file": benchmarks / "phone-link.txt"}


def lan_address() -> str:
    """Address of the interface that routes to the home network (no packet is sent)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.168.1.1", 80))
        address = probe.getsockname()[0]
    finally:
        probe.close()
    if address.startswith("127.") or not address.startswith(("192.168.", "10.", "172.")):
        raise SystemExit(f"Không tìm thấy Wi-Fi nhà (địa chỉ {address}); chế độ điện thoại chỉ dùng trong mạng nội bộ")
    return address


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--phone", action="store_true", help="also open to a phone on the same home Wi-Fi (access code)")
    parser.add_argument("--set", default="v1", choices=KNOWN_SETS, help="Golden Set to label (default v1)")
    args = parser.parse_args(argv)
    paths = set_paths(args.set)
    if not (paths["labels_dir"] / "segments.json").exists():
        raise SystemExit(f"Chưa có manifest {paths['labels_dir'] / 'segments.json'}; tạo bằng "
                         f"scripts/golden_prefill.py --set {args.set} manifest")
    host, code = "127.0.0.1", None
    if args.phone:
        host = lan_address()
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    app = GoldenLabelApp(ROOT, labels_dir=paths["labels_dir"], suggestions_path=paths["suggestions_path"],
                         frames_dir=paths["frames_dir"], ffmpeg=ROOT / "tools/ffmpeg/bin/ffmpeg.exe",
                         host=host, port=args.port, access_code=code)
    link_file = paths["link_file"]
    if args.phone:
        link = f"http://{host}:{args.port}/?code={code}"
        link_file.parent.mkdir(parents=True, exist_ok=True)
        link_file.write_text(link + "\n", encoding="utf-8")
        print("=" * 64, flush=True)
        print(f"  GÁN NHÃN GOLDEN SET {args.set} QUA ĐIỆN THOẠI (cùng Wi-Fi nhà)", flush=True)
        print(f"  Mở trên điện thoại:  {link}", flush=True)
        print(f"  Hoặc vào http://{host}:{args.port}/ và nhập mã:  {code}", flush=True)
        print("  Lần đầu Windows có thể hỏi cho phép Python qua tường lửa: bấm Allow access.", flush=True)
        print("  Tắt hẳn: đóng cửa sổ này hoặc bấm đúp Golden-Label-Stop.cmd", flush=True)
        print("=" * 64, flush=True)
    else:
        url = f"http://127.0.0.1:{args.port}/"
        print(f"Golden Set {args.set} labeling: {url} (Ctrl+C để dừng; nhãn lưu ở annotations/golden/{args.set})",
              flush=True)
        if not args.no_browser:
            webbrowser.open(url)
    try:
        app.serve()
    except KeyboardInterrupt:
        pass
    finally:
        if args.phone and link_file.exists():
            link_file.unlink()  # the code dies with the server


if __name__ == "__main__":
    main()
