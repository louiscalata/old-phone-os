# SPDX-License-Identifier: GPL-3.0-or-later
"""Soft blank when the lid switch says the phone is folded.

The compositor output is left alone. Brightness goes to the floor and the poll slows.
Not measured on a phone.
"""
from __future__ import annotations

FLOOR = 3
SLOW_POLL_MS = 10000


def read_lid(text: str) -> bool | None:
    """True when the last SW_LID value is 1 (folded). None when the switch is absent."""
    last = None
    for line in text.splitlines():
        if "SW_LID" not in line:
            continue
        bits = line.replace(",", " ").split()
        if "value" in bits:
            index = bits.index("value") + 1
        elif "SW_LID" in bits:
            index = bits.index("SW_LID") + 1
        else:
            continue
        if index < len(bits) and bits[index] in ("0", "1"):
            last = bits[index] == "1"
    return last


def plan(closed, level, poll_ms):
    """What to apply. closed True is a soft blank. The compositor is not touched."""
    if closed is True:
        try:
            current = int(poll_ms)
        except (TypeError, ValueError):
            current = 0
        return {"blank": True, "brightness": FLOOR, "poll_ms": max(current, SLOW_POLL_MS), "page": "black"}
    return {"blank": False, "brightness": level, "poll_ms": poll_ms, "page": "restore"}
