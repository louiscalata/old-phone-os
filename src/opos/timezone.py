# SPDX-License-Identifier: GPL-3.0-or-later
"""The owner's time zone, applied without root.

The unprivileged OPOS user cannot change the system zone (/etc/localtime), so OPOS keeps its own: one line in the
config directory (<config>/timezone). The server applies it to itself at start (TZ and time.tzset()) and
packaging/duo-os/opos-kiosk exports it to the kiosk's Chromium, which reads TZ.

A zone name is only ever compared with the names found under /usr/share/zoneinfo (or, when that is missing, with the
short built-in list below); it is never joined into a path that gets opened, and it never reaches a shell.

  python3 -m opos.timezone              show the saved and the active zone
  python3 -m opos.timezone --list       the zone names OPOS offers
  python3 -m opos.timezone --set NAME   save a zone (from ssh; the server and the screen use it from their next restart)
"""
import contextlib
import os
import re
import sys
import tempfile
import time

from opos.setup.config import config_dir

ZONEINFO = "/usr/share/zoneinfo"
SETTING = "timezone"
_NAME = re.compile(r"[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+){0,2}")
# Left out of the list: copies of the same zones (posix/, right/, the old country-named directories) and fixed-offset Etc/ zones.
_SKIP_TOP = frozenset(("posix", "right", "SystemV", "Etc", "Brazil", "Canada", "Chile", "Mexico", "US"))
_MAX_ZONES = 2000
_CET = "CET-1CEST,M3.5.0,M10.5.0/3"
_EET = "EET-2EEST,M3.5.0/3,M10.5.0/4"
_US = ",M3.2.0,M11.1.0"
# Used only when the system zone database is missing. Fixed POSIX rules as of 2026: a country that changes its daylight
# saving makes an entry wrong until OPOS is updated, and the zone page says so.
BUILTIN = {
    "UTC": "UTC0",
    "Africa/Johannesburg": "SAST-2", "Africa/Lagos": "WAT-1", "Africa/Nairobi": "EAT-3",
    "America/Anchorage": "AKST9AKDT" + _US, "America/Argentina/Buenos_Aires": "<-03>3", "America/Bogota": "<-05>5",
    "America/Chicago": "CST6CDT" + _US, "America/Denver": "MST7MDT" + _US, "America/Halifax": "AST4ADT" + _US,
    "America/Los_Angeles": "PST8PDT" + _US, "America/Mexico_City": "CST6", "America/New_York": "EST5EDT" + _US,
    "America/Phoenix": "MST7", "America/Sao_Paulo": "<-03>3", "America/St_Johns": "NST3:30NDT" + _US,
    "America/Toronto": "EST5EDT" + _US,
    "Asia/Bangkok": "<+07>-7", "Asia/Colombo": "<+0530>-5:30", "Asia/Dhaka": "<+06>-6", "Asia/Dubai": "<+04>-4",
    "Asia/Hong_Kong": "HKT-8", "Asia/Jakarta": "WIB-7", "Asia/Karachi": "PKT-5", "Asia/Kathmandu": "<+0545>-5:45",
    "Asia/Kolkata": "IST-5:30", "Asia/Manila": "PST-8", "Asia/Riyadh": "<+03>-3", "Asia/Seoul": "KST-9",
    "Asia/Shanghai": "CST-8", "Asia/Singapore": "<+08>-8", "Asia/Tehran": "<+0330>-3:30", "Asia/Tokyo": "JST-9",
    "Atlantic/Reykjavik": "GMT0",
    "Australia/Adelaide": "ACST-9:30ACDT,M10.1.0,M4.1.0/3", "Australia/Brisbane": "AEST-10", "Australia/Perth": "AWST-8",
    "Australia/Sydney": "AEST-10AEDT,M10.1.0,M4.1.0/3",
    "Europe/Amsterdam": _CET, "Europe/Athens": _EET, "Europe/Berlin": _CET, "Europe/Brussels": _CET,
    "Europe/Copenhagen": _CET, "Europe/Dublin": "GMT0IST,M3.5.0/1,M10.5.0", "Europe/Helsinki": _EET,
    "Europe/Istanbul": "<+03>-3", "Europe/Kyiv": _EET, "Europe/Lisbon": "WET0WEST,M3.5.0/1,M10.5.0",
    "Europe/London": "GMT0BST,M3.5.0/1,M10.5.0", "Europe/Madrid": _CET, "Europe/Moscow": "MSK-3", "Europe/Oslo": _CET,
    "Europe/Paris": _CET, "Europe/Prague": _CET, "Europe/Rome": _CET, "Europe/Stockholm": _CET, "Europe/Vienna": _CET,
    "Europe/Warsaw": _CET, "Europe/Zurich": _CET,
    "Pacific/Auckland": "NZST-12NZDT,M9.5.0,M4.1.0/3", "Pacific/Honolulu": "HST10",
}


def _tzif(path):
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"TZif"
    except OSError:
        return False


