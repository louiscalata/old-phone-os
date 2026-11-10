# SPDX-License-Identifier: GPL-3.0-or-later
"""Plain cellular status from oFono, with ModemManager as the fallback.

The line is one of a fixed set. Equipment and subscriber identifiers are dropped
and never copied into the result. Not measured on a phone.
"""
from __future__ import annotations

import re

BANNED = (
    "imei",
    "imsi",
    "iccid",
    "msisdn",
    "serial",
    "subscriberidentity",
    "cardidentifier",
    "subscribernumbers",
    "equipmentidentifier",
)


def _keep(key: str) -> bool:
    leaf = key.rsplit(".", 1)[-1].lower().replace("_", "").replace("-", "")
    return not any(word in leaf for word in BANNED)


def _bars(strength) -> int | None:
    try:
        value = int(strength)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return 0
    if value > 100:
        value = 100
    if value == 0:
        return 0
    return min(5, (value + 19) // 20)


_PAIR = re.compile(r"\('([A-Za-z0-9_.-]+)',\s*<([^>]*)>")


def _pair_value(body: str) -> str:
    body = body.strip()
    if body.startswith("'") and body.endswith("'") and len(body) >= 2:
        return body[1:-1]
    lowered = body.lower()
    if lowered in ("true", "false"):
        return lowered
    digits = "".join(ch if ch.isdigit() else " " for ch in body).split()
    return digits[-1] if digits else body


def parse_props(text: str) -> dict:
    """Line-oriented properties, or a shallow gdbus-style pair list. Banned keys are omitted."""
    props: dict[str, str] = {}
    if not text:
        return props
    for key, body in _PAIR.findall(text):
        if _keep(key):
            props.setdefault(key, _pair_value(body))
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("("):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and _keep(key):
            props.setdefault(key, value)
    return props


def _get(props: dict, *names: str):
    for name in names:
        if name in props and props[name] != "":
            return props[name]
    lowered = {k.lower(): v for k, v in props.items()}
    for name in names:
        if name.lower() in lowered and lowered[name.lower()] != "":
            return lowered[name.lower()]
    return None


def _truth(value) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("true", "yes", "1"):
        return True
    if text in ("false", "no", "0", "none", ""):
        return False
    return None


def _tech_line(tech, operator, strength) -> str:
    name = (tech or "LTE").strip()
    label = "LTE" if name.lower() in ("lte", "4g") else name.upper()
    who = operator.strip() if isinstance(operator, str) and operator.strip() else "unknown"
    bars = _bars(strength)
    signal = f"{bars} bars" if bars is not None else "signal unknown"
    return f"{label}, operator {who}, signal {signal}"


def explain(ofono=None, mm=None, wifi_connected: bool = False) -> dict:
    """One plain line and a short reason. Identifiers are not copied."""
    ofono = {k: v for k, v in (ofono or {}).items() if _keep(k)}
    mm = {k: v for k, v in (mm or {}).items() if _keep(k)}
    if ofono:
        return _from_ofono(ofono, wifi_connected)
    if mm:
        return _from_mm(mm, wifi_connected)
    return {"line": "Cellular: unavailable", "reason": "no modem report"}


def _from_ofono(props: dict, wifi_connected: bool) -> dict:
    present = _truth(_get(props, "sim.Present", "Present"))
    pin = str(_get(props, "sim.PinRequired", "PinRequired") or "").strip().lower()
    status = str(_get(props, "net.Status", "Status") or "").strip().lower()
    if present is False:
        return {"line": "No SIM card", "reason": "sim absent"}
    if pin and pin not in ("none", "false", "no"):
        return {"line": "SIM locked (PIN)", "reason": "pin required"}
    if status == "denied" or _truth(_get(props, "sim.CarrierLock", "CarrierLock")) is True:
        return {"line": "SIM not accepted (carrier lock)", "reason": "registration denied"}
    if status == "searching":
        return {"line": "Searching", "reason": "searching"}
    attached = _truth(_get(props, "data.Attached", "Attached"))
    registered = status in ("registered", "roaming")
    if registered and attached is False and wifi_connected:
        return {"line": "Data off: using Wi-Fi", "reason": "data not attached"}
    ims = _truth(_get(props, "ims.Registered", "Registered"))
    if registered and ims is False:
        return {"line": "Calls need VoLTE, not available on this build", "reason": "ims not registered"}
    if registered:
        return {
            "line": _tech_line(
                _get(props, "net.Technology", "Technology"),
                _get(props, "net.Name", "Name"),
                _get(props, "net.Strength", "Strength"),
            ),
            "reason": status,
        }
    if status:
        return {"line": "Cellular: " + status, "reason": status}
    return {"line": "Cellular: unavailable", "reason": "ofono had no status"}


def _from_mm(props: dict, wifi_connected: bool) -> dict:
    state = str(_get(props, "modem.generic.state") or "").strip().lower()
    why = str(_get(props, "modem.generic.state-failed-reason") or "").strip().lower()
    sim = str(_get(props, "modem.generic.sim") or "").strip()
    lock = str(_get(props, "modem.generic.unlock-required") or "").strip().lower()
    if why == "sim-missing" or (state in ("failed", "absent") and not sim):
        return {"line": "No SIM card", "reason": "sim absent"}
    if lock and lock not in ("none", "--"):
        return {"line": "SIM locked (PIN)", "reason": "pin required"}
    if state == "searching":
        return {"line": "Searching", "reason": "searching"}
    if state in ("registered", "connected"):
        if wifi_connected and state == "registered":
            return {"line": "Data off: using Wi-Fi", "reason": "registered without a data session"}
        tech = None
        for key, value in props.items():
            if key.startswith("modem.generic.access-technologies.value") and value:
                tech = value
                break
        return {
            "line": _tech_line(tech, _get(props, "modem.3gpp.operator-name"), _get(props, "modem.generic.signal-quality.value")),
            "reason": state,
        }
    if state:
        return {"line": "Cellular: " + state, "reason": state}
    return {"line": "Cellular: unavailable", "reason": "modemmanager had no state"}


def read_ofono(runner) -> dict | None:
    """Ask oFono. None when the tool is missing or does not answer. Identifiers are not kept."""
    try:
        result = runner(
            ["gdbus", "call", "--system", "--dest", "org.ofono", "--object-path", "/", "--method", "org.ofono.Manager.GetModems"],
            5,
            None,
        )
    except Exception:
        return None
    if getattr(result, "missing", False) or not getattr(result, "ok", False):
        return None
    props = parse_props(getattr(result, "out", "") or "")
    return props or None
