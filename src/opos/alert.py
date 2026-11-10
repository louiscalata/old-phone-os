# SPDX-License-Identifier: GPL-3.0-or-later
"""A short alert through PulseAudio, capped at 20 percent, without a media role.

This is not the alarm. Alarms step from 20 percent to 70 percent in opos.alarm_sound.
The previous volume is put back in a finally block, including when playback fails.
Not measured on a phone.
"""
from __future__ import annotations

import re

CAP = 20
_PERCENT = re.compile(r"(\d+)\s*%")


def percent_from(text: str) -> int | None:
    found = _PERCENT.findall(text or "")
    if not found:
        return None
    value = int(found[0])
    if value < 0 or value > 100:
        return None
    return value


def play(runner, path) -> dict:
    """Play path. No media.role property is set. Volume is restored afterwards."""
    before = _call(runner, ["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
    current = percent_from(getattr(before, "out", "") or "") if getattr(before, "ok", False) else None
    changed = False
    try:
        if current is not None and current > CAP:
            lowered = _call(runner, ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{CAP}%"])
            changed = bool(getattr(lowered, "ok", False))
        played = _call(runner, ["paplay", str(path)])
        return {"ok": bool(getattr(played, "ok", False))}
    finally:
        if changed and current is not None:
            _call(runner, ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{current}%"])


def _call(runner, argv):
    if any("media.role" in part for part in argv):
        raise RuntimeError("media.role is not used")
    return runner(argv, 5, None)
