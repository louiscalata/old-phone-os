# SPDX-License-Identifier: GPL-3.0-or-later
"""Play an alarm tone and, only after a self-test file says so, a haptic pulse.

The tone is a short self-made beep. A ring plays it once at each step from 20%
to 70% and waits for that beep to finish before the next louder one. That
sequence is the ramp. It is not a fade inside one stream. The paplay process is
kept and terminated when it is still running, including when a later step
raises. There is no stop command. This never sets a media role of alarm: that
role crashes PulseAudio on this phone. Vibration stays off until a self-test
file exists.
"""
import subprocess
from pathlib import Path

TONE = Path(__file__).with_name("sounds") / "alarm.wav"
CAP = 70
START = 20
STEP_S = 2
_ON_MS = 400
_OFF_MS = 600


def levels(steps=6):
    """Volume percents from 20 up to the cap of 70."""
    if steps < 2:
        return (CAP,)
    span = CAP - START
    return tuple(START + round(span * i / (steps - 1)) for i in range(steps))


def play(spawn, steps=6):
    """Play one beep per step, louder each time. spawn(command) returns a process and may raise.

    Each process is waited on, up to STEP_S seconds, before the next step. A process
    that is still running is terminated before the next step and before play returns.
    """
    proc = None
    try:
        for level in levels(steps):
            if level > CAP:
                raise RuntimeError("volume")
            stop_process(proc)
            proc = spawn(["paplay", "--volume", str(level * 655), str(TONE)])
            _wait(proc)
        return proc
    finally:
        stop_process(proc)


def _wait(proc):
    if proc is None:
        return
    wait = getattr(proc, "wait", None)
    if not callable(wait):
        return
    try:
        wait(STEP_S)
    except TypeError:
        wait()
    except subprocess.TimeoutExpired:
        return


def stop_process(proc):
    """Terminate a process that has not exited, then reap it. A finished process is left alone."""
    if proc is None or not _running(proc):
        return
    terminate = getattr(proc, "terminate", None)
    if not callable(terminate):
        return
    terminate()
    wait = getattr(proc, "wait", None)
    if not callable(wait):
        return
    try:
        wait(1)
    except TypeError:
        try:
            wait()
        except Exception:
            return
    except subprocess.TimeoutExpired:
        kill = getattr(proc, "kill", None)
        if callable(kill):
            kill()
        try:
            wait(1)
        except Exception:
            return
    except Exception:
        return


def _running(proc):
    poll = getattr(proc, "poll", None)
    if not callable(poll):
        return True
    try:
        return poll() is None
    except Exception:
        return True


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


def handle(events, spawn=None, write=None, haptic_path=None):
    """Start the ramp for a ring. Anything else stops the player that was passed in."""
    ringing = any(getattr(event, "kind", None) == "ring" for event in events)
    if ringing:
        play(spawn or _refuse)
        pulse(write or (lambda _effect: None), selftest_ok(haptic_path or ""))
        return
    stop = getattr(spawn, "stop", None)
    if callable(stop):
        stop()


def _refuse(_command):
    raise RuntimeError("no player")
