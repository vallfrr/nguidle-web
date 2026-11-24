"""SQLite store for one browser save profile."""

from contextlib import closing
import hashlib
import json
import sqlite3
import time

KEYS = tuple(f"slots/slot-{i}.txt" for i in range(1, 6)) + (
    "NGUSaveSteam.txt", "NGUSave.txt", "autosave-slot.txt", "prefs.json", "achievements.json",
)
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
            auto = self._apply_action(db, action, owner, request, now, entries, leases, holders, auto)

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

    def _apply_action(self, db, action, owner, request, now, entries, leases, holders, auto):
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
                auto = self._write_changes(db, entries, holders, owner, request, slot, auto)
        return auto

    def _write_changes(self, db, entries, holders, owner, request, slot, auto):
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
        return auto

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
