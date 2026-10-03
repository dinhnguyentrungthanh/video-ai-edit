"""Serve only prototype files, on loopback; no BiliFlow imports or API forwarding."""
from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent
# The demo server never serves adapter.js or live.html: those belong to /dashboard-v2/ of the Control Center.
PUBLIC = {"index.html", "styles.css", "theme.css", "contracts.js", "mock-data.js", "demo-store.js", "download-demo.js", "app.js"}
PUBLIC.update(f"assets/{p.name}" for p in (ROOT / "assets").glob("*.svg"))


class DemoHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def allowed(self):
        relative = unquote(urlsplit(self.path).path).lstrip("/") or "index.html"
        target = (ROOT / relative).resolve()
        if relative not in PUBLIC or not target.is_relative_to(ROOT):
            self.send_error(404, "Prototype asset not found")
            return False
        return True

    def do_GET(self):
        if not self.allowed():
            return
        super().do_GET()

    def do_HEAD(self):
        if self.allowed():
            super().do_HEAD()

    def do_POST(self):
        self.send_error(405, "Static demo: API writes are not supported")

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Content-Security-Policy", "frame-ancestors 'self'")
        super().end_headers()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8794)
    args = parser.parse_args()
    if ROOT.drive.lower() != "e:":
        raise SystemExit("Keep this demo and its resources on drive E.")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), DemoHandler)
    print(f"BiliFlow V2 demo: http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
