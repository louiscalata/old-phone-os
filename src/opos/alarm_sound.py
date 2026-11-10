# SPDX-License-Identifier: GPL-3.0-or-later
"""Play an alarm tone and, only after a self-test file says so, a haptic pulse.

The tone is a short self-made beep. Volume steps from 20% to 70% on the paplay
stream, not the sink, so a sink-wide duck does not scale it. The player we started is stopped
by its process id. This never sets a media role of
alarm: that role crashes PulseAudio on this phone. Vibration stays off until a
self-test file exists.
"""
from pathlib import Path

TONE = Path(__file__).with_name("sounds") / "alarm.wav"
CAP = 70
START = 20
_ON_MS = 400
_OFF_MS = 600
_current = None


def levels(steps=6):
    """Volume percents from 20 up to the cap of 70."""
    if steps < 2:
        return (CAP,)
    span = CAP - START
    return tuple(START + round(span * i / (steps - 1)) for i in range(steps))


def stop(proc, spawn):
    """Stop the paplay process we started. kill(pid) is the real stop."""
    if proc is None:
        return
    pid = getattr(proc, "pid", None)
    kill = getattr(proc, "kill", None)
    if callable(kill):
        try:
            kill()
            return
        except OSError:
            pass
    if isinstance(pid, int) and pid > 0:
        spawn(["kill", "-TERM", str(pid)])


def play(spawn, steps=6):
    """Run the tone. spawn(command) may raise. The process we started is still killed."""
    global _current
    try:
        for level in levels(steps):
            if level > CAP:
                raise RuntimeError("volume")
            stop(_current, spawn)
            _current = spawn(["paplay", "--volume", str(level * 655), str(TONE)])
        return _current
    finally:
        stop(_current, spawn)
        _current = None


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
    """Start the tone for a ring and stop it for anything else. Haptics follow the self-test."""
    global _current
    spawn = spawn or _refuse
    ringing = any(getattr(event, "kind", None) == "ring" for event in events)
    if ringing:
        play(spawn)
        pulse(write or (lambda _effect: None), selftest_ok(haptic_path or ""))
    else:
        stop(_current, spawn)
        _current = None


def _refuse(_command):
    raise RuntimeError("no player")
