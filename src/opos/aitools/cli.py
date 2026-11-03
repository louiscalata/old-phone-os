# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import os
import platform
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from opos.aitools import catalog, installer, place, power, sessions
from opos.aitools import wizard

_SRC_DIR = Path(__file__).resolve().parents[2]

def _supply_dir(env: Mapping[str, str]) -> Path:
    """Resolve supply dir."""
    v = env.get("OPOS_POWER_SUPPLY_DIR")
    if v:
        return Path(v)
    return power.SUPPLY_DIR

@dataclass
class _Ctx:
    """Context for subcommands."""
    env: Mapping[str, str]
    home: Path
    machine: str
    emit: Callable[..., None]
    fetch: Callable[..., object] | None
    runner: Callable[..., object] | None

def _cmd_list(ctx: _Ctx, args: argparse.Namespace) -> int:
    """List tools."""
    try:
        tools = catalog.load(wizard.table_path(ctx.env))
    except Exception as e:
        ctx.emit(f"error: {e}", True)
        return 1
    for t in tools:
        ctx.emit(f"{t.id}: {t.name} - {t.signin}")
    return 0

def _cmd_install(ctx: _Ctx, args: argparse.Namespace) -> int:
    """Install tools."""
    try:
        tools = catalog.load(wizard.table_path(ctx.env))
    except Exception as e:
        ctx.emit(f"error: {e}", True)
        return 1
    prefix = args.prefix if args.prefix is not None else place.choose_prefix(ctx.env, ctx.home)
    prefix = prefix.resolve()
    warn = place.volatile_warning(prefix)
    if warn:
        ctx.emit(warn)
    ctx.emit(f"prefix: {prefix}")
    for tid in args.tool:
        try:
            tool = catalog.get(tools, tid)
        except Exception as e:
            ctx.emit(f"error: {e}", True)
            return 1
        kwargs: dict[str, object] = {}
        if ctx.fetch is not None:
            kwargs["fetch"] = ctx.fetch
        if ctx.runner is not None:
            kwargs["runner"] = ctx.runner
        try:
            report = installer.install(tool, prefix=prefix, mode=args.mode, machine=ctx.machine, home=ctx.home, python=sys.executable, src_dir=_SRC_DIR, free_mb_fn=place.free_mb, dry_run=args.dry_run, force=args.force, **kwargs)  # type: ignore[arg-type]
        except installer.InstallError as e:
            ctx.emit(f"error: {e}", True)
            return 1
        except Exception as e:
            ctx.emit(f"error: {e}", True)
            return 1
        for line in report:
            ctx.emit(line)
    return 0

def _cmd_mode(ctx: _Ctx, args: argparse.Namespace) -> int:
    """Change mode."""
    try:
        tools = catalog.load(wizard.table_path(ctx.env))
    except Exception as e:
        ctx.emit(f"error: {e}", True)
        return 1
    prefix = args.prefix if args.prefix is not None else place.choose_prefix(ctx.env, ctx.home)
    try:
        report = installer.set_mode(prefix, args.mode, tools)
    except installer.InstallError as e:
        ctx.emit(f"error: {e}", True)
        return 1
    for line in report:
        ctx.emit(line)
    return 0

def _cmd_uninstall(ctx: _Ctx, args: argparse.Namespace) -> int:
    """Uninstall tools."""
    prefix = args.prefix if args.prefix is not None else place.choose_prefix(ctx.env, ctx.home)
    try:
        report = installer.uninstall(prefix, dry_run=args.dry_run)
    except installer.InstallError as e:
        ctx.emit(f"error: {e}", True)
        return 1
    for line in report:
        ctx.emit(line)
    if any(l.startswith("could not remove") for l in report):
        return 1
    return 0

