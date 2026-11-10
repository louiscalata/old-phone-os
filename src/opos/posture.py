# SPDX-License-Identifier: GPL-3.0-or-later
"""Hinge posture from an angle in degrees.

Bands are closed, peek, book, flat, tent, and folded-back. A reading must leave the
current band by more than 10 degrees before the name changes, so a small jitter does not flap.
OPOS_POSTURE=1 turns the reader on. The image does not start sensorfwd unless that flag is set
at build time. Not measured on a phone.
"""
from __future__ import annotations

HYSTERESIS = 10
# Inclusive degree ranges. Gaps belong to the nearer band when no state is held yet.
BANDS = (
    ("closed", 0, 20),
    ("peek", 30, 60),
    ("book", 80, 130),
    ("flat", 150, 190),
    ("tent", 220, 280),
    ("folded-back", 300, 360),
)


def enabled(env) -> bool:
    return env.get("OPOS_POSTURE") == "1"


def _wrap(angle: float) -> float:
    return float(angle) % 360.0


def _band(angle: float) -> str:
    angle = _wrap(angle)
    for name, lo, hi in BANDS:
        if lo <= angle <= hi:
            return name
    best = None
    best_distance = 999.0
    for name, lo, hi in BANDS:
        if angle < lo:
            distance = lo - angle
        elif angle > hi:
            distance = angle - hi
        else:
            distance = 0.0
        if distance < best_distance:
            best = name
            best_distance = distance
    return best or "closed"


class Posture:
    def __init__(self) -> None:
        self.state: str | None = None

    def update(self, angle: float) -> str:
        angle = _wrap(angle)
        if self.state is None:
            self.state = _band(angle)
            return self.state
        lo, hi = next((lo, hi) for name, lo, hi in BANDS if name == self.state)
        if lo - HYSTERESIS <= angle <= hi + HYSTERESIS:
            return self.state
        self.state = _band(angle)
        return self.state


def replay(angles) -> list[str]:
    """The posture name after each angle, in order."""
    holder = Posture()
    return [holder.update(angle) for angle in angles]
