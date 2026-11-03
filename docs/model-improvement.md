# Model Improvement
> Historical. This describes a learned governor classifier that was removed; the governor is now the rule router in src/opos/router.py (see docs/META.md). The train and evaluate --model commands below no longer exist.

## Summary
Important minutes up +44.0 on unseen seeds. New model keeps essential tasks allowed longer while starved stays 0. Gain is a trade of useful minutes for important minutes via horizon dial. Versus rules +41.8 percent and versus a static low-power mode +155 percent important minutes, with zero starved important tasks.

## Metric
Primary metric is important_min = simulated minutes until battery hits 0 while calls, messages, app in use and alarms stay allowed. Secondary metrics are useful_min = summed minutes email+maps allowed, starved = ticks where an important task was not allowed must be 0, and accuracy against rule labels. Evaluation uses src/opos/evaluate.py, DrainSim speed 1.0, 60 s ticks, 50 scenarios per seed. Level 20-100, health 0.4-1.0, temp 20-40 C. Tasks = calls, messages, email, maps, photos backup, app in use, alarms. Selection seeds 0,1,2. Confirmation seeds 10,11,12 never used for selection. Training seeds 0,1,2 per experiment. Training-seed spread of important_min was 1-5 minutes.

## Baseline
Shipped model before this work: 6 features, rule labels, 4000 samples, 300 epochs. Held-out accuracy on rule labels 86.4 percent. Size 254 bytes JSON. Inference 0.37 microseconds per score.
New model: 9 features, 348 bytes JSON, 0.56 microseconds per score, trained on the PC with 4000 samples and 1000 epochs in about 9 s.

Seeds 0,1,2:

| policy | important_min | spread | useful_min |
| --- | --- | --- | --- |
| rules | 354.3 | 23.0 | 240.2 |
| static | 202.6 | 29.9 | 405.1 |
| model | 458.1 | 34.4 | 178.4 |
Starved 0 for all.

Seeds 10,11,12:

| policy | important_min | spread | useful_min |
| --- | --- | --- | --- |
| rules | 343.6 | 16.8 | 220.7 |
| static | 191.0 | 19.7 | 382.1 |
| model | 443.3 | 31.5 | 156.0 |
Starved 0. Safety-floor violations 0.

## Experiments
### E1
Retrain with 8 features added eff_norm = level*health, hot flag, rule labels.

| important_min | useful_min | held-out accuracy |
| --- | --- | --- |
| 374.5 | 225.1 | 0.945 |
What it showed: more accurate but less battery life than baseline model because old under-trained model happened to be conservative.

### E2
Budget labels from drain simulation. Task allowed if charging, or OPTIONAL level*health >= optional_floor and temp <45, or USEFUL level*health/(5+cost) >= horizon_h hours and temp <45. 300 epochs.

| h | f | important_min | useful_min |
| --- | --- | --- | --- |
| 2 | 60 | 469.4 | 166.3 |
| 2 | 80 | 498.4 | 152.6 |
| 2 | 101 | 509.4 | 143.8 |
| 3 | 60 | 545.8 | 105.7 |
| 3 | 80 | 578.9 | 84.5 |
| 3 | 101 | 592.8 | 72.9 |
| 4 | 60 | 598.6 | 64.8 |
| 4 | 80 | 632.2 | 39.9 |
| 4 | 101 | 646.7 | 27.9 |
| 6 | 60 | 664.1 | 13.5 |
| 6 | 80 | 679.8 | 1.0 |
| 6 | 101 | 680.4 | 0.0 |
What it showed: horizon is a dial that trades useful minutes for important minutes.

### E3
Data size and epochs, h2 f80.

| n | e | important_min | useful_min |
| --- | --- | --- | --- |
| 1000 | 300 | 502.4 | 149.6 |
| 4000 | 100 | 583.3 | 81.7 |
| 4000 | 1000 | 423.6 | 215.4 |
| 16000 | 300 | 502.3 | 149.5 |
| 16000 | 1000 | 427.8 | 212.0 |
What it showed: 300-epoch models are under-trained and lean conservative. Training to convergence 1000 epochs raises accuracy on budget labels from about 0.94 to 0.97 and makes result principled instead of accidental. More data 16000 changes nothing.

### E4
Ninth feature hours_budget = min(level*health/(5+cost),40)/20 plus cost_weight cw divisor 5+cw*cost. 1000 epochs, f80.

