# SPDX-License-Identifier: GPL-3.0-or-later
"""Play an alarm tone and, only after a self-test file says so, a haptic pulse.

The tone is a short self-made beep. Volume steps from 20% to 70% and always stops,
including when the player raises. This never sets a media role of alarm: that role
crashes PulseAudio on this phone. Vibration stays off until a self-test file exists.
"""
from pathlib import Path

TONE = Path(__file__).with_name("sounds") / "alarm.wav"
CAP = 70
START = 20
_ON_MS = 400
_OFF_MS = 600


def levels(steps=6):
    """Volume percents from 20 up to the cap of 70."""
    if steps < 2:
        return (CAP,)
    span = CAP - START
    return tuple(START + round(span * i / (steps - 1)) for i in range(steps))


def play(spawn, steps=6):
    """Run the tone. spawn(command) may raise. The stop command still runs."""
    proc = None
    try:
        for level in levels(steps):
            if level > CAP:
                raise RuntimeError("volume")
            proc = spawn(["paplay", "--volume", str(level * 655), str(TONE)])
        return proc
    finally:
        spawn(["paplay-stop"])


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
    spawn = spawn or _refuse
    ringing = any(getattr(event, "kind", None) == "ring" for event in events)
    if ringing:
        play(spawn)
        pulse(write or (lambda _effect: None), selftest_ok(haptic_path or ""))
    else:
        spawn(["paplay-stop"])


def _refuse(_command):
    raise RuntimeError("no player")
