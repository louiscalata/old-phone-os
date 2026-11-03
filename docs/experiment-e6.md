<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# E6: does the gain hold on more unseen seeds?
> Historical. This describes a learned governor classifier that was removed; the governor is now the rule router in src/opos/router.py (see docs/META.md). The train and evaluate --model commands below no longer exist.

## Question
Does the +44 important-minute gain from docs/model-improvement.md hold on 10 new never-used seeds, and how much does it depend on the training seed?

## Setup
Command: python -m opos.evaluate --model <json> --seeds 20,21,22,23,24,25,26,27,28,29
50 scenarios per seed, DrainSim speed 1.0, 60 s ticks.
Hardware: Windows PC, Python 3.13 stdlib. Measured 2026-10-27 2:10-2:25 PM PDT.
Seeds 20-29 were never used before. Selection used 0,1,2; confirmation used 10,11,12.

## Results
| policy | important_min mean | spread across eval seeds | useful_min mean | starved |
|---|---|---|---|---|
| rules | 346.4 | 26.8 | 225.3 | 0 |
| static low power | 194.0 | 39.1 | 388.0 | 0 |
| baseline model (6 features, evaluated with the benchmark code from a private development commit) | 445.6 | 37.1 | 163.9 | 0 |
| new model, training seed 0 (the weights shipped at the time; the model file has since been removed) | 491.0 | 41.9 | 133.3 | 0 |
| new model, training seed 1 | 494.6 | 45.2 | 130.3 | 0 |
| new model, training seed 2 | 491.1 | 44.2 | 133.2 | 0 |

Derived: new (seed 0) vs baseline +45.4 important minutes (+10.2 percent), -30.6 useful minutes.
vs rules +144.6 (+41.7 percent); vs static +297.0 (+153 percent).
Across training seeds 0,1,2 the new model's important_min varies by 3.6 minutes (491.0-494.6).
On seeds 10,11,12 gain vs baseline was +44.0; on seeds 20-29 it is +45.4.

## Finding: old model files fall back to rules silently
Evaluating the old 6-feature JSON with the code that added the new model gives exactly the rules numbers 346.4 / 225.3 and accuracy N/A.
The model silently falls back to rules when the feature list does not match.
Baseline had to be evaluated with the older benchmark code. Anyone with an old model file gets rules without a warning.

## Conclusion
The gain holds on 10 more unseen seeds. New model seed 0 is +45.4 important minutes vs baseline at -30.6 useful minutes.
Training-seed spread is 3.6 minutes. Starved is 0 everywhere.
