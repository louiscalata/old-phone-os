# SPDX-License-Identifier: GPL-3.0-or-later
"""The phone's real battery, behind the same attributes the web Phone reads from DrainSim.

Sources, first that answers wins: Termux:API's termux-battery-status (stock Android under
Termux), then the Linux sysfs reader in opos.hw.battery (postmarketOS, Droidian). Health
comes from sysfs charge_full/charge_full_design when readable; else, on Android, from the
fuel gauge's charge counter scaled to 100% against the design capacity (OPOS_BATTERY_DESIGN_MAH
or the DESIGN_MAH model table), at MIN_ESTIMATE_PCT charge or more; else OPOS_BATTERY_HEALTH;
else 1.0 with health_source "assumed", so the screen never claims a measured health it did
not read. Time left follows MeasuredDrain, the discharge rate seen in the readings themselves, once
it has enough of them; until then the caller keeps the model's rate and says so.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import time
from pathlib import Path

from opos.hw.battery import _read_int, _read_text, read_battery

REFRESH_S = 15.0
_TERMUX_TIMEOUT_S = 10.0
# Manufacturer design capacities, keyed by ro.product.model.
DESIGN_MAH = {"Surface Duo": 3577, "Surface Duo 2": 4449}
# Below this charge, percentage rounding makes the counter estimate too coarse to use.
MIN_ESTIMATE_PCT = 30
# Measured drain (MeasuredDrain), in seconds, percent points and percent per hour.
DRAIN_TAU_S = 1800.0  # weight of the past: a segment moves the average by 1 - exp(-length / DRAIN_TAU_S)
DRAIN_WINDOW_S = 600.0  # discharge to observe before the rate is trusted
DRAIN_MIN_DROP = 0.5  # ... and at least this many points of it
DRAIN_MIN_SEG_S = 120.0  # shortest segment; a faster step waits for the next one
DRAIN_SOFT_GAP_S = 120.0  # readings further apart than this cut the open segment (the average stays)
DRAIN_MAX_GAP_S = 900.0  # ... and further apart than this start a new run
DRAIN_JUMP = 5.0  # more points lost between two readings is a gauge jump, not discharge
DRAIN_RISE = 2.0  # a level up to this far above the run's lowest is gauge noise; higher is a jump
DRAIN_FINE_STEP = 0.25  # counter positions close a segment every quarter point
DRAIN_FINE_TOL = 1.25  # counter and shown level may differ by about a point
DRAIN_MAX = 200.0  # a segment faster than this (percent per hour) is thrown away
DRAIN_MIN = 0.25  # the floor under a stalled run's rate, so time left stays a number


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
    counter = data.get("charge_counter")
    return {
        "level": float(_clamp(data["percentage"], 0, 100)),
        "temp_c": float(temp) if isinstance(temp, (int, float)) else None,
        "charging": status in ("CHARGING", "FULL") or (plugged not in ("", "UNPLUGGED") and status != "DISCHARGING"),
        "source": "termux-api",
        "charge_counter_uah": int(counter) if isinstance(counter, (int, float)) and counter > 0 else None,
    }


def _model(run=subprocess.run):
    try:
        return run(["getprop", "ro.product.model"], capture_output=True, text=True, timeout=5, check=False).stdout.strip()
    except Exception:
        return ""


def design_mah(env, run=subprocess.run):
    raw = env.get("OPOS_BATTERY_DESIGN_MAH")
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return DESIGN_MAH.get(_model(run))


def counter_health(level, counter_uah, design):
    """Full-charge capacity over design capacity, from one charge-counter reading, or None."""
    if not counter_uah or not design or level < MIN_ESTIMATE_PCT:
        return None
    return _clamp(counter_uah / (level / 100) / (design * 1000), 0.1, 1.0)


def _sysfs_counter(root="/"):
    """The one battery's remaining-charge counter (charge_counter, charge_now or energy_now), else None.

    Only its ratio to the shown level is used, so its unit does not matter; with several batteries
    the counters could cover a different part than the level does, so none is returned.
    """
    found = []
    try:
        for e in (Path(root) / "sys/class/power_supply").iterdir():
            if _read_text(e / "type") != "Battery" or _read_text(e / "present") == "0":
                continue
            for name in ("charge_counter", "charge_now", "energy_now"):
                v = _read_int(e / name)
                if v and v > 0:
                    found.append(v)
                    break
    except OSError:
        return None
    return found[0] if len(found) == 1 else None


def _sysfs_status(root="/"):
    b = read_battery(root)
    if not b["present"] or b["capacity_pct"] is None:
        return None
    return {
        "level": float(b["capacity_pct"]),
        "temp_c": None,
        "charging": (b["status"] or "").lower() in ("charging", "full"),
        "source": "sysfs",
        "counter": _sysfs_counter(root),
    }


def _health(env, root="/", reading=None, design=None):
    b = read_battery(root)
    if b["health"] is not None and b["health"] > 0:
        return float(b["health"]), "sysfs"
    if reading is not None:
        h = counter_health(reading["level"], reading.get("charge_counter_uah"), design)
        if h is not None:
            return float(h), "charge-counter"
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
    design = design_mah(env, run) if r.get("charge_counter_uah") else None
    r["health"], r["health_source"] = _health(env, root, r, design)
    return r


class MeasuredDrain:
    """The phone's own discharge rate, in percent of the shown level per hour, from the readings it gave.

    A battery reports whole percent, so a rate between two arbitrary readings can be off by a whole
    point. The time each level step is first seen anchors a segment instead (stamped midway between
    the two readings that bracket it), and a segment's rate is the points lost over the time between
    anchors. Each segment moves an exponentially weighted average by 1 - exp(-length / DRAIN_TAU_S).
    A level that bounces back up a point or two is ignored. When a charge counter is given and stays
    within about a point of the shown level, it gives finer positions and anchors need no step; one that
    disagrees with the level, or is missing from a reading after being used, is not used again in that run.
    Until the run spans DRAIN_WINDOW_S, rate() is None and the caller keeps its model. Charging or a
    jump in the level starts the run again, and so does a gap of DRAIN_MAX_GAP_S between readings
    (the phone slept, or nobody read it); a shorter gap past DRAIN_SOFT_GAP_S only drops the segment
    it falls in, since the steps inside it cannot be timed. Times are seconds on any steady clock that
    keeps counting while the phone sleeps.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.ema = None
        self.t_last = None  # time of the newest reading
        self.window_s = 0.0  # span from the first anchor to the newest reading
        self._level = self._low = None  # newest and lowest shown level of this run
        self._lo = self._lo_t = None  # lowest position (level, or counter position) and when it was first seen
        self._anchor = None  # (time, position) where the last segment ended
        self._t0 = None
        self._drop = 0.0
        self._cap = None  # counter units per 100 points, from the run's first reading at MIN_ESTIMATE_PCT or more
        self._off = False  # the counter disagreed with the level, or went missing once in use: not used again this run
        self._fine = False

    @property
    def via(self):
        return None if self.t_last is None else "counter" if self._fine else "level"

    def observe(self, t, level, charging, counter=None):
        if charging:
            self.reset()
            return
        if self.t_last is not None:
            if t <= self.t_last:
                return
            if t - self.t_last > DRAIN_MAX_GAP_S or self._level - level > DRAIN_JUMP or level - self._low > DRAIN_RISE:
                self.reset()
        first = self.t_last is None
        gap = not first and t - self.t_last > DRAIN_SOFT_GAP_S
        edge = not first and not gap and level < self._low
        x, fine = level, False
        if counter and counter > 0 and not self._off:
            if self._cap is None and level >= MIN_ESTIMATE_PCT:
                self._cap = counter / level * 100
            if self._cap:
                pos = counter / self._cap * 100
                if abs(pos - level) > DRAIN_FINE_TOL:
                    self._off = True
                else:
                    x, fine = pos, True
        if self._fine and not fine:
            self._off = True  # a counter in use went missing: one switch to the level, then no more for the run
        if fine != self._fine:
            self._fine, self._anchor, self._lo = fine, None, None
        if gap:
            self._anchor, self._lo = None, None
        if self._lo is None or x < self._lo:
            self._lo, self._lo_t = x, t
        te = t if fine or first else (self.t_last + t) / 2
        a = self._anchor
        if a is None:
            if fine or edge:
                self._anchor = (te, x)
                self._t0 = te if self._t0 is None else self._t0
        elif fine or edge:
            drop, dt = a[1] - x, te - a[0]
            if drop >= (DRAIN_FINE_STEP if fine else 1.0) and dt >= DRAIN_MIN_SEG_S:
                r = drop / dt * 3600.0
                if r > DRAIN_MAX:
                    self.reset()
                else:
                    w = 1.0 - math.exp(-dt / DRAIN_TAU_S)
                    self.ema = r if self.ema is None else self.ema + w * (r - self.ema)
                    self._drop += drop
                    self._anchor = (te, x)
        self.t_last, self._level = t, level
        self._low = level if self._low is None else min(self._low, level)
        self.window_s = 0.0 if self._t0 is None else max(0.0, t - self._t0)

    def rate(self, now):
        """Percent per hour once DRAIN_WINDOW_S of discharge has been seen, else None.

        While the level has not stepped for longer than the average predicts, the stretch since it
        last reached a new low caps the average: that stretch lost under one point (a quarter with
        the counter), so the rate is under that over its length.
        """
        if self.ema is None or now - self.t_last > DRAIN_MAX_GAP_S or self.window_s < DRAIN_WINDOW_S or self._drop < DRAIN_MIN_DROP:
            return None
        r = self.ema
        if self.t_last > self._lo_t:
            r = min(r, (DRAIN_FINE_STEP if self._fine else 1.0) * 3600.0 / (self.t_last - self._lo_t))
        return max(DRAIN_MIN, r)


