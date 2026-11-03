# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure the governor on six device profiles."""
import random
import sys
from opos.evaluate import EVAL_TASKS, router_policy, rules_policy, static_low_power
from opos.governor import PowerState, Priority
from opos.power import KEEP
from opos.sim import DrainSim
PROFILES = {
    "fresh": {"level": (40, 100), "health": (0.85, 1.0), "temp": (20, 32), "r0": None},
    "worn": {"level": (30, 100), "health": (0.4, 0.6), "temp": (20, 32), "r0": None},
    "cold": {"level": (30, 100), "health": (0.5, 0.9), "temp": (-5, 10), "r0": None},
    "hot": {"level": (30, 100), "health": (0.5, 0.9), "temp": (38, 48), "r0": None},
    "lowstart": {"level": (5, 20), "health": (0.5, 1.0), "temp": (20, 32), "r0": None},
    "eol": {"level": (30, 100), "health": (0.35, 0.35), "temp": (15, 30), "r0": 0.2},
}
def scenarios(profile, n, seed):
    if profile not in PROFILES:
        raise ValueError(profile)
    cfg = PROFILES[profile]
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        level = rng.uniform(*cfg["level"])
        health = rng.uniform(*cfg["health"])
        temp = rng.uniform(*cfg["temp"])
        out.append({"level": level, "health": health, "temp": temp, "r0": cfg["r0"]})
    return out
def run(policy, scenario, tick_s=60.0, max_min=1440):
    lvl = scenario.get("level")
    hlth = scenario.get("health")
    temp = scenario.get("temp", scenario.get("temp_c"))
    sim = DrainSim(level=lvl, health=hlth, temp_c=temp, speed=1.0, r0=scenario.get("r0"))
    elapsed = 0
    useful_min = 0
    missing_kept = 0
    eval_names = {t.name for t in EVAL_TASKS}
    keep_needed = [k for k in KEEP if k in eval_names]
    while not sim.dead and sim.level > 0 and elapsed < max_min:
        state = PowerState(sim.level, False, sim.health, sim.temp_c)
        d = policy(state, EVAL_TASKS, t_s=elapsed * tick_s)
        allowed = set(d.allowed)
        for k in keep_needed:
            if k not in allowed:
                missing_kept += 1
        for t in EVAL_TASKS:
            if t.priority == Priority.USEFUL and t.name in allowed:
                useful_min += 1
        costs = [t.cost for t in EVAL_TASKS if t.name in allowed]
        sim.advance(tick_s, d.mode.value, costs)
        elapsed += 1
    brownout = 1 if sim.dead and sim.level > 0 else 0
    return {"important_min": elapsed, "useful_min": useful_min, "missing_kept": missing_kept, "brownout": brownout}
def benchmark(policy_factory, profile, n=40, seed=100):
    scens = scenarios(profile, n, seed)
    sums = {"important_min": 0.0, "useful_min": 0.0, "missing_kept": 0.0, "brownout": 0.0}
    for scen in scens:
        try:
            pol = policy_factory()
            if not callable(pol):
                pol = policy_factory
        except TypeError:
            pol = policy_factory
        res = run(pol, scen)
        for k in sums:
            sums[k] += res[k]
    return {k: sums[k] / len(scens) if scens else 0.0 for k in sums}
def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    n = 40
    i = 0
    while i < len(argv):
        if argv[i] == "--n" and i + 1 < len(argv):
            n = int(argv[i + 1])
            i += 2
        else:
            i += 1
    factories = {
        "rules": lambda: rules_policy,
        "static": lambda: static_low_power,
        "router": lambda: router_policy(),
    }
    for pname in PROFILES:
        for pol_name, fac in factories.items():
            res = benchmark(fac, pname, n=n, seed=100)
            print(f"{pname} {pol_name} important_min={res['important_min']:.1f} useful_min={res['useful_min']:.1f} missing_kept={res['missing_kept']:.1f} brownout={res['brownout']:.1f}")
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
