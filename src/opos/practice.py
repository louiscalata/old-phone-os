# SPDX-License-Identifier: GPL-3.0-or-later
"""Practice mode: the words and the one rule that keep the simulated phone honest.

The Duo OS image has no radio behind the page. Phone.modem is a ScreenModem, so a call "connects" and a text "is
sent" only inside the server. While that is so, the page must say practice, not "In call" or "Message sent", and it
must never pretend to reach an emergency number. web.py keeps one-line hooks into this module (notice_for, Phone.call,
Phone.state); everything here is plain text and arithmetic.
"""
from opos.essentials import normalize_number
from opos.ui import ScreenModem

CALL = "Practice call. No real call is made."
NOTICES = {"call ended": "Practice call ended.", "sent": "Practice message. Nothing was sent."}
REFUSED = "error: this phone cannot call emergency numbers. Use another phone."
HOME = "Practice mode: calls, texts and tasks are simulated."
HOME_LIVE = "Calls and messages are on."
MAX_SHORT_DIGITS = 4  # 911, 112, 999, 1234: a number this short without a plus is a service number, not a phone number


def simulated(essentials):
    """True while the modem is the in-memory ScreenModem: calls and texts go nowhere. Any other modem, or none, is not."""
    return type(getattr(essentials, "modem", None)) is ScreenModem


def notice(status, in_call, sim):
    """The practice wording for a call or a text, or "" when the ordinary notice stands (not simulated, or nothing to say).

    Called after errors have been handled, so an error always wins over "in call".
    """
    if not sim:
        return ""
    if in_call:
        return CALL
    return NOTICES.get(status, "")


def refuses(number):
    """True for a valid number of four digits or fewer with no leading plus: the practice phone will not dial it.

    A number normalize_number refuses is not decided here, so it keeps its own "Number not valid".
    """
    try:
        cleaned = normalize_number(number)
    except ValueError:
        return False
    return not cleaned.startswith("+") and len(cleaned) <= MAX_SHORT_DIGITS


def _known(value):
    """The home card's rule for a name worth showing: present, not blank and not "not known"."""
    text = "" if value is None else str(value)
    return bool(text.strip()) and "not known" not in text.lower()


def home_note(sim, paused):
    """The Home card: what is true about calls, texts and tasks, plus the tasks paused to save power.

    paused is the battery view's list of {"name", "id"} rows (names already worded for the helper being hidden or shown).
    """
    names = [str(n) for n in (p.get("name") if isinstance(p, dict) else p for p in paused) if _known(n)]
    note = HOME if sim else HOME_LIVE
    return note + ("\nPaused to save power: " + ", ".join(names) + "." if names else "")
