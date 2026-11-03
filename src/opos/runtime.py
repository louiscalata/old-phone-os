# SPDX-License-Identifier: GPL-3.0-or-later
"""The final decision: the policy's soft pauses, then the owner's overrides, then the hard floors."""
from dataclasses import dataclass
from opos.governor import PowerState, Priority, Task, Governor, Decision, Mode, rule_decide
from opos.battery import Sample, DrainEstimator, hours_left, essentials_reserve
from opos.budget import budget_for
from opos.home import home_screen
from opos.router import GOAL_MISS, HOT_C, Router, fund
from opos.power import KEEP, steps_for_goal, total_drain
from opos.reserve import apply_reserve, reserve_keeps

DEFAULT_TASKS = (
    Task("calls", Priority.ESSENTIAL, 0.5),
    Task("messages", Priority.ESSENTIAL, 0.2),
    Task("email", Priority.USEFUL, 2),
    Task("maps", Priority.USEFUL, 4),
    Task("photos backup", Priority.OPTIONAL, 6),
)

# Hard floors, strongest first: nothing the owner does re-allows a task one of these refuses.
# "reserve": the worn-battery reserve or the measured reserve for calls; "hot": 45 C or more;
# "low": the battery rules' CRITICAL mode (usable battery under 15 percent).
HARD_FLOORS = ("reserve", "hot", "low")
# Soft causes, which the owner's Undo and foreground use may override: "goal", "optional"
# (usable battery under 80 percent), "horizon" (not enough for the next 4 hours), "rules"
# (the battery rules' SAVER mode), "glimmer" (Muse Glimmer's extra pause) and "other".
_ROUTER_CAUSES = {"would miss your goal": "goal", "battery below 80 percent": "optional",
                  "not enough for the next 4 hours": "horizon", "too hot": "hot"}
_ORDER = {Mode.NORMAL: 0, Mode.SAVER: 1, Mode.CRITICAL: 2}

def _goal_refused(verdicts):
    """Names the goal budget does not fund."""
    return {name for name, (ok, why) in verdicts.items() if not ok and why == GOAL_MISS}

@dataclass(frozen=True)
class Tick:
    decision: Decision
    budget: object
    screen: list[str]
    hours_left: float
    reserve: float
    steps: tuple = ()
    causes: tuple = ()  # (name, cause) for each paused task, in task order
    drain_per_h: float = 0.0  # percent per hour for the final allowed set and steps

    @property
    def held(self):
        """Paused tasks a hard floor refuses; Undo and foreground use do not change these."""
        return tuple(name for name, cause in self.causes if cause in HARD_FLOORS)