| h | cw | important_min | useful_min |
| --- | --- | --- | --- |
| 2 | 1 | 428.1 | 211.8 |
| 2 | 2 | 508.8 | 142.3 |
| 2 | 4 | 600.2 | 64.8 |
| 3 | 1 | 523.7 | 130.4 |
| 3 | 2 | 593.7 | 70.9 |
| 3 | 4 | 657.6 | 18.3 |
| 4 | 1 | 584.0 | 79.6 |
| 4 | 2 | 642.5 | 30.6 |
| 4 | 4 | 677.4 | 2.9 |
Held-out accuracy on budget labels 0.954-0.967.
What it showed: cost_weight defers expensive tasks before cheap ones.

### E5
Around chosen point h2 cw2 e1000.

| config | important_min | useful_min |
| --- | --- | --- |
| optional_floor 60 | 498.8 | 143.4 |
| floor 101 | 511.2 | 140.1 |
| floor 80 with n16000 | 510.3 | 141.1 |
| h2.5 cw1.5 f80 | 525.0 | 128.9 |
What it showed: point is stable near h2 cw2 f80.

## Result
Confirmation on unseen eval seeds 10,11,12.

| policy | important_min | spread | useful_min | starved |
| --- | --- | --- | --- | --- |
| rules | 343.6 | 16.8 | 220.7 | 0 |
| static low power | 191.0 | 19.7 | 382.1 | 0 |
| baseline model | 443.3 | 31.5 | 156.0 | 0 |
| new model | 487.3 | 30.9 | 127.8 | 0 |

New model per-seed useful_min: 146.8, 117.2, 119.3.
Gains in important minutes vs old model +44.0 +9.9 percent. Vs rules +143.7 +41.8 percent. Vs static low power +296.3 +155 percent.
Useful minutes: new 127.8 vs old 156.0, rules 220.7, static 382.1.
Starved 0, safety-floor violations 0.
On seeds 0,1,2: new 506.8 spread 34.1 versus old 458.1.

## What was kept and why
Kept h2, cw2, f80, n4000, e1000, seed 0, 9 features. Pareto point.
It lies on frontier slightly above old model. Interpolating converged h2 cw1 428/212 and h2 cw2 509/142 at 458 important minutes gives about 186 useful minutes versus 178 for old model.
Rule-label accuracy new model 0.769 old 0.864. Expected. Model is trained to follow battery budget not copy rules. Accuracy on its own budget labels is 0.954.

## Limits
Gain over old model is trade of useful minutes for important minutes via horizon_h dial. Only small frontier improvement about 8 useful minutes at equal life beyond dial. Benchmark is repo's DrainSim, not real phone measurements. No 'last until' scenario existed on main at time. PC and Mac produce weights equal to about 1e-12 float differences, evaluation results match.
Hardware: a Windows PC, Python 3.13 stdlib only, no installs. Total PC training+evaluation time about 26 minutes across four sweeps 400 s, 313 s, 507 s, 322 s.

## Reproduce
```
python -m opos.train out.json --labels budget --horizon 2 --cost-weight 2 --optional-floor 80 --epochs 1000 --seed 0
python -m opos.evaluate --model out.json --seeds 10,11,12
```

The benchmark, model and docs were made in earlier development commits that are not in this repository.

## Goal-aware model (goalsim)

Goal: "last until 11 PM" from the home screen. goalsim is a closed-loop simulation (`opos.goalsim.simulate`): each minute it applies the policy's decisions, drains with `total_drain`, and reports hours reached. Arms: plan (`opos.power.plan`, the planner's analytic answer), basic (static pauses + 2 savings steps), model (the on-phone model through `Runtime.step` with the remaining goal hours).

### Before

Before (an earlier development commit that is not in this repository). Table, hours until empty, goal 11 h:

| scenario | plan hours | plan reaches | model hours | model reaches | model paused | basic hours |
|---|---|---|---|---|---|---|
| level 40 health 0.6 | 3.89 | False | 3.15 | False | alarms,email,maps,photos backup | 1.75 |
| level 60 health 0.6 | 5.83 | False | 4.28 | False | alarms,email,maps,photos backup | 2.63 |
| level 100 health 0.6 | 9.72 | False | 5.96 | False | alarms,email,maps,photos backup | 4.38 |
| level 40 health 1.0 | 6.48 | False | 4.46 | False | alarms,email,maps,photos backup | 2.92 |
| level 60 health 1.0 | 9.72 | False | 5.87 | False | alarms,email,maps,photos backup | 4.38 |
| level 80 health 1.0 | 12.97 | True | 7.23 | False | alarms,email,maps,photos backup | 5.84 |

