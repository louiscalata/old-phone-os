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


def _report_keys(errors: list[str], prefix: str, missing: set, unknown: set) -> None:
    for k in missing:
        errors.append(f"{prefix}missing key: {k}")
    for k in unknown:
        errors.append(f"{prefix}unknown key: {k}")


def _run_field_checks(obj: dict, checks: tuple, errors: list[str]) -> None:
    for key, check in checks:
        if key in obj:
            check(obj[key], errors)


def _check_unique(items: list, message: str, errors: list[str]) -> None:
    try:
        seen: set[object] = set()
        dup = False
        for item in items:
            if item in seen:
                dup = True
                break
            seen.add(item)
        if dup:
            errors.append(message)
    except TypeError:
        errors.append(message)


def _check_version(v: object, errors: list[str]) -> None:
    if type(v) is bool or not isinstance(v, int) or v != SCHEMA_VERSION:
        errors.append(f"version must be {SCHEMA_VERSION}")


def _check_device_id(idv: object, errors: list[str]) -> None:
    if not isinstance(idv, str):
        errors.append("device.id must be str")
    elif not (1 <= len(idv) <= 64):
        errors.append("device.id must be 1-64 chars")
    elif not re.fullmatch(r"[a-z0-9._-]+", idv):
        errors.append("device.id must match [a-z0-9._-]")


def _check_device_name(namev: object, errors: list[str]) -> None:
    if not isinstance(namev, str):
        errors.append("device.name must be str")
    elif not (1 <= len(namev) <= 80):
        errors.append("device.name must be 1-80 chars")
    elif any(ord(c) < 32 or ord(c) == 127 for c in namev):
        errors.append("device.name must not contain control characters")


def _check_device_source(src: object, errors: list[str]) -> None:
    if src not in ("hw-adapt", "stub", "manual"):
        errors.append("device.source must be one of hw-adapt, stub, manual")


def _check_device_battery_health(bh: object, errors: list[str]) -> None:
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


def _check_device_form_factor(ff: object, errors: list[str]) -> None:
    if not isinstance(ff, str):
        errors.append("device.form_factor must be str")
    elif ff not in FORM_FACTORS:
        errors.append("device.form_factor must be one of slab, foldable-book, dual-screen, slide-keyboard")


# Each table below is ordered: checks run, and report, in this sequence.
_DEVICE_FIELD_CHECKS = (
    ("id", _check_device_id),
    ("name", _check_device_name),
    ("source", _check_device_source),
    ("battery_health", _check_device_battery_health),
    ("form_factor", _check_device_form_factor),
)


def _check_device(dev: object, errors: list[str]) -> None:
    if not isinstance(dev, dict):
        errors.append("device must be dict")
        return
    exp_dev = {"id", "name", "source", "battery_health"}
    dev_actual = set(dev.keys())
    _report_keys(errors, "device: ", exp_dev - dev_actual, dev_actual - exp_dev - {"form_factor"})
    _run_field_checks(dev, _DEVICE_FIELD_CHECKS, errors)


def _check_services(svcs: object, errors: list[str]) -> None:
    if not isinstance(svcs, list):
        errors.append("services must be list")
        return
    if len(svcs) == 0:
        errors.append("services must be non-empty")
    _check_unique(svcs, "services must be unique", errors)
    for item in svcs:
        if not isinstance(item, str):
            errors.append(f"services: invalid entry {item!r} must be str")
        elif item not in SERVICES:
            errors.append(f"services: invalid entry {item!r}")
    if "calls" not in svcs:
        errors.append("services must include calls")


def _check_goal_enabled(en: object, errors: list[str]) -> None:
    if type(en) is not bool:
        errors.append("goal.enabled must be bool")


def _check_goal_time(t: object, errors: list[str]) -> None:
    if not isinstance(t, str):
        errors.append("goal.time must be str")
    elif not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
        errors.append("goal.time must be HH:MM 24-hour")


_GOAL_FIELD_CHECKS = (
    ("enabled", _check_goal_enabled),
    ("time", _check_goal_time),
)


def _check_goal(goal: object, errors: list[str]) -> None:
    if not isinstance(goal, dict):
        errors.append("goal must be dict")
        return
    exp_goal = {"enabled", "time"}
    goal_actual = set(goal.keys())
    _report_keys(errors, "goal: ", exp_goal - goal_actual, goal_actual - exp_goal)
    _run_field_checks(goal, _GOAL_FIELD_CHECKS, errors)


def _check_battery_mode(bm: object, errors: list[str]) -> None:
    if bm not in BATTERY_MODES:
        errors.append(f"battery_mode must be one of {', '.join(BATTERY_MODES)}")


