from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from profile_store import LEASE_SECONDS, Profile, Rejected
from server import is_loopback


class BindingTests(unittest.TestCase):
    def test_only_loopback_addresses_are_treated_as_local(self):
        for address in ("localhost", "127.0.0.1", "[::1]"):
            self.assertTrue(is_loopback(address), address)
        for address in ("0.0.0.0", "192.168.0.20", "[::]", "example.test"):
            self.assertFalse(is_loopback(address), address)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / "out"
        scratch.mkdir(exist_ok=True)
        folder = TemporaryDirectory(dir=scratch)
        self.addCleanup(folder.cleanup)
        self.location = Path(folder.name)
        self.profile = Profile(self.location)

    def query(self, operation, owner="desktop", **request):
        return self.profile.query(operation, owner, request)

    def write(self, value, revision=None, owner="desktop", slot=None):
        return self.query("write", owner, slot=slot, changes={
            "slots/slot-1.txt": {"value": value, "revision": revision},
        })

    def test_only_one_device_can_claim_one_slot(self):
        def claim(owner):
            try:
                self.query("claim", owner, slot=1)
                return owner
            except Rejected as error:
                self.assertEqual(error.status, 423)
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            owners = [owner for owner in pool.map(claim, ("desktop", "phone")) if owner]
        self.assertEqual(len(owners), 1)
        other = "desktop" if owners[0] == "phone" else "phone"
        self.assertEqual(self.query("status", other)["locked"], [1])
        self.query("release", owners[0], slot=1)
        self.query("claim", other, slot=1)

    def test_autosave_tracks_owner_and_lease_expiry_fences_old_browser(self):
        with patch("profile_store.time.time", return_value=200):
            self.query("claim", slot=3)
            self.query("autosave", slot=3, save="first")
            self.assertEqual(self.query("status", "phone")["locked"], [0, 3])
            with self.assertRaises(Rejected):
                self.query("autosave", "phone", slot=3, save="unsaved")
        with patch("profile_store.time.time", return_value=200 + LEASE_SECONDS + 1):
            self.query("claim", "phone", slot=3)
            with self.assertRaises(Rejected):
                self.query("autosave", slot=3, save="late")
            latest = self.query("autosave", "phone", slot=3, save="newer")
        self.assertEqual(latest["autoSlot"], 3)
        self.assertEqual(latest["files"]["NGUSaveSteam.txt"], "newer")

    def test_stale_changes_never_replace_a_slot(self):
        old = self.write("first")["revisions"]["slots/slot-1.txt"]
        latest = self.write("newest", revision=old, owner="phone")
        with self.assertRaises(Rejected) as conflict:
            self.write("old", revision=old)
        self.assertEqual(conflict.exception.status, 409)
        self.assertEqual(self.query("profile")["files"], latest["files"])

    def test_same_browser_can_renew_after_outage_if_slot_was_not_claimed(self):
        with patch("profile_store.time.time", return_value=200):
            self.query("claim", slot=5)
        with patch("profile_store.time.time", return_value=200 + LEASE_SECONDS + 1):
            self.assertEqual(self.query("status", "phone")["locked"], [])
            self.query("renew", slot=5)
            self.assertEqual(self.query("status", "phone")["locked"], [5])

    def test_platform_achievements_merge_without_touching_slot_revisions(self):
        self.query("write", changes={"achievements.json": {"value": "[1]"}})
        merged = self.query("write", "phone", changes={
            "achievements.json": {"value": "[2]", "revision": None},
            "slots/slot-4.txt": {"value": "save on phone", "revision": None},
        })
        self.assertEqual(merged["files"]["achievements.json"], "[1,2]")
        self.assertEqual(merged["files"]["slots/slot-4.txt"], "save on phone")

    def test_delete_releases_number_but_keeps_existing_autosave(self):
        self.query("claim", slot=1)
        current = self.write("manual", slot=1)
        self.query("autosave", slot=1, save="autosaved")
        self.query("release", slot=1)
        after = self.write(None, revision=current["revisions"]["slots/slot-1.txt"])
        self.assertEqual(after["files"]["NGUSaveSteam.txt"], "autosaved")
        self.assertNotIn("slots/slot-1.txt", after["files"])
        self.assertEqual(after["autoSlot"], 0)

    def test_flat_file_migrates_once_and_survives_restart(self):
        with TemporaryDirectory(dir=self.location) as folder:
            location = Path(folder)
            (location / "slots").mkdir()
            source = location / "slots/slot-1.txt"
            source.write_text("desktop save", encoding="utf-8")
            self.profile = Profile(location)
            imported = self.query("profile")
            self.assertEqual(imported["files"]["slots/slot-1.txt"], "desktop save")
            self.assertGreater(imported["modified"]["slots/slot-1.txt"], 0)
            self.write(None, revision=imported["revisions"]["slots/slot-1.txt"])
            self.profile = Profile(location)
            self.assertEqual(self.query("profile")["files"], {})
            self.assertEqual(source.read_text(encoding="utf-8"), "desktop save")


if __name__ == "__main__":
    unittest.main()