Why it was far from the plan: at level 80 health 1.0 the on-phone model paused only photos backup until about 22% battery and never used more savings steps, because `hours_budget` (funded hours / goal, capped at 2.0) saturated and the weights barely reacted; the on-phone model also paused alarms. It died at about 7.2 h versus the plan's 12.97 h.

### Change

(1) New features `goal_on` (1 when a goal is set else 0) and `goal_slack = clamp((level*health/goal_hours - 6.17 - task cost)/5, -4, 4)`, where 6.17 (`GOAL_BASE_DRAIN`) is the drain with all four savings steps applied and calls, messages and alarms kept. `FEATURES` now has 11 entries; `hours_budget` stays last. (2) The training labels for goal samples are an oracle: allow a task iff 6.17 + task cost <= level*health/goal_hours (always allow while charging, never at 45 C or hotter); synth got a `goal_fraction` option (default 0.0 keeps the old data byte for byte) and the on-phone model was retrained with `--goal-fraction 0.5 --n 8000` (4000 no-goal samples as before). Train accuracy 0.951, held-out 0.949. (3) `opos.power.steps_for_goal` picks the smallest prefix of the savings steps (dim screen, short timeout, batch sync, slow background polling) that makes the drain fit level/goal_hours; `Runtime.step` returns it as `Tick.steps` and goalsim applies it. (4) `Runtime.step` always keeps calls, messages and alarms (`KEEP`) running.

### After

After (same scenarios, same 60 s step):

| scenario | plan hours | plan reaches | model hours | model reaches | model paused | basic hours |
|---|---|---|---|---|---|---|
| level 40 health 0.6 | 3.89 | False | 3.89 | False | email,maps,photos backup | 1.75 |
| level 60 health 0.6 | 5.83 | False | 5.83 | False | email,maps,photos backup | 2.63 |
| level 100 health 0.6 | 9.72 | False | 9.72 | False | email,maps,photos backup | 4.38 |
| level 40 health 1.0 | 6.48 | False | 6.48 | False | email,maps,photos backup | 2.92 |
| level 60 health 1.0 | 9.72 | False | 9.72 | False | email,maps,photos backup | 4.38 |
| level 80 health 1.0 | 12.97 | True | 11.15 | True | email,maps,photos backup | 5.84 |

Reading the after table: at health 0.6 and at level 40 or 60 with health 1.0 the goal is physically unreachable even with every savings step (the plan itself says so), and the on-phone model now matches the plan's hours to two decimals (3.89 h for level 40 health 0.6, the 3 h 53 min of the plan), where before it lost 0.7 to 3.8 h. At level 80 health 1.0 the goal is reachable and the on-phone model now reaches it (11.15 h, goal 11.0 h) where before it died at 7.23 h. It does not maximise life when the goal is already met: with slack it uses fewer savings steps than the plan (all 4 at the start, 3 from about hour 3 to hour 9, 2 near hour 10, measured in a trace at level 80 health 1.0) and spends the surplus, so 11.15 h is below the plan's 12.97 h maximum by design. It never lets the paused set go below email, maps and photos backup after hour 3 (maps and photos backup in the first hours). Alarms are never paused now. The basic arm is unchanged.

### Benchmark

Benchmark (no goal, `DrainSim`, `opos.evaluate`, unseen seeds 10,11,12, model arm): important minutes 487.3 before, 548.1 after (rules 343.6, static low power 191.0); useful minutes 127.8 before, 77.5 after (per seed 91.6, 70.7, 70.3); starved 0 and safety-floor violations 0 in both. Seeds 0,1,2: 506.8 before, 573.2 after. So without a goal the on-phone model gives more important minutes at the cost of fewer useful minutes; this is a trade-off, not a free gain. Rule-label accuracy of the on-phone model is 0.727 (it follows its battery budget, not the rules).

### Limits

The benchmark and goalsim are simulations on the repo's drain model, not measurements on a real phone. Health 0.6 cannot reach 11 h at any charge level under this model (plan 9.72 h at 100%). The oracle label uses the same drain constants as the planner, so agreement with the plan is by construction; the on-phone model's job is to apply them on-device without a solver.

### Reproduce

Reproduce (inline code, no fence): `python -m opos.train out.json --labels budget --horizon 2 --cost-weight 2 --optional-floor 80 --epochs 1000 --seed 0 --n 8000 --goal-fraction 0.5`, then `python -m opos.evaluate --model out.json --seeds 10,11,12`.
