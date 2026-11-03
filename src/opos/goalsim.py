# SPDX-License-Identifier: GPL-3.0-or-later
import math

from opos.benchmark_model import _load_model, resolve_model_path
from opos.power import static_low_power, total_drain
from opos.runtime import DEFAULT_TASKS, Priority, Runtime, Task
from opos.eol import EOL_PROFILE, BURST_A, brownout, load_amps
from opos.reserve import call_possible, should_shutdown


KNOWN_STEPS = ("dim screen", "batch sync", "model")


def _drain_loop(tasks, health, temp_c, level, t_s, step_s, max_h, profile, r0, paused, steps_used, update=None, pre_shutdown=None, check_brownout=True):
    shutdown_level = None
    while True:
        if update is not None:
            paused, steps_used = update(t_s, level)
        drain_per_h = total_drain(tasks, steps=steps_used, health=health, paused_set=paused)
        if profile == "eol":
            drain_per_h *= 1 + max(0.0, (30.0 - level) / 30.0)
        if pre_shutdown is not None and pre_shutdown(level, load_amps(drain_per_h * health)) is True:
            shutdown_level = level
            break
        if profile == "eol":
            if check_brownout and brownout(level, load_amps(drain_per_h * health) + BURST_A["call"], temp_c, r0) is True:
                shutdown_level = level
                break
        remaining = max_h * 3600.0 - t_s
        if remaining <= 0:
            break
        dt = step_s if step_s < remaining else remaining
        step_h = dt / 3600.0
        if drain_per_h > 0 and level - drain_per_h * step_h <= 0:
            t_s += level / drain_per_h * 3600.0
            level = 0
            break
        level -= drain_per_h * step_h
        t_s += dt
        if level <= 0 or t_s / 3600.0 >= max_h:
            break
    return level, t_s, paused, steps_used, shutdown_level


def simulate(policy, level=40.0, health=0.6, temp_c=30.0, goal_h=11.0, tasks=None, step_s=60.0, max_h=48.0, profile=None, model_path=None) -> dict:
    if policy not in ("basic", "router", "model"):
        raise ValueError(f"unknown policy: {policy!r}")
    if not isinstance(step_s, (int, float)) or not math.isfinite(step_s) or step_s <= 0:
        raise ValueError(f"step_s must be a finite positive number: {step_s!r}")
    if not isinstance(max_h, (int, float)) or not math.isfinite(max_h) or max_h <= 0:
        raise ValueError(f"max_h must be a finite positive number: {max_h!r}")
    if not isinstance(health, (int, float)) or not math.isfinite(health) or health <= 0:
        raise ValueError(f"health must be a finite positive number: {health!r}")
    if profile not in (None, "eol"):
        raise ValueError(f"unknown profile: {profile!r}")
    r0 = None
    if profile == "eol":
        health = EOL_PROFILE["health"]
        r0 = EOL_PROFILE["r0"]
    if tasks is None:
        tasks = tuple(list(DEFAULT_TASKS) + [Task("alarms", Priority.ESSENTIAL, 0.1)])
    else:
        tasks = tuple(tasks)
    protected = {t.name for t in tasks if t.priority == Priority.ESSENTIAL}
    steps = tuple(s for s in KNOWN_STEPS if s != "model")
    t_s = 0.0
    shutdown_level = None
    if policy == "basic":
        paused = set(static_low_power(level, tasks, health).paused)
        floor_overrides = sorted(paused & protected)
        paused = {p for p in paused if p not in protected}
        steps_used = tuple(steps)
        level, t_s, paused, steps_used, shutdown_level = _drain_loop(tasks, health, temp_c, level, t_s, step_s, max_h, profile, r0, paused, tuple(steps))
    elif policy == "router":
        rt = Runtime(tasks=tasks)
        paused = set()
        steps_used = ()
        floor_seen = set()

        def _update(t, lvl):
            tick = rt.step(t, lvl, health=health, temp_c=temp_c, goal_hours=max(goal_h - t / 3600.0, 0.5))
            floor_seen.update(p for p in tick.decision.deferred if p in protected)
            kept = {p for p in tick.decision.deferred if p not in protected}
            return kept, tuple(tick.steps)

        def _pre(lvl, base_a):
            return (profile == "eol" and (not call_possible(lvl, temp_c, health, base_a=base_a) or should_shutdown(lvl, temp_c, health)) is True)

        level, t_s, paused, steps_used, shutdown_level = _drain_loop(tasks, health, temp_c, level, t_s, step_s, max_h, profile, r0, paused, steps_used, update=_update, pre_shutdown=_pre, check_brownout=False)
        floor_overrides = sorted(floor_seen)
    else:
        mpath = resolve_model_path(model_path)
        if not mpath:
            raise FileNotFoundError("model unavailable: no path provided")
        model_data = _load_model(mpath)
        paused_cfg = model_data.get("paused")
        if not isinstance(paused_cfg, list):
            raise ValueError(f"model invalid: paused must be list {mpath!r}")
        steps_cfg = model_data.get("steps")
        if not isinstance(steps_cfg, list):
            raise ValueError(f"model invalid: steps must be list {mpath!r}")
        for _step in steps_cfg:
            if _step not in KNOWN_STEPS:
                raise ValueError(f"model invalid: unknown step {_step!r} {mpath!r}")
        paused = set(paused_cfg)
        overridden = paused & protected
        if overridden:
            raise ValueError(f"model invalid: cannot pause ESSENTIAL {sorted(overridden)!r} {mpath!r}")
        steps_used = tuple(steps_cfg)
        level, t_s, paused, steps_used, shutdown_level = _drain_loop(tasks, health, temp_c, level, t_s, step_s, max_h, profile, r0, paused, steps_used)
        hours = t_s / 3600.0
        return {"policy": policy, "hours": hours, "reaches_goal": hours >= goal_h, "paused_final": sorted(paused), "steps_final": list(steps_used), "profile": profile, "shutdown_level": shutdown_level, "model_version": model_data.get("version"), "floor_overrides": []}
    hours = t_s / 3600.0
    return {"policy": policy, "hours": hours, "reaches_goal": hours >= goal_h, "paused_final": sorted(paused), "steps_final": list(steps_used), "profile": profile, "shutdown_level": shutdown_level, "floor_overrides": floor_overrides}
