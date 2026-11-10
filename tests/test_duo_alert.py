# SPDX-License-Identifier: GPL-3.0-or-later
"""Alert playback caps volume and restores it. No media role."""
import unittest
from pathlib import Path

from opos import alarm_sound
from opos.alert import CAP, play


class Run:
    def __init__(self, ok=True, out=""):
        self.ok = ok
        self.out = out


class Alert(unittest.TestCase):
    def test_caps_at_20_and_restores_when_playback_fails(self):
        calls = []

        def runner(argv, timeout=None, stdin=None):
            calls.append(list(argv))
            if argv[0] == "paplay":
                raise OSError("no sink")
            if argv[0] == "pactl" and argv[1] == "get-sink-volume":
                return Run(True, "Volume: front-left: 65536 / 80% / -1.00 dB")
            return Run(True, "")

        with self.assertRaises(OSError):
            play(runner, "alert.wav")
        joined = " ".join(" ".join(c) for c in calls)
        self.assertNotIn("media.role", joined)
        self.assertIn(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "20%"], calls)
        self.assertEqual(calls[-1], ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "80%"])

    def test_a_quiet_volume_is_left_alone(self):
        calls = []

        def runner(argv, timeout=None, stdin=None):
            calls.append(list(argv))
            if argv[1:2] == ["get-sink-volume"]:
                return Run(True, "Volume: mono: 10%")
            return Run(True, "")

        self.assertTrue(play(runner, "alert.wav")["ok"])
        self.assertEqual([c[1] for c in calls if c[0] == "pactl"], ["get-sink-volume"])

    def test_the_20_percent_cap_is_not_the_alarm_ramp(self):
        self.assertEqual(CAP, 20)
        self.assertEqual(alarm_sound.levels()[0], 20)
        self.assertEqual(alarm_sound.levels()[-1], 70)
        self.assertGreater(alarm_sound.CAP, CAP)
        text = Path(alarm_sound.__file__).read_text(encoding="utf-8")
        self.assertNotIn("alert", text)
        alert_text = (Path(__file__).resolve().parents[1] / "src/opos/alert.py").read_text(encoding="utf-8")
        self.assertNotIn("alarm_sound", alert_text.split('"""', 2)[-1])
