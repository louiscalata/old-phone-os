# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib
import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from opos.aitools import launcher, place
from opos.aitools.catalog import Tool


class InstallError(RuntimeError):
    """installer failure"""


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """reject non-https redirects"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://"):
            raise InstallError("redirect to non-https")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


Fetch = Callable[[str], bytes]
Runner = Callable[[Sequence[str], Mapping[str, str], float], tuple[int, str]]


def default_fetch(url: str, max_bytes: int = 2_000_000, timeout_s: float = 30.0) -> bytes:
    """fetch https url with size limit"""
    if not url.startswith("https://"):
        raise InstallError("https only")
    if any(c.isspace() for c in url):
        raise InstallError("bad url")
    req = urllib.request.Request(url)  # noqa: S310
    opener = urllib.request.build_opener(_HttpsOnlyRedirectHandler())  # noqa: S310
    try:
        with opener.open(req, timeout=timeout_s) as r:  # noqa: S310
            final = r.geturl() if hasattr(r, "geturl") else url
            if not final.startswith("https://"):
                raise InstallError("redirect to non-https")
            data = r.read(max_bytes + 1)
    except InstallError:
        raise
    except Exception as e:
        raise InstallError(str(e)) from e
    if len(data) > max_bytes:
        raise InstallError(f"too large: {len(data)} > {max_bytes}")
    return data


def default_runner(argv: Sequence[str], env: Mapping[str, str], timeout_s: float) -> tuple[int, str]:
    """run subprocess without shell"""
    try:
        cp = subprocess.run(list(argv), env=dict(env), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout_s, text=True)
        return cp.returncode, (cp.stdout or "")[-2000:]
    except subprocess.TimeoutExpired:
        return 124, "timed out"


def tools_root(prefix: Path) -> Path:
    """tools directory under prefix"""
    return prefix / "opos-tools"


def manifest_path(prefix: Path) -> Path:
    """manifest file path"""
    return tools_root(prefix) / "manifest.json"


def read_manifest(prefix: Path) -> dict:
    """read manifest or empty dict"""
    p = manifest_path(prefix)
    if not p.exists():
        return {}
    try:
        t = p.read_text(encoding="utf-8")
        d = json.loads(t)
    except Exception as e:
        raise InstallError(str(e)) from e
    if not isinstance(d, dict):
        raise InstallError("manifest not a dict")
    return d


def _inside(child: Path, parent: Path) -> bool:
    """check child is inside parent"""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _runner_env(sandbox: Path, extra: Mapping[str, str]) -> dict[str, str]:
    """env for vendor installer"""
    e: dict[str, str] = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(sandbox), "LANG": "C.UTF-8", "TERM": "dumb"}
    e.update(extra)
    return e


def _atomic_manifest(path: Path, data: dict) -> None:
    """write manifest atomically with 0600"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _atomic_write_text(path: Path, text: str, mode: int) -> None:
    """write text atomically"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _atomic_write_bytes(path: Path, data: bytes, mode: int) -> None:
    """write bytes atomically"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _ensure_dirs(sandbox: Path, tid_dir: Path, bin_dir: Path, run_dir: Path) -> None:
    """create required directories"""
    tid_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(tid_dir, 0o700)
    sandbox.mkdir(parents=True, exist_ok=True)
    os.chmod(sandbox, 0o700)
    bin_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)


def _remove_fresh_dir(tid_dir: Path, tr: Path, had: bool) -> None:
    """remove newly created tool dir on abort"""
    if had:
        return
    with contextlib.suppress(Exception):
        if tid_dir.is_dir() and not tid_dir.is_symlink() and _inside(tid_dir, tr):
            shutil.rmtree(tid_dir)


