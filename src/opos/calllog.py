# SPDX-License-Identifier: GPL-3.0-or-later
"""A bounded local call log. record(event) is the optional modem observer.

Only OPOS_APPS=calls enables recording. Starts are saved immediately; connected
and ended update the same row. An unfinished row has no known duration. There is
no modem control here and no HTTP action that can insert a call or mark it real.
"""
from datetime import datetime, timezone
import math
import os
import re
import threading

from opos import apps
from opos.call_events import CallEvent
from opos.essentials import normalize_number
from opos.store import MAX_JSON, Store, default_root

MAX_CALLS = 200
_ID = re.compile(r"[0-9a-f]{32}")
_LOCK = threading.RLock()
_STORE = None
_RECOVERED = ""


class _CallStore(Store):
    def _quarantine(self, path):
        dest = super()._quarantine(path)
        if dest is None:
            # Never overwrite a damaged file when its copy could not be kept.
            raise OSError("The saved call log could not be kept aside.")
        os.chmod(dest, 0o600)
        return dest


def reset():
    global _STORE, _RECOVERED
    with _LOCK:
        _STORE = None
        _RECOVERED = ""


def _store():
    global _STORE
    if _STORE is None:
        _STORE = _CallStore(os.environ.get("OPOS_STATE_DIR") or default_root())
    return _STORE


def _seconds(value):
    return type(value) in (int, float) and 0 <= value <= 253402300799 and math.isfinite(value)


def _valid(row):
    if not isinstance(row, dict) or set(row) != {"id", "number", "name", "direction", "start_time", "duration", "practice", "state"}:
        return False
    try:
        if not isinstance(row["id"], str) or not _ID.fullmatch(row["id"]):
            return False
        if normalize_number(row["number"]) != row["number"]:
            return False
        when = datetime.fromisoformat(row["start_time"])
        if when.tzinfo is None or when.utcoffset().total_seconds() != 0:
            return False
    except (TypeError, ValueError, OverflowError):
        return False
    return (isinstance(row["name"], str) and len(row["name"]) <= 80
            and row["direction"] in ("outgoing", "incoming", "missed")
            and type(row["practice"]) is bool
            and row["state"] in ("started", "connected", "ended")
            and (row["duration"] is None or _seconds(row["duration"]))
            and (row["state"] == "ended" or row["duration"] is None)
            and (row["direction"] != "missed" or row["state"] == "ended"))


def _load():
    global _RECOVERED
    saved = _store()
    path = saved._path("calls.json")
    try:
        oversized = path.stat().st_size > MAX_JSON
    except FileNotFoundError:
        oversized = False
    if oversized:
        saved._quarantine(path)
        data, note = {"calls": []}, saved.note_once("calls.json")
    else:
        data, note = saved.read_json("calls.json", {"calls": []})
    rows = data.get("calls")
    if (not isinstance(rows, list) or not all(_valid(row) for row in rows)
            or len({row["id"] for row in rows}) != len(rows)):
        saved._quarantine(path)
        rows, note = [], saved.note_once("calls.json")
    _RECOVERED = note or _RECOVERED
    return sorted(rows, key=lambda row: row["start_time"])[-MAX_CALLS:]


def record(event):
    """Accept a modem CallEvent; HTTP clients cannot supply these events.

    Adapters may pass this function as call_observer, including a future real
    adapter with practice=False. The current phone uses only ScreenModem.
    """
    if "calls" not in (apps.names_from() or []):
        return
    if (not isinstance(event, CallEvent) or not isinstance(event.call_id, str)
            or not _ID.fullmatch(event.call_id) or event.state not in ("started", "connected", "ended")
            or event.direction not in ("outgoing", "incoming") or type(event.practice) is not bool
            or (event.answered is not None and type(event.answered) is not bool)
            or not _seconds(event.at) or (event.duration is not None and not _seconds(event.duration))):
        raise ValueError("bad call event")
    number = normalize_number(event.number)
    with _LOCK:
        rows = _load()
        row = next((r for r in rows if r["id"] == event.call_id), None)
        if event.state == "started":
            if row is not None:
                return
            from opos.contacts_page import name_for
            rows.append({
                "id": event.call_id, "number": number,
                "name": name_for(number, practice=event.practice),
                "direction": event.direction,
                "start_time": datetime.fromtimestamp(event.at, timezone.utc).isoformat(),
                "duration": None, "practice": event.practice, "state": "started",
            })
        else:
            if row is None or row["state"] == "ended":
                return
            if row["number"] != number or row["practice"] != event.practice or row["direction"] != event.direction:
                raise ValueError("call event identity changed")
            if event.state == "ended":
                answered = event.answered if event.answered is not None else row["state"] == "connected"
                if row["direction"] == "incoming" and not answered:
                    row["direction"] = "missed"
                    row["duration"] = 0
                else:
                    row["duration"] = event.duration
            row["state"] = event.state
        rows.sort(key=lambda r: r["start_time"])
        _store().write_json("calls.json", {"calls": rows[-MAX_CALLS:]})


def snapshot():
    """missed_count counts retained missed calls; reading does not acknowledge them."""
    with _LOCK:
        rows = _load()
        return {"ok": True, "calls": list(reversed(rows)),
                "missed_count": sum(row["direction"] == "missed" for row in rows),
                "recovered": _RECOVERED}


def clear():
    global _RECOVERED
    with _LOCK:
        _load()  # Keep an unreadable file aside before replacing it.
        _store().write_json("calls.json", {"calls": []})
        _RECOVERED = ""
        return snapshot()
