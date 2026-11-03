# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from opos.aitools.catalog import Tool
MODES = ("lite", "full")
def normalize_arch(machine: str) -> str | None:
    """Normalize CPU architecture string."""
    v = machine.lower()
    if v in ("aarch64", "arm64"):
        return "aarch64"
    if v in ("x86_64", "amd64"):
        return "x86_64"
    return None
def choose_prefix(env: Mapping[str, str], home: Path, persistent_roots: Sequence[str] = ("/userdata",)) -> Path:
    """Choose storage prefix for tools."""
    p = env.get("OPOS_TOOLS_PREFIX")
    if p and Path(p).is_absolute():
        return Path(p)
    for r in persistent_roots:
        rp = Path(r)
        if rp.is_dir() and os.access(r, os.W_OK):
            return rp / "opos"
    return home / ".local" / "share" / "opos"
def free_mb(path: Path) -> int:
    """Free space for path in MiB."""
    cur = path
    while not cur.exists():
        par = cur.parent
        if par == cur:
            break
        cur = par
    return shutil.disk_usage(cur).free // (1024 * 1024)
def volatile_warning(prefix: Path, mounts_file: Path = Path("/proc/mounts")) -> str | None:
    """Warn if prefix is on volatile filesystem."""
    try:
        text = mounts_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    best = ""
    fstype_best = ""
    pref = str(prefix)
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mnt = parts[1].replace("\\040", " ")
        ftype = parts[2]
        if pref == mnt or mnt == "/" or pref.startswith(mnt.rstrip("/") + "/"):
            if len(mnt) > len(best):
                best = mnt
                fstype_best = ftype
    if fstype_best in ("tmpfs", "ramfs", "overlay", "overlayfs"):
        return f"warning: {prefix} is on {fstype_best} and will be lost on reboot"
    return None
def preflight(tool: Tool, prefix: Path, machine: str, free_mb_fn: Callable[[Path], int] = free_mb) -> list[str]:
    """Check arch and disk space for tool."""
    probs: list[str] = []
    arch = normalize_arch(machine)
    if arch is None or arch not in tool.archs:
        probs.append(f"unsupported CPU arch '{machine}' for this tool")
    free = free_mb_fn(prefix)
    if free < tool.min_free_mb:
        probs.append(f"only {free} MB free, {tool.min_free_mb} MB required")
    return probs
