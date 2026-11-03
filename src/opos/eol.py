# SPDX-License-Identifier: GPL-3.0-or-later
CUTOFF_V = 3.0
WORN_HEALTH = 0.5
EOL_PROFILE = {"health": 0.35, "r0": 0.2}
BURST_A = {"call": 1.0, "message": 0.3, "alarm": 0.2, "app": 0.7, "sync": 0.5}
MIN_CALL_A = 0.3

def is_worn(health):
    return health < WORN_HEALTH

def ocv(q):
    q = max(0.0, min(100.0, q))
    return 3.3 + 0.9 * (q / 100) ** 0.7

def resistance(q, temp_c, r0):
    q = max(0.0, min(100.0, q))
    if temp_c < 25:
        f = 1 + 0.03 * (25 - temp_c)
    elif temp_c > 35:
        f = 1 + 0.02 * (temp_c - 35)
    else:
        f = 1
    return r0 * f * (1 + 3 * (1 - q / 100) ** 3)

def load_amps(load_units):
    if load_units < 0:
        load_units = 0.0
    return 0.1 + 0.025 * load_units

def voltage(q, amps, temp_c, r0):
    return ocv(q) - amps * resistance(q, temp_c, r0)

def brownout(q, amps, temp_c, r0, margin_v=0.0):
    return voltage(q, amps, temp_c, r0) < CUTOFF_V + margin_v

def max_safe_amps(q, temp_c, r0, margin_v=0.05):
    denom = resistance(q, temp_c, r0)
    val = (ocv(q) - CUTOFF_V - margin_v) / denom
    return val if val > 0 else 0.0

def burst_floor(amps, temp_c, r0, margin_v=0.0):
    qs = [i * 0.1 for i in range(0, 1001)]
    for i, q in enumerate(qs):
        if not brownout(q, amps, temp_c, r0, margin_v):
            ok = True
            for q2 in qs[i + 1:]:
                if brownout(q2, amps, temp_c, r0, margin_v):
                    ok = False
                    break
            if ok:
                return float(q)
    return 100.0

def call_reserve(temp_c, r0, call_minutes=3.0, health=0.35, load_units=15.0):
    base = burst_floor(MIN_CALL_A, temp_c, r0, 0.05)
    add = 2 * load_units * (call_minutes / 60) / max(health, 0.1)
    return base + add

def shutdown_floor(temp_c, r0):
    return burst_floor(load_amps(0.0) + 0.05, temp_c, r0, 0.05)
def r0_for_health(health):
    health = max(0.05, min(1.0, health))
    return 0.05 + 0.23 * (1 - health)
