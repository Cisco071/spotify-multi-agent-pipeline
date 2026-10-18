"""Tiny local server for human review pages (stdlib only; binds to 127.0.0.1).

    python evals/review_server.py            # then open http://localhost:8765/spotcheck/<run>/review.html

GET serves files under evals/. POST /api/save/<run_id> writes the reviewer's decisions to
evals/spotcheck/<run_id>/decisions.json (atomic replace) so nothing lives only in browser memory.
"""
import json
import os
import re
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent


class Handler(SimpleHTTPRequestHandler):
    def do_POST(self):
        m = re.fullmatch(r"/api/save/([A-Za-z0-9_.-]+)", self.path)
        if not m:
            return self.send_error(404)
        n = int(self.headers.get("Content-Length", 0))
        if n > 5_000_000:
            return self.send_error(413)
        try:
            data = json.loads(self.rfile.read(n))
        except ValueError:
            return self.send_error(400)
        out = BASE / "spotcheck" / m.group(1) / "decisions.json"
        if not out.parent.exists():
            return self.send_error(404)
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, out)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"saved":true}')

    def log_message(self, fmt, *args):
        if "POST" in (args[0] if args else ""):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    print(f"serving {BASE} on http://localhost:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(BASE))).serve_forever()
