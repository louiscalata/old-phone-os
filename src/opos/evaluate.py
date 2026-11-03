# SPDX-License-Identifier: GPL-3.0-or-later
"""Benchmark battery policies on DrainSim."""
import random
import sys

from opos.benchmark_model import _load_model, resolve_model_path
from opos.runtime import DEFAULT_TASKS, Runtime
from opos.governor import PowerState, Priority, Task, Mode, Decision, rule_decide
from opos.sim import DrainSim


def model_policy(model_path=None, *args, **kwargs):
    path = resolve_model_path(model_path, *args, **kwargs)
    if not path:
        raise FileNotFoundError("model unavailable: no path provided")
    data = _load_model(path)
    allowed_cfg = data.get("allowed")
    deferred_cfg = data.get("deferred")
    mode_name = data.get("mode", "saver")
    if mode_name == "saver":
        mode = Mode.SAVER
    elif mode_name == "performance":
        mode = Mode.NORMAL
    else:
        raise ValueError(f"model invalid: unknown mode {path!r}")
    if allowed_cfg is not None:
        if not isinstance(allowed_cfg, list) or any(not isinstance(x, str) for x in allowed_cfg):
            raise ValueError(f"model invalid: allowed must be list {path!r}")
    elif deferred_cfg is not None:
        if not isinstance(deferred_cfg, list) or any(not isinstance(x, str) for x in deferred_cfg):
            raise ValueError(f"model invalid: deferred must be list {path!r}")

    def policy(state, tasks, t_s=0.0):
        if allowed_cfg is not None:
            allowed = tuple(allowed_cfg)
            deferred = tuple(t.name for t in tasks if t.name not in set(allowed))
        elif deferred_cfg is not None:
            deferred = tuple(deferred_cfg)
            allowed = tuple(t.name for t in tasks if t.name not in set(deferred))
        else:
            allowed = tuple(t.name for t in tasks)
            deferred = ()
        protected = {t.name for t in tasks if t.priority == Priority.ESSENTIAL}
        if protected & set(deferred):
            raise ValueError(f"model invalid: cannot defer ESSENTIAL {sorted(protected & set(deferred))!r} {path!r}")
        if not protected.issubset(set(allowed)):
            raise ValueError(f"model invalid: allowed missing ESSENTIAL {sorted(protected - set(allowed))!r} {path!r}")
        if allowed_cfg is not None and deferred_cfg is not None:
            if set(deferred_cfg) != set(deferred):
                raise ValueError(f"model invalid: allowed/deferred disagree {path!r}")
        return Decision(mode, allowed, deferred, "model")

    return policy

EVAL_TASKS = DEFAULT_TASKS + (
    Task("app in use", Priority.ESSENTIAL, 1.0),
    Task("alarms", Priority.ESSENTIAL, 0.1),
)
IMPORTANT = [t.name for t in EVAL_TASKS if t.priority == Priority.ESSENTIAL]


def rules_policy(state: PowerState, tasks, t_s=0.0):
    """Return rule based decision."""
    return rule_decide(state, tasks)


def static_low_power(state, tasks, t_s=0.0):
    """Always SAVER, allow ESSENTIAL and USEFUL."""
    allowed = []
    deferred = []
    for t in tasks:
        if t.priority == Priority.ESSENTIAL or t.priority == Priority.USEFUL:
            allowed.append(t.name)
        else:
            deferred.append(t.name)
    return Decision(Mode.SAVER, tuple(allowed), tuple(deferred), "static")


def router_policy():
    """Factory returning the rule router policy."""
    rt = Runtime(tasks=EVAL_TASKS)

    def policy(state, tasks, t_s=0.0):
        tick = rt.step(t_s, state.level, state.charging, state.health, state.temp_c)
        return tick.decision

    return policy


def scenarios(n, seed):
    """Generate deterministic scenarios."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        out.append({
            "level": rng.uniform(20, 100),
            "health": rng.uniform(0.4, 1.0),
            "temp_c": rng.uniform(20, 40),
        })
    return out


def run(policy, scenario, tick_s=60.0, max_min=24 * 60):
    """Simulate one scenario."""
    sim = DrainSim(level=scenario["level"], health=scenario["health"],
                   temp_c=scenario["temp_c"], speed=1.0)
    starved = 0
    useful_min = 0.0
    elapsed = 0
    max_ticks = int(max_min)
    while sim.level > 0 and elapsed < max_ticks:
        t_s = elapsed * tick_s
        state = PowerState(sim.level, False, sim.health, sim.temp_c)
        d = policy(state, EVAL_TASKS, t_s=t_s)
        if sim.level > 0:
            missing = [name for name in IMPORTANT if name not in d.allowed]
            starved += len(missing)
        allowed_set = set(d.allowed)
        costs = []
        for t in EVAL_TASKS:
            if t.name in allowed_set:
                costs.append(t.cost)
                if t.priority == Priority.USEFUL:
                    useful_min += tick_s / 60.0
        sim.advance(tick_s, d.mode.value, costs)
        elapsed += 1
    return {"important_min": float(elapsed), "useful_min": useful_min, "starved": starved}


def benchmark(policy_factory, n=50, seed=0):
    """Mean metrics over scenarios."""
    scens = scenarios(n, seed)
    metrics = []
    for scen in scens:
        pol = policy_factory()
        metrics.append(run(pol, scen))
    mean_imp = sum(m["important_min"] for m in metrics) / len(metrics)
    mean_use = sum(m["useful_min"] for m in metrics) / len(metrics)
    mean_star = sum(m["starved"] for m in metrics) / len(metrics)
    return {"important_min": mean_imp, "useful_min": mean_use, "starved": mean_star}


def main(argv):
    """CLI entry."""
    seeds = [0, 1, 2]
    i = 0
    while i < len(argv):
        if argv[i] == "--seeds":
            i += 1
            seeds = [int(s) for s in argv[i].split(",")]
        i += 1

    factories = {
        "rules": lambda: rules_policy,
        "static": lambda: static_low_power,
        "router": lambda: router_policy(),
    }
    results = {name: [] for name in factories}
    for seed in seeds:
        for name, fac in factories.items():
            res = benchmark(fac, n=50, seed=seed)
            print(f"{name} {res['important_min']:.1f} {res['useful_min']:.1f} {res['starved']:.1f}")
            results[name].append(res["important_min"])
    for name, vals in results.items():
        mean_imp = sum(vals) / len(vals)
        spread = max(vals) - min(vals)
        print(f"{name} mean important_min {mean_imp:.1f} spread {spread:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
