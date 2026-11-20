# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib
import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from opos import muse
from opos.aitools import launcher, place
from opos.aitools.catalog import SHA256_RE, Tool


class InstallError(RuntimeError):
    """installer failure"""


Fetch = Callable[[str], bytes]
Runner = Callable[[Sequence[str], Mapping[str, str], float], tuple[int, str]]
Opener = Callable[..., object]


def _origin(url: str) -> tuple[str, str | None, int | None]:
    """scheme, host and port of a url"""
    parts = urllib.parse.urlsplit(url)
    return parts.scheme, parts.hostname, parts.port


def _printable(text: object, limit: int = 120) -> str:
    """text without control characters, cut to limit"""
    return "".join(ch for ch in str(text) if ord(ch) >= 32 and ord(ch) != 127)[:limit]


def default_fetch(url: str, max_bytes: int = 2_000_000, timeout_s: float = 30.0, opener: Opener | None = None) -> bytes:
    """Fetch an https installer with a size limit; ignores proxy settings and follows no redirects."""
    if not url.startswith("https://"):
        raise InstallError("https only")
    if any(c.isspace() for c in url):
        raise InstallError("bad url")
    if opener is None:
        opener = muse._open_no_redirect  # no proxies, no redirects
    req = urllib.request.Request(url)  # noqa: S310
    try:
        with opener(req, timeout=timeout_s) as r:  # type: ignore[attr-defined]
            final = r.geturl() if hasattr(r, "geturl") else url
            # A custom opener could follow a redirect; only the requested https host is accepted.
            if not isinstance(final, str) or not final.startswith("https://") or _origin(final) != _origin(url):
                raise InstallError(
                    f"redirect not followed: {url} ended at {_printable(final)}; OPOS downloads installers only from the catalog URL"
                )
            data = r.read(max_bytes + 1)
    except InstallError:
        raise
    except urllib.error.HTTPError as e:
        location = e.headers.get("Location") if e.headers is not None else None
        with contextlib.suppress(Exception):
            e.close()
        if 300 <= e.code < 400:
            raise InstallError(
                f"redirect not followed: {url} answered HTTP {e.code} to {_printable(location or 'another address')}; OPOS downloads installers only from the catalog URL, so update installer_url and installer_sha256"
            ) from e
        raise InstallError(f"HTTP {e.code} from {url}") from e
    except Exception as e:
        raise InstallError(str(e)) from e
    if len(data) > max_bytes:
        raise InstallError(f"too large: {len(data)} > {max_bytes}")
    return data


def digest_problem(tool: Tool) -> str | None:
    """Why the catalog entry cannot be installed safely, or None"""
    if not tool.installer_sha256:
        return f"no installer_sha256 in the catalog for {tool.installer_url}; OPOS runs a downloaded installer only after checking its sha256"
    if not SHA256_RE.fullmatch(tool.installer_sha256):
        return f"installer_sha256 for {tool.id} is not 64 lowercase hex characters"
    return None


