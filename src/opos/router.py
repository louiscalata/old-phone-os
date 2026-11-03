# SPDX-License-Identifier: GPL-3.0-or-later
"""Battery-aware routing of tasks by power, heat, and goals."""
from opos.governor import PowerState, Priority, Mode, Decision
from opos.power import SAVINGS_STEPS, feature_drain

HORIZON_H = 4.0 # hours a useful task must be funded for
OPTIONAL_FLOOR = 80.0 # effective battery percent an optional task needs
COST_WEIGHT = 2.0 # how strongly a task's cost counts
HOT_C = 45.0 # temperature where non-essential work stops

GOAL_BASE_DRAIN = sum(feature_drain(tuple(s[0] for s in SAVINGS_STEPS), 1.0).values()) + 0.8 # percent per hour with every saving step applied, plus 0.8 for the radio

def afford(state: PowerState, task):
    """Return whether battery can fund task and why."""
    eff = state.level * state.health
    if task.priority == Priority.ESSENTIAL:
        return True, "essential"
    if state.charging:
        return True, "charging"
    if state.temp_c >= HOT_C:
        return False, "too hot"
    if state.goal_hours is not None:
        goal = max(float(state.goal_hours), 0.5)
        if eff / goal - GOAL_BASE_DRAIN - task.cost >= 0:
            return True, "fits your goal"
        return False, "would miss your goal"
    if task.priority == Priority.OPTIONAL:
        if eff >= OPTIONAL_FLOOR:
            return True, "battery above 80 percent"
        return False, "battery below 80 percent"
    if task.priority == Priority.USEFUL:
        denom = 5.0 + COST_WEIGHT * task.cost
        if eff / denom >= HORIZON_H:
            return True, "enough for the next 4 hours"
        return False, "not enough for the next 4 hours"
    return False, "unknown"

class Router:
    """Convert task affordability into a Decision."""
    def __init__(self, visible=False):
        self.visible = visible

    def __call__(self, state, tasks):
        if not 0 <= state.level <= 100:
            raise ValueError
        if not 0 <= state.health <= 1:
            raise ValueError
        allowed = []
        deferred = []
        has_non_essential = False
        for t in tasks:
            if t.priority != Priority.ESSENTIAL:
                has_non_essential = True
            ok, _ = afford(state, t)
            if ok:
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
