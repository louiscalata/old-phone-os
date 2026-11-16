# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare a phone's declared hardware facts (opos.hw.device) with what its kernel reports. Read-only.

    python3 -B -m opos.hw.facts --root / [--profile NAME]

One line for each fact it reads (model, compatible, memory, panels, panel_px, lid, fuel_gauges, backlights), then a
summary. The declared output_px, scale, span, hinge and session_uid are not read here; tests/test_hw_facts.py checks
them against the repository's own copies only.
    MATCH       the kernel reports the declared value
    DIFF        the kernel reports something else (exit status 1)
    UNREADABLE  the files for the fact are missing or unreadable; nothing is compared, so this is never a match
    UNDECLARED  the kernel reports a value and the profile declares none

It opens files for reading only, under --root, and writes nothing. Without --profile the profile is the one
opos.hw.detect picks.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass

from opos.hw import store
from opos.hw.device import DeviceFacts, Fact

STATUSES = ("MATCH", "DIFF", "UNREADABLE", "UNDECLARED")


def _read(root: str, rel: str) -> bytes | None:
    try:
        with open(os.path.join(root, rel), "rb") as f:
            return f.read(65536)
    except (OSError, ValueError):
        return None


def _names(root: str, rel: str) -> tuple[str, ...] | None:
    try:
        return tuple(sorted(os.listdir(os.path.join(root, rel))))
    except OSError:
        return None


def read_model(root: str):
    raw = _read(root, "proc/device-tree/model")
    text = None if raw is None else raw.replace(b"\x00", b"").decode("utf-8", "replace").strip()
    return text or None


def read_compatible(root: str):
    raw = _read(root, "proc/device-tree/compatible")
    entries = () if raw is None else tuple(e.decode("utf-8", "replace") for e in raw.split(b"\x00") if e.strip())
    return entries or None


def read_memory(root: str):
    raw = _read(root, "proc/meminfo")
    m = None if raw is None else re.search(rb"(?m)^MemTotal:\s+(\d+) kB$", raw)
    return int(m.group(1)) if m else None


def _panel_modes(root: str):
    """The first mode of each DRM connector that lists one, or None when no connector's modes file can be read."""
    names = _names(root, "sys/class/drm")
    if names is None:
        return None
    seen, modes = False, []
    for n in names:
        raw = _read(root, "sys/class/drm/%s/modes" % n)
        if raw is None:
            continue
        seen = True
        m = re.match(rb"\s*(\d+)x(\d+)", raw)
        if m:
            modes.append((int(m.group(1)), int(m.group(2))))
    return tuple(modes) if seen else None


def read_panels(root: str):
    modes = _panel_modes(root)
    return None if modes is None else len(modes)


def read_panel_px(root: str):
    modes = _panel_modes(root)
    return modes or None


def read_input_names(root: str):
    raw = _read(root, "proc/bus/input/devices")
    if raw is None:
        return None
    return tuple(m.decode("utf-8", "replace") for m in re.findall(rb'(?m)^N: Name="(.*)"$', raw))


def read_power_supplies(root: str):
    return _names(root, "sys/class/power_supply")


def read_backlights(root: str):
    return _names(root, "sys/class/backlight")


@dataclass(frozen=True)
class Reading:
    name: str
    status: str
    declared: str | None
    measured: bool | None
    read: str | None
    where: str


def _px(v) -> str:
    return "%dx%d" % tuple(v)


def _gauges(f: DeviceFacts):
    if f.fuel_gauge_driver is None or f.fuel_gauges is None:
        return None
    return Fact(
        (f.fuel_gauge_driver.value, f.fuel_gauges.value),
        f.fuel_gauge_driver.measured and f.fuel_gauges.measured,
        f.fuel_gauges.source,
    )


def _gauge_count(read, declared) -> int:
    return sum(1 for n in read if n.startswith(declared[0]))


