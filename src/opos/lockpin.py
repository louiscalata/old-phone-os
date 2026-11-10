# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional lock PIN. The hash is PBKDF2-HMAC-SHA256. Five wrong tries start a 30 second wait that grows.

The server starts enforcing only when OPOS_LOCK=1. With the variable unset, the phone behaves as before.
There is no emergency button: practice mode cannot place that call, and a real one has not been tested.
"""
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

from opos.store import Store, default_root

ITERATIONS = 200_000
_FILE = "lock.json"
_STORE = None
_WAIT = 30
_MAX_WAIT = 3600
_CLOCK = time.time


def active():
    return os.environ.get("OPOS_LOCK") == "1"


def set_clock(clock=None):
    """Tests replace the server clock. Request bodies are not a clock."""
    global _CLOCK
    if clock is None:
        _CLOCK = time.time
        return
    if not callable(clock):
        raise TypeError("clock")
    _CLOCK = clock


def reset():
    global _STORE
    _STORE = None
    set_clock(None)


def _now():
    return float(_CLOCK())


def _backoff_s(failures):
    """The longest wait this failure count may impose, never more than one hour."""
    try:
        count = int(failures or 0)
    except (TypeError, ValueError):
        count = 0
    if isinstance(failures, bool) or count < 5:
        return 0
    return min(_WAIT * (2 ** (count - 5)), _MAX_WAIT)


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
    return _clamp_wait(data)


def _clamp_wait(data):
    """A stored wait from the old client clock cannot outlast this failure count."""
    try:
        wait_until = float(data.get("wait_until") or 0)
    except (TypeError, ValueError):
        wait_until = 0.0
    cap = _now() + _backoff_s(data.get("failures"))
    if wait_until > cap:
        data["wait_until"] = cap
        _write(data)
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


def unlock(pin):
    """(ok, status). The wait uses the server clock. Wait text is included when a wait is running."""
    data = _read()
    now = _now()
    wait_until = float(data.get("wait_until") or 0)
    if now < wait_until:
        return False, _status(data)
    record = data.get("record")
    if not isinstance(record, dict):
        # There is no PIN to check. An empty unlock does not open a lock, because there is not one.
        return False, _status(data)
    if verify(pin, record):
        data["locked"] = False
        data["failures"] = 0
        data["wait_until"] = 0
        _write(data)
        return True, _status(data)
    failures = int(data.get("failures") or 0) + 1
    data["failures"] = failures
    data["locked"] = True
    if failures >= 5:
        data["wait_until"] = now + _backoff_s(failures)
    _write(data)
    return False, _status(data)


def _status(data):
    remaining = max(0, int(float(data.get("wait_until") or 0) - _now()))
    return {
        "ok": not data.get("locked", True),
        "locked": bool(data.get("locked", True)),
        "has_pin": isinstance(data.get("record"), dict),
        "wait_s": remaining,
        "failures": int(data.get("failures") or 0),
    }


def status():
    if not active():
        return {"ok": True, "locked": False, "has_pin": False, "wait_s": 0, "enforced": False}
    return dict(_status(_read()), enforced=True)


def blocks(action, path):
    """True when a write must be refused. Alarm stop and snooze, hang up, and unlock stay allowed.

    set and skip change the PIN. They are refused while the phone is locked.
    """
    if not is_locked():
        return False
    if path == "/api/lock":
        return action in {"set", "skip"}
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
