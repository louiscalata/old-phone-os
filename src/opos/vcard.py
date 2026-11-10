# SPDX-License-Identifier: GPL-3.0-or-later
"""A small vCard 3.0 reader and writer. FN, N, TEL and NOTE are kept. Other fields are counted and skipped."""
import re

from opos.essentials import normalize_number

_UNFOLD = re.compile(r"\r?\n[ \t]")
_LABELS = {"cell": "Mobile", "mobile": "Mobile", "home": "Home", "work": "Work"}


def _unfold(text):
    return _UNFOLD.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))


def _label(params):
    for part in params:
        key, _, value = part.partition("=")
        if key.upper() in ("TYPE", "TEL"):
            for word in value.replace(",", ";").split(";"):
                found = _LABELS.get(word.strip().lower())
                if found:
                    return found
        found = _LABELS.get(part.strip().lower())
        if found:
            return found
    return "Mobile"


def parse(text):
    """(people, skipped). people is a list of {name, numbers, note}. skipped counts ignored fields."""
    people = []
    skipped = 0
    current = None

    def finish():
        nonlocal current
        if current and current.get("name") and current.get("numbers"):
            people.append(current)
        current = None

    for raw in _unfold(text).split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.upper() == "BEGIN:VCARD":
            finish()
            current = {"name": "", "numbers": [], "note": ""}
            continue
        if line.upper() == "END:VCARD":
            finish()
            continue
        if current is None or ":" not in line:
            continue
        left, _, value = line.partition(":")
        name, *params = left.split(";")
        key = name.upper()
        value = value.replace("\\n", " ").replace("\\,", ",").strip()
        if key == "FN" and value:
            current["name"] = value[:80]
        elif key == "N" and not current["name"]:
            bits = [part.strip() for part in value.split(";") if part.strip()]
            current["name"] = " ".join(reversed(bits[:2]))[:80] if bits else ""
        elif key == "TEL":
            try:
                number = normalize_number(value)
            except ValueError:
                skipped += 1
                continue
            if len(current["numbers"]) < 4:
                current["numbers"].append({"number": number, "label": _label(params)})
            else:
                skipped += 1
        elif key == "NOTE":
            current["note"] = value[:200]
        elif key not in ("BEGIN", "END", "VERSION"):
            skipped += 1
    finish()
    return people, skipped


def emit(people):
    """vCard 3.0 text for people with name and numbers."""
    lines = []
    for person in people:
        numbers = person.get("numbers") or []
        if not person.get("name") or not numbers:
            continue
        lines.append("BEGIN:VCARD")
        lines.append("VERSION:3.0")
        lines.append("FN:" + str(person["name"]).replace("\n", " "))
        for item in numbers:
            kind = {"Mobile": "CELL", "Home": "HOME", "Work": "WORK"}.get(item.get("label") or "Mobile", "CELL")
            lines.append("TEL;TYPE=%s:%s" % (kind, item["number"]))
        if person.get("note"):
            lines.append("NOTE:" + str(person["note"]).replace("\n", " "))
        lines.append("END:VCARD")
    return "\n".join(lines) + ("\n" if lines else "")
