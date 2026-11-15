# SPDX-License-Identifier: GPL-3.0-or-later
"""The alarm tone: a bounded ramp of short beeps through paplay, at or under 70%, that a stop ends at once.

What is pinned here: each beep waits for the one before; a beep that does not end is terminated, then killed;
a stop from another thread ends the ramp and the running beep; a stop that comes before the ramp starts
wins; a missing paplay leaves the alarm on screen without raising; no media role, no sink volume command.
"""
import os
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from opos import alarm_sound
from opos.alarms import Event

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 16, 7, 0, tzinfo=timezone.utc)
RING = Event("ring", "alarm", "a", NOW, "Alarm 07:00")
MISSED = Event("missed", "alarm", "a", NOW, "Missed alarm 07:00")


class Proc:
    """A stand-in for a Popen. finish=False is a beep that never ends by itself."""

    def __init__(self, command, finish=True, stubborn=False):
        self.command = list(command)
        self.done = finish
        self.stubborn = stubborn
        self.terminated = False
        self.killed = False
        self.waits = []

    def poll(self):
        return 0 if self.done else None

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.done:
            return 0
        raise subprocess.TimeoutExpired(self.command, timeout)

    def terminate(self):
        self.terminated = True
        if not self.stubborn:
            self.done = True

    def kill(self):
        self.killed = True
        self.done = True


def fake_popen(made, **kw):
    def popen(command, **_options):
        proc = Proc(command, **kw)
        made.append(proc)
        return proc
    return popen


def stub_paplay(folder, body):
    script = Path(folder) / "paplay"
    script.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


