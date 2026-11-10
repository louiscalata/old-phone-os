# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional lock PIN. The hash is PBKDF2-HMAC-SHA256. Five wrong tries start a 30 second wait that grows.

The server starts enforcing only when OPOS_LOCK=1. With the variable unset, the phone behaves as before.
There is no emergency button: practice mode cannot place that call, and a real one has not been tested.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path

from opos.store import Store, default_root

ITERATIONS = 200_000
_FILE = "lock.json"
_STORE = None
_WAIT = 30


def active():
    return os.environ.get("OPOS_LOCK") == "1"


def reset():
    global _STORE
    _STORE = None


def _store():
    global _STORE
    if _STORE is None:
        _STORE = Store(os.environ.get("OPOS_STATE_DIR") or default_root())
    return _STORE


def _read():
    data, _note = _store().read_json(_FILE, {"locked": False, "failures": 0, "wait_until": 0})
    if not isinstance(data, dict):
        data = {"locked": False, "failures": 0, "wait_until": 0}
    data.setdefault("failures", 0)
    data.setdefault("wait_until", 0)
    # No saved PIN means the phone is not locked. A file that says locked without a record is not a lock.
    if not isinstance(data.get("record"), dict):
        data["locked"] = False
    else:
        data.setdefault("locked", True)
    return data


def _write(data):
    _store().write_json(_FILE, data)


def hash_pin(pin, salt=None, iterations=ITERATIONS):
    if not isinstance(pin, str) or not pin.isdigit() or not 4 <= len(pin) <= 8:
        raise ValueError("pin")
    if salt is None:
        salt = os.urandom(16)
    elif isinstance(salt, str):
        salt = bytes.fromhex(salt)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iterations)
    return {"salt": salt.hex(), "hash": digest.hex(), "iterations": iterations}


def verify(pin, record):
    if not isinstance(record, dict) or not isinstance(pin, str):
        return False
    try:
        fresh = hash_pin(pin, record.get("salt"), int(record.get("iterations") or ITERATIONS))
    except ValueError:
        return False
    return hmac.compare_digest(fresh["hash"], str(record.get("hash") or ""))


def is_locked():
    if not active():
        return False
    data = _read()
    return isinstance(data.get("record"), dict) and bool(data.get("locked"))


def has_pin():
    return isinstance(_read().get("record"), dict)


def set_pin(pin):
    data = _read()
    data["record"] = hash_pin(pin)
    data["locked"] = True
    data["failures"] = 0
    data["wait_until"] = 0
    _write(data)


def skip():
    """The owner chose no PIN. The screen can still lock, and unlock needs no digits."""
    data = _read()
    data.pop("record", None)
    data["locked"] = False
    data["failures"] = 0
    data["wait_until"] = 0
    _write(data)


def lock():
    data = _read()
    if not isinstance(data.get("record"), dict):
        return
    data["locked"] = True
    _write(data)


def unlock(pin, now):
    """(ok, status). now is a number the test can move. Wait text is included when a wait is running."""
    data = _read()
    now = float(now)
    wait_until = float(data.get("wait_until") or 0)
    if now < wait_until:
        return False, _status(data, now)
    record = data.get("record")
    if not isinstance(record, dict):
        # There is no PIN to check. An empty unlock does not open a lock, because there is not one.
        return False, _status(data, now)
    if verify(pin, record):
        data["locked"] = False
        data["failures"] = 0
        data["wait_until"] = 0
        _write(data)
        return True, _status(data, now)
    failures = int(data.get("failures") or 0) + 1
    data["failures"] = failures
    data["locked"] = True
    if failures >= 5:
        wait = _WAIT * (2 ** (failures - 5))
        data["wait_until"] = now + wait
    _write(data)
    return False, _status(data, now)


def _status(data, now=0):
    remaining = max(0, int(float(data.get("wait_until") or 0) - float(now)))
    return {
        "ok": not data.get("locked", True),
        "locked": bool(data.get("locked", True)),
        "has_pin": isinstance(data.get("record"), dict),
        "wait_s": remaining,
        "failures": int(data.get("failures") or 0),
    }


def status(now=0):
    if not active():
        return {"ok": True, "locked": False, "has_pin": False, "wait_s": 0, "enforced": False}
    return dict(_status(_read(), now), enforced=True)


def blocks(action, path):
    """True when a write must be refused. Alarm stop and snooze, hang up, and unlock stay allowed."""
    if not is_locked():
        return False
    if path == "/api/lock":
        return False
    if path == "/api/clock" and action in {"stop", "snooze"}:
        return False
    if action in {"hang_up", "unlock"}:
        return False
    return True


def counts():
    """Counts only. No names and no message text."""
    alarm = ""
    try:
        from opos import clock_page
        nxt = (clock_page.snapshot() or {}).get("next") or {}
        alarm = nxt.get("text") or ""
    except Exception:
        alarm = ""
    return {"messages": 0, "missed": 0, "alarm": alarm}


def redact(snap):
    """The locked /api/state body. Battery percent and the clock stay. Names do not."""
    battery = snap.get("battery") if isinstance(snap.get("battery"), dict) else {}
    percent = battery.get("percent")
    return {
        "locked": True,
        "clock": snap.get("clock") if isinstance(snap.get("clock"), str) else "",
        "battery": {"percent": percent} if isinstance(percent, (int, float)) and not isinstance(percent, bool) else {},
        "lock": counts(),
    }
