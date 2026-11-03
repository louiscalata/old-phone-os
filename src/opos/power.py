# SPDX-License-Identifier: GPL-3.0-or-later
from dataclasses import dataclass
import math

FEATURE_COST = {"screen": 6.0, "radio": 1.5, "sync": 2.5, "model": 0.8}
SAVINGS_STEPS = (
    ("dim screen", "screen", 0.6),
    ("short screen timeout", "screen", 0.7),
    ("batch sync", "sync", 0.4),
    ("slow background polling", "radio", 0.7),
)
KEEP = frozenset({"calls", "messages", "alarms"})

@dataclass(frozen=True)
class Plan:
    steps: tuple
    paused: tuple
    drain_per_h: float
    hours_left: float
    meets_goal: bool
    reason: str

def feature_drain(steps=(), health=1.0):
    divisor = max(health, 0.1)
    factors = {f: 1.0 for f in FEATURE_COST}
    for name in steps:
        for s_name, feat, factor in SAVINGS_STEPS:
            if s_name == name:
                factors[feat] *= factor
                break
    return {f: FEATURE_COST[f] * factors[f] / divisor for f in FEATURE_COST}

def total_drain(tasks, paused=(), steps=(), health=1.0, *, paused_set=None):
    tasks = tuple(tasks)
    if paused_set is None:
        paused_set = set(paused)
    divisor = max(health, 0.1)
    drain = sum(feature_drain(steps, health).values())
    for t in tasks:
        if getattr(t, "name", None) in paused_set:
            continue
        drain += getattr(t, "cost", 0) / divisor
    return drain

def steps_for_goal(level, goal_h, tasks, paused, health=1.0):
    tasks = tuple(tasks)
    names = tuple(s[0] for s in SAVINGS_STEPS)
    if goal_h is None or goal_h <= 0:
        return ()
    if level <= 0:
        return names
    try:
        paused_set = set(paused)
    except TypeError:
        paused_set = set()
    target = level / goal_h
    for n in range(len(names) + 1):
        prefix = names[:n]
        if total_drain(tasks, paused_set=paused_set, steps=prefix, health=health) <= target:
            return prefix
    return names

def time_left_words(hours):
    if math.isinf(hours):
        return "Lasts all day"
    if hours < 1/60:
        return "Almost empty"
    if hours < 1:
        m = round(hours * 60)
        return f"About {m} min left"
    h = int(hours)
    m = round((hours - h) * 60)
    if m == 60:
        h += 1
        m = 0
    if m == 0:
        return f"About {h} h left"
    return f"About {h} h {m} min left"

def plan(level, hours_to_goal, tasks, health=1.0, charging=False, in_use=None):
    tasks = tuple(tasks)
    if charging:
        drain = total_drain(tasks, steps=(), health=health)
        return Plan((), (), drain, float("inf"), True, "Charging: everything back on")
    if hours_to_goal is None:
        drain = total_drain(tasks, steps=(), health=health)
        hours_left = float("inf") if drain <= 0 else level / drain
        return Plan((), (), drain, hours_left, False, "No goal set")
    steps_applied = []
    paused_names = []
    step_idx = 0
    def cur_drain():
        return total_drain(tasks, paused=tuple(paused_names), steps=tuple(steps_applied), health=health)
    while True:
        drain = cur_drain()
        hours_left_cur = float("inf") if drain <= 0 else level / drain
        if hours_left_cur >= hours_to_goal:
            break
        if step_idx < len(SAVINGS_STEPS):
            steps_applied.append(SAVINGS_STEPS[step_idx][0])
            step_idx += 1
            continue
        best_task = None
        best_cost = -1.0
        best_pos = -1
        for idx, t in enumerate(tasks):
            name = getattr(t, "name", None)
            if not name or name in paused_names:
                continue
            if name in KEEP:
                continue
            if in_use and name == in_use:
                continue
            prio = getattr(t, "priority", None)
            if prio == "ESSENTIAL" or getattr(prio, "name", None) == "ESSENTIAL":
                continue
            cost = getattr(t, "cost", 0)
            if cost > best_cost or (cost == best_cost and idx > best_pos):
                best_cost = cost
                best_task = t
                best_pos = idx
        if best_task is None:
            break
        paused_names.append(best_task.name)
    drain_final = cur_drain()
    hours_left_final = float("inf") if drain_final <= 0 else level / drain_final
    meets_goal = hours_left_final >= hours_to_goal
    reason = plan_reason(steps_applied, paused_names, meets_goal, in_use)
    return Plan(tuple(steps_applied), tuple(paused_names), drain_final, hours_left_final, meets_goal, reason)

def plan_reason(steps, paused, meets_goal, in_use=None):
    """Words for a goal plan: its saving steps, its pauses and whether it reaches the goal."""
    if not steps and not paused:
        return "Lasts to the goal with everything on"
    base = "Saving power for calls and messages"
    if in_use:
        base += f" and {in_use}"
    parts = []
    if steps:
        parts.append(", ".join(steps))
    if paused:
        parts.append(f"paused {', '.join(paused)}")
    reason = f"{base}: {'; '.join(parts)}"
    if not meets_goal:
        reason += ". Cannot reach the goal; calls and messages kept on"
    return reason

def static_low_power(level, tasks, health=1.0):
    tasks = tuple(tasks)
    steps = ("dim screen", "batch sync")
    paused = tuple(
        t.name
        for t in tasks
        if (getattr(t, "priority", None) == "OPTIONAL" or getattr(getattr(t, "priority", None), "name", None) == "OPTIONAL")
    )
    drain = total_drain(tasks, paused=paused, steps=steps, health=health)
    hours_left = float("inf") if drain <= 0 else level / drain
    return Plan(steps, paused, drain, hours_left, False, "Basic low-power mode")

def important_hours(plan_obj, level):
    d = plan_obj.drain_per_h
    if d <= 0:
        return float("inf")
    return level / d

def minutes_gained(level, hours_to_goal, tasks, health=1.0, in_use=None):
    tasks = tuple(tasks)
    plan_obj = plan(level, hours_to_goal, tasks, health=health, in_use=in_use)
    baseline_drain = total_drain(tasks, steps=(), health=health)
    baseline_hours = float("inf") if baseline_drain <= 0 else level / baseline_drain
    plan_hours = plan_obj.hours_left
    if math.isinf(baseline_hours) and math.isinf(plan_hours):
        return 0.0
    return (plan_hours - baseline_hours) * 60