def _check_digest(tool: Tool, path: str) -> None:
    """refuse to run a downloaded installer whose sha256 differs from the catalog"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    got = h.hexdigest()
    if got != tool.installer_sha256:
        when = f" (measured {tool.installer_sha256_checked})" if tool.installer_sha256_checked else ""
        raise InstallError(
            f"the vendor installer changed: {tool.installer_url} now has sha256 {got[:16]}..., the catalog lists {tool.installer_sha256[:16]}...{when}; nothing was run. Review the new script, then update installer_sha256 in the catalog."
        )


def default_runner(argv: Sequence[str], env: Mapping[str, str], timeout_s: float) -> tuple[int, str]:
    """run subprocess without shell"""
    try:
        cp = subprocess.run(
            list(argv),
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_s,
            text=True,
        )
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


def _manifest_sandbox(tr: Path, tid: str, meta: Mapping) -> Path:
    """sandbox recorded in a manifest entry; it must be a string that resolves inside the tools root"""
    raw = meta.get("sandbox")
    if raw is None:
        return tr / tid / "home"
    if not isinstance(raw, str) or not raw or "\0" in raw or not _inside(Path(raw), tr):
        raise InstallError(f"bad sandbox in manifest entry: {tid}")
    return Path(raw)


def _runner_env(sandbox: Path, extra: Mapping[str, str]) -> dict[str, str]:
    """env for vendor installer"""
    e: dict[str, str] = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(sandbox),
        "LANG": "C.UTF-8",
        "TERM": "dumb",
    }
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


@dataclasses.dataclass(frozen=True)
class _Layout:
    """paths install() uses for one tool"""

    tr: Path
    tid_dir: Path
    sandbox: Path
    real_bin: Path
    bin_dir: Path
    wrapper: Path
    run_dir: Path
    env_file: Path


def _layout(prefix: Path, tool: Tool) -> _Layout:
    """paths for tool under prefix"""
    tr = tools_root(prefix)
    sandbox = tr / tool.id / "home"
    bin_dir = tr / "bin"
    return _Layout(
        tr=tr,
        tid_dir=tr / tool.id,
        sandbox=sandbox,
        real_bin=sandbox / ".local" / "bin" / tool.binary,
        bin_dir=bin_dir,
        wrapper=bin_dir / tool.binary,
        run_dir=tr / "run",
        env_file=tr / "env.sh",
    )


def _refuse_early(tool: Tool, prefix: Path, mode: str, machine: str, free_mb_fn: Callable[[Path], int]) -> None:
    """mode, preflight and catalog digest checks, in that order, before anything is read or written"""
    if mode not in place.MODES:
        raise InstallError(f"unknown mode: {mode}")
    probs = place.preflight(tool, prefix, machine, free_mb_fn)
    if probs:
        raise InstallError("; ".join(probs))
    bad_digest = digest_problem(tool)
    if bad_digest:
        raise InstallError(bad_digest + ". Nothing was downloaded.")


def _lookup(tool: Tool, all_tools: Sequence[Tool] | None) -> dict[str, Tool]:
    """catalog entries by id; all_tools wins over tool for the same id"""
    lookup: dict[str, Tool] = {tool.id: tool}
    if all_tools is not None:
        for t in all_tools:
            lookup[t.id] = t
    return lookup


def _other_bins(lay: _Layout, tid: str, meta: dict, t: Tool) -> tuple[Path, Path]:
    """sandbox and real binary of another installed tool, from its manifest entry"""
    sb = _manifest_sandbox(lay.tr, tid, meta)
    return sb, sb / ".local" / "bin" / t.binary


def _others_need_regen(
    prev: dict,
    prev_tools: dict,
    regen_ids: list[str],
    lookup: Mapping[str, Tool],
    lay: _Layout,
    mode: str,
    python: str,
    src_dir: Path,
) -> bool:
    """whether the other tools' wrappers differ from what this mode, python and src_dir render"""
    if prev.get("mode") != mode or prev.get("python") != python or str(prev.get("src_dir", "")) != str(src_dir):
        return True
    for tid in regen_ids:
        if tid not in lookup:
            continue
        t = lookup[tid]
        sb, rb = _other_bins(lay, tid, prev_tools[tid], t)
        wp = lay.tr / "bin" / t.binary
        try:
            exp = launcher.render_wrapper(t, rb, sb, lay.run_dir, mode, python, src_dir)
        except Exception:
            return True
        try:
            cur_text = wp.read_text(encoding="utf-8") if wp.is_file() else None
        except Exception:
            cur_text = None
        if cur_text != exp:
            return True
    return False


def _regen_plan(
    tool: Tool,
    prev: dict,
    prev_tools: dict,
    lookup: Mapping[str, Tool],
    lay: _Layout,
    mode: str,
    python: str,
    src_dir: Path,
) -> tuple[list[str], bool]:
    """ids of the other installed tools, and whether their wrappers are rewritten; refuses a missing catalog entry"""
    regen_ids = [tid for tid in prev_tools if tid != tool.id and isinstance(prev_tools.get(tid), dict)]
    regen_needed = bool(regen_ids) and _others_need_regen(
        prev, prev_tools, regen_ids, lookup, lay, mode, python, src_dir
    )
    changing = bool(prev_tools) and bool(regen_ids) and regen_needed
    if changing:
        missing = [tid for tid in regen_ids if tid not in lookup]
        if missing:
            raise InstallError(f"missing catalog entry: {missing[0]}")
    return regen_ids, changing


