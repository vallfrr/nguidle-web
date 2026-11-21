"""Host the browser build and a single personal save profile."""

import argparse
from contextlib import closing
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import shutil
import sqlite3
import time
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
VENDOR = ROOT / "node_modules/@bjorn3/browser_wasi_shim/dist"
KEYS = tuple(f"slots/slot-{i}.txt" for i in range(1, 6)) + (
    "NGUSaveSteam.txt", "NGUSave.txt", "autosave-slot.txt", "prefs.json", "achievements.json",
)
STATIC = {"/": "index.html", "/app.js": "app.js", "/engine.js": "engine.js", "/saves.js": "saves.js", "/style.css": "style.css"}
MAX_SAVE = 16 * 1024 * 1024
LEASE_SECONDS = 120


class Rejected(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Profile:
    def __init__(self, location):
        location.mkdir(parents=True, exist_ok=True)
        self.database = location / "browser-profile.sqlite3"
        with closing(self.connect()) as db, db:
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS files (name TEXT PRIMARY KEY, value TEXT)")
            if "modified" not in {row[1] for row in db.execute("PRAGMA table_info(files)")}:
                db.execute("ALTER TABLE files ADD COLUMN modified INTEGER NOT NULL DEFAULT 0")
            db.execute("CREATE TABLE IF NOT EXISTS leases (slot INTEGER PRIMARY KEY, owner TEXT, until REAL)")
            db.execute("CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY)")
            if db.execute("INSERT OR IGNORE INTO settings VALUES ('legacy-imported')").rowcount:
                legacy = [location / name for name in ("NGUSaveSteam.txt", "NGUSave.txt")]
                valid = [path for path in legacy if path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_SAVE]
                autosave = max(valid, key=lambda path: path.stat().st_mtime_ns, default=None)
                for name in KEYS:
                    path = location / name
                    if path in legacy and path != autosave:
                        continue
                    if path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_SAVE:
                        db.execute("INSERT INTO files VALUES (?, ?, ?)", (
                            name, path.read_text(encoding="utf-8-sig"), path.stat().st_mtime_ns // 1_000_000,
                        ))

    def connect(self):
        return sqlite3.connect(self.database, timeout=10)

    def query(self, action, owner, request):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            entries = dict(db.execute("SELECT name, value FROM files"))
            leases = {slot: (holder, until) for slot, holder, until in db.execute("SELECT slot, owner, until FROM leases")}
            holders = {slot: holder for slot, (holder, until) in leases.items() if until > now}
            auto = self.auto_slot(entries)
            if action in ("claim", "release", "renew", "write", "autosave"):
                slot = request.get("slot")
                if (action != "write" or slot is not None) and (type(slot) is not int or not 0 <= slot <= 5):
                    raise ValueError("Choose a valid slot.")
                if action == "claim":
                    if holders.get(slot, owner) != owner:
                        raise Rejected(423, "This save is in use on another device.")
                    db.execute("INSERT OR REPLACE INTO leases VALUES (?, ?, ?)", (slot, owner, now + LEASE_SECONDS))
                    holders[slot] = owner
                elif action == "release":
                    if leases.get(slot, (None, 0))[0] == owner:
                        db.execute("DELETE FROM leases WHERE slot = ?", (slot,))
                        holders.pop(slot, None)
                elif action in ("renew", "autosave"):
                    # A disconnected tab can renew after expiry if nobody else claimed the slot.
                    previous_owner = leases.get(slot, (None, 0))[0]
                    if (previous_owner if action == "renew" else holders.get(slot)) != owner:
                        raise Rejected(423, "The save lock expired or another device took it.")
                    db.execute("UPDATE leases SET until = ? WHERE slot = ?", (now + LEASE_SECONDS, slot))
                    holders[slot] = owner
                    if action == "autosave":
                        value = self.validate(request.get("save"))
                        if not value:
                            raise ValueError("Autosave is empty.")
                        metadata = f"{slot}\n{hashlib.md5(value.encode()).hexdigest()}"
                        self.put(db, entries, "NGUSaveSteam.txt", value)
                        self.put(db, entries, "NGUSave.txt", None)
                        self.put(db, entries, "autosave-slot.txt", metadata)
                        auto = slot
                else:
                    if slot is not None and holders.get(slot) != owner:
                        raise Rejected(423, "The save lock expired or another device took it.")
                    changes = request.get("changes")
                    if not isinstance(changes, dict) or not changes or len(changes) > len(KEYS):
                        raise ValueError("Invalid save changes.")
                    for name, change in changes.items():
                        if name not in KEYS or not isinstance(change, dict):
                            raise ValueError("Invalid save file.")
                        if name.startswith("slots/"):
                            target = int(name[11])
                            if holders.get(target, owner) != owner:
                                raise Rejected(423, "This save is in use on another device.")
                        elif name not in ("prefs.json", "achievements.json"):
                            raise ValueError("Use the autosave operation for the autosave.")
                        current = entries.get(name)
                        stale = change.get("revision") != self.revision(current)
                        if stale and name == "prefs.json":
                            continue
                        if stale and name != "achievements.json":
                            raise Rejected(409, "The save was changed on another device. Open the slot again.")
                        value = self.validate(change.get("value"))
                        if name == "achievements.json":
                            value = self.merge_achievements(current, value)
                        self.put(db, entries, name, value)
                        if name.startswith("slots/") and value is None and target == auto:
                            save = entries.get("NGUSaveSteam.txt") or entries.get("NGUSave.txt")
                            self.put(db, entries, "autosave-slot.txt", "0\n" + hashlib.md5(save.encode()).hexdigest())
                            auto = 0

            locked = {slot for slot, holder in holders.items() if holder != owner}
            if auto in locked:
                locked.add(0)
            modified = {name: stamp or int(now * 1000) for name, stamp in db.execute("SELECT name, modified FROM files")}
            version = hashlib.sha256(json.dumps(sorted(modified.items())).encode()).hexdigest()
            if action == "status":
                return {"locked": sorted(locked), "autoSlot": auto, "version": version}
            return {
                "locked": sorted(locked), "autoSlot": auto,
                "files": entries,
                "modified": modified, "version": version,
                "revisions": {name: self.revision(value) for name, value in entries.items()},
            }

    @staticmethod
    def auto_slot(entries):
        save = entries.get("NGUSaveSteam.txt") or entries.get("NGUSave.txt")
        if not save:
            return 0
        metadata = entries.get("autosave-slot.txt", "").splitlines()
        if len(metadata) == 2 and metadata[0] in "0 1 2 3 4 5".split():
            if metadata[1] == hashlib.md5(save.encode()).hexdigest():
                return int(metadata[0])
        return 0

    @staticmethod
    def validate(value):
        if value is not None and (not isinstance(value, str) or len(value.encode()) > MAX_SAVE):
            raise ValueError("Invalid save size.")
        return value

    @staticmethod
    def merge_achievements(previous, saved):
        ids = set()
        for raw in (previous, saved):
            if raw is None:
                continue
            values = json.loads(raw)
            if not isinstance(values, list) or any(type(value) is not int for value in values):
                raise ValueError("Invalid achievements file.")
            ids.update(values)
        return json.dumps(sorted(ids), separators=(",", ":"))

    @staticmethod
    def revision(value):
        return hashlib.sha256(value.encode()).hexdigest() if value is not None else None

    @staticmethod
    def put(db, entries, name, value):
        if value is None:
            db.execute("DELETE FROM files WHERE name = ?", (name,))
            entries.pop(name, None)
        else:
            db.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?)", (name, value, time.time_ns() // 1_000_000))
            entries[name] = value


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", required=True, type=Path)
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--save", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    if not all((args.build / name).is_file() for name in ("runtime.wasm", "game.pak")):
        parser.error("Build runtime.wasm and game.pak before starting the server.")
    if not (VENDOR / "index.js").is_file():
        parser.error("Install the pinned browser dependency with npm ci.")
    with ThreadingHTTPServer((args.host, args.port), Handler) as server:
        server.build, server.assets, server.profile = args.build, args.assets, Profile(args.save)
        print(f"NGU Idle available on http://{args.host}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
