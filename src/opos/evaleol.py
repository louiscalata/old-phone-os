# SPDX-License-Identifier: GPL-3.0-or-later
"""EOL evaluation helpers."""
import random
import sys
from opos.eol import BURST_A, MIN_CALL_A
from opos.governor import PowerState, Decision, Mode
from opos.reserve import apply_reserve, shape_burst, should_shutdown
from opos.sim import eol_sim
from opos.evaluate import EVAL_TASKS, rules_policy, static_low_power, router_policy
from opos.eol import load_amps
from opos.sim import DRAIN_PER_HOUR

SERVICES = (("calls","calls","call",60), ("sms","messages","message",30), ("alarms","alarms","alarm",120), ("app","app in use","app",30))
BACKGROUND = (("email","sync",15), ("photos backup","sync",10), ("maps","app",20))   # (task, burst key, period minutes)

def basic_policy(state, tasks, t_s=0.0):
    return Decision(Mode.NORMAL, tuple(t.name for t in tasks), (), "basic")

def scenarios_eol(n, seed):
    rng = random.Random(seed)
    temps = [25, 0, 10, 35, 45]
    out = []
    for i in range(n):
        level = rng.uniform(60, 100)
        health = rng.uniform(0.3, 0.4)
        temp_c = temps[i % 5]
        out.append({"level": level, "health": health, "temp_c": temp_c})
    return out
def run_eol(policy, scenario, aware=False, tick_s=60.0, max_min=1440):
    sim = eol_sim(level=scenario["level"], temp_c=scenario["temp_c"], speed=1.0)
    sim.health = scenario["health"]
    graceful = False
    active = {n: True for n, _, _, _ in SERVICES}
    end_time = {}
    i = 0
    while i < max_min and not sim.dead:
        t_s = i * tick_s
        state = PowerState(sim.level, False, sim.health, sim.temp_c)
        d = policy(state, EVAL_TASKS, t_s=t_s)
        if aware:
            d = apply_reserve(d, sim.level, sim.temp_c, sim.health, EVAL_TASKS)
            if should_shutdown(sim.level, sim.temp_c, sim.health):
                graceful = True
                break
        allowed = set(d.allowed)
        costs = [t.cost for t in EVAL_TASKS if t.name in allowed]
        base_a = load_amps(DRAIN_PER_HOUR[d.mode.value] + sum(costs))
        spikes = []
        for name, task, key, period in SERVICES:
            if not active[name]:
                continue
            if i % period == 0 and i > 0:
                if task not in allowed:
                    active[name] = False
                    end_time[name] = i / 60.0
                    continue
                amps = BURST_A[key]
                if aware:
                    amps = shape_burst(amps, sim.level, sim.temp_c, sim.health, base_a)
                    if name == "calls" and amps < MIN_CALL_A:
                        active[name] = False
                        end_time[name] = i / 60.0
                        continue
                spikes.append(amps)
        for task, key, period in BACKGROUND:
            if i % period == 0 and i > 0 and task in allowed:
                amps = BURST_A[key]
                if aware:
                    amps = shape_burst(amps, sim.level, sim.temp_c, sim.health, base_a)
                    if amps < 0.1:
                        continue
                spikes.append(amps)
        burst = max(spikes) if spikes else 0.0
        sim.advance(tick_s, d.mode.value, costs, burst_a=burst)
        i += 1
    if i >= max_min:
        end_h = max_min / 60.0
    else:
        end_h = i / 60.0
    hours = {}
    for name, _, _, _ in SERVICES:
        if active[name]:
            hours[name] = end_h
        else:
            hours[name] = end_time.get(name, end_h)
    brownout = sim.dead and not graceful and sim.shutdown_level is not None and sim.shutdown_level > 0.0
    return {
        "hours": hours,
        "brownout": brownout,
        "shutdown_level": sim.shutdown_level if brownout else None,
        "graceful": graceful,
        "alive_h": end_h,
    }
def benchmark_eol(policy_factory, aware=False, n=40, seed=0):
    scenarios = scenarios_eol(n, seed)
    count = len(scenarios)
    calls = sms = alarms = app = 0.0
    alive = 0.0
    brownouts = gracefuls = 0
    levels = []
    for scenario in scenarios:
        res = run_eol(policy_factory(), scenario, aware)
        hours = res["hours"]
        calls += hours["calls"]
        sms += hours["sms"]
        alarms += hours["alarms"]
        app += hours["app"]
        alive += res["alive_h"]
        brownouts += bool(res["brownout"])
        gracefuls += bool(res["graceful"])
        if res["shutdown_level"] is not None:
            levels.append(res["shutdown_level"])
    return {
        "calls": calls / count,
        "sms": sms / count,
        "alarms": alarms / count,
        "app": app / count,
        "alive": alive / count,
        "brownout_pct": 100.0 * brownouts / count,
        "shutdown_level": sum(levels) / len(levels) if levels else 0.0,
        "graceful_pct": 100.0 * gracefuls / count,
    }
def report(n=40, seed=0):
    lines = []
    lines.append("policy  mode    calls_h sms_h alarms_h app_h alive_h brownouts% died_at%")
    factories = [
        ("basic", lambda: basic_policy),
        ("static", lambda: static_low_power),
        ("rules", lambda: rules_policy),
        ("router", lambda: router_policy()),
    ]
    for name, factory in factories:
        for aware in (False, True):
            r = benchmark_eol(factory, aware, n, seed)
            mode_str = 'after' if aware else 'before'
            line = f"{name:<7} {mode_str:<7} {r['calls']:7.2f} {r['sms']:5.2f} {r['alarms']:8.2f} {r['app']:5.2f} {r['alive']:7.2f} {r['brownout_pct']:10.0f} {r['shutdown_level']:8.1f}"
            lines.append(line)
    return lines


def main(argv):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=40)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    print('worn battery, health 0.3-0.4, hours each service keeps working (before = plain phone, after = reserve and burst shaping)')
    for line in report(args.n, args.seed):
        print(line)
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
