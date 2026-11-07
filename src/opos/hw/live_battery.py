# SPDX-License-Identifier: GPL-3.0-or-later
"""The phone's real battery, behind the same attributes the web Phone reads from DrainSim.

Sources, first that answers wins: Termux:API's termux-battery-status (stock Android under
Termux), then the Linux sysfs reader in opos.hw.battery (postmarketOS, Droidian). Health
comes from sysfs charge_full/charge_full_design when readable, else OPOS_BATTERY_HEALTH,
else 1.0 with health_source "assumed", so the screen never claims a measured health it
did not read.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

from opos.hw.battery import read_battery

REFRESH_S = 15.0
_TERMUX_TIMEOUT_S = 10.0


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _termux_status(run=subprocess.run, which=shutil.which):
    exe = which("termux-battery-status")
    if not exe:
        return None
    try:
        r = run([exe], capture_output=True, text=True, timeout=_TERMUX_TIMEOUT_S, check=False)
        data = json.loads(r.stdout)
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("percentage"), (int, float)):
        return None
    status = str(data.get("status", "")).upper()
    plugged = str(data.get("plugged", "")).upper()
    temp = data.get("temperature")
    return {
        "level": float(_clamp(data["percentage"], 0, 100)),
        "temp_c": float(temp) if isinstance(temp, (int, float)) else None,
        "charging": status in ("CHARGING", "FULL") or (plugged not in ("", "UNPLUGGED") and status != "DISCHARGING"),
        "source": "termux-api",
    }


def _sysfs_status(root="/"):
    b = read_battery(root)
    if not b["present"] or b["capacity_pct"] is None:
        return None
    return {
        "level": float(b["capacity_pct"]),
        "temp_c": None,
        "charging": (b["status"] or "").lower() in ("charging", "full"),
        "source": "sysfs",
    }


def _health(env, root="/"):
    b = read_battery(root)
    if b["health"] is not None and b["health"] > 0:
        return float(b["health"]), "sysfs"
    raw = env.get("OPOS_BATTERY_HEALTH")
    if raw:
        try:
            return float(_clamp(float(raw), 0.1, 1.0)), "OPOS_BATTERY_HEALTH"
        except ValueError:
            pass
    return 1.0, "assumed"


def read_live(env=None, root="/", run=subprocess.run, which=shutil.which):
    """One reading as {level, temp_c, charging, source, health, health_source}, or None."""
    env = os.environ if env is None else env
    r = _termux_status(run, which) or _sysfs_status(root)
    if r is None:
        return None
    r["health"], r["health_source"] = _health(env, root)
    return r


class LiveBattery:
    """Stands where DrainSim stands; advance() and charge() re-read instead of simulating."""

    def __init__(self, reader=read_live, clock=time.monotonic, refresh_s=REFRESH_S):
        self._reader = reader
        self._clock = clock
        self.refresh_s = refresh_s
        self.level = 0.0
        self.health = 1.0
        self.health_source = "assumed"
        self.temp_c = 30.0
        self.ambient_c = 30.0
        self.charging = False
        self.source = None
        self.speed = 1.0
        self.t_s = 0.0
        self.dead = False
        self.shutdown_level = None
        self._last = None
        self.refresh(force=True)

    @property
    def ok(self):
        return self.source is not None

    def refresh(self, force=False):
        now = self._clock()
        if not force and self._last is not None and now - self._last < self.refresh_s:
            return
        if self._last is not None:
            self.t_s += max(0.0, now - self._last)
        self._last = now
        r = self._reader()
        if r is None:
            self.source = None
            return
        self.level = r["level"]
        self.health = r["health"]
        self.health_source = r["health_source"]
        if r["temp_c"] is not None:
            self.temp_c = r["temp_c"]
        self.charging = r["charging"]
        self.source = r["source"]

    def advance(self, real_s, mode_name=None, task_costs=(), burst_a=0.0, per_hour=None):
        if real_s < 0:
            raise ValueError(real_s)
        self.refresh()
        return self.level

    def charge(self, real_s, rate_per_hour=None):
        if real_s < 0:
            raise ValueError(real_s)
        self.refresh()
        return self.level
