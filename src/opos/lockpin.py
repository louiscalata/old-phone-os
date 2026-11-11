# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional lock PIN. The hash is PBKDF2-HMAC-SHA256. Five wrong tries start a 30 second wait that grows.

The server starts enforcing only when OPOS_LOCK=1. With the variable unset, the phone behaves as before.
There is no emergency button: practice mode cannot place that call, and a real one has not been tested.
"""
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import time

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
    data, _note = _store().read_json(_FILE, {"locked": True, "failures": 0, "wait_until": 0})
    if not isinstance(data, dict):
        data = {"locked": True, "failures": 0, "wait_until": 0}
    data.setdefault("locked", True)
    data.setdefault("failures", 0)
    data.setdefault("wait_until", 0)
    try:
        if (type(data["locked"]) is not bool or type(data["failures"]) is not int
                or data["failures"] < 0 or type(data["wait_until"]) not in (int, float)
                or not math.isfinite(data["wait_until"]) or data["wait_until"] < 0):
            raise ValueError("lock state")
    except (ValueError, OverflowError):
        data.update(locked=True, failures=0, wait_until=0)
    if "record" in data:
        try:
            record = data["record"]
            if (not isinstance(record, dict) or type(record.get("iterations")) is not int
                    or not 0 < record["iterations"] <= 2**31 - 1
                    or len(bytes.fromhex(record["hash"])) != 32):
                raise ValueError("PIN record")
            bytes.fromhex(record["salt"])
        except (KeyError, TypeError, ValueError):
            # A damaged PIN record must not become a PIN-less unlock.
            data.update(record={}, locked=True)
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


def _clock(now=None):
    """Use the server clock; explicit times work only with OPOS_LOCK_TEST_CLOCK=1."""
    if os.environ.get("OPOS_LOCK_TEST_CLOCK") == "1" and type(now) in (int, float):
        try:
            if math.isfinite(now):
                return float(now)
        except OverflowError:
            pass
    return time.time()


def _state(data, now):
    """Derive the internal state from the saved data and the clock."""
    if not active():
        return "OFF"
    if not data["locked"]:
        return "UNLOCKED"
    if "record" not in data:
        return "LOCKED_NO_PIN"
    return "WAITING" if now < data["wait_until"] else "LOCKED_PIN"


def _transition(state, event, data, now, pin=None):
    """Return (allowed, new data). All lock transition policy lives here."""
    if (event in {"set", "skip"} and state in {"LOCKED_PIN", "WAITING"}
            or event in {"unlock_ok", "unlock_wrong"} and state == "WAITING"):
        return False, data
    changed = dict(data)
    if event == "set":
        changed.update(record=hash_pin(pin), locked=True, failures=0, wait_until=0)
    elif event == "skip":
        changed.pop("record", None)
        changed.update(locked=False, failures=0, wait_until=0)
    elif event == "lock":
        changed["locked"] = True
    elif event == "unlock_ok":
        changed.update(locked=False, failures=0, wait_until=0)
    elif event == "unlock_wrong":
        failures = changed["failures"] + 1
        changed.update(locked=True, failures=failures)
        if failures >= 5:
            changed["wait_until"] = now + _WAIT * (2 ** (failures - 5))
    else:
        raise ValueError("lock event")
    return True, changed


def _change(event, pin=None, now=None):
    data = _read()
    now = _clock(now)
    if event == "unlock":
        event = "unlock_ok" if "record" not in data or verify(pin, data["record"]) else "unlock_wrong"
    allowed, changed = _transition(_state(data, now), event, data, now, pin)
    if allowed:
        _write(changed)
    return allowed and event != "unlock_wrong", _status(changed, now)


def is_locked():
    data = _read() if active() else {}
    return _status(data, _clock())["locked"]


def has_pin():
    return isinstance(_read().get("record"), dict)


def set_pin(pin):
    return _change("set", pin=pin)[0]


def skip():
    """The owner chose no PIN. The screen can still lock, and unlock needs no digits."""
    return _change("skip")[0]


def lock():
    return _change("lock")[0]


def unlock(pin, now=None):
    """Return (ok, status); now is honoured only with OPOS_LOCK_TEST_CLOCK=1."""
    return _change("unlock", pin=pin, now=now)


def _status(data, now):
    state = _state(data, now)
    locked = state in {"LOCKED_NO_PIN", "LOCKED_PIN", "WAITING"}
    remaining = max(0, int(data["wait_until"] - now)) if state == "WAITING" else 0
    return {
        "ok": not locked,
        "locked": locked,
        "has_pin": isinstance(data.get("record"), dict),
        "wait_s": remaining,
        "failures": int(data.get("failures") or 0),
    }


def status(now=None):
    data = _read() if active() else {}
    now = _clock(now)
    if _state(data, now) == "OFF":
        return {"ok": True, "locked": False, "has_pin": False, "wait_s": 0, "enforced": False}
    return dict(_status(data, now), enforced=True)


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
