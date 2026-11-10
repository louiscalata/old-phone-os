# SPDX-License-Identifier: GPL-3.0-or-later
"""A local list of calls. The dialer writes a row when a call starts and closes it when the call ends.

Nothing is sent. A practice modem is marked practice, so the page does not say the call left the phone.
"""
import os
import uuid
from datetime import datetime

from opos.essentials import normalize_number
from opos.secure import clean_text
from opos.store import Store, default_root

_FILE = "calls.json"
_MAX = 100
_STORE = None
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_WAYS = {"out": "Out", "in": "In", "missed": "Missed"}


def reset():
    """Tests drop the open store."""
    global _STORE
    _STORE = None


def enabled():
    """True when OPOS_APPS names this list. Otherwise the dialer does not write."""
    names = [part.strip().lower() for part in (os.environ.get("OPOS_APPS") or "").split(",")]
    return "calls" in names


def _store():
    global _STORE
    if _STORE is None:
        _STORE = Store(os.environ.get("OPOS_STATE_DIR") or default_root())
    return _STORE


def _row_ok(row):
    return (
        isinstance(row, dict)
        and isinstance(row.get("id"), str)
        and isinstance(row.get("number"), str)
        and row.get("direction") in _WAYS
        and isinstance(row.get("started"), str)
    )


def _read():
    data, note = _store().read_json(_FILE, {"calls": []})
    rows = data.get("calls") if isinstance(data.get("calls"), list) else []
    return [row for row in rows if _row_ok(row)], note


def _write(rows):
    _store().write_json(_FILE, {"calls": rows[-_MAX:]})


def _clock(now):
    if now is None:
        now = datetime.now().astimezone()
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("now")
    return now


def _name(name):
    if not isinstance(name, str):
        return ""
    return clean_text(name, 80).strip()


def begin(number, name="", practice=False, now=None, direction="out"):
    """Append one open call. direction is out, in, or missed."""
    if direction not in _WAYS:
        raise ValueError("direction")
    norm = normalize_number(number)
    when = _clock(now)
    rows, _note = _read()
    row = {
        "id": uuid.uuid4().hex[:8],
        "number": norm,
        "name": _name(name),
        "direction": direction,
        "started": when.isoformat(),
        "ended": None,
        "seconds": None,
        "practice": bool(practice),
    }
    rows.append(row)
    _write(rows)
    return dict(row)


def end(now=None):
    """Close the newest call that is still open. None when every call has ended."""
    when = _clock(now)
    rows, _note = _read()
    for row in reversed(rows):
        if row.get("ended"):
            continue
        started = datetime.fromisoformat(row["started"])
        if started.tzinfo is None:
            raise ValueError("now")
        seconds = int((when - started).total_seconds())
        row["ended"] = when.isoformat()
        row["seconds"] = 0 if seconds < 0 else seconds
        _write(rows)
        return dict(row)
    return None


def missed(number, name="", practice=False, now=None):
    """One missed call, already ended, with length 0 s."""
    row = begin(number, name, practice, now, "missed")
    return end(_clock(now) if now is not None else datetime.fromisoformat(row["started"]))


def note_start(number, name, practice, now=None):
    """The dialer hook. Quiet when Recents is off, and a write error does not stop the call."""
    if not enabled():
        return None
    try:
        return begin(number, name, practice, now, "out")
    except Exception:
        return None


def note_end(now=None):
    """Close the open call. Quiet when Recents is off."""
    if not enabled():
        return None
    try:
        return end(now)
    except Exception:
        return None


def duration_words(seconds):
    """A short length: 45 s, 2 min 5 s, 1 h 2 min, or Not ended."""
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        return "Not ended"
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return "%d s" % seconds
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        if sec:
            return "%d min %d s" % (minutes, sec)
        return "%d min" % minutes
    hours, minutes = divmod(minutes, 60)
    if minutes:
        return "%d h %d min" % (hours, minutes)
    return "%d h" % hours


def when_words(iso):
    """Day, month, and clock from the stored time."""
    dt = datetime.fromisoformat(iso)
    return "%d %s, %02d:%02d" % (dt.day, _MONTHS[dt.month - 1], dt.hour, dt.minute)


def snapshot():
    """Newest first. A title is the contact name, or the number when there is no name."""
    rows, note = _read()
    public = []
    for row in reversed(rows):
        title = row.get("name") or row["number"]
        public.append({
            "id": row["id"],
            "number": row["number"],
            "name": row.get("name") or "",
            "title": title,
            "direction": row["direction"],
            "way": _WAYS[row["direction"]],
            "when": when_words(row["started"]),
            "length": duration_words(row.get("seconds") if isinstance(row.get("seconds"), int) else None),
            "practice": bool(row.get("practice")),
        })
    return {"ok": True, "calls": public, "recovered": note}
