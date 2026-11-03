# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
from collections.abc import Callable, Mapping, Sequence
from opos.aitools import catalog, installer, place

_FALLBACK_PREFIXES = (Path("/userdata") / "opos",)

def table_path(env: Mapping[str, str]) -> Path:
    """Return the catalog table path."""
    p = env.get("OPOS_AI_TOOLS_TABLE")
    if p:
        return Path(p)
    return catalog.default_path(Path(__file__).resolve().parents[3])

def tools_supported(platform: str) -> bool:
    """Check platform support."""
    return platform in ("linux", "darwin")

def public_state(*, env: Mapping[str, str], home: Path, machine: str, platform: str, free_mb_fn: Callable[[Path], int] | None = None) -> dict:
    """Return wizard state."""
    supported = tools_supported(platform)
    try:
        prefix = place.choose_prefix(env, home)
    except Exception:
        prefix = home / ".local" / "share" / "opos"
    _free = free_mb_fn if free_mb_fn is not None else place.free_mb
    try:
        free = _free(prefix)
    except Exception:
        free = None
    try:
        arch = place.normalize_arch(machine)
    except Exception:
        arch = None
    try:
        vw = place.volatile_warning(prefix)
    except Exception:
        vw = None
    if not supported:
        return {"supported": False, "prefix": str(prefix), "free_mb": free, "arch": arch, "modes": list(place.MODES), "default_mode": "lite", "volatile_warning": vw, "error": None, "tools": []}
    try:
        tbl = table_path(env)
        loaded = catalog.load(tbl)
    except Exception as e:
        return {"supported": True, "prefix": str(prefix), "free_mb": free, "arch": arch, "modes": list(place.MODES), "default_mode": "lite", "volatile_warning": vw, "error": str(e), "tools": []}
    out: list[dict] = []
    for t in loaded:
        try:
            probs = place.preflight(t, prefix, machine, _free)
        except Exception as e:
            probs = [str(e)]
        bad_digest = installer.digest_problem(t)
        if bad_digest:
            probs = [*probs, bad_digest]
        ok = not probs
        prob = None if ok else "; ".join(probs)
        out.append({"id": t.id, "name": t.name, "min_free_mb": t.min_free_mb, "archs": list(t.archs), "signin": t.signin, "docs_url": t.docs_url, "ok": ok, "problem": prob})
    return {"supported": True, "prefix": str(prefix), "free_mb": free, "arch": arch, "modes": list(place.MODES), "default_mode": "lite", "volatile_warning": vw, "error": None, "tools": out}

def install_selected(tool_ids: Sequence[str], mode: str, *, env: Mapping[str, str], home: Path, machine: str, platform: str, python: str, src_dir: Path, dry_run: bool = False, fetch: Callable[[str], bytes] | None = None, runner: Callable | None = None, free_mb_fn: Callable[[Path], int] | None = None) -> list[str]:
    """Install selected tools."""
    if mode not in place.MODES:
        raise installer.InstallError(f"unknown mode: {mode}")
    if not tool_ids:
        raise installer.InstallError("no tools selected")
    if len(set(tool_ids)) != len(tool_ids):
        raise installer.InstallError("duplicate tool ids")
    if not tools_supported(platform):
        raise installer.InstallError(f"unsupported platform: {platform}")
    tbl = table_path(env)
    try:
        loaded = catalog.load(tbl)
    except Exception as e:
        raise installer.InstallError(str(e)) from e
    prefix = place.choose_prefix(env, home)
    out: list[str] = []
    for tid in tool_ids:
        try:
            tool = catalog.get(loaded, tid)
        except Exception as e:
            raise installer.InstallError(f"{tid}: {e}") from e
        extra: dict = {}
        if fetch is not None:
            extra["fetch"] = fetch
        if runner is not None:
            extra["runner"] = runner
        if free_mb_fn is not None:
            extra["free_mb_fn"] = free_mb_fn
        try:
            lines = installer.install(tool, prefix=prefix, mode=mode, machine=machine, home=home, python=python, src_dir=src_dir, dry_run=dry_run, **extra)
        except installer.InstallError as e:
            raise installer.InstallError(f"{tid}: {e}") from e
        out.extend(lines)
    return out

def uninstall_all(*, env: Mapping[str, str], home: Path, dry_run: bool = False) -> list[str]:
    """Remove all tools."""
    try:
        primary = place.choose_prefix(env, home)
    except Exception as e:
        return [f"could not remove {e}"]
    if "OPOS_TOOLS_PREFIX" in env:
        cands: list[Path] = [primary]
    else:
        cands = [primary, home / ".local" / "share" / "opos", *_FALLBACK_PREFIXES]
    seen: set[Path] = set()
    uniq: list[Path] = []
    for p in cands:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    out: list[str] = []
    for prefix in uniq:
        try:
            lines = installer.uninstall(prefix, dry_run=dry_run)
        except (installer.InstallError, OSError) as e:
            lines = [f"could not remove {prefix}: {e}"]
        except Exception as e:
            lines = [f"could not remove {prefix}: {e}"]
        if lines == ["nothing to remove"]:
            continue
        out.extend(lines)
    if not out:
        return ["nothing to remove"]
    return out
