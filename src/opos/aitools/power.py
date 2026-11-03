# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

SUPPLY_DIR = Path("/sys/class/power_supply")


def _read(p: Path) -> str | None:
    """Read stripped file or None if missing."""
    try:
        return p.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return None


def _dirs(s: Path) -> list[Path]:
    """List subdirectories of supply dir."""
    try:
        return [q for q in s.iterdir() if q.is_dir()]
    except OSError:
        return []


def on_external_power(supply_dir: Path = SUPPLY_DIR) -> bool:
    """Return True if on external power."""
    ds = _dirs(supply_dir)
    has_bat = False
    for d in ds:
        if _read(d / "type") == "Battery":
            has_bat = True
            break
    if not has_bat:
        return True
    for d in ds:
        t = _read(d / "type")
        if t == "Battery":
            if _read(d / "status") in ("Charging", "Full", "Not charging"):
                return True
        else:
            if _read(d / "online") == "1":
                return True
    return False


def battery_percent(supply_dir: Path = SUPPLY_DIR) -> float | None:
    """Return battery capacity percent or None."""
    ds = sorted(_dirs(supply_dir), key=lambda p: p.name)
    bat: Path | None = None
    for d in ds:
        if _read(d / "type") == "Battery":
            bat = d
            break
    if bat is None:
        return None
    v = _read(bat / "capacity")
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except ValueError:
        return None
    if 0 <= f <= 100:
        return f
    return None
