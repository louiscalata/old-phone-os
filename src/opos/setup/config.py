# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
import contextlib
import json
import math
import os
import re
import secrets
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

SCHEMA_VERSION = 1
SERVICES = ("calls", "sms", "alarms", "apps")
BATTERY_MODES = ("auto", "worn", "normal")
AI_TOOLS_MODES = ("lite", "full")
FORM_FACTORS = ("slab", "foldable-book", "dual-screen", "slide-keyboard")


class ConfigError(ValueError):
    """Raised for invalid or unreadable configuration."""


def config_dir(env: Mapping[str, str] | None = None, platform: str | None = None) -> Path:
    """Return the OS-specific config directory."""
    if env is None:
        env = os.environ
    if platform is None:
        platform = sys.platform
    home_val = env.get("HOME") if isinstance(env, Mapping) else None
    if home_val:
        home = Path(home_val).expanduser()
        if not home.is_absolute():
            try:
                home = (Path.cwd() / home).resolve()
            except Exception:
                home = Path.home()
    else:
        home = Path.home()
    opos = env.get("OPOS_CONFIG_DIR") if isinstance(env, Mapping) else None
    if opos:
        p = Path(opos).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        return p
    if platform == "darwin":
        return home / "Library" / "Application Support" / "opos"
    if platform == "win32":
        appdata = env.get("APPDATA") if isinstance(env, Mapping) else None
        if appdata:
            p = Path(appdata).expanduser()
            if not p.is_absolute():
                p = (Path.cwd() / p).resolve()
            return p / "opos"
        return home / "AppData" / "Roaming" / "opos"
    xdg = env.get("XDG_CONFIG_HOME") if isinstance(env, Mapping) else None
    if xdg:
        p = Path(xdg).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        return p / "opos"
    return home / ".config" / "opos"


def default_config() -> dict:
    """Return the default configuration."""
    return {
        "version": SCHEMA_VERSION,
        "device": {"id": "unknown", "name": "Unknown device", "source": "stub", "battery_health": None},
        "services": list(SERVICES),
        "goal": {"enabled": True, "time": "23:00"},
        "battery_mode": "auto",
        "security": {"ssh_public_key": None},
        "applied": False,
        "ai_tools": {"tools": [], "mode": "lite"},
    }


def form_factor(cfg: dict) -> str:
    """Return device form factor."""
    return (cfg.get("device") or {}).get("form_factor", "slab")