def _scan(root):
    """Sorted zone names under root whose files really are zone data; [] when root is missing or empty."""
    out = []

    def walk(rel, depth):
        try:
            entries = sorted(os.scandir(os.path.join(root, rel) if rel else root), key=lambda e: e.name)
        except OSError:
            return
        for e in entries:
            if len(out) >= _MAX_ZONES or not _NAME.fullmatch(e.name):
                continue
            path = rel + "/" + e.name if rel else e.name
            try:
                is_dir = e.is_dir(follow_symlinks=False)
                is_file = not is_dir and e.is_file()
            except OSError:
                continue
            if is_dir:
                if depth < 2 and not (depth == 0 and e.name in _SKIP_TOP):
                    walk(path, depth + 1)
            elif is_file and (depth > 0 or e.name == "UTC") and _tzif(os.path.join(root, path)):
                out.append(path)

    walk("", 0)
    return sorted(out)


_SEEN = {}  # root -> (signature, names): the scan opens ~450 files, and every page load and every validation asks for it


def _signature(root):
    """Changes when the database does: the modification times of the root and of its region directories (None if unreadable)."""
    try:
        with os.scandir(root) as it:
            subdirs = sorted((e.name, e.stat(follow_symlinks=False).st_mtime_ns) for e in it if e.is_dir(follow_symlinks=False))
        return (os.stat(root).st_mtime_ns, tuple(subdirs))
    except OSError:
        return None


def zones(root=ZONEINFO):
    """(names, source): the zones found under root with source "system", else the built-in ones with source "builtin"."""
    key, sig = str(root), _signature(root)
    hit = _SEEN.get(key)
    if sig is not None and hit is not None and hit[0] == sig:
        found = list(hit[1])
    else:
        found = _scan(root)
        if sig is not None:
            _SEEN[key] = (sig, tuple(found))
    return (found, "system") if found else (sorted(BUILTIN), "builtin")


def _match(name, root):
    """(zone, source): the listed zone that equals name exactly (None if there is none) and which list was used."""
    names, source = zones(root)
    if not isinstance(name, str) or len(name) > 64 or not _NAME.fullmatch(name) or name not in names:
        return None, source
    return names[names.index(name)], source


def valid_zone(name, root=ZONEINFO):
    """The listed zone that equals name exactly, or None. Nothing else is accepted: no paths, no patterns, no odd characters."""
    return _match(name, root)[0]


def tz_value(name, root=ZONEINFO):
    """What TZ must hold in this process for a listed zone: the name when the system database has it, else its built-in rule."""
    zone, source = _match(name, root)
    if zone is None:
        raise ValueError("unknown time zone")
    return zone if source == "system" else BUILTIN[zone]


def setting_path(env=None):
    return config_dir(env) / SETTING


def load_setting(env=None, root=ZONEINFO):
    """The saved zone, or None when nothing valid is saved."""
    try:
        with open(setting_path(env), "rb") as f:
            raw = f.read(129)
        lines = raw.decode("ascii").splitlines()
    except (OSError, ValueError):
        return None
    return valid_zone(lines[0].strip(), root) if lines else None


def save_setting(name, env=None, root=ZONEINFO):
    """Save a valid zone as the owner's setting (atomic). ValueError for an unknown name."""
    zone = valid_zone(name, root)
    if zone is None:
        raise ValueError("unknown time zone")
    path = setting_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.parent.chmod(0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(zone + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return zone


def apply(name, root=ZONEINFO):
    """Make this process use the zone: TZ and time.tzset(). ValueError for an unknown name."""
    zone, source = _match(name, root)
    if zone is None:
        raise ValueError("unknown time zone")
    os.environ["TZ"] = zone if source == "system" else BUILTIN[zone]
    if hasattr(time, "tzset"):
        time.tzset()
    return zone


def apply_saved(env=None, root=ZONEINFO):
    """At server start: apply the saved zone if there is one. Returns it, or None (and changes nothing). Never raises."""
    try:
        zone = load_setting(env, root)
        return apply(zone, root) if zone else None
    except (OSError, ValueError):
        return None


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--list"]:
        names, src = zones()
        print("\n".join(names))
        print(f"({len(names)} zones, {src} list)", file=sys.stderr)
        return 0
    if len(args) == 2 and args[0] == "--set":
        try:
            zone = save_setting(args[1])
            apply(zone)
        except ValueError:
            print("opos.timezone: not a zone in the list (see --list)", file=sys.stderr)
            return 2
        except OSError as e:
            print(f"opos.timezone: cannot save: {e.__class__.__name__}", file=sys.stderr)
            return 1
        print(f"saved {zone}; the server uses it from its next start and the screen from its next restart")
        return 0
    if args:
        print("usage: python3 -m opos.timezone [--list | --set NAME]", file=sys.stderr)
        return 2
    saved = load_setting()
    print(f"saved zone: {saved or 'none (system default)'}")
    print(f"TZ in this shell: {os.environ.get('TZ', '(unset)')}")
    print(f"list: {zones()[1]} ({len(zones()[0])} zones)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
