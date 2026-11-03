# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line interface for Old Phone OS Interface setup."""
from __future__ import annotations
import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Mapping, TextIO
import opos.hw.detect
import opos.setup.apply
import opos.setup.config
import opos.setup.device
import opos.setup.server
from opos.setup.services import ServiceError, checkout_root
RAM_BOOT_WARNING = (
    "warning: Surface Duo detected. If this kernel was RAM-booted from a PC with fastboot boot, do not run reboot or systemctl reboot: the phone would start the stock kernel and be stranded.\n"
    "To restart safely run reboot bootloader, then re-arm the misc brake and RAM-boot once from the PC. See docs/duo-usb-install.md, section \"Rebooting safely / recovering\"."
)


def reboot_warning(root: str = "/") -> str | None:
    """Return RAM-boot reboot warning for Surface Duo, else None."""
    try:
        profile = opos.hw.detect.detect(root)
    except Exception:
        return None
    if profile is None:
        return None
    try:
        name = profile.get("name") if isinstance(profile, dict) else getattr(profile, "name", None)
    except Exception:
        return None
    if name == "Surface Duo":
        return RAM_BOOT_WARNING
    return None
def _build_parser() -> argparse.ArgumentParser:
    """Create argument parser."""
    p = argparse.ArgumentParser(prog="opos")
    p.add_argument("--version", action="version", version="opos 0.1.0")
    subs = p.add_subparsers(dest="cmd")
    r = subs.add_parser("run")
    r.add_argument("--reconfigure", action="store_true")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--no-browser", action="store_true")
    r.add_argument("--port", type=int, default=None)
    r.add_argument("--config-dir", dest="config_dir", default=None)
    r.add_argument("--defaults", action="store_true", help="apply the default settings without the browser wizard (for headless installs)")
    s = subs.add_parser("status")
    s.add_argument("--config-dir", dest="config_dir", default=None)
    u = subs.add_parser("uninstall")
    u.add_argument("--keep-config", action="store_true")
    u.add_argument("--dry-run", action="store_true")
    u.add_argument("--config-dir", dest="config_dir", default=None)
    return p
def _handle_run(args: argparse.Namespace, env: dict[str, str], platform: str, home: Path, out: TextIO, repo_root: Path | None = None) -> int:
    """Handle run subcommand."""
    if repo_root is None:
        repo_root = checkout_root(Path(__file__))
    _warn = reboot_warning("/")
    if _warn:
        print(_warn, file=out)
    eff = dict(env)
    if args.config_dir is not None:
        eff["OPOS_CONFIG_DIR"] = args.config_dir
    if getattr(args, "defaults", False):
        cfg = opos.setup.config.default_config()
        try:
            _opos_dev = opos.setup.device.detect_device("/", config_dir=opos.setup.config.config_dir(eff, platform))
            _opos_cand = opos.setup.device.to_config_device(_opos_dev)
            if isinstance(_opos_cand, dict):
                _is_stub = _opos_cand.get("source") == "stub" or _opos_cand.get("id") == "unknown"
                if not _is_stub:
                    cfg["device"] = _opos_cand
        except Exception as exc:
            print(f"device detection failed: {exc}", file=out)
            return 1
        errors = opos.setup.config.validate(cfg)
        if errors:
            for e in errors:
                print(e, file=out)
            return 1
        result = opos.setup.apply.apply(cfg, repo_root=repo_root, python=sys.executable, home=home, platform=platform, env=eff, dry_run=args.dry_run)
        steps = result.snapshot()
        failed = [s for s in steps if s["status"] == "failed"]
        for s in steps:
            if s["status"] != "failed" and s["detail"]:
                print(f'step {s["label"]}: {s["detail"]}', file=out)
        if failed:
            for s in failed:
                print(f'step {s["label"]}: {s["detail"]}', file=out)
            print("setup failed", file=out)
            return 1
        if args.dry_run:
            print("Dry run finished. Nothing was configured or started.", file=out)
            return 0
        _dev_name = ""
        try:
            _d = cfg.get("device")
            if isinstance(_d, dict):
                _dev_name = _d.get("name", "")
            elif _d is not None:
                _dev_name = str(_d)
        except Exception:
            _dev_name = ""
        if _dev_name:
            print(f"Old Phone OS Interface is set up with the default settings. Device: {_dev_name}", file=out)
        else:
            print("Old Phone OS Interface is set up with the default settings.", file=out)
        print("Open it at http://127.0.0.1:8765/", file=out)
        return 0
    needs = False
    if args.reconfigure:
        needs = True
    else:
        try:
            cfg_path = opos.setup.config.config_path(eff, platform)
            data = opos.setup.config.load(cfg_path)
        except opos.setup.config.ConfigError:
            needs = True
        except Exception:
            needs = True
        else:
            if data is None or data.get("applied") is not True:
                needs = True
    if needs:
        srv_argv: list[str] = []
        if args.port is not None:
            srv_argv.extend(["--port", str(args.port)])
        if args.dry_run:
            srv_argv.append("--dry-run")
        if args.no_browser:
            srv_argv.append("--no-browser")
        if args.config_dir is not None:
            srv_argv.extend(["--config-dir", args.config_dir])
        return opos.setup.server.main(srv_argv)
    if args.dry_run:
        print("Dry run finished. Nothing was configured or started.", file=out)
        return 0
    print("Old Phone OS Interface is already set up.", file=out)
    print("Open it at http://127.0.0.1:8765/", file=out)
    print("Run 'opos run --reconfigure' to change preferences.", file=out)
    return 0
