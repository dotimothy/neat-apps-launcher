#!/usr/bin/env python3
"""A tiny stand-in for neat-insight (plain HTTP) with the API the launcher uses."""
import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=9900)
args = ap.parse_args()

SOURCES = [{"index": i, "file": "", "state": "stopped"} for i in range(1, 49)]
VIDEOS: list[str] = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, obj, code=200, ctype="application/json"):
        body = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/health":
            return self.send({"status": "ok", "service": "neat-insight"})
        if u.path == "/api/mediasrc":
            return self.send([{**s, "urls": {"rtsp": f"rtsp://127.0.0.1:8554/src{s['index']}"}} for s in SOURCES])
        if u.path == "/api/mediasrc/videos":
            return self.send(sorted(VIDEOS))
        if u.path == "/api/viewer-url":
            src = parse_qs(u.query).get("src", ["0"])[0]
            return self.send({"url": f"http://127.0.0.1:{args.port}/viewer?src={src}"})
        if u.path == "/api/ingest/stats":
            return self.send({"channels": []})
        if u.path in ("/", "/viewer"):
            return self.send(f"<h1>Fake Insight</h1><p>{self.path}</p>", ctype="text/html")
        return self.send({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        raw = self.body()
        if u.path == "/api/upload/media":
            m = re.search(rb'filename="([^"]+)"', raw)
            name = m.group(1).decode() if m else ""
            if not name:
                return self.send("No file provided.\n", ctype="text/plain")
            if name not in VIDEOS:
                VIDEOS.append(name)
            return self.send(f"Uploaded to /media/{name}\nUpload complete.\n", ctype="text/plain")
        data = json.loads(raw or b"{}")
        src = next((s for s in SOURCES if s["index"] == data.get("index")), None)
        if u.path == "/api/mediasrc/assign":
            if src is None:
                return self.send({"error": "Source not found"}, 404)
            src["file"], src["state"] = data.get("file", ""), "stopped"
            return self.send({"success": True})
        if u.path == "/api/mediasrc/start":
            if src is None or not src["file"]:
                return self.send({"error": "No file assigned to source"}, 400)
            src["state"] = "playing"
            return self.send({"success": True})
        if u.path == "/api/mediasrc/stop":
            if src:
                src["state"] = "stopped"
            return self.send({"success": True})
        return self.send({"error": "not found"}, 404)


ThreadingHTTPServer(("127.0.0.1", args.port), H).serve_forever()
