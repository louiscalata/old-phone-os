# SPDX-License-Identifier: GPL-3.0-or-later
from opos.eol import is_worn, r0_for_health, call_reserve, shutdown_floor, max_safe_amps, MIN_CALL_A
from opos.governor import Decision, Mode, Priority
from opos.power import KEEP

WORN_REASON = "worn battery: keeping a reserve for calls"
BACKGROUND_MARGIN = 15.0
ESSENTIAL_MARGIN = 4.0

def reserve_level(temp_c, health):
    return call_reserve(temp_c, r0_for_health(health), health=health)

def apply_reserve(decision, level, temp_c, health, tasks):
    if not is_worn(health):
        return decision
    reserve = reserve_level(temp_c, health)
    if level > reserve + BACKGROUND_MARGIN:
        return decision
    allowed = decision.allowed
    deferred = decision.deferred
    if level <= reserve + ESSENTIAL_MARGIN:
        new_allowed = tuple(name for name in allowed if name in KEEP)
    else:
        task_map = {t.name: t for t in tasks}
        new_allowed = tuple(name for name in allowed if name in KEEP or ((t := task_map.get(name)) and t.priority == Priority.ESSENTIAL))
    removed = [name for name in allowed if name not in new_allowed]
    seen = set(deferred)
    new_deferred_list = list(deferred)
    for name in removed:
        if name not in seen:
            new_deferred_list.append(name)
            seen.add(name)
    new_deferred = tuple(new_deferred_list)
    return Decision(Mode.CRITICAL, new_allowed, new_deferred, WORN_REASON)

def shape_burst(burst_a, level, temp_c, health, base_a=0.0):
    if not is_worn(health):
        return burst_a
    return min(burst_a, max(0.0, max_safe_amps(level, temp_c, r0_for_health(health)) - base_a))

def should_shutdown(level, temp_c, health):
    if not is_worn(health):
        return False
    return level <= shutdown_floor(temp_c, r0_for_health(health))

def call_possible(level, temp_c, health, base_a=0.0):
    return shape_burst(1.0, level, temp_c, health, base_a) >= MIN_CALL_A