def validate(cfg: object) -> list[str]:
    """Validate config and return error messages."""
    errors: list[str] = []
    if not isinstance(cfg, dict):
        return ["config must be dict"]
    expected = set(default_config().keys())
    actual = set(cfg.keys())
    optional = {"ai_tools"}
    for k in (expected - optional) - actual:
        errors.append(f"missing key: {k}")
    for k in actual - expected:
        errors.append(f"unknown key: {k}")
    if "version" in cfg:
        v = cfg["version"]
        if type(v) is bool or not isinstance(v, int) or v != SCHEMA_VERSION:
            errors.append(f"version must be {SCHEMA_VERSION}")
    if "device" in cfg:
        dev = cfg["device"]
        if not isinstance(dev, dict):
            errors.append("device must be dict")
        else:
            exp_dev = {"id", "name", "source", "battery_health"}
            dev_actual = set(dev.keys())
            for k in exp_dev - dev_actual:
                errors.append(f"device: missing key: {k}")
            for k in dev_actual - exp_dev - {"form_factor"}:
                errors.append(f"device: unknown key: {k}")
            if "id" in dev:
                idv = dev["id"]
                if not isinstance(idv, str):
                    errors.append("device.id must be str")
                elif not (1 <= len(idv) <= 64):
                    errors.append("device.id must be 1-64 chars")
                elif not re.fullmatch(r"[a-z0-9._-]+", idv):
                    errors.append("device.id must match [a-z0-9._-]")
            if "name" in dev:
                namev = dev["name"]
                if not isinstance(namev, str):
                    errors.append("device.name must be str")
                elif not (1 <= len(namev) <= 80):
                    errors.append("device.name must be 1-80 chars")
                elif any(ord(c) < 32 or ord(c) == 127 for c in namev):
                    errors.append("device.name must not contain control characters")
            if "source" in dev:
                src = dev["source"]
                if src not in ("hw-adapt", "stub", "manual"):
                    errors.append("device.source must be one of hw-adapt, stub, manual")
            if "battery_health" in dev:
                bh = dev["battery_health"]
                if bh is None:
                    pass
                elif isinstance(bh, bool):
                    errors.append("device.battery_health must not be bool")
                elif isinstance(bh, int):
                    if not (0 <= bh <= 1):
                        errors.append("device.battery_health must be in 0.0..1.0")
                elif isinstance(bh, float):
                    if math.isnan(bh) or not (0.0 <= bh <= 1.0):
                        errors.append("device.battery_health must be in 0.0..1.0")
                else:
                    errors.append("device.battery_health must be None or number in 0.0..1.0")
            if "form_factor" in dev:
                ff = dev["form_factor"]
                if not isinstance(ff, str):
                    errors.append("device.form_factor must be str")
                elif ff not in FORM_FACTORS:
                    errors.append("device.form_factor must be one of slab, foldable-book, dual-screen, slide-keyboard")
    if "services" in cfg:
        svcs = cfg["services"]
        if not isinstance(svcs, list):
            errors.append("services must be list")
        else:
            if len(svcs) == 0:
                errors.append("services must be non-empty")
            try:
                seen: set[object] = set()
                dup = False
                for item in svcs:
                    if item in seen:
                        dup = True
                        break
                    seen.add(item)
                if dup:
                    errors.append("services must be unique")
            except TypeError:
                errors.append("services must be unique")
            for item in svcs:
                if not isinstance(item, str):
                    errors.append(f"services: invalid entry {item!r} must be str")
                elif item not in SERVICES:
                    errors.append(f"services: invalid entry {item!r}")
            if "calls" not in svcs:
                errors.append("services must include calls")
    if "goal" in cfg:
        goal = cfg["goal"]
        if not isinstance(goal, dict):
            errors.append("goal must be dict")
        else:
            exp_goal = {"enabled", "time"}
            goal_actual = set(goal.keys())
            for k in exp_goal - goal_actual:
                errors.append(f"goal: missing key: {k}")
            for k in goal_actual - exp_goal:
                errors.append(f"goal: unknown key: {k}")
            if "enabled" in goal:
                en = goal["enabled"]
                if type(en) is not bool:
                    errors.append("goal.enabled must be bool")
            if "time" in goal:
                t = goal["time"]
                if not isinstance(t, str):
                    errors.append("goal.time must be str")
                elif not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
                    errors.append("goal.time must be HH:MM 24-hour")
    if "battery_mode" in cfg:
        bm = cfg["battery_mode"]
        if bm not in BATTERY_MODES:
            errors.append(f"battery_mode must be one of {', '.join(BATTERY_MODES)}")
    if "security" in cfg:
        sec = cfg["security"]
        if not isinstance(sec, dict):
            errors.append("security must be dict")
        else:
            exp_sec = {"ssh_public_key"}
            sec_actual = set(sec.keys())
            for k in exp_sec - sec_actual:
                errors.append(f"security: missing key: {k}")
            for k in sec_actual - exp_sec:
                errors.append(f"security: unknown key: {k}")
            if "ssh_public_key" in sec:
                spk = sec["ssh_public_key"]
                if spk is None:
                    pass
                elif not isinstance(spk, str):
                    errors.append("security.ssh_public_key must be str or None")
                else:
                    if len(spk) > 2000:
                        errors.append("security.ssh_public_key must be at most 2000 chars")
                    if "\n" in spk or "\r" in spk:
                        errors.append("security.ssh_public_key must be single-line")
                    elif any(ord(c) < 32 or ord(c) == 127 for c in spk):
                        errors.append("security.ssh_public_key must not contain control characters")
                    else:
                        prefixes = ("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-nistp256 ", "sk-ssh-ed25519@openssh.com ")
                        if not any(spk.startswith(p) for p in prefixes):
                            errors.append("security.ssh_public_key must start with allowed prefix")
                        else:
                            for prefix in prefixes:
                                if spk.startswith(prefix):
                                    rest = spk[len(prefix):]
                                    break
                            if not rest.strip():
                                errors.append("security.ssh_public_key must contain base64 field")
                            else:
                                parts = rest.strip().split()
                                b64 = parts[0] if parts else ""
                                if not b64:
                                    errors.append("security.ssh_public_key must contain base64 field")
                                elif not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", b64):
                                    errors.append("security.ssh_public_key must contain valid base64")
    if "applied" in cfg:
        ap = cfg["applied"]
        if type(ap) is not bool:
            errors.append("applied must be bool")
    if "ai_tools" in cfg:
        at = cfg["ai_tools"]
        if not isinstance(at, dict):
            errors.append("ai_tools must be dict")
        else:
            exp_at = {"tools", "mode"}
            at_actual = set(at.keys())
            for k in exp_at - at_actual:
                errors.append(f"ai_tools: missing key: {k}")
            for k in at_actual - exp_at:
                errors.append(f"ai_tools: unknown key: {k}")
            if "tools" in at:
                tools = at["tools"]
                if not isinstance(tools, list):
                    errors.append("ai_tools.tools must be list")
                else:
                    if len(tools) > 8:
                        errors.append("ai_tools.tools must contain at most 8 items")
                    try:
                        seen: set[object] = set()
                        dup = False
                        for item in tools:
                            if item in seen:
                                dup = True
                                break
                            seen.add(item)
                        if dup:
                            errors.append("ai_tools.tools must be unique")
                    except TypeError:
                        errors.append("ai_tools.tools must be unique")
                    for item in tools:
                        if not isinstance(item, str):
                            errors.append(f"ai_tools.tools: invalid entry {item!r} must be str")
                        elif not re.fullmatch(r"[a-z0-9-]{1,32}", item):
                            errors.append(f"ai_tools.tools: invalid entry {item!r} must match [a-z0-9-]")
            if "mode" in at:
                mode = at["mode"]
                if mode not in AI_TOOLS_MODES:
                    errors.append("ai_tools.mode must be one of lite, full")

    def _check_token(obj: object, prefix: str = "") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(k, str) and "token" in k.lower():
                    errors.append(f"{prefix}{k} must not contain token")
                _check_token(v, f"{prefix}{k}.")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _check_token(item, f"{prefix}{i}.")

    _check_token(cfg)
    return errors