class Runtime:
    def __init__(self, visible=False, tasks=DEFAULT_TASKS):
        self.visible = visible
        self.tasks = tuple(tasks)
        self.estimator = DrainEstimator()
        self.governor = Governor(advisor=Router(visible=visible))

    def step(self, t_s, level, charging=False, health=1.0, temp_c=25.0, goal_hours=None, *, in_use=None, undone=()):
        """Decide what runs now.

        Soft pauses (the policy's, and the goal budget's) come first, then the owner's
        overrides (in_use, undone), then the hard floors; the owner cannot undo a floor.
        """
        self.estimator.add(Sample(t_s, level))
        rate = self.estimator.rate()
        state = PowerState(level, charging, health, temp_c, goal_hours)
        decision = self.governor.decide(state, self.tasks)
        rules = rule_decide(state, self.tasks)
        names = tuple(t.name for t in self.tasks)
        known = set(names)
        essential = {t.name for t in self.tasks if t.priority == Priority.ESSENTIAL} | (KEEP & known)
        mode = decision.mode
        allowed = set(decision.allowed)
        soft = set(decision.deferred)
        # Goal-saving pauses (soft) are the router's cumulative goal budget, with the app in
        # use funded first; they replace the router's own goal pauses, made without that app.
        plain = fund(state, self.tasks)
        verdicts = fund(state, self.tasks, (in_use,)) if in_use else plain
        goal_paused = _goal_refused(verdicts)
        if decision.reason.startswith("router"):
            # A task the battery rules pause (SAVER) stays paused; only the owner's override below frees it.
            freed = ((_goal_refused(plain) - goal_paused) & soft) - set(rules.deferred)
            soft -= freed
            allowed |= freed
        allowed.difference_update(goal_paused)
        soft.update(goal_paused)
        if _ORDER[rules.mode] > _ORDER[mode]:
            mode = rules.mode
            allowed.difference_update(rules.deferred)
            soft.update(rules.deferred)
        # The owner's overrides re-allow soft pauses only.
        wanted = set(undone) | ({in_use} if in_use else set())
        overridden = wanted & soft & known
        soft -= overridden
        allowed |= overridden
        # Hard floors, applied last.
        reserve = essentials_reserve(rate) if rate is not None else 0.0
        h_left = hours_left(level, rate) if rate and rate != 0 else float("inf")
        waived = goal_hours is not None and not charging and temp_c < HOT_C
        held = {}
        floors = (("reserve", level <= essentials_reserve(None) or (level <= reserve and not waived)),
                  ("hot", temp_c >= HOT_C), ("low", rules.mode == Mode.CRITICAL))
        for floor, active in floors:
            if active:
                for name in names:
                    if name not in essential:
                        held.setdefault(name, floor)
        allowed.difference_update(held)
        soft.update(held)
        allowed |= KEEP & known
        soft -= KEEP & known
        reason = decision.reason
        if reason.startswith("router"):
            who = "router+glimmer" if reason.startswith("router+glimmer") else "router"
            reason = f"{who}: allowed {len(self._ordered(allowed))}, deferred {len(self._ordered(soft))}"
        final = Decision(mode, self._ordered(allowed), self._ordered(soft), reason)
        if not charging:
            keeps = reserve_keeps(level, temp_c, health, self.tasks)
            if keeps is not None:
                for name in names:
                    if not keeps(name):
                        held[name] = "reserve"
            before_reserve = final
            final = apply_reserve(final, level, temp_c, health, self.tasks)
            final = Decision(final.mode, self._ordered(final.allowed), self._ordered(final.deferred), final.reason)
            if before_reserve.reason.startswith("router+glimmer") and not set(final.deferred).difference(before_reserve.deferred):
                final = Decision(final.mode, final.allowed, final.deferred, before_reserve.reason)
        causes = self._causes(final, held, verdicts, rules, goal_paused)
        if final.reason.startswith("router+glimmer") and overridden - set(held) and "glimmer" not in dict(causes).values():
            # The owner turned Glimmer's only extra pauses back on: the battery rules decide again.
            final = Decision(final.mode, final.allowed, final.deferred, f"router: allowed {len(final.allowed)}, deferred {len(final.deferred)}")
        # The fewest saving steps that, with the final pauses, fit the goal.
        steps = steps_for_goal(level, goal_hours, self.tasks, final.deferred, health) if goal_hours is not None and not charging else ()
        budget = budget_for(final.mode, temp_c)
        screen = home_screen(level, final, visible=self.visible)
        drain = total_drain(self.tasks, paused=final.deferred, steps=steps, health=health)
        return Tick(final, budget, screen, h_left, reserve, steps, causes, drain)

    def _ordered(self, names):
        """Names in task order, then any unknown names sorted."""
        names = set(names)
        known = tuple(t.name for t in self.tasks)
        return tuple(n for n in known if n in names) + tuple(sorted(names - set(known)))

    def _causes(self, final, held, verdicts, rules, goal_paused):
        """Why each paused task is paused: its hard floor first, then the soft cause."""
        glimmer = final.reason.startswith("router+glimmer")
        out = []
        for name in final.deferred:
            if name in held:
                cause = held[name]
            elif name in verdicts and not verdicts[name][0]:
                cause = _ROUTER_CAUSES.get(verdicts[name][1], "other")
            elif name in rules.deferred:
                cause = "rules"
            elif name in goal_paused:
                cause = "goal"
            elif glimmer:
                cause = "glimmer"
            else:
                cause = "other"
            out.append((name, cause))
        return tuple(out)
