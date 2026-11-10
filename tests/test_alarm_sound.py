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

    def test_stop_kills_the_tracked_paplay_when_the_player_raises(self):
        calls = []
        killed = []

        class Proc:
            pid = 4242

            def kill(self):
                killed.append(self.pid)

        def spawn(command):
            calls.append(list(command))
            if command[0] == "paplay":
                if sum(1 for call in calls if call[0] == "paplay") > 1:
                    raise RuntimeError("boom")
                return Proc()
            return None

        with self.assertRaises(RuntimeError):
            alarm_sound.play(spawn, steps=3)
        self.assertEqual(killed, [4242, 4242])
        self.assertFalse(any(call[0] == "paplay-stop" for call in calls))
        volumes = [int(call[2]) for call in calls if call[0] == "paplay"]
        self.assertTrue(volumes)
        self.assertTrue(all(volume <= 70 * 655 for volume in volumes))

    def test_a_kill_that_fails_uses_the_process_id(self):
        calls = []

        class Proc:
            pid = 77

            def kill(self):
                raise OSError("busy")

        def spawn(command):
            calls.append(list(command))
            if command[0] == "paplay":
                return Proc()
            return None

        alarm_sound.play(spawn, steps=2)
        self.assertIn(["kill", "-TERM", "77"], calls)

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

    def test_a_ring_starts_and_a_miss_kills_the_tracked_player(self):
        calls = []
        killed = []

        class Proc:
            pid = 55

            def kill(self):
                killed.append(self.pid)

        now = datetime(2026, 11, 16, 7, 0, tzinfo=timezone.utc)

        def spawn(command):
            calls.append(command[0])
            if command[0] == "paplay":
                return Proc()
            return None

        alarm_sound.handle([Event("ring", "alarm", "a", now, "Alarm 07:00")], spawn=spawn)
        self.assertIn("paplay", calls)
        self.assertIn(55, killed)
        self.assertIsNone(alarm_sound._current)
        left = Proc()
        alarm_sound._current = left
        before = len(killed)
        alarm_sound.handle([Event("missed", "alarm", "a", now, "Missed alarm 07:00")], spawn=spawn)
        self.assertEqual(len(killed), before + 1)
        self.assertIsNone(alarm_sound._current)

    def test_the_alarm_media_role_is_not_in_the_source(self):
        for path in (ROOT / "src").rglob("*"):
            if path.suffix.lower() not in {".py", ".js", ".md", ".wav"} or not path.is_file():
                continue
            data = path.read_bytes()
            self.assertNotIn(b"media.role=alarm", data, str(path))
            self.assertNotIn(b"paplay-stop", data, str(path))
        self.assertTrue(alarm_sound.TONE.is_file())
        self.assertLess(alarm_sound.TONE.stat().st_size, 20000)


if __name__ == "__main__":
    unittest.main()
