# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import contextlib
import os
import shlex
import tempfile
from opos.aitools.catalog import Tool
BEGIN = "# >>> opos coding tools >>>"
END = "# <<< opos coding tools <<<"
def _target(p: Path) -> Path:
    """Resolve symlink."""
    return p.resolve() if p.is_symlink() else p
def _atomic_write(t: Path, d: bytes) -> None:
    """Write atomically."""
    t.parent.mkdir(parents=True, exist_ok=True)
    m = t.stat().st_mode & 0o777 if t.exists() else 0o644
    if m == 0:
        m = 0o644
    fd, tp = tempfile.mkstemp(dir=str(t.parent))
    try:
        os.write(fd, d)
        os.close(fd)
        fd = -1
        os.chmod(tp, m)
        os.replace(tp, str(t))
    finally:
        if fd != -1:
            with contextlib.suppress(Exception):
                os.close(fd)
        with contextlib.suppress(Exception):
            os.unlink(tp)
def render_wrapper(tool: Tool, real_bin: Path, sandbox: Path, run_dir: Path, mode: str, python: str, src_dir: Path) -> str:
    """Render wrapper."""
    lines = ["#!/bin/sh", "# SPDX-License-Identifier: GPL-3.0-or-later"]
    if mode == "lite":
        msg = f"{tool.name} runs only while the phone is charging (Lite mode). Plug in power and try again, or switch to Full mode with: {python} -m opos.aitools mode full"
        qmsg = shlex.quote(msg)
        lines.append('d="${OPOS_POWER_SUPPLY_DIR:-/sys/class/power_supply}"')
        lines.append("has_battery=0")
        lines.append("ok=0")
        lines.append('for p in "$d"/*; do')
        lines.append('  [ -d "$p" ] || continue')
        lines.append('  t=""; [ -r "$p/type" ] && read -r t < "$p/type" 2>/dev/null || true')
        lines.append('  if [ "$t" = "Battery" ]; then')
        lines.append("    has_battery=1")
        lines.append('    s=""; [ -r "$p/status" ] && read -r s < "$p/status" 2>/dev/null || true')
        lines.append('    if [ "$s" = "Charging" ] || [ "$s" = "Full" ] || [ "$s" = "Not charging" ]; then ok=1; fi')
        lines.append("  else")
        lines.append('    o=""; [ -r "$p/online" ] && read -r o < "$p/online" 2>/dev/null || true')
        lines.append('    if [ "$o" = "1" ]; then ok=1; fi')
        lines.append("  fi")
        lines.append("done")
        lines.append('if [ "$has_battery" = "1" ] && [ "$ok" = "0" ]; then')
        lines.append(f"  printf '%s\\n' {qmsg} >&2")
        lines.append("  exit 75")
        lines.append("fi")
    for k, v in tool.run_env.items():
        v2 = v.replace("{sandbox}", str(sandbox))
        lines.append(f"export {k}={shlex.quote(v2)}")
    if mode == "lite":
        for k, v in tool.lite_env.items():
            v2 = v.replace("{sandbox}", str(sandbox))
            lines.append(f"export {k}={shlex.quote(v2)}")
    lines.append(f"mkdir -p {shlex.quote(str(run_dir))}")
    lines.append(f"printf '%s' {shlex.quote(tool.id)} > {shlex.quote(str(run_dir))}/{shlex.quote(tool.id)}.$$")
    q_src = shlex.quote(str(src_dir))
    q_py = shlex.quote(python)
    q_run = shlex.quote(str(run_dir))
    lines.append(f"PYTHONPATH={q_src} nohup {q_py} -m opos.aitools guard --run-dir {q_run} >/dev/null 2>&1 </dev/null &")
    lines.append(f"exec {shlex.quote(str(real_bin))} \"$@\"")
    return "\n".join(lines) + "\n"
def render_env(bin_dir: Path) -> str:
    """Render env snippet."""
    return f"# SPDX-License-Identifier: GPL-3.0-or-later\nexport PATH={shlex.quote(str(bin_dir))}:\"$PATH\"\n"
def add_profile_block(profile: Path, env_file: Path) -> bool:
    """Add block."""
    q = shlex.quote(str(env_file))
    block = f"{BEGIN}\nif [ -r {q} ]; then . {q}; else :; fi\n{END}\n".encode()
    b_begin = BEGIN.encode()
    tgt = _target(profile)
    if tgt.exists():
        data = tgt.read_bytes()
        if b_begin in data:
            return False
        if data and not data.endswith(b"\n"):
            data += b"\n"
        data += block
        _atomic_write(tgt, data)
        return True
    tgt.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(tgt, block)
    return True
def remove_profile_block(profile: Path) -> bool:
    """Remove block."""
    tgt = _target(profile)
    if not tgt.exists():
        return False
    data = tgt.read_bytes()
    b_begin = BEGIN.encode()
    b_end = END.encode()
    if b_begin not in data:
        return False
    parts = data.splitlines(keepends=True)
    out: list[bytes] = []
    n = len(parts)
    i = 0
    changed = False
    while i < n:
        core = parts[i].rstrip(b"\r\n")
        if core == b_begin:
            j = -1
            for k in range(i + 1, n):
                if parts[k].rstrip(b"\r\n") == b_end:
                    j = k
                    break
            if j == -1:
                return False
            changed = True
            i = j + 1
            continue
        out.append(parts[i])
        i += 1
    if not changed:
        return False
    out_b = b"".join(out)
    if out_b == b"":
        tgt.unlink()
    else:
        _atomic_write(tgt, out_b)
    return True