def _plan_writes(
    tool: Tool,
    lay: _Layout,
    changing: bool,
    regen_ids: list[str],
    lookup: Mapping[str, Tool],
    prev_tools: dict,
    mode: str,
    python: str,
    src_dir: Path,
) -> tuple[dict[Path, str], str]:
    """render every wrapper (other tools first, this tool last) and env.sh, before anything is written"""
    planned: dict[Path, str] = {}
    try:
        if changing:
            for tid in regen_ids:
                t = lookup[tid]
                sb, rb = _other_bins(lay, tid, prev_tools[tid], t)
                planned[lay.tr / "bin" / t.binary] = launcher.render_wrapper(
                    t, rb, sb, lay.run_dir, mode, python, src_dir
                )
        planned[lay.wrapper] = launcher.render_wrapper(
            tool, lay.real_bin, lay.sandbox, lay.run_dir, mode, python, src_dir
        )
        etext = launcher.render_env(lay.bin_dir)
    except InstallError:
        raise
    except Exception as e:
        raise InstallError(str(e)) from e
    return planned, etext


def _write_all(fd: int, data: bytes) -> None:
    """write all of data to fd and close it; a failed write closes fd and re-raises"""
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


def _run_checked(tool: Tool, sp: str, lay: _Layout, runner: Runner) -> None:
    """run the downloaded installer at sp only if its sha256 matches, then require the binary"""
    # Hash the file the interpreter will read, before it is made executable or run.
    _check_digest(tool, sp)
    os.chmod(sp, 0o700)
    env = _runner_env(lay.sandbox, tool.installer_env)
    rc, out = runner([tool.interpreter, sp, *tool.installer_args], env, 900.0)
    if rc != 0:
        raise InstallError(f"installer failed ({rc}): {out}")
    if not (lay.real_bin.is_file() and os.access(lay.real_bin, os.X_OK)):
        raise InstallError(f"installer did not create {lay.real_bin}")


def _undo_fresh_fetch(prefix: Path, lay: _Layout, had: bool) -> None:
    """after a failed first download or run: remove the tool dir, and the shared dirs if no tool is installed"""
    if had:
        return
    _remove_fresh_dir(lay.tid_dir, lay.tr, had)
    with contextlib.suppress(Exception):
        c2 = read_manifest(prefix)
        if not c2.get("tools"):
            for p in (lay.bin_dir, lay.run_dir, lay.tr):
                with contextlib.suppress(OSError):
                    p.rmdir()


def _fetch_and_run(tool: Tool, prefix: Path, lay: _Layout, fetch: Fetch, runner: Runner, had: bool) -> None:
    """download the installer to a temp file in the tool dir, check it, run it; the temp file never survives"""
    sp = ""
    try:
        data = fetch(tool.installer_url)
        fd, sp = tempfile.mkstemp(dir=str(lay.tid_dir))
        _write_all(fd, data)
        _run_checked(tool, sp, lay, runner)
    except Exception:
        _undo_fresh_fetch(prefix, lay, had)
        raise
    finally:
        with contextlib.suppress(Exception):
            if sp:
                os.unlink(sp)


def _default_profiles(home: Path) -> list[Path]:
    """~/.profile, plus ~/.bashrc when it exists"""
    cands: list[Path] = [home / ".profile"]
    brc = home / ".bashrc"
    if brc.is_file():
        cands.append(brc)
    return cands


def _add_profile_blocks(profiles: Sequence[Path], env_file: Path) -> list[str]:
    """add the env block to each profile; return those that now hold it"""
    got: list[str] = []
    for p in profiles:
        launcher.add_profile_block(p, env_file)
        with contextlib.suppress(Exception):
            if launcher.BEGIN in p.read_text(encoding="utf-8", errors="ignore"):
                got.append(str(p))
    return got


