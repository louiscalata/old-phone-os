# SPDX-License-Identifier: GPL-3.0-or-later
"""Which screen faces the user while the Duo is folded back. No toolkit, no clock of its own.

Folded back (hinge about 360 degrees) the two screens face opposite ways. The phone's accelerometer sits in the RIGHT
half (peer-measured by the PC session on one phone, 2026-11-15, not reproduced from the Mac): its z axis points out
of that half's screen and reads about +1024 (1 g, in 1/1024 g) when that screen faces up and about -1024 when it faces down. So z above 0.35 g means the right screen
is the one facing up or toward the user, and z below -0.35 g means the left one is. In between (held upright, z about
0) it cannot tell, and the screen the owner touched last stays. A verdict must hold for HOLD_S seconds before it
replaces the one in force, so moving the phone about does not flip the screens.
"""
from __future__ import annotations

G = 1024.0                  # one g in the sensor's units
THRESHOLD = 0.35 * G
HOLD_S = 0.8
LEFT, RIGHT = "left", "right"


def verdict(z):
    """RIGHT, LEFT, or None (cannot tell) for one accelerometer z reading; anything that is not a finite number is None."""
    if isinstance(z, bool) or not isinstance(z, (int, float)) or z != z or abs(z) == float("inf"):
        return None
    if z > THRESHOLD:
        return RIGHT
    if z < -THRESHOLD:
        return LEFT
    return None


class Facing:
    """The screen to keep lit, from accelerometer readings fed with their times; `fallback` is where it stays when the
    sensor cannot tell (the screen last touched, set by the shell). now() and update() take a time in seconds."""

    def __init__(self, fallback=None, hold_s=HOLD_S):
        self.hold_s, self.fallback = hold_s, fallback
        self.current = None         # the verdict in force: LEFT, RIGHT or None
        self._candidate, self._since = None, 0.0

    def update(self, z, now):
        """Feed one reading taken at `now`; returns the screen to keep lit (the fallback while there is no verdict)."""
        v = verdict(z)
        if v is None:
            self._candidate = None      # an unclear reading breaks a run, but the verdict in force stays
        elif v == self.current:
            self._candidate = None
        elif v != self._candidate:
            self._candidate, self._since = v, now
        elif now - self._since >= self.hold_s:
            self.current, self._candidate = v, None
        return self.screen()

    def screen(self):
        return self.current or self.fallback

    def reset(self):
        """Forget the verdict (the phone left the folded-back posture or the sensor was lost)."""
        self.current, self._candidate = None, None
