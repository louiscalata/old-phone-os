# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib
from dataclasses import dataclass
from enum import Enum

@dataclass(frozen=True)
class PowerState:
    level: float
    charging: bool
    health: float
    temp_c: float
    goal_hours: float | None = None

class Mode(Enum):
    NORMAL = "NORMAL"
    SAVER = "SAVER"
    CRITICAL = "CRITICAL"

class Priority(Enum):
    ESSENTIAL = "ESSENTIAL"
    USEFUL = "USEFUL"
    OPTIONAL = "OPTIONAL"

@dataclass(frozen=True)
class Task:
    name: str
    priority: Priority
    cost: float

@dataclass(frozen=True)
class Decision:
    mode: Mode
    allowed: tuple
    deferred: tuple
    reason: str

def effective_level(state: PowerState) -> float:
    eff = state.level * state.health
    if eff < 0:
        eff = 0.0
    if eff > 100:
        eff = 100.0
    return float(eff)

def rule_decide(state: PowerState, tasks) -> Decision:
    if not 0 <= state.level <= 100:
        raise ValueError
    if not 0 <= state.health <= 1:
        raise ValueError
    effective = effective_level(state)
    if state.charging or effective >= 50:
        mode = Mode.NORMAL
    elif effective >= 15:
        mode = Mode.SAVER
    else:
        mode = Mode.CRITICAL
    if state.temp_c >= 45:
        if mode == Mode.NORMAL:
            mode = Mode.SAVER
        elif mode == Mode.SAVER:
            mode = Mode.CRITICAL
    allowed = []
    deferred = []
    for t in tasks:
        if t.priority == Priority.ESSENTIAL:
            allowed.append(t.name)
            continue
        if mode == Mode.NORMAL:
            allowed.append(t.name)
        elif mode == Mode.SAVER and t.priority == Priority.USEFUL:
            allowed.append(t.name)
        else:
            deferred.append(t.name)
    return Decision(mode, tuple(allowed), tuple(deferred), "rules")

class Governor:
    def __init__(self, advisor=None):
        self.advisor = advisor

    def decide(self, state: PowerState, tasks):
        tasks = tuple(tasks)
        baseline = rule_decide(state, tasks)
        if self.advisor is not None:
            with contextlib.suppress(Exception):
                dec = self.advisor(state, tasks)
                if not isinstance(dec, Decision):
                    raise TypeError("not Decision")
                if not isinstance(dec.mode, Mode):
                    raise TypeError("invalid mode")
                if dec.allowed is None or dec.deferred is None:
                    raise TypeError("None collection")
                if isinstance(dec.allowed, (str, bytes)) or isinstance(dec.deferred, (str, bytes)):
                    raise TypeError("str collection")
                if not isinstance(dec.allowed, (tuple, list, set)) or not isinstance(dec.deferred, (tuple, list, set)):
                    raise TypeError("bad collection type")
                if not isinstance(dec.reason, str):
                    raise TypeError("invalid reason")
                allowed = tuple(dec.allowed)
                deferred = tuple(dec.deferred)
                task_names = {t.name for t in tasks}
                allowed_set = set(allowed)
                deferred_set = set(deferred)
                if len(allowed) != len(allowed_set):
                    raise ValueError("duplicate in allowed")
                if len(deferred) != len(deferred_set):
                    raise ValueError("duplicate in deferred")
                if allowed_set & deferred_set:
                    raise ValueError("overlap")
                if allowed_set | deferred_set != task_names:
                    raise ValueError("coverage")
                essential_names = {t.name for t in tasks if t.priority == Priority.ESSENTIAL}
                if not essential_names.issubset(allowed_set):
                    raise ValueError("essential not allowed")
                if essential_names & deferred_set:
                    raise ValueError("essential deferred")
                order = {Mode.NORMAL: 0, Mode.SAVER: 1, Mode.CRITICAL: 2}
                final_mode = dec.mode if order[dec.mode] >= order[baseline.mode] else baseline.mode
                baseline_allowed = set(baseline.allowed)
                filtered_allowed_set = allowed_set & baseline_allowed
                filtered_allowed = tuple(t.name for t in tasks if t.name in filtered_allowed_set)
                filtered_deferred = tuple(t.name for t in tasks if t.name not in filtered_allowed_set)
                mode_overridden = final_mode != dec.mode
                tasks_overridden = filtered_allowed_set != allowed_set
                reason = baseline.reason if mode_overridden or tasks_overridden else dec.reason
                return Decision(final_mode, filtered_allowed, filtered_deferred, reason)
        return baseline