def config_path(env: Mapping[str, str] | None = None, platform: str | None = None) -> Path:
    """Return the config file path."""
    return config_dir(env, platform) / "config.json"


def save(cfg: dict, path: Path | None = None) -> Path:
    """Save config atomically."""
    errs = validate(cfg)
    if errs:
        raise ConfigError("; ".join(errs))
    if path is None:
        path = config_path()
    else:
        path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(Exception):
        path.parent.chmod(0o700)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, sort_keys=True)
            f.write("\n")
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
    except Exception:
        with contextlib.suppress(Exception):
            os.unlink(tmp_name)
        raise
    return path


def load(path: Path | None = None) -> dict | None:
    """Load config from file."""
    if path is None:
        path = config_path()
    else:
        path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        raise ConfigError(str(e)) from e
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ConfigError(str(e)) from e
    errs = validate(data)
    if errs:
        raise ConfigError("; ".join(errs))
    return data


def token_path(env: Mapping[str, str] | None = None, platform: str | None = None) -> Path:
    """Return the session token path."""
    return config_dir(env, platform) / "session.token"


def ensure_token(path: Path | None = None) -> str:
    """Return session token, creating it if needed."""
    if path is None:
        path = token_path()
    else:
        path = Path(path)
    with contextlib.suppress(FileNotFoundError, OSError):
        existing = path.read_text(encoding="utf-8").strip()
        if existing and len(existing) >= 32:
            return existing
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(Exception):
        path.parent.chmod(0o700)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(token)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
    except Exception:
        with contextlib.suppress(Exception):
            os.unlink(tmp_name)
        raise
    return token
