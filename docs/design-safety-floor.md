<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Rules remain the safety floor in every mode

The Governor first computes the deterministic rule decision. An optional model advisor can suggest additional pauses and explanations, but it cannot enable a task that those rules defer. This applies in NORMAL, SAVER and CRITICAL modes.

For a valid advisor decision, `src/opos/governor.py` intersects its allowed tasks with the rule baseline's allowed tasks. The result can be more restrictive, never less restrictive. It also keeps the baseline mode if the advisor proposes a less restrictive mode.

The Governor rejects malformed task partitions or an advisor decision that drops an essential task, and falls back to the rule decision if the advisor fails validation or raises an exception. Essential tasks are defined by their priority in the supplied task list; this policy is not a guarantee of physical calling or modem service.

An earlier version of this note said SAVER let the model re-enable optional work. That is no longer true: the intersection applies in every mode.

## Hard floors

Nothing the owner does, neither Undo nor using an app in the foreground, can re-allow a task that a hard floor refuses.

- Calls, messages and alarms are always allowed. The desktop demo simulates calls and messages only.
- At or above 45 C (`HOT_C` in `src/opos/router.py`) only essential tasks run, charging or not.
- On battery, when less than 15 percent of the charge is usable (charge times battery health), the battery rules' mode is CRITICAL and only essential tasks run.
- Below the reserve kept for calls, only essential tasks run, even while charging. The reserve is 0.7 percent of charge (10 minutes of calls and 20 messages, `essentials_reserve` in `src/opos/battery.py`), plus one hour of the measured drain unless a daily goal is set and the phone is on battery below 45 C.
- On battery, the worn-battery reserve (`apply_reserve` in `src/opos/reserve.py`) may refuse tasks, including the app in use, to keep charge for calls. It engages only when battery health is below 50 percent (`WORN_HEALTH = 0.5` in `src/opos/eol.py`).

The runtime names these floors in `HARD_FLOORS` in `src/opos/runtime.py` (causes "reserve", "hot" and "low"), and the battery panel gives each held task the matching reason, such as "to keep a battery reserve for calls".

## Soft pauses

Every other pause is soft:

- goal-saving pauses, which help the battery last until the owner's daily goal;
- the router's battery-affordability pauses, for tasks the remaining charge cannot fund;
- the battery rules' SAVER pauses of optional tasks, when 15 to 50 percent of the charge is usable;
- extra pauses added by Muse Glimmer.

The owner's Undo, or using the app in the foreground, may override a soft pause.

The router can report CRITICAL mode when the goal or the remaining charge leaves room only for essentials. Those pauses are soft: only the battery rules' CRITICAL mode is a hard floor. So the mode shown is a summary, and the final allowed and paused lists decide what runs.

## Undo

An Undo lasts until the mode (NORMAL, SAVER or CRITICAL) changes; after that the task is decided again from the current battery state. A task kept paused by a hard floor shows that floor as its reason, and Undo has no effect on it.

## Goal budget

With a daily goal, the energy budget is cumulative. Non-essential tasks are funded in a fixed order: USEFUL before OPTIONAL, then lower cost first, then by name. Each funded task's cost is subtracted from the budget before the next task is checked, so two tasks that each fit on their own may not both fit.

## One final decision

The home screen, the battery panel, the lock-screen story, the helper's facts and the simulated drain all read the same final allowed and deferred sets, after hard floors, soft pauses and the owner's overrides have been applied.

## Worn battery wording

In what the phone shows, "worn" means battery health below 80 percent. The worn-battery reserve engages only below 50 percent. The default demo battery has 60 percent health: it is worn, but its reserve is not active. The desktop demo cannot change battery health, so the worn-battery reserve appears only in the simulations (`PYTHONPATH=src python3 -m opos.evaleol`, 30 to 40 percent health; see [worn-battery-results.md](worn-battery-results.md)).

## Limits

The optional model is not bundled, and this policy does not establish an on-phone trained model or native inference. With no usable advisor, the rule baseline remains available. See `tests/test_governor.py` for the existing all-mode subset, validation and fallback checks, and the README for deployment and evidence limits.