def install(tool: Tool, *, prefix: Path, mode: str, machine: str, home: Path, python: str, src_dir: Path, fetch: Fetch = default_fetch, runner: Runner = default_runner, free_mb_fn: Callable[[Path], int] = place.free_mb, profiles: Sequence[Path] | None = None, dry_run: bool = False, force: bool = False, all_tools: Sequence[Tool] | None = None) -> list[str]:
    """install tool and update manifest"""
    if mode not in place.MODES:
        raise InstallError(f"unknown mode: {mode}")
    probs = place.preflight(tool, prefix, machine, free_mb_fn)
    if probs:
        raise InstallError("; ".join(probs))
    tr = tools_root(prefix)
    sandbox = tr / tool.id / "home"
    real_bin = sandbox / ".local" / "bin" / tool.binary
    bin_dir = tr / "bin"
    wrapper = bin_dir / tool.binary
    run_dir = tr / "run"
    env_file = tr / "env.sh"
    tid_dir = tr / tool.id
    if tid_dir.is_symlink() or sandbox.is_symlink():
        raise InstallError(f"refusing symlink: {tid_dir if tid_dir.is_symlink() else sandbox}")
    prev = read_manifest(prefix)
    prev_tools = dict(prev.get("tools", {})) if isinstance(prev.get("tools"), dict) else {}
    had = tool.id in prev_tools
    lookup: dict[str, Tool] = {tool.id: tool}
    if all_tools is not None:
        for t in all_tools:
            lookup[t.id] = t
    regen_ids = [tid for tid in prev_tools if tid != tool.id and isinstance(prev_tools.get(tid), dict)]
    regen_needed = False
    if regen_ids:
        if prev.get("mode") != mode or prev.get("python") != python or str(prev.get("src_dir", "")) != str(src_dir):
            regen_needed = True
        else:
            for tid in regen_ids:
                if tid not in lookup:
                    continue
                t = lookup[tid]
                meta = prev_tools[tid]
                sb = Path(meta.get("sandbox", str(tr / tid / "home")))
                rb = sb / ".local" / "bin" / t.binary
                wp = tr / "bin" / t.binary
                try:
                    exp = launcher.render_wrapper(t, rb, sb, run_dir, mode, python, src_dir)
                except Exception:
                    regen_needed = True
                    break
                try:
                    cur_text = wp.read_text(encoding="utf-8") if wp.is_file() else None
                except Exception:
                    cur_text = None
                if cur_text != exp:
                    regen_needed = True
                    break
    changing = bool(prev_tools) and bool(regen_ids) and regen_needed
    if changing:
        missing = [tid for tid in regen_ids if tid not in lookup]
        if missing:
            raise InstallError(f"missing catalog entry: {missing[0]}")
    planned: dict[Path, str] = {}
    try:
        if changing:
            for tid in regen_ids:
                t = lookup[tid]
                meta = prev_tools[tid]
                sb = Path(meta.get("sandbox", str(tr / tid / "home")))
                rb = sb / ".local" / "bin" / t.binary
                planned[tr / "bin" / t.binary] = launcher.render_wrapper(t, rb, sb, run_dir, mode, python, src_dir)
        planned[wrapper] = launcher.render_wrapper(tool, real_bin, sandbox, run_dir, mode, python, src_dir)
        etext = launcher.render_env(bin_dir)
    except InstallError:
        raise
    except Exception as e:
        raise InstallError(str(e)) from e
    if dry_run:
        return [f"would install {tool.id} to {prefix}", f"would fetch {tool.installer_url}", f"Sign in yourself: {tool.signin}"]
    _ensure_dirs(sandbox, tid_dir, bin_dir, run_dir)
    need_fetch = force or not (real_bin.is_file() and os.access(real_bin, os.X_OK))
    if need_fetch:
        sp = ""
        try:
            data = fetch(tool.installer_url)
            fd, sp = tempfile.mkstemp(dir=str(tid_dir))
            try:
                off = 0
                while off < len(data):
                    n = os.write(fd, data[off:])
                    if n == 0:
                        raise OSError("short write: incomplete installer")
                    off += n
            except Exception:
                with contextlib.suppress(Exception):
                    os.close(fd)
                raise
            else:
                os.close(fd)
            os.chmod(sp, 0o700)
            env = _runner_env(sandbox, tool.installer_env)
            rc, out = runner([tool.interpreter, sp, *tool.installer_args], env, 900.0)
            if rc != 0:
                raise InstallError(f"installer failed ({rc}): {out}")
            if not (real_bin.is_file() and os.access(real_bin, os.X_OK)):
                raise InstallError(f"installer did not create {real_bin}")
        except Exception:
            if not had:
                with contextlib.suppress(Exception):
                    if tid_dir.is_dir() and not tid_dir.is_symlink() and _inside(tid_dir, tr):
                        shutil.rmtree(tid_dir)
                with contextlib.suppress(Exception):
                    c2 = read_manifest(prefix)
                    if not c2.get("tools"):
                        for p in (bin_dir, run_dir, tr):
                            with contextlib.suppress(OSError):
                                p.rmdir()
            raise
        finally:
            with contextlib.suppress(Exception):
                if sp:
                    os.unlink(sp)
    if profiles is None:
        cands: list[Path] = [home / ".profile"]
        brc = home / ".bashrc"
        if brc.is_file():
            cands.append(brc)
        profiles = cands
    got: list[str] = []
    for p in profiles:
        launcher.add_profile_block(p, env_file)
        with contextlib.suppress(Exception):
            if launcher.BEGIN in p.read_text(encoding="utf-8", errors="ignore"):
                got.append(str(p))
    cur = prev
    tools_dict = dict(prev_tools)
    tools_dict[tool.id] = {"binary": tool.binary, "sandbox": str(sandbox), "signin": tool.signin, "mode": mode}
    if changing:
        for tid in regen_ids:
            existing = tools_dict.get(tid)
            if isinstance(existing, dict):
                updated = dict(existing)
                updated["mode"] = mode
                tools_dict[tid] = updated
    old_profiles = cur.get("profiles", []) if isinstance(cur.get("profiles"), list) else []
    merged: list[str] = []
    seen: set[str] = set()
    for x in list(old_profiles) + got:
        if x not in seen:
            seen.add(x)
            merged.append(x)
    new_manifest = {"version": 1, "mode": mode, "python": python, "src_dir": str(src_dir), "profiles": merged, "tools": tools_dict}
    mp = manifest_path(prefix)
    try:
        old_bytes = mp.read_bytes() if mp.is_file() else None
    except OSError as e:
        _remove_fresh_dir(tid_dir, tr, had)
        raise InstallError(str(e)) from e
    backups: dict[Path, bytes | None] = {}
    for p in list(planned) + [env_file]:
        try:
            backups[p] = p.read_bytes() if p.is_file() else None
        except OSError as e:
            _remove_fresh_dir(tid_dir, tr, had)
            raise InstallError(str(e)) from e
    manifest_replaced = False
    try:
        for p, text in planned.items():
            _atomic_write_text(p, text, 0o755)
        _atomic_write_text(env_file, etext, 0o644)
        _atomic_manifest(mp, new_manifest)
        manifest_replaced = True
    except Exception as e:
        rollback_errors: list[str] = []
        for p, b in backups.items():
            try:
                if b is None:
                    if p.is_file() or p.is_symlink():
                        p.unlink()
                else:
                    mode = 0o644 if p == env_file else 0o755
                    _atomic_write_bytes(p, b, mode)
            except Exception as re:
                rollback_errors.append(f"{p}: {re}")
        if manifest_replaced:
            try:
                if old_bytes is None:
                    if mp.is_file() or mp.is_symlink():
                        mp.unlink()
                else:
                    _atomic_write_bytes(mp, old_bytes, 0o600)
            except Exception as re:
                rollback_errors.append(f"{mp}: {re}")
        _remove_fresh_dir(tid_dir, tr, had)
        msg = str(e)
        if rollback_errors:
            msg += f" (rollback failed: {'; '.join(rollback_errors)})"
        raise InstallError(msg) from e
    return [f"installed {tool.id} to {prefix}", f"wrapper {wrapper}", f"Sign in yourself: {tool.signin}"]


