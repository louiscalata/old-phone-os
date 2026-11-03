# SPDX-License-Identifier: GPL-3.0-or-later
"""Battery-aware routing of tasks by power, heat, and goals."""
from opos.governor import PowerState, Priority, Mode, Decision
from opos.power import SAVINGS_STEPS, feature_drain

HORIZON_H = 4.0 # hours a useful task must be funded for
OPTIONAL_FLOOR = 80.0 # effective battery percent an optional task needs
COST_WEIGHT = 2.0 # how strongly a task's cost counts
HOT_C = 45.0 # temperature where non-essential work stops, charging or not

GOAL_BASE_DRAIN = sum(feature_drain(tuple(s[0] for s in SAVINGS_STEPS), 1.0).values()) + 0.8 # percent per hour with every saving step applied, plus 0.8 for calls, messages and alarms
_FUND_ORDER = {Priority.USEFUL: 0, Priority.OPTIONAL: 1}
GOAL_MISS = "would miss your goal"

def goal_spare(state: PowerState):
    """Percent per hour a goal leaves for non-essential tasks, or None without a goal."""
    if state.goal_hours is None:
        return None
    return state.level * state.health / max(float(state.goal_hours), 0.5) - GOAL_BASE_DRAIN

def fund(state: PowerState, tasks, first=()):
    """Return {name: (allowed, why)} for tasks, funding a goal budget once for all of them.

    With a goal, non-essential tasks are funded in a fixed order: useful before optional,
    then lower cost first, then name. Each funded task's cost is subtracted before the next
    is checked, so the allowed tasks together fit the goal. Heat stops non-essential work
    even while charging.

    first names tasks funded before that order (the app in use). Their cost comes off the
    budget even when it does not fit, because the owner's foreground use runs them anyway.
    """
    tasks = tuple(tasks)
    first = frozenset(first)
    out = {}
    eff = state.level * state.health
    spare = goal_spare(state)
    queue = []
    for t in tasks:
        if t.priority == Priority.ESSENTIAL:
            out[t.name] = (True, "essential")
        elif state.temp_c >= HOT_C:
            out[t.name] = (False, "too hot")
        elif state.charging:
            out[t.name] = (True, "charging")
        elif spare is not None:
            queue.append(t)
        elif t.priority == Priority.OPTIONAL:
            out[t.name] = (True, "battery above 80 percent") if eff >= OPTIONAL_FLOOR else (False, "battery below 80 percent")
        elif t.priority == Priority.USEFUL:
            ok = eff / (5.0 + COST_WEIGHT * t.cost) >= HORIZON_H
            out[t.name] = (True, "enough for the next 4 hours") if ok else (False, "not enough for the next 4 hours")
        else:
            out[t.name] = (False, "unknown")
    for t in sorted(queue, key=lambda t: (t.name not in first, _FUND_ORDER.get(t.priority, 2), t.cost, t.name)):
        ok = spare - t.cost >= 0
        if ok or t.name in first:
            spare -= t.cost
        out[t.name] = (True, "fits your goal") if ok else (False, GOAL_MISS)
    return out

def afford(state: PowerState, task):
    """Return whether battery can fund task on its own, and why."""
    return fund(state, (task,))[task.name]

class Router:
    """Convert task affordability into a Decision."""
    def __init__(self, visible=False):
        self.visible = visible

    def __call__(self, state, tasks):
        if not 0 <= state.level <= 100:
            raise ValueError
        if not 0 <= state.health <= 1:
            raise ValueError
        tasks = tuple(tasks)
        verdicts = fund(state, tasks)
        allowed = []
        deferred = []
        has_non_essential = False
        for t in tasks:
            if t.priority != Priority.ESSENTIAL:
                has_non_essential = True
            if verdicts[t.name][0]:
                allowed.append(t)
            else:
                deferred.append(t)
        if has_non_essential and all(t.priority == Priority.ESSENTIAL for t in allowed):
            mode = Mode.CRITICAL
        elif deferred:
            mode = Mode.SAVER
        else:
            mode = Mode.NORMAL
        if self.visible:
            reason = f"router: allowed {len(allowed)}, deferred {len(deferred)}"
        else:
            reason = "router"
        return Decision(mode, tuple(t.name for t in allowed), tuple(t.name for t in deferred), reason)