def _merged_profiles(prev: dict, got: list[str]) -> list[str]:
    """profiles already in the manifest, then the new ones, without repeats"""
    old_profiles = prev.get("profiles", []) if isinstance(prev.get("profiles"), list) else []
    merged: list[str] = []
    seen: set[str] = set()
    for x in list(old_profiles) + got:
        if x not in seen:
            seen.add(x)
            merged.append(x)
    return merged


def _next_manifest(
    tool: Tool,
    lay: _Layout,
    prev: dict,
    prev_tools: dict,
    changing: bool,
    regen_ids: list[str],
    got: list[str],
    mode: str,
    python: str,
    src_dir: Path,
) -> dict:
    """the manifest after this install"""
    tools_dict = dict(prev_tools)
    tools_dict[tool.id] = {"binary": tool.binary, "sandbox": str(lay.sandbox), "signin": tool.signin, "mode": mode}
    if changing:
        for tid in regen_ids:
            existing = tools_dict.get(tid)
            if isinstance(existing, dict):
                updated = dict(existing)
                updated["mode"] = mode
                tools_dict[tid] = updated
    merged = _merged_profiles(prev, got)
    return {
        "version": 1,
        "mode": mode,
        "python": python,
        "src_dir": str(src_dir),
        "profiles": merged,
        "tools": tools_dict,
    }


def _read_backups(
    mp: Path, planned: Mapping[Path, str], lay: _Layout, had: bool
) -> tuple[bytes | None, dict[Path, bytes | None]]:
    """current manifest, wrapper and env.sh bytes (None when absent); a read error aborts before any write"""
    try:
        old_bytes = mp.read_bytes() if mp.is_file() else None
    except OSError as e:
        _remove_fresh_dir(lay.tid_dir, lay.tr, had)
        raise InstallError(str(e)) from e
    backups: dict[Path, bytes | None] = {}
    for p in list(planned) + [lay.env_file]:
        try:
            backups[p] = p.read_bytes() if p.is_file() else None
        except OSError as e:
            _remove_fresh_dir(lay.tid_dir, lay.tr, had)
            raise InstallError(str(e)) from e
    return old_bytes, backups


def _rollback(
    backups: Mapping[Path, bytes | None], env_file: Path, mp: Path, old_bytes: bytes | None, manifest_replaced: bool
) -> list[str]:
    """restore backed-up files in write order; return what could not be restored"""
    rollback_errors: list[str] = []
    for p, b in backups.items():
        try:
            if b is None:
                if p.is_file() or p.is_symlink():
                    p.unlink()
            else:
                file_mode = 0o644 if p == env_file else 0o755
                _atomic_write_bytes(p, b, file_mode)
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
    return rollback_errors


def _commit(prefix: Path, lay: _Layout, planned: Mapping[Path, str], etext: str, new_manifest: dict, had: bool) -> None:
    """write wrappers, env.sh, then the manifest; on failure restore them all and report"""
    mp = manifest_path(prefix)
    old_bytes, backups = _read_backups(mp, planned, lay, had)
    manifest_replaced = False
    try:
        for p, text in planned.items():
            _atomic_write_text(p, text, 0o755)
        _atomic_write_text(lay.env_file, etext, 0o644)
        _atomic_manifest(mp, new_manifest)
        manifest_replaced = True
    except Exception as e:
        rollback_errors = _rollback(backups, lay.env_file, mp, old_bytes, manifest_replaced)
        _remove_fresh_dir(lay.tid_dir, lay.tr, had)
        msg = str(e)
        if rollback_errors:
            msg += f" (rollback failed: {'; '.join(rollback_errors)})"
        raise InstallError(msg) from e