def _check_ssh_key_body(spk: str, errors: list[str]) -> None:
    prefixes = ("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-nistp256 ", "sk-ssh-ed25519@openssh.com ")
    if not any(spk.startswith(p) for p in prefixes):
        errors.append("security.ssh_public_key must start with allowed prefix")
        return
    for prefix in prefixes:
        if spk.startswith(prefix):
            rest = spk[len(prefix) :]
            break
    if not rest.strip():
        errors.append("security.ssh_public_key must contain base64 field")
        return
    parts = rest.strip().split()
    b64 = parts[0] if parts else ""
    if not b64:
        errors.append("security.ssh_public_key must contain base64 field")
    elif not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", b64):
        errors.append("security.ssh_public_key must contain valid base64")


def _check_ssh_public_key(spk: object, errors: list[str]) -> None:
    if spk is None:
        return
    if not isinstance(spk, str):
        errors.append("security.ssh_public_key must be str or None")
        return
    if len(spk) > 2000:
        errors.append("security.ssh_public_key must be at most 2000 chars")
    if "\n" in spk or "\r" in spk:
        errors.append("security.ssh_public_key must be single-line")
    elif any(ord(c) < 32 or ord(c) == 127 for c in spk):
        errors.append("security.ssh_public_key must not contain control characters")
    else:
        _check_ssh_key_body(spk, errors)


_SECURITY_FIELD_CHECKS = (("ssh_public_key", _check_ssh_public_key),)


def _check_security(sec: object, errors: list[str]) -> None:
    if not isinstance(sec, dict):
        errors.append("security must be dict")
        return
    exp_sec = {"ssh_public_key"}
    sec_actual = set(sec.keys())
    _report_keys(errors, "security: ", exp_sec - sec_actual, sec_actual - exp_sec)
    _run_field_checks(sec, _SECURITY_FIELD_CHECKS, errors)


def _check_applied(ap: object, errors: list[str]) -> None:
    if type(ap) is not bool:
        errors.append("applied must be bool")


def _check_ai_tools_tools(tools: object, errors: list[str]) -> None:
    if not isinstance(tools, list):
        errors.append("ai_tools.tools must be list")
        return
    if len(tools) > 8:
        errors.append("ai_tools.tools must contain at most 8 items")
    _check_unique(tools, "ai_tools.tools must be unique", errors)
    for item in tools:
        if not isinstance(item, str):
            errors.append(f"ai_tools.tools: invalid entry {item!r} must be str")
        elif not re.fullmatch(r"[a-z0-9-]{1,32}", item):
            errors.append(f"ai_tools.tools: invalid entry {item!r} must match [a-z0-9-]")


def _check_ai_tools_mode(mode: object, errors: list[str]) -> None:
    if mode not in AI_TOOLS_MODES:
        errors.append("ai_tools.mode must be one of lite, full")


_AI_TOOLS_FIELD_CHECKS = (
    ("tools", _check_ai_tools_tools),
    ("mode", _check_ai_tools_mode),
)


def _check_ai_tools(at: object, errors: list[str]) -> None:
    if not isinstance(at, dict):
        errors.append("ai_tools must be dict")
        return
    exp_at = {"tools", "mode"}
    at_actual = set(at.keys())
    _report_keys(errors, "ai_tools: ", exp_at - at_actual, at_actual - exp_at)
    _run_field_checks(at, _AI_TOOLS_FIELD_CHECKS, errors)


_TOP_LEVEL_CHECKS = (
    ("version", _check_version),
    ("device", _check_device),
    ("services", _check_services),
    ("goal", _check_goal),
    ("battery_mode", _check_battery_mode),
    ("security", _check_security),
    ("applied", _check_applied),
    ("ai_tools", _check_ai_tools),
)


def _check_token(errors: list[str], obj: object, prefix: str = "") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and "token" in k.lower():
                errors.append(f"{prefix}{k} must not contain token")
            _check_token(errors, v, f"{prefix}{k}.")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _check_token(errors, item, f"{prefix}{i}.")


def validate(cfg: object) -> list[str]:
    """Validate config and return error messages."""
    errors: list[str] = []
    if not isinstance(cfg, dict):
        return ["config must be dict"]
    expected = set(default_config().keys())
    actual = set(cfg.keys())
    optional = {"ai_tools"}
    _report_keys(errors, "", (expected - optional) - actual, actual - expected)
    _run_field_checks(cfg, _TOP_LEVEL_CHECKS, errors)
    # Called directly from validate() so the recursion depth of the token scan is unchanged.
    _check_token(errors, cfg)
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
