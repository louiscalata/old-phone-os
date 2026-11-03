# SPDX-License-Identifier: GPL-3.0-or-later
"""On-screen button prompts for first-boot setup."""
from __future__ import annotations
PROMPTS: dict[str, str] = {
    "welcome": "Welcome to OPOS\nButtons: Tap START, or press Power",
    "kiosk": "OPOS kiosk is ON\nButtons: Tap KEEP, or press Power. Tap UNDO, or hold Volume Down",
    "done": "Setup finished\nButtons: Tap OK, or press Power",
}
def prompt(step: str) -> str:
    return PROMPTS[step]
