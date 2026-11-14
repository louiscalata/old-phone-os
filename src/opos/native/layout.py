# SPDX-License-Identifier: GPL-3.0-or-later
"""The parts of the native shell that need no toolkit: the hinge gap, which hand, and what a swipe or a drag means. Pure functions."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

EDGE = 48     # a swipe that starts this close to an edge is an edge swipe (logical pixels)
FAR = 80      # how fast a swipe must be to count (the toolkit reports a velocity in pixels per second)
MIDDLE = 110  # a sideways swipe that starts this close to the hinge (or the middle of one screen) moves the buttons

# Drags that follow the finger (drag_kind and the functions after it). Travel is a fraction of the window's height.
# Following the finger is direct manipulation, not animation: each frame shows the surface where the finger has put it,
# and letting go settles in one frame (no easing, no coasting, no spring). Only the decision uses the release speed.
SLOP = 12           # the finger must move this far (logical pixels) before a drag is decided
SHEET_FULL = 0.45   # battery sheet: progress (for the release decision) is the travel over this much of the height
UNLOCK_FULL = 0.5   # lock screen: this much travel upward is a full unlock
HOME_FULL = 0.4     # bottom edge: this much travel upward is a full trip Home
SHEET_DONE = 0.35   # let go at or past this much progress and the sheet opens (or closes)
UNLOCK_DONE = 0.4   # ... and the lock screen unlocks
HOME_DONE = 0.3     # ... and the page goes Home
FLICK_MS = 150      # the release velocity is measured over the last this many milliseconds of the drag

HANDS = ("right", "left")


def hinge_px(env=None):
    """The hinge gap in logical pixels, from OPOS_SPAN="<pane width>,<gap>" as the server reads it; 0 on one screen."""
    m = re.fullmatch(r"\s*(\d+)\s*,\s*(\d+)\s*", (os.environ if env is None else env).get("OPOS_SPAN", ""))
    return int(m.group(2)) if m else 0


def swipe_action(page, shade_open, start, width, height, vx, vy, hinge=0):
    """What a finished swipe does: "shade-open", "shade-close", "home", "hand-right", "hand-left" or None.

    page is "lock", "home" or "app"; start is (x, y) where the finger went down, or None; vx and vy are the swipe's
    velocity (right and down are positive). The rules, first match wins:

      1. the shade is open          a swipe up closes it; nothing else happens under it
      2. from the top edge, down    opens the shade, on any page
      3. on the lock screen, up     unlocks (goes Home)
      4. from the bottom edge, up   goes Home, from any page that is not the lock screen
      5. sideways, from the middle  moves the buttons to the side the finger went: right = right-handed, left = left-handed
         (the middle is the hinge and MIDDLE pixels on each side of it)
      6. sideways, from an outer side edge, on an app page: goes Home

    Rule 5 is "push the buttons across the hinge"; it works the same on every page, including the lock screen.
    """
    if not start:
        return None
    x, y = start
    down, up = vy > FAR, vy < -FAR
    side = abs(vx) > abs(vy) and abs(vx) > FAR
    if shade_open:
        return "shade-close" if up else None
    if down and y < EDGE:
        return "shade-open"
    if page == "lock" and up:
        return "home"
    if page != "lock" and up and y > height - EDGE:
        return "home"
    if side and abs(x - width / 2) <= hinge / 2 + MIDDLE:
        return "hand-right" if vx > 0 else "hand-left"
    if page == "app" and side and (x < EDGE or x > width - EDGE):
        return "home"
    return None


def _clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


def drag_kind(page, sheet_open, start, width, height, dx, dy):
    """Which drag the finger is making, decided once it has moved more than SLOP pixels: "sheet-open", "sheet-close",
    "lock", "home" or None. None leaves the touch alone: taps reach the buttons and a flick is still read by swipe_action.

    page is "lock", "home" or "app"; start is (x, y) where the finger went down, or None; dx and dy are how far it has
    moved since (down is positive). Only a mostly vertical move of more than SLOP pixels is a drag. First match wins:

      1. the sheet is open           an upward drag anywhere closes it; nothing else happens under it
      2. from the top edge, down     opens the sheet, on any page
      3. on the lock screen, up      unlocks
      4. from the bottom edge, up    goes Home, from an app page (on Home there is nowhere to go)

    width is unused (no drag depends on where across the screens it starts, or on the hand); it keeps the call the
    same shape as swipe_action's.
    """
    if not start or abs(dy) <= SLOP or abs(dx) > abs(dy):
        return None
    y = start[1]
    if sheet_open:
        return "sheet-close" if dy < 0 else None
    if dy > 0:
        return "sheet-open" if y < EDGE else None
    if page == "lock":
        return "lock"
    if page == "app" and y > height - EDGE:
        return "home"
    return None


_FULL = {"sheet-open": SHEET_FULL, "sheet-close": SHEET_FULL, "lock": UNLOCK_FULL, "home": HOME_FULL}
_DONE = {"sheet-open": SHEET_DONE, "sheet-close": SHEET_DONE, "lock": UNLOCK_DONE, "home": HOME_DONE}


def drag_progress(kind, dy, height):
    """How far along the drag is, 0 to 1. The sheet opens with a downward drag; everything else is an upward one
    (closing the sheet is measured from fully open)."""
    if kind not in _FULL or height <= 0:
        return 0.0
    travel = dy if kind == "sheet-open" else -dy
    return _clamp(travel / (_FULL[kind] * height))


def drag_release(kind, progress, velocity_y):
    """What letting go does: "complete" when the drag got far enough, or was a flick faster than FAR pixels a second
    in its own direction; otherwise "cancel" (the surface is put back where it was, in the same frame)."""
    if kind not in _DONE:
        return "cancel"
    flick = velocity_y > FAR if kind == "sheet-open" else velocity_y < -FAR
    return "complete" if progress >= _DONE[kind] or flick else "cancel"


def drag_velocity(samples, window_ms=FLICK_MS):
    """Vertical velocity in pixels a second from (time_ms, dy) samples, oldest first, the last one taken when the finger
    lifted. Only the last window_ms count, so a finger that stopped before it lifted has no velocity."""
    if len(samples) < 2:
        return 0.0
    t1, y1 = samples[-1]
    recent = [s for s in samples if t1 - s[0] <= window_ms]
    t0, y0 = recent[0]
    return (y1 - y0) * 1000.0 / (t1 - t0) if t1 > t0 else 0.0


def drag_offset(kind, dy, height):
    """How far up (negative) the dragged surface is drawn while the finger holds it, in pixels. Every surface moves
    exactly as far as the finger (1:1): the sheet's bottom edge comes down with a downward drag and goes up with an
    upward one, and a page moves with the finger, never down; nothing goes past fully shown or fully hidden."""
    if kind == "sheet-open":
        return _clamp(dy - height, -float(height), 0.0)
    if kind == "sheet-close":
        return _clamp(dy, -float(height), 0.0)
    if kind in ("lock", "home"):
        return _clamp(dy, -float(height), 0.0)
    return 0.0


def pane_order(hand):
    """Left-to-right order of the two panes: the list pane (reading, choosing) and the thumb pane (buttons).

    Right-handed: the buttons are on the right screen. Left-handed: on the left screen. Nothing else changes.
    """
    return ("act", "read") if hand == "left" else ("read", "act")


def settings_path(env=None):
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config")
    return Path(base) / "opos" / "shell.json"


def load_hand(path=None, env=None):
    """The saved hand, "right" or "left". OPOS_HAND overrides the file; anything unreadable or unknown means "right"."""
    env = os.environ if env is None else env
    if env.get("OPOS_HAND") in HANDS:
        return env["OPOS_HAND"]
    try:
        hand = json.loads(Path(path or settings_path(env)).read_text(encoding="utf-8")).get("hand")
    except (OSError, ValueError, AttributeError):
        return "right"
    return hand if hand in HANDS else "right"


def save_hand(hand, path=None, env=None):
    """Write the hand to the settings file (other keys are kept). Returns True when it was written."""
    if hand not in HANDS:
        return False
    path = Path(path or settings_path(env))
    try:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        data["hand"] = hand
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        return False
