# SPDX-License-Identifier: GPL-3.0-or-later
"Battery-drain simulations for normal and end-of-life batteries."
from opos.eol import brownout, load_amps
DRAIN_PER_HOUR = {"NORMAL": 12.0, "SAVER": 5.0, "CRITICAL": 2.0}


class DrainSim:
    "Simulates battery drain, heating and charging over accelerated time."
    def __init__(self, level=40.0, health=0.6, temp_c=30.0, speed=60.0, r0=None):
        self.level = level
        self.health = health
        self.temp_c = temp_c
        self.ambient_c = temp_c
        self.speed = speed
        self.t_s = 0.0
        self.r0 = r0
        self.dead = False
        self.shutdown_level = None

    def advance(self, real_s, mode_name, task_costs, burst_a=0.0) -> float:
        if real_s < 0:
            raise ValueError(real_s)
        total_cost = sum(task_costs)
        if self.r0 is None:
            base = DRAIN_PER_HOUR[mode_name]
            dt_h = real_s * self.speed / 3600
            drain = (base + total_cost) * dt_h / max(self.health, 0.1)
            self.level = max(0.0, self.level - drain)
            target = 25 + drain * 40
            frac = min(1.0, 0.1 * dt_h * 6)
            self.temp_c = min(60.0, max(15.0, self.temp_c + frac * (target - self.temp_c)))
            self.t_s += real_s * self.speed
            return self.level
        if self.dead:
            return self.level
        base = DRAIN_PER_HOUR[mode_name]
        units = base + total_cost
        amps = load_amps(units) + burst_a
        if brownout(self.level, amps, self.temp_c, self.r0):
            self.dead = True
            self.shutdown_level = self.level
            return self.level
        dt_h = real_s * self.speed / 3600
        drain_base = (base + total_cost) * dt_h / max(self.health, 0.1)
        low_factor = 1 + max(0.0, (30.0 - self.level) / 30.0)
        if self.temp_c < 15:
            temp_factor = 1 + 0.02 * (15 - self.temp_c)
        elif self.temp_c > 40:
            temp_factor = 1 + 0.01 * (self.temp_c - 40)
        else:
            temp_factor = 1
        drain = drain_base * low_factor * temp_factor
        self.level = max(0.0, self.level - drain)
        target = self.ambient_c + drain * 40
        frac = min(1.0, 0.1 * dt_h * 6)
        self.temp_c = min(60.0, max(-10.0, self.temp_c + frac * (target - self.temp_c)))
        self.t_s += real_s * self.speed
        if self.level == 0.0:
            self.dead = True
            self.shutdown_level = 0.0
        return self.level

    def charge(self, real_s, rate_per_hour=60.0):
        if real_s < 0:
            raise ValueError(real_s)
        self.level = min(100.0, self.level + rate_per_hour * real_s * self.speed / 3600)
        self.t_s += real_s * self.speed
        return self.level


def eol_sim(level=40.0, temp_c=30.0, speed=60.0):
    "Create a DrainSim configured as an end-of-life battery."
    return DrainSim(level=level, health=0.35, temp_c=temp_c, speed=speed, r0=0.2)
