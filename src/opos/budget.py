# SPDX-License-Identifier: GPL-3.0-or-later
from dataclasses import dataclass
from opos.governor import Mode
@dataclass(frozen=True)
class Budget:
    cpu_pct: int
    screen_brightness: int
    sync_minutes: int
    radio: str
BUDGETS: dict[Mode, Budget] = {
    Mode.NORMAL: Budget(80, 70, 15, "full"),
    Mode.SAVER: Budget(40, 40, 60, "full"),
    Mode.CRITICAL: Budget(10, 20, 0, "calls-sms"),
}
def budget_for(mode: Mode, temp_c: float) -> Budget:
    base = BUDGETS[mode]
    cpu = base.cpu_pct
    brightness = base.screen_brightness
    sync = base.sync_minutes
    radio = base.radio
    if temp_c >= 40:
        cpu //= 2
        brightness = min(brightness, 30)
    if temp_c >= 50:
        radio = "calls-sms"
        sync = 0
    return Budget(cpu, brightness, sync, radio)
def allows_app(budget: Budget, essential: bool) -> bool:
    return True if essential else budget.cpu_pct > 10
