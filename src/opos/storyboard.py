# SPDX-License-Identifier: GPL-3.0-or-later
import sys
import time
import math
from opos.essentials import Essentials
from opos.goalsim import simulate
from opos.home import is_quiet
from opos.runtime import DEFAULT_TASKS, Runtime, Task, Priority
from opos.sim import DrainSim
from opos.ui import ScreenModem
from opos.power import static_low_power, plan, important_hours

TAGLINE = "We're giving more capabilities and life to an old phone."

def _play(emit, fast):
    emit(TAGLINE)

    tick = Runtime().step(0, 40.0, health=0.6, temp_c=30.0)
    emit("== Old phone, worn battery ==")
    for line in tick.screen:
        emit("  " + line)
    if not fast:
        time.sleep(1.5)

    tasks = list(DEFAULT_TASKS) + [Task("alarms", Priority.USEFUL, 0.1)]
    basic_plan = static_low_power(40, tasks, 0.6)
    smart_plan = plan(40, 11, tasks, 0.6)

    def fmt(h):
        if math.isinf(h):
            return "∞"
        h_int = int(h)
        m = int(round((h - h_int) * 60))
        if m >= 60:
            h_int += 1
            m = 0
        return f"{h_int} h {m} min"

    basic_hours = important_hours(basic_plan, 40)
    smart_hours = important_hours(smart_plan, 40)

    emit("== More life: last until 11 PM ==")
    emit(f"  Basic low-power mode: calls and messages work for about {fmt(basic_hours)}")
    emit(f"  Smart low-power mode: calls and messages work for about {fmt(smart_hours)}")
    emit(f"  {smart_plan.reason}")
    gain = round((smart_hours - basic_hours) * 60)
    emit(f"  Gain: {gain} more minutes for calls and messages")
    emit(f"  Reaches 11 PM: {'yes' if smart_plan.meets_goal else 'no'}")
    if not fast:
        time.sleep(1.5)

    basic_goal = simulate("basic", step_s=300.0)
    router_goal = simulate("router", step_s=300.0)
    emit("== More life: the rule router drives the goal ==")
    emit(f"  Basic low-power mode: calls and messages work for about {fmt(basic_goal['hours'])}")
    emit(f"  Rule router, last until 11 PM: calls and messages work for about {fmt(router_goal['hours'])}")
    emit(f"  Router gain over basic: {round((router_goal['hours'] - basic_goal['hours']) * 60)} minutes")
    emit(f"  Router reaches 11 PM: {'yes' if router_goal['reaches_goal'] else 'no'}")
    if not fast:
        time.sleep(1.5)

    emit("== Battery falling ==")
    task_cost = {}
    for t in DEFAULT_TASKS:
        name = getattr(t, 'name', None)
        if name is None:
            try:
                name = t[0]
            except Exception:
                continue
        cost = getattr(t, 'cost', None)
        if cost is None:
            try:
                cost = t[2]
            except Exception:
                cost = 0
        task_cost[name] = cost
    sim = DrainSim(level=40.0, health=0.6, temp_c=30.0, speed=60.0)
    for _ in range(40):
        mode_upper = str(getattr(tick.decision.mode, 'name', tick.decision.mode)).upper()
        allowed = tick.decision.allowed
        costs_list = [task_cost[n] for n in allowed if n in task_cost]
        sim.advance(10.0, mode_upper, costs_list)
        emit(f"  battery {int(sim.level)}% - {mode_upper.lower()}")
        if sim.level <= 5:
            break
        tick = Runtime().step(sim.t_s, sim.level, health=sim.health, temp_c=sim.temp_c)
    if not fast:
        time.sleep(1.5)

    modem = ScreenModem()
    emit("== At 5%: a call and a message ==")
    ess = Essentials(modem)
    ess.call("+1 555 0100")
    ess.end_call()
    ess.send_message("+1 555 0100", "On my way")
    emit("  called +15550100")
    emit("  sent: On my way")
    if not fast:
        time.sleep(1.5)

    quiet = Runtime().step(sim.t_s, sim.level, health=0.6, temp_c=sim.temp_c)
    emit("== Quiet by default ==")
    for line in quiet.screen:
        emit("  " + line)
    emit("  quiet: yes" if is_quiet(quiet.screen) else "  quiet: NO")
    if not fast:
        time.sleep(1.5)

    shown = Runtime(visible=True).step(sim.t_s, sim.level, health=0.6, temp_c=sim.temp_c)
    emit("== More capabilities: the rule router, shown when you opt in ==")
    for line in shown.screen:
        emit("  " + line)
    if not fast:
        time.sleep(1.5)

    emit("== Charging ==")
    charging_plan = plan(40, 11, tasks, 0.6, charging=True)
    emit(f"  {charging_plan.reason}")

    emit(TAGLINE)


def run(fast=True):
    out = []
    _play(out.append, fast)
    return out


def main(argv=None):
    fast = "--fast" in (sys.argv[1:] if argv is None else argv)
    _play(lambda s: print(s, flush=True), fast)


if __name__ == "__main__":
    main()