def _handle_status(args: argparse.Namespace, env: dict[str, str], platform: str, out: TextIO) -> int:
    """Handle status subcommand."""
    eff = dict(env)
    if args.config_dir is not None:
        eff["OPOS_CONFIG_DIR"] = args.config_dir
    cfg_path = opos.setup.config.config_path(eff, platform)
    cfg = None
    try:
        cfg = opos.setup.config.load(cfg_path)
    except opos.setup.config.ConfigError:
        cfg = None
    except Exception:
        cfg = None
    configured = cfg is not None
    print(f"configured: {'yes' if configured else 'no'}", file=out)
    print(f"config: {cfg_path}", file=out)
    if configured and isinstance(cfg, dict):
        def fmt(v: object) -> str:
            if isinstance(v, (list, tuple, set)):
                return ", ".join(str(x) for x in v)
            if isinstance(v, dict):
                return ", ".join(str(k) for k in v.keys())
            return str(v)
        dev = None
        if "device_name" in cfg:
            dev = cfg["device_name"]
        elif "device" in cfg:
            d = cfg["device"]
            if isinstance(d, dict):
                dev = d.get("name") or d.get("device_name")
            else:
                dev = d
        elif "name" in cfg:
            dev = cfg["name"]
        stored = cfg.get("device")
        if isinstance(stored, dict) and (stored.get("source") == "stub" or stored.get("id") in ("generic-phone", "unknown")):
            try:
                found = opos.setup.device.detect_device("/")
            except Exception:
                found = None
            if isinstance(found, dict) and found.get("source") != "stub" and isinstance(found.get("name"), str) and found.get("name"):
                dev = f"{found.get('name')} (detected)"
        if dev is not None:
            print(f"device_name: {fmt(dev)}", file=out)
        svc = None
        for k in ("services", "apps", "enabled_services"):
            if k in cfg:
                svc = cfg[k]
                break
        if svc is not None:
            print(f"services: {fmt(svc)}", file=out)
        goal = None
        for k in ("goal", "purpose", "objective"):
            if k in cfg:
                goal = cfg[k]
                break
        if goal is not None:
            if isinstance(goal, dict) and "enabled" in goal and "time" in goal:
                if goal.get("enabled"):
                    print(f"goal: {goal.get('time')}", file=out)
                else:
                    print("goal: off", file=out)
            else:
                print(f"goal: {fmt(goal)}", file=out)
        batt = None
        for k in ("battery_mode", "battery", "power_mode"):
            if k in cfg:
                batt = cfg[k]
                break
        if batt is not None:
            print(f"battery_mode: {fmt(batt)}", file=out)
        ai = cfg.get("ai_tools")
        if isinstance(ai, dict) and isinstance(ai.get("tools"), (list, tuple)) and ai.get("tools"):
            mode = ai.get("mode")
            if not isinstance(mode, str):
                mode = "lite"
            print(f"ai_tools: {', '.join(str(x) for x in ai['tools'])} ({mode})", file=out)
        else:
            print("ai_tools: none", file=out)
    persist = "unverified"
    try:
        p = cfg_path.parent / "persist-marker.json"
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(p, flags)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise ValueError("not regular file")
            if st.st_size > 65536:
                raise ValueError("too large")
            parts: list[bytes] = []
            want = 65537
            while want > 0:
                chunk = os.read(fd, 65536 if want > 65536 else want)
                if not chunk:
                    break
                parts.append(chunk)
                want -= len(chunk)
            raw = b"".join(parts)
        finally:
            os.close(fd)
        if len(raw) > 65536:
            raise ValueError("too large")
        doc = json.loads(raw.decode("utf-8"))
        lv = doc.get("last_verify") if isinstance(doc, dict) else None
        res = lv.get("result") if isinstance(lv, dict) else None
        if res in ("ok", "fail", "unverified"):
            persist = res
    except Exception:
        persist = "unverified"
    print(f"persist: {persist}", file=out)
    token = None
    try:
        token = opos.setup.config.token_path(eff, platform).read_text(encoding="utf-8").strip() or None
    except (OSError, UnicodeError):
        pass
    try:
        running, _ = opos.setup.apply.http_health(8765, timeout_s=1.0, interval_s=0.2, token=token)
    except Exception:
        running = False
    print(f"running: {'yes' if running else 'no'}", file=out)
    return 0
