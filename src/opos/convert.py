# SPDX-License-Identifier: GPL-3.0-or-later
"""Length, mass, volume and temperature. The factors are fixed. Nothing is stored or sent."""
import re
from decimal import Decimal, ROUND_HALF_UP

_NUM = re.compile(r"^[+-]?\d+(\.\d+)?$")
_LIMIT = Decimal("1000000000000")

# Metres, kilograms, litres. Temperature is handled on its own.
_LENGTH = (
    ("mm", "mm", Decimal("0.001")),
    ("cm", "cm", Decimal("0.01")),
    ("m", "m", Decimal("1")),
    ("km", "km", Decimal("1000")),
    ("in", "inch", Decimal("0.0254")),
    ("ft", "foot", Decimal("0.3048")),
    ("yd", "yard", Decimal("0.9144")),
    ("mi", "mile", Decimal("1609.344")),
)
_MASS = (
    ("g", "g", Decimal("0.001")),
    ("kg", "kg", Decimal("1")),
    ("oz", "oz", Decimal("0.028349523125")),
    ("lb", "lb", Decimal("0.45359237")),
)
_VOLUME = (
    ("ml", "ml", Decimal("0.001")),
    ("l", "L", Decimal("1")),
    ("floz", "US fl oz", Decimal("0.0295735295625")),
    ("cup", "US cup", Decimal("0.2365882365")),
    ("pt", "US pint", Decimal("0.473176473")),
    ("gal", "US gal", Decimal("3.785411784")),
)
_TEMP = (("c", "C", None), ("f", "F", None), ("k", "K", None))

KINDS = (
    {"id": "length", "title": "Length", "src": "km", "dst": "mi", "units": _LENGTH},
    {"id": "mass", "title": "Mass", "src": "kg", "dst": "lb", "units": _MASS},
    {"id": "volume", "title": "Volume", "src": "l", "dst": "gal", "units": _VOLUME},
    {"id": "temperature", "title": "Temperature", "src": "c", "dst": "f", "units": _TEMP},
)
_BY_ID = {item["id"]: item for item in KINDS}


def _parse(value):
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("value")
    text = str(value) if isinstance(value, int) else value.strip() if isinstance(value, str) else None
    if text is None or len(text) > 40 or _NUM.fullmatch(text) is None:
        raise ValueError("value")
    number = Decimal(text)
    if abs(number) > _LIMIT:
        raise ValueError("value")
    return number


def _text(number):
    """Up to six significant figures, with trailing zeros removed."""
    if not isinstance(number, Decimal):
        raise ValueError("value")
    if number == 0:
        return "0"
    sign = "-" if number < 0 else ""
    number = abs(number)
    places = number.adjusted() - 5
    rounded = number.quantize(Decimal("1e%d" % places), rounding=ROUND_HALF_UP)
    text = format(rounded, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return sign + (text or "0")


def _kelvin(unit, number):
    if unit == "k":
        kelvin = number
    elif unit == "c":
        kelvin = number + Decimal("273.15")
    elif unit == "f":
        kelvin = (number - Decimal("32")) * Decimal("5") / Decimal("9") + Decimal("273.15")
    else:
        raise ValueError("unit")
    if kelvin < 0:
        raise ValueError("cold")
    return kelvin


def _from_kelvin(unit, kelvin):
    if unit == "k":
        return kelvin
    if unit == "c":
        return kelvin - Decimal("273.15")
    if unit == "f":
        return (kelvin - Decimal("273.15")) * Decimal("9") / Decimal("5") + Decimal("32")
    raise ValueError("unit")


def convert(kind, value, src, dst):
    """One conversion. kind, src and dst are the short ids. value is text or an int."""
    spec = _BY_ID.get(kind)
    if spec is None:
        raise ValueError("kind")
    units = {item[0]: item for item in spec["units"]}
    if src not in units or dst not in units:
        raise ValueError("unit")
    number = _parse(value)
    if kind == "temperature":
        out = _from_kelvin(dst, _kelvin(src, number))
    else:
        if number < 0:
            raise ValueError("sign")
        out = number * units[src][2] / units[dst][2]
    text = _text(out)
    line = "%s %s = %s %s" % (_text(number), units[src][1], text, units[dst][1])
    return {"ok": True, "text": text, "line": line, "kind": kind, "src": src, "dst": dst}


def snapshot():
    """The measures and their units, for the page."""
    kinds = []
    for spec in KINDS:
        kinds.append({
            "id": spec["id"],
            "title": spec["title"],
            "src": spec["src"],
            "dst": spec["dst"],
            "units": [{"id": item[0], "title": item[1]} for item in spec["units"]],
        })
    return {"ok": True, "kinds": kinds}