def install(
    tool: Tool,
    *,
    prefix: Path,
    mode: str,
    machine: str,
    home: Path,
    python: str,
    src_dir: Path,
    fetch: Fetch = default_fetch,
    runner: Runner = default_runner,
    free_mb_fn: Callable[[Path], int] = place.free_mb,
    profiles: Sequence[Path] | None = None,
    dry_run: bool = False,
    force: bool = False,
    all_tools: Sequence[Tool] | None = None,
) -> list[str]:
    """install tool and update manifest"""
    _refuse_early(tool, prefix, mode, machine, free_mb_fn)
    lay = _layout(prefix, tool)
    if lay.tid_dir.is_symlink() or lay.sandbox.is_symlink():
        raise InstallError(f"refusing symlink: {lay.tid_dir if lay.tid_dir.is_symlink() else lay.sandbox}")
    prev = read_manifest(prefix)
    prev_tools = dict(prev.get("tools", {})) if isinstance(prev.get("tools"), dict) else {}
    had = tool.id in prev_tools
    lookup = _lookup(tool, all_tools)
    regen_ids, changing = _regen_plan(tool, prev, prev_tools, lookup, lay, mode, python, src_dir)
    planned, etext = _plan_writes(tool, lay, changing, regen_ids, lookup, prev_tools, mode, python, src_dir)
    if dry_run:
        # Kept short: setup shows at most 300 characters of this joined list, and the sign-in line comes last.
        return [
            f"would install {tool.id} to {prefix}",
            f"would fetch {tool.installer_url} and run it only if its sha256 matches",
            f"Sign in yourself: {tool.signin}",
        ]
    _ensure_dirs(lay.sandbox, lay.tid_dir, lay.bin_dir, lay.run_dir)
    need_fetch = force or not (lay.real_bin.is_file() and os.access(lay.real_bin, os.X_OK))
    if need_fetch:
        _fetch_and_run(tool, prefix, lay, fetch, runner, had)
    if profiles is None:
        profiles = _default_profiles(home)
    got = _add_profile_blocks(profiles, lay.env_file)
    new_manifest = _next_manifest(tool, lay, prev, prev_tools, changing, regen_ids, got, mode, python, src_dir)
    _commit(prefix, lay, planned, etext, new_manifest, had)
    return [f"installed {tool.id} to {prefix}", f"wrapper {lay.wrapper}", f"Sign in yourself: {tool.signin}"]


def _render_mode_wrappers(
    entries: Sequence[tuple[str, object]], lookup: Mapping[str, Tool], tr: Path, mode: str, py: str, sd: Path
) -> dict[Path, str]:
    """render the wrapper of every manifest entry with a catalog tool, before anything is written"""
    run_dir = tr / "run"
    planned: dict[Path, str] = {}
    try:
        for tid, meta in entries:
            if tid not in lookup:
                continue
            t = lookup[tid]
            if not isinstance(meta, dict):
                raise InstallError(f"bad manifest entry: {tid}")
            sandbox = _manifest_sandbox(tr, tid, meta)
            real_bin = sandbox / ".local" / "bin" / t.binary
            planned[tr / "bin" / t.binary] = launcher.render_wrapper(t, real_bin, sandbox, run_dir, mode, py, sd)
    except InstallError:
        raise
    except Exception as e:
        raise InstallError(str(e)) from e
    return planned


def _read_mode_backups(mp: Path, planned: Mapping[Path, str]) -> tuple[bytes, dict[Path, bytes | None]]:
    """current manifest and wrapper bytes (None when a wrapper is absent); a read error aborts before any write"""
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
    return old_bytes, backups


def _apply_mode(
    cur: dict, entries: Sequence[tuple[str, object]], lookup: Mapping[str, Tool], mode: str, prev_mode: object
) -> None:
    """set mode on the manifest and on entries with a catalog tool; others without a mode inherit prev_mode"""
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


def _rollback_mode(
    backups: Mapping[Path, bytes | None], mp: Path, old_bytes: bytes, manifest_replaced: bool
) -> list[str]:
    """restore backed-up wrappers in write order, then the manifest if it was replaced; return what failed"""
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
    return rollback_errors