# name, where it is read, reader, the declared fact, how a declared value is shown, does the read value match it, how a read value is shown
FACTS = (
    ("model", "/proc/device-tree/model", read_model, lambda f: f.model, str, lambda r, d: d.lower() in r.lower(), str),
    (
        "compatible",
        "/proc/device-tree/compatible",
        read_compatible,
        lambda f: f.compatible,
        str,
        lambda r, d: d in r,
        ",".join,
    ),
    ("memory", "/proc/meminfo", read_memory, lambda f: None, str, lambda r, d: False, lambda r: "%d kB" % r),
    ("panels", "/sys/class/drm/*/modes", read_panels, lambda f: f.panels, str, lambda r, d: r == d, str),
    (
        "panel_px",
        "/sys/class/drm/*/modes",
        read_panel_px,
        lambda f: f.panel_px,
        _px,
        lambda r, d: all(m == tuple(d) for m in r),
        lambda r: ",".join(_px(m) for m in r),
    ),
    (
        "lid",
        "/proc/bus/input/devices",
        read_input_names,
        lambda f: f.lid_name,
        lambda d: '"%s"' % d,
        lambda r, d: d in r,
        lambda r: "%d input devices" % len(r),
    ),
    (
        "fuel_gauges",
        "/sys/class/power_supply/*",
        read_power_supplies,
        _gauges,
        lambda d: "%d named %s*" % (d[1], d[0]),
        lambda r, d: _gauge_count(r, d) == d[1],
        lambda r: ",".join(r) or "none",
    ),
    (
        "backlights",
        "/sys/class/backlight/*",
        read_backlights,
        lambda f: f.backlights,
        str,
        lambda r, d: len(r) == d,
        lambda r: ",".join(r) or "none",
    ),
)
FACT_NAMES = tuple(f[0] for f in FACTS)


def read_kernel(root: str) -> dict:
    """Each fact as read under root; None where it could not be read."""
    return {name: reader(root) for name, _, reader, *_ in FACTS}


def compare(facts: DeviceFacts | None, root: str) -> list[Reading]:
    out = []
    for name, where, reader, declare, show_d, matches, show_r in FACTS:
        read = reader(root)
        fact = declare(facts) if facts is not None else None
        declared = None if fact is None else show_d(fact.value)
        measured = None if fact is None else fact.measured
        if read is None:
            status, shown = "UNREADABLE", None  # nothing was read, so nothing is compared
        elif fact is None:
            status, shown = "UNDECLARED", show_r(read)
        else:
            status, shown = ("MATCH" if matches(read, fact.value) else "DIFF"), show_r(read)
        out.append(Reading(name, status, declared, measured, shown, where))
    return out


def line(r: Reading) -> str:
    declared = "-" if r.declared is None else "%s (%s)" % (r.declared, "measured" if r.measured else "not yet measured")
    return "%s %s declared=%s read=%s from %s" % (
        r.status,
        r.name,
        declared,
        "-" if r.read is None else r.read,
        r.where,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python3 -m opos.hw.facts",
        description="Compare the declared hardware facts with what the kernel reports. Read-only.",
    )
    p.add_argument("--root", default="/", help="the filesystem root to read (default /)")
    p.add_argument(
        "--profile",
        choices=store.builtin_names(),
        help="the profile to compare with (default: the one opos.hw.detect picks)",
    )
    a = p.parse_args(argv)
    if a.profile is None:
        from opos.hw.detect import detect_or_generic

        profile = detect_or_generic(a.root)
    else:
        profile = store.builtin_profile(a.profile)
    readings = compare(profile.facts, a.root)
    for r in readings:
        print(line(r))
    counts = ", ".join("%d %s" % (sum(r.status == s for r in readings), s) for s in STATUSES)
    print("facts: %s (profile %s, root %s)" % (counts, profile.name, a.root))
    return 1 if any(r.status == "DIFF" for r in readings) else 0


if __name__ == "__main__":
    sys.exit(main())