def set_mode(prefix: Path, mode: str, tools: Sequence[Tool]) -> list[str]:
    """update wrappers for new mode"""
    if mode not in place.MODES:
        raise InstallError(f"unknown mode: {mode}")
    cur = read_manifest(prefix)
    if not cur or not cur.get("tools"):
        raise InstallError("nothing installed")
    tr = tools_root(prefix)
    run_dir = tr / "run"
    lookup = {t.id: t for t in tools}
    py = cur.get("python", "python3")
    sd = Path(cur.get("src_dir", "."))
    entries = list(cur["tools"].items())
    missing = [tid for tid, _ in entries if tid not in lookup]
    planned: dict[Path, str] = {}
    try:
        for tid, meta in entries:
            if tid not in lookup:
                continue
            t = lookup[tid]
            if not isinstance(meta, dict):
                raise InstallError(f"bad manifest entry: {tid}")
            sandbox = Path(meta.get("sandbox", str(tr / tid / "home")))
            real_bin = sandbox / ".local" / "bin" / t.binary
            planned[tr / "bin" / t.binary] = launcher.render_wrapper(t, real_bin, sandbox, run_dir, mode, py, sd)
    except InstallError:
        raise
    except Exception as e:
        raise InstallError(str(e)) from e
    mp = manifest_path(prefix)
    try:
        old_bytes = mp.read_bytes()
    except OSError as e:
        raise InstallError(str(e)) from e
    backups: dict[Path, bytes | None] = {}
    for p in planned:
        try:
            backups[p] = p.read_bytes() if p.is_file() else None
        except OSError as e:
            raise InstallError(str(e)) from e
    prev_mode = cur.get("mode")
    manifest_replaced = False
    try:
        for p, text in planned.items():
            _atomic_write_text(p, text, 0o755)
        tools_map = cur.get("tools")
        if isinstance(tools_map, dict):
            for tid, meta in entries:
                entry = tools_map.get(tid)
                if not isinstance(entry, dict) or not isinstance(meta, dict):
                    continue
                if tid in lookup:
                    entry["mode"] = mode
                elif "mode" not in entry and isinstance(prev_mode, str):
                    entry["mode"] = prev_mode
        cur["mode"] = mode
        _atomic_manifest(mp, cur)
        manifest_replaced = True
    except Exception as e:
        rollback_errors: list[str] = []
        for p, b in backups.items():
            try:
                if b is None:
                    if p.is_file() or p.is_symlink():
                        p.unlink()
                else:
                    _atomic_write_bytes(p, b, 0o755)
            except Exception as re:
                rollback_errors.append(f"{p}: {re}")
        if manifest_replaced:
            try:
                _atomic_write_bytes(mp, old_bytes, 0o600)
            except Exception as re:
                rollback_errors.append(f"{mp}: {re}")
        msg = str(e)
        if rollback_errors:
            msg += f" (rollback failed: {'; '.join(rollback_errors)})"
        raise InstallError(msg) from e
    out = [f"mode set to {mode}"]
    for tid in missing:
        out.append(f"missing catalog entry: {tid} (wrapper left unchanged; restore this tool's original catalog entry, including installer_url, then rerun the mode command)")
    return out


