# SPDX-License-Identifier: GPL-3.0-or-later
"""Device detection for first-run wizard."""
from __future__ import annotations
import contextlib
import re
from pathlib import Path

STUB_ID: str = "generic-phone"

def slugify(name: str) -> str:
    """Slugify name."""
    s = re.sub(r"[^a-z0-9._-]+", "-", name.lower())
    s = s.strip("-")[:64]
    return s if s else STUB_ID

def stub_device() -> dict:
    """Return stub device."""
    return {"id": STUB_ID, "name": "Generic phone (stub)", "source": "stub", "battery_health": None, "radios": [], "modem": False, "calibrated": False}

def profile_to_device(profile: object) -> dict:
    """Convert profile to device dict."""
    try:
        if profile is None:
            return stub_device()
        raw_name = getattr(profile, "name", None)
        if not isinstance(raw_name, str):
            return stub_device()
        clean_name = re.sub(r"[\x00-\x1f\x7f]", "", raw_name)[:80]
        if not clean_name:
            return stub_device()
        radios_raw = getattr(profile, "radios", None)
        if radios_raw is None or isinstance(radios_raw, (str, bytes)):
            return stub_device()
        try:
            radios = list(radios_raw)
        except Exception:
            return stub_device()
        if not all(isinstance(r, str) for r in radios):
            return stub_device()
        battery = getattr(profile, "battery", None)
        if battery is None:
            return stub_device()
        health_raw = getattr(battery, "health", None)
        if isinstance(health_raw, bool):
            battery_health = None
        elif isinstance(health_raw, (int, float)):
            hv = float(health_raw)
            if hv != hv:
                battery_health = None
            elif 0.0 <= hv <= 1.0:
                battery_health = hv
            else:
                battery_health = None
        else:
            battery_health = None
        modem = bool(getattr(profile, "modem", False))
        dev_id = slugify(clean_name)
        return {"id": dev_id, "name": clean_name, "source": "hw-adapt", "battery_health": battery_health, "radios": radios, "modem": modem, "calibrated": False}
    except Exception:
        return stub_device()

def calibration_present(config_dir: Path) -> bool:
    """Check calibration file."""
    try:
        return (config_dir / "drain-calibration.json").is_file()
    except Exception:
        return False

def detect_device(root: str = "/", config_dir: Path | None = None) -> dict:
    """Detect device."""
    try:
        from opos.hw.detect import detect
        device = profile_to_device(detect(root))
    except ImportError:
        device = stub_device()
    except Exception:
        device = stub_device()
    with contextlib.suppress(Exception):
        from opos.hw.battery import read_battery
        info = read_battery(root)
        if isinstance(info, dict):
            h = info.get("health")
            if isinstance(h, (int, float)) and not isinstance(h, bool):
                hv = float(h)
                if hv == hv:
                    if 0.0 <= hv <= 1.0:
                        device["battery_health"] = hv
                    elif hv < 0.0:
                        device["battery_health"] = 0.0
                    elif hv > 1.0:
                        device["battery_health"] = 1.0
    with contextlib.suppress(Exception):
        from opos.hw.battery import read_radios
        rinfo = read_radios(root)
        if isinstance(rinfo, dict):
            radios_present = rinfo.get("radios_present")
            if isinstance(radios_present, list) and radios_present:
                device["radios_present"] = list(radios_present)
                net = rinfo.get("net_ifaces")
                if isinstance(net, list) and net:
                    device["net_ifaces"] = list(net)
                rfkill = rinfo.get("rfkill_types")
                if isinstance(rfkill, list) and rfkill:
                    device["rfkill"] = list(rfkill)
                device["hw_radios"] = list(radios_present)
    if config_dir is not None:
        try:
            device["calibrated"] = calibration_present(config_dir)
        except Exception:
            device["calibrated"] = False
    return device

def list_profiles() -> list[dict]:
    """List built-in profiles."""
    try:
        from opos.hw.store import builtin_names
        names = builtin_names()
        result = [{"id": slugify(n), "name": n} for n in names]
        return sorted(result, key=lambda d: d["name"].casefold())
    except Exception:
        return []

def choose_profile(profile_id: str) -> dict | None:
    """Choose profile by id."""
    try:
        from opos.hw.store import builtin_names, builtin_profile
        for n in builtin_names():
            if slugify(n) == profile_id:
                device = profile_to_device(builtin_profile(n))
                device["source"] = "manual"
                return device
        return None
    except Exception:
        return None

def to_config_device(device: dict) -> dict:
    """Create config device."""
    return {k: device[k] for k in ("id", "name", "source", "battery_health", "form_factor") if k in device}
