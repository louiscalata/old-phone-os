# SPDX-License-Identifier: GPL-3.0-or-later
"Build home screens and check quiet output."
import re
BANNED = ("ai","model","governor","assistant")
def home_screen(level, decision, visible=False):
    """Build home-screen lines from a governor decision."""
    lines = []
    lines.append("Calls")
    lines.append("Messages")
    seen = {"calls","messages"}
    for t in decision.allowed:
        tl = t.lower()
        if tl in seen:
            continue
        lines.append(t.capitalize())
        seen.add(tl)
    if decision.deferred:
        names = ", ".join(str(t) for t in decision.deferred)
        lines.append(f"Paused to save power: {names}")
    if visible:
        lines.append(f"Helper: {plain_reason(decision.reason)}")
    return lines
def plain_reason(reason):
    """Say who made the decision in words an owner understands."""
    if reason.startswith("router+glimmer"):
        return "Muse Glimmer paused extra tasks"
    if reason == "rules" or reason.startswith("router"):
        return "decided by the battery rules"
    return reason
def is_quiet(lines):
    """Check whether lines exclude banned words."""
    pattern = re.compile(r'\b(' + '|'.join(BANNED) + r')\b', re.I)
    for line in lines:
        if pattern.search(line):
            return False
    return True