def _boot_clock():
    try:
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    except (AttributeError, OSError):
        return time.monotonic()


class _MeasuredRate:
    """Stands where opos.battery.DrainEstimator stands in Runtime; the readings are fed in by LiveBattery."""

    def __init__(self, battery):
        self._battery = battery

    def add(self, sample):
        pass

    def rate(self):
        return self._battery.drain.rate(self._battery._drain_clock())


class LiveBattery:
    """Stands where DrainSim stands; advance() and charge() re-read instead of simulating."""

    def __init__(self, reader=read_live, clock=time.monotonic, refresh_s=REFRESH_S):
        self._reader = reader
        self._clock = clock
        # The drain is timed on the boot clock, which keeps counting while the phone sleeps; monotonic stops.
        self._drain_clock = _boot_clock if clock is time.monotonic else clock
        self.refresh_s = refresh_s
        self.drain = MeasuredDrain()
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
        self.drain.observe(self._drain_clock(), self.level, self.charging, r.get("charge_counter_uah") or r.get("counter"))

    def drain_rate(self, model_per_h):
        """(percent per hour to divide the level by, how it was got): measured once trusted, else the model's.

        The dict is the state payload's battery.drain: basis "measured" or "model", per_hour (the rate
        in use), measured_per_hour (None until trusted), model_per_hour, age_s (since the newest
        discharge reading, None when there is none), window_s (discharge observed so far) and via
        ("level" or "counter", None without one). A charging phone measures nothing, so its basis is "model".
        """
        now = self._drain_clock()
        d = self.drain
        m = d.rate(now)
        use = model_per_h if m is None else m
        return use, {
            "basis": "model" if m is None else "measured",
            "per_hour": round(use, 2),
            "measured_per_hour": None if m is None else round(m, 2),
            "model_per_hour": round(model_per_h, 2),
            "age_s": None if d.t_last is None else round(max(0.0, now - d.t_last), 1),
            "window_s": round(d.window_s, 1),
            "via": d.via,
        }

    @staticmethod
    def drain_words(info):
        """One line for the technical view saying where the drain figure came from."""
        if info["basis"] == "measured":
            return f"drain measured at {info['per_hour']:.1f}%/h over {info['window_s'] / 60:.0f} min"
        return f"drain from the model, not measured yet ({min(info['window_s'], DRAIN_WINDOW_S) / 60:.0f} of {DRAIN_WINDOW_S / 60:.0f} min)"

    def rate_estimator(self):
        return _MeasuredRate(self)

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
