# SPDX-License-Identifier: GPL-3.0-or-later
"""The alarm tone stops even when the player raises, stays at or under 70%, and does not use the alarm media role."""
import tempfile
import unittest
from pathlib import Path

from opos import alarm_sound
from opos.alarms import Event
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


class Sound(unittest.TestCase):
    def test_volume_stays_at_or_under_the_cap(self):
        self.assertTrue(all(level <= alarm_sound.CAP for level in alarm_sound.levels()))
        self.assertEqual(alarm_sound.levels()[0], 20)
        self.assertEqual(alarm_sound.levels()[-1], 70)

    def test_stop_runs_when_the_player_raises(self):
        calls = []

        def spawn(command):
            calls.append(list(command))
            if command[0] == "paplay":
                raise RuntimeError("boom")
            return "stopped"

        with self.assertRaises(RuntimeError):
            alarm_sound.play(spawn, steps=3)
        self.assertEqual(calls[-1], ["paplay-stop"])
        volumes = [int(call[2]) for call in calls if call[0] == "paplay"]
        self.assertTrue(volumes)
        self.assertTrue(all(volume <= 70 * 655 for volume in volumes))

    def test_haptic_stays_off_without_a_self_test(self):
        seen = []
        self.assertFalse(alarm_sound.pulse(seen.append, False))
        self.assertEqual(seen, [])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "haptic"
            self.assertFalse(alarm_sound.selftest_ok(path))
            path.write_text("ok\n", encoding="utf-8")
            self.assertTrue(alarm_sound.selftest_ok(path))
            self.assertTrue(alarm_sound.pulse(seen.append, True))
        self.assertEqual(seen[0]["on_ms"], 400)
        self.assertEqual(seen[0]["off_ms"], 600)

    def test_a_ring_starts_and_a_miss_stops(self):
        calls = []
        now = datetime(2026, 11, 16, 7, 0, tzinfo=timezone.utc)
        alarm_sound.handle([Event("ring", "alarm", "a", now, "Alarm 07:00")], spawn=lambda command: calls.append(command[0]) or "ok")
        self.assertIn("paplay", calls)
        self.assertEqual(calls[-1], "paplay-stop")
        calls.clear()
        alarm_sound.handle([Event("missed", "alarm", "a", now, "Missed alarm 07:00")], spawn=lambda command: calls.append(command[0]))
        self.assertEqual(calls, ["paplay-stop"])

    def test_the_alarm_media_role_is_not_in_the_source(self):
        for path in (ROOT / "src").rglob("*"):
            if path.suffix.lower() not in {".py", ".js", ".md", ".wav"} or not path.is_file():
                continue
            data = path.read_bytes()
            self.assertNotIn(b"media.role=alarm", data, str(path))
        self.assertTrue(alarm_sound.TONE.is_file())
        self.assertLess(alarm_sound.TONE.stat().st_size, 20000)


if __name__ == "__main__":
    unittest.main()
