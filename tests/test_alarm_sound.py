# SPDX-License-Identifier: GPL-3.0-or-later
"""The alarm tone waits between volume steps, stops by terminating paplay, and stays at or under 70%."""
import os
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from opos import alarm_sound, clock_page
from opos.alarms import Event

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 16, 7, 0, tzinfo=timezone.utc)


class Proc:
    def __init__(self, level, finish=True):
        self.level = level
        self.finish = finish
        self.terminated = False
        self.killed = False
        self.waits = 0

    def wait(self, timeout=None):
        self.waits += 1
        if self.terminated or self.finish:
            self.finish = True
            return 0
        raise subprocess.TimeoutExpired(["paplay"], timeout)

    def poll(self):
        return 0 if self.finish or self.terminated else None

    def terminate(self):
        self.terminated = True
        self.finish = True

    def kill(self):
        self.killed = True
        self.finish = True


class Sound(unittest.TestCase):
    def test_volume_stays_at_or_under_the_cap(self):
        self.assertTrue(all(level <= alarm_sound.CAP for level in alarm_sound.levels()))
        self.assertEqual(alarm_sound.levels()[0], 20)
        self.assertEqual(alarm_sound.levels()[-1], 70)

    def test_the_ramp_waits_for_each_beep_before_the_next(self):
        order = []

        def spawn(command):
            level = int(command[2])
            order.append(("start", level))
            proc = Proc(level)
            real_wait = proc.wait

            def wait(timeout=None):
                order.append(("wait", level))
                return real_wait(timeout)

            proc.wait = wait
            return proc

        alarm_sound.play(spawn, steps=3)
        self.assertEqual(order, [
            ("start", 20 * 655), ("wait", 20 * 655),
            ("start", 45 * 655), ("wait", 45 * 655),
            ("start", 70 * 655), ("wait", 70 * 655),
        ])
        self.assertNotIn("paplay-stop", Path(alarm_sound.__file__).read_text(encoding="utf-8"))

    def test_a_running_step_is_terminated_when_a_later_step_raises(self):
        procs = []

        def spawn(command):
            if procs:
                raise RuntimeError("boom")
            proc = Proc(int(command[2]), finish=False)
            procs.append(proc)
            return proc

        with self.assertRaises(RuntimeError):
            alarm_sound.play(spawn, steps=3)
        self.assertTrue(procs[0].terminated)
        self.assertNotIn(b"paplay-stop", Path(alarm_sound.__file__).read_bytes())

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

    def test_a_ring_without_a_player_raises_and_a_miss_stops_the_one_it_was_given(self):
        with self.assertRaises(RuntimeError) as caught:
            alarm_sound.handle([Event("ring", "alarm", "a", NOW, "Alarm 07:00")])
        self.assertEqual(str(caught.exception), "no player")

        class Player:
            def __init__(self):
                self.commands = []
                self.proc = None
                self.stopped = 0

            def __call__(self, command):
                self.commands.append(list(command))
                self.proc = Proc(int(command[2]), finish=False)
                return self.proc

            def stop(self):
                self.stopped += 1
                alarm_sound.stop_process(self.proc)

        player = Player()
        alarm_sound.handle([Event("ring", "alarm", "a", NOW, "Alarm 07:00")], spawn=player)
        self.assertGreater(len(player.commands), 1)
        self.assertTrue(all(command[0] == "paplay" and "media.role" not in " ".join(command) for command in player.commands))
        self.assertLess(int(player.commands[0][2]), int(player.commands[-1][2]))
        alarm_sound.handle([Event("missed", "alarm", "a", NOW, "Missed alarm 07:00")], spawn=player)
        self.assertEqual(player.stopped, 1)

    def test_the_alarm_media_role_is_not_in_the_source(self):
        for path in (ROOT / "src").rglob("*"):
            if path.suffix.lower() not in {".py", ".js", ".md", ".wav"} or not path.is_file():
                continue
            data = path.read_bytes()
            self.assertNotIn(b"media.role=alarm", data, str(path))
        self.assertTrue(alarm_sound.TONE.is_file())
        self.assertLess(alarm_sound.TONE.stat().st_size, 20000)


class ClockThread(unittest.TestCase):
    def tearDown(self):
        thread = clock_page._THREAD
        clock_page._THREAD = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
        clock_page.reset()

    def test_the_unpatched_clock_thread_starts_paplay_for_a_ring(self):
        """The thread calls the real player. Nothing here replaces handle or hands it a fake spawn."""
        started = []

        def popen(argv, **_kwargs):
            started.append(list(argv))
            return Proc(int(argv[2]))

        def sleep(_seconds):
            sleep.calls += 1
            if sleep.calls >= 2:
                raise RuntimeError("stop the loop")

        def quiet(args):
            if str(args.exc_value) != "stop the loop":
                raise args.exc_value

        sleep.calls = 0
        early = datetime(2026, 11, 16, 6, 59, tzinfo=timezone.utc)
        fire = datetime(2026, 11, 16, 7, 0, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as folder:
            env = {"OPOS_STATE_DIR": folder, "OPOS_CLOCK_ZONE": "UTC", "OPOS_CLOCK_OK": "1", "OPOS_CLOCK_TEST": "1"}
            with mock.patch.dict(os.environ, env):
                clock_page.reset()
                clock_page._THREAD = None
                clock_page._FAKE = early
                clock_page.book().add(7, 0, (), "Kitchen", early)
                clock_page._FAKE = fire
                with mock.patch.object(clock_page.subprocess, "Popen", popen), mock.patch.object(clock_page.time, "sleep", sleep), mock.patch.object(threading, "excepthook", quiet):
                    clock_page.start_thread()
                    clock_page._THREAD.join(timeout=2)
        self.assertFalse(clock_page._THREAD.is_alive())
        self.assertTrue(started)
        self.assertTrue(all(command[0] == "paplay" and command[1] == "--volume" for command in started))
        volumes = [int(command[2]) for command in started]
        self.assertEqual(volumes, [level * 655 for level in alarm_sound.levels()])
        self.assertLess(volumes[0], volumes[-1])
        self.assertTrue(all(command[-1].endswith("alarm.wav") for command in started))
        self.assertNotIn("paplay-stop", [part for command in started for part in command])


if __name__ == "__main__":
    unittest.main()
