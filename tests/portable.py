# SPDX-License-Identifier: GPL-3.0-or-later
"""What the suite needs to run the Linux image scripts on a developer machine that is not Linux (a Mac).

The image scripts under packaging/duo-os/ run on the phone and on a Linux builder, so they use GNU tools. CI runs on
Ubuntu, where nothing here changes anything: every helper is a no-op on Linux and every skip condition is false there.
"""
import functools
import os
import shutil
import subprocess
import sys
from pathlib import Path

LINUX = sys.platform.startswith("linux")


def _gnu(tool):
    path = shutil.which(tool)
    if not path:
        return False
    try:
        p = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return p.returncode == 0 and "GNU" in p.stdout


@functools.lru_cache(maxsize=None)
def gnu(*tools):
    """True when every tool on PATH is the GNU one (always checked, also on Linux)."""
    return all(_gnu(t) for t in tools)


def needs_gnu(*tools):
    """A skipUnless pair for a test whose subject runs these GNU tools (dd iflag=, stat -c, ...).

    It never skips on Linux: there a missing GNU tool is a real failure the test must report.
    """
    return LINUX or gnu(*tools), "the image script under test needs GNU %s (Linux); this host has %s" % (
        ", ".join(tools), sys.platform)


SED_SHIM = """#!/bin/sh
# GNU sed -i edits in place with no suffix; BSD sed reads the next word as the suffix. Give BSD sed its empty one.
for a do
  if [ -z "${shim_started:-}" ]; then set --; shim_started=1; fi
  if [ "$a" = "-i" ]; then set -- "$@" -i ''; else set -- "$@" "$a"; fi
done
exec %s "$@"
"""


STAT_SHIM = r"""#!%(python)s -I
# GNU `stat -c FORMAT [-L] FILE...` for a host whose stat is BSD's; anything else goes to the real stat.
import grp, os, pwd, stat, sys
args = sys.argv[1:]
follow, fmt, files = False, None, []
i = 0
while i < len(args):
    a = args[i]
    if a in ("-c", "--format") and i + 1 < len(args):
        fmt = args[i + 1]; i += 2; continue
    if a.startswith("--format="):
        fmt = a.split("=", 1)[1]
    elif a in ("-L", "--dereference"):
        follow = True
    elif a == "--":
        files.extend(args[i + 1:]); break
    else:
        files.append(a)
    i += 1
if fmt is None:
    os.execv(%(real)r, [%(real)r] + args)
def kind(m):
    return ("directory" if stat.S_ISDIR(m) else "symbolic link" if stat.S_ISLNK(m) else "fifo" if stat.S_ISFIFO(m)
            else "regular file" if stat.S_ISREG(m) else "other")
def name(f, n, ids):
    try:
        return f(n).pw_name if ids == "u" else f(n).gr_name
    except KeyError:
        return "UNKNOWN"
rc = 0
for path in files:
    try:
        st = os.stat(path) if follow else os.lstat(path)
    except OSError as e:
        sys.stderr.write("stat: cannot statx '%%s': %%s\n" %% (path, e.strerror)); rc = 1; continue
    out, j = [], 0
    while j < len(fmt):
        c = fmt[j]
        if c != "%%" or j + 1 == len(fmt):
            out.append(c); j += 1; continue
        d = fmt[j + 1]; j += 2
        if d == "a": out.append(format(stat.S_IMODE(st.st_mode), "o"))
        elif d == "u": out.append(str(st.st_uid))
        elif d == "g": out.append(str(st.st_gid))
        elif d == "U": out.append(name(pwd.getpwuid, st.st_uid, "u"))
        elif d == "G": out.append(name(grp.getgrgid, st.st_gid, "g"))
        elif d == "s": out.append(str(st.st_size))
        elif d == "Y": out.append(str(int(st.st_mtime)))
        elif d == "h": out.append(str(st.st_nlink))
        elif d == "i": out.append(str(st.st_ino))
        elif d == "n": out.append(path)
        elif d == "F": out.append("regular empty file" if stat.S_ISREG(st.st_mode) and not st.st_size else kind(st.st_mode))
        elif d == "%%": out.append("%%")
        else:
            sys.stderr.write("stat shim: %%%%%%s is not translated\n" %% d); sys.exit(2)
    sys.stdout.write("".join(out) + "\n")
sys.exit(rc)
"""


def gnu_stat_path(directory):
    """A directory to put first on PATH so that `stat -c FORMAT` answers as GNU stat does, or None when stat is GNU's.

    The answers come from os.stat/os.lstat of the same files; a format letter the shim does not know fails loudly.
    """
    if LINUX or gnu("stat"):
        return None
    real = shutil.which("stat", path="/usr/bin:/bin") or shutil.which("stat")
    if not real:
        return None
    d = Path(directory) / "gnu-stat"
    d.mkdir(parents=True, exist_ok=True)
    shim = d / "stat"
    shim.write_text(STAT_SHIM % {"python": sys.executable, "real": real}, newline="\n")
    shim.chmod(0o755)
    return str(d)


def gnu_tool(name, directory):
    """The path of a GNU-compatible `name` (sed or stat): the shim off Linux when the host's tool is BSD's, else which()."""
    make = {"sed": gnu_sed_path, "stat": gnu_stat_path}[name]
    d = make(directory)
    return str(Path(d) / name) if d else shutil.which(name)


def with_gnu_tools(path, directory, tools=("sed", "stat")):
    """PATH with the shims for these tools first, where the host needs them; PATH unchanged on Linux."""
    dirs = [d for d in ({"sed": gnu_sed_path, "stat": gnu_stat_path}[t](directory) for t in tools) if d]
    return os.pathsep.join(dirs + [path])


def gnu_sed_path(directory):
    """A directory to put first on PATH so that `sed -i` behaves as GNU sed's, or None when sed is already GNU's.

    Only the in-place flag is translated; every expression still runs on the real sed.
    """
    if LINUX or gnu("sed"):
        return None
    real = shutil.which("sed", path="/usr/bin:/bin") or shutil.which("sed")
    if not real:
        return None
    d = Path(directory) / "gnu-sed"
    d.mkdir(parents=True, exist_ok=True)
    shim = d / "sed"
    shim.write_text(SED_SHIM % real, newline="\n")
    shim.chmod(0o755)
    return str(d)


def with_gnu_sed(path, directory):
    """PATH with the sed shim first when one is needed, else PATH unchanged."""
    d = gnu_sed_path(directory)
    return path if d is None else d + os.pathsep + path
