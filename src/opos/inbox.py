# SPDX-License-Identifier: GPL-3.0-or-later
"""A local notice list and quiet hours. Alarms are never silenced. No network."""

import os
import re
from datetime import datetime

from opos.store import Store, default_root

CAP = 100
KINDS = {"call", "message", "alarm", "system", "battery"}
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_STORE = None
_FILE = "inbox.json"


def reset():
    global _STORE
    _STORE = None


def _store():
    global _STORE
    if _STORE is None:
        _STORE = Store(os.environ.get("OPOS_STATE_DIR") or default_root())
    return _STORE


def _default():
    return {"items": [], "quiet": {"from": "22:00", "to": "07:00", "on": False}, "next": 1}


def _state(data):
    items = data.get("items") if isinstance(data.get("items"), list) else []
    quiet = data.get("quiet") if isinstance(data.get("quiet"), dict) else {}
    try:
        nxt = int(data.get("next") or 1)
    except (TypeError, ValueError):
        nxt = 1
    if nxt < 1:
        nxt = 1
    return items, quiet, nxt


def _save(save, items, quiet, nxt):
    save({"items": items[-CAP:], "quiet": quiet, "next": nxt})


def _minutes(text):
    if not isinstance(text, str) or _HHMM.fullmatch(text) is None:
        raise ValueError("time")
    hour, _, minute = text.partition(":")
    return int(hour) * 60 + int(minute)


def in_quiet(now, window):
    """True when now (HH:MM) is inside from/to, including a window that crosses midnight."""
    if not window or not window.get("on"):
        return False
    try:
        start = _minutes(window.get("from") or "22:00")
        end = _minutes(window.get("to") or "07:00")
        cur = now.hour * 60 + now.minute if isinstance(now, datetime) else _minutes(now)
    except ValueError:
        return False
    if start == end:
        return True
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end


def allows_sound(kind, now, window, favorite=False):
    if kind == "alarm" or not in_quiet(now, window) or (kind == "call" and favorite):
        return True
    return False


def add(kind, text, now=None):
    if kind not in KINDS:
        raise ValueError("kind")
    if not isinstance(text, str) or not text.strip() or len(text) > 120:
        raise ValueError("text")
    with _store()._transaction(_FILE, _default()) as (data, _, save):
        items, quiet, nxt = _state(data)
        when = now if isinstance(now, datetime) else datetime.now()
        sound = allows_sound(kind, when, quiet, False)
        row = {"id": str(nxt), "kind": kind, "text": text.strip(), "sound": sound}
        items.append(row)
        _save(save, items, quiet, nxt + 1)
        return row


def dismiss(item_id):
    with _store()._transaction(_FILE, _default()) as (data, _, save):
        items, quiet, nxt = _state(data)
        items = [row for row in items if str(row.get("id")) != str(item_id)]
        _save(save, items, quiet, nxt)


def clear():
    with _store()._transaction(_FILE, _default()) as (data, _, save):
        _, quiet, nxt = _state(data)
        _save(save, [], quiet, nxt)


def set_quiet(on, start="22:00", end="07:00"):
    _minutes(start)
    _minutes(end)
    with _store()._transaction(_FILE, _default()) as (data, _, save):
        items, _, nxt = _state(data)
        quiet = {
            "on": bool(on),
            "from": start,
            "to": end,
            "alarms": "Alarms always ring.",
            "favorites": "Calls from Favorites ring.",
        }
        _save(save, items, quiet, nxt)
        return quiet


def snapshot():
    data, note = _store().read_json(_FILE, _default())
    items, quiet, _ = _state(data)
    return {"ok": True, "items": items, "quiet": quiet, "recovered": note}
