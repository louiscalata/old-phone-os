# SPDX-License-Identifier: GPL-3.0-or-later
"""Play an alarm tone through paplay and, only after a self-test file says so, a haptic pulse.

The tone is a 0.4 second self-made beep. A ring plays it once per step, from 20% to 70%
of the paplay stream, waiting for each beep before the next louder one. That series is
the ramp; it is not one sound that rises. The volume is the stream's own (paplay
--volume), never the sink's, so nothing else on the phone gets louder or quieter.

The ring is bounded. A beep that has not ended after STEP_S seconds is terminated,
then killed, so a ring lasts at most worst_case_seconds(). Player.stop() may be called
from any thread: it ends the running beep and no further beep of that ring starts. A
missing paplay leaves the alarm on screen and raises nothing. This never sets a media
role of alarm: that role crashes PulseAudio on this phone. Vibration stays off until a
self-test file exists. None of this has been heard on a phone.
"""

import subprocess
import threading
from pathlib import Path

TONE = Path(__file__).with_name("sounds") / "alarm.wav"
CAP = 70
START = 20
STEP_S = 2
REAP_S = 1
_ON_MS = 400
_OFF_MS = 600


def levels(steps=6):
    """Volume percents from 20 up to the cap of 70."""
    if steps < 2:
        return (CAP,)
    span = CAP - START
    return tuple(START + round(span * i / (steps - 1)) for i in range(steps))


def worst_case_seconds(steps=6):
    """The longest one ring can play: each beep waits STEP_S, then terminate and kill wait REAP_S each."""
    return len(levels(steps)) * (STEP_S + 2 * REAP_S)


def stop_process(proc, reap_s=REAP_S):
    """End a process that is still running: terminate, then kill. Each wait is bounded. A finished one is left alone."""
    if proc is None:
        return
    try:
        if proc.poll() is not None:
            return
    except OSError:
        pass
    for end in ("terminate", "kill"):
        try:
            getattr(proc, end)()
        except OSError:
            pass
        try:
            proc.wait(timeout=reap_s)
            return
        except subprocess.TimeoutExpired:
            continue
        except OSError:
            return


class Player:
    """Starts paplay without a shell and keeps the one process it started, so stop() can end it.

    generation counts stops. A ring started with since=generation plays nothing once a stop
    has come after that value was read, even if the stop came before the first beep.
    """

    def __init__(self, popen=None, step_s=STEP_S):
        self._popen = popen or subprocess.Popen
        self.step_s = step_s
        self.lock = threading.Lock()
        self.proc = None
        self.generation = 0
        self.missing = False

    def start(self, command, since):
        """Start one beep unless a stop came after since. Returns the process, or None."""
        if not command or command[0] != "paplay":
            raise RuntimeError("no player")
        with self.lock:
            if self.generation != since:
                return None
            try:
                self.proc = self._popen(
                    list(command), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            except OSError:
                self.missing = True
                self.proc = None
            return self.proc

    def finish(self, proc):
        """Wait for one beep, at most step_s seconds, then end it if it is still running."""
        try:
            proc.wait(timeout=self.step_s)
        except subprocess.TimeoutExpired:
            pass
        except OSError:
            pass
        self._drop(proc)

    def stop(self):
        """End the running beep and every later beep of the ring that is playing. Safe from any thread."""
        with self.lock:
            self.generation += 1
            proc, self.proc = self.proc, None
        stop_process(proc)

    def _drop(self, proc):
        with self.lock:
            if self.proc is proc:
                self.proc = None
        stop_process(proc)


def play(player, steps=6, since=None):
    """Play the ramp on player. Returns the last process, or None when nothing played."""
    if not isinstance(player, Player):
        raise RuntimeError("no player")
    if since is None:
        since = player.generation
    proc = last = None
    try:
        for level in levels(steps):
            if level > CAP:
                raise RuntimeError("volume")
            proc = player.start(["paplay", "--volume", str(level * 655), str(TONE)], since)
            if proc is None:
                return last
            player.finish(proc)
            last, proc = proc, None
        return last
    finally:
        if proc is not None:
            player._drop(proc)


def pulse(write, enabled):
    """One on/off pair. enabled is false unless a self-test has passed. Nothing is written then."""
    if not enabled:
        return False
    write({"on_ms": _ON_MS, "off_ms": _OFF_MS})
    return True


def selftest_ok(path):
    """True only when the self-test file exists and its text is ok."""
    try:
        return Path(path).read_text(encoding="utf-8").strip() == "ok"
    except OSError:
        return False


def handle(events, spawn=None, write=None, haptic_path=None, since=None):
    """Play the ramp for a ring and stop the player for anything else. Haptics follow the self-test.

    spawn is a Player. With none, a ring raises "no player": nothing plays by accident.
    """
    ringing = any(getattr(event, "kind", None) == "ring" for event in events)
    if ringing:
        play(spawn, since=since)
        pulse(write or (lambda _effect: None), selftest_ok(haptic_path or ""))
    elif isinstance(spawn, Player):
        spawn.stop()
