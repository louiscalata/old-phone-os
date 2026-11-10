# SPDX-License-Identifier: GPL-3.0-or-later
"""A short alert through PulseAudio at 20 percent of that one stream.

paplay is given --volume. The sink volume is never changed, so other streams
keep their level. Alarms do not use this module. No media role is set.
Not measured on a phone.
"""
from __future__ import annotations

CAP = 20
# paplay treats 65536 as 100 percent of this stream.
STREAM = 65536 * CAP // 100


def play(runner, path) -> dict:
    """Play path at 20 percent stream volume. The sink is not moved."""
    played = _call(runner, ["paplay", "--volume", str(STREAM), str(path)])
    return {"ok": bool(getattr(played, "ok", False))}


def _call(runner, argv):
    if any("media.role" in part for part in argv):
        raise RuntimeError("media role is not used")
    return runner(argv, 5, None)