def _cmd_status(ctx: _Ctx, args: argparse.Namespace) -> int:
    """Show status."""
    prefix = args.prefix if args.prefix is not None else place.choose_prefix(ctx.env, ctx.home)
    ctx.emit(f"prefix: {prefix}")
    try:
        manifest = installer.read_manifest(prefix)
    except installer.InstallError as e:
        ctx.emit(f"error: {e}", True)
        return 1
    mode_val = manifest.get("mode", "none") if manifest else "none"
    ctx.emit(f"mode: {mode_val}")
    tools_map = manifest.get("tools", {}) if manifest else {}
    for tid, info in tools_map.items():
        ctx.emit(f"{tid} {info.get('binary', '')}")
    try:
        t_root = installer.tools_root(prefix)
        r_dir = t_root / "run"
        sess = sessions.list_sessions(r_dir, t_root)
        ctx.emit(f"sessions: {len(sess)}")
        for s in sess:
            ctx.emit(f"session {s.tool_id} {s.pid}")
    except OSError:
        ctx.emit("sessions: 0")
    return 0

def _cmd_guard(ctx: _Ctx, args: argparse.Namespace) -> int:
    """Run guard."""
    try:
        return sessions.main_guard(args.run_dir, interval_s=args.interval, supply_dir=_supply_dir(ctx.env))
    except Exception as e:
        ctx.emit(f"error: {e}", True)
        return 1

def _cmd_power(ctx: _Ctx, args: argparse.Namespace) -> int:
    """Report power."""
    try:
        if ctx.env.get("OPOS_POWER_SUPPLY_DIR"):
            ext = power.on_external_power(supply_dir=_supply_dir(ctx.env))
        else:
            ext = power.on_external_power()
    except Exception:
        ctx.emit("battery")
        return 1
    if ext:
        ctx.emit("external power")
        return 0
    ctx.emit("battery")
    return 1

def main(argv=None, *, env=None, home=None, machine=None, out=None, fetch=None, runner=None) -> int:
    """CLI entry point."""
    env_map = env if env is not None else os.environ
    home_path = home if home is not None else Path.home()
    machine_str = machine if machine is not None else platform.machine()
    def emit(msg: str, err: bool = False) -> None:
        if out is not None:
            out.write(msg + "\n")
        else:
            print(msg, file=sys.stderr if err else sys.stdout)
    ctx = _Ctx(env=env_map, home=home_path, machine=machine_str, emit=emit, fetch=fetch, runner=runner)
    parser = argparse.ArgumentParser(prog="python -m opos.aitools")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("list")
    p_install = sub.add_parser("install")
    p_install.add_argument("--tool", action="append", required=True, dest="tool")
    p_install.add_argument("--mode", choices=place.MODES, default="lite")
    p_install.add_argument("--prefix", type=Path, default=None)
    p_install.add_argument("--dry-run", action="store_true", default=False)
    p_install.add_argument("--force", action="store_true", default=False)
    p_mode = sub.add_parser("mode")
    p_mode.add_argument("mode", choices=place.MODES)
    p_mode.add_argument("--prefix", type=Path, default=None)
    p_uninstall = sub.add_parser("uninstall")
    p_uninstall.add_argument("--prefix", type=Path, default=None)
    p_uninstall.add_argument("--dry-run", action="store_true", default=False)
    p_status = sub.add_parser("status")
    p_status.add_argument("--prefix", type=Path, default=None)
    p_guard = sub.add_parser("guard")
    p_guard.add_argument("--run-dir", type=Path, required=True)
    p_guard.add_argument("--interval", type=float, default=5.0)
    sub.add_parser("power")
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help(sys.stderr if out is None else out)
        return 2
    if args.cmd == "list":
        return _cmd_list(ctx, args)
    if args.cmd == "install":
        return _cmd_install(ctx, args)
    if args.cmd == "mode":
        return _cmd_mode(ctx, args)
    if args.cmd == "uninstall":
        return _cmd_uninstall(ctx, args)
    if args.cmd == "status":
        return _cmd_status(ctx, args)
    if args.cmd == "guard":
        return _cmd_guard(ctx, args)
    if args.cmd == "power":
        return _cmd_power(ctx, args)
    parser.print_help(sys.stderr if out is None else out)
    return 2