def uninstall(prefix: Path, *, dry_run: bool = False) -> list[str]:
    """remove installed tools"""
    mp = manifest_path(prefix)
    if not mp.exists():
        return ["nothing to remove"]
    cur = read_manifest(prefix)
    if not cur:
        return ["nothing to remove"]
    tr = tools_root(prefix)
    if tr.is_symlink():
        raise InstallError(f"refusing to remove symlink {tr}")
    if not _inside(tr, prefix):
        raise InstallError(f"refusing to remove {tr} not inside {prefix}")
    profs = cur.get("profiles", []) if isinstance(cur.get("profiles"), list) else []
    if dry_run:
        out: list[str] = []
        for s in profs:
            out.append(f"would remove profile block from {s}")
        out.append(f"would remove {tr}")
        return out
    out: list[str] = []
    for s in profs:
        p = Path(s)
        try:
            changed = launcher.remove_profile_block(p)
            if changed:
                if not p.exists():
                    out.append(f"removed {p}")
                else:
                    out.append(f"removed profile block from {p}")
        except Exception as e:
            out.append(f"could not remove profile block from {s}: {e}")
    try:
        if tr.is_dir() and not tr.is_symlink() and _inside(tr, prefix):
            shutil.rmtree(tr)
            out.append(f"removed {tr}")
        elif tr.exists():
            out.append(f"could not remove {tr}: not a directory")
    except Exception as e:
        out.append(f"could not remove {tr}: {e}")
    try:
        if prefix.is_dir() and not prefix.is_symlink():
            if not any(prefix.iterdir()):
                prefix.rmdir()
                out.append(f"removed {prefix}")
    except Exception as e:
        out.append(f"could not remove {prefix}: {e}")
    return out
