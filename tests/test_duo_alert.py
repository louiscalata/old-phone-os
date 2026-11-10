# SPDX-License-Identifier: GPL-3.0-or-later
"""Alert playback caps one stream at 20 percent. It does not duck the sink the alarm uses."""
import unittest
from pathlib import Path

from opos import alarm_sound
from opos.alert import CAP, play, stream_volume


class Run:
    def __init__(self, ok=True, out=""):
        self.ok = ok
        self.out = out


class Alert(unittest.TestCase):
    def test_a_failed_play_does_not_change_the_sink(self):
        calls = []

        def runner(argv, timeout=None, stdin=None):
            calls.append(list(argv))
            if argv[0] == "paplay":
                raise OSError("no sink")
            return Run(True, "Volume: front-left: 65536 / 80%")

        with self.assertRaises(OSError):
            play(runner, "alert.wav")
        joined = " ".join(" ".join(c) for c in calls)
        self.assertNotIn("media.role", joined)
        self.assertNotIn("set-sink-volume", joined)
        self.assertEqual(calls, [["paplay", "--volume", stream_volume(CAP), "alert.wav"]])

    def test_the_20_percent_cap_is_not_the_alarm_ramp(self):
        self.assertEqual(CAP, 20)
        self.assertEqual(stream_volume(CAP), str(20 * 655))
        self.assertEqual(alarm_sound.levels()[0], 20)
        self.assertEqual(alarm_sound.levels()[-1], 70)
        self.assertGreater(alarm_sound.CAP, CAP)
        text = Path(alarm_sound.__file__).read_text(encoding="utf-8")
        self.assertNotIn("alert", text)

    def test_an_alarm_is_not_scaled_by_the_alert_cap(self):
        alert_calls = []

        def runner(argv, timeout=None, stdin=None):
            alert_calls.append(list(argv))
            return Run(True, "")

        self.assertTrue(play(runner, "alert.wav")["ok"])
        alarm_calls = []

        class Proc:
            pid = 3

            def kill(self):
                return None

        def spawn(command):
            alarm_calls.append(list(command))
            return Proc()

        alarm_sound.play(spawn, steps=2)
        volumes = [int(call[2]) for call in alarm_calls if call[0] == "paplay"]
        self.assertEqual(volumes[0], 20 * 655)
        self.assertEqual(volumes[-1], 70 * 655)
        ducked = int(70 * 655 * CAP / 100)
        self.assertNotIn(ducked, volumes)
        joined = " ".join(" ".join(call) for call in alert_calls + alarm_calls)
        self.assertNotIn("set-sink-volume", joined)


if __name__ == "__main__":
    unittest.main()
