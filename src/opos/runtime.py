# SPDX-License-Identifier: GPL-3.0-or-later
from dataclasses import dataclass
from opos.governor import PowerState, Priority, Task, Governor, Decision, Mode, rule_decide
from opos.battery import Sample, DrainEstimator, hours_left, essentials_reserve
from opos.budget import budget_for
from opos.home import home_screen
from opos.router import Router
from opos.power import KEEP, steps_for_goal
from opos.reserve import apply_reserve

DEFAULT_TASKS = (
    Task("calls", Priority.ESSENTIAL, 0.5),
    Task("messages", Priority.ESSENTIAL, 0.2),
    Task("email", Priority.USEFUL, 2),
    Task("maps", Priority.USEFUL, 4),
    Task("photos backup", Priority.OPTIONAL, 6),
)

@dataclass(frozen=True)
class Tick:
    decision: Decision
    budget: object
    screen: list[str]
    hours_left: float
    reserve: float
    steps: tuple = ()

class Runtime:
    def __init__(self, visible=False, tasks=DEFAULT_TASKS):
        self.visible = visible
        self.tasks = tuple(tasks)
        self.estimator = DrainEstimator()
        self.governor = Governor(advisor=Router(visible=visible))

    def step(self, t_s, level, charging=False, health=1.0, temp_c=25.0, goal_hours=None):
        self.estimator.add(Sample(t_s, level))
        rate = self.estimator.rate()
        state = PowerState(level, charging, health, temp_c, goal_hours)
        decision = self.governor.decide(state, self.tasks)
        rules = rule_decide(state, self.tasks)
        mode = decision.mode
        allowed_set = set(decision.allowed)
        deferred_set = set(decision.deferred)
        order = {Mode.NORMAL: 0, Mode.SAVER: 1, Mode.CRITICAL: 2}
        if order[rules.mode] > order[mode]:
            mode = rules.mode
            for name in rules.deferred:
                if name in allowed_set:
                    allowed_set.remove(name)
                deferred_set.add(name)
        merged = Decision(mode, tuple(t.name for t in self.tasks if t.name in allowed_set) + tuple(sorted(allowed_set - {t.name for t in self.tasks})), tuple(t.name for t in self.tasks if t.name in deferred_set) + tuple(sorted(deferred_set - {t.name for t in self.tasks})), decision.reason)
        reserve = essentials_reserve(rate) if rate is not None else 0.0
        h_left = hours_left(level, rate) if rate and rate != 0 else float("inf")
        final_decision = merged
        floor = essentials_reserve(None)
        waived = goal_hours is not None and not charging and temp_c < 45
        if level <= floor or (level <= reserve and not waived):
            essential_names = {t.name for t in self.tasks if t.priority == Priority.ESSENTIAL}
            allowed_filtered = tuple(n for n in merged.allowed if n in essential_names)
            deferred_set2 = set(merged.deferred)
            for n in merged.allowed:
                if n not in essential_names:
                    deferred_set2.add(n)
            final_decision = Decision(merged.mode, allowed_filtered, tuple(t.name for t in self.tasks if t.name in deferred_set2) + tuple(sorted(n for n in deferred_set2 if n not in {t.name for t in self.tasks})), merged.reason)
        task_names = {t.name for t in self.tasks}
        allowed_list = list(final_decision.allowed)
        allowed_lookup = set(allowed_list)
        deferred_set3 = set(final_decision.deferred)
        for t in self.tasks:
            name = t.name
            if name in KEEP and name in task_names:
                if name not in allowed_lookup:
                    allowed_list.append(name)
                    allowed_lookup.add(name)
                if name in deferred_set3:
                    deferred_set3.remove(name)
        final_decision = Decision(final_decision.mode, tuple(allowed_list), tuple(t.name for t in self.tasks if t.name in deferred_set3) + tuple(sorted(n for n in deferred_set3 if n not in task_names)), final_decision.reason)
        if final_decision.reason.startswith("router"):
            # Keep who decided: Glimmer's extra pauses stay attributed to Glimmer.
            who = "router+glimmer" if final_decision.reason.startswith("router+glimmer") else "router"
            final_decision = Decision(final_decision.mode, final_decision.allowed, final_decision.deferred, f"{who}: allowed {len(final_decision.allowed)}, deferred {len(final_decision.deferred)}")
        if not charging:
            before_reserve = final_decision
            final_decision = apply_reserve(final_decision, level, temp_c, health, self.tasks)
            if before_reserve.reason.startswith("router+glimmer") and not set(final_decision.deferred).difference(before_reserve.deferred):
                final_decision = Decision(final_decision.mode, final_decision.allowed, final_decision.deferred, before_reserve.reason)
        if goal_hours is not None and not charging:
            steps = steps_for_goal(level, goal_hours, self.tasks, final_decision.deferred, health)
        else:
            steps = ()
        budget = budget_for(final_decision.mode, temp_c)
        screen = home_screen(level, final_decision, visible=self.visible)
        return Tick(final_decision, budget, screen, h_left, reserve, steps)
