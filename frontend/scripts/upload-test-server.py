#!/usr/bin/env python3
"""Loopback HTTP fixture for CourseUploadTests' native background transport test."""
import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class UploadServer(ThreadingHTTPServer):
    def __init__(self, port):
        super().__init__(("127.0.0.1", port), UploadHandler)
        self.counts = {}
        self.lock = threading.Lock()


class UploadHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, value):
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/api/upload-test/counts":
            self.send_error(404)
            return
        with self.server.lock:
            counts = dict(self.server.counts)
        self.respond(counts)

    def do_POST(self):
        if self.path != "/api/native/uploads":
            self.send_error(404)
            return
        remaining = int(self.headers.get("Content-Length", "0"))
        if remaining <= 0:
            self.send_error(400)
            return
        while remaining:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                return
            remaining -= len(chunk)
        key = self.headers.get("X-CloudPath-Upload-ID", "unknown")
        with self.server.lock:
            self.server.counts[key] = self.server.counts.get(key, 0) + 1
        self.respond(dict(book_id="server-" + key, filename="background.pdf", status="uploaded"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18785)
    with UploadServer(parser.parse_args().port) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
