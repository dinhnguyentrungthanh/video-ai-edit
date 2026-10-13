"""Item 3 isolated reproduction: headless Edge (as the E2E fixture launches it) loads a page with many scripts from
a ThreadingHTTPServer on 127.0.0.1 (HTTP/1.0: one TCP connection per request, like the Control Center), K times;
count requests that fail with ERR_NO_BUFFER_SPACE, for a given listen backlog (socketserver's default is 5).
Usage: edge_burst.py <profile_dir> <backlog> <scripts> <loads> [busy]
busy=1 keeps a CPU-bound Python thread running (GIL contention, like a busy test process)."""
import sys
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright

profile, backlog, scripts, loads = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
busy = len(sys.argv) > 5 and sys.argv[5] == "1"
PAGE = ("<!doctype html><title>t</title>" + "".join(f'<script src="/s/{i}.js"></script>' for i in range(scripts))
        + "<p id=done>ok</p>").encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE if self.path.startswith("/page") else b"window.x=(window.x||0)+1;"
        self.send_response(200)
        self.send_header("Content-Type", "text/html" if self.path.startswith("/page") else "text/javascript")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class Server(ThreadingHTTPServer):
    request_queue_size = backlog
    daemon_threads = True


server = Server(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
stop = threading.Event()
if busy:
    def spin():
        while not stop.is_set():
            sum(range(10000))
    for _ in range(2):
        threading.Thread(target=spin, daemon=True).start()
failures = Counter()
with sync_playwright() as pw:
    context = pw.chromium.launch_persistent_context(profile, channel="msedge", headless=True, args=["--no-proxy-server"])
    page = context.pages[0] if context.pages else context.new_page()
    page.on("requestfailed", lambda request: failures.update([request.failure or "?"]))
    started = time.monotonic()
    for number in range(loads):
        page.goto(f"http://127.0.0.1:{server.server_address[1]}/page?{number}", wait_until="load")
    elapsed = time.monotonic() - started
    context.close()
stop.set()
server.shutdown()
server.server_close()
print(f"backlog {backlog} scripts {scripts} loads {loads} busy {busy}: {elapsed:.0f} s, "
      f"{loads * (scripts + 1)} requests, failed {dict(failures)}")
