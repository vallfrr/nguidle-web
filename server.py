"""Host the browser build and a single personal save profile."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
import json
import mimetypes
from pathlib import Path
import re
import shutil
import sqlite3
import sys
from urllib.parse import urlsplit

from profile_store import MAX_SAVE, Profile, Rejected


ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
VENDOR = ROOT / "node_modules/@bjorn3/browser_wasi_shim/dist"
STATIC = {"/": "index.html", "/app.js": "app.js", "/engine.js": "engine.js", "/saves.js": "saves.js", "/style.css": "style.css"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def headers_for(self, status, size, media, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", media)
        if status != 304:
            self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-cache" if extra else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()

    def respond(self, status, body, media="text/plain; charset=utf-8"):
        self.headers_for(status, len(body), media)
        self.wfile.write(body)

    def download(self, path):
        if not path.is_file():
            self.respond(404, b"Not found.")
            return
        media = {".wasm": "application/wasm", ".js": "text/javascript"}.get(path.suffix)
        media = media or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        headers = {}
        if path.name == "game.pak":
            headers["Vary"] = "Accept-Encoding"
            compressed = path.with_suffix(".pak.gz")
            if compressed.is_file() and re.search(r"(?:^|,)\s*gzip\s*(?:,|$)", self.headers.get("Accept-Encoding", "")):
                path = compressed
                headers["Content-Encoding"] = "gzip"
        stat = path.stat()
        tag = f'"{stat.st_size:x}-{stat.st_mtime_ns:x}"'
        headers["ETag"] = tag
        if self.headers.get("If-None-Match") == tag:
            self.headers_for(304, 0, media, headers)
            return
        self.headers_for(200, stat.st_size, media, headers)
        with path.open("rb") as source:
            shutil.copyfileobj(source, self.wfile, 256 * 1024)

    def same_origin(self):
        origin = urlsplit(self.headers.get("Origin", ""))
        return origin.scheme in ("http", "https") and origin.netloc == self.headers.get("Host")

    def owner(self, request):
        owner = request.get("owner", self.headers.get("X-NGU-Owner", ""))
        if not isinstance(owner, str) or not re.fullmatch("[0-9a-f]{48}", owner):
            raise ValueError("Invalid browser session.")
        return owner

    def api(self, action, request):
        try:
            info = self.server.profile.query(action, self.owner(request), request)
            self.respond(200, json.dumps(info, separators=(",", ":")).encode(), "application/json")
        except Rejected as error:
            self.respond(error.status, str(error).encode())
        except (ValueError, UnicodeError) as error:
            self.respond(400, str(error).encode())
        except (OSError, sqlite3.Error):
            self.respond(503, b"Save server unavailable.")

    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            if path in STATIC:
                self.download(PUBLIC / STATIC[path])
            elif path in ("/runtime.wasm", "/game.pak"):
                self.download(self.server.build / path[1:])
            elif re.fullmatch(r"/vendor/[a-z_]+\.js", path):
                self.download(VENDOR / path.rsplit("/", 1)[1])
            elif path in ("/audio/Fart.wav", "/audio/LongFart.wav") and self.server.assets:
                self.download(self.server.assets / path[1:])
            elif path == "/healthz":
                self.respond(200, b"OK\n")
            elif path in ("/api/profile", "/api/status"):
                self.api(path[5:], {})
            else:
                self.respond(404, b"Not found.")
        except (ConnectionError, TimeoutError):
            self.close_connection = True

    def do_POST(self):
        path = urlsplit(self.path).path
        if not self.same_origin():
            self.close_connection = True
            self.respond(403, b"Open the game from the same address to change saves.")
            return
        if path not in {f"/api/{action}" for action in ("claim", "release", "renew", "write", "autosave")}:
            self.close_connection = True
            self.respond(404, b"Not found.")
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
            if not 0 < length <= 2 * MAX_SAVE + 8192 or self.headers.get("Transfer-Encoding"):
                raise ValueError("Invalid request size.")
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError("Incomplete request.")
            request = json.loads(data)
            if not isinstance(request, dict):
                raise ValueError("Expected a save request object.")
            self.api(path[5:], request)
        except (ValueError, UnicodeError) as error:
            self.close_connection = True
            self.respond(400, str(error).encode())
        except (ConnectionError, TimeoutError):
            self.close_connection = True

    def log_request(self, code="-", size="-"):
        if self.path != "/api/status" or code != 200:
            super().log_request(code, size)


def is_loopback(address):
    if address.casefold() == "localhost":
        return True
    try:
        return ip_address(address.strip("[]")).is_loopback
    except ValueError:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", required=True, type=Path)
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--save", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--published-host", help="Host address published by the container runtime")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    if not all((args.build / name).is_file() for name in ("runtime.wasm", "game.pak")):
        parser.error("Build runtime.wasm and game.pak before starting the server.")
    if not (VENDOR / "index.js").is_file():
        parser.error("Install the pinned browser dependency with npm ci.")
    with ThreadingHTTPServer((args.host, args.port), Handler) as server:
        server.build, server.assets, server.profile = args.build, args.assets, Profile(args.save)
        if not is_loopback(args.published_host or args.host):
            print(
                "Warning: this server exposes a personal save profile without authentication. "
                "Use a loopback binding or restrict access with an authenticated proxy.",
                file=sys.stderr, flush=True,
            )
        print(f"NGU Idle available on http://{args.host}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