class Sound(unittest.TestCase):
    def test_volume_stays_at_or_under_the_cap(self):
        self.assertTrue(all(level <= alarm_sound.CAP for level in alarm_sound.levels()))
        self.assertEqual(alarm_sound.levels()[0], 20)
        self.assertEqual(alarm_sound.levels()[-1], 70)

    def test_the_ramp_waits_for_each_beep_and_gets_louder(self):
        made = []
        player = alarm_sound.Player(popen=fake_popen(made))
        alarm_sound.play(player, steps=3)
        self.assertEqual([p.command[:3] for p in made], [
            ["paplay", "--volume", str(20 * 655)],
            ["paplay", "--volume", str(45 * 655)],
            ["paplay", "--volume", str(70 * 655)],
        ])
        self.assertTrue(all(p.command[3] == str(alarm_sound.TONE) for p in made))
        self.assertTrue(all(p.waits and p.waits[0] == alarm_sound.STEP_S for p in made))

    def test_a_beep_that_does_not_end_is_terminated_then_killed(self):
        made = []
        player = alarm_sound.Player(popen=fake_popen(made, finish=False, stubborn=True))
        alarm_sound.play(player, steps=2)
        self.assertEqual(len(made), 2)
        self.assertTrue(all(p.terminated and p.killed for p in made))
        self.assertIsNone(player.proc)

    def test_a_raise_mid_ramp_still_ends_the_running_beep(self):
        made = []
        popen = fake_popen(made, finish=False)

        def flaky(command, **options):
            if made:
                raise RuntimeError("boom")
            return popen(command, **options)

        player = alarm_sound.Player(popen=flaky)
        with self.assertRaises(RuntimeError):
            alarm_sound.play(player, steps=3)
        self.assertTrue(made[0].terminated)
        self.assertIsNone(player.proc)

    def test_a_stop_from_another_thread_ends_the_ramp(self):
        made = []
        started = threading.Event()
        popen = fake_popen(made, finish=False)

        def watched(command, **options):
            proc = popen(command, **options)
            started.set()
            return proc

        # A fake wait that honours the timeout by sleeping, so the ramp is really blocked on the beep.
        def sleepy_popen(command, **options):
            proc = watched(command, **options)
            real_wait = proc.wait

            def wait(timeout=None):
                end = time.monotonic() + (timeout or 0)
                while not proc.done and time.monotonic() < end:
                    time.sleep(0.01)
                return real_wait(timeout)

            proc.wait = wait
            return proc

        player = alarm_sound.Player(popen=sleepy_popen, step_s=5)
        ramp = threading.Thread(target=alarm_sound.play, args=(player,), kwargs={"steps": 6})
        begun = time.monotonic()
        ramp.start()
        self.assertTrue(started.wait(2))
        player.stop()
        ramp.join(3)
        self.assertFalse(ramp.is_alive())
        self.assertLess(time.monotonic() - begun, 3)
        self.assertEqual(len(made), 1)
        self.assertTrue(made[0].terminated)

    def test_a_stop_before_the_ramp_starts_wins(self):
        made = []
        player = alarm_sound.Player(popen=fake_popen(made))
        since = player.generation
        player.stop()
        alarm_sound.handle([RING], spawn=player, since=since)
        self.assertEqual(made, [])

    def test_a_later_ring_plays_after_an_earlier_stop(self):
        made = []
        player = alarm_sound.Player(popen=fake_popen(made))
        player.stop()
        alarm_sound.handle([RING], spawn=player, since=player.generation)
        self.assertEqual(len(made), len(alarm_sound.levels()))

    def test_a_missing_paplay_leaves_the_alarm_on_screen_without_raising(self):
        def missing(command, **_options):
            raise FileNotFoundError(command[0])

        player = alarm_sound.Player(popen=missing)
        self.assertIsNone(alarm_sound.play(player))
        self.assertTrue(player.missing)

    def test_a_ring_without_a_player_raises_and_a_miss_stops_the_player(self):
        with self.assertRaisesRegex(RuntimeError, "no player"):
            alarm_sound.handle([RING])
        made = []
        player = alarm_sound.Player(popen=fake_popen(made, finish=False))
        with player.lock:
            player.proc = Proc(["paplay"], finish=False)
            left = player.proc
        alarm_sound.handle([MISSED], spawn=player)
        self.assertTrue(left.terminated)
        self.assertIsNone(player.proc)

    def test_a_real_paplay_that_hangs_is_ended_within_the_bound(self):
        with tempfile.TemporaryDirectory() as folder:
            stub_paplay(folder, "exec sleep 30")
            old = os.environ.get("PATH", "")
            os.environ["PATH"] = folder + os.pathsep + old
            try:
                player = alarm_sound.Player(step_s=0.2)
                begun = time.monotonic()
                alarm_sound.play(player, steps=2)
                spent = time.monotonic() - begun
            finally:
                os.environ["PATH"] = old
        self.assertLess(spent, 2 * (0.2 + 2 * alarm_sound.REAP_S) + 1)
        self.assertIsNone(player.proc)

    def test_the_worst_case_ring_is_bounded(self):
        bound = alarm_sound.worst_case_seconds()
        self.assertEqual(bound, len(alarm_sound.levels()) * (alarm_sound.STEP_S + 2 * alarm_sound.REAP_S))
        self.assertLessEqual(bound, 30)

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

    def test_no_media_role_no_sink_volume_and_no_made_up_stop_command(self):
        for path in (ROOT / "src").rglob("*"):
            if path.suffix.lower() not in {".py", ".js", ".md", ".wav"} or not path.is_file():
                continue
            data = path.read_bytes()
            self.assertNotIn(b"media.role=alarm", data, str(path))
            self.assertNotIn(b"paplay-stop", data, str(path))
        source = Path(alarm_sound.__file__).read_text(encoding="utf-8")
        for word in ("pactl", "set-sink-volume", "DEFAULT_SINK", "shell=True"):
            self.assertNotIn(word, source)
        self.assertTrue(alarm_sound.TONE.is_file())
        self.assertLess(alarm_sound.TONE.stat().st_size, 20000)


if __name__ == "__main__":
    unittest.main()
