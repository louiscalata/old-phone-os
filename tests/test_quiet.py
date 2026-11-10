# SPDX-License-Identifier: GPL-3.0-or-later
"""Quiet hours, the notice cap, and a file that cannot be read."""
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from opos import inbox
from opos.home import is_quiet


class Quiet(unittest.TestCase):
    def test_quiet_hours_cross_midnight_and_alarms_still_ring(self):
        window = {"on": True, "from": "22:00", "to": "07:00"}
        self.assertTrue(inbox.in_quiet("23:30", window))
        self.assertTrue(inbox.in_quiet("06:00", window))
        self.assertFalse(inbox.in_quiet("12:00", window))
        night = datetime(2026, 11, 10, 23, 0)
        self.assertTrue(inbox.allows_sound("alarm", night, window))
        self.assertTrue(inbox.allows_sound("call", night, window, favorite=True))
        self.assertFalse(inbox.allows_sound("message", night, window))
        self.assertTrue(inbox.allows_sound("message", datetime(2026, 11, 10, 12, 0), window))
        self.assertTrue(is_quiet(["Alarms always ring.", "Calls from Favorites ring."]))

    def test_a_bad_time_is_refused_and_the_list_is_capped(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.environ["OPOS_STATE_DIR"] = tmp.name
        self.addCleanup(lambda: os.environ.pop("OPOS_STATE_DIR", None))
        inbox.reset()
        self.addCleanup(inbox.reset)
        with self.assertRaises(ValueError):
            inbox.set_quiet(True, "25:00", "07:00")
        for n in range(inbox.CAP + 5):
            inbox.add("system", "Notice %d" % n)
        snap = inbox.snapshot()
        self.assertEqual(len(snap["items"]), inbox.CAP)
        self.assertEqual(snap["items"][0]["text"], "Notice 5")
        path = Path(tmp.name) / "inbox.json"
        path.write_bytes(b"not json")
        inbox.reset()
        again = inbox.snapshot()
        self.assertEqual(again["items"], [])
        self.assertIn("could not be read", again["recovered"])
        self.assertTrue(any(name.startswith("inbox.json.bad-") for name in os.listdir(tmp.name)))
