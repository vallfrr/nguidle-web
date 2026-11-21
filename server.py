"""Serve a single personal NGU Idle profile using the native Rust engine."""

import argparse
import base64
import binascii
import json
import mimetypes
import os
from pathlib import Path
import re
import signal
import struct
import subprocess
import tempfile
from threading import Lock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote, urlsplit


PUBLIC = Path(__file__).resolve().parent / "public"
MAX_SAVE_BYTES = 16 * 1024 * 1024
MAX_FRAME_REQUEST = 64 * 1024
STATIC = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}


def read_exact(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise OSError("The Rust game process stopped.")
    return data


class Game:
    def __init__(self, engine, dll, assets, save):
        self.command = [str(engine), "web-worker", str(dll), str(assets), str(save)]
        self.assets = assets
        self.save = save
        self.process = None
        self.lock = Lock()

    def exchange(self, request):
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            proc = self.process
            try:
                proc.stdin.write(json.dumps(request, separators=(",", ":")).encode() + b"\n")
                proc.stdin.flush()
                info_size, image_size = struct.unpack("<II", read_exact(proc.stdout, 8))
                if info_size > 8192 or image_size > 8 * 1024 * 1024:
                    raise OSError("The Rust game returned an invalid frame.")
                info = json.loads(read_exact(proc.stdout, info_size))
                return info, read_exact(proc.stdout, image_size)
            except (OSError, ValueError):
                self.close()
                raise

    def close(self):
        proc, self.process = self.process, None
        if proc is None:
            return
        proc.stdin.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        proc.stdout.close()

    def upload(self, data, raw_name, slot):
        clean = b"".join(data.split())
        try:
            if not base64.b64decode(clean, validate=True):
                raise ValueError("Choose a valid NGU Idle .txt save.")
        except binascii.Error as error:
            raise ValueError("Choose a valid NGU Idle .txt save.") from error
        folder = self.save / "import"
        folder.mkdir(exist_ok=True)
        name = re.sub(r"[^a-zA-Z0-9._-]+", "-", unquote(raw_name)).strip("-.")[:48]
        name = name.removesuffix(".txt") or "save"
        fd, temporary = tempfile.mkstemp(prefix=f"{name}-", suffix=".txt", dir=folder)
        path = Path(temporary)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(clean)
            info, _ = self.exchange({"import": {"name": path.name, "slot": slot}})
            if info.get("error"):
                raise ValueError(info["error"])
        finally:
            path.unlink(missing_ok=True)

    def slot(self, number):
        return self.save / "slots" / f"slot-{number}.txt"

    def slots(self):
        return [{"number": i, "occupied": self.slot(i).exists()} for i in range(1, 6)]

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def respond(self, code, body, media_type, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def plain(self, code, message):
        self.respond(code, message.encode(), "text/plain; charset=utf-8")

    def body(self, limit):
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError as error:
            raise ValueError("A Content-Length header is required.") from error
        if not 0 < length <= limit or self.headers.get("Transfer-Encoding"):
            raise ValueError("Request size is outside the permitted range.")
        return read_exact(self.rfile, length)

    def same_origin(self):
        origin = urlsplit(self.headers.get("Origin", ""))
        return origin.scheme in ("http", "https") and origin.netloc == self.headers.get("Host")

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in STATIC:
            file = PUBLIC / STATIC[path]
            self.respond(200, file.read_bytes(), mimetypes.guess_type(file.name)[0])
        elif path == "/healthz":
            self.plain(200, "OK\n")
        elif path in ("/audio/Fart.wav", "/audio/LongFart.wav"):
            file = self.server.game.assets / path.removeprefix("/")
            if file.is_file():
                self.respond(200, file.read_bytes(), "audio/wav")
            else:
                self.plain(404, "Audio not found.")
        elif path == "/api/slots":
            self.respond(200, json.dumps(self.server.game.slots()).encode(), "application/json")
        elif match := re.fullmatch(r"/api/slots/([1-5])", path):
            number = int(match[1])
            file = self.server.game.slot(number)
            if not file.is_file() or file.is_symlink() or file.stat().st_size > MAX_SAVE_BYTES:
                self.plain(404, "Save not found.")
                return
            self.respond(200, file.read_bytes(), "text/plain", {"Content-Disposition": f'attachment; filename="NGU-Slot-{number}.txt"'})
        else:
            self.plain(404, "Not found.")

    def do_POST(self):
        if not self.same_origin():
            self.close_connection = True
            self.plain(403, "Open the game from the same address to send input.")
            return
        path = urlsplit(self.path).path
        try:
            if path == "/api/frame":
                request = json.loads(self.body(MAX_FRAME_REQUEST))
                if not isinstance(request, dict):
                    raise ValueError("Frame request must be an object.")
                events = request.get("events")
                if not isinstance(events, list) or len(events) > 96:
                    raise ValueError("Too many input events.")
                info, image = self.server.game.exchange(request)
                if info.get("error"):
                    raise ValueError(info["error"])
                headers = {}
                if info.get("text") is not None:
                    headers["X-NGU-Text"] = quote(json.dumps(info["text"], ensure_ascii=False))
                if info.get("sounds"):
                    headers["X-NGU-Sounds"] = ",".join(info["sounds"])
                self.respond(200, image, "image/png", headers)
            elif match := re.fullmatch(r"/api/import/([1-5])", path):
                self.server.game.upload(self.body(MAX_SAVE_BYTES), self.headers.get("X-NGU-Filename", "save"), int(match[1]))
                self.plain(201, "Save imported.")
            else:
                self.close_connection = True
                self.plain(404, "Not found.")
        except (ValueError, UnicodeError) as error:
            self.close_connection = True
            self.plain(400, str(error))
        except (OSError, BrokenPipeError) as error:
            self.close_connection = True
            try:
                self.plain(503, str(error))
            except (OSError, BrokenPipeError):
                pass

    def log_request(self, code="-", size="-"):
        if urlsplit(self.path).path != "/api/frame" or code != 200:
            super().log_request(code, size)


def interrupt(_signal, _frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("engine", "dll", "assets", "save"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    engine, dll, assets, save = (getattr(args, k).resolve() for k in ("engine", "dll", "assets", "save"))
    if not engine.is_file() or not dll.is_file() or not (assets / "scene.json").is_file():
        parser.error("Provide the compiled Rust engine, original DLL, and prepared scene.")
    save.mkdir(parents=True, exist_ok=True)
    game = Game(engine, dll, assets, save)
    signal.signal(signal.SIGTERM, interrupt)
    with ThreadingHTTPServer((args.host, args.port), Handler) as server:
        server.game = game
        print(f"NGU Idle available on http://{args.host}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            game.close()


if __name__ == "__main__":
    main()