def _handle_uninstall(args: argparse.Namespace, env: dict[str, str], platform: str, home: Path, out: TextIO) -> int:
    """Handle uninstall subcommand."""
    eff = dict(env)
    if args.config_dir is not None:
        eff["OPOS_CONFIG_DIR"] = args.config_dir
    try:
        lines = opos.setup.apply.uninstall(home=home, platform=platform, env=eff, dry_run=args.dry_run, keep_config=args.keep_config)
    except ServiceError as exc:
        msg = str(exc).strip() or "service removal failed: ServiceError"
        for line in getattr(exc, "actions", None) or []:
            if line != msg:
                print(line, file=out)
        print(msg, file=out)
        return 1
    except Exception as exc:  # pragma: no cover - unexpected failure
        print(f"uninstall failed: {type(exc).__name__}", file=out)
        return 1
    for line in lines:
        print(line, file=out)
    return 0
def main(argv: list[str] | None = None, *, env: Mapping[str, str] | None = None, home: Path | None = None, platform: str | None = None, out: TextIO | None = None) -> int:
    """Entry point for setup CLI."""
    env_dict = dict(os.environ) if env is None else dict(env)
    home_val = home if home is not None else Path.home()
    platform_val = platform if platform is not None else sys.platform
    out_val = out if out is not None else sys.stdout
    raw_argv = sys.argv[1:] if argv is None else argv
    subcommands = {"run", "status", "uninstall"}
    if not raw_argv or raw_argv[0] not in subcommands:
        if raw_argv and raw_argv[0] in ("-h", "--help", "--version"):
            effective_argv = raw_argv
        else:
            effective_argv = ["run"] + list(raw_argv)
    else:
        effective_argv = list(raw_argv)
    parser = _build_parser()
    args = parser.parse_args(effective_argv)
    try:
        # Check before detection, config writes, wizard launch or service removal.
        # Cleanup/status remain usable if a retained checkout lost its template.
        repo_root = checkout_root(Path(__file__), require_template=args.cmd == "run")
    except ServiceError as exc:
        print(str(exc), file=out_val)
        return 2
    if args.cmd == "run":
        return _handle_run(args, env_dict, platform_val, home_val, out_val, repo_root)
    if args.cmd == "status":
        return _handle_status(args, env_dict, platform_val, out_val)
    if args.cmd == "uninstall":
        return _handle_uninstall(args, env_dict, platform_val, home_val, out_val)
    return _handle_run(args, env_dict, platform_val, home_val, out_val, repo_root)
