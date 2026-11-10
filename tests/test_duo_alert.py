# SPDX-License-Identifier: GPL-3.0-or-later
"""Alert playback uses paplay's own volume and does not move the sink."""
import unittest
from pathlib import Path

from opos.alert import STREAM, play

ROOT = Path(__file__).resolve().parents[1]


class Run:
    def __init__(self, ok=True, out=""):
        self.ok = ok
        self.out = out


class Alert(unittest.TestCase):
    def test_volume_is_per_stream(self):
        self.assertEqual(STREAM, 13107)
        calls = []

        def runner(argv, timeout=None, stdin=None):
            calls.append((list(argv), timeout))
            return Run(True, "")

        self.assertEqual(play(runner, "alert.wav"), {"ok": True})
        self.assertEqual(calls, [(["paplay", "--volume", "13107", "alert.wav"], 5)])
        joined = " ".join(calls[0][0])
        self.assertNotIn("media.role", joined)
        self.assertNotIn("pactl", joined)

    def test_a_failed_play_does_not_move_the_sink(self):
        calls = []

        def runner(argv, timeout=None, stdin=None):
            calls.append(list(argv))
            raise OSError("no sink")

        with self.assertRaises(OSError):
            play(runner, "alert.wav")
        self.assertEqual(calls, [["paplay", "--volume", "13107", "alert.wav"]])

    def test_alarms_do_not_use_alert_play(self):
        for rel in ("src/opos/alarm_sound.py", "src/opos/clock_page.py", "src/opos/alert.py"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("set-sink-volume", text, rel)
            self.assertNotIn("alert.play", text, rel)
        alert = (ROOT / "src/opos/alert.py").read_text(encoding="utf-8")
        self.assertNotIn("pactl", alert)
        self.assertIn("--volume", alert)


if __name__ == "__main__":
    unittest.main()
