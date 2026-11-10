# SPDX-License-Identifier: GPL-3.0-or-later
"""A short alert through PulseAudio, capped at 20 percent, without a media role.

This is not the alarm. The cap is paplay's per-stream volume, not the sink, so an
alarm that steps from 20 percent to 70 percent is not scaled by this cap.
Not measured on a phone.
"""
from __future__ import annotations

CAP = 20


def stream_volume(percent: int) -> str:
    """paplay --volume units. 65536 would be 100 percent. This uses the same 655 step as the alarm."""
    if not isinstance(percent, int) or percent < 0 or percent > 100:
        raise ValueError("volume")
    return str(percent * 655)


def play(runner, path) -> dict:
    """Play path at the cap. The sink volume is not changed. No media.role property is set."""
    played = _call(runner, ["paplay", "--volume", stream_volume(CAP), str(path)])
    return {"ok": bool(getattr(played, "ok", False))}


def _call(runner, argv):
    if any("media.role" in part for part in argv):
        raise RuntimeError("media.role is not used")
    return runner(argv, 5, None)
