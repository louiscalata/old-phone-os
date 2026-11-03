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

    def advance(self, real_s, mode_name, task_costs, burst_a=0.0, per_hour=None) -> float:
        """Drain for real_s seconds of real time (times speed).

        By default the drain is the mode's base rate plus task_costs, divided by health.
        per_hour, when given, is the whole drain in percent per hour with health already
        counted (as opos.power.total_drain gives it) and replaces that sum.
        """
        if real_s < 0:
            raise ValueError(real_s)
        dt_h = real_s * self.speed / 3600
        divisor = max(self.health, 0.1)
        if per_hour is None:
            units = DRAIN_PER_HOUR[mode_name] + sum(task_costs)
        else:
            units = per_hour * divisor
        if self.r0 is None:
            drain = units * dt_h / divisor if per_hour is None else per_hour * dt_h
            self.level = max(0.0, self.level - drain)
            target = 25 + drain * 40
            frac = min(1.0, 0.1 * dt_h * 6)
            self.temp_c = min(60.0, max(15.0, self.temp_c + frac * (target - self.temp_c)))
            self.t_s += real_s * self.speed
            return self.level
        if self.dead:
            return self.level
        amps = load_amps(units) + burst_a
        if brownout(self.level, amps, self.temp_c, self.r0):
            self.dead = True
            self.shutdown_level = self.level
            return self.level
        drain_base = units * dt_h / divisor if per_hour is None else per_hour * dt_h
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
