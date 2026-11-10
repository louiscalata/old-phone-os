# SPDX-License-Identifier: GPL-3.0-or-later
"""One brightness value for both Duo panels.

The vendor curve maps an OS level (1-255) to a raw backlight value. Floor 3, cap 245.
Both panel nodes get the same number. A restore file is written for the lid helper.
Not measured on a phone.
"""
from __future__ import annotations

import os
from pathlib import Path

# (os level, raw backlight). Piecewise linear between these knots.
CURVE = (
    (1, 3),
    (20, 22),
    (40, 48),
    (60, 74),
    (90, 107),
    (128, 147),
    (200, 211),
    (255, 245),
)
FLOOR = 3
CAP = 245
PANELS = ("panel0-backlight", "panel1-backlight")


def panel_value(level: int) -> int:
    """Raw backlight for an OS level. Values outside 1-255 clamp to the floor or the cap."""
    try:
        level = int(level)
    except (TypeError, ValueError):
        return FLOOR
    if level <= CURVE[0][0]:
        return FLOOR
    if level >= CURVE[-1][0]:
        return CAP
    for (x0, y0), (x1, y1) in zip(CURVE, CURVE[1:]):
        if x0 <= level <= x1:
            span = x1 - x0
            value = y0 if span == 0 else round(y0 + (level - x0) * (y1 - y0) / span)
            if value < FLOOR:
                return FLOOR
            if value > CAP:
                return CAP
            return value
    return CAP


def apply(root, level, runtime_dir=None) -> int:
    """Write the same raw value to both panels. Return that value.

    Missing panel nodes are skipped. runtime_dir receives sfduo-brightness when given.
    """
    value = panel_value(level)
    base = Path(root) / "sys/class/backlight"
    for name in PANELS:
        path = base / name / "brightness"
        if path.parent.is_dir():
            path.write_text(f"{value}\n", encoding="utf-8")
    if runtime_dir is not None:
        dest = Path(runtime_dir) / "sfduo-brightness"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f"{value}\n", encoding="utf-8")
    return value


def main(argv=None) -> int:
    level = os.environ.get("OPOS_BRIGHTNESS", "128")
    runtime = os.environ.get("OPOS_BRIGHTNESS_DIR")
    root = os.environ.get("OPOS_SYS_ROOT", "/")
    apply(root, level, runtime)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