def _commit_mode(
    prefix: Path,
    cur: dict,
    entries: Sequence[tuple[str, object]],
    lookup: Mapping[str, Tool],
    planned: Mapping[Path, str],
    mode: str,
) -> None:
    """write wrappers, then the updated manifest; on failure restore them and report"""
    mp = manifest_path(prefix)
    old_bytes, backups = _read_mode_backups(mp, planned)
    prev_mode = cur.get("mode")
    manifest_replaced = False
    try:
        for p, text in planned.items():
            _atomic_write_text(p, text, 0o755)
        _apply_mode(cur, entries, lookup, mode, prev_mode)
        _atomic_manifest(mp, cur)
        manifest_replaced = True
    except Exception as e:
        rollback_errors = _rollback_mode(backups, mp, old_bytes, manifest_replaced)
        msg = str(e)
        if rollback_errors:
            msg += f" (rollback failed: {'; '.join(rollback_errors)})"
        raise InstallError(msg) from e


def set_mode(prefix: Path, mode: str, tools: Sequence[Tool]) -> list[str]:
    """update wrappers for new mode"""
    if mode not in place.MODES:
        raise InstallError(f"unknown mode: {mode}")
    cur = read_manifest(prefix)
    if not cur or not cur.get("tools"):
        raise InstallError("nothing installed")
    tr = tools_root(prefix)
    lookup = {t.id: t for t in tools}
    py = cur.get("python", "python3")
    sd = Path(cur.get("src_dir", "."))
    entries = list(cur["tools"].items())
    missing = [tid for tid, _ in entries if tid not in lookup]
    planned = _render_mode_wrappers(entries, lookup, tr, mode, py, sd)
    _commit_mode(prefix, cur, entries, lookup, planned, mode)
    out = [f"mode set to {mode}"]
    for tid in missing:
        out.append(
            f"missing catalog entry: {tid} (wrapper left unchanged; restore this tool's original catalog entry, including installer_url, then rerun the mode command)"
        )
    return out


def _refuse_uninstall(tr: Path, prefix: Path) -> None:
    """refuse a symlinked tools root, then one outside prefix"""
    if tr.is_symlink():
        raise InstallError(f"refusing to remove symlink {tr}")
    if not _inside(tr, prefix):
        raise InstallError(f"refusing to remove {tr} not inside {prefix}")


def _remove_profiles(profs: Sequence[str]) -> list[str]:
    """remove the env block from each profile; failures are reported, not raised"""
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
    return out


def _remove_tools_root(tr: Path, prefix: Path) -> list[str]:
    """remove the tools root if it is still a real directory inside prefix; failures are reported"""
    try:
        if tr.is_dir() and not tr.is_symlink() and _inside(tr, prefix):
            shutil.rmtree(tr)
            return [f"removed {tr}"]
        if tr.exists():
            return [f"could not remove {tr}: not a directory"]
    except Exception as e:
        return [f"could not remove {tr}: {e}"]
    return []


def _remove_empty_prefix(prefix: Path) -> list[str]:
    """remove prefix if it is an empty real directory; failures are reported"""
    try:
        if prefix.is_dir() and not prefix.is_symlink():
            if not any(prefix.iterdir()):
                prefix.rmdir()
                return [f"removed {prefix}"]
    except Exception as e:
        return [f"could not remove {prefix}: {e}"]
    return []


def uninstall(prefix: Path, *, dry_run: bool = False) -> list[str]:
    """remove installed tools"""
    mp = manifest_path(prefix)
    if not mp.exists():
        return ["nothing to remove"]
    cur = read_manifest(prefix)
    if not cur:
        return ["nothing to remove"]
    tr = tools_root(prefix)
    _refuse_uninstall(tr, prefix)
    profs = cur.get("profiles", []) if isinstance(cur.get("profiles"), list) else []
    if dry_run:
        return [f"would remove profile block from {s}" for s in profs] + [f"would remove {tr}"]
    out = _remove_profiles(profs)
    out += _remove_tools_root(tr, prefix)
    out += _remove_empty_prefix(prefix)
    return out
