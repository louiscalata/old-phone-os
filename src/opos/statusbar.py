# SPDX-License-Identifier: GPL-3.0-or-later
"""What the status bar says about the phone's own facts (interface plan W1-05, W1-06).

web.Phone.state() calls apply() once, at the end: the logic lives here so web.py keeps a one-line hook. The page draws status_note in
the status bar's network slot unless a call is on (then it says "In call").
"""


def note(phone):
    """The one thing the bar must not leave unsaid, most important first, or "" when there is none.

    A simulated battery is never presented as the phone's; a live battery that cannot be read says so; a clock that is only a
    guess (opos.web.clock_ok) says so. The page itself says "Offline" only when none of these applies.
    """
    if not getattr(phone, "live", False):
        return "Simulated battery"
    if getattr(getattr(phone, "sim", None), "source", "reading") is None:
        return "Battery not readable"
    if not getattr(phone, "clock_ok", True):
        return "Clock not set"
    return ""


def apply(phone, out):
    """Add the status bar's fields to a state dict."""
    out["status_note"] = note(phone)
