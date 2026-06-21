#!/usr/bin/env python3
"""Precog web server — serves the built frontend and the live comparison API.

Endpoints:
  GET  /                 -> the built React app (web/static/)
  POST /api/compare      -> {prompt, latency?, think?} => measured comparison JSON
  POST /api/plan         -> {prompt} => the planned reasoning + calls (preview)

Run:  python3 web/server.py [--port 8765]

Stdlib only. The frontend is built with Vite into web/static; if that folder is
missing, the root route explains how to build it.
"""

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import threading  # noqa: E402

from web.compare import run_comparison  # noqa: E402
from web.planner import plan as build_plan  # noqa: E402
from web.stream import stream_comparison  # noqa: E402
from web import learning  # noqa: E402

# A comparison run mutates a process-wide env var (PRECOG_DEMO_LATENCY) that the
# mock server reads at call time, so concurrent runs would race on latency.
# Serialize comparison runs behind one lock; the UI is single-user anyway, and
# this guarantees clean, reproducible numbers during a live demo.
_run_lock = threading.Lock()
MAX_BODY_BYTES = 64 * 1024

STATIC_DIR = os.path.join(_HERE, "static")

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".woff2": "font/woff2",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "PrecogWeb/0.1"

    def _send(self, code, body, content_type="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quieter logging
        sys.stderr.write("[web] " + (fmt % args) + "\n")

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(min(length, MAX_BODY_BYTES))
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            return {}

    def _stream_ndjson(self, plan, latency, think, learn):
        """Stream race events to the client as newline-delimited JSON.

        Runs are serialized behind ``_run_lock`` so a second request can't
        perturb the shared latency env var mid-race. If the lock is held, we
        wait briefly; the demo is single-user so contention is unexpected.
        """
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")  # disable proxy buffering
        self.send_header("Connection", "close")
        self.end_headers()
        acquired = _run_lock.acquire(timeout=30)
        if not acquired:
            try:
                self.wfile.write((json.dumps(
                    {"ev": "error", "error": "server busy with another run"})
                    + "\n").encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass
            return
        try:
            for event in stream_comparison(plan, latency=latency, think=think, learn=learn):
                line = (json.dumps(event) + "\n").encode("utf-8")
                self.wfile.write(line)
                self.wfile.flush()  # push each event immediately
        except (BrokenPipeError, ConnectionResetError):
            pass  # client navigated away mid-stream
        except Exception as exc:
            try:
                self.wfile.write((json.dumps({"ev": "error", "error": str(exc)})
                                  + "\n").encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass
        finally:
            _run_lock.release()

    def _clamp_params(self, data):
        try:
            latency = float(data.get("latency", 0.4))
        except (TypeError, ValueError):
            latency = 0.4
        try:
            think = float(data.get("think", 1.0))
        except (TypeError, ValueError):
            think = 1.0
        return max(0.05, min(latency, 2.0)), max(0.0, min(think, 3.0))

    def do_POST(self):
        if self.path == "/api/reset-learning":
            learning.reset()
            return self._send(200, {"ok": True, "message": "learning reset"})

        if self.path == "/api/compare/stream":
            data = self._read_json()
            prompt = (data.get("prompt") or "").strip()
            if not prompt:
                return self._send(400, {"error": "prompt is required"})
            latency, think = self._clamp_params(data)
            learn = data.get("learn", True) is not False
            planned = build_plan(prompt)
            return self._stream_ndjson(planned, latency, think, learn)

        if self.path == "/api/compare":
            data = self._read_json()
            prompt = (data.get("prompt") or "").strip()
            if not prompt:
                return self._send(400, {"error": "prompt is required"})
            latency = float(data.get("latency", 0.4))
            think = float(data.get("think", 1.0))
            # Clamp to keep a single request bounded.
            latency = max(0.05, min(latency, 2.0))
            think = max(0.0, min(think, 3.0))
            planned = build_plan(prompt)
            try:
                result = run_comparison(planned, latency=latency, think=think)
            except Exception as exc:  # surface failures rather than hang
                return self._send(500, {"error": "comparison failed: %s" % exc})
            return self._send(200, result)

        if self.path == "/api/plan":
            data = self._read_json()
            prompt = (data.get("prompt") or "").strip()
            if not prompt:
                return self._send(400, {"error": "prompt is required"})
            return self._send(200, build_plan(prompt))

        self._send(404, {"error": "not found"})

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            # Cheap liveness probe for the preflight check / demo morning.
            return self._send(200, {
                "ok": True,
                "ui_built": os.path.isfile(os.path.join(STATIC_DIR, "index.html")),
                "busy": _run_lock.locked(),
            })
        if path == "/":
            path = "/index.html"
        # Serve from the built static dir; prevent path traversal.
        rel = path.lstrip("/")
        target = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not target.startswith(STATIC_DIR):
            return self._send(403, {"error": "forbidden"})
        if not os.path.isfile(target):
            if path == "/index.html":
                return self._send(
                    200,
                    "<!doctype html><meta charset=utf-8>"
                    "<body style='font-family:sans-serif;max-width:40rem;margin:4rem auto'>"
                    "<h1>Precog web — not built yet</h1>"
                    "<p>Build the frontend first:</p>"
                    "<pre>cd web/frontend &amp;&amp; npm install &amp;&amp; npm run build</pre>"
                    "<p>Then restart this server. The API at "
                    "<code>POST /api/compare</code> is already live.</p></body>",
                    "text/html; charset=utf-8")
            return self._send(404, {"error": "not found"})
        ext = os.path.splitext(target)[1]
        ctype = _CONTENT_TYPES.get(ext, "application/octet-stream")
        with open(target, "rb") as fh:
            self._send(200, fh.read(), ctype)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Precog web server")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    sys.stderr.write("[web] Precog UI on http://%s:%d  (Ctrl-C to stop)\n"
                     % (args.host, args.port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\n[web] shutting down\n")
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
